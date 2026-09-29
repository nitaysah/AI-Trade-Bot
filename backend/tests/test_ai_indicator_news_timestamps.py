from datetime import datetime, timedelta, timezone
from email.utils import format_datetime
import time
import unittest
from unittest.mock import patch

import ai_indicator


class AiIndicatorNewsTimestampTests(unittest.TestCase):
    def test_timestamp_window_rejects_old_missing_and_future_values(self):
        now = time.time()
        self.assertIsNone(
            ai_indicator._recent_news_timestamp(
                now - ai_indicator.NEWS_MAX_AGE_SECONDS - 1,
                now,
            )
        )
        self.assertIsNone(ai_indicator._recent_news_timestamp(None, now))
        self.assertIsNone(
            ai_indicator._recent_news_timestamp(
                now + ai_indicator.NEWS_MAX_FUTURE_SKEW_SECONDS + 1,
                now,
            )
        )
        self.assertIsNotNone(ai_indicator._recent_news_timestamp(now - 60, now))

    def test_google_feed_drops_old_and_undated_articles(self):
        now = datetime.now(timezone.utc)
        old_date = format_datetime(now - timedelta(days=NEWS_AGE_DAYS_FOR_TEST), usegmt=True)
        recent_date = format_datetime(now - timedelta(hours=2), usegmt=True)
        rss = f"""<rss><channel>
          <item>
            <title>TSLA Stock Jumps 3% Ahead Of Q2 Report - Stocktwits</title>
            <link>https://stocktwits.com/old</link>
            <source>Stocktwits</source>
            <pubDate>{old_date}</pubDate>
          </item>
          <item>
            <title>Tesla delivery update</title>
            <link>https://example.com/recent</link>
            <source>Example</source>
            <pubDate>{recent_date}</pubDate>
          </item>
          <item>
            <title>Tesla article with no timestamp</title>
            <link>https://example.com/missing</link>
            <source>Example</source>
          </item>
        </channel></rss>""".encode()

        with patch.object(ai_indicator, "_fetch_public_url", return_value=rss):
            articles = ai_indicator._fetch_google_news_articles("TSLA", 8)

        self.assertEqual([article["title"] for article in articles], ["Tesla delivery update"])
        self.assertGreater(articles[0]["published_ts"], 0)


NEWS_AGE_DAYS_FOR_TEST = ai_indicator.NEWS_MAX_AGE_DAYS + 20


if __name__ == "__main__":
    unittest.main()
