// ═══════════════════════════════════════════════════
// AI Trading Bot — Dashboard JS
// Auto-refreshing, multi-panel, real-time dashboard
// ═══════════════════════════════════════════════════

// 6. Production API Configuration (Auto-switch between Local and Cloud)
var CLOUD_URL = 'https://207-211-184-22.sslip.io';
var API_BASE = window.location.hostname === 'localhost' || window.location.hostname === '127.0.0.1'
    ? 'http://localhost:8000'
    : CLOUD_URL;
var REFRESH_INTERVAL = 60000; // 60 seconds (1 minute)
var LAST_VIEWED_TICKER_KEY = 'lastViewedTicker';
var DASHBOARD_CHART_CONTEXT_KEY = 'dashboardChartContext';
var GENERIC_DASHBOARD_CARDS_CACHE_KEY = 'dashboardGenericCardsCacheV1';
var GENERIC_DASHBOARD_CARDS_CLIENT_TTL_MS = 5 * 60 * 1000;
var GENERIC_DASHBOARD_CARDS_REFRESH_MS = GENERIC_DASHBOARD_CARDS_CLIENT_TTL_MS;
// Coalesce identical chart refreshes. A chart calculation can take several
// seconds on a cold provider fetch; overlapping requests waste provider/API
// capacity and let a slower response race the current selection.
var chartRequestInFlight = new Map();

function isDashboardPageActive() {
    const path = (window.location.pathname || '').toLowerCase();
    return path.endsWith('/dashboard.html') || path === '/' || path.endsWith('/frontend/');
}

function shouldRunDashboardRefresh() {
    return isDashboardPageActive() && !document.hidden;
}

function normalizeTicker(ticker) {
    const normalized = (ticker || '').toString().trim().toUpperCase();
    return normalized && normalized !== 'NULL' && normalized !== 'UNDEFINED' ? normalized : null;
}

async function fetchWithTimeout(url, options = {}, timeoutMs = 12000) {
    const controller = new AbortController();
    const externalSignal = options.signal;
    const requestOptions = { ...options };
    delete requestOptions.signal;
    const abortFromCaller = () => controller.abort();
    if (externalSignal?.aborted) controller.abort();
    else externalSignal?.addEventListener('abort', abortFromCaller, { once: true });
    let didTimeout = false;
    const timeoutId = setTimeout(() => {
        didTimeout = true;
        controller.abort();
    }, timeoutMs);
    try {
        return await fetch(url, {
            ...requestOptions,
            signal: controller.signal
        });
    } catch (error) {
        if (didTimeout && error?.name === 'AbortError') {
            const timeoutError = new Error(`Request timed out after ${timeoutMs}ms`);
            timeoutError.name = 'TimeoutError';
            timeoutError.url = url;
            throw timeoutError;
        }
        throw error;
    } finally {
        clearTimeout(timeoutId);
        externalSignal?.removeEventListener('abort', abortFromCaller);
    }
}

function isRequestTimeout(error) {
    return error?.name === 'TimeoutError';
}

function isRequestAbort(error) {
    return error?.name === 'AbortError';
}

function readLastViewedTicker() {
    try {
        return normalizeTicker(localStorage.getItem(LAST_VIEWED_TICKER_KEY));
    } catch (e) {
        console.warn('[prefs] Could not read last viewed ticker:', e);
        return null;
    }
}

function rememberSelectedTicker(ticker) {
    const normalized = normalizeTicker(ticker);
    if (!normalized) return null;
    selectedTicker = normalized;
    try {
        localStorage.setItem(LAST_VIEWED_TICKER_KEY, normalized);
    } catch (e) {
        console.warn('[prefs] Could not save last viewed ticker:', e);
    }
    return normalized;
}

function readDashboardChartContext() {
    try {
        const raw = sessionStorage.getItem(DASHBOARD_CHART_CONTEXT_KEY);
        if (!raw) return null;
        const parsed = JSON.parse(raw);
        return parsed && typeof parsed === 'object' ? parsed : null;
    } catch (e) {
        console.warn('[prefs] Could not read dashboard chart context:', e);
        return null;
    }
}

function getChartSourceForTicker(ticker) {
    const normalized = normalizeTicker(ticker);
    const context = readDashboardChartContext();
    if (context && normalizeTicker(context.ticker) === normalized && context.source) {
        return context.source;
    }
    return 'dashboard';
}

function updateActiveTickerDisplay(ticker) {
    const normalized = normalizeTicker(ticker);
    if (!normalized) return;

    const labelEl = document.getElementById('chartTickerLabel');
    if (labelEl) labelEl.textContent = normalized;

    const searchInput = document.getElementById('tickerSearch');
    if (searchInput && searchInput.value !== normalized) {
        searchInput.value = normalized;
    }
    showChartCompanyName(normalized);
}

// Company logo beside the ticker. Lookups are cached per symbol; one that finishes after the user has moved to another
// ticker is ignored. With no logo (ETFs, crypto, failed lookup) a letter badge keeps the header aligned.
const chartCompanyProfiles = new Map();
const chartLogoRetryTimers = new Map();
const chartLogoRetryCounts = new Map();
const chartLogoInFlight = new Set();
function setChartLogo(symbol, profile) {
    const el = document.getElementById('chartLogo');
    if (!el) return;
    const name = (profile && profile.name) || '';
    el.title = name || symbol;
    el.textContent = '';
    el.classList.remove('chart-logo-img');
    const showLetter = () => { el.textContent = ''; el.classList.remove('chart-logo-img'); el.textContent = symbol.charAt(0); };
    if (profile && /^https:\/\//.test(profile.logo || '')) {
        const img = document.createElement('img');
        img.alt = '';
        img.decoding = 'async';
        img.referrerPolicy = 'no-referrer';
        img.onerror = showLetter;
        img.src = profile.logo;
        el.classList.add('chart-logo-img');
        el.appendChild(img);
    } else {
        showLetter();
    }
}

async function showChartCompanyName(symbol) {
    if (chartCompanyProfiles.has(symbol)) {
        setChartLogo(symbol, chartCompanyProfiles.get(symbol));
        return;
    }
    setChartLogo(symbol, null);
    // Same gate as the other dashboard fetches: before sign-in there is no token, so asking would only log a 401.
    // The caller runs again on every chart sync, and one delayed retry covers a page that is still signing in.
    const authReady = window.auth && (window.auth.currentUser || localStorage.getItem('dev_mode') === 'true');
    if (!authReady) {
        if (!chartLogoRetryTimers.has(symbol) && (chartLogoRetryCounts.get(symbol) || 0) < 5) {
            chartLogoRetryCounts.set(symbol, (chartLogoRetryCounts.get(symbol) || 0) + 1);
            chartLogoRetryTimers.set(symbol, setTimeout(() => {
                chartLogoRetryTimers.delete(symbol);
                const current = normalizeTicker(document.getElementById('chartTickerLabel')?.textContent);
                if (current === symbol) showChartCompanyName(symbol);
            }, 2000));
        }
        return;
    }
    if (chartLogoInFlight.has(symbol)) return;
    chartLogoInFlight.add(symbol);
    try {
        const headers = await getAuthHeaders();
        const res = await fetch(`${API_BASE}/api/company-name/${encodeURIComponent(symbol)}`, { headers });
        if (!res.ok) return;
        const data = await res.json();
        const profile = { name: String((data && data.name) || '').trim(), logo: String((data && data.logo) || '').trim() };
        if (profile.name || profile.logo) chartCompanyProfiles.set(symbol, profile);
        const current = normalizeTicker(document.getElementById('chartTickerLabel')?.textContent);
        if (current === symbol) setChartLogo(symbol, profile);
    } catch (e) { /* the logo is cosmetic */ }
    finally { chartLogoInFlight.delete(symbol); }
}

function isCryptoTicker(ticker) {
    const clean = normalizeTicker(ticker) || '';
    return clean.includes('USD') || ['BTC', 'ETH', 'LTC', 'SOL', 'DOGE', 'ADA', 'DOT', 'SHIB', 'AVAX', 'XRP'].some(c => clean.startsWith(c));
}

function isExtendedChartSession(ticker = selectedTicker) {
    return !isCryptoTicker(ticker)
        && currentChartSession === 'extended'
        && currentBackendTf !== '1Day';
}

function isDashboardMobileViewport() {
    return typeof window !== 'undefined'
        && (window.matchMedia?.('(max-width: 768px)').matches || window.innerWidth <= 768);
}

function getDashboardChartFontSize() {
    return isDashboardMobileViewport() ? 9 : 12;
}

function getDashboardSubchartResponsiveOptions() {
    return {
        layout: { fontSize: getDashboardChartFontSize() },
        rightPriceScale: { minimumWidth: isDashboardMobileViewport() ? 48 : 75 },
    };
}

function getDashboardChartBackground(ticker = selectedTicker) {
    // Extended mode uses the RTH white base; the extHoursSeries shades only
    // pre-market and after-hours bars so regular trading hours stay identical
    // to the dedicated RTH view.
    return DASHBOARD_CHART_BG_REGULAR;
}

function applyDashboardChartBackground(ticker = selectedTicker) {
    const isExtended = isExtendedChartSession(ticker);
    const backgroundColor = getDashboardChartBackground(ticker);
    const chartContainer = document.getElementById('tradingChart');
    const chartStage = chartContainer?.closest('.chart-stage');
    const applyDomBackground = () => {
        [chartStage, chartContainer].forEach(el => {
            if (!el) return;
            el.style.setProperty('--dashboard-chart-bg', backgroundColor);
            el.classList.toggle('chart-extended-session-bg', isExtended);
        });
        if (chartContainer) {
            chartContainer
                .querySelectorAll('table, tr, td')
                .forEach(el => {
                    el.style.setProperty('background', backgroundColor, 'important');
                    el.style.setProperty('background-color', backgroundColor, 'important');
                });
        }
    };
    applyDomBackground();
    requestAnimationFrame(applyDomBackground);
    if (lwChart) {
        lwChart.applyOptions({
            layout: {
                background: { color: backgroundColor },
                textColor: '#334155',
                fontSize: getDashboardChartFontSize(),
            },
            rightPriceScale: {
                minimumWidth: isDashboardMobileViewport() ? 48 : 64,
            },
        });
    }
}

function formatChartTime(time, compact = true) {
    const ts = typeof time === 'object' && time?.timestamp ? time.timestamp : time;
    const d = new Date(ts * 1000);
    const tfSeconds = CHART_INTERVALS[currentBackendTf]?.seconds || 300;

    // For tooltip/crosshair (compact = false), we want full Date + Time
    if (!compact) {
        if (tfSeconds >= 86400) {
            return d.toLocaleDateString('en-US', { timeZone: 'America/Chicago', month: 'short', day: '2-digit', year: 'numeric' });
        }
        return d.toLocaleString('en-US', { timeZone: 'America/Chicago', month: 'short', day: '2-digit', year: 'numeric', hour: '2-digit', minute: '2-digit', hour12: false });
    }

    // For x-axis tick marks (compact = true)
    if (tfSeconds < 60) {
        return d.toLocaleTimeString('en-US', { timeZone: 'America/Chicago', hour: '2-digit', minute: '2-digit', second: '2-digit', hour12: false });
    }
    if (tfSeconds >= 86400) {
        return d.toLocaleDateString('en-US', { timeZone: 'America/Chicago', month: 'short', day: '2-digit' });
    }
    return d.toLocaleTimeString('en-US', { timeZone: 'America/Chicago', hour: '2-digit', minute: '2-digit', hour12: false });
}

function formatDateTimeCT(time) {
    if (!time) return '—';
    const d = new Date(time * 1000);
    return d.toLocaleString('en-US', {
        timeZone: 'America/Chicago',
        month: 'short',
        day: '2-digit',
        year: 'numeric',
        hour: '2-digit',
        minute: '2-digit',
        hour12: false
    }) + ' CT';
}

function formatTimeOnlyCT(time) {
    if (!time) return '—';
    const d = new Date(time * 1000);
    return d.toLocaleTimeString('en-US', {
        timeZone: 'America/Chicago',
        hour: '2-digit',
        minute: '2-digit',
        hour12: false
    }) + ' CT';
}

function getChartDisplayOffsetSeconds(timeframe = currentBackendTf) {
    // Provider timestamps identify the candle open. Display intraday candles
    // at their close so the axis answers "when did this completed candle
    // finish?" (for example, 08:30–08:45 is labelled 08:45 CT). Daily bars
    // remain date-labelled and therefore keep their original timestamp.
    return CHART_INTERVALS[timeframe]?.seconds < 86400
        ? (CHART_INTERVALS[timeframe]?.seconds || 0)
        : 0;
}

function toCloseTimeDisplayBar(bar, timeframe = currentBackendTf) {
    const sourceTime = Number(bar?.time);
    if (!Number.isFinite(sourceTime)) return bar;
    return {
        ...bar,
        source_time: sourceTime,
        time: sourceTime + getChartDisplayOffsetSeconds(timeframe),
    };
}

function isRegularMarketBar(bar) {
    const sourceTime = Number(bar?.source_time);
    const d = new Date((Number.isFinite(sourceTime) ? sourceTime : Number(bar?.time)) * 1000);
    const parts = new Intl.DateTimeFormat('en-US', {
        timeZone: 'America/Chicago',
        weekday: 'short',
        hour: '2-digit',
        minute: '2-digit',
        hour12: false
    }).formatToParts(d);
    const values = Object.fromEntries(parts.map(p => [p.type, p.value]));
    if (values.weekday === 'Sat' || values.weekday === 'Sun') return false;
    const mins = Number(values.hour) * 60 + Number(values.minute);
    return mins >= (8 * 60 + 30)
        && (Number.isFinite(sourceTime) ? mins < (15 * 60) : mins <= (15 * 60));
}

function dashboardChartRefreshSessionIsOpen(nowMs = Date.now(), ticker = selectedTicker) {
    if (isCryptoTicker(ticker)) return true;
    try {
        const parts = new Intl.DateTimeFormat('en-US', {
            timeZone: 'America/Chicago',
            weekday: 'short',
            hour: '2-digit',
            minute: '2-digit',
            hour12: false,
        }).formatToParts(new Date(nowMs));
        const values = Object.fromEntries(parts.map(part => [part.type, part.value]));
        if (values.weekday === 'Sat' || values.weekday === 'Sun') return false;
        const minutes = Number(values.hour) * 60 + Number(values.minute);
        if (!Number.isFinite(minutes)) return false;

        // The Dashboard's displayed sessions are 03:00–19:00 CT for extended
        // trading and 08:30–15:00 CT for regular trading. Do not use wall-clock
        // candle boundaries outside these windows: an equity provider will not
        // deliver the "missing" bar and the heartbeat would otherwise retry it.
        return currentChartSession === 'extended'
            ? minutes >= (3 * 60) && minutes <= (19 * 60)
            : minutes >= (8 * 60 + 30) && minutes <= (15 * 60);
    } catch (error) {
        // Keep a chart usable in an unusual browser locale; the server still
        // coalesces duplicate requests if session detection is unavailable.
        return true;
    }
}

function getRangeStartTime(bars) {
    if (!bars?.length) return null;
    const range = CHART_RANGES[currentChartRange] || CHART_RANGES["1D"];
    if (range.days == null && !range.ytd) return null;
    const lastTime = bars[bars.length - 1].time;
    if (range.ytd) {
        const d = new Date(lastTime * 1000);
        return Date.UTC(d.getUTCFullYear(), 0, 1) / 1000;
    }
    return lastTime - (range.days * 86400);
}

function getDisplayBars(rawBars, ticker = selectedTicker) {
    const sorted = [...(rawBars || [])]
        .map(bar => toCloseTimeDisplayBar(bar))
        .sort((a, b) => a.time - b.time);
    const rangeStart = getRangeStartTime(sorted);
    const ranged = rangeStart ? sorted.filter(b => b.time >= rangeStart) : sorted;
    if (isCryptoTicker(ticker) || currentChartSession === 'extended' || currentBackendTf === '1Day') return ranged;
    return ranged.filter(isRegularMarketBar);
}

function updateChartControlState(ticker = selectedTicker) {
    applyDashboardChartBackground(ticker);
    if (lwChart) {
        const showSeconds = (CHART_INTERVALS[currentBackendTf]?.seconds || 300) < 60;
        lwChart.applyOptions({ timeScale: { secondsVisible: showSeconds, timeVisible: true } });
    }
    document.querySelectorAll('#chartTfBar .chart-tf-btn').forEach(btn => {
        btn.classList.toggle('chart-tf-active', btn.dataset.tf === currentBackendTf);
    });
    document.querySelectorAll('#chartRangeBar .chart-range-btn').forEach(btn => {
        btn.classList.toggle('chart-range-active', btn.dataset.range === currentChartRange);
    });

    const crypto = isCryptoTicker(ticker);
    document.getElementById('cryptoLiveBtn')?.classList.toggle('hidden', !crypto);
    document.getElementById('regularHoursBtn')?.classList.toggle('hidden', crypto);
    document.getElementById('extendedHoursBtn')?.classList.toggle('hidden', crypto);
    document.getElementById('regularHoursBtn')?.classList.toggle('chart-session-active', !crypto && currentChartSession === 'regular');
    document.getElementById('extendedHoursBtn')?.classList.toggle('chart-session-active', !crypto && currentChartSession === 'extended');
    document.getElementById('cryptoLiveBtn')?.classList.toggle('chart-session-active', crypto);

    const status = document.getElementById('chartSessionStatus');
    if (status) status.textContent = crypto ? 'Crypto 24h live' : (currentChartSession === 'extended' ? 'Extended Hours' : 'Regular Hours');
}

function updateLastBarInfo(bars, ticker = selectedTicker) {
    const info = document.getElementById('chartLastBarInfo');
    if (!info) return;
    const last = bars?.[bars.length - 1];
    const normalized = normalizeTicker(ticker);
    if (!last || !normalized) {
        info.textContent = 'Last ticker/day: —';
        return;
    }

    let text = `${normalized} • Last completed: ${formatTimeOnlyCT(last.time)}`;
    if (currentBackendTf === '1Day') {
        text += ' • Daily chart uses completed candles';
    }
    info.textContent = text;
}

function updateVisibleHighLow() {
    const bars = window._displayPriceHistory || [];
    if (!bars.length) return;
    let visibleBars = bars;
    try {
        const range = lwChart?.timeScale().getVisibleRange();
        if (range?.from && range?.to) {
            visibleBars = bars.filter(b => b.time >= range.from && b.time <= range.to);
        }
    } catch (e) { }
    if (!visibleBars.length) visibleBars = bars;
    const highBar = visibleBars.reduce((max, b) => b.high > max.high ? b : max, visibleBars[0]);
    const lowBar = visibleBars.reduce((min, b) => b.low < min.low ? b : min, visibleBars[0]);

    if (window._hlLines) {
        window._hlLines.forEach(l => { try { candleSeries.removePriceLine(l); } catch (e) { } });
    }
    window._hlLines = [
        candleSeries.createPriceLine({
            price: highBar.high,
            color: 'rgba(16,185,129,0.75)',
            lineWidth: 1,
            lineStyle: 1,
            axisLabelVisible: true,
            title: '',
        }),
        candleSeries.createPriceLine({
            price: lowBar.low,
            color: 'rgba(239,68,68,0.75)',
            lineWidth: 1,
            lineStyle: 1,
            axisLabelVisible: true,
            title: '',
        }),
    ];
}

function scheduleChartRangeUiRefresh() {
    if (chartRangeUiFrame) return;
    chartRangeUiFrame = requestAnimationFrame(() => {
        chartRangeUiFrame = null;
        updateVisibleHighLow();
        _refreshOverlayPriceLineLabels();
    });
}

function syncOscillatorRanges(range) {
    if (!range) return;
    try { if (oscChartRsi && _visibleIndicators.rsi) oscChartRsi.timeScale().setVisibleLogicalRange(range); } catch (e) { }
    try { if (oscChartMacd && _visibleIndicators.macd) oscChartMacd.timeScale().setVisibleLogicalRange(range); } catch (e) { }
    try { if (oscChartMystic && _visibleIndicators.mystic) oscChartMystic.timeScale().setVisibleLogicalRange(range); } catch (e) { }
    try { if (oscChartAdx && _visibleIndicators.adx) oscChartAdx.timeScale().setVisibleLogicalRange(range); } catch (e) { }
    window.StandardIndicatorSubcharts?.sync('dashboard', range);
}

function scheduleOscillatorRangeSync(range) {
    if (!range) return;
    if (chartOscSyncFrame) cancelAnimationFrame(chartOscSyncFrame);
    chartOscSyncFrame = requestAnimationFrame(() => {
        chartOscSyncFrame = null;
        syncOscillatorRanges(range);
    });
}

