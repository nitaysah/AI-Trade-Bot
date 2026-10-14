import unittest
from datetime import date, datetime
from unittest.mock import patch
from zoneinfo import ZoneInfo

import nyse_calendar
import preset_runtime as rt
from corporate_actions import SplitEvent

ET = ZoneInfo("America/New_York")


def setUpModule():
    global _patch
    _patch = patch.object(nyse_calendar, "_calendar", return_value=None)  # deterministic rule-based calendar
    _patch.start()


def tearDownModule():
    _patch.stop()


def et(y, mo, d, h, mi=0):
    return datetime(y, mo, d, h, mi, tzinfo=ET)


def buy_result(key="golden_cross:sma_50_200:1Day:2026-10-02:BUY", atr=2.0):
    return {"action": "BUY", "reason": "entry", "strategy_preset": {"signal_key": key, "atr": atr, "stop_atr_mult": 3.0}}


class WindowTests(unittest.TestCase):
    def test_open_inside_the_regular_session_only(self):
        self.assertIsNone(rt.window_reason(et(2026, 10, 5, 9, 30)))
        self.assertIsNone(rt.window_reason(et(2026, 10, 5, 15, 59)))
        self.assertIn("open", rt.window_reason(et(2026, 10, 5, 9, 28)))      # pre-market scan
        self.assertIn("closed", rt.window_reason(et(2026, 10, 5, 16, 0)))    # after the close
        self.assertIn("closed today", rt.window_reason(et(2026, 10, 3, 11, 0)))  # Saturday
        self.assertIn("closed today", rt.window_reason(et(2026, 11, 26, 11, 0)))  # Thanksgiving

    def test_early_close_day_ends_the_window_at_one_pm(self):
        self.assertIsNone(rt.window_reason(et(2025, 11, 28, 12, 59)))
        self.assertIn("closed", rt.window_reason(et(2025, 11, 28, 13, 1)))


class ExtendedWindowTests(unittest.TestCase):
    def test_extended_session_allows_pre_market_and_after_hours_but_not_overnight(self):
        for hour, minute, allowed in ((3, 59, False), (4, 0, True), (8, 0, True), (17, 30, True), (19, 59, True), (20, 0, False)):
            reason = rt.window_reason(et(2026, 10, 5, hour, minute), "Extended")
            self.assertEqual(reason is None, allowed, (hour, minute, reason))

    def test_extended_session_still_respects_holidays_weekends_and_early_closes(self):
        self.assertIn("closed today", rt.window_reason(et(2026, 10, 3, 10, 0), "Extended"))
        self.assertIn("closed today", rt.window_reason(et(2026, 11, 26, 10, 0), "Extended"))
        self.assertIsNone(rt.window_reason(et(2025, 11, 28, 16, 59), "Extended"))
        self.assertIn("closed", rt.window_reason(et(2025, 11, 28, 17, 0), "Extended"))

    def test_regular_remains_the_default_and_the_gate_follows_the_chosen_session(self):
        self.assertIn("open", rt.window_reason(et(2026, 10, 5, 8, 0)))
        early = datetime(2026, 10, 5, 8, 0, tzinfo=ET)
        regular = buy_result()
        rt.gate_result({"market_session": "Regular"}, regular, state=rt.STATE_FLAT, now=early)
        self.assertEqual(regular["action"], "HOLD")
        extended = buy_result()
        rt.gate_result({"market_session": "Extended"}, extended, state=rt.STATE_FLAT, now=early)
        self.assertEqual(extended["action"], "BUY")


