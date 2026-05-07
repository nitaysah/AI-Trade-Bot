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

let selectedTicker = null;
let currentBackendTf = "5Min";
let isAlpacaLinked = false; // Track live connection status
let favoriteTickers = JSON.parse(localStorage.getItem('favoriteTickers') || '["BTCUSD", "ETHUSD", "TSLA", "AAPL", "MSFT"]');
let tvWidget = null;
let currentLogTab = "all"; // 'all' or 'trades'
let latestTradesData = []; // Cached log data

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
        return date.toLocaleTimeString('en-US', {
            timeZone: 'America/Chicago',
            hour: '2-digit',
            minute: '2-digit',
            second: '2-digit',
            hour12: false
        });
    } catch (e) {
        return isoString;
    }
}
// Tracked in consolidated block at top

// ──────────────────────────────────────────────
// 1. Initialize Chart (TradingView Advanced Widget)
// ──────────────────────────────────────────────
function initChart(ticker = "AAPL", tf = "1D") {
    const container = document.getElementById('tradingChart');
    if (!container) return;

    let interval = "5";
    if (tf === "1Min") interval = "1";
    if (tf === "5Min") interval = "5";
    if (tf === "15Min") interval = "15";
    if (tf === "1Hour") interval = "60";
    if (tf === "1D" || tf === "1Day") interval = "D";

    // Format crypto for TradingView if needed (e.g. BTCUSD -> CRYPTO:BTCUSD)
    let tvSymbol = ticker;
    if (ticker.includes("USD") && ticker.length >= 6) {
        tvSymbol = `BITSTAMP:${ticker}`; // Bitstamp is a very stable feed for TV widget
    }

    container.innerHTML = '';

    tvWidget = new TradingView.widget({
        "autosize": true,
        "symbol": tvSymbol,
        "interval": interval,
        "timezone": "America/Chicago",
        "theme": "light",
        "style": "1",
        "locale": "en",
        "enable_publishing": false,
        "backgroundColor": "rgba(255, 255, 255, 1)",
        "gridColor": "rgba(168, 85, 247, 0.1)",
        "hide_top_toolbar": false,
        "hide_legend": false,
        "save_image": false,
        "container_id": "tradingChart"
    });
}

// ──────────────────────────────────────────────
// 2. Update Chart
// ──────────────────────────────────────────────
function updateChart(priceHistory, ticker, signals) {
    // The TradingView widget handles its own data fetching directly from TradingView.
    // We only need to update the label.
    document.getElementById('chartTickerLabel').textContent = ticker;
}

// ──────────────────────────────────────────────
// 3. Render Signal Confluence Grid
// ──────────────────────────────────────────────
function renderSignals(signals, action, reason, bullishCount, bearishCount) {
    const grid = document.getElementById('signalGrid');
    const loading = document.getElementById('gridLoading');

    if (!signals || Object.keys(signals).length === 0) {
        if (loading) loading.classList.remove('hidden');
        grid.innerHTML = '';
        return;
    }

    if (loading) loading.classList.add('hidden');
    grid.innerHTML = '';

    for (const [name, data] of Object.entries(signals)) {
        const isEnabled = data.enabled !== false;

        let signalClass = data.signal === 'BULLISH' ? 'bullish' : data.signal === 'BEARISH' ? 'bearish' : 'neutral';
        if (!isEnabled) {
            signalClass = 'disabled-signal';
        }

        const icon = data.signal === 'BULLISH' ? '▲' : data.signal === 'BEARISH' ? '▼' : '●';
        const iconColor = data.signal === 'BULLISH' ? 'text-emerald-600' : data.signal === 'BEARISH' ? 'text-red-500' : 'text-purple-400';

        const card = document.createElement('div');
        // Add cursor-pointer to indicate it's clickable
        card.className = `signal-card ${signalClass} fade-in cursor-pointer`;

        // When clicked, toggle the indicator
        if (data.toggle_key) {
            card.onclick = () => toggleIndicator(data.toggle_key, !isEnabled);
            card.title = isEnabled ? "Click to disable this indicator" : "Click to enable this indicator";
        }

        card.innerHTML = `
            <div class="flex items-center justify-between mb-1">
                <span class="font-bold text-xs text-indigo-900 ${!isEnabled ? 'opacity-50' : ''}">${name}</span>
                <span class="${iconColor} text-sm font-bold ${!isEnabled ? 'opacity-50 grayscale' : ''}">${icon}</span>
            </div>
            <p class="text-[0.65rem] text-purple-600 leading-tight ${!isEnabled ? 'opacity-50' : ''}">${data.reason}</p>
        `;
        grid.appendChild(card);
    }

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
}

