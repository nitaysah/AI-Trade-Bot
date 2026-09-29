import unittest

import ai_indicator


class NewsSummaryDirectionTests(unittest.TestCase):
    def test_bearish_summary_drops_bullish_evidence(self):
        summary = ai_indicator.canonicalize_sentiment_summary(
            "Positive article evidence: Headline emphasizes strong performance, giving a bullish slant. "
            "Negative article evidence: Major legal loss directly affecting Apple, strong negative impact.",
            "BEARISH",
            18.0,
            0.78,
        )

        self.assertNotIn("Positive article evidence:", summary)
        self.assertIn("Negative article evidence: Major legal loss directly affecting Apple", summary)
        self.assertIn("Aggregate headline tone is bearish at 18/100", summary)
        self.assertIn("78% classifier confidence", summary)

    def test_bullish_summary_drops_bearish_evidence(self):
        summary = ai_indicator.canonicalize_sentiment_summary(
            "Positive article evidence: Strong product demand supports Apple. "
            "Negative article evidence: A legal dispute creates downside risk for Apple.",
            "BULLISH",
            82.0,
            0.74,
        )

        self.assertNotIn("Negative article evidence:", summary)
        self.assertIn("Positive article evidence: Strong product demand supports Apple", summary)
        self.assertIn("Aggregate headline tone is bullish at 82/100", summary)


if __name__ == "__main__":
    unittest.main()