class PositionStateTests(unittest.TestCase):
    def test_flat_clears_ownership_and_the_stored_stop(self):
        settings = {rt.OWNS_KEY: True, rt.STOP_KEY: {"entry_atr": 2}}
        self.assertEqual(rt.sync_position_state(settings, False), rt.STATE_FLAT)
        self.assertFalse(settings[rt.OWNS_KEY])
        self.assertNotIn(rt.STOP_KEY, settings)

    def test_existing_position_is_unmanaged_by_default_and_adopted_on_request(self):
        settings = {}
        self.assertEqual(rt.sync_position_state(settings, True, 10), rt.STATE_UNMANAGED)
        self.assertFalse(settings.get(rt.OWNS_KEY))
        settings = {rt.POLICY_KEY: "adopt"}
        self.assertEqual(rt.sync_position_state(settings, True, 10), rt.STATE_MANAGED)
        self.assertTrue(settings[rt.OWNS_KEY])
        self.assertEqual(settings[rt.LAST_QTY_KEY], 10.0)

    def test_ownership_survives_the_gap_between_an_accepted_order_and_a_visible_position(self):
        now = et(2026, 10, 5, 10, 0)
        settings = {}
        rt.record_entry(settings, buy_result(), now.isoformat())
        # open buy order still working: not flat yet
        self.assertEqual(rt.sync_position_state(settings, False, has_open_buy_order=True, now=now), rt.STATE_FLAT)
        self.assertTrue(settings[rt.OWNS_KEY])
        # no order visible and no position yet, but the entry was accepted a minute ago
        self.assertEqual(rt.sync_position_state(settings, False, now=et(2026, 10, 5, 10, 1)), rt.STATE_FLAT)
        self.assertTrue(settings[rt.OWNS_KEY])
        self.assertIn(rt.STOP_KEY, settings)
        # the order fills: the position is managed with the recorded entry ATR
        self.assertEqual(rt.sync_position_state(settings, True, 5, now=et(2026, 10, 5, 10, 2)), rt.STATE_MANAGED)
        self.assertEqual(settings[rt.STOP_KEY]["entry_atr"], 2.0)

    def test_an_entry_that_never_became_a_position_is_cleared_after_the_grace_period(self):
        settings = {}
        rt.record_entry(settings, buy_result(), et(2026, 10, 5, 10, 0).isoformat())
        self.assertEqual(rt.sync_position_state(settings, False, now=et(2026, 10, 5, 10, 11)), rt.STATE_FLAT)
        self.assertFalse(settings[rt.OWNS_KEY])
        self.assertNotIn(rt.STOP_KEY, settings)

    def test_a_position_the_strategy_opened_stays_managed(self):
        settings = {rt.OWNS_KEY: True}
        self.assertEqual(rt.sync_position_state(settings, True, 5), rt.STATE_MANAGED)


class GateTests(unittest.TestCase):
    OPEN = et(2026, 10, 5, 10, 0)

    def gate(self, result, settings=None, state=rt.STATE_FLAT, now=None):
        settings = {} if settings is None else settings
        rt.gate_result(settings, result, state=state, now=now or self.OPEN)
        return result

    def test_a_fresh_buy_inside_the_session_passes(self):
        self.assertEqual(self.gate(buy_result())["action"], "BUY")

    def test_buy_is_held_outside_the_window(self):
        for when in (et(2026, 10, 5, 9, 0), et(2026, 10, 5, 17, 0), et(2026, 10, 3, 11, 0)):
            self.assertEqual(self.gate(buy_result(), now=when)["action"], "HOLD")

    def test_duplicate_signal_after_a_stop_out_does_not_reenter(self):
        settings = {}
        rt.record_entry(settings, buy_result(), "2026-10-05T10:00:00")
        again = self.gate(buy_result(), settings=settings)
        self.assertEqual(again["action"], "HOLD")
        self.assertIn("already acted on", again["reason"])
        fresh = self.gate(buy_result(key="golden_cross:sma_50_200:1Day:2026-10-09:BUY"), settings=settings)
        self.assertEqual(fresh["action"], "BUY")

    def test_unmanaged_position_ignores_strategy_exits_but_a_managed_one_sells(self):
        sell = {"action": "SELL", "reason": "exit", "strategy_preset": {}}
        self.assertEqual(self.gate(dict(sell), state=rt.STATE_UNMANAGED)["action"], "HOLD")
        self.assertEqual(self.gate(dict(sell), state=rt.STATE_MANAGED)["action"], "SELL")

    def test_strategy_exit_waits_for_the_session_but_the_protective_stop_is_separate(self):
        sell = {"action": "SELL", "reason": "exit", "strategy_preset": {}}
        self.assertEqual(self.gate(dict(sell), state=rt.STATE_MANAGED, now=et(2026, 10, 5, 9, 0))["action"], "HOLD")

    def test_corporate_action_pause_blocks_entries_not_exits(self):
        settings = {rt.PAUSE_KEY: "SPY:2026-10-05:4.000000"}
        self.assertEqual(self.gate(buy_result(), settings=settings)["action"], "HOLD")
        sell = {"action": "SELL", "reason": "exit", "strategy_preset": {}}
        self.assertEqual(self.gate(dict(sell), settings=settings, state=rt.STATE_MANAGED)["action"], "SELL")


