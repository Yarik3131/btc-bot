import unittest
from strategy import (ema, rsi, atr, calc_position, interval_to_ms, filter_closed_candles)

class StrategyTests(unittest.TestCase):
    def test_interval_to_ms(self):
        self.assertEqual(interval_to_ms("15"), 900_000)
        self.assertEqual(interval_to_ms("60"), 3_600_000)
        self.assertEqual(interval_to_ms("D"), 86_400_000)
        with self.assertRaises(ValueError):
            interval_to_ms("unknown")

    def test_only_closed_candles_are_kept(self):
        candles = [
            {"time": 0, "close": 10},
            {"time": 900_000, "close": 11},
            {"time": 1_800_000, "close": 12},
        ]
        result = filter_closed_candles(
            candles, interval_ms=900_000, now_ms=1_800_000
        )
        self.assertEqual([c["close"] for c in result], [10, 11])

    def test_filter_rejects_invalid_interval(self):
        with self.assertRaises(ValueError):
            filter_closed_candles([], interval_ms=0, now_ms=1000)

    def test_ema_length(self):
        values=list(range(1,31))
        self.assertEqual(len(ema(values,10)),30)
        self.assertIsNone(ema(values,10)[8])
        self.assertIsNotNone(ema(values,10)[9])
    def test_rsi_bounds(self):
        values=list(range(1,40))
        result=rsi(values,14)
        self.assertTrue(99 <= result[-1] <= 100)
    def test_atr_positive(self):
        c=[{"high":i+2.0,"low":i,"close":i+1.0} for i in range(30)]
        self.assertGreater(atr(c,14)[-1],0)
    def test_position_respects_risk_budget(self):
        p=calc_position(31.5,80000,79200,0.01,0.00055,0.0005)
        self.assertLessEqual(p["qty_btc"]*(80000-79200),0.315)
        self.assertGreater(p["qty_btc"],0)

if __name__=="__main__": unittest.main()
