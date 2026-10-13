"""Live intraday scanner for the SENSEX EMA/VWAP strategy.

It uses the SAME signal logic and config as the backtester (src.strategy) so the
alerts you receive match the setups you tested. It does NOT place orders; it
scans, detects a confirmed setup on a CLOSED 15-minute candle, and sends the
details to Telegram.

Run:  python live_scanner.py            (real alerts)
      python live_scanner.py --dry-run  (prints alerts, no Telegram, no waiting)

Designed to be launched by Windows Task Scheduler at market open; it then loops
internally until the market closes.
"""
import argparse
import json
import os
import time
from datetime import datetime, time as dtime, timedelta
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd
from dotenv import load_dotenv

from src.fyers_client import FyersClient
from src.strategy import indicators, signals
from src.telegram_notifier import TelegramNotifier

ROOT = os.path.dirname(os.path.abspath(__file__))
IST = ZoneInfo("Asia/Kolkata")
MARKET_OPEN = dtime(9, 15)
MARKET_CLOSE = dtime(15, 30)


def load_config():
    with open(os.path.join(ROOT, "config.json"), encoding="utf-8") as f:
        return json.load(f)


def client(cfg):
    return FyersClient(os.getenv("FYERS_CLIENT_ID", ""), os.getenv("FYERS_ACCESS_TOKEN", ""))


def now_ist():
    return datetime.now(IST)


