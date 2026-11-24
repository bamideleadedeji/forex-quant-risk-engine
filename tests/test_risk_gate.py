import unittest
from datetime import datetime, timezone
from risk.position_size import compute_dynamic_lot_size
from risk.gatekeeper import evaluate_risk_gate


class DummyPos:
    def __init__(self, symbol):
        self.symbol = symbol


class TestFQRERiskControls(unittest.TestCase):
    def setUp(self):
        self.cfg = {
            "risk_per_trade_pct": 0.0035,
            "max_open_positions": 3,
            "max_usd_exposure": 2,
            "max_drawdown_pct": 0.08,
            "max_spread_pips": 2.5,
            "friday_cutoff_hour_utc": 15,
        }
        self.tuesday = datetime(2026, 11, 24, 10, 0, tzinfo=timezone.utc)

    def test_dynamic_lot_sizing(self):
        lots = compute_dynamic_lot_size(equity=10000.0, sl_distance=0.0035, tick_size=0.00001, tick_value=1.0, risk_pct=0.0035)
        self.assertEqual(lots, 0.10)

    def test_correlation_guard_blocks_third_usd_pair(self):
        open_pos = [DummyPos("GBPUSDm"), DummyPos("USDCHFm")]
        ok, trail = evaluate_risk_gate("EURUSDm", "SELL", 0.8, 10050.0, 10068.0, 9500.0, open_pos, self.cfg, True, "PASS", self.tuesday)
        self.assertFalse(ok)
        self.assertTrue(any("Correlation Guard = BLOCKED" in step for step in trail))

    def test_drawdown_circuit_breaker(self):
        ok, trail = evaluate_risk_gate("EURUSDm", "BUY", 0.8, 9100.0, 10000.0, 8500.0, [], self.cfg, True, "PASS", self.tuesday)
        self.assertFalse(ok)
        self.assertTrue(any("Drawdown Circuit Breaker = TRIPPED" in step for step in trail))


if __name__ == "__main__":
    unittest.main()
