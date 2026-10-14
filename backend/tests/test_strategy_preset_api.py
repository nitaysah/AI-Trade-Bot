import asyncio
import copy
import json
import unittest
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import numpy as np
import pandas as pd
from fastapi import HTTPException

import backtester
import main
import preset_runtime as rt
import strategy_presets as sp
from user_config import UserConfig, set_user_config, _active_user_config


def make_engine(settings=None):
    config = SimpleNamespace(
        TICKER_SETTINGS={"SPY": copy.deepcopy(settings)} if settings is not None else {},
        TRADELIST=[], WATCHLIST=[], DEFAULT_TIMEFRAME="1Hour",
        MIN_BULLISH_SIGNALS=1, MIN_BEARISH_SIGNALS=1,
    )
    broker = MagicMock()
    broker.check_market_session_allowed.return_value = {"success": True, "allowed": True}
    broker.check_tradable.return_value = {"success": True, "tradable": True, "asset_class": "us_equity"}
    engine = SimpleNamespace(
        uid="preset-api", config=config, broker=broker, force_scan_trigger=asyncio.Event(),
        save_settings=AsyncMock(), _record_bot_scan=MagicMock(), _clear_signal_reset_lock=MagicMock(return_value=False),
    )
    return engine


def swallow_background_tasks():
    def fake(coro, **kwargs):
        coro.close()
        return MagicMock()
    return patch.object(main.asyncio, "create_task", side_effect=fake)


MANUAL = {
    "source": "bots", "buy_mode": "swing_points", "sell_mode": "hybrid", "indicators": ["RSI", "MACD"],
    "min_buy_signals": 2, "min_sell_signals": 2, "timeframe": "1Hour", "market_session": "Extended",
    "amount": 100.0, "allocation_mode": "fixed", "risk_per_trade": 0.1, "max_position_pct": 1.0,
    "max_daily_drawdown": 0.5,
}


class HelperTests(unittest.TestCase):
    def test_server_owned_keys_are_stripped_from_client_payloads(self):
        payload = {rt.OWNS_KEY: True, rt.LAST_ENTRY_KEY: "x", rt.STOP_KEY: {}, rt.PAUSE_KEY: "p",
                   rt.LAST_CORPORATE_ACTION_KEY: "k", "_strategy_manual_backup": {"buy_mode": "evil"},
                   "strategy_preset_catalog_version": 99, "strategy_preset": "golden_cross"}
        main._strip_strategy_preset_server_keys(payload)
        self.assertEqual(payload, {"strategy_preset": "golden_cross"})

    def test_settings_without_any_preset_state_are_returned_untouched(self):
        settings = dict(MANUAL)
        self.assertIs(main._resolve_strategy_preset(settings, "SPY", None), settings)

    def test_validation_rejects_options_crypto_and_a_bad_position_policy(self):
        base = {"strategy_preset_enabled": True, "strategy_preset": "golden_cross"}
        main._validate_strategy_preset_request(base, "SPY")
        for payload, ticker in (({**base, "options_trading": True}, "SPY"), (base, "BTCUSD"),
                                ({**base, "strategy_preset_position_policy": "yolo"}, "SPY")):
            with self.assertRaises(HTTPException) as caught:
                main._validate_strategy_preset_request(payload, ticker)
            self.assertEqual(caught.exception.status_code, 400)


