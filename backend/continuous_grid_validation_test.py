import unittest

from engine import (
    _continuous_grid_forecast_state,
    _continuous_grid_sell_fraction,
    _evaluate_continuous_grid,
    _grid_entry_level_amount,
    _grid_effective_distance,
    _grid_level_allocation_pct,
    _grid_trailing_threshold,
    _grid_level_pct,
    _initial_continuous_grid_entry_instruction,
    _next_grid_level,
    _prepare_continuous_grid_state,
    _record_continuous_grid_fill,
    _reconcile_continuous_grid_pending,
    _set_continuous_grid_pending,
)


def settings_fixture():
    return {
        "buy_mode": "percentage",
        "sell_mode": "percentage",
        "entry_pct_style": "dip_reclaim",
        "grid_continuous_mode": True,
        "exit_grid_continuous_mode": True,
        "grid_max_levels": 5,
        "exit_grid_max_levels": 5,
        "amount": 1000,
        "exit_grid_sell_pct": 20,
    }


def atr_settings_fixture():
    return {
        "buy_mode": "atr",
        "sell_mode": "sltp",
        "atr_grid_continuous_mode": True,
        "exit_atr_grid_continuous_mode": True,
        "amount": 1000,
        **{f"grid_atr_entry_l{level}_mult": float(level * 2) for level in range(1, 6)},
        **{f"grid_atr_entry_l{level}_buy_pct": 20.0 for level in range(1, 6)},
        **{f"grid_atr_entry_l{level}_enabled": True for level in range(1, 6)},
        **{f"grid_atr_exit_l{level}_mult": float(level * 2) for level in range(1, 6)},
        **{f"grid_atr_exit_l{level}_sell_pct": 20.0 for level in range(1, 6)},
        **{f"grid_atr_exit_l{level}_enabled": True for level in range(1, 6)},
    }


