import numpy as np
import pandas as pd

# Baseline crossover filters plus the enhanced "trend" family.
# trend      = crossover + VWAP + higher-timeframe bias gate
# trend_orb  = trend + opening-range broken in the trade direction
# trend_orb_bt = trend_orb + continuation (close breaks the prior bar's extreme)
FILTERS = ["baseline", "vwap", "vwap_slope", "vwap_volume", "vwap_adx",
           "trend", "trend_orb", "trend_orb_bt"]


# ---------------------------------------------------------------------------
# Context layers (higher-timeframe bias, opening range, prior-day levels)
# ---------------------------------------------------------------------------
def _add_htf_bias(d, mode):
    """Attach a 1H and daily EMA bias. Both are shifted by one completed bar so
    a 15-minute candle only ever sees *closed* higher-timeframe information."""
    if mode in (None, "none"):
        d["h1_bias"] = 0
        d["d_bias"] = 0
        return d

    d = d.drop(columns=[c for c in ("hr", "h1_bias", "d_bias") if c in d.columns])
    g = d[["timestamp", "close"]].copy()
    g["hr"] = g.timestamp.dt.floor("h")
    h1 = g.groupby("hr", as_index=False).agg(close=("close", "last"))
    h1["ema20"] = h1.close.ewm(span=20, adjust=False, min_periods=20).mean()
    h1["ema50"] = h1.close.ewm(span=50, adjust=False, min_periods=50).mean()
    h1["h1_bias"] = np.select(
        [(h1.close > h1.ema50) & (h1.ema20 > h1.ema50),
         (h1.close < h1.ema50) & (h1.ema20 < h1.ema50)],
        [1, -1], default=0).astype(int)
    h1["h1_bias"] = h1["h1_bias"].shift(1).fillna(0).astype(int)
    d = d.assign(hr=d.timestamp.dt.floor("h")).merge(h1[["hr", "h1_bias"]], on="hr", how="left")
    d["h1_bias"] = d["h1_bias"].fillna(0).astype(int)

    gd = d.groupby("day", as_index=False).agg(close=("close", "last"))
    gd["dema20"] = gd.close.ewm(span=20, adjust=False, min_periods=20).mean()
    gd["d_bias"] = np.where(gd.close > gd.dema20, 1, -1).astype(int)
    gd["d_bias"] = gd["d_bias"].shift(1).fillna(0).astype(int)
    d = d.merge(gd[["day", "d_bias"]], on="day", how="left")
    d["d_bias"] = d["d_bias"].fillna(0).astype(int)
    return d


def _add_opening_range(d, or_end):
    """Opening-range high/low from the first candles of the session, plus sticky
    flags that say whether price has broken that range in either direction."""
    d = d.drop(columns=[c for c in ("or_high", "or_low") if c in d.columns])
    d["tstr"] = d.timestamp.dt.strftime("%H:%M")
    orhl = (d[d.tstr <= or_end].groupby("day")
            .agg(or_high=("high", "max"), or_low=("low", "min")).reset_index())
    d = d.merge(orhl, on="day", how="left")
    after = d.tstr > or_end
    d["or_break_up"] = after & (d.close > d.or_high)
    d["or_break_down"] = after & (d.close < d.or_low)
    d["or_up"] = d.groupby("day")["or_break_up"].cummax().astype(int)
    d["or_down"] = d.groupby("day")["or_break_down"].cummax().astype(int)
    return d


