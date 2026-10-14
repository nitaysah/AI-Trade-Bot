"""A new Screener profile goes on top of the list; saving an existing one does not move it."""
import unittest
from unittest.mock import AsyncMock, patch

import main
from screener_service import ScreenerUserState


class NewProfileOnTopTests(unittest.IsolatedAsyncioTestCase):
    async def _save(self, state, symbol, enabled=False):
        def swallow(coro, *args, **kwargs):
            coro.close()
            return None

        with patch.object(main.asyncio, "create_task", side_effect=swallow):
            return await main.save_screener_profile({"symbol": symbol, "settings": {
                "enabled": enabled, "timeframe": "30Min", "indicators": ["RSI"], "alert_source": "indicators"}}, state)

    async def test_new_profiles_are_added_at_the_top_in_the_order_they_were_created(self):
        state = ScreenerUserState("new-on-top", None)
        state.save = AsyncMock()
        for symbol in ("AAPL", "MSFT", "TSLA"):
            await self._save(state, symbol)
        self.assertEqual(state.order, ["TSLA", "MSFT", "AAPL"])

    async def test_saving_an_existing_profile_keeps_its_place(self):
        state = ScreenerUserState("existing-keeps-place", None)
        state.save = AsyncMock()
        for symbol in ("AAPL", "MSFT", "TSLA"):
            await self._save(state, symbol)
        await self._save(state, "AAPL")
        self.assertEqual(state.order, ["TSLA", "MSFT", "AAPL"])


if __name__ == "__main__":
    unittest.main()
