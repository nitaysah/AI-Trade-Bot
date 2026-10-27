"""Bots-page Earnings / Fundamentals endpoints: no Screener profile needed, cached per user, crypto skipped."""
import os
import sys
import asyncio
import time
import unittest
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import main  # noqa: E402
from fastapi import HTTPException  # noqa: E402


def make_engine(tickers=("AAPL", "BTCUSD"), options=()):
    eng = MagicMock()
    eng.config.TRADELIST = list(tickers)
    eng.config.TICKER_SETTINGS = {t: {"options_trading": t in options} for t in tickers}
    eng.bot_research_cache = None
    del eng.bot_research_cache  # a real engine has no attribute until first use
    return eng


def make_engine_with(settings_by_ticker):
    eng = make_engine(tuple(settings_by_ticker))
    eng.config.TICKER_SETTINGS = dict(settings_by_ticker)
    return eng


def _event_in(days):
    """An event date that really is ``days`` exchange days away, so the read-time countdown keeps the fixture's value."""
    from datetime import datetime, timedelta
    from zoneinfo import ZoneInfo
    return (datetime.now(ZoneInfo("America/New_York")).date() + timedelta(days=days)).isoformat()


def earnings(symbol, days=24, status="current", next_in=3600, risk="low"):
    return {"data_version": main.EARNINGS_DATA_VERSION, "initialized": True, "status": status, "ticker": symbol, "event_date": _event_in(days),
            "days_to_event": days, "risk_level": risk, "next_check_at": time.time() + next_in, "analysis_summary": "x"}


class EarningsEndpointTests(unittest.IsolatedAsyncioTestCase):
    async def test_works_without_any_screener_profile_and_caches(self):
        eng = make_engine()
        with patch.object(main, "build_earnings_watch", side_effect=lambda t, prev, opt, force=False: earnings(t)) as build, \
                patch.object(main, "get_screener_state", side_effect=AssertionError("must not touch Screener state"), create=True):
            first = await main.get_bot_earnings("aapl", False, eng)
            second = await main.get_bot_earnings("AAPL", False, eng)
        self.assertEqual((first["ticker"], first["days_to_event"]), ("AAPL", 24))
        self.assertEqual(second, first)
        self.assertEqual(build.call_count, 1)

    async def test_due_cache_refreshes_and_force_bypasses_the_service_cache(self):
        eng = make_engine()
        seen = []

        def build(t, prev, opt, force=False):
            seen.append((prev is not None, force))
            return earnings(t, next_in=-1, days=5 if force else 24)

        with patch.object(main, "build_earnings_watch", side_effect=build):
            await main.get_bot_earnings("AAPL", False, eng)
            await main.get_bot_earnings("AAPL", False, eng)  # next_check_at already passed: refreshed, not forced
            forced = await main.get_bot_earnings("AAPL", True, eng)
        self.assertEqual(seen, [(False, False), (True, False), (False, True)])
        self.assertEqual(forced["days_to_event"], 5)

    async def test_unknown_ticker_and_bad_symbol_are_rejected_crypto_is_disabled(self):
        eng = make_engine()
        with self.assertRaises(HTTPException) as unknown:
            await main.get_bot_earnings("MSFT", False, eng)
        self.assertEqual(unknown.exception.status_code, 404)
        with self.assertRaises(HTTPException) as bad:
            await main.get_bot_earnings("!!!", False, eng)
        self.assertEqual(bad.exception.status_code, 400)
        with patch.object(main, "build_earnings_watch", side_effect=AssertionError("crypto must not be fetched")):
            out = await main.get_bot_earnings("BTCUSD", False, eng)
        self.assertFalse(out["enabled"])

    async def test_options_flag_is_passed_through(self):
        eng = make_engine(("AAPL",), options=("AAPL",))
        with patch.object(main, "build_earnings_watch", side_effect=lambda t, prev, opt, force=False: earnings(t) | {"opt": opt}):
            self.assertTrue((await main.get_bot_earnings("AAPL", False, eng))["opt"])


def _fundamental_report(symbol, previous=None, options=False):
    return {"report_version": 2, "status": "current", "ticker": symbol, "assessment": "Mixed evidence", "score": 61,
            "next_check_at": time.time() + 3600, "metrics": {"huge": "payload that badges must not carry"}}


class BadgeEndpointTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        # The badges endpoint also asks for the (cached or background-built) fundamentals: never hit the network in tests.
        patcher = patch.object(main, "get_fundamental_report_detail", side_effect=_fundamental_report)
        patcher.start()
        self.addCleanup(patcher.stop)
        main._bot_fundamental_tasks.clear()

    async def test_badges_carry_compact_fundamentals_for_stock_bots_only(self):
        eng = make_engine(("AAPL", "BTCUSD"))
        with patch.object(main, "build_earnings_watch", side_effect=lambda t, prev, opt, force=False: earnings(t)):
            out = await main.get_bot_earnings_badges(eng)
        self.assertEqual(set(out["fundamentals"]), {"AAPL"})
        self.assertEqual(out["fundamentals"]["AAPL"], {"status": "current", "assessment": "Mixed evidence", "score": 61})

    async def test_fundamentals_for_a_badge_are_built_once_and_served_from_cache(self):
        eng = make_engine(("AAPL",))
        with patch.object(main, "build_earnings_watch", side_effect=lambda t, prev, opt, force=False: earnings(t)):
            await main.get_bot_earnings_badges(eng)
            await main.get_bot_earnings_badges(eng)
        self.assertEqual(main.get_fundamental_report_detail.call_count, 1)

    async def test_a_slow_fundamentals_build_shows_checking_instead_of_blocking(self):
        eng = make_engine(("AAPL",))

        def slow(symbol, previous=None, options=False):
            time.sleep(0.4)
            return _fundamental_report(symbol)

        with patch.object(main, "get_fundamental_report_detail", side_effect=slow), \
                patch.object(main.asyncio, "wait_for", side_effect=asyncio.TimeoutError()):
            view = await main._bot_fundamentals_for_badge(eng, "AAPL")
        self.assertEqual(view, {"status": "waiting"})
        await asyncio.sleep(0.6)  # the background build still lands in the cache
        self.assertEqual(main._fundamental_badge_view(main._bot_research_cache(eng, "fundamentals")["AAPL"])["assessment"], "Mixed evidence")

    async def test_compact_badges_for_stock_bots_only(self):
        eng = make_engine(("AAPL", "MSFT", "BTCUSD"))
        eng.config.TICKER_SETTINGS["MSFT"] = {}
        with patch.object(main, "build_earnings_watch", side_effect=lambda t, prev, opt, force=False: earnings(t, days=3, risk="elevated")):
            out = await main.get_bot_earnings_badges(eng)
        self.assertEqual(set(out["badges"]), {"AAPL", "MSFT"})
        self.assertEqual(out["badges"]["AAPL"], {"status": "current", "event_date": _event_in(3), "last_event_date": None,
                                                 "days_to_event": 3, "risk_level": "elevated", "initialized": True,
                                                 "timing": None})

    async def test_a_failing_symbol_keeps_its_last_value_and_does_not_break_the_rest(self):
        eng = make_engine(("AAPL", "MSFT"))
        eng.config.TICKER_SETTINGS["MSFT"] = {}
        calls = {"n": 0}

        def build(t, prev, opt, force=False):
            calls["n"] += 1
            if t == "AAPL" and calls["n"] > 2:
                raise RuntimeError("provider down")
            return earnings(t, next_in=-1)

        with patch.object(main, "build_earnings_watch", side_effect=build):
            await main.get_bot_earnings_badges(eng)
            out = await main.get_bot_earnings_badges(eng)
        self.assertEqual(out["badges"]["AAPL"]["days_to_event"], 24)  # last value kept
        self.assertEqual(out["badges"]["MSFT"]["status"], "current")


class PerBotSwitchTests(unittest.IsolatedAsyncioTestCase):
    """Show Earnings Date / Fundamental Research are per-bot settings (default on, like the Screener profile)."""

    async def test_disabled_switches_return_disabled_and_never_fetch(self):
        eng = make_engine_with({"AAPL": {"earnings_watch_enabled": False, "fundamental_watch_enabled": False}})
        with patch.object(main, "build_earnings_watch", side_effect=AssertionError("disabled")), \
                patch.object(main, "get_fundamental_report_detail", side_effect=AssertionError("disabled")):
            self.assertEqual((await main.get_bot_earnings("AAPL", False, eng))["status"], "disabled")
            self.assertEqual((await main.get_bot_fundamentals("AAPL", True, eng))["status"], "disabled")

    async def test_the_two_switches_are_independent_and_missing_means_on(self):
        eng = make_engine_with({"AAPL": {"earnings_watch_enabled": False}, "MSFT": {}})
        with patch.object(main, "build_earnings_watch", side_effect=lambda t, prev, opt, force=False: earnings(t)), \
                patch.object(main, "get_fundamental_report_detail",
                             side_effect=lambda t, prev, opt: {"report_version": 2, "status": "current", "ticker": t, "next_check_at": time.time() + 60}):
            self.assertEqual((await main.get_bot_fundamentals("AAPL", False, eng))["status"], "current")
            self.assertEqual((await main.get_bot_earnings("MSFT", False, eng))["status"], "current")

    async def test_badge_refresh_skips_bots_with_earnings_off(self):
        eng = make_engine_with({"AAPL": {"earnings_watch_enabled": False}, "MSFT": {}})
        with patch.object(main, "build_earnings_watch", side_effect=lambda t, prev, opt, force=False: earnings(t)):
            out = await main.get_bot_earnings_badges(eng)
        self.assertEqual(set(out["badges"]), {"MSFT"})

    def test_create_bot_keeps_both_switches(self):
        import inspect
        source = inspect.getsource(main.create_bot)
        self.assertIn('"earnings_watch_enabled"', source)
        self.assertIn('"fundamental_watch_enabled"', source)


class FundamentalsEndpointTests(unittest.IsolatedAsyncioTestCase):
    async def test_cached_until_due_and_crypto_not_applicable(self):
        eng = make_engine()
        report = lambda t, prev, opt: {"report_version": 2, "status": "current", "ticker": t, "next_check_at": time.time() + 3600}
        with patch.object(main, "get_fundamental_report_detail", side_effect=report) as detail:
            await main.get_bot_fundamentals("AAPL", False, eng)
            again = await main.get_bot_fundamentals("AAPL", False, eng)
            forced = await main.get_bot_fundamentals("AAPL", True, eng)
        self.assertEqual((again["status"], detail.call_count, forced["ticker"]), ("current", 2, "AAPL"))
        with patch.object(main, "get_fundamental_report_detail", side_effect=AssertionError("crypto skipped")):
            self.assertEqual((await main.get_bot_fundamentals("BTCUSD", False, eng))["status"], "not_applicable")


if __name__ == "__main__":
    unittest.main()
