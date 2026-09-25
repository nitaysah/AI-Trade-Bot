import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import main
from screener_service import ScreenerUserState


class ScreenerNewsToggleSaveTests(unittest.IsolatedAsyncioTestCase):
    async def test_combined_forces_news_watch_enabled(self):
        state = ScreenerUserState("news-toggle-test", None)
        initial = main._screener_profile_from_request({
            "symbol": "AAPL",
            "settings": {
                "enabled": True,
                "timeframe": "15Min",
                "scan_interval_seconds": 10,
                "indicators": ["RSI", "EMA Cross", "SMA"],
                "min_buy_signals": 2,
                "min_sell_signals": 2,
                "strategy_mode": "combined",
                "news_watch_enabled": True,
                "earnings_watch_enabled": False,
            },
        }, state, "AAPL")
        state.config.TICKER_SETTINGS = {"AAPL": initial}
        state.profiles = state.config._ticker_settings
        state.order = ["AAPL"]
        existing_scan = {
            "ticker": "AAPL",
            "price": "$332.27",
            "evaluation_status": "valid",
            "direction": "BULLISH",
        }
        state.latest_scans["AAPL"] = existing_scan
        state.save = AsyncMock()

        updated_settings = {**state.profiles["AAPL"], "news_watch_enabled": False}
        with patch.object(main.asyncio, "create_task") as create_task:
            response = await main.save_screener_profile(
                {"symbol": "AAPL", "settings": updated_settings},
                state,
            )

        self.assertFalse(response["technical_scan_changed"])
        self.assertTrue(response["news_watch_enabled"])
        self.assertIs(state.latest_scans["AAPL"], existing_scan)
        self.assertFalse(state.force_scan.is_set())
        create_task.assert_called_once()
        create_task.call_args.args[0].close()
        state.save.assert_awaited_once()

    async def test_dashboard_watchlist_toggle_updates_dashboard_settings_only(self):
        state = ScreenerUserState("dashboard-watchlist-toggle-test", None)
        engine = SimpleNamespace(
            uid=state.uid,
            loaded_from_cloud=True,
            load_lock=__import__("asyncio").Lock(),
            config=SimpleNamespace(WATCHLIST=[]),
            save_settings=AsyncMock(),
        )
        with patch.object(main.user_manager, "get_engine", return_value=engine), patch.object(main, "clear_evaluation_cache"), patch.object(main, "_clear_chart_analysis_cache"):
            added = await main._set_screener_dashboard_watchlist(state, "AAPL", True)
            removed = await main._set_screener_dashboard_watchlist(state, "AAPL", False)

        self.assertTrue(added["changed"])
        self.assertTrue(added["enabled"])
        self.assertTrue(removed["changed"])
        self.assertFalse(removed["enabled"])
        self.assertEqual(engine.config.WATCHLIST, [])
        self.assertEqual(engine.save_settings.await_count, 2)

    async def test_profile_save_applies_explicit_dashboard_watchlist_choice(self):
        state = ScreenerUserState("dashboard-watchlist-save-test", None)
        initial = main._screener_profile_from_request({
            "symbol": "AAPL",
            "settings": {"enabled": True, "timeframe": "15Min", "indicators": ["SMA"]},
        }, state, "AAPL")
        state.config.TICKER_SETTINGS = {"AAPL": initial}
        state.profiles = state.config._ticker_settings
        state.order = ["AAPL"]
        state.save = AsyncMock()
        watchlist_result = {"enabled": True, "changed": True, "watchlist": ["AAPL"]}

        with patch.object(main, "_set_screener_dashboard_watchlist", new=AsyncMock(return_value=watchlist_result)) as sync_watchlist:
            response = await main.save_screener_profile(
                {"symbol": "AAPL", "add_to_watchlist": True, "settings": dict(initial)}, state
            )

        sync_watchlist.assert_awaited_once_with(state, "AAPL", True)
        self.assertEqual(response["dashboard_watchlist"], ["AAPL"])
        self.assertTrue(response["dashboard_watchlist_enabled"])

    async def test_disabling_earnings_watch_preserves_latest_technical_scan(self):
        state = ScreenerUserState("earnings-toggle-test", None)
        initial = main._screener_profile_from_request({
            "symbol": "AAPL",
            "settings": {
                "enabled": True,
                "timeframe": "15Min",
                "scan_interval_seconds": 60,
                "indicators": ["RSI", "SMA"],
                "min_buy_signals": 2,
                "min_sell_signals": 2,
                "strategy_mode": "technical",
                "earnings_watch_enabled": True,
                "news_watch_enabled": True,
            },
        }, state, "AAPL")
        self.assertFalse(initial["news_watch_enabled"])
        state.config.TICKER_SETTINGS = {"AAPL": initial}
        state.profiles = state.config._ticker_settings
        state.order = ["AAPL"]
        existing_scan = {"ticker": "AAPL", "direction": "BULLISH"}
        state.latest_scans["AAPL"] = existing_scan
        state.earnings_watch["AAPL"] = {"initialized": True, "status": "current", "event_date": "2099-10-29"}
        state.save = AsyncMock()

        updated_settings = {**state.profiles["AAPL"], "earnings_watch_enabled": False}
        with patch.object(main.asyncio, "create_task") as create_task:
            response = await main.save_screener_profile({"symbol": "AAPL", "settings": updated_settings}, state)

        self.assertFalse(response["technical_scan_changed"])
        self.assertFalse(response["earnings_watch_enabled"])
        self.assertFalse(response["news_watch_enabled"])
        self.assertIs(state.latest_scans["AAPL"], existing_scan)
        create_task.assert_called_once()
        create_task.call_args.args[0].close()


if __name__ == "__main__":
    unittest.main()
