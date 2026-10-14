const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');

const read = (name) => fs.readFileSync(path.join(__dirname, '../frontend', name), 'utf8');

for (const [page, script, endpoint] of [['bots', 'bots.js', '/api/bots/reorder'], ['screener', 'screener.js', '/api/screener/reorder']]) {
    test(`${page}: Arrange uses the shared drag-and-drop module and no longer has the slow arrows or the mouse-only drag`, () => {
        const source = read(script);
        // gone: the up/down arrow buttons (each click waited for a save and a full refresh) and the HTML5 drag (mouse only)
        assert.doesNotMatch(source, /moveBotCard|arrangeControlsHtml|dragHandleHtml|handleDragStart|handleDragOver|handleDrop|draggedItem|dragPlaceholder|\.draggable\b/);
        // in: one handle per card in Arrange mode, attached once to the card list
        assert.match(source, /const reorderHandleHtml = botArrangeMode \? `[\s\S]*?data-reorder-handle[\s\S]*?` : '';/);
        assert.match(source, /\$\{reorderHandleHtml\}/);
        assert.match(source, /window\.CardReorder\.attach\(container, \{[\s\S]*?isEnabled: \(\) => botArrangeMode,[\s\S]*?onCommit: persistBotOrderFromVisible,[\s\S]*?onRevert:/);
        // a redraw never happens while a card is held or its order is being saved
        assert.match(source, /function applyBotsData\(data\) \{\s*if \(window\.CardReorder && window\.CardReorder\.isBusy\(\)\) \{/);
        // the save is one request on drop, with no forced snapshot refresh and no disabled page
        const persist = source.slice(source.indexOf('async function persistBotOrderFromVisible'), source.indexOf('\n}\n', source.indexOf('async function persistBotOrderFromVisible')));
        assert.ok(persist.includes(endpoint));
        assert.doesNotMatch(persist, /fetchBotsData|pointer-events-none|opacity-75/);
        assert.match(persist, /applyLocalBotOrder\(finalFullList\)/);          // the order changes on screen first
        assert.match(persist, /applyLocalBotOrder\(previousFull\);\s*throw err;/);   // and is put back, then reported, if the save fails
        // an in-flight poll cannot bring the old order back
        assert.match(source, /botsMasterTradelist = lockedBotOrder\(data\.tradelist\.slice\(\)\);/);
    });

    test(`${page}.html loads card-reorder.js before ${script}`, () => {
        const html = read(`${page}.html`);
        const module = html.indexOf('card-reorder.js?v=');
        assert.ok(module > 0 && module < html.indexOf(`${script}?v=`));
    });
}

test('the handle: a real button, finger-sized, drag starts only from it (touch-action none), keyboard reachable', () => {
    const css = read('styles.css');
    const rule = css.slice(css.indexOf('.card-reorder-handle {'), css.indexOf('}', css.indexOf('.card-reorder-handle {')));
    assert.ok(rule.includes('touch-action: none;'));
    assert.ok(rule.includes('min-width: 2.25rem;') && rule.includes('min-height: 2.25rem;'));
    const module = read('card-reorder.js');
    assert.match(module, /event\.target\.closest && event\.target\.closest\(handleSelector\)/);          // only the handle starts a drag
    assert.match(module, /event\.key !== 'ArrowUp' && event\.key !== 'ArrowDown'/);                         // keyboard
    assert.match(module, /pointerdown/); assert.match(module, /pointercancel/); assert.match(module, /Escape/);
    assert.match(module, /isBusy: \(\) => dragging \|\| committing > 0/);
});

test('a bot or profile that appears after the list was first shown goes on top; the first load keeps the server order', () => {
    const vm = require('node:vm');
    for (const file of ['bots.js', 'screener.js']) {
        const source = read(file);
        const grab = (name) => {
            const start = source.indexOf(`function ${name}(`);
            const open = source.indexOf(') {', start) + 2;
            let depth = 0, end = open;
            for (; end < source.length; end += 1) { if (source[end] === '{') depth += 1; if (source[end] === '}') { depth -= 1; if (depth === 0) break; } }
            return source.slice(start, end + 1);
        };
        const box = { botsPartitionSnapshots: { regular: null, options: null }, botsSharedSnapshot: null, botsMasterTradelist: [], ScreenerSnapshotState: { summary: () => ({}) } };
        vm.createContext(box);
        vm.runInContext(`${grab('mergeUniqueSnapshotItems')}\n${grab('composeBotsSnapshot')}`, box);
        const snap = (list) => ({ tradelist: list, botScans: {}, ticker_settings: {}, positions: [], pendingOrders: [] });
        const order = () => JSON.parse(vm.runInContext('JSON.stringify(composeBotsSnapshot().tradelist)', box));

        box.botsPartitionSnapshots.regular = snap(['A', 'B', 'C']);
        box.botsMasterTradelist = [];
        assert.deepEqual(order(), ['A', 'B', 'C'], `${file}: first load`);
        box.botsPartitionSnapshots.regular = snap(['D', 'A', 'B', 'C']);
        assert.deepEqual(order(), ['D', 'A', 'B', 'C'], `${file}: a new one goes on top`);
        box.botsPartitionSnapshots.options = snap(['E']);
        assert.deepEqual(order(), ['E', 'D', 'A', 'B', 'C'], `${file}: a new one in the other partition too`);
        box.botsMasterTradelist = ['B', 'A', 'E', 'D', 'C'];                       // an order the user arranged
        assert.deepEqual(order(), ['B', 'A', 'E', 'D', 'C'], `${file}: the arranged order is kept`);
        box.botsPartitionSnapshots.regular = snap(['A', 'B', 'C']);                // D was removed
        assert.deepEqual(order(), ['B', 'A', 'E', 'C'], `${file}: a removed one disappears`);
    }
});

test('Arrange: the whole card is the drag target, cards shrink to a header so long moves fit one screen, inner controls stay clickable', () => {
    const module = read('card-reorder.js');
    assert.match(module, /wholeCard/);
    assert.match(module, /ignoreSelector/);
    assert.match(module, /HOLD_MS/);   // touch needs a short hold, so ordinary scrolling still works
    const css = read('styles.css');
    assert.match(css, /#tradelistContainer\.arrange-compact \.watchlist-item > :not\(:first-child\)\s*\{\s*display: none !important;/);
    for (const script of ['bots.js', 'screener.js']) {
        assert.match(read(script), /arrange-compact/);
    }
});