function formatPrice(n, forceTicker = null) {
    if (isNaN(n) || n === null) return '---';
    const t = forceTicker || document.getElementById('chartTickerLabel')?.textContent;
    if (t && isCryptoTicker(t)) return new Intl.NumberFormat('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 8, useGrouping: false }).format(n);
    return n >= 1.0 ? n.toFixed(2) : n.toFixed(4);
}

const DEFAULT_TICKER = 'SPY';
let favoriteTickers = JSON.parse(localStorage.getItem('favoriteTickers') || '[]');
let selectedTicker = readLastViewedTicker()
    || normalizeTicker(localStorage.getItem('watchlistFirstTicker'))
    || DEFAULT_TICKER;
let currentBackendTf = localStorage.getItem('lastChartTf') || "1Hour";
let currentChartRange = localStorage.getItem('lastChartRange') || "1M";
let currentChartSession = localStorage.getItem('lastChartSession') || "regular";
let isAlpacaLinked = false; // Track live connection status
let tvWidget = null;
let currentLogTab = "all"; // 'all' or 'trades'
let currentWatchlistTab = "all"; // 'all', 'stocks', or 'crypto'
let latestTradesData = []; // Cached log data
let pendingDashboardRefreshMode = null;
let dashboardHeartbeatTimer = null;
let dashboardHeartbeatEnabled = false;
let lastDashboardRefreshCompletedAt = 0;
let authTokenRequestPromise = null;
let authTokenRequestUserId = null;
let genericDashboardCardsRequestPromise = null;
const chartDataCache = new Map();
let chartRequestSerial = 0;
const CHART_CACHE_VERSION = 'v12';
const CHART_CACHE_TTL_MS = 30 * 60 * 1000;
const CHART_IDB_NAME = 'aiTradeBotChartCache';
const CHART_IDB_VERSION = 1;
const CHART_IDB_STORE = 'chartPayloads';
const COMPLETED_CANDLE_HEARTBEAT_MS = 10000;
const COMPLETED_CANDLE_DELAY_MS = 15000;
const COMPLETED_CANDLE_RETRY_MS = 60000;
const LIVE_PRICE_HEARTBEAT_MS = 10000;
const DASHBOARD_FETCH_TIMEOUT_MS = 20000;
const CHART_FETCH_TIMEOUT_MS = 30000;
const QUOTE_FETCH_TIMEOUT_MS = 4500;
const GENERIC_CARDS_FETCH_TIMEOUT_MS = 8000;
const OLLAMA_MODELS_FETCH_TIMEOUT_MS = 2000;
const DASHBOARD_VISIBILITY_REFRESH_MIN_AGE_MS = 15000;
const DASHBOARD_CHART_BG_REGULAR = '#ffffff';
const DASHBOARD_CHART_EXTENDED_FILL = 'rgba(100, 116, 139, 0.12)';
let completedCandleHeartbeatId = null;
let lastCompletedChartFetchAt = 0;
let lastCompletedChartBarTime = 0;
let lastCompletedChartAttemptedCloseTime = 0;
let lastCompletedChartAttemptAt = 0;
let activeChartRefreshInFlight = false;
let livePriceRequestSerial = 0;
let livePriceRequestInFlight = false;
let livePriceFailureCount = 0;
let livePriceNextAttemptAt = 0;
let chartIndexedDbPromise = null;
let legacyChartLocalStorageCleared = false;
let signalRefreshRequestSerial = 0;
let chartRangeUiFrame = null;
let chartOscSyncFrame = null;

const CHART_INTERVALS = {
    "30Sec": { label: "30s", seconds: 30, backend: "30Sec" },
    "1Min": { label: "1m", seconds: 60, backend: "1Min" },
    "2Min": { label: "2m", seconds: 120, backend: "2Min" },
    "3Min": { label: "3m", seconds: 180, backend: "3Min" },
    "5Min": { label: "5m", seconds: 300, backend: "5Min" },
    "10Min": { label: "10m", seconds: 600, backend: "10Min" },
    "15Min": { label: "15m", seconds: 900, backend: "15Min" },
    "30Min": { label: "30m", seconds: 1800, backend: "30Min" },
    "1Hour": { label: "1h", seconds: 3600, backend: "1Hour" },
    "2Hour": { label: "2h", seconds: 7200, backend: "2Hour" },
    "4Hour": { label: "4h", seconds: 14400, backend: "4Hour" },
    "1Day": { label: "1D", seconds: 86400, backend: "1Day" },
};

const CHART_RANGES = {
    "1D": { label: "1D", days: 1, preferredTf: "1Min" },
    "5D": { label: "5D", days: 5, preferredTf: "5Min" },
    "1M": { label: "1M", days: 31, preferredTf: "30Min" },
    "3M": { label: "3M", days: 93, preferredTf: "1Hour" },
    "6M": { label: "6M", days: 186, preferredTf: "4Hour" },
    "YTD": { label: "YTD", ytd: true, preferredTf: "4Hour" },
    "1Y": { label: "1Y", days: 366, preferredTf: "4Hour" },
    "5Y": { label: "5Y", days: 365 * 5, preferredTf: "1Day" },
    "MAX": { label: "Max", days: null, preferredTf: "1Day" },
};

function resetCompletedCandleState() {
    lastCompletedChartFetchAt = 0;
    lastCompletedChartBarTime = 0;
    lastCompletedChartAttemptedCloseTime = 0;
    lastCompletedChartAttemptAt = 0;
    updateLastBarInfo(window._confirmedDisplayPriceHistory || [], selectedTicker);
}

/**
 * Retrieves the current Firebase ID token and prepares headers.
 */
let developmentAuthTokenPromise = null;

async function getDevelopmentAuthHeaders(baseHeaders) {
    if (localStorage.getItem('dev_mode') !== 'true') return baseHeaders;
    try {
        if (!developmentAuthTokenPromise) {
            developmentAuthTokenPromise = fetch(`${API_BASE}/api/dev-token`, {
                cache: 'no-store',
                headers: { 'Cache-Control': 'no-cache' }
            }).then(async response => {
                const payload = await response.json().catch(() => ({}));
                if (!response.ok || typeof payload.token !== 'string' || !payload.token.trim()) {
                    throw new Error('Development authentication is unavailable');
                }
                return payload.token.trim();
            }).catch(error => {
                developmentAuthTokenPromise = null;
                throw error;
            });
        }
        const token = await developmentAuthTokenPromise;
        return { ...baseHeaders, 'Authorization': `Bearer ${token}` };
    } catch (error) {
        console.warn('[auth] Development authentication is unavailable:', error);
        return baseHeaders;
    }
}

async function getAuthHeaders() {
    const auth = window.auth;
    const baseHeaders = {
        'Content-Type': 'application/json',
        'Bypass-Tunnel-Reminder': 'true'
    };

    if (!auth) {
        console.warn('[auth] window.auth is NOT defined yet');
        return getDevelopmentAuthHeaders(baseHeaders);
    }
    const user = auth.currentUser;
    if (!user) {
        const developmentHeaders = await getDevelopmentAuthHeaders(baseHeaders);
        if (developmentHeaders !== baseHeaders) return developmentHeaders;
        console.warn('[auth] No current user found in auth instance');
        return baseHeaders;
    }

    const userId = String(user.uid || 'current-user');
    if (!authTokenRequestPromise || authTokenRequestUserId !== userId) {
        authTokenRequestUserId = userId;
        authTokenRequestPromise = Promise.resolve().then(() => user.getIdToken());
    }
    const tokenRequest = authTokenRequestPromise;

    try {
        const token = await tokenRequest;
        return {
            ...baseHeaders,
            'Authorization': `Bearer ${token}`
        };
    } catch (e) {
        console.error('[auth] Error getting ID token:', e);
        return baseHeaders;
    } finally {
        if (authTokenRequestPromise === tokenRequest) {
            authTokenRequestPromise = null;
            authTokenRequestUserId = null;
        }
    }
}

async function unlinkAlpacaDashboard() {
    if (!confirm("Are you sure you want to unlink your Alpaca account? The bot will switch back to simulation mode.")) return;

    try {
        const headers = await getAuthHeaders();
        const response = await fetch(`${API_BASE}/api/alpaca_config`, {
            method: 'DELETE',
            headers: headers
        });
        const data = await response.json();
        if (data.status === 'success') {
            alert(data.message);
            window.AlpacaStatusCache?.clear();
            window.AlpacaStatusCache?.render({ status: 'disconnected', has_keys: false, simulation: true, updatedAt: Date.now() });
            fetchDashboard('heavy');
        } else {
            alert("Error unlinking account.");
        }
    } catch (err) {
        console.error(err);
        alert("Backend communication error.");
    }
}
window.unlinkAlpacaDashboard = unlinkAlpacaDashboard;

function formatLocalTime(isoString) {
    if (!isoString || isoString === "Starting...") return isoString;
    try {
        // If the string is naive (no 'Z' and no offset like +00:00 or -05:00), 
        // append 'Z' to force the browser to treat it as UTC/GMT.
        let timestamp = isoString;
        if (isoString && !isoString.includes('Z') && !/[+-]\d{2}:\d{2}$/.test(isoString)) {
            timestamp += 'Z';
        }
        const date = new Date(timestamp);
        // Force Central Time (Chicago) for everywhere
        const dateStr = date.toLocaleDateString('en-US', {
            timeZone: 'America/Chicago',
            month: '2-digit', day: '2-digit', year: '2-digit'
        });
        const timeStr = date.toLocaleTimeString('en-US', {
            timeZone: 'America/Chicago',
            hour: '2-digit',
            minute: '2-digit',
            second: '2-digit',
            hour12: false
        });
        return `${dateStr} ${timeStr}`;
    } catch (e) {
        return isoString;
    }
}
// Tracked in consolidated block at top

// ──────────────────────────────────────────────
// 1. Chart Engine — Webull data + indicator overlays
// ──────────────────────────────────────────────
let lwChart = null;
let extHoursSeries = null;
let candleSeries = null;
let volumeSeries = null;
let oscChartRsi = null;
let oscChartMacd = null;
let oscChartMystic = null;
let oscChartAdx = null;

// All series objects keyed by indicator name
const _overlaySeries = {};
const _oscSeries = {};
const _overlayPriceLines = {};
const _overlayLabelMeta = {};
const _segmentedOverlaySeries = {};
const _horizontalOverlayPriceLines = {};
const _horizontalOverlayLabelMeta = {};
let dashboardVolumeProfileOverlay = null;
let dashboardVolumeProfilePayload = null;

// Which indicators are currently visible (toggled by toolbar)
const _defaultVisibleIndicators = {
    ema: false,
    vwap: false,
    auto_avwap: false,
    supertrend: false,
    bollinger: false,
    rsi: false,
    macd: false,
    mystic: false,
    sma: false,
    daily_high_low: false,
    pivot_points: false,
    volume_profile: false,
    swing_points: false,
    adx: false,
    'Candle Patterns': false,
};

const INDICATOR_STORE_VERSION = 'v4';

function loadIndicatorPrefs() {
    try {
        const storedVersion = localStorage.getItem('visibleIndicatorsVersion');
        if (storedVersion !== INDICATOR_STORE_VERSION) {
            // Fresh session or version mismatch — reset to all-off
            localStorage.removeItem('visibleIndicators');
            localStorage.removeItem('globalIndicatorPrefs');
            localStorage.setItem('visibleIndicatorsVersion', INDICATOR_STORE_VERSION);
            console.log('[indicators] Fresh session detected — all indicators reset to OFF.');
            return { ..._defaultVisibleIndicators };
        }
        return JSON.parse(localStorage.getItem('visibleIndicators')) || { ..._defaultVisibleIndicators };
    } catch (e) {
        return { ..._defaultVisibleIndicators };
    }
}

let _visibleIndicators = loadIndicatorPrefs();

const IND_COLORS = {
    ema_fast: '#3b82f6',
    ema_slow: '#ef4444',
    vwap: '#000000',
    auto_avwap: '#7c3aed',
    boll_upper: '#f97316', // Orange
    boll_middle: '#3b82f6', // Blue
    boll_lower: '#a855f7', // Purple
    rsi: '#f59e0b',
    macd_line: '#06b6d4',
    macd_signal: '#f97316', // Orange
    sma: '#8b5cf6', // Violet
    sma_secondary: '#64748b',
    adx: '#6366f1', // Indigo
    di_plus: '#10b981', // Green
    di_minus: '#ef4444', // Red
    daily_high_low_high: '#d97706',
    daily_high_low_low: '#0891b2',
    pivot_p: '#1f2937',
    pivot_r1: '#dc2626',
    pivot_s1: '#059669',
    pivot_r2: '#f97316',
    pivot_s2: '#22c55e',
    pivot_r_ladder: 'rgba(220, 38, 38, 0.78)',
    pivot_s_ladder: 'rgba(5, 150, 105, 0.78)',
    volume_profile_poc: '#7c2d12',
    volume_profile_vah: '#ea580c',
    volume_profile_val: '#0f766e',
    swing_support: '#16a34a',
    swing_resistance: '#dc2626',
};

function initChart() {
    const container = document.getElementById('tradingChart');
    if (!container) return;
    if (dashboardVolumeProfileOverlay) {
        try { dashboardVolumeProfileOverlay.destroy(); } catch (_) {}
        dashboardVolumeProfileOverlay = null;
    }
    container.innerHTML = '';
    container.style.setProperty('--dashboard-chart-bg', getDashboardChartBackground(selectedTicker));


    lwChart = LightweightCharts.createChart(container, {
        handleScroll: {
            mouseWheel: true,
            pressedMouseMove: true,
            horzTouchDrag: true,
            vertTouchDrag: true,
        },
        handleScale: {
            axisPressedMouseMove: true,
            mouseWheel: true,
            pinch: true,
        },
        layout: {
            background: { color: getDashboardChartBackground(selectedTicker) },
            textColor: '#334155',
            fontSize: getDashboardChartFontSize(),
        },
        grid: {
            vertLines: { color: 'rgba(168, 85, 247, 0.05)' },
            horzLines: { color: 'rgba(168, 85, 247, 0.05)' },
        },
        crosshair: { mode: LightweightCharts.CrosshairMode.Normal },
        rightPriceScale: {
            borderColor: 'rgba(197, 203, 206, 0.8)',
            minimumWidth: isDashboardMobileViewport() ? 48 : 64,
            scaleMargins: { top: 0.08, bottom: 0.08 },
        },
        localization: {
            timeFormatter: time => {
                return `${formatChartTime(time, false)} CT`;
            }
        },
        timeScale: {
            borderColor: 'rgba(197, 203, 206, 0.8)',
            timeVisible: true,
            secondsVisible: false,
            rightOffset: 12,
            tickMarkFormatter: (time, tickMarkType, locale) => {
                const d = new Date(time * 1000);
                // tickMarkType 2 is DayOfMonth, 1 is Month, 0 is Year. 
                // We show the date for these major boundaries.
                if (tickMarkType <= 2) {
                    return d.toLocaleDateString('en-US', { timeZone: 'America/Chicago', month: 'short', day: 'numeric' });
                }
                // Otherwise show HH:mm
                return d.toLocaleTimeString('en-US', { timeZone: 'America/Chicago', hour: '2-digit', minute: '2-digit', hour12: false });
            }
        },
    });

    extHoursSeries = lwChart.addHistogramSeries({
        color: DASHBOARD_CHART_EXTENDED_FILL,
        priceFormat: { type: 'custom', formatter: () => '' },
        priceScaleId: 'ext-hours-bg',
        scaleMargins: { top: 0, bottom: 0 },
        autoscaleInfoProvider: () => ({
            priceRange: { minValue: 0, maxValue: 100 },
        }),
    });
    lwChart.priceScale('ext-hours-bg').applyOptions({
        visible: false,
        scaleMargins: { top: 0, bottom: 0 },
    });
    applyDashboardChartBackground(selectedTicker);

    candleSeries = lwChart.addCandlestickSeries({
        upColor: '#10b981', downColor: '#ef4444',
        borderVisible: true,
        borderUpColor: '#10b981', borderDownColor: '#ef4444',
        wickUpColor: '#10b981', wickDownColor: '#ef4444',
        priceScaleId: 'right',
    });

    dashboardVolumeProfileOverlay = window.VolumeProfileOverlay?.attach({
        chart: lwChart,
        priceSeries: candleSeries,
        container,
        profile: dashboardVolumeProfilePayload,
        candles: window._displayPriceHistory || [],
        enabled: Boolean(_visibleIndicators.volume_profile),
        onProfileChange: (profile, enabled) => {
            if (!enabled || !profile || !Number.isFinite(profile.startTime) || !Number.isFinite(profile.endTime)) return;
            const visibleBars = (window._displayPriceHistory || []).filter(bar => (
                Number(bar.time) >= profile.startTime && Number(bar.time) <= profile.endTime
            ));
            const lineData = value => clean(visibleBars.map(bar => ({ time: bar.time, value })));
            _setLabeledLineData(_overlaySeries, 'volume_profile_poc', lwChart, lineData(profile.poc), {
                color: IND_COLORS.volume_profile_poc, lineWidth: 3, lineStyle: 0,
            }, 'POC');
            _setLabeledLineData(_overlaySeries, 'volume_profile_vah', lwChart, lineData(profile.vah), {
                color: IND_COLORS.volume_profile_vah, lineWidth: 2, lineStyle: 2,
            }, 'VAH');
            _setLabeledLineData(_overlaySeries, 'volume_profile_val', lwChart, lineData(profile.val), {
                color: IND_COLORS.volume_profile_val, lineWidth: 2, lineStyle: 2,
            }, 'VAL');
        },
    }) || null;

    volumeSeries = lwChart.addHistogramSeries({
        priceFormat: { type: 'volume' },
        priceScaleId: 'volume',
        scaleMargins: { top: 0.8, bottom: 0 },
    });
    lwChart.priceScale('volume').applyOptions({
        scaleMargins: { top: 0.8, bottom: 0 },
        visible: false,
    });

    let lastChartWidth = 0;
    const resizeObserver = new ResizeObserver(entries => {
        if (entries.length === 0 || !entries[0].contentRect) return;
        const { width, height } = entries[0].contentRect;
        lwChart.applyOptions({ width, height });
        applyDashboardChartBackground(selectedTicker);
        scheduleChartRangeUiRefresh();
        
        // Auto-fit content on significant width changes (e.g. going full screen)
        if (lastChartWidth > 0 && Math.abs(width - lastChartWidth) > 100) {
            if (window._displayPriceHistory && window._displayPriceHistory.length > 0) {
                setTimeout(() => {
                    try { lwChart.timeScale().fitContent(); } catch(e){}
                    applyDashboardChartBackground(selectedTicker);
                }, 150);
            }
        }
        lastChartWidth = width;
    });
    resizeObserver.observe(container);

    _initOscPane();
    lwChart.timeScale().subscribeVisibleTimeRangeChange(() => {
        applyDashboardChartBackground(selectedTicker);
        scheduleChartRangeUiRefresh();
    });
}

function _initOscPane() {
    // RSI Chart
    const rsiContainer = document.getElementById('oscChart_rsi');
    if (rsiContainer) {
        rsiContainer.innerHTML = '';
        oscChartRsi = LightweightCharts.createChart(rsiContainer, {
            layout: { background: { color: '#fafbff' }, textColor: '#64748b', fontSize: getDashboardChartFontSize() },
            grid: { vertLines: { color: 'rgba(168,85,247,0.04)' }, horzLines: { color: 'rgba(168,85,247,0.04)' } },
            rightPriceScale: { borderColor: 'rgba(197,203,206,0.6)', minimumWidth: isDashboardMobileViewport() ? 48 : 75 },
            localization: {
                timeFormatter: time => {
                    return `${formatChartTime(time, false)} CT`;
                }
            },
            timeScale: { visible: false },
        });
        const resizeRsi = new ResizeObserver(entries => {
            if (!entries[0] || !oscChartRsi) return;
            oscChartRsi.applyOptions({ 
                width: entries[0].contentRect.width,
                height: entries[0].contentRect.height,
                ...getDashboardSubchartResponsiveOptions(),
            });
        });
        resizeRsi.observe(rsiContainer);
    }

    // MACD Chart
    const macdContainer = document.getElementById('oscChart_macd');
    if (macdContainer) {
        macdContainer.innerHTML = '';
        oscChartMacd = LightweightCharts.createChart(macdContainer, {
            layout: { background: { color: '#fafbff' }, textColor: '#64748b', fontSize: getDashboardChartFontSize() },
            grid: { vertLines: { color: 'rgba(168,85,247,0.04)' }, horzLines: { color: 'rgba(168,85,247,0.04)' } },
            rightPriceScale: { borderColor: 'rgba(197,203,206,0.6)', minimumWidth: isDashboardMobileViewport() ? 48 : 75 },
            localization: {
                timeFormatter: time => {
                    return `${formatChartTime(time, false)} CT`;
                }
            },
            timeScale: { visible: false },
        });
        const resizeMacd = new ResizeObserver(entries => {
            if (!entries[0] || !oscChartMacd) return;
            oscChartMacd.applyOptions({ 
                width: entries[0].contentRect.width,
                height: entries[0].contentRect.height,
                ...getDashboardSubchartResponsiveOptions(),
            });
        });
        resizeMacd.observe(macdContainer);
    }

    // Mystic Pulse Chart
    const mysticContainer = document.getElementById('oscChart_mystic');
    if (mysticContainer) {
        mysticContainer.innerHTML = '';
        oscChartMystic = LightweightCharts.createChart(mysticContainer, {
            layout: { background: { color: '#fafbff' }, textColor: '#64748b', fontSize: getDashboardChartFontSize() },
            grid: { vertLines: { color: 'rgba(168,85,247,0.04)' }, horzLines: { color: 'rgba(168,85,247,0.04)' } },
            rightPriceScale: { borderColor: 'rgba(197,203,206,0.6)', minimumWidth: isDashboardMobileViewport() ? 48 : 75 },
            localization: {
                timeFormatter: time => {
                    return `${formatChartTime(time, false)} CT`;
                }
            },
            timeScale: { visible: false },
        });
        const resizeMystic = new ResizeObserver(entries => {
            if (!entries[0] || !oscChartMystic) return;
            oscChartMystic.applyOptions({ 
                width: entries[0].contentRect.width,
                height: entries[0].contentRect.height,
                ...getDashboardSubchartResponsiveOptions(),
            });
        });
        resizeMystic.observe(mysticContainer);
    }

    // ADX Chart
    const adxContainer = document.getElementById('oscChart_adx');
    if (adxContainer) {
        adxContainer.innerHTML = '';
        oscChartAdx = LightweightCharts.createChart(adxContainer, {
            layout: { background: { color: '#fafbff' }, textColor: '#64748b', fontSize: getDashboardChartFontSize() },
            grid: { vertLines: { color: 'rgba(168,85,247,0.04)' }, horzLines: { color: 'rgba(168,85,247,0.04)' } },
            rightPriceScale: { borderColor: 'rgba(197,203,206,0.6)', minimumWidth: isDashboardMobileViewport() ? 48 : 75 },
            localization: {
                timeFormatter: time => {
                    return `${formatChartTime(time, false)} CT`;
                }
            },
            timeScale: { visible: false },
        });
        const resizeAdx = new ResizeObserver(entries => {
            if (!entries[0] || !oscChartAdx) return;
            oscChartAdx.applyOptions({ 
                width: entries[0].contentRect.width,
                height: entries[0].contentRect.height,
                ...getDashboardSubchartResponsiveOptions(),
            });
        });
        resizeAdx.observe(adxContainer);
    }


    // Sync all time scales to main chart (guarded by visibility and wrapped in try/catch to prevent uncaught "Value is null" library errors)
    lwChart.timeScale().subscribeVisibleLogicalRangeChange(range => {
        scheduleOscillatorRangeSync(range);
    });
}

function updateChart(priceHistory, ticker, volumeProfile = undefined) {
    if (!lwChart || !candleSeries) initChart();

    if (volumeProfile !== undefined) dashboardVolumeProfilePayload = volumeProfile;

    updateActiveTickerDisplay(ticker);
    if (!priceHistory || priceHistory.length === 0) return;

    // Cache for instant re-draw on toggle
    window._lastPriceHistory = priceHistory;
    window._lastTicker = ticker;

    const rawSorted = [...priceHistory].sort((a, b) => a.time - b.time);
    const sorted = getDisplayBars(rawSorted, ticker);
    if (!sorted.length) {
        window._confirmedPriceHistory = rawSorted;
        window._confirmedDisplayPriceHistory = [];
        resetCompletedCandleState();
        updateLastBarInfo(rawSorted, ticker);
        updateChartControlState(ticker);
        return;
    }
    let shouldFollowRealtime = true;
    let logicalRange = null;
    try {
        const currentRange = lwChart.timeScale().getVisibleLogicalRange();
        if (currentRange && window._displayPriceHistory) {
            const oldLength = window._displayPriceHistory.length;
            if (oldLength > 0) {
                shouldFollowRealtime = currentRange.to >= oldLength - 3;
                if (!shouldFollowRealtime) {
                    const toIdx = Math.min(oldLength - 1, Math.max(0, Math.floor(currentRange.to)));
                    const toTime = window._displayPriceHistory[toIdx]?.time;
                    if (toTime) {
                        const newToIdx = sorted.findIndex(b => b.time === toTime);
                        if (newToIdx !== -1) {
                            const shift = newToIdx - toIdx;
                            logicalRange = { from: currentRange.from + shift, to: currentRange.to + shift };
                        } else {
                            logicalRange = currentRange;
                        }
                    } else {
                        logicalRange = currentRange;
                    }
                }
            }
        }
    } catch (e) {
        shouldFollowRealtime = true;
    }

    window._displayPriceHistory = sorted;
    window._confirmedPriceHistory = rawSorted;
    window._confirmedDisplayPriceHistory = sorted;
    // Scheduling is based on the provider's candle-open timestamp. The chart
    // may display a close-time label, but using that shifted value here would
    // delay the next completed-candle refresh by one full interval.
    const lastDisplayedBar = sorted[sorted.length - 1];
    lastCompletedChartBarTime = Number(lastDisplayedBar?.source_time ?? lastDisplayedBar?.time ?? 0);
    // Background highlight for Extended Hours
    if (extHoursSeries) {
        if (!isCryptoTicker(ticker) && currentChartSession === 'extended' && currentBackendTf !== '1Day') {
            const extData = sorted.map(b => {
                const isExt = !isRegularMarketBar(b);
                return {
                    time: b.time,
                    value: isExt ? 100 : 0,
                    color: isExt ? DASHBOARD_CHART_EXTENDED_FILL : 'rgba(0,0,0,0)'
                };
            });
            extHoursSeries.setData(extData);
        } else {
            extHoursSeries.setData([]);
        }
    }
    applyDashboardChartBackground(ticker);

    const chartCandles = sorted.map(b => ({
        time: b.time,
        open: b.open,
        high: b.high,
        low: b.low,
        close: b.close,
        volume: b.volume,
    }));
        const displayedChartCandles = _visibleIndicators['Candle Patterns']
        ? (window.StandardIndicatorSubcharts?.highlightCandlePatternContext?.(chartCandles, sorted, {
            rolling24Hours: isCryptoTicker(ticker),
            maxCandles: 20,
        }) || chartCandles)
        : chartCandles;
    candleSeries.setData(displayedChartCandles);
    if (volumeSeries) {
        volumeSeries.setData(sorted.map(b => ({
            time: b.time,
            value: Number(b.volume || 0),
            color: b.close >= b.open ? 'rgba(16, 185, 129, 0.45)' : 'rgba(239, 68, 68, 0.45)',
        })));
    }
    if (shouldFollowRealtime) {
        try { lwChart.timeScale().scrollToRealTime(); } catch (e) {}
    }

    // Crosshair OHLCV subscription (wire once)
    if (!window._crosshairWired) {
        window._crosshairWired = true;
        lwChart.subscribeCrosshairMove(param => {
            if (!param || !param.seriesData) return;
            const bar = param.seriesData.get(candleSeries);
            if (!bar) return;
            const fmtVol = v => v >= 1e6 ? `${(v / 1e6).toFixed(2)}M` : v >= 1e3 ? `${(v / 1e3).toFixed(1)}K` : v;
            document.getElementById('ohlcvO').textContent = formatPrice(bar.open);
            document.getElementById('ohlcvH').textContent = formatPrice(bar.high);
            document.getElementById('ohlcvL').textContent = formatPrice(bar.low);
            document.getElementById('ohlcvC').textContent = formatPrice(bar.close);
            const vEl = document.getElementById('ohlcvV');
            if (vEl) {
                const ts = param.time;
                const vBar = window._displayPriceHistory?.find(b => b.time === ts);
                const vol = (vBar && vBar.volume != null) ? vBar.volume : (bar && bar.volume != null ? bar.volume : null);
                vEl.textContent = (vol != null && !isNaN(vol)) ? fmtVol(vol) : '—';
            }
        });
    }

    updateChartControlState(ticker);
    updateLastBarInfo(sorted, ticker);

    _updateChartHeader(sorted);
    _refreshOverlays(sorted);
    _refreshOscillators(sorted);
    dashboardVolumeProfileOverlay?.setCandles(chartCandles);
    dashboardVolumeProfileOverlay?.setProfile(
        dashboardVolumeProfilePayload,
        Boolean(_visibleIndicators.volume_profile),
    );

    // Always maintain a comfortable zoom level for horizontal sliding
    const isNewTicker = window._lastFittedTicker !== ticker;
    const isNewRange = window._lastFittedRange !== currentChartRange;

    if (isNewTicker || isNewRange) {
        setTimeout(() => {
            lwChart.timeScale().fitContent();
        }, 150);
        window._lastFittedTicker = ticker;
        window._lastFittedRange = currentChartRange;
    } else if (!shouldFollowRealtime && logicalRange !== null) {
        try { lwChart.timeScale().setVisibleLogicalRange(logicalRange); } catch(e) {}
    }
    applyDashboardChartBackground(ticker);

    updateVisibleHighLow();
    const visibleRange = lwChart.timeScale().getVisibleLogicalRange();
    syncOscillatorRanges(visibleRange);

    if (typeof setChartLoadingState === 'function') {
        setChartLoadingState(false);
    }
}

function _getOrCreate(map, key, chartInst, options) {
    if (!map[key]) map[key] = chartInst.addLineSeries(options);
    else if (typeof map[key].applyOptions === 'function') map[key].applyOptions(options);
    return map[key];
}
function _removeSeries(map, key, chartInst) {
    if (_overlayPriceLines[key] && map[key]) {
        try { map[key].removePriceLine(_overlayPriceLines[key]); } catch (e) { }
        delete _overlayPriceLines[key];
    }
    delete _overlayLabelMeta[key];
    if (map[key]) { try { chartInst.removeSeries(map[key]); } catch (e) { } delete map[key]; }
}

function _rightVisiblePointForData(data, chartInst) {
    if (!Array.isArray(data) || data.length === 0) return null;
    const isValuePoint = point => Number.isFinite(Number(point?.value));
    let from = NaN;
    let to = NaN;
    try {
        const range = chartInst?.timeScale?.().getVisibleRange?.();
        from = Number(range?.from);
        to = Number(range?.to);
    } catch (e) { }
    if (Number.isFinite(to)) {
        for (let i = data.length - 1; i >= 0; i -= 1) {
            const pointTime = Number(data[i]?.time);
            if (isValuePoint(data[i]) && pointTime <= to && (!Number.isFinite(from) || pointTime >= from)) {
                return data[i];
            }
        }
        for (let i = data.length - 1; i >= 0; i -= 1) {
            if (isValuePoint(data[i]) && Number(data[i]?.time) <= to) return data[i];
        }
    }
    for (let i = data.length - 1; i >= 0; i -= 1) {
        if (isValuePoint(data[i])) return data[i];
    }
    return null;
}

function _refreshOverlayPriceLineLabel(key) {
    const meta = _overlayLabelMeta[key];
    if (!meta) return;
    const series = meta.map[key];
    if (!series) return;
    if (_overlayPriceLines[key]) {
        try { series.removePriceLine(_overlayPriceLines[key]); } catch (e) { }
        delete _overlayPriceLines[key];
    }
    const point = _rightVisiblePointForData(meta.data, meta.chartInst);
    const price = Number(point?.value);
    if (Number.isFinite(price) && meta.labelTitle) {
        _overlayPriceLines[key] = series.createPriceLine({
            price,
            color: meta.options.color,
            lineWidth: 1,
            lineStyle: meta.options.lineStyle ?? 0,
            axisLabelVisible: true,
            lineVisible: false,
            title: meta.labelTitle,
        });
    }
}

function _refreshOverlayPriceLineLabels() {
    Object.keys(_overlayLabelMeta).forEach(_refreshOverlayPriceLineLabel);
    Object.keys(_horizontalOverlayLabelMeta).forEach(_refreshHorizontalOverlayPriceLineLabel);
}

function _setLabeledLineData(map, key, chartInst, data, options, labelTitle) {
    const series = _getOrCreate(map, key, chartInst, {
        priceLineVisible: false,
        lastValueVisible: false,
        crosshairMarkerVisible: false,
        ...options,
    });
    series.setData(data);
    _overlayLabelMeta[key] = { map, chartInst, data, options, labelTitle };
    _refreshOverlayPriceLineLabel(key);
    return series;
}

function _removeSegmentedLabeledLineData(key, chartInst) {
    (_segmentedOverlaySeries[key] || []).forEach(segmentKey => {
        _removeSeries(_overlaySeries, segmentKey, chartInst);
    });
    delete _segmentedOverlaySeries[key];
    delete _horizontalOverlayLabelMeta[key];
    if (_horizontalOverlayPriceLines[key] && candleSeries) {
        try { candleSeries.removePriceLine(_horizontalOverlayPriceLines[key]); } catch (e) {}
    }
    delete _horizontalOverlayPriceLines[key];
}

function _refreshHorizontalOverlayPriceLineLabel(key) {
    const meta = _horizontalOverlayLabelMeta[key];
    if (!meta || !candleSeries) return;
    if (_horizontalOverlayPriceLines[key]) {
        try { candleSeries.removePriceLine(_horizontalOverlayPriceLines[key]); } catch (e) {}
        delete _horizontalOverlayPriceLines[key];
    }
    const point = _rightVisiblePointForData(meta.data, lwChart);
    const price = Number(point?.value);
    if (!Number.isFinite(price) || !meta.labelTitle) return;
    _horizontalOverlayPriceLines[key] = candleSeries.createPriceLine({
        price,
        color: meta.options.color,
        lineWidth: meta.options.lineWidth || 2,
        lineStyle: meta.options.lineStyle ?? 0,
        axisLabelVisible: true,
        lineVisible: true,
        title: meta.labelTitle,
    });
}

function _setSegmentedLabeledLineData(key, chartInst, data, options, labelTitle) {
    _removeSegmentedLabeledLineData(key, chartInst);
    // Swing levels are rolling zones, not a time-series value that should be
    // connected across every candle. Render the level selected at the right
    // side of the visible range as a true horizontal price line instead.
    _segmentedOverlaySeries[key] = [];
    _horizontalOverlayLabelMeta[key] = { data, options, labelTitle };
    _refreshHorizontalOverlayPriceLineLabel(key);
}

function clean(arr) {
    const seen = new Set();
    return arr.filter(p => {
        if (p.value == null || isNaN(p.value)) return false;
        if (p.time == null || isNaN(p.time)) return false;
        if (seen.has(p.time)) return false;
        seen.add(p.time);
        return true;
    }).sort((a, b) => a.time - b.time);
}

function segmentedLevelData(bars, key) {
    const points = clean((bars || [])
        .filter(bar => bar && bar[key] != null)
        .map(bar => ({ time: Number(bar.time), value: Number(bar[key]) })));
    if (points.length < 2) return points;

    const segmented = [points[0]];
    let previous = points[0];
    for (let i = 1; i < points.length; i += 1) {
        const point = points[i];
        if (Math.abs(Number(point.value) - Number(previous.value)) > 1e-9) {
            // A whitespace point breaks the line before the new horizontal
            // zone, preventing Lightweight Charts from drawing a diagonal
            // connector between unrelated support/resistance prices.
            const breakTime = Number(point.time) - 1;
            if (breakTime > Number(previous.time)) segmented.push({ time: breakTime });
        }
        segmented.push(point);
        previous = point;
    }
    return segmented;
}

function _refreshOverlays(bars) {
    const activeMarkers = [];

    // EMA
    if (_visibleIndicators.ema) {
        _getOrCreate(_overlaySeries, 'ema_fast', lwChart, { color: IND_COLORS.ema_fast, lineWidth: 1.5 })
            .setData(clean(bars.filter(b => b.ema_fast != null).map(b => ({ time: b.time, value: b.ema_fast }))));
        if (indicatorParamValue('EMA_USE_SECOND_LINE', 1) !== 0) {
            _getOrCreate(_overlaySeries, 'ema_slow', lwChart, { color: IND_COLORS.ema_slow, lineWidth: 1.5 })
                .setData(clean(bars.filter(b => b.ema_slow != null).map(b => ({ time: b.time, value: b.ema_slow }))));
        } else {
            _removeSeries(_overlaySeries, 'ema_slow', lwChart);
        }
    } else {
        _removeSeries(_overlaySeries, 'ema_fast', lwChart);
        _removeSeries(_overlaySeries, 'ema_slow', lwChart);
    }
    // Session VWAP
    if (_visibleIndicators.vwap) {
        _getOrCreate(_overlaySeries, 'vwap', lwChart, { color: IND_COLORS.vwap || '#000000', lineWidth: 2, lineStyle: 0 })
            .setData(clean(bars.filter(b => b.vwap != null).map(b => ({ time: b.time, value: b.vwap }))));
    } else {
        _removeSeries(_overlaySeries, 'vwap', lwChart);
    }
    // SMA
    if (_visibleIndicators.sma) {
        _getOrCreate(_overlaySeries, 'sma', lwChart, { color: IND_COLORS.sma, lineWidth: 2.5, lineStyle: 0 })
            .setData(clean(bars.filter(b => b.sma != null).map(b => ({ time: b.time, value: b.sma }))));
        if (indicatorParamValue('SMA_USE_SECOND_LINE', 1) !== 0) {
            _getOrCreate(_overlaySeries, 'sma_secondary', lwChart, { color: IND_COLORS.sma_secondary, lineWidth: 2, lineStyle: 2 })
                .setData(clean(bars.filter(b => b.sma_secondary != null).map(b => ({ time: b.time, value: b.sma_secondary }))));
        } else {
            _removeSeries(_overlaySeries, 'sma_secondary', lwChart);
        }
    } else {
        _removeSeries(_overlaySeries, 'sma', lwChart);
        _removeSeries(_overlaySeries, 'sma_secondary', lwChart);
    }
    // Supertrend (Continuous multi-color line)
    if (_visibleIndicators.supertrend) {
        const stData = [];

        for (let i = 0; i < bars.length; i++) {
            const b = bars[i];
            if (b.supertrend == null) continue;

            stData.push({
                time: b.time,
                value: b.supertrend,
                color: b.supertrend_up ? '#10b981' : '#ef4444'
            });
        }


        _getOrCreate(_overlaySeries, 'st_line', lwChart, {
            lineWidth: 2,
            lineType: 0 // Straight candle-to-candle line; avoids curved Supertrend distortion.
        }).setData(clean(stData));

        // Clean up old multi-series if they exist
        _removeSeries(_overlaySeries, 'st_bull', lwChart);
        _removeSeries(_overlaySeries, 'st_bear', lwChart);

        // Trend Reversal Markers (Arrows)
        for (let i = 1; i < bars.length; i++) {
            const curr = bars[i];
            const prev = bars[i - 1];
            if (curr.supertrend == null || prev.supertrend == null) continue;

            if (curr.supertrend_up && !prev.supertrend_up) {
                // Flip to BULLISH (Buy)
                activeMarkers.push({
                    time: curr.time,
                    position: 'belowBar',
                    color: '#10b981',
                    shape: 'arrowUp',
                    text: 'ST Buy',
                    size: 1
                });
            } else if (!curr.supertrend_up && prev.supertrend_up) {
                // Flip to BEARISH (Sell)
                activeMarkers.push({
                    time: curr.time,
                    position: 'aboveBar',
                    color: '#ef4444',
                    shape: 'arrowDown',
                    text: 'ST Sell',
                    size: 1
                });
            }
        }
    } else {
        _removeSeries(_overlaySeries, 'st_bull', lwChart);
        _removeSeries(_overlaySeries, 'st_bear', lwChart);
        _removeSeries(_overlaySeries, 'st_line', lwChart);
    }
    // Bollinger Bands
    if (_visibleIndicators.bollinger) {
        _getOrCreate(_overlaySeries, 'boll_upper', lwChart, { color: IND_COLORS.boll_upper, lineWidth: 1.5 }).setData(clean(bars.filter(b => b.boll_upper != null).map(b => ({ time: b.time, value: b.boll_upper }))));
        _getOrCreate(_overlaySeries, 'boll_middle', lwChart, { color: IND_COLORS.boll_middle, lineWidth: 1, lineStyle: 2 }).setData(clean(bars.filter(b => b.boll_middle != null).map(b => ({ time: b.time, value: b.boll_middle }))));
        _getOrCreate(_overlaySeries, 'boll_lower', lwChart, { color: IND_COLORS.boll_lower, lineWidth: 1.5 }).setData(clean(bars.filter(b => b.boll_lower != null).map(b => ({ time: b.time, value: b.boll_lower }))));
    } else {
        _removeSeries(_overlaySeries, 'boll_upper', lwChart);
        _removeSeries(_overlaySeries, 'boll_middle', lwChart);
        _removeSeries(_overlaySeries, 'boll_lower', lwChart);
    }

    // Prior Day High/Low — prior session structure lines
    if (_visibleIndicators.daily_high_low) {
        _setLabeledLineData(_overlaySeries, 'daily_high_low_high', lwChart, clean(bars.filter(b => b.daily_high_low_high != null).map(b => ({ time: b.time, value: b.daily_high_low_high }))), {
            color: IND_COLORS.daily_high_low_high,
            lineWidth: 3,
            lineStyle: 1,
        }, 'PDH');
        _setLabeledLineData(_overlaySeries, 'daily_high_low_low', lwChart, clean(bars.filter(b => b.daily_high_low_low != null).map(b => ({ time: b.time, value: b.daily_high_low_low }))), {
            color: IND_COLORS.daily_high_low_low,
            lineWidth: 3,
            lineStyle: 1,
        }, 'PDL');
        _getOrCreate(_overlaySeries, 'daily_high_low_high_trigger', lwChart, {
            color: 'rgba(217, 119, 6, 0.75)',
            lineWidth: 2,
            lineStyle: 1,
        }).setData(clean(bars.filter(b => b.daily_high_low_high_trigger != null).map(b => ({ time: b.time, value: b.daily_high_low_high_trigger }))));
        _getOrCreate(_overlaySeries, 'daily_high_low_low_trigger', lwChart, {
            color: 'rgba(8, 145, 178, 0.75)',
            lineWidth: 2,
            lineStyle: 1,
        }).setData(clean(bars.filter(b => b.daily_high_low_low_trigger != null).map(b => ({ time: b.time, value: b.daily_high_low_low_trigger }))));
    } else {
        _removeSeries(_overlaySeries, 'daily_high_low_high', lwChart);
        _removeSeries(_overlaySeries, 'daily_high_low_low', lwChart);
        _removeSeries(_overlaySeries, 'daily_high_low_high_trigger', lwChart);
        _removeSeries(_overlaySeries, 'daily_high_low_low_trigger', lwChart);
    }

    // Pivot Points (S/R) — prior-session floor pivot structure
    if (_visibleIndicators.pivot_points) {
        const pivotLevelCount = Math.min(5, Math.max(1, Math.round(indicatorParamValue('PIVOT_LEVEL_COUNT', 1))));
        _setLabeledLineData(_overlaySeries, 'pivot_p', lwChart, clean(bars.filter(b => b.pivot_p != null).map(b => ({ time: b.time, value: b.pivot_p }))), {
            color: IND_COLORS.pivot_p,
            lineWidth: 3,
            lineStyle: 2,
        }, 'P');
        for (let level = 1; level <= 5; level += 1) {
            const active = level <= pivotLevelCount;
            const rKey = `pivot_r${level}`;
            const sKey = `pivot_s${level}`;
            if (active) {
                _setLabeledLineData(_overlaySeries, rKey, lwChart, clean(bars.filter(b => b[rKey] != null).map(b => ({ time: b.time, value: b[rKey] }))), {
                    color: level === 1 ? IND_COLORS.pivot_r1 : IND_COLORS.pivot_r_ladder,
                    lineWidth: 2,
                    lineStyle: level === 1 ? 1 : 2,
                }, `R${level}`);
                _setLabeledLineData(_overlaySeries, sKey, lwChart, clean(bars.filter(b => b[sKey] != null).map(b => ({ time: b.time, value: b[sKey] }))), {
                    color: level === 1 ? IND_COLORS.pivot_s1 : IND_COLORS.pivot_s_ladder,
                    lineWidth: 2,
                    lineStyle: level === 1 ? 1 : 2,
                }, `S${level}`);
            } else {
                _removeSeries(_overlaySeries, rKey, lwChart);
                _removeSeries(_overlaySeries, sKey, lwChart);
            }
        }
    } else {
        _removeSeries(_overlaySeries, 'pivot_p', lwChart);
        for (let level = 1; level <= 5; level += 1) {
            _removeSeries(_overlaySeries, `pivot_r${level}`, lwChart);
            _removeSeries(_overlaySeries, `pivot_s${level}`, lwChart);
        }
    }

    // Swing Points — rolling support/resistance clusters
    if (_visibleIndicators.swing_points) {
        const swingLevelCount = Math.min(5, Math.max(1, Math.round(indicatorParamValue('SWING_POINTS_LEVEL_COUNT', 1))));
        for (let level = 1; level <= 5; level += 1) {
            const active = level <= swingLevelCount;
            const sKey = `swing_support_${level}`;
            const rKey = `swing_resistance_${level}`;
            if (active) {
                _setSegmentedLabeledLineData(sKey, lwChart, segmentedLevelData(bars, sKey), {
                    color: level === 1 ? IND_COLORS.swing_support : 'rgba(22, 163, 74, 0.72)',
                    lineWidth: level === 1 ? 3 : 2,
                    lineStyle: level === 1 ? 1 : 2,
                }, `SW S${level}`);
                _setSegmentedLabeledLineData(rKey, lwChart, segmentedLevelData(bars, rKey), {
                    color: level === 1 ? IND_COLORS.swing_resistance : 'rgba(220, 38, 38, 0.72)',
                    lineWidth: level === 1 ? 3 : 2,
                    lineStyle: level === 1 ? 1 : 2,
                }, `SW R${level}`);
            } else {
                _removeSegmentedLabeledLineData(sKey, lwChart);
                _removeSegmentedLabeledLineData(rKey, lwChart);
            }
        }
    } else {
        for (let level = 1; level <= 5; level += 1) {
            _removeSegmentedLabeledLineData(`swing_support_${level}`, lwChart);
            _removeSegmentedLabeledLineData(`swing_resistance_${level}`, lwChart);
        }
    }

    // Volume Profile — server levels remain available for signal context;
    // the VPVR overlay itself uses the current visible candle range.
    if (_visibleIndicators.volume_profile) {
        _setLabeledLineData(_overlaySeries, 'volume_profile_poc', lwChart, clean(bars.filter(b => b.volume_profile_poc != null).map(b => ({ time: b.time, value: b.volume_profile_poc }))), {
            color: IND_COLORS.volume_profile_poc,
            lineWidth: 3,
            lineStyle: 0,
        }, 'POC');
        _setLabeledLineData(_overlaySeries, 'volume_profile_vah', lwChart, clean(bars.filter(b => b.volume_profile_vah != null).map(b => ({ time: b.time, value: b.volume_profile_vah }))), {
            color: IND_COLORS.volume_profile_vah,
            lineWidth: 2,
            lineStyle: 2,
        }, 'VAH');
        _setLabeledLineData(_overlaySeries, 'volume_profile_val', lwChart, clean(bars.filter(b => b.volume_profile_val != null).map(b => ({ time: b.time, value: b.volume_profile_val }))), {
            color: IND_COLORS.volume_profile_val,
            lineWidth: 2,
            lineStyle: 2,
        }, 'VAL');
    } else {
        _removeSeries(_overlaySeries, 'volume_profile_poc', lwChart);
        _removeSeries(_overlaySeries, 'volume_profile_vah', lwChart);
        _removeSeries(_overlaySeries, 'volume_profile_val', lwChart);
    }

    // Candle Patterns Markers
    if (_visibleIndicators['Candle Patterns']) {
        const candlePatternMarkers = window.StandardIndicatorSubcharts?.buildCandlePatternMarkers?.(bars, {
            rolling24Hours: isCryptoTicker(selectedTicker),
            maxCandles: 20,
        }) || [];
        activeMarkers.push(...candlePatternMarkers);
    }

    const dashboardIndicatorPrefs = getGlobalIndicatorPrefs();
    if (dashboardIndicatorPrefs['Chart Patterns'] === true && isDashboardIndicatorAllowed('Chart Patterns')) {
        activeMarkers.push(...(window.StandardIndicatorSubcharts?.buildChartPatternMarkers?.(bars, {
            rolling24Hours: isCryptoTicker(selectedTicker),
            maxCandles: 20,
        }) || []));
    }
    const flipIndicatorNames = new Set((window.STANDARD_EXTENSION_OVERLAYS || [])
        .filter(definition => definition.flipMarkers && dashboardIndicatorPrefs[definition.name] === true
            && isDashboardIndicatorAllowed(definition.name))
        .map(definition => definition.name));
    if (flipIndicatorNames.size) {
        activeMarkers.push(...(window.StandardIndicatorSubcharts?.buildFlipMarkers?.(bars, flipIndicatorNames) || []));
    }
    (window.STANDARD_EXTENSION_OVERLAYS || []).forEach(definition => {
        const show = dashboardIndicatorPrefs[definition.name] === true
            && isDashboardIndicatorAllowed(definition.name);
        definition.series.forEach(spec => {
            const seriesKey = `standard_${spec.field}`;
            if (show) {
                const overlayParameters = spec.shiftPeriodsKey ? {
                    [spec.shiftPeriodsKey]: indicatorParamValue(spec.shiftPeriodsKey, spec.shiftPeriodsFallback || 0),
                } : {};
                const overlayData = window.StandardIndicatorSubcharts?.buildOverlayData
                    ? window.StandardIndicatorSubcharts.buildOverlayData(bars, spec, overlayParameters)
                    : clean(bars
                        .filter(bar => bar?.[spec.field] != null)
                        .map(bar => ({ time: bar.time, value: Number(bar[spec.field]) })));
                _getOrCreate(_overlaySeries, seriesKey, lwChart, {
                    color: spec.color,
                    lineWidth: spec.lineWidth || 1,
                    lineStyle: spec.lineStyle || 0,
                    lineVisible: spec.lineVisible !== false,
                    pointMarkersVisible: spec.pointMarkersVisible === true,
                    pointMarkersRadius: spec.pointMarkersRadius || 2,
                    priceLineVisible: false,
                    lastValueVisible: false,
                    crosshairMarkerVisible: false,
                }).setData(overlayData);
            } else {
                _removeSeries(_overlaySeries, seriesKey, lwChart);
            }
        });
    });
    // Apply merged markers
    activeMarkers.sort((a, b) => a.time - b.time);
    candleSeries.setMarkers(activeMarkers);
}

function _refreshOscillators(bars) {
    // Handle RSI
    const rsiContainer = document.getElementById('oscChart_rsi');
    const rsiLabel = document.getElementById('rsiLabel');
    if (rsiContainer) {
        const wasHidden = rsiContainer.style.display === 'none' || getComputedStyle(rsiContainer).display === 'none';
        rsiContainer.style.display = _visibleIndicators.rsi ? 'block' : 'none';
        if (rsiLabel) rsiLabel.style.display = _visibleIndicators.rsi ? 'block' : 'none';
        if (wasHidden && _visibleIndicators.rsi && oscChartRsi) {
            oscChartRsi.applyOptions({ width: rsiContainer.clientWidth, height: rsiContainer.clientHeight });
        }
    }
    if (_visibleIndicators.rsi && oscChartRsi) {
        _getOrCreate(_oscSeries, 'rsi', oscChartRsi, { color: IND_COLORS.rsi, lineWidth: 1 })
            .setData(clean(bars.filter(b => b.rsi != null).map(b => ({ time: b.time, value: b.rsi }))));
        const ob = _getOrCreate(_oscSeries, 'rsi_ob', oscChartRsi, { color: 'rgba(239,68,68,0.4)', lineWidth: 1, lineStyle: 2 });
        const os = _getOrCreate(_oscSeries, 'rsi_os', oscChartRsi, { color: 'rgba(16,185,129,0.4)', lineWidth: 1, lineStyle: 2 });
        const rsiPeriod = indicatorParamValue('RSI_PERIOD', 14);
        const rsiFirstThreshold = indicatorParamValue('RSI_OVERBOUGHT', 70);
        const rsiSecondThreshold = indicatorParamValue('RSI_OVERSOLD', 30);
        const obVal = Math.max(rsiFirstThreshold, rsiSecondThreshold);
        const osVal = Math.min(rsiFirstThreshold, rsiSecondThreshold);
        if (rsiLabel) {
            rsiLabel.textContent = `RSI (${rsiPeriod})  •  OB: ${obVal} / OS: ${osVal}`;
        }
        if (bars.length) {
            ob.setData(clean([{ time: bars[0].time, value: obVal }, { time: bars[bars.length - 1].time, value: obVal }]));
            os.setData(clean([{ time: bars[0].time, value: osVal }, { time: bars[bars.length - 1].time, value: osVal }]));
        }
        try { oscChartRsi.timeScale().fitContent(); } catch (e) { }
    } else if (oscChartRsi) {
        _removeSeries(_oscSeries, 'rsi', oscChartRsi);
        _removeSeries(_oscSeries, 'rsi_ob', oscChartRsi);
        _removeSeries(_oscSeries, 'rsi_os', oscChartRsi);
    }

    // Handle MACD
    const macdContainer = document.getElementById('oscChart_macd');
    const macdLabel = document.getElementById('macdLabel');
    if (macdContainer) {
        const wasHidden = macdContainer.style.display === 'none' || getComputedStyle(macdContainer).display === 'none';
        macdContainer.style.display = _visibleIndicators.macd ? 'block' : 'none';
        if (macdLabel) macdLabel.style.display = _visibleIndicators.macd ? 'flex' : 'none';
        if (wasHidden && _visibleIndicators.macd && oscChartMacd) {
            oscChartMacd.applyOptions({ width: macdContainer.clientWidth, height: macdContainer.clientHeight });
        }
    }
    if (_visibleIndicators.macd && oscChartMacd) {
        const macdFirst = indicatorParamValue('MACD_FAST', 12);
        const macdSecond = indicatorParamValue('MACD_SLOW', 26);
        const macdFast = Math.min(macdFirst, macdSecond);
        const macdSlow = Math.max(macdFirst, macdSecond);
        const macdSig = indicatorParamValue('MACD_SIGNAL', 9);
        if (macdLabel) {
            macdLabel.textContent = `MACD (${macdFast}, ${macdSlow}, ${macdSig})`;
        }
        // Histogram
        if (!_oscSeries['macd_hist']) {
            _oscSeries['macd_hist'] = oscChartMacd.addHistogramSeries({
                base: 0,
                priceFormat: { type: 'price', precision: 2, minMove: 0.01 }
            });
        }
        const histData = bars.filter(b => b.macd_hist != null).map(b => ({
            time: b.time,
            value: b.macd_hist,
            color: b.macd_hist >= 0 ? 'rgba(16, 185, 129, 0.5)' : 'rgba(239, 68, 68, 0.5)'
        }));
        _oscSeries['macd_hist'].setData(clean(histData));

        _getOrCreate(_oscSeries, 'macd_line', oscChartMacd, { 
            color: IND_COLORS.macd_line, 
            lineWidth: 1,
            priceFormat: { type: 'price', precision: 2, minMove: 0.01 }
        }).setData(clean(bars.filter(b => b.macd_line != null).map(b => ({ time: b.time, value: b.macd_line }))));
        
        _getOrCreate(_oscSeries, 'macd_signal', oscChartMacd, { 
            color: IND_COLORS.macd_signal, 
            lineWidth: 1,
            priceFormat: { type: 'price', precision: 2, minMove: 0.01 }
        }).setData(clean(bars.filter(b => b.macd_signal != null).map(b => ({ time: b.time, value: b.macd_signal }))));
        
        try { oscChartMacd.timeScale().fitContent(); } catch (e) { }
    } else if (oscChartMacd) {
        _removeSeries(_oscSeries, 'macd_hist', oscChartMacd);
        _removeSeries(_oscSeries, 'macd_line', oscChartMacd);
        _removeSeries(_oscSeries, 'macd_signal', oscChartMacd);
    }

    // Handle Mystic Pulse
    const mysticContainer = document.getElementById('oscChart_mystic');
    const mysticLabel = document.getElementById('mysticPulseLabel');
    if (mysticContainer) {
        const wasHidden = mysticContainer.style.display === 'none' || getComputedStyle(mysticContainer).display === 'none';
        mysticContainer.style.display = _visibleIndicators.mystic ? 'block' : 'none';
        if (mysticLabel) mysticLabel.style.display = _visibleIndicators.mystic ? 'flex' : 'none';
        if (wasHidden && _visibleIndicators.mystic && oscChartMystic) {
            oscChartMystic.applyOptions({ width: mysticContainer.clientWidth, height: mysticContainer.clientHeight });
        }
    }
    if (_visibleIndicators.mystic && oscChartMystic) {
        const mysticThresh = indicatorParamValue('MYSTIC_PULSE_THRESHOLD', 5);
        if (mysticLabel) {
            mysticLabel.textContent = `Mystic Pulse  •  Threshold: ${mysticThresh}`;
        }
        if (!_oscSeries['mystic_hist']) {
            _oscSeries['mystic_hist'] = oscChartMystic.addHistogramSeries({
                base: 0,
                priceScaleId: '',
            });
        }
        const histData = bars.filter(b => b.mystic_bull != null || b.mystic_bear != null).map(b => {
            let val = 0;
            let col = 'rgba(168,85,247,0.2)'; // neutral
            if (b.mystic_bull > 0) {
                val = b.mystic_bull;
                col = b.mystic_bull >= mysticThresh ? 'rgba(16, 185, 129, 0.9)' : 'rgba(16, 185, 129, 0.4)';
            } else if (b.mystic_bear > 0) {
                val = -b.mystic_bear;
                col = b.mystic_bear >= mysticThresh ? 'rgba(239, 68, 68, 0.9)' : 'rgba(239, 68, 68, 0.4)';
            }
            return { time: b.time, value: val, color: col };
        });
        _oscSeries['mystic_hist'].setData(clean(histData));

        const ob = _getOrCreate(_oscSeries, 'mystic_ob', oscChartMystic, { color: 'rgba(16,185,129,0.35)', lineWidth: 1, lineStyle: 2 });
        const os = _getOrCreate(_oscSeries, 'mystic_os', oscChartMystic, { color: 'rgba(239,68,68,0.35)', lineWidth: 1, lineStyle: 2 });
        if (bars.length) {
            ob.setData(clean([{ time: bars[0].time, value: mysticThresh }, { time: bars[bars.length - 1].time, value: mysticThresh }]));
            os.setData(clean([{ time: bars[0].time, value: -mysticThresh }, { time: bars[bars.length - 1].time, value: -mysticThresh }]));
        }
        try { oscChartMystic.timeScale().fitContent(); } catch (e) { }
    } else if (oscChartMystic) {
        _removeSeries(_oscSeries, 'mystic_hist', oscChartMystic);
    }

    // Handle ADX
    const adxContainer = document.getElementById('oscChart_adx');
    const adxLabel = document.getElementById('adxLabel');
    if (adxContainer) {
        const wasHidden = adxContainer.style.display === 'none' || getComputedStyle(adxContainer).display === 'none';
        adxContainer.style.display = _visibleIndicators.adx ? 'block' : 'none';
        if (adxLabel) adxLabel.style.display = _visibleIndicators.adx ? 'flex' : 'none';
        if (wasHidden && _visibleIndicators.adx && oscChartAdx) {
            oscChartAdx.applyOptions({ width: adxContainer.clientWidth, height: adxContainer.clientHeight });
        }
    }
    if (_visibleIndicators.adx && oscChartAdx) {
        const adxPeriod = indicatorParamValue('ADX_PERIOD', 14);
        const adxThreshVal = indicatorParamValue('ADX_TRENDING_THRESHOLD', 25);
        if (adxLabel) {
            adxLabel.textContent = `ADX (${adxPeriod}) · strong ≥ ${adxThreshVal} · +DI green · −DI red`;
        }
        _getOrCreate(_oscSeries, 'adx', oscChartAdx, { color: IND_COLORS.adx, lineWidth: 2 })
            .setData(clean(bars.filter(b => b.adx != null).map(b => ({ time: b.time, value: b.adx }))));
        _getOrCreate(_oscSeries, 'di_plus', oscChartAdx, { color: IND_COLORS.di_plus, lineWidth: 1 })
            .setData(clean(bars.filter(b => b.di_plus != null).map(b => ({ time: b.time, value: b.di_plus }))));
        _getOrCreate(_oscSeries, 'di_minus', oscChartAdx, { color: IND_COLORS.di_minus, lineWidth: 1 })
            .setData(clean(bars.filter(b => b.di_minus != null).map(b => ({ time: b.time, value: b.di_minus }))));

        // Threshold line at 25 (trending vs choppy)
        const adxThreshold = _getOrCreate(_oscSeries, 'adx_threshold', oscChartAdx, { color: 'rgba(99,102,241,0.25)', lineWidth: 1, lineStyle: 2 });
        adxThreshold.setData(clean(bars.map(b => ({ time: b.time, value: adxThreshVal }))));

        try { oscChartAdx.timeScale().fitContent(); } catch (e) { }
    } else if (oscChartAdx) {
        _removeSeries(_oscSeries, 'adx', oscChartAdx);
        _removeSeries(_oscSeries, 'di_plus', oscChartAdx);
        _removeSeries(_oscSeries, 'di_minus', oscChartAdx);
        _removeSeries(_oscSeries, 'adx_threshold', oscChartAdx);
    }

    const dashboardPrefs = getGlobalIndicatorPrefs();
    const dashboardSubchartParameters = Object.fromEntries(
        Object.entries(window.STANDARD_INDICATOR_DEFAULTS || {}).map(([key, fallback]) => [
            key,
            indicatorParamValue(key, fallback),
        ])
    );
    let dashboardVisibleRange = null;
    try { dashboardVisibleRange = lwChart?.timeScale().getVisibleLogicalRange() || null; } catch (e) {}
    window.StandardIndicatorSubcharts?.render({
        context: 'dashboard',
        hostId: 'dashboardDynamicOscillators',
        history: bars,
        enabledIndicators: Object.keys(dashboardPrefs).filter(name => (
            dashboardPrefs[name] === true && isDashboardIndicatorAllowed(name)
        )),
        parameters: dashboardSubchartParameters,
        height: 120,
        visibleRange: dashboardVisibleRange,
    });
}

// Called by toolbar buttons
function toggleChartIndicator(key) {
    _visibleIndicators[key] = !_visibleIndicators[key];
    const isEnabled = !!_visibleIndicators[key];
    localStorage.setItem('visibleIndicators', JSON.stringify(_visibleIndicators));
    const btn = document.getElementById(`ind_btn_${key}`);
    if (btn) {
        btn.classList.toggle('ind-btn-active', isEnabled);
        btn.classList.toggle('ind-btn-inactive', !isEnabled);
    }
    if (isEnabled && typeof SIGNAL_TO_CHART_KEY !== 'undefined') {
        const signalEntry = Object.entries(SIGNAL_TO_CHART_KEY).find(([, chartKey]) => chartKey === key);
        if (signalEntry) {
            setGlobalIndicatorPref(signalEntry[0], true, { refreshChart: true });
            return;
        }
    }
    if (isEnabled && typeof loadSelectedTickerChart === 'function') {
        loadSelectedTickerChart({ force: true, renderCached: false });
        return;
    }
    // Hiding an indicator can redraw immediately from cache.
    if (window._lastPriceHistory) updateChart(window._lastPriceHistory, window._lastTicker || selectedTicker);
}

function syncChart() {
    if (selectedTicker) {
        if (isCryptoTicker(selectedTicker)) currentChartSession = 'crypto24';
        else if (currentChartSession === 'crypto24') currentChartSession = 'regular';
        updateActiveTickerDisplay(selectedTicker);
        updateChartControlState(selectedTicker);
        _updateStarState();
    }
}

function chartCacheKey(ticker, timeframe = currentBackendTf, session = currentChartSession) {
    return `${normalizeTicker(ticker)}|${timeframe}|${session}|${CHART_CACHE_VERSION}`;
}

function clearLegacyChartLocalStorageCaches() {
    if (legacyChartLocalStorageCleared) return;
    legacyChartLocalStorageCleared = true;
    try {
        const keysToRemove = [];
        for (let i = 0; i < localStorage.length; i++) {
            const key = localStorage.key(i);
            if (key && key.startsWith('chart_cache_')) keysToRemove.push(key);
        }
        keysToRemove.forEach(key => localStorage.removeItem(key));
    } catch (e) {
        console.warn('[chart] Could not clear legacy localStorage chart cache:', e);
    }
}

function openChartIndexedDB() {
    if (typeof indexedDB === 'undefined') return Promise.resolve(null);
    if (chartIndexedDbPromise) return chartIndexedDbPromise;
    chartIndexedDbPromise = new Promise(resolve => {
        const request = indexedDB.open(CHART_IDB_NAME, CHART_IDB_VERSION);
        request.onupgradeneeded = event => {
            const db = event.target.result;
            if (!db.objectStoreNames.contains(CHART_IDB_STORE)) {
                const store = db.createObjectStore(CHART_IDB_STORE, { keyPath: 'key' });
                store.createIndex('fetchedAt', 'fetchedAt', { unique: false });
            }
        };
        request.onsuccess = event => resolve(event.target.result);
        request.onerror = () => {
            console.warn('[chart] IndexedDB chart cache unavailable:', request.error);
            resolve(null);
        };
        request.onblocked = () => {
            console.warn('[chart] IndexedDB chart cache upgrade blocked by another tab.');
            resolve(null);
        };
    });
    return chartIndexedDbPromise;
}

async function readChartCacheFromIndexedDB(key) {
    const db = await openChartIndexedDB();
    if (!db) return null;
    return new Promise(resolve => {
        try {
            const tx = db.transaction(CHART_IDB_STORE, 'readonly');
            const store = tx.objectStore(CHART_IDB_STORE);
            const request = store.get(key);
            request.onsuccess = () => resolve(request.result || null);
            request.onerror = () => {
                console.warn('[chart] Failed to read chart cache from IndexedDB:', request.error);
                resolve(null);
            };
        } catch (e) {
            console.warn('[chart] Failed to open IndexedDB read transaction:', e);
            resolve(null);
        }
    });
}

async function pruneChartIndexedDB(maxEntries = 30) {
    const db = await openChartIndexedDB();
    if (!db) return;
    return new Promise(resolve => {
        try {
            const tx = db.transaction(CHART_IDB_STORE, 'readwrite');
            const store = tx.objectStore(CHART_IDB_STORE);
            const request = store.getAll();
            request.onsuccess = () => {
                const rows = (Array.isArray(request.result) ? request.result : [])
                    .filter(row => !String(row?.key || '').startsWith('bot-preview|'));
                rows
                    .sort((a, b) => Number(b.fetchedAt || 0) - Number(a.fetchedAt || 0))
                    .slice(maxEntries)
                    .forEach(row => {
                        if (row?.key) store.delete(row.key);
                    });
            };
            tx.oncomplete = () => resolve();
            tx.onerror = () => resolve();
            tx.onabort = () => resolve();
        } catch (e) {
            resolve();
        }
    });
}

async function saveChartCacheToIndexedDB(key, wrapped) {
    const db = await openChartIndexedDB();
    if (!db) return false;
    const record = { ...wrapped, key, cacheType: 'dashboard-chart' };
    const writeRecord = () => new Promise((resolve, reject) => {
        try {
            const tx = db.transaction(CHART_IDB_STORE, 'readwrite');
            tx.objectStore(CHART_IDB_STORE).put(record);
            tx.oncomplete = () => resolve(true);
            tx.onerror = () => reject(tx.error);
            tx.onabort = () => reject(tx.error);
        } catch (e) {
            reject(e);
        }
    });
    try {
        await writeRecord();
        pruneChartIndexedDB().catch(() => {});
        clearLegacyChartLocalStorageCaches();
        return true;
    } catch (e) {
        await pruneChartIndexedDB(10);
        try {
            await writeRecord();
            clearLegacyChartLocalStorageCaches();
            return true;
        } catch (retryErr) {
            console.warn('[chart] IndexedDB chart cache write skipped:', retryErr);
            return false;
        }
    }
}

/* Time chip in the Indicator Signal Confluence header: when these signals were last refreshed (Central Time, like the
 * rest of the Dashboard). The tooltip adds the completed candle they are based on when the payload carries it. Stamped
 * only when a fresh payload is applied, not when the grid is merely redrawn (for example by an indicator toggle). */
/* Countdown to the next automatic refresh of the chart and its indicator signals. It mirrors refreshCompletedCandlesIfDue():
 * the refresh is due once the current candle has closed plus COMPLETED_CANDLE_DELAY_MS, a missing candle is retried every
 * COMPLETED_CANDLE_RETRY_MS, and there is no automatic refresh for daily charts or outside the chart session. */
function formatRefreshCountdown(totalSeconds) {
    const seconds = Math.max(0, Math.ceil(totalSeconds));
    const hours = Math.floor(seconds / 3600);
    const minutes = Math.floor((seconds % 3600) / 60);
    const rest = seconds % 60;
    if (hours > 0) return `${hours}h${minutes}m`;
    if (minutes > 0) return `${minutes}m${rest}s`;
    return `${rest}s`;
}

function signalRefreshCountdownText(nowMs = Date.now()) {
    const intervalSeconds = CHART_INTERVALS[currentBackendTf]?.seconds;
    if (!intervalSeconds) return '—';
    if (intervalSeconds >= 86400) return 'Auto-refresh off (1D)';
    if (!dashboardChartRefreshSessionIsOpen(nowMs)) return 'Market closed';
    const lastBar = Number(lastCompletedChartBarTime || 0);
    if (!(lastBar > 0)) return '—';
    const expectedCloseSeconds = lastBar + intervalSeconds;
    const dueAt = expectedCloseSeconds * 1000 + COMPLETED_CANDLE_DELAY_MS;
    if (dueAt > nowMs) return `Next refresh in ${formatRefreshCountdown((dueAt - nowMs) / 1000)}`;
    if (activeChartRefreshInFlight) return 'Refreshing…';
    // Due, but the new candle has not arrived: the next attempt is a retry.
    if (lastCompletedChartAttemptedCloseTime === expectedCloseSeconds) {
        const retryAt = lastCompletedChartAttemptAt + COMPLETED_CANDLE_RETRY_MS;
        if (retryAt > nowMs) return `Refreshing in ${formatRefreshCountdown((retryAt - nowMs) / 1000)}`;
    }
    return 'Refreshing…';
}

function tickSignalRefreshCountdown() {
    const el = document.getElementById('signalRefreshCountdown');
    if (!el || document.hidden) return;
    const text = signalRefreshCountdownText();
    const label = `⏳ ${text}`;
    if (el.textContent !== label) el.textContent = label;
}

function startSignalRefreshCountdown() {
    if (window._signalRefreshCountdownTimer) return;
    window._signalRefreshCountdownTimer = setInterval(tickSignalRefreshCountdown, 1000);
    tickSignalRefreshCountdown();
}

function stampSignalUpdatedTime(scan) {
    // The countdown badge must keep running even though the "last refreshed" clock is no longer on the page.
    startSignalRefreshCountdown();
    tickSignalRefreshCountdown();
    const el = document.getElementById('signalUpdatedTime');
    if (!el) return;
    const clock = (date, seconds) => date.toLocaleTimeString('en-US', {
        timeZone: 'America/Chicago', hour: 'numeric', minute: '2-digit', ...(seconds ? { second: '2-digit' } : {}), hour12: true,
    });
    const now = new Date();
    el.textContent = `🕒 ${clock(now, true)} CT`;
    let closeTs = Number(scan?.candle_meta?.candle_close_ts);
    if (Number.isFinite(closeTs) && closeTs > 0) {
        if (closeTs > 1e11) closeTs /= 1000;
        const label = CHART_INTERVALS[currentBackendTf]?.label || currentBackendTf;
        el.title = `Signals last refreshed ${clock(now, true)} CT. Calculated on the completed ${label} candle that closed ${clock(new Date(closeTs * 1000), false)} CT. They refresh about 15 seconds after each candle closes.`;
    } else {
        el.title = `Signals last refreshed ${clock(now, true)} CT. They refresh about 15 seconds after each candle closes.`;
    }
}

function renderFastChartPayload(data, requestedTicker) {
    const ticker = normalizeTicker(requestedTicker || data?.ticker || data?.primaryTicker);
    if (!ticker || normalizeTicker(selectedTicker) !== ticker) return;

    const payload = data?.data || data;
    const history = payload?.priceHistory || payload?.price_history || payload?.primaryScan?.price_history || [];
    const volumeProfile = payload?.volumeProfile ?? payload?.volume_profile;
    if (volumeProfile !== undefined) dashboardVolumeProfilePayload = volumeProfile;
    if (history.length > 0) {
        // A chart-overlay failure must not prevent the confluence grid from
        // receiving the same payload. The grid is the primary signal surface
        // and should be able to report degraded chart data independently.
        try {
            updateChart(history, ticker, volumeProfile);
        } catch (error) {
            console.error('[chart] Overlay render failed; continuing with signal confluence:', error);
        }
    }

    const scan = payload?.primaryScan || payload;
    const signals = scan?.signals || payload?.signals || {};
    const hasSignals = signals && Object.keys(signals).length > 0;
    window._lastSignals = signals || {};
    window._lastSignalsTicker = ticker;
    // Always resolve the loading state after a successful chart response.
    // An empty signal map is degraded data, not a reason to leave an
    // indefinite spinner over the panel.
    // renderSignals() drops renders while an indicator-save lock is held (up to 15s). On a slower
    // backend the chart response lands inside that window, was dropped, and left the spinner on
    // until the next candle. Local indicator prefs already override backend flags, so a fresh
    // chart payload is safe to render regardless of the lock.
    const previousRenderAllowed = window._indicatorRefreshRenderAllowed;
    window._indicatorRefreshRenderAllowed = true;
    try {
        renderSignals(
            signals,
            scan.action || payload.action || 'HOLD',
            hasSignals
                ? (scan.reason || payload.reason || '')
                : `No indicator signal values were returned for ${ticker}. Refresh to retry.`,
            scan.bullish_count ?? payload.bullish_count ?? 0,
            scan.bearish_count ?? payload.bearish_count ?? 0,
            { allowEmpty: true }
        );
    } finally {
        window._indicatorRefreshRenderAllowed = previousRenderAllowed;
    }
    updateSizingPanel(scan);
    if (hasSignals) stampSignalUpdatedTime(scan);

    if (window.lastDashboardData) {
        window.lastDashboardData.primaryTicker = ticker;
        window.lastDashboardData.primaryScan = scan;
        window.lastDashboardData.signals = signals;
        window.lastDashboardData.priceHistory = history;
    }
}

async function refreshActiveTickerSignals(options = {}) {
    const { force = true, showLoading = true } = options;
    const requestedTicker = normalizeTicker(selectedTicker);
    if (!requestedTicker || !shouldRunDashboardRefresh()) return;

    const isAuthReady = window.auth && (window.auth.currentUser || localStorage.getItem('dev_mode') === 'true');
    if (!isAuthReady) return;

    if (showLoading) {
        lockSignalConfluencePanelHeight();
        setSignalConfluenceLoadingState(true);
    }

    const requestId = ++signalRefreshRequestSerial;
    try {
        const headers = await getAuthHeaders();
        const url = `${API_BASE}/api/signals/${encodeURIComponent(requestedTicker)}?timeframe=${encodeURIComponent(currentBackendTf)}&source=dashboard&force=${force ? 'true' : 'false'}&session=${encodeURIComponent(currentChartSession)}`;
        const response = await fetchWithTimeout(url, { headers }, 12000);
        if (!response.ok) {
            console.error(`[signals] HTTP Error: ${response.status} - ${await response.text()}`);
            return;
        }
        const data = await response.json();
        if (requestId !== signalRefreshRequestSerial || normalizeTicker(selectedTicker) !== requestedTicker) return;
        renderFastChartPayload(data, requestedTicker);
    } catch (e) {
        console.error('[signals] Refresh failed:', e);
    }
}

async function loadSelectedTickerChart(options = {}) {
    return loadTickerChartFast(selectedTicker, options);
}

async function loadTickerChartFast(ticker, options = {}) {
    const requestedTicker = normalizeTicker(ticker);
    if (!requestedTicker) return;

    // Cached-only renders must remain synchronous with the UI and do not need
    // coalescing. Network refreshes share a key for the active chart context
    // and refresh class. Forced refreshes stay separate from ordinary cache
    // refreshes so a force request can never be satisfied by a stale request.
    if (options.fetchFresh === false) {
        return _loadTickerChartFastImpl(requestedTicker, options);
    }
    const refreshClass = options.force ? 'force' : 'normal';
    const requestKey = `${chartCacheKey(requestedTicker, currentBackendTf, currentChartSession)}|fresh|${refreshClass}`;
    const existing = chartRequestInFlight.get(requestKey);
    if (existing) return existing;

    const request = _loadTickerChartFastImpl(requestedTicker, options);
    chartRequestInFlight.set(requestKey, request);
    try {
        return await request;
    } finally {
        if (chartRequestInFlight.get(requestKey) === request) {
            chartRequestInFlight.delete(requestKey);
        }
    }
}

async function _loadTickerChartFastImpl(ticker, options = {}) {
    const requestedTicker = normalizeTicker(ticker);
    if (!requestedTicker) return;

    let { force = false, renderCached = true, fetchFresh = true } = options;
    const chartContext = readDashboardChartContext();
    const isForceRefresh = chartContext && chartContext.forceRefresh && normalizeTicker(chartContext.ticker) === requestedTicker;
    if (isForceRefresh) {
        force = true;
        renderCached = false;
        fetchFresh = true;
    }

    const key = chartCacheKey(requestedTicker);
    let cached = chartDataCache.get(key);

    // Fallback to IndexedDB if not found in memory.
    if (!cached && renderCached) {
        const idbData = await readChartCacheFromIndexedDB(key);
        if (idbData) {
            if (idbData?.fetchedAt && (Date.now() - idbData.fetchedAt) > CHART_CACHE_TTL_MS) {
                cached = null;
            } else {
                cached = idbData;
                chartDataCache.set(key, cached);
            }
        }
    }

    if (renderCached && cached) {
        renderFastChartPayload(cached, requestedTicker);
    } else {
        lockSignalConfluencePanelHeight();
        setSignalConfluenceLoadingState(true);
    }

    if (!fetchFresh) return;
    if (!shouldRunDashboardRefresh()) return;

    // Check if auth is ready before fetching fresh data
    const isAuthReady = window.auth && (window.auth.currentUser || localStorage.getItem('dev_mode') === 'true');
    if (!isAuthReady) {
        console.log('[chart] Auth not ready yet. Skipping network fetch until auth resolves.');
        return;
    }

    if (isForceRefresh) {
        try {
            delete chartContext.forceRefresh;
            sessionStorage.setItem(DASHBOARD_CHART_CONTEXT_KEY, JSON.stringify(chartContext));
        } catch (e) {}
    }

    const requestId = ++chartRequestSerial;
    lastCompletedChartFetchAt = Date.now();
    try {
        const headers = await getAuthHeaders();
        const chartSource = getChartSourceForTicker(requestedTicker);
        const url = `${API_BASE}/api/chart/${encodeURIComponent(requestedTicker)}?timeframe=${encodeURIComponent(currentBackendTf)}&force=${force ? 'true' : 'false'}&source=${encodeURIComponent(chartSource)}&session=${encodeURIComponent(currentChartSession)}`;
        const response = await fetchWithTimeout(url, { headers }, CHART_FETCH_TIMEOUT_MS);
        if (!response.ok) {
            console.error(`[chart] HTTP Error: ${response.status} - ${await response.text()}`);
            if (requestId === chartRequestSerial) showSignalConfluenceFetchError(`Server returned ${response.status}`);
            return;
        }
        const data = await response.json();
        if (requestId !== chartRequestSerial || normalizeTicker(selectedTicker) !== requestedTicker) return;
        lastCompletedChartFetchAt = Date.now();
        const wrapped = {
            data,
            fetchedAt: Date.now(),
            version: CHART_CACHE_VERSION,
            timeframe: currentBackendTf,
            ticker: requestedTicker
        };
        chartDataCache.set(key, wrapped);
        saveChartCacheToIndexedDB(key, wrapped).catch(e => {
            console.warn('[chart] IndexedDB chart cache save failed:', e);
        });
        renderFastChartPayload(data, requestedTicker);
        return data;
    } catch (e) {
        setChartLoadingState(false);
        if (requestId === chartRequestSerial && !isRequestAbort(e)) {
            showSignalConfluenceFetchError(isRequestTimeout(e) ? 'Request timed out' : 'Network error');
        }
        if (isRequestTimeout(e) || isRequestAbort(e)) {
            console.warn(`[chart] Refresh skipped: ${e.message || e.name}`);
        } else {
            console.error('[chart] Fast chart load failed:', e);
        }
        return null;
    }
}

async function refreshCompletedCandlesIfDue() {
    if (!shouldRunDashboardRefresh() || activeChartRefreshInFlight) return;
    const intervalSeconds = CHART_INTERVALS[currentBackendTf]?.seconds;
    if (!intervalSeconds || intervalSeconds >= 86400) return;
    if (!dashboardChartRefreshSessionIsOpen()) return;
    if (Date.now() - lastCompletedChartFetchAt < COMPLETED_CANDLE_HEARTBEAT_MS) return;

    const now = Date.now();
    const lastClosedBarTime = Number(lastCompletedChartBarTime || 0);
    // Do not align to UTC hour boundaries. Equity intraday bars begin at the
    // 08:30 CT open, so a 1-hour bar closes at :30. The prior floor-to-hour
    // logic repeatedly requested a non-existent :00 candle during RTH.
    const expectedNextCloseTime = lastClosedBarTime > 0
        ? lastClosedBarTime + intervalSeconds
        : Math.floor(((now - COMPLETED_CANDLE_DELAY_MS) / 1000) / intervalSeconds) * intervalSeconds;
    if (expectedNextCloseTime <= 0) return;
    if ((now - COMPLETED_CANDLE_DELAY_MS) / 1000 < expectedNextCloseTime) return;

    const retryingSameMissingBar = lastCompletedChartAttemptedCloseTime === expectedNextCloseTime;
    if (retryingSameMissingBar && now - lastCompletedChartAttemptAt < COMPLETED_CANDLE_RETRY_MS) return;
    if (lastCompletedChartAttemptedCloseTime > expectedNextCloseTime) return;

    activeChartRefreshInFlight = true;
    lastCompletedChartAttemptedCloseTime = expectedNextCloseTime;
    lastCompletedChartAttemptAt = now;
    try {
        await loadSelectedTickerChart({ force: true, renderCached: false, fetchFresh: true });
    } finally {
        activeChartRefreshInFlight = false;
    }
}

/* Chart header: the price is always the latest live quote and the change is the day change from the previous
 * regular-session close. Neither depends on the chart timeframe or on how many candles are loaded (the old header
 * measured from the first loaded candle, so switching timeframe changed the percentage). */
const HEADER_QUOTE_FRESH_MS = 120000;
const HEADER_PREV_CLOSE_KEEP_MS = 6 * 60 * 60 * 1000;
window._headerQuote = window._headerQuote || null;          // { ticker, price, at }
window._headerPrevClose = window._headerPrevClose || {};    // ticker -> { value, at }  (changes once a day)

function freshHeaderQuote() {
    const q = window._headerQuote;
    if (!q || q.ticker !== normalizeTicker(selectedTicker)) return null;
    return Date.now() - q.at <= HEADER_QUOTE_FRESH_MS ? q : null;
}

window._headerPrevCloseFromBars = window._headerPrevCloseFromBars || {};   // fallback, same shape as _headerPrevClose

function headerPreviousClose() {
    const ticker = normalizeTicker(selectedTicker);
    const usable = (entry) => entry && Date.now() - entry.at <= HEADER_PREV_CLOSE_KEEP_MS && entry.value > 0;
    // The server's previous close wins; the candle-derived one only fills in when the quote did not carry it.
    if (usable(window._headerPrevClose[ticker])) return window._headerPrevClose[ticker].value;
    if (usable(window._headerPrevCloseFromBars[ticker])) return window._headerPrevCloseFromBars[ticker].value;
    return 0;
}

const _chartCtDateFormatter = new Intl.DateTimeFormat('en-CA', { timeZone: 'America/Chicago', year: 'numeric', month: '2-digit', day: '2-digit' });
function chartBarDateKey(bar, ticker) {
    const t = Number(Number.isFinite(Number(bar?.source_time)) ? bar.source_time : bar?.time);
    if (!Number.isFinite(t)) return '';
    const d = new Date(t * 1000);
    return isCryptoTicker(ticker) ? d.toISOString().slice(0, 10) : _chartCtDateFormatter.format(d);
}

/* Previous regular-session close from candles already loaded, for when the quote has none. For intraday charts it is
 * the close of the last regular-hours candle on an earlier trading date than the newest candle; for daily candles it is
 * the candle before the latest session. Returns 0 when the loaded candles do not reach a previous session (for example a
 * single-day range), and the caller keeps whatever it already knew. */
function previousCloseFromBars(bars) {
    if (!Array.isArray(bars) || bars.length < 2) return 0;
    const ticker = normalizeTicker(selectedTicker);
    const last = bars[bars.length - 1];
    const lastKey = chartBarDateKey(last, ticker);
    if (!lastKey) return 0;
    if (currentBackendTf === '1Day') {
        const prior = bars[bars.length - 2];
        return chartBarDateKey(prior, ticker) < lastKey && Number(prior.close) > 0 ? Number(prior.close) : 0;
    }
    for (let i = bars.length - 1; i >= 0; i -= 1) {
        const key = chartBarDateKey(bars[i], ticker);
        if (!key || key >= lastKey) continue;
        if (isCryptoTicker(ticker) || isRegularMarketBar(bars[i])) {
            return Number(bars[i].close) > 0 ? Number(bars[i].close) : 0;
        }
    }
    return 0;
}

function paintChartHeader(price) {
    const priceEl = document.getElementById('chartPrice');
    const changeEl = document.getElementById('chartPriceChange');
    if (!priceEl || !Number.isFinite(price) || price <= 0) return;
    const prev = headerPreviousClose();
    const known = prev > 0;
    const change = known ? price - prev : 0;
    const changePct = known ? (change / prev) * 100 : 0;

    priceEl.textContent = formatPrice(price, selectedTicker);
    priceEl.className = `text-xl font-black ${!known ? 'text-gray-700' : (change >= 0 ? 'text-emerald-600' : 'text-red-500')}`;
    if (changeEl) {
        if (known) {
            const sign = change > 0 ? '+' : (change < 0 ? '-' : '');
            const diffStr = formatPrice(Math.abs(change), selectedTicker);
            const pctSign = changePct > 0 ? '+' : (changePct < 0 ? '-' : '');
            changeEl.textContent = `${sign}${diffStr} (${pctSign}${Math.abs(changePct).toFixed(2)}%)`;
            changeEl.className = `text-xs font-bold ${change >= 0 ? 'text-emerald-500' : 'text-red-500'}`;
        } else {
            // No previous close yet: show no change rather than a window-dependent one.
            changeEl.textContent = '';
        }
    }
}

function updateChartHeaderLivePrice(price, quote) {
    if (!Number.isFinite(price) || price <= 0) return;
    const ticker = normalizeTicker(selectedTicker);
    const prev = Number(quote && quote.previous_close);
    if (Number.isFinite(prev) && prev > 0) window._headerPrevClose[ticker] = { value: prev, at: Date.now() };
    window._headerQuote = { ticker, price, at: Date.now() };
    paintChartHeader(price);
}

async function fetchLivePriceForActiveTicker() {
    const requestedTicker = normalizeTicker(selectedTicker);
    if (!requestedTicker || !shouldRunDashboardRefresh()) return;
    if (livePriceRequestInFlight || Date.now() < livePriceNextAttemptAt) return;
    if (dashboardIndicatorSaveInFlight || dashboardIndicatorSaveTimer) return;

    const isAuthReady = window.auth && (window.auth.currentUser || localStorage.getItem('dev_mode') === 'true');
    if (!isAuthReady) return;

    const requestId = ++livePriceRequestSerial;
    livePriceRequestInFlight = true;
    try {
        const headers = await getAuthHeaders();
        const response = await fetchWithTimeout(`${API_BASE}/api/quote/${encodeURIComponent(requestedTicker)}`, { headers }, QUOTE_FETCH_TIMEOUT_MS);
        if (!response.ok) {
            livePriceFailureCount += 1;
            livePriceNextAttemptAt = Date.now() + Math.min(60000, 5000 * (2 ** Math.min(livePriceFailureCount - 1, 4)));
            return;
        }

        const quote = await response.json();
        if (requestId !== livePriceRequestSerial || normalizeTicker(selectedTicker) !== requestedTicker) return;
        if (quote.stale) return;

        const price = Number(quote.price);
        updateChartHeaderLivePrice(price, quote);
        livePriceFailureCount = 0;
        livePriceNextAttemptAt = 0;
    } catch (e) {
        livePriceFailureCount += 1;
        const retryDelay = Math.min(60000, 5000 * (2 ** Math.min(livePriceFailureCount - 1, 4)));
        livePriceNextAttemptAt = Date.now() + retryDelay;
        if (!isRequestTimeout(e) && !isRequestAbort(e)) {
            console.warn('[chart] Live price update failed:', e);
        }
    } finally {
        livePriceRequestInFlight = false;
    }
}

function startActiveChartHeartbeat() {
    if (completedCandleHeartbeatId) clearInterval(completedCandleHeartbeatId);
    const tick = () => {
        if (!shouldRunDashboardRefresh()) return;
        fetchLivePriceForActiveTicker();
        refreshCompletedCandlesIfDue();
    };
    tick();
    completedCandleHeartbeatId = setInterval(tick, LIVE_PRICE_HEARTBEAT_MS);
}

// ──────────────────────────────────────────────
// 1b. Chart header: price + change label
// ──────────────────────────────────────────────
function _updateChartHeader(bars) {
    if (!bars || bars.length === 0) return;
    // Switching timeframe loads different candles, but the header must not change with them: use the latest live quote
    // for this ticker when there is one, and the last candle's close only until the first quote arrives.
    const quote = freshHeaderQuote();
    const price = quote ? quote.price : bars[bars.length - 1].close;
    const ticker = normalizeTicker(selectedTicker);
    if (!window._headerPrevClose[ticker]) {
        const derived = previousCloseFromBars(bars);
        if (derived > 0) window._headerPrevCloseFromBars[ticker] = { value: derived, at: Date.now() };
    }
    paintChartHeader(price);
    if (!quote) fetchLivePriceForActiveTicker();
}

function setChartLoadingState(isLoading, ticker = '') {
    const overlay = document.getElementById('chartLoadingOverlay');
    const loadingText = document.getElementById('chartLoadingText');
    if (!overlay) return;
    if (isLoading) {
        const displayTicker = ticker || selectedTicker || '';
        const normalized = displayTicker ? normalizeTicker(displayTicker) : '';
        if (loadingText) {
            loadingText.textContent = normalized ? `Loading ${normalized} Chart...` : 'Loading Chart...';
        }
        overlay.classList.remove('hidden');
        overlay.classList.add('flex');
    } else {
        overlay.classList.add('hidden');
        overlay.classList.remove('flex');
    }
}

function clearChartVisuals() {
    if (candleSeries) candleSeries.setData([]);
    if (volumeSeries) volumeSeries.setData([]);
    if (extHoursSeries) extHoursSeries.setData([]);
    if (_overlaySeries) Object.values(_overlaySeries).forEach(s => s && typeof s.setData === 'function' && s.setData([]));
    if (_oscSeries) Object.values(_oscSeries).forEach(s => s && typeof s.setData === 'function' && s.setData([]));
    
    setChartLoadingState(true);

    const priceEl = document.getElementById('chartPrice');
    const changeEl = document.getElementById('chartPriceChange');
    if (priceEl) {
        priceEl.innerText = 'Loading...';
        priceEl.className = 'text-xl font-black text-gray-400';
    }
    if (changeEl) {
        changeEl.innerText = '';
        changeEl.className = 'text-xs font-bold text-gray-500';
    }
}

function showTickerChangeLoadingState(ticker, options = {}) {
    const normalized = normalizeTicker(ticker);
    if (!normalized) return;

    setChartLoadingState(true, normalized);
    updateActiveTickerDisplay(normalized);
    window._lastSignals = null;
    window._lastSignalsTicker = normalized;
    livePriceRequestSerial++;
    chartRequestSerial++;
    _aiIndicatorFetchId++;
    updateSizingPanel(null);

    if (!options.keepChart) {
        clearChartVisuals();
    } else {
        const priceEl = document.getElementById('chartPrice');
        const changeEl = document.getElementById('chartPriceChange');
        if (priceEl) {
            priceEl.innerText = 'Refreshing...';
            priceEl.className = 'text-xl font-black text-gray-400';
        }
        if (changeEl) {
            changeEl.innerText = '';
            changeEl.className = 'text-xs font-bold text-gray-500';
        }
    }

    const grid = document.getElementById('signalGrid');
    const loading = document.getElementById('gridLoading');
    if (grid) {
        lockSignalConfluencePanelHeight();
        renderSignals({}, 'HOLD', `Loading ${normalized} indicator signals...`, 0, 0, { allowEmpty: true });
    }
    setSignalConfluenceLoadingState(true);

    const summaryEl = document.getElementById('aiSummary');
    const cardSentSummary = document.getElementById('sentimentSummary');
    const aiHeaderEl = document.getElementById('aiIndicatorHeader');
    if (aiHeaderEl) aiHeaderEl.textContent = `BotBulls News AI — ${normalized}`;
    if (summaryEl) {
        summaryEl.textContent = `Fetching latest scan analysis for ${normalized}...`;
        summaryEl.classList.add('italic');
    }
    if (cardSentSummary) cardSentSummary.textContent = `Fetching latest news for ${normalized}...`;
    _showAIIndicatorLoading(normalized);
}

// ──────────────────────────────────────────────
// 1c. Timeframe button bar
// ──────────────────────────────────────────────
function setChartTimeframe(tf) {
    currentBackendTf = CHART_INTERVALS[tf]?.backend || tf;
    localStorage.setItem('lastChartTf', currentBackendTf);
    resetCompletedCandleState();
    setChartLoadingState(true, selectedTicker);

    // If they choose a very small timeframe, check if the current range is too large.
    const smallTfs = ["1Min", "5Min", "15Min", "30Min"];
    if (smallTfs.includes(currentBackendTf)) {
        if (currentChartRange === '1Y' || currentChartRange === '5Y' || currentChartRange === 'MAX') {
            currentChartRange = "1M";
            localStorage.setItem('lastChartRange', "1M");
        }
    } else if (currentBackendTf === "1Hour") {
        if (currentChartRange === '5Y' || currentChartRange === 'MAX') {
            currentChartRange = "1Y";
            localStorage.setItem('lastChartRange', "1Y");
        }
    }

    updateChartControlState(selectedTicker);
    loadSelectedTickerChart({ force: true, renderCached: false, fetchFresh: true });
    fetchDashboard('fast');
    if (selectedTicker) fetchAIIndicator(selectedTicker, currentBackendTf);
}

function setChartRange(range) {
    if (!CHART_RANGES[range]) return;
    currentChartRange = range;
    localStorage.setItem('lastChartRange', range);
    resetCompletedCandleState();
    setChartLoadingState(true, selectedTicker);

    // Only force 1Y and 5Y (and MAX) to minimum timeframes if they are currently set to smaller ones.
    if (range === '1Y') {
        const smallTfs = ["1Min", "5Min", "15Min", "30Min"];
        if (smallTfs.includes(currentBackendTf)) {
            currentBackendTf = "1Hour";
            localStorage.setItem('lastChartTf', currentBackendTf);
        }
    } else if (range === '5Y' || range === 'MAX') {
        const smallTfs = ["1Min", "5Min", "15Min", "30Min", "1Hour"];
        if (smallTfs.includes(currentBackendTf)) {
            currentBackendTf = "4Hour";
            localStorage.setItem('lastChartTf', currentBackendTf);
        }
    }

    updateChartControlState(selectedTicker);
    if (window._lastPriceHistory) {
        updateChart(window._lastPriceHistory, window._lastTicker || selectedTicker);
    }
    loadSelectedTickerChart({ force: true, renderCached: false, fetchFresh: true });
    fetchDashboard('fast');
}

function setChartSession(session) {
    if (session === 'crypto24' || isCryptoTicker(selectedTicker)) {
        currentChartSession = 'crypto24';
    } else {
        currentChartSession = session === 'extended' ? 'extended' : 'regular';
    }
    localStorage.setItem('lastChartSession', currentChartSession);
    resetCompletedCandleState();
    setChartLoadingState(true, selectedTicker);
    updateChartControlState(selectedTicker);
    loadSelectedTickerChart({ force: true, renderCached: true, fetchFresh: true });
}

// ──────────────────────────────────────────────
// 1d. Watchlist Star toggle
// ──────────────────────────────────────────────
function _updateStarState() {
    const icon = document.getElementById('favIcon');
    if (!icon || !selectedTicker) return;
    const inList = (window._currentWatchlist || []).includes(selectedTicker);
    icon.setAttribute('fill', inList ? '#f59e0b' : 'none');
    icon.setAttribute('stroke', inList ? '#f59e0b' : 'currentColor');
}

async function toggleWatchlistStar() {
    if (!selectedTicker) return;
    const inList = (window._currentWatchlist || []).includes(selectedTicker);
    const headers = await getAuthHeaders();
    if (inList) {
        await fetch(`${API_BASE}/api/watchlist/${selectedTicker}`, { method: 'DELETE', headers });
    } else {
        await fetch(`${API_BASE}/api/watchlist`, { method: 'POST', headers, body: JSON.stringify({ ticker: selectedTicker }) });
    }
    fetchDashboard('fast');
}

// ──────────────────────────────────────────────
// 1e. Ticker Search
// ──────────────────────────────────────────────
const COMMON_TICKERS = [
    'AAPL', 'MSFT', 'GOOGL', 'AMZN', 'TSLA', 'NVDA', 'META', 'NFLX', 'AMD', 'INTC',
    'BTCUSD', 'ETHUSD', 'DOGEUSD', 'XRPUSD', 'SOLUSD', 'TRUMPUSD', 'LTCUSD',
    'SPY', 'QQQ', 'ARKK', 'GLD', 'BABA', 'JPM', 'GS', 'BAC', 'WMT', 'COST'
];
let _searchDebounce = null;

function onTickerSearchInput(val) {
    clearTimeout(_searchDebounce);
    _searchDebounce = setTimeout(() => _showSearchResults(val.trim().toUpperCase()), 200);
}

function _showSearchResults(query) {
    const container = document.getElementById('tickerSearchResults');
    if (!container) return;
    if (!query || query.length < 1) { container.classList.add('hidden'); return; }
    const combined = [...new Set([...COMMON_TICKERS, ...(window._currentWatchlist || [])])]
        .filter(t => t.startsWith(query) || t.includes(query)).slice(0, 10);
    if (combined.length === 0) { container.classList.add('hidden'); return; }
    container.innerHTML = combined.map(t => {
        const inWl = (window._currentWatchlist || []).includes(t);
        const safeTicker = escapeDashboardHtml(t);
        const safeTickerArgument = escapeDashboardHtml(JSON.stringify(String(t)));
        return `<div class="flex items-center justify-between px-4 py-2.5 hover:bg-indigo-50 cursor-pointer text-sm font-bold text-indigo-900 border-b border-slate-50 last:border-0 transition-colors"
                    onclick="selectTickerFromSearch(${safeTickerArgument})">
                    <span>${safeTicker}</span>
                    ${inWl ? '<span class="text-yellow-500 text-xs">★</span>' : ''}
                </div>`;
    }).join('');
    container.classList.remove('hidden');
}

function onTickerSearchKey(e) {
    if (e.key === 'Enter') {
        const val = e.target.value.trim().toUpperCase();
        if (val) selectTickerFromSearch(val);
    } else if (e.key === 'Escape') {
        document.getElementById('tickerSearchResults')?.classList.add('hidden');
    }
}

function selectTickerFromSearch(ticker) {
    document.getElementById('tickerSearchResults')?.classList.add('hidden');
    selectTicker(ticker);
}

// Close search dropdown on outside click
document.addEventListener('click', e => {
    if (!e.target.closest('#tickerSearchResults') && !e.target.closest('#tickerSearch')) {
        document.getElementById('tickerSearchResults')?.classList.add('hidden');
    }
});

// Indicator Config Mapping

var INDICATOR_TOOLTIPS = {
    "RSI": "Relative Strength Index (RSI) is a momentum oscillator. Purpose: Identifies overbought (>70) or oversold (<30) conditions.",
    "MACD": "Moving Average Convergence Divergence (MACD) shows the relationship between two moving averages. Purpose: Detects changes in trend momentum.",
    "EMA Cross": "Exponential Moving Average (EMA) places greater weight on recent prices. Purpose: Reacts faster to price changes to catch trends early.",
    "SMA": "Simple Moving Average (SMA) is the unweighted mean of previous prices. Purpose: Smooths out price data to identify trend direction.",
    "Bollinger": "Bollinger Bands are volatility bands placed above and below a moving average. Purpose: Measures market volatility and potential overbought/oversold levels.",
    "Supertrend": "Supertrend is a trend-following indicator based on Average True Range (ATR). Purpose: Identifies the current market trend and provides dynamic stop-loss levels.",
    "Mystic Pulse": "Mystic Pulse is a custom proprietary oscillator. Purpose: Combines multi-timeframe momentum, volatility, and volume flow to detect early trend reversals.",
    "Candle patterns": "Candlestick Patterns identify specific OHLC price formations. Purpose: Provides visual clues about market psychology and potential reversals.",
    "ADX Trend": "Average Directional Index (ADX) quantifies trend strength. Purpose: Determines if the market is trending strongly (ADX > 25) or ranging.",
    "VWAP": "Session VWAP is the cumulative typical-price and volume average for the exchange-local trading day, matching the standard Webull-style VWAP calculation.",
    "Auto-AVWAP": "Auto-AVWAP anchors the volume-weighted average at the latest volume spike. It is intentionally separate from Session VWAP and may not match Webull VWAP.",
    "Volume Profile": "Prior-session volume-at-price profile using POC, VAH, and VAL. Purpose: Identifies institutional acceptance/rejection zones where the most volume traded.",
    "Prior Day High/Low": "Uses the previous completed trading session's high and low as breakout/breakdown levels. Buffer is entered as %, with dotted trigger lines when non-zero.",
    "Pivot Points (S/R)": "Classic prior-session floor pivot levels. Draws up to 5 support/resistance ladders; bullish when price reclaims support/pivot, bearish when price rejects resistance or loses pivot.",
    "Swing Points": "Rolling swing support/resistance clusters from recent local highs/lows. Purpose: Identifies structural bounce, rejection, reclaim, and breakdown zones.",
    "Strategy Confidence": "Strategy Confidence combines multiple indicator signals into a single probability score. Purpose: Measures the overall conviction of a bullish or bearish trend."
};

var INDICATOR_CONFIG_MAP = {
    'RSI': ['RSI_PERIOD', 'RSI_OVERBOUGHT', 'RSI_OVERSOLD'],
    'MACD': ['MACD_FAST', 'MACD_SLOW', 'MACD_SIGNAL'],
    'EMA Cross': ['EMA_USE_SECOND_LINE', 'EMA_FAST', 'EMA_SLOW'],
    'Supertrend': ['SUPERTREND_PERIOD', 'SUPERTREND_MULTIPLIER'],
    'Bollinger': ['BOLL_PERIOD', 'BOLL_STD_DEV'],
    'Mystic Pulse': ['MYSTIC_PULSE_THRESHOLD'],
    'ADX Trend': ['ADX_PERIOD', 'ADX_TRENDING_THRESHOLD'],
    'SMA': ['SMA_USE_SECOND_LINE', 'SMA_PERIOD', 'SMA_SECOND_PERIOD'],
    'Prior Day High/Low': ['DAILY_HIGH_LOW_BUFFER_PCT'],
    'Pivot Points (S/R)': ['PIVOT_LEVEL_COUNT', 'PIVOT_TOLERANCE_PCT'],
    'Volume Profile': ['VOLUME_PROFILE_ROWS', 'VOLUME_PROFILE_VALUE_AREA_PCT', 'VOLUME_PROFILE_TOLERANCE_PCT', 'VOLUME_PROFILE_CONFIRMATION_CANDLES'],
    'Swing Points': ['SWING_POINTS_LOOKBACK_DAYS', 'SWING_POINTS_ENTRY_MODE', 'SWING_POINTS_LEVEL_COUNT'],
    'ATR Volatility': ['ATR_PERIOD', 'ATR_STOP_MULTIPLIER', 'ATR_TRAIL_MULTIPLIER', 'ATR_TAKE_PROFIT_MULTIPLIER'],
    'Strategy Confidence': ['MIN_BULLISH_SIGNALS', 'MIN_BEARISH_SIGNALS'],
};
Object.assign(INDICATOR_CONFIG_MAP, window.STANDARD_INDICATOR_CONFIG_MAP || {});

// Industry Defaults for indicators based on config.py
var INDICATOR_DEFAULTS = {
    'RSI_PERIOD': 14,
    'RSI_OVERBOUGHT': 70,
    'RSI_OVERSOLD': 30,
    'EMA_USE_SECOND_LINE': 1,
    'EMA_FAST': 9,
    'EMA_SLOW': 21,
    'MACD_FAST': 12,
    'MACD_SLOW': 26,
    'MACD_SIGNAL': 9,
    'BOLL_PERIOD': 20,
    'BOLL_STD_DEV': 2.0,
    'SUPERTREND_PERIOD': 10,
    'SUPERTREND_MULTIPLIER': 3.0,
    'MYSTIC_PULSE_THRESHOLD': 5,
    'ADX_PERIOD': 14,
    'ADX_TRENDING_THRESHOLD': 25,
    'SMA_USE_SECOND_LINE': 1,
    'SMA_PERIOD': 200,
    'SMA_SECOND_PERIOD': 50,
    'DAILY_HIGH_LOW_BUFFER_PCT': 0.0,
    'PIVOT_LEVEL_COUNT': 1,
    'PIVOT_TOLERANCE_PCT': 0.0,
    'VOLUME_PROFILE_ROWS': 48,
    'VOLUME_PROFILE_VALUE_AREA_PCT': 70.0,
    'VOLUME_PROFILE_TOLERANCE_PCT': 0.15,
    'VOLUME_PROFILE_CONFIRMATION_CANDLES': 1,
    'SWING_POINTS_LOOKBACK_DAYS': 20,
    'SWING_POINTS_ENTRY_MODE': 'bounce_retest',
    'SWING_POINTS_LEVEL_COUNT': 1,
    'ATR_PERIOD': 14,
    'ATR_STOP_MULTIPLIER': 2.0,
    'ATR_TRAIL_MULTIPLIER': 3.0,
    'ATR_TAKE_PROFIT_MULTIPLIER': 4.0,
    'MIN_BULLISH_SIGNALS': 4,
    'MIN_BEARISH_SIGNALS': 4,
    'SENTIMENT_BULLISH_PERCENTAGE': 70,
    'SENTIMENT_BEARISH_PERCENTAGE': 30
};
Object.assign(INDICATOR_DEFAULTS, window.STANDARD_INDICATOR_DEFAULTS || {});

let currentEditingIndicator = null;

// ──────────────────────────────────────────────
// 2.5 Global Indicator State Management
// ──────────────────────────────────────────────
const GLOBAL_INDICATOR_PREFS_KEY = 'globalIndicatorPrefs';
const DASHBOARD_CONFLUENCE_PREFS_VERSION_KEY = 'dashboardConfluencePrefsVersion';
const DASHBOARD_CONFLUENCE_PREFS_VERSION = 'v2';
const ALL_SIGNAL_NAMES = [
    ...(window.STANDARD_INDICATOR_NAMES || [
        'RSI', 'MACD', 'EMA Cross', 'ADX Trend', 'Supertrend',
        'Bollinger', 'VWAP', 'Volume Profile', 'SMA', 'Prior Day High/Low',
        'Pivot Points (S/R)', 'Swing Points', 'Mystic Pulse', 'Candle Patterns',
    ]),
];
const INDICATOR_TOGGLE_KEYS = {
    'RSI': 'ENABLE_RSI',
    'MACD': 'ENABLE_MACD',
    'EMA Cross': 'ENABLE_EMA',
    'Supertrend': 'ENABLE_SUPERTREND',
    'Bollinger': 'ENABLE_BOLLINGER',
    'VWAP': 'ENABLE_VWAP',
    'Auto-AVWAP': 'ENABLE_AUTO_AVWAP',
    'Mystic Pulse': 'ENABLE_MYSTIC_PULSE',
    'ADX Trend': 'ENABLE_ADX_TREND',
    'SMA': 'ENABLE_SMA',
    'Prior Day High/Low': 'ENABLE_DAILY_HIGH_LOW',
    'Pivot Points (S/R)': 'ENABLE_PIVOT_POINTS',
    'Volume Profile': 'ENABLE_VOLUME_PROFILE',
    'Swing Points': 'ENABLE_SWING_POINTS',
    'Candle Patterns': 'ENABLE_CANDLE_PATTERNS',
};
Object.assign(INDICATOR_TOGGLE_KEYS, window.STANDARD_INDICATOR_TOGGLE_KEYS || {});
const DASHBOARD_STANDARD_INDICATOR_NAMES = window.STANDARD_INDICATOR_NAMES || [];
const DASHBOARD_STANDARD_INDICATOR_SET = new Set(DASHBOARD_STANDARD_INDICATOR_NAMES);
const DASHBOARD_INDICATOR_DISPLAY_LIMIT = 15;
const DASHBOARD_DEFAULT_DISPLAYED_INDICATORS = window.STANDARD_INDICATOR_DEFAULT_NAMES || [
    'RSI', 'EMA Cross', 'SMA', 'VWAP', 'ADX Trend', 'Supertrend',
    'ATR Volatility', 'Volume Profile', 'Candle Patterns', 'Prior Day High/Low',
    'Swing Points', 'Pivot Points (S/R)', 'MACD', 'Bollinger', 'Chart Patterns',
];
const DASHBOARD_STANDARD_TOGGLE_KEYS = new Set(
    DASHBOARD_STANDARD_INDICATOR_NAMES
        .map(name => INDICATOR_TOGGLE_KEYS[name])
        .filter(Boolean)
);

function getGlobalIndicatorPrefs() {
    try {
        let prefs = JSON.parse(localStorage.getItem(GLOBAL_INDICATOR_PREFS_KEY)) || {};
        const storedVersion = localStorage.getItem(DASHBOARD_CONFLUENCE_PREFS_VERSION_KEY);

        if (storedVersion !== DASHBOARD_CONFLUENCE_PREFS_VERSION) {
            const hasResetArtifactShape = ALL_SIGNAL_NAMES.every(name => (
                Object.prototype.hasOwnProperty.call(prefs, name)
            ));
            const enabledNames = ALL_SIGNAL_NAMES.filter(name => prefs[name] === true);

            // The previous Reset implementation persisted every indicator OFF.
            // If the user subsequently enabled one other card, the
            // Dashboard became a one-card grid. Repair only that exact legacy shape;
            // valid partial preference maps and deliberate multi-indicator setups stay intact.
            if (hasResetArtifactShape && enabledNames.length <= 1) {
                prefs = getDashboardDefaultIndicatorPrefs();
                localStorage.setItem(GLOBAL_INDICATOR_PREFS_KEY, JSON.stringify(prefs));
                window._dashboardConfluenceBackendRepairPending = true;
            }
            localStorage.setItem(
                DASHBOARD_CONFLUENCE_PREFS_VERSION_KEY,
                DASHBOARD_CONFLUENCE_PREFS_VERSION
            );
        }
        return prefs;
    } catch { return {}; }
}

function getDashboardWorkspaceShownSet() {
    const fallback = DASHBOARD_DEFAULT_DISPLAYED_INDICATORS.slice(0, DASHBOARD_INDICATOR_DISPLAY_LIMIT);
    const ticker = String(typeof selectedTicker !== 'undefined'
        ? selectedTicker
        : document.getElementById('chartTickerLabel')?.textContent || '')
        .trim()
        .toUpperCase()
        .replace(/[^A-Z0-9.^/\-]/g, '');
    const shown = window.IndicatorWorkspace?.getShownForScope?.('dashboard', ticker)
        || window.IndicatorWorkspace?.getShown?.('dashboard');
    if (Array.isArray(shown)) return new Set(shown);
    try {
        const store = JSON.parse(localStorage.getItem('botbullsIndicatorWorkspace:dashboard:v3') || 'null');
        // Dashboard uses one shared indicator grid across every ticker. Keep
        // the former ticker scope only as a pre-migration fallback while the
        // workspace script initializes after a hard refresh.
        const sharedShown = store?.scopes?.['dashboard:GLOBAL']?.shown;
        const tickerShown = store?.scopes?.[`dashboard:${ticker || 'DEFAULT'}`]?.shown;
        const scopedShown = Array.isArray(sharedShown) ? sharedShown : tickerShown;
        if (Array.isArray(scopedShown) && scopedShown.length) {
            return new Set([...new Set(scopedShown)].slice(0, DASHBOARD_INDICATOR_DISPLAY_LIMIT));
        }
    } catch (error) {}
    return new Set(fallback);
}

function isDashboardIndicatorAllowed(indicatorName) {
    const standardNames = window.STANDARD_INDICATOR_NAMES || [];
    return !standardNames.includes(indicatorName) || getDashboardWorkspaceShownSet().has(indicatorName);
}

function getDashboardDefaultIndicatorPrefs() {
    const shown = getDashboardWorkspaceShownSet();
    const prefs = allIndicatorPrefs(false);
    shown.forEach(name => {
        if (Object.prototype.hasOwnProperty.call(prefs, name)) prefs[name] = true;
    });
    return prefs;
}

function getDashboardConfluenceToggleDefaults(prefs = getDashboardDefaultIndicatorPrefs()) {
    return Object.fromEntries(
        Object.entries(INDICATOR_TOGGLE_KEYS).map(([name, toggleKey]) => [
            toggleKey,
            prefs[name] === true,
        ])
    );
}

function allIndicatorPrefs(enabled = false) {
    return Object.fromEntries(ALL_SIGNAL_NAMES.map(name => [name, enabled]));
}

function getDashboardIndicatorWorkspacePayload(enabledOverride = null) {
    const selected = window.IndicatorWorkspace?.getShown?.('dashboard');
    const selectedStandard = Array.isArray(selected)
        ? [...new Set(selected.filter(name => DASHBOARD_STANDARD_INDICATOR_SET.has(name)))]
        : [];
    const displayed = (selectedStandard.length
        ? selectedStandard
        : DASHBOARD_DEFAULT_DISPLAYED_INDICATORS)
        .slice(0, DASHBOARD_INDICATOR_DISPLAY_LIMIT);
    const displayedSet = new Set(displayed);
    const prefs = getGlobalIndicatorPrefs();
    const enabledSource = Array.isArray(enabledOverride)
        ? new Set(enabledOverride)
        : null;
    return {
        displayed_indicators: displayed
            .filter(name => DASHBOARD_STANDARD_INDICATOR_SET.has(name))
            .slice(0, DASHBOARD_INDICATOR_DISPLAY_LIMIT),
        enabled_indicators: displayed.filter(name => (
            DASHBOARD_STANDARD_INDICATOR_SET.has(name)
            && (enabledSource ? enabledSource.has(name) : prefs[name] === true)
        )),
        favorites: (window.IndicatorWorkspace?.getFavorites?.() || [])
            .filter(name => DASHBOARD_STANDARD_INDICATOR_SET.has(name)),
    };
}

function applyDashboardIndicatorWorkspace(workspace) {
    if (!workspace || typeof workspace !== 'object') return false;
    const requestedDisplayed = Array.isArray(workspace.displayed_indicators)
        ? workspace.displayed_indicators.filter(name => DASHBOARD_STANDARD_INDICATOR_SET.has(name))
        : [];
    const displayed = (requestedDisplayed.length
        ? [...new Set(requestedDisplayed)]
        : DASHBOARD_DEFAULT_DISPLAYED_INDICATORS)
        .slice(0, DASHBOARD_INDICATOR_DISPLAY_LIMIT);
    const displayedSet = new Set(displayed);
    const enabled = new Set(
        (Array.isArray(workspace.enabled_indicators) ? workspace.enabled_indicators : [])
            .filter(name => displayedSet.has(name))
    );
    const favorites = Array.isArray(workspace.favorites)
        ? workspace.favorites.filter(name => DASHBOARD_STANDARD_INDICATOR_SET.has(name))
        : [];

    const prefs = getGlobalIndicatorPrefs();
    DASHBOARD_STANDARD_INDICATOR_NAMES.forEach(name => {
        prefs[name] = displayedSet.has(name) && enabled.has(name);
        if (window._lastSignals?.[name]) window._lastSignals[name].enabled = prefs[name];
    });
    localStorage.setItem(GLOBAL_INDICATOR_PREFS_KEY, JSON.stringify(prefs));
    window.IndicatorWorkspace?.setDashboardState?.({
        displayed_indicators: displayed,
        favorites,
    });
    _syncIndicatorsFromSignals(window._lastSignals || {});
    if (window._lastSignals) recomputeConfluence();
    return true;
}

async function postDashboardIndicatorWorkspace(workspace, { refresh = false, applyResponse = true } = {}) {
    const headers = await getAuthHeaders();
    const response = await fetchWithTimeout(`${API_BASE}/api/settings/dashboard-indicator-workspace`, {
        method: 'POST',
        headers,
        body: JSON.stringify(workspace),
    }, 8000);
    if (!response.ok) throw new Error(`Dashboard indicator workspace save failed with HTTP ${response.status}`);
    const saved = await response.json();
    if (applyResponse) applyDashboardIndicatorWorkspace(saved);
    if (refresh && saved.calculation_changed !== false) {
        refreshDashboardSignalsAfterIndicatorSave();
    }
    return saved;
}

async function hydrateDashboardIndicatorWorkspace() {
    try {
        const headers = await getAuthHeaders();
        const response = await fetchWithTimeout(`${API_BASE}/api/settings/dashboard-indicator-workspace`, {
            headers,
        }, 5000);
        if (!response.ok) throw new Error(`Dashboard indicator workspace load failed with HTTP ${response.status}`);
        const remote = await response.json();

        if (remote.configured === true) {
            applyDashboardIndicatorWorkspace(remote);
            return remote;
        }

        // First-use migration: preserve the browser's shared Dashboard grid
        // and favorites, but take the enabled subset from the server toggles.
        const enabledFromServer = remote.legacy_enabled_indicators || remote.enabled_indicators || [];
        const migration = getDashboardIndicatorWorkspacePayload(enabledFromServer);
        return await postDashboardIndicatorWorkspace(migration, { refresh: false, applyResponse: true });
    } catch (error) {
        console.warn('[indicator-workspace] Backend workspace sync unavailable; using the local Dashboard cache.', error);
        return null;
    }
}

let dashboardWorkspaceSaveTimer = null;
let dashboardWorkspaceSaveInFlight = false;
let dashboardWorkspaceSaveRetryCount = 0;
let dashboardWorkspaceRefreshRequired = false;
const dashboardWorkspaceRefreshIndicators = new Set();

function scheduleDashboardWorkspaceSave({ refresh = null, indicatorName = null } = {}) {
    // An enable needs a fresh chart calculation. A disable only changes the
    // local presentation/confluence state, so it must be allowed to cancel a
    // queued refresh (for example, when the user toggles twice quickly).
    // Calls without an explicit refresh flag preserve the current requirement.
    if (indicatorName && refresh !== null) {
        if (refresh === true) dashboardWorkspaceRefreshIndicators.add(indicatorName);
        else dashboardWorkspaceRefreshIndicators.delete(indicatorName);
    } else if (refresh !== null) {
        dashboardWorkspaceRefreshRequired = refresh === true;
    }
    holdDashboardIndicatorRefreshLock();
    clearTimeout(dashboardWorkspaceSaveTimer);
    dashboardWorkspaceSaveTimer = setTimeout(flushDashboardWorkspaceSave, 120);
}

async function flushDashboardWorkspaceSave() {
    dashboardWorkspaceSaveTimer = null;
    if (dashboardWorkspaceSaveInFlight) {
        scheduleDashboardWorkspaceSave();
        return;
    }
    dashboardWorkspaceSaveInFlight = true;
    const refreshAfterSave = dashboardWorkspaceRefreshRequired || dashboardWorkspaceRefreshIndicators.size > 0;
    dashboardWorkspaceRefreshRequired = false;
    dashboardWorkspaceRefreshIndicators.clear();
    try {
        // Do not reapply the response here: the user may make another change
        // while this request is in flight. The next queued snapshot must win.
        await postDashboardIndicatorWorkspace(
            getDashboardIndicatorWorkspacePayload(),
            { refresh: refreshAfterSave, applyResponse: false }
        );
        dashboardWorkspaceSaveRetryCount = 0;
    } catch (error) {
        if (dashboardWorkspaceSaveRetryCount < 1) {
            dashboardWorkspaceSaveRetryCount += 1;
            dashboardWorkspaceRefreshRequired = dashboardWorkspaceRefreshRequired || refreshAfterSave;
            dashboardWorkspaceSaveTimer = setTimeout(flushDashboardWorkspaceSave, 1200);
        } else {
            dashboardWorkspaceSaveRetryCount = 0;
            console.warn('[indicator-workspace] Dashboard workspace save failed; local selection is retained.', error);
        }
    } finally {
        dashboardWorkspaceSaveInFlight = false;
        if (!dashboardWorkspaceSaveTimer && !dashboardIndicatorSaveInFlight) {
            clearTimeout(window._indicatorToggleDebounce);
            window._indicatorToggleDebounce = null;
        }
    }
}

let pendingDashboardIndicatorUpdates = {};
let dashboardIndicatorSaveTimer = null;
let dashboardIndicatorSaveInFlight = false;
let dashboardIndicatorSaveRetryCount = 0;
let dashboardIndicatorSignalRefreshes = 0;

function holdDashboardIndicatorRefreshLock() {
    clearTimeout(window._indicatorToggleDebounce);
    window._indicatorToggleDebounce = setTimeout(() => {
        window._indicatorToggleDebounce = null;
    }, 15000);
}

function scheduleDashboardIndicatorSave(toggleKey, enabled) {
    if (DASHBOARD_STANDARD_TOGGLE_KEYS.has(toggleKey)) {
        const indicatorName = DASHBOARD_STANDARD_INDICATOR_NAMES.find(
            name => INDICATOR_TOGGLE_KEYS[name] === toggleKey
        );
        scheduleDashboardWorkspaceSave({
            refresh: enabled === true,
            indicatorName,
        });
        return;
    }
    pendingDashboardIndicatorUpdates[toggleKey] = enabled;
    holdDashboardIndicatorRefreshLock();
    clearTimeout(dashboardIndicatorSaveTimer);
    dashboardIndicatorSaveTimer = setTimeout(flushDashboardIndicatorUpdates, 120);
}

function repairLegacyDashboardConfluenceStateIfNeeded() {
    if (window._dashboardConfluenceBackendRepairPending !== true) return;
    window._dashboardConfluenceBackendRepairPending = false;
    const toggleDefaults = getDashboardConfluenceToggleDefaults(getGlobalIndicatorPrefs());
    Object.entries(toggleDefaults).forEach(([toggleKey, enabled]) => {
        scheduleDashboardIndicatorSave(toggleKey, enabled);
    });
}

function refreshDashboardSignalsAfterIndicatorSave() {
    dashboardIndicatorSignalRefreshes += 1;
    window._indicatorRefreshRenderAllowed = true;
    // A signal-only refresh cannot supply the per-candle fields for a newly
    // enabled overlay/subchart. Fetch the chart once so its vote and drawing
    // arrive from the same freshly calculated payload.
    loadSelectedTickerChart({ force: true, renderCached: false, fetchFresh: true })
        .finally(() => {
            dashboardIndicatorSignalRefreshes = Math.max(0, dashboardIndicatorSignalRefreshes - 1);
            window._indicatorRefreshRenderAllowed = dashboardIndicatorSignalRefreshes > 0;
        });
}

async function flushDashboardIndicatorUpdates() {
    dashboardIndicatorSaveTimer = null;
    if (dashboardIndicatorSaveInFlight) return;

    const updates = { ...pendingDashboardIndicatorUpdates };
    if (!Object.keys(updates).length) {
        clearTimeout(window._indicatorToggleDebounce);
        window._indicatorToggleDebounce = null;
        return;
    }
    pendingDashboardIndicatorUpdates = {};
    dashboardIndicatorSaveInFlight = true;

    try {
        const headers = await getAuthHeaders();
        const response = await fetchWithTimeout(`${API_BASE}/api/settings/indicators?context=dashboard`, {
            method: 'POST',
            headers,
            body: JSON.stringify(updates)
        }, 8000);
        if (!response.ok) throw new Error(`Indicator save failed with HTTP ${response.status}`);

        dashboardIndicatorSaveRetryCount = 0;
        // Disabling is intentionally local-only after the preference is
        // persisted. Recalculate only when at least one indicator was enabled.
        if (Object.values(updates).some(value => value === true)) {
            refreshDashboardSignalsAfterIndicatorSave();
        }
    } catch (error) {
        if (dashboardIndicatorSaveRetryCount < 1) {
            dashboardIndicatorSaveRetryCount += 1;
            Object.entries(updates).forEach(([key, value]) => {
                if (!Object.prototype.hasOwnProperty.call(pendingDashboardIndicatorUpdates, key)) {
                    pendingDashboardIndicatorUpdates[key] = value;
                }
            });
        } else {
            dashboardIndicatorSaveRetryCount = 0;
            console.warn('[confluence] Indicator preference save failed; local selection is retained.', error);
        }
    } finally {
        dashboardIndicatorSaveInFlight = false;
        if (Object.keys(pendingDashboardIndicatorUpdates).length) {
            holdDashboardIndicatorRefreshLock();
            clearTimeout(dashboardIndicatorSaveTimer);
            dashboardIndicatorSaveTimer = setTimeout(flushDashboardIndicatorUpdates, 1200);
        } else if (!dashboardWorkspaceSaveTimer && !dashboardWorkspaceSaveInFlight) {
            clearTimeout(window._indicatorToggleDebounce);
            window._indicatorToggleDebounce = null;
        }
    }
}

function setGlobalIndicatorPref(indicatorName, enabled, options = {}) {
    enabled = enabled === true && isDashboardIndicatorAllowed(indicatorName);
    const prefs = getGlobalIndicatorPrefs();
    prefs[indicatorName] = enabled;
    localStorage.setItem(GLOBAL_INDICATOR_PREFS_KEY, JSON.stringify(prefs));
    if (!enabled) clearDisabledIndicatorSignalLocally(indicatorName);
    _syncIndicatorsFromSignals(window._lastSignals || {});
    recomputeConfluence();
    if (Array.isArray(window._displayPriceHistory) && window._displayPriceHistory.length) {
        _refreshOverlays(window._displayPriceHistory);
        _refreshOscillators(window._displayPriceHistory);
    }

    const toggleKey = window._lastSignals?.[indicatorName]?.toggle_key || INDICATOR_TOGGLE_KEYS[indicatorName];
    if (toggleKey) {
        scheduleDashboardIndicatorSave(toggleKey, enabled);
    }
}

function reconcileDashboardIndicatorsWithWorkspace(event) {
    const detail = event?.detail || {};
    if (detail.contextLabel && detail.contextLabel !== 'dashboard') return;

    const shown = new Set(detail.shown || getDashboardWorkspaceShownSet());
    const prefs = getGlobalIndicatorPrefs();
    const disabledNames = [];
    (window.STANDARD_INDICATOR_NAMES || []).forEach(name => {
        if (shown.has(name) || prefs[name] !== true) return;
        prefs[name] = false;
        disabledNames.push(name);
    });

    if (disabledNames.length) {
        localStorage.setItem(GLOBAL_INDICATOR_PREFS_KEY, JSON.stringify(prefs));
        disabledNames.forEach(name => {
            const toggleKey = window._lastSignals?.[name]?.toggle_key || INDICATOR_TOGGLE_KEYS[name];
            if (toggleKey) scheduleDashboardIndicatorSave(toggleKey, false);
            if (window._lastSignals?.[name]) window._lastSignals[name].enabled = false;
        });
    }

    _syncIndicatorsFromSignals(window._lastSignals || {});
    if (Array.isArray(window._displayPriceHistory) && window._displayPriceHistory.length) {
        _refreshOverlays(window._displayPriceHistory);
        _refreshOscillators(window._displayPriceHistory);
    }
    if (window._lastSignals) recomputeConfluence();
}

window.addEventListener('indicator-workspace:selection-change', reconcileDashboardIndicatorsWithWorkspace);
window.addEventListener('indicator-workspace:workspace-change', event => {
    if (event?.detail?.contextLabel !== 'dashboard') return;
    scheduleDashboardWorkspaceSave();
});

async function resetConfluenceIndicatorsToDefaults() {
    try {
        const defaultPrefs = allIndicatorPrefs(false);
        localStorage.setItem(GLOBAL_INDICATOR_PREFS_KEY, JSON.stringify(defaultPrefs));
        localStorage.setItem(
            DASHBOARD_CONFLUENCE_PREFS_VERSION_KEY,
            DASHBOARD_CONFLUENCE_PREFS_VERSION
        );
        const disabledVisible = Object.fromEntries(ALL_SIGNAL_NAMES.map(name => [name, false]));
        localStorage.setItem('visibleIndicators', JSON.stringify(disabledVisible));

        _visibleIndicators = { ...disabledVisible };
        ALL_SIGNAL_NAMES.forEach(key => {
            const btn = document.getElementById(`ind_btn_${key}`);
            if (btn) {
                btn.classList.remove('ind-btn-active');
                btn.classList.add('ind-btn-inactive');
            }
        });

        if (window.lastDashboardData) {
            window.lastDashboardData.indicator_overrides = {};
            window.lastDashboardData.indicator_toggle_overrides = getDashboardConfluenceToggleDefaults(defaultPrefs);
            window.lastDashboardData.indicator_parameters = { ...INDICATOR_DEFAULTS };
            if (window.lastDashboardData.primaryScan?.signals) {
                for (const [name, signal] of Object.entries(window.lastDashboardData.primaryScan.signals)) {
                    signal.enabled = false;
                }
            }
            if (window.lastDashboardData.signals) {
                for (const [name, signal] of Object.entries(window.lastDashboardData.signals)) {
                    signal.enabled = false;
                }
            }
        }
        if (window._lastSignals) {
            for (const [name, signal] of Object.entries(window._lastSignals)) {
                signal.enabled = false;
            }
        }
        recomputeConfluence();
    } catch (e) {
        console.warn('[confluence] Local reset prep failed:', e);
    }

    try {
        const headers = await getAuthHeaders();
        const response = await fetchWithTimeout(`${API_BASE}/api/settings/indicators?context=dashboard`, {
            method: 'POST',
            headers: headers,
            body: JSON.stringify({
                reset_confluence: true,
                confluence_toggles: getDashboardConfluenceToggleDefaults(allIndicatorPrefs(false)),
            })
        }, 8000);
        const resetPayload = response.ok ? await response.json().catch(() => null) : null;
        if (resetPayload?.toggles && window.lastDashboardData) {
            window.lastDashboardData.indicator_toggle_overrides = { ...resetPayload.toggles };
            window.lastDashboardData.indicator_overrides = {};
            window.lastDashboardData.indicator_parameters = { ...INDICATOR_DEFAULTS };
        }
        await postDashboardIndicatorWorkspace(
            getDashboardIndicatorWorkspacePayload(allIndicatorPrefs(false)),
            { refresh: false, applyResponse: false }
        );

        clearTimeout(window._indicatorToggleDebounce);
        window._indicatorToggleDebounce = null;
        if (typeof loadSelectedTickerChart === 'function') {
            loadSelectedTickerChart({ force: true, renderCached: false });
        } else {
            refreshActiveTickerSignals({ force: true });
        }
    } catch (e) {
        console.error('[confluence] Reset failed:', e);
    }
}

function indicatorParamValue(key, fallback) {
    const overrides = window.lastDashboardData?.indicator_overrides || {};
    if (overrides[key] !== undefined && overrides[key] !== null && overrides[key] !== '') {
        const val = Number(overrides[key]);
        if (Number.isFinite(val)) return val;
    }
    const params = window.lastDashboardData?.indicator_parameters || {};
    const raw = params[key] !== undefined && params[key] !== null && params[key] !== ''
        ? params[key]
        : fallback;
    const value = Number(raw);
    return Number.isFinite(value) ? value : fallback;
}

function getEnabledSignalCounts(signals, prefs = getGlobalIndicatorPrefs()) {
    let bullish = 0;
    let bearish = 0;
    for (const [name, data] of Object.entries(signals || {})) {
        if (prefs[name] !== true || !isDashboardIndicatorAllowed(name)) continue;
        if (data?.signal === 'BULLISH') bullish++;
        if (data?.signal === 'BEARISH') bearish++;
    }
    return { bullish, bearish };
}

function clearDisabledIndicatorSignalLocally(indicatorName) {
    // The same signal object can be held by several dashboard caches. Update
    // each store so a later local render cannot restore a stale BULLISH/
    // BEARISH vote before the next intentional enable refresh.
    const stores = [
        window._lastSignals,
        window.lastDashboardData?.signals,
        window.lastDashboardData?.primaryScan?.signals,
    ];
    const seen = new Set();
    stores.forEach(store => {
        if (!store || seen.has(store) || !store[indicatorName]) return;
        seen.add(store);
        const signal = store[indicatorName];
        if (typeof signal !== 'object') return;
        signal.signal = 'NEUTRAL';
        signal.reason = 'Disabled — click to enable';
        signal.enabled = false;
    });

    const counts = getEnabledSignalCounts(window._lastSignals || {});
    const scans = [
        window.lastDashboardData?.primaryScan,
        window.lastDashboardData,
    ];
    const updatedScans = new Set();
    scans.forEach(scan => {
        if (!scan || updatedScans.has(scan)) return;
        updatedScans.add(scan);
        scan.bullish_count = counts.bullish;
        scan.bearish_count = counts.bearish;
    });
}

function recomputeConfluence() {
    const signals = window._lastSignals;
    if (!signals) return;

    const prefs = getGlobalIndicatorPrefs();
    const { bullish, bearish } = getEnabledSignalCounts(signals, prefs);

    if (window.lastDashboardData?.primaryScan) {
        window.lastDashboardData.primaryScan.bullish_count = bullish;
        window.lastDashboardData.primaryScan.bearish_count = bearish;
    }
    if (window.lastDashboardData) {
        window.lastDashboardData.bullish_count = bullish;
        window.lastDashboardData.bearish_count = bearish;
    }

    // Determine verdict
    const minBullish = indicatorParamValue('MIN_BULLISH_SIGNALS', INDICATOR_DEFAULTS.MIN_BULLISH_SIGNALS || 4);
    const minBearish = indicatorParamValue('MIN_BEARISH_SIGNALS', INDICATOR_DEFAULTS.MIN_BEARISH_SIGNALS || 4);
    let action = 'HOLD', reason = 'Waiting for scan...';
    if (bullish >= minBullish) {
        action = 'BUY';
        reason = `BUY Triggered: ${bullish} bullish signals`;
    } else if (bearish >= minBearish) {
        action = 'SELL';
        reason = `SELL Triggered: ${bearish} bearish signals`;
    }

    if (window.lastDashboardData?.primaryScan) {
        window.lastDashboardData.primaryScan.action = action;
        window.lastDashboardData.primaryScan.reason = reason;
    }

    // We pass signals, action, reason, bullish, bearish to renderSignals
    // We need to bypass the debounce logic in renderSignals since this is a local redraw
    const wasDebouncing = window._indicatorToggleDebounce;
    window._indicatorToggleDebounce = null;

    renderSignals(signals, action, reason, bullish, bearish);

    window._indicatorToggleDebounce = wasDebouncing;
}
// AI Indicator — Groq/Ollama Agentic Signal
// ──────────────────────────────────────────────

window.onAIIndicatorUpdated = function (data, fromCache) {
    if (window._lastSignals) {
        renderSignals(
            window._lastSignals,
            window.lastDashboardData?.primaryScan?.action || 'HOLD',
            window.lastDashboardData?.primaryScan?.reason || '',
            window.lastDashboardData?.primaryScan?.bullish_count ?? 0,
            window.lastDashboardData?.primaryScan?.bearish_count ?? 0
        );
    }
    if (typeof recomputeConfluence === 'function') recomputeConfluence();
};



// 3. Render Signal Confluence Grid
// ──────────────────────────────────────────────

function lockSignalConfluencePanelHeight() {
    const panel = document.getElementById('signalConfluencePanel');
    if (!panel) return;

    const height = Math.ceil(panel.getBoundingClientRect().height);
    const lockedHeight = parseFloat(panel.style.minHeight) || 0;
    if (height > lockedHeight + 1 && height > 50) {
        panel.style.minHeight = `${height}px`;
        panel.dataset.heightLocked = 'true';
    }
}

function unlockSignalConfluencePanelHeight() {
    const panel = document.getElementById('signalConfluencePanel');
    if (!panel) return;
    panel.style.minHeight = '';
    panel.dataset.heightLocked = 'false';
}

if (typeof window !== 'undefined') {
    window.addEventListener('resize', unlockSignalConfluencePanelHeight);
    window.addEventListener('orientationchange', unlockSignalConfluencePanelHeight);
}

/* If the chart request fails and no signals are on screen, drop the spinner and offer a retry
 * instead of leaving 'Fetching Signals' up forever. Existing signals stay visible. */
function showSignalConfluenceFetchError(reason) {
    setSignalConfluenceLoadingState(false);
    const grid = document.getElementById('signalGrid');
    if (!grid || grid.children.length > 0) return;
    const box = document.createElement('div');
    box.className = 'col-span-full text-center text-xs text-slate-500 py-6';
    box.textContent = `Signals unavailable (${reason}). `;
    const btn = document.createElement('button');
    btn.type = 'button';
    btn.className = 'font-bold text-indigo-600 underline min-h-[44px] px-2';
    btn.textContent = 'Retry';
    btn.addEventListener('click', () => loadSelectedTickerChart({ force: true, renderCached: false, fetchFresh: true }));
    box.appendChild(btn);
    grid.appendChild(box);
}

function setSignalConfluenceLoadingState(isLoading) {
    const panel = document.getElementById('signalConfluencePanel');
    const loading = document.getElementById('gridLoading');
    if (panel) panel.classList.toggle('signal-confluence-loading', isLoading);
    if (loading) loading.classList.toggle('hidden', !isLoading);
    if (!isLoading) {
        unlockSignalConfluencePanelHeight();
    }
}

function buildMissingSignalPlaceholder(name, prefs) {
    const locallyEnabled = prefs && prefs[name] === true;
    const disabledReason = name === 'RSI'
        ? 'RSI: Disabled — click to enable'
        : 'Disabled - click to enable';
    return {
        signal: 'NEUTRAL',
        reason: locallyEnabled ? 'Waiting for refreshed values...' : disabledReason,
        enabled: locallyEnabled,
        toggle_key: INDICATOR_TOGGLE_KEYS[name],
    };
}

function renderSignals(signals, action, reason, bullishCount, bearishCount, options = {}) {
    // If the user is currently rapid-fire toggling indicators, ignore incoming dashboard data
    // to prevent reverting their optimistic UI changes. The final debounce will fetch the real state.
    if (window._indicatorToggleDebounce && !window._indicatorRefreshRenderAllowed) return;

    const grid = document.getElementById('signalGrid');
    const loading = document.getElementById('gridLoading');
    signals = signals || {};

    if (Object.keys(signals).length === 0 && !options.allowEmpty) {
        setSignalConfluenceLoadingState(true);
        return;
    }

    setSignalConfluenceLoadingState(false);

    // Backend counts may include indicators the user has just disabled. The
    // enabled local preferences are authoritative for the visible confluence
    // grid, so always render counts from the current signal set.
    const effectiveCounts = getEnabledSignalCounts(signals);
    bullishCount = effectiveCounts.bullish;
    bearishCount = effectiveCounts.bearish;

    const activeIds = new Set();
    const prefs = getGlobalIndicatorPrefs();
    const displaySignals = {};
    for (const name of ALL_SIGNAL_NAMES) {
        displaySignals[name] = signals[name] || buildMissingSignalPlaceholder(name, prefs);
    }
    for (const [name, data] of Object.entries(signals)) {
        if (!displaySignals[name]) displaySignals[name] = data;
    }

    for (const [name, data] of Object.entries(displaySignals)) {
        const targetGrid = grid;
        if (!targetGrid) continue;

        // Local prefs override backend 'enabled' flag
        const hasLocalPref = Object.prototype.hasOwnProperty.call(prefs, name);
        const isInDashboardGrid = isDashboardIndicatorAllowed(name);
        const isEnabled = isInDashboardGrid
            && (hasLocalPref ? prefs[name] === true : data.enabled !== false);

        // The Dashboard-wide regular grid must always retain its selected
        // cards. A disabled indicator is rendered as disabled instead of disappearing,
        // so the user can see the full confluence layout and re-enable it.
        if (!isInDashboardGrid) continue;

        // Determine CSS class
        const signalClass = !isEnabled
            ? 'neutral'
            : data.signal === 'BULLISH' ? 'bullish' : data.signal === 'BEARISH' ? 'bearish' : 'neutral';

        const icon = !isEnabled ? '●' : data.signal === 'BULLISH' ? '▲' : data.signal === 'BEARISH' ? '▼' : '●';
        const iconColor = !isEnabled ? 'text-slate-400' : data.signal === 'BULLISH' ? 'text-emerald-600' : data.signal === 'BEARISH' ? 'text-red-500' : 'text-purple-400';

        const cardId = 'signal-card-' + name.replace(/\s+/g, '-');
        activeIds.add(cardId);

        let card = document.getElementById(cardId);
        let isNew = false;
        if (!card) {
            card = document.createElement('div');
            card.id = cardId;
            isNew = true;
        }

        const newClassName = `signal-card group ${signalClass}${isEnabled ? '' : ' disabled-signal'} fade-in cursor-pointer transition-all duration-300`;
        if (card.className !== newClassName) {
            card.className = newClassName;
        }

        if (data.toggle_key) {
            card.onclick = function () {
                const isNowEnabled = !this.classList.contains('disabled-signal');
                const targetState = !isNowEnabled;
                setGlobalIndicatorPref(name, targetState);
            };
            card.title = isEnabled ? "Click to disable this indicator" : "Click to enable this indicator";
        }

        const cacheKey = `${isEnabled}-${data.signal}-${data.reason}-${data.value ?? ''}-${data.baseline ?? ''}-${data.period ?? ''}-${data.percentage ?? data.sentiment_pct ?? ''}`;
        if (card.dataset.cacheKey !== cacheKey) {
            let reasonHtml = isEnabled ? escapeDashboardHtml(data.reason) : 'Disabled — click to enable';
            const safeIndicatorName = escapeDashboardHtml(name);
            const encodedIndicatorName = encodeURIComponent(String(name));

            {
                // Standard signal card
                const displayName = name === 'VWAP' ? 'Session VWAP' : name;
                const safeDisplayName = escapeDashboardHtml(displayName);
                const labelClass = name === 'VWAP' ? 'vwap-indicator-label' : 'text-indigo-900';
                card.innerHTML = `
                    <div class="flex items-start justify-between">
                        <div class="flex items-start gap-1">
                            <span class="font-bold ${labelClass} ${!isEnabled ? 'opacity-50' : ''} transition-opacity duration-300">${safeDisplayName}</span>
                            <div class="group/tooltip relative inline-flex items-center">
                                <svg onclick="event.stopPropagation(); event.preventDefault(); typeof openInfoModal === 'function' ? openInfoModal(decodeURIComponent('${encodedIndicatorName}')) : null" xmlns="http://www.w3.org/2000/svg" class="text-indigo-300 hover:text-indigo-600 cursor-pointer transition-colors" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                                    <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M13 16h-1v-4h-1m1-4h.01M21 12a9 9 0 11-18 0 9 9 0 0118 0z" />
                                </svg>
                                <div class="invisible group-hover/tooltip:visible opacity-0 group-hover/tooltip:opacity-100 transition-all absolute bottom-full left-1/2 -translate-x-1/2 mb-2 px-3 py-1.5 bg-indigo-900 text-white text-[0.65rem] leading-snug rounded-lg shadow-2xl z-[999] pointer-events-none whitespace-nowrap text-center font-bold normal-case tracking-normal">
                                    Click to read guide
                                    <div class="absolute top-full left-1/2 -translate-x-1/2 border-4 border-transparent border-t-indigo-900"></div>
                                </div>
                            </div>
                        </div>
                        <div class="flex items-center gap-1">
                            <button class="opacity-70 hover:opacity-100 transition-opacity p-0.5 rounded hover:bg-indigo-50" 
                                    onclick="event.stopPropagation(); openIndicatorSettings(decodeURIComponent('${encodedIndicatorName}'))"
                                    title="Indicator Settings">
                                <svg xmlns="http://www.w3.org/2000/svg" class="text-indigo-500" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                                    <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2.5" d="M10.325 4.317c.426-1.756 2.924-1.756 3.35 0a1.724 1.724 0 002.573 1.066c1.543-.94 3.31.826 2.37 2.37a1.724 1.724 0 001.065 2.572c1.756.426 1.756 2.924 0 3.35a1.724 1.724 0 00-1.066 2.573c.94 1.543-.826 3.31-2.37 2.37a1.724 1.724 0 00-2.572 1.065c-.426 1.756-2.924 1.756-3.35 0a1.724 1.724 0 00-2.573-1.066c-1.543.94-3.31-.826-2.37-2.37a1.724 1.724 0 00-1.065-2.572c-1.756-.426-1.756-2.924 0-3.35a1.724 1.724 0 001.066-2.573c-.94-1.543.826-3.31 2.37-2.37.996.608 2.296.07 2.572-1.065z" />
                                    <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2.5" d="M15 12a3 3 0 11-6 0 3 3 0 016 0z" />
                                </svg>
                            </button>
                            <span class="${iconColor} text-sm font-bold ${!isEnabled ? 'opacity-50 grayscale' : ''} transition-all duration-300">${icon}</span>
                        </div>
                    </div>
                    <p class="text-purple-600 ${!isEnabled ? 'opacity-50' : ''} transition-opacity duration-300">${reasonHtml}</p>
                `;
            }
            card.dataset.cacheKey = cacheKey;
        }

        if (isNew) {
            const shownNames = window.IndicatorWorkspace?.getShown?.('dashboard')
                || (window.STANDARD_INDICATOR_NAMES || ALL_SIGNAL_NAMES).slice(0, DASHBOARD_INDICATOR_DISPLAY_LIMIT);
            const isShownInWorkspace = shownNames.includes(name);
            card.hidden = !isShownInWorkspace;
            card.classList.toggle('indicator-workspace-hidden', !isShownInWorkspace);
            card.setAttribute('aria-hidden', isShownInWorkspace ? 'false' : 'true');
            targetGrid.appendChild(card);
        }
    }

    // Remove stale cards from standard grid
    Array.from(grid.children).forEach(child => {
        if (!activeIds.has(child.id)) {
            grid.removeChild(child);
        }
    });

    // Update counts
    document.getElementById('bullCount').textContent = `${bullishCount} Bullish`;
    document.getElementById('bearCount').textContent = `${bearishCount} Bearish`;


    // Sync chart overlays to match which indicators are enabled here
    _syncIndicatorsFromSignals(signals);

    // Once real signals are fully rendered, unlock minHeight so the panel naturally sizes to fit its content without trailing blank space in landscape.
    if (!(options.allowEmpty && Object.keys(signals).length === 0)) {
        unlockSignalConfluencePanelHeight();
    }
    window.IndicatorWorkspace?.refresh();
    repairLegacyDashboardConfluenceStateIfNeeded();
}

// Map signal name → chart indicator key
const SIGNAL_TO_CHART_KEY = {
    'RSI': 'rsi',
    'MACD': 'macd',
    'EMA Cross': 'ema',
    'Supertrend': 'supertrend',
    'Bollinger': 'bollinger',
    'VWAP': 'vwap',
    'Auto-AVWAP': 'auto_avwap',
    'Mystic Pulse': 'mystic',
    'SMA': 'sma',
    'Prior Day High/Low': 'daily_high_low',
    'Pivot Points (S/R)': 'pivot_points',
    'Volume Profile': 'volume_profile',
    'Swing Points': 'swing_points',
    'ADX Trend': 'adx',
    'Candle Patterns': 'Candle Patterns',
};

function _syncIndicatorsFromSignals(signals) {
    let changed = false;
    const prefs = getGlobalIndicatorPrefs();
    for (const [name, key] of Object.entries(SIGNAL_TO_CHART_KEY)) {
        // If there's no explicitly saved preference, default to OFF.
        const shouldShow = prefs[name] === true && isDashboardIndicatorAllowed(name);
        if (_visibleIndicators[key] !== shouldShow) {
            _visibleIndicators[key] = shouldShow;
            
            // Also sync the toolbar button visually
            const btn = document.getElementById(`ind_btn_${key}`);
            if (btn) {
                btn.classList.toggle('ind-btn-active', shouldShow);
                btn.classList.toggle('ind-btn-inactive', !shouldShow);
            }
            
            changed = true;
        }
    }
    // Re-draw overlays from cache without a server call
    if (changed && window._lastPriceHistory) {
        updateChart(window._lastPriceHistory, window._lastTicker || selectedTicker);
    }
}

// ──────────────────────────────────────────────
// 4. Render Watchlist
// ──────────────────────────────────────────────
function renderWatchlist(scans, watchlist) {
    const container = document.getElementById('watchlistContainer');
    container.innerHTML = '';

    let tickers = watchlist || Object.keys(scans || {});

    // Filter based on active tab selection
    if (currentWatchlistTab === 'stocks') {
        tickers = tickers.filter(t => !isCryptoTicker(t));
    } else if (currentWatchlistTab === 'crypto') {
        tickers = tickers.filter(t => isCryptoTicker(t));
    }

    tickers.forEach(ticker => {
        // Always use general scans for the Dashboard Watchlist to ensure settings separation
        const scan = (scans || {})[ticker];
        const item = document.createElement('div');
        item.className = `watchlist-item group cursor-pointer ${selectedTicker === ticker ? 'active ring-2 ring-indigo-500 bg-indigo-50/60 shadow-sm' : 'bg-white/80 hover:bg-white'} px-2.5 py-1.5 rounded-lg border border-slate-200/80 hover:shadow-sm transition-all flex flex-col justify-between`;
        item.onclick = () => selectTicker(ticker);

        const price = scan?.price ? `$${isCryptoTicker(ticker) ? new Intl.NumberFormat('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 8, useGrouping: false }).format(parseFloat(scan.price.toString().replace('$', ''))) : parseFloat(scan.price.toString().replace('$', '')).toFixed(2)}` : '---';

        item.innerHTML = `
                <div class="flex items-center justify-between">
                    <div class="flex items-center gap-2">
                        <span class="font-black text-sm text-indigo-950 tracking-tight">${escapeDashboardHtml(ticker)}</span>
                    </div>
                    
                    <div class="flex items-center gap-3">
                        <span class="text-xs font-bold text-slate-500 font-mono tracking-tight">${price}</span>
                        <div class="opacity-0 group-hover:opacity-100 transition-opacity">
                            <button class="p-1 rounded hover:bg-rose-50 text-rose-300 hover:text-rose-400 transition-all"
                                title="Remove from Watchlist"
                                onclick="event.stopPropagation(); removeFromWatchlist(decodeURIComponent('${encodeURIComponent(String(ticker))}'))">
                                <svg xmlns="http://www.w3.org/2000/svg" class="h-4 w-4" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                                    <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2.5" d="M6 18L18 6M6 6l12 12" />
                                </svg>
                            </button>
                        </div>
                    </div>
                </div>
        `;
        container.appendChild(item);
    });

    // Populate ticker search datalist
    const datalist = document.getElementById('watchlistData');
    if (datalist) {
        datalist.innerHTML = '';
        tickers.forEach(t => {
            const opt = document.createElement('option');
            opt.value = t;
            datalist.appendChild(opt);
        });
    }

    updateActiveTickerDisplay(selectedTicker);
    updateFavoriteIcon();
}

function setWatchlistTab(tab) {
    currentWatchlistTab = tab;

    const tabs = {
        all: document.getElementById('wlTabAll'),
        stocks: document.getElementById('wlTabStocks'),
        crypto: document.getElementById('wlTabCrypto')
    };

    Object.keys(tabs).forEach(k => {
        const el = tabs[k];
        if (el) {
            if (k === tab) {
                el.className = 'flex-1 py-1 text-center rounded-md font-bold transition-all text-indigo-900 bg-white shadow-sm';
            } else {
                el.className = 'flex-1 py-1 text-center rounded-md font-bold transition-all text-slate-500 hover:text-indigo-900';
            }
        }
    });

    if (window.lastDashboardData) {
        renderWatchlist(
            window.lastDashboardData.watchlistScans,
            window.lastDashboardData.watchlist
        );
    }
}

function selectTicker(ticker) {
    const normalizedTicker = normalizeTicker(ticker);
    if (!normalizedTicker) return;

    // Auto-collapse watchlist when selecting a ticker and highlight active chart
    if (typeof window.toggleWatchlistCollapse === 'function') {
        window.toggleWatchlistCollapse(true);
    }
    setTimeout(() => {
        const chartPanel = document.getElementById('mainChartPanel');
        if (chartPanel) {
            const isMobile = window.innerWidth < 640;
            const topOffset = isMobile ? 8 : 16;
            const alignChartToTop = () => {
                const targetY = chartPanel.getBoundingClientRect().top + window.pageYOffset - topOffset;
                window.scrollTo({ top: Math.max(0, targetY), behavior: 'smooth' });
            };
            alignChartToTop();
            // Second check for mobile browsers after address bar / viewport reflow
            setTimeout(alignChartToTop, 160);

            chartPanel.classList.add('ring-4', 'ring-indigo-400', 'shadow-2xl', 'transition-all', 'duration-500');
            setTimeout(() => {
                chartPanel.classList.remove('ring-4', 'ring-indigo-400', 'shadow-2xl');
            }, 1400);
        }
    }, 340);

    if (selectedTicker === normalizedTicker) {
        rememberSelectedTicker(normalizedTicker);
        updateActiveTickerDisplay(normalizedTicker);
        return;
    }
    rememberSelectedTicker(normalizedTicker);
    resetCompletedCandleState();

    // Keep whatever timeframe and range were previously selected

    // Update header/search immediately and clear stale ticker-specific UI.
    showTickerChangeLoadingState(normalizedTicker);
    updateChartControlState(selectedTicker);

    const cachedChart = chartDataCache.get(chartCacheKey(normalizedTicker));
    if (cachedChart) {
        renderFastChartPayload(cachedChart, normalizedTicker);
    }

    syncChart();
    fetchLivePriceForActiveTicker();
    loadSelectedTickerChart({ force: true, renderCached: false });
    fetchDashboard('heavy', { queueIfBusy: true });
    fetchAIIndicator(normalizedTicker, currentBackendTf);
}

async function removeFromWatchlist(ticker) {
    try {
        const headers = await getAuthHeaders();
        const response = await fetch(`${API_BASE}/api/watchlist/${ticker}`, {
            method: 'DELETE',
            headers: headers
        });
        if (response.ok) {
            // Remove from local favorites as well for fallback consistency
            let favorites = JSON.parse(localStorage.getItem('userFavorites')) || [];
            favorites = favorites.filter(t => t !== ticker);
            localStorage.setItem('userFavorites', JSON.stringify(favorites));

            fetchDashboard('fast');
        }
    } catch (e) {
        console.error('Error removing from watchlist:', e);
    }
}

// ──────────────────────────────────────────────

// ──────────────────────────────────────────────
// 5. Render Trade Log
// ──────────────────────────────────────────────
let latestScanHistory = [];
let latestOrderHistory = [];
let latestPendingOrders = [];
let currentScanPage = 1;
let currentTradePage = 1;
let currentPendingPage = 1;
var LOG_PAGE_SIZE = 20;

function setLogTab(tab) {
    currentLogTab = tab;
    // Reset to first page when switching tabs
    if (tab === 'all') currentScanPage = 1;
    else if (tab === 'trades') currentTradePage = 1;
    else currentPendingPage = 1;

    // UI Update
    const allBtn = document.getElementById('tabLogAll');
    const tradesBtn = document.getElementById('tabLogTrades');

    if (allBtn && tradesBtn) {
        if (tab === 'all') {
            allBtn.className = "px-4 py-1.5 text-[0.6rem] font-black uppercase rounded-lg transition-all bg-white text-indigo-600 shadow-sm border border-indigo-100";
            tradesBtn.className = "px-4 py-1.5 text-[0.6rem] font-black uppercase rounded-lg transition-all text-indigo-400 hover:text-indigo-600";
        } else {
            tradesBtn.className = "px-4 py-1.5 text-[0.6rem] font-black uppercase rounded-lg transition-all bg-white text-indigo-600 shadow-sm border border-indigo-100";
            allBtn.className = "px-4 py-1.5 text-[0.6rem] font-black uppercase rounded-lg transition-all text-indigo-400 hover:text-indigo-600";
        }
    }

    renderTradeLog(latestScanHistory, latestOrderHistory, latestPendingOrders);
}

function renderTradeLog(scanHistory, orderHistory, pendingOrders) {
    const tbody = document.getElementById('tradeLogBody');
    const noMsg = document.getElementById('noTradesMsg');
    if (!tbody) return;

    tbody.innerHTML = '';
    const filterToggle = document.getElementById('filterLogToggle');
    const isFiltered = filterToggle && filterToggle.checked;

    let trades = currentLogTab === 'trades' ? (orderHistory || []) : (scanHistory || []);

    // Apply Filtering if enabled
    if (currentLogTab === 'all') {
        if (isFiltered && selectedTicker) {
            trades = trades.filter(t =>
                t.ticker === selectedTicker &&
                t.timeframe === currentBackendTf
            );
        }
    }

    const currentPage = currentLogTab === 'trades' ? currentTradePage : currentScanPage;

    if (!trades || trades.length === 0) {
        if (noMsg) {
            noMsg.style.display = 'block';
            noMsg.textContent = currentLogTab === 'trades' ? "No orders executed yet." : "Waiting for first scan...";
        }
        updatePaginationUI(0, 1);
        return;
    }
    if (noMsg) noMsg.style.display = 'none';

    // Calculate pagination
    const totalPages = Math.ceil(trades.length / LOG_PAGE_SIZE) || 1;
    const start = (currentPage - 1) * LOG_PAGE_SIZE;
    const end = start + LOG_PAGE_SIZE;
    const paginated = trades.slice(start, end);

    updatePaginationUI(totalPages, currentPage);

    paginated.forEach(trade => {
        const actionColor = trade.action === 'BUY'
            ? 'text-emerald-600 font-bold'
            : trade.action === 'SELL'
                ? 'text-red-500 font-bold'
                : 'text-purple-500 font-medium';

        const signalBadge = trade.exit_monitoring && trade.exit_signal_label
            ? trade.exit_signal_label
            : trade.bullish_count !== undefined
            ? `${trade.bullish_count}B / ${trade.bearish_count}S`
            : '—';

        const plVal = trade.pl !== undefined && trade.pl !== null ? Number(trade.pl) : null;
        const plPct = trade.pl_pct !== undefined && trade.pl_pct !== null ? Number(trade.pl_pct) : null;
        let plHtml = '<td class="py-2.5 pr-4 text-center text-[0.65rem] text-slate-300">—</td>';

        if (plVal !== null) {
            const plColor = plVal >= 0 ? 'text-emerald-500' : 'text-red-500';
            const sign = plVal >= 0 ? '+' : '';
            plHtml = `
                <td class="py-2.5 pr-4 text-center ${plColor} font-bold text-[0.65rem]">
                    <div>${sign}${plVal.toFixed(2)}</div>
                    <div class="text-[0.55rem] opacity-80">${sign}${plPct.toFixed(2)}%</div>
                </td>
            `;
        }

        const row = document.createElement('tr');
        row.className = 'border-b border-purple-100 fade-in';
        const safeTradeAction = escapeDashboardHtml(trade.action);
        const safeTradeTicker = escapeDashboardHtml(trade.ticker);
        const safeTradePrice = escapeDashboardHtml(trade.price);
        const safeTradeTime = escapeDashboardHtml(formatLocalTime(trade.time));
        const safeTradeTimeframe = escapeDashboardHtml(trade.timeframe || '5M');
        const safeTradeReason = escapeDashboardHtml(trade.reason);
        const safeSignalBadge = escapeDashboardHtml(signalBadge);
        row.innerHTML = `
            <td class="py-2.5 pr-4 text-purple-600 text-xs">${safeTradeTime}</td>
            <td class="py-2.5 pr-4 ${actionColor} text-xs">${safeTradeAction}</td>
            <td class="py-2.5 pr-4 text-indigo-950 font-semibold text-xs">${safeTradeTicker}</td>
            <td class="py-2.5 pr-4 text-center">
                <span class="px-1.5 py-0.5 rounded-md bg-indigo-50 text-indigo-500 font-black text-[0.55rem] border border-indigo-100">${safeTradeTimeframe}</span>
            </td>
            <td class="py-2.5 pr-4 text-indigo-700 font-medium text-xs">${safeTradePrice}</td>
            <td class="py-2.5 pr-4 text-center text-[0.65rem] font-bold text-indigo-900">${trade.qty && !isNaN(trade.qty) ? Number(trade.qty).toFixed(4) : (trade.qty || '—')}</td>
            <td class="py-2.5 pr-4 text-center text-[0.65rem] font-bold text-emerald-600">$${trade.total_cost ? Number(trade.total_cost).toFixed(2) : '—'}</td>
            <td class="py-2.5 pr-4 text-center text-[0.65rem] text-purple-400 font-mono">$${trade.fees ? Number(trade.fees).toFixed(2) : '0.00'}</td>
            ${plHtml}
            <td class="py-2.5 pr-4 text-xs">
                <span class="px-1.5 py-0.5 rounded bg-purple-100 text-purple-700 font-mono text-[0.6rem]">${safeSignalBadge}</span>
            </td>
            <td class="py-2.5 pr-4 text-purple-500 italic text-xs max-w-xs truncate" title="${safeTradeReason}">${safeTradeReason}</td>
        `;
        tbody.appendChild(row);
    });
}

function updatePaginationUI(totalPages, currentPage) {
    const indicator = document.getElementById('logPageIndicator');
    const prevBtn = document.getElementById('logPrevBtn');
    const nextBtn = document.getElementById('logNextBtn');

    if (!indicator || !prevBtn || !nextBtn) return;

    indicator.textContent = `Page ${currentPage} of ${Math.max(1, totalPages)}`;
    prevBtn.disabled = currentPage <= 1;
    nextBtn.disabled = currentPage >= totalPages;
}

function changeLogPage(delta) {
    if (currentLogTab === 'all') {
        const total = Math.ceil(latestScanHistory.length / LOG_PAGE_SIZE) || 1;
        currentScanPage = Math.max(1, Math.min(total, currentScanPage + delta));
    } else if (currentLogTab === 'trades') {
        const total = Math.ceil(latestOrderHistory.length / LOG_PAGE_SIZE) || 1;
        currentTradePage = Math.max(1, Math.min(total, currentTradePage + delta));
    } else {
        const total = Math.ceil(latestPendingOrders.length / LOG_PAGE_SIZE) || 1;
        currentPendingPage = Math.max(1, Math.min(total, currentPendingPage + delta));
    }
    renderTradeLog(latestScanHistory, latestOrderHistory, latestPendingOrders);
}

// ──────────────────────────────────────────────
// 6. Update Risk Panel
// ──────────────────────────────────────────────


// ──────────────────────────────────────────────
// 7. Update Position Sizing Panel
// ──────────────────────────────────────────────
function updateSizingPanel(scan) {
    const panel = document.getElementById('sizingPanel');
    if (!panel) return; // FIX: Prevent crash if panel is missing (e.g. on Dashboard after move)

    if (!scan || !scan.position_sizing || scan.action === 'HOLD') {
        panel.style.display = 'none';
        return;
    }

    panel.style.display = 'block';
    const sz = scan.position_sizing;
    document.getElementById('sizingNotional').textContent = `$${sz.notional?.toFixed(2) || '0.00'}`;
    document.getElementById('sizingShares').textContent = sz.shares?.toFixed(4) || '0';
    document.getElementById('sizingStop').textContent = `$${sz.stop_loss?.toFixed(2) || '0.00'}`;
    document.getElementById('sizingTarget').textContent = `$${sz.take_profit?.toFixed(2) || '0.00'}`;

    const rr = document.getElementById('sizingRR');
    if (rr) rr.textContent = scan.is_custom ? 'CUSTOM OVERRIDE ACTIVE' : `1:${sz.risk_reward_ratio || '1.5'}`;
    if (rr) rr.className = scan.is_custom ? 'font-black text-[0.6rem] text-amber-500 animate-pulse' : 'font-bold';
}


// ──────────────────────────────────────────────
// 8. Main Fetch Loop
// ──────────────────────────────────────────────
function scheduleDashboardHeartbeat(delayMs = REFRESH_INTERVAL) {
    if (!dashboardHeartbeatEnabled) return;
    if (dashboardHeartbeatTimer) clearTimeout(dashboardHeartbeatTimer);

    dashboardHeartbeatTimer = setTimeout(async () => {
        dashboardHeartbeatTimer = null;
        if (
            shouldRunDashboardRefresh()
            && !window._indicatorToggleDebounce
            && !window._dashboardRefreshInFlight
        ) {
            await fetchDashboard('heavy');
        }
        if (!dashboardHeartbeatTimer && dashboardHeartbeatEnabled) {
            scheduleDashboardHeartbeat();
        }
    }, Math.max(1000, Number(delayMs) || REFRESH_INTERVAL));
}

function stopDashboardHeartbeat() {
    if (dashboardHeartbeatTimer) clearTimeout(dashboardHeartbeatTimer);
    dashboardHeartbeatTimer = null;
    dashboardHeartbeatEnabled = false;
}

function dashboardRefreshIsRecent(maxAgeMs = DASHBOARD_VISIBILITY_REFRESH_MIN_AGE_MS) {
    return lastDashboardRefreshCompletedAt > 0
        && Date.now() - lastDashboardRefreshCompletedAt < maxAgeMs;
}

async function fetchDashboard(mode = 'heavy', options = {}) {
    if (!shouldRunDashboardRefresh()) {
        return;
    }
    if (window._dashboardRefreshInFlight) {
        if (options.queueIfBusy) {
            pendingDashboardRefreshMode = mode;
        }
        return;
    }
    window._dashboardRefreshInFlight = true;
    try {
        const requestedTicker = normalizeTicker(selectedTicker);
        const url = requestedTicker
            ? `${API_BASE}/api/dashboard?ticker=${encodeURIComponent(requestedTicker)}&timeframe=${encodeURIComponent(currentBackendTf)}&mode=${encodeURIComponent(mode)}&source=dashboard&include_primary_analysis=false`
            : `${API_BASE}/api/dashboard?mode=${mode}&timeframe=${encodeURIComponent(currentBackendTf)}&source=dashboard&include_primary_analysis=false`;

        const headers = await getAuthHeaders();
        // Removed redundant header log

        const response = await fetchWithTimeout(url, { headers }, DASHBOARD_FETCH_TIMEOUT_MS);
        if (!response.ok) {
            const errText = await response.text();
            console.error(`[dashboard] HTTP Error: ${response.status} - ${errText}`);
            showGlobalError(`Security Error: ${response.status}. Please refresh or re-login.`);
            return;
        }
        const data = await response.json();
        lastDashboardRefreshCompletedAt = Date.now();
        const responseTicker = normalizeTicker(data.primaryTicker);
        const activeTicker = normalizeTicker(selectedTicker);
        const responseDataTicker = responseTicker || requestedTicker;
        const isStaleTickerResponse = Boolean(activeTicker && responseDataTicker && responseDataTicker !== activeTicker);
        if (isStaleTickerResponse) {
            console.debug(`[dashboard] Ignoring stale ${responseDataTicker} response; current selection is ${activeTicker}.`);
            return;
        }

        // Sync Timeframe from Backend
        if (data.strategyTimeframe) {
            // Strategy timeframe can differ from the chart interval; keep chart controls user-driven.
            updateChartControlState(selectedTicker);
        }

        window.AlpacaStatusCache?.writeFromData(data);
        window.AlpacaStatusCache?.render();

        // Log diagnostic info from backend
        if (data.debug_logs && data.debug_logs.length > 0) {
            // Diagnostic logs silenced as requested
        }

        // Set default selected ticker
        if (!selectedTicker && data.watchlist?.length > 0) {
            selectedTicker = normalizeTicker(data.primaryTicker || data.watchlist[0]);
            
            // Try to mirror global AI analysis if it exists
            let mirrored = false;
            try {
                const aiContext = typeof getAIIndicatorPageContext === 'function' ? getAIIndicatorPageContext() : 'dashboard';
                const globalAiStr = localStorage.getItem(`latest_ai_analysis:${aiContext}`);
                if (globalAiStr) {
                    const globalAi = JSON.parse(globalAiStr);
                    const selectedModel = typeof getSelectedAIModelForContext === 'function'
                        ? getSelectedAIModelForContext(aiContext)
                        : normalizeSelectedAIModel(null);
                    if (isAICacheForContext(globalAi, selectedTicker, currentBackendTf, selectedModel)) {
                        renderAIIndicatorCard(globalAi.data, true);
                        if (typeof window.onAIIndicatorUpdated === 'function') {
                            window.onAIIndicatorUpdated(globalAi.data, true);
                        }
                        mirrored = true;
                    }
                }
            } catch (e) {}

            if (!mirrored) {
                fetchAIIndicator(selectedTicker, currentBackendTf);
            }
        }

        // Cache the first watchlist ticker for instant use on next load
        if (data.watchlist?.length > 0) {
            try {
                localStorage.setItem('watchlistFirstTicker', normalizeTicker(data.watchlist[0]) || DEFAULT_TICKER);
            } catch (e) { /* ignore storage errors */ }
        }

        // Alpaca Status Pill & Action Toggle removed (migrated to active bots)
        const dashContent = document.getElementById('dashboardContent');

        const summaryEl = document.getElementById('aiSummary');
        const cardSentSummary = document.getElementById('sentimentSummary');
        const aiHeaderEl = document.getElementById('aiIndicatorHeader');

        if (aiHeaderEl) {
            aiHeaderEl.textContent = `BotBulls News AI — ${selectedTicker}`;
        }

        if (summaryEl) {
            if (data.sentiment_summary) {
                summaryEl.innerHTML = `
                    <div class="mb-3">
                        <span class="text-[10px] uppercase tracking-wider text-amber-600 font-black block mb-1">🔥 Key Market Factor</span>
                        <p class="text-amber-950 font-bold not-italic text-sm leading-snug">${escapeDashboardHtml(data.sentiment_key_factor || 'Mixed Catalysts')}</p>
                    </div>
                    <div class="space-y-2">
                        <span class="text-[10px] uppercase tracking-wider text-orange-600 font-black block mb-1">📰 AI News Briefing & Catalyst Summary</span>
                        <p class="text-amber-900 font-medium not-italic leading-relaxed text-xs">${escapeDashboardHtml(data.sentiment_summary)}</p>
                    </div>
                    <div class="mt-4 pt-3 border-t border-amber-200/50 flex justify-between items-center text-[10px] font-black uppercase text-amber-700">
                         <span>Analysis Confidence Index</span>
                        <span class="font-extrabold text-xs text-orange-600 bg-orange-100 px-2 py-0.5 rounded border border-orange-200 shadow-sm">${Math.max(0, Math.min(100, Math.round(Number(data.sentiment_confidence) * 100) || 0))}%</span>
                    </div>
                `;
                summaryEl.classList.remove('italic');
                if (cardSentSummary) cardSentSummary.textContent = data.sentiment_summary;
            } else {
                const renderedCachedAI = renderCachedAIIndicatorForTicker(selectedTicker, currentBackendTf);
                if (!renderedCachedAI) {
                    summaryEl.textContent = `Waiting for next ${selectedTicker} scan analysis...`;
                    if (cardSentSummary) cardSentSummary.textContent = `Fetching latest news for ${selectedTicker}...`;
                }
            }
        }

        if (dashContent) dashContent.classList.remove('hidden');
        // Portfolio stats removed from dashboard (migrated to active bots)
        const sentEl = document.getElementById('aiSentiment');

        // Generic market-context cards are refreshed through the shared cache loop below.

        // Update new Macro, Breadth, and Sector trading cards
        updateTradingCards(data);
        const posLabel = document.getElementById('positionsList');
        if (posLabel) {
            if (data.positions?.length > 0) {
                posLabel.textContent = data.positions.map(p => p.symbol).join(', ');
            } else {
                posLabel.textContent = 'No positions';
            }
        }

        // Mode indicator
        const modeEl = document.getElementById('modeIndicator');
        if (modeEl) {
            if (data.simulation) {
                modeEl.innerHTML = '<span class="h-2.5 w-2.5 bg-amber-500 rounded-full mr-2 animate-pulse"></span> Simulation Mode';
            } else {
                modeEl.innerHTML = '<span class="h-2.5 w-2.5 bg-green-500 rounded-full mr-2 animate-pulse"></span> Live Trading';
            }
        }

        // Last scan
        const lastScanEl = document.getElementById('lastScanTime');
        if (lastScanEl) lastScanEl.textContent = formatLocalTime(data.lastScan) || '—';

        // Sync Strategy TF buttons
        document.querySelectorAll('#chartTfBar .chart-tf-btn').forEach(btn => {
            btn.classList.toggle('chart-tf-active', btn.dataset.tf === currentBackendTf);
        });
        updateChartControlState(selectedTicker);

        // Store watchlist for star state
        window._currentWatchlist = data.watchlist || [];

        // If no ticker selected yet, pick the primary one from backend, falling back to SPY
        if (!selectedTicker) {
            selectedTicker = normalizeTicker(data.primaryTicker)
                || normalizeTicker(data.watchlist?.[0])
                || DEFAULT_TICKER;
        }

        // Update chart label + star state. Chart candles, overlays, and
        // confluence signals are all owned by /api/chart.
        if (selectedTicker) {
            updateActiveTickerDisplay(selectedTicker);
            _updateStarState();
        }

        // Watchlist scans are price-only. Full signals belong to the chart's primary ticker.
        const activeScan = (
            !isStaleTickerResponse &&
            normalizeTicker(data.primaryTicker) === selectedTicker
        ) ? data.primaryScan : null;

        // Keep the chart-derived scan while refreshing dashboard/account data.
        // The dashboard request explicitly omits primary analysis to avoid a
        // duplicate indicator calculation every 60 seconds.
        const previousDashboardData = window.lastDashboardData || {};
        const primaryAnalysisIncluded = data.primaryAnalysisIncluded !== false;
        const previousAnalysisMatchesTicker = (
            normalizeTicker(previousDashboardData.primaryTicker) === normalizeTicker(data.primaryTicker)
        );
        window.lastDashboardData = primaryAnalysisIncluded
            ? data
            : {
                ...previousDashboardData,
                ...data,
                primaryScan: previousAnalysisMatchesTicker
                    ? (previousDashboardData.primaryScan || data.primaryScan)
                    : data.primaryScan,
                signals: previousAnalysisMatchesTicker
                    ? (previousDashboardData.signals || {})
                    : {},
                priceHistory: previousAnalysisMatchesTicker
                    ? (previousDashboardData.priceHistory || [])
                    : [],
            };

        // Signals
        if (primaryAnalysisIncluded && activeScan && mode !== 'fast') {
            const freshSignals = activeScan.signals || data.signals || {};
            if (normalizeTicker(data.primaryTicker) === normalizeTicker(selectedTicker)) {
                window._lastSignalsTicker = normalizeTicker(selectedTicker);
            }
            if (Object.keys(freshSignals).length) {
                window._lastSignals = freshSignals;
                renderSignals(
                    freshSignals,
                    activeScan.action || data.action || 'HOLD',
                    activeScan.reason || data.reason || '',
                    activeScan.bullish_count ?? data.bullish_count ?? 0,
                    activeScan.bearish_count ?? data.bearish_count ?? 0,
                );
            }
            updateSizingPanel(activeScan);
        }

        // Watchlist
        renderWatchlist(data.watchlistScans, data.watchlist);

        if (data.indicator_parameters) {
            const macdFirst = data.indicator_parameters.MACD_FAST || 12;
            const macdSecond = data.indicator_parameters.MACD_SLOW || 26;
            const macdFast = Math.min(macdFirst, macdSecond);
            const macdSlow = Math.max(macdFirst, macdSecond);
            const macdSig = data.indicator_parameters.MACD_SIGNAL || 9;
            const macdLabel = document.getElementById('macdLabel');
            if (macdLabel) {
                macdLabel.innerHTML = `
                    MACD (${macdFast}, ${macdSlow}, ${macdSig})
                    <svg class="w-3 h-3 opacity-0 group-hover:opacity-100 transition-opacity" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M10.325 4.317c.426-1.756 2.924-1.756 3.35 0a1.724 1.724 0 002.573 1.066c1.543-.94 3.31.826 2.37 2.37a1.724 1.724 0 001.065 2.572c1.756.426 1.756 2.924 0 3.35a1.724 1.724 0 00-1.066 2.573c.94 1.543-.826 3.31-2.37 2.37a1.724 1.724 0 00-2.572 1.065c-.426 1.756-2.924 1.756-3.35 0a1.724 1.724 0 00-2.573-1.066c-1.543.94-3.31-.826-2.37-2.37a1.724 1.724 0 00-1.065-2.572c-1.756-.426-1.756-2.924 0-3.35a1.724 1.724 0 001.066-2.573c-.94-1.543.826-3.31 2.37-2.37.996.608 2.296.07 2.572-1.065z"></path><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M15 12a3 3 0 11-6 0 3 3 0 016 0z"></path></svg>
                `;
            }
        }

        // Trade log
        latestScanHistory = data.recentTrades || [];
        const latestOrderHistory = data.orderHistory || [];
        const latestPendingOrders = data.pendingOrders || [];
        renderTradeLog(latestScanHistory, latestOrderHistory, latestPendingOrders);

        // Update Performance Panel
        if (data.performance) {
            const dashboardMs = document.getElementById('perfDashboardMs');
            const evalMs = document.getElementById('perfEvalMs');
            const cacheStatus = document.getElementById('perfCache');
            const perfUpdated = document.getElementById('perfUpdated');
            const quoteMs = document.getElementById('perfQuoteMs');
            const accountMs = document.getElementById('perfAccountMs');
            const orderMs = document.getElementById('perfOrderMs');

            if (dashboardMs) dashboardMs.textContent = `${Math.round(data.performance.total_ms || 0)}ms`;
            if (evalMs) evalMs.textContent = `${Math.round(data.performance.eval_ms || 0)}ms`;
            if (cacheStatus) cacheStatus.textContent = data.performance.cached ? 'HIT' : 'MISS';
            if (quoteMs) quoteMs.textContent = `${Math.round(data.performance.quote_ms || 0)}ms`;
            if (accountMs) accountMs.textContent = `${Math.round(data.performance.account_ms || 0)}ms`;
            if (orderMs) orderMs.textContent = `${Math.round(data.performance.order_history_ms || 0)}ms`;
            if (perfUpdated) perfUpdated.textContent = new Date().toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit' });
        }

    } catch (error) {
        if (isRequestTimeout(error) || isRequestAbort(error)) {
            console.warn(`[dashboard] Refresh skipped: ${error.message || error.name}`);
            return;
        }

        console.error('[dashboard] Fetch error:', error);
        const lastScanEl = document.getElementById('lastScanTime');
        if (lastScanEl) lastScanEl.textContent = 'Connection error';

        // On mobile, show a more intrusive alert for connectivity issues to help debug
        if (window.innerWidth < 768) {
            alert(`Dashboard Connection Failed: ${error.message}\n\nTroubleshooting:\n1. Check your internet.\n2. Disable Mobile VPN/Private Relay.\n3. Try a Hard Refresh.`);
        }

        showGlobalError('Cannot connect to the trading backend.');
    } finally {
        window._dashboardRefreshInFlight = false;
        const queuedMode = pendingDashboardRefreshMode;
        pendingDashboardRefreshMode = null;
        if (queuedMode && shouldRunDashboardRefresh()) {
            setTimeout(() => fetchDashboard(queuedMode, { queueIfBusy: true }), 0);
        } else if (dashboardHeartbeatEnabled) {
            scheduleDashboardHeartbeat();
        }
    }
}

async function fetchTickerChart(ticker, signals) {
    const requestedTicker = normalizeTicker(ticker);
    if (!requestedTicker) return;
    return loadTickerChartFast(requestedTicker, { renderCached: true });
}

// ──────────────────────────────────────────────
// 9. Event Listeners (attached in DOMContentLoaded below)
// ──────────────────────────────────────────────
function attachEventListeners() {
    // Setup ticker search (Rich Dropdown)
    const tickerSearch = document.getElementById('tickerSearch');
    let dashboardSearchTimeout = null;

    if (tickerSearch) {
        tickerSearch.addEventListener('input', (e) => {
            const query = e.target.value.trim();
            const resultsContainer = document.getElementById('tickerSearchResults');

            if (query.length < 2) {
                resultsContainer.classList.add('hidden');
                return;
            }

            clearTimeout(dashboardSearchTimeout);
            dashboardSearchTimeout = setTimeout(async () => {
                try {
                    const headers = await getAuthHeaders();
                    const res = await fetch(`${API_BASE}/api/search/${encodeURIComponent(query)}`, { headers });
                    const data = await res.json();

                    resultsContainer.innerHTML = '';
                    if (data && data.length > 0) {
                        data.forEach(item => {
                            const div = document.createElement('div');
                            div.className = "p-3 hover:bg-indigo-50 cursor-pointer border-b border-indigo-50 last:border-0 transition-colors flex justify-between items-center";
                            const details = document.createElement('div');
                            details.className = 'flex flex-col';
                            const symbol = document.createElement('span');
                            symbol.className = 'font-black text-indigo-900';
                            symbol.textContent = item.symbol || '';
                            const name = document.createElement('span');
                            name.className = 'text-[0.65rem] text-indigo-400 font-medium truncate w-40';
                            name.textContent = item.name || '';
                            details.append(symbol, name);
                            const action = document.createElement('span');
                            action.className = 'text-[0.6rem] bg-indigo-100 text-indigo-500 px-2 py-0.5 rounded uppercase font-bold tracking-widest';
                            action.textContent = '+ View';
                            div.append(details, action);
                            div.onclick = () => {
                                selectTickerFromSearch(item.symbol);
                            };
                            resultsContainer.appendChild(div);
                        });
                        resultsContainer.classList.remove('hidden');
                    } else {
                        resultsContainer.innerHTML = '<div class="p-3 text-center text-xs text-indigo-400 font-medium">No assets found</div>';
                        resultsContainer.classList.remove('hidden');
                    }
                } catch (e) {
                    console.error('[search] Error fetching results:', e);
                }
            }, 300); // 300ms debounce
        });

        // Close search dropdown on click outside
        document.addEventListener('click', (e) => {
            if (!e.target.closest('#tickerSearch') && !e.target.closest('#tickerSearchResults')) {
                document.getElementById('tickerSearchResults')?.classList.add('hidden');
            }
        });
    }

    const favoriteBtn = document.getElementById('favoriteBtn');
    if (favoriteBtn) {
        favoriteBtn.addEventListener('click', async () => {
            if (!selectedTicker) return;

            // Check current active watchlist from cache
            const currentWatchlist = window.lastDashboardData?.watchlist || [];
            const isRemoving = currentWatchlist.includes(selectedTicker);

            try {
                const headers = await getAuthHeaders();
                if (isRemoving) {
                    await fetch(`${API_BASE}/api/watchlist/${selectedTicker}`, {
                        method: 'DELETE',
                        headers: headers
                    });
                    window.lastDashboardData.watchlist = currentWatchlist.filter(t => t !== selectedTicker);
                } else {
                    await fetch(`${API_BASE}/api/watchlist`, {
                        method: 'POST',
                        headers: headers,
                        body: JSON.stringify({ ticker: selectedTicker })
                    });
                    window.lastDashboardData.watchlist.push(selectedTicker);
                }

                updateFavoriteIcon();
                fetchDashboard('fast');
            } catch (e) {
                console.error('Error toggling favorite:', e);
            }
        });
    }
}

function updateFavoriteIcon() {
    const icon = document.getElementById('favIcon');
    if (!icon || !selectedTicker) return;

    // Check current active watchlist from cache
    const currentWatchlist = window.lastDashboardData?.watchlist || [];

    if (currentWatchlist.includes(selectedTicker)) {
        icon.setAttribute('fill', 'currentColor');
        icon.classList.add('text-yellow-500');
    } else {
        icon.setAttribute('fill', 'none');
        icon.classList.remove('text-yellow-500');
    }
}

// ──────────────────────────────────────────────
// 10. Settings & Toggles
window._indicatorToggleDebounce = null;
window._dashboardRefreshInFlight = false;
async function toggleIndicator(key, value) {
    if (DASHBOARD_STANDARD_TOGGLE_KEYS.has(key)) {
        const indicatorName = DASHBOARD_STANDARD_INDICATOR_NAMES.find(
            name => INDICATOR_TOGGLE_KEYS[name] === key
        );
        if (indicatorName) {
            setGlobalIndicatorPref(indicatorName, value === true);
            return;
        }
    }
    try {
        const headers = await getAuthHeaders();
        await fetchWithTimeout(`${API_BASE}/api/settings/indicators?context=dashboard`, {
            method: 'POST',
            headers: headers,
            body: JSON.stringify({ [key]: value })
        }, 8000);

        // Persisting a disable is enough. Do not request a new chart/signal
        // calculation for an indicator the user just turned off.
        if (value !== true) {
            clearTimeout(window._indicatorToggleDebounce);
            window._indicatorToggleDebounce = null;
            return;
        }

        clearTimeout(window._indicatorToggleDebounce);
        window._indicatorToggleDebounce = setTimeout(() => {
            if (typeof loadSelectedTickerChart === 'function') {
                loadSelectedTickerChart({ force: true, renderCached: false });
            } else {
                refreshActiveTickerSignals({ force: true });
            }
            window._indicatorToggleDebounce = null; // Unblock the heartbeat
        }, 1000);
    } catch (e) {
        console.error('Error saving setting:', e);
    }
}

async function updateStrategyTf(newTf) {
    try {
        currentBackendTf = newTf;
        localStorage.setItem('lastChartTf', newTf);
        if (window.lastDashboardData) {
            window.lastDashboardData.watchlistScans = {};
            window.lastDashboardData.botScans = {};
            window.lastDashboardData.signals = {};
        }

        // Show loader immediately
        lockSignalConfluencePanelHeight();
        setSignalConfluenceLoadingState(true);

        const headers = await getAuthHeaders();
        const response = await fetch(`${API_BASE}/api/settings/timeframe`, {
            method: 'POST',
            headers: headers,
            body: JSON.stringify({ timeframe: newTf })
        });

        if (response.ok) {
            console.log(`[settings] Timeframe updated to ${newTf}`);

            syncChart();
            loadSelectedTickerChart({ force: true, renderCached: false });
            fetchDashboard('fast');
        }
    } catch (e) {
        console.error('Error updating timeframe:', e);
    }
}

// ──────────────────────────────────────────────
// 11. Init
// ──────────────────────────────────────────────
document.addEventListener('DOMContentLoaded', () => {
    // Protect against accidental duplicate script injection (for example a
    // hot reload or a page shell that includes app.js twice). Without this,
    // each copy creates its own auth poll, heartbeat, and refresh timers.
    if (window.__botBullsDashboardInitStarted) {
        console.debug('[dashboard] Initialization already started; skipping duplicate init.');
        return;
    }
    window.__botBullsDashboardInitStarted = true;
    console.log('[dashboard] Initializing components...');
    setChartLoadingState(true, selectedTicker);
    clearLegacyChartLocalStorageCaches();
    window.AlpacaStatusCache?.render();
    renderCachedGenericDashboardCards();
    renderCachedAIIndicatorForTicker(selectedTicker, currentBackendTf);

    // Attach event listeners first (DOM is now ready)
    attachEventListeners();
    syncChart();

    // PHASE 0: Instant cached render (no network, no auth needed)
    if (selectedTicker) {
        console.log(`[dashboard] Phase 0 — Instant chart render from cache for: ${selectedTicker}`);
        loadSelectedTickerChart({ renderCached: true, fetchFresh: false });
    }

    // Wait for auth — use 50ms interval for near-instant startup
    const checkAuth = setInterval(async () => {
        if (window.auth && (window.auth.currentUser || localStorage.getItem('dev_mode') === 'true')) {
            clearInterval(checkAuth);
            console.log('[dashboard] Auth ready. Starting phased data stream.');

            // The workspace controls the visible indicator grid, not the
            // provider fetch. Start chart loading while the settings sync is
            // in flight so a cold login does not wait on a settings round trip.
            console.log('[dashboard] Phase 0.5 — Synchronizing Dashboard indicator workspace in parallel...');
            const workspaceWarmup = hydrateDashboardIndicatorWorkspace();

            // PHASE 1: Chart candles first — highest priority
            let chartWarmup = null;
            if (selectedTicker) {
                console.log(`[dashboard] Phase 1 — Fetching completed candles for: ${selectedTicker}`);
                // A startup refresh is still a network request, but it may
                // reuse the backend's short-lived chart cache. Explicit user
                // refreshes and timeframe changes continue to use force=true.
                chartWarmup = loadSelectedTickerChart({ force: false, renderCached: false, fetchFresh: true });
            }
            // Do not let the heartbeat's immediate tick launch a second chart
            // refresh while the Phase 1 warmup is still in flight. The backend
            // coalesces duplicate work, but waiting here avoids the duplicate
            // request and the transient 404 it can produce with no cache yet.
            let chartHeartbeatStarted = false;
            const startChartHeartbeat = () => {
                if (chartHeartbeatStarted) return;
                chartHeartbeatStarted = true;
                startActiveChartHeartbeat();
            };
            if (chartWarmup && typeof chartWarmup.then === 'function') {
                chartWarmup.then(startChartHeartbeat, startChartHeartbeat);
            } else {
                startChartHeartbeat();
            }
            dashboardHeartbeatEnabled = shouldRunDashboardRefresh();
            if (dashboardHeartbeatEnabled) scheduleDashboardHeartbeat();

            // PHASE 2: Dashboard shell/watchlist. Fast mode does not calculate
            // primary technical analysis, so it can start while the chart is
            // waiting on a cold provider response.
            console.log('[dashboard] Phase 2 — Scheduling fast dashboard scan...');
            let dashboardWarmupStarted = false;
            const startDashboardWarmup = () => {
                if (dashboardWarmupStarted || !shouldRunDashboardRefresh()) return;
                dashboardWarmupStarted = true;
                fetchDashboard('fast');
            };
            const dashboardWarmupTimer = setTimeout(startDashboardWarmup, chartWarmup ? 250 : 0);
            if (chartWarmup && typeof chartWarmup.finally === 'function') {
                chartWarmup.finally(() => {
                    clearTimeout(dashboardWarmupTimer);
                    startDashboardWarmup();
                });
            }

            workspaceWarmup.catch(error => {
                console.warn('[indicator-workspace] Parallel startup sync failed; local preferences remain active.', error);
            });

            // We intentionally do NOT await the warmups so the UI does not block if one of them hangs.
            // PHASE 3: Remaining background data. The fast Dashboard response
            // already contains the complete shell, so do not immediately
            // download the same payload again in heavy mode.
            setTimeout(async () => {
                if (!shouldRunDashboardRefresh()) return;
                console.log('[dashboard] Phase 3 — Loading background data...');
                const runBackground = async () => {
                    if (!shouldRunDashboardRefresh()) return;
                    fetchGenericDashboardCards();
                };
                if (window.requestIdleCallback) {
                    window.requestIdleCallback(runBackground, { timeout: 1500 });
                } else {
                    setTimeout(runBackground, 0);
                }
                if (selectedTicker && shouldRunDashboardRefresh()) fetchAIIndicator(selectedTicker, currentBackendTf);
            }, 2500);

            setInterval(() => {
                if (!shouldRunDashboardRefresh()) return;
                fetchGenericDashboardCards();
            }, GENERIC_DASHBOARD_CARDS_REFRESH_MS);
        }
    }, 50);

    document.addEventListener('visibilitychange', () => {
        if (shouldRunDashboardRefresh()) {
            dashboardHeartbeatEnabled = true;
            if (!dashboardRefreshIsRecent() && !window._dashboardRefreshInFlight) {
                fetchDashboard('fast');
            } else {
                scheduleDashboardHeartbeat();
            }
            fetchGenericDashboardCards();
            fetchLivePriceForActiveTicker();
            refreshCompletedCandlesIfDue();
        } else {
            stopDashboardHeartbeat();
        }
    });

    // Backtest Timeframe Listener
    const btTfSelect = document.getElementById('btTimeframe');
    const btDaysInput = document.getElementById('btDays');
    if (btTfSelect && btDaysInput) {
        btTfSelect.addEventListener('change', () => {
            const tf = btTfSelect.value;
            if (tf === '1Min') btDaysInput.value = 7;
            else if (['5Min', '15Min', '30Min'].includes(tf)) btDaysInput.value = 60;
            else if (tf === '1Hour') btDaysInput.value = 365;
            else if (tf === '4Hour') btDaysInput.value = 730;
            else if (tf === '1Day') btDaysInput.value = 1825;
        });
    }

    // Backtest Indicator Listener for Dynamic Slider
    document.querySelectorAll('.bt-indicator-check').forEach(cb => {
        cb.addEventListener('change', syncBacktestSliderRange);
    });

    console.log('[dashboard] Bot Bulls Dashboard initialized.');
});

function showGlobalError(msg) {
    const errEl = document.getElementById('globalError');
    const msgEl = document.getElementById('errorMessage');
    if (errEl && msgEl) {
        errEl.classList.remove('hidden');
        msgEl.textContent = msg;
    }
}
// ──────────────────────────────────────────────
// 12. Backtesting
// ──────────────────────────────────────────────
function openBacktestModal() {
    document.getElementById('backtestModal').classList.remove('hidden');
    document.getElementById('btTicker').value = selectedTicker || "MSFT";
    
    const savedBuyThreshold = localStorage.getItem('lastBotBuyThreshold');
    if (savedBuyThreshold) {
        const inp = document.getElementById('btThreshold');
        const valSpan = document.getElementById('btThresholdVal');
        if (inp) inp.value = savedBuyThreshold;
        if (valSpan) valSpan.textContent = savedBuyThreshold;
    }
    
    const savedSellThreshold = localStorage.getItem('lastBotSellThreshold');
    if (savedSellThreshold) {
        const inp = document.getElementById('btSellThreshold');
        const valSpan = document.getElementById('btSellThresholdVal');
        if (inp) inp.value = savedSellThreshold;
        if (valSpan) valSpan.textContent = savedSellThreshold;
    }
    
    resetBacktestUI(); // Ensure settings are visible
    syncBacktestSliderRange(); // Initial sync of slider range
}

function closeBacktestModal() {
    document.getElementById('backtestModal').classList.add('hidden');
}

function resetBacktestUI() {
    document.getElementById('btSettingsContainer').classList.remove('hidden');
    document.getElementById('btResults').classList.add('hidden');
    document.getElementById('btLoading').classList.add('hidden');
}

async function runBacktest() {
    const ticker = document.getElementById('btTicker').value.trim().toUpperCase();
    const timeframe = document.getElementById('btTimeframe').value;
    const days = document.getElementById('btDays').value;
    const capital = document.getElementById('btCapital').value;
    const threshold = document.getElementById('btThreshold').value;
    const sell_threshold = document.getElementById('btSellThreshold').value;

    const indicators = Array.from(document.querySelectorAll('.bt-indicator-check:checked')).map(cb => cb.value);

    const btn = document.getElementById('runBtBtn');
    const loading = document.getElementById('btLoading');
    const results = document.getElementById('btResults');
    const settings = document.getElementById('btSettingsContainer');

    btn.disabled = true;
    settings.classList.add('hidden'); // Hide settings while loading
    loading.classList.remove('hidden');
    results.classList.add('hidden');

    try {
        const headers = await getAuthHeaders();
        const response = await fetch(`${API_BASE}/api/backtest`, {
            method: 'POST',
            headers: headers,
            body: JSON.stringify({ ticker, timeframe, days, capital, threshold, sell_threshold, indicators })
        });

        const data = await response.json();

        if (data.error) {
            alert("Backtest failed: " + data.error);
            loading.classList.add('hidden');
            btn.disabled = false;
            return;
        }

        // Render Results
        // Render Results with Color Coding
        const roiEl = document.getElementById('resRoi');
        roiEl.textContent = `${data.summary.roi_pct}%`;
        roiEl.className = `text-2xl font-black ${data.summary.roi_pct >= 0 ? 'text-emerald-600' : 'text-red-500'}`;

        const wrEl = document.getElementById('resWinRate');
        wrEl.textContent = `${data.summary.win_rate_pct}%`;
        wrEl.className = `text-2xl font-black ${data.summary.win_rate_pct >= 50 ? 'text-emerald-600' : 'text-indigo-600'}`;

        const tradesEl = document.getElementById('resTrades');
        tradesEl.textContent = data.summary.total_trades;

        const equityEl = document.getElementById('resEquity');
        equityEl.textContent = `$${data.summary.final_equity}`;
        equityEl.className = `text-2xl font-black ${data.summary.final_equity >= data.summary.initial_capital ? 'text-indigo-900' : 'text-red-900'}`;

        // Comparison
        document.getElementById('resHoldEquity').textContent = `$${data.summary.buy_hold_equity}`;
        document.getElementById('resHoldRoi').textContent = `${data.summary.buy_hold_roi_pct}% Market Return`;

        // Setup Pagination and Render Trades
        btAllTrades = data.trades;
        btCurrentPage = 0;
        renderBtTradesPage();

        results.classList.remove('hidden');
    } catch (e) {
        console.error('Backtest error:', e);
        alert('Backtest failed. Check console for details.');
    } finally {
        loading.classList.add('hidden');
        btn.disabled = false;
    }
}

async function bulkDownloadTicker() {
    const ticker = document.getElementById('btTicker').value.trim().toUpperCase();
    if (!ticker) return alert("Please enter a ticker symbol");

    const btn = event.currentTarget;
    const originalHtml = btn.innerHTML;
    btn.disabled = true;
    btn.innerHTML = '<div class="h-4 w-4 border-2 border-indigo-600 border-t-transparent rounded-full animate-spin"></div>';

    try {
        const response = await fetch(`${API_BASE}/api/download_all`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ ticker })
        });
        const data = await response.json();

        if (data.error) throw new Error(data.error);

        alert(`Bulk Download Complete for ${ticker}!\n\n${data.status.join('\n')}`);
    } catch (e) {
        console.error('Bulk download error:', e);
        alert('Bulk download failed: ' + e.message);
    } finally {
        btn.disabled = false;
        btn.innerHTML = originalHtml;
    }
}

