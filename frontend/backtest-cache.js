/*
 * Candle-cache control for the Backtest "Target Ticker" box.
 *
 * Backtests reuse cached candles when the cache covers the requested range, so the same data is not
 * downloaded twice; on a miss the server downloads from Alpaca first and falls back to Yahoo. This
 * module shows what is cached for the typed ticker and lets the user purge it (trash icon) so the next
 * run downloads fresh candles. The cache holds public market candles only; a purge never touches
 * settings or trade history. Endpoints: GET /api/backtest/cache, POST /api/backtest/cache/purge.
 */
(function () {
    'use strict';
    if (window.BtCache) return;

    const TICKER_RE = /^[A-Z0-9.\-]{1,15}$/;
    const el = (id) => document.getElementById(id);
    const state = { checked: '', cached: [], busy: false, seq: 0 };

    const apiBase = () => window.BACKTEST_API_BASE || '';
    const tickerValue = () => String(el('btTicker')?.value || '').trim().toUpperCase();

    async function request(path, options = {}) {
        const headers = typeof getAuthHeaders === 'function' ? await getAuthHeaders() : { 'Content-Type': 'application/json' };
        const response = await fetch(`${apiBase()}${path}`, { ...options, headers });
        if (!response.ok) throw new Error(`HTTP ${response.status}`);
        return response.json();
    }

    function render(message, tone) {
        const status = el('btCacheStatus');
        const dot = el('btCacheDot');
        const button = el('btCachePurgeBtn');
        const cached = state.cached;
        if (dot) dot.classList.toggle('hidden', !cached.length);
        if (button) {
            const symbol = state.checked || 'this ticker';
            const title = cached.length
                ? `Cached candles for ${symbol}: ${cached.map((c) => c.timeframe).join(', ')}. Click to purge them.`
                : `No cached candles for ${symbol}. Click to check.`;
            button.title = title;
            button.setAttribute('aria-label', title);
            button.disabled = state.busy;
            button.classList.toggle('opacity-50', state.busy);
        }
        if (!status) return;
        status.className = `text-[0.6rem] font-bold px-1 break-words ${tone === 'warn' ? 'text-amber-600' : 'text-indigo-500/70'}`;
        if (message) { status.textContent = message; return; }
        if (!state.checked) { status.textContent = ''; return; }
        status.textContent = cached.length
            ? `Cached candles for ${state.checked}: ${cached.map((c) => c.timeframe + (c.requested_provider === 'yahoo' ? ' (Yahoo)' : '')).join(', ')} · reused without downloading.`
            : `No cached candles for ${state.checked}: the next run downloads from Alpaca (Yahoo as fallback).`;
    }

    async function refresh() {
        const symbol = tickerValue();
        if (!TICKER_RE.test(symbol)) { state.checked = ''; state.cached = []; render(); return; }
        const ticket = ++state.seq;
        try {
            const data = await request(`/api/backtest/cache?ticker=${encodeURIComponent(symbol)}`);
            if (ticket !== state.seq) return; // a newer ticker was typed meanwhile
            state.checked = symbol;
            state.cached = Array.isArray(data.cached) ? data.cached : [];
            render();
        } catch (error) {
            if (ticket !== state.seq) return;
            state.checked = symbol; state.cached = [];
            render('Cache status unavailable right now.', 'warn');
        }
    }

    async function purge() {
        const symbol = tickerValue();
        if (state.busy) return;
        if (!TICKER_RE.test(symbol)) { render('Enter a valid ticker first.', 'warn'); return; }
        await refresh();
        if (!state.cached.length) return; // nothing to purge: the status line already says so
        const sets = state.cached.map((c) => c.timeframe).join(', ');
        if (!window.confirm(`Purge cached candle data for ${symbol} (${sets})?\n\nThe next backtest downloads it again: Alpaca first, Yahoo as the fallback.`)) return;
        state.busy = true; render('Purging…');
        try {
            const data = await request('/api/backtest/cache/purge', { method: 'POST', body: JSON.stringify({ ticker: symbol }) });
            try { if (typeof manualBtReplayDataCache !== 'undefined') manualBtReplayDataCache.clear(); } catch (e) { /* chart cache is optional */ }
            state.busy = false;
            state.cached = [];
            state.checked = symbol;
            render(`Purged ${data.files_removed} cached file(s) for ${symbol}. The next run downloads fresh candles (Alpaca first, Yahoo as fallback).`);
        } catch (error) {
            state.busy = false;
            render('Could not purge the cache. Try again.', 'warn');
        }
    }

    function init() {
        const input = el('btTicker');
        const button = el('btCachePurgeBtn');
        if (!input || !button) return;
        let timer = null;
        const soon = () => { clearTimeout(timer); timer = setTimeout(refresh, 450); };
        ['input', 'change', 'blur'].forEach((name) => input.addEventListener(name, soon));
        button.addEventListener('click', purge);
        // A ticker picked from the search list is set programmatically (no input event): notice it cheaply.
        setInterval(() => { if (!document.hidden && !state.busy && tickerValue() !== state.checked) soon(); }, 1500);
        soon();
    }

    window.BtCache = { refresh, purge };
    if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', init);
    else init();
})();
