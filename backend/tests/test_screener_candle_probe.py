import time
import unittest
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import Mock, patch

import indicators
import main
import pandas as pd
import pytz
from screener_service import ScreenerUserState, normalize_screener_symbol
from webull_broker import WebullBroker


class _BarsProvider:
    def __init__(self, bars):
        self.bars = bars
        self.calls = []

    def get_bars(self, *args, **kwargs):
        self.calls.append((args, kwargs))
        return self.bars


class ScreenerCandleProbeTests(unittest.TestCase):
    def _closed_bars(self):
        now = int(time.time())
        return [
            {"time": now - 3600, "open": 100, "high": 101, "low": 99, "close": 100},
            {"time": now - 2700, "open": 100, "high": 102, "low": 99, "close": 101},
            {"time": now - 1800, "open": 101, "high": 103, "low": 100, "close": 102},
        ]

    def test_probe_uses_three_bars_and_returns_latest_closed_key(self):
        bars = self._closed_bars()
        provider = _BarsProvider(bars)
        with patch.object(indicators, "_get_wb_data_provider", return_value=provider):
            payload = indicators.get_latest_completed_candle_metadata(
                "AAPL", "15Min", "regular"
            )

        self.assertEqual(payload["candle_status"], "closed")
        self.assertEqual(payload["candle_key"], f"15MIN:{bars[-1]['time']}")
        self.assertEqual(len(provider.calls), 1)
        args, kwargs = provider.calls[0]
        self.assertEqual(args, ("AAPL",))
        self.assertEqual(kwargs["timespan"], "m15")
        self.assertEqual(kwargs["count"], 3)
        self.assertEqual(kwargs["trading_sessions"], "RTH")

    def test_probe_unwraps_provider_result_and_uses_extended_crypto_session(self):
        provider = _BarsProvider([{"result": self._closed_bars()}])
        with patch.object(indicators, "_get_wb_data_provider", return_value=provider):
            payload = indicators.get_latest_completed_candle_metadata(
                "ETHUSD", "30Min", "regular"
            )

        self.assertEqual(payload["candle_status"], "closed")
        self.assertTrue(payload["candle_key"].startswith("30MIN:"))
        self.assertEqual(provider.calls[0][1]["timespan"], "m30")
        self.assertEqual(provider.calls[0][1]["trading_sessions"], "RTH,PRE,ATH")

    def test_probe_reports_unavailable_without_bars(self):
        provider = _BarsProvider([])
        with patch.object(indicators, "_get_wb_data_provider", return_value=provider):
            payload = indicators.get_latest_completed_candle_metadata("AAPL", "15Min")

        self.assertEqual(payload["candle_status"], "unavailable")
        self.assertIsNone(payload["candle_key"])

    def test_probe_parses_epoch_timestamps_delivered_as_strings(self):
        bars = [
            {**bar, "time": str(bar["time"])}
            for bar in self._closed_bars()
        ]
        provider = _BarsProvider(bars)
        with patch.object(indicators, "_get_wb_data_provider", return_value=provider):
            payload = indicators.get_latest_completed_candle_metadata("MSFT", "15Min")

        self.assertEqual(payload["candle_status"], "closed")
        self.assertEqual(payload["candle_key"], f"15MIN:{bars[-1]['time']}")

    def test_provider_time_parser_handles_epoch_milliseconds_and_iso_values(self):
        values = pd.Series(["1790721000000", "2026-09-29T22:30:00Z"])
        parsed = indicators._provider_time_to_datetime(values)

        self.assertEqual(parsed.iloc[0].isoformat(), "2026-09-29T22:30:00+00:00")
        self.assertEqual(parsed.iloc[1].isoformat(), "2026-09-29T22:30:00+00:00")

    def test_supported_crypto_pair_forms_share_one_canonical_symbol(self):
        self.assertEqual(normalize_screener_symbol("BTC-USD"), "BTCUSD")
        self.assertEqual(normalize_screener_symbol("btc/usd"), "BTCUSD")
        self.assertEqual(normalize_screener_symbol("BTCUSD"), "BTCUSD")
        self.assertEqual(normalize_screener_symbol("ETH-USDT"), "ETHUSDT")
        self.assertEqual(normalize_screener_symbol("BRK-B"), "BRK-B")

    def test_webull_search_uses_compact_crypto_symbol(self):
        response = Mock()
        response.json.return_value = {
            "quotes": [{
                "quoteType": "CRYPTOCURRENCY",
                "symbol": "BTC-USD",
                "shortname": "Bitcoin USD",
            }]
        }
        broker = WebullBroker.__new__(WebullBroker)
        with patch("requests.get", return_value=response):
            results = broker.search_assets("BTC")

        self.assertEqual(results[0]["symbol"], "BTCUSD")
        self.assertEqual(broker._normalize_symbol("BTC-USD"), "BTCUSD")
        self.assertEqual(broker._normalize_symbol("BTC/USD"), "BTCUSD")
        self.assertEqual(broker._normalize_symbol("BRK-B"), "BRK-B")

    def test_webull_bar_refresh_failure_retains_cached_page(self):
        bars = self._closed_bars()
        broker = WebullBroker.__new__(WebullBroker)
        history_key = ("MSFT", "M15", "RTH,PRE,ATH")
        broker.market_client = SimpleNamespace(
            market_data=SimpleNamespace(
                get_history_bar=Mock(side_effect=RuntimeError("provider unavailable"))
            )
        )
        broker._bar_history_cache = {history_key: bars}
        broker._bar_history_meta = {history_key: {"last_full_sync_at": 0}}
        broker._bar_history_loaded = {history_key}

        with patch("webull_broker.time.sleep"):
            result = broker.get_bars(
                "MSFT", timespan="m15", count=3, trading_sessions="RTH,PRE,ATH"
            )

        self.assertEqual(result, bars)


