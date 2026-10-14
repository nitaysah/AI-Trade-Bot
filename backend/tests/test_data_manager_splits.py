import json
import os
import tempfile
import unittest
from datetime import date, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import numpy as np
import pandas as pd

import corporate_actions_service as svc
import data_manager
from corporate_actions import SplitEvent
from corporate_actions_service import SplitLookup

N = 120
SPLIT_AT = 60


def daily_index():
    return pd.date_range("2026-01-05", periods=N, freq="B")


def raw_closes():
    values = np.linspace(100.0, 160.0, N)
    values[SPLIT_AT:] = values[SPLIT_AT:] / 4.0
    return values


def alpaca_bars(closes, adjusted_symbol="TEST"):
    idx = daily_index().tz_localize("America/New_York").tz_convert("UTC")
    frame = pd.DataFrame(
        {"Open": closes, "High": closes * 1.01, "Low": closes * 0.99, "Close": closes, "Volume": 1000.0},
        index=pd.MultiIndex.from_product([[adjusted_symbol], idx], names=["symbol", "timestamp"]),
    )
    frame.columns = [c.lower() for c in frame.columns]
    return SimpleNamespace(df=frame)


def yahoo_frame(closes):
    idx = daily_index()
    return pd.DataFrame(
        {"Open": closes, "High": closes * 1.01, "Low": closes * 0.99, "Close": closes,
         "Adj Close": closes * 0.97, "Volume": 1000.0},
        index=idx,
    )


class DataManagerSplitTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        real = os.path.realpath(self.tmp.name)
        patcher = patch.object(data_manager, "DATA_DIR", real)
        patcher.start()
        self.addCleanup(patcher.stop)
        svc.clear_cache()
        self.event = SplitEvent("TEST", daily_index()[SPLIT_AT].date(), 4.0)
        self.start, self.end = datetime(2026, 1, 5), datetime(2026, 7, 1)

    def events_available(self):
        return patch.object(data_manager, "get_split_events", create=True)

    def lookup(self, events, available=True):
        return SplitLookup("ok" if available else "unavailable", "alpaca" if available else "none", events)

    def run_yahoo(self, closes, lookup, **kwargs):
        with patch("broker_factory.get_alpaca_clients", return_value=(None, None)), \
                patch("data_manager.yf.download", return_value=yahoo_frame(closes)) as download, \
                patch("corporate_actions_service.get_split_events", return_value=lookup):
            frame = data_manager.get_historical_data("TEST", "1Day", self.start, self.end, **kwargs)
        return frame, download

    def test_yahoo_fallback_is_split_only_and_corrects_raw_data(self):
        frame, download = self.run_yahoo(raw_closes(), self.lookup([self.event]))
        self.assertEqual(download.call_args.kwargs["auto_adjust"], False)
        self.assertEqual(download.call_args.kwargs["prepost"], False)  # daily bars: no extended sessions
        ratios = (frame["close"] / frame["close"].shift(1)).dropna()
        self.assertTrue(((ratios > 0.97) & (ratios < 1.03)).all())
        self.assertEqual(frame.attrs["data_source"], "yahoo")
        self.assertEqual(frame.attrs["adjustment"], "split")
        self.assertEqual(frame.attrs["data_quality"], "OK")
        # adj_close mirrors the split-only close, not Yahoo's dividend-adjusted value
        self.assertTrue(np.allclose(frame["adj_close"], frame["close"]))

    def test_already_adjusted_provider_data_is_not_adjusted_twice(self):
        adjusted = np.linspace(100.0, 160.0, N) / 4.0
        frame, _ = self.run_yahoo(adjusted, self.lookup([self.event]))
        self.assertTrue(np.allclose(frame["close"].to_numpy(), adjusted))
        self.assertEqual(frame.attrs["data_quality"], "OK")

    def test_unavailable_events_with_a_split_like_jump_is_degraded(self):
        frame, _ = self.run_yahoo(raw_closes(), self.lookup([], available=False))
        self.assertEqual(frame.attrs["data_quality"], "DEGRADED")
        self.assertFalse(frame.attrs["splits_checked"])

    def test_unavailable_events_with_calm_data_is_ok_but_unchecked(self):
        frame, _ = self.run_yahoo(np.linspace(100.0, 130.0, N), self.lookup([], available=False))
        self.assertEqual(frame.attrs["data_quality"], "OK")
        self.assertFalse(frame.attrs["splits_checked"])

    def test_alpaca_requests_are_split_adjusted_and_report_the_feed(self):
        from alpaca.data.enums import Adjustment, DataFeed
        stock_client = MagicMock()
        stock_client.get_stock_bars.return_value = alpaca_bars(np.linspace(100.0, 160.0, N) / 4.0)
        with patch("broker_factory.get_alpaca_clients", return_value=(stock_client, MagicMock())), \
                patch("corporate_actions_service.get_split_events", return_value=self.lookup([self.event])):
            frame = data_manager.get_historical_data("TEST", "1Day", self.start, self.end)
        request = stock_client.get_stock_bars.call_args.args[0]
        self.assertEqual(request.adjustment, Adjustment.SPLIT)
        self.assertEqual(request.feed, DataFeed.SIP)
        self.assertEqual((frame.attrs["data_source"], frame.attrs["feed"]), ("alpaca", "sip"))

    def test_iex_fallback_is_recorded(self):
        stock_client = MagicMock()
        stock_client.get_stock_bars.side_effect = [RuntimeError("no sip subscription"),
                                                   alpaca_bars(np.linspace(100.0, 160.0, N) / 4.0)]
        with patch("broker_factory.get_alpaca_clients", return_value=(stock_client, MagicMock())), \
                patch("corporate_actions_service.get_split_events", return_value=self.lookup([self.event])):
            frame = data_manager.get_historical_data("TEST", "1Day", self.start, self.end)
        self.assertEqual(frame.attrs["feed"], "iex")
        self.assertEqual(stock_client.get_stock_bars.call_count, 2)

    def test_second_call_uses_the_tagged_cache_and_keeps_provenance(self):
        lookup = self.lookup([self.event])
        with patch("broker_factory.get_alpaca_clients", return_value=(None, None)), \
                patch("data_manager.yf.download", return_value=yahoo_frame(raw_closes())) as download, \
                patch("corporate_actions_service.get_split_events", return_value=lookup):
            covered_end = daily_index()[-1].to_pydatetime() + timedelta(days=1)  # the cache must cover the requested end
            first = data_manager.get_historical_data("TEST", "1Day", self.start, covered_end)
            second = data_manager.get_historical_data("TEST", "1Day", self.start, covered_end)
        self.assertEqual(download.call_count, 1)
        self.assertEqual(second.attrs["adjustment"], "split")
        self.assertEqual(second.attrs["data_source"], first.attrs["data_source"])
        self.assertTrue(any(name.endswith("_adjsplit.csv") for name in os.listdir(data_manager.DATA_DIR)))

    def test_legacy_untagged_cache_is_ignored(self):
        legacy = os.path.join(data_manager.DATA_DIR, "TEST_1Day.csv")
        yahoo_frame(raw_closes()).rename(columns=str.lower).to_csv(legacy)  # unknown adjustment basis
        lookup = self.lookup([self.event])
        with patch("broker_factory.get_alpaca_clients", return_value=(None, None)), \
                patch("data_manager.yf.download", return_value=yahoo_frame(np.linspace(100.0, 160.0, N) / 4.0)) as download, \
                patch("corporate_actions_service.get_split_events", return_value=lookup):
            frame = data_manager.get_historical_data("TEST", "1Day", self.start, self.end)
        self.assertEqual(download.call_count, 1)
        self.assertAlmostEqual(float(frame["close"].iloc[0]), 25.0)

    def test_cache_fetched_before_a_split_is_refetched(self):
        lookup = self.lookup([self.event])
        with patch("broker_factory.get_alpaca_clients", return_value=(None, None)), \
                patch("data_manager.yf.download", return_value=yahoo_frame(np.linspace(100.0, 160.0, N) / 4.0)) as download, \
                patch("corporate_actions_service.get_split_events", return_value=lookup):
            covered_end = daily_index()[-1].to_pydatetime() + timedelta(days=1)
            data_manager.get_historical_data("TEST", "1Day", self.start, covered_end)
            self.assertEqual(download.call_count, 1)
            # pretend the cache was written before the ex-date
            meta_files = [f for f in os.listdir(data_manager.DATA_DIR) if f.endswith(".meta.json")]
            meta_path = os.path.join(data_manager.DATA_DIR, meta_files[0])
            meta = json.load(open(meta_path))
            meta["fetched_at"] = (datetime.combine(self.event.ex_date, datetime.min.time()) - timedelta(days=3)).isoformat()
            json.dump(meta, open(meta_path, "w"))
            data_manager.get_historical_data("TEST", "1Day", self.start, covered_end)
        self.assertEqual(download.call_count, 2)

    def test_failed_refetch_never_returns_a_stale_basis_cache(self):
        lookup = self.lookup([self.event])
        with patch("broker_factory.get_alpaca_clients", return_value=(None, None)), \
                patch("data_manager.yf.download", return_value=yahoo_frame(np.linspace(100.0, 160.0, N) / 4.0)), \
                patch("corporate_actions_service.get_split_events", return_value=lookup):
            covered_end = daily_index()[-1].to_pydatetime() + timedelta(days=1)
            data_manager.get_historical_data("TEST", "1Day", self.start, covered_end)
            meta_path = os.path.join(data_manager.DATA_DIR, [f for f in os.listdir(data_manager.DATA_DIR) if f.endswith(".meta.json")][0])
            meta = json.load(open(meta_path))
            meta["fetched_at"] = (datetime.combine(self.event.ex_date, datetime.min.time()) - timedelta(days=3)).isoformat()
            json.dump(meta, open(meta_path, "w"))
        with patch("broker_factory.get_alpaca_clients", return_value=(None, None)), \
                patch("data_manager.yf.download", side_effect=RuntimeError("yahoo down")), \
                patch("corporate_actions_service.get_split_events", return_value=lookup):
            self.assertIsNone(data_manager.get_historical_data("TEST", "1Day", self.start, covered_end))

    def test_crypto_skips_split_handling(self):
        index = pd.date_range("2026-01-01", periods=N, freq="D")
        crypto = pd.DataFrame({"Open": 1.0, "High": 2.0, "Low": 0.5, "Close": 1.5, "Adj Close": 1.5, "Volume": 1.0}, index=index)
        with patch("broker_factory.get_alpaca_clients", return_value=(None, None)), \
                patch("data_manager.yf.download", return_value=crypto), \
                patch("corporate_actions_service.get_split_events", side_effect=AssertionError("must not be called")):
            frame = data_manager.get_historical_data("BTCUSD", "1Day", self.start, self.end)
        self.assertEqual(frame.attrs["data_quality"], "OK")


