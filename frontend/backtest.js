// ──────────────────────────────────────────────
// BACKTEST CENTER LOGIC v2.0
// ──────────────────────────────────────────────

const CLOUD_URL = 'https://ai-trade-bot-backend-946557219642.us-central1.run.app';
const API_BASE = window.location.hostname === 'localhost' || window.location.hostname === '127.0.0.1' 
    ? 'http://localhost:8000' 
    : CLOUD_URL;
let allBtTrades = [];
let btPage = 1;
const btPageSize = 10;
window.lastDashboardData = null;

/**
 * Retrieves the current Firebase ID token and prepares headers.
 */
async function getAuthHeaders() {
    // Use the globally exposed auth instance from the HTML module block
    const auth = window.auth;
    const user = auth ? auth.currentUser : null;

    if (!user) return { 'Content-Type': 'application/json' };
    const token = await user.getIdToken();
    return {
        'Content-Type': 'application/json',
        'Authorization': `Bearer ${token}`
    };
}

// 1. Initialization
document.addEventListener('DOMContentLoaded', () => {
    // Attach listeners to indicator checkboxes for dynamic slider
    document.querySelectorAll('.bt-indicator-check').forEach(cb => {
        cb.addEventListener('change', syncBacktestSliderRange);
    });

    // Sync timeframe select change to days input value dynamically
    const btTfSelect = document.getElementById('btTimeframe');
    const btDaysInput = document.getElementById('btDays');
    if (btTfSelect && btDaysInput) {
        const updateDaysVal = () => {
            const tf = btTfSelect.value;
            if (tf === '1Min') btDaysInput.value = 7;
            else if (['5Min', '15Min', '30Min'].includes(tf)) btDaysInput.value = 60;
            else if (tf === '1Hour') btDaysInput.value = 365;
            else if (tf === '4Hour') btDaysInput.value = 730;
            else if (tf === '1Day') btDaysInput.value = 1825;
        };
        btTfSelect.addEventListener('change', updateDaysVal);
        // Trigger initially so it matches the default selected dropdown value
        updateDaysVal();
    }

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
                    const res = await fetch(`${API_BASE}/api/search/${encodeURIComponent(query)}`, { headers });
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

        // Hide dropdown when clicking outside
        document.addEventListener('click', (e) => {
            if (!btTickerInput.contains(e.target) && !btResultsContainer.contains(e.target)) {
                btResultsContainer.classList.add('hidden');
            }
        });
    }

    // Initial slider sync
    syncBacktestSliderRange();
    console.log('[backtest] Center Initialized.');

    // Start Alpaca Status Polling
    pollAlpacaStatus();
    setInterval(pollAlpacaStatus, 30000); // Check every 30s
});

async function pollAlpacaStatus() {
    const statusPill = document.getElementById('alpacaLinkStatus');
    const statusDot = document.getElementById('alpacaStatusDot');
    const statusText = document.getElementById('alpacaStatusText');
    if (!statusPill) return;

    try {
        const headers = await getAuthHeaders();
        const response = await fetch(`${API_BASE}/api/dashboard`, { headers });
        const data = await response.json();
        window.lastDashboardData = data;

        if (data.simulation) {
            if (data.has_keys) {
                statusPill.className = "flex items-center text-sm font-black px-8 py-3 rounded-full bg-amber-50 text-amber-600 border-2 border-amber-100 shadow-sm whitespace-nowrap transition-all duration-500 uppercase tracking-wider";
                statusDot.className = "h-2.5 w-2.5 rounded-full mr-2.5 bg-amber-500 animate-pulse";
                statusText.textContent = "Connection Failed (Retrying...)";
            } else {
                statusPill.className = "flex items-center text-sm font-black px-8 py-3 rounded-full bg-rose-50 text-rose-600 border-2 border-rose-100 shadow-sm whitespace-nowrap transition-all duration-500 uppercase tracking-wider";
                statusDot.className = "h-2.5 w-2.5 rounded-full mr-2.5 bg-rose-500 animate-pulse";
                statusText.textContent = "Alpaca Not Linked";
            }
        } else {
            statusPill.className = "flex items-center text-sm font-black px-8 py-3 rounded-full bg-emerald-50 text-emerald-600 border-2 border-emerald-100 shadow-sm whitespace-nowrap transition-all duration-500 uppercase tracking-wider";
            statusDot.className = "h-2.5 w-2.5 rounded-full mr-2.5 bg-emerald-500 animate-pulse";
            statusText.textContent = "Alpaca Live";
        }
    } catch (e) {
        console.error('[backtest] Alpaca status check failed:', e);
    }
}

