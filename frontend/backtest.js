
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

let backtestRequestInProgress = false;
let alpacaStatusPollInFlight = false;
let alpacaStatusPollTimer = null;

const BT_AI_INDICATOR_CACHE_TTL = 30 * 60 * 1000;

function normalizeBtAIIndicatorTicker(ticker) {
    return typeof window.normalizeAIIndicatorTicker === 'function' ? window.normalizeAIIndicatorTicker(ticker) : (ticker || '').toString().trim().toUpperCase();
}

function getBtAIIndicatorCacheKey(ticker, timeframe, model) {
    return typeof window.getAIIndicatorCacheKey === 'function' ? window.getAIIndicatorCacheKey(ticker, timeframe, model) : `backtest|${normalizeBtAIIndicatorTicker(ticker)}|${timeframe}|${model}|ai-hourly-v2`;
}

function isBtAICacheFresh(cached) {
    return typeof window.isAICacheFresh === 'function' ? window.isAICacheFresh(cached) : (cached && cached.timestamp && (Date.now() - cached.timestamp) < BT_AI_INDICATOR_CACHE_TTL);
}

function getBtAICache(key) {
    return typeof window.getAICache === 'function' ? window.getAICache(key) : null;
}

function normalizeBtCachedAISentimentScore(rawScore) {
    return typeof window.normalizeCachedAISentimentScore === 'function' ? window.normalizeCachedAISentimentScore(rawScore) : null;
}

