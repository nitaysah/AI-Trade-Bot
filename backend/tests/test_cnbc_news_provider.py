import time
import unittest
from unittest.mock import patch

import ai_indicator


class CnbcNewsProviderTests(unittest.TestCase):
    def setUp(self):
        ai_indicator._CNBC_RSS_CACHE.clear()
        ai_indicator._CNBC_RSS_FAILURE_CACHE.clear()

    def test_cnbc_rss_parser_keeps_recent_timestamped_items(self):
        rss = b"""
        <rss version="2.0"><channel>
          <item>
            <title>Tesla Roadster event postponed to October 15</title>
            <link>https://www.cnbc.com/2026/09/28/tesla-roadster-delay.html</link>
            <pubDate>Mon, 28 Sep 2026 19:27:00 GMT</pubDate>
            <description>Tesla rescheduled the outdoor Roadster event.</description>
          </item>
          <item>
            <title>Old Tesla headline</title>
            <link>https://www.cnbc.com/old.html</link>
            <pubDate>Mon, 01 Jan 2024 00:00:00 GMT</pubDate>
          </item>
        </channel></rss>
        """
        with patch.object(ai_indicator, "_fetch_public_url", return_value=rss):
            items = ai_indicator._fetch_cnbc_rss_feed(
                "autos",
                "https://www.cnbc.com/id/10000101/device/rss/rss.html",
            )

        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["source"], "CNBC")
        self.assertEqual(items[0]["feed_provider"], "cnbc")
        self.assertEqual(items[0]["title"], "Tesla Roadster event postponed to October 15")
        self.assertGreater(items[0]["published_ts"], 0)

    def test_metadata_merge_preserves_newest_cnbc_article_across_providers(self):
        now = int(time.time())
        google = [{
            "title": "Tesla related context",
            "source": "Reuters",
            "feed_provider": "google",
            "url": "https://news.google.com/tesla-context",
            "published_ts": now - 900,
        }]
        cnbc = [{
            "title": "Tesla Roadster event postponed to October 15",
            "source": "CNBC",
            "feed_provider": "cnbc",
            "url": "https://www.cnbc.com/tesla-roadster-delay",
            "published_ts": now - 60,
        }]
        with patch.object(ai_indicator, "_fetch_google_news_articles", return_value=google), \
             patch.object(ai_indicator, "_fetch_cnbc_news_articles", return_value=(cnbc, [])):
            articles, status = ai_indicator._fetch_news_article_metadata("TSLA", 8)

        self.assertNotIn("yahoo", status)
        self.assertEqual(status["google"], "ok")
        self.assertEqual(status["cnbc"], "ok")
        self.assertEqual(articles[0]["source"], "CNBC")
        self.assertEqual(articles[0]["published_ts"], now - 60)
        self.assertEqual({item["feed_provider"] for item in articles}, {"google", "cnbc"})

    def test_cnbc_uses_google_site_index_when_direct_feed_is_blocked(self):
        indexed = [{
            "title": "Tesla Roadster event postponed",
            "source": "CNBC",
            "feed_provider": "cnbc",
            "url": "https://www.cnbc.com/tesla-roadster-delay",
            "published_ts": int(time.time()) - 30,
        }]
        with patch.object(ai_indicator, "_fetch_cnbc_rss_feed", side_effect=RuntimeError("HTTP 403")), \
             patch.object(ai_indicator, "_fetch_google_news_articles", return_value=indexed) as google_fetch:
            articles, errors = ai_indicator._fetch_cnbc_news_articles("TSLA", 8)

        self.assertEqual(len(articles), 1)
        self.assertEqual(articles[0]["feed_provider"], "cnbc")
        self.assertEqual(len(errors), len(ai_indicator.CNBC_RSS_FEEDS))
        google_fetch.assert_called_once_with(
            "TSLA",
            8,
            source_domain="cnbc.com",
            feed_provider="cnbc",
        )

    def test_tsla_roadster_headline_is_relevant_without_tesla_in_title(self):
        item = {
            "title": "Roadster event postponed to October 15",
            "source": "CNBC",
            "feed_provider": "cnbc",
            "url": "https://www.cnbc.com/roadster-delay",
            "published_ts": int(time.time()) - 30,
        }
        with patch.object(ai_indicator, "_fetch_cnbc_rss_feed", return_value=[item]), \
             patch.object(ai_indicator, "_fetch_google_news_articles", return_value=[]):
            articles, errors = ai_indicator._fetch_cnbc_news_articles("TSLA", 8)

        self.assertFalse(errors)
        self.assertEqual(articles[0]["title"], "Roadster event postponed to October 15")


if __name__ == "__main__":
    unittest.main()
