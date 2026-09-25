"""Bound synchronous Screener work beyond the lifetime of its async caller."""

import asyncio


class ScreenerWorkBusy(RuntimeError):
    """A previous attempt is still running; the caller should retry later."""


class ScreenerWorkTracker:
    def __init__(self):
        self.pending = {}

    async def run(self, key, semaphore, func, *args, timeout):
        # Callers never adopt a late result from an older profile snapshot.
        previous = self.pending.get(key)
        if previous is not None and not previous.done():
            raise ScreenerWorkBusy("Previous Screener operation is still running")
        started = False

        async def work():
            nonlocal started
            async with semaphore:
                started = True
                return await asyncio.to_thread(func, *args)

        task = asyncio.create_task(work())
        self.pending[key] = task

        def completed(done):
            if self.pending.get(key) is done:
                self.pending.pop(key, None)
            # Retrieve errors even when the requesting coroutine timed out.
            if not done.cancelled():
                done.exception()

        task.add_done_callback(completed)
        try:
            return await asyncio.wait_for(asyncio.shield(task), timeout=timeout)
        except (asyncio.TimeoutError, asyncio.CancelledError):
            # A queued job can be cancelled. A running thread cannot: retain
            # its semaphore permit and identity until it actually finishes.
            if not started:
                task.cancel()
            raise