function getBtCachedAIAnalysisForTicker(ticker, timeframe) {
    return typeof window.getCachedAIAnalysisForTicker === 'function' ? window.getCachedAIAnalysisForTicker(ticker, timeframe) : null;
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
        grid_continuous_mode: document.getElementById('strategyGridContinuous_regular')?.checked || false,
        grid_buy_allocation: parseFloat(document.getElementById('strategyGridBuyAllocation_regular')?.value || '1000'),
        grid_sell_pct: parseFloat(document.getElementById('strategyGridSellPct_regular')?.value || '50'),
        grid_max_levels: parseInt(document.getElementById('strategyGridMaxLevels_regular')?.value || '5'),
        exit_grid_continuous_mode: document.getElementById('strategyExitGridContinuous_regular')?.checked || false,
        exit_grid_sell_pct: parseFloat(document.getElementById('strategyExitGridSellPct_regular')?.value || '50'),
        exit_grid_max_levels: parseInt(document.getElementById('strategyExitGridMaxLevels_regular')?.value || '5'),
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

function getBacktestWorkspaceShownSet(mode = 'regular', ticker = '') {
    const normalizedMode = mode === 'options' ? 'options' : 'regular';
    const contextLabel = `backtest-${normalizedMode}`;
    const activeTicker = ticker
        || (typeof manualBtTicker !== 'undefined' ? manualBtTicker : '')
        || document.getElementById('btTicker')?.value
        || '';
    const fallback = (window.STANDARD_INDICATOR_NAMES || []).slice(0, 10);
    const shown = window.IndicatorWorkspace?.getShownForScope?.(contextLabel, activeTicker)
        || window.IndicatorWorkspace?.getShown?.(contextLabel)
        || fallback;
    return new Set(shown);
}

function isBacktestIndicatorAllowed(indicatorName, mode = 'regular', ticker = '') {
    const standardNames = window.STANDARD_INDICATOR_NAMES || [];
    return !standardNames.includes(indicatorName)
        || getBacktestWorkspaceShownSet(mode, ticker).has(indicatorName);
}

function getBtCheckedIndicatorValues(mode = 'regular') {
    const normalizedMode = mode === 'options' ? 'options' : 'regular';
    const selector = normalizedMode === 'options'
        ? '.bt-indicator-check-options:checked:not(:disabled)'
        : '.bt-indicator-check-regular:checked:not(:disabled)';
    return Array.from(document.querySelectorAll(selector))
        .map(input => String(input.value || '').trim())
        .filter(name => name && isBacktestIndicatorAllowed(name, normalizedMode));
}

function getBtSignalThreshold(mode, type, indicatorCount = null) {
    const field = type === 'buy' ? 'Buy' : 'Sell';
    const input = document.getElementById(`strategy${field}Threshold_${mode}`);
    const selectedCount = Number.isFinite(Number(indicatorCount))
        ? Number(indicatorCount)
        : getBtCheckedIndicatorValues(mode).length;
    if (selectedCount <= 0) return 0;
    const parsed = parseInt(input?.value || '1', 10);
    return Math.max(1, Math.min(Number.isFinite(parsed) ? parsed : 1, selectedCount));
}

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
var btChartMarkersRef = [];    // Visible merged markers array
var btTradeMarkersRef = [];    // Executed backtest entry/exit markers
var btIndicatorMarkersRef = []; // Indicator signal markers (ST/BB/candles)
var btStrategyChartTradesRef = [];
var btStrategyTradeMarkersRef = [];
var btChartTradeIdx = 0;       // Currently highlighted trade (0-based, newest-first order)
var btChartTradesRef = [];     // Reference to trades array (newest-first, same as allBtTrades)
var btChartCandlesRef = [];    // Reference to candles array
var btChartEquityCurveRef = [];
var btChartTimeframeRef = null; // Timeframe captured from the completed backtest
var btChartResizeObserver = null; // ResizeObserver for responsive chart
var btManualChartEnabled = false;
var btManualPickTarget = null;
var btManualTradePoints = { entry: null, exit: null, sl: null, tp: null };
var btManualEntryPriceLine = null;
var btManualExitPriceLine = null;
var btManualStopPriceLine = null;
var btManualTargetPriceLine = null;
var btManualChartClickHandler = null;

const BT_CHART_LOCALIZATION = {
    timeFormatter: (businessDayOrTimestamp) => {
        if (!businessDayOrTimestamp) return '';
        if (typeof businessDayOrTimestamp === 'object') {
            return `${businessDayOrTimestamp.year}-${String(businessDayOrTimestamp.month).padStart(2, '0')}-${String(businessDayOrTimestamp.day).padStart(2, '0')}`;
        }
        return new Date(businessDayOrTimestamp * 1000).toLocaleString('en-US', {
            timeZone: 'America/Chicago',
            year: 'numeric',
            month: 'short',
            day: 'numeric',
            hour: '2-digit',
            minute: '2-digit',
            hour12: false
        }) + ' CT';
    }
};
var btManualRiskRewardEls = [];
var btManualReplayVisibleTime = null;
// Historical replay state is deliberately isolated to the backtest chart.
var btReplayActive = false;
var btReplayPlaying = false;
var btReplayCursor = 0;
var btReplaySpeed = 1;
var btReplayTimer = null;
var btReplayBaseMarkersRef = [];
var btReplayFullCandlesRef = [];
var btChartAuxSeries = [];      // Non-price-series overlays, tracked for replay slicing
var btReplayStopPriceLine = null;
var btReplayTargetPriceLine = null;
// ────────────────────────────────────────────────────────────────────────

// ── Manual Backtest Chart Preview Modal State ────────────────────────────
var manualBtChart = null;
var manualBtCandleSeries = null;
var manualBtVolumeSeries = null;
var manualBtExtHoursSeries = null;
var manualBtOverlaySeries = {};
var manualBtOscCharts = {};
var manualBtOscSeries = {};
var manualBtResizeObserver = null;
var manualBtHistory = [];
var manualBtDisplayHistory = [];
var manualBtSignals = {};
var manualBtRange = '1M';
var manualBtSession = 'regular';
var manualBtReplayActive = false;
var manualBtReplayPlaying = false;
var manualBtReplayCursor = null;
var manualBtReplayStartIndex = 0;
var manualBtReplayTimer = null;
var manualBtReplaySpeed = 1;
var manualBtReplayStatusNote = '';
var manualBtPickTarget = null;
var manualBtTrade = { entry: null, tp: null, sl: null };
var manualBtDraggingLine = null;
var manualBtCrosshairPrice = null;
var manualBtDragMousedownHandler = null;
var manualBtDragMouseupHandler = null;
var manualBtRiskEls = [];
var manualBtPriceLines = [];
var manualBtClickHandler = null;
var manualBtOverlayPriceLines = {};
var manualBtOverlayLabelMeta = {};
var manualBtVisibleHistory = [];
var manualBtDataSource = 'Webull';
var manualBtTimeframe = null;
var manualBtTicker = '';
var manualBtDays = 90;
var manualBtSourcePreference = 'webull';
var manualBtReplayDataCache = new Map();
const MANUAL_BT_CHART_PREVIEW_INDICATORS = [
    ...(window.STANDARD_INDICATOR_NAMES || [
        'RSI', 'MACD', 'EMA Cross', 'ADX Trend', 'Supertrend', 'Bollinger',
        'VWAP', 'Volume Profile', 'SMA', 'Prior Day High/Low',
        'Pivot Points (S/R)', 'Swing Points', 'Mystic Pulse', 'Candle Patterns',
    ]),
    'Botbulls4 News AI',
    'BotBulls1',
    'BotBulls2',
    'BotBulls3',
    'BotBulls5',
];
const MANUAL_BT_PREMIUM_INDICATORS = new Set(['BotBulls1', 'BotBulls2', 'BotBulls3', 'BotBulls5']);
const MANUAL_BT_INDICATOR_PREF_KEY = 'manualBacktestChartIndicatorsV1';
const MANUAL_BT_DEFAULT_INDICATORS = new Set(['RSI', 'MACD', 'EMA Cross', 'Supertrend', 'VWAP', 'BotBulls1', 'BotBulls2', 'BotBulls3']);
const MANUAL_BT_REPLAY_CACHE_LIMIT = 8;
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
        { name: 'BotBulls5', subtitle: 'Premium Pattern Predictor', settings: true, premium: true },
    ];
    [
        { mode: 'regular', containerId: 'indicatorChecksContainer_bt_regular', checkClass: 'bt-indicator-check-regular' },
        { mode: 'options', containerId: 'indicatorChecksContainer_bt_options', checkClass: 'bt-indicator-check-options' },
    ].forEach(({ mode, containerId, checkClass }) => {
        const container = document.getElementById(containerId);
        if (!container) return;

        cards.forEach(({ name, subtitle, settings, premium = false }) => {
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
            card.className = premium
                ? 'indicator-card group p-2.5 border border-amber-300/30 bg-amber-50/5 hover:bg-amber-50/15 rounded-2xl cursor-pointer flex items-center justify-between shadow-sm transition-all'
                : 'indicator-card group p-2.5 border border-white/20 rounded-2xl cursor-pointer flex items-center justify-between shadow-sm transition-all';
            card.setAttribute('onclick', 'toggleIndicatorCard(this, event)');
            card.innerHTML = `
            <div class="flex flex-col">
                <span class="font-black ${premium ? 'text-amber-800' : 'text-indigo-900'} uppercase">${premium ? '✦ ' : ''}${name}
                    <div class="group/tooltip relative inline-flex items-center ml-1">
                        <svg onclick="event.stopPropagation(); event.preventDefault(); openInfoModal('${escapedName}')" xmlns="http://www.w3.org/2000/svg" class="h-3.5 w-3.5 text-indigo-300 hover:text-indigo-600 cursor-pointer transition-colors" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                            <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M13 16h-1v-4h-1m1-4h.01M21 12a9 9 0 11-18 0 9 9 0 0118 0z" />
                        </svg>
                    </div>
                </span>
                <span class="text-[0.6rem] ${premium ? 'text-amber-600' : 'text-indigo-400'} font-bold uppercase tracking-widest">${subtitle}</span>
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
    if (window.IndicatorWorkspace) {
        window.IndicatorWorkspace.refresh();
        return;
    }
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
    window.AlpacaStatusCache?.render();
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

    // Initialize the form UI without mutating the isolated Backtest settings.
    setTimeout(() => {
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
                                window.IndicatorWorkspace?.setTicker(item.symbol);
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
            window.IndicatorWorkspace?.setTicker(e.target.value.trim());
            window.checkCryptoOptionStatus(e.target.value.trim());
        });
        btTickerInput.addEventListener('blur', (e) => {
            window.IndicatorWorkspace?.setTicker(e.target.value.trim());
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

    // Manual chart ticker search is intentionally separate from the regular
    // backtest ticker field, so opening manual replay never preloads a strategy ticker.
    const manualTickerInput = document.getElementById('manualBtTickerSearch');
    const manualResultsContainer = document.getElementById('manualBtTickerSearchResults');
    let manualSearchTimeout = null;
    if (manualTickerInput && manualResultsContainer) {
        manualTickerInput.addEventListener('input', (e) => {
            const query = e.target.value.trim();
            if (query.length < 2) {
                manualResultsContainer.classList.add('hidden');
                return;
            }
            clearTimeout(manualSearchTimeout);
            manualSearchTimeout = setTimeout(async () => {
                try {
                    const headers = await getAuthHeaders();
                    const res = await fetch(`${window.BACKTEST_API_BASE}/api/search/${encodeURIComponent(query)}`, { headers });
                    const data = await res.json();
                    manualResultsContainer.innerHTML = '';
                    if (data && data.length > 0) {
                        data.forEach(item => {
                            const div = document.createElement('div');
                            div.className = "p-3 hover:bg-indigo-50 cursor-pointer border-b border-indigo-50 last:border-0 transition-colors flex justify-between items-center";
                            div.innerHTML = `
                                <div class="flex flex-col text-left min-w-0">
                                    <span class="font-black text-indigo-900">${item.symbol}</span>
                                    <span class="text-[0.65rem] text-indigo-400 font-medium truncate w-56">${item.name || ''}</span>
                                </div>
                                <span class="text-[0.6rem] bg-indigo-100 text-indigo-500 px-2 py-0.5 rounded uppercase font-bold tracking-widest">Load</span>
                            `;
                            div.onclick = () => {
                                manualTickerInput.value = item.symbol;
                                manualResultsContainer.classList.add('hidden');
                                window.loadManualBtChartTicker(item.symbol);
                            };
                            manualResultsContainer.appendChild(div);
                        });
                        manualResultsContainer.classList.remove('hidden');
                    } else {
                        manualResultsContainer.innerHTML = '<div class="p-3 text-center text-xs text-indigo-400 font-medium">No assets found</div>';
                        manualResultsContainer.classList.remove('hidden');
                    }
                } catch (err) {
                    console.error('[manual-backtest-search] Error:', err);
                }
            }, 250);
        });
        document.addEventListener('click', (e) => {
            if (!manualTickerInput.contains(e.target) && !manualResultsContainer.contains(e.target)) {
                manualResultsContainer.classList.add('hidden');
            }
        });
    }

    // Initial slider sync
    syncBacktestSliderRange();
    console.log('[backtest] Center Initialized.');

    // Start Alpaca Status Polling once auth is ready
    const checkAuth = setInterval(async () => {
        if (window.auth && (window.auth.currentUser || localStorage.getItem('dev_mode') === 'true')) {
            clearInterval(checkAuth);
            await window.loadBacktestIndicatorSettings?.();
            disableBacktestBotBulls4Indicator();
            pollAlpacaStatus();
            loadBacktestOllamaModels();
            if (!alpacaStatusPollTimer) {
                alpacaStatusPollTimer = setInterval(pollAlpacaStatus, 30000); // Check every 30s
            }
        }
    }, 500);
});

async function pollAlpacaStatus() {
    if (backtestRequestInProgress || alpacaStatusPollInFlight) return;
    const statusPill = document.getElementById('alpacaLinkStatus');
    const statusDot = document.getElementById('alpacaStatusDot');
    const statusText = document.getElementById('alpacaStatusText');
    const btnUnlink = document.getElementById('btnUnlinkAlpaca');
    if (!statusPill) return;
    if (window.AlpacaStatusCache?.isFresh()) {
        window.AlpacaStatusCache.render();
        return;
    }

    try {
        alpacaStatusPollInFlight = true;
        const headers = await getAuthHeaders();
        const response = await fetch(`${window.BACKTEST_API_BASE}/api/alpaca_status`, { headers });
        const data = await response.json();
        window.lastBacktestData = {
            ...(window.lastBacktestData || {}),
            ...data,
            indicator_overrides: window.lastBacktestData?.indicator_overrides || {},
            indicator_parameters: window.lastBacktestData?.indicator_parameters || {},
        };
        window.AlpacaStatusCache?.writeFromData(data);
        if (window.AlpacaStatusCache?.render()) return;

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
    } finally {
        alpacaStatusPollInFlight = false;
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
            window.AlpacaStatusCache?.clear();
            window.AlpacaStatusCache?.render({ status: 'disconnected', has_keys: false, simulation: true, updatedAt: Date.now() });
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

function btReplaySelectedIndicators() {
    const mode = currentBtMode === 'options' ? 'options' : 'regular';
    return getBtCheckedIndicatorValues(mode);
}

function btPlaceChartInResultSlot() {
    const chart = document.getElementById('btChartSection');
    const slot = document.getElementById('btChartSectionSlot');
    if (chart && slot && chart.parentElement !== slot) slot.appendChild(chart);
}

function btPlaceChartInManualSlot() {
    const chart = document.getElementById('btChartSection');
    const slot = document.getElementById('btManualChartSlot');
    if (chart && slot && chart.parentElement !== slot) slot.appendChild(chart);
}

function btPrepareChartDataFromResults(res) {
    const s = res?.summary || {};
    btChartTimeframeRef = s.timeframe || window.lastBacktestRun?.timeframe || null;
    btChartCandlesRef = btFilterCandlesForChartSession(res?.candles || []);
    btChartTradesRef = btFilterTradesForChartSession([...(res?.trades || [])].reverse()); // oldest-first chronological order
    btStrategyChartTradesRef = [...btChartTradesRef];
    btChartEquityCurveRef = btFilterEquityForChartSession(res?.equity_curve || []);
    btChartTradeIdx = 0;
    applyBtPriorDayBufferToCachedCandles();
}

function btRenderPreparedChart(destination = 'result') {
    const chartEl = document.getElementById('btChartSection');
    if (!chartEl) return false;
    // Manual testing now lives in the dedicated bots-style modal. The result
    // chart stays strategy-only even if an old caller passes "manual".
    const manualDestination = false;
    const preparedStrategyTrades = manualDestination
        ? []
        : [...((btStrategyChartTradesRef && btStrategyChartTradesRef.length) ? btStrategyChartTradesRef : btChartTradesRef)];
    destroyBtChartOnly();
    btStrategyChartTradesRef = preparedStrategyTrades;
    btChartTradesRef = manualDestination ? [] : [...preparedStrategyTrades];
    if (manualDestination) btChartEquityCurveRef = [];
    btManualChartEnabled = manualDestination;
    const manualToolbar = document.getElementById('btManualTradeToolbar');
    if (manualToolbar) manualToolbar.classList.toggle('hidden', !btManualChartEnabled);
    const replayModeLabel = document.getElementById('btReplayModeLabel');
    const replayModeHint = document.getElementById('btReplayModeHint');
    if (replayModeLabel) replayModeLabel.textContent = btManualChartEnabled ? 'Manual trade replay' : 'Strategy result replay';
    if (replayModeHint) {
        replayModeHint.textContent = btManualChartEnabled
            ? 'Manual chart ignores strategy trades; indicators stay visible and TP/SL control replay exit'
            : 'Entries and exits appear from the completed backtest only';
    }
    if (!btManualChartEnabled) {
        btManualPickTarget = null;
        btManualTradePoints = { entry: null, exit: null, sl: null, tp: null };
    }
    btPlaceChartInResultSlot();
    if (btChartCandlesRef.length > 0) {
        chartEl.classList.remove('hidden');
        btResetReplayState(false);
        _btInitChart(btChartCandlesRef, manualDestination ? [] : btChartTradesRef, manualDestination ? [] : btChartEquityCurveRef);
        return true;
    }
    chartEl.classList.add('hidden');
    btResetReplayState(false);
    return false;
}

function btManualPointLabel(point) {
    if (!point) return '—';
    const price = formatAssetPrice(point.price, isCrypto(window.lastBacktestRun?.ticker || document.getElementById('btTicker')?.value || ''));
    const time = new Date(Number(point.time) * 1000).toLocaleString('en-US', {
        timeZone: 'America/Chicago',
        month: 'short',
        day: 'numeric',
        hour: '2-digit',
        minute: '2-digit',
    });
    return `$${price} @ ${time}`;
}

function btManualTargetHitOnCandle(point, candle) {
    if (!point || !candle) return false;
    const price = Number(point.price);
    const high = Number(candle.high);
    const low = Number(candle.low);
    return Number.isFinite(price) && Number.isFinite(high) && Number.isFinite(low) && low <= price && price <= high;
}

function btManualResolvedExit() {
    const entry = btManualTradePoints?.entry;
    if (!entry) return null;
    const entryTime = Number(entry.time);
    let resolved = null;
    const chartTimeframe = getBtChartTimeframe();
    const candles = [...(btChartCandlesRef || [])].sort((a, b) => Number(a.time) - Number(b.time));
    for (const candle of candles) {
        const displayTime = btDisplayTime(Number(candle.time), chartTimeframe);
        if (!Number.isFinite(Number(displayTime)) || Number(displayTime) <= entryTime) continue;
        // Conservative same-candle ordering: SL wins if both TP and SL are touched.
        if (btManualTargetHitOnCandle(btManualTradePoints.sl, candle)) {
            resolved = { ...btManualTradePoints.sl, time: Number(displayTime), kind: 'sl' };
            break;
        }
        if (btManualTargetHitOnCandle(btManualTradePoints.tp, candle)) {
            resolved = { ...btManualTradePoints.tp, time: Number(displayTime), kind: 'tp' };
            break;
        }
    }
    return resolved && Number.isFinite(Number(resolved.time)) && Number.isFinite(Number(resolved.price)) ? resolved : null;
}

function btManualTradeIsComplete() {
    return Boolean(btManualTradePoints?.entry && btManualResolvedExit());
}

function btManualTradePl() {
    const entry = Number(btManualTradePoints?.entry?.price);
    const exit = Number(btManualResolvedExit()?.price);
    if (!Number.isFinite(entry) || !Number.isFinite(exit)) return { pl: 0, pct: 0 };
    const pl = exit - entry;
    const pct = entry > 0 ? (pl / entry) * 100 : 0;
    return { pl, pct };
}

function btManualTradeObject() {
    const resolvedExit = btManualResolvedExit();
    if (!btManualTradePoints?.entry || !resolvedExit) return null;
    const { pl, pct } = btManualTradePl();
    const ticker = window.lastBacktestRun?.ticker || document.getElementById('btTicker')?.value || 'Manual';
    return {
        manual: true,
        symbol: ticker,
        side: 'long',
        quantity: 1,
        entry_price: Number(btManualTradePoints.entry.price),
        exit_price: Number(resolvedExit.price),
        entry_display_time: Number(btManualTradePoints.entry.time),
        exit_display_time: Number(resolvedExit.time),
        entry_time: new Date(Number(btManualTradePoints.entry.time) * 1000).toISOString(),
        exit_time: new Date(Number(resolvedExit.time) * 1000).toISOString(),
        pl,
        pl_pct: pct,
        reason: 'Manual chart entry',
        exit_reason: resolvedExit.kind === 'tp' ? 'Manual TP hit' : 'Manual SL hit',
        stop_loss_price: btManualTradePoints.sl ? Number(btManualTradePoints.sl.price) : null,
        take_profit_price: btManualTradePoints.tp ? Number(btManualTradePoints.tp.price) : null,
    };
}

function btManualTradeMarkers() {
    const markers = [];
    const entry = btManualTradePoints?.entry;
    const exit = btManualResolvedExit();
    if (entry) {
        markers.push({
            time: Number(entry.time),
            position: 'belowBar',
            color: '#111827',
            shape: 'arrowUp',
            text: `Manual Entry $${formatAssetPrice(entry.price, false)}`,
            size: 2,
        });
    }
    if (exit) {
        const { pct } = btManualTradePl();
        const exitLabel = exit.kind === 'tp' ? 'TP Hit' : 'SL Hit';
        markers.push({
            time: Number(exit.time),
            position: 'aboveBar',
            color: pct >= 0 ? '#10b981' : '#ef4444',
            shape: 'arrowDown',
            text: `${exitLabel} ${pct >= 0 ? '+' : ''}${pct.toFixed(2)}%`,
            size: 2,
        });
    }
    return markers;
}

function btRemoveManualTradePriceLines() {
    if (!btCandleSeries) return;
    [btManualEntryPriceLine, btManualExitPriceLine, btManualStopPriceLine, btManualTargetPriceLine].forEach(line => {
        if (line) {
            try { btCandleSeries.removePriceLine(line); } catch (e) {}
        }
    });
    btManualEntryPriceLine = null;
    btManualExitPriceLine = null;
    btManualStopPriceLine = null;
    btManualTargetPriceLine = null;
}

function btManualCreatePriceLine(point, title, color, style = 0) {
    if (!btCandleSeries || !point || !Number.isFinite(Number(point.price))) return null;
    return btCandleSeries.createPriceLine({
        price: Number(point.price),
        color,
        lineWidth: 2,
        lineStyle: style,
        axisLabelVisible: true,
        title,
    });
}

function btDrawManualTradePriceLines() {
    btRemoveManualTradePriceLines();
    btManualEntryPriceLine = btManualCreatePriceLine(btManualTradePoints.entry, 'ENTRY', '#111827', 0);
    btManualExitPriceLine = null;
    btManualTargetPriceLine = btManualCreatePriceLine(btManualTradePoints.tp, '', '#10b981', 2);
    btManualStopPriceLine = btManualCreatePriceLine(btManualTradePoints.sl, '', '#ef4444', 2);
}

function btClearManualRiskRewardAreas() {
    (btManualRiskRewardEls || []).forEach(el => {
        try { el.remove(); } catch (e) {}
    });
    btManualRiskRewardEls = [];
}

function btManualRiskRewardEndTime() {
    const entryTime = Number(btManualTradePoints?.entry?.time);
    const exitTime = Number(btManualResolvedExit()?.time);
    if (!Number.isFinite(entryTime)) return null;
    let endTime = Number.isFinite(exitTime) ? exitTime : null;
    if (btReplayActive && Number.isFinite(Number(btManualReplayVisibleTime))) {
        const replayTime = Number(btManualReplayVisibleTime);
        if (replayTime < entryTime) return null;
        endTime = endTime == null ? replayTime : Math.min(endTime, replayTime);
    }
    if (endTime == null) {
        const visibleRange = btChart?.timeScale?.().getVisibleRange?.();
        endTime = Number(visibleRange?.to);
    }
    if (!Number.isFinite(endTime) || endTime <= entryTime) {
        const next = (btChartCandlesRef || [])
            .map(c => btDisplayTime(Number(c.time), getBtChartTimeframe()))
            .find(time => Number(time) > entryTime);
        endTime = Number.isFinite(Number(next)) ? Number(next) : entryTime + getBtChartTimeframeSeconds();
    }
    return endTime;
}

function btCreateManualRiskRewardArea({ top, bottom, left, right, color, borderColor, title, metric }) {
    const wrap = document.getElementById('btChartWrap');
    if (!wrap) return;
    const width = Math.max(2, right - left);
    const height = Math.max(2, bottom - top);
    const box = document.createElement('div');
    box.className = 'bt-manual-risk-area';
    box.style.position = 'absolute';
    box.style.pointerEvents = 'none';
    box.style.zIndex = '4';
    box.style.left = `${left}px`;
    box.style.top = `${top}px`;
    box.style.width = `${width}px`;
    box.style.height = `${height}px`;
    box.style.background = color;
    box.style.border = `1px solid ${borderColor}`;
    box.style.borderRadius = '10px';
    box.style.boxShadow = 'inset 0 0 0 1px rgba(255,255,255,0.18)';
    box.innerHTML = `
        <div style="position:absolute;right:6px;top:5px;font-size:10px;font-weight:900;letter-spacing:.08em;color:${borderColor};text-transform:uppercase;background:rgba(255,255,255,.72);border-radius:999px;padding:2px 6px;backdrop-filter:blur(4px);">
            ${title}${metric ? ` · ${metric}` : ''}
        </div>
    `;
    wrap.appendChild(box);
    btManualRiskRewardEls.push(box);
}

function btRefreshManualRiskRewardAreas() {
    btClearManualRiskRewardAreas();
    if (!btManualChartEnabled || !btChart || !btCandleSeries || !btManualTradePoints?.entry) return;
    const entry = Number(btManualTradePoints.entry.price);
    if (!Number.isFinite(entry)) return;
    const startTime = Number(btManualTradePoints.entry.time);
    const endTime = btManualRiskRewardEndTime();
    if (!Number.isFinite(startTime) || !Number.isFinite(Number(endTime))) return;
    const x1 = btChart.timeScale().timeToCoordinate(startTime);
    const x2 = btChart.timeScale().timeToCoordinate(Number(endTime));
    const entryY = btCandleSeries.priceToCoordinate(entry);
    if (!Number.isFinite(Number(x1)) || !Number.isFinite(Number(x2)) || !Number.isFinite(Number(entryY))) return;
    const left = Math.min(Number(x1), Number(x2));
    let right = Math.max(Number(x1), Number(x2));
    
    if (!btManualTradeIsComplete()) {
        let barSpacing = 10;
        try {
            const range = btChart.timeScale().getVisibleLogicalRange();
            const px = btChart.timeScale().width();
            if (range && px) barSpacing = px / (range.to - range.from);
        } catch(e) {}
        right += Math.max(10, Math.round(barSpacing * 10));
    }

    const tp = Number(btManualTradePoints?.tp?.price);
    if (Number.isFinite(tp) && tp !== entry) {
        const tpY = btCandleSeries.priceToCoordinate(tp);
        if (Number.isFinite(Number(tpY))) {
            const pct = Math.abs((tp - entry) / entry * 100);
            btCreateManualRiskRewardArea({
                top: Math.min(Number(tpY), Number(entryY)),
                bottom: Math.max(Number(tpY), Number(entryY)),
                left,
                right,
                color: 'rgba(16, 185, 129, 0.16)',
                borderColor: '#10b981',
                title: 'TP',
                metric: `+${pct.toFixed(2)}%`,
            });
        }
    }

    const sl = Number(btManualTradePoints?.sl?.price);
    if (Number.isFinite(sl) && sl !== entry) {
        const slY = btCandleSeries.priceToCoordinate(sl);
        if (Number.isFinite(Number(slY))) {
            const pct = Math.abs((entry - sl) / entry * 100);
            btCreateManualRiskRewardArea({
                top: Math.min(Number(slY), Number(entryY)),
                bottom: Math.max(Number(slY), Number(entryY)),
                left,
                right,
                color: 'rgba(239, 68, 68, 0.15)',
                borderColor: '#ef4444',
                title: 'SL',
                metric: `-${pct.toFixed(2)}%`,
            });
        }
    }
}

function btUpdateManualTradeStatus() {
    const status = document.getElementById('btManualTradeStatus');
    if (!status) return;
    const waiting = btManualPickTarget ? `Click chart to place ${btManualPickTarget.toUpperCase()}` : null;
    const { pl, pct } = btManualTradePl();
    const resolvedExit = btManualResolvedExit();
    const resolvedLabel = resolvedExit?.kind === 'tp' ? `Resolved Exit TP ${btManualPointLabel(resolvedExit)}`
        : (resolvedExit?.kind === 'sl' ? `Resolved Exit SL ${btManualPointLabel(resolvedExit)}` : null);
    const summary = [
        `Entry ${btManualPointLabel(btManualTradePoints.entry)}`,
        btManualTradePoints.tp ? `TP ${btManualPointLabel(btManualTradePoints.tp)}` : null,
        btManualTradePoints.sl ? `SL ${btManualPointLabel(btManualTradePoints.sl)}` : null,
        resolvedLabel,
    ].filter(Boolean).join(' · ');
    if (waiting) {
        status.textContent = waiting;
        status.className = 'ml-auto text-[0.6rem] font-black text-indigo-700';
    } else if (btManualTradeIsComplete()) {
        status.textContent = `${summary} · P/L ${pl >= 0 ? '+' : ''}$${pl.toFixed(2)} (${pct >= 0 ? '+' : ''}${pct.toFixed(2)}%)`;
        status.className = `ml-auto text-[0.6rem] font-black ${pl >= 0 ? 'text-emerald-700' : 'text-rose-700'}`;
    } else {
        status.textContent = summary || 'Pick Entry, TP Area, or SL Area, then click the chart.';
        status.className = 'ml-auto text-[0.6rem] font-bold text-emerald-700';
    }
}

function btApplyManualTradeLayer() {
    if (!btManualChartEnabled || !btCandleSeries) return;
    btDrawManualTradePriceLines();
    btRefreshManualRiskRewardAreas();
    const manualTrade = btManualTradeObject();
    if (manualTrade) {
        btChartTradesRef = [manualTrade];
        btChartTradeIdx = 0;
        btTradeMarkersRef = btManualTradeMarkers();
    } else {
        btChartTradesRef = [];
        btTradeMarkersRef = btManualTradeMarkers();
    }
    btChartMarkersRef = btMergeMarkersForToggles();
    btCandleSeries.setMarkers(btChartMarkersRef);
    btReplayBaseMarkersRef = btMergeMarkersForToggles(btTradeMarkersRef, btIndicatorMarkersRef);
    const countEl = document.getElementById('btChartTradeCount');
    if (countEl) {
        countEl.textContent = manualTrade ? '1 manual TP/SL test plotted' : 'Manual TP/SL testing · no strategy trades';
    }
    _btUpdateNavLabel();
    btReplayUpdateControls();
    btUpdateManualTradeStatus();
}

function btSetManualTradePoint(kind, time, price) {
    if (!['entry', 'sl', 'tp'].includes(kind)) return;
    if (!Number.isFinite(Number(time)) || !Number.isFinite(Number(price))) return;
    btManualTradePoints[kind] = { time: Number(time), price: Number(price) };
    btManualPickTarget = null;
    btApplyManualTradeLayer();
}

function btAttachManualChartClickHandler() {
    if (!btChart || !btCandleSeries) return;
    btManualChartClickHandler = param => {
        if (!btManualChartEnabled || !btManualPickTarget || !param?.point || param.time == null) return;
        const price = btCandleSeries.coordinateToPrice(param.point.y);
        if (!Number.isFinite(Number(price))) return;
        btSetManualTradePoint(btManualPickTarget, Number(param.time), Number(price));
    };
    try { btChart.subscribeClick(btManualChartClickHandler); } catch (e) {}
}

window.btSelectManualTradePoint = function(kind) {
    if (!btManualChartEnabled) return;
    btManualPickTarget = kind;
    btUpdateManualTradeStatus();
};

window.btClearManualTrade = function() {
    btManualPickTarget = null;
    btManualTradePoints = { entry: null, exit: null, sl: null, tp: null };
    btApplyManualTradeLayer();
};

function manualBtFormatPrice(value) {
    const n = Number(value);
    if (!Number.isFinite(n)) return '---';
    return n >= 1 ? n.toFixed(2) : n.toFixed(4);
}

function manualBtFormatVolume(value) {
    const n = Number(value);
    if (!Number.isFinite(n)) return '—';
    if (n >= 1e6) return `${(n / 1e6).toFixed(2)}M`;
    if (n >= 1e3) return `${(n / 1e3).toFixed(1)}K`;
    return String(Math.round(n));
}

function manualBtSeededIndicatorPrefs() {
    const prefs = {};
    MANUAL_BT_CHART_PREVIEW_INDICATORS.forEach(name => {
        if (name !== 'Botbulls4 News AI') prefs[name] = false;
    });
    MANUAL_BT_DEFAULT_INDICATORS.forEach(name => {
        if (Object.prototype.hasOwnProperty.call(prefs, name)) prefs[name] = true;
    });
    return prefs;
}

function manualBtIndicatorPrefs() {
    try {
        const raw = localStorage.getItem(MANUAL_BT_INDICATOR_PREF_KEY);
        if (raw) {
            const parsed = JSON.parse(raw);
            const seeded = manualBtSeededIndicatorPrefs();
            MANUAL_BT_CHART_PREVIEW_INDICATORS.forEach(name => {
                if (name === 'Botbulls4 News AI') return;
                if (Object.prototype.hasOwnProperty.call(parsed || {}, name)) {
                    seeded[name] = parsed[name] === true;
                }
            });
            return seeded;
        }
    } catch (e) {
        console.warn('[manual-backtest] Could not read manual indicator prefs:', e);
    }
    const seeded = manualBtSeededIndicatorPrefs();
    manualBtSaveIndicatorPrefs(seeded);
    return seeded;
}

function manualBtSaveIndicatorPrefs(prefs) {
    try {
        localStorage.setItem(MANUAL_BT_INDICATOR_PREF_KEY, JSON.stringify(prefs || {}));
    } catch (e) {
        console.warn('[manual-backtest] Could not save manual indicator prefs:', e);
    }
}

function manualBtSourcePref() {
    const raw = (document.getElementById('manualBtDataSourceSelect')?.value || manualBtSourcePreference || 'webull').toLowerCase();
    return raw === 'yahoo' ? 'yahoo' : 'webull';
}

function manualBtStableJson(value) {
    if (!value || typeof value !== 'object') return JSON.stringify(value ?? null);
    if (Array.isArray(value)) return `[${value.map(manualBtStableJson).join(',')}]`;
    return `{${Object.keys(value).sort().map(key => `${JSON.stringify(key)}:${manualBtStableJson(value[key])}`).join(',')}}`;
}

function manualBtReplayCacheKey({ ticker, timeframe, days, source, marketSession, indicators, overrides }) {
    return [
        String(ticker || '').toUpperCase(),
        String(timeframe || ''),
        String(days || ''),
        String(source || 'webull').toLowerCase(),
        String(marketSession || 'Regular'),
        manualBtStableJson([...(indicators || [])].sort()),
        manualBtStableJson(overrides || {}),
    ].join('|');
}

function manualBtClonePayload(payload) {
    try {
        return JSON.parse(JSON.stringify(payload));
    } catch (e) {
        return payload;
    }
}

function manualBtCacheReplayPayload(key, payload) {
    if (!key || !payload) return;
    if (manualBtReplayDataCache.has(key)) manualBtReplayDataCache.delete(key);
    manualBtReplayDataCache.set(key, manualBtClonePayload(payload));
    while (manualBtReplayDataCache.size > MANUAL_BT_REPLAY_CACHE_LIMIT) {
        const oldest = manualBtReplayDataCache.keys().next().value;
        manualBtReplayDataCache.delete(oldest);
    }
}

function manualBtSelectedIndicators() {
    const prefs = manualBtIndicatorPrefs();
    const mode = typeof currentBtMode !== 'undefined' && currentBtMode === 'options' ? 'options' : 'regular';
    return MANUAL_BT_CHART_PREVIEW_INDICATORS
        .filter(name => (
            name !== 'Botbulls4 News AI'
            && prefs[name] === true
            && isBacktestIndicatorAllowed(name, mode, manualBtTicker)
        ));
}

function reconcileBacktestIndicatorsWithWorkspace(event) {
    const detail = event?.detail || {};
    if (!String(detail.contextLabel || '').startsWith('backtest-')) return;
    const mode = detail.contextLabel.endsWith('options') ? 'options' : 'regular';
    const shown = new Set(detail.shown || getBacktestWorkspaceShownSet(mode, detail.ticker));
    const selector = mode === 'options' ? '.bt-indicator-check-options' : '.bt-indicator-check-regular';
    document.querySelectorAll(`${selector}:checked`).forEach(checkbox => {
        const name = String(checkbox.value || '').trim();
        if ((window.STANDARD_INDICATOR_NAMES || []).includes(name) && !shown.has(name)) {
            checkbox.checked = false;
        }
    });

    if ((currentBtMode === 'options' ? 'options' : 'regular') === mode) {
        const prefs = manualBtIndicatorPrefs();
        let manualPrefsChanged = false;
        (window.STANDARD_INDICATOR_NAMES || []).forEach(name => {
            if (!shown.has(name) && prefs[name] === true) {
                prefs[name] = false;
                manualPrefsChanged = true;
            }
        });
        if (manualPrefsChanged) manualBtSaveIndicatorPrefs(prefs);
    }

    window.updateSignalLabels?.(mode);
    if (manualBtChart || manualBtHistory.length) manualBtRefreshIndicatorsOnly();
}

window.addEventListener('indicator-workspace:selection-change', reconcileBacktestIndicatorsWithWorkspace);

function manualBtCleanLineData(data = []) {
    const byTime = new Map();
    (Array.isArray(data) ? data : []).forEach(point => {
        if (!point || !Number.isFinite(Number(point.time)) || !Number.isFinite(Number(point.value))) return;
        byTime.set(Number(point.time), { ...point, time: Number(point.time), value: Number(point.value) });
    });
    return Array.from(byTime.values()).sort((a, b) => Number(a.time) - Number(b.time));
}

function manualBtCleanCandles(data = []) {
    const byTime = new Map();
    (Array.isArray(data) ? data : []).forEach(bar => {
        if (!bar || !Number.isFinite(Number(bar.time))) return;
        if (![bar.open, bar.high, bar.low, bar.close].every(v => Number.isFinite(Number(v)))) return;
        byTime.set(Number(bar.time), {
            ...bar,
            time: Number(bar.time),
            open: Number(bar.open),
            high: Number(bar.high),
            low: Number(bar.low),
            close: Number(bar.close),
            volume: Number(bar.volume || 0),
        });
    });
    return Array.from(byTime.values()).sort((a, b) => Number(a.time) - Number(b.time));
}

function manualBtRangeStart(history) {
    const last = history?.[history.length - 1];
    if (!last) return null;
    const lastTime = Number(last.time);
    const days = {
        '1D': 1,
        '5D': 5,
        '1M': 31,
        '3M': 93,
        '6M': 186,
        '1Y': 366,
        '5Y': 365 * 5,
    }[manualBtRange];
    if (manualBtRange === 'YTD') {
        const d = new Date(lastTime * 1000);
        return Date.UTC(d.getUTCFullYear(), 0, 1) / 1000;
    }
    if (!days) return null;
    return lastTime - days * 86400;
}

function manualBtMaxDaysForTimeframe(tf = manualBtTimeframe || '1Hour', source = manualBtSourcePref()) {
    const src = String(source || 'webull').toLowerCase();
    if (src === 'webull') {
        if (tf === '1Min') return 1;
        if (['5Min', '10Min', '15Min', '30Min'].includes(tf)) return 5;
        if (tf === '1Hour') return 31;
        if (tf === '4Hour') return 93;
    }
    if (tf === '1Min') return 7;
    if (['5Min', '10Min', '15Min', '30Min'].includes(tf)) return 60;
    if (['1Hour', '4Hour'].includes(tf)) return 730;
    return 1825;
}

function manualBtRangeDays(range = manualBtRange, maxDays = manualBtMaxDaysForTimeframe()) {
    const fixed = {
        '1D': 1,
        '5D': 5,
        '1M': 31,
        '3M': 93,
        '6M': 186,
        '1Y': 366,
        '5Y': 365 * 5,
        'MAX': maxDays,
    }[range];
    if (Number.isFinite(Number(fixed))) return Number(fixed);
    if (range === 'YTD') {
        const now = new Date();
        const start = Date.UTC(now.getUTCFullYear(), 0, 1);
        return Math.max(1, Math.ceil((Date.now() - start) / 86400000));
    }
    return 31;
}

function manualBtWebullFastRange(tf = manualBtTimeframe || '1Hour') {
    if (tf === '1Min') return '1D';
    if (['5Min', '10Min', '15Min', '30Min'].includes(tf)) return '5D';
    if (tf === '1Hour') return '1M';
    if (tf === '4Hour') return '3M';
    return '5Y';
}

function manualBtCompatibleRange(range = manualBtRange, tf = manualBtTimeframe || '1Hour', source = manualBtSourcePref()) {
    const maxDays = manualBtMaxDaysForTimeframe(tf, source);
    const days = manualBtRangeDays(range, maxDays);
    if (days <= maxDays) return range;
    if (String(source || 'webull').toLowerCase() === 'webull') return manualBtWebullFastRange(tf);
    if (maxDays <= 7) return '5D';
    if (maxDays <= 60) return '1M';
    if (maxDays <= 730) return '1Y';
    return range === 'MAX' ? '5Y' : range;
}

function manualBtSyncDaySpanForView({ adjustRange = true } = {}) {
    const tf = manualBtTimeframe || '1Hour';
    const source = manualBtSourcePref();
    if (adjustRange) manualBtRange = manualBtCompatibleRange(manualBtRange, tf, source);
    const maxDays = manualBtMaxDaysForTimeframe(tf, source);
    manualBtDays = Math.max(1, Math.min(maxDays, Math.ceil(manualBtRangeDays(manualBtRange, maxDays))));
    return manualBtDays;
}

function manualBtLoadOrRenderAfterViewChange() {
    manualBtStopReplay();
    manualBtReplayStartIndex = 0;
    manualBtSyncDaySpanForView();
    if (!manualBtTicker) {
        manualBtRenderChart();
        manualBtSetReplayStatus('Search a ticker to load chart');
        return;
    }
    runBacktest({ chartOnly: true, force: true });
}

function manualBtDisplayTime(time, timeframe = manualBtTimeframe || '1Hour') {
    const sourceTime = Number(time);
    if (!Number.isFinite(sourceTime)) return time;
    const seconds = getBtChartTimeframeSeconds(timeframe);
    return sourceTime + (seconds > 0 && seconds < 86400 ? seconds : 0);
}

function manualBtDisplayCandle(candle, timeframe = manualBtTimeframe || '1Hour') {
    const sourceTime = Number(candle?.source_time ?? candle?.time);
    if (!Number.isFinite(sourceTime)) return candle;
    return {
        ...candle,
        source_time: sourceTime,
        time: manualBtDisplayTime(sourceTime, timeframe),
    };
}

function manualBtMarketSessionKind(bar) {
    const sourceTime = Number(bar?.source_time);
    const d = new Date((Number.isFinite(sourceTime) ? sourceTime : Number(bar?.time)) * 1000);
    const parts = new Intl.DateTimeFormat('en-US', {
        timeZone: 'America/New_York',
        weekday: 'short',
        hour: '2-digit',
        minute: '2-digit',
        hour12: false
    }).formatToParts(d);
    const values = Object.fromEntries(parts.map(p => [p.type, p.value]));
    if (values.weekday === 'Sat' || values.weekday === 'Sun') return 'closed';
    const mins = (Number(values.hour) % 24) * 60 + Number(values.minute);
    if (mins >= 4 * 60 && mins < 9 * 60 + 30) return 'pre';
    if (mins >= 9 * 60 + 30 && mins < 16 * 60) return 'regular';
    if (mins >= 16 * 60 && mins < 20 * 60) return 'after';
    return 'closed';
}

function manualBtIsRegularBar(bar) {
    return manualBtMarketSessionKind(bar) === 'regular';
}

function manualBtIsExtendedHoursBar(bar) {
    const kind = manualBtMarketSessionKind(bar);
    return kind === 'pre' || kind === 'after';
}

function manualBtExtendedHoursBackground(history = []) {
    return (history || []).map(bar => {
        const isExtended = manualBtIsExtendedHoursBar(bar);
        return {
            time: Number(bar.time),
            value: isExtended ? 100 : 0,
            color: isExtended ? 'rgba(100, 116, 139, 0.12)' : 'rgba(0,0,0,0)',
        };
    });
}

function manualBtFilteredHistory(source = manualBtHistory) {
    let history = [...(source || [])].sort((a, b) => Number(a.time) - Number(b.time));
    const start = manualBtRangeStart(history);
    if (Number.isFinite(Number(start))) history = history.filter(bar => Number(bar.time) >= Number(start));
    const ticker = manualBtTicker || '';
    const tf = manualBtTimeframe || '1Hour';
    if (isCrypto(ticker) || manualBtSession === 'crypto24' || String(tf).includes('Day')) return history;
    if (manualBtSession === 'extended') {
        return history.filter(bar => manualBtMarketSessionKind(bar) !== 'closed');
    }
    return history.filter(manualBtIsRegularBar);
}

function manualBtSetText(id, value) {
    const el = document.getElementById(id);
    if (el) el.textContent = value;
}

function manualBtReplayStartText(history, idx) {
    const len = history?.length || 0;
    if (!len) return 'Start: no candles';
    const safeIdx = Math.max(0, Math.min(len - 1, Number(idx) || 0));
    const bar = history[safeIdx];
    const stamp = bar?.time
        ? new Date(Number(bar.time) * 1000).toLocaleString('en-US', {
            timeZone: 'America/Chicago',
            month: 'short',
            day: 'numeric',
            hour: '2-digit',
            minute: '2-digit',
        })
        : 'selected candle';
    return `Start: ${safeIdx + 1}/${len} · ${stamp} CT`;
}

function manualBtUpdateReplayStartControl(history = manualBtFilteredHistory(), syncValue = true) {
    const slider = document.getElementById('manualBtReplayStartSlider');
    const len = history?.length || 0;
    manualBtReplayStartIndex = len
        ? Math.max(0, Math.min(len - 1, Number(manualBtReplayStartIndex) || 0))
        : 0;
    if (slider) {
        slider.disabled = len <= 1 || manualBtReplayActive;
        slider.max = String(Math.max(0, len - 1));
        if (syncValue) slider.value = String(manualBtReplayStartIndex);
    }
    manualBtSetText('manualBtReplayStartLabel', manualBtReplayStartText(history, manualBtReplayStartIndex));
}

function manualBtReplayClockText(bar) {
    if (!bar?.time) return '—';
    return new Date(Number(bar.time) * 1000).toLocaleString('en-US', {
        timeZone: 'America/Chicago',
        month: 'short',
        day: 'numeric',
        year: 'numeric',
        hour: '2-digit',
        minute: '2-digit',
    });
}

function manualBtSetReplayStatus(text) {
    manualBtSetText('manualBtReplayStatus', text);
    manualBtSetText('manualBtReplayStageStatus', text);
}

function manualBtReplayAccountText() {
    const exit = manualBtResolvedExit();
    const entryPrice = Number(manualBtTrade.entry?.price);
    const last = manualBtVisibleHistory?.[manualBtVisibleHistory.length - 1];
    const lastTime = Number(last?.time);
    const exitReached = exit && Number.isFinite(lastTime) && Number(exit.time) <= lastTime;
    if (exitReached && Number.isFinite(entryPrice)) {
        const realized = Number(exit.price) - entryPrice;
        return `Realized ${realized >= 0 ? '+' : '-'}$${Math.abs(realized).toFixed(2)}/share · ${exit.kind.toUpperCase()} hit`;
    }
    if (manualBtTrade.entry && Number.isFinite(entryPrice) && Number.isFinite(Number(last?.close))) {
        const unrealized = Number(last.close) - entryPrice;
        return `Unrealized ${unrealized >= 0 ? '+' : '-'}$${Math.abs(unrealized).toFixed(2)}/share · Manual trade active`;
    }
    return 'Realized +$0.00 · No active manual trade';
}

function manualBtReplayUpdateControls() {
    const history = manualBtFilteredHistory();
    const total = history.length;
    const active = Boolean(manualBtReplayActive && total);
    const cursor = active
        ? Math.max(0, Math.min(total - 1, Number(manualBtReplayCursor) || 0))
        : Math.max(0, Math.min(total - 1, Number(manualBtReplayStartIndex) || 0));
    const toggle = document.getElementById('manualBtReplayToggle');
    if (toggle) toggle.textContent = active ? 'Exit Replay' : 'Start Replay';
    const play = document.getElementById('manualBtReplayPlayBtn');
    if (play) {
        play.disabled = !active;
        play.textContent = manualBtReplayPlaying ? 'Pause' : 'Play';
    }
    ['manualBtReplayBack', 'manualBtReplayForward', 'manualBtReplayReset'].forEach(id => {
        const btn = document.getElementById(id);
        if (btn) btn.disabled = !active;
    });
    const slider = document.getElementById('manualBtReplayFrameSlider');
    if (slider) {
        slider.disabled = !active || total <= 1;
        slider.max = String(Math.max(0, total - 1));
        slider.value = String(cursor);
    }
    const speedEl = document.getElementById('manualBtReplaySpeed');
    if (speedEl && speedEl.value !== String(manualBtReplaySpeed)) speedEl.value = String(manualBtReplaySpeed);
    const candle = history[cursor];
    manualBtSetText('manualBtReplayClock', active ? manualBtReplayClockText(candle) : '—');
    const baseStatus = active
        ? `${manualBtReplayPlaying ? 'Playing' : 'Paused'} · ${cursor + 1}/${total} · ${manualBtReplaySpeed}x`
        : (total ? 'Replay idle' : 'Search/load ticker to replay');
    manualBtSetReplayStatus(manualBtReplayStatusNote || baseStatus);
    manualBtSetText('manualBtReplayAccount', manualBtReplayAccountText());
    document.getElementById('manualBtChartPreviewPlayBadge')?.classList.toggle('hidden', !active);
}

function manualBtUpdateHeader(history = manualBtDisplayHistory) {
    const first = history.find(bar => Number.isFinite(Number(bar.open || bar.close)));
    const last = [...history].reverse().find(bar => Number.isFinite(Number(bar.close)));
    const ticker = manualBtTicker || 'Search Ticker';
    const sourceLabel = String(manualBtDataSource || 'Yahoo').toUpperCase();
    manualBtSetText('manualBtChartPreviewTickerLabel', ticker);
    manualBtSetText('manualBtChartPreviewModeBadge', `${sourceLabel} candles`);
    manualBtSetText('manualBtChartPreviewPrice', last ? manualBtFormatPrice(last.close) : '---');
    manualBtUpdateOhlcvReadout(last);
    const changeEl = document.getElementById('manualBtChartPreviewPriceChange');
    const base = Number(first?.open || first?.close || 0);
    const close = Number(last?.close || 0);
    if (changeEl && base > 0 && close > 0) {
        const change = close - base;
        const pct = change / base * 100;
        changeEl.textContent = `${change >= 0 ? '+' : '-'}${manualBtFormatPrice(Math.abs(change))} (${change >= 0 ? '+' : '-'}${Math.abs(pct).toFixed(2)}%)`;
        changeEl.className = `text-xs font-bold ${change >= 0 ? 'text-emerald-500' : 'text-red-400'}`;
    }
    document.querySelectorAll('#manualBtChartPreviewRangeBar .chart-range-btn').forEach(btn => btn.classList.toggle('chart-range-active', btn.dataset.range === manualBtRange));
    document.querySelectorAll('#manualBtChartPreviewTfBar .chart-tf-btn').forEach(btn => btn.classList.toggle('chart-tf-active', btn.dataset.tf === (manualBtTimeframe || '1Hour')));
    const crypto = isCrypto(ticker);
    document.getElementById('manualBtChartPreviewRegularHoursBtn')?.classList.toggle('hidden', crypto);
    document.getElementById('manualBtChartPreviewExtendedHoursBtn')?.classList.toggle('hidden', crypto);
    document.getElementById('manualBtChartPreviewCryptoLiveBtn')?.classList.toggle('hidden', !crypto);
    document.getElementById('manualBtChartPreviewRegularHoursBtn')?.classList.toggle('chart-session-active', manualBtSession === 'regular');
    document.getElementById('manualBtChartPreviewExtendedHoursBtn')?.classList.toggle('chart-session-active', manualBtSession === 'extended');
    document.getElementById('manualBtChartPreviewCryptoLiveBtn')?.classList.toggle('chart-session-active', manualBtSession === 'crypto24');
}

function manualBtUpdateOhlcvReadout(bar) {
    manualBtSetText('manualBtChartPreviewOhlcvO', bar ? manualBtFormatPrice(bar.open) : '—');
    manualBtSetText('manualBtChartPreviewOhlcvH', bar ? manualBtFormatPrice(bar.high) : '—');
    manualBtSetText('manualBtChartPreviewOhlcvL', bar ? manualBtFormatPrice(bar.low) : '—');
    manualBtSetText('manualBtChartPreviewOhlcvC', bar ? manualBtFormatPrice(bar.close) : '—');
    manualBtSetText('manualBtChartPreviewOhlcvV', bar ? manualBtFormatVolume(bar.volume) : '—');
}

function manualBtRemoveSeriesMap(map, chart) {
    Object.values(map || {}).forEach(series => {
        try { chart?.removeSeries(series); } catch (e) {}
    });
}

function manualBtDestroyChart(preserveReplay = false) {
    if (!preserveReplay) manualBtStopReplay();
    if (manualBtChart && manualBtClickHandler) {
        try { manualBtChart.unsubscribeClick(manualBtClickHandler); } catch (e) {}
    }
    const container = document.getElementById('manualBtChartPreviewChart');
    if (container && manualBtDragMousedownHandler) {
        container.removeEventListener('mousedown', manualBtDragMousedownHandler);
    }
    if (manualBtDragMouseupHandler) {
        window.removeEventListener('mouseup', manualBtDragMouseupHandler);
        window.removeEventListener('mousemove', manualBtDragMouseupHandler); // if we use it
    }
    manualBtClickHandler = null;
    manualBtDragMousedownHandler = null;
    manualBtDragMouseupHandler = null;
    manualBtClearRiskAreas();
    if (manualBtResizeObserver) {
        try { manualBtResizeObserver.disconnect(); } catch (e) {}
        manualBtResizeObserver = null;
    }
    Object.values(manualBtOscCharts || {}).forEach(chart => {
        try { chart.remove(); } catch (e) {}
    });
    manualBtOscCharts = {};
    manualBtOscSeries = {};
    window.StandardIndicatorSubcharts?.destroy('backtest');
    if (manualBtChart) {
        try { manualBtChart.remove(); } catch (e) {}
    }
    manualBtChart = null;
    manualBtCandleSeries = null;
    manualBtVolumeSeries = null;
    manualBtExtHoursSeries = null;
    manualBtOverlaySeries = {};
    manualBtOverlayPriceLines = {};
    manualBtOverlayLabelMeta = {};
    manualBtPriceLines = [];
}

function manualBtAddLine(key, data, options = {}) {
    if (!manualBtChart) return null;
    const byTime = new Map();
    (Array.isArray(data) ? data : []).forEach(point => {
        const time = Number(point?.time);
        if (!Number.isFinite(time)) return;
        const rawValue = point?.value;
        const value = Number(rawValue);
        byTime.set(time, rawValue !== null && rawValue !== undefined && Number.isFinite(value)
            ? { ...point, time, value }
            : { time });
    });
    const clean = Array.from(byTime.values()).sort((left, right) => left.time - right.time);
    if (clean.filter(point => Number.isFinite(Number(point?.value))).length < 2) return null;
    const series = manualBtChart.addLineSeries({
        priceLineVisible: false,
        lastValueVisible: false,
        crosshairMarkerVisible: false,
        ...options,
    });
    series.setData(clean);
    manualBtOverlaySeries[key] = series;
    return series;
}

function manualBtIndicatorParamValue(key, fallback) {
    const defaults = window.BT_INDICATOR_DEFAULTS || window.STANDARD_INDICATOR_DEFAULTS || {};
    const parameters = window.lastBacktestData?.indicator_parameters || {};
    const overrides = typeof getIndicatorOverrides === 'function'
        ? (getIndicatorOverrides() || {})
        : (window.lastBacktestData?.indicator_overrides || {});
    const raw = overrides[key] ?? parameters[key] ?? defaults[key] ?? fallback;
    const parsed = Number(raw);
    return raw !== '' && Number.isFinite(parsed) ? parsed : Number(fallback);
}

function manualBtRightVisiblePoint(data) {
    if (!Array.isArray(data) || !data.length) return null;
    let from = NaN;
    let to = NaN;
    try {
        const range = manualBtChart?.timeScale?.().getVisibleRange?.();
        from = Number(range?.from);
        to = Number(range?.to);
    } catch (e) {}
    if (Number.isFinite(to)) {
        for (let index = data.length - 1; index >= 0; index -= 1) {
            const pointTime = Number(data[index]?.time);
            if (pointTime <= to && (!Number.isFinite(from) || pointTime >= from)) return data[index];
        }
        for (let index = data.length - 1; index >= 0; index -= 1) {
            if (Number(data[index]?.time) <= to) return data[index];
        }
    }
    return data[data.length - 1];
}

function manualBtRefreshOverlayPriceLineLabel(key) {
    const meta = manualBtOverlayLabelMeta[key];
    const series = manualBtOverlaySeries[key];
    if (!meta || !series) return;
    if (manualBtOverlayPriceLines[key]) {
        try { series.removePriceLine(manualBtOverlayPriceLines[key]); } catch (e) {}
        delete manualBtOverlayPriceLines[key];
    }
    const price = Number(manualBtRightVisiblePoint(meta.data)?.value);
    if (!Number.isFinite(price) || !meta.labelTitle) return;
    manualBtOverlayPriceLines[key] = series.createPriceLine({
        price,
        color: meta.options.color,
        lineWidth: 1,
        lineStyle: meta.options.lineStyle ?? 0,
        axisLabelVisible: true,
        lineVisible: false,
        title: meta.labelTitle,
    });
}

function manualBtRefreshOverlayPriceLineLabels() {
    Object.keys(manualBtOverlayLabelMeta).forEach(manualBtRefreshOverlayPriceLineLabel);
}

function manualBtAddLabeledLine(key, data, options, labelTitle) {
    const clean = manualBtCleanLineData(data);
    const series = manualBtAddLine(key, clean, options);
    if (!series) return null;
    manualBtOverlayLabelMeta[key] = { data: clean, options, labelTitle };
    manualBtRefreshOverlayPriceLineLabel(key);
    return series;
}

function manualBtClearOverlaySeries() {
    Object.entries(manualBtOverlaySeries || {}).forEach(([key, series]) => {
        if (manualBtOverlayPriceLines[key]) {
            try { series.removePriceLine(manualBtOverlayPriceLines[key]); } catch (e) {}
        }
        try { manualBtChart?.removeSeries(series); } catch (e) {}
    });
    manualBtOverlaySeries = {};
    manualBtOverlayPriceLines = {};
    manualBtOverlayLabelMeta = {};
}

function manualBtLineData(history, key) {
    return (history || [])
        .filter(bar => bar && bar[key] !== null && bar[key] !== undefined && Number.isFinite(Number(bar[key])))
        .map(bar => ({ time: Number(bar.time), value: Number(bar[key]) }));
}

function manualBtRenderOverlays(history) {
    manualBtOverlaySeries = {};
    manualBtOverlayPriceLines = {};
    manualBtOverlayLabelMeta = {};
    const enabled = new Set(manualBtSelectedIndicators());
    if (enabled.has('EMA Cross')) {
        manualBtAddLine('ema_fast', manualBtLineData(history, 'ema_fast'), { color: '#3b82f6', lineWidth: 1.5 });
        manualBtAddLine('ema_slow', manualBtLineData(history, 'ema_slow'), { color: '#ef4444', lineWidth: 1.5 });
    }
    if (enabled.has('VWAP')) manualBtAddLine('vwap', manualBtLineData(history, 'vwap'), { color: '#000000', lineWidth: 2 });
    if (enabled.has('SMA')) manualBtAddLine('sma', manualBtLineData(history, 'sma'), { color: '#8b5cf6', lineWidth: 2.5 });
    if (enabled.has('Supertrend')) {
        manualBtAddLine('supertrend', (history || [])
            .filter(bar => bar && Number.isFinite(Number(bar.supertrend)))
            .map(bar => ({ time: Number(bar.time), value: Number(bar.supertrend), color: btIsSupertrendBullish(bar) ? '#10b981' : '#ef4444' })), { lineWidth: 2, lineType: 0 });
    }
    if (enabled.has('Bollinger')) {
        manualBtAddLine('boll_upper', manualBtLineData(history, 'boll_upper'), { color: '#f97316', lineWidth: 1.5 });
        manualBtAddLine('boll_middle', manualBtLineData(history, 'boll_middle'), { color: '#3b82f6', lineWidth: 1, lineStyle: 2 });
        manualBtAddLine('boll_lower', manualBtLineData(history, 'boll_lower'), { color: '#a855f7', lineWidth: 1.5 });
    }
    if (enabled.has('Volume Profile')) {
        manualBtAddLabeledLine('volume_profile_poc', manualBtLineData(history, 'volume_profile_poc'), { color: '#7c2d12', lineWidth: 3 }, 'POC');
        manualBtAddLabeledLine('volume_profile_vah', manualBtLineData(history, 'volume_profile_vah'), { color: '#ea580c', lineWidth: 2, lineStyle: 2 }, 'VAH');
        manualBtAddLabeledLine('volume_profile_val', manualBtLineData(history, 'volume_profile_val'), { color: '#0f766e', lineWidth: 2, lineStyle: 2 }, 'VAL');
    }
    if (enabled.has('Prior Day High/Low')) {
        manualBtAddLabeledLine('daily_high_low_high', manualBtLineData(history, 'daily_high_low_high'), { color: '#d97706', lineWidth: 3, lineStyle: 1 }, 'PDH');
        manualBtAddLabeledLine('daily_high_low_low', manualBtLineData(history, 'daily_high_low_low'), { color: '#0891b2', lineWidth: 3, lineStyle: 1 }, 'PDL');
        manualBtAddLine('daily_high_low_high_trigger', manualBtLineData(history, 'daily_high_low_high_trigger'), { color: 'rgba(217,119,6,0.75)', lineWidth: 2, lineStyle: 1 });
        manualBtAddLine('daily_high_low_low_trigger', manualBtLineData(history, 'daily_high_low_low_trigger'), { color: 'rgba(8,145,178,0.75)', lineWidth: 2, lineStyle: 1 });
    }
    if (enabled.has('Pivot Points (S/R)')) {
        const levelCount = Math.min(5, Math.max(1, Math.round(manualBtIndicatorParamValue('PIVOT_LEVEL_COUNT', 1))));
        manualBtAddLabeledLine('pivot_p', manualBtLineData(history, 'pivot_p'), { color: '#1f2937', lineWidth: 3, lineStyle: 2 }, 'P');
        [1, 2, 3, 4, 5].slice(0, levelCount).forEach(level => {
            manualBtAddLabeledLine(`pivot_r${level}`, manualBtLineData(history, `pivot_r${level}`), { color: level === 1 ? '#dc2626' : 'rgba(220,38,38,0.78)', lineWidth: 2, lineStyle: level === 1 ? 1 : 2 }, `R${level}`);
            manualBtAddLabeledLine(`pivot_s${level}`, manualBtLineData(history, `pivot_s${level}`), { color: level === 1 ? '#059669' : 'rgba(5,150,105,0.78)', lineWidth: 2, lineStyle: level === 1 ? 1 : 2 }, `S${level}`);
        });
    }
    if (enabled.has('Swing Points')) {
        const levelCount = Math.min(5, Math.max(1, Math.round(manualBtIndicatorParamValue('SWING_POINTS_LEVEL_COUNT', 1))));
        [1, 2, 3, 4, 5].slice(0, levelCount).forEach(level => {
            manualBtAddLabeledLine(`swing_support_${level}`, manualBtLineData(history, `swing_support_${level}`), { color: level === 1 ? '#16a34a' : 'rgba(22,163,74,0.72)', lineWidth: level === 1 ? 3 : 2, lineStyle: level === 1 ? 1 : 2 }, `SW S${level}`);
            manualBtAddLabeledLine(`swing_resistance_${level}`, manualBtLineData(history, `swing_resistance_${level}`), { color: level === 1 ? '#dc2626' : 'rgba(220,38,38,0.72)', lineWidth: level === 1 ? 3 : 2, lineStyle: level === 1 ? 1 : 2 }, `SW R${level}`);
        });
    }
    if (enabled.has('BotBulls2')) {
        manualBtAddLine('lx_smart_trail', (history || [])
            .filter(bar => bar && Number.isFinite(Number(bar.lx_smart_trail)))
            .map(bar => ({
                time: Number(bar.time),
                value: Number(bar.lx_smart_trail),
                color: bar.lx_smart_trend ? '#10b981' : '#ef4444'
            })), { lineWidth: 2.5, lineType: 0 });
        manualBtAddLine('lx_trend_tracer', manualBtLineData(history, 'lx_trend_tracer'), { color: '#8b5cf6', lineWidth: 1.5 });
        manualBtAddLine('lx_rz_upper', manualBtLineData(history, 'lx_rz_upper'), { color: '#f59e0b', lineWidth: 1, lineStyle: 1 });
        manualBtAddLine('lx_rz_lower', manualBtLineData(history, 'lx_rz_lower'), { color: '#ec4899', lineWidth: 1, lineStyle: 1 });
    }
    if (enabled.has('BotBulls3')) {
        manualBtAddLine('ut_trail', (history || [])
            .filter(bar => bar && Number.isFinite(Number(bar.ut_trail)))
            .map(bar => ({
                time: Number(bar.time),
                value: Number(bar.ut_trail),
                color: bar.ut_above ? '#0ea5e9' : '#f43f5e'
            })), { lineWidth: 2.5, lineType: 0 });
    }
    (window.STANDARD_EXTENSION_OVERLAYS || []).forEach(definition => {
        if (!enabled.has(definition.name)) return;
        definition.series.forEach(spec => {
            const overlayParameters = spec.shiftPeriodsKey ? {
                [spec.shiftPeriodsKey]: manualBtIndicatorParamValue(spec.shiftPeriodsKey, spec.shiftPeriodsFallback || 0),
            } : {};
            if (definition.name === 'BotBulls5') {
                overlayParameters.rolling24Hours = isCrypto(manualBtTicker || '');
            }
            const overlayData = window.StandardIndicatorSubcharts?.buildOverlayData
                ? window.StandardIndicatorSubcharts.buildOverlayData(history, spec, overlayParameters)
                : manualBtLineData(history, spec.field);
            manualBtAddLine(`standard_${spec.field}`, overlayData, {
                color: spec.color,
                lineWidth: spec.lineWidth || 1,
                lineStyle: spec.lineStyle || 0,
                lineVisible: spec.lineVisible !== false,
                pointMarkersVisible: spec.pointMarkersVisible === true,
                pointMarkersRadius: spec.pointMarkersRadius || 2,
            });
        });
    });
    if (enabled.has('BotBulls5')) {
        const drawingLayers = window.StandardIndicatorSubcharts?.buildBotBulls5PatternDrawing?.(history, {
            rolling24Hours: isCrypto(manualBtTicker || ''),
        }) || [];
        drawingLayers.forEach(layer => {
            manualBtAddLine(`bb5_drawing_${layer.key}`, layer.data, {
                color: layer.color,
                lineWidth: layer.lineWidth || 2,
                lineStyle: layer.lineStyle || 0,
            });
        });
    }
}

function manualBtMarkers(history) {
    const enabled = new Set(manualBtSelectedIndicators());
    const markers = [];
    const botBulls5VisibleTimes = enabled.has('BotBulls5')
        ? (window.StandardIndicatorSubcharts?.botBulls5RecentEventTimes
            ? window.StandardIndicatorSubcharts.botBulls5RecentEventTimes(history, {
                rolling24Hours: isCrypto(manualBtTicker || ''),
            })
            : new Set(history.map(bar => Number(bar?.time)).filter(Number.isFinite)))
        : new Set();
    for (let i = 1; i < history.length; i += 1) {
        const prev = history[i - 1], curr = history[i];
        if (enabled.has('Supertrend') && curr.supertrend != null && prev.supertrend != null) {
            const c = btIsSupertrendBullish(curr), p = btIsSupertrendBullish(prev);
            if (c && !p) markers.push({ time: Number(curr.time), position: 'belowBar', color: '#10b981', shape: 'arrowUp', text: 'ST Buy', size: 1 });
            else if (!c && p) markers.push({ time: Number(curr.time), position: 'aboveBar', color: '#ef4444', shape: 'arrowDown', text: 'ST Sell', size: 1 });
        }
        if (enabled.has('BotBulls1')) {
            if (curr.ba1_buy) markers.push({ time: Number(curr.time), position: 'belowBar', color: '#06b6d4', shape: 'arrowUp', text: 'BB1 BUY', size: 1 });
            if (curr.ba1_sell) markers.push({ time: Number(curr.time), position: 'aboveBar', color: '#f43f5e', shape: 'arrowDown', text: 'BB1 SELL', size: 1 });
        }
        if (enabled.has('BotBulls2')) {
            if (curr.lx_smart_trail != null && prev.lx_smart_trail != null && curr.lx_smart_trend && !prev.lx_smart_trend) {
                markers.push({ time: Number(curr.time), position: 'belowBar', color: '#10b981', shape: 'arrowUp', text: 'BB2 BUY', size: 1 });
            } else if (curr.lx_smart_trail != null && prev.lx_smart_trail != null && !curr.lx_smart_trend && prev.lx_smart_trend) {
                markers.push({ time: Number(curr.time), position: 'aboveBar', color: '#ef4444', shape: 'arrowDown', text: 'BB2 SELL', size: 1 });
            }
        }
        if (enabled.has('BotBulls3')) {
            if (curr.ut_trail != null && prev.ut_trail != null && curr.ut_above && !prev.ut_above) {
                markers.push({ time: Number(curr.time), position: 'belowBar', color: '#0ea5e9', shape: 'arrowUp', text: 'BB3 BUY', size: 1.2 });
            } else if (curr.ut_trail != null && prev.ut_trail != null && !curr.ut_above && prev.ut_above) {
                markers.push({ time: Number(curr.time), position: 'aboveBar', color: '#f43f5e', shape: 'arrowDown', text: 'BB3 SELL', size: 1.2 });
            }
        }
        if (enabled.has('BotBulls5') && botBulls5VisibleTimes.has(Number(curr.time))) {
            if (curr.bb5_breakout_bull) {
                markers.push({ time: Number(curr.time), position: 'belowBar', color: '#10b981', shape: 'arrowUp', text: `BB5 ${String(curr.bb5_pattern || 'Bull').replaceAll('_', ' ')}`, size: 1.2 });
            } else if (curr.bb5_breakout_bear) {
                markers.push({ time: Number(curr.time), position: 'aboveBar', color: '#f43f5e', shape: 'arrowDown', text: `BB5 ${String(curr.bb5_pattern || 'Bear').replaceAll('_', ' ')}`, size: 1.2 });
            }
        }
    }
    if (enabled.has('BotBulls5')) {
        const latestPattern = history.at(-1);
        if (latestPattern?.bb5_state === 'FORMING') {
            markers.push({
                time: Number(latestPattern.time),
                position: Number(latestPattern.bb5_direction) < 0 ? 'aboveBar' : 'belowBar',
                color: '#f59e0b',
                shape: 'circle',
                text: `BB5 ${String(latestPattern.bb5_pattern).replaceAll('_', ' ')} FORMING`,
                size: 1,
            });
        }
    }
    const last = history[history.length - 1];
    if (enabled.has('Candle Patterns') && last?.cdl_name && last.cdl_name !== 'Neutral') {
        markers.push({ time: Number(last.time), position: last.cdl_bullish ? 'belowBar' : 'aboveBar', color: last.cdl_bullish ? '#10b981' : '#ef4444', shape: last.cdl_bullish ? 'arrowUp' : 'arrowDown', text: last.cdl_name, size: 1.1 });
    }
    if (manualBtTrade.entry) {
        markers.push({ time: Number(manualBtTrade.entry.time), position: 'belowBar', color: '#111827', shape: 'arrowUp', text: `Entry $${manualBtFormatPrice(manualBtTrade.entry.price)}`, size: 2 });
    }
    const exit = manualBtResolvedExit();
    if (exit) {
        const pl = manualBtTradePl();
        markers.push({ time: Number(exit.time), position: 'aboveBar', color: exit.kind === 'tp' ? '#10b981' : '#ef4444', shape: 'arrowDown', text: `${exit.kind.toUpperCase()} ${pl.pct >= 0 ? '+' : ''}${pl.pct.toFixed(2)}%`, size: 2 });
    }
    return typeof sanitizeBotChartPreviewMarkers === 'function'
        ? sanitizeBotChartPreviewMarkers(markers)
        : markers.sort((a, b) => Number(a.time) - Number(b.time));
}

function manualBtStatusFor(indicator, bar) {
    if (!bar) return { signal: 'NEUTRAL', reason: 'No data' };
    const close = Number(bar.close);
    if (indicator === 'RSI') {
        const r = Number(bar.rsi);
        return r < 30 ? { signal: 'BULLISH', reason: `Oversold ${r.toFixed(1)}` } : r > 70 ? { signal: 'BEARISH', reason: `Overbought ${r.toFixed(1)}` } : { signal: 'NEUTRAL', reason: Number.isFinite(r) ? `Neutral ${r.toFixed(1)}` : 'No RSI' };
    }
    if (indicator === 'MACD') {
        const line = Number(bar.macd_line), sig = Number(bar.macd_signal), hist = Number(bar.macd_hist);
        const bull = Number.isFinite(line) && Number.isFinite(sig) ? line > sig : hist > 0;
        const bear = Number.isFinite(line) && Number.isFinite(sig) ? line < sig : hist < 0;
        return bull ? { signal: 'BULLISH', reason: 'MACD above signal' } : bear ? { signal: 'BEARISH', reason: 'MACD below signal' } : { signal: 'NEUTRAL', reason: 'Flat MACD' };
    }
    if (indicator === 'EMA Cross') return Number(bar.ema_fast) > Number(bar.ema_slow) ? { signal: 'BULLISH', reason: 'Fast EMA above slow' } : { signal: 'BEARISH', reason: 'Fast EMA below slow' };
    if (indicator === 'ADX Trend') return Number(bar.di_plus) > Number(bar.di_minus) ? { signal: 'BULLISH', reason: `DI+ > DI- · ADX ${manualBtFormatPrice(bar.adx)}` } : { signal: 'BEARISH', reason: `DI- > DI+ · ADX ${manualBtFormatPrice(bar.adx)}` };
    if (indicator === 'Supertrend') return btIsSupertrendBullish(bar) ? { signal: 'BULLISH', reason: 'Supertrend bullish' } : { signal: 'BEARISH', reason: 'Supertrend bearish' };
    if (indicator === 'Bollinger') return close <= Number(bar.boll_lower) ? { signal: 'BULLISH', reason: 'Near lower band' } : close >= Number(bar.boll_upper) ? { signal: 'BEARISH', reason: 'Near upper band' } : { signal: 'NEUTRAL', reason: 'Inside bands' };
    if (indicator === 'VWAP') return close >= Number(bar.vwap) ? { signal: 'BULLISH', reason: 'Above AVWAP' } : { signal: 'BEARISH', reason: 'Below AVWAP' };
    if (indicator === 'SMA') return close >= Number(bar.sma) ? { signal: 'BULLISH', reason: 'Above SMA' } : { signal: 'BEARISH', reason: 'Below SMA' };
    if (indicator === 'Volume Profile') return bar.volume_profile_bullish ? { signal: 'BULLISH', reason: 'Value area acceptance' } : bar.volume_profile_bearish ? { signal: 'BEARISH', reason: 'Value area rejection' } : { signal: 'NEUTRAL', reason: 'Neutral profile' };
    if (indicator === 'Prior Day High/Low') return bar.daily_high_low_bullish ? { signal: 'BULLISH', reason: 'Prior high breakout' } : bar.daily_high_low_bearish ? { signal: 'BEARISH', reason: 'Prior low breakdown' } : { signal: 'NEUTRAL', reason: 'Inside prior range' };
    if (indicator === 'Pivot Points (S/R)') return bar.pivot_bullish ? { signal: 'BULLISH', reason: 'Pivot support/reclaim' } : bar.pivot_bearish ? { signal: 'BEARISH', reason: 'Pivot rejection/breakdown' } : { signal: 'NEUTRAL', reason: 'Neutral pivot' };
    if (indicator === 'Swing Points') return bar.swing_bullish ? { signal: 'BULLISH', reason: 'Swing support/reclaim' } : bar.swing_bearish ? { signal: 'BEARISH', reason: 'Swing resistance/reject' } : { signal: 'NEUTRAL', reason: 'Neutral swing' };
    if (indicator === 'Mystic Pulse') {
        const bull = Number(bar.mystic_bull || 0), bear = Number(bar.mystic_bear || 0);
        const threshold = Number(getIndicatorOverrides?.().MYSTIC_PULSE_THRESHOLD ?? 5);
        return bull >= threshold ? { signal: 'BULLISH', reason: `Strength ${Math.round(bull)}/${threshold}` }
            : bear >= threshold ? { signal: 'BEARISH', reason: `Strength ${Math.round(bear)}/${threshold}` }
            : { signal: 'NEUTRAL', reason: `Strength ${Math.round(Math.max(bull, bear))}/${threshold}` };
    }
    if (indicator === 'BotBulls1') return bar.ba1_bullish || bar.ba1_buy ? { signal: 'BULLISH', reason: 'BB1 bullish' } : bar.ba1_bearish || bar.ba1_sell ? { signal: 'BEARISH', reason: 'BB1 bearish' } : { signal: 'NEUTRAL', reason: 'No BB1 trigger' };
    if (indicator === 'BotBulls2') return bar.ba2_buy ? { signal: 'BULLISH', reason: 'BB2 buy marker' } : bar.ba2_sell ? { signal: 'BEARISH', reason: 'BB2 sell marker' } : { signal: 'NEUTRAL', reason: 'No BB2 trigger' };
    if (indicator === 'BotBulls5') {
        return bar.bb5_breakout_bull || String(bar.bb5_state || '').includes('BULLISH')
            ? { signal: 'BULLISH', reason: `${String(bar.bb5_pattern || 'Bull pattern').replaceAll('_', ' ')} · ${Number(bar.bb5_confidence || 0).toFixed(0)}%` }
            : bar.bb5_breakout_bear || String(bar.bb5_state || '').includes('BEARISH')
                ? { signal: 'BEARISH', reason: `${String(bar.bb5_pattern || 'Bear pattern').replaceAll('_', ' ')} · ${Number(bar.bb5_confidence || 0).toFixed(0)}%` }
                : { signal: 'NEUTRAL', reason: String(bar.bb5_state || 'No qualified pattern').replaceAll('_', ' ') };
    }
    if (indicator === 'BotBulls3') return bar.ba3_buy ? { signal: 'BULLISH', reason: 'BB3 buy marker' } : bar.ba3_sell ? { signal: 'BEARISH', reason: 'BB3 sell marker' } : { signal: 'NEUTRAL', reason: 'No BB3 trigger' };
    if (indicator === 'Candle Patterns') return bar.cdl_bullish ? { signal: 'BULLISH', reason: bar.cdl_name || 'Bullish candle' } : bar.cdl_bearish ? { signal: 'BEARISH', reason: bar.cdl_name || 'Bearish candle' } : { signal: 'NEUTRAL', reason: bar.cdl_name || 'Neutral candle' };
    if (indicator === 'Botbulls4 News AI') return { signal: 'NEUTRAL', reason: 'Live-news only; disabled in backtest replay' };
    return { signal: 'NEUTRAL', reason: 'Selected' };
}

function manualBtEscapeAttr(value) {
    return String(value || '').replace(/&/g, '&amp;').replace(/"/g, '&quot;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
}

function manualBtEscapeJs(value) {
    return String(value || '').replace(/\\/g, '\\\\').replace(/'/g, "\\'");
}

function manualBtSignalFor(name, bar) {
    const fallback = manualBtStatusFor(name, bar);
    const serverSignal = manualBtSignals?.[name] || {};
    return {
        ...fallback,
        ...serverSignal,
        signal: serverSignal?.signal || fallback.signal,
        reason: serverSignal?.reason || fallback.reason,
        premium: Boolean(serverSignal?.premium) || MANUAL_BT_PREMIUM_INDICATORS.has(name),
    };
}

function manualBtRenderConfluence(history) {
    const grid = document.getElementById('manualBtChartPreviewSignalGrid');
    const premiumGrid = document.getElementById('manualBtChartPreviewPremiumSignalGrid');
    const loading = document.getElementById('manualBtChartPreviewGridLoading');
    if (!grid) return;
    if (loading) loading.classList.add('hidden');
    if (premiumGrid) premiumGrid.innerHTML = '';
    const activeSet = new Set(manualBtSelectedIndicators());
    const mode = typeof currentBtMode !== 'undefined' && currentBtMode === 'options' ? 'options' : 'regular';
    const shown = getBacktestWorkspaceShownSet(mode, manualBtTicker);
    const signalEntries = Object.entries(manualBtSignals || {})
        .filter(([name]) => name !== 'Botbulls4 News AI')
        .sort(([a], [b]) => {
            const ai = MANUAL_BT_CHART_PREVIEW_INDICATORS.indexOf(a);
            const bi = MANUAL_BT_CHART_PREVIEW_INDICATORS.indexOf(b);
            const ar = ai >= 0 ? ai : Number.MAX_SAFE_INTEGER;
            const br = bi >= 0 ? bi : Number.MAX_SAFE_INTEGER;
            return ar === br ? a.localeCompare(b) : ar - br;
        });
    if (!signalEntries.length) {
        MANUAL_BT_CHART_PREVIEW_INDICATORS.forEach(name => {
            if (name !== 'Botbulls4 News AI') {
                signalEntries.push([name, {
                    enabled: activeSet.has(name),
                    premium: MANUAL_BT_PREMIUM_INDICATORS.has(name),
                }]);
            }
        });
    }
    const last = history[history.length - 1];
    let bull = 0, bear = 0;
    const standardCards = [];
    const premiumCards = [];
    signalEntries.forEach(([name]) => {
        const s = manualBtSignalFor(name, last);
        const enabled = activeSet.has(name);
        
        // Only show regular indicators if they are part of the active workspace.
        // Premium indicators are managed separately and bypass the workspace limit.
        if (!MANUAL_BT_PREMIUM_INDICATORS.has(name) && !shown.has(name)) return;
        
        if (enabled && s.signal === 'BULLISH') bull += 1;
        if (enabled && s.signal === 'BEARISH') bear += 1;
        const isPremium = Boolean(s.premium);
        const cardClass = !enabled
            ? (isPremium ? 'premium-neutral' : 'neutral')
            : isPremium
            ? (s.signal === 'BULLISH' ? 'premium-bullish' : s.signal === 'BEARISH' ? 'premium-bearish' : 'premium-neutral')
            : (s.signal === 'BULLISH' ? 'bullish' : s.signal === 'BEARISH' ? 'bearish' : 'neutral');
        const icon = !enabled ? '●' : s.signal === 'BULLISH' ? '▲' : s.signal === 'BEARISH' ? '▼' : '●';
        const iconColor = !enabled ? 'text-slate-400' : s.signal === 'BULLISH' ? 'text-emerald-600' : s.signal === 'BEARISH' ? 'text-red-500' : 'text-purple-400';
        const display = name === 'VWAP' ? 'AVWAP' : name;
        const escapedName = manualBtEscapeJs(name);
        const safeReason = manualBtEscapeAttr(enabled ? (s.reason || 'No reason available.') : 'Disabled — click to enable');
        const displayLabelClass = name === 'VWAP' ? 'vwap-indicator-label' : (isPremium ? 'text-amber-800' : 'text-indigo-900');
        const infoColor = isPremium ? 'text-amber-400 hover:text-amber-600' : 'text-indigo-300 hover:text-indigo-600';
        const tooltipBg = isPremium ? 'bg-amber-900' : 'bg-indigo-900';
        const tooltipBorder = isPremium ? 'border-t-amber-900' : 'border-t-indigo-900';
        const settingsHover = isPremium ? 'hover:bg-amber-100/50' : 'hover:bg-indigo-50';
        const settingsColor = isPremium ? 'text-amber-600' : 'text-indigo-500';
        const reasonColor = isPremium ? 'text-amber-700' : 'text-purple-600';
        const disabledClass = enabled ? '' : 'disabled-signal';
        const card = `
            <div class="signal-card group ${cardClass} ${disabledClass} cursor-pointer transition-all duration-300"
                onclick="toggleManualBtChartPreviewIndicator('${escapedName}')">
                <div class="flex items-center justify-between mb-1">
                    <div class="flex items-center gap-1.5 min-w-0">
                        <span class="font-bold text-xs ${displayLabelClass} transition-opacity duration-300 truncate">${display}</span>
                        <div class="group/tooltip relative inline-flex items-center shrink-0">
                            <svg onclick="event.stopPropagation(); event.preventDefault(); typeof openInfoModal === 'function' ? openInfoModal('${escapedName}') : null" xmlns="http://www.w3.org/2000/svg" class="h-3.5 w-3.5 ${infoColor} cursor-pointer transition-colors" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                                <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M13 16h-1v-4h-1m1-4h.01M21 12a9 9 0 11-18 0 9 9 0 0118 0z" />
                            </svg>
                            <div class="invisible group-hover/tooltip:visible opacity-0 group-hover/tooltip:opacity-100 transition-all absolute bottom-full left-1/2 -translate-x-1/2 mb-2 px-3 py-1.5 ${tooltipBg} text-white text-[0.65rem] leading-snug rounded-lg shadow-2xl z-[999] pointer-events-none whitespace-nowrap text-center font-bold normal-case tracking-normal">
                                Click to read guide
                                <div class="absolute top-full left-1/2 -translate-x-1/2 border-4 border-transparent ${tooltipBorder}"></div>
                            </div>
                        </div>
                        <button class="opacity-70 hover:opacity-100 transition-opacity p-0.5 rounded ${settingsHover}"
                                onclick="event.stopPropagation(); typeof openIndicatorSettings === 'function' ? openIndicatorSettings('${escapedName}') : null"
                                title="${display} Settings">
                            <svg xmlns="http://www.w3.org/2000/svg" class="h-3 w-3 ${settingsColor}" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                                <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2.5" d="M10.325 4.317c.426-1.756 2.924-1.756 3.35 0a1.724 1.724 0 002.573 1.066c1.543-.94 3.31.826 2.37 2.37a1.724 1.724 0 001.065 2.572c1.756.426 1.756 2.924 0 3.35a1.724 1.724 0 00-1.066 2.573c.94 1.543-.826 3.31-2.37 2.37a1.724 1.724 0 00-2.572 1.065c-.426 1.756-2.924 1.756-3.35 0a1.724 1.724 0 00-2.573-1.066c-1.543.94-3.31-.826-2.37-2.37a1.724 1.724 0 00-1.065-2.572c-1.756-.426-1.756-2.924 0-3.35a1.724 1.724 0 001.066-2.573c-.94-1.543.826-3.31 2.37-2.37.996.608 2.296.07 2.572-1.065z" />
                                <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2.5" d="M15 12a3 3 0 11-6 0 3 3 0 016 0z" />
                            </svg>
                        </button>
                    </div>
                    <span class="${iconColor} text-sm font-bold transition-all duration-300">${icon}</span>
                </div>
                <p class="text-[0.6rem] ${reasonColor} leading-tight line-clamp-1 transition-opacity duration-300">${safeReason}</p>
            </div>
        `;
        if (isPremium) premiumCards.push(card);
        else standardCards.push(card);
    });
    const isMobileView = typeof window !== 'undefined' && window.innerWidth <= 640;
    if (standardCards.length > 0) {
        grid.style.gridTemplateColumns = isMobileView
            ? 'repeat(2, minmax(0, 1fr))'
            : `repeat(${Math.max(Math.ceil(standardCards.length / 3), 2)}, minmax(0, 1fr))`;
        grid.innerHTML = standardCards.join('');
    } else {
        grid.style.gridTemplateColumns = '';
        grid.innerHTML = `<div class="col-span-full signal-card neutral px-4 py-6 text-center text-sm text-slate-500">No standard indicator selected for this manual replay.</div>`;
    }
    if (premiumGrid) {
        if (premiumCards.length > 0) {
            premiumGrid.style.gridTemplateColumns = isMobileView
                ? 'repeat(2, minmax(0, 1fr))'
                : `repeat(${Math.max(premiumCards.length, 1)}, minmax(0, 1fr))`;
            premiumGrid.innerHTML = premiumCards.join('');
        } else {
            premiumGrid.style.gridTemplateColumns = '';
            premiumGrid.innerHTML = `<div class="col-span-full signal-card neutral px-4 py-4 text-center text-sm text-slate-500">No premium indicator selected.</div>`;
        }
    }
    manualBtSetText('manualBtChartPreviewBullCount', `${bull} Bullish`);
    manualBtSetText('manualBtChartPreviewBearCount', `${bear} Bearish`);
}

function manualBtCreateOsc(containerId) {
    const el = document.getElementById(containerId);
    if (!el) return null;
    const chart = LightweightCharts.createChart(el, {
        layout: { background: { color: '#fafbff' }, textColor: '#64748b' },
        grid: { vertLines: { color: 'rgba(226,232,240,0.45)' }, horzLines: { color: 'rgba(226,232,240,0.45)' } },
        rightPriceScale: { borderVisible: false },
        timeScale: { visible: false },
        localization: BT_CHART_LOCALIZATION,
        width: el.clientWidth || 600,
        height: el.clientHeight || 120,
    });
    chart.__manualBtHost = el;
    return chart;
}

function manualBtSyncOscillatorRanges(range) {
    if (!range || !Number.isFinite(Number(range.from)) || !Number.isFinite(Number(range.to))) return;
    Object.values(manualBtOscCharts || {}).forEach(chart => {
        try { chart?.timeScale?.().setVisibleLogicalRange(range); } catch (e) {}
    });
    window.StandardIndicatorSubcharts?.sync('backtest', range);
}

function manualBtRenderOscillators(history) {
    const enabled = new Set(manualBtSelectedIndicators());
    const toggle = (id, labelId, show) => {
        const el = document.getElementById(id);
        const label = document.getElementById(labelId);
        if (el) el.style.display = show ? 'block' : 'none';
        if (label) label.style.display = show ? 'block' : 'none';
    };

    const keyMap = { rsi: 'RSI', macd: 'MACD', mystic: 'Mystic Pulse', adx: 'ADX Trend', botbulls1: 'BotBulls1' };
    Object.keys(keyMap).forEach(key => {
        if (!enabled.has(keyMap[key]) && manualBtOscCharts[key]) {
            try { manualBtOscCharts[key].remove(); } catch(e){}
            delete manualBtOscCharts[key];
            Object.keys(manualBtOscSeries).forEach(seriesKey => {
                if (seriesKey === key || seriesKey.startsWith(`${key}_`)) delete manualBtOscSeries[seriesKey];
            });
        }
    });

    toggle('manualBtChartPreviewOsc_rsi', 'manualBtChartPreviewRsiLabel', enabled.has('RSI'));
    toggle('manualBtChartPreviewOsc_macd', 'manualBtChartPreviewMacdLabel', enabled.has('MACD'));
    toggle('manualBtChartPreviewOsc_mystic', 'manualBtChartPreviewMysticLabel', enabled.has('Mystic Pulse'));
    toggle('manualBtChartPreviewOsc_adx', 'manualBtChartPreviewAdxLabel', enabled.has('ADX Trend'));
    toggle('manualBtChartPreviewOsc_botbulls1', 'manualBtChartPreviewBotbulls1Label', enabled.has('BotBulls1'));

    if (enabled.has('RSI')) {
        if (!manualBtOscCharts.rsi) {
            manualBtOscCharts.rsi = manualBtCreateOsc('manualBtChartPreviewOsc_rsi');
        }
        if (!manualBtOscSeries.rsi) manualBtOscSeries.rsi = manualBtOscCharts.rsi?.addLineSeries({ color: '#f59e0b', lineWidth: 1 });
        if (!manualBtOscSeries.rsi_ob) manualBtOscSeries.rsi_ob = manualBtOscCharts.rsi?.addLineSeries({ color: 'rgba(239,68,68,0.4)', lineWidth: 1, lineStyle: 2 });
        if (!manualBtOscSeries.rsi_os) manualBtOscSeries.rsi_os = manualBtOscCharts.rsi?.addLineSeries({ color: 'rgba(16,185,129,0.4)', lineWidth: 1, lineStyle: 2 });
        const period = manualBtIndicatorParamValue('RSI_PERIOD', 14);
        const overbought = manualBtIndicatorParamValue('RSI_OVERBOUGHT', 70);
        const oversold = manualBtIndicatorParamValue('RSI_OVERSOLD', 30);
        manualBtSetText('manualBtChartPreviewRsiLabel', `RSI (${period})  •  OB: ${overbought} / OS: ${oversold}`);
        manualBtOscSeries.rsi?.setData(manualBtCleanLineData(manualBtLineData(history, 'rsi')));
        manualBtOscSeries.rsi_ob?.setData(history.length > 1 ? manualBtCleanLineData([
                { time: history[0].time, value: overbought },
                { time: history[history.length - 1].time, value: overbought },
            ]) : []);
        manualBtOscSeries.rsi_os?.setData(history.length > 1 ? manualBtCleanLineData([
                { time: history[0].time, value: oversold },
                { time: history[history.length - 1].time, value: oversold },
            ]) : []);
    }
    if (enabled.has('MACD')) {
        if (!manualBtOscCharts.macd) {
            manualBtOscCharts.macd = manualBtCreateOsc('manualBtChartPreviewOsc_macd');
        }
        if (!manualBtOscSeries.macd_hist) manualBtOscSeries.macd_hist = manualBtOscCharts.macd?.addHistogramSeries({ base: 0, priceFormat: { type: 'price', precision: 2, minMove: 0.01 } });
        if (!manualBtOscSeries.macd_line) manualBtOscSeries.macd_line = manualBtOscCharts.macd?.addLineSeries({ color: '#06b6d4', lineWidth: 1, priceFormat: { type: 'price', precision: 2, minMove: 0.01 } });
        if (!manualBtOscSeries.macd_signal) manualBtOscSeries.macd_signal = manualBtOscCharts.macd?.addLineSeries({ color: '#f97316', lineWidth: 1, priceFormat: { type: 'price', precision: 2, minMove: 0.01 } });
        const fast = manualBtIndicatorParamValue('MACD_FAST', 12);
        const slow = manualBtIndicatorParamValue('MACD_SLOW', 26);
        const signal = manualBtIndicatorParamValue('MACD_SIGNAL', 9);
        manualBtSetText('manualBtChartPreviewMacdLabel', `MACD (${fast}, ${slow}, ${signal})`);
        manualBtOscSeries.macd_hist?.setData(manualBtCleanLineData(history.filter(b => b.macd_hist != null).map(b => ({ time: b.time, value: b.macd_hist, color: Number(b.macd_hist) >= 0 ? 'rgba(16,185,129,0.5)' : 'rgba(239,68,68,0.5)' }))));
        manualBtOscSeries.macd_line?.setData(manualBtCleanLineData(manualBtLineData(history, 'macd_line')));
        manualBtOscSeries.macd_signal?.setData(manualBtCleanLineData(manualBtLineData(history, 'macd_signal')));
    }
    if (enabled.has('Mystic Pulse')) {
        const threshold = manualBtIndicatorParamValue('MYSTIC_PULSE_THRESHOLD', 5);
        if (!manualBtOscCharts.mystic) {
            manualBtOscCharts.mystic = manualBtCreateOsc('manualBtChartPreviewOsc_mystic');
            manualBtOscSeries.mystic_hist = manualBtOscCharts.mystic?.addHistogramSeries({ base: 0 });
            manualBtOscSeries.mystic_up = manualBtOscCharts.mystic?.addLineSeries({ color: 'rgba(16,185,129,0.35)', lineWidth: 1, lineStyle: 2 });
            manualBtOscSeries.mystic_dn = manualBtOscCharts.mystic?.addLineSeries({ color: 'rgba(239,68,68,0.35)', lineWidth: 1, lineStyle: 2 });
        }
        manualBtSetText('manualBtChartPreviewMysticLabel', `Mystic Pulse  •  Threshold: ${threshold}`);
        manualBtOscSeries.mystic_hist?.setData(manualBtCleanLineData(history
            .filter(b => b.mystic_bull != null || b.mystic_bear != null)
            .map(b => {
                const bull = Number(b.mystic_bull || 0);
                const bear = Number(b.mystic_bear || 0);
                const value = bull > 0 ? bull : -bear;
                const color = bull > 0
                    ? (bull >= threshold ? 'rgba(16,185,129,0.9)' : 'rgba(16,185,129,0.4)')
                    : (bear >= threshold ? 'rgba(239,68,68,0.9)' : 'rgba(239,68,68,0.4)');
                return { time: b.time, value, color };
            })));
        manualBtOscSeries.mystic_up?.setData(history.length > 1 ? [
                { time: history[0].time, value: threshold },
                { time: history[history.length - 1].time, value: threshold },
            ] : []);
        manualBtOscSeries.mystic_dn?.setData(history.length > 1 ? [
                { time: history[0].time, value: -threshold },
                { time: history[history.length - 1].time, value: -threshold },
            ] : []);
    }
    if (enabled.has('ADX Trend')) {
        if (!manualBtOscCharts.adx) {
            manualBtOscCharts.adx = manualBtCreateOsc('manualBtChartPreviewOsc_adx');
        }
        if (!manualBtOscSeries.adx) manualBtOscSeries.adx = manualBtOscCharts.adx?.addLineSeries({ color: '#6366f1', lineWidth: 2 });
        if (!manualBtOscSeries.adx_plus) manualBtOscSeries.adx_plus = manualBtOscCharts.adx?.addLineSeries({ color: '#10b981', lineWidth: 1 });
        if (!manualBtOscSeries.adx_minus) manualBtOscSeries.adx_minus = manualBtOscCharts.adx?.addLineSeries({ color: '#ef4444', lineWidth: 1 });
        if (!manualBtOscSeries.adx_threshold) manualBtOscSeries.adx_threshold = manualBtOscCharts.adx?.addLineSeries({ color: 'rgba(99,102,241,0.25)', lineWidth: 1, lineStyle: 2 });
        const period = manualBtIndicatorParamValue('ADX_PERIOD', 14);
        const threshold = manualBtIndicatorParamValue('ADX_TRENDING_THRESHOLD', 25);
        manualBtSetText('manualBtChartPreviewAdxLabel', `ADX (${period})  •  Trend Thresh: ${threshold}`);
        manualBtOscSeries.adx?.setData(manualBtCleanLineData(manualBtLineData(history, 'adx')));
        manualBtOscSeries.adx_plus?.setData(manualBtCleanLineData(manualBtLineData(history, 'di_plus')));
        manualBtOscSeries.adx_minus?.setData(manualBtCleanLineData(manualBtLineData(history, 'di_minus')));
        manualBtOscSeries.adx_threshold?.setData(manualBtCleanLineData(history.map(bar => ({ time: bar.time, value: threshold }))));
    }
    if (enabled.has('BotBulls1')) {
        if (!manualBtOscCharts.botbulls1) {
            manualBtOscCharts.botbulls1 = manualBtCreateOsc('manualBtChartPreviewOsc_botbulls1');
        }
        if (!manualBtOscSeries.botbulls1_1) manualBtOscSeries.botbulls1_1 = manualBtOscCharts.botbulls1?.addLineSeries({ color: '#0ea5e9', lineWidth: 1.5 });
        if (!manualBtOscSeries.botbulls1_2) manualBtOscSeries.botbulls1_2 = manualBtOscCharts.botbulls1?.addLineSeries({ color: '#f43f5e', lineWidth: 1, lineStyle: 2 });
        if (!manualBtOscSeries.botbulls1_mfi) manualBtOscSeries.botbulls1_mfi = manualBtOscCharts.botbulls1?.addHistogramSeries({ base: 50 });
        if (!manualBtOscSeries.botbulls1_ob) manualBtOscSeries.botbulls1_ob = manualBtOscCharts.botbulls1?.addLineSeries({ color: 'rgba(239,68,68,0.35)', lineWidth: 1, lineStyle: 2 });
        if (!manualBtOscSeries.botbulls1_os) manualBtOscSeries.botbulls1_os = manualBtOscCharts.botbulls1?.addLineSeries({ color: 'rgba(16,185,129,0.35)', lineWidth: 1, lineStyle: 2 });
        if (!manualBtOscSeries.botbulls1_zero) manualBtOscSeries.botbulls1_zero = manualBtOscCharts.botbulls1?.addLineSeries({ color: 'rgba(148,163,184,0.15)', lineWidth: 1 });
        const wtChannel = manualBtIndicatorParamValue('BOTBULLS1_WT_CHANNEL', 10);
        const wtAverage = manualBtIndicatorParamValue('BOTBULLS1_WT_AVERAGE', 21);
        const mfiBuy = manualBtIndicatorParamValue('BOTBULLS1_MFI_BUY_RECLAIM', 35);
        const mfiSell = manualBtIndicatorParamValue('BOTBULLS1_MFI_SELL_REJECT', 65);
        manualBtSetText('manualBtChartPreviewBotbulls1Label', `BotBulls1 (WT: ${wtChannel}, ${wtAverage} | MFI: ${mfiBuy}/${mfiSell})`);
        manualBtOscSeries.botbulls1_1?.setData(manualBtCleanLineData(manualBtLineData(history, 'wt1')));
        manualBtOscSeries.botbulls1_2?.setData(manualBtCleanLineData(manualBtLineData(history, 'wt2')));
        manualBtOscSeries.botbulls1_mfi?.setData(manualBtCleanLineData(history
            .filter(bar => bar?.mfi != null)
            .map(bar => ({
                time: bar.time,
                value: Number(bar.mfi),
                color: Number(bar.mfi) >= 50 ? 'rgba(16,185,129,0.12)' : 'rgba(239,68,68,0.12)',
            }))));
        const firstTime = history[0]?.time;
        const lastTime = history[history.length - 1]?.time;
        manualBtOscSeries.botbulls1_ob?.setData(history.length > 1 ? manualBtCleanLineData([{ time: firstTime, value: 53 }, { time: lastTime, value: 53 }]) : []);
        manualBtOscSeries.botbulls1_os?.setData(history.length > 1 ? manualBtCleanLineData([{ time: firstTime, value: -53 }, { time: lastTime, value: -53 }]) : []);
        manualBtOscSeries.botbulls1_zero?.setData(history.length > 1 ? manualBtCleanLineData([{ time: firstTime, value: 0 }, { time: lastTime, value: 0 }]) : []);
        manualBtOscSeries.botbulls1_1?.setMarkers((history || []).flatMap(bar => {
            if (bar?.ba1_buy) return [{ time: Number(bar.time), position: 'inLine', color: '#10b981', shape: 'circle', size: 2 }];
            if (bar?.ba1_sell) return [{ time: Number(bar.time), position: 'inLine', color: '#ef4444', shape: 'circle', size: 2 }];
            return [];
        }));
    }

    const backtestSubchartParameters = {
        ...(window.STANDARD_INDICATOR_DEFAULTS || {}),
        ...(window.lastBacktestData?.indicator_parameters || {}),
        ...(typeof getIndicatorOverrides === 'function' ? getIndicatorOverrides() : {}),
    };
    let backtestVisibleRange = null;
    try { backtestVisibleRange = manualBtChart?.timeScale().getVisibleLogicalRange() || null; } catch (e) {}
    window.StandardIndicatorSubcharts?.render({
        context: 'backtest',
        hostId: 'manualBtChartPreviewDynamicOscillators',
        history,
        enabledIndicators: enabled,
        parameters: backtestSubchartParameters,
        height: 120,
        visibleRange: backtestVisibleRange,
    });
    manualBtSyncOscillatorRanges(backtestVisibleRange);
}

function manualBtClearRiskAreas() {
    (manualBtRiskEls || []).forEach(el => { try { el.remove(); } catch (e) {} });
    manualBtRiskEls = [];
    manualBtPriceLines.forEach(line => { try { manualBtCandleSeries?.removePriceLine(line); } catch (e) {} });
    manualBtPriceLines = [];
}

function manualBtResolvedExit() {
    if (!manualBtTrade.entry) return null;
    const entryTime = Number(manualBtTrade.entry.time);
    for (const bar of manualBtVisibleHistory) {
        if (Number(bar.time) <= entryTime) continue;
        if (manualBtTrade.sl && Number(bar.low) <= Number(manualBtTrade.sl.price) && Number(bar.high) >= Number(manualBtTrade.sl.price)) return { ...manualBtTrade.sl, time: Number(bar.time), kind: 'sl' };
        if (manualBtTrade.tp && Number(bar.low) <= Number(manualBtTrade.tp.price) && Number(bar.high) >= Number(manualBtTrade.tp.price)) return { ...manualBtTrade.tp, time: Number(bar.time), kind: 'tp' };
    }
    return null;
}

function manualBtTradePl() {
    const entry = Number(manualBtTrade.entry?.price);
    const exit = Number(manualBtResolvedExit()?.price);
    if (!Number.isFinite(entry) || !Number.isFinite(exit)) return { pl: 0, pct: 0 };
    const pl = exit - entry;
    return { pl, pct: entry > 0 ? pl / entry * 100 : 0 };
}

function manualBtDrawRisk() {
    manualBtClearRiskAreas();
    if (!manualBtChart || !manualBtCandleSeries || !manualBtTrade.entry) return;
    const addLine = (point, title, color, style = 0) => {
        if (!point || !Number.isFinite(Number(point.price))) return;
        manualBtPriceLines.push(manualBtCandleSeries.createPriceLine({ price: Number(point.price), color, lineWidth: 2, lineStyle: style, axisLabelVisible: true, title }));
    };
    addLine(manualBtTrade.entry, 'ENTRY', '#111827', 0);
    addLine(manualBtTrade.tp, '', '#10b981', 2);
    addLine(manualBtTrade.sl, '', '#ef4444', 2);
    const wrap = document.getElementById('manualBtChartPreviewChart');
    const entryY = manualBtCandleSeries.priceToCoordinate(Number(manualBtTrade.entry.price));
    const x1 = manualBtChart.timeScale().timeToCoordinate(Number(manualBtTrade.entry.time));
    const exit = manualBtResolvedExit();
    const lastVisibleTime = manualBtVisibleHistory[manualBtVisibleHistory.length - 1]?.time;
    const endTime = exit && Number(exit.time) <= Number(lastVisibleTime) ? exit.time : lastVisibleTime;
    const x2 = manualBtChart.timeScale().timeToCoordinate(Number(endTime));
    if (!wrap || !Number.isFinite(Number(entryY)) || !Number.isFinite(Number(x1)) || !Number.isFinite(Number(x2))) return;
    const left = Math.min(Number(x1), Number(x2));
    let width = Math.max(2, Math.abs(Number(x2) - Number(x1)));
    if (!exit) {
        let barSpacing = 10;
        try {
            const range = manualBtChart.timeScale().getVisibleLogicalRange();
            const px = manualBtChart.timeScale().width();
            if (range && px) barSpacing = px / (range.to - range.from);
        } catch(e) {}
        width += Math.max(10, Math.round(barSpacing * 10));
    }
    
    const addArea = (point, title, color, borderColor, metricPrefix) => {
        const y = manualBtCandleSeries.priceToCoordinate(Number(point?.price));
        if (!point || !Number.isFinite(Number(y))) return;
        const box = document.createElement('div');
        const top = Math.min(Number(y), Number(entryY));
        const height = Math.max(2, Math.abs(Number(y) - Number(entryY)));
        const pct = Math.abs((Number(point.price) - Number(manualBtTrade.entry.price)) / Number(manualBtTrade.entry.price) * 100);
        box.style.cssText = `position:absolute;pointer-events:none;z-index:4;left:${left}px;top:${top}px;width:${width}px;height:${height}px;background:${color};border:1px solid ${borderColor};border-radius:10px;`;
        box.innerHTML = `<div style="position:absolute;right:6px;top:5px;font-size:10px;font-weight:900;color:${borderColor};background:rgba(255,255,255,.72);border-radius:999px;padding:2px 6px;">${title} · ${metricPrefix}${pct.toFixed(2)}%</div>`;
        wrap.appendChild(box);
        manualBtRiskEls.push(box);
    };
    addArea(manualBtTrade.tp, 'TP', 'rgba(16,185,129,0.07)', '#10b981', '+');
    addArea(manualBtTrade.sl, 'SL', 'rgba(239,68,68,0.07)', '#ef4444', '-');
}

function manualBtUpdateTradeStatus() {
    const status = document.getElementById('manualBtTradeStatus');
    if (!status) return;
    const exit = manualBtResolvedExit();
    const pl = manualBtTradePl();
    if (manualBtPickTarget) {
        status.textContent = `Click chart to place ${manualBtPickTarget.toUpperCase()}`;
        return;
    }
    if (exit) {
        status.textContent = `${exit.kind.toUpperCase()} hit · P/L ${pl.pl >= 0 ? '+' : ''}$${pl.pl.toFixed(2)} (${pl.pct >= 0 ? '+' : ''}${pl.pct.toFixed(2)}%)`;
        status.className = `ml-auto text-[0.6rem] font-black ${pl.pl >= 0 ? 'text-emerald-700' : 'text-rose-700'}`;
        return;
    }
    const parts = [
        manualBtTrade.entry ? `Entry $${manualBtFormatPrice(manualBtTrade.entry.price)}` : null,
        manualBtTrade.tp ? `TP $${manualBtFormatPrice(manualBtTrade.tp.price)}` : null,
        manualBtTrade.sl ? `SL $${manualBtFormatPrice(manualBtTrade.sl.price)}` : null,
    ].filter(Boolean);
    status.textContent = parts.join(' · ') || 'Pick Entry, TP Area, or SL Area, then click the chart.';
    status.className = 'ml-auto text-[0.6rem] font-bold text-emerald-700';
}

function manualBtRenderChart(sourceHistory = null, preserveReplay = false) {
    const fullHistory = manualBtFilteredHistory();
    const history = sourceHistory || fullHistory;
    manualBtVisibleHistory = history;
    manualBtUpdateReplayStartControl(fullHistory, !sourceHistory || !manualBtReplayActive);
    if (!history.length) {
        manualBtDestroyChart(false);
        manualBtUpdateHeader([]);
        manualBtSetText('manualBtChartPreviewBanner', 'No candles available for this session/range.');
        manualBtSetReplayStatus('No candles for selected view');
        manualBtReplayUpdateControls();
        document.getElementById('manualBtChartPreviewBanner')?.classList.remove('hidden');
        return;
    }
    document.getElementById('manualBtChartPreviewBanner')?.classList.add('hidden');
    manualBtUpdateHeader(history);
    manualBtDestroyChart(preserveReplay);
    const container = document.getElementById('manualBtChartPreviewChart');
    if (!container) return;
    manualBtChart = LightweightCharts.createChart(container, {
        layout: { background: { color: '#ffffff' }, textColor: '#334155' },
        grid: { vertLines: { color: 'rgba(226,232,240,0.45)' }, horzLines: { color: 'rgba(226,232,240,0.45)' } },
        crosshair: { mode: LightweightCharts.CrosshairMode.Normal },
        rightPriceScale: { borderColor: 'rgba(197,203,206,0.8)', minimumWidth: 75 },
        timeScale: { 
            borderColor: 'rgba(197,203,206,0.8)', 
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
        localization: BT_CHART_LOCALIZATION,
        width: container.clientWidth,
        height: container.clientHeight,
    });
    manualBtExtHoursSeries = manualBtChart.addHistogramSeries({
        color: 'rgba(100, 116, 139, 0.12)',
        priceFormat: { type: 'custom', formatter: () => '' },
        priceScaleId: 'ext-hours-bg',
        scaleMargins: { top: 0, bottom: 0 },
        autoscaleInfoProvider: () => ({
            priceRange: { minValue: 0, maxValue: 100 },
        }),
    });
    manualBtChart.priceScale('ext-hours-bg').applyOptions({
        scaleMargins: { top: 0, bottom: 0 },
        visible: false,
    });
    const ticker = manualBtTicker || '';
    const tf = manualBtTimeframe || '1Hour';
    if (!isCrypto(ticker) && manualBtSession === 'extended' && !String(tf).includes('Day')) {
        manualBtExtHoursSeries.setData(manualBtExtendedHoursBackground(history));
    } else {
        manualBtExtHoursSeries.setData([]);
    }

    manualBtCandleSeries = manualBtChart.addCandlestickSeries({ upColor: '#10b981', downColor: '#ef4444', borderVisible: false, wickUpColor: '#10b981', wickDownColor: '#ef4444' });
    manualBtCandleSeries.setData(manualBtCleanCandles(history));
    manualBtVolumeSeries = manualBtChart.addHistogramSeries({ priceFormat: { type: 'volume' }, priceScaleId: '' });
    manualBtChart.priceScale('').applyOptions({ scaleMargins: { top: 0.8, bottom: 0 } });
    manualBtVolumeSeries.setData(history.map(bar => ({ time: Number(bar.time), value: Number(bar.volume || 0), color: Number(bar.close) >= Number(bar.open) ? 'rgba(16,185,129,0.35)' : 'rgba(239,68,68,0.35)' })));
    manualBtRenderOverlays(history);
    manualBtCandleSeries.setMarkers(manualBtMarkers(history));
    manualBtRenderOscillators(history);
    manualBtRenderConfluence(history);
    manualBtDrawRisk();
    manualBtClickHandler = param => {
        if (!manualBtPickTarget || !param?.point || param.time == null || !manualBtCandleSeries) return;
        
        if (manualBtPickTarget === 'replay_start') {
            const time = Number(param.time);
            const idx = manualBtDisplayHistory.findIndex(b => Number(b.time) === time);
            if (idx >= 0) {
                const range = manualBtVisibleLogicalRange();
                manualBtReplayStartIndex = idx;
                manualBtReplayCursor = idx;
                manualBtPickTarget = null;
                if (!manualBtReplayActive) {
                    manualBtToggleReplay();
                } else {
                    manualBtRenderReplayFrame(idx);
                    if (range) setTimeout(() => manualBtRestoreVisibleLogicalRange(range), 0);
                }
                manualBtUpdateReplayStartControl(manualBtDisplayHistory, true);
                manualBtSetText('manualBtTradeStatus', `Replay start set.`);
            }
            return;
        }

        const price = manualBtCandleSeries.coordinateToPrice(param.point.y);
        if (!Number.isFinite(Number(price))) return;
        manualBtTrade[manualBtPickTarget] = { time: Number(param.time), price: Number(price) };
        if (manualBtPickTarget === 'entry') {
            const tpPct = Number(document.getElementById('manualBtDefaultTpPct')?.value) || 2.0;
            const slPct = Number(document.getElementById('manualBtDefaultSlPct')?.value) || -1.0;
            if (!manualBtTrade.tp) manualBtTrade.tp = { time: Number(param.time), price: Number(price) * (1 + tpPct / 100) };
            if (!manualBtTrade.sl) manualBtTrade.sl = { time: Number(param.time), price: Number(price) * (1 + slPct / 100) };
        }
        manualBtPickTarget = null;
        manualBtRenderChart(manualBtVisibleHistory, manualBtReplayActive);
        manualBtUpdateTradeStatus();
    };
    try { manualBtChart.subscribeClick(manualBtClickHandler); } catch (e) {}

    try {
        manualBtChart.subscribeCrosshairMove(param => {
            if (!manualBtCandleSeries) return;
            if (!param || !param.point) {
                manualBtUpdateOhlcvReadout(manualBtVisibleHistory?.[manualBtVisibleHistory.length - 1] || null);
                return;
            }
            const price = manualBtCandleSeries.coordinateToPrice(param.point.y);
            if (Number.isFinite(price)) manualBtCrosshairPrice = price;
            
            if (param.time && manualBtVisibleHistory?.length) {
                const hoveredBar = manualBtVisibleHistory.find(b => Number(b.time) === Number(param.time));
                if (hoveredBar) {
                    manualBtUpdateOhlcvReadout(hoveredBar);
                }
            }

            if (manualBtDraggingLine && manualBtTrade[manualBtDraggingLine]) {
                manualBtTrade[manualBtDraggingLine].price = manualBtCrosshairPrice;
                manualBtDrawRisk();
                manualBtUpdateTradeStatus();
            }
        });
        
        manualBtChart.timeScale().subscribeVisibleLogicalRangeChange(range => {
            manualBtDrawRisk();
            manualBtSyncOscillatorRanges(range);
        });
        manualBtChart.timeScale().subscribeVisibleTimeRangeChange(() => {
            manualBtRefreshOverlayPriceLineLabels();
        });
        
        manualBtDragMousedownHandler = (e) => {
            if (!manualBtCrosshairPrice || !manualBtTrade.entry || !manualBtCandleSeries) return;
            const y = e.offsetY;
            if (manualBtTrade.sl && manualBtTrade.sl.price) {
                const slY = manualBtCandleSeries.priceToCoordinate(manualBtTrade.sl.price);
                if (slY !== null && Math.abs(y - slY) < 15) {
                    manualBtDraggingLine = 'sl';
                    manualBtChart.applyOptions({ handleScroll: false, handleScale: false });
                    return;
                }
            }
            if (manualBtTrade.tp && manualBtTrade.tp.price) {
                const tpY = manualBtCandleSeries.priceToCoordinate(manualBtTrade.tp.price);
                if (tpY !== null && Math.abs(y - tpY) < 15) {
                    manualBtDraggingLine = 'tp';
                    manualBtChart.applyOptions({ handleScroll: false, handleScale: false });
                    return;
                }
            }
        };
        
        manualBtDragMouseupHandler = () => {
            if (manualBtDraggingLine) {
                manualBtDraggingLine = null;
                if (manualBtChart) {
                    manualBtChart.applyOptions({ handleScroll: true, handleScale: true });
                    manualBtUpdateTradeStatus();
                }
            }
        };

        container.addEventListener('mousedown', manualBtDragMousedownHandler);
        window.addEventListener('mouseup', manualBtDragMouseupHandler);
    } catch (e) {}

    try {
        if (sourceHistory || manualBtReplayActive || history.length <= 20) {
            const visibleCount = Math.max(1, history.length);
            const from = visibleCount <= 2 ? -20 : Math.max(-10, visibleCount - 80);
            const to = Math.max(30, visibleCount + 8);
            manualBtChart.timeScale().setVisibleLogicalRange({ from, to });
        } else {
            manualBtChart.timeScale().fitContent();
        }
    } catch (e) {}
    try { manualBtChart.timeScale().subscribeVisibleTimeRangeChange(() => manualBtDrawRisk()); } catch (e) {}
    manualBtResizeObserver = new ResizeObserver(entries => {
        if (!manualBtChart || !entries.length) return;
        const { width, height } = entries[0].contentRect;
        manualBtChart.applyOptions({ width, height });
        Object.values(manualBtOscCharts || {}).forEach(chart => {
            const host = chart?.__manualBtHost;
            if (!host) return;
            try { chart.applyOptions({ width: Math.max(host.clientWidth || width, 1), height: Math.max(host.clientHeight || 120, 1) }); } catch (e) {}
        });
        manualBtDrawRisk();
    });
    manualBtResizeObserver.observe(container);
    manualBtUpdateTradeStatus();
    manualBtReplayUpdateControls();
}

function manualBtVisibleLogicalRange() {
    try {
        const range = manualBtChart?.timeScale?.().getVisibleLogicalRange?.();
        if (range && Number.isFinite(Number(range.from)) && Number.isFinite(Number(range.to))) {
            return { from: Number(range.from), to: Number(range.to) };
        }
    } catch (e) {}
    return null;
}

function manualBtRestoreVisibleLogicalRange(range) {
    if (!range || !manualBtChart) return;
    try {
        manualBtChart.timeScale().setVisibleLogicalRange(range);
    } catch (e) {}
}

function manualBtCurrentViewHistory() {
    const fullHistory = manualBtFilteredHistory();
    if (!fullHistory.length) return [];
    if (manualBtReplayActive) {
        const cursor = Math.max(0, Math.min(fullHistory.length - 1, Number(manualBtReplayCursor) || 0));
        return fullHistory.slice(0, cursor + 1);
    }
    if (Array.isArray(manualBtVisibleHistory) && manualBtVisibleHistory.length) {
        return [...manualBtVisibleHistory];
    }
    return fullHistory;
}

function manualBtRefreshCurrentView() {
    const visibleRange = manualBtVisibleLogicalRange();
    const wasReplayActive = Boolean(manualBtReplayActive);
    const wasReplayPlaying = Boolean(manualBtReplayPlaying);
    const replayCursor = Number(manualBtReplayCursor);
    const history = manualBtCurrentViewHistory();
    manualBtRenderChart(history, wasReplayActive);
    if (wasReplayActive && Number.isFinite(replayCursor)) {
        manualBtReplayCursor = replayCursor;
    }
    manualBtReplayPlaying = wasReplayPlaying && manualBtReplayActive;
    manualBtRestoreVisibleLogicalRange(visibleRange);
    manualBtReplayUpdateControls();
}

function manualBtRefreshIndicatorsOnly() {
    const history = manualBtCurrentViewHistory();
    if (!manualBtChart || !manualBtCandleSeries || !history.length) {
        manualBtRefreshCurrentView();
        return;
    }
    manualBtVisibleHistory = history;
    manualBtClearOverlaySeries();
    manualBtRenderOverlays(history);
    manualBtCandleSeries.setMarkers(manualBtMarkers(history));
    manualBtRenderOscillators(history);
    manualBtRenderConfluence(history);
    manualBtDrawRisk();
    manualBtUpdateTradeStatus();
    manualBtRefreshOverlayPriceLineLabels();
}

function manualBtStopReplay() {
    if (manualBtReplayTimer) clearTimeout(manualBtReplayTimer);
    manualBtReplayTimer = null;
    manualBtReplayPlaying = false;
    manualBtReplayActive = false;
    manualBtReplayStatusNote = '';
    manualBtUpdateReplayStartControl();
    manualBtReplayUpdateControls();
}

function manualBtPauseReplay(note = '') {
    if (manualBtReplayTimer) clearTimeout(manualBtReplayTimer);
    manualBtReplayTimer = null;
    manualBtReplayPlaying = false;
    manualBtReplayStatusNote = note || '';
    manualBtReplayUpdateControls();
}

function manualBtRenderReplayFrame(cursor = manualBtReplayCursor) {
    const fullHistory = manualBtFilteredHistory();
    if (!fullHistory.length) {
        manualBtReplayUpdateControls();
        return;
    }
    manualBtReplayCursor = Math.max(0, Math.min(fullHistory.length - 1, Math.round(Number(cursor) || 0)));
    const history = fullHistory.slice(0, manualBtReplayCursor + 1);
    manualBtVisibleHistory = history;

    if (manualBtChart && manualBtCandleSeries && manualBtVolumeSeries) {
        manualBtCandleSeries.setData(history);
        manualBtVolumeSeries.setData(history.map(c => ({ time: c.time, value: Number(c.volume || 0), color: c.close >= c.open ? 'rgba(16, 185, 129, 0.4)' : 'rgba(239, 68, 68, 0.4)' })));
        if (manualBtExtHoursSeries) {
            const ticker = manualBtTicker || '';
            const tf = manualBtTimeframe || '1Hour';
            if (!isCrypto(ticker) && manualBtSession === 'extended' && !String(tf).includes('Day')) {
                manualBtExtHoursSeries.setData(manualBtExtendedHoursBackground(history));
            } else {
                manualBtExtHoursSeries.setData([]);
            }
        }
        
        manualBtClearOverlaySeries();
        manualBtRenderOverlays(history);
        
        manualBtRenderOscillators(history);
        
        manualBtRenderConfluence(history);
        manualBtDrawRisk();
        manualBtUpdateTradeStatus();
        manualBtCandleSeries.setMarkers(manualBtMarkers(history));
        manualBtUpdateHeader(history);
    } else {
        manualBtRenderChart(history, true);
    }
    
    manualBtReplayUpdateControls();
}

function manualBtReplayTick() {
    if (!manualBtReplayActive || !manualBtReplayPlaying) return;
    const history = manualBtFilteredHistory();
    if (manualBtReplayCursor == null) manualBtReplayCursor = 0;
    
    if (manualBtReplayCursor >= history.length - 1) {
        manualBtPauseReplay(`Replay complete · ${history.length}/${history.length} · ${manualBtReplaySpeed}x`);
        return;
    }
    manualBtReplayCursor += 1;
    manualBtReplayStatusNote = '';
    manualBtRenderReplayFrame(manualBtReplayCursor);
    
    const exit = manualBtResolvedExit();
    if (exit && Number(exit.time) === Number(history[manualBtReplayCursor].time)) {
        manualBtPauseReplay(`Stopped at ${exit.kind.toUpperCase()} · ${manualBtReplayCursor + 1}/${history.length} · ${manualBtReplaySpeed}x`);
        return;
    }
    manualBtReplayTimer = setTimeout(manualBtReplayTick, Math.max(80, Math.round(1000 / manualBtReplaySpeed)));
}

window.manualBtToggleReplay = function() {
    const range = manualBtVisibleLogicalRange();
    if (manualBtReplayActive) {
        manualBtStopReplay();
        manualBtRenderChart();
        if (range) setTimeout(() => manualBtRestoreVisibleLogicalRange(range), 0);
        return;
    }
    const history = manualBtFilteredHistory();
    if (!history.length) {
        manualBtSetReplayStatus('No candles to replay');
        return;
    }
    manualBtReplayStartIndex = Math.max(0, Math.min(history.length - 1, Number(manualBtReplayStartIndex) || 0));
    manualBtReplayActive = true;
    manualBtReplayPlaying = false;
    manualBtReplayCursor = manualBtReplayStartIndex;
    manualBtReplayStatusNote = '';
    manualBtUpdateReplayStartControl(history, true);
    manualBtRenderReplayFrame(manualBtReplayCursor);
    if (range) setTimeout(() => manualBtRestoreVisibleLogicalRange(range), 0);
};

window.manualBtToggleReplayPlayback = function() {
    const history = manualBtFilteredHistory();
    if (!history.length) {
        manualBtSetReplayStatus('No candles to replay');
        return;
    }
    if (!manualBtReplayActive) {
        window.manualBtToggleReplay();
    }
    if (manualBtReplayPlaying) {
        manualBtPauseReplay();
        return;
    }
    manualBtReplayStatusNote = '';
    manualBtReplayPlaying = true;
    manualBtReplayUpdateControls();
    manualBtReplayTimer = setTimeout(manualBtReplayTick, Math.max(80, Math.round(1000 / manualBtReplaySpeed)));
};

window.manualBtSetReplaySpeed = function(value) {
    const speed = Number(value);
    manualBtReplaySpeed = Number.isFinite(speed) ? Math.max(1, Math.min(10, speed)) : 1;
    manualBtReplayStatusNote = '';
    if (manualBtReplayPlaying) {
        if (manualBtReplayTimer) clearTimeout(manualBtReplayTimer);
        manualBtReplayTimer = setTimeout(manualBtReplayTick, Math.max(80, Math.round(1000 / manualBtReplaySpeed)));
    }
    manualBtReplayUpdateControls();
};

window.manualBtStepReplay = function(delta) {
    if (!manualBtReplayActive) return;
    manualBtPauseReplay();
    manualBtReplayStatusNote = '';
    manualBtRenderReplayFrame((Number(manualBtReplayCursor) || 0) + Number(delta || 0));
};

window.manualBtSeekReplay = function(value) {
    if (!manualBtReplayActive) return;
    manualBtPauseReplay();
    manualBtReplayStatusNote = '';
    manualBtRenderReplayFrame(Number(value) || 0);
};

window.manualBtResetReplay = function() {
    if (!manualBtReplayActive) return;
    manualBtPauseReplay();
    manualBtReplayStatusNote = '';
    manualBtReplayCursor = manualBtReplayStartIndex;
    manualBtRenderReplayFrame(manualBtReplayCursor);
};

window.toggleManualBtChartPreviewIndicator = function(name) {
    const prefs = manualBtIndicatorPrefs();
    if (!Object.prototype.hasOwnProperty.call(prefs, name)) return;
    const mode = currentBtMode === 'options' ? 'options' : 'regular';
    if (!isBacktestIndicatorAllowed(name, mode, manualBtTicker)) {
        prefs[name] = false;
        manualBtSaveIndicatorPrefs(prefs);
        manualBtRefreshIndicatorsOnly();
        return;
    }
    const enabled = prefs[name] !== true;
    prefs[name] = enabled;
    manualBtSaveIndicatorPrefs(prefs);
    if (enabled && manualBtTicker) {
        // A newly enabled indicator may not exist in the current payload.
        // Fetch it once; disabling only needs an immediate local redraw.
        runBacktest({ chartOnly: true, force: true });
    } else {
        manualBtRefreshIndicatorsOnly();
    }
};

window.resetManualBtChartPreviewIndicatorsToDefaults = function() {
    const prefs = {};
    MANUAL_BT_CHART_PREVIEW_INDICATORS.forEach(name => {
        if (name !== 'Botbulls4 News AI') {
            prefs[name] = false;
        }
    });
    manualBtSaveIndicatorPrefs(prefs);
    
    // Also instruct the workspace to reset if the API is available
    if (window.IndicatorWorkspace?.resetScope) {
        const mode = typeof currentBtMode !== 'undefined' && currentBtMode === 'options' ? 'options' : 'regular';
        const contextLabel = mode === 'options' ? 'backtest-options' : 'backtest-regular';
        window.IndicatorWorkspace.resetScope(contextLabel, manualBtTicker);
    }
    
    manualBtRefreshIndicatorsOnly();
};

window.manualBtSetReplayStart = function(value) {
    const history = manualBtFilteredHistory();
    if (!history.length || manualBtReplayActive) return;
    manualBtReplayStartIndex = Math.max(0, Math.min(history.length - 1, Number(value) || 0));
    manualBtReplayCursor = manualBtReplayStartIndex;
    manualBtUpdateReplayStartControl(history, true);
    manualBtSetReplayStatus(`Ready from ${manualBtReplayStartIndex + 1}/${history.length}`);
    manualBtRenderChart(history.slice(0, manualBtReplayStartIndex + 1));
};

window.manualBtSelectPoint = function(kind) {
    manualBtPickTarget = kind;
    manualBtUpdateTradeStatus();
};

window.manualBtClearTrade = function() {
    manualBtPickTarget = null;
    manualBtTrade = { entry: null, tp: null, sl: null };
    manualBtRefreshCurrentView();
};

window.setManualBtChartPreviewRange = function(range) {
    manualBtRange = range || '1M';
    const source = manualBtSourcePref();
    const days = manualBtRangeDays(manualBtRange, manualBtMaxDaysForTimeframe(manualBtTimeframe || '1Hour', source));
    // Mirror dashboard behavior: large ranges promote tiny intervals to a
    // higher timeframe instead of letting the historical request fail.
    if (['1Y'].includes(manualBtRange) && ['1Min', '5Min', '10Min', '15Min', '30Min'].includes(manualBtTimeframe || '1Hour')) {
        manualBtTimeframe = '1Hour';
    } else if (['5Y', 'MAX'].includes(manualBtRange) && ['1Min', '5Min', '10Min', '15Min', '30Min', '1Hour'].includes(manualBtTimeframe || '1Hour')) {
        manualBtTimeframe = '4Hour';
    } else if (days > manualBtMaxDaysForTimeframe(manualBtTimeframe || '1Hour', source)) {
        manualBtRange = manualBtCompatibleRange(manualBtRange, manualBtTimeframe || '1Hour', source);
        if (source === 'webull') {
            manualBtSetReplayStatus(`Webull fast load capped to ${manualBtRange} for ${manualBtTimeframe || '1Hour'}`);
        }
    }
    manualBtLoadOrRenderAfterViewChange();
};

window.setManualBtChartPreviewSession = function(session) {
    manualBtSession = session || 'regular';
    manualBtLoadOrRenderAfterViewChange();
};

window.setManualBtDataSource = function(source) {
    manualBtSourcePreference = String(source || 'webull').toLowerCase() === 'yahoo' ? 'yahoo' : 'webull';
    manualBtStopReplay();
    manualBtReplayStartIndex = 0;
    if (!manualBtTicker) {
        manualBtSetReplayStatus('Search a ticker to load chart');
        return;
    }
    runBacktest({ chartOnly: true, force: true });
};

window.setManualBtChartPreviewTimeframe = function(tf) {
    manualBtTimeframe = tf || manualBtTimeframe || '1Hour';
    manualBtLoadOrRenderAfterViewChange();
};

window.openManualBtChartEmpty = function() {
    manualBtStopReplay();
    manualBtTicker = '';
    manualBtSyncDaySpanForView();
    manualBtHistory = [];
    manualBtDisplayHistory = [];
    manualBtSignals = {};
    manualBtTrade = { entry: null, tp: null, sl: null };
    manualBtPickTarget = null;
    manualBtReplayStartIndex = 0;
    manualBtReplayCursor = null;
    manualBtTimeframe = manualBtTimeframe || '1Hour';

    // Turn all indicators off by default and show only default lists
    const prefs = {};
    MANUAL_BT_CHART_PREVIEW_INDICATORS.forEach(name => {
        if (name !== 'Botbulls4 News AI') prefs[name] = false;
    });
    manualBtSaveIndicatorPrefs(prefs);

    if (window.IndicatorWorkspace?.resetScope) {
        const mode = typeof currentBtMode !== 'undefined' && currentBtMode === 'options' ? 'options' : 'regular';
        const contextLabel = mode === 'options' ? 'backtest-options' : 'backtest-regular';
        window.IndicatorWorkspace.resetScope(contextLabel, '');
    }

    document.getElementById('manualBtChartPreviewModal')?.classList.remove('hidden');
    const searchInput = document.getElementById('manualBtTickerSearch');
    if (searchInput) {
        searchInput.value = '';
        setTimeout(() => searchInput.focus(), 80);
    }
    document.getElementById('manualBtTickerSearchResults')?.classList.add('hidden');
    manualBtDestroyChart(false);
    manualBtUpdateHeader([]);
    manualBtRenderConfluence([]);
    manualBtUpdateReplayStartControl([], true);
    manualBtSetText('manualBtChartPreviewBanner', 'Search a ticker above to load manual replay candles. All indicators are turned off by default.');
    document.getElementById('manualBtChartPreviewBanner')?.classList.remove('hidden');
    manualBtSetReplayStatus('Search a ticker to load chart');
    manualBtSetText('manualBtTradeStatus', 'Load a ticker, then pick Entry, TP Area, or SL Area.');
};

window.loadManualBtChartTicker = function(rawTicker) {
    const ticker = String(rawTicker || '').trim().toUpperCase().replace(/[^A-Z0-9.\-]/g, '');
    if (!ticker) {
        manualBtSetReplayStatus('Enter a ticker first');
        return;
    }
    manualBtTicker = ticker;
    
    // As requested: all indicators turned off by default for new indicator search
    const prefs = {};
    MANUAL_BT_CHART_PREVIEW_INDICATORS.forEach(name => {
        if (name !== 'Botbulls4 News AI') prefs[name] = false;
    });
    manualBtSaveIndicatorPrefs(prefs);
    
    // Re-verify the workspace scope maintains the default list for the new ticker
    if (window.IndicatorWorkspace?.resetScope) {
        const mode = typeof currentBtMode !== 'undefined' && currentBtMode === 'options' ? 'options' : 'regular';
        const contextLabel = mode === 'options' ? 'backtest-options' : 'backtest-regular';
        window.IndicatorWorkspace.resetScope(contextLabel, ticker);
    }
    manualBtTimeframe = manualBtTimeframe || '1Hour';
    manualBtSyncDaySpanForView();
    manualBtTrade = { entry: null, tp: null, sl: null };
    manualBtPickTarget = null;
    manualBtReplayStartIndex = 0;
    manualBtReplayCursor = null;
    const input = document.getElementById('manualBtTickerSearch');
    if (input) input.value = ticker;
    document.getElementById('manualBtTickerSearchResults')?.classList.add('hidden');
    runBacktest({ chartOnly: true, force: true });
};

window.closeManualBtChartPreview = function() {
    manualBtStopReplay();
    manualBtDestroyChart();
    document.getElementById('manualBtChartPreviewModal')?.classList.add('hidden');
};

function openManualBtChartPreviewFromResults(res) {
    const s = res?.summary || {};
    btChartTimeframeRef = s.timeframe || res?.timeframe || manualBtTimeframe || '1Hour';
    manualBtTimeframe = btChartTimeframeRef || manualBtTimeframe;
    manualBtDataSource = s.data_source || res?.data_source || 'yahoo';
    const sourceSelect = document.getElementById('manualBtDataSourceSelect');
    if (sourceSelect) {
        sourceSelect.value = manualBtSourcePreference === 'yahoo' ? 'yahoo' : 'webull';
    }
    manualBtSignals = res?.signals || s.signals || {};
    // The replay endpoint already applies the requested Manual Backtest
    // session. Do not pass these candles through the strategy-result chart's
    // separate session selector, or extended candles can be removed before
    // the manual chart receives them.
    manualBtHistory = manualBtCleanCandles((res?.candles || []).map(c => manualBtDisplayCandle(c, btChartTimeframeRef)));
    manualBtDisplayHistory = manualBtFilteredHistory();
    manualBtTrade = { entry: null, tp: null, sl: null };
    manualBtPickTarget = null;
    manualBtReplayStartIndex = 0;
    manualBtReplayCursor = null;
    manualBtSession = isCrypto(manualBtTicker || '') ? 'crypto24' : (manualBtSession === 'extended' ? 'extended' : 'regular');
    document.getElementById('manualBtChartPreviewModal')?.classList.remove('hidden');
    setTimeout(() => manualBtRenderChart(), 50);
}

function displayManualBacktestChart(res) {
    if (!res?.candles?.length) {
        manualBtSetText('manualBtChartPreviewBanner', 'No manual chart candles were returned for the selected ticker/session/timeframe.');
        document.getElementById('manualBtChartPreviewBanner')?.classList.remove('hidden');
        manualBtSetReplayStatus('No candles returned');
        return;
    }
    openManualBtChartPreviewFromResults(res);
}

// 2. Strategy Execution
async function runBacktest(options = {}) {
    const forceRun = options?.force === true;
    const chartOnly = options?.chartOnly === true;
    const runBtn = document.getElementById('runBtBtn');
    if (runBtn?.disabled && !forceRun) return;

    const ticker = chartOnly
        ? String(manualBtTicker || '').trim().toUpperCase()
        : document.getElementById('btTicker').value.toUpperCase();
    if (chartOnly && !ticker) {
        manualBtSetReplayStatus('Search a ticker to load chart');
        return;
    }
    let days = chartOnly ? String(manualBtSyncDaySpanForView()) : document.getElementById('btDays').value;
    const strategyTimeframe = document.getElementById('strategyTimeframe_regular')?.value || '1Hour';
    const timeframe = chartOnly ? (manualBtTimeframe || '1Hour') : strategyTimeframe;
    const capital = chartOnly ? '0' : currentBtMode === 'regular'
        ? (document.getElementById('strategyTradeAmount_regular')?.value || '1000')
        : (document.getElementById('strategyOptionsTradeAmount')?.value || document.getElementById('strategyCapital_options')?.value || '1000');

    // Validate requested days based on timeframe/source limits.
    let maxDays = 1825;
    let limitDesc = "5 years";
    if (chartOnly) {
        maxDays = manualBtMaxDaysForTimeframe(timeframe, manualBtSourcePref());
        limitDesc = `${maxDays} day${maxDays === 1 ? '' : 's'}`;
    } else if (timeframe === '1Min') {
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
        if (chartOnly) {
            manualBtDays = maxDays;
            days = String(maxDays);
            manualBtRange = manualBtCompatibleRange(manualBtRange, timeframe, manualBtSourcePref());
        } else {
        alert(`Historical chart data supports a maximum of ${limitDesc} for the ${timeframe} interval. Please reduce the requested days to ${maxDays} or less.`);
        return;
        }
    }

    const regularStrategyPayload = !chartOnly && (!currentBtMode || currentBtMode === 'regular') ? getBtRegularStrategyPayload() : null;
    const extHoursVal = !chartOnly && currentBtMode === 'regular' ? regularStrategyPayload.ext_hours : false;
    const closeBeforeExpiry = 0;

    const optionsEnabled = (!chartOnly && currentBtMode === 'options');

    const cooldownMode = optionsEnabled ? 'options' : 'regular';
    const enableCooldown = chartOnly ? false : (document.getElementById(`strategyEnableCooldown_${cooldownMode}`)?.checked ?? true);
    let cooldownVal = chartOnly ? 'default' : (document.getElementById(`strategyCooldownMinutes_${cooldownMode}`)?.value || 'default');
    let cooldownMinutes = null;
    if (cooldownVal === 'custom') {
        cooldownMinutes = parseInt(document.getElementById(`strategyCooldownCustomMinutes_${cooldownMode}`)?.value || '60');
    } else if (cooldownVal !== 'default') {
        cooldownMinutes = parseInt(cooldownVal);
    }

    const indicators = chartOnly
        ? []
        : getBtCheckedIndicatorValues(optionsEnabled ? 'options' : 'regular');

    const indicatorModes = ['indicator', 'hybrid', 'hybrid_atr', 'hybrid_pct', 'hybrid_pivot', 'hybrid_high_low', 'hybrid_swing'];
    const modeVal = optionsEnabled ? 'options' : 'regular';
    const buyModeVal = chartOnly ? '' : (document.getElementById(`strategyBuyMode_${modeVal}`)?.value || 'indicator');
    const sellModeVal = chartOnly ? '' : (document.getElementById(`strategySellMode_${modeVal}`)?.value || (optionsEnabled ? 'hybrid' : 'indicator'));

    if (!chartOnly && indicatorModes.includes(buyModeVal)) {
        if (indicators.length === 0) {
            const modeName = buyModeVal.replace(/_/g, ' ').toUpperCase();
            alert(`You selected [${modeName}] as your Entry Strategy, which requires technical indicators to generate buy signals.\n\nPlease check at least 1 indicator from the Indicator Pool below, or switch your Entry Strategy dropdown to a standalone mode.`);
            return;
        }
        const buyThreshold = getBtSignalThreshold(modeVal, 'buy', indicators.length);
        if (buyThreshold > indicators.length) {
            alert(`Your Entry Strategy requires ${buyThreshold} buy signals to trigger an entry, but only ${indicators.length} indicator(s) are checked in your pool!\n\nPlease lower your Buy Signal Threshold to match your checked indicators (between 1 and ${indicators.length}), or check more indicators below.`);
            return;
        }
    }
    if (!chartOnly && indicatorModes.includes(sellModeVal)) {
        if (indicators.length === 0) {
            const modeName = sellModeVal.replace(/_/g, ' ').toUpperCase();
            alert(`You selected [${modeName}] as your Exit Strategy, which requires technical indicators to generate sell signals.\n\nPlease check at least 1 indicator from the Indicator Pool below, or switch your Exit Strategy dropdown to a standalone mode.`);
            return;
        }
        const sellThreshold = getBtSignalThreshold(modeVal, 'sell', indicators.length);
        if (sellThreshold > indicators.length) {
            alert(`Your Exit Strategy requires ${sellThreshold} sell signals to trigger an exit, but only ${indicators.length} indicator(s) are checked in your pool!\n\nPlease lower your Sell Signal Threshold to match your checked indicators (between 1 and ${indicators.length}), or check more indicators below.`);
            return;
        }
    }

    // UI Feedback
    const chartBtn = document.getElementById('runBtChartBtn');
    if (chartOnly) {
        document.getElementById('manualBtChartPreviewBoxLoading')?.classList.remove('hidden');
        manualBtSetReplayStatus(`Loading ${ticker} chart...`);
    } else {
        if (runBtn) {
            runBtn.disabled = true;
            runBtn.classList.add('hidden');
        }
        if (chartBtn) {
            chartBtn.disabled = true;
            chartBtn.classList.add('hidden');
        }
        document.getElementById('btResultsModal')?.classList.add('hidden');
        document.getElementById('btSettingsContainer').classList.add('hidden');
        document.getElementById('btResults').classList.add('hidden');
        document.getElementById('btLoading').classList.remove('hidden');
    }

    try {
        backtestRequestInProgress = true;
        const headers = await getAuthHeaders();
        let response;
        let manualReplayCacheKey = null;

        if (chartOnly) {
            const marketSession = manualBtSession === 'extended' ? 'Extended' : 'Regular';
            const manualIndicators = manualBtSelectedIndicators();
            const manualSource = manualBtSourcePref();
            manualBtSyncDaySpanForView();
            days = String(manualBtDays);
            const indicatorOverrides = typeof getIndicatorOverrides === 'function'
                ? { ...getIndicatorOverrides() }
                : { ...(window.lastBacktestData?.indicator_overrides || {}) };
            manualReplayCacheKey = manualBtReplayCacheKey({
                ticker,
                timeframe,
                days: parseInt(days),
                source: manualSource,
                marketSession,
                indicators: manualIndicators,
                overrides: indicatorOverrides,
            });
            manualBtTimeframe = timeframe;
            manualBtSourcePreference = manualSource;
            try {
                const cachedReplay = forceRun ? null : manualBtReplayDataCache.get(manualReplayCacheKey);
                if (cachedReplay) {
                    console.log(`[backtest] Manual chart cache hit: ${ticker} ${timeframe} ${manualSource}`);
                    manualBtSetReplayStatus(`Loaded ${ticker} ${timeframe} ${manualBtRange} from cache`);
                    response = {
                        ok: true,
                        json: async () => manualBtClonePayload(cachedReplay),
                    };
                } else {
                    manualBtSetReplayStatus(`Loading ${ticker} ${timeframe} ${manualBtRange} from ${manualSource.toUpperCase()}...`);
                    console.log(`[backtest] Starting independent ${manualSource.toUpperCase()} manual chart request (${ticker} ${timeframe} ${manualBtRange}/${days}d) to: ${window.BACKTEST_API_BASE}/api/backtest/replay/data`);
                    const replayResponse = await fetch(`${window.BACKTEST_API_BASE}/api/backtest/replay/data`, {
                        method: 'POST',
                        headers,
                        body: JSON.stringify({
                            ticker,
                            timeframe,
                            days: parseInt(days),
                            indicators: manualIndicators,
                            indicator_overrides: indicatorOverrides,
                            market_session: marketSession,
                            source: manualSource,
                            buy_mode: buyModeVal,
                            sell_mode: sellModeVal,
                        }),
                    });
                    if (!replayResponse.ok) {
                        throw new Error(`Manual chart endpoint HTTP ${replayResponse.status}`);
                    }
                    response = replayResponse;
                }
            } catch (manualErr) {
                console.warn('[manual-backtest] Independent chart endpoint failed:', manualErr);
                throw manualErr;
            }
        }

        if (!response && optionsEnabled) {
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
            const dividendYield = parseFloat(document.getElementById('strategyOptionsDividendYield')?.value || '0.0') / 100.0;
            const optionContractFee = parseFloat(document.getElementById('strategyOptionsContractFee')?.value || '0.65');

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
                grid_continuous_mode: document.getElementById('strategyGridContinuous_options')?.checked || false,
                grid_buy_allocation: parseFloat(document.getElementById('strategyGridBuyAllocation_options')?.value || '1000'),
                grid_sell_pct: parseFloat(document.getElementById('strategyGridSellPct_options')?.value || '50'),
                grid_max_levels: parseInt(document.getElementById('strategyGridMaxLevels_options')?.value || '5'),
                exit_grid_continuous_mode: document.getElementById('strategyExitGridContinuous_options')?.checked || false,
                exit_grid_sell_pct: parseFloat(document.getElementById('strategyExitGridSellPct_options')?.value || '50'),
                exit_grid_max_levels: parseInt(document.getElementById('strategyExitGridMaxLevels_options')?.value || '5'),
                swing_entry_mode: swingEntryMode,
                swing_entry_lookback_days: swingEntryLookbackDays,
                swing_exit_lookback_days: swingExitLookbackDays,
                indicators, ext_hours: extHoursVal, r, dividend_yield: dividendYield, option_contract_fee: optionContractFee, results: null,
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
                    threshold: getBtSignalThreshold('options', 'buy', indicators.length),
                    sell_threshold: getBtSignalThreshold('options', 'sell', indicators.length),
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
                    grid_continuous_mode: document.getElementById('strategyGridContinuous_options')?.checked || false,
                    grid_buy_allocation: parseFloat(document.getElementById('strategyGridBuyAllocation_options')?.value || '1000'),
                    grid_sell_pct: parseFloat(document.getElementById('strategyGridSellPct_options')?.value || '50'),
                    grid_max_levels: parseInt(document.getElementById('strategyGridMaxLevels_options')?.value || '5'),
                    exit_grid_continuous_mode: document.getElementById('strategyExitGridContinuous_options')?.checked || false,
                    exit_grid_sell_pct: parseFloat(document.getElementById('strategyExitGridSellPct_options')?.value || '50'),
                    exit_grid_max_levels: parseInt(document.getElementById('strategyExitGridMaxLevels_options')?.value || '5'),
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
                    dividend_yield: dividendYield,
                    option_contract_fee: optionContractFee,
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

        } else if (!response) {
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
                    threshold: getBtSignalThreshold('regular', 'buy', indicators.length),
                    sell_threshold: getBtSignalThreshold('regular', 'sell', indicators.length),
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
        if (chartOnly && manualReplayCacheKey && data?.status === 'success') {
            manualBtCacheReplayPayload(manualReplayCacheKey, data);
        }

        if (data.status === 'success') {
            let resToDisplay = data.results || data;
            if (data.results && data.results.outsample_results) {
                resToDisplay = data.results.outsample_results;
                resToDisplay.optimizer_meta = {
                    overfit_score: data.results.overfit_score,
                    insample_roi: data.results.insample_results?.summary?.roi_pct || 0,
                    best_params: data.results.best_parameters
                };
            }
            if (!chartOnly && window.lastBacktestRun) {
                window.lastBacktestRun.results = resToDisplay;
            }
            if (chartOnly) {
                displayManualBacktestChart(resToDisplay);
            } else {
                displayResults(resToDisplay);
            }
            if (window.lastBacktestRun && resToDisplay && resToDisplay.summary) {
            }
        } else {
            const errMsg = data.message || data.error || "Unknown error";
            if (chartOnly) {
                manualBtSetText('manualBtChartPreviewBanner', `Manual chart load failed: ${errMsg}`);
                document.getElementById('manualBtChartPreviewBanner')?.classList.remove('hidden');
                manualBtSetReplayStatus('Chart load failed');
            } else {
                alert("Backtest Failed: " + errMsg);
                resetBacktestUI();
            }
        }
    } catch (err) {
        console.error("BACKTEST FETCH ERROR:", err);
        if (chartOnly) {
            manualBtSetText('manualBtChartPreviewBanner', `Manual chart connection failed: ${err.message}`);
            document.getElementById('manualBtChartPreviewBanner')?.classList.remove('hidden');
            manualBtSetReplayStatus('Chart load failed');
        } else {
            alert(`Connection Failed: ${err.message}\n\nTroubleshooting:\n1. Ensure you have a stable internet connection.\n2. If on mobile, try disabling VPN or Private Relay.\n3. Try a Hard Refresh (Cmd+Shift+R or clear mobile cache).`);
            resetBacktestUI();
        }
    } finally {
        backtestRequestInProgress = false;
        if (chartOnly) {
            document.getElementById('manualBtChartPreviewBoxLoading')?.classList.add('hidden');
        }
    }
}

// 3. Results Rendering
function displayResults(res) {
    document.getElementById('btLoading').classList.add('hidden');
    const runBtn = document.getElementById('runBtBtn');
    if (runBtn) {
        runBtn.disabled = false;
        runBtn.classList.remove('hidden');
    }
    const chartBtn = document.getElementById('runBtChartBtn');
    if (chartBtn) {
        chartBtn.disabled = false;
        chartBtn.classList.remove('hidden');
    }
    
    // Show Modal
    const modal = document.getElementById('btResultsModal');
    modal.classList.remove('hidden');
    document.getElementById('btResults').classList.remove('hidden');

    const s = res.summary;
    const formatMetric = (value, digits = 2, suffix = '') => {
        if (value === undefined) return '—';
        if (value === null) return suffix === 'x' ? '∞' : '—';
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
                const g = s.avg_greeks || {};
                const avgIvPct = Number.isFinite(Number(g.iv)) ? (Number(g.iv) * 100).toFixed(1) : '—';
                const greeksBadge = g.delta !== undefined ? ` <span class="ml-1 px-2 py-0.5 rounded bg-purple-500/30 text-purple-200 border border-purple-400/40 font-mono" title="Average Black-Scholes-Merton Greeks across executions">Avg Greeks: Δ ${g.delta} · Γ ${g.gamma} · Θ ${g.theta} · ν ${g.vega} · IV ${avgIvPct}%</span>` : '';
                optionBadge = ` <span class="ml-2 px-2 py-0.5 rounded bg-fuchsia-500/30 text-fuchsia-200 border border-fuchsia-400/40">${optLabel} · ${s.expiry_days} DTE · ${s.strike_type}</span>${greeksBadge}`;
            }
            const riskBadge = s.enable_risk !== undefined ? ` <span class="ml-1 px-2 py-0.5 rounded ${s.enable_risk ? 'bg-indigo-500/30 text-indigo-200' : 'bg-rose-500/30 text-rose-200'}">Risk: ${s.enable_risk ? 'ON' : 'OFF'}</span>` : '';
            const sourceBadge = s.data_source ? ` <span class="ml-1 px-2 py-0.5 rounded bg-emerald-500/20 text-emerald-200 border border-emerald-400/30">Data: ${String(s.data_source).toUpperCase()}</span>` : '';
            const executionBadge = s.execution_model ? ` <span class="ml-1 px-2 py-0.5 rounded bg-sky-500/20 text-sky-100 border border-sky-400/30" title="${s.execution_model}">Fills: Next Open</span>` : '';
            const quoteCapability = s.historical_quote_capabilities || {};
            const quoteBadge = quoteCapability.two_sided_rows > 0
                ? ` <span class="ml-1 px-2 py-0.5 rounded bg-emerald-500/20 text-emerald-100 border border-emerald-400/30">Historical NBBO</span>`
                : (quoteCapability.historical_rows > 0 ? ` <span class="ml-1 px-2 py-0.5 rounded bg-amber-500/20 text-amber-100 border border-amber-400/30" title="Historical option prices are marks; bid/ask was not supplied.">Historical marks</span>` : '');
            metaEl.innerHTML = `<div>${s.ticker}  ·  ${s.timeframe}  ·  ${s.period_days} Days  ·  ${startCST} – ${endCST} CST</div><div class="mt-1 flex flex-wrap items-center gap-1.5 text-[0.6rem]">${optionBadge}${executionBadge}${quoteBadge}${riskBadge}${sourceBadge}</div>`;
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
    if (s.profit_factor === null || s.profit_factor >= 1.5) pfEl.className = 'text-xl font-black tracking-tight text-emerald-600';
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
    btPrepareChartDataFromResults(res);
    btRenderPreparedChart('result');
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
            const eg = t.entry_greeks || {};
            const xg = t.exit_greeks || {};
            const formatIvPct = (displayIv, greekIv) => Number.isFinite(Number(displayIv))
                ? Number(displayIv).toFixed(1)
                : (Number.isFinite(Number(greekIv)) ? (Number(greekIv) * 100).toFixed(1) : '—');
            const egHtml = eg.delta !== undefined ? `<div class="text-[0.55rem] font-mono text-purple-600 bg-purple-50/80 px-1.5 py-0.5 rounded border border-purple-200/60 mt-1 inline-block" title="Black-Scholes-Merton entry Greeks">Δ ${eg.delta} · Γ ${eg.gamma} · Θ ${eg.theta} · ν ${eg.vega} · IV ${formatIvPct(t.entry_iv, eg.iv)}%</div>` : '';
            const xgHtml = xg.delta !== undefined ? `<div class="text-[0.55rem] font-mono text-indigo-600 bg-indigo-50/80 px-1.5 py-0.5 rounded border border-indigo-200/60 mt-1 inline-block" title="Black-Scholes-Merton exit Greeks">Δ ${xg.delta} · Γ ${xg.gamma} · Θ ${xg.theta} · ν ${xg.vega} · IV ${formatIvPct(t.exit_iv, xg.iv)}%</div>` : '';
            const sourceLabel = (source) => source === 'historical_obbo'
                ? 'Historical bid/ask'
                : (source === 'historical_option_mark' ? 'Historical mark' : 'BSM + spread');
            const entrySource = sourceLabel(t.entry_price_source);
            const exitSource = sourceLabel(t.exit_price_source);

            entryHtml = `
                <div class="text-xs font-semibold text-indigo-950">${formatDate(t.entry_time)}</div>
                <div class="text-[0.65rem] text-indigo-600 font-black mt-0.5">${t.option_type} $${t.strike} @ $${formatAssetPrice(t.entry_price, isCrypto(t.symbol || window.activeTicker))}</div>
                <div class="text-[0.55rem] text-slate-400">Stock: $${formatAssetPrice(t.entry_underlying, isCrypto(t.symbol || window.activeTicker))}</div>
                ${t.contract_symbol ? `<div class="text-[0.5rem] text-slate-400 font-mono truncate max-w-[160px]" title="${t.contract_symbol}">${t.contract_symbol}</div>` : ''}
                <div class="text-[0.5rem] text-slate-400">Fill: ${entrySource}</div>
                ${egHtml}
            `;
            exitHtml = `
                <div class="text-xs font-semibold text-indigo-950">${formatDate(t.exit_time)}</div>
                <div class="text-[0.65rem] text-indigo-600 font-black mt-0.5">Exit @ $${formatAssetPrice(t.exit_price, isCrypto(t.symbol || window.activeTicker))}</div>
                <div class="text-[0.55rem] text-slate-400">Stock: $${formatAssetPrice(t.exit_underlying, isCrypto(t.symbol || window.activeTicker))}</div>
                <div class="text-[0.5rem] text-slate-400">Fill: ${exitSource}</div>
                ${xgHtml}
                <div class="text-[0.55rem] text-rose-500 font-bold max-w-[140px] truncate mt-0.5" title="${t.reason || ''}">${t.reason || ''}</div>
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
    btPlaceChartInResultSlot();
    // ── NEW: Destroy chart to prevent stale renders on next run ──────────
    if (btChart && btManualChartClickHandler) {
        try { btChart.unsubscribeClick(btManualChartClickHandler); } catch (e) {}
    }
    btManualChartClickHandler = null;
    btRemoveManualTradePriceLines();
    btClearManualRiskRewardAreas();
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
        btTradeMarkersRef = [];
        btIndicatorMarkersRef = [];
        btStrategyChartTradesRef = [];
        btStrategyTradeMarkersRef = [];
        btChartEquityCurveRef = [];
        btChartTimeframeRef = null;
        btEqSeries = null;
    }
    if (btChartResizeObserver) {
        try { btChartResizeObserver.disconnect(); } catch (e) {}
        btChartResizeObserver = null;
    }
    btChartTimeframeRef = null;
    btManualChartEnabled = false;
    btManualPickTarget = null;
    btManualReplayVisibleTime = null;
    btManualTradePoints = { entry: null, exit: null, sl: null, tp: null };
    document.getElementById('btManualTradeToolbar')?.classList.add('hidden');
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
    const chartBtn = document.getElementById('runBtChartBtn');
    if (chartBtn) {
        chartBtn.disabled = false;
        chartBtn.classList.remove('hidden');
    }
    document.getElementById('btLoading').classList.add('hidden');
    window.lastBacktestRun = null;
}

window.loadBacktestIndicatorSettings = async function() {
    try {
        const headers = await getAuthHeaders();
        const response = await fetch(`${window.BACKTEST_API_BASE}/api/dashboard?mode=fast&source=backtest`, {
            headers,
            cache: 'no-store',
        });
        if (!response.ok) throw new Error(`HTTP ${response.status}`);
        const data = await response.json();
        window.lastBacktestData = {
            ...(window.lastBacktestData || {}),
            ...data,
            indicator_overrides: { ...(data.indicator_overrides || {}) },
            indicator_parameters: { ...(data.indicator_parameters || {}) },
        };
        window.btIndicatorOverrides = {};
        return window.lastBacktestData;
    } catch (error) {
        console.warn('[backtest] Could not load isolated indicator settings:', error);
        return window.lastBacktestData;
    }
};

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
    setCheck('strategyGridContinuous_regular', false);
    setVal('strategyGridBuyAllocation_regular', '1000');
    setVal('strategyGridSellPct_regular', '50');
    setVal('strategyGridMaxLevels_regular', '5');
    setCheck('strategyGridContinuous_options', false);
    setVal('strategyGridBuyAllocation_options', '1000');
    setVal('strategyGridSellPct_options', '50');
    setVal('strategyGridMaxLevels_options', '5');
    setVal('strategySwingLookbackDays_entry_regular', '20');
    setVal('strategyBuyThreshold_regular', '1');
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
    setVal('strategySellThreshold_regular', '1');
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
    setVal('strategyOptionsDividendYield', '0');
    setVal('strategyOptionsContractFee', '0.65');
    setVal('strategyOptionsAllocationMode', 'fixed');
    setVal('strategyOptionsTradeAmount', '1000');
    setVal('strategyOptionsCompoundPct', '10');
    setVal('strategyBuyMode_options', 'indicator');
    setVal('strategyCustomBuyPrice_options', '');
    setVal('strategyEntryAtrMult_options', '2');
    setVal('strategyEntryPctMove_options', '5');
    setVal('strategySwingLookbackDays_entry_options', '20');
    setVal('strategyBuyThreshold_options', '1');
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
    setVal('strategySellThreshold_options', '1');
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

function btReplayStopTimer() {
    if (btReplayTimer) {
        clearTimeout(btReplayTimer);
        btReplayTimer = null;
    }
    btReplayPlaying = false;
}

function btReplayCandleTime(candle) {
    const raw = Number(candle?.source_time ?? candle?.time);
    return Number.isFinite(raw) ? raw : null;
}

function btReplayVisibleCandles(cursor = btReplayCursor) {
    const end = Math.max(0, Math.min(btReplayFullCandlesRef.length - 1, Number(cursor) || 0));
    return btReplayFullCandlesRef.slice(0, end + 1);
}

function btManualExitDisplayTime() {
    const exitTime = Number(btManualResolvedExit()?.time);
    return Number.isFinite(exitTime) ? exitTime : null;
}

function btReplayCursorForDisplayTime(displayTime) {
    const target = Number(displayTime);
    if (!Number.isFinite(target) || !Array.isArray(btReplayFullCandlesRef) || !btReplayFullCandlesRef.length) return null;
    const chartTimeframe = getBtChartTimeframe();
    for (let i = 0; i < btReplayFullCandlesRef.length; i += 1) {
        const raw = btReplayCandleTime(btReplayFullCandlesRef[i]);
        const shown = raw == null ? null : btDisplayTime(raw, chartTimeframe);
        if (Number.isFinite(Number(shown)) && Number(shown) >= target) return i;
    }
    return btReplayFullCandlesRef.length - 1;
}

function btManualReplayExitCursor() {
    if (!btManualChartEnabled || !btManualTradeIsComplete()) return null;
    return btReplayCursorForDisplayTime(btManualExitDisplayTime());
}

function btReplayMarkersForTime(maxDisplayTime) {
    const tradeMarkers = (btTradeMarkersRef || []).filter(marker => Number(marker.time) <= maxDisplayTime);
    const indicatorMarkers = (btIndicatorMarkersRef || []).filter(marker => Number(marker.time) <= maxDisplayTime);
    return btMergeMarkersForToggles(tradeMarkers, indicatorMarkers);
}

function btMergeMarkersForToggles(tradeMarkers = btTradeMarkersRef, indicatorMarkers = btIndicatorMarkersRef) {
    const markBox = document.getElementById('btToggleMarkers');
    const indBox = document.getElementById('btToggleIndicators');
    const showTrades = markBox ? markBox.checked : true;
    const showIndicators = indBox ? indBox.checked : true;
    const markers = [
        ...(showTrades ? (tradeMarkers || []) : []),
        ...(showIndicators ? (indicatorMarkers || []) : []),
    ];
    const markerMap = {};
    markers.forEach(marker => {
        const key = `${marker.time}_${marker.position}`;
        if (!markerMap[key]) {
            markerMap[key] = { ...marker };
        } else {
            markerMap[key].text += ` | ${marker.text}`;
        }
    });
    return Object.values(markerMap).sort((a, b) => Number(a.time) - Number(b.time));
}

function btReplayRemoveAuxSeries() {
    if (!btChart) return;
    (btChartAuxSeries || []).forEach(series => {
        try { btChart.removeSeries(series); } catch (e) {}
    });
    btChartAuxSeries = [];
    btIndicatorOverlaySeries = [];
    btDailyHighLineSeries = null;
    btDailyLowLineSeries = null;
    btDailyHighTriggerLineSeries = null;
    btDailyLowTriggerLineSeries = null;
    btPivotPLineSeries = null;
    btPivotR1LineSeries = null;
    btPivotS1LineSeries = null;
    btEqSeries = null;
    btPriceLineLabels = {};
    btPriceLineLabelMeta = {};
}

function btReplayClearRiskLines() {
    if (!btCandleSeries) return;
    if (btReplayStopPriceLine) {
        try { btCandleSeries.removePriceLine(btReplayStopPriceLine); } catch (e) {}
    }
    if (btReplayTargetPriceLine) {
        try { btCandleSeries.removePriceLine(btReplayTargetPriceLine); } catch (e) {}
    }
    btReplayStopPriceLine = null;
    btReplayTargetPriceLine = null;
}

function btReplayDrawRiskLines() {
    btReplayClearRiskLines();
}

function btReplaySetEquity(visibleDisplayTime) {
    if (!btChart || !Array.isArray(btChartEquityCurveRef) || btChartEquityCurveRef.length < 1) return;
    const chartTimeframe = getBtChartTimeframe();
    const seen = new Set();
    const data = btChartEquityCurveRef.map(point => {
        const dt = _btParseDate(point.time);
        if (!dt) return null;
        return { time: btDisplayTime(Math.floor(dt.getTime() / 1000), chartTimeframe), value: Number(point.equity) };
    }).filter(point => point && Number.isFinite(point.value) && point.time <= visibleDisplayTime)
        .filter(point => { if (seen.has(point.time)) return false; seen.add(point.time); return true; })
        .sort((a, b) => a.time - b.time);
    if (data.length < 2) return;
    btEqSeries = btAddTrackedLineSeries({
        priceScaleId: 'equity', color: 'rgba(139, 92, 246, 0.7)', lineWidth: 2,
        priceLineVisible: false, lastValueVisible: false, crosshairMarkerVisible: false,
    }, 'equity');
    btChart.priceScale('equity').applyOptions({ scaleMargins: { top: 0.8, bottom: 0 } });
    btEqSeries.setData(data);
}

function btTradeDisplayTimes(trade) {
    const chartTimeframe = getBtChartTimeframe();
    if (Number.isFinite(Number(trade?.entry_display_time)) || Number.isFinite(Number(trade?.exit_display_time))) {
        return {
            entry: Number.isFinite(Number(trade?.entry_display_time)) ? Number(trade.entry_display_time) : null,
            exit: Number.isFinite(Number(trade?.exit_display_time)) ? Number(trade.exit_display_time) : null,
        };
    }
    const entryDt = _btParseDate(trade?.entry_time);
    const exitDt = _btParseDate(trade?.exit_time);
    return {
        entry: entryDt ? btDisplayTime(Math.floor(entryDt.getTime() / 1000), chartTimeframe) : null,
        exit: exitDt ? btDisplayTime(Math.floor(exitDt.getTime() / 1000), chartTimeframe) : null,
    };
}

function btReplayStrategySnapshot(visibleDisplayTime, lastClose) {
    const trades = Array.isArray(btChartTradesRef) ? btChartTradesRef : [];
    let realized = 0;
    let activeTrade = null;
    trades.forEach(trade => {
        const times = btTradeDisplayTimes(trade);
        if (times.exit != null && times.exit <= visibleDisplayTime) {
            realized += Number(trade.pl || 0);
        } else if (!activeTrade && times.entry != null && times.exit != null && times.entry <= visibleDisplayTime && visibleDisplayTime < times.exit) {
            activeTrade = trade;
        }
    });
    let unrealized = 0;
    if (activeTrade) {
        const entry = Number(activeTrade.entry_underlying ?? activeTrade.entry_price);
        const last = Number(lastClose);
        const qty = Math.max(1, Number(activeTrade.quantity || activeTrade.qty || activeTrade.contracts || 1));
        if (Number.isFinite(entry) && Number.isFinite(last)) {
            unrealized = (last - entry) * qty;
        }
    }
    return { realized, activeTrade, unrealized };
}

function btReplayUpdateAccount(lastClose, visibleDisplayTime = null) {
    const account = document.getElementById('btReplayAccount');
    if (!account) return;
    const displayTime = Number.isFinite(Number(visibleDisplayTime)) ? Number(visibleDisplayTime) : null;
    if (displayTime == null) {
        account.textContent = btManualChartEnabled ? 'No active manual trade' : 'No active strategy trade';
        account.className = 'ml-auto text-[0.6rem] font-bold text-slate-500';
        return;
    }
    const snapshot = btReplayStrategySnapshot(displayTime, lastClose);
    if (!snapshot.activeTrade) {
        account.textContent = `Realized ${snapshot.realized >= 0 ? '+' : ''}$${snapshot.realized.toFixed(2)} · No active ${btManualChartEnabled && btManualTradeIsComplete() ? 'manual' : 'strategy'} trade`;
        account.className = `ml-auto text-[0.6rem] font-bold ${snapshot.realized >= 0 ? 'text-emerald-600' : 'text-rose-600'}`;
        return;
    }
    const trade = snapshot.activeTrade;
    const isOption = trade.option_type !== undefined;
    const label = trade.manual ? 'MANUAL LONG' : (isOption ? `${String(trade.option_type).toUpperCase()} ${trade.strike || ''}`.trim() : 'LONG');
    const entry = Number(trade.entry_underlying ?? trade.entry_price);
    const entryText = Number.isFinite(entry) ? `$${formatAssetPrice(entry, isCrypto(trade.symbol || window.activeTicker))}` : '$--';
    account.textContent = `${label} active @ ${entryText} · U/R ${snapshot.unrealized >= 0 ? '+' : ''}$${snapshot.unrealized.toFixed(2)} · Realized ${snapshot.realized >= 0 ? '+' : ''}$${snapshot.realized.toFixed(2)}`;
    account.className = `ml-auto text-[0.6rem] font-bold ${snapshot.unrealized >= 0 ? 'text-emerald-600' : 'text-rose-600'}`;
}

function btReplayUpdateControls() {
    const active = Boolean(btReplayActive && btReplayFullCandlesRef.length);
    const total = btReplayFullCandlesRef.length;
    const slider = document.getElementById('btReplaySlider');
    if (slider) {
        slider.max = Math.max(0, total - 1);
        slider.value = Math.max(0, Math.min(btReplayCursor, Math.max(0, total - 1)));
        slider.disabled = !active;
    }
    ['btReplayPlay', 'btReplayBack', 'btReplayForward', 'btReplayReset'].forEach(id => {
        const el = document.getElementById(id);
        if (el) el.disabled = !active;
    });
    const toggle = document.getElementById('btReplayToggle');
    if (toggle) toggle.textContent = active ? 'Exit Replay' : 'Start Replay';
    const play = document.getElementById('btReplayPlay');
    if (play) play.textContent = btReplayPlaying ? 'Pause' : 'Play';
    const status = document.getElementById('btReplayStatus');
    const clock = document.getElementById('btReplayClock');
    if (!active) {
        if (status) status.textContent = 'Replay idle';
        if (clock) clock.textContent = '—';
        return;
    }
    const candle = btReplayFullCandlesRef[btReplayCursor];
    const ts = btReplayCandleTime(candle);
    const dateText = ts ? new Date(ts * 1000).toLocaleString('en-US', { timeZone: 'America/Chicago', month: 'short', day: 'numeric', year: 'numeric', hour: '2-digit', minute: '2-digit', hour12: false }) : '—';
    if (clock) clock.textContent = dateText;
    if (status) status.textContent = `${btReplayPlaying ? 'Playing' : 'Paused'} · ${btReplayCursor + 1}/${total} · ${btReplaySpeed}x`;
}

function btRenderReplayFrame(cursor = btReplayCursor) {
    if (!btReplayActive || !btChart || !btReplayFullCandlesRef.length) return;
    btReplayCursor = Math.max(0, Math.min(btReplayFullCandlesRef.length - 1, Math.round(Number(cursor) || 0)));
    const rawVisible = btReplayVisibleCandles(btReplayCursor);
    const chartTimeframe = getBtChartTimeframe();
    const visible = rawVisible.map(candle => btDisplayCandle(candle, chartTimeframe)).filter(candle => Number.isFinite(Number(candle.time)));
    if (!visible.length) return;
    btCandleSeries.setData(visible);
    btVolumeSeries.setData(visible.map(c => ({ time: c.time, value: Number(c.volume || 0), color: c.close >= c.open ? 'rgba(16, 185, 129, 0.4)' : 'rgba(239, 68, 68, 0.4)' })));

    // Rebuild only tracked overlay series from the visible prefix. The chart
    // instance and candle/volume series are retained, so playback stays smooth.
    btReplayRemoveAuxSeries();
    btDrawPriceIndicatorOverlays(visible);
    const lastDisplayTime = Number(visible[visible.length - 1].time);
    btManualReplayVisibleTime = lastDisplayTime;
    btReplaySetEquity(lastDisplayTime);
    btChartMarkersRef = btReplayMarkersForTime(lastDisplayTime);
    btCandleSeries.setMarkers(btChartMarkersRef);
    btReplayDrawRiskLines();
    try { btChart.timeScale().scrollToPosition(0, false); } catch (e) {}
    btRefreshManualRiskRewardAreas();
    btReplayUpdateAccount(Number(rawVisible[rawVisible.length - 1].close), lastDisplayTime);
    const countEl = document.getElementById('btChartTradeCount');
    if (countEl) countEl.textContent = `${btChartTradesRef.length} ${btManualChartEnabled && btManualTradeIsComplete() ? 'manual' : 'strategy'} trade${btChartTradesRef.length === 1 ? '' : 's'} · replay ${btReplayCursor + 1}/${btReplayFullCandlesRef.length}`;
    btReplayUpdateControls();
}

function btScheduleReplayTick() {
    btReplayStopTimer();
    btReplayPlaying = true;
    btReplayUpdateControls();
    const tick = () => {
        if (!btReplayActive || !btReplayPlaying) return;
        const manualExitCursor = btManualReplayExitCursor();
        if (manualExitCursor != null && btReplayCursor >= manualExitCursor) {
            btReplayStopTimer();
            btReplayUpdateControls();
            return;
        }
        if (btReplayCursor >= btReplayFullCandlesRef.length - 1) {
            btReplayStopTimer();
            btReplayUpdateControls();
            return;
        }
        const nextCursor = btReplayCursor + 1;
        if (manualExitCursor != null && nextCursor >= manualExitCursor) {
            btRenderReplayFrame(manualExitCursor);
            btReplayStopTimer();
            btReplayUpdateControls();
            return;
        }
        btRenderReplayFrame(nextCursor);
        btReplayTimer = setTimeout(tick, Math.max(80, Math.round(1000 / btReplaySpeed)));
    };
    btReplayTimer = setTimeout(tick, Math.max(80, Math.round(1000 / btReplaySpeed)));
}

window.btToggleReplay = function() {
    if (!btChartCandlesRef.length) return;
    if (btReplayActive) {
        btReplayStopTimer();
        btReplayActive = false;
        btReplayFullCandlesRef = [];
        btManualReplayVisibleTime = null;
        btReplayUpdateControls();
        redrawBtChartWithCurrentSettings();
        return;
    }
    btReplayActive = true;
    btReplayPlaying = false;
    btReplayCursor = 0;
    btManualReplayVisibleTime = null;
    btReplayFullCandlesRef = [...btChartCandlesRef];
    btReplayUpdateControls();
    btRenderReplayFrame(0);
};

window.btToggleReplayPlayback = function() {
    if (!btReplayActive) return;
    if (btReplayPlaying) btReplayStopTimer();
    else btScheduleReplayTick();
    btReplayUpdateControls();
};

window.btSetReplaySpeed = function(speed) {
    const parsed = Number(speed);
    btReplaySpeed = Number.isFinite(parsed) ? Math.max(1, Math.min(10, parsed)) : 1;
    try { sessionStorage.setItem('bt_replay_speed', String(btReplaySpeed)); } catch (e) {}
    if (btReplayPlaying) btScheduleReplayTick();
    btReplayUpdateControls();
};

window.btSetReplayResolution = function(resolution) {
    // Intrabar mode remains unavailable until a lower-resolution historical
    // feed is supplied by the backtest response; never synthesize ticks from
    // OHLC because that would create invalid fills and false precision.
    try { sessionStorage.setItem('bt_replay_resolution', String(resolution || 'bar')); } catch (e) {}
};

window.btStepReplay = function(delta) {
    if (!btReplayActive) return;
    btReplayStopTimer();
    btRenderReplayFrame(btReplayCursor + Number(delta || 0));
};

window.btSeekReplay = function(cursor) {
    if (!btReplayActive) return;
    btReplayStopTimer();
    btRenderReplayFrame(cursor);
};

window.btResetReplay = function() {
    if (!btReplayActive) return;
    btReplayStopTimer();
    btReplayCursor = 0;
    btManualReplayVisibleTime = null;
    btReplayClearRiskLines();
    btRenderReplayFrame(0);
};

function btResetReplayState(silent = false) {
    try {
        const savedSpeed = Number(sessionStorage.getItem('bt_replay_speed'));
        if (Number.isFinite(savedSpeed)) btReplaySpeed = Math.max(1, Math.min(10, savedSpeed));
        const speedEl = document.getElementById('btReplaySpeed');
        if (speedEl) speedEl.value = String(btReplaySpeed);
        const resolutionEl = document.getElementById('btReplayResolution');
        if (resolutionEl) resolutionEl.value = sessionStorage.getItem('bt_replay_resolution') || 'bar';
    } catch (e) {}
    btReplayStopTimer();
    btReplayActive = false;
    btReplayCursor = 0;
    btManualReplayVisibleTime = null;
    btReplayClearRiskLines();
    btReplayFullCandlesRef = [];
    btReplayBaseMarkersRef = [];
    btReplayStopPriceLine = null;
    btReplayTargetPriceLine = null;
    if (!silent) btReplayUpdateControls();
}

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
    return btChartTimeframeRef
        || document.getElementById(`strategyTimeframe_${mode}`)?.value
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
    return Number.isFinite(sourceTime) ? sourceTime : time;
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

function btNewYorkMinutesFromUnix(unixSeconds) {
    const sourceTime = Number(unixSeconds);
    if (!Number.isFinite(sourceTime)) return null;
    const parts = new Intl.DateTimeFormat('en-US', {
        timeZone: 'America/New_York',
        hour12: false,
        hour: '2-digit',
        minute: '2-digit',
    }).formatToParts(new Date(sourceTime * 1000));
    const hour = Number(parts.find(part => part.type === 'hour')?.value);
    const minute = Number(parts.find(part => part.type === 'minute')?.value);
    if (!Number.isFinite(hour) || !Number.isFinite(minute)) return null;
    return (hour % 24) * 60 + minute;
}

function btChartSessionMode() {
    if (currentBtMode === 'options' || window.lastBacktestRun?.is_options) return 'regular';
    const ticker = window.lastBacktestRun?.ticker || document.getElementById('btTicker')?.value || '';
    if (isCrypto(ticker)) return '24h';
    return String(window.lastBacktestRun?.market_session || document.getElementById('strategyMarketSession_regular')?.value || 'Regular').trim().toLowerCase();
}

function btTimestampMatchesChartSession(unixSeconds) {
    const session = btChartSessionMode();
    if (session === '24h' || session === 'crypto24' || session === 'all') return true;
    if (getBtChartTimeframeSeconds() >= 86400) return true;
    const minutes = btNewYorkMinutesFromUnix(unixSeconds);
    if (minutes === null) return true;
    const isRegular = minutes >= 570 && minutes < 960; // 09:30 <= t < 16:00 ET
    if (session === 'extended') return true;
    return isRegular;
}

function btFilterCandlesForChartSession(candles) {
    return (candles || []).filter(candle => btTimestampMatchesChartSession(candle?.source_time ?? candle?.time));
}

function btFilterTradesForChartSession(trades) {
    return (trades || []).filter(trade => {
        const entryDt = _btParseDate(trade?.entry_time);
        const exitDt = _btParseDate(trade?.exit_time);
        const entryTs = entryDt ? Math.floor(entryDt.getTime() / 1000) : null;
        const exitTs = exitDt ? Math.floor(exitDt.getTime() / 1000) : null;
        return (entryTs != null && btTimestampMatchesChartSession(entryTs))
            || (exitTs != null && btTimestampMatchesChartSession(exitTs));
    });
}

function btFilterEquityForChartSession(equityCurve) {
    return (equityCurve || []).filter(point => {
        const dt = _btParseDate(point?.time);
        return !dt || btTimestampMatchesChartSession(Math.floor(dt.getTime() / 1000));
    });
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

function btLineDataFromCandlesWhere(candles, key, predicate) {
    const seen = new Set();
    return (candles || [])
        .filter(c => c && c.time && predicate(c) && c[key] !== null && c[key] !== undefined && !Number.isNaN(Number(c[key])))
        .map(c => ({ time: Number(c.time), value: Number(c[key]) }))
        .filter(point => {
            if (seen.has(point.time)) return false;
            seen.add(point.time);
            return true;
        })
        .sort((a, b) => a.time - b.time);
}

function btLineDataFromCandlesWhereWithGaps(candles, key, predicate) {
    const seen = new Set();
    return (candles || [])
        .filter(c => c && c.time)
        .map(c => {
            const time = Number(c.time);
            if (!Number.isFinite(time) || seen.has(time)) return null;
            seen.add(time);
            const value = Number(c[key]);
            if (predicate(c) && c[key] !== null && c[key] !== undefined && !Number.isNaN(value)) {
                return { time, value };
            }
            return { time };
        })
        .filter(Boolean)
        .sort((a, b) => a.time - b.time);
}

function btIsSupertrendBullish(candle) {
    const value = candle?.supertrend_up;
    if (typeof value === 'boolean') return value;
    if (typeof value === 'number') return value === 1;
    if (typeof value === 'string') {
        const normalized = value.trim().toLowerCase();
        if (['true', '1', 'bullish', 'bull', 'up', 'green'].includes(normalized)) return true;
        if (['false', '0', 'bearish', 'bear', 'down', 'red'].includes(normalized)) return false;
    }
    return false;
}

function btSupertrendLineData(candles) {
    const seen = new Set();
    return (candles || [])
        .filter(c => c && c.time && c.supertrend !== null && c.supertrend !== undefined && !Number.isNaN(Number(c.supertrend)))
        .sort((a, b) => Number(a.time) - Number(b.time))
        .map(candle => ({
            time: Number(candle.time),
            value: Number(candle.supertrend),
            color: btIsSupertrendBullish(candle) ? '#10b981' : '#ef4444',
        }))
        .filter(point => {
            if (!Number.isFinite(point.time) || seen.has(point.time)) return false;
            seen.add(point.time);
            return true;
        });
}

function btAddIndicatorDataLine(data, options) {
    if (!btChart || !Array.isArray(data) || data.filter(point => point && point.value !== undefined).length <= 1) return null;
    const series = btAddTrackedLineSeries({
        priceLineVisible: false,
        lastValueVisible: false,
        crosshairMarkerVisible: false,
        ...options,
    });
    series.setData(data);
    return series;
}

function btSelectedIndicatorNames() {
    const mode = currentBtMode === 'options' ? 'options' : 'regular';
    return new Set(getBtCheckedIndicatorValues(mode));
}

function btAddIndicatorLine(candles, key, options) {
    const data = btLineDataFromCandles(candles, key);
    if (data.length <= 1 || !btChart) return null;
    const series = btAddTrackedLineSeries({
        priceLineVisible: false,
        lastValueVisible: false,
        crosshairMarkerVisible: false,
        ...options,
    });
    series.setData(data);
    return series;
}

function btAddTrackedLineSeries(options, layer = 'indicator') {
    if (!btChart) return null;
    const series = btChart.addLineSeries(options || {});
    btChartAuxSeries.push(series);
    if (layer === 'indicator') {
        btIndicatorOverlaySeries.push(series);
        const indicatorBox = document.getElementById('btToggleIndicators');
        if (indicatorBox && !indicatorBox.checked) {
            try { series.applyOptions({ visible: false }); } catch (e) {}
        }
    }
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
        const avwapData = btLineDataFromCandles(chartCandles, 'vwap');
        if (avwapData.length > 1 && btChart) {
            const avwapSeries = btAddTrackedLineSeries({
                color: '#000000',
                lineWidth: 2,
                priceLineVisible: false,
                lastValueVisible: false,
                crosshairMarkerVisible: false,
            });
            avwapSeries.setData(avwapData);
        }
    }
    if (enabled.has('Supertrend')) {
        btAddIndicatorDataLine(btSupertrendLineData(chartCandles), {
            lineWidth: 2,
            lineType: 0, // Dashboard-style straight candle-to-candle Supertrend line.
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
    if (enabled.has('BotBulls5')) {
        const ticker = window.lastBacktestRun?.ticker || document.getElementById('btTicker')?.value || '';
        const patternOptions = { rolling24Hours: isCrypto(ticker) };
        const definition = (window.STANDARD_EXTENSION_OVERLAYS || [])
            .find(item => item.name === 'BotBulls5');
        (definition?.series || []).forEach(spec => {
            const data = window.StandardIndicatorSubcharts?.buildOverlayData?.(
                chartCandles,
                spec,
                patternOptions,
            ) || [];
            btAddIndicatorDataLine(data, {
                color: spec.color,
                lineWidth: spec.lineWidth || 1,
                lineStyle: spec.lineStyle || 0,
            });
        });
        const drawingLayers = window.StandardIndicatorSubcharts?.buildBotBulls5PatternDrawing?.(
            chartCandles,
            patternOptions,
        ) || [];
        drawingLayers.forEach(layer => {
            btAddIndicatorDataLine(layer.data, {
                color: layer.color,
                lineWidth: layer.lineWidth || 2,
                lineStyle: layer.lineStyle || 0,
            });
        });
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
    btReplayStopTimer();
    if (btChart && btManualChartClickHandler) {
        try { btChart.unsubscribeClick(btManualChartClickHandler); } catch (e) {}
    }
    btManualChartClickHandler = null;
    btRemoveManualTradePriceLines();
    btClearManualRiskRewardAreas();
    btManualReplayVisibleTime = null;
    if (btChart) {
        try { btChart.remove(); } catch (e) {}
    }
    btChart = null;
    btCandleSeries = null;
    btReplayStopPriceLine = null;
    btReplayTargetPriceLine = null;
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
    btChartAuxSeries = [];
    btPriceLineLabels = {};
    btPriceLineLabelMeta = {};
    btChartMarkersRef = [];
    btTradeMarkersRef = [];
    btIndicatorMarkersRef = [];
    btStrategyChartTradesRef = [];
    btStrategyTradeMarkersRef = [];
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
    const replayWasActive = btReplayActive;
    const replayCursor = btReplayCursor;
    const savedStrategyTrades = [...((btStrategyChartTradesRef && btStrategyChartTradesRef.length) ? btStrategyChartTradesRef : btChartTradesRef)];
    const savedManualEnabled = btManualChartEnabled;
    const savedManualPoints = { ...btManualTradePoints };
    if (replayWasActive) btReplayStopTimer();
    applyBtPriorDayBufferToCachedCandles();
    destroyBtChartOnly();
    btManualChartEnabled = savedManualEnabled;
    btManualTradePoints = savedManualPoints;
    btStrategyChartTradesRef = savedManualEnabled ? [] : savedStrategyTrades;
    btChartTradesRef = savedManualEnabled ? [] : [...savedStrategyTrades];
    document.getElementById('btChartSection').classList.remove('hidden');
    _btInitChart(btChartCandlesRef, savedManualEnabled ? [] : savedStrategyTrades, btChartEquityCurveRef || []);
    if (replayWasActive) {
        btReplayActive = true;
        btReplayFullCandlesRef = [...btChartCandlesRef];
        btRenderReplayFrame(replayCursor);
    }
}

function _btInitChart(candles, trades, equityCurve) {
    const wrap = document.getElementById('btChartWrap');
    if (!wrap) return;
    btChartAuxSeries = [];
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
        btChart.timeScale().subscribeVisibleTimeRangeChange(() => {
            refreshBtPriceLineLabels();
            btRefreshManualRiskRewardAreas();
        });
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
            btDailyHighLineSeries = btAddTrackedLineSeries({
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
            btDailyLowLineSeries = btAddTrackedLineSeries({
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
            btDailyHighTriggerLineSeries = btAddTrackedLineSeries({
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
            btDailyLowTriggerLineSeries = btAddTrackedLineSeries({
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
            btPivotPLineSeries = btAddTrackedLineSeries({
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
                const rSeries = btAddTrackedLineSeries({
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
                const sSeries = btAddTrackedLineSeries({
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
            const pocSeries = btAddTrackedLineSeries({
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
            const vahSeries = btAddTrackedLineSeries({
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
            const valSeries = btAddTrackedLineSeries({
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
                const sSeries = btAddTrackedLineSeries({
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
                const rSeries = btAddTrackedLineSeries({
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
            btEqSeries = btAddTrackedLineSeries({
                priceScaleId: 'equity',
                color: 'rgba(139, 92, 246, 0.7)',
                lineWidth: 2,
                priceLineVisible: false,
                lastValueVisible: false,
                crosshairMarkerVisible: false,
            }, 'equity');
            btChart.priceScale('equity').applyOptions({
                scaleMargins: { top: 0.8, bottom: 0 },
            });
            btEqSeries.setData(uniqueEquity);
        }
    }

    // ── Build buy/sell markers (clean P/L % style) ───────────────────────
    const tradeMarkers = [];
    const indicatorMarkers = [];
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
        tradeMarkers.push({
            time: entryTs,
            position: 'belowBar',
            color: '#10b981',
            shape: 'arrowUp',
            text: isOption ? `${trade.option_type} ${trade.strike}` : 'B',
            size: 2,
        });

        // Sell marker — shows P/L %
        tradeMarkers.push({
            time: exitTs,
            position: 'aboveBar',
            color: isWin ? '#10b981' : '#ef4444',
            shape: 'arrowDown',
            text: plLabel,
            size: 2,
        });
    });

    const enabledChartIndicators = btSelectedIndicatorNames();
    const backtestTicker = window.lastBacktestRun?.ticker || document.getElementById('btTicker')?.value || '';
    const botBulls5VisibleTimes = enabledChartIndicators.has('BotBulls5')
        ? (window.StandardIndicatorSubcharts?.botBulls5RecentEventTimes
            ? window.StandardIndicatorSubcharts.botBulls5RecentEventTimes(chartCandles, {
                rolling24Hours: isCrypto(backtestTicker),
            })
            : new Set(chartCandles.map(bar => Number(bar?.time)).filter(Number.isFinite)))
        : new Set();
    for (let i = 1; i < chartCandles.length; i += 1) {
        const prev = chartCandles[i - 1];
        const curr = chartCandles[i];
        if (enabledChartIndicators.has('Supertrend') && curr.supertrend != null && prev.supertrend != null) {
            const currStBullish = btIsSupertrendBullish(curr);
            const prevStBullish = btIsSupertrendBullish(prev);
            if (currStBullish && !prevStBullish) {
                indicatorMarkers.push({ time: Number(curr.time), position: 'belowBar', color: '#10b981', shape: 'arrowUp', text: 'ST Buy', size: 1 });
            } else if (!currStBullish && prevStBullish) {
                indicatorMarkers.push({ time: Number(curr.time), position: 'aboveBar', color: '#ef4444', shape: 'arrowDown', text: 'ST Sell', size: 1 });
            }
        }
        if (enabledChartIndicators.has('BotBulls1')) {
            if (curr.ba1_buy) indicatorMarkers.push({ time: Number(curr.time), position: 'belowBar', color: '#06b6d4', shape: 'arrowUp', text: 'BB1 BUY', size: 1 });
            if (curr.ba1_sell) indicatorMarkers.push({ time: Number(curr.time), position: 'aboveBar', color: '#f43f5e', shape: 'arrowDown', text: 'BB1 SELL', size: 1 });
        }
        if (enabledChartIndicators.has('BotBulls2')) {
            if (curr.ba2_buy) indicatorMarkers.push({ time: Number(curr.time), position: 'belowBar', color: '#10b981', shape: 'arrowUp', text: 'BB2 BUY', size: 1 });
            if (curr.ba2_sell) indicatorMarkers.push({ time: Number(curr.time), position: 'aboveBar', color: '#ef4444', shape: 'arrowDown', text: 'BB2 SELL', size: 1 });
        }
        if (enabledChartIndicators.has('BotBulls3')) {
            if (curr.ba3_buy) indicatorMarkers.push({ time: Number(curr.time), position: 'belowBar', color: '#0ea5e9', shape: 'arrowUp', text: 'BB3 BUY', size: 1.2 });
            if (curr.ba3_sell) indicatorMarkers.push({ time: Number(curr.time), position: 'aboveBar', color: '#f43f5e', shape: 'arrowDown', text: 'BB3 SELL', size: 1.2 });
        }
        if (enabledChartIndicators.has('BotBulls5') && botBulls5VisibleTimes.has(Number(curr.time))) {
            if (curr.bb5_breakout_bull) {
                indicatorMarkers.push({ time: Number(curr.time), position: 'belowBar', color: '#10b981', shape: 'arrowUp', text: `BB5 ${String(curr.bb5_pattern || 'Bull').replaceAll('_', ' ')}`, size: 1.2 });
            } else if (curr.bb5_breakout_bear) {
                indicatorMarkers.push({ time: Number(curr.time), position: 'aboveBar', color: '#f43f5e', shape: 'arrowDown', text: `BB5 ${String(curr.bb5_pattern || 'Bear').replaceAll('_', ' ')}`, size: 1.2 });
            }
        }
    }

    // Candle Patterns Markers on backtest chart (last candle only, no noise across full chart)
    const c = chartCandles[chartCandles.length - 1];
    if (c?.cdl_name && c.cdl_name !== 'Neutral') {
        if (c.cdl_bullish) {
            indicatorMarkers.push({
                time: Number(c.time),
                position: 'belowBar',
                color: '#10b981',
                shape: 'arrowUp',
                text: c.cdl_name,
                size: 1.1
            });
        } else if (c.cdl_bearish) {
            indicatorMarkers.push({
                time: Number(c.time),
                position: 'aboveBar',
                color: '#ef4444',
                shape: 'arrowDown',
                text: c.cdl_name,
                size: 1.1
            });
        }
    }

    btTradeMarkersRef = [...tradeMarkers].sort((a, b) => Number(a.time) - Number(b.time));
    btStrategyTradeMarkersRef = [...btTradeMarkersRef];
    btIndicatorMarkersRef = [...indicatorMarkers].sort((a, b) => Number(a.time) - Number(b.time));
    btChartMarkersRef = btMergeMarkersForToggles();
    btCandleSeries.setMarkers(btChartMarkersRef);
    btReplayBaseMarkersRef = btMergeMarkersForToggles(btTradeMarkersRef, btIndicatorMarkersRef);
    btDrawManualTradePriceLines();
    btAttachManualChartClickHandler();
    if (btManualChartEnabled) btApplyManualTradeLayer();
    
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
        const activePos = (btChartTradesRef || []).find(t => {
            const times = btTradeDisplayTimes(t);
            const eTs = Number.isFinite(Number(times.entry)) ? Number(times.entry) : 0;
            const xTs = Number.isFinite(Number(times.exit)) ? Number(times.exit) : 0;
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
        btRefreshManualRiskRewardAreas();
    });
    btChartResizeObserver.observe(wrap);

    // ── Keyboard navigation (arrow keys when modal is open) ───────────────
    window._btKeyHandler = (e) => {
        if (!btChart) return;
        const tag = String(e.target?.tagName || '').toLowerCase();
        if (['input', 'select', 'textarea', 'button'].includes(tag)) return;
        if (btReplayActive) {
            if (e.key === ' ') { e.preventDefault(); btToggleReplayPlayback(); return; }
            if (e.key === 'ArrowLeft') { e.preventDefault(); btStepReplay(-1); return; }
            if (e.key === 'ArrowRight') { e.preventDefault(); btStepReplay(1); return; }
            if (e.key.toLowerCase() === 'r') { e.preventDefault(); btResetReplay(); return; }
            return;
        }
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
    const indBox = document.getElementById('btToggleIndicators');
    const markBox = document.getElementById('btToggleMarkers');

    const showVol = volBox ? volBox.checked : true;
    const showEq = eqBox ? eqBox.checked : true;
    const showIndicators = indBox ? indBox.checked : true;

    if (btVolumeSeries) {
        btVolumeSeries.applyOptions({ visible: showVol });
    }
    if (btEqSeries) {
        btEqSeries.applyOptions({ visible: showEq });
    }
    (btIndicatorOverlaySeries || []).forEach(series => {
        try { series.applyOptions({ visible: showIndicators }); } catch (e) {}
    });
    if (btCandleSeries) {
        btChartMarkersRef = btMergeMarkersForToggles();
        btCandleSeries.setMarkers(btChartMarkersRef);
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

    const displayTimes = btTradeDisplayTimes(trade);
    const entryTs = Number.isFinite(Number(displayTimes.entry)) ? Number(displayTimes.entry) : 0;
    const chartTimeframe = getBtChartTimeframe();
    let entryIdx = btChartCandlesRef.findIndex(c => btDisplayTime(Number(c.time), chartTimeframe) >= entryTs);
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
    if (btManualChartEnabled && btManualTradeIsComplete()) {
        lbl.textContent = 'Manual trade';
        return;
    }
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
    
    const checkedCount = getBtCheckedIndicatorValues(mode).length;

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
    "VWAP": "Anchored Volume Weighted Average Price (AVWAP) tracks the volume-weighted average from the most recent major volume spike. Purpose: Shows institutional-style anchored cost basis and acceptance/rejection.",
    "Strategy Confidence": "Strategy Confidence combines multiple indicator signals into a single probability score. Purpose: Measures the overall conviction of a bullish or bearish trend.",
    "BotBulls1": "Confirmed WaveTrend reversal with MFI reclaim/rejection, then price confirmation within the BB1 window. Confluence stays bullish after the last BB1 BUY marker and bearish after the last BB1 SELL marker.",
    "BotBulls2": "Adaptive ATR trailing stop with trend-smoothed EMA and reversal zone detection. Fires on trend flips confirmed by multi-condition confluence scoring (1-4 strength rating).",
    "BotBulls3": "Heikin-Ashi noise filter + ATR-based trailing stop. Generates buy/sell alerts only on confirmed momentum flips — removing false signals from choppy markets.",
    "BotBulls5": "Premium causal chart-pattern engine for continuation, reversal, compression, channel, and rounded structures. Draws qualified geometry and votes only after a completed-candle breakout.",
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
    'BotBulls1': ['BOTBULLS1_WT_CHANNEL', 'BOTBULLS1_WT_AVERAGE', 'BOTBULLS1_WT_OVERSOLD', 'BOTBULLS1_WT_OVERBOUGHT', 'BOTBULLS1_MFI_BUY_RECLAIM', 'BOTBULLS1_MFI_SELL_REJECT', 'BOTBULLS1_CONFIRM_LOOKBACK', 'BOTBULLS1_REQUIRE_MFI_CONFIRM', 'BOTBULLS1_REQUIRE_CANDLE_CONFIRM', 'BOTBULLS1_REQUIRE_PRICE_CONFIRM', 'BOTBULLS1_PRICE_CONFIRM_EMA', 'BOTBULLS1_PRICE_CONFIRM_LOOKBACK'],
    'BotBulls2': ['BOTBULLS2_ATR_MULT', 'BOTBULLS2_TREND_TRACER_PERIOD', 'BOTBULLS2_REVERSAL_ZONE_PERIOD'],
    'BotBulls3': ['BOTBULLS3_ATR_PERIOD', 'BOTBULLS3_ATR_MULT'],
    'BotBulls5': ['BOTBULLS5_MIN_CONFIDENCE', 'BOTBULLS5_BREAKOUT_BUFFER_ATR', 'BOTBULLS5_STRUCTURE_LOOKBACK', 'BOTBULLS5_SIGNAL_HOLD_BARS', 'BOTBULLS5_REQUIRE_VOLUME']
};
Object.assign(window.BT_INDICATOR_CONFIG_MAP, window.STANDARD_INDICATOR_CONFIG_MAP || {});

// Industry Defaults for indicators based on config.py
window.BT_INDICATOR_DEFAULTS = window.BT_INDICATOR_DEFAULTS || {
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
    'BOTBULLS5_BREAKOUT_BUFFER_ATR': 0.10,
    'BOTBULLS5_MIN_CONFIDENCE': 70.0,
    'BOTBULLS5_SIGNAL_HOLD_BARS': 3,
    'BOTBULLS5_REQUIRE_VOLUME': 0,
    'BOTBULLS5_STRUCTURE_LOOKBACK': 48,
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
Object.assign(window.BT_INDICATOR_DEFAULTS, window.STANDARD_INDICATOR_DEFAULTS || {});

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
    if (indicatorName === 'BotBulls5' && typeof window.renderBotBulls5PatternSummary === 'function') {
        window.renderBotBulls5PatternSummary(
            container,
            manualBtVisibleHistory?.length ? manualBtVisibleHistory : manualBtHistory,
            { rolling24Hours: isCrypto(manualBtTicker || '') },
        );
    }

    const currentParams = window.lastBacktestData?.indicator_parameters || {};
    const overrides = window.lastBacktestData?.indicator_overrides || {};
    const paramLabels = {
        ...(window.STANDARD_INDICATOR_PARAM_LABELS || {}),
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
        BOTBULLS5_BREAKOUT_BUFFER_ATR: 'Breakout Close Buffer (ATR)',
        BOTBULLS5_MIN_CONFIDENCE: 'Minimum Setup Quality (%)',
        BOTBULLS5_SIGNAL_HOLD_BARS: 'Confluence Vote Duration (Bars)',
        BOTBULLS5_REQUIRE_VOLUME: 'Require Breakout Volume (1=On, 0=Off)',
        BOTBULLS5_STRUCTURE_LOOKBACK: 'Structural Pattern Lookback Bars',
    };
    const botbulls5InputAttrs = {
        BOTBULLS5_BREAKOUT_BUFFER_ATR: 'min="0" max="1" step="0.05"',
        BOTBULLS5_MIN_CONFIDENCE: 'min="40" max="95" step="1"',
        BOTBULLS5_SIGNAL_HOLD_BARS: 'min="1" max="20" step="1"',
        BOTBULLS5_REQUIRE_VOLUME: 'min="0" max="1" step="1"',
        BOTBULLS5_STRUCTURE_LOOKBACK: 'min="20" max="120" step="1"',
    };

    configKeys.forEach(key => {
        const hasOverride = overrides[key] !== undefined && overrides[key] !== null && overrides[key] !== '';
        const val = currentParams[key] !== undefined ? currentParams[key] : (window.BT_INDICATOR_DEFAULTS[key] !== undefined ? window.BT_INDICATOR_DEFAULTS[key] : '');
        const label = paramLabels[key] || key.replace(/_/g, ' ').toLowerCase();
        const isPercent = key.endsWith('_PCT') || key.endsWith('_PERCENTAGE');
        const inputAttrs = botbulls5InputAttrs[key] || (key === 'PIVOT_LEVEL_COUNT'
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
                    : (window.STANDARD_INDICATOR_INPUT_ATTRS?.[key] || 'step="any"'));

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
            const manualChartSettingsOpen = Boolean(
                manualBtTicker
                && !document.getElementById('manualBtChartPreviewModal')?.classList.contains('hidden')
            );
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
            if (manualChartSettingsOpen) {
                console.log('[settings] Reloading the Manual Backtest chart with its updated indicator parameters.');
                await runBacktest({ chartOnly: true, force: true });
                return;
            }
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
            putInput.value = sellInput.value || '1';
        }
        putInput.min = sellInput.min;
        putInput.max = sellInput.max;
    }
    if (putLabel) {
        const indicatorModes = ['indicator', 'hybrid', 'hybrid_atr', 'hybrid_pct', 'hybrid_pivot', 'hybrid_high_low', 'hybrid_swing'];
        const sellMode = document.getElementById('strategySellMode_options')?.value || 'hybrid';
        const needsInd = indicatorModes.includes(sellMode);
        const val = parseInt((putInput?.value || sellInput?.value || '1'), 10) || 0;
        const count = putInput?.max || sellInput?.max || 0;
        putLabel.textContent = (!needsInd && val === 0) ? `0 of ${count} signals (Not Required)` : `${val} of ${count} signals`;
    }
}

window.syncBtOptionsPutEntryThreshold = function() {
    const putInput = document.getElementById('strategyPutEntryThreshold_options');
    const sellInput = document.getElementById('strategySellThreshold_options');
    if (putInput && sellInput) {
        sellInput.value = putInput.value || '1';
    }
    updateSignalLabels('options');
    syncBtOptionsPutEntryThresholdLabel();
};

window.adjustBtOptionsPutEntryThreshold = function(delta) {
    const putInput = document.getElementById('strategyPutEntryThreshold_options');
    if (!putInput) return;
    const min = parseInt(putInput.min || '1', 10);
    const max = parseInt(putInput.max || '10', 10);
    const next = Math.max(min, Math.min(max, (parseInt(putInput.value || String(min), 10) || min) + delta));
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

    let val = (parseInt(input.value, 10) || 1) + delta;
    val = Math.max(1, Math.min(val, checkedCount));
    input.value = val;

    // Update labels
    updateSignalLabels(mode);
    if (mode === 'options') syncBtOptionsPutEntryThresholdLabel();
};
window.toggleModeActive = function(mode) {
    if (mode === 'options') return window.toggleBtMode('options');
    return window.toggleBtMode('regular');
};
