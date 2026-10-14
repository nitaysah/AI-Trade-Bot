"""Screener alert source: Indicators / Strategy / Event-Driven are exclusive, per ticker."""
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from fastapi import HTTPException

import main
import strategy_presets as sp
from screener_service import ScreenerUserState, profile_revision
from screener_strategy import (
    alert_source_policy,
    derive_alert_source,
    migrate_legacy_alert_source,
    normalize_alert_source,
)
from user_config import UserConfig, set_user_config, _active_user_config


class AlertSourceHelperTests(unittest.TestCase):
    def test_normalize_accepts_aliases_and_rejects_unknown(self):
        self.assertEqual(normalize_alert_source("Event-Driven"), "event")
        self.assertEqual(normalize_alert_source("technical"), "indicators")
        self.assertEqual(normalize_alert_source("paused"), "none")
        self.assertIsNone(normalize_alert_source("combined"))
        self.assertIsNone(normalize_alert_source(None))

    def test_policy_mapping(self):
        self.assertEqual(alert_source_policy("indicators"), "technical")
        self.assertEqual(alert_source_policy("strategy"), "technical")
        self.assertEqual(alert_source_policy("event"), "event")

    def test_derive_prefers_explicit_then_preset_then_event(self):
        self.assertEqual(derive_alert_source({"alert_source": "none", "strategy_mode": "event"}), "none")
        self.assertEqual(derive_alert_source({"strategy_preset_enabled": True, "strategy_preset": "golden_cross"}), "strategy")
        self.assertEqual(derive_alert_source({"strategy_mode": "event"}), "event")
        self.assertEqual(derive_alert_source({"strategy_mode": "discovery"}), "indicators")
        self.assertEqual(derive_alert_source({}), "indicators")


class LegacyMigrationTests(unittest.TestCase):
    def test_combined_and_discovery_convert_once_and_keep_the_old_value(self):
        for legacy in ("combined", "discovery"):
            settings = {"strategy_mode": legacy}
            self.assertTrue(migrate_legacy_alert_source(settings))
            self.assertEqual(settings["alert_source"], "indicators")
            self.assertEqual(settings["strategy_mode"], "technical")
            self.assertEqual(settings["legacy_strategy_mode"], legacy)
            self.assertTrue(settings["migration_notice"])
            self.assertFalse(migrate_legacy_alert_source(settings))  # idempotent

    def test_technical_and_event_profiles_only_gain_the_derived_field(self):
        for mode, source in (("technical", "indicators"), ("event", "event")):
            settings = {"strategy_mode": mode}
            self.assertFalse(migrate_legacy_alert_source(settings))
            self.assertEqual((settings["alert_source"], settings["strategy_mode"]), (source, mode))
            self.assertNotIn("migration_notice", settings)

    def test_preset_saved_with_event_policy_becomes_strategy_with_notice(self):
        settings = {"strategy_mode": "event", "strategy_preset_enabled": True, "strategy_preset": "golden_cross"}
        self.assertTrue(migrate_legacy_alert_source(settings))
        self.assertEqual((settings["alert_source"], settings["strategy_mode"]), ("strategy", "technical"))

    def test_migration_does_not_reset_unaffected_baselines(self):
        before = {"strategy_mode": "technical", "indicators": ["RSI"], "timeframe": "30Min"}
        after = dict(before)
        migrate_legacy_alert_source(after)
        self.assertEqual(profile_revision(before), profile_revision(after))

    def test_converted_combined_profile_gets_a_new_revision(self):
        before = {"strategy_mode": "combined", "indicators": ["RSI"]}
        after = dict(before)
        migrate_legacy_alert_source(after)
        self.assertNotEqual(profile_revision(before), profile_revision(after))

    def test_pausing_alerts_changes_the_revision_so_resume_starts_fresh(self):
        live = {"strategy_mode": "technical", "alert_source": "indicators", "indicators": ["RSI"]}
        paused = {**live, "alert_source": "none"}
        self.assertNotEqual(profile_revision(live), profile_revision(paused))

    def test_load_applies_the_migration(self):
        state = ScreenerUserState("alert-source-load", None)
        state._apply({"profiles": {"AAPL": {"strategy_mode": "combined", "indicators": ["RSI"]}}, "order": ["AAPL"]})
        profile = state.profiles["AAPL"]
        self.assertEqual((profile["alert_source"], profile["strategy_mode"]), ("indicators", "technical"))
        self.assertEqual(profile["legacy_strategy_mode"], "combined")


