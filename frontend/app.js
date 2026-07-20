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
    let didTimeout = false;
    const timeoutId = setTimeout(() => {
        didTimeout = true;
        controller.abort();
    }, timeoutMs);
    try {
        return await fetch(url, {
            ...options,
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
}

function isCryptoTicker(ticker) {
    const clean = normalizeTicker(ticker) || '';
    return clean.includes('USD') || ['BTC', 'ETH', 'LTC', 'SOL', 'DOGE', 'ADA', 'DOT', 'SHIB', 'AVAX', 'XRP'].some(c => clean.startsWith(c));
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
    const seconds = CHART_INTERVALS[timeframe]?.seconds || 300;
    return seconds > 0 && seconds < 86400 ? seconds : 0;
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
    const d = new Date(bar.time * 1000);
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
    return mins >= (8 * 60 + 30) && mins <= (15 * 60);
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
        candleSeries.createPriceLine({ price: highBar.high, color: 'rgba(16,185,129,0.75)', lineWidth: 1, lineStyle: 1, axisLabelVisible: true, title: `${formatPrice(highBar.high)}` }),
        candleSeries.createPriceLine({ price: lowBar.low, color: 'rgba(239,68,68,0.75)', lineWidth: 1, lineStyle: 1, axisLabelVisible: true, title: `${formatPrice(lowBar.low)}` }),
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
    try { if (oscChartBotbulls1 && _visibleIndicators.botbulls1) oscChartBotbulls1.timeScale().setVisibleLogicalRange(range); } catch (e) { }
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
const chartDataCache = new Map();
let chartRequestSerial = 0;
const CHART_CACHE_VERSION = 'v7';
const CHART_CACHE_TTL_MS = 30 * 60 * 1000;
const CHART_IDB_NAME = 'aiTradeBotChartCache';
const CHART_IDB_VERSION = 1;
const CHART_IDB_STORE = 'chartPayloads';
const COMPLETED_CANDLE_HEARTBEAT_MS = 10000;
const COMPLETED_CANDLE_DELAY_MS = 15000;
const LIVE_PRICE_HEARTBEAT_MS = 10000;
const DASHBOARD_FETCH_TIMEOUT_MS = 20000;
const CHART_FETCH_TIMEOUT_MS = 30000;
const QUOTE_FETCH_TIMEOUT_MS = 10000;
let completedCandleHeartbeatId = null;
let lastCompletedChartFetchAt = 0;
let lastCompletedChartBarTime = 0;
let lastCompletedChartAttemptedCloseTime = 0;
let activeChartRefreshInFlight = false;
let livePriceRequestSerial = 0;
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
    updateLastBarInfo(window._confirmedDisplayPriceHistory || [], selectedTicker);
}

/**
 * Retrieves the current Firebase ID token and prepares headers.
 */
async function getAuthHeaders() {
    const auth = window.auth;
    const baseHeaders = {
        'Content-Type': 'application/json',
        'Bypass-Tunnel-Reminder': 'true'
    };

    if (!auth) {
        console.warn('[auth] window.auth is NOT defined yet');
        return baseHeaders;
    }
    const user = auth.currentUser;
    if (!user) {
        if (localStorage.getItem('dev_mode') === 'true') {
            return {
                ...baseHeaders,
                'Authorization': `Bearer dev-token`
            };
        }
        console.warn('[auth] No current user found in auth instance');
        return baseHeaders;
    }

    try {
        const token = await user.getIdToken();
        return {
            ...baseHeaders,
            'Authorization': `Bearer ${token}`
        };
    } catch (e) {
        console.error('[auth] Error getting ID token:', e);
        return baseHeaders;
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
let oscChartRsi = null;
let oscChartMacd = null;
let oscChartMystic = null;
let oscChartAdx = null;
let oscChartBotbulls1 = null;

// All series objects keyed by indicator name
const _overlaySeries = {};
const _oscSeries = {};
const _overlayPriceLines = {};
const _overlayLabelMeta = {};

// Which indicators are currently visible (toggled by toolbar)
const _defaultVisibleIndicators = {
    ema: false,
    vwap: false,
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
    botbulls1: false,
    botbulls2: false,
    botbulls3: false,
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
    boll_upper: '#f97316', // Orange
    boll_middle: '#3b82f6', // Blue
    boll_lower: '#a855f7', // Purple
    rsi: '#f59e0b',
    macd_line: '#06b6d4',
    macd_signal: '#f97316', // Orange
    sma: '#8b5cf6', // Violet
    adx: '#6366f1', // Indigo
    di_plus: '#10b981', // Green
    di_minus: '#ef4444', // Red
    wt1: '#0ea5e9', // Sky blue
    wt2: '#f43f5e', // Rose
    mfi: '#10b981', // Emerald
    lx_smart_trail: '#10b981', // Smart trail
    lx_trend_tracer: '#8b5cf6', // Trend tracer
    lx_rz_upper: '#f59e0b', // Reversal zone upper
    lx_rz_lower: '#ec4899', // Reversal zone lower
    ut_trail: '#3b82f6', // UT Bot trail
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
    container.innerHTML = '';


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
        layout: { background: { color: '#ffffff' }, textColor: '#334155' },
        grid: {
            vertLines: { color: 'rgba(168, 85, 247, 0.05)' },
            horzLines: { color: 'rgba(168, 85, 247, 0.05)' },
        },
        crosshair: { mode: LightweightCharts.CrosshairMode.Normal },
        rightPriceScale: {
            borderColor: 'rgba(197, 203, 206, 0.8)',
            minimumWidth: 64,
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
        color: 'rgba(100, 116, 139, 0.1)', // Slight grey background
        priceFormat: { type: 'volume' },
        priceScaleId: 'ext-hours-bg',
        scaleMargins: { top: 0, bottom: 0 },
    });
    lwChart.priceScale('ext-hours-bg').applyOptions({
        visible: false,
        autoScale: false,
        scaleMargins: { top: 0, bottom: 0 },
    });

    candleSeries = lwChart.addCandlestickSeries({
        upColor: '#10b981', downColor: '#ef4444',
        borderVisible: false,
        wickUpColor: '#10b981', wickDownColor: '#ef4444',
        priceScaleId: 'right',
    });

    let lastChartWidth = 0;
    const resizeObserver = new ResizeObserver(entries => {
        if (entries.length === 0 || !entries[0].contentRect) return;
        const { width, height } = entries[0].contentRect;
        lwChart.applyOptions({ width, height });
        
        // Auto-fit content on significant width changes (e.g. going full screen)
        if (lastChartWidth > 0 && Math.abs(width - lastChartWidth) > 100) {
            if (window._displayPriceHistory && window._displayPriceHistory.length > 0) {
                setTimeout(() => {
                    try { lwChart.timeScale().fitContent(); } catch(e){}
                }, 150);
            }
        }
        lastChartWidth = width;
    });
    resizeObserver.observe(container);

    _initOscPane();
    lwChart.timeScale().subscribeVisibleTimeRangeChange(() => {
        scheduleChartRangeUiRefresh();
    });
}

function _initOscPane() {
    // RSI Chart
    const rsiContainer = document.getElementById('oscChart_rsi');
    if (rsiContainer) {
        rsiContainer.innerHTML = '';
        oscChartRsi = LightweightCharts.createChart(rsiContainer, {
            layout: { background: { color: '#fafbff' }, textColor: '#64748b' },
            grid: { vertLines: { color: 'rgba(168,85,247,0.04)' }, horzLines: { color: 'rgba(168,85,247,0.04)' } },
            rightPriceScale: { borderColor: 'rgba(197,203,206,0.6)', minimumWidth: 75 },
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
                height: entries[0].contentRect.height
            });
        });
        resizeRsi.observe(rsiContainer);
    }

    // MACD Chart
    const macdContainer = document.getElementById('oscChart_macd');
    if (macdContainer) {
        macdContainer.innerHTML = '';
        oscChartMacd = LightweightCharts.createChart(macdContainer, {
            layout: { background: { color: '#fafbff' }, textColor: '#64748b' },
            grid: { vertLines: { color: 'rgba(168,85,247,0.04)' }, horzLines: { color: 'rgba(168,85,247,0.04)' } },
            rightPriceScale: { borderColor: 'rgba(197,203,206,0.6)', minimumWidth: 75 },
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
                height: entries[0].contentRect.height
            });
        });
        resizeMacd.observe(macdContainer);
    }

    // Mystic Pulse Chart
    const mysticContainer = document.getElementById('oscChart_mystic');
    if (mysticContainer) {
        mysticContainer.innerHTML = '';
        oscChartMystic = LightweightCharts.createChart(mysticContainer, {
            layout: { background: { color: '#fafbff' }, textColor: '#64748b' },
            grid: { vertLines: { color: 'rgba(168,85,247,0.04)' }, horzLines: { color: 'rgba(168,85,247,0.04)' } },
            rightPriceScale: { borderColor: 'rgba(197,203,206,0.6)', minimumWidth: 75 },
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
                height: entries[0].contentRect.height
            });
        });
        resizeMystic.observe(mysticContainer);
    }

    // ADX Chart
    const adxContainer = document.getElementById('oscChart_adx');
    if (adxContainer) {
        adxContainer.innerHTML = '';
        oscChartAdx = LightweightCharts.createChart(adxContainer, {
            layout: { background: { color: '#fafbff' }, textColor: '#64748b' },
            grid: { vertLines: { color: 'rgba(168,85,247,0.04)' }, horzLines: { color: 'rgba(168,85,247,0.04)' } },
            rightPriceScale: { borderColor: 'rgba(197,203,206,0.6)', minimumWidth: 75 },
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
                height: entries[0].contentRect.height
            });
        });
        resizeAdx.observe(adxContainer);
    }

    // BotBulls1 (WaveTrend) Chart
    const botbulls1Container = document.getElementById('oscChart_botbulls1');
    if (botbulls1Container) {
        botbulls1Container.innerHTML = '';
        oscChartBotbulls1 = LightweightCharts.createChart(botbulls1Container, {
            layout: { background: { color: '#fafbff' }, textColor: '#64748b' },
            grid: { vertLines: { color: 'rgba(168,85,247,0.04)' }, horzLines: { color: 'rgba(168,85,247,0.04)' } },
            rightPriceScale: { borderColor: 'rgba(197,203,206,0.6)', minimumWidth: 75 },
            localization: {
                timeFormatter: time => {
                    return `${formatChartTime(time, false)} CT`;
                }
            },
            timeScale: { visible: false },
        });
        const resizeBotbulls1 = new ResizeObserver(entries => {
            if (!entries[0] || !oscChartBotbulls1) return;
            oscChartBotbulls1.applyOptions({ 
                width: entries[0].contentRect.width,
                height: entries[0].contentRect.height
            });
        });
        resizeBotbulls1.observe(botbulls1Container);
    }

    // Sync all time scales to main chart (guarded by visibility and wrapped in try/catch to prevent uncaught "Value is null" library errors)
    lwChart.timeScale().subscribeVisibleLogicalRangeChange(range => {
        scheduleOscillatorRangeSync(range);
    });
}

function updateChart(priceHistory, ticker) {
    if (!lwChart || !candleSeries) initChart();

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
    window._displayPriceHistory = sorted;
    window._confirmedPriceHistory = rawSorted;
    window._confirmedDisplayPriceHistory = sorted;
    lastCompletedChartBarTime = Number(sorted[sorted.length - 1]?.time || 0);
    let shouldFollowRealtime = true;
    try {
        const scrollPosition = lwChart?.timeScale?.().scrollPosition?.();
        shouldFollowRealtime = !Number.isFinite(scrollPosition) || scrollPosition < 3;
    } catch (e) {
        shouldFollowRealtime = true;
    }
    // Background highlight for Extended Hours
    if (extHoursSeries) {
        if (!isCryptoTicker(ticker) && currentChartSession === 'extended' && currentBackendTf !== '1Day') {
            const extData = sorted.map(b => {
                const isExt = !isRegularMarketBar(b);
                return {
                    time: b.time,
                    value: isExt ? 9999999 : 0,
                    color: isExt ? 'rgba(100, 116, 139, 0.12)' : 'rgba(0,0,0,0)'
                };
            });
            extHoursSeries.setData(extData);
        } else {
            extHoursSeries.setData([]);
        }
    }

    candleSeries.setData(sorted.map(b => ({ time: b.time, open: b.open, high: b.high, low: b.low, close: b.close })));
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
            // volume from sorted bars by time
            const ts = param.time;
            const vBar = window._displayPriceHistory?.find(b => b.time === ts);
            document.getElementById('ohlcvV').textContent = vBar ? fmtVol(vBar.volume) : '—';
        });
    }

    updateChartControlState(ticker);
    updateLastBarInfo(sorted, ticker);

    _updateChartHeader(sorted);
    _refreshOverlays(sorted);
    _refreshOscillators(sorted);

    // Always maintain a comfortable zoom level for horizontal sliding
    const isNewTicker = window._lastFittedTicker !== ticker;
    const isNewRange = window._lastFittedRange !== currentChartRange;

    if (isNewTicker || isNewRange) {
        setTimeout(() => {
            lwChart.timeScale().fitContent();
        }, 150);
        window._lastFittedTicker = ticker;
        window._lastFittedRange = currentChartRange;
    }

    updateVisibleHighLow();
    const visibleRange = lwChart.timeScale().getVisibleLogicalRange();
    syncOscillatorRanges(visibleRange);
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
            if (pointTime <= to && (!Number.isFinite(from) || pointTime >= from)) {
                return data[i];
            }
        }
        for (let i = data.length - 1; i >= 0; i -= 1) {
            if (Number(data[i]?.time) <= to) return data[i];
        }
    }
    return data[data.length - 1];
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