// ──────────────────────────────────────────────
// 4. Render Watchlist
// ──────────────────────────────────────────────
function renderWatchlist(scans, watchlist, tradelist = []) {
    const container = document.getElementById('watchlistContainer');
    container.innerHTML = '';

    const tickers = watchlist || Object.keys(scans || {});

    tickers.forEach(ticker => {
        const scan = (scans || {})[ticker];
        const item = document.createElement('div');
        item.className = `watchlist-item group ${selectedTicker === ticker ? 'active' : ''} p-3 mb-2 flex flex-col gap-2`;

        const action = scan?.action || '—';
        const price = scan?.price ? `$${parseFloat(scan.price.toString().replace('$', '')).toFixed(2)}` : '---';
        const actionColor = action === 'BUY' ? 'bg-emerald-50 text-emerald-600 border-emerald-100' : action === 'SELL' ? 'bg-red-50 text-red-600 border-red-100' : 'bg-slate-50 text-slate-400 border-slate-100';

        const bullish = scan?.bullish_count || 0;
        const total = scan?.total_signals || 0;
        const isBotActive = tradelist.includes(ticker);

        item.innerHTML = `
                <!-- Top Layer: Ticker & Bot Badge -->
                <div class="flex items-center justify-between">
                    <div class="flex items-center gap-2 cursor-pointer" onclick="selectTicker('${ticker}')">
                        <span class="font-black text-sm text-indigo-950 tracking-tight">${ticker}</span>
                        ${isBotActive ? '<span class="px-1.5 py-0.5 rounded-full bg-indigo-100 text-indigo-600 text-[0.6rem] font-black tracking-widest uppercase">Active Bot</span>' : ''}
                    </div>
                    
                    <div class="flex items-center gap-1 opacity-60 group-hover:opacity-100 transition-opacity">
                        <button class="p-1.5 rounded hover:bg-indigo-50 ${isBotActive ? 'text-indigo-600' : 'text-indigo-300'}" 
                            title="${isBotActive ? 'Bot Active' : 'Activate Bot'}"
                            onclick="event.stopPropagation(); addToTradelist('${ticker}')">
                            <svg xmlns="http://www.w3.org/2000/svg" class="h-4 w-4" fill="${isBotActive ? 'currentColor' : 'none'}" viewBox="0 0 24 24" stroke="currentColor">
                                <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2.5" d="M14.752 11.168l-3.197-2.132A1 1 0 0010 9.87v4.263a1 1 0 001.555.832l3.197-2.132a1 1 0 000-1.664z" />
                            </svg>
                        </button>
                        <button class="p-1.5 rounded hover:bg-rose-50 text-rose-300 hover:text-rose-400 transition-all" 
                            title="Remove from Watchlist"
                            onclick="event.stopPropagation(); removeFromWatchlist('${ticker}')">
                            <svg xmlns="http://www.w3.org/2000/svg" class="h-4 w-4" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                                <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2.5" d="M6 18L18 6M6 6l12 12" />
                            </svg>
                        </button>
                    </div>
                </div>

                <!-- Bottom Layer: Price, Signal Count & Action -->
                <div class="flex items-center justify-between border-t border-slate-50 pt-2 cursor-pointer" onclick="selectTicker('${ticker}')">
                    <div class="flex flex-col">
                        <span class="text-xs font-black text-slate-500 font-mono tracking-tight">${price}</span>
                        <span class="text-[0.6rem] text-slate-400 font-bold uppercase tracking-tighter">${bullish}/${total} Signals</span>
                    </div>
                    <div class="px-2 py-0.5 rounded border ${actionColor} shadow-sm min-w-[50px] text-center">
                        <span class="font-black text-[0.65rem] tracking-widest">${action}</span>
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

    const searchInput = document.getElementById('tickerSearch');
    if (searchInput && !searchInput.value && selectedTicker) {
        searchInput.value = selectedTicker;
    }
    updateFavoriteIcon();
}

function selectTicker(ticker) {
    selectedTicker = ticker;
    document.getElementById('chartTickerLabel').textContent = selectedTicker;
    initChart(selectedTicker, "1D");
    fetchDashboard(); // Refresh with new ticker context
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

            fetchDashboard(); // Refresh UI
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
        const response = await fetch(`${API_BASE}/api/tradelist`, {
            method: 'POST',
            headers: headers,
            body: JSON.stringify({ ticker: ticker })
        });
        if (response.ok) fetchDashboard();
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
        if (response.ok) fetchDashboard();
    } catch (e) {
        console.error('Error removing from tradelist:', e);
    }
}

// ──────────────────────────────────────────────
// 5. Render Trade Log
// ──────────────────────────────────────────────
let latestScanHistory = [];
let latestExecutedTrades = [];
let currentScanPage = 1;
let currentTradePage = 1;
const LOG_PAGE_SIZE = 20;

function setLogTab(tab) {
    currentLogTab = tab;
    // Reset to first page when switching tabs
    if (tab === 'all') currentScanPage = 1;
    else currentTradePage = 1;

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

    renderTradeLog(latestScanHistory, latestExecutedTrades);
}

function renderTradeLog(scanHistory, executedTrades) {
    const tbody = document.getElementById('tradeLogBody');
    const noMsg = document.getElementById('noTradesMsg');
    if (!tbody) return;

    tbody.innerHTML = '';
    const filterToggle = document.getElementById('filterLogToggle');
    const isFiltered = filterToggle && filterToggle.checked;

    let trades = currentLogTab === 'trades' ? (executedTrades || []) : (scanHistory || []);
    
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
        const total = Math.ceil(latestScanHistory.length / LOG_PAGE_SIZE);
        currentScanPage = Math.max(1, Math.min(total, currentScanPage + delta));
    } else {
        const total = Math.ceil(latestExecutedTrades.length / LOG_PAGE_SIZE);
        currentTradePage = Math.max(1, Math.min(total, currentTradePage + delta));
    }
    renderTradeLog(latestScanHistory, latestExecutedTrades);
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
            take_profit_multiplier: parseFloat(document.getElementById('modalTpMult').value) || null
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
            fetchDashboard(); // Refresh to show changes
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
        fetchDashboard();
    } catch (e) {
        console.error('Error resetting ticker settings:', e);
    }
}

// ──────────────────────────────────────────────
// 8. Main Fetch Loop
// ──────────────────────────────────────────────
async function fetchDashboard() {
    try {
        const url = selectedTicker
            ? `${API_BASE}/api/dashboard?ticker=${selectedTicker}&timeframe=${currentBackendTf}`
            : `${API_BASE}/api/dashboard`;

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

        // Sync Timeframe from Backend
        if (data.strategyTimeframe && data.strategyTimeframe !== currentBackendTf) {
            console.log(`[dashboard] Syncing timeframe to backend: ${data.strategyTimeframe}`);
            currentBackendTf = data.strategyTimeframe;
            const tfSelector = document.getElementById('strategyTf');
            if (tfSelector) tfSelector.value = currentBackendTf;
        }

        // Log diagnostic info from backend
        if (data.debug_logs && data.debug_logs.length > 0) {
            // Diagnostic logs silenced as requested
        }

        // Set default selected ticker
        if (!selectedTicker && data.watchlist?.length > 0) {
            selectedTicker = data.primaryTicker || data.watchlist[0];
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
                    statusPill.className = "flex items-center text-sm font-black px-8 py-3 rounded-full bg-amber-50 text-amber-600 border-2 border-amber-100 shadow-sm whitespace-nowrap transition-all duration-500 uppercase tracking-wider";
                    statusDot.className = "h-2.5 w-2.5 rounded-full mr-2.5 bg-amber-500 animate-pulse";
                    statusText.textContent = "Connection Failed (Retrying...)";
                } else {
                    statusPill.className = "flex items-center text-sm font-black px-8 py-3 rounded-full bg-rose-50 text-rose-600 border-2 border-rose-100 shadow-sm whitespace-nowrap transition-all duration-500 uppercase tracking-wider";
                    statusDot.className = "h-2.5 w-2.5 rounded-full mr-2.5 bg-rose-500 animate-pulse";
                    statusText.textContent = "Alpaca Not Linked";
                }
                if (btnUnlink) btnUnlink.classList.add('hidden');
            } else {
                isAlpacaLinked = true;
                statusPill.className = "flex items-center text-sm font-black px-8 py-3 rounded-full bg-emerald-50 text-emerald-600 border-2 border-emerald-100 shadow-sm whitespace-nowrap transition-all duration-500 uppercase tracking-wider";
                statusDot.className = "h-2.5 w-2.5 rounded-full mr-2.5 bg-emerald-500 animate-pulse";
                statusText.textContent = "Alpaca Live";
                if (btnUnlink) btnUnlink.classList.remove('hidden');
            }
        }

        // Connection prompt visibility
        const connPrompt = document.getElementById('connectionPrompt');
        const dashContent = document.getElementById('dashboardContent');

        const summaryEl = document.getElementById('aiSummary');
        const cardSentSummary = document.getElementById('sentimentSummary');

        if (summaryEl) {
            if (data.sentiment_summary) {
                summaryEl.innerHTML = `
                    <div class="mb-2">
                        <span class="text-[10px] uppercase tracking-wider text-fuchsia-500 font-bold block mb-1">Key Market Factor</span>
                        <p class="text-indigo-900 font-semibold not-italic">${data.sentiment_key_factor || 'Mixed Catalysts'}</p>
                    </div>
                    <div>
                        <span class="text-[10px] uppercase tracking-wider text-purple-400 font-bold block mb-1">AI Briefing</span>
                        <p>${data.sentiment_summary}</p>
                    </div>
                    <div class="mt-2 pt-2 border-t border-purple-100 flex justify-between items-center opacity-60">
                         <span>Analysis Confidence</span>
                         <span class="font-bold">${Math.round(data.sentiment_confidence * 100)}%</span>
                    </div>
                `;
                summaryEl.classList.remove('italic');
                if (cardSentSummary) cardSentSummary.textContent = data.sentiment_summary;
            } else {
                summaryEl.textContent = 'Waiting for next scan analysis...';
                if (cardSentSummary) cardSentSummary.textContent = 'Fetching latest market news...';
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

        if (sentEl) {
            sentEl.textContent = data.aiSentiment || 'Neutral';
            sentEl.classList.remove('animate-pulse');
        }


        const sentBar = document.getElementById('sentimentBar');
        if (sentBar) {
            const sentScore = data.sentiment_confidence || 0.5;
            sentBar.style.width = `${Math.max(10, ((sentScore + 1) / 2) * 100)}%`;
        }

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

        // Sync Strategy TF dropdown
        const tfSelector = document.getElementById('strategyTf');
        if (tfSelector && data.strategyTimeframe && tfSelector.value !== data.strategyTimeframe) {
            tfSelector.value = data.strategyTimeframe;
        }

        // If no ticker selected yet, pick the primary one from backend
        if (!selectedTicker && data.primaryTicker) {
            selectedTicker = data.primaryTicker;
        }

        // Update Title Label (force sync every refresh)
        if (selectedTicker) {
            const labelEl = document.getElementById('chartTickerLabel');
            if (labelEl) labelEl.textContent = selectedTicker;
        }

        // Get selected ticker scan
        const activeScan = data.watchlistScans?.[selectedTicker];

        // Chart update logic
        if (!tvWidget && selectedTicker) {
            initChart(selectedTicker, currentBackendTf);
        } else if (tvWidget && data.priceHistory && data.priceHistory.length > 0) {
            // Priority: Use the price history that came directly from the dashboard call
            updateChart(data.priceHistory, selectedTicker, activeScan?.signals || data.signals);
        } else if (tvWidget && activeScan) {
            // Fallback: If dashboard was empty but we have an active scan, fetch history manually
            fetchTickerChart(selectedTicker, activeScan.signals);
        }

        // Signals
        if (activeScan) {
            renderSignals(
                activeScan.signals,
                activeScan.action,
                activeScan.reason,
                activeScan.bullish_count,
                activeScan.bearish_count
            );
            updateSizingPanel(activeScan);
        } else if (data.signals) {
            renderSignals(data.signals, 'HOLD', 'Waiting for scan...', 0, 0);
        }

        // Watchlist & Tradelist
        renderTradelist(data.watchlistScans, data.tradelist, data.tickerAmounts);
        renderWatchlist(data.watchlistScans, data.watchlist, data.tradelist);
        
        // Cache data for settings modals to access
        window.lastDashboardData = data;

        // Trade log
        latestScanHistory = data.recentTrades || [];
        latestExecutedTrades = data.executedTrades || [];
        renderTradeLog(latestScanHistory, latestExecutedTrades);

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
    if (!isAlpacaLinked) {
        console.log('[chart] Skip fetch: Alpaca not linked.');
        return;
    }
    try {
        const headers = await getAuthHeaders();
        const response = await fetch(`${API_BASE}/api/scan/${ticker}?timeframe=${currentBackendTf}`, { headers });
        if (!response.ok) return;
        const data = await response.json();
        if (data.price_history) {
            updateChart(data.price_history, ticker, signals || data.signals);
        }
    } catch (e) {
        console.log('[chart] Could not fetch ticker data:', e);
    }
}

// ──────────────────────────────────────────────
// 9. Event Listeners (attached in DOMContentLoaded below)
// ──────────────────────────────────────────────
function attachEventListeners() {
    const tickerSearch = document.getElementById('tickerSearch');
    if (tickerSearch) {
        // Handle selecting from dropdown or pressing Enter
        tickerSearch.addEventListener('change', (e) => {
            const val = e.target.value.trim().toUpperCase();
            if (val && val !== selectedTicker) {
                selectedTicker = val;
                document.getElementById('chartTickerLabel').textContent = selectedTicker;
                initChart(selectedTicker, "1D");
                fetchTickerChart(selectedTicker);
                updateFavoriteIcon();
            }
        });

        // Ensure Enter key triggers the change if it didn't automatically
        tickerSearch.addEventListener('keypress', (e) => {
            if (e.key === 'Enter') {
                e.preventDefault();
                tickerSearch.blur(); // Triggers change event
            }
        });
    }

    const favoriteBtn = document.getElementById('favoriteBtn');
    if (favoriteBtn) {
        favoriteBtn.addEventListener('click', async () => {
            if (!selectedTicker) return;

            // Toggle in backend
            const datalist = document.getElementById('watchlistData');
            const currentWatchlist = Array.from(datalist?.options || []).map(o => o.value);

            const isRemoving = currentWatchlist.includes(selectedTicker);

            try {
                const headers = await getAuthHeaders();
                if (isRemoving) {
                    await fetch(`${API_BASE}/api/watchlist/${selectedTicker}`, {
                        method: 'DELETE',
                        headers: headers
                    });
                } else {
                    await fetch(`${API_BASE}/api/watchlist`, {
                        method: 'POST',
                        headers: headers,
                        body: JSON.stringify({ ticker: selectedTicker })
                    });
                }

                // Sync local storage for faster UI feedback and fallback
                let favorites = JSON.parse(localStorage.getItem('userFavorites')) || currentWatchlist;
                if (isRemoving) {
                    favorites = favorites.filter(t => t !== selectedTicker);
                } else {
                    if (!favorites.includes(selectedTicker)) favorites.push(selectedTicker);
                }
                localStorage.setItem('userFavorites', JSON.stringify(favorites));

                fetchDashboard(); // Trigger UI update
            } catch (e) {
                console.error('Error toggling favorite:', e);
            }
        });
    }
}

function updateFavoriteIcon() {
    const icon = document.getElementById('favIcon');
    if (!icon || !selectedTicker) return;

    // Check current datalist (which represents the active watchlist)
    const datalist = document.getElementById('watchlistData');
    const currentWatchlist = Array.from(datalist?.options || []).map(o => o.value);

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
// ──────────────────────────────────────────────
async function toggleIndicator(key, value) {
    try {
        const headers = await getAuthHeaders();
        await fetch(`${API_BASE}/api/settings/indicators`, {
            method: 'POST',
            headers: headers,
            body: JSON.stringify({ [key]: value })
        });

        fetchDashboard(); // Refresh chart and dashboard immediately
    } catch (e) {
        console.error('Error saving setting:', e);
    }
}

async function updateStrategyTf(newTf) {
    try {
        // Show loader immediately
        const loading = document.getElementById('gridLoading');
        if (loading) loading.classList.remove('hidden');

        const headers = await getAuthHeaders();
        const response = await fetch(`${API_BASE}/api/settings/timeframe`, {
            method: 'POST',
            headers: headers,
            body: JSON.stringify({ timeframe: newTf })
        });

        if (response.ok) {
            console.log(`[settings] Timeframe updated to ${newTf}`);
            // Update global state so future chart/dashboard calls use it
            currentBackendTf = newTf;
            // Force a refresh to update everything with new timeframe
            fetchDashboard();
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

    // Chart will be initialized dynamically by fetchDashboard() once primary bot is known
    console.log('[dashboard] Waiting for backend data to initialize chart...');

    // Initial data fetch once auth is ready
    const checkAuth = setInterval(() => {
        if (window.auth && window.auth.currentUser) {
            console.log('[dashboard] Auth ready. Starting data stream.');
            fetchDashboard();
            setInterval(fetchDashboard, REFRESH_INTERVAL);
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

    console.log('[dashboard] AI Trading Bot Dashboard initialized.');
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
        return;
    }

    slider.disabled = false;
    slider.min = 1;
    slider.max = checkedCount;

    // If current value is higher than new max, cap it
    if (parseInt(slider.value) > checkedCount) {
        slider.value = checkedCount;
    }

    updateBtAggressiveness(slider.value);
}

function updateBtAggressiveness(val) {
    const slider = document.getElementById('btAggressiveSlider');
    const max = parseInt(slider.max) || 1;
    const label = document.getElementById('btAggressiveLabel');
    const buyInp = document.getElementById('btThreshold');
    const sellInp = document.getElementById('btSellThreshold');

    val = parseInt(val);
    buyInp.value = val;
    sellInp.value = val;

    const pct = Math.round((val / max) * 100);
    let mode = "Balanced";
    let colorClass = "bg-indigo-600";

    if (pct <= 34) {
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
            if (typeof fetchDashboard === 'function') fetchDashboard();
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