class AlertSourceSaveTests(unittest.TestCase):
    def profile(self, data, prior=None):
        config = UserConfig("alert-source-save")
        token = set_user_config(config)
        self.addCleanup(_active_user_config.reset, token)
        profiles = {"SPY": prior} if prior else {}
        state = SimpleNamespace(
            config=SimpleNamespace(TICKER_SETTINGS=dict(profiles), MIN_BULLISH_SIGNALS=1, MIN_BEARISH_SIGNALS=1),
            profiles=profiles,
        )
        return main._screener_profile_from_request({"symbol": "SPY", **data}, state, "SPY")

    def test_each_source_forces_the_others_off(self):
        indicators = self.profile({"alert_source": "indicators", "indicators": ["RSI"],
                                   "strategy_preset_enabled": True, "strategy_preset": "golden_cross"})
        self.assertEqual((indicators["alert_source"], indicators["strategy_mode"]), ("indicators", "technical"))
        self.assertFalse(indicators["strategy_preset_enabled"])
        self.assertNotEqual(indicators.get("buy_mode"), sp.PRESET_MODE)

        event = self.profile({"alert_source": "event", "indicators": ["RSI"],
                              "strategy_preset_enabled": True, "strategy_preset": "golden_cross"})
        self.assertEqual((event["alert_source"], event["strategy_mode"]), ("event", "event"))
        self.assertFalse(event["strategy_preset_enabled"])

        strategy = self.profile({"alert_source": "strategy", "strategy_mode": "event",
                                 "strategy_preset_enabled": True, "strategy_preset": "golden_cross"})
        self.assertEqual((strategy["alert_source"], strategy["strategy_mode"]), ("strategy", "technical"))
        self.assertTrue(strategy["strategy_preset_enabled"])
        self.assertEqual(strategy["buy_mode"], sp.PRESET_MODE)

    def test_strategy_requires_a_preset(self):
        with self.assertRaises(HTTPException) as caught:
            self.profile({"alert_source": "strategy", "indicators": ["RSI"]})
        self.assertEqual(caught.exception.status_code, 400)

    def test_unknown_source_is_rejected(self):
        with self.assertRaises(HTTPException):
            self.profile({"alert_source": "combined"})

    def test_none_pauses_without_touching_the_preset(self):
        saved = self.profile({"alert_source": "strategy", "strategy_preset_enabled": True, "strategy_preset": "golden_cross"})
        paused = self.profile({"alert_source": "none"}, prior=saved)
        self.assertEqual(paused["alert_source"], "none")
        self.assertTrue(paused["strategy_preset_enabled"])
        resumed = self.profile({"alert_source": "strategy"}, prior=paused)
        self.assertEqual(resumed["alert_source"], "strategy")

    def test_switching_away_from_strategy_restores_the_manual_setup(self):
        manual = self.profile({"alert_source": "indicators", "indicators": ["RSI", "SMA"], "threshold": 2})
        strategy = self.profile({"alert_source": "strategy", "strategy_preset_enabled": True,
                                 "strategy_preset": "golden_cross"}, prior=manual)
        self.assertEqual(strategy["indicators"], [])
        back = self.profile({"alert_source": "indicators"}, prior=strategy)
        self.assertEqual(back["indicators"], ["RSI", "SMA"])

    def test_old_client_without_alert_source_is_mapped(self):
        self.assertEqual(self.profile({"strategy_mode": "event"})["alert_source"], "event")
        self.assertEqual(self.profile({"strategy_mode": "discovery"})["alert_source"], "indicators")
        self.assertEqual(self.profile({"strategy_mode": "event"})["strategy_mode"], "event")

    def test_old_client_keeps_a_paused_profile_paused(self):
        paused = self.profile({"alert_source": "none"})
        again = self.profile({"indicators": ["RSI"]}, prior=paused)
        self.assertEqual(again["alert_source"], "none")

    def test_saving_an_explicit_source_clears_the_migration_notice(self):
        prior = {"alert_source": "indicators", "strategy_mode": "technical", "migration_notice": True,
                 "legacy_strategy_mode": "combined", "indicators": ["RSI"]}
        saved = self.profile({"alert_source": "indicators"}, prior=prior)
        self.assertNotIn("migration_notice", saved)
        self.assertEqual(saved.get("legacy_strategy_mode"), "combined")


class PausedScanTests(unittest.IsolatedAsyncioTestCase):
    async def test_paused_ticker_is_never_evaluated_or_notified(self):
        state = ScreenerUserState("alert-source-paused-scan", None)
        state._apply({
            "profiles": {"AAPL": {"enabled": True, "timeframe": "15Min", "alert_source": "none",
                                  "strategy_mode": "technical", "indicators": ["RSI"]}},
            "order": ["AAPL"],
        })
        with patch.object(main, "evaluate_screener_profile", side_effect=AssertionError("evaluated while paused")) as evaluate:
            await main._scan_screener_state(state, ["AAPL"], force_scan=True)
        evaluate.assert_not_called()
        self.assertEqual(list(state.notifications), [])


