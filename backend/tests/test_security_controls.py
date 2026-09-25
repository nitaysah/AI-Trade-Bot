import asyncio
import unittest
from unittest.mock import patch

from fastapi import HTTPException

import main
from alpaca_broker import AlpacaBroker
from webull_broker import _safe_error_type


class SecurityControlTests(unittest.TestCase):
    def test_risk_management_rejects_false_like_numeric_bypass(self):
        self.assertTrue(main._normalize_risk_management_setting("true"))
        self.assertTrue(main._normalize_risk_management_setting(1))
        with self.assertRaises(HTTPException):
            main._normalize_risk_management_setting(2)
        with self.assertRaises(HTTPException):
            main._normalize_risk_management_setting("unexpected")

    def test_symbol_search_rate_limit_is_tenant_scoped_and_bounded(self):
        main._symbol_search_rate_windows.clear()
        with patch.object(main, "_SYMBOL_SEARCH_MAX_REQUESTS", 2):
            main._enforce_symbol_search_rate_limit("security-test-user")
            main._enforce_symbol_search_rate_limit("security-test-user")
            with self.assertRaises(HTTPException):
                main._enforce_symbol_search_rate_limit("security-test-user")
            main._enforce_symbol_search_rate_limit("other-security-test-user")
        main._symbol_search_rate_windows.clear()

    def test_paper_writes_remain_allowed_but_unarmed_live_writes_do_not(self):
        broker = AlpacaBroker()
        broker.simulation_mode = False
        broker.paper_mode = True
        self.assertTrue(broker._live_write_allowed())
        broker.paper_mode = False
        self.assertFalse(broker._live_write_allowed())
        broker.live_trading_armed = True
        self.assertTrue(broker._live_write_allowed())

    def test_provider_error_diagnostics_do_not_echo_exception_text(self):
        error = ValueError("authorization=super-secret-token")
        self.assertEqual(_safe_error_type(error), "ValueError")
        self.assertNotIn("super-secret-token", _safe_error_type(error))

    def test_blocking_provider_work_runs_through_bounded_adapter(self):
        result = asyncio.run(
            main._run_bounded_provider_call(
                "security-provider-user",
                "security-test-provider",
                lambda: {"status": "ok"},
                timeout=1.0,
            )
        )
        self.assertEqual(result, {"status": "ok"})
        main._analysis_rate_windows.pop("security-provider-user", None)
        main._analysis_user_semaphores.pop("security-provider-user", None)
        main._analysis_user_last_seen.pop("security-provider-user", None)


if __name__ == "__main__":
    unittest.main()
