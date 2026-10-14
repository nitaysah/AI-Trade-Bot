import asyncio
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


class _SlowQuery:
    def __init__(self, release, calls):
        self.release, self.calls = release, calls

    def where(self, **_kwargs):
        return self

    def stream(self, **_kwargs):
        self.calls.append(1)
        self.release.wait(5)
        return [_Document("late-user")]


class _SlowDb:
    def __init__(self, release, calls):
        self.release, self.calls = release, calls

    def collection(self, _name):
        return _SlowQuery(self.release, self.calls)


class ScreenerRegistryResilienceTests(unittest.IsolatedAsyncioTestCase):
    async def test_failure_backs_off_instead_of_querying_every_cycle(self):
        manager = ScreenerManager(_FailingDb())
        with patch("local_store.load_state", return_value={"uids": ["cached"], "saved_at": 1}):
            await manager.discover_active_users()
            first_retry = manager._registry_retry_at
            self.assertGreater(first_retry, 0)
            calls = []
            manager.db = type("D", (), {"collection": lambda self, _n: calls.append(1)})()
            await manager.discover_active_users()  # still inside the back-off window: Firestore is not touched
            self.assertEqual(calls, [])
        self.assertEqual(manager._registry_failures, 1)

    async def test_a_slow_read_is_not_duplicated_by_the_next_refresh(self):
        import threading

        release, calls = threading.Event(), []
        manager = ScreenerManager(_SlowDb(release, calls))
        with patch("local_store.save_state"), patch("local_store.load_state", return_value=None):
            task = asyncio.ensure_future(manager.discover_active_users())
            await asyncio.sleep(0.2)
            second = asyncio.ensure_future(manager.discover_active_users())
            await asyncio.sleep(0.2)
            release.set()
            await asyncio.gather(task, second)
        self.assertEqual(len(calls), 1)  # one worker thread served both refreshes
        self.assertIn("late-user", manager.states)


if __name__ == "__main__":
    unittest.main()