class NewsRefreshFollowsScanIntervalTests(unittest.TestCase):
    def test_auto_scan_interval_gives_the_timeframe_cadence(self):
        self.assertEqual(main._screener_news_refresh_interval_seconds({"timeframe": "30Min", "scan_interval_auto": True}), 1800)
        self.assertEqual(main._screener_news_refresh_interval_seconds({"timeframe": "1Hour", "scan_interval_auto": True}), 3600)

    def test_custom_scan_interval_is_used_for_news(self):
        self.assertEqual(main._screener_news_refresh_interval_seconds(
            {"timeframe": "30Min", "scan_interval_auto": False, "scan_interval_seconds": 900}), 900)

    def test_news_cadence_keeps_its_minute_floor_for_fast_scans(self):
        self.assertEqual(main._screener_news_refresh_interval_seconds(
            {"timeframe": "5Min", "scan_interval_auto": False, "scan_interval_seconds": 10}), 60)

    def test_legacy_news_fields_are_ignored(self):
        self.assertEqual(main._screener_news_refresh_interval_seconds(
            {"timeframe": "30Min", "scan_interval_auto": True,
             "news_refresh_interval_auto": False, "news_refresh_interval_seconds": 86400}), 1800)


class SaveThenScanTests(unittest.IsolatedAsyncioTestCase):
    """The page waits for the scan that a save starts; it must always get a terminal answer."""

    CLOSED = {
        "is_active_window": False, "is_open": False, "market_session_label": "Regular",
        "seconds_to_open": 3600, "seconds_to_pre_open": 3480, "next_open_str": "Monday at 9:30 AM ET",
    }

    def _state(self, uid):
        state = ScreenerUserState(uid, None)
        state.save = AsyncMock()
        return state

    async def _save(self, state, settings):
        started = []

        def capture(coro, *args, **kwargs):
            started.append(getattr(getattr(coro, "cr_code", None), "co_name", ""))
            coro.close()
            return None

        with patch.object(main.asyncio, "create_task", side_effect=capture):
            response = await main.save_screener_profile({"symbol": "AAPL", "settings": settings}, state)
        return response, started

    async def test_saving_with_alerts_paused_starts_no_scan(self):
        state = self._state("save-paused")
        response, started = await self._save(state, {
            "enabled": True, "timeframe": "30Min", "indicators": ["RSI"], "alert_source": "none",
        })
        self.assertEqual(response["settings"]["alert_source"], "none")
        self.assertNotIn("_scan_screener_state", started)
        self.assertNotIn("_scan_screener_after_save", started)

    async def test_saving_a_live_source_starts_one_forced_scan(self):
        state = self._state("save-live")
        _, started = await self._save(state, {
            "enabled": True, "timeframe": "30Min", "indicators": ["RSI"], "alert_source": "indicators",
        })
        self.assertEqual(started.count("_scan_screener_after_save"), 1)  # one task that runs the two scans in order

    async def test_a_profile_with_no_valid_scan_gets_one_evaluation_even_when_the_session_is_closed(self):
        state = self._state("save-closed")
        await self._save(state, {
            "enabled": True, "timeframe": "30Min", "market_session": "Regular",
            "indicators": ["RSI"], "alert_source": "indicators",
        })
        self.assertNotIn("AAPL", state.latest_scans)  # the save cleared the cached scan
        self.assertTrue(state.evaluation_state["AAPL"]["baseline_required"])
        with patch.object(main, "_screener_market_status", return_value=self.CLOSED), patch.object(
            main, "evaluate_screener_profile", return_value={"evaluation_status": "error"}
        ) as evaluate:
            await main._scan_screener_state(state, ["AAPL"], force_scan=True)
        # The first baseline is evaluated once so the card can show last-session evidence; a failure there is
        # reported as degraded data, never as an endless wait.
        evaluate.assert_called_once()
        self.assertEqual(state.latest_scans["AAPL"]["data_status"], "degraded")

    async def test_once_a_valid_scan_exists_the_closed_session_gate_applies_again(self):
        import time as _t
        state = self._state("closed-gate-holds")
        await self._save(state, {
            "enabled": True, "timeframe": "30Min", "market_session": "Regular",
            "indicators": ["RSI"], "alert_source": "indicators",
        })
        candle = int(_t.time()) - 1800
        state.latest_scans["AAPL"] = {
            "ticker": "AAPL", "evaluation_status": "valid", "data_status": "current", "direction": "NEUTRAL",
            "price": "$100.00", "price_raw": 100.0, "signals": {},
            "candle_meta": {"candle_key": f"30MIN:{candle}", "candle_open_ts": candle, "candle_close_ts": candle + 1800, "candle_status": "closed"},
            "scan_log": {"status": "WATCHING_BOTH"},
        }
        # even with the baseline flag still set, a valid scan on file means there is nothing to wait for
        state.evaluation_state["AAPL"]["baseline_required"] = True
        with patch.object(main, "_screener_market_status", return_value=self.CLOSED), patch.object(
            main, "evaluate_screener_profile"
        ) as evaluate:
            await main._scan_screener_state(state, ["AAPL"], force_scan=True)
        evaluate.assert_not_called()
        card = state.latest_scans["AAPL"]
        self.assertEqual(card["scan_log"]["status"], "MARKET_CLOSED")
        self.assertEqual(card["price"], "$100.00")  # the last session's analysis is kept in place

    async def test_baseline_done_and_no_scan_does_not_evaluate_when_closed(self):
        state = self._state("closed-no-baseline-flag")
        state.profiles = {"AAPL": {"enabled": True, "timeframe": "15Min", "market_session": "Regular",
                                   "indicators": [], "strategy_mode": "technical", "alert_source": "indicators"}}
        state.order = ["AAPL"]
        with patch.object(main, "_screener_market_status", return_value=self.CLOSED), patch.object(
            main, "evaluate_screener_profile"
        ) as evaluate:
            await main._scan_screener_state(state, ["AAPL"], force_scan=True)
        evaluate.assert_not_called()
        self.assertEqual(state.latest_scans["AAPL"]["scan_log"]["status"], "MARKET_CLOSED")

    async def test_event_source_card_carries_its_alert_source_when_closed(self):
        state = self._state("save-closed-event")
        await self._save(state, {
            "enabled": True, "timeframe": "30Min", "market_session": "Regular",
            "indicators": ["RSI"], "alert_source": "event",
        })
        with patch.object(main, "_screener_market_status", return_value=self.CLOSED), patch.object(
            main, "evaluate_screener_profile"
        ):
            await main._scan_screener_state(state, ["AAPL"], force_scan=True)
        self.assertIn("AAPL", state.latest_scans, msg=str({k: v for k, v in state.health.items() if "error" in k or "count" in k}) + str(state.evaluation_state.get("AAPL")))
        card = state.latest_scans["AAPL"]
        self.assertEqual(card["alert_source"] if "alert_source" in card else card["scan_log"]["alert_source"], "event")
        # The page stops waiting on this marker, and the guard must not call it an out-of-order candle.
        self.assertTrue(card["event_only_market_closed"])
        self.assertNotEqual(state.health.get("last_error"), "Out-of-order completed candle for AAPL; retrying market-data fetch")
        self.assertNotIn("older completed candle", str(card.get("stale_reason", "")))


