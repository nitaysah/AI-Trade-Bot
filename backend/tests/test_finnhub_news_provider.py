import json
import time
import unittest
from unittest.mock import patch

import ai_indicator


class FinnhubNewsProviderTests(unittest.TestCase):
    def test_finnhub_news_is_timestamp_sorted_and_bounded(self):
        now = int(time.time())
        payload = [
            {
                "headline": "Older AAPL headline",
                "source": "Older Wire",
                "url": "https://example.com/older",
                "datetime": now - 3600,
                "summary": "Older summary",
            },
            {
                "headline": "Newest AAPL headline",
                "source": "Current Wire",
                "url": "https://example.com/newest",
                "datetime": now - 30,
                "summary": "Newest summary",
            },
        ]

        with patch.object(ai_indicator.global_config, "FINNHUB_API_KEY", "test-key"), \
             patch.object(ai_indicator, "_fetch_public_url", return_value=json.dumps(payload).encode()) as fetch:
            articles = ai_indicator._fetch_finnhub_news_articles("AAPL", 1)

        self.assertEqual(len(articles), 1)
        self.assertEqual(articles[0]["title"], "Newest AAPL headline")
        self.assertEqual(articles[0]["feed_provider"], "finnhub")
        self.assertGreaterEqual(articles[0]["published_ts"], now - 30)
        request_url = fetch.call_args.args[0]
        self.assertIn("symbol=AAPL", request_url)
        self.assertIn("token=test-key", request_url)

    def test_finnhub_is_primary_but_fallbacks_remain_available(self):
        now = int(time.time())
        finnhub = [{
            "title": "AAPL current headline",
            "source": "Finnhub source",
            "feed_provider": "finnhub",
            "url": "https://example.com/current",
            "published_ts": now - 30,
        }]
        with patch.object(ai_indicator.global_config, "FINNHUB_API_KEY", "test-key"), \
             patch.object(ai_indicator, "_fetch_finnhub_news_articles", return_value=finnhub), \
             patch.object(ai_indicator, "_fetch_yahoo_news_articles", return_value=[]), \
             patch.object(ai_indicator, "_fetch_google_news_articles", return_value=[]), \
             patch.object(ai_indicator, "_fetch_cnbc_news_articles", return_value=([], [])):
            articles, status = ai_indicator._fetch_news_article_metadata("AAPL", 8)

        self.assertEqual(status["finnhub"], "ok")
        self.assertEqual(articles[0]["feed_provider"], "finnhub")
        self.assertEqual(ai_indicator._news_provider_label(articles, status), "Finnhub")


if __name__ == "__main__":
    unittest.main()
