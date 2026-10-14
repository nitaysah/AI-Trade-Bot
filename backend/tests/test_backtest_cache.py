import json
import os
import tempfile
import unittest
from unittest.mock import MagicMock, patch

from fastapi import HTTPException

import data_manager


class CacheFolderTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = os.path.realpath(self.tmp.name)
        patcher = patch.object(data_manager, "DATA_DIR", self.root)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.addCleanup(self.tmp.cleanup)
        for name in (
            "SPY_5Min_adjsplit.csv", "SPY_5Min_adjsplit.csv.meta.json",
            "SPY_1Day_yahoo_adjsplit.csv", "SPY_1Day_yahoo_adjsplit.csv.meta.json",
            "SPY_1Hour_adjsplit.csv",                                   # csv without meta is still purged
            "SPY_5Min.csv", "SPY_5Min.csv.meta.json",                   # legacy untagged: ignored by the app, left alone
            "SPYG_5Min_adjsplit.csv", "SPYG_5Min_adjsplit.csv.meta.json",  # a different ticker sharing a prefix
            "BRK-B_1Day_adjsplit.csv",
            "SPY_info.json", "user_settings_abc.json", "botbulls_local.sqlite3",
        ):
            with open(os.path.join(self.root, name), "w") as handle:
                handle.write("x")
        with open(os.path.join(self.root, "SPY_5Min_adjsplit.csv.meta.json"), "w") as handle:
            json.dump({"data_source": "alpaca", "feed": "sip", "fetched_at": "2026-10-08T10:00:00"}, handle)

    def names(self):
        return sorted(os.listdir(self.root))

    def test_list_shows_each_cached_set_with_its_provider_and_feed(self):
        entries = data_manager.list_cached_data("spy")
        self.assertEqual([(e["timeframe"], e["requested_provider"]) for e in entries],
                         [("1Day", "yahoo"), ("1Hour", "alpaca"), ("5Min", "alpaca")])
        five = next(e for e in entries if e["timeframe"] == "5Min")
        self.assertEqual((five["data_source"], five["feed"], five["fetched_at"]), ("alpaca", "sip", "2026-10-08T10:00:00"))

    def test_purge_removes_only_that_tickers_candle_cache(self):
        result = data_manager.purge_cached_data("SPY")
        self.assertEqual(result["timeframes"], ["1Day", "1Hour", "5Min"])
        self.assertEqual(result["files_removed"], 5)
        self.assertEqual(self.names(), sorted([
            "SPY_5Min.csv", "SPY_5Min.csv.meta.json", "SPYG_5Min_adjsplit.csv", "SPYG_5Min_adjsplit.csv.meta.json",
            "BRK-B_1Day_adjsplit.csv", "SPY_info.json", "user_settings_abc.json", "botbulls_local.sqlite3",
        ]))

    def test_purging_twice_or_an_uncached_ticker_is_harmless(self):
        data_manager.purge_cached_data("SPY")
        self.assertEqual(data_manager.purge_cached_data("SPY"), {"files_removed": 0, "timeframes": []})
        self.assertEqual(data_manager.purge_cached_data("ZZZZ"), {"files_removed": 0, "timeframes": []})
        self.assertEqual(data_manager.list_cached_data("ZZZZ"), [])

    def test_tickers_with_dots_and_dashes_work_and_hostile_input_matches_nothing(self):
        self.assertEqual(data_manager.purge_cached_data("brk-b")["files_removed"], 1)
        before = self.names()
        for hostile in ("../etc", "SPY/../x", "SPY*", "", None, "A" * 40, "spy_5min"):
            self.assertEqual(data_manager.purge_cached_data(hostile)["files_removed"], 0, hostile)
        self.assertEqual(self.names(), before)

    def test_a_purged_ticker_is_downloaded_again_instead_of_served_from_cache(self):
        import pandas as pd
        frame = pd.DataFrame({"open": [1.0], "high": [1.0], "low": [1.0], "close": [1.0], "volume": [1.0]},
                             index=pd.DatetimeIndex(["2026-10-01"]))
        frame.to_csv(os.path.join(self.root, "QQQ_1Day_adjsplit.csv"))
        self.assertEqual(len(data_manager.list_cached_data("QQQ")), 1)
        data_manager.purge_cached_data("QQQ")
        self.assertEqual(data_manager.list_cached_data("QQQ"), [])
        self.assertFalse(os.path.exists(os.path.join(self.root, "QQQ_1Day_adjsplit.csv")))


class CacheEndpointTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = os.path.realpath(self.tmp.name)
        patcher = patch.object(data_manager, "DATA_DIR", self.root)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.addCleanup(self.tmp.cleanup)
        with open(os.path.join(self.root, "SPY_5Min_adjsplit.csv"), "w") as handle:
            handle.write("x")

    async def test_status_then_purge_then_status(self):
        import main
        eng = MagicMock()
        status = await main.backtest_cache_status("spy", eng)
        self.assertEqual((status["ticker"], [c["timeframe"] for c in status["cached"]]), ("SPY", ["5Min"]))
        purged = await main.backtest_cache_purge({"ticker": " spy "}, eng)
        self.assertEqual((purged["status"], purged["files_removed"], purged["timeframes"]), ("success", 1, ["5Min"]))
        self.assertEqual((await main.backtest_cache_status("SPY", eng))["cached"], [])

    async def test_invalid_tickers_are_rejected_with_400(self):
        import main
        for bad in ("", "../x", "SPY;DROP", "A" * 40):
            with self.assertRaises(HTTPException) as caught:
                await main.backtest_cache_purge({"ticker": bad}, MagicMock())
            self.assertEqual(caught.exception.status_code, 400)
            with self.assertRaises(HTTPException):
                await main.backtest_cache_status(bad, MagicMock())
        with self.assertRaises(HTTPException):
            await main.backtest_cache_purge({}, MagicMock())


if __name__ == "__main__":
    unittest.main()