// --- Pagination Logic for Backtest ---
let btAllTrades = [];
let btCurrentPage = 0;
const BT_PAGE_SIZE = 10;

function renderBtTradesPage() {
    const logBody = document.getElementById('btTradeLog');
    const pageInfo = document.getElementById('btPageInfo');
    const pageInfoTop = document.getElementById('btPageInfoTop');
    const pagination = document.getElementById('btPagination');

    if (!btAllTrades || !btAllTrades.length) {
        if (pagination) pagination.classList.add('hidden');
        return;
    }

    if (pagination) pagination.classList.remove('hidden');
    const totalPages = Math.ceil(btAllTrades.length / BT_PAGE_SIZE);
    const infoText = `Page ${btCurrentPage + 1} of ${totalPages} (${btAllTrades.length} trades)`;

    if (pageInfo) pageInfo.innerText = infoText;
    if (pageInfoTop) pageInfoTop.innerText = infoText;

    const start = btCurrentPage * BT_PAGE_SIZE;
    const end = start + BT_PAGE_SIZE;
    const pageTrades = btAllTrades.slice(start, end);

    logBody.innerHTML = pageTrades.map(t => {
        const isWin = t.pl >= 0;
        const plColor = isWin ? 'text-emerald-600' : 'text-rose-600';
        const plBg = isWin ? 'bg-emerald-50' : 'bg-rose-50';
        const safeEntryTime = escapeDashboardHtml(t.entry_time);
        const safeExitTime = escapeDashboardHtml(t.exit_time);
        const safeEntryPrice = escapeDashboardHtml(t.entry_price);
        const safeExitPrice = escapeDashboardHtml(t.exit_price);
        const safeQty = escapeDashboardHtml(t.qty || '—');
        const safePlPct = escapeDashboardHtml(t.pl_pct);
        const safeEntryCost = escapeDashboardHtml(t.entry_cost ? Number(t.entry_cost).toFixed(2) : '—');
        const safeExitValue = escapeDashboardHtml(t.exit_value ? Number(t.exit_value).toFixed(2) : '—');
        const safeFees = escapeDashboardHtml(t.fees ? Number(t.fees).toFixed(2) : '0.00');
        const safeReason = escapeDashboardHtml(t.reason);

        return `
            <tr class="hover:bg-indigo-50/30 transition-colors">
                <td class="p-4 font-medium text-indigo-900">${safeEntryTime}</td>
                <td class="p-4 font-medium text-indigo-400">${safeExitTime}</td>
                <td class="p-4 font-black text-indigo-900 tracking-tight">$${safeEntryPrice} → $${safeExitPrice}</td>
                <td class="p-4 text-center text-indigo-900 font-bold">${safeQty}</td>
                <td class="p-4 text-center text-indigo-600 font-medium">$${safeEntryCost}</td>
                <td class="p-4 text-center text-emerald-600 font-bold">$${safeExitValue}</td>
                <td class="p-4 text-center text-purple-400 font-mono text-[0.6rem]">$${safeFees}</td>
                <td class="p-4 text-center">
                    <span class="inline-block px-2 py-1 rounded-md ${plBg} ${plColor} font-black text-[0.7rem] min-w-[60px]">
                        ${isWin ? '+' : ''}${safePlPct}%
                    </span>
                </td>
                <td class="p-4 text-[0.65rem] font-bold text-indigo-400 uppercase tracking-tighter">${safeReason}</td>
            </tr>
        `;
    }).join('');
}

