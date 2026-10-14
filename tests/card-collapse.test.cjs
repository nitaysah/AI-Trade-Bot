const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const read = (name) => fs.readFileSync(path.join(__dirname, '../frontend', name), 'utf8');

// Rendered and clicked in Chrome at 390px and 1200px with mocked snapshots; these keep the wiring from being edited away.
test('Bots: the card chevron is shown on desktop too, and collapsing keeps the Bots summary box', () => {
    assert.doesNotMatch(read('bots.js'), /bot-card-chevron sm:hidden/);
    const css = read('styles.css');
    assert.doesNotMatch(css, /@media \(max-width: 767px\) \{\s*\.active-bot\.bot-card-collapsed/);
    assert.match(css, /\.active-bot\.bot-card-collapsed \[data-bots-scan-card\] > :not\(\[data-bots-watch-summary\]\) \{ display: none; \}/);
});

test('Screener: every Watch summary variant is marked, the chevron exists, and the collapsed state is remembered per ticker', () => {
    const source = read('screener.js');
    assert.equal((source.match(/<section data-card-summary class="mt-1 rounded-lg border/g) || []).length, 3);
    assert.match(source, /toggleScreenerCardBody\('\$\{ticker\}', this\)/);
    assert.match(source, /screenerCardCollapsed:v1/);
    assert.match(source, /item\.classList\.toggle\('screener-card-collapsed', isScreenerCardCollapsed\(ticker\)\);/);
    assert.match(read('styles.css'), /screener-card-collapsed > :not\(:first-child\):not\(\[data-card-summary\]\) \{ display: none; \}/);
});

test('Screener and Bots keep separate collapse stores', () => {
    assert.match(read('bots.js'), /botsCardCollapsed:v1/);
    assert.doesNotMatch(read('screener.js'), /botsCardCollapsed/);
});
