
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
// ═══════════════════════════════════════════════════
// AI Trading Bot — Active Bots Page JS
// Dedicated management console for active trading bots
// ═══════════════════════════════════════════════════

// Production API Configuration (Auto-switch between Local and Cloud)
const CLOUD_URL = 'https://207-211-184-22.sslip.io';
const API_BASE = window.location.hostname === 'localhost' || window.location.hostname === '127.0.0.1'
    ? 'http://localhost:8000'
    : CLOUD_URL;
const OPTIONS_REFRESH_INTERVAL = 10000;
const REGULAR_REFRESH_INTERVAL = 60000;
const SCAN_ACTIVITY_REFRESH_INTERVAL = 10000;
const ORDER_ACTIVITY_REFRESH_INTERVAL = 30000;
const DASHBOARD_FETCH_TIMEOUT_MS = 15000;
const PORTFOLIO_FETCH_TIMEOUT_MS = 15000;
const PORTFOLIO_SHORT_REFRESH_INTERVAL = 30000;
const PORTFOLIO_LONG_REFRESH_INTERVAL = 5 * 60 * 1000;
const BOT_CHART_CACHE_VERSION = 'v8';
// Preview calculations can include a fresh provider fetch plus indicator
// warm-up. Leave a small cushion beyond the backend's 45s calculation limit.
const BOT_CHART_PREVIEW_FETCH_TIMEOUT_MS = 50000;
const BOT_CHART_IDB_NAME = 'aiTradeBotChartCache';
const BOT_CHART_IDB_VERSION = 1;
const BOT_CHART_IDB_STORE = 'chartPayloads';

let selectedTicker = null;
let currentBackendTf = "5Min";
let isAlpacaLinked = false;
let currentLogTab = "all";
let currentChartPeriod = "ALL";
let portfolioChartInstance = null;
let botArrangeMode = false;
let portfolioHistoryFetchInFlight = false;
let portfolioHistoryAbortController = null;
let portfolioHistoryFetchId = 0;
const portfolioRefreshAtByRange = new Map();
let botsRegularRefreshTimerId = null;
let botsOptionsRefreshTimerId = null;
let botsActivityRefreshTimerId = null;
let botsPortfolioRefreshTimerId = null;
let botsCountdownTimerId = null;
let botsSoftRefreshTimerId = null;
let botChartPreviewIndexedDbPromise = null;
const botChartPreviewPayloadCache = new Map();
const botChartPreviewRequests = new Map();
const botFullSettingsCache = new Map();
const botsPartitionSnapshots = { options: null, regular: null };
const botsFetchState = {
    all: { controller: null, promise: null, fetchId: 0 },
    options: { controller: null, promise: null, fetchId: 0 },
    regular: { controller: null, promise: null, fetchId: 0 }
};
const botsActivityState = {
    scans: { controller: null, promise: null, fetchedAt: 0, fetchId: 0 },
    orders: { controller: null, promise: null, fetchedAt: 0, fetchId: 0 }
};
let botsSharedSnapshot = null;
let botsMasterTradelist = [];
let lastObservedTradeEventSeq = null;
let crossPartitionRefreshTimerId = null;

function shouldRunBotsCardRefresh() {
    return !document.hidden && !!document.getElementById('tradelistContainer');
}

function shouldRunBotsVisualRefresh() {
    return !document.hidden && shouldRunBotsCardRefresh();
}

async function fetchWithTimeout(url, options = {}, timeoutMs = 10000) {
    const { controller: suppliedController, signal: externalSignal, ...fetchOptions } = options;
    const controller = suppliedController || new AbortController();
    let timedOut = false;
    const forwardExternalAbort = () => {
        if (!controller.signal.aborted) controller.abort(externalSignal?.reason);
    };
    if (externalSignal?.aborted) {
        forwardExternalAbort();
    } else if (externalSignal) {
        externalSignal.addEventListener('abort', forwardExternalAbort, { once: true });
    }
    const timeoutId = setTimeout(() => {
        timedOut = true;
        if (!controller.signal.aborted) controller.abort();
    }, timeoutMs);
    try {
        return await fetch(url, { ...fetchOptions, signal: controller.signal });
    } catch (error) {
        if (timedOut && error?.name === 'AbortError') {
            const timeoutError = new Error(`Request timed out after ${timeoutMs}ms`);
            timeoutError.name = 'TimeoutError';
            throw timeoutError;
        }
        throw error;
    } finally {
        clearTimeout(timeoutId);
        externalSignal?.removeEventListener('abort', forwardExternalAbort);
    }
}

// ──────────────────────────────────────────────
// Auth Headers
// ──────────────────────────────────────────────
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

// ──────────────────────────────────────────────
// Time Formatting
// ──────────────────────────────────────────────
function formatLocalTime(isoString) {
    if (!isoString || isoString === "Starting...") return isoString;
    try {
        let timestamp = isoString;
        if (isoString && !isoString.includes('Z') && !/[+-]\d{2}:\d{2}$/.test(isoString)) {
            timestamp += 'Z';
        }
        const date = new Date(timestamp);
        const dateStr = date.toLocaleDateString('en-US', {
            timeZone: 'America/Chicago',
            month: '2-digit', day: '2-digit', year: '2-digit'
        });
        const timeStr = date.toLocaleTimeString('en-US', {
            timeZone: 'America/Chicago',
            hour: '2-digit', minute: '2-digit', second: '2-digit', hour12: false
        });
        return `${dateStr} ${timeStr}`;
    } catch {
        return isoString;
    }
}

function formatUptime(isoString) {
    if (!isoString) return '';
    let timestamp = isoString;
    if (isoString && !isoString.includes('Z') && !/[+-]\d{2}:\d{2}$/.test(isoString)) {
        timestamp += 'Z';
    }
    const diffMs = new Date() - new Date(timestamp);
    if (isNaN(diffMs) || diffMs < 0) return 'Running for < 1m';
    const diffMins = Math.floor(diffMs / 60000);
    if (diffMins < 1) {
        return 'Running for < 1m';
    }
    if (diffMins < 60) {
        return `Running for ${diffMins}m`;
    }
    const diffHours = Math.floor(diffMins / 60);
    const remainingMins = diffMins % 60;
    if (diffHours < 24) {
        return `Running for ${diffHours}h ${remainingMins}m`;
    }
    const diffDays = Math.floor(diffHours / 24);
    const remainingHours = diffHours % 24;
    return `Running for ${diffDays}d ${remainingHours}h`;
}

function formatRelativeTime(isoString) {
    if (!isoString) return 'No trades yet';
    let timestamp = isoString;
    if (isoString && !isoString.includes('Z') && !/[+-]\d{2}:\d{2}$/.test(isoString)) {
        timestamp += 'Z';
    }
    const diffMs = new Date() - new Date(timestamp);
    if (isNaN(diffMs) || diffMs < 0) return 'Just now';
    const diffMins = Math.floor(diffMs / 60000);
    if (diffMins < 1) return 'Just now';
    if (diffMins < 60) return `${diffMins}m ago`;
    const diffHours = Math.floor(diffMins / 60);
    if (diffHours < 24) return `${diffHours}h ago`;
    const diffDays = Math.floor(diffHours / 24);
    return `${diffDays}d ago`;
}

function formatDurationCompactJs(seconds) {
    const rem = Math.max(0, Math.floor(seconds));
    const h = Math.floor(rem / 3600);
    const m = Math.floor((rem % 3600) / 60);
    const s = rem % 60;
    if (h > 0) return `${h}h ${m}m ${s}s`;
    if (m > 0) return `${m}m ${s}s`;
    return `${s}s`;
}

// ──────────────────────────────────────────────
// Render Active Bots List
// ──────────────────────────────────────────────
let currentBotTab = 'all';
const OPTIONS_DEFAULT_SL_PCT = 10;
const OPTIONS_DEFAULT_TP_PCT = 20;
const OPTIONS_DEFAULT_TRAIL_PCT = 10;

function isCrypto(ticker) {
    if (!ticker) return false;
    const raw = ticker.toString().toUpperCase().trim();
    const clean = raw.replace(/[\/\-\s]/g, '');
    const settingsByTicker = typeof window !== 'undefined' ? (window.lastBotsData?.ticker_settings || {}) : {};
    const assetClass = String(settingsByTicker[raw]?.asset_class || settingsByTicker[clean]?.asset_class || '').toLowerCase();
    if (assetClass === 'crypto' || assetClass === 'cryptocurrency') return true;
    if (['stock', 'equity', 'us_equity', 'options', 'option'].includes(assetClass)) return false;
    return clean.endsWith('USD') || clean.endsWith('USDT') || clean.endsWith('USDC') ||
        ['BTC', 'ETH', 'LTC', 'SOL', 'DOGE', 'ADA', 'DOT', 'SHIB', 'AVAX', 'XRP'].includes(clean);
}

function getMarketSessionForStrategy(mode = 'regular') {
    const symbol = (window.currentStrategySymbol || '').toUpperCase();
    if (isCrypto(symbol)) return '24h';
    return document.getElementById(`strategyMarketSession_${mode}`)?.value || 'Regular';
}

function applyMarketSessionForSymbol(symbol, settings = {}) {
    ['regular', 'ai_autopilot'].forEach(mode => {
        const select = document.getElementById(`strategyMarketSession_${mode}`);
        if (!select) return;
        const crypto = isCrypto((symbol || '').toUpperCase());
        const savedSession = settings.market_session === 'Crypto24' ? '24h' : (settings.market_session || 'Regular');
        select.value = crypto ? '24h' : savedSession;
        select.disabled = crypto;
        select.title = crypto ? 'Crypto trades 24 hours, so all candles are used.' : '';
        select.classList.toggle('opacity-70', crypto);
    });
    if (window.updateMarketSessionNotice) window.updateMarketSessionNotice();
}

function setMarketSessionNotice(message = '', state = 'info') {
    ['regular', 'ai_autopilot'].forEach(mode => {
        const notice = document.getElementById(`strategyMarketSessionNotice_${mode}`);
        if (!notice) return;
        if (!message) {
            notice.classList.add('hidden');
            notice.textContent = '';
            return;
        }
        notice.textContent = message;
        notice.classList.remove('hidden', 'text-slate-500', 'text-emerald-600', 'text-amber-600', 'text-rose-600');
        const colorClass = state === 'success' ? 'text-emerald-600' : state === 'warning' ? 'text-amber-600' : state === 'error' ? 'text-rose-600' : 'text-slate-500';
        notice.classList.add(colorClass);
    });
}

window.updateMarketSessionNotice = async function () {
    const symbol = (window.currentStrategySymbol || '').toUpperCase();
    const mode = window.activeTradingMode || 'regular';
    const select = document.getElementById(`strategyMarketSession_${mode}`) || document.getElementById('strategyMarketSession_regular');
    if (!select || !symbol) {
        setMarketSessionNotice('');
        return { allowed: true };
    }

    const session = getMarketSessionForStrategy(mode);
    if (isCrypto(symbol)) {
        setMarketSessionNotice('Crypto uses 24 hr market session automatically.', 'success');
        return { allowed: true };
    }
    if (session === 'Regular') {
        setMarketSessionNotice('Regular-hours stock trading uses the normal order flow.', 'info');
        return { allowed: true };
    }
    if (session === 'Extended') {
        setMarketSessionNotice('Extended-hours stock trades will use limit orders with extended_hours enabled.', 'warning');
        return { allowed: true };
    }
    if (session !== '24h') {
        setMarketSessionNotice('');
        return { allowed: true };
    }

    setMarketSessionNotice(`Checking Alpaca 24 hr eligibility for ${symbol}...`, 'info');
    try {
        const headers = await getAuthHeaders();
        const res = await fetch(`${API_BASE}/api/assets/session_eligibility/${encodeURIComponent(symbol)}?market_session=24h`, { headers });
        const data = await res.json().catch(() => ({}));
        const allowed = res.ok && data.allowed !== false;
        setMarketSessionNotice(data.message || (allowed ? `${symbol} can use 24 hr trading.` : `${symbol} is not eligible for 24 hr trading.`), allowed ? 'success' : 'error');
        return { allowed, message: data.message };
    } catch (err) {
        const message = `Could not verify 24 hr eligibility for ${symbol}.`;
        setMarketSessionNotice(message, 'error');
        return { allowed: false, message };
    }
};

function setBotTab(tab) {
    currentBotTab = tab;
    // Update button styles
    const allBtn = document.getElementById('tabBotsAll');
    const stocksBtn = document.getElementById('tabBotsStocks');
    const cryptoBtn = document.getElementById('tabBotsCrypto');

    const inactiveClass = "flex-1 sm:flex-none px-3.5 py-1.5 text-xs font-black uppercase tracking-wider rounded-lg transition-all text-indigo-400 hover:text-indigo-600";
    const activeClass = "flex-1 sm:flex-none px-3.5 py-1.5 text-xs font-black uppercase tracking-wider rounded-lg transition-all bg-white text-indigo-600 shadow-sm border border-indigo-100";

    [allBtn, stocksBtn, cryptoBtn].forEach(btn => {
        if (btn) btn.className = inactiveClass;
    });

    const activeBtn = document.getElementById(`tabBots${tab.charAt(0).toUpperCase() + tab.slice(1)}`);
    if (activeBtn) {
        activeBtn.className = activeClass;
    }

    if (window.lastBotsData) {
        renderTradelist(window.lastBotsData.botScans || window.lastBotsData.watchlistScans, window.lastBotsData.tradelist, window.lastBotsData.tickerAmounts);
    }
}

window.goToDashboardChart = async function (ticker) {
    if (window.openBotCardChartPreview) {
        await window.openBotCardChartPreview(ticker);
    }
};

function updateBotArrangeToggleUI(totalCount = (window.lastBotsData?.tradelist || []).length) {
    const btn = document.getElementById('botArrangeToggle');
    const label = document.getElementById('botArrangeToggleLabel');
    if (!btn) return;

    if (totalCount < 2) {
        botArrangeMode = false;
        btn.disabled = true;
    } else {
        btn.disabled = false;
    }

    btn.classList.toggle('bg-emerald-600', botArrangeMode);
    btn.classList.toggle('text-white', botArrangeMode);
    btn.classList.toggle('border-emerald-600', botArrangeMode);
    btn.classList.toggle('bg-white/60', !botArrangeMode);
    btn.classList.toggle('text-emerald-600', !botArrangeMode);
    btn.classList.toggle('border-emerald-200/70', !botArrangeMode);
    btn.title = botArrangeMode ? 'Done arranging bot cards' : 'Arrange bot cards';
    btn.style.backgroundColor = botArrangeMode ? '#059669' : '';
    btn.style.borderColor = botArrangeMode ? '#047857' : '';
    btn.style.color = botArrangeMode ? '#ffffff' : '';
    btn.style.boxShadow = botArrangeMode ? '0 8px 18px rgba(5, 150, 105, 0.25)' : '';
    if (label) {
        label.textContent = botArrangeMode ? 'Done' : 'Arrange';
        label.style.color = botArrangeMode ? '#ffffff' : '';
    }
}

window.toggleBotArrangeMode = function () {
    const totalCount = (window.lastBotsData?.tradelist || []).length;
    if (totalCount < 2) return;
    botArrangeMode = !botArrangeMode;
    updateBotArrangeToggleUI(totalCount);
    renderTradelist(window.lastBotsData?.botScans || window.lastBotsData?.watchlistScans, window.lastBotsData?.tradelist, window.lastBotsData?.tickerAmounts);
};

window.toggleBotDeployGuideDrawer = function () {
    const drawer = document.getElementById('botDeployGuideDrawer');
    const arrow = document.getElementById('botDeployGuideArrow');
    if (!drawer) return;
    if (drawer.classList.contains('hidden')) {
        drawer.classList.remove('hidden');
        if (arrow) arrow.classList.add('rotate-180');
    } else {
        drawer.classList.add('hidden');
        if (arrow) arrow.classList.remove('rotate-180');
    }
};

window.switchGuideTab = function (tabId) {
    const tabs = ['overview', 'stocks', 'crypto', 'options'];
    tabs.forEach(t => {
        const btn = document.getElementById(`gtab-btn-${t}`);
        const content = document.getElementById(`gtab-content-${t}`);
        if (t === tabId) {
            if (btn) btn.className = "px-3 py-1.5 text-[0.7rem] font-black rounded-lg transition-all bg-indigo-500 text-white shadow-sm";
            if (content) {
                content.classList.remove('hidden');
                content.classList.add('block');
            }
        } else {
            if (btn) btn.className = "px-3 py-1.5 text-[0.7rem] font-black rounded-lg transition-all text-indigo-300 hover:text-white hover:bg-indigo-800/60";
            if (content) {
                content.classList.remove('block');
                content.classList.add('hidden');
            }
        }
    });
};


function buildFullOrderFromVisible(newVisibleTickers) {
    const currentFullList = window.lastBotsData?.tradelist || [];
    const visibleSet = new Set(newVisibleTickers);
    let visibleIdx = 0;
    const newFullList = [];

    for (const ticker of currentFullList) {
        if (visibleSet.has(ticker)) {
            newFullList.push(newVisibleTickers[visibleIdx++]);
        } else {
            newFullList.push(ticker);
        }
    }

    return [...new Set(newFullList)];
}

async function persistBotOrderFromVisible(newVisibleTickers) {
    const container = document.getElementById('tradelistContainer');
    const finalFullList = buildFullOrderFromVisible(newVisibleTickers);
    if (!finalFullList.length) return;

    if (container) container.classList.add('opacity-75', 'pointer-events-none');

    try {
        const headers = await getAuthHeaders();
        const response = await fetch(`${API_BASE}/api/bots/reorder`, {
            method: 'POST',
            headers,
            body: JSON.stringify({ tradelist: finalFullList })
        });
        if (response.ok) {
            if (window.lastBotsData) window.lastBotsData.tradelist = finalFullList;
            await fetchBotsData({ force: true });
        }
    } catch (err) {
        console.error("Error reordering:", err);
    } finally {
        if (container) container.classList.remove('opacity-75', 'pointer-events-none');
    }
}

window.moveBotCard = async function (ticker, direction) {
    if (!botArrangeMode) return;
    const visibleTickers = Array.from(document.querySelectorAll('#tradelistContainer .watchlist-item'))
        .map(el => el.dataset.ticker)
        .filter(Boolean);
    const idx = visibleTickers.indexOf(ticker);
    const nextIdx = idx + direction;
    if (idx < 0 || nextIdx < 0 || nextIdx >= visibleTickers.length) return;

    [visibleTickers[idx], visibleTickers[nextIdx]] = [visibleTickers[nextIdx], visibleTickers[idx]];
    await persistBotOrderFromVisible(visibleTickers);
};

function sanitizePlainText(text) {
    return String(text || '').replace(/<[^>]*>?/gm, '');
}

const botLossPauseAlertQueue = [];
let botLossPauseAlertOpen = false;
const botLossPauseAlertSeen = new Set();

try {
    const seenIds = JSON.parse(sessionStorage.getItem('botLossPauseAlertSeen') || '[]');
    if (Array.isArray(seenIds)) seenIds.forEach(id => botLossPauseAlertSeen.add(String(id)));
} catch (_) { }

function rememberBotLossPauseAlert(eventId) {
    botLossPauseAlertSeen.add(eventId);
    try {
        sessionStorage.setItem('botLossPauseAlertSeen', JSON.stringify(Array.from(botLossPauseAlertSeen).slice(-100)));
    } catch (_) { }
}

function showNextBotLossPauseAlert() {
    if (botLossPauseAlertOpen || !botLossPauseAlertQueue.length) return;
    const alertItem = botLossPauseAlertQueue.shift();
    botLossPauseAlertOpen = true;

    const overlay = document.createElement('div');
    overlay.id = 'botLossPauseAlert';
    overlay.className = 'fixed inset-0 z-[10000] flex items-center justify-center p-4 bg-slate-950/55 backdrop-blur-sm';
    overlay.innerHTML = `
        <div role="alertdialog" aria-modal="true" aria-labelledby="botLossPauseAlertTitle"
            class="w-full max-w-md rounded-3xl border border-rose-200/70 bg-white/95 p-6 shadow-2xl">
            <div class="flex items-start gap-3">
                <div class="flex h-11 w-11 shrink-0 items-center justify-center rounded-2xl bg-rose-100 text-xl">🛑</div>
                <div>
                    <h2 id="botLossPauseAlertTitle" class="text-base font-black text-slate-900">Bot paused for safety</h2>
                    <p data-role="message" class="mt-2 text-sm font-semibold leading-relaxed text-slate-600"></p>
                </div>
            </div>
            <div class="mt-6 flex flex-col-reverse gap-2 sm:flex-row sm:justify-end">
                <button type="button" data-action="dismiss"
                    class="rounded-xl border border-slate-200 px-4 py-2.5 text-xs font-black uppercase tracking-wider text-slate-600 hover:bg-slate-50">Dismiss</button>
                <button type="button" data-action="review"
                    class="rounded-xl bg-rose-600 px-4 py-2.5 text-xs font-black uppercase tracking-wider text-white shadow-lg shadow-rose-500/20 hover:bg-rose-700">Review Bot Settings</button>
            </div>
        </div>`;

    overlay.querySelector('[data-role="message"]').textContent =
        `${alertItem.ticker} reached 3 consecutive losing trades and was automatically paused. Review its strategy and risk settings, then activate the bot again manually.`;

    const closeAlert = (reviewSettings = false) => {
        overlay.remove();
        botLossPauseAlertOpen = false;
        if (reviewSettings && typeof window.openStrategyModal === 'function') {
            window.openStrategyModal(alertItem.ticker, 'edit');
        }
        setTimeout(showNextBotLossPauseAlert, 0);
    };
    overlay.querySelector('[data-action="dismiss"]').addEventListener('click', () => closeAlert(false));
    overlay.querySelector('[data-action="review"]').addEventListener('click', () => closeAlert(true));
    document.body.appendChild(overlay);
    overlay.querySelector('[data-action="review"]').focus();
}

function checkBotLossPauseAlerts(data) {
    const settingsByTicker = data?.ticker_settings || {};
    Object.entries(settingsByTicker).forEach(([ticker, settings]) => {
        if (!settings?.paused || settings.pause_code !== 'consecutive_losses') return;
        const eventId = String(settings.pause_event_id || `${ticker}:${settings.paused_at || settings.pause_reason || 'loss-streak'}`);
        if (botLossPauseAlertSeen.has(eventId)) return;
        rememberBotLossPauseAlert(eventId);
        botLossPauseAlertQueue.push({ ticker, eventId });
    });
    showNextBotLossPauseAlert();
}

function scanLogStatusClass(status) {
    switch (status) {
        case 'MARKET_CLOSED':
            return 'bg-red-100 text-red-800 border-red-300 font-bold';
        case 'COOLDOWN':
            return 'bg-purple-100 text-purple-800 border-purple-300 font-bold animate-pulse';
        case 'SIGNAL_RESET':
            return 'bg-indigo-100 text-indigo-800 border-indigo-300 font-bold';
        case 'READY':
            return 'bg-emerald-50 text-emerald-700 border-emerald-200';
        case 'GRID_MONITORING':
            return 'bg-fuchsia-50 text-fuchsia-700 border-fuchsia-200';
        case 'BLOCKED':
            return 'bg-rose-50 text-rose-700 border-rose-200';
        case 'NEAR_ENTRY':
        case 'NEAR_EXIT':
        case 'MOVING_CLOSER':
            return 'bg-amber-50 text-amber-700 border-amber-200';
        case 'MOVING_AWAY':
            return 'bg-slate-100 text-slate-600 border-slate-200';
        default:
            return 'bg-blue-50 text-blue-700 border-blue-200';
    }
}

function formatScanIndicatorGroup(label, indicators, icon = '') {
    if (!indicators || indicators.length === 0) return '';
    return `<strong class="font-bold text-slate-500">${label}:</strong> ${indicators.map(item => {
        const reason = item.reason ? ` (${item.reason})` : '';
        const itemIcon = (icon && !String(item.name || '').startsWith(icon)) ? `${icon} ` : '';
        return `${itemIcon}${item.name} ${item.signal}${reason}`;
    }).join(', ')}`;
}

function formatProtectionBox(protection, fallbackStatus = '') {
    const status = String(protection?.status || fallbackStatus || '').trim();
    if (!status) return '';

    const parts = status.split('|').map(part => part.trim()).filter(Boolean);
    const state = parts[0] || (protection?.active ? 'Active' : 'Pending');
    const type = parts[1] || String(protection?.protection_type || 'Protection').replace(/_/g, ' ');
    const tpPart = parts.find(part => /^(U-)?TP\b/i.test(part));
    const slPart = parts.find(part => /^(U-)?SL\b/i.test(part));
    const active = protection?.active || /^active$/i.test(state);
    const boxClass = active
        ? 'bg-emerald-50/70 border-emerald-200 text-emerald-800'
        : 'bg-amber-50/70 border-amber-200 text-amber-800';
    const stateClass = active
        ? 'bg-emerald-100 text-emerald-700 border-emerald-200'
        : 'bg-amber-100 text-amber-700 border-amber-200';
    const cleanType = type.replace(/\boco\b/i, 'OCO').replace(/\bwatcher\b/i, 'Watcher');
    const extraText = parts.filter(part => part !== state && part !== type && part !== tpPart && part !== slPart).join(' | ');

    return `
        <div class="mt-0.5 px-2 py-1 rounded-md border ${boxClass} flex flex-col gap-1" title="${sanitizePlainText(status)}">
            <div class="flex items-center gap-1.5 flex-wrap">
                <span class="text-[0.48rem] font-black uppercase tracking-wider opacity-70">Protection</span>
                <span class="px-1.5 py-0.5 rounded border ${stateClass} text-[0.48rem] font-black uppercase tracking-widest">${state}</span>
                <span class="text-[0.5rem] font-black uppercase tracking-wider">${cleanType}</span>
            </div>
            ${(tpPart || slPart || extraText) ? `
                <div class="flex items-center gap-1 flex-wrap text-[0.5rem] font-bold font-mono leading-tight">
                    ${tpPart ? `<span class="px-1.5 py-0.5 rounded bg-white/70 border border-current/10">${tpPart}</span>` : ''}
                    ${slPart ? `<span class="px-1.5 py-0.5 rounded bg-white/70 border border-current/10">${slPart}</span>` : ''}
                    ${extraText ? `<span class="text-[0.5rem] font-bold opacity-80">${extraText}</span>` : ''}
                </div>
            ` : ''}
        </div>
    `;
}

function formatContinuousGridForecastBox(forecast, compact = false, isCryptoAsset = false) {
    if (!forecast?.enabled) return '';
    const formatSide = (side, type) => {
        if (!side?.enabled) return '';
        const isEntry = type === 'entry';
        const label = isEntry ? 'Next Buy' : 'Next Sell';
        const color = isEntry
            ? 'bg-fuchsia-50/70 border-fuchsia-200 text-fuchsia-900'
            : 'bg-purple-50/70 border-purple-200 text-purple-900';
        const status = String(side.status || 'monitoring');
        const progress = `${Number(side.completed_count || 0)}/${Number(side.enabled_count || 0)}`;
        let primary = '';
        if (status === 'waiting_position') {
            primary = 'Waiting for a position';
        } else if (status === 'waiting_initial_entry') {
            const level = Number(side.next_level || side.display_level || 0);
            const allocationPct = Number(side.allocation_pct || 0);
            const allocationAmount = Number(side.allocation_amount || 0);
            primary = `Initial L${level} after signal • Buy ${allocationPct.toFixed(1)}%${allocationAmount > 0 ? ` / $${formatAssetPrice(allocationAmount, false)}` : ''}`;
        } else if (status === 'all_levels_complete') {
            primary = 'All enabled levels completed';
        } else {
            const level = Number(status === 'order_pending' ? side.pending_level : side.next_level) || 0;
            const fromLevel = Number(side.from_level || level);
            const rungLabel = fromLevel < level ? `L${fromLevel}–L${level}` : `L${level}`;
            const trigger = Number(side.trigger_price || 0);
            const allocationPct = Number(side.allocation_pct || 0);
            const action = isEntry ? 'at/below' : 'at/above';
            const allocation = isEntry
                ? `${allocationPct.toFixed(1)}%${Number(side.allocation_amount || 0) > 0 ? ` / $${formatAssetPrice(Number(side.allocation_amount), false)}` : ''}`
                : `${allocationPct.toFixed(1)}% of start qty`;
            primary = `${rungLabel} ${action} $${formatAssetPrice(trigger, isCryptoAsset)} • ${allocation}`;
        }
        const pendingBadge = status === 'order_pending'
            ? '<span class="px-1 py-0.5 rounded border border-amber-300 bg-amber-100 text-amber-700 text-[0.45rem] font-black uppercase">Order Pending</span>'
            : '';
        const anchor = Number(side.anchor_price || 0);
        const anchorText = anchor > 0
            ? `${isEntry ? 'Buy anchor' : 'Avg-cost anchor'} $${formatAssetPrice(anchor, isCryptoAsset)}`
            : 'Anchor pending';
        return `
            <div class="rounded-lg border ${color} px-2 py-1.5 min-w-0">
                <div class="flex items-center gap-1 flex-wrap">
                    <span class="text-[0.48rem] font-black uppercase tracking-wider">${label}</span>
                    <span class="px-1 py-0.5 rounded bg-white/70 border border-current/10 text-[0.45rem] font-black">${progress} DONE</span>
                    ${pendingBadge}
                </div>
                <div class="text-[0.5rem] sm:text-[0.55rem] font-black font-mono leading-normal break-words">${primary}</div>
                ${compact ? '' : `<div class="text-[0.46rem] sm:text-[0.5rem] font-bold opacity-70 font-mono">${anchorText}</div>`}
            </div>
        `;
    };
    const sides = [formatSide(forecast.entry, 'entry'), formatSide(forecast.exit, 'exit')].filter(Boolean).join('');
    if (!sides) return '';
    return `<div class="grid grid-cols-1 sm:grid-cols-2 gap-1.5 ${compact ? 'mt-0.5' : 'mt-1'}">${sides}</div>`;
}

function renderTradelist(scans, tradelist, tickerAmounts = {}) {
    const container = document.getElementById('tradelistContainer');
    if (!container) return;
    container.innerHTML = '';

    // Update active bot count card with breakdown
    const countEl = document.getElementById('activeBotCount');
    const subEl = document.getElementById('botStatusSub');

    const totalCount = (tradelist || []).length;
    const globalRiskHalted = Boolean(window.lastBotsData?.global_risk_status?.halted);
    const individuallyPausedCount = tradelist ? tradelist.filter(t => (window.lastBotsData?.ticker_settings || {})[t]?.paused).length : 0;
    const pausedCount = globalRiskHalted ? totalCount : individuallyPausedCount;
    const runningCount = globalRiskHalted ? 0 : totalCount - individuallyPausedCount;

    if (countEl) countEl.textContent = totalCount;
    if (subEl) {
        if (totalCount > 0) {
            subEl.innerHTML = globalRiskHalted
                ? `<span class="text-rose-600">0 Active</span> • <span class="text-rose-500">${totalCount} Global Halted</span>`
                : `<span class="text-emerald-500">${runningCount} Active</span> • <span class="text-amber-500">${pausedCount} Paused</span>`;
        } else {
            subEl.textContent = 'No bots running';
        }
    }
    updateBotArrangeToggleUI(totalCount);

    if (!tradelist || tradelist.length === 0) {
        container.innerHTML = '<p class="text-center text-purple-400 text-[0.65rem] py-4">No active bots. Add from watchlist on the Dashboard.</p>';
        return;
    }

    // Filter by Tab
    let filteredTradelist = tradelist;
    if (currentBotTab === 'stocks') {
        filteredTradelist = tradelist.filter(t => !isCrypto(t));
    } else if (currentBotTab === 'crypto') {
        filteredTradelist = tradelist.filter(t => isCrypto(t));
    }

    if (filteredTradelist.length === 0) {
        container.innerHTML = `<p class="text-center text-purple-400 text-[0.65rem] py-4">No ${currentBotTab} bots active.</p>`;
        return;
    }

    filteredTradelist.forEach((ticker, index) => {
        const scan = (scans || {})[ticker];
        const tickerSettings = (window.lastBotsData?.ticker_settings || {})[ticker] || {};
        const isCryptoFlag = isCrypto(ticker);
        const isOptions = !isCryptoFlag && Boolean(tickerSettings.options_trading);
        const premiumCardClass = isCryptoFlag ? 'crypto-premium-card' : (isOptions ? 'options-premium-card' : '');

        const item = document.createElement('div');
        item.className = `watchlist-item group active-bot ${premiumCardClass} ${selectedTicker === ticker ? 'active' : ''} ${botArrangeMode ? 'ring-1 ring-emerald-200 bg-emerald-50/30' : ''} p-2.5 mb-1.5 flex flex-col gap-1.5 transition-transform duration-200`;
        item.dataset.ticker = ticker;
        item.dataset.ticker = ticker;

        item.addEventListener('dragstart', (e) => {
            // Only allow dragging if the drag handle activated it
            if (!item.draggable) {
                e.preventDefault();
                return;
            }
            handleDragStart.call(item, e);
        });
        item.addEventListener('dragend', (e) => {
            item.draggable = false;
            handleDragEnd.call(item, e);
        });
        item.addEventListener('dragover', handleDragOver);
        item.addEventListener('dragenter', handleDragEnter);
        item.addEventListener('dragleave', handleDragLeave);
        item.addEventListener('drop', handleDrop);

        const action = scan?.action || '—';
        let price = '---';
        const isCryptoAsset = isCrypto(ticker);
        if (scan?.price) {
            const parsedPrice = parseFloat(scan.price.toString().replace('$', ''));
            price = `$${formatAssetPrice(parsedPrice, isCryptoAsset)}`;
        }
        const bullish = scan?.bullish_count ?? 0;
        const bearish = scan?.bearish_count ?? 0;
        const buyThreshold = Number(tickerSettings.min_buy_signals ?? tickerSettings.threshold ?? 0);
        const sellThreshold = Number(tickerSettings.min_sell_signals ?? tickerSettings.sell_threshold ?? 0);

        // ── Mode sets (sourced directly from updateRegularEntryFields / updateRegularExitFields) ──
        // Entry: pure structural modes where indicators are NOT used at all
        const PURE_STRUCTURAL_ENTRY_MODES = new Set([
            'swing_points', 'atr', 'percentage', 'pivot_points',
            'high_low', 'custom_price'
        ]);
        // Exit: pure structural modes where indicators are NOT used at all
        // NOTE: 'sltp' is included here because engine.py explicitly suppresses any
        // indicator SELL signal when sell_mode == 'sltp' (line 1449-1451):
        //   result['action'] = 'HOLD'  ← indicator signal overridden
        //   result['reason'] = 'Sell Signal Ignored (Fixed SL/TP Mode)'
        // Exit only happens via ATR price level breach (SL/TP). Indicator pool irrelevant.
        const PURE_STRUCTURAL_EXIT_MODES = new Set([
            'swing_points', 'atr', 'sltp', 'percentage', 'pivot_points',
            'high_low', 'custom_sltp', 'custom_underlying_sltp', 'trend_rider'
        ]);
        // Hybrid entry modes: require structural trigger + indicator confirmation
        const HYBRID_ENTRY_MODES = new Set([
            'hybrid_atr', 'hybrid_pct', 'hybrid_pivot', 'hybrid_high_low', 'hybrid_swing'
        ]);
        // Hybrid exit modes: use structural trigger + indicator signals
        const HYBRID_EXIT_MODES = new Set([
            'hybrid', 'hybrid_pct', 'hybrid_pivot', 'hybrid_high_low', 'hybrid_swing'
        ]);

        // ── Resolve modes from tickerSettings ──
        // buy_mode: always stored as 'buy_mode' in TICKER_SETTINGS
        // Prefer the live scan-log strategy mode as well as saved settings so
        // existing AI Autopilot bots render correctly even if older settings
        // omitted the ai_autopilot flag from the ticker-settings payload.
        const isAiAutopilot = Boolean(
            tickerSettings.ai_autopilot ||
            scan?.scan_log?.strategy_mode === 'ai_autopilot' ||
            scan?.strategy_mode === 'ai_autopilot' ||
            String(scan?.forecast_entry_static || '').startsWith('AI Autopilot')
        );
        const buyMode = isAiAutopilot ? 'ai_autopilot' : (tickerSettings.buy_mode || 'indicator');
        // sell_mode: stored as 'sell_mode'. options_exit_strategy is a legacy fallback only.
        const sellMode = isAiAutopilot ? 'ai_autopilot' : (tickerSettings.sell_mode || tickerSettings.options_exit_strategy || 'indicator');

        // ── Determine whether each side actively uses indicator pool ──
        const isEntryIndActive = !isAiAutopilot && !PURE_STRUCTURAL_ENTRY_MODES.has(buyMode);
        const isExitIndActive = !isAiAutopilot && !PURE_STRUCTURAL_EXIT_MODES.has(sellMode);

        // ── Label prefixes ──
        const entryLabel = isAiAutopilot ? 'Entry AI' : (HYBRID_ENTRY_MODES.has(buyMode) ? 'Entry Hybrid Ind' : 'Entry Ind');
        const exitLabel = isAiAutopilot ? 'Exit AI' : (HYBRID_EXIT_MODES.has(sellMode) ? 'Exit Hybrid Ind' : 'Exit Ind');

        // ── Badge color for the Decision badge (action uses BUY/SELL/HOLD) ──
        const badgeColor = (signal) => signal === 'BUY'
            ? 'bg-emerald-50 text-emerald-600 border-emerald-100'
            : signal === 'SELL'
                ? 'bg-red-50 text-red-600 border-red-100'
                : 'bg-slate-50 text-slate-400 border-slate-100';
        const actionColor = badgeColor(action);

        // ── Badge color for indicator status rows (uses BULLISH/BEARISH/HOLD) ──
        const indBadgeColor = (sig) => sig === 'BULLISH'
            ? 'bg-emerald-50 text-emerald-600 border-emerald-100'
            : sig === 'BEARISH'
                ? 'bg-red-50 text-red-600 border-red-100'
                : 'bg-slate-50 text-slate-400 border-slate-100';

        // ── Progress badge helpers ──
        const bullishRemaining = buyThreshold > 0 ? Math.max(0, buyThreshold - bullish) : null;
        const bearishRemaining = sellThreshold > 0 ? Math.max(0, sellThreshold - bearish) : null;
        const bullishHit = buyThreshold > 0 && bullish >= buyThreshold;
        const bearishHit = sellThreshold > 0 && bearish >= sellThreshold;
        const bullishProgressText = buyThreshold > 0 ? `${bullish}/${buyThreshold}B` : `${bullish}B`;
        const bearishProgressText = sellThreshold > 0 ? `${bearish}/${sellThreshold}S` : `${bearish}S`;
        const bullishProgressTitle = buyThreshold > 0
            ? `${bullish} bullish indicator signal${bullish === 1 ? '' : 's'} active. ${bullishHit ? 'Bullish threshold met.' : `${bullishRemaining} more needed for bullish threshold.`}`
            : `${bullish} bullish indicator signal${bullish === 1 ? '' : 's'} active. No buy threshold configured.`;
        const bearishProgressTitle = sellThreshold > 0
            ? `${bearish} bearish indicator signal${bearish === 1 ? '' : 's'} active. ${bearishHit ? 'Bearish threshold met.' : `${bearishRemaining} more needed for bearish threshold.`}`
            : `${bearish} bearish indicator signal${bearish === 1 ? '' : 's'} active. No sell threshold configured.`;
        const bullishProgressClass = bullishHit
            ? 'text-emerald-700 bg-emerald-100 border-emerald-200'
            : 'text-emerald-600 bg-emerald-50 border-emerald-100';
        const bearishProgressClass = bearishHit
            ? 'text-red-700 bg-red-100 border-red-200'
            : 'text-red-600 bg-red-50 border-red-100';
        const tickerTf = (window.lastBotsData?.ticker_settings || {})[ticker]?.timeframe || '';
        const tfLabel = tickerTf || (window.lastBotsData?.strategyTimeframe || '5Min');
        const isLocallyPaused = (window.lastBotsData?.ticker_settings || {})[ticker]?.paused || false;
        const isGlobalRiskHalted = Boolean(window.lastBotsData?.global_risk_status?.halted);
        const isPaused = isLocallyPaused || isGlobalRiskHalted;
        const inWatchlist = (window.lastBotsData?.watchlist || []).includes(ticker);
        const dotClass = isPaused
            ? 'h-2 w-2 bg-slate-300 rounded-full'
            : 'h-2 w-2 bg-emerald-500 rounded-full animate-pulse shadow-[0_0_8px_rgba(16,185,129,0.8)]';

        // Check for active position (handling option symbols starting with the underlying ticker)
        const positions = window.lastBotsData?.positions || [];
        const activePosition = positions.find(p => {
            if (p.symbol === ticker) return true;
            if (tickerSettings.options_trading && p.symbol.startsWith(ticker) && (p.is_option || p.symbol.length >= 15)) {
                return true;
            }
            return false;
        });
        const decisionLabel = isOptions
            ? (activePosition && action === 'SELL'
                ? 'EXIT'
                : action === 'BUY'
                    ? 'CALL'
                    : action === 'SELL'
                        ? 'PUT'
                        : action)
            : action;
        const decisionTitle = isOptions
            ? 'Decision: final bot action. When flat, BUY maps to CALL and SELL maps to PUT. When holding an option, SELL means exit.'
            : 'Decision: final bot action from the selected entry/exit strategy.';
        const decisionBadgeHtml = `
            <span class="px-1.5 py-0.5 rounded border ${actionColor} font-black text-[0.5rem] tracking-widest uppercase" title="${decisionTitle}">
                <span class="opacity-60">Decision</span> ${decisionLabel}
            </span>
        `;

        // ── Put position detection (for options dual-direction exit badge) ──
        // Primary: OCC symbol format XXXXXYYMMDD[C|P]XXXXXXXX — 'P' is at offset [length - 9]
        // Secondary: option_type field from backend (may be null for non-Alpaca brokers)
        const isPutPosition = !!activePosition && (
            (activePosition.option_type === 'PUT') ||
            (typeof activePosition.symbol === 'string' &&
                activePosition.symbol.length >= 15 &&
                activePosition.symbol.charAt(activePosition.symbol.length - 9) === 'P')
        );

        // ── Options contract direction ──
        const optionsType = tickerSettings.options_type || 'calls_only'; // 'calls_only' | 'puts_only'
        const isLegacyBoth = isOptions && optionsType === 'both';
        const isCallsOnly = isOptions && optionsType === 'calls_only';
        const isPutsOnly = isOptions && optionsType === 'puts_only';

        // ── N/A badge (shared style for inactive sides) ──
        const naClass = 'bg-slate-50 text-slate-400 border-slate-100';
        const naBadge = (labelText) =>
            `<span class="px-1.5 py-0.5 rounded border ${naClass} font-black text-[0.48rem] tracking-widest uppercase" title="Indicator signals are not used for this strategy mode.">${labelText} N/A</span>`;

        // ────────────────────────────────────────────
        // BUILD: Entry Indicator HTML
        // ────────────────────────────────────────────
        // Signal labels use BULLISH / BEARISH / HOLD (not BUY/SELL).
        //
        // Logic:
        //  - If entry mode is pure structural → N/A badge
        //  - If entry mode uses indicators (indicator / hybrid_*):
        //      Regular/Crypto  : BULLISH/HOLD + B count ONLY — never show BEARISH or S count
        //      Options Calls Only: BULLISH/HOLD + B count only
        //      Options Puts Only : BEARISH/HOLD + S count only
        //      Legacy Both      : disabled; user must choose CALL or PUT
        let entryIndHtml;
        if (isAiAutopilot) {
            const aiEntrySignal = action === 'BUY' ? 'BUY' : 'HOLD';
            entryIndHtml = `<span class="px-1.5 py-0.5 rounded border ${badgeColor(aiEntrySignal)} font-black text-[0.58rem] sm:text-[0.62rem] tracking-widest uppercase" title="AI Autopilot controls entry decisions from the AI market analysis.">${entryLabel} <span class="opacity-75">${aiEntrySignal}</span></span>`;
        } else if (!isEntryIndActive) {
            entryIndHtml = naBadge(entryLabel);
        } else {
            let entrySignal;
            let entryProgressBadges = '';
            let entryProgressTitle = '';

            if (isLegacyBoth) {
                entrySignal = 'HOLD';
                entryProgressBadges = `<span class="text-[0.6rem] font-bold px-1 rounded border bg-amber-50 text-amber-700 border-amber-200" title="Legacy Both direction disabled. Choose CALL or PUT.">DISABLED</span>`;
                entryProgressTitle = 'Legacy Both direction disabled. Choose CALL or PUT.';
            } else if (isPutsOnly) {
                // Options Puts Only: only bearish signal matters for entry
                entrySignal = bearishHit ? 'BEARISH' : 'HOLD';
                entryProgressBadges = `<span class="text-[0.6rem] font-bold px-1 rounded border ${bearishProgressClass}" title="${bearishProgressTitle}">${bearishProgressText}</span>`;
                entryProgressTitle = bearishProgressTitle;
            } else {
                // Regular/Crypto or Options Calls Only:
                // ONLY bullish signal is relevant for entry — never show BEARISH or S count
                entrySignal = bullishHit ? 'BULLISH' : 'HOLD';
                entryProgressBadges = `<span class="text-[0.6rem] font-bold px-1 rounded border ${bullishProgressClass}" title="${bullishProgressTitle}">${bullishProgressText}</span>`;
                entryProgressTitle = bullishProgressTitle;
            }

            const entrySignalColor = indBadgeColor(entrySignal);
            const entryLabelBadge = `<span class="px-1.5 py-0.5 rounded border ${entrySignalColor} font-black text-[0.58rem] sm:text-[0.62rem] tracking-widest uppercase" title="Entry indicator pool: ${buyMode} mode. ${entryProgressTitle}">${entryLabel} <span class="opacity-75">${entrySignal}</span></span>`;

            entryIndHtml = entryLabelBadge + entryProgressBadges;
        }

        // ────────────────────────────────────────────
        // BUILD: Exit Indicator HTML
        // ────────────────────────────────────────────
        // Signal labels use BULLISH / BEARISH / HOLD (not BUY/SELL).
        //
        // Logic:
        //  - If exit mode is pure structural → N/A
        //  - If exit mode uses indicators (indicator / hybrid_*):
        //      Regular/Crypto           : BEARISH/HOLD + S count (exit long on bearish)
        //      Options holding CALL     : BEARISH/HOLD + S count (bearish needed to close)
        //      Options holding PUT      : BULLISH/HOLD + B count (bullish needed to close)
        //      Options flat (calls): BEARISH/HOLD + S count (default exit readiness)
        //      Options flat (puts only) : BULLISH/HOLD + B count (default exit readiness)
        let exitIndHtml;
        if (isAiAutopilot) {
            const aiExitSignal = activePosition && action === 'SELL' ? 'SELL' : 'HOLD';
            exitIndHtml = `<span class="px-1.5 py-0.5 rounded border ${badgeColor(aiExitSignal)} font-black text-[0.58rem] sm:text-[0.62rem] tracking-widest uppercase" title="AI Autopilot controls exits from position risk, trend invalidation, stops, and targets.">${exitLabel} <span class="opacity-75">${aiExitSignal}</span></span>`;
        } else if (!isExitIndActive) {
            exitIndHtml = naBadge(exitLabel);
        } else {
            let exitSignal;
            let exitProgressBadges = '';
            let exitProgressTitle = '';

            if (isOptions) {
                if (activePosition) {
                    if (isPutPosition) {
                        // Holding a Put — need bullish reversal to exit
                        exitSignal = bullishHit ? 'BULLISH' : 'HOLD';
                        exitProgressBadges = `<span class="text-[0.6rem] font-bold px-1 rounded border ${bullishProgressClass}" title="${bullishProgressTitle}">${bullishProgressText}</span>`;
                        exitProgressTitle = bullishProgressTitle;
                    } else {
                        // Holding a Call — need bearish reversal to exit
                        exitSignal = bearishHit ? 'BEARISH' : 'HOLD';
                        exitProgressBadges = `<span class="text-[0.6rem] font-bold px-1 rounded border ${bearishProgressClass}" title="${bearishProgressTitle}">${bearishProgressText}</span>`;
                        exitProgressTitle = bearishProgressTitle;
                    }
                } else {
                    // Flat — no position open, show default exit readiness direction
                    if (isPutsOnly) {
                        // Puts Only bot: needs bullish to exit a future Put
                        exitSignal = bullishHit ? 'BULLISH' : 'HOLD';
                        exitProgressBadges = `<span class="text-[0.6rem] font-bold px-1 rounded border ${bullishProgressClass}" title="${bullishProgressTitle}">${bullishProgressText}</span>`;
                        exitProgressTitle = bullishProgressTitle;
                    } else {
                        // Calls Only: needs bearish to exit a future Call
                        exitSignal = bearishHit ? 'BEARISH' : 'HOLD';
                        exitProgressBadges = `<span class="text-[0.6rem] font-bold px-1 rounded border ${bearishProgressClass}" title="${bearishProgressTitle}">${bearishProgressText}</span>`;
                        exitProgressTitle = bearishProgressTitle;
                    }
                }
            } else {
                // Regular stock / crypto: always long — exit on bearish signals
                exitSignal = bearishHit ? 'BEARISH' : 'HOLD';
                exitProgressBadges = `<span class="text-[0.6rem] font-bold px-1 rounded border ${bearishProgressClass}" title="${bearishProgressTitle}">${bearishProgressText}</span>`;
                exitProgressTitle = bearishProgressTitle;
            }

            const exitSignalColor = indBadgeColor(exitSignal);
            const exitModeTitle = `Exit indicator pool: ${sellMode} mode. ${exitProgressTitle}`;
            const exitLabelBadge = `<span class="px-1.5 py-0.5 rounded border ${exitSignalColor} font-black text-[0.58rem] sm:text-[0.62rem] tracking-widest uppercase" title="${exitModeTitle}">${exitLabel} <span class="opacity-75">${exitSignal}</span></span>`;

            exitIndHtml = exitLabelBadge + exitProgressBadges;
        }

        const pauseReason = isGlobalRiskHalted
            ? `Global risk halt: ${window.lastBotsData?.global_risk_status?.halt_reason || 'Review global risk controls before reactivation.'}`
            : ((window.lastBotsData?.ticker_settings || {})[ticker]?.pause_reason || '');
        let pauseReasonHtml = '';
        if (isPaused && pauseReason) {
            pauseReasonHtml = `
                <div class="mt-1 px-2.5 py-1.5 bg-amber-500/10 border border-amber-500/20 rounded-xl text-[0.58rem] font-black text-amber-700 flex items-center gap-1.5 leading-snug">
                    <span>⚠️</span>
                    <span>${pauseReason}</span>
                </div>
            `;
        }

        let positionHtml = '';
        let scanLogHtml = '';
        const continuousGridForecast = scan?.continuous_grid_forecast || scan?.scan_log?.continuous_grid_forecast || null;
        const continuousGridForecastHtml = formatContinuousGridForecastBox(continuousGridForecast, false, isCryptoAsset);
        if (activePosition) {
            const qty = activePosition.qty;
            const avgCostVal = activePosition.avg_price;

            // Dynamic price formatting helper
            const formatPrice = (val) => formatAssetPrice(val, isCrypto);

            const avgCost = formatPrice(avgCostVal);
            const mktValue = activePosition.market_value.toFixed(2);
            const currPriceVal = activePosition.current_price;
            const currPrice = formatPrice(currPriceVal);

            const dailyPl = activePosition.unrealized_intraday_pl || 0;
            const dailyPlPc = activePosition.unrealized_intraday_plpc || 0;
            const dailyPlStr = dailyPl >= 0 ? `+$${dailyPl.toFixed(2)}` : `-$${Math.abs(dailyPl).toFixed(2)}`;
            const dailyPlPcStr = dailyPlPc >= 0 ? `(+${dailyPlPc.toFixed(2)}%)` : `(${dailyPlPc.toFixed(2)}%)`;
            const dailyColorClass = dailyPl >= 0 ? 'text-emerald-500 bg-emerald-50/50' : 'text-rose-500 bg-rose-50/50';

            const totalPl = activePosition.unrealized_pl || 0;
            const totalPlPc = activePosition.unrealized_pl_pct || 0;
            const totalPlStr = totalPl >= 0 ? `+$${totalPl.toFixed(2)}` : `-$${Math.abs(totalPl).toFixed(2)}`;
            const totalPlPcStr = totalPlPc >= 0 ? `(+${totalPlPc.toFixed(2)}%)` : `(${totalPlPc.toFixed(2)}%)`;
            const totalColorClass = totalPl >= 0 ? 'text-emerald-500 bg-emerald-50/50' : 'text-rose-500 bg-rose-50/50';

            const costBasis = activePosition.cost_basis !== undefined
                ? activePosition.cost_basis.toFixed(2)
                : (qty * avgCostVal).toFixed(2);
            const qtyDisp = qty % 1 === 0 ? qty : (qty < 1 ? qty.toFixed(6) : qty.toFixed(4));

            const rawForecastExitText = scan?.forecast_exit_static || activePosition.exit_forecast_static || activePosition.exit_forecast || 'Checking exit criteria...';
            const forecastExitText = String(rawForecastExitText).replace(/\[(Entry|Exit) Strategy\]\s*/gi, '');
            positionHtml = `
                <div class="mt-2 pt-2 border-t border-slate-50 grid grid-cols-2 sm:grid-cols-4 gap-2 sm:gap-1.5 cursor-pointer" onclick="selectTicker('${ticker}')">
                    <div class="flex flex-col">
                        <span class="text-[0.52rem] text-slate-400 font-bold uppercase tracking-wider">Holding</span>
                        <span class="text-[0.62rem] font-black text-indigo-950 font-mono tracking-tight">${qtyDisp} @ $${avgCost}</span>
                    </div>
                    <div class="flex flex-col">
                        <span class="text-[0.52rem] text-slate-400 font-bold uppercase tracking-wider">Total Cost</span>
                        <span class="text-[0.62rem] font-black text-indigo-950 font-mono tracking-tight">$${costBasis}</span>
                    </div>
                    <div class="flex flex-col">
                        <span class="text-[0.52rem] text-slate-400 font-bold uppercase tracking-wider">Market Val</span>
                        <span class="text-[0.62rem] font-black text-indigo-950 font-mono tracking-tight">$${mktValue}</span>
                    </div>
                    <div class="flex flex-col gap-1 justify-center">
                        <div class="flex items-center justify-between px-1.5 py-0.5 rounded ${dailyColorClass} w-full leading-none">
                            <span class="text-[0.45rem] font-bold uppercase opacity-85">1D</span>
                            <span class="text-[0.55rem] font-black font-mono tracking-tight ml-1">${dailyPlStr} <span class="opacity-75 text-[0.45rem] font-medium ml-0.5">${dailyPlPcStr}</span></span>
                        </div>
                        <div class="flex items-center justify-between px-1.5 py-0.5 rounded ${totalColorClass} w-full leading-none">
                            <span class="text-[0.45rem] font-bold uppercase opacity-85">Tot</span>
                            <span class="text-[0.55rem] font-black font-mono tracking-tight ml-1">${totalPlStr} <span class="opacity-75 text-[0.45rem] font-medium ml-0.5">${totalPlPcStr}</span></span>
                        </div>
                    </div>
                </div>
                <div class="mt-1.5 pt-1.5 border-t border-slate-50 flex flex-col justify-center cursor-pointer w-full min-w-0" onclick="selectTicker('${ticker}')">
                    <div class="flex items-center gap-1.5 mb-1 flex-wrap">
                        <span class="text-[0.52rem] text-slate-400 font-bold uppercase tracking-wider">${continuousGridForecast?.enabled ? 'Live Adaptive Grid Forecast' : 'Forecast Exit Strategy'}</span>
                    </div>
                    ${continuousGridForecast?.enabled
                        ? continuousGridForecastHtml
                        : `<span class="text-[0.5rem] sm:text-[0.55rem] font-bold text-slate-500 font-mono leading-normal whitespace-normal break-words w-full min-w-0" title="${sanitizePlainText(forecastExitText)}">${forecastExitText}</span>`}
                </div>
            `;
        } else {
            const rawForecastText = scan?.forecast_entry_static || scan?.entry_forecast || "Standing by. Watching market for entry...";
            const forecastText = String(rawForecastText).replace(/\[(Entry|Exit) Strategy\]\s*/gi, '');
            positionHtml = `
                <div class="mt-2 pt-2 border-t border-slate-50 flex flex-col justify-center cursor-pointer w-full min-w-0" onclick="selectTicker('${ticker}')">
                    <div class="flex items-center gap-1.5 mb-1 flex-wrap">
                        <span class="text-[0.52rem] text-slate-400 font-bold uppercase tracking-wider">${continuousGridForecast?.enabled ? 'Live Adaptive Grid Forecast' : 'Forecast Entry Strategy'}</span>
                    </div>
                    ${continuousGridForecast?.enabled
                        ? continuousGridForecastHtml
                        : `<span class="text-[0.5rem] sm:text-[0.55rem] font-bold text-slate-500 font-mono leading-normal whitespace-normal break-words w-full min-w-0" title="${forecastText.replace(/<[^>]*>?/gm, '')}">${forecastText}</span>`}
                </div>
            `;
        }

        const scanLog = scan?.scan_log || null;
        if (scanLog) {
            const indicators = Array.isArray(scanLog.indicators) ? scanLog.indicators : [];
            const confirmedText = formatScanIndicatorGroup('Confirmed', indicators.filter(item => item.status === 'CONFIRMED'), '🟢');
            const waitingText = formatScanIndicatorGroup('Waiting', indicators.filter(item => item.status === 'WAITING'), '🟡');
            const opposingText = formatScanIndicatorGroup('Opposing', indicators.filter(item => item.status === 'OPPOSING'), '🔴');
            const blockers = Array.isArray(scanLog.blockers) ? scanLog.blockers : [];
            const primaryBlocker = blockers[0] || '';
            const secondaryBlockers = blockers.slice(1);
            const protection = scanLog.exit_protection || activePosition?.exit_protection || {};
            const protectionText = protection.status || activePosition?.exit_protection_status || '';
            const protectionBoxHtml = formatProtectionBox(protection, protectionText);
            const gridScanForecastHtml = formatContinuousGridForecastBox(
                scanLog.continuous_grid_forecast || continuousGridForecast,
                true,
                isCryptoAsset
            );
            const statusText = String(scanLog.status || 'WAITING_ENTRY').replace(/_/g, ' ');
            const confirmedStrength = Number.isFinite(Number(scanLog.confirmed_weight))
                ? Number(scanLog.confirmed_weight)
                : Number(scanLog.confirmed_count || 0);
            const fallbackProgressText = scanLog.required_count
                ? `${scanLog.target_direction || 'BULLISH'} ${confirmedStrength}/${scanLog.required_count} confirmed${scanLog.remaining_count > 0 ? `, needs ${scanLog.remaining_count}` : ''}`
                : (scanLog.target_direction || 'Monitoring');
            const progressText = scanLog.progress_label || fallbackProgressText;
            const summaryText = [progressText, primaryBlocker].filter(Boolean).join(' | ') || scanLog.summary || 'Waiting for next heartbeat...';
            const detailParts = [
                secondaryBlockers.length ? `Also blocked by: ${secondaryBlockers.join(' | ')}` : '',
                confirmedText,
                waitingText,
                opposingText,
            ].filter(Boolean);
            const rawDetail = detailParts.join(' | ');
            const cleanDetail = rawDetail.replace(/[🟢🟡🔴]\s*(Confirmed|Waiting|Opposing):/g, '$1:');
            const cleanTitle = (scanLog.detail || cleanDetail).replace(/[🟢🟡🔴]\s*(Confirmed|Waiting|Opposing):/g, '$1:');
            const formattedDetail = cleanDetail.replace(/(?<!<strong[^>]*>)(Confirmed|Waiting|Opposing):/g, '<strong class="font-bold text-slate-500">$1:</strong>');
            const detailHtml = formattedDetail
                ? `<span class="text-[0.58rem] sm:text-[0.65rem] text-slate-600 font-medium leading-relaxed whitespace-normal break-words w-full min-w-0" title="${sanitizePlainText(cleanTitle)}">${formattedDetail}</span>`
                : '';

            const isOptBot = scanLog.is_option || tickerSettings.options_trading || tickerSettings.is_option;
            const isMarketClosed = scanLog.is_options_market_closed || scanLog.is_stock_market_closed || scanLog.status === 'MARKET_CLOSED';
            if (isMarketClosed) {
                const secToPre = Number(scanLog.seconds_to_pre_open || 0);
                const secToOpen = Number(scanLog.seconds_to_open || 0);
                const nextOpenStr = scanLog.next_open_str || 'Market Open';
                const countdownText = secToPre > 0
                    ? `⏱️ Starts scanning in ${formatDurationCompactJs(secToPre)} (2m prior to Market Open)`
                    : `🔥 Pre-Market Active! Scanning started (${formatDurationCompactJs(secToOpen)} until Open)`;

                const sessionTag = isOptBot ? 'Options' : (scanLog.market_session || (tickerSettings.market_session && tickerSettings.market_session.toLowerCase() === 'extended' ? 'Extended' : 'Regular'));

                scanLogHtml = `
                    <div class="mt-2 pt-2 border-t border-slate-100 dark:border-slate-800 flex flex-col gap-1.5 cursor-pointer w-full min-w-0" onclick="selectTicker('${ticker}')">
                        <div class="w-full p-2.5 rounded-xl bg-gradient-to-r from-red-500/15 via-rose-500/20 to-red-500/15 border-2 border-red-500/60 dark:border-red-500/50 shadow-sm flex flex-col sm:flex-row items-start sm:items-center justify-between gap-2">
                            <div class="flex items-center gap-1.5 font-black text-red-600 dark:text-red-400 text-[0.65rem] sm:text-[0.7rem] uppercase tracking-widest">
                                <span class="text-base leading-none">🛑</span>
                                <span>MARKET CLOSED</span>
                                <span class="px-1.5 py-0.5 rounded bg-red-600 dark:bg-red-500 text-white font-mono font-bold text-[0.5rem] uppercase tracking-normal">${sessionTag}</span>
                            </div>
                            <span class="options-market-countdown font-mono font-bold text-[0.55rem] sm:text-[0.6rem] bg-red-500/10 px-2 py-1 rounded-lg border border-red-500/30" style="color: #991b1b !important;" data-rendered-at="${Date.now()}" data-initial-pre-open="${secToPre}" data-initial-open="${secToOpen}">
                                ${countdownText}
                            </span>
                        </div>
                        <span class="text-[0.5rem] sm:text-[0.55rem] font-medium text-slate-500 dark:text-slate-400 leading-normal whitespace-normal break-words w-full min-w-0">
                            ⚡ Scanning resumes automatically 2 minutes prior to ${nextOpenStr}.
                        </span>
                        ${gridScanForecastHtml}
                    </div>
                `;
            } else {
                const refreshRate = Number(scanLog.refresh_rate_sec) || (isOptBot ? 10 : 60);
                let timeAgoText = '';
                if (scanLog.updated_at) {
                    try {
                        const dt = new Date(scanLog.updated_at);
                        if (!isNaN(dt.getTime())) {
                            const secAgo = Math.max(0, Math.floor((Date.now() - dt.getTime()) / 1000));
                            const remMod = secAgo % refreshRate;
                            const initialRemaining = remMod === 0 && secAgo > 0 ? refreshRate : (refreshRate - remMod);
                            const timeStr = dt.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit', hour12: false });
                            timeAgoText = `<span class="scan-countdown text-[0.48rem] font-mono text-slate-400 font-normal ml-auto" data-rendered-at="${Date.now()}" data-refresh-rate="${refreshRate}" data-initial-remaining="${initialRemaining}" title="Last scan at ${timeStr} (${secAgo}s ago)">⏱️ Refreshing in ${initialRemaining}s</span>`;
                        }
                    } catch (e) { }
                }

                scanLogHtml = `
                    <div class="mt-1.5 pt-1.5 border-t border-slate-50 flex flex-col gap-1 cursor-pointer w-full min-w-0" onclick="selectTicker('${ticker}')">
                        <div class="flex items-center gap-1.5 flex-wrap">
                            <span class="text-[0.52rem] text-slate-400 font-bold uppercase tracking-wider">Scan Log</span>
                            <span class="px-1.5 py-0.5 rounded border ${scanLogStatusClass(scanLog.status)} font-black text-[0.48rem] tracking-widest uppercase">${statusText}</span>
                            ${decisionBadgeHtml}
                            ${timeAgoText}
                        </div>
                        <span class="text-[0.5rem] sm:text-[0.55rem] font-bold text-slate-500 font-mono leading-normal whitespace-normal break-words w-full min-w-0" title="${sanitizePlainText(scanLog.summary || summaryText)}">${summaryText}</span>
                        ${gridScanForecastHtml}
                        ${protectionBoxHtml}
                        ${detailHtml}
                    </div>
                `;
            }
        }

        const startedAt = tickerSettings.started_at;
        const lastTradeAt = tickerSettings.last_trade_at;
        const totalTrades = tickerSettings.total_trades || 0;
        const entryTrades = Number(tickerSettings.entry_trades || 0);
        const maxTrades = Number(tickerSettings.max_trades || 0);
        let todayTrades = tickerSettings.today_trades || 0;

        // Validate daily count based on local date
        const lastTradeDate = tickerSettings.last_trade_date;
        const currentDate = new Date().toLocaleDateString('en-CA'); // YYYY-MM-DD
        if (lastTradeDate && lastTradeDate !== currentDate) {
            todayTrades = 0;
        }

        let runtimeText = 'Not started';
        if (isPaused) {
            runtimeText = 'Paused';
        } else if (startedAt) {
            runtimeText = formatUptime(startedAt);
        }

        let lastTradeText = 'No trades yet';
        if (lastTradeAt) {
            lastTradeText = `Last trade: ${formatRelativeTime(lastTradeAt)}`;
        }

        let tradeBadgeHtml = `<div class="flex items-center divide-x divide-indigo-200/50 rounded-md bg-indigo-50/50 border border-indigo-100/50 overflow-hidden shadow-sm">
            <span class="px-1.5 py-0.5 text-indigo-600 font-black text-[0.55rem] uppercase tracking-tight">${todayTrades} TODAY</span>
            <span class="px-1.5 py-0.5 text-indigo-600 font-black text-[0.55rem] uppercase tracking-tight">${totalTrades} TOTAL</span>
            ${maxTrades > 0 ? `<span class="px-1.5 py-0.5 text-amber-600 font-black text-[0.55rem] uppercase tracking-tight">${entryTrades}/${maxTrades} LIMIT</span>` : ''}
        </div>`;

        const allocationMode = tickerSettings.options_trading
            ? (tickerSettings.options_allocation_mode || tickerSettings.allocation_mode || 'fixed')
            : (tickerSettings.allocation_mode || 'fixed');
        const allocationBase = tickerSettings.options_trading
            ? Number(tickerSettings.options_trade_amount || tickerSettings.amount || 0)
            : Number(tickerSettings.amount || 0);
        const allocationCurrent = tickerSettings.options_trading
            ? Number(tickerSettings.options_compound_amount || allocationBase)
            : Number(tickerSettings.compound_amount || allocationBase);
        const allocationText = allocationMode === 'compound'
            ? `Compound $${allocationCurrent.toFixed(2)}`
            : `Fixed $${allocationBase.toFixed(2)}`;
        const allocationBadgeHtml = `<span class="px-1.5 py-0.5 rounded-md bg-slate-50 text-slate-500 border border-slate-100 font-black text-[0.55rem] uppercase tracking-tight">${allocationText}</span>`;

        const fromBacktest = (window.lastBotsData?.ticker_settings || {})[ticker]?.source === 'backtest';
        const isIncubator = (window.lastBotsData?.ticker_settings || {})[ticker]?.is_incubator;
        const backtestBadge = fromBacktest && !isIncubator
            ? `<span class="px-1.5 py-0.5 rounded-full bg-violet-100 text-violet-600 font-black text-[0.5rem] border border-violet-200 uppercase tracking-widest ml-1">
                 🧪 From Backtest
               </span>`
            : '';
        const incubatorBadge = '';
        const isOpt = tickerSettings.options_trading || tickerSettings.is_option || (scan && scan.scan_log && scan.scan_log.is_option);
        const fastRefreshBadge = isOpt ? `<span class="px-1.5 py-0.5 rounded-md bg-amber-50 text-amber-600 border border-amber-200 font-black text-[0.55rem] uppercase tracking-tight ml-1 inline-flex items-center gap-0.5" title="Options Bot running on 10s Fast Quant Loop">⚡ 10s Refresh</span>` : '';
        const arrangeControlsHtml = botArrangeMode ? `
                        <div class="flex items-center gap-1 mr-1">
                            <button class="h-7 w-7 flex items-center justify-center rounded-lg bg-white/80 border border-emerald-200 text-emerald-600 disabled:opacity-30 disabled:text-slate-300 disabled:border-slate-200"
                                title="Move up"
                                ${index === 0 ? 'disabled' : ''}
                                onclick="event.stopPropagation(); window.moveBotCard('${ticker}', -1)">
                                <svg xmlns="http://www.w3.org/2000/svg" class="h-3.5 w-3.5" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                                    <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2.5" d="M5 15l7-7 7 7" />
                                </svg>
                            </button>
                            <button class="h-7 w-7 flex items-center justify-center rounded-lg bg-white/80 border border-emerald-200 text-emerald-600 disabled:opacity-30 disabled:text-slate-300 disabled:border-slate-200"
                                title="Move down"
                                ${index === filteredTradelist.length - 1 ? 'disabled' : ''}
                                onclick="event.stopPropagation(); window.moveBotCard('${ticker}', 1)">
                                <svg xmlns="http://www.w3.org/2000/svg" class="h-3.5 w-3.5" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                                    <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2.5" d="M19 9l-7 7-7-7" />
                                </svg>
                            </button>
                        </div>
        ` : '';
        const dragHandleHtml = botArrangeMode ? `
                        <span class="cursor-grab text-emerald-400 hover:text-emerald-600 hover:bg-emerald-50 p-0.5 rounded transition-all active:cursor-grabbing"
                              onmousedown="this.closest('.watchlist-item').draggable = true;"
                              onmouseup="this.closest('.watchlist-item').draggable = false;"
                              title="Drag to reorder">
                            <svg xmlns="http://www.w3.org/2000/svg" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><circle cx="9" cy="12" r="1"/><circle cx="9" cy="5" r="1"/><circle cx="9" cy="19" r="1"/><circle cx="15" cy="12" r="1"/><circle cx="15" cy="5" r="1"/><circle cx="15" cy="19" r="1"/></svg>
                        </span>
        ` : '';

        item.innerHTML = `
                <!-- 1st Line: Ticker Name, Price, Watchlist Star, Settings Gear, and Remove Bot Cross -->
                <div class="flex items-start sm:items-center justify-between gap-1.5 cursor-text">
                    <div class="flex items-center gap-1.5 sm:gap-2 flex-wrap">
                        ${arrangeControlsHtml}
                        ${dragHandleHtml}
                        <div class="${dotClass} cursor-pointer shrink-0" onclick="selectTicker('${ticker}')"></div>
                        <span class="font-black text-sm ${isPaused ? 'text-slate-400' : 'text-indigo-950'} tracking-tight cursor-pointer" onclick="selectTicker('${ticker}')">${ticker}</span>
                        <span class="text-[0.75rem] font-black text-slate-600 font-mono tracking-tight ml-1">${price}</span>
                        ${backtestBadge}${incubatorBadge}${allocationBadgeHtml}${fastRefreshBadge}
                    </div>
                    
                    <div class="flex items-center gap-1 opacity-90 sm:opacity-60 group-hover:opacity-100 transition-opacity ml-1 shrink-0">
                        <button class="p-1 rounded hover:bg-amber-50 ${inWatchlist ? 'text-amber-400' : 'text-slate-300 hover:text-amber-400'} transition-all" 
                            title="Toggle Watchlist"
                            onclick="event.stopPropagation(); toggleWatchlist('${ticker}')">
                            <svg xmlns="http://www.w3.org/2000/svg" class="h-3.5 w-3.5 ${inWatchlist ? 'fill-current' : ''}" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                                <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2.5" d="M11.049 2.927c.3-.921 1.603-.921 1.902 0l1.519 4.674a1 1 0 00.95.69h4.915c.969 0 1.371 1.24.588 1.81l-3.976 2.888a1 1 0 00-.363 1.118l1.518 4.674c.3.922-.755 1.688-1.538 1.118l-3.976-2.888a1 1 0 00-1.176 0l-3.976 2.888c-.783.57-1.838-.197-1.538-1.118l1.518-4.674a1 1 0 00-.363-1.118l-3.976-2.888c-.784-.57-.38-1.81.588-1.81h4.914a1 1 0 00.951-.69l1.519-4.674z" />
                            </svg>
                        </button>
                        <button class="p-1 rounded hover:bg-indigo-50 text-indigo-400 transition-all" 
                            title="Configure Strategy"
                            onclick="event.stopPropagation(); openStrategyModal('${ticker}', 'edit')">
                            <svg xmlns="http://www.w3.org/2000/svg" class="h-3.5 w-3.5" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                                <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2.5" d="M10.325 4.317c.426-1.756 2.924-1.756 3.35 0a1.724 1.724 0 002.573 1.066c1.543-.94 3.31.826 2.37 2.37a1.724 1.724 0 001.065 2.572c1.756.426 1.756 2.924 0 3.35a1.724 1.724 0 00-1.066 2.573c.94 1.543-.826 3.31-2.37 2.37a1.724 1.724 0 00-2.572 1.065c-.426 1.756-2.924 1.756-3.35 0a1.724 1.724 0 00-2.573-1.066c-1.543.94-3.31-.826-2.37-2.37a1.724 1.724 0 00-1.065-2.572c-1.756-.426-1.756-2.924 0-3.35a1.724 1.724 0 001.066-2.573c-.94-1.543.826-3.31-2.37-2.37.996.608 2.296.07 2.572-1.065z" />
                                <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2.5" d="M15 12a3 3 0 11-6 0 3 3 0 016 0z" />
                            </svg>
                        </button>
                        <button class="p-1 rounded hover:bg-rose-50 text-rose-400 transition-all" 
                            title="Remove Bot"
                            onclick="event.stopPropagation(); removeFromTradelist('${ticker}')">
                            <svg xmlns="http://www.w3.org/2000/svg" class="h-3.5 w-3.5" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                                <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2.5" d="M6 18L18 6M6 6l12 12" />
                            </svg>
                        </button>
                    </div>
                </div>

                <!-- 2nd Line: Active/Passive Status, Timeframe (TF), and Signal Indicators -->
                <div class="flex flex-col sm:flex-row sm:items-center justify-between gap-1.5 sm:gap-1 cursor-pointer" onclick="selectTicker('${ticker}')">
                    <div class="flex items-center gap-1.5 flex-wrap">
                        ${isGlobalRiskHalted
                ? '<span class="text-[0.5rem] font-bold px-1.5 py-0.5 rounded bg-rose-50 text-rose-600 border border-rose-200">GLOBAL HALT</span>'
                : (isPaused ? '<span class="text-[0.5rem] font-bold px-1.5 py-0.5 rounded bg-amber-50 text-amber-500 border border-amber-200">PAUSED</span>' : '<span class="text-[0.5rem] font-bold px-1.5 py-0.5 rounded bg-emerald-50 text-emerald-600 border border-emerald-100">ACTIVE</span>')}
                        ${isOptions
                ? `<span class="px-1.5 py-0.5 rounded bg-amber-500/10 text-amber-700 font-black text-[0.5rem] border border-amber-500/20 uppercase tracking-widest">Options</span>
                                   <span class="px-1.5 py-0.5 rounded bg-amber-50 text-amber-600 font-black text-[0.45rem] border border-amber-200 uppercase tracking-widest">${tickerSettings.options_type === 'puts_only' ? 'PUT' : (tickerSettings.options_type === 'both' ? 'Legacy Both Disabled' : 'CALL')}</span>
                                   <span class="px-1.5 py-0.5 rounded bg-amber-50 text-amber-600 font-black text-[0.45rem] border border-amber-200 uppercase tracking-widest">${tickerSettings.options_expiry_days || 7}D Exp</span>`
                : (isCryptoFlag
                    ? `<span class="px-1.5 py-0.5 rounded bg-fuchsia-500/10 text-fuchsia-700 font-black text-[0.5rem] border border-fuchsia-500/20 uppercase tracking-widest">Crypto</span>`
                    : `<span class="px-1.5 py-0.5 rounded bg-indigo-500/10 text-indigo-700 font-black text-[0.5rem] border border-indigo-500/20 uppercase tracking-widest">Stocks</span>`)
            }
                        <span class="px-1.5 py-0.5 rounded bg-indigo-50/50 text-indigo-500 font-mono font-black text-[0.55rem] border border-indigo-100/50 uppercase">${tfLabel}</span>
                        <button class="px-1.5 py-0.5 rounded bg-blue-50 hover:bg-blue-100 text-blue-600 font-black text-[0.5rem] border border-blue-200 transition-colors uppercase tracking-widest flex items-center gap-0.5" onclick="event.stopPropagation(); window.openBotCardChartPreview('${ticker}')">
                            <svg xmlns="http://www.w3.org/2000/svg" class="h-2.5 w-2.5" viewBox="0 0 20 20" fill="currentColor">
                                <path d="M2 11a1 1 0 011-1h2a1 1 0 011 1v5a1 1 0 01-1 1H3a1 1 0 01-1-1v-5zM8 7a1 1 0 011-1h2a1 1 0 011 1v9a1 1 0 01-1 1H9a1 1 0 01-1-1V7zM14 4a1 1 0 011-1h2a1 1 0 011 1v12a1 1 0 01-1 1h-2a1 1 0 01-1-1V4z" />
                            </svg>
                            OPEN CHART
                        </button>
                    </div>
                    
                    <div class="flex flex-col gap-1 sm:gap-0.5 items-start sm:items-end justify-center sm:ml-auto w-full sm:w-auto pt-1 sm:pt-0 border-t border-slate-50 sm:border-t-0">
                        <div class="flex items-center gap-1 flex-wrap sm:justify-end">
                            ${entryIndHtml}
                        </div>
                        <div class="flex items-center gap-1 flex-wrap sm:justify-end">
                            ${exitIndHtml}
                        </div>
                    </div>
                </div>

                <!-- Metadata Row: Runtime, Last Trade, and Trade Counts -->
                <div class="flex flex-col sm:flex-row sm:items-center justify-between gap-1 mt-1 text-[0.58rem] text-slate-500 font-semibold cursor-pointer" onclick="selectTicker('${ticker}')">
                    <div class="flex items-center gap-1 flex-wrap">
                        <span class="flex items-center gap-0.5 ${isPaused ? 'text-slate-400' : 'text-slate-600'}" title="Bot Running Time">
                            <svg xmlns="http://www.w3.org/2000/svg" class="h-3 w-3 ${isPaused ? 'text-slate-300' : 'text-indigo-400'}" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                                <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2.5" d="M12 8v4l3 3m6-3a9 9 0 11-18 0 9 9 0 0118 0z" />
                            </svg>
                            <span>${runtimeText}</span>
                        </span>
                        <span class="text-slate-300">•</span>
                        <span class="text-slate-400 font-normal" title="Last Trade Time">${lastTradeText}</span>
                    </div>
                    <div class="flex items-center self-start sm:self-auto">
                        ${tradeBadgeHtml}
                    </div>
                </div>

                ${pauseReasonHtml}

                <!-- 3rd Line: Position Details or Standing By Message -->
                ${positionHtml}
                ${scanLogHtml}
        `;
        container.appendChild(item);
    });

    // Deep link highlighting logic
    const urlParams = new URLSearchParams(window.location.search);
    const highlightTicker = urlParams.get('highlight');
    if (highlightTicker) {
        const allCards = document.querySelectorAll('#tradelistContainer .watchlist-item');
        for (const card of allCards) {
            if (card.dataset.ticker === highlightTicker.toUpperCase()) {
                card.scrollIntoView({ behavior: 'smooth', block: 'center' });
                card.classList.add('ring-2', 'ring-emerald-400', 'ring-offset-2');
                // Pulse highlight for 3 seconds then remove
                setTimeout(() => card.classList.remove('ring-2', 'ring-emerald-400', 'ring-offset-2'), 3000);
                // Also select the ticker to open its panel
                if (typeof selectTicker === 'function') {
                    selectTicker(highlightTicker.toUpperCase());
                }
                break;
            }
        }
        // Clean the URL to avoid re-highlighting on refresh
        window.history.replaceState({}, '', 'bots.html');
    }
}

let draggedItem = null;
let dragPlaceholder = null;
let originalSibling = null;

function handleDragStart(e) {
    draggedItem = this;
    originalSibling = this.nextSibling;
    e.dataTransfer.effectAllowed = 'move';
    e.dataTransfer.setData('text/plain', this.dataset.ticker);

    // Create a smooth visual placeholder
    dragPlaceholder = document.createElement('div');
    dragPlaceholder.className = 'drag-placeholder rounded-2xl border-2 border-dashed border-indigo-400 bg-indigo-500/5 transition-all duration-200';
    dragPlaceholder.style.height = `${this.offsetHeight}px`;
    dragPlaceholder.style.width = `${this.offsetWidth}px`;
    dragPlaceholder.style.marginBottom = window.getComputedStyle(this).marginBottom;

    // Allow drop directly on placeholder
    dragPlaceholder.addEventListener('dragover', (ev) => {
        ev.preventDefault();
        ev.dataTransfer.dropEffect = 'move';
    });
    dragPlaceholder.addEventListener('drop', handleDrop);

    // Hide the actual element from view but keep the ghost image
    setTimeout(() => {
        this.classList.add('hidden');
        if (this.parentNode) {
            this.parentNode.insertBefore(dragPlaceholder, this);
        }
    }, 0);
}

function handleDragEnd(e) {
    // If draggedItem is not null, it means handleDrop did not run (drag was cancelled or dropped invalidly)
    if (draggedItem) {
        draggedItem.classList.remove('hidden');
        // Restore to original position
        if (originalSibling && originalSibling.parentNode) {
            originalSibling.parentNode.insertBefore(draggedItem, originalSibling);
        } else {
            const container = document.getElementById('tradelistContainer');
            if (container) container.appendChild(draggedItem);
        }
        if (dragPlaceholder && dragPlaceholder.parentNode) {
            dragPlaceholder.remove();
        }
    }
    draggedItem = null;
    dragPlaceholder = null;
    originalSibling = null;
}

function handleDragOver(e) {
    if (e.preventDefault) {
        e.preventDefault();
    }
    e.dataTransfer.dropEffect = 'move';

    // Skip if we are dragging over the dragged item or the placeholder itself
    if (this === draggedItem || this === dragPlaceholder) return false;

    const bounding = this.getBoundingClientRect();
    const offset = bounding.y + (bounding.height / 2);

    // Smoothly insert placeholder in the target position
    if (e.clientY - offset > 0) {
        this.parentNode.insertBefore(dragPlaceholder, this.nextSibling);
    } else {
        this.parentNode.insertBefore(dragPlaceholder, this);
    }
    return false;
}

function handleDragEnter(e) {
    if (e.preventDefault) e.preventDefault();
}

function handleDragLeave(e) { }

async function handleDrop(e) {
    if (e.stopPropagation) e.stopPropagation();

    if (draggedItem && dragPlaceholder) {
        const container = document.getElementById('tradelistContainer');

        // Put the dragged item in its final position
        dragPlaceholder.parentNode.insertBefore(draggedItem, dragPlaceholder);
        dragPlaceholder.remove();
        draggedItem.classList.remove('hidden');

        // Build the new list of visible tickers directly from the DOM, ensuring no duplicates
        const newVisibleTickers = [];
        container.querySelectorAll('.watchlist-item').forEach(el => {
            const ticker = el.dataset.ticker;
            if (ticker && el !== dragPlaceholder && !newVisibleTickers.includes(ticker)) {
                newVisibleTickers.push(ticker);
            }
        });

        const currentFullList = window.lastBotsData?.tradelist || [];
        const originalVisibleSet = new Set(newVisibleTickers);

        // Reconstruct the full list preserving non-visible (hidden tab) items
        let visibleIdx = 0;
        const newFullList = [];
        for (const t of currentFullList) {
            if (originalVisibleSet.has(t)) {
                newFullList.push(newVisibleTickers[visibleIdx++]);
            } else {
                newFullList.push(t);
            }
        }

        // Sanity deduplication
        const finalFullList = [...new Set(newFullList)];

        container.classList.add('opacity-75', 'pointer-events-none');

        try {
            const headers = await getAuthHeaders();
            const response = await fetch(`${API_BASE}/api/bots/reorder`, {
                method: 'POST',
                headers: headers,
                body: JSON.stringify({ tradelist: finalFullList })
            });
            if (response.ok) {
                // Fetch the updated list from the backend
                await fetchBotsData({ force: true });
            }
        } catch (err) {
            console.error("Error reordering:", err);
        } finally {
            container.classList.remove('opacity-75', 'pointer-events-none');
            draggedItem = null;
            dragPlaceholder = null;
            originalSibling = null;
        }
    }
    return false;
}

function selectTicker(ticker) {
    if (window.getSelection().toString().length > 0) return; // Allow text selection without triggering navigation
    if (selectedTicker === ticker) return;
    selectedTicker = ticker;

    // Instantly swap the 'active' highlight on the DOM – no network call needed
    document.querySelectorAll('#tradelistContainer .watchlist-item').forEach(el => {
        // Each item's first onclick contains the ticker string
        const isMatch = el.innerHTML.includes(`selectTicker('${ticker}')`);
        if (isMatch) {
            el.classList.add('active');
        } else {
            el.classList.remove('active');
        }
    });

    // Load AI summary for the newly selected ticker (uses client cache if available)
    void fetchAIIndicator(ticker, currentBackendTf).catch(error => {
        if (!['AbortError', 'TimeoutError'].includes(error?.name)) {
            console.error('[ai-indicator] Unexpected ticker refresh failure:', error);
        }
    });
}

async function removeFromTradelist(ticker) {
    if (!confirm(`Warning: If you delete the bot for ${ticker}, any open position for this stock will be automatically liquidated, and all pending orders will be cancelled.\n\nAre you sure you want to proceed?`)) {
        return;
    }
    try {
        const headers = await getAuthHeaders();
        const response = await fetch(`${API_BASE}/api/tradelist/${ticker}?liquidate=true`, {
            method: 'DELETE',
            headers: headers
        });
        if (response.ok) fetchBotsData({ force: true });
    } catch (e) {
        console.error('Error removing from tradelist:', e);
    }
}

// ──────────────────────────────────────────────
// Render Trade Log
// ──────────────────────────────────────────────
let latestScanHistory = [];
let latestOrderHistory = [];
let latestPendingOrders = [];
let latestPositions = [];
let currentScanPage = 1;
let currentTradePage = 1;
let currentPendingPage = 1;
let currentPositionsPage = 1;
const LOG_PAGE_SIZE = 20;

function setLogTab(tab) {
    currentLogTab = tab;
    if (tab === 'all') currentScanPage = 1;
    else if (tab === 'trades') currentTradePage = 1;
    else if (tab === 'pending') currentPendingPage = 1;
    else currentPositionsPage = 1;

    const allBtn = document.getElementById('tabLogAll');
    const tradesBtn = document.getElementById('tabLogTrades');
    const pendingBtn = document.getElementById('tabLogPending');
    const positionsBtn = document.getElementById('tabLogPositions');

    if (allBtn && tradesBtn && pendingBtn) {
        allBtn.className = "px-4 py-1.5 text-[0.6rem] font-black uppercase rounded-lg transition-all " +
            (tab === 'all' ? "bg-white text-indigo-600 shadow-sm border border-indigo-100" : "text-indigo-400 hover:text-indigo-600");
        tradesBtn.className = "px-4 py-1.5 text-[0.6rem] font-black uppercase rounded-lg transition-all " +
            (tab === 'trades' ? "bg-white text-indigo-600 shadow-sm border border-indigo-100" : "text-indigo-400 hover:text-indigo-600");
        pendingBtn.className = "px-4 py-1.5 text-[0.6rem] font-black uppercase rounded-lg transition-all " +
            (tab === 'pending' ? "bg-white text-indigo-600 shadow-sm border border-indigo-100" : "text-indigo-400 hover:text-indigo-600");
        if (positionsBtn) {
            positionsBtn.className = "px-4 py-1.5 text-[0.6rem] font-black uppercase rounded-lg transition-all " +
                (tab === 'positions' ? "bg-white text-indigo-600 shadow-sm border border-indigo-100" : "text-indigo-400 hover:text-indigo-600");
        }
    }

    renderTradeLog(latestScanHistory, latestOrderHistory, latestPendingOrders, latestPositions);
    if (tab === 'all') fetchBotsActivity('scans', { force: true });
    if (tab === 'trades') fetchBotsActivity('orders', { force: true });
}

async function fetchBotsActivity(kind, options = {}) {
    const kindNorm = kind === 'orders' ? 'orders' : 'scans';
    const state = botsActivityState[kindNorm];
    const { force = false, bypassThrottle = false } = options;
    const refreshInterval = kindNorm === 'scans'
        ? SCAN_ACTIVITY_REFRESH_INTERVAL
        : ORDER_ACTIVITY_REFRESH_INTERVAL;
    if (!shouldRunBotsCardRefresh()) return false;
    if (state.promise) {
        if (!force) return state.promise;
        state.controller?.abort();
    }
    if (!force && !bypassThrottle && Date.now() - state.fetchedAt < refreshInterval) return false;

    const fetchId = ++state.fetchId;
    state.controller = new AbortController();
    state.promise = (async () => {
        const headers = await getAuthHeaders();
        const response = await fetchWithTimeout(
            `${API_BASE}/api/bots/activity?kind=${kindNorm}&limit=50`,
            { headers, cache: 'no-store', controller: state.controller },
            DASHBOARD_FETCH_TIMEOUT_MS
        );
        if (!response.ok) throw new Error(`Activity HTTP ${response.status}`);
        const payload = await response.json();
        if (payload.schema_version !== '1.0' || !Array.isArray(payload.activity)) {
            throw new Error('Unsupported Bots activity schema');
        }
        state.fetchedAt = Date.now();
        if (kindNorm === 'orders') latestOrderHistory = payload.activity;
        else latestScanHistory = payload.activity;
        renderTradeLog(latestScanHistory, latestOrderHistory, latestPendingOrders, latestPositions);
        return payload.activity;
    })();

    try {
        return await state.promise;
    } catch (error) {
        if (error.name !== 'AbortError') console.warn(`[bots] ${kindNorm} activity refresh failed:`, error);
        return false;
    } finally {
        if (fetchId === state.fetchId) {
            state.controller = null;
            state.promise = null;
        }
    }
}

async function cancelOrder(orderId) {
    if (!confirm("Are you sure you want to cancel this pending order?")) return;
    try {
        const headers = await getAuthHeaders();
        const response = await fetch(`${API_BASE}/api/cancel_order`, {
            method: 'POST',
            headers: headers,
            body: JSON.stringify({ order_id: orderId })
        });
        const result = await response.json();
        if (result.status === 'success') {
            alert(result.message || "Order cancelled successfully.");
            fetchBotsData({ force: true }); // reload
        } else {
            alert("Error: " + (result.message || "Could not cancel order."));
        }
    } catch (e) {
        console.error('Error cancelling order:', e);
        alert("Failed to contact the backend to cancel the order.");
    }
}

function renderTradeLog(scanHistory, orderHistory, pendingOrders, positions) {
    const tbody = document.getElementById('tradeLogBody');
    const thead = document.getElementById('tradeLogHeader');
    const noMsg = document.getElementById('noTradesMsg');
    if (!tbody) return;

    tbody.innerHTML = '';

    if (thead) {
        if (currentLogTab === 'all') {
            thead.innerHTML = `
                <tr class="border-b border-purple-200 text-purple-700 text-[10px] sm:text-xs uppercase tracking-tight">
                    <th class="py-2 pr-2">Time</th>
                    <th class="py-2 pr-2">Action</th>
                    <th class="py-2 pr-2">Ticker</th>
                    <th class="py-2 pr-2 text-center">TF</th>
                    <th class="py-2 pr-2">Price</th>
                    <th class="py-2 pr-2">Signals</th>
                    <th class="py-2 pr-2">Reason</th>
                </tr>
            `;
        } else if (currentLogTab === 'trades') {
            thead.innerHTML = `
                <tr class="border-b border-purple-200 text-purple-700 text-[10px] sm:text-xs uppercase tracking-tight">
                    <th class="py-2 pr-2">Time</th>
                    <th class="py-2 pr-2">Ticker</th>
                    <th class="py-2 pr-2">Side</th>
                    <th class="py-2 pr-2 text-center">Qty</th>
                    <th class="py-2 pr-2 text-center">Avg Price</th>
                    <th class="py-2 pr-2 text-center">Cost</th>
                    <th class="py-2 pr-2 text-center">P/L</th>
                    <th class="py-2 pr-2">Summary</th>
                </tr>
            `;
        } else if (currentLogTab === 'pending') {
            thead.innerHTML = `
                <tr class="border-b border-purple-200 text-purple-700 text-[10px] sm:text-xs uppercase tracking-tight">
                    <th class="py-2 pr-2">Time</th>
                    <th class="py-2 pr-2">Ticker</th>
                    <th class="py-2 pr-2">Side</th>
                    <th class="py-2 pr-2 text-center">TIF</th>
                    <th class="py-2 pr-2 text-center">Qty</th>
                    <th class="py-2 pr-2 text-center">Target</th>
                    <th class="py-2 pr-2 text-center">Value</th>
                    <th class="py-2 pr-2">Summary</th>
                    <th class="py-2 pr-2 text-center">Action</th>
                </tr>
            `;
        } else if (currentLogTab === 'positions') {
            thead.innerHTML = `
                <tr class="border-b border-purple-200 text-purple-700 text-[10px] sm:text-xs uppercase tracking-tight">
                    <th class="py-2 pr-2">Ticker</th>
                    <th class="py-2 pr-2 text-center">Qty</th>
                    <th class="py-2 pr-2 text-center">Avg Price</th>
                    <th class="py-2 pr-2 text-center">Current</th>
                    <th class="py-2 pr-2 text-center">Value</th>
                    <th class="py-2 pr-2 text-center">Total P/L</th>
                    <th class="py-2 pr-2 text-center">Daily Chg</th>
                </tr>
            `;
        }
    }

    let items = [];
    let currentPage = 1;

    if (currentLogTab === 'all') {
        items = scanHistory || [];
        currentPage = currentScanPage;
    } else if (currentLogTab === 'trades') {
        items = orderHistory || [];
        currentPage = currentTradePage;
    } else if (currentLogTab === 'pending') {
        items = pendingOrders || [];
        currentPage = currentPendingPage;
    } else if (currentLogTab === 'positions') {
        items = positions || [];
        currentPage = currentPositionsPage;
    }

    if (!items || items.length === 0) {
        if (noMsg) {
            noMsg.style.display = 'block';
            if (currentLogTab === 'all') {
                noMsg.textContent = "Waiting for first scan...";
            } else if (currentLogTab === 'trades') {
                noMsg.textContent = "No order history found.";
            } else if (currentLogTab === 'pending') {
                noMsg.textContent = "No pending/working orders.";
            } else {
                noMsg.textContent = "No open positions found.";
            }
        }
        updatePaginationUI(0, 1);
        return;
    }
    if (noMsg) noMsg.style.display = 'none';

    const totalPages = Math.ceil(items.length / LOG_PAGE_SIZE) || 1;
    const start = (currentPage - 1) * LOG_PAGE_SIZE;
    const end = start + LOG_PAGE_SIZE;
    const paginated = items.slice(start, end);

    updatePaginationUI(totalPages, currentPage);

    paginated.forEach(item => {
        const row = document.createElement('tr');
        row.className = 'border-b border-purple-100 fade-in hover:bg-purple-50/30 transition-colors';

        if (currentLogTab === 'all') {
            const rawReason = String(item.reason || '');
            // Basic cleanup of generic prefixes
            let cleanReason = rawReason.replace(/Hybrid HOLD:/gi, 'Hybrid Waiting:').replace(/^HOLD:/gi, 'Waiting:');
            // Enhance Hybrid waiting reason with mode and structure status
            if (cleanReason.toLowerCase().startsWith('hybrid waiting:')) {
                const mode = String(item.buy_mode || item.sell_mode || '').toUpperCase();
                let structLabel = 'structure';
                if (mode.includes('ATR')) structLabel = 'ATR';
                else if (mode.includes('PCT')) structLabel = 'Pct';
                else if (mode.includes('PIVOT')) structLabel = 'Pivot';
                else if (mode.includes('HIGH_LOW') || mode.includes('HL')) structLabel = 'H/L';
                else if (mode.includes('SWING')) structLabel = 'Swing';

                if (item.structure_ok) {
                    cleanReason = `${mode} Waiting: ${structLabel} confirmed, only waiting for the indicator signals.`;
                } else {
                    cleanReason = `${mode} Waiting: waiting for indicator signals and ${structLabel} confirmation.`;
                }
            }
            const isCooldown = cleanReason.includes('Cooldown Active');
            const isSignalReset = cleanReason.includes('Signal Reset Active');
            const isWaitingEntry = !item.exit_monitoring && (item.action === 'HOLD' || cleanReason.toLowerCase().includes('waiting') || cleanReason.toLowerCase().includes('entry'));

            const actionLabel = isCooldown
                ? 'COOLDOWN'
                : (isSignalReset
                    ? 'RESET'
                    : (isWaitingEntry ? 'WAITING' : item.action));

            const actionColor = item.action === 'BUY'
                ? 'text-emerald-600 font-bold'
                : item.action === 'SELL'
                    ? 'text-red-500 font-bold'
                    : isCooldown
                        ? 'text-purple-600 font-bold'
                        : isSignalReset
                            ? 'text-indigo-600 font-bold'
                            : (isWaitingEntry ? 'text-blue-600 font-medium' : 'text-purple-500 font-medium');

            let signalBadge = item.exit_monitoring && item.exit_signal_label
                ? item.exit_signal_label.replace(/Exit\s*/gi, '').trim()
                : item.bullish_count !== undefined
                    ? `${item.bullish_count}B / ${item.bearish_count}S`
                    : '—';

            // For Hybrid strategies (Indicator + Structural/ATR), display single compact badge: e.g. "2B/1S + ATR"
            const modeStr = String(item.buy_mode || item.sell_mode || cleanReason).toLowerCase();
            if (modeStr.includes('hybrid') && !item.exit_monitoring && item.bullish_count !== undefined) {
                let structLabel = 'ATR';
                if (modeStr.includes('pct')) structLabel = 'Pct';
                else if (modeStr.includes('pivot')) structLabel = 'Pivot';
                else if (modeStr.includes('high_low') || modeStr.includes('hl')) structLabel = 'H/L';
                else if (modeStr.includes('swing')) structLabel = 'Swing';

                const isStructOk = item.structure_ok || cleanReason.toLowerCase().includes('structure confirmed');
                const badgeText = isStructOk
                    ? `${item.bullish_count}B/${item.bearish_count}S + ${structLabel} (OK)`
                    : `${item.bullish_count}B/${item.bearish_count}S + ${structLabel}`;

                const badgeColor = isStructOk
                    ? 'bg-emerald-100 text-emerald-800 border border-emerald-200'
                    : 'bg-purple-100 text-purple-700 border border-purple-200';

                signalBadge = `<span class="px-1.5 py-0.5 rounded ${badgeColor} font-mono text-[0.58rem] sm:text-[0.62rem] whitespace-nowrap">${badgeText}</span>`;
            } else {
                signalBadge = `<span class="px-1.5 py-0.5 rounded bg-purple-100 text-purple-700 font-mono text-[0.6rem] whitespace-nowrap">${signalBadge}</span>`;
            }

            row.innerHTML = `
                <td class="py-2 pr-2 text-purple-600 text-[10px] sm:text-xs whitespace-normal leading-tight max-w-[65px] sm:max-w-none">${formatLocalTime(item.time)}</td>
                <td class="py-2 pr-2 ${actionColor} text-[10px] sm:text-xs">${actionLabel}</td>
                <td class="py-2 pr-2 text-indigo-950 font-bold text-[10px] sm:text-xs">${item.ticker}</td>
                <td class="py-2 pr-2 text-center">
                    <span class="px-1.5 py-0.5 rounded-md bg-indigo-50 text-indigo-500 font-black text-[0.55rem] border border-indigo-100">${item.timeframe || '5M'}</span>
                </td>
                <td class="py-2 pr-2 text-indigo-700 font-medium text-[10px] sm:text-xs">${item.price}</td>
                <td class="py-2 pr-2 text-[10px] sm:text-xs flex items-center gap-1 flex-wrap">${signalBadge}</td>
                <td class="py-2 pr-2 text-purple-600 italic text-[10px] sm:text-xs min-w-[200px] sm:min-w-[280px] whitespace-normal leading-snug" title="${cleanReason}">${cleanReason}</td>
            `;
        } else if (currentLogTab === 'trades') {
            const sideColor = item.side === 'buy' ? 'text-emerald-600 font-bold' : 'text-rose-500 font-bold';

            // Dynamic precision price formatting
            const cleanSym = String(item.symbol).toUpperCase();
            const isCrypto = cleanSym.includes("USD") || cleanSym.includes("USDT") || cleanSym.includes("USDC");
            const avgPriceStr = item.filled_avg_price ? formatAssetPrice(item.filled_avg_price, isCrypto) : '0.00';
            const totalCostStr = item.total_cost ? Number(item.total_cost).toFixed(2) : '0.00';

            // Realized P/L cell formatting
            let plHtml = '<td class="py-2.5 pr-4 text-center text-slate-300 text-[11px]">—</td>';
            if (item.pl !== undefined && item.pl !== null) {
                const plVal = Number(item.pl);
                const plPct = Number(item.pl_pct || 0.0);
                const plColor = plVal >= 0 ? 'text-emerald-500 font-bold' : 'text-red-500 font-bold';
                const sign = plVal >= 0 ? '+' : '';
                plHtml = `
                    <td class="py-2.5 pr-4 text-center ${plColor} text-[11px]">
                        <div>${sign}$${plVal.toFixed(2)}</div>
                        <div class="text-[9px] opacity-80 font-semibold">${sign}${plPct.toFixed(2)}%</div>
                    </td>
                `;
            } else if (item.side === 'buy') {
                plHtml = '<td class="py-2.5 pr-4 text-center text-indigo-400 font-black text-[9px] uppercase tracking-wider">Entry</td>';
            }

            const aiReason = item.reason || 'AI Strategy: Execution criteria satisfied.';

            row.innerHTML = `
                <td class="py-2 pr-2 text-purple-600 text-[10px] sm:text-xs whitespace-normal leading-tight max-w-[65px] sm:max-w-none">${formatLocalTime(item.created_at)}</td>
                <td class="py-2 pr-2 text-indigo-950 font-bold text-[10px] sm:text-xs">${item.symbol}</td>
                <td class="py-2 pr-2 text-[10px] sm:text-xs ${sideColor} uppercase">${item.side}</td>
                <td class="py-2 pr-2 text-center text-[10px] sm:text-xs font-bold text-indigo-900">${item.filled_qty ? item.filled_qty.toFixed(4) : '0.0000'}</td>
                <td class="py-2 pr-2 text-center text-[10px] sm:text-xs text-indigo-700 font-medium">$${avgPriceStr}</td>
                <td class="py-2 pr-2 text-center text-[10px] sm:text-xs text-indigo-900 font-bold">$${totalCostStr}</td>
                ${plHtml}
                <td class="py-2 pr-2 text-purple-600 italic text-[10px] sm:text-xs min-w-[200px] sm:min-w-[280px] whitespace-normal leading-snug" title="${aiReason}">${aiReason}</td>
            `;
        } else if (currentLogTab === 'pending') {
            const sideColor = item.side === 'buy' ? 'text-emerald-600 font-bold' : 'text-rose-500 font-bold';

            // Dynamic precision price formatting
            const cleanSym = String(item.symbol).toUpperCase();
            const isCrypto = cleanSym.includes("USD") || cleanSym.includes("USDT") || cleanSym.includes("USDC");
            const priceDecimals = (isCrypto || (item.limit_price && item.limit_price < 10)) ? 4 : 2;

            const limitPriceVal = item.limit_price ? Number(item.limit_price) : (item.stop_price ? Number(item.stop_price) : 0);
            const limitPriceStr = limitPriceVal > 0 ? `$${limitPriceVal.toFixed(priceDecimals)}` : 'Market';
            const totalValueStr = item.total_cost ? `$${Number(item.total_cost).toFixed(2)}` : '—';

            const tifBadge = `<span class="px-1.5 py-0.5 rounded-md bg-indigo-50 text-indigo-500 font-black text-[0.55rem] border border-indigo-100 uppercase">${item.time_in_force || 'GTC'}</span>`;
            const aiReason = item.reason || 'AI Strategy: Order pending execution.';

            row.innerHTML = `
                <td class="py-2 pr-2 text-purple-600 text-[10px] sm:text-xs whitespace-normal leading-tight max-w-[65px] sm:max-w-none">${formatLocalTime(item.created_at)}</td>
                <td class="py-2 pr-2 text-indigo-950 font-bold text-[10px] sm:text-xs">${item.symbol}</td>
                <td class="py-2 pr-2 text-[10px] sm:text-xs ${sideColor} uppercase">${item.side}</td>
                <td class="py-2 pr-2 text-center">${tifBadge}</td>
                <td class="py-2 pr-2 text-center text-[10px] sm:text-xs font-bold text-indigo-900">${item.qty ? item.qty.toFixed(4) : '0.0000'}</td>
                <td class="py-2 pr-2 text-center text-[10px] sm:text-xs text-indigo-700 font-medium">${limitPriceStr}</td>
                <td class="py-2 pr-2 text-center text-[10px] sm:text-xs text-indigo-900 font-bold">${totalValueStr}</td>
                <td class="py-2 pr-2 text-purple-600 italic text-[10px] sm:text-xs min-w-[200px] sm:min-w-[280px] whitespace-normal leading-snug" title="${aiReason}">${aiReason}</td>
                <td class="py-2 pr-2 text-center">
                    <button onclick="cancelOrder('${item.id}')"
                        class="px-2 py-1 bg-rose-500 text-white rounded hover:bg-rose-600 transition-all font-black text-[9px] uppercase tracking-wider">
                        Cancel
                    </button>
                </td>
            `;
        } else if (currentLogTab === 'positions') {
            const isCrypto = String(item.symbol).toUpperCase().includes("USD");
            const qtyStr = Number(item.qty) % 1 === 0 ? Number(item.qty).toString() : Number(item.qty) < 1 ? Number(item.qty).toFixed(6) : Number(item.qty).toFixed(4);
            const formatVal = (v) => formatAssetPrice(v, isCrypto);
            const avgPriceStr = `$${formatVal(item.avg_price || 0)}`;
            const currentPriceStr = `$${formatVal(item.current_price || 0)}`;
            const marketValueStr = `$${Number(item.market_value || 0).toFixed(2)}`;

            const plVal = Number(item.unrealized_pl || 0);
            const plPct = Number(item.unrealized_pl_pct || 0);
            const plColor = plVal >= 0 ? 'text-emerald-500 font-bold' : 'text-red-500 font-bold';
            const sign = plVal >= 0 ? '+' : '';

            const dayPlVal = Number(item.unrealized_intraday_pl || 0);
            const dayPlPct = Number(item.unrealized_intraday_plpc || 0);
            const dayPlColor = dayPlVal >= 0 ? 'text-emerald-500 font-bold' : 'text-red-500 font-bold';
            const daySign = dayPlVal >= 0 ? '+' : '';

            row.innerHTML = `
                <td class="py-2 pr-2 text-indigo-950 font-bold text-[10px] sm:text-xs">${item.symbol}</td>
                <td class="py-2 pr-2 text-center text-[10px] sm:text-xs font-bold text-indigo-900">${qtyStr}</td>
                <td class="py-2 pr-2 text-center text-[10px] sm:text-xs text-indigo-700 font-medium">${avgPriceStr}</td>
                <td class="py-2 pr-2 text-center text-[10px] sm:text-xs text-indigo-700 font-medium">${currentPriceStr}</td>
                <td class="py-2 pr-2 text-center text-[10px] sm:text-xs text-indigo-900 font-bold">${marketValueStr}</td>
                <td class="py-2 pr-2 text-center ${plColor} text-[10px] sm:text-xs">
                    <div>${sign}$${plVal.toFixed(2)}</div>
                    <div class="text-[9px] opacity-80 font-semibold">${sign}${plPct.toFixed(2)}%</div>
                </td>
                <td class="py-2 pr-2 text-center ${dayPlColor} text-[10px] sm:text-xs">
                    <div>${daySign}$${dayPlVal.toFixed(2)}</div>
                    <div class="text-[9px] opacity-80 font-semibold">${daySign}${dayPlPct.toFixed(2)}%</div>
                </td>
            `;
        }

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
    } else if (currentLogTab === 'pending') {
        const total = Math.ceil(latestPendingOrders.length / LOG_PAGE_SIZE) || 1;
        currentPendingPage = Math.max(1, Math.min(total, currentPendingPage + delta));
    } else {
        const total = Math.ceil(latestPositions.length / LOG_PAGE_SIZE) || 1;
        currentPositionsPage = Math.max(1, Math.min(total, currentPositionsPage + delta));
    }
    renderTradeLog(latestScanHistory, latestOrderHistory, latestPendingOrders, latestPositions);
}

// ──────────────────────────────────────────────
// Position Sizing Panel
// ──────────────────────────────────────────────


// ──────────────────────────────────────────────
// Ticker Settings Modal
// ──────────────────────────────────────────────
// ──────────────────────────────────────────────
// Unified Bot Strategy Modal
// ──────────────────────────────────────────────
let currentStrategySymbol = null;
window.currentStrategySymbol = null;
let strategyModalMode = 'deploy'; // 'deploy' or 'edit'
let strategyDraftIndicatorOverrides = {};
let botChartPreviewState = {
    open: false,
    mode: 'view',
    source: 'card',
    ticker: null,
    tradingMode: 'regular',
    settings: {},
    signals: {},
    priceHistory: [],
    volumeProfile: {},
    previewMeta: {},
};
let botChartPreviewIndicatorSettingsContext = false;

function getTimeframeMinutes(tf) {
    switch (tf) {
        case '1Min': return 1;
        case '5Min': return 5;
        case '15Min': return 15;
        case '30Min': return 30;
        case '1Hour': return 60;
        case '4Hour': return 240;
        case '1Day': return 1440;
        default: return 60;
    }
}

function clonePlainObject(value) {
    if (!value || typeof value !== 'object') return {};
    return JSON.parse(JSON.stringify(value));
}

function stablePreviewStringify(value) {
    if (value === null || typeof value !== 'object') return JSON.stringify(value);
    if (Array.isArray(value)) {
        return `[${value.map(stablePreviewStringify).join(',')}]`;
    }
    return `{${Object.keys(value).sort().map(key => `${JSON.stringify(key)}:${stablePreviewStringify(value[key])}`).join(',')}}`;
}

function hashStringDjb2(input) {
    let hash = 5381;
    const str = String(input || '');
    for (let i = 0; i < str.length; i += 1) {
        hash = ((hash << 5) + hash) + str.charCodeAt(i);
        hash |= 0;
    }
    return (hash >>> 0).toString(36);
}

function normalizeBotChartPreviewCacheSettings(settings = {}) {
    const normalized = clonePlainObject(settings);
    delete normalized.last_signal_time;
    delete normalized.last_trade_time;
    delete normalized.runtime_state;
    if (Array.isArray(normalized.indicators)) {
        normalized.indicators = [...normalized.indicators].sort();
    }
    return normalized;
}

function getBotChartPreviewCacheKey(ticker, settings = {}) {
    const cleanTicker = (ticker || '').toUpperCase().trim();
    const normalizedSettings = normalizeBotChartPreviewCacheSettings(settings);
    const timeframe = normalizedSettings.timeframe || '1Hour';
    const session = isCrypto(cleanTicker)
        ? '24h'
        : (normalizedSettings.market_session || botChartPreviewSession || 'regular');
    const mode = normalizedSettings.options_trading ? 'options' : 'regular';
    const settingsHash = hashStringDjb2(stablePreviewStringify(normalizedSettings));
    return `bot-preview|${cleanTicker}|${timeframe}|${mode}|${session}|${settingsHash}|${BOT_CHART_CACHE_VERSION}`;
}

function openBotChartIndexedDB() {
    if (typeof indexedDB === 'undefined') return Promise.resolve(null);
    if (botChartPreviewIndexedDbPromise) return botChartPreviewIndexedDbPromise;
    botChartPreviewIndexedDbPromise = new Promise(resolve => {
        const request = indexedDB.open(BOT_CHART_IDB_NAME, BOT_CHART_IDB_VERSION);
        request.onupgradeneeded = event => {
            const db = event.target.result;
            if (!db.objectStoreNames.contains(BOT_CHART_IDB_STORE)) {
                const store = db.createObjectStore(BOT_CHART_IDB_STORE, { keyPath: 'key' });
                store.createIndex('fetchedAt', 'fetchedAt', { unique: false });
            }
        };
        request.onsuccess = event => resolve(event.target.result);
        request.onerror = () => {
            console.warn('[bot-chart] IndexedDB cache unavailable:', request.error);
            resolve(null);
        };
        request.onblocked = () => {
            console.warn('[bot-chart] IndexedDB cache upgrade blocked by another tab.');
            resolve(null);
        };
    });
    return botChartPreviewIndexedDbPromise;
}

async function readBotChartPreviewCache(key) {
    const memoryHit = botChartPreviewPayloadCache.get(key);
    if (memoryHit) return memoryHit;
    const db = await openBotChartIndexedDB();
    if (!db) return null;
    return new Promise(resolve => {
        try {
            const tx = db.transaction(BOT_CHART_IDB_STORE, 'readonly');
            const request = tx.objectStore(BOT_CHART_IDB_STORE).get(key);
            request.onsuccess = () => {
                const record = request.result || null;
                if (record) botChartPreviewPayloadCache.set(key, record);
                resolve(record);
            };
            request.onerror = () => {
                console.warn('[bot-chart] Failed reading IndexedDB cache:', request.error);
                resolve(null);
            };
        } catch (e) {
            console.warn('[bot-chart] Failed opening IndexedDB read transaction:', e);
            resolve(null);
        }
    });
}

async function pruneBotChartPreviewCache(maxEntries = 40) {
    const db = await openBotChartIndexedDB();
    if (!db) return;
    return new Promise(resolve => {
        try {
            const tx = db.transaction(BOT_CHART_IDB_STORE, 'readwrite');
            const store = tx.objectStore(BOT_CHART_IDB_STORE);
            const request = store.getAll();
            request.onsuccess = () => {
                const rows = (Array.isArray(request.result) ? request.result : [])
                    .filter(row => String(row?.key || '').startsWith('bot-preview|'));
                rows
                    .sort((a, b) => Number(b.fetchedAt || 0) - Number(a.fetchedAt || 0))
                    .slice(maxEntries)
                    .forEach(row => {
                        if (row?.key) {
                            store.delete(row.key);
                            botChartPreviewPayloadCache.delete(row.key);
                        }
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

async function saveBotChartPreviewCache(key, payload, ticker, settings) {
    if (!key || !payload) return false;
    const db = await openBotChartIndexedDB();
    if (!db) return false;
    const record = {
        key,
        payload,
        ticker: (ticker || '').toUpperCase().trim(),
        timeframe: settings?.timeframe || '1Hour',
        fetchedAt: Date.now(),
        version: BOT_CHART_CACHE_VERSION,
        cacheType: 'bot-preview',
    };
    botChartPreviewPayloadCache.set(key, record);
    const writeRecord = () => new Promise((resolve, reject) => {
        try {
            const tx = db.transaction(BOT_CHART_IDB_STORE, 'readwrite');
            tx.objectStore(BOT_CHART_IDB_STORE).put(record);
            tx.oncomplete = () => resolve(true);
            tx.onerror = () => reject(tx.error);
            tx.onabort = () => reject(tx.error);
        } catch (e) {
            reject(e);
        }
    });
    try {
        await writeRecord();
        pruneBotChartPreviewCache().catch(() => { });
        return true;
    } catch (e) {
        await pruneBotChartPreviewCache(12);
        try {
            await writeRecord();
            return true;
        } catch (retryErr) {
            console.warn('[bot-chart] IndexedDB cache write skipped:', retryErr);
            return false;
        }
    }
}

function getSavedBotSettings(symbol) {
    const ticker = (symbol || '').toUpperCase();
    const slim = (window.lastBotsData?.ticker_settings || {})[ticker] || {};
    const full = botFullSettingsCache.get(ticker) || {};
    return clonePlainObject({ ...slim, ...full });
}

async function fetchBotSettingsLazy(symbol) {
    const ticker = (symbol || '').toUpperCase();
    if (!ticker) return {};
    const existing = getSavedBotSettings(ticker);
    if (botFullSettingsCache.has(ticker)) return existing;
    try {
        const headers = await getAuthHeaders();
        const res = await fetch(`${API_BASE}/api/bots/${encodeURIComponent(ticker)}/settings`, { headers, cache: 'no-store' });
        if (res.ok) {
            const data = await res.json();
            const fetchedSettings = data.settings || {};
            botFullSettingsCache.set(ticker, clonePlainObject(fetchedSettings));
            return getSavedBotSettings(ticker);
        }
    } catch (err) {
        console.warn(`[bots] Could not lazy-fetch settings for ${ticker}:`, err);
    }
    return existing;
}

function setStrategyDraftIndicatorOverrides(overrides = {}) {
    strategyDraftIndicatorOverrides = clonePlainObject(overrides);
}

function getStrategyDraftIndicatorOverrides() {
    return clonePlainObject(strategyDraftIndicatorOverrides);
}

function formatMinutes(min) {
    if (min < 60) return min + " min";
    const hrs = min / 60;
    if (hrs < 24) {
        return hrs + (hrs === 1 ? " hour" : " hours");
    }
    const days = hrs / 24;
    return days + (days === 1 ? " day" : " days");
}

function updateDefaultCooldownLabel() {
    const modes = ['regular', 'options'];
    modes.forEach(mode => {
        const tfEl = document.getElementById(`strategyTimeframe_${mode}`);
        if (!tfEl) return;
        const minutes = getTimeframeMinutes(tfEl.value) * 4;
        const defaultOption = document.querySelector(`#strategyCooldownMinutes_${mode} option[value="default"]`);
        if (defaultOption) {
            defaultOption.textContent = `Default (${formatMinutes(minutes)})`;
        }
    });
}

function getCooldownMinutesValue(mode = 'regular') {
    // AI Autopilot has no separate cooldown controls in the form. Return the
    // backend default instead of dereferencing a missing DOM element.
    const selectEl = document.getElementById(`strategyCooldownMinutes_${mode}`);
    if (!selectEl) return "default";

    const val = selectEl.value;
    if (val === 'default') return "default";
    if (val === 'custom') {
        return parseInt(document.getElementById(`strategyCooldownCustomMinutes_${mode}`)?.value) || 60;
    }
    return parseInt(val) || 60;
}

function getCoreTradeGuardSettings(mode = 'regular') {
    const isOptions = mode === 'options';
    const shared = {
        post_stop_stabilization_bars: Math.max(0, parseInt(document.getElementById(`strategyPostStopBars_${mode}`)?.value || '3', 10)),
        max_consecutive_losses: 3,
        enable_liquidity_filter: true,
    };
    if (isOptions) {
        return {
            ...shared,
            options_max_entry_spread_pct: parseFloat(document.getElementById('strategyOptionsMaxSpreadPct')?.value || '5'),
            enable_options_market_open_filter: true,
            options_market_open_blackout_minutes: Math.max(0, parseInt(document.getElementById('strategyOptionsOpenBlackoutMinutes')?.value || '15', 10)),
            options_limit_aggression: 0.25,
            options_entry_order_timeout_seconds: 20,
            options_exit_order_timeout_seconds: 10,
        };
    }
    return {
        ...shared,
        stock_max_spread_pct: parseFloat(document.getElementById('strategyStockMaxSpreadPct')?.value || '0.25'),
        crypto_max_spread_pct: parseFloat(document.getElementById('strategyCryptoMaxSpreadPct')?.value || '0.50'),
    };
}

function getDefaultSignalThreshold(type = 'buy') {
    const key = type === 'sell' ? 'MIN_BEARISH_SIGNALS' : 'MIN_BULLISH_SIGNALS';
    return parseInt(INDICATOR_DEFAULTS[key] || 4, 10) || 4;
}

function normalizeSignalThreshold(value, checkedCount, type = 'buy', minVal = 1) {
    if (checkedCount <= 0 && minVal === 0) return 0;
    if (checkedCount <= 0) return 0;

    const defaultValue = getDefaultSignalThreshold(type);
    let normalized = parseInt(value, 10);

    if (!Number.isFinite(normalized) || (minVal > 0 && normalized <= 0)) {
        normalized = Math.min(defaultValue, checkedCount);
    }

    return Math.max(minVal, Math.min(normalized, checkedCount));
}

function getBotWorkspaceShownSet(mode = window.activeTradingMode || 'regular', ticker = currentStrategySymbol) {
    const contextLabel = `bots-${mode === 'options' ? 'options' : 'regular'}`;
    const fallback = (window.STANDARD_INDICATOR_NAMES || []).slice(0, 12);
    const shown = window.IndicatorWorkspace?.getShownForScope?.(contextLabel, ticker)
        || window.IndicatorWorkspace?.getShown?.(contextLabel)
        || fallback;
    return new Set(shown);
}

function isBotIndicatorAllowed(indicatorName, mode = window.activeTradingMode || 'regular', ticker = currentStrategySymbol) {
    const standardNames = window.STANDARD_INDICATOR_NAMES || [];
    return !standardNames.includes(indicatorName) || getBotWorkspaceShownSet(mode, ticker).has(indicatorName);
}

function getSelectedStrategyIndicators(mode = window.activeTradingMode || 'regular') {
    const container = document.getElementById(`indicatorChecksContainer_${mode}`);
    if (!container) return [];
    const selected = [];
    const seen = new Set();
    container.querySelectorAll('.strategy-indicator-check:checked:not(:disabled)').forEach(checkbox => {
        const name = String(checkbox.value || '').trim();
        if (!name || seen.has(name) || !isBotIndicatorAllowed(name, mode, currentStrategySymbol)) return;
        seen.add(name);
        selected.push(name);
    });
    return selected;
}
window.getSelectedStrategyIndicators = getSelectedStrategyIndicators;

const botWorkspaceIndicatorSaveTimers = new Map();

function queueBotWorkspaceIndicatorRemoval(ticker, mode, shown) {
    const normalizedTicker = String(ticker || '').trim().toUpperCase();
    const saved = window.lastBotsData?.ticker_settings?.[normalizedTicker];
    if (!normalizedTicker || !saved || !Array.isArray(saved.indicators)) return;
    const savedMode = saved.options_trading ? 'options' : 'regular';
    if (savedMode !== mode) return;

    const standardNames = new Set(window.STANDARD_INDICATOR_NAMES || []);
    const nextIndicators = saved.indicators.filter(name => !standardNames.has(name) || shown.has(name));
    if (nextIndicators.length === saved.indicators.length) return;

    saved.indicators = nextIndicators;
    saved.min_buy_signals = normalizeSignalThreshold(saved.min_buy_signals, nextIndicators.length, 'buy');
    saved.min_sell_signals = normalizeSignalThreshold(saved.min_sell_signals, nextIndicators.length, 'sell');

    clearTimeout(botWorkspaceIndicatorSaveTimers.get(normalizedTicker));
    const saveTimer = setTimeout(async () => {
        botWorkspaceIndicatorSaveTimers.delete(normalizedTicker);
        try {
            const headers = await getAuthHeaders();
            const response = await fetch(`${API_BASE}/api/settings/ticker`, {
                method: 'POST',
                headers,
                body: JSON.stringify({
                    ticker: normalizedTicker,
                    settings: {
                        indicators: [...nextIndicators],
                        min_buy_signals: saved.min_buy_signals,
                        min_sell_signals: saved.min_sell_signals,
                    },
                }),
            });
            if (!response.ok) throw new Error(`HTTP ${response.status}`);
        } catch (error) {
            console.warn(`[indicator-workspace] Could not persist hidden indicators for ${normalizedTicker}.`, error);
        }
    }, 150);
    botWorkspaceIndicatorSaveTimers.set(normalizedTicker, saveTimer);
}

function reconcileBotIndicatorsWithWorkspace(event) {
    const detail = event?.detail || {};
    if (!String(detail.contextLabel || '').startsWith('bots-')) return;
    const mode = detail.contextLabel.endsWith('options') ? 'options' : 'regular';
    const shown = new Set(detail.shown || getBotWorkspaceShownSet(mode, detail.ticker));
    const container = document.getElementById(`indicatorChecksContainer_${mode}`);
    container?.querySelectorAll('.strategy-indicator-check:checked').forEach(checkbox => {
        const name = String(checkbox.value || '').trim();
        if ((window.STANDARD_INDICATOR_NAMES || []).includes(name) && !shown.has(name)) {
            checkbox.checked = false;
        }
    });
    updateStrategySignalLabels(mode);
    queueBotWorkspaceIndicatorRemoval(detail.ticker || currentStrategySymbol, mode, shown);

    if (botChartPreviewState?.open && botChartPreviewState.settings) {
        const previewMode = botChartPreviewState.settings.options_trading || botChartPreviewState.tradingMode === 'options'
            ? 'options'
            : 'regular';
        if (previewMode === mode) {
            const indicators = getEffectivePreviewIndicators(
                botChartPreviewState.signals,
                botChartPreviewState.settings
            );
            botChartPreviewState.settings.indicators = indicators;
            botChartPreviewState.settings.enabled_indicators = [...indicators];
            botChartPreviewState.settings.min_buy_signals = normalizeSignalThreshold(
                botChartPreviewState.settings.min_buy_signals,
                indicators.length,
                'buy'
            );
            botChartPreviewState.settings.min_sell_signals = normalizeSignalThreshold(
                botChartPreviewState.settings.min_sell_signals,
                indicators.length,
                'sell'
            );
            renderBotChartPreviewSignals(botChartPreviewState.signals, botChartPreviewState.settings);
            updateBotChartPreviewChart(botChartPreviewState.priceHistory, botChartPreviewState.settings);
        }
    }
}

window.addEventListener('indicator-workspace:selection-change', reconcileBotIndicatorsWithWorkspace);

function getSwingLookbackDaysValue(mode = 'regular', kind = 'entry') {
    const selectEl = document.getElementById(`strategySwing${kind === 'entry' ? 'Entry' : 'Exit'}Lookback_${mode}`);
    if (!selectEl) return 20;
    if (selectEl.value === 'custom') {
        return parseInt(document.getElementById(`strategySwing${kind === 'entry' ? 'Entry' : 'Exit'}LookbackCustom_${mode}`)?.value || 20) || 20;
    }
    return parseInt(selectEl.value) || 20;
}

function updateSwingLookbackFields(mode = 'regular', kind = 'entry') {
    const prefix = kind === 'entry' ? 'Entry' : 'Exit';
    const selectEl = document.getElementById(`strategySwing${prefix}Lookback_${mode}`);
    const wrapEl = document.getElementById(`strategySwing${prefix}LookbackCustomWrap_${mode}`);
    if (!selectEl || !wrapEl) return;
    wrapEl.classList.toggle('hidden', selectEl.value !== 'custom');
}

function updateSwingSettingsVisibility(mode = 'regular') {
    const buyMode = document.getElementById(`strategyBuyMode_${mode}`)?.value || 'indicator';
    const sellMode = document.getElementById(`strategySellMode_${mode}`)?.value || 'indicator';
    const showEntrySwing = ['swing_points', 'hybrid_swing'].includes(buyMode);
    const showExitSwing = ['swing_points', 'hybrid_swing'].includes(sellMode);
    const entrySwingBox = document.getElementById(`strategySwingEntrySettings_${mode}`);
    const exitSwingBox = document.getElementById(`strategySwingExitSettings_${mode}`);
    if (entrySwingBox) entrySwingBox.classList.toggle('hidden', !showEntrySwing);
    if (exitSwingBox) exitSwingBox.classList.toggle('hidden', !showExitSwing);
    updateSwingLookbackFields(mode, 'entry');
    updateSwingLookbackFields(mode, 'exit');
}
window.updateSwingLookbackFields = updateSwingLookbackFields;
window.updateSwingSettingsVisibility = updateSwingSettingsVisibility;

function getAllocationMode(mode = 'regular') {
    return document.getElementById(`strategyAllocationMode_${mode}`)?.value || 'fixed';
}

function getSavedCompoundAmount(settings = {}, mode = 'regular') {
    const key = mode === 'options' ? 'options_compound_amount' : 'compound_amount';
    const value = parseFloat(settings?.[key]);
    return Number.isFinite(value) && value > 0 ? value : null;
}

function getStrategyExitNumber(mode, regularId, optionsId, fallback) {
    const id = mode === 'options' ? optionsId : regularId;
    const value = parseFloat(document.getElementById(id)?.value);
    return Number.isFinite(value) ? value : fallback;
}

function getStrategyExitChecked(mode, regularId, optionsId, fallback = true) {
    const id = mode === 'options' ? optionsId : regularId;
    const el = document.getElementById(id);
    return el ? el.checked : fallback;
}

window.updateAllocationModeUI = function (mode = 'regular', settings = null) {
    const allocationMode = getAllocationMode(mode);
    const label = document.getElementById(`strategyCapitalLabel_${mode}`);
    const hint = document.getElementById(`strategyCompoundAmountHint_${mode}`);
    const compoundAmount = settings ? getSavedCompoundAmount(settings, mode) : null;

    if (label) {
        label.textContent = allocationMode === 'compound' ? 'Starting Amount ($)' : 'Amount Per Trade ($)';
    }
    if (!hint) return;
    if (allocationMode === 'compound') {
        const valueText = compoundAmount ? `Current compounded amount: $${compoundAmount.toFixed(2)}` : 'Current amount will update after closed trades.';
        hint.textContent = valueText;
        hint.classList.remove('hidden');
    } else {
        hint.textContent = '';
        hint.classList.add('hidden');
    }
    if (mode === 'regular' && window.updateLivePreview_bots) {
        window.updateLivePreview_bots('regular', 'entry');
    }
};

function ensureContinuousGridLevelSizingUi(mode = 'regular') {
    if (mode !== 'regular') return;
    const defaultEntryBuyPcts = [40, 25, 15, 10, 10];
    const defaultExitSellPcts = [10, 10, 15, 20, 20];
    const enhance = (type) => {
        const isEntry = type === 'entry';
        const panel = document.getElementById(
            isEntry ? `strategyGridLevelPcts_${mode}` : `strategyExitGridLevelPcts_${mode}`
        );
        const rows = Array.from(panel?.querySelectorAll(`[id^="${isEntry ? 'gridEntry' : 'gridExit'}L"][id$="_wrap_${mode}"]`) || []);
        const grid = rows[0]?.parentElement;
        if (grid) grid.className = 'grid grid-cols-1 sm:grid-cols-2 gap-1.5';

        rows.forEach((row, index) => {
            const level = index + 1;
            if (row.dataset.sizingEnhanced === 'true') return;
            const pctLabel = row.querySelector('label');
            const pctInput = row.querySelector('input');
            if (!pctLabel || !pctInput) return;
            pctInput.addEventListener('input', () => window.updateContinuousGridUi?.(mode, type));
            const pctColumn = document.createElement('div');
            pctColumn.className = 'space-y-0.5';
            pctLabel.textContent = isEntry ? `L${level} Dip %` : `L${level} Gain %`;
            row.insertBefore(pctColumn, pctLabel);
            pctColumn.append(pctLabel, pctInput);

            const sizeColumn = document.createElement('div');
            sizeColumn.className = 'space-y-0.5';
            const sizeLabel = document.createElement('label');
            sizeLabel.className = `block text-[0.5rem] font-black uppercase ${isEntry ? 'text-fuchsia-600' : 'text-purple-600'}`;
            sizeLabel.textContent = isEntry ? 'Buy %' : 'Sell %';
            const sizeInput = document.createElement('input');
            sizeInput.type = 'number';
            sizeInput.step = isEntry ? '1' : '1';
            sizeInput.min = '1';
            sizeInput.max = '100';
            sizeInput.id = isEntry
                ? `strategyGridEntryL${level}BuyPct_${mode}`
                : `strategyGridExitL${level}SellPct_${mode}`;
            sizeInput.value = isEntry
                ? String(defaultEntryBuyPcts[level - 1])
                : String(defaultExitSellPcts[level - 1]);
            sizeInput.className = `w-full bg-white/60 border rounded-lg px-2 py-1 text-xs font-black text-indigo-950 outline-none ${isEntry ? 'border-fuchsia-200' : 'border-purple-200'}`;
            sizeInput.addEventListener('input', () => window.updateContinuousGridUi?.(mode, type));
            const enabledLabel = document.createElement('label');
            enabledLabel.className = `col-span-2 flex items-center gap-2 cursor-pointer rounded-md px-1 py-0.5 ${isEntry ? 'text-fuchsia-800' : 'text-purple-800'}`;
            const enabledInput = document.createElement('input');
            enabledInput.type = 'checkbox';
            enabledInput.checked = true;
            enabledInput.id = isEntry
                ? `strategyGridEntryL${level}Enabled_${mode}`
                : `strategyGridExitL${level}Enabled_${mode}`;
            enabledInput.className = `h-3.5 w-3.5 ${isEntry ? 'accent-fuchsia-500' : 'accent-purple-500'}`;
            const enabledText = document.createElement('span');
            enabledText.className = 'text-[0.52rem] font-black uppercase tracking-wider';
            enabledText.textContent = `Enable Level ${level}`;
            enabledLabel.append(enabledInput, enabledText);
            enabledInput.addEventListener('change', () => window.updateGridLevelInputs?.(mode, type));
            row.prepend(enabledLabel);
            sizeColumn.append(sizeLabel, sizeInput);
            row.append(sizeColumn);
            row.className = `grid grid-cols-2 gap-1.5 rounded-lg border p-1.5 ${isEntry ? 'border-fuchsia-100 bg-fuchsia-50/40' : 'border-purple-100 bg-purple-50/40'}`;
            row.dataset.sizingEnhanced = 'true';
        });
    };

    enhance('entry');
    enhance('exit');

    [
        'strategyGridBuyAllocation_regular',
        'strategyGridMaxLevels_regular',
        'strategyExitGridSellPct_regular',
        'strategyExitGridMaxLevels_regular',
    ].forEach((id) => {
        const legacy = document.getElementById(id);
        if (!legacy) return;
        legacy.disabled = true;
        const legacyBox = legacy.closest('.space-y-1');
        legacyBox?.classList.add('hidden');
    });
}

function getEnabledGridLevels(mode = 'regular', type = 'entry') {
    const prefix = type === 'entry' ? 'Entry' : 'Exit';
    return Array.from({ length: 5 }, (_, index) => index + 1).filter(level =>
        document.getElementById(`strategyGrid${prefix}L${level}Enabled_${mode}`)?.checked
    );
}

function ensureSmartDynamicGridControls(kind, parentId) {
    const suffix = kind === 'atr' ? 'Atr' : 'Pct';
    if (document.getElementById(`strategySmartGridMode${suffix}_regular`)) return;
    const parent = document.getElementById(parentId);
    if (!parent) return;
    const box = document.createElement('div');
    box.className = 'rounded-xl border border-sky-200/70 bg-sky-50/50 p-2 space-y-2';
    box.innerHTML = `<label class="flex items-center gap-2 cursor-pointer"><input type="checkbox" id="strategySmartGridMode${suffix}_regular" checked class="h-3.5 w-3.5 accent-sky-500"><span class="text-[0.55rem] font-black uppercase tracking-widest text-sky-900">Smart Dynamic Ladder (Entry + Exit)</span></label><label class="flex items-center gap-2 cursor-pointer"><input type="checkbox" id="strategySmartGridTrail${suffix}_regular" checked class="h-3.5 w-3.5 accent-sky-500"><span class="text-[0.52rem] font-black uppercase text-sky-800">Ratchet buy levels and sell levels from price peaks</span></label><p class="text-[0.52rem] text-slate-600 font-semibold">Shared trend and volatility controls: buys use a favorable high-water mark; sells activate at profit and trail the favorable peak.</p><div class="grid grid-cols-2 gap-2"><label class="text-[0.5rem] font-black uppercase text-sky-700">Adaptation Strength %<input id="strategySmartGridStrength${suffix}_regular" type="number" min="0" max="100" step="5" value="50" class="mt-0.5 w-full rounded-lg border border-sky-200 bg-white/70 px-2 py-1 text-xs font-black text-indigo-950"></label><label class="text-[0.5rem] font-black uppercase text-sky-700">Minimum Cycle Edge %<input id="strategySmartGridEdge${suffix}_regular" type="number" min="0" max="10" step="0.05" value="0.35" class="mt-0.5 w-full rounded-lg border border-sky-200 bg-white/70 px-2 py-1 text-xs font-black text-indigo-950"></label></div>${kind === 'pct' ? '<div class="grid grid-cols-2 gap-2 border-t border-sky-200/70 pt-2"><label class="text-[0.5rem] font-black uppercase text-sky-700">PCT Regime Profile<select id="strategyPctGridRegimeMode_regular" class="mt-0.5 w-full rounded-lg border border-sky-200 bg-white/70 px-2 py-1 text-xs font-black text-indigo-950"><option value="auto">Auto (recommended)</option><option value="balanced">Balanced</option><option value="runner">Always keep runner</option></select></label><label class="text-[0.5rem] font-black uppercase text-sky-700">Runner %<input id="strategyPctGridRunnerPct_regular" type="number" min="0" max="60" step="1" value="25" class="mt-0.5 w-full rounded-lg border border-sky-200 bg-white/70 px-2 py-1 text-xs font-black text-indigo-950"></label><label class="text-[0.5rem] font-black uppercase text-sky-700">Runner trail %<input id="strategyPctGridRunnerTrail_regular" type="number" min="0.1" max="25" step="0.1" value="3" class="mt-0.5 w-full rounded-lg border border-sky-200 bg-white/70 px-2 py-1 text-xs font-black text-indigo-950"></label><label class="text-[0.5rem] font-black uppercase text-sky-700">Bull trend threshold<input id="strategyPctGridBullishThreshold_regular" type="number" min="0" max="0.95" step="0.05" value="0.35" class="mt-0.5 w-full rounded-lg border border-sky-200 bg-white/70 px-2 py-1 text-xs font-black text-indigo-950"></label></div><p class="text-[0.52rem] text-sky-800 font-semibold">Exit profile: Auto sells about 75% through the ladder and retains the runner until its trailing exit.</p>' : ''}`;
    parent.prepend(box);
    if (kind === 'pct') {
        const profileGrid = document.getElementById('strategyPctGridRegimeMode_regular')?.closest('.grid');
        const profileHint = profileGrid?.nextElementSibling;
        const exitFields = document.getElementById('strategyExitGridFields_regular');
        if (profileGrid && exitFields) {
            const exitProfile = document.createElement('div');
            exitProfile.className = 'rounded-xl border border-purple-200/70 bg-purple-50/50 p-2 space-y-2';
            const heading = document.createElement('p');
            heading.className = 'text-[0.55rem] font-black uppercase tracking-widest text-purple-900';
            heading.textContent = 'PCT Exit Profile & Runner';
            exitProfile.append(heading, profileGrid);
            if (profileHint) exitProfile.append(profileHint);
            exitFields.prepend(exitProfile);
        }
    }
    box.querySelectorAll('input, select').forEach(input => input.addEventListener(input.type === 'checkbox' || input.tagName === 'SELECT' ? 'change' : 'input', () => {
        const other = suffix === 'Atr' ? 'Pct' : 'Atr';
        const sourceId = input.id.replace(suffix, other);
        const target = document.getElementById(sourceId);
        if (target) target[input.type === 'checkbox' ? 'checked' : 'value'] = input[input.type === 'checkbox' ? 'checked' : 'value'];
        window.updateLivePreview_bots?.('regular', 'entry');
    }));
    if (kind === 'pct') {
        ['strategyPctGridRegimeMode_regular', 'strategyPctGridRunnerPct_regular', 'strategyPctGridRunnerTrail_regular', 'strategyPctGridBullishThreshold_regular']
            .map(id => document.getElementById(id))
            .filter(Boolean)
            .forEach(input => input.addEventListener(input.tagName === 'SELECT' ? 'change' : 'input', () => {
                window.updateLivePreview_bots?.('regular', 'exit');
            }));
    }
}

function loadSmartDynamicGridSettings(settings = {}, newSetup = false) {
    const enabled = settings.continuous_grid_adaptive_mode !== undefined
        ? !!settings.continuous_grid_adaptive_mode
        : !!newSetup;
    for (const suffix of ['Pct', 'Atr']) {
        const toggle = document.getElementById(`strategySmartGridMode${suffix}_regular`);
        const strength = document.getElementById(`strategySmartGridStrength${suffix}_regular`);
        const edge = document.getElementById(`strategySmartGridEdge${suffix}_regular`);
        const trail = document.getElementById(`strategySmartGridTrail${suffix}_regular`);
        if (toggle) toggle.checked = enabled;
        if (trail) trail.checked = settings.continuous_grid_trailing_mode !== undefined ? !!settings.continuous_grid_trailing_mode : enabled;
        if (strength) strength.value = settings.continuous_grid_adaptation_strength ?? 50;
        if (edge) edge.value = settings.continuous_grid_min_edge_pct ?? (isCrypto(currentStrategySymbol) ? 0.6 : 0.35);
    }
    const regime = document.getElementById('strategyPctGridRegimeMode_regular');
    const runnerPct = document.getElementById('strategyPctGridRunnerPct_regular');
    const runnerTrail = document.getElementById('strategyPctGridRunnerTrail_regular');
    const threshold = document.getElementById('strategyPctGridBullishThreshold_regular');
    if (regime) regime.value = settings.pct_grid_regime_mode || 'auto';
    if (runnerPct) runnerPct.value = settings.pct_grid_runner_pct ?? 25;
    if (runnerTrail) runnerTrail.value = settings.pct_grid_runner_trail_pct ?? 3;
    if (threshold) threshold.value = settings.pct_grid_bullish_trend_threshold ?? 0.35;
}

function getSmartDynamicGridSettings() {
    const atrActive = !!document.getElementById('strategyAtrGridContinuous_regular')?.checked;
    const suffix = atrActive ? 'Atr' : 'Pct';
    return {
        continuous_grid_adaptive_mode: !!document.getElementById(`strategySmartGridMode${suffix}_regular`)?.checked,
        continuous_grid_trailing_mode: !!document.getElementById(`strategySmartGridTrail${suffix}_regular`)?.checked,
        continuous_grid_adaptation_strength: parseFloat(document.getElementById(`strategySmartGridStrength${suffix}_regular`)?.value || 50),
        continuous_grid_min_edge_pct: parseFloat(document.getElementById(`strategySmartGridEdge${suffix}_regular`)?.value || (isCrypto(currentStrategySymbol) ? 0.6 : 0.35)),
        pct_grid_regime_mode: document.getElementById('strategyPctGridRegimeMode_regular')?.value || 'auto',
        pct_grid_runner_pct: parseFloat(document.getElementById('strategyPctGridRunnerPct_regular')?.value || 25),
        pct_grid_runner_trail_pct: parseFloat(document.getElementById('strategyPctGridRunnerTrail_regular')?.value || 3),
        pct_grid_bullish_trend_threshold: parseFloat(document.getElementById('strategyPctGridBullishThreshold_regular')?.value || 0.35),
    };
}

function setAdaptiveGridLegacyControls(type, enabled) {
    const ids = type === 'entry'
        ? ['strategyEntryPctMove_regular', 'strategyEntryPctStyle_regular']
        : ['strategyRegularSlPct', 'strategyRegularTpPct', 'strategyEnablePctTrail', 'strategyRegularTrailPct'];
    ids.forEach(id => {
        const control = document.getElementById(id);
        if (!control) return;
        const mustRemainDisabled = type === 'exit'
            && id === 'strategyRegularTrailPct'
            && !document.getElementById('strategyEnablePctTrail')?.checked;
        control.disabled = enabled || mustRemainDisabled;
        const box = control.closest('.space-y-1') || control.parentElement;
        box?.classList.toggle('opacity-40', enabled);
        box?.classList.toggle('cursor-not-allowed', enabled);
        control.title = enabled
            ? `Replaced by the adaptive continuous ${type === 'entry' ? 'buy' : 'sell'} ladder.`
            : '';
    });
    const legacyRegion = type === 'entry'
        ? Array.from(document.getElementById('strategyEntryPctFields_regular')?.children || []).slice(0, 2)
        : Array.from(document.getElementById('exitFieldsPct')?.children || []).slice(0, 1);
    legacyRegion.forEach(region => {
        region.classList.toggle('opacity-40', enabled);
        region.title = enabled
            ? `Disabled while the adaptive continuous ${type === 'entry' ? 'buy' : 'sell'} ladder is enabled.`
            : '';
    });
}

window.updateGridLevelInputs = function (mode, type) {
    ensureContinuousGridLevelSizingUi(mode);
    const isEntry = type === 'entry';
    const prefix = isEntry ? `gridEntry` : `gridExit`;
    for (let i = 1; i <= 5; i++) {
        const wrap = document.getElementById(`${prefix}L${i}_wrap_${mode}`);
        const enabledInput = document.getElementById(`strategyGrid${isEntry ? 'Entry' : 'Exit'}L${i}Enabled_${mode}`);
        const levelEnabled = !!enabledInput?.checked;
        wrap?.classList.toggle('opacity-40', !levelEnabled);
        wrap?.classList.toggle('bg-slate-50/60', !levelEnabled);
        const pctInput = document.getElementById(`strategyGrid${isEntry ? 'Entry' : 'Exit'}L${i}_${mode}`);
        const sizeInput = document.getElementById(
            isEntry ? `strategyGridEntryL${i}BuyPct_${mode}` : `strategyGridExitL${i}SellPct_${mode}`
        );
        if (pctInput) pctInput.disabled = !levelEnabled;
        if (sizeInput) sizeInput.disabled = !levelEnabled;
    }
    if (window.updateContinuousGridUi) window.updateContinuousGridUi(mode, type);
};

window.updateContinuousGridUi = function (mode, type) {
    ensureContinuousGridLevelSizingUi(mode);
    if (mode === 'regular') ensureSmartDynamicGridControls('pct', 'strategyGridFields_regular');
    const isEntry = type === 'entry';
    const checkbox = document.getElementById(
        isEntry ? `strategyGridContinuous_${mode}` : `strategyExitGridContinuous_${mode}`
    );
    const fields = document.getElementById(
        isEntry ? `strategyGridFields_${mode}` : `strategyExitGridFields_${mode}`
    );
    const enabled = mode === 'regular' && !!checkbox?.checked;
    if (enabled) {
        const atrEntry = document.getElementById('strategyAtrGridContinuous_regular');
        const atrExit = document.getElementById('strategyExitAtrGridContinuous_regular');
        if (atrEntry) atrEntry.checked = false;
        if (atrExit) atrExit.checked = false;
        window.updateContinuousAtrGridUi?.('entry');
    }
    if (mode !== 'regular' && checkbox) checkbox.checked = false;
    if (fields) fields.classList.toggle('hidden', !enabled);
    if (mode === 'regular') setAdaptiveGridLegacyControls(type, enabled);
    if (mode === 'regular' && checkbox && !window.__adaptiveGridToggleSync) {
        const otherType = isEntry ? 'exit' : 'entry';
        const otherCheckbox = document.getElementById(
            isEntry ? `strategyExitGridContinuous_${mode}` : `strategyGridContinuous_${mode}`
        );
        if (otherCheckbox && otherCheckbox.checked !== checkbox.checked) {
            window.__adaptiveGridToggleSync = true;
            otherCheckbox.checked = checkbox.checked;
            window.updateContinuousGridUi(mode, otherType);
            window.__adaptiveGridToggleSync = false;
        }
    }

    if (mode === 'regular' && isEntry) {
        const levels = getEnabledGridLevels('regular', 'entry');
        const totalBuyPct = levels.map(level =>
            Math.max(0, parseFloat(document.getElementById(`strategyGridEntryL${level}BuyPct_regular`)?.value || 0))
        ).reduce((sum, value) => sum + value, 0);
        const help = document.getElementById('strategyGridEntryHelp_regular');
        if (help) {
            help.textContent = `Enabled: ${levels.length ? levels.map(level => `L${level}`).join(', ') : 'none'} • Buy allocation: ${totalBuyPct.toFixed(1)}%. Each Buy % is calculated from bot capital. Crossed enabled levels combine into one protected order.`;
            help.classList.toggle('text-red-600', !levels.length || totalBuyPct > 100.0001);
            help.classList.toggle('text-fuchsia-800', !!levels.length && totalBuyPct <= 100.0001);
        }
    }
    if (mode === 'regular' && !isEntry) {
        const levels = getEnabledGridLevels('regular', 'exit');
        const sizes = levels.map(level => Math.max(1, parseFloat(document.getElementById(`strategyGridExitL${level}SellPct_regular`)?.value || 20)));
        const totalSellPct = sizes.reduce((sum, value) => sum + value, 0);
        const help = document.getElementById('strategyGridExitHelp_regular');
        if (help) {
            help.textContent = `Enabled: ${levels.length ? levels.map(level => `L${level}`).join(', ') : 'none'} • Sell allocation: ${totalSellPct.toFixed(1)}% of the ladder-start position. Crossed enabled levels combine into one protected order.`;
            help.classList.toggle('text-red-600', !levels.length || totalSellPct > 100.0001);
            help.classList.toggle('text-purple-800', !!levels.length && totalSellPct <= 100.0001);
        }
    }
    if (mode === 'regular' && window.updateLivePreview_bots) {
        window.updateLivePreview_bots('regular', type);
    }
};

function ensureContinuousAtrGridUi() {
    if (document.getElementById('strategyAtrGridContinuous_regular')) return;
    const defaults = [2, 4, 6, 8, 10];
    const build = (side, parentId) => {
        const entry = side === 'entry';
        const parent = document.getElementById(parentId);
        if (!parent) return;
        const wrapper = document.createElement('div');
        wrapper.className = entry ? 'col-span-1 sm:col-span-2 pt-2 border-t border-cyan-500/20' : 'col-span-1 sm:col-span-3 pt-3 border-t border-cyan-500/20';
        const toggleId = entry ? 'strategyAtrGridContinuous_regular' : 'strategyExitAtrGridContinuous_regular';
        const panelId = entry ? 'strategyAtrGridFields_regular' : 'strategyExitAtrGridFields_regular';
        const rows = defaults.map((mult, index) => {
            const level = index + 1;
            const prefix = entry ? 'strategyAtrGridEntry' : 'strategyAtrGridExit';
            return `<div class="grid grid-cols-2 gap-1.5 rounded-lg border border-cyan-100 bg-cyan-50/40 p-1.5" data-atr-grid-row="${side}-${level}">
                <label class="col-span-2 flex items-center gap-2 cursor-pointer text-cyan-800"><input type="checkbox" id="${prefix}L${level}Enabled_regular" checked class="h-3.5 w-3.5 accent-cyan-500"><span class="text-[0.52rem] font-black uppercase tracking-wider">Enable Level ${level}</span></label>
                <div class="space-y-0.5"><label class="block text-[0.5rem] font-black text-cyan-600 uppercase">L${level} ATR Multiple</label><input type="number" step="0.1" min="0.1" max="100" id="${prefix}L${level}Mult_regular" value="${mult}" class="w-full bg-white/60 border border-cyan-200 rounded-lg px-2 py-1 text-xs font-black text-indigo-950 outline-none"></div>
                <div class="space-y-0.5"><label class="block text-[0.5rem] font-black text-cyan-600 uppercase">${entry ? 'Buy' : 'Sell'} %</label><input type="number" step="1" min="1" max="100" id="${prefix}L${level}${entry ? 'Buy' : 'Sell'}Pct_regular" value="20" class="w-full bg-white/60 border border-cyan-200 rounded-lg px-2 py-1 text-xs font-black text-indigo-950 outline-none"></div>
            </div>`;
        }).join('');
        wrapper.innerHTML = `<label class="flex items-center gap-2 mb-2 cursor-pointer group"><span class="relative flex items-center"><input type="checkbox" id="${toggleId}" class="sr-only peer"><span class="w-8 h-4 bg-slate-300 rounded-full peer peer-checked:after:translate-x-full after:content-[''] after:absolute after:top-[2px] after:left-[2px] after:bg-white after:border after:rounded-full after:h-3 after:w-3 after:transition-all peer-checked:bg-cyan-500 shadow-inner"></span></span><span class="text-[0.6rem] font-black text-indigo-950 uppercase tracking-widest">Enable Adaptive Continuous ATR ${entry ? 'Buy' : 'Sell'} Grid</span></label><div id="${panelId}" class="hidden space-y-2"><div class="text-[0.55rem] font-black text-cyan-900 uppercase tracking-widest">ATR Ladder — Enable • ATR Multiple • ${entry ? 'Buy' : 'Sell'} %</div><div class="grid grid-cols-1 sm:grid-cols-2 gap-1.5">${rows}</div><p id="${panelId}Help" class="text-[0.55rem] text-cyan-800 font-bold"></p><p class="text-[0.52rem] text-slate-500 font-semibold">Anchor ATR is kept for cycle context; active trigger prices recalculate from bounded live ATR as volatility changes.</p></div>`;
        parent.appendChild(wrapper);
        wrapper.querySelectorAll('input').forEach(input => input.addEventListener(input.type === 'checkbox' ? 'change' : 'input', () => window.updateContinuousAtrGridUi?.(side)));
    };
    build('entry', 'strategyEntryAtrFields_regular');
    build('exit', 'exitFieldsATR');
    ensureSmartDynamicGridControls('atr', 'strategyAtrGridFields_regular');
}

function getEnabledAtrGridLevels(side) {
    const prefix = side === 'entry' ? 'strategyAtrGridEntry' : 'strategyAtrGridExit';
    return Array.from({ length: 5 }, (_, i) => i + 1).filter(level => document.getElementById(`${prefix}L${level}Enabled_regular`)?.checked);
}

window.updateContinuousAtrGridUi = function (side = 'entry') {
    ensureContinuousAtrGridUi();
    const entryToggle = document.getElementById('strategyAtrGridContinuous_regular');
    const exitToggle = document.getElementById('strategyExitAtrGridContinuous_regular');
    const source = side === 'entry' ? entryToggle : exitToggle;
    if (!window.__adaptiveAtrGridToggleSync && source) {
        window.__adaptiveAtrGridToggleSync = true;
        if (entryToggle) entryToggle.checked = source.checked;
        if (exitToggle) exitToggle.checked = source.checked;
        window.__adaptiveAtrGridToggleSync = false;
    }
    const enabled = !!entryToggle?.checked && !!exitToggle?.checked;
    if (enabled) {
        const pctEntry = document.getElementById('strategyGridContinuous_regular');
        const pctExit = document.getElementById('strategyExitGridContinuous_regular');
        if (pctEntry) pctEntry.checked = false;
        if (pctExit) pctExit.checked = false;
        document.getElementById('strategyGridFields_regular')?.classList.add('hidden');
        document.getElementById('strategyExitGridFields_regular')?.classList.add('hidden');
        setAdaptiveGridLegacyControls('entry', false);
        setAdaptiveGridLegacyControls('exit', false);
    }
    document.getElementById('strategyAtrGridFields_regular')?.classList.toggle('hidden', !enabled);
    document.getElementById('strategyExitAtrGridFields_regular')?.classList.toggle('hidden', !enabled);
    ['strategyEntryAtrMult_regular', 'strategyEntryAtrStyle_regular', 'strategyAtrStopMult', 'strategyAtrTpMult', 'strategyEnableAtrTrail', 'strategyAtrTrailMult'].forEach(id => {
        const el = document.getElementById(id); if (!el) return;
        el.disabled = enabled || (id === 'strategyAtrTrailMult' && !document.getElementById('strategyEnableAtrTrail')?.checked);
        el.closest('.space-y-1')?.classList.toggle('opacity-40', enabled);
    });
    for (const ladderSide of ['entry', 'exit']) {
        const levels = getEnabledAtrGridLevels(ladderSide);
        const prefix = ladderSide === 'entry' ? 'strategyAtrGridEntry' : 'strategyAtrGridExit';
        for (let level = 1; level <= 5; level++) {
            const active = levels.includes(level);
            document.querySelector(`[data-atr-grid-row="${ladderSide}-${level}"]`)?.classList.toggle('opacity-40', !active);
            const mult = document.getElementById(`${prefix}L${level}Mult_regular`);
            const size = document.getElementById(`${prefix}L${level}${ladderSide === 'entry' ? 'Buy' : 'Sell'}Pct_regular`);
            if (mult) mult.disabled = !active; if (size) size.disabled = !active;
        }
        const total = levels.reduce((sum, level) => sum + Math.max(0, Number(document.getElementById(`${prefix}L${level}${ladderSide === 'entry' ? 'Buy' : 'Sell'}Pct_regular`)?.value || 0)), 0);
        const help = document.getElementById(`${ladderSide === 'entry' ? 'strategyAtrGridFields_regular' : 'strategyExitAtrGridFields_regular'}Help`);
        if (help) { help.textContent = `Enabled: ${levels.length ? levels.map(level => `L${level}`).join(', ') : 'none'} • ${ladderSide === 'entry' ? 'Buy' : 'Sell'} allocation: ${total.toFixed(1)}% (maximum 100%).`; help.classList.toggle('text-red-600', !levels.length || total > 100.0001); }
    }
    window.updateLivePreview_bots?.('regular', side);
};

function loadContinuousAtrGridSettings(settings = {}) {
    ensureContinuousAtrGridUi();
    const entryToggle = document.getElementById('strategyAtrGridContinuous_regular');
    const exitToggle = document.getElementById('strategyExitAtrGridContinuous_regular');
    if (entryToggle) entryToggle.checked = !!settings.atr_grid_continuous_mode;
    if (exitToggle) exitToggle.checked = !!settings.exit_atr_grid_continuous_mode;
    for (let level = 1; level <= 5; level++) {
        const fallback = level * 2;
        const fields = [
            [`strategyAtrGridEntryL${level}Mult_regular`, settings[`grid_atr_entry_l${level}_mult`] ?? fallback],
            [`strategyAtrGridEntryL${level}BuyPct_regular`, settings[`grid_atr_entry_l${level}_buy_pct`] ?? 20],
            [`strategyAtrGridExitL${level}Mult_regular`, settings[`grid_atr_exit_l${level}_mult`] ?? fallback],
            [`strategyAtrGridExitL${level}SellPct_regular`, settings[`grid_atr_exit_l${level}_sell_pct`] ?? 20],
        ];
        fields.forEach(([id, value]) => { const el = document.getElementById(id); if (el) el.value = value; });
        const entryEnabled = document.getElementById(`strategyAtrGridEntryL${level}Enabled_regular`);
        const exitEnabled = document.getElementById(`strategyAtrGridExitL${level}Enabled_regular`);
        if (entryEnabled) entryEnabled.checked = settings[`grid_atr_entry_l${level}_enabled`] ?? true;
        if (exitEnabled) exitEnabled.checked = settings[`grid_atr_exit_l${level}_enabled`] ?? true;
    }
    window.updateContinuousAtrGridUi?.('entry');
    window.updateContinuousAtrGridUi?.('exit');
}

function getStrategyModeSettings(mode = 'regular') {
    const isRegular = mode === 'regular';
    const isAutopilot = mode === 'ai_autopilot';
    return {
        allocation_mode: (isRegular || isAutopilot) ? getAllocationMode(mode) : undefined,
        options_allocation_mode: mode === 'options' ? getAllocationMode('options') : undefined,
        buy_mode: document.getElementById(`strategyBuyMode_${mode}`)?.value || 'indicator',
        custom_buy_price: parseFloat(document.getElementById(`strategyCustomBuyPrice_${mode}`)?.value || 0),
        custom_stop_loss_price: parseFloat(document.getElementById(`strategyCustomStopLossPrice_${mode}`)?.value || 0),
        custom_take_profit_price: parseFloat(document.getElementById(`strategyCustomTakeProfitPrice_${mode}`)?.value || 0),
        custom_underlying_stop_loss_price: mode === 'options' ? parseFloat(document.getElementById('strategyCustomUnderlyingStopLossPrice_options')?.value || 0) : undefined,
        custom_underlying_take_profit_price: mode === 'options' ? parseFloat(document.getElementById('strategyCustomUnderlyingTakeProfitPrice_options')?.value || 0) : undefined,
        market_session: getMarketSessionForStrategy(mode),
        entry_atr_multiplier: parseFloat(document.getElementById(`strategyEntryAtrMult_${mode}`)?.value || 2),
        entry_pct_move: parseFloat(document.getElementById(`strategyEntryPctMove_${mode}`)?.value || 5),
        entry_atr_style: document.getElementById(`strategyEntryAtrStyle_${mode}`)?.value || 'pullback_reclaim',
        entry_pct_style: document.getElementById(`strategyEntryPctStyle_${mode}`)?.value || 'dip_reclaim',
        grid_continuous_mode: document.getElementById(`strategyGridContinuous_${mode}`)?.checked || false,
        continuous_grid_ladder_version: 3,
        ...getSmartDynamicGridSettings(),
        grid_max_levels: 5,
        grid_entry_l1_pct: parseFloat(document.getElementById(`strategyGridEntryL1_${mode}`)?.value || 1),
        grid_entry_l2_pct: parseFloat(document.getElementById(`strategyGridEntryL2_${mode}`)?.value || 2),
        grid_entry_l3_pct: parseFloat(document.getElementById(`strategyGridEntryL3_${mode}`)?.value || 3),
        grid_entry_l4_pct: parseFloat(document.getElementById(`strategyGridEntryL4_${mode}`)?.value || 4),
        grid_entry_l5_pct: parseFloat(document.getElementById(`strategyGridEntryL5_${mode}`)?.value || 5),
        grid_entry_l1_buy_pct: parseFloat(document.getElementById(`strategyGridEntryL1BuyPct_${mode}`)?.value || 40),
        grid_entry_l2_buy_pct: parseFloat(document.getElementById(`strategyGridEntryL2BuyPct_${mode}`)?.value || 25),
        grid_entry_l3_buy_pct: parseFloat(document.getElementById(`strategyGridEntryL3BuyPct_${mode}`)?.value || 15),
        grid_entry_l4_buy_pct: parseFloat(document.getElementById(`strategyGridEntryL4BuyPct_${mode}`)?.value || 10),
        grid_entry_l5_buy_pct: parseFloat(document.getElementById(`strategyGridEntryL5BuyPct_${mode}`)?.value || 10),
        grid_entry_l1_enabled: document.getElementById(`strategyGridEntryL1Enabled_${mode}`)?.checked ?? true,
        grid_entry_l2_enabled: document.getElementById(`strategyGridEntryL2Enabled_${mode}`)?.checked ?? true,
        grid_entry_l3_enabled: document.getElementById(`strategyGridEntryL3Enabled_${mode}`)?.checked ?? true,
        grid_entry_l4_enabled: document.getElementById(`strategyGridEntryL4Enabled_${mode}`)?.checked ?? true,
        grid_entry_l5_enabled: document.getElementById(`strategyGridEntryL5Enabled_${mode}`)?.checked ?? true,
        exit_grid_continuous_mode: document.getElementById(`strategyExitGridContinuous_${mode}`)?.checked || false,
        exit_grid_sell_pct: parseFloat(document.getElementById(`strategyGridExitL1SellPct_${mode}`)?.value || document.getElementById(`strategyExitGridSellPct_${mode}`)?.value || 20),
        exit_grid_max_levels: 5,
        grid_exit_l1_pct: parseFloat(document.getElementById(`strategyGridExitL1_${mode}`)?.value || 1),
        grid_exit_l2_pct: parseFloat(document.getElementById(`strategyGridExitL2_${mode}`)?.value || 2),
        grid_exit_l3_pct: parseFloat(document.getElementById(`strategyGridExitL3_${mode}`)?.value || 3),
        grid_exit_l4_pct: parseFloat(document.getElementById(`strategyGridExitL4_${mode}`)?.value || 4),
        grid_exit_l5_pct: parseFloat(document.getElementById(`strategyGridExitL5_${mode}`)?.value || 5),
        grid_exit_l1_sell_pct: parseFloat(document.getElementById(`strategyGridExitL1SellPct_${mode}`)?.value || 10),
        grid_exit_l2_sell_pct: parseFloat(document.getElementById(`strategyGridExitL2SellPct_${mode}`)?.value || 10),
        grid_exit_l3_sell_pct: parseFloat(document.getElementById(`strategyGridExitL3SellPct_${mode}`)?.value || 15),
        grid_exit_l4_sell_pct: parseFloat(document.getElementById(`strategyGridExitL4SellPct_${mode}`)?.value || 20),
        grid_exit_l5_sell_pct: parseFloat(document.getElementById(`strategyGridExitL5SellPct_${mode}`)?.value || 20),
        grid_exit_l1_enabled: document.getElementById(`strategyGridExitL1Enabled_${mode}`)?.checked ?? true,
        grid_exit_l2_enabled: document.getElementById(`strategyGridExitL2Enabled_${mode}`)?.checked ?? true,
        grid_exit_l3_enabled: document.getElementById(`strategyGridExitL3Enabled_${mode}`)?.checked ?? true,
        grid_exit_l4_enabled: document.getElementById(`strategyGridExitL4Enabled_${mode}`)?.checked ?? true,
        grid_exit_l5_enabled: document.getElementById(`strategyGridExitL5Enabled_${mode}`)?.checked ?? true,
        atr_grid_continuous_mode: mode === 'regular' && !!document.getElementById('strategyAtrGridContinuous_regular')?.checked,
        exit_atr_grid_continuous_mode: mode === 'regular' && !!document.getElementById('strategyExitAtrGridContinuous_regular')?.checked,
        ...Object.fromEntries(Array.from({ length: 5 }, (_, index) => {
            const level = index + 1;
            return [
                [`grid_atr_entry_l${level}_mult`, parseFloat(document.getElementById(`strategyAtrGridEntryL${level}Mult_regular`)?.value || level * 2)],
                [`grid_atr_entry_l${level}_buy_pct`, parseFloat(document.getElementById(`strategyAtrGridEntryL${level}BuyPct_regular`)?.value || 20)],
                [`grid_atr_entry_l${level}_enabled`, document.getElementById(`strategyAtrGridEntryL${level}Enabled_regular`)?.checked ?? true],
                [`grid_atr_exit_l${level}_mult`, parseFloat(document.getElementById(`strategyAtrGridExitL${level}Mult_regular`)?.value || level * 2)],
                [`grid_atr_exit_l${level}_sell_pct`, parseFloat(document.getElementById(`strategyAtrGridExitL${level}SellPct_regular`)?.value || 20)],
                [`grid_atr_exit_l${level}_enabled`, document.getElementById(`strategyAtrGridExitL${level}Enabled_regular`)?.checked ?? true],
            ];
        }).flat()),
        swing_entry_mode: document.getElementById(`strategySwingEntryMode_${mode}`)?.value || 'bounce_retest',
        swing_entry_lookback_mode: document.getElementById(`strategySwingEntryLookback_${mode}`)?.value || '20D',
        swing_entry_lookback_days: getSwingLookbackDaysValue(mode, 'entry'),
        swing_exit_lookback_mode: document.getElementById(`strategySwingExitLookback_${mode}`)?.value || '20D',
        swing_exit_lookback_days: getSwingLookbackDaysValue(mode, 'exit'),
        enable_risk_management: document.getElementById(`strategyEnableRisk_${mode}`)?.checked || false,
        enable_atr_trail: getStrategyExitChecked(mode, 'strategyEnableAtrTrail', 'strategyEnableAtrTrail_options', true),
        enable_pct_trail: mode === 'regular' && document.getElementById('strategyExitGridContinuous_regular')?.checked
            ? false
            : getStrategyExitChecked(mode, 'strategyEnablePctTrail', 'strategyEnablePctTrail_options', true),
        regular_stop_loss_pct: (isRegular || isAutopilot) ? parseFloat(document.getElementById('strategyRegularSlPct')?.value || 5) / 100.0 : undefined,
        regular_take_profit_pct: (isRegular || isAutopilot) ? parseFloat(document.getElementById('strategyRegularTpPct')?.value || 10) / 100.0 : undefined,
        regular_trailing_pct: (isRegular || isAutopilot) ? parseFloat(document.getElementById('strategyRegularTrailPct')?.value || 3) / 100.0 : undefined,
        options_trailing_pct: mode === 'options' ? parseFloat(document.getElementById('strategyOptionsTrailPct')?.value || OPTIONS_DEFAULT_TRAIL_PCT) / 100.0 : undefined,
    };
}

window.openStrategyModal = async function (symbol, mode = 'deploy', name = '') {
    currentStrategySymbol = symbol.toUpperCase();
    window.currentStrategySymbol = currentStrategySymbol;
    window.IndicatorWorkspace?.setTicker(currentStrategySymbol);
    strategyModalMode = mode;
    const editSettings = mode === 'edit'
        ? await fetchBotSettingsLazy(currentStrategySymbol)
        : null;

    if (mode === 'deploy') {
        const activeBots = window.lastBotsData?.tradelist || [];
        if (activeBots.includes(currentStrategySymbol)) {
            const existingSettings = (window.lastBotsData?.ticker_settings || {})[currentStrategySymbol] || {};
            const modeLabel = existingSettings.options_trading ? 'Options' : (isCrypto(currentStrategySymbol) ? 'Crypto' : 'Stock');
            alert(
                `⚠️ Active Bot Already Deployed for ${currentStrategySymbol}\n\n` +
                `An active ${modeLabel} bot is already running for ${currentStrategySymbol}.\n\n` +
                `To deploy a new bot, please delete the existing ${currentStrategySymbol} bot from your dashboard first.\n` +
                `*(Note: Deleting an active bot will automatically close any open position and cancel pending orders for that ticker).*`
            );
            return;
        }
    }

    const modal = document.getElementById('botStrategyModal');
    const titleEl = document.getElementById('strategyModalTitle');
    const subtitleEl = document.getElementById('strategyModalSubtitle');
    const actionBtn = document.getElementById('strategyActionButton');

    if (!modal) return;

    // UI State based on Mode
    if (mode === 'deploy') {
        setStrategyDraftIndicatorOverrides({});
        titleEl.textContent = `Deploy ${currentStrategySymbol}`;
        subtitleEl.textContent = name ? `Configure parameters for ${name}` : 'Set initial strategy parameters';
        actionBtn.textContent = 'Deploy Strategy 🚀';
        actionBtn.className = "w-full bg-gradient-to-r from-emerald-500 to-teal-500 text-white font-black py-4 rounded-2xl hover:shadow-lg hover:shadow-emerald-500/30 transition-all active:scale-[0.98] uppercase tracking-[0.2em] shadow-md flex items-center justify-center gap-2 disabled:opacity-50 disabled:cursor-not-allowed disabled:hover:shadow-none disabled:active:scale-100";
        actionBtn.disabled = true;

        const riskContainer = document.getElementById('deployRiskAgreementContainer');
        const riskCheck = document.getElementById('riskAgreementCheckbox');
        if (riskContainer) riskContainer.classList.remove('hidden');
        if (riskCheck) riskCheck.checked = false;

        // Defaults for deployment
        document.getElementById('strategyCapital').value = 100;
        if (document.getElementById('strategyMaxTrades')) document.getElementById('strategyMaxTrades').value = '';
        if (document.getElementById('strategyCapital_ai_autopilot')) document.getElementById('strategyCapital_ai_autopilot').value = 100;
        if (document.getElementById('strategyOptionsTradeAmount')) document.getElementById('strategyOptionsTradeAmount').value = 100;
        if (document.getElementById('strategyAllocationMode_regular')) document.getElementById('strategyAllocationMode_regular').value = 'fixed';
        if (document.getElementById('strategyAllocationMode_options')) document.getElementById('strategyAllocationMode_options').value = 'fixed';
        if (document.getElementById('strategyAllocationMode_ai_autopilot')) document.getElementById('strategyAllocationMode_ai_autopilot').value = 'fixed';
        window.updateAllocationModeUI?.('regular');
        window.updateAllocationModeUI?.('options');
        window.updateAllocationModeUI?.('ai_autopilot');
        document.getElementById('strategyBuyThreshold_regular').value = 0;
        document.getElementById('strategyBuyThreshold_options').value = 0;
        document.getElementById('strategySellThreshold_regular').value = 0;
        document.getElementById('strategySellThreshold_options').value = 0;
        document.getElementById('strategyTimeframe_regular').value = "1Hour";
        document.getElementById('strategyTimeframe_options').value = "5Min";
        if (document.getElementById('strategyTimeframe_ai_autopilot')) document.getElementById('strategyTimeframe_ai_autopilot').value = "1Hour";
        if (document.getElementById('strategyAiModel')) document.getElementById('strategyAiModel').value = "llama-3.3-70b-versatile";
        if (document.getElementById('strategyBuyMode_regular')) document.getElementById('strategyBuyMode_regular').value = "indicator";
        if (document.getElementById('strategyBuyMode_options')) document.getElementById('strategyBuyMode_options').value = "indicator";
        if (document.getElementById('strategyCustomBuyPrice_regular')) document.getElementById('strategyCustomBuyPrice_regular').value = "";
        if (document.getElementById('strategyCustomBuyPrice_options')) document.getElementById('strategyCustomBuyPrice_options').value = "";
        if (document.getElementById('strategyCustomStopLossPrice_regular')) document.getElementById('strategyCustomStopLossPrice_regular').value = "";
        if (document.getElementById('strategyCustomTakeProfitPrice_regular')) document.getElementById('strategyCustomTakeProfitPrice_regular').value = "";
        if (document.getElementById('strategyCustomStopLossPrice_options')) document.getElementById('strategyCustomStopLossPrice_options').value = "";
        if (document.getElementById('strategyCustomTakeProfitPrice_options')) document.getElementById('strategyCustomTakeProfitPrice_options').value = "";
        if (document.getElementById('strategyCustomUnderlyingStopLossPrice_options')) document.getElementById('strategyCustomUnderlyingStopLossPrice_options').value = "";
        if (document.getElementById('strategyCustomUnderlyingTakeProfitPrice_options')) document.getElementById('strategyCustomUnderlyingTakeProfitPrice_options').value = "";
        if (document.getElementById('strategyEntryAtrMult_regular')) document.getElementById('strategyEntryAtrMult_regular').value = 2;
        if (document.getElementById('strategyEntryAtrMult_options')) document.getElementById('strategyEntryAtrMult_options').value = 2;
        if (document.getElementById('strategyEntryPctMove_regular')) document.getElementById('strategyEntryPctMove_regular').value = 5;
        if (document.getElementById('strategyEntryPctMove_options')) document.getElementById('strategyEntryPctMove_options').value = 5;
        if (document.getElementById('strategyEntryAtrStyle_regular')) document.getElementById('strategyEntryAtrStyle_regular').value = 'pullback_reclaim';
        if (document.getElementById('strategyEntryAtrStyle_options')) document.getElementById('strategyEntryAtrStyle_options').value = 'pullback_reclaim';
        if (document.getElementById('strategyEntryPctStyle_regular')) document.getElementById('strategyEntryPctStyle_regular').value = 'dip_reclaim';
        if (document.getElementById('strategyEntryPctStyle_options')) document.getElementById('strategyEntryPctStyle_options').value = 'dip_reclaim';
        ensureContinuousGridLevelSizingUi('regular');
        const entryGridToggle = document.getElementById('strategyGridContinuous_regular');
        const exitGridToggle = document.getElementById('strategyExitGridContinuous_regular');
        if (entryGridToggle) entryGridToggle.checked = false;
        if (exitGridToggle) exitGridToggle.checked = false;
        for (let level = 1; level <= 5; level++) {
            const entryEnabled = document.getElementById(`strategyGridEntryL${level}Enabled_regular`);
            const exitEnabled = document.getElementById(`strategyGridExitL${level}Enabled_regular`);
            const entryTrigger = document.getElementById(`strategyGridEntryL${level}_regular`);
            const exitTrigger = document.getElementById(`strategyGridExitL${level}_regular`);
            const buyPct = document.getElementById(`strategyGridEntryL${level}BuyPct_regular`);
            const sellPct = document.getElementById(`strategyGridExitL${level}SellPct_regular`);
            if (entryEnabled) entryEnabled.checked = true;
            if (exitEnabled) exitEnabled.checked = true;
            if (entryTrigger) entryTrigger.value = level;
            if (exitTrigger) exitTrigger.value = level;
            if (buyPct) buyPct.value = [40, 25, 15, 10, 10][level - 1];
            if (sellPct) sellPct.value = [10, 10, 15, 20, 20][level - 1];
        }
        window.updateGridLevelInputs?.('regular', 'entry');
        window.updateGridLevelInputs?.('regular', 'exit');
        window.updateContinuousGridUi?.('regular', 'entry');
        window.updateContinuousGridUi?.('regular', 'exit');
        loadContinuousAtrGridSettings({});
        loadSmartDynamicGridSettings({}, true);
        applyMarketSessionForSymbol(currentStrategySymbol, {});
        if (document.getElementById('strategySwingEntryMode_regular')) document.getElementById('strategySwingEntryMode_regular').value = "bounce_retest";
        if (document.getElementById('strategySwingEntryMode_options')) document.getElementById('strategySwingEntryMode_options').value = "bounce_retest";
        if (document.getElementById('strategySwingEntryLookback_regular')) document.getElementById('strategySwingEntryLookback_regular').value = "20D";
        if (document.getElementById('strategySwingEntryLookback_options')) document.getElementById('strategySwingEntryLookback_options').value = "20D";
        if (document.getElementById('strategySwingEntryLookbackCustom_regular')) document.getElementById('strategySwingEntryLookbackCustom_regular').value = "20";
        if (document.getElementById('strategySwingEntryLookbackCustom_options')) document.getElementById('strategySwingEntryLookbackCustom_options').value = "20";
        if (document.getElementById('strategySwingExitLookback_regular')) document.getElementById('strategySwingExitLookback_regular').value = "20D";
        if (document.getElementById('strategySwingExitLookback_options')) document.getElementById('strategySwingExitLookback_options').value = "20D";
        if (document.getElementById('strategySwingExitLookbackCustom_regular')) document.getElementById('strategySwingExitLookbackCustom_regular').value = "20";
        if (document.getElementById('strategySwingExitLookbackCustom_options')) document.getElementById('strategySwingExitLookbackCustom_options').value = "20";
        window.toggleModeActive('regular');
        document.getElementById('strategySellMode_regular').value = "indicator";
        if (document.getElementById('strategySellMode_options')) document.getElementById('strategySellMode_options').value = "hybrid";
        window._strategyPaused = false;

        const autopilotEl = document.getElementById('strategyAiAutopilot');
        if (autopilotEl) autopilotEl.checked = false;
        window.handleStrategyAutopilotToggle(false);

        // Reset Options Defaults

        document.getElementById('strategyOptionsType').value = "calls_only";
        delete document.getElementById('strategyOptionsType').dataset.legacyBoth;
        document.getElementById('strategyOptionsTradeAmount').value = 100;
        document.getElementById('strategyOptionsExpiryDays').value = 3;
        document.getElementById('strategyOptionsStrikeType').value = "ATM";
        if (document.getElementById('strategySellMode_options')) document.getElementById('strategySellMode_options').value = "hybrid";
        if (document.getElementById('strategyOptionsSlPct')) document.getElementById('strategyOptionsSlPct').value = OPTIONS_DEFAULT_SL_PCT;
        if (document.getElementById('strategyOptionsTpPct')) document.getElementById('strategyOptionsTpPct').value = OPTIONS_DEFAULT_TP_PCT;

        // Reset Risk Overrides to defaults
        document.getElementById('strategyRiskPerTrade').value = '10';
        document.getElementById('strategyMaxDailyDrawdown').value = '50';
        document.getElementById('strategyMaxPositionPct').value = '100';
        document.getElementById('strategyEnableRisk_regular').checked = true;
        document.getElementById('strategyEnableRisk_options').checked = true;
        window.toggleRiskControls(true, 'regular');
        window.toggleRiskControls(true, 'options');
        if (document.getElementById('strategyOptionsRiskPerTrade')) {
            document.getElementById('strategyOptionsRiskPerTrade').value = '30';
            document.getElementById('strategyOptionsMaxDailyDrawdown').value = '60';
            document.getElementById('strategyOptionsMaxPositionPct').value = '100';
        }
        document.getElementById('strategyAtrStopMult').value = INDICATOR_DEFAULTS['ATR_STOP_MULTIPLIER'] || 2;
        document.getElementById('strategyAtrTrailMult').value = INDICATOR_DEFAULTS['ATR_TRAIL_MULTIPLIER'] || 3;
        document.getElementById('strategyAtrTpMult').value = INDICATOR_DEFAULTS['ATR_TAKE_PROFIT_MULTIPLIER'] || 4;
        if (document.getElementById('strategyEnableAtrTrail')) document.getElementById('strategyEnableAtrTrail').checked = true;
        if (document.getElementById('strategyEnablePctTrail')) document.getElementById('strategyEnablePctTrail').checked = true;
        if (document.getElementById('strategyAtrStopMult_options')) document.getElementById('strategyAtrStopMult_options').value = INDICATOR_DEFAULTS['ATR_STOP_MULTIPLIER'] || 2;
        if (document.getElementById('strategyAtrTrailMult_options')) document.getElementById('strategyAtrTrailMult_options').value = INDICATOR_DEFAULTS['ATR_TRAIL_MULTIPLIER'] || 3;
        if (document.getElementById('strategyAtrTpMult_options')) document.getElementById('strategyAtrTpMult_options').value = INDICATOR_DEFAULTS['ATR_TAKE_PROFIT_MULTIPLIER'] || 4;
        if (document.getElementById('strategyEnableAtrTrail_options')) document.getElementById('strategyEnableAtrTrail_options').checked = true;
        if (document.getElementById('strategyEnablePctTrail_options')) document.getElementById('strategyEnablePctTrail_options').checked = true;
        if (document.getElementById('strategyOptionsTrailPct')) document.getElementById('strategyOptionsTrailPct').value = OPTIONS_DEFAULT_TRAIL_PCT;
        if (document.getElementById('strategyRegularSlPct')) document.getElementById('strategyRegularSlPct').value = 5;
        if (document.getElementById('strategyRegularTpPct')) document.getElementById('strategyRegularTpPct').value = 10;
        if (document.getElementById('strategyRegularTrailPct')) document.getElementById('strategyRegularTrailPct').value = 3;
        document.getElementById('strategyEnableCooldown_regular').checked = true;
        document.getElementById('strategyEnableCooldown_options').checked = true;
        document.getElementById('strategyCooldownSettingsWrapper_regular').classList.remove('hidden');
        document.getElementById('strategyCooldownSettingsWrapper_options').classList.remove('hidden');
        document.getElementById('strategyCooldownMinutes_regular').value = "default";
        document.getElementById('strategyCooldownMinutes_options').value = "default";
        document.getElementById('strategyCooldownCustomContainer_regular').classList.add('hidden');
        document.getElementById('strategyCooldownCustomContainer_options').classList.add('hidden');
        document.getElementById('strategyCooldownCustomMinutes_regular').value = "";
        document.getElementById('strategyCooldownCustomMinutes_options').value = "";
        ['regular', 'options'].forEach(mode => {
            const signalReset = document.getElementById(mode === 'regular' ? 'strategyRegularSignalReset' : 'strategyOptionsSignalReset');
            if (signalReset) signalReset.checked = true;
            if (document.getElementById(`strategyPostStopBars_${mode}`)) document.getElementById(`strategyPostStopBars_${mode}`).value = '3';
            if (document.getElementById(`strategyMaxConsecutiveLosses_${mode}`)) document.getElementById(`strategyMaxConsecutiveLosses_${mode}`).value = '3';
        });
        if (document.getElementById('strategyStockMaxSpreadPct')) document.getElementById('strategyStockMaxSpreadPct').value = '0.25';
        if (document.getElementById('strategyCryptoMaxSpreadPct')) document.getElementById('strategyCryptoMaxSpreadPct').value = '0.50';
        if (document.getElementById('strategyOptionsMaxSpreadPct')) document.getElementById('strategyOptionsMaxSpreadPct').value = '5';
        if (document.getElementById('strategyOptionsOpenBlackoutMinutes')) document.getElementById('strategyOptionsOpenBlackoutMinutes').value = '15';



        // All indicators start in unchecked mode with default settings when deploying new bots
        document.querySelectorAll('.strategy-indicator-check').forEach(chk => {
            chk.checked = false;
        });

        const addWatchlistEl = document.getElementById('strategyAddWatchlist');
        if (addWatchlistEl) addWatchlistEl.checked = true;
    } else {
        titleEl.textContent = `${currentStrategySymbol} Settings`;
        subtitleEl.textContent = 'Adjust active bot strategy parameters';
        actionBtn.textContent = 'Save Settings 💾';
        actionBtn.className = "w-full bg-gradient-to-r from-indigo-600 to-purple-600 text-white font-black py-4 rounded-2xl hover:shadow-lg hover:shadow-indigo-500/30 transition-all active:scale-[0.98] uppercase tracking-[0.2em] shadow-md flex items-center justify-center gap-2";
        actionBtn.disabled = false;

        const riskContainer = document.getElementById('deployRiskAgreementContainer');
        if (riskContainer) riskContainer.classList.add('hidden');

        // Load existing settings
        const settings = editSettings || getSavedBotSettings(currentStrategySymbol);
        setStrategyDraftIndicatorOverrides(settings.indicator_overrides || {});
        document.getElementById('strategyCapital').value = settings.amount || '';
        if (document.getElementById('strategyMaxTrades')) {
            const savedMaxTrades = Number(settings.max_trades || 0);
            document.getElementById('strategyMaxTrades').value = savedMaxTrades > 0 ? Math.min(100, savedMaxTrades) : '';
        }
        if (document.getElementById('strategyCapital_ai_autopilot')) document.getElementById('strategyCapital_ai_autopilot').value = settings.amount || '';
        if (document.getElementById('strategyOptionsTradeAmount')) document.getElementById('strategyOptionsTradeAmount').value = settings.options_trade_amount !== undefined ? settings.options_trade_amount : 100;
        if (document.getElementById('strategyAllocationMode_regular')) document.getElementById('strategyAllocationMode_regular').value = settings.allocation_mode || 'fixed';
        if (document.getElementById('strategyAllocationMode_options')) document.getElementById('strategyAllocationMode_options').value = settings.options_allocation_mode || settings.allocation_mode || 'fixed';
        if (document.getElementById('strategyAllocationMode_ai_autopilot')) document.getElementById('strategyAllocationMode_ai_autopilot').value = settings.allocation_mode || 'fixed';
        window.updateAllocationModeUI?.('regular', settings);
        window.updateAllocationModeUI?.('options', settings);
        window.updateAllocationModeUI?.('ai_autopilot', settings);
        const buySignals = settings.min_buy_signals !== undefined ? settings.min_buy_signals : (INDICATOR_DEFAULTS['MIN_BULLISH_SIGNALS'] || 4);
        document.getElementById('strategyBuyThreshold_regular').value = buySignals;
        document.getElementById('strategyBuyThreshold_options').value = buySignals;
        const sellSignals = settings.min_sell_signals !== undefined ? settings.min_sell_signals : (INDICATOR_DEFAULTS['MIN_BEARISH_SIGNALS'] || 4);
        document.getElementById('strategySellThreshold_regular').value = sellSignals;
        document.getElementById('strategySellThreshold_options').value = sellSignals;
        document.getElementById('strategyTimeframe_regular').value = settings.timeframe || '1Hour';
        document.getElementById('strategyTimeframe_options').value = settings.timeframe || '1Hour';
        if (document.getElementById('strategyTimeframe_ai_autopilot')) document.getElementById('strategyTimeframe_ai_autopilot').value = settings.timeframe || '1Hour';
        if (document.getElementById('strategyBuyMode_regular')) document.getElementById('strategyBuyMode_regular').value = settings.buy_mode || 'indicator';
        if (document.getElementById('strategyBuyMode_options')) document.getElementById('strategyBuyMode_options').value = settings.buy_mode || 'indicator';
        if (document.getElementById('strategyCustomBuyPrice_regular')) document.getElementById('strategyCustomBuyPrice_regular').value = settings.custom_buy_price || '';
        if (document.getElementById('strategyCustomBuyPrice_options')) document.getElementById('strategyCustomBuyPrice_options').value = settings.custom_buy_price || '';
        if (document.getElementById('strategyCustomStopLossPrice_regular')) document.getElementById('strategyCustomStopLossPrice_regular').value = settings.custom_stop_loss_price || '';
        if (document.getElementById('strategyCustomTakeProfitPrice_regular')) document.getElementById('strategyCustomTakeProfitPrice_regular').value = settings.custom_take_profit_price || '';
        if (document.getElementById('strategyCustomStopLossPrice_options')) document.getElementById('strategyCustomStopLossPrice_options').value = settings.custom_stop_loss_price || '';
        if (document.getElementById('strategyCustomTakeProfitPrice_options')) document.getElementById('strategyCustomTakeProfitPrice_options').value = settings.custom_take_profit_price || '';
        if (document.getElementById('strategyCustomUnderlyingStopLossPrice_options')) document.getElementById('strategyCustomUnderlyingStopLossPrice_options').value = settings.custom_underlying_stop_loss_price || '';
        if (document.getElementById('strategyCustomUnderlyingTakeProfitPrice_options')) document.getElementById('strategyCustomUnderlyingTakeProfitPrice_options').value = settings.custom_underlying_take_profit_price || '';
        if (document.getElementById('strategyEntryAtrMult_regular')) document.getElementById('strategyEntryAtrMult_regular').value = settings.entry_atr_multiplier !== undefined ? settings.entry_atr_multiplier : (settings.atr_stop_multiplier !== undefined ? settings.atr_stop_multiplier : 2);
        if (document.getElementById('strategyEntryAtrMult_options')) document.getElementById('strategyEntryAtrMult_options').value = settings.entry_atr_multiplier !== undefined ? settings.entry_atr_multiplier : (settings.atr_stop_multiplier !== undefined ? settings.atr_stop_multiplier : 2);
        if (document.getElementById('strategyEntryPctMove_regular')) document.getElementById('strategyEntryPctMove_regular').value = settings.entry_pct_move !== undefined ? settings.entry_pct_move : Math.max(1, Math.round((settings.regular_stop_loss_pct !== undefined ? settings.regular_stop_loss_pct : 0.05) * 100));
        if (document.getElementById('strategyEntryPctMove_options')) document.getElementById('strategyEntryPctMove_options').value = settings.entry_pct_move !== undefined ? settings.entry_pct_move : Math.max(1, Math.round((settings.regular_stop_loss_pct !== undefined ? settings.regular_stop_loss_pct : 0.05) * 100));
        if (document.getElementById('strategyEntryAtrStyle_regular')) document.getElementById('strategyEntryAtrStyle_regular').value = settings.entry_atr_style || 'pullback_reclaim';
        if (document.getElementById('strategyEntryAtrStyle_options')) document.getElementById('strategyEntryAtrStyle_options').value = settings.entry_atr_style || 'pullback_reclaim';
        if (document.getElementById('strategyEntryPctStyle_regular')) document.getElementById('strategyEntryPctStyle_regular').value = settings.entry_pct_style || 'dip_reclaim';
        if (document.getElementById('strategyEntryPctStyle_options')) document.getElementById('strategyEntryPctStyle_options').value = settings.entry_pct_style || 'dip_reclaim';

        ['regular', 'options'].forEach(m => {
            const gridCont = document.getElementById(`strategyGridContinuous_${m}`);
            if (gridCont) {
                gridCont.checked = m === 'regular' && !!settings.grid_continuous_mode;
                // Manually trigger the onchange to show/hide the fields
                const ev = new Event('change');
                gridCont.dispatchEvent(ev);
            }
            if (document.getElementById(`strategyGridBuyAllocation_${m}`)) document.getElementById(`strategyGridBuyAllocation_${m}`).value = 20;
            if (document.getElementById(`strategyGridMaxLevels_${m}`)) document.getElementById(`strategyGridMaxLevels_${m}`).value = settings.grid_max_levels ?? 5;
            const entryDefaults = [1, 2, 3, 4, 5];
            for (let i = 1; i <= 5; i++) {
                const el = document.getElementById(`strategyGridEntryL${i}_${m}`);
                if (el) el.value = settings[`grid_entry_l${i}_pct`] !== undefined ? settings[`grid_entry_l${i}_pct`] : entryDefaults[i - 1];
                const buyPctEl = document.getElementById(`strategyGridEntryL${i}BuyPct_${m}`);
                if (buyPctEl) buyPctEl.value = settings[`grid_entry_l${i}_buy_pct`] ?? [40, 25, 15, 10, 10][i - 1];
                const enabledEl = document.getElementById(`strategyGridEntryL${i}Enabled_${m}`);
                if (enabledEl) enabledEl.checked = settings[`grid_entry_l${i}_enabled`] ?? (i <= (settings.grid_max_levels ?? 5));
            }
            if (window.updateGridLevelInputs) window.updateGridLevelInputs(m, 'entry');

            const exitGridCont = document.getElementById(`strategyExitGridContinuous_${m}`);
            if (exitGridCont) {
                exitGridCont.checked = m === 'regular' && !!settings.exit_grid_continuous_mode;
                const ev = new Event('change');
                exitGridCont.dispatchEvent(ev);
            }
            if (document.getElementById(`strategyExitGridSellPct_${m}`)) document.getElementById(`strategyExitGridSellPct_${m}`).value = settings.exit_grid_sell_pct ?? 20;
            if (document.getElementById(`strategyExitGridMaxLevels_${m}`)) document.getElementById(`strategyExitGridMaxLevels_${m}`).value = settings.exit_grid_max_levels ?? 5;
            const savedExitTriggers = Array.from({ length: 5 }, (_, index) =>
                Number(settings[`grid_exit_l${index + 1}_pct`] ?? [2, 4, 6, 8, 10][index])
            );
            const savedExitSizes = Array.from({ length: 5 }, (_, index) =>
                Number(settings[`grid_exit_l${index + 1}_sell_pct`] ?? [10, 10, 15, 20, 20][index])
            );
            const legacyLadder = Number(settings.continuous_grid_ladder_version || 0) < 2;
            const migrateLegacyTriggers = legacyLadder
                && savedExitTriggers.every((value, index) => Math.abs(value - [2, 4, 6, 8, 10][index]) < 0.000001);
            const migrateLegacySizes = legacyLadder && (
                savedExitSizes.every((value, index) => Math.abs(value - [25, 25, 25, 25, 100][index]) < 0.000001)
                || savedExitSizes.every(value => Math.abs(value - 25) < 0.000001)
            );
            const exitDefaults = [1, 2, 3, 4, 5];
            for (let i = 1; i <= 5; i++) {
                const el = document.getElementById(`strategyGridExitL${i}_${m}`);
                if (el) el.value = migrateLegacyTriggers
                    ? exitDefaults[i - 1]
                    : (settings[`grid_exit_l${i}_pct`] !== undefined ? settings[`grid_exit_l${i}_pct`] : exitDefaults[i - 1]);
                const sellEl = document.getElementById(`strategyGridExitL${i}SellPct_${m}`);
                if (sellEl) sellEl.value = migrateLegacySizes
                    ? [10, 10, 15, 20, 20][i - 1]
                    : (settings[`grid_exit_l${i}_sell_pct`] ?? settings.exit_grid_sell_pct ?? [10, 10, 15, 20, 20][i - 1]);
                const enabledEl = document.getElementById(`strategyGridExitL${i}Enabled_${m}`);
                if (enabledEl) enabledEl.checked = settings[`grid_exit_l${i}_enabled`] ?? (i <= (settings.exit_grid_max_levels ?? 5));
            }
            if (window.updateGridLevelInputs) window.updateGridLevelInputs(m, 'exit');
        });
        loadContinuousAtrGridSettings(settings);
        loadSmartDynamicGridSettings(settings, false);

        applyMarketSessionForSymbol(currentStrategySymbol, settings);
        if (document.getElementById('strategySellMode_regular')) if (document.getElementById('strategySellMode_regular')) document.getElementById('strategySellMode_regular').value = settings.sell_mode || 'indicator';
        if (document.getElementById('strategySwingEntryMode_regular')) document.getElementById('strategySwingEntryMode_regular').value = settings.swing_entry_mode || 'bounce_retest';
        if (document.getElementById('strategySwingEntryMode_options')) document.getElementById('strategySwingEntryMode_options').value = settings.swing_entry_mode || 'bounce_retest';
        const entrySwingDays = parseInt(settings.swing_entry_lookback_days ?? settings.swing_lookback_days ?? 20);
        const entrySwingMode = settings.swing_entry_lookback_mode || settings.swing_lookback_mode || (entrySwingDays === 5 ? '5D' : entrySwingDays === 20 ? '20D' : entrySwingDays === 60 ? '60D' : entrySwingDays === 120 ? '120D' : 'custom');
        if (document.getElementById('strategySwingEntryLookback_regular')) document.getElementById('strategySwingEntryLookback_regular').value = entrySwingMode;
        if (document.getElementById('strategySwingEntryLookback_options')) document.getElementById('strategySwingEntryLookback_options').value = entrySwingMode;
        if (document.getElementById('strategySwingEntryLookbackCustom_regular')) document.getElementById('strategySwingEntryLookbackCustom_regular').value = entrySwingDays;
        if (document.getElementById('strategySwingEntryLookbackCustom_options')) document.getElementById('strategySwingEntryLookbackCustom_options').value = entrySwingDays;

        const exitSwingDays = parseInt(settings.swing_exit_lookback_days ?? settings.swing_lookback_days ?? entrySwingDays ?? 20);
        const exitSwingMode = settings.swing_exit_lookback_mode || settings.swing_lookback_mode || (exitSwingDays === 5 ? '5D' : exitSwingDays === 20 ? '20D' : exitSwingDays === 60 ? '60D' : exitSwingDays === 120 ? '120D' : 'custom');
        if (document.getElementById('strategySwingExitLookback_regular')) document.getElementById('strategySwingExitLookback_regular').value = exitSwingMode;
        if (document.getElementById('strategySwingExitLookback_options')) document.getElementById('strategySwingExitLookback_options').value = exitSwingMode;
        if (document.getElementById('strategySwingExitLookbackCustom_regular')) document.getElementById('strategySwingExitLookbackCustom_regular').value = exitSwingDays;
        if (document.getElementById('strategySwingExitLookbackCustom_options')) document.getElementById('strategySwingExitLookbackCustom_options').value = exitSwingDays;
        window._strategyPaused = settings.paused || false;

        const aiAutopilot = settings.ai_autopilot || false;
        const optEnabled = settings.options_trading || false;
        if (typeof populateStrategyAiModelSelect === 'function') populateStrategyAiModelSelect();
        const aiModelSelect = document.getElementById('strategyAiModel');
        if (aiModelSelect) {
            aiModelSelect.value = normalizeBotAIModel(settings.ai_model || 'llama-3.3-70b-versatile');
        }

        if (aiAutopilot) {
            window.toggleModeActive('ai_autopilot');
        } else if (optEnabled) {
            window.toggleModeActive('options');
        } else {
            window.toggleModeActive('regular');
        }
        const savedOptionsType = settings.options_type === 'puts_only' ? 'puts_only' : 'calls_only';
        const optionsTypeEl = document.getElementById('strategyOptionsType');
        optionsTypeEl.value = savedOptionsType;
        if (settings.options_type === 'both') {
            optionsTypeEl.dataset.legacyBoth = 'true';
            const directionHint = document.getElementById('strategyOptionsDirectionHint');
            if (directionHint) directionHint.textContent = 'Legacy Both direction is disabled. Choose CALL or PUT before saving this bot.';
        } else {
            delete optionsTypeEl.dataset.legacyBoth;
        }
        document.getElementById('strategyOptionsTradeAmount').value = settings.options_trade_amount !== undefined ? settings.options_trade_amount : 100;
        document.getElementById('strategyOptionsExpiryDays').value = settings.options_expiry_days !== undefined ? settings.options_expiry_days : 7;
        document.getElementById('strategyOptionsStrikeType').value = settings.options_strike_type || 'ATM';
        if (document.getElementById('strategySellMode_options')) document.getElementById('strategySellMode_options').value = settings.sell_mode_options || settings.options_exit_strategy || settings.sell_mode || 'hybrid';
        if (document.getElementById('strategyOptionsSlPct')) document.getElementById('strategyOptionsSlPct').value = settings.options_stop_loss_pct !== undefined ? Math.round(settings.options_stop_loss_pct * 100) : OPTIONS_DEFAULT_SL_PCT;
        if (document.getElementById('strategyOptionsTpPct')) document.getElementById('strategyOptionsTpPct').value = settings.options_take_profit_pct !== undefined ? Math.round(settings.options_take_profit_pct * 100) : OPTIONS_DEFAULT_TP_PCT;

        // Load Smart Re-entry and Signal Reset
        ['regular', 'options'].forEach(m => {
            const capMode = m === 'regular' ? 'Regular' : 'Options';
            if (document.getElementById(`strategy${capMode}SignalReset`)) document.getElementById(`strategy${capMode}SignalReset`).checked = settings.enable_signal_reset !== undefined ? settings.enable_signal_reset : true;
            if (document.getElementById(`strategy${capMode}SmartReentry`)) document.getElementById(`strategy${capMode}SmartReentry`).checked = settings.enable_smart_reentry || false;
            if (document.getElementById(`strategy${capMode}SmartReentryType`)) document.getElementById(`strategy${capMode}SmartReentryType`).value = settings.smart_reentry_type || 'RSI';
            if (document.getElementById(`strategy${capMode}RsiCallMax`)) document.getElementById(`strategy${capMode}RsiCallMax`).value = settings.smart_reentry_rsi_call_max !== undefined ? settings.smart_reentry_rsi_call_max : 70;
            if (document.getElementById(`strategy${capMode}RsiPutMin`)) document.getElementById(`strategy${capMode}RsiPutMin`).value = settings.smart_reentry_rsi_put_min !== undefined ? settings.smart_reentry_rsi_put_min : 30;
            if (document.getElementById(`strategy${capMode}VwapCallMaxPct`)) document.getElementById(`strategy${capMode}VwapCallMaxPct`).value = settings.smart_reentry_vwap_call_max_dist_pct !== undefined ? settings.smart_reentry_vwap_call_max_dist_pct : 0.5;
            if (document.getElementById(`strategy${capMode}VwapPutMaxPct`)) document.getElementById(`strategy${capMode}VwapPutMaxPct`).value = settings.smart_reentry_vwap_put_max_dist_pct !== undefined ? settings.smart_reentry_vwap_put_max_dist_pct : 0.5;
        });
        ['regular', 'options'].forEach(mode => {
            if (document.getElementById(`strategyPostStopBars_${mode}`)) document.getElementById(`strategyPostStopBars_${mode}`).value = settings.post_stop_stabilization_bars ?? 3;
            if (document.getElementById(`strategyMaxConsecutiveLosses_${mode}`)) document.getElementById(`strategyMaxConsecutiveLosses_${mode}`).value = 3;
        });
        if (document.getElementById('strategyStockMaxSpreadPct')) document.getElementById('strategyStockMaxSpreadPct').value = settings.stock_max_spread_pct ?? 0.25;
        if (document.getElementById('strategyCryptoMaxSpreadPct')) document.getElementById('strategyCryptoMaxSpreadPct').value = settings.crypto_max_spread_pct ?? 0.50;
        if (document.getElementById('strategyOptionsMaxSpreadPct')) document.getElementById('strategyOptionsMaxSpreadPct').value = settings.options_max_entry_spread_pct ?? 5;
        if (document.getElementById('strategyOptionsOpenBlackoutMinutes')) document.getElementById('strategyOptionsOpenBlackoutMinutes').value = settings.options_market_open_blackout_minutes ?? 15;
        window.updateStrategySmartReentryFields('regular');
        window.updateStrategySmartReentryFields('options');

        // Load Risk Overrides
        const hasRisk = (settings.risk_per_trade !== undefined && settings.risk_per_trade !== null);

        document.getElementById('strategyEnableRisk_regular').checked = hasRisk;
        document.getElementById('strategyEnableRisk_options').checked = hasRisk;
        window.toggleRiskControls(hasRisk, 'regular');
        window.toggleRiskControls(hasRisk, 'options');

        document.getElementById('strategyRiskPerTrade').value = hasRisk ? (settings.risk_per_trade * 100).toFixed(0) : 10;
        document.getElementById('strategyMaxDailyDrawdown').value = hasRisk ? (settings.max_daily_drawdown * 100).toFixed(0) : 50;
        document.getElementById('strategyMaxPositionPct').value = hasRisk ? (settings.max_position_pct * 100).toFixed(0) : 100;

        // Also fix options inputs
        if (document.getElementById('strategyOptionsRiskPerTrade')) {
            document.getElementById('strategyOptionsRiskPerTrade').value = hasRisk ? (settings.risk_per_trade * 100).toFixed(0) : 30;
            document.getElementById('strategyOptionsMaxDailyDrawdown').value = hasRisk ? (settings.max_daily_drawdown * 100).toFixed(0) : 60;
            document.getElementById('strategyOptionsMaxPositionPct').value = hasRisk ? (settings.max_position_pct * 100).toFixed(0) : 100;
        }

        document.getElementById('strategyAtrStopMult').value = settings.atr_stop_multiplier !== undefined ? settings.atr_stop_multiplier : (INDICATOR_DEFAULTS['ATR_STOP_MULTIPLIER'] || 2.0);
        document.getElementById('strategyAtrTrailMult').value = settings.atr_trail_multiplier !== undefined ? settings.atr_trail_multiplier : (INDICATOR_DEFAULTS['ATR_TRAIL_MULTIPLIER'] || 3.0);
        document.getElementById('strategyAtrTpMult').value = settings.take_profit_multiplier !== undefined ? settings.take_profit_multiplier : (INDICATOR_DEFAULTS['ATR_TAKE_PROFIT_MULTIPLIER'] || 4.0);
        if (document.getElementById('strategyEnableAtrTrail')) document.getElementById('strategyEnableAtrTrail').checked = settings.enable_atr_trail !== undefined ? settings.enable_atr_trail : true;
        if (document.getElementById('strategyEnablePctTrail')) document.getElementById('strategyEnablePctTrail').checked = settings.enable_pct_trail !== undefined ? settings.enable_pct_trail : true;
        if (document.getElementById('strategyAtrStopMult_options')) document.getElementById('strategyAtrStopMult_options').value = settings.atr_stop_multiplier !== undefined ? settings.atr_stop_multiplier : (INDICATOR_DEFAULTS['ATR_STOP_MULTIPLIER'] || 2.0);
        if (document.getElementById('strategyAtrTrailMult_options')) document.getElementById('strategyAtrTrailMult_options').value = settings.atr_trail_multiplier !== undefined ? settings.atr_trail_multiplier : (INDICATOR_DEFAULTS['ATR_TRAIL_MULTIPLIER'] || 3.0);
        if (document.getElementById('strategyAtrTpMult_options')) document.getElementById('strategyAtrTpMult_options').value = settings.take_profit_multiplier !== undefined ? settings.take_profit_multiplier : (INDICATOR_DEFAULTS['ATR_TAKE_PROFIT_MULTIPLIER'] || 4.0);
        if (document.getElementById('strategyEnableAtrTrail_options')) document.getElementById('strategyEnableAtrTrail_options').checked = settings.enable_atr_trail !== undefined ? settings.enable_atr_trail : true;
        if (document.getElementById('strategyEnablePctTrail_options')) document.getElementById('strategyEnablePctTrail_options').checked = settings.enable_pct_trail !== undefined ? settings.enable_pct_trail : true;
        if (document.getElementById('strategyOptionsTrailPct')) document.getElementById('strategyOptionsTrailPct').value = settings.options_trailing_pct !== undefined ? Math.round(settings.options_trailing_pct * 100) : OPTIONS_DEFAULT_TRAIL_PCT;
        if (document.getElementById('strategyRegularSlPct')) document.getElementById('strategyRegularSlPct').value = settings.regular_stop_loss_pct !== undefined ? Math.round(settings.regular_stop_loss_pct * 100) : 5;
        if (document.getElementById('strategyRegularTpPct')) document.getElementById('strategyRegularTpPct').value = settings.regular_take_profit_pct !== undefined ? Math.round(settings.regular_take_profit_pct * 100) : 10;
        if (document.getElementById('strategyRegularTrailPct')) document.getElementById('strategyRegularTrailPct').value = settings.regular_trailing_pct !== undefined ? Math.round(settings.regular_trailing_pct * 100) : 3;

        // Enable Cooldown logic
        const enableCooldown = settings.enable_cooldown !== undefined ? settings.enable_cooldown : true;
        document.getElementById('strategyEnableCooldown_regular').checked = enableCooldown;
        document.getElementById('strategyEnableCooldown_options').checked = enableCooldown;
        document.getElementById('strategyCooldownSettingsWrapper_regular').classList.toggle('hidden', !enableCooldown);
        document.getElementById('strategyCooldownSettingsWrapper_options').classList.toggle('hidden', !enableCooldown);

        let cooldownVal = settings.cooldown_minutes;
        const standardCooldowns = [5, 15, 30, 60, 240];
        if (cooldownVal === undefined || cooldownVal === null || cooldownVal === "default") {
            document.getElementById('strategyCooldownMinutes_regular').value = "default";
            document.getElementById('strategyCooldownMinutes_options').value = "default";
            document.getElementById('strategyCooldownCustomContainer_regular').classList.add('hidden');
            document.getElementById('strategyCooldownCustomContainer_options').classList.add('hidden');
            document.getElementById('strategyCooldownCustomMinutes_regular').value = "";
            document.getElementById('strategyCooldownCustomMinutes_options').value = "";
        } else if (standardCooldowns.includes(cooldownVal)) {
            document.getElementById('strategyCooldownMinutes_regular').value = cooldownVal.toString();
            document.getElementById('strategyCooldownMinutes_options').value = cooldownVal.toString();
            document.getElementById('strategyCooldownCustomContainer_regular').classList.add('hidden');
            document.getElementById('strategyCooldownCustomContainer_options').classList.add('hidden');
            document.getElementById('strategyCooldownCustomMinutes_regular').value = "";
            document.getElementById('strategyCooldownCustomMinutes_options').value = "";
        } else {
            document.getElementById('strategyCooldownMinutes_regular').value = "custom";
            document.getElementById('strategyCooldownMinutes_options').value = "custom";
            document.getElementById('strategyCooldownCustomContainer_regular').classList.remove('hidden');
            document.getElementById('strategyCooldownCustomContainer_options').classList.remove('hidden');
            document.getElementById('strategyCooldownCustomMinutes_regular').value = cooldownVal;
            document.getElementById('strategyCooldownCustomMinutes_options').value = cooldownVal;
        }



        const enabledIndicators = settings.indicators;
        document.querySelectorAll('.strategy-indicator-check').forEach(chk => {
            if (enabledIndicators === undefined) {
                chk.checked = false;
            } else {
                chk.checked = enabledIndicators.includes(chk.value);
            }
        });

        const inWatchlist = (window.lastBotsData?.watchlist || []).includes(currentStrategySymbol);
        const addWatchlistEl = document.getElementById('strategyAddWatchlist');
        if (addWatchlistEl) addWatchlistEl.checked = inWatchlist;
    }

    // Run crypto option status check to disable if it is a crypto coin
    window.checkStrategyCryptoOptionStatus(currentStrategySymbol);

    const modes = ['regular', 'options'];
    modes.forEach(mode => {
        const tfEl = document.getElementById(`strategyTimeframe_${mode}`);
        if (tfEl) {
            tfEl.removeEventListener('change', updateDefaultCooldownLabel);
            tfEl.addEventListener('change', updateDefaultCooldownLabel);
        }
    });
    updateDefaultCooldownLabel();

    ['regular', 'options'].forEach(mode => updateStrategySignalLabels(mode));
    if (typeof window.updateRegularExitFields === 'function') window.updateRegularExitFields();
    if (typeof window.updateRegularEntryFields === 'function') window.updateRegularEntryFields();
    if (typeof window.updateOptionsExitFields === 'function') window.updateOptionsExitFields();
    if (typeof window.updateOptionsEntryFields === 'function') window.updateOptionsEntryFields();
    if (typeof window.updateSwingSettingsVisibility === 'function') {
        window.updateSwingSettingsVisibility('regular');
        window.updateSwingSettingsVisibility('options');
    }
    if (typeof window.updateStrategySmartReentryFields === 'function') window.updateStrategySmartReentryFields();
    updateStrategyPauseButton();
    modal.classList.remove('hidden');

    // Load TV Chart
    const tvContainer = document.getElementById('tv_strategy_chart');
    if (tvContainer) {
        tvContainer.innerHTML = '';
        const script = document.createElement('script');
        script.src = 'https://s3.tradingview.com/external-embedding/embed-widget-mini-symbol-overview.js';
        script.async = true;
        script.innerHTML = JSON.stringify({
            "symbol": isCrypto(currentStrategySymbol) && !currentStrategySymbol.includes('^') ? `CRYPTO:${currentStrategySymbol}` : currentStrategySymbol,
            "width": "100%",
            "height": "100%",
            "locale": "en",
            "dateRange": "1D",
            "colorTheme": "light",
            "isTransparent": true,
            "autosize": true,
            "largeChartUrl": ""
        });
        tvContainer.appendChild(script);
    }
};

window.closeStrategyModal = function () {
    document.getElementById('botStrategyModal').classList.add('hidden');
    if (botChartPreviewState.source === 'settings') {
        closeBotChartPreview();
    }
    setStrategyDraftIndicatorOverrides({});
    currentStrategySymbol = null;
    window.currentStrategySymbol = null;
};

window.toggleDeployButton = function () {
    const riskCheck = document.getElementById('riskAgreementCheckbox');
    const actionBtn = document.getElementById('strategyActionButton');
    if (actionBtn) {
        if (strategyModalMode === 'deploy') {
            actionBtn.disabled = !window.activeTradingMode || (riskCheck && !riskCheck.checked);
        } else {
            actionBtn.disabled = !window.activeTradingMode;
        }
    }
};

window.openRiskAgreementModal = function () {
    const modal = document.getElementById('riskAgreementModal');
    if (modal) modal.classList.remove('hidden');
};

window.closeRiskAgreementModal = function () {
    const modal = document.getElementById('riskAgreementModal');
    if (modal) modal.classList.add('hidden');
};

window.updateStrategySignalLabels = function (mode = window.activeTradingMode || 'regular') {
    if (!mode) return;
    const container = document.getElementById(`indicatorChecksContainer_${mode}`);
    if (!container) return;
    const selectedIndicators = getSelectedStrategyIndicators(mode);
    const checkedCount = selectedIndicators.length;
    const selectedCountBadge = document.getElementById(`strategyIndicatorSelectedCount_${mode}`);
    if (selectedCountBadge) {
        selectedCountBadge.textContent = `${checkedCount} selected for this bot`;
        selectedCountBadge.title = selectedIndicators.length
            ? selectedIndicators.join(', ')
            : 'No indicators selected';
    }
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
        sellInput.min = 0;
        buyInput.max = 0;
        sellInput.max = 0;
        buyInput.value = 0;
        sellInput.value = 0;
        buyLabel.textContent = buyNeedsInd ? "0 of 0 Selected Signals" : "0 of 0 Selected Signals (Not Required)";
        sellLabel.textContent = sellNeedsInd ? "0 of 0 Selected Signals" : "0 of 0 Selected Signals (Not Required)";
        buyLabel.title = '';
        sellLabel.title = '';
        if (mode === 'options') syncOptionsPutEntryThresholdLabel(checkedCount);
        return;
    }

    buyInput.min = buyMin;
    sellInput.min = sellMin;
    buyInput.max = checkedCount;
    sellInput.max = checkedCount;

    buyInput.value = normalizeSignalThreshold(buyInput.value, checkedCount, 'buy', buyMin);
    sellInput.value = normalizeSignalThreshold(sellInput.value, checkedCount, 'sell', sellMin);

    buyLabel.textContent = (!buyNeedsInd && parseInt(buyInput.value) === 0) ? `0 of ${checkedCount} Selected Signals (Not Required)` : `${buyInput.value} of ${checkedCount} Selected Signals`;
    sellLabel.textContent = (!sellNeedsInd && parseInt(sellInput.value) === 0) ? `0 of ${checkedCount} Selected Signals (Not Required)` : `${sellInput.value} of ${checkedCount} Selected Signals`;
    const selectedNames = selectedIndicators.join(', ');
    buyLabel.title = selectedNames;
    sellLabel.title = selectedNames;
    if (mode === 'options') syncOptionsPutEntryThresholdLabel(checkedCount);
};

function syncOptionsPutEntryThresholdLabel(checkedCount = null) {
    const putInput = document.getElementById('strategyPutEntryThreshold_options');
    const putLabel = document.getElementById('strategyPutEntryLabel_options');
    const sellInput = document.getElementById('strategySellThreshold_options');
    if (!putInput || !putLabel || !sellInput) return;

    if (checkedCount === null) {
        checkedCount = getSelectedStrategyIndicators('options').length;
    }

    putInput.min = sellInput.min;
    putInput.max = sellInput.max;
    putInput.value = sellInput.value;
    const indicatorModes = ['indicator', 'hybrid', 'hybrid_atr', 'hybrid_pct', 'hybrid_pivot', 'hybrid_high_low', 'hybrid_swing'];
    const sellMode = document.getElementById('strategySellMode_options')?.value || 'hybrid';
    const needsInd = indicatorModes.includes(sellMode);
    putLabel.textContent = (!needsInd && parseInt(putInput.value) === 0) ? `0 of ${checkedCount} Selected Signals (Not Required)` : `${putInput.value} of ${checkedCount} Selected Signals`;
    putLabel.title = getSelectedStrategyIndicators('options').join(', ');
}

window.syncOptionsPutEntryThreshold = function () {
    const putInput = document.getElementById('strategyPutEntryThreshold_options');
    const sellInput = document.getElementById('strategySellThreshold_options');
    const container = document.getElementById('indicatorChecksContainer_options');
    if (!putInput || !sellInput || !container) return;

    const checkedCount = getSelectedStrategyIndicators('options').length;
    sellInput.value = normalizeSignalThreshold(putInput.value, checkedCount, 'sell');
    window.updateStrategySignalLabels('options');
};

window.adjustOptionsPutEntryThreshold = function (delta) {
    window.adjustStrategyThreshold('options', 'sell', delta);
    syncOptionsPutEntryThresholdLabel();
    if (window.updateLivePreview_bots) window.updateLivePreview_bots('options', 'entry');
};

window.adjustStrategyThreshold = function (mode, type, delta) {
    const container = document.getElementById(`indicatorChecksContainer_${mode}`);
    if (!container) return;
    const checkedCount = getSelectedStrategyIndicators(mode).length;
    if (checkedCount === 0) return;

    const input = type === 'buy' ? document.getElementById(`strategyBuyThreshold_${mode}`) : document.getElementById(`strategySellThreshold_${mode}`);
    if (!input) return;

    const indicatorModes = ['indicator', 'hybrid', 'hybrid_atr', 'hybrid_pct', 'hybrid_pivot', 'hybrid_high_low', 'hybrid_swing'];
    const modeSelect = type === 'buy'
        ? (document.getElementById(`strategyBuyMode_${mode}`)?.value || 'indicator')
        : (document.getElementById(`strategySellMode_${mode}`)?.value || (mode === 'options' ? 'hybrid' : 'indicator'));
    const minVal = indicatorModes.includes(modeSelect) ? 1 : 0;

    let val = normalizeSignalThreshold(input.value, checkedCount, type, minVal) + delta;
    val = Math.max(minVal, Math.min(val, checkedCount));
    input.value = val;
    window.updateStrategySignalLabels(mode);
};

window.toggleRiskSection = function () {
    const content = document.getElementById('riskControlsContent');
    const arrow = document.getElementById('riskArrowIcon');
    if (content.classList.contains('hidden')) {
        content.classList.remove('hidden');
        if (arrow) arrow.classList.add('rotate-180');
    } else {
        content.classList.add('hidden');
        if (arrow) arrow.classList.remove('rotate-180');
    }
};

window.togglePauseInStrategyModal = function () {
    window._strategyPaused = !window._strategyPaused;
    updateStrategyPauseButton();
};

function updateStrategyPauseButton() {
    const btn = document.getElementById('strategyPauseBtn');
    if (!btn) return;
    if (window._strategyPaused) {
        btn.textContent = '⏸ Paused';
        btn.className = 'px-6 py-2 rounded-xl font-black text-xs uppercase tracking-wider transition-all bg-amber-500/10 text-amber-700 border border-amber-500/25 backdrop-blur-sm hover:bg-amber-500/20 shadow-sm shadow-amber-500/5';
    } else {
        btn.textContent = '▶ Active';
        btn.className = 'px-6 py-2 rounded-xl font-black text-xs uppercase tracking-wider transition-all bg-emerald-500/10 text-emerald-700 border border-emerald-500/25 backdrop-blur-sm hover:bg-emerald-500/20 shadow-sm shadow-emerald-500/5';
    }
}



function populateStrategyAiModelSelect() {
    const select = document.getElementById('strategyAiModel');
    if (!select) return;
    const currentValue = normalizeBotAIModel(select.value);

    let html = '';
    html += '<optgroup label="Groq AI (Cloud)">';
    html += '  <option value="llama-3.3-70b-versatile">Llama 3.3 70B (Recommended)</option>';
    html += '  <option value="llama-3.1-8b-instant">Llama 3.1 8B (Fast / Lightweight)</option>';
    html += '</optgroup>';
    html += '<optgroup label="Ollama (Local)">';
    if (window.ollamaModels && window.ollamaModels.length > 0) {
        window.ollamaModels.forEach(m => {
            html += `  <option value="ollama/${m}">${m}</option>`;
        });
    } else {
        html += '  <option disabled>No local models found</option>';
    }
    html += '</optgroup>';
    select.innerHTML = html;

    if (currentValue) {
        select.value = currentValue;
    }
}

window.handleStrategyAutopilotToggle = function (enabled) {
    const section = document.getElementById('strategyAiAutopilotSection');
    const strategyModeSelects = [
        'strategyBuyMode_regular',
        'strategySellMode_regular',
        'strategyBuyMode_options',
        'strategySellMode_options'
    ].map(id => document.getElementById(id)).filter(Boolean);

    if (enabled) {
        populateStrategyAiModelSelect();
        section?.classList.remove('hidden');
        strategyModeSelects.forEach(select => {
            select.disabled = true;
            select.style.opacity = '0.5';
        });
        // Dim and disable indicators
        document.querySelectorAll('.strategy-indicator-check').forEach(chk => {
            chk.disabled = true;
            chk.parentElement.style.opacity = '0.5';
            chk.parentElement.style.pointerEvents = 'none';
        });
    } else {
        section?.classList.add('hidden');
        strategyModeSelects.forEach(select => {
            select.disabled = false;
            select.style.opacity = '1';
        });
        // Enable indicators
        document.querySelectorAll('.strategy-indicator-check').forEach(chk => {
            chk.disabled = false;
            chk.parentElement.style.opacity = '1';
            chk.parentElement.style.pointerEvents = 'auto';
        });
    }
};

window.checkStrategyCryptoOptionStatus = function (symbol) {
    const cryptoSymbol = isCrypto(symbol);
    const btnOpt = document.getElementById('btnOptionsMode');
    const tooltip = document.getElementById('optionsCryptoDisableTooltip');

    if (cryptoSymbol) {
        if (btnOpt) {
            btnOpt.disabled = true;
            btnOpt.style.opacity = '0.5';
            btnOpt.style.cursor = 'not-allowed';
            btnOpt.onmouseover = () => tooltip && tooltip.classList.remove('hidden');
            btnOpt.onmouseout = () => tooltip && tooltip.classList.add('hidden');
        }

        // Force back to regular if they were on options
        if (window.activeTradingMode === 'options') {
            window.toggleModeActive('regular');
        }
    } else {
        if (btnOpt) {
            btnOpt.disabled = false;
            btnOpt.style.opacity = '1';
            btnOpt.style.cursor = 'pointer';
            btnOpt.onmouseover = null;
            btnOpt.onmouseout = null;
        }
        if (tooltip) tooltip.classList.add('hidden');
    }
};

function validateContinuousGridSettings(symbol, mode, buyMode, sellMode) {
    const entryEnabled = !!document.getElementById(`strategyGridContinuous_${mode}`)?.checked;
    const exitEnabled = !!document.getElementById(`strategyExitGridContinuous_${mode}`)?.checked;
    const atrEntryEnabled = mode === 'regular' && !!document.getElementById('strategyAtrGridContinuous_regular')?.checked;
    const atrExitEnabled = mode === 'regular' && !!document.getElementById('strategyExitAtrGridContinuous_regular')?.checked;
    if (!entryEnabled && !exitEnabled && !atrEntryEnabled && !atrExitEnabled) return null;
    if ((entryEnabled || exitEnabled) && (atrEntryEnabled || atrExitEnabled)) {
        return { valid: false, title: '❌ Choose One Grid Type', message: 'Use either the Percentage ladder or the ATR ladder for this bot. Their saved values remain separate when you switch strategy modes.' };
    }
    if (atrEntryEnabled || atrExitEnabled) {
        if (atrEntryEnabled !== atrExitEnabled) return { valid: false, title: '❌ ATR Grid Requires Both Sides', message: 'Enable both the continuous ATR Buy and Sell ladders.' };
        if (!['atr', 'hybrid_atr'].includes(buyMode) || !['sltp', 'hybrid', 'atr', 'hybrid_atr'].includes(sellMode)) {
            return { valid: false, title: '❌ ATR Strategy Required', message: `Select ATR or Hybrid ATR Entry and ATR / Volatility or Hybrid ATR Exit for ${symbol}.` };
        }
        for (const side of ['entry', 'exit']) {
            const levels = getEnabledAtrGridLevels(side);
            if (!levels.length) return { valid: false, title: '❌ ATR Ladder Has No Levels', message: `Enable at least one ATR ${side === 'entry' ? 'buy' : 'sell'} level.` };
            const prefix = side === 'entry' ? 'strategyAtrGridEntry' : 'strategyAtrGridExit';
            const multiples = levels.map(level => Number(document.getElementById(`${prefix}L${level}Mult_regular`)?.value));
            if (multiples.some((value, index) => !Number.isFinite(value) || value < 0.1 || value > 100 || (index > 0 && value <= multiples[index - 1]))) return { valid: false, title: '❌ Invalid ATR Ladder', message: 'Enabled ATR multiples must be valid and strictly increase, for example 2, 4, 6, 8, 10.' };
            const total = levels.reduce((sum, level) => sum + Number(document.getElementById(`${prefix}L${level}${side === 'entry' ? 'Buy' : 'Sell'}Pct_regular`)?.value || 0), 0);
            if (total <= 0 || total > 100.0001) return { valid: false, title: '❌ ATR Allocation Exceeds 100%', message: `The enabled ATR ${side === 'entry' ? 'Buy' : 'Sell'} percentages must total between 1% and 100%.` };
        }
        return null;
    }
    if (mode !== 'regular') {
        return {
            valid: false,
            title: '❌ Continuous Grid Not Available for Options',
            message: 'Continuous scale-in/scale-out currently manages stock and crypto positions only. Disable it for this options bot.'
        };
    }
    if (entryEnabled !== exitEnabled) {
        return {
            valid: false,
            title: '❌ Adaptive Grid Requires Both Sides',
            message: 'Enable both Continuous Scale-In and Continuous Scale-Out. A continuous grid needs sell fills to reopen buy capacity and buy fills to rebuild the exit ladder.'
        };
    }

    const validateLevels = (side, maximum) => {
        const isEntry = side === 'entry';
        const activeLevels = getEnabledGridLevels(mode, side);
        if (!activeLevels.length) {
            return `Enable at least one ${side === 'entry' ? 'buy' : 'sell'} ladder level.`;
        }
        const values = activeLevels.map(level => {
            const id = isEntry
                ? `strategyGridEntryL${level}_${mode}`
                : `strategyGridExitL${level}_${mode}`;
            return parseFloat(document.getElementById(id)?.value);
        });
        if (values.some(value => !Number.isFinite(value) || value < 0.1 || value > maximum)) {
            return `${side === 'entry' ? 'Scale-In' : 'Scale-Out'} percentages must be between 0.1% and ${maximum}%.`;
        }
        if (values.some((value, index) => index > 0 && value <= values[index - 1])) {
            return `${side === 'entry' ? 'Scale-In' : 'Scale-Out'} percentages must increase across enabled levels (for example 1, 2, 3, 4, 5).`;
        }
        return null;
    };

    if (entryEnabled) {
        if (!['percentage', 'hybrid_pct'].includes(buyMode)) {
            return {
                valid: false,
                title: '❌ Scale-In Requires Percentage Entry',
                message: `Select Percentage or Hybrid Percentage as the Entry Strategy for ${symbol}, or disable Continuous Scale-In.`
            };
        }
        const activeLevels = getEnabledGridLevels(mode, 'entry');
        const buyPcts = activeLevels.map(level =>
            parseFloat(document.getElementById(`strategyGridEntryL${level}BuyPct_${mode}`)?.value)
        );
        if (buyPcts.some(buyPct => !Number.isFinite(buyPct) || buyPct < 1 || buyPct > 100)) {
            return {
                valid: false,
                title: '❌ Invalid Scale-In Buy %',
                message: 'Every active scale-in level must use a Buy % between 1% and 100%.'
            };
        }
        const totalBuyPct = buyPcts.reduce((sum, value) => sum + value, 0);
        if (totalBuyPct > 100.0001) {
            return {
                valid: false,
                title: '❌ Scale-In Budget Exceeds 100%',
                message: `The active Buy % levels total ${totalBuyPct.toFixed(1)}%. Reduce them to 100% or less of the bot capital.`
            };
        }
        const levelError = validateLevels('entry', 50);
        if (levelError) return { valid: false, title: '❌ Invalid Scale-In Ladder', message: levelError };
    }

    if (exitEnabled) {
        if (!['percentage', 'hybrid_pct'].includes(sellMode)) {
            return {
                valid: false,
                title: '❌ Scale-Out Requires Percentage Exit',
                message: `Select Percentage or Hybrid Percentage as the Exit Strategy for ${symbol}, or disable Continuous Scale-Out.`
            };
        }
        const activeLevels = getEnabledGridLevels(mode, 'exit');
        const sellSizes = activeLevels.map(level =>
            parseFloat(document.getElementById(`strategyGridExitL${level}SellPct_${mode}`)?.value)
        );
        if (sellSizes.some(sellPct => !Number.isFinite(sellPct) || sellPct < 1 || sellPct > 100)) {
            return {
                valid: false,
                title: '❌ Invalid Scale-Out Size',
                message: 'Every active scale-out level must use a Sell % between 1% and 100%.'
            };
        }
        const totalSellPct = sellSizes.reduce((sum, value) => sum + value, 0);
        if (totalSellPct > 100.0001) {
            return {
                valid: false,
                title: '❌ Scale-Out Allocation Exceeds 100%',
                message: `The active Sell % levels total ${totalSellPct.toFixed(1)}%. Reduce them to 100% or less of the ladder-start position.`
            };
        }
        const levelError = validateLevels('exit', 100);
        if (levelError) return { valid: false, title: '❌ Invalid Scale-Out Ladder', message: levelError };
    }
    return null;
}

window.validateStrategyPreflight = function (symbol, mode = 'regular') {
    const indicatorModes = ['indicator', 'hybrid', 'hybrid_atr', 'hybrid_pct', 'hybrid_pivot', 'hybrid_high_low', 'hybrid_swing'];
    const buyMode = document.getElementById(mode === 'options' ? 'strategyBuyMode_options' : 'strategyBuyMode_regular')?.value || 'indicator';
    const sellMode = document.getElementById(mode === 'options' ? 'strategySellMode_options' : 'strategySellMode_regular')?.value || 'hybrid';

    // 1. TOP OF FORM: Capital & Options Sizing
    if (mode === 'regular') {
        const capital = parseFloat(document.getElementById('strategyCapital')?.value || 0);
        if (!Number.isFinite(capital) || capital <= 0) {
            return {
                valid: false,
                title: "❌ Invalid Capital Allocation",
                message: `The trade capital allocated for ${symbol} must be greater than $0.00.\n\nHow to fix: Enter a valid dollar amount in the Trade Capital field at the top of the form.`
            };
        }
    } else if (mode === 'options') {
        const optAmount = parseFloat(document.getElementById('strategyOptionsTradeAmount')?.value || 0);
        const optExpiry = parseInt(document.getElementById('strategyOptionsExpiryDays')?.value || 0, 10);
        if (!Number.isFinite(optAmount) || optAmount <= 0) {
            return {
                valid: false,
                title: "❌ Invalid Options Trade Amount",
                message: `The options trade amount for ${symbol} must be greater than $0.00.\n\nHow to fix: Enter a valid dollar amount in the Options Trade Amount field.`
            };
        }
        if (!Number.isFinite(optExpiry) || optExpiry <= 0) {
            return {
                valid: false,
                title: "❌ Invalid Expiry Days",
                message: `The options expiration timeframe (Expiry Days) must be at least 1 day.\n\nHow to fix: Enter a valid number of days (e.g. 7 or 30) in the Expiry Days field.`
            };
        }
    } else if (mode === 'ai_autopilot') {
        const capital = parseFloat(document.getElementById('strategyCapital_ai_autopilot')?.value || 0);
        if (!Number.isFinite(capital) || capital <= 0) {
            return {
                valid: false,
                title: "❌ Invalid Capital Allocation",
                message: `The trade capital allocated for ${symbol} must be greater than $0.00.\n\nHow to fix: Enter a valid dollar amount in the Amount Per Trade ($) field.`
            };
        }
    }

    // 2. UPPER MIDDLE: Timeframe Validation
    const timeframe = document.getElementById(`strategyTimeframe_${mode}`)?.value;
    if (!timeframe) {
        return {
            valid: false,
            title: "❌ No Timeframe Selected",
            message: `Please select a chart timeframe (e.g., 1Hour, 5Min) for ${symbol} at the top of the strategy settings.`
        };
    }
    if (mode === 'ai_autopilot') {
        return { valid: true };
    }

    const gridValidation = validateContinuousGridSettings(symbol, mode, buyMode, sellMode);
    if (gridValidation) return gridValidation;

    // Gather Indicator checks for Entry & Exit evaluation
    const selectedIndicators = getSelectedStrategyIndicators(mode);
    const checkedIndicators = selectedIndicators.length;

    // 3. MIDDLE OF FORM: Entry Strategy Validation
    if (buyMode === 'custom_price') {
        const customBuyPrice = parseFloat(document.getElementById(`strategyCustomBuyPrice_${mode}`)?.value || 0);
        if (!Number.isFinite(customBuyPrice) || customBuyPrice <= 0) {
            return {
                valid: false,
                title: "❌ Entry Strategy Error: Invalid Custom Buy Price",
                message: `You selected Custom Price entry mode for ${symbol}, but the target buy price is set to $0.00 or blank.\n\nHow to fix: Enter a valid dollar price greater than $0.00 under the Custom Price Entry fields.`
            };
        }
    } else if (['percentage', 'hybrid_pct'].includes(buyMode)) {
        const pctMove = parseFloat(document.getElementById(`strategyEntryPctMove_${mode}`)?.value || 0);
        if (!Number.isFinite(pctMove) || pctMove < 0.1 || pctMove > 100) {
            return {
                valid: false,
                title: "❌ Entry Strategy Error: Invalid Pullback %",
                message: `Percentage entry must be between 0.1% and 100%. Enter a valid pullback value such as 3% or 5%.`
            };
        }
    }
    if (indicatorModes.includes(buyMode)) {
        if (checkedIndicators === 0) {
            const modeName = buyMode.replace(/_/g, ' ').toUpperCase();
            return {
                valid: false,
                title: "❌ Entry Strategy Error: No Indicators Selected",
                message: `You selected [${modeName}] as your Entry Strategy for ${symbol}, which requires technical indicators to generate buy signals.\n\nHowever, 0 indicators are currently checked in your Indicator Pool!\n\nHow to fix: Scroll down to the Indicator Pool checklist and check at least 1 indicator (e.g., RSI, EMA, MACD), or switch your Entry Strategy dropdown to a standalone mode like ATR / Volatility or Swing Points.`
            };
        }
        const buyThreshold = parseInt(document.getElementById(`strategyBuyThreshold_${mode}`)?.value || 1, 10);
        if (buyThreshold > checkedIndicators) {
            return {
                valid: false,
                title: "❌ Entry Strategy Error: Buy Threshold Too High",
                message: `Your Entry Strategy requires ${buyThreshold} buy signals to trigger an entry, but only ${checkedIndicators} indicator(s) are checked in your pool!\n\nHow to fix: Lower your Buy Signal Threshold to match your checked indicators (between 1 and ${checkedIndicators}), or check more indicators below.`
            };
        }
    }

    // 4. LOWER MIDDLE OF FORM: Exit Strategy Validation
    if (sellMode === 'custom_sltp') {
        const customSl = parseFloat(document.getElementById(`strategyCustomStopLossPrice_${mode}`)?.value || 0);
        const customTp = parseFloat(document.getElementById(`strategyCustomTakeProfitPrice_${mode}`)?.value || 0);
        if (!Number.isFinite(customSl) || customSl <= 0 || !Number.isFinite(customTp) || customTp <= 0) {
            return {
                valid: false,
                title: "❌ Exit Strategy Error: Invalid Custom SL/TP",
                message: `You selected Custom SL/TP exit mode for ${symbol}, but the Stop-Loss or Take-Profit price is set to $0.00 or blank.\n\nHow to fix: Enter valid dollar prices greater than $0.00 for both Stop-Loss and Take-Profit.`
            };
        }
    } else if (mode === 'options' && sellMode === 'custom_underlying_sltp') {
        const customUndSl = parseFloat(document.getElementById('strategyCustomUnderlyingStopLossPrice_options')?.value || 0);
        const customUndTp = parseFloat(document.getElementById('strategyCustomUnderlyingTakeProfitPrice_options')?.value || 0);
        if (!Number.isFinite(customUndSl) || customUndSl <= 0 || !Number.isFinite(customUndTp) || customUndTp <= 0) {
            return {
                valid: false,
                title: "❌ Exit Strategy Error: Invalid Custom Underlying SL/TP",
                message: `You selected Custom Underlying SL/TP exit mode for ${symbol}, but the Underlying Stop-Loss or Take-Profit price is set to $0.00 or blank.\n\nHow to fix: Enter valid dollar prices greater than $0.00 for both Underlying SL and TP.`
            };
        }
    }
    if (['percentage', 'hybrid_pct'].includes(sellMode) && !(mode === 'regular' && document.getElementById('strategyExitGridContinuous_regular')?.checked)) {
        const prefix = mode === 'options' ? 'Options' : 'Regular';
        const stop = parseFloat(document.getElementById(`strategy${prefix}SlPct`)?.value);
        const target = parseFloat(document.getElementById(`strategy${prefix}TpPct`)?.value);
        const trailEnabled = !!document.getElementById(mode === 'options' ? 'strategyEnablePctTrail_options' : 'strategyEnablePctTrail')?.checked;
        const trail = parseFloat(document.getElementById(`strategy${prefix}TrailPct`)?.value);
        if (!Number.isFinite(stop) || stop < 0.1 || stop >= 100) {
            return { valid: false, title: '❌ Invalid Percentage Stop', message: 'Stop Loss must be between 0.1% and 99%.' };
        }
        if (!Number.isFinite(target) || target < 0.1 || target > 1000) {
            return { valid: false, title: '❌ Invalid Percentage Target', message: 'Take Profit must be between 0.1% and 1000%.' };
        }
        if (trailEnabled && (!Number.isFinite(trail) || trail < 0.1 || trail >= 100)) {
            return { valid: false, title: '❌ Invalid Trailing Stop', message: 'Trailing Stop must be between 0.1% and 99%, or disable Trail.' };
        }
    }
    if (indicatorModes.includes(sellMode)) {
        if (checkedIndicators === 0) {
            const modeName = sellMode.replace(/_/g, ' ').toUpperCase();
            return {
                valid: false,
                title: "❌ Exit Strategy Error: No Indicators Selected",
                message: `You selected [${modeName}] as your Exit Strategy for ${symbol}, which requires technical indicators to generate sell signals.\n\nHowever, 0 indicators are currently checked in your Indicator Pool!\n\nHow to fix: Scroll down to the Indicator Pool checklist and check at least 1 indicator, or switch your Exit Strategy dropdown to a standalone mode.`
            };
        }
        const sellThreshold = parseInt(document.getElementById(`strategySellThreshold_${mode}`)?.value || 1, 10);
        if (sellThreshold > checkedIndicators) {
            return {
                valid: false,
                title: "❌ Exit Strategy Error: Sell Threshold Too High",
                message: `Your Exit Strategy requires ${sellThreshold} sell signals to trigger an exit, but only ${checkedIndicators} indicator(s) are checked in your pool!\n\nHow to fix: Lower your Sell Signal Threshold to match your checked indicators (between 1 and ${checkedIndicators}), or check more indicators below.`
            };
        }
    }

    // 5. BOTTOM OF FORM: Risk Management Validation
    const enableRisk = document.getElementById(`strategyEnableRisk_${mode}`)?.checked;
    if (enableRisk) {
        const riskPerTrade = parseFloat(document.getElementById(mode === 'regular' ? 'strategyRiskPerTrade' : 'strategyOptionsRiskPerTrade')?.value || 0);
        const maxDrawdown = parseFloat(document.getElementById(mode === 'regular' ? 'strategyMaxDailyDrawdown' : 'strategyOptionsMaxDailyDrawdown')?.value || 0);
        const maxPosPct = parseFloat(document.getElementById(mode === 'regular' ? 'strategyMaxPositionPct' : 'strategyOptionsMaxPositionPct')?.value || 0);

        if (!Number.isFinite(riskPerTrade) || riskPerTrade <= 0) {
            return {
                valid: false,
                title: "❌ Risk Management Error: Risk Per Trade",
                message: `You enabled Risk Management for ${symbol}, but Risk Per Trade % is set to 0 or negative.\n\nHow to fix: Enter a valid percentage (e.g. 2.0) under Risk Management settings at the bottom of the form.`
            };
        }
        if (!Number.isFinite(maxDrawdown) || maxDrawdown <= 0) {
            return {
                valid: false,
                title: "❌ Risk Management Error: Max Daily Drawdown",
                message: `You enabled Risk Management for ${symbol}, but Max Daily Drawdown % is set to 0 or negative.\n\nHow to fix: Enter a valid percentage (e.g. 5.0) under Risk Management settings at the bottom of the form.`
            };
        }
        if (!Number.isFinite(maxPosPct) || maxPosPct <= 0) {
            return {
                valid: false,
                title: "❌ Risk Management Error: Max Position Allocation",
                message: `You enabled Risk Management for ${symbol}, but Max Position Allocation % is set to 0 or negative.\n\nHow to fix: Enter a valid percentage (e.g. 100.0) under Risk Management settings at the bottom of the form.`
            };
        }
    }

    return { valid: true };
};

window.handleStrategyAction = async function () {
    const mode = window.activeTradingMode || 'regular';
    const validation = window.validateStrategyPreflight(currentStrategySymbol, mode);
    if (!validation.valid) {
        alert(`${validation.title}\n\n${validation.message}`);
        return;
    }

    if (strategyModalMode === 'deploy') {
        await confirmAndDeployBot();
    } else {
        await saveTickerSettings();
    }
};

function setBotCardRefreshing(ticker, statusText = "Refreshing settings...") {
    if (!ticker) return;
    const cards = document.querySelectorAll(`.active-bot[data-ticker="${ticker}"]`);
    cards.forEach(card => {
        card.classList.add('opacity-80', 'transition-all', 'duration-200');
        let badge = card.querySelector('.bot-refreshing-badge');
        if (!badge) {
            badge = document.createElement('div');
            badge.className = 'bot-refreshing-badge mt-2 p-2 bg-indigo-50/95 border border-indigo-200/80 rounded-lg flex items-center justify-center gap-2 text-xs font-bold text-indigo-800 shadow-sm transition-all duration-200';
            card.appendChild(badge);
        }
        badge.innerHTML = `
            <svg class="animate-spin h-3.5 w-3.5 text-indigo-600 shrink-0" xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24">
                <circle class="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" stroke-width="4"></circle>
                <path class="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4zm2 5.291A7.962 7.962 0 014 12H0c0 3.042 1.135 5.824 3 7.938l3-2.647z"></path>
            </svg>
            <span>${statusText}</span>
        `;
    });
}

async function saveTickerSettings() {
    if (!currentStrategySymbol) return;

    const mode = window.activeTradingMode || 'regular';
    if (!requireValidOptionsDirectionForSave(mode)) return;
    const validation = window.validateStrategyPreflight(currentStrategySymbol, mode);
    if (!validation.valid) {
        alert(`${validation.title}\n\n${validation.message}`);
        return;
    }

    let buyThresholdVal = parseInt(document.getElementById(`strategyBuyThreshold_${mode}`).value, 10);
    let sellThresholdVal = parseInt(document.getElementById(`strategySellThreshold_${mode}`).value, 10);

    const indicators = getSelectedStrategyIndicators(mode);

    buyThresholdVal = normalizeSignalThreshold(buyThresholdVal, indicators.length, 'buy');
    sellThresholdVal = normalizeSignalThreshold(sellThresholdVal, indicators.length, 'sell');

    const data = {
        ticker: currentStrategySymbol,
        add_to_watchlist: document.getElementById('strategyAddWatchlist')?.checked || false,
        settings: {
            ...getStrategyModeSettings(mode),
            amount: mode === 'regular' ? (parseFloat(document.getElementById('strategyCapital')?.value || '') || null) : mode === 'ai_autopilot' ? (parseFloat(document.getElementById('strategyCapital_ai_autopilot')?.value || '') || null) : undefined,
            timeframe: document.getElementById(`strategyTimeframe_${mode}`)?.value || null,
            min_buy_signals: mode === 'ai_autopilot' ? 4 : buyThresholdVal,
            min_sell_signals: mode === 'ai_autopilot' ? 4 : sellThresholdVal,
            max_trades: Math.min(100, Math.max(0, parseInt(document.getElementById('strategyMaxTrades')?.value || 0, 10) || 0)),
            sell_mode: document.getElementById(`strategySellMode_${mode}`)?.value || 'hybrid',
            indicators: mode === 'ai_autopilot' ? [] : indicators,
            indicator_overrides: mode === 'ai_autopilot' ? {} : getStrategyDraftIndicatorOverrides(),
            paused: window._strategyPaused || false,
            ai_autopilot: mode === 'ai_autopilot',
            ai_model: normalizeBotAIModel(document.getElementById('strategyAiModel')?.value || 'llama-3.3-70b-versatile'),
            risk_per_trade: (document.getElementById(mode === 'regular' ? 'strategyEnableRisk_regular' : 'strategyEnableRisk_options')?.checked) ? parseFloat(document.getElementById(mode === 'regular' ? 'strategyRiskPerTrade' : 'strategyOptionsRiskPerTrade')?.value || 2.0) / 100.0 : null,
            max_daily_drawdown: (document.getElementById(mode === 'regular' ? 'strategyEnableRisk_regular' : 'strategyEnableRisk_options')?.checked) ? parseFloat(document.getElementById(mode === 'regular' ? 'strategyMaxDailyDrawdown' : 'strategyOptionsMaxDailyDrawdown')?.value || (mode === 'regular' ? 50.0 : 60.0)) / 100.0 : null,
            max_position_pct: (document.getElementById(mode === 'regular' ? 'strategyEnableRisk_regular' : 'strategyEnableRisk_options')?.checked) ? parseFloat(document.getElementById(mode === 'regular' ? 'strategyMaxPositionPct' : 'strategyOptionsMaxPositionPct')?.value || 100.0) / 100.0 : null,
            atr_stop_multiplier: getStrategyExitNumber(mode, 'strategyAtrStopMult', 'strategyAtrStopMult_options', 2.0),
            atr_trail_multiplier: getStrategyExitNumber(mode, 'strategyAtrTrailMult', 'strategyAtrTrailMult_options', 3.0),
            take_profit_multiplier: getStrategyExitNumber(mode, 'strategyAtrTpMult', 'strategyAtrTpMult_options', 4.0),
            enable_cooldown: document.getElementById(`strategyEnableCooldown_${mode}`)?.checked || false,
            cooldown_minutes: getCooldownMinutesValue(mode),
            ...getCoreTradeGuardSettings(mode),
            options_trading: mode === 'options',
            options_type: document.getElementById('strategyOptionsType')?.value || 'calls_only',
            options_trade_amount: parseFloat(document.getElementById('strategyOptionsTradeAmount')?.value || 100),
            options_expiry_days: parseInt(document.getElementById('strategyOptionsExpiryDays')?.value || 7),
            options_strike_type: document.getElementById('strategyOptionsStrikeType')?.value || 'ATM',
            options_exit_strategy: document.getElementById('strategySellMode_options')?.value || 'hybrid',
            options_stop_loss_pct: parseFloat(document.getElementById('strategyOptionsSlPct')?.value || OPTIONS_DEFAULT_SL_PCT) / 100.0,
            options_take_profit_pct: parseFloat(document.getElementById('strategyOptionsTpPct')?.value || OPTIONS_DEFAULT_TP_PCT) / 100.0,
            options_trailing_pct: parseFloat(document.getElementById('strategyOptionsTrailPct')?.value || OPTIONS_DEFAULT_TRAIL_PCT) / 100.0,
            enable_signal_reset: document.getElementById(mode === 'regular' ? 'strategyRegularSignalReset' : 'strategyOptionsSignalReset')?.checked || false,
            enable_smart_reentry: document.getElementById(mode === 'regular' ? 'strategyRegularSmartReentry' : 'strategyOptionsSmartReentry')?.checked || false,
            smart_reentry_type: document.getElementById(mode === 'regular' ? 'strategyRegularSmartReentryType' : 'strategyOptionsSmartReentryType')?.value || 'RSI',
            smart_reentry_rsi_call_max: parseInt(document.getElementById(mode === 'regular' ? 'strategyRegularRsiCallMax' : 'strategyOptionsRsiCallMax')?.value || 70),
            smart_reentry_rsi_put_min: parseInt(document.getElementById(mode === 'regular' ? 'strategyRegularRsiPutMin' : 'strategyOptionsRsiPutMin')?.value || 30),
            smart_reentry_vwap_call_max_dist_pct: parseFloat(document.getElementById(mode === 'regular' ? 'strategyRegularVwapCallMaxPct' : 'strategyOptionsVwapCallMaxPct')?.value || 0.5),
            smart_reentry_vwap_put_max_dist_pct: parseFloat(document.getElementById(mode === 'regular' ? 'strategyRegularVwapPutMaxPct' : 'strategyOptionsVwapPutMaxPct')?.value || 0.5)
        }
    };

    const targetTicker = currentStrategySymbol;
    try {
        if ((mode === 'regular' || mode === 'ai_autopilot') && window.updateMarketSessionNotice) {
            const sessionCheck = await window.updateMarketSessionNotice();
            if (sessionCheck && sessionCheck.allowed === false) {
                alert(sessionCheck.message || 'This ticker is not eligible for the selected market session.');
                return;
            }
        }
        closeStrategyModal();
        setBotCardRefreshing(targetTicker, "Saving settings & scanning...");

        const headers = await getAuthHeaders();
        const response = await fetch(`${API_BASE}/api/settings/ticker`, {
            method: 'POST',
            headers: headers,
            body: JSON.stringify(data)
        });
        if (response.ok) {
            botFullSettingsCache.delete(targetTicker);
            setBotCardRefreshing(targetTicker, "Refreshing scan results...");
            setTimeout(() => {
                fetchBotsData({ force: true });
            }, 1500);
        } else {
            const errData = await response.json().catch(() => ({}));
            alert(`Failed to save settings: ${errData.message || errData.detail || response.statusText}`);
        }
    } catch (e) {
        console.error('Error saving ticker settings:', e);
        alert(`Error saving ticker settings: ${e.message}`);
    }
}

window.resetTickerSettings = async function () {
    if (!currentStrategySymbol) return;
    if (!confirm(`Reset ${currentStrategySymbol} to global defaults?`)) return;

    const targetTicker = currentStrategySymbol;
    closeStrategyModal();
    setBotCardRefreshing(targetTicker, "Resetting to defaults...");

    try {
        const headers = await getAuthHeaders();
        await fetch(`${API_BASE}/api/settings/ticker/${targetTicker}`, {
            method: 'DELETE',
            headers: headers
        });
        botFullSettingsCache.delete(targetTicker);
        setBotCardRefreshing(targetTicker, "Refreshing scan results...");
        setTimeout(() => {
            fetchBotsData({ force: true });
        }, 1500);
    } catch (e) {
        console.error('Error resetting ticker settings:', e);
    }
};

window.toggleWatchlist = async function (ticker) {
    if (!ticker) return;

    const inWatchlist = (window.lastBotsData?.watchlist || []).includes(ticker);

    try {
        const headers = await getAuthHeaders();
        if (inWatchlist) {
            // Remove from watchlist
            await fetch(`${API_BASE}/api/watchlist/${ticker}`, {
                method: 'DELETE',
                headers: headers
            });
        } else {
            // Add to watchlist
            await fetch(`${API_BASE}/api/watchlist`, {
                method: 'POST',
                headers: headers,
                body: JSON.stringify({ ticker: ticker })
            });
        }
        // Refresh UI
        fetchBotsData({ force: true });
    } catch (e) {
        console.error('Error toggling watchlist:', e);
    }
};
// ──────────────────────────────────────────────
// Main Fetch Loop
// ──────────────────────────────────────────────
let botsSnapshotFailCount = 0;

function isOptionSnapshotItem(item = {}) {
    const assetClass = String(item.asset_class || '').toLowerCase();
    const symbol = String(item.symbol || '').toUpperCase().replace(/\s+/g, '');
    return assetClass === 'option' || assetClass === 'us_option' || /^[A-Z]{1,6}\d{6}[CP]\d{8}$/.test(symbol);
}

function normalizeBotsSnapshot(data) {
    if (!data || data.schema_version !== '1.0') throw new Error('Unsupported Bots snapshot schema');
    data.botScans = data.bot_scans || data.botScans || {};
    data.tradelist = Array.isArray(data.tradelist) ? data.tradelist : Object.keys(data.botScans);
    data.ticker_settings = data.ticker_settings || {};
    data.has_keys = data.has_keys ?? true;
    data.simulation = data.simulation ?? false;
    data.pendingOrders = data.pendingOrders || data.open_orders || [];
    data.positions = data.positions || [];

    const acc = data.account || {};
    const eqVal = parseFloat(acc.equity ?? 0);
    const cashVal = parseFloat(acc.cash ?? 0);
    const dPlVal = parseFloat(acc.dailyPL ?? 0);
    const dPctVal = parseFloat(acc.dailyPLPct ?? 0);
    if (!data.capital || data.capital === '---') data.capital = eqVal > 0 ? `$${eqVal.toFixed(2)}` : '---';
    if (!data.cash || data.cash === '---') data.cash = cashVal > 0 ? `$${cashVal.toFixed(2)}` : '---';
    if (data.openPositions === undefined) data.openPositions = String(data.positions.length);
    if (!data.positionsList) data.positionsList = data.positions.length ? data.positions.map(p => p.symbol).join(', ') : 'No positions';
    if (!data.dailyPL) {
        data.dailyPL = dPlVal > 0
            ? `+$${Math.abs(dPlVal).toFixed(2)} (+${Math.abs(dPctVal).toFixed(1)}%)`
            : dPlVal < 0
                ? `-$${Math.abs(dPlVal).toFixed(2)} (-${Math.abs(dPctVal).toFixed(1)}%)`
                : '$0.00 (0.0%)';
    }
    if (!data.totalProfit) data.totalProfit = '$0.00 (0.0%)';
    return data;
}

function projectBotsPartition(snapshot, partition) {
    const wantsOptions = partition === 'options';
    const symbols = snapshot.tradelist.filter(ticker => {
        const settings = snapshot.ticker_settings[ticker] || {};
        return Boolean(settings.options_trading || settings.is_option) === wantsOptions;
    });
    const symbolSet = new Set(symbols);
    return {
        ...snapshot,
        partition,
        tradelist: symbols,
        botScans: Object.fromEntries(Object.entries(snapshot.botScans).filter(([ticker]) => symbolSet.has(ticker))),
        ticker_settings: Object.fromEntries(Object.entries(snapshot.ticker_settings).filter(([ticker]) => symbolSet.has(ticker))),
        positions: snapshot.positions.filter(item => isOptionSnapshotItem(item) === wantsOptions),
        pendingOrders: snapshot.pendingOrders.filter(item => isOptionSnapshotItem(item) === wantsOptions),
        open_orders: (snapshot.open_orders || snapshot.pendingOrders).filter(item => isOptionSnapshotItem(item) === wantsOptions)
    };
}

function mergeUniqueSnapshotItems(items, keyFn) {
    const seen = new Set();
    return items.filter(item => {
        const key = keyFn(item);
        if (seen.has(key)) return false;
        seen.add(key);
        return true;
    });
}

function composeBotsSnapshot() {
    const optionsData = botsPartitionSnapshots.options;
    const regularData = botsPartitionSnapshots.regular;
    const available = [regularData, optionsData].filter(Boolean);
    if (!available.length) return null;
    const shared = botsSharedSnapshot || available[available.length - 1];
    const allSymbols = new Set(available.flatMap(item => item.tradelist || []));
    botsMasterTradelist = botsMasterTradelist.filter(ticker => allSymbols.has(ticker));
    available.forEach(item => (item.tradelist || []).forEach(ticker => {
        if (!botsMasterTradelist.includes(ticker)) botsMasterTradelist.push(ticker);
    }));

    const positions = mergeUniqueSnapshotItems(
        available.flatMap(item => item.positions || []),
        item => `${item.id || ''}:${item.symbol || ''}`
    );
    const pendingOrders = mergeUniqueSnapshotItems(
        available.flatMap(item => item.pendingOrders || item.open_orders || []),
        item => `${item.id || ''}:${item.symbol || ''}:${item.side || ''}`
    );
    return {
        ...shared,
        partition: 'all',
        tradelist: botsMasterTradelist.slice(),
        botScans: Object.assign({}, regularData?.botScans || {}, optionsData?.botScans || {}),
        ticker_settings: Object.assign({}, regularData?.ticker_settings || {}, optionsData?.ticker_settings || {}),
        positions,
        pendingOrders,
        open_orders: pendingOrders
    };
}

function scheduleCrossPartitionRefresh(sourcePartition) {
    if (!['options', 'regular'].includes(sourcePartition)) return;
    clearTimeout(crossPartitionRefreshTimerId);
    const otherPartition = sourcePartition === 'options' ? 'regular' : 'options';
    crossPartitionRefreshTimerId = setTimeout(() => {
        fetchBotsData({ force: true, partition: otherPartition });
    }, 250);
}

function ingestBotsSnapshot(rawData) {
    const data = normalizeBotsSnapshot(rawData);
    const partition = data.partition || 'all';
    const previousTradeSeq = lastObservedTradeEventSeq;
    botsSharedSnapshot = data;

    if (partition === 'all') {
        botsMasterTradelist = data.tradelist.slice();
        botsPartitionSnapshots.options = projectBotsPartition(data, 'options');
        botsPartitionSnapshots.regular = projectBotsPartition(data, 'regular');
    } else {
        botsPartitionSnapshots[partition] = data;
    }

    if (data.trade_event_seq !== undefined && data.trade_event_seq !== null) {
        lastObservedTradeEventSeq = data.trade_event_seq;
        if (previousTradeSeq !== null && previousTradeSeq !== data.trade_event_seq) {
            scheduleCrossPartitionRefresh(partition);
        }
    }

    const composed = composeBotsSnapshot();
    if (!composed) return null;
    window.lastBotsData = composed;
    window.lastDashboardData = composed;
    applyBotsData(composed);
    return composed;
}

function hasOptionsSnapshotInterest() {
    const data = botsPartitionSnapshots.options;
    return Boolean(data && (
        (data.tradelist || []).length ||
        (data.positions || []).length ||
        (data.pendingOrders || data.open_orders || []).length
    ));
}

async function fetchBotsData(options = {}) {
    const { force = false, partition = 'all' } = options;
    const partitionNorm = ['all', 'options', 'regular'].includes(partition) ? partition : 'all';
    if (!force && !shouldRunBotsCardRefresh()) return false;
    const state = botsFetchState[partitionNorm];
    if (state.promise) {
        if (!force) return state.promise;
        state.controller?.abort();
    }

    const fetchId = ++state.fetchId;
    state.controller = new AbortController();
    state.promise = (async () => {
        const fetchStartTime = performance.now();
        const headers = await getAuthHeaders();
        const url = `${API_BASE}/api/bots/snapshot?partition=${partitionNorm}`;
        const response = await fetchWithTimeout(
            url,
            { headers, cache: 'no-store', controller: state.controller },
            DASHBOARD_FETCH_TIMEOUT_MS
        );
        if (!response.ok) throw new Error(`Snapshot HTTP ${response.status}`);
        const data = await response.json();

        // ── Phase 4 Telemetry Metrics ──
        const fetchMs = Math.round(performance.now() - fetchStartTime);
        const serverExecMs = response.headers.get('X-Snapshot-Time-MS');
        const payloadKb = (JSON.stringify(data).length / 1024).toFixed(1);
        if (fetchMs > 4000 || payloadKb > 400) {
            console.warn(`[telemetry] Heavy snapshot: ${payloadKb} KB in ${fetchMs}ms (backend: ${serverExecMs || '?'}ms)`);
        }

        botsSnapshotFailCount = 0;
        const composed = ingestBotsSnapshot(data);
        // Activity is independent from the partitioned bot snapshots.  A
        // regular/options poll must not trigger another scans request because
        // those polls run on different cadences (60s/10s).  Refresh scans only
        // after a full snapshot, and use the activity fetcher's normal
        // throttle/coalescing rules instead of bypassing them.
        if (partitionNorm === 'all' && currentLogTab === 'all') {
            void fetchBotsActivity('scans');
        }
        if (partitionNorm === 'all') {
            try {
                sessionStorage.setItem('botsDataCache', JSON.stringify({
                    schemaVersion: '1.0',
                    data,
                    fetchedAt: Date.now()
                }));
            } catch (error) {
                console.warn('[bots] Could not update snapshot cache:', error);
            }
        }
        return composed;
    })();

    try {
        return await state.promise;
    } catch (error) {
        if (error.name === 'AbortError') {
            // A forced refresh intentionally superseded this request.
        } else if (error.name === 'TimeoutError') {
            console.warn(`[bots] Snapshot timed out after ${DASHBOARD_FETCH_TIMEOUT_MS}ms.`);
        } else {
            botsSnapshotFailCount++;
            console.error(`[bots] Fetch error (attempt ${botsSnapshotFailCount}):`, error);
            // Self-Healing Fallback: If network stutters, maintain UI state using latest cached snapshot
            if (botsSnapshotFailCount >= 2 && window.lastBotsData) {
                console.warn('[bots] Self-healing activated: Rendering cached snapshot data.');
                applyBotsData(window.lastBotsData);
            }
        }
        return false;
    } finally {
        if (fetchId === state.fetchId) {
            state.controller = null;
            state.promise = null;
        }
    }
}

function applyBotsData(data) {
    if (draggedItem) {
        console.log('[bots] Drag in progress, skipping UI apply to avoid layout disruption.');
        return;
    }
    // Connection status
    isAlpacaLinked = !data.simulation && data.has_keys;
    window.AlpacaStatusCache?.writeFromData(data);
    window.AlpacaStatusCache?.render();
    updateAlpacaStatus(isAlpacaLinked, data);

    const connPrompt = document.getElementById('connectionPrompt');
    const dashContent = document.getElementById('dashboardContent');

    if (!isAlpacaLinked && !data.has_keys) {
        if (connPrompt) connPrompt.classList.remove('hidden');
        if (dashContent) dashContent.classList.add('hidden');
        return; // Don't try to render portfolio cards or bots if we are showing the prompt
    } else {
        if (connPrompt) connPrompt.classList.add('hidden');
        if (dashContent) dashContent.classList.remove('hidden');
    }

    // Portfolio cards
    if (document.getElementById('globalMaxDrawdown')) {
        if (document.activeElement !== document.getElementById('globalMaxDrawdown')) {
            let dd = data.indicator_parameters?.MAX_DAILY_DRAWDOWN;
            if (dd !== undefined && dd !== null) document.getElementById('globalMaxDrawdown').value = Number((dd * 100).toFixed(2));
        }
    }
    if (document.getElementById('globalHardStop')) {
        if (document.activeElement !== document.getElementById('globalHardStop')) {
            let hs = data.indicator_parameters?.GLOBAL_HARD_STOP_PCT;
            if (hs !== undefined && hs !== null) document.getElementById('globalHardStop').value = Number((hs * 100).toFixed(2));
        }
    }
    renderGlobalRiskCard(data.global_risk_status || {});

    if (document.getElementById('totalCapital')) {
        document.getElementById('totalCapital').textContent = data.capital || '---';
        document.getElementById('totalCapital').classList.remove('animate-pulse');
    }
    if (document.getElementById('cashDisplay')) {
        document.getElementById('cashDisplay').textContent = `Cash: ${data.cash || '---'}`;
    }
    if (document.getElementById('openPositions')) {
        document.getElementById('openPositions').textContent = data.openPositions || '0';
        document.getElementById('openPositions').classList.remove('animate-pulse');
        if (document.getElementById('positionsList')) {
            document.getElementById('positionsList').textContent = data.positionsList || 'No positions';
        }
    }
    if (document.getElementById('dailyPL')) {
        const plEl = document.getElementById('dailyPL');
        const plStr = data.dailyPL || '$0.00 (0.0%)';
        const plParts = plStr.split(' (');
        if (plParts.length === 2) {
            plEl.innerHTML = `${plParts[0]}<div class="text-[0.65rem] opacity-80 font-bold leading-tight -mt-0.5">(${plParts[1]}</div>`;
        } else {
            plEl.textContent = plStr;
        }
        plEl.classList.remove('animate-pulse');
        const plVal = parseFloat((data.dailyPL || '').replace(/[^-\d.]/g, ''));
        plEl.classList.remove('text-emerald-600', 'text-red-500'); plEl.classList.add(plVal >= 0 ? 'text-emerald-600' : 'text-red-500');
    }
    if (document.getElementById('totalProfit')) {
        const tpEl = document.getElementById('totalProfit');
        const tpStr = data.totalProfit || '$0.00 (0.0%)';
        const tpParts = tpStr.split(' (');
        if (tpParts.length === 2) {
            tpEl.innerHTML = `${tpParts[0]}<div class="text-[0.65rem] opacity-80 font-bold leading-tight -mt-0.5">(${tpParts[1]}</div>`;
        } else {
            tpEl.textContent = tpStr;
        }
        tpEl.classList.remove('animate-pulse');
        const tpVal = parseFloat((data.totalProfit || '').replace(/[^-\d.]/g, ''));
        tpEl.classList.remove('text-emerald-600', 'text-red-500'); tpEl.classList.add(tpVal >= 0 ? 'text-emerald-600' : 'text-red-500');
    }

    // Last scan time

    // Strategy timeframe sync
    if (data.strategyTimeframe) {
        currentBackendTf = data.strategyTimeframe;
    }

    // Auto-select first active bot if none selected BEFORE rendering tradelist
    if (!selectedTicker && data.tradelist && data.tradelist.length > 0) {
        selectedTicker = data.tradelist[0];
    }

    // Active bots list
    renderTradelist(data.botScans || data.watchlistScans, data.tradelist, data.tickerAmounts);
    checkBotLossPauseAlerts(data);

    // Reuse fresh AI analysis across Dashboard/Bots navigation before starting a request.
    let mirrored = false;
    try {
        const aiContext = typeof getAIIndicatorPageContext === 'function' ? getAIIndicatorPageContext() : 'bots';
        const aiTimeframe = typeof getAIDecisionTimeframe === 'function'
            ? getAIDecisionTimeframe()
            : currentBackendTf;
        const selectedModel = typeof getSelectedAIModelForContext === 'function'
            ? getSelectedAIModelForContext(aiContext)
            : normalizeBotAIModel(null);
        const cacheKey = getAIIndicatorCacheKey(selectedTicker, aiTimeframe, selectedModel);
        const cachedAi = getAICache(cacheKey);
        if (cachedAi && isAICacheForContext(cachedAi, selectedTicker, aiTimeframe, selectedModel)) {
            renderAIIndicatorCard(cachedAi.data, true);
            if (typeof window.onAIIndicatorUpdated === 'function') {
                window.onAIIndicatorUpdated(cachedAi.data, true);
            }
            mirrored = true;
        }
    } catch (e) { }

    // Only fetch if we didn't mirror the global AI indicator
    if (selectedTicker && !mirrored) {
        void fetchAIIndicator(selectedTicker, currentBackendTf).catch(error => {
            if (!['AbortError', 'TimeoutError'].includes(error?.name)) {
                console.error('[ai-indicator] Unexpected fetch failure:', error);
            }
        });
    }

    // Execution log
    latestPendingOrders = data.pendingOrders || [];
    latestPositions = data.positions || [];
    renderTradeLog(latestScanHistory, latestOrderHistory, latestPendingOrders, latestPositions);
}

function updateAlpacaStatus(isLinked, data = null) {
    const statusEl = document.getElementById('alpacaLinkStatus');
    const dotEl = document.getElementById('alpacaStatusDot');
    const textEl = document.getElementById('alpacaStatusText');
    const btnUnlink = document.getElementById('btnUnlinkAlpaca');

    if (!statusEl || !dotEl || !textEl) return;
    statusEl.title = data?.brokerStatusReason || data?.alpacaLoadStatus || '';

    if (isLinked) {
        statusEl.className = 'flex items-center justify-center h-9 text-xs font-black px-5 rounded-full border-2 shadow-sm whitespace-nowrap transition-all duration-500 uppercase tracking-wider bg-emerald-50 text-emerald-600 border-emerald-100';
        dotEl.className = 'h-2 w-2 rounded-full mr-2 bg-emerald-500 animate-pulse shadow-[0_0_8px_rgba(16,185,129,0.8)]';
        textEl.textContent = 'Alpaca';
        if (btnUnlink) btnUnlink.classList.remove('hidden');
    } else {
        if (data && data.has_keys) {
            statusEl.className = "flex items-center justify-center h-9 text-xs font-black px-5 rounded-full bg-amber-50 text-amber-600 border-2 border-amber-100 shadow-sm whitespace-nowrap transition-all duration-500 uppercase tracking-wider";
            dotEl.className = "h-2 w-2 rounded-full mr-2 bg-amber-500 animate-pulse";
            textEl.textContent = "RETRYING...";
            if (btnUnlink) btnUnlink.classList.remove('hidden');
        } else {
            statusEl.className = "flex items-center justify-center h-9 text-xs font-black px-5 rounded-full bg-rose-50 text-rose-600 border-2 border-rose-100 shadow-sm whitespace-nowrap transition-all duration-500 uppercase tracking-wider";
            dotEl.className = "h-2 w-2 rounded-full mr-2 bg-rose-500 animate-pulse";
            textEl.textContent = "SIMULATION";
            if (btnUnlink) btnUnlink.classList.add('hidden');
        }
    }
}

// ──────────────────────────────────────────────
// Alpaca Connection Logic
// ──────────────────────────────────────────────
function openAlpacaModal() {
    const modal = document.getElementById('alpacaModal');
    if (modal) {
        modal.classList.remove('hidden');
        // Clear fields for security
        const keyEl = document.getElementById('alpacaKey');
        const secretEl = document.getElementById('alpacaSecret');
        if (keyEl) keyEl.value = "";
        if (secretEl) secretEl.value = "";
    }
}

function closeAlpacaModal() {
    const modal = document.getElementById('alpacaModal');
    if (modal) modal.classList.add('hidden');
}

function openGuideModal() {
    const modal = document.getElementById('guideModal');
    if (modal) modal.classList.remove('hidden');
}

function closeGuideModal() {
    const modal = document.getElementById('guideModal');
    if (modal) modal.classList.add('hidden');
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
            window.AlpacaStatusCache?.writeFromData({ simulation: false, has_keys: true, brokerConnected: true, brokerStatus: 'connected' });
            window.AlpacaStatusCache?.render();
            closeAlpacaModal();
            fetchBotsData({ force: true });
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
            window.AlpacaStatusCache?.clear();
            window.AlpacaStatusCache?.render({ status: 'disconnected', has_keys: false, simulation: true, updatedAt: Date.now() });
            fetchBotsData({ force: true });
        } else {
            alert("Error unlinking account.");
        }
    } catch (err) {
        console.error(err);
        alert("Backend communication error.");
    }
}

// ──────────────────────────────────────────────
// Live Strategy Matrix
// ──────────────────────────────────────────────
const INDICATOR_META = {
    'RSI': { key: 'ENABLE_RSI', desc: 'Momentum Oscillator', icon: '📊' },
    'MACD': { key: 'ENABLE_MACD', desc: 'Trend Momentum', icon: '📈' },
    'EMA Cross': { key: 'ENABLE_EMA', desc: '9/21 EMA Crossover', icon: '✂️' },
    'Supertrend': { key: 'ENABLE_SUPERTREND', desc: 'ATR Trend Follower', icon: '🚀' },
    'Bollinger': { key: 'ENABLE_BOLLINGER', desc: 'Volatility Bands', icon: '🎯' },
    'VWAP': { key: 'ENABLE_VWAP', label: 'Session VWAP', desc: 'Webull-compatible session VWAP', icon: '⚖️' },
    'Auto-AVWAP': { key: 'ENABLE_AUTO_AVWAP', desc: 'Automatic volume-spike AVWAP', icon: '⚖️' },
    'Mystic Pulse': { key: 'ENABLE_MYSTIC_PULSE', desc: 'Trend Persistence', icon: '🔮' },
    'Candle Patterns': { key: 'ENABLE_CANDLE_PATTERNS', desc: 'Reversal Detection', icon: '🕯️' },
    'Botbulls4 News AI': { key: 'ENABLE_AI_SENTIMENT', desc: 'Premium AI News Sentiment', icon: '🧠' },
    'ADX Trend': { key: 'ENABLE_ADX_TREND', desc: 'Trend Strength Filter', icon: '🧭' },
    'SMA': { key: 'ENABLE_SMA', desc: 'Long-Term Trend Filter', icon: '📏' },
    'Volume Profile': { key: 'ENABLE_VOLUME_PROFILE', desc: 'POC / Value Area', icon: 'VP' },
    'Prior Day High/Low': { key: 'ENABLE_DAILY_HIGH_LOW', desc: 'Prior Session Breakout', icon: 'HL' },
    'Pivot Points (S/R)': { key: 'ENABLE_PIVOT_POINTS', desc: 'Prior Session S/R', icon: 'P' },
    'Swing Points': { key: 'ENABLE_SWING_POINTS', desc: 'Swing S/R Structure', icon: 'SW' },
    'BotBulls1': { key: 'ENABLE_BOTBULLS1', desc: 'Premium Oscillator WaveTrend', icon: '🌊' },
    'BotBulls2': { key: 'ENABLE_BOTBULLS2', desc: 'Premium Smart Trail', icon: '🛰️' },
    'BotBulls3': { key: 'ENABLE_BOTBULLS3', desc: 'Premium Filtered Trail', icon: '🎯' },
    'BotBulls5': { key: 'ENABLE_BOTBULLS5', desc: 'Premium Pattern Predictor', icon: '🏁' },
};
for (const item of (window.STANDARD_INDICATOR_CATALOG || [])) {
    INDICATOR_META[item.name] ||= {
        key: window.STANDARD_INDICATOR_TOGGLE_KEYS?.[item.name],
        label: item.label,
        desc: item.category,
        icon: '◈',
    };
}

const BOT_CHART_PREVIEW_INDICATORS = [
    ...(window.STANDARD_INDICATOR_NAMES || [
        'RSI', 'MACD', 'EMA Cross', 'ADX Trend', 'Supertrend',
        'Bollinger', 'VWAP', 'Volume Profile', 'SMA', 'Prior Day High/Low',
        'Pivot Points (S/R)', 'Swing Points', 'Mystic Pulse', 'Candle Patterns',
    ]),
    'BotBulls1',
    'BotBulls2',
    'BotBulls3',
    'Botbulls4 News AI',
    'BotBulls5',
];
const BOT_DEFAULT_STRATEGY_INDICATORS = (
    window.STANDARD_INDICATOR_DEFAULT_NAMES || BOT_CHART_PREVIEW_INDICATORS
).slice(0, 12);

const BOT_CHART_PREVIEW_OVERLAY_INDICATORS = new Set([
    'EMA Cross',
    'Supertrend',
    'Bollinger',
    'VWAP',
    'Auto-AVWAP',
    'Volume Profile',
    'SMA',
    'Prior Day High/Low',
    'Pivot Points (S/R)',
    'Swing Points',
    'BotBulls2',
    'BotBulls3',
    'BotBulls5',
]);

const BOT_CHART_PREVIEW_PREMIUM_INFO = {
    'BotBulls1': 'Confirmed WaveTrend reversal with MFI reclaim/rejection, then price confirmation within the BB1 window. Confluence stays bullish after the last BB1 BUY marker and bearish after the last BB1 SELL marker.',
    'BotBulls2': 'Adaptive ATR trailing stop with trend-smoothed EMA and reversal zone detection. Fires on trend flips confirmed by confluence scoring.',
    'BotBulls3': 'Heikin-Ashi noise filter + ATR-based trailing stop. Generates buy/sell alerts on confirmed momentum flips.',
    'BotBulls5': 'Causal multi-pattern recognition with completed-candle confirmation, measured targets, structural invalidation, and quality scoring.',
    'Volume Profile': 'Prior-session volume-at-price profile. Draws POC, VAH, and VAL to identify institutional acceptance/rejection zones.',
    'Prior Day High/Low': "Uses the previous completed trading session's high and low as breakout/breakdown levels. Buffer is entered as %, with dotted trigger lines when non-zero.",
    'Pivot Points (S/R)': 'Classic prior-session floor pivot levels. Draws up to 5 support/resistance ladders; bullish on support/pivot reclaim, bearish on resistance rejection or pivot breakdown.',
    'Swing Points': 'Rolling swing support/resistance clusters from recent local highs/lows. Draws structural bounce/rejection and reclaim/breakdown zones.',
    'Botbulls4 News AI': 'AI news sentiment analysis. Parses current headlines and financial catalysts to determine market direction.',
};

function ensureDailyHighLowBotIndicatorCards() {
    const cards = [
        { name: 'Volume Profile', subtitle: 'POC / Value Area', settings: true },
        { name: 'Prior Day High/Low', subtitle: 'Prior Session', settings: true },
        { name: 'Pivot Points (S/R)', subtitle: 'Pivot S/R', settings: true },
        { name: 'Swing Points', subtitle: 'Swing S/R', settings: true },
        { name: 'BotBulls5', subtitle: 'Premium Pattern Predictor', settings: true, premium: true },
    ];
    ['regular', 'options'].forEach(mode => {
        const container = document.getElementById(`indicatorChecksContainer_${mode}`);
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
                ? 'indicator-card group p-2.5 sm:p-3 border border-amber-300/30 bg-amber-50/5 hover:bg-amber-50/15 rounded-2xl cursor-pointer flex items-center justify-between shadow-sm transition-all'
                : 'indicator-card group p-2.5 sm:p-3 border border-white/20 rounded-2xl cursor-pointer flex items-center justify-between shadow-sm transition-all';
            card.setAttribute('onclick', 'toggleIndicatorCard(this, event)');
            card.innerHTML = `
            <div class="flex flex-col">
                <span class="font-black ${premium ? 'text-amber-800' : 'text-indigo-900'} uppercase text-[0.6rem] sm:text-xs tracking-tight leading-tight">${premium ? '✦ ' : ''}${name}
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
                <input type="checkbox" class="strategy-indicator-check strategy-indicator-check-${mode} w-5 h-5 accent-indigo-600 rounded" value="${name}" onclick="event.stopPropagation()" onchange="updateStrategySignalLabels('${mode}')">
            </div>
        `;
            container.appendChild(card);
        });
        placeDailyHighLowBotIndicatorCards(container);
    });
}

function placeDailyHighLowBotIndicatorCards(container) {
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

const BOT_CHART_PREVIEW_RANGES = {
    '1D': { days: 1 },
    '5D': { days: 5 },
    '1M': { days: 31 },
    '3M': { days: 93 },
    '6M': { days: 186 },
    'YTD': { ytd: true },
    '1Y': { days: 366 },
    '5Y': { days: 365 * 5 },
    'MAX': { days: null },
};

let botChartPreviewChart = null;
let botChartPreviewCandleSeries = null;
let botChartPreviewVolumeSeries = null;
let botChartPreviewExtHoursSeries = null;
let botChartPreviewOverlaySeries = {};
let botChartPreviewVolumeProfileOverlay = null;
let botChartPreviewOscCharts = {};
let botChartPreviewOscSeries = {};
let botChartPreviewResizeObserver = null;
let botChartPreviewRefreshTimer = null;
let botChartPreviewLiveRefreshTimer = null;
let botChartPreviewCrosshairWired = false;
let botChartPreviewDisplayHistory = [];
let botChartPreviewRange = '1M';
let botChartPreviewSession = 'regular';
let botChartPreviewOverlayPriceLines = {};
let botChartPreviewOverlayLabelMeta = {};
let botChartPreviewRangeUiFrame = null;
let botChartPreviewOscSyncFrame = null;

function formatBotChartPreviewPrice(value) {
    const number = Number(value);
    if (!Number.isFinite(number)) return '---';
    return number >= 1 ? number.toFixed(2) : number.toFixed(4);
}

function formatBotChartPreviewVolume(value) {
    const number = Number(value);
    if (!Number.isFinite(number)) return '—';
    if (number >= 1e6) return `${(number / 1e6).toFixed(2)}M`;
    if (number >= 1e3) return `${(number / 1e3).toFixed(1)}K`;
    return String(Math.round(number));
}

function formatBotChartPreviewTimeCT(time) {
    if (!time) return '—';
    return new Date(Number(time) * 1000).toLocaleTimeString('en-US', {
        timeZone: 'America/Chicago',
        hour: '2-digit',
        minute: '2-digit',
        hour12: false,
    }) + ' CT';
}

function getBotChartPreviewTimeframeSeconds(timeframe = botChartPreviewState.settings?.timeframe || '1Hour') {
    const tf = String(timeframe || '').toUpperCase();
    if (tf.includes('DAY') || tf === '1D') return 86400;
    const value = Number((tf.match(/\d+/) || ['1'])[0]);
    if (tf.includes('HOUR') || tf.endsWith('H')) return value * 3600;
    if (tf.includes('SEC') || tf.endsWith('S')) return value;
    if (tf.includes('MIN') || tf.endsWith('M')) return value * 60;
    return 300;
}

function isFiniteBotChartPreviewNumber(value) {
    return Number.isFinite(Number(value));
}

function isValidBotChartPreviewTime(value) {
    const time = Number(value);
    return Number.isFinite(time) && time > 0;
}

function sanitizeBotChartPreviewLineData(data = []) {
    const byTime = new Map();
    (Array.isArray(data) ? data : []).forEach(point => {
        if (!point || !isValidBotChartPreviewTime(point.time) || !isFiniteBotChartPreviewNumber(point.value)) return;
        byTime.set(Number(point.time), {
            ...point,
            time: Number(point.time),
            value: Number(point.value),
        });
    });
    return Array.from(byTime.values()).sort((a, b) => Number(a.time) - Number(b.time));
}

function sanitizeBotChartPreviewCandles(data = []) {
    const byTime = new Map();
    (Array.isArray(data) ? data : []).forEach(bar => {
        if (!bar || !isValidBotChartPreviewTime(bar.time)) return;
        if (![bar.open, bar.high, bar.low, bar.close].every(isFiniteBotChartPreviewNumber)) return;
        byTime.set(Number(bar.time), {
            time: Number(bar.time),
            open: Number(bar.open),
            high: Number(bar.high),
            low: Number(bar.low),
            close: Number(bar.close),
        });
    });
    return Array.from(byTime.values()).sort((a, b) => Number(a.time) - Number(b.time));
}

function sanitizeBotChartPreviewMarkers(markers = []) {
    return (Array.isArray(markers) ? markers : [])
        .filter(marker => marker && isValidBotChartPreviewTime(marker.time))
        .map(marker => ({ ...marker, time: Number(marker.time) }))
        .sort((a, b) => Number(a.time) - Number(b.time));
}

function toBotChartPreviewCloseTimeBar(bar, timeframe = botChartPreviewState.settings?.timeframe || '1Hour') {
    const sourceTime = Number(bar?.time);
    if (!Number.isFinite(sourceTime)) return bar;
    const seconds = getBotChartPreviewTimeframeSeconds(timeframe);
    const offset = seconds > 0 && seconds < 86400 ? seconds : 0;
    return {
        ...bar,
        source_time: sourceTime,
        time: sourceTime + offset,
    };
}

function isBotChartPreviewRegularMarketBar(bar) {
    if (!isValidBotChartPreviewTime(bar?.time)) return false;
    const parts = new Intl.DateTimeFormat('en-US', {
        timeZone: 'America/Chicago',
        weekday: 'short',
        hour: '2-digit',
        minute: '2-digit',
        hour12: false,
    }).formatToParts(new Date(Number(bar.time) * 1000));
    const values = Object.fromEntries(parts.map(part => [part.type, part.value]));
    if (values.weekday === 'Sat' || values.weekday === 'Sun') return false;
    const mins = Number(values.hour) * 60 + Number(values.minute);
    return mins >= (8 * 60 + 30) && mins <= (15 * 60);
}

function getBotChartPreviewDisplayHistory(rawHistory = [], settings = {}) {
    const sorted = [...(rawHistory || [])]
        .map(bar => toBotChartPreviewCloseTimeBar(bar, settings.timeframe || '1Hour'))
        .filter(bar => bar && isValidBotChartPreviewTime(bar.time))
        .sort((a, b) => Number(a.time || 0) - Number(b.time || 0));
    const range = BOT_CHART_PREVIEW_RANGES[botChartPreviewRange] || BOT_CHART_PREVIEW_RANGES['1M'];
    const last = sorted[sorted.length - 1];
    let ranged = sorted;

    if (last && range?.ytd) {
        const d = new Date(Number(last.time) * 1000);
        const start = Date.UTC(d.getUTCFullYear(), 0, 1) / 1000;
        ranged = sorted.filter(bar => Number(bar.time || 0) >= start);
    } else if (last && range?.days != null) {
        const start = Number(last.time || 0) - (range.days * 86400);
        ranged = sorted.filter(bar => Number(bar.time || 0) >= start);
    }

    if (isCrypto(tickerFromPreviewState()) || botChartPreviewSession === 'crypto24' || botChartPreviewSession === 'extended' || settings.timeframe === '1Day') {
        return ranged;
    }
    return ranged.filter(isBotChartPreviewRegularMarketBar);
}

function tickerFromPreviewState() {
    return (botChartPreviewState.ticker || '').toUpperCase().trim();
}

function updateBotChartPreviewRangeSessionState(settings = {}) {
    const crypto = isCrypto(tickerFromPreviewState());
    if (crypto) botChartPreviewSession = 'crypto24';

    document.querySelectorAll('#botChartPreviewRangeBar .chart-range-btn').forEach(btn => {
        btn.classList.toggle('chart-range-active', btn.dataset.range === botChartPreviewRange);
    });
    document.getElementById('botChartPreviewCryptoLiveBtn')?.classList.toggle('hidden', !crypto);
    document.getElementById('botChartPreviewRegularHoursBtn')?.classList.toggle('hidden', crypto);
    document.getElementById('botChartPreviewExtendedHoursBtn')?.classList.toggle('hidden', crypto);
    document.getElementById('botChartPreviewRegularHoursBtn')?.classList.toggle('chart-session-active', !crypto && botChartPreviewSession === 'regular');
    document.getElementById('botChartPreviewExtendedHoursBtn')?.classList.toggle('chart-session-active', !crypto && botChartPreviewSession === 'extended');
    document.getElementById('botChartPreviewCryptoLiveBtn')?.classList.toggle('chart-session-active', crypto);

    const sessionEl = document.getElementById('botChartPreviewSessionStatus');
    if (sessionEl) {
        sessionEl.textContent = crypto ? 'Crypto 24h live' : (botChartPreviewSession === 'extended' ? 'Extended Hours' : 'Regular Hours');
    }

    document.querySelectorAll('#botChartPreviewTfBar .chart-tf-btn').forEach(btn => {
        btn.classList.toggle('chart-tf-active', btn.dataset.tf === (settings.timeframe || '1Hour'));
    });
}

function setBotChartPreviewOhlcv(bar) {
    const setText = (id, value) => {
        const el = document.getElementById(id);
        if (el) el.textContent = value;
    };
    if (!bar) {
        setText('botChartPreviewOhlcvO', '—');
        setText('botChartPreviewOhlcvH', '—');
        setText('botChartPreviewOhlcvL', '—');
        setText('botChartPreviewOhlcvC', '—');
        setText('botChartPreviewOhlcvV', '—');
        return;
    }
    setText('botChartPreviewOhlcvO', formatBotChartPreviewPrice(bar.open));
    setText('botChartPreviewOhlcvH', formatBotChartPreviewPrice(bar.high));
    setText('botChartPreviewOhlcvL', formatBotChartPreviewPrice(bar.low));
    setText('botChartPreviewOhlcvC', formatBotChartPreviewPrice(bar.close));
    setText('botChartPreviewOhlcvV', formatBotChartPreviewVolume(bar.volume));
}

function updateBotChartPreviewHeader(priceHistory = [], settings = {}) {
    const history = getBotChartPreviewDisplayHistory(priceHistory, settings);
    botChartPreviewDisplayHistory = history;
    const first = history.find(bar => Number.isFinite(Number(bar?.open || bar?.close)));
    const last = [...history].reverse().find(bar => Number.isFinite(Number(bar?.close)));
    const ticker = botChartPreviewState.ticker || 'Ticker';
    const timeframe = settings.timeframe || '1Hour';

    const tickerEl = document.getElementById('botChartPreviewTickerLabel');
    const priceEl = document.getElementById('botChartPreviewPrice');
    const changeEl = document.getElementById('botChartPreviewPriceChange');
    const sessionEl = document.getElementById('botChartPreviewSessionStatus');
    const lastInfoEl = document.getElementById('botChartPreviewLastBarInfo');

    if (tickerEl) tickerEl.textContent = ticker;
    if (priceEl) priceEl.textContent = last ? formatBotChartPreviewPrice(last.close) : '---';

    const baseline = Number(first?.open || first?.close || 0);
    const close = Number(last?.close || 0);
    if (changeEl && baseline > 0 && close > 0) {
        const change = close - baseline;
        const changePct = (change / baseline) * 100;
        const sign = change >= 0 ? '+' : '';
        changeEl.textContent = `${sign}${formatBotChartPreviewPrice(Math.abs(change))} (${sign}${changePct.toFixed(2)}%)`;
        changeEl.className = `text-xs font-bold ${change >= 0 ? 'text-emerald-500' : 'text-red-400'}`;
    } else if (changeEl) {
        changeEl.textContent = '---';
        changeEl.className = 'text-xs font-bold text-slate-400';
    }

    updateBotChartPreviewRangeSessionState(settings);
    if (sessionEl) {
        sessionEl.textContent = isCrypto(ticker) ? 'Crypto 24h live' : (botChartPreviewSession === 'extended' ? 'Extended Hours' : 'Regular Hours');
    }
    if (lastInfoEl) {
        lastInfoEl.textContent = last ? `${ticker} • Last completed: ${formatBotChartPreviewTimeCT(last.time)}` : `${ticker} • Last completed: —`;
    }
    setBotChartPreviewOhlcv(last);
}

function getEffectivePreviewIndicators(signals = {}, settings = {}) {
    const names = Array.isArray(settings.indicators)
        ? settings.indicators
        : Object.keys(signals || {}).filter(name => (signals[name] || {}).enabled !== false);
    const mode = settings.options_trading || botChartPreviewState?.tradingMode === 'options'
        ? 'options'
        : 'regular';
    const ticker = botChartPreviewState?.ticker || currentStrategySymbol;
    return [...new Set(names
        .map(name => String(name || '').trim())
        .filter(name => name && isBotIndicatorAllowed(name, mode, ticker)))];
}

function resolveBotChartPreviewPayloadSignals(payload = {}) {
    const directSignals = payload?.signals;
    if (directSignals && typeof directSignals === 'object' && Object.keys(directSignals).length > 0) {
        return directSignals;
    }
    const nestedSignals = payload?.primaryScan?.signals;
    if (nestedSignals && typeof nestedSignals === 'object') {
        return nestedSignals;
    }
    return {};
}

function getBotChartPreviewSignalCounts(signals = {}, settings = {}) {
    const selected = new Set(getEffectivePreviewIndicators(signals, settings));
    let bullish = 0;
    let bearish = 0;
    Object.entries(signals || {}).forEach(([name, signal]) => {
        if (selected.size > 0 && !selected.has(name)) return;
        if (Array.isArray(settings.indicators) && !selected.has(name)) return;
        const weight = Number(signal?.weight ?? 1);
        if (signal?.signal === 'BULLISH') bullish += Number.isFinite(weight) ? weight : 1;
        if (signal?.signal === 'BEARISH') bearish += Number.isFinite(weight) ? weight : 1;
    });
    return { bullish, bearish, selectedCount: selected.size };
}

function getBotChartPreviewSettingsForCard(ticker) {
    const settings = getSavedBotSettings(ticker);
    if (Array.isArray(settings.indicators)) {
        settings.indicators = getEffectivePreviewIndicators({}, settings);
        settings.enabled_indicators = [...settings.indicators];
        settings.min_buy_signals = normalizeSignalThreshold(settings.min_buy_signals, settings.indicators.length, 'buy');
        settings.min_sell_signals = normalizeSignalThreshold(settings.min_sell_signals, settings.indicators.length, 'sell');
    }
    settings.indicator_overrides = clonePlainObject(settings.indicator_overrides || {});
    return settings;
}

function collectStrategyPreviewSettings(mode = window.activeTradingMode || 'regular') {
    ensureDailyHighLowBotIndicatorCards();
    const settings = {
        ...getSavedBotSettings(currentStrategySymbol),
        ...getStrategyModeSettings(mode),
    };

    const indicators = getSelectedStrategyIndicators(mode);

    settings.timeframe = document.getElementById(`strategyTimeframe_${mode}`)?.value || settings.timeframe || '1Hour';
    settings.min_buy_signals = normalizeSignalThreshold(
        document.getElementById(`strategyBuyThreshold_${mode}`)?.value,
        indicators.length,
        'buy'
    );
    settings.min_sell_signals = normalizeSignalThreshold(
        document.getElementById(`strategySellThreshold_${mode}`)?.value,
        indicators.length,
        'sell'
    );
    settings.sell_mode = document.getElementById(`strategySellMode_${mode}`)?.value || settings.sell_mode || 'indicator';
    settings.indicators = indicators;
    settings.enabled_indicators = [...indicators];
    settings.indicator_overrides = getStrategyDraftIndicatorOverrides();
    settings.options_trading = mode === 'options';
    settings.options_type = document.getElementById('strategyOptionsType')?.value || settings.options_type || 'calls_only';
    settings.options_trade_amount = parseFloat(document.getElementById('strategyOptionsTradeAmount')?.value || settings.options_trade_amount || 100);
    settings.options_expiry_days = parseInt(document.getElementById('strategyOptionsExpiryDays')?.value || settings.options_expiry_days || 7, 10);
    settings.options_strike_type = document.getElementById('strategyOptionsStrikeType')?.value || settings.options_strike_type || 'ATM';
    settings.options_exit_strategy = document.getElementById('strategySellMode_options')?.value || settings.options_exit_strategy || settings.sell_mode || 'hybrid';
    settings.options_stop_loss_pct = parseFloat(document.getElementById('strategyOptionsSlPct')?.value || settings.options_stop_loss_pct || OPTIONS_DEFAULT_SL_PCT) / 100.0;
    settings.options_take_profit_pct = parseFloat(document.getElementById('strategyOptionsTpPct')?.value || settings.options_take_profit_pct || OPTIONS_DEFAULT_TP_PCT) / 100.0;
    settings.options_trailing_pct = parseFloat(document.getElementById('strategyOptionsTrailPct')?.value || settings.options_trailing_pct || OPTIONS_DEFAULT_TRAIL_PCT) / 100.0;
    settings.enable_signal_reset = document.getElementById(mode === 'regular' ? 'strategyRegularSignalReset' : 'strategyOptionsSignalReset')?.checked || false;
    Object.assign(settings, getCoreTradeGuardSettings(mode));
    settings.enable_smart_reentry = document.getElementById(mode === 'regular' ? 'strategyRegularSmartReentry' : 'strategyOptionsSmartReentry')?.checked || false;
    settings.smart_reentry_type = document.getElementById(mode === 'regular' ? 'strategyRegularSmartReentryType' : 'strategyOptionsSmartReentryType')?.value || 'RSI';
    settings.smart_reentry_rsi_call_max = parseInt(document.getElementById(mode === 'regular' ? 'strategyRegularRsiCallMax' : 'strategyOptionsRsiCallMax')?.value || 70, 10);
    settings.smart_reentry_rsi_put_min = parseInt(document.getElementById(mode === 'regular' ? 'strategyRegularRsiPutMin' : 'strategyOptionsRsiPutMin')?.value || 30, 10);
    settings.smart_reentry_vwap_call_max_dist_pct = parseFloat(document.getElementById(mode === 'regular' ? 'strategyRegularVwapCallMaxPct' : 'strategyOptionsVwapCallMaxPct')?.value || 0.5);
    settings.smart_reentry_vwap_put_max_dist_pct = parseFloat(document.getElementById(mode === 'regular' ? 'strategyRegularVwapPutMaxPct' : 'strategyOptionsVwapPutMaxPct')?.value || 0.5);
    return settings;
}

function applyPreviewSettingsToStrategyForm(settings, mode) {
    ensureDailyHighLowBotIndicatorCards();
    const container = document.getElementById(`indicatorChecksContainer_${mode}`);
    const enabled = new Set(Array.isArray(settings.indicators) ? settings.indicators : []);
    if (container) {
        container.querySelectorAll('.strategy-indicator-check').forEach(chk => {
            chk.checked = enabled.has(chk.value);
        });
    }

    const buyInput = document.getElementById(`strategyBuyThreshold_${mode}`);
    const sellInput = document.getElementById(`strategySellThreshold_${mode}`);
    if (buyInput) buyInput.value = settings.min_buy_signals ?? 0;
    if (sellInput) sellInput.value = settings.min_sell_signals ?? 0;
    if (mode === 'options') {
        const putInput = document.getElementById('strategyPutEntryThreshold_options');
        if (putInput) putInput.value = settings.min_sell_signals ?? 0;
    }

    updateStrategySignalLabels(mode);
    if (mode === 'options' && typeof syncOptionsPutEntryThresholdLabel === 'function') {
        syncOptionsPutEntryThresholdLabel();
    }
    if (typeof window.updateRegularEntryFields === 'function') window.updateRegularEntryFields();
    if (typeof window.updateRegularExitFields === 'function') window.updateRegularExitFields();
    if (typeof window.updateOptionsEntryFields === 'function') window.updateOptionsEntryFields();
    if (typeof window.updateOptionsExitFields === 'function') window.updateOptionsExitFields();
    if (typeof window.updateLivePreview_bots === 'function') {
        window.updateLivePreview_bots('regular', 'both');
        window.updateLivePreview_bots('options', 'both');
    }
}

function setBotChartPreviewLoading(message = 'Loading chart preview...') {
    const boxLoading = document.getElementById('botChartPreviewBoxLoading');
    const boxLoadingText = document.getElementById('botChartPreviewBoxLoadingText');
    if (boxLoading) boxLoading.classList.remove('hidden');
    if (boxLoadingText) boxLoadingText.textContent = message;

    const tickerEl = document.getElementById('botChartPreviewTickerLabel');
    const priceEl = document.getElementById('botChartPreviewPrice');
    const changeEl = document.getElementById('botChartPreviewPriceChange');
    const titleEl = document.getElementById('botChartPreviewTitle');
    const lastInfoEl = document.getElementById('botChartPreviewLastBarInfo');

    if (tickerEl) tickerEl.textContent = '';
    if (priceEl) priceEl.textContent = '';
    if (changeEl) changeEl.textContent = '';
    if (titleEl) titleEl.textContent = '';
    if (lastInfoEl) lastInfoEl.textContent = '';

    ['botChartPreviewOhlcvO', 'botChartPreviewOhlcvH', 'botChartPreviewOhlcvL', 'botChartPreviewOhlcvC', 'botChartPreviewOhlcvV'].forEach(id => {
        const el = document.getElementById(id);
        if (el) el.textContent = '—';
    });

    const reasonEl = document.getElementById('botChartPreviewReason');
    const grid = document.getElementById('botChartPreviewSignalGrid');
    const premiumGrid = document.getElementById('botChartPreviewPremiumSignalGrid');
    const loading = document.getElementById('botChartPreviewGridLoading');
    if (reasonEl) reasonEl.textContent = message;
    if (loading) loading.classList.remove('hidden');
    if (premiumGrid) premiumGrid.innerHTML = '';
    if (grid) {
        grid.innerHTML = `
            <div class="col-span-full signal-card neutral px-4 py-6 text-center text-sm text-slate-500">
                ${message}
            </div>
        `;
    }
}

function updateBotChartPreviewVisibleHighLow() {
    const bars = botChartPreviewDisplayHistory || [];
    if (!bars.length || !botChartPreviewChart || !botChartPreviewCandleSeries) return;
    let visibleBars = bars;
    try {
        const range = botChartPreviewChart.timeScale().getVisibleRange();
        if (range?.from && range?.to) {
            visibleBars = bars.filter(b => b.time >= range.from && b.time <= range.to);
        }
    } catch (e) { }
    if (!visibleBars.length) visibleBars = bars;

    const highBar = visibleBars.reduce((max, b) => (b.high > max.high ? b : max), visibleBars[0]);
    const lowBar = visibleBars.reduce((min, b) => (b.low < min.low ? b : min), visibleBars[0]);

    if (window._botChartPreviewHlLines) {
        window._botChartPreviewHlLines.forEach(l => {
            try { botChartPreviewCandleSeries.removePriceLine(l); } catch (e) { }
        });
    }

    if (highBar && lowBar && typeof botChartPreviewCandleSeries.createPriceLine === 'function') {
        window._botChartPreviewHlLines = [
            botChartPreviewCandleSeries.createPriceLine({
                price: highBar.high,
                color: 'rgba(16,185,129,0.75)',
                lineWidth: 1,
                lineStyle: 1,
                axisLabelVisible: true,
                title: `${formatBotChartPreviewPrice(highBar.high)}`
            }),
            botChartPreviewCandleSeries.createPriceLine({
                price: lowBar.low,
                color: 'rgba(239,68,68,0.75)',
                lineWidth: 1,
                lineStyle: 1,
                axisLabelVisible: true,
                title: `${formatBotChartPreviewPrice(lowBar.low)}`
            }),
        ];
    }
}

function scheduleBotChartPreviewRangeUiRefresh() {
    if (botChartPreviewRangeUiFrame) return;
    botChartPreviewRangeUiFrame = requestAnimationFrame(() => {
        botChartPreviewRangeUiFrame = null;
        updateBotChartPreviewVisibleHighLow();
        refreshBotChartPreviewPriceLineLabels();
    });
}

function syncBotChartPreviewOscillatorRanges(range) {
    if (!range) return;
    Object.values(botChartPreviewOscCharts).forEach(chart => {
        try {
            if (chart) chart.timeScale().setVisibleLogicalRange(range);
        } catch (e) { }
    });
    window.StandardIndicatorSubcharts?.sync('bots', range);
}

function scheduleBotChartPreviewOscillatorRangeSync(range) {
    if (!range) return;
    if (botChartPreviewOscSyncFrame) cancelAnimationFrame(botChartPreviewOscSyncFrame);
    botChartPreviewOscSyncFrame = requestAnimationFrame(() => {
        botChartPreviewOscSyncFrame = null;
        syncBotChartPreviewOscillatorRanges(range);
    });
}

function ensureBotChartPreviewChart() {
    const container = document.getElementById('botChartPreviewChart');
    if (!container || typeof LightweightCharts === 'undefined') return null;
    if (botChartPreviewChart) return botChartPreviewChart;

    botChartPreviewChart = LightweightCharts.createChart(container, {
        width: Math.max(container.clientWidth, 280),
        height: Math.max(container.clientHeight, 240),
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
            background: { color: 'transparent' },
            textColor: '#64748b',
        },
        grid: {
            vertLines: { color: 'rgba(148, 163, 184, 0.12)' },
            horzLines: { color: 'rgba(148, 163, 184, 0.12)' },
        },
        rightPriceScale: {
            borderColor: 'rgba(148, 163, 184, 0.28)',
            minimumWidth: 75,
        },
        localization: {
            timeFormatter: time => {
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
        },
        timeScale: {
            borderColor: 'rgba(148, 163, 184, 0.28)',
            timeVisible: true,
            secondsVisible: false,
            tickMarkFormatter: (time, tickMarkType) => {
                const d = new Date(time * 1000);
                if (tickMarkType <= 2) {
                    return d.toLocaleDateString('en-US', { timeZone: 'America/Chicago', month: 'short', day: 'numeric' });
                }
                return d.toLocaleTimeString('en-US', { timeZone: 'America/Chicago', hour: '2-digit', minute: '2-digit', hour12: false });
            }
        },
        crosshair: {
            vertLine: { color: 'rgba(129, 140, 248, 0.35)' },
            horzLine: { color: 'rgba(167, 139, 250, 0.28)' },
        },
    });

    botChartPreviewExtHoursSeries = botChartPreviewChart.addHistogramSeries({
        color: 'rgba(100, 116, 139, 0.12)',
        priceFormat: { type: 'custom', formatter: () => '' },
        priceScaleId: 'ext-hours-bg',
        scaleMargins: { top: 0, bottom: 0 },
        autoscaleInfoProvider: () => ({
            priceRange: { minValue: 0, maxValue: 100 },
        }),
    });
    botChartPreviewChart.priceScale('ext-hours-bg').applyOptions({
        visible: false,
        scaleMargins: { top: 0, bottom: 0 }
    });

    botChartPreviewCandleSeries = botChartPreviewChart.addCandlestickSeries({
        upColor: '#10b981',
        downColor: '#ef4444',
        borderVisible: true,
        borderUpColor: '#10b981',
        borderDownColor: '#ef4444',
        wickUpColor: '#10b981',
        wickDownColor: '#ef4444',
    });

    botChartPreviewVolumeProfileOverlay = window.VolumeProfileOverlay?.attach({
        chart: botChartPreviewChart,
        priceSeries: botChartPreviewCandleSeries,
        container,
        profile: botChartPreviewState.volumeProfile,
        candles: botChartPreviewState.priceHistory || [],
        enabled: false,
        onProfileChange: (profile, enabled) => {
            if (!enabled || !profile || !Number.isFinite(profile.startTime) || !Number.isFinite(profile.endTime)) return;
            const visibleBars = botChartPreviewDisplayHistory.filter(bar => (
                Number(bar.time) >= profile.startTime && Number(bar.time) <= profile.endTime
            ));
            const lineData = value => visibleBars.map(bar => ({ time: Number(bar.time), value: Number(value) }));
            setBotChartPreviewLineSeries('volume_profile_poc', lineData(profile.poc), { color: '#7c2d12', lineWidth: 3, lineStyle: 0, priceLineTitle: 'POC' });
            setBotChartPreviewLineSeries('volume_profile_vah', lineData(profile.vah), { color: '#ea580c', lineWidth: 2, lineStyle: 2, priceLineTitle: 'VAH' });
            setBotChartPreviewLineSeries('volume_profile_val', lineData(profile.val), { color: '#0f766e', lineWidth: 2, lineStyle: 2, priceLineTitle: 'VAL' });
        },
    }) || null;

    botChartPreviewVolumeSeries = botChartPreviewChart.addHistogramSeries({
        priceFormat: { type: 'volume' },
        priceScaleId: 'volume',
        scaleMargins: { top: 0.8, bottom: 0 },
    });
    botChartPreviewChart.priceScale('volume').applyOptions({
        scaleMargins: { top: 0.8, bottom: 0 },
        visible: false,
    });

    const resizeChart = () => {
        if (!botChartPreviewChart || !container) return;
        botChartPreviewChart.applyOptions({
            width: Math.max(container.clientWidth, 280),
            height: Math.max(container.clientHeight, 240),
        });
    };

    if (typeof ResizeObserver !== 'undefined') {
        botChartPreviewResizeObserver = new ResizeObserver(() => resizeChart());
        botChartPreviewResizeObserver.observe(container);
    } else {
        window.addEventListener('resize', resizeChart);
    }

    initBotChartPreviewOscPanes();

    botChartPreviewChart.timeScale().subscribeVisibleLogicalRangeChange(range => {
        scheduleBotChartPreviewOscillatorRangeSync(range);
        scheduleBotChartPreviewRangeUiRefresh();
    });

    try {
        botChartPreviewChart.timeScale().subscribeVisibleTimeRangeChange(() => {
            scheduleBotChartPreviewRangeUiRefresh();
        });
    } catch (e) { }

    return botChartPreviewChart;
}

function initBotChartPreviewOscPanes() {
    const panes = [
        { id: 'botChartPreviewOsc_rsi', key: 'rsi' },
        { id: 'botChartPreviewOsc_macd', key: 'macd' },
        { id: 'botChartPreviewOsc_mystic', key: 'mystic' },
        { id: 'botChartPreviewOsc_adx', key: 'adx' },
        { id: 'botChartPreviewOsc_botbulls1', key: 'botbulls1' }
    ];
    panes.forEach(({ id, key }) => {
        const container = document.getElementById(id);
        if (container && !botChartPreviewOscCharts[key]) {
            container.innerHTML = '';
            botChartPreviewOscCharts[key] = LightweightCharts.createChart(container, {
                layout: { background: { color: '#fafbff' }, textColor: '#64748b' },
                grid: { vertLines: { color: 'rgba(168,85,247,0.04)' }, horzLines: { color: 'rgba(168,85,247,0.04)' } },
                rightPriceScale: { borderColor: 'rgba(197,203,206,0.6)', minimumWidth: 75 },
                localization: {
                    timeFormatter: time => {
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
                },
                timeScale: { visible: false },
            });
            if (typeof ResizeObserver !== 'undefined') {
                const ro = new ResizeObserver(entries => {
                    if (!entries[0] || !botChartPreviewOscCharts[key]) return;
                    botChartPreviewOscCharts[key].applyOptions({
                        width: entries[0].contentRect.width,
                        height: entries[0].contentRect.height
                    });
                });
                ro.observe(container);
            }
        }
    });
}

function clearBotChartPreviewSeries() {
    if (!botChartPreviewChart) return;
    Object.values(botChartPreviewOverlaySeries).forEach(series => {
        try {
            botChartPreviewChart.removeSeries(series);
        } catch (err) {
            console.warn('[bot-chart] Failed removing preview series:', err);
        }
    });
    botChartPreviewOverlaySeries = {};
    botChartPreviewOverlayPriceLines = {};
    botChartPreviewOverlayLabelMeta = {};
}

function getBotChartPreviewRightVisiblePoint(data) {
    if (!Array.isArray(data) || data.length === 0 || !botChartPreviewChart) return null;
    let from = NaN;
    let to = NaN;
    try {
        const range = botChartPreviewChart.timeScale().getVisibleRange();
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

function refreshBotChartPreviewPriceLineLabel(seriesKey) {
    const meta = botChartPreviewOverlayLabelMeta[seriesKey];
    const series = botChartPreviewOverlaySeries[seriesKey];
    if (!meta || !series) return;
    if (botChartPreviewOverlayPriceLines[seriesKey]) {
        try { series.removePriceLine(botChartPreviewOverlayPriceLines[seriesKey]); } catch (e) { }
        delete botChartPreviewOverlayPriceLines[seriesKey];
    }
    const point = getBotChartPreviewRightVisiblePoint(meta.data);
    const price = Number(point?.value);
    if (Number.isFinite(price)) {
        botChartPreviewOverlayPriceLines[seriesKey] = series.createPriceLine({
            price,
            color: meta.color,
            lineWidth: 1,
            lineStyle: meta.lineStyle,
            axisLabelVisible: true,
            lineVisible: false,
            title: meta.title,
        });
    }
}

function refreshBotChartPreviewPriceLineLabels() {
    Object.keys(botChartPreviewOverlayLabelMeta).forEach(refreshBotChartPreviewPriceLineLabel);
}

function setBotChartPreviewLineSeries(seriesKey, data, options = {}) {
    if (!botChartPreviewChart) return;
    const { priceLineTitle, ...seriesOptions } = options;
    const safeData = sanitizeBotChartPreviewLineData(data);
    if (!botChartPreviewOverlaySeries[seriesKey]) {
        botChartPreviewOverlaySeries[seriesKey] = botChartPreviewChart.addLineSeries({
            lineWidth: 2,
            priceLineVisible: false,
            lastValueVisible: false,
            crosshairMarkerVisible: false,
            ...seriesOptions,
        });
    }
    botChartPreviewOverlaySeries[seriesKey].setData(safeData);
    if (priceLineTitle && safeData.length) {
        botChartPreviewOverlayLabelMeta[seriesKey] = {
            data: safeData,
            title: priceLineTitle,
            color: seriesOptions.color || '#64748b',
            lineStyle: seriesOptions.lineStyle ?? 0,
        };
        refreshBotChartPreviewPriceLineLabel(seriesKey);
    } else {
        delete botChartPreviewOverlayLabelMeta[seriesKey];
    }
}

function updateBotChartPreviewChart(priceHistory, settings) {
    const chart = ensureBotChartPreviewChart();
    if (!chart || !botChartPreviewCandleSeries) return;

    const rawHistory = Array.isArray(priceHistory) ? [...priceHistory] : [];
    rawHistory.sort((a, b) => Number(a.time || 0) - Number(b.time || 0));
    const history = getBotChartPreviewDisplayHistory(rawHistory, settings);

    if (botChartPreviewExtHoursSeries) {
        if (!isCrypto(botChartPreviewState.ticker) && botChartPreviewSession === 'extended' && settings.timeframe !== '1Day') {
            const extData = sanitizeBotChartPreviewLineData(history.map(b => {
                const isExt = !isBotChartPreviewRegularMarketBar(b);
                return {
                    time: Number(b.time),
                    value: isExt ? 100 : 0,
                    color: isExt ? 'rgba(100, 116, 139, 0.12)' : 'rgba(0,0,0,0)'
                };
            }));
            botChartPreviewExtHoursSeries.setData(extData);
        } else {
            botChartPreviewExtHoursSeries.setData([]);
        }
    }

    const enabled = new Set(getEffectivePreviewIndicators(botChartPreviewState.signals, settings));
    const candles = sanitizeBotChartPreviewCandles(history);
    const displayedCandles = enabled.has('Candle Patterns')
        ? (window.StandardIndicatorSubcharts?.highlightCandlePatternContext?.(candles, history, {
            rolling24Hours: isCrypto(tickerFromPreviewState()),
        }) || candles)
        : candles;

    botChartPreviewCandleSeries.setData(displayedCandles);
    if (botChartPreviewVolumeSeries) {
        botChartPreviewVolumeSeries.setData(history.map(b => ({
            time: Number(b.time),
            value: Number(b.volume || 0),
            color: (b.close >= b.open) ? 'rgba(16, 185, 129, 0.45)' : 'rgba(239, 68, 68, 0.45)',
        })));
    }
    updateBotChartPreviewHeader(rawHistory, settings);
    clearBotChartPreviewSeries();

    if (!botChartPreviewCrosshairWired) {
        botChartPreviewCrosshairWired = true;
        chart.subscribeCrosshairMove(param => {
            if (!param || !param.seriesData || !botChartPreviewCandleSeries) return;
            const bar = param.seriesData.get(botChartPreviewCandleSeries);
            if (!bar) return;
            const volumeBar = botChartPreviewDisplayHistory.find(item => Number(item.time) === Number(param.time));
            setBotChartPreviewOhlcv({ ...bar, volume: volumeBar?.volume });
        });
    }

    const toLineData = key => history
        .filter(bar => bar && isValidBotChartPreviewTime(bar.time) && isFiniteBotChartPreviewNumber(bar[key]))
        .map(bar => ({ time: Number(bar.time), value: Number(bar[key]) }));

    const activeMarkers = [];

    if (enabled.has('EMA Cross')) {
        setBotChartPreviewLineSeries('ema_fast', toLineData('ema_fast'), { color: '#3b82f6', lineWidth: 1.5 });
        if (getBotIndicatorParam(settings, 'EMA_USE_SECOND_LINE', 1) !== 0) {
            setBotChartPreviewLineSeries('ema_slow', toLineData('ema_slow'), { color: '#ef4444', lineWidth: 1.5 });
        }
    }
    if (enabled.has('VWAP')) {
        setBotChartPreviewLineSeries('vwap', toLineData('vwap'), { color: '#000000', lineWidth: 2 });
    }
    if (enabled.has('Volume Profile')) {
        setBotChartPreviewLineSeries('volume_profile_poc', toLineData('volume_profile_poc'), { color: '#7c2d12', lineWidth: 3, lineStyle: 0, priceLineTitle: 'POC' });
        setBotChartPreviewLineSeries('volume_profile_vah', toLineData('volume_profile_vah'), { color: '#ea580c', lineWidth: 2, lineStyle: 2, priceLineTitle: 'VAH' });
        setBotChartPreviewLineSeries('volume_profile_val', toLineData('volume_profile_val'), { color: '#0f766e', lineWidth: 2, lineStyle: 2, priceLineTitle: 'VAL' });
    }
    if (enabled.has('Swing Points')) {
        const swingLevelCount = Math.min(5, Math.max(1, Math.round(getBotIndicatorParam(settings, 'SWING_POINTS_LEVEL_COUNT', 1))));
        for (let level = 1; level <= swingLevelCount; level += 1) {
            setBotChartPreviewLineSeries(`swing_support_${level}`, toLineData(`swing_support_${level}`), {
                color: level === 1 ? '#16a34a' : 'rgba(22, 163, 74, 0.72)',
                lineWidth: level === 1 ? 3 : 2,
                lineStyle: level === 1 ? 1 : 2,
                priceLineTitle: `SW S${level}`,
            });
            setBotChartPreviewLineSeries(`swing_resistance_${level}`, toLineData(`swing_resistance_${level}`), {
                color: level === 1 ? '#dc2626' : 'rgba(220, 38, 38, 0.72)',
                lineWidth: level === 1 ? 3 : 2,
                lineStyle: level === 1 ? 1 : 2,
                priceLineTitle: `SW R${level}`,
            });
        }
    }
    if (enabled.has('SMA')) {
        setBotChartPreviewLineSeries('sma', toLineData('sma'), { color: '#8b5cf6', lineWidth: 2.5 });
        if (getBotIndicatorParam(settings, 'SMA_USE_SECOND_LINE', 1) !== 0) {
            setBotChartPreviewLineSeries('sma_secondary', toLineData('sma_secondary'), {
                color: '#64748b',
                lineWidth: 2,
                lineStyle: 2,
            });
        }
    }
    if (enabled.has('Supertrend')) {
        const stData = history
            .filter(bar => bar && isValidBotChartPreviewTime(bar.time) && isFiniteBotChartPreviewNumber(bar.supertrend))
            .map(bar => ({
                time: Number(bar.time),
                value: Number(bar.supertrend),
                color: bar.supertrend_up ? '#10b981' : '#ef4444'
            }));
        setBotChartPreviewLineSeries('supertrend', stData, { lineWidth: 2, lineType: 0 });
    }
    if (enabled.has('Bollinger')) {
        setBotChartPreviewLineSeries('boll_upper', toLineData('boll_upper'), { color: '#f97316', lineWidth: 1.5 });
        setBotChartPreviewLineSeries('boll_middle', toLineData('boll_middle'), { color: '#3b82f6', lineWidth: 1, lineStyle: 2 });
        setBotChartPreviewLineSeries('boll_lower', toLineData('boll_lower'), { color: '#a855f7', lineWidth: 1.5 });
    }
    if (enabled.has('Prior Day High/Low')) {
        setBotChartPreviewLineSeries('daily_high_low_high', toLineData('daily_high_low_high'), { color: '#d97706', lineWidth: 3, lineStyle: 1, priceLineTitle: 'PDH' });
        setBotChartPreviewLineSeries('daily_high_low_low', toLineData('daily_high_low_low'), { color: '#0891b2', lineWidth: 3, lineStyle: 1, priceLineTitle: 'PDL' });
        setBotChartPreviewLineSeries('daily_high_low_high_trigger', toLineData('daily_high_low_high_trigger'), { color: 'rgba(217, 119, 6, 0.75)', lineWidth: 2, lineStyle: 1 });
        setBotChartPreviewLineSeries('daily_high_low_low_trigger', toLineData('daily_high_low_low_trigger'), { color: 'rgba(8, 145, 178, 0.75)', lineWidth: 2, lineStyle: 1 });
    }
    if (enabled.has('Pivot Points (S/R)')) {
        const pivotLevelCount = Math.min(5, Math.max(1, Math.round(getBotIndicatorParam(settings, 'PIVOT_LEVEL_COUNT', 1))));
        setBotChartPreviewLineSeries('pivot_p', toLineData('pivot_p'), { color: '#1f2937', lineWidth: 3, lineStyle: 2, priceLineTitle: 'P' });
        for (let level = 1; level <= pivotLevelCount; level += 1) {
            setBotChartPreviewLineSeries(`pivot_r${level}`, toLineData(`pivot_r${level}`), {
                color: level === 1 ? '#dc2626' : 'rgba(220, 38, 38, 0.78)',
                lineWidth: 2,
                lineStyle: level === 1 ? 1 : 2,
                priceLineTitle: `R${level}`,
            });
            setBotChartPreviewLineSeries(`pivot_s${level}`, toLineData(`pivot_s${level}`), {
                color: level === 1 ? '#059669' : 'rgba(5, 150, 105, 0.78)',
                lineWidth: 2,
                lineStyle: level === 1 ? 1 : 2,
                priceLineTitle: `S${level}`,
            });
        }
    }
    if (enabled.has('BotBulls2')) {
        const lxTrailData = history
            .filter(bar => bar && isValidBotChartPreviewTime(bar.time) && isFiniteBotChartPreviewNumber(bar.lx_smart_trail))
            .map(bar => ({
                time: Number(bar.time),
                value: Number(bar.lx_smart_trail),
                color: bar.lx_smart_trend ? '#10b981' : '#ef4444'
            }));
        setBotChartPreviewLineSeries('lx_smart_trail', lxTrailData, { lineWidth: 2.5 });
        setBotChartPreviewLineSeries('lx_trend_tracer', toLineData('lx_trend_tracer'), { color: '#8b5cf6', lineWidth: 1.5 });
        setBotChartPreviewLineSeries('lx_rz_upper', toLineData('lx_rz_upper'), { color: '#f59e0b', lineWidth: 1, lineStyle: 1 });
        setBotChartPreviewLineSeries('lx_rz_lower', toLineData('lx_rz_lower'), { color: '#ec4899', lineWidth: 1, lineStyle: 1 });
    }
    if (enabled.has('BotBulls3')) {
        const utTrailData = history
            .filter(bar => bar && isValidBotChartPreviewTime(bar.time) && isFiniteBotChartPreviewNumber(bar.ut_trail))
            .map(bar => ({
                time: Number(bar.time),
                value: Number(bar.ut_trail),
                color: bar.ut_above ? '#0ea5e9' : '#f43f5e'
            }));
        setBotChartPreviewLineSeries('ut_trail', utTrailData, { lineWidth: 2.5 });
    }

    (window.STANDARD_EXTENSION_OVERLAYS || []).forEach(definition => {
        if (!enabled.has(definition.name)) return;
        definition.series.forEach(spec => {
            const overlayParameters = spec.shiftPeriodsKey ? {
                [spec.shiftPeriodsKey]: getBotIndicatorParam(settings, spec.shiftPeriodsKey, spec.shiftPeriodsFallback || 0),
            } : {};
            if (definition.name === 'BotBulls5') {
                overlayParameters.rolling24Hours = isCrypto(tickerFromPreviewState());
            }
            const overlayData = window.StandardIndicatorSubcharts?.buildOverlayData
                ? window.StandardIndicatorSubcharts.buildOverlayData(history, spec, overlayParameters)
                : toLineData(spec.field);
            setBotChartPreviewLineSeries(`standard_${spec.field}`, overlayData, {
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
            rolling24Hours: isCrypto(tickerFromPreviewState()),
        }) || [];
        drawingLayers.forEach(layer => {
            setBotChartPreviewLineSeries(`bb5_drawing_${layer.key}`, layer.data, {
                color: layer.color,
                lineWidth: layer.lineWidth || 2,
                lineStyle: layer.lineStyle || 0,
            });
        });
    }
    const botBulls5VisibleTimes = enabled.has('BotBulls5')
        ? (window.StandardIndicatorSubcharts?.botBulls5RecentEventTimes
            ? window.StandardIndicatorSubcharts.botBulls5RecentEventTimes(history, {
                rolling24Hours: isCrypto(tickerFromPreviewState()),
            })
            : new Set(history.map(bar => Number(bar?.time)).filter(Number.isFinite)))
        : new Set();

    for (let i = 1; i < history.length; i++) {
        const curr = history[i];
        const prev = history[i - 1];
        if (!curr || !prev || !isValidBotChartPreviewTime(curr.time)) continue;

        if (enabled.has('Supertrend') && isFiniteBotChartPreviewNumber(curr.supertrend) && isFiniteBotChartPreviewNumber(prev.supertrend)) {
            if (curr.supertrend_up && !prev.supertrend_up) {
                activeMarkers.push({ time: Number(curr.time), position: 'belowBar', color: '#10b981', shape: 'arrowUp', text: 'ST Buy', size: 1 });
            } else if (!curr.supertrend_up && prev.supertrend_up) {
                activeMarkers.push({ time: Number(curr.time), position: 'aboveBar', color: '#ef4444', shape: 'arrowDown', text: 'ST Sell', size: 1 });
            }
        }
        if (enabled.has('BotBulls2') && isFiniteBotChartPreviewNumber(curr.lx_smart_trail) && isFiniteBotChartPreviewNumber(prev.lx_smart_trail)) {
            if (curr.lx_smart_trend && !prev.lx_smart_trend) {
                activeMarkers.push({ time: Number(curr.time), position: 'belowBar', color: '#10b981', shape: 'arrowUp', text: 'BB2 BUY', size: 1 });
            } else if (!curr.lx_smart_trend && prev.lx_smart_trend) {
                activeMarkers.push({ time: Number(curr.time), position: 'aboveBar', color: '#ef4444', shape: 'arrowDown', text: 'BB2 SELL', size: 1 });
            }
        }
        if (enabled.has('BotBulls3') && isFiniteBotChartPreviewNumber(curr.ut_trail) && isFiniteBotChartPreviewNumber(prev.ut_trail)) {
            if (curr.ut_above && !prev.ut_above) {
                activeMarkers.push({ time: Number(curr.time), position: 'belowBar', color: '#0ea5e9', shape: 'arrowUp', text: 'BB3 BUY', size: 1.2 });
            } else if (!curr.ut_above && prev.ut_above) {
                activeMarkers.push({ time: Number(curr.time), position: 'aboveBar', color: '#f43f5e', shape: 'arrowDown', text: 'BB3 SELL', size: 1.2 });
            }
        }
        if (enabled.has('BotBulls5') && botBulls5VisibleTimes.has(Number(curr.time))) {
            if (curr.bb5_breakout_bull) {
                activeMarkers.push({
                    time: Number(curr.time), position: 'belowBar', color: '#10b981', shape: 'arrowUp',
                    text: `BB5 ${String(curr.bb5_pattern || 'Bull').replaceAll('_', ' ')}`, size: 1.2
                });
            } else if (curr.bb5_breakout_bear) {
                activeMarkers.push({
                    time: Number(curr.time), position: 'aboveBar', color: '#f43f5e', shape: 'arrowDown',
                    text: `BB5 ${String(curr.bb5_pattern || 'Bear').replaceAll('_', ' ')}`, size: 1.2
                });
            }
        }
    }
    if (enabled.has('BotBulls5')) {
        const latestPattern = history.at(-1);
        if (latestPattern?.bb5_state === 'FORMING' && isValidBotChartPreviewTime(latestPattern.time)) {
            activeMarkers.push({
                time: Number(latestPattern.time),
                position: Number(latestPattern.bb5_direction) < 0 ? 'aboveBar' : 'belowBar',
                color: '#f59e0b',
                shape: 'circle',
                text: `BB5 ${String(latestPattern.bb5_pattern).replaceAll('_', ' ')} FORMING`,
                size: 1,
            });
        }
    }

    if (enabled.has('BotBulls1')) {
        for (let i = 0; i < history.length; i++) {
            const b = history[i];
            if (!b || !isValidBotChartPreviewTime(b.time)) continue;
            if (b.ba1_buy) {
                activeMarkers.push({ time: Number(b.time), position: 'belowBar', color: '#06b6d4', shape: 'arrowUp', text: 'BB1 BUY', size: 1 });
            } else if (b.ba1_sell) {
                activeMarkers.push({ time: Number(b.time), position: 'aboveBar', color: '#f43f5e', shape: 'arrowDown', text: 'BB1 SELL', size: 1 });
            }
        }
    }

    if (enabled.has('Candle Patterns')) {
        const candlePatternMarkers = window.StandardIndicatorSubcharts?.buildCandlePatternMarkers?.(history, {
            rolling24Hours: isCrypto(tickerFromPreviewState()),
        }) || [];
        activeMarkers.push(...candlePatternMarkers.filter(marker => isValidBotChartPreviewTime(marker.time)));
    }

    botChartPreviewCandleSeries.setMarkers(sanitizeBotChartPreviewMarkers(activeMarkers));

    botChartPreviewVolumeProfileOverlay?.setCandles(history);
    botChartPreviewVolumeProfileOverlay?.setProfile(
        botChartPreviewState.volumeProfile,
        enabled.has('Volume Profile'),
    );

    refreshBotChartPreviewOscillators(history, enabled, settings);

    try {
        chart.timeScale().fitContent();
        const visibleRange = chart.timeScale().getVisibleLogicalRange();
        if (visibleRange) {
            syncBotChartPreviewOscillatorRanges(visibleRange);
        }
    } catch (err) {
        console.warn('[bot-chart] Failed fitting content:', err);
    }
}

function _getOrCreateBotPreviewSeries(seriesMap, key, chart, options, type = 'line') {
    if (!seriesMap[key]) {
        if (type === 'histogram') {
            seriesMap[key] = chart.addHistogramSeries(options);
        } else {
            seriesMap[key] = chart.addLineSeries(options);
        }
    } else {
        seriesMap[key].applyOptions(options);
    }
    return seriesMap[key];
}

function getBotIndicatorParam(settings, key, fallback) {
    if (settings && settings[key] !== undefined && settings[key] !== null && settings[key] !== '') {
        const val = Number(settings[key]);
        if (Number.isFinite(val)) return val;
    }
    if (settings && settings.indicator_overrides && settings.indicator_overrides[key] !== undefined && settings.indicator_overrides[key] !== null && settings.indicator_overrides[key] !== '') {
        const val = Number(settings.indicator_overrides[key]);
        if (Number.isFinite(val)) return val;
    }
    if (settings && settings.indicator_parameters && settings.indicator_parameters[key] !== undefined && settings.indicator_parameters[key] !== null && settings.indicator_parameters[key] !== '') {
        const val = Number(settings.indicator_parameters[key]);
        if (Number.isFinite(val)) return val;
    }
    const globalOverrides = window.lastBotsData?.indicator_overrides || {};
    if (globalOverrides[key] !== undefined && globalOverrides[key] !== null && globalOverrides[key] !== '') {
        const val = Number(globalOverrides[key]);
        if (Number.isFinite(val)) return val;
    }
    const globalParams = window.lastBotsData?.indicator_parameters || {};
    if (globalParams[key] !== undefined && globalParams[key] !== null && globalParams[key] !== '') {
        const val = Number(globalParams[key]);
        if (Number.isFinite(val)) return val;
    }
    return fallback;
}

function getPriorDayHighLowPreviewLevels(priorDay, settings = null) {
    if (!priorDay || !Number.isFinite(Number(priorDay.high)) || !Number.isFinite(Number(priorDay.low))) {
        return null;
    }
    const draftOverrides = typeof getStrategyDraftIndicatorOverrides === 'function' ? getStrategyDraftIndicatorOverrides() : {};
    const sourceSettings = settings || botChartPreviewState?.settings || { indicator_overrides: draftOverrides };
    const bufferPct = Math.max(0, Number(getBotIndicatorParam(sourceSettings, 'DAILY_HIGH_LOW_BUFFER_PCT', 0)) || 0);
    const high = Number(priorDay.high);
    const low = Number(priorDay.low);
    return {
        high,
        low,
        bufferPct,
        highTrigger: high * (1 + bufferPct / 100),
        lowTrigger: low * (1 - bufferPct / 100),
    };
}

function refreshBotChartPreviewOscillators(bars = [], enabledSet = new Set(), settings = {}) {
    const clean = data => sanitizeBotChartPreviewLineData(data);

    // RSI
    const rsiCont = document.getElementById('botChartPreviewOsc_rsi');
    const rsiLabel = document.getElementById('botChartPreviewRsiLabel');
    const hasRsi = enabledSet.has('RSI');
    if (rsiCont) rsiCont.style.display = hasRsi ? 'block' : 'none';
    if (rsiLabel) rsiLabel.style.display = hasRsi ? 'block' : 'none';
    const oscRsi = botChartPreviewOscCharts['rsi'];
    if (hasRsi && oscRsi) {
        _getOrCreateBotPreviewSeries(botChartPreviewOscSeries, 'rsi', oscRsi, { color: '#818cf8', lineWidth: 1 })
            .setData(clean(bars.filter(b => b.rsi != null).map(b => ({ time: b.time, value: b.rsi }))));
        const ob = _getOrCreateBotPreviewSeries(botChartPreviewOscSeries, 'rsi_ob', oscRsi, { color: 'rgba(239,68,68,0.4)', lineWidth: 1, lineStyle: 2 });
        const os = _getOrCreateBotPreviewSeries(botChartPreviewOscSeries, 'rsi_os', oscRsi, { color: 'rgba(16,185,129,0.4)', lineWidth: 1, lineStyle: 2 });
        const rsiPeriod = getBotIndicatorParam(settings, 'RSI_PERIOD', 14);
        const rsiFirstThreshold = getBotIndicatorParam(settings, 'RSI_OVERBOUGHT', 70);
        const rsiSecondThreshold = getBotIndicatorParam(settings, 'RSI_OVERSOLD', 30);
        const obVal = Math.max(rsiFirstThreshold, rsiSecondThreshold);
        const osVal = Math.min(rsiFirstThreshold, rsiSecondThreshold);
        if (rsiLabel) {
            rsiLabel.textContent = `RSI (${rsiPeriod})  •  OB: ${obVal} / OS: ${osVal}`;
        }
        if (bars.length) {
            ob.setData(clean([{ time: bars[0].time, value: obVal }, { time: bars[bars.length - 1].time, value: obVal }]));
            os.setData(clean([{ time: bars[0].time, value: osVal }, { time: bars[bars.length - 1].time, value: osVal }]));
        }
        try { oscRsi.timeScale().fitContent(); } catch (e) { }
    }

    // MACD
    const macdCont = document.getElementById('botChartPreviewOsc_macd');
    const macdLabel = document.getElementById('botChartPreviewMacdLabel');
    const hasMacd = enabledSet.has('MACD');
    if (macdCont) macdCont.style.display = hasMacd ? 'block' : 'none';
    if (macdLabel) macdLabel.style.display = hasMacd ? 'block' : 'none';
    const oscMacd = botChartPreviewOscCharts['macd'];
    if (hasMacd && oscMacd) {
        const macdFirst = getBotIndicatorParam(settings, 'MACD_FAST', 12);
        const macdSecond = getBotIndicatorParam(settings, 'MACD_SLOW', 26);
        const macdFast = Math.min(macdFirst, macdSecond);
        const macdSlow = Math.max(macdFirst, macdSecond);
        const macdSig = getBotIndicatorParam(settings, 'MACD_SIGNAL', 9);
        if (macdLabel) {
            macdLabel.textContent = `MACD (${macdFast}, ${macdSlow}, ${macdSig})`;
        }
        _getOrCreateBotPreviewSeries(botChartPreviewOscSeries, 'macd_hist', oscMacd, {
            base: 0,
            priceFormat: { type: 'price', precision: 2, minMove: 0.01 }
        }, 'histogram').setData(clean(bars.filter(b => b.macd_hist != null).map(b => ({
            time: b.time,
            value: b.macd_hist,
            color: b.macd_hist >= 0 ? 'rgba(16, 185, 129, 0.5)' : 'rgba(239, 68, 68, 0.5)'
        }))));
        _getOrCreateBotPreviewSeries(botChartPreviewOscSeries, 'macd_line', oscMacd, {
            color: '#06b6d4',
            lineWidth: 1,
            priceFormat: { type: 'price', precision: 2, minMove: 0.01 }
        }).setData(clean(bars.filter(b => b.macd_line != null).map(b => ({ time: b.time, value: b.macd_line }))));
        _getOrCreateBotPreviewSeries(botChartPreviewOscSeries, 'macd_signal', oscMacd, {
            color: '#f97316',
            lineWidth: 1,
            priceFormat: { type: 'price', precision: 2, minMove: 0.01 }
        }).setData(clean(bars.filter(b => b.macd_signal != null).map(b => ({ time: b.time, value: b.macd_signal }))));
        try { oscMacd.timeScale().fitContent(); } catch (e) { }
    }

    // Mystic Pulse
    const mysticCont = document.getElementById('botChartPreviewOsc_mystic');
    const mysticLabel = document.getElementById('botChartPreviewMysticLabel');
    const hasMystic = enabledSet.has('Mystic Pulse');
    if (mysticCont) mysticCont.style.display = hasMystic ? 'block' : 'none';
    if (mysticLabel) mysticLabel.style.display = hasMystic ? 'block' : 'none';
    const oscMystic = botChartPreviewOscCharts['mystic'];
    if (hasMystic && oscMystic) {
        const mysticThresh = getBotIndicatorParam(settings, 'MYSTIC_PULSE_THRESHOLD', 5);
        if (mysticLabel) {
            mysticLabel.textContent = `Mystic Pulse  •  Threshold: ${mysticThresh}`;
        }
        _getOrCreateBotPreviewSeries(botChartPreviewOscSeries, 'mystic_hist', oscMystic, { base: 0 }, 'histogram')
            .setData(clean(bars.filter(b => b.mystic_bull != null || b.mystic_bear != null).map(b => {
                let val = 0, col = 'rgba(168,85,247,0.2)';
                if (b.mystic_bull > 0) { val = b.mystic_bull; col = b.mystic_bull >= mysticThresh ? 'rgba(16, 185, 129, 0.9)' : 'rgba(16, 185, 129, 0.4)'; }
                else if (b.mystic_bear > 0) { val = -b.mystic_bear; col = b.mystic_bear >= mysticThresh ? 'rgba(239, 68, 68, 0.9)' : 'rgba(239, 68, 68, 0.4)'; }
                return { time: b.time, value: val, color: col };
            })));
        if (bars.length) {
            const ob = _getOrCreateBotPreviewSeries(botChartPreviewOscSeries, 'mystic_ob', oscMystic, { color: 'rgba(16,185,129,0.35)', lineWidth: 1, lineStyle: 2 });
            const os = _getOrCreateBotPreviewSeries(botChartPreviewOscSeries, 'mystic_os', oscMystic, { color: 'rgba(239,68,68,0.35)', lineWidth: 1, lineStyle: 2 });
            ob.setData(clean([{ time: bars[0].time, value: mysticThresh }, { time: bars[bars.length - 1].time, value: mysticThresh }]));
            os.setData(clean([{ time: bars[0].time, value: -mysticThresh }, { time: bars[bars.length - 1].time, value: -mysticThresh }]));
        }
        try { oscMystic.timeScale().fitContent(); } catch (e) { }
    }

    // ADX
    const adxCont = document.getElementById('botChartPreviewOsc_adx');
    const adxLabel = document.getElementById('botChartPreviewAdxLabel');
    const hasAdx = enabledSet.has('ADX Trend') || enabledSet.has('ADX');
    if (adxCont) adxCont.style.display = hasAdx ? 'block' : 'none';
    if (adxLabel) adxLabel.style.display = hasAdx ? 'block' : 'none';
    const oscAdx = botChartPreviewOscCharts['adx'];
    if (hasAdx && oscAdx) {
        _getOrCreateBotPreviewSeries(botChartPreviewOscSeries, 'adx', oscAdx, { color: '#6366f1', lineWidth: 2 })
            .setData(clean(bars.filter(b => b.adx != null).map(b => ({ time: b.time, value: b.adx }))));
        _getOrCreateBotPreviewSeries(botChartPreviewOscSeries, 'di_plus', oscAdx, { color: '#10b981', lineWidth: 1 })
            .setData(clean(bars.filter(b => b.di_plus != null).map(b => ({ time: b.time, value: b.di_plus }))));
        _getOrCreateBotPreviewSeries(botChartPreviewOscSeries, 'di_minus', oscAdx, { color: '#ef4444', lineWidth: 1 })
            .setData(clean(bars.filter(b => b.di_minus != null).map(b => ({ time: b.time, value: b.di_minus }))));
        const adxPeriod = getBotIndicatorParam(settings, 'ADX_PERIOD', 14);
        const adxThreshVal = getBotIndicatorParam(settings, 'ADX_TRENDING_THRESHOLD', 25);
        if (adxLabel) {
            adxLabel.textContent = `ADX (${adxPeriod}) · strong ≥ ${adxThreshVal} · +DI green · −DI red`;
        }
        _getOrCreateBotPreviewSeries(botChartPreviewOscSeries, 'adx_threshold', oscAdx, { color: 'rgba(99,102,241,0.25)', lineWidth: 1, lineStyle: 2 })
            .setData(clean(bars.map(b => ({ time: b.time, value: adxThreshVal }))));
        try { oscAdx.timeScale().fitContent(); } catch (e) { }
    }

    // BotBulls1
    const bb1Cont = document.getElementById('botChartPreviewOsc_botbulls1');
    const bb1Label = document.getElementById('botChartPreviewBotbulls1Label');
    const hasBb1 = enabledSet.has('BotBulls1');
    if (bb1Cont) bb1Cont.style.display = hasBb1 ? 'block' : 'none';
    if (bb1Label) bb1Label.style.display = hasBb1 ? 'block' : 'none';
    const oscBb1 = botChartPreviewOscCharts['botbulls1'];
    if (hasBb1 && oscBb1) {
        const wtChannel = getBotIndicatorParam(settings, 'BOTBULLS1_WT_CHANNEL', 10);
        const wtAvg = getBotIndicatorParam(settings, 'BOTBULLS1_WT_AVERAGE', 21);
        const mfiBuy = getBotIndicatorParam(settings, 'BOTBULLS1_MFI_BUY_RECLAIM', 35);
        const mfiSell = getBotIndicatorParam(settings, 'BOTBULLS1_MFI_SELL_REJECT', 65);
        if (bb1Label) {
            bb1Label.textContent = `BotBulls1 (WT: ${wtChannel}, ${wtAvg} | MFI: ${mfiBuy}/${mfiSell})`;
        }
        _getOrCreateBotPreviewSeries(botChartPreviewOscSeries, 'wt1', oscBb1, { color: '#0ea5e9', lineWidth: 1.5 })
            .setData(clean(bars.filter(b => b.wt1 != null).map(b => ({ time: b.time, value: b.wt1 }))));
        _getOrCreateBotPreviewSeries(botChartPreviewOscSeries, 'wt2', oscBb1, { color: '#f43f5e', lineWidth: 1, lineStyle: 2 })
            .setData(clean(bars.filter(b => b.wt2 != null).map(b => ({ time: b.time, value: b.wt2 }))));
        try { oscBb1.timeScale().fitContent(); } catch (e) { }
    }

    const botSubchartParameters = Object.fromEntries(
        Object.entries(window.STANDARD_INDICATOR_DEFAULTS || {}).map(([key, fallback]) => [
            key,
            getBotIndicatorParam(settings, key, fallback),
        ])
    );
    let botVisibleRange = null;
    try { botVisibleRange = botChartPreviewChart?.timeScale().getVisibleLogicalRange() || null; } catch (e) { }
    window.StandardIndicatorSubcharts?.render({
        context: 'bots',
        hostId: 'botChartPreviewDynamicOscillators',
        history: bars,
        enabledIndicators: enabledSet,
        parameters: botSubchartParameters,
        height: 85,
        visibleRange: botVisibleRange,
    });

    updateBotChartPreviewVisibleHighLow();
    const boxLoading = document.getElementById('botChartPreviewBoxLoading');
    if (boxLoading) boxLoading.classList.add('hidden');
}

function renderBotChartPreviewSignals(signals = {}, settings = {}) {
    const grid = document.getElementById('botChartPreviewSignalGrid');
    const premiumGrid = document.getElementById('botChartPreviewPremiumSignalGrid');
    const loading = document.getElementById('botChartPreviewGridLoading');
    if (!grid) return;
    if (loading) loading.classList.add('hidden');
    if (premiumGrid) premiumGrid.innerHTML = '';

    const activeIndicators = getEffectivePreviewIndicators(signals, settings);
    const activeSet = new Set(activeIndicators);
    const premiumNames = new Set(['Botbulls4 News AI', 'BotBulls1', 'BotBulls2', 'BotBulls3', 'BotBulls5']);
    const allPreviewIndicators = [
        ...BOT_DEFAULT_STRATEGY_INDICATORS,
        'BotBulls1',
        'BotBulls2',
        'BotBulls3',
        'Botbulls4 News AI',
        'BotBulls5',
    ];
    const rawEntries = allPreviewIndicators.map(name => [
        name,
        signals?.[name] || {
            signal: 'NEUTRAL',
            reason: activeSet.has(name) ? 'Awaiting sufficient market data' : 'Indicator disabled',
            weight: 1,
            enabled: activeSet.has(name),
            premium: premiumNames.has(name),
        },
    ]);
    const previewOrder = new Map(BOT_CHART_PREVIEW_INDICATORS.map((name, index) => [name, index]));
    const signalEntries = rawEntries.sort(([nameA], [nameB]) => {
        const rankA = previewOrder.has(nameA) ? previewOrder.get(nameA) : Number.MAX_SAFE_INTEGER;
        const rankB = previewOrder.has(nameB) ? previewOrder.get(nameB) : Number.MAX_SAFE_INTEGER;
        if (rankA !== rankB) return rankA - rankB;
        return nameA.localeCompare(nameB);
    });
    if (signalEntries.length === 0) {
        const emptyMessage = Array.isArray(settings.indicators)
            ? 'Select at least one indicator in the Bot Indicator Pool to generate preview signals.'
            : 'Signal data is still loading for this preview.';
        grid.innerHTML = `
            <div class="col-span-full signal-card neutral px-4 py-6 text-center text-sm text-slate-500">
                ${emptyMessage}
            </div>
        `;
        return;
    }

    const standardCards = [];
    const premiumCards = [];
    signalEntries.forEach(([name, signal]) => {
        if (name === 'Botbulls4 News AI') {
            signal = applyCachedNewsAISignal({ ...(signal || {}) }, {
                ticker: botChartPreviewState?.ticker || selectedTicker,
                timeframe: settings.timeframe || currentBackendTf,
                params: settings.indicator_overrides || settings.indicator_parameters || {},
            });
        }
        const signalType = signal?.signal || 'NEUTRAL';
        const enabled = Array.isArray(settings.indicators)
            ? activeSet.has(name)
            : activeSet.has(name) || signal?.enabled === true;
        const isPremium = signal?.premium === true || premiumNames.has(name);
        const cardClass = !enabled
            ? 'disabled-signal'
            : isPremium
                ? (signalType === 'BULLISH' ? 'premium-bullish' : signalType === 'BEARISH' ? 'premium-bearish' : 'premium-neutral')
                : (signalType === 'BULLISH' ? 'bullish' : signalType === 'BEARISH' ? 'bearish' : 'neutral');
        const icon = signalType === 'BULLISH' ? '▲' : signalType === 'BEARISH' ? '▼' : '●';
        const iconColor = signalType === 'BULLISH' ? 'text-emerald-600' : signalType === 'BEARISH' ? 'text-red-500' : 'text-purple-400';
        const escapedName = name.replace(/'/g, "\\'");
        const displayName = name === 'VWAP' ? 'Session VWAP' : name;
        const displayLabelClass = name === 'VWAP' ? 'vwap-indicator-label' : (isPremium ? 'text-amber-800' : 'text-indigo-900');
        const settingAction = `openBotChartPreviewIndicatorSettings('${escapedName}')`;
        const cardClick = `onclick="toggleBotChartPreviewIndicator('${escapedName}')"`;
        let reasonHtml = signal?.reason || 'No reason available.';

        if (name === 'Botbulls4 News AI' && signal?.value != null && enabled) {
            const scoreVal = parseFloat(signal.value);
            if (!Number.isNaN(scoreVal)) {
                const sentPct = Math.round(
                    typeof normalizeAISentimentPct === 'function'
                        ? normalizeAISentimentPct(signal.percentage ?? signal.sentiment_pct, scoreVal)
                        : (((scoreVal + 1.0) / 2.0) * 100)
                );
                const overrides = settings.indicator_overrides || {};
                const bullishThresh = overrides.SENTIMENT_BULLISH_PERCENTAGE ?? INDICATOR_DEFAULTS.SENTIMENT_BULLISH_PERCENTAGE ?? 70;
                const bearishThresh = overrides.SENTIMENT_BEARISH_PERCENTAGE ?? INDICATOR_DEFAULTS.SENTIMENT_BEARISH_PERCENTAGE ?? 30;
                const scoreColor = sentPct >= bullishThresh
                    ? 'text-emerald-600'
                    : sentPct <= bearishThresh
                        ? 'text-rose-600'
                        : 'text-slate-500';
                const reasonText = String(signal.reason || 'Evaluating market sentiment').replace(/catalysts/i, 'catalyst');
                reasonHtml = `${reasonText}: <span class="${scoreColor} font-black">${sentPct}%</span>`;
            }
        }

        const card = isPremium
            ? `
                <div class="signal-card group ${cardClass} fade-in cursor-pointer transition-all duration-300" ${cardClick}>
                    <div class="flex items-center justify-between mb-1">
                        <div class="flex items-center gap-1.5 min-w-0">
                            <span class="font-bold text-xs ${displayLabelClass} ${!enabled ? 'opacity-50' : ''} transition-opacity duration-300 truncate">${displayName}</span>
                            <div class="group/tooltip relative inline-flex items-center shrink-0">
                                <svg onclick="event.stopPropagation(); event.preventDefault(); typeof openInfoModal === 'function' ? openInfoModal('${name}') : null" xmlns="http://www.w3.org/2000/svg" class="h-3.5 w-3.5 text-amber-400 hover:text-amber-600 cursor-pointer transition-colors" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                                    <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M13 16h-1v-4h-1m1-4h.01M21 12a9 9 0 11-18 0 9 9 0 0118 0z" />
                                </svg>
                                <div class="invisible group-hover/tooltip:visible opacity-0 group-hover/tooltip:opacity-100 transition-all absolute bottom-full left-1/2 -translate-x-1/2 mb-2 px-3 py-1.5 bg-amber-900 text-white text-[0.65rem] leading-snug rounded-lg shadow-2xl z-[999] pointer-events-none whitespace-nowrap text-center font-bold normal-case tracking-normal">
                                    Click to read guide
                                    <div class="absolute top-full left-1/2 -translate-x-1/2 border-4 border-transparent border-t-amber-900"></div>
                                </div>
                            </div>
                            <button class="opacity-70 hover:opacity-100 transition-opacity p-0.5 rounded hover:bg-amber-100/50"
                                    onclick="event.stopPropagation(); ${settingAction}"
                                    title="Indicator Settings">
                                <svg xmlns="http://www.w3.org/2000/svg" class="h-3 w-3 text-amber-600" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                                    <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2.5" d="M10.325 4.317c.426-1.756 2.924-1.756 3.35 0a1.724 1.724 0 002.573 1.066c1.543-.94 3.31.826 2.37 2.37a1.724 1.724 0 001.065 2.572c1.756.426 1.756 2.924 0 3.35a1.724 1.724 0 00-1.066 2.573c.94 1.543-.826 3.31-2.37 2.37a1.724 1.724 0 00-2.572 1.065c-.426 1.756-2.924 1.756-3.35 0a1.724 1.724 0 00-2.573-1.066c-1.543.94-3.31-.826-2.37-2.37a1.724 1.724 0 00-1.065-2.572c-1.756-.426-1.756-2.924 0-3.35a1.724 1.724 0 001.066-2.573c-.94-1.543.826-3.31 2.37-2.37.996.608 2.296.07 2.572-1.065z" />
                                    <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2.5" d="M15 12a3 3 0 11-6 0 3 3 0 016 0z" />
                                </svg>
                            </button>
                        </div>
                        <span class="${iconColor} text-sm font-bold ${!enabled ? 'opacity-50 grayscale' : ''} transition-all duration-300">${icon}</span>
                    </div>
                    <p class="text-[0.6rem] text-amber-700 leading-tight line-clamp-1 ${!enabled ? 'opacity-50' : ''} transition-opacity duration-300">${reasonHtml}</p>
                </div>
            `
            : `
                <div class="signal-card group ${cardClass} fade-in cursor-pointer transition-all duration-300" ${cardClick}>
                    <div class="flex items-center justify-between mb-1">
                        <div class="flex items-center gap-1.5 min-w-0">
                            <span class="font-bold text-xs ${displayLabelClass} ${!enabled ? 'opacity-50' : ''} transition-opacity duration-300 truncate">${displayName}</span>
                            <div class="group/tooltip relative inline-flex items-center shrink-0">
                                <svg onclick="event.stopPropagation(); event.preventDefault(); typeof openInfoModal === 'function' ? openInfoModal('${name}') : null" xmlns="http://www.w3.org/2000/svg" class="h-3.5 w-3.5 text-indigo-300 hover:text-indigo-600 cursor-pointer transition-colors" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                                    <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M13 16h-1v-4h-1m1-4h.01M21 12a9 9 0 11-18 0 9 9 0 0118 0z" />
                                </svg>
                                <div class="invisible group-hover/tooltip:visible opacity-0 group-hover/tooltip:opacity-100 transition-all absolute bottom-full left-1/2 -translate-x-1/2 mb-2 px-3 py-1.5 bg-indigo-900 text-white text-[0.65rem] leading-snug rounded-lg shadow-2xl z-[999] pointer-events-none whitespace-nowrap text-center font-bold normal-case tracking-normal">
                                    Click to read guide
                                    <div class="absolute top-full left-1/2 -translate-x-1/2 border-4 border-transparent border-t-indigo-900"></div>
                                </div>
                            </div>
                        </div>
                        <div class="flex items-center gap-2 shrink-0">
                            <button class="opacity-70 hover:opacity-100 transition-opacity p-0.5 rounded hover:bg-indigo-50"
                                    onclick="event.stopPropagation(); ${settingAction}"
                                    title="Indicator Settings">
                                <svg xmlns="http://www.w3.org/2000/svg" class="h-3 w-3 text-indigo-500" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                                    <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2.5" d="M10.325 4.317c.426-1.756 2.924-1.756 3.35 0a1.724 1.724 0 002.573 1.066c1.543-.94 3.31.826 2.37 2.37a1.724 1.724 0 001.065 2.572c1.756.426 1.756 2.924 0 3.35a1.724 1.724 0 00-1.066 2.573c.94 1.543-.826 3.31-2.37 2.37a1.724 1.724 0 00-2.572 1.065c-.426 1.756-2.924 1.756-3.35 0a1.724 1.724 0 00-2.573-1.066c-1.543.94-3.31-.826-2.37-2.37a1.724 1.724 0 00-1.065-2.572c-1.756-.426-1.756-2.924 0-3.35a1.724 1.724 0 001.066-2.573c-.94-1.543.826-3.31 2.37-2.37.996.608 2.296.07 2.572-1.065z" />
                                    <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2.5" d="M15 12a3 3 0 11-6 0 3 3 0 016 0z" />
                                </svg>
                            </button>
                            <span class="${iconColor} text-sm font-bold ${!enabled ? 'opacity-50 grayscale' : ''} transition-all duration-300">${icon}</span>
                        </div>
                    </div>
                    <p class="text-[0.6rem] text-purple-600 leading-tight line-clamp-1 ${!enabled ? 'opacity-50' : ''} transition-opacity duration-300">${reasonHtml}</p>
                </div>
            `;

        if (isPremium) premiumCards.push(card);
        else standardCards.push(card);
    });

    const isMobileView = typeof window !== 'undefined' && window.innerWidth <= 768;

    if (standardCards.length > 0) {
        grid.style.gridTemplateColumns = isMobileView ? 'repeat(3, minmax(0, 1fr))' : 'repeat(4, minmax(0, 1fr))';
        grid.innerHTML = standardCards.join('');
    } else if (premiumCards.length > 0) {
        grid.style.gridTemplateColumns = '';
        grid.innerHTML = '';
    } else {
        grid.style.gridTemplateColumns = '';
        grid.innerHTML = `
            <div class="col-span-full signal-card neutral px-4 py-6 text-center text-sm text-slate-500">
                Signal data is still loading for the selected indicators.
            </div>
        `;
    }

    if (premiumGrid) {
        if (premiumCards.length > 0) {
            premiumGrid.style.gridTemplateColumns = 'repeat(3, minmax(0, 1fr))';
            premiumGrid.innerHTML = premiumCards.join('');
        } else {
            premiumGrid.style.gridTemplateColumns = '';
            premiumGrid.innerHTML = '';
        }
    }
}

function renderBotChartPreview(payload) {
    const settings = botChartPreviewState.settings || {};
    const previewMeta = payload?.preview_meta || {};
    const signals = resolveBotChartPreviewPayloadSignals(payload);
    const previewCounts = getBotChartPreviewSignalCounts(signals, settings);
    const bullCount = previewCounts.bullish;
    const bearCount = previewCounts.bearish;
    const reason = payload?.reason || payload?.primaryScan?.reason || 'Preview ready.';
    const aiBadge = document.getElementById('botChartPreviewAiBadge');
    const banner = document.getElementById('botChartPreviewBanner');
    const modeBadge = document.getElementById('botChartPreviewModeBadge');
    const openSettingsBtn = document.getElementById('botChartPreviewOpenSettingsButton');
    const applyBtn = document.getElementById('botChartPreviewApplyButton');
    const reasonEl = document.getElementById('botChartPreviewReason');

    if (modeBadge) modeBadge.textContent = botChartPreviewState.mode === 'edit' ? 'Edit' : 'View';
    if (document.getElementById('botChartPreviewTitle')) {
        document.getElementById('botChartPreviewTitle').textContent = `${botChartPreviewState.ticker} Chart Preview`;
    }
    if (document.getElementById('botChartPreviewSubtitle')) {
        document.getElementById('botChartPreviewSubtitle').textContent = botChartPreviewState.mode === 'edit'
            ? 'Draft-safe preview of the bot settings you are editing right now.'
            : 'Saved bot chart preview from the Bots page.';
    }
    if (reasonEl) reasonEl.textContent = reason;
    if (document.getElementById('botChartPreviewBullCount')) {
        document.getElementById('botChartPreviewBullCount').textContent = `${bullCount} Bullish`;
    }
    if (document.getElementById('botChartPreviewBearCount')) {
        document.getElementById('botChartPreviewBearCount').textContent = `${bearCount} Bearish`;
    }
    const selectedCount = previewCounts.selectedCount;
    const entryThresholdEl = document.getElementById('botChartPreviewEntryThreshold');
    const exitThresholdEl = document.getElementById('botChartPreviewExitThreshold');
    if (entryThresholdEl) {
        const entryThreshold = normalizeSignalThreshold(settings.min_buy_signals, selectedCount, 'buy');
        entryThresholdEl.textContent = `Entry ${entryThreshold}/${selectedCount}`;
    }
    if (exitThresholdEl) {
        const exitThreshold = normalizeSignalThreshold(settings.min_sell_signals, selectedCount, 'sell');
        exitThresholdEl.textContent = `Exit ${exitThreshold}/${selectedCount}`;
    }

    if (aiBadge) {
        const aiStatus = previewMeta.ai_status || 'disabled';
        if (aiStatus === 'disabled') {
            aiBadge.classList.add('hidden');
        } else {
            aiBadge.classList.remove('hidden');
            aiBadge.textContent = aiStatus === 'available' ? 'AI Ready' : 'AI Neutral';
        }
    }

    if (banner) {
        const bannerText = botChartPreviewState.mode === 'edit'
            ? 'Changes here stay draft-only until you hit Save Settings or Deploy Strategy.'
            : 'Viewing active bot configuration. Click "Open Bot Settings" below to edit indicators or adjust strategy parameters.';
        banner.textContent = bannerText;
        banner.classList.remove('hidden');
    }

    if (openSettingsBtn) openSettingsBtn.classList.toggle('hidden', botChartPreviewState.mode !== 'view');
    if (applyBtn) applyBtn.classList.toggle('hidden', botChartPreviewState.mode !== 'edit');

    renderBotChartPreviewSignals(signals, settings);
    updateBotChartPreviewChart(payload?.priceHistory || payload?.price_history || [], settings);
}

async function requestBotChartPreviewPayload(ticker, settings, force = false) {
    const requestSettings = clonePlainObject(settings || {});
    if (isCrypto((ticker || '').toUpperCase())) {
        requestSettings.market_session = '24h';
    }
    const requestKey = `${getBotChartPreviewCacheKey(ticker, requestSettings)}|force=${force ? '1' : '0'}`;
    const existingRequest = botChartPreviewRequests.get(requestKey);
    if (existingRequest) return existingRequest;

    const requestPromise = (async () => {
        const headers = await getAuthHeaders();
        const response = await fetchWithTimeout(`${API_BASE}/api/bots/chart-preview`, {
            method: 'POST',
            headers,
            body: JSON.stringify({
                ticker,
                timeframe: requestSettings.timeframe,
                settings: requestSettings,
                force,
            }),
        }, BOT_CHART_PREVIEW_FETCH_TIMEOUT_MS);

        if (!response.ok) {
            const err = await response.json().catch(() => ({}));
            throw new Error(err.detail || err.message || `Chart preview failed (${response.status})`);
        }

        const payload = await response.json();
        if (payload?.error) {
            throw new Error(payload.error);
        }
        return payload;
    })();
    botChartPreviewRequests.set(requestKey, requestPromise);
    try {
        return await requestPromise;
    } finally {
        if (botChartPreviewRequests.get(requestKey) === requestPromise) {
            botChartPreviewRequests.delete(requestKey);
        }
    }
}

function applyBotChartPreviewPayload(payload) {
    const settings = botChartPreviewState.settings || {};
    botChartPreviewState.signals = resolveBotChartPreviewPayloadSignals(payload);
    botChartPreviewState.priceHistory = payload?.priceHistory || payload?.price_history || [];
    botChartPreviewState.volumeProfile = payload?.volumeProfile || payload?.volume_profile || {};
    botChartPreviewState.previewMeta = payload?.preview_meta || {};
    const effectiveIndicators = getEffectivePreviewIndicators(botChartPreviewState.signals, settings);
    if (!Array.isArray(botChartPreviewState.settings.indicators)) {
        botChartPreviewState.settings.indicators = effectiveIndicators;
    }
    renderBotChartPreview(payload);
}

function getBotChartPreviewLiveRefreshMs(settings = {}) {
    const minutes = getTimeframeMinutes(settings.timeframe || '1Hour');
    if (minutes <= 5) return 30000;
    if (minutes <= 30) return 60000;
    if (minutes <= 60) return 120000;
    if (minutes <= 240) return 180000;
    return 300000;
}

function scheduleBotChartPreviewLiveRefresh() {
    clearTimeout(botChartPreviewLiveRefreshTimer);
    if (!botChartPreviewState.open) return;
    const delay = getBotChartPreviewLiveRefreshMs(botChartPreviewState.settings || {});
    botChartPreviewLiveRefreshTimer = setTimeout(() => {
        if (!botChartPreviewState.open || document.hidden) {
            scheduleBotChartPreviewLiveRefresh();
            return;
        }
        refreshOpenBotChartPreview(false, { showLoading: false, allowCachedRender: false, forceNetwork: true });
    }, delay);
}

async function refreshOpenBotChartPreview(force = false, options = {}) {
    if (!botChartPreviewState.open || !botChartPreviewState.ticker) return;
    const { showLoading = true, allowCachedRender = !force, forceNetwork = force } = options;
    const ticker = botChartPreviewState.ticker;
    const settings = clonePlainObject(botChartPreviewState.settings || {});
    const cacheKey = getBotChartPreviewCacheKey(ticker, settings);
    let renderedCached = false;

    if (allowCachedRender) {
        const cached = await readBotChartPreviewCache(cacheKey);
        // Render the last successful preview immediately, even if it is older
        // than the freshness window. The network request below still runs,
        // but a slow provider cannot leave the user staring at an empty chart.
        // Freshness controls whether this is considered current, not whether
        // it is useful to display while refreshing.
        if (cached?.payload && botChartPreviewState.open && botChartPreviewState.ticker === ticker) {
            applyBotChartPreviewPayload(cached.payload);
            renderedCached = true;
        }
    }

    if (!renderedCached && showLoading) {
        setBotChartPreviewLoading(force ? 'Refreshing bot chart preview...' : 'Loading bot chart preview...');
    }
    try {
        // The draft settings are part of the server cache key, so a changed
        // setting/timeframe still gets a fresh calculation. Let the backend's
        // short timeframe TTL coalesce repeated preview refreshes instead of
        // forcing another provider fetch every time the control is clicked.
        const payload = await requestBotChartPreviewPayload(ticker, settings, false);
        const currentKey = getBotChartPreviewCacheKey(botChartPreviewState.ticker, botChartPreviewState.settings || {});
        if (!botChartPreviewState.open || botChartPreviewState.ticker !== ticker || currentKey !== cacheKey) return;
        applyBotChartPreviewPayload(payload);
        saveBotChartPreviewCache(cacheKey, payload, ticker, settings).catch(err => {
            console.warn('[bot-chart] Preview cache save failed:', err);
        });
        scheduleBotChartPreviewLiveRefresh();
    } catch (err) {
        console.error('[bot-chart] Preview refresh failed:', err);
        scheduleBotChartPreviewLiveRefresh();
        const hasExistingPreview = Array.isArray(botChartPreviewState.priceHistory)
            && botChartPreviewState.priceHistory.length > 0;
        const loading = document.getElementById('botChartPreviewGridLoading');
        const boxLoading = document.getElementById('botChartPreviewBoxLoading');
        const signalsEl = document.getElementById('botChartPreviewSignalGrid');
        const premiumSignalsEl = document.getElementById('botChartPreviewPremiumSignalGrid');
        const reasonEl = document.getElementById('botChartPreviewReason');
        if (loading) loading.classList.add('hidden');
        if (boxLoading) boxLoading.classList.add('hidden');
        if (reasonEl) reasonEl.textContent = hasExistingPreview
            ? `Showing the last preview while ${ticker} refreshes.`
            : (err.message || 'Could not load chart preview.');
        if (!hasExistingPreview && premiumSignalsEl) premiumSignalsEl.innerHTML = '';
        if (!hasExistingPreview && signalsEl) {
            signalsEl.innerHTML = `
                <div class="col-span-full rounded-2xl border border-rose-200 bg-rose-50 px-4 py-6 text-center text-sm text-rose-600">
                    ${err.message || 'Could not load chart preview.'}
                </div>
            `;
        }
    }
}

function queueBotChartPreviewRefresh(force = true) {
    clearTimeout(botChartPreviewRefreshTimer);
    clearTimeout(botChartPreviewLiveRefreshTimer);
    if (!botChartPreviewState.open) return;
    botChartPreviewRefreshTimer = setTimeout(() => {
        if (botChartPreviewState.open) {
            refreshOpenBotChartPreview(force);
        }
    }, 180);
}

function openBotChartPreview(statePatch) {
    clearTimeout(botChartPreviewRefreshTimer);
    clearTimeout(botChartPreviewLiveRefreshTimer);
    botChartPreviewState = {
        ...botChartPreviewState,
        ...statePatch,
        open: true,
    };
    botChartPreviewRange = '1M';
    const configuredSession = String(botChartPreviewState.settings?.market_session || '').trim().toLowerCase();
    botChartPreviewSession = isCrypto(tickerFromPreviewState())
        ? 'crypto24'
        : (['extended', '24h', 'all'].includes(configuredSession) ? 'extended' : 'regular');
    if (botChartPreviewSession === 'crypto24') {
        botChartPreviewState.settings = {
            ...(botChartPreviewState.settings || {}),
            market_session: '24h',
        };
    }
    document.getElementById('botChartPreviewModal')?.classList.remove('hidden');
    setBotChartPreviewLoading('Loading bot chart preview...');
    updateBotChartPreviewRangeSessionState(botChartPreviewState.settings || {});
    refreshOpenBotChartPreview(false, { forceNetwork: true });
}

window.openBotCardChartPreview = async function (ticker) {
    const cleanTicker = (ticker || '').toUpperCase().trim();
    if (!cleanTicker) return;
    const settings = getBotChartPreviewSettingsForCard(cleanTicker);
    openBotChartPreview({
        mode: 'view',
        source: 'card',
        ticker: cleanTicker,
        tradingMode: settings.options_trading ? 'options' : 'regular',
        settings,
        signals: {},
        priceHistory: [],
        volumeProfile: {},
        previewMeta: {},
    });
};

window.openStrategyChartPreview = function (mode = window.activeTradingMode || 'regular') {
    if (!currentStrategySymbol) return;
    openBotChartPreview({
        mode: 'edit',
        source: 'settings',
        ticker: currentStrategySymbol,
        tradingMode: mode,
        settings: collectStrategyPreviewSettings(mode),
        signals: {},
        priceHistory: [],
        volumeProfile: {},
        previewMeta: {},
    });
};

window.closeBotChartPreview = function () {
    clearTimeout(botChartPreviewRefreshTimer);
    clearTimeout(botChartPreviewLiveRefreshTimer);
    document.getElementById('botChartPreviewModal')?.classList.add('hidden');
    botChartPreviewState.open = false;
};

window.openBotChartPreviewSettings = function () {
    const ticker = botChartPreviewState.ticker;
    const mode = botChartPreviewState.tradingMode || 'regular';
    closeBotChartPreview();
    openStrategyModal(ticker, 'edit');
    window.toggleModeActive(mode);
};

window.resetBotChartPreviewIndicatorsToDefaults = function () {
    if (!botChartPreviewState.open) return;
    const settings = clonePlainObject(botChartPreviewState.settings || {});
    settings.indicator_overrides = {};
    settings.indicator_toggle_overrides = {};
    settings.indicator_parameters = typeof INDICATOR_DEFAULTS !== 'undefined' ? { ...INDICATOR_DEFAULTS } : {};

    settings.indicators = [];
    settings.enabled_indicators = [];
    settings.min_buy_signals = 0;
    settings.min_sell_signals = 0;
    botChartPreviewState.settings = settings;

    if (typeof setStrategyDraftIndicatorOverrides === 'function') {
        setStrategyDraftIndicatorOverrides({});
    }

    renderBotChartPreviewSignals(botChartPreviewState.signals, settings);
    queueBotChartPreviewRefresh(true);
};

window.openBotChartPreviewIndicatorSettings = function (indicatorName) {
    if (!botChartPreviewState.ticker) return;
    botChartPreviewIndicatorSettingsContext = botChartPreviewState.source === 'card';
    currentStrategySymbol = botChartPreviewState.ticker;
    window.currentStrategySymbol = currentStrategySymbol;
    setStrategyDraftIndicatorOverrides(botChartPreviewState.settings?.indicator_overrides || {});
    openIndicatorSettings(indicatorName);
};

window.applyBotChartPreviewToSettings = function () {
    if (botChartPreviewState.mode !== 'edit') return;
    applyPreviewSettingsToStrategyForm(botChartPreviewState.settings, botChartPreviewState.tradingMode || 'regular');
    closeBotChartPreview();
};

window.toggleBotChartPreviewIndicator = function (indicatorName) {
    const settings = clonePlainObject(botChartPreviewState.settings);
    const indicators = Array.isArray(settings.indicators) ? [...settings.indicators] : [];
    const idx = indicators.indexOf(indicatorName);
    if (idx >= 0) indicators.splice(idx, 1);
    else indicators.push(indicatorName);
    settings.indicators = indicators;
    settings.enabled_indicators = [...indicators];
    settings.min_buy_signals = normalizeSignalThreshold(settings.min_buy_signals, indicators.length, 'buy');
    settings.min_sell_signals = normalizeSignalThreshold(settings.min_sell_signals, indicators.length, 'sell');
    botChartPreviewState.settings = settings;
    renderBotChartPreviewSignals(botChartPreviewState.signals, settings);
    queueBotChartPreviewRefresh(true);
};

window.setBotChartPreviewTimeframe = function (timeframe) {
    if (!timeframe || !botChartPreviewState.open) return;
    const settings = clonePlainObject(botChartPreviewState.settings || {});
    settings.timeframe = timeframe;
    botChartPreviewState.settings = settings;
    updateBotChartPreviewHeader(botChartPreviewState.priceHistory, settings);
    queueBotChartPreviewRefresh(true);
};

window.setBotChartPreviewRange = function (range) {
    if (!BOT_CHART_PREVIEW_RANGES[range] || !botChartPreviewState.open) return;
    botChartPreviewRange = range;
    updateBotChartPreviewChart(botChartPreviewState.priceHistory || [], botChartPreviewState.settings || {});
};

window.setBotChartPreviewSession = function (session) {
    if (!botChartPreviewState.open) return;
    botChartPreviewSession = (session === 'crypto24' || isCrypto(tickerFromPreviewState()))
        ? 'crypto24'
        : (session === 'extended' ? 'extended' : 'regular');
    botChartPreviewState.settings = {
        ...(botChartPreviewState.settings || {}),
        market_session: botChartPreviewSession === 'crypto24'
            ? '24h'
            : botChartPreviewSession === 'extended'
                ? 'Extended'
                : 'Regular',
    };
    updateBotChartPreviewRangeSessionState(botChartPreviewState.settings);
    queueBotChartPreviewRefresh(true);
};

window.adjustBotChartPreviewThreshold = function (kind, delta) {
    if (botChartPreviewState.mode !== 'edit') return;
    const settings = clonePlainObject(botChartPreviewState.settings);
    const count = Array.isArray(settings.indicators) ? settings.indicators.length : 0;
    if (count <= 0) {
        settings.min_buy_signals = 0;
        settings.min_sell_signals = 0;
    } else {
        const key = kind === 'sell' ? 'min_sell_signals' : 'min_buy_signals';
        const nextValue = normalizeSignalThreshold(settings[key], count, kind) + delta;
        settings[key] = Math.max(1, Math.min(nextValue, count));
    }
    botChartPreviewState.settings = settings;
    renderBotChartPreviewSignals(botChartPreviewState.signals, settings);
    queueBotChartPreviewRefresh(true);
};

window.changeBotChartPreviewThreshold = function (kind, value) {
    if (botChartPreviewState.mode !== 'edit') return;
    const settings = clonePlainObject(botChartPreviewState.settings);
    const count = Array.isArray(settings.indicators) ? settings.indicators.length : 0;
    const key = kind === 'sell' ? 'min_sell_signals' : 'min_buy_signals';
    settings[key] = normalizeSignalThreshold(value, count, kind);
    botChartPreviewState.settings = settings;
    renderBotChartPreviewSignals(botChartPreviewState.signals, settings);
    queueBotChartPreviewRefresh(true);
};


// ──────────────────────────────────────────────
// Bot Creation & Search
// ──────────────────────────────────────────────
let searchTimeout = null;
let currentSelectedBot = null;

document.getElementById('botSearchInput')?.addEventListener('input', (e) => {
    const query = e.target.value.trim();
    const resultsContainer = document.getElementById('botSearchResults');

    if (query.length < 2) {
        resultsContainer.classList.add('hidden');
        return;
    }

    clearTimeout(searchTimeout);
    searchTimeout = setTimeout(async () => {
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
                        <div>
                            <span class="font-black text-indigo-900">${item.symbol}</span>
                            <span class="text-xs text-indigo-400 ml-2 font-medium">${item.name}</span>
                        </div>
                        <span class="text-[0.6rem] bg-indigo-100 text-indigo-500 px-2 py-0.5 rounded uppercase font-bold tracking-widest">+ Add</span>
                    `;
                    div.onclick = () => selectSearchResult(item);
                    resultsContainer.appendChild(div);
                });
                resultsContainer.classList.remove('hidden');
            } else {
                resultsContainer.innerHTML = '<div class="p-4 text-center text-xs text-indigo-400 font-medium">No assets found</div>';
                resultsContainer.classList.remove('hidden');
            }
        } catch (e) {
            console.error('[search] Error fetching results:', e);
        }
    }, 300); // 300ms debounce
});

// Close search dropdown on click outside
document.addEventListener('click', (e) => {
    if (!e.target.closest('.relative.w-full.z-50')) {
        document.getElementById('botSearchResults')?.classList.add('hidden');
    }
});

function selectSearchResult(item) {
    document.getElementById('botSearchResults').classList.add('hidden');
    document.getElementById('botSearchInput').value = '';

    currentSelectedBot = item.symbol;
    openStrategyModal(item.symbol, 'deploy', item.name);
}


window.confirmAndDeployBot = async function () {
    if (!currentStrategySymbol) return;

    const activeBots = window.lastBotsData?.tradelist || [];
    if (activeBots.includes(currentStrategySymbol)) {
        const existingSettings = (window.lastBotsData?.ticker_settings || {})[currentStrategySymbol] || {};
        const modeLabel = existingSettings.options_trading ? 'Options' : (isCrypto(currentStrategySymbol) ? 'Crypto' : 'Stock');
        alert(
            `⚠️ Active Bot Already Deployed for ${currentStrategySymbol}\n\n` +
            `An active ${modeLabel} bot is already running for ${currentStrategySymbol}.\n\n` +
            `To deploy a new bot, please delete the existing ${currentStrategySymbol} bot from your dashboard first.\n` +
            `*(Note: Deleting an active bot will automatically close any open position and cancel pending orders for that ticker).*`
        );
        return;
    }

    const mode = window.activeTradingMode || 'regular';
    if (!requireValidOptionsDirectionForSave(mode)) return;
    const validation = window.validateStrategyPreflight(currentStrategySymbol, mode);
    if (!validation.valid) {
        alert(`${validation.title}\n\n${validation.message}`);
        return;
    }

    // Gather Settings
    const capital = (mode === 'options' ? document.getElementById('strategyOptionsTradeAmount')?.value : mode === 'ai_autopilot' ? document.getElementById('strategyCapital_ai_autopilot')?.value : document.getElementById('strategyCapital')?.value) || 100;
    const threshold = document.getElementById(`strategyBuyThreshold_${mode}`)?.value || 4;
    const sellThreshold = document.getElementById(`strategySellThreshold_${mode}`)?.value || 4;
    const sellMode = document.getElementById(`strategySellMode_${mode}`)?.value || 'hybrid';
    const timeframe = document.getElementById(`strategyTimeframe_${mode}`)?.value || '1Hour';

    // Gather Indicators
    const indicators = getSelectedStrategyIndicators(mode);
    const normalizedBuyThreshold = normalizeSignalThreshold(threshold, indicators.length, 'buy');
    const normalizedSellThreshold = normalizeSignalThreshold(sellThreshold, indicators.length, 'sell');
    const draftOverrides = getStrategyDraftIndicatorOverrides();

    const addWatchlistEl = document.getElementById('strategyAddWatchlist');
    const addToWatchlist = addWatchlistEl ? addWatchlistEl.checked : false;

    const symbol = currentStrategySymbol;
    const tradelistContainer = document.getElementById('tradelistContainer');

    if ((mode === 'regular' || mode === 'ai_autopilot') && window.updateMarketSessionNotice) {
        const sessionCheck = await window.updateMarketSessionNotice();
        if (sessionCheck && sessionCheck.allowed === false) {
            alert(sessionCheck.message || 'This ticker is not eligible for the selected market session.');
            return;
        }
    }

    closeStrategyModal();

    if (tradelistContainer.querySelector('p')) {
        tradelistContainer.innerHTML = '';
    }

    const tempDiv = document.createElement('div');
    tempDiv.id = `temp-bot-${symbol}`;
    tempDiv.className = "watchlist-item group active-bot p-3 mb-2 flex flex-col gap-2 bg-indigo-50 border-indigo-200 animate-pulse";
    tempDiv.innerHTML = `
        <div class="flex items-center justify-between">
            <div class="flex items-center gap-2">
                <div class="h-4 w-4 border-2 border-indigo-500 border-t-transparent rounded-full animate-spin"></div>
                <span class="font-black text-sm text-indigo-950 tracking-tight">${symbol}</span>
                <span class="text-[0.5rem] font-bold px-1.5 py-0.5 rounded bg-amber-50 text-amber-500 border border-amber-200">STARTING...</span>
            </div>
        </div>
    `;
    tradelistContainer.insertBefore(tempDiv, tradelistContainer.firstChild);

    try {
        const headers = await getAuthHeaders();
        const payload = {
            symbol,
            add_to_watchlist: addToWatchlist,
            ...getStrategyModeSettings(mode),
            capital: parseFloat(capital) || 100,
            threshold: mode === 'ai_autopilot' ? 4 : normalizedBuyThreshold,
            sell_threshold: mode === 'ai_autopilot' ? 4 : normalizedSellThreshold,
            max_trades: Math.min(100, Math.max(0, parseInt(document.getElementById('strategyMaxTrades')?.value || 0, 10) || 0)),
            sell_mode: sellMode,
            indicators: mode === 'ai_autopilot' ? [] : indicators,
            indicator_overrides: mode === 'ai_autopilot' ? {} : draftOverrides,
            timeframe: timeframe,
            paused: window._strategyPaused || false,
            ai_autopilot: mode === 'ai_autopilot',
            ai_model: normalizeBotAIModel(document.getElementById('strategyAiModel')?.value || 'llama-3.3-70b-versatile'),
            risk_per_trade: (document.getElementById(mode === 'regular' ? 'strategyEnableRisk_regular' : 'strategyEnableRisk_options')?.checked) ? parseFloat(document.getElementById(mode === 'regular' ? 'strategyRiskPerTrade' : 'strategyOptionsRiskPerTrade')?.value || 2.0) / 100.0 : null,
            max_daily_drawdown: (document.getElementById(mode === 'regular' ? 'strategyEnableRisk_regular' : 'strategyEnableRisk_options')?.checked) ? parseFloat(document.getElementById(mode === 'regular' ? 'strategyMaxDailyDrawdown' : 'strategyOptionsMaxDailyDrawdown')?.value || (mode === 'regular' ? 50.0 : 60.0)) / 100.0 : null,
            max_position_pct: (document.getElementById(mode === 'regular' ? 'strategyEnableRisk_regular' : 'strategyEnableRisk_options')?.checked) ? parseFloat(document.getElementById(mode === 'regular' ? 'strategyMaxPositionPct' : 'strategyOptionsMaxPositionPct')?.value || 100.0) / 100.0 : null,
            atr_stop_multiplier: getStrategyExitNumber(mode, 'strategyAtrStopMult', 'strategyAtrStopMult_options', 2.0),
            atr_trail_multiplier: getStrategyExitNumber(mode, 'strategyAtrTrailMult', 'strategyAtrTrailMult_options', 3.0),
            take_profit_multiplier: getStrategyExitNumber(mode, 'strategyAtrTpMult', 'strategyAtrTpMult_options', 4.0),
            enable_cooldown: document.getElementById(`strategyEnableCooldown_${mode}`)?.checked || false,
            cooldown_minutes: getCooldownMinutesValue(mode),
            ...getCoreTradeGuardSettings(mode),
            options_trading: mode === 'options',
            options_type: document.getElementById('strategyOptionsType')?.value || 'calls_only',
            options_trade_amount: parseFloat(document.getElementById('strategyOptionsTradeAmount')?.value || 100),
            options_expiry_days: parseInt(document.getElementById('strategyOptionsExpiryDays')?.value || 7),
            options_strike_type: document.getElementById('strategyOptionsStrikeType')?.value || 'ATM',
            options_exit_strategy: document.getElementById('strategySellMode_options')?.value || 'hybrid',
            options_stop_loss_pct: parseFloat(document.getElementById('strategyOptionsSlPct')?.value || OPTIONS_DEFAULT_SL_PCT) / 100.0,
            options_take_profit_pct: parseFloat(document.getElementById('strategyOptionsTpPct')?.value || OPTIONS_DEFAULT_TP_PCT) / 100.0,
            options_trailing_pct: parseFloat(document.getElementById('strategyOptionsTrailPct')?.value || OPTIONS_DEFAULT_TRAIL_PCT) / 100.0,
            enable_signal_reset: document.getElementById(mode === 'regular' ? 'strategyRegularSignalReset' : 'strategyOptionsSignalReset')?.checked || false,
            enable_smart_reentry: document.getElementById(mode === 'regular' ? 'strategyRegularSmartReentry' : 'strategyOptionsSmartReentry')?.checked || false,
            smart_reentry_type: document.getElementById(mode === 'regular' ? 'strategyRegularSmartReentryType' : 'strategyOptionsSmartReentryType')?.value || 'RSI',
            smart_reentry_rsi_call_max: parseInt(document.getElementById(mode === 'regular' ? 'strategyRegularRsiCallMax' : 'strategyOptionsRsiCallMax')?.value || 70),
            smart_reentry_rsi_put_min: parseInt(document.getElementById(mode === 'regular' ? 'strategyRegularRsiPutMin' : 'strategyOptionsRsiPutMin')?.value || 30),
            smart_reentry_vwap_call_max_dist_pct: parseFloat(document.getElementById(mode === 'regular' ? 'strategyRegularVwapCallMaxPct' : 'strategyOptionsVwapCallMaxPct')?.value || 0.5),
            smart_reentry_vwap_put_max_dist_pct: parseFloat(document.getElementById(mode === 'regular' ? 'strategyRegularVwapPutMaxPct' : 'strategyOptionsVwapPutMaxPct')?.value || 0.5)
        };

        const response = await fetch(`${API_BASE}/api/bots/create`, {
            method: 'POST',
            headers: headers,
            body: JSON.stringify(payload)
        });

        const data = await response.json().catch(() => ({}));
        if (response.ok && data.status !== 'error') {
            await fetchBotsData({ force: true });
        } else {
            tempDiv.remove();
            alert(data.message || data.detail || "Failed to launch bot. Please try again.");
        }
    } catch (e) {
        console.error('[launch] Error:', e);
        tempDiv.remove();
        alert(`Failed to launch bot: ${e.message}`);
    }
};

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
            window.AlpacaStatusCache?.clear();
            window.AlpacaStatusCache?.render({ status: 'disconnected', has_keys: false, simulation: true, updatedAt: Date.now() });
            // Refresh based on which page we are on
            if (typeof fetchDashboard === 'function') fetchDashboard();
            else if (typeof fetchBotsData === 'function') fetchBotsData();
        } else {
            alert("Error unlinking account.");
        }
    } catch (err) {
        console.error(err);
        alert("Backend communication error.");
    }
}

// ──────────────────────────────────────────────
// Init
// ──────────────────────────────────────────────
(function init() {
    // Wait for Firebase auth to be ready and currentUser to be populated before first fetch
    const waitForAuth = setInterval(() => {
        if (window.auth && (window.auth.currentUser || localStorage.getItem('dev_mode') === 'true')) {
            clearInterval(waitForAuth);

            // Prioritize loading the dashboard layout and chart first!
            const dashContent = document.getElementById('dashboardContent');
            if (dashContent) dashContent.classList.remove('hidden');

            const container = document.getElementById('tradelistContainer');
            if (container) {
                container.addEventListener('dragover', (e) => {
                    if (e.preventDefault) e.preventDefault();
                    e.dataTransfer.dropEffect = 'move';
                    if (dragPlaceholder && (e.target === container || e.target.id === 'tradelistContainer')) {
                        container.appendChild(dragPlaceholder);
                    }
                });
                container.addEventListener('drop', handleDrop);
            }

            // INSTANT RENDER: Check for prefetched data from dashboard page
            let usedCache = false;
            try {
                const cachedRaw = sessionStorage.getItem('botsDataCache');
                if (cachedRaw) {
                    const { schemaVersion, data, fetchedAt } = JSON.parse(cachedRaw);
                    const ageMs = Date.now() - fetchedAt;
                    if (schemaVersion === '1.0' && data?.schema_version === '1.0' && ageMs < 60000) {
                        console.log(`[bots] Using prefetched cache (${Math.round(ageMs / 1000)}s old) for instant render.`);
                        usedCache = Boolean(ingestBotsSnapshot(data));
                    }
                }
            } catch (e) {
                console.warn('[bots] Could not read bots cache:', e);
            }

            // Always fetch fresh data — immediately if no cache, delayed if cache was used.
            fetchPortfolioHistory({ force: true });
            if (!usedCache) {
                fetchBotsData({ force: true }); // Full load if no cache
            } else {
                // Soft refresh after a short delay when cache was used
                botsSoftRefreshTimerId = setTimeout(() => fetchBotsData({ force: true }), 2000);
            }
            fetchBotsActivity('scans', { force: true });
            loadOllamaModels();

            botsRegularRefreshTimerId = setInterval(() => {
                fetchBotsData({ partition: 'regular' });
            }, REGULAR_REFRESH_INTERVAL);
            botsOptionsRefreshTimerId = setInterval(() => {
                if (hasOptionsSnapshotInterest() && !botsFetchState.regular.promise && !botsFetchState.all.promise) {
                    fetchBotsData({ partition: 'options' });
                }
            }, OPTIONS_REFRESH_INTERVAL);
            botsActivityRefreshTimerId = setInterval(() => {
                if (currentLogTab === 'all') fetchBotsActivity('scans');
                if (currentLogTab === 'trades') fetchBotsActivity('orders');
            }, SCAN_ACTIVITY_REFRESH_INTERVAL);
            botsPortfolioRefreshTimerId = setInterval(() => {
                fetchPortfolioHistory();
            }, PORTFOLIO_SHORT_REFRESH_INTERVAL);

            // 1-second live countdown ticker for Bot Card Scan Logs (zero-drift architecture)
            botsCountdownTimerId = setInterval(() => {
                document.querySelectorAll('.scan-countdown').forEach(el => {
                    const renderedAt = Number(el.getAttribute('data-rendered-at'));
                    const initialRemaining = Number(el.getAttribute('data-initial-remaining'));
                    if (!isNaN(renderedAt) && !isNaN(initialRemaining)) {
                        const elapsedSec = Math.floor((Date.now() - renderedAt) / 1000);
                        const currentRemaining = Math.max(0, initialRemaining - elapsedSec);
                        if (currentRemaining > 0) {
                            el.innerHTML = `⏱️ Refreshing in ${currentRemaining}s`;
                        } else {
                            el.innerHTML = `⏱️ Refreshing now...`;
                        }
                    }
                });
                document.querySelectorAll('.options-market-countdown').forEach(el => {
                    const renderedAt = Number(el.getAttribute('data-rendered-at'));
                    const initialPreOpen = Number(el.getAttribute('data-initial-pre-open'));
                    const initialOpen = Number(el.getAttribute('data-initial-open'));
                    if (!isNaN(renderedAt) && !isNaN(initialPreOpen)) {
                        const elapsedSec = Math.floor((Date.now() - renderedAt) / 1000);
                        const remPreOpen = Math.max(0, initialPreOpen - elapsedSec);
                        const remOpen = Math.max(0, initialOpen - elapsedSec);
                        if (remPreOpen > 0) {
                            el.innerHTML = `⏱️ Starts scanning in ${formatDurationCompactJs(remPreOpen)} (2m prior to Market Open)`;
                        } else if (remOpen > 0) {
                            el.innerHTML = `🔥 Pre-Market Active! Scanning started (${formatDurationCompactJs(remOpen)} until Open)`;
                        } else {
                            el.innerHTML = `🔔 Market Open! Refreshing signals...`;
                        }
                    }
                });
            }, 1000);

            document.addEventListener('visibilitychange', () => {
                if (!document.hidden) {
                    fetchBotsData({ force: true });
                    fetchPortfolioHistory({ force: true });
                    if (currentLogTab === 'all') fetchBotsActivity('scans', { force: true });
                    if (currentLogTab === 'trades') fetchBotsActivity('orders', { force: true });
                    if (botChartPreviewState.open) {
                        refreshOpenBotChartPreview(false, { showLoading: false, allowCachedRender: false, forceNetwork: true });
                    }
                }
            });
        }
    }, 200);
})();

// ──────────────────────────────────────────────
// Indicator Settings Logic
// ──────────────────────────────────────────────

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
    "VWAP": "Session VWAP is the cumulative typical-price and volume average for the exchange-local trading day, matching the standard Webull-style VWAP calculation.",
    "Auto-AVWAP": "Auto-AVWAP anchors the volume-weighted average at the latest volume spike. It is intentionally separate from Session VWAP and may not match Webull VWAP.",
    "Volume Profile": "Prior-session volume-at-price profile using POC, VAH, and VAL. Purpose: Finds institutional acceptance/rejection zones where the most volume traded.",
    "Swing Points": "Rolling swing support/resistance clusters from recent local highs/lows. Purpose: Finds structural bounce, rejection, reclaim, and breakdown zones.",
    "Strategy Confidence": "Strategy Confidence combines multiple indicator signals into a single probability score. Purpose: Measures the overall conviction of a bullish or bearish trend.",
    "BotBulls1": "Confirmed WaveTrend reversal with MFI reclaim/rejection, then price confirmation within the BB1 window. Confluence stays bullish after the last BB1 BUY marker and bearish after the last BB1 SELL marker.",
    "BotBulls2": "Adaptive ATR trailing stop with trend-smoothed EMA and reversal zone detection. Fires on trend flips confirmed by multi-condition confluence scoring (1-4 strength rating).",
    "BotBulls3": "Heikin-Ashi noise filter + ATR-based trailing stop. Generates buy/sell alerts only on confirmed momentum flips — removing false signals from choppy markets.",
    "BotBulls5": "Premium causal chart-pattern engine for continuation, reversal, compression, channel, and rounded structures. It draws qualified geometry and stays neutral until a completed candle confirms the breakout.",
    "Prior Day High/Low": "Uses the previous completed trading session's high and low as breakout/breakdown levels. Buffer is entered as %, with dotted trigger lines when non-zero.",
    "Pivot Points (S/R)": "Classic prior-session floor pivot levels. Draws up to 5 support/resistance ladders; bullish on support/pivot reclaim, bearish on resistance rejection or pivot breakdown.",
    "Botbulls4 News AI": "Analyzes current news headlines and assigns a bullish/bearish score based on natural language processing of market sentiment."
};

const INDICATOR_CONFIG_MAP = {
    'RSI': ['RSI_PERIOD', 'RSI_OVERBOUGHT', 'RSI_OVERSOLD'],
    'MACD': ['MACD_FAST', 'MACD_SLOW', 'MACD_SIGNAL'],
    'EMA Cross': ['EMA_USE_SECOND_LINE', 'EMA_FAST', 'EMA_SLOW'],
    'Supertrend': ['SUPERTREND_PERIOD', 'SUPERTREND_MULTIPLIER'],
    'Bollinger': ['BOLL_PERIOD', 'BOLL_STD_DEV'],
    'Mystic Pulse': ['MYSTIC_PULSE_THRESHOLD'],
    'ADX Trend': ['ADX_PERIOD', 'ADX_TRENDING_THRESHOLD'],
    'SMA': ['SMA_USE_SECOND_LINE', 'SMA_PERIOD', 'SMA_SECOND_PERIOD'],
    'Volume Profile': ['VOLUME_PROFILE_ROWS', 'VOLUME_PROFILE_VALUE_AREA_PCT', 'VOLUME_PROFILE_TOLERANCE_PCT', 'VOLUME_PROFILE_CONFIRMATION_CANDLES'],
    'Swing Points': ['SWING_POINTS_LOOKBACK_DAYS', 'SWING_POINTS_ENTRY_MODE', 'SWING_POINTS_LEVEL_COUNT'],
    'Prior Day High/Low': ['DAILY_HIGH_LOW_BUFFER_PCT'],
    'Pivot Points (S/R)': ['PIVOT_LEVEL_COUNT', 'PIVOT_TOLERANCE_PCT'],
    'ATR Volatility': ['ATR_PERIOD', 'ATR_STOP_MULTIPLIER', 'ATR_TRAIL_MULTIPLIER', 'ATR_TAKE_PROFIT_MULTIPLIER'],
    'Strategy Confidence': ['MIN_BULLISH_SIGNALS', 'MIN_BEARISH_SIGNALS'],
    'Botbulls4 News AI': ['SENTIMENT_BULLISH_PERCENTAGE', 'SENTIMENT_BEARISH_PERCENTAGE'],
    'BotBulls1': ['BOTBULLS1_WT_CHANNEL', 'BOTBULLS1_WT_AVERAGE', 'BOTBULLS1_WT_OVERSOLD', 'BOTBULLS1_WT_OVERBOUGHT', 'BOTBULLS1_MFI_BUY_RECLAIM', 'BOTBULLS1_MFI_SELL_REJECT', 'BOTBULLS1_CONFIRM_LOOKBACK', 'BOTBULLS1_REQUIRE_MFI_CONFIRM', 'BOTBULLS1_REQUIRE_CANDLE_CONFIRM', 'BOTBULLS1_REQUIRE_PRICE_CONFIRM', 'BOTBULLS1_PRICE_CONFIRM_EMA', 'BOTBULLS1_PRICE_CONFIRM_LOOKBACK'],
    'BotBulls2': ['BOTBULLS2_ATR_MULT', 'BOTBULLS2_TREND_TRACER_PERIOD', 'BOTBULLS2_REVERSAL_ZONE_PERIOD'],
    'BotBulls3': ['BOTBULLS3_ATR_PERIOD', 'BOTBULLS3_ATR_MULT'],
    'BotBulls5': ['BOTBULLS5_MIN_CONFIDENCE', 'BOTBULLS5_BREAKOUT_BUFFER_ATR', 'BOTBULLS5_STRUCTURE_LOOKBACK', 'BOTBULLS5_SIGNAL_HOLD_BARS', 'BOTBULLS5_REQUIRE_VOLUME']
};
Object.assign(INDICATOR_CONFIG_MAP, window.STANDARD_INDICATOR_CONFIG_MAP || {});

const INDICATOR_DEFAULTS = {
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
    'VOLUME_PROFILE_ROWS': 48,
    'VOLUME_PROFILE_VALUE_AREA_PCT': 70.0,
    'VOLUME_PROFILE_TOLERANCE_PCT': 0.15,
    'VOLUME_PROFILE_CONFIRMATION_CANDLES': 1,
    'SWING_POINTS_LOOKBACK_DAYS': 20,
    'SWING_POINTS_ENTRY_MODE': 'bounce_retest',
    'SWING_POINTS_LEVEL_COUNT': 1,
    'DAILY_HIGH_LOW_BUFFER_PCT': 0.0,
    'PIVOT_LEVEL_COUNT': 1,
    'PIVOT_TOLERANCE_PCT': 0.0,
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
    if (indicatorName === 'BotBulls5' && typeof window.renderBotBulls5PatternSummary === 'function') {
        window.renderBotBulls5PatternSummary(
            container,
            botChartPreviewState?.priceHistory || [],
            { rolling24Hours: isCrypto(tickerFromPreviewState()) },
        );
    }

    let currentParams = {};
    let overrides = {};

    if (currentStrategySymbol) {
        const tickerOverrides = getStrategyDraftIndicatorOverrides();
        overrides = tickerOverrides;
        // Do NOT merge with global indicator_parameters. Fall back to pure system defaults.
        currentParams = { ...tickerOverrides };
    } else {
        currentParams = window.lastBotsData?.indicator_parameters || {};
        overrides = window.lastBotsData?.indicator_overrides || {};
    }
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
        const val = currentParams[key] !== undefined ? currentParams[key] : (INDICATOR_DEFAULTS[key] !== undefined ? INDICATOR_DEFAULTS[key] : '');
        const isLineToggle = key === 'EMA_USE_SECOND_LINE' || key === 'SMA_USE_SECOND_LINE';
        const toggleChecked = val === true || val === 1 || String(val).toLowerCase() === 'true' || String(val) === '1';
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
    if (botChartPreviewIndicatorSettingsContext) {
        currentStrategySymbol = null;
        window.currentStrategySymbol = null;
        botChartPreviewIndicatorSettingsContext = false;
    }
}

function resetIndicatorSettingsToDefaults() {
    if (!currentEditingIndicator) return;
    const inputs = document.querySelectorAll('#indicatorModalContent input, #indicatorModalContent select');
    inputs.forEach(input => {
        if (input.type === 'checkbox') {
            input.checked = String(input.dataset.default) === '1';
            input.dispatchEvent(new Event('change'));
        } else {
            input.value = input.tagName === 'SELECT' ? (input.dataset.default || '') : input.placeholder; // Revert to system default
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

    if (currentStrategySymbol) {
        const nextOverrides = getStrategyDraftIndicatorOverrides();
        for (const [k, v] of Object.entries(updates)) {
            if (v === "" || v === null) {
                delete nextOverrides[k];
            } else {
                nextOverrides[k] = v;
            }
        }
        setStrategyDraftIndicatorOverrides(nextOverrides);
        if (botChartPreviewState.open) {
            botChartPreviewState.settings = {
                ...(botChartPreviewState.settings || {}),
                indicator_overrides: clonePlainObject(nextOverrides),
            };
        }
        closeIndicatorSettings();
        if (botChartPreviewState.open) {
            await refreshOpenBotChartPreview(true);
        }
        return;
    }

    try {
        const headers = await getAuthHeaders();
        let url = `${API_BASE}/api/settings/indicators?context=bots`;

        const response = await fetch(url, {
            method: 'POST',
            headers: headers,
            body: JSON.stringify(updates)
        });

        if (response.ok) {
            console.log(`[settings] Updated settings for ${currentEditingIndicator}`);
            if (!window.lastBotsData) {
                window.lastBotsData = { indicator_parameters: {}, indicator_overrides: {}, ticker_settings: {} };
            }

            if (currentStrategySymbol) {
                if (!window.lastBotsData.ticker_settings) window.lastBotsData.ticker_settings = {};
                if (!window.lastBotsData.ticker_settings[currentStrategySymbol]) window.lastBotsData.ticker_settings[currentStrategySymbol] = {};
                if (!window.lastBotsData.ticker_settings[currentStrategySymbol].indicator_overrides) window.lastBotsData.ticker_settings[currentStrategySymbol].indicator_overrides = {};

                for (const [k, v] of Object.entries(updates)) {
                    if (v === "" || v === null) {
                        delete window.lastBotsData.ticker_settings[currentStrategySymbol].indicator_overrides[k];
                    } else {
                        window.lastBotsData.ticker_settings[currentStrategySymbol].indicator_overrides[k] = v;
                    }
                }
            } else {
                if (!window.lastBotsData.indicator_parameters) window.lastBotsData.indicator_parameters = {};
                if (!window.lastBotsData.indicator_overrides) window.lastBotsData.indicator_overrides = {};

                for (const [k, v] of Object.entries(updates)) {
                    if (v === "" || v === null) {
                        delete window.lastBotsData.indicator_overrides[k];
                        delete window.lastBotsData.indicator_parameters[k];
                    } else {
                        window.lastBotsData.indicator_overrides[k] = v;
                        window.lastBotsData.indicator_parameters[k] = v;
                    }
                }
            }

            closeIndicatorSettings();
            const chartPreviewSettingKeys = [
                'EMA_USE_SECOND_LINE',
                'EMA_FAST',
                'EMA_SLOW',
                'SMA_USE_SECOND_LINE',
                'SMA_PERIOD',
                'SMA_SECOND_PERIOD',
                'DAILY_HIGH_LOW_BUFFER_PCT',
                'PIVOT_LEVEL_COUNT',
                'PIVOT_TOLERANCE_PCT',
                'VOLUME_PROFILE_ROWS',
                'VOLUME_PROFILE_VALUE_AREA_PCT',
                'VOLUME_PROFILE_TOLERANCE_PCT',
                'VOLUME_PROFILE_CONFIRMATION_CANDLES',
                'SWING_POINTS_LOOKBACK_DAYS',
                'SWING_POINTS_ENTRY_MODE',
                'SWING_POINTS_LEVEL_COUNT',
            ];
            const shouldRefreshPreview = botChartPreviewState.open && chartPreviewSettingKeys.some(key => updates[key] !== undefined);
            if (shouldRefreshPreview) {
                const nextPreviewOverrides = clonePlainObject(botChartPreviewState.settings?.indicator_overrides || {});
                const nextPreviewParams = clonePlainObject(botChartPreviewState.settings?.indicator_parameters || {});
                chartPreviewSettingKeys.forEach(key => {
                    if (updates[key] === undefined) return;
                    const value = updates[key];
                    if (value === "" || value === null) {
                        delete nextPreviewOverrides[key];
                        delete nextPreviewParams[key];
                    } else {
                        nextPreviewOverrides[key] = value;
                        nextPreviewParams[key] = value;
                    }
                });
                botChartPreviewState.settings = {
                    ...(botChartPreviewState.settings || {}),
                    indicator_overrides: nextPreviewOverrides,
                    indicator_parameters: nextPreviewParams,
                };
                await refreshOpenBotChartPreview(true);
                return;
            }
            await fetchBotsData({ force: true });
        } else {
            alert("Error saving indicator settings.");
        }
    } catch (err) {
        console.error(err);
        alert("Backend communication error.");
    }
}

function toggleIndicatorCard(card, event) {
    if (document.getElementById('strategyAiAutopilot')?.checked) {
        return;
    }
    if (event.target.closest('button') || event.target.closest('input[type="checkbox"]')) {
        return;
    }
    const checkbox = card.querySelector('input[type="checkbox"]');
    if (checkbox) {
        checkbox.checked = !checkbox.checked;
        checkbox.dispatchEvent(new Event('change', { bubbles: true }));
    }
}

// ──────────────────────────────────────────────
// Portfolio Equity History Chart Logic
// ──────────────────────────────────────────────
function getChartTimeframe(period) {
    switch (period.toUpperCase()) {
        case '1D': return '15Min';
        case '1W': return '1H';
        case '1M': return '1D';
        case '1Y': return '1D';
        case 'ALL': return '1D';
        default: return '1D';
    }
}

async function setChartPeriod(period) {
    currentChartPeriod = period;

    // Update active period button styling
    const buttons = ['1D', '1W', '1M', '1Y', 'ALL'];
    buttons.forEach(btn => {
        const el = document.getElementById(`period-${btn}`);
        if (el) {
            if (btn === period) {
                el.className = "px-3.5 py-1.5 text-[0.65rem] font-black uppercase rounded-lg transition-all bg-white text-indigo-600 shadow-sm border border-indigo-100";
            } else {
                el.className = "px-3.5 py-1.5 text-[0.65rem] font-black uppercase rounded-lg transition-all text-indigo-400 hover:text-indigo-600";
            }
        }
    });

    await fetchPortfolioHistory({ force: true });
}

async function fetchPortfolioHistory(options = {}) {
    const { force = false } = options;
    const canvas = document.getElementById('portfolioHistoryChart');
    if (!canvas) return;
    if (!force && !shouldRunBotsVisualRefresh()) return;
    const now = Date.now();
    const tf = getChartTimeframe(currentChartPeriod);
    const rangeKey = `${currentChartPeriod}:${tf}`;
    const refreshInterval = ['1D', '1W'].includes(currentChartPeriod.toUpperCase())
        ? PORTFOLIO_SHORT_REFRESH_INTERVAL
        : PORTFOLIO_LONG_REFRESH_INTERVAL;
    const lastRefreshAt = portfolioRefreshAtByRange.get(rangeKey) || 0;
    if (!force && now - lastRefreshAt < refreshInterval) return;
    if (portfolioHistoryFetchInFlight) {
        if (force && portfolioHistoryAbortController) {
            portfolioHistoryAbortController.abort();
        } else {
            return;
        }
    }

    const fetchId = ++portfolioHistoryFetchId;
    portfolioHistoryFetchInFlight = true;
    portfolioHistoryAbortController = new AbortController();
    try {
        const headers = await getAuthHeaders();
        const url = `${API_BASE}/api/portfolio/history?period=${currentChartPeriod}&timeframe=${tf}`;

        const response = await fetchWithTimeout(
            url,
            { headers, cache: 'no-store', controller: portfolioHistoryAbortController },
            PORTFOLIO_FETCH_TIMEOUT_MS
        );
        if (!response.ok) throw new Error(`HTTP error! status: ${response.status}`);

        const data = await response.json();
        if (!data || !data.timestamp || data.timestamp.length === 0) {
            console.warn("[chart] No portfolio history data returned.");
            return;
        }
        portfolioRefreshAtByRange.set(rangeKey, Date.now());

        // Format timestamps for the labels based on period
        const labels = data.timestamp.map(ts => {
            const date = new Date(ts * 1000);
            if (currentChartPeriod.toUpperCase() === '1D') {
                return date.toLocaleTimeString('en-US', { hour: '2-digit', minute: '2-digit', hour12: false });
            } else if (currentChartPeriod.toUpperCase() === '1W') {
                return date.toLocaleDateString('en-US', { month: 'short', day: 'numeric' }) + ' ' + date.toLocaleTimeString('en-US', { hour: '2-digit', hour12: false }) + ':00';
            } else {
                return date.toLocaleDateString('en-US', { month: 'short', day: 'numeric', year: '2-digit' });
            }
        });

        const equities = data.equity;

        // Calculate dynamic limits to fit the curve beautifully without starting at zero
        const minEquity = Math.min(...equities);
        const maxEquity = Math.max(...equities);
        const padding = (maxEquity - minEquity) * 0.1 || 100; // fallback if no variation
        const yMin = Math.floor(minEquity - padding);
        const yMax = Math.ceil(maxEquity + padding);

        // Update performance metric text based on first and last points of active curve
        if (equities && equities.length > 0 && data.timestamp && data.timestamp.length > 0) {
            const firstEq = equities[0];
            const lastEq = equities[equities.length - 1];

            // Align 1D Performance metric with standard brokerage Daily P/L (inclusive of overnight gaps)
            let finalPl = lastEq - firstEq;
            let finalPlPct = firstEq > 0 ? (finalPl / firstEq * 100) : 0.0;

            if (currentChartPeriod.toUpperCase() === '1D' && data.profit_loss && data.profit_loss.length > 0) {
                finalPl = data.profit_loss[data.profit_loss.length - 1];
                if (data.profit_loss_pct && data.profit_loss_pct.length > 0) {
                    finalPlPct = data.profit_loss_pct[data.profit_loss_pct.length - 1];
                } else {
                    const baseVal = data.base_value || firstEq;
                    finalPlPct = baseVal > 0 ? (finalPl / baseVal * 100) : 0.0;
                }
            }

            const subEl = document.getElementById('equityPerformanceSub');
            if (subEl) {
                const sign = finalPl >= 0 ? '+' : '';
                const colorClass = finalPl >= 0 ? 'text-emerald-500' : 'text-rose-500';

                const firstTs = data.timestamp[0];
                const lastTs = data.timestamp[data.timestamp.length - 1];
                const firstDate = new Date(firstTs * 1000);
                const lastDate = new Date(lastTs * 1000);

                let dateRangeStr = '';
                if (currentChartPeriod.toUpperCase() === '1D') {
                    const optTime = { hour: '2-digit', minute: '2-digit', hour12: false };
                    dateRangeStr = `from ${firstDate.toLocaleTimeString('en-US', optTime)} to ${lastDate.toLocaleTimeString('en-US', optTime)}`;
                } else if (currentChartPeriod.toUpperCase() === '1W') {
                    const optDate = { month: 'short', day: 'numeric' };
                    dateRangeStr = `from ${firstDate.toLocaleDateString('en-US', optDate)} to ${lastDate.toLocaleDateString('en-US', optDate)}`;
                } else {
                    const optDate = { month: 'short', day: 'numeric', year: 'numeric' };
                    dateRangeStr = `from ${firstDate.toLocaleDateString('en-US', optDate)} to ${lastDate.toLocaleDateString('en-US', optDate)}`;
                }

                subEl.innerHTML = `Performance: <span class="${colorClass} font-black">${sign}$${finalPl.toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 })} (${sign}${finalPlPct.toFixed(2)}%)</span> ${dateRangeStr}`;
            }
        }

        // Destroy existing chart if it exists to avoid overlapping
        if (portfolioChartInstance) {
            portfolioChartInstance.destroy();
        }

        const ctx = canvas.getContext('2d');

        // Create gorgeous linear area gradient
        const gradient = ctx.createLinearGradient(0, 0, 0, 240);
        gradient.addColorStop(0, 'rgba(168, 85, 247, 0.4)'); // Fuchsia
        gradient.addColorStop(0.5, 'rgba(99, 102, 241, 0.15)'); // Indigo
        gradient.addColorStop(1, 'rgba(99, 102, 241, 0)');

        // Premium chart configuration
        const currentEquityLinePlugin = {
            id: 'currentEquityLine',
            afterDraw: (chart) => {
                const { ctx, chartArea: { left, right }, scales: { y } } = chart;
                if (equities && equities.length > 0) {
                    const lastEquity = equities[equities.length - 1];
                    const yPos = y.getPixelForValue(lastEquity);

                    ctx.save();
                    ctx.beginPath();
                    ctx.setLineDash([4, 4]); // Dashed line
                    ctx.strokeStyle = 'rgba(168, 85, 247, 0.6)'; // Purple dashed line
                    ctx.lineWidth = 1.5;
                    ctx.moveTo(left, yPos);
                    ctx.lineTo(right, yPos);
                    ctx.stroke();

                    // Draw a little badge showing the current equity value at the right end
                    ctx.font = 'bold 9px Inter, sans-serif';
                    ctx.textBaseline = 'middle';
                    const text = `$${lastEquity.toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;
                    const textWidth = ctx.measureText(text).width;

                    // Background badge
                    ctx.beginPath();
                    if (ctx.roundRect) {
                        ctx.roundRect(right - textWidth - 8, yPos - 7, textWidth + 8, 14, 4);
                    } else {
                        ctx.rect(right - textWidth - 8, yPos - 7, textWidth + 8, 14);
                    }
                    ctx.fillStyle = 'rgba(147, 51, 234, 0.95)';
                    ctx.fill();

                    // Text
                    ctx.fillStyle = '#ffffff';
                    ctx.fillText(text, right - textWidth - 4, yPos);

                    ctx.restore();
                }
            }
        };

        portfolioChartInstance = new Chart(ctx, {
            type: 'line',
            data: {
                labels: labels,
                datasets: [{
                    label: 'Portfolio Equity',
                    data: equities,
                    fill: true,
                    backgroundColor: gradient,
                    borderColor: 'rgb(147, 51, 234)', // Purple-600
                    borderWidth: 2.5,
                    pointBackgroundColor: 'rgb(147, 51, 234)',
                    pointBorderColor: '#ffffff',
                    pointBorderWidth: 1.5,
                    pointRadius: 0, // Clean line, show points only on hover
                    pointHoverRadius: 6,
                    pointHoverBackgroundColor: 'rgb(79, 70, 229)', // Indigo-600
                    pointHoverBorderColor: '#ffffff',
                    pointHoverBorderWidth: 2.5,
                    tension: 0.35, // Smooth cubic spline interpolation
                }]
            },
            options: {
                responsive: true,
                maintainAspectRatio: false,
                plugins: {
                    legend: {
                        display: false // Clean look, no legend
                    },
                    tooltip: {
                        enabled: true,
                        mode: 'index',
                        intersect: false,
                        backgroundColor: 'rgba(15, 23, 42, 0.85)', // Premium slate-900 background
                        titleColor: '#ffffff',
                        titleFont: {
                            family: "'Inter', sans-serif",
                            size: 11,
                            weight: '800'
                        },
                        bodyColor: '#e2e8f0',
                        bodyFont: {
                            family: "'Inter', sans-serif",
                            size: 12,
                            weight: 'bold'
                        },
                        padding: 12,
                        borderRadius: 12,
                        borderColor: 'rgba(255, 255, 255, 0.1)',
                        borderWidth: 1,
                        callbacks: {
                            label: function (context) {
                                let val = context.parsed.y;
                                return ` Equity: $${val.toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;
                            }
                        }
                    }
                },
                scales: {
                    x: {
                        grid: {
                            display: false,
                        },
                        ticks: {
                            color: 'rgb(156, 163, 175)', // Gray-400
                            font: {
                                family: "'Inter', sans-serif",
                                size: 9,
                                weight: 'bold'
                            },
                            maxTicksLimit: currentChartPeriod.toUpperCase() === '1D' ? 6 : 8
                        },
                        border: {
                            display: true,
                            color: 'rgba(99, 102, 241, 0.15)', // Subtle indigo axis line
                            width: 1
                        }
                    },
                    y: {
                        min: yMin,
                        max: yMax,
                        grid: {
                            color: 'rgba(99, 102, 241, 0.05)', // Super clean subtle gridlines
                        },
                        ticks: {
                            color: 'rgb(156, 163, 175)',
                            font: {
                                family: "'Inter', sans-serif",
                                size: 9,
                                weight: 'bold'
                            },
                            callback: function (value) {
                                return '$' + value.toLocaleString('en-US', { maximumFractionDigits: 0 });
                            }
                        },
                        border: {
                            display: true,
                            color: 'rgba(99, 102, 241, 0.15)', // Left vertical axis line
                            width: 1
                        }
                    }
                },
                interaction: {
                    mode: 'nearest',
                    axis: 'x',
                    intersect: false
                }
            },
            plugins: [currentEquityLinePlugin]
        });

    } catch (e) {
        if (e.name === 'AbortError') {
            // A period change or visibility refresh superseded this request.
        } else if (e.name === 'TimeoutError') {
            console.warn(`[chart] Portfolio history timed out after ${PORTFOLIO_FETCH_TIMEOUT_MS}ms.`);
        } else {
            console.error("[chart] Error rendering portfolio history:", e);
        }
    } finally {
        if (fetchId === portfolioHistoryFetchId) {
            portfolioHistoryFetchInFlight = false;
            portfolioHistoryAbortController = null;
        }
    }
}

// ──────────────────────────────────────────────
// premium AI indicator & news sentiment briefing engine (decoupled & cached)
// ──────────────────────────────────────────────
window.ollamaModels = [];
async function loadOllamaModels() {
    try {
        const headers = await getAuthHeaders();
        const res = await fetchWithTimeout(`${API_BASE}/api/ollama/models`, { headers }, 3000);
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

window.onAIIndicatorUpdated = function (data, fromCache) {
    if (typeof botChartPreviewState !== 'undefined' && botChartPreviewState?.open && botChartPreviewState.signals) {
        renderBotChartPreviewSignals(botChartPreviewState.signals, botChartPreviewState.settings || {});
    }
};



window.toggleModeActive = function (mode) {
    const regBox = document.getElementById('regularTradingBox');
    const optBox = document.getElementById('optionsTradingBox');
    const aiBox = document.getElementById('aiAutopilotTradingBox');
    const pill = document.getElementById('modeTogglePill');
    const btnReg = document.getElementById('btnRegularMode');
    const btnOpt = document.getElementById('btnOptionsMode');
    const btnAi = document.getElementById('btnAiAutopilotMode');

    // Prevent options if disabled
    if (mode === 'options' && btnOpt && btnOpt.disabled) {
        return;
    }

    window.activeTradingMode = mode;

    [btnReg, btnOpt, btnAi].forEach(btn => {
        if (!btn) return;
        btn.classList.remove('text-indigo-950');
        btn.classList.add('text-indigo-400');
    });
    if (regBox) regBox.classList.add('hidden');
    if (optBox) optBox.classList.add('hidden');
    if (aiBox) aiBox.classList.add('hidden');

    if (mode === 'regular') {
        if (pill) pill.style.transform = 'translateX(0)';
        if (btnReg) { btnReg.classList.remove('text-indigo-400'); btnReg.classList.add('text-indigo-950'); }
        if (regBox) regBox.classList.remove('hidden');
        window.updateStrategySignalLabels('regular');
    } else if (mode === 'options') {
        if (pill) pill.style.transform = 'translateX(100%)';
        if (btnOpt) { btnOpt.classList.remove('text-indigo-400'); btnOpt.classList.add('text-indigo-950'); }
        if (optBox) optBox.classList.remove('hidden');
        window.updateStrategySignalLabels('options');
    } else if (mode === 'ai_autopilot') {
        if (pill) pill.style.transform = 'translateX(200%)';
        if (btnAi) { btnAi.classList.remove('text-indigo-400'); btnAi.classList.add('text-indigo-950'); }
        if (aiBox) aiBox.classList.remove('hidden');
        if (typeof populateStrategyAiModelSelect === 'function') populateStrategyAiModelSelect();
    }

    if (typeof window.updateSwingSettingsVisibility === 'function' && mode !== 'ai_autopilot') {
        window.updateSwingSettingsVisibility(mode);
    }
    if (typeof window.updateAllocationModeUI === 'function') {
        window.updateAllocationModeUI(mode);
    }

    window.toggleDeployButton();

    if (window.fetchLiveAtrForPreview && window.currentStrategySymbol && window.activeTradingMode) {
        window.fetchLiveAtrForPreview(window.currentStrategySymbol);
    }
};



const STRATEGY_HELPER_TEXTS = {
    entry: {
        indicator: "💡 Indicator (Signals) — Triggers buy order when active Bullish signals from your checked Indicator Pool meet or exceed the Buy Threshold.",
        custom_price: "💡 Custom Price (Limit/Dip Entry) — Executes buy immediately when live market price drops to or crosses below your specified Target Buy Price.",
        atr: "💡 ATR / Volatility Breakout — Buys when price confirms an upward breakout or pullback reversal above Average True Range (ATR) dynamic volatility bands.",
        hybrid_atr: "💡 Hybrid (Indicator + ATR) — Pro quant entry rule: BOTH conditions must be true before opening risk. Your bullish Indicator Pool threshold must confirm AND the ATR volatility setup must confirm. This filters weak breakouts and avoids entering on structure alone.",
        percentage: "💡 Percentage Pullback — Executes buy when price pulls back by your fixed percentage drop from recent swing highs, catching dip reversals.",
        hybrid_pct: "💡 Hybrid (Indicator + Pct) — Pro quant entry rule: BOTH conditions must be true. Bullish indicators must meet threshold AND the fixed percentage move must confirm. This avoids buying every dip unless momentum/confluence also agrees.",
        pivot_points: "💡 Pivot Points (Support Reclaim) — Buys when live price tests and confirms a bounce or reclaim above classic daily/weekly S1/S2/P support pivot levels.",
        hybrid_pivot: "💡 Hybrid (Indicator + Pivots) — Pro quant entry rule: BOTH conditions must be true. Bullish indicators must confirm AND price must validate the pivot/support structure. This prevents entering just because price touches a level.",
        high_low: "💡 Prior Day High/Low Breakout — Momentum entry: buys when current price breaks and closes above the previous trading day's high.",
        hybrid_high_low: "💡 Hybrid (Indicator + High/Low) — Pro quant entry rule: BOTH conditions must be true. Bullish indicators must confirm AND price must break/validate the prior-day structure. This reduces false breakouts.",
        swing_points: "💡 Swing Points (Structure) — Buys on confirmed structural price action: higher-low support bounce or resistance breakout confirmation.",
        hybrid_swing: "💡 Hybrid (Indicator + Swing) — Pro quant entry rule: BOTH conditions must be true. Bullish indicators must confirm AND swing support/breakout structure must confirm. This keeps entries aligned with trend structure, not just signals."
    },
    entry_options: {
        indicator: "💡 Indicator (Directional Options) — Buys CALL when Bullish Signals meet threshold; buys PUT when Bearish Signals meet threshold (respected by Options Direction filter).",
        custom_price: "💡 Custom Underlying Price — CALL buys when the underlying drops to your target; PUT buys when the underlying rallies to or above your target.",
        atr: "💡 ATR / Volatility Breakout — Buys CALL on bullish ATR band breakout; buys PUT on bearish ATR band breakdown.",
        hybrid_atr: "💡 Hybrid (Indicator + ATR) — Pro quant options entry rule: BOTH conditions must be true before buying premium. CALLs need bullish indicators + bullish ATR structure; PUTs need bearish indicators + bearish ATR structure. This helps avoid paying option spread/theta on low-conviction moves.",
        percentage: "💡 Percentage Move — Buys CALL on fixed % pullback dip; buys PUT on fixed % extension rally from recent extremes.",
        hybrid_pct: "💡 Hybrid (Indicator + Pct) — Pro quant options entry rule: BOTH conditions must be true. CALLs require bullish indicators + bullish % setup; PUTs require bearish indicators + bearish % setup. This avoids chasing moves unless signal confluence agrees.",
        pivot_points: "💡 Pivot Points (S/R Levels) — Buys CALL on support pivot bounce/reclaim; buys PUT on resistance pivot rejection.",
        hybrid_pivot: "💡 Hybrid (Indicator + Pivots) — Pro quant options entry rule: BOTH conditions must be true. CALLs need bullish indicators + support/reclaim; PUTs need bearish indicators + resistance rejection/breakdown. This keeps contract direction aligned with structure.",
        high_low: "💡 Prior Day High/Low Momentum — Buys CALL on breakout above prior day high; buys PUT on breakdown below prior day low.",
        hybrid_high_low: "💡 Hybrid (Indicator + High/Low) — Pro quant options entry rule: BOTH conditions must be true. CALLs need bullish indicators + prior-high breakout; PUTs need bearish indicators + prior-low breakdown. This reduces false intraday option entries.",
        swing_points: "💡 Swing Points (Structure) — Buys CALL on structural swing support bounce; buys PUT on structural swing resistance rejection/breakdown.",
        hybrid_swing: "💡 Hybrid (Indicator + Swing) — Pro quant options entry rule: BOTH conditions must be true. CALLs need bullish indicators + bullish swing structure; PUTs need bearish indicators + bearish swing structure. This avoids buying options against the dominant swing setup."
    },
    exit: {
        indicator: "💡 Indicator (Signal Exit) — Closes position when active Bearish signals from your checked Indicator Pool meet or exceed the Sell Threshold.",
        custom_sltp: "💡 Custom SL/TP (Hard Limits) — Exits position immediately when live market price hits your exact Stop-Loss price OR Take-Profit price target.",
        custom_underlying_sltp: "💡 Custom Underlying SL/TP — Exits option when the underlying hits custom prices.",
        sltp: "💡 ATR / Volatility Multipliers — Dynamic exit: sets Stop-Loss, Take-Profit, and trailing stops based on multiples of Average True Range (ATR) volatility.",
        hybrid: "💡 Hybrid (Indicator + ATR) — Pro quant exit rule: EITHER condition can close the trade. Bearish indicator reversal OR ATR stop/take-profit can trigger exit. Exits are risk control, so waiting for both can turn a good exit into a late exit.",
        percentage: "💡 Percentage (Fixed Pct) — Exits position when price hits your fixed % Take-Profit gain, fixed % Stop-Loss drop, OR trailing percentage stop.",
        hybrid_pct: "💡 Hybrid (Indicator + Pct) — Pro quant exit rule: EITHER condition can close the trade. Bearish indicator reversal OR fixed % SL/TP/trailing stop can trigger exit. This protects profit and limits drawdown without waiting for extra confirmation.",
        pivot_points: "💡 Pivot Points (Resistance Exit) — Sells to close when price rallies into structural R1/R2/R3 resistance or breaks below support pivots.",
        hybrid_pivot: "💡 Hybrid (Indicator + Pivots) — Pro quant exit rule: EITHER condition can close the trade. Bearish indicator reversal OR pivot support/resistance invalidation can trigger exit. Structure-based exits should stay defensive.",
        high_low: "💡 Prior Day High/Low Exit — Sells position if price breaks down below prior day low (stop) or extends through prior day high target.",
        hybrid_high_low: "💡 Hybrid (Indicator + High/Low) — Pro quant exit rule: EITHER condition can close the trade. Bearish indicator reversal OR prior-day high/low target/stop can trigger exit. This avoids holding through a failed breakout.",
        swing_points: "💡 Swing Points (Structure) — Closes position when price hits structural swing resistance targets or violates lower-high/lower-low support.",
        hybrid_swing: "💡 Hybrid (Indicator + Swing) — Pro quant exit rule: EITHER condition can close the trade. Bearish indicator reversal OR swing support/resistance violation can trigger exit. Once structure breaks, risk should be reduced quickly."
    },
    exit_options: {
        indicator: "💡 Indicator (Opposite Signal Exit) — Sells contract to close when opposite signals hit threshold (Bearish threshold for CALLs; Bullish threshold for PUTs).",
        custom_sltp: "💡 Custom SL/TP (Option Premium) — Exits position when the option contract's live premium ($) hits your user-defined Stop-Loss or Take-Profit price.",
        custom_underlying_sltp: "💡 Custom Underlying SL/TP (Stock Price) — Exits option contract when the UNDERLYING stock's market price hits your custom stock SL/TP targets.",
        sltp: "💡 ATR / Volatility Multipliers — Exits contract using UNDERLYING stock ATR levels. CALL: stop/trail below and target above. PUT: stop/trail above and target below.",
        hybrid: "💡 Hybrid (Indicator + ATR) — Pro quant options exit rule: EITHER condition can sell-to-close. Opposite indicator reversal OR underlying ATR stop/take-profit can exit. CALL levels are below/above; PUT levels are above/below. Options decay fast, so exits prioritize risk control.",
        percentage: "💡 Percentage (Contract Pct) — Exits option contract based on fixed % gain (TP), fixed % loss (SL), or trailing percentage drop from contract high.",
        hybrid_pct: "💡 Hybrid (Indicator + Pct) — Pro quant options exit rule: EITHER condition can sell-to-close. Opposite indicator reversal OR premium % SL/TP/trailing stop can exit. This protects against theta decay, spread widening, and fast premium reversals.",
        pivot_points: "💡 Pivot Points (S/R Levels) — Closes option contract when underlying stock price reaches structural S1/R1 Support & Resistance levels.",
        hybrid_pivot: "💡 Hybrid (Indicator + Pivots) — Pro quant options exit rule: EITHER condition can sell-to-close. Opposite indicator reversal OR pivot S/R target/stop can exit. CALL exits watch bearish reversal/resistance failure; PUT exits watch bullish reversal/support failure.",
        high_low: "💡 Prior Day High/Low Exit — Closes option position upon an underlying stock prior day high/low breakout or breakdown.",
        hybrid_high_low: "💡 Hybrid (Indicator + High/Low) — Pro quant options exit rule: EITHER condition can sell-to-close. Opposite indicator reversal OR underlying prior-day high/low target/stop can exit. This keeps options from bleeding while waiting for perfect confirmation.",
        swing_points: "💡 Swing Points (Structure) — Exits option contract when underlying price hits confirmed structural swing support or resistance levels.",
        hybrid_swing: "💡 Hybrid (Indicator + Swing) — Pro quant options exit rule: EITHER condition can sell-to-close. Opposite indicator reversal OR swing support/resistance target/stop can exit. For options, structure failure is enough reason to reduce risk."
    }
};

window.updateStrategyHelperText = function (targetId, category, mode) {
    const el = document.getElementById(targetId);
    if (!el) return;
    const text = (STRATEGY_HELPER_TEXTS[category] && STRATEGY_HELPER_TEXTS[category][mode]) || "";
    el.textContent = text;
    el.classList.toggle('hidden', !text);
};

window.updateRegularExitFields = function () {
    const mode = document.getElementById('strategySellMode_regular')?.value || 'indicator';
    const showIndicator = ['indicator', 'hybrid', 'hybrid_pct', 'hybrid_pivot', 'hybrid_high_low', 'hybrid_swing'].includes(mode);
    const showCustom = mode === 'custom_sltp';
    const showATR = ['sltp', 'hybrid'].includes(mode);
    const showPct = ['percentage', 'hybrid_pct'].includes(mode);

    if (document.getElementById('exitFieldsIndicator')) document.getElementById('exitFieldsIndicator').classList.toggle('hidden', !showIndicator);
    if (document.getElementById('exitFieldsCustom_regular')) document.getElementById('exitFieldsCustom_regular').classList.toggle('hidden', !showCustom);
    if (document.getElementById('exitFieldsATR')) document.getElementById('exitFieldsATR').classList.toggle('hidden', !showATR);
    if (document.getElementById('exitFieldsPct')) document.getElementById('exitFieldsPct').classList.toggle('hidden', !showPct);
    updateSwingSettingsVisibility('regular');
    if (window.updateStrategyHelperText) window.updateStrategyHelperText('strategySellHelper_regular', 'exit', mode);
    window.updateStrategySignalLabels('regular');
    if (window.updateLivePreview_bots) window.updateLivePreview_bots('regular', 'exit');
};

window.updateRegularEntryFields = function () {
    const mode = document.getElementById('strategyBuyMode_regular')?.value || 'indicator';
    const showIndicator = ['indicator', 'hybrid_atr', 'hybrid_pct', 'hybrid_pivot', 'hybrid_high_low', 'hybrid_swing'].includes(mode);
    const showCustom = mode === 'custom_price';
    const showATR = ['atr', 'hybrid_atr'].includes(mode);
    const showPct = ['percentage', 'hybrid_pct'].includes(mode);
    const showPivot = ['pivot_points', 'hybrid_pivot'].includes(mode);
    const showHL = ['high_low', 'hybrid_high_low'].includes(mode);
    const showSwing = ['swing_points', 'hybrid_swing'].includes(mode);
    if (document.getElementById('strategyBuyFields_regular')) document.getElementById('strategyBuyFields_regular').classList.toggle('hidden', !showIndicator);
    if (document.getElementById('strategyCustomBuyFields_regular')) document.getElementById('strategyCustomBuyFields_regular').classList.toggle('hidden', !showCustom);
    if (document.getElementById('strategyEntryAtrFields_regular')) document.getElementById('strategyEntryAtrFields_regular').classList.toggle('hidden', !showATR);
    if (document.getElementById('strategyEntryPctFields_regular')) document.getElementById('strategyEntryPctFields_regular').classList.toggle('hidden', !showPct);
    if (document.getElementById('strategyEntryPivotFields_regular')) document.getElementById('strategyEntryPivotFields_regular').classList.toggle('hidden', !showPivot);
    if (document.getElementById('strategyEntryHighLowFields_regular')) document.getElementById('strategyEntryHighLowFields_regular').classList.toggle('hidden', !showHL);
    if (document.getElementById('strategySwingEntrySettings_regular')) document.getElementById('strategySwingEntrySettings_regular').classList.toggle('hidden', !showSwing);
    updateSwingSettingsVisibility('regular');
    if (window.updateStrategyHelperText) window.updateStrategyHelperText('strategyBuyHelper_regular', 'entry', mode);
    window.updateStrategySignalLabels('regular');
    if (window.updateLivePreview_bots) window.updateLivePreview_bots('regular', 'entry');
};

window.updateOptionsEntryFields = function () {
    const mode = document.getElementById('strategyBuyMode_options')?.value || 'indicator';
    const direction = document.getElementById('strategyOptionsType')?.value || 'calls_only';
    const showIndicator = ['indicator', 'hybrid_atr', 'hybrid_pct', 'hybrid_pivot', 'hybrid_high_low', 'hybrid_swing'].includes(mode);
    const showCustom = mode === 'custom_price';
    const showATR = ['atr', 'hybrid_atr'].includes(mode);
    const showPct = ['percentage', 'hybrid_pct'].includes(mode);
    const showPivot = ['pivot_points', 'hybrid_pivot'].includes(mode);
    const showHL = ['high_low', 'hybrid_high_low'].includes(mode);
    const showSwing = ['swing_points', 'hybrid_swing'].includes(mode);
    if (document.getElementById('strategyBuyFields_options')) document.getElementById('strategyBuyFields_options').classList.toggle('hidden', !showIndicator);
    if (document.getElementById('strategyCallEntryThresholdPanel_options')) document.getElementById('strategyCallEntryThresholdPanel_options').classList.toggle('hidden', direction === 'puts_only');
    if (document.getElementById('strategyPutEntryThresholdPanel_options')) document.getElementById('strategyPutEntryThresholdPanel_options').classList.toggle('hidden', direction !== 'puts_only');
    if (document.getElementById('strategyCustomBuyFields_options')) document.getElementById('strategyCustomBuyFields_options').classList.toggle('hidden', !showCustom);
    if (document.getElementById('strategyEntryAtrFields_options')) document.getElementById('strategyEntryAtrFields_options').classList.toggle('hidden', !showATR);
    if (document.getElementById('strategyEntryPctFields_options')) document.getElementById('strategyEntryPctFields_options').classList.toggle('hidden', !showPct);
    if (document.getElementById('strategyEntryPivotFields_options')) document.getElementById('strategyEntryPivotFields_options').classList.toggle('hidden', !showPivot);
    if (document.getElementById('strategyEntryHighLowFields_options')) document.getElementById('strategyEntryHighLowFields_options').classList.toggle('hidden', !showHL);
    if (document.getElementById('strategySwingEntrySettings_options')) document.getElementById('strategySwingEntrySettings_options').classList.toggle('hidden', !showSwing);
    const directionHint = document.getElementById('strategyOptionsDirectionHint');
    if (directionHint) {
        const isLegacyBoth = document.getElementById('strategyOptionsType')?.dataset.legacyBoth === 'true';
        directionHint.textContent = isLegacyBoth
            ? 'Legacy Both direction is disabled. Choose CALL or PUT before saving this bot.'
            : direction === 'calls_only'
                ? '⚡ Calls Only: Bot will ONLY open bullish CALL option contracts when bullish entry signals confirm. All bearish signals are ignored.'
                : direction === 'puts_only'
                    ? '⚡ Puts Only: Bot will ONLY open bearish PUT option contracts when bearish entry signals confirm. All bullish signals are ignored.'
                    : '⚡ Legacy Both direction is disabled. Choose CALL or PUT.';
    }
    updateSwingSettingsVisibility('options');
    if (window.updateStrategyHelperText) window.updateStrategyHelperText('strategyBuyHelper_options', 'entry_options', mode);
    const customTitle = document.getElementById('strategyCustomBuyTitle_options');
    const customHint = document.getElementById('strategyCustomBuyHint_options');
    if (customTitle) {
        customTitle.textContent = direction === 'puts_only'
            ? 'Underlying PUT Entry Target'
            : 'Underlying CALL Entry Target';
    }
    if (customHint) {
        customHint.textContent = direction === 'puts_only'
            ? 'Custom price opens a PUT when the underlying rallies to or above this target.'
            : 'Custom price opens a CALL when the underlying drops to this target.';
    }
    syncOptionsPutEntryThresholdLabel();
    window.updateStrategySignalLabels('options');
    if (window.updateLivePreview_bots) window.updateLivePreview_bots('options', 'entry');
};

window.clearLegacyOptionsDirectionWarning = function () {
    const directionEl = document.getElementById('strategyOptionsType');
    if (directionEl) delete directionEl.dataset.legacyBoth;
};

function requireValidOptionsDirectionForSave(mode) {
    if (mode !== 'options') return true;
    const directionEl = document.getElementById('strategyOptionsType');
    const value = directionEl?.value || 'calls_only';
    if (directionEl?.dataset.legacyBoth === 'true') {
        alert('Legacy Both direction is disabled. Please choose CALL or PUT before saving this bot.');
        return false;
    }
    if (!['calls_only', 'puts_only'].includes(value)) {
        alert('Options direction must be CALL or PUT.');
        return false;
    }
    return true;
}

window.updateOptionsExitFields = function () {
    const mode = document.getElementById('strategySellMode_options')?.value || 'hybrid';
    const optionsType = document.getElementById('strategyOptionsType')?.value || 'calls_only';
    const isPut = optionsType === 'puts_only';

    const showIndicator = ['indicator', 'hybrid', 'hybrid_pct', 'hybrid_pivot', 'hybrid_high_low', 'hybrid_swing'].includes(mode);
    const showCustom = mode === 'custom_sltp';
    const showCustomUnderlying = mode === 'custom_underlying_sltp';
    const showATR = ['sltp', 'hybrid'].includes(mode);
    const showPct = ['percentage', 'hybrid_pct'].includes(mode);

    if (document.getElementById('exitFieldsIndicator_options')) document.getElementById('exitFieldsIndicator_options').classList.toggle('hidden', !showIndicator);
    if (document.getElementById('exitFieldsCustom_options')) document.getElementById('exitFieldsCustom_options').classList.toggle('hidden', !showCustom);
    if (document.getElementById('exitFieldsCustomUnderlying_options')) document.getElementById('exitFieldsCustomUnderlying_options').classList.toggle('hidden', !showCustomUnderlying);
    if (document.getElementById('exitFieldsATR_options')) document.getElementById('exitFieldsATR_options').classList.toggle('hidden', !showATR);
    if (document.getElementById('exitFieldsPct_options')) document.getElementById('exitFieldsPct_options').classList.toggle('hidden', !showPct);
    updateSwingSettingsVisibility('options');
    if (window.updateStrategyHelperText) window.updateStrategyHelperText('strategySellHelper_options', 'exit_options', mode);

    const helper = document.getElementById('strategySellHelper_options');
    if (helper && helper.textContent) {
        const directionNoteByMode = {
            indicator: isPut
                ? 'PUT-specific: sell-to-close on bullish reversal signals.'
                : 'CALL-specific: sell-to-close on bearish reversal signals.',
            custom_sltp: 'Premium-based: same logic for CALL and PUT, because it watches the option contract price.',
            custom_underlying_sltp: isPut
                ? 'PUT-specific: underlying stop is above entry/current; take-profit is below.'
                : 'CALL-specific: underlying stop is below entry/current; take-profit is above.',
            sltp: isPut
                ? 'PUT-specific: ATR stop/trail is above the underlying; ATR take-profit is below.'
                : 'CALL-specific: ATR stop/trail is below the underlying; ATR take-profit is above.',
            hybrid: isPut
                ? 'PUT-specific: bullish reversal OR ATR stop/target can close.'
                : 'CALL-specific: bearish reversal OR ATR stop/target can close.',
            percentage: 'Premium-percent based: same logic for CALL and PUT, because it watches option premium gain/loss.',
            hybrid_pct: isPut
                ? 'PUT-specific: bullish reversal OR premium % stop/target/trail can close.'
                : 'CALL-specific: bearish reversal OR premium % stop/target/trail can close.',
            pivot_points: isPut
                ? 'PUT-specific: take-profit at support/S1; stop-loss at resistance/R1.'
                : 'CALL-specific: take-profit at resistance/R1; stop-loss at support/S1.',
            hybrid_pivot: isPut
                ? 'PUT-specific: bullish reversal OR support target/resistance stop can close.'
                : 'CALL-specific: bearish reversal OR resistance target/support stop can close.',
            high_low: isPut
                ? 'PUT-specific: take-profit at prior low; stop-loss at prior high.'
                : 'CALL-specific: take-profit at prior high; stop-loss at prior low.',
            hybrid_high_low: isPut
                ? 'PUT-specific: bullish reversal OR prior-low target/prior-high stop can close.'
                : 'CALL-specific: bearish reversal OR prior-high target/prior-low stop can close.',
            swing_points: isPut
                ? 'PUT-specific: take-profit at swing support; stop-loss at swing resistance.'
                : 'CALL-specific: take-profit at swing resistance; stop-loss at swing support.',
            hybrid_swing: isPut
                ? 'PUT-specific: bullish reversal OR swing support/resistance exit can close.'
                : 'CALL-specific: bearish reversal OR swing resistance/support exit can close.'
        };
        const note = directionNoteByMode[mode];
        if (note) helper.textContent = `${helper.textContent} ${note}`;
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
    if (title) {
        title.textContent = isPut
            ? 'Exit Threshold (Min Bullish Signals)'
            : 'Exit Threshold (Min Bearish Signals)';
    }
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
    if (stopLabel) {
        stopLabel.textContent = isPut
            ? 'Underlying Stop Loss Price (Above Entry)'
            : 'Underlying Stop Loss Price (Below Entry)';
    }
    if (tpLabel) {
        tpLabel.textContent = isPut
            ? 'Underlying Take Profit Price (Below Entry)'
            : 'Underlying Take Profit Price (Above Entry)';
    }

    window.updateStrategySignalLabels('options');
    if (window.updateLivePreview_bots) window.updateLivePreview_bots('options', 'exit');
};

window.updateStrategySmartReentryFields = function (mode) {
    if (!mode) mode = window.activeTradingMode || 'regular';
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
        if (vwapPut) vwapPut.classList.remove('hidden');
    }
};

window.currentAtrPreviewData = { price: 100, atr: 2.0, symbol: null, isLive: false, prior_day: null, price_history: [], entry_forecast: '', swing_entry_forecast: '', swing_exit_forecast: '', timeframe: '5Min' };

function parsePreviewNumber(value, fallback = 0) {
    const parsed = parseFloat(String(value ?? '').replace(/[^0-9.-]+/g, ''));
    return Number.isFinite(parsed) ? parsed : fallback;
}

function getPreviewTimeMs(value) {
    const n = Number(value);
    if (!Number.isFinite(n)) return NaN;
    return n > 1e12 ? n : n * 1000;
}

function getEtDateParts(value) {
    const ms = getPreviewTimeMs(value);
    if (!Number.isFinite(ms)) return null;
    const dt = new Date(ms);
    if (Number.isNaN(dt.getTime())) return null;
    try {
        const parts = new Intl.DateTimeFormat('en-US', {
            timeZone: 'America/New_York',
            year: 'numeric',
            month: '2-digit',
            day: '2-digit',
            weekday: 'short',
            hour: '2-digit',
            minute: '2-digit',
            hour12: false
        }).formatToParts(dt);
        const map = {};
        parts.forEach(part => { if (part.type !== 'literal') map[part.type] = part.value; });
        return {
            key: `${map.year}-${map.month}-${map.day}`,
            weekday: map.weekday || '',
            minutes: (parseInt(map.hour || '0', 10) * 60) + parseInt(map.minute || '0', 10),
        };
    } catch {
        return null;
    }
}

function normalizePreviewCandle(candle) {
    if (!candle || typeof candle !== 'object') return null;
    const high = parsePreviewNumber(candle.high, NaN);
    const low = parsePreviewNumber(candle.low, NaN);
    const close = parsePreviewNumber(candle.close, NaN);
    if (!Number.isFinite(high) || !Number.isFinite(low) || !Number.isFinite(close)) return null;
    const open = parsePreviewNumber(candle.open, close);
    const volume = Math.max(0, parsePreviewNumber(candle.volume, 0));
    const et = getEtDateParts(candle.time);
    return {
        time: candle.time,
        open,
        high: Math.max(high, low),
        low: Math.min(high, low),
        close,
        volume,
        etKey: et?.key || null,
        etWeekday: et?.weekday || null,
        etMinutes: et?.minutes ?? null,
    };
}

function isPreviewRegularSession(candle) {
    if (!candle?.etKey || !candle?.etWeekday || candle.etMinutes === null) return true;
    if (['Sat', 'Sun'].includes(candle.etWeekday)) return false;
    return candle.etMinutes >= (9 * 60 + 30) && candle.etMinutes <= (16 * 60);
}

function filterPreviewCandles(priceHistory, lookbackDays, marketSession = 'Regular', isCrypto = false) {
    const candles = (priceHistory || []).map(normalizePreviewCandle).filter(Boolean);
    if (!candles.length) return [];

    const session = String(marketSession || 'Regular').trim().toLowerCase();
    let filtered = candles;
    const useAllSessionCandles = ['extended', '24h', 'crypto24', 'all'].includes(session);
    if (!isCrypto && !useAllSessionCandles) {
        filtered = filtered.filter(isPreviewRegularSession);
    }

    if (!filtered.length) return [];

    const latestKey = filtered.reduce((acc, c) => (acc && acc > c.etKey ? acc : c.etKey), null);
    if (!latestKey || !lookbackDays || lookbackDays <= 0) return filtered;

    const [year, month, day] = latestKey.split('-').map(n => parseInt(n, 10));
    const cutoffDate = new Date(Date.UTC(year, month - 1, day));
    cutoffDate.setUTCDate(cutoffDate.getUTCDate() - lookbackDays);
    const cutoffKey = cutoffDate.toISOString().slice(0, 10);
    return filtered.filter(c => !c.etKey || c.etKey >= cutoffKey);
}

function candidateSwingScore(candle, idx, total, side, avgVolume, averageRange) {
    const volume = Math.max(0, candle.volume || 0);
    const rng = Math.max((candle.high - candle.low) || 0, 1e-9);
    const body = Math.abs(candle.close - candle.open);
    const bodyRatio = Math.min(1, body / rng);
    const rejection = side === 'support'
        ? Math.max(0, (Math.min(candle.open, candle.close) - candle.low) / rng)
        : Math.max(0, (candle.high - Math.max(candle.open, candle.close)) / rng);
    const volWeight = avgVolume > 0 ? Math.min(3, volume / avgVolume) : (volume <= 0 ? 0.5 : 1);
    const rangeWeight = averageRange > 0 ? Math.min(2, rng / averageRange) : 1;
    const recencyWeight = 1 + (idx / Math.max(1, total - 1)) * 0.75;
    const zeroVolumePenalty = volume <= 0 ? 0.35 : 1;
    const base = 1 + (rejection * 1.5) + (bodyRatio * 0.5);
    return base * recencyWeight * Math.max(0.75, volWeight) * Math.max(0.75, rangeWeight) * zeroVolumePenalty;
}

function clusterSwingLevels(candidates, tolerance) {
    if (!candidates.length) return [];
    const ordered = [...candidates].sort((a, b) => a.price - b.price);
    const clusters = [];
    ordered.forEach(cand => {
        const last = clusters[clusters.length - 1];
        if (!last) {
            clusters.push({
                priceSum: cand.price * cand.score,
                weightSum: cand.score,
                touches: 1,
                score: cand.score,
                firstTime: cand.time,
                lastTime: cand.time,
                kind: cand.kind,
                prices: [cand.price],
                volumes: [cand.volume || 0],
            });
            return;
        }
        const center = last.priceSum / Math.max(last.weightSum, 1e-9);
        if (Math.abs(cand.price - center) <= tolerance) {
            last.priceSum += cand.price * cand.score;
            last.weightSum += cand.score;
            last.touches += 1;
            last.score += cand.score;
            last.lastTime = cand.time || last.lastTime;
            last.prices.push(cand.price);
            last.volumes.push(cand.volume || 0);
        } else {
            clusters.push({
                priceSum: cand.price * cand.score,
                weightSum: cand.score,
                touches: 1,
                score: cand.score,
                firstTime: cand.time,
                lastTime: cand.time,
                kind: cand.kind,
                prices: [cand.price],
                volumes: [cand.volume || 0],
            });
        }
    });

    return clusters.map(cluster => {
        const price = cluster.priceSum / Math.max(cluster.weightSum, 1e-9);
        const totalVolume = cluster.volumes.reduce((sum, v) => sum + Math.max(0, v || 0), 0);
        const avgVolume = totalVolume / Math.max(1, cluster.volumes.length);
        const score = cluster.score + Math.log1p(Math.max(0, totalVolume));
        return {
            price,
            touches: cluster.touches,
            score: Number(score.toFixed(3)),
            kind: cluster.kind,
            firstTime: cluster.firstTime,
            lastTime: cluster.lastTime,
            volume: totalVolume,
            avgVolume,
            validated: cluster.touches >= 2 || score >= 2,
            width: Math.max(...cluster.prices) - Math.min(...cluster.prices),
        };
    }).sort((a, b) => b.score - a.score);
}

function computeSwingPreviewContext(priceHistory, currentPrice, lookbackDays, marketSession = 'Regular', isCrypto = false) {
    const candles = filterPreviewCandles(priceHistory, lookbackDays, marketSession, isCrypto);
    if (!candles.length) {
        return {
            candles: [],
            support_levels: [],
            resistance_levels: [],
            nearest_support: null,
            nearest_resistance: null,
            price_tolerance: 0,
            average_volume: 0,
            average_range: 0,
            lookback_days: lookbackDays,
            market_session: marketSession,
            is_crypto: isCrypto,
        };
    }

    const price = parsePreviewNumber(currentPrice, candles[candles.length - 1].close);
    const highs = candles.map(c => c.high);
    const lows = candles.map(c => c.low);
    const volumes = candles.map(c => c.volume || 0);
    const ranges = candles.map(c => Math.max(0, c.high - c.low));
    const averageVolume = volumes.reduce((sum, v) => sum + v, 0) / Math.max(1, volumes.length);
    const averageRange = ranges.reduce((sum, r) => sum + r, 0) / Math.max(1, ranges.length);
    const priceTolerance = Math.max(price * 0.0025, averageRange * 0.5, 0.01);
    const window = candles.length >= 9 ? 2 : 1;
    const supportCandidates = [];
    const resistanceCandidates = [];

    for (let idx = window; idx < candles.length - window; idx += 1) {
        const candle = candles[idx];
        const lowWindow = lows.slice(idx - window, idx + window + 1);
        const highWindow = highs.slice(idx - window, idx + window + 1);
        const localLow = Math.min(...lowWindow);
        const localHigh = Math.max(...highWindow);
        const supportScore = candidateSwingScore(candle, idx, candles.length, 'support', averageVolume, averageRange);
        if (candle.low <= localLow + (priceTolerance * 0.25)) {
            supportCandidates.push({
                price: candle.low,
                score: supportScore,
                kind: 'support',
                time: candle.time,
                volume: candle.volume || 0,
            });
        }
        const resistanceScore = candidateSwingScore(candle, idx, candles.length, 'resistance', averageVolume, averageRange);
        if (candle.high >= localHigh - (priceTolerance * 0.25)) {
            resistanceCandidates.push({
                price: candle.high,
                score: resistanceScore,
                kind: 'resistance',
                time: candle.time,
                volume: candle.volume || 0,
            });
        }
    }

    let supportLevels = clusterSwingLevels(supportCandidates, priceTolerance);
    let resistanceLevels = clusterSwingLevels(resistanceCandidates, priceTolerance);

    const nearest = (levels, above) => {
        const eligible = levels.filter(level => (above ? level.price >= price : level.price <= price));
        if (!eligible.length) return null;
        eligible.sort((a, b) => Math.abs(a.price - price) - Math.abs(b.price - price) || b.score - a.score);
        return eligible[0];
    };

    return {
        candles,
        support_levels: supportLevels,
        resistance_levels: resistanceLevels,
        nearest_support: nearest(supportLevels, false),
        nearest_resistance: nearest(resistanceLevels, true),
        current_price: price,
        price_tolerance: priceTolerance,
        average_volume: averageVolume,
        average_range: averageRange,
        lookback_days: lookbackDays,
        market_session: marketSession,
        is_crypto: isCrypto,
    };
}

function isBullishConfirmation(candle, prev) {
    if (candle.close <= candle.open) return false;
    if (prev && candle.close < prev.close) return false;
    return true;
}

function isBearishConfirmation(candle, prev) {
    if (candle.close >= candle.open) return false;
    if (prev && candle.close > prev.close) return false;
    return true;
}

function touchZone(candle, zonePrice, tolerance) {
    return candle.low <= zonePrice + tolerance && candle.high >= zonePrice - tolerance;
}

function formatSwingLevel(value) {
    return Number.isFinite(value) ? `$${value.toFixed(2)}` : 'N/A';
}

function isPreviewCryptoSymbol(symbol) {
    return isCrypto(symbol);
}

function getStrategyMarketSession(mode = 'regular') {
    return getMarketSessionForStrategy(mode);
}

function buildSwingEntryPreviewHtml(mode, isOptions, symbol, simPrice) {
    const buyMode = document.getElementById(isOptions ? 'strategyBuyMode_options' : 'strategyBuyMode_regular')?.value || 'indicator';
    const optionsType = document.getElementById('strategyOptionsType')?.value || 'calls_only';
    const isPutOption = isOptions && optionsType === 'puts_only';
    const entryMode = document.getElementById(`strategySwingEntryMode_${mode}`)?.value || 'bounce_retest';
    const lookbackDays = getSwingLookbackDaysValue(mode, 'entry');
    const marketSession = getStrategyMarketSession(mode);
    const priceHistory = window.currentAtrPreviewData?.price_history || [];
    const swingCtx = computeSwingPreviewContext(priceHistory, simPrice, lookbackDays, marketSession, isPreviewCryptoSymbol(symbol));
    const support = swingCtx.nearest_support;
    const resistance = swingCtx.nearest_resistance;
    const modeLabel = buyMode === 'hybrid_swing' ? 'Hybrid (Indicator + Swing)' : 'Swing Points';
    const directionLabel = isPutOption ? 'PUT Swing Entry' : isOptions ? 'CALL Swing Entry' : 'Long Swing Entry';
    const parts = [modeLabel, `[${directionLabel}] ${entryMode === 'breakout_retest' ? 'Breakout / Retest' : 'Bounce / Retest'} | ${lookbackDays}D`];

    if (buyMode === 'hybrid_swing') {
        parts.push(isPutOption
            ? '[Hybrid] Bearish swing setup + bearish indicator threshold required'
            : '[Hybrid] Bullish swing setup + bullish indicator threshold required');
    }

    if (!swingCtx.candles.length) {
        parts.push('Awaiting swing levels...');
        return parts.join(' | ');
    }

    const latest = swingCtx.candles[swingCtx.candles.length - 1];
    const prev = swingCtx.candles.length >= 2 ? swingCtx.candles[swingCtx.candles.length - 2] : null;
    const tolerance = swingCtx.price_tolerance || 0;
    let reason = 'Awaiting confirmation';

    if (entryMode === 'breakout_retest') {
        if (!isPutOption && resistance && touchZone(latest, resistance.price, tolerance) && latest.close > resistance.price + tolerance && isBullishConfirmation(latest, prev)) {
            reason = `Breakout retest confirmed above ${formatSwingLevel(resistance.price)} (${resistance.touches} touches)`;
        } else if (isPutOption && support && touchZone(latest, support.price, tolerance) && latest.close < support.price - tolerance && isBearishConfirmation(latest, prev)) {
            reason = `Breakdown retest confirmed below ${formatSwingLevel(support.price)} (${support.touches} touches)`;
        } else if (isPutOption && support) {
            reason = `Waiting for breakdown retest near ${formatSwingLevel(support.price)}`;
        } else if (resistance) {
            reason = `Waiting for breakout retest near ${formatSwingLevel(resistance.price)}`;
        }
    } else {
        if (!isPutOption && support && touchZone(latest, support.price, tolerance) && latest.close > support.price + (tolerance * 0.1) && isBullishConfirmation(latest, prev)) {
            reason = `Bounce confirmed from ${formatSwingLevel(support.price)} (${support.touches} touches)`;
        } else if (isPutOption && resistance && touchZone(latest, resistance.price, tolerance) && latest.close < resistance.price - (tolerance * 0.1) && isBearishConfirmation(latest, prev)) {
            reason = `Rejection confirmed from ${formatSwingLevel(resistance.price)} (${resistance.touches} touches)`;
        } else if (isPutOption && resistance) {
            reason = `Waiting for rejection confirmation at ${formatSwingLevel(resistance.price)}`;
        } else if (support) {
            reason = `Waiting for bounce confirmation at ${formatSwingLevel(support.price)}`;
        }
    }

    parts.push(support ? `Support: ${formatSwingLevel(support.price)} (${support.touches} touches)` : 'Support: N/A');
    parts.push(resistance ? `Resistance: ${formatSwingLevel(resistance.price)} (${resistance.touches} touches)` : 'Resistance: N/A');
    parts.push(reason);
    return parts.join(' | ');
}

function buildSwingExitPreviewHtml(mode, isOptions, symbol, simPrice) {
    const sellMode = document.getElementById(isOptions ? 'strategySellMode_options' : 'strategySellMode_regular')?.value || 'hybrid';
    const optionsType = document.getElementById('strategyOptionsType')?.value || 'calls_only';
    const isPutOption = isOptions && optionsType === 'puts_only';
    const lookbackDays = getSwingLookbackDaysValue(mode, 'exit');
    const marketSession = getStrategyMarketSession(mode);
    const priceHistory = window.currentAtrPreviewData?.price_history || [];
    const swingCtx = computeSwingPreviewContext(priceHistory, simPrice, lookbackDays, marketSession, isPreviewCryptoSymbol(symbol));
    const support = swingCtx.nearest_support;
    const resistance = swingCtx.nearest_resistance;
    const modeLabel = sellMode === 'hybrid_swing' ? 'Hybrid (Indicator + Swing)' : 'Swing Points';
    const directionLabel = isPutOption ? 'PUT Swing Exit' : isOptions ? 'CALL Swing Exit' : 'Long Swing Exit';
    const parts = [`[Exit Strategy] ${modeLabel}`, `[${directionLabel}] ${lookbackDays}D`];

    if (sellMode === 'hybrid_swing') {
        parts.push(isPutOption
            ? '[Hybrid] EITHER bullish reversal signals OR swing support/target can close'
            : '[Hybrid] EITHER bearish reversal signals OR swing resistance/stop can close');
    }

    if (!swingCtx.candles.length) {
        parts.push('Awaiting swing levels...');
        return parts.join(' | ');
    }

    const isLong = !isPutOption;
    const tpZone = isLong ? resistance : support;
    const slZone = isLong ? support : resistance;
    parts.push(tpZone ? `Take Profit: ${formatSwingLevel(tpZone.price)} (${tpZone.touches} touches)` : 'Take Profit: N/A');
    parts.push(slZone ? `Stop Loss: ${formatSwingLevel(slZone.price)} (${slZone.touches} touches)` : 'Stop Loss: N/A');
    if (tpZone) parts.push(`TP Strength: ${tpZone.score.toFixed(2)}`);
    if (slZone) parts.push(`SL Strength: ${slZone.score.toFixed(2)}`);
    return parts.join(' | ');
}

window.fetchLiveAtrForPreview = async function (ticker, overrideTf) {
    if (!ticker || !botChartPreviewState.open) return;
    try {
        const headers = await getAuthHeaders();
        const activeMode = window.activeTradingMode || 'regular';
        const tf = overrideTf || document.getElementById(`strategyTimeframe_${activeMode}`)?.value || '5Min';

        if (window.currentAtrPreviewData && window.currentAtrPreviewData.symbol === ticker && window.currentAtrPreviewData.timeframe === tf && window.currentAtrPreviewData.isLive) {
            if (window.updateLivePreview_bots) {
                window.updateLivePreview_bots('regular', 'both');
                window.updateLivePreview_bots('options', 'both');
            }
            return;
        }

        const entryBoxRegular = document.getElementById('livePreviewEntryBox_regular');
        const exitBoxRegular = document.getElementById('livePreviewExitBox_regular');
        const entryBoxOptions = document.getElementById('livePreviewEntryBox_options');
        const exitBoxOptions = document.getElementById('livePreviewExitBox_options');
        const loadingHtml = `&#8987; Fetching live market data for ${ticker} (${tf})...`;
        if (entryBoxRegular && !entryBoxRegular.classList.contains('hidden')) entryBoxRegular.innerHTML = loadingHtml;
        if (exitBoxRegular && !exitBoxRegular.classList.contains('hidden')) exitBoxRegular.innerHTML = loadingHtml;
        if (entryBoxOptions && !entryBoxOptions.classList.contains('hidden')) entryBoxOptions.innerHTML = loadingHtml;
        if (exitBoxOptions && !exitBoxOptions.classList.contains('hidden')) exitBoxOptions.innerHTML = loadingHtml;

        const res = await fetchWithTimeout(`${API_BASE}/api/chart/${ticker}?timeframe=${tf}&source=bots`, { headers }, 10000);
        if (res.ok) {
            const data = await res.json();
            if (data && data.primaryScan) {
                window.currentAtrPreviewData = {
                    price: parseFloat(data.primaryScan.price_raw || String(data.primaryScan.price || '100').replace(/[^0-9.-]+/g, '')),
                    atr: parseFloat(data.primaryScan.atr || 2.0),
                    symbol: ticker,
                    isLive: true,
                    prior_day: data.primaryScan.prior_day_ohlc || null,
                    price_history: data.priceHistory || data.price_history || data.primaryScan.price_history || [],
                    entry_forecast: data.primaryScan.entry_forecast || '',
                    swing_entry_forecast: data.primaryScan.swing_entry_forecast || '',
                    swing_exit_forecast: data.primaryScan.swing_exit_forecast || '',
                    timeframe: tf
                };
                if (window.updateLivePreview_bots) {
                    window.updateLivePreview_bots('regular', 'both');
                    window.updateLivePreview_bots('options', 'both');
                }
            }
        }
    } catch (e) {
        if (e.name !== 'AbortError') {
            console.warn("[live-preview] Could not fetch fresh preview data; using cached preview if available.", e.message || e);
        }
        if (window.updateLivePreview_bots) {
            window.updateLivePreview_bots('regular', 'both');
            window.updateLivePreview_bots('options', 'both');
        }
    }
};

function getAdaptiveGridPreviewAnchor(type, fallbackPrice, isLive) {
    if (!isLive || typeof getSavedBotSettings !== 'function') return fallbackPrice;
    const settings = getSavedBotSettings(window.currentStrategySymbol || window.currentAtrPreviewData?.symbol || '');
    const key = type === 'entry' ? 'entry_grid_anchor_price' : 'exit_grid_anchor_price';
    const savedAnchor = parseFloat(settings?.[key]);
    return Number.isFinite(savedAnchor) && savedAnchor > 0 ? savedAnchor : fallbackPrice;
}

function getAdaptiveGridPreviewCapital() {
    const configured = parseFloat(document.getElementById('strategyCapital')?.value || 0);
    const settings = typeof getSavedBotSettings === 'function'
        ? getSavedBotSettings(window.currentStrategySymbol || window.currentAtrPreviewData?.symbol || '')
        : {};
    const compound = parseFloat(settings?.compound_amount);
    if (getAllocationMode('regular') === 'compound' && Number.isFinite(compound) && compound > 0) {
        return compound;
    }
    return Number.isFinite(configured) && configured > 0 ? configured : 0;
}

function getSmartGridPreviewPlan(type, kind, levels, anchor, currentAtr, configuredDistances, configuredAllocations) {
    const smart = getSmartDynamicGridSettings();
    if (!smart.continuous_grid_adaptive_mode || !levels.length) {
        return { distances: configuredDistances, allocations: configuredAllocations, label: '', trailing: false, peakPrice: anchor };
    }
    const saved = typeof getSavedBotSettings === 'function'
        ? getSavedBotSettings(window.currentStrategySymbol || window.currentAtrPreviewData?.symbol || '')
        : {};
    const history = (window.currentAtrPreviewData?.price_history || []).slice(-80).map(row => ({
        close: Number(row.close ?? row.Close),
        high: Number(row.high ?? row.High),
        low: Number(row.low ?? row.Low),
    })).filter(row => row.close > 0 && row.high > 0 && row.low > 0);
    let trend = Number(saved?.[`${type}_grid_trend_score`]);
    let volatility = Number(saved?.[`${type}_grid_volatility_ratio`]);
    if (!Number.isFinite(trend) || !Number.isFinite(volatility)) {
        const closes = history.map(row => row.close);
        const ema = (period) => {
            if (!closes.length) return 0;
            const alpha = 2 / (period + 1);
            return closes.slice(-60).reduce((value, close, index) => index ? value + alpha * (close - value) : close, 0);
        };
        const ranges = history.map((row, index) => {
            const previous = index ? history[index - 1].close : row.close;
            return Math.max(row.high - row.low, Math.abs(row.high - previous), Math.abs(row.low - previous));
        }).filter(value => value > 0);
        const sorted = ranges.slice(-40).sort((a, b) => a - b);
        const median = sorted.length ? sorted[Math.floor(sorted.length / 2)] : 0;
        const atr = Math.max(0, Number(currentAtr) || (ranges.slice(-14).reduce((sum, value) => sum + value, 0) / Math.max(1, Math.min(14, ranges.length))));
        trend = closes.length >= 22 ? Math.max(-1, Math.min(1, (ema(8) - ema(21)) / Math.max(atr, median, closes.at(-1) * 0.001, 0.000001))) : 0;
        volatility = atr > 0 && median > 0 ? Math.max(0.65, Math.min(1.75, atr / median)) : 1;
    }
    trend = Math.max(-1, Math.min(1, Number(trend) || 0));
    volatility = Math.max(0.65, Math.min(1.75, Number(volatility) || 1));
    const strength = Math.max(0, Math.min(1, Number(smart.continuous_grid_adaptation_strength) / 100));
    const trendFactor = type === 'entry'
        ? 1 - 0.25 * strength * Math.max(trend, 0) + 0.55 * strength * Math.max(-trend, 0)
        : 1 + 0.55 * strength * Math.max(trend, 0) - 0.35 * strength * Math.max(-trend, 0);
    const distanceFactor = Math.max(0.5, Math.min(2, trendFactor * (kind === 'pct' ? Math.pow(volatility, 0.75 * strength) : 1)));
    const minEdge = Math.max(0, Math.min(10, Number(smart.continuous_grid_min_edge_pct) || 0));
    const distances = configuredDistances.map((base, index) => {
        let effective = base * distanceFactor;
        if (kind === 'atr' && anchor > 0 && currentAtr > 0) effective = Math.max(effective, anchor * minEdge / 100 * (index + 1) / currentAtr);
        if (kind === 'pct') effective = Math.max(effective, minEdge * (index + 1));
        return effective;
    });
    const center = (levels.length - 1) / 2;
    const weighted = configuredAllocations.map((allocation, index) => {
        const position = (index - center) / Math.max(1, center);
        const exponent = (type === 'entry' ? -trend * position : trend * position) * strength * 0.70;
        return allocation * Math.exp(exponent);
    });
    const configuredTotal = configuredAllocations.reduce((sum, value) => sum + value, 0);
    const weightedTotal = weighted.reduce((sum, value) => sum + value, 0) || 1;
    const allocations = weighted.map(value => value * configuredTotal / weightedTotal);
    const regime = trend > 0.15 ? 'uptrend' : (trend < -0.15 ? 'downtrend' : 'balanced');
    const peakPrice = Math.max(anchor, Number(saved?.[`${type}_grid_high_water_price`]) || 0, ...history.map(row => row.high));
    return { distances, allocations, label: `Smart Dynamic: ${regime} ${trend >= 0 ? '+' : ''}${trend.toFixed(2)} • volatility ${volatility.toFixed(2)}x • ${smart.continuous_grid_trailing_mode ? 'levels ratchet from high-water' : 'targets fixed at anchor'}`, trailing: !!smart.continuous_grid_trailing_mode, peakPrice };
}

function buildAdaptiveGridPreviewHtml(type, symbol, referencePrice, isLive, tf) {
    const isEntry = type === 'entry';
    const levels = getEnabledGridLevels('regular', type);
    const anchor = getAdaptiveGridPreviewAnchor(type, referencePrice, isLive);
    const capital = getAdaptiveGridPreviewCapital();
    const title = isLive
        ? `&#128302; Live Preview: ${symbol}${tf ? ` (${tf} Timeframe)` : ''}`
        : `&#128302; Example Preview: ${symbol}`;
    const anchorName = isEntry ? 'Buy reference anchor' : 'Average-cost sell anchor';

    if (!levels.length) {
        return `${title} (${isEntry ? 'Adaptive Buy Ladder' : 'Adaptive Sell Ladder'})<br/>${anchorName}: $${anchor.toFixed(2)}<br/><span class="text-red-600">Enable at least one ladder level.</span>`;
    }

    const configuredDistances = levels.map(level => Math.max(0, parseFloat(document.getElementById(`strategyGrid${isEntry ? 'Entry' : 'Exit'}L${level}_regular`)?.value || 0)));
    const configuredAllocations = levels.map(level => Math.max(0, parseFloat(document.getElementById(isEntry ? `strategyGridEntryL${level}BuyPct_regular` : `strategyGridExitL${level}SellPct_regular`)?.value || 0)));
    const smart = getSmartDynamicGridSettings();
    const plan = getSmartGridPreviewPlan(type, 'pct', levels, anchor, Number(window.currentAtrPreviewData?.atr || 0), configuredDistances, configuredAllocations);
    let allocationTotal = 0;
    const rows = levels.map((level, index) => {
        const basePct = configuredDistances[index];
        const triggerPct = plan.distances[index];
        const allocationPct = plan.allocations[index];
        allocationTotal += allocationPct;
        const activationPrice = anchor * (1 + (isEntry ? -1 : 1) * triggerPct / 100);
        const previewAtr = Number(window.currentAtrPreviewData?.atr || 0);
        const trailGapPct = Math.max(Number(smart.continuous_grid_min_edge_pct) || 0, triggerPct * 0.5, previewAtr > 0 && anchor > 0 ? previewAtr / anchor * 100 * 0.75 : 0);
        const trailingArmed = plan.trailing && (!isEntry ? plan.peakPrice >= activationPrice : plan.peakPrice > anchor);
        const triggerPrice = trailingArmed && !isEntry
            ? plan.peakPrice * (1 - trailGapPct / 100)
            : (isEntry && trailingArmed ? plan.peakPrice * (1 - triggerPct / 100) : activationPrice);
        const actionText = isEntry ? 'Buy at/below' : 'Sell at/above';
        const moveText = trailingArmed
            ? (isEntry ? `ratchet -${triggerPct.toFixed(2)}% from high-water $${plan.peakPrice.toFixed(2)}` : `activation +${triggerPct.toFixed(2)}% → trail -${trailGapPct.toFixed(2)}% from $${plan.peakPrice.toFixed(2)}`)
            : `${isEntry ? '-' : '+'}${triggerPct.toFixed(2)}%${Math.abs(basePct - triggerPct) > 0.0001 ? `; base ${basePct.toFixed(2)}%` : ''}`;
        const allocationText = isEntry
            ? `Buy ${allocationPct.toFixed(1)}%${capital > 0 ? ` ($${(capital * allocationPct / 100).toFixed(2)})` : ''}`
            : `Sell ${allocationPct.toFixed(1)}% of ladder-start position`;
        return `<span class="font-black">L${level}</span> • ${isEntry ? 'Buy at/below' : (trailingArmed ? 'Sell at/below' : actionText)} $${triggerPrice.toFixed(2)} (${moveText}) • ${allocationText}`;
    });
    const totalClass = allocationTotal > 100.0001 ? 'text-red-600' : 'text-slate-600';
    const totalBasis = isEntry
        ? `${allocationTotal.toFixed(1)}%${capital > 0 ? ` of $${capital.toFixed(2)} capital` : ''}`
        : `${allocationTotal.toFixed(1)}% of the ladder-start position`;
    const disabledText = isEntry
        ? 'Fixed Pullback % and Entry Style are bypassed.'
        : 'Fixed Stop Loss, Take Profit and Trail are bypassed.';

    return `${title} (${isEntry ? 'Adaptive Buy Ladder' : 'Adaptive Sell Ladder'}) | ${anchorName}: $${anchor.toFixed(2)}<br/>${plan.label ? `<span class="text-sky-700 font-bold">${plan.label}</span><br/>` : ''}${rows.join('<br/>')}<br/><span class="${totalClass}">Enabled allocation: ${totalBasis}</span><br/><span class="text-slate-500">${disabledText} Fast gaps combine crossed enabled levels into one order.</span>`;
}

function buildAdaptiveAtrGridPreviewHtml(type, symbol, referencePrice, currentAtr, isLive, tf) {
    const isEntry = type === 'entry';
    const levels = getEnabledAtrGridLevels(type);
    const anchor = getAdaptiveGridPreviewAnchor(type, referencePrice, isLive);
    const saved = typeof getSavedBotSettings === 'function' ? getSavedBotSettings(window.currentStrategySymbol || symbol) : {};
    const savedAtr = Number(saved?.[`${type}_grid_anchor_atr`]);
    const anchorAtr = isLive && Number.isFinite(savedAtr) && savedAtr > 0 ? savedAtr : Math.max(0, Number(currentAtr) || 0);
    const capital = getAdaptiveGridPreviewCapital();
    const title = isLive ? `&#128302; Live Preview: ${symbol}${tf ? ` (${tf} Timeframe)` : ''}` : `&#128302; Example Preview: ${symbol}`;
    if (!levels.length || anchorAtr <= 0) return `${title} (Adaptive ATR ${isEntry ? 'Buy' : 'Sell'} Ladder)<br/>${!levels.length ? 'Enable at least one ladder level.' : 'Waiting for a valid ATR value.'}`;
    const prefix = isEntry ? 'strategyAtrGridEntry' : 'strategyAtrGridExit';
    const configuredDistances = levels.map(level => Math.max(0, Number(document.getElementById(`${prefix}L${level}Mult_regular`)?.value || 0)));
    const configuredAllocations = levels.map(level => Math.max(0, Number(document.getElementById(`${prefix}L${level}${isEntry ? 'Buy' : 'Sell'}Pct_regular`)?.value || 0)));
    const smart = getSmartDynamicGridSettings();
    const liveAtr = Math.max(0, Number(currentAtr) || anchorAtr);
    const runtimeAtr = smart.continuous_grid_adaptive_mode && smart.continuous_grid_trailing_mode && anchorAtr > 0
        ? anchorAtr * Math.min(1.5, Math.max(0.75, liveAtr / anchorAtr))
        : anchorAtr;
    const plan = getSmartGridPreviewPlan(type, 'atr', levels, anchor, runtimeAtr, configuredDistances, configuredAllocations);
    const total = plan.allocations.reduce((sum, value) => sum + value, 0);
    const rows = levels.map((level, index) => {
        const mult = plan.distances[index];
        const base = configuredDistances[index];
        const allocation = plan.allocations[index];
        const activation = anchor + (isEntry ? -1 : 1) * mult * runtimeAtr;
        const edgeAtr = anchor > 0 && runtimeAtr > 0 ? anchor * (Number(getSmartDynamicGridSettings().continuous_grid_min_edge_pct) || 0) / 100 / runtimeAtr : 0;
        const trailGapAtr = Math.max(edgeAtr, mult * 0.5);
        const trailingArmed = plan.trailing && (!isEntry ? plan.peakPrice >= activation : plan.peakPrice > anchor);
        const trigger = trailingArmed
            ? (isEntry ? plan.peakPrice - mult * runtimeAtr : plan.peakPrice - trailGapAtr * runtimeAtr)
            : activation;
        const move = trailingArmed
            ? (isEntry ? `ratchet -${mult.toFixed(2)}x from high-water $${plan.peakPrice.toFixed(2)}` : `activation +${mult.toFixed(2)}x → trail -${trailGapAtr.toFixed(2)}x from $${plan.peakPrice.toFixed(2)}`)
            : `${isEntry ? '-' : '+'}${mult.toFixed(2)}x ATR${Math.abs(base - mult) > 0.0001 ? `; base ${base.toFixed(2)}x` : ''}`;
        const action = !isEntry && trailingArmed ? 'Sell at/below' : (isEntry ? 'Buy at/below' : 'Sell at/above');
        return `<span class="font-black">L${level}</span> • ${action} $${trigger.toFixed(2)} (${move}) • ${isEntry ? `Buy ${allocation.toFixed(1)}%${capital > 0 ? ` ($${(capital * allocation / 100).toFixed(2)})` : ''}` : `Sell ${allocation.toFixed(1)}%`}`;
    }).join('<br/>');
    return `${title} (Adaptive ATR ${isEntry ? 'Buy' : 'Sell'} Ladder) | Anchor: $${anchor.toFixed(2)} | Runtime ATR: $${runtimeAtr.toFixed(2)} (anchor $${anchorAtr.toFixed(2)})<br/>${plan.label ? `<span class="text-sky-700 font-bold">${plan.label}</span><br/>` : ''}${rows}<br/>Enabled allocation: ${total.toFixed(1)}%<br/><span class="text-slate-500">ATR levels recalculate from bounded live volatility; fixed stop, target and trail controls are bypassed. Fast gaps combine crossed levels.</span>`;
}

function buildEntryPreviewHtml(mode, isOptions, symbol, simPrice, simAtr, priorDay, tf, suffix) {
    const buyMode = document.getElementById(isOptions ? 'strategyBuyMode_options' : 'strategyBuyMode_regular')?.value || 'indicator';
    const modeLabel = buyMode.replace(/_/g, ' ').replace(/\b\w/g, ch => ch.toUpperCase());
    const isLive = !!window.currentAtrPreviewData?.isLive;
    const previewLabel = isLive ? 'Live Preview' : 'Example Preview';
    const basePrefix = `🔮 ${previewLabel}: ${symbol} (Entry: ${modeLabel}) | Price: $${simPrice.toFixed(2)}`;
    const entryForecast = window.currentAtrPreviewData?.entry_forecast || '';
    const swingEntryForecast = window.currentAtrPreviewData?.swing_entry_forecast || '';

    if (buyMode === 'custom_price') {
        const targetEl = document.getElementById(`strategyCustomBuyPrice_${isOptions ? 'options' : 'regular'}`);
        const target = parseFloat(targetEl?.value || 0);
        if (isOptions) {
            const optionsType = document.getElementById('strategyOptionsType')?.value || 'calls_only';
            const isPut = optionsType === 'puts_only';
            const targetLabel = isPut ? 'PUT Entry At/Above' : 'CALL Entry At/Below';
            const directionNote = isPut ? 'PUT custom entry waits for an upside rally/rejection zone' : 'CALL custom entry waits for a dip/reclaim zone';
            return `${basePrefix}<br/>${targetLabel}: $${target.toFixed(2)}<br/>${directionNote}`;
        }
        return `${basePrefix}<br/>Target Entry: $${target.toFixed(2)}`;
    }

    if (['atr', 'hybrid_atr'].includes(buyMode)) {
        if (!isOptions && document.getElementById('strategyAtrGridContinuous_regular')?.checked) {
            return buildAdaptiveAtrGridPreviewHtml('entry', symbol, simPrice, simAtr, isLive, tf);
        }
        const multEl = document.getElementById(`strategyEntryAtrMult_${isOptions ? 'options' : 'regular'}`);
        const mult = parseFloat(multEl?.value || 2);
        const style = document.getElementById(`strategyEntryAtrStyle_${isOptions ? 'options' : 'regular'}`)?.value || 'pullback_reclaim';
        const longLevel = style === 'breakout_momentum' ? simPrice + (simAtr * mult) : simPrice - (simAtr * mult);
        const shortLevel = style === 'breakout_momentum' ? simPrice - (simAtr * mult) : simPrice + (simAtr * mult);
        const styleLabel = style === 'breakout_momentum' ? 'Breakout / Momentum' : 'Pullback / Reclaim';
        const longLabel = style === 'breakout_momentum' ? 'Buy Above' : 'Buy Below';
        if (isOptions) {
            const optionsType = document.getElementById('strategyOptionsType')?.value || 'calls_only';
            const callLabel = style === 'breakout_momentum' ? 'CALL Above' : 'CALL Reclaim From';
            const putLabel = style === 'breakout_momentum' ? 'PUT Below' : 'PUT Rejection From';
            const triggerLines = [];
            if (optionsType === 'puts_only') triggerLines.push(`${putLabel}: $${shortLevel.toFixed(2)}`);
            else triggerLines.push(`${callLabel}: $${longLevel.toFixed(2)}`);
            return `${basePrefix}<br/>${triggerLines.join('<br/>')}<br/>${styleLabel} | ATR x ${mult.toFixed(1)}${buyMode.startsWith('hybrid_') ? ' | Hybrid requires indicator + ATR' : ''}`;
        }
        return `${basePrefix}<br/>${longLabel}: $${longLevel.toFixed(2)}<br/>${styleLabel} | ATR x ${mult.toFixed(1)}${buyMode.startsWith('hybrid_') ? ' | Hybrid requires indicator + ATR' : ''}`;
    }

    if (['percentage', 'hybrid_pct'].includes(buyMode)) {
        const adaptiveGridEnabled = !isOptions && !!document.getElementById('strategyGridContinuous_regular')?.checked;
        if (adaptiveGridEnabled) {
            return buildAdaptiveGridPreviewHtml('entry', symbol, simPrice, isLive, tf);
        }
        const pctEl = document.getElementById(`strategyEntryPctMove_${isOptions ? 'options' : 'regular'}`);
        const pct = parseFloat(pctEl?.value || 5);
        const style = document.getElementById(`strategyEntryPctStyle_${isOptions ? 'options' : 'regular'}`)?.value || 'dip_reclaim';
        const longLevel = style === 'breakout_momentum' ? simPrice * (1 + pct / 100) : simPrice * (1 - pct / 100);
        const shortLevel = style === 'breakout_momentum' ? simPrice * (1 - pct / 100) : simPrice * (1 + pct / 100);
        const styleLabel = style === 'breakout_momentum' ? 'Breakout / Momentum' : (isOptions ? 'Reversal / Reclaim' : 'Dip / Reclaim');
        const longLabel = style === 'breakout_momentum' ? 'Buy Above' : 'Buy Below';
        if (isOptions) {
            const optionsType = document.getElementById('strategyOptionsType')?.value || 'calls_only';
            const callLabel = style === 'breakout_momentum' ? 'CALL Above' : 'CALL Reclaim From';
            const putLabel = style === 'breakout_momentum' ? 'PUT Below' : 'PUT Rejection From';
            const triggerLines = [];
            if (optionsType === 'puts_only') triggerLines.push(`${putLabel}: $${shortLevel.toFixed(2)}`);
            else triggerLines.push(`${callLabel}: $${longLevel.toFixed(2)}`);
            return `${basePrefix}<br/>${triggerLines.join('<br/>')}<br/>${styleLabel} | ${pct.toFixed(1)}% move${buyMode.startsWith('hybrid_') ? ' | Hybrid requires indicator + structure' : ''}`;
        }
        return `${basePrefix}<br/>${longLabel}: $${longLevel.toFixed(2)}<br/>${styleLabel} | ${pct.toFixed(1)}% move${buyMode.startsWith('hybrid_') ? ' | Hybrid requires indicator + structure' : ''}`;
    }

    if (['pivot_points', 'hybrid_pivot'].includes(buyMode)) {
        if (!priorDay) {
            return `${basePrefix}<br/>Awaiting prior day data...`;
        }
        const h = priorDay.high, l = priorDay.low, c = priorDay.close;
        const p = (h + l + c) / 3;
        const r1 = (p * 2) - l;
        const s1 = (p * 2) - h;
        if (isOptions) {
            const optionsType = document.getElementById('strategyOptionsType')?.value || 'calls_only';
            if (optionsType === 'puts_only') {
                return `${basePrefix}<br/>PUT Pivot Entry: resistance rejection or pivot breakdown<br/>Pivot: $${p.toFixed(2)} &nbsp;|&nbsp; Resistance (R1): $${r1.toFixed(2)}${buyMode.startsWith('hybrid_') ? '<br/>Hybrid requires bearish indicators + pivot structure' : ''}`;
            }
            return `${basePrefix}<br/>CALL Pivot Entry: support reclaim or pivot breakout<br/>Pivot: $${p.toFixed(2)} &nbsp;|&nbsp; Support (S1): $${s1.toFixed(2)}${buyMode.startsWith('hybrid_') ? '<br/>Hybrid requires bullish indicators + pivot structure' : ''}`;
        }
        return `${basePrefix}<br/>Pivot: $${p.toFixed(2)}<br/>Support (S1): $${s1.toFixed(2)} &nbsp;|&nbsp; Resistance (R1): $${r1.toFixed(2)}${buyMode.startsWith('hybrid_') ? '<br/>Hybrid requires indicator + pivot structure' : ''}`;
    }

    if (['high_low', 'hybrid_high_low'].includes(buyMode)) {
        if (!priorDay) {
            return `${basePrefix}<br/>Awaiting prior day data...`;
        }
        const levels = getPriorDayHighLowPreviewLevels(priorDay);
        if (!levels) {
            return `${basePrefix}<br/>Awaiting valid prior day data...`;
        }
        const bufferLine = levels.bufferPct > 0 ? `<br/>Buffer: ${levels.bufferPct.toFixed(2)}% | PDH/PDL: $${levels.high.toFixed(2)} / $${levels.low.toFixed(2)}` : `<br/>PDH/PDL: $${levels.high.toFixed(2)} / $${levels.low.toFixed(2)}`;
        if (isOptions) {
            const optionsType = document.getElementById('strategyOptionsType')?.value || 'calls_only';
            if (optionsType === 'puts_only') {
                return `${basePrefix}<br/>PUT High/Low Entry: breakdown below prior low trigger<br/>PUT Below: $${levels.lowTrigger.toFixed(2)}${bufferLine}${buyMode.startsWith('hybrid_') ? '<br/>Hybrid requires bearish indicators + prior-low breakdown' : ''}`;
            }
            return `${basePrefix}<br/>CALL High/Low Entry: breakout above prior high trigger<br/>CALL Above: $${levels.highTrigger.toFixed(2)}${bufferLine}${buyMode.startsWith('hybrid_') ? '<br/>Hybrid requires bullish indicators + prior-high breakout' : ''}`;
        }
        return `${basePrefix}<br/>Buy Above Trigger: $${levels.highTrigger.toFixed(2)}<br/>Breakdown Below Trigger: $${levels.lowTrigger.toFixed(2)}${bufferLine}${buyMode.startsWith('hybrid_') ? '<br/>Hybrid requires indicator + high/low structure' : ''}`;
    }

    if (['swing_points', 'hybrid_swing'].includes(buyMode)) {
        return `${basePrefix}<br/>${buildSwingEntryPreviewHtml(mode, isOptions, symbol, simPrice)}`;
    }

    if (buyMode === 'indicator') {
        return '';
    }

    const buyThreshold = document.getElementById(`strategyBuyThreshold_${mode}`)?.value || '0';
    return `${basePrefix}<br/>Buy Threshold: ${buyThreshold}`;
}

window.updateLivePreview_bots = function (mode, scope = 'both') {
    const isOptions = mode === 'options';
    const suffix = isOptions ? '_options' : '';
    const entryBox = document.getElementById(`livePreviewEntryBox_${isOptions ? 'options' : 'regular'}`);
    const exitBox = document.getElementById(`livePreviewExitBox_${isOptions ? 'options' : 'regular'}`);

    // Use correct sell mode ID based on recent changes!
    const sellModeEl = document.getElementById(isOptions ? 'strategySellMode_options' : 'strategySellMode_regular');
    const buyModeEl = document.getElementById(isOptions ? 'strategyBuyMode_options' : 'strategyBuyMode_regular');
    if (!sellModeEl) return;
    const sellMode = sellModeEl.value;
    const buyMode = buyModeEl?.value || 'indicator';

    const showCustom = sellMode === 'custom_sltp';
    const showCustomUnderlying = isOptions && sellMode === 'custom_underlying_sltp';
    const showATR = ['sltp', 'hybrid'].includes(sellMode);
    const showPct = ['percentage', 'hybrid_pct'].includes(sellMode);
    const showPivot = ['pivot_points', 'hybrid_pivot'].includes(sellMode);
    const showHL = ['high_low', 'hybrid_high_low'].includes(sellMode) && !showATR;
    const showEntrySwing = ['swing_points', 'hybrid_swing'].includes(buyMode);
    const showExitSwing = ['swing_points', 'hybrid_swing'].includes(sellMode);

    let simPrice = window.currentAtrPreviewData ? window.currentAtrPreviewData.price || 100 : 100;
    const isLive = window.currentAtrPreviewData ? window.currentAtrPreviewData.isLive : false;
    let symbol = window.currentAtrPreviewData ? window.currentAtrPreviewData.symbol || 'Example' : 'Example';
    const simAtr = window.currentAtrPreviewData ? window.currentAtrPreviewData.atr || 2.0 : 2;
    const priorDay = window.currentAtrPreviewData ? window.currentAtrPreviewData.prior_day : null;
    const tf = window.currentAtrPreviewData ? window.currentAtrPreviewData.timeframe : '5m';

    if ((scope === 'both' || scope === 'entry') && entryBox) {
        const entryHtml = buildEntryPreviewHtml(mode, isOptions, symbol, simPrice, simAtr, priorDay, tf, suffix);
        if (entryHtml) {
            entryBox.innerHTML = entryHtml;
            entryBox.classList.remove('hidden');
        } else {
            entryBox.classList.add('hidden');
            entryBox.innerHTML = '';
        }
    }

    if (!exitBox || scope === 'entry') return;

    const adaptiveExitGridEnabled = !isOptions
        && showPct
        && !!document.getElementById('strategyExitGridContinuous_regular')?.checked;
    if (adaptiveExitGridEnabled) {
        exitBox.innerHTML = buildAdaptiveGridPreviewHtml('exit', symbol, simPrice, isLive, tf);
        exitBox.classList.remove('hidden');
        return;
    }
    const adaptiveAtrExitEnabled = !isOptions
        && showATR
        && !!document.getElementById('strategyExitAtrGridContinuous_regular')?.checked;
    if (adaptiveAtrExitEnabled) {
        exitBox.innerHTML = buildAdaptiveAtrGridPreviewHtml('exit', symbol, simPrice, simAtr, isLive, tf);
        exitBox.classList.remove('hidden');
        return;
    }

    if (showCustom) {
        if (isOptions) {
            simPrice = 5.00;
            symbol = "Assumed Option Premium";
        }
        const customSl = parseFloat(document.getElementById(`strategyCustomStopLossPrice_${isOptions ? 'options' : 'regular'}`)?.value || 0);
        const customTp = parseFloat(document.getElementById(`strategyCustomTakeProfitPrice_${isOptions ? 'options' : 'regular'}`)?.value || 0);
        const slText = customSl > 0 ? `$${customSl.toFixed(2)}` : 'Not set';
        const tpText = customTp > 0 ? `$${customTp.toFixed(2)}` : 'Not set';
        const priceLabel = isOptions ? 'Premium' : 'Price';
        exitBox.innerHTML = `&#128302; Live Preview: ${symbol} (Exit: Custom SL/TP) | ${priceLabel}: $${simPrice.toFixed(2)}<br/>Stop Loss: ${slText} &nbsp;|&nbsp; Take Profit: ${tpText}`;
        exitBox.classList.remove('hidden');
        return;
    }

    if (showCustomUnderlying) {
        const optionsType = document.getElementById('strategyOptionsType')?.value || 'calls_only';
        const isPutUnderlyingExit = optionsType === 'puts_only';
        const customSl = parseFloat(document.getElementById('strategyCustomUnderlyingStopLossPrice_options')?.value || 0);
        const customTp = parseFloat(document.getElementById('strategyCustomUnderlyingTakeProfitPrice_options')?.value || 0);
        const slText = customSl > 0 ? `$${customSl.toFixed(2)}` : 'Not set';
        const tpText = customTp > 0 ? `$${customTp.toFixed(2)}` : 'Not set';
        const directionText = isPutUnderlyingExit
            ? 'PUT underlying exit: stop above, target below'
            : 'CALL underlying exit: stop below, target above';
        exitBox.innerHTML = `&#128302; Live Preview: ${symbol} (Exit: Custom Underlying SL/TP) | Underlying Price: $${simPrice.toFixed(2)}<br/>${directionText}<br/>Underlying Stop Loss: ${slText} &nbsp;|&nbsp; Underlying Take Profit: ${tpText}`;
        exitBox.classList.remove('hidden');
        return;
    }

    if (showExitSwing) {
        exitBox.innerHTML = `&#128302; Live Preview: ${symbol} (Exit Swing) | Price: $${simPrice.toFixed(2)}<br/>${buildSwingExitPreviewHtml(mode, isOptions, symbol, simPrice)}`;
        exitBox.classList.remove('hidden');
        return;
    }

    if (!showATR && !showPct && !showPivot && !showHL) {
        exitBox.classList.add('hidden');
        exitBox.innerHTML = '';
        return;
    }

    let html = '';
    if (showPivot || showHL) {
        if (!priorDay) {
            html = `&#128302; Live Preview (${symbol}) | Price: $${simPrice.toFixed(2)}<br/>Awaiting Prior Day Data...`;
        } else {
            if (showPivot) {
                const h = priorDay.high, l = priorDay.low, c = priorDay.close;
                const p = (h + l + c) / 3;
                const r1 = (p * 2) - l;
                const s1 = (p * 2) - h;
                const optionsType = document.getElementById('strategyOptionsType')?.value || 'calls_only';
                if (isOptions && optionsType === 'puts_only') {
                    html = `&#128302; Live Preview: ${symbol} (PUT Pivot Exit) | Price: $${simPrice.toFixed(2)}<br/>
                        Floor Pivot: $${p.toFixed(2)}<br/>
                        Stop Loss (R1): $${r1.toFixed(2)} &nbsp;|&nbsp; Take Profit (S1): $${s1.toFixed(2)}`;
                } else {
                    html = `&#128302; Live Preview: ${symbol} (CALL/Long Pivot Exit) | Price: $${simPrice.toFixed(2)}<br/>
                            Floor Pivot: $${p.toFixed(2)}<br/>
                            Stop Loss (S1): $${s1.toFixed(2)} &nbsp;|&nbsp; Take Profit (R1): $${r1.toFixed(2)}`;
                }
            } else if (showHL) {
                const optionsType = document.getElementById('strategyOptionsType')?.value || 'calls_only';
                const levels = getPriorDayHighLowPreviewLevels(priorDay);
                if (!levels) {
                    html = `&#128302; Live Preview (${symbol}) | Price: $${simPrice.toFixed(2)}<br/>Awaiting valid prior day data...`;
                } else if (isOptions && optionsType === 'puts_only') {
                    html = `&#128302; Live Preview: ${symbol} (PUT Prior Day High/Low Exit) | Price: $${simPrice.toFixed(2)}<br/>
                        Stop Loss (High Trigger): $${levels.highTrigger.toFixed(2)} &nbsp;|&nbsp; Take Profit (Low Trigger): $${levels.lowTrigger.toFixed(2)}<br/>
                        Buffer: ${levels.bufferPct.toFixed(2)}% | PDH/PDL: $${levels.high.toFixed(2)} / $${levels.low.toFixed(2)}`;
                } else {
                    html = `&#128302; Live Preview: ${symbol} (CALL/Long Prior Day High/Low Exit) | Price: $${simPrice.toFixed(2)}<br/>
                            Stop Loss (Low Trigger): $${levels.lowTrigger.toFixed(2)} &nbsp;|&nbsp; Take Profit (High Trigger): $${levels.highTrigger.toFixed(2)}<br/>
                            Buffer: ${levels.bufferPct.toFixed(2)}% | PDH/PDL: $${levels.high.toFixed(2)} / $${levels.low.toFixed(2)}`;
                }
            }
        }
    } else {
        let slDist = 0;
        let tpDist = 0;
        let trailDist = 0;
        let hasTrail = false;

        if (showATR) {
            const stopEl = document.getElementById('strategyAtrStopMult' + suffix);
            const tpEl = document.getElementById('strategyAtrTpMult' + suffix);
            const trailEnableEl = document.getElementById('strategyEnableAtrTrail' + suffix);
            const trailMultEl = document.getElementById('strategyAtrTrailMult' + suffix);

            const stopMult = stopEl ? parseFloat(stopEl.value) || 0 : 0;
            const tpMult = tpEl ? parseFloat(tpEl.value) || 0 : 0;
            hasTrail = trailEnableEl ? trailEnableEl.checked : false;
            const trailMult = trailMultEl ? parseFloat(trailMultEl.value) || 0 : 0;

            slDist = stopMult * simAtr;
            tpDist = tpMult * simAtr;
            if (hasTrail) trailDist = trailMult * simAtr;

        } else if (showPct) {
            if (isOptions) {
                simPrice = 5.00; // Mock option premium
                symbol = "Assumed Option Premium";
            }
            let slId = isOptions ? 'strategyOptionsSlPct' : 'strategyRegularSlPct';
            let tpId = isOptions ? 'strategyOptionsTpPct' : 'strategyRegularTpPct';
            let enableTrailId = isOptions ? 'strategyEnablePctTrail_options' : 'strategyEnablePctTrail';
            let trailPctId = isOptions ? 'strategyOptionsTrailPct' : 'strategyRegularTrailPct';

            const stopEl = document.getElementById(slId);
            const tpEl = document.getElementById(tpId);
            const trailEnableEl = document.getElementById(enableTrailId);
            const trailPctEl = document.getElementById(trailPctId);

            const stopPct = stopEl ? parseFloat(stopEl.value) || 0 : 0;
            const tpPct = tpEl ? parseFloat(tpEl.value) || 0 : 0;
            hasTrail = trailEnableEl ? trailEnableEl.checked : false;
            const trailPct = trailPctEl ? parseFloat(trailPctEl.value) || 0 : 0;

            slDist = simPrice * (stopPct / 100);
            tpDist = simPrice * (tpPct / 100);
            if (hasTrail) trailDist = simPrice * (trailPct / 100);
        }

        const optionsType = document.getElementById('strategyOptionsType')?.value || 'calls_only';
        const isPutUnderlyingAtr = isOptions && showATR && optionsType === 'puts_only';
        const slPrice = isPutUnderlyingAtr ? simPrice + slDist : simPrice - slDist;
        const tpPrice = isPutUnderlyingAtr ? simPrice - tpDist : simPrice + tpDist;

        const slPctCalc = simPrice > 0 ? ((slDist / simPrice) * 100).toFixed(2) : '0.00';
        const tpPctCalc = simPrice > 0 ? ((tpDist / simPrice) * 100).toFixed(2) : '0.00';

        let label = isLive
            ? `&#128302; Live Preview: ${symbol} (${tf} Timeframe) &nbsp;|&nbsp; Price: $${simPrice.toFixed(2)}${showATR ? ` &nbsp;|&nbsp; ATR: $${simAtr.toFixed(2)}` : ''}`
            : `&#128302; Example Preview (Assuming $100 Entry${showATR ? ' & $2 ATR' : ''})`;

        const directionLabel = isPutUnderlyingAtr ? 'PUT ATR Exit: stop/trail above, target below' : isOptions && showATR ? 'CALL ATR Exit: stop/trail below, target above' : '';
        const slSign = isPutUnderlyingAtr ? '+' : '-';
        const tpSign = isPutUnderlyingAtr ? '-' : '+';
        html = `${label}${directionLabel ? `<br/>${directionLabel}` : ''} <br/> Stop Loss: $${slPrice.toFixed(2)} (${slSign}${slPctCalc}%) &nbsp;|&nbsp; Take Profit: $${tpPrice.toFixed(2)} (${tpSign}${tpPctCalc}%)`;

        if (hasTrail) {
            if (showATR) {
                const trailNote = isPutUnderlyingAtr ? 'PUT trail follows downward move from above' : 'Trail follows favorable move from below';
                html += `<br/>Trail Distance: $${trailDist.toFixed(2)} (ATR * ${document.getElementById('strategyAtrTrailMult' + suffix)?.value || '0'}) | ${trailNote}`;
            } else {
                html += `<br/>Trail Distance: $${trailDist.toFixed(2)} (${document.getElementById(isOptions ? 'strategyOptionsTrailPct' : 'strategyRegularTrailPct')?.value || '0'}%)`;
            }
        }
    }

    exitBox.innerHTML = html;
    exitBox.classList.remove('hidden');
};

const oldOpenStrategyModalUnified = window.openStrategyModal;
if (oldOpenStrategyModalUnified && !window._livePreviewPatchedUnified) {
    window._livePreviewPatchedUnified = true;
    window.openStrategyModal = async function (symbol, ...args) {
        window.currentAtrPreviewData = { price: 100, atr: 2.0, symbol: symbol, isLive: false };
        await oldOpenStrategyModalUnified(symbol, ...args);
        if (symbol) window.fetchLiveAtrForPreview(symbol);

        if (window.updateLivePreview_bots) {
            window.updateLivePreview_bots('regular', 'both');
            window.updateLivePreview_bots('options', 'both');
        }
    };
}


window.toggleRiskControls = function (enabled, mode) {
    const wrapperId = mode === 'regular' ? 'riskControlsContentWrapper_regular' : 'riskControlsContentWrapper_options';
    const wrapper = document.getElementById(wrapperId);
    if (wrapper) {
        if (enabled) {
            wrapper.style.display = 'block';
        } else {
            wrapper.style.display = 'none';
        }
    }

    // Disable inputs
    const contentId = mode === 'regular' ? 'riskControlsContent' : 'riskControlsContent_options';
    const content = document.getElementById(contentId);
    if (content) {
        const inputs = content.querySelectorAll('input');
        inputs.forEach(input => {
            input.disabled = !enabled;
        });
    }
};


// ──────────────────────────────────────────────
// Global Risk Settings Auto-Save
// ──────────────────────────────────────────────
function formatGlobalRiskMoney(value) {
    const number = Number(value || 0);
    return Number.isFinite(number)
        ? number.toLocaleString(undefined, { style: 'currency', currency: 'USD', maximumFractionDigits: 2 })
        : '—';
}

function renderGlobalRiskCard(status = {}) {
    const card = document.getElementById('globalRiskCard');
    const badge = document.getElementById('globalRiskStatusBadge');
    const summary = document.getElementById('globalRiskSummary');
    if (!card || !badge || !summary) return;

    const halted = Boolean(status.halted);
    card.classList.toggle('ring-2', halted);
    card.classList.toggle('ring-rose-400/70', halted);
    card.classList.toggle('bg-rose-50/40', halted);
    badge.textContent = halted ? 'All Halted' : 'Active';
    badge.className = halted
        ? 'rounded-md border border-rose-200 bg-rose-50 px-1.5 py-0.5 text-[0.48rem] font-black uppercase tracking-wider text-rose-700'
        : 'rounded-md border border-emerald-200 bg-emerald-50 px-1.5 py-0.5 text-[0.48rem] font-black uppercase tracking-wider text-emerald-700';
    summary.textContent = halted
        ? (status.halt_reason || 'All new bot entries are halted. Click to review and reactivate.')
        : `Daily ${Number(status.daily_drawdown_pct || 0).toFixed(2)}% • Equity ${Number(status.equity_drawdown_pct || 0).toFixed(2)}%`;
    summary.className = halted
        ? 'truncate text-[0.5rem] font-black text-rose-600'
        : 'truncate text-[0.5rem] font-bold text-emerald-600';
    card.setAttribute('aria-label', halted
        ? `Global risk halted. ${status.halt_reason || ''} Open details.`
        : 'Global risk active. Open details.');
}

function closeGlobalRiskModal() {
    document.getElementById('globalRiskModal')?.remove();
}

window.openGlobalRiskModal = function () {
    closeGlobalRiskModal();
    const status = window.lastBotsData?.global_risk_status || {};
    const halted = Boolean(status.halted);
    const modal = document.createElement('div');
    modal.id = 'globalRiskModal';
    modal.className = 'fixed inset-0 z-[10000] flex items-center justify-center bg-slate-950/55 p-2.5 backdrop-blur-sm sm:p-4';
    modal.innerHTML = `
        <div role="dialog" aria-modal="true" aria-labelledby="globalRiskModalTitle"
            class="max-h-[86dvh] w-[90vw] max-w-xl overflow-y-auto rounded-2xl border border-white/60 bg-white/95 p-3.5 shadow-2xl sm:w-full sm:rounded-3xl sm:p-6">
            <div class="flex items-start justify-between gap-2.5 sm:gap-4">
                <div class="flex items-start gap-2.5 sm:gap-3">
                    <div data-role="icon" class="flex h-9 w-9 shrink-0 items-center justify-center rounded-xl text-base sm:h-11 sm:w-11 sm:rounded-2xl sm:text-xl"></div>
                    <div>
                        <h2 id="globalRiskModalTitle" class="text-sm font-black leading-tight text-slate-900 sm:text-lg"></h2>
                        <p data-role="reason" class="mt-1 text-[0.7rem] font-semibold leading-snug text-slate-600 sm:mt-1.5 sm:text-sm sm:leading-relaxed"></p>
                        <p data-role="current" class="mt-0.5 text-[0.6rem] font-bold text-slate-400 sm:mt-1 sm:text-[0.68rem]"></p>
                    </div>
                </div>
                <button type="button" data-action="close" aria-label="Close global risk details"
                    class="rounded-lg border border-slate-200 px-2 py-1 text-xs font-black text-slate-500 hover:bg-slate-50 sm:rounded-xl sm:px-2.5 sm:py-1.5 sm:text-sm">✕</button>
            </div>
            <div class="mt-3 grid grid-cols-2 gap-2 sm:mt-5 sm:gap-3">
                <div class="rounded-xl border border-indigo-100 bg-indigo-50/60 p-2 sm:rounded-2xl sm:p-3">
                    <p class="text-[0.5rem] font-black uppercase tracking-wider text-indigo-500 sm:text-[0.62rem] sm:tracking-widest">Daily Drawdown</p>
                    <p data-role="daily" class="mt-0.5 text-sm font-black text-indigo-950 sm:mt-1 sm:text-base"></p>
                    <p data-role="dailyBase" class="mt-0.5 text-[0.56rem] font-semibold leading-tight text-indigo-500 sm:text-[0.68rem]"></p>
                </div>
                <div class="rounded-xl border border-purple-100 bg-purple-50/60 p-2 sm:rounded-2xl sm:p-3">
                    <p class="text-[0.5rem] font-black uppercase tracking-wider text-purple-500 sm:text-[0.62rem] sm:tracking-widest">Loss From Equity High</p>
                    <p data-role="equity" class="mt-0.5 text-sm font-black text-purple-950 sm:mt-1 sm:text-base"></p>
                    <p data-role="equityBase" class="mt-0.5 text-[0.56rem] font-semibold leading-tight text-purple-500 sm:text-[0.68rem]"></p>
                </div>
            </div>
            <div class="mt-2.5 rounded-xl border border-amber-200 bg-amber-50/70 p-2 text-[0.65rem] font-semibold leading-snug text-amber-900 sm:mt-4 sm:rounded-2xl sm:p-3 sm:text-xs sm:leading-relaxed">
                <strong>Safety behavior:</strong> A global halt blocks all new stock, crypto, and options entries and cancels pending entry orders. Existing positions and protective exits continue to be managed. Open positions are not automatically liquidated.
            </div>
            <div data-role="haltNotice" class="mt-2 hidden rounded-xl border border-rose-200 bg-rose-50/80 p-2 text-[0.65rem] font-bold leading-snug text-rose-800 sm:mt-3 sm:rounded-2xl sm:p-3 sm:text-xs sm:leading-relaxed">
                Review every bot’s strategy, recent losses, allocation, thresholds, and exit protection before reactivating. Reactivation establishes new daily and equity high-water baselines.
            </div>
            <div class="mt-3 flex gap-2 sm:mt-5 sm:justify-end">
                <button type="button" data-action="review"
                    class="flex-1 rounded-lg border border-indigo-200 bg-indigo-50 px-2.5 py-2 text-[0.62rem] font-black uppercase tracking-wide text-indigo-700 hover:bg-indigo-100 sm:flex-none sm:rounded-xl sm:px-4 sm:py-2.5 sm:text-xs sm:tracking-wider">Review Bots</button>
                <button type="button" data-action="reactivate"
                    class="hidden flex-1 rounded-lg bg-rose-600 px-2.5 py-2 text-[0.62rem] font-black uppercase tracking-wide text-white shadow-lg shadow-rose-500/20 hover:bg-rose-700 sm:flex-none sm:rounded-xl sm:px-4 sm:py-2.5 sm:text-xs sm:tracking-wider">Reactivate All Eligible Bots</button>
            </div>
        </div>`;

    modal.querySelector('[data-role="icon"]').textContent = halted ? '🛑' : '🛡️';
    modal.querySelector('[data-role="icon"]').classList.add(halted ? 'bg-rose-100' : 'bg-emerald-100');
    modal.querySelector('#globalRiskModalTitle').textContent = halted ? 'All bots halted by global risk' : 'Global risk is active';
    modal.querySelector('[data-role="reason"]').textContent = halted
        ? (status.halt_reason || 'A global risk threshold was reached. Manual review and reactivation are required.')
        : 'All eligible bots may evaluate new entries. The account-level limits below are monitored continuously.';
    const haltedTime = status.halted_at ? new Date(status.halted_at).toLocaleString() : '';
    modal.querySelector('[data-role="current"]').textContent = halted
        ? `Equity at review: ${formatGlobalRiskMoney(status.current_equity)}${haltedTime ? ` • Halted ${haltedTime}` : ''}`
        : `Current equity: ${formatGlobalRiskMoney(status.current_equity)}`;
    modal.querySelector('[data-role="daily"]').textContent = `${Number(status.daily_drawdown_pct || 0).toFixed(2)}% / ${status.max_daily_drawdown_pct == null ? 'Off' : `${Number(status.max_daily_drawdown_pct).toFixed(2)}%`}`;
    modal.querySelector('[data-role="dailyBase"]').textContent = `Start-of-day equity: ${formatGlobalRiskMoney(status.daily_starting_equity)}`;
    modal.querySelector('[data-role="equity"]').textContent = `${Number(status.equity_drawdown_pct || 0).toFixed(2)}% / ${status.max_loss_to_equity_pct == null ? 'Off' : `${Number(status.max_loss_to_equity_pct).toFixed(2)}%`}`;
    modal.querySelector('[data-role="equityBase"]').textContent = `High-water equity: ${formatGlobalRiskMoney(status.equity_high_watermark)}`;
    modal.querySelector('[data-role="haltNotice"]').classList.toggle('hidden', !halted);
    modal.querySelector('[data-action="reactivate"]').classList.toggle('hidden', !halted);

    modal.querySelector('[data-action="close"]').addEventListener('click', closeGlobalRiskModal);
    modal.querySelector('[data-action="review"]').addEventListener('click', () => {
        closeGlobalRiskModal();
        setBotTab('all');
        document.getElementById('tradelistContainer')?.scrollIntoView({ behavior: 'smooth', block: 'start' });
    });
    modal.querySelector('[data-action="reactivate"]').addEventListener('click', reactivateGlobalRisk);
    modal.addEventListener('click', event => {
        if (event.target === modal) closeGlobalRiskModal();
    });
    document.body.appendChild(modal);
    modal.querySelector(halted ? '[data-action="reactivate"]' : '[data-action="close"]')?.focus();
};

async function reactivateGlobalRisk(event) {
    const button = event?.currentTarget;
    if (!confirm('Reactivate all eligible bots?\n\nReview each bot first. This clears the global halt and starts new daily and equity high-water risk baselines. Individually paused bots will remain paused.')) return;
    if (button) {
        button.disabled = true;
        button.textContent = 'Reactivating...';
    }
    try {
        const headers = await getAuthHeaders();
        const response = await fetch(`${API_BASE}/api/settings/risk/reactivate`, {
            method: 'POST',
            headers
        });
        const data = await response.json().catch(() => ({}));
        if (!response.ok || data.status !== 'success') throw new Error(data.detail || data.message || 'Global risk reactivation failed');
        closeGlobalRiskModal();
        await fetchBotsData({ force: true });
        const remaining = Number(data.individually_paused_bots || 0);
        alert(remaining > 0
            ? `Global risk halt cleared. ${remaining} bot${remaining === 1 ? '' : 's'} remain individually paused and must be reviewed separately.`
            : 'Global risk halt cleared. All eligible bots can evaluate new entries again.');
    } catch (error) {
        alert(`Could not reactivate global risk: ${error.message}`);
        if (button) {
            button.disabled = false;
            button.textContent = 'Reactivate All Eligible Bots';
        }
    }
}

async function saveGlobalRiskSettings() {
    const maxDrawdownEl = document.getElementById('globalMaxDrawdown');
    const hardStopEl = document.getElementById('globalHardStop');

    if (!maxDrawdownEl || !hardStopEl) return;

    const payload = {
        MAX_DAILY_DRAWDOWN: maxDrawdownEl.value === '' ? null : parseFloat(maxDrawdownEl.value) / 100,
        GLOBAL_HARD_STOP_PCT: hardStopEl.value === '' ? null : parseFloat(hardStopEl.value) / 100
    };

    try {
        const headers = await getAuthHeaders();
        const response = await fetch(`${API_BASE}/api/settings/risk?context=bots`, {
            method: 'POST',
            headers: headers,
            body: JSON.stringify(payload)
        });
        if (!response.ok) throw new Error('Failed to save risk settings');
        console.log('[bots] Global Risk Settings Auto-saved');
        await fetchBotsData({ force: true });
    } catch (e) {
        console.error('[bots] Error saving global risk settings:', e);
    }
}

document.addEventListener('DOMContentLoaded', () => {
    window.AlpacaStatusCache?.render();
    ensureDailyHighLowBotIndicatorCards();
    const dd = document.getElementById('globalMaxDrawdown');
    const hs = document.getElementById('globalHardStop');
    if (dd) dd.addEventListener('change', saveGlobalRiskSettings);
    if (hs) hs.addEventListener('change', saveGlobalRiskSettings);
});