class UpdateTickerSettingsTests(unittest.IsolatedAsyncioTestCase):
    async def update(self, eng, settings):
        with swallow_background_tasks():
            return await main.update_ticker_settings({"ticker": "SPY", "settings": settings}, eng)

    async def test_enable_then_disable_round_trips_the_manual_setup(self):
        MANUAL_UNSUPPORTED = {**MANUAL, "timeframe": "2Min", "market_session": "24h"}  # values the engine cannot run
        eng = make_engine(MANUAL_UNSUPPORTED)
        await self.update(eng, {"strategy_preset_enabled": True, "strategy_preset": "golden_cross",
                                "strategy_preset_variant": "sma_50_200", rt.OWNS_KEY: True})
        saved = eng.config.TICKER_SETTINGS["SPY"]
        self.assertEqual((saved["buy_mode"], saved["sell_mode"]), (sp.PRESET_MODE, sp.PRESET_MODE))
        self.assertEqual((saved["timeframe"], saved["market_session"]), ("1Day", "Regular"))
        self.assertEqual(saved["strategy_preset_catalog_version"], sp.CATALOG_VERSION)
        self.assertEqual(saved["strategy_preset_position_policy"], "unmanaged")
        self.assertNotIn(rt.OWNS_KEY, saved)  # a client cannot claim position ownership
        for key in ("amount", "allocation_mode", "risk_per_trade", "max_position_pct", "max_daily_drawdown"):
            self.assertEqual(saved[key], MANUAL[key], key)  # risk and sizing untouched
        self.assertEqual(saved["_strategy_manual_backup"]["buy_mode"], "swing_points")
        await self.update(eng, {"strategy_preset_enabled": False})
        restored = eng.config.TICKER_SETTINGS["SPY"]
        for key in ("buy_mode", "sell_mode", "indicators", "min_buy_signals"):
            self.assertEqual(restored[key], MANUAL_UNSUPPORTED[key], key)
        self.assertEqual((restored["timeframe"], restored["market_session"]), ("1Day", "Regular"))  # not rolled back
        self.assertNotIn("_strategy_manual_backup", restored)
        self.assertEqual(restored["strategy_preset"], sp.DISABLED)

    async def test_a_client_cannot_overwrite_the_saved_manual_backup(self):
        eng = make_engine(MANUAL)
        await self.update(eng, {"strategy_preset_enabled": True, "strategy_preset": "golden_cross"})
        await self.update(eng, {"strategy_preset_enabled": True, "strategy_preset": "golden_cross",
                                "_strategy_manual_backup": {"buy_mode": "custom_price"}})
        self.assertEqual(eng.config.TICKER_SETTINGS["SPY"]["_strategy_manual_backup"]["buy_mode"], "swing_points")

    async def test_options_and_crypto_are_rejected_before_any_state_changes(self):
        eng = make_engine(MANUAL)
        before = copy.deepcopy(eng.config.TICKER_SETTINGS)
        for settings in ({"strategy_preset_enabled": True, "strategy_preset": "golden_cross", "options_trading": True},
                         {"strategy_preset_enabled": True, "strategy_preset": "golden_cross", "strategy_preset_position_policy": "bad"}):
            with self.assertRaises(HTTPException) as caught:
                await self.update(eng, settings)
            self.assertEqual(caught.exception.status_code, 400)
            self.assertEqual(eng.config.TICKER_SETTINGS, before)
        with swallow_background_tasks(), self.assertRaises(HTTPException):
            await main.update_ticker_settings({"ticker": "BTCUSD", "settings": {"strategy_preset_enabled": True, "strategy_preset": "golden_cross"}}, eng)
        self.assertNotIn("BTCUSD", eng.config.TICKER_SETTINGS)

    async def test_plain_manual_bots_are_not_given_preset_keys(self):
        eng = make_engine(MANUAL)
        await self.update(eng, {"min_buy_signals": 1})
        saved = eng.config.TICKER_SETTINGS["SPY"]
        self.assertNotIn("strategy_preset", saved)
        self.assertEqual(saved["buy_mode"], "swing_points")

    async def test_adopt_policy_is_accepted_and_stored(self):
        eng = make_engine(MANUAL)
        await self.update(eng, {"strategy_preset_enabled": True, "strategy_preset": "supertrend_ema200",
                                "strategy_preset_position_policy": "ADOPT"})
        self.assertEqual(eng.config.TICKER_SETTINGS["SPY"]["strategy_preset_position_policy"], "adopt")