window.logoutCommander = async () => {
    const auth = window.auth;
    if (auth) {
        const { signOut } = await import("https://www.gstatic.com/firebasejs/10.7.1/firebase-auth.js");
        await signOut(auth);
    }
    window.location.href = "index.html";
};

// 2. Strategy Execution
async function runBacktest() {
    const ticker = document.getElementById('btTicker').value.toUpperCase();
    const timeframe = document.getElementById('btTimeframe').value;
    const days = document.getElementById('btDays').value;
    const capital = document.getElementById('btCapital').value;
    const threshold = document.getElementById('btThreshold').value;
    const sellThreshold = document.getElementById('btSellThreshold').value;

    const extHoursVal = document.getElementById('btExtHours') ? (document.getElementById('btExtHours').value === 'true') : true;

    // Collect checked indicators
    const indicators = Array.from(document.querySelectorAll('.bt-indicator-check:checked'))
        .map(cb => cb.value);

    if (indicators.length === 0) {
        alert("Please select at least one indicator for the strategy matrix.");
        return;
    }

    // UI Feedback
    document.getElementById('btSettingsContainer').classList.add('hidden');
    document.getElementById('btResults').classList.add('hidden');
    document.getElementById('btLoading').classList.remove('hidden');

    try {
        console.log(`[backtest] Starting request to: ${API_BASE}/api/backtest`);
        const headers = await getAuthHeaders();
        const response = await fetch(`${API_BASE}/api/backtest`, {
            method: 'POST',
            headers: headers,
            body: JSON.stringify({
                ticker,
                timeframe,
                days: parseInt(days),
                capital: parseFloat(capital),
                threshold: parseInt(threshold),
                sell_threshold: parseInt(sellThreshold),
                indicators: indicators,
                ext_hours: extHoursVal
            })
        });

        if (!response.ok) {
            throw new Error(`HTTP Error: ${response.status}`);
        }

        const data = await response.json();
        console.log('[backtest] Response received:', data.status);

        if (data.status === 'success') {
            displayResults(data.results);
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

    // Stats
    document.getElementById('resRoi').textContent = s.roi_pct.toFixed(2) + '%';
    document.getElementById('resWinRate').textContent = s.win_rate_pct.toFixed(1) + '%';
    document.getElementById('resTrades').textContent = s.total_trades;
    document.getElementById('resEquity').textContent = '$' + s.final_equity.toLocaleString(undefined, { minimumFractionDigits: 2 });
    document.getElementById('resHoldEquity').textContent = '$' + s.buy_hold_equity.toLocaleString(undefined, { minimumFractionDigits: 2 });
    document.getElementById('resHoldRoi').textContent = s.buy_hold_roi_pct.toFixed(2) + '% Return';

    // Color logic for ROI
    const roiEl = document.getElementById('resRoi');
    roiEl.className = `text-4xl font-black tracking-tighter ${s.roi_pct >= 0 ? 'text-emerald-500' : 'text-rose-500'}`;

    const holdRoiEl = document.getElementById('resHoldRoi');
    const holdColor = s.buy_hold_roi_pct >= 0 ? 'text-emerald-400 bg-emerald-400/10 border-emerald-400/20' : 'text-rose-400 bg-rose-400/10 border-rose-400/20';
    holdRoiEl.className = `text-[0.7rem] font-black uppercase tracking-widest px-4 py-1.5 rounded-full inline-block mt-3 border transition-all ${holdColor}`;

    // Trade Log
    allBtTrades = res.trades || [];
    btPage = 1;
    renderBtTradesPage();
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
        document.getElementById('btPageInfo').textContent = `Page ${btPage} of ${totalPages}`;
        document.getElementById('btPageInfoTop').textContent = `Showing ${start + 1}-${Math.min(end, allBtTrades.length)} of ${allBtTrades.length} trades`;
    } else {
        document.getElementById('btPagination').classList.add('hidden');
        tbody.innerHTML = '<tr><td colspan="7" class="py-12 text-center text-purple-400 text-xs md:text-sm font-medium italic">No strategy executions detected with current parameters.</td></tr>';
        return;
    }

    paginated.forEach(t => {
        const plClass = t.pl_pct >= 0 ? 'text-emerald-600 font-bold' : 'text-red-500 font-bold';
        const row = `
            <tr class="border-b border-purple-100 hover:bg-purple-50/30 transition-colors">
                <td class="py-3 px-4">
                    <div class="text-xs font-semibold text-indigo-950">${formatDate(t.entry_time)}</div>
                    <div class="text-[0.6rem] text-purple-400 font-medium mt-0.5">Entry @ $${t.entry_price.toFixed(2)}</div>
                </td>
                <td class="py-3 px-4">
                    <div class="text-xs font-semibold text-indigo-950">${formatDate(t.exit_time)}</div>
                    <div class="text-[0.6rem] text-purple-400 font-medium mt-0.5">Exit @ $${t.exit_price.toFixed(2)}</div>
                </td>
                <td class="py-3 px-4 text-center text-xs font-bold text-indigo-900">${t.qty.toFixed(4)}</td>
                <td class="py-3 px-4 text-center text-xs font-medium text-indigo-700">$${t.entry_cost.toFixed(2)}</td>
                <td class="py-3 px-4 text-center text-xs font-bold text-indigo-950">$${t.exit_value.toFixed(2)}</td>
                <td class="py-3 px-4 text-center text-[0.65rem] text-purple-400 font-mono">$${(t.fees || 0).toFixed(2)}</td>
                <td class="py-3 px-4 text-center text-xs ${plClass}">${t.pl_pct >= 0 ? '+' : ''}${t.pl_pct.toFixed(2)}%</td>
            </tr>
        `;
        tbody.insertAdjacentHTML('beforeend', row);
    });
}

function changeBtPage(dir) {
    const totalPages = Math.ceil(allBtTrades.length / btPageSize);
    const newPage = btPage + dir;
    if (newPage >= 1 && newPage <= totalPages) {
        btPage = newPage;
        renderBtTradesPage();
    }
}

// 4. Helper Functions
function resetBacktestUI() {
    document.getElementById('btResultsModal').classList.add('hidden');
    document.getElementById('btResults').classList.add('hidden');
    document.getElementById('btSettingsContainer').classList.remove('hidden');
    document.getElementById('runBtBtn').disabled = false;
    document.getElementById('btLoading').classList.add('hidden');
}

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
        label.className = "text-[0.75rem] font-black px-5 py-2 rounded-full bg-rose-500 text-white uppercase shadow-md";
        return;
    }

    slider.disabled = false;
    slider.min = 1;
    slider.max = checkedCount;

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
    let colorClass = "bg-indigo-100 text-indigo-600";

    if (pct <= 34) {
        mode = "Aggressive";
        colorClass = "bg-rose-100 text-rose-600";
    } else if (pct >= 75) {
        mode = "Quality";
        colorClass = "bg-emerald-100 text-emerald-600";
    }

    if (val === max && max > 1) {
        mode = "Ultra-Quality";
        colorClass = "bg-emerald-600 text-white"; // High contrast green for max quality
    }

    label.textContent = `${mode} (${val} of ${max} signals)`;
    label.className = `text-[0.65rem] font-black px-4 py-1.5 rounded-full ${colorClass} shadow-sm uppercase tracking-wider transition-all duration-300`;
}

function formatDate(ds) {
    if (!ds) return "---";
    // If naive, treat as UTC
    let timestamp = ds;
    if (ds && !ds.includes('Z') && !/[+-]\d{2}:\d{2}$/.test(ds)) {
        timestamp += 'Z';
    }
    const date = new Date(timestamp);
    // Force Central Time (Chicago)
    const options = { 
        timeZone: 'America/Chicago',
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
        const response = await fetch(`${API_BASE}/api/download_all`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
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
