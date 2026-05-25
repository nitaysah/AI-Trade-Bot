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
let currentChartPeriod = "1M";
let portfolioChartInstance = null;

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
        renderTradelist(window.lastBotsData.botScans || window.lastBotsData.watchlistScans, window.lastBotsData.tradelist, window.lastBotsData.tickerAmounts);
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
        let price = '---';
        const isCrypto = ticker.includes('USD') || ticker.includes('BTC') || ticker.includes('ETH') || ticker.includes('SOL') || ticker.includes('DOGE') || ticker.includes('LTC');
        if (scan?.price) {
            const parsedPrice = parseFloat(scan.price.toString().replace('$', ''));
            price = isCrypto ? `$${parsedPrice.toFixed(4)}` : `$${parsedPrice.toFixed(2)}`;
        }
        const actionColor = action === 'BUY' ? 'bg-emerald-50 text-emerald-600 border-emerald-100' : action === 'SELL' ? 'bg-red-50 text-red-600 border-red-100' : 'bg-slate-50 text-slate-400 border-slate-100';

        const bullish = scan?.bullish_count ?? 0;
        const bearish = scan?.bearish_count ?? 0;
        const tickerTf = (window.lastBotsData?.ticker_settings || {})[ticker]?.timeframe || '';
        const tfLabel = tickerTf || (window.lastBotsData?.strategyTimeframe || '5Min');
        const isPaused = (window.lastBotsData?.ticker_settings || {})[ticker]?.paused || false;
        const dotClass = isPaused
            ? 'h-2 w-2 bg-slate-300 rounded-full'
            : 'h-2 w-2 bg-emerald-500 rounded-full animate-pulse shadow-[0_0_8px_rgba(16,185,129,0.8)]';

        // Check for active position
        const positions = window.lastBotsData?.positions || [];
        const activePosition = positions.find(p => p.symbol === ticker);

        let positionHtml = '';
        if (activePosition) {
            const qty = activePosition.qty;
            const avgCostVal = activePosition.avg_price;
            const avgCost = isCrypto ? avgCostVal.toFixed(4) : avgCostVal.toFixed(2);
            const mktValue = activePosition.market_value.toFixed(2);
            const currPriceVal = activePosition.current_price;
            const currPrice = isCrypto ? currPriceVal.toFixed(4) : currPriceVal.toFixed(2);

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

            const costBasis = (qty * avgCostVal).toFixed(2);
            const qtyDisp = qty % 1 === 0 ? qty : (qty < 1 ? qty.toFixed(6) : qty.toFixed(4));

            positionHtml = `
                <div class="mt-2 pt-2 border-t border-slate-50 grid grid-cols-4 gap-1.5 cursor-pointer" onclick="selectTicker('${ticker}')">
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
            `;
        } else {
            positionHtml = `
                <div class="mt-2 pt-2 border-t border-slate-50 flex items-center justify-center py-1 cursor-pointer" onclick="selectTicker('${ticker}')">
                    <span class="text-[0.65rem] font-bold text-slate-400 animate-pulse tracking-wide flex items-center gap-1.5">
                        💤 Standing by. Watching market for entry...
                    </span>
                </div>
            `;
        }

        item.innerHTML = `
                <!-- Top Layer: Ticker, Status, Price, Action & Signals -->
                <div class="flex items-center justify-between cursor-pointer" onclick="selectTicker('${ticker}')">
                    <div class="flex items-center gap-2">
                        <div class="${dotClass}"></div>
                        <span class="font-black text-sm ${isPaused ? 'text-slate-400' : 'text-indigo-950'} tracking-tight">${ticker}</span>
                        ${isPaused ? '<span class="text-[0.5rem] font-bold px-1.5 py-0.5 rounded bg-amber-50 text-amber-500 border border-amber-200">PAUSED</span>' : '<span class="text-[0.5rem] font-bold px-1.5 py-0.5 rounded bg-emerald-50 text-emerald-600 border border-emerald-100">ACTIVE</span>'}
                        <span class="px-1.5 py-0.5 rounded bg-indigo-50/50 text-indigo-500 font-mono font-black text-[0.55rem] border border-indigo-100/50 uppercase">${tfLabel}</span>
                        <span class="text-[0.75rem] font-black text-slate-600 font-mono tracking-tight ml-1">${price}</span>
                    </div>
                    
                    <div class="flex items-center gap-1.5">
                        <span class="px-1.5 py-0.5 rounded border ${actionColor} font-black text-[0.5rem] tracking-widest uppercase">${action}</span>
                        <span class="text-[0.6rem] font-bold text-emerald-600 bg-emerald-50 px-1 rounded border border-emerald-100">${bullish}B</span>
                        <span class="text-[0.6rem] font-bold text-red-600 bg-red-50 px-1 rounded border border-red-100">${bearish}S</span>

                        <div class="flex items-center gap-1 opacity-60 group-hover:opacity-100 transition-opacity ml-1">
                            <button class="p-1 rounded hover:bg-indigo-50 text-indigo-400 transition-all" 
                                onclick="event.stopPropagation(); openStrategyModal('${ticker}', 'edit')">
                                <svg xmlns="http://www.w3.org/2000/svg" class="h-3.5 w-3.5" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                                    <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2.5" d="M10.325 4.317c.426-1.756 2.924-1.756 3.35 0a1.724 1.724 0 002.573 1.066c1.543-.94 3.31.826 2.37 2.37a1.724 1.724 0 001.065 2.572c1.756.426 1.756 2.924 0 3.35a1.724 1.724 0 00-1.066 2.573c.94 1.543-.826 3.31-2.37 2.37a1.724 1.724 0 00-2.572 1.065c-.426 1.756-2.924 1.756-3.35 0a1.724 1.724 0 00-2.573-1.066c-1.543.94-3.31-.826-2.37-2.37a1.724 1.724 0 00-1.065-2.572c-1.756-.426-1.756-2.924 0-3.35a1.724 1.724 0 001.066-2.573c-.94-1.543.826-3.31 2.37-2.37.996.608 2.296.07 2.572-1.065z" />
                                    <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2.5" d="M15 12a3 3 0 11-6 0 3 3 0 016 0z" />
                                </svg>
                            </button>
                            <button class="p-1 rounded hover:bg-rose-50 text-rose-400 transition-all" 
                                onclick="event.stopPropagation(); removeFromTradelist('${ticker}')">
                                <svg xmlns="http://www.w3.org/2000/svg" class="h-3.5 w-3.5" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                                    <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2.5" d="M6 18L18 6M6 6l12 12" />
                                </svg>
                            </button>
                        </div>
                    </div>
                </div>

                ${positionHtml}
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
let latestOrderHistory = [];
let latestPendingOrders = [];
let currentScanPage = 1;
let currentTradePage = 1;
let currentPendingPage = 1;
const LOG_PAGE_SIZE = 20;

function setLogTab(tab) {
    currentLogTab = tab;
    if (tab === 'all') currentScanPage = 1;
    else if (tab === 'trades') currentTradePage = 1;
    else currentPendingPage = 1;

    const allBtn = document.getElementById('tabLogAll');
    const tradesBtn = document.getElementById('tabLogTrades');
    const pendingBtn = document.getElementById('tabLogPending');

    if (allBtn && tradesBtn && pendingBtn) {
        allBtn.className = "px-4 py-1.5 text-[0.6rem] font-black uppercase rounded-lg transition-all " +
            (tab === 'all' ? "bg-white text-indigo-600 shadow-sm border border-indigo-100" : "text-indigo-400 hover:text-indigo-600");
        tradesBtn.className = "px-4 py-1.5 text-[0.6rem] font-black uppercase rounded-lg transition-all " +
            (tab === 'trades' ? "bg-white text-indigo-600 shadow-sm border border-indigo-100" : "text-indigo-400 hover:text-indigo-600");
        pendingBtn.className = "px-4 py-1.5 text-[0.6rem] font-black uppercase rounded-lg transition-all " +
            (tab === 'pending' ? "bg-white text-indigo-600 shadow-sm border border-indigo-100" : "text-indigo-400 hover:text-indigo-600");
    }

    renderTradeLog(latestScanHistory, latestOrderHistory, latestPendingOrders);
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
            fetchBotsData(); // reload
        } else {
            alert("Error: " + (result.message || "Could not cancel order."));
        }
    } catch (e) {
        console.error('Error cancelling order:', e);
        alert("Failed to contact the backend to cancel the order.");
    }
}

function renderTradeLog(scanHistory, orderHistory, pendingOrders) {
    const tbody = document.getElementById('tradeLogBody');
    const thead = document.getElementById('tradeLogHeader');
    const noMsg = document.getElementById('noTradesMsg');
    if (!tbody) return;

    tbody.innerHTML = '';

    if (thead) {
        if (currentLogTab === 'all') {
            thead.innerHTML = `
                <tr class="border-b border-purple-200 text-purple-700 text-[10px] md:text-xs uppercase tracking-wider">
                    <th class="pb-3 pr-4">Time</th>
                    <th class="pb-3 pr-4">Action</th>
                    <th class="pb-3 pr-4">Ticker</th>
                    <th class="pb-3 pr-4 text-center">TF</th>
                    <th class="pb-3 pr-4">Price</th>
                    <th class="pb-3 pr-4">Signals</th>
                    <th class="pb-3 pr-4">AI Reason</th>
                </tr>
            `;
        } else if (currentLogTab === 'trades') {
            thead.innerHTML = `
                <tr class="border-b border-purple-200 text-purple-700 text-[10px] md:text-xs uppercase tracking-wider">
                    <th class="pb-3 pr-4">Time Created</th>
                    <th class="pb-3 pr-4">Ticker</th>
                    <th class="pb-3 pr-4">Side</th>
                    <th class="pb-3 pr-4 text-center">Qty Filled</th>
                    <th class="pb-3 pr-4 text-center">Avg Price</th>
                    <th class="pb-3 pr-4 text-center">Total Cost</th>
                    <th class="pb-3 pr-4 text-center">P/L</th>
                    <th class="pb-3 pr-4">AI Summary</th>
                </tr>
            `;
        } else if (currentLogTab === 'pending') {
            thead.innerHTML = `
                <tr class="border-b border-purple-200 text-purple-700 text-[10px] md:text-xs uppercase tracking-wider">
                    <th class="pb-3 pr-4">Time Placed</th>
                    <th class="pb-3 pr-4">Ticker</th>
                    <th class="pb-3 pr-4">Side</th>
                    <th class="pb-3 pr-4 text-center">TIF</th>
                    <th class="pb-3 pr-4 text-center">Qty</th>
                    <th class="pb-3 pr-4 text-center">Target Price</th>
                    <th class="pb-3 pr-4 text-center">Total Value</th>
                    <th class="pb-3 pr-4">AI Summary</th>
                    <th class="pb-3 pr-4 text-center">Action</th>
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
    }

    if (!items || items.length === 0) {
        if (noMsg) {
            noMsg.style.display = 'block';
            if (currentLogTab === 'all') {
                noMsg.textContent = "Waiting for first scan...";
            } else if (currentLogTab === 'trades') {
                noMsg.textContent = "No order history found.";
            } else {
                noMsg.textContent = "No pending/working orders.";
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
            const actionColor = item.action === 'BUY'
                ? 'text-emerald-600 font-bold'
                : item.action === 'SELL'
                    ? 'text-red-500 font-bold'
                    : 'text-purple-500 font-medium';

            const signalBadge = item.bullish_count !== undefined
                ? `${item.bullish_count}B / ${item.bearish_count}S`
                : '—';

            row.innerHTML = `
                <td class="py-2.5 pr-4 text-purple-600 text-xs">${formatLocalTime(item.time)}</td>
                <td class="py-2.5 pr-4 ${actionColor} text-xs">${item.action}</td>
                <td class="py-2.5 pr-4 text-indigo-950 font-semibold text-xs">${item.ticker}</td>
                <td class="py-2.5 pr-4 text-center">
                    <span class="px-1.5 py-0.5 rounded-md bg-indigo-50 text-indigo-500 font-black text-[0.55rem] border border-indigo-100">${item.timeframe || '5M'}</span>
                </td>
                <td class="py-2.5 pr-4 text-indigo-700 font-medium text-xs">${item.price}</td>
                <td class="py-2.5 pr-4 text-xs">
                    <span class="px-1.5 py-0.5 rounded bg-purple-100 text-purple-700 font-mono text-[0.6rem]">${signalBadge}</span>
                </td>
                <td class="py-2.5 pr-4 text-purple-500 italic text-xs max-w-xs truncate" title="${item.reason}">${item.reason}</td>
            `;
        } else if (currentLogTab === 'trades') {
            const sideColor = item.side === 'buy' ? 'text-emerald-600 font-bold' : 'text-rose-500 font-bold';
            
            // Dynamic precision price formatting
            const cleanSym = String(item.symbol).toUpperCase();
            const isCrypto = cleanSym.includes("USD") || cleanSym.includes("USDT") || cleanSym.includes("USDC");
            const priceDecimals = (isCrypto || (item.filled_avg_price && item.filled_avg_price < 10)) ? 4 : 2;
            const avgPriceStr = item.filled_avg_price ? Number(item.filled_avg_price).toFixed(priceDecimals) : '0.00';
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
                <td class="py-2.5 pr-4 text-purple-600 text-xs">${formatLocalTime(item.created_at)}</td>
                <td class="py-2.5 pr-4 text-indigo-950 font-semibold text-xs">${item.symbol}</td>
                <td class="py-2.5 pr-4 text-xs ${sideColor} uppercase">${item.side}</td>
                <td class="py-2.5 pr-4 text-center text-[0.65rem] font-bold text-indigo-900">${item.filled_qty ? item.filled_qty.toFixed(4) : '0.0000'}</td>
                <td class="py-2.5 pr-4 text-center text-xs text-indigo-700 font-medium">$${avgPriceStr}</td>
                <td class="py-2.5 pr-4 text-center text-xs text-indigo-900 font-bold">$${totalCostStr}</td>
                ${plHtml}
                <td class="py-2.5 pr-4 text-purple-500 italic text-[11px] max-w-[200px] truncate" title="${aiReason}">${aiReason}</td>
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
            const aiReason = item.reason || 'AI Strategy: Trigger awaiting limit order criteria.';

            row.innerHTML = `
                <td class="py-2.5 pr-4 text-purple-600 text-xs">${formatLocalTime(item.created_at)}</td>
                <td class="py-2.5 pr-4 text-indigo-950 font-semibold text-xs">${item.symbol}</td>
                <td class="py-2.5 pr-4 text-xs ${sideColor} uppercase">${item.side}</td>
                <td class="py-2.5 pr-4 text-center">${tifBadge}</td>
                <td class="py-2.5 pr-4 text-center text-[0.65rem] font-bold text-indigo-900">${item.qty ? item.qty.toFixed(4) : '0.0000'}</td>
                <td class="py-2.5 pr-4 text-center text-xs text-indigo-700 font-medium">${limitPriceStr}</td>
                <td class="py-2.5 pr-4 text-center text-xs text-indigo-900 font-bold">${totalValueStr}</td>
                <td class="py-2.5 pr-4 text-purple-500 italic text-[11px] max-w-[200px] truncate" title="${aiReason}">${aiReason}</td>
                <td class="py-2.5 pr-4 text-center">
                    <button onclick="cancelOrder('${item.id}')"
                        class="px-2 py-1 bg-rose-500 text-white rounded hover:bg-rose-600 transition-all font-black text-[9px] uppercase tracking-wider">
                        Cancel
                    </button>
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
    } else {
        const total = Math.ceil(latestPendingOrders.length / LOG_PAGE_SIZE) || 1;
        currentPendingPage = Math.max(1, Math.min(total, currentPendingPage + delta));
    }
    renderTradeLog(latestScanHistory, latestOrderHistory, latestPendingOrders);
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
        const globalParams = window.lastBotsData?.indicator_parameters || {};
        document.getElementById('strategyCapital').value = 100;
        document.getElementById('strategyBuyThreshold').value = globalParams.MIN_BULLISH_SIGNALS || 4;
        document.getElementById('strategySellThreshold').value = globalParams.MIN_BEARISH_SIGNALS || 4;
        document.getElementById('strategyTimeframe').value = "1Hour";
        document.getElementById('strategySellMode').value = "indicator";
        window._strategyPaused = false;

        // Reset Risk Overrides to defaults
        document.getElementById('strategyRiskPerTrade').value = 2.0;
        document.getElementById('strategyMaxDailyDrawdown').value = 5.0;
        document.getElementById('strategyMaxPositionPct').value = 100.0;
        document.getElementById('strategyAtrStopMult').value = 2.0;
        document.getElementById('strategyAtrTrailMult').value = 3.0;
        document.getElementById('strategyAtrTpMult').value = 4.0;

        // Ensure collapsed on open
        document.getElementById('riskControlsContent').classList.add('hidden');
        document.getElementById('riskArrowIcon').classList.remove('rotate-180');

        // Reset Indicators
        document.querySelectorAll('.strategy-indicator-check').forEach(chk => chk.checked = false);
    } else {
        titleEl.textContent = `${currentStrategySymbol} Settings`;
        subtitleEl.textContent = 'Adjust active bot strategy parameters';
        actionBtn.textContent = 'Save Settings 💾';
        actionBtn.className = "w-full bg-gradient-to-r from-indigo-600 to-purple-600 text-white font-black py-4 rounded-2xl hover:shadow-lg hover:shadow-indigo-500/30 transition-all active:scale-[0.98] uppercase tracking-[0.2em] shadow-md flex items-center justify-center gap-2";

        // Load existing settings
        const settings = (window.lastBotsData?.ticker_settings || {})[currentStrategySymbol] || {};
        const globalParams = window.lastBotsData?.indicator_parameters || {};
        document.getElementById('strategyCapital').value = settings.amount || '';
        document.getElementById('strategyBuyThreshold').value = settings.min_buy_signals !== undefined ? settings.min_buy_signals : (globalParams.MIN_BULLISH_SIGNALS || 4);
        document.getElementById('strategySellThreshold').value = settings.min_sell_signals !== undefined ? settings.min_sell_signals : (globalParams.MIN_BEARISH_SIGNALS || 4);
        document.getElementById('strategyTimeframe').value = settings.timeframe || '1Hour';
        document.getElementById('strategySellMode').value = settings.sell_mode || 'indicator';
        window._strategyPaused = settings.paused || false;

        // Load Risk Overrides
        document.getElementById('strategyRiskPerTrade').value = settings.risk_per_trade !== undefined ? (settings.risk_per_trade * 100).toFixed(1) : 2.0;
        document.getElementById('strategyMaxDailyDrawdown').value = settings.max_daily_drawdown !== undefined ? (settings.max_daily_drawdown * 100).toFixed(1) : 5.0;
        document.getElementById('strategyMaxPositionPct').value = settings.max_position_pct !== undefined ? (settings.max_position_pct * 100).toFixed(1) : 100.0;
        document.getElementById('strategyAtrStopMult').value = settings.atr_stop_multiplier !== undefined ? settings.atr_stop_multiplier : 2.0;
        document.getElementById('strategyAtrTrailMult').value = settings.atr_trail_multiplier !== undefined ? settings.atr_trail_multiplier : 3.0;
        document.getElementById('strategyAtrTpMult').value = settings.take_profit_multiplier !== undefined ? settings.take_profit_multiplier : 4.0;

        // Ensure collapsed on open
        document.getElementById('riskControlsContent').classList.add('hidden');
        document.getElementById('riskArrowIcon').classList.remove('rotate-180');

        const enabledIndicators = settings.indicators || ['RSI', 'MACD', 'EMA Cross', 'Supertrend', 'Bollinger', 'VWAP', 'Mystic Pulse', 'Candle Patterns'];
        document.querySelectorAll('.strategy-indicator-check').forEach(chk => {
            chk.checked = enabledIndicators.includes(chk.value);
        });
    }

    updateStrategySignalLabels();
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

window.updateStrategySignalLabels = function () {
    const checkedCount = document.querySelectorAll('.strategy-indicator-check:checked').length;
    const buyInput = document.getElementById('strategyBuyThreshold');
    const sellInput = document.getElementById('strategySellThreshold');
    const buyLabel = document.getElementById('strategyBuyLabel');
    const sellLabel = document.getElementById('strategySellLabel');

    if (!buyInput || !sellInput || !buyLabel || !sellLabel) return;

    if (checkedCount === 0) {
        buyInput.max = 0; buyInput.value = 0;
        sellInput.max = 0; sellInput.value = 0;
        buyLabel.textContent = "Select Indicators";
        sellLabel.textContent = "Select Indicators";
        return;
    }

    buyInput.max = checkedCount;
    sellInput.max = checkedCount;

    if (parseInt(buyInput.value) > checkedCount) buyInput.value = checkedCount;
    if (parseInt(buyInput.value) < 0) buyInput.value = 0;
    if (parseInt(sellInput.value) > checkedCount) sellInput.value = checkedCount;
    if (parseInt(sellInput.value) < 0) sellInput.value = 0;

    buyLabel.textContent = `${buyInput.value} of ${checkedCount} Signals`;
    sellLabel.textContent = `${sellInput.value} of ${checkedCount} Signals`;
};

window.adjustStrategyThreshold = function (type, delta) {
    const checkedCount = document.querySelectorAll('.strategy-indicator-check:checked').length;
    if (checkedCount === 0) return;

    const input = type === 'buy' ? document.getElementById('strategyBuyThreshold') : document.getElementById('strategySellThreshold');
    if (!input) return;

    let val = parseInt(input.value) + delta;
    val = Math.max(0, Math.min(val, checkedCount));
    input.value = val;
    window.updateStrategySignalLabels();
};

window.toggleRiskSection = function () {
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

window.handleStrategyAction = async function () {
    if (strategyModalMode === 'deploy') {
        await confirmAndDeployBot();
    } else {
        await saveTickerSettings();
    }
};

async function saveTickerSettings() {
    if (!currentStrategySymbol) return;

    const buyThresholdVal = parseInt(document.getElementById('strategyBuyThreshold').value);
    const sellThresholdVal = parseInt(document.getElementById('strategySellThreshold').value);
    const indicators = [];
    document.querySelectorAll('.strategy-indicator-check:checked').forEach(check => {
        indicators.push(check.value);
    });

    const data = {
        ticker: currentStrategySymbol,
        settings: {
            amount: parseFloat(document.getElementById('strategyCapital').value) || null,
            timeframe: document.getElementById('strategyTimeframe').value || null,
            min_buy_signals: buyThresholdVal,
            min_sell_signals: sellThresholdVal,
            sell_mode: document.getElementById('strategySellMode').value,
            indicators: indicators,
            paused: window._strategyPaused || false,
            risk_per_trade: parseFloat(document.getElementById('strategyRiskPerTrade').value) / 100.0,
            max_daily_drawdown: parseFloat(document.getElementById('strategyMaxDailyDrawdown').value) / 100.0,
            max_position_pct: parseFloat(document.getElementById('strategyMaxPositionPct').value) / 100.0,
            atr_stop_multiplier: parseFloat(document.getElementById('strategyAtrStopMult').value),
            atr_trail_multiplier: parseFloat(document.getElementById('strategyAtrTrailMult').value),
            take_profit_multiplier: parseFloat(document.getElementById('strategyAtrTpMult').value)
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

window.resetTickerSettings = async function () {
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
        const url = `${API_BASE}/api/dashboard?mode=fast&source=bots`;
        const response = await fetch(url, { headers });
        const data = await response.json();

        // Cache for modal access
        window.lastBotsData = data;
        window.lastDashboardData = data;

        // Connection status
        isAlpacaLinked = !data.simulation && data.has_keys;
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
            fetchPortfolioHistory();
        }

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
        renderTradelist(data.botScans || data.watchlistScans, data.tradelist, data.tickerAmounts);

        // Auto-select first active bot if none selected
        if (!selectedTicker && data.tradelist && data.tradelist.length > 0) {
            selectedTicker = data.tradelist[0];
        }

        // AI Summary
        if (document.getElementById('aiSummary')) {
            let summaryText = "";
            if (selectedTicker && data.botScans && data.botScans[selectedTicker] && data.botScans[selectedTicker].sentiment_summary) {
                summaryText = data.botScans[selectedTicker].sentiment_summary;
            } else if (data.sentiment_summary) {
                summaryText = data.sentiment_summary;
            }
            document.getElementById('aiSummary').textContent = summaryText || "Waiting for scan analysis...";
        }

        // Execution log
        latestScanHistory = data.recentTrades || [];
        latestOrderHistory = data.orderHistory || [];
        latestPendingOrders = data.pendingOrders || [];
        renderTradeLog(latestScanHistory, latestOrderHistory, latestPendingOrders);

    } catch (error) {
        console.error('[bots] Fetch error:', error);
    }
}

function updateAlpacaStatus(isLinked, data = null) {
    const statusEl = document.getElementById('alpacaLinkStatus');
    const dotEl = document.getElementById('alpacaStatusDot');
    const textEl = document.getElementById('alpacaStatusText');
    const btnUnlink = document.getElementById('btnUnlinkAlpaca');

    if (!statusEl || !dotEl || !textEl) return;

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
            closeAlpacaModal();
            fetchBotsData();
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
            fetchBotsData();
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
    const threshold = document.getElementById('strategyBuyThreshold').value;
    const sellThreshold = document.getElementById('strategySellThreshold').value;
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
                sell_threshold: parseInt(sellThreshold),
                sell_mode: sellMode,
                indicators: indicators,
                timeframe: timeframe,
                paused: window._strategyPaused || false,
                risk_per_trade: parseFloat(document.getElementById('strategyRiskPerTrade').value) / 100.0,
                max_daily_drawdown: parseFloat(document.getElementById('strategyMaxDailyDrawdown').value) / 100.0,
                max_position_pct: parseFloat(document.getElementById('strategyMaxPositionPct').value) / 100.0,
                atr_stop_multiplier: parseFloat(document.getElementById('strategyAtrStopMult').value),
                atr_trail_multiplier: parseFloat(document.getElementById('strategyAtrTrailMult').value),
                take_profit_multiplier: parseFloat(document.getElementById('strategyAtrTpMult').value)
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
    // Wait for Firebase auth to be ready and currentUser to be populated before first fetch
    const waitForAuth = setInterval(() => {
        if (window.auth && window.auth.currentUser) {
            clearInterval(waitForAuth);
            fetchBotsData();
            setInterval(fetchBotsData, REFRESH_INTERVAL);
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

// ──────────────────────────────────────────────
// Portfolio Equity History Chart Logic
// ──────────────────────────────────────────────
function getChartTimeframe(period) {
    switch (period.toUpperCase()) {
        case '1D': return '15Min';
        case '1W': return '1H';
        case '1M': return '1D';
        case '1Y': return '1D';
        default: return '1D';
    }
}

async function setChartPeriod(period) {
    currentChartPeriod = period;

    // Update active period button styling
    const buttons = ['1D', '1W', '1M', '1Y'];
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

    await fetchPortfolioHistory();
}

async function fetchPortfolioHistory() {
    const canvas = document.getElementById('portfolioHistoryChart');
    if (!canvas) return;

    try {
        const headers = await getAuthHeaders();
        const tf = getChartTimeframe(currentChartPeriod);
        const url = `${API_BASE}/api/portfolio/history?period=${currentChartPeriod}&timeframe=${tf}`;

        const response = await fetch(url, { headers });
        if (!response.ok) throw new Error(`HTTP error! status: ${response.status}`);

        const data = await response.json();
        if (!data || !data.timestamp || data.timestamp.length === 0) {
            console.warn("[chart] No portfolio history data returned.");
            return;
        }

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
        if (equities && equities.length > 0) {
            const firstEq = equities[0];
            const lastEq = equities[equities.length - 1];
            const finalPl = lastEq - firstEq;
            const finalPlPct = firstEq > 0 ? (finalPl / firstEq * 100) : 0.0;

            const subEl = document.getElementById('equityPerformanceSub');
            if (subEl) {
                const sign = finalPl >= 0 ? '+' : '';
                const colorClass = finalPl >= 0 ? 'text-emerald-500' : 'text-rose-500';
                subEl.innerHTML = `Performance: <span class="${colorClass} font-black">${sign}$${finalPl.toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 })} (${sign}${finalPlPct.toFixed(2)}%)</span> over this ${currentChartPeriod}`;
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
                            display: false, // Clean look
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
                            display: false
                        }
                    },
                    y: {
                        min: yMin,
                        max: yMax,
                        grid: {
                            color: 'rgba(243, 244, 246, 0.8)', // Light gridlines
                            drawBorder: false
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
                            display: false
                        }
                    }
                },
                interaction: {
                    mode: 'nearest',
                    axis: 'x',
                    intersect: false
                }
            }
        });

    } catch (e) {
        console.error("[chart] Error rendering portfolio history:", e);
    }
}