function changeBtPage(delta) {
    const totalPages = Math.ceil(btAllTrades.length / BT_PAGE_SIZE);
    const newPage = btCurrentPage + delta;

    if (newPage >= 0 && newPage < totalPages) {
        btCurrentPage = newPage;
        renderBtTradesPage();
    }
}
// ──────────────────────────────────────────────
// 13. Backtest Aggressiveness Logic
// ──────────────────────────────────────────────
function syncBacktestSliderRange() {
    const checkedCount = document.querySelectorAll('.bt-indicator-check:checked').length;
    const slider = document.getElementById('btAggressiveSlider');
    const label = document.getElementById('btAggressiveLabel');

    if (checkedCount === 0) {
        slider.min = 0;
        slider.max = 0;
        slider.value = 0;
        slider.disabled = true;
        label.textContent = "Select Indicators First";
        label.className = "text-[0.75rem] font-black px-3 py-1 rounded-full bg-rose-500 text-white uppercase shadow-md";
        const buyInp = document.getElementById('btThreshold');
        const sellInp = document.getElementById('btSellThreshold');
        if (buyInp) buyInp.value = 0;
        if (sellInp) sellInp.value = 0;
        return;
    }

    slider.disabled = false;
    slider.min = 0;
    slider.max = checkedCount;

    // If current value is higher than new max, cap it
    if (parseInt(slider.value) > checkedCount) {
        slider.value = checkedCount;
    }

    updateBtAggressiveness(slider.value);
}

