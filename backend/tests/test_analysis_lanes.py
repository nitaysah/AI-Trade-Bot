"""Chart requests get their own per-user lane and wait briefly for a slot instead of failing at once with 429."""
import asyncio
import unittest

from fastapi import HTTPException

import main

UID = "lane-test-user"


def _reset():
    for key in list(main._analysis_user_semaphores):
        if key.startswith(UID):
            main._analysis_user_semaphores.pop(key, None)
            main._analysis_user_last_seen.pop(key, None)
    main._analysis_rate_windows.pop(UID, None)


class AnalysisLaneTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        _reset()

    def tearDown(self):
        _reset()

    async def _hold(self, stack, count, lane=""):
        """Take ``count`` of the account's slots in a lane and keep them until the stack closes."""
        for _ in range(count):
            await stack.enter_async_context(main._bounded_analysis_work(UID, "background work", lane=lane))

    async def test_background_work_filling_the_default_lane_does_not_block_a_chart(self):
        from contextlib import AsyncExitStack
        async with AsyncExitStack() as stack:
            await self._hold(stack, main._ANALYSIS_MAX_PER_USER)
            with self.assertRaises(HTTPException) as blocked:                           # other work still fails fast
                async with main._bounded_analysis_work(UID, "AI indicator"):
                    pass
            self.assertEqual(blocked.exception.status_code, 429)
            ran = False
            async with main._bounded_analysis_work(UID, "chart preview", 0.25, main._ANALYSIS_CHART_LANE):
                ran = True                                                              # the chart has its own lane
            self.assertTrue(ran)

    async def test_a_chart_waits_for_a_slot_that_frees_up_soon(self):
        from contextlib import AsyncExitStack
        async with AsyncExitStack() as stack:
            await self._hold(stack, main._ANALYSIS_MAX_PER_USER, lane=main._ANALYSIS_CHART_LANE)

            async def free_later():
                await asyncio.sleep(0.3)
                await stack.aclose()

            releaser = asyncio.create_task(free_later())
            async with main._bounded_analysis_work(UID, "chart preview", 3.0, main._ANALYSIS_CHART_LANE):
                pass                                                                    # got in after the wait, no 429
            await releaser

    async def test_a_full_chart_lane_still_answers_429_after_the_wait(self):
        from contextlib import AsyncExitStack
        async with AsyncExitStack() as stack:
            await self._hold(stack, main._ANALYSIS_MAX_PER_USER, lane=main._ANALYSIS_CHART_LANE)
            with self.assertRaises(HTTPException) as busy:
                async with main._bounded_analysis_work(UID, "chart preview", 0.2, main._ANALYSIS_CHART_LANE):
                    pass
            self.assertEqual(busy.exception.status_code, 429)
            self.assertIn("for this account", busy.exception.detail)

    async def test_the_chart_endpoints_use_the_chart_lane_and_wait(self):
        source = open(main.__file__).read()
        for operation in ('"chart analysis"', '"chart preview"', '"Screener chart analysis"'):
            self.assertIn(f"_bounded_analysis_work(", source)
            self.assertRegex(source, rf"_bounded_analysis_work\((?:eng|state)\.uid, {operation}, _ANALYSIS_CHART_QUEUE_TIMEOUT_SECONDS, _ANALYSIS_CHART_LANE\)")
        self.assertGreater(main._ANALYSIS_CHART_QUEUE_TIMEOUT_SECONDS, main._ANALYSIS_QUEUE_TIMEOUT_SECONDS)


if __name__ == "__main__":
    unittest.main()
