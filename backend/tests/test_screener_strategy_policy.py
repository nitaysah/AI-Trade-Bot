import time
import unittest

from screener_strategy import (
    assess_event_catalyst,
    assess_swing_technical,
    combine_strategy_qualification,
)


class ScreenerStrategyPolicyTests(unittest.TestCase):
    def setUp(self):
        self.now = 1_800_000_000.0
        self.news = {
            "initialized": True,
            "status": "current",
            "sentiment_direction": "BULLISH",
            "sentiment_pct": 78.0,
            "sentiment_confidence": 0.82,
            "latest": {
                "id": "catalyst-1",
                "title": "Company launches a material AI product",
                "source": "Company newsroom",
                "relevance": "DIRECT",
                "impact": "HIGH",
                "published_ts": self.now - 3600,
            },
        }
        self.scan = {
            "evaluation_status": "valid",
            "direction": "BULLISH",
            "price_raw": 120.0,
            "timeframe": "1Day",
            "price_history": [
                {"close": float(value), "volume": 1000.0}
                for value in list(range(80, 120)) + [120.0]
            ],
        }

    def test_recent_direct_catalyst_qualifies(self):
        result = assess_event_catalyst(self.news, now_ts=self.now)
        self.assertTrue(result["eligible"])
        self.assertEqual(result["direction"], "BULLISH")
        self.assertEqual(result["catalyst_id"], "catalyst-1")

    def test_stale_catalyst_is_blocked(self):
        stale = {**self.news, "latest": {**self.news["latest"], "published_ts": self.now - 11 * 86400}}
        result = assess_event_catalyst(stale, now_ts=self.now)
        self.assertFalse(result["eligible"])
        self.assertIn("older", result["reason"])

    def test_combined_requires_same_direction_and_both_legs(self):
        technical = {"eligible": True, "direction": "BULLISH", "reason": "Technical qualified"}
        event = {"eligible": True, "direction": "BULLISH", "reason": "Event qualified"}
        self.assertTrue(combine_strategy_qualification("combined", technical, event)["qualified"])
        self.assertFalse(combine_strategy_qualification(
            "combined",
            technical,
            {**event, "direction": "BEARISH"},
        )["qualified"])

    def test_discovery_accepts_one_leg_but_vetoes_conflict(self):
        technical = {"eligible": True, "direction": "BULLISH", "reason": "Technical qualified"}
        missing_event = {"eligible": False, "direction": "NEUTRAL", "reason": "No catalyst"}
        self.assertTrue(combine_strategy_qualification("discovery", technical, missing_event)["qualified"])
        conflicting_event = {"eligible": True, "direction": "BEARISH", "reason": "Risk event"}
        self.assertFalse(combine_strategy_qualification("discovery", technical, conflicting_event)["qualified"])

    def test_swing_technical_requires_completed_history_and_trend(self):
        result = assess_swing_technical(self.scan, {})
        self.assertTrue(result["eligible"])
        self.assertEqual(result["status"], "QUALIFIED")
        blocked = assess_swing_technical({**self.scan, "price_raw": 75.0}, {})
        self.assertFalse(blocked["eligible"])


if __name__ == "__main__":
    unittest.main()