class ProtectiveStopTests(unittest.TestCase):
    def test_stop_is_anchored_to_broker_average_cost_and_the_stored_entry_atr(self):
        settings = {rt.STOP_KEY: {"stop_atr_mult": 3.0, "entry_atr": 2.0}}
        price, multiple, atr = rt.protective_stop(settings, {"atr": 9.0}, 100.0)
        self.assertEqual((price, multiple, atr), (94.0, 3.0, 2.0))  # entry ATR wins over today's ATR

    def test_falls_back_to_the_current_atr_and_rejects_bad_inputs(self):
        self.assertEqual(rt.protective_stop({}, {"atr": 2.0, "stop_atr_mult": 3.0}, 100.0)[0], 94.0)
        self.assertIsNone(rt.protective_stop({}, {}, 100.0))
        self.assertIsNone(rt.protective_stop({}, {"atr": 2.0}, 0.0))

    def test_adopted_position_records_an_entry_atr_once(self):
        settings = {}
        rt.ensure_stop_recorded(settings, {"atr": 2.0, "stop_atr_mult": 3.0}, "2026-10-05T10:00:00")
        rt.ensure_stop_recorded(settings, {"atr": 9.0, "stop_atr_mult": 3.0}, "2026-10-06T10:00:00")
        self.assertEqual(settings[rt.STOP_KEY]["entry_atr"], 2.0)


class SplitReconcileTests(unittest.TestCase):
    EVENT = SplitEvent("SPY", date(2026, 10, 5), 4.0)

    def settings(self):
        return {
            "custom_buy_price": 400.0, "custom_stop_loss_price": 360.0, "exit_grid_anchor_qty": 10,
            "strategy_preset_stop": {"stop_atr_mult": 3.0, "entry_atr": 8.0},
            "stop_loss_pct": 5.0, rt.LAST_QTY_KEY: 10.0, rt.OWNS_KEY: True,
        }

    def test_applied_split_rescales_levels_and_verifies_the_broker_quantity(self):
        settings = self.settings()
        outcome = rt.apply_split_event(settings, self.EVENT, broker_qty=40.0, has_position=True)
        self.assertEqual(outcome["status"], "applied")
        self.assertEqual(settings["custom_buy_price"], 100.0)
        self.assertEqual(settings["custom_stop_loss_price"], 90.0)
        self.assertEqual(settings["exit_grid_anchor_qty"], 40.0)
        self.assertEqual(settings["strategy_preset_stop"]["entry_atr"], 2.0)
        self.assertEqual(settings["stop_loss_pct"], 5.0)
        self.assertEqual(settings[rt.LAST_QTY_KEY], 40.0)
        self.assertEqual(settings[rt.LAST_CORPORATE_ACTION_KEY], self.EVENT.key)
        self.assertNotIn(rt.PAUSE_KEY, settings)
        self.assertIn("4-for-1", outcome["message"])

    def test_protective_stop_stays_correct_after_the_split(self):
        settings = self.settings()
        rt.apply_split_event(settings, self.EVENT, broker_qty=40.0, has_position=True)
        # broker average cost after a 4:1 split is 400 / 4 = 100; the entry ATR was rescaled to 2.0
        self.assertEqual(rt.protective_stop(settings, {}, 100.0)[0], 94.0)

    def test_reverse_split_with_cash_in_lieu_is_accepted_within_one_share(self):
        settings = {"custom_buy_price": 2.0, rt.LAST_QTY_KEY: 105.0}
        event = SplitEvent("XYZ", date(2026, 10, 5), 0.1)
        outcome = rt.apply_split_event(settings, event, broker_qty=10.0, has_position=True)  # 10.5 expected
        self.assertEqual(outcome["status"], "applied")
        self.assertAlmostEqual(settings["custom_buy_price"], 20.0)

    def test_quantity_mismatch_keeps_entries_paused_and_changes_nothing(self):
        settings = self.settings()
        before = dict(settings)
        outcome = rt.apply_split_event(settings, self.EVENT, broker_qty=10.0, has_position=True)  # broker not updated yet
        self.assertEqual(outcome["status"], "review")
        self.assertEqual(settings[rt.PAUSE_KEY], self.EVENT.key)
        for key in ("custom_buy_price", "custom_stop_loss_price", "exit_grid_anchor_qty"):
            self.assertEqual(settings[key], before[key])
        self.assertNotIn(rt.LAST_CORPORATE_ACTION_KEY, settings)
        self.assertEqual(outcome["changes"], {})

    def test_retry_applies_once_the_broker_books_the_split_and_a_second_run_is_a_noop(self):
        settings = self.settings()
        self.assertEqual(rt.apply_split_event(settings, self.EVENT, broker_qty=10.0, has_position=True)["status"], "review")
        self.assertEqual(rt.apply_split_event(settings, self.EVENT, broker_qty=40.0, has_position=True)["status"], "applied")
        self.assertNotIn(rt.PAUSE_KEY, settings)
        # the engine only asks for events newer than LAST_CORPORATE_ACTION_KEY, so it is not applied twice
        self.assertEqual(settings["custom_buy_price"], 100.0)

    def test_pending_order_in_flight_requires_review(self):
        settings = {"entry_grid_pending_order_id": "abc", "custom_buy_price": 400.0}
        outcome = rt.apply_split_event(settings, self.EVENT, broker_qty=0.0, has_position=False)
        self.assertEqual(outcome["status"], "review")
        self.assertEqual(settings["custom_buy_price"], 400.0)

    def test_flat_bot_still_rescales_saved_levels(self):
        settings = {"custom_buy_price": 400.0}
        outcome = rt.apply_split_event(settings, self.EVENT, broker_qty=0.0, has_position=False)
        self.assertEqual(outcome["status"], "applied")
        self.assertEqual(settings["custom_buy_price"], 100.0)
        self.assertEqual(settings[rt.LAST_QTY_KEY], 0.0)

    def test_baseline_marks_past_splits_processed_without_rescaling(self):
        settings = {"custom_buy_price": 100.0}
        old, future = SplitEvent("SPY", date(2020, 8, 31), 4.0), SplitEvent("SPY", date(2026, 12, 1), 2.0)
        self.assertTrue(rt.baseline_corporate_actions(settings, [old, future], date(2026, 10, 5)))
        self.assertEqual(settings[rt.LAST_CORPORATE_ACTION_KEY], old.key)
        self.assertEqual(settings["custom_buy_price"], 100.0)
        self.assertFalse(rt.baseline_corporate_actions(settings, [old, future], date(2026, 10, 5)))