function _refreshOverlays(bars) {
    const activeMarkers = [];

    // EMA
    if (_visibleIndicators.ema) {
        _getOrCreate(_overlaySeries, 'ema_fast', lwChart, { color: IND_COLORS.ema_fast, lineWidth: 1.5 })
            .setData(clean(bars.filter(b => b.ema_fast != null).map(b => ({ time: b.time, value: b.ema_fast }))));
        _getOrCreate(_overlaySeries, 'ema_slow', lwChart, { color: IND_COLORS.ema_slow, lineWidth: 1.5 })
            .setData(clean(bars.filter(b => b.ema_slow != null).map(b => ({ time: b.time, value: b.ema_slow }))));
    } else {
        _removeSeries(_overlaySeries, 'ema_fast', lwChart);
        _removeSeries(_overlaySeries, 'ema_slow', lwChart);
    }
    // VWAP
    if (_visibleIndicators.vwap) {
        _getOrCreate(_overlaySeries, 'vwap', lwChart, { color: '#f59e0b', lineWidth: 2.5, lineStyle: 0 })
            .setData(clean(bars.filter(b => b.vwap != null).map(b => ({ time: b.time, value: b.vwap }))));
    } else {
        _removeSeries(_overlaySeries, 'vwap', lwChart);
    }
    // SMA
    if (_visibleIndicators.sma) {
        _getOrCreate(_overlaySeries, 'sma', lwChart, { color: IND_COLORS.sma, lineWidth: 2.5, lineStyle: 0 })
            .setData(clean(bars.filter(b => b.sma != null).map(b => ({ time: b.time, value: b.sma }))));
    } else {
        _removeSeries(_overlaySeries, 'sma', lwChart);
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
                _setLabeledLineData(_overlaySeries, sKey, lwChart, clean(bars.filter(b => b[sKey] != null).map(b => ({ time: b.time, value: b[sKey] }))), {
                    color: level === 1 ? IND_COLORS.swing_support : 'rgba(22, 163, 74, 0.72)',
                    lineWidth: level === 1 ? 3 : 2,
                    lineStyle: level === 1 ? 1 : 2,
                }, `SW S${level}`);
                _setLabeledLineData(_overlaySeries, rKey, lwChart, clean(bars.filter(b => b[rKey] != null).map(b => ({ time: b.time, value: b[rKey] }))), {
                    color: level === 1 ? IND_COLORS.swing_resistance : 'rgba(220, 38, 38, 0.72)',
                    lineWidth: level === 1 ? 3 : 2,
                    lineStyle: level === 1 ? 1 : 2,
                }, `SW R${level}`);
            } else {
                _removeSeries(_overlaySeries, sKey, lwChart);
                _removeSeries(_overlaySeries, rKey, lwChart);
            }
        }
    } else {
        for (let level = 1; level <= 5; level += 1) {
            _removeSeries(_overlaySeries, `swing_support_${level}`, lwChart);
            _removeSeries(_overlaySeries, `swing_resistance_${level}`, lwChart);
        }
    }

    // Volume Profile — prior-session POC and value-area structure
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

    // BotBulls2 — Universal Trailing Stop (UT) + Trend Tracer + Reversal Zones
    if (_visibleIndicators.botbulls2) {
        const lxTrailData = bars.filter(b => b.lx_smart_trail != null).map(b => ({
            time: b.time,
            value: b.lx_smart_trail,
            color: b.lx_smart_trend ? '#10b981' : '#ef4444'
        }));
        _getOrCreate(_overlaySeries, 'lx_smart_trail', lwChart, {
            lineWidth: 2.5,
            lineType: 0
        }).setData(clean(lxTrailData));

        _getOrCreate(_overlaySeries, 'lx_trend_tracer', lwChart, {
            color: IND_COLORS.lx_trend_tracer,
            lineWidth: 1.5,
            lineStyle: 0
        }).setData(clean(bars.filter(b => b.lx_trend_tracer != null).map(b => ({ time: b.time, value: b.lx_trend_tracer }))));

        _getOrCreate(_overlaySeries, 'lx_rz_upper', lwChart, {
            color: IND_COLORS.lx_rz_upper,
            lineWidth: 1,
            lineStyle: 1 // Dashed
        }).setData(clean(bars.filter(b => b.lx_rz_upper != null).map(b => ({ time: b.time, value: b.lx_rz_upper }))));

        _getOrCreate(_overlaySeries, 'lx_rz_lower', lwChart, {
            color: IND_COLORS.lx_rz_lower,
            lineWidth: 1,
            lineStyle: 1 // Dashed
        }).setData(clean(bars.filter(b => b.lx_rz_lower != null).map(b => ({ time: b.time, value: b.lx_rz_lower }))));

        // Trend flip alerts for Smart Trail
        for (let i = 1; i < bars.length; i++) {
            const curr = bars[i];
            const prev = bars[i - 1];
            if (curr.lx_smart_trail == null || prev.lx_smart_trail == null) continue;

            if (curr.lx_smart_trend && !prev.lx_smart_trend) {
                activeMarkers.push({
                    time: curr.time,
                    position: 'belowBar',
                    color: '#10b981',
                    shape: 'arrowUp',
                    text: 'BB2 BUY',
                    size: 1
                });
            } else if (!curr.lx_smart_trend && prev.lx_smart_trend) {
                activeMarkers.push({
                    time: curr.time,
                    position: 'aboveBar',
                    color: '#ef4444',
                    shape: 'arrowDown',
                    text: 'BB2 SELL',
                    size: 1
                });
            }
        }
    } else {
        _removeSeries(_overlaySeries, 'lx_smart_trail', lwChart);
        _removeSeries(_overlaySeries, 'lx_trend_tracer', lwChart);
        _removeSeries(_overlaySeries, 'lx_rz_upper', lwChart);
        _removeSeries(_overlaySeries, 'lx_rz_lower', lwChart);
    }

    // BotBulls3 — Heikin-Ashi Smoothed + UT Bot Trailing Stop
    if (_visibleIndicators.botbulls3) {
        const utTrailData = bars.filter(b => b.ut_trail != null).map(b => ({
            time: b.time,
            value: b.ut_trail,
            color: b.ut_above ? '#0ea5e9' : '#f43f5e'
        }));
        _getOrCreate(_overlaySeries, 'ut_trail', lwChart, {
            lineWidth: 2.5,
            lineType: 0
        }).setData(clean(utTrailData));

        // UT Bot buy/sell flip alerts
        for (let i = 1; i < bars.length; i++) {
            const curr = bars[i];
            const prev = bars[i - 1];
            if (curr.ut_trail == null || prev.ut_trail == null) continue;

            if (curr.ut_above && !prev.ut_above) {
                activeMarkers.push({
                    time: curr.time,
                    position: 'belowBar',
                    color: '#0ea5e9',
                    shape: 'arrowUp',
                    text: 'BB3 BUY',
                    size: 1.2
                });
            } else if (!curr.ut_above && prev.ut_above) {
                activeMarkers.push({
                    time: curr.time,
                    position: 'aboveBar',
                    color: '#f43f5e',
                    shape: 'arrowDown',
                    text: 'BB3 SELL',
                    size: 1.2
                });
            }
        }
    } else {
        _removeSeries(_overlaySeries, 'ut_trail', lwChart);
    }

    // BotBulls1 WT Buy/Sell Markers (WaveTrend Extreme Crossover alerts)
    if (_visibleIndicators.botbulls1) {
        for (let i = 0; i < bars.length; i++) {
            const b = bars[i];
            if (b.ba1_buy) {
                activeMarkers.push({
                    time: b.time,
                    position: 'belowBar',
                    color: '#06b6d4',
                    shape: 'arrowUp',
                    text: 'BB1 BUY',
                    size: 1
                });
            } else if (b.ba1_sell) {
                activeMarkers.push({
                    time: b.time,
                    position: 'aboveBar',
                    color: '#f43f5e',
                    shape: 'arrowDown',
                    text: 'BB1 SELL',
                    size: 1
                });
            }
        }
    }

    // Candle Patterns Markers
    if (_visibleIndicators['Candle Patterns']) {
        const b = bars[bars.length - 1];
        if (b?.cdl_name && b.cdl_name !== 'Neutral') {
            if (b.cdl_bullish) {
                activeMarkers.push({
                    time: b.time,
                    position: 'belowBar',
                    color: '#10b981',
                    shape: 'arrowUp',
                    text: b.cdl_name,
                    size: 1.1
                });
            } else if (b.cdl_bearish) {
                activeMarkers.push({
                    time: b.time,
                    position: 'aboveBar',
                    color: '#ef4444',
                    shape: 'arrowDown',
                    text: b.cdl_name,
                    size: 1.1
                });
            } else {
                activeMarkers.push({
                    time: b.time,
                    position: 'aboveBar',
                    color: '#f59e0b',
                    shape: 'circle',
                    text: b.cdl_name,
                    size: 1.1
                });
            }
        }
    }

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
        const obVal = indicatorParamValue('RSI_OVERBOUGHT', 70);
        const osVal = indicatorParamValue('RSI_OVERSOLD', 30);
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
        const macdFast = indicatorParamValue('MACD_FAST', 12);
        const macdSlow = indicatorParamValue('MACD_SLOW', 26);
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
            adxLabel.textContent = `ADX (${adxPeriod})  •  Trend Thresh: ${adxThreshVal}`;
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

    // Handle BotBulls1 (WaveTrend + MFI)
    const botbulls1Container = document.getElementById('oscChart_botbulls1');
    const botbulls1Label = document.getElementById('botbulls1Label');
    if (botbulls1Container) {
        const wasHidden = botbulls1Container.style.display === 'none' || getComputedStyle(botbulls1Container).display === 'none';
        botbulls1Container.style.display = _visibleIndicators.botbulls1 ? 'block' : 'none';
        if (botbulls1Label) botbulls1Label.style.display = _visibleIndicators.botbulls1 ? 'flex' : 'none';
        if (wasHidden && _visibleIndicators.botbulls1 && oscChartBotbulls1) {
            oscChartBotbulls1.applyOptions({ width: botbulls1Container.clientWidth, height: botbulls1Container.clientHeight });
        }
    }
    if (_visibleIndicators.botbulls1 && oscChartBotbulls1) {
        const wtChannel = indicatorParamValue('BOTBULLS1_WT_CHANNEL', 10);
        const wtAvg = indicatorParamValue('BOTBULLS1_WT_AVERAGE', 21);
        const mfiBuy = indicatorParamValue('BOTBULLS1_MFI_BUY_RECLAIM', 35);
        const mfiSell = indicatorParamValue('BOTBULLS1_MFI_SELL_REJECT', 65);
        if (botbulls1Label) {
            botbulls1Label.textContent = `BotBulls1 (WT: ${wtChannel}, ${wtAvg} | MFI: ${mfiBuy}/${mfiSell})`;
        }
        // wt1 line
        _getOrCreate(_oscSeries, 'wt1', oscChartBotbulls1, { color: IND_COLORS.wt1, lineWidth: 1.5 })
            .setData(clean(bars.filter(b => b.wt1 != null).map(b => ({ time: b.time, value: b.wt1 }))));

        // wt2 line
        _getOrCreate(_oscSeries, 'wt2', oscChartBotbulls1, { color: IND_COLORS.wt2, lineWidth: 1, lineStyle: 2 })
            .setData(clean(bars.filter(b => b.wt2 != null).map(b => ({ time: b.time, value: b.wt2 }))));

        // MFI filled zone (centered at 50)
        if (!_oscSeries['mfi_hist']) {
            _oscSeries['mfi_hist'] = oscChartBotbulls1.addHistogramSeries({
                base: 50,
                priceScaleId: '', // Same pane scale
            });
        }
        const mfiHistData = bars.filter(b => b.mfi != null).map(b => ({
            time: b.time,
            value: b.mfi,
            color: b.mfi >= 50 ? 'rgba(16, 185, 129, 0.12)' : 'rgba(239, 68, 68, 0.12)'
        }));
        _oscSeries['mfi_hist'].setData(clean(mfiHistData));

        // Reference lines: Overbought (+53), Oversold (-53), Zero Line (0)
        const obLine = _getOrCreate(_oscSeries, 'wt_ob', oscChartBotbulls1, { color: 'rgba(239,68,68,0.35)', lineWidth: 1, lineStyle: 2 });
        const osLine = _getOrCreate(_oscSeries, 'wt_os', oscChartBotbulls1, { color: 'rgba(16,185,129,0.35)', lineWidth: 1, lineStyle: 2 });
        const zeroLine = _getOrCreate(_oscSeries, 'wt_zero', oscChartBotbulls1, { color: 'rgba(148,163,184,0.15)', lineWidth: 1, lineStyle: 0 });
        if (bars.length) {
            obLine.setData(clean([{ time: bars[0].time, value: 53 }, { time: bars[bars.length - 1].time, value: 53 }]));
            osLine.setData(clean([{ time: bars[0].time, value: -53 }, { time: bars[bars.length - 1].time, value: -53 }]));
            zeroLine.setData(clean([{ time: bars[0].time, value: 0 }, { time: bars[bars.length - 1].time, value: 0 }]));
        }

        // Wave crossover dots directly on wt1 line
        const wt1Series = _oscSeries['wt1'];
        if (wt1Series) {
            const wtMarkers = [];
            for (let i = 0; i < bars.length; i++) {
                const b = bars[i];
                if (b.ba1_buy) {
                    wtMarkers.push({
                        time: b.time,
                        position: 'inLine',
                        color: '#10b981', // Green dot
                        shape: 'circle',
                        size: 2
                    });
                } else if (b.ba1_sell) {
                    wtMarkers.push({
                        time: b.time,
                        position: 'inLine',
                        color: '#ef4444', // Red dot
                        shape: 'circle',
                        size: 2
                    });
                }
            }
            wt1Series.setMarkers(wtMarkers);
        }

        try { oscChartBotbulls1.timeScale().fitContent(); } catch (e) { }
    } else if (oscChartBotbulls1) {
        _removeSeries(_oscSeries, 'wt1', oscChartBotbulls1);
        _removeSeries(_oscSeries, 'wt2', oscChartBotbulls1);
        _removeSeries(_oscSeries, 'mfi_hist', oscChartBotbulls1);
        _removeSeries(_oscSeries, 'wt_ob', oscChartBotbulls1);
        _removeSeries(_oscSeries, 'wt_os', oscChartBotbulls1);
        _removeSeries(_oscSeries, 'wt_zero', oscChartBotbulls1);
    }
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

