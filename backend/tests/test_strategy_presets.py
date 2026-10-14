import copy
import unittest
from unittest.mock import patch
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd

import nyse_calendar
import strategy_presets as sp

ET = ZoneInfo("America/New_York")


def setUpModule():
    # The rule-based fallback is deterministic; official-calendar behaviour is tested separately.
    global _calendar_patch
    _calendar_patch = patch.object(nyse_calendar, "_calendar", return_value=None)
    _calendar_patch.start()


def tearDownModule():
    _calendar_patch.stop()


def make_frame(close, start="2020-01-02", freq="B"):
    close = np.asarray(close, dtype=float)
    index = pd.date_range(start=start, periods=len(close), freq=freq)
    return pd.DataFrame(
        {"Open": close, "High": close + 0.5, "Low": close - 0.5, "Close": close, "Volume": 1000.0},
        index=index,
    )


def one_cross_closes():
    # down, then a strong rally (one bull cross), then a decline (one bear cross)
    return np.concatenate([
        np.linspace(100, 60, 120),
        np.linspace(60, 140, 120),
        np.linspace(140, 70, 120),
    ])


def regime_walk(n=1200, seed=7):
    rng = np.random.default_rng(seed)
    trend = 100 + 25 * np.sin(np.linspace(0, 9 * np.pi, n)) + np.linspace(0, 40, n)
    return trend + np.cumsum(rng.normal(0, 0.6, n)) * 0.4


def choppy_walk(n=1500, seed=7):
    """Trending series with enough noise for Supertrend to flip several times."""
    rng = np.random.default_rng(seed)
    base = 100 + 30 * np.sin(np.linspace(0, 14 * np.pi, n)) + np.linspace(0, 60, n)
    return base + np.cumsum(rng.normal(0, 2.5, n)) * 0.5


class GoldenCrossTests(unittest.TestCase):
    def setUp(self):
        self.df = make_frame(one_cross_closes())
        self.frame = sp.compute_strategy_frame(self.df, "golden_cross", "sma_20_50")

    def test_exactly_one_entry_and_one_exit_event_per_cross(self):
        self.assertEqual(int(self.frame["SP_entry"].sum()), 1)
        self.assertEqual(int(self.frame["SP_exit_event"].sum()), 1)

    def test_entry_bar_matches_independent_cross_calculation(self):
        close = self.df["Close"]
        fast, slow = close.rolling(20).mean(), close.rolling(50).mean()
        diff = (fast - slow)
        expected = diff.index[(diff > 0) & (diff.shift(1) < 0)]
        self.assertEqual(list(self.frame.index[self.frame["SP_entry"]]), list(expected))

    def test_no_signal_before_warmup_and_flat_series_never_crosses(self):
        required = sp.warmup_bars("golden_cross", "sma_20_50")
        self.assertFalse(self.frame["SP_entry"].iloc[: required - 1].any())
        flat = sp.compute_strategy_frame(make_frame([100.0] * 120), "golden_cross", "sma_20_50")
        self.assertFalse(flat["SP_entry"].any())
        # equal averages (a touch) are not a cross: a flat start followed by a rise has no prior bearish sign
        rise = sp.compute_strategy_frame(make_frame([100.0] * 80 + list(np.linspace(100, 130, 60))),
                                         "golden_cross", "sma_20_50")
        self.assertFalse(rise["SP_entry"].any())

    def test_state_replay_and_exit_condition(self):
        entry_pos = int(np.flatnonzero(self.frame["SP_entry"].to_numpy())[0])
        self.assertEqual(self.frame["SP_state"].iloc[entry_pos], "LONG")
        exit_pos = int(np.flatnonzero(self.frame["SP_exit_event"].to_numpy())[0])
        self.assertEqual(self.frame["SP_state"].iloc[exit_pos], "FLAT")
        self.assertGreater(exit_pos, entry_pos)

    def test_variants_use_different_parameters(self):
        classic = sp.compute_strategy_frame(self.df, "golden_cross", "sma_50_200")
        ema = sp.compute_strategy_frame(self.df, "golden_cross", "ema_50_200")
        self.assertFalse(np.allclose(
            classic["SP_fast"].dropna().to_numpy()[-20:], ema["SP_fast"].dropna().to_numpy()[-20:]))
        self.assertGreater(sp.warmup_bars("golden_cross", "ema_50_200"), sp.warmup_bars("golden_cross", "sma_50_200"))


