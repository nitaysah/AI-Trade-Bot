const test = require('node:test');
const assert = require('node:assert/strict');
const rules = require('../frontend/screener-snapshot-state.js');

const scan = (direction, status = 'current') => ({ evaluation_status: 'valid', direction, data_status: status });
test('composed counts include both partitions and exclude stale, paused, pending and closed-session scans', () => {
    const result = rules.summary({
        tradelist: ['A', 'B', 'C', 'D', 'E', 'F', 'G'],
        botScans: { A: scan('BULLISH'), B: scan('BEARISH'), C: scan('BULLISH', 'degraded'), D: scan('NEUTRAL'), E: {}, F: scan('BULLISH', 'session_closed'), G: scan('NEUTRAL') },
        ticker_settings: { D: { enabled: false } },
        screener_summary: { profiles: { total: 1 }, setups: { bullish: 42 }, alerts_today: 9, monitoring: { status: 'healthy' } }
    });
    assert.deepEqual(result.profiles, { total: 7, enabled: 6, paused: 1 });
    assert.deepEqual(result.setups, { bullish: 1, bearish: 1, neutral: 1, valid: 3, unavailable: 3, degraded: 1, attention: 2 });
    assert.equal(result.alerts_today, 9);
    assert.equal(result.monitoring.status, 'degraded');
});
test('old partition cannot overwrite newer scan or resurrect a deleted profile', () => {
    assert.equal(rules.accepts({ partition: 'regular', snapshot_seq: 1 }, { regular: { snapshot_seq: 2, tradelist: [] } }), false);
    assert.equal(rules.accepts({ partition: 'regular', snapshot_seq: 3 }, { regular: { snapshot_seq: 2 } }), true);
});
test('full snapshot must be at least as new as both partition snapshots', () => {
    assert.equal(rules.accepts({ partition: 'all', snapshot_seq: 10 }, { regular: { snapshot_seq: 9 }, options: { snapshot_seq: 11 } }), false);
    assert.equal(rules.accepts({ partition: 'all', snapshot_seq: 12 }, { regular: { snapshot_seq: 9 }, options: { snapshot_seq: 11 } }), true);
});
test('a newer other partition does not suppress independent partition updates', () => {
    assert.equal(rules.accepts({ partition: 'regular', snapshot_seq: 10 }, { regular: { snapshot_seq: 9 }, options: { snapshot_seq: 11 } }), true);
});
test('snapshot caches are separated by user identity; anonymous cache is disabled', () => {
    assert.notEqual(rules.cacheKey('alice'), rules.cacheKey('bob'));
    assert.equal(rules.cacheKey(null), null);
    assert.notEqual(rules.cacheKey('alice'), 'screenerDataCache');
});
test('cached snapshot is historical, not a current setup, and source remains unchanged', () => {
    const source = { tradelist: ['A'], bot_scans: { A: scan('BULLISH') } };
    const cached = rules.asReference(source);
    assert.equal(cached.botScans.A.direction, 'BULLISH');
    assert.equal(rules.summary(cached).setups.bullish, 0);
    assert.equal(rules.needsAttention(cached.botScans.A, {}), true);
    assert.equal(source.bot_scans.A.data_status, 'current');
});
test('waiting is not neutral, while paused and closed-session scans are not data errors', () => {
    assert.equal(rules.isCurrent({ direction: 'NEUTRAL' }), false);
    assert.equal(rules.needsAttention({}, {}), true);
    assert.equal(rules.needsAttention({}, { enabled: false }), false);
    assert.equal(rules.needsAttention(scan('BULLISH', 'session_closed'), {}), false);
});
