// ═══════════════════════════════════════════════════
// AI Trading Bot — Dashboard JS
// Auto-refreshing, multi-panel, real-time dashboard
// ═══════════════════════════════════════════════════

// 6. Smart API Configuration
// Auto-switches between Localhost (laptop) and Google Cloud (production)
const CLOUD_API_URL = 'https://ai-trade-bot-backend-1077198186521.us-central1.run.app'; // Update this if your URL changed
const API_BASE = (window.location.hostname === 'localhost' || window.location.hostname === '127.0.0.1') 
    ? 'http://localhost:8000' 
    : CLOUD_API_URL;
const REFRESH_INTERVAL = 15000; // 15 seconds
let selectedTicker = null; // Start null to force sync with Active Bots
let tvWidget = null;
let currentBackendTf = "5Min";
let currentLogTab = "all"; // 'all' or 'trades'
let latestTradesData = []; // Cached log data

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
        "timezone": "Etc/UTC",
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
function renderWatchlist(scans, watchlist) {
    const container = document.getElementById('watchlistContainer');
    container.innerHTML = '';

    const tickers = watchlist || Object.keys(scans || {});

    tickers.forEach(ticker => {
        const scan = (scans || {})[ticker];
        const item = document.createElement('div');
        item.className = `watchlist-item group ${selectedTicker === ticker ? 'active' : ''}`;

        const action = scan?.action || '—';
        const price = scan?.price ? `$${parseFloat(scan.price.toString().replace('$', '')).toFixed(2)}` : '—';
        const actionColor = action === 'BUY' ? 'text-emerald-600' : action === 'SELL' ? 'text-red-500' : 'text-purple-500';

        const bullish = scan?.bullish_count || 0;
        const total = scan?.total_signals || 0;

        item.innerHTML = `
            <div class="flex items-center justify-between flex-grow cursor-pointer" onclick="selectTicker('${ticker}')">
                <div>
                    <span class="font-bold text-sm text-indigo-900">${ticker}</span>
                    <span class="text-xs text-purple-500 ml-2">${price}</span>
                </div>
                <div class="flex items-center gap-2">
                    <span class="text-[0.6rem] text-purple-400">${bullish}/${total}</span>
                    <span class="font-bold text-xs ${actionColor}">${action}</span>
                </div>
            </div>
            <div class="flex items-center gap-1 ml-2">
                <button class="watchlist-action text-emerald-500 hover:bg-emerald-50" title="Activate Bot for ${ticker}" onclick="addToTradelist('${ticker}')">
                    <svg xmlns="http://www.w3.org/2000/svg" class="h-4 w-4" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                        <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M14.752 11.168l-3.197-2.132A1 1 0 0010 9.87v4.263a1 1 0 001.555.832l3.197-2.132a1 1 0 000-1.664z" />
                    </svg>
                </button>
                <button class="watchlist-delete text-red-400 hover:bg-red-50" title="Remove ${ticker}" onclick="removeFromWatchlist('${ticker}')">
                    <svg xmlns="http://www.w3.org/2000/svg" class="h-4 w-4" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                        <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M6 18L18 6M6 6l12 12" />
                    </svg>
                </button>
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
        const response = await fetch(`${API_BASE}/api/watchlist/${ticker}`, {
            method: 'DELETE'
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
        const amount = tickerAmounts[ticker] || '';
        const item = document.createElement('div');
        item.className = `watchlist-item group active-bot ${selectedTicker === ticker ? 'active' : ''} flex items-center gap-2`;

        const action = scan?.action || '—';
        const price = scan?.price ? `$${parseFloat(scan.price.toString().replace('$', '')).toFixed(2)}` : '—';
        const actionColor = action === 'BUY' ? 'text-emerald-600' : action === 'SELL' ? 'text-red-500' : 'text-purple-500';

        item.innerHTML = `
            <div class="flex items-center justify-between flex-grow cursor-pointer" onclick="selectTicker('${ticker}')">
                <div class="flex items-center">
                    <div class="h-2 w-2 bg-emerald-500 rounded-full mr-2 animate-pulse"></div>
                    <span class="font-bold text-sm text-indigo-900">${ticker}</span>
                </div>
                <div class="flex items-center gap-2">
                    <span class="text-xs text-purple-500">${price}</span>
                    <span class="font-bold text-xs ${actionColor}">${action}</span>
                </div>
            </div>
            


            <button class="text-indigo-400 hover:text-indigo-600 p-1 rounded hover:bg-indigo-50 transition-all" 
                title="Individual Risk Settings for ${ticker}" 
                onclick="event.stopPropagation(); openTickerModal('${ticker}')">
                <svg xmlns="http://www.w3.org/2000/svg" class="h-4 w-4" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                    <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M10.325 4.317c.426-1.756 2.924-1.756 3.35 0a1.724 1.724 0 002.573 1.066c1.543-.94 3.31.826 2.37 2.37a1.724 1.724 0 001.065 2.572c1.756.426 1.756 2.924 0 3.35a1.724 1.724 0 00-1.066 2.573c.94 1.543-.826 3.31-2.37 2.37a1.724 1.724 0 00-2.572 1.065c-.426 1.756-2.924 1.756-3.35 0a1.724 1.724 0 00-2.573-1.066c-1.543.94-3.31-.826-2.37-2.37a1.724 1.724 0 00-1.065-2.572c-1.756-.426-1.756-2.924 0-3.35a1.724 1.724 0 001.066-2.573c-.94-1.543.826-3.31 2.37-2.37.996.608 2.296.07 2.572-1.065z" />
                    <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M15 12a3 3 0 11-6 0 3 3 0 016 0z" />
                </svg>
            </button>

            <button class="watchlist-delete text-red-400 hover:bg-red-50" title="Deactivate Bot for ${ticker}" onclick="removeFromTradelist('${ticker}')">
                <svg xmlns="http://www.w3.org/2000/svg" class="h-4 w-4" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                    <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M10 9v6m4-6v6" />
                </svg>
            </button>
        `;
        container.appendChild(item);
    });
}

async function updateTickerAmount(ticker, amount) {
    try {
        await fetch(`${API_BASE}/api/settings/ticker_amount`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
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
        const response = await fetch(`${API_BASE}/api/tradelist`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ ticker: ticker })
        });
        if (response.ok) fetchDashboard();
    } catch (e) {
        console.error('Error adding to tradelist:', e);
    }
}

async function removeFromTradelist(ticker) {
    try {
        const response = await fetch(`${API_BASE}/api/tradelist/${ticker}`, {
            method: 'DELETE'
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

function setLogTab(tab) {
    currentLogTab = tab;

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
    tbody.innerHTML = '';

    const trades = currentLogTab === 'trades' ? (executedTrades || []) : (scanHistory || []);

    if (!trades || trades.length === 0) {
        noMsg.style.display = 'block';
        noMsg.textContent = currentLogTab === 'trades' ? "No orders executed yet." : "Waiting for first scan...";
        return;
    }

    // Slice for display
    let filtered = trades.slice(0, 50);

    noMsg.style.display = 'none';

    filtered.forEach(trade => {
        const actionColor = trade.action === 'BUY'
            ? 'text-emerald-600 font-bold'
            : trade.action === 'SELL'
                ? 'text-red-500 font-bold'
                : 'text-purple-500 font-medium';

        const signalBadge = trade.bullish_count !== undefined
            ? `${trade.bullish_count}B / ${trade.bearish_count}S`
            : '—';

        const row = document.createElement('tr');
        row.className = 'border-b border-purple-100 fade-in';
        row.innerHTML = `
            <td class="py-2.5 pr-4 text-purple-600 text-xs">${trade.time}</td>
            <td class="py-2.5 pr-4 ${actionColor} text-xs">${trade.action}</td>
            <td class="py-2.5 pr-4 text-indigo-950 font-semibold text-xs">${trade.ticker}</td>
            <td class="py-2.5 pr-4 text-indigo-700 font-medium text-xs">${trade.price}</td>
            <td class="py-2.5 pr-4 text-xs">
                <span class="px-1.5 py-0.5 rounded bg-purple-100 text-purple-700 font-mono text-[0.6rem]">${signalBadge}</span>
            </td>
            <td class="py-2.5 pr-4 text-purple-500 italic text-xs max-w-xs truncate" title="${trade.reason}">${trade.reason}</td>
        `;
        tbody.appendChild(row);
    });
}

// ──────────────────────────────────────────────
// 6. Update Risk Panel
// ──────────────────────────────────────────────


// ──────────────────────────────────────────────
// 7. Update Position Sizing Panel
// ──────────────────────────────────────────────
function updateSizingPanel(scan) {
    const panel = document.getElementById('sizingPanel');
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
        const response = await fetch(`${API_BASE}/api/settings/ticker`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
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
        await fetch(`${API_BASE}/api/settings/ticker/${currentEditingTicker}`, { method: 'DELETE' });
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

        const response = await fetch(url);
        if (!response.ok) throw new Error(`HTTP ${response.status}`);
        const data = await response.json();

        // Set default selected ticker
        if (!selectedTicker && data.watchlist?.length > 0) {
            selectedTicker = data.primaryTicker || data.watchlist[0];
        }

        // Portfolio stats
        const capitalEl = document.getElementById('totalCapital');
        const openPosEl = document.getElementById('openPositions');
        const plEl = document.getElementById('dailyPL');
        const sentEl = document.getElementById('aiSentiment');

        if (capitalEl) {
            capitalEl.textContent = data.capital || '---';
            capitalEl.classList.remove('animate-pulse');
        }
        const cashEl = document.getElementById('cashDisplay');
        if (cashEl) cashEl.textContent = `Cash: ${data.cash || '---'}`;

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

        const summaryEl = document.getElementById('aiSummary');
        if (summaryEl) {
            if (data.sentimentSummary) {
                summaryEl.textContent = data.sentimentSummary;
                summaryEl.classList.remove('italic', 'text-purple-400');
            } else {
                summaryEl.textContent = 'Waiting for scan analysis...';
                summaryEl.classList.add('italic', 'text-purple-400');
            }
        }

        const sentBar = document.getElementById('sentimentBar');
        if (sentBar) {
            const sentScore = data.sentimentConfidence || 0.5;
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
        if (data.simulation) {
            modeEl.innerHTML = '<span class="h-2.5 w-2.5 bg-amber-500 rounded-full mr-2 animate-pulse"></span> Simulation Mode';
        } else {
            modeEl.innerHTML = '<span class="h-2.5 w-2.5 bg-green-500 rounded-full mr-2 animate-pulse"></span> Live Trading';
        }

        // Last scan
        document.getElementById('lastScanTime').textContent = data.lastScan || '—';

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

        // Chart
        if (!tvWidget && selectedTicker) {
            initChart(selectedTicker, currentBackendTf);
        } else if (tvWidget && data.priceHistory?.length > 0 && selectedTicker === data.primaryTicker) {
            updateChart(data.priceHistory, selectedTicker, activeScan?.signals || data.signals);
        } else if (tvWidget && activeScan) {
            // Try to get price history from a manual scan
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
        renderWatchlist(data.watchlistScans, data.watchlist);

        // Risk
        
        // Cache data for settings modals to access
        window.lastDashboardData = data;

        // Trade log
        latestScanHistory = data.recentTrades || [];
        latestExecutedTrades = data.executedTrades || [];
        renderTradeLog(latestScanHistory, latestExecutedTrades);

    } catch (error) {
        console.error('[dashboard] Fetch error:', error);
        document.getElementById('lastScanTime').textContent = 'Connection error';
        showGlobalError('Cannot connect to the trading backend. Ensure "python backend/main.py" is running.');
    }
}

async function fetchTickerChart(ticker, signals) {
    try {
        const response = await fetch(`${API_BASE}/api/scan/${ticker}?timeframe=${currentBackendTf}`);
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
                if (isRemoving) {
                    await fetch(`${API_BASE}/api/watchlist/${selectedTicker}`, { method: 'DELETE' });
                } else {
                    await fetch(`${API_BASE}/api/watchlist`, {
                        method: 'POST',
                        headers: { 'Content-Type': 'application/json' },
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
        await fetch(`${API_BASE}/api/settings/indicators`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
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

        const response = await fetch(`${API_BASE}/api/settings/timeframe`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
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

    // Initial data fetch
    fetchDashboard();

    // Auto-refresh every 15s
    setInterval(fetchDashboard, REFRESH_INTERVAL);

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
        const response = await fetch(`${API_BASE}/api/backtest`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
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