function updateBtAggressiveness(val) {
    const slider = document.getElementById('btAggressiveSlider');
    const max = parseInt(slider.max) || 0;
    const label = document.getElementById('btAggressiveLabel');
    const buyInp = document.getElementById('btThreshold');
    const sellInp = document.getElementById('btSellThreshold');

    val = parseInt(val);
    if (buyInp) buyInp.value = val;
    if (sellInp) sellInp.value = val;

    const pct = max > 0 ? Math.round((val / max) * 100) : 0;
    let mode = "Balanced";
    let colorClass = "bg-indigo-600";

    if (val === 0) {
        mode = "Always Trade";
        colorClass = "bg-gray-500";
    } else if (pct <= 34) {
        mode = "Aggressive";
        colorClass = "bg-emerald-600";
    } else if (pct >= 75) {
        mode = "Quality";
        colorClass = "bg-purple-600";
    }

    if (val === max && max > 1) {
        mode = "Ultra-Quality";
        colorClass = "bg-indigo-900";
    }

    label.textContent = `${mode} (${val} of ${max} signals)`;
    label.className = `text-[0.75rem] font-black px-3 py-1 rounded-full ${colorClass} text-white uppercase shadow-md`;
}

// ──────────────────────────────────────────────
// 11. Emergency Shutdown
// ──────────────────────────────────────────────
async function emergencyShutdown() {
    if (!confirm("☢️ WARNING: This will DEACTIVATE the entire trading engine (Local and Cloud). The bot will remain dormant until manually restarted. Continue?")) return;

    try {
        const response = await fetch(`${API_BASE}/api/bot/shutdown`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' }
        });
        const data = await response.json();
        if (data.status === 'success') {
            alert("SYSTEM OFFLINE: Trading engine has been deactivated. You must manually set ENGINE_ACTIVE to true in settings.json to restart.");
            window.location.reload();
        }
    } catch (e) {
        console.error('Shutdown error:', e);
        alert("Failed to send shutdown command.");
    }
}