class ScreenerScanGateTests(unittest.IsolatedAsyncioTestCase):
    async def test_saved_crypto_profile_and_runtime_state_are_migrated_together(self):
        state = ScreenerUserState("crypto-symbol-migration", None)
        state._apply({
            "profiles": {"BTC-USD": {"enabled": True, "timeframe": "15Min"}},
            "order": ["BTC/USD"],
            "latest_scans": {"BTC-USD": {"ticker": "BTC-USD", "price": "$1"}},
            "news_watch": {"BTC/USD": {"status": "current"}},
            "evaluation_state": {"BTC-USD": {"last_candle_key": "15MIN:1"}},
        })

        self.assertEqual(state.order, ["BTCUSD"])
        self.assertIn("BTCUSD", state.profiles)
        self.assertIn("BTCUSD", state.latest_scans)
        self.assertIn("BTCUSD", state.news_watch)
        self.assertIn("BTCUSD", state.evaluation_state)
        self.assertNotIn("BTC-USD", state.latest_scans)

    def test_market_status_matches_bots_regular_window(self):
        eastern = pytz.timezone("US/Eastern")
        status = main._screener_market_status(
            "AAPL",
            {"market_session": "Regular"},
            eastern.localize(datetime(2026, 9, 14, 8, 0)),
        )
        self.assertFalse(status["is_active_window"])
        self.assertEqual(status["market_session_label"], "Regular")
        self.assertIn("9:30 AM ET", status["next_open_str"])

    def test_crypto_market_status_is_always_active(self):
        eastern = pytz.timezone("US/Eastern")
        status = main._screener_market_status(
            "ETHUSD",
            {"market_session": "Regular"},
            eastern.localize(datetime(2026, 9, 13, 2, 0)),
        )
        self.assertTrue(status["is_active_window"])
        self.assertEqual(status["market_session_label"], "24/7")

    def test_legacy_stock_24h_uses_extended_session_window(self):
        eastern = pytz.timezone("US/Eastern")
        status = main._screener_market_status(
            "AAPL",
            {"market_session": "24h"},
            eastern.localize(datetime(2026, 9, 14, 6, 0)),
        )
        self.assertTrue(status["is_active_window"])
        self.assertEqual(status["market_session_label"], "Extended")

    def test_stock_24h_profile_is_migrated_to_extended(self):
        state = ScreenerUserState("stock-session-normalization", None)
        profile = main._screener_profile_from_request(
            {"settings": {"market_session": "24h", "timeframe": "15Min"}}, state, "AAPL"
        )
        self.assertEqual(profile["market_session"], "Extended")

    def test_stock_24h_eligibility_is_rejected_for_free_candle_path(self):
        state = ScreenerUserState("stock-session-eligibility", None)
        payload = main.get_screener_session_eligibility("AAPL", "24h", state)
        self.assertTrue(payload["success"])
        self.assertFalse(payload["allowed"])
        self.assertEqual(payload["market_session"], "Extended")
        self.assertIn("overnight candles", payload["message"])

    async def test_same_candle_reuses_cached_scan_without_full_evaluation(self):
        candle_open = int(time.time()) - 1800
        state = ScreenerUserState("candle-gate-test", None)
        profile = {
            "enabled": True,
            "timeframe": "15Min",
            "market_session": "regular",
            "scan_interval_seconds": 10,
        }
        state.profiles = {"AAPL": profile}
        state.order = ["AAPL"]
        state.config.TICKER_SETTINGS = state.profiles
        state.evaluation_state["AAPL"] = {
            "last_candle_key": f"15MIN:{candle_open}",
            "baseline_required": False,
        }
        state.latest_scans["AAPL"] = {
            "ticker": "AAPL",
            "scan_log": {"status": "WAITING", "summary": "cached"},
        }

        with patch.object(
            main,
            "_screener_market_status",
            return_value={"is_active_window": True, "is_open": True},
        ), patch.object(
            main,
            "get_latest_completed_candle_metadata",
            return_value={"candle_key": f"15MIN:{candle_open}", "candle_close_ts": candle_open + 900, "candle_status": "closed"},
        ), patch.object(main, "evaluate_screener_profile") as evaluate:
            await main._scan_screener_state(state)

        evaluate.assert_not_called()
        self.assertEqual(state.latest_scans["AAPL"]["scan_log"]["summary"], "cached")
        self.assertTrue(state.latest_scans["AAPL"]["scan_log"]["no_new_completed_candle"])
        self.assertEqual(state.activity, [])

    async def test_closed_session_skips_probe_and_evaluation(self):
        state = ScreenerUserState("session-gate-test", None)
        state.profiles = {
            "AAPL": {
                "enabled": True,
                "timeframe": "15Min",
                "market_session": "Regular",
                "scan_interval_seconds": 60,
            }
        }
        state.order = ["AAPL"]
        state.evaluation_state["AAPL"] = {"baseline_required": True}

        closed = {
            "is_active_window": False,
            "is_open": False,
            "market_session_label": "Regular",
            "seconds_to_open": 3600,
            "seconds_to_pre_open": 3480,
            "next_open_str": "Monday at 9:30 AM ET",
        }
        with patch.object(main, "_screener_market_status", return_value=closed), patch.object(
            main, "get_latest_completed_candle_metadata"
        ) as probe, patch.object(main, "evaluate_screener_profile") as evaluate:
            await main._scan_screener_state(state)

        probe.assert_not_called()
        evaluate.assert_not_called()
        self.assertEqual(state.latest_scans["AAPL"]["scan_log"]["status"], "MARKET_CLOSED")
        self.assertTrue(state.latest_scans["AAPL"]["scan_log"]["is_stock_market_closed"])
        self.assertEqual(state.latest_scans["AAPL"]["scan_log"]["refresh_rate_sec"], 60)

    async def test_force_scan_does_not_bypass_closed_session_for_indicator_profile(self):
        candle_open = int(time.time()) - 1800
        state = ScreenerUserState("forced-session-test", None)
        state.profiles = {
            "AAPL": {
                "enabled": True,
                "timeframe": "15Min",
                "market_session": "Regular",
                "scan_interval_seconds": 600,
                "indicators": [],
                "strategy_mode": "technical",
            }
        }
        state.order = ["AAPL"]
        closed = {
            "is_active_window": False,
            "is_open": False,
            "market_session_label": "Regular",
            "seconds_to_open": 3600,
            "seconds_to_pre_open": 3480,
            "next_open_str": "Monday at 9:30 AM ET",
        }
        with patch.object(main, "_screener_market_status", return_value=closed), patch.object(
            main, "get_latest_completed_candle_metadata"
        ) as probe, patch.object(main, "evaluate_screener_profile") as evaluate:
            await main._scan_screener_state(state, force_scan=True)

        evaluate.assert_not_called()
        probe.assert_not_called()
        self.assertEqual(state.latest_scans["AAPL"]["scan_log"]["status"], "MARKET_CLOSED")
        self.assertTrue(state.latest_scans["AAPL"]["scan_log"]["is_stock_market_closed"])

    async def test_closed_event_profile_refreshes_without_indicator_scan(self):
        candle_open = int(time.time()) - 1800
        state = ScreenerUserState("closed-event-test", None)
        state.profiles = {
            "AAPL": {
                "enabled": True,
                "timeframe": "15Min",
                "market_session": "Regular",
                "scan_interval_seconds": 600,
                "strategy_mode": "event",
                "indicators": ["RSI"],
            }
        }
        state.order = ["AAPL"]
        state.latest_scans["AAPL"] = {
            "ticker": "AAPL",
            "timeframe": "15Min",
            "evaluation_status": "valid",
            "data_status": "current",
            "direction": "NEUTRAL",
            "price": "$100.00",
            "price_raw": 100.0,
            "reason": "Cached completed-candle context",
            "signals": {"RSI": {"signal": "NEUTRAL", "enabled": True, "weight": 1}},
            "candle_meta": {
                "candle_key": f"15MIN:{candle_open}",
                "candle_open_ts": candle_open,
                "candle_close_ts": candle_open + 900,
                "candle_status": "closed",
            },
            "scan_log": {"status": "WAITING_EVENT"},
        }
        state.news_watch["AAPL"] = {
            "initialized": True,
            "status": "current",
            "latest": {
                "id": "event-1",
                "title": "Company announces a material product launch",
                "source": "Fixture News",
                "published_ts": time.time() - 60,
                "impact": "HIGH",
                "relevance": "DIRECT",
            },
            "latest_summary": "Material product launch",
            "sentiment_direction": "BULLISH",
            "sentiment_pct": 90,
            "sentiment_confidence": 0.9,
            "article_count": 1,
            "ai_analysis": "Bullish catalyst",
        }
        state.evaluation_state["AAPL"] = {
            "last_candle_key": f"15MIN:{candle_open}",
            "baseline_required": False,
        }
        closed = {
            "is_active_window": False,
            "is_open": False,
            "market_session_label": "Regular",
            "seconds_to_open": 3600,
            "seconds_to_pre_open": 3480,
            "next_open_str": "Monday at 9:30 AM ET",
        }
        with patch.object(main, "_screener_market_status", return_value=closed), patch.object(
            main, "get_latest_completed_candle_metadata"
        ) as probe, patch.object(main, "evaluate_screener_profile") as evaluate:
            await main._scan_screener_state(state, force_scan=True)

        evaluate.assert_not_called()
        probe.assert_not_called()
        scan = state.latest_scans["AAPL"]
        self.assertTrue(scan["event_only_market_closed"])
        self.assertTrue(scan["event_qualification"]["eligible"])
        self.assertEqual(scan["screening_strategy_mode"], "event")

    def test_empty_scan_uses_configured_interval(self):
        scan = main._empty_screener_scan(
            "AAPL",
            {"scan_interval_seconds": 60, "options_trading": False},
        )
        self.assertEqual(scan["scan_log"]["refresh_rate_sec"], 60)

    def test_interval_change_does_not_change_technical_profile_revision(self):
        profile = {"timeframe": "15Min", "market_session": "Regular", "indicators": ["SMA"]}
        changed = {**profile, "scan_interval_seconds": 600}
        self.assertEqual(main.profile_revision(profile), main.profile_revision(changed))

    def test_state_revision_migrates_legacy_stored_hash(self):
        state = ScreenerUserState("revision-migration-test", None)
        profile = {
            "timeframe": "15Min",
            "market_session": "Regular",
            "indicators": ["SMA"],
            "scan_interval_seconds": 600,
            "revision": "legacy-hash-including-interval",
        }
        state.profiles = {"AAPL": profile}
        expected = main.profile_revision(profile)
        self.assertEqual(state.profile_revision("AAPL"), expected)


if __name__ == "__main__":
    unittest.main()
