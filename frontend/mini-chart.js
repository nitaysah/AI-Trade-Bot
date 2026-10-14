/*
 * Mini candle chart for the Bots and Screener settings dialogs.
 *
 * It asks the light /api/mini-chart endpoint for recent candles only (no indicator analysis), so it no longer waits
 * behind slow scans. To stay useful when the network or a provider is slow:
 *   - the last good candles for that ticker and timeframe are drawn immediately from localStorage,
 *   - the live request gets a generous timeout and one retry,
 *   - if it still fails the saved chart stays on screen with a small note; with nothing saved the message is clickable to retry,
 *   - an obsolete request (dialog closed, ticker or timeframe changed) is aborted and never draws.
 * Informational only; nothing here places orders.
 */
(function () {
    'use strict';
    if (window.MiniChart) return;

    const CACHE_PREFIX = 'miniChart:v1:';
    const CACHE_MAX_AGE_MS = 24 * 60 * 60 * 1000;
    const REQUEST_TIMEOUT_MS = 20000;
    const RETRY_DELAY_MS = 1500;
    const FRESH_MS = 12000;

    const state = {
        chart: null, candles: null, volume: null, observer: null, resizeHandler: null,
        container: null, statusEl: null, noteEl: null,
        requestId: 0, controller: null, key: '', drawnAt: 0, lastArgs: null,
    };

    const esc = (value) => String(value == null ? '' : value);

    function setStatus(message, tone, onClick) {
        const el = state.statusEl;
        if (!el) return;
        el.textContent = message || '';
        el.classList.toggle('hidden', !message);
        el.classList.toggle('text-rose-500', tone === 'error');
        el.classList.toggle('text-indigo-400/80', tone !== 'error');
        el.classList.toggle('pointer-events-none', !onClick);
        el.classList.toggle('pointer-events-auto', !!onClick);
        el.classList.toggle('cursor-pointer', !!onClick);
        el.onclick = onClick || null;
        el.setAttribute('role', onClick ? 'button' : 'status');
    }

    function setNote(message) {
        if (!state.container) return;
        if (!state.noteEl || !state.noteEl.isConnected) {
            const host = state.container.parentElement && state.container.parentElement.parentElement
                ? state.container.parentElement.parentElement : state.container.parentElement;
            if (!host) return;
            const note = document.createElement('div');
            note.className = 'absolute top-1 left-2 px-1.5 py-0.5 rounded bg-white/70 text-[0.5rem] font-black uppercase tracking-wider text-amber-600 pointer-events-none hidden';
            host.appendChild(note);
            state.noteEl = note;
        }
        state.noteEl.textContent = message || '';
        state.noteEl.classList.toggle('hidden', !message);
    }

    function destroy() {
        state.requestId += 1;
        if (state.controller) { try { state.controller.abort(); } catch (e) { /* already settled */ } state.controller = null; }
        if (state.observer) { state.observer.disconnect(); state.observer = null; }
        if (state.resizeHandler) { window.removeEventListener('resize', state.resizeHandler); state.resizeHandler = null; }
        if (state.chart) { try { state.chart.remove(); } catch (e) { /* chart may already be detached */ } }
        state.chart = state.candles = state.volume = null;
        state.key = '';
        state.drawnAt = 0;
        setNote('');
    }

    function ensureChart() {
        const container = state.container;
        if (!container || typeof LightweightCharts === 'undefined') return false;
        if (state.chart) return true;
        state.chart = LightweightCharts.createChart(container, {
            width: Math.max(container.clientWidth, 240),
            height: Math.max(container.clientHeight, 72),
            layout: { background: { color: 'transparent' }, textColor: '#64748b', fontSize: 9 },
            grid: { vertLines: { color: 'rgba(148,163,184,0.08)' }, horzLines: { color: 'rgba(148,163,184,0.08)' } },
            rightPriceScale: { borderColor: 'rgba(148,163,184,0.22)', minimumWidth: 48 },
            timeScale: { borderColor: 'rgba(148,163,184,0.22)', timeVisible: true, secondsVisible: false, rightOffset: 2, barSpacing: 7 },
            crosshair: { vertLine: { color: 'rgba(99,102,241,0.35)' }, horzLine: { color: 'rgba(99,102,241,0.25)' } },
            handleScroll: false,
            handleScale: false,
        });
        state.candles = state.chart.addCandlestickSeries({
            upColor: '#10b981', downColor: '#ef4444', borderUpColor: '#10b981', borderDownColor: '#ef4444',
            wickUpColor: '#10b981', wickDownColor: '#ef4444', priceLineVisible: false, lastValueVisible: true,
        });
        state.volume = state.chart.addHistogramSeries({
            color: 'rgba(99,102,241,0.20)', priceFormat: { type: 'volume' }, priceScaleId: 'mini-chart-volume',
            scaleMargins: { top: 0.78, bottom: 0 },
        });
        state.chart.priceScale('mini-chart-volume').applyOptions({ visible: false });
        const resize = () => {
            if (!state.chart || !container.isConnected) return;
            state.chart.applyOptions({ width: Math.max(container.clientWidth, 240), height: Math.max(container.clientHeight, 72) });
        };
        if (typeof ResizeObserver !== 'undefined') {
            state.observer = new ResizeObserver(resize);
            state.observer.observe(container);
        } else {
            state.resizeHandler = resize;
            window.addEventListener('resize', resize, { passive: true });
        }
        requestAnimationFrame(resize);
        return true;
    }

    function clean(bars) {
        const seen = new Set();
        return (Array.isArray(bars) ? bars : []).map((b) => ({
            time: Math.floor(Number(b.time)), open: Number(b.open), high: Number(b.high), low: Number(b.low), close: Number(b.close),
            value: Math.max(0, Number(b.volume) || 0),
        })).filter((b) => b.time > 0 && [b.open, b.high, b.low, b.close].every((n) => isFinite(n) && n > 0) && !seen.has(b.time) && seen.add(b.time))
            .sort((a, b) => a.time - b.time).slice(-120);
    }

    function draw(bars) {
        const data = clean(bars);
        if (!data.length || !ensureChart()) return false;
        state.candles.setData(data.map(({ time, open, high, low, close }) => ({ time, open, high, low, close })));
        state.volume.setData(data.map((b) => ({ time: b.time, value: b.value, color: b.close >= b.open ? 'rgba(16,185,129,0.30)' : 'rgba(239,68,68,0.30)' })));
        state.chart.timeScale().fitContent();
        state.drawnAt = Date.now();
        return true;
    }

    function readSaved(key) {
        try {
            const saved = JSON.parse(localStorage.getItem(CACHE_PREFIX + key) || 'null');
            if (saved && Array.isArray(saved.bars) && saved.bars.length && Date.now() - Number(saved.savedAt || 0) < CACHE_MAX_AGE_MS) return saved;
        } catch (e) { /* storage is a per-viewer convenience only */ }
        return null;
    }

    function save(key, bars) {
        try { localStorage.setItem(CACHE_PREFIX + key, JSON.stringify({ savedAt: Date.now(), bars: bars.slice(-120) })); } catch (e) { /* ignore */ }
    }

    async function request(url, headers, parentController) {
        const controller = new AbortController();
        const timer = setTimeout(() => controller.abort('timeout'), REQUEST_TIMEOUT_MS);
        const onParentAbort = () => controller.abort('superseded');
        parentController.signal.addEventListener('abort', onParentAbort, { once: true });
        try {
            const response = await fetch(url, { headers, signal: controller.signal });
            if (!response.ok) {
                const detail = await response.json().then((j) => j.detail).catch(() => '');
                const error = new Error(detail || `HTTP ${response.status}`);
                error.status = response.status;
                throw error;
            }
            return await response.json();
        } catch (error) {
            // abort(reason) makes fetch reject with the reason itself, so name the two cases explicitly.
            if (controller.signal.reason === 'timeout') throw Object.assign(new Error('Request timed out'), { timeout: true });
            if (controller.signal.reason === 'superseded') throw Object.assign(new Error('superseded'), { superseded: true });
            throw error;
        } finally {
            clearTimeout(timer);
            parentController.signal.removeEventListener('abort', onParentAbort);
        }
    }

    /*
     * args: { container, status, ticker, timeframe, apiBase, getHeaders, force }
     * container / status are element ids; the page's own auth helper supplies the headers.
     */
    async function show(args) {
        const ticker = String(args.ticker || '').trim().toUpperCase();
        const timeframe = args.timeframe || '5Min';
        if (!ticker) return;
        const container = document.getElementById(args.container);
        if (!container) return;
        if (state.container !== container) { destroy(); state.container = container; state.noteEl = null; }
        state.statusEl = document.getElementById(args.status);
        state.lastArgs = args;
        const key = `${ticker}:${timeframe}`;
        // Same chart already drawn a moment ago, or already loading: nothing to do.
        if (!args.force && state.key === key && state.controller) return;
        if (!args.force && state.key === key && state.drawnAt && Date.now() - state.drawnAt < FRESH_MS) return;

        const requestId = ++state.requestId;
        if (state.controller) { try { state.controller.abort('superseded'); } catch (e) { /* settled */ } }
        const controller = new AbortController();
        state.controller = controller;
        const keyChanged = state.key !== key;
        state.key = key;
        const current = () => requestId === state.requestId && container.isConnected;

        let showing = !keyChanged && state.drawnAt > 0;
        if (keyChanged) {
            if (state.chart) { state.candles.setData([]); state.volume.setData([]); }
            state.drawnAt = 0;
            const saved = readSaved(key);
            showing = !!(saved && draw(saved.bars));
            if (showing) { state.drawnAt = 0; setNote('Updating…'); setStatus(''); } else { setNote(''); setStatus('Loading chart…'); }
        }

        const url = `${args.apiBase}/api/mini-chart/${encodeURIComponent(ticker)}?timeframe=${encodeURIComponent(timeframe)}`;
        let lastError = null;
        for (let attempt = 0; attempt < 2; attempt += 1) {
            try {
                const headers = await args.getHeaders();
                if (!current()) return;
                const data = await request(url, headers, controller);
                if (!current()) return;
                if (!draw(data.bars)) throw new Error('No candles returned');
                save(key, clean(data.bars));
                setStatus('');
                setNote(data.stale ? 'Delayed data' : '');
                if (state.controller === controller) state.controller = null;
                return;
            } catch (error) {
                lastError = error;
                if (!current() || (error && error.superseded)) return;
                if (error && (error.status === 400 || error.status === 404 || error.status === 401)) break;  // a retry cannot fix these
                if (attempt === 0) await new Promise((resolve) => setTimeout(resolve, RETRY_DELAY_MS));
                if (!current()) return;
            }
        }
        if (!current()) return;
        if (state.controller === controller) state.controller = null;
        state.key = showing ? key : '';  // a failed load must not look "already loaded" next time
        if (showing) {
            setStatus('');
            setNote('Saved chart · live update failed');
        } else {
            const retry = () => show({ ...args, force: true });
            const timedOut = !!lastError && (lastError.timeout === true || lastError.status === 504 || /timed out|taking too long/i.test(esc(lastError.message)));
            setStatus(timedOut ? 'Chart is slow · tap to retry' : 'Chart unavailable · tap to retry', 'error', retry);
        }
    }

    window.MiniChart = { show, destroy, retry() { if (state.lastArgs) return show({ ...state.lastArgs, force: true }); return undefined; } };
})();