// ──────────────────────────────────────────────
// Alpaca functions moved to bots.js

// ──────────────────────────────────────────────
// 13. Onboarding Guide Logic
// ──────────────────────────────────────────────
function openGuideModal() {
    document.getElementById('guideModal').classList.remove('hidden');
}

function closeGuideModal() {
    document.getElementById('guideModal').classList.add('hidden');
}

// ──────────────────────────────────────────────
// 14. Indicator Settings Logic
// ──────────────────────────────────────────────
function openIndicatorSettings(indicatorName) {
    currentEditingIndicator = indicatorName;
    const configKeys = INDICATOR_CONFIG_MAP[indicatorName] || [];
    const container = document.getElementById('indicatorModalContent');
    const title = document.getElementById('indicatorModalTitle');


    const tooltipHtml = `<div class="group/tooltip relative inline-flex items-center ml-2 align-middle">
        <svg onclick="event.stopPropagation(); event.preventDefault(); typeof openInfoModal === 'function' ? openInfoModal('${indicatorName}') : null" xmlns="http://www.w3.org/2000/svg" class="h-4 w-4 text-indigo-200 hover:text-white cursor-pointer transition-colors" fill="none" viewBox="0 0 24 24" stroke="currentColor">
            <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M13 16h-1v-4h-1m1-4h.01M21 12a9 9 0 11-18 0 9 9 0 0118 0z" />
        </svg>
        <div class="invisible group-hover/tooltip:visible opacity-0 group-hover/tooltip:opacity-100 transition-all absolute top-full left-1/2 -translate-x-1/2 mt-2 px-3 py-1.5 bg-white text-indigo-900 text-[0.65rem] leading-snug rounded-lg shadow-2xl z-[999] pointer-events-none whitespace-nowrap text-center font-bold normal-case tracking-normal border border-indigo-100">
            Click to read guide
            <div class="absolute bottom-full left-1/2 -translate-x-1/2 border-4 border-transparent border-b-white"></div>
        </div>
    </div>`;
    title.innerHTML = `${indicatorName} Settings ${tooltipHtml}`;

    container.innerHTML = '';

    if (configKeys.length === 0) {
        container.innerHTML = '<p class="text-xs text-slate-500 italic">No adjustable parameters for this indicator.</p>';
    }

    const currentParams = window.lastDashboardData?.indicator_parameters || {};
    const overrides = window.lastDashboardData?.indicator_overrides || {};
    const paramLabels = {
        ...(window.STANDARD_INDICATOR_PARAM_LABELS || {}),
        EMA_USE_SECOND_LINE: 'Use Two EMA Lines',
        EMA_FAST: 'Primary EMA Period',
        EMA_SLOW: 'Secondary EMA Period',
        SMA_USE_SECOND_LINE: 'Use Two SMA Lines',
        SMA_PERIOD: 'Primary SMA Period',
        SMA_SECOND_PERIOD: 'Secondary SMA Period',
        DAILY_HIGH_LOW_BUFFER_PCT: 'Breakout Buffer (%)',
        PIVOT_LEVEL_COUNT: 'S/R Levels To Draw (1-5)',
        PIVOT_TOLERANCE_PCT: 'Reclaim/Rejection Tolerance (%)',
        VOLUME_PROFILE_ROWS: 'Profile Rows / Price Bins',
        VOLUME_PROFILE_VALUE_AREA_PCT: 'Value Area (%)',
        VOLUME_PROFILE_TOLERANCE_PCT: 'Signal Tolerance (%)',
        VOLUME_PROFILE_CONFIRMATION_CANDLES: 'Acceptance Confirmation Candles',
        SWING_POINTS_LOOKBACK_DAYS: 'Swing Lookback Days',
        SWING_POINTS_ENTRY_MODE: 'Swing Entry Mode',
        SWING_POINTS_LEVEL_COUNT: 'S/R Levels To Draw (1-5)',
    };

    configKeys.forEach(key => {
        const hasOverride = overrides[key] !== undefined && overrides[key] !== null && overrides[key] !== '';
        const val = currentParams[key] !== undefined ? currentParams[key] : (INDICATOR_DEFAULTS[key] !== undefined ? INDICATOR_DEFAULTS[key] : '');
        const isLineToggle = key === 'EMA_USE_SECOND_LINE' || key === 'SMA_USE_SECOND_LINE';
        const toggleChecked = val === true || val === 1 || String(val).toLowerCase() === 'true' || String(val) === '1';
        const label = paramLabels[key] || key.replace(/_/g, ' ').toLowerCase();
        const isPercent = key.endsWith('_PCT') || key.endsWith('_PERCENTAGE');
        const inputAttrs = (key === 'PIVOT_LEVEL_COUNT'
            ? 'min="1" max="5" step="1"'
            : key === 'SWING_POINTS_LEVEL_COUNT'
                ? 'min="1" max="5" step="1"'
                : key === 'SWING_POINTS_LOOKBACK_DAYS'
                    ? 'min="1" max="365" step="1"'
            : key === 'VOLUME_PROFILE_ROWS'
                ? 'min="12" max="120" step="1"'
                : key === 'VOLUME_PROFILE_CONFIRMATION_CANDLES'
                    ? 'min="1" max="5" step="1"'
                    : (window.STANDARD_INDICATOR_INPUT_ATTRS?.[key] || 'step="any"'));

        const div = document.createElement('div');
        div.className = 'flex flex-col gap-1.5';
        const controlHtml = isLineToggle
            ? `<label class="flex items-center justify-between gap-3 w-full bg-slate-50 border border-slate-200 rounded-lg px-3 py-2 cursor-pointer">
                    <span class="text-xs font-bold text-indigo-900">Checked: two-line crossover · Unchecked: price vs primary line</span>
                    <input type="checkbox" data-key="${key}" data-default="${INDICATOR_DEFAULTS[key] ? 1 : 0}" ${toggleChecked ? 'checked' : ''}
                        class="w-5 h-5 accent-indigo-600 rounded">
                </label>`
            : key === 'SWING_POINTS_ENTRY_MODE'
            ? `<select data-key="${key}" data-default="${INDICATOR_DEFAULTS[key] !== undefined ? INDICATOR_DEFAULTS[key] : ''}"
                    class="w-full bg-slate-50 border border-slate-200 rounded-lg px-3 py-2 text-sm font-bold text-indigo-900 focus:ring-2 focus:ring-indigo-500 focus:border-transparent outline-none transition-all">
                    <option value="bounce_retest" ${val === 'bounce_retest' ? 'selected' : ''}>Bounce / Retest</option>
                    <option value="breakout_retest" ${val === 'breakout_retest' ? 'selected' : ''}>Breakout / Retest</option>
                </select>`
            : `<input type="number" ${inputAttrs} data-key="${key}" value="${val}" placeholder="${INDICATOR_DEFAULTS[key] !== undefined ? INDICATOR_DEFAULTS[key] : ''}"
                    class="w-full bg-slate-50 border border-slate-200 rounded-lg px-3 ${isPercent ? 'pr-8' : ''} py-2 text-sm font-bold text-indigo-900 focus:ring-2 focus:ring-indigo-500 focus:border-transparent outline-none transition-all">
                ${isPercent ? '<span class="absolute right-3 top-1/2 -translate-y-1/2 text-xs font-black text-indigo-400">%</span>' : ''}`;
        div.innerHTML = `
            <div class="flex justify-between items-center mb-0.5">
                <label class="text-[0.65rem] font-black text-indigo-950 uppercase tracking-widest opacity-60">${label}</label>
                ${!hasOverride ? '<span class="text-[0.55rem] font-extrabold text-emerald-500 uppercase tracking-widest bg-emerald-50 px-1.5 py-0.5 rounded-md">System Default</span>' : ''}
            </div>
            <div class="relative">
                ${controlHtml}
            </div>
        `;
        container.appendChild(div);
    });

    window.wireMovingAverageLineToggles?.(container);

    document.getElementById('indicatorSettingsModal').classList.remove('hidden');
}

