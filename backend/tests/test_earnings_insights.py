import unittest
from datetime import date, datetime, timedelta
from types import SimpleNamespace

import pandas as pd

import earnings_service as es


def closes(prices, start=date(2026, 1, 5)):
    days, d = [], start
    while len(days) < len(prices):
        if d.weekday() < 5:
            days.append(pd.Timestamp(d))
        d += timedelta(days=1)
    return pd.Series(prices, index=pd.DatetimeIndex(days))


class BeatRecordTests(unittest.TestCase):
    def hist(self, *pairs):  # newest first
        return [{"eps_actual": a, "eps_estimate": e} for a, e in pairs]

    def test_counts_and_streak(self):
        record = es.beat_record(self.hist((2.1, 2.0), (2.05, 2.0), (1.9, 2.0), (2.2, 2.0)))
        self.assertEqual((record["beats"], record["misses"], record["quarters"]), (3, 1, 4))
        self.assertEqual((record["streak"], record["streak_kind"]), (2, "beat"))

    def test_in_line_uses_a_small_tolerance_and_missing_rows_are_skipped(self):
        record = es.beat_record(self.hist((2.001, 2.0), (None, 2.0), (2.0, None)))
        self.assertEqual(record["in_line"], 1)
        self.assertEqual(record["quarters"], 1)

    def test_empty_history(self):
        self.assertEqual(es.beat_record([])["quarters"], 0)


class TimingTests(unittest.TestCase):
    def test_time_of_day_maps_to_before_after_or_during(self):
        make = lambda h, m=0: datetime(2026, 7, 30, h, m)
        self.assertEqual(es._timing_from_time(make(8, 0)), "BMO")
        self.assertEqual(es._timing_from_time(make(16, 0)), "AMC")
        self.assertEqual(es._timing_from_time(make(16, 30)), "AMC")
        self.assertEqual(es._timing_from_time(make(12, 0)), "DURING")
        self.assertEqual(es._timing_from_time(make(0, 0)), "UNKNOWN")  # date-only stamp
        self.assertEqual(es._timing_from_time("not a time"), "UNKNOWN")


class ReactionTests(unittest.TestCase):
    # Mon Jan 5 .. : index 0=Jan5, 1=Jan6, 2=Jan7, 3=Jan8, 4=Jan9, 5=Jan12, 6=Jan13, 7=Jan14
    PRICES = [100, 100, 100, 100, 100, 100, 110, 110]

    def moves(self, timing, event_day):
        series = closes(self.PRICES)
        return es.post_earnings_moves(series, [{"date": event_day, "timing": timing}])

    def test_after_close_uses_the_next_session(self):
        out = self.moves("AMC", date(2026, 1, 12))  # reported Mon after close; Tue Jan 13 closed +10%
        self.assertEqual(out[0]["move_pct"], 10.0)
        self.assertFalse(out[0]["approx"])

    def test_before_open_uses_the_same_session(self):
        out = self.moves("BMO", date(2026, 1, 13))  # reported Tue before the open; Tue closed +10% vs Mon
        self.assertEqual(out[0]["move_pct"], 10.0)

    def test_unknown_timing_uses_the_two_day_move_and_is_flagged(self):
        out = self.moves("UNKNOWN", date(2026, 1, 12))
        self.assertTrue(out[0]["approx"])
        self.assertEqual(out[0]["move_pct"], 10.0)

    def test_future_events_and_missing_next_session_are_skipped(self):
        self.assertEqual(self.moves("AMC", date(2026, 1, 14)), [])  # last bar: no next session yet
        future = es.post_earnings_moves(closes(self.PRICES), [{"date": date(2099, 1, 1), "timing": "BMO"}])
        self.assertEqual(future, [])

    def test_weekend_announcement_rolls_to_the_next_trading_day(self):
        out = self.moves("BMO", date(2026, 1, 10))  # Saturday -> Monday Jan 12 vs Friday: flat
        self.assertEqual(out[0]["move_pct"], 0.0)

    def test_typical_move_needs_three_reactions(self):
        self.assertIsNone(es.typical_move([{"move_pct": 5.0}, {"move_pct": -3.0}]))
        self.assertEqual(es.typical_move([{"move_pct": 5.0}, {"move_pct": -3.0}, {"move_pct": 4.0}]), 4.0)


class AnalysisTests(unittest.TestCase):
    def raw(self, **extra):
        base = {"eps_estimate": {"growth": 0.07, "low": 1.9, "average": 2.0, "high": 2.1},
                "revenue_estimate": {"growth": 0.06},
                "eps_revisions": {"up_7d": 3, "up_30d": 5, "down_7d": 0, "down_30d": 1},
                "history": [{"eps_actual": 2.1, "eps_estimate": 2.0, "surprise_pct": 5.0}] * 4,
                "reactions": [{"move_pct": 4.0}, {"move_pct": -5.0}, {"move_pct": 6.0}]}
        base.update(extra)
        return base

    def test_all_beats_and_typical_move_reach_the_analysis(self):
        out = es._build_analysis(self.raw(), 3)
        self.assertTrue(any("beat EPS estimates in all of the last 4" in b for b in out["bullish_factors"]))
        self.assertEqual(out["typical_move_pct"], 5.0)
        self.assertIn("typically moved about 5.0%", out["risk_text"])
        self.assertEqual(out["beat_record"]["beats"], 4)
        self.assertTrue(any("typical post-earnings move" in w for w in out["what_to_watch"]))
        self.assertEqual(out["risk_level"], "ELEVATED")

    def test_move_text_only_appears_when_earnings_are_near(self):
        self.assertNotIn("typically moved", es._build_analysis(self.raw(), 40)["risk_text"])

    def test_misses_are_bearish(self):
        history = [{"eps_actual": 1.8, "eps_estimate": 2.0, "surprise_pct": -10.0}] * 4
        out = es._build_analysis(self.raw(history=history, eps_revisions={}), 20)
        self.assertTrue(any("missed" in b for b in out["bearish_factors"]))

    def test_without_reaction_data_nothing_is_invented(self):
        out = es._build_analysis(self.raw(reactions=[]), 3)
        self.assertIsNone(out["typical_move_pct"])
        self.assertNotIn("typically moved", out["risk_text"])


if __name__ == "__main__":
    unittest.main()
