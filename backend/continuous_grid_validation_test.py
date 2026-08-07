import unittest

from engine import (
    _evaluate_continuous_grid,
    _grid_level_pct,
    _prepare_continuous_grid_state,
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
        "exit_grid_sell_pct": 25,
    }


class ContinuousGridValidation(unittest.TestCase):
    def test_defaults_and_reclaim_entry(self):
        settings = settings_fixture()
        self.assertEqual(
            [_grid_level_pct(settings, "entry", level) for level in range(1, 6)],
            [1.0, 2.0, 3.0, 4.0, 5.0],
        )
        self.assertEqual(
            [_grid_level_pct(settings, "exit", level) for level in range(1, 6)],
            [2.0, 4.0, 6.0, 8.0, 10.0],
        )
        armed = _evaluate_continuous_grid(
            settings, current_price=99, has_position=True, average_entry_price=100
        )
        self.assertIsNone(armed["action"])
        reclaimed = _evaluate_continuous_grid(
            settings, current_price=99.20, has_position=True, average_entry_price=100
        )
        self.assertEqual(reclaimed["action"], "BUY")
        self.assertEqual(reclaimed["level"], 1)

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

    def test_full_final_exit_clears_position_cycle(self):
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
        self.assertNotIn("entry_grid_anchor_price", settings)
        self.assertNotIn("exit_grid_anchor_price", settings)
        self.assertNotIn("exit_grid_pending_order_id", settings)


if __name__ == "__main__":
    unittest.main()
