import asyncio
import os
import sys
import unittest

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import trader  # noqa: E402
from screener_service import ScreenerUserState  # noqa: E402
from screener_strategy import assess_strategy_preset_technical  # noqa: E402

DAY = 86400
SETTINGS = {"strategy_preset_enabled": True, "strategy_preset": "malik_qqq", "strategy_preset_variant": "long_only",
            "allocation_bull_etf": "TQQQ"}


def daily(values, forming=None):
    idx = pd.bdate_range(end="2026-09-30", periods=len(values))
    df = pd.DataFrame({"open": values, "high": values, "low": values, "close": values, "volume": 1e6}, index=idx)
    if forming is not None:
        df.loc[pd.Timestamp("2099-01-02")] = [forming] * 4 + [1e6]  # a bar that has not completed
    return df


class AllocationInfoTests(unittest.TestCase):
    def test_confirmed_regime_uses_completed_closes_and_targets_the_chosen_etf(self):
        closes = list(np.linspace(100, 200, 300))
        info = trader._allocation_preset_info("QQQ", {"df": daily(closes)}, SETTINGS)
        self.assertEqual(info["status"], "READY")
        self.assertEqual(info["regime"], "BULL_RIDER")
        self.assertEqual(info["targets"], [{"symbol": "TQQQ", "pct": 100}])
        self.assertEqual(info["alert_direction"], "BULLISH")
        self.assertIsNone(info["provisional"])

    def test_provisional_regime_from_the_forming_bar_never_changes_the_confirmed_one(self):
        closes = list(np.linspace(100, 200, 300))
        info = trader._allocation_preset_info("QQQ", {"df": daily(closes, forming=120.0), "price_raw": 120.0}, SETTINGS)
        self.assertEqual(info["regime"], "BULL_RIDER")
        # 120 is far below both averages after a sharp drop: the live view shows a bear regime
        self.assertEqual(info["provisional"]["regime"], "BEAR_CRASH")
        self.assertEqual(info["provisional"]["target_text"], "cash")

    def test_short_history_and_degraded_data_wait(self):
        self.assertEqual(trader._allocation_preset_info("QQQ", {"df": daily(list(range(100, 200)))}, SETTINGS)["status"], "WAITING_DATA")
        self.assertEqual(trader._allocation_preset_info("QQQ", {"df": daily(list(np.linspace(100, 200, 300))), "data_quality": "DEGRADED"},
                                                        SETTINGS)["status"], "DEGRADED")

    def test_decision_is_hold_only(self):
        d = trader._allocation_preset_decision("QQQ", {"df": daily(list(np.linspace(100, 200, 300)))}, SETTINGS)
        self.assertEqual((d["action"], d["evaluation_status"]), ("HOLD", "valid"))
        tech = assess_strategy_preset_technical({"evaluation_status": "valid", "strategy_preset": d["strategy_preset"]})
        self.assertTrue(tech["eligible"])


class AllocationAlertTests(unittest.TestCase):
    def setUp(self):
        try:
            asyncio.get_event_loop()
        except RuntimeError:
            asyncio.set_event_loop(asyncio.new_event_loop())
        self.state = ScreenerUserState("alloc-alerts", None)
        self.state.profiles["QQQ"] = {"source": "screener", "enabled": True, **SETTINGS, "alert_bullish_enabled": True,
                                      "alert_bearish_enabled": True, "alert_cooldown_minutes": 60}

    def commit(self, regime, direction, day, target="100% TQQQ"):
        ts = 1_790_000_000 + day * DAY
        ev = {"evaluation_status": "valid", "direction": direction, "price_raw": 100.0, "reason": regime, "signals": {},
              "candle_meta": {"candle_key": f"1DAY:{ts}", "candle_open_ts": ts, "candle_close_ts": ts + DAY},
              "strategy_preset": {"kind": "allocation", "status": "READY", "alert_direction": direction, "regime": regime,
                                  "regime_label": regime.title(), "target_text": target, "label": "Malik QQQ",
                                  "signal_age_bars": None}}
        return self.state.commit_evaluation("QQQ", ev, now_ts=ts + 60)

    def test_first_regime_is_silent_then_every_confirmed_change_alerts_once(self):
        self.assertIsNone(self.commit("BULL_RIDER", "BULLISH", 0))
        self.assertIsNone(self.commit("BULL_RIDER", "BULLISH", 1))
        trim = self.commit("DECEL_TRIM", "BULLISH", 2, "30% TQQQ")  # same direction, new regime
        self.assertIsNotNone(trim)
        self.assertIn("target 30% TQQQ", trim["title"])
        self.assertNotIn("BUY", trim["title"])
        self.assertIsNone(self.commit("DECEL_TRIM", "BULLISH", 3, "30% TQQQ"))
        cash = self.commit("CASH", "BEARISH", 4, "cash")
        self.assertIsNotNone(cash)
        back = self.commit("BULL_RIDER", "BULLISH", 5)  # within the cooldown of nothing: a new state still alerts
        self.assertIsNotNone(back)
        self.assertEqual(len(self.state.notifications), 3)


if __name__ == "__main__":
    unittest.main()
