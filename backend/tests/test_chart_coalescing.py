import unittest
from types import SimpleNamespace

import main


class ChartCoalescingTests(unittest.TestCase):
    def test_forced_duplicate_uses_last_chart_while_refresh_is_in_flight(self):
        config = SimpleNamespace(
            contexts={},
            active_toggles={},
            active_parameters={},
            TICKER_SETTINGS={},
        )
        engine = SimpleNamespace(uid="chart-coalescing-test", config=config)
        main._chart_analysis_cache.clear()
        cache_key = main.stable_cache_key(
            engine.uid, "dashboard", "TSLA", "5Min", "webull",
            main._cache_settings_hash(engine, "TSLA", "dashboard", {}), 3,
        )
        fetch_lock = main._chart_analysis_cache.with_inflight_lock(
            main.stable_cache_key("chart_fetch", cache_key)
        )
        main._chart_analysis_cache.set(
            cache_key,
            {"analysis": {"price_history": [{"close": 250.0}]}},
        )
        fetch_lock.acquire()
        try:
            analysis, cached, _age = main._get_cached_chart_analysis(
                "TSLA", "5Min", engine, force=True, source="dashboard", settings={}
            )
        finally:
            fetch_lock.release()
            main._chart_analysis_cache.clear()

        self.assertEqual(analysis["price_history"][0]["close"], 250.0)
        self.assertTrue(cached)


if __name__ == "__main__":
    unittest.main()
