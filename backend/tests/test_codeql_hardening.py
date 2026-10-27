"""Behaviour checks for the CodeQL hardening changes: same results for normal input, safe refusal otherwise."""

import hashlib
import os
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import alpaca_broker
from options_backtester import OptionsBacktester


class AlpacaAccountFingerprintTests(unittest.TestCase):
    def _connect(self, account):
        broker = alpaca_broker.AlpacaBroker()
        client = MagicMock()
        client.get_account.return_value = account
        with patch.object(alpaca_broker, "ALPACA_AVAILABLE", True), \
                patch.object(alpaca_broker, "TradingClient", return_value=client, create=True):
            return broker, broker.connect("PKTESTKEY", "test-secret", paper=True)

    def test_fingerprint_is_unchanged_for_a_normal_account(self):
        broker, ok = self._connect(SimpleNamespace(account_number="PA3XYZ123", id="uuid-1"))
        self.assertTrue(ok)
        expected = hashlib.sha256(b"PA3XYZ123").hexdigest()[:16]        # same formula as before the change
        self.assertEqual(broker._risk_account_key, f"alpaca:paper:{expected}")

    def test_account_id_is_used_when_there_is_no_account_number(self):
        broker, ok = self._connect(SimpleNamespace(account_number="", id="uuid-1"))
        self.assertTrue(ok)
        self.assertEqual(broker._risk_account_key, f"alpaca:paper:{hashlib.sha256(b'uuid-1').hexdigest()[:16]}")

    def test_an_account_without_any_identity_is_refused_instead_of_hashing_the_api_key(self):
        broker, ok = self._connect(SimpleNamespace(account_number="", id=""))
        self.assertFalse(ok)
        self.assertFalse(broker._api_key)                                # credentials cleared


class OptionsCsvPathTests(unittest.TestCase):
    CSV = "timestamp,strike_price,option_type,expiration_dte,option_price\n2026-10-01 10:00,100,CALL,7,1.25\n"

    def _loader(self, ticker):
        bt = OptionsBacktester.__new__(OptionsBacktester)
        bt.ticker = ticker
        bt.options_csv_cache = {}
        bt.options_csv_by_timestamp = {}
        bt.option_quote_capabilities = {"historical_rows": 0, "two_sided_rows": 0}
        return bt

    def test_a_normal_ticker_still_loads_its_csv(self):
        with tempfile.TemporaryDirectory() as root:
            os.makedirs(os.path.join(root, "data"))
            with open(os.path.join(root, "data", "aapl_options_chain_5m.csv"), "w") as handle:
                handle.write(self.CSV)
            cwd = os.getcwd()
            os.chdir(root)
            try:
                bt = self._loader("AAPL")
                bt._load_options_csv_cache()
            finally:
                os.chdir(cwd)
            self.assertEqual(bt.option_quote_capabilities["historical_rows"], 1)

    def test_a_ticker_cannot_reach_a_file_outside_the_data_folder(self):
        with tempfile.TemporaryDirectory() as root:
            os.makedirs(os.path.join(root, "data"))
            with open(os.path.join(root, "evil_options_chain_5m.csv"), "w") as handle:   # one level above data/
                handle.write(self.CSV)
            cwd = os.getcwd()
            os.chdir(root)
            try:
                bt = self._loader("../EVIL")                                # would have resolved to ../evil_... before
                bt._load_options_csv_cache()
            finally:
                os.chdir(cwd)
            self.assertEqual(bt.option_quote_capabilities["historical_rows"], 0)


if __name__ == "__main__":
    unittest.main()