class EventGatesReportedTests(unittest.TestCase):
    """The card explains what a buy/sell needs from the gates the assessment actually applied."""

    def _watch(self, direction, pct, conf):
        import time as _t
        return {
            "initialized": True, "status": "current",
            "latest": {"id": "a", "title": "Upgrade", "source": "Wire", "published_ts": _t.time() - 600, "impact": "HIGH", "relevance": "DIRECT"},
            "sentiment_direction": direction, "sentiment_pct": pct, "sentiment_confidence": conf, "article_count": 1,
        }

    def test_gates_are_reported_with_defaults(self):
        from screener_strategy import assess_event_catalyst
        gates = assess_event_catalyst(self._watch("BULLISH", 90, 0.9))["gates"]
        self.assertEqual((gates["min_bullish_pct"], gates["max_bearish_pct"], gates["min_confidence"], gates["max_age_days"]), (65.0, 35.0, 0.55, 10))

    def test_gates_reflect_the_users_settings(self):
        from screener_strategy import assess_event_catalyst
        gates = assess_event_catalyst(self._watch("BULLISH", 90, 0.9), max_age_days=3, min_confidence=0.7)["gates"]
        self.assertEqual((gates["min_confidence"], gates["max_age_days"]), (0.7, 3))

    def test_gates_present_even_when_the_feed_is_not_ready(self):
        from screener_strategy import assess_event_catalyst
        self.assertIn("gates", assess_event_catalyst({}))

    def test_the_reported_gates_decide_the_outcome(self):
        from screener_strategy import assess_event_catalyst
        # 64 is just below the buy gate, 36 just above the sell gate; confidence exactly at the gate qualifies.
        self.assertFalse(assess_event_catalyst(self._watch("BULLISH", 64, 0.9))["eligible"])
        self.assertTrue(assess_event_catalyst(self._watch("BULLISH", 65, 0.55))["eligible"])
        self.assertFalse(assess_event_catalyst(self._watch("BEARISH", 36, 0.9))["eligible"])
        self.assertTrue(assess_event_catalyst(self._watch("BEARISH", 35, 0.55))["eligible"])
        self.assertFalse(assess_event_catalyst(self._watch("BULLISH", 90, 0.54))["eligible"])


