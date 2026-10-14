"""Out-of-order completed-candle guard: only same-timeframe candles are comparable."""
import unittest

from screener_service import candle_key_regressed


class CandleKeyRegressedTests(unittest.TestCase):
    def test_older_candle_of_the_same_timeframe_regresses(self):
        self.assertTrue(candle_key_regressed("30MIN:1760000000", "30MIN:1759998200"))

    def test_same_or_newer_candle_does_not_regress(self):
        self.assertFalse(candle_key_regressed("30MIN:1760000000", "30MIN:1760000000"))
        self.assertFalse(candle_key_regressed("30MIN:1760000000", "30MIN:1760001800"))

    def test_different_timeframes_are_never_compared(self):
        # A daily candle stamped at midnight is "older" than a 30-minute candle from the same day.
        self.assertFalse(candle_key_regressed("30MIN:1760000000", "1DAY:1759900000"))
        self.assertFalse(candle_key_regressed("1HOUR:1760000000", "5MIN:1759999000"))

    def test_timeframe_case_is_ignored(self):
        self.assertTrue(candle_key_regressed("30min:1760000000", "30MIN:1759998200"))

    def test_missing_or_invalid_keys_never_regress(self):
        for previous, current in ((None, "30MIN:1"), ("", "30MIN:1"), ("30MIN:1", None), ("30MIN:abc", "30MIN:5"), ("30MIN:0", "30MIN:5")):
            self.assertFalse(candle_key_regressed(previous, current), (previous, current))


if __name__ == "__main__":
    unittest.main()
