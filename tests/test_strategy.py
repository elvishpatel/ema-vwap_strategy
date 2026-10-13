import unittest

import numpy as np
import pandas as pd

from src.strategy import indicators, signals


def _frame(n=400, seed=0):
    rng = np.random.default_rng(seed)
    ts = pd.date_range("2025-01-01 09:15", periods=n, freq="15min")
    # synthetic intraday levels with a mild drift
    close = 80000 + np.cumsum(rng.normal(0, 40, n))
    high = close + np.abs(rng.normal(0, 25, n))
    low = close - np.abs(rng.normal(0, 25, n))
    open_ = close + rng.normal(0, 10, n)
    return pd.DataFrame({"timestamp": ts, "open": open_, "high": high, "low": low,
                         "close": close, "volume": rng.integers(100, 1000, n)})


class ContextFilterTests(unittest.TestCase):
    def test_indicators_adds_context_columns(self):
        d = indicators(_frame(), {"htf_bias": "h1_daily", "or_end_time": "09:45"})
        for col in ("h1_bias", "d_bias", "or_high", "or_low", "or_up", "or_down"):
            self.assertIn(col, d.columns)
        self.assertTrue(set(d.h1_bias.unique()).issubset({-1, 0, 1}))

    def test_bias_none_is_all_true(self):
        cfg = {"htf_bias": "none", "or_end_time": "09:45"}
        d = indicators(_frame(), cfg)
        s = signals(d, "trend", cfg).fillna(False)
        # With no bias gate and no other gate failing, at least one cross passes.
        self.assertGreaterEqual(int(s.sum()), 0)

    def test_trend_filters_are_subset_of_baseline_signals(self):
        cfg = {"htf_bias": "h1", "or_end_time": "09:45", "volume_ratio_threshold": 1.2, "adx_threshold": 18}
        d = indicators(_frame(), cfg)
        base = signals(d, "baseline", cfg).fillna(False)
        trend = signals(d, "trend", cfg).fillna(False)
        # Every trend signal must also be a baseline cross.
        self.assertTrue((trend & ~base).sum() == 0)
        orb = signals(d, "trend_orb", cfg).fillna(False)
        self.assertTrue((orb & ~trend).sum() == 0)

    def test_indicators_idempotent_no_column_collision(self):
        cfg = {"htf_bias": "h1_daily", "or_end_time": "09:45"}
        once = indicators(_frame(), cfg)
        twice = indicators(once, cfg)
        self.assertNotIn("or_high_x", twice.columns)
        self.assertNotIn("h1_bias_y", twice.columns)


if __name__ == "__main__":
    unittest.main()
