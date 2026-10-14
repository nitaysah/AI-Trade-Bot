"""Session Rider schedule rules (pure): times, modes, early closes, DST, weekends, entry windows."""
import os
import sys
import unittest
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import session_schedule as S  # noqa: E402
import strategy_presets as sp  # noqa: E402

ET = ZoneInfo("America/New_York")


def cfg(**kw):
    return S.normalized_settings({S.BUY_KEY: kw.get("buy"), S.SELL_KEY: kw.get("sell"),
                                  S.SKIP_WEEKEND_KEY: kw.get("skip", False), S.LOSS_PAUSE_KEY: kw.get("loss")})


class TimeNormalization(unittest.TestCase):
    def test_accepts_anchors_and_minutes(self):
        self.assertEqual(S.normalize_time("close-5"), "close-5")
        self.assertEqual(S.normalize_time("Close - 1"), "close-1")
        self.assertEqual(S.normalize_time("open+3"), "open+3")
        self.assertEqual(S.normalize_time("9:31"), "09:31")
        self.assertEqual(S.normalize_time("15:59"), "15:59")

    def test_rejects_out_of_bounds(self):
        for bad in ("close-6", "open+0", "open-1", "close+1", "09:30", "16:00", "15:60", "x", "", None):
            self.assertIsNone(S.normalize_time(bad), bad)

    def test_defaults_and_mode(self):
        c = cfg()
        self.assertEqual((c[S.BUY_KEY], c[S.SELL_KEY], c["mode"]), ("close-5", "open+5", S.MODE_OVERNIGHT))
        self.assertEqual(cfg(buy="open+1", sell="close-1")["mode"], S.MODE_INTRADAY)
        self.assertIsNone(cfg(buy="09:35", sell="open+5")["mode"])  # same minute

    def test_validate_errors(self):
        self.assertEqual(S.validate({S.BUY_KEY: "09:35", S.SELL_KEY: "open+5"}), ["Buy time and sell time must be different."])
        self.assertTrue(S.validate({S.BUY_KEY: "16:00"}))
        self.assertTrue(S.validate({S.LOSS_PAUSE_KEY: 2}))
        self.assertTrue(S.validate({S.LOSS_PAUSE_KEY: 51}))
        self.assertEqual(S.validate({S.LOSS_PAUSE_KEY: 5}), [])

    def test_options_and_labels(self):
        opts = S.time_options()
        values = [o["value"] for o in opts]
        self.assertEqual(values[:5], ["open+1", "open+2", "open+3", "open+4", "open+5"])
        self.assertEqual(values[-5:], ["close-5", "close-4", "close-3", "close-2", "close-1"])
        self.assertIn("09:45", values)
        self.assertIn("15:45", values)
        self.assertTrue(next(o for o in opts if o["value"] == "close-1")["tight"])
        self.assertFalse(next(o for o in opts if o["value"] == "close-5")["tight"])
        self.assertEqual(S.et_ct_label("close-5"), "2:55 PM CT")          # Central time only
        self.assertTrue(S.needs_minute_bars("close-1", "open+5"))
        self.assertFalse(S.needs_minute_bars("close-5", "open+5"))


class Resolution(unittest.TestCase):
    def test_early_close_moves_anchors_and_clamps_clock_times(self):
        day = date(2026, 11, 27)  # day after Thanksgiving, 1:00 PM close
        self.assertEqual(S.resolve(day, "close-5")["at"].time().isoformat(), "12:55:00")
        self.assertEqual(S.resolve(day, "close-1")["at"].time().isoformat(), "12:59:00")
        clamped = S.resolve(day, "15:30")
        self.assertEqual(clamped["at"].time().isoformat(), "12:55:00")
        self.assertTrue(clamped["adjusted"])
        self.assertIsNone(S.resolve(date(2026, 11, 26), "close-5"))  # Thanksgiving

    def test_intraday_collision_on_early_close_skips_the_day(self):
        c = cfg(buy="close-5", sell="15:58")
        self.assertEqual(c["mode"], S.MODE_INTRADAY)
        self.assertIn("early close", S.skip_reason(date(2026, 11, 27), c))
        self.assertIsNone(S.buy_event(date(2026, 11, 27), c))
        self.assertIsNotNone(S.buy_event(date(2026, 11, 25), c))

    def test_dst_boundary_keeps_et_clock_time(self):
        before, after = S.resolve(date(2026, 3, 6), "close-5")["at"], S.resolve(date(2026, 3, 9), "close-5")["at"]
        self.assertEqual((before.hour, before.minute, after.hour, after.minute), (15, 55, 15, 55))
        self.assertNotEqual(before.utcoffset(), after.utcoffset())

    def test_overnight_sell_is_next_trading_day(self):
        c = cfg()
        fri = S.buy_event(date(2026, 10, 9), c)
        self.assertEqual(fri["sell"]["session"], "2026-10-12")  # Friday -> Monday
        pre_holiday = S.buy_event(date(2026, 11, 25), c)
        self.assertEqual(pre_holiday["sell"]["session"], "2026-11-27")  # skips Thanksgiving

    def test_skip_weekend_rule(self):
        c = cfg(skip=True)
        self.assertIsNone(S.buy_event(date(2026, 10, 9), c))  # Friday
        self.assertIsNone(S.buy_event(date(2026, 11, 25), c))  # day before Thanksgiving
        self.assertIsNotNone(S.buy_event(date(2026, 10, 8), c))
        intraday = cfg(buy="open+5", sell="close-5", skip=True)
        self.assertIsNotNone(S.buy_event(date(2026, 10, 9), intraday))  # no overnight hold, nothing to skip


