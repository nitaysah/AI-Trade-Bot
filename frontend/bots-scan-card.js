/*
 * Scan section of a Bots card, in the Screener card's style: setup badges, a "Last scan result" header and a
 * collapsible panel holding the Bots scan summary, the Entry setup watch and the Exit setup watch.
 *
 * What decides the bot's entries and exits is shown as either one Strategy badge (a strategy preset) or the
 * Entry and Exit strategy setups. While the market is closed the last real scan stays visible under the
 * banner. Content for strategy bots comes from StrategyLog; manual bots pass their already-built pieces.
 * Every dynamic string is escaped. Phone first: chips and text wrap, nothing sets a fixed width.
 */
(function () {
    'use strict';
    if (window.BotsScanCard) return;

    const STORE_KEY = 'botsScanLogExpanded';
    const esc = (value) => (window.StrategyLog ? window.StrategyLog.esc(value) : String(value == null ? '' : value)
        .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;').replace(/'/g, '&#39;'));

    function loadExpanded() {
        try { return JSON.parse(localStorage.getItem(STORE_KEY) || '{}') || {}; } catch (e) { return {}; }
    }
    window.botsScanLogExpanded = window.botsScanLogExpanded || loadExpanded();

    /* Expand/collapse one card's scan panel; the choice is remembered per ticker. */
    window.toggleBotsScanLog = function (panelId, button) {
        const panel = document.getElementById(panelId);
        if (!panel) return;
        const expanded = panel.classList.toggle('hidden') === false;
        const key = button && button.dataset ? button.dataset.scanLogKey : '';
        if (key) {
            window.botsScanLogExpanded[key] = expanded;
            try { localStorage.setItem(STORE_KEY, JSON.stringify(window.botsScanLogExpanded)); } catch (e) { /* per-viewer convenience only */ }
        }
        if (button) {
            button.setAttribute('aria-expanded', expanded ? 'true' : 'false');
            const arrow = button.querySelector('[data-scan-log-arrow]');
            if (arrow) arrow.classList.toggle('rotate-180', expanded);
        }
    };

    const SUMMARY_STORE_KEY = 'botsWatchSummaryExpanded';
    function loadSummaryExpanded() {
        try { return JSON.parse(localStorage.getItem(SUMMARY_STORE_KEY) || '{}') || {}; } catch (e) { return {}; }
    }
    window.botsWatchSummaryExpanded = window.botsWatchSummaryExpanded || loadSummaryExpanded();

    /* Expand/collapse a card's Watch summary box (collapsed by default, like the Screener); remembered per ticker. */
    window.toggleBotsWatchSummary = function (panelId, button) {
        const panel = document.getElementById(panelId);
        if (!panel) return;
        const expanded = panel.classList.toggle('hidden') === false;
        const key = button && button.dataset ? button.dataset.watchSummaryKey : '';
        if (key) {
            window.botsWatchSummaryExpanded[key] = expanded;
            try { localStorage.setItem(SUMMARY_STORE_KEY, JSON.stringify(window.botsWatchSummaryExpanded)); } catch (e) { /* per-viewer convenience only */ }
        }
        if (button) {
            button.setAttribute('aria-expanded', expanded ? 'true' : 'false');
            const arrow = button.querySelector('[data-watch-summary-arrow]');
            if (arrow) arrow.classList.toggle('rotate-180', expanded);
        }
    };

    function badge(text, tone, title) {
        return `<span class="px-1.5 py-0.5 rounded-md border ${tone} font-black text-[0.48rem] uppercase tracking-wide inline-flex items-center gap-0.5 max-w-full break-words" title="${esc(title || '')}">${esc(text)}</span>`;
    }

    function setupBadges(setup, phase, untested) {
        if (!setup) return '';
        if (setup.kind === 'strategy') {
            // The strategy name is already in the Entry/Exit Setup and the evidence sections, and "Untested settings" is dropped;
            // a strategy that failed its research check keeps its warning.
            return String(untested || '').startsWith('Did not pass')
                ? badge('Did not pass research', 'bg-amber-50 text-amber-700 border-amber-300', untested) : '';
        }
        // Manual bots: the Entry and Exit setups are shown in the Entry/Exit Setup and in the Buy / Sell Evidence, not as badges.
        return '';
    }

    function watchSection(title, label, active, bodyHtml) {
        const tone = active ? 'border-indigo-200 bg-white/80' : 'border-slate-200 bg-slate-50/60';
        return `
            <section class="rounded-md border ${tone} px-1.5 py-1 flex flex-col gap-0.5 min-w-0">
                <div class="flex items-center gap-1.5 flex-wrap">
                    <span class="text-[0.5rem] font-black uppercase tracking-wider text-slate-500">${esc(title)}</span>
                    ${label ? `<span class="px-1.5 py-0.5 rounded border border-slate-200 bg-white/70 text-slate-600 font-black text-[0.46rem] uppercase tracking-wide break-words">${esc(label)}</span>` : ''}
                    ${active ? '<span class="px-1.5 py-0.5 rounded border border-emerald-200 bg-emerald-50 text-emerald-700 font-black text-[0.46rem] uppercase tracking-widest">Watching</span>' : ''}
                </div>
                ${bodyHtml || ''}
            </section>`;
    }

    function summarySection(summaryHtml, timingHtml) {
        return `
            <section class="rounded-md border border-slate-200 bg-white/60 px-1.5 py-1 flex flex-col gap-0.5 min-w-0">
                <span class="text-[0.5rem] font-black uppercase tracking-wider text-slate-500">Bots scan summary</span>
                ${summaryHtml}
                ${timingHtml || ''}
            </section>`;
    }

    const plain = (text) => `<span class="text-[0.52rem] sm:text-[0.58rem] font-bold text-slate-600 leading-snug break-words">${esc(text)}</span>`;

    const FORCED_REASONS = { created: 'created', settings_changed: 'settings changed' };

    /* "Forced scan · created · 10:42", or an amber "Initial scan failed · will retry" when the first evaluation did not complete. */
    function forcedChip(forced) {
        if (!forced || typeof forced !== 'object') return '';
        if (forced.status === 'failed') {
            return `<span class="px-1.5 py-0.5 rounded border border-amber-300 bg-amber-50 text-amber-700 font-black text-[0.48rem] uppercase tracking-wide break-words" title="${esc(forced.message || '')}">Initial scan failed \u00b7 will retry</span>`;
        }
        const date = new Date(forced.at);
        const time = isNaN(date.getTime()) ? '' : ` \u00b7 ${date.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', hour12: false })}`;
        const label = FORCED_REASONS[forced.reason] || String(forced.reason || '').replace(/_/g, ' ');
        return `<span class="px-1.5 py-0.5 rounded border border-indigo-200 bg-indigo-50 text-indigo-700 font-black text-[0.48rem] uppercase tracking-wide break-words" title="One extra scan was run right away; it places no order.">\u26a1 Forced scan \u00b7 ${esc(label)}${esc(time)}</span>`;
    }

    function decisionChip(decision) {
        const value = String(decision || 'HOLD').toUpperCase();
        const tone = value === 'BUY' ? 'bg-emerald-50 text-emerald-600 border-emerald-100'
            : value === 'SELL' ? 'bg-red-50 text-red-600 border-red-100' : 'bg-slate-50 text-slate-500 border-slate-200';
        return `<span class="px-1.5 py-0.5 rounded border ${tone} font-black text-[0.5rem] tracking-widest uppercase">${esc(value)}</span>`;
    }

    /* "Oct 13, 2026, 2:22:54 PM CDT": the Screener's scan-time format (Central time). */
    function chicagoDateTime(iso) {
        if (!iso) return '';
        try {
            const raw = String(iso);
            const date = new Date(raw.includes('Z') || /[+-]\d{2}:\d{2}$/.test(raw) ? raw : `${raw}Z`);
            if (isNaN(date.getTime())) return String(iso);
            return date.toLocaleString('en-US', {
                timeZone: 'America/Chicago', month: 'short', day: 'numeric', year: 'numeric',
                hour: 'numeric', minute: '2-digit', second: '2-digit', hour12: true, timeZoneName: 'short',
            });
        } catch (e) { return String(iso); }
    }

    const STATE_BADGE = {
        CONFIRMED: { text: 'Confirmed', buy: 'text-emerald-700 bg-emerald-100 border-emerald-300', sell: 'text-rose-700 bg-rose-100 border-rose-300' },
        ACTIVE: { text: 'Holding', buy: 'text-sky-800 bg-sky-100 border-sky-300', sell: 'text-sky-800 bg-sky-100 border-sky-300' },
        BLOCKED: { text: 'Blocked', buy: 'text-amber-800 bg-amber-100 border-amber-300', sell: 'text-amber-800 bg-amber-100 border-amber-300' },
        WAITING: { text: 'Waiting', buy: 'text-slate-600 bg-slate-100 border-slate-200', sell: 'text-slate-600 bg-slate-100 border-slate-200' },
    };
    const SUMMARY_TONE = {
        buy: 'border-emerald-200 bg-emerald-50/70 text-emerald-900',
        sell: 'border-rose-200 bg-rose-50/70 text-rose-900',
        blocked: 'border-amber-200 bg-amber-50/70 text-amber-900',
        wait: 'border-slate-200 bg-white/60 text-slate-700',
    };

    /* One state for the whole card, read from the scan log only: nothing here changes what the bot does. */
    /* Older servers do not send `lifecycle`: derive it from what they always send (phase and blockers). */
    function withLifecycle(log) {
        if (log.lifecycle) return log;
        const blockers = Array.isArray(log.blockers) ? log.blockers : [];
        const pending = blockers.some((b) => /order pending/i.test(String(b)));
        return Object.assign({}, log, { lifecycle: log.phase === 'EXIT' ? 'IN_POSITION' : (pending ? 'ORDER_PENDING' : 'FLAT') });
    }

    function botState(log) {
        const guard = log.entry_guard && log.entry_guard.reason;
        if (log.lifecycle === 'ORDER_PENDING' || (Array.isArray(log.blockers) && log.blockers.length) || guard) return 'BLOCKED';
        if (log.status === 'READY') return 'CONFIRMED';
        return log.lifecycle === 'IN_POSITION' ? 'ACTIVE' : 'WAITING';
    }

    /* Section chips: a strategy bot shows its strategy's short name (setup.entry/exit are long rule texts for it). */
    function firstScanSetups(setup) {
        if (!setup) return { buy: '', sell: '' };
        if (setup.kind === 'strategy') {
            const name = String(setup.strategy || '').split(' \u00b7 ')[0];
            return { buy: name, sell: name };
        }
        return { buy: setup.entry || '', sell: setup.exit || '' };
    }

    /* Strategy bots (signal strategies and Session Rider): the same Buy / Sell Evidence, filled from the strategy's own scan view. */
    const STRATEGY_STATE = {
        ENTRY_READY: 'CONFIRMED', EXIT_SIGNAL: 'CONFIRMED', REBALANCED: 'ACTIVE',
        BLOCKED: 'BLOCKED', DEGRADED: 'BLOCKED', STRATEGY_ERROR: 'BLOCKED', EXIT_OVERDUE: 'BLOCKED', ORDER_PENDING: 'BLOCKED',
        HOLDING: 'ACTIVE', HOLDING_OVERNIGHT: 'ACTIVE', HOLDING_INTRADAY: 'ACTIVE',
    };
    const STRATEGY_LABEL = { REBALANCED: 'Rebalanced', SCHEDULED: 'Scheduled', ORDER_PENDING: 'Order pending', EXIT_OVERDUE: 'Exit overdue', DEGRADED: 'Degraded', STRATEGY_ERROR: 'Strategy error', WAITING_DATA: 'Waiting for data' };

    function strategyView(view, log, blockers, opts) {
        const status = String(view.status || '');
        const holding = Boolean(view.position) || /^HOLDING|^EXIT_/.test(status) || log.phase === 'EXIT';
        const state = STRATEGY_STATE[status] || 'WAITING';
        const metrics = Array.isArray(view.metrics) ? view.metrics : [];
        const metric = (label) => (metrics.find((m) => m.label === label) || {}).value || '';
        const rules = view.rules || {};
        const shortName = String(view.strategy || '').split(' \u00b7 ')[0];
        const pos = view.position;
        const posLine = pos ? [['Position', `entry ${pos.entry} · now ${pos.current}${pos.pl_pct != null ? ` · ${pos.pl_pct > 0 ? '+' : ''}${pos.pl_pct}%` : ''}`]] : [];
        const watch = (Array.isArray(view.exit_watch) ? view.exit_watch : []).map((row) => [row.label, `${row.value}${row.near_pct > 0 && row.kind !== 'time' ? ` (${row.near_pct}% of the way)` : ''}`]);
        let buy; let sell;
        const allocation = view.kind === 'allocation';
        const noDefault = (text) => String(text || '').replace(/\s*\(default[^)]*\)/gi, '');   // the bot's own times are shown instead
        const adapt = (text) => (window.BotsSummary ? window.BotsSummary.adaptRule(text, opts.timeframe) : String(text || ''));   // "daily bar" -> the bot's real timeframe
        if (allocation) {
            const alloc = view.allocation || {};
            const legs = (alloc.legs || []).map((leg) => `${leg.symbol} target ${leg.target_pct}% · held ${leg.held_pct == null ? '—' : Math.round(leg.held_pct) + '%'}${leg.pending ? ' · order working' : ''}`).join('; ');
            buy = [['Targets', legs], ['Now', metrics.map((m) => `${m.label} ${m.value}`).join(' · ')], ['Decides', rules.entry], ['Executes daily', alloc.execution_time], ['Next decision', alloc.next_decision]];
            sell = [['Reduces', rules.exit], ['', rules.stop]];
        } else if (view.kind === 'schedule') {
            buy = [['Buy time', metric('Buy')], ['Mode', metric('Mode')], ['Entry', noDefault(rules.entry)]];
            sell = [['Sell time', metric('Sell')]].concat(posLine, watch.filter((r) => r[0] !== 'Scheduled sell'),
                [['Exit', noDefault(String(rules.exit || '').replace(/\s*No stop-loss, take-profit or trailing exits\.?/, ''))], ['', rules.stop]]);
        } else {
            const now = metrics.map((m) => `${m.label} ${m.value}`).join(' · ');
            buy = [['Now', now], ['Entry', adapt(rules.entry)]];
            sell = posLine.concat(watch, [['Exit', adapt(rules.exit)], ['', rules.stop]]);
        }
        return {
            state, holding,
            label: STRATEGY_LABEL[status] || '',
            kind: view.kind === 'schedule' ? 'Schedule' : 'Strategy',
            html: window.ScreenerEvidence.buildBot({
                active: allocation ? 'both' : (holding ? 'sell' : 'buy'), state,
                setups: { buy: shortName, sell: shortName },
                custom: { buy, sell },
                hint: view.kind === 'schedule' ? { buy: [['Buy time', metric('Buy')]], sell: [['Sell time', metric('Sell')]] } : null,
                blockers: Array.isArray(blockers) ? blockers : [], guard: '', isCrypto: Boolean(opts.isCrypto), exitWatch: null,
            }).html,
        };
    }

    function botEvidenceHtml(log, state, opts) {
        if (!window.ScreenerEvidence || typeof window.ScreenerEvidence.buildBot !== 'function') return '';
        const holding = log.phase === 'EXIT';
        const setup = log.setup && log.setup.kind === 'manual' ? log.setup : {};
        return window.ScreenerEvidence.buildBot({
            active: holding ? 'sell' : 'buy',
            state,
            setups: { buy: setup.entry || '', sell: setup.exit || '' },
            targetDirection: String(log.target_direction || '').toUpperCase(),
            usesIndicators: Boolean(log.show_indicator_leg),
            indicators: Array.isArray(log.indicators) ? log.indicators : [],
            confirmed: Number.isFinite(Number(log.confirmed_weight)) ? Number(log.confirmed_weight) : Number(log.confirmed_count || 0),
            required: Number(log.required_count || 0),
            remaining: Number(log.remaining_count || 0),
            conditionLogic: log.condition_logic || '',
            levels: Array.isArray(log.live_levels) ? log.live_levels : [],
            exitWatch: opts.exitWatch || null,
            exitScan: opts.exitScan || null,
            blockers: Array.isArray(log.blockers) ? log.blockers : [],
            guard: log.entry_guard && log.entry_guard.reason ? log.entry_guard.reason : '',
            isCrypto: Boolean(opts.isCrypto),
        }).html;
    }

    /*
     * opts: ticker, scanLog (top-level log), closed, closedBannerHtml, statusClass(fn), statusText,
     *       decisionBadgeHtml, timeAgoText, manual: { summaryText, watchHtml }
     */
    function render(opts) {
        const { ticker, scanLog, closed } = opts;
        if (!scanLog) return '';
        // While the market is closed the content comes from the last real scan, if the server kept one.
        const content = closed ? (scanLog.last_scan || null) : scanLog;
        const setup = scanLog.setup || (content && content.setup) || null;
        const phase = content ? content.phase : scanLog.phase;
        const key = String(ticker || '').toUpperCase();
        const panelId = `bots-scan-log-${key.replace(/[^a-zA-Z0-9_-]/g, '_')}`;
        const expanded = window.botsScanLogExpanded[key] !== false;
        const toggle = `<button type="button" data-scan-log-key="${esc(key)}" aria-controls="${esc(panelId)}" aria-expanded="${expanded ? 'true' : 'false'}"
            class="order-2 sm:order-3 ml-auto inline-flex h-6 w-6 shrink-0 items-center justify-center rounded text-slate-500 hover:bg-white/70" title="Expand or collapse scan details"
            onclick="event.stopPropagation(); window.toggleBotsScanLog('${esc(panelId)}', this)"><svg data-scan-log-arrow class="h-3 w-3 transition-transform ${expanded ? 'rotate-180' : ''}" viewBox="0 0 20 20" fill="currentColor" aria-hidden="true"><path fill-rule="evenodd" d="M5.23 7.21a.75.75 0 011.06.02L10 11.168l3.71-3.938a.75.75 0 111.08 1.04l-4.25 4.5a.75.75 0 01-1.08 0l-4.25-4.5a.75.75 0 01.02-1.06z" clip-rule="evenodd" /></svg></button>`;

        const strategy = window.StrategyLog && content && content.strategy_log ? window.StrategyLog.sections(content.strategy_log, content.blockers) : null;
        let summaryHtml; let timingHtml = ''; let entryHtml; let exitHtml; let entryActive; let exitActive;
        if (strategy) {
            summaryHtml = plain(strategy.summary);
            timingHtml = strategy.timingHtml;
            entryHtml = strategy.entryHtml;
            exitHtml = strategy.exitHtml;
            entryActive = strategy.entryActive;
            exitActive = !strategy.entryActive;
        } else if (!content) {
            summaryHtml = plain(closed ? 'No scan result yet: the first scan runs when the market opens.' : 'Waiting for the next scan.');
            entryHtml = plain('No scan result yet.');
            exitHtml = plain('No scan result yet.');
            entryActive = exitActive = false;
        } else {
            const manual = (!closed && opts.manual) ? opts.manual : {};
            const watchHtml = manual.watchHtml
                || ((closed ? [content.detail].concat(content.blockers || []) : [content.detail]).filter(Boolean).map(plain).join(''));
            summaryHtml = plain(manual.summaryText || content.summary || 'Waiting for the next scan.');
            entryActive = phase !== 'EXIT';
            exitActive = phase === 'EXIT';
            entryHtml = entryActive ? watchHtml : plain('A position is open: entry watch resumes after the exit.');
            exitHtml = exitActive ? watchHtml : plain('No open position: the exit watch starts after an entry.');
        }

        // Manual bots (not strategy, not market closed) get the Screener-style card; anything missing falls back to the layout above.
        const norm = (!strategy && content) ? Object.assign({}, withLifecycle(content), { setup: content.setup || setup }) : null;
        const evidence = (norm && opts.manual) ? botEvidenceHtml(norm, botState(norm), opts) : '';
        const state = evidence ? botState(norm) : '';

        // Strategy bots (signal strategies, Session Rider) get the same card from their own scan view; Malik keeps its view.
        const stratView = (strategy && content && content.strategy_log
            && window.ScreenerEvidence && typeof window.ScreenerEvidence.buildBot === 'function')
            ? strategyView(content.strategy_log, content, content.blockers, opts) : null;

        const status = closed ? 'MARKET_CLOSED' : scanLog.status;
        const forcedHtml = forcedChip(content && content.forced_scan);
        const headerChips = closed
            ? `<span class="px-1.5 py-0.5 rounded border ${opts.statusClass('MARKET_CLOSED')} font-black text-[0.48rem] tracking-widest uppercase">MARKET CLOSED</span>`
              + (content ? `<span class="text-[0.48rem] font-bold text-slate-400 uppercase">last:</span><span class="px-1.5 py-0.5 rounded border ${opts.statusClass(content.status)} font-black text-[0.48rem] tracking-widest uppercase">${esc(String(content.status || '').replace(/_/g, ' '))}</span>${decisionChip(content.decision)}${forcedHtml}` : '')
            : `<span class="px-1.5 py-0.5 rounded border ${opts.statusClass(status)} font-black text-[0.48rem] tracking-widest uppercase">${esc(opts.statusText)}</span>${opts.decisionBadgeHtml || ''}${forcedHtml}${opts.timeAgoText || ''}`;

        // Market closed before any scan has run: the same card, saying so.
        const firstScan = (!content && closed && window.ScreenerEvidence && typeof window.ScreenerEvidence.buildBot === 'function')
            ? { html: window.ScreenerEvidence.buildBot({
                    active: 'both', state: 'WAITING', setups: firstScanSetups(setup),
                    custom: opts.schedule
                        ? { buy: [['Buy time', opts.schedule.buy_label], ['Mode', opts.schedule.mode], ['', 'No scan result yet: the first scan runs when the market opens.']],
                            sell: [['Sell time', opts.schedule.sell_label], ['', 'Nothing to sell until an entry is made.']] }
                        : { buy: [['', 'No scan result yet: the first scan runs when the market opens.']], sell: [['', 'Nothing to sell until an entry is made.']] },
                    blockers: [], guard: '', isCrypto: false, exitWatch: null }).html,
                state: 'WAITING', side: 'buy', kind: '', label: STATE_BADGE.WAITING.text, summaryText: 'No scan result yet: the first scan runs when the market opens.',
                timingHtml: '', decision: 'HOLD' }
            : null;
        if (firstScan && opts.schedule) firstScan.summaryText = `Waiting for the market to open. Buy ${opts.schedule.buy_label} · Sell ${opts.schedule.sell_label}.`;
        // One card model for manual bots and strategy bots; null falls back to the Entry/Exit watch sections above.
        const card = firstScan || (evidence
            ? { html: evidence, state, side: norm.phase === 'EXIT' ? 'sell' : 'buy', kind: '',
                label: norm.lifecycle === 'ORDER_PENDING' ? 'Order pending' : STATE_BADGE[state].text,
                summaryText: opts.manual.summaryText || content.summary, timingHtml: '', decision: content.decision }
            : (stratView && stratView.html
                ? { html: stratView.html, state: stratView.state, side: stratView.holding ? 'sell' : 'buy', kind: stratView.kind,
                    label: stratView.label || STATE_BADGE[stratView.state].text,
                    summaryText: strategy.summary,
                    // "Candle evaluated: 2026-10-06" is a date only; on an intraday bot it would hide which candle that was, so it is left out.
                    timingHtml: !opts.timeframe || /^1day$/i.test(opts.timeframe) ? strategy.timingHtml : '', decision: content.decision }
                : null));
        let headerHtml; let panelHtml; let watchSummaryHtml = '';
        if (card) {
            const badgeInfo = STATE_BADGE[card.state];
            const tone = card.state === 'CONFIRMED' ? SUMMARY_TONE[card.side] : card.state === 'BLOCKED' ? SUMMARY_TONE.blocked : SUMMARY_TONE.wait;
            const inPosition = Boolean(opts.position) || Boolean(content && (content.lifecycle === 'IN_POSITION' || content.phase === 'EXIT'));
            const timerChip = closed
                ? '<span class="inline-flex items-center gap-0.5 whitespace-nowrap rounded border border-slate-200 bg-slate-100 px-1 sm:px-1.5 py-0.5 text-[0.44rem] sm:text-[0.48rem] font-black uppercase tracking-normal sm:tracking-wide text-slate-600" title="Scanning is paused while the market is closed">\u23f8 Paused</span>'
                : (opts.timeAgoText || '');
            // Badges at the left (state, Active position); the timer and the arrow stay together at the right and wrap as a pair
            // only if they cannot fit on the line.
            headerHtml = (closed ? `<span class="order-2 px-1 sm:px-1.5 py-0.5 rounded border ${opts.statusClass('MARKET_CLOSED')} font-black text-[0.44rem] sm:text-[0.48rem] tracking-normal sm:tracking-widest uppercase whitespace-nowrap"><span class="hidden sm:inline">Market </span>Closed</span>` : '')
                + (closed ? '' : `<span class="order-2 px-1 sm:px-1.5 py-0.5 rounded border ${badgeInfo[card.side]} font-black text-[0.44rem] sm:text-[0.48rem] tracking-normal sm:tracking-widest uppercase whitespace-nowrap">${esc(card.label)}</span>`)
                + (inPosition ? '<span class="order-2 px-1 sm:px-1.5 py-0.5 rounded border bg-amber-50 text-amber-700 border-amber-200 text-[0.44rem] sm:text-[0.48rem] font-black uppercase whitespace-nowrap" data-bots-active-position>Active position</span>' : '')
                + `<span class="order-3 ml-auto inline-flex items-center gap-1.5 shrink-0">${timerChip}${toggle.replace('order-2 sm:order-3 ml-auto', 'shrink-0')}</span>`;
            const scanAt = (content && content.updated_at) || scanLog.updated_at || '';
            const isSchedule = Boolean(content && content.strategy_log && content.strategy_log.kind === 'schedule');
            const scanLine = scanAt
                ? `${isSchedule ? 'Schedule checked at' : 'Last closed-candle scan at'} ${chicagoDateTime(scanAt)}`
                : (isSchedule ? 'Schedule check pending' : 'First completed-candle scan pending');
            const closedSummary = closed && scanLog.summary
                ? `<span class="text-[0.58rem] sm:text-[0.65rem] leading-snug text-slate-700 break-words">${esc(scanLog.summary)}</span>` : '';
            // A separate box under the Last scan result, as on the Screener: the headline in the header row, the full explanation
            // (how close an entry is, or what the open position waits for) when expanded. Open by default; the choice is remembered.
            const summaryKey = String(ticker || '').toUpperCase();
            const summaryPanelId = `bots-watch-summary-${summaryKey.replace(/[^a-zA-Z0-9_-]/g, '_')}`;
            const summaryExpanded = window.botsWatchSummaryExpanded[summaryKey] !== false;
            const detail = !firstScan && window.BotsSummary && content
                ? window.BotsSummary.build(content, { exitWatch: opts.exitWatch, position: opts.position, schedule: opts.schedule, timeframe: opts.timeframe }) : null;
            const BADGE = {
                buy: 'text-emerald-800 bg-emerald-100 border-emerald-400', sell: 'text-rose-800 bg-rose-100 border-rose-400',
                hold: 'text-sky-800 bg-sky-100 border-sky-300', neutral: 'text-slate-600 bg-slate-100 border-slate-200',
            };
            const headline = (detail && detail.headline) || card.summaryText || 'Waiting for the next scan.';
            const badgeHtml = detail
                ? `<span class="px-1.5 py-0.5 rounded border ${BADGE[detail.badge.tone] || BADGE.neutral} font-black text-[0.48rem] tracking-widest uppercase whitespace-nowrap shrink-0">${esc(detail.badge.text)}</span>` : '';
            const detailRows = detail ? detail.lines.map(([label, text]) => `<span class="break-words"><span class="font-black opacity-70">${esc(label)}:</span> <span class="font-semibold">${esc(text)}</span></span>`).join('') : '';
            watchSummaryHtml = `
                <section class="rounded-lg border ${tone} overflow-hidden w-full min-w-0" aria-label="Bots summary" data-bots-watch-summary="${card.state}">
                    <button type="button" data-watch-summary-key="${esc(summaryKey)}" aria-controls="${esc(summaryPanelId)}" aria-expanded="${summaryExpanded ? 'true' : 'false'}"
                        class="w-full flex items-center gap-1.5 px-1.5 py-0.5 sm:py-1 text-left hover:bg-indigo-50/60 transition-colors"
                        onclick="event.stopPropagation(); window.toggleBotsWatchSummary('${esc(summaryPanelId)}', this)">
                        <span class="text-[0.52rem] sm:text-[0.65rem] font-black uppercase tracking-tight sm:tracking-wider text-indigo-800 whitespace-nowrap shrink-0">Bots summary</span>
                        ${badgeHtml}
                        <span class="text-[0.58rem] sm:text-[0.65rem] font-semibold min-w-0 flex-1 whitespace-normal break-words line-clamp-3 leading-snug opacity-90" title="${esc(headline)}">${esc(headline)}</span>
                        <svg data-watch-summary-arrow class="ml-auto h-3 w-3 shrink-0 text-indigo-500 transition-transform ${summaryExpanded ? 'rotate-180' : ''}" viewBox="0 0 20 20" fill="currentColor" aria-hidden="true"><path fill-rule="evenodd" d="M5.23 7.21a.75.75 0 011.06.02L10 11.168l3.71-3.938a.75.75 0 111.08 1.04l-4.25 4.5a.75.75 0 01-1.08 0l-4.25-4.5a.75.75 0 01.02-1.06z" clip-rule="evenodd" /></svg>
                    </button>
                    <div id="${esc(summaryPanelId)}" class="${summaryExpanded ? '' : 'hidden '}border-t border-current/10 px-1.5 pb-1 pt-0.5 sm:pb-1.5 sm:pt-1 flex flex-col gap-0 sm:gap-0.5 text-[0.58rem] sm:text-[0.65rem] leading-snug">
                        ${detail ? '' : `<span class="font-bold break-words">${esc(headline)}</span>`}
                        ${detailRows}
                        ${forcedHtml ? `<div class="flex items-center gap-1.5 flex-wrap pt-0.5">${forcedHtml}</div>` : ''}
                        ${card.timingHtml || ''}
                    </div>
                </section>`;
            panelHtml = `
                <div class="flex flex-col gap-0">
                    <span class="text-[0.52rem] text-slate-500 font-semibold leading-snug break-words">${esc(scanLine)}</span>
                    ${closedSummary}
                </div>
                <div class="text-[0.52rem] sm:text-[0.6rem] font-medium leading-snug break-words min-w-0">${card.html}</div>`;
        }

        const setupBadgesHtml = setupBadges(setup, phase, content && content.strategy_log ? content.strategy_log.untested : '');
        const entryLabel = setup && setup.kind === 'manual' ? setup.entry : '';
        const exitLabel = setup && setup.kind === 'manual' ? setup.exit : '';
        return `
            <div class="mt-0 sm:mt-1 flex flex-col gap-0.5 sm:gap-1 w-full min-w-0" data-bots-scan-card>
                ${setupBadgesHtml ? `<div class="flex items-center gap-1.5 flex-wrap pb-0.5">${setupBadgesHtml}</div>` : ''}
                <div class="rounded-lg border border-slate-200 bg-slate-50/80 px-1.5 py-0.5 sm:py-1 flex flex-col gap-0.5 sm:gap-1 min-w-0">
                    <div class="flex items-center gap-1 sm:gap-1.5 flex-wrap cursor-pointer select-none" title="Expand or collapse scan details"
                        onclick="event.stopPropagation(); window.toggleBotsScanLog('${esc(panelId)}', this.querySelector('[data-scan-log-key]'))">
                        <span class="order-1 whitespace-nowrap text-[0.52rem] sm:text-[0.65rem] font-black uppercase tracking-tight sm:tracking-wider text-indigo-800" data-section-title>Last scan result</span>
                        ${card ? headerHtml : `<span class="order-3 sm:order-2 w-full sm:w-auto flex items-center gap-1.5 flex-wrap">${headerChips}</span>`}
                        ${card ? '' : toggle}
                    </div>
                    <div id="${esc(panelId)}" class="${expanded ? '' : 'hidden '}border-t border-slate-200/70 pt-0.5 sm:pt-1 flex flex-col gap-0.5 sm:gap-1">
                        ${card ? panelHtml : `${summarySection(summaryHtml, timingHtml)}
                        ${watchSection((strategy && strategy.titles && strategy.titles.entry) || 'Entry setup watch', entryLabel, entryActive, entryHtml)}
                        ${watchSection((strategy && strategy.titles && strategy.titles.exit) || 'Exit setup watch', exitLabel, exitActive, exitHtml)}`}
                    </div>
                </div>
                ${watchSummaryHtml}
                ${closed ? (opts.closedBannerHtml || '') : ''}
            </div>`;
    }

    window.BotsScanCard = { render };
})();
