import time
import unittest
from unittest.mock import patch

import ai_indicator
from screener_strategy import assess_event_catalyst


class ScreenerNewsFallbackStatusTests(unittest.TestCase):
    def test_fresh_google_fallback_remains_current_for_event_gate(self):
        published_ts = int(time.time()) - 60
        article = {
            "title": "TSLA Roadster event postponed",
            "source": "Google News source",
            "feed_provider": "google",
            "url": "https://example.com/tsla-roadster",
            "published_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(published_ts)),
            "published_ts": published_ts,
            "age_hours": 0.0,
            "excerpt": "The event was rescheduled due to weather.",
        }
        provider_status = {
            "yahoo": "error",
            "google": "ok",
            "errors": ["Yahoo: HTTP 429"],
        }
        sentiment = {
            "sentiment_pct": 25.0,
            "sentiment_direction": "BEARISH",
            "confidence": 0.8,
            "summary": "A recent event postponement is negative for the ticker.",
            "key_factor": "Event postponement",
            "analysis_model": "test-model",
            "articles": [{
                **article,
                "id": "fresh-roadster-event",
                "relevance": "DIRECT",
                "sentiment": "BEARISH",
                "impact": "HIGH",
                "summary": article["excerpt"],
                "reason": "Direct recent catalyst.",
            }],
        }

        with patch.object(ai_indicator, "_fetch_news_article_metadata_cached", return_value=([article], provider_status)), \
             patch.object(ai_indicator, "_add_news_article_previews", side_effect=lambda items: items), \
             patch.object(ai_indicator, "get_ai_sentiment", return_value=sentiment):
            result = ai_indicator.build_screener_news_watch("TSLA")

        self.assertEqual(result["status"], "current")
        self.assertEqual(result["provider"], "Google News fallback")
        self.assertEqual(result["latest"]["published_ts"], published_ts)
        self.assertEqual(result["sentiment_direction"], "BEARISH")

        event = assess_event_catalyst(result, now_ts=published_ts + 60)
        self.assertTrue(event["eligible"])
        self.assertEqual(event["direction"], "BEARISH")


if __name__ == "__main__":
    unittest.main()
