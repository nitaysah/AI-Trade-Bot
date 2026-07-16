
function getIndicatorOverrides() {
    const overrides = {};
    if (window.lastBacktestData && window.lastBacktestData.indicator_overrides) {
        Object.assign(overrides, window.lastBacktestData.indicator_overrides);
    }
    if (window.btIndicatorOverrides) {
        Object.assign(overrides, window.btIndicatorOverrides);
    }
    return overrides;
}

const BT_AI_INDICATOR_CACHE_TTL = 30 * 60 * 1000;

function normalizeBtAIIndicatorTicker(ticker) {
    return (ticker || '').toString().trim().toUpperCase();
}

function getBtAIIndicatorCacheKey(ticker, timeframe, model) {
    return `${normalizeBtAIIndicatorTicker(ticker)}|${timeframe}|${model}`;
}

function isBtAICacheFresh(cached) {
    return cached && cached.timestamp && (Date.now() - cached.timestamp) < BT_AI_INDICATOR_CACHE_TTL;
}

function getBtAICache(key) {
    try {
        const cachedStr = localStorage.getItem(`ai_cache_${key}`);
        if (!cachedStr) return null;
        const cached = JSON.parse(cachedStr);
        if (isBtAICacheFresh(cached)) return cached;
        localStorage.removeItem(`ai_cache_${key}`);
    } catch (e) {
        console.warn('[backtest] Could not read AI indicator cache:', e);
    }
    return null;
}

function normalizeBtCachedAISentimentScore(rawScore) {
    const value = Number(rawScore);
    if (!Number.isFinite(value)) return null;
    if (value >= -1 && value <= 1) return value;
    if (value >= 0 && value <= 100) return (value / 100) * 2 - 1;
    return Math.max(-1, Math.min(1, value));
}

function getBtCachedAIAnalysisForTicker(ticker, timeframe) {
    const normalizedTicker = normalizeBtAIIndicatorTicker(ticker);
    if (!normalizedTicker) return null;
    const selectedModel = localStorage.getItem('selected_ai_model') || 'llama-3.3-70b-versatile';
    const exact = getBtAICache(getBtAIIndicatorCacheKey(normalizedTicker, timeframe, selectedModel));
    if (exact?.data?.sentiment_score != null) return exact;

    try {
        const globalAi = JSON.parse(localStorage.getItem('latest_global_ai_analysis') || 'null');
        if (
            globalAi?.data?.sentiment_score != null &&
            isBtAICacheFresh(globalAi) &&
            normalizeBtAIIndicatorTicker(globalAi.ticker || globalAi.data?.ticker) === normalizedTicker
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
            if (cached?.data?.sentiment_score != null && isBtAICacheFresh(cached)) return cached;
        }
    } catch (e) {}
    return null;
}

function getBtCachedNewsAISentiment(mode = 'regular') {
    const ticker = document.getElementById('btTicker')?.value || '';
    const timeframe = document.getElementById(`strategyTimeframe_${mode}`)?.value
        || document.getElementById('strategyTimeframe_regular')?.value
        || '5Min';
    const cached = getBtCachedAIAnalysisForTicker(ticker, timeframe);
    const data = cached?.data;
    if (!data) return null;
    const score = normalizeBtCachedAISentimentScore(data.sentiment_score);
    if (score === null) return null;
    return {
        score,
        pct: Math.round(((score + 1.0) / 2.0) * 100),
        reason: data.sentiment_key_factor || data.key_catalyst || 'Cached news sentiment',
    };
}

function updateBacktestBotBulls4CachedBadge(mode = 'regular') {
    const containerId = mode === 'options' ? 'indicatorChecksContainer_bt_options' : 'indicatorChecksContainer_bt_regular';
    const container = document.getElementById(containerId);
    if (!container) return;
    const checkbox = container.querySelector('input[value="Botbulls4 News AI"]');
    if (!checkbox) return;
    const card = checkbox.closest('.indicator-card');
    const textColumn = card?.querySelector('.flex.flex-col');
    if (!card || !textColumn) return;

    let badge = card.querySelector('.bt-news-ai-cache-badge');
    if (checkbox.disabled) {
        if (!badge) {
            badge = document.createElement('span');
            textColumn.appendChild(badge);
        }
        badge.className = 'bt-news-ai-cache-badge mt-1 text-[0.58rem] font-black uppercase tracking-widest rounded-full px-2 py-0.5 inline-flex w-fit border border-slate-200 bg-slate-50 text-slate-500';
        badge.textContent = 'Live news only - disabled for backtest';
        badge.title = 'BotBulls4 uses current live news sentiment, so it is not replayed in historical backtests.';
        return;
    }

    if (!checkbox.checked) {
        if (badge) badge.remove();
        return;
    }

    const sentiment = getBtCachedNewsAISentiment(mode);
    if (!badge) {
        badge = document.createElement('span');
        badge.className = 'bt-news-ai-cache-badge mt-1 text-[0.58rem] font-black uppercase tracking-widest rounded-full px-2 py-0.5 inline-flex w-fit border';
        textColumn.appendChild(badge);
    }

    if (!sentiment) {
        badge.className = 'bt-news-ai-cache-badge mt-1 text-[0.58rem] font-black uppercase tracking-widest rounded-full px-2 py-0.5 inline-flex w-fit border border-slate-200 bg-slate-50 text-slate-500';
        badge.textContent = 'News AI: waiting for cache';
        return;
    }

    const overrides = getIndicatorOverrides();
    const bullishThresh = overrides.SENTIMENT_BULLISH_PERCENTAGE ?? window.BT_INDICATOR_DEFAULTS?.SENTIMENT_BULLISH_PERCENTAGE ?? 70;
    const bearishThresh = overrides.SENTIMENT_BEARISH_PERCENTAGE ?? window.BT_INDICATOR_DEFAULTS?.SENTIMENT_BEARISH_PERCENTAGE ?? 30;
    const tone = sentiment.pct >= bullishThresh
        ? 'border-emerald-200 bg-emerald-50 text-emerald-700'
        : sentiment.pct <= bearishThresh
            ? 'border-rose-200 bg-rose-50 text-rose-700'
            : 'border-amber-200 bg-amber-50 text-amber-700';
    badge.className = `bt-news-ai-cache-badge mt-1 text-[0.58rem] font-black uppercase tracking-widest rounded-full px-2 py-0.5 inline-flex w-fit border ${tone}`;
    badge.textContent = `News AI cached: ${sentiment.pct}%`;
    badge.title = sentiment.reason || '';
}

function updateBacktestBotBulls4CachedBadges(mode) {
    if (mode) {
        updateBacktestBotBulls4CachedBadge(mode);
        return;
    }
    updateBacktestBotBulls4CachedBadge('regular');
    updateBacktestBotBulls4CachedBadge('options');
}

function disableBacktestBotBulls4Indicator() {
    ['regular', 'options'].forEach(mode => {
        const containerId = mode === 'options' ? 'indicatorChecksContainer_bt_options' : 'indicatorChecksContainer_bt_regular';
        const container = document.getElementById(containerId);
        if (!container) return;
        const checkbox = container.querySelector('input[value="Botbulls4 News AI"]');
        if (!checkbox) return;
        checkbox.checked = false;
        checkbox.disabled = true;
        checkbox.setAttribute('aria-disabled', 'true');
        checkbox.title = 'BotBulls4 News AI is live-news only and disabled for backtesting.';
        const card = checkbox.closest('.indicator-card');
        if (card) {
            card.classList.add('opacity-60', 'cursor-not-allowed', 'grayscale');
            card.title = 'BotBulls4 News AI is live-news only and disabled for backtesting.';
        }
        const settingsButton = card?.querySelector('button[title="Botbulls4 News AI Settings"], button[title="BotBulls4 News AI Settings"]');
        if (settingsButton) {
            settingsButton.disabled = true;
            settingsButton.classList.add('cursor-not-allowed', 'opacity-40');
            settingsButton.title = 'Disabled for backtesting because BotBulls4 depends on live news.';
        }
        updateBacktestBotBulls4CachedBadge(mode);
    });
}


function isCrypto(ticker) {
    if (!ticker) return false;
    const t = ticker.toUpperCase();
    return t.includes('USD') || t.includes('BTC') || t.includes('ETH') || t.includes('SOL') || t.includes('DOGE') || t.includes('LTC');
}

