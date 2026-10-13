import unittest
from datetime import datetime
from zoneinfo import ZoneInfo

from src.fyers_client import FyersClient
from src.telegram_notifier import TelegramNotifier

IST = ZoneInfo("Asia/Kolkata")


class LiveOptionParsingTests(unittest.TestCase):
    def test_parses_ce_and_pe(self):
        self.assertEqual(FyersClient._parse_live_option("BSE:SENSEX25OCT78000CE"),
                         {"strike": 78000.0, "option_type": "CE"})
        self.assertEqual(FyersClient._parse_live_option("NSE:NIFTY25OCT25000PE"),
                         {"strike": 25000.0, "option_type": "PE"})

    def test_strips_leading_day_token(self):
        # Weekly encoding may prepend a day token; only the last 5 digits matter.
        parsed = FyersClient._parse_live_option("BSE:SENSEX2510378000CE")
        self.assertEqual(parsed["strike"], 78000.0)
        self.assertEqual(parsed["option_type"], "CE")

    def test_rejects_non_option(self):
        self.assertIsNone(FyersClient._parse_live_option("BSE:SENSEX25OCTFUT"))


class TelegramFormatTests(unittest.TestCase):
    def test_format_contains_key_fields(self):
        sig = {"market": "SENSEX", "filter": "trend", "option_type": "CE",
               "symbol": "BSE:SENSEX25OCT78000CE", "strike": 78000, "spot": 77980.5,
               "signal_time": "2026-10-13 10:30:00", "entry": 210.5, "stop": 180.2,
               "target": 260.8, "risk_pct": 14.4, "reward_pct": 23.9, "lot_size": 20,
               "qty": 20, "risk_amount": 150}
        msg = TelegramNotifier.format_signal(sig)
        for token in ("SENSEX", "78000", "210.50", "180.20", "260.80", "LONG CE"):
            self.assertIn(token, msg)


class MarketClockTests(unittest.TestCase):
    def setUp(self):
        import live_scanner
        self.ls = live_scanner

    def _dt(self, s):
        return datetime.strptime(s, "%Y-%m-%d %H:%M:%S").replace(tzinfo=IST)

    def test_market_hours(self):
        self.assertFalse(self.ls.is_market_open(self._dt("2026-10-13 09:10:00")))
        self.assertTrue(self.ls.is_market_open(self._dt("2026-10-13 10:00:00")))
        self.assertFalse(self.ls.is_market_open(self._dt("2026-10-17 11:00:00")))  # Saturday

    def test_last_closed_bar(self):
        self.assertEqual(self.ls.last_closed_candle_time(self._dt("2026-10-13 10:31:00")),
                         datetime(2026, 10, 13, 10, 30))
        # Exactly on a boundary the just-ended bar is the previous one.
        self.assertEqual(self.ls.last_closed_candle_time(self._dt("2026-10-13 10:45:00")),
                         datetime(2026, 10, 13, 10, 30))

    def test_blackout_window(self):
        cfg = {"no_entry_windows": ["10:00-11:00"]}
        self.assertTrue(self.ls.within_blackout(self._dt("2026-10-13 10:30:00"), cfg))
        self.assertFalse(self.ls.within_blackout(self._dt("2026-10-13 09:30:00"), cfg))
        self.assertFalse(self.ls.within_blackout(self._dt("2026-10-13 12:00:00"), cfg))

    def test_state_file_roundtrip(self):
        import tempfile, os
        p = os.path.join(tempfile.mkdtemp(), "seen.txt")
        self.ls.save_seen(p, {"SENSEX|2026-10-13 10:30:00|1"})
        self.assertEqual(self.ls.load_seen(p), {"SENSEX|2026-10-13 10:30:00|1"})


if __name__ == "__main__":
    unittest.main()
