import unittest
from unittest.mock import patch

import fundamental_service


class _Series:
    def __init__(self, values):
        self.values = values

    def to_dict(self):
        return self.values


class _Frame:
    def __init__(self, rows):
        self.rows = rows
        self.loc = self

    def __getitem__(self, key):
        if key not in self.rows:
            raise KeyError(key)
        return _Series(self.rows[key])


class _Ticker:
    def __init__(self):
        self.income = _Frame({
            "Total Revenue": {"2025-12-31": 1200, "2024-12-31": 1000},
            "Gross Profit": {"2025-12-31": 600, "2024-12-31": 480},
            "Operating Income": {"2025-12-31": 240, "2024-12-31": 180},
            "Net Income": {"2025-12-31": 180, "2024-12-31": 120},
        })
        self.balance = _Frame({
            "Cash And Cash Equivalents": {"2025-12-31": 500},
            "Total Debt": {"2025-12-31": 250},
            "Stockholders Equity": {"2025-12-31": 1000},
            "Current Assets": {"2025-12-31": 700},
            "Current Liabilities": {"2025-12-31": 350},
        })
        self.cashflow = _Frame({
            "Operating Cash Flow": {"2025-12-31": 300},
            "Free Cash Flow": {"2025-12-31": 220},
        })

    def get_info(self):
        return {
            "longName": "Example Holdings",
            "longBusinessSummary": "A diversified operating company.",
            "sector": "Technology",
            "industry": "Software",
            "country": "US",
            "currency": "USD",
            "marketCap": 1000000000,
            "trailingPE": 20,
            "forwardPE": 18,
            "pegRatio": 1.2,
            "heldPercentInsiders": 0.1,
            "heldPercentInstitutions": 0.6,
            "shortPercentOfFloat": 0.02,
        }

    def get_income_stmt(self, freq="yearly"):
        return self.income

    def get_balance_sheet(self, freq="yearly"):
        return self.balance

    def get_cash_flow(self, freq="yearly"):
        return self.cashflow

    def get_sec_filings(self):
        return []


class FundamentalServiceTests(unittest.TestCase):
    def test_builds_explained_report_and_normalizes_percentages(self):
        with patch.object(fundamental_service.yf, "Ticker", return_value=_Ticker()):
            report = fundamental_service.build_fundamental_report("EXMP", force=True)
        self.assertEqual(report["status"], "current")
        self.assertEqual(report["assessment"], "Worth considering")
        self.assertEqual(report["score"], 100)
        self.assertEqual({c["rating"] for c in report["scorecard"]}, {"strong"})
        self.assertEqual(report["ownership"]["institutional_pct"], 60.0)
        growth = report["metrics"]["growth"][0]
        self.assertEqual(growth["value"], "20.0%")
        self.assertIn("meaning", growth)
        self.assertTrue(report["fundamental_conditions"][-1].startswith("Use the technical Entry"))
        self.assertGreater(report["next_check_at"], 0)

    def test_crypto_is_not_applicable(self):
        report = fundamental_service.build_fundamental_report("ETHUSD", force=True)
        self.assertEqual(report["status"], "not_applicable")

    def test_etf_is_not_applicable_without_calling_company_fundamentals(self):
        with patch.object(fundamental_service.yf, "Ticker", side_effect=AssertionError("ETF must not call quoteSummary")):
            report = fundamental_service.build_fundamental_report("SPY", force=True)
        self.assertEqual(report["status"], "not_applicable")
        self.assertIn("exchange-traded fund", report["summary"])

    def test_provider_failure_preserves_previous_as_stale(self):
        previous = {"report_version": 1, "ticker": "EXMP", "status": "current", "assessment": "Worth considering"}
        with patch.object(fundamental_service, "_build_report", side_effect=RuntimeError("provider down")):
            report = fundamental_service.build_fundamental_report("EXMP", previous=previous, force=True)
        self.assertEqual(report["status"], "stale")
        self.assertEqual(report["assessment"], "Worth considering")
        self.assertIn("provider down", report["error"])

    def test_detail_uses_fresh_cache_until_next_check(self):
        cached = {"report_version": fundamental_service.REPORT_VERSION, "ticker": "EXMP", "status": "current", "next_check_at": fundamental_service.time.time() + 3600}
        with patch.object(fundamental_service, "_build_report", side_effect=AssertionError("fresh cache was not used")):
            report = fundamental_service.get_fundamental_report_detail("EXMP", cached)
        self.assertEqual(report, cached)


if __name__ == "__main__":
    unittest.main()