def indicators(frame, cfg=None):
    cfg = cfg or {}
    or_end = cfg.get("or_end_time", "09:45")
    bias_mode = cfg.get("htf_bias", "none")
    d = frame.copy()
    d["timestamp"] = pd.to_datetime(d["timestamp"])
    d = d.sort_values("timestamp").drop_duplicates("timestamp").reset_index(drop=True)
    for c in ["open", "high", "low", "close", "volume"]:
        d[c] = pd.to_numeric(d[c], errors="coerce")
    d = d.dropna(subset=["timestamp", "open", "high", "low", "close"]).copy()
    d["ema9"] = d.close.ewm(span=9, adjust=False, min_periods=9).mean()
    d["ema15"] = d.close.ewm(span=15, adjust=False, min_periods=15).mean()
    prev = d.close.shift()
    tr = pd.concat([(d.high - d.low).abs(), (d.high - prev).abs(), (d.low - prev).abs()], axis=1).max(axis=1)
    d["atr"] = tr.rolling(14, min_periods=14).mean()
    d["session"] = d.timestamp.dt.date
    d["day"] = d.timestamp.dt.normalize()
    typical = (d.high + d.low + d.close) / 3
    vol = d.volume.fillna(0)
    d["vwap"] = (typical * vol).groupby(d.session).cumsum() / vol.groupby(d.session).cumsum().replace(0, np.nan)
    d["ema9_slope"] = d.ema9 - d.ema9.shift()
    d["ema15_slope"] = d.ema15 - d.ema15.shift()
    d["volume_ratio"] = d.volume / d.volume.rolling(20, min_periods=20).mean().replace(0, np.nan)
    up, down = d.high.diff(), -d.low.diff()
    plus = up.where((up > down) & (up > 0), 0.0)
    minus = down.where((down > up) & (down > 0), 0.0)
    atrw = tr.ewm(alpha=1 / 14, adjust=False, min_periods=14).mean().replace(0, np.nan)
    pdi = 100 * plus.ewm(alpha=1 / 14, adjust=False, min_periods=14).mean() / atrw
    mdi = 100 * minus.ewm(alpha=1 / 14, adjust=False, min_periods=14).mean() / atrw
    dx = 100 * (pdi - mdi).abs() / (pdi + mdi).replace(0, np.nan)
    d["adx"] = dx.ewm(alpha=1 / 14, adjust=False, min_periods=14).mean()
    bull = (d.ema9 > d.ema15) & (d.ema9.shift() <= d.ema15.shift())
    bear = (d.ema9 < d.ema15) & (d.ema9.shift() >= d.ema15.shift())
    d["cross"] = np.select([bull, bear], [1, -1], default=0)
    # Break of the prior bar's extreme = simple continuation confirmation.
    d["break_prior_up"] = d.close > d.groupby("day")["high"].shift()
    d["break_prior_down"] = d.close < d.groupby("day")["low"].shift()
    # Context layers.
    d = _add_htf_bias(d, bias_mode)
    d = _add_opening_range(d, or_end)
    pdhl = d.groupby("day", as_index=False).agg(dh=("high", "max"), dl=("low", "min"))
    pdhl["prev_high"] = pdhl.dh.shift(1)
    pdhl["prev_low"] = pdhl.dl.shift(1)
    d = d.merge(pdhl[["day", "prev_high", "prev_low"]], on="day", how="left")
    return d.sort_values("timestamp").reset_index(drop=True)


def _bias_up(d, cfg):
    mode = cfg.get("htf_bias", "none")
    if mode == "none":
        return pd.Series(True, index=d.index)
    if mode == "h1":
        return d.h1_bias > 0
    if mode == "daily":
        return d.d_bias > 0
    return (d.h1_bias > 0) & (d.d_bias > 0)          # h1_daily


def _bias_dn(d, cfg):
    mode = cfg.get("htf_bias", "none")
    if mode == "none":
        return pd.Series(True, index=d.index)
    if mode == "h1":
        return d.h1_bias < 0
    if mode == "daily":
        return d.d_bias < 0
    return (d.h1_bias < 0) & (d.d_bias < 0)