function chartCacheKey(ticker, timeframe = currentBackendTf) {
    return `${normalizeTicker(ticker)}|${timeframe}|${CHART_CACHE_VERSION}`;
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

function renderFastChartPayload(data, requestedTicker) {
    const ticker = normalizeTicker(requestedTicker || data?.ticker || data?.primaryTicker);
    if (!ticker || normalizeTicker(selectedTicker) !== ticker) return;

    const payload = data?.data || data;
    const history = payload?.priceHistory || payload?.price_history || payload?.primaryScan?.price_history || [];
    if (history.length > 0) {
        updateChart(history, ticker);
    }

    const scan = payload?.primaryScan || payload;
    const signals = scan?.signals || payload?.signals || {};
    if (signals && Object.keys(signals).length > 0) {
        window._lastSignals = signals;
        window._lastSignalsTicker = ticker;
        renderSignals(
            signals,
            scan.action || payload.action || 'HOLD',
            scan.reason || payload.reason || '',
            scan.bullish_count ?? payload.bullish_count ?? 0,
            scan.bearish_count ?? payload.bearish_count ?? 0
        );
        updateSizingPanel(scan);
        recomputeConfluence();
    }

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
        const url = `${API_BASE}/api/signals/${encodeURIComponent(requestedTicker)}?timeframe=${encodeURIComponent(currentBackendTf)}&source=dashboard&force=${force ? 'true' : 'false'}`;
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
        const url = `${API_BASE}/api/chart/${encodeURIComponent(requestedTicker)}?timeframe=${encodeURIComponent(currentBackendTf)}&force=${force ? 'true' : 'false'}&source=${encodeURIComponent(chartSource)}`;
        const response = await fetchWithTimeout(url, { headers }, CHART_FETCH_TIMEOUT_MS);
        if (!response.ok) {
            console.error(`[chart] HTTP Error: ${response.status} - ${await response.text()}`);
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
    if (Date.now() - lastCompletedChartFetchAt < COMPLETED_CANDLE_HEARTBEAT_MS) return;

    const now = Date.now();
    const latestSafeClosedBarTime = Math.floor(((now - COMPLETED_CANDLE_DELAY_MS) / 1000) / intervalSeconds) * intervalSeconds;
    if (latestSafeClosedBarTime <= 0) return;
    if (lastCompletedChartBarTime >= latestSafeClosedBarTime) return;
    if (lastCompletedChartAttemptedCloseTime >= latestSafeClosedBarTime) return;

    activeChartRefreshInFlight = true;
    lastCompletedChartAttemptedCloseTime = latestSafeClosedBarTime;
    try {
        await loadSelectedTickerChart({ force: true, renderCached: false, fetchFresh: true });
        if (lastCompletedChartBarTime < latestSafeClosedBarTime) {
            // Webull can lag completed candles by 1-2 minutes. Do not suppress
            // retries for a candle that the provider has not actually returned.
            lastCompletedChartAttemptedCloseTime = Math.max(0, lastCompletedChartBarTime);
        }
    } finally {
        activeChartRefreshInFlight = false;
    }
}

function updateChartHeaderLivePrice(price) {
    const priceEl = document.getElementById('chartPrice');
    const changeEl = document.getElementById('chartPriceChange');
    if (!priceEl || !Number.isFinite(price) || price <= 0) return;

    const bars = window._confirmedDisplayPriceHistory || window._displayPriceHistory || [];
    const first = bars?.[0];
    const baseline = Number(first?.open || first?.close || 0);
    const change = baseline > 0 ? price - baseline : 0;
    const changePct = baseline > 0 ? (change / baseline) * 100 : 0;

    priceEl.textContent = formatPrice(price, selectedTicker);
    priceEl.className = `text-xl font-black ${change >= 0 ? 'text-emerald-600' : 'text-red-500'}`;
    if (changeEl && baseline > 0) {
        const sign = change >= 0 ? '+' : '';
        changeEl.textContent = `${sign}${formatPrice(Math.abs(change), selectedTicker)} (${sign}${changePct.toFixed(2)}%)`;
        changeEl.className = `text-xs font-bold ${change >= 0 ? 'text-emerald-500' : 'text-red-400'}`;
    }
}

async function fetchLivePriceForActiveTicker() {
    const requestedTicker = normalizeTicker(selectedTicker);
    if (!requestedTicker || !shouldRunDashboardRefresh()) return;

    const isAuthReady = window.auth && (window.auth.currentUser || localStorage.getItem('dev_mode') === 'true');
    if (!isAuthReady) return;

    const requestId = ++livePriceRequestSerial;
    try {
        const headers = await getAuthHeaders();
        const response = await fetchWithTimeout(`${API_BASE}/api/quote/${encodeURIComponent(requestedTicker)}`, { headers }, QUOTE_FETCH_TIMEOUT_MS);
        if (!response.ok) return;

        const quote = await response.json();
        if (requestId !== livePriceRequestSerial || normalizeTicker(selectedTicker) !== requestedTicker) return;
        if (quote.stale) return;

        const price = Number(quote.price);
        updateChartHeaderLivePrice(price);
    } catch (e) {
        console.warn('[chart] Live price update failed:', e);
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
    const last = bars[bars.length - 1];
    const first = bars[0];
    const price = last.close;
    const change = price - first.open;
    const changePct = (change / first.open) * 100;
    const priceEl = document.getElementById('chartPrice');
    const changeEl = document.getElementById('chartPriceChange');
    if (priceEl) {
        priceEl.textContent = formatPrice(price, selectedTicker);
        priceEl.className = `text-xl font-black ${change >= 0 ? 'text-emerald-600' : 'text-red-500'}`;
    }
    if (changeEl) {
        const sign = change >= 0 ? '+' : '';
        changeEl.textContent = `${sign}${formatPrice(Math.abs(change), selectedTicker)} (${sign}${changePct.toFixed(2)}%)`;
        changeEl.className = `text-xs font-bold ${change >= 0 ? 'text-emerald-500' : 'text-red-400'}`;
    }
}

function clearChartVisuals() {
    if (candleSeries) candleSeries.setData([]);
    if (extHoursSeries) extHoursSeries.setData([]);
    if (_overlaySeries) Object.values(_overlaySeries).forEach(s => s && typeof s.setData === 'function' && s.setData([]));
    if (_oscSeries) Object.values(_oscSeries).forEach(s => s && typeof s.setData === 'function' && s.setData([]));
    
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
    const premiumGrid = document.getElementById('premiumSignalGrid');
    const loading = document.getElementById('gridLoading');
    if (grid || premiumGrid) {
        lockSignalConfluencePanelHeight();
        renderSignals({}, 'HOLD', `Loading ${normalized} indicator signals...`, 0, 0, { allowEmpty: true });
    }
    setSignalConfluenceLoadingState(true);

    const summaryEl = document.getElementById('aiSummary');
    const cardSentSummary = document.getElementById('sentimentSummary');
    const aiHeaderEl = document.getElementById('aiIndicatorHeader');
    if (aiHeaderEl) aiHeaderEl.textContent = `BotBulls AI Analysis - ${normalized}`;
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
    loadSelectedTickerChart({ renderCached: true });
    fetchDashboard('fast');
    if (selectedTicker) fetchAIIndicator(selectedTicker, currentBackendTf);
}

function setChartRange(range) {
    if (!CHART_RANGES[range]) return;
    currentChartRange = range;
    localStorage.setItem('lastChartRange', range);
    resetCompletedCandleState();

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
    loadSelectedTickerChart({ renderCached: true });
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
    updateChartControlState(selectedTicker);
    if (window._lastPriceHistory) {
        updateChart(window._lastPriceHistory, window._lastTicker || selectedTicker);
    }
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
        return `<div class="flex items-center justify-between px-4 py-2.5 hover:bg-indigo-50 cursor-pointer text-sm font-bold text-indigo-900 border-b border-slate-50 last:border-0 transition-colors"
                    onclick="selectTickerFromSearch('${t}')">
                    <span>${t}</span>
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
    "VWAP": "Volume Weighted Average Price (VWAP) is the ratio of value traded to total volume. Purpose: Provides the true average price a security traded at throughout the day.",
    "Volume Profile": "Prior-session volume-at-price profile using POC, VAH, and VAL. Purpose: Identifies institutional acceptance/rejection zones where the most volume traded.",
    "Prior Day High/Low": "Uses the previous completed trading session's high and low as breakout/breakdown levels. Buffer is entered as %, with dotted trigger lines when non-zero.",
    "Pivot Points (S/R)": "Classic prior-session floor pivot levels. Draws up to 5 support/resistance ladders; bullish when price reclaims support/pivot, bearish when price rejects resistance or loses pivot.",
    "Swing Points": "Rolling swing support/resistance clusters from recent local highs/lows. Purpose: Identifies structural bounce, rejection, reclaim, and breakdown zones.",
    "Strategy Confidence": "Strategy Confidence combines multiple indicator signals into a single probability score. Purpose: Measures the overall conviction of a bullish or bearish trend."
};

var INDICATOR_CONFIG_MAP = {
    'RSI': ['RSI_PERIOD', 'RSI_OVERBOUGHT', 'RSI_OVERSOLD'],
    'MACD': ['MACD_FAST', 'MACD_SLOW', 'MACD_SIGNAL'],
    'EMA Cross': ['EMA_FAST', 'EMA_SLOW'],
    'Supertrend': ['SUPERTREND_PERIOD', 'SUPERTREND_MULTIPLIER'],
    'Bollinger': ['BOLL_PERIOD', 'BOLL_STD_DEV'],
    'Mystic Pulse': ['MYSTIC_PULSE_THRESHOLD'],
    'ADX Trend': ['ADX_PERIOD', 'ADX_TRENDING_THRESHOLD'],
    'SMA': ['SMA_PERIOD'],
    'Prior Day High/Low': ['DAILY_HIGH_LOW_BUFFER_PCT'],
    'Pivot Points (S/R)': ['PIVOT_LEVEL_COUNT', 'PIVOT_TOLERANCE_PCT'],
    'Volume Profile': ['VOLUME_PROFILE_ROWS', 'VOLUME_PROFILE_VALUE_AREA_PCT', 'VOLUME_PROFILE_TOLERANCE_PCT', 'VOLUME_PROFILE_CONFIRMATION_CANDLES'],
    'Swing Points': ['SWING_POINTS_LOOKBACK_DAYS', 'SWING_POINTS_ENTRY_MODE', 'SWING_POINTS_LEVEL_COUNT'],
    'ATR Volatility': ['ATR_PERIOD', 'ATR_STOP_MULTIPLIER', 'ATR_TRAIL_MULTIPLIER', 'ATR_TAKE_PROFIT_MULTIPLIER'],
    'Strategy Confidence': ['MIN_BULLISH_SIGNALS', 'MIN_BEARISH_SIGNALS'],
    'Botbulls4 News AI': ['SENTIMENT_BULLISH_PERCENTAGE', 'SENTIMENT_BEARISH_PERCENTAGE'],
    'BotBulls1': ['BOTBULLS1_WT_CHANNEL', 'BOTBULLS1_WT_AVERAGE', 'BOTBULLS1_WT_OVERSOLD', 'BOTBULLS1_WT_OVERBOUGHT', 'BOTBULLS1_MFI_BUY_RECLAIM', 'BOTBULLS1_MFI_SELL_REJECT', 'BOTBULLS1_CONFIRM_LOOKBACK', 'BOTBULLS1_REQUIRE_MFI_CONFIRM', 'BOTBULLS1_REQUIRE_CANDLE_CONFIRM', 'BOTBULLS1_REQUIRE_PRICE_CONFIRM', 'BOTBULLS1_PRICE_CONFIRM_EMA', 'BOTBULLS1_PRICE_CONFIRM_LOOKBACK'],
    'BotBulls2': ['BOTBULLS2_ATR_MULT', 'BOTBULLS2_TREND_TRACER_PERIOD', 'BOTBULLS2_REVERSAL_ZONE_PERIOD'],
    'BotBulls3': ['BOTBULLS3_ATR_PERIOD', 'BOTBULLS3_ATR_MULT']
};

// Industry Defaults for indicators based on config.py
var INDICATOR_DEFAULTS = {
    'BOTBULLS1_WT_CHANNEL': 10,
    'BOTBULLS1_WT_AVERAGE': 21,
    'BOTBULLS1_MFI_CONFIRM': 30,
    'BOTBULLS1_WT_OVERSOLD': -55,
    'BOTBULLS1_WT_OVERBOUGHT': 55,
    'BOTBULLS1_MFI_BUY_RECLAIM': 35,
    'BOTBULLS1_MFI_SELL_REJECT': 65,
    'BOTBULLS1_CONFIRM_LOOKBACK': 3,
    'BOTBULLS1_REQUIRE_MFI_CONFIRM': 1,
    'BOTBULLS1_REQUIRE_CANDLE_CONFIRM': 1,
    'BOTBULLS1_REQUIRE_PRICE_CONFIRM': 1,
    'BOTBULLS1_PRICE_CONFIRM_EMA': 9,
    'BOTBULLS1_PRICE_CONFIRM_LOOKBACK': 4,
    'BOTBULLS2_ATR_MULT': 2.0,
    'BOTBULLS2_TREND_TRACER_PERIOD': 50,
    'BOTBULLS2_REVERSAL_ZONE_PERIOD': 50,
    'BOTBULLS3_ATR_PERIOD': 10,
    'BOTBULLS3_ATR_MULT': 1.0,
    'RSI_PERIOD': 14,
    'RSI_OVERBOUGHT': 70,
    'RSI_OVERSOLD': 30,
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
    'SMA_PERIOD': 200,
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

let currentEditingIndicator = null;

// ──────────────────────────────────────────────
// 2.5 Global Indicator State Management
// ──────────────────────────────────────────────
const GLOBAL_INDICATOR_PREFS_KEY = 'globalIndicatorPrefs';
const ALL_SIGNAL_NAMES = [
    'RSI', 'MACD', 'EMA Cross', 'ADX Trend', 'Supertrend',
    'Bollinger', 'VWAP', 'Volume Profile', 'SMA', 'Prior Day High/Low',
    'Pivot Points (S/R)', 'Swing Points', 'Mystic Pulse', 'Candle Patterns',
    'BotBulls1', 'BotBulls2', 'BotBulls3', 'Botbulls4 News AI'
];
const INDICATOR_TOGGLE_KEYS = {
    'RSI': 'ENABLE_RSI',
    'MACD': 'ENABLE_MACD',
    'EMA Cross': 'ENABLE_EMA',
    'Supertrend': 'ENABLE_SUPERTREND',
    'Bollinger': 'ENABLE_BOLLINGER',
    'VWAP': 'ENABLE_VWAP',
    'Mystic Pulse': 'ENABLE_MYSTIC_PULSE',
    'ADX Trend': 'ENABLE_ADX_TREND',
    'SMA': 'ENABLE_SMA',
    'Prior Day High/Low': 'ENABLE_DAILY_HIGH_LOW',
    'Pivot Points (S/R)': 'ENABLE_PIVOT_POINTS',
    'Volume Profile': 'ENABLE_VOLUME_PROFILE',
    'Swing Points': 'ENABLE_SWING_POINTS',
    'Candle Patterns': 'ENABLE_CANDLE_PATTERNS',
    'Botbulls4 News AI': 'ENABLE_AI_SENTIMENT',
    'BotBulls1': 'ENABLE_BOTBULLS1',
    'BotBulls2': 'ENABLE_BOTBULLS2',
    'BotBulls3': 'ENABLE_BOTBULLS3',
};

function getGlobalIndicatorPrefs() {
    try {
        return JSON.parse(localStorage.getItem(GLOBAL_INDICATOR_PREFS_KEY)) || {};
    } catch { return {}; }
}

function allIndicatorPrefs(enabled = false) {
    return Object.fromEntries(ALL_SIGNAL_NAMES.map(name => [name, enabled]));
}

function setGlobalIndicatorPref(indicatorName, enabled, options = {}) {
    const prefs = getGlobalIndicatorPrefs();
    prefs[indicatorName] = enabled;
    localStorage.setItem(GLOBAL_INDICATOR_PREFS_KEY, JSON.stringify(prefs));
    recomputeConfluence();

    // Sync to backend via existing toggleIndicator logic in background
    const shouldRefreshChart = (options.refreshChart === true || enabled === true) && indicatorName !== 'Botbulls4 News AI';
    const shouldRefreshNewsAI = enabled === true && indicatorName === 'Botbulls4 News AI';
    const refreshChartOnce = async () => {
        if (!shouldRefreshChart) return;
        clearTimeout(window._indicatorToggleDebounce);
        window._indicatorToggleDebounce = null;
        window._indicatorRefreshRenderAllowed = true;
        try {
            if (typeof loadSelectedTickerChart === 'function') {
                return await loadSelectedTickerChart({ force: true, renderCached: false });
            } else if (typeof refreshActiveTickerSignals === 'function') {
                return await refreshActiveTickerSignals({ force: true });
            }
            return null;
        } finally {
            window._indicatorRefreshRenderAllowed = false;
        }
    };
    const refreshNewsAIOnce = async () => {
        if (!shouldRefreshNewsAI || typeof fetchAIIndicator !== 'function') return;
        clearTimeout(window._indicatorToggleDebounce);
        window._indicatorToggleDebounce = null;
        window._indicatorRefreshRenderAllowed = true;
        try {
            await fetchAIIndicator(selectedTicker, currentBackendTf, false);
        } finally {
            window._indicatorRefreshRenderAllowed = false;
        }
    };
    const toggleKey = window._lastSignals?.[indicatorName]?.toggle_key || INDICATOR_TOGGLE_KEYS[indicatorName];
    if (toggleKey) {
        // we do this without triggering the full redraw since recomputeConfluence handles it
        const wasDebouncing = window._indicatorToggleDebounce;
        // Prevent fetchDashboard from ruining our optimistic UI
        if (!wasDebouncing) window._indicatorToggleDebounce = setTimeout(() => { window._indicatorToggleDebounce = null; }, 2000);

        getAuthHeaders().then(async headers => {
            const response = await fetchWithTimeout(`${API_BASE}/api/settings/indicators?context=dashboard`, {
                method: 'POST',
                headers: headers,
                body: JSON.stringify({ [toggleKey]: enabled })
            }, 8000);
            if (response.ok) {
                await refreshChartOnce();
                await refreshNewsAIOnce();
            }
        }).catch(e => console.error('Error saving setting:', e));
    } else {
        refreshChartOnce();
        refreshNewsAIOnce();
    }
}

async function resetConfluenceIndicatorsToDefaults() {
    try {
        const disabledPrefs = allIndicatorPrefs(false);
        localStorage.setItem(GLOBAL_INDICATOR_PREFS_KEY, JSON.stringify(disabledPrefs));
        localStorage.setItem('visibleIndicators', JSON.stringify(_defaultVisibleIndicators));

        _visibleIndicators = { ..._defaultVisibleIndicators };
        Object.entries(_visibleIndicators).forEach(([key, enabled]) => {
            const btn = document.getElementById(`ind_btn_${key}`);
            if (btn) {
                btn.classList.toggle('ind-btn-active', enabled);
                btn.classList.toggle('ind-btn-inactive', !enabled);
            }
        });

        if (window.lastDashboardData) {
            window.lastDashboardData.indicator_overrides = {};
            window.lastDashboardData.indicator_toggle_overrides = {};
            window.lastDashboardData.indicator_parameters = { ...INDICATOR_DEFAULTS };
            if (window.lastDashboardData.primaryScan?.signals) {
                for (const signal of Object.values(window.lastDashboardData.primaryScan.signals)) {
                    signal.enabled = false;
                }
            }
            if (window.lastDashboardData.signals) {
                for (const signal of Object.values(window.lastDashboardData.signals)) {
                    signal.enabled = false;
                }
            }
        }
        if (window._lastSignals) {
            for (const signal of Object.values(window._lastSignals)) {
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
            body: JSON.stringify({ reset_confluence: true })
        }, 8000);
        const resetPayload = response.ok ? await response.json().catch(() => null) : null;
        if (resetPayload?.toggles && window.lastDashboardData) {
            window.lastDashboardData.indicator_toggle_overrides = { ...resetPayload.toggles };
            window.lastDashboardData.indicator_overrides = {};
            window.lastDashboardData.indicator_parameters = { ...INDICATOR_DEFAULTS };
        }

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

function recomputeConfluence() {
    const signals = window._lastSignals;
    if (!signals) return;

    const prefs = getGlobalIndicatorPrefs();
    let bullish = 0, bearish = 0;

    for (const [name, data] of Object.entries(signals)) {
        const isEnabled = prefs[name] === true;
        if (!isEnabled) continue;
        if (data.signal === 'BULLISH') bullish++;
        if (data.signal === 'BEARISH') bearish++;
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

    // We pass signals, action, reason, bullish, bearish to renderSignals
    // We need to bypass the debounce logic in renderSignals since this is a local redraw
    const wasDebouncing = window._indicatorToggleDebounce;
    window._indicatorToggleDebounce = null;

    renderSignals(signals, action, reason, bullish, bearish);

    window._indicatorToggleDebounce = wasDebouncing;
}
// AI Indicator — Groq/Ollama Agentic Signal
// ──────────────────────────────────────────────

window.ollamaModels = [];
async function loadOllamaModels() {
    try {
        const headers = await getAuthHeaders();
        const res = await fetch(`${API_BASE}/api/ollama/models`, { headers });
        if (res.ok) {
            const data = await res.json();
            if (data.status === 'online') {
                window.ollamaModels = data.models || [];
            } else {
                window.ollamaModels = [];
            }
        }
    } catch (e) {
        console.warn('[init] Failed to fetch Ollama models:', e);
        window.ollamaModels = [];
    }
}

var AI_INDICATOR_CACHE_TTL = 30 * 60 * 1000; // 30 minutes
let _aiIndicatorFetchId = 0; // prevents stale response rendering
const AI_MODEL_MIGRATIONS = {
    'mixtral-8x7b-32768': 'llama-3.3-70b-versatile',
    'mistral-saba-24b': 'llama-3.3-70b-versatile',
    'openai/gpt-oss-120b': 'llama-3.3-70b-versatile',
    'openai/gpt-oss-20b': 'llama-3.1-8b-instant'
};

function normalizeSelectedAIModel(model) {
    const value = String(model || '').trim() || 'llama-3.3-70b-versatile';
    return AI_MODEL_MIGRATIONS[value] || value;
}

function getAIIndicatorCacheKey(ticker, timeframe, model) {
    return `${normalizeTicker(ticker)}|${timeframe}|${model}|ai-hourly-v1`;
}

function isAICacheFresh(cached) {
    return cached && cached.timestamp && (Date.now() - cached.timestamp) < AI_INDICATOR_CACHE_TTL;
}

function isAICacheForContext(cached, ticker, timeframe, model) {
    if (!isAICacheFresh(cached)) return false;
    const expectedKey = getAIIndicatorCacheKey(ticker, timeframe, model);
    if (cached.key) return cached.key === expectedKey;
    return (
        normalizeTicker(cached.ticker) === normalizeTicker(ticker) &&
        cached.timeframe === timeframe &&
        cached.model === model
    );
}

function getAICache(key) {
    try {
        const cachedStr = localStorage.getItem(`ai_cache_${key}`);
        if (cachedStr) {
            const cached = JSON.parse(cachedStr);
            if (isAICacheFresh(cached)) {
                return cached;
            }
            localStorage.removeItem(`ai_cache_${key}`);
        }
    } catch (e) { console.error('Cache read error', e); }
    return null;
}

function normalizeCachedAISentimentScore(rawScore) {
    const value = Number(rawScore);
    if (!Number.isFinite(value)) return null;
    if (value >= -1 && value <= 1) return value;
    if (value >= 0 && value <= 100) return (value / 100) * 2 - 1;
    return Math.max(-1, Math.min(1, value));
}

function getCachedAIAnalysisForTicker(ticker, timeframe = currentBackendTf) {
    const normalizedTicker = normalizeTicker(ticker);
    if (!normalizedTicker) return null;
    const selectedModel = normalizeSelectedAIModel(localStorage.getItem('selected_ai_model'));
    const exact = getAICache(getAIIndicatorCacheKey(normalizedTicker, timeframe, selectedModel));
    if (exact?.data?.sentiment_score != null) return exact;

    try {
        const globalAi = JSON.parse(localStorage.getItem('latest_global_ai_analysis') || 'null');
        if (
            globalAi?.data?.sentiment_score != null &&
            isAICacheForContext(globalAi, normalizedTicker, timeframe, selectedModel)
        ) {
            return globalAi;
        }
    } catch (e) {}

    try {
        const prefix = `ai_cache_${normalizedTicker}|`;
        for (let i = localStorage.length - 1; i >= 0; i -= 1) {
            const key = localStorage.key(i);
            if (!key || !key.startsWith(prefix)) continue;
            const cached = JSON.parse(localStorage.getItem(key) || 'null');
            if (
                cached?.data?.sentiment_score != null &&
                isAICacheForContext(cached, normalizedTicker, timeframe, selectedModel)
            ) return cached;
        }
    } catch (e) {}
    return null;
}

function getCachedNewsAISentiment(ticker, timeframe = currentBackendTf) {
    const cached = getCachedAIAnalysisForTicker(ticker, timeframe);
    const data = cached?.data;
    if (!data) return null;
    const score = normalizeCachedAISentimentScore(data.sentiment_score);
    if (score === null) return null;
    return {
        score,
        pct: Math.round(((score + 1.0) / 2.0) * 100),
        reason: data.sentiment_key_factor || data.key_catalyst || 'Mixed Catalysts',
        summary: data.sentiment_summary || data.summary || '',
        confidence: Number(data.sentiment_confidence ?? data.confidence ?? 0),
    };
}

function renderCachedAIIndicatorForTicker(ticker = selectedTicker, timeframe = currentBackendTf) {
    const cached = getCachedAIAnalysisForTicker(ticker, timeframe);
    if (!cached?.data) return false;
    renderAIIndicatorCard(cached.data, true);
    return true;
}

function applyCachedNewsAISignal(signal, options = {}) {
    if (!signal) return signal;
    const sentiment = getCachedNewsAISentiment(options.ticker || selectedTicker, options.timeframe || currentBackendTf);
    if (!sentiment) return signal;
    signal.value = sentiment.score;
    signal.reason = sentiment.reason;
    signal.cached = true;
    signal.sentiment_summary = sentiment.summary;
    signal.sentiment_confidence = sentiment.confidence;
    const params = options.params || window.lastDashboardData?.indicator_parameters || {};
    const bullishThresh = params.SENTIMENT_BULLISH_PERCENTAGE ?? INDICATOR_DEFAULTS?.SENTIMENT_BULLISH_PERCENTAGE ?? 70;
    const bearishThresh = params.SENTIMENT_BEARISH_PERCENTAGE ?? INDICATOR_DEFAULTS?.SENTIMENT_BEARISH_PERCENTAGE ?? 30;
    signal.signal = sentiment.pct >= bullishThresh ? 'BULLISH' : sentiment.pct <= bearishThresh ? 'BEARISH' : 'NEUTRAL';
    return signal;
}

function setAICache(key, data, meta = {}) {
    try {
        const payload = {
            data: data,
            timestamp: Date.now(),
            key,
            ticker: normalizeTicker(meta.ticker || data?.ticker),
            timeframe: meta.timeframe,
            model: meta.model
        };
        localStorage.setItem(`ai_cache_${key}`, JSON.stringify(payload));
        localStorage.setItem(`latest_global_ai_analysis`, JSON.stringify(payload));
    } catch (e) { console.error('Cache write error', e); }
}

async function fetchAIIndicator(ticker, timeframe, force = false) {
    if (!ticker) return;
    const normalizedTicker = normalizeTicker(ticker);
    const selectedModel = normalizeSelectedAIModel(localStorage.getItem('selected_ai_model'));
    localStorage.setItem('selected_ai_model', selectedModel);
    const key = getAIIndicatorCacheKey(normalizedTicker, timeframe, selectedModel);

    // Check client-side cache first (unless forced)
    if (!force) {
        const cached = getAICache(key);
        if (cached) {
            renderAIIndicatorCard(cached.data, true);
            if (window._lastSignals) {
                renderSignals(
                    window._lastSignals,
                    window.lastDashboardData?.primaryScan?.action || 'HOLD',
                    window.lastDashboardData?.primaryScan?.reason || '',
                    window.lastDashboardData?.primaryScan?.bullish_count ?? 0,
                    window.lastDashboardData?.primaryScan?.bearish_count ?? 0
                );
            }
            recomputeConfluence();
            return;
        }
    }

    // Show loading skeleton
    _showAIIndicatorLoading(normalizedTicker);

    const fetchId = ++_aiIndicatorFetchId;
    try {
        const headers = await getAuthHeaders();
        const url = `${API_BASE}/api/ai-indicator?ticker=${encodeURIComponent(normalizedTicker)}&timeframe=${encodeURIComponent(timeframe)}&model=${encodeURIComponent(selectedModel)}${force ? '&force=true' : ''}`;
        const res = await fetch(url, { headers });
        if (!res.ok) throw new Error(`HTTP ${res.status}`);
        const data = await res.json();

        // Guard against stale responses
        if (fetchId !== _aiIndicatorFetchId) return;
        if (data.error) {
            if (selectedModel !== 'llama-3.3-70b-versatile') {
                console.warn(`[ai-indicator] Model ${selectedModel} failed. Falling back to default Groq model.`);
                localStorage.setItem('selected_ai_model', 'llama-3.3-70b-versatile');
                fetchAIIndicator(ticker, timeframe, force);
                return;
            }
            _showAIIndicatorError(data.error, ticker);
            return;
        }

        setAICache(key, data, { ticker: normalizedTicker, timeframe, model: selectedModel });
        renderAIIndicatorCard(data, false);
        if (window._lastSignals) {
            renderSignals(
                window._lastSignals,
                window.lastDashboardData?.primaryScan?.action || 'HOLD',
                window.lastDashboardData?.primaryScan?.reason || '',
                window.lastDashboardData?.primaryScan?.bullish_count ?? 0,
                window.lastDashboardData?.primaryScan?.bearish_count ?? 0
            );
        }

        // Refresh the signal grid so the Botbulls4 News AI row syncs with the fresh score
        recomputeConfluence();
    } catch (e) {
        if (fetchId === _aiIndicatorFetchId) {
            console.error('[ai-indicator] Fetch error:', e);
            if (selectedModel !== 'llama-3.3-70b-versatile') {
                console.warn(`[ai-indicator] Fetch failed for ${selectedModel}. Falling back to default Groq model.`);
                localStorage.setItem('selected_ai_model', 'llama-3.3-70b-versatile');
                fetchAIIndicator(ticker, timeframe, force);
                return;
            }
            _showAIIndicatorError('Could not load AI analysis', ticker);
        }
    }
}

function _getAIModelSelectHtml(selectedModel, isError = false) {
    let optionsHtml = '';
    optionsHtml += '<optgroup label="Groq AI (Cloud)">';
    const groqModels = [
        { value: 'llama-3.3-70b-versatile', label: 'Llama 3.3 70B (Recommended)' },
        { value: 'llama-3.1-8b-instant', label: 'Llama 3.1 8B (Fast)' }
    ];
    groqModels.forEach(m => {
        const sel = selectedModel === m.value ? 'selected' : '';
        optionsHtml += `<option value="${m.value}" ${sel}>${m.label}</option>`;
    });
    optionsHtml += '</optgroup>';

    optionsHtml += '<optgroup label="Ollama (Local)">';
    if (window.ollamaModels && window.ollamaModels.length > 0) {
        window.ollamaModels.forEach(m => {
            const val = `ollama/${m}`;
            const sel = selectedModel === val ? 'selected' : '';
            optionsHtml += `<option value="${val}" ${sel}>${m}</option>`;
        });
    } else {
        optionsHtml += `<option disabled>No local models found</option>`;
    }
    optionsHtml += '</optgroup>';

    const borderCls = isError ? 'border-red-200 text-red-900 focus:ring-red-400' : 'border-amber-200/50 text-amber-900 focus:ring-amber-400';
    return `
        <select onchange="changeAIIndicatorModel(this.value)" class="bg-white/80 backdrop-blur border ${borderCls} text-[10px] font-black px-2 py-0.5 rounded-lg shadow-sm focus:ring-1 outline-none cursor-pointer">
            ${optionsHtml}
        </select>
    `;
}

window.changeAIIndicatorModel = async function (model) {
    model = normalizeSelectedAIModel(model);
    localStorage.setItem('selected_ai_model', model);
    try {
        const headers = await getAuthHeaders();
        await fetch(`${API_BASE}/api/settings/indicators?context=dashboard`, {
            method: 'POST',
            headers: headers,
            body: JSON.stringify({ AI_INDICATOR_MODEL: model })
        });
    } catch (e) {
        console.error('[ai-indicator] Failed to save model settings to backend:', e);
    }
    const ticker = (typeof selectedTicker !== 'undefined' ? selectedTicker : '') || 'TSLA';
    const timeframe = currentBackendTf || (typeof selectedTimeframe !== 'undefined' ? selectedTimeframe : '') || '1Hour';
    _showAIIndicatorLoading(ticker);
    try {
        await fetchAIIndicator(ticker, timeframe, true);
    } catch (e) {
        console.error('[ai-indicator] Failed to reload indicator:', e);
    }
};

function _showAIIndicatorLoading(ticker) {
    const el = document.getElementById('aiIndicatorCard');
    if (!el) return;
    const selectedModel = normalizeSelectedAIModel(localStorage.getItem('selected_ai_model'));
    const isOllamaActive = selectedModel.startsWith('ollama/') || selectedModel.startsWith('ollama:');
    const badgeHtml = isOllamaActive
        ? `<span class="px-1.5 py-0.5 rounded text-[0.55rem] font-black bg-gradient-to-r from-indigo-500 to-blue-600 text-white border border-indigo-600/50 shadow-sm">OLLAMA</span>`
        : `<span class="px-1.5 py-0.5 rounded text-[0.55rem] font-black bg-gradient-to-r from-amber-200 to-amber-300 text-amber-800 border border-amber-400/50 shadow-sm">GROQ AI</span>`;
    const selectHtml = _getAIModelSelectHtml(selectedModel, false);

    el.className = 'ai-indicator-card ai-indicator-loading border-amber-200/60 bg-gradient-to-br from-amber-50/50 to-orange-50/30';
    el.innerHTML = `
        <div class="ai-orb bg-gradient-to-r from-amber-400 to-orange-500 opacity-20"></div>
        <div class="flex flex-wrap items-center justify-between gap-y-2 mb-3 relative z-10">
            <div class="flex flex-wrap items-center gap-1.5 sm:gap-2">
                <span class="text-base drop-shadow-sm">🤖</span>
                <span class="font-black text-xs text-amber-900 tracking-wide uppercase">BotBulls AI Analysis - ${ticker || '---'}</span>
                ${badgeHtml}
            </div>
            <div class="flex items-center gap-2 relative z-30">
                ${selectHtml}
                <button onclick="openAIInfoModal()" title="How AI analysis works" class="w-5 h-5 rounded-full flex items-center justify-center text-[10px] font-bold text-amber-700 hover:text-amber-900 bg-amber-100 hover:bg-amber-200 transition-colors border border-amber-300/50 shadow-sm">
                    ℹ
                </button>
                <button disabled class="w-5 h-5 rounded-full flex items-center justify-center text-xs text-amber-300 bg-amber-50 border border-amber-200 animate-spin">
                    ↻
                </button>
            </div>
        </div>
        <div class="space-y-2 animate-pulse relative z-10">
            <div class="h-8 bg-amber-200/60 rounded-lg w-2/3"></div>
            <div class="h-4 bg-amber-200/40 rounded w-full"></div>
            <div class="h-4 bg-amber-200/40 rounded w-4/5"></div>
            <div class="h-4 bg-amber-200/40 rounded w-3/5"></div>
        </div>
        <p class="text-[0.6rem] text-amber-700 mt-3 flex items-center gap-1 font-bold relative z-10">
            <svg class="h-3 w-3 animate-spin text-amber-600" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                <path stroke-linecap="round" stroke-linejoin="round" stroke-width="3" d="M12 4v4m0 8v4m4-12h-4m4 8h-4" />
            </svg>
            Analyzing market catalysts and news sentiment...
        </p>
    `;
}

function _showAIIndicatorError(err, ticker) {
    const el = document.getElementById('aiIndicatorCard');
    if (!el) return;
    const selectedModel = normalizeSelectedAIModel(localStorage.getItem('selected_ai_model'));
    const selectHtml = _getAIModelSelectHtml(selectedModel, true);

    el.className = 'ai-indicator-card ai-indicator-error relative overflow-hidden p-4 rounded-xl border transition-all duration-300';
    el.innerHTML = `
        <div class="flex flex-wrap items-center justify-between gap-y-2 mb-3 relative z-10">
            <div class="flex flex-wrap items-center gap-1.5 sm:gap-2">
                <span class="text-base">🤖</span>
                <span class="font-black text-xs text-red-800 tracking-wide uppercase">BotBulls AI Analysis - ${ticker || '---'}</span>
                <span class="px-1.5 py-0.5 rounded text-[0.55rem] font-bold bg-red-100 text-red-600 border border-red-300/50">Error</span>
            </div>
            <div class="flex items-center gap-2 relative z-30">
                ${selectHtml}
                <button onclick="openAIInfoModal()" title="How AI analysis works" class="w-5 h-5 rounded-full flex items-center justify-center text-[10px] font-bold text-red-500 hover:text-red-700 bg-red-100/50 hover:bg-red-100 transition-colors border border-red-300/30">
                    ℹ
                </button>
                <button id="aiRefreshBtn" onclick="refreshAIIndicator()" title="Force refresh AI analysis" class="w-5 h-5 rounded-full flex items-center justify-center text-xs text-red-500 hover:text-red-700 bg-red-100/50 hover:bg-red-100 transition-colors border border-red-300/30">
                    ↻
                </button>
            </div>
        </div>
        <p class="text-[0.6rem] text-red-500 relative z-10">${err || 'Could not load AI analysis'}</p>
    `;
}

function renderAIIndicatorCard(data, fromCache = false) {
    const el = document.getElementById('aiIndicatorCard');
    if (!el) return;

    const signal = String(data.signal || 'HOLD').toUpperCase();
    const confidence = data.confidence || 0;
    const confPct = Math.round(confidence * 100);
    const summary = data.summary || '';
    const catalyst = data.key_catalyst || '';
    const risks = data.risk_factors || '';
    const regime = data.volatility_regime || 'NORMAL';
    const headlineCount = data.headline_count || 0;
    const cached = fromCache || data.cached || false;

    const sentSummary = data.sentiment_summary || '';
    const sentKeyFactor = data.sentiment_key_factor || 'Mixed Catalysts';
    const sentConfidence = data.sentiment_confidence != null ? data.sentiment_confidence : confidence;
    const sentConfPct = Math.round(sentConfidence * 100);

    const confBarWidth = `${confPct}%`;
    // Confidence strength is the fill width; decision direction determines
    // the color so SELL is never shown with a bullish green confidence bar.
    const confBarColor = signal === 'BUY'
        ? 'bg-emerald-500'
        : signal === 'SELL'
            ? 'bg-rose-500'
            : 'bg-slate-400';
    const confTextColor = signal === 'BUY'
        ? 'text-emerald-600'
        : signal === 'SELL'
            ? 'text-rose-600'
            : 'text-slate-500';

    const sentScore = data.sentiment_score != null ? parseFloat(data.sentiment_score) : 0.0;
    const sentPct = data.sentiment_pct != null
        ? Math.round(parseFloat(data.sentiment_pct))
        : Math.round(((sentScore + 1.0) / 2.0) * 100);
    const newsDirection = data.sentiment_direction || (
        sentPct >= 70 ? 'BULLISH' : sentPct <= 30 ? 'BEARISH' : 'NEUTRAL'
    );
    const rawModelSignal = data.model_signal || signal;
    const alignmentReason = data.news_alignment_reason || '';
    const sentBarWidth = `${sentPct}%`;
    let sentBarColor = 'bg-slate-400';
    let sentTextColor = 'text-slate-500';

    if (newsDirection === 'BULLISH') {
        sentBarColor = 'bg-emerald-500';
        sentTextColor = 'text-emerald-600';
    } else if (newsDirection === 'BEARISH') {
        sentBarColor = 'bg-rose-500';
        sentTextColor = 'text-rose-600';
    } else if (sentPct > 55) {
        sentBarColor = 'bg-amber-500';
        sentTextColor = 'text-amber-600';
    } else if (sentPct < 25) {
        sentBarColor = 'bg-rose-500';
        sentTextColor = 'text-rose-600';
    } else if (sentPct < 45) {
        sentBarColor = 'bg-amber-500';
        sentTextColor = 'text-amber-600';
    }

    const sigColors = {
        BUY: { bg: 'ai-indicator-buy', badge: 'bg-emerald-100 text-emerald-700 border-emerald-300/50', icon: '▲', iconCls: 'text-emerald-600', regimeCls: 'text-emerald-600' },
        SELL: { bg: 'ai-indicator-sell', badge: 'bg-rose-100 text-rose-700 border-rose-300/50', icon: '▼', iconCls: 'text-rose-600', regimeCls: 'text-rose-600' },
        HOLD: { bg: 'ai-indicator-hold', badge: 'bg-slate-100 text-slate-700 border-slate-300/50', icon: '●', iconCls: 'text-slate-500', regimeCls: 'text-slate-500' }
    };
    const colors = sigColors[signal] || sigColors['HOLD'];

    const cachedBadge = cached
        ? `<span class="text-[0.5rem] text-amber-600 opacity-60 ml-1">cached</span>`
        : '';

    const selectedModel = normalizeSelectedAIModel(localStorage.getItem('selected_ai_model'));
    const usedModel = data.model || selectedModel;
    const candleTimeframe = data.candle_timeframe || '1Hour';
    const isOllamaActive = selectedModel.startsWith('ollama/') || selectedModel.startsWith('ollama:');
    const badgeHtml = isOllamaActive
        ? `<span class="px-1.5 py-0.5 rounded text-[0.55rem] font-black bg-gradient-to-r from-indigo-500 to-blue-600 text-white border border-indigo-600/50 shadow-sm">OLLAMA</span>`
        : `<span class="px-1.5 py-0.5 rounded text-[0.55rem] font-black bg-gradient-to-r from-amber-200 to-amber-300 text-amber-800 border border-amber-400/50 shadow-sm">GROQ AI</span>`;
    const usedModelHtml = `<span title="Actual model used for this response" class="px-1.5 py-0.5 rounded text-[0.5rem] font-bold bg-white/70 text-slate-600 border border-slate-200/70">${usedModel}</span>`;
    const candleTimeframeHtml = `<span title="Candle timeframe used by AI market analysis" class="px-1.5 py-0.5 rounded text-[0.5rem] font-bold bg-fuchsia-50/80 text-fuchsia-700 border border-fuchsia-200/70">${candleTimeframe}</span>`;
    const selectHtml = _getAIModelSelectHtml(selectedModel, false);

    el.className = `ai-indicator-card ${colors.bg} relative overflow-hidden p-4 rounded-xl border border-amber-200/60 bg-gradient-to-br from-amber-50/50 to-orange-50/30 transition-all duration-300`;
    el.innerHTML = `
        <div class="ai-orb bg-gradient-to-r from-amber-400 to-orange-500 opacity-20"></div>
        <div class="flex flex-wrap items-center justify-between gap-y-2 mb-3 relative z-10">
            <div class="flex flex-wrap items-center gap-1.5 sm:gap-2">
                <span class="text-base drop-shadow-sm">🤖</span>
                <span class="font-black text-xs text-amber-900 tracking-wide uppercase" id="aiIndicatorHeader">BotBulls AI Analysis - ${data.ticker || selectedTicker || '---'}</span>
                ${badgeHtml}
                ${usedModelHtml}
                ${candleTimeframeHtml}
                ${cachedBadge}
            </div>
            <div class="flex items-center gap-2 relative z-30">
                ${selectHtml}
                <button onclick="openAIInfoModal()" title="How AI analysis works" class="w-5 h-5 rounded-full flex items-center justify-center text-[10px] font-bold text-amber-700 hover:text-amber-900 bg-amber-100 hover:bg-amber-200 transition-colors border border-amber-300/50 shadow-sm">
                    ℹ
                </button>
                <button id="aiRefreshBtn" onclick="refreshAIIndicator()" title="Force refresh AI analysis" class="w-5 h-5 rounded-full flex items-center justify-center text-xs text-amber-700 hover:text-amber-900 bg-amber-100 hover:bg-amber-200 transition-colors border border-amber-300/50 shadow-sm">
                    ↻
                </button>
            </div>
        </div>

        <div class="flex items-center justify-between mb-3 relative z-10">
            <div class="flex items-center gap-2 text-left">
                <div>
                    <div class="text-[0.55rem] text-slate-400 uppercase font-black tracking-wider">Trade Signal</div>
                    <div class="flex items-center gap-1.5 mt-0.5">
                        <span class="px-2 py-0.5 text-xs font-black rounded-full border ${colors.badge} flex items-center gap-1">
                            ${signal} <span class="text-[9px] ${colors.iconCls}">${colors.icon}</span>
                        </span>
                    </div>
                </div>
            </div>
            <div class="text-right">
                <div class="text-[0.55rem] text-slate-400 uppercase font-black tracking-wider">Volatility Regime</div>
                <div class="text-xs font-black ${colors.regimeCls}">${regime}</div>
            </div>
        </div>

        <div class="mb-3.5 relative z-10 text-left">
            <div class="text-[0.55rem] text-slate-500 font-extrabold uppercase tracking-wider mb-1">Decision Confidence <span class="normal-case font-semibold tracking-normal">(composite)</span></div>
            <div class="flex items-center gap-2">
                <div class="flex-1 bg-slate-200/50 rounded-full h-1.5 overflow-hidden">
                    <div class="h-1.5 rounded-full ${confBarColor} transition-all duration-500" style="width: ${confBarWidth}"></div>
                </div>
                <span class="text-[10px] font-black leading-none ${confTextColor} px-1.5 py-0.5 rounded bg-white/60 border border-slate-200/50 shadow-sm">${confPct}%</span>
            </div>
        </div>

        <div class="mb-3.5 relative z-10 text-left">
            <div class="text-[0.55rem] text-slate-500 font-extrabold uppercase tracking-wider mb-1">News Sentiment Score <span class="normal-case font-semibold tracking-normal">(0–100%, ${newsDirection})</span></div>
            <div class="flex items-center gap-2">
                <div class="flex-1 bg-slate-200/50 rounded-full h-1.5 overflow-hidden">
                    <div class="h-1.5 rounded-full ${sentBarColor} transition-all duration-500" style="width: ${sentBarWidth}"></div>
                </div>
                <span class="text-[10px] font-black leading-none ${sentTextColor} px-1.5 py-0.5 rounded bg-white/60 border border-slate-200/50 shadow-sm">${sentPct}%</span>
            </div>
        </div>

        <div class="mb-3 p-2 rounded-lg ${data.news_alignment_applied ? 'bg-amber-50/80 border-amber-300/70' : 'bg-slate-50/70 border-slate-200/70'} border text-left relative z-10">
            <span class="text-[0.52rem] font-black ${data.news_alignment_applied ? 'text-amber-700' : 'text-slate-500'} uppercase tracking-widest block mb-0.5">News / Decision Alignment</span>
            <p class="text-[0.62rem] ${data.news_alignment_applied ? 'text-amber-800' : 'text-slate-600'} leading-snug font-semibold">${alignmentReason || (rawModelSignal === signal ? 'The composite decision is consistent with the directional news read.' : `Model ${rawModelSignal} was adjusted to ${signal}.`)}</p>
        </div>

        <div class="mb-3 p-2 rounded-lg bg-fuchsia-50/50 border border-fuchsia-200/50 text-left relative z-10">
            <span class="text-[0.52rem] font-black text-fuchsia-600 uppercase tracking-widest block mb-0.5">🔥 Key Market Factor</span>
            <p class="text-[0.65rem] font-bold text-fuchsia-800 leading-snug">${sentKeyFactor}</p>
        </div>

        ${sentSummary ? `
        <div class="mb-3 p-2.5 rounded-lg bg-white/50 border border-purple-200/50 text-left relative z-10">
            <span class="text-[0.52rem] font-black text-purple-600 uppercase tracking-widest block mb-0.5">📰 AI News Briefing & Catalyst Summary</span>
            <p class="text-[0.6rem] text-violet-700 leading-relaxed font-medium">${sentSummary}</p>
        </div>
        ` : ''}

        ${summary ? `
        <div class="mb-3 p-3 rounded-lg bg-white/30 border border-violet-200/50 text-left relative z-10 shadow-sm">
            <span class="text-[0.52rem] font-black text-violet-600 uppercase tracking-widest block mb-1">📝 Comprehensive Trade Thesis Summary</span>
            <p class="text-[0.62rem] text-violet-700 leading-relaxed font-semibold">${summary}</p>
        </div>
        ` : ''}

        <div class="grid grid-cols-2 gap-2 mb-3 relative z-10">
            <div class="p-2.5 rounded-lg bg-emerald-50/40 border border-emerald-200/40 text-left shadow-sm">
                <span class="text-[0.52rem] font-black text-emerald-600 uppercase tracking-widest block mb-1">⚡ Primary Catalyst</span>
                <p class="text-[0.6rem] text-emerald-800 leading-snug font-bold">${catalyst || 'Confluence alignment'}</p>
            </div>
            <div class="p-2.5 rounded-lg bg-rose-50/40 border border-rose-200/40 text-left shadow-sm">
                <span class="text-[0.52rem] font-black text-rose-600 uppercase tracking-widest block mb-1">⚠ Main Risk Factors</span>
                <p class="text-[0.6rem] text-rose-800 leading-snug font-bold">${risks || 'Trend invalidation'}</p>
            </div>
        </div>

        <div class="flex items-center justify-between pt-2 border-t border-violet-200/30 text-[8px] text-violet-400 relative z-10">
            <span>${headlineCount} headlines analyzed</span>
            <span>Advisory only — not investment advice</span>
        </div>
    `;
}

window.refreshAIIndicator = async function () {
    const refreshBtn = document.getElementById('aiRefreshBtn');
    if (refreshBtn) {
        refreshBtn.classList.add('animate-spin');
        refreshBtn.disabled = true;
    }
    const ticker = (typeof selectedTicker !== 'undefined' ? selectedTicker : '') || 'TSLA';
    const timeframe = currentBackendTf || (typeof selectedTimeframe !== 'undefined' ? selectedTimeframe : '') || '1Hour';
    try {
        await fetchAIIndicator(ticker, timeframe, true);
    } catch (e) {
        console.error('[ai-indicator] Refresh failed:', e);
    } finally {
        if (refreshBtn) {
            refreshBtn.classList.remove('animate-spin');
            refreshBtn.disabled = false;
        }
    }
};

window.openAIInfoModal = function () {
    const modal = document.getElementById('aiInfoModal');
    if (modal) modal.classList.remove('hidden');
};

window.closeAIInfoModal = function () {
    const modal = document.getElementById('aiInfoModal');
    if (modal) modal.classList.add('hidden');
};

// 3. Render Signal Confluence Grid
// ──────────────────────────────────────────────

// Premium indicator tooltip descriptions
const PREMIUM_INFO = {
    'BotBulls1': 'Confirmed WaveTrend reversal with MFI reclaim/rejection, then price confirmation within the BB1 window. Confluence stays bullish after the last BB1 BUY marker and bearish after the last BB1 SELL marker.',
    'BotBulls2': 'Adaptive ATR trailing stop with trend-smoothed EMA and reversal zone detection. Fires on trend flips confirmed by multi-condition confluence scoring (1-4 strength rating).',
    'BotBulls3': 'Heikin-Ashi noise filter + ATR-based trailing stop. Generates buy/sell alerts only on confirmed momentum flips — removing false signals from choppy markets.',
    'Botbulls4 News AI': 'Groq-powered real-time AI news sentiment analysis. Parses current headlines and financial catalysts to determine market direction.',
};
const PREMIUM_SIGNAL_NAMES = new Set(['BotBulls1', 'BotBulls2', 'BotBulls3', 'Botbulls4 News AI']);

function lockSignalConfluencePanelHeight() {
    const panel = document.getElementById('signalConfluencePanel');
    if (!panel) return;

    const height = Math.ceil(panel.getBoundingClientRect().height);
    const lockedHeight = parseFloat(panel.style.minHeight) || 0;
    if (height > lockedHeight + 1) {
        panel.style.minHeight = `${height}px`;
        panel.dataset.heightLocked = 'true';
    }
}

function setSignalConfluenceLoadingState(isLoading) {
    const panel = document.getElementById('signalConfluencePanel');
    const loading = document.getElementById('gridLoading');
    if (panel) panel.classList.toggle('signal-confluence-loading', isLoading);
    if (loading) loading.classList.toggle('hidden', !isLoading);
}

function buildMissingSignalPlaceholder(name, prefs) {
    const locallyEnabled = prefs && prefs[name] === true;
    return {
        signal: 'NEUTRAL',
        reason: locallyEnabled ? 'Waiting for refreshed values...' : 'Disabled - click to enable',
        enabled: locallyEnabled,
        toggle_key: INDICATOR_TOGGLE_KEYS[name],
        premium: PREMIUM_SIGNAL_NAMES.has(name),
    };
}

function renderSignals(signals, action, reason, bullishCount, bearishCount, options = {}) {
    // If the user is currently rapid-fire toggling indicators, ignore incoming dashboard data
    // to prevent reverting their optimistic UI changes. The final debounce will fetch the real state.
    if (window._indicatorToggleDebounce && !window._indicatorRefreshRenderAllowed) return;

    const grid = document.getElementById('signalGrid');
    const premiumGrid = document.getElementById('premiumSignalGrid');
    const loading = document.getElementById('gridLoading');
    signals = signals || {};

    if (Object.keys(signals).length === 0 && !options.allowEmpty) {
        setSignalConfluenceLoadingState(true);
        return;
    }

    setSignalConfluenceLoadingState(false);

    const activeIds = new Set();
    const premiumActiveIds = new Set();
    const prefs = getGlobalIndicatorPrefs();
    const displaySignals = {};
    for (const name of ALL_SIGNAL_NAMES) {
        displaySignals[name] = signals[name] || buildMissingSignalPlaceholder(name, prefs);
    }
    for (const [name, data] of Object.entries(signals)) {
        if (!displaySignals[name]) displaySignals[name] = data;
    }

    for (const [name, data] of Object.entries(displaySignals)) {
        const isPremium = data.premium === true;
        const targetGrid = isPremium ? premiumGrid : grid;
        if (!targetGrid) continue;

        // Local prefs override backend 'enabled' flag
        const hasLocalPref = Object.prototype.hasOwnProperty.call(prefs, name);
        const isEnabled = hasLocalPref ? prefs[name] === true : data.enabled !== false;

        // Sync Botbulls4 News AI with actual cached AI Indicator score before style evaluation
        if (name === 'Botbulls4 News AI') {
            applyCachedNewsAISignal(data, {
                ticker: typeof selectedTicker !== 'undefined' ? selectedTicker : '',
                timeframe: typeof currentBackendTf !== 'undefined' ? currentBackendTf : '',
                params: window.lastDashboardData?.indicator_parameters || {},
            });
        }

        // Determine CSS class
        let signalClass;
        if (!isEnabled) {
            signalClass = 'disabled-signal';
        } else if (isPremium) {
            signalClass = data.signal === 'BULLISH' ? 'premium-bullish' : data.signal === 'BEARISH' ? 'premium-bearish' : 'premium-neutral';
        } else {
            signalClass = data.signal === 'BULLISH' ? 'bullish' : data.signal === 'BEARISH' ? 'bearish' : 'neutral';
        }

        const icon = data.signal === 'BULLISH' ? '▲' : data.signal === 'BEARISH' ? '▼' : '●';
        const iconColor = data.signal === 'BULLISH' ? 'text-emerald-600' : data.signal === 'BEARISH' ? 'text-red-500' : 'text-purple-400';

        const cardId = 'signal-card-' + name.replace(/\s+/g, '-');
        if (isPremium) {
            premiumActiveIds.add(cardId);
        } else {
            activeIds.add(cardId);
        }

        let card = document.getElementById(cardId);
        let isNew = false;
        if (!card) {
            card = document.createElement('div');
            card.id = cardId;
            isNew = true;
        }

        const newClassName = `signal-card group ${signalClass} fade-in cursor-pointer transition-all duration-300`;
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

        const cacheKey = `${isEnabled}-${data.signal}-${data.reason}-${data.value || ''}`;
        if (card.dataset.cacheKey !== cacheKey) {
            let reasonHtml = data.reason;
            const isNewsSentiment = name === 'Botbulls4 News AI';

            if (isNewsSentiment && data.value != null && isEnabled) {
                const scoreVal = parseFloat(data.value);
                const sentPct = Math.round(((scoreVal + 1.0) / 2.0) * 100);
                let scoreColor = 'text-slate-500';

                const currentParams = window.lastDashboardData?.indicator_parameters || {};
                const bullishThresh = currentParams['SENTIMENT_BULLISH_PERCENTAGE'] !== undefined
                    ? currentParams['SENTIMENT_BULLISH_PERCENTAGE']
                    : (typeof INDICATOR_DEFAULTS !== 'undefined' ? INDICATOR_DEFAULTS['SENTIMENT_BULLISH_PERCENTAGE'] : 70);

                const bearishThresh = currentParams['SENTIMENT_BEARISH_PERCENTAGE'] !== undefined
                    ? currentParams['SENTIMENT_BEARISH_PERCENTAGE']
                    : (typeof INDICATOR_DEFAULTS !== 'undefined' ? INDICATOR_DEFAULTS['SENTIMENT_BEARISH_PERCENTAGE'] : 30);

                if (sentPct >= bullishThresh) {
                    scoreColor = 'text-emerald-600';
                } else if (sentPct <= bearishThresh) {
                    scoreColor = 'text-rose-600';
                }

                let reasonText = (data.reason || '').toLowerCase().replace('catalysts', 'catalyst');
                if (reasonText.includes('disabled')) {
                    reasonText = "evaluating market sentiment";
                }
                reasonText = reasonText.charAt(0).toUpperCase() + reasonText.slice(1);
                reasonHtml = `${reasonText}: <span class="${scoreColor} font-black">${sentPct}%</span>`;
            }

            if (isPremium) {
                // Premium card with ⓘ info tooltip
                card.innerHTML = `
                    <div class="flex items-start justify-between">
                        <div class="flex items-start gap-1">
                            <span class="font-bold text-amber-800 ${!isEnabled ? 'opacity-50' : ''} transition-opacity duration-300">${name}</span>
                            <div class="group/tooltip relative inline-flex items-center">
                                <svg onclick="event.stopPropagation(); event.preventDefault(); typeof openInfoModal === 'function' ? openInfoModal('${name}') : null" xmlns="http://www.w3.org/2000/svg" class="text-amber-400 hover:text-amber-600 cursor-pointer transition-colors" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                                    <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M13 16h-1v-4h-1m1-4h.01M21 12a9 9 0 11-18 0 9 9 0 0118 0z" />
                                </svg>
                                <div class="invisible group-hover/tooltip:visible opacity-0 group-hover/tooltip:opacity-100 transition-all absolute bottom-full left-1/2 -translate-x-1/2 mb-2 px-3 py-1.5 bg-amber-900 text-white text-[0.65rem] leading-snug rounded-lg shadow-2xl z-[999] pointer-events-none whitespace-nowrap text-center font-bold normal-case tracking-normal">
                                    Click to read guide
                                    <div class="absolute top-full left-1/2 -translate-x-1/2 border-4 border-transparent border-t-amber-900"></div>
                                </div>
                            </div>
                            <button class="opacity-70 hover:opacity-100 transition-opacity p-0.5 rounded hover:bg-amber-100/50" 
                                    onclick="event.stopPropagation(); openIndicatorSettings('${name}')"
                                    title="Indicator Settings">
                                <svg xmlns="http://www.w3.org/2000/svg" class="text-amber-600" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                                    <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2.5" d="M10.325 4.317c.426-1.756 2.924-1.756 3.35 0a1.724 1.724 0 002.573 1.066c1.543-.94 3.31.826 2.37 2.37a1.724 1.724 0 001.065 2.572c1.756.426 1.756 2.924 0 3.35a1.724 1.724 0 00-1.066 2.573c.94 1.543-.826 3.31-2.37 2.37a1.724 1.724 0 00-2.572 1.065c-.426 1.756-2.924 1.756-3.35 0a1.724 1.724 0 00-2.573-1.066c-1.543.94-3.31-.826-2.37-2.37a1.724 1.724 0 00-1.065-2.572c-1.756-.426-1.756-2.924 0-3.35a1.724 1.724 0 001.066-2.573c-.94-1.543.826-3.31 2.37-2.37.996.608 2.296.07 2.572-1.065z" />
                                    <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2.5" d="M15 12a3 3 0 11-6 0 3 3 0 016 0z" />
                                </svg>
                            </button>
                        </div>
                        <span class="${iconColor} text-sm font-bold ${!isEnabled ? 'opacity-50 grayscale' : ''} transition-all duration-300">${icon}</span>
                    </div>
                    <p class="text-amber-700 ${!isEnabled ? 'opacity-50' : ''} transition-opacity duration-300">${reasonHtml}</p>
                `;
            } else {
                // Standard signal card
                card.innerHTML = `
                    <div class="flex items-start justify-between">
                        <div class="flex items-start gap-1">
                            <span class="font-bold text-indigo-900 ${!isEnabled ? 'opacity-50' : ''} transition-opacity duration-300">${name}</span>
                            <div class="group/tooltip relative inline-flex items-center">
                                <svg onclick="event.stopPropagation(); event.preventDefault(); typeof openInfoModal === 'function' ? openInfoModal('${name}') : null" xmlns="http://www.w3.org/2000/svg" class="text-indigo-300 hover:text-indigo-600 cursor-pointer transition-colors" fill="none" viewBox="0 0 24 24" stroke="currentColor">
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
                                    onclick="event.stopPropagation(); openIndicatorSettings('${name}')"
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
            targetGrid.appendChild(card);
        }
    }

    // Remove stale cards from standard grid
    Array.from(grid.children).forEach(child => {
        if (!activeIds.has(child.id)) {
            grid.removeChild(child);
        }
    });

    // Remove stale cards from premium grid
    if (premiumGrid) {
        Array.from(premiumGrid.children).forEach(child => {
            if (!premiumActiveIds.has(child.id)) {
                premiumGrid.removeChild(child);
            }
        });
    }

    // Update counts
    document.getElementById('bullCount').textContent = `${bullishCount} Bullish`;
    document.getElementById('bearCount').textContent = `${bearishCount} Bearish`;


    // Sync chart overlays to match which indicators are enabled here
    _syncIndicatorsFromSignals(signals);

    // Capture only the final real-signal height. Temporary loading placeholders
    // can contain more cards than the normal desktop layout and must not enlarge it.
    if (!(options.allowEmpty && Object.keys(signals).length === 0)) {
        lockSignalConfluencePanelHeight();
    }
}

// Map signal name → chart indicator key
const SIGNAL_TO_CHART_KEY = {
    'RSI': 'rsi',
    'MACD': 'macd',
    'EMA Cross': 'ema',
    'Supertrend': 'supertrend',
    'Bollinger': 'bollinger',
    'VWAP': 'vwap',
    'Mystic Pulse': 'mystic',
    'SMA': 'sma',
    'Prior Day High/Low': 'daily_high_low',
    'Pivot Points (S/R)': 'pivot_points',
    'Volume Profile': 'volume_profile',
    'Swing Points': 'swing_points',
    'ADX Trend': 'adx',
    'BotBulls1': 'botbulls1',
    'BotBulls2': 'botbulls2',
    'BotBulls3': 'botbulls3',
    'Candle Patterns': 'Candle Patterns',
};

function _syncIndicatorsFromSignals(signals) {
    let changed = false;
    const prefs = getGlobalIndicatorPrefs();
    for (const [name, key] of Object.entries(SIGNAL_TO_CHART_KEY)) {
        // If there's no explicitly saved preference, default to OFF.
        const shouldShow = prefs[name] === true;
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
                        <span class="font-black text-sm text-indigo-950 tracking-tight">${ticker}</span>
                    </div>
                    
                    <div class="flex items-center gap-3">
                        <span class="text-xs font-bold text-slate-500 font-mono tracking-tight">${price}</span>
                        <div class="opacity-0 group-hover:opacity-100 transition-opacity">
                            <button class="p-1 rounded hover:bg-rose-50 text-rose-300 hover:text-rose-400 transition-all"
                                title="Remove from Watchlist"
                                onclick="event.stopPropagation(); removeFromWatchlist('${ticker}')">
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
        row.innerHTML = `
            <td class="py-2.5 pr-4 text-purple-600 text-xs">${formatLocalTime(trade.time)}</td>
            <td class="py-2.5 pr-4 ${actionColor} text-xs">${trade.action}</td>
            <td class="py-2.5 pr-4 text-indigo-950 font-semibold text-xs">${trade.ticker}</td>
            <td class="py-2.5 pr-4 text-center">
                <span class="px-1.5 py-0.5 rounded-md bg-indigo-50 text-indigo-500 font-black text-[0.55rem] border border-indigo-100">${trade.timeframe || '5M'}</span>
            </td>
            <td class="py-2.5 pr-4 text-indigo-700 font-medium text-xs">${trade.price}</td>
            <td class="py-2.5 pr-4 text-center text-[0.65rem] font-bold text-indigo-900">${trade.qty && !isNaN(trade.qty) ? Number(trade.qty).toFixed(4) : (trade.qty || '—')}</td>
            <td class="py-2.5 pr-4 text-center text-[0.65rem] font-bold text-emerald-600">$${trade.total_cost ? Number(trade.total_cost).toFixed(2) : '—'}</td>
            <td class="py-2.5 pr-4 text-center text-[0.65rem] text-purple-400 font-mono">$${trade.fees ? Number(trade.fees).toFixed(2) : '0.00'}</td>
            ${plHtml}
            <td class="py-2.5 pr-4 text-xs">
                <span class="px-1.5 py-0.5 rounded bg-purple-100 text-purple-700 font-mono text-[0.6rem]">${signalBadge}</span>
            </td>
            <td class="py-2.5 pr-4 text-purple-500 italic text-xs max-w-xs truncate" title="${trade.reason}">${trade.reason}</td>
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
            ? `${API_BASE}/api/dashboard?ticker=${encodeURIComponent(requestedTicker)}&timeframe=${encodeURIComponent(currentBackendTf)}&mode=${encodeURIComponent(mode)}&source=dashboard`
            : `${API_BASE}/api/dashboard?mode=${mode}&timeframe=${encodeURIComponent(currentBackendTf)}&source=dashboard`;

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
                const globalAiStr = localStorage.getItem('latest_global_ai_analysis');
                if (globalAiStr) {
                    const globalAi = JSON.parse(globalAiStr);
                    const selectedModel = normalizeSelectedAIModel(localStorage.getItem('selected_ai_model'));
                    if (isAICacheForContext(globalAi, selectedTicker, currentBackendTf, selectedModel)) {
                        renderAIIndicatorCard(globalAi.data, true);
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
            aiHeaderEl.textContent = `BotBulls AI Analysis - ${selectedTicker}`;
        }

        if (summaryEl) {
            if (data.sentiment_summary) {
                summaryEl.innerHTML = `
                    <div class="mb-3">
                        <span class="text-[10px] uppercase tracking-wider text-amber-600 font-black block mb-1">🔥 Key Market Factor</span>
                        <p class="text-amber-950 font-bold not-italic text-sm leading-snug">${data.sentiment_key_factor || 'Mixed Catalysts'}</p>
                    </div>
                    <div class="space-y-2">
                        <span class="text-[10px] uppercase tracking-wider text-orange-600 font-black block mb-1">📰 AI News Briefing & Catalyst Summary</span>
                        <p class="text-amber-900 font-medium not-italic leading-relaxed text-xs">${data.sentiment_summary}</p>
                    </div>
                    <div class="mt-4 pt-3 border-t border-amber-200/50 flex justify-between items-center text-[10px] font-black uppercase text-amber-700">
                         <span>Analysis Confidence Index</span>
                         <span class="font-extrabold text-xs text-orange-600 bg-orange-100 px-2 py-0.5 rounded border border-orange-200 shadow-sm">${Math.round(data.sentiment_confidence * 100)}%</span>
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

        // Update chart label + star state
        if (selectedTicker) {
            updateActiveTickerDisplay(selectedTicker);
            _updateStarState();
            // Chart candles are owned by /api/chart so dashboard heartbeats do not
            // trigger full redraws or overlay/oscillator recalculation.
        }

        // Watchlist scans are price-only. Full signals belong to the chart's primary ticker.
        const activeScan = (
            !isStaleTickerResponse &&
            normalizeTicker(data.primaryTicker) === selectedTicker
        ) ? data.primaryScan : null;



        // Signals
        if (activeScan && mode !== 'fast') {
            if (normalizeTicker(data.primaryTicker) === normalizeTicker(selectedTicker)) {
                window._lastSignalsTicker = normalizeTicker(selectedTicker);
            }
            updateSizingPanel(activeScan);
        }

        if (mode !== 'fast' && !isStaleTickerResponse) {
            recomputeConfluence();
        }

        // Cache data first so renderWatchlist can access it safely
        window.lastDashboardData = data;

        // Watchlist
        renderWatchlist(data.watchlistScans, data.watchlist);

        if (data.indicator_parameters) {
            const macdFast = data.indicator_parameters.MACD_FAST || 12;
            const macdSlow = data.indicator_parameters.MACD_SLOW || 26;
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
                            div.innerHTML = `
                                <div class="flex flex-col">
                                    <span class="font-black text-indigo-900">${item.symbol}</span>
                                    <span class="text-[0.65rem] text-indigo-400 font-medium truncate w-40">${item.name}</span>
                                </div>
                                <span class="text-[0.6rem] bg-indigo-100 text-indigo-500 px-2 py-0.5 rounded uppercase font-bold tracking-widest">+ View</span>
                            `;
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
    try {
        const headers = await getAuthHeaders();
        await fetchWithTimeout(`${API_BASE}/api/settings/indicators?context=dashboard`, {
            method: 'POST',
            headers: headers,
            body: JSON.stringify({ [key]: value })
        }, 8000);

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
// 10b. Background Bots Data Prefetch
// Fires after fetchDashboard('fast') to populate sessionStorage
// so bots.js can render instantly on navigation.
// ──────────────────────────────────────────────
async function prefetchBotsData() {
    if (!shouldRunDashboardRefresh()) {
        return;
    }
    try {
        const headers = await getAuthHeaders();
        const url = `${API_BASE}/api/dashboard?mode=fast&source=bots`;
        const response = await fetch(url, { headers });
        if (!response.ok) return;
        const data = await response.json();
        // Make available cross-page via sessionStorage (persists until browser close)
        try {
            sessionStorage.setItem('botsDataCache', JSON.stringify({
                data,
                fetchedAt: Date.now()
            }));
        } catch (e) {
            console.warn('[prefetch] Could not cache bots data in sessionStorage:', e);
        }
        // Also make available if user navigates within same session without a full reload
        window.lastBotsData = data;
        console.log('[prefetch] Bots data cached successfully for instant bots page load.');
    } catch (e) {
        console.warn('[prefetch] Bots prefetch failed silently:', e);
    }
}

// ──────────────────────────────────────────────
// 11. Init
// ──────────────────────────────────────────────
document.addEventListener('DOMContentLoaded', () => {
    console.log('[dashboard] Initializing components...');
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

            // PHASE 1: Chart candles first — highest priority
            let chartWarmup = null;
            if (selectedTicker) {
                console.log(`[dashboard] Phase 1 — Fetching completed candles for: ${selectedTicker}`);
                chartWarmup = loadSelectedTickerChart({ renderCached: true, fetchFresh: true });
            }
            startActiveChartHeartbeat();

            // PHASE 2: Dashboard shell/watchlist — give chart candles the first network lane.
            console.log('[dashboard] Phase 2 — Scheduling fast dashboard scan after chart priority window...');
            let dashboardWarmupStarted = false;
            const startDashboardWarmup = () => {
                if (dashboardWarmupStarted || !shouldRunDashboardRefresh()) return;
                dashboardWarmupStarted = true;
                fetchDashboard('fast');
            };
            const dashboardWarmupTimer = setTimeout(startDashboardWarmup, chartWarmup ? 1200 : 0);
            if (chartWarmup && typeof chartWarmup.finally === 'function') {
                chartWarmup.finally(() => {
                    clearTimeout(dashboardWarmupTimer);
                    startDashboardWarmup();
                });
            }

            // We intentionally do NOT await the warmups so the UI does not block if one of them hangs.
            // PHASE 3: Bots data prefetch — delayed/idle so it never competes with chart startup.
            console.log('[dashboard] Phase 3 — Scheduling bots prefetch for idle time...');
            const runBotsPrefetch = () => {
                if (!shouldRunDashboardRefresh()) return;
                prefetchBotsData();
            };
            setTimeout(() => {
                if (window.requestIdleCallback) {
                    window.requestIdleCallback(runBotsPrefetch, { timeout: 2000 });
                } else {
                    runBotsPrefetch();
                }
            }, 3000);

            // PHASE 4: All remaining background data — lowest priority, after chart + dashboard shell.
            setTimeout(async () => {
                if (!shouldRunDashboardRefresh()) return;
                console.log('[dashboard] Phase 4 — Loading heavy background data...');
                fetchDashboard('heavy', { queueIfBusy: true });
                const runBackground = async () => {
                    if (!shouldRunDashboardRefresh()) return;
                    fetchGenericDashboardCards();
                    await loadOllamaModels();
                };
                if (window.requestIdleCallback) {
                    window.requestIdleCallback(runBackground, { timeout: 1500 });
                } else {
                    setTimeout(runBackground, 0);
                }
                if (selectedTicker && shouldRunDashboardRefresh()) fetchAIIndicator(selectedTicker, currentBackendTf);
            }, 2500);

            // Heartbeat refresh
            setInterval(() => {
                if (!shouldRunDashboardRefresh()) return;
                if (window._indicatorToggleDebounce || window._dashboardRefreshInFlight) return;
                fetchDashboard('heavy');
            }, REFRESH_INTERVAL);
            setInterval(() => {
                if (!shouldRunDashboardRefresh()) return;
                fetchGenericDashboardCards();
            }, 60000);
        }
    }, 50);

    document.addEventListener('visibilitychange', () => {
        if (shouldRunDashboardRefresh()) {
            fetchDashboard('fast');
            fetchGenericDashboardCards();
            fetchLivePriceForActiveTicker();
            refreshCompletedCandlesIfDue();
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

        return `
            <tr class="hover:bg-indigo-50/30 transition-colors">
                <td class="p-4 font-medium text-indigo-900">${t.entry_time}</td>
                <td class="p-4 font-medium text-indigo-400">${t.exit_time}</td>
                <td class="p-4 font-black text-indigo-900 tracking-tight">$${t.entry_price} → $${t.exit_price}</td>
                <td class="p-4 text-center text-indigo-900 font-bold">${t.qty || '—'}</td>
                <td class="p-4 text-center text-indigo-600 font-medium">$${t.entry_cost ? Number(t.entry_cost).toFixed(2) : '—'}</td>
                <td class="p-4 text-center text-emerald-600 font-bold">$${t.exit_value ? Number(t.exit_value).toFixed(2) : '—'}</td>
                <td class="p-4 text-center text-purple-400 font-mono text-[0.6rem]">$${t.fees ? Number(t.fees).toFixed(2) : '0.00'}</td>
                <td class="p-4 text-center">
                    <span class="inline-block px-2 py-1 rounded-md ${plBg} ${plColor} font-black text-[0.7rem] min-w-[60px]">
                        ${isWin ? '+' : ''}${t.pl_pct}%
                    </span>
                </td>
                <td class="p-4 text-[0.65rem] font-bold text-indigo-400 uppercase tracking-tighter">${t.reason}</td>
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
        BOTBULLS1_WT_CHANNEL: 'WaveTrend Channel Period',
        BOTBULLS1_WT_AVERAGE: 'WaveTrend Average Period',
        BOTBULLS1_WT_OVERSOLD: 'WT Oversold Level',
        BOTBULLS1_WT_OVERBOUGHT: 'WT Overbought Level',
        BOTBULLS1_MFI_BUY_RECLAIM: 'MFI Buy Reclaim Level (0-100)',
        BOTBULLS1_MFI_SELL_REJECT: 'MFI Sell Reject Level (0-100)',
        BOTBULLS1_CONFIRM_LOOKBACK: 'WT Extreme Lookback Candles',
        BOTBULLS1_REQUIRE_MFI_CONFIRM: 'Require MFI Confirmation (1=On, 0=Off)',
        BOTBULLS1_REQUIRE_CANDLE_CONFIRM: 'Require Candle Confirmation (1=On, 0=Off)',
        BOTBULLS1_REQUIRE_PRICE_CONFIRM: 'Require Price EMA Confirmation (1=On, 0=Off)',
        BOTBULLS1_PRICE_CONFIRM_EMA: 'Price Confirmation EMA Period',
        BOTBULLS1_PRICE_CONFIRM_LOOKBACK: 'Price Confirmation Window Candles',
    };

    configKeys.forEach(key => {
        const hasOverride = overrides[key] !== undefined && overrides[key] !== null && overrides[key] !== '';
        const val = currentParams[key] !== undefined ? currentParams[key] : (INDICATOR_DEFAULTS[key] !== undefined ? INDICATOR_DEFAULTS[key] : '');
        const label = paramLabels[key] || key.replace(/_/g, ' ').toLowerCase();
        const isPercent = key.endsWith('_PCT') || key.endsWith('_PERCENTAGE');
        const inputAttrs = key === 'PIVOT_LEVEL_COUNT'
            ? 'min="1" max="5" step="1"'
            : key === 'SWING_POINTS_LEVEL_COUNT'
                ? 'min="1" max="5" step="1"'
                : key === 'SWING_POINTS_LOOKBACK_DAYS'
                    ? 'min="1" max="365" step="1"'
                    : key === 'BOTBULLS1_CONFIRM_LOOKBACK'
                        ? 'min="1" max="10" step="1"'
                        : key === 'BOTBULLS1_REQUIRE_MFI_CONFIRM' || key === 'BOTBULLS1_REQUIRE_CANDLE_CONFIRM' || key === 'BOTBULLS1_REQUIRE_PRICE_CONFIRM'
                            ? 'min="0" max="1" step="1"'
                            : key === 'BOTBULLS1_PRICE_CONFIRM_EMA'
                                ? 'min="5" max="50" step="1"'
                            : key === 'BOTBULLS1_PRICE_CONFIRM_LOOKBACK'
                                ? 'min="0" max="10" step="1"'
                            : key === 'BOTBULLS1_MFI_BUY_RECLAIM' || key === 'BOTBULLS1_MFI_SELL_REJECT'
                                ? 'min="0" max="100" step="1"'
            : key === 'VOLUME_PROFILE_ROWS'
                ? 'min="12" max="120" step="1"'
                : key === 'VOLUME_PROFILE_CONFIRMATION_CANDLES'
                    ? 'min="1" max="5" step="1"'
                    : 'step="any"';

        const div = document.createElement('div');
        div.className = 'flex flex-col gap-1.5';
        const controlHtml = key === 'SWING_POINTS_ENTRY_MODE'
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
        control.value = control.tagName === 'SELECT' ? (control.dataset.default || '') : control.placeholder; // Revert to system default
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
    const updates = {};
    inputs.forEach(input => {
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
    try {
        const headers = await getAuthHeaders();
        const req = await fetch(`${API_BASE}/api/generic-dashboard-cards`, { headers });
        if (!req.ok) throw new Error(`Generic card cache request failed (${req.status})`);
        const payload = await req.json();
        writeCachedGenericDashboardCards(payload);
        await applyGenericDashboardCardsPayload(payload);
    } catch (e) {
        console.warn("[dashboard] Combined generic-card cache failed; falling back to individual cached endpoints.", e);
        fetchFearAndGreed();
        fetchMarketData();
        fetchMacroEvents();
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
                <div class="card-glass p-5 flex flex-col justify-between hover:ring-2 hover:ring-indigo-400 transition-all overflow-hidden relative group">
                    <div class="card-glow"></div>
                    <div class="relative z-10">
                        <div class="flex items-center justify-between mb-3">
                            <h4 class="font-black text-slate-700 text-[13px] tracking-wide">${comp.name}</h4>
                            <span class="${color} text-white text-[10px] px-2.5 py-1 rounded-md font-bold uppercase tracking-widest shadow-sm">${label}</span>
                        </div>
                        <p class="text-xs text-slate-500 mb-2 leading-relaxed">${comp.desc}</p>
                        <p class="text-[10px] text-slate-400 font-mono bg-slate-50/50 backdrop-blur-sm px-2 py-1 rounded border border-slate-200 inline-block truncate max-w-full">${comp.raw}</p>
                    </div>
                    <div class="mt-5 relative z-10">
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
            if (tab) tab.className = 'pb-3 text-sm font-bold border-b-2 border-indigo-600 text-indigo-700 transition-colors';
            if (view) view.classList.remove('hidden');
        } else {
            if (tab) tab.className = 'pb-3 text-sm font-bold border-b-2 border-transparent text-slate-500 hover:text-slate-700 transition-colors';
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
            const techList = ["AAPL", "MSFT", "GOOG", "NVDA", "META", "TSLA", "AVGO", "AMD", "QCOM", "NFLX", "AMZN", "ADBE", "CRM", "INTC", "CSCO", "ORCL"];
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
                    <div class="col-span-full rounded-xl border border-slate-200 bg-white/90 px-3 py-2 shadow-sm">
                        <div class="flex items-start justify-between gap-2">
                            <div>
                                <p class="text-[11px] font-black text-slate-800 uppercase tracking-wide">${summary.label || 'Sector'} Summary</p>
                                <p class="text-[9px] font-bold text-slate-400 uppercase tracking-widest">Proxy: ${summary.benchmark || '--'}</p>
                            </div>
                            <span class="px-2 py-1 rounded-lg border text-[10px] font-black ${moveClass}">${moveText}</span>
                        </div>
                        <div class="mt-2 grid grid-cols-3 gap-2 text-center">
                            <div>
                                <p class="text-[9px] font-black text-slate-400 uppercase">Positive</p>
                                <p class="text-sm font-black text-emerald-600">${summary.positive_count ?? 0}</p>
                            </div>
                            <div>
                                <p class="text-[9px] font-black text-slate-400 uppercase">Negative</p>
                                <p class="text-sm font-black text-red-600">${summary.negative_count ?? 0}</p>
                            </div>
                            <div>
                                <p class="text-[9px] font-black text-slate-400 uppercase">Part.</p>
                                <p class="text-sm font-black ${participationClass}">${participation}%</p>
                            </div>
                        </div>
                        <p class="mt-1 text-[10px] text-slate-400 font-bold">Analyzed: ${summary.analyzed_count ?? 0} • Avg constituents: ${avgMove >= 0 ? '+' : ''}${avgMove.toFixed(2)}%${summary.flat_count ? ` • Flat: ${summary.flat_count}` : ''}</p>
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
                    let spanClass = 'col-span-1 row-span-1 p-1.5 min-h-[42px] flex flex-col justify-center';
                    let fontClass = 'text-xs font-bold';
                    let changeFontClass = 'text-[9px]';

                    if (weight === 3) {
                        spanClass = 'col-span-2 row-span-2 p-2 md:p-3 flex flex-col justify-center min-h-[88px]';
                        fontClass = 'text-sm md:text-base font-black tracking-widest';
                        changeFontClass = 'text-[10px] font-bold';
                    } else if (weight === 2) {
                        spanClass = 'col-span-2 row-span-1 p-1.5 md:p-2 flex flex-col justify-center min-h-[42px]';
                        fontClass = 'text-xs md:text-sm font-black tracking-wider';
                        changeFontClass = 'text-[10px] font-bold';
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
                        '<span class="px-2 py-0.5 rounded text-[10px] font-bold bg-slate-100 text-slate-400">N/A</span>' :
                        (t.above_50 ?
                            '<span class="px-2 py-0.5 rounded text-[10px] font-bold bg-emerald-100 text-emerald-700">ABOVE</span>' :
                            '<span class="px-2 py-0.5 rounded text-[10px] font-bold bg-red-100 text-red-700">BELOW</span>'
                        );

                    const ma200Pill = t.above_200 === null ?
                        '<span class="px-2 py-0.5 rounded text-[10px] font-bold bg-slate-100 text-slate-400">N/A</span>' :
                        (t.above_200 ?
                            '<span class="px-2 py-0.5 rounded text-[10px] font-bold bg-emerald-100 text-emerald-700 font-semibold">ABOVE</span>' :
                            '<span class="px-2 py-0.5 rounded text-[10px] font-bold bg-red-100 text-red-700 font-semibold">BELOW</span>'
                        );

                    let hlPill = '<span class="px-2 py-0.5 rounded text-[10px] font-bold bg-slate-100 text-slate-400">NORMAL</span>';
                    if (t.is_high) {
                        hlPill = '<span class="px-2 py-0.5 rounded text-[10px] font-bold bg-emerald-500 text-white shadow-sm shadow-emerald-100">52W HIGH</span>';
                    } else if (t.is_low) {
                        hlPill = '<span class="px-2 py-0.5 rounded text-[10px] font-bold bg-red-500 text-white shadow-sm shadow-red-100">52W LOW</span>';
                    }

                    rowsHtml += `
                    <tr class="border-b border-slate-100 hover:bg-slate-50/50 transition-colors">
                        <td class="px-4 py-3 font-bold text-slate-800 text-xs">${t.ticker}</td>
                        <td class="px-4 py-3 font-semibold text-slate-700 text-xs text-right">$${t.price.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}</td>
                        <td class="px-4 py-3 font-bold text-xs text-right ${changeColor}">${changeSign}${t.change_pct}%</td>
                        <td class="px-4 py-3 text-center">${ma50Pill}</td>
                        <td class="px-4 py-3 text-center">${ma200Pill}</td>
                        <td class="px-4 py-3 text-center">${hlPill}</td>
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

        // Render Indicators
        const indList = document.getElementById('macro-indicators-list');
        if (indList && data.indicators) {
            let html = '<div class="grid grid-cols-1 md:grid-cols-2 gap-3">';
            const indicators = Object.values(data.indicators);

            indicators.forEach(ind => {
                if (ind.error) return;

                const isUp = ind.direction === 'up';
                const colorClass = isUp ? 'text-emerald-500' : 'text-red-500';
                const bgClass = isUp ? 'bg-emerald-100' : 'bg-red-100';
                const arrow = isUp ? '↑' : '↓';

                let newsHtml = '';
                if (ind.recent_news && ind.recent_news.length > 0) {
                    const topNews = ind.recent_news[0];
                    newsHtml = `
                    <div class="mt-3 pt-3 border-t border-slate-100">
                        <a href="${topNews.link}" target="_blank" class="block group">
                            <span class="text-[10px] font-black text-slate-400 uppercase tracking-widest block mb-1">${topNews.publisher}</span>
                            <p class="text-xs text-slate-600 font-medium group-hover:text-indigo-600 transition-colors line-clamp-2">${topNews.title}</p>
                        </a>
                    </div>`;
                }

                html += `
                <div class="bg-slate-50 border border-slate-100 rounded-xl p-5 hover:shadow-md transition-all hover:border-indigo-100">
                    <div class="flex justify-between items-start mb-2">
                        <div>
                            <span class="text-[10px] font-black text-slate-400 uppercase tracking-widest">${ind.ticker}</span>
                            <h4 class="text-sm font-bold text-slate-800 leading-tight">${ind.name}</h4>
                        </div>
                        <span class="px-2 py-1 rounded text-xs font-bold ${bgClass} ${colorClass}">
                            ${arrow} ${Math.abs(ind.change_pct)}%
                        </span>
                    </div>
                    <div class="flex items-end gap-2 mb-2">
                        <span class="text-2xl font-black text-slate-800">${ind.current_value}</span>
                        <span class="text-xs font-bold text-slate-400 mb-1">${ind.unit}</span>
                    </div>
                    <p class="text-[10px] text-slate-500 mb-2 leading-relaxed">${ind.description}</p>
                    ${newsHtml}
                </div>`;
            });

            html += '</div>';
            indList.innerHTML = html;
        }

        // Render Calendar (All week data grouped by day, past vs upcoming styled)
        const calList = document.getElementById('macro-calendar-list');
        const line = document.getElementById('macro-timeline-line');
        if (calList && data.calendar) {
            let html = '';
            const sortedCalendar = [...data.calendar].sort((a, b) => new Date(a.date) - new Date(b.date));
            if (sortedCalendar.length > 0) {
                if (line) line.classList.remove('hidden');
                const now = new Date();
                let currentGroupDay = '';

                sortedCalendar.forEach(e => {
                    const dateObj = new Date(e.date);
                    const dayGroupStr = dateObj.toLocaleDateString(undefined, { weekday: 'long', month: 'short', day: 'numeric' });
                    const timeStr = dateObj.toLocaleTimeString(undefined, { hour: 'numeric', minute: '2-digit' });
                    const isPast = dateObj < now;

                    if (dayGroupStr !== currentGroupDay) {
                        currentGroupDay = dayGroupStr;
                        const isTodayGroup = dateObj.toDateString() === now.toDateString();
                        const headerBg = isTodayGroup ? 'bg-indigo-600 text-white ring-2 ring-indigo-200' : 'bg-slate-200/80 text-slate-700';
                        html += `
                        <div class="sticky top-0 z-10 py-1.5 px-3 rounded-md text-xs font-black uppercase tracking-wider mb-5 ${headerBg} shadow-sm inline-block">
                            ${dayGroupStr} ${isTodayGroup ? '• Today' : ''}
                        </div>`;
                    }

                    let impactColor = 'bg-red-100 text-red-600';
                    let dotStyle = 'bg-red-500 ring-red-100 animate-pulse';
                    if (e.impact === 'Medium' || e.impact === 'Medium/Low') {
                        impactColor = 'bg-orange-100 text-orange-600';
                        dotStyle = 'bg-orange-500 ring-orange-100';
                    } else if (e.impact === 'Low') {
                        impactColor = 'bg-yellow-100 text-yellow-600';
                        dotStyle = 'bg-yellow-500 ring-yellow-100';
                    }

                    if (isPast) {
                        dotStyle = 'bg-slate-300 ring-slate-100';
                    }

                    const cardBg = isPast ? 'bg-slate-50/70 border-slate-100 opacity-80' : 'bg-white border-indigo-100/80 shadow-sm ring-1 ring-indigo-50 hover:shadow-md';
                    const statusBadge = isPast
                        ? `<span class="px-1.5 py-0.5 rounded text-[9px] font-black bg-slate-200 text-slate-500 uppercase tracking-widest ml-2">Completed</span>`
                        : `<span class="px-1.5 py-0.5 rounded text-[9px] font-black bg-indigo-100 text-indigo-700 uppercase tracking-widest ml-2">Upcoming</span>`;
                    const actualDisplay = macroEventActualDisplay(e, isPast);
                    const surpriseDisplay = macroEventSurpriseDisplay(e);
                    const sourceHtml = e.actual_source
                        ? `<p class="mt-2 text-[9px] font-bold text-slate-400 uppercase tracking-wide">Actual source: ${e.actual_source}${e.actual_period ? ` • ${e.actual_period}` : ''}</p>`
                        : '';
                    const actualNoteHtml = actualDisplay.note
                        ? `<p class="mt-1 text-[9px] font-medium text-slate-400 leading-snug">${actualDisplay.note}</p>`
                        : '';
                    const macroReadHtml = e.macro_read
                        ? `<div class="mt-3 rounded-lg border border-indigo-100 bg-indigo-50/70 px-3 py-2 text-[10px] font-bold text-indigo-800 leading-snug">Quant read: ${e.macro_read}</div>`
                        : '';

                    html += `
                    <div class="relative pl-12 mb-6">
                        <div class="absolute left-[-5px] top-4 w-3.5 h-3.5 ${dotStyle} rounded-full border-2 border-white shadow-sm ring-4"></div>
                        <div class="border rounded-xl p-4 transition-all ${cardBg}">
                            <div class="flex items-center justify-between mb-1.5">
                                <div class="flex items-center">
                                    <span class="text-xs font-black text-slate-500 uppercase tracking-wider">${timeStr} • ${e.country || 'USD'}</span>
                                    ${statusBadge}
                                </div>
	                                <span class="px-2 py-0.5 rounded text-[10px] font-black ${impactColor} uppercase">${e.impact || 'High'}</span>
	                            </div>
	                            <h4 class="text-base font-bold text-slate-800 mb-2">${e.title}</h4>
	                            <div class="grid grid-cols-2 md:grid-cols-4 gap-2 text-xs font-medium bg-slate-50/80 p-2.5 rounded-lg border border-slate-100/80">
	                                <div><span class="text-slate-400 font-bold block text-[10px] uppercase">Previous</span><span class="text-slate-700 font-semibold">${e.previous || 'N/A'}</span></div>
	                                <div><span class="text-slate-400 font-bold block text-[10px] uppercase">Forecast</span><span class="text-slate-700 font-semibold">${e.forecast || 'N/A'}</span></div>
	                                <div><span class="text-slate-400 font-bold block text-[10px] uppercase">Actual</span><span class="${actualDisplay.cls}">${actualDisplay.text}</span>${actualNoteHtml}</div>
	                                <div><span class="text-slate-400 font-bold block text-[10px] uppercase">Surprise</span><span class="${surpriseDisplay.cls}">${surpriseDisplay.text}</span></div>
	                            </div>
                                ${sourceHtml}
                                ${macroReadHtml}
                        </div>
                    </div>`;
                });
            } else {
                html = `<p class="text-center text-slate-500 font-bold mt-10">No high impact USD events scheduled for this week.</p>`;
                if (line) line.classList.add('hidden');
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
                const topEvents = upcomingEvents.slice(0, 3);
                topEvents.forEach(e => {
                    let badgeColor = 'bg-red-100 text-red-600';
                    if (e.impact === 'Medium' || e.impact === 'Medium/Low') {
                        badgeColor = 'bg-orange-100 text-orange-600';
                    } else if (e.impact === 'Low') {
                        badgeColor = 'bg-yellow-100 text-yellow-600';
                    }

                    const dateObj = new Date(e.date);
                    let dayStr = dateObj.toLocaleDateString(undefined, { weekday: 'short' });
                    if (dateObj.toDateString() === now.toDateString()) {
                        dayStr = 'Today';
                    } else if (dateObj.toDateString() === new Date(now.getTime() + 86400000).toDateString()) {
                        dayStr = 'Tomorrow';
                    }
                    const timeStr = dateObj.toLocaleTimeString(undefined, { hour: 'numeric', minute: '2-digit' });
                    const country = e.country || 'USD';

                    miniHtml += `
                        <div class="flex flex-col gap-0.5 w-full py-0.5 border-b border-slate-100/60 last:border-0">
                            <div class="flex items-center justify-between text-[8.5px] text-slate-500 font-bold uppercase tracking-wider">
                                <span class="flex items-center gap-1.5 truncate">
                                    <span class="w-1.5 h-1.5 rounded-full bg-indigo-500 animate-pulse shrink-0"></span>
                                    <span>${dayStr} • ${timeStr} • ${country}</span>
                                </span>
                                <span class="px-1.5 py-0.1 rounded-[2.5px] text-[7px] font-black ${badgeColor} uppercase shrink-0">${e.impact || 'High'}</span>
                            </div>
                            <span class="font-bold text-slate-800 text-[11px] truncate w-full group-hover:text-indigo-600 transition-colors" title="${e.title}">${e.title}</span>
                        </div>
                    `;
                });
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
                                <span class="truncate">[Done] ${dayStr} • ${timeStr}</span>
                                <span class="px-1.5 py-0.1 rounded-[2.5px] text-[7px] font-black ${badgeColor} uppercase shrink-0">${e.impact || 'High'}</span>
                            </div>
                            <span class="font-bold text-slate-600 text-[10.5px] truncate w-full" title="${e.title}">${e.title}</span>
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
