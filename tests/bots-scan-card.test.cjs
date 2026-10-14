const test = require('node:test');
const assert = require('node:assert/strict');
const vm = require('node:vm');
const fs = require('node:fs');
const path = require('node:path');

const read = (name) => fs.readFileSync(path.join(__dirname, '../frontend', name), 'utf8');
const logs = JSON.parse(fs.readFileSync(path.join(__dirname, 'fixtures/bots_scan_logs.json'), 'utf8'));   // real _build_scan_log output
const alloc = JSON.parse(fs.readFileSync(path.join(__dirname, 'fixtures/bots_allocation_logs.json'), 'utf8'));
const strat = JSON.parse(fs.readFileSync(path.join(__dirname, 'fixtures/bots_strategy_logs.json'), 'utf8'));

function sandbox() {
    const box = {
        console, localStorage: { getItem: () => null, setItem() {} }, Date,
        sanitizePlainText: (t) => String(t).replace(/</g, '&lt;'),
        formatAssetPrice: (v) => Number(v).toFixed(2),
    };
    box.window = box;
    vm.createContext(box);
    vm.runInContext(read('screener-evidence.js'), box);
    vm.runInContext(read('strategy-log.js'), box);
    vm.runInContext(read('bots-summary.js'), box);
    vm.runInContext(read('bots-scan-card.js'), box);
    return box;
}
const countdown = '<span class="scan-countdown" data-rendered-at="1" data-refresh-rate="60" data-initial-remaining="30">T</span>';
const render = (box, log, extra = {}) => box.BotsScanCard.render({
    ticker: 'SPY', scanLog: log, closed: false, statusClass: () => 'x', statusText: log.status, decisionBadgeHtml: '',
    timeAgoText: countdown, exitWatch: null, isCrypto: false,
    manual: { summaryText: 'summary text', watchHtml: '<i>old</i>' }, ...extra,
});

test('flat bot: Buy Evidence active, Sell Evidence is a note, one status badge', () => {
    const html = render(sandbox(), logs.flat_waiting);
    assert.match(html, /Buy Evidence/);
    assert.match(html, /Starts after an entry\./);
    assert.match(html, /data-evidence-side="buy" data-evidence-status="WAITING"/);
    assert.match(html, /data-bots-watch-summary="WAITING"/);
    assert.doesNotMatch(html, /alerts (on|off)/);
});

test('confirmed entry turns the watch summary green and the badge Confirmed', () => {
    const html = render(sandbox(), logs.flat_confirmed);
    assert.match(html, /data-bots-watch-summary="CONFIRMED"/);
    assert.match(html, /border-emerald-200 bg-emerald-50\/70/);
    assert.match(html, />Confirmed</);
});

test('guard reason and pending order show as Blocked', () => {
    const guard = render(sandbox(), logs.flat_guard);
    assert.match(guard, /data-evidence-status="BLOCKED"/);
    assert.match(guard, /Liquidity filter: spread/);
    const pending = render(sandbox(), logs.pending);
    assert.match(pending, />Order pending</);
    assert.match(pending, /Order pending\./);
});

test('holding bot: Sell Evidence active with the real stop and target, Buy resumes after exit', () => {
    const html = render(sandbox(), logs.holding, { exitWatch: { stop_loss: 94.5, take_profit: 112, trail_enabled: true } });
    assert.match(html, /Resumes after the exit\./);
    assert.match(html, /data-evidence-side="sell" data-evidence-status="ACTIVE"/);
    assert.match(html, /stop \$94\.50 · target \$112\.00 · trailing on/);
    assert.match(html, /not a broker-side stop/);
    assert.match(html, />Holding</);
});

test('the countdown element keeps its class and data attributes', () => {
    const html = render(sandbox(), logs.flat_waiting);
    assert.match(html, /class="scan-countdown" data-rendered-at="1" data-refresh-rate="60" data-initial-remaining="30"/);
});

test('a log from an older server (no lifecycle field) still gets Buy / Sell Evidence', () => {
    const old = { ...logs.flat_waiting };
    delete old.lifecycle; delete old.entry_guard; delete old.next_due_at; delete old.setup_config;
    const html = render(sandbox(), old);
    assert.match(html, /Buy Evidence/);
    assert.match(html, /Sell Evidence/);
    assert.doesNotMatch(html, /Entry setup watch|Exit setup watch/);
    const holding = { ...logs.holding }; delete holding.lifecycle;
    assert.match(render(sandbox(), holding), /data-evidence-side="sell" data-evidence-status="ACTIVE"/);
});

test('market closed: same card with a Market closed badge and the last scan under it', () => {
    const closed = { status: 'MARKET_CLOSED', last_scan: logs.flat_waiting, setup: logs.flat_waiting.setup };
    const html = render(sandbox(), closed, { closed: true, closedBannerHtml: '<div id="banner">closed</div>' });
    assert.match(html, /Market <\/span>Closed</);   // "Market closed" on desktop, "Closed" on phones
    assert.match(html, /Buy Evidence/);
    assert.match(html, /id="banner"/);
    assert.doesNotMatch(html, /Entry setup watch|Exit setup watch/);
});

test('market closed before any scan: still the same card, saying no scan yet', () => {
    const html = render(sandbox(), { status: 'MARKET_CLOSED', last_scan: null, setup: logs.flat_waiting.setup }, { closed: true });
    assert.match(html, /Buy Evidence/);
    assert.match(html, /Sell Evidence/);
    assert.match(html, /No scan result yet/);
    assert.doesNotMatch(html, /Entry setup watch|Exit setup watch/);
});

test('Malik allocation bot: both sides shown, with targets vs held and the daily execution time', () => {
    const html = render(sandbox(), alloc.malik);
    assert.match(html, /Buy Evidence/);
    assert.match(html, /Sell Evidence/);
    assert.match(html, /TQQQ target 100%/);
    assert.match(html, /Executes daily:/);
    assert.doesNotMatch(html, /Starts after an entry|Resumes after the exit/);
    assert.doesNotMatch(html, /Entry setup watch|Exit setup watch|Regime & targets/);
});

test('Screener output is unchanged by the Bots mode (alerts badge still present)', () => {
    const box = sandbox();
    const out = box.ScreenerEvidence.build({
        source: 'indicators', settings: { buy_mode: 'indicator', sell_mode: 'indicator' }, counts: { bullHave: 1, bullNeed: 2, bearHave: 0, bearNeed: 2 },
        indicators: [], technical: {}, watch: 'BOTH', scanLog: {}, scan: {},
    });
    assert.match(out.html, /Buy alerts on/);
    assert.match(out.html, /Sell alerts on/);
});

test('each side shows its own setup label, active or not', () => {
    const html = render(sandbox(), logs.holding);
    const buy = html.split('data-evidence-side="buy"')[1].split('data-evidence-side="sell"')[0];
    const sell = html.split('data-evidence-side="sell"')[1];
    assert.match(buy, new RegExp(logs.holding.setup.entry.replace(/[.*+?^${}()|[\]\\]/g, '\\$&'), 'i'));
    assert.match(sell, new RegExp(logs.holding.setup.exit.replace(/[.*+?^${}()|[\]\\]/g, '\\$&'), 'i'));
});

