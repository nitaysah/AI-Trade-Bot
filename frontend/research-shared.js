/* Shared research views for Bots and Screener: the Fundamentals report, the Earnings watch, and their badges.
 *
 * One implementation, so a ticker reads the same on both pages. Each page only says where its data comes from:
 *
 *   BotBullsResearch.configure({ earningsUrl: (t) => `/api/bots/earnings/${t}`, fundamentalsUrl: (t) => `/api/bots/fundamentals/${t}`,
 *                                cachePrefix: 'bot' });
 *
 * The dialogs are built here (no copy in the page HTML). All text from the API is escaped. Everything is informational:
 * nothing here creates an order or an alert, and the wording is evidence, not advice. Phone first: single column below
 * 640px, sections scroll inside the dialog, no horizontal page scroll. */
(function () {
    'use strict';

    const FUNDAMENTAL_REPORT_VERSION = 2;
    const EARNINGS_DATA_VERSION = 4;
    const FUNDAMENTAL_CACHE_MS = 24 * 60 * 60 * 1000;
    const config = { earningsUrl: null, fundamentalsUrl: null, cachePrefix: 'research' };

    // ── formatting ───────────────────────────────────────────────────────────────────────────────────────────────
    function esc(value) {
        return String(value ?? '').replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
    }

    function safeUrl(value) {
        try {
            const url = new URL(String(value || ''), window.location.href);
            return ['http:', 'https:'].includes(url.protocol) ? url.href : '';
        } catch (_) { return ''; }
    }

    function formatDate(value) {
        if (!value) return '';
        const date = new Date(`${String(value).slice(0, 10)}T12:00:00`);
        return Number.isNaN(date.getTime()) ? String(value) : date.toLocaleDateString('en-US', { month: 'short', day: 'numeric', year: 'numeric' });
    }

    function shortDate(value) {
        if (!value) return '';
        const date = new Date(`${String(value).slice(0, 10)}T12:00:00`);
        return Number.isNaN(date.getTime()) ? String(value) : date.toLocaleDateString('en-US', { month: 'short', day: 'numeric' });
    }

    // Earnings dates are exchange (New York) calendar dates. Count days against today's New York date, so the countdown
    // always matches the date shown even when the cached server value is hours or days old.
    function exchangeToday() {
        try {
            return new Intl.DateTimeFormat('en-CA', { timeZone: 'America/New_York', year: 'numeric', month: '2-digit', day: '2-digit' }).format(new Date());
        } catch (error) {
            return new Date().toISOString().slice(0, 10);
        }
    }

    function daysUntil(value, fallback) {
        const target = String(value || '').slice(0, 10);
        const match = /^(\d{4})-(\d{2})-(\d{2})$/.exec(target);
        const today = /^(\d{4})-(\d{2})-(\d{2})$/.exec(exchangeToday());
        if (!match || !today) return fallback;
        const utc = (m) => Date.UTC(Number(m[1]), Number(m[2]) - 1, Number(m[3]));
        return Math.round((utc(match) - utc(today)) / 86400000);
    }

    function countdown(days) {
        const n = Number(days);
        if (!Number.isFinite(n)) return 'Date pending';
        if (n < 0) return `${Math.abs(n)}d ago`;
        if (n === 0) return 'Today';
        if (n === 1) return 'Tomorrow';
        return `in ${n}d`;
    }

    function money(value, prefix = '$') {
        const n = Number(value);
        if (!Number.isFinite(n)) return '—';
        const abs = Math.abs(n);
        const [scaled, suffix] = abs >= 1e12 ? [n / 1e12, 'T'] : abs >= 1e9 ? [n / 1e9, 'B'] : abs >= 1e6 ? [n / 1e6, 'M'] : abs >= 1e4 ? [n / 1e3, 'K'] : [n, ''];
        return `${scaled < 0 ? '-' : ''}${prefix}${Math.abs(scaled).toLocaleString('en-US', { maximumFractionDigits: suffix ? 2 : 2 })}${suffix}`;
    }

    function percent(value, digits = 1) {
        const n = Number(value);
        if (!Number.isFinite(n)) return '—';
        return `${n.toFixed(digits)}%`;
    }

    function signedPercent(value, digits = 1) {
        const n = Number(value);
        return Number.isFinite(n) ? `${n > 0 ? '+' : ''}${n.toFixed(digits)}%` : '—';
    }

    function dateTime(iso) {
        if (!iso) return '';
        try {
            return new Intl.DateTimeFormat('en-US', { timeZone: 'America/Chicago', month: 'short', day: 'numeric', hour: 'numeric', minute: '2-digit', timeZoneName: 'short' }).format(new Date(iso));
        } catch (_) { return String(iso); }
    }

    const PERCENT_TOKENS = ['margin', 'growth', 'yield', 'payout', 'ownership', 'float', 'return on', 'upside', 'vs 52', 'insider', 'institution'];
    const MONEY_TOKENS = ['market cap', 'enterprise value', 'cash', 'total debt', 'net cash', 'operating cash flow', 'free cash flow'];

    /** One metric value as text. Strings the API already formatted ("6.4%", "1.00x", "+0.5 pts") are shown as they are. */
    function metricValue(value, label = '') {
        if (value === null || value === undefined || value === '') return 'Not available';
        if (typeof value === 'string' && !/^-?[\d.]+$/.test(value.trim())) return value;
        const n = Number(value);
        if (!Number.isFinite(n)) return String(value);
        const lower = String(label).toLowerCase();
        if (MONEY_TOKENS.some((t) => lower.includes(t)) && !lower.includes('margin') && !lower.includes('yield') && !lower.includes('conversion')) return money(n);
        if (PERCENT_TOKENS.some((t) => lower.includes(t))) return percent(n);
        return n.toLocaleString('en-US', { maximumFractionDigits: 3 });
    }

    function list(items, empty) {
        const rows = Array.isArray(items) ? items.filter(Boolean) : [];
        return rows.length ? rows.map((item) => `<li>${esc(item)}</li>`).join('') : `<li class="list-none text-slate-400">${esc(empty)}</li>`;
    }

    // ── dialogs ──────────────────────────────────────────────────────────────────────────────────────────────────
    const MODAL_SHELL = (id, titleId, closeLabel, wide) => `
        <div id="${id}" class="fixed inset-0 z-[220] hidden items-center justify-center bg-slate-900/50 backdrop-blur-sm p-2 sm:p-3" role="dialog" aria-modal="true" aria-labelledby="${titleId}" data-research-modal>
            <div class="bg-white rounded-2xl shadow-2xl border border-slate-200 w-full ${wide ? 'max-w-4xl' : 'max-w-2xl'} max-h-[94vh] overflow-hidden flex flex-col">
                <div class="px-4 py-3 border-b border-slate-100 flex items-center justify-between gap-2 shrink-0">
                    <div class="min-w-0"><h3 id="${titleId}" class="text-base font-black text-slate-900 truncate"></h3><span data-sub class="text-[0.58rem] font-black uppercase tracking-wider text-slate-500"></span></div>
                    <button type="button" data-close class="shrink-0 min-h-[40px] min-w-[40px] rounded-lg text-slate-400 hover:text-slate-700 hover:bg-slate-100" aria-label="${closeLabel}">✕</button>
                </div>
                <div data-loading class="hidden px-4 py-2 text-[0.65rem] font-bold text-sky-700 bg-sky-50 border-b border-sky-100"></div>
                <div data-body class="p-3 sm:p-4 overflow-y-auto space-y-3 text-[0.7rem] sm:text-[0.72rem] text-slate-600 leading-snug"></div>
            </div>
        </div>`;

    function ensureModals() {
        if (document.getElementById('researchEarningsModal')) return;
        const holder = document.createElement('div');
        holder.innerHTML = MODAL_SHELL('researchEarningsModal', 'researchEarningsTitle', 'Close earnings details', false)
            + MODAL_SHELL('researchFundamentalsModal', 'researchFundamentalsTitle', 'Close fundamental research', true);
        while (holder.firstElementChild) document.body.appendChild(holder.firstElementChild);
        document.querySelectorAll('[data-research-modal]').forEach((modal) => {
            modal.addEventListener('click', (event) => { if (event.target === modal) closeModal(modal); });
            modal.querySelector('[data-close]').addEventListener('click', () => closeModal(modal));
        });
        document.addEventListener('keydown', (event) => {
            if (event.key === 'Escape') document.querySelectorAll('[data-research-modal]').forEach(closeModal);
        });
    }

    function openModal(modal) { modal.classList.remove('hidden'); modal.classList.add('flex'); }
    function closeModal(modal) { modal.classList.add('hidden'); modal.classList.remove('flex'); }

    const card = (title, inner, extra = '') => `<section class="rounded-xl border border-slate-200 p-3 ${extra}"><div class="font-black text-slate-800 mb-1.5">${title}</div>${inner}</section>`;
    const tile = (label, value, tone = 'violet') => `<div class="rounded-xl border border-${tone}-100 bg-${tone}-50/60 p-2 min-w-0"><div class="text-[0.52rem] font-black uppercase text-${tone}-600">${esc(label)}</div><div class="font-black text-slate-900 mt-0.5 break-words">${esc(value)}</div></div>`;
    const pill = (text, classes) => `<span class="rounded-full border px-2 py-0.5 text-[0.6rem] font-black ${classes}">${esc(text)}</span>`;
    const DISCLAIMER = '<div class="text-[0.55rem] text-slate-400">Informational evidence from public data, which can be delayed or wrong. Not investment advice, and it never places an order.</div>';

    // ── Earnings ─────────────────────────────────────────────────────────────────────────────────────────────────
    const OUTLOOK_TONE = { BULLISH: 'text-emerald-700 bg-emerald-50 border-emerald-200', BEARISH: 'text-rose-700 bg-rose-50 border-rose-200' };
    const RISK_TONE = { CRITICAL: 'text-rose-700 bg-rose-50 border-rose-200', ELEVATED: 'text-rose-700 bg-rose-50 border-rose-200', MODERATE: 'text-amber-700 bg-amber-50 border-amber-200' };

    function renderEarnings(data = {}) {
        ensureModals();
        const modal = document.getElementById('researchEarningsModal');
        const status = String(data.status || 'waiting').toUpperCase();
        const symbol = data.ticker || 'Ticker';
        const outlook = String(data.outlook || 'INSUFFICIENT').toUpperCase();
        const risk = String(data.risk_level || 'UNKNOWN').toUpperCase();
        const eps = data.eps_estimate || {};
        const rev = data.revenue_estimate || {};
        const record = data.beat_record || {};
        const moves = Array.isArray(data.reactions) ? data.reactions : [];
        const revisions = data.eps_revisions || {};
        const count = (v) => (Number.isFinite(Number(v)) ? Number(v).toLocaleString('en-US', { maximumFractionDigits: 0 }) : '0');
        modal.querySelector('#researchEarningsTitle').textContent = `${symbol} Earnings Watch`;
        modal.querySelector('[data-sub]').textContent = status === 'CURRENT' ? 'Scheduled' : status;
        const loading = modal.querySelector('[data-loading]');
        loading.textContent = 'Refreshing structured earnings data…';
        loading.classList.toggle('hidden', status !== 'WAITING');

        const recordHtml = record.quarters >= 2
            ? `<div class="flex flex-wrap items-baseline gap-x-3 gap-y-1"><span class="text-lg font-black text-slate-900">${esc(record.beats)} of ${esc(record.quarters)}</span><span>quarters beat the EPS estimate${record.misses ? `, ${esc(record.misses)} missed` : ''}${record.in_line ? `, ${esc(record.in_line)} in line` : ''}.</span></div>`
              + (record.streak >= 2 ? `<div class="mt-0.5 text-slate-500">Current streak: ${esc(record.streak)} ${esc(record.streak_kind)}${record.streak > 1 && record.streak_kind !== 'in line' ? 's' : ''} in a row.</div>` : '')
            : '<div class="text-slate-400">Not enough reported quarters to show a beat record.</div>';

        const typical = Number(data.typical_move_pct);
        const movesHtml = moves.length
            ? `<div class="flex flex-wrap items-baseline gap-x-2"><span class="text-lg font-black text-slate-900">±${Number.isFinite(typical) ? esc(typical.toFixed(1)) : '—'}%</span><span>typical move the session after a report (average of the last ${moves.length}).</span></div>`
              + `<div class="mt-1.5 flex flex-wrap gap-1">${moves.map((m) => `<span class="rounded border px-1.5 py-0.5 font-black ${Number(m.move_pct) >= 0 ? 'border-emerald-200 bg-emerald-50 text-emerald-700' : 'border-rose-200 bg-rose-50 text-rose-700'}" title="${esc(formatDate(m.date))}${m.approx ? ' (approximate: report time unknown)' : ''}">${esc(shortDate(m.date))} ${esc(signedPercent(m.move_pct))}${m.approx ? '~' : ''}</span>`).join('')}</div>`
              + '<div class="mt-1 text-slate-500">If you hold through a report, a stop tighter than this typical move can be gapped through.</div>'
            : '<div class="text-slate-400">Past price reactions are not available for this ticker yet.</div>';

        const history = Array.isArray(data.history) && data.history.length
            ? data.history.map((row) => `<tr class="border-t border-slate-100"><td class="py-1 pr-2">${esc(shortDate(row.date))}</td><td class="py-1 pr-2">${esc(money(row.eps_estimate))}</td><td class="py-1 pr-2 font-black text-slate-800">${esc(money(row.eps_actual))}</td><td class="py-1 font-black ${Number(row.surprise_pct) >= 0 ? 'text-emerald-600' : 'text-rose-600'}">${esc(signedPercent(row.surprise_pct))}</td></tr>`).join('')
            : '<tr><td colspan="4" class="py-2 text-slate-400">No reported results available.</td></tr>';

        modal.querySelector('[data-body]').innerHTML = `
            <div class="grid grid-cols-2 sm:grid-cols-4 gap-2">
                ${tile('Event date', data.event_date ? formatDate(data.event_date) : data.last_event_date ? `Next date not announced (last reported ${formatDate(data.last_event_date)})` : 'Date not published')}
                ${tile('Countdown', data.event_date ? countdown(daysUntil(data.event_date, data.days_to_event)) : 'Date pending')}
                ${tile('Release timing', data.timing && data.timing !== 'UNKNOWN' ? data.timing : 'Timing not published')}
                ${tile('Date status', data.event_status === 'estimated' ? 'Estimated date' : data.event_status === 'not_published' ? 'Not published' : status)}
            </div>
            <div class="flex flex-wrap items-center gap-1.5">${pill(outlook, OUTLOOK_TONE[outlook] || 'text-slate-700 bg-slate-50 border-slate-200')}${pill(`${risk} RISK`, RISK_TONE[risk] || 'text-slate-700 bg-slate-50 border-slate-200')}<span class="text-[0.58rem] font-bold text-slate-400">Pre-earnings view from estimates, revisions and history, not a forecast of the result.</span></div>
            ${card('What the numbers say', `<div>${esc(data.analysis_summary || 'Structured earnings evidence is not available yet.')}</div>`, 'bg-slate-50')}
            <div class="grid grid-cols-1 sm:grid-cols-2 gap-2">
                ${card('Beat record', recordHtml)}
                ${card('How the stock reacted before', movesHtml)}
            </div>
            <div class="grid grid-cols-1 sm:grid-cols-3 gap-2">
                <div class="rounded-xl border border-emerald-100 bg-emerald-50/50 p-2"><div class="font-black text-emerald-700 mb-1">Bullish evidence</div><ul class="list-disc pl-4 space-y-0.5">${list(data.bullish_factors, 'No bullish evidence recorded.')}</ul></div>
                <div class="rounded-xl border border-rose-100 bg-rose-50/50 p-2"><div class="font-black text-rose-700 mb-1">Bearish evidence</div><ul class="list-disc pl-4 space-y-0.5">${list(data.bearish_factors, 'No bearish evidence recorded.')}</ul></div>
                <div class="rounded-xl border border-amber-100 bg-amber-50/50 p-2"><div class="font-black text-amber-700 mb-1">Uncertainties</div><ul class="list-disc pl-4 space-y-0.5">${list(data.uncertainties, 'No additional uncertainty recorded.')}</ul></div>
            </div>
            ${card('What to watch in the report', `<ul class="list-disc pl-4 space-y-0.5">${list(data.what_to_watch, 'No watch items recorded.')}</ul>`, 'bg-sky-50/50 border-sky-100')}
            ${card('Consensus estimates', `<div class="grid grid-cols-2 gap-x-4 gap-y-1"><span>EPS low / avg / high</span><strong class="text-right sm:text-left">${esc(money(eps.low))} / ${esc(money(eps.average))} / ${esc(money(eps.high))}</strong><span>Revenue low / avg / high</span><strong class="text-right sm:text-left">${esc(money(rev.low))} / ${esc(money(rev.average))} / ${esc(money(rev.high))}</strong><span>Analysts covering</span><strong class="text-right sm:text-left">${esc(eps.analysts ?? '—')}</strong></div>`)}
            ${card('Recent reported results', `<div class="overflow-x-auto"><table class="w-full text-left"><thead><tr class="text-[0.55rem] uppercase text-slate-400"><th class="pr-2">Quarter</th><th class="pr-2">Estimate</th><th class="pr-2">Actual</th><th>Surprise</th></tr></thead><tbody>${history}</tbody></table></div>`)}
            ${card('Analyst EPS revisions', `<div>7 days: ${esc(count(revisions.up_7d))} up / ${esc(count(revisions.down_7d))} down · 30 days: ${esc(count(revisions.up_30d))} up / ${esc(count(revisions.down_30d))} down</div><div class="mt-0.5 text-slate-500">More upward revisions before a report usually mean expectations are rising, which raises the bar for a good reaction.</div>`)}
            ${DISCLAIMER}
            <div class="text-[0.55rem] text-slate-400">${esc(data.source || 'Yahoo Finance')} · Checked ${esc(dateTime(data.checked_at) || 'pending')}${data.error ? ` · ${esc(data.error)}` : ''}</div>`;
        openModal(modal);
    }

    // ── Fundamentals ─────────────────────────────────────────────────────────────────────────────────────────────
    const RATING = {
        strong: ['Strong', 'border-emerald-200 bg-emerald-50 text-emerald-700'],
        ok: ['OK', 'border-sky-200 bg-sky-50 text-sky-700'],
        weak: ['Weak', 'border-rose-200 bg-rose-50 text-rose-700'],
        unknown: ['No data', 'border-slate-200 bg-slate-50 text-slate-500'],
    };
    const ASSESSMENT_TONE = {
        'Worth considering': 'border-emerald-300 bg-emerald-50 text-emerald-800',
        'Mixed evidence': 'border-indigo-300 bg-indigo-50 text-indigo-800',
        'Wait for improvement': 'border-amber-300 bg-amber-50 text-amber-800',
        'Wait for a better valuation': 'border-amber-300 bg-amber-50 text-amber-800',
        'Insufficient evidence': 'border-slate-300 bg-slate-50 text-slate-600',
    };

    function metricCard(metric = {}) {
        const trend = String(metric.trend || '');
        const trendClass = /pos|up|improv/i.test(trend) ? 'text-emerald-600' : /neg|down|weak/i.test(trend) ? 'text-rose-600' : 'text-slate-400';
        return `<div class="rounded-xl border border-slate-200 bg-white p-2"><div class="flex items-start justify-between gap-2"><span class="font-black text-slate-800">${esc(metric.label || 'Metric')}</span><span class="font-black text-slate-900 text-right shrink-0">${esc(metricValue(metric.value, metric.label))}</span></div>`
            + `${trend ? `<div class="text-[0.55rem] font-black uppercase ${trendClass}">${esc(trend)}</div>` : ''}`
            + `${metric.meaning ? `<div class="mt-0.5 text-slate-500">${esc(metric.meaning)}</div>` : ''}`
            + `${metric.interpretation ? `<div class="mt-0.5 text-slate-600">${esc(metric.interpretation)}</div>` : ''}</div>`;
    }

    function metricSection(title, metrics, wide) {
        const rows = Array.isArray(metrics) ? metrics : [];
        return card(esc(title), `<div class="grid grid-cols-1 ${wide ? 'sm:grid-cols-2' : ''} gap-2">${rows.length ? rows.map(metricCard).join('') : '<div class="text-slate-400">Not available for this company.</div>'}</div>`);
    }

    function trendRow(series, label) {
        const rows = Array.isArray(series) ? series : [];
        if (!rows.length) return `<div class="text-slate-400">${esc(label)}: Not available</div>`;
        return `<div><span class="font-black text-slate-700">${esc(label)}</span><div class="flex flex-wrap gap-1 mt-1">${rows.slice().reverse().map((row) => `<span class="rounded border border-slate-200 bg-slate-50 px-1.5 py-0.5"><span class="text-slate-400">${esc(String(row.period || '').slice(0, 4))}</span> <strong>${esc(label.toLowerCase().includes('eps') ? money(row.value) : money(row.value))}</strong></span>`).join('')}</div></div>`;
    }

    function renderFundamentals(report = {}) {
        ensureModals();
        const modal = document.getElementById('researchFundamentalsModal');
        const company = report.company || {};
        const status = String(report.status || 'waiting').toLowerCase();
        const assessment = report.assessment || 'Insufficient evidence';
        const score = Number(report.score);
        const metrics = report.metrics || {};
        const context = report.price_context || {};
        const f = report.piotroski || {};
        const trends = report.financial_trends || {};
        const estimates = report.estimates || {};
        const ownership = report.ownership || {};
        modal.querySelector('#researchFundamentalsTitle').textContent = `${report.ticker || 'Ticker'} Fundamental Research`;
        modal.querySelector('[data-sub]').textContent = 'Explained public financial evidence';
        const loading = modal.querySelector('[data-loading]');
        loading.textContent = report.summary && status === 'waiting' ? report.summary : 'Refreshing the fundamental report…';
        loading.classList.toggle('hidden', status !== 'waiting');

        if (status === 'not_applicable' || status === 'disabled') {
            modal.querySelector('[data-body]').innerHTML = card('Not applicable', `<div>${esc(report.summary || 'Company financial statements do not apply to this instrument.')}</div>`) + DISCLAIMER;
            openModal(modal);
            return;
        }

        const scoreBar = Number.isFinite(score)
            ? `<div class="mt-2"><div class="flex items-center justify-between text-[0.58rem] font-black uppercase text-slate-500"><span>Evidence score</span><span>${esc(score)} / 100</span></div><div class="mt-1 h-2 rounded-full bg-slate-200 overflow-hidden"><div class="h-full rounded-full ${score >= 72 ? 'bg-emerald-500' : score >= 50 ? 'bg-indigo-500' : 'bg-amber-500'}" style="width:${Math.max(3, Math.min(100, score))}%"></div></div><div class="mt-0.5 text-[0.55rem] text-slate-400">A summary of the five areas below, not a prediction of the price.</div></div>` : '';

        const scorecard = (report.scorecard || []).map((c) => {
            const [label, classes] = RATING[c.rating] || RATING.unknown;
            return `<div class="flex items-start gap-2 py-1.5 border-t border-slate-100 first:border-t-0"><span class="w-24 shrink-0 font-black text-slate-800">${esc(c.label)}</span><span class="shrink-0 rounded-full border px-2 py-0.5 text-[0.58rem] font-black ${classes}">${label}</span><span class="min-w-0 text-slate-600">${esc((c.reasons || []).join(' · '))}</span></div>`;
        }).join('');

        const tests = (f.tests || []).map((t) => `<li class="list-none flex gap-1.5"><span class="${t.result === true ? 'text-emerald-600' : t.result === false ? 'text-rose-600' : 'text-slate-300'} font-black w-3">${t.result === true ? '✓' : t.result === false ? '✗' : '–'}</span><span><span class="font-black text-slate-700">${esc(t.test)}</span> <span class="text-slate-500">${esc(t.detail)}</span></span></li>`).join('');
        const piotroski = f.available
            ? card('Financial health tests (Piotroski F-score)', `<div class="flex items-baseline gap-2"><span class="text-lg font-black text-slate-900">${esc(f.score)} of ${esc(f.available)}</span><span>${f.score >= 7 ? 'passed: health is improving on most tests' : f.score <= 3 ? 'passed: most tests are failing' : 'passed: a mixed picture'}${f.reliable ? '' : ' (some tests lacked data)'}.</span></div><details class="mt-1"><summary class="cursor-pointer font-black text-indigo-600">Show the nine tests</summary><ul class="mt-1 space-y-0.5">${tests}</ul></details>`)
            : '';

        const range = Number(context.range_position_pct);
        const priceContext = Number.isFinite(range)
            ? card('Where the price sits', `<div class="flex justify-between text-[0.58rem] font-black text-slate-400"><span>52-week low ${esc(money(context.low_52w))}</span><span>high ${esc(money(context.high_52w))}</span></div><div class="relative mt-1 h-2 rounded-full bg-slate-200"><div class="absolute top-1/2 h-3.5 w-3.5 -translate-y-1/2 -translate-x-1/2 rounded-full border-2 border-white bg-indigo-600 shadow" style="left:${Math.max(2, Math.min(98, range))}%"></div></div><div class="mt-1.5">${esc(money(context.price))} is ${esc(percent(Math.abs(Number(context.below_high_pct))))} ${Number(context.below_high_pct) <= 0 ? 'below' : 'above'} the 52-week high${Number.isFinite(Number(context.target_upside_pct)) ? `; the average analyst target implies ${esc(signedPercent(context.target_upside_pct))}.` : '.'}</div>`)
            : '';

        modal.querySelector('[data-body]').innerHTML = `
            <section class="rounded-xl border border-indigo-100 bg-indigo-50/60 p-3"><div class="text-base font-black text-slate-900">${esc(company.name || report.ticker || 'Company')}</div><div class="text-[0.6rem] text-slate-500 mt-0.5">${esc([company.sector, company.industry, company.country, company.employees ? `${Number(company.employees).toLocaleString('en-US')} employees` : ''].filter(Boolean).join(' · ') || 'Company information pending.')}</div><div class="mt-2 text-slate-700">${esc(company.description || 'Business description not available.')}</div></section>
            <section class="rounded-xl border border-indigo-200 bg-indigo-50 p-3"><div class="flex flex-wrap items-center gap-2"><span class="text-[0.55rem] font-black uppercase tracking-wider text-indigo-700">Evidence assessment</span>${pill(assessment, ASSESSMENT_TONE[assessment] || ASSESSMENT_TONE['Insufficient evidence'])}</div><div class="mt-1.5 font-semibold text-slate-700">${esc(report.summary || 'Structured fundamental evidence is not available yet.')}</div>${scoreBar}</section>
            ${card('What this means', `<ul class="list-disc pl-4 space-y-1">${list(report.takeaways, 'Not enough structured data to form a view.')}</ul>`, 'bg-sky-50/50 border-sky-100')}
            ${card('Scorecard', `<div>${scorecard || '<div class="text-slate-400">Scorecard not available.</div>'}</div><div class="mt-1 text-[0.55rem] text-slate-400">Strong / OK / Weak follow stated thresholds (growth, margins, cash conversion, leverage, valuation vs growth); the reasons are shown beside each rating.</div>`)}
            <div class="grid grid-cols-1 md:grid-cols-3 gap-2">
                <div class="rounded-xl border border-emerald-100 bg-emerald-50/50 p-2"><div class="font-black text-emerald-700 mb-1">Strengths</div><ul class="list-disc pl-4 space-y-0.5">${list(report.strengths, 'No supported strengths recorded.')}</ul></div>
                <div class="rounded-xl border border-rose-100 bg-rose-50/50 p-2"><div class="font-black text-rose-700 mb-1">Concerns</div><ul class="list-disc pl-4 space-y-0.5">${list(report.concerns, 'No supported concerns recorded.')}</ul></div>
                <div class="rounded-xl border border-amber-100 bg-amber-50/50 p-2"><div class="font-black text-amber-700 mb-1">What to monitor</div><ul class="list-disc pl-4 space-y-0.5">${list(report.monitor, 'No monitoring items recorded.')}</ul></div>
            </div>
            ${piotroski}
            ${priceContext}
            <div class="grid grid-cols-1 lg:grid-cols-2 gap-2">
                ${metricSection('Valuation', metrics.valuation)}
                ${metricSection('Growth', metrics.growth)}
                ${metricSection('Profitability', metrics.profitability)}
                ${metricSection('Cash generation', metrics.cash_generation)}
                ${metricSection('Financial strength', metrics.financial_strength)}
                ${metricSection('Shareholder returns', metrics.shareholder_returns)}
            </div>
            ${card('Multi-year trend', `<div class="grid grid-cols-1 sm:grid-cols-2 gap-2">${[trendRow(trends.revenue, 'Revenue'), trendRow(trends.net_income, 'Net income'), trendRow(trends.free_cash_flow, 'Free cash flow'), trendRow(trends.eps, 'EPS'), trendRow(trends.cash, 'Cash'), trendRow(trends.debt, 'Debt')].join('')}</div>`)}
            <div class="grid grid-cols-1 md:grid-cols-2 gap-2">
                ${card('Analyst view', `<div>Analysts covering: ${esc(estimates.analyst_count ?? 'Not available')} · Consensus: ${esc(estimates.recommendation || 'Not available')}${estimates.target_mean ? ` · Target ${esc(money(estimates.target_low))} / ${esc(money(estimates.target_mean))} / ${esc(money(estimates.target_high))}` : ''}</div>`)}
                ${card('Ownership', `<div>Shares outstanding: ${esc(metricValue(ownership.shares_outstanding, 'shares'))} · Insiders: ${esc(ownership.insider_pct != null ? percent(ownership.insider_pct) : 'Not available')} · Institutions: ${esc(ownership.institutional_pct != null ? percent(ownership.institutional_pct) : 'Not available')}${ownership.short_pct_float != null ? ` · Short interest: ${esc(percent(ownership.short_pct_float))} of float` : ''}</div>`)}
            </div>
            ${card('How to use this', `<ul class="list-disc pl-4 space-y-0.5">${list(report.fundamental_conditions, 'No separate timing conditions recorded.')}</ul>`)}
            ${card('SEC filings and source documents', `<ul class="list-disc pl-4 space-y-0.5">${Array.isArray(report.filings) && report.filings.length ? report.filings.map((row) => { const url = safeUrl(row.url); const text = esc([row.date, row.type, row.title].filter(Boolean).join(' · ') || 'Filing'); return `<li>${url ? `<a class="text-indigo-600 underline" href="${esc(url)}" target="_blank" rel="noopener noreferrer">${text}</a>` : text}</li>`; }).join('') : '<li class="list-none text-slate-400">No filings returned by the data provider.</li>'}</ul>`)}
            ${DISCLAIMER}
            <div class="text-[0.55rem] text-slate-400">${esc(report.source || 'Structured public data')} · Financial period ${esc(report.financial_period || 'not available')} · Checked ${esc(dateTime(report.checked_at) || 'pending')}${report.error ? ` · ${esc(report.error)}` : ''}</div>`;
        openModal(modal);
    }

    // ── data loading ─────────────────────────────────────────────────────────────────────────────────────────────
    async function authedFetch(url) {
        const headers = typeof getAuthHeaders === 'function' ? await getAuthHeaders() : {};
        const base = typeof API_BASE !== 'undefined' ? API_BASE : '';
        return fetch(`${base}${url}`, { headers });
    }

    async function openEarnings(ticker, cached = null) {
        const symbol = String(ticker || '').toUpperCase();
        if (!symbol || !config.earningsUrl) return;
        renderEarnings({ ...(cached || {}), ticker: symbol, status: (cached && cached.status) || 'waiting' });
        try {
            const response = await authedFetch(config.earningsUrl(encodeURIComponent(symbol)));
            const data = await response.json();
            if (response.ok) renderEarnings(data);
            else renderEarnings({ ...(cached || {}), ticker: symbol, status: 'unavailable', error: data.detail || 'Unable to load earnings details.' });
        } catch (error) {
            renderEarnings({ ...(cached || {}), ticker: symbol, status: 'unavailable', error: error.message || 'Unable to load earnings details.' });
        }
    }

    async function openFundamentals(ticker) {
        const symbol = String(ticker || '').toUpperCase();
        if (!symbol || !config.fundamentalsUrl) return;
        const cacheKey = `${config.cachePrefix}FundamentalReport:${symbol}`;
        let cached = null;
        try { cached = JSON.parse(localStorage.getItem(cacheKey) || 'null'); } catch (_) { cached = null; }
        if (cached && cached.report_version !== FUNDAMENTAL_REPORT_VERSION) cached = null;  // an old report lacks the scorecard
        const fresh = cached && cached.status === 'current' && cached.checked_at && (Date.now() - new Date(cached.checked_at).getTime()) < FUNDAMENTAL_CACHE_MS;
        renderFundamentals(fresh ? cached : { ...(cached || {}), ticker: symbol, status: 'waiting', summary: cached ? 'Refreshing the 24-hour fundamental report in the background…' : 'Loading the fundamental report…' });
        if (fresh) return;
        try {
            const response = await authedFetch(config.fundamentalsUrl(encodeURIComponent(symbol)));
            const data = await response.json();
            if (response.ok) { try { localStorage.setItem(cacheKey, JSON.stringify(data)); } catch (_) {} renderFundamentals(data); }
            else renderFundamentals({ ...(cached || {}), ticker: symbol, status: 'stale', error: data.detail || 'Unable to load fundamental research.' });
        } catch (error) {
            renderFundamentals({ ...(cached || {}), ticker: symbol, status: 'stale', error: error.message || 'Unable to load fundamental research.' });
        }
    }

    // ── badges (the same on Bots and Screener) ───────────────────────────────────────────────────────────────────
    // Compact on phones so the chart button, Fundamentals and Earnings badges share one line; the desktop size is unchanged.
    const BUTTON = 'research-chip px-1 sm:px-1.5 py-0.5 rounded-md border font-black text-[0.5rem] sm:text-[0.52rem] uppercase tracking-tight sm:tracking-wide hover:shadow-sm transition-shadow whitespace-nowrap shrink-0 text-left';
    const SHORT_ASSESSMENT = {
        'Worth considering': ['Strong', 'border-emerald-200 bg-emerald-50 text-emerald-700'],
        'Mixed evidence': ['Mixed', 'border-indigo-200 bg-indigo-50 text-indigo-700'],
        'Wait for improvement': ['Weak', 'border-amber-200 bg-amber-50 text-amber-700'],
        'Wait for a better valuation': ['Pricey', 'border-amber-200 bg-amber-50 text-amber-700'],
        'Insufficient evidence': ['Limited data', 'border-slate-200 bg-slate-50 text-slate-600'],
    };
    const TIMING_SHORT = { 'Before market open': 'BMO', 'After market close': 'AMC', 'During market hours': 'intraday' };

    /** data: { status, assessment, score } for the ticker, or null while it loads. */
    function fundamentalsBadge(ticker, data) {
        const status = String(data?.status || 'waiting').toLowerCase();
        const head = '📊 Fundamentals';
        let label = ' · Checking…';
        let tone = 'border-slate-200 bg-slate-50 text-slate-600';
        if (status === 'not_applicable') return '';
        if ((status === 'current' || status === 'stale') && data?.assessment) {
            const [word, classes] = SHORT_ASSESSMENT[data.assessment] || [data.assessment, 'border-indigo-200 bg-indigo-50 text-indigo-700'];
            label = ` · ${word}${Number.isFinite(Number(data.score)) ? ` · ${Math.round(Number(data.score))}` : ''}`;
            tone = status === 'stale' ? 'border-amber-200 bg-amber-50 text-amber-700' : classes;
        } else if (status === 'unavailable') {
            label = ' · Unavailable';
        } else if (status === 'current' || status === 'stale') {
            label = '';
            tone = 'border-indigo-200 bg-indigo-50 text-indigo-700';
        }
        const title = data?.assessment ? `${data.assessment}${Number.isFinite(Number(data.score)) ? ` (evidence score ${Math.round(Number(data.score))}/100)` : ''}. Open the explained fundamental research report.` : 'Open explained fundamental research report';
        return `<button type="button" class="${BUTTON} ${tone}" title="${esc(title)}" onclick="event.stopPropagation(); BotBullsResearch.openFundamentals('${esc(ticker)}')">${head}${esc(label)}</button>`;
    }

    /** data: the earnings entry (status, event_date, days_to_event, risk_level, timing, initialized) or null. */
    function earningsBadge(ticker, data, retrying = false) {
        if (data && data.status === 'not_applicable') return '';
        const risk = String(data?.risk_level || '').toLowerCase();
        const tone = risk === 'critical' || risk === 'elevated' ? 'border-rose-200 bg-rose-50 text-rose-700'
            : risk === 'moderate' ? 'border-amber-200 bg-amber-50 text-amber-700' : 'border-violet-200 bg-violet-50 text-violet-700';
        const status = String(data?.status || 'waiting').toLowerCase();
        const date = data?.event_date ? shortDate(data.event_date) : '';
        const lastReported = !date && data?.last_event_date ? shortDate(data.last_event_date) : '';
        const timing = TIMING_SHORT[data?.timing] ? ` ${TIMING_SHORT[data.timing]}` : '';
        // The label is HTML (escaped piece by piece): "Earnings · Oct 28 · in 15d" on every screen; desktop adds the timing (BMO/AMC).
        const countdownText = date ? countdown(daysUntil(data.event_date, data.days_to_event)) : '';
        const labelHtml = !data || (status === 'waiting' && !data.initialized)
            ? esc(retrying && !data ? 'Earnings · Retrying…' : 'Earnings · Checking…')
            : date ? `Earnings · ${esc(date)}<span class="hidden sm:inline">${esc(timing)}</span> · ${esc(countdownText)}`
                : lastReported ? `Earnings · Reported ${esc(lastReported)}<span class="hidden sm:inline"> · next TBA</span>`
                    : esc(`Earnings · ${status === 'stale' ? 'Stale date' : 'Date unavailable'}`);
        const baseTitle = 'Open earnings date, estimates, beat record, past reactions and risk analysis';
        const title = date ? `Earnings ${date}${timing}${countdownText ? ` (${countdownText})` : ''}. ${baseTitle}`
            : lastReported ? `Last reported ${lastReported}; the next date is not announced yet. ${baseTitle}` : baseTitle;
        return `<button type="button" class="${BUTTON} ${tone}" title="${esc(title)}" onclick="event.stopPropagation(); BotBullsResearch.openEarnings('${esc(ticker)}')">📅 ${labelHtml}</button>`;
    }

    function configure(options = {}) {
        Object.assign(config, options);
    }

    window.BotBullsResearch = {
        configure, openEarnings, openFundamentals, fundamentalsBadge, earningsBadge,
        renderEarnings, renderFundamentals, metricValue, esc,
        FUNDAMENTAL_REPORT_VERSION, EARNINGS_DATA_VERSION,
        closeAll() { document.querySelectorAll('[data-research-modal]').forEach(closeModal); },
    };
})();