class CreateBotTests(unittest.IsolatedAsyncioTestCase):
    async def test_create_bot_applies_the_preset_and_rejects_options_without_side_effects(self):
        eng = make_engine()
        payload = {"symbol": "SPY", "capital": 500, "amount": 500, "timeframe": "1Hour", "market_session": "Regular",
                   "sell_mode": "indicator", "buy_mode": "indicator", "indicators": ["RSI"], "threshold": 1,
                   "strategy_preset_enabled": True, "strategy_preset": "golden_cross", "strategy_preset_variant": "ema_50_200",
                   "risk_per_trade": 0.1, "max_position_pct": 1.0, "max_daily_drawdown": 0.5}
        with swallow_background_tasks():
            await main.create_bot(dict(payload), eng)
        saved = eng.config.TICKER_SETTINGS["SPY"]
        self.assertEqual((saved["buy_mode"], saved["sell_mode"], saved["timeframe"]), (sp.PRESET_MODE, sp.PRESET_MODE, "1Hour"))  # the user's own allowed value is kept
        self.assertEqual(saved["strategy_preset_variant"], "ema_50_200")
        self.assertEqual((saved["risk_per_trade"], saved["max_position_pct"], saved["amount"]), (0.1, 1.0, 500.0))
        self.assertEqual(saved["_strategy_manual_backup"]["timeframe"], "1Hour")
        self.assertEqual(saved["max_consecutive_losses"], 3)
        eng = make_engine()
        with self.assertRaises(HTTPException) as caught:
            await main.create_bot({**payload, "options_trading": True}, eng)
        self.assertEqual(caught.exception.status_code, 400)
        self.assertEqual(eng.config.TICKER_SETTINGS, {})
        eng.broker.check_tradable.assert_not_called()


class CatalogAndBacktestApiTests(unittest.IsolatedAsyncioTestCase):
    async def test_catalog_endpoint_is_read_only_and_serializable(self):
        out = await main.get_strategy_presets(make_engine())
        json.dumps(out)
        self.assertEqual(out["version"], sp.CATALOG_VERSION)
        self.assertEqual({p["id"] for p in out["presets"]}, {"golden_cross", "supertrend_ema200", "malik_qqq", "malik_soxx", "malik_iwm", "session_rider"})
        self.assertEqual(set(out["allowed_override_keys"]), set(sp.ALLOWED_OVERRIDE_KEYS))

    async def test_optimizer_refuses_a_fixed_strategy(self):
        with self.assertRaises(HTTPException) as caught:
            await main.run_optimization_endpoint({"ticker": "SPY", "strategy_preset_enabled": True, "strategy_preset": "golden_cross"}, make_engine())
        self.assertEqual(caught.exception.status_code, 400)
        self.assertIn("cannot be optimized", caught.exception.detail)

    async def test_backtest_endpoint_runs_the_preset_and_reports_it(self):
        closes = np.concatenate([np.linspace(100, 60, 120), np.linspace(60, 140, 120), np.linspace(140, 70, 120)])
        index = pd.bdate_range(end=pd.Timestamp.now().normalize() - pd.Timedelta(days=1), periods=len(closes))
        opens = np.concatenate([[closes[0]], closes[:-1] * 1.01])
        frame = pd.DataFrame({"open": opens, "high": np.maximum(opens, closes) + 0.5, "low": np.minimum(opens, closes) - 0.5,
                              "close": closes, "volume": 1e6}, index=index)
        frame.attrs.update({"data_source": "alpaca", "feed": "sip", "data_quality": "OK", "adjustment": "split"})
        request = {"ticker": "SPY", "timeframe": "1Day", "days": 700, "capital": 10000, "trade_amount": 10000,
                   "strategy_preset_enabled": True, "strategy_preset": "golden_cross", "strategy_preset_variant": "sma_20_50",
                   "buy_mode": "swing_points", "sell_mode": "hybrid", "indicators": ["RSI"], "threshold": 3,
                   "enable_cooldown": False, rt.OWNS_KEY: True}
        with patch.object(backtester, "get_historical_data", return_value=frame):
            out = await main.run_backtest(request, make_engine())
        self.assertEqual(out["status"], "success", out)
        info = out["results"]["summary"]["strategy_preset"]
        self.assertEqual((info["id"], info["variant_id"], info["fill_model"]), ("golden_cross", "sma_20_50", "next_bar_open"))
        self.assertEqual(out["results"]["summary"]["data_source"], "alpaca")

    async def test_backtest_rejects_options_with_a_preset(self):
        with self.assertRaises(HTTPException):
            await main.run_backtest({"ticker": "SPY", "timeframe": "1Day", "days": 100, "options_trading": True,
                                     "strategy_preset_enabled": True, "strategy_preset": "golden_cross"}, make_engine())


