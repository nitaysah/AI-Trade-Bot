import unittest
from datetime import date
from unittest.mock import patch

import earnings_service


class _CalendarTicker:
    def __init__(self, calendar):
        self.calendar = calendar

    def get_calendar(self):
        return self.calendar

    def get_earnings_estimate(self):
        return None

    def get_revenue_estimate(self):
        return None

    def get_earnings_history(self):
        return None

    def get_eps_revisions(self):
        return None


class _Row:
    def __init__(self, values):
        self.values = values

    def to_dict(self):
        return self.values


class _HistoryTable:
    def iterrows(self):
        return iter([(date(2026, 6, 30), _Row({"surprisePercent": 0.0674}))])

    def tail(self, _count):
        return self


class EarningsServiceTests(unittest.TestCase):
    def setUp(self):
        earnings_service.clear_earnings_cache()

    def test_selects_earliest_future_calendar_date(self):
        fake = _CalendarTicker({"Earnings Date": [date(2020, 1, 1), date(2099, 10, 29), date(2099, 11, 1)]})
        with patch.object(earnings_service.yf, "Ticker", return_value=fake):
            payload = earnings_service.build_earnings_watch("AAPL", force=True)
        self.assertEqual(payload["event_date"], "2099-10-29")
        self.assertEqual(payload["event_status"], "estimated")
        self.assertEqual(payload["timing"], "UNKNOWN")

    def test_missing_event_is_unavailable_without_inventing_date(self):
        fake = _CalendarTicker({})
        with patch.object(earnings_service.yf, "Ticker", return_value=fake):
            payload = earnings_service.build_earnings_watch("AAPL", force=True)
        self.assertEqual(payload["status"], "unavailable")
        self.assertEqual(payload["event_date"], "")
        self.assertEqual(payload["event_status"], "not_published")

    def test_crypto_is_not_applicable(self):
        payload = earnings_service.build_earnings_watch("ETHUSD", force=True)
        self.assertEqual(payload["status"], "not_applicable")

    def test_etf_is_not_applicable_without_calling_earnings_endpoint(self):
        with patch.object(earnings_service.yf, "Ticker", side_effect=AssertionError("ETF must not call quoteSummary")):
            payload = earnings_service.build_earnings_watch("QQQ", force=True)
        self.assertEqual(payload["status"], "not_applicable")

    def test_provider_failure_preserves_previous_payload(self):
        previous = {"initialized": True, "status": "current", "event_date": "2099-10-29", "ticker": "AAPL"}
        with patch.object(earnings_service, "_get_raw", side_effect=RuntimeError("provider down")):
            payload = earnings_service.build_earnings_watch("AAPL", previous=previous, force=True)
        self.assertEqual(payload["status"], "stale")
        self.assertEqual(payload["event_date"], previous["event_date"])
        self.assertIn("provider down", payload["error"])

    def test_refresh_intervals_are_adaptive(self):
        self.assertEqual(earnings_service.earnings_refresh_seconds(None), 21600)
        self.assertEqual(earnings_service.earnings_refresh_seconds(20), 43200)
        self.assertEqual(earnings_service.earnings_refresh_seconds(7), 14400)
        self.assertEqual(earnings_service.earnings_refresh_seconds(0), 3600)

    def test_fractional_surprise_is_normalized_to_percent(self):
        history = earnings_service._history_records(_HistoryTable())
        self.assertEqual(history[0]["surprise_pct"], 6.74)



class EarningsCountdownTests(unittest.TestCase):
    """The countdown must always match the date, however old the cached payload is."""

    def setUp(self):
        earnings_service.clear_earnings_cache()

    def _payload(self, event):
        fake = _CalendarTicker({"Earnings Date": [event]})
        with patch.object(earnings_service.yf, "Ticker", return_value=fake):
            return earnings_service.build_earnings_watch("AAPL", force=True)

    def test_a_cached_payload_is_recounted_from_the_date_when_read(self):
        from datetime import datetime, timezone
        payload = self._payload(date(2099, 10, 28))
        early = earnings_service.with_current_countdown(payload, now=datetime(2099, 10, 13, 15, tzinfo=timezone.utc))
        self.assertEqual((early["days_to_event"], early["risk_level"]), (15, "LOW"))
        later = earnings_service.with_current_countdown(early, now=datetime(2099, 10, 26, 15, tzinfo=timezone.utc))
        self.assertEqual(later["days_to_event"], 2)                      # recounted, not the stale 15
        self.assertEqual(later["risk_level"], "CRITICAL")
        self.assertIn("2 day(s) away", later["risk_text"])
        self.assertIn("2 day(s) away", later["analysis_summary"])
        self.assertNotIn("15 days away", later["analysis_summary"])
        self.assertEqual(early["days_to_event"], 15)                     # the cached copy itself is untouched

    def test_the_count_uses_the_new_york_date(self):
        from datetime import datetime, timezone
        payload = self._payload(date(2099, 10, 28))
        # 11:30 PM in Chicago (04:30 UTC) is already the next day in New York
        late = earnings_service.with_current_countdown(payload, now=datetime(2099, 10, 27, 4, 30, tzinfo=timezone.utc))
        self.assertEqual(late["days_to_event"], 1)

    def test_after_a_report_the_last_date_is_kept_when_no_next_date_is_published(self):
        fake = _CalendarTicker({"Earnings Date": [date(2020, 10, 21)]})
        with patch.object(earnings_service.yf, "Ticker", return_value=fake):
            payload = earnings_service.build_earnings_watch("TSLA", force=True)
        self.assertEqual(payload["status"], "unavailable")
        self.assertEqual(payload["event_date"], "")
        self.assertEqual(payload["last_event_date"], "2020-10-21")
        self.assertIsNone(earnings_service.with_current_countdown(payload)["days_to_event"])

    def test_an_uninitialized_payload_is_returned_unchanged(self):
        waiting = {"status": "waiting", "ticker": "AAPL"}
        self.assertEqual(earnings_service.with_current_countdown(waiting), waiting)


if __name__ == "__main__":
    unittest.main()