function setupSandbox() {
    const box = sandbox();
    vm.runInContext(read('bots-setup-watch.js'), box);
    box.STRATEGY_HELPER_TEXTS = { entry: { hybrid_atr: '💡 Hybrid entry text' }, exit: { sltp: '💡 Fixed ATR exit text' } };
    return box;
}
const settings = { buy_mode: 'hybrid_atr', sell_mode: 'sltp' };

test('Entry/Exit Setup shows both Buy and Sell setup with the configured numbers', () => {
    const box = setupSandbox();
    const html = box.BotsSetupWatch.render({ ticker: 'SPY', setup: logs.flat_waiting.setup, config: logs.flat_waiting.setup_config, settings, isOptions: false, hasPosition: false });
    assert.match(html, /Entry\/Exit Setup/);
    assert.match(html, /Entry Setup/);
    assert.match(html, /Exit Setup/);
    assert.doesNotMatch(html, /Bullish \/ Buy Setup|Bearish \/ Sell Setup/);
    assert.match(html, /Indicators Config: RSI, MACD \(2 of 2 bullish confirmations needed\)/);
    assert.match(html, /ATR config: 1\.5× volatility/);
    assert.match(html, /ATR config: SL 2× · TP 4× · trail 3× \(on\)/);
    assert.match(html, /Hybrid entry text/);
    assert.match(html, /Fixed ATR exit text/);
    assert.match(html, /aria-expanded="false"/);       // collapsed by default, like the Screener
    assert.doesNotMatch(html, /Active position/);
});

test('Entry/Exit Setup flags an open position and escapes text', () => {
    const box = setupSandbox();
    const html = box.BotsSetupWatch.render({ ticker: 'SPY', setup: { kind: 'manual', entry: '<b>x</b>', exit: 'y' }, config: { entry: ['<i>z</i>'], exit: [] }, settings, hasPosition: true });
    assert.doesNotMatch(html, /Active position/);   // the badge moved to the Last scan result header
    assert.doesNotMatch(html, /<b>x<\/b>|<i>z<\/i>/);
});

test('Strategy bots use the strategy definition; a missing definition falls back to the rule text', () => {
    const box = setupSandbox();
    box.StrategyPreset = { describe: () => ({ label: 'Golden Cross', summary: 'Trend follow', entry_rule: 'Fast crosses above slow', exit_rule: 'Fast falls below slow', timeframes: ['1Day'], sessions: [], variant: { label: 'Classic', description: '50/200', warmup_bars: 202 } }) };
    const setup = { kind: 'strategy', strategy: 'Golden Cross', entry: 'e', exit: 'x' };
    const html = box.BotsSetupWatch.render({ ticker: 'SPY', setup, strategyInfo: { preset_id: 'golden_cross' } });
    assert.match(html, /Fast crosses above slow/);
    assert.match(html, /Fast falls below slow/);
    assert.match(html, /Strategy/);
    box.StrategyPreset = { describe: () => null };
    assert.match(box.BotsSetupWatch.render({ ticker: 'SPY', setup }), /Entry:<\/span> e/);
});

test('no setup data renders nothing', () => {
    assert.equal(setupSandbox().BotsSetupWatch.render({ ticker: 'SPY', setup: null }), '');
});

test('Session Rider flat: Schedule + Waiting badges, Buy Evidence with the buy time, Sell Evidence with the sell time', () => {
    const html = render(sandbox(), strat.sr_flat);
    assert.doesNotMatch(html.slice(html.indexOf('Last scan result'), html.indexOf('id="bots-scan-log-SPY"')), />(Schedule|Strategy)</);   // the source badge lives in the Entry/Exit Setup
    assert.match(html, />Waiting</);
    assert.match(html, /data-evidence-side="buy" data-evidence-status="WAITING"/);
    assert.match(html, /Buy time:<\/strong> .*CT/);
    assert.match(html, /Sell time:<\/strong> .*CT/);
    assert.match(html, /Starts after an entry\./);
    assert.doesNotMatch(html, /Entry setup watch|Exit setup watch/);
    assert.ok(html.indexOf('Buy Evidence') < html.indexOf('Bots summary'));   // evidence first, Bots summary last, like the Screener
});

test('Session Rider holding: Sell Evidence is the active side and the badge says Holding', () => {
    const html = render(sandbox(), strat.sr_holding);
    assert.match(html, />Holding</);
    assert.match(html, /data-evidence-side="sell" data-evidence-status="ACTIVE"/);
    assert.match(html, /Resumes after the exit\./);
    assert.match(html, /Scheduled sell/);
});

test('signal strategy: flat shows entry rule and numbers; holding shows the live exit rows', () => {
    const flat = render(sandbox(), strat.gc_flat);
    assert.doesNotMatch(flat.slice(flat.indexOf('Last scan result'), flat.indexOf('id="bots-scan-log-SPY"')), />(Schedule|Strategy)</);
    assert.match(flat, /data-evidence-side="buy"/);
    assert.match(flat, /Entry:<\/strong>/);
    const held = render(sandbox(), strat.st_holding);
    assert.match(held, /data-evidence-side="sell" data-evidence-status="ACTIVE"/);
    assert.match(held, /Protective stop/);
    assert.match(held, /not a broker order/);
});

test('exit levels are not repeated when TP/SL rows already show them', () => {
    const log = { ...logs.holding, live_levels: ['TP $557.13 (2.06% above)', 'SL $535.37 (1.93% below)'] };
    const html = render(sandbox(), log, { exitWatch: { stop_loss: 535.37, take_profit: 557.13, trail_enabled: true } });
    assert.match(html, /TP \$557\.13 \(2\.06% above\)/);
    assert.match(html, /SL \$535\.37 \(1\.93% below\)/);
    const sellSection = html.split('data-evidence-side="sell"')[1].split('aria-label="Bots summary"')[0];
    assert.doesNotMatch(sellSection, /Exit levels:/);                                  // the evidence section does not repeat them
    assert.match(html, /Trailing stop:<\/strong> on/);
    assert.match(html, /not a broker-side stop/);
});

const schedule = { mode: 'Intraday', buy_label: 'Open + 1 min · 8:31 AM CT', sell_label: 'Close − 5 min · 2:55 PM CT', skip_weekend: false };

test('Session Rider setup shows the bot\'s own times and drops the "default" wording', () => {
    const box = setupSandbox();
    box.StrategyPreset = { describe: () => ({ label: 'Session Rider', summary: 's', entry_rule: 'Market buy at the Buy time (default 5 minutes before the close). Only inside a 5-minute window.', exit_rule: 'Market sell at the Sell time (default 5 minutes after the next open).', timeframes: [], sessions: [], variant: null }) };
    const html = box.BotsSetupWatch.render({ ticker: 'AAPL', setup: { kind: 'strategy', strategy: 'Session Rider' }, strategyInfo: { preset_id: 'session_rider' }, schedule });
    assert.match(html, /Your buy time:<\/span> Open \+ 1 min · 8:31 AM CT/);
    assert.match(html, /Your sell time:<\/span> Close − 5 min · 2:55 PM CT/);
    assert.match(html, /Mode:<\/span> Intraday/);
    assert.doesNotMatch(html, /default 5 minutes/);
});

