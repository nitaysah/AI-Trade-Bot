// ═══════════════════════════════════════════════════
// AI Trading Bot — Active Bots Page JS
// Dedicated management console for active trading bots
// ═══════════════════════════════════════════════════

// Production API Configuration (Auto-switch between Local and Cloud)
const CLOUD_URL = 'https://ai-trade-bot-backend-946557219642.us-central1.run.app';
const API_BASE = window.location.hostname === 'localhost' || window.location.hostname === '127.0.0.1'
    ? 'http://localhost:8000'
    : CLOUD_URL;
const REFRESH_INTERVAL = 60000; // 60 seconds (1 minute)

let selectedTicker = null;
let currentBackendTf = "5Min";
let isAlpacaLinked = false;
let currentLogTab = "all";

// ──────────────────────────────────────────────
// Auth Headers
// ──────────────────────────────────────────────
async function getAuthHeaders() {
    const auth = window.auth;
    if (!auth) return { 'Content-Type': 'application/json' };
    const user = auth.currentUser;
    if (!user) return { 'Content-Type': 'application/json' };

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
        return date.toLocaleTimeString('en-US', {
            timeZone: 'America/Chicago',
            hour: '2-digit', minute: '2-digit', second: '2-digit', hour12: false
        });
    } catch {
        return isoString;
    }
}

// ──────────────────────────────────────────────
// Render Active Bots List
// ──────────────────────────────────────────────
let currentBotTab = 'all';

function isCrypto(ticker) {
    if (!ticker) return false;
    return ticker.includes('USD') || ['BTC', 'ETH', 'LTC', 'SOL', 'DOGE', 'ADA', 'DOT', 'SHIB', 'AVAX'].some(c => ticker.startsWith(c));
}

function setBotTab(tab) {
    currentBotTab = tab;
    // Update button styles
    const allBtn = document.getElementById('tabBotsAll');
    const stocksBtn = document.getElementById('tabBotsStocks');
    const cryptoBtn = document.getElementById('tabBotsCrypto');
    
    [allBtn, stocksBtn, cryptoBtn].forEach(btn => {
        if (btn) btn.className = "px-3 py-1.5 text-[0.6rem] font-black uppercase rounded-lg transition-all text-indigo-400 hover:text-indigo-600";
    });
    
    const activeBtn = document.getElementById(`tabBots${tab.charAt(0).toUpperCase() + tab.slice(1)}`);
    if (activeBtn) {
        activeBtn.className = "px-3 py-1.5 text-[0.6rem] font-black uppercase rounded-lg transition-all bg-white text-indigo-600 shadow-sm border border-indigo-100";
    }
    
    if (window.lastBotsData) {
        renderTradelist(window.lastBotsData.scans, window.lastBotsData.tradelist, window.lastBotsData.tickerAmounts);
    }
}