class TwoForcedScansAfterSaveTests(unittest.IsolatedAsyncioTestCase):
    async def test_runs_two_forced_scans_one_after_the_other(self):
        order = []
        running = {"now": 0, "max": 0}

        async def fake_scan(state, symbols, force_scan=False):
            running["now"] += 1
            running["max"] = max(running["max"], running["now"])
            order.append((tuple(symbols), force_scan))
            await __import__("asyncio").sleep(0)
            running["now"] -= 1

        with patch.object(main, "_scan_screener_state", side_effect=fake_scan), patch.object(main.asyncio, "sleep", new=AsyncMock()):
            await main._scan_screener_after_save(object(), "AAPL")
        self.assertEqual(order, [(("AAPL",), True), (("AAPL",), True)])
        self.assertEqual(running["max"], 1)  # never overlapping

    async def test_a_failed_first_scan_does_not_stop_the_second(self):
        calls = []

        async def flaky(state, symbols, force_scan=False):
            calls.append(1)
            if len(calls) == 1:
                raise RuntimeError("provider down")

        with patch.object(main, "_scan_screener_state", side_effect=flaky), patch.object(main.asyncio, "sleep", new=AsyncMock()):
            await main._scan_screener_after_save(object(), "AAPL")
        self.assertEqual(len(calls), 2)

    async def test_cancellation_is_not_swallowed(self):
        async def cancelled(state, symbols, force_scan=False):
            raise __import__("asyncio").CancelledError()

        with patch.object(main, "_scan_screener_state", side_effect=cancelled):
            with self.assertRaises(__import__("asyncio").CancelledError):
                await main._scan_screener_after_save(object(), "AAPL")


class ExitEvidenceTests(unittest.TestCase):
    """Exit-side evidence is produced in every watching state, not only once a virtual position exists."""

    SCAN = {"price_raw": 100.0, "atr": 2.0}

    def test_an_atr_exit_without_a_position_is_a_reference_at_the_current_price(self):
        ev = main._screener_exit_evidence({"sell_mode": "atr", "atr_stop_multiplier": 2.0, "take_profit_multiplier": 4.0}, self.SCAN, None)
        self.assertTrue(ev["reference"])
        self.assertTrue(ev["available"])
        self.assertAlmostEqual(ev["stop"], 96.0)     # 100 - 2 x 2.0
        self.assertAlmostEqual(ev["target"], 108.0)  # 100 + 4 x 2.0
        self.assertEqual((ev["mode"], ev["atr"]), ("atr", 2.0))
        self.assertEqual(ev["nearest_level"], "SL")

    def test_a_percentage_exit_uses_the_percentage_levels(self):
        ev = main._screener_exit_evidence({"sell_mode": "percentage"}, self.SCAN, None)
        self.assertTrue(ev["reference"] and ev["available"])
        self.assertGreater(ev["target"], ev["price"])
        self.assertLess(ev["stop"], ev["price"])

    def test_indicator_strategy_and_ai_exits_have_no_structural_evidence(self):
        for mode in ("indicator", "strategy_preset", "ai_autopilot", "trend_rider", ""):
            self.assertEqual(main._screener_exit_evidence({"sell_mode": mode}, self.SCAN, None), {}, mode)

    def test_a_live_position_reports_its_own_levels_not_a_reference(self):
        live = {"available": True, "triggered": False, "text": "ATR exit: price $101.00; SL $97.00", "stop": 97.0, "target": 110.0}
        ev = main._screener_exit_evidence({"sell_mode": "atr"}, self.SCAN, live)
        self.assertFalse(ev["reference"])
        self.assertEqual(ev["stop"], 97.0)

    def test_no_valid_price_is_reported_as_unavailable_not_invented(self):
        ev = main._screener_exit_evidence({"sell_mode": "atr"}, {"price_raw": 0, "atr": 2.0}, None)
        self.assertFalse(ev["available"])
        self.assertTrue(ev["reference"])
        self.assertNotIn("stop", ev)

    def test_the_reference_never_writes_into_the_profile_or_scan(self):
        settings = {"sell_mode": "atr", "enable_atr_trail": True}
        scan = dict(self.SCAN)
        main._screener_exit_evidence(settings, scan, None)
        self.assertEqual(settings, {"sell_mode": "atr", "enable_atr_trail": True})
        self.assertEqual(scan, self.SCAN)

    def test_atr_trail_setting_is_reported(self):
        on = main._screener_exit_evidence({"sell_mode": "atr", "enable_atr_trail": True}, self.SCAN, None)
        off = main._screener_exit_evidence({"sell_mode": "atr", "enable_atr_trail": False}, self.SCAN, None)
        self.assertTrue(on["atr_trail_enabled"])
        self.assertFalse(off["atr_trail_enabled"])


