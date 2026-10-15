"""Session Rider backtest against a hand-computed fixture (no network: data loading is patched)."""
import os
import sys
import unittest
from datetime import datetime
from unittest.mock import patch

import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import backtester as B  # noqa: E402
import nyse_calendar as N  # noqa: E402

DAYS = ["2026-09-14", "2026-09-15", "2026-09-16", "2026-09-17", "2026-09-18", "2026-09-21"]


def make_data(step_minutes, overrides, source="alpaca", days=None):
    """Intraday bars at ``step_minutes`` (price 100 unless overridden at 'YYYY-MM-DD HH:MM') plus daily bars."""
    rows, daily = [], []
    for d in days or DAYS:
        o, c = (x.replace(tzinfo=None) for x in N.session_bounds(pd.Timestamp(d).date()))
        t = o
        day_rows = []
        while t < c:
            px = overrides.get(t.strftime("%Y-%m-%d %H:%M"), 100.0)
            day_rows.append((t, px, px, px, px, 1000))
            t += pd.Timedelta(minutes=step_minutes)
        rows += day_rows
        daily.append((pd.Timestamp(d), day_rows[0][1], 101.0, 99.0, day_rows[-1][4], 1e6))
    cols = ["timestamp", "open", "high", "low", "close", "volume"]
    intr = pd.DataFrame(rows, columns=cols).set_index("timestamp")
    day = pd.DataFrame(daily, columns=cols).set_index("timestamp")
    intr.attrs = {"data_source": source, "feed": "sip"}
    day.attrs = {"data_source": "alpaca", "feed": "sip"}

    def fake(ticker, timeframe, start, end, force_yahoo=False):
        frame = day if timeframe == "1Day" else intr
        out = frame.loc[pd.Timestamp(start):pd.Timestamp(end)].copy()
        out.attrs = dict(frame.attrs)
        out.attrs["requested_timeframe"] = timeframe
        return out
    return fake


def run(fake, settings=None, start=datetime(2026, 9, 14), end=datetime(2026, 9, 21), timeframe="1Day", **kw):
    with patch.object(B, "get_historical_data", side_effect=fake):
        bt = B.Backtester("SPY", timeframe, start, end, initial_capital=10000.0,
                          trade_amount=1000.0, strategy_preset="session_rider", session_settings=settings or {},
                          max_pos_pct=1.0, **kw)
        return bt.run()


BUYS = {"2026-09-14": 100.0, "2026-09-15": 102.0, "2026-09-16": 101.0, "2026-09-17": 103.0, "2026-09-18": 100.0}
SELLS = {"2026-09-15": 101.0, "2026-09-16": 101.0, "2026-09-17": 102.0, "2026-09-18": 103.0, "2026-09-21": 99.0}


