import time
import unittest
from unittest.mock import patch

import ai_indicator


class NewsFreshnessOrderTests(unittest.TestCase):
    def test_google_source_scoped_candidates_are_sorted_before_truncation(self):
        now = int(time.time())
        rss = f"""<rss><channel>
          <item>
            <title>Older Apple market story - CNBC</title>
            <link>https://news.google.com/older</link>
            <source>CNBC</source>
            <pubDate>{time.strftime('%a, %d %b %Y %H:%M:%S GMT', time.gmtime(now - 7200))}</pubDate>
          </item>
          <item>
            <title>Jim Cramer says these stocks can win as yields rise - CNBC</title>
            <link>https://news.google.com/newest</link>
            <source>CNBC</source>
            <pubDate>{time.strftime('%a, %d %b %Y %H:%M:%S GMT', time.gmtime(now - 60))}</pubDate>
          </item>
          <item>
            <title>Apple prior legal update - CNBC</title>
            <link>https://news.google.com/middle</link>
            <source>CNBC</source>
            <pubDate>{time.strftime('%a, %d %b %Y %H:%M:%S GMT', time.gmtime(now - 3600))}</pubDate>
          </item>
        </channel></rss>""".encode()

        with patch.object(ai_indicator, "_fetch_public_url", return_value=rss):
            articles = ai_indicator._fetch_google_news_articles(
                "AAPL",
                2,
                source_domain="cnbc.com",
                feed_provider="cnbc",
            )

        self.assertEqual(
            [article["title"] for article in articles],
            [
                "Jim Cramer says these stocks can win as yields rise",
                "Apple prior legal update",
            ],
        )
        self.assertGreaterEqual(articles[0]["published_ts"], articles[1]["published_ts"])

    def test_news_watch_uses_newest_validated_ai_article(self):
        now = int(time.time())
        metadata = [
            {
                "title": "AAPL newest headline",
                "source": "CNBC",
                "feed_provider": "cnbc",
                "url": "https://example.com/newest",
                "published_ts": now - 60,
            },
            {
                "title": "AAPL older headline",
                "source": "CNBC",
                "feed_provider": "cnbc",
                "url": "https://example.com/older",
                "published_ts": now - 3600,
            },
        ]
        # Simulate an analyzer returning the articles in the wrong order.
        analyzed = {
            "score": 0.0,
            "sentiment_pct": 50.0,
            "sentiment_direction": "NEUTRAL",
            "confidence": 0.5,
            "summary": "Mixed article evidence.",
            "key_factor": "Mixed evidence",
            "analysis_model": "test-model",
            "articles": [
                {**metadata[1], "relevance": "DIRECT", "sentiment": "NEUTRAL"},
                {**metadata[0], "relevance": "DIRECT", "sentiment": "NEUTRAL"},
            ],
        }
        provider_status = {"yahoo": "empty", "google": "empty", "cnbc": "ok", "errors": []}
        with patch.object(ai_indicator, "_fetch_news_article_metadata_cached", return_value=(metadata, provider_status)), \
             patch.object(ai_indicator, "_add_news_article_previews", side_effect=lambda items: items), \
             patch.object(ai_indicator, "get_ai_sentiment", return_value=analyzed):
            result = ai_indicator.build_screener_news_watch("AAPL")

        self.assertEqual(result["latest"]["title"], "AAPL newest headline")
        self.assertEqual(result["latest"]["published_ts"], now - 60)


if __name__ == "__main__":
    unittest.main()