test('Session Rider card with no scan yet still shows the bot\'s buy and sell times', () => {
    const html = render(sandbox(), { status: 'MARKET_CLOSED', last_scan: null, setup: { kind: 'strategy', strategy: 'Session Rider', entry: 'e', exit: 'x' } }, { closed: true, schedule });
    assert.match(html, /Buy time:<\/strong> Open \+ 1 min · 8:31 AM CT/);
    assert.match(html, /Sell time:<\/strong> Close − 5 min · 2:55 PM CT/);
    assert.match(html, /Buy Open \+ 1 min · 8:31 AM CT · Sell Close − 5 min/);
});

test('the schedule view of a live Session Rider scan no longer repeats the "default" wording', () => {
    const html = render(sandbox(), strat.sr_flat);
    assert.doesNotMatch(html, /default 5 minutes/);
    assert.match(html, /Buy time:<\/strong>/);
});

test('no-scan card for a strategy bot labels its sections with the short strategy name', () => {
    const html = render(sandbox(), { status: 'MARKET_CLOSED', last_scan: null, setup: { kind: 'strategy', strategy: 'Session Rider · Scheduled buy and sell', entry: 'A long rule text that must not be a chip', exit: 'x' } }, { closed: true, schedule });
    assert.doesNotMatch(html, /text-slate-700">A long rule text/);
    assert.match(html, /text-slate-700">Session Rider<\/span>/);
});

test('strategy bots show neither the Strategy setup badge nor "Untested settings"; the research warning stays', () => {
    const view = { ...strat.gc_flat.strategy_log, untested: 'Intraday timeframes (15Min) were not backtested.' };
    const html = render(sandbox(), { ...strat.gc_flat, strategy_log: view });
    assert.doesNotMatch(html, /Strategy · Golden Cross/);
    assert.doesNotMatch(html, /Untested settings/);
    const failed = render(sandbox(), { ...strat.gc_flat, strategy_log: { ...strat.gc_flat.strategy_log, untested: 'Did not pass research: x' } });
    assert.match(failed, /Did not pass research/);
    assert.doesNotMatch(render(sandbox(), logs.flat_waiting), /Entry · |Exit · /);   // and manual bots no longer show Entry / Exit badges
});

test('market closed looks like the Screener: MARKET CLOSED badge, ⏸ Paused chip, scan time and the closed summary', () => {
    const lastScan = { ...logs.flat_waiting, updated_at: '2026-10-13T14:22:54-05:00' };
    const closed = { status: 'MARKET_CLOSED', last_scan: lastScan, setup: lastScan.setup, updated_at: '2026-10-13T14:22:54-05:00',
        summary: '🛑 Regular Market Closed. Starts scanning 2m before open (Wednesday at 8:30 AM CT).' };
    const html = render(sandbox(), closed, { closed: true, timeAgoText: '<span class="bots-closed-countdown">NEXT SCAN IN 15H25M</span>', closedBannerHtml: '' });
    assert.match(html, /Market <\/span>Closed</);   // "Market closed" on desktop, "Closed" on phones
    assert.match(html, /⏸ Paused/);
    assert.doesNotMatch(html, /NEXT SCAN IN|bots-closed-countdown/);                       // the Screener shows Paused, not a countdown
    assert.match(html, /Last closed-candle scan at Oct 13, 2026, 2:22:54 PM CDT/);
    assert.match(html, /🛑 Regular Market Closed\. Starts scanning 2m before open \(Wednesday at 8:30 AM CT\)\./);
    assert.doesNotMatch(html, /Scanning resumes automatically|Starts scanning in /);
    const order = ['Market </span>Closed', 'Last closed-candle scan at', '🛑 Regular Market Closed', 'Buy Evidence', 'Bots summary'].map((text) => html.indexOf(text));
    assert.deepEqual(order, [...order].sort((a, b) => a - b));                             // same order as the Screener card
    assert.ok(order.every((index) => index >= 0));
});

test('an open-market card also carries the Screener\'s "Last closed-candle scan at" line', () => {
    const html = render(sandbox(), { ...logs.flat_waiting, updated_at: '2026-10-13T14:22:54-05:00' });
    assert.match(html, /Last closed-candle scan at Oct 13, 2026, 2:22:54 PM CDT/);
    assert.doesNotMatch(html, /⏸ Paused/);
});

test('no scan yet says so, like the Screener', () => {
    const html = render(sandbox(), { status: 'MARKET_CLOSED', last_scan: null, setup: logs.flat_waiting.setup }, { closed: true });
    assert.match(html, /First completed-candle scan pending/);
});

test('the compact closed-market countdown formats like the Screener (15H25M, 25M10S, 40S)', () => {
    const source = read('bots.js');
    const start = source.indexOf('function formatClosedCountdown');
    const fn = source.slice(start, source.indexOf('\n}\n', start) + 2);
    const box = {};
    vm.createContext(box);
    vm.runInContext(fn + '; this.f = formatClosedCountdown;', box);
    assert.equal(box.f(15 * 3600 + 25 * 60 + 20), '15H25M');
    assert.equal(box.f(25 * 60 + 10), '25M10S');
    assert.equal(box.f(40), '40S');
    assert.equal(box.f(-5), '0S');
});

test('the Bots scan timer keeps the Screener chip; the label is "Next scan in" on desktop and "Next" on phones, in the first render and every tick', () => {
    const source = read('bots.js');
    assert.match(source, /class="scan-countdown inline-flex items-center gap-0\.5 whitespace-nowrap rounded border border-indigo-200 bg-indigo-50 px-1 sm:px-1\.5 py-0\.5 text-\[0\.44rem\] sm:text-\[0\.48rem\] font-black uppercase tracking-normal sm:tracking-wide text-indigo-700"/);
    const label = '\\u25f7 <span class=\\"hidden sm:inline\\">Next scan in</span><span class=\\"sm:hidden\\">Next</span> ';
    assert.ok(source.includes('\\u25f7 <span class="hidden sm:inline">Next scan in</span><span class="sm:hidden">Next</span> ${formatClosedCountdown(initialRemaining)}'));
    assert.ok(source.includes('el.innerHTML = `\\u25f7 <span class="hidden sm:inline">Next scan in</span><span class="sm:hidden">Next</span> ${formatClosedCountdown(currentRemaining)}`'));
    assert.doesNotMatch(source, /Refreshing in \$\{|Refreshing now/);
});

test('Screener header: context word hidden on phones only for Last session / Signal, header chips carry the short label, and every header chip is compact on phones', () => {
    const screener = read('screener.js');
    assert.ok(screener.includes("['Last session', 'Signal'].includes(decisionContextLabel) ? 'hidden sm:inline ' : ''"));
    assert.ok(screener.includes('data-timer-short="Next"'));
    assert.ok(screener.includes('<span class="hidden sm:inline">Next scan in</span><span class="sm:hidden">Next</span>'));
    assert.match(screener, /const shortLabel = el\.getAttribute\('data-timer-short'\);/);
    assert.match(screener, /px-1 sm:px-1\.5 py-0\.5 rounded border \$\{scanLogStatusClass\(scanLog\.status\)\} font-black text-\[0\.44rem\] sm:text-\[0\.48rem\]/);
    assert.match(screener, /text-\[0\.52rem\] sm:text-\[0\.65rem\] font-black uppercase tracking-tight sm:tracking-wider text-indigo-800 whitespace-nowrap" data-section-title>Last scan result/);
});

test('Bots header badges are compact on phones (Market closed, state, Active position, Paused)', () => {
    const card = read('bots-scan-card.js');
    for (const text of ['Market closed</span>', 'data-bots-active-position>Active position</span>']) {
        const at = card.indexOf(text);
        assert.ok(card.slice(Math.max(0, at - 330), at).includes('px-1 sm:px-1.5'), text);
        assert.ok(card.slice(Math.max(0, at - 330), at).includes('text-[0.44rem] sm:text-[0.48rem]'), text);
    }
    assert.match(card, /px-1 sm:px-1\.5 py-0\.5 text-\[0\.44rem\] sm:text-\[0\.48rem\] font-black uppercase tracking-normal sm:tracking-wide text-slate-600/);
});
test('Bots summary is its own collapsible box under the Last scan result, like the Screener', () => {
    const html = render(sandbox(), logs.flat_waiting);
    const panelStart = html.indexOf('id="bots-scan-log-SPY"');
    const summaryStart = html.indexOf('aria-label="Bots summary"');
    assert.ok(panelStart > 0 && summaryStart > panelStart);
    const between = html.slice(panelStart, summaryStart);
    assert.ok((between.match(/<div/g) || []).length <= (between.match(/<\/div>/g) || []).length);   // the scan panel is closed first
    assert.match(html, /Bots summary<\/span>/);
    assert.doesNotMatch(html, /Watch summary/);
    assert.match(html, /data-watch-summary-key="SPY" aria-controls="bots-watch-summary-SPY" aria-expanded="true"/);   // open by default
    assert.match(html, /line-clamp-3[^>]*>Waiting for an entry: 1 of 2 bullish confirmations, 1 more needed\./);   // headline in the header row
});

test('the Bots summary box remembers an expanded choice per ticker', () => {
    const box = sandbox();
    box.window.botsWatchSummaryExpanded.SPY = true;
    const html = render(box, logs.flat_waiting);
    assert.match(html, /aria-controls="bots-watch-summary-SPY" aria-expanded="true"/);
    assert.match(html, /id="bots-watch-summary-SPY" class="border-t/);
});

const summaryOf = (log, opts = {}) => {
    const box = sandbox();
    return box.BotsSummary.build(log, opts);
};

test('Bots summary, flat: how close the entry is, with the indicators and a Bullish badge', () => {
    const out = summaryOf(logs.flat_waiting);
    assert.equal(out.badge.text, 'Waiting');             // looking for an entry, not confirmed yet
    assert.equal(out.badge.tone, 'neutral');
    assert.match(out.headline, /Waiting for an entry: 1 of 2 bullish confirmations, 1 more needed\./);
    const rows = Object.fromEntries(out.lines);
    assert.match(rows.Bullish, /RSI/);
    assert.match(rows.Waiting, /MACD/);
    assert.match(rows.Rule, /BOTH/);
});

test('Bots summary, flat and confirmed: Bullish in green; blocked: says why', () => {
    const ok = summaryOf(logs.flat_confirmed);
    assert.equal(ok.badge.tone, 'buy');
    assert.match(ok.headline, /Entry conditions met: 2 of 2 bullish confirmations\./);
    const blocked = summaryOf(logs.flat_guard);
    assert.match(blocked.headline, /^Entry conditions met \(2 of 2 bullish confirmations\), but blocked: Liquidity filter: spread 0\.80% above 0\.25%/);
    assert.equal(blocked.badge.text, 'Waiting');   // conditions met but blocked: still waiting for the entry
});

test('Bots summary, in a position: Holding, what is held and what it waits for', () => {
    const log = { ...logs.holding, live_levels: ['TP $557.13 (2.06% above)', 'SL $535.37 (1.93% below)'] };
    const out = summaryOf(log, { position: { qty: 5, avg_price: 90, current_price: 100, unrealized_pl_pct: 11.11, unrealized_pl: 50 }, exitWatch: { stop_loss: 535.37, take_profit: 557.13, trail_enabled: true } });
    assert.equal(out.badge.text, 'Holding');
    assert.match(out.headline, /^Waiting for an exit: TP \$557\.13 \(2\.06% above\) · SL \$535\.37 \(1\.93% below\)\./);
    const rows = Object.fromEntries(out.lines);
    assert.equal(rows.Position, '5 @ $90.00 · now $100.00 · +11.11% (+$50.00)');
    assert.match(rows['Trailing stop'], /rises with price/);
    assert.match(rows.Note, /not broker-side orders/);
});

test('Bots summary, strategy bots: Session Rider shows the next buy, a holder shows the scheduled sell and Holding', () => {
    const flat = summaryOf(strat.sr_flat);
    assert.equal(flat.badge.text, 'Waiting');   // a clock-driven strategy has no direction to read
    assert.match(Object.fromEntries(flat.lines)['Next buy'], /CT/);
    const held = summaryOf(strat.sr_holding);
    assert.equal(held.badge.text, 'Holding');
    assert.match(Object.fromEntries(held.lines)['Waiting for'], /scheduled sell/);
    const st = summaryOf(strat.st_holding);
    assert.equal(st.badge.text, 'Holding');
    assert.ok(st.lines.some(([label]) => label === 'Protective stop'));
});

test('the card shows the detailed summary rows in the Bots summary box', () => {
    const html = render(sandbox(), logs.flat_waiting);
    assert.match(html.slice(html.indexOf('aria-label="Bots summary"')), />Waiting</);
    assert.match(html, /How close|Waiting:<\/span>/);
    assert.match(html, /Rule:<\/span>/);
});

test('the Bots summary no longer ends with a HOLD / BUY / SELL badge', () => {
    for (const log of [logs.flat_waiting, logs.flat_confirmed, logs.holding]) {
        const html = render(sandbox(), log);
        const summary = html.slice(html.indexOf('aria-label="Bots summary"'));
        assert.doesNotMatch(summary, />\s*(HOLD|BUY|SELL)\s*</);
    }
});

test('the Bots summary headline does not repeat the Holding badge', () => {
    assert.doesNotMatch(summaryOf(logs.holding).headline, /^Holding/i);
    const rider = summaryOf(strat.sr_holding);
    assert.equal(rider.badge.text, 'Holding');
    assert.doesNotMatch(rider.headline, /Holding/);
    assert.match(rider.headline, /^5 shares held\. Scheduled sell:/);
    const supertrend = summaryOf(strat.st_holding);
    assert.doesNotMatch(supertrend.headline, /^Holding/);
    assert.match(supertrend.headline, /^Sells when Supertrend turns bearish/);
    assert.match(summaryOf(strat.gc_flat).headline, /^Flat · waiting for SMA50/);   // a flat bot keeps its headline (no Holding to repeat)
});

test('badge colours: Waiting grey, Bullish (entry confirmed) green, Bearish red, Holding blue (sky)', () => {
    const html = (log, opts) => render(sandbox(), log, opts);
    const badgeClass = (markup, text) => (markup.match(new RegExp(`<span class="px-1\\.5 py-0\\.5 rounded border ([^"]*) font-black[^"]*">${text}</span>`)) || [])[1] || '';
    assert.match(badgeClass(html(logs.flat_waiting), 'Waiting'), /slate/);        // looking for an entry: grey
    assert.match(badgeClass(html(logs.flat_confirmed), 'Bullish'), /emerald/);     // entry confirmed: green
    assert.match(badgeClass(html(logs.holding), 'Holding'), /sky/);
    assert.match(summaryOf(strat.st_holding).badge.tone, /hold/);
    assert.equal(summaryOf({ ...logs.flat_confirmed, target_direction: 'BEARISH' }).badge.tone, 'sell');   // a confirmed put entry is red
    assert.equal(summaryOf({ ...logs.flat_waiting, target_direction: 'BEARISH' }).badge.tone, 'neutral');   // waiting is never red
});

test('the Bots card template no longer renders the Entry Ind / Exit Ind badge block', () => {
    const source = read('bots.js');
    assert.doesNotMatch(source, /\$\{entryIndHtml\}|\$\{exitIndHtml\}/);
    assert.match(source, /\$\{researchBadgesHtml\}/);   // the chart button and research badges stay
});

test('Golden Cross on a 15-minute bot: the rule says 15-minute bars, "how close" is spelled out, and the untested settings are noted', () => {
    const log = strat.qqq_gc_15m;
    const out = summaryOf(log, { timeframe: '15Min' });
    const rows = Object.fromEntries(out.lines);
    assert.equal(out.badge.text, 'Waiting');   // flat strategy bot: looking for an entry
    assert.match(rows['How close'], /^SMA20 is 0\.28% below SMA50\. A buy needs SMA20 to cross above SMA50\.$/);
    assert.match(rows.Numbers, /SMA20 742\.25 · SMA50 744\.30/);
    assert.match(rows['Waiting for'], /completed 15-minute bar/);
    assert.doesNotMatch(rows['Waiting for'], /daily/);
    assert.match(rows.Settings, /timeframe 15Min \(designed for 1Day\)/);
    // on the designed timeframe nothing is rewritten
    assert.match(summaryOf(strat.gc_flat, { timeframe: '1Day' }).lines.find(([l]) => l === 'Waiting for')[1], /daily bar/);
});

test('the Buy and Sell evidence for a 15-minute Golden Cross bot do not say "daily"', () => {
    const html = render(sandbox(), strat.qqq_gc_15m, { timeframe: '15Min' });
    assert.match(html, /completed 15-minute bar/);
    assert.doesNotMatch(html.slice(html.indexOf('Buy Evidence')), /completed daily bar/);
});

test('Entry/Exit Setup shows the bot\'s own timeframe and says when it is not the designed one', () => {
    const box = setupSandbox();
    box.StrategyPreset = { describe: () => ({ label: 'Golden Cross', summary: 's', entry_rule: 'Fast average crosses above the slow average on a completed daily bar.', exit_rule: 'Fast average is below the slow average on a completed daily bar.', timeframes: ['1Day'], sessions: ['Regular'], variant: null }) };
    const html = box.BotsSetupWatch.render({ ticker: 'QQQ', setup: { kind: 'strategy', strategy: 'Golden Cross' }, strategyInfo: { preset_id: 'golden_cross' }, timeframe: '15Min' });
    assert.match(html, /completed 15-minute bar/);
    assert.doesNotMatch(html, /completed daily bar/);
    assert.match(html, /Timeframe 15Min\. Designed for 1Day; other timeframes are untested/);
    const daily = box.BotsSetupWatch.render({ ticker: 'QQQ', setup: { kind: 'strategy', strategy: 'Golden Cross' }, strategyInfo: { preset_id: 'golden_cross' }, timeframe: '1Day' });
    assert.match(daily, /completed daily bar/);
    assert.doesNotMatch(daily, /untested/);
});

test('an intraday strategy bot does not show a date-only "Candle evaluated" line', () => {
    assert.match(render(sandbox(), strat.gc_flat, { timeframe: '1Day' }), /Candle evaluated/);
    assert.doesNotMatch(render(sandbox(), strat.qqq_gc_15m, { timeframe: '15Min' }), /Candle evaluated/);
});

test('the Entry/Exit Setup header carries the RTH / ATH / 24H session badge, like the Screener', () => {
    const box = setupSandbox();
    const setup = { kind: 'manual', entry: 'Indicator', exit: 'ATR' };
    const badge = (opts) => (box.BotsSetupWatch.render({ ticker: 'SPY', setup, ...opts }).match(/data-bots-session="(\w+)"/) || [])[1];
    assert.equal(badge({ settings: { market_session: 'Regular' } }), 'RTH');
    assert.equal(badge({ settings: {} }), 'RTH');
    assert.equal(badge({ settings: { market_session: 'Extended' } }), 'ATH');
    assert.equal(badge({ settings: { market_session: 'Extended' }, isCrypto: true }), '24H');
    assert.equal(badge({ settings: { market_session: 'Extended' }, isOptions: true }), 'RTH');   // options trade regular hours only
    const html = box.BotsSetupWatch.render({ ticker: 'SPY', setup, settings: {}, hasPosition: true });
    assert.doesNotMatch(html, /Active position/);                                              // Active position is in the Last scan result header
    assert.doesNotMatch(html, /Regular Hours|Extended Hrs|24 Hrs/);
});

test('the Screener badge uses RTH / ATH / 24H and no longer says Regular Hours / Extended Hrs / 24 Hrs', () => {
    const source = read('screener.js');
    assert.match(source, /\? '24H'\s*:\s*\['extended'[^\]]*\]\.includes\(configuredMarketSession\)\s*\? 'ATH'\s*:\s*'RTH'/);
    assert.doesNotMatch(source, /marketHoursLabel === '24 Hrs'|'Extended Hrs'|\? '24 Hrs'/);
});

test('Active position badge sits in the Last scan result header, with the timer and arrow kept together at the right', () => {
    const holding = render(sandbox(), logs.holding, { position: { qty: 5, avg_price: 90 } });
    const header = holding.slice(holding.indexOf('Last scan result'), holding.indexOf('id="bots-scan-log-SPY"'));
    assert.match(header, /data-bots-active-position>Active position</);
    assert.ok(header.indexOf('Holding') < header.indexOf('Active position') && header.indexOf('Active position') < header.indexOf('scan-countdown'));
    assert.match(header, /order-3 ml-auto inline-flex[^"]*">.*scan-countdown.*data-scan-log-arrow/s);   // timer and arrow in one right-hand group
    // a bot with no position, or only the log saying flat, has no badge
    assert.doesNotMatch(render(sandbox(), logs.flat_waiting), /Active position/);
    // it is derived from the log too, so it also shows when the position object is not passed
    assert.match(render(sandbox(), logs.holding), /Active position/);
    // market closed with a position: Market closed, Active position, Paused
    const closed = render(sandbox(), { status: 'MARKET_CLOSED', last_scan: logs.holding, setup: logs.holding.setup }, { closed: true, position: { qty: 5 } });
    const closedHeader = closed.slice(closed.indexOf('Last scan result'), closed.indexOf('id="bots-scan-log-SPY"'));
    assert.ok(closedHeader.indexOf('Market closed') < closedHeader.indexOf('Active position') && closedHeader.indexOf('Active position') < closedHeader.indexOf('Paused'));
});

test('the timeframe badge (30M candle) sits next to the session badge in the Entry/Exit Setup header line', () => {
    const html = setupSandbox().BotsSetupWatch.render({ ticker: 'SPY', setup: { kind: 'manual', entry: 'Indicator', exit: 'ATR' }, settings: {}, timeframeLabel: '30M candle' });
    const header = html.slice(0, html.indexOf('id="bots-setup-watch-SPY"'));
    assert.match(header, /data-bots-timeframe>30M candle</);
    const order = ['Entry/Exit Setup', 'Indicators', 'data-bots-session', 'data-bots-timeframe', 'data-setup-watch-arrow'].map((text) => header.indexOf(text));
    assert.ok(order.every((i) => i >= 0) && order.every((v, i) => i === 0 || v > order[i - 1]));    // title, source, session, timeframe, arrow: one line
    assert.doesNotMatch(setupSandbox().BotsSetupWatch.render({ ticker: 'SPY', setup: { kind: 'manual', entry: 'a', exit: 'b' }, settings: {} }), /data-bots-timeframe/);
});

test('Bots top header: Compound / Fixed sits next to Stocks, and the timeframe badge has left the top header', () => {
    const bots = read('bots.js');
    assert.match(bots, /\$\{identityBadgesHtml\}\s*\$\{allocationBadgeHtml\}\s*\$\{backtestBadge\}/);
    const identity = bots.slice(bots.indexOf('const identityBadgesHtml'), bots.indexOf('const researchBadgesHtml'));
    assert.doesNotMatch(identity, /\$\{tfLabel\}/);
    assert.match(bots, /timeframeLabel: tfLabel/);
});

test('Screener: the timeframe badge moved from the top header to the Entry/Exit Setup header', () => {
    const screener = read('screener.js');
    const identity = screener.slice(screener.indexOf('const profileIdentityBadgesHtml'), screener.indexOf("item.innerHTML = `", screener.indexOf('const profileIdentityBadgesHtml')));
    assert.doesNotMatch(identity, /\$\{tfLabel\}/);
    assert.match(screener, /\$\{setupWatchHeaderExtras\}\s*<span[^>]*data-setup-timeframe>\$\{tfLabel\}<\/span>/);
});

test('no divider line between the Entry/Exit Setup and the Last scan result', () => {
    for (const html of [render(sandbox(), logs.flat_waiting), render(sandbox(), { status: 'MARKET_CLOSED', last_scan: null, setup: logs.flat_waiting.setup }, { closed: true })]) {
        const root = html.match(/<div class="[^"]*" data-bots-scan-card>/)[0];
        assert.doesNotMatch(root, /border-t/);
    }
});

test('Entry Setup / Exit Setup titles on both pages (options bots say Call or Put)', () => {
    const box = setupSandbox();
    const setup = { kind: 'manual', entry: 'Indicator', exit: 'ATR' };
    assert.match(box.BotsSetupWatch.render({ ticker: 'A', setup, settings: {} }), /Entry Setup - <span/);
    assert.match(box.BotsSetupWatch.render({ ticker: 'A', setup, settings: {}, isOptions: true }), /Entry Setup · Call - <span/);
    assert.match(box.BotsSetupWatch.render({ ticker: 'A', setup, settings: { options_type: 'puts_only' }, isOptions: true }), /Entry Setup · Put - <span/);
    const screener = read('screener.js');
    assert.doesNotMatch(screener, /Bullish \/ Buy Setup|Bearish \/ Sell Setup/);
    assert.equal((screener.match(/Entry Setup/g) || []).length >= 2 && (screener.match(/Exit Setup/g) || []).length >= 2, true);
});

test('Entry Setup and Exit Setup are two columns from the sm breakpoint (stacked on phones), like the evidence sections, on both pages', () => {
    const html = setupSandbox().BotsSetupWatch.render({ ticker: 'A', setup: { kind: 'manual', entry: 'Indicator', exit: 'ATR' }, settings: {} });
    assert.match(html, /grid grid-cols-1 sm:grid-cols-2 gap-0\.5 sm:gap-1 items-start" data-setup-columns>/);
    assert.ok(html.indexOf('data-bots-setup-side="buy"') < html.indexOf('data-bots-setup-side="sell"'));   // Entry left, Exit right
    const screener = read('screener.js');
    assert.equal((screener.match(/grid grid-cols-1 sm:grid-cols-2 gap-1 items-start" data-setup-columns>/g) || []).length, 2);
    assert.doesNotMatch(screener, /<div class="grid grid-cols-1 gap-0\.5">/);
    // the evidence sections use the same pattern, so the two blocks line up
    assert.match(read('screener-evidence.js'), /grid grid-cols-1 sm:grid-cols-2 gap-1/);
});

test('phone spacing is tighter and desktop spacing is kept (every tightened value has an sm: counterpart)', () => {
    const bots = read('bots.js');
    assert.match(bots, /p-2 sm:p-2\.5 mb-1\.5 flex flex-col gap-0\.5 sm:gap-1\.5/);
    assert.match(bots, /data-bot-card-body class="flex flex-col gap-0\.5 sm:gap-1\.5"/);
    assert.match(bots, /mt-0\.5 sm:mt-1 grid grid-cols-2 sm:grid-cols-4 gap-1\.5 cursor-pointer" data-bots-position-grid/);
    assert.match(read('bots-scan-card.js'), /data-bots-scan-card>/);
    assert.match(read('bots-scan-card.js'), /mt-0 sm:mt-1 flex flex-col gap-0\.5 sm:gap-1/);
    assert.match(read('bots-setup-watch.js'), /mt-0\.5 sm:mt-1 rounded-lg/);
});

test('no divider line between the Running / Last trade line and the Holding numbers', () => {
    const bots = read('bots.js');
    const grid = bots.match(/<div class="[^"]*" data-bots-position-grid/)[0];
    assert.doesNotMatch(grid, /border-t/);
});

test('phone line spacing: a Bots-scoped, phone-only rule tightens the line height, and the shared evidence frame is only tightened for Bots', () => {
    const css = read('styles.css');
    const rule = css.match(/@media \(max-width: 767px\) \{\s*\[data-bots-scan-card\] \.leading-snug[\s\S]*?\}\s*\}/);
    assert.ok(rule, 'phone-only Bots line-height rule exists');
    assert.match(rule[0], /\[data-bots-setup-watch\] \.leading-snug/);
    assert.doesNotMatch(rule[0], /!important/);
    const evidence = read('screener-evidence.js');
    assert.match(evidence, /\$\{ctx\.bot \? 'gap-0 sm:gap-0\.5' : 'gap-0\.5'\}/);   // Screener sections keep gap-0.5
    assert.equal(sandbox().ScreenerEvidence.build({ source: 'indicators', settings: {}, counts: { bullHave: 0, bullNeed: 1, bearHave: 0, bearNeed: 1 }, indicators: [], technical: {}, watch: 'BOTH', scanLog: {}, scan: {} }).html.includes('gap-0 sm:gap-0.5'), false);
});

test('on a phone the trade counts (0 TODAY / 27 TOTAL) share one line with the Running for / Last trade text', () => {
    const bots = read('bots.js');
    const row = bots.match(/<div class="([^"]*)" data-bots-meta-row/)[1];
    assert.match(row, /\bflex-row\b/);
    assert.doesNotMatch(row, /flex-col/);                        // no stacking on phones
    assert.match(row, /justify-between/);
    assert.match(bots, /<div class="flex items-center shrink-0 ml-auto" data-bots-trade-counts>\s*\$\{tradeBadgeHtml\}/);
    assert.match(bots, /<div class="flex items-center gap-1 flex-wrap min-w-0">\s*<span class="flex items-center gap-0\.5 \$\{isPaused/);   // the left group may wrap, the counts never do
});

test('Screener header on phones says Closed (Market closed on desktop) so the closed-market line fits', () => {
    const screener = read('screener.js');
    assert.ok(screener.includes("'<span class=\"hidden sm:inline\">Market </span>Closed'"));
});

test('LTC case: 0 of 3 bullish confirmations with two opposing signals and a spread block is not called Bullish', () => {
    const out = summaryOf(logs.ltc_blocked);
    assert.equal(out.badge.text, 'Waiting');                        // looking for an entry: never called Bearish while flat
    assert.equal(out.badge.tone, 'neutral');
    assert.match(out.headline, /^Waiting for an entry: 0 of 3 bullish confirmations, 3 more needed\. Blocked: Liquidity filter: spread 0\.59% exceeds 0\.50%$/);
    const rows = Object.fromEntries(out.lines);
    assert.match(rows.Opposing, /EMA Cross/);
    assert.match(rows.Waiting, /RSI/);
    const html = render(sandbox(), logs.ltc_blocked);
    assert.match(html, /aria-label="Bots summary"/);
    assert.match(html.slice(html.indexOf('aria-label="Bots summary"')), />Waiting</);
    assert.doesNotMatch(html.slice(html.indexOf('aria-label="Bots summary"')), />Bearish</);
});

test('badge rule while flat: Waiting until the entry is confirmed, then the side it enters on', () => {
    const base = logs.ltc_blocked;
    const tweak = (patch) => summaryOf({ ...base, entry_guard: null, blockers: [], ...patch }).badge;
    assert.equal(tweak({}).text, 'Waiting');                                                                     // opposing signals: still Waiting
    assert.equal(tweak({ indicators: base.indicators.map((i, n) => ({ ...i, status: n === 0 ? 'CONFIRMED' : 'WAITING' })) }).text, 'Waiting');   // some confirmations: still Waiting
    assert.equal(tweak({ status: 'READY', indicators: base.indicators.map((i) => ({ ...i, status: 'CONFIRMED' })) }).text, 'Bullish');
    assert.equal(tweak({ status: 'READY', target_direction: 'BEARISH', indicators: base.indicators.map((i) => ({ ...i, status: 'CONFIRMED' })) }).text, 'Bearish');   // a put entry
    assert.equal(summaryOf({ ...base, status: 'READY', blockers: [], entry_guard: { reason: 'spread' } }).badge.text, 'Waiting');   // confirmed but blocked
    assert.equal(summaryOf(strat.qqq_gc_15m).badge.text, 'Waiting');      // Golden Cross below the slow average
    assert.equal(summaryOf(strat.gc_flat).badge.text, 'Waiting');
});

test('Session Rider options no longer show "Pause after losses in a row"; the default pause setting is still sent', () => {
    const source = read('strategy-preset.js');
    assert.doesNotMatch(source, /Pause after losses in a row|strategySchedLoss_regular|\$\{lossField\}/);
    assert.match(source, /payload\.session_loss_pause_after = state\.sched\.lossPause;/);   // the server keeps its own minimum of 3
    assert.match(source, /skipWeekend: false, lossPause: 3 \}/);                               // default stays 3
});

test('section titles share the Entry/Exit Setup style (bold, uppercase, indigo) on Bots and Screener', () => {
    const TITLE = 'text-[0.52rem] sm:text-[0.65rem] font-black uppercase tracking-tight sm:tracking-wider text-indigo-800';
    const card = read('bots-scan-card.js');
    assert.ok(card.includes(`${TITLE} whitespace-nowrap shrink-0">Bots summary</span>`));
    assert.ok(card.includes(`${TITLE}" data-section-title>Last scan result</span>`));
    const screener = read('screener.js');
    assert.equal(screener.split(`${TITLE} whitespace-nowrap shrink-0">Watch summary</span>`).length - 1, 2);
    assert.ok(screener.includes(`${TITLE} whitespace-nowrap" data-section-title>Last scan result</span>`));
    assert.ok(read('bots-setup-watch.js').includes('text-indigo-800 whitespace-nowrap">Entry/Exit Setup'));
    assert.doesNotMatch(card + screener, /opacity-80 shrink-0">(Bots|Watch) summary/);
});

test('Bots and Screener charts draw only PDH and PDL for Prior Day High/Low (no buffered trigger lines)', () => {
    for (const file of ['bots.js', 'screener.js']) {
        const source = read(file);
        assert.match(source, /setBotChartPreviewLineSeries\('daily_high_low_high', toLineData\('daily_high_low_high'\), \{ color: '#d97706', lineWidth: 3, lineStyle: 1, priceLineTitle: 'PDH' \}\);/, file);
        assert.match(source, /setBotChartPreviewLineSeries\('daily_high_low_low', toLineData\('daily_high_low_low'\), \{ color: '#0891b2', lineWidth: 3, lineStyle: 1, priceLineTitle: 'PDL' \}\);/, file);
        assert.doesNotMatch(source, /setBotChartPreviewLineSeries\('daily_high_low_(high|low)_trigger'/, file);
    }
    // the Dashboard is unchanged
    assert.match(read('app.js'), /_getOrCreate\(_overlaySeries, 'daily_high_low_high_trigger'/);
});

test('opening a chart retries a 429 (server busy) up to three times with a short jittered wait, on Bots and Screener', () => {
    for (const [file, endpoint] of [['bots.js', 'bots'], ['screener.js', 'screener']]) {
        const source = read(file);
        const start = source.indexOf('async function requestBotChartPreviewPayload');
        const body = source.slice(start, source.indexOf('function applyBotChartPreviewPayload', start) > 0 ? source.indexOf('\n}\n', start) + 3 : start + 3000);
        assert.ok(body.includes(`/api/${endpoint}/chart-preview`), file);
        assert.match(body, /for \(let attempt = 0; attempt < 3; attempt \+= 1\)/, file);
        assert.match(body, /if \(response\.status !== 429 \|\| attempt === 2\) break;/, file);
        assert.match(body, /700 \* \(attempt \+ 1\) \+ Math\.floor\(Math\.random\(\) \* 300\)/, file);
    }
});

test('OPEN CHART, Fundamentals and Earnings: full words and the 📊 / 📅 icons on phones too, the earnings date on every screen, one line for the common cases', () => {
    const research = read('research-shared.js');
    assert.ok(research.includes("'Worth considering': ['Strong',"));
    assert.doesNotMatch(research, /Strong evidence/);
    assert.match(research, /const BUTTON = 'research-chip px-1 sm:px-1\.5 py-0\.5 rounded-md border font-black text-\[0\.5rem\] sm:text-\[0\.52rem\] uppercase tracking-tight sm:tracking-wide [^']*whitespace-nowrap shrink-0 text-left';/);
    for (const file of ['bots.js', 'screener.js']) {
        const source = read(file);
        assert.match(source, /<div class="flex items-center gap-0\.5 sm:gap-1\.5 flex-wrap min-w-0" data-research-row>/, file);
        assert.match(source, /font-black text-\[0\.5rem\] sm:text-\[0\.52rem\] border border-blue-200[^"]*whitespace-nowrap shrink-0"/, file);
    }
    const box = { window: {}, document: { getElementById: () => null }, localStorage: { getItem: () => null, setItem() {} }, console };
    box.window = box;
    vm.createContext(box);
    vm.runInContext(research, box);
    const fund = box.BotBullsResearch.fundamentalsBadge('MSFT', { status: 'current', assessment: 'Worth considering', score: 85 });
    assert.match(fund, />📊 Fundamentals · Strong · 85<\/button>/);              // the same text on every screen width
    assert.doesNotMatch(fund, /hidden sm:inline|Fund\.|Strong evidence/);
    const earn = box.BotBullsResearch.earningsBadge('MSFT', { status: 'current', initialized: true, event_date: '2026-10-28', days_to_event: 15, risk_level: 'low' });
    assert.match(earn, />📅 Earnings · Oct 28<span class="hidden sm:inline"><\/span> · in 15d<\/button>/);   // the date and "in 15d" on phones too
    assert.match(earn, /title="Earnings Oct 28 \(in 15d\)\./);
    const bmo = box.BotBullsResearch.earningsBadge('MSFT', { status: 'current', initialized: true, event_date: '2026-10-28', days_to_event: 15, timing: 'Before market open' });
    assert.match(bmo, /Oct 28<span class="hidden sm:inline"> BMO<\/span> ·/);                // BMO/AMC only on desktop
    const waiting = box.BotBullsResearch.earningsBadge('MSFT', null);
    assert.match(waiting, />📅 Earnings · Checking…</);
});

test('OPEN CHART, Fundamentals and Earnings use exactly the same text size, spacing, padding and shape', () => {
    const research = read('research-shared.js');
    const shared = ['px-1 sm:px-1.5', 'py-0.5', 'rounded-md', 'font-black', 'text-[0.5rem] sm:text-[0.52rem]', 'uppercase', 'tracking-tight sm:tracking-wide', 'whitespace-nowrap', 'shrink-0'];
    const buttonClasses = research.match(/const BUTTON = '([^']*)'/)[1];
    for (const token of shared) assert.ok(buttonClasses.includes(token), `badges: ${token}`);
    for (const file of ['bots.js', 'screener.js']) {
        const open = read(file).match(/<button class="([^"]*)" onclick="event\.stopPropagation\(\); window\.openBotCardChartPreview/)[1];
        for (const token of shared) assert.ok(open.includes(token), `${file} OPEN CHART: ${token}`);
        assert.doesNotMatch(open, /text-\[0\.545rem\]|tracking-widest|text-\[0\.44rem\]|text-\[0\.42rem\]/);
    }
});

test('on phones one CSS rule sets OPEN CHART, Fundamentals and Earnings to the same size, padding and shape (the older card rules had shrunk only the chart button)', () => {
    const css = read('styles.css');
    const media = css.slice(css.indexOf('@media (max-width: 767px) {'));
    const older = media.indexOf('#tradelistContainer .watchlist-item button.bg-blue-50 {');
    const shared = media.indexOf('#tradelistContainer .watchlist-item button.research-chip {');
    assert.ok(older > 0 && shared > older, 'the shared rule comes after the chart-button rule so it wins at equal specificity');
    const block = media.slice(shared, media.indexOf('}', shared));
    for (const declaration of ['font-size: 0.5rem !important', 'line-height: 0.75rem !important', 'padding: 0.125rem 0.25rem !important', 'border-radius: 0.375rem !important']) {
        assert.ok(block.includes(declaration), declaration);
    }
    assert.ok(read('research-shared.js').includes("const BUTTON = 'research-chip px-1"));
    for (const file of ['bots.js', 'screener.js']) assert.ok(read(file).includes('<button class="research-chip px-1 sm:px-1.5'), file);
});

test('snapshot telemetry: "Heavy" is only for big payloads; slow small ones are info with sign-in / request / backend timings', () => {
    for (const file of ['bots.js', 'screener.js']) {
        const source = read(file);
        assert.match(source, /const authMs = Math\.round\(performance\.now\(\) - fetchStartTime\);/, file);
        assert.match(source, /const requestMs = Math\.round\(performance\.now\(\) - requestStartTime\);/, file);
        assert.ok(source.includes('const timing = `sign-in ${authMs}ms, request ${requestMs}ms, backend: ${serverExecMs || \'?\'}ms`;'), file);
        assert.match(source, /if \(payloadKb > 400\) \{\s*console\.warn\(`\[telemetry\] Heavy snapshot:/, file);
        assert.match(source, /console\.info\(`\[telemetry\] Slow snapshot \(small payload\):/, file);
    }
    assert.doesNotMatch(read('bots.js'), /if \(fetchMs > 4000 \|\| payloadKb > 400\)/);
});

test('holding bot: the Sell Evidence carries a small badge for how the exit is watched', () => {
    const price = render(sandbox(), logs.holding, { exitScan: { kind: 'price', interval_sec: 60 } });
    assert.match(price, /data-exit-scan="price"[^>]*>Price watch · 60s</);
    assert.match(price, /not a broker-side stop and does not guarantee a fill/);
    const indicator = render(sandbox(), logs.holding, { exitScan: { kind: 'indicator', interval_sec: 900 } });
    assert.match(indicator, /data-exit-scan="indicator"[^>]*>Exit scan · 15m</);
    assert.match(render(sandbox(), logs.holding, { exitScan: { kind: 'indicator', interval_sec: 3600 } }), /Exit scan · 1h</);
    assert.match(render(sandbox(), logs.holding, { exitScan: { kind: 'price', interval_sec: 10 } }), /Price watch · 10s</);
});

test('the exit badge appears on the Sell side only, and not without data, while flat, or for unknown kinds', () => {
    const html = render(sandbox(), logs.holding, { exitScan: { kind: 'price', interval_sec: 60 } });
    assert.equal((html.match(/data-exit-scan=/g) || []).length, 1);
    assert.ok(html.indexOf('data-exit-scan=') > html.indexOf('data-evidence-side="sell"'));
    for (const info of [null, undefined, {}, { kind: 'weird', interval_sec: 60 }, { kind: 'price', interval_sec: 0 }]) {
        assert.doesNotMatch(render(sandbox(), logs.holding, { exitScan: info }), /data-exit-scan=/);
    }
    assert.doesNotMatch(render(sandbox(), logs.flat_waiting, {}), /data-exit-scan=/);
});

test('the Screener never shows the Bots exit badge', () => {
    const box = sandbox();
    assert.doesNotMatch(box.ScreenerEvidence.build({ active: 'both', state: 'WAITING', counts: {}, indicators: [], settings: {}, exitScan: { kind: 'price', interval_sec: 60 } }).html || '', /data-exit-scan=/);
});
