import unittest
from types import SimpleNamespace

import pandas as pd

import fundamental_service as fs
import research_metrics as rm


def series(*values, start=2025):
    return [{"period": f"{start - i}-12-31", "value": v} for i, v in enumerate(values)]


class GrowthMathTests(unittest.TestCase):
    def test_cagr_and_percent_change(self):
        self.assertAlmostEqual(rm.cagr(series(133.1, 121.0, 110.0, 100.0), 3), 10.0, places=6)
        self.assertAlmostEqual(rm.pct_change(series(110.0, 100.0)), 10.0)

    def test_growth_from_a_negative_or_zero_base_is_not_reported(self):
        self.assertIsNone(rm.pct_change(series(10.0, -5.0)))
        self.assertIsNone(rm.pct_change(series(10.0, 0.0)))
        self.assertIsNone(rm.cagr(series(10.0, 5.0, 4.0, -1.0), 3))

    def test_margin_change_points_matches_by_period(self):
        change = rm.margin_change_points(series(30.0, 20.0), series(100.0, 100.0))
        self.assertAlmostEqual(change, 10.0)
        self.assertIsNone(rm.margin_change_points(series(30.0), series(100.0)))


class PiotroskiTests(unittest.TestCase):
    def healthy(self):
        return dict(net_income=series(120, 100), operating_cash=series(150, 110), assets=series(1000, 1000), debt=series(100, 150),
                    current_assets=series(500, 400), current_liabilities=series(250, 250), shares=series(100, 101),
                    gross_profit=series(450, 400), revenue=series(1000, 950))

    def test_all_nine_pass_for_an_improving_company(self):
        result = rm.piotroski(**self.healthy())
        self.assertEqual((result["score"], result["available"]), (9, 9))
        self.assertTrue(result["reliable"])

    def test_missing_inputs_shrink_the_denominator_instead_of_failing(self):
        data = self.healthy()
        data["shares"] = []
        data["debt"] = []
        result = rm.piotroski(**data)
        self.assertEqual(result["available"], 7)
        self.assertEqual(result["score"], 7)

    def test_weak_company_scores_low(self):
        weak = dict(net_income=series(-10, 5), operating_cash=series(-20, 5), assets=series(1000, 800), debt=series(400, 200),
                    current_assets=series(200, 300), current_liabilities=series(300, 250), shares=series(120, 100),
                    gross_profit=series(100, 200), revenue=series(900, 1000))
        self.assertEqual(rm.piotroski(**weak)["score"], 0)


class ScorecardTests(unittest.TestCase):
    def test_ratings_follow_the_stated_thresholds(self):
        self.assertEqual(rm.rate_growth(15.0, 12.0, 20.0)["rating"], rm.STRONG)
        self.assertEqual(rm.rate_growth(15.0, None, -5.0)["rating"], rm.OK)  # sales up but profit down
        self.assertEqual(rm.rate_growth(-3.0, None, None)["rating"], rm.WEAK)
        self.assertEqual(rm.rate_growth(None, None, None)["rating"], rm.UNKNOWN)
        self.assertEqual(rm.rate_profitability(-5.0, -8.0, 1.0, None)["rating"], rm.WEAK)
        self.assertEqual(rm.rate_profitability(25.0, 30.0, 0.5, 40.0)["rating"], rm.STRONG)
        self.assertEqual(rm.rate_cash_quality(-5.0, None, -100.0)["rating"], rm.WEAK)
        self.assertEqual(rm.rate_cash_quality(20.0, 1.1, 1000.0)["rating"], rm.STRONG)
        self.assertEqual(rm.rate_cash_quality(12.0, 0.4, 1000.0)["rating"], rm.OK)

    def test_strength_flags_negative_equity_leverage_and_thin_coverage(self):
        self.assertEqual(rm.rate_strength(None, True, -1e9, 8.0, 1.0)["rating"], rm.WEAK)
        self.assertEqual(rm.rate_strength(2.0, False, None, None, None)["rating"], rm.WEAK)
        self.assertEqual(rm.rate_strength(0.3, False, 1e9, 20.0, 1.5)["rating"], rm.STRONG)
        self.assertEqual(rm.rate_strength(1.0, False, -1e9, 5.0, 1.0)["rating"], rm.OK)
        self.assertIn("not covered", " ".join(rm.rate_strength(0.3, False, 1e9, -2.0, 1.5)["reasons"]))

    def test_valuation_uses_peg_pe_and_cash_yield(self):
        self.assertEqual(rm.rate_valuation(35.0, 39.0, 2.7, 2.0, 6.0)["rating"], rm.WEAK)
        self.assertEqual(rm.rate_valuation(12.0, 14.0, 1.0, 7.0, 8.0)["rating"], rm.STRONG)
        self.assertEqual(rm.rate_valuation(22.0, 25.0, 1.6, 3.0, 10.0)["rating"], rm.OK)
        self.assertEqual(rm.rate_valuation(None, None, None, None, None)["rating"], rm.UNKNOWN)

    def test_overall_needs_enough_evidence_and_maps_to_the_existing_labels(self):
        cat = lambda key, rating: {"key": key, "label": key, "rating": rating, "reasons": []}
        thin = [cat("growth", rm.STRONG), cat("cash", rm.UNKNOWN), cat("strength", rm.UNKNOWN), cat("valuation", rm.UNKNOWN), cat("profitability", rm.UNKNOWN)]
        self.assertEqual(rm.overall(thin)["assessment"], "Insufficient evidence")
        good = [cat(k, rm.STRONG) for k in ("growth", "profitability", "cash", "strength")] + [cat("valuation", rm.OK)]
        self.assertEqual(rm.overall(good)["assessment"], "Worth considering")
        pricey = [cat(k, rm.STRONG) for k in ("growth", "profitability", "cash", "strength")] + [cat("valuation", rm.WEAK)]
        self.assertEqual(rm.overall(pricey)["assessment"], "Wait for a better valuation")
        poor = [cat("growth", rm.OK), cat("profitability", rm.WEAK), cat("cash", rm.WEAK), cat("strength", rm.WEAK), cat("valuation", rm.OK)]
        self.assertEqual(rm.overall(poor)["assessment"], "Wait for improvement")
        mixed = [cat("growth", rm.OK), cat("profitability", rm.OK), cat("cash", rm.OK), cat("strength", rm.OK), cat("valuation", rm.OK)]
        self.assertEqual(rm.overall(mixed)["assessment"], "Mixed evidence")

    def test_takeaways_lead_with_strengths_then_concerns(self):
        cats = [rm._category("growth", "Growth", rm.OK, ["a"]), rm._category("cash", "Cash", rm.STRONG, ["b"]), rm._category("strength", "Strength", rm.WEAK, ["c"])]
        lines = rm.takeaways(cats, "x")
        self.assertTrue(lines[0].startswith("Cash is a strength"))
        self.assertTrue(lines[1].startswith("Strength is a concern"))


