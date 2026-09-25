import unittest
from unittest.mock import patch

import ai_indicator
from screener_service import profile_revision


def article(title, published_ts, source="Reuters"):
    return {
        "title": title,
        "source": source,
        "feed_provider": "yahoo",
        "url": f"https://example.com/{published_ts}",
        "published_at": f"2026-09-11T{published_ts % 24:02d}:00:00Z",
        "published_ts": published_ts,
        "excerpt": f"Summary for {title}",
    }


def sentiment_for(items):
    enriched = []
    for item in items:
        enriched.append({
            **item,
            "relevance": "DIRECT",
            "sentiment": "BULLISH",
            "impact": "MEDIUM",
            "summary": item["excerpt"],
            "reason": "Direct ticker catalyst.",
        })
    return {
        "score": 0.5,
        "sentiment_pct": 75.0,
        "sentiment_direction": "BULLISH",
        "confidence": 0.7,
        "summary": "Recent directly relevant coverage is constructive.",
        "key_factor": "Constructive coverage",
        "analysis_model": "test-model",
        "articles": enriched,
    }


class ScreenerNewsWatchTests(unittest.TestCase):
    def setUp(self):
        ai_indicator._NEWS_METADATA_CACHE.clear()
        self.items = [
            article("AAPL newest catalyst", 23),
            article("AAPL prior story one", 22),
            article("AAPL prior story two", 21),
            article("AAPL prior story three", 20),
        ]
        self.provider_status = {"yahoo": "ok", "google": "not_checked", "errors": []}

    def test_metadata_poll_is_shared_for_the_same_ticker(self):
        with patch.object(ai_indicator, "_fetch_news_article_metadata", return_value=(self.items, self.provider_status)) as fetcher:
            first, _ = ai_indicator._fetch_news_article_metadata_cached("AAPL", 8)
            second, _ = ai_indicator._fetch_news_article_metadata_cached("AAPL", 8)
        self.assertEqual(first, second)
        fetcher.assert_called_once_with("AAPL", 8)

    def _initial_watch(self):
        with patch.object(ai_indicator, "_fetch_news_article_metadata_cached", return_value=(self.items, self.provider_status)), \
             patch.object(ai_indicator, "_add_news_article_previews", side_effect=lambda value: value), \
             patch.object(ai_indicator, "get_ai_sentiment", return_value=sentiment_for(self.items)):
            return ai_indicator.build_screener_news_watch("AAPL")

    def test_initial_sync_summarizes_four_without_marking_existing_articles_new(self):
        result = self._initial_watch()
        self.assertEqual(result["status"], "current")
        self.assertEqual(result["new_count"], 0)
        self.assertEqual(result["article_count"], 4)
        self.assertIn("Summary for AAPL newest catalyst", result["latest_summary"])
        self.assertIn("AAPL prior story three", result["previous_summary"])

    def test_unchanged_feed_skips_ai_analysis(self):
        previous = self._initial_watch()
        with patch.object(ai_indicator, "_fetch_news_article_metadata_cached", return_value=(self.items, self.provider_status)), \
             patch.object(ai_indicator, "_add_news_article_previews") as previews, \
             patch.object(ai_indicator, "get_ai_sentiment") as sentiment:
            result = ai_indicator.build_screener_news_watch("AAPL", previous)
        previews.assert_not_called()
        sentiment.assert_not_called()
        self.assertEqual(result["new_count"], 0)
        self.assertEqual(result["feed_fingerprint"], previous["feed_fingerprint"])

    def test_changed_feed_marks_only_unseen_direct_article_new(self):
        previous = self._initial_watch()
        changed = [article("AAPL breaking catalyst", 24)] + self.items[:3]
        with patch.object(ai_indicator, "_fetch_news_article_metadata_cached", return_value=(changed, self.provider_status)), \
             patch.object(ai_indicator, "_add_news_article_previews", side_effect=lambda value: value), \
             patch.object(ai_indicator, "get_ai_sentiment", return_value=sentiment_for(changed)):
            result = ai_indicator.build_screener_news_watch("AAPL", previous)
        self.assertEqual(result["new_count"], 1)
        self.assertEqual(result["latest"]["title"], "AAPL breaking catalyst")

    def test_provider_failure_retains_last_valid_news_as_stale(self):
        previous = self._initial_watch()
        failed_status = {"yahoo": "error", "google": "error", "errors": ["Yahoo: Timeout", "Google: Timeout"]}
        with patch.object(ai_indicator, "_fetch_news_article_metadata_cached", return_value=([], failed_status)), \
             patch.object(ai_indicator, "get_ai_sentiment") as sentiment:
            result = ai_indicator.build_screener_news_watch("AAPL", previous)
        sentiment.assert_not_called()
        self.assertEqual(result["status"], "stale")
        self.assertEqual(result["latest"]["title"], previous["latest"]["title"])
        self.assertEqual(result["new_count"], 0)
        self.assertGreater(result["next_check_at"], 0)

    def test_news_toggle_does_not_change_technical_profile_revision(self):
        enabled = {
            "timeframe": "15Min",
            "indicators": ["RSI", "SMA"],
            "news_watch_enabled": True,
        }
        disabled = {**enabled, "news_watch_enabled": False}
        changed_timeframe = {**enabled, "timeframe": "30Min"}

        self.assertEqual(profile_revision(enabled), profile_revision(disabled))
        self.assertNotEqual(profile_revision(enabled), profile_revision(changed_timeframe))


if __name__ == "__main__":
    unittest.main()