def load_seen(path):
    """Load already-alerted bar keys from a small text file (one per line).
    Lets stateless GitHub Actions runs avoid re-alerting the same candle."""
    seen = set()
    if path and os.path.exists(path):
        try:
            with open(path, encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if line:
                        seen.add(line)
        except Exception:
            pass
    return seen


def save_seen(path, seen):
    if not path:
        return
    try:
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            # Keep the file bounded; only the recent keys matter for one day.
            for key in sorted(seen)[-200:]:
                f.write(key + "\n")
    except Exception as e:
        print(f"warn: could not write state file {path}: {e}")


def is_market_open(now):
    if now.weekday() >= 5:  # Sat/Sun
        return False
    return MARKET_OPEN <= now.time() <= MARKET_CLOSE


def within_blackout(now, cfg):
    """True if the current time falls inside a configured no-trade window.

    Windows are 'HH:MM-HH:MM' strings in IST, e.g. the chop-prone lunch period.
    """
    for win in cfg.get("no_entry_windows", []):
        try:
            a, b = win.split("-")
            start = dtime(*map(int, a.split(":")))
            end = dtime(*map(int, b.split(":")))
        except Exception:
            continue
        if start <= now.time() <= end:
            return True
    return False


def last_closed_candle_time(now):
    """The timestamp of the most recently CLOSED 15-minute bar (IST, naive)."""
    minutes = (now.minute // 15) * 15
    bar_start = now.replace(minute=minutes, second=0, microsecond=0)
    # A bar is only closed after the next boundary, so if we are exactly at a
    # boundary the just-ended bar is bar_start - 15min.
    if now.minute % 15 == 0 and now.second < 30:
        bar_start -= timedelta(minutes=15)
    return bar_start.replace(tzinfo=None)


def fetch_index_history(api, cfg, market, days=20):
    sym = cfg["symbols"][market]
    end = now_ist().date()
    start = end - timedelta(days=days)
    return api.history_range(sym, start.isoformat(), end.isoformat(), cfg["resolution"])


def latest_signal(api, cfg, market, filter_name):
    """Compute indicators on recent candles and report whether the LAST closed
    candle produced a fresh entry signal. Returns (signal_dict_or_None, bar_time)."""
    hist = fetch_index_history(api, cfg, market)
    if hist.empty:
        return None, None
    d = indicators(hist, cfg)
    if len(d) < 60:
        return None, None
    mask = signals(d, filter_name, cfg).fillna(False)
    last = d.iloc[-1]
    bar_time = pd.Timestamp(last.timestamp)
    if not bool(mask.iloc[-1]):
        return None, bar_time
    side = int(last.cross)
    if side == 0:
        return None, bar_time
    return {"side": side, "spot": float(last.close), "bar_time": bar_time,
            "vwap": float(last.vwap) if np.isfinite(last.vwap) else None}, bar_time


def pick_contract(api, cfg, market, side, spot):
    """Choose the near-ATM live option contract nearest the spot."""
    exchange = "BSE" if market == "SENSEX" else "NSE"
    underlying = {"SENSEX": "SENSEX", "NIFTY": "NIFTY", "BANKNIFTY": "BANKNIFTY"}[market]
    opt_type = "CE" if side == 1 else "PE"
    contracts = api.live_option_contracts(underlying=underlying, exchange=exchange)
    if contracts.empty:
        return None
    sub = contracts[contracts.option_type == opt_type].copy()
    if sub.empty:
        return None
    sub["dist"] = (sub.strike - spot).abs()
    return sub.sort_values(["dist", "strike"]).iloc[0].to_dict()


def premium_atr(api, cfg, symbol, entry_price):
    """Estimate option-premium ATR from recent 5-minute candles; fall back to a
    percentage of premium if the option has too little history (e.g. new strike)."""
    try:
        end = now_ist().date()
        start = end - timedelta(days=3)
        o = api.history(symbol, start.isoformat(), end.isoformat(), "5")
        if o is not None and len(o) >= 6:
            h, l, c = o.high, o.low, o.close
            prev = c.shift()
            tr = pd.concat([(h - l).abs(), (h - prev).abs(), (l - prev).abs()], axis=1).max(axis=1)
            atr = float(tr.tail(14).mean())
            if np.isfinite(atr) and atr > 0:
                return atr, "option 5m ATR"
    except Exception:
        pass
    return entry_price * 0.06, "premium % fallback"  # ~6% of premium


def build_signal(api, cfg, market, filt, sig):
    contract = pick_contract(api, cfg, market, sig["side"], sig["spot"])
    if contract is None:
        return None
    symbol = contract["symbol"]
    prices = api.ltp([symbol])
    entry = prices.get(symbol)
    if entry is None or not np.isfinite(entry) or entry <= 0:
        return None
    atr, atr_src = premium_atr(api, cfg, symbol, entry)
    stop_atr, target_atr = float(cfg["stop_atr"]), float(cfg["target_atr"])
    stop = max(0.05, entry - stop_atr * atr)
    target = entry + target_atr * atr
    lot = int(cfg["lot_sizes"][market])
    # Risk-based qty so the stop risks ~risk_pct of capital.
    risk_amount = float(cfg["initial_capital"]) * float(cfg.get("risk_pct", 0.01))
    qty = max(lot, int(risk_amount // max(1e-6, (entry - stop))) // lot * lot)
    return {
        "market": market, "filter": filt, "option_type": contract["option_type"],
        "symbol": symbol, "strike": float(contract["strike"]), "spot": sig["spot"],
        "signal_time": str(sig["bar_time"]), "entry": float(entry),
        "stop": float(stop), "target": float(target),
        "risk_pct": (entry - stop) / entry * 100,
        "reward_pct": (target - entry) / entry * 100,
        "lot_size": lot, "qty": int(qty), "risk_amount": risk_amount,
        "atr_source": atr_src, "expiry": str(contract.get("expiry", "-")),
    }


def scan_once(api, cfg, notifier, market, filt, dry_run=False, seen=None, max_bar_age_min=16):
    """One scan pass. Returns (signal_dict_or_None, bar_time, reason).

    Guards, in order: fresh-candle age, last-entry cutoff, danger-zone blackout,
    duplicate (already alerted). These mirror the backtest's trading rules so the
    live alerts match what was validated.
    """
    seen = seen if seen is not None else set()
    sig, bar_time = latest_signal(api, cfg, market, filt)
    if sig is None:
        return None, bar_time, ("no_data" if bar_time is None else "no_setup")
    now = now_ist()
    # Fresh-candle guard: only act right after a candle closes (stateless-run safe).
    age_min = (now.replace(tzinfo=None) - pd.Timestamp(bar_time)).total_seconds() / 60.0
    if age_min > max_bar_age_min:
        return None, bar_time, f"stale_bar_{age_min:.0f}m"
    # Respect the backtest's latest-entry cutoff.
    if pd.Timestamp(bar_time).strftime("%H:%M") > cfg.get("last_entry_time", "14:30"):
        return None, bar_time, "after_last_entry"
    # Danger-zone / lunch blackout.
    if within_blackout(now, cfg):
        return None, bar_time, "blackout_window"
    key = f"{market}|{bar_time}|{sig['side']}"
    if key in seen:
        return None, bar_time, "already_alerted"
    details = build_signal(api, cfg, market, filt, sig)
    if details is None:
        return None, bar_time, "no_contract_or_quote"
    notifier.send(TelegramNotifier.format_signal(details))
    seen.add(key)
    return details, bar_time, "alert_sent"

def main():
    ap = argparse.ArgumentParser(description="Live SENSEX EMA/VWAP scanner")
    ap.add_argument("--market", default="SENSEX")
    ap.add_argument("--filter", default="trend", help="Signal filter (trend recommended)")
    ap.add_argument("--dry-run", action="store_true", help="Print instead of sending; no market-hours gate")
    ap.add_argument("--once", action="store_true", help="Scan a single time and exit (for GitHub Actions)")
    ap.add_argument("--state-file", default="", help="Path to persist alerted bars (dedup across runs)")
    ap.add_argument("--max-bar-age-min", type=float, default=None, help="Only alert on a candle this fresh")
    args = ap.parse_args()

    load_dotenv(os.path.join(ROOT, ".env"))
    cfg = load_config()
    api = client(cfg)
    notifier = TelegramNotifier(dry_run=args.dry_run)
    max_age = args.max_bar_age_min if args.max_bar_age_min is not None else float(cfg.get("max_bar_age_min", 16))
    seen = load_seen(args.state_file)

    print(f"Scanner start {now_ist():%Y-%m-%d %H:%M IST} | market={args.market} filter={args.filter} "
          f"| telegram={'dry-run' if args.dry_run else ('on' if notifier.enabled else 'OFF (no creds)')} "
          f"| blackout={cfg.get('no_entry_windows', [])}")

    while True:
        now = now_ist()
        if args.dry_run or is_market_open(now):
            try:
                details, bar, reason = scan_once(api, cfg, notifier, args.market, args.filter,
                                                 args.dry_run, seen, max_age)
                print(f"[{now:%H:%M:%S}] last bar {bar} -> {reason}")
                if details:
                    save_seen(args.state_file, seen)
            except Exception as e:
                print(f"[{now:%H:%M:%S}] scan error: {e}")
        else:
            print(f"[{now:%H:%M:%S}] market closed; waiting...")
        if args.once or args.dry_run:
            break
        # Scan every 15-minute boundary; sleep in short ticks so Ctrl+C is clean.
        for _ in range(15 * 60 // 20):
            time.sleep(20)


if __name__ == "__main__":
    main()
