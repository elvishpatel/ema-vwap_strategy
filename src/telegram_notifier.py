"""Minimal Telegram notifier using the free Bot API.

No third-party library: a single HTTPS POST to the Bot API. Configure a bot in
Telegram (see README) and set TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID in .env.
"""
import os

import requests

API = "https://api.telegram.org"


class TelegramNotifier:
    def __init__(self, bot_token=None, chat_id=None, dry_run=False):
        self.bot_token = (bot_token or os.getenv("TELEGRAM_BOT_TOKEN", "")).strip()
        self.chat_id = str(chat_id or os.getenv("TELEGRAM_CHAT_ID", "")).strip()
        self.dry_run = dry_run
        self.enabled = bool(self.bot_token and self.chat_id)
        self.session = requests.Session()

    def send(self, text):
        """Send a message. In dry-run mode, print instead of calling the API."""
        if self.dry_run or not self.enabled:
            self._print_safe("\n--- TELEGRAM (dry run) ---\n" + text + "\n--------------------------")
            return {"dry_run": True}
        url = f"{API}/bot{self.bot_token}/sendMessage"
        resp = self.session.post(
            url,
            json={"chat_id": self.chat_id, "text": text,
                  "parse_mode": "HTML", "disable_web_page_preview": True},
            timeout=20,
        )
        try:
            body = resp.json()
        except Exception:
            body = {"ok": False, "raw": resp.text[:300]}
        if resp.status_code >= 400 or not body.get("ok", False):
            raise RuntimeError(f"Telegram send failed (HTTP {resp.status_code}): {body}")
        return body

    @staticmethod
    def _print_safe(text):
        """Print without crashing on legacy Windows console encodings."""
        try:
            print(text)
        except UnicodeEncodeError:
            print(text.encode("ascii", "replace").decode("ascii"))

    @staticmethod
    def format_signal(sig):
        """Render a signal dict as an HTML Telegram message."""
        arrow = "\U0001F7E2 LONG CE" if sig["option_type"] == "CE" else "\U0001F534 LONG PE"
        lines = [
            f"<b>{arrow} — {sig['market']}</b>",
            f"<b>Setup:</b> {sig.get('filter','trend')} (EMA 9/15 + VWAP + 1H bias)",
            f"<b>Signal candle:</b> {sig['signal_time']}",
            f"<b>Spot:</b> {sig['spot']:.2f}",
            f"<b>Strike:</b> {sig['strike']:.0f} {sig['option_type']}  (<i>near-ATM</i>)",
            f"<b>Contract:</b> <code>{sig['symbol']}</code>",
            f"<b>Expiry:</b> {sig.get('expiry','-')}",
            "",
            f"<b>Entry (approx):</b> \u20b9{sig['entry']:.2f}",
            f"<b>Stop loss:</b> \u20b9{sig['stop']:.2f}  ({sig['risk_pct']:.1f}% move)",
            f"<b>Target:</b> \u20b9{sig['target']:.2f}  ({sig['reward_pct']:.1f}% move)",
            f"<b>Lot size:</b> {sig.get('lot_size','-')}  |  "
            f"<b>Suggested qty:</b> {sig.get('qty','-')} "
            f"(\u20b9{sig.get('risk_amount',0):.0f} risk)",
            "",
            f"<i>Enter at the NEXT 15-min candle open. Re-check premium before order. "
            f"Not investment advice.</i>",
        ]
        return "\n".join(lines)
