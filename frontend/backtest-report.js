/* Backtest result report: Edge vs Buy & Hold, consistency (first vs second
 * half), monthly returns, trade breakdown, recent-run comparison, run record
 * and CSV export. Reads only the backtest response; never calls the broker.
 * Rendered after backtest.js has drawn the main metrics and charts.
 */
(function () {
    'use strict';

    const RECENT_KEY_PREFIX = 'botbulls.backtest.recentRuns.v1';
    const RECENT_LIMIT = 3;
    const MONTHS = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];
    const EXIT_LABELS = {
        stop: 'Stop', target: 'Target', signal: 'Signal', grid: 'Grid', end: 'End of test', other: 'Other',
    };
    const EXIT_TONES = {
        stop: 'bg-rose-50 text-rose-600 border-rose-100',
        target: 'bg-emerald-50 text-emerald-700 border-emerald-100',
        signal: 'bg-indigo-50 text-indigo-700 border-indigo-100',
        grid: 'bg-sky-50 text-sky-700 border-sky-100',
        end: 'bg-slate-50 text-slate-500 border-slate-200',
        other: 'bg-slate-50 text-slate-500 border-slate-200',
    };

    let lastResult = null;

    // ── Formatting ───────────────────────────────────────────────────────────
    const esc = (value) => String(value ?? '').replace(/[&<>"']/g, (c) => ({
        '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;',
    }[c]));
    const num = (v) => (v === undefined || v === null || v === '' || !Number.isFinite(Number(v))) ? null : Number(v);
    const pct = (v, digits = 2, signed = true) => {
        const n = num(v);
        if (n === null) return '—';
        return `${signed && n > 0 ? '+' : ''}${n.toFixed(digits)}%`;
    };
    const money = (v, signed = false) => {
        const n = num(v);
        if (n === null) return '—';
        const abs = Math.abs(n).toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 });
        return `${n < 0 ? '-' : (signed && n > 0 ? '+' : '')}$${abs}`;
    };
    const ratio = (v) => { const n = num(v); return n === null ? '—' : n.toFixed(2); };
    const tone = (v) => { const n = num(v); return n === null ? 'text-indigo-950' : (n >= 0 ? 'text-emerald-600' : 'text-rose-600'); };
    const day = (iso) => {
        if (!iso) return '—';
        const d = new Date(String(iso).replace(' ', 'T'));
        if (Number.isNaN(d.getTime())) return String(iso);
        return d.toLocaleDateString('en-US', { timeZone: 'America/Chicago', month: 'short', day: 'numeric', year: '2-digit' });
    };
    const holdText = (hours) => {
        const h = num(hours);
        if (h === null) return '';
        if (h < 1) return `${Math.max(1, Math.round(h * 60))}m`;
        if (h < 24) return `${h.toFixed(h < 10 ? 1 : 0)}h`;
        const d = h / 24;
        return `${d.toFixed(d < 10 ? 1 : 0)}d`;
    };

    const panel = (title, accent, body, extraHeader = '') => `
        <div class="rounded-2xl border border-indigo-100/70 shadow-sm overflow-hidden bg-white">
            <div class="flex flex-wrap items-center gap-2 px-4 py-3 border-b border-indigo-50/70">
                <div class="w-1 h-5 ${accent} rounded-full"></div>
                <h3 class="text-xs font-black text-indigo-950 uppercase tracking-tight mr-auto">${title}</h3>
                ${extraHeader}
            </div>
            ${body}
        </div>`;
    const badge = (text, cls) => `<span class="inline-flex items-center px-2 py-0.5 rounded-lg border text-[0.55rem] font-black uppercase tracking-wider ${cls}">${esc(text)}</span>`;

    // ── Exit category helpers (shared with the trade table) ──────────────────
    function exitLabel(category) { return EXIT_LABELS[category] || EXIT_LABELS.other; }
    function exitBadge(category) {
        const key = EXIT_LABELS[category] ? category : 'other';
        return `<span class="inline-flex items-center px-1.5 py-0.5 rounded-md border text-[0.5rem] font-black uppercase tracking-wider ${EXIT_TONES[key]}">${esc(EXIT_LABELS[key])}</span>`;
    }

    // ── 1. Edge vs Buy & Hold ────────────────────────────────────────────────
    function renderEdge(s) {
        const host = document.getElementById('btEdgeBody');
        if (!host) return;
        const e = s.edge_vs_buy_hold;
        if (!e) { host.classList.add('hidden'); host.innerHTML = ''; return; }
        const verdict = {
            beats: ['Beats buy & hold', 'bg-emerald-50 text-emerald-700 border-emerald-200'],
            lags: ['Lags buy & hold', 'bg-rose-50 text-rose-600 border-rose-200'],
            tie: ['Ties buy & hold', 'bg-amber-50 text-amber-700 border-amber-200'],
        }[e.verdict] || ['—', 'bg-slate-50 text-slate-500 border-slate-200'];
        const perDd = (v, roi) => (num(v) === null ? (num(roi) > 0 ? 'No drawdown' : '—') : `${ratio(v)}x`);
        const row = (label, a, b, aCls = 'text-indigo-950', bCls = 'text-indigo-950') => `
            <tr class="border-t border-indigo-50/70">
                <td class="py-1.5 pr-2 text-[0.6rem] font-semibold text-indigo-600">${label}</td>
                <td class="py-1.5 px-2 text-right font-mono font-black text-[0.65rem] ${aCls}">${a}</td>
                <td class="py-1.5 pl-2 text-right font-mono font-black text-[0.65rem] ${bCls}">${b}</td>
            </tr>`;
        host.innerHTML = `
            <div class="flex flex-wrap items-center gap-2 mb-2">
                ${badge(verdict[0], verdict[1])}
                <span class="text-[0.55rem] font-bold text-indigo-400">Judged on return ÷ max drawdown, same window and starting capital</span>
            </div>
            <table class="w-full border-collapse">
                <thead>
                    <tr class="text-[0.5rem] font-black uppercase tracking-widest text-indigo-400">
                        <th class="pb-1 text-left font-black"></th>
                        <th class="pb-1 px-2 text-right font-black">Strategy</th>
                        <th class="pb-1 pl-2 text-right font-black">Buy &amp; Hold</th>
                    </tr>
                </thead>
                <tbody>
                    ${row('Return', pct(e.strategy_return_pct), pct(e.buy_hold_return_pct), tone(e.strategy_return_pct), tone(e.buy_hold_return_pct))}
                    ${row('Max drawdown', `-${num(e.strategy_max_drawdown_pct)?.toFixed(2) ?? '0.00'}%`, `-${num(e.buy_hold_max_drawdown_pct)?.toFixed(2) ?? '0.00'}%`, 'text-rose-600', 'text-rose-600')}
                    ${row('Return ÷ drawdown', perDd(e.strategy_return_per_drawdown, e.strategy_return_pct), perDd(e.buy_hold_return_per_drawdown, e.buy_hold_return_pct), 'text-violet-700', 'text-violet-700')}
                    ${num(s.exposure_pct) === null ? '' : row('Time in market', `${num(s.exposure_pct).toFixed(1)}%`, '100.0%')}
                </tbody>
            </table>
            <p class="text-[0.5rem] font-bold text-slate-400 mt-1.5 leading-snug">${esc(e.rule || '')}</p>`;
        host.classList.remove('hidden');
    }

    // ── 2. First half vs second half ─────────────────────────────────────────
    function consistencyPanel(s) {
        const halves = Array.isArray(s.period_split) ? s.period_split : [];
        if (halves.length !== 2) return '';
        const profitable = halves.map((h) => num(h.return_pct) !== null && num(h.return_pct) > 0);
        let verdict;
        if (profitable[0] && profitable[1]) verdict = badge('Both halves profitable', 'bg-emerald-50 text-emerald-700 border-emerald-200');
        else if (profitable[0] || profitable[1]) verdict = badge(`Only the ${profitable[0] ? 'first' : 'second'} half was profitable`, 'bg-amber-50 text-amber-700 border-amber-200');
        else verdict = badge('Neither half was profitable', 'bg-rose-50 text-rose-600 border-rose-200');
        const card = (h) => `
            <div class="rounded-xl border border-indigo-50 bg-slate-50/40 p-2.5 min-w-0">
                <p class="text-[0.5rem] font-black uppercase tracking-widest text-indigo-400">${esc(h.label)}</p>
                <p class="text-[0.5rem] font-bold text-slate-400 mb-1 break-words">${esc(day(h.start_time))} – ${esc(day(h.end_time))}</p>
                <p class="font-mono font-black text-sm ${tone(h.return_pct)}">${pct(h.return_pct)}</p>
                <div class="mt-1 space-y-0.5 text-[0.55rem] font-semibold text-indigo-600">
                    <div class="flex justify-between gap-1"><span>Trades closed</span><span class="font-mono font-black text-indigo-950">${num(h.trades) ?? 0}</span></div>
                    <div class="flex justify-between gap-1"><span>Win rate</span><span class="font-mono font-black text-indigo-950">${h.win_rate_pct === null || h.win_rate_pct === undefined ? '—' : `${num(h.win_rate_pct).toFixed(1)}%`}</span></div>
                    <div class="flex justify-between gap-1"><span>Closed P/L</span><span class="font-mono font-black ${tone(h.pl)}">${money(h.pl, true)}</span></div>
                </div>
            </div>`;
        const body = `
            <div class="px-4 py-3 space-y-2">
                <div class="flex flex-wrap items-center gap-2">${verdict}</div>
                <div class="grid grid-cols-2 gap-2">${card(halves[0])}${card(halves[1])}</div>
                <p class="text-[0.5rem] font-bold text-slate-400 leading-snug">The same settings on the same data, split in two by time. A strategy that only made money in one half may depend on one market period.</p>
            </div>`;
        return panel('Consistency Check', 'bg-gradient-to-b from-teal-500 to-indigo-600', body);
    }

    // ── 3. Monthly returns ──────────────────────────────────────────────────
    function monthlyPanel(s) {
        const rows = Array.isArray(s.monthly_returns) ? s.monthly_returns : [];
        if (!rows.length) return '';
        const byYear = new Map();
        rows.forEach((r) => {
            if (!byYear.has(r.year)) byYear.set(r.year, new Array(12).fill(null));
            byYear.get(r.year)[r.month - 1] = num(r.return_pct);
        });
        const maxAbs = Math.max(0.01, ...rows.map((r) => Math.abs(num(r.return_pct) || 0)));
        const cell = (v) => {
            if (v === null) return '<td class="px-1 py-1.5 text-center text-[0.55rem] text-slate-300 font-mono">·</td>';
            const alpha = (0.1 + Math.min(1, Math.abs(v) / maxAbs) * 0.55).toFixed(2);
            const bg = v >= 0 ? `rgba(16,185,129,${alpha})` : `rgba(244,63,94,${alpha})`;
            const fg = Math.abs(v) / maxAbs > 0.6 ? '#ffffff' : (v >= 0 ? '#047857' : '#be123c');
            return `<td class="px-1 py-1.5 text-center text-[0.55rem] font-mono font-black whitespace-nowrap" style="background:${bg};color:${fg}" title="${esc(pct(v))}">${v > 0 ? '+' : ''}${v.toFixed(1)}</td>`;
        };
        const years = [...byYear.keys()].sort((a, b) => a - b);
        const body = years.map((y) => {
            const months = byYear.get(y);
            const known = months.filter((v) => v !== null);
            const yearRet = known.length ? (known.reduce((acc, v) => acc * (1 + v / 100), 1) - 1) * 100 : null;
            return `<tr class="border-t border-white">
                <th class="sticky left-0 z-10 bg-white px-2 py-1.5 text-left text-[0.55rem] font-black text-indigo-950">${y}</th>
                ${months.map(cell).join('')}
                <td class="px-2 py-1.5 text-right text-[0.55rem] font-mono font-black ${tone(yearRet)} whitespace-nowrap">${yearRet === null ? '—' : pct(yearRet, 1)}</td>
            </tr>`;
        }).join('');
        const positive = rows.filter((r) => num(r.return_pct) > 0).length;
        const best = rows.reduce((a, r) => (num(r.return_pct) > num(a.return_pct) ? r : a), rows[0]);
        const worst = rows.reduce((a, r) => (num(r.return_pct) < num(a.return_pct) ? r : a), rows[0]);
        const chips = `
            <div class="flex flex-wrap gap-1.5 text-[0.55rem] font-mono font-bold">
                <span class="px-2 py-0.5 rounded-lg border bg-indigo-50 text-indigo-700 border-indigo-100">Up months: ${positive}/${rows.length}</span>
                <span class="px-2 py-0.5 rounded-lg border bg-emerald-50 text-emerald-700 border-emerald-100">Best: ${MONTHS[best.month - 1]} ${best.year} ${pct(best.return_pct, 1)}</span>
                <span class="px-2 py-0.5 rounded-lg border bg-rose-50 text-rose-600 border-rose-100">Worst: ${MONTHS[worst.month - 1]} ${worst.year} ${pct(worst.return_pct, 1)}</span>
            </div>`;
        const table = `
            <div class="px-4 pt-3 pb-1">${chips}</div>
            <div class="overflow-x-auto px-2 pb-3 pt-2">
                <table class="min-w-[560px] w-full border-collapse">
                    <thead>
                        <tr class="text-[0.5rem] font-black uppercase tracking-wider text-indigo-400">
                            <th class="sticky left-0 z-10 bg-white px-2 py-1 text-left">Year</th>
                            ${MONTHS.map((m) => `<th class="px-1 py-1 text-center">${m}</th>`).join('')}
                            <th class="px-2 py-1 text-right">Year</th>
                        </tr>
                    </thead>
                    <tbody>${body}</tbody>
                </table>
            </div>
            <p class="px-4 pb-3 text-[0.5rem] font-bold text-slate-400">Month-end account equity versus the previous month-end (open positions valued at the close).</p>`;
        return panel('Monthly Returns', 'bg-gradient-to-b from-emerald-500 to-rose-500', table);
    }

    // ── 4. Trade breakdown ──────────────────────────────────────────────────
    function tradesPanel(res) {
        const trades = (res.trades || []).filter((t) => num(t.pl) !== null);
        if (!trades.length) return '';
        const groups = {};
        trades.forEach((t) => {
            const key = EXIT_LABELS[t.exit_category] ? t.exit_category : 'other';
            groups[key] = groups[key] || { n: 0, pl: 0, pctSum: 0 };
            groups[key].n += 1;
            groups[key].pl += Number(t.pl);
            groups[key].pctSum += num(t.pl_pct) || 0;
        });
        const order = ['stop', 'target', 'signal', 'grid', 'end', 'other'];
        const reasonRows = order.filter((k) => groups[k]).map((k) => {
            const g = groups[k];
            return `<tr class="border-t border-indigo-50/70">
                <td class="py-1.5 pr-2">${exitBadge(k)}</td>
                <td class="py-1.5 px-2 text-right font-mono font-black text-[0.6rem] text-indigo-950">${g.n}</td>
                <td class="py-1.5 px-2 text-right font-mono font-bold text-[0.6rem] text-indigo-500">${(g.n / trades.length * 100).toFixed(0)}%</td>
                <td class="py-1.5 px-2 text-right font-mono font-black text-[0.6rem] ${tone(g.pl)}">${money(g.pl, true)}</td>
                <td class="py-1.5 pl-2 text-right font-mono font-bold text-[0.6rem] ${tone(g.pctSum / g.n)}">${pct(g.pctSum / g.n)}</td>
            </tr>`;
        }).join('');

        const sorted = [...trades].sort((a, b) => Number(b.pl) - Number(a.pl));
        const listRow = (t) => `
            <li class="flex items-center justify-between gap-2 py-1 border-t border-indigo-50/70 first:border-t-0">
                <span class="min-w-0">
                    <span class="block text-[0.55rem] font-bold text-indigo-950">${esc(day(t.exit_time))}</span>
                    <span class="block">${exitBadge(t.exit_category)} <span class="text-[0.5rem] font-bold text-slate-400">${esc(holdText(t.hold_hours))}</span></span>
                </span>
                <span class="text-right font-mono">
                    <span class="block text-[0.6rem] font-black ${tone(t.pl)}">${money(t.pl, true)}</span>
                    <span class="block text-[0.5rem] font-bold ${tone(t.pl_pct)}">${pct(t.pl_pct)}</span>
                </span>
            </li>`;
        const best = sorted.slice(0, 3);
        const worst = sorted.slice(-3).reverse().filter((t) => !best.includes(t));

        const net = trades.reduce((acc, t) => acc + Number(t.pl), 0);
        const top = sorted[0];
        const top3 = sorted.slice(0, 3).reduce((acc, t) => acc + Math.max(0, Number(t.pl)), 0);
        let concentration;
        if (net > 0 && Number(top.pl) > 0) {
            const share = Number(top.pl) / net * 100;
            const warn = share > 50;
            concentration = `
                <div class="rounded-xl border ${warn ? 'border-amber-200 bg-amber-50/60' : 'border-indigo-50 bg-slate-50/40'} px-3 py-2">
                    <p class="text-[0.6rem] font-black ${warn ? 'text-amber-700' : 'text-indigo-950'}">Top trade = ${share.toFixed(0)}% of net profit${trades.length >= 3 ? ` · top 3 = ${(top3 / net * 100).toFixed(0)}%` : ''}</p>
                    <p class="text-[0.5rem] font-bold ${warn ? 'text-amber-600' : 'text-slate-400'} mt-0.5">${warn ? 'Most of the profit came from one trade, so the result is fragile.' : 'Profit is not dominated by a single trade.'}</p>
                </div>`;
        } else {
            concentration = '<div class="rounded-xl border border-indigo-50 bg-slate-50/40 px-3 py-2"><p class="text-[0.6rem] font-black text-indigo-950">No net profit</p><p class="text-[0.5rem] font-bold text-slate-400 mt-0.5">Profit concentration does not apply.</p></div>';
        }

        const body = `
            <div class="px-4 py-3 space-y-3">
                ${concentration}
                <div>
                    <p class="text-[0.5rem] font-black uppercase tracking-widest text-indigo-400 mb-1">Exits by reason</p>
                    <div class="overflow-x-auto">
                        <table class="w-full border-collapse">
                            <thead><tr class="text-[0.5rem] font-black uppercase tracking-wider text-indigo-400">
                                <th class="pb-1 text-left">Exit</th><th class="pb-1 px-2 text-right">Trades</th><th class="pb-1 px-2 text-right">Share</th><th class="pb-1 px-2 text-right">P/L</th><th class="pb-1 pl-2 text-right">Avg</th>
                            </tr></thead>
                            <tbody>${reasonRows}</tbody>
                        </table>
                    </div>
                </div>
                <div class="grid grid-cols-1 sm:grid-cols-2 gap-3">
                    <div class="min-w-0">
                        <p class="text-[0.5rem] font-black uppercase tracking-widest text-emerald-600 mb-1">Best trades</p>
                        <ul>${best.map(listRow).join('')}</ul>
                    </div>
                    <div class="min-w-0">
                        <p class="text-[0.5rem] font-black uppercase tracking-widest text-rose-500 mb-1">Worst trades</p>
                        <ul>${worst.length ? worst.map(listRow).join('') : '<li class="text-[0.55rem] font-bold text-slate-400 py-1">Fewer than 4 trades: see best trades.</li>'}</ul>
                    </div>
                </div>
            </div>`;
        return panel('Trade Breakdown', 'bg-gradient-to-b from-fuchsia-500 to-indigo-600', body);
    }

    // ── 5. Recent runs (compare) ────────────────────────────────────────────
    function storageKey() {
        let uid = 'anon';
        try { uid = window.auth?.currentUser?.uid || 'anon'; } catch (e) { /* signed-out view */ }
        return `${RECENT_KEY_PREFIX}:${uid}`;
    }
    function readRecent() {
        try {
            const raw = window.localStorage.getItem(storageKey());
            const list = raw ? JSON.parse(raw) : [];
            return Array.isArray(list) ? list.slice(0, RECENT_LIMIT) : [];
        } catch (e) { return []; }
    }
    function writeRecent(list) {
        try { window.localStorage.setItem(storageKey(), JSON.stringify(list.slice(0, RECENT_LIMIT))); } catch (e) { /* storage full or blocked */ }
    }
    function strategyLabel(s) {
        const p = s.strategy_preset;
        if (p && p.label) return `${p.label}${p.variant_label ? ` · ${p.variant_label}` : ''}`;
        const rs = s.run_settings || {};
        const nice = (v) => String(v || '').replace(/_/g, ' ');
        return rs.buy_mode ? `${nice(rs.buy_mode)} → ${nice(rs.sell_mode)}` : '—';
    }
    function runEntry(s) {
        const rs = s.run_settings || {};
        return {
            at: new Date().toISOString(),
            ticker: s.ticker,
            timeframe: s.timeframe,
            strategy: strategyLabel(s),
            sizing: rs.allocation_mode ? `${rs.allocation_mode === 'compound' ? 'Compound' : 'Fixed'} $${Math.round(num(rs.trade_amount) || 0).toLocaleString('en-US')}` : '',
            period: `${day(s.start_date_utc)} – ${day(s.end_date_utc)}`,
            roi: num(s.roi_pct), ann: num(s.annualised_roi_pct), ann_method: s.annualised_roi_method,
            max_dd: num(s.max_drawdown_pct), sharpe: num(s.sharpe_ratio), trades: num(s.total_trades),
            win: num(s.win_rate_pct), pf: num(s.profit_factor), exposure: num(s.exposure_pct),
            edge: s.edge_vs_buy_hold?.verdict || null, bh_roi: num(s.buy_hold_roi_pct),
            settings: rs,
        };
    }
    function signature(entry) {
        return JSON.stringify([entry.ticker, entry.timeframe, entry.strategy, entry.period, entry.roi, entry.trades, entry.max_dd, entry.settings]);
    }
    function recordRun(s) {
        const entry = runEntry(s);
        const list = readRecent();
        if (list.length && signature(list[0]) === signature(entry)) return list;
        list.unshift(entry);
        writeRecent(list);
        return list.slice(0, RECENT_LIMIT);
    }
    function comparePanel(list) {
        if (!list.length) return '';
        const edgeCell = (v) => v === 'beats' ? '<span class="text-emerald-600">Beats</span>' : (v === 'lags' ? '<span class="text-rose-600">Lags</span>' : (v === 'tie' ? 'Tie' : '—'));
        const rows = list.map((r, i) => `
            <tr class="border-t border-indigo-50/70 ${i === 0 ? 'bg-indigo-50/40' : ''}">
                <td class="py-1.5 px-2 min-w-[150px]">
                    <span class="block text-[0.6rem] font-black text-indigo-950">${esc(r.ticker)} · ${esc(r.timeframe)}${i === 0 ? ' <span class="text-[0.5rem] text-purple-500">(this run)</span>' : ''}</span>
                    <span class="block text-[0.5rem] font-bold text-indigo-500 break-words">${esc(r.strategy)}</span>
                    <span class="block text-[0.5rem] font-bold text-slate-400">${esc(r.sizing)}${r.sizing ? ' · ' : ''}${esc(r.period)}</span>
                </td>
                <td class="py-1.5 px-2 text-right font-mono font-black text-[0.6rem] ${tone(r.roi)}">${pct(r.roi)}</td>
                <td class="py-1.5 px-2 text-right font-mono font-bold text-[0.6rem] ${tone(r.ann)}">${pct(r.ann, 1)}</td>
                <td class="py-1.5 px-2 text-right font-mono font-bold text-[0.6rem] text-rose-600">${r.max_dd === null ? '—' : `-${r.max_dd.toFixed(2)}%`}</td>
                <td class="py-1.5 px-2 text-right font-mono font-bold text-[0.6rem] text-indigo-950">${ratio(r.sharpe)}</td>
                <td class="py-1.5 px-2 text-right font-mono font-bold text-[0.6rem] text-indigo-950">${r.trades ?? '—'}</td>
                <td class="py-1.5 px-2 text-right font-mono font-bold text-[0.6rem] text-indigo-950">${r.win === null ? '—' : `${r.win.toFixed(0)}%`}</td>
                <td class="py-1.5 px-2 text-right font-mono font-bold text-[0.6rem] text-indigo-950">${r.exposure === null ? '—' : `${r.exposure.toFixed(0)}%`}</td>
                <td class="py-1.5 px-2 text-right font-mono font-black text-[0.6rem]">${edgeCell(r.edge)}</td>
            </tr>`).join('');
        const clear = '<button type="button" data-bt-report="clear-recent" class="min-h-[32px] px-3 rounded-lg border border-indigo-100 bg-white text-[0.55rem] font-black uppercase tracking-widest text-indigo-500 hover:bg-indigo-50">Clear</button>';
        const body = `
            <div class="overflow-x-auto">
                <table class="w-full min-w-[620px] border-collapse">
                    <thead><tr class="text-[0.5rem] font-black uppercase tracking-wider text-indigo-400 bg-slate-50/60">
                        <th class="py-1.5 px-2 text-left">Run</th><th class="py-1.5 px-2 text-right">ROI</th><th class="py-1.5 px-2 text-right">Annual.</th>
                        <th class="py-1.5 px-2 text-right">Max DD</th><th class="py-1.5 px-2 text-right">Sharpe</th><th class="py-1.5 px-2 text-right">Trades</th>
                        <th class="py-1.5 px-2 text-right">Win</th><th class="py-1.5 px-2 text-right">In mkt</th><th class="py-1.5 px-2 text-right">vs B&amp;H</th>
                    </tr></thead>
                    <tbody>${rows}</tbody>
                </table>
            </div>
            <p class="px-4 py-2 text-[0.5rem] font-bold text-slate-400">Your last ${RECENT_LIMIT} runs, kept in this browser only.</p>`;
        return panel('Recent Runs', 'bg-gradient-to-b from-indigo-500 to-purple-600', body, clear);
    }

    // ── 6. Run record + CSV export ──────────────────────────────────────────
    const SETTING_LABELS = {
        ticker: 'Ticker', timeframe: 'Timeframe', start_date: 'Requested start', end_date: 'Requested end',
        initial_capital: 'Starting capital', allocation_mode: 'Sizing method', trade_amount: 'Amount per trade',
        market_session: 'Market session', ext_hours: 'Extended hours', strategy_preset: 'Strategy',
        strategy_preset_variant: 'Strategy variant', buy_mode: 'Entry mode', sell_mode: 'Exit mode',
        threshold: 'Buy threshold', sell_threshold: 'Sell threshold', enabled_indicators: 'Indicators',
        fill_model: 'Fill model', metrics_version: 'Metrics version',
    };
    function valueText(v) {
        if (v === null || v === undefined || v === '') return '—';
        if (Array.isArray(v)) return v.length ? v.join(', ') : '—';
        if (typeof v === 'object') return JSON.stringify(v);
        if (typeof v === 'boolean') return v ? 'On' : 'Off';
        return String(v);
    }
    function runRecordPanel(s) {
        const rs = s.run_settings;
        if (!rs || typeof rs !== 'object') return '';
        const keys = Object.keys(rs);
        const first = keys.filter((k) => SETTING_LABELS[k]);
        const rest = keys.filter((k) => !SETTING_LABELS[k]).sort();
        const item = (k) => `
            <div class="flex items-start justify-between gap-2 py-1 border-t border-indigo-50/60 min-w-0">
                <span class="text-[0.55rem] font-semibold text-indigo-500 shrink-0">${esc(SETTING_LABELS[k] || k)}</span>
                <span class="text-[0.55rem] font-mono font-bold text-indigo-950 text-right break-all min-w-0">${esc(valueText(rs[k]))}</span>
            </div>`;
        const provenance = [
            ['Data source', [s.data_source, s.data_feed, s.price_adjustment === 'split' ? 'split-adjusted' : null].filter(Boolean).join(' · ')],
            ['Bars tested', `${day(s.start_date_utc)} – ${day(s.end_date_utc)}`],
        ];
        const exportBtn = '<button type="button" data-bt-report="export-csv" class="min-h-[32px] px-3 rounded-lg border border-indigo-200 bg-indigo-600 text-white text-[0.55rem] font-black uppercase tracking-widest hover:bg-indigo-700">Export CSV</button>';
        const body = `
            <details class="group px-4 py-2">
                <summary class="cursor-pointer list-none flex items-center justify-between gap-2 py-1 text-[0.6rem] font-black text-indigo-700">
                    <span>Exact settings used for this result</span>
                    <span class="text-indigo-400 group-open:rotate-180 transition-transform">▾</span>
                </summary>
                <div class="grid grid-cols-1 md:grid-cols-2 md:gap-x-6 pb-2">
                    ${provenance.map(([k, v]) => `<div class="flex items-start justify-between gap-2 py-1 border-t border-indigo-50/60"><span class="text-[0.55rem] font-semibold text-indigo-500 shrink-0">${esc(k)}</span><span class="text-[0.55rem] font-mono font-bold text-indigo-950 text-right break-all">${esc(v || '—')}</span></div>`).join('')}
                    ${first.map(item).join('')}
                    ${rest.map(item).join('')}
                </div>
            </details>
            <p class="px-4 pb-3 text-[0.5rem] font-bold text-slate-400">Re-run with these values on the same data to reproduce the numbers. The CSV includes these settings, the summary, monthly returns and every trade.</p>`;
        return panel('Run Record', 'bg-gradient-to-b from-slate-400 to-indigo-500', body, exportBtn);
    }

    function csvCell(v) {
        const text = v === null || v === undefined ? '' : (typeof v === 'object' ? JSON.stringify(v) : String(v));
        return /[",\n\r]/.test(text) ? `"${text.replace(/"/g, '""')}"` : text;
    }
    function exportCsv() {
        const res = lastResult;
        const s = res?.summary;
        if (!s) return;
        const lines = [];
        const add = (...cells) => lines.push(cells.map(csvCell).join(','));
        add('BotBulls backtest export', new Date().toISOString());
        add();
        add('Run settings');
        Object.entries(s.run_settings || {}).forEach(([k, v]) => add(k, v));
        add();
        add('Summary');
        Object.entries(s).forEach(([k, v]) => {
            if (['run_settings', 'monthly_returns', 'period_split', 'strategy_preset'].includes(k)) return;
            add(k, v);
        });
        add();
        add('Monthly returns', 'year', 'month', 'return_pct');
        (s.monthly_returns || []).forEach((r) => add('', r.year, r.month, r.return_pct));
        add();
        add('Period split', 'start', 'end', 'return_pct', 'trades', 'win_rate_pct', 'pl');
        (s.period_split || []).forEach((h) => add(h.label, h.start_time, h.end_time, h.return_pct, h.trades, h.win_rate_pct, h.pl));
        add();
        const cols = ['entry_time', 'exit_time', 'side', 'qty', 'entry_price', 'exit_price', 'entry_cost', 'exit_value', 'fees', 'pl', 'pl_pct', 'exit_category', 'hold_hours', 'reason'];
        add('Trades');
        add(...cols);
        [...(res.trades || [])].reverse().forEach((t) => add(...cols.map((c) => t[c])));
        const blob = new Blob([lines.join('\r\n')], { type: 'text/csv;charset=utf-8' });
        const stamp = new Date().toISOString().slice(0, 16).replace(/[:T]/g, '-');
        const name = `botbulls-backtest-${String(s.ticker || 'run').replace(/[^A-Za-z0-9.-]/g, '')}-${s.timeframe || ''}-${stamp}.csv`;
        const url = URL.createObjectURL(blob);
        const a = document.createElement('a');
        a.href = url;
        a.download = name;
        document.body.appendChild(a);
        a.click();
        a.remove();
        setTimeout(() => URL.revokeObjectURL(url), 2000);
    }

    // ── Entry point ─────────────────────────────────────────────────────────
    function render(res) {
        lastResult = res || null;
        const host = document.getElementById('btReportExtras');
        const s = res?.summary;
        if (!s || !s.metrics_version) {
            // Options results and older responses do not carry the new analytics.
            renderEdge({});
            if (host) { host.innerHTML = ''; host.classList.add('hidden'); }
            return;
        }
        renderEdge(s);
        if (!host) return;
        const recent = recordRun(s);
        host.innerHTML = [consistencyPanel(s), monthlyPanel(s), tradesPanel(res), comparePanel(recent), runRecordPanel(s)].filter(Boolean).join('');
        host.classList.toggle('hidden', !host.innerHTML.trim());
    }

    document.addEventListener('click', (event) => {
        const action = event.target?.closest?.('[data-bt-report]')?.getAttribute('data-bt-report');
        if (action === 'export-csv') exportCsv();
        if (action === 'clear-recent') {
            writeRecent([]);
            const s = lastResult?.summary;
            if (s?.metrics_version) {
                writeRecent([runEntry(s)]);
                render(lastResult);
            }
        }
    });

    window.BtReport = { render, exitBadge, exitLabel, holdText, exportCsv };
})();