class StatementLabelTests(unittest.TestCase):
    def frame(self, labels):
        return pd.DataFrame({pd.Timestamp("2025-12-31"): [110.0] * len(labels), pd.Timestamp("2024-12-31"): [100.0] * len(labels)}, index=labels)

    def test_row_finds_both_spaced_and_camel_case_labels(self):
        for label in ("Total Revenue", "TotalRevenue", "total_revenue"):
            rows = fs._row(self.frame([label]), ("Total Revenue",))
            self.assertEqual([r["value"] for r in rows], [110.0, 100.0], label)
            self.assertEqual(rows[0]["period"], "2025-12-31")

    def test_row_returns_nothing_for_missing_or_broken_frames(self):
        self.assertEqual(fs._row(None, ("X",)), [])
        self.assertEqual(fs._row(self.frame(["Other"]), ("Total Revenue",)), [])

    def test_alias_order_is_respected(self):
        frame = self.frame(["NetIncome", "NetIncomeCommonStockholders"])
        frame.loc["NetIncomeCommonStockholders"] = [1.0, 1.0]
        self.assertEqual(fs._row(frame, ("Net Income", "Net Income Common Stockholders"))[0]["value"], 110.0)


class DividendScaleTests(unittest.TestCase):
    def test_small_percent_yield_is_not_multiplied_by_100(self):
        # Yahoo: dividendYield 0.32 means 0.32 percent; payoutRatio 0.12 means 12 percent.
        info = {"dividendYield": 0.32, "payoutRatio": 0.1204, "marketCap": 1e12, "currentPrice": 100.0}
        income = pd.DataFrame({pd.Timestamp("2025-12-31"): [1000.0, 300.0], pd.Timestamp("2024-12-31"): [900.0, 250.0]}, index=["TotalRevenue", "NetIncome"])
        stub = SimpleNamespace(get_info=lambda: info, get_income_stmt=lambda freq="yearly": income,
                               get_balance_sheet=lambda freq="yearly": None, get_cash_flow=lambda freq="yearly": None,
                               get_sec_filings=lambda: [])
        original = fs.yf.Ticker
        fs.yf.Ticker = lambda symbol: stub
        try:
            report = fs._build_report("TEST")
        finally:
            fs.yf.Ticker = original
        self.assertEqual(report["valuation"]["dividend_yield"], 0.32)
        self.assertAlmostEqual(report["valuation"]["payout_ratio"], 12.04, places=2)
        self.assertEqual(report["report_version"], fs.REPORT_VERSION)
        self.assertIn("scorecard", report)
        self.assertEqual({c["key"] for c in report["scorecard"]}, {"growth", "profitability", "cash", "strength", "valuation"})


if __name__ == "__main__":
    unittest.main()
