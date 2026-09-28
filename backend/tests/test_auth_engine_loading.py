import asyncio
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import main


def _request(path: str):
    return SimpleNamespace(
        url=SimpleNamespace(path=path),
        query_params={},
    )


def _engine(*, legal_settings_loaded: bool, loaded_from_cloud: bool, accepted: bool):
    acceptance = {
        "terms_version": main.LEGAL_TERMS_VERSION,
        "paper_trading_acknowledged": True,
    } if accepted else {}
    engine = SimpleNamespace(
        uid="auth-load-test-user",
        loaded_from_cloud=loaded_from_cloud,
        legal_settings_loaded=legal_settings_loaded,
        load_lock=asyncio.Lock(),
        alpaca_load_status="not_started",
        config=SimpleNamespace(legal_acceptance=acceptance),
        bot_running=True,
        task_start_lock=asyncio.Lock(),
        task=None,
    )

    async def load_from_cloud(*, connect_broker=True):
        if connect_broker:
            engine.loaded_from_cloud = True
        else:
            engine.legal_settings_loaded = True
            engine.alpaca_load_status = "deferred"
        return True

    engine.load_from_cloud = AsyncMock(side_effect=load_from_cloud)
    return engine


class AuthEngineLoadingTests(unittest.IsolatedAsyncioTestCase):
    async def test_legal_acceptance_load_defers_broker_initialization(self):
        engine = _engine(
            legal_settings_loaded=False,
            loaded_from_cloud=False,
            accepted=False,
        )
        with patch.object(main.user_manager, "get_engine", return_value=engine), patch.object(main, "set_user_config"):
            result = await main.get_user_engine(
                _request("/api/legal/acceptance"),
                {"uid": engine.uid, "email": "test@example.com"},
            )

        self.assertIs(result, engine)
        engine.load_from_cloud.assert_awaited_once_with(connect_broker=False)
        self.assertTrue(engine.legal_settings_loaded)
        self.assertFalse(engine.loaded_from_cloud)

    async def test_workspace_request_full_loads_after_legal_only_load(self):
        engine = _engine(
            legal_settings_loaded=True,
            loaded_from_cloud=False,
            accepted=True,
        )
        with patch.object(main.user_manager, "get_engine", return_value=engine), patch.object(main, "set_user_config"):
            result = await main.get_user_engine(
                _request("/api/dashboard"),
                {"uid": engine.uid, "email": "test@example.com"},
            )

        self.assertIs(result, engine)
        engine.load_from_cloud.assert_awaited_once_with(connect_broker=True)
        self.assertTrue(engine.loaded_from_cloud)


if __name__ == "__main__":
    unittest.main()