class ContinuousGridValidation(unittest.TestCase):
    def test_pct_bullish_profile_frontloads_buys_and_reserves_runner(self):
        settings = settings_fixture()
        settings.update({
            "continuous_grid_adaptive_mode": True,
            "continuous_grid_adaptation_strength": 100,
            "pct_grid_regime_mode": "auto",
            "pct_grid_runner_pct": 25,
            "entry_grid_trend_score": 0.8,
            "exit_grid_trend_score": 0.8,
            **{f"grid_entry_l{i}_buy_pct": 20.0 for i in range(1, 6)},
            **{f"grid_exit_l{i}_sell_pct": 20.0 for i in range(1, 6)},
        })
        entry = [_grid_level_allocation_pct(settings, "entry", i) for i in range(1, 6)]
        exit_sizes = [_grid_level_allocation_pct(settings, "exit", i) for i in range(1, 6)]
        self.assertEqual(entry, [40.0, 25.0, 15.0, 10.0, 10.0])
        self.assertEqual(exit_sizes, [10.0, 10.0, 15.0, 20.0, 20.0])
        self.assertAlmostEqual(sum(exit_sizes), 75.0)

    def test_pct_defaults_are_recommended_when_rung_sizes_are_missing(self):
        settings = settings_fixture()
        settings.update({
            "continuous_grid_adaptive_mode": True,
            "continuous_grid_adaptation_strength": 100,
            "pct_grid_regime_mode": "auto",
            "entry_grid_trend_score": 0.8,
            "exit_grid_trend_score": 0.8,
        })
        self.assertEqual(
            [_grid_level_allocation_pct(settings, "entry", i) for i in range(1, 6)],
            [40.0, 25.0, 15.0, 10.0, 10.0],
        )
        self.assertEqual(
            [_grid_level_allocation_pct(settings, "exit", i) for i in range(1, 6)],
            [10.0, 10.0, 15.0, 20.0, 20.0],
        )

    def test_pct_runner_closes_remaining_inventory_after_trailing_pullback(self):
        settings = settings_fixture()
        settings.update({
            "continuous_grid_adaptive_mode": True,
            "continuous_grid_trailing_mode": True,
            "continuous_grid_adaptation_strength": 100,
            "pct_grid_regime_mode": "auto",
            "pct_grid_runner_pct": 25,
            "pct_grid_runner_trail_pct": 3,
            "entry_grid_trend_score": 0.8,
            "exit_grid_trend_score": 0.8,
            **{f"grid_entry_l{i}_buy_pct": 20.0 for i in range(1, 6)},
            **{f"grid_exit_l{i}_sell_pct": 20.0 for i in range(1, 6)},
        })
        _record_continuous_grid_fill(
            settings, side="buy", fill_price=100.0, current_atr=1.0,
            was_grid_order=True, had_position=False, filled_level=1,
        )
        settings["exit_grid_level"] = 4
        _record_continuous_grid_fill(
            settings, side="sell", fill_price=110.0, current_atr=1.0,
            was_grid_order=True, had_position=True, position_closed=False, filled_level=5,
        )
        self.assertTrue(settings["exit_grid_runner_active"])
        monitoring = _evaluate_continuous_grid(
            settings, current_price=108.0, current_atr=1.0,
            has_position=True, average_entry_price=100.0, position_qty=1.0,
        )
        self.assertIsNone(monitoring["action"])
        exit_decision = _evaluate_continuous_grid(
            settings, current_price=106.5, current_atr=1.0,
            has_position=True, average_entry_price=100.0, position_qty=1.0,
        )
        self.assertTrue(exit_decision["continuous_grid_runner_exit"])
        self.assertEqual(_continuous_grid_sell_fraction(settings, exit_decision, 1.0), 1.0)

    def test_smart_grid_skews_spacing_and_inventory_but_preserves_total(self):
        settings = settings_fixture()
        settings.update({
            "continuous_grid_adaptive_mode": True,
            "continuous_grid_adaptation_strength": 50,
            "continuous_grid_min_edge_pct": 0.35,
            "entry_grid_trend_score": 1.0,
            "entry_grid_volatility_ratio": 1.0,
            "exit_grid_trend_score": 1.0,
            "exit_grid_volatility_ratio": 1.0,
            **{f"grid_entry_l{i}_pct": float(i) for i in range(1, 6)},
            **{f"grid_exit_l{i}_pct": float(i) for i in range(1, 6)},
            **{f"grid_entry_l{i}_buy_pct": 20.0 for i in range(1, 6)},
            **{f"grid_exit_l{i}_sell_pct": 20.0 for i in range(1, 6)},
        })
        self.assertLess(_grid_effective_distance(settings, "entry", 2, 100.0), 2.0)
        self.assertGreater(_grid_effective_distance(settings, "exit", 2, 100.0), 2.0)
        entry_sizes = [_grid_level_allocation_pct(settings, "entry", level) for level in range(1, 6)]
        exit_sizes = [_grid_level_allocation_pct(settings, "exit", level) for level in range(1, 6)]
        self.assertGreater(entry_sizes[0], entry_sizes[-1])
        self.assertLess(exit_sizes[0], exit_sizes[-1])
        self.assertAlmostEqual(sum(entry_sizes), 100.0, places=4)
        self.assertAlmostEqual(sum(exit_sizes), 100.0, places=4)

    def test_smart_grid_minimum_edge_and_anchor_context_are_frozen(self):
        settings = settings_fixture()
        settings.update({
            "continuous_grid_adaptive_mode": True,
            "continuous_grid_adaptation_strength": 100,
            "continuous_grid_min_edge_pct": 0.50,
            **{f"grid_entry_l{i}_pct": float(i) / 10.0 for i in range(1, 6)},
            **{f"grid_exit_l{i}_pct": float(i) / 10.0 for i in range(1, 6)},
        })
        rising = [
            {"close": 100 + index, "high": 100.5 + index, "low": 99.5 + index}
            for index in range(30)
        ]
        first = _evaluate_continuous_grid(
            settings, current_price=129.0, current_atr=1.0, price_history=rising,
            has_position=True, average_entry_price=129.0, position_qty=5.0,
        )
        self.assertTrue(first["state_changed"])
        frozen_trend = settings["entry_grid_trend_score"]
        self.assertGreater(frozen_trend, 0.0)
        # L2 is the second enabled rung, so the 0.50% cycle-edge floor is 1.00%.
        self.assertGreaterEqual(_grid_effective_distance(settings, "entry", 2, 129.0), 1.0)
        falling = [
            {"close": 130 - index, "high": 130.5 - index, "low": 129.5 - index}
            for index in range(30)
        ]
        _evaluate_continuous_grid(
            settings, current_price=129.0, current_atr=4.0, price_history=falling,
            has_position=True, average_entry_price=129.0, position_qty=5.0,
        )
        self.assertEqual(settings["entry_grid_trend_score"], frozen_trend)

    def test_existing_grid_remains_static_when_smart_setting_is_missing(self):
        settings = settings_fixture()
        settings.update({
            "entry_grid_trend_score": -1.0,
            "entry_grid_volatility_ratio": 1.75,
            "grid_entry_l2_pct": 2.0,
        })
        self.assertEqual(_grid_effective_distance(settings, "entry", 2, 100.0), 2.0)
        self.assertEqual(_grid_level_allocation_pct(settings, "entry", 2), 20.0)

    def test_smart_initial_entry_freezes_context_and_pending_plan_survives(self):
        settings = settings_fixture()
        settings.update({
            "continuous_grid_adaptive_mode": True,
            "continuous_grid_adaptation_strength": 50,
            **{f"grid_entry_l{i}_buy_pct": 20.0 for i in range(1, 6)},
        })
        history = [
            {"close": 100 + index, "high": 100.5 + index, "low": 99.5 + index}
            for index in range(30)
        ]
        instruction = _initial_continuous_grid_entry_instruction(
            settings, price_history=history, current_atr=1.0,
        )
        self.assertGreater(settings["entry_grid_trend_score"], 0.0)
        self.assertGreater(instruction["allocation_pct"], 20.0)
        settings.update({
            "entry_grid_pending_order_id": "initial-order",
            "entry_grid_pending_level": instruction["level"],
        })
        _prepare_continuous_grid_state(
            settings, has_position=False, current_price=129.0, current_atr=1.0,
        )
        self.assertEqual(settings["entry_grid_pending_order_id"], "initial-order")
        self.assertIn("entry_grid_trend_score", settings)

    def test_pct_trailing_buy_rung_ratchets_after_price_rises(self):
        settings = settings_fixture()
        settings.update({
            "continuous_grid_adaptive_mode": True,
            "continuous_grid_trailing_mode": True,
            "continuous_grid_adaptation_strength": 0,
            "continuous_grid_min_edge_pct": 0.35,
            **{f"grid_entry_l{i}_pct": float(i) for i in range(1, 6)},
            **{f"grid_exit_l{i}_pct": float(i) for i in range(1, 6)},
            **{f"grid_entry_l{i}_buy_pct": 20.0 for i in range(1, 6)},
            **{f"grid_exit_l{i}_sell_pct": 20.0 for i in range(1, 6)},
        })
        _record_continuous_grid_fill(
            settings, side="buy", fill_price=775.0, current_atr=0.0,
            was_grid_order=True, had_position=False, filled_level=1,
        )
        monitoring = _evaluate_continuous_grid(
            settings, current_price=781.0, has_position=True,
            average_entry_price=775.0, position_qty=2.0,
        )
        self.assertIsNone(monitoring["action"])
        self.assertEqual(settings["entry_grid_high_water_price"], 781.0)
        forecast = _continuous_grid_forecast_state(
            settings, current_price=781.0, has_position=True,
            average_entry_price=775.0, position_qty=2.0,
        )
        self.assertAlmostEqual(forecast["entry"]["trigger_price"], 781.0 * 0.98, places=4)
        buy_l2 = _evaluate_continuous_grid(
            settings, current_price=765.3, has_position=True,
            average_entry_price=775.0, position_qty=2.0,
        )
        self.assertEqual((buy_l2["action"], buy_l2["level"]), ("BUY", 2))

    def test_pct_trailing_sell_arms_at_profit_then_exits_on_pullback(self):
        settings = settings_fixture()
        settings.update({
            "continuous_grid_adaptive_mode": True,
            "continuous_grid_trailing_mode": True,
            "continuous_grid_adaptation_strength": 0,
            "continuous_grid_min_edge_pct": 0.35,
            **{f"grid_entry_l{i}_pct": float(i) for i in range(1, 6)},
            **{f"grid_exit_l{i}_pct": float(i) for i in range(1, 6)},
            **{f"grid_entry_l{i}_buy_pct": 20.0 for i in range(1, 6)},
            **{f"grid_exit_l{i}_sell_pct": 20.0 for i in range(1, 6)},
        })
        _record_continuous_grid_fill(
            settings, side="buy", fill_price=775.0, current_atr=0.0,
            was_grid_order=True, had_position=False, filled_level=1,
        )
        armed = _evaluate_continuous_grid(
            settings, current_price=782.75, has_position=True,
            average_entry_price=775.0, position_qty=2.0,
        )
        self.assertIsNone(armed["action"])
        self.assertEqual(settings["exit_grid_armed_level"], 1)
        self.assertGreater(armed.get("state_changed", False), 0)
        pulled_back = _evaluate_continuous_grid(
            settings, current_price=778.7, has_position=True,
            average_entry_price=775.0, position_qty=2.0,
        )
        self.assertEqual((pulled_back["action"], pulled_back["level"]), ("SELL", 1))
        self.assertTrue(pulled_back["trailing_enabled"])

    def test_atr_trailing_buy_and_sell_use_high_water_mark(self):
        settings = atr_settings_fixture()
        settings.update({
            "continuous_grid_adaptive_mode": True,
            "continuous_grid_trailing_mode": True,
            "continuous_grid_adaptation_strength": 0,
            "continuous_grid_min_edge_pct": 0.35,
        })
        _record_continuous_grid_fill(
            settings, side="buy", fill_price=100.0, current_atr=2.0,
            was_grid_order=True, had_position=False, filled_level=1,
        )
        _evaluate_continuous_grid(
            settings, current_price=106.0, current_atr=2.0,
            has_position=True, average_entry_price=100.0, position_qty=2.0,
        )
        self.assertEqual(settings["entry_grid_high_water_price"], 106.0)
        settings["grid_flat_reentry_armed"] = True
        buy_l2 = _evaluate_continuous_grid(
            settings, current_price=97.9, current_atr=2.0,
            has_position=False, average_entry_price=0.0, position_qty=0.0,
        )
        self.assertEqual((buy_l2["action"], buy_l2["level"]), ("BUY", 2))

        settings = atr_settings_fixture()
        settings.update({
            "continuous_grid_adaptive_mode": True,
            "continuous_grid_trailing_mode": True,
            "continuous_grid_adaptation_strength": 0,
            "continuous_grid_min_edge_pct": 0.35,
        })
        _record_continuous_grid_fill(
            settings, side="buy", fill_price=100.0, current_atr=2.0,
            was_grid_order=True, had_position=False, filled_level=1,
        )
        _evaluate_continuous_grid(
            settings, current_price=104.0, current_atr=2.0,
            has_position=True, average_entry_price=100.0, position_qty=2.0,
        )
        self.assertIsNone(_evaluate_continuous_grid(
            settings, current_price=103.0, current_atr=2.0,
            has_position=True, average_entry_price=100.0, position_qty=2.0,
        )["action"])
        sell = _evaluate_continuous_grid(
            settings, current_price=101.8, current_atr=2.0,
            has_position=True, average_entry_price=100.0, position_qty=2.0,
        )
        self.assertEqual((sell["action"], sell["level"]), ("SELL", 1))

    def test_atr_grid_defaults_to_first_twenty_percent_tranche(self):
        settings = atr_settings_fixture()
        instruction = _initial_continuous_grid_entry_instruction(settings)
        self.assertEqual(instruction["grid_kind"], "atr")
        self.assertEqual(instruction["level"], 1)
        self.assertEqual(instruction["level_pct"], 2.0)
        self.assertEqual(instruction["level_amount"], 200.0)

    def test_atr_grid_rerates_levels_from_bounded_live_atr_and_catches_crossed_levels(self):
        settings = atr_settings_fixture()
        settings.update({
            "continuous_grid_adaptive_mode": True,
            "continuous_grid_trailing_mode": True,
        })
        _record_continuous_grid_fill(
            settings, side="buy", fill_price=100.0, current_atr=2.0,
            was_grid_order=True, had_position=False, filled_level=1,
        )
        self.assertEqual(settings["entry_grid_anchor_atr"], 2.0)
        forecast = _continuous_grid_forecast_state(
            settings, current_price=95.0, current_atr=5.0, has_position=True,
            average_entry_price=100.0, position_qty=2.0,
        )
        # Smart trailing keeps the original ATR as the anchor context but
        # re-rates the active rung from bounded live ATR (2 * 1.5 = 3).
        self.assertEqual(forecast["entry"]["trigger_atr"], 3.0)
        self.assertEqual(forecast["entry"]["trigger_price"], 88.0)
        # A lower live ATR tightens the same L2 rung point-by-point.
        tighter = _continuous_grid_forecast_state(
            settings, current_price=95.0, current_atr=1.0, has_position=True,
            average_entry_price=100.0, position_qty=2.0,
        )
        self.assertEqual(tighter["entry"]["trigger_atr"], 1.5)
        self.assertEqual(tighter["entry"]["trigger_price"], 94.0)
        # Crossed levels are still combined into one order using the live
        # trigger ATR, rather than issuing duplicate orders per scan.
        decision = _evaluate_continuous_grid(
            settings, current_price=69.9, current_atr=5.0, has_position=True,
            average_entry_price=100.0, position_qty=2.0,
        )
        self.assertEqual(decision["action"], "BUY")
        self.assertEqual(decision["from_level"], 2)
        self.assertEqual(decision["level"], 5)
        self.assertEqual(decision["level_amount"], 800.0)

    def test_atr_exit_trailing_uses_bounded_live_atr_when_volatility_contracts(self):
        settings = atr_settings_fixture()
        settings.update({
            "continuous_grid_adaptive_mode": True,
            "continuous_grid_trailing_mode": True,
            "exit_grid_anchor_atr": 2.0,
        })
        wide = _grid_trailing_threshold(
            settings, "exit", 5, peak_price=120.0, anchor=100.0, current_atr=3.0,
        )
        tight = _grid_trailing_threshold(
            settings, "exit", 5, peak_price=120.0, anchor=100.0, current_atr=1.5,
        )
        # Live ATR is bounded to 1.5x and 0.75x of the anchor respectively;
        # a contracting ATR therefore tightens the trailing threshold.
        self.assertEqual(wide, 105.0)
        self.assertEqual(tight, 112.5)

    def test_atr_sell_reanchors_buy_cycle_with_fill_atr(self):
        settings = atr_settings_fixture()
        settings.update({"entry_grid_level": 2, "exit_grid_level": 0, "grid_cycle_active": True})
        _record_continuous_grid_fill(
            settings, side="sell", fill_price=108.0, current_atr=3.0,
            was_grid_order=True, had_position=True, filled_level=1,
        )
        self.assertEqual(settings["entry_grid_anchor_price"], 108.0)
        self.assertEqual(settings["entry_grid_anchor_atr"], 3.0)
        self.assertEqual(settings["entry_grid_level"], 1)

    def test_initial_signal_uses_first_grid_tranche_not_full_allocation(self):
        settings = settings_fixture()
        settings.update({
            **{f"grid_entry_l{i}_pct": float(i) for i in range(1, 6)},
            **{f"grid_entry_l{i}_buy_pct": 20.0 for i in range(1, 6)},
        })
        instruction = _initial_continuous_grid_entry_instruction(settings)
        self.assertEqual(instruction["level"], 1)
        self.assertEqual(instruction["allocation_pct"], 20.0)
        self.assertEqual(instruction["level_amount"], 200.0)

        _record_continuous_grid_fill(
            settings,
            side="buy",
            fill_price=100.0,
            was_grid_order=True,
            had_position=False,
            filled_level=instruction["level"],
        )
        self.assertEqual(settings["entry_grid_level"], 1)
        self.assertEqual(settings["entry_grid_anchor_price"], 100.0)

        remaining = sum(
            _grid_entry_level_amount(settings, level)
            for level in range(2, 6)
        )
        self.assertEqual(instruction["level_amount"] + remaining, 1000.0)

    def test_initial_grid_respects_first_non_contiguous_enabled_level(self):
        settings = settings_fixture()
        settings.update({
            **{f"grid_entry_l{i}_enabled": i in {2, 5} for i in range(1, 6)},
            "grid_entry_l2_buy_pct": 30.0,
            "grid_entry_l5_buy_pct": 70.0,
        })
        instruction = _initial_continuous_grid_entry_instruction(settings)
        self.assertEqual(instruction["level"], 2)
        self.assertEqual(instruction["level_amount"], 300.0)
        _record_continuous_grid_fill(
            settings,
            side="buy",
            fill_price=50.0,
            was_grid_order=True,
            had_position=False,
            filled_level=2,
        )
        self.assertEqual(settings["entry_grid_level"], 2)
        self.assertEqual(_next_grid_level(settings, "entry", 2), 5)

    def test_fixed_percentage_mode_keeps_normal_entry_sizing(self):
        settings = settings_fixture()
        settings["grid_continuous_mode"] = False
        settings["exit_grid_continuous_mode"] = False
        self.assertEqual(_initial_continuous_grid_entry_instruction(settings), {})

    def test_forecast_reports_next_dynamic_buy_and_sell_rungs(self):
        settings = settings_fixture()
        settings.update({
            **{f"grid_entry_l{i}_pct": float(i) for i in range(1, 6)},
            **{f"grid_exit_l{i}_pct": float(i) for i in range(1, 6)},
            **{f"grid_entry_l{i}_buy_pct": 20.0 for i in range(1, 6)},
            **{f"grid_exit_l{i}_sell_pct": 20.0 for i in range(1, 6)},
            "entry_grid_anchor_price": 105.0,
            "entry_grid_level": 1,
            "exit_grid_anchor_price": 100.0,
            "exit_grid_anchor_qty": 10.0,
            "exit_grid_level": 2,
        })
        forecast = _continuous_grid_forecast_state(
            settings,
            current_price=101.0,
            has_position=True,
            average_entry_price=100.0,
            position_qty=8.0,
        )
        self.assertTrue(forecast["adaptive_cycle"])
        self.assertEqual(forecast["entry"]["next_level"], 2)
        self.assertAlmostEqual(forecast["entry"]["trigger_price"], 102.9)
        self.assertEqual(forecast["entry"]["allocation_amount"], 200.0)
        self.assertEqual(forecast["exit"]["next_level"], 3)
        self.assertAlmostEqual(forecast["exit"]["trigger_price"], 103.0)
        self.assertEqual(forecast["exit"]["allocation_pct"], 20.0)

        monitoring = _evaluate_continuous_grid(
            settings,
            current_price=102.95,
            has_position=True,
            average_entry_price=100.0,
            position_qty=8.0,
        )
        self.assertIsNone(monitoring["action"])
        self.assertIn("Next Buy L2", monitoring["reason"])
        self.assertIn("Next Sell L3", monitoring["reason"])

    def test_forecast_respects_non_contiguous_enabled_levels(self):
        settings = settings_fixture()
        settings.update({
            **{f"grid_entry_l{i}_pct": float(i) for i in range(1, 6)},
            **{f"grid_exit_l{i}_pct": float(i) for i in range(1, 6)},
            **{f"grid_entry_l{i}_enabled": i in {1, 4} for i in range(1, 6)},
            **{f"grid_exit_l{i}_enabled": i in {2, 5} for i in range(1, 6)},
            "entry_grid_level": 1,
            "exit_grid_level": 2,
        })
        forecast = _continuous_grid_forecast_state(
            settings,
            current_price=100.0,
            has_position=True,
            average_entry_price=100.0,
        )
        self.assertEqual(forecast["entry"]["next_level"], 4)
        self.assertEqual(forecast["entry"]["enabled_count"], 2)
        self.assertEqual(forecast["exit"]["next_level"], 5)
        self.assertEqual(forecast["exit"]["enabled_count"], 2)

    def test_forecast_marks_pending_and_flat_exit_states(self):
        settings = settings_fixture()
        settings.update({
            "entry_grid_pending_order_id": "order-1",
            "entry_grid_pending_level": 2,
        })
        forecast = _continuous_grid_forecast_state(
            settings,
            current_price=99.0,
            has_position=False,
        )
        self.assertEqual(forecast["entry"]["status"], "order_pending")
        self.assertEqual(forecast["entry"]["pending_level"], 2)
        self.assertEqual(forecast["entry"]["from_level"], 1)
        self.assertEqual(forecast["entry"]["allocation_levels"], [1, 2])
        self.assertAlmostEqual(forecast["entry"]["trigger_price"], 97.02)
        self.assertEqual(forecast["entry"]["allocation_pct"], 40.0)
        self.assertEqual(forecast["entry"]["allocation_amount"], 400.0)
        self.assertEqual(forecast["exit"]["status"], "waiting_position")

        settings.pop("entry_grid_pending_order_id")
        settings.pop("entry_grid_pending_level")
        waiting = _continuous_grid_forecast_state(
            settings,
            current_price=99.0,
            has_position=False,
        )
        self.assertEqual(waiting["entry"]["status"], "waiting_initial_entry")

    def test_five_level_rise_drop_rebound_cycle(self):
        settings = settings_fixture()
        settings.update({
            **{f"grid_entry_l{i}_pct": float(i) for i in range(1, 6)},
            **{f"grid_exit_l{i}_pct": float(i) for i in range(1, 6)},
            **{f"grid_entry_l{i}_buy_pct": 20.0 for i in range(1, 6)},
            **{f"grid_exit_l{i}_sell_pct": 20.0 for i in range(1, 6)},
        })
        _record_continuous_grid_fill(
            settings,
            side="buy",
            fill_price=100,
            was_grid_order=False,
            had_position=False,
        )

        # A fast 5% rise between scans catches up L1-L5 in one protected
        # final-exit order, then preserves a flat buy-back cycle at $105.
        decision = _evaluate_continuous_grid(
            settings,
            current_price=105,
            has_position=True,
            average_entry_price=100,
        )
        self.assertEqual((decision["from_level"], decision["level"]), (1, 5))
        self.assertTrue(decision["final_level"])
        _record_continuous_grid_fill(
            settings,
            side="sell",
            fill_price=105,
            was_grid_order=True,
            had_position=True,
            position_closed=True,
            continue_cycle=True,
            filled_level=5,
        )
        self.assertTrue(settings["grid_flat_reentry_armed"])
        self.assertEqual(settings["entry_grid_anchor_price"], 105)

        # A sudden decline through L1-L3 combines three $200 allocations into
        # one $600 buy rather than waiting three scans and missing a reversal.
        decision = _evaluate_continuous_grid(
            settings,
            current_price=101.8,
            has_position=False,
            average_entry_price=0,
        )
        self.assertEqual((decision["side"], decision["from_level"], decision["level"]), ("entry", 1, 3))
        self.assertEqual(decision["level_amount"], 600)
        _record_continuous_grid_fill(
            settings,
            side="buy",
            fill_price=101.8,
            was_grid_order=True,
            had_position=False,
            filled_level=3,
        )
        self.assertEqual(settings["entry_grid_level"], 3)

        # A rebound just above 2% catches up the first two 20% sell rungs.
        rebound = _evaluate_continuous_grid(
            settings,
            current_price=103.9,
            has_position=True,
            average_entry_price=101.8,
        )
        self.assertEqual((rebound["side"], rebound["from_level"], rebound["level"]), ("exit", 1, 2))
        self.assertAlmostEqual(
            _continuous_grid_sell_fraction(
                settings,
                {
                    "continuous_grid_side": "exit",
                    "continuous_grid_from_level": 1,
                    "continuous_grid_level": 2,
                },
            ),
            0.40,
        )

    def test_level_specific_entry_buy_pcts_and_exit_sizes(self):
        settings = settings_fixture()
        settings.update({
            "grid_entry_l1_buy_pct": 10,
            "grid_entry_l2_buy_pct": 20,
            "grid_entry_l3_buy_pct": 30,
            "grid_exit_l1_sell_pct": 10,
            "grid_exit_l2_sell_pct": 20,
        })
        self.assertEqual(_grid_entry_level_amount(settings, 1), 100)
        self.assertEqual(_grid_entry_level_amount(settings, 2), 200)
        self.assertEqual(_grid_entry_level_amount(settings, 3), 300)
        self.assertEqual(_grid_entry_level_amount(settings, 4), 200)

        settings.update({"allocation_mode": "compound", "compound_amount": 1500})
        self.assertEqual(_grid_entry_level_amount(settings, 2), 300)
        self.assertAlmostEqual(
            _continuous_grid_sell_fraction(
                settings,
                {"continuous_grid_side": "exit", "continuous_grid_level": 1},
            ),
            0.10,
        )
        self.assertAlmostEqual(
            _continuous_grid_sell_fraction(
                settings,
                {"continuous_grid_side": "exit", "continuous_grid_level": 2},
            ),
            0.20,
        )
        self.assertAlmostEqual(
            _continuous_grid_sell_fraction(
                settings,
                {
                    "continuous_grid_side": "exit",
                    "continuous_grid_from_level": 1,
                    "continuous_grid_level": 2,
                },
            ),
            0.30,
        )
        self.assertEqual(
            _continuous_grid_sell_fraction(
                settings,
                {
                    "continuous_grid_side": "exit",
                    "continuous_grid_level": 3,
                    "continuous_grid_final_level": True,
                },
            ),
            0.20,
        )

    def test_defaults_and_adaptive_entry_touch(self):
        settings = settings_fixture()
        self.assertEqual(
            [_grid_level_pct(settings, "entry", level) for level in range(1, 6)],
            [1.0, 2.0, 3.0, 4.0, 5.0],
        )
        self.assertEqual(
            [_grid_level_pct(settings, "exit", level) for level in range(1, 6)],
            [1.0, 2.0, 3.0, 4.0, 5.0],
        )
        touched = _evaluate_continuous_grid(
            settings, current_price=99, has_position=True, average_entry_price=100
        )
        self.assertEqual(touched["action"], "BUY")
        self.assertEqual(touched["level"], 1)

    def test_non_contiguous_enabled_levels_and_position_baseline_sizing(self):
        settings = settings_fixture()
        settings.update({
            **{f"grid_entry_l{i}_enabled": i in {2, 4} for i in range(1, 6)},
            **{f"grid_exit_l{i}_enabled": i in {2, 5} for i in range(1, 6)},
            "entry_grid_anchor_price": 100,
            "entry_grid_level": 0,
            "exit_grid_anchor_price": 100,
            "exit_grid_anchor_qty": 10,
            "exit_grid_level": 0,
            "grid_cycle_active": True,
            "grid_flat_reentry_armed": False,
        })

        buy = _evaluate_continuous_grid(
            settings,
            current_price=96,
            has_position=True,
            average_entry_price=100,
            position_qty=10,
        )
        self.assertEqual((buy["from_level"], buy["level"]), (2, 4))
        self.assertEqual(buy["level_amount"], 400)

        first_sell = _evaluate_continuous_grid(
            settings,
            current_price=102,
            has_position=True,
            average_entry_price=100,
            position_qty=10,
        )
        self.assertEqual((first_sell["from_level"], first_sell["level"]), (2, 2))
        self.assertAlmostEqual(
            _continuous_grid_sell_fraction(settings, {
                "continuous_grid_side": "exit",
                "continuous_grid_from_level": 2,
                "continuous_grid_level": 2,
            }, current_qty=10),
            0.20,
        )
        settings["exit_grid_level"] = 2
        second_sell = _evaluate_continuous_grid(
            settings,
            current_price=105,
            has_position=True,
            average_entry_price=100,
            position_qty=8,
        )
        self.assertEqual((second_sell["from_level"], second_sell["level"]), (5, 5))
        self.assertAlmostEqual(
            _continuous_grid_sell_fraction(settings, {
                "continuous_grid_side": "exit",
                "continuous_grid_from_level": 5,
                "continuous_grid_level": 5,
            }, current_qty=8),
            0.25,
        )

    def test_entry_only_legacy_grid_keeps_reclaim_confirmation(self):
        settings = settings_fixture()
        settings["exit_grid_continuous_mode"] = False
        armed = _evaluate_continuous_grid(
            settings, current_price=99, has_position=True, average_entry_price=100
        )
        self.assertIsNone(armed["action"])
        reclaimed = _evaluate_continuous_grid(
            settings, current_price=99.20, has_position=True, average_entry_price=100
        )
        self.assertEqual(reclaimed["action"], "BUY")

    def test_open_entry_order_does_not_advance(self):
        settings = settings_fixture()
        _prepare_continuous_grid_state(
            settings, has_position=True, current_price=100, average_entry_price=100
        )
        _set_continuous_grid_pending(
            settings,
            side="entry",
            order_id="buy-1",
            level=1,
            before_qty=10,
            fill_price=99,
        )
        changed = _reconcile_continuous_grid_pending(
            settings,
            current_qty=10,
            average_entry_price=100,
            current_price=99,
            open_order_ids={"buy-1"},
        )
        self.assertFalse(changed)
        self.assertEqual(settings["entry_grid_level"], 0)

    def test_canceled_entry_retries_same_level(self):
        settings = settings_fixture()
        _prepare_continuous_grid_state(
            settings, has_position=True, current_price=100, average_entry_price=100
        )
        _set_continuous_grid_pending(
            settings,
            side="entry",
            order_id="buy-canceled",
            level=1,
            before_qty=10,
            fill_price=99,
        )
        changed = _reconcile_continuous_grid_pending(
            settings,
            current_qty=10,
            average_entry_price=100,
            current_price=99.2,
            open_order_ids=set(),
        )
        self.assertTrue(changed)
        self.assertEqual(settings["entry_grid_level"], 0)
        self.assertEqual(settings["entry_grid_pending_missing_scans"], 1)
        _reconcile_continuous_grid_pending(
            settings,
            current_qty=10,
            average_entry_price=100,
            current_price=99.2,
            open_order_ids=set(),
        )
        self.assertNotIn("entry_grid_pending_order_id", settings)

    def test_filled_entry_advances_after_quantity_confirmation(self):
        settings = settings_fixture()
        _prepare_continuous_grid_state(
            settings, has_position=True, current_price=100, average_entry_price=100
        )
        _set_continuous_grid_pending(
            settings,
            side="entry",
            order_id="buy-filled",
            level=1,
            before_qty=10,
            fill_price=99,
        )
        _reconcile_continuous_grid_pending(
            settings,
            current_qty=12,
            average_entry_price=99.8,
            current_price=99.2,
            open_order_ids=set(),
        )
        self.assertEqual(settings["entry_grid_level"], 1)
        self.assertNotIn("entry_grid_pending_order_id", settings)

    def test_filled_initial_entry_marks_first_rung_after_broker_confirmation(self):
        settings = settings_fixture()
        _set_continuous_grid_pending(
            settings,
            side="entry",
            order_id="initial-buy-filled",
            level=1,
            before_qty=0,
            fill_price=100,
        )
        _reconcile_continuous_grid_pending(
            settings,
            current_qty=2,
            average_entry_price=100,
            current_price=100,
            open_order_ids=set(),
        )
        self.assertEqual(settings["entry_grid_level"], 1)
        self.assertEqual(settings["entry_grid_anchor_price"], 100)
        self.assertTrue(settings["grid_cycle_active"])
        self.assertNotIn("entry_grid_pending_order_id", settings)

    def test_filled_scale_out_advances_after_quantity_confirmation(self):
        settings = settings_fixture()
        _prepare_continuous_grid_state(
            settings, has_position=True, current_price=100, average_entry_price=100
        )
        settings["entry_grid_level"] = 1
        _set_continuous_grid_pending(
            settings,
            side="exit",
            order_id="sell-filled",
            level=1,
            before_qty=10,
            fill_price=102,
        )
        _reconcile_continuous_grid_pending(
            settings,
            current_qty=7.5,
            average_entry_price=100,
            current_price=102,
            open_order_ids=set(),
        )
        self.assertEqual(settings["exit_grid_level"], 1)
        self.assertEqual(settings["entry_grid_level"], 0)

    def test_partial_final_exit_retries_final_rung(self):
        settings = settings_fixture()
        _prepare_continuous_grid_state(
            settings, has_position=True, current_price=100, average_entry_price=100
        )
        settings["entry_grid_level"] = 2
        settings["exit_grid_level"] = 4
        _set_continuous_grid_pending(
            settings,
            side="exit",
            order_id="final-partial",
            level=5,
            before_qty=10,
            fill_price=110,
            final_level=True,
        )
        _reconcile_continuous_grid_pending(
            settings,
            current_qty=3,
            average_entry_price=100,
            current_price=110,
            open_order_ids=set(),
        )
        self.assertEqual(settings["exit_grid_level"], 4)
        self.assertEqual(settings["entry_grid_level"], 1)

    def test_full_final_exit_arms_flat_adaptive_reentry(self):
        settings = settings_fixture()
        _prepare_continuous_grid_state(
            settings, has_position=True, current_price=100, average_entry_price=100
        )
        settings["exit_grid_level"] = 4
        _set_continuous_grid_pending(
            settings,
            side="exit",
            order_id="final-filled",
            level=5,
            before_qty=10,
            fill_price=110,
            final_level=True,
        )
        _reconcile_continuous_grid_pending(
            settings,
            current_qty=0,
            average_entry_price=0,
            current_price=110,
            open_order_ids=set(),
        )
        self.assertEqual(settings["entry_grid_anchor_price"], 110)
        self.assertTrue(settings["grid_flat_reentry_armed"])
        self.assertEqual(settings["entry_grid_level"], 0)
        self.assertNotIn("exit_grid_anchor_price", settings)
        self.assertNotIn("exit_grid_pending_order_id", settings)

        buyback = _evaluate_continuous_grid(
            settings, current_price=108.8, has_position=False, average_entry_price=0
        )
        self.assertEqual(buyback["action"], "BUY")
        self.assertEqual(buyback["level"], 1)

    def test_sell_reanchors_and_reopens_buy_capacity(self):
        settings = settings_fixture()
        settings.update({
            "entry_grid_anchor_price": 105,
            "entry_grid_level": 2,
            "exit_grid_anchor_price": 102,
            "exit_grid_level": 0,
            "grid_cycle_active": True,
            "grid_flat_reentry_armed": False,
            "grid_entry_l2_pct": 2,
        })
        _record_continuous_grid_fill(
            settings,
            side="sell",
            fill_price=104,
            was_grid_order=True,
            had_position=True,
        )
        self.assertEqual(settings["entry_grid_level"], 1)
        self.assertEqual(settings["entry_grid_anchor_price"], 104)
        buyback = _evaluate_continuous_grid(
            settings, current_price=101.9, has_position=True, average_entry_price=102
        )
        self.assertEqual(buyback["action"], "BUY")
        self.assertEqual(buyback["level"], 2)

    def test_non_grid_full_exit_ends_cycle(self):
        settings = settings_fixture()
        _record_continuous_grid_fill(
            settings,
            side="sell",
            fill_price=95,
            was_grid_order=False,
            had_position=True,
            position_closed=True,
            continue_cycle=False,
        )
        self.assertNotIn("grid_cycle_active", settings)
        self.assertNotIn("grid_flat_reentry_armed", settings)
        self.assertNotIn("entry_grid_anchor_price", settings)


if __name__ == "__main__":
    unittest.main()
