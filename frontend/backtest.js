
function getIndicatorOverrides() {
    try {
        const prefs = localStorage.getItem('globalIndicatorPrefs');
        if (prefs) {
            return JSON.parse(prefs);
        }
    } catch (e) {
        console.error('Failed to parse indicator prefs:', e);
    }
    return {};
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
// ──────────────────────────────────────────────
// BACKTEST CENTER LOGIC v2.0
// ──────────────────────────────────────────────

var CLOUD_URL = 'https://207-211-184-22.sslip.io';
var API_BASE = window.location.hostname === 'localhost' || window.location.hostname === '127.0.0.1' 
    ? 'http://localhost:8000' 
    : CLOUD_URL;
let allBtTrades = [];
let btPage = 1;
const btPageSize = 10;
window.lastBacktestData = null;
window.lastBacktestRun = null;

// ── Backtest Chart State ─────────────────────────────────────────────────
let btChart = null;            // LightweightCharts chart instance
let btCandleSeries = null;     // Candlestick series
let btEqSeries = null;         // Equity curve line series
let btVolumeSeries = null;     // Volume histogram series
let btChartMarkersRef = [];    // Saved markers array for toggling
let btChartTradeIdx = 0;       // Currently highlighted trade (0-based, newest-first order)
let btChartTradesRef = [];     // Reference to trades array (newest-first, same as allBtTrades)
let btChartCandlesRef = [];    // Reference to candles array
let btChartResizeObserver = null; // ResizeObserver for responsive chart
// ────────────────────────────────────────────────────────────────────────

// ── Backtest Cancellation on Unload ──────────────────────────────────────
window.addEventListener('beforeunload', () => {
    // Attempt to cancel any running backtest on the backend when navigating away or refreshing
    navigator.sendBeacon(`${API_BASE}/api/backtest/cancel`);
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
        const res = await fetch(`${API_BASE}/api/ollama/models`, { headers });
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
document.addEventListener('DOMContentLoaded', () => {
    // Attach listeners to indicator checkboxes for dynamic slider
    document.querySelectorAll('.strategy-indicator-check').forEach(cb => {
        cb.addEventListener('change', syncBacktestSliderRange);
    });

    // Sync timeframe select change to days input value dynamically
    const btDaysInput = document.getElementById('btDays');
    const btTfRegular = document.getElementById('strategyTimeframe_regular');
    const btTfOptions = document.getElementById('strategyTimeframe_options');
    if (btDaysInput) {
        const updateDaysVal = (e) => {
            const tf = e ? e.target.value : (currentBtMode === 'regular' ? btTfRegular?.value : btTfOptions?.value);
            if (!tf) return;
            if (tf === '1Min') btDaysInput.value = 7;
            else if (['5Min', '10Min', '15Min', '30Min'].includes(tf)) btDaysInput.value = 60;
            else if (tf === '1Hour') btDaysInput.value = 365;
            else if (tf === '4Hour') btDaysInput.value = 730;
            else if (tf === '1Day') btDaysInput.value = 1825;
        };
        btTfRegular?.addEventListener('change', updateDaysVal);
        btTfOptions?.addEventListener('change', updateDaysVal);
        updateDaysVal();
    }

    // Initialize/Sync strategy settings display on load
    setTimeout(() => {
        window.toggleBtMode(currentBtMode);
        window.updateBtRegularEntryFields?.();
        window.updateBtRegularExitFields?.();
        window.updateBtOptionsEntryFields?.();
        window.updateBtOptionsExitFields?.();
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
    const checkAuth = setInterval(() => {
        if (window.auth && (window.auth.currentUser || localStorage.getItem('dev_mode') === 'true')) {
            clearInterval(checkAuth);
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
        const response = await fetch(`${API_BASE}/api/dashboard?mode=fast&source=backtest`, { headers });
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
        const response = await fetch(`${API_BASE}/api/alpaca_config`, {
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

let currentBtMode = 'regular';

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
    
    // Enforce regular hours for options trading
    const extHoursSelect = document.getElementById('btExtHours');
    if (extHoursSelect) {
        if (mode === 'options') {
            extHoursSelect.value = 'false';
            extHoursSelect.disabled = true;
            extHoursSelect.classList.add('opacity-50', 'cursor-not-allowed');
        } else {
            extHoursSelect.disabled = false;
            extHoursSelect.classList.remove('opacity-50', 'cursor-not-allowed');
        }
    }
}

// 2. Strategy Execution
async function runBacktest() {
    const ticker = document.getElementById('btTicker').value.toUpperCase();
    const days = document.getElementById('btDays').value;
    const timeframe = currentBtMode === 'regular'
        ? document.getElementById('strategyTimeframe_regular').value
        : document.getElementById('strategyTimeframe_options').value;
    const capital = currentBtMode === 'regular'
        ? document.getElementById('strategyCapital_regular').value
        : document.getElementById('strategyOptionsTradeAmount').value;

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

    const extHoursVal = document.getElementById('btExtHours') ? (document.getElementById('btExtHours').value === 'true') : true;
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
    const indicators = Array.from(document.querySelectorAll(`${selectedIndicatorClass}:checked`))
        .map(cb => cb.value);

    if (!optionsEnabled && indicators.length === 0) {
        alert("Please select at least one indicator for the strategy matrix.");
        return;
    }
    if (optionsEnabled && indicators.length === 0) {
        alert("Please select at least one indicator for options entry/exit triggers.");
        return;
    }

    // UI Feedback
    document.getElementById('btSettingsContainer').classList.add('hidden');
    document.getElementById('btResults').classList.add('hidden');
    document.getElementById('btLoading').classList.remove('hidden');

    try {
        const headers = await getAuthHeaders();
        let response;

        if (optionsEnabled) {
            const tradeAmount = parseFloat(document.getElementById('strategyCapital_options')?.value || '1000');
            
            const optionsType = document.getElementById('strategyOptionsOptionType')?.value || 'both';
            const expiryDays = parseInt(document.getElementById('strategyOptionsExpiryDays')?.value || '3');
            const strikeType = document.getElementById('strategyOptionsStrikeType')?.value || 'ATM';
            const exitStrategy = document.getElementById('strategySellMode_options')?.value || 'indicator';
            const stopLossPct = parseFloat(document.getElementById('strategyOptionsSlPct')?.value || '50') / 100.0;
            const takeProfitPct = parseFloat(document.getElementById('strategyOptionsTpPct')?.value || '100') / 100.0;
            const trailPct = parseFloat(document.getElementById('strategyOptionsTrailPct')?.value || '3') / 100.0;
            const atrStopMult = parseFloat(document.getElementById('strategyAtrStopMult_options')?.value || '2');
            const atrTpMult = parseFloat(document.getElementById('strategyAtrTpMult_options')?.value || '4');
            const atrTrailMult = parseFloat(document.getElementById('strategyAtrTrailMult_options')?.value || '3');
            const buyMode = document.getElementById('strategyBuyMode_options')?.value || 'indicator';
            const customBuyPrice = parseFloat(document.getElementById('strategyCustomBuyPrice_options')?.value || '0');
            const customUnderlyingStopLossPrice = parseFloat(document.getElementById('strategyCustomUnderlyingStopLossPrice_options')?.value || '0');
            const customUnderlyingTakeProfitPrice = parseFloat(document.getElementById('strategyCustomUnderlyingTakeProfitPrice_options')?.value || '0');
            const enableRisk = document.getElementById('btEnableRisk_options')?.checked ?? true;
            const riskPerTrade = enableRisk ? (parseFloat(document.getElementById('btRiskPerTrade_options')?.value || '2.0') / 100.0) : 1.0;
            const maxPositionPct = enableRisk ? (parseFloat(document.getElementById('btMaxPositionPct_options')?.value || '100.0') / 100.0) : 1.0;
            const r = parseFloat(document.getElementById('strategyOptionsRiskFreeRate')?.value || '5.0') / 100.0;

            window.lastBacktestRun = {
                is_options: true,
                ticker, timeframe: timeframe, days: parseInt(days), capital: parseFloat(capital),
                trade_amount: tradeAmount, expiry_days: expiryDays, strike_type: strikeType,
                options_type: optionsType,
                stop_loss_pct: stopLossPct, take_profit_pct: takeProfitPct,
                exit_strategy: exitStrategy, close_before_expiry_hours: closeBeforeExpiry,
                custom_underlying_stop_loss_price: customUnderlyingStopLossPrice,
                custom_underlying_take_profit_price: customUnderlyingTakeProfitPrice,
                indicators, ext_hours: extHoursVal, r, results: null,
                enable_cooldown: enableCooldown,
                cooldown_minutes: cooldownMinutes,
                    indicator_overrides: getIndicatorOverrides()
            };

            console.log(`[backtest] Starting Options request to: ${API_BASE}/api/options/backtest`);
            response = await fetch(`${API_BASE}/api/options/backtest`, {
                method: 'POST',
                headers: headers,
                body: JSON.stringify({
                    ticker,
                    timeframe: timeframe,
                    days: parseInt(days),
                    capital: parseFloat(capital),
                    threshold: parseInt(document.getElementById('strategyBuyThreshold_options')?.value || '0'),
                    sell_threshold: parseInt(document.getElementById('strategySellThreshold_options')?.value || '1'),
                    trade_amount: tradeAmount,
                    expiry_days: expiryDays,
                    strike_type: strikeType,
                    options_type: optionsType,
                    stop_loss_pct: stopLossPct,
                    take_profit_pct: takeProfitPct,
                    regular_trailing_pct: trailPct,
                    atr_stop_multiplier: atrStopMult,
                    atr_take_profit_multiplier: atrTpMult,
                    atr_trail_multiplier: atrTrailMult,
                    buy_mode: buyMode,
                    custom_buy_price: customBuyPrice,
                    custom_underlying_stop_loss_price: customUnderlyingStopLossPrice,
                    custom_underlying_take_profit_price: customUnderlyingTakeProfitPrice,
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
                    indicator_overrides: getIndicatorOverrides()
                })
            });

        } else {
            // Gather exit strategy and risk controls for stock
            const sellMode = document.getElementById('strategySellMode_regular')?.value || 'indicator';
            const enableRisk = document.getElementById('btEnableRisk_regular')?.checked ?? true;
            const riskPerTrade = enableRisk ? (parseFloat(document.getElementById('btRiskPerTrade')?.value || '2.0') / 100.0) : 1.0;
            const maxPositionPct = enableRisk ? (parseFloat(document.getElementById('btMaxPositionPct')?.value || '100.0') / 100.0) : 1.0;
            const atrStopMult = parseFloat(document.getElementById('strategyAtrStopMult')?.value || '2');
            const atrTrailMult = parseFloat(document.getElementById('strategyAtrTrailMult')?.value || '3');
            const atrTpMult = parseFloat(document.getElementById('strategyAtrTpMult')?.value || '4');
            const regularSlPct = parseFloat(document.getElementById('strategyRegularSlPct')?.value || '5') / 100.0;
            const regularTpPct = parseFloat(document.getElementById('strategyRegularTpPct')?.value || '10') / 100.0;
            const regularTrailPct = parseFloat(document.getElementById('strategyRegularTrailPct')?.value || '3') / 100.0;
            const buyMode = document.getElementById('strategyBuyMode_regular')?.value || 'indicator';
            const customBuyPrice = parseFloat(document.getElementById('strategyCustomBuyPrice_regular')?.value || '0');

            window.lastBacktestRun = {
                is_options: false,
                ticker, timeframe, days: parseInt(days), capital: parseFloat(capital),
                indicators, ext_hours: extHoursVal,
                buy_mode: buyMode, custom_buy_price: customBuyPrice,
                sell_mode: sellMode, risk_per_trade: riskPerTrade,
                max_position_pct: maxPositionPct, atr_stop_multiplier: atrStopMult,
                atr_trail_multiplier: atrTrailMult, atr_take_profit_multiplier: atrTpMult,
                regular_stop_loss_pct: regularSlPct, regular_take_profit_pct: regularTpPct, regular_trailing_pct: regularTrailPct,
                enable_cooldown: enableCooldown, cooldown_minutes: cooldownMinutes,
                    indicator_overrides: getIndicatorOverrides(),
                results: null
            };

            console.log(`[backtest] Starting Stock request to: ${API_BASE}/api/backtest`);
            response = await fetch(`${API_BASE}/api/backtest`, {
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
                    ext_hours: extHoursVal,
                    buy_mode: buyMode,
                    custom_buy_price: customBuyPrice,
                    sell_mode: sellMode,
                    risk_per_trade: riskPerTrade,
                    max_position_pct: maxPositionPct,
                    atr_stop_multiplier: atrStopMult,
                    atr_trail_multiplier: atrTrailMult,
                    atr_take_profit_multiplier: atrTpMult,
                    regular_stop_loss_pct: regularSlPct,
                    regular_take_profit_pct: regularTpPct,
                    regular_trailing_pct: regularTrailPct,
                    enable_cooldown: enableCooldown,
                    cooldown_minutes: cooldownMinutes,
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
            metaEl.textContent = `${s.ticker}  ·  ${s.timeframe}  ·  ${s.period_days} Days  ·  ${startCST} – ${endCST} CST`;
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
    document.getElementById('resSharpe').textContent = (s.sharpe_ratio !== undefined) ? s.sharpe_ratio.toFixed(2) : '—';
    document.getElementById('resProfitFactor').textContent = (s.profit_factor !== undefined && s.profit_factor !== null) ? s.profit_factor.toFixed(2) + 'x' : '—';
    
    // Win / Loss Ratio text formatting
    const wlEl = document.getElementById('resWinLossRatio');
    if (s.win_loss_ratio !== undefined && s.win_loss_ratio !== null && isFinite(s.win_loss_ratio)) {
        wlEl.textContent = s.win_loss_ratio.toFixed(2) + 'x';
    } else {
        wlEl.textContent = '—';
    }

    // Advanced Metrics Row
    document.getElementById('resSortino').textContent = (s.sortino_ratio !== undefined) ? s.sortino_ratio.toFixed(2) : '—';
    document.getElementById('resCalmar').textContent = (s.calmar_ratio !== undefined) ? s.calmar_ratio.toFixed(2) : '—';
    document.getElementById('resHoldTime').textContent = (s.avg_hold_hours !== undefined) ? s.avg_hold_hours.toFixed(1) + 'h' : '—';
    document.getElementById('resExpectancy').textContent = (s.expectancy !== undefined) ? '$' + s.expectancy.toFixed(2) : '—';
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
    btChartTradeIdx = 0;

    if (btChartCandlesRef.length > 0) {
        document.getElementById('btChartSection').classList.remove('hidden');
        _btInitChart(btChartCandlesRef, btChartTradesRef, res.equity_curve || []);
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
        btChartMarkersRef = [];
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
    document.getElementById('runBtBtn').disabled = false;
    document.getElementById('btLoading').classList.add('hidden');
    window.lastBacktestRun = null;
}

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

function _btInitChart(candles, trades, equityCurve) {
    const wrap = document.getElementById('btChartWrap');
    if (!wrap) return;

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
        rightPriceScale: { borderColor: 'rgba(197, 203, 206, 0.8)' },
        timeScale: {
            borderColor: 'rgba(197, 203, 206, 0.8)',
            timeVisible: true,
            secondsVisible: false,
        },
        width: wrap.clientWidth,
        height: wrap.clientHeight,
    });

    // ── Candlestick series ───────────────────────────────────────────────
    btCandleSeries = btChart.addCandlestickSeries({
        upColor: '#10b981',
        downColor: '#ef4444',
        borderVisible: false,
        wickUpColor: '#10b981',
        wickDownColor: '#ef4444',
    });
    btCandleSeries.setData(candles);

    // ── Volume series ────────────────────────────────────────────────────
    btVolumeSeries = btChart.addHistogramSeries({
        color: '#26a69a',
        priceFormat: { type: 'volume' },
        priceScaleId: '', // set as an overlay by default
    });
    btChart.priceScale('').applyOptions({
        scaleMargins: { top: 0.8, bottom: 0 },
    });
    
    const volumeData = candles.map(c => ({
        time: c.time,
        value: c.volume,
        color: c.close >= c.open ? 'rgba(16, 185, 129, 0.4)' : 'rgba(239, 68, 68, 0.4)'
    }));
    btVolumeSeries.setData(volumeData);

    // ── Equity Curve overlay (purple line, bottom 20% of chart) ──────────
    if (equityCurve && equityCurve.length > 0) {
        const equityData = equityCurve.map(e => {
            const dt = _btParseDate(e.time);
            return dt ? { time: Math.floor(dt.getTime() / 1000), value: e.equity } : null;
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
        const entryTs = entryDt ? Math.floor(entryDt.getTime() / 1000) : 0;
        const exitTs = exitDt ? Math.floor(exitDt.getTime() / 1000) : 0;
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
            const eTs = entryDt ? Math.floor(entryDt.getTime() / 1000) : 0;
            const xTs = exitDt ? Math.floor(exitDt.getTime() / 1000) : 0;
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
    const checkedCount = container.querySelectorAll(`${checkClass}:checked`).length;

    const buyInput = document.getElementById(`strategyBuyThreshold_${mode}`);
    const sellInput = document.getElementById(`strategySellThreshold_${mode}`);
    const buyLabel = document.getElementById(`strategyBuyLabel_${mode}`);
    const sellLabel = document.getElementById(`strategySellLabel_${mode}`);

    if (!buyInput || !sellInput || !buyLabel || !sellLabel) return;

    if (checkedCount === 0) {
        buyInput.max = 0;
        buyInput.value = 0;
        sellInput.max = 0;
        sellInput.value = 0;
        buyLabel.textContent = "Select Indicators";
        buyLabel.className = "text-[0.6rem] font-black px-4 py-1.5 rounded-full bg-purple-100 text-purple-500 shadow-sm uppercase tracking-wider transition-all duration-300";
        sellLabel.textContent = "Select Indicators";
        sellLabel.className = "text-[0.6rem] font-black px-4 py-1.5 rounded-full bg-purple-100 text-purple-500 shadow-sm uppercase tracking-wider transition-all duration-300";
        return;
    }

    buyInput.max = checkedCount;
    sellInput.max = checkedCount;

    // Clamp values
    if (parseInt(buyInput.value) > checkedCount) buyInput.value = checkedCount;
    if (parseInt(buyInput.value) < 0) buyInput.value = 0;
    if (parseInt(sellInput.value) > checkedCount) sellInput.value = checkedCount;
    if (parseInt(sellInput.value) < 0) sellInput.value = 0;

    const buyVal = parseInt(buyInput.value);
    const sellVal = parseInt(sellInput.value);

    // Buy label
    const buyPct = checkedCount > 0 ? Math.round((buyVal / checkedCount) * 100) : 0;
    let buyMode = "Balanced";
    let buyColor = "bg-emerald-100 text-emerald-600";
    if (buyPct <= 34) { buyMode = "Aggressive"; buyColor = "bg-amber-100 text-amber-600"; }
    else if (buyPct >= 75) { buyMode = "Quality"; buyColor = "bg-emerald-500 text-white"; }
    if (buyVal === checkedCount && checkedCount > 1) { buyMode = "Ultra-Quality"; buyColor = "bg-emerald-600 text-white"; }
    buyLabel.textContent = `${buyMode} — ${buyVal} of ${checkedCount} signals`;
    buyLabel.className = `text-[0.6rem] font-black px-4 py-1.5 rounded-full ${buyColor} shadow-sm uppercase tracking-wider transition-all duration-300`;

    // Sell label
    const sellPct = checkedCount > 0 ? Math.round((sellVal / checkedCount) * 100) : 0;
    let sellMode = "Balanced";
    let sellColor = "bg-rose-100 text-rose-600";
    if (sellPct <= 34) { sellMode = "Quick Exit"; sellColor = "bg-amber-100 text-amber-600"; }
    else if (sellPct >= 75) { sellMode = "Patient"; sellColor = "bg-rose-500 text-white"; }
    if (sellVal === checkedCount && checkedCount > 1) { sellMode = "Ultra-Patient"; sellColor = "bg-rose-600 text-white"; }
    sellLabel.textContent = `${sellMode} — ${sellVal} of ${checkedCount} signals`;
    sellLabel.className = `text-[0.6rem] font-black px-4 py-1.5 rounded-full ${sellColor} shadow-sm uppercase tracking-wider transition-all duration-300`;
}

function adjustSignalThreshold(mode, type, delta) {
    const containerId = mode === 'regular' ? 'indicatorChecksContainer_bt_regular' : 'indicatorChecksContainer_bt_options';
    const checkClass = mode === 'regular' ? '.bt-indicator-check-regular' : '.bt-indicator-check-options';
    const checkedCount = document.getElementById(containerId).querySelectorAll(`${checkClass}:checked`).length;
    
    if (checkedCount === 0) return;

    const input = type === 'buy' ? document.getElementById(`strategyBuyThreshold_${mode}`) : document.getElementById(`strategySellThreshold_${mode}`);
    let val = parseInt(input.value) + delta;
    val = Math.max(0, Math.min(val, checkedCount));
    input.value = val;
    updateSignalLabels();
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
        const response = await fetch(`${API_BASE}/api/download_all`, {
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

var INDICATOR_TOOLTIPS = {
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
    "BotBulls3": "Heikin-Ashi noise filter + ATR-based trailing stop. Generates buy/sell alerts only on confirmed momentum flips — removing false signals from choppy markets."
};

var INDICATOR_CONFIG_MAP = {
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
    'Botbulls4 News AI': ['SENTIMENT_BULLISH_PERCENTAGE', 'SENTIMENT_BEARISH_PERCENTAGE'],
    'BotBulls1': ['BOTBULLS1_WT_CHANNEL', 'BOTBULLS1_WT_AVERAGE', 'BOTBULLS1_MFI_CONFIRM'],
    'BotBulls2': ['BOTBULLS2_ATR_MULT', 'BOTBULLS2_TREND_TRACER_PERIOD', 'BOTBULLS2_REVERSAL_ZONE_PERIOD'],
    'BotBulls3': ['BOTBULLS3_ATR_PERIOD', 'BOTBULLS3_ATR_MULT']
};

// Industry Defaults for indicators based on config.py
var INDICATOR_DEFAULTS = {
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
    'ATR_PERIOD': 14,
    'ATR_STOP_MULTIPLIER': 2.0,
    'ATR_TRAIL_MULTIPLIER': 3.0,
    'ATR_TAKE_PROFIT_MULTIPLIER': 4.0,
    'MIN_BULLISH_SIGNALS': 4,
    'MIN_BEARISH_SIGNALS': 4,
    'SENTIMENT_BULLISH_PERCENTAGE': 70,
    'SENTIMENT_BEARISH_PERCENTAGE': 30
};

let btCurrentEditingIndicator = null;

function openIndicatorSettings(indicatorName) {
    btCurrentEditingIndicator = indicatorName;
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

    const currentParams = window.lastBacktestData?.indicator_parameters || {};
    const overrides = window.lastBacktestData?.indicator_overrides || {};

    configKeys.forEach(key => {
        const hasOverride = overrides[key] !== undefined && overrides[key] !== null && overrides[key] !== '';
        const val = currentParams[key] !== undefined ? currentParams[key] : (INDICATOR_DEFAULTS[key] !== undefined ? INDICATOR_DEFAULTS[key] : '');
        const label = key.replace(/_/g, ' ').toLowerCase();

        const div = document.createElement('div');
        div.className = 'flex flex-col gap-1.5';
        div.innerHTML = `
            <div class="flex justify-between items-center mb-0.5">
                <label class="text-[0.65rem] font-black text-indigo-950 uppercase tracking-widest opacity-60">${label}</label>
                ${!hasOverride ? '<span class="text-[0.55rem] font-extrabold text-emerald-500 uppercase tracking-widest bg-emerald-50 px-1.5 py-0.5 rounded-md">System Default</span>' : ''}
            </div>
            <input type="number" step="any" data-key="${key}" value="${val}" placeholder="${INDICATOR_DEFAULTS[key] !== undefined ? INDICATOR_DEFAULTS[key] : ''}" 
                class="w-full bg-slate-50 border border-slate-200 rounded-lg px-3 py-2 text-sm font-bold text-indigo-900 focus:ring-2 focus:ring-indigo-500 focus:border-transparent outline-none transition-all">
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
    const inputs = document.querySelectorAll('#indicatorModalContent input');
    inputs.forEach(input => {
        input.value = input.placeholder; // Revert to system default (visible value)
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
    const inputs = document.querySelectorAll('#indicatorModalContent input');
    const updates = {};
    inputs.forEach(input => {
        if (input.value === input.placeholder || input.value === "") {
            updates[input.dataset.key] = "";
        } else {
            updates[input.dataset.key] = input.value;
        }
    });

    if (Object.keys(updates).length === 0) {
        closeIndicatorSettings();
        return;
    }

    try {
        const headers = await getAuthHeaders();
        const response = await fetch(`${API_BASE}/api/settings/indicators?context=backtest`, {
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
        const response = await fetch(`${API_BASE}/api/backtest/monte_carlo`, {
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

let mcChartInstance = null;

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
    document.getElementById('strategyBuyFields_regular')?.classList.toggle('hidden', mode !== 'indicator');
    document.getElementById('strategyCustomBuyFields_regular')?.classList.toggle('hidden', mode === 'indicator');
};

window.updateBtRegularExitFields = function() {
    const mode = document.getElementById('strategySellMode_regular')?.value || 'indicator';
    const showATR    = ['sltp', 'hybrid'].includes(mode);
    const showPct    = ['percentage', 'hybrid_pct'].includes(mode);
    const showPivot  = ['pivot_points', 'hybrid_pivot'].includes(mode);
    const showHighLow = ['high_low', 'hybrid_high_low'].includes(mode);
    
    document.getElementById('exitFieldsIndicator')?.classList.toggle('hidden', !['indicator', 'hybrid', 'hybrid_pct', 'hybrid_pivot', 'hybrid_high_low'].includes(mode));
    document.getElementById('exitFieldsATR')?.classList.toggle('hidden', !showATR);
    document.getElementById('exitFieldsPct')?.classList.toggle('hidden', !showPct);
    document.getElementById('exitFieldsPivot')?.classList.toggle('hidden', !showPivot);
    document.getElementById('exitFieldsHighLow')?.classList.toggle('hidden', !showHighLow);
};

window.adjustBtThreshold = function(type, delta) {
    window.adjustStrategyThreshold('regular', type, delta);
};

window.updateBtOptionsEntryFields = function() {
    const mode = document.getElementById('strategyBuyMode_options')?.value || 'indicator';
    document.getElementById('strategyBuyFields_options')?.classList.toggle('hidden', mode !== 'indicator');
    document.getElementById('strategyCustomBuyFields_options')?.classList.toggle('hidden', mode === 'indicator');
};

window.updateBtOptionsExitFields = function() {
    const mode = document.getElementById('strategySellMode_options')?.value || 'indicator';
    const showATR     = ['sltp', 'hybrid'].includes(mode);
    const showPct     = ['percentage', 'hybrid_pct'].includes(mode);
    const showPivot   = ['pivot_points', 'hybrid_pivot'].includes(mode);
    const showHighLow = ['high_low', 'hybrid_high_low'].includes(mode);
    const showCustomUnderlying = mode === 'custom_underlying_sltp';
    
    document.getElementById('exitFieldsIndicator_options')?.classList.toggle('hidden', !['indicator', 'hybrid', 'hybrid_pct', 'hybrid_pivot', 'hybrid_high_low'].includes(mode));
    document.getElementById('exitFieldsCustomUnderlying_options')?.classList.toggle('hidden', !showCustomUnderlying);
    document.getElementById('exitFieldsATR_options')?.classList.toggle('hidden', !showATR);
    document.getElementById('exitFieldsPct_options')?.classList.toggle('hidden', !showPct);
    document.getElementById('exitFieldsPivot_options')?.classList.toggle('hidden', !showPivot);
    document.getElementById('exitFieldsHighLow_options')?.classList.toggle('hidden', !showHighLow);
};

window.adjustBtOptionsThreshold = function(type, delta) {
    window.adjustStrategyThreshold('options', type, delta);
};

window.updateBtRegularSignalLabels = function() {
    updateSignalLabels('regular');
};
window.updateBtOptionsSignalLabels = function() {
    updateSignalLabels('options');
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
    const checkedCount = container.querySelectorAll(`${checkClass}:checked`).length;
    
    if (checkedCount === 0) return;

    const input = document.getElementById(`strategy${type === 'buy' ? 'Buy' : 'Sell'}Threshold_${mode}`);
    if (!input) return;

    let val = parseInt(input.value) + delta;
    val = Math.max(0, Math.min(val, checkedCount));
    input.value = val;

    // Update labels
    updateSignalLabels(mode);
};
window.toggleModeActive = function(mode) {
    if (mode === 'options') return window.toggleBtMode('options');
    return window.toggleBtMode('regular');
};
