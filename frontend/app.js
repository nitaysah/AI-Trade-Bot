// ═══════════════════════════════════════════════════
// AI Trading Bot — Dashboard JS
// Auto-refreshing, multi-panel, real-time dashboard
// ═══════════════════════════════════════════════════

// 6. Production API Configuration (Auto-switch between Local and Cloud)
const CLOUD_URL = 'https://ai-trade-bot-backend-946557219642.us-central1.run.app';
const API_BASE = window.location.hostname === 'localhost' || window.location.hostname === '127.0.0.1'
    ? 'http://localhost:8000'
    : CLOUD_URL;
const REFRESH_INTERVAL = 60000; // 60 seconds (1 minute)
const LAST_VIEWED_TICKER_KEY = 'lastViewedTicker';

function normalizeTicker(ticker) {
    const normalized = (ticker || '').toString().trim().toUpperCase();
    return normalized && normalized !== 'NULL' && normalized !== 'UNDEFINED' ? normalized : null;
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
    const sorted = [...(rawBars || [])].sort((a, b) => a.time - b.time);
    const rangeStart = getRangeStartTime(sorted);
    const ranged = rangeStart ? sorted.filter(b => b.time >= rangeStart) : sorted;
    if (isCryptoTicker(ticker) || currentChartSession === 'extended') return ranged;
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
    info.textContent = last ? `Last ticker/day: ${normalizeTicker(ticker)} • ${formatDateTimeCT(last.time)}` : 'Last ticker/day: —';
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

function formatPrice(value) {
    if (value == null || Number.isNaN(Number(value))) return '—';
    const n = Number(value);
    return n > 10 ? n.toFixed(2) : n.toFixed(4);
}

let selectedTicker = readLastViewedTicker();
let currentBackendTf = "1Hour";
let currentChartRange = localStorage.getItem('lastChartRange') || "1M";
let currentChartSession = localStorage.getItem('lastChartSession') || "regular";
let isAlpacaLinked = false; // Track live connection status
let favoriteTickers = JSON.parse(localStorage.getItem('favoriteTickers') || '["BTCUSD", "ETHUSD", "TSLA", "AAPL", "MSFT"]');
let tvWidget = null;
let currentLogTab = "all"; // 'all' or 'trades'
let currentWatchlistTab = "all"; // 'all', 'stocks', or 'crypto'
let latestTradesData = []; // Cached log data

const CHART_INTERVALS = {
    "1Min": { label: "1m", seconds: 60, backend: "1Min" },
    "5Min": { label: "5m", seconds: 300, backend: "5Min" },
    "15Min": { label: "15m", seconds: 900, backend: "15Min" },
    "30Min": { label: "30m", seconds: 1800, backend: "30Min" },
    "1Hour": { label: "1h", seconds: 3600, backend: "1Hour" },
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

/**
 * Retrieves the current Firebase ID token and prepares headers.
 */
async function getAuthHeaders() {
    const auth = window.auth;
    if (!auth) {
        console.warn('[auth] window.auth is NOT defined yet');
        return { 'Content-Type': 'application/json' };
    }
    const user = auth.currentUser;
    if (!user) {
        if (localStorage.getItem('dev_mode') === 'true') {
            return {
                'Content-Type': 'application/json',
                'Authorization': `Bearer dev-token`
            };
        }
        console.warn('[auth] No current user found in auth instance');
        return { 'Content-Type': 'application/json' };
    }

    try {
        const token = await user.getIdToken();
        return {
            'Content-Type': 'application/json',
            'Authorization': `Bearer ${token}`
        };
    } catch (e) {
        console.error('[auth] Error getting ID token:', e);
        return { 'Content-Type': 'application/json' };
    }
}

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

// All series objects keyed by indicator name
const _overlaySeries = {};
const _oscSeries = {};

// Which indicators are currently visible (toggled by toolbar)
const _visibleIndicators = {
    ema: true,
    vwap: true,
    supertrend: false,
    bollinger: false,
    rsi: false,
    macd: false,
    mystic: false,
};

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
};

function initChart() {
    const container = document.getElementById('tradingChart');
    if (!container) return;
    container.innerHTML = '';


    lwChart = LightweightCharts.createChart(container, {
        layout: { background: { color: '#ffffff' }, textColor: '#334155' },
        grid: {
            vertLines: { color: 'rgba(168, 85, 247, 0.05)' },
            horzLines: { color: 'rgba(168, 85, 247, 0.05)' },
        },
        crosshair: { mode: LightweightCharts.CrosshairMode.Normal },
        rightPriceScale: { borderColor: 'rgba(197, 203, 206, 0.8)' },
        localization: {
            timeFormatter: time => {
                return `${formatChartTime(time, false)} CT`;
            }
        },
        timeScale: {
            borderColor: 'rgba(197, 203, 206, 0.8)',
            timeVisible: true,
            secondsVisible: false,
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
        priceScaleId: '', // Overlay independently
        scaleMargins: { top: 0, bottom: 0 },
    });

    candleSeries = lwChart.addCandlestickSeries({
        upColor: '#10b981', downColor: '#ef4444',
        borderVisible: false,
        wickUpColor: '#10b981', wickDownColor: '#ef4444',
    });

    const resizeObserver = new ResizeObserver(entries => {
        if (entries.length === 0 || !entries[0].contentRect) return;
        const { width, height } = entries[0].contentRect;
        lwChart.applyOptions({ width, height });
    });
    resizeObserver.observe(container);

    _initOscPane();
    lwChart.timeScale().subscribeVisibleTimeRangeChange(() => updateVisibleHighLow());
}

function _initOscPane() {
    // RSI Chart
    const rsiContainer = document.getElementById('oscChart_rsi');
    if (rsiContainer) {
        rsiContainer.innerHTML = '';
        oscChartRsi = LightweightCharts.createChart(rsiContainer, {
            layout: { background: { color: '#fafbff' }, textColor: '#64748b' },
            grid: { vertLines: { color: 'rgba(168,85,247,0.04)' }, horzLines: { color: 'rgba(168,85,247,0.04)' } },
            rightPriceScale: { borderColor: 'rgba(197,203,206,0.6)' },
            localization: {
                timeFormatter: time => {
                    return `${formatChartTime(time, false)} CT`;
                }
            },
            timeScale: { visible: false },
        });
        const resizeRsi = new ResizeObserver(entries => {
            if (!entries[0] || !oscChartRsi) return;
            oscChartRsi.applyOptions({ width: entries[0].contentRect.width });
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
            rightPriceScale: { borderColor: 'rgba(197,203,206,0.6)' },
            localization: {
                timeFormatter: time => {
                    return `${formatChartTime(time, false)} CT`;
                }
            },
            timeScale: { visible: false },
        });
        const resizeMacd = new ResizeObserver(entries => {
            if (!entries[0] || !oscChartMacd) return;
            oscChartMacd.applyOptions({ width: entries[0].contentRect.width });
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
            rightPriceScale: { borderColor: 'rgba(197,203,206,0.6)' },
            localization: {
                timeFormatter: time => {
                    return `${formatChartTime(time, false)} CT`;
                }
            },
            timeScale: { visible: false },
        });
        const resizeMystic = new ResizeObserver(entries => {
            if (!entries[0] || !oscChartMystic) return;
            oscChartMystic.applyOptions({ width: entries[0].contentRect.width });
        });
        resizeMystic.observe(mysticContainer);
    }

    // Sync all time scales to main chart
    lwChart.timeScale().subscribeVisibleLogicalRangeChange(range => {
        if (range) {
            if (oscChartRsi) oscChartRsi.timeScale().setVisibleLogicalRange(range);
            if (oscChartMacd) oscChartMacd.timeScale().setVisibleLogicalRange(range);
            if (oscChartMystic) oscChartMystic.timeScale().setVisibleLogicalRange(range);
        }
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
        updateLastBarInfo(rawSorted, ticker);
        updateChartControlState(ticker);
        return;
    }
    window._displayPriceHistory = sorted;
    // Background highlight for Extended Hours
    if (extHoursSeries) {
        if (!isCryptoTicker(ticker) && currentChartSession === 'extended') {
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
        // Instead of squeezing all data into the view, we set a comfortable bar spacing
        // This allows the user to see candles clearly and slide horizontally
        const totalBars = sorted.length;
        if (totalBars > 150) {
            lwChart.timeScale().setVisibleLogicalRange({
                from: totalBars - 150,
                to: totalBars + 5 // add a little empty space on the right
            });
        } else {
            lwChart.timeScale().fitContent();
        }
        window._lastFittedTicker = ticker;
        window._lastFittedRange = currentChartRange;
    }

    updateVisibleHighLow();
    try {
        if (oscChartRsi) oscChartRsi.timeScale().setVisibleLogicalRange(lwChart.timeScale().getVisibleLogicalRange());
        if (oscChartMacd) oscChartMacd.timeScale().setVisibleLogicalRange(lwChart.timeScale().getVisibleLogicalRange());
        if (oscChartMystic) oscChartMystic.timeScale().setVisibleLogicalRange(lwChart.timeScale().getVisibleLogicalRange());
    } catch (e) { }
}

function _getOrCreate(map, key, chartInst, options) {
    if (!map[key]) map[key] = chartInst.addLineSeries(options);
    return map[key];
}
function _removeSeries(map, key, chartInst) {
    if (map[key]) { try { chartInst.removeSeries(map[key]); } catch (e) { } delete map[key]; }
}

function _refreshOverlays(bars) {
    // EMA
    if (_visibleIndicators.ema) {
        _getOrCreate(_overlaySeries, 'ema_fast', lwChart, { color: IND_COLORS.ema_fast, lineWidth: 1 })
            .setData(bars.filter(b => b.ema_fast != null).map(b => ({ time: b.time, value: b.ema_fast })));
        _getOrCreate(_overlaySeries, 'ema_slow', lwChart, { color: IND_COLORS.ema_slow, lineWidth: 1 })
            .setData(bars.filter(b => b.ema_slow != null).map(b => ({ time: b.time, value: b.ema_slow })));
    } else {
        _removeSeries(_overlaySeries, 'ema_fast', lwChart);
        _removeSeries(_overlaySeries, 'ema_slow', lwChart);
    }
    // VWAP
    if (_visibleIndicators.vwap) {
        _getOrCreate(_overlaySeries, 'vwap', lwChart, { color: IND_COLORS.vwap, lineWidth: 3, lineStyle: 0 })
            .setData(bars.filter(b => b.vwap != null).map(b => ({ time: b.time, value: b.vwap })));
    } else {
        _removeSeries(_overlaySeries, 'vwap', lwChart);
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

        const clean = (arr) => {
            const seen = new Set();
            return arr.filter(p => {
                if (seen.has(p.time)) return false;
                seen.add(p.time);
                return true;
            }).sort((a, b) => a.time - b.time);
        };

        _getOrCreate(_overlaySeries, 'st_line', lwChart, {
            lineWidth: 2,
            lineType: 1 // LineType.WithSteps
        }).setData(clean(stData));

        // Clean up old multi-series if they exist
        _removeSeries(_overlaySeries, 'st_bull', lwChart);
        _removeSeries(_overlaySeries, 'st_bear', lwChart);

        // Trend Reversal Markers (Arrows)
        const markers = [];
        for (let i = 1; i < bars.length; i++) {
            const curr = bars[i];
            const prev = bars[i - 1];
            if (curr.supertrend == null || prev.supertrend == null) continue;

            if (curr.supertrend_up && !prev.supertrend_up) {
                // Flip to BULLISH (Buy)
                markers.push({
                    time: curr.time,
                    position: 'belowBar',
                    color: '#10b981',
                    shape: 'arrowUp',
                    text: 'BUY',
                    size: 1
                });
            } else if (!curr.supertrend_up && prev.supertrend_up) {
                // Flip to BEARISH (Sell)
                markers.push({
                    time: curr.time,
                    position: 'aboveBar',
                    color: '#ef4444',
                    shape: 'arrowDown',
                    text: 'SELL',
                    size: 1
                });
            }
        }
        candleSeries.setMarkers(markers);
    } else {
        _removeSeries(_overlaySeries, 'st_bull', lwChart);
        _removeSeries(_overlaySeries, 'st_bear', lwChart);
        _removeSeries(_overlaySeries, 'st_line', lwChart);
        candleSeries.setMarkers([]);
    }
    // Bollinger Bands
    if (_visibleIndicators.bollinger) {
        _getOrCreate(_overlaySeries, 'boll_upper', lwChart, { color: IND_COLORS.boll_upper, lineWidth: 2 }).setData(bars.filter(b => b.boll_upper != null).map(b => ({ time: b.time, value: b.boll_upper })));
        _getOrCreate(_overlaySeries, 'boll_middle', lwChart, { color: IND_COLORS.boll_middle, lineWidth: 2, lineStyle: 2 }).setData(bars.filter(b => b.boll_middle != null).map(b => ({ time: b.time, value: b.boll_middle })));
        _getOrCreate(_overlaySeries, 'boll_lower', lwChart, { color: IND_COLORS.boll_lower, lineWidth: 2 }).setData(bars.filter(b => b.boll_lower != null).map(b => ({ time: b.time, value: b.boll_lower })));
    } else {
        _removeSeries(_overlaySeries, 'boll_upper', lwChart);
        _removeSeries(_overlaySeries, 'boll_middle', lwChart);
        _removeSeries(_overlaySeries, 'boll_lower', lwChart);
    }
}

function _refreshOscillators(bars) {
    // Handle RSI
    const rsiContainer = document.getElementById('oscChart_rsi');
    const rsiLabel = document.getElementById('rsiLabel');
    if (rsiContainer) {
        const wasHidden = rsiContainer.style.display === 'none';
        rsiContainer.style.display = _visibleIndicators.rsi ? 'block' : 'none';
        if (rsiLabel) rsiLabel.style.display = _visibleIndicators.rsi ? 'block' : 'none';
        if (wasHidden && _visibleIndicators.rsi && oscChartRsi) {
            oscChartRsi.applyOptions({ width: rsiContainer.clientWidth, height: rsiContainer.clientHeight });
        }
    }
    if (_visibleIndicators.rsi && oscChartRsi) {
        _getOrCreate(_oscSeries, 'rsi', oscChartRsi, { color: IND_COLORS.rsi, lineWidth: 1 })
            .setData(bars.filter(b => b.rsi != null).map(b => ({ time: b.time, value: b.rsi })));
        const ob = _getOrCreate(_oscSeries, 'rsi_ob', oscChartRsi, { color: 'rgba(239,68,68,0.4)', lineWidth: 1, lineStyle: 2 });
        const os = _getOrCreate(_oscSeries, 'rsi_os', oscChartRsi, { color: 'rgba(16,185,129,0.4)', lineWidth: 1, lineStyle: 2 });
        if (bars.length) {
            ob.setData([{ time: bars[0].time, value: 70 }, { time: bars[bars.length - 1].time, value: 70 }]);
            os.setData([{ time: bars[0].time, value: 30 }, { time: bars[bars.length - 1].time, value: 30 }]);
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
        const wasHidden = macdContainer.style.display === 'none';
        macdContainer.style.display = _visibleIndicators.macd ? 'block' : 'none';
        if (macdLabel) macdLabel.style.display = _visibleIndicators.macd ? 'flex' : 'none';
        if (wasHidden && _visibleIndicators.macd && oscChartMacd) {
            oscChartMacd.applyOptions({ width: macdContainer.clientWidth, height: macdContainer.clientHeight });
        }
    }
    if (_visibleIndicators.macd && oscChartMacd) {
        // Histogram
        if (!_oscSeries['macd_hist']) {
            _oscSeries['macd_hist'] = oscChartMacd.addHistogramSeries({
                base: 0,
                priceScaleId: '', // Attach to same scale as lines
            });
        }
        const histData = bars.filter(b => b.macd_hist != null).map(b => ({
            time: b.time,
            value: b.macd_hist,
            color: b.macd_hist >= 0 ? 'rgba(16, 185, 129, 0.5)' : 'rgba(239, 68, 68, 0.5)'
        }));
        _oscSeries['macd_hist'].setData(histData);

        _getOrCreate(_oscSeries, 'macd_line', oscChartMacd, { color: IND_COLORS.macd_line, lineWidth: 1 })
            .setData(bars.filter(b => b.macd_line != null).map(b => ({ time: b.time, value: b.macd_line })));
        _getOrCreate(_oscSeries, 'macd_signal', oscChartMacd, { color: IND_COLORS.macd_signal, lineWidth: 1 })
            .setData(bars.filter(b => b.macd_signal != null).map(b => ({ time: b.time, value: b.macd_signal })));
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
        const wasHidden = mysticContainer.style.display === 'none';
        mysticContainer.style.display = _visibleIndicators.mystic ? 'block' : 'none';
        if (mysticLabel) mysticLabel.style.display = _visibleIndicators.mystic ? 'flex' : 'none';
        if (wasHidden && _visibleIndicators.mystic && oscChartMystic) {
            oscChartMystic.applyOptions({ width: mysticContainer.clientWidth, height: mysticContainer.clientHeight });
        }
    }
    if (_visibleIndicators.mystic && oscChartMystic) {
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
                col = b.mystic_bull >= 5 ? 'rgba(16, 185, 129, 0.9)' : 'rgba(16, 185, 129, 0.4)';
            } else if (b.mystic_bear > 0) {
                val = -b.mystic_bear;
                col = b.mystic_bear >= 5 ? 'rgba(239, 68, 68, 0.9)' : 'rgba(239, 68, 68, 0.4)';
            }
            return { time: b.time, value: val, color: col };
        });
        _oscSeries['mystic_hist'].setData(histData);
        try { oscChartMystic.timeScale().fitContent(); } catch (e) { }
    } else if (oscChartMystic) {
        _removeSeries(_oscSeries, 'mystic_hist', oscChartMystic);
    }
}

// Called by toolbar buttons
function toggleChartIndicator(key) {
    _visibleIndicators[key] = !_visibleIndicators[key];
    const btn = document.getElementById(`ind_btn_${key}`);
    if (btn) {
        btn.classList.toggle('ind-btn-active', _visibleIndicators[key]);
        btn.classList.toggle('ind-btn-inactive', !_visibleIndicators[key]);
    }
    // Re-draw immediately from cache — no server call needed
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
        priceEl.textContent = price.toFixed(price > 10 ? 2 : 4);
        priceEl.className = `text-xl font-black ${change >= 0 ? 'text-emerald-600' : 'text-red-500'}`;
    }
    if (changeEl) {
        const sign = change >= 0 ? '+' : '';
        changeEl.textContent = `${sign}${change.toFixed(price > 10 ? 2 : 4)} (${sign}${changePct.toFixed(2)}%)`;
        changeEl.className = `text-xs font-bold ${change >= 0 ? 'text-emerald-500' : 'text-red-400'}`;
    }
}

// ──────────────────────────────────────────────
// 1c. Timeframe button bar
// ──────────────────────────────────────────────
function setChartTimeframe(tf) {
    currentBackendTf = CHART_INTERVALS[tf]?.backend || tf;
    updateChartControlState(selectedTicker);
    // Reload chart + signals
    fetchDashboard('heavy');
}

function setChartRange(range) {
    if (!CHART_RANGES[range]) return;
    currentChartRange = range;
    localStorage.setItem('lastChartRange', range);
    const preferredTf = CHART_RANGES[range].preferredTf;
    if (preferredTf && CHART_INTERVALS[preferredTf]) currentBackendTf = preferredTf;
    updateChartControlState(selectedTicker);
    if (window._lastPriceHistory) {
        updateChart(window._lastPriceHistory, window._lastTicker || selectedTicker);
    }
    fetchDashboard('heavy');
}

function setChartSession(session) {
    if (session === 'crypto24' || isCryptoTicker(selectedTicker)) {
        currentChartSession = 'crypto24';
    } else {
        currentChartSession = session === 'extended' ? 'extended' : 'regular';
    }
    localStorage.setItem('lastChartSession', currentChartSession);
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
    fetchDashboard('heavy');
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
    if (!rememberSelectedTicker(ticker)) return;
    syncChart();
    // Chart loads first (fast mode), signals follow
    fetchDashboard('fast');
    fetchDashboard('heavy');
}

// Close search dropdown on outside click
document.addEventListener('click', e => {
    if (!e.target.closest('#tickerSearchResults') && !e.target.closest('#tickerSearch')) {
        document.getElementById('tickerSearchResults')?.classList.add('hidden');
    }
});

// Indicator Config Mapping

const INDICATOR_TOOLTIPS = {
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
    "Strategy Confidence": "Strategy Confidence combines multiple indicator signals into a single probability score. Purpose: Measures the overall conviction of a bullish or bearish trend."
};

const INDICATOR_CONFIG_MAP = {
    'RSI': ['RSI_PERIOD', 'RSI_OVERBOUGHT', 'RSI_OVERSOLD'],
    'MACD': ['MACD_FAST', 'MACD_SLOW', 'MACD_SIGNAL'],
    'EMA Cross': ['EMA_FAST', 'EMA_SLOW'],
    'Supertrend': ['SUPERTREND_PERIOD', 'SUPERTREND_MULTIPLIER'],
    'Bollinger': ['BOLL_PERIOD', 'BOLL_STD_DEV'],
    'Mystic Pulse': ['MYSTIC_PULSE_THRESHOLD'],
    'ADX Trend': ['ADX_PERIOD', 'ADX_TRENDING_THRESHOLD'],
    'SMA': ['SMA_PERIOD'],
    'ATR Volatility': ['ATR_PERIOD', 'ATR_STOP_MULTIPLIER', 'ATR_TRAIL_MULTIPLIER', 'ATR_TAKE_PROFIT_MULTIPLIER'],
    'Strategy Confidence': ['MIN_BULLISH_SIGNALS', 'MIN_BEARISH_SIGNALS'],
    'Sentiment AI': ['SENTIMENT_BULLISH_THRESHOLD', 'SENTIMENT_BEARISH_THRESHOLD']
};

// Industry Defaults for indicators based on config.py
const INDICATOR_DEFAULTS = {
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
    'ATR_PERIOD': 14,
    'ATR_STOP_MULTIPLIER': 2.0,
    'ATR_TRAIL_MULTIPLIER': 3.0,
    'ATR_TAKE_PROFIT_MULTIPLIER': 4.0,
    'MIN_BULLISH_SIGNALS': 4,
    'MIN_BEARISH_SIGNALS': 4,
    'SENTIMENT_BULLISH_THRESHOLD': 0.5,
    'SENTIMENT_BEARISH_THRESHOLD': -0.5
};

let currentEditingIndicator = null;

// ──────────────────────────────────────────────
// 3. Render Signal Confluence Grid
// ──────────────────────────────────────────────
function renderSignals(signals, action, reason, bullishCount, bearishCount) {
    // If the user is currently rapid-fire toggling indicators, ignore incoming dashboard data
    // to prevent reverting their optimistic UI changes. The final debounce will fetch the real state.
    if (window._indicatorToggleDebounce) return;

    const grid = document.getElementById('signalGrid');
    const loading = document.getElementById('gridLoading');

    if (!signals || Object.keys(signals).length === 0) {
        if (loading) loading.classList.remove('hidden');
        grid.innerHTML = '';
        return;
    }

    if (loading) loading.classList.add('hidden');

    const activeIds = new Set();

    for (const [name, data] of Object.entries(signals)) {
        const isEnabled = data.enabled !== false;
        let signalClass = data.signal === 'BULLISH' ? 'bullish' : data.signal === 'BEARISH' ? 'bearish' : 'neutral';
        if (!isEnabled) {
            signalClass = 'disabled-signal';
        }

        const icon = data.signal === 'BULLISH' ? '▲' : data.signal === 'BEARISH' ? '▼' : '●';
        const iconColor = data.signal === 'BULLISH' ? 'text-emerald-600' : data.signal === 'BEARISH' ? 'text-red-500' : 'text-purple-400';

        const cardId = 'signal-card-' + name.replace(/\s+/g, '-');
        activeIds.add(cardId);
        
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
            card.onclick = function() {
                const isNowEnabled = !this.classList.contains('disabled-signal');
                const targetState = !isNowEnabled;
                
                // Optimistic UI toggle for the card
                if (isNowEnabled) {
                    this.classList.add('disabled-signal');
                    this.classList.remove('bullish', 'bearish', 'neutral');
                    const spans = this.querySelectorAll('span');
                    spans.forEach(s => s.classList.add('opacity-50'));
                    this.querySelector('p').classList.add('opacity-50');
                } else {
                    this.classList.remove('disabled-signal');
                    this.classList.add(data.signal === 'BULLISH' ? 'bullish' : data.signal === 'BEARISH' ? 'bearish' : 'neutral');
                    const spans = this.querySelectorAll('span');
                    spans.forEach(s => s.classList.remove('opacity-50', 'grayscale'));
                    this.querySelector('p').classList.remove('opacity-50');
                }
                
                const chartKey = SIGNAL_TO_CHART_KEY[name];
                if (chartKey) {
                    _visibleIndicators[chartKey] = targetState;
                    if (window._lastPriceHistory) {
                        updateChart(window._lastPriceHistory, window._lastTicker || selectedTicker);
                    }
                }
                
                // Update internal dataset to prevent the next heartbeat from reverting it visually 
                // until the backend confirms the new state.
                this.dataset.cacheKey = `${targetState}-${data.signal}-${data.reason}`;
                
                toggleIndicator(data.toggle_key, targetState);
            };
            card.title = isEnabled ? "Click to disable this indicator" : "Click to enable this indicator";
        }

        const cacheKey = `${isEnabled}-${data.signal}-${data.reason}`;
        if (card.dataset.cacheKey !== cacheKey) {
            card.innerHTML = `
                <div class="flex items-center justify-between mb-1">
                    <span class="font-bold text-xs text-indigo-900 ${!isEnabled ? 'opacity-50' : ''} transition-opacity duration-300">${name}</span>
                    <div class="flex items-center gap-2">
                        <button class="opacity-70 hover:opacity-100 transition-opacity p-0.5 rounded hover:bg-indigo-50" 
                                onclick="event.stopPropagation(); openIndicatorSettings('${name}')"
                                title="Indicator Settings">
                            <svg xmlns="http://www.w3.org/2000/svg" class="h-3 w-3 text-indigo-500" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                                <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2.5" d="M10.325 4.317c.426-1.756 2.924-1.756 3.35 0a1.724 1.724 0 002.573 1.066c1.543-.94 3.31.826 2.37 2.37a1.724 1.724 0 001.065 2.572c1.756.426 1.756 2.924 0 3.35a1.724 1.724 0 00-1.066 2.573c.94 1.543-.826 3.31-2.37 2.37a1.724 1.724 0 00-2.572 1.065c-.426 1.756-2.924 1.756-3.35 0a1.724 1.724 0 00-2.573-1.066c-1.543.94-3.31-.826-2.37-2.37a1.724 1.724 0 00-1.065-2.572c-1.756-.426-1.756-2.924 0-3.35a1.724 1.724 0 001.066-2.573c-.94-1.543.826-3.31 2.37-2.37.996.608 2.296.07 2.572-1.065z" />
                                <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2.5" d="M15 12a3 3 0 11-6 0 3 3 0 016 0z" />
                            </svg>
                        </button>
                        <span class="${iconColor} text-sm font-bold ${!isEnabled ? 'opacity-50 grayscale' : ''} transition-all duration-300">${icon}</span>
                    </div>
                </div>
                <p class="text-[0.65rem] text-purple-600 leading-tight ${!isEnabled ? 'opacity-50' : ''} transition-opacity duration-300">${data.reason}</p>
            `;
            card.dataset.cacheKey = cacheKey;
        }
        
        if (isNew) {
            grid.appendChild(card);
        }
    }

    // Remove stale cards that no longer exist in signals
    Array.from(grid.children).forEach(child => {
        if (!activeIds.has(child.id)) {
            grid.removeChild(child);
        }
    });

    // Update counts
    document.getElementById('bullCount').textContent = `${bullishCount} Bullish`;
    document.getElementById('bearCount').textContent = `${bearishCount} Bearish`;

    // Update verdict bar
    const verdictBar = document.getElementById('verdictBar');
    const verdictLabel = document.getElementById('verdictLabel');
    const verdictReason = document.getElementById('verdictReason');

    verdictBar.className = 'mt-4 p-3 rounded-lg border ';
    if (action === 'BUY') {
        verdictBar.className += 'verdict-buy';
        verdictLabel.textContent = '🟢 BUY SIGNAL';
        verdictLabel.className = 'font-bold text-sm text-emerald-700';
    } else if (action === 'SELL') {
        verdictBar.className += 'verdict-sell';
        verdictLabel.textContent = '🔴 SELL SIGNAL';
        verdictLabel.className = 'font-bold text-sm text-red-700';
    } else {
        verdictBar.className += 'verdict-hold';
        verdictLabel.textContent = '⏸ HOLD';
        verdictLabel.className = 'font-bold text-sm text-purple-700';
    }
    verdictReason.textContent = reason || 'No signal';

    // Sync chart overlays to match which indicators are enabled here
    _syncIndicatorsFromSignals(signals);
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
};

