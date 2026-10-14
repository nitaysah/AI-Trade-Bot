import asyncio
import unittest
from unittest.mock import patch

import screener_service
from screener_service import ScreenerUserState, evaluate_screener_profile
from screener_strategy import assess_strategy_preset_technical, combine_strategy_qualification

DAY = 86400


def preset_info(direction, age, status="READY", label="Golden Cross"):
    return {"status": status, "alert_direction": direction, "signal_age_bars": age, "label": label,
            "reason": f"{label}: {direction}"}


def evaluation(direction, candle_ts, age, status="valid", preset_status="READY"):
    return {
        "evaluation_status": status, "direction": direction, "price_raw": 100.0, "reason": direction,
        "signals": {}, "candle_meta": {"candle_key": f"1DAY:{candle_ts}", "candle_open_ts": candle_ts,
                                       "candle_close_ts": candle_ts + DAY},
        "strategy_preset": preset_info(direction, age, preset_status),
    }


class TechnicalLegTests(unittest.TestCase):
    def test_ready_directional_state_is_eligible_without_an_indicator_recount(self):
        for direction in ("BULLISH", "BEARISH"):
            result = assess_strategy_preset_technical({"evaluation_status": "valid", "price_raw": 100.0,
                                                       "strategy_preset": preset_info(direction, 0)})
            self.assertTrue(result["eligible"])
            self.assertEqual((result["direction"], result["status"]), (direction, "QUALIFIED"))

    def test_not_ready_or_invalid_evaluations_are_never_a_neutral_pass(self):
        for scan in (
            {"evaluation_status": "valid", "strategy_preset": preset_info("BULLISH", 0, status="WAITING_DATA")},
            {"evaluation_status": "valid", "strategy_preset": preset_info("BULLISH", 0, status="DEGRADED")},
            {"evaluation_status": "waiting", "strategy_preset": preset_info("BULLISH", 0)},
            {"evaluation_status": "valid", "strategy_preset": preset_info("NEUTRAL", None)},
            {},
        ):
            result = assess_strategy_preset_technical(scan)
            self.assertFalse(result["eligible"], scan)
            self.assertEqual(result["direction"], "NEUTRAL")

    def test_combined_policy_accepts_the_preset_leg_in_technical_mode(self):
        technical = assess_strategy_preset_technical({"evaluation_status": "valid", "strategy_preset": preset_info("BULLISH", 0)})
        outcome = combine_strategy_qualification("technical", technical, {"eligible": False, "direction": "NEUTRAL"})
        self.assertEqual(outcome["direction"], "BULLISH")


class AlertTests(unittest.TestCase):
    def setUp(self):
        try:
            asyncio.get_event_loop()
        except RuntimeError:
            asyncio.set_event_loop(asyncio.new_event_loop())
        self.state = ScreenerUserState("preset-alerts", None)
        self.state.profiles["SPY"] = {"source": "screener", "enabled": True, "strategy_preset_enabled": True,
                                      "strategy_preset": "golden_cross", "alert_bullish_enabled": True,
                                      "alert_bearish_enabled": True, "alert_cooldown_minutes": 0}

    def commit(self, direction, day, age, now=None):
        return self.state.commit_evaluation("SPY", evaluation(direction, 1_790_000_000 + day * DAY, age),
                                            now_ts=now or 1_790_000_000 + day * DAY)

    def test_first_evaluation_of_an_old_trend_is_recorded_silently(self):
        self.assertIsNone(self.commit("BULLISH", 0, age=40))
        self.assertEqual(self.state.evaluation_state["SPY"]["last_direction"], "BULLISH")
        self.assertEqual(self.state.notifications, [])
        # the same old trend on later candles is still not announced
        self.assertIsNone(self.commit("BULLISH", 1, age=41))

    def test_a_signal_that_just_happened_is_announced_on_the_first_evaluation(self):
        alert = self.commit("BULLISH", 0, age=0)
        self.assertIsNotNone(alert)
        self.assertEqual(alert["title"], "SPY BUY signal: Golden Cross")

    def test_exit_after_a_silent_baseline_sends_a_sell_alert_once_per_candle(self):
        self.commit("BULLISH", 0, age=40)
        alert = self.commit("BEARISH", 1, age=0)
        self.assertIsNotNone(alert)
        self.assertEqual(alert["title"], "SPY SELL (exit) signal: Golden Cross")
        self.assertEqual(alert["direction"], "BEARISH")
        self.assertIsNone(self.commit("BEARISH", 1, age=0))      # repeated scan of the same candle
        self.assertIsNone(self.commit("BEARISH", 2, age=1))      # later candle, same state: no duplicate
        self.assertEqual(len(self.state.notifications), 1)

    def test_a_missed_scan_still_delivers_the_exit_because_the_state_is_published(self):
        self.commit("BULLISH", 0, age=40)
        self.assertIsNone(self.commit("BULLISH", 1, age=41))
        # candles 2 and 3 were never scanned; the next scan still reports the bearish state
        alert = self.commit("BEARISH", 4, age=2)
        self.assertIsNotNone(alert)

    def test_bearish_toggle_suppresses_the_sell_alert_but_not_the_bullish_one(self):
        self.state.profiles["SPY"]["alert_bearish_enabled"] = False
        self.commit("BULLISH", 0, age=0)
        self.assertIsNone(self.commit("BEARISH", 1, age=0))
        self.assertEqual([n["direction"] for n in self.state.notifications], ["BULLISH"])

    def test_waiting_evaluations_create_no_alert_and_keep_the_last_state(self):
        self.commit("BULLISH", 0, age=0)
        waiting = evaluation("NEUTRAL", 1_790_000_000 + DAY, None, status="waiting", preset_status="WAITING_DATA")
        self.assertIsNone(self.state.commit_evaluation("SPY", waiting, now_ts=1_790_000_000 + DAY))
        self.assertEqual(self.state.evaluation_state["SPY"]["last_direction"], "BULLISH")


class EvaluateProfileTests(unittest.TestCase):
    def evaluate(self, result):
        settings = {"symbol": "SPY", "timeframe": "1Day", "strategy_preset_enabled": True, "strategy_preset": "golden_cross",
                    "buy_mode": "strategy_preset", "sell_mode": "strategy_preset"}
        with patch("trader.evaluate_trade", return_value=result):
            return evaluate_screener_profile("preset-screener", settings)

    def test_direction_is_the_published_strategy_state_not_the_position_action(self):
        result = self.evaluate({
            "evaluation_status": "valid", "action": "HOLD", "price_raw": 100.0, "atr": 2.0,
            "candle_meta": {"candle_key": "1DAY:1790000000"}, "strategy_preset": preset_info("BEARISH", 0),
        })
        self.assertEqual(result["direction"], "BEARISH")
        self.assertEqual(result["atr_levels"], {})  # no indicator-style ATR levels for a strategy

    def test_not_ready_or_missing_candle_key_is_waiting_not_valid(self):
        result = self.evaluate({
            "evaluation_status": "waiting", "action": "HOLD", "candle_meta": {}, "strategy_preset": preset_info("NEUTRAL", None, "WAITING_DATA"),
        })
        self.assertEqual(result["evaluation_status"], "waiting")


if __name__ == "__main__":
    unittest.main()