function renderTradelist(scans, tradelist, tickerAmounts = {}) {
    const container = document.getElementById('tradelistContainer');
    if (!container) return;
    container.innerHTML = '';

    // Update active bot count card with breakdown
    const countEl = document.getElementById('activeBotCount');
    const subEl = document.getElementById('botStatusSub');
    
    const totalCount = (tradelist || []).length;
    const pausedCount = tradelist ? tradelist.filter(t => (window.lastBotsData?.ticker_settings || {})[t]?.paused).length : 0;
    const runningCount = totalCount - pausedCount;

    if (countEl) countEl.textContent = totalCount;
    if (subEl) {
        if (totalCount > 0) {
            subEl.innerHTML = `<span class="text-emerald-500">${runningCount} Active</span> • <span class="text-amber-500">${pausedCount} Paused</span>`;
        } else {
            subEl.textContent = 'No bots running';
        }
    }

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

    filteredTradelist.forEach(ticker => {
        const scan = (scans || {})[ticker];
        const item = document.createElement('div');
        item.className = `watchlist-item group active-bot ${selectedTicker === ticker ? 'active' : ''} p-3 mb-2 flex flex-col gap-2`;

        const action = scan?.action || '—';
        const price = scan?.price ? `$${parseFloat(scan.price.toString().replace('$', '')).toFixed(2)}` : '---';
        const actionColor = action === 'BUY' ? 'bg-emerald-50 text-emerald-600 border-emerald-100' : action === 'SELL' ? 'bg-red-50 text-red-600 border-red-100' : 'bg-slate-50 text-slate-400 border-slate-100';

        const bullish = scan?.bullish_count ?? 0;
        const bearish = scan?.bearish_count ?? 0;
        const tickerTf = (window.lastBotsData?.ticker_settings || {})[ticker]?.timeframe || '';
        const tfLabel = tickerTf || (window.lastBotsData?.strategyTimeframe || '5Min');
        const isPaused = (window.lastBotsData?.ticker_settings || {})[ticker]?.paused || false;
        const dotClass = isPaused
            ? 'h-2 w-2 bg-slate-300 rounded-full'
            : 'h-2 w-2 bg-emerald-500 rounded-full animate-pulse shadow-[0_0_8px_rgba(16,185,129,0.8)]';

        item.innerHTML = `
                <!-- Top Layer: Ticker & Status -->
                <div class="flex items-center justify-between">
                    <div class="flex items-center gap-2 cursor-pointer" onclick="selectTicker('${ticker}')">
                        <div class="${dotClass}"></div>
                        <span class="font-black text-sm ${isPaused ? 'text-slate-400' : 'text-indigo-950'} tracking-tight">${ticker}</span>
                        <span class="text-[0.5rem] font-bold px-1.5 py-0.5 rounded bg-indigo-50 text-indigo-400 border border-indigo-100">${tfLabel}</span>
                        ${isPaused ? '<span class="text-[0.5rem] font-bold px-1.5 py-0.5 rounded bg-amber-50 text-amber-500 border border-amber-200">PAUSED</span>' : '<span class="text-[0.5rem] font-bold px-1.5 py-0.5 rounded bg-emerald-50 text-emerald-600 border border-emerald-100">ACTIVE</span>'}
                    </div>
                    
                    <div class="flex items-center gap-1.5">
                        <span class="text-[0.6rem] font-bold text-emerald-600 bg-emerald-50 px-1 rounded border border-emerald-100">${bullish}B</span>
                        <span class="text-[0.6rem] font-bold text-red-600 bg-red-50 px-1 rounded border border-red-100">${bearish}S</span>
                    </div>

                    <div class="flex items-center gap-1 opacity-60 group-hover:opacity-100 transition-opacity">
                        <button class="p-1.5 rounded hover:bg-indigo-50 text-indigo-400 transition-all" 
                            onclick="event.stopPropagation(); openStrategyModal('${ticker}', 'edit')">
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

function selectTicker(ticker) {
    selectedTicker = ticker;
    fetchBotsData();
}

async function removeFromTradelist(ticker) {
    try {
        const headers = await getAuthHeaders();
        const response = await fetch(`${API_BASE}/api/tradelist/${ticker}`, {
            method: 'DELETE',
            headers: headers
        });
        if (response.ok) fetchBotsData();
    } catch (e) {
        console.error('Error removing from tradelist:', e);
    }
}

// ──────────────────────────────────────────────
// Render Trade Log
// ──────────────────────────────────────────────
let latestScanHistory = [];
let latestExecutedTrades = [];
let currentScanPage = 1;
let currentTradePage = 1;
const LOG_PAGE_SIZE = 20;

function setLogTab(tab) {
    currentLogTab = tab;
    if (tab === 'all') currentScanPage = 1;
    else currentTradePage = 1;

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

    renderTradeLog(latestScanHistory, latestExecutedTrades);
}

function renderTradeLog(scanHistory, executedTrades) {
    const tbody = document.getElementById('tradeLogBody');
    const noMsg = document.getElementById('noTradesMsg');
    if (!tbody) return;

    tbody.innerHTML = '';

    let trades = currentLogTab === 'trades' ? (executedTrades || []) : (scanHistory || []);
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
        const total = Math.ceil(latestScanHistory.length / LOG_PAGE_SIZE);
        currentScanPage = Math.max(1, Math.min(total, currentScanPage + delta));
    } else {
        const total = Math.ceil(latestExecutedTrades.length / LOG_PAGE_SIZE);
        currentTradePage = Math.max(1, Math.min(total, currentTradePage + delta));
    }
    renderTradeLog(latestScanHistory, latestExecutedTrades);
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
let strategyModalMode = 'deploy'; // 'deploy' or 'edit'

window.openStrategyModal = function (symbol, mode = 'deploy', name = '') {
    currentStrategySymbol = symbol.toUpperCase();
    strategyModalMode = mode;
    
    const modal = document.getElementById('botStrategyModal');
    const titleEl = document.getElementById('strategyModalTitle');
    const subtitleEl = document.getElementById('strategyModalSubtitle');
    const actionBtn = document.getElementById('strategyActionButton');
    
    if (!modal) return;

    // UI State based on Mode
    if (mode === 'deploy') {
        titleEl.textContent = `Deploy ${currentStrategySymbol}`;
        subtitleEl.textContent = name ? `Configure parameters for ${name}` : 'Set initial strategy parameters';
        actionBtn.textContent = 'Deploy Strategy 🚀';
        actionBtn.className = "w-full bg-gradient-to-r from-emerald-500 to-teal-500 text-white font-black py-4 rounded-2xl hover:shadow-lg hover:shadow-emerald-500/30 transition-all active:scale-[0.98] uppercase tracking-[0.2em] shadow-md flex items-center justify-center gap-2";
        
        // Defaults for deployment
        document.getElementById('strategyCapital').value = 100;
        document.getElementById('strategyThreshold').value = 4;
        document.getElementById('strategyThresholdLabel').textContent = "4 Signals Required";
        document.getElementById('strategyTimeframe').value = "4Hour";
        document.getElementById('strategySellMode').value = "indicator";
        window._strategyPaused = false;
        
        // Reset Indicators
        document.querySelectorAll('.strategy-indicator-check').forEach(chk => chk.checked = true);
    } else {
        titleEl.textContent = `${currentStrategySymbol} Settings`;
        subtitleEl.textContent = 'Adjust active bot strategy parameters';
        actionBtn.textContent = 'Save Settings 💾';
        actionBtn.className = "w-full bg-gradient-to-r from-indigo-600 to-purple-600 text-white font-black py-4 rounded-2xl hover:shadow-lg hover:shadow-indigo-500/30 transition-all active:scale-[0.98] uppercase tracking-[0.2em] shadow-md flex items-center justify-center gap-2";
        
        // Load existing settings
        const settings = (window.lastBotsData?.ticker_settings || {})[currentStrategySymbol] || {};
        document.getElementById('strategyCapital').value = settings.amount || '';
        const thresh = settings.min_buy_signals || 4;
        document.getElementById('strategyThreshold').value = thresh;
        document.getElementById('strategyThresholdLabel').textContent = `${thresh} Signals Required`;
        document.getElementById('strategyTimeframe').value = settings.timeframe || '4Hour';
        document.getElementById('strategySellMode').value = settings.sell_mode || 'indicator';
        window._strategyPaused = settings.paused || false;
        
        const enabledIndicators = settings.indicators || ['RSI', 'MACD', 'EMA Cross', 'Supertrend', 'Bollinger', 'VWAP', 'Mystic Pulse', 'Candle Patterns'];
        document.querySelectorAll('.strategy-indicator-check').forEach(chk => {
            chk.checked = enabledIndicators.includes(chk.value);
        });
    }

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
            "symbol": currentStrategySymbol.includes('USD') && !currentStrategySymbol.includes('^') ? `CRYPTO:${currentStrategySymbol}` : currentStrategySymbol,
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
    currentStrategySymbol = null;
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
        btn.className = 'px-6 py-2 rounded-xl font-black text-xs uppercase tracking-wider transition-all bg-amber-100 text-amber-700 border border-amber-300';
    } else {
        btn.textContent = '▶ Active';
        btn.className = 'px-6 py-2 rounded-xl font-black text-xs uppercase tracking-wider transition-all bg-emerald-100 text-emerald-700 border border-emerald-300';
    }
}

window.handleStrategyAction = async function() {
    if (strategyModalMode === 'deploy') {
        await confirmAndDeployBot();
    } else {
        await saveTickerSettings();
    }
};

async function saveTickerSettings() {
    if (!currentStrategySymbol) return;

    const thresholdVal = parseInt(document.getElementById('strategyThreshold').value);
    const indicators = [];
    document.querySelectorAll('.strategy-indicator-check:checked').forEach(check => {
        indicators.push(check.value);
    });

    const data = {
        ticker: currentStrategySymbol,
        settings: {
            amount: parseFloat(document.getElementById('strategyCapital').value) || null,
            timeframe: document.getElementById('strategyTimeframe').value || null,
            min_buy_signals: thresholdVal,
            min_sell_signals: thresholdVal,
            sell_mode: document.getElementById('strategySellMode').value,
            indicators: indicators,
            paused: window._strategyPaused || false
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
            closeStrategyModal();
            fetchBotsData();
        }
    } catch (e) {
        console.error('Error saving ticker settings:', e);
    }
}

window.resetTickerSettings = async function() {
    if (!currentStrategySymbol) return;
    if (!confirm(`Reset ${currentStrategySymbol} to global defaults?`)) return;

    try {
        const headers = await getAuthHeaders();
        await fetch(`${API_BASE}/api/settings/ticker/${currentStrategySymbol}`, {
            method: 'DELETE',
            headers: headers
        });
        closeStrategyModal();
        fetchBotsData();
    } catch (e) {
        console.error('Error resetting ticker settings:', e);
    }
};

// ──────────────────────────────────────────────
// Main Fetch Loop
// ──────────────────────────────────────────────
async function fetchBotsData() {
    try {
        const headers = await getAuthHeaders();
        const url = `${API_BASE}/api/dashboard`;
        const response = await fetch(url, { headers });
        const data = await response.json();

        // Cache for modal access
        window.lastBotsData = data;
        window.lastDashboardData = data;

        // Connection status
        isAlpacaLinked = !data.simulation && data.has_keys;
        updateAlpacaStatus(isAlpacaLinked);

        // Portfolio cards
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
            plEl.textContent = data.dailyPL || '$0.00 (0.0%)';
            plEl.classList.remove('animate-pulse');
            const plVal = parseFloat((data.dailyPL || '').replace(/[^-\d.]/g, ''));
            plEl.className = `card-value ${plVal >= 0 ? 'text-emerald-600' : 'text-red-500'}`;
        }
        if (document.getElementById('totalProfit')) {
            const tpEl = document.getElementById('totalProfit');
            tpEl.textContent = data.totalProfit || '$0.00 (0.0%)';
            tpEl.classList.remove('animate-pulse');
            const tpVal = parseFloat((data.totalProfit || '').replace(/[^-\d.]/g, ''));
            tpEl.className = `card-value ${tpVal >= 0 ? 'text-emerald-600' : 'text-red-500'}`;
        }

        // Last scan time

        // Strategy timeframe sync
        if (data.strategyTimeframe) {
            currentBackendTf = data.strategyTimeframe;
        }

        // Active bots list
        renderTradelist(data.watchlistScans, data.tradelist, data.tickerAmounts);

        // Auto-select first active bot if none selected
        if (!selectedTicker && data.tradelist && data.tradelist.length > 0) {
            selectedTicker = data.tradelist[0];
        }

        // AI Summary
        if (document.getElementById('aiSummary') && data.sentiment_summary) {
            document.getElementById('aiSummary').textContent = data.sentiment_summary;
        }

        // Execution log
        latestScanHistory = data.recentTrades || [];
        latestExecutedTrades = data.executedTrades || [];
        renderTradeLog(latestScanHistory, latestExecutedTrades);

    } catch (error) {
        console.error('[bots] Fetch error:', error);
    }
}

function updateAlpacaStatus(isLinked) {
    const statusEl = document.getElementById('alpacaLinkStatus');
    const dotEl = document.getElementById('alpacaStatusDot');
    const textEl = document.getElementById('alpacaStatusText');
    const btnUnlink = document.getElementById('btnUnlinkAlpaca');

    if (!statusEl || !dotEl || !textEl) return;

    if (isLinked) {
        statusEl.className = 'flex items-center text-sm font-black px-8 py-3 rounded-full border-2 shadow-sm whitespace-nowrap transition-all duration-500 uppercase tracking-wider bg-emerald-50 text-emerald-700 border-emerald-200';
        dotEl.className = 'h-2.5 w-2.5 rounded-full mr-2.5 bg-emerald-500 animate-pulse shadow-[0_0_8px_rgba(16,185,129,0.8)]';
        textEl.textContent = 'LIVE';
        if (btnUnlink) btnUnlink.classList.remove('hidden');
    } else {
        statusEl.className = 'flex items-center text-sm font-black px-8 py-3 rounded-full border-2 shadow-sm whitespace-nowrap transition-all duration-500 uppercase tracking-wider bg-amber-50 text-amber-700 border-amber-200';
        dotEl.className = 'h-2.5 w-2.5 rounded-full mr-2.5 bg-amber-500 animate-pulse';
        textEl.textContent = 'SIMULATION';
        if (btnUnlink) btnUnlink.classList.add('hidden');
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
    'VWAP': { key: 'ENABLE_VWAP', desc: 'Volume Weighted Avg', icon: '⚖️' },
    'Mystic Pulse': { key: 'ENABLE_MYSTIC_PULSE', desc: 'Trend Persistence', icon: '🔮' },
    'Candle Patterns': { key: 'ENABLE_CANDLE_PATTERNS', desc: 'Reversal Detection', icon: '🕯️' },
    'News Sentiment': { key: 'ENABLE_AI_SENTIMENT', desc: 'AI News Analysis', icon: '🧠' },
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

    // Gather Settings
    const capital = document.getElementById('strategyCapital').value;
    const threshold = document.getElementById('strategyThreshold').value;
    const sellMode = document.getElementById('strategySellMode').value;
    const timeframe = document.getElementById('strategyTimeframe').value;

    // Gather Indicators
    const indicators = [];
    document.querySelectorAll('.strategy-indicator-check').forEach(chk => {
        if (chk.checked) indicators.push(chk.value);
    });

    // Optimistic UI Update
    const symbol = currentStrategySymbol;
    const container = document.getElementById('tradelistContainer');

    // Close Modal
    closeStrategyModal();

    // Remove "No active bots" text if present
    if (container.querySelector('p')) {
        container.innerHTML = '';
    }

    // Insert optimistic card
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
    container.insertBefore(tempDiv, container.firstChild);

    try {
        const headers = await getAuthHeaders();
        const response = await fetch(`${API_BASE}/api/bots/create`, {
            method: 'POST',
            headers: headers,
            body: JSON.stringify({
                symbol,
                capital: parseFloat(capital),
                threshold: parseInt(threshold),
                sell_mode: sellMode,
                indicators: indicators,
                timeframe: timeframe,
                paused: window._strategyPaused || false
            })
        });

        if (response.ok) {
            // Force an immediate refresh to sync real data
            await fetchBotsData();
        } else {
            // Revert on error
            tempDiv.remove();
            alert("Failed to launch bot. Please try again.");
        }
    } catch (e) {
        console.error('[launch] Error:', e);
        tempDiv.remove();
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
    // Wait for Firebase auth to be ready before first fetch
    const waitForAuth = setInterval(() => {
        if (window.auth) {
            clearInterval(waitForAuth);
            fetchBotsData();
            setInterval(fetchBotsData, REFRESH_INTERVAL);
        }
    }, 200);
})();

// ──────────────────────────────────────────────
// Indicator Settings Logic
// ──────────────────────────────────────────────
const INDICATOR_CONFIG_MAP = {
    'RSI': ['RSI_PERIOD', 'RSI_OVERBOUGHT', 'RSI_OVERSOLD'],
    'MACD': ['MACD_FAST', 'MACD_SLOW', 'MACD_SIGNAL'],
    'EMA Cross': ['EMA_FAST', 'EMA_SLOW'],
    'Supertrend': ['SUPERTREND_PERIOD', 'SUPERTREND_MULTIPLIER'],
    'Bollinger': ['BOLL_PERIOD', 'BOLL_STD_DEV'],
    'Mystic Pulse': ['MYSTIC_PULSE_THRESHOLD'],
    'ATR Volatility': ['ATR_PERIOD', 'ATR_STOP_MULTIPLIER', 'ATR_TRAIL_MULTIPLIER', 'ATR_TAKE_PROFIT_MULTIPLIER'],
    'Strategy Confidence': ['MIN_BULLISH_SIGNALS', 'MIN_BEARISH_SIGNALS'],
    'Sentiment AI': ['SENTIMENT_BULLISH_THRESHOLD', 'SENTIMENT_BEARISH_THRESHOLD']
};

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

function openIndicatorSettings(indicatorName) {
    currentEditingIndicator = indicatorName;
    const configKeys = INDICATOR_CONFIG_MAP[indicatorName] || [];
    const container = document.getElementById('indicatorModalContent');
    const title = document.getElementById('indicatorModalTitle');

    title.textContent = `${indicatorName} Settings`;
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
            if (!window.lastDashboardData) {
                window.lastDashboardData = { indicator_parameters: {} };
            }
            if (!window.lastDashboardData.indicator_parameters) {
                window.lastDashboardData.indicator_parameters = {};
            }
            Object.assign(window.lastDashboardData.indicator_parameters, updates);
            closeIndicatorSettings();
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
    if (checkbox) {
        checkbox.checked = !checkbox.checked;
        checkbox.dispatchEvent(new Event('change', { bubbles: true }));
    }
}
