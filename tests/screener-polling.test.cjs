const test = require('node:test');
const assert = require('node:assert/strict');
const vm = require('node:vm');
const fs = require('node:fs');
const path = require('node:path');
const rules = require('../frontend/screener-snapshot-state.js');
const source = fs.readFileSync(path.join(__dirname, '../frontend/screener.js'), 'utf8');

// Execute the actual polling/composition functions. DOM rendering, auth and
// HTTP are test doubles; this is not an authenticated browser end-to-end test.
function harness() {
    const requests = [], renders = [], storage = new Map();
    const sandbox = {
        ScreenerSnapshotState: rules, AbortController, performance,
        console: { info() {}, warn() {}, error() {} },
        document: { getElementById: () => null },
        localStorage: { getItem: () => null },
        sessionStorage: { setItem: (key, value) => storage.set(key, value) },
        auth: { currentUser: { uid: 'alice' } },
        API_BASE: '', DASHBOARD_FETCH_TIMEOUT_MS: 12000,
        shouldRunBotsCardRefresh: () => true,
        getAuthHeaders: async () => ({}),
        fetchWithTimeout: () => new Promise((resolve, reject) => requests.push({ resolve, reject })),
        applyBotsData: value => renders.push(value),
        setTimeout: () => 0, clearTimeout() {}
    };
    sandbox.window = sandbox;
    vm.createContext(sandbox);
    vm.runInContext(`
        const botsPartitionSnapshots = { regular: null, options: null };
        const botsFetchState = Object.fromEntries(['all', 'regular', 'options'].map(key => [key, { fetchId: 0, promise: null, controller: null }]));
        let botsSharedSnapshot = null, botsMasterTradelist = [], lastObservedTradeEventSeq = null;
        let selectedTicker = null, crossPartitionRefreshTimerId = null;
    ` + source.slice(source.indexOf('let botsSnapshotFailCount = 0;'), source.indexOf('function applyBotsData(data)')), sandbox);
    return { sandbox, requests, renders, storage };
}
function snapshot(seq, symbols, partition = 'all', options = false) {
    return { schema_version: '1.0', snapshot_seq: seq, partition, tradelist: symbols,
        bot_scans: Object.fromEntries(symbols.map(symbol => [symbol, { evaluation_status: 'valid', data_status: 'current', direction: 'BULLISH' }])),
        ticker_settings: Object.fromEntries(symbols.map(symbol => [symbol, { enabled: true, options_trading: options }])),
        screener_summary: { monitoring: { status: 'healthy' } }
    };
}
function reply(request, data) {
    request.resolve({ ok: true, headers: { get: () => null }, json: async () => data });
}
const tick = () => new Promise(resolve => setImmediate(resolve));

test('superseded request cannot overwrite newer snapshot even if abort is ignored by transport', async () => {
    const h = harness();
    const old = h.sandbox.fetchBotsData({ force: true });
    await tick();
    const newer = h.sandbox.fetchBotsData({ force: true });
    await tick();
    reply(h.requests[1], snapshot(2, ['NEW']));
    await newer;
    reply(h.requests[0], snapshot(3, ['OLD']));
    assert.equal(await old, false);
    assert.equal(h.sandbox.lastBotsData.tradelist.join(), 'NEW');
    assert.equal(h.renders.length, 1);
});
test('user changes during HTTP request discard response and prevent cache writes', async () => {
    const h = harness();
    const pending = h.sandbox.fetchBotsData({ force: true });
    await tick();
    h.sandbox.auth.currentUser = { uid: 'bob' };
    reply(h.requests[0], snapshot(1, ['PRIVATE']));
    assert.equal(await pending, false);
    assert.equal(h.renders.length, 0);
    assert.equal(h.storage.size, 0);
});
test('account switch clears prior partition cards before new data arrives', async () => {
    const h = harness();
    let pending = h.sandbox.fetchBotsData({ force: true });
    await tick();
    reply(h.requests[0], snapshot(1, ['ALICE']));
    await pending;
    h.sandbox.auth.currentUser = { uid: 'bob' };
    pending = h.sandbox.fetchBotsData({ force: true });
    assert.equal(h.sandbox.lastBotsData, null);
    await tick();
    reply(h.requests[1], snapshot(2, ['BOB']));
    await pending;
    assert.equal(h.sandbox.lastBotsData.tradelist.join(), 'BOB');
    assert.ok(h.storage.has(rules.cacheKey('alice')));
    assert.ok(h.storage.has(rules.cacheKey('bob')));
});
test('real composition updates combined totals and rejects older full snapshots', () => {
    const h = harness();
    h.sandbox.ingestBotsSnapshot(snapshot(1, ['A'], 'regular'));
    h.sandbox.ingestBotsSnapshot(snapshot(3, ['B'], 'options', true));
    assert.equal(h.sandbox.lastBotsData.screener_summary.profiles.total, 2);
    assert.equal(h.sandbox.lastBotsData.screener_summary.setups.bullish, 2);
    assert.equal(h.sandbox.ingestBotsSnapshot(snapshot(2, [])), null);
    assert.equal(h.sandbox.lastBotsData.tradelist.length, 2);
    h.sandbox.ingestBotsSnapshot(snapshot(4, [], 'regular'));
    assert.equal(h.sandbox.lastBotsData.tradelist.join(), 'B');
});
test('timeout preserves historical analysis but removes current setup counts', async () => {
    const h = harness();
    let pending = h.sandbox.fetchBotsData({ force: true });
    await tick();
    reply(h.requests[0], snapshot(1, ['A']));
    await pending;
    pending = h.sandbox.fetchBotsData({ force: true });
    await tick();
    h.requests[1].reject(Object.assign(new Error('fixture timeout'), { name: 'TimeoutError' }));
    assert.equal(await pending, false);
    assert.equal(h.sandbox.lastBotsData.botScans.A.direction, 'BULLISH');
    assert.equal(h.sandbox.lastBotsData.botScans.A.data_status, 'stale_reference');
    assert.equal(h.sandbox.lastBotsData.screener_summary.setups.bullish, 0);
});

test('failed regular refresh cannot mark the healthy options partition stale', async () => {
    const h = harness();
    let pending = h.sandbox.fetchBotsData({ force: true });
    await tick();
    const both = snapshot(1, ['A', 'B']);
    both.ticker_settings.B.options_trading = true;
    reply(h.requests[0], both);
    await pending;
    pending = h.sandbox.fetchBotsData({ force: true, partition: 'regular' });
    await tick();
    h.requests[1].reject(new Error('provider unavailable'));
    await pending;
    assert.equal(h.sandbox.lastBotsData.botScans.A.data_status, 'stale_reference');
    assert.equal(h.sandbox.lastBotsData.botScans.B.data_status, 'current');
    assert.equal(h.sandbox.lastBotsData.screener_summary.setups.bullish, 1);
});
