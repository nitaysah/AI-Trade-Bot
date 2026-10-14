"""Chart header day change: measured from the previous regular-session close, independent of the chart timeframe."""
import asyncio
import unittest
from datetime import date, datetime, timezone
from types import SimpleNamespace
from unittest.mock import patch

import pytz

import main
from cache_manager import stable_cache_key

ET = pytz.timezone("America/New_York")


def et(y, m, d, hh=12, mm=0):
    return ET.localize(datetime(y, m, d, hh, mm)).astimezone(timezone.utc)


ROWS = [(date(2026, 10, 7), 100.0), (date(2026, 10, 8), 102.0), (date(2026, 10, 9), 104.0)]  # Wed, Thu, Fri


class ReferenceSessionTests(unittest.TestCase):
    def test_a_trading_day_from_pre_market_is_today(self):
        self.assertEqual(main._quote_reference_session(et(2026, 10, 12, 4, 0)), date(2026, 10, 12))
        self.assertEqual(main._quote_reference_session(et(2026, 10, 12, 11, 0)), date(2026, 10, 12))
        self.assertEqual(main._quote_reference_session(et(2026, 10, 12, 21, 0)), date(2026, 10, 12))  # after hours

    def test_before_pre_market_it_is_still_the_previous_trading_day(self):
        self.assertEqual(main._quote_reference_session(et(2026, 10, 12, 2, 0)), date(2026, 10, 9))  # Monday 2 AM -> Friday

    def test_weekends_and_holidays_roll_back_to_the_last_trading_day(self):
        self.assertEqual(main._quote_reference_session(et(2026, 10, 10, 12)), date(2026, 10, 9))   # Saturday
        self.assertEqual(main._quote_reference_session(et(2026, 10, 11, 12)), date(2026, 10, 9))   # Sunday
        self.assertEqual(main._quote_reference_session(et(2026, 11, 26, 12)), date(2026, 11, 25))  # Thanksgiving

    def test_crypto_uses_the_utc_day(self):
        self.assertEqual(main._quote_reference_session(datetime(2026, 10, 10, 23, 30, tzinfo=timezone.utc), crypto=True), date(2026, 10, 10))


class PreviousCloseTests(unittest.TestCase):
    def test_during_the_session_it_is_the_bar_before_todays(self):
        rows = ROWS + [(date(2026, 10, 12), 105.5)]  # today's partial daily bar
        self.assertEqual(main._previous_close_from_daily(rows, date(2026, 10, 12)), 104.0)

    def test_pre_market_before_todays_bar_exists_it_is_the_last_bar(self):
        self.assertEqual(main._previous_close_from_daily(ROWS, date(2026, 10, 12)), 104.0)

    def test_on_a_weekend_the_header_reports_fridays_change(self):
        # session = Friday; Friday's bar exists, so the baseline is Thursday's close
        self.assertEqual(main._previous_close_from_daily(ROWS, date(2026, 10, 9)), 102.0)

    def test_bad_rows_are_skipped_and_too_little_history_is_none(self):
        self.assertEqual(main._previous_close_from_daily([(date(2026, 10, 8), None), (date(2026, 10, 9), "x"), (date(2026, 10, 12), 5.0)], date(2026, 10, 13)), 5.0)
        self.assertIsNone(main._previous_close_from_daily([(date(2026, 10, 12), 5.0)], date(2026, 10, 12)))
        self.assertIsNone(main._previous_close_from_daily([], date(2026, 10, 12)))

    def test_the_result_does_not_depend_on_anything_but_the_daily_rows(self):
        a = main._previous_close_from_daily(ROWS + [(date(2026, 10, 12), 1.0)], date(2026, 10, 12))
        b = main._previous_close_from_daily(ROWS + [(date(2026, 10, 12), 999.0)], date(2026, 10, 12))
        self.assertEqual(a, b)


class ChangeFieldsTests(unittest.TestCase):
    def test_change_and_percent_from_the_previous_close(self):
        out = main._quote_change_fields(250.0, 240.0)
        self.assertEqual(out["previous_close"], 240.0)
        self.assertAlmostEqual(out["change"], 10.0)
        self.assertAlmostEqual(out["change_pct"], 10.0 / 240.0 * 100.0)

    def test_a_drop_is_negative(self):
        self.assertLess(main._quote_change_fields(230.0, 240.0)["change_pct"], 0)

    def test_unusable_inputs_give_no_change_instead_of_a_wrong_one(self):
        for price, prev in ((250.0, None), (250.0, 0), (250.0, "x"), (None, 240.0), (0, 240.0), (float("nan"), 240.0)):
            out = main._quote_change_fields(price, prev)
            self.assertEqual(out, {"previous_close": None, "change": None, "change_pct": None}, (price, prev))


class QuoteEndpointTests(unittest.IsolatedAsyncioTestCase):
    def _prime(self, eng, price):
        main._quote_cache.set(stable_cache_key(eng.uid, "TSLA", "quote", 1), price)

    async def test_the_quote_carries_the_change_from_the_previous_close(self):
        eng = SimpleNamespace(uid="quote-change-user")
        self._prime(eng, 250.0)
        with patch.object(main, "_get_previous_close", return_value=240.0):
            out = await main.get_live_quote("TSLA", eng)
        self.assertEqual(out["price"], 250.0)
        self.assertEqual(out["previous_close"], 240.0)
        self.assertAlmostEqual(out["change_pct"], 10.0 / 240.0 * 100.0)

    async def test_a_missing_previous_close_never_blocks_or_breaks_the_price(self):
        eng = SimpleNamespace(uid="quote-change-user2")
        self._prime(eng, 250.0)
        with patch.object(main, "_get_previous_close", side_effect=RuntimeError("provider down")):
            out = await main.get_live_quote("TSLA", eng)
        self.assertEqual(out["price"], 250.0)
        self.assertIsNone(out["change_pct"])
        self.assertIsNone(out["previous_close"])


if __name__ == "__main__":
    unittest.main()