class IndicatorSellFollowsTheExitSetupTests(unittest.TestCase):
    """A Sell alert must come from what the exit is set up as, not from bearish indicators under a price-level exit."""

    def evaluate(self, action, sell_mode, buy_mode="indicator"):
        from screener_service import evaluate_screener_profile
        result = {"evaluation_status": "valid", "action": action, "price_raw": 100.0, "atr": 2.0,
                  "candle_meta": {"candle_key": "30MIN:1790000000"}, "reason": f"{action} Triggered: 3 signals"}
        settings = {"symbol": "AAPL", "timeframe": "30Min", "buy_mode": buy_mode, "sell_mode": sell_mode, "indicators": ["RSI"]}
        with patch("trader.evaluate_trade", return_value=result):
            return evaluate_screener_profile("exit-setup", settings)

    def test_a_price_level_exit_ignores_the_bearish_indicator_sell(self):
        for mode in ("atr", "sltp", "percentage", "custom_sltp", "pivot_points", "high_low", "swing_points"):
            out = self.evaluate("SELL", mode)
            self.assertEqual(out["direction"], "NEUTRAL", mode)
            self.assertEqual(out["action"], "HOLD", mode)
            self.assertTrue(out["indicator_sell_ignored"], mode)
            self.assertIn("Sell signal ignored", out["reason"])
            self.assertIn("SELL Triggered", out["reason"])  # the original reason is kept for the card

    def test_indicator_and_hybrid_exits_still_use_the_bearish_indicators(self):
        for mode in ("indicator", "hybrid", "hybrid_atr", "hybrid_pct", "hybrid_pivot", "hybrid_high_low", "hybrid_swing"):
            out = self.evaluate("SELL", mode)
            self.assertEqual(out["direction"], "BEARISH", mode)
            self.assertFalse(out.get("indicator_sell_ignored"), mode)

    def test_the_buy_side_is_never_affected_by_the_exit_mode(self):
        for mode in ("atr", "indicator", "percentage"):
            self.assertEqual(self.evaluate("BUY", mode)["direction"], "BULLISH", mode)

    def test_a_missing_sell_mode_defaults_to_the_indicator_exit(self):
        self.assertEqual(self.evaluate("SELL", "")["direction"], "BEARISH")

    def test_the_helper_matches_the_mode_names_case_insensitively(self):
        from screener_service import indicator_sell_is_ignored
        self.assertTrue(indicator_sell_is_ignored(" ATR "))
        self.assertFalse(indicator_sell_is_ignored("Hybrid_ATR"))