function _syncIndicatorsFromSignals(signals) {
    if (!signals) return;
    let changed = false;
    for (const [name, data] of Object.entries(signals)) {
        const key = SIGNAL_TO_CHART_KEY[name];
        if (!key) continue;
        const shouldShow = data.enabled !== false;
        if (_visibleIndicators[key] !== shouldShow) {
            _visibleIndicators[key] = shouldShow;
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
function renderWatchlist(scans, watchlist, tradelist = [], botScans = {}) {
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
        // Prioritize botScans for active bots, otherwise use general scans
        const scan = (botScans || {})[ticker] || (scans || {})[ticker];
        const item = document.createElement('div');
        item.className = `watchlist-item group cursor-pointer ${selectedTicker === ticker ? 'active' : ''} p-3 mb-2 flex flex-col gap-2`;
        item.onclick = () => selectTicker(ticker);

        const action = scan?.action || '—';
        const price = scan?.price ? `$${parseFloat(scan.price.toString().replace('$', '')).toFixed(2)}` : '---';
        const actionColor = action === 'BUY' ? 'bg-emerald-50 text-emerald-600 border-emerald-100' : action === 'SELL' ? 'bg-red-50 text-red-600 border-red-100' : 'bg-slate-50 text-slate-400 border-slate-100';

        const bullish = scan?.bullish_count || 0;
        const total = scan?.total_signals || 0;
        const isBotActive = tradelist.includes(ticker);

        item.innerHTML = `
                <!-- Top Layer: Ticker & Bot Badge -->
                <div class="flex items-center justify-between">
                    <div class="flex items-center gap-2">
                        <span class="font-black text-sm text-indigo-950 tracking-tight">${ticker}</span>
                        ${isBotActive ? '<span class="px-1.5 py-0.5 rounded-full bg-indigo-100 text-indigo-600 text-[0.6rem] font-black tracking-widest uppercase">Active Bot</span>' : ''}
                    </div>
                    
                    <div class="flex items-center gap-1 opacity-60 group-hover:opacity-100 transition-opacity">
                        <button class="p-1.5 rounded hover:bg-rose-50 text-rose-300 hover:text-rose-400 transition-all" 
                            title="Remove from Watchlist"
                            onclick="event.stopPropagation(); removeFromWatchlist('${ticker}')">
                            <svg xmlns="http://www.w3.org/2000/svg" class="h-4 w-4" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                                <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2.5" d="M6 18L18 6M6 6l12 12" />
                            </svg>
                        </button>
                    </div>
                </div>

                <!-- Bottom Layer: Price & Signals -->
                <div class="flex items-center justify-between border-t border-slate-50 pt-2.5">
                    <span class="text-xs font-bold text-slate-500 font-mono tracking-tight">${price}</span>
                    <div class="flex items-center gap-1.5">
                        <span class="text-[0.65rem] font-black text-emerald-600 bg-emerald-50/80 px-2 py-0.5 rounded border border-emerald-100 shadow-sm">${bullish} B</span>
                        <span class="text-[0.65rem] font-black text-red-600 bg-red-50/80 px-2 py-0.5 rounded border border-red-100 shadow-sm">${scan?.bearish_count || 0} S</span>
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
            window.lastDashboardData.watchlist,
            window.lastDashboardData.tradelist,
            window.lastDashboardData.botScans
        );
    }
}

function selectTicker(ticker) {
    const normalizedTicker = normalizeTicker(ticker);
    if (!normalizedTicker) return;
    if (selectedTicker === normalizedTicker) {
        rememberSelectedTicker(normalizedTicker);
        updateActiveTickerDisplay(normalizedTicker);
        return;
    }
    rememberSelectedTicker(normalizedTicker);

    // Force reset to 1h timeframe and 1M range when loading a new stock
    const previousTf = currentBackendTf;
    currentBackendTf = "1Hour";
    currentChartRange = "1M";
    localStorage.setItem('lastChartRange', "1M");

    // Update header and search box immediately for visual feedback
    updateActiveTickerDisplay(selectedTicker);
    updateChartControlState(selectedTicker);

    // Optimization: Check if we have cached signals for this ticker in the current dashboard data
    // Only use cache if the timeframe hasn't changed, otherwise we need fresh signals for 1h
    const cachedScan = (previousTf === "1Hour") ? (window.lastDashboardData?.watchlistScans || {})[normalizedTicker] : null;
    if (cachedScan) {
        renderSignals(
            cachedScan.signals,
            cachedScan.action,
            cachedScan.reason,
            cachedScan.bullish_count,
            cachedScan.bearish_count
        );
        updateSizingPanel(cachedScan);
    } else {
        // Clear signals and show loader only if we don't have cached data
        const grid = document.getElementById('signalGrid');
        const loading = document.getElementById('gridLoading');
        if (grid) grid.innerHTML = '';
        if (loading) loading.classList.remove('hidden');
    }

    syncChart(); // Load chart in parallel
    fetchDashboard('fast');
    fetchDashboard('heavy'); // Get fresh data
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
            fetchDashboard('heavy'); // Refresh UI
        }
    } catch (e) {
        console.error('Error removing from watchlist:', e);
    }
}

// ──────────────────────────────────────────────
// 4b. Render Active Trade List (Bots)
// ──────────────────────────────────────────────
function renderTradelist(scans, tradelist, tickerAmounts = {}) {
    const container = document.getElementById('tradelistContainer');
    if (!container) return; // Prevent crash if container is removed
    container.innerHTML = '';

    if (!tradelist || tradelist.length === 0) {
        container.innerHTML = '<p class="text-center text-purple-400 text-[0.65rem] py-4">No active bots. Add from watchlist below.</p>';
        return;
    }

    tradelist.forEach(ticker => {
        const scan = (scans || {})[ticker];
        const item = document.createElement('div');
        item.className = `watchlist-item group active-bot ${selectedTicker === ticker ? 'active' : ''} p-3 mb-2 flex flex-col gap-2`;

        const action = scan?.action || '—';
        const price = scan?.price ? `$${parseFloat(scan.price.toString().replace('$', '')).toFixed(2)}` : '---';
        const actionColor = action === 'BUY' ? 'bg-emerald-50 text-emerald-600 border-emerald-100' : action === 'SELL' ? 'bg-red-50 text-red-600 border-red-100' : 'bg-slate-50 text-slate-400 border-slate-100';
        const bullish = scan?.bullish_count ?? 0;
        const bearish = scan?.bearish_count ?? 0;
        const tickerTf = (window.lastDashboardData?.ticker_settings || {})[ticker]?.timeframe || '';
        const tfLabel = tickerTf || (window.lastDashboardData?.strategyTimeframe || '5Min');

        item.innerHTML = `
                <!-- Top Layer: Ticker & Status -->
                <div class="flex items-center justify-between">
                    <div class="flex items-center gap-2 cursor-pointer" onclick="selectTicker('${ticker}')">
                        <div class="h-2 w-2 bg-emerald-500 rounded-full animate-pulse shadow-[0_0_8px_rgba(16,185,129,0.8)]"></div>
                        <span class="font-black text-sm text-indigo-950 tracking-tight">${ticker}</span>
                        <span class="text-[0.5rem] font-bold px-1.5 py-0.5 rounded bg-indigo-50 text-indigo-400 border border-indigo-100">${tfLabel}</span>
                    </div>
                    
                    <div class="flex items-center gap-1.5">
                        <span class="text-[0.6rem] font-bold text-emerald-600 bg-emerald-50 px-1 rounded border border-emerald-100">${bullish}B</span>
                        <span class="text-[0.6rem] font-bold text-red-600 bg-red-50 px-1 rounded border border-red-100">${bearish}S</span>
                    </div>

                    <div class="flex items-center gap-1 opacity-60 group-hover:opacity-100 transition-opacity">
                        <button class="p-1.5 rounded hover:bg-indigo-50 text-indigo-400 transition-all" 
                            onclick="event.stopPropagation(); openTickerModal('${ticker}')">
                            <svg xmlns="http://www.w3.org/2000/svg" class="h-3.5 w-3.5" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                                <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2.5" d="M10.325 4.317c.426-1.756 2.924-1.756 3.35 0a1.724 1.724 0 002.573 1.066c1.543-.94 3.31.826 2.37 2.37a1.724 1.724 0 001.065 2.572c1.756.426 1.756 2.924 0 3.35a1.724 1.724 0 00-1.066 2.573c.94 1.543-.826 3.31-2.37 2.37a1.724 1.724 0 00-2.572 1.065c-.426 1.756-2.924 1.756-3.35 0a1.724 1.724 0 00-2.573-1.066c-1.543.94-3.31-.826-2.37-2.37a1.724 1.724 0 00-1.065-2.572c-1.756-.426-1.756-2.924 0-3.35a1.724 1.724 0 001.066-2.573c-.94-1.543.826-3.31 2.37-2.37.996.608 2.296.07 2.572-1.065z" />
                                <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2.5" d="M15 12a3 3 0 11-6 0 3 3 0 016 0z" />
                            </svg>
                        </button>
                        <button class="p-1.5 rounded hover:bg-rose-50 text-rose-400 transition-all" 
                            onclick="event.stopPropagation(); removeFromTradelist('${ticker}')">
                            <svg xmlns="http://www.w3.org/2000/svg" class="h-3.5 w-3.5" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                                <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2.5" d="M6 18L18 6M6 6l12 12" />
                            </svg>
                        </button>
                    </div>
                </div>

                <!-- Bottom Layer: Price & Action -->
                <div class="flex items-center justify-between border-t border-slate-50 pt-2 cursor-pointer" onclick="selectTicker('${ticker}')">
                    <span class="text-xs font-black text-slate-500 font-mono tracking-tight">${price}</span>
                    <div class="px-2 py-0.5 rounded border ${actionColor} shadow-sm min-w-[50px] text-center">
                        <span class="font-black text-[0.65rem] tracking-widest">${action}</span>
                    </div>
                </div>
        `;
        container.appendChild(item);
    });
}

async function updateTickerAmount(ticker, amount) {
    try {
        const headers = await getAuthHeaders();
        await fetch(`${API_BASE}/api/settings/ticker_amount`, {
            method: 'POST',
            headers: headers,
            body: JSON.stringify({ ticker: ticker, amount: amount })
        });
        // Quietly update in background, no need to refresh entire dashboard unless needed
        console.log(`[settings] Updated amount for ${ticker}: $${amount}`);
    } catch (e) {
        console.error('Error updating ticker amount:', e);
    }
}

async function addToTradelist(ticker) {
    try {
        const headers = await getAuthHeaders();
        const timeframe = document.getElementById('strategyTimeframe')?.value || '4Hour';
        const response = await fetch(`${API_BASE}/api/tradelist`, {
            method: 'POST',
            headers: headers,
            body: JSON.stringify({ ticker: ticker, timeframe: timeframe })
        });
        if (response.ok) {
            fetchDashboard('fast');
            fetchDashboard('heavy');
        }
    } catch (e) {
        console.error('Error adding to tradelist:', e);
    }
}

async function removeFromTradelist(ticker) {
    try {
        const headers = await getAuthHeaders();
        const response = await fetch(`${API_BASE}/api/tradelist/${ticker}`, {
            method: 'DELETE',
            headers: headers
        });
        if (response.ok) {
            fetchDashboard('fast');
            fetchDashboard('heavy');
        }
    } catch (e) {
        console.error('Error removing from tradelist:', e);
    }
}

// ──────────────────────────────────────────────
// 5. Render Trade Log
// ──────────────────────────────────────────────
let latestScanHistory = [];
let latestOrderHistory = [];
let latestPendingOrders = [];
let currentScanPage = 1;
let currentTradePage = 1;
let currentPendingPage = 1;
const LOG_PAGE_SIZE = 20;

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

        const signalBadge = trade.bullish_count !== undefined
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
// 8. Ticker-Specific Settings
// ──────────────────────────────────────────────
let currentEditingTicker = null;

function openTickerModal(ticker) {
    currentEditingTicker = ticker.toUpperCase();
    const modal = document.getElementById('tickerModal');
    const nameEl = document.getElementById('modalTickerName');
    if (!modal || !nameEl) return;

    nameEl.textContent = `${currentEditingTicker} Settings`;
    modal.classList.remove('hidden');

    const settings = (window.lastDashboardData?.ticker_settings || {})[currentEditingTicker] || {};

    // 1. Set Manual Placeholders
    const amountInput = document.getElementById('modalAmount');
    const riskInput = document.getElementById('modalRisk');
    const atrInput = document.getElementById('modalAtrStop');
    const tpInput = document.getElementById('modalTpMult');

    amountInput.placeholder = "Enter Budget (e.g. 500)";
    riskInput.placeholder = "Enter Risk % (e.g. 2.5)";
    atrInput.placeholder = "Enter ATR Mult (e.g. 2.0)";
    tpInput.placeholder = "Enter TP Mult (e.g. 1.5)";

    // 2. Set Actual Values (Overrides)
    amountInput.value = settings.amount || '';
    riskInput.value = settings.risk_per_trade ? settings.risk_per_trade * 100 : '';
    atrInput.value = settings.atr_stop_multiplier || '';
    tpInput.value = settings.take_profit_multiplier || '';

    const tfSelect = document.getElementById('modalTimeframe');
    if (tfSelect) tfSelect.value = settings.timeframe || '';
}

function closeTickerModal() {
    document.getElementById('tickerModal').classList.add('hidden');
    currentEditingTicker = null;
}

async function saveTickerSettings() {
    if (!currentEditingTicker) return;

    const tfEl = document.getElementById('modalTimeframe');
    const data = {
        ticker: currentEditingTicker,
        settings: {
            amount: parseFloat(document.getElementById('modalAmount').value) || null,
            risk_per_trade: parseFloat(document.getElementById('modalRisk').value) / 100 || null,
            atr_stop_multiplier: parseFloat(document.getElementById('modalAtrStop').value) || null,
            take_profit_multiplier: parseFloat(document.getElementById('modalTpMult').value) || null,
            timeframe: tfEl ? (tfEl.value || null) : null
        }
    };

    try {
        const headers = await getAuthHeaders();
        const response = await fetch(`${API_BASE}/api/settings/ticker`, {
            method: 'POST',
            headers: headers,
            body: JSON.stringify(data)
        });
        if (response.ok) {
            console.log(`[settings] Saved settings for ${currentEditingTicker}`);
            closeTickerModal();
            fetchDashboard('fast');
            fetchDashboard('heavy'); // Refresh to show changes
        }
    } catch (e) {
        console.error('Error saving ticker settings:', e);
    }
}

async function resetTickerSettings() {
    if (!currentEditingTicker) return;
    if (!confirm(`Reset ${currentEditingTicker} to global defaults?`)) return;

    try {
        const headers = await getAuthHeaders();
        await fetch(`${API_BASE}/api/settings/ticker/${currentEditingTicker}`, {
            method: 'DELETE',
            headers: headers
        });
        closeTickerModal();
        fetchDashboard('fast');
        fetchDashboard('heavy');
    } catch (e) {
        console.error('Error resetting ticker settings:', e);
    }
}

// ──────────────────────────────────────────────
// 8. Main Fetch Loop
// ──────────────────────────────────────────────
async function fetchDashboard(mode = 'heavy') {
    try {
        const requestedTicker = normalizeTicker(selectedTicker);
        const url = requestedTicker
            ? `${API_BASE}/api/dashboard?ticker=${encodeURIComponent(requestedTicker)}&timeframe=${encodeURIComponent(currentBackendTf)}&mode=${encodeURIComponent(mode)}`
            : `${API_BASE}/api/dashboard?mode=${mode}`;

        const headers = await getAuthHeaders();
        // Removed redundant header log

        const response = await fetch(url, { headers });
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

        // Log diagnostic info from backend
        if (data.debug_logs && data.debug_logs.length > 0) {
            // Diagnostic logs silenced as requested
        }

        // Set default selected ticker
        if (!selectedTicker && data.watchlist?.length > 0) {
            selectedTicker = normalizeTicker(data.primaryTicker || data.watchlist[0]);
        }

        // Alpaca Status Pill & Action Toggle
        const statusPill = document.getElementById('alpacaLinkStatus');
        const statusDot = document.getElementById('alpacaStatusDot');
        const statusText = document.getElementById('alpacaStatusText');
        const btnConnect = document.getElementById('btnConnectAlpaca');
        const btnUnlink = document.getElementById('btnUnlinkAlpaca');

        if (statusPill && statusDot && statusText) {
            if (data.simulation) {
                isAlpacaLinked = false;
                if (data.has_keys) {
                    statusPill.className = "flex items-center justify-center h-9 text-xs font-black px-5 rounded-full bg-amber-50 text-amber-600 border-2 border-amber-100 shadow-sm whitespace-nowrap transition-all duration-500 uppercase tracking-wider";
                    statusDot.className = "h-2 w-2 rounded-full mr-2 bg-amber-500 animate-pulse";
                    statusText.textContent = "RETRYING...";
                } else {
                    statusPill.className = "flex items-center justify-center h-9 text-xs font-black px-5 rounded-full bg-rose-50 text-rose-600 border-2 border-rose-100 shadow-sm whitespace-nowrap transition-all duration-500 uppercase tracking-wider";
                    statusDot.className = "h-2 w-2 rounded-full mr-2 bg-rose-500 animate-pulse";
                    statusText.textContent = "SIMULATION";
                }
                if (btnUnlink) btnUnlink.classList.add('hidden');
            } else {
                isAlpacaLinked = true;
                statusPill.className = "flex items-center justify-center h-9 text-xs font-black px-5 rounded-full bg-emerald-50 text-emerald-600 border-2 border-emerald-100 shadow-sm whitespace-nowrap transition-all duration-500 uppercase tracking-wider";
                statusDot.className = "h-2 w-2 rounded-full mr-2 bg-emerald-500 animate-pulse";
                statusText.textContent = "Alpaca";
                if (btnUnlink) btnUnlink.classList.remove('hidden');
            }
        }

        // Connection prompt visibility
        const connPrompt = document.getElementById('connectionPrompt');
        const dashContent = document.getElementById('dashboardContent');

        const summaryEl = document.getElementById('aiSummary');
        const cardSentSummary = document.getElementById('sentimentSummary');
        const aiHeaderEl = document.getElementById('aiAnalysisHeader');

        if (aiHeaderEl) {
            aiHeaderEl.textContent = `✦ AI Sentiment Analysis — ${selectedTicker}`;
        }

        if (summaryEl) {
            if (data.sentiment_summary) {
                summaryEl.innerHTML = `
                    <div class="mb-3">
                        <span class="text-[10px] uppercase tracking-wider text-fuchsia-500 font-black block mb-1">🔥 Key Market Factor</span>
                        <p class="text-indigo-950 font-bold not-italic text-sm leading-snug">${data.sentiment_key_factor || 'Mixed Catalysts'}</p>
                    </div>
                    <div class="space-y-2">
                        <span class="text-[10px] uppercase tracking-wider text-purple-500 font-black block mb-1">📰 AI News Briefing & Catalyst Summary</span>
                        <p class="text-indigo-900 font-medium not-italic leading-relaxed text-xs">${data.sentiment_summary}</p>
                    </div>
                    <div class="mt-4 pt-3 border-t border-purple-100 flex justify-between items-center text-[10px] font-black uppercase text-purple-400">
                         <span>Analysis Confidence Index</span>
                         <span class="font-extrabold text-xs text-fuchsia-500 bg-fuchsia-50 px-2 py-0.5 rounded border border-fuchsia-100">${Math.round(data.sentiment_confidence * 100)}%</span>
                    </div>
                `;
                summaryEl.classList.remove('italic');
                if (cardSentSummary) cardSentSummary.textContent = data.sentiment_summary;
            } else {
                summaryEl.textContent = `Waiting for next ${selectedTicker} scan analysis...`;
                if (cardSentSummary) cardSentSummary.textContent = `Fetching latest news for ${selectedTicker}...`;
            }
        }

        if (data.simulation) {
            if (connPrompt) connPrompt.classList.remove('hidden');
            if (dashContent) dashContent.classList.add('hidden');
        } else {
            if (connPrompt) connPrompt.classList.add('hidden');
            if (dashContent) dashContent.classList.remove('hidden');
        }

        // Portfolio stats
        const capitalEl = document.getElementById('totalCapital');
        const openPosEl = document.getElementById('openPositions');
        const plEl = document.getElementById('dailyPL');
        const sentEl = document.getElementById('aiSentiment');

        if (capitalEl) {
            capitalEl.textContent = data.capital || '---';
            if (data.simulation) capitalEl.textContent += ' (Simulated)';
            capitalEl.classList.remove('animate-pulse');
        }
        const cashEl = document.getElementById('cashDisplay');
        if (cashEl) {
            cashEl.textContent = `Cash: ${data.cash || '---'}`;
            if (data.simulation) cashEl.textContent += ' (Simulated)';
        }

        if (openPosEl) {
            openPosEl.textContent = data.openPositions || '0';
            openPosEl.classList.remove('animate-pulse');
        }
        if (plEl) {
            plEl.textContent = data.dailyPL || '---';
            plEl.classList.remove('animate-pulse');
            if (data.dailyPL?.startsWith('+')) {
                plEl.className = 'card-value text-emerald-600';
            } else if (data.dailyPL?.startsWith('-')) {
                plEl.className = 'card-value text-red-500';
            } else {
                plEl.className = 'card-value text-indigo-900';
            }
        }

        // Fetch dynamic Market Sentiment (Fear & Greed)
        fetchFearAndGreed();
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

        // If no ticker selected yet, pick the primary one from backend
        if (!selectedTicker && data.primaryTicker) {
            selectedTicker = normalizeTicker(data.primaryTicker);
        }

        // Update chart label + star state
        if (selectedTicker) {
            updateActiveTickerDisplay(selectedTicker);
            _updateStarState();

            // Push price data to chart
            if (data.priceHistory && !isStaleTickerResponse) {
                updateChart(data.priceHistory, selectedTicker);
            }
        }

        // Get selected ticker scan
        const activeScan = isStaleTickerResponse ? null : data.watchlistScans?.[selectedTicker];



        // Signals
        if (activeScan && mode !== 'fast') {
            renderSignals(
                activeScan.signals,
                activeScan.action,
                activeScan.reason,
                activeScan.bullish_count,
                activeScan.bearish_count
            );
            updateSizingPanel(activeScan);
        } else if (data.signals && mode !== 'fast' && !isStaleTickerResponse) {
            renderSignals(data.signals, 'HOLD', 'Waiting for scan...', 0, 0);
        }

        // Cache data first so renderWatchlist can access it safely
        window.lastDashboardData = data;

        // Watchlist & Tradelist
        renderTradelist(data.watchlistScans, data.tradelist, data.tickerAmounts);
        renderWatchlist(data.watchlistScans, data.watchlist, data.tradelist, data.botScans);

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

            if (dashboardMs) dashboardMs.textContent = `${Math.round(data.performance.total_ms || 0)}ms`;
            if (evalMs) evalMs.textContent = `${Math.round(data.performance.eval_ms || 0)}ms`;
            if (cacheStatus) cacheStatus.textContent = data.performance.cached ? 'HIT' : 'MISS';
            if (perfUpdated) perfUpdated.textContent = new Date().toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit' });
        }

    } catch (error) {
        console.error('[dashboard] Fetch error:', error);
        const lastScanEl = document.getElementById('lastScanTime');
        if (lastScanEl) lastScanEl.textContent = 'Connection error';

        // On mobile, show a more intrusive alert for connectivity issues to help debug
        if (window.innerWidth < 768) {
            alert(`Dashboard Connection Failed: ${error.message}\n\nTroubleshooting:\n1. Check your internet.\n2. Disable Mobile VPN/Private Relay.\n3. Try a Hard Refresh.`);
        }

        showGlobalError('Cannot connect to the trading backend.');
    }
}

async function fetchTickerChart(ticker, signals) {
    const requestedTicker = normalizeTicker(ticker);
    if (!requestedTicker) return;
    if (!isAlpacaLinked) {
        console.log('[chart] Skip fetch: Alpaca not linked.');
        return;
    }
    try {
        const headers = await getAuthHeaders();
        const response = await fetch(`${API_BASE}/api/scan/${encodeURIComponent(requestedTicker)}?timeframe=${encodeURIComponent(currentBackendTf)}`, { headers });
        if (!response.ok) return;
        const data = await response.json();
        if (normalizeTicker(selectedTicker) !== requestedTicker) return;
        if (data.price_history) {
            updateChart(data.price_history, requestedTicker, signals || data.signals);
        }
    } catch (e) {
        console.log('[chart] Could not fetch ticker data:', e);
    }
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
                fetchDashboard('heavy'); // Trigger UI update
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
async function toggleIndicator(key, value) {
    try {
        const headers = await getAuthHeaders();
        await fetch(`${API_BASE}/api/settings/indicators`, {
            method: 'POST',
            headers: headers,
            body: JSON.stringify({ [key]: value })
        });

        clearTimeout(window._indicatorToggleDebounce);
        window._indicatorToggleDebounce = setTimeout(() => {
            fetchDashboard('fast');
            fetchDashboard('heavy'); // Refresh chart and dashboard once after clicks settle
            window._indicatorToggleDebounce = null; // Unblock the heartbeat
        }, 1000);
    } catch (e) {
        console.error('Error saving setting:', e);
    }
}

async function updateStrategyTf(newTf) {
    try {
        currentBackendTf = newTf;
        if (window.lastDashboardData) {
            window.lastDashboardData.watchlistScans = {};
            window.lastDashboardData.botScans = {};
            window.lastDashboardData.signals = {};
        }

        // Show loader immediately
        const loading = document.getElementById('gridLoading');
        if (loading) loading.classList.remove('hidden');
        const grid = document.getElementById('signalGrid');
        if (grid) grid.innerHTML = '';

        const headers = await getAuthHeaders();
        const response = await fetch(`${API_BASE}/api/settings/timeframe`, {
            method: 'POST',
            headers: headers,
            body: JSON.stringify({ timeframe: newTf })
        });

        if (response.ok) {
            console.log(`[settings] Timeframe updated to ${newTf}`);

            syncChart(); // Load chart in parallel
            fetchDashboard('fast');
            fetchDashboard('heavy'); // Refresh data
        }
    } catch (e) {
        console.error('Error updating timeframe:', e);
    }
}

// ──────────────────────────────────────────────
// 11. Init
// ──────────────────────────────────────────────
document.addEventListener('DOMContentLoaded', () => {
    console.log('[dashboard] Initializing components...');

    // Attach event listeners first (DOM is now ready)
    attachEventListeners();
    syncChart();

    // Chart will be initialized dynamically by fetchDashboard() once primary bot is known
    console.log('[dashboard] Waiting for backend data to initialize chart...');

    // Initial data fetch once auth is ready
    const checkAuth = setInterval(() => {
        if (window.auth && window.auth.currentUser) {
            console.log('[dashboard] Auth ready. Starting data stream.');
            fetchDashboard('fast');
            fetchDashboard('heavy');
            setInterval(() => {
                // Skip the "heartbeat" live refresh if the user is currently rapid-fire clicking indicators
                if (window._indicatorToggleDebounce) return; 
                fetchDashboard('heavy');
            }, REFRESH_INTERVAL);
            clearInterval(checkAuth);
        }
    }, 500);

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
    document.getElementById('btTicker').value = selectedTicker || "TSLA";
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
// 12. Alpaca Connection Logic
// ──────────────────────────────────────────────
function openAlpacaModal() {
    document.getElementById('alpacaModal').classList.remove('hidden');
    // Clear fields for security
    document.getElementById('alpacaKey').value = "";
    document.getElementById('alpacaSecret').value = "";
}

function closeAlpacaModal() {
    document.getElementById('alpacaModal').classList.add('hidden');
}

async function submitAlpacaConfig() {
    const key = document.getElementById('alpacaKey').value.trim();
    const secret = document.getElementById('alpacaSecret').value.trim();
    const isPaper = document.getElementById('alpacaPaper').checked;
    const btn = document.getElementById('alpacaSubmitBtn');

    if (!key || !secret) {
        alert("Please provide both API Key and Secret.");
        return;
    }

    const originalText = btn.innerText;
    btn.innerText = "ESTABLISHING BRIDGE...";
    btn.disabled = true;

    try {
        const headers = await getAuthHeaders();
        const response = await fetch(`${API_BASE}/api/alpaca_config`, {
            method: 'POST',
            headers: headers,
            body: JSON.stringify({
                api_key: key,
                secret_key: secret,
                paper: isPaper
            })
        });

        const data = await response.json();
        if (data.status === 'success') {
            alert("Connection Successful! Your Alpaca account is now linked.");
            closeAlpacaModal();
            // Refresh dashboard data to show new balance
            if (typeof fetchDashboardData === 'function') fetchDashboardData();
        } else {
            alert("Connection Failed: " + data.message);
        }
    } catch (err) {
        console.error(err);
        alert("Backend communication error.");
    } finally {
        btn.innerText = originalText;
        btn.disabled = false;
    }
}

async function unlinkAlpaca() {
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
            if (typeof fetchDashboard === 'function') {
                fetchDashboard('fast');
                fetchDashboard('heavy');
            }
        } else {
            alert("Error unlinking account.");
        }
    } catch (err) {
        console.error(err);
        alert("Backend communication error.");
    }
}

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

    
    const desc = INDICATOR_TOOLTIPS[indicatorName] || "Adjust parameters for this indicator.";
    const tooltipHtml = `<div class="group/tooltip relative inline-flex items-center ml-2 align-middle">
        <svg xmlns="http://www.w3.org/2000/svg" class="h-4 w-4 text-indigo-200 hover:text-white cursor-pointer transition-colors" fill="none" viewBox="0 0 24 24" stroke="currentColor">
            <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M13 16h-1v-4h-1m1-4h.01M21 12a9 9 0 11-18 0 9 9 0 0118 0z" />
        </svg>
        <div class="invisible group-hover/tooltip:visible opacity-0 group-hover/tooltip:opacity-100 transition-all absolute top-full left-1/2 -translate-x-1/2 mt-2 w-56 p-2.5 bg-white text-indigo-900 text-[0.65rem] leading-snug rounded-lg shadow-2xl z-[999] pointer-events-none text-center font-normal capitalize-none normal-case tracking-normal border border-indigo-100">
            ${desc}
            <div class="absolute bottom-full left-1/2 -translate-x-1/2 border-4 border-transparent border-b-white"></div>
        </div>
    </div>`;
    title.innerHTML = `${indicatorName} Settings ${tooltipHtml}`;

    container.innerHTML = '';

    if (configKeys.length === 0) {
        container.innerHTML = '<p class="text-xs text-slate-500 italic">No adjustable parameters for this indicator.</p>';
    }

    const currentParams = window.lastDashboardData?.indicator_parameters || {};

    configKeys.forEach(key => {
        const hasValue = currentParams[key] !== undefined && currentParams[key] !== null && currentParams[key] !== '';
        const val = hasValue ? currentParams[key] : (INDICATOR_DEFAULTS[key] !== undefined ? INDICATOR_DEFAULTS[key] : '');
        const label = key.replace(/_/g, ' ').toLowerCase();

        const div = document.createElement('div');
        div.className = 'flex flex-col gap-1.5';
        div.innerHTML = `
            <div class="flex justify-between items-center mb-0.5">
                <label class="text-[0.65rem] font-black text-indigo-950 uppercase tracking-widest opacity-60">${label}</label>
                ${!hasValue ? '<span class="text-[0.55rem] font-extrabold text-emerald-500 uppercase tracking-widest bg-emerald-50 px-1.5 py-0.5 rounded-md">System Default</span>' : ''}
            </div>
            <input type="number" step="any" data-key="${key}" value="${val}" 
                class="w-full bg-slate-50 border border-slate-200 rounded-lg px-3 py-2 text-sm font-bold text-indigo-900 focus:ring-2 focus:ring-indigo-500 focus:border-transparent outline-none transition-all">
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
    const inputs = document.querySelectorAll('#indicatorModalContent input');
    inputs.forEach(input => {
        const key = input.dataset.key;
        if (INDICATOR_DEFAULTS[key] !== undefined) {
            input.value = INDICATOR_DEFAULTS[key];
            const container = input.closest('.flex-col');
            if (container) {
                const header = container.querySelector('.flex.justify-between.items-center');
                if (header) {
                    const badge = header.querySelector('span');
                    if (!badge) {
                        header.insertAdjacentHTML('beforeend', '<span class="text-[0.55rem] font-extrabold text-emerald-500 uppercase tracking-widest bg-emerald-50 px-1.5 py-0.5 rounded-md">System Default</span>');
                    }
                }
            }
        }
    });
}

async function saveIndicatorSettings() {
    const inputs = document.querySelectorAll('#indicatorModalContent input');
    const updates = {};
    inputs.forEach(input => {
        updates[input.dataset.key] = input.value;
    });

    if (Object.keys(updates).length === 0) {
        closeIndicatorSettings();
        return;
    }

    try {
        const headers = await getAuthHeaders();
        const response = await fetch(`${API_BASE}/api/settings/indicators`, {
            method: 'POST',
            headers: headers,
            body: JSON.stringify(updates)
        });

        if (response.ok) {
            console.log(`[settings] Updated settings for ${currentEditingIndicator}`);
            closeIndicatorSettings();
            // Fetch dashboard to see effects (calculating with new params)
            fetchDashboard('fast');
            fetchDashboard('heavy');
        } else {
            alert("Error saving indicator settings.");
        }
    } catch (err) {
        console.error(err);
        alert("Backend communication error.");
    }
}

// ──────────────────────────────────────────────
// Fear & Greed Integration
// ──────────────────────────────────────────────
let lastFngScore = null;
let lastFngComponents = [];
let lastFngFetch = 0;

async function fetchFearAndGreed() {
    const now = Date.now();
    // Cache for 5 minutes
    if (lastFngScore !== null && (now - lastFngFetch < 300000)) {
        updateFearAndGreedUI(lastFngScore);
        return;
    }
    try {
        const res = await fetch("https://feargreedchart.com/api/");
        const data = await res.json();
        if (data && data.score && data.score.score !== undefined) {
            lastFngScore = data.score.score;
            lastFngComponents = data.score.components || [];
            lastFngFetch = now;
            updateFearAndGreedUI(lastFngScore);
        }
    } catch (e) {
        console.error("Failed to fetch Fear and Greed:", e);
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
                <div class="bg-white border border-slate-200 rounded-2xl p-5 flex flex-col justify-between shadow-sm hover:shadow-md transition-shadow">
                    <div>
                        <div class="flex items-center justify-between mb-3">
                            <h4 class="font-black text-slate-800 text-[13px] tracking-wide">${comp.name}</h4>
                            <span class="${color} text-white text-[10px] px-2.5 py-1 rounded-md font-bold uppercase tracking-widest shadow-sm">${label}</span>
                        </div>
                        <p class="text-xs text-slate-500 mb-2 leading-relaxed">${comp.desc}</p>
                        <p class="text-[10px] text-slate-400 font-mono bg-slate-50 px-2 py-1 rounded border border-slate-100 inline-block truncate max-w-full">${comp.raw}</p>
                    </div>
                    <div class="mt-5">
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
