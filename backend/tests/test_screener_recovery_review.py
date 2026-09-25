import asyncio
import copy
import threading
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import AsyncMock, Mock, patch

import main
import trader
from screener_service import ScreenerUserState
from screener_tasks import ScreenerWorkBusy, ScreenerWorkTracker
from user_config import UserConfig, active_evaluation_context


class WorkerLifetimeTests(unittest.IsolatedAsyncioTestCase):
    async def test_timeout_retains_permit_and_blocks_duplicate_until_thread_finishes(self):
        tracker, gate = ScreenerWorkTracker(), asyncio.Semaphore(1)
        started, release = threading.Event(), threading.Event()
        def blocked():
            started.set()
            release.wait(2)
            return 'late'
        try:
            with self.assertRaises(asyncio.TimeoutError):
                await tracker.run('symbol', gate, blocked, timeout=0.05)
            self.assertTrue(started.is_set())
            self.assertTrue(gate.locked())
            with self.assertRaises(ScreenerWorkBusy):
                await tracker.run('symbol', gate, lambda: 'duplicate', timeout=1)
        finally:
            release.set()
            await asyncio.gather(*list(tracker.pending.values()), return_exceptions=True)
        self.assertFalse(gate.locked())
        self.assertEqual(await tracker.run('symbol', gate, lambda: 'new', timeout=1), 'new')

    async def test_queued_timeout_never_launches_late_provider_work(self):
        tracker, gate, provider = ScreenerWorkTracker(), asyncio.Semaphore(0), Mock()
        with self.assertRaises(asyncio.TimeoutError):
            await tracker.run('queued', gate, provider, timeout=0.01)
        gate.release()
        await asyncio.sleep(0)
        provider.assert_not_called()

    async def test_caller_cancellation_does_not_release_running_thread_permit(self):
        tracker, gate = ScreenerWorkTracker(), asyncio.Semaphore(1)
        started, release = threading.Event(), threading.Event()
        def blocked():
            started.set()
            release.wait(2)
        caller = asyncio.create_task(tracker.run('cancelled', gate, blocked, timeout=1))
        try:
            self.assertTrue(await asyncio.to_thread(started.wait, 1))
            caller.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await caller
            self.assertTrue(gate.locked())
        finally:
            release.set()
            await asyncio.gather(*list(tracker.pending.values()), return_exceptions=True)


class EvaluationIdentityTests(unittest.TestCase):
    def test_position_and_context_have_separate_cache_identity(self):
        config = UserConfig('cache-review')
        def key(position):
            return trader._evaluation_cache_key(config, 'BTCUSD', '5MIN', 'webull', True, 1000, 1000, position)
        token = active_evaluation_context.set('screener')
        try:
            flat = key(False)
            self.assertNotEqual(flat, key(True))
            active_evaluation_context.set('bots')
            self.assertNotEqual(flat, key(False))
        finally:
            active_evaluation_context.reset(token)

    def test_full_orchestration_pool_can_complete_nested_calculations(self):
        config = UserConfig('pool-review')
        config.ENABLE_AI_SENTIMENT = False
        class NoNestedExecutor(ThreadPoolExecutor):
            def submit(self, *args, **kwargs):
                # Fail immediately if this regresses, rather than deadlocking
                # the test runner during executor shutdown.
                if threading.current_thread().name.startswith('orchestration-review'):
                    raise AssertionError('Nested work submitted to orchestration pool')
                return super().submit(*args, **kwargs)
        with NoNestedExecutor(2, thread_name_prefix='orchestration-review') as orchestration, ThreadPoolExecutor(2) as calculation, ThreadPoolExecutor(6) as callers:
            with patch.object(trader, 'executor', orchestration), patch.object(trader, '_calculation_executor', calculation), patch.object(trader, 'get_user_config', return_value=config), patch.object(trader, 'get_full_analysis', return_value=None) as provider:
                futures = [callers.submit(trader.evaluate_trade, f'TEST{i}', timeframe='5Min', ignore_cache=True) for i in range(6)]
                results = [future.result(timeout=3) for future in futures]
                self.assertEqual(len(results), 6)
                self.assertEqual(provider.call_count, 6)