class ExitAlertLifecycleTests(unittest.IsolatedAsyncioTestCase):
    """Buy alert -> virtual position -> ATR stop reached -> Sell alert, through the real scan loop."""

    ACTIVE = {"is_active_window": True, "is_open": True, "market_session_label": "Regular",
              "seconds_to_open": 0, "seconds_to_pre_open": 0, "next_open_str": ""}

    def _result(self, direction, price, ts):
        return {
            "evaluation_status": "valid", "direction": direction,
            "action": {"BULLISH": "BUY", "BEARISH": "SELL"}.get(direction, "HOLD"),
            "price_raw": price, "price": f"${price:.2f}", "atr": 2.0, "reason": f"{direction} fixture", "signals": {},
            "candle_meta": {"candle_key": f"30MIN:{ts}", "candle_open_ts": ts, "candle_close_ts": ts + 1800, "candle_status": "closed"},
            "atr_levels": {}, "profile_revision": "x",
        }

    def _state(self, uid, sell_mode):
        state = ScreenerUserState(uid, None)
        state.save = AsyncMock()
        state.profiles = {"AAPL": {"enabled": True, "timeframe": "30Min", "market_session": "Regular", "source": "screener",
                                   "indicators": [], "strategy_mode": "technical", "alert_source": "indicators",
                                   "buy_mode": "indicator", "sell_mode": sell_mode, "atr_stop_multiplier": 2.0,
                                   "take_profit_multiplier": 4.0, "enable_atr_trail": False,
                                   "alert_cooldown_minutes": 0, "scan_interval_seconds": 60}}
        state.order = ["AAPL"]
        state.mark_profile_baseline("AAPL", "profile_activated")
        return state

    @staticmethod
    def _technical(scan, settings=None):
        # Only the technical leg is stubbed: it qualifies whatever direction the fixture candle carries, so the test
        # exercises the lifecycle (alert, virtual position, level watcher, exit alert) and not the indicator maths.
        direction = str((scan or {}).get("direction") or "NEUTRAL").upper()
        return {"eligible": direction in {"BULLISH", "BEARISH"}, "direction": direction, "status": "QUALIFIED", "reason": "fixture"}

    async def _scan(self, state, result):
        with patch.object(main, "_screener_market_status", return_value=self.ACTIVE), patch.object(
            main, "evaluate_screener_profile", return_value=result
        ), patch.object(main, "assess_swing_technical", side_effect=self._technical):
            await main._scan_screener_state(state, ["AAPL"], force_scan=True)

    async def test_atr_stop_after_a_buy_sends_the_sell_alert(self):
        import time as _t
        t2 = (int(_t.time()) // 1800) * 1800 - 1800 * 2
        t1 = t2 - 1800
        state = self._state("exit-life-atr", "atr")
        await self._scan(state, self._result("BULLISH", 100.0, t1))
        self.assertEqual([n["direction"] for n in state.notifications], ["BULLISH"])
        self.assertTrue(state.evaluation_state["AAPL"]["virtual_position"])
        # next completed candle: price falls through the 2 x ATR stop (96) while the indicator direction is neutral
        await self._scan(state, self._result("NEUTRAL", 94.0, t2))
        self.assertEqual([n["direction"] for n in state.notifications], ["BEARISH", "BULLISH"])  # newest first
        self.assertIn("ATR exit", str(state.notifications[0].get("reason")))
        self.assertFalse(state.evaluation_state["AAPL"].get("virtual_position"))

    async def test_no_sell_alert_while_price_stays_inside_the_atr_levels(self):
        import time as _t
        t2 = (int(_t.time()) // 1800) * 1800 - 1800 * 2
        state = self._state("exit-life-inside", "atr")
        await self._scan(state, self._result("BULLISH", 100.0, t2 - 1800))
        await self._scan(state, self._result("NEUTRAL", 99.0, t2))
        self.assertEqual([n["direction"] for n in state.notifications], ["BULLISH"])
        self.assertTrue(state.evaluation_state["AAPL"]["virtual_position"])

    async def test_bearish_indicators_alone_do_not_end_an_atr_setup_but_do_for_an_indicator_exit(self):
        from screener_service import evaluate_screener_profile
        sell = {"evaluation_status": "valid", "action": "SELL", "price_raw": 100.0, "atr": 2.0,
                "candle_meta": {"candle_key": "30MIN:1790000000"}, "reason": "SELL Triggered"}
        for mode, expected in (("atr", "NEUTRAL"), ("indicator", "BEARISH"), ("hybrid", "BEARISH")):
            with patch("trader.evaluate_trade", return_value=dict(sell)):
                out = evaluate_screener_profile("lifecycle", {"symbol": "AAPL", "timeframe": "30Min", "buy_mode": "indicator", "sell_mode": mode})
            self.assertEqual(out["direction"], expected, mode)


class EventCatalystAlertTests(unittest.TestCase):
    """A different qualifying headline in the same direction is a new alert (after the cooldown), not a repeat."""

    DAY_TS = 1_790_000_000

    def _state(self, cooldown=0, source="event"):
        s = ScreenerUserState("catalyst-" + str(cooldown) + source, None)
        s.profiles["AAPL"] = {"enabled": True, "timeframe": "30Min", "market_session": "Regular", "alert_source": source,
                              "strategy_mode": "event" if source == "event" else "technical", "indicators": [],
                              "alert_cooldown_minutes": cooldown}
        s.order = ["AAPL"]
        s.mark_profile_baseline("AAPL", "profile_activated")
        return s

    def _ev(self, direction, i, catalyst):
        ts = self.DAY_TS + i * 1800
        return ts, {"evaluation_status": "valid", "direction": direction, "action": "BUY" if direction == "BULLISH" else "SELL",
                    "price_raw": 100.0, "price": "$100.00", "atr": 2.0, "reason": "x", "signals": {}, "atr_levels": {},
                    "event_qualification": {"catalyst_id": catalyst, "eligible": True, "direction": direction},
                    "candle_meta": {"candle_key": f"30MIN:{ts}", "candle_open_ts": ts, "candle_close_ts": ts + 1800, "candle_status": "closed"}}

    def _run(self, s, steps):
        out = []
        for i, (direction, catalyst) in enumerate(steps):
            ts, e = self._ev(direction, i, catalyst)
            n = s.commit_evaluation("AAPL", e, now_ts=ts + 1800)
            out.append(n["direction"] if n else None)
        return out

    def test_a_new_bullish_catalyst_alerts_again_and_the_same_one_does_not(self):
        s = self._state(cooldown=0)
        self.assertEqual(self._run(s, [("BULLISH", "A"), ("BULLISH", "A"), ("BULLISH", "B"), ("BULLISH", "B"), ("BULLISH", "C")]),
                         ["BULLISH", None, "BULLISH", None, "BULLISH"])

    def test_the_cooldown_still_throttles_a_burst_of_headlines(self):
        s = self._state(cooldown=60)
        # candles are 30 minutes apart: B arrives inside the 60-minute cooldown, then keeps being the latest catalyst
        self.assertEqual(self._run(s, [("BULLISH", "A"), ("BULLISH", "B"), ("BULLISH", "B"), ("BULLISH", "B")]),
                         ["BULLISH", None, "BULLISH", None])

    def test_a_flip_to_bearish_still_alerts_immediately(self):
        s = self._state(cooldown=60)
        self.assertEqual(self._run(s, [("BULLISH", "A"), ("BEARISH", "B")]), ["BULLISH", "BEARISH"])

    def test_other_alert_sources_ignore_catalyst_ids(self):
        s = self._state(cooldown=0, source="indicators")
        self.assertEqual(self._run(s, [("BULLISH", "A"), ("BULLISH", "B")]), ["BULLISH", None])

    def test_an_older_profile_without_a_recorded_catalyst_does_not_re_alert_on_the_first_scan(self):
        s = self._state(cooldown=0)
        self._run(s, [("BULLISH", "A")])
        s.evaluation_state["AAPL"].pop("last_event_catalyst_id", None)   # as if alerted before catalysts were tracked
        ts, e = self._ev("BULLISH", 1, "A")
        self.assertIsNone(s.commit_evaluation("AAPL", e, now_ts=ts + 1800))
        self.assertEqual(s.evaluation_state["AAPL"]["last_event_catalyst_id"], "A")   # adopted silently
        ts, e = self._ev("BULLISH", 2, "B")
        self.assertIsNotNone(s.commit_evaluation("AAPL", e, now_ts=ts + 1800))       # a later headline is new

    def test_replaying_the_same_candle_never_duplicates(self):
        s = self._state(cooldown=0)
        ts, e = self._ev("BULLISH", 0, "A")
        self.assertIsNotNone(s.commit_evaluation("AAPL", e, now_ts=ts + 1800))
        self.assertIsNone(s.commit_evaluation("AAPL", e, now_ts=ts + 1900))
        self.assertEqual(len(s.notifications), 1)


class OpenPositionExitEvidenceTests(unittest.TestCase):
    """After a Buy alert opens a virtual position, the Sell evidence shows the position's own levels."""

    def _state(self, entry=100.0, atr=2.0, **extra):
        return {"virtual_position": True, "virtual_entry_price": entry, "virtual_entry_atr": atr,
                "virtual_exit_settings": {"sell_mode": "atr", "atr_stop_multiplier": 2.0, "take_profit_multiplier": 4.0, "enable_atr_trail": False},
                "virtual_peak_price": entry, **extra}

    def test_levels_are_anchored_to_the_entry_not_the_current_price(self):
        settings = {"sell_mode": "atr", "atr_stop_multiplier": 2.0, "take_profit_multiplier": 4.0, "enable_atr_trail": False}
        ev = main._screener_exit_evidence(settings, {"price_raw": 110.0, "atr": 2.5}, None, self._state())
        self.assertFalse(ev["reference"])
        self.assertAlmostEqual(ev["stop"], 96.0)       # 100 - 2 x 2.0 (the ENTRY price and ATR, not today's)
        self.assertAlmostEqual(ev["target"], 108.0)
        self.assertEqual(ev["entry_price"], 100.0)
        self.assertTrue(ev["triggered"])               # 110 is already past the 108 target

    def test_no_position_still_gives_the_reference(self):
        settings = {"sell_mode": "atr", "atr_stop_multiplier": 2.0}
        ev = main._screener_exit_evidence(settings, {"price_raw": 110.0, "atr": 2.0}, None, {"virtual_position": False})
        self.assertTrue(ev["reference"])
        self.assertNotIn("entry_price", ev)

    def test_the_position_state_is_never_modified(self):
        settings = {"sell_mode": "atr", "enable_atr_trail": True}
        state = self._state()
        state["virtual_exit_settings"]["enable_atr_trail"] = True
        before = {k: (dict(v) if isinstance(v, dict) else v) for k, v in state.items()}
        main._screener_exit_evidence(settings, {"price_raw": 105.0, "atr": 2.0}, None, state)
        self.assertEqual(state, before)

    def test_a_position_without_an_entry_price_falls_back_to_the_reference(self):
        ev = main._screener_exit_evidence({"sell_mode": "atr"}, {"price_raw": 100.0, "atr": 2.0}, None, {"virtual_position": True})
        self.assertTrue(ev["reference"])


if __name__ == "__main__":
    unittest.main()
