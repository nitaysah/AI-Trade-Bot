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
function renderTradelist(scans, tradelist, tickerAmounts = {}) {
    const container = document.getElementById('tradelistContainer');
    if (!container) return;
    container.innerHTML = '';

    // Update active bot count card
    const countEl = document.getElementById('activeBotCount');
    const subEl = document.getElementById('botStatusSub');
    if (countEl) countEl.textContent = (tradelist || []).length;
    if (subEl) subEl.textContent = tradelist && tradelist.length > 0 ? `${tradelist.length} bot(s) running` : 'No bots running';

    if (!tradelist || tradelist.length === 0) {
        container.innerHTML = '<p class="text-center text-purple-400 text-[0.65rem] py-4">No active bots. Add from watchlist on the Dashboard.</p>';
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

        item.innerHTML = `
                <!-- Top Layer: Ticker & Status -->
                <div class="flex items-center justify-between">
                    <div class="flex items-center gap-2 cursor-pointer" onclick="selectTicker('${ticker}')">
                        <div class="h-2 w-2 bg-emerald-500 rounded-full animate-pulse shadow-[0_0_8px_rgba(16,185,129,0.8)]"></div>
                        <span class="font-black text-sm text-indigo-950 tracking-tight">${ticker}</span>
                    </div>
                    
                    <div class="flex items-center gap-1.5 opacity-80">
                        <span class="text-[0.6rem] font-bold text-emerald-600 bg-emerald-50 px-1 rounded border border-emerald-100">${bullish}B</span>
                        <span class="text-[0.6rem] font-bold text-red-600 bg-red-50 px-1 rounded border border-red-100">${bearish}S</span>
                    </div>

                    <div class="flex items-center gap-1 opacity-60 group-hover:opacity-100 transition-opacity">
                        <button class="p-1.5 rounded hover:bg-indigo-50 text-indigo-400 transition-all" 
                            onclick="event.stopPropagation(); openTickerModal('${ticker}')">
                            <svg xmlns="http://www.w3.org/2000/svg" class="h-3.5 w-3.5" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                                <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2.5" d="M10.325 4.317c.426-1.756 2.924-1.756 3.35 0a1.724 1.724 0 002.573 1.066c1.543-.94 3.31.826 2.37 2.37a1.724 1.724 0 001.065 2.572c1.756.426 1.756 2.924 0 3.35a1.724 1.724 0 00-1.066 2.573c.94 1.543-.826 3.31-2.37 2.37a1.724 1.724 0 00-2.572 1.065c-.426 1.756-2.924-1.756-3.35 0a1.724 1.724 0 00-2.573-1.066c-1.543.94-3.31-.826-2.37-2.37a1.724 1.724 0 00-1.065-2.572c-1.756-.426-1.756-2.924 0-3.35a1.724 1.724 0 001.066-2.573c-.94-1.543.826-3.31 2.37-2.37.996.608 2.296.07 2.572-1.065z" />
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
function updateSizingPanel(scan) {
    const panel = document.getElementById('sizingPanel');
    if (!panel) return;
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
// Ticker Settings Modal
// ──────────────────────────────────────────────
let currentEditingTicker = null;

function openTickerModal(ticker) {
    currentEditingTicker = ticker.toUpperCase();
    const modal = document.getElementById('tickerModal');
    const nameEl = document.getElementById('modalTickerName');
    if (!modal || !nameEl) return;

    nameEl.textContent = `${currentEditingTicker} Settings`;
    modal.classList.remove('hidden');

    const settings = (window.lastBotsData?.ticker_settings || {})[currentEditingTicker] || {};

    const amountInput = document.getElementById('modalAmount');
    const riskInput = document.getElementById('modalRisk');
    const atrInput = document.getElementById('modalAtrStop');
    const tpInput = document.getElementById('modalTpMult');

    amountInput.placeholder = "Enter Budget (e.g. 500)";
    riskInput.placeholder = "Enter Risk % (e.g. 2.5)";
    atrInput.placeholder = "Enter ATR Mult (e.g. 2.0)";
    tpInput.placeholder = "Enter TP Mult (e.g. 1.5)";

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

    const data = {
        ticker: currentEditingTicker,
        settings: {
            amount: parseFloat(document.getElementById('modalAmount').value) || null,
            risk_per_trade: parseFloat(document.getElementById('modalRisk').value) / 100 || null,
            atr_stop_multiplier: parseFloat(document.getElementById('modalAtrStop').value) || null,
            take_profit_multiplier: parseFloat(document.getElementById('modalTpMult').value) || null,
            timeframe: document.getElementById('modalTimeframe').value || null
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
            fetchBotsData();
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
        fetchBotsData();
    } catch (e) {
        console.error('Error resetting ticker settings:', e);
    }
}

// ──────────────────────────────────────────────
// Main Fetch Loop
// ──────────────────────────────────────────────
async function fetchBotsData() {
    try {
        const headers = await getAuthHeaders();
        // Use strategy timeframe from state if available
        const url = `${API_BASE}/api/dashboard?timeframe=${currentBackendTf}`;
        const response = await fetch(url, { headers });
        const data = await response.json();

        // Cache for modal access
        window.lastBotsData = data;

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
        }
        if (document.getElementById('dailyPL')) {
            const plEl = document.getElementById('dailyPL');
            plEl.textContent = data.dailyPL || '$0.00';
            plEl.classList.remove('animate-pulse');
            const plVal = parseFloat((data.dailyPL || '').replace(/[^-\d.]/g, ''));
            plEl.className = `card-value ${plVal >= 0 ? 'text-emerald-600' : 'text-red-500'}`;
        }

        // Last scan time
        if (document.getElementById('lastScanTime')) {
            document.getElementById('lastScanTime').textContent = data.lastScan ? formatLocalTime(data.lastScan) : '—';
        }

        // Strategy timeframe sync
        if (data.strategyTimeframe) {
            currentBackendTf = data.strategyTimeframe;
        }

        // Active bots list
        renderTradelist(data.watchlistScans, data.tradelist, data.tickerAmounts);

        // Position sizing for selected ticker
        if (selectedTicker && data.watchlistScans?.[selectedTicker]) {
            updateSizingPanel(data.watchlistScans[selectedTicker]);
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

    if (!statusEl || !dotEl || !textEl) return;

    if (isLinked) {
        statusEl.className = 'flex items-center text-sm font-black px-8 py-3 rounded-full border-2 shadow-sm whitespace-nowrap transition-all duration-500 uppercase tracking-wider bg-emerald-50 text-emerald-700 border-emerald-200';
        dotEl.className = 'h-2.5 w-2.5 rounded-full mr-2.5 bg-emerald-500 animate-pulse shadow-[0_0_8px_rgba(16,185,129,0.8)]';
        textEl.textContent = 'LIVE';
    } else {
        statusEl.className = 'flex items-center text-sm font-black px-8 py-3 rounded-full border-2 shadow-sm whitespace-nowrap transition-all duration-500 uppercase tracking-wider bg-amber-50 text-amber-700 border-amber-200';
        dotEl.className = 'h-2.5 w-2.5 rounded-full mr-2.5 bg-amber-500 animate-pulse';
        textEl.textContent = 'SIMULATION';
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
