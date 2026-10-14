import unittest

import scan_phase as sp

SIGNALS = {
    "EMA Cross": {"signal": "BULLISH", "enabled": True},
    "Supertrend": {"signal": "NEUTRAL", "enabled": True},
    "SMA": {"signal": "BEARISH", "enabled": True},
    "RSI": {"signal": "BULLISH", "enabled": False},  # not used by this bot: never counted
}


class EntryWatchTests(unittest.TestCase):
    def test_flat_bot_reports_progress_and_what_is_missing(self):
        out = sp.describe({"signals": SIGNALS}, {"min_buy_signals": 3}, has_position=False)
        self.assertEqual(out["phase"], sp.PHASE_ENTRY)
        self.assertEqual(out["progress"], {"have": 1, "need": 3})
        self.assertIn("1/3 bullish", out["headline"])
        self.assertIn("on: EMA Cross", out["headline"])
        self.assertIn("neutral: Supertrend", out["headline"])
        self.assertIn("bearish: SMA", out["headline"])
        self.assertIn("2 more needed", out["headline"])
        self.assertNotIn("RSI", out["headline"])
        self.assertNotIn("ignored", out["headline"].lower())

    def test_one_signal_away_is_called_out(self):
        out = sp.describe({"signals": SIGNALS}, {"min_buy_signals": 2}, has_position=False)
        self.assertIn("1 more signal to enter", out["headline"])

    def test_all_signals_on_but_held_back_says_why(self):
        signals = {"A": {"signal": "BULLISH", "enabled": True}, "B": {"signal": "BULLISH", "enabled": True}}
        out = sp.describe({"signals": signals}, {"min_buy_signals": 2}, has_position=False,
                          held_back="Liquidity filter: spread 0.59% exceeds 0.50%")
        self.assertIn("All entry signals are on (2/2)", out["headline"])
        self.assertIn("held back: Liquidity filter", out["headline"])

    def test_bearish_signals_are_not_presented_as_a_sell_decision_when_flat(self):
        signals = {"A": {"signal": "BEARISH", "enabled": True}, "B": {"signal": "BEARISH", "enabled": True}}
        out = sp.describe({"signals": signals}, {"min_buy_signals": 2}, has_position=False)
        self.assertEqual(out["progress"]["have"], 0)
        self.assertTrue(out["headline"].startswith("Entry watch"))
        self.assertNotIn("sell", out["headline"].lower())

    def test_hybrid_reports_structure_status(self):
        base = {"signals": SIGNALS, "structure_ok": True}
        out = sp.describe(base, {"min_buy_signals": 2, "buy_mode": "hybrid_atr"}, has_position=False)
        self.assertIn("price structure confirmed", out["headline"])
        out = sp.describe({**base, "structure_ok": False}, {"min_buy_signals": 2, "buy_mode": "hybrid_atr"}, has_position=False)
        self.assertIn("not confirmed yet", out["headline"])

    def test_structure_modes_keep_their_own_reason(self):
        out = sp.describe({"signals": {}, "reason": "Price $100 has not reached the ATR entry $98.50"},
                          {"buy_mode": "atr"}, has_position=False)
        self.assertIn("atr entry", out["headline"].lower())


class ExitWatchTests(unittest.TestCase):
    def test_distances_use_the_current_price(self):
        result = {"price_raw": 100.0, "exit_signal_label": "SL/TP Exit",
                  "exit_watch_details": {"stop_loss": 98.0, "take_profit": 104.0, "trail_enabled": True}}
        out = sp.describe(result, {"sell_mode": "sltp"}, has_position=True)
        self.assertEqual(out["phase"], sp.PHASE_EXIT)
        self.assertIn("stop $98.00 (-2.0%)", out["headline"])
        self.assertIn("target $104.00 (+4.0%)", out["headline"])
        self.assertIn("trailing stop on", out["headline"])
        self.assertEqual(out["chip"], "SL/TP")

    def test_indicator_exit_shows_bearish_progress(self):
        out = sp.describe({"price_raw": 50.0, "bearish_count": 1}, {"sell_mode": "indicator", "min_sell_signals": 2}, has_position=True)
        self.assertIn("1/2 bearish exit signals", out["headline"])

    def test_no_data_still_says_something_useful(self):
        out = sp.describe({}, {}, has_position=True)
        self.assertIn("Exit watch", out["headline"])


class ReentryGateTests(unittest.TestCase):
    GATES = [
        {"key": "smart_reentry", "state": "clear"},
        {"key": "signal_reset", "state": "blocking", "detail": "2 bullish signal(s) still on"},
        {"key": "cooldown", "state": "blocking", "detail": "1h 12m left"},
        {"key": "post_stop", "state": "clear"},
    ]

    def test_gates_are_listed_in_order_and_first_blocker_labels_the_row(self):
        out = sp.describe({"signals": SIGNALS}, {}, has_position=False, gates=self.GATES)
        self.assertEqual(out["phase"], sp.PHASE_REENTRY)
        self.assertEqual(out["gate"], "cooldown")
        self.assertEqual(out["label"], "COOLDOWN")
        text = out["headline"]
        self.assertLess(text.index("Cooldown"), text.index("Signal reset"))
        self.assertLess(text.index("Signal reset"), text.index("Smart re-entry"))
        self.assertIn("Cooldown: 1h 12m left", text)
        self.assertIn("Smart re-entry ✓", text)
        self.assertTrue(text.endswith("then entry signals."))

    def test_disabled_gates_are_not_mentioned(self):
        gates = [{"key": "cooldown", "state": "off"}, {"key": "signal_reset", "state": "blocking", "detail": "1 bullish signal(s) still on"}]
        out = sp.describe({"signals": SIGNALS}, {}, has_position=False, gates=gates)
        self.assertNotIn("Cooldown", out["headline"])
        self.assertEqual(out["label"], "RESET")

    def test_first_entry_gate_is_not_called_a_re_entry(self):
        gates = [{"key": "smart_reentry", "state": "blocking", "detail": "RSI 74.2 above 70"}]
        out = sp.describe({"signals": SIGNALS}, {}, has_position=False, gates=gates, after_exit=False)
        self.assertEqual(out["phase"], sp.PHASE_GATE)
        self.assertTrue(out["headline"].startswith("Entry gate"))

    def test_clear_gates_fall_through_to_entry_watch(self):
        gates = [{"key": key, "state": "clear"} for key in sp.GATE_ORDER]
        out = sp.describe({"signals": SIGNALS}, {"min_buy_signals": 3}, has_position=False, gates=gates)
        self.assertEqual(out["phase"], sp.PHASE_ENTRY)

    def test_position_open_ignores_gates(self):
        out = sp.describe({}, {}, has_position=True, gates=self.GATES)
        self.assertEqual(out["phase"], sp.PHASE_EXIT)

    def test_duration_text(self):
        self.assertEqual(sp.duration_text(4320), "1h 12m")
        self.assertEqual(sp.duration_text(75), "1m 15s")
        self.assertEqual(sp.duration_text(9), "9s")


if __name__ == "__main__":
    unittest.main()