function closeIndicatorSettings() {
    document.getElementById('indicatorSettingsModal').classList.add('hidden');
    currentEditingIndicator = null;
}

function resetIndicatorSettingsToDefaults() {
    if (!currentEditingIndicator) return;
    const controls = document.querySelectorAll('#indicatorModalContent input, #indicatorModalContent select');
    controls.forEach(control => {
        if (control.type === 'checkbox') {
            control.checked = String(control.dataset.default) === '1';
            control.dispatchEvent(new Event('change'));
        } else {
            control.value = control.tagName === 'SELECT' ? (control.dataset.default || '') : control.placeholder; // Revert to system default
        }
        const container = control.closest('.flex-col');
        if (container) {
            const header = container.querySelector('.flex.justify-between.items-center');
            if (header) {
                const badge = header.querySelector('span');
                if (!badge) {
                    header.insertAdjacentHTML('beforeend', '<span class="text-[0.55rem] font-extrabold text-emerald-500 uppercase tracking-widest bg-emerald-50 px-1.5 py-0.5 rounded-md">System Default</span>');
                }
            }
        }
    });
}

function applyIndicatorSettingUpdatesLocally(updates) {
    if (!window.lastDashboardData) window.lastDashboardData = {};
    const params = {
        ...(window.lastDashboardData.indicator_parameters || {})
    };
    const overrides = {
        ...(window.lastDashboardData.indicator_overrides || {})
    };

    Object.entries(updates || {}).forEach(([key, rawValue]) => {
        if (rawValue === '' || rawValue === null || rawValue === undefined) {
            delete overrides[key];
            if (INDICATOR_DEFAULTS[key] !== undefined) {
                params[key] = INDICATOR_DEFAULTS[key];
            } else {
                delete params[key];
            }
            return;
        }

        const defaultValue = INDICATOR_DEFAULTS[key];
        const numericValue = Number(rawValue);
        const value = Number.isFinite(numericValue) ? numericValue : rawValue;
        params[key] = value;
        if (defaultValue !== undefined && String(value) === String(defaultValue)) {
            delete overrides[key];
        } else {
            overrides[key] = value;
        }
    });

    window.lastDashboardData.indicator_parameters = params;
    window.lastDashboardData.indicator_overrides = overrides;
}

async function saveIndicatorSettings() {
    const inputs = document.querySelectorAll('#indicatorModalContent input, #indicatorModalContent select');
    const invalidInput = Array.from(inputs).find(input => input.tagName === 'INPUT' && !input.checkValidity());
    if (invalidInput) {
        invalidInput.reportValidity();
        return;
    }
    const updates = {};
    inputs.forEach(input => {
        if (input.type === 'checkbox') {
            const value = input.checked ? 1 : 0;
            const defaultValue = String(input.dataset.default) === '1' ? 1 : 0;
            updates[input.dataset.key] = value === defaultValue ? "" : value;
            return;
        }
        const defaultValue = input.tagName === 'SELECT' ? input.dataset.default : input.placeholder;
        if (input.value === defaultValue || input.value === "") {
            updates[input.dataset.key] = "";
        } else {
            updates[input.dataset.key] = input.value;
        }
    });

    if (Object.keys(updates).length === 0) {
        closeIndicatorSettings();
        return;
    }

    try {
        const headers = await getAuthHeaders();
        const response = await fetchWithTimeout(`${API_BASE}/api/settings/indicators?context=dashboard`, {
            method: 'POST',
            headers: headers,
            body: JSON.stringify(updates)
        }, 8000);

        if (response.ok) {
            console.log(`[settings] Updated settings for ${currentEditingIndicator}`);
            applyIndicatorSettingUpdatesLocally(updates);
            closeIndicatorSettings();
            // Recalculate current confluence and chart indicators without hitting broad dashboard/broker endpoints.
            if (typeof loadSelectedTickerChart === 'function') {
                loadSelectedTickerChart({ force: true, renderCached: false });
            } else {
                refreshActiveTickerSignals({ force: true });
            }
        } else {
            alert("Error saving indicator settings.");
        }
    } catch (err) {
        console.error(err);
        alert("Backend communication error.");
    }
}

// ──────────────────────────────────────────────
// Fear & Greed Integration and Trading Cards
// ──────────────────────────────────────────────

function updateTradingCards(data) {
    if (!data || !data.watchlistScans) return;

    // Market breadth is intentionally updated only from /api/market-data.
    // It must remain market-context data, not portfolio/watchlist/bot-profile data.
    if (!window._marketBreadthLoaded) {
        setBreadthSummaryCard(null);
    }
}

function setBreadthSummaryCard(breadth) {
    const breadthScoreEl = document.getElementById('breadthScore');
    const breadthLabelEl = document.getElementById('breadthLabel');
    const breadthSubLabelEl = document.getElementById('breadthSubLabel');
    const breadthProgressEl = document.getElementById('breadthProgress');
    if (!breadthScoreEl || !breadthProgressEl || !breadthLabelEl) return;

    const total = Number(breadth?.advancing || 0) + Number(breadth?.declining || 0);
    const analyzedCount = Number(breadth?.analyzed_count || breadth?.tickers?.length || 0);
    if (!breadth || analyzedCount <= 0) {
        breadthScoreEl.textContent = `--%`;
        breadthProgressEl.setAttribute('stroke-dasharray', `0, 100`);
        breadthProgressEl.className.baseVal = "text-slate-300";
        breadthLabelEl.textContent = "Loading Market...";
        breadthLabelEl.className = "text-xs font-bold text-slate-500 mt-2 uppercase tracking-wide";
        if (breadthSubLabelEl) breadthSubLabelEl.textContent = "Composite breadth score";
        return;
    }

    const breadthPct = Number.isFinite(Number(breadth.breadth_score))
        ? Number(breadth.breadth_score)
        : Number.isFinite(Number(breadth.advance_pct))
            ? Number(breadth.advance_pct)
        : total > 0
            ? Math.round((Number(breadth.advancing || 0) / total) * 100)
            : 50;
    breadthScoreEl.textContent = `${breadthPct}%`;
    breadthProgressEl.setAttribute('stroke-dasharray', `${breadthPct}, 100`);
    if (breadthSubLabelEl) breadthSubLabelEl.textContent = "Composite breadth score";

    if (breadthPct >= 65) {
        breadthProgressEl.className.baseVal = "text-emerald-500";
        breadthLabelEl.textContent = "Healthy Breadth";
        breadthLabelEl.className = "text-xs font-bold text-emerald-600 mt-2 uppercase tracking-wide";
    } else if (breadthPct <= 45) {
        breadthProgressEl.className.baseVal = "text-red-500";
        breadthLabelEl.textContent = "Weak Breadth";
        breadthLabelEl.className = "text-xs font-bold text-red-600 mt-2 uppercase tracking-wide";
    } else {
        breadthProgressEl.className.baseVal = "text-yellow-500";
        breadthLabelEl.textContent = "Mixed Breadth";
        breadthLabelEl.className = "text-xs font-bold text-yellow-600 mt-2 uppercase tracking-wide";
    }
}
let lastFngScore = null;
let lastFngComponents = [];
let lastFngFetch = 0;

function applyFearAndGreedData(data) {
    const score = data?.score ?? data?.raw_score?.score;
    if (score === undefined || score === null) return false;
    lastFngScore = Number(score);
    lastFngComponents = data.components || data.raw_score?.components || [];
    lastFngFetch = Date.now();
    updateFearAndGreedUI(lastFngScore);
    return true;
}

function readCachedGenericDashboardCards() {
    try {
        const cached = JSON.parse(sessionStorage.getItem(GENERIC_DASHBOARD_CARDS_CACHE_KEY) || 'null');
        if (!cached || !cached.payload || !cached.fetchedAt) return null;
        if (cached.payload.market_data && cached.payload.market_data.sector_summary) {
            const techAnalyzed = (cached.payload.market_data.sector_summary.tech || {}).analyzed_count || 0;
            if (techAnalyzed < 24) return null;
        }
        return cached;
    } catch (e) {
        console.warn('[dashboard] Could not read generic cards cache:', e);
        return null;
    }
}

function writeCachedGenericDashboardCards(payload) {
    try {
        sessionStorage.setItem(GENERIC_DASHBOARD_CARDS_CACHE_KEY, JSON.stringify({
            payload,
            fetchedAt: Date.now()
        }));
    } catch (e) {
        console.warn('[dashboard] Could not save generic cards cache:', e);
    }
}

async function applyGenericDashboardCardsPayload(payload) {
    if (!payload) return false;
    if (payload.fear_greed) applyFearAndGreedData(payload.fear_greed);
    if (payload.market_data) await fetchMarketData(payload.market_data);
    if (payload.macro_events) await fetchMacroEvents(payload.macro_events);
    return Boolean(payload.fear_greed || payload.market_data || payload.macro_events);
}

async function renderCachedGenericDashboardCards(maxAgeMs = Infinity) {
    const cached = readCachedGenericDashboardCards();
    if (!cached) return false;
    if (Date.now() - cached.fetchedAt > maxAgeMs) return false;
    return applyGenericDashboardCardsPayload(cached.payload);
}

async function fetchFearAndGreed() {
    const now = Date.now();
    // Browser reads the server-wide hourly cache; keep a short client cache to avoid duplicate paints.
    if (lastFngScore !== null && (now - lastFngFetch < 60000)) {
        updateFearAndGreedUI(lastFngScore);
        return;
    }
    try {
        const headers = await getAuthHeaders();
        const res = await fetch(`${API_BASE}/api/fear-greed`, { headers });
        if (!res.ok) return;
        const data = await res.json();
        applyFearAndGreedData(data);
    } catch (e) {
        console.error("Failed to fetch Fear and Greed:", e);
    }
}

async function fetchGenericDashboardCards(options = {}) {
    if (!shouldRunDashboardRefresh()) {
        return;
    }
    if (genericDashboardCardsRequestPromise) {
        return genericDashboardCardsRequestPromise;
    }
    const { allowRecentCache = true, paintCachedFirst = true } = options;
    const cached = readCachedGenericDashboardCards();
    if (cached && paintCachedFirst) {
        await applyGenericDashboardCardsPayload(cached.payload);
    }
    if (
        allowRecentCache
        && cached
        && Date.now() - cached.fetchedAt < GENERIC_DASHBOARD_CARDS_CLIENT_TTL_MS
    ) {
        return;
    }

    const requestPromise = (async () => {
        try {
            const headers = await getAuthHeaders();
            const req = await fetchWithTimeout(
                `${API_BASE}/api/generic-dashboard-cards`,
                { headers },
                GENERIC_CARDS_FETCH_TIMEOUT_MS,
            );
            if (!req.ok) throw new Error(`Generic card cache request failed (${req.status})`);
            const payload = await req.json();
            writeCachedGenericDashboardCards(payload);
            await applyGenericDashboardCardsPayload(payload);
            // The server answers within a bound; a card still warming up arrives as null, so load just that one.
            if (!payload.fear_greed) fetchFearAndGreed();
            if (!payload.market_data) fetchMarketData();
            if (!payload.macro_events) fetchMacroEvents();
            return payload;
        } catch (e) {
            console.warn("[dashboard] Combined generic-card cache failed; falling back to individual cached endpoints.", e);
            fetchFearAndGreed();
            fetchMarketData();
            fetchMacroEvents();
            return null;
        }
    })();
    genericDashboardCardsRequestPromise = requestPromise;
    try {
        return await requestPromise;
    } finally {
        if (genericDashboardCardsRequestPromise === requestPromise) {
            genericDashboardCardsRequestPromise = null;
        }
    }
}

function updateFearAndGreedUI(score) {
    // Needle rotation: 0=far left (-90°), 100=far right (+90°)
    const angle = (score / 100) * 180 - 90;

    // Rotate the SVG <g> needle group around the arc center (100, 100)
    const needleG = document.getElementById('fgNeedleSvg');
    if (needleG) {
        needleG.setAttribute('transform', `rotate(${angle}, 100, 100)`);
    }

    // Update score number
    const scoreEl = document.getElementById('fgScore');
    if (scoreEl) {
        scoreEl.textContent = score;
    }

    // Generate tick marks (once)
    const ticksG = document.getElementById('fngTicks');
    if (ticksG && ticksG.childNodes.length === 0) {
        const cx = 100, cy = 100, r = 85;
        for (let i = 0; i <= 50; i++) {
            const angleDeg = 180 + (i / 50) * 180;
            const rad = angleDeg * Math.PI / 180;
            const isMajor = (i % 5 === 0);
            const tickLen = isMajor ? 8 : 4;
            const x1 = cx + (r - 9) * Math.cos(rad);
            const y1 = cy + (r - 9) * Math.sin(rad);
            const x2 = cx + (r - 9 - tickLen) * Math.cos(rad);
            const y2 = cy + (r - 9 - tickLen) * Math.sin(rad);
            const line = document.createElementNS('http://www.w3.org/2000/svg', 'line');
            line.setAttribute('x1', x1);
            line.setAttribute('y1', y1);
            line.setAttribute('x2', x2);
            line.setAttribute('y2', y2);
            line.setAttribute('stroke', 'rgba(0,0,0,0.45)');
            line.setAttribute('stroke-width', isMajor ? '1.5' : '0.8');
            ticksG.appendChild(line);
        }
    }

    // Determine label and color based on score
    let label = "Neutral";
    let fillColor = "#eab308"; // yellow
    if (score <= 25) { label = "Extreme Fear"; fillColor = "#22c55e"; }
    else if (score <= 45) { label = "Fear"; fillColor = "#84cc16"; }
    else if (score <= 55) { label = "Neutral"; fillColor = "#eab308"; }
    else if (score <= 75) { label = "Greed"; fillColor = "#f97316"; }
    else { label = "Extreme Greed"; fillColor = "#ef4444"; }

    // Update the SVG label text
    const labelEl = document.getElementById('fgLabel');
    if (labelEl) {
        labelEl.textContent = label;
        labelEl.setAttribute('fill', fillColor);
    }
}

function openFngModal() {
    const modal = document.getElementById('fngModal');
    const grid = document.getElementById('fngComponentsGrid');
    if (!modal || !grid) return;

    grid.innerHTML = '';

    if (lastFngComponents.length === 0) {
        grid.innerHTML = '<p class="col-span-full text-center text-slate-400 py-10 font-medium">Indicator data not available yet. Please wait for the initial fetch.</p>';
    } else {
        lastFngComponents.forEach(comp => {
            let label = "Neutral";
            let color = "bg-yellow-500";
            if (comp.val <= 25) { label = "Extreme Fear"; color = "bg-emerald-500"; }
            else if (comp.val <= 45) { label = "Fear"; color = "bg-lime-500"; }
            else if (comp.val <= 55) { label = "Neutral"; color = "bg-yellow-500"; }
            else if (comp.val <= 75) { label = "Greed"; color = "bg-orange-500"; }
            else { label = "Extreme Greed"; color = "bg-red-500"; }

            grid.innerHTML += `
                <div class="card-glass p-3 sm:p-5 flex flex-col justify-between hover:ring-2 hover:ring-indigo-400 transition-all overflow-hidden relative group">
                    <div class="card-glow"></div>
                    <div class="relative z-10">
                        <div class="flex items-center justify-between gap-2 mb-1.5 sm:mb-3">
                        <h4 class="font-black text-slate-700 text-xs sm:text-[13px] tracking-wide">${escapeDashboardHtml(comp.name)}</h4>
                            <span class="${color} text-white text-[9px] sm:text-[10px] px-2 sm:px-2.5 py-0.5 sm:py-1 rounded-md font-bold uppercase tracking-wider sm:tracking-widest shadow-sm shrink-0">${label}</span>
                        </div>
                        <p class="text-[11px] sm:text-xs text-slate-500 mb-1.5 sm:mb-2 leading-snug sm:leading-relaxed">${escapeDashboardHtml(comp.desc)}</p>
                        <p class="text-[10px] text-slate-400 font-mono bg-slate-50/50 backdrop-blur-sm px-2 py-1 rounded border border-slate-200 inline-block truncate max-w-full">${escapeDashboardHtml(comp.raw)}</p>
                    </div>
                    <div class="mt-2.5 sm:mt-5 relative z-10">
                        <div class="flex justify-between items-end text-[10px] text-slate-400 font-bold mb-1.5">
                            <span>Fear</span>
                            <span class="text-slate-800 text-lg font-black leading-none">${comp.val}</span>
                            <span>Greed</span>
                        </div>
                        <div class="w-full bg-slate-100 rounded-full h-2 overflow-hidden shadow-inner">
                            <div class="${color} h-2 rounded-full transition-all duration-1000 ease-out" style="width: ${comp.val}%"></div>
                        </div>
                    </div>
                </div>
            `;
        });
    }

    modal.classList.remove('hidden');
}

function openMacroModal() {
    const modal = document.getElementById('macroModal');
    if (modal) {
        switchMacroTab('calendar');
        modal.classList.remove('hidden');
        if (!window._macroDataLoaded) {
            fetchMacroEvents();
        }
    }
}

function closeMacroModal() {
    const modal = document.getElementById('macroModal');
    if (modal) modal.classList.add('hidden');
}

function openSectorModal() {
    const modal = document.getElementById('sectorModal');
    if (modal) modal.classList.remove('hidden');
}

function closeSectorModal() {
    const modal = document.getElementById('sectorModal');
    if (modal) modal.classList.add('hidden');
}

function switchSectorTab(sector) {
    const sectors = ['tech', 'financials', 'healthcare', 'energy', 'crypto'];
    sectors.forEach(s => {
        const tab = document.getElementById(`tab-${s}`);
        const view = document.getElementById(`view-${s}`);

        if (s === sector) {
            if (tab) tab.className = 'pb-2.5 sm:pb-3 px-1 sm:px-0 text-[10.5px] sm:text-sm font-bold border-b-2 border-indigo-600 text-indigo-700 transition-colors whitespace-nowrap';
            if (view) view.classList.remove('hidden');
        } else {
            if (tab) tab.className = 'pb-2.5 sm:pb-3 px-1 sm:px-0 text-[10.5px] sm:text-sm font-bold border-b-2 border-transparent text-slate-500 hover:text-slate-700 transition-colors whitespace-nowrap';
            if (view) view.classList.add('hidden');
        }
    });
}

function openBreadthModal() {
    const modal = document.getElementById('breadthModal');
    if (modal) modal.classList.remove('hidden');
}

function closeBreadthModal() {
    const modal = document.getElementById('breadthModal');
    if (modal) modal.classList.add('hidden');
}