class ScanTimingTests(unittest.TestCase):
    def test_latest_completed_daily_candle_changes_five_minutes_after_the_close(self):
        self.assertEqual(rt.latest_completed_daily_date(et(2026, 10, 7, 10, 0)), date(2026, 10, 6))   # Wednesday morning
        self.assertEqual(rt.latest_completed_daily_date(et(2026, 10, 7, 16, 4)), date(2026, 10, 6))   # not yet
        self.assertEqual(rt.latest_completed_daily_date(et(2026, 10, 7, 16, 5)), date(2026, 10, 7))   # completes
        self.assertEqual(rt.latest_completed_daily_date(et(2026, 10, 10, 12, 0)), date(2026, 10, 9))  # Saturday -> Friday
        self.assertEqual(rt.latest_completed_daily_date(et(2026, 10, 5, 10, 0)), date(2026, 10, 2))   # Monday -> Friday
        self.assertEqual(rt.latest_completed_daily_date(et(2026, 11, 27, 9, 0)), date(2026, 11, 25))  # day after Thanksgiving
        self.assertEqual(rt.latest_completed_daily_date(et(2026, 11, 27, 13, 5)), date(2026, 11, 27))  # early close at 13:00

    def test_next_evaluation_for_the_regular_session_is_the_next_open(self):
        self.assertEqual(rt.next_evaluation_at(et(2026, 10, 7, 10, 0)), et(2026, 10, 8, 9, 30))
        self.assertEqual(rt.next_evaluation_at(et(2026, 10, 9, 10, 0)), et(2026, 10, 12, 9, 30))   # Friday -> Monday
        self.assertEqual(rt.next_evaluation_at(et(2026, 11, 25, 10, 0)), et(2026, 11, 27, 9, 30))  # skips Thanksgiving
        # Saturday: Friday's candle is already evaluated, the next one completes Monday 16:05 -> first open after is Tuesday.
        self.assertEqual(rt.next_evaluation_at(et(2026, 10, 10, 12, 0)), et(2026, 10, 13, 9, 30))

    def test_next_evaluation_for_the_extended_session_is_the_candle_completion_itself(self):
        self.assertEqual(rt.next_evaluation_at(et(2026, 10, 7, 10, 0), "Extended"), et(2026, 10, 7, 16, 5))
        self.assertEqual(rt.next_evaluation_at(et(2026, 11, 27, 10, 0), "Extended"), et(2026, 11, 27, 13, 5))  # early close
        self.assertEqual(rt.next_evaluation_at(et(2026, 10, 7, 17, 0), "Extended"), et(2026, 10, 8, 16, 5))

    def settings(self, **extra):
        return {"strategy_preset": "golden_cross", "strategy_preset_variant": "sma_50_200", "timeframe": "1Day",
                "market_session": "Regular", **extra}

    def hold(self, candle_date=date(2026, 10, 6), **extra):
        stamp = int(datetime(candle_date.year, candle_date.month, candle_date.day, tzinfo=ET).timestamp())
        result = {"action": "HOLD", "evaluation_status": "valid", "candle_meta": {"candle_key": f"1DAY:{stamp}", "candle_status": "closed"},
                  "strategy_preset": {"status": "READY", "action": "HOLD"}}
        result.update(extra)
        return result

    def mark_ok(self, result, now=None, **kw):
        args = dict(has_position=False, has_open_order=False, now=now or et(2026, 10, 7, 10, 0))
        args.update(kw)
        return rt.can_mark_scan_done(result, rt.STATE_FLAT, **args)

    def test_only_a_clean_flat_hold_on_the_newest_candle_can_idle(self):
        self.assertTrue(self.mark_ok(self.hold()))
        self.assertFalse(self.mark_ok(self.hold(), has_position=True))
        self.assertFalse(self.mark_ok(self.hold(), has_open_order=True))
        self.assertFalse(rt.can_mark_scan_done(self.hold(), rt.STATE_MANAGED, has_position=False, has_open_order=False, now=et(2026, 10, 7, 10, 0)))
        self.assertFalse(self.mark_ok(self.hold(action="BUY")))                                  # a signal was blocked/held back
        blocked = self.hold(); blocked["strategy_preset"] = {"status": "READY", "action": "BUY"}
        self.assertFalse(self.mark_ok(blocked))
        waiting = self.hold(); waiting["strategy_preset"] = {"status": "WAITING_DATA", "action": "HOLD"}
        self.assertFalse(self.mark_ok(waiting))
        self.assertFalse(self.mark_ok(self.hold(evaluation_status="waiting")))
        self.assertFalse(self.mark_ok(self.hold(candle_date=date(2026, 10, 5))))               # data a day behind
        forming = self.hold(); forming["candle_meta"]["candle_status"] = "forming"
        self.assertFalse(self.mark_ok(forming))
        missing = self.hold(); missing["candle_meta"] = {}
        self.assertFalse(self.mark_ok(missing))

    def test_skip_only_within_the_same_candle_and_for_an_unchanged_flat_bot(self):
        settings = self.settings()
        marker = rt.scan_marker(settings, et(2026, 10, 7, 10, 0))
        skip = lambda now, s=None, **kw: rt.scan_should_skip(marker, s or settings, now, **{"has_position": False, "has_open_order": False, **kw})
        self.assertTrue(skip(et(2026, 10, 7, 10, 2)))
        self.assertTrue(skip(et(2026, 10, 7, 15, 59)))
        self.assertFalse(skip(et(2026, 10, 7, 16, 5)))      # the next daily candle completed
        self.assertFalse(skip(et(2026, 10, 8, 9, 30)))
        self.assertFalse(skip(et(2026, 10, 7, 10, 2), has_position=True))
        self.assertFalse(skip(et(2026, 10, 7, 10, 2), has_open_order=True))
        self.assertFalse(skip(et(2026, 10, 7, 10, 2), {**settings, rt.PAUSE_KEY: True}))
        for changed in ({"strategy_preset": "supertrend_ema200"}, {"strategy_preset_variant": "ema_50_200"},
                        {"timeframe": "1Hour"}, {"market_session": "Extended"}):
            self.assertFalse(skip(et(2026, 10, 7, 10, 2), {**settings, **changed}), changed)
        self.assertFalse(rt.scan_should_skip(None, settings, et(2026, 10, 7, 10, 2), has_position=False, has_open_order=False))


if __name__ == "__main__":
    unittest.main()
