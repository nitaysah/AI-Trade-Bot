/*
 * Card sections and log-row chips for bots running a strategy (the Bots card and the Logs table).
 *
 * The server builds the content (backend/strategy_scan_log.py); this file only renders it. Every string is
 * escaped. Phone first: chips wrap, text breaks, nothing sets a fixed width.
 */
(function () {
    'use strict';
    if (window.StrategyLog) return;

    const esc = (value) => String(value == null ? '' : value)
        .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;').replace(/'/g, '&#39;');

    const STATUS_TONE = {
        STRATEGY_ERROR: 'bg-rose-50 text-rose-700 border-rose-200',
        WAITING_DATA: 'bg-amber-50 text-amber-700 border-amber-200',
        DEGRADED: 'bg-rose-50 text-rose-700 border-rose-200',
        BLOCKED: 'bg-rose-50 text-rose-700 border-rose-200',
        ENTRY_READY: 'bg-emerald-50 text-emerald-700 border-emerald-200',
        EXIT_SIGNAL: 'bg-orange-50 text-orange-700 border-orange-200',
        HOLDING: 'bg-indigo-50 text-indigo-700 border-indigo-200',
        IDLE: 'bg-slate-100 text-slate-600 border-slate-200',
        WATCHING: 'bg-blue-50 text-blue-700 border-blue-200',
        SCHEDULED: 'bg-blue-50 text-blue-700 border-blue-200',
        ORDER_PENDING: 'bg-amber-50 text-amber-700 border-amber-200',
        REBALANCED: 'bg-emerald-50 text-emerald-700 border-emerald-200',
        HOLDING_OVERNIGHT: 'bg-indigo-50 text-indigo-700 border-indigo-200',
        HOLDING_INTRADAY: 'bg-indigo-50 text-indigo-700 border-indigo-200',
        EXIT_OVERDUE: 'bg-rose-50 text-rose-700 border-rose-200',
        FLAT: 'bg-blue-50 text-blue-700 border-blue-200',
    };

    function statusClass(status) {
        return STATUS_TONE[status] || STATUS_TONE.WATCHING;
    }

    function chip(label, value, tone) {
        const color = tone === 'up' ? 'text-emerald-700' : (tone === 'down' ? 'text-rose-700' : 'text-slate-700');
        return `<span class="px-1.5 py-0.5 rounded bg-white/70 border border-slate-200/70 font-mono text-[0.5rem] leading-tight whitespace-nowrap">`
            + `<span class="text-slate-400 font-bold">${esc(label)}</span> <span class="font-black ${color}">${esc(value)}</span></span>`;
    }

    function localTime(iso) {
        const date = new Date(iso);
        if (isNaN(date.getTime())) return '';
        return date.toLocaleString([], { weekday: 'short', month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit', hour12: false });
    }

    function blockerList(blockers) {
        const list = Array.isArray(blockers) ? blockers.filter(Boolean) : [];
        return list.length
            ? `<div class="flex flex-col gap-0.5">${list.map((b) => `<span class="text-[0.52rem] font-black text-rose-600 leading-snug break-words">\u26d4 ${esc(b)}</span>`).join('')}</div>` : '';
    }

    const muted = (text) => `<span class="text-[0.52rem] font-bold text-slate-400 leading-snug break-words">${esc(text)}</span>`;
    const ruleLine = (label, text) => (text
        ? `<span class="text-[0.5rem] font-bold text-slate-500 leading-snug break-words"><span class="font-black uppercase tracking-wider text-slate-400">${esc(label)}</span> ${esc(text)}</span>` : '');

    /*
     * The pieces of the Bots card for a strategy bot: summary line, entry watch, exit watch and timing.
     * Entry and exit watch are both always present: the one that does not apply right now says why.
     */
    function countdownText(untilIso) {
        const ms = new Date(untilIso).getTime() - Date.now();
        if (!isFinite(ms)) return '';
        if (ms <= 0) return 'due now';
        const minutes = Math.floor(ms / 60000), days = Math.floor(minutes / 1440), hours = Math.floor((minutes % 1440) / 60);
        return days ? `in ${days}d ${hours}h` : (hours ? `in ${hours}h ${minutes % 60}m` : `in ${minutes}m`);
    }

    /*
     * Live exit rows for an open position: each exit the strategy has, its real distance, and a bar for how near it is
     * (empty = far, full = triggers). The bar never carries meaning alone: the % is written next to it.
     */
    function exitWatchHtml(rows) {
        if (!Array.isArray(rows) || !rows.length) return '';
        return rows.map((row) => {
            let near = row.near_pct;
            let value = row.value;
            let nearText = row.near_text || 'near this exit';
            if (row.kind === 'time' && row.until) {
                const start = new Date(row.since).getTime(), end = new Date(row.until).getTime();
                near = isFinite(start) && isFinite(end) && end > start ? Math.max(0, Math.min(100, Math.round(((Date.now() - start) / (end - start)) * 100))) : null;
                value = `${row.value}${countdownText(row.until) ? ' · ' + countdownText(row.until) : ''}`;
                nearText = 'of the hold elapsed';
            }
            const tone = near == null ? 'bg-slate-300' : (near >= 80 ? 'bg-rose-500' : (near >= 50 ? 'bg-amber-400' : 'bg-emerald-400'));
            const bar = near == null ? '' : `<div role="progressbar" aria-valuemin="0" aria-valuemax="100" aria-valuenow="${near}" aria-label="${esc(row.label)}: ${near}% ${esc(nearText)}" class="h-1.5 w-full rounded-full bg-slate-200 overflow-hidden"><div class="h-full rounded-full ${tone}" style="width:${near}%"></div></div>`
                + `<span class="text-[0.48rem] font-black text-slate-500">${near}% ${esc(nearText)}</span>`;
            return `<div class="flex flex-col gap-0.5 min-w-0">`
                + `<div class="flex items-baseline justify-between gap-2 flex-wrap"><span class="text-[0.52rem] font-black uppercase tracking-wider text-slate-500 break-words">${esc(row.label)}</span>`
                + `<span class="text-[0.52rem] font-black font-mono text-slate-800 break-words">${esc(value)}</span></div>`
                + bar
                + (row.detail ? `<span class="text-[0.48rem] font-bold text-slate-400 leading-snug break-words">${esc(row.detail)}</span>` : '')
                + `</div>`;
        }).join('');
    }

    /* Malik allocation bots: regime, target vs held per ETF leg, and the daily execution time. */
    function allocationSections(view, blockers) {
        const alloc = view.allocation || {};
        const rules = view.rules || {};
        const metrics = Array.isArray(view.metrics) ? view.metrics : [];
        const metricRow = metrics.length
            ? `<div class="flex items-center gap-1 flex-wrap">${metrics.map((m) => chip(m.label, m.value, m.tone)).join('')}</div>` : '';
        const money = (v) => (v == null || !isFinite(Number(v)) ? '—' : `$${Number(v).toLocaleString(undefined, { maximumFractionDigits: 0 })}`);
        const pct = (v) => (v == null || !isFinite(Number(v)) ? '—' : `${Number(v).toFixed(0)}%`);
        const legs = (alloc.legs || []).map((leg) => {
            const off = leg.held_pct != null && Math.abs(Number(leg.held_pct) - Number(leg.target_pct)) > 5;
            return `<div class="flex items-center gap-1 flex-wrap">`
                + `<span class="text-[0.55rem] font-black text-indigo-900 min-w-[2.6rem]">${esc(leg.symbol)}</span>`
                + chip('Target', pct(leg.target_pct)) + chip('Held', pct(leg.held_pct), off ? 'down' : '')
                + chip('Value', money(leg.value)) + (leg.pending ? chip('Order', 'working', 'down') : '') + `</div>`;
        }).join('');
        const sleeve = alloc.sleeve != null ? muted(`Sleeve ${money(alloc.sleeve)} (Fixed: your amount · Compound: amount + P/L).`) : '';
        const headline = `<span class="text-[0.55rem] sm:text-[0.6rem] font-bold leading-snug break-words">${esc(view.headline)}</span>`;
        const research = view.untested ? `<span class="text-[0.5rem] font-bold text-amber-700 leading-snug break-words">${esc(view.untested)}</span>` : '';
        const timingParts = [];
        if (alloc.execution_time) timingParts.push(`Executes daily: ${esc(alloc.execution_time)}`);
        if (alloc.next_decision) timingParts.push(`Next decision: ${esc(alloc.next_decision)}`);
        return {
            status: view.status,
            summary: `${view.status_label || view.status} \u00b7 ${view.headline || ''}`.trim(),
            entryHtml: headline + blockerList(blockers) + metricRow + legs + sleeve,
            exitHtml: ruleLine('Decides', rules.entry) + ruleLine('Reduces', rules.exit) + ruleLine('', rules.stop) + research,
            timingHtml: timingParts.length ? `<span class="text-[0.48rem] font-bold text-slate-400 leading-snug break-words">${timingParts.join(' \u00b7 ')}</span>` : '',
            entryActive: true,
            titles: { entry: 'Regime & targets', exit: 'Rules' },
        };
    }

    /* Session Rider bots: buy/sell times, where the schedule is, the open position and the next action. */
    function scheduleSections(view, blockers) {
        const rules = view.rules || {};
        const sched = view.schedule || {};
        const metrics = Array.isArray(view.metrics) ? view.metrics : [];
        const metricRow = metrics.length
            ? `<div class="flex items-center gap-1 flex-wrap">${metrics.map((m) => chip(m.label, m.value, m.tone)).join('')}</div>` : '';
        const overdue = view.status === 'EXIT_OVERDUE';
        const headline = `<span class="text-[0.55rem] sm:text-[0.6rem] font-bold leading-snug break-words ${overdue ? 'text-rose-600' : ''}">${esc(view.headline)}</span>`;
        const position = view.position;
        const scheduleExitRows = exitWatchHtml(view.exit_watch);
        const positionRow = position && !scheduleExitRows
            ? `<div class="flex items-center gap-1 flex-wrap">${chip('Entry', position.entry)}${chip('Now', position.current)}`
                + (position.pl_pct != null ? chip('P/L', `${position.pl_pct > 0 ? '+' : ''}${position.pl_pct}%`, position.pl_pct > 0 ? 'up' : (position.pl_pct < 0 ? 'down' : '')) : '')
                + `</div><span class="text-[0.48rem] font-bold text-slate-500 leading-snug break-words">${esc(position.stop_note || '')}</span>` : '';
        const note = view.untested ? `<span class="text-[0.5rem] font-bold text-amber-700 leading-snug break-words">${esc(view.untested)}</span>` : '';
        const timing = sched.next_label ? `<span class="text-[0.48rem] font-bold text-slate-400 leading-snug break-words">Next ${esc(String(sched.next_event || '').toLowerCase())}: ${esc(sched.next_label)}</span>` : '';
        return {
            status: view.status,
            summary: `${view.status_label || view.status} \u00b7 ${view.headline || ''}`.trim(),
            entryHtml: headline + blockerList(blockers) + metricRow + positionRow,
            exitHtml: scheduleExitRows + ruleLine('Buys', rules.entry) + ruleLine('Sells', rules.exit) + ruleLine('', rules.stop) + note,
            timingHtml: timing,
            entryActive: true,
            titles: { entry: 'Schedule', exit: 'Rules' },
        };
    }

    function sections(view, blockers) {
        if (!view || typeof view !== 'object') return null;
        if (view.kind === 'allocation') return allocationSections(view, blockers);
        if (view.kind === 'schedule') return scheduleSections(view, blockers);
        const metrics = Array.isArray(view.metrics) ? view.metrics : [];
        const position = view.position;
        const timing = view.timing || {};
        const rules = view.rules || {};
        const metricRow = metrics.length
            ? `<div class="flex items-center gap-1 flex-wrap">${metrics.map((m) => chip(m.label, m.value, m.tone)).join('')}</div>` : '';
        const headline = `<span class="text-[0.55rem] sm:text-[0.6rem] font-bold leading-snug break-words">${esc(view.headline)}</span>`;

        const exitRows = exitWatchHtml(view.exit_watch);
        let positionRow = '';
        if (position && !exitRows) {
            const pl = position.pl_pct;
            const stop = position.stop
                ? chip('Stop', `${position.stop}${position.stop_distance_pct != null ? ` (${position.stop_distance_pct}% below)` : ''}`)
                : chip('Stop', 'not recorded yet');
            positionRow = `<div class="flex items-center gap-1 flex-wrap">`
                + chip('Entry', position.entry) + chip('Now', position.current)
                + (pl != null ? chip('P/L', `${pl > 0 ? '+' : ''}${pl}%`, pl > 0 ? 'up' : (pl < 0 ? 'down' : '')) : '')
                + stop + `</div>`
                + `<span class="text-[0.48rem] font-bold text-slate-500 leading-snug break-words">${esc(position.stop_rule ? position.stop_rule + '. ' : '')}${esc(position.stop_note || '')}</span>`;
        }

        const entryHtml = position
            ? muted('A position is open: entry watch resumes after the exit.') + ruleLine('Buys when', rules.entry)
            : headline + blockerList(blockers) + metricRow + ruleLine('Buys when', rules.entry);
        const exitHtml = position
            ? headline + blockerList(blockers) + metricRow + (exitRows || positionRow) + ruleLine('Sells when', rules.exit)
            : muted('No open position: the exit watch starts after an entry.') + ruleLine('Sells when', rules.exit) + ruleLine('', rules.stop);

        const timingParts = [];
        if (timing.candle) timingParts.push(`Candle evaluated: ${esc(timing.candle)}`);
        if (timing.idle && timing.next_evaluation_at) timingParts.push(`Next evaluation: ${esc(localTime(timing.next_evaluation_at))}`);
        return {
            status: view.status,
            // "Holding · sells when..." already starts with the status word: do not repeat it.
            summary: String(view.headline || '').toLowerCase().startsWith(String(view.status_label || '').toLowerCase())
                ? String(view.headline)
                : `${view.status_label || view.status} \u00b7 ${view.headline || ''}`.trim(),
            entryHtml,
            exitHtml,
            timingHtml: timingParts.length ? `<span class="text-[0.48rem] font-bold text-slate-400 leading-snug break-words">${timingParts.join(' \u00b7 ')}</span>` : '',
            entryActive: !position,
        };
    }

    const EVENT_TONE = {
        'ENTRY': 'text-emerald-600 font-bold',
        'STRATEGY EXIT': 'text-red-500 font-bold',
        'STOP EXIT': 'text-red-600 font-black',
        'WAITING DATA': 'text-amber-600 font-medium',
        'DEGRADED': 'text-rose-600 font-medium',
        'HELD BACK': 'text-rose-500 font-medium',
        'WATCHING': 'text-blue-600 font-medium',
        'BUY': 'text-emerald-600 font-bold',
        'SELL': 'text-red-500 font-bold',
        'EXIT LATE': 'text-red-600 font-black',
        'EXIT RETRY': 'text-rose-600 font-black',
        'SKIPPED': 'text-amber-600 font-medium',
        'MISSED ENTRY': 'text-amber-600 font-medium',
        'ORDER PENDING': 'text-amber-600 font-medium',
    };

    /* Action label + colour for a log row of a strategy bot. */
    function rowEvent(strategy) {
        if (!strategy || !strategy.event) return null;
        const event = String(strategy.event);
        const tone = EVENT_TONE[event]
            || (event.startsWith('BUY ') ? 'text-emerald-600 font-bold' : event.startsWith('SELL ') ? 'text-red-500 font-bold'
                : event === 'REBALANCE' ? 'text-purple-600 font-black' : 'text-purple-500 font-medium');
        return { label: event, color: tone };
    }

    /* The "Signals" cell of a strategy row: the strategy's own numbers instead of "0B / 0S". */
    function rowChip(item) {
        const strategy = item && item.strategy;
        if (!strategy) return '';
        const repeat = Number(item.repeat_count) > 1
            ? ` <span class="px-1 rounded bg-slate-100 text-slate-500 font-black text-[0.5rem]" title="Identical checks collapsed into one row">×${esc(item.repeat_count)}</span>` : '';
        const lines = chipLines(strategy.chip);
        const body = lines.length > 1
            ? `<span class="block whitespace-nowrap">${esc(lines[0])}</span><span class="block whitespace-nowrap text-indigo-500">${esc(lines[1])}</span>`
            : `<span class="block whitespace-nowrap">${esc(lines[0] || '—')}</span>`;
        return `<span class="inline-block px-1.5 py-0.5 rounded bg-indigo-50 text-indigo-700 border border-indigo-100 font-mono text-[0.55rem] sm:text-[0.6rem] leading-tight align-top"${strategy.chip ? ` title="${esc(strategy.chip)}"` : ''}>${body}</span>${repeat}`;
    }

    /* A long chip such as "SMA20 742.25 < SMA50 744.30 (-0.27%)" would stretch the table: split it in two lines, the
       numbers first and the gap on the second. Without a bracketed part, a long chip is split at its middle separator. */
    function chipLines(chip) {
        const text = String(chip || '').trim();
        if (!text) return [];
        const paren = text.indexOf(' (');
        if (paren > 0) return [text.slice(0, paren), text.slice(paren + 1)];
        if (text.length <= 22) return [text];
        const middle = text.length / 2;
        let best = -1;
        for (const separator of [' · ', ', ', ' < ', ' > ', ' ']) {
            for (let index = text.indexOf(separator); index !== -1; index = text.indexOf(separator, index + 1)) {
                if (best === -1 || Math.abs(index - middle) < Math.abs(best - middle)) best = index;
            }
            if (best !== -1) { return [text.slice(0, best + (separator === ', ' ? 1 : 0)), text.slice(best + separator.length)]; }
        }
        return [text];
    }

    window.StrategyLog = { sections, rowEvent, rowChip, chipLines, statusClass, esc, muted, blockerList };
})();
