/* Screener live price: a price-only refresh, like the Dashboard's, independent of scans.
 *
 * A scan is slow (completed candles, indicators) and does not run while the session is closed, and a settings save
 * clears the cached scan, so a card could show "---" for a long time. This module asks the lightweight
 * /api/quote/{ticker} endpoint (the one the Dashboard uses) every 60 seconds for each Screener ticker and writes only
 * the price text. It never creates a signal, alert or order, and it is UI polling only: it pauses while the tab is
 * hidden and the backend does not depend on it. */
(function () {
    'use strict';

    const INTERVAL_MS = 60000;
    const TIMEOUT_MS = 6000;
    const MAX_PARALLEL = 3;
    const MAX_TICKERS = 40;
    const FRESH_MS = 150000;         // a quote older than this is not shown over the scan price (2.5 refresh intervals,
                                     // so one slow or failed refresh does not make the price flip back to the scan value)
    const MAX_QUOTE_AGE_MS = 120000; // oldest cached quote accepted when the server marks it stale
    const quotes = new Map();        // ticker -> { price, at }
    const inFlight = new Set();
    const nextAttemptAt = new Map(); // ticker -> epoch ms (failure backoff)
    const failures = new Map();
    let timer = null;
    let cycleRunning = false;

    const norm = (t) => String(t || '').toUpperCase().replace('/', '').trim();

    function format(ticker, price) {
        const crypto = typeof isCrypto === 'function' ? isCrypto(ticker) : false;
        return `$${formatAssetPrice(price, crypto)}`;
    }

    /** Latest fresh quote text for a ticker, or '' when there is none. */
    function get(ticker) {
        const entry = quotes.get(norm(ticker));
        if (!entry || Date.now() - entry.at > FRESH_MS) return '';
        return format(norm(ticker), entry.price);
    }

    function paint(ticker) {
        const text = get(ticker);
        if (!text) return;
        document.querySelectorAll(`[data-live-price="${norm(ticker)}"]`).forEach((node) => {
            if (node.textContent !== text) node.textContent = text;
            node.dataset.priceSource = 'quote';
        });
    }

    function tickers() {
        const data = window.lastBotsData || {};
        const settings = data.ticker_settings || {};
        return (Array.isArray(data.tradelist) ? data.tradelist : [])
            .map(norm)
            .filter((t) => t && settings[t]?.enabled !== false)
            .slice(0, MAX_TICKERS);
    }

    async function fetchOne(ticker) {
        if (inFlight.has(ticker) || Date.now() < (nextAttemptAt.get(ticker) || 0)) return;
        inFlight.add(ticker);
        const controller = new AbortController();
        const abortTimer = setTimeout(() => controller.abort(), TIMEOUT_MS);
        try {
            const headers = await getAuthHeaders();
            const response = await fetch(`${API_BASE}/api/quote/${encodeURIComponent(ticker)}`, { headers, signal: controller.signal, cache: 'no-store' });
            if (!response.ok) throw new Error(`HTTP ${response.status}`);
            const quote = await response.json();
            const price = Number(quote.price);
            if (!Number.isFinite(price) || price <= 0) return;
            // The server answers from its cache while it refreshes in the background, and flags that answer "stale"
            // once it is older than 10 s. This module polls every 60 s, so its answer is ALWAYS flagged stale and
            // skipping those left cards without a scan price (a Session Rider card has none) showing "---". A
            // stale quote is fine while it is recent: accept up to MAX_QUOTE_AGE_MS and date it by its real age,
            // so FRESH_MS still measures how old the price on screen is.
            const ageMs = Math.max(0, Number(quote.cache_age_seconds) || 0) * 1000;
            if (quote.stale && (!Number.isFinite(ageMs) || ageMs > MAX_QUOTE_AGE_MS)) return;
            quotes.set(ticker, { price, at: Date.now() - (quote.stale ? ageMs : 0) });
            failures.delete(ticker);
            nextAttemptAt.delete(ticker);
            paint(ticker);
        } catch (error) {
            const count = (failures.get(ticker) || 0) + 1;
            failures.set(ticker, count);
            nextAttemptAt.set(ticker, Date.now() + Math.min(60000, 5000 * (2 ** Math.min(count - 1, 4))));
        } finally {
            clearTimeout(abortTimer);
            inFlight.delete(ticker);
        }
    }

    /** Fetch the given tickers now (bounded parallelism). Used on a timer and right after a settings save. */
    async function refresh(list) {
        const queue = (Array.isArray(list) ? list : tickers()).map(norm).filter(Boolean);
        if (!queue.length || document.hidden) return;
        const workers = Array.from({ length: Math.min(MAX_PARALLEL, queue.length) }, async () => {
            while (queue.length) await fetchOne(queue.shift());
        });
        await Promise.all(workers);
    }

    async function cycle() {
        if (cycleRunning || document.hidden || !document.getElementById('tradelistContainer')) return;
        cycleRunning = true;
        try { await refresh(); } finally { cycleRunning = false; }
    }

    function start() {
        if (timer) return;
        timer = setInterval(cycle, INTERVAL_MS);
        cycle();
    }

    /** Make the next fetch for a ticker immediate (e.g. after its settings changed). */
    function refreshNow(ticker) {
        const t = norm(ticker);
        if (!t) return Promise.resolve();
        nextAttemptAt.delete(t);
        failures.delete(t);
        return refresh([t]);
    }

    /** After the cards render: fetch only tickers that have no fresh quote yet, so first paint does not wait a whole refresh interval. */
    function refreshMissing() {
        const missing = tickers().filter((t) => !get(t));
        if (missing.length) void refresh(missing);
        else tickers().forEach(paint);
    }

    document.addEventListener('visibilitychange', () => { if (!document.hidden) cycle(); });
    window.ScreenerLivePrice = { get, refresh, refreshNow, refreshMissing, start, paint };
    if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', start);
    else start();
})();
