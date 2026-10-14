"""Settings-dialog mini chart endpoint: candles only, cached, coalesced, stale-on-failure, never slow-fails."""
import asyncio
import os
import sys
import time
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import main  # noqa: E402
from fastapi import HTTPException  # noqa: E402


def bars(n=30, start=1_790_000_000, step=300):
    return [{"time": start + i * step, "open": 100 + i, "high": 101 + i, "low": 99 + i, "close": 100.5 + i, "volume": 1000.0} for i in range(n)]


class ParsingTests(unittest.TestCase):
    def test_provider_rows_are_normalised_sorted_deduplicated_and_trimmed(self):
        raw = [{"time": str(1_790_000_300), "open": "10", "high": "11", "low": "9", "close": "10.5", "volume": "5"},
               {"time": str(1_790_000_000), "open": "9", "high": "10", "low": "8", "close": "9.5", "volume": None},
               {"time": str(1_790_000_000), "open": "9", "high": "10", "low": "8", "close": "9.5", "volume": "1"},   # duplicate
               {"time": str(1_790_000_600), "open": "x", "high": "1", "low": "1", "close": "1"},                     # garbage
               {"time": str(1_790_000_900), "open": "0", "high": "1", "low": "0", "close": "0"}]                       # non-positive
        out = main._mini_chart_bars_from_provider(raw)
        self.assertEqual([b["time"] for b in out], [1_790_000_000, 1_790_000_300])
        self.assertEqual(out[0]["volume"], 0.0)
        self.assertEqual(out[1]["close"], 10.5)
        many = [{"time": str(1_790_000_000 + i * 60), "open": 1, "high": 2, "low": 1, "close": 1.5, "volume": 1} for i in range(300)]
        self.assertEqual(len(main._mini_chart_bars_from_provider(many)), main._MINI_CHART_BARS)

    def test_crypto_envelope_and_empty_inputs(self):
        envelope = [{"symbol": "BTCUSD", "result": [{"time": "1790000000", "open": 1, "high": 2, "low": 1, "close": 2, "volume": 3}]}]
        self.assertEqual(len(main._mini_chart_bars_from_provider(envelope)), 1)
        for empty in (None, [], [{"time": None, "open": 1, "high": 1, "low": 1, "close": 1}], "x"):
            self.assertEqual(main._mini_chart_bars_from_provider(empty), [])


class EndpointTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        main._MINI_CHART_CACHE.clear()
        main._MINI_CHART_INFLIGHT.clear()

    async def get(self, ticker="AAPL", tf="5Min"):
        return await main.get_mini_chart(ticker, tf, {"uid": "u"})

    async def test_fetches_once_then_serves_the_cache(self):
        with patch.object(main, "_fetch_mini_chart_bars", return_value=(bars(), "webull")) as fetch:
            first = await self.get()
            second = await self.get()
        self.assertEqual((first["ticker"], first["stale"], len(first["bars"]), first["source"]), ("AAPL", False, 30, "webull"))
        self.assertEqual(second["bars"], first["bars"])
        self.assertEqual(fetch.call_count, 1)

    async def test_concurrent_requests_share_one_fetch(self):
        def slow(symbol, tf):
            time.sleep(0.2)
            return bars(), "webull"
        with patch.object(main, "_fetch_mini_chart_bars", side_effect=slow) as fetch:
            results = await asyncio.gather(*(self.get() for _ in range(5)))
        self.assertEqual(fetch.call_count, 1)
        self.assertTrue(all(len(r["bars"]) == 30 for r in results))

    async def test_refresh_failure_returns_the_last_good_bars_marked_stale(self):
        with patch.object(main, "_fetch_mini_chart_bars", return_value=(bars(), "webull")):
            await self.get()
        main._MINI_CHART_CACHE[("AAPL", "5Min")]["at"] -= 100   # older than the 15 s freshness window
        with patch.object(main, "_fetch_mini_chart_bars", side_effect=RuntimeError("provider down")):
            out = await self.get()
        self.assertTrue(out["stale"])
        self.assertEqual(len(out["bars"]), 30)

    async def test_slow_refresh_returns_stale_bars_instead_of_waiting(self):
        with patch.object(main, "_fetch_mini_chart_bars", return_value=(bars(), "webull")):
            await self.get()
        main._MINI_CHART_CACHE[("AAPL", "5Min")]["at"] -= 100

        def very_slow(symbol, tf):
            time.sleep(0.5)
            return bars(40), "webull"
        with patch.object(main, "_MINI_CHART_WAIT_SECONDS", 0.1), patch.object(main, "_fetch_mini_chart_bars", side_effect=very_slow):
            started = time.monotonic()
            out = await self.get()
            waited = time.monotonic() - started
            self.assertTrue(out["stale"])
            self.assertLess(waited, 0.4)
            await asyncio.sleep(0.6)  # the background fetch finishes and refreshes the cache for the next request
        self.assertEqual(len(main._MINI_CHART_CACHE[("AAPL", "5Min")]["bars"]), 40)

    async def test_no_cache_and_failure_is_a_clear_error_not_a_hang(self):
        with patch.object(main, "_fetch_mini_chart_bars", side_effect=RuntimeError("down")):
            with self.assertRaises(HTTPException) as caught:
                await self.get()
        self.assertEqual(caught.exception.status_code, 502)
        with patch.object(main, "_MINI_CHART_WAIT_SECONDS", 0.05), \
                patch.object(main, "_fetch_mini_chart_bars", side_effect=lambda s, t: (time.sleep(0.3), (bars(), "x"))[1]):
            with self.assertRaises(HTTPException) as slow:
                await self.get("MSFT")
        self.assertEqual(slow.exception.status_code, 504)
        await asyncio.sleep(0.4)

    async def test_empty_data_is_404_and_input_is_validated(self):
        with patch.object(main, "_fetch_mini_chart_bars", return_value=([], "none")):
            with self.assertRaises(HTTPException) as empty:
                await self.get()
        self.assertEqual(empty.exception.status_code, 404)
        for ticker, tf in (("!!", "5Min"), ("AAPL", "7Min"), ("A" * 40, "5Min")):
            with self.assertRaises(HTTPException) as bad:
                await self.get(ticker, tf)
            self.assertEqual(bad.exception.status_code, 400)

    async def test_it_does_not_load_the_engine_or_screener_state(self):
        import inspect
        source = inspect.getsource(main.get_mini_chart)
        self.assertNotIn("get_user_engine", source)
        self.assertNotIn("get_screener_state", source)


if __name__ == "__main__":
    unittest.main()
