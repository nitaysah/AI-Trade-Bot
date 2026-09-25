import math
import json
import unittest

import main


class ChartJsonSafetyTests(unittest.TestCase):
    def test_sanitizer_replaces_non_finite_values_recursively(self):
        payload = {
            "primaryScan": {"price": float("nan"), "atr": float("inf")},
            "priceHistory": [{"close": 123.45, "rsi": float("-inf")}],
        }

        safe = main._sanitize_json_values(payload)

        self.assertIsNone(safe["primaryScan"]["price"])
        self.assertIsNone(safe["primaryScan"]["atr"])
        self.assertEqual(safe["priceHistory"][0]["close"], 123.45)
        self.assertIsNone(safe["priceHistory"][0]["rsi"])
        self.assertTrue(math.isfinite(safe["priceHistory"][0]["close"]))
        json.dumps(safe, allow_nan=False)

    def test_chart_history_requires_a_finite_close(self):
        self.assertFalse(main._has_usable_chart_history({"price_history": []}))
        self.assertFalse(main._has_usable_chart_history({"price_history": [{"close": None}]}))
        self.assertFalse(main._has_usable_chart_history({"price_history": [{"close": float("nan")}]}))
        self.assertTrue(main._has_usable_chart_history({"price_history": [{"close": 332.27}]}))


if __name__ == "__main__":
    unittest.main()
