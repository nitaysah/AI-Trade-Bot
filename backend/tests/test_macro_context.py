import unittest

import pandas as pd

import macro_context as mc
import macro_service as ms


def closes(values, start="2026-01-01"):
    return pd.Series(values, index=pd.date_range(start, periods=len(values), freq="B"))


class LevelTests(unittest.TestCase):
    def test_vix_bands(self):
        self.assertEqual(mc.vix_level(12.0)["label"], "Calm")
        self.assertEqual(mc.vix_level(17.0)["label"], "Normal")
        self.assertEqual(mc.vix_level(25.0)["label"], "Elevated")
        self.assertEqual(mc.vix_level(45.0)["label"], "Stress")
        self.assertIsNone(mc.vix_level(None))

    def test_curve_shapes(self):
        self.assertEqual(mc.curve_level(-0.2)["label"], "Inverted")
        self.assertEqual(mc.curve_level(0.2)["label"], "Flat")
        self.assertEqual(mc.curve_level(1.2)["label"], "Normal")

    def test_range_label(self):
        self.assertEqual(mc.range_label(95.0), "near its 1-year high")
        self.assertEqual(mc.range_label(4.0), "near its 1-year low")
        self.assertIn("55%", mc.range_label(55.0))


class WhyItMattersTests(unittest.TestCase):
    def test_known_releases_get_an_explanation_and_unknown_get_none(self):
        self.assertIn("Inflation", mc.why_it_matters("Core CPI m/m"))
        self.assertIn("Jobs", mc.why_it_matters("Non-Farm Employment Change"))
        self.assertIn("Consumer spending", mc.why_it_matters("Retail Sales m/m"))
        self.assertIn("commentary", mc.why_it_matters("Fed Chairman Warsh Speaks").lower())
        self.assertEqual(mc.why_it_matters("Totally Unknown Release"), "")

    def test_specific_titles_are_not_swallowed_by_generic_ones(self):
        self.assertIn("Wholesale", mc.why_it_matters("PPI m/m"))
        self.assertIn("Weekly layoffs", mc.why_it_matters("Unemployment Claims"))


class BackdropTests(unittest.TestCase):
    def test_points_name_their_numbers_and_tone_follows_volatility(self):
        indicators = {
            "^VIX": {"current_value": 32.0, "level": mc.vix_level(32.0)},
            "^TNX": {"current_value": 4.5, "week_change_bp": 12.0},
            "DX-Y.NYB": {"week_change_pct": 1.2},
            "CURVE": {"current_value": -0.3, "level": mc.curve_level(-0.3)},
        }
        out = mc.backdrop(indicators)
        self.assertEqual(out["tone"], "bad")
        text = " ".join(out["points"])
        self.assertIn("VIX 32.0", text)
        self.assertIn("rising", text)
        self.assertIn("firmer", text)
        self.assertIn("inverted", text)

    def test_missing_data_is_skipped_not_invented(self):
        self.assertEqual(mc.backdrop({})["points"], [])


class ServiceSummaryTests(unittest.TestCase):
    def test_yield_moves_are_in_basis_points_not_percent_of_a_percent(self):
        data = closes([4.00, 4.02, 4.03, 4.01, 4.04, 4.06, 4.10])
        out = ms._summarise("^TNX", ms.MACRO_TICKERS["^TNX"], data)
        self.assertEqual(out["change_display"], "+4.0 bp")
        self.assertAlmostEqual(out["week_change_bp"], 8.0, places=1)
        self.assertNotIn("week_change_pct", out)

    def test_index_moves_are_percent_and_carry_range_and_sparkline(self):
        data = closes([100.0 + i for i in range(40)])
        out = ms._summarise("^GSPC", ms.MACRO_TICKERS["^GSPC"], data)
        self.assertEqual(out["change_display"], f"{(139 - 138) / 138 * 100:+.2f}%")
        self.assertEqual(out["range_position_pct"], 100.0)
        self.assertEqual(len(out["sparkline"]), 30)
        self.assertEqual(out["direction"], "up")

    def test_vix_gets_a_level(self):
        out = ms._summarise("^VIX", ms.MACRO_TICKERS["^VIX"], closes([14.0, 13.5, 13.0, 12.5, 12.0, 11.5, 11.0]))
        self.assertEqual(out["level"]["label"], "Calm")

    def test_yield_curve_is_the_ten_year_minus_three_month(self):
        series = {"^TNX": closes([4.5, 4.4, 4.3, 4.2, 4.1, 4.0, 3.9]), "^IRX": closes([4.0, 4.0, 4.0, 4.0, 4.0, 4.0, 4.0])}
        out = ms._yield_curve(series)
        self.assertAlmostEqual(out["current_value"], -0.1, places=3)
        self.assertEqual(out["level"]["label"], "Inverted")
        self.assertEqual(out["ticker"], "10Y-3M")
        self.assertIsNone(ms._yield_curve({"^TNX": closes([4.0, 4.1])}))

    def test_labels_no_longer_call_the_t_bill_the_fed_rate(self):
        self.assertNotIn("Fed Rate", ms.MACRO_TICKERS["^IRX"]["name"])
        self.assertIn("T-Bill", ms.MACRO_TICKERS["^IRX"]["name"])


if __name__ == "__main__":
    unittest.main()


class ParallelMacroFetchTests(unittest.TestCase):
    def test_fetch_keeps_ticker_order_and_isolates_failures(self):
        import macro_service

        def fake_fetch_one(symbol, meta):
            if symbol == "GC=F":
                return symbol, {"error": "boom", "ticker": symbol}, None
            return symbol, {"ticker": symbol}, None

        with unittest.mock.patch.object(macro_service, "_fetch_one", side_effect=fake_fetch_one):
            result = macro_service._fetch_all_macro()
        tickers = [k for k in result if k != "CURVE"]
        self.assertEqual(tickers, list(macro_service.MACRO_TICKERS))
        self.assertEqual(result["GC=F"]["error"], "boom")
        self.assertNotIn("error", result["^VIX"])
