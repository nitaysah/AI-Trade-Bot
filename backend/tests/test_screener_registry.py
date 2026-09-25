import unittest
from unittest.mock import patch

from screener_service import ScreenerManager


class _Document:
    def __init__(self, document_id):
        self.id = document_id


class _Query:
    def __init__(self, documents):
        self.documents = documents

    def where(self, **_kwargs):
        return self

    def stream(self, **_kwargs):
        return list(self.documents)


class _Db:
    def __init__(self, documents):
        self.documents = documents

    def collection(self, _name):
        return _Query(self.documents)


class _FailingDb:
    def collection(self, _name):
        return self

    def where(self, **_kwargs):
        return self

    def stream(self, **_kwargs):
        raise RuntimeError("503 failed to connect to all addresses")


class ScreenerRegistryTests(unittest.IsolatedAsyncioTestCase):
    async def test_registry_refresh_prunes_deactivated_discovered_user(self):
        manager = ScreenerManager(_Db([_Document("active-user")]))
        manager.get_state("dev-user")
        manager._registry_uids = {"dev-user"}

        with patch("local_store.save_state"):
            states = await manager.discover_active_users()

        self.assertNotIn("dev-user", manager.states)
        self.assertIn("active-user", manager.states)
        self.assertEqual([state.uid for state in states], ["active-user"])

    async def test_registry_failure_uses_local_cache_and_keeps_existing_state(self):
        manager = ScreenerManager(_FailingDb())
        existing = manager.get_state("existing-user")
        manager._registry_uids = {"existing-user"}

        with patch(
            "local_store.load_state",
            return_value={"uids": ["cached-user"], "saved_at": 123},
        ):
            states = await manager.discover_active_users()

        self.assertIn("existing-user", manager.states)
        self.assertIn("cached-user", manager.states)
        self.assertIs(manager.states["existing-user"], existing)
        self.assertEqual({state.uid for state in states}, {"cached-user"})
        self.assertIn("cached-user", manager._registry_uids)


if __name__ == "__main__":
    unittest.main()