class Windows(unittest.TestCase):
    def test_entry_window_and_arming(self):
        c = cfg()
        at = datetime(2026, 10, 8, 15, 55, tzinfo=ET)
        self.assertIsNone(S.entry_due(at - timedelta(seconds=1), c, None))
        self.assertIsNotNone(S.entry_due(at, c, None))
        self.assertIsNotNone(S.entry_due(at + timedelta(minutes=4, seconds=29), c, None))
        self.assertIsNone(S.entry_due(at + timedelta(minutes=4, seconds=30), c, None))  # close - 30s cutoff
        # Saved inside the window: that window is not used.
        self.assertIsNone(S.entry_due(at + timedelta(minutes=1), c, at + timedelta(seconds=30)))
        self.assertIsNotNone(S.entry_due(at + timedelta(minutes=1), c, at - timedelta(minutes=10)))

    def test_window_never_reaches_the_close(self):
        c = cfg(buy="close-1", sell="open+5")
        ev = S.buy_event(date(2026, 10, 8), c)
        self.assertEqual(ev["window_end"].time().isoformat(), "15:59:30")

    def test_sell_due_and_phase(self):
        c = cfg()
        self.assertEqual(S.sell_due_at("2026-10-09", datetime(2026, 10, 12, 9, 0, tzinfo=ET), c),
                         datetime(2026, 10, 12, 9, 35, tzinfo=ET))
        sat = datetime(2026, 10, 10, 12, 0, tzinfo=ET)
        self.assertEqual(S.schedule_phase(sat, c)["phase"], "HOLDING_OVERNIGHT")
        mon_after = datetime(2026, 10, 12, 10, 0, tzinfo=ET)
        phase = S.schedule_phase(mon_after, c)
        self.assertEqual((phase["phase"], phase["next_event"]), ("FLAT", "BUY"))
        self.assertEqual(phase["next_at"], datetime(2026, 10, 12, 15, 55, tzinfo=ET))

    def test_due_alerts_once_inside_grace(self):
        c = cfg()
        alerts = S.due_alerts(datetime(2026, 10, 12, 9, 36, tzinfo=ET), c)
        self.assertEqual([(a["event"], a["key"]) for a in alerts], [("SELL", "2026-10-12:SELL")])
        self.assertEqual(S.due_alerts(datetime(2026, 10, 12, 9, 50, tzinfo=ET), c), [])
        self.assertEqual(S.next_event_time(datetime(2026, 10, 9, 16, 1, tzinfo=ET), c), datetime(2026, 10, 12, 9, 35, tzinfo=ET))


class CatalogIntegration(unittest.TestCase):
    def test_preset_is_served_and_applied(self):
        option = next(o for o in sp.strategy_preset_options() if o["id"] == "session_rider")
        self.assertEqual(option["kind"], "schedule")
        self.assertEqual(option["variants"][0]["warmup_bars"], 0)
        self.assertEqual(option["schedule"]["default_buy"], "close-5")
        applied = sp.apply_strategy_preset({"strategy_preset_enabled": True, "strategy_preset": "session_rider",
                                            "timeframe": "5Min", S.BUY_KEY: "Close - 2"})
        self.assertEqual(applied["timeframe"], "1Day")
        self.assertEqual(applied[S.BUY_KEY], "close-2")
        self.assertEqual(applied[S.SELL_KEY], "open+5")
        self.assertTrue(sp.is_schedule_active(applied))
        self.assertIsNone(sp.effective_scan_policy(applied))

    def test_validation_and_signal_paths_refuse(self):
        on = {"strategy_preset_enabled": True, "strategy_preset": "session_rider"}
        self.assertEqual(sp.validate_strategy_preset({**on, S.BUY_KEY: "open+5", S.SELL_KEY: "09:35"}, ticker="SPY"),
                         ["Buy time and sell time must be different."])
        self.assertTrue(sp.validate_strategy_preset(on, ticker="USD"))
        self.assertTrue(sp.validate_strategy_preset(on, ticker="BTCUSD", is_crypto=True))
        result = sp.evaluate_strategy_preset(None, "session_rider")
        self.assertEqual((result["status"], result["action"]), (sp.STATUS_INVALID, "HOLD"))
        with self.assertRaises(ValueError):
            sp.compute_strategy_frame(None, "session_rider")


if __name__ == "__main__":
    unittest.main()