def signals(d, name, cfg):
    c = d.cross
    bu, bd = _bias_up(d, cfg), _bias_dn(d, cfg)
    if name == "baseline": return c != 0
    if name == "vwap":
        return ((c == 1) & (d.close > d.vwap)) | ((c == -1) & (d.close < d.vwap))
    if name == "vwap_slope":
        return (((c == 1) & (d.close > d.vwap) & (d.ema9_slope > 0) & (d.ema15_slope > 0)) |
                ((c == -1) & (d.close < d.vwap) & (d.ema9_slope < 0) & (d.ema15_slope < 0)))
    if name == "vwap_volume":
        return (((c == 1) & (d.close > d.vwap) & (d.volume_ratio >= cfg["volume_ratio_threshold"])) |
                ((c == -1) & (d.close < d.vwap) & (d.volume_ratio >= cfg["volume_ratio_threshold"])))
    if name == "vwap_adx":
        return (((c == 1) & (d.close > d.vwap) & (d.adx >= cfg["adx_threshold"])) |
                ((c == -1) & (d.close < d.vwap) & (d.adx >= cfg["adx_threshold"])))
    if name == "trend":
        return ((c == 1) & (d.close > d.vwap) & bu) | ((c == -1) & (d.close < d.vwap) & bd)
    if name == "trend_orb":
        return (((c == 1) & (d.close > d.vwap) & bu & (d.or_up > 0)) |
                ((c == -1) & (d.close < d.vwap) & bd & (d.or_down > 0)))
    if name == "trend_orb_bt":
        return (((c == 1) & (d.close > d.vwap) & bu & (d.or_up > 0) & d.break_prior_up) |
                ((c == -1) & (d.close < d.vwap) & bd & (d.or_down > 0) & d.break_prior_down))
    raise ValueError(f"Unknown filter {name}")


def prepare_options(options):
    required = ["timestamp", "symbol", "underlying", "expiry", "strike", "option_type", "open", "high", "low", "close", "volume"]
    missing = [x for x in required if x not in options.columns]
    if missing: raise ValueError("Option CSV missing columns: " + ", ".join(missing))
    d = options.copy()
    d["timestamp"] = pd.to_datetime(d.timestamp)
    d["expiry"] = pd.to_datetime(d.expiry).dt.date
    d["underlying"] = d.underlying.astype(str).str.upper()
    d["option_type"] = d.option_type.astype(str).str.upper()
    for c in ["strike", "open", "high", "low", "close", "volume"]:
        d[c] = pd.to_numeric(d[c], errors="coerce")
    if "lot_size" not in d: d["lot_size"] = np.nan
    d["lot_size"] = pd.to_numeric(d["lot_size"], errors="coerce")
    return d.dropna(subset=["timestamp", "strike", "open", "high", "low", "close"]).sort_values(["underlying", "symbol", "timestamp"])


def metrics(trades, initial_capital):
    if trades is None or len(trades) == 0:
        return {"trades": 0, "win_rate_pct": 0.0, "net_pnl": 0.0, "profit_factor": 0.0, "avg_trade": 0.0,
                "max_drawdown": 0.0, "max_consecutive_losses": 0}
    p = pd.to_numeric(trades.net_pnl)
    win_sum = p[p > 0].sum()
    loss_sum = -p[p < 0].sum()
    equity = initial_capital + p.cumsum()
    dd = equity - equity.cummax()
    streak = cur = 0
    for x in p:
        if x < 0: cur += 1; streak = max(streak, cur)
        else: cur = 0
    return {"trades": int(len(p)), "win_rate_pct": round(float((p > 0).mean() * 100), 2),
            "net_pnl": round(float(p.sum()), 2),
            "profit_factor": round(float(win_sum / loss_sum), 3) if loss_sum > 0 else (999.0 if win_sum > 0 else 0.0),
            "avg_trade": round(float(p.mean()), 2), "max_drawdown": round(float(dd.min()), 2),
            "max_consecutive_losses": int(streak)}