class CredentialOrderTests(unittest.TestCase):
    def test_user_keys_win_then_server_keys_then_none(self):
        import broker_factory
        user = SimpleNamespace(ALPACA_API_KEY=" user-key ", ALPACA_SECRET_KEY="user-secret")
        blank = SimpleNamespace(ALPACA_API_KEY="", ALPACA_SECRET_KEY="")
        placeholder = SimpleNamespace(ALPACA_API_KEY="your_alpaca_key", ALPACA_SECRET_KEY="x")
        server = SimpleNamespace(ALPACA_API_KEY="server-key", ALPACA_SECRET_KEY="server-secret")
        no_server = SimpleNamespace(ALPACA_API_KEY="", ALPACA_SECRET_KEY="")
        with patch("user_config.get_user_config", return_value=user), patch.object(broker_factory, "config", server):
            self.assertEqual(broker_factory.get_alpaca_data_credentials(), ("user-key", "user-secret"))
        with patch("user_config.get_user_config", return_value=blank), patch.object(broker_factory, "config", server):
            self.assertEqual(broker_factory.get_alpaca_data_credentials(), ("server-key", "server-secret"))
        with patch("user_config.get_user_config", return_value=placeholder), patch.object(broker_factory, "config", server):
            self.assertEqual(broker_factory.get_alpaca_data_credentials(), ("server-key", "server-secret"))
        with patch("user_config.get_user_config", return_value=blank), patch.object(broker_factory, "config", no_server):
            self.assertEqual(broker_factory.get_alpaca_data_credentials(), (None, None))


if __name__ == "__main__":
    unittest.main()