function formatAssetPrice(val, isCrypto) {
    if (val === undefined || val === null || isNaN(val)) return '---';
    const v = parseFloat(val);
    if (v === 0) return '0.00';
    if (isCrypto) {
        return new Intl.NumberFormat('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 8, useGrouping: false }).format(v);
    }
    if (v < 0.0001) return v.toFixed(8);
    if (v < 0.01) return v.toFixed(6);
    if (v < 1.0) return v.toFixed(4);
    return v.toFixed(2);
}

var BT_REGULAR_HELPER_TEXTS = {
    entry: {
        indicator: 'Indicator entry buys when bullish signals reach the buy threshold.',
        custom_price: 'Custom Price entry buys when the underlying trades at or below your target price.',
        atr: 'ATR entry uses volatility structure, such as a pullback reclaim or breakout expansion, without needing indicator confirmation.',
        hybrid_atr: 'Hybrid (Indicator + ATR) entry is strict: bullish indicators AND bullish ATR structure must both confirm before a long trade.',
        percentage: 'Percentage entry buys after the configured dip or breakout percentage move is confirmed.',
        hybrid_pct: 'Hybrid (Indicator + Percentage) entry is strict: bullish indicators AND the percentage structure must both confirm.',
        pivot_points: 'Pivot entry buys on bullish support reclaim or pivot breakout structure.',
        hybrid_pivot: 'Hybrid (Indicator + Pivots) entry is strict: bullish indicators AND pivot structure must both confirm.',
        high_low: 'High/Low entry buys on a prior high breakout, matching the live regular bot behavior.',
        hybrid_high_low: 'Hybrid (Indicator + High/Low) entry is strict: bullish indicators AND prior high breakout must both confirm.',
        swing_points: 'Swing entry buys around support/reclaim or breakout zones using the selected swing lookback.',
        hybrid_swing: 'Hybrid (Indicator + Swing) entry is strict: bullish indicators AND swing structure must both confirm.'
    },
    exit: {
        indicator: 'Indicator exit sells when bearish signals reach the sell threshold.',
        sltp: 'ATR exit uses volatility stop-loss, take-profit, and optional trailing stop levels.',
        hybrid: 'Hybrid (Indicator + ATR) exit is protective OR: bearish reversal OR ATR risk level can close the trade.',
        percentage: 'Percentage exit sells when fixed stop-loss, take-profit, or trailing stop levels are hit.',
        hybrid_pct: 'Hybrid (Indicator + Percentage) exit is protective OR: bearish reversal OR percentage risk level can close the trade.',
        custom_sltp: 'Custom Price SL/TP exits when the bar low reaches your stop or the bar high reaches your take-profit.',
        pivot_points: 'Pivot exit uses support as stop loss and resistance as take profit for regular long trades.',
        hybrid_pivot: 'Hybrid (Indicator + Pivots) exit is protective OR: bearish reversal OR pivot support/resistance level can close.',
        high_low: 'High/Low exit reacts to prior low breakdown or prior high breakout.',
        hybrid_high_low: 'Hybrid (Indicator + High/Low) exit is protective OR: bearish reversal OR high/low level can close.',
        swing_points: 'Swing exit uses nearby swing support as stop loss and resistance as take profit.',
        hybrid_swing: 'Hybrid (Indicator + Swing) exit is protective OR: bearish reversal OR swing support/resistance level can close.'
    },
    entry_options: {
        indicator: 'Indicator entry opens CALLs on bullish confluence or PUTs on bearish confluence, based on Trade Direction.',
        custom_price: 'Custom Price entry is direction-aware: CALL opens when the underlying drops to target; PUT opens when the underlying rallies to or above target.',
        atr: 'ATR entry uses underlying volatility structure. Bullish ATR confirmation opens CALL; bearish ATR confirmation opens PUT.',
        hybrid_atr: 'Hybrid (Indicator + ATR) entry is strict: BOTH conditions must be true. CALL needs bullish indicators + bullish ATR; PUT needs bearish indicators + bearish ATR.',
        percentage: 'Percentage entry uses the underlying move. Bullish confirmation opens CALL; bearish confirmation opens PUT.',
        hybrid_pct: 'Hybrid (Indicator + Pct) entry is strict: BOTH conditions must be true. CALL needs bullish indicators + percentage setup; PUT needs bearish indicators + percentage setup.',
        pivot_points: 'Pivot Points entry is direction-aware: CALL uses support/reclaim or bullish pivot break; PUT uses resistance rejection or bearish pivot break.',
        hybrid_pivot: 'Hybrid (Indicator + Pivots) entry is strict: BOTH conditions must be true. CALL needs bullish indicators + bullish pivot; PUT needs bearish indicators + bearish pivot.',
        high_low: 'Prior Day High/Low entry is direction-aware: CALL opens on prior-high breakout; PUT opens on prior-low breakdown.',
        hybrid_high_low: 'Hybrid (Indicator + High/Low) entry is strict: BOTH conditions must be true. CALL needs bullish indicators + prior-high breakout; PUT needs bearish indicators + prior-low breakdown.',
        swing_points: 'Swing Points entry is direction-aware: CALL uses bullish swing support/breakout; PUT uses bearish swing resistance/breakdown.',
        hybrid_swing: 'Hybrid (Indicator + Swing) entry is strict: BOTH conditions must be true. CALL needs bullish indicators + bullish swing structure; PUT needs bearish indicators + bearish swing structure.'
    },
    exit_options: {
        indicator: 'Indicator exit sells-to-close on opposite confluence: bearish threshold for CALLs; bullish threshold for PUTs.',
        custom_sltp: 'Custom SL/TP watches option premium directly. Same rule for CALL and PUT: close when contract premium hits your stop or target.',
        custom_underlying_sltp: 'Custom Underlying SL/TP watches stock price. CALL stop is below and target above; PUT stop is above and target below.',
        sltp: 'ATR exit uses underlying ATR levels. CALL stop/trail below and target above; PUT stop/trail above and target below.',
        hybrid: 'Hybrid (Indicator + ATR) options exit is protective OR: opposite indicator reversal OR ATR stop/take-profit can sell-to-close. Options decay fast, so exits prioritize risk control.',
        percentage: 'Percentage exit watches option premium gain/loss/trailing percent. Same premium-based logic for CALL and PUT.',
        hybrid_pct: 'Hybrid (Indicator + Pct) options exit is protective OR: opposite indicator reversal OR premium % stop/target/trail can sell-to-close.',
        pivot_points: 'Pivot exit is direction-aware. CALL takes profit at resistance and stops at support; PUT takes profit at support and stops at resistance.',
        hybrid_pivot: 'Hybrid (Indicator + Pivots) options exit is protective OR: opposite signal OR pivot target/stop can sell-to-close.',
        high_low: 'Prior Day High/Low exit is direction-aware. CALL target is prior high and stop is prior low; PUT target is prior low and stop is prior high.',
        hybrid_high_low: 'Hybrid (Indicator + High/Low) options exit is protective OR: opposite signal OR prior high/low target/stop can sell-to-close.',
        swing_points: 'Swing exit is direction-aware. CALL targets swing resistance and stops at swing support; PUT targets swing support and stops at swing resistance.',
        hybrid_swing: 'Hybrid (Indicator + Swing) options exit is protective OR: opposite signal OR swing support/resistance target/stop can sell-to-close.'
    }
};

function getBtRegularMarketSessionPayload() {
    const ticker = document.getElementById('btTicker')?.value || '';
    let session = document.getElementById('strategyMarketSession_regular')?.value || 'Regular';
    if (isCrypto(ticker) && session === 'Regular') {
        session = '24h';
    }
    return {
        market_session: session,
        ext_hours: session !== 'Regular'
    };
}

function getBtSwingLookbackDays(kind) {
    const prefix = kind === 'exit' ? 'strategySwingExitLookback' : 'strategySwingEntryLookback';
    const value = document.getElementById(`${prefix}_regular`)?.value || '20';
    if (value === 'custom') {
        const custom = parseInt(document.getElementById(`${prefix}Custom_regular`)?.value || '20', 10);
        return Number.isFinite(custom) && custom > 0 ? custom : 20;
    }
    const parsed = parseInt(value, 10);
    return Number.isFinite(parsed) && parsed > 0 ? parsed : 20;
}

function getBtOptionsSwingLookbackDays(kind) {
    const prefix = kind === 'exit' ? 'strategySwingExitLookback' : 'strategySwingEntryLookback';
    const value = document.getElementById(`${prefix}_options`)?.value || '20D';
    if (value === 'custom') {
        const custom = parseInt(document.getElementById(`${prefix}Custom_options`)?.value || '20', 10);
        return Number.isFinite(custom) && custom > 0 ? custom : 20;
    }
    const parsed = parseInt(value, 10);
    return Number.isFinite(parsed) && parsed > 0 ? parsed : 20;
}

function getBtRegularStrategyPayload() {
    const market = getBtRegularMarketSessionPayload();
    const smartType = document.getElementById('strategyRegularSmartReentryType')?.value || 'RSI';
    return {
        allocation_mode: document.getElementById('strategyAllocationMode_regular')?.value || 'fixed',
        trade_amount: parseFloat(document.getElementById('strategyTradeAmount_regular')?.value || '1000'),
        market_session: market.market_session,
        ext_hours: market.ext_hours,
        entry_atr_multiplier: parseFloat(document.getElementById('strategyEntryAtrMult_regular')?.value || '1.5'),
        entry_atr_style: document.getElementById('strategyEntryAtrStyle_regular')?.value || 'pullback_reclaim',
        entry_pct_move: parseFloat(document.getElementById('strategyEntryPctMove_regular')?.value || '2'),
        entry_pct_style: document.getElementById('strategyEntryPctStyle_regular')?.value || 'dip_reclaim',
        swing_entry_mode: document.getElementById('strategySwingEntryMode_regular')?.value || 'bounce_retest',
        swing_entry_lookback_days: getBtSwingLookbackDays('entry'),
        swing_entry_lookback_mode: document.getElementById('strategySwingEntryLookback_regular')?.value || '20',
        swing_exit_lookback_days: getBtSwingLookbackDays('exit'),
        swing_exit_lookback_mode: document.getElementById('strategySwingExitLookback_regular')?.value || '20',
        custom_stop_loss_price: parseFloat(document.getElementById('strategyCustomStopLossPrice_regular')?.value || '0'),
        custom_take_profit_price: parseFloat(document.getElementById('strategyCustomTakeProfitPrice_regular')?.value || '0'),
        enable_signal_reset: document.getElementById('strategyRegularSignalReset')?.checked || false,
        enable_smart_reentry: document.getElementById('strategyRegularSmartReentry')?.checked || false,
        smart_reentry_type: smartType,
        smart_reentry_rsi_call_max: parseFloat(document.getElementById('strategyRegularRsiCallMax')?.value || '70'),
        smart_reentry_rsi_put_min: parseFloat(document.getElementById('strategyRegularRsiPutMin')?.value || '30'),
        smart_reentry_vwap_call_max_dist_pct: parseFloat(document.getElementById('strategyRegularVwapCallMaxPct')?.value || '0.5'),
        smart_reentry_vwap_put_max_dist_pct: parseFloat(document.getElementById('strategyRegularVwapPutMaxPct')?.value || '0.5')
    };
}
// ──────────────────────────────────────────────
// BACKTEST CENTER LOGIC v2.0
// ──────────────────────────────────────────────

window.BACKTEST_CLOUD_URL = window.BACKTEST_CLOUD_URL || 'https://207-211-184-22.sslip.io';
window.BACKTEST_API_BASE = window.location.hostname === 'localhost' || window.location.hostname === '127.0.0.1'
    ? 'http://localhost:8000' 
    : window.BACKTEST_CLOUD_URL;
var allBtTrades = [];
var btPage = 1;
var btPageSize = 10;
window.lastBacktestData = null;
window.lastBacktestRun = null;

// ── Backtest Chart State ─────────────────────────────────────────────────
var btChart = null;            // LightweightCharts chart instance
var btCandleSeries = null;     // Candlestick series
var btEqSeries = null;         // Equity curve line series
var btVolumeSeries = null;     // Volume histogram series
var btDailyHighLineSeries = null; // Prior Day High/Low prior high line
var btDailyLowLineSeries = null;  // Prior Day High/Low prior low line
var btDailyHighTriggerLineSeries = null;
var btDailyLowTriggerLineSeries = null;
var btPivotPLineSeries = null;
var btPivotR1LineSeries = null;
var btPivotS1LineSeries = null;
var btIndicatorOverlaySeries = [];
var btPriceLineLabels = {};
var btPriceLineLabelMeta = {};
var btChartMarkersRef = [];    // Saved markers array for toggling
var btChartTradeIdx = 0;       // Currently highlighted trade (0-based, newest-first order)
var btChartTradesRef = [];     // Reference to trades array (newest-first, same as allBtTrades)
var btChartCandlesRef = [];    // Reference to candles array
var btChartEquityCurveRef = [];
var btChartResizeObserver = null; // ResizeObserver for responsive chart
// ────────────────────────────────────────────────────────────────────────

// ── Backtest Cancellation on Unload ──────────────────────────────────────
window.addEventListener('beforeunload', () => {
    // Attempt to cancel any running backtest on the backend when navigating away or refreshing
    navigator.sendBeacon(`${window.BACKTEST_API_BASE}/api/backtest/cancel`);
});
// ────────────────────────────────────────────────────────────────────────

/**
 * Retrieves the current Firebase ID token and prepares headers.
 */
async function getAuthHeaders() {
    const auth = window.auth;
    const baseHeaders = { 
        'Content-Type': 'application/json',
        'Bypass-Tunnel-Reminder': 'true'
    };
    
    if (!auth) return baseHeaders;
    const user = auth.currentUser;
    if (!user) {
        if (localStorage.getItem('dev_mode') === 'true') {
            return {
                ...baseHeaders,
                'Authorization': `Bearer dev-token`
            };
        }
    }
    const token = await user.getIdToken();
    return {
        ...baseHeaders,
        'Authorization': `Bearer ${token}`
    };
}

async function loadBacktestOllamaModels() {
    const select = document.getElementById('btAiModel');
    if (!select) return;
    try {
        const headers = await getAuthHeaders();
        const res = await fetch(`${window.BACKTEST_API_BASE}/api/ollama/models`, { headers });
        if (res.ok) {
            const data = await res.json();
            if (data.status === 'online' && data.models && data.models.length > 0) {
                // Create an optgroup for Ollama models
                const optGroup = document.createElement('optgroup');
                optGroup.label = "Ollama (Local)";
                
                data.models.forEach(m => {
                    const opt = document.createElement('option');
                    opt.value = `ollama/${m}`;
                    opt.textContent = m;
                    optGroup.appendChild(opt);
                });
                select.appendChild(optGroup);
                console.log(`[backtest] Loaded ${data.models.length} local Ollama models.`);
            } else {
                console.log('[backtest] Ollama offline or has no local models pulled.');
            }
        }
    } catch (e) {
        console.warn('[backtest] Failed to fetch Ollama models for backtester:', e);
    }
}

// 1. Initialization
function ensureDailyHighLowBacktestIndicatorCards() {
    const cards = [
        { name: 'Volume Profile', subtitle: 'POC / Value Area', settings: true },
        { name: 'Prior Day High/Low', subtitle: 'Prior Session', settings: true },
        { name: 'Pivot Points (S/R)', subtitle: 'Pivot S/R', settings: true },
        { name: 'Swing Points', subtitle: 'Swing S/R', settings: true },
    ];
    [
        { mode: 'regular', containerId: 'indicatorChecksContainer_bt_regular', checkClass: 'bt-indicator-check-regular' },
        { mode: 'options', containerId: 'indicatorChecksContainer_bt_options', checkClass: 'bt-indicator-check-options' },
    ].forEach(({ mode, containerId, checkClass }) => {
        const container = document.getElementById(containerId);
        if (!container) return;

        cards.forEach(({ name, subtitle, settings }) => {
            const existingInput = container.querySelector(`input[value="${name}"]`);
            if (existingInput) {
                return;
            }
            const escapedName = name.replace(/'/g, "\\'");
            const settingsButton = settings ? `
                <button onclick="event.stopPropagation(); event.preventDefault(); openIndicatorSettings('${escapedName}')" class="opacity-70 hover:opacity-100 transition-opacity p-0.5 rounded hover:bg-indigo-50" title="${name} Settings">
                    <svg xmlns="http://www.w3.org/2000/svg" class="h-3.5 w-3.5 text-indigo-500" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                        <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2.5" d="M10.325 4.317c.426-1.756 2.924-1.756 3.35 0a1.724 1.724 0 002.573 1.066c1.543-.94 3.31.826 2.37 2.37a1.724 1.724 0 001.065 2.572c1.756.426 1.756 2.924 0 3.35a1.724 1.724 0 00-1.066 2.573c.94 1.543-.826 3.31-2.37 2.37a1.724 1.724 0 00-2.572 1.065c-.426 1.756-2.924 1.756-3.35 0a1.724 1.724 0 00-2.573-1.066c-1.543.94-3.31-.826-2.37-2.37a1.724 1.724 0 00-1.065-2.572c-1.756-.426-1.756-2.924 0-3.35a1.724 1.724 0 001.066-2.573c-.94-1.543.826-3.31 2.37-2.37.996.608 2.296.07 2.572-1.065z" />
                        <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2.5" d="M15 12a3 3 0 11-6 0 3 3 0 016 0z" />
                    </svg>
                </button>` : '';
            const card = document.createElement('div');
            card.className = 'indicator-card group p-2.5 border border-white/20 rounded-2xl cursor-pointer flex items-center justify-between shadow-sm transition-all';
            card.setAttribute('onclick', 'toggleIndicatorCard(this, event)');
            card.innerHTML = `
            <div class="flex flex-col">
                <span class="font-black text-indigo-900 uppercase">${name}
                    <div class="group/tooltip relative inline-flex items-center ml-1">
                        <svg onclick="event.stopPropagation(); event.preventDefault(); openInfoModal('${escapedName}')" xmlns="http://www.w3.org/2000/svg" class="h-3.5 w-3.5 text-indigo-300 hover:text-indigo-600 cursor-pointer transition-colors" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                            <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M13 16h-1v-4h-1m1-4h.01M21 12a9 9 0 11-18 0 9 9 0 0118 0z" />
                        </svg>
                    </div>
                </span>
                <span class="text-[0.6rem] text-indigo-400 font-bold uppercase tracking-widest">${subtitle}</span>
            </div>
            <div class="flex items-center gap-2">
                ${settingsButton}
                <input type="checkbox" class="strategy-indicator-check ${checkClass} w-5 h-5 accent-indigo-600 rounded" value="${name}" onclick="event.stopPropagation()" onchange="updateSignalLabels('${mode}')">
            </div>
        `;
            container.appendChild(card);
        });
        placeDailyHighLowBacktestIndicatorCards(container);
    });
}

function placeDailyHighLowBacktestIndicatorCards(container) {
    if (!container) return;
    const vpCard = container.querySelector('input[value="Volume Profile"]')?.closest('.indicator-card');
    const vwapAnchor = container.querySelector('input[value="VWAP"]')?.closest('.indicator-card');
    if (vpCard) {
        if (vwapAnchor?.parentElement === container) {
            container.insertBefore(vpCard, vwapAnchor.nextSibling);
        } else {
            container.appendChild(vpCard);
        }
    }
    let anchor = container.querySelector('input[value="SMA"]')?.closest('.indicator-card');
    ['Prior Day High/Low', 'Pivot Points (S/R)', 'Swing Points'].forEach(name => {
        const card = container.querySelector(`input[value="${name}"]`)?.closest('.indicator-card');
        if (!card) return;
        if (anchor?.parentElement === container) {
            container.insertBefore(card, anchor.nextSibling);
        } else {
            container.appendChild(card);
        }
        anchor = card;
    });
}

document.addEventListener('DOMContentLoaded', () => {
    ensureDailyHighLowBacktestIndicatorCards();
    disableBacktestBotBulls4Indicator();

    // Attach listeners to indicator checkboxes for dynamic slider
    document.querySelectorAll('.strategy-indicator-check').forEach(cb => {
        cb.addEventListener('change', syncBacktestSliderRange);
    });

    // Sync timeframe select change to days input value dynamically
    const btDaysInput = document.getElementById('btDays');
    const btTfRegular = document.getElementById('strategyTimeframe_regular');
    if (btDaysInput) {
        const updateDaysVal = (e) => {
            const tf = e ? e.target.value : btTfRegular?.value;
            if (!tf) return;
            if (tf === '1Min') btDaysInput.value = 7;
            else if (['5Min', '10Min', '15Min', '30Min'].includes(tf)) btDaysInput.value = 60;
            else if (tf === '1Hour') btDaysInput.value = 365;
            else if (tf === '4Hour') btDaysInput.value = 730;
            else if (tf === '1Day') btDaysInput.value = 1825;
        };
        btTfRegular?.addEventListener('change', updateDaysVal);
        btTfRegular?.addEventListener('change', () => window.updateBtDefaultCooldownLabel?.());
        updateDaysVal();
    }

    // Initialize/Sync strategy settings display on load and reset to defaults
    setTimeout(() => {
        if (window.resetAllBacktestSettingsToDefaults) {
            window.resetAllBacktestSettingsToDefaults();
        } else {
            window.toggleBtMode(currentBtMode);
            window.updateBtRegularEntryFields?.();
            window.updateBtRegularExitFields?.();
            window.updateBtAllocationModeUI?.();
            window.updateBtOptionsAllocationModeUI?.();
            window.updateBtSmartReentryFields?.('regular');
            window.updateBtSmartReentryFields?.('options');
            window.updateBtSwingLookbackFields?.('entry');
            window.updateBtSwingLookbackFields?.('exit');
            window.updateBtOptionsEntryFields?.();
            window.updateBtOptionsExitFields?.();
            window.updateBtOptionsDirectionHint?.();
            window.updateBtDefaultCooldownLabel?.();
        }
        disableBacktestBotBulls4Indicator();
    }, 50);

    // Setup Ticker Search Dropdown
    const btTickerInput = document.getElementById('btTicker');
    const btResultsContainer = document.getElementById('btTickerSearchResults');
    let btSearchTimeout = null;

    if (btTickerInput && btResultsContainer) {
        btTickerInput.addEventListener('input', (e) => {
            const query = e.target.value.trim();

            if (query.length < 2) {
                btResultsContainer.classList.add('hidden');
                return;
            }

            clearTimeout(btSearchTimeout);
            btSearchTimeout = setTimeout(async () => {
                try {
                    const headers = await getAuthHeaders();
                    const res = await fetch(`${window.BACKTEST_API_BASE}/api/search/${encodeURIComponent(query)}`, { headers });
                    const data = await res.json();

                    btResultsContainer.innerHTML = '';
                    if (data && data.length > 0) {
                        data.forEach(item => {
                            const div = document.createElement('div');
                            div.className = "p-3 hover:bg-indigo-50 cursor-pointer border-b border-indigo-50 last:border-0 transition-colors flex justify-between items-center";
                            div.innerHTML = `
                                <div class="flex flex-col text-left">
                                    <span class="font-black text-indigo-900">${item.symbol}</span>
                                    <span class="text-[0.65rem] text-indigo-400 font-medium truncate w-40">${item.name}</span>
                                </div>
                                <span class="text-[0.6rem] bg-indigo-100 text-indigo-500 px-2 py-0.5 rounded uppercase font-bold tracking-widest">+ Select</span>
                            `;
                            div.onclick = () => {
                                btTickerInput.value = item.symbol;
                                btResultsContainer.classList.add('hidden');
                                window.checkCryptoOptionStatus(item.symbol);
                            };
                            btResultsContainer.appendChild(div);
                        });
                        btResultsContainer.classList.remove('hidden');
                    } else {
                        btResultsContainer.innerHTML = '<div class="p-3 text-center text-xs text-indigo-400 font-medium">No assets found</div>';
                        btResultsContainer.classList.remove('hidden');
                    }
                } catch (err) {
                    console.error('[backtest-search] Error:', err);
                }
            }, 300);
        });

        // Check on direct keyboard changes and input blur/change
        btTickerInput.addEventListener('change', (e) => {
            window.checkCryptoOptionStatus(e.target.value.trim());
        });
        btTickerInput.addEventListener('blur', (e) => {
            window.checkCryptoOptionStatus(e.target.value.trim());
        });

        // Hide dropdown when clicking outside
        document.addEventListener('click', (e) => {
            if (!btTickerInput.contains(e.target) && !btResultsContainer.contains(e.target)) {
                btResultsContainer.classList.add('hidden');
            }
        });

        // Trigger on load
        setTimeout(() => {
            window.checkCryptoOptionStatus(btTickerInput.value.trim());
        }, 100);
    }

    // Initial slider sync
    syncBacktestSliderRange();
    console.log('[backtest] Center Initialized.');

    // Start Alpaca Status Polling once auth is ready
    const checkAuth = setInterval(async () => {
        if (window.auth && (window.auth.currentUser || localStorage.getItem('dev_mode') === 'true')) {
            clearInterval(checkAuth);
            if (window.resetAllBacktestSettingsToDefaults) {
                await window.resetAllBacktestSettingsToDefaults();
            }
            disableBacktestBotBulls4Indicator();
            pollAlpacaStatus();
            loadBacktestOllamaModels();
            setInterval(pollAlpacaStatus, 30000); // Check every 30s
        }
    }, 500);
});

async function pollAlpacaStatus() {
    const statusPill = document.getElementById('alpacaLinkStatus');
    const statusDot = document.getElementById('alpacaStatusDot');
    const statusText = document.getElementById('alpacaStatusText');
    const btnUnlink = document.getElementById('btnUnlinkAlpaca');
    if (!statusPill) return;

    try {
        const headers = await getAuthHeaders();
        const response = await fetch(`${window.BACKTEST_API_BASE}/api/dashboard?mode=fast&source=backtest`, { headers });
        const data = await response.json();
        window.lastBacktestData = data;

        if (data.simulation) {
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
            statusPill.className = "flex items-center justify-center h-9 text-xs font-black px-5 rounded-full bg-emerald-50 text-emerald-600 border-2 border-emerald-100 shadow-sm whitespace-nowrap transition-all duration-500 uppercase tracking-wider";
            statusDot.className = "h-2 w-2 rounded-full mr-2 bg-emerald-500 animate-pulse";
            statusText.textContent = "Alpaca";
            if (btnUnlink) btnUnlink.classList.remove('hidden');
        }
    } catch (e) {
        console.error('[backtest] Alpaca status check failed:', e);
    }
}

window.unlinkAlpaca = async () => {
    if (!confirm("Are you sure you want to unlink your Alpaca account? The bot will switch back to simulation mode.")) return;

    try {
        const headers = await getAuthHeaders();
        const response = await fetch(`${window.BACKTEST_API_BASE}/api/alpaca_config`, {
            method: 'DELETE',
            headers: headers
        });
        const data = await response.json();
        if (data.status === 'success') {
            alert(data.message);
            pollAlpacaStatus();
        } else {
            alert("Error unlinking account.");
        }
    } catch (err) {
        console.error(err);
        alert("Backend communication error.");
    }
};

window.logoutCommander = async () => {
    const auth = window.auth;
    if (auth) {
        const { signOut } = await import("https://www.gstatic.com/firebasejs/10.7.1/firebase-auth.js");
        await signOut(auth);
    }
    window.location.href = "index.html";
};

var currentBtMode = 'regular';

window.toggleBtMode = function(mode) {
    if (mode === 'automate_grid' || mode === 'automate_ai') return;
    
    currentBtMode = mode;
    
    // Update active UI cards
    document.querySelectorAll('.bt-mode-card').forEach(card => {
        card.classList.remove('border-indigo-500', 'bg-indigo-50', 'active');
        card.classList.add('border-white/60', 'bg-white/40');
        
        // Reset the circle
        const circle = card.querySelector('.w-4.h-4.rounded-full');
        if (circle) {
            circle.className = 'w-4 h-4 rounded-full border-2 border-indigo-200';
            circle.innerHTML = '';
        }
    });
    
    const activeCard = document.getElementById(`btModeCard_${mode}`);
    if (activeCard) {
        activeCard.classList.remove('border-white/60', 'bg-white/40');
        activeCard.classList.add('border-indigo-500', 'bg-indigo-50', 'active');
        
        // Set the active circle
        const circle = activeCard.querySelector('.w-4.h-4.rounded-full');
        if (circle) {
            circle.className = 'w-4 h-4 rounded-full bg-indigo-500 shadow-inner flex items-center justify-center';
            circle.innerHTML = '<div class="w-1.5 h-1.5 bg-white rounded-full"></div>';
        }
    }

    // Toggle boxes
    document.getElementById('btRegularTradingBox')?.classList.toggle('hidden', mode !== 'regular');
    document.getElementById('btOptionsTradingBox')?.classList.toggle('hidden', mode !== 'options');
    
    // The visible market-session selector is regular-only. Options requests stay regular-hours in payload code.
    const marketSessionSelect = document.getElementById('strategyMarketSession_regular');
    if (marketSessionSelect) {
        marketSessionSelect.disabled = mode === 'options';
        marketSessionSelect.classList.toggle('opacity-50', mode === 'options');
        marketSessionSelect.classList.toggle('cursor-not-allowed', mode === 'options');
    }
    window.updateBtSmartReentryFields?.(mode);
    window.updateBtDefaultCooldownLabel?.();
}

// 2. Strategy Execution
async function runBacktest(options = {}) {
    const forceRun = options?.force === true;
    const runBtn = document.getElementById('runBtBtn');
    if (runBtn?.disabled && !forceRun) return;

    const ticker = document.getElementById('btTicker').value.toUpperCase();
    const days = document.getElementById('btDays').value;
    const timeframe = document.getElementById('strategyTimeframe_regular').value;
    const capital = currentBtMode === 'regular'
        ? (document.getElementById('strategyTradeAmount_regular')?.value || '1000')
        : (document.getElementById('strategyOptionsTradeAmount')?.value || document.getElementById('strategyCapital_options')?.value || '1000');

    // Validate requested days based on timeframe limits (Yahoo Finance constraints)
    let maxDays = 1825;
    let limitDesc = "5 years";
    if (timeframe === '1Min') {
        maxDays = 7;
        limitDesc = "7 days";
    } else if (['5Min', '10Min', '15Min', '30Min'].includes(timeframe)) {
        maxDays = 60;
        limitDesc = "60 days";
    } else if (['1Hour', '4Hour'].includes(timeframe)) {
        maxDays = 730;
        limitDesc = "2 years (730 days)";
    }

    if (parseInt(days) > maxDays) {
        alert(`Yahoo Finance only supports a maximum of ${limitDesc} of historical data for the ${timeframe} interval. Please reduce "Simulation Days" to ${maxDays} or less.`);
        return;
    }

    const regularStrategyPayload = !currentBtMode || currentBtMode === 'regular' ? getBtRegularStrategyPayload() : null;
    const extHoursVal = currentBtMode === 'regular' ? regularStrategyPayload.ext_hours : false;
    const closeBeforeExpiry = 0;

    const optionsEnabled = (currentBtMode === 'options');

    const cooldownMode = optionsEnabled ? 'options' : 'regular';
    const enableCooldown = document.getElementById(`strategyEnableCooldown_${cooldownMode}`)?.checked ?? true;
    let cooldownVal = document.getElementById(`strategyCooldownMinutes_${cooldownMode}`)?.value || 'default';
    let cooldownMinutes = null;
    if (cooldownVal === 'custom') {
        cooldownMinutes = parseInt(document.getElementById(`strategyCooldownCustomMinutes_${cooldownMode}`)?.value || '60');
    } else if (cooldownVal !== 'default') {
        cooldownMinutes = parseInt(cooldownVal);
    }

    const selectedIndicatorClass = optionsEnabled ? '.bt-indicator-check-options' : '.bt-indicator-check-regular';
    const indicators = Array.from(document.querySelectorAll(`${selectedIndicatorClass}:checked:not(:disabled)`))
        .map(cb => cb.value);

    const indicatorModes = ['indicator', 'hybrid', 'hybrid_atr', 'hybrid_pct', 'hybrid_pivot', 'hybrid_high_low', 'hybrid_swing'];
    const modeVal = optionsEnabled ? 'options' : 'regular';
    const buyModeVal = document.getElementById(`strategyBuyMode_${modeVal}`)?.value || 'indicator';
    const sellModeVal = document.getElementById(`strategySellMode_${modeVal}`)?.value || (optionsEnabled ? 'hybrid' : 'indicator');

    if (indicatorModes.includes(buyModeVal)) {
        if (indicators.length === 0) {
            const modeName = buyModeVal.replace(/_/g, ' ').toUpperCase();
            alert(`You selected [${modeName}] as your Entry Strategy, which requires technical indicators to generate buy signals.\n\nPlease check at least 1 indicator from the Indicator Pool below, or switch your Entry Strategy dropdown to a standalone mode.`);
            return;
        }
        const buyThreshold = parseInt(document.getElementById(`strategyBuyThreshold_${modeVal}`)?.value || 1, 10);
        if (buyThreshold > indicators.length) {
            alert(`Your Entry Strategy requires ${buyThreshold} buy signals to trigger an entry, but only ${indicators.length} indicator(s) are checked in your pool!\n\nPlease lower your Buy Signal Threshold to match your checked indicators (between 1 and ${indicators.length}), or check more indicators below.`);
            return;
        }
    }
    if (indicatorModes.includes(sellModeVal)) {
        if (indicators.length === 0) {
            const modeName = sellModeVal.replace(/_/g, ' ').toUpperCase();
            alert(`You selected [${modeName}] as your Exit Strategy, which requires technical indicators to generate sell signals.\n\nPlease check at least 1 indicator from the Indicator Pool below, or switch your Exit Strategy dropdown to a standalone mode.`);
            return;
        }
        const sellThreshold = parseInt(document.getElementById(`strategySellThreshold_${modeVal}`)?.value || 1, 10);
        if (sellThreshold > indicators.length) {
            alert(`Your Exit Strategy requires ${sellThreshold} sell signals to trigger an exit, but only ${indicators.length} indicator(s) are checked in your pool!\n\nPlease lower your Sell Signal Threshold to match your checked indicators (between 1 and ${indicators.length}), or check more indicators below.`);
            return;
        }
    }

    // UI Feedback
    if (runBtn) {
        runBtn.disabled = true;
        runBtn.classList.add('hidden');
    }
    document.getElementById('btResultsModal')?.classList.add('hidden');
    document.getElementById('btSettingsContainer').classList.add('hidden');
    document.getElementById('btResults').classList.add('hidden');
    document.getElementById('btLoading').classList.remove('hidden');

    try {
        const headers = await getAuthHeaders();
        let response;

        if (optionsEnabled) {
            const tradeAmount = parseFloat(document.getElementById('strategyCapital_options')?.value || '1000');
            
            const optionsType = document.getElementById('strategyOptionsOptionType')?.value || 'calls_only';
            const expiryDays = parseInt(document.getElementById('strategyOptionsExpiryDays')?.value || '3');
            const strikeType = document.getElementById('strategyOptionsStrikeType')?.value || 'ATM';
            const exitStrategy = document.getElementById('strategySellMode_options')?.value || 'indicator';
            const stopLossPct = parseFloat(document.getElementById('strategyOptionsSlPct')?.value || '50') / 100.0;
            const takeProfitPct = parseFloat(document.getElementById('strategyOptionsTpPct')?.value || '100') / 100.0;
            const enablePctTrail = document.getElementById('strategyEnablePctTrail_options')?.checked ?? false;
            const trailPct = enablePctTrail
                ? (parseFloat(document.getElementById('strategyOptionsTrailPct')?.value || '3') / 100.0)
                : 0.0;
            const atrStopMult = parseFloat(document.getElementById('strategyAtrStopMult_options')?.value || '2');
            const atrTpMult = parseFloat(document.getElementById('strategyAtrTpMult_options')?.value || '4');
            const enableAtrTrail = document.getElementById('strategyEnableAtrTrail_options')?.checked ?? false;
            const atrTrailMult = enableAtrTrail
                ? parseFloat(document.getElementById('strategyAtrTrailMult_options')?.value || '3')
                : 0.0;
            const buyMode = document.getElementById('strategyBuyMode_options')?.value || 'indicator';
            const customBuyPrice = parseFloat(document.getElementById('strategyCustomBuyPrice_options')?.value || '0');
            const customStopLossPrice = parseFloat(document.getElementById('strategyCustomStopLossPrice_options')?.value || '0');
            const customTakeProfitPrice = parseFloat(document.getElementById('strategyCustomTakeProfitPrice_options')?.value || '0');
            const customUnderlyingStopLossPrice = parseFloat(document.getElementById('strategyCustomUnderlyingStopLossPrice_options')?.value || '0');
            const customUnderlyingTakeProfitPrice = parseFloat(document.getElementById('strategyCustomUnderlyingTakeProfitPrice_options')?.value || '0');
            const entryAtrMultiplier = parseFloat(document.getElementById('strategyEntryAtrMult_options')?.value || '2');
            const entryAtrStyle = document.getElementById('strategyEntryAtrStyle_options')?.value || 'pullback_reclaim';
            const entryPctMove = parseFloat(document.getElementById('strategyEntryPctMove_options')?.value || '5');
            const entryPctStyle = document.getElementById('strategyEntryPctStyle_options')?.value || 'dip_reclaim';
            const swingEntryMode = document.getElementById('strategySwingEntryMode_options')?.value || 'bounce_retest';
            const swingEntryLookbackDays = getBtOptionsSwingLookbackDays('entry');
            const swingExitLookbackDays = getBtOptionsSwingLookbackDays('exit');
            const enableRisk = document.getElementById('btEnableRisk_options')?.checked ?? true;
            const riskPerTrade = enableRisk ? (Math.min(50.0, parseFloat(document.getElementById('btRiskPerTrade_options')?.value || '50.0')) / 100.0) : 1.0;
            const maxPositionPct = enableRisk ? (parseFloat(document.getElementById('btMaxPositionPct_options')?.value || '100.0') / 100.0) : 1.0;
            const r = parseFloat(document.getElementById('strategyOptionsRiskFreeRate')?.value || '5.0') / 100.0;

            const enableSignalReset = document.getElementById('strategyOptionsSignalReset')?.checked || false;
            const enableSmartReentry = document.getElementById('strategyOptionsSmartReentry')?.checked || false;
            const smartReentryType = document.getElementById('strategyOptionsSmartReentryType')?.value || 'RSI';
            const smartReentryRsiCallMax = parseFloat(document.getElementById('strategyOptionsRsiCallMax')?.value || '70');
            const smartReentryRsiPutMin = parseFloat(document.getElementById('strategyOptionsRsiPutMin')?.value || '30');
            const smartReentryVwapCallMaxDistPct = parseFloat(document.getElementById('strategyOptionsVwapCallMaxPct')?.value || '0.5');
            const smartReentryVwapPutMaxDistPct = parseFloat(document.getElementById('strategyOptionsVwapPutMaxPct')?.value || '0.5');

            window.lastBacktestRun = {
                is_options: true,
                ticker, timeframe: timeframe, days: parseInt(days), capital: parseFloat(capital),
                trade_amount: tradeAmount, expiry_days: expiryDays, strike_type: strikeType,
                options_type: optionsType,
                stop_loss_pct: stopLossPct, take_profit_pct: takeProfitPct,
                exit_strategy: exitStrategy, close_before_expiry_hours: closeBeforeExpiry,
                buy_mode: buyMode, custom_buy_price: customBuyPrice,
                custom_stop_loss_price: customStopLossPrice,
                custom_take_profit_price: customTakeProfitPrice,
                custom_underlying_stop_loss_price: customUnderlyingStopLossPrice,
                custom_underlying_take_profit_price: customUnderlyingTakeProfitPrice,
                entry_atr_multiplier: entryAtrMultiplier,
                entry_atr_style: entryAtrStyle,
                entry_pct_move: entryPctMove,
                entry_pct_style: entryPctStyle,
                swing_entry_mode: swingEntryMode,
                swing_entry_lookback_days: swingEntryLookbackDays,
                swing_exit_lookback_days: swingExitLookbackDays,
                indicators, ext_hours: extHoursVal, r, results: null,
                enable_cooldown: enableCooldown,
                cooldown_minutes: cooldownMinutes,
                enable_risk: enableRisk,
                enable_atr_trail: enableAtrTrail,
                enable_signal_reset: enableSignalReset,
                enable_smart_reentry: enableSmartReentry,
                smart_reentry_type: smartReentryType,
                smart_reentry_rsi_call_max: smartReentryRsiCallMax,
                smart_reentry_rsi_put_min: smartReentryRsiPutMin,
                smart_reentry_vwap_call_max_dist_pct: smartReentryVwapCallMaxDistPct,
                smart_reentry_vwap_put_max_dist_pct: smartReentryVwapPutMaxDistPct,
                indicator_overrides: getIndicatorOverrides()
            };

            console.log(`[backtest] Starting Options request to: ${window.BACKTEST_API_BASE}/api/options/backtest`);
            response = await fetch(`${window.BACKTEST_API_BASE}/api/options/backtest`, {
                method: 'POST',
                headers: headers,
                body: JSON.stringify({
                    ticker,
                    timeframe: timeframe,
                    days: parseInt(days),
                    capital: parseFloat(capital),
                    threshold: parseInt(document.getElementById('strategyBuyThreshold_options')?.value || '0'),
                    sell_threshold: parseInt(document.getElementById('strategySellThreshold_options')?.value || '0'),
                    trade_amount: tradeAmount,
                    expiry_days: expiryDays,
                    strike_type: strikeType,
                    options_type: optionsType,
                    stop_loss_pct: stopLossPct,
                    take_profit_pct: takeProfitPct,
                    regular_trailing_pct: trailPct,
                    enable_pct_trail: enablePctTrail,
                    enable_atr_trail: enableAtrTrail,
                    atr_stop_multiplier: atrStopMult,
                    atr_take_profit_multiplier: atrTpMult,
                    atr_trail_multiplier: atrTrailMult,
                    buy_mode: buyMode,
                    custom_buy_price: customBuyPrice,
                    custom_stop_loss_price: customStopLossPrice,
                    custom_take_profit_price: customTakeProfitPrice,
                    custom_underlying_stop_loss_price: customUnderlyingStopLossPrice,
                    custom_underlying_take_profit_price: customUnderlyingTakeProfitPrice,
                    entry_atr_multiplier: entryAtrMultiplier,
                    entry_atr_style: entryAtrStyle,
                    entry_pct_move: entryPctMove,
                    entry_pct_style: entryPctStyle,
                    swing_entry_mode: swingEntryMode,
                    swing_entry_lookback_days: swingEntryLookbackDays,
                    swing_exit_lookback_days: swingExitLookbackDays,
                    sell_mode: exitStrategy,
                    risk_per_trade: riskPerTrade,
                    max_pos_pct: maxPositionPct,
                    exit_strategy: exitStrategy,
                    close_before_expiry_hours: closeBeforeExpiry,
                    indicators: indicators,
                    ext_hours: extHoursVal,
                    r,
                    enable_cooldown: enableCooldown,
                    cooldown_minutes: cooldownMinutes,
                    enable_risk: enableRisk,
                    enable_signal_reset: enableSignalReset,
                    enable_smart_reentry: enableSmartReentry,
                    smart_reentry_type: smartReentryType,
                    smart_reentry_rsi_call_max: smartReentryRsiCallMax,
                    smart_reentry_rsi_put_min: smartReentryRsiPutMin,
                    smart_reentry_vwap_call_max_dist_pct: smartReentryVwapCallMaxDistPct,
                    smart_reentry_vwap_put_max_dist_pct: smartReentryVwapPutMaxDistPct,
                    indicator_overrides: getIndicatorOverrides()
                })
            });

        } else {
            // Gather exit strategy and risk controls for stock
            const sellMode = document.getElementById('strategySellMode_regular')?.value || 'indicator';
            const enableRisk = document.getElementById('btEnableRisk_regular')?.checked ?? true;
            const riskPerTrade = enableRisk ? (Math.min(50.0, parseFloat(document.getElementById('btRiskPerTrade')?.value || '50.0')) / 100.0) : 1.0;
            const maxPositionPct = enableRisk ? (parseFloat(document.getElementById('btMaxPositionPct')?.value || '100.0') / 100.0) : 1.0;
            const atrStopMult = parseFloat(document.getElementById('strategyAtrStopMult')?.value || '2');
            const enableAtrTrail = document.getElementById('strategyEnableAtrTrail')?.checked ?? false;
            const atrTrailMult = enableAtrTrail
                ? parseFloat(document.getElementById('strategyAtrTrailMult')?.value || '3')
                : 0.0;
            const atrTpMult = parseFloat(document.getElementById('strategyAtrTpMult')?.value || '4');
            const regularSlPct = parseFloat(document.getElementById('strategyRegularSlPct')?.value || '5') / 100.0;
            const regularTpPct = parseFloat(document.getElementById('strategyRegularTpPct')?.value || '10') / 100.0;
            const enablePctTrail = document.getElementById('strategyEnablePctTrail')?.checked ?? false;
            const regularTrailPct = enablePctTrail
                ? (parseFloat(document.getElementById('strategyRegularTrailPct')?.value || '3') / 100.0)
                : 0.0;
            const buyMode = document.getElementById('strategyBuyMode_regular')?.value || 'indicator';
            const customBuyPrice = parseFloat(document.getElementById('strategyCustomBuyPrice_regular')?.value || '0');
            const regularExtra = getBtRegularStrategyPayload();

            window.lastBacktestRun = {
                is_options: false,
                ticker, timeframe, days: parseInt(days), capital: parseFloat(capital),
                indicators, ext_hours: regularExtra.ext_hours,
                buy_mode: buyMode, custom_buy_price: customBuyPrice,
                sell_mode: sellMode, risk_per_trade: riskPerTrade,
                max_position_pct: maxPositionPct, atr_stop_multiplier: atrStopMult,
                atr_trail_multiplier: atrTrailMult, atr_take_profit_multiplier: atrTpMult,
                enable_atr_trail: enableAtrTrail,
                regular_stop_loss_pct: regularSlPct, regular_take_profit_pct: regularTpPct, regular_trailing_pct: regularTrailPct,
                enable_pct_trail: enablePctTrail,
                enable_cooldown: enableCooldown, cooldown_minutes: cooldownMinutes,
                enable_risk: enableRisk,
                ...regularExtra,
                indicator_overrides: getIndicatorOverrides(),
                results: null
            };

            console.log(`[backtest] Starting Stock request to: ${window.BACKTEST_API_BASE}/api/backtest`);
            response = await fetch(`${window.BACKTEST_API_BASE}/api/backtest`, {
                method: 'POST',
                headers: headers,
                body: JSON.stringify({
                    ticker,
                    timeframe,
                    days: parseInt(days),
                    capital: parseFloat(capital),
                    threshold: parseInt(document.getElementById('strategyBuyThreshold_regular')?.value || '0'),
                    sell_threshold: parseInt(document.getElementById('strategySellThreshold_regular')?.value || '0'),
                    indicators,
                    ext_hours: regularExtra.ext_hours,
                    buy_mode: buyMode,
                    custom_buy_price: customBuyPrice,
                    sell_mode: sellMode,
                    risk_per_trade: riskPerTrade,
                    max_position_pct: maxPositionPct,
                    atr_stop_multiplier: atrStopMult,
                    atr_trail_multiplier: atrTrailMult,
                    atr_take_profit_multiplier: atrTpMult,
                    enable_atr_trail: enableAtrTrail,
                    regular_stop_loss_pct: regularSlPct,
                    regular_take_profit_pct: regularTpPct,
                    regular_trailing_pct: regularTrailPct,
                    enable_pct_trail: enablePctTrail,
                    enable_cooldown: enableCooldown,
                    cooldown_minutes: cooldownMinutes,
                    enable_risk: enableRisk,
                    ...regularExtra,
                    indicator_overrides: getIndicatorOverrides()
                })
            });
        }

        if (!response.ok) {
            throw new Error(`HTTP Error: ${response.status}`);
        }

        const data = await response.json();
        console.log('[backtest] Response received:', data.status);

        if (data.status === 'success') {
            let resToDisplay = data.results;
            if (data.results && data.results.outsample_results) {
                resToDisplay = data.results.outsample_results;
                resToDisplay.optimizer_meta = {
                    overfit_score: data.results.overfit_score,
                    insample_roi: data.results.insample_results?.summary?.roi_pct || 0,
                    best_params: data.results.best_parameters
                };
            }
            if (window.lastBacktestRun) {
                window.lastBacktestRun.results = resToDisplay;
            }
            displayResults(resToDisplay);
            if (window.lastBacktestRun && resToDisplay && resToDisplay.summary) {
            }
        } else {
            const errMsg = data.message || data.error || "Unknown error";
            alert("Backtest Failed: " + errMsg);
            resetBacktestUI();
        }
    } catch (err) {
        console.error("BACKTEST FETCH ERROR:", err);
        alert(`Connection Failed: ${err.message}\n\nTroubleshooting:\n1. Ensure you have a stable internet connection.\n2. If on mobile, try disabling VPN or Private Relay.\n3. Try a Hard Refresh (Cmd+Shift+R or clear mobile cache).`);
        resetBacktestUI();
    }
}

// 3. Results Rendering
function displayResults(res) {
    document.getElementById('btLoading').classList.add('hidden');
    
    // Show Modal
    const modal = document.getElementById('btResultsModal');
    modal.classList.remove('hidden');
    document.getElementById('btResults').classList.remove('hidden');

    const s = res.summary;
    const formatMetric = (value, digits = 2, suffix = '') => {
        if (value === undefined || value === null) return '—';
        if (Number.isFinite(value)) return value.toFixed(digits) + suffix;
        return '∞';
    };

    if (!s) {
        console.error('[backtest] displayResults called with no summary:', res);
        alert('Backtest returned an unexpected result format. Check browser console for details.');
        resetBacktestUI();
        return;
    }
    // ── Header Metadata

    const metaEl = document.getElementById('resRunMeta');
    if (res.optimizer_meta) {
        let overfitHtml = '';
        if (res.optimizer_meta.overfit_score >= 0.8) {
            overfitHtml = `<span class="bg-emerald-500/20 text-emerald-300 px-2 py-0.5 rounded ml-2">🟢 ROBUST (Score: ${res.optimizer_meta.overfit_score})</span>`;
        } else if (res.optimizer_meta.overfit_score >= 0.5) {
            overfitHtml = `<span class="bg-amber-500/20 text-amber-300 px-2 py-0.5 rounded ml-2">🟡 DEGRADED (Score: ${res.optimizer_meta.overfit_score})</span>`;
        } else {
            overfitHtml = `<span class="bg-rose-500/20 text-rose-300 px-2 py-0.5 rounded ml-2">🔴 OVERFITTED (Score: ${res.optimizer_meta.overfit_score})</span>`;
        }
        if (metaEl) {
            metaEl.innerHTML = `Out-of-Sample Validation Result ${overfitHtml} <br> Best Params: Thresh ${res.optimizer_meta.best_params.threshold}, Sell ${res.optimizer_meta.best_params.sell_threshold}, ATR Stop ${res.optimizer_meta.best_params.atr_stop_multiplier}`;
        }
    } else {
        if (metaEl && s.ticker) {
            const cstOpts = { timeZone: 'America/Chicago', month: 'short', day: 'numeric', year: 'numeric' };
            let startCST = '';
            let endCST = '';
            try {
                startCST = new Date(s.start_date_utc).toLocaleString('en-US', cstOpts);
                endCST = new Date(s.end_date_utc).toLocaleString('en-US', cstOpts);
            } catch (e) {
                startCST = s.start_date_utc || '';
                endCST = s.end_date_utc || '';
            }
            let optionBadge = '';
            if (s.options_type) {
                const optLabel = s.options_type === 'calls_only' ? 'CALL' : (s.options_type === 'puts_only' ? 'PUT' : 'CALL + PUT');
                optionBadge = ` <span class="ml-2 px-2 py-0.5 rounded bg-fuchsia-500/30 text-fuchsia-200 border border-fuchsia-400/40">${optLabel} · ${s.expiry_days} DTE · ${s.strike_type}</span>`;
            }
            const riskBadge = s.enable_risk !== undefined ? ` <span class="ml-1 px-2 py-0.5 rounded ${s.enable_risk ? 'bg-indigo-500/30 text-indigo-200' : 'bg-rose-500/30 text-rose-200'}">Risk: ${s.enable_risk ? 'ON' : 'OFF'}</span>` : '';
            const sourceBadge = s.data_source ? ` <span class="ml-1 px-2 py-0.5 rounded bg-emerald-500/20 text-emerald-200 border border-emerald-400/30">Data: ${String(s.data_source).toUpperCase()}</span>` : '';
            metaEl.innerHTML = `<div>${s.ticker}  ·  ${s.timeframe}  ·  ${s.period_days} Days  ·  ${startCST} – ${endCST} CST</div><div class="mt-1 flex flex-wrap items-center gap-1.5 text-[0.6rem]">${optionBadge}${riskBadge}${sourceBadge}</div>`;
        }
        if (metaEl && s.no_trade_reason) {
            metaEl.innerHTML = `${metaEl.innerHTML || ''}<div class="mt-2 rounded-xl border border-amber-300/40 bg-amber-500/10 px-3 py-2 text-amber-100 font-bold">No trades executed: ${s.no_trade_reason}</div>`;
        }
    }

    // Stats
    document.getElementById('resRoi').textContent = s.roi_pct.toFixed(2) + '%';
    document.getElementById('resWinRate').textContent = s.win_rate_pct.toFixed(1) + '%';
    document.getElementById('resTrades').textContent = s.total_trades;
    document.getElementById('resEquity').textContent = '$' + s.final_equity.toLocaleString(undefined, { minimumFractionDigits: 2 });
    document.getElementById('resHoldEquity').textContent = '$' + s.buy_hold_equity.toLocaleString(undefined, { minimumFractionDigits: 2 });
    document.getElementById('resHoldRoi').textContent = s.buy_hold_roi_pct.toFixed(2) + '% Return';

    // Max Drawdown
    const ddEl = document.getElementById('resMaxDrawdown');
    if (ddEl) ddEl.textContent = (s.max_drawdown_pct || 0).toFixed(2) + '%';

    // Professional Metrics
    document.getElementById('resSharpe').textContent = formatMetric(s.sharpe_ratio);
    document.getElementById('resProfitFactor').textContent = formatMetric(s.profit_factor, 2, 'x');
    
    // Win / Loss Ratio text formatting
    const wlEl = document.getElementById('resWinLossRatio');
    if (s.win_loss_ratio !== undefined && s.win_loss_ratio !== null && isFinite(s.win_loss_ratio)) {
        wlEl.textContent = s.win_loss_ratio.toFixed(2) + 'x';
    } else {
        wlEl.textContent = '—';
    }

    // Advanced Metrics Row
    document.getElementById('resSortino').textContent = formatMetric(s.sortino_ratio);
    document.getElementById('resCalmar').textContent = formatMetric(s.calmar_ratio);
    document.getElementById('resHoldTime').textContent = (s.avg_hold_hours !== undefined && s.avg_hold_hours !== null) ? formatMetric(s.avg_hold_hours, 1, 'h') : '—';
    document.getElementById('resExpectancy').textContent = (s.expectancy !== undefined && s.expectancy !== null) ? '$' + formatMetric(s.expectancy) : '—';
    document.getElementById('resWinStreak').textContent = (s.max_win_streak !== undefined) ? s.max_win_streak : '—';
    document.getElementById('resLossStreak').textContent = (s.max_loss_streak !== undefined) ? s.max_loss_streak : '—';

    // Color logic for ROI
    const roiEl = document.getElementById('resRoi');
    roiEl.className = `text-xl font-black tracking-tight ${s.roi_pct >= 0 ? 'text-emerald-500' : 'text-rose-500'}`;

    // Color logic for new cards
    const sharpeEl = document.getElementById('resSharpe');
    if (s.sharpe_ratio >= 1.2) sharpeEl.className = 'text-xl font-black tracking-tight text-emerald-600';
    else if (s.sharpe_ratio >= 0.5) sharpeEl.className = 'text-xl font-black tracking-tight text-amber-600';
    else sharpeEl.className = 'text-xl font-black tracking-tight text-rose-600';

    const pfEl = document.getElementById('resProfitFactor');
    if (s.profit_factor >= 1.5) pfEl.className = 'text-xl font-black tracking-tight text-emerald-600';
    else if (s.profit_factor >= 1.0) pfEl.className = 'text-xl font-black tracking-tight text-amber-600';
    else pfEl.className = 'text-xl font-black tracking-tight text-rose-600';

    const holdRoiEl = document.getElementById('resHoldRoi');
    const holdColor = s.buy_hold_roi_pct >= 0 ? 'text-emerald-400 bg-emerald-400/10 border-emerald-400/20' : 'text-rose-400 bg-rose-400/10 border-rose-400/20';
    holdRoiEl.className = `text-[0.65rem] font-black uppercase tracking-widest px-3 py-1 rounded-full inline-block mt-2 border transition-all ${holdColor}`;

    // Reset Monte Carlo panel state
    document.getElementById('mcResults').classList.add('hidden');
    document.getElementById('mcHint').classList.remove('hidden');
    document.getElementById('mcLoading').classList.add('hidden');
    document.getElementById('runMcBtn').disabled = false;

    // Trade Log (existing table below)
    allBtTrades = [...(res.trades || [])].reverse(); // oldest-first chronological order
    btPage = 1;
    renderBtTradesPage();

    // ── Build backtest chart + trade card slider ─────────────────────────
    btChartTradesRef = [...(res.trades || [])].reverse(); // oldest-first chronological order
    btChartCandlesRef = res.candles || [];
    btChartEquityCurveRef = res.equity_curve || [];
    btChartTradeIdx = 0;
    applyBtPriorDayBufferToCachedCandles();

    if (btChartCandlesRef.length > 0) {
        document.getElementById('btChartSection').classList.remove('hidden');
        _btInitChart(btChartCandlesRef, btChartTradesRef, btChartEquityCurveRef);
    } else {
        document.getElementById('btChartSection').classList.add('hidden');
    }
    // ─────────────────────────────────────────────────────────────────────
}

function renderBtTradesPage() {
    const start = (btPage - 1) * btPageSize;
    const end = start + btPageSize;
    const paginated = allBtTrades.slice(start, end);
    const tbody = document.getElementById('btTradeLog');
    tbody.innerHTML = '';

    if (allBtTrades.length > 0) {
        document.getElementById('btPagination').classList.remove('hidden');
        const totalPages = Math.ceil(allBtTrades.length / btPageSize);
        document.getElementById('btPageInput').value = btPage;
        document.getElementById('btPageTotal').textContent = `of ${totalPages}`;
        document.getElementById('btPageInfoTop').textContent = `Showing ${start + 1}-${Math.min(end, allBtTrades.length)} of ${allBtTrades.length} trades`;
    } else {
        document.getElementById('btPagination').classList.add('hidden');
        tbody.innerHTML = '<tr><td colspan="7" class="py-6 text-center text-purple-400 text-xs md:text-sm font-medium italic">No strategy executions detected with current parameters.</td></tr>';
        return;
    }

    paginated.forEach(t => {
        const plClass = t.pl_pct >= 0 ? 'text-emerald-600 font-bold' : 'text-red-500 font-bold';
        const isOption = t.option_type !== undefined;
        let entryHtml = `
            <div class="text-xs font-semibold text-indigo-950">${formatDate(t.entry_time)}</div>
            <div class="text-[0.6rem] text-purple-400 font-medium mt-0.5">Entry @ $${formatAssetPrice(t.entry_price, isCrypto(t.symbol || window.activeTicker))}</div>
        `;
        let exitHtml = `
            <div class="text-xs font-semibold text-indigo-950">${formatDate(t.exit_time)}</div>
            <div class="text-[0.6rem] text-purple-400 font-medium mt-0.5">Exit @ $${formatAssetPrice(t.exit_price, isCrypto(t.symbol || window.activeTicker))}</div>
        `;
        let qtyHtml = `${t.qty.toFixed(4)}`;

        if (isOption) {
            entryHtml = `
                <div class="text-xs font-semibold text-indigo-950">${formatDate(t.entry_time)}</div>
                <div class="text-[0.65rem] text-indigo-600 font-black mt-0.5">${t.option_type} $${t.strike} @ $${formatAssetPrice(t.entry_price, isCrypto(t.symbol || window.activeTicker))}</div>
                <div class="text-[0.55rem] text-slate-400">Stock: $${formatAssetPrice(t.entry_underlying, isCrypto(t.symbol || window.activeTicker))}</div>
            `;
            exitHtml = `
                <div class="text-xs font-semibold text-indigo-950">${formatDate(t.exit_time)}</div>
                <div class="text-[0.65rem] text-indigo-600 font-black mt-0.5">Exit @ $${formatAssetPrice(t.exit_price, isCrypto(t.symbol || window.activeTicker))}</div>
                <div class="text-[0.55rem] text-slate-400">Stock: $${formatAssetPrice(t.exit_underlying, isCrypto(t.symbol || window.activeTicker))}</div>
                <div class="text-[0.55rem] text-rose-500 font-bold max-w-[120px] truncate" title="${t.reason || ''}">${t.reason || ''}</div>
            `;
            qtyHtml = `${t.qty} Contract${t.qty !== 1 ? 's' : ''}`;
        }

        const row = `
            <tr class="border-b border-purple-100 hover:bg-purple-50/30 transition-colors">
                <td class="py-2 px-3">${entryHtml}</td>
                <td class="py-2 px-3">${exitHtml}</td>
                <td class="py-2 px-3 text-center text-xs font-bold text-indigo-900">${qtyHtml}</td>
                <td class="py-2 px-3 text-center text-xs font-medium text-indigo-700">$${t.entry_cost.toFixed(2)}</td>
                <td class="py-2 px-3 text-center text-xs font-bold text-indigo-950">$${t.exit_value.toFixed(2)}</td>
                <td class="py-2 px-3 text-center text-[0.65rem] text-purple-400 font-mono">$${(t.fees || 0).toFixed(2)}</td>
                <td class="py-2 px-3 text-center text-xs ${plClass}">${t.pl_pct >= 0 ? '+' : ''}${t.pl_pct.toFixed(2)}%</td>
            </tr>
        `;
        tbody.insertAdjacentHTML('beforeend', row);
    });

    // NEW: Wire trade row clicks for chart sync
    if (btChart) _btWireTradeRowClicks();
}

function changeBtPage(dir) {
    const totalPages = Math.ceil(allBtTrades.length / btPageSize);
    const newPage = btPage + dir;
    if (newPage >= 1 && newPage <= totalPages) {
        btPage = newPage;
        renderBtTradesPage();
    }
}

function jumpBtPage(pageStr) {
    const totalPages = Math.ceil(allBtTrades.length / btPageSize);
    let newPage = parseInt(pageStr, 10);
    if (isNaN(newPage)) newPage = 1;
    newPage = Math.max(1, Math.min(totalPages, newPage));
    btPage = newPage;
    renderBtTradesPage();
}

// 4. Helper Functions
function resetBacktestUI() {
    // ── NEW: Destroy chart to prevent stale renders on next run ──────────
    if (btChart) {
        try { btChart.remove(); } catch (e) {}
        btChart = null;
        btCandleSeries = null;
        btEqSeries = null;
        btVolumeSeries = null;
        btDailyHighLineSeries = null;
        btDailyLowLineSeries = null;
        btDailyHighTriggerLineSeries = null;
        btDailyLowTriggerLineSeries = null;
        btPriceLineLabels = {};
        btPriceLineLabelMeta = {};
        btChartMarkersRef = [];
        btChartEquityCurveRef = [];
        btEqSeries = null;
    }
    if (btChartResizeObserver) {
        try { btChartResizeObserver.disconnect(); } catch (e) {}
        btChartResizeObserver = null;
    }
    document.getElementById('btChartSection').classList.add('hidden');
    
    // Remove keyboard navigator to avoid leaking listeners
    if (window._btKeyHandler) {
        document.removeEventListener('keydown', window._btKeyHandler);
        window._btKeyHandler = null;
    }
    // ─────────────────────────────────────────────────────────────────────

    document.getElementById('btResultsModal').classList.add('hidden');
    document.getElementById('btResults').classList.add('hidden');
    document.getElementById('btSettingsContainer').classList.remove('hidden');
    const runBtn = document.getElementById('runBtBtn');
    if (runBtn) {
        runBtn.disabled = false;
        runBtn.classList.remove('hidden');
    }
    document.getElementById('btLoading').classList.add('hidden');
    window.lastBacktestRun = null;
}

window.resetAllBacktestSettingsToDefaults = async function() {
    window.btIndicatorOverrides = {};
    if (window.lastBacktestData) {
        window.lastBacktestData.indicator_overrides = {};
        window.lastBacktestData.indicator_parameters = {};
    }
    try {
        const headers = await getAuthHeaders();
        await fetch(`${window.BACKTEST_API_BASE}/api/settings/indicators?context=backtest`, {
            method: 'POST',
            headers: headers,
            body: JSON.stringify({ reset_all: true })
        });
    } catch (e) {}

    const setVal = (id, val) => {
        const el = document.getElementById(id);
        if (el) el.value = val;
    };
    const setCheck = (id, checked) => {
        const el = document.getElementById(id);
        if (el) el.checked = checked;
    };

    // Global Top Inputs
    setVal('btTicker', 'SPY');
    setVal('btDays', 365);
    setVal('strategyTimeframe_regular', '1Hour');

    // Uncheck all indicator boxes
    document.querySelectorAll('.strategy-indicator-check').forEach(cb => {
        cb.checked = false;
    });

    // Regular Strategy Defaults
    setVal('strategyAllocationMode_regular', 'fixed');
    setVal('strategyTradeAmount_regular', '1000');
    setVal('strategyCompoundPct_regular', '10');
    setVal('strategyBuyMode_regular', 'indicator');
    setVal('strategyCustomBuyPrice_regular', '');
    setVal('strategyEntryAtrMult_regular', '2');
    setVal('strategyEntryPctMove_regular', '5');
    setVal('strategySwingLookbackDays_entry_regular', '20');
    setVal('strategyBuyThreshold_regular', '0');
    setVal('strategySellMode_regular', 'indicator');
    setVal('strategyCustomStopLossPrice_regular', '');
    setVal('strategyCustomTakeProfitPrice_regular', '');
    setVal('strategyCustomUnderlyingStopLossPrice_regular', '');
    setVal('strategyCustomUnderlyingTakeProfitPrice_regular', '');
    setVal('strategyStopLossPct_regular', '5');
    setVal('strategyTakeProfitPct_regular', '10');
    setVal('strategyRegularTrailPct_regular', '3');
    setCheck('strategyEnablePctTrail_regular', true);
    setVal('strategyAtrStopMult_regular', '2');
    setVal('strategyAtrTpMult_regular', '4');
    setVal('strategyAtrTrailMult_regular', '3');
    setVal('strategySwingLookbackDays_exit_regular', '20');
    setVal('strategySellThreshold_regular', '0');
    setCheck('btEnableRisk_regular', false);
    const riskContentReg = document.getElementById('btRiskControlsContent_regular');
    if (riskContentReg) riskContentReg.classList.add('hidden');
    setVal('btRiskPerTrade', '50.0');
    setVal('btMaxPositionPct_regular', '100.0');
    setCheck('strategyEnableCooldown_regular', true);
    setCheck('strategyOptionsSmartReentry_regular', false);

    // Options Strategy Defaults
    setVal('strategyOptionsOptionType', 'calls_only');
    setVal('strategyOptionsExpiryDays', '3');
    setVal('strategyOptionsStrikeType', 'ATM');
    setVal('strategyOptionsAllocationMode', 'fixed');
    setVal('strategyOptionsTradeAmount', '1000');
    setVal('strategyOptionsCompoundPct', '10');
    setVal('strategyBuyMode_options', 'indicator');
    setVal('strategyCustomBuyPrice_options', '');
    setVal('strategyEntryAtrMult_options', '2');
    setVal('strategyEntryPctMove_options', '5');
    setVal('strategySwingLookbackDays_entry_options', '20');
    setVal('strategyBuyThreshold_options', '0');
    setVal('strategySellMode_options', 'indicator');
    setVal('strategyCustomStopLossPrice_options', '');
    setVal('strategyCustomTakeProfitPrice_options', '');
    setVal('strategyCustomUnderlyingStopLossPrice_options', '');
    setVal('strategyCustomUnderlyingTakeProfitPrice_options', '');
    setVal('strategyOptionsSlPct', '50');
    setVal('strategyOptionsTpPct', '100');
    setVal('strategyOptionsTrailPct', '3');
    setCheck('strategyEnablePctTrail_options', true);
    setVal('strategyAtrStopMult_options', '2');
    setVal('strategyAtrTpMult_options', '4');
    setVal('strategyAtrTrailMult_options', '3');
    setVal('strategySwingLookbackDays_exit_options', '20');
    setVal('strategySellThreshold_options', '0');
    setCheck('btEnableRisk_options', false);
    const riskContentOpt = document.getElementById('btRiskControlsContent_options');
    if (riskContentOpt) riskContentOpt.classList.add('hidden');
    setVal('btRiskPerTrade_options', '50.0');
    setVal('btMaxPositionPct_options', '100.0');
    setCheck('strategyEnableCooldown_options', true);
    setCheck('strategyOptionsSmartReentry', false);
    setCheck('strategyOptionsSignalReset', false);

    // Sync all UI states
    window.toggleBtMode(typeof currentBtMode !== 'undefined' ? currentBtMode : 'regular');
    window.updateBtRegularEntryFields?.();
    window.updateBtRegularExitFields?.();
    window.updateBtAllocationModeUI?.();
    window.updateBtOptionsAllocationModeUI?.();
    window.updateBtSmartReentryFields?.('regular');
    window.updateBtSmartReentryFields?.('options');
    window.updateBtSwingLookbackFields?.('entry');
    window.updateBtSwingLookbackFields?.('exit');
    window.updateBtOptionsEntryFields?.();
    window.updateBtOptionsExitFields?.();
    window.updateBtOptionsDirectionHint?.();
    window.updateBtDefaultCooldownLabel?.();
    window.updateSignalLabels?.('regular');
    window.updateSignalLabels?.('options');
};

// ── Backtest Chart Functions ─────────────────────────────────────────────

function _btParseDate(ds) {
    if (!ds) return null;
    let ts = ds;
    if (!ds.includes('Z') && !/[+-]\d{2}:\d{2}$/.test(ds)) {
        ts += 'Z';
    }
    // Cross-browser compatibility fix (e.g., Safari requires 'T' separator)
    ts = ts.replace(' ', 'T');
    return new Date(ts);
}

function getBtChartTimeframe() {
    const mode = currentBtMode === 'options' ? 'options' : 'regular';
    return document.getElementById(`strategyTimeframe_${mode}`)?.value
        || document.getElementById('strategyTimeframe_regular')?.value
        || '1Hour';
}

function getBtChartTimeframeSeconds(timeframe = getBtChartTimeframe()) {
    const tf = String(timeframe || '').toUpperCase();
    if (tf.includes('DAY') || tf === '1D') return 86400;
    const value = Number((tf.match(/\d+/) || ['1'])[0]);
    if (tf.includes('HOUR') || tf.endsWith('H')) return value * 3600;
    if (tf.includes('SEC') || tf.endsWith('S')) return value;
    if (tf.includes('MIN') || tf.endsWith('M')) return value * 60;
    return 300;
}

function btDisplayTime(time, timeframe = getBtChartTimeframe()) {
    const sourceTime = Number(time);
    if (!Number.isFinite(sourceTime)) return sourceTime;
    const seconds = getBtChartTimeframeSeconds(timeframe);
    return sourceTime + (seconds > 0 && seconds < 86400 ? seconds : 0);
}

function btDisplayCandle(candle, timeframe = getBtChartTimeframe()) {
    const sourceTime = Number(candle?.time);
    if (!Number.isFinite(sourceTime)) return candle;
    return {
        ...candle,
        source_time: sourceTime,
        time: btDisplayTime(sourceTime, timeframe),
    };
}

function btShouldShowDailyHighLowLines() {
    const mode = currentBtMode === 'options' ? 'options' : 'regular';
    const checkClass = mode === 'options' ? '.bt-indicator-check-options' : '.bt-indicator-check-regular';
    const selected = document.querySelector(`${checkClass}[value="Prior Day High/Low"]:checked`);
    const buyMode = document.getElementById(`strategyBuyMode_${mode}`)?.value || '';
    const sellMode = document.getElementById(`strategySellMode_${mode}`)?.value || '';
    return Boolean(selected) || ['high_low', 'hybrid_high_low'].includes(buyMode) || ['high_low', 'hybrid_high_low'].includes(sellMode);
}

function btShouldShowPivotLines() {
    const mode = currentBtMode === 'options' ? 'options' : 'regular';
    const checkClass = mode === 'options' ? '.bt-indicator-check-options' : '.bt-indicator-check-regular';
    const selected = document.querySelector(`${checkClass}[value="Pivot Points (S/R)"]:checked`);
    const buyMode = document.getElementById(`strategyBuyMode_${mode}`)?.value || '';
    const sellMode = document.getElementById(`strategySellMode_${mode}`)?.value || '';
    return Boolean(selected) || ['pivot_points', 'hybrid_pivot'].includes(buyMode) || ['pivot_points', 'hybrid_pivot'].includes(sellMode);
}

function btShouldShowVolumeProfileLines() {
    const mode = currentBtMode === 'options' ? 'options' : 'regular';
    const checkClass = mode === 'options' ? '.bt-indicator-check-options' : '.bt-indicator-check-regular';
    const selected = document.querySelector(`${checkClass}[value="Volume Profile"]:checked`);
    return Boolean(selected);
}

function btShouldShowSwingPointLines() {
    const mode = currentBtMode === 'options' ? 'options' : 'regular';
    const checkClass = mode === 'options' ? '.bt-indicator-check-options' : '.bt-indicator-check-regular';
    const selected = document.querySelector(`${checkClass}[value="Swing Points"]:checked`);
    return Boolean(selected);
}

function btLineDataFromCandles(candles, key) {
    const seen = new Set();
    return (candles || [])
        .filter(c => c && c.time && c[key] !== null && c[key] !== undefined && !Number.isNaN(Number(c[key])))
        .map(c => ({ time: Number(c.time), value: Number(c[key]) }))
        .filter(point => {
            if (seen.has(point.time)) return false;
            seen.add(point.time);
            return true;
        })
        .sort((a, b) => a.time - b.time);
}

function btSelectedIndicatorNames() {
    const mode = currentBtMode === 'options' ? 'options' : 'regular';
    const selector = mode === 'options' ? '.bt-indicator-check-options:checked:not(:disabled)' : '.bt-indicator-check-regular:checked:not(:disabled)';
    return new Set(Array.from(document.querySelectorAll(selector)).map(input => input.value));
}

function btAddIndicatorLine(candles, key, options) {
    const data = btLineDataFromCandles(candles, key);
    if (data.length <= 1 || !btChart) return null;
    const series = btChart.addLineSeries({
        priceLineVisible: false,
        lastValueVisible: false,
        crosshairMarkerVisible: false,
        ...options,
    });
    series.setData(data);
    btIndicatorOverlaySeries.push(series);
    return series;
}

function btDrawPriceIndicatorOverlays(chartCandles) {
    const enabled = btSelectedIndicatorNames();
    btIndicatorOverlaySeries = [];

    if (enabled.has('EMA Cross')) {
        btAddIndicatorLine(chartCandles, 'ema_fast', { color: '#3b82f6', lineWidth: 1.5 });
        btAddIndicatorLine(chartCandles, 'ema_slow', { color: '#ef4444', lineWidth: 1.5 });
    }
    if (enabled.has('SMA')) {
        btAddIndicatorLine(chartCandles, 'sma', { color: '#64748b', lineWidth: 2.5 });
    }
    if (enabled.has('VWAP')) {
        btAddIndicatorLine(chartCandles, 'vwap', { color: '#f59e0b', lineWidth: 2.5 });
    }
    if (enabled.has('Supertrend')) {
        const lastTrend = [...chartCandles].reverse().find(c => c.supertrend != null);
        btAddIndicatorLine(chartCandles, 'supertrend', {
            color: lastTrend?.supertrend_up ? '#10b981' : '#ef4444',
            lineWidth: 2.5,
        });
    }
    if (enabled.has('Bollinger')) {
        btAddIndicatorLine(chartCandles, 'boll_upper', { color: '#8b5cf6', lineWidth: 1.5 });
        btAddIndicatorLine(chartCandles, 'boll_middle', { color: '#64748b', lineWidth: 1, lineStyle: 2 });
        btAddIndicatorLine(chartCandles, 'boll_lower', { color: '#8b5cf6', lineWidth: 1.5 });
    }
    if (enabled.has('BotBulls2')) {
        btAddIndicatorLine(chartCandles, 'lx_smart_trail', { color: '#10b981', lineWidth: 2.5 });
        btAddIndicatorLine(chartCandles, 'lx_trend_tracer', { color: '#0ea5e9', lineWidth: 2 });
        btAddIndicatorLine(chartCandles, 'lx_rz_upper', { color: '#f59e0b', lineWidth: 1.5, lineStyle: 2 });
        btAddIndicatorLine(chartCandles, 'lx_rz_lower', { color: '#ec4899', lineWidth: 1.5, lineStyle: 2 });
    }
    if (enabled.has('BotBulls3')) {
        btAddIndicatorLine(chartCandles, 'ut_trail', { color: '#3b82f6', lineWidth: 2.5 });
    }
}

function btRightVisiblePointForData(data) {
    if (!Array.isArray(data) || data.length === 0 || !btChart) return null;
    let from = NaN;
    let to = NaN;
    try {
        const range = btChart.timeScale().getVisibleRange();
        from = Number(range?.from);
        to = Number(range?.to);
    } catch (e) {}
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

function refreshBtPriceLineLabel(key) {
    const meta = btPriceLineLabelMeta[key];
    if (!meta || !meta.series) return;
    if (btPriceLineLabels[key]) {
        try { meta.series.removePriceLine(btPriceLineLabels[key]); } catch (e) {}
        delete btPriceLineLabels[key];
    }
    const point = btRightVisiblePointForData(meta.data);
    const price = Number(point?.value);
    if (!Number.isFinite(price)) return;
    btPriceLineLabels[key] = meta.series.createPriceLine({
        price,
        color: meta.color,
        lineWidth: 1,
        lineStyle: meta.lineStyle,
        axisLabelVisible: true,
        lineVisible: false,
        title: meta.labelTitle,
    });
}

function refreshBtPriceLineLabels() {
    Object.keys(btPriceLineLabelMeta).forEach(refreshBtPriceLineLabel);
}

function btAddPriceLineLabel(key, series, data, labelTitle, color, lineStyle = 0) {
    if (!series || !labelTitle) return;
    btPriceLineLabelMeta[key] = { series, data, labelTitle, color, lineStyle };
    refreshBtPriceLineLabel(key);
}

function getBtPriorDayBufferPct() {
    const overrides = window.lastBacktestData?.indicator_overrides || {};
    const params = window.lastBacktestData?.indicator_parameters || {};
    const raw = overrides.DAILY_HIGH_LOW_BUFFER_PCT ?? params.DAILY_HIGH_LOW_BUFFER_PCT ?? window.BT_INDICATOR_DEFAULTS?.DAILY_HIGH_LOW_BUFFER_PCT ?? 0;
    const parsed = Number(raw);
    return Number.isFinite(parsed) && parsed > 0 ? parsed : 0;
}

function getBtPivotLevelCount() {
    const overrides = window.lastBacktestData?.indicator_overrides || {};
    const params = window.lastBacktestData?.indicator_parameters || {};
    const raw = overrides.PIVOT_LEVEL_COUNT ?? params.PIVOT_LEVEL_COUNT ?? window.BT_INDICATOR_DEFAULTS?.PIVOT_LEVEL_COUNT ?? 1;
    const parsed = Math.round(Number(raw));
    return Number.isFinite(parsed) ? Math.min(5, Math.max(1, parsed)) : 1;
}

function getBtSwingLevelCount() {
    const overrides = window.lastBacktestData?.indicator_overrides || {};
    const params = window.lastBacktestData?.indicator_parameters || {};
    const raw = overrides.SWING_POINTS_LEVEL_COUNT ?? params.SWING_POINTS_LEVEL_COUNT ?? window.BT_INDICATOR_DEFAULTS?.SWING_POINTS_LEVEL_COUNT ?? 1;
    const parsed = Math.round(Number(raw));
    return Number.isFinite(parsed) ? Math.min(5, Math.max(1, parsed)) : 1;
}

function applyBtPriorDayBufferToCachedCandles() {
    if (!Array.isArray(btChartCandlesRef) || !btChartCandlesRef.length) return;
    const bufferPct = getBtPriorDayBufferPct();
    btChartCandlesRef = btChartCandlesRef.map(candle => {
        const high = Number(candle?.daily_high_low_high);
        const low = Number(candle?.daily_high_low_low);
        const hasLevels = Number.isFinite(high) && Number.isFinite(low) && high > 0 && low > 0;
        return {
            ...candle,
            daily_high_low_high_trigger: hasLevels && bufferPct > 0 ? high * (1 + bufferPct / 100) : null,
            daily_high_low_low_trigger: hasLevels && bufferPct > 0 ? low * (1 - bufferPct / 100) : null,
        };
    });
}

function destroyBtChartOnly() {
    if (btChart) {
        try { btChart.remove(); } catch (e) {}
    }
    btChart = null;
    btCandleSeries = null;
    btEqSeries = null;
    btVolumeSeries = null;
    btDailyHighLineSeries = null;
    btDailyLowLineSeries = null;
    btDailyHighTriggerLineSeries = null;
    btDailyLowTriggerLineSeries = null;
    btPivotPLineSeries = null;
    btPivotR1LineSeries = null;
    btPivotS1LineSeries = null;
    btIndicatorOverlaySeries = [];
    btPriceLineLabels = {};
    btPriceLineLabelMeta = {};
    btChartMarkersRef = [];
    if (btChartResizeObserver) {
        try { btChartResizeObserver.disconnect(); } catch (e) {}
        btChartResizeObserver = null;
    }
    if (window._btKeyHandler) {
        document.removeEventListener('keydown', window._btKeyHandler);
        window._btKeyHandler = null;
    }
}

function redrawBtChartWithCurrentSettings() {
    if (!btChartCandlesRef.length || !document.getElementById('btChartSection')) return;
    applyBtPriorDayBufferToCachedCandles();
    destroyBtChartOnly();
    document.getElementById('btChartSection').classList.remove('hidden');
    _btInitChart(btChartCandlesRef, btChartTradesRef, btChartEquityCurveRef || []);
}

function _btInitChart(candles, trades, equityCurve) {
    const wrap = document.getElementById('btChartWrap');
    if (!wrap) return;
    const chartTimeframe = getBtChartTimeframe();
    const chartCandles = (candles || [])
        .map(candle => btDisplayCandle(candle, chartTimeframe))
        .filter(candle => candle && Number.isFinite(Number(candle.time)))
        .sort((a, b) => Number(a.time) - Number(b.time));

    // ── Create chart ─────────────────────────────────────────────────────
    btChart = LightweightCharts.createChart(wrap, {
        layout: {
            background: { color: '#ffffff' },
            textColor: '#334155',
        },
        grid: {
            vertLines: { color: 'rgba(168, 85, 247, 0.05)' },
            horzLines: { color: 'rgba(168, 85, 247, 0.05)' },
        },
        crosshair: { mode: LightweightCharts.CrosshairMode.Normal },
        rightPriceScale: { borderColor: 'rgba(197, 203, 206, 0.8)', minimumWidth: 75 },
        timeScale: {
            borderColor: 'rgba(197, 203, 206, 0.8)',
            timeVisible: true,
            secondsVisible: false,
            tickMarkFormatter: (time, tickMarkType) => {
                const d = new Date(Number(time) * 1000);
                if (tickMarkType <= 2) {
                    return d.toLocaleDateString('en-US', { timeZone: 'America/Chicago', month: 'short', day: 'numeric' });
                }
                return d.toLocaleTimeString('en-US', { timeZone: 'America/Chicago', hour: '2-digit', minute: '2-digit', hour12: false });
            },
        },
        localization: {
            timeFormatter: time => new Date(Number(time) * 1000).toLocaleString('en-US', {
                timeZone: 'America/Chicago',
                month: 'short',
                day: 'numeric',
                hour: '2-digit',
                minute: '2-digit',
                hour12: false,
            }),
        },
        width: wrap.clientWidth,
        height: wrap.clientHeight,
    });
    try {
        btChart.timeScale().subscribeVisibleTimeRangeChange(() => refreshBtPriceLineLabels());
    } catch (e) {}

    // ── Candlestick series ───────────────────────────────────────────────
    btCandleSeries = btChart.addCandlestickSeries({
        upColor: '#10b981',
        downColor: '#ef4444',
        borderVisible: false,
        wickUpColor: '#10b981',
        wickDownColor: '#ef4444',
    });
    btCandleSeries.setData(chartCandles);

    // ── Volume series ────────────────────────────────────────────────────
    btVolumeSeries = btChart.addHistogramSeries({
        color: '#26a69a',
        priceFormat: { type: 'volume' },
        priceScaleId: '', // set as an overlay by default
    });
    btChart.priceScale('').applyOptions({
        scaleMargins: { top: 0.8, bottom: 0 },
    });
    
    const volumeData = chartCandles.map(c => ({
        time: c.time,
        value: c.volume,
        color: c.close >= c.open ? 'rgba(16, 185, 129, 0.4)' : 'rgba(239, 68, 68, 0.4)'
    }));
    btVolumeSeries.setData(volumeData);

    btDrawPriceIndicatorOverlays(chartCandles);

    if (btShouldShowDailyHighLowLines()) {
        const dailyHighData = btLineDataFromCandles(chartCandles, 'daily_high_low_high');
        const dailyLowData = btLineDataFromCandles(chartCandles, 'daily_high_low_low');
        const dailyHighTriggerData = btLineDataFromCandles(chartCandles, 'daily_high_low_high_trigger');
        const dailyLowTriggerData = btLineDataFromCandles(chartCandles, 'daily_high_low_low_trigger');
        if (dailyHighData.length > 1) {
            btDailyHighLineSeries = btChart.addLineSeries({
                color: '#d97706',
                lineWidth: 3,
                lineStyle: 1,
                priceLineVisible: false,
                lastValueVisible: false,
                crosshairMarkerVisible: false,
            });
            btDailyHighLineSeries.setData(dailyHighData);
            btAddPriceLineLabel('daily_high_low_high', btDailyHighLineSeries, dailyHighData, 'PDH', '#d97706', 1);
        }
        if (dailyLowData.length > 1) {
            btDailyLowLineSeries = btChart.addLineSeries({
                color: '#0891b2',
                lineWidth: 3,
                lineStyle: 1,
                priceLineVisible: false,
                lastValueVisible: false,
                crosshairMarkerVisible: false,
            });
            btDailyLowLineSeries.setData(dailyLowData);
            btAddPriceLineLabel('daily_high_low_low', btDailyLowLineSeries, dailyLowData, 'PDL', '#0891b2', 1);
        }
        if (dailyHighTriggerData.length > 1) {
            btDailyHighTriggerLineSeries = btChart.addLineSeries({
                color: 'rgba(217, 119, 6, 0.75)',
                lineWidth: 2,
                lineStyle: 1,
                priceLineVisible: false,
                lastValueVisible: false,
                crosshairMarkerVisible: false,
            });
            btDailyHighTriggerLineSeries.setData(dailyHighTriggerData);
        }
        if (dailyLowTriggerData.length > 1) {
            btDailyLowTriggerLineSeries = btChart.addLineSeries({
                color: 'rgba(8, 145, 178, 0.75)',
                lineWidth: 2,
                lineStyle: 1,
                priceLineVisible: false,
                lastValueVisible: false,
                crosshairMarkerVisible: false,
            });
            btDailyLowTriggerLineSeries.setData(dailyLowTriggerData);
        }
    }

    if (btShouldShowPivotLines()) {
        const pivotLevelCount = getBtPivotLevelCount();
        const pivotData = btLineDataFromCandles(chartCandles, 'pivot_p');
        if (pivotData.length > 1) {
            btPivotPLineSeries = btChart.addLineSeries({
                color: '#1f2937',
                lineWidth: 3,
                lineStyle: 2,
                priceLineVisible: false,
                lastValueVisible: false,
                crosshairMarkerVisible: false,
            });
            btPivotPLineSeries.setData(pivotData);
            btAddPriceLineLabel('pivot_p', btPivotPLineSeries, pivotData, 'P', '#1f2937', 2);
        }
        for (let level = 1; level <= pivotLevelCount; level += 1) {
            const rData = btLineDataFromCandles(chartCandles, `pivot_r${level}`);
            const sData = btLineDataFromCandles(chartCandles, `pivot_s${level}`);
            if (rData.length > 1) {
                const rColor = level === 1 ? '#dc2626' : 'rgba(220, 38, 38, 0.78)';
                const rStyle = level === 1 ? 1 : 2;
                const rSeries = btChart.addLineSeries({
                    color: rColor,
                    lineWidth: 2,
                    lineStyle: rStyle,
                    priceLineVisible: false,
                    lastValueVisible: false,
                    crosshairMarkerVisible: false,
                });
                rSeries.setData(rData);
                btAddPriceLineLabel(`pivot_r${level}`, rSeries, rData, `R${level}`, rColor, rStyle);
            }
            if (sData.length > 1) {
                const sColor = level === 1 ? '#059669' : 'rgba(5, 150, 105, 0.78)';
                const sStyle = level === 1 ? 1 : 2;
                const sSeries = btChart.addLineSeries({
                    color: sColor,
                    lineWidth: 2,
                    lineStyle: sStyle,
                    priceLineVisible: false,
                    lastValueVisible: false,
                    crosshairMarkerVisible: false,
                });
                sSeries.setData(sData);
                btAddPriceLineLabel(`pivot_s${level}`, sSeries, sData, `S${level}`, sColor, sStyle);
            }
        }
    }

    if (btShouldShowVolumeProfileLines()) {
        const pocData = btLineDataFromCandles(chartCandles, 'volume_profile_poc');
        const vahData = btLineDataFromCandles(chartCandles, 'volume_profile_vah');
        const valData = btLineDataFromCandles(chartCandles, 'volume_profile_val');
        if (pocData.length > 1) {
            const pocSeries = btChart.addLineSeries({
                color: '#7c2d12',
                lineWidth: 3,
                lineStyle: 0,
                priceLineVisible: false,
                lastValueVisible: false,
                crosshairMarkerVisible: false,
            });
            pocSeries.setData(pocData);
            btAddPriceLineLabel('volume_profile_poc', pocSeries, pocData, 'POC', '#7c2d12', 0);
        }
        if (vahData.length > 1) {
            const vahSeries = btChart.addLineSeries({
                color: '#ea580c',
                lineWidth: 2,
                lineStyle: 2,
                priceLineVisible: false,
                lastValueVisible: false,
                crosshairMarkerVisible: false,
            });
            vahSeries.setData(vahData);
            btAddPriceLineLabel('volume_profile_vah', vahSeries, vahData, 'VAH', '#ea580c', 2);
        }
        if (valData.length > 1) {
            const valSeries = btChart.addLineSeries({
                color: '#0f766e',
                lineWidth: 2,
                lineStyle: 2,
                priceLineVisible: false,
                lastValueVisible: false,
                crosshairMarkerVisible: false,
            });
            valSeries.setData(valData);
            btAddPriceLineLabel('volume_profile_val', valSeries, valData, 'VAL', '#0f766e', 2);
        }
    }

    if (btShouldShowSwingPointLines()) {
        const swingLevelCount = getBtSwingLevelCount();
        for (let level = 1; level <= swingLevelCount; level += 1) {
            const sData = btLineDataFromCandles(chartCandles, `swing_support_${level}`);
            const rData = btLineDataFromCandles(chartCandles, `swing_resistance_${level}`);
            if (sData.length > 1) {
                const sColor = level === 1 ? '#16a34a' : 'rgba(22, 163, 74, 0.72)';
                const sStyle = level === 1 ? 1 : 2;
                const sSeries = btChart.addLineSeries({
                    color: sColor,
                    lineWidth: level === 1 ? 3 : 2,
                    lineStyle: sStyle,
                    priceLineVisible: false,
                    lastValueVisible: false,
                    crosshairMarkerVisible: false,
                });
                sSeries.setData(sData);
                btAddPriceLineLabel(`swing_support_${level}`, sSeries, sData, `SW S${level}`, sColor, sStyle);
            }
            if (rData.length > 1) {
                const rColor = level === 1 ? '#dc2626' : 'rgba(220, 38, 38, 0.72)';
                const rStyle = level === 1 ? 1 : 2;
                const rSeries = btChart.addLineSeries({
                    color: rColor,
                    lineWidth: level === 1 ? 3 : 2,
                    lineStyle: rStyle,
                    priceLineVisible: false,
                    lastValueVisible: false,
                    crosshairMarkerVisible: false,
                });
                rSeries.setData(rData);
                btAddPriceLineLabel(`swing_resistance_${level}`, rSeries, rData, `SW R${level}`, rColor, rStyle);
            }
        }
    }

    // ── Equity Curve overlay (purple line, bottom 20% of chart) ──────────
    if (equityCurve && equityCurve.length > 0) {
        const equityData = equityCurve.map(e => {
            const dt = _btParseDate(e.time);
            return dt ? { time: btDisplayTime(Math.floor(dt.getTime() / 1000), chartTimeframe), value: e.equity } : null;
        }).filter(Boolean).sort((a, b) => a.time - b.time);

        // Deduplicate (same timestamp can appear multiple times)
        const seen = new Set();
        const uniqueEquity = equityData.filter(e => {
            if (seen.has(e.time)) return false;
            seen.add(e.time);
            return true;
        });

        if (uniqueEquity.length > 1) {
            btEqSeries = btChart.addLineSeries({
                priceScaleId: 'equity',
                color: 'rgba(139, 92, 246, 0.7)',
                lineWidth: 2,
                priceLineVisible: false,
                lastValueVisible: false,
                crosshairMarkerVisible: false,
            });
            btChart.priceScale('equity').applyOptions({
                scaleMargins: { top: 0.8, bottom: 0 },
            });
            btEqSeries.setData(uniqueEquity);
        }
    }

    // ── Build buy/sell markers (clean P/L % style) ───────────────────────
    const markers = [];
    const sortedTrades = trades; // already oldest-first

    sortedTrades.forEach((trade, idx) => {
        const entryDt = _btParseDate(trade.entry_time);
        const exitDt = _btParseDate(trade.exit_time);
        const entryTs = entryDt ? btDisplayTime(Math.floor(entryDt.getTime() / 1000), chartTimeframe) : 0;
        const exitTs = exitDt ? btDisplayTime(Math.floor(exitDt.getTime() / 1000), chartTimeframe) : 0;
        const isWin = trade.pl >= 0;
        const plLabel = `${isWin ? '+' : ''}${trade.pl_pct.toFixed(1)}%`;

        const isOption = trade.option_type !== undefined;

        // Buy marker — clean "B" label
        markers.push({
            time: entryTs,
            position: 'belowBar',
            color: '#10b981',
            shape: 'arrowUp',
            text: isOption ? `${trade.option_type} ${trade.strike}` : 'B',
            size: 2,
        });

        // Sell marker — shows P/L %
        markers.push({
            time: exitTs,
            position: 'aboveBar',
            color: isWin ? '#10b981' : '#ef4444',
            shape: 'arrowDown',
            text: plLabel,
            size: 2,
        });
    });

    const enabledChartIndicators = btSelectedIndicatorNames();
    for (let i = 1; i < chartCandles.length; i += 1) {
        const prev = chartCandles[i - 1];
        const curr = chartCandles[i];
        if (enabledChartIndicators.has('Supertrend') && curr.supertrend != null && prev.supertrend != null) {
            if (curr.supertrend_up && !prev.supertrend_up) {
                markers.push({ time: Number(curr.time), position: 'belowBar', color: '#10b981', shape: 'arrowUp', text: 'ST Buy', size: 1 });
            } else if (!curr.supertrend_up && prev.supertrend_up) {
                markers.push({ time: Number(curr.time), position: 'aboveBar', color: '#ef4444', shape: 'arrowDown', text: 'ST Sell', size: 1 });
            }
        }
        if (enabledChartIndicators.has('BotBulls1')) {
            if (curr.ba1_buy) markers.push({ time: Number(curr.time), position: 'belowBar', color: '#06b6d4', shape: 'arrowUp', text: 'BB1 BUY', size: 1 });
            if (curr.ba1_sell) markers.push({ time: Number(curr.time), position: 'aboveBar', color: '#f43f5e', shape: 'arrowDown', text: 'BB1 SELL', size: 1 });
        }
        if (enabledChartIndicators.has('BotBulls2')) {
            if (curr.ba2_buy) markers.push({ time: Number(curr.time), position: 'belowBar', color: '#10b981', shape: 'arrowUp', text: 'BB2 BUY', size: 1 });
            if (curr.ba2_sell) markers.push({ time: Number(curr.time), position: 'aboveBar', color: '#ef4444', shape: 'arrowDown', text: 'BB2 SELL', size: 1 });
        }
        if (enabledChartIndicators.has('BotBulls3')) {
            if (curr.ba3_buy) markers.push({ time: Number(curr.time), position: 'belowBar', color: '#0ea5e9', shape: 'arrowUp', text: 'BB3 BUY', size: 1.2 });
            if (curr.ba3_sell) markers.push({ time: Number(curr.time), position: 'aboveBar', color: '#f43f5e', shape: 'arrowDown', text: 'BB3 SELL', size: 1.2 });
        }
    }

    // Candle Patterns Markers on backtest chart (last candle only, no noise across full chart)
    const c = chartCandles[chartCandles.length - 1];
    if (c?.cdl_name && c.cdl_name !== 'Neutral') {
        if (c.cdl_bullish) {
            markers.push({
                time: Number(c.time),
                position: 'belowBar',
                color: '#10b981',
                shape: 'arrowUp',
                text: c.cdl_name,
                size: 1.1
            });
        } else if (c.cdl_bearish) {
            markers.push({
                time: Number(c.time),
                position: 'aboveBar',
                color: '#ef4444',
                shape: 'arrowDown',
                text: c.cdl_name,
                size: 1.1
            });
        }
    }

    // Deduplicate by time (LightweightCharts requires unique times per marker array)
    const markerMap = {};
    markers.forEach(m => {
        const key = `${m.time}_${m.position}`;
        if (!markerMap[key]) {
            markerMap[key] = { ...m };
        } else {
            markerMap[key].text += ` | ${m.text}`;
        }
    });
    btChartMarkersRef = Object.values(markerMap).sort((a, b) => a.time - b.time);
    btCandleSeries.setMarkers(btChartMarkersRef);
    
    // Set initial toggle states
    btToggleChartLayers();

    // ── Crosshair tooltip (shows OHLCV + position status on hover) ────────
    const tooltip = document.getElementById('btChartTooltip');
    btChart.subscribeCrosshairMove(param => {
        if (!param || !param.time || !param.seriesData || !tooltip) {
            if (tooltip) tooltip.classList.add('hidden');
            return;
        }
        const bar = param.seriesData.get(btCandleSeries);
        if (!bar) { tooltip.classList.add('hidden'); return; }

        // Check if bot was in a position at this bar's time
        const barTime = param.time;
        const activePos = sortedTrades.find(t => {
            const entryDt = _btParseDate(t.entry_time);
            const exitDt = _btParseDate(t.exit_time);
            const eTs = entryDt ? btDisplayTime(Math.floor(entryDt.getTime() / 1000), chartTimeframe) : 0;
            const xTs = exitDt ? btDisplayTime(Math.floor(exitDt.getTime() / 1000), chartTimeframe) : 0;
            return barTime >= eTs && barTime <= xTs;
        });

        const isOption = activePos && activePos.option_type !== undefined;
        const posLine = activePos
            ? (isOption
                ? `<div class="mt-1 pt-1 border-t border-indigo-50 text-[0.55rem]">
                     <span class="text-emerald-600 font-black">IN ${activePos.option_type} ${activePos.strike}</span>
                     <span class="text-slate-400 ml-1">Premium: $${formatAssetPrice(activePos.entry_price, isCrypto(activePos.symbol || window.activeTicker))}</span>
                     <span class="text-slate-400 ml-1">Stock Entry: $${formatAssetPrice(activePos.entry_underlying, isCrypto(activePos.symbol || window.activeTicker))}</span>
                   </div>`
                : `<div class="mt-1 pt-1 border-t border-indigo-50 text-[0.55rem]">
                     <span class="text-emerald-600 font-black">IN POSITION</span>
                     <span class="text-slate-400 ml-1">@ $${activePos.entry_price}</span>
                     <span class="ml-1 ${((bar.close - activePos.entry_price) / activePos.entry_price * 100) >= 0 ? 'text-emerald-500' : 'text-rose-500'}">
                       (${((bar.close - activePos.entry_price) / activePos.entry_price * 100).toFixed(2)}%)
                     </span>
                   </div>`)
            : '';

        let eqLine = '';
        if (btEqSeries) {
            const eqData = param.seriesData.get(btEqSeries);
            if (eqData && eqData.value !== undefined) {
                eqLine = `<div class="mt-1 pt-1 border-t border-indigo-50 text-[0.55rem] flex justify-between">
                            <span class="text-violet-500 font-black">EQUITY</span>
                            <span class="font-bold text-violet-600">$${eqData.value.toLocaleString('en-US', {minimumFractionDigits: 2, maximumFractionDigits: 2})}</span>
                          </div>`;
            }
        }

        tooltip.innerHTML = `
            <div class="text-[0.55rem] text-slate-400 mb-1">${new Date(param.time * 1000).toLocaleString('en-US', { timeZone: 'America/Chicago', month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit', hour12: false })}</div>
            <div class="grid grid-cols-2 gap-x-3 gap-y-0.5">
                <span class="text-slate-400">O</span><span class="font-bold">$${bar.open < 10 || String(bar.open).split(".")[1]?.length > 2 ? new Intl.NumberFormat("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 8, useGrouping: false }).format(bar.open) : bar.open.toFixed(2)}</span>
                <span class="text-slate-400">H</span><span class="font-bold text-emerald-600">$${bar.high < 10 || String(bar.high).split(".")[1]?.length > 2 ? new Intl.NumberFormat("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 8, useGrouping: false }).format(bar.high) : bar.high.toFixed(2)}</span>
                <span class="text-slate-400">L</span><span class="font-bold text-rose-500">$${bar.low < 10 || String(bar.low).split(".")[1]?.length > 2 ? new Intl.NumberFormat("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 8, useGrouping: false }).format(bar.low) : bar.low.toFixed(2)}</span>
                <span class="text-slate-400">C</span><span class="font-bold">$${bar.close < 10 || String(bar.close).split(".")[1]?.length > 2 ? new Intl.NumberFormat("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 8, useGrouping: false }).format(bar.close) : bar.close.toFixed(2)}</span>
            </div>
            ${posLine}
            ${eqLine}
        `;
        tooltip.classList.remove('hidden');
    });

    // ── Update trade count badge ──────────────────────────────────────────
    const countEl = document.getElementById('btChartTradeCount');
    if (countEl) countEl.textContent = `${trades.length} trade${trades.length !== 1 ? 's' : ''} plotted`;

    // ── Responsive resize ─────────────────────────────────────────────────
    if (btChartResizeObserver) {
        try { btChartResizeObserver.disconnect(); } catch (e) {}
    }
    btChartResizeObserver = new ResizeObserver(entries => {
        if (!btChart || !entries.length) return;
        const { width, height } = entries[0].contentRect;
        btChart.applyOptions({ width, height });
    });
    btChartResizeObserver.observe(wrap);

    // ── Keyboard navigation (arrow keys when modal is open) ───────────────
    window._btKeyHandler = (e) => {
        if (!btChart) return;
        if (e.key === 'ArrowLeft') btNavigateTrade(-1);
        else if (e.key === 'ArrowRight') btNavigateTrade(1);
    };
    document.addEventListener('keydown', window._btKeyHandler);

    // ── Initial view: fit all candles ─────────────────────────────────────
    if (btChartTradesRef.length > 0) {
        btChartTradeIdx = 0;
        _btScrollToTrade(0);
    } else {
        btChart.timeScale().applyOptions({ rightOffset: 5, barSpacing: 10 });
        btChart.timeScale().scrollToPosition(0, false);
    }

    // ── Update navigator label ────────────────────────────────────────────
    _btUpdateNavLabel();
}

// ─── Chart Layer Toggles ─────────────────────────────────────────────────
window.btToggleChartLayers = function() {
    if (!btChart) return;
    
    const volBox = document.getElementById('btToggleVolume');
    const eqBox = document.getElementById('btToggleEquity');
    const markBox = document.getElementById('btToggleMarkers');

    const showVol = volBox ? volBox.checked : true;
    const showEq = eqBox ? eqBox.checked : true;
    const showMarkers = markBox ? markBox.checked : true;

    if (btVolumeSeries) {
        btVolumeSeries.applyOptions({ visible: showVol });
    }
    if (btEqSeries) {
        btEqSeries.applyOptions({ visible: showEq });
    }
    if (btCandleSeries) {
        btCandleSeries.setMarkers(showMarkers ? btChartMarkersRef : []);
    }
};

// ─── Trade Navigator ─────────────────────────────────────────────────────

function btNavigateTrade(dir) {
    if (!btChart || btChartTradesRef.length === 0) return;
    const total = btChartTradesRef.length;
    btChartTradeIdx = Math.max(0, Math.min(total - 1, btChartTradeIdx + dir));
    _btScrollToTrade(btChartTradeIdx);
    _btUpdateNavLabel();
    _btSyncTradeRow(btChartTradeIdx);
}

function btFitAll() {
    if (!btChart) return;
    btChart.timeScale().applyOptions({ rightOffset: 5, barSpacing: 10 });
    btChart.timeScale().scrollToPosition(0, false);
    
    btChartTradeIdx = -1;
    const lbl = document.getElementById('btChartTradeLabel');
    if (lbl) lbl.textContent = 'All trades';
}

function _btScrollToTrade(idx) {
    if (!btChart || !btChartTradesRef[idx]) return;
    const trade = btChartTradesRef[idx];

    const entryDt = _btParseDate(trade.entry_time);
    const entryTs = entryDt ? Math.floor(entryDt.getTime() / 1000) : 0;

    let entryIdx = btChartCandlesRef.findIndex(c => c.time >= entryTs);
    if (entryIdx === -1) entryIdx = btChartCandlesRef.length - 1;

    const wrap = document.getElementById('btChartWrap');
    const containerWidth = wrap ? wrap.clientWidth : 800;
    const visibleCandles = Math.floor(containerWidth / 12);

    const paddingBefore = Math.floor(visibleCandles * 0.3);
    const paddingAfter = visibleCandles - paddingBefore;

    btChart.timeScale().setVisibleLogicalRange({
        from: entryIdx - paddingBefore,
        to: entryIdx + paddingAfter
    });
}

function _btUpdateNavLabel() {
    const lbl = document.getElementById('btChartTradeLabel');
    if (!lbl) return;
    const total = btChartTradesRef.length;
    if (total === 0) { lbl.textContent = '—'; return; }
    if (btChartTradeIdx === -1) { lbl.textContent = 'All trades'; return; }
    lbl.textContent = `Trade ${btChartTradeIdx + 1} / ${total}`;
}

function _btSyncTradeRow(idx) {
    const targetPage = Math.floor(idx / btPageSize) + 1;
    if (targetPage !== btPage) {
        btPage = targetPage;
        renderBtTradesPage();
    }
    const rowInPage = idx % btPageSize;
    const rows = document.querySelectorAll('#btTradeLog tr');
    rows.forEach(r => r.classList.remove('ring-2', 'ring-indigo-400', 'ring-inset'));
    if (rows[rowInPage]) {
        rows[rowInPage].classList.add('ring-2', 'ring-indigo-400', 'ring-inset');
        rows[rowInPage].scrollIntoView({ behavior: 'smooth', block: 'nearest' });
    }
}

// ─── Trade Card Slider (Removed) ──────────────────────────────────────────

// ─── Chart → Table Sync: clicking trade row scrolls chart to that trade ──
function _btWireTradeRowClicks() {
    const rows = document.querySelectorAll('#btTradeLog tr');
    rows.forEach((row, rowInPage) => {
        row.style.cursor = 'pointer';
        row.addEventListener('click', () => {
            const globalIdx = (btPage - 1) * btPageSize + rowInPage;
            if (globalIdx >= btChartTradesRef.length) return;
            btChartTradeIdx = globalIdx;
            _btScrollToTrade(btChartTradeIdx);
            _btUpdateNavLabel();
            _btSyncTradeRow(btChartTradeIdx);
        });
    });
}
// ─────────────────────────────────────────────────────────────────────────

function syncBacktestSliderRange() {
    // Called when indicators are toggled — update the labels for both B and S
    updateSignalLabels(typeof currentBtMode !== 'undefined' ? currentBtMode : 'regular');
}

function updateSignalLabels(mode) {
    mode = mode || 'regular';
    const containerId = mode === 'regular' ? 'indicatorChecksContainer_bt_regular' : 'indicatorChecksContainer_bt_options';
    const container = document.getElementById(containerId);
    if (!container) return;
    
    const checkClass = mode === 'regular' ? '.bt-indicator-check-regular' : '.bt-indicator-check-options';
    const checkedCount = container.querySelectorAll(`${checkClass}:checked:not(:disabled)`).length;

    const buyInput = document.getElementById(`strategyBuyThreshold_${mode}`);
    const sellInput = document.getElementById(`strategySellThreshold_${mode}`);
    const buyLabel = document.getElementById(`strategyBuyLabel_${mode}`);
    const sellLabel = document.getElementById(`strategySellLabel_${mode}`);

    if (!buyInput || !sellInput || !buyLabel || !sellLabel) return;

    const indicatorModes = ['indicator', 'hybrid', 'hybrid_atr', 'hybrid_pct', 'hybrid_pivot', 'hybrid_high_low', 'hybrid_swing'];
    const buyModeSelect = document.getElementById(`strategyBuyMode_${mode}`)?.value || 'indicator';
    const sellModeSelect = document.getElementById(`strategySellMode_${mode}`)?.value || (mode === 'options' ? 'hybrid' : 'indicator');
    const buyNeedsInd = indicatorModes.includes(buyModeSelect);
    const sellNeedsInd = indicatorModes.includes(sellModeSelect);

    const buyMin = (checkedCount > 0 && buyNeedsInd) ? 1 : 0;
    const sellMin = (checkedCount > 0 && sellNeedsInd) ? 1 : 0;

    if (checkedCount === 0) {
        buyInput.min = 0;
        buyInput.max = 0;
        buyInput.value = 0;
        sellInput.min = 0;
        sellInput.max = 0;
        sellInput.value = 0;
        buyLabel.textContent = buyNeedsInd ? "Select Indicators" : "0 of 0 signals (Not Required)";
        buyLabel.className = buyNeedsInd
            ? "text-[0.6rem] font-black px-4 py-1.5 rounded-full bg-purple-100 text-purple-500 shadow-sm uppercase tracking-wider transition-all duration-300"
            : "text-[0.6rem] font-black px-4 py-1.5 rounded-full bg-slate-100 text-slate-600 shadow-sm uppercase tracking-wider transition-all duration-300";
        sellLabel.textContent = sellNeedsInd ? "Select Indicators" : "0 of 0 signals (Not Required)";
        sellLabel.className = sellNeedsInd
            ? "text-[0.6rem] font-black px-4 py-1.5 rounded-full bg-purple-100 text-purple-500 shadow-sm uppercase tracking-wider transition-all duration-300"
            : "text-[0.6rem] font-black px-4 py-1.5 rounded-full bg-slate-100 text-slate-600 shadow-sm uppercase tracking-wider transition-all duration-300";
        if (mode === 'options') syncBtOptionsPutEntryThresholdLabel();
        return;
    }

    buyInput.min = buyMin;
    buyInput.max = checkedCount;
    sellInput.min = sellMin;
    sellInput.max = checkedCount;

    // Respect user threshold selection with correct minimum when indicators checked
    let buyVal = parseInt(buyInput.value, 10);
    if (!Number.isFinite(buyVal) || (buyNeedsInd && buyVal <= 0)) buyVal = buyMin;
    buyVal = Math.max(buyMin, Math.min(buyVal, checkedCount));
    buyInput.value = buyVal;

    let sellVal = parseInt(sellInput.value, 10);
    if (!Number.isFinite(sellVal) || (sellNeedsInd && sellVal <= 0)) sellVal = sellMin;
    sellVal = Math.max(sellMin, Math.min(sellVal, checkedCount));
    sellInput.value = sellVal;

    // Buy label
    if (!buyNeedsInd && buyVal === 0) {
        buyLabel.textContent = `0 of ${checkedCount} signals (Not Required)`;
        buyLabel.className = "text-[0.6rem] font-black px-4 py-1.5 rounded-full bg-slate-100 text-slate-600 shadow-sm uppercase tracking-wider transition-all duration-300";
    } else {
        const buyPct = checkedCount > 0 ? Math.round((buyVal / checkedCount) * 100) : 0;
        let buyModeStr = "Balanced";
        let buyColor = "bg-emerald-100 text-emerald-600";
        if (buyPct <= 34) { buyModeStr = "Aggressive"; buyColor = "bg-amber-100 text-amber-600"; }
        else if (buyPct >= 75) { buyModeStr = "Quality"; buyColor = "bg-emerald-500 text-white"; }
        if (buyVal === checkedCount && checkedCount > 1) { buyModeStr = "Ultra-Quality"; buyColor = "bg-emerald-600 text-white"; }
        buyLabel.textContent = `${buyModeStr} — ${buyVal} of ${checkedCount} signals`;
        buyLabel.className = `text-[0.6rem] font-black px-4 py-1.5 rounded-full ${buyColor} shadow-sm uppercase tracking-wider transition-all duration-300`;
    }

    // Sell label
    if (!sellNeedsInd && sellVal === 0) {
        sellLabel.textContent = `0 of ${checkedCount} signals (Not Required)`;
        sellLabel.className = "text-[0.6rem] font-black px-4 py-1.5 rounded-full bg-slate-100 text-slate-600 shadow-sm uppercase tracking-wider transition-all duration-300";
    } else {
        const sellPct = checkedCount > 0 ? Math.round((sellVal / checkedCount) * 100) : 0;
        let sellModeStr = "Balanced";
        let sellColor = "bg-rose-100 text-rose-600";
        if (sellPct <= 34) { sellModeStr = "Quick Exit"; sellColor = "bg-amber-100 text-amber-600"; }
        else if (sellPct >= 75) { sellModeStr = "Patient"; sellColor = "bg-rose-500 text-white"; }
        if (sellVal === checkedCount && checkedCount > 1) { sellModeStr = "Ultra-Patient"; sellColor = "bg-rose-600 text-white"; }
        sellLabel.textContent = `${sellModeStr} — ${sellVal} of ${checkedCount} signals`;
        sellLabel.className = `text-[0.6rem] font-black px-4 py-1.5 rounded-full ${sellColor} shadow-sm uppercase tracking-wider transition-all duration-300`;
    }
    if (mode === 'options') syncBtOptionsPutEntryThresholdLabel();
    updateBacktestBotBulls4CachedBadges(mode);
}

function adjustSignalThreshold(mode, type, delta) {
    const containerId = mode === 'regular' ? 'indicatorChecksContainer_bt_regular' : 'indicatorChecksContainer_bt_options';
    const checkClass = mode === 'regular' ? '.bt-indicator-check-regular' : '.bt-indicator-check-options';
    const checkedCount = document.getElementById(containerId).querySelectorAll(`${checkClass}:checked:not(:disabled)`).length;
    
    if (checkedCount === 0) return;

    const input = type === 'buy' ? document.getElementById(`strategyBuyThreshold_${mode}`) : document.getElementById(`strategySellThreshold_${mode}`);
    const indicatorModes = ['indicator', 'hybrid', 'hybrid_atr', 'hybrid_pct', 'hybrid_pivot', 'hybrid_high_low', 'hybrid_swing'];
    const modeSelect = type === 'buy'
        ? (document.getElementById(`strategyBuyMode_${mode}`)?.value || 'indicator')
        : (document.getElementById(`strategySellMode_${mode}`)?.value || (mode === 'options' ? 'hybrid' : 'indicator'));
    const minVal = indicatorModes.includes(modeSelect) ? 1 : 0;

    let val = parseInt(input.value) + delta;
    val = Math.max(minVal, Math.min(val, checkedCount));
    input.value = val;
    updateSignalLabels(mode);
}

// Keep legacy function name referenced by HTML oninput on the old slider (no-op safety)
function updateBtAggressiveness(val) {
    updateSignalLabels();
}

function formatDate(ds) {
    const date = _btParseDate(ds);
    if (!date) return "---";
    // Force Central Time (Chicago)
    const options = { 
        timeZone: 'America/Chicago',
        year: 'numeric',
        month: 'short', 
        day: 'numeric',
        hour: '2-digit', 
        minute: '2-digit', 
        hour12: false 
    };
    return date.toLocaleString('en-US', options);
}

async function bulkDownloadTicker() {
    const ticker = document.getElementById('btTicker').value.toUpperCase();
    const btn = event.currentTarget;
    const originalHtml = btn.innerHTML;

    btn.disabled = true;
    btn.innerHTML = `<svg class="animate-spin h-5 w-5" viewBox="0 0 24 24"><circle class="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" stroke-width="4"></circle><path class="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4zm2 5.291A7.962 7.962 0 014 12H0c0 3.042 1.135 5.824 3 7.938l3-2.647z"></path></svg>`;

    try {
        const headers = await getAuthHeaders();
        const response = await fetch(`${window.BACKTEST_API_BASE}/api/download_all`, {
            method: 'POST',
            headers: headers,
            body: JSON.stringify({ ticker })
        });
        const data = await response.json();
        alert(data.message || data.status || "History updated.");
    } catch (err) {
        alert("Download failed.");
    } finally {
        btn.disabled = false;
        btn.innerHTML = originalHtml;
    }
}

// ──────────────────────────────────────────────
// Indicator Settings Logic
// ──────────────────────────────────────────────

window.BT_INDICATOR_TOOLTIPS = window.BT_INDICATOR_TOOLTIPS || {
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
    "Strategy Confidence": "Strategy Confidence combines multiple indicator signals into a single probability score. Purpose: Measures the overall conviction of a bullish or bearish trend.",
    "BotBulls1": "WaveTrend momentum oscillator + Money Flow analysis. Detects reversals when momentum and institutional volume flow align at oversold/overbought extremes.",
    "BotBulls2": "Adaptive ATR trailing stop with trend-smoothed EMA and reversal zone detection. Fires on trend flips confirmed by multi-condition confluence scoring (1-4 strength rating).",
    "BotBulls3": "Heikin-Ashi noise filter + ATR-based trailing stop. Generates buy/sell alerts only on confirmed momentum flips — removing false signals from choppy markets.",
    "Prior Day High/Low": "Uses the previous completed trading session's high and low as breakout/breakdown levels. Buffer is entered as %, with dotted trigger lines when non-zero.",
    "Pivot Points (S/R)": "Classic prior-session floor pivot levels. Draws up to 5 support/resistance ladders; bullish on support/pivot reclaim, bearish on resistance rejection or pivot breakdown.",
    "Volume Profile": "Prior-session volume-at-price profile using POC, VAH, and VAL. Purpose: Identifies institutional acceptance/rejection zones where the most volume traded.",
    "Swing Points": "Rolling swing support/resistance clusters from recent local highs/lows. Purpose: Finds structural bounce, rejection, reclaim, and breakdown zones."
};

window.BT_INDICATOR_CONFIG_MAP = window.BT_INDICATOR_CONFIG_MAP || {
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
    'BotBulls1': ['BOTBULLS1_WT_CHANNEL', 'BOTBULLS1_WT_AVERAGE', 'BOTBULLS1_MFI_CONFIRM'],
    'BotBulls2': ['BOTBULLS2_ATR_MULT', 'BOTBULLS2_TREND_TRACER_PERIOD', 'BOTBULLS2_REVERSAL_ZONE_PERIOD'],
    'BotBulls3': ['BOTBULLS3_ATR_PERIOD', 'BOTBULLS3_ATR_MULT']
};

// Industry Defaults for indicators based on config.py
window.BT_INDICATOR_DEFAULTS = window.BT_INDICATOR_DEFAULTS || {
    'BOTBULLS1_WT_CHANNEL': 10,
    'BOTBULLS1_WT_AVERAGE': 21,
    'BOTBULLS1_MFI_CONFIRM': 30,
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

var btCurrentEditingIndicator = null;

function openIndicatorSettings(indicatorName) {
    btCurrentEditingIndicator = indicatorName;
    const configKeys = window.BT_INDICATOR_CONFIG_MAP[indicatorName] || [];
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

    const currentParams = window.lastBacktestData?.indicator_parameters || {};
    const overrides = window.lastBacktestData?.indicator_overrides || {};
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
    };

    configKeys.forEach(key => {
        const hasOverride = overrides[key] !== undefined && overrides[key] !== null && overrides[key] !== '';
        const val = currentParams[key] !== undefined ? currentParams[key] : (window.BT_INDICATOR_DEFAULTS[key] !== undefined ? window.BT_INDICATOR_DEFAULTS[key] : '');
        const label = paramLabels[key] || key.replace(/_/g, ' ').toLowerCase();
        const isPercent = key.endsWith('_PCT') || key.endsWith('_PERCENTAGE');
        const inputAttrs = key === 'PIVOT_LEVEL_COUNT'
            ? 'min="1" max="5" step="1"'
            : key === 'SWING_POINTS_LEVEL_COUNT'
                ? 'min="1" max="5" step="1"'
                : key === 'SWING_POINTS_LOOKBACK_DAYS'
                    ? 'min="1" max="365" step="1"'
            : key === 'VOLUME_PROFILE_ROWS'
                ? 'min="12" max="120" step="1"'
                : key === 'VOLUME_PROFILE_CONFIRMATION_CANDLES'
                    ? 'min="1" max="5" step="1"'
                    : 'step="any"';

        const div = document.createElement('div');
        div.className = 'flex flex-col gap-1.5';
        const controlHtml = key === 'SWING_POINTS_ENTRY_MODE'
            ? `<select data-key="${key}" data-default="${window.BT_INDICATOR_DEFAULTS[key] !== undefined ? window.BT_INDICATOR_DEFAULTS[key] : ''}"
                    class="w-full bg-slate-50 border border-slate-200 rounded-lg px-3 py-2 text-sm font-bold text-indigo-900 focus:ring-2 focus:ring-indigo-500 focus:border-transparent outline-none transition-all">
                    <option value="bounce_retest" ${val === 'bounce_retest' ? 'selected' : ''}>Bounce / Retest</option>
                    <option value="breakout_retest" ${val === 'breakout_retest' ? 'selected' : ''}>Breakout / Retest</option>
                </select>`
            : `<input type="number" ${inputAttrs} data-key="${key}" value="${val}" placeholder="${window.BT_INDICATOR_DEFAULTS[key] !== undefined ? window.BT_INDICATOR_DEFAULTS[key] : ''}"
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
    btCurrentEditingIndicator = null;
}

function resetIndicatorSettingsToDefaults() {
    if (!btCurrentEditingIndicator) return;
    const inputs = document.querySelectorAll('#indicatorModalContent input, #indicatorModalContent select');
    inputs.forEach(input => {
        input.value = input.tagName === 'SELECT' ? (input.dataset.default || '') : input.placeholder; // Revert to system default
        const key = input.dataset.key;
        if (key) {
            delete window.btIndicatorOverrides[key];
            if (window.lastBacktestData) {
                if (window.lastBacktestData.indicator_overrides) delete window.lastBacktestData.indicator_overrides[key];
                if (window.lastBacktestData.indicator_parameters) delete window.lastBacktestData.indicator_parameters[key];
            }
        }
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
    });
}

async function saveIndicatorSettings() {
    const inputs = document.querySelectorAll('#indicatorModalContent input, #indicatorModalContent select');
    const updates = {};
    inputs.forEach(input => {
        const defaultValue = input.tagName === 'SELECT' ? input.dataset.default : input.placeholder;
        if (input.value === defaultValue || input.value === "") {
            updates[input.dataset.key] = "";
        } else {
            const numVal = Number(input.value);
            updates[input.dataset.key] = Number.isFinite(numVal) ? numVal : input.value;
        }
    });

    if (Object.keys(updates).length === 0) {
        closeIndicatorSettings();
        return;
    }

    try {
        const headers = await getAuthHeaders();
        const response = await fetch(`${window.BACKTEST_API_BASE}/api/settings/indicators?context=backtest`, {
            method: 'POST',
            headers: headers,
            body: JSON.stringify(updates)
        });

        if (response.ok) {
            console.log(`[settings] Updated settings for ${btCurrentEditingIndicator}`);
            if (!window.lastBacktestData) {
                window.lastBacktestData = { indicator_parameters: {}, indicator_overrides: {} };
            }
            if (!window.lastBacktestData.indicator_parameters) {
                window.lastBacktestData.indicator_parameters = {};
            }
            if (!window.lastBacktestData.indicator_overrides) {
                window.lastBacktestData.indicator_overrides = {};
            }
            for (const [k, v] of Object.entries(updates)) {
                if (v === "" || v === null) {
                    delete window.lastBacktestData.indicator_overrides[k];
                    delete window.lastBacktestData.indicator_parameters[k];
                } else {
                    window.lastBacktestData.indicator_overrides[k] = v;
                    window.lastBacktestData.indicator_parameters[k] = v;
                }
            }
            closeIndicatorSettings();
            const rerunRequiredKeys = [
                'DAILY_HIGH_LOW_BUFFER_PCT',
                'PIVOT_TOLERANCE_PCT',
                'VOLUME_PROFILE_ROWS',
                'VOLUME_PROFILE_VALUE_AREA_PCT',
                'VOLUME_PROFILE_TOLERANCE_PCT',
                'VOLUME_PROFILE_CONFIRMATION_CANDLES',
                'SWING_POINTS_LOOKBACK_DAYS',
                'SWING_POINTS_ENTRY_MODE',
            ];
            const redrawOnlyKeys = ['PIVOT_LEVEL_COUNT', 'SWING_POINTS_LEVEL_COUNT'];
            const shouldRerun = Boolean(window.lastBacktestRun?.results) && rerunRequiredKeys.some(key => updates[key] !== undefined);
            if (shouldRerun) {
                console.log('[settings] Re-running backtest because indicator settings changed signal/level calculations.');
                await runBacktest({ force: true });
                return;
            }
            if (redrawOnlyKeys.some(key => updates[key] !== undefined)) {
                redrawBtChartWithCurrentSettings();
            }
        } else {
            alert("Error saving indicator settings.");
        }
    } catch (err) {
        console.error(err);
        alert("Backend communication error.");
    }
}

function toggleIndicatorCard(card, event) {
    if (event.target.closest('button') || event.target.closest('input[type="checkbox"]')) {
        return;
    }
    const checkbox = card.querySelector('input[type="checkbox"]');
    if (checkbox?.disabled) {
        return;
    }
    if (checkbox) {
        checkbox.checked = !checkbox.checked;
        checkbox.dispatchEvent(new Event('change', { bubbles: true }));
    }
}

window.toggleRiskSection = function() {
    const content = document.getElementById('riskControlsContent');
    const arrow = document.getElementById('riskArrowIcon');
    if (content.classList.contains('hidden')) {
        content.classList.remove('hidden');
        arrow.classList.add('rotate-180');
    } else {
        content.classList.add('hidden');
        arrow.classList.remove('rotate-180');
    }
};

function checkCryptoOptionStatus(ticker) {
    const isCrypto = ticker && (
        ticker.toUpperCase().includes('USD') || 
        ticker.toUpperCase().includes('USDT') || 
        ticker.toUpperCase().includes('USDC') ||
        ['BTC', 'ETH', 'LTC', 'SOL', 'DOGE', 'ADA', 'DOT', 'SHIB', 'AVAX'].some(c => ticker.toUpperCase().startsWith(c))
    );
    const optionsToggle = document.getElementById('btOptionsToggle');
    const optionsCard = document.getElementById('btOptionsCard');

    if (isCrypto) {
        if (optionsToggle && optionsToggle.checked) {
            optionsToggle.checked = false;
            window.handleBacktestOptionsToggle(false);
        }
        if (optionsCard) {
            optionsCard.style.opacity = '0.4';
            optionsCard.style.pointerEvents = 'none';
        }
    } else {
        if (optionsCard) {
            optionsCard.style.opacity = '1';
            optionsCard.style.pointerEvents = 'auto';
        }
    }
}
window.checkCryptoOptionStatus = checkCryptoOptionStatus;


// ── Monte Carlo Simulation Functions ─────────────────────────────────────

async function runMonteCarlo() {
    if (!window.lastBacktestRun || !window.lastBacktestRun.results || !window.lastBacktestRun.results.trades) {
        alert("Run a backtest first before running Monte Carlo.");
        return;
    }

    const trades = window.lastBacktestRun.results.trades;
    if (trades.length < 3) {
        alert("Not enough trades executed in the backtest to run a meaningful Monte Carlo simulation. Need at least 3 trades.");
        return;
    }

    const capital = window.lastBacktestRun.capital || 1000.0;
    
    // UI state
    document.getElementById('runMcBtn').disabled = true;
    document.getElementById('mcHint').classList.add('hidden');
    document.getElementById('mcResults').classList.add('hidden');
    document.getElementById('mcLoading').classList.remove('hidden');

    try {
        const headers = await getAuthHeaders();
        const response = await fetch(`${window.BACKTEST_API_BASE}/api/backtest/monte_carlo`, {
            method: 'POST',
            headers: headers,
            body: JSON.stringify({
                trades: trades,
                capital: capital,
                n_simulations: 1000
            })
        });

        if (!response.ok) throw new Error(`HTTP Error: ${response.status}`);
        const data = await response.json();

        if (data.status === 'success') {
            _renderMonteCarloResults(data.result);
        } else {
            alert("Monte Carlo Failed: " + (data.message || "Unknown error"));
            document.getElementById('mcHint').classList.remove('hidden');
        }
    } catch (err) {
        console.error("MONTE CARLO ERROR:", err);
        alert("Failed to run simulation. Check connection.");
        document.getElementById('mcHint').classList.remove('hidden');
    } finally {
        document.getElementById('mcLoading').classList.add('hidden');
        document.getElementById('runMcBtn').disabled = false;
    }
}

function _renderMonteCarloResults(res) {
    document.getElementById('mcResults').classList.remove('hidden');

    // Update text stats
    document.getElementById('mcOrigSharpe').textContent = res.original_sharpe.toFixed(2);
    document.getElementById('mcMedianSharpe').textContent = res.median_sharpe.toFixed(2);
    document.getElementById('mcBest5Sharpe').textContent = res.best_5pct_sharpe.toFixed(2);
    document.getElementById('mcPercentile').textContent = res.percentile_rank.toFixed(1) + '%';
    document.getElementById('mcRiskDesc').textContent = res.risk_description;

    // Badge styling
    const badge = document.getElementById('mcRiskBadge');
    badge.textContent = res.overfitting_risk;
    badge.className = "text-[0.6rem] font-black px-2.5 py-1 rounded-full uppercase tracking-wider ";
    if (res.overfitting_risk === 'LOW') {
        badge.className += "bg-emerald-100 text-emerald-700";
        badge.textContent += " ✅";
    } else if (res.overfitting_risk === 'MEDIUM') {
        badge.className += "bg-amber-100 text-amber-700";
        badge.textContent += " ⚠️";
    } else {
        badge.className += "bg-rose-100 text-rose-700";
        badge.textContent += " 🚨";
    }

    // Render Fan Chart
    _renderMonteCarloFanChart(res.fan_curves, res.original_equity_curve);
}

var mcChartInstance = null;

function _renderMonteCarloFanChart(fanCurves, originalCurve) {
    const canvas = document.getElementById('mcFanChart');
    if (!canvas) return;
    
    const ctx = canvas.getContext('2d');
    
    // Destroy previous Chart.js instance if it exists
    if (mcChartInstance) {
        mcChartInstance.destroy();
    }

    // Use Chart.js to draw the fan chart since it handles hundreds of datasets better than TV lightweight-charts
    const datasets = [];
    
    // Add all simulation curves (faint grey/blue)
    fanCurves.forEach((curve, idx) => {
        datasets.push({
            data: curve,
            borderColor: 'rgba(99, 102, 241, 0.04)', // very faint indigo
            borderWidth: 1,
            pointRadius: 0,
            pointHoverRadius: 0,
            fill: false,
            tension: 0.1
        });
    });

    // Add the original strategy curve (bright gold/amber)
    datasets.push({
        data: originalCurve,
        borderColor: 'rgba(245, 158, 11, 1)', // amber-500
        borderWidth: 2.5,
        pointRadius: 0,
        pointHoverRadius: 0,
        fill: false,
        tension: 0.1,
        zIndex: 100 // draw on top
    });

    const labels = Array.from({length: originalCurve.length}, (_, i) => i);

    mcChartInstance = new Chart(ctx, {
        type: 'line',
        data: {
            labels: labels,
            datasets: datasets
        },
        options: {
            responsive: true,
            maintainAspectRatio: false,
            animation: { duration: 0 },
            plugins: {
                legend: { display: false },
                tooltip: { enabled: false }
            },
            scales: {
                x: { display: false },
                y: { 
                    display: true,
                    position: 'right',
                    grid: { color: 'rgba(99, 102, 241, 0.05)' },
                    ticks: {
                        color: '#94a3b8',
                        font: { size: 9 },
                        callback: function(value) {
                            if (value >= 1000) return '$' + (value/1000).toFixed(1) + 'k';
                            return '$' + value;
                        }
                    }
                }
            },
            interaction: {
                mode: 'nearest',
                intersect: false,
            }
        }
    });
}

window.updateBtRegularEntryFields = function() {
    const mode = document.getElementById('strategyBuyMode_regular')?.value || 'indicator';
    const indicatorModes = ['indicator', 'hybrid_atr', 'hybrid_pct', 'hybrid_pivot', 'hybrid_high_low', 'hybrid_swing'];
    const atrModes = ['atr', 'hybrid_atr'];
    const pctModes = ['percentage', 'hybrid_pct'];
    const pivotModes = ['pivot_points', 'hybrid_pivot'];
    const highLowModes = ['high_low', 'hybrid_high_low'];
    const swingModes = ['swing_points', 'hybrid_swing'];

    document.getElementById('strategyBuyFields_regular')?.classList.toggle('hidden', !indicatorModes.includes(mode));
    document.getElementById('strategyCustomBuyFields_regular')?.classList.toggle('hidden', mode !== 'custom_price');
    document.getElementById('strategyEntryAtrFields_regular')?.classList.toggle('hidden', !atrModes.includes(mode));
    document.getElementById('strategyEntryPctFields_regular')?.classList.toggle('hidden', !pctModes.includes(mode));
    document.getElementById('strategyEntryPivotFields_regular')?.classList.toggle('hidden', !pivotModes.includes(mode));
    document.getElementById('strategyEntryHighLowFields_regular')?.classList.toggle('hidden', !highLowModes.includes(mode));
    document.getElementById('strategySwingEntrySettings_regular')?.classList.toggle('hidden', !swingModes.includes(mode));
    const helper = document.getElementById('strategyBuyHelper_regular');
    if (helper) helper.textContent = BT_REGULAR_HELPER_TEXTS.entry[mode] || BT_REGULAR_HELPER_TEXTS.entry.indicator;
    window.updateBtSwingLookbackFields?.('entry');
    updateSignalLabels('regular');
};

window.updateBtRegularExitFields = function() {
    const mode = document.getElementById('strategySellMode_regular')?.value || 'indicator';
    const showATR    = ['sltp', 'hybrid'].includes(mode);
    const showPct    = ['percentage', 'hybrid_pct'].includes(mode);
    const showCustom = mode === 'custom_sltp';
    const showPivot  = ['pivot_points', 'hybrid_pivot'].includes(mode);
    const showHighLow = ['high_low', 'hybrid_high_low'].includes(mode);
    const showSwing = ['swing_points', 'hybrid_swing'].includes(mode);
    
    document.getElementById('exitFieldsIndicator')?.classList.toggle('hidden', !['indicator', 'hybrid', 'hybrid_pct', 'hybrid_pivot', 'hybrid_high_low', 'hybrid_swing'].includes(mode));
    document.getElementById('exitFieldsATR')?.classList.toggle('hidden', !showATR);
    document.getElementById('exitFieldsPct')?.classList.toggle('hidden', !showPct);
    document.getElementById('exitFieldsCustom_regular')?.classList.toggle('hidden', !showCustom);
    document.getElementById('exitFieldsPivot')?.classList.toggle('hidden', !showPivot);
    document.getElementById('exitFieldsHighLow')?.classList.toggle('hidden', !showHighLow);
    document.getElementById('strategySwingExitSettings_regular')?.classList.toggle('hidden', !showSwing);
    const helper = document.getElementById('strategySellHelper_regular');
    if (helper) helper.textContent = BT_REGULAR_HELPER_TEXTS.exit[mode] || BT_REGULAR_HELPER_TEXTS.exit.indicator;
    window.updateBtSwingLookbackFields?.('exit');
    updateSignalLabels('regular');
};

window.updateBtAllocationModeUI = function() {
    const mode = document.getElementById('strategyAllocationMode_regular')?.value || 'fixed';
    const hint = document.getElementById('strategyAllocationHint_regular');
    const label = document.getElementById('strategyCapitalLabel_regular');
    if (label) {
        label.textContent = mode === 'compound' ? 'Starting Amount ($)' : 'Amount Per Trade ($)';
    }
    if (hint) {
        hint.classList.toggle('hidden', mode !== 'compound');
        hint.textContent = mode === 'compound'
            ? 'Backtest compounds realized P/L into the next trade amount.'
            : '';
    }
};

window.updateBtOptionsAllocationModeUI = function() {
    const mode = document.getElementById('strategyAllocationMode_options')?.value || 'fixed';
    const hint = document.getElementById('strategyAllocationHint_options');
    const label = document.getElementById('strategyCapitalLabel_options');
    if (label) {
        label.textContent = mode === 'compound' ? 'Starting Amount ($)' : 'Amount Per Trade ($)';
    }
    if (hint) {
        hint.classList.toggle('hidden', mode !== 'compound');
        hint.textContent = mode === 'compound'
            ? 'Options backtest compounds realized P/L into the next trade amount.'
            : '';
    }
};

window.updateBtOptionsDirectionHint = function() {
    const type = document.getElementById('strategyOptionsOptionType')?.value || 'calls_only';
    const hint = document.getElementById('strategyOptionsDirectionHint');
    if (hint) {
        hint.textContent = type === 'puts_only'
            ? '⚡ Puts Only: Bot will ONLY open bearish PUT option contracts when bearish entry signals confirm. All bullish signals are ignored.'
            : '⚡ Calls Only: Bot will ONLY open bullish CALL option contracts when bullish entry signals confirm. All bearish signals are ignored.';
    }
    window.updateBtOptionsEntryFields?.();
    window.updateBtOptionsExitFields?.();
};

window.updateBtSwingLookbackFields = function(kind) {
    const prefix = kind === 'exit' ? 'strategySwingExitLookback' : 'strategySwingEntryLookback';
    const select = document.getElementById(`${prefix}_regular`);
    const input = document.getElementById(`${prefix}Custom_regular`);
    if (!select || !input) return;
    input.classList.toggle('hidden', select.value !== 'custom');
};

window.updateBtOptionsSwingLookbackFields = function(kind) {
    const prefix = kind === 'exit' ? 'strategySwingExitLookback' : 'strategySwingEntryLookback';
    const select = document.getElementById(`${prefix}_options`);
    const wrap = document.getElementById(`${prefix}CustomWrap_options`);
    if (!select || !wrap) return;
    wrap.classList.toggle('hidden', select.value !== 'custom');
};

window.updateBtSmartReentryFields = function(mode) {
    if (!mode) mode = window.currentBtMode || 'regular';
    const capMode = mode === 'regular' ? 'Regular' : 'Options';

    const isEnabled = document.getElementById(`strategy${capMode}SmartReentry`)?.checked;
    const fieldsContainer = document.getElementById(`strategySmartReentryFields_${mode}`);
    if (!fieldsContainer) return;

    if (!isEnabled) {
        fieldsContainer.classList.add('hidden');
        return;
    }

    fieldsContainer.classList.remove('hidden');
    const type = document.getElementById(`strategy${capMode}SmartReentryType`)?.value || 'RSI';

    const rsiCall = document.getElementById(`strategy${capMode}RsiCallContainer`);
    const rsiPut = document.getElementById(`strategy${capMode}RsiPutContainer`);
    const vwapCall = document.getElementById(`strategy${capMode}VwapCallContainer`);
    const vwapPut = document.getElementById(`strategy${capMode}VwapPutContainer`);

    if (type === 'RSI') {
        if (rsiCall) rsiCall.classList.remove('hidden');
        if (rsiPut) rsiPut.classList.remove('hidden');
        if (vwapCall) vwapCall.classList.add('hidden');
        if (vwapPut) vwapPut.classList.add('hidden');
    } else {
        if (rsiCall) rsiCall.classList.add('hidden');
        if (rsiPut) rsiPut.classList.add('hidden');
        if (vwapCall) vwapCall.classList.remove('hidden');
    }
};

window.updateBtDefaultCooldownLabel = function() {
    const tfEl = document.getElementById('strategyTimeframe_regular');
    if (!tfEl) return;
    const minutes = getBtTimeframeMinutes(tfEl.value) * 4;
    const modes = ['regular', 'options'];
    modes.forEach(mode => {
        const defaultOption = document.querySelector(`#strategyCooldownMinutes_${mode} option[value="default"]`);
        if (defaultOption) {
            defaultOption.textContent = `Default (${formatBtMinutes(minutes)})`;
        }
    });
};

function getBtTimeframeMinutes(tfStr) {
    switch(tfStr) {
        case '1Min': return 1;
        case '5Min': return 5;
        case '10Min': return 10;
        case '15Min': return 15;
        case '30Min': return 30;
        case '1Hour': return 60;
        case '4Hour': return 240;
        case '1Day': return 1440;
        default: return 60;
    }
}

function formatBtMinutes(min) {
    if (min < 60) return min + " min";
    const hrs = min / 60;
    if (hrs < 24) {
        return hrs + (hrs === 1 ? " hour" : " hours");
    }
    const days = hrs / 24;
    return days + (days === 1 ? " day" : " days");
}

window.adjustBtThreshold = function(type, delta) {
    window.adjustStrategyThreshold('regular', type, delta);
};

window.updateBtOptionsEntryFields = function() {
    const mode = document.getElementById('strategyBuyMode_options')?.value || 'indicator';
    const direction = document.getElementById('strategyOptionsOptionType')?.value || 'calls_only';
    const isPut = direction === 'puts_only';
    const showIndicator = ['indicator', 'hybrid_atr', 'hybrid_pct', 'hybrid_pivot', 'hybrid_high_low', 'hybrid_swing'].includes(mode);
    const showCustom = mode === 'custom_price';
    const showATR = ['atr', 'hybrid_atr'].includes(mode);
    const showPct = ['percentage', 'hybrid_pct'].includes(mode);
    const showPivot = ['pivot_points', 'hybrid_pivot'].includes(mode);
    const showHighLow = ['high_low', 'hybrid_high_low'].includes(mode);
    const showSwing = ['swing_points', 'hybrid_swing'].includes(mode);

    document.getElementById('strategyBuyFields_options')?.classList.toggle('hidden', !showIndicator);
    document.getElementById('strategyCallEntryThresholdPanel_options')?.classList.toggle('hidden', isPut);
    document.getElementById('strategyPutEntryThresholdPanel_options')?.classList.toggle('hidden', !isPut);
    document.getElementById('strategyCustomBuyFields_options')?.classList.toggle('hidden', !showCustom);
    document.getElementById('strategyEntryAtrFields_options')?.classList.toggle('hidden', !showATR);
    document.getElementById('strategyEntryPctFields_options')?.classList.toggle('hidden', !showPct);
    document.getElementById('strategyEntryPivotFields_options')?.classList.toggle('hidden', !showPivot);
    document.getElementById('strategyEntryHighLowFields_options')?.classList.toggle('hidden', !showHighLow);
    document.getElementById('strategySwingEntrySettings_options')?.classList.toggle('hidden', !showSwing);

    const helper = document.getElementById('strategyBuyHelper_options');
    if (helper) helper.textContent = BT_REGULAR_HELPER_TEXTS.entry_options[mode] || BT_REGULAR_HELPER_TEXTS.entry_options.indicator;
    const customTitle = document.getElementById('strategyCustomBuyTitle_options');
    const customHint = document.getElementById('strategyCustomBuyHint_options');
    if (customTitle) customTitle.textContent = isPut ? 'Underlying PUT Entry Target' : 'Underlying CALL Entry Target';
    if (customHint) {
        customHint.textContent = isPut
            ? 'Custom price opens a PUT when the underlying rallies to or above this target.'
            : 'Custom price opens a CALL when the underlying drops to this target.';
    }
    window.updateBtOptionsSwingLookbackFields?.('entry');
    updateSignalLabels('options');
};

window.updateBtOptionsExitFields = function() {
    const mode = document.getElementById('strategySellMode_options')?.value || 'indicator';
    const direction = document.getElementById('strategyOptionsOptionType')?.value || 'calls_only';
    const isPut = direction === 'puts_only';
    const showIndicator = ['indicator', 'hybrid', 'hybrid_pct', 'hybrid_pivot', 'hybrid_high_low', 'hybrid_swing'].includes(mode);
    const showCustom = mode === 'custom_sltp';
    const showCustomUnderlying = mode === 'custom_underlying_sltp';
    const showATR = ['sltp', 'hybrid'].includes(mode);
    const showPct = ['percentage', 'hybrid_pct'].includes(mode);
    const showPivot = ['pivot_points', 'hybrid_pivot'].includes(mode);
    const showHighLow = ['high_low', 'hybrid_high_low'].includes(mode);
    const showSwing = ['swing_points', 'hybrid_swing'].includes(mode);
    
    document.getElementById('exitFieldsIndicator_options')?.classList.toggle('hidden', !showIndicator);
    document.getElementById('exitFieldsCustom_options')?.classList.toggle('hidden', !showCustom);
    document.getElementById('exitFieldsCustomUnderlying_options')?.classList.toggle('hidden', !showCustomUnderlying);
    document.getElementById('exitFieldsATR_options')?.classList.toggle('hidden', !showATR);
    document.getElementById('exitFieldsPct_options')?.classList.toggle('hidden', !showPct);
    document.getElementById('exitFieldsPivot_options')?.classList.toggle('hidden', !showPivot);
    document.getElementById('exitFieldsHighLow_options')?.classList.toggle('hidden', !showHighLow);
    document.getElementById('strategySwingExitSettings_options')?.classList.toggle('hidden', !showSwing);

    const helper = document.getElementById('strategySellHelper_options');
    if (helper) {
        const base = BT_REGULAR_HELPER_TEXTS.exit_options[mode] || BT_REGULAR_HELPER_TEXTS.exit_options.indicator;
        const directionNoteByMode = {
            indicator: isPut ? 'PUT-specific: sell-to-close on bullish reversal signals.' : 'CALL-specific: sell-to-close on bearish reversal signals.',
            custom_sltp: 'Premium-based: same logic for CALL and PUT because it watches the option contract price.',
            custom_underlying_sltp: isPut ? 'PUT-specific: underlying stop is above entry/current; take-profit is below.' : 'CALL-specific: underlying stop is below entry/current; take-profit is above.',
            sltp: isPut ? 'PUT-specific: ATR stop/trail is above the underlying; ATR take-profit is below.' : 'CALL-specific: ATR stop/trail is below the underlying; ATR take-profit is above.',
            hybrid: isPut ? 'PUT-specific: bullish reversal OR ATR stop/target can close.' : 'CALL-specific: bearish reversal OR ATR stop/target can close.',
            percentage: 'Premium-percent based: same logic for CALL and PUT because it watches option premium gain/loss.',
            hybrid_pct: isPut ? 'PUT-specific: bullish reversal OR premium % stop/target/trail can close.' : 'CALL-specific: bearish reversal OR premium % stop/target/trail can close.',
            pivot_points: isPut ? 'PUT-specific: take-profit at support/S1; stop-loss at resistance/R1.' : 'CALL-specific: take-profit at resistance/R1; stop-loss at support/S1.',
            hybrid_pivot: isPut ? 'PUT-specific: bullish reversal OR support target/resistance stop can close.' : 'CALL-specific: bearish reversal OR resistance target/support stop can close.',
            high_low: isPut ? 'PUT-specific: take-profit at prior low; stop-loss at prior high.' : 'CALL-specific: take-profit at prior high; stop-loss at prior low.',
            hybrid_high_low: isPut ? 'PUT-specific: bullish reversal OR prior-low target/prior-high stop can close.' : 'CALL-specific: bearish reversal OR prior-high target/prior-low stop can close.',
            swing_points: isPut ? 'PUT-specific: take-profit at swing support; stop-loss at swing resistance.' : 'CALL-specific: take-profit at swing resistance; stop-loss at swing support.',
            hybrid_swing: isPut ? 'PUT-specific: bullish reversal OR swing support/resistance exit can close.' : 'CALL-specific: bearish reversal OR swing resistance/support exit can close.'
        };
        helper.textContent = `${base} ${directionNoteByMode[mode] || ''}`.trim();
    }

    const badge = document.getElementById('strategyExitSignalBadge_options');
    const title = document.getElementById('strategyExitThresholdTitle_options');
    const hint = document.getElementById('strategyExitThresholdHint_options');
    const label = document.getElementById('strategySellLabel_options');
    const stopLabel = document.getElementById('strategyCustomUnderlyingStopLabel_options');
    const tpLabel = document.getElementById('strategyCustomUnderlyingTakeProfitLabel_options');
    if (badge) {
        badge.textContent = isPut ? 'B' : 'S';
        badge.classList.toggle('bg-emerald-500', isPut);
        badge.classList.toggle('bg-rose-500', !isPut);
    }
    if (title) title.textContent = isPut ? 'Exit Threshold (Min Bullish Signals)' : 'Exit Threshold (Min Bearish Signals)';
    if (hint) {
        hint.textContent = isPut
            ? 'PUT exit waits for bullish reversal signals, or the selected risk target/stop.'
            : 'CALL exit waits for bearish reversal signals, or the selected risk target/stop.';
        hint.classList.toggle('text-emerald-700/80', isPut);
        hint.classList.toggle('text-rose-700/80', !isPut);
    }
    if (label) {
        label.classList.toggle('bg-emerald-100/80', isPut);
        label.classList.toggle('text-emerald-700', isPut);
        label.classList.toggle('border-emerald-200/55', isPut);
        label.classList.toggle('bg-rose-100/80', !isPut);
        label.classList.toggle('text-rose-700', !isPut);
        label.classList.toggle('border-rose-200/55', !isPut);
    }
    if (stopLabel) stopLabel.textContent = isPut ? 'Underlying Stop Loss Price (Above Entry)' : 'Underlying Stop Loss Price (Below Entry)';
    if (tpLabel) tpLabel.textContent = isPut ? 'Underlying Take Profit Price (Below Entry)' : 'Underlying Take Profit Price (Above Entry)';
    window.updateBtOptionsSwingLookbackFields?.('exit');
    updateSignalLabels('options');
};

function syncBtOptionsPutEntryThresholdLabel() {
    const sellInput = document.getElementById('strategySellThreshold_options');
    const putInput = document.getElementById('strategyPutEntryThreshold_options');
    const putLabel = document.getElementById('strategyPutEntryLabel_options');
    if (sellInput && putInput) {
        if (document.activeElement !== putInput) {
            putInput.value = sellInput.value || '0';
        }
        putInput.min = sellInput.min;
        putInput.max = sellInput.max;
    }
    if (putLabel) {
        const indicatorModes = ['indicator', 'hybrid', 'hybrid_atr', 'hybrid_pct', 'hybrid_pivot', 'hybrid_high_low', 'hybrid_swing'];
        const sellMode = document.getElementById('strategySellMode_options')?.value || 'hybrid';
        const needsInd = indicatorModes.includes(sellMode);
        const val = parseInt((putInput?.value || sellInput?.value || '0'), 10) || 0;
        const count = putInput?.max || sellInput?.max || 0;
        putLabel.textContent = (!needsInd && val === 0) ? `0 of ${count} signals (Not Required)` : `${val} of ${count} signals`;
    }
}

window.syncBtOptionsPutEntryThreshold = function() {
    const putInput = document.getElementById('strategyPutEntryThreshold_options');
    const sellInput = document.getElementById('strategySellThreshold_options');
    if (putInput && sellInput) {
        sellInput.value = putInput.value || '0';
    }
    updateSignalLabels('options');
    syncBtOptionsPutEntryThresholdLabel();
};

window.adjustBtOptionsPutEntryThreshold = function(delta) {
    const putInput = document.getElementById('strategyPutEntryThreshold_options');
    if (!putInput) return;
    const min = parseInt(putInput.min || '0', 10);
    const max = parseInt(putInput.max || '10', 10);
    const next = Math.max(min, Math.min(max, (parseInt(putInput.value || '0', 10) || 0) + delta));
    putInput.value = String(next);
    window.syncBtOptionsPutEntryThreshold();
};

window.adjustBtOptionsThreshold = function(type, delta) {
    window.adjustStrategyThreshold('options', type, delta);
};

window.updateBtRegularSignalLabels = function() {
    updateSignalLabels('regular');
};
window.updateBtOptionsSignalLabels = function() {
    updateSignalLabels('options');
    syncBtOptionsPutEntryThresholdLabel();
};
window.updateStrategySignalLabels = function(mode) {
    updateSignalLabels(mode);
};
window.updateRegularEntryFields = window.updateBtRegularEntryFields;
window.updateRegularExitFields = window.updateBtRegularExitFields;
window.updateOptionsEntryFields = window.updateBtOptionsEntryFields;
window.updateOptionsExitFields = window.updateBtOptionsExitFields;

window.adjustStrategyThreshold = function(mode, type, delta) {
    const containerId = mode === 'regular' ? 'indicatorChecksContainer_bt_regular' : 'indicatorChecksContainer_bt_options';
    const container = document.getElementById(containerId);
    if (!container) return;
    
    const checkClass = mode === 'regular' ? '.bt-indicator-check-regular' : '.bt-indicator-check-options';
    const checkedCount = container.querySelectorAll(`${checkClass}:checked:not(:disabled)`).length;
    
    if (checkedCount === 0) return;

    const input = document.getElementById(`strategy${type === 'buy' ? 'Buy' : 'Sell'}Threshold_${mode}`);
    if (!input) return;

    let val = parseInt(input.value) + delta;
    val = Math.max(0, Math.min(val, checkedCount));
    input.value = val;

    // Update labels
    updateSignalLabels(mode);
    if (mode === 'options') syncBtOptionsPutEntryThresholdLabel();
};
window.toggleModeActive = function(mode) {
    if (mode === 'options') return window.toggleBtMode('options');
    return window.toggleBtMode('regular');
};
