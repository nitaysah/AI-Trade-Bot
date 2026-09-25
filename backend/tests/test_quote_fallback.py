import unittest
from types import SimpleNamespace
from unittest.mock import patch

import main


class _UnsupportedWebull:
    def get_latest_prices(self, _symbols):
        return {}


class QuoteFallbackTests(unittest.TestCase):
    def setUp(self):
        main._quote_cache.clear()
        main._webull_unavailable_quote_cache.clear()

    def tearDown(self):
        main._quote_cache.clear()
        main._webull_unavailable_quote_cache.clear()

    def test_unsupported_webull_symbol_uses_yahoo_and_is_cached_as_fallback(self):
        engine = SimpleNamespace(uid="quote-fallback-test", broker=None)
        provider = _UnsupportedWebull()
        with patch.object(main, "_get_webull_market_data_broker", return_value=provider), \
             patch.object(main, "_get_yahoo_latest_price", return_value=42.50):
            result = main._get_cached_quote_detail(engine, "DJT", max_age=0)

        self.assertEqual(result["price"], 42.50)
        self.assertEqual(result["source"], "yahoo")
        self.assertIsNotNone(main._webull_unavailable_quote_cache.get("DJT", ttl_seconds=900))

    def test_batch_quote_path_uses_yahoo_for_webull_miss(self):
        engine = SimpleNamespace(uid="quote-batch-fallback-test", broker=None)
        provider = _UnsupportedWebull()
        with patch.object(main, "_get_webull_market_data_broker", return_value=provider), \
             patch.object(main, "_get_yahoo_latest_price", return_value=42.50):
            result = main._get_cached_latest_prices(engine, ["DJT"], max_age=0)

        self.assertEqual(result, {"DJT": 42.50})


if __name__ == "__main__":
    unittest.main()