class SessionBacktestTests(unittest.TestCase):
    def overrides(self, buy_hm, sell_hm, buys=BUYS, sells=SELLS):
        o = {f"{d} {buy_hm}": p for d, p in buys.items()}
        o.update({f"{d} {sell_hm}": p for d, p in sells.items()})
        return o

    def test_overnight_default_matches_hand_computation(self):
        res = run(make_data(5, self.overrides("15:55", "09:35")))
        self.assertNotIn("error", res)
        pairs = list(zip(BUYS.values(), SELLS.values()))
        expected_pl = [1000.0 / b * (s - b) for b, s in pairs]
        summary, info = res["summary"], res["summary"]["strategy_preset"]
        self.assertEqual(info["kind"], "schedule")
        self.assertEqual(info["mode"], "overnight")
        self.assertEqual(info["bar_timeframe"], "5Min")
        self.assertEqual(info["round_trips"], 5)  # the Sep 21 entry would sell after the end date: not taken
        trades = sorted(res["trades"], key=lambda t: t["entry_time"])
        self.assertEqual([round(t["pl"], 2) for t in trades], [round(p, 2) for p in expected_pl])
        self.assertAlmostEqual(summary["roi_pct"], round(sum(expected_pl) / 10000 * 100, 2), places=2)
        # entry at 15:55 ET (EDT = UTC-4), exit at 09:35 next trading day; Friday -> Monday
        self.assertEqual(trades[0]["entry_time"], "2026-09-14 19:55:00Z")
        self.assertEqual(trades[-1]["exit_time"], "2026-09-21 13:35:00Z")
        # cash + position = equity on the last day (flat): initial + realized
        self.assertAlmostEqual(res["equity_curve"][-1]["equity"], 10000 + sum(expected_pl), places=2)

    def test_cost_table_and_buy_hold_split(self):
        res = run(make_data(5, self.overrides("15:55", "09:35")))
        info = res["summary"]["strategy_preset"]
        cost2 = sum(1000.0 / (b * 1.0002) * (s * 0.9998 - b * 1.0002) for b, s in zip(BUYS.values(), SELLS.values()))
        self.assertAlmostEqual(info["cost_table"][0]["roi_pct"], round(cost2 / 10000 * 100, 2), places=2)
        self.assertEqual([c["bps_per_side"] for c in info["cost_table"]], [2, 5, 10])
        labels = [b["label"] for b in info["buy_hold_split"]]
        self.assertTrue(labels[1].startswith("Overnight") and labels[2].startswith("Intraday"))

    def test_minute_times_use_one_minute_bars(self):
        res = run(make_data(1, self.overrides("15:59", "09:31")), {"session_buy_time": "close-1", "session_sell_time": "open+1"})
        self.assertNotIn("error", res)
        info = res["summary"]["strategy_preset"]
        self.assertEqual(info["bar_timeframe"], "1Min")
        expected = sum(1000.0 / b * (s - b) for b, s in zip(BUYS.values(), SELLS.values()))
        self.assertAlmostEqual(res["summary"]["roi_pct"], round(expected / 10000 * 100, 2), places=2)

    def test_selected_five_minute_timeframe_controls_intraday_bars(self):
        res = run(make_data(5, self.overrides("15:55", "09:35")), timeframe="5Min")
        self.assertNotIn("error", res)
        self.assertEqual(res["summary"]["strategy_preset"]["bar_timeframe"], "5Min")

    def test_selected_one_day_timeframe_keeps_schedule_compatibility(self):
        res = run(make_data(5, self.overrides("15:55", "09:35")), timeframe="1Day")
        self.assertNotIn("error", res)
        self.assertEqual(res["summary"]["strategy_preset"]["bar_timeframe"], "5Min")

    def test_intraday_mode(self):
        o = {f"{d} 09:35": 100.0 for d in DAYS}
        o.update({f"{d} 15:55": 101.0 for d in DAYS})
        res = run(make_data(5, o), {"session_buy_time": "open+5", "session_sell_time": "close-5"})
        info = res["summary"]["strategy_preset"]
        self.assertEqual(info["mode"], "intraday")
        self.assertEqual(info["round_trips"], 6)
        self.assertAlmostEqual(res["summary"]["roi_pct"], round(6 * 10.0 / 10000 * 100, 2), places=2)
        self.assertFalse(any("day trade" in n for n in info["notes"]))

    def test_yahoo_intraday_fallback_is_refused(self):
        res = run(make_data(5, {}, source="yahoo"))
        self.assertIn("error", res)
        self.assertIn("Alpaca", res["error"])

    def test_loss_streak_pause_count(self):
        losing = {d: 99.0 for d in SELLS}
        res = run(make_data(5, self.overrides("15:55", "09:35", sells=losing)), {"session_loss_pause_after": 3})
        info = res["summary"]["strategy_preset"]
        self.assertEqual(info["would_pause_count"], 1)  # 5 losses: paused once at 3, then 2 more
        self.assertEqual(info["loss_pause_after"], 3)

    def test_skip_weekend_drops_friday_entry(self):
        res = run(make_data(5, self.overrides("15:55", "09:35")), {"session_skip_weekend": True})
        info = res["summary"]["strategy_preset"]
        self.assertEqual(info["round_trips"], 4)
        self.assertEqual(info["skipped_days"]["weekend/holiday rule"], 1)

    def test_window_ending_at_year_end_does_not_need_next_years_bars(self):
        days = ["2025-12-29", "2025-12-30", "2025-12-31"]
        res = run(make_data(5, {}, days=days), start=datetime(2025, 12, 29), end=datetime(2025, 12, 31))
        self.assertNotIn("error", res)
        self.assertEqual(res["summary"]["strategy_preset"]["round_trips"], 2)

    def test_invalid_settings_are_reported(self):
        res = run(make_data(5, {}), {"session_buy_time": "09:35", "session_sell_time": "open+5"})
        self.assertEqual(res, {"error": "Buy time and sell time must be different."})


if __name__ == "__main__":
    unittest.main()