class SupertrendTests(unittest.TestCase):
    def setUp(self):
        self.df = make_frame(choppy_walk())

    def test_entries_only_on_bullish_flip_above_ema200(self):
        frame = sp.compute_strategy_frame(self.df, "supertrend_ema200", "classic_10_3")
        entries = frame[frame["SP_entry"]]
        self.assertGreater(len(entries), 0)
        self.assertTrue((entries["Close"] > entries["SP_ema"]).all())
        pos = np.flatnonzero(frame["SP_entry"].to_numpy())
        self.assertTrue((frame["SP_st_dir"].to_numpy()[pos] == 1.0).all())
        self.assertTrue((frame["SP_st_dir"].to_numpy()[pos - 1] == -1.0).all())
        self.assertGreater(int(frame["SP_exit_event"].sum()), 0)

    def test_aggressive_variant_flips_more_often(self):
        classic = sp.compute_strategy_frame(self.df, "supertrend_ema200", "classic_10_3")
        aggressive = sp.compute_strategy_frame(self.df, "supertrend_ema200", "aggressive_7_2")
        flips = lambda f: int((f["SP_st_dir"].diff().abs() > 0).sum())
        self.assertGreater(flips(aggressive), flips(classic))

    def test_no_entry_when_flip_happens_below_ema(self):
        frame = sp.compute_strategy_frame(self.df, "supertrend_ema200", "classic_10_3")
        flips_up = (frame["SP_st_dir"] == 1.0) & (frame["SP_st_dir"].shift(1) == -1.0) & frame["SP_ready"]
        below = flips_up & (frame["Close"] <= frame["SP_ema"])
        self.assertFalse((frame["SP_entry"] & below).any())

    def test_warmup_covers_ema_convergence(self):
        self.assertGreaterEqual(sp.warmup_bars("supertrend_ema200", "classic_10_3"), 601)


