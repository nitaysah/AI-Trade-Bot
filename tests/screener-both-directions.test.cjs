const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const read = (name) => fs.readFileSync(path.join(__dirname, '../frontend', name), 'utf8');

// Behaviour was checked in a real Chrome page with the real buildScreenerPolicyWatchSummary (see the session notes);
// these assertions keep the wording and the independence rules from being edited away.
test('Screener Watch summary reports both directions whenever Buy & Sell is watched', () => {
    const source = read('screener.js');
    assert.match(source, /Watching Buy & Sell: Buy \$\{sideState\(buy\)\}; Sell \$\{sideState\(sell\)\}\./);
    assert.match(source, /: bothSidesText \? bothSidesText\s*\n\s*: phase === 'EXIT'/);
    assert.match(source, /phase === 'EXIT' && !bothDirectionWatch/);
});

test('Screener calls its virtual position a tracked alert position, never an open (Bots) position', () => {
    const evidence = read('screener-evidence.js');
    assert.match(evidence, /ctx\.bot \? 'Open position' : 'Tracked alert position'/);
    assert.doesNotMatch(read('screener.js'), /'Open position levels'/);
});

test('Screener page code never reads the Bots engine snapshot or positions endpoint', () => {
    for (const file of ['screener.js', 'screener-snapshot-state.js', 'screener-live-price.js', 'screener-evidence.js']) {
        assert.doesNotMatch(read(file), /\/api\/bots\//, file);
    }
});
