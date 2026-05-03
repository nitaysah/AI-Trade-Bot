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

// 1. Initialization
document.addEventListener('DOMContentLoaded', () => {
    // Attach listeners to indicator checkboxes for dynamic slider
    document.querySelectorAll('.bt-indicator-check').forEach(cb => {
        cb.addEventListener('change', syncBacktestSliderRange);
    });

    // Initial slider sync
    syncBacktestSliderRange();
    console.log('[backtest] Center Initialized.');
});

// 2. Strategy Execution
async function runBacktest() {
    const ticker = document.getElementById('btTicker').value.toUpperCase();
    const timeframe = document.getElementById('btTimeframe').value;
    const days = document.getElementById('btDays').value;
    const capital = document.getElementById('btCapital').value;
    const threshold = document.getElementById('btThreshold').value;
    const sellThreshold = document.getElementById('btSellThreshold').value;

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
        const response = await fetch(`${API_BASE}/api/backtest`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                ticker,
                timeframe,
                days: parseInt(days),
                capital: parseFloat(capital),
                threshold: parseInt(threshold),
                sell_threshold: parseInt(sellThreshold),
                indicators: indicators // Pass the manual selection to backend
            })
        });

        const data = await response.json();

        if (data.status === 'success') {
            displayResults(data.results);
        } else {
            // Handle both structured error objects and raw error fields
            const errMsg = data.message || data.error || "Unknown error";
            alert("Backtest Failed: " + errMsg);
            resetBacktestUI();
        }
    } catch (err) {
        console.error("BACKTEST FETCH ERROR:", err);
        alert("Connection to AI Backend failed. Check console for details.");
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
        tbody.innerHTML = '<tr><td colspan="9" class="p-10 text-center text-indigo-300 font-bold italic">No trades executed with current strategy parameters.</td></tr>';
        return;
    }

    paginated.forEach(t => {
        const plClass = t.pl_pct >= 0 ? 'text-emerald-500' : 'text-rose-500';
        const row = `
            <tr class="hover:bg-indigo-50/50 transition-all border-b border-indigo-50/30">
                <td class="p-8">
                    <div class="font-black text-indigo-950 text-sm tracking-tight">${formatDate(t.entry_time)}</div>
                    <div class="text-[0.65rem] text-indigo-300 font-bold uppercase mt-1">Entry @ $${t.entry_price.toFixed(2)}</div>
                </td>
                <td class="p-8">
                    <div class="font-black text-indigo-950 text-sm tracking-tight">${formatDate(t.exit_time)}</div>
                    <div class="text-[0.65rem] text-indigo-300 font-bold uppercase mt-1">Exit @ $${t.exit_price.toFixed(2)}</div>
                </td>
                <td class="p-8 text-center">
                    <span class="px-4 py-1.5 rounded-xl bg-indigo-100/50 text-indigo-600 text-[0.65rem] font-black uppercase tracking-widest">LONG</span>
                </td>
                <td class="p-8 text-center font-black text-indigo-950">${t.qty.toFixed(4)}</td>
                <td class="p-8 text-center font-bold text-indigo-400">$${t.entry_cost.toFixed(2)}</td>
                <td class="p-8 text-center font-bold text-indigo-950">$${t.exit_value.toFixed(2)}</td>
                <td class="p-8 text-center text-indigo-300 font-medium">$${(t.fees || 0).toFixed(2)}</td>
                <td class="p-8 text-center font-black ${plClass} text-base">${t.pl_pct >= 0 ? '+' : ''}${t.pl_pct.toFixed(2)}%</td>
                <td class="p-8">
                    <div class="text-[0.65rem] font-black text-indigo-400 leading-relaxed uppercase max-w-[140px] bg-indigo-50/50 p-3 rounded-2xl border border-indigo-100/50">${t.reason}</div>
                </td>
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
    const date = new Date(ds);
    return date.toLocaleDateString(undefined, { month: 'short', day: 'numeric' }) + ' ' +
        date.toLocaleTimeString(undefined, { hour: '2-digit', minute: '2-digit', hour12: false });
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