class NoLookaheadTests(unittest.TestCase):
    def test_prefix_evaluation_equals_full_frame_row(self):
        cases = [
            ("golden_cross", "sma_20_50", make_frame(one_cross_closes())),
            ("golden_cross", "ema_50_200", make_frame(regime_walk(900, seed=3))),
            ("supertrend_ema200", "classic_10_3", make_frame(regime_walk(1000, seed=11))),
        ]
        columns = ["SP_ready", "SP_entry", "SP_exit_cond", "SP_exit_event", "SP_state", "SP_atr"]
        for preset, variant, df in cases:
            full = sp.compute_strategy_frame(df, preset, variant)
            for t in (len(df) - 1, len(df) - 17, len(df) // 2 + 3, 700 if len(df) > 700 else len(df) - 40):
                prefix = sp.compute_strategy_frame(df.iloc[: t + 1], preset, variant)
                for column in columns:
                    a, b = prefix[column].iloc[-1], full[column].iloc[t]
                    if isinstance(a, (float, np.floating)):
                        self.assertTrue((np.isnan(a) and np.isnan(b)) or np.isclose(a, b), (preset, t, column))
                    else:
                        self.assertEqual(a, b, (preset, t, column))


class CompletedBarsTests(unittest.TestCase):
    def test_daily_forming_candle_is_dropped_until_after_close(self):
        df = make_frame([10, 11, 12, 13], start="2026-09-28")  # Mon..Thu
        during = datetime(2026, 10, 1, 15, 0, tzinfo=ET).astimezone(timezone.utc)
        after = datetime(2026, 10, 1, 16, 6, tzinfo=ET).astimezone(timezone.utc)
        self.assertEqual(len(sp.completed_bars(df, "1Day", during)), 3)
        self.assertEqual(len(sp.completed_bars(df, "1Day", after)), 4)

    def test_daily_bars_stamped_at_utc_midnight_map_to_the_right_session(self):
        # 2026-10-02 00:00 UTC is 20:00 ET on 2026-10-01; the bar describes Friday 2026-10-02.
        stamped = pd.Timestamp("2026-10-02 00:00", tz="UTC")
        completes = sp.bar_completion_time(stamped, "1Day").tz_convert(ET)
        self.assertEqual(completes.strftime("%Y-%m-%d %H:%M"), "2026-10-02 16:05")
        for stamp in ("2026-10-02 04:00", "2026-10-02 13:30"):  # midnight ET (EDT) and the 9:30 open
            done = sp.bar_completion_time(pd.Timestamp(stamp, tz="UTC"), "1Day").tz_convert(ET)
            self.assertEqual(done.strftime("%Y-%m-%d %H:%M"), "2026-10-02 16:05")

    def test_intraday_hour_bar_completes_at_session_close_not_after(self):
        idx = pd.DatetimeIndex(["2026-10-01 14:30", "2026-10-01 15:30"])
        df = pd.DataFrame({"Open": 1.0, "High": 2.0, "Low": 0.5, "Close": 1.5}, index=idx)
        now = datetime(2026, 10, 1, 16, 0, 1, tzinfo=ET).astimezone(timezone.utc)
        self.assertEqual(len(sp.completed_bars(df, "1Hour", now)), 2)
        now = datetime(2026, 10, 1, 15, 45, tzinfo=ET).astimezone(timezone.utc)
        self.assertEqual(len(sp.completed_bars(df, "1Hour", now)), 1)

    def test_duplicates_order_and_nan_rows(self):
        df = make_frame([10, 11, 12, 13], start="2026-09-28")
        messy = pd.concat([df.iloc[[2, 0, 3, 1]], df.iloc[[1]]])
        messy.iloc[0, messy.columns.get_loc("Close")] = np.nan
        out = sp.completed_bars(messy, "1Day", datetime(2026, 11, 1, tzinfo=timezone.utc))
        self.assertTrue(out.index.is_monotonic_increasing)
        self.assertFalse(out.index.duplicated().any())
        self.assertTrue(np.isfinite(out["Close"]).all())
        again = sp.completed_bars(out, "1Day", datetime(2026, 11, 1, tzinfo=timezone.utc))
        self.assertEqual(list(again.index), list(out.index))


class EvaluateTests(unittest.TestCase):
    def setUp(self):
        self.df = make_frame(one_cross_closes())
        self.full = sp.compute_strategy_frame(self.df, "golden_cross", "sma_20_50")
        self.entry_pos = int(np.flatnonzero(self.full["SP_entry"].to_numpy())[0])
        self.exit_pos = int(np.flatnonzero(self.full["SP_exit_event"].to_numpy())[0])

    def evaluate(self, upto, **kwargs):
        return sp.evaluate_strategy_preset(self.full.iloc[: upto + 1], "golden_cross", "sma_20_50",
                                           timeframe="1Day", **kwargs)

    def test_buy_on_fresh_entry_only_when_flat(self):
        flat = self.evaluate(self.entry_pos, has_position=False)
        self.assertEqual((flat["status"], flat["action"]), ("READY", "BUY"))
        self.assertIn(":BUY", flat["signal_key"])
        self.assertEqual(self.evaluate(self.entry_pos, has_position=True)["action"], "HOLD")
        stale = self.evaluate(self.entry_pos + 5, has_position=False)
        self.assertEqual(stale["action"], "HOLD")  # not a fresh signal any more

    def test_long_only_never_sells_without_a_position(self):
        for pos in range(len(self.full) - 1, len(self.full) - 60, -1):
            self.assertNotEqual(self.evaluate(pos, has_position=False)["action"], "SELL")

    def test_sell_is_state_based_and_survives_missed_bars(self):
        self.assertEqual(self.evaluate(self.exit_pos, has_position=True)["action"], "SELL")
        self.assertEqual(self.evaluate(self.exit_pos + 10, has_position=True)["action"], "SELL")

    def test_alert_direction_is_state_based(self):
        self.assertEqual(self.evaluate(self.entry_pos + 3)["alert_direction"], "BULLISH")
        self.assertEqual(self.evaluate(self.exit_pos + 3)["alert_direction"], "BEARISH")

    def test_waiting_degraded_and_open_candle_never_signal(self):
        waiting = self.evaluate(10)
        self.assertEqual((waiting["status"], waiting["action"]), ("WAITING_DATA", "HOLD"))
        self.assertIn("/", waiting["reason"])
        degraded = self.evaluate(self.entry_pos, data_quality="DEGRADED")
        self.assertEqual((degraded["status"], degraded["action"]), ("DEGRADED", "HOLD"))
        open_candle = self.evaluate(self.entry_pos, candle_ok=False)
        self.assertEqual((open_candle["status"], open_candle["action"]), ("WAITING_DATA", "HOLD"))

    def test_invalid_preset_and_unsupported_timeframe(self):
        self.assertEqual(sp.evaluate_strategy_preset(self.full, "nope")["status"], "INVALID_PRESET")
        wrong_tf = sp.evaluate_strategy_preset(self.full, "golden_cross", "sma_20_50", timeframe="2Min")
        self.assertEqual(wrong_tf["status"], "INVALID_PRESET")

    def test_protective_stop_uses_strategy_atr(self):
        result = self.evaluate(self.entry_pos)
        self.assertAlmostEqual(result["protective_stop_distance"], 3.0 * result["atr"])


class PresetActionTests(unittest.TestCase):
    def test_shared_decision_rule_truth_table(self):
        for has_position in (False, True):
            for entry in (False, True):
                for exit_cond in (False, True):
                    expected = "HOLD"
                    if has_position and exit_cond:
                        expected = "SELL"
                    elif (not has_position) and entry:
                        expected = "BUY"
                    self.assertEqual(sp.preset_action(has_position, entry, exit_cond), expected)


class SettingsTests(unittest.TestCase):
    def manual(self):
        return {
            "buy_mode": "swing_points", "sell_mode": "hybrid", "indicators": ["RSI", "MACD"],
            "min_buy_signals": 3, "min_sell_signals": 2, "threshold": 3, "sell_threshold": 2,
            "timeframe": "2Min", "market_session": "24h", "ext_hours": True,  # values the engine cannot run
            "risk_per_trade": 10, "max_position_pct": 100, "bot_loss_limit_pct": 50,
            "allocation_mode": "fixed", "trade_amount": 100,
            "enable_cooldown": True, "enable_signal_reset": True, "enable_smart_reentry": True,
        }

    def test_the_manual_reentry_rules_are_switched_off_while_a_strategy_is_on_and_restored_after(self):
        for preset in ("golden_cross", "supertrend_ema200"):
            enabled = sp.apply_strategy_preset({**self.manual(), "strategy_preset_enabled": True, "strategy_preset": preset})
            self.assertEqual({k: enabled[k] for k in sp.PRESET_FORCED_OFF}, sp.PRESET_FORCED_OFF)
            self.assertEqual(sp.PRESET_FORCED_OFF, {"enable_cooldown": False, "enable_signal_reset": False, "enable_smart_reentry": False})
            self.assertEqual([enabled["_strategy_manual_backup"][k] for k in sp.PRESET_FORCED_OFF], [True, True, True])
            restored = sp.apply_strategy_preset({**enabled, "strategy_preset_enabled": False})
            self.assertEqual({k: restored[k] for k in sp.PRESET_FORCED_OFF}, {"enable_cooldown": True, "enable_signal_reset": True, "enable_smart_reentry": True})

    def test_a_client_cannot_turn_the_reentry_rules_back_on_while_a_strategy_is_enabled(self):
        enabled = sp.apply_strategy_preset({**self.manual(), "strategy_preset_enabled": True, "strategy_preset": "golden_cross",
                                            "enable_cooldown": True, "enable_signal_reset": True, "enable_smart_reentry": True})
        self.assertFalse(any(enabled[k] for k in sp.PRESET_FORCED_OFF))
        again = sp.apply_strategy_preset({**enabled, "enable_cooldown": True})  # a later save re-sending the old toggle
        self.assertFalse(again["enable_cooldown"])
        self.assertTrue(again["_strategy_manual_backup"]["enable_cooldown"])  # the user's own value survives

    def test_unset_reentry_toggles_come_back_with_their_bot_defaults(self):
        bare = {"strategy_preset_enabled": True, "strategy_preset": "golden_cross"}
        enabled = sp.apply_strategy_preset(bare)
        restored = sp.apply_strategy_preset({**enabled, "strategy_preset_enabled": False})
        self.assertEqual((restored["enable_cooldown"], restored["enable_signal_reset"], restored["enable_smart_reentry"]), (True, True, False))

    def test_enable_backs_up_manual_config_and_disable_restores_it(self):
        manual = self.manual()
        enabled = sp.apply_strategy_preset({**manual, "strategy_preset_enabled": True, "strategy_preset": "golden_cross"})
        self.assertEqual((enabled["buy_mode"], enabled["sell_mode"]), (sp.PRESET_MODE, sp.PRESET_MODE))
        self.assertEqual(enabled["timeframe"], "1Day")
        self.assertEqual(enabled["market_session"], "Regular")
        self.assertFalse(enabled["ext_hours"])
        self.assertNotIn(sp.PRESET_MODE, enabled["_strategy_manual_backup"].values())
        restored = sp.apply_strategy_preset({**enabled, "strategy_preset_enabled": False})
        for key in sp.MANUAL_BACKUP_KEYS:
            if key in sp.ALLOWED_OVERRIDE_KEYS:
                self.assertEqual(restored[key], enabled[key], key)  # timeframe/session stay as the user has them
            else:
                self.assertEqual(restored[key], manual[key], key)
        self.assertNotIn("_strategy_manual_backup", restored)
        self.assertEqual(restored["strategy_preset"], sp.DISABLED)

    def test_risk_and_sizing_fields_are_untouched(self):
        manual = self.manual()
        enabled = sp.apply_strategy_preset({**manual, "strategy_preset_enabled": True, "strategy_preset": "supertrend_ema200"})
        for key in ("risk_per_trade", "max_position_pct", "bot_loss_limit_pct", "allocation_mode", "trade_amount"):
            self.assertEqual(enabled[key], manual[key])

    def test_enabling_twice_keeps_the_original_backup(self):
        first = sp.apply_strategy_preset({**self.manual(), "strategy_preset_enabled": True, "strategy_preset": "golden_cross"})
        second = sp.apply_strategy_preset(copy.deepcopy(first))
        self.assertEqual(second["_strategy_manual_backup"], first["_strategy_manual_backup"])
        self.assertEqual(second["_strategy_manual_backup"]["buy_mode"], "swing_points")

    def test_backup_prefers_prior_persisted_settings_over_form_values(self):
        prior = self.manual()
        form = {**self.manual(), "timeframe": "1Day", "strategy_preset_enabled": True, "strategy_preset": "golden_cross"}
        enabled = sp.apply_strategy_preset(form, prior)
        self.assertEqual(enabled["_strategy_manual_backup"]["timeframe"], prior["timeframe"])

    def test_an_unrunnable_timeframe_is_replaced_and_disabling_keeps_the_users_current_value(self):
        for preset in ("golden_cross", "supertrend_ema200"):
            enabled = sp.apply_strategy_preset({"timeframe": "2Min", "strategy_preset_enabled": True, "strategy_preset": preset})
            self.assertEqual(enabled["timeframe"], "1Day")
            self.assertEqual(enabled["_strategy_manual_backup"]["timeframe"], "2Min")
            restored = sp.apply_strategy_preset({**enabled, "timeframe": "15Min", "strategy_preset_enabled": False})
            self.assertEqual(restored["timeframe"], "15Min")  # not rolled back to the backup

    def test_any_timeframe_or_session_the_engine_can_run_is_kept_as_the_user_chose(self):
        for preset in ("golden_cross", "supertrend_ema200"):
            enabled = sp.apply_strategy_preset({"timeframe": "5Min", "market_session": "Extended", "ext_hours": True,
                                                "strategy_preset_enabled": True, "strategy_preset": preset})
            self.assertEqual((enabled["timeframe"], enabled["market_session"]), ("5Min", "Extended"))
            self.assertTrue(enabled["ext_hours"])  # only forced off when the session is Regular

    def test_untested_settings_are_flagged_but_designed_settings_are_not(self):
        self.assertEqual(sp.untested_settings_note("golden_cross", "1Day", "Regular"), "")
        note = sp.untested_settings_note("golden_cross", "5Min", "Extended")
        self.assertIn("5Min", note)
        self.assertIn("Extended", note)
        self.assertIn("untested", note)
        self.assertEqual(sp.untested_settings_note("nope", "5Min"), "")

    def test_a_supported_timeframe_and_session_chosen_by_the_user_are_kept_untouched(self):
        enabled = sp.apply_strategy_preset({"timeframe": "1Day", "market_session": "regular", "ext_hours": True,
                                            "strategy_preset_enabled": True, "strategy_preset": "golden_cross"})
        self.assertEqual((enabled["timeframe"], enabled["market_session"]), ("1Day", "Regular"))  # same value, canonical casing
        self.assertFalse(enabled["ext_hours"])

    def test_unsupported_timeframe_or_session_is_replaced_by_the_strategy_and_disabling_keeps_current_fields(self):
        enabled = sp.apply_strategy_preset({"timeframe": "2Min", "market_session": "24h", "ext_hours": True,
                                            "strategy_preset_enabled": True, "strategy_preset": "supertrend_ema200"})
        self.assertEqual((enabled["timeframe"], enabled["market_session"]), ("1Day", "Regular"))
        restored = sp.apply_strategy_preset({**enabled, "strategy_preset_enabled": False})
        self.assertEqual((restored["timeframe"], restored["market_session"], restored["ext_hours"]), ("1Day", "Regular", False))

    def test_no_v1_strategy_invents_a_capital_amount_and_a_malformed_one_is_rejected(self):
        for option in sp.strategy_preset_options():
            self.assertEqual(option["suggested_sizing"], {})
        import dataclasses
        base = sp.PRESETS["golden_cross"]
        for bad in ({"max_position_pct": 1.0}, {"allocation_mode": "yolo"}, {"trade_amount": -5}, {"trade_amount": True}):
            sp.PRESETS["golden_cross"] = dataclasses.replace(base, suggested_sizing=bad)
            try:
                with self.assertRaises(ValueError, msg=str(bad)):
                    sp._validate_catalog()
            finally:
                sp.PRESETS["golden_cross"] = base
        sp.PRESETS["golden_cross"] = dataclasses.replace(base, suggested_sizing={"allocation_mode": "compound", "trade_amount": 500})
        try:
            sp._validate_catalog()
            self.assertEqual(sp.strategy_preset_options()[0]["suggested_sizing"], {"allocation_mode": "compound", "trade_amount": 500})
        finally:
            sp.PRESETS["golden_cross"] = base

    def test_each_variant_can_carry_its_own_timeframe_session_and_sizing_and_inherits_otherwise(self):
        import dataclasses
        base = sp.PRESETS["golden_cross"]
        own = dataclasses.replace(base.variants[1], supported_timeframes=("1Hour",), default_timeframe="1Hour",
                                  supported_sessions=("Extended",), suggested_sizing={"allocation_mode": "compound", "trade_amount": 750})
        sp.PRESETS["golden_cross"] = dataclasses.replace(base, variants=(base.variants[0], own, base.variants[2]),
                                                         suggested_sizing={"trade_amount": 300})
        try:
            sp._validate_catalog()
            variants = {v["id"]: v for v in sp.strategy_preset_options()[0]["variants"]}
            self.assertEqual(variants["sma_50_200"]["supported_timeframes"], ["1Day"])            # inherited
            self.assertEqual(variants["sma_50_200"]["suggested_sizing"], {"trade_amount": 300})  # inherited
            self.assertEqual(variants["ema_50_200"]["supported_timeframes"], ["1Hour"])
            self.assertEqual(variants["ema_50_200"]["supported_sessions"], ["Extended"])
            self.assertEqual(variants["ema_50_200"]["default_timeframe"], "1Hour")
            self.assertEqual(variants["ema_50_200"]["suggested_sizing"], {"allocation_mode": "compound", "trade_amount": 750})
            # the untested note follows the chosen variant
            self.assertEqual(sp.untested_settings_note("golden_cross", "1Hour", "Extended", "ema_50_200"), "")
            self.assertIn("1Hour", sp.untested_settings_note("golden_cross", "1Hour", "Extended", "sma_50_200"))
            # an engine-unrunnable timeframe falls back to that variant's own default
            applied = sp.apply_strategy_preset({"timeframe": "2Min", "strategy_preset_enabled": True,
                                                "strategy_preset": "golden_cross", "strategy_preset_variant": "ema_50_200"})
            self.assertEqual((applied["timeframe"], applied["market_session"]), ("1Hour", "Extended"))
            # a variant may not recommend something the engine cannot run
            sp.PRESETS["golden_cross"] = dataclasses.replace(base, variants=(dataclasses.replace(base.variants[0], supported_timeframes=("2Min",), default_timeframe="2Min"),) + base.variants[1:])
            with self.assertRaises(ValueError):
                sp._validate_catalog()
            sp.PRESETS["golden_cross"] = dataclasses.replace(base, variants=(dataclasses.replace(base.variants[0], suggested_sizing={"max_position_pct": 1}),) + base.variants[1:])
            with self.assertRaises(ValueError):
                sp._validate_catalog()
        finally:
            sp.PRESETS["golden_cross"] = base

    def test_catalog_lists_recommended_and_allowed_values_and_no_forced_timeframe(self):
        for option in [o for o in sp.strategy_preset_options() if o["kind"] == "signal"]:
            self.assertEqual(option["supported_sessions"], ["Regular"])
            self.assertEqual(option["supported_timeframes"], ["1Day"])
            self.assertIn("5Min", option["allowed_timeframes"])
            self.assertIn("1Min", option["allowed_timeframes"])
            self.assertNotIn("2Min", option["allowed_timeframes"])
            self.assertEqual(option["allowed_sessions"], ["Regular", "Extended"])
            self.assertNotIn("timeframe", option["overrides"])
            self.assertNotIn("market_session", option["overrides"])

    def test_resolve_overrides_keeps_a_supported_timeframe_when_the_strategy_does_not_force_one(self):
        preset = sp.StrategyPreset(
            id="x", family="t", label="x", summary="", entry_rule="", exit_rule="", variants=(), default_variant="v",
            supported_timeframes=("1Hour", "1Day"), default_timeframe="1Day", allowed_timeframes=("1Hour", "1Day"))
        self.assertEqual(sp.resolve_overrides(preset, {"timeframe": "1Hour"})["timeframe"], "1Hour")
        self.assertEqual(sp.resolve_overrides(preset, {"timeframe": "5Min"})["timeframe"], "1Day")

    def test_unknown_preset_is_disabled_and_legacy_sentinel_falls_back(self):
        self.assertEqual(sp.normalize_strategy_preset({"strategy_preset_enabled": True, "strategy_preset": "x"}),
                         (False, sp.DISABLED, ""))
        out = sp.apply_strategy_preset({"strategy_preset_enabled": False, "buy_mode": sp.PRESET_MODE, "sell_mode": sp.PRESET_MODE})
        self.assertEqual((out["buy_mode"], out["sell_mode"]), ("indicator", "indicator"))

    def test_overrides_are_limited_to_the_allow_list(self):
        for preset in sp.PRESETS.values():
            self.assertTrue(set(preset.overrides) <= set(sp.ALLOWED_OVERRIDE_KEYS))
        for banned in ("risk_per_trade", "max_position_pct", "trade_amount", "bot_loss_limit_pct"):
            self.assertNotIn(banned, sp.ALLOWED_OVERRIDE_KEYS)

    def test_validation_rejects_options_and_crypto(self):
        base = {"strategy_preset_enabled": True, "strategy_preset": "golden_cross"}
        self.assertEqual(sp.validate_strategy_preset(base, ticker="SPY"), [])
        self.assertTrue(sp.validate_strategy_preset({**base, "options_trading": True}))
        self.assertTrue(sp.validate_strategy_preset(base, ticker="BTCUSD", is_crypto=True))

    def test_catalog_options_are_serializable(self):
        import json
        options = sp.strategy_preset_options()
        json.dumps(options)
        ids = {o["id"] for o in options}
        self.assertEqual(ids, {"golden_cross", "supertrend_ema200", "malik_qqq", "malik_soxx", "malik_iwm", "session_rider"})
        self.assertEqual({o["id"] for o in options if o["kind"] == "allocation"}, {"malik_qqq", "malik_soxx", "malik_iwm"})
        for option in options:
            self.assertEqual(option["direction"], "long_only")
            if option["kind"] == "schedule":
                self.assertTrue(all(v["warmup_bars"] == 0 for v in option["variants"]))
            else:
                self.assertTrue(all(v["warmup_bars"] > 0 for v in option["variants"]))
        self.assertIn("Golden Cross", sp.strategy_preset_label(
            {"strategy_preset_enabled": True, "strategy_preset": "golden_cross"}))


class ExecutionWindowTests(unittest.TestCase):
    def window(self, bar_open, timeframe="1Day", **kwargs):
        return sp.next_execution_window(pd.Timestamp(bar_open), timeframe, **kwargs)

    def test_friday_daily_signal_executes_monday_open(self):
        w = self.window("2026-10-02")  # Friday
        self.assertEqual(w["kind"], "next_session_open")
        self.assertEqual(w["window_start"].astimezone(ET).strftime("%Y-%m-%d %H:%M"), "2026-10-05 09:30")

    def test_holiday_is_skipped(self):
        w = self.window("2026-11-25")  # Wed before Thanksgiving
        self.assertEqual(w["window_start"].astimezone(ET).strftime("%Y-%m-%d %H:%M"), "2026-11-27 09:30")

    def test_blackout_offset_is_added_for_session_open_fills(self):
        w = self.window("2026-10-02", blackout_minutes=5)
        self.assertEqual(w["window_start"].astimezone(ET).strftime("%H:%M"), "09:35")

    def test_dst_changes_keep_the_eastern_open(self):
        spring = self.window("2026-03-06")  # Friday before DST starts
        self.assertEqual(spring["window_start"].utcoffset().total_seconds(), -4 * 3600)
        fall = self.window("2025-10-31")  # Friday before DST ends
        self.assertEqual(fall["window_start"].utcoffset().total_seconds(), -5 * 3600)
        self.assertEqual(fall["window_start"].astimezone(ET).strftime("%Y-%m-%d %H:%M"), "2025-11-03 09:30")

    def test_early_close_day_bar_completes_at_one_pm(self):
        completes = sp.bar_completion_time(pd.Timestamp("2025-11-28 00:00", tz="America/New_York").tz_convert("UTC"), "1Day")
        self.assertEqual(completes.tz_convert(ET).strftime("%H:%M"), "13:05")

    def test_intraday_mid_session_executes_at_next_bar_and_last_bar_waits_for_next_open(self):
        mid = self.window("2026-10-02 11:30", "1Hour")
        self.assertEqual(mid["kind"], "next_bar_open")
        self.assertEqual(mid["window_start"].astimezone(ET).strftime("%H:%M"), "12:30")
        last = self.window("2026-10-02 15:30", "1Hour")
        self.assertEqual(last["kind"], "next_session_open")
        self.assertEqual(last["window_start"].astimezone(ET).strftime("%Y-%m-%d %H:%M"), "2026-10-05 09:30")

    def test_never_extended_hours(self):
        for stamp in ("2026-10-02", "2026-10-02 15:30"):
            start = self.window(stamp, "1Day" if len(stamp) == 10 else "1Hour")["window_start"].astimezone(ET)
            bounds = nyse_calendar.session_bounds(start.date())
            self.assertIsNotNone(bounds)
            self.assertTrue(bounds[0] <= start < bounds[1])


class OfficialCalendarTests(unittest.TestCase):
    """When exchange_calendars is installed it is preferred; failures fall back to the rules."""

    def fake_calendar(self, sessions):
        class Calendar:
            def is_session(self, label):
                if label == "2099-01-01":
                    raise ValueError("out of range")
                return label in sessions

            def session_open(self, label):
                return pd.Timestamp(f"{label} 14:30", tz="UTC")  # 09:30 EST

            def session_close(self, label):
                return pd.Timestamp(f"{label} {sessions[label]}", tz="UTC")

        return Calendar()

    def test_official_calendar_is_preferred_including_special_closures(self):
        from datetime import date as d
        calendar = self.fake_calendar({"2026-10-02": "21:00"})  # normal close, 16:00 EST
        with patch.object(nyse_calendar, "_calendar", return_value=calendar):
            self.assertTrue(nyse_calendar.is_trading_day(d(2026, 10, 2)))
            self.assertFalse(nyse_calendar.is_trading_day(d(2026, 10, 1)))  # a special closure the rules would miss
            self.assertIsNone(nyse_calendar.session_bounds(d(2026, 10, 1)))
            self.assertFalse(nyse_calendar.is_early_close(d(2026, 10, 2)))

    def test_early_close_comes_from_the_official_session_close(self):
        from datetime import date as d
        calendar = self.fake_calendar({"2026-10-02": "18:00"})  # 13:00 EST
        with patch.object(nyse_calendar, "_calendar", return_value=calendar):
            self.assertTrue(nyse_calendar.is_early_close(d(2026, 10, 2)))

    def test_out_of_range_dates_fall_back_to_the_rules(self):
        from datetime import date as d
        with patch.object(nyse_calendar, "_calendar", return_value=self.fake_calendar({})):
            self.assertEqual(nyse_calendar.is_trading_day(d(2099, 1, 1)), False)  # rules: New Year's Day


class PositionSizeTests(unittest.TestCase):
    base = dict(equity=10_000, price=100.0, atr=2.0, base_amount=500.0, risk_fraction=0.01,
                max_position_fraction=0.5, stop_atr_mult=3.0)

    def test_smallest_limit_decides_and_is_reported(self):
        out = sp.preset_position_size(**self.base)
        # risk qty = 10000*0.01/(3*2) = 16.67 ; amount qty = 5 ; max position = 50
        self.assertEqual(out["limit"], "amount")
        self.assertAlmostEqual(out["qty"], 5.0)
        out = sp.preset_position_size(**{**self.base, "base_amount": 50_000})
        self.assertEqual(out["limit"], "risk")
        self.assertAlmostEqual(out["qty"], 10_000 * 0.01 / 6.0)

    def test_never_exceeds_max_position_or_buying_power(self):
        out = sp.preset_position_size(**{**self.base, "base_amount": 50_000, "risk_fraction": 0.5})
        self.assertEqual(out["limit"], "max_position")
        self.assertLessEqual(out["notional"], 10_000 * 0.5 + 1e-9)
        out = sp.preset_position_size(**{**self.base, "buying_power": 120.0})
        self.assertEqual(out["limit"], "buying_power")
        self.assertLessEqual(out["notional"], 120.0)

    def test_whole_shares_and_invalid_input(self):
        out = sp.preset_position_size(**{**self.base, "base_amount": 150.0}, fractional=False)
        self.assertEqual(out["qty"], 1.0)
        out = sp.preset_position_size(**{**self.base, "base_amount": 50.0}, fractional=False)
        self.assertEqual(out["qty"], 0.0)
        self.assertEqual(sp.preset_position_size(**{**self.base, "price": 0})["qty"], 0.0)


class ScanPolicyTests(unittest.TestCase):
    ON = {"strategy_preset_enabled": True, "strategy_preset": "golden_cross", "strategy_preset_variant": "sma_50_200", "timeframe": "1Day"}

    def test_both_v1_strategies_scan_once_per_daily_candle_and_the_catalog_says_so(self):
        for option in [o for o in sp.strategy_preset_options() if o["kind"] == "signal"]:
            for variant in option["variants"]:
                self.assertEqual(variant["scan_policy"]["mode"], "per_candle")
                self.assertEqual(list(variant["scan_policy"]["timeframes"]), ["1Day"])
        for preset in ("golden_cross", "supertrend_ema200"):
            self.assertEqual(sp.effective_scan_policy({**self.ON, "strategy_preset": preset, "strategy_preset_variant": ""})["mode"], "per_candle")

    def test_the_policy_applies_only_while_enabled_and_on_a_covered_timeframe(self):
        self.assertIsNone(sp.effective_scan_policy({**self.ON, "strategy_preset_enabled": False}))
        self.assertIsNone(sp.effective_scan_policy({}))
        self.assertIsNone(sp.effective_scan_policy({**self.ON, "strategy_preset": "unknown"}))
        for timeframe in ("5Min", "15Min", "1Hour", "4Hour", ""):
            self.assertIsNone(sp.effective_scan_policy({**self.ON, "timeframe": timeframe}), timeframe)

    def test_a_variant_can_override_the_strategy_policy_and_bad_policies_are_rejected(self):
        import dataclasses
        base = sp.PRESETS["golden_cross"]
        own = dataclasses.replace(base.variants[1], scan_policy={"mode": "interval"})
        sp.PRESETS["golden_cross"] = dataclasses.replace(base, variants=(base.variants[0], own, base.variants[2]))
        try:
            sp._validate_catalog()
            self.assertIsNone(sp.effective_scan_policy({**self.ON, "strategy_preset_variant": "ema_50_200"}))
            self.assertEqual(sp.effective_scan_policy({**self.ON, "strategy_preset_variant": "sma_50_200"})["mode"], "per_candle")
            for bad in ({"mode": "every_tick"}, {"mode": "per_candle"}, {"mode": "per_candle", "timeframes": ("5Min",)},
                        {"mode": "per_candle", "timeframes": ("1Day",), "seconds": 5}):
                sp.PRESETS["golden_cross"] = dataclasses.replace(base, scan_policy=bad)
                with self.assertRaises(ValueError, msg=str(bad)):
                    sp._validate_catalog()
        finally:
            sp.PRESETS["golden_cross"] = base


if __name__ == "__main__":
    unittest.main()
