import time
import unittest
from datetime import date
from types import SimpleNamespace
from unittest.mock import patch

import corporate_actions_service as svc
from corporate_actions import SplitEvent

EVENT_A = SplitEvent("AAPL", date(2020, 8, 31), 4.0)
EVENT_B = SplitEvent("AAPL", date(2026, 6, 9), 2.0)


class SplitLookupTests(unittest.TestCase):
    def setUp(self):
        svc.clear_cache()

    def test_alpaca_is_primary_and_results_are_cached(self):
        with patch.object(svc, "_fetch_alpaca", return_value=[EVENT_A, EVENT_B]) as alpaca, \
                patch.object(svc, "_fetch_yahoo", return_value=[]) as yahoo:
            first = svc.get_split_events("aapl")
            second = svc.get_split_events("AAPL")
        self.assertEqual((first.status, first.source), ("ok", "alpaca"))
        self.assertEqual([e.ex_date for e in first.events], [EVENT_A.ex_date, EVENT_B.ex_date])
        self.assertEqual(second.source, "alpaca")
        self.assertEqual(alpaca.call_count, 1)
        yahoo.assert_not_called()

    def test_window_filter_applies_to_the_cached_history(self):
        with patch.object(svc, "_fetch_alpaca", return_value=[EVENT_A, EVENT_B]):
            window = svc.get_split_events("AAPL", start=date(2025, 1, 1), end=date(2026, 12, 31))
        self.assertEqual(window.events, [EVENT_B])

    def test_yahoo_is_the_fallback_when_alpaca_fails(self):
        with patch.object(svc, "_fetch_alpaca", side_effect=RuntimeError("keys missing")), \
                patch.object(svc, "_fetch_yahoo", return_value=[EVENT_B]):
            lookup = svc.get_split_events("AAPL")
        self.assertEqual((lookup.status, lookup.source), ("ok", "yahoo"))
        self.assertEqual(lookup.events, [EVENT_B])

    def test_both_sources_failing_is_unavailable_never_no_split(self):
        with patch.object(svc, "_fetch_alpaca", side_effect=RuntimeError("down")), \
                patch.object(svc, "_fetch_yahoo", side_effect=TimeoutError("slow")):
            lookup = svc.get_split_events("AAPL")
        self.assertFalse(lookup.available)
        self.assertEqual(lookup.status, "unavailable")
        self.assertIn("alpaca", lookup.detail)
        self.assertNotIn("down", lookup.detail)  # exception text is not leaked, only its type

    def test_failure_is_cached_briefly_then_retried(self):
        with patch.object(svc, "_fetch_alpaca", side_effect=RuntimeError("down")) as alpaca, \
                patch.object(svc, "_fetch_yahoo", side_effect=RuntimeError("down")):
            now = time.time()
            svc.get_split_events("AAPL", now=now)
            svc.get_split_events("AAPL", now=now + 10)
            self.assertEqual(alpaca.call_count, 1)
            svc.get_split_events("AAPL", now=now + svc.FAILURE_TTL_SECONDS + 5)
            self.assertEqual(alpaca.call_count, 2)

    def test_success_cache_expires_after_the_ttl(self):
        with patch.object(svc, "_fetch_alpaca", return_value=[EVENT_A]) as alpaca:
            now = time.time()
            svc.get_split_events("AAPL", now=now)
            svc.get_split_events("AAPL", now=now + svc.CACHE_TTL_SECONDS - 60)
            self.assertEqual(alpaca.call_count, 1)
            svc.get_split_events("AAPL", now=now + svc.CACHE_TTL_SECONDS + 60)
            self.assertEqual(alpaca.call_count, 2)

    def test_crypto_and_empty_symbols_do_not_hit_providers(self):
        with patch.object(svc, "_fetch_alpaca") as alpaca, patch.object(svc, "_fetch_yahoo") as yahoo:
            crypto = svc.get_split_events("BTCUSD")
            empty = svc.get_split_events("")
        self.assertEqual((crypto.status, crypto.events), ("ok", []))
        self.assertFalse(empty.available)
        alpaca.assert_not_called()
        yahoo.assert_not_called()

    def test_events_after_returns_only_unprocessed_events(self):
        with patch.object(svc, "_fetch_alpaca", return_value=[EVENT_A, EVENT_B]):
            self.assertEqual(svc.events_after("AAPL", None).events, [EVENT_A, EVENT_B])
            self.assertEqual(svc.events_after("AAPL", EVENT_A.key).events, [EVENT_B])
            self.assertEqual(svc.events_after("AAPL", EVENT_B.key).events, [])
            self.assertEqual(svc.latest_split_date("AAPL"), EVENT_B.ex_date)

    def test_timeout_helper_raises(self):
        with self.assertRaises(TimeoutError):
            svc._call_with_timeout(lambda: time.sleep(0.5), 0.05)


class AlpacaParsingTests(unittest.TestCase):
    def test_forward_and_reverse_ratios_are_new_over_old(self):
        forward = SimpleNamespace(symbol="AAPL", new_rate=4.0, old_rate=1.0, ex_date=date(2020, 8, 31))
        reverse = SimpleNamespace(symbol="XYZ", new_rate=1.0, old_rate=10.0, ex_date=date(2026, 3, 2))
        bad = SimpleNamespace(symbol="BAD", new_rate=0.0, old_rate=1.0, ex_date=date(2026, 3, 2))
        response = SimpleNamespace(data={"forward_splits": [forward, bad], "reverse_splits": [reverse]})

        class FakeClient:
            def __init__(self, *args, **kwargs):
                pass

            def get_corporate_actions(self, request):
                return response

        with patch("alpaca.data.historical.corporate_actions.CorporateActionsClient", FakeClient), \
                patch("broker_factory.get_alpaca_data_credentials", return_value=("key", "secret")):
            events = svc._fetch_alpaca("AAPL", date(2000, 1, 1), date(2027, 1, 1))
        ratios = sorted(e.ratio for e in events)
        self.assertEqual(len(events), 2)
        self.assertAlmostEqual(ratios[0], 0.1)
        self.assertAlmostEqual(ratios[1], 4.0)

    def test_missing_keys_raise_so_the_yahoo_fallback_runs(self):
        with patch("broker_factory.get_alpaca_data_credentials", return_value=(None, None)):
            with self.assertRaises(RuntimeError):
                svc._fetch_alpaca("AAPL", date(2000, 1, 1), date(2027, 1, 1))


if __name__ == "__main__":
    unittest.main()