def _size_position(cfg, entry, stop, lot, equity, market):
    """Return quantity. 'risk' mode sizes so (entry-stop) costs ~risk_pct of
    equity, capped by max_capital_pct of equity deployed as premium."""
    lot = int(lot)
    if cfg.get("sizing_mode", "fixed_lots") != "risk":
        return lot
    risk_per_share = entry - stop
    if risk_per_share <= 0 or not np.isfinite(risk_per_share):
        return lot
    risk_amount = max(0.0, equity) * float(cfg.get("risk_pct", 0.01))
    lots = int(risk_amount // (risk_per_share * lot))
    max_cap = max(0.0, equity) * float(cfg.get("max_capital_pct", 0.6))
    if entry > 0:
        lots = min(lots, int(max_cap // (entry * lot)))
    lots = max(1, lots)
    return lots * lot


def backtest_one(index_frame, options, market, filter_name, cfg):
    idx = indicators(index_frame, cfg).reset_index(drop=True)
    options = prepare_options(options)
    opt = options[options.underlying == market].copy()
    if opt.empty: return pd.DataFrame(), {"signals": 0, "skipped_no_contract": 0}
    maps = {}
    for sym, grp in opt.groupby("symbol"):
        maps[sym] = grp.sort_values("timestamp").drop_duplicates("timestamp").set_index("timestamp")
    # Precompute, per option type, the eligible expiries and their contract
    # metadata so the per-signal lookup is O(#expiries), not O(#option rows).
    min_dte, max_dte = int(cfg["min_dte"]), int(cfg["max_dte"])
    contracts_by_type = {}
    for typ in ("CE", "PE"):
        sub = opt[opt.option_type == typ][["symbol", "strike", "expiry", "lot_size"]].drop_duplicates()
        contracts_by_type[typ] = sub
        sub_by_exp = {exp: g for exp, g in sub.groupby("expiry")}
        contracts_by_type[typ + "_by_exp"] = sub_by_exp
    expiries_sorted = sorted(opt["expiry"].dropna().unique())
    signal_mask = signals(idx, filter_name, cfg).fillna(False).to_numpy()
    exit_on_opposite = bool(cfg.get("exit_on_opposite", True))
    trades, skipped, trades_day = [], 0, {}
    equity = float(cfg["initial_capital"])
    active = None
    for i in range(len(idx) - 1):
        row = idx.iloc[i]
        nextrow = idx.iloc[i + 1]
        t = pd.Timestamp(row.timestamp)
        nt = pd.Timestamp(nextrow.timestamp)
        # Force flat before end-of-day; no overnight option positions.
        force_exit = nt.date() != t.date() or t.strftime("%H:%M") >= cfg["force_exit_time"]
        if active is not None:
            cmap = maps[active["symbol"]]
            # At this loop iteration, `row` is the option entry candle for a trade entered
            # at the previous iteration's next-candle open. Evaluate its intrabar range now.
            if t in cmap.index:
                bar = cmap.loc[t]
                if isinstance(bar, pd.DataFrame): bar = bar.iloc[-1]
                hit_stop = float(bar.low) <= active["stop"]
                hit_target = np.isfinite(active["target"]) and float(bar.high) >= active["target"]
                opposite = exit_on_opposite and (int(row.cross) == -active["side"])
                timed = (i - active["entry_i"]) >= int(cfg["max_holding_bars"])
                if hit_stop or hit_target or opposite or timed or force_exit:
                    if hit_stop: raw_exit, reason = active["stop"], "stop"
                    elif hit_target: raw_exit, reason = active["target"], "target"
                    elif force_exit: raw_exit, reason = float(bar.close), "session_end"
                    elif opposite: raw_exit, reason = float(bar.close), "opposite_cross"
                    else: raw_exit, reason = float(bar.close), "max_holding"
                    exit_px = raw_exit * (1 - float(cfg["slippage_pct_per_side"]))
                    qty = int(active["qty"])
                    gross = (exit_px - active["entry_px"]) * qty
                    turnover = (active["entry_px"] + exit_px) * qty
                    costs = 2 * float(cfg["brokerage_per_order"]) + turnover * float(cfg["other_cost_pct_per_side"])
                    net = gross - costs
                    trades.append({"market": market, "filter": filter_name, "symbol": active["symbol"],
                        "option_type": active["option_type"], "expiry": str(active["expiry"]), "strike": active["strike"],
                        "entry_signal_time": str(active["signal_time"]), "entry_time": str(active["entry_time"]),
                        "exit_time": str(t), "entry_price": round(active["entry_px"], 2), "exit_price": round(exit_px, 2),
                        "quantity": qty, "gross_pnl": round(gross, 2), "costs": round(costs, 2), "net_pnl": round(net, 2),
                        "exit_reason": reason, "holding_bars": i - active["entry_i"]})
                    equity += net
                    active = None
        if active is not None: continue
        if not signal_mask[i]: continue
        if t.strftime("%H:%M") > cfg["last_entry_time"]: continue
        day = t.date()
        if trades_day.get(day, 0) >= int(cfg["max_trades_per_day"]): continue
        side = int(row.cross)
        desired = "CE" if side == 1 else "PE"
        # Eligible expiries within the DTE window, nearest expiry first.
        elig = [e for e in expiries_sorted if min_dte <= (e - day).days <= max_dte]
        if not elig:
            skipped += 1; continue
        by_exp = contracts_by_type[desired + "_by_exp"]
        expiry = next((e for e in elig if e in by_exp), None)
        if expiry is None:
            skipped += 1; continue
        expiry_contracts = by_exp[expiry]
        strikes = expiry_contracts.assign(dist=(expiry_contracts.strike - float(row.close)).abs()).sort_values(["dist", "strike"])
        chosen = None
        for _, c in strikes.iterrows():
            cmap = maps.get(c.symbol)
            if cmap is not None and nt in cmap.index:
                b = cmap.loc[nt]
                if isinstance(b, pd.DataFrame): b = b.iloc[-1]
                if float(b.open) > 0:
                    chosen = (c, b); break
        if chosen is None: skipped += 1; continue
        contract, obar = chosen
        cmap = maps[contract.symbol]
        # Premium ATR is computed only from that option's candles up to the entry candle.
        hist = cmap.loc[:nt].tail(15)
        prevclose = hist.close.shift()
        tr = pd.concat([(hist.high - hist.low).abs(), (hist.high - prevclose).abs(), (hist.low - prevclose).abs()], axis=1).max(axis=1)
        opt_atr = float(tr.tail(14).mean()) if len(tr.dropna()) >= 5 else float((hist.high - hist.low).tail(5).mean())
        if not np.isfinite(opt_atr) or opt_atr <= 0: skipped += 1; continue
        entry_raw = float(obar.open)
        entry = entry_raw * (1 + float(cfg["slippage_pct_per_side"]))
        # Stop: option-premium ATR, optionally floored by the recent premium swing low.
        stop = max(0.05, entry - float(cfg["stop_atr"]) * opt_atr)
        if cfg.get("stop_mode", "atr") == "structure":
            swing_low = float(hist.low.tail(int(cfg.get("struct_lookback", 5))).min())
            struct_stop = swing_low * (1 - float(cfg["slippage_pct_per_side"]))
            stop = max(stop, min(struct_stop, entry - 0.05))
        target = entry + float(cfg["target_atr"]) * opt_atr
        lot = contract.lot_size
        if pd.isna(lot) or lot <= 0: lot = int(cfg["lot_sizes"][market])
        qty = _size_position(cfg, entry, stop, lot, equity, market)
        active = {"symbol": contract.symbol, "option_type": desired, "expiry": contract.expiry,
                "strike": float(contract.strike), "side": side, "entry_time": nt, "signal_time": t,
                "signal_i": i, "entry_i": i + 1, "entry_px": entry, "stop": stop,
                "target": target, "atr": opt_atr,
                "lot_size": int(lot), "qty": int(qty)}
        trades_day[day] = trades_day.get(day, 0) + 1
    # Don't silently leave an open position at end of data.
    if active is not None:
        cmap = maps[active["symbol"]]
        valid = cmap[cmap.index >= active["entry_time"]]
        if not valid.empty:
            b = valid.iloc[-1]
            exit_px = float(b.close) * (1 - float(cfg["slippage_pct_per_side"]))
            qty = int(active["qty"])
            gross = (exit_px - active["entry_px"]) * qty
            turnover = (active["entry_px"] + exit_px) * qty
            costs = 2 * float(cfg["brokerage_per_order"]) + turnover * float(cfg["other_cost_pct_per_side"])
            trades.append({"market": market, "filter": filter_name, "symbol": active["symbol"], "option_type": active["option_type"],
                "expiry": str(active["expiry"]), "strike": active["strike"], "entry_signal_time": str(active["signal_time"]),
                "entry_time": str(active["entry_time"]), "exit_time": str(valid.index[-1]), "entry_price": round(active["entry_px"], 2),
                "exit_price": round(exit_px, 2), "quantity": qty, "gross_pnl": round(gross, 2), "costs": round(costs, 2),
                "net_pnl": round(gross - costs, 2), "exit_reason": "end_of_data", "holding_bars": None})
    return pd.DataFrame(trades), {"signals": int(signal_mask.sum()), "skipped_no_contract": int(skipped)}


def run_comparison(index_frame, options, market, cfg):
    idx = indicators(index_frame, cfg)
    split_idx = int(len(idx) * float(cfg["train_fraction"]))
    if split_idx < 30 or split_idx >= len(idx) - 5:
        raise ValueError("Not enough data for chronological train/test split. Download a longer period.")
    split_time = pd.Timestamp(idx.iloc[split_idx].timestamp)
    # Split underlying candles before computing signals to avoid future rows affecting test; warm-up naturally occurs.
    train_idx = idx[idx.timestamp < split_time].copy()
    test_idx = idx[idx.timestamp >= split_time].copy()
    results, all_trades = [], []
    for filt in FILTERS:
        train, q1 = backtest_one(train_idx, options, market, filt, cfg)
        test, q2 = backtest_one(test_idx, options, market, filt, cfg)
        for period, tr, q in [("train", train, q1), ("test", test, q2)]:
            m = metrics(tr, cfg["initial_capital"])
            results.append({"market": market, "filter": filt, "period": period, **m,
                            "signals": q["signals"], "skipped_no_contract": q["skipped_no_contract"]})
            if not tr.empty:
                tr = tr.copy(); tr["period"] = period; all_trades.append(tr)
    comparison = pd.DataFrame(results)
    # Filter selection (train-side only, never test-side): require a robust trade floor
    # and a positive edge (avg_trade > 0), then rank by net_pnl.
    trainres = comparison[comparison.period == "train"].copy()
    eligible = trainres[trainres.trades >= int(cfg["min_train_trades"])]
    if eligible.empty: eligible = trainres[trainres.trades > 0]
    eligible = eligible[eligible.avg_trade > 0]
    if not eligible.empty:
        eligible = eligible.sort_values("net_pnl", ascending=False)
        selected = eligible.iloc[0]["filter"]
    else:
        selected = "baseline"
    trades = pd.concat(all_trades, ignore_index=True) if all_trades else pd.DataFrame()
    return comparison, trades, selected, str(split_time)


def equity_curve(trades, initial_capital):
    if trades.empty: return pd.DataFrame(columns=["exit_time", "equity", "drawdown"])
    d = trades.sort_values("exit_time").copy()
    d["equity"] = float(initial_capital) + d.net_pnl.cumsum()
    d["peak"] = d.equity.cummax()
    d["drawdown"] = d.equity - d.peak
    return d[["exit_time", "market", "filter", "period", "net_pnl", "equity", "drawdown"]]