class PersistenceAndDeliveryTests(unittest.IsolatedAsyncioTestCase):
    def make_state(self, uid):
        state = ScreenerUserState(uid, None)
        state.profiles = {'BTCUSD': {'enabled': True, 'email_enabled': True}}
        state.order = ['BTCUSD']
        state.notification_settings.update(email_enabled=True, sms_enabled=True)
        state.notifications = [{'id': 'n1', 'symbol': 'BTCUSD', 'direction': 'BULLISH', 'created_at': time.time(), 'reason': 'fixture', 'price': 100}]
        state.email_outbox = [{'id': 'e1', 'notification_id': 'n1', 'status': 'queued'}]
        state.sms_outbox = [{'id': 's1', 'notification_id': 'n1', 'status': 'queued', 'to': '+12025550123'}]
        return state

    async def test_disk_flush_runs_off_event_loop_and_sqlite_failure_is_reported(self):
        state = self.make_state('save-review')
        loop_thread = threading.get_ident()
        disk_threads = []
        with patch('screener_service.os.fsync', side_effect=lambda _: disk_threads.append(threading.get_ident())), patch('local_store.save_state', side_effect=OSError('test disk unavailable')):
            self.assertFalse(await state.save())
        self.assertTrue(disk_threads)
        self.assertTrue(all(value != loop_thread for value in disk_threads))
        self.assertEqual(state.health['persistence_status'], 'degraded')

    async def test_delivery_refuses_email_and_sms_without_durable_claim(self):
        for channel in ('email', 'sms'):
            with self.subTest(channel=channel):
                state = self.make_state(f'failed-claim-{channel}')
                state.save = AsyncMock(return_value=False)
                with patch.object(main, '_smtp_settings', return_value={'host': 'fixture', 'from_email': 'test@example.invalid'}), patch.object(main, '_verified_email_for_user', new=AsyncMock(return_value='test@example.invalid')), patch.object(main, '_sms_provider_configured', return_value=True), patch.object(main, f'_send_screener_{channel}_sync') as sender:
                    await getattr(main, f'_process_screener_{channel}_outbox_body')(state)
                sender.assert_not_called()
                self.assertEqual(getattr(state, f'{channel}_outbox')[0]['error'], 'durable_claim_failed')

    async def test_delivery_persists_claim_then_result_for_each_channel(self):
        for channel in ('email', 'sms'):
            with self.subTest(channel=channel):
                state = self.make_state(f'ordered-claim-{channel}')
                sequence = []
                async def save():
                    sequence.append(getattr(state, f'{channel}_outbox')[0]['status'])
                    return True
                state.save = save
                with patch.object(main, '_smtp_settings', return_value={'host': 'fixture', 'from_email': 'test@example.invalid'}), patch.object(main, '_verified_email_for_user', new=AsyncMock(return_value='test@example.invalid')), patch.object(main, '_sms_provider_configured', return_value=True), patch.object(main, f'_send_screener_{channel}_sync', side_effect=lambda *args: sequence.append('dispatch')):
                    await getattr(main, f'_process_screener_{channel}_outbox_body')(state)
                self.assertEqual(sequence[:3], ['sending', 'dispatch', 'sent'])

    async def test_restart_recovers_expired_lease_but_does_not_resend_acknowledged_item(self):
        state = self.make_state('restart-review')
        state.email_outbox[0].update(status='sending', lease_until=time.time() - 1)
        state.email_outbox.append({'id': 'sent', 'status': 'sent'})
        self.assertTrue(await state.save())
        restored = ScreenerUserState(state.uid, None)
        await restored.load()
        main._reclaim_expired_screener_leases(restored.email_outbox, time.time())
        self.assertEqual(restored.email_outbox[0]['status'], 'retrying')
        self.assertEqual(restored.email_outbox[1]['status'], 'sent')

    async def test_sender_failure_persists_bounded_retry(self):
        state = self.make_state('retry-review')
        persisted = []
        async def save():
            persisted.append(copy.deepcopy(state.sms_outbox[0]))
            return True
        state.save = save
        with patch.object(main, '_sms_provider_configured', return_value=True), patch.object(main, '_send_screener_sms_sync', side_effect=TimeoutError()):
            await main._process_screener_sms_outbox_body(state)
        self.assertEqual(persisted[0]['status'], 'sending')
        self.assertEqual(persisted[1]['status'], 'retrying')
        self.assertGreater(persisted[1]['next_attempt_at'], time.time())


class ObsoleteQuoteTests(unittest.IsolatedAsyncioTestCase):
    async def test_profile_changes_during_quote_cannot_record_virtual_entry(self):
        for change in ('disabled', 'revision', 'generation', 'context', 'deleted'):
            with self.subTest(change=change):
                state = ScreenerUserState(f'quote-review-{change}', None)
                state.profiles = {'BTCUSD': {'enabled': True, 'buy_mode': 'custom_price', 'custom_buy_price': 100}}
                state.order = ['BTCUSD']
                state.evaluation_state['BTCUSD'] = {'generation': '1'}
                state.save = AsyncMock(return_value=True)
                state.refresh_from_persistence = AsyncMock(return_value=False)
                state.try_acquire_process_scan_lock = AsyncMock(return_value=True)
                state.release_process_scan_lock = AsyncMock()
                state.commit_price_entry = Mock()
                async def quote(*args, **kwargs):
                    if change == 'disabled': state.profiles['BTCUSD']['enabled'] = False
                    if change == 'revision': state.profiles['BTCUSD']['custom_buy_price'] = 99
                    if change == 'generation': state.evaluation_state['BTCUSD']['generation'] = '2'
                    if change == 'context': state.config.contexts['screener']['parameters']['RSI_PERIOD'] = 20
                    if change == 'deleted': state.profiles.clear()
                    return {'price': 90, 'stale': False}
                with patch.object(main.screener_manager.work_tracker, 'run', side_effect=quote):
                    await main._watch_screener_price_levels_state(state)
                state.commit_price_entry.assert_not_called()
                self.assertEqual(state.notifications, [])