async function fetchMarketData(cachedData = null) {
    if (!shouldRunDashboardRefresh()) {
        return;
    }
    try {
        let data = cachedData;
        if (data && (
            (data.sector_summary && data.sector_summary.tech && data.sector_summary.tech.analyzed_count < 24) ||
            (data.sectors && data.sectors['UBER'] === undefined)
        )) {
            data = null;
        }
        if (!data) {
            const headers = await getAuthHeaders();
            const req = await fetch(`${API_BASE}/api/market-data`, {
                headers: headers
            });
            if (!req.ok) return;
            data = await req.json();
        }

        // Update Sector Heatmap Modal (Dynamic Webull-Style Grid)
        if (data.sectors) {
            // Calculate actual mathematical averages for the main dashboard card
            const techList = ["AAPL", "MSFT", "GOOG", "NVDA", "META", "TSLA", "AVGO", "AMD", "QCOM", "NFLX", "AMZN", "ADBE", "CRM", "INTC", "CSCO", "ORCL", "IBM", "UBER", "ABNB", "PLTR", "MU", "TXN", "NOW", "SHOP"];
            const cryptoList = ["BTC-USD", "ETH-USD", "SOL-USD", "ADA-USD", "XRP-USD", "DOGE-USD", "DOT-USD", "LINK-USD", "LTC-USD", "NEAR-USD", "BCH-USD", "AVAX-USD"];
            const energyList = ["XOM", "CVX", "COP", "OXY", "SLB", "CAT", "GE", "HON", "UNP", "LMT", "DE", "MMM"];
            const financialsList = ["JPM", "BAC", "WFC", "MS", "GS", "V", "MA", "AXP", "BLK", "C", "SCHW", "HDB"];
            const healthcareList = ["LLY", "UNH", "JNJ", "MRK", "ABBV", "PFE", "WMT", "COST", "PG", "KO", "PEP", "NKE", "MCD", "EL"];

            const calcAvg = (list) => {
                let sum = 0;
                let count = 0;
                list.forEach(t => {
                    if (data.sectors[t] !== undefined) {
                        sum += data.sectors[t];
                        count++;
                    }
                });
                return count > 0 ? (sum / count) : 0.0;
            };

            const techAvg = calcAvg(techList);
            const cryptoAvg = calcAvg(cryptoList);
            const energyAvg = calcAvg(energyList);
            const financialsAvg = calcAvg(financialsList);
            const healthcareAvg = calcAvg(healthcareList);

            const mainSectorContainer = document.getElementById('sectorHeatmapContainer');
            if (mainSectorContainer) {
                const getPill = (avg) => {
                    const isUp = avg >= 0;
                    const pillColor = isUp ? 'bg-emerald-100 text-emerald-700' : 'bg-red-100 text-red-700';
                    return `<span class="px-1.5 py-0.5 rounded text-[10px] font-bold ${pillColor}">${isUp ? '+' : ''}${avg.toFixed(2)}%</span>`;
                };
                const row = (label, avg) => `
                    <div class="flex items-center justify-between gap-2 leading-tight">
                        <span class="text-base sm:text-sm font-bold text-slate-600 truncate">${label}</span>
                        ${getPill(avg)}
                    </div>
                `;

                mainSectorContainer.innerHTML = `
                    ${row('Technology', techAvg)}
                    ${row('Financials', financialsAvg)}
                    ${row('Healthcare & Retail', healthcareAvg)}
                    ${row('Energy & Industrials', energyAvg)}
                    ${row('Crypto', cryptoAvg)}
                `;
            }

            const SECTOR_TICKERS = {
                tech: techList,
                financials: ["JPM", "BAC", "WFC", "MS", "GS", "V", "MA", "AXP", "BLK", "C", "SCHW", "HDB"],
                healthcare: ["LLY", "UNH", "JNJ", "MRK", "ABBV", "PFE", "WMT", "COST", "PG", "KO", "PEP", "NKE", "MCD", "EL"],
                energy: energyList,
                crypto: cryptoList
            };

            const TICKER_WEIGHTS = {
                // Mega-Caps (Weight 3 - Large Mosaic Tiles: 2x2)
                "AAPL": 3, "MSFT": 3, "GOOG": 3, "NVDA": 3, "AMZN": 3,
                "BTC-USD": 3, "ETH-USD": 3,
                "JPM": 3, "LLY": 3, "XOM": 3,

                // Large-Caps (Weight 2 - Wide Rectangle Tiles: 2x1)
                "META": 2, "TSLA": 2, "AVGO": 2, "NFLX": 2, "ADBE": 2,
                "BAC": 2, "V": 2, "MA": 2,
                "UNH": 2, "COST": 2, "PG": 2,
                "CVX": 2, "SOL-USD": 2
            };

            const renderSectorSummaryCard = (summary) => {
                if (!summary) return '';
                const benchmarkMove = Number(summary.benchmark_change_pct);
                const avgMove = Number(summary.avg_constituent_change_pct || 0);
                const participation = Number(summary.participation_pct || 0);
                const isBenchmarkUp = Number.isFinite(benchmarkMove) ? benchmarkMove >= 0 : avgMove >= 0;
                const moveText = Number.isFinite(benchmarkMove)
                    ? `${benchmarkMove >= 0 ? '+' : ''}${benchmarkMove.toFixed(2)}%`
                    : 'N/A';
                const moveClass = isBenchmarkUp ? 'text-emerald-600 bg-emerald-50 border-emerald-100' : 'text-red-600 bg-red-50 border-red-100';
                const participationClass = participation >= 60
                    ? 'text-emerald-600'
                    : participation <= 40
                        ? 'text-red-600'
                        : 'text-amber-600';
                return `
                    <div class="col-span-full rounded-xl border border-slate-200 bg-white/90 px-2.5 py-1.5 sm:px-3 sm:py-2 shadow-sm">
                        <div class="flex items-center justify-between gap-1 sm:gap-2">
                            <p class="text-[10px] sm:text-[11px] font-black text-slate-800 uppercase tracking-wide leading-tight truncate mr-1">${escapeDashboardHtml(summary.label || 'Sector')} Summary</p>
                            <div class="flex items-center gap-1 sm:gap-1.5 shrink-0">
                                <span class="text-[8.5px] sm:text-[9.5px] font-bold text-slate-400 uppercase tracking-widest">Proxy: <strong class="text-slate-700 font-black">${escapeDashboardHtml(summary.benchmark || '--')}</strong></span>
                                <span class="px-1.5 py-0.5 rounded border text-[8.5px] sm:text-[9.5px] font-black leading-none ${moveClass}">${moveText}</span>
                            </div>
                        </div>
                        <div class="mt-1 sm:mt-2 grid grid-cols-3 gap-1 sm:gap-2 text-center">
                            <div>
                                <p class="text-[8px] sm:text-[9px] font-black text-slate-400 uppercase leading-none">Positive</p>
                                <p class="text-xs sm:text-sm font-black text-emerald-600 mt-0.5">${summary.positive_count ?? 0}</p>
                            </div>
                            <div>
                                <p class="text-[8px] sm:text-[9px] font-black text-slate-400 uppercase leading-none">Negative</p>
                                <p class="text-xs sm:text-sm font-black text-red-600 mt-0.5">${summary.negative_count ?? 0}</p>
                            </div>
                            <div>
                                <p class="text-[8px] sm:text-[9px] font-black text-slate-400 uppercase leading-none">Part.</p>
                                <p class="text-xs sm:text-sm font-black ${participationClass} mt-0.5">${participation}%</p>
                            </div>
                        </div>
                        <p class="mt-1 text-[9px] sm:text-[10px] text-slate-400 font-bold leading-tight">Analyzed: ${summary.analyzed_count ?? 0} • Avg constituents: ${avgMove >= 0 ? '+' : ''}${avgMove.toFixed(2)}%${summary.flat_count ? ` • Flat: ${summary.flat_count}` : ''}</p>
                    </div>
                `;
            };
            const sectorSummary = data.sector_summary || {};

            for (const [sectorName, tickers] of Object.entries(SECTOR_TICKERS)) {
                const gridContainer = document.getElementById(`view-${sectorName}`);
                if (!gridContainer) continue;

                // Sort tickers by weight descending so larger tiles render first at the top
                const sortedTickers = [...tickers].sort((a, b) => {
                    const wA = TICKER_WEIGHTS[a] || 1;
                    const wB = TICKER_WEIGHTS[b] || 1;
                    return wB - wA;
                });

                let html = renderSectorSummaryCard(sectorSummary[sectorName]);
                sortedTickers.forEach(ticker => {
                    const pct = data.sectors[ticker] !== undefined ? data.sectors[ticker] : 0.0;
                    const cleanSymbol = ticker.replace('-USD', '');
                    const isUp = pct > 0;
                    const isDown = pct < 0;

                    // Light-mode glassmorphic return-based color scaling
                    let bgClass = 'bg-white/90 border-slate-200/80 text-slate-600 shadow-sm'; // Flat/Neutral
                    if (isUp) {
                        if (pct > 4) bgClass = 'bg-emerald-600 border-emerald-700 text-white shadow-md shadow-emerald-100';
                        else if (pct > 2) bgClass = 'bg-emerald-400/90 border-emerald-500/60 text-slate-900 shadow-sm';
                        else bgClass = 'bg-emerald-100/80 border-emerald-200 text-emerald-800 shadow-sm';
                    } else if (isDown) {
                        if (pct < -4) bgClass = 'bg-rose-600 border-rose-700 text-white shadow-md shadow-rose-100';
                        else if (pct < -2) bgClass = 'bg-rose-400/90 border-rose-500/60 text-slate-900 shadow-sm';
                        else bgClass = 'bg-rose-100/80 border-rose-200 text-rose-800 shadow-sm';
                    }

                    // Sizing and spanning classes based on relative market cap weight
                    const weight = TICKER_WEIGHTS[ticker] || 1;
                    let spanClass = 'col-span-1 row-span-1 p-1 sm:p-1.5 min-h-[36px] sm:min-h-[42px] flex flex-col justify-center';
                    let fontClass = 'text-[11px] sm:text-xs font-bold leading-tight';
                    let changeFontClass = 'text-[8.5px] sm:text-[9px] leading-none';

                    if (weight === 3) {
                        spanClass = 'col-span-2 sm:col-span-2 row-span-1 sm:row-span-2 p-1.5 sm:p-2 md:p-3 flex flex-col justify-center min-h-[42px] sm:min-h-[80px]';
                        fontClass = 'text-xs sm:text-sm md:text-base font-black tracking-wider leading-tight';
                        changeFontClass = 'text-[9px] sm:text-[10px] font-bold leading-none';
                    } else if (weight === 2) {
                        spanClass = 'col-span-1 sm:col-span-2 row-span-1 p-1 sm:p-1.5 md:p-2 flex flex-col justify-center min-h-[36px] sm:min-h-[42px]';
                        fontClass = 'text-[11px] sm:text-xs md:text-sm font-black tracking-wide leading-tight';
                        changeFontClass = 'text-[8.5px] sm:text-[10px] font-bold leading-none';
                    }

                    const tileClass = `flex flex-col items-center justify-center rounded-xl border backdrop-blur-sm transition-all duration-300 hover:scale-[1.03] hover:shadow-md cursor-pointer text-center relative ${spanClass} ${bgClass}`;

                    // Convert to alpaca/standard ticker representation for selectTicker (remove hyphens)
                    const selectSymbol = cleanSymbol.replace('-', '');

                    html += `
                    <div class="${tileClass}" onclick="selectTicker('${selectSymbol}'); closeSectorModal();" title="${cleanSymbol}: ${pct > 0 ? '+' : ''}${pct}%">
                        <span class="${fontClass}">${cleanSymbol}</span>
                        <span class="${changeFontClass} mt-0.5">${pct > 0 ? '+' : ''}${pct}%</span>
                    </div>
                    `;
                });
                gridContainer.innerHTML = html;
            }
        }

        // Update Market Breadth Modal
        if (data.breadth) {
            window._marketBreadthLoaded = true;
            setBreadthSummaryCard(data.breadth);
            const universeEl = document.getElementById('bm-universe-label');
            if (universeEl) {
                const analyzed = data.breadth.analyzed_count ?? data.breadth.tickers?.length ?? 0;
                const universe = data.breadth.universe || 'Fixed market benchmark universe';
                universeEl.innerText = `${universe} • ${analyzed}/${data.breadth.universe_count || analyzed} symbols analyzed`;
            }
            document.getElementById('bm-adv').innerText = data.breadth.advancing;
            document.getElementById('bm-dec').innerText = data.breadth.declining;

            const total = data.breadth.advancing + data.breadth.declining;
            const adv_pct = Number.isFinite(Number(data.breadth.advance_pct))
                ? Number(data.breadth.advance_pct)
                : (total > 0 ? (data.breadth.advancing / total) * 100 : 50);
            const dec_pct = Math.max(0, 100 - adv_pct);

            document.getElementById('bm-adv-bar').style.width = `${adv_pct}%`;
            document.getElementById('bm-dec-bar').style.width = `${dec_pct}%`;

            if (data.breadth.declining > 0) {
                document.getElementById('bm-ad-ratio').innerText = `Ratio: ${(data.breadth.advancing / data.breadth.declining).toFixed(2)} • ${Math.round(adv_pct)}% advancing${data.breadth.unchanged ? ` • ${data.breadth.unchanged} unchanged` : ''}`;
            } else {
                document.getElementById('bm-ad-ratio').innerText = `Ratio: ${data.breadth.advancing} Adv / 0 Dec${data.breadth.unchanged ? ` • ${data.breadth.unchanged} unchanged` : ''}`;
            }

            const ad_label = document.getElementById('bm-ad-label');
            if (adv_pct >= 60) {
                ad_label.innerText = 'Bullish Bias';
                ad_label.className = 'text-emerald-500 bg-emerald-50 text-[10px] px-2 py-1 rounded font-bold uppercase tracking-widest shadow-sm';
            } else if (dec_pct >= 60) {
                ad_label.innerText = 'Bearish Bias';
                ad_label.className = 'text-red-500 bg-red-50 text-[10px] px-2 py-1 rounded font-bold uppercase tracking-widest shadow-sm';
            } else {
                ad_label.innerText = 'Neutral';
                ad_label.className = 'text-slate-500 bg-slate-50 text-[10px] px-2 py-1 rounded font-bold uppercase tracking-widest shadow-sm';
            }

            document.getElementById('bm-highs').innerText = data.breadth.new_highs;
            document.getElementById('bm-lows').innerText = data.breadth.new_lows;

            const hl_label = document.getElementById('bm-hl-label');
            const hl_text = document.getElementById('bm-hl-text');
            if (data.breadth.new_highs > data.breadth.new_lows * 2) {
                hl_label.innerText = 'Expansion';
                hl_label.className = 'text-emerald-500 bg-emerald-50 text-[10px] px-2 py-1 rounded font-bold uppercase tracking-widest shadow-sm';
                hl_text.innerText = 'Momentum heavily favors buyers';
            } else if (data.breadth.new_lows > data.breadth.new_highs * 2) {
                hl_label.innerText = 'Contraction';
                hl_label.className = 'text-red-500 bg-red-50 text-[10px] px-2 py-1 rounded font-bold uppercase tracking-widest shadow-sm';
                hl_text.innerText = 'Momentum heavily favors sellers';
            } else {
                hl_label.innerText = 'Mixed';
                hl_label.className = 'text-slate-500 bg-slate-50 text-[10px] px-2 py-1 rounded font-bold uppercase tracking-widest shadow-sm';
                hl_text.innerText = 'No clear momentum dominance';
            }

            document.getElementById('bm-50-val').innerText = `${data.breadth.above_50_pct}%`;
            const gauge50 = document.getElementById('bm-50-gauge');
            gauge50.setAttribute('stroke-dasharray', `${data.breadth.above_50_pct}, 100`);
            if (data.breadth.above_50_pct > 50) gauge50.className.baseVal = "text-emerald-500";
            else gauge50.className.baseVal = "text-red-500";

            document.getElementById('bm-200-val').innerText = `${data.breadth.above_200_pct}%`;
            const gauge200 = document.getElementById('bm-200-gauge');
            gauge200.setAttribute('stroke-dasharray', `${data.breadth.above_200_pct}, 100`);
            if (data.breadth.above_200_pct > 50) gauge200.className.baseVal = "text-emerald-400";
            else gauge200.className.baseVal = "text-red-400";

            // Render individual tickers list
            const rowsContainer = document.getElementById('bm-ticker-rows');
            if (rowsContainer && data.breadth.tickers) {
                let rowsHtml = '';
                data.breadth.tickers.forEach(t => {
                    const isUp = t.change_pct >= 0;
                    const changeColor = isUp ? 'text-emerald-500' : 'text-red-500';
                    const changeSign = isUp ? '+' : '';

                    const ma50Pill = t.above_50 === null ?
                        '<span class="px-1.5 sm:px-2 py-0.5 rounded text-[9px] sm:text-[10px] font-bold bg-slate-100 text-slate-400">N/A</span>' :
                        (t.above_50 ?
                            '<span class="px-1.5 sm:px-2 py-0.5 rounded text-[9px] sm:text-[10px] font-bold bg-emerald-100 text-emerald-700"><span class="sm:hidden">▲</span><span class="hidden sm:inline">ABOVE</span></span>' :
                            '<span class="px-1.5 sm:px-2 py-0.5 rounded text-[9px] sm:text-[10px] font-bold bg-red-100 text-red-700"><span class="sm:hidden">▼</span><span class="hidden sm:inline">BELOW</span></span>'
                        );

                    const ma200Pill = t.above_200 === null ?
                        '<span class="px-1.5 sm:px-2 py-0.5 rounded text-[9px] sm:text-[10px] font-bold bg-slate-100 text-slate-400">N/A</span>' :
                        (t.above_200 ?
                            '<span class="px-1.5 sm:px-2 py-0.5 rounded text-[9px] sm:text-[10px] font-bold bg-emerald-100 text-emerald-700 font-semibold"><span class="sm:hidden">▲</span><span class="hidden sm:inline">ABOVE</span></span>' :
                            '<span class="px-1.5 sm:px-2 py-0.5 rounded text-[9px] sm:text-[10px] font-bold bg-red-100 text-red-700 font-semibold"><span class="sm:hidden">▼</span><span class="hidden sm:inline">BELOW</span></span>'
                        );

                    let hlPill = '<span class="px-1.5 sm:px-2 py-0.5 rounded text-[9px] sm:text-[10px] font-bold bg-slate-100 text-slate-400">NORMAL</span>';
                    if (t.is_high) {
                        hlPill = '<span class="px-1.5 sm:px-2 py-0.5 rounded text-[9px] sm:text-[10px] font-bold bg-emerald-500 text-white shadow-sm shadow-emerald-100"><span class="sm:hidden">HIGH</span><span class="hidden sm:inline">52W HIGH</span></span>';
                    } else if (t.is_low) {
                        hlPill = '<span class="px-1.5 sm:px-2 py-0.5 rounded text-[9px] sm:text-[10px] font-bold bg-red-500 text-white shadow-sm shadow-red-100"><span class="sm:hidden">LOW</span><span class="hidden sm:inline">52W LOW</span></span>';
                    }

                    rowsHtml += `
                    <tr class="border-b border-slate-100 hover:bg-slate-50/50 transition-colors">
                        <td class="px-1.5 py-1.5 sm:px-4 sm:py-3 font-bold text-slate-800 text-[11px] sm:text-xs">${t.ticker}</td>
                        <td class="px-1.5 py-1.5 sm:px-4 sm:py-3 font-semibold text-slate-700 text-[11px] sm:text-xs text-right">$${t.price.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}</td>
                        <td class="px-1.5 py-1.5 sm:px-4 sm:py-3 font-bold text-[11px] sm:text-xs text-right ${changeColor}">${changeSign}${t.change_pct}%</td>
                        <td class="px-1 py-1.5 sm:px-4 sm:py-3 text-center">${ma50Pill}</td>
                        <td class="px-1 py-1.5 sm:px-4 sm:py-3 text-center">${ma200Pill}</td>
                        <td class="px-1 py-1.5 sm:px-4 sm:py-3 text-center">${hlPill}</td>
                    </tr>
                    `;
                });
                rowsContainer.innerHTML = rowsHtml;
            }
        }
    } catch (e) {
        console.error("Failed to fetch market data:", e);
    }
}

function escapeDashboardHtml(value) {
    return String(value ?? '').replace(/[&<>\"']/g, char => ({
        '&': '&amp;', '<': '&lt;', '>': '&gt;', '\"': '&quot;', "'": '&#39;'
    })[char]);
}

function safeDashboardUrl(value) {
    try {
        const url = new URL(String(value || ''), window.location.origin);
        return ['http:', 'https:'].includes(url.protocol) ? url.href : '';
    } catch (_) {
        return '';
    }
}

function macroEventActualDisplay(event, isPast) {
    const hasActual = event.actual !== undefined && event.actual !== null && String(event.actual).trim() !== '';
    if (hasActual) {
        return {
            text: event.actual,
            cls: 'text-indigo-600 font-black',
            note: event.actual_period ? `Period: ${event.actual_period}` : '',
        };
    }
    if (!isPast) {
        return { text: 'Pending', cls: 'text-slate-400 italic', note: '' };
    }
    if (event.actual_status === 'not_available') {
        const reason = event.actual_unavailable_reason || 'Actual not available from source';
        return { text: 'Not available', cls: 'text-slate-500 font-bold', note: reason };
    }
    return { text: 'Not available', cls: 'text-slate-500 font-bold', note: 'Calendar source did not provide actual value.' };
}

function macroEventSurpriseDisplay(event) {
    if (!event.surprise) {
        return { text: 'N/A', cls: 'text-slate-400 font-semibold' };
    }
    const cls = event.surprise_bias === 'positive'
        ? 'text-emerald-600 font-black'
        : event.surprise_bias === 'negative'
            ? 'text-red-600 font-black'
            : 'text-slate-600 font-bold';
    return { text: event.surprise, cls };
}

function macroTimeLabel(date) {
    // US releases are quoted in Eastern time; show Central too, as the rest of the app does.
    const fmt = (zone) => date.toLocaleTimeString('en-US', { timeZone: zone, hour: 'numeric', minute: '2-digit' });
    return `${fmt('America/New_York')} ET · ${fmt('America/Chicago')} CT`;
}

function macroSparklineSvg(values, color) {
    const points = Array.isArray(values) ? values.filter(Number.isFinite) : [];
    if (points.length < 2) return '';
    const min = Math.min(...points), max = Math.max(...points), span = max - min || 1;
    const coords = points.map((value, index) => `${(index / (points.length - 1) * 100).toFixed(1)},${(22 - ((value - min) / span) * 20).toFixed(1)}`).join(' ');
    return `<svg viewBox="0 0 100 24" preserveAspectRatio="none" class="w-full h-6" aria-hidden="true"><polyline fill="none" stroke="${color}" stroke-width="1.6" stroke-linejoin="round" stroke-linecap="round" vector-effect="non-scaling-stroke" points="${coords}"/></svg>`;
}

async function fetchMacroEvents(cachedData = null) {
    try {
        let data = cachedData;
        if (!data) {
            const headers = await getAuthHeaders();
            const req = await fetch(`${API_BASE}/api/macro-events`, { headers });
            if (!req.ok) return;
            data = await req.json();
        }
        window._macroDataLoaded = true;
        window._macroData = data;

        const loader = document.getElementById('macro-events-loading');
        if (loader) loader.style.display = 'none';

        // Render Indicators (compact tiles: value, move, level, sparkline; the long text is behind "What it means")
        const indList = document.getElementById('macro-indicators-list');
        if (indList && data.indicators) {
            const toneClass = { good: 'bg-emerald-50 text-emerald-700 border-emerald-200', neutral: 'bg-slate-50 text-slate-600 border-slate-200', warn: 'bg-amber-50 text-amber-700 border-amber-200', bad: 'bg-rose-50 text-rose-700 border-rose-200' };
            const backdrop = data.backdrop && Array.isArray(data.backdrop.points) && data.backdrop.points.length ? data.backdrop : null;
            let html = backdrop ? `
                <div class="mb-2 sm:mb-3 rounded-xl border px-3 py-2 ${toneClass[backdrop.tone] || toneClass.neutral}">
                    <div class="text-[9px] font-black uppercase tracking-widest opacity-70">Market backdrop</div>
                    <ul class="mt-0.5 space-y-0.5 text-[11px] sm:text-xs font-semibold leading-snug">${backdrop.points.map(point => `<li>${escapeDashboardHtml(point)}</li>`).join('')}</ul>
                </div>` : '';
            html += '<div class="grid grid-cols-2 lg:grid-cols-3 gap-2 sm:gap-3">';
            const order = ['^VIX', '^GSPC', '^TNX', '^IRX', 'CURVE', 'DX-Y.NYB', 'CL=F', 'GC=F'];
            const indicators = Object.entries(data.indicators)
                .sort((a, b) => (order.indexOf(a[0]) + 100) % 100 - (order.indexOf(b[0]) + 100) % 100)
                .map(entry => entry[1]);

            indicators.forEach(ind => {
                if (ind.error) return;
                const up = ind.direction === 'up';
                // Up is "good" for prices, "bad" for volatility, and neither for yields, the dollar and the curve.
                const inverse = ind.ticker === '^VIX';
                const neutral = ['yield', 'spread'].includes(ind.kind) || ind.ticker === 'DX-Y.NYB';
                const good = inverse ? !up : up;
                const move = neutral ? 'bg-indigo-50 text-indigo-600' : (good ? 'bg-emerald-100 text-emerald-600' : 'bg-red-100 text-red-600');
                const arrow = up ? '↑' : '↓';
                const changeText = ind.change_display || `${Math.abs(ind.change_pct)}%`;
                const week = ind.week_change_bp != null ? `5d ${ind.week_change_bp > 0 ? '+' : ''}${ind.week_change_bp} bp`
                    : ind.week_change_pct != null ? `5d ${ind.week_change_pct > 0 ? '+' : ''}${ind.week_change_pct}%` : '';
                const level = ind.level && ind.level.label
                    ? `<span class="px-1.5 py-0.5 rounded border text-[9px] font-black uppercase ${toneClass[ind.level.tone] || toneClass.neutral}" title="${escapeDashboardHtml(ind.level.note || '')}">${escapeDashboardHtml(ind.level.label)}</span>` : '';
                const unit = ind.unit === '$' ? '$' : '';
                const unitSuffix = ind.unit === '%' ? '%' : '';
                const valueText = `${unit}${Number(ind.current_value).toLocaleString('en-US', { maximumFractionDigits: 2 })}${unitSuffix}`;

                let newsHtml = '';
                if (ind.recent_news && ind.recent_news.length > 0) {
                    const topNews = ind.recent_news[0];
                    const newsUrl = safeDashboardUrl(topNews.link);
                    newsHtml = newsUrl ? `
                        <a href="${escapeDashboardHtml(newsUrl)}" target="_blank" rel="noopener noreferrer" class="block mt-1.5 pt-1.5 border-t border-slate-100">
                            <span class="text-[9px] font-black text-slate-400 uppercase tracking-widest block">${escapeDashboardHtml(topNews.publisher)}</span>
                            <span class="text-[11px] text-slate-600 font-medium line-clamp-2">${escapeDashboardHtml(topNews.title)}</span>
                        </a>` : '';
                }
                const sparkline = macroSparklineSvg(ind.sparkline, neutral ? '#6366f1' : (good ? '#10b981' : '#ef4444'));
                const rangeNote = ind.range_note ? `<div class="text-[9px] font-semibold text-slate-400 leading-tight">${escapeDashboardHtml(ind.range_note)}</div>` : '';

                html += `
                <div class="bg-slate-50 border border-slate-100 rounded-xl p-2.5 sm:p-4 min-w-0">
                    <div class="flex items-start justify-between gap-1">
                        <div class="min-w-0">
                            <span class="text-[9px] font-black text-slate-400 uppercase tracking-widest">${escapeDashboardHtml(ind.ticker)}</span>
                            <h4 class="text-[11px] sm:text-sm font-bold text-slate-800 leading-tight">${escapeDashboardHtml(ind.name)}</h4>
                        </div>
                        <span class="shrink-0 px-1.5 py-0.5 rounded text-[10px] sm:text-xs font-bold ${move}">${arrow} ${escapeDashboardHtml(changeText.replace(/^[+-]/, ''))}</span>
                    </div>
                    <div class="flex items-end justify-between gap-1 mt-1">
                        <span class="text-lg sm:text-2xl font-black text-slate-800 leading-none">${escapeDashboardHtml(valueText)}</span>
                        ${level}
                    </div>
                    <div class="mt-1">${sparkline}</div>
                    <div class="flex items-center justify-between gap-1 text-[9px] font-bold text-slate-400 mt-0.5"><span>${escapeDashboardHtml(week)}</span><span>${escapeDashboardHtml(ind.as_of || '')}</span></div>
                    ${rangeNote}
                    <details class="mt-1">
                        <summary class="cursor-pointer text-[10px] font-black text-indigo-600">What it means</summary>
                        <p class="mt-1 text-[11px] text-slate-600 leading-snug">${escapeDashboardHtml(ind.description || '')}</p>
                        ${ind.level && ind.level.note ? `<p class="mt-1 text-[11px] text-slate-500 leading-snug">${escapeDashboardHtml(ind.level.note)}</p>` : ''}
                        ${newsHtml}
                    </details>
                </div>`;
            });

            html += '</div>';
            indList.innerHTML = html;
        }

        // Render Calendar. Releases at the same time share one card (CPI, core CPI and the y/y readings arrive together),
        // so a phone shows more of the week in less scrolling.
        const calList = document.getElementById('macro-calendar-list');
        const line = document.getElementById('macro-timeline-line');
        if (line) line.classList.add('hidden');
        if (calList && data.calendar) {
            let html = '';
            const sortedCalendar = [...data.calendar].sort((a, b) => new Date(a.date) - new Date(b.date));
            if (sortedCalendar.length > 0) {
                const now = new Date();
                const upcoming = sortedCalendar.find(e => new Date(e.date) >= now);
                if (upcoming) {
                    const minutes = Math.round((new Date(upcoming.date) - now) / 60000);
                    const eta = minutes < 60 ? `in ${minutes} min` : minutes < 1440 ? `in ${Math.floor(minutes / 60)}h ${minutes % 60}m` : `in ${Math.floor(minutes / 1440)}d ${Math.floor((minutes % 1440) / 60)}h`;
                    html += `<div class="mb-2 sm:mb-3 rounded-xl border border-indigo-200 bg-indigo-50 px-3 py-2 text-[11px] sm:text-xs font-bold text-indigo-800"><span class="text-[9px] font-black uppercase tracking-widest text-indigo-500 mr-1">Next up</span>${escapeDashboardHtml(upcoming.title)} · ${escapeDashboardHtml(upcoming.country || 'USD')} · ${escapeDashboardHtml(macroTimeLabel(new Date(upcoming.date)))} (${eta})</div>`;
                }

                // Group by exact release time.
                const groups = [];
                sortedCalendar.forEach(e => {
                    const last = groups[groups.length - 1];
                    if (last && last.date === e.date && last.country === (e.country || 'USD')) last.events.push(e);
                    else groups.push({ date: e.date, country: e.country || 'USD', events: [e] });
                });

                let currentGroupDay = '';
                groups.forEach(group => {
                    const dateObj = new Date(group.date);
                    const dayGroupStr = dateObj.toLocaleDateString(undefined, { weekday: 'long', month: 'short', day: 'numeric' });
                    const isPast = dateObj < now;

                    if (dayGroupStr !== currentGroupDay) {
                        currentGroupDay = dayGroupStr;
                        const isTodayGroup = dateObj.toDateString() === now.toDateString();
                        const headerBg = isTodayGroup ? 'bg-indigo-600 text-white ring-2 ring-indigo-200' : 'bg-slate-200/80 text-slate-700';
                        html += `<div class="sticky top-0 z-10 py-1 px-2.5 rounded-md text-[11px] sm:text-xs font-black uppercase tracking-wider mb-2 ${headerBg} shadow-sm inline-block">${dayGroupStr} ${isTodayGroup ? '• Today' : ''}</div>`;
                    }

                    const rank = { High: 3, Medium: 2, 'Medium/Low': 2, Low: 1 };
                    const topImpact = group.events.reduce((best, e) => (rank[e.impact] || 0) > (rank[best] || 0) ? e.impact : best, group.events[0].impact || 'High');
                    const impactColor = topImpact === 'High' ? 'bg-red-100 text-red-600' : topImpact === 'Low' ? 'bg-yellow-100 text-yellow-600' : 'bg-orange-100 text-orange-600';
                    const dot = isPast ? 'bg-slate-300' : topImpact === 'High' ? 'bg-red-500 animate-pulse' : topImpact === 'Low' ? 'bg-yellow-500' : 'bg-orange-500';
                    const cardBg = isPast ? 'bg-slate-50/70 border-slate-100 opacity-80' : 'bg-white border-indigo-100/80 shadow-sm';
                    const status = isPast ? 'Done' : 'Upcoming';
                    const statusCls = isPast ? 'bg-slate-200 text-slate-500' : 'bg-indigo-100 text-indigo-700';
                    const why = (group.events.find(e => e.why) || {}).why || '';

                    const rows = group.events.map(e => {
                        const actualDisplay = macroEventActualDisplay(e, isPast);
                        const surpriseDisplay = macroEventSurpriseDisplay(e);
                        const cell = (label, value, cls = 'text-slate-700 font-semibold') => `<div class="min-w-0"><span class="block text-[8.5px] font-bold uppercase text-slate-400 leading-none">${label}</span><span class="${cls} text-[11px] sm:text-xs break-words">${escapeDashboardHtml(value)}</span></div>`;
                        const read = e.macro_read ? `<div class="mt-1 rounded-md border border-indigo-100 bg-indigo-50/70 px-2 py-1 text-[10px] font-bold text-indigo-800 leading-snug">Quant read: ${escapeDashboardHtml(e.macro_read)}</div>` : '';
                        const source = e.actual_source ? `<div class="mt-0.5 text-[9px] font-bold text-slate-400 uppercase tracking-wide">Actual: ${escapeDashboardHtml(e.actual_source)}${e.actual_period && !(actualDisplay.note || '').includes(e.actual_period) ? ` • ${escapeDashboardHtml(e.actual_period)}` : ''}</div>` : '';
                        const note = actualDisplay.note ? `<div class="mt-0.5 text-[9px] font-medium text-slate-400 leading-snug">${escapeDashboardHtml(actualDisplay.note)}</div>` : '';
                        return `<div class="py-1.5 border-t border-slate-100 first:border-t-0 first:pt-0">
                            <div class="flex items-start justify-between gap-2"><h4 class="text-[13px] sm:text-sm font-bold text-slate-800 leading-snug">${escapeDashboardHtml(e.title)}</h4>${group.events.length > 1 ? `<span class="shrink-0 px-1.5 rounded text-[9px] font-black ${e.impact === 'High' ? 'bg-red-100 text-red-600' : 'bg-orange-100 text-orange-600'} uppercase">${escapeDashboardHtml(e.impact || 'High')}</span>` : ''}</div>
                            <div class="mt-1 grid grid-cols-4 gap-x-2 gap-y-1 rounded-md bg-slate-50/80 px-2 py-1.5 border border-slate-100/80">
                                ${cell('Prev', e.previous || 'N/A')}${cell('Fcst', e.forecast || 'N/A')}${cell('Actual', actualDisplay.text, actualDisplay.cls)}${cell('Surprise', surpriseDisplay.text, surpriseDisplay.cls)}
                            </div>${note}${source}${read}</div>`;
                    }).join('');

                    html += `
                    <div class="border rounded-xl px-2.5 py-2 sm:px-4 sm:py-3 mb-2 sm:mb-3 ${cardBg}">
                        <div class="flex items-center justify-between gap-2 mb-1.5">
                            <div class="flex items-center gap-1.5 min-w-0 flex-wrap">
                                <span class="w-2 h-2 rounded-full shrink-0 ${dot}"></span>
                                <span class="text-[11px] sm:text-xs font-black text-slate-600 uppercase tracking-wide">${escapeDashboardHtml(macroTimeLabel(dateObj))} • ${escapeDashboardHtml(group.country)}</span>
                                <span class="px-1.5 py-0.5 rounded text-[9px] font-black ${statusCls} uppercase tracking-widest">${status}</span>
                            </div>
                            ${group.events.length === 1 ? `<span class="shrink-0 px-1.5 py-0.5 rounded text-[9px] font-black ${impactColor} uppercase">${escapeDashboardHtml(topImpact || 'High')}</span>` : `<span class="shrink-0 text-[9px] font-black text-slate-400 uppercase">${group.events.length} releases</span>`}
                        </div>
                        ${rows}
                        ${why ? `<details class="mt-1.5"><summary class="cursor-pointer text-[10px] font-black text-indigo-600">Why it matters</summary><p class="mt-1 text-[11px] text-slate-600 leading-snug">${escapeDashboardHtml(why)}</p></details>` : ''}
                    </div>`;
                });
            } else {
                html = `<p class="text-center text-slate-500 font-bold mt-10">No high impact events scheduled for this week.</p>`;
            }
            calList.innerHTML = html;
        }

        // Render mini dashboard card with upcoming calendar events
        const miniContainer = document.getElementById('macroEventsContainer');
        if (miniContainer && data.calendar) {
            let miniHtml = '';
            const sortedCalendar = [...data.calendar].sort((a, b) => new Date(a.date) - new Date(b.date));
            const now = new Date();
            const upcomingEvents = sortedCalendar.filter(e => new Date(e.date) >= now);

            if (upcomingEvents.length > 0) {
                // Releases that come out together (CPI, core CPI and their y/y readings) share ONE header line, so all of
                // them fit in the small card instead of three header-and-title pairs using up its height.
                const groups = [];
                upcomingEvents.forEach(e => {
                    const last = groups[groups.length - 1];
                    if (last && last.date === e.date && last.country === (e.country || 'USD')) last.events.push(e);
                    else groups.push({ date: e.date, country: e.country || 'USD', events: [e] });
                });
                const LINE_BUDGET = 6;  // header and title lines the card has room for
                let used = 0;
                for (const group of groups) {
                    if (used >= LINE_BUDGET - 1) break;
                    const rank = { High: 3, Medium: 2, 'Medium/Low': 2, Low: 1 };
                    const impact = group.events.reduce((best, e) => (rank[e.impact] || 0) > (rank[best] || 0) ? e.impact : best, group.events[0].impact || 'High');
                    let badgeColor = 'bg-red-100 text-red-600';
                    if (impact === 'Medium' || impact === 'Medium/Low') badgeColor = 'bg-orange-100 text-orange-600';
                    else if (impact === 'Low') badgeColor = 'bg-yellow-100 text-yellow-600';

                    const dateObj = new Date(group.date);
                    let dayStr = dateObj.toLocaleDateString(undefined, { weekday: 'short' });
                    if (dateObj.toDateString() === now.toDateString()) {
                        dayStr = 'Today';
                    } else if (dateObj.toDateString() === new Date(now.getTime() + 86400000).toDateString()) {
                        dayStr = 'Tomorrow';
                    }
                    const timeStr = dateObj.toLocaleTimeString(undefined, { hour: 'numeric', minute: '2-digit' });
                    const room = Math.max(1, LINE_BUDGET - used - 1);
                    const shown = group.events.slice(0, room);
                    const hidden = group.events.length - shown.length;
                    used += 1 + shown.length + (hidden ? 1 : 0);

                    miniHtml += `
                        <div class="flex flex-col gap-0.5 w-full py-0.5 border-b border-slate-100/60 last:border-0">
                            <div class="flex items-center justify-between text-[8.5px] text-slate-500 font-bold uppercase tracking-wider">
                                <span class="flex items-center gap-1.5 truncate">
                                    <span class="w-1.5 h-1.5 rounded-full bg-indigo-500 animate-pulse shrink-0"></span>
                                    <span>${escapeDashboardHtml(dayStr)} • ${escapeDashboardHtml(timeStr)}${group.country && group.country !== 'USD' ? ` • ${escapeDashboardHtml(group.country)}` : ''}</span>
                                </span>
                                <span class="px-1.5 py-0.1 rounded-[2.5px] text-[7px] font-black ${badgeColor} uppercase shrink-0">${escapeDashboardHtml(impact || 'High')}</span>
                            </div>
                            ${shown.map(e => {
                                const prev = e.previous ? escapeDashboardHtml(e.previous) : '';
                                const fcst = e.forecast ? escapeDashboardHtml(e.forecast) : '';
                                const readings = (prev || fcst)
                                    ? `<span class="macro-mini-readings shrink-0" title="Previous ${prev || 'n/a'} · Forecast ${fcst || 'n/a'}"><span class="macro-mini-key">P</span>${prev || '–'}<span class="macro-mini-key">F</span>${fcst || '–'}</span>`
                                    : '';
                                return `<div class="macro-mini-row" title="${escapeDashboardHtml(e.title)}"><span class="macro-mini-title font-bold text-slate-800 group-hover:text-indigo-600 transition-colors">${escapeDashboardHtml(e.title)}</span>${readings}</div>`;
                            }).join('')}
                            ${hidden ? `<span class="font-bold text-slate-400 text-[10px]">+${hidden} more</span>` : ''}
                        </div>
                    `;
                }
            } else if (sortedCalendar.length > 0) {
                // All weekly events have passed, display the 3 most recent completed events
                const pastEvents = sortedCalendar.slice(-3);
                miniHtml = `<div class="text-[9px] font-black text-slate-400 uppercase tracking-wider mb-1 text-center">All weekly events completed</div>`;
                pastEvents.forEach(e => {
                    let badgeColor = e.impact === 'High' ? 'bg-red-100 text-red-600' : (e.impact === 'Medium' ? 'bg-orange-100 text-orange-600' : 'bg-yellow-100 text-yellow-600');
                    const dateObj = new Date(e.date);
                    const dayStr = dateObj.toLocaleDateString(undefined, { weekday: 'short' });
                    const timeStr = dateObj.toLocaleTimeString(undefined, { hour: 'numeric', minute: '2-digit' });
                    miniHtml += `
                        <div class="flex flex-col gap-0.5 w-full py-0.5 border-b border-slate-100/60 last:border-0 opacity-75">
                            <div class="flex items-center justify-between text-[8px] text-slate-400 font-bold uppercase tracking-wider">
                                <span class="truncate">[Done] ${escapeDashboardHtml(dayStr)} • ${escapeDashboardHtml(timeStr)}</span>
                                <span class="px-1.5 py-0.1 rounded-[2.5px] text-[7px] font-black ${badgeColor} uppercase shrink-0">${escapeDashboardHtml(e.impact || 'High')}</span>
                            </div>
                            <span class="font-bold text-slate-600 text-[10.5px] truncate w-full" title="${escapeDashboardHtml(e.title)}">${escapeDashboardHtml(e.title)}</span>
                        </div>
                    `;
                });
            } else {
                miniHtml = `
                    <div class="flex items-center justify-center text-xs font-semibold text-slate-400 py-4">
                        No events scheduled this week
                    </div>
                `;
            }
            miniContainer.innerHTML = miniHtml;
        }

    } catch (e) {
        console.error("Error fetching macro events", e);
    }
}

function switchMacroTab(tab) {
    const tabs = ['calendar', 'indicators'];
    tabs.forEach(t => {
        const btn = document.getElementById(`tab-macro-${t}`);
        const view = document.getElementById(`view-macro-${t}`);
        if (t === tab) {
            btn.classList.replace('border-transparent', 'border-indigo-600');
            btn.classList.replace('text-slate-500', 'text-indigo-700');
            view.classList.remove('hidden');
        } else {
            btn.classList.replace('border-indigo-600', 'border-transparent');
            btn.classList.replace('text-indigo-700', 'text-slate-500');
            view.classList.add('hidden');
        }
    });
}

// ──────────────────────────────────────────────
// Watchlist Mobile UI Toggles
// ──────────────────────────────────────────────
window.toggleWatchlistCollapse = function (forceCollapse = false) {
    const area = document.getElementById('watchlistCollapsibleArea');
    const chevron = document.getElementById('watchlistChevron');
    if (!area || !chevron) return;

    // Check if it's currently expanded
    const isExpanded = area.style.maxHeight !== '0px';

    if (isExpanded || forceCollapse === true) {
        // Collapse it
        area.style.maxHeight = '0px';
        area.style.opacity = '0';
        area.style.marginTop = '0';
        area.style.pointerEvents = 'none';
        chevron.style.transform = 'rotate(0deg)';
    } else {
        // Expand it
        area.style.maxHeight = '3000px';
        area.style.opacity = '1';
        area.style.marginTop = '1rem';
        area.style.pointerEvents = 'auto';
        chevron.style.transform = 'rotate(90deg)';
    }
};