class ScreenerProfileTests(unittest.TestCase):
    def profile(self, data):
        config = UserConfig("preset-screener-profile")
        token = set_user_config(config)
        self.addCleanup(_active_user_config.reset, token)
        state = SimpleNamespace(config=SimpleNamespace(TICKER_SETTINGS={}, MIN_BULLISH_SIGNALS=1, MIN_BEARISH_SIGNALS=1))
        return main._screener_profile_from_request(data, state, "SPY")

    def test_preset_profile_defaults_to_the_technical_policy_and_never_keeps_position_settings(self):
        settings = self.profile({"symbol": "SPY", "timeframe": "5Min", "indicators": ["RSI"],
                                 "strategy_preset_enabled": True, "strategy_preset": "golden_cross",
                                 "strategy_preset_position_policy": "adopt", rt.OWNS_KEY: True})
        self.assertEqual(settings["strategy_mode"], "technical")
        self.assertEqual((settings["buy_mode"], settings["timeframe"], settings["source"]), (sp.PRESET_MODE, "5Min", "screener"))  # allowed value kept
        self.assertNotIn("strategy_preset_position_policy", settings)
        self.assertNotIn(rt.OWNS_KEY, settings)

    def test_an_engine_unsupported_timeframe_is_replaced_in_a_screener_profile(self):
        settings = self.profile({"symbol": "SPY", "timeframe": "2Min", "strategy_preset_enabled": True, "strategy_preset": "golden_cross"})
        self.assertEqual(settings["timeframe"], "1Day")

    def test_legacy_combined_clients_map_to_the_exclusive_alert_sources(self):
        # Combined no longer exists: a preset wins (Strategy), a plain profile is Indicators.
        explicit = self.profile({"symbol": "SPY", "strategy_mode": "combined", "strategy_preset_enabled": True,
                                 "strategy_preset": "golden_cross"})
        self.assertEqual((explicit["alert_source"], explicit["strategy_mode"]), ("strategy", "technical"))
        plain = self.profile({"symbol": "SPY", "indicators": ["RSI"]})
        self.assertEqual((plain["alert_source"], plain["strategy_mode"]), ("indicators", "technical"))
        self.assertNotIn("strategy_preset", plain)

    def test_options_profiles_cannot_use_a_preset(self):
        with self.assertRaises(HTTPException):
            self.profile({"symbol": "SPY", "options_trading": True, "strategy_preset_enabled": True, "strategy_preset": "golden_cross"})


if __name__ == "__main__":
    unittest.main()


class ScanPolicyStatusTests(unittest.TestCase):
    """The Scan Log tells the user when a strategy-timed bot will evaluate next."""

    SETTINGS = {"strategy_preset_enabled": True, "strategy_preset": "golden_cross", "strategy_preset_variant": "sma_50_200",
                "timeframe": "1Day", "market_session": "Regular"}

    def status(self, settings=None, marker=None, **kwargs):
        import main
        eng = MagicMock()
        eng.preset_scan_markers = {"SPY": marker} if marker is not None else {}
        settings = {**self.SETTINGS, **(settings or {})}
        return main._scan_policy_status(eng, "SPY", settings, kwargs.get("has_position", False), kwargs.get("has_open_order", False))

    def marker(self):
        import preset_runtime
        from datetime import datetime, timezone
        return {"fingerprint": preset_runtime.scan_fingerprint({**self.SETTINGS}), "epoch": "2026-10-06",
                "marked_at": "2026-10-07T10:00:00-04:00", "next_evaluation_at": "2026-10-08T09:30:00-04:00"}

    def test_idle_flat_bot_reports_the_next_evaluation(self):
        info = self.status(marker=self.marker())
        self.assertTrue(info["idle"])
        self.assertEqual(info["next_evaluation_at"], "2026-10-08T09:30:00-04:00")
        self.assertIn("once per completed daily candle", info["label"])

    def test_a_position_or_order_is_never_reported_as_idle(self):
        for kwargs in ({"has_position": True}, {"has_open_order": True}):
            info = self.status(marker=self.marker(), **kwargs)
            self.assertFalse(info["idle"])
            self.assertIsNone(info["next_evaluation_at"])
            self.assertIn("normal refresh", info["label"])

    def test_no_marker_or_changed_settings_are_not_idle(self):
        self.assertFalse(self.status()["idle"])
        self.assertFalse(self.status({"timeframe": "1Day", "market_session": "Extended"}, marker=self.marker())["idle"])

    def test_no_policy_for_intraday_manual_or_options_bots(self):
        self.assertIsNone(self.status({"timeframe": "5Min"}))
        self.assertIsNone(self.status({"strategy_preset_enabled": False}))
        self.assertIsNone(self.status({"options_trading": True}))
