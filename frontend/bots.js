
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
const REFRESH_INTERVAL = 10000; // 10 seconds (faster real-time tracking)

let selectedTicker = null;
let currentBackendTf = "5Min";
let isAlpacaLinked = false;
let currentLogTab = "all";
let currentChartPeriod = "ALL";
let portfolioChartInstance = null;
let botArrangeMode = false;

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

// ──────────────────────────────────────────────
// Render Active Bots List
// ──────────────────────────────────────────────
let currentBotTab = 'all';

function isCrypto(ticker) {
    if (!ticker) return false;
    return ticker.includes('USD') || ['BTC', 'ETH', 'LTC', 'SOL', 'DOGE', 'ADA', 'DOT', 'SHIB', 'AVAX'].some(c => ticker.startsWith(c));
}

function getMarketSessionForStrategy(mode = 'regular') {
    const symbol = (window.currentStrategySymbol || '').toUpperCase();
    if (isCrypto(symbol)) return '24h';
    return document.getElementById(`strategyMarketSession_${mode}`)?.value || 'Regular';
}

function applyMarketSessionForSymbol(symbol, settings = {}) {
    const select = document.getElementById('strategyMarketSession_regular');
    if (!select) return;
    const crypto = isCrypto((symbol || '').toUpperCase());
    const savedSession = settings.market_session === 'Crypto24' ? '24h' : (settings.market_session || 'Regular');
    select.value = crypto ? '24h' : savedSession;
    select.disabled = crypto;
    select.title = crypto ? 'Crypto trades 24 hours, so all candles are used.' : '';
    select.classList.toggle('opacity-70', crypto);
    if (window.updateMarketSessionNotice) window.updateMarketSessionNotice();
}

function setMarketSessionNotice(message = '', state = 'info') {
    const notice = document.getElementById('strategyMarketSessionNotice_regular');
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
}

window.updateMarketSessionNotice = async function () {
    const symbol = (window.currentStrategySymbol || '').toUpperCase();
    const select = document.getElementById('strategyMarketSession_regular');
    if (!select || !symbol) {
        setMarketSessionNotice('');
        return { allowed: true };
    }

    const session = getMarketSessionForStrategy('regular');
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

window.goToDashboardChart = async function (ticker) {
    try {
        if (window.lastBotsData && window.lastBotsData.ticker_settings) {
            const settings = window.lastBotsData.ticker_settings[ticker];
            if (settings) {
                const headers = await getAuthHeaders();

                // Update timeframe
                if (settings.timeframe) {
                    localStorage.setItem('lastChartTf', settings.timeframe);

                    // Enforce chart ranges based on timeframes when coming from active bots
                    const midTimeframes = ['15Min', '30Min', '1Hour', '2Hour', '4Hour'];
                    const shortTimeframes = ['1Min', '2Min', '3Min', '5Min', '10Min'];

                    if (midTimeframes.includes(settings.timeframe)) {
                        localStorage.setItem('lastChartRange', '1M');
                    } else if (shortTimeframes.includes(settings.timeframe)) {
                        localStorage.setItem('lastChartRange', '5D');
                    }

                    await fetch(`${API_BASE}/api/settings/timeframe`, {
                        method: 'POST',
                        headers: headers,
                        body: JSON.stringify({ timeframe: settings.timeframe })
                    });
                }

                // Overwrite global dashboard indicators
                if (settings.indicators) {
                    const SIGNAL_TO_TOGGLE = {
                        'RSI': 'ENABLE_RSI', 'MACD': 'ENABLE_MACD', 'EMA Cross': 'ENABLE_EMA',
                        'Supertrend': 'ENABLE_SUPERTREND', 'Bollinger': 'ENABLE_BOLLINGER',
                        'VWAP': 'ENABLE_VWAP', 'Mystic Pulse': 'ENABLE_MYSTIC_PULSE',
                        'Botbulls4 News AI': 'ENABLE_AI_SENTIMENT', 'Candle Patterns': 'ENABLE_CANDLE_PATTERNS',
                        'ADX Trend': 'ENABLE_ADX_TREND', 'SMA': 'ENABLE_SMA',
                        'BotBulls1': 'ENABLE_BOTBULLS1', 'BotBulls2': 'ENABLE_BOTBULLS2', 'BotBulls3': 'ENABLE_BOTBULLS3'
                    };

                    const payload = {};
                    const newPrefs = {};
                    Object.values(SIGNAL_TO_TOGGLE).forEach(k => payload[k] = false); // Disable all
                    Object.keys(SIGNAL_TO_TOGGLE).forEach(ind => newPrefs[ind] = false);

                    settings.indicators.forEach(ind => {
                        if (SIGNAL_TO_TOGGLE[ind]) {
                            payload[SIGNAL_TO_TOGGLE[ind]] = true; // Enable bot's in backend
                            newPrefs[ind] = true; // Enable in frontend
                        }
                    });

                    // Inject bot's custom settings (overrides) into the payload
                    // so the global chart accurately reflects the bot's custom parameters
                    if (settings.indicator_overrides) {
                        for (const [key, val] of Object.entries(settings.indicator_overrides)) {
                            payload[key] = val;
                        }
                    }

                    // Save the bot's threshold settings so the Dashboard Backtest modal can auto-load them
                    if (settings.min_buy_signals !== undefined) {
                        localStorage.setItem('lastBotBuyThreshold', settings.min_buy_signals);
                    }
                    if (settings.min_sell_signals !== undefined) {
                        localStorage.setItem('lastBotSellThreshold', settings.min_sell_signals);
                    }

                    // Save to local storage so the UI updates immediately
                    localStorage.setItem('globalIndicatorPrefs', JSON.stringify(newPrefs));

                    try {
                        sessionStorage.setItem('dashboardChartContext', JSON.stringify({
                            ticker,
                            source: 'dashboard',
                            timeframe: settings.timeframe || null,
                            indicatorPrefs: newPrefs
                        }));
                    } catch (contextErr) {
                        console.warn('Failed to store dashboard chart context:', contextErr);
                    }

                    await fetch(`${API_BASE}/api/settings/indicators?context=dashboard`, {
                        method: 'POST',
                        headers: headers,
                        body: JSON.stringify(payload)
                    });
                }
            }
        }
    } catch (e) {
        console.error("Failed to transfer settings:", e);
    }

    localStorage.setItem('lastViewedTicker', ticker);
    window.location.href = 'dashboard.html';
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
            await fetchBotsData();
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
        const isOptions = tickerSettings.options_trading || false;
        const isCryptoFlag = ticker.includes('USD') || ticker.includes('BTC') || ticker.includes('ETH') || ticker.includes('SOL') || ticker.includes('DOGE') || ticker.includes('LTC');
        const premiumCardClass = isOptions ? 'options-premium-card' : (isCryptoFlag ? 'crypto-premium-card' : '');

        const item = document.createElement('div');
        item.className = `watchlist-item group active-bot ${premiumCardClass} ${selectedTicker === ticker ? 'active' : ''} ${botArrangeMode ? 'ring-1 ring-emerald-200 bg-emerald-50/30' : ''} p-3 mb-2 flex flex-col gap-2 transition-transform duration-200`;
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
        const isCrypto = ticker.includes('USD') || ticker.includes('BTC') || ticker.includes('ETH') || ticker.includes('SOL') || ticker.includes('DOGE') || ticker.includes('LTC');
        if (scan?.price) {
            const parsedPrice = parseFloat(scan.price.toString().replace('$', ''));
            price = `$${formatAssetPrice(parsedPrice, isCrypto)}`;
        }
        const actionColor = action === 'BUY' ? 'bg-emerald-50 text-emerald-600 border-emerald-100' : action === 'SELL' ? 'bg-red-50 text-red-600 border-red-100' : 'bg-slate-50 text-slate-400 border-slate-100';

        const bullish = scan?.bullish_count ?? 0;
        const bearish = scan?.bearish_count ?? 0;
        const tickerTf = (window.lastBotsData?.ticker_settings || {})[ticker]?.timeframe || '';
        const tfLabel = tickerTf || (window.lastBotsData?.strategyTimeframe || '5Min');
        const isPaused = (window.lastBotsData?.ticker_settings || {})[ticker]?.paused || false;
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

        const pauseReason = (window.lastBotsData?.ticker_settings || {})[ticker]?.pause_reason || '';
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
            const exitProtection = activePosition.exit_protection || {};
            const exitProtectionText = exitProtection.status || activePosition.exit_protection_status || '';
            const exitProtectionClass = exitProtection.active
                ? 'text-emerald-600 bg-emerald-50 border-emerald-100'
                : 'text-slate-500 bg-slate-50 border-slate-100';
            const exitProtectionHtml = exitProtectionText
                ? `<span class="mt-1 inline-flex w-fit max-w-full px-1.5 py-0.5 rounded border ${exitProtectionClass} text-[0.5rem] font-black uppercase tracking-wide font-mono leading-tight whitespace-normal break-words" title="${exitProtectionText.replace(/<[^>]*>?/gm, '')}">${exitProtectionText}</span>`
                : '';

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
                <div class="mt-1.5 pt-1.5 border-t border-slate-50 flex flex-col justify-center cursor-pointer" onclick="selectTicker('${ticker}')">
                    <span class="text-[0.52rem] text-slate-400 font-bold uppercase tracking-wider mb-1">Forecast Exit</span>
                    <span class="text-[0.55rem] font-bold text-slate-500 font-mono leading-tight whitespace-normal break-words" title="${(activePosition.exit_forecast || 'Checking exit criteria...').replace(/<[^>]*>?/gm, '')}">${activePosition.exit_forecast || 'Checking exit criteria...'}</span>
                    ${exitProtectionHtml}
                </div>
            `;
        } else {
            const forecastText = scan.entry_forecast || "💤 Standing by. Watching market for entry...";
            positionHtml = `
                <div class="mt-2 pt-2 border-t border-slate-50 flex flex-col justify-center cursor-pointer" onclick="selectTicker('${ticker}')">
                    <span class="text-[0.52rem] text-slate-400 font-bold uppercase tracking-wider mb-1">Forecast Entry</span>
                    <span class="text-[0.55rem] font-bold text-slate-500 font-mono leading-tight whitespace-normal break-words" title="${forecastText.replace(/<[^>]*>?/gm, '')}">
                        ${forecastText}
                    </span>
                </div>
            `;
        }

        const startedAt = tickerSettings.started_at;
        const lastTradeAt = tickerSettings.last_trade_at;
        const totalTrades = tickerSettings.total_trades || 0;
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
                <div class="flex items-center justify-between cursor-text">
                    <div class="flex items-center gap-2">
                        ${arrangeControlsHtml}
                        ${dragHandleHtml}
                        <div class="${dotClass} cursor-pointer" onclick="selectTicker('${ticker}')"></div>
                        <span class="font-black text-sm ${isPaused ? 'text-slate-400' : 'text-indigo-950'} tracking-tight cursor-pointer" onclick="selectTicker('${ticker}')">${ticker}</span>
                        <span class="text-[0.75rem] font-black text-slate-600 font-mono tracking-tight ml-1">${price}</span>
                        ${backtestBadge}${incubatorBadge}${allocationBadgeHtml}
                    </div>
                    
                    <div class="flex items-center gap-1 opacity-60 group-hover:opacity-100 transition-opacity ml-1">
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
                <div class="flex items-center justify-between cursor-pointer" onclick="selectTicker('${ticker}')">
                    <div class="flex items-center gap-1.5 flex-wrap">
                        ${isPaused ? '<span class="text-[0.5rem] font-bold px-1.5 py-0.5 rounded bg-amber-50 text-amber-500 border border-amber-200">PAUSED</span>' : '<span class="text-[0.5rem] font-bold px-1.5 py-0.5 rounded bg-emerald-50 text-emerald-600 border border-emerald-100">ACTIVE</span>'}
                        ${isOptions
                ? `<span class="px-1.5 py-0.5 rounded bg-amber-500/10 text-amber-700 font-black text-[0.5rem] border border-amber-500/20 uppercase tracking-widest">Options</span>
                                   <span class="px-1.5 py-0.5 rounded bg-amber-50 text-amber-600 font-black text-[0.45rem] border border-amber-200 uppercase tracking-widest">${tickerSettings.options_type === 'calls_only' ? 'Calls Only' : (tickerSettings.options_type === 'puts_only' ? 'Puts Only' : 'Calls & Puts')}</span>
                                   <span class="px-1.5 py-0.5 rounded bg-amber-50 text-amber-600 font-black text-[0.45rem] border border-amber-200 uppercase tracking-widest">${tickerSettings.options_expiry_days || 7}D Exp</span>`
                : (isCrypto
                    ? `<span class="px-1.5 py-0.5 rounded bg-fuchsia-500/10 text-fuchsia-700 font-black text-[0.5rem] border border-fuchsia-500/20 uppercase tracking-widest">Crypto</span>`
                    : `<span class="px-1.5 py-0.5 rounded bg-indigo-500/10 text-indigo-700 font-black text-[0.5rem] border border-indigo-500/20 uppercase tracking-widest">Stocks</span>`)
            }
                        <span class="px-1.5 py-0.5 rounded bg-indigo-50/50 text-indigo-500 font-mono font-black text-[0.55rem] border border-indigo-100/50 uppercase">${tfLabel}</span>
                        <button class="px-1.5 py-0.5 rounded bg-blue-50 hover:bg-blue-100 text-blue-600 font-black text-[0.5rem] border border-blue-200 transition-colors uppercase tracking-widest flex items-center gap-0.5" onclick="event.stopPropagation(); window.goToDashboardChart('${ticker}')">
                            <svg xmlns="http://www.w3.org/2000/svg" class="h-2.5 w-2.5" viewBox="0 0 20 20" fill="currentColor">
                                <path d="M2 11a1 1 0 011-1h2a1 1 0 011 1v5a1 1 0 01-1 1H3a1 1 0 01-1-1v-5zM8 7a1 1 0 011-1h2a1 1 0 011 1v9a1 1 0 01-1 1H9a1 1 0 01-1-1V7zM14 4a1 1 0 011-1h2a1 1 0 011 1v12a1 1 0 01-1 1h-2a1 1 0 01-1-1V4z" />
                            </svg>
                            GO TO CHART
                        </button>
                    </div>
                    
                    <div class="flex items-center gap-1.5">
                        <span class="px-1.5 py-0.5 rounded border ${actionColor} font-black text-[0.5rem] tracking-widest uppercase">${action}</span>
                        <span class="text-[0.6rem] font-bold text-emerald-600 bg-emerald-50 px-1 rounded border border-emerald-100">${bullish}B</span>
                        <span class="text-[0.6rem] font-bold text-red-600 bg-red-50 px-1 rounded border border-red-100">${bearish}S</span>
                    </div>
                </div>

                <!-- Metadata Row: Runtime, Last Trade, and Trade Counts -->
                <div class="flex items-center justify-between mt-1 text-[0.58rem] text-slate-500 font-semibold cursor-pointer" onclick="selectTicker('${ticker}')">
                    <div class="flex items-center gap-1">
                        <span class="flex items-center gap-0.5 ${isPaused ? 'text-slate-400' : 'text-slate-600'}" title="Bot Running Time">
                            <svg xmlns="http://www.w3.org/2000/svg" class="h-3 w-3 ${isPaused ? 'text-slate-300' : 'text-indigo-400'}" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                                <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2.5" d="M12 8v4l3 3m6-3a9 9 0 11-18 0 9 9 0 0118 0z" />
                            </svg>
                            <span>${runtimeText}</span>
                        </span>
                        <span class="text-slate-300">•</span>
                        <span class="text-slate-400 font-normal" title="Last Trade Time">${lastTradeText}</span>
                    </div>
                    <div class="flex items-center">
                        ${tradeBadgeHtml}
                    </div>
                </div>

                ${pauseReasonHtml}

                <!-- 3rd Line: Position Details or Standing By Message -->
                ${positionHtml}
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
                await fetchBotsData();
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
    fetchAIIndicator(ticker, currentBackendTf);
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

function renderTradeLog(scanHistory, orderHistory, pendingOrders, positions) {
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
        } else if (currentLogTab === 'positions') {
            thead.innerHTML = `
                <tr class="border-b border-purple-200 text-purple-700 text-[10px] md:text-xs uppercase tracking-wider">
                    <th class="pb-3 pr-4">Ticker</th>
                    <th class="pb-3 pr-4 text-center">Qty</th>
                    <th class="pb-3 pr-4 text-center">Avg Price</th>
                    <th class="pb-3 pr-4 text-center">Current Price</th>
                    <th class="pb-3 pr-4 text-center">Market Value</th>
                    <th class="pb-3 pr-4 text-center">Total P/L</th>
                    <th class="pb-3 pr-4 text-center">Daily Change</th>
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
                <td class="py-2.5 pr-4 text-purple-500 italic text-xs min-w-[250px] max-w-[400px] whitespace-normal" title="${item.reason}">${item.reason}</td>
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
                <td class="py-2.5 pr-4 text-purple-600 text-xs">${formatLocalTime(item.created_at)}</td>
                <td class="py-2.5 pr-4 text-indigo-950 font-semibold text-xs">${item.symbol}</td>
                <td class="py-2.5 pr-4 text-xs ${sideColor} uppercase">${item.side}</td>
                <td class="py-2.5 pr-4 text-center text-[0.65rem] font-bold text-indigo-900">${item.filled_qty ? item.filled_qty.toFixed(4) : '0.0000'}</td>
                <td class="py-2.5 pr-4 text-center text-xs text-indigo-700 font-medium">$${avgPriceStr}</td>
                <td class="py-2.5 pr-4 text-center text-xs text-indigo-900 font-bold">$${totalCostStr}</td>
                ${plHtml}
                <td class="py-2.5 pr-4 text-purple-500 italic text-[11px] min-w-[250px] max-w-[400px] whitespace-normal" title="${aiReason}">${aiReason}</td>
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
                <td class="py-2.5 pr-4 text-purple-600 text-xs">${formatLocalTime(item.created_at)}</td>
                <td class="py-2.5 pr-4 text-indigo-950 font-semibold text-xs">${item.symbol}</td>
                <td class="py-2.5 pr-4 text-xs ${sideColor} uppercase">${item.side}</td>
                <td class="py-2.5 pr-4 text-center">${tifBadge}</td>
                <td class="py-2.5 pr-4 text-center text-[0.65rem] font-bold text-indigo-900">${item.qty ? item.qty.toFixed(4) : '0.0000'}</td>
                <td class="py-2.5 pr-4 text-center text-xs text-indigo-700 font-medium">${limitPriceStr}</td>
                <td class="py-2.5 pr-4 text-center text-xs text-indigo-900 font-bold">${totalValueStr}</td>
                <td class="py-2.5 pr-4 text-purple-500 italic text-[11px] min-w-[250px] max-w-[400px] whitespace-normal" title="${aiReason}">${aiReason}</td>
                <td class="py-2.5 pr-4 text-center">
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
                <td class="py-2.5 pr-4 text-indigo-950 font-semibold text-xs">${item.symbol}</td>
                <td class="py-2.5 pr-4 text-center text-[0.65rem] font-bold text-indigo-900">${qtyStr}</td>
                <td class="py-2.5 pr-4 text-center text-xs text-indigo-700 font-medium">${avgPriceStr}</td>
                <td class="py-2.5 pr-4 text-center text-xs text-indigo-700 font-medium">${currentPriceStr}</td>
                <td class="py-2.5 pr-4 text-center text-xs text-indigo-900 font-bold">${marketValueStr}</td>
                <td class="py-2.5 pr-4 text-center ${plColor} text-[11px]">
                    <div>${sign}$${plVal.toFixed(2)}</div>
                    <div class="text-[9px] opacity-80 font-semibold">${sign}${plPct.toFixed(2)}%</div>
                </td>
                <td class="py-2.5 pr-4 text-center ${dayPlColor} text-[11px]">
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
    const val = document.getElementById(`strategyCooldownMinutes_${mode}`).value;
    if (val === 'default') return "default";
    if (val === 'custom') return parseInt(document.getElementById(`strategyCooldownCustomMinutes_${mode}`).value) || 60;
    return parseInt(val) || 60;
}

function getDefaultSignalThreshold(type = 'buy') {
    const key = type === 'sell' ? 'MIN_BEARISH_SIGNALS' : 'MIN_BULLISH_SIGNALS';
    return parseInt(INDICATOR_DEFAULTS[key] || 4, 10) || 4;
}

function normalizeSignalThreshold(value, checkedCount, type = 'buy') {
    const defaultValue = getDefaultSignalThreshold(type);
    let normalized = parseInt(value, 10);

    if (!Number.isFinite(normalized) || normalized <= 0) {
        normalized = checkedCount > 0 ? Math.min(defaultValue, checkedCount) : defaultValue;
    }

    if (checkedCount > 0) {
        normalized = Math.max(1, Math.min(normalized, checkedCount));
    } else {
        normalized = Math.max(1, normalized);
    }

    return normalized;
}

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
};

function getStrategyModeSettings(mode = 'regular') {
    const isRegular = mode === 'regular';
    return {
        allocation_mode: mode === 'regular' ? getAllocationMode('regular') : undefined,
        options_allocation_mode: mode === 'options' ? getAllocationMode('options') : undefined,
        buy_mode: document.getElementById(`strategyBuyMode_${mode}`)?.value || 'indicator',
        custom_buy_price: parseFloat(document.getElementById(`strategyCustomBuyPrice_${mode}`)?.value || 0),
        custom_stop_loss_price: parseFloat(document.getElementById(`strategyCustomStopLossPrice_${mode}`)?.value || 0),
        custom_take_profit_price: parseFloat(document.getElementById(`strategyCustomTakeProfitPrice_${mode}`)?.value || 0),
        market_session: getMarketSessionForStrategy(mode),
        entry_atr_multiplier: parseFloat(document.getElementById(`strategyEntryAtrMult_${mode}`)?.value || 2),
        entry_pct_move: parseFloat(document.getElementById(`strategyEntryPctMove_${mode}`)?.value || 5),
        entry_atr_style: document.getElementById(`strategyEntryAtrStyle_${mode}`)?.value || 'pullback_reclaim',
        entry_pct_style: document.getElementById(`strategyEntryPctStyle_${mode}`)?.value || 'dip_reclaim',
        swing_entry_mode: document.getElementById(`strategySwingEntryMode_${mode}`)?.value || 'bounce_retest',
        swing_entry_lookback_mode: document.getElementById(`strategySwingEntryLookback_${mode}`)?.value || '20D',
        swing_entry_lookback_days: getSwingLookbackDaysValue(mode, 'entry'),
        swing_exit_lookback_mode: document.getElementById(`strategySwingExitLookback_${mode}`)?.value || '20D',
        swing_exit_lookback_days: getSwingLookbackDaysValue(mode, 'exit'),
        enable_risk_management: document.getElementById(`strategyEnableRisk_${mode}`)?.checked || false,
        enable_atr_trail: isRegular ? (document.getElementById('strategyEnableAtrTrail')?.checked || false) : undefined,
        enable_pct_trail: isRegular ? (document.getElementById('strategyEnablePctTrail')?.checked || false) : undefined,
        regular_stop_loss_pct: isRegular ? parseFloat(document.getElementById('strategyRegularSlPct')?.value || 5) / 100.0 : undefined,
        regular_take_profit_pct: isRegular ? parseFloat(document.getElementById('strategyRegularTpPct')?.value || 10) / 100.0 : undefined,
        regular_trailing_pct: isRegular ? parseFloat(document.getElementById('strategyRegularTrailPct')?.value || 3) / 100.0 : undefined,
    };
}

window.openStrategyModal = function (symbol, mode = 'deploy', name = '') {
    currentStrategySymbol = symbol.toUpperCase();
    window.currentStrategySymbol = currentStrategySymbol;
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
        actionBtn.className = "w-full bg-gradient-to-r from-emerald-500 to-teal-500 text-white font-black py-4 rounded-2xl hover:shadow-lg hover:shadow-emerald-500/30 transition-all active:scale-[0.98] uppercase tracking-[0.2em] shadow-md flex items-center justify-center gap-2 disabled:opacity-50 disabled:cursor-not-allowed disabled:hover:shadow-none disabled:active:scale-100";
        actionBtn.disabled = true;

        const riskContainer = document.getElementById('deployRiskAgreementContainer');
        const riskCheck = document.getElementById('riskAgreementCheckbox');
        if (riskContainer) riskContainer.classList.remove('hidden');
        if (riskCheck) riskCheck.checked = false;

        // Defaults for deployment
        document.getElementById('strategyCapital').value = 100;
        if (document.getElementById('strategyOptionsTradeAmount')) document.getElementById('strategyOptionsTradeAmount').value = 100;
        if (document.getElementById('strategyAllocationMode_regular')) document.getElementById('strategyAllocationMode_regular').value = 'fixed';
        if (document.getElementById('strategyAllocationMode_options')) document.getElementById('strategyAllocationMode_options').value = 'fixed';
        window.updateAllocationModeUI?.('regular');
        window.updateAllocationModeUI?.('options');
        document.getElementById('strategyBuyThreshold_regular').value = INDICATOR_DEFAULTS['MIN_BULLISH_SIGNALS'] || 4;
        document.getElementById('strategyBuyThreshold_options').value = INDICATOR_DEFAULTS['MIN_BULLISH_SIGNALS'] || 4;
        document.getElementById('strategySellThreshold_regular').value = INDICATOR_DEFAULTS['MIN_BEARISH_SIGNALS'] || 4;
        document.getElementById('strategySellThreshold_options').value = INDICATOR_DEFAULTS['MIN_BEARISH_SIGNALS'] || 4;
        document.getElementById('strategyTimeframe_regular').value = "1Hour";
        document.getElementById('strategyTimeframe_options').value = "5Min";
        if (document.getElementById('strategyBuyMode_regular')) document.getElementById('strategyBuyMode_regular').value = "indicator";
        if (document.getElementById('strategyBuyMode_options')) document.getElementById('strategyBuyMode_options').value = "indicator";
        if (document.getElementById('strategyCustomBuyPrice_regular')) document.getElementById('strategyCustomBuyPrice_regular').value = "";
        if (document.getElementById('strategyCustomBuyPrice_options')) document.getElementById('strategyCustomBuyPrice_options').value = "";
        if (document.getElementById('strategyCustomStopLossPrice_regular')) document.getElementById('strategyCustomStopLossPrice_regular').value = "";
        if (document.getElementById('strategyCustomTakeProfitPrice_regular')) document.getElementById('strategyCustomTakeProfitPrice_regular').value = "";
        if (document.getElementById('strategyCustomStopLossPrice_options')) document.getElementById('strategyCustomStopLossPrice_options').value = "";
        if (document.getElementById('strategyCustomTakeProfitPrice_options')) document.getElementById('strategyCustomTakeProfitPrice_options').value = "";
        if (document.getElementById('strategyEntryAtrMult_regular')) document.getElementById('strategyEntryAtrMult_regular').value = 2;
        if (document.getElementById('strategyEntryAtrMult_options')) document.getElementById('strategyEntryAtrMult_options').value = 2;
        if (document.getElementById('strategyEntryPctMove_regular')) document.getElementById('strategyEntryPctMove_regular').value = 5;
        if (document.getElementById('strategyEntryPctMove_options')) document.getElementById('strategyEntryPctMove_options').value = 5;
        if (document.getElementById('strategyEntryAtrStyle_regular')) document.getElementById('strategyEntryAtrStyle_regular').value = 'pullback_reclaim';
        if (document.getElementById('strategyEntryAtrStyle_options')) document.getElementById('strategyEntryAtrStyle_options').value = 'pullback_reclaim';
        if (document.getElementById('strategyEntryPctStyle_regular')) document.getElementById('strategyEntryPctStyle_regular').value = 'dip_reclaim';
        if (document.getElementById('strategyEntryPctStyle_options')) document.getElementById('strategyEntryPctStyle_options').value = 'dip_reclaim';
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

        document.getElementById('strategyOptionsType').value = "both";
        document.getElementById('strategyOptionsTradeAmount').value = 100;
        document.getElementById('strategyOptionsExpiryDays').value = 3;
        document.getElementById('strategyOptionsStrikeType').value = "ATM";
        if (document.getElementById('strategySellMode_options')) document.getElementById('strategySellMode_options').value = "hybrid";
        if (document.getElementById('strategyOptionsSlPct')) document.getElementById('strategyOptionsSlPct').value = 10;
        if (document.getElementById('strategyOptionsTpPct')) document.getElementById('strategyOptionsTpPct').value = 20;

        // Reset Risk Overrides to defaults
        document.getElementById('strategyRiskPerTrade').value = '10';
        document.getElementById('strategyMaxDailyDrawdown').value = '50';
        document.getElementById('strategyMaxPositionPct').value = '100';
        document.getElementById('strategyEnableRisk_regular').checked = true;
        document.getElementById('strategyEnableRisk_options').checked = true;
        window.toggleRiskControls(true, 'regular');
        window.toggleRiskControls(true, 'options');
        if(document.getElementById('strategyOptionsRiskPerTrade')) {
            document.getElementById('strategyOptionsRiskPerTrade').value = '30';
            document.getElementById('strategyOptionsMaxDailyDrawdown').value = '60';
            document.getElementById('strategyOptionsMaxPositionPct').value = '100';
        }
        document.getElementById('strategyAtrStopMult').value = INDICATOR_DEFAULTS['ATR_STOP_MULTIPLIER'] || 2;
        document.getElementById('strategyAtrTrailMult').value = INDICATOR_DEFAULTS['ATR_TRAIL_MULTIPLIER'] || 3;
        document.getElementById('strategyAtrTpMult').value = INDICATOR_DEFAULTS['ATR_TAKE_PROFIT_MULTIPLIER'] || 4;
        if (document.getElementById('strategyEnableAtrTrail')) document.getElementById('strategyEnableAtrTrail').checked = true;
        if (document.getElementById('strategyEnablePctTrail')) document.getElementById('strategyEnablePctTrail').checked = true;
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



        // Start new bots with indicators disabled by default.
        // Parameter dialogs still show system defaults when the user opts in.
        document.querySelectorAll('.strategy-indicator-check').forEach(chk => chk.checked = false);

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
        const settings = (window.lastBotsData?.ticker_settings || {})[currentStrategySymbol] || {};
        document.getElementById('strategyCapital').value = settings.amount || '';
        if (document.getElementById('strategyOptionsTradeAmount')) document.getElementById('strategyOptionsTradeAmount').value = settings.options_trade_amount !== undefined ? settings.options_trade_amount : 100;
        if (document.getElementById('strategyAllocationMode_regular')) document.getElementById('strategyAllocationMode_regular').value = settings.allocation_mode || 'fixed';
        if (document.getElementById('strategyAllocationMode_options')) document.getElementById('strategyAllocationMode_options').value = settings.options_allocation_mode || settings.allocation_mode || 'fixed';
        window.updateAllocationModeUI?.('regular', settings);
        window.updateAllocationModeUI?.('options', settings);
        const buySignals = settings.min_buy_signals !== undefined ? settings.min_buy_signals : (INDICATOR_DEFAULTS['MIN_BULLISH_SIGNALS'] || 4);
        document.getElementById('strategyBuyThreshold_regular').value = buySignals;
        document.getElementById('strategyBuyThreshold_options').value = buySignals;
        const sellSignals = settings.min_sell_signals !== undefined ? settings.min_sell_signals : (INDICATOR_DEFAULTS['MIN_BEARISH_SIGNALS'] || 4);
        document.getElementById('strategySellThreshold_regular').value = sellSignals;
        document.getElementById('strategySellThreshold_options').value = sellSignals;
        document.getElementById('strategyTimeframe_regular').value = settings.timeframe || '1Hour';
        document.getElementById('strategyTimeframe_options').value = settings.timeframe || '1Hour';
        if (document.getElementById('strategyBuyMode_regular')) document.getElementById('strategyBuyMode_regular').value = settings.buy_mode || 'indicator';
        if (document.getElementById('strategyBuyMode_options')) document.getElementById('strategyBuyMode_options').value = settings.buy_mode || 'indicator';
        if (document.getElementById('strategyCustomBuyPrice_regular')) document.getElementById('strategyCustomBuyPrice_regular').value = settings.custom_buy_price || '';
        if (document.getElementById('strategyCustomBuyPrice_options')) document.getElementById('strategyCustomBuyPrice_options').value = settings.custom_buy_price || '';
        if (document.getElementById('strategyCustomStopLossPrice_regular')) document.getElementById('strategyCustomStopLossPrice_regular').value = settings.custom_stop_loss_price || '';
        if (document.getElementById('strategyCustomTakeProfitPrice_regular')) document.getElementById('strategyCustomTakeProfitPrice_regular').value = settings.custom_take_profit_price || '';
        if (document.getElementById('strategyCustomStopLossPrice_options')) document.getElementById('strategyCustomStopLossPrice_options').value = settings.custom_stop_loss_price || '';
        if (document.getElementById('strategyCustomTakeProfitPrice_options')) document.getElementById('strategyCustomTakeProfitPrice_options').value = settings.custom_take_profit_price || '';
        if (document.getElementById('strategyEntryAtrMult_regular')) document.getElementById('strategyEntryAtrMult_regular').value = settings.entry_atr_multiplier !== undefined ? settings.entry_atr_multiplier : (settings.atr_stop_multiplier !== undefined ? settings.atr_stop_multiplier : 2);
        if (document.getElementById('strategyEntryAtrMult_options')) document.getElementById('strategyEntryAtrMult_options').value = settings.entry_atr_multiplier !== undefined ? settings.entry_atr_multiplier : (settings.atr_stop_multiplier !== undefined ? settings.atr_stop_multiplier : 2);
        if (document.getElementById('strategyEntryPctMove_regular')) document.getElementById('strategyEntryPctMove_regular').value = settings.entry_pct_move !== undefined ? settings.entry_pct_move : Math.max(1, Math.round((settings.regular_stop_loss_pct !== undefined ? settings.regular_stop_loss_pct : 0.05) * 100));
        if (document.getElementById('strategyEntryPctMove_options')) document.getElementById('strategyEntryPctMove_options').value = settings.entry_pct_move !== undefined ? settings.entry_pct_move : Math.max(1, Math.round((settings.regular_stop_loss_pct !== undefined ? settings.regular_stop_loss_pct : 0.05) * 100));
        if (document.getElementById('strategyEntryAtrStyle_regular')) document.getElementById('strategyEntryAtrStyle_regular').value = settings.entry_atr_style || 'pullback_reclaim';
        if (document.getElementById('strategyEntryAtrStyle_options')) document.getElementById('strategyEntryAtrStyle_options').value = settings.entry_atr_style || 'pullback_reclaim';
        if (document.getElementById('strategyEntryPctStyle_regular')) document.getElementById('strategyEntryPctStyle_regular').value = settings.entry_pct_style || 'dip_reclaim';
        if (document.getElementById('strategyEntryPctStyle_options')) document.getElementById('strategyEntryPctStyle_options').value = settings.entry_pct_style || 'dip_reclaim';
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
        const autopilotEl = document.getElementById('strategyAiAutopilot');
        if (autopilotEl) autopilotEl.checked = aiAutopilot;
        window.handleStrategyAutopilotToggle(aiAutopilot);
        const aiModelSelect = document.getElementById('strategyAiModel');
        if (aiModelSelect) {
            aiModelSelect.value = settings.ai_model || 'llama-3.3-70b-versatile';
        }

        // Load Options settings
        const optEnabled = settings.options_trading || false;
        window.toggleModeActive(optEnabled ? 'options' : 'regular');
        document.getElementById('strategyOptionsType').value = settings.options_type || 'both';
        document.getElementById('strategyOptionsTradeAmount').value = settings.options_trade_amount !== undefined ? settings.options_trade_amount : 100;
        document.getElementById('strategyOptionsExpiryDays').value = settings.options_expiry_days !== undefined ? settings.options_expiry_days : 7;
        document.getElementById('strategyOptionsStrikeType').value = settings.options_strike_type || 'ATM';
        if (document.getElementById('strategySellMode_options')) document.getElementById('strategySellMode_options').value = settings.sell_mode_options || settings.options_exit_strategy || 'hybrid';
        if (document.getElementById('strategyOptionsSlPct')) document.getElementById('strategyOptionsSlPct').value = settings.options_stop_loss_pct !== undefined ? Math.round(settings.options_stop_loss_pct * 100) : 50;
        if (document.getElementById('strategyOptionsTpPct')) document.getElementById('strategyOptionsTpPct').value = settings.options_take_profit_pct !== undefined ? Math.round(settings.options_take_profit_pct * 100) : 100;

        // Load Smart Re-entry and Signal Reset
        ['regular', 'options'].forEach(m => {
            const capMode = m === 'regular' ? 'Regular' : 'Options';
            if (document.getElementById(`strategy${capMode}SignalReset`)) document.getElementById(`strategy${capMode}SignalReset`).checked = settings.enable_signal_reset || false;
            if (document.getElementById(`strategy${capMode}SmartReentry`)) document.getElementById(`strategy${capMode}SmartReentry`).checked = settings.enable_smart_reentry || false;
            if (document.getElementById(`strategy${capMode}SmartReentryType`)) document.getElementById(`strategy${capMode}SmartReentryType`).value = settings.smart_reentry_type || 'RSI';
            if (document.getElementById(`strategy${capMode}RsiCallMax`)) document.getElementById(`strategy${capMode}RsiCallMax`).value = settings.smart_reentry_rsi_call_max !== undefined ? settings.smart_reentry_rsi_call_max : 70;
            if (document.getElementById(`strategy${capMode}RsiPutMin`)) document.getElementById(`strategy${capMode}RsiPutMin`).value = settings.smart_reentry_rsi_put_min !== undefined ? settings.smart_reentry_rsi_put_min : 30;
            if (document.getElementById(`strategy${capMode}VwapCallMaxPct`)) document.getElementById(`strategy${capMode}VwapCallMaxPct`).value = settings.smart_reentry_vwap_call_max_dist_pct !== undefined ? settings.smart_reentry_vwap_call_max_dist_pct : 0.5;
            if (document.getElementById(`strategy${capMode}VwapPutMaxPct`)) document.getElementById(`strategy${capMode}VwapPutMaxPct`).value = settings.smart_reentry_vwap_put_max_dist_pct !== undefined ? settings.smart_reentry_vwap_put_max_dist_pct : 0.5;
        });
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
        if(document.getElementById('strategyOptionsRiskPerTrade')) {
            document.getElementById('strategyOptionsRiskPerTrade').value = hasRisk ? (settings.risk_per_trade * 100).toFixed(0) : 30;
            document.getElementById('strategyOptionsMaxDailyDrawdown').value = hasRisk ? (settings.max_daily_drawdown * 100).toFixed(0) : 60;
            document.getElementById('strategyOptionsMaxPositionPct').value = hasRisk ? (settings.max_position_pct * 100).toFixed(0) : 100;
        }

        document.getElementById('strategyAtrStopMult').value = settings.atr_stop_multiplier !== undefined ? settings.atr_stop_multiplier : (INDICATOR_DEFAULTS['ATR_STOP_MULTIPLIER'] || 2.0);
        document.getElementById('strategyAtrTrailMult').value = settings.atr_trail_multiplier !== undefined ? settings.atr_trail_multiplier : (INDICATOR_DEFAULTS['ATR_TRAIL_MULTIPLIER'] || 3.0);
        document.getElementById('strategyAtrTpMult').value = settings.take_profit_multiplier !== undefined ? settings.take_profit_multiplier : (INDICATOR_DEFAULTS['ATR_TAKE_PROFIT_MULTIPLIER'] || 4.0);
        if (document.getElementById('strategyEnableAtrTrail')) document.getElementById('strategyEnableAtrTrail').checked = settings.enable_atr_trail !== undefined ? settings.enable_atr_trail : true;
        if (document.getElementById('strategyEnablePctTrail')) document.getElementById('strategyEnablePctTrail').checked = settings.enable_pct_trail !== undefined ? settings.enable_pct_trail : true;
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
                chk.checked = true;
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

    updateStrategySignalLabels(window.activeTradingMode || 'regular');
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
    const checkedCount = container.querySelectorAll('.strategy-indicator-check:checked').length;
    const buyInput = document.getElementById(`strategyBuyThreshold_${mode}`);
    const sellInput = document.getElementById(`strategySellThreshold_${mode}`);
    const buyLabel = document.getElementById(`strategyBuyLabel_${mode}`);
    const sellLabel = document.getElementById(`strategySellLabel_${mode}`);

    if (!buyInput || !sellInput || !buyLabel || !sellLabel) return;

    if (checkedCount === 0) {
        buyInput.min = 0;
        sellInput.min = 0;
        buyInput.max = 0;
        sellInput.max = 0;
        buyInput.value = 0;
        sellInput.value = 0;
        buyLabel.textContent = "0 of 0 Signals";
        sellLabel.textContent = "0 of 0 Signals";
        return;
    }

    buyInput.min = 1;
    sellInput.min = 1;
    buyInput.max = checkedCount;
    sellInput.max = checkedCount;

    buyInput.value = normalizeSignalThreshold(buyInput.value, checkedCount, 'buy');
    sellInput.value = normalizeSignalThreshold(sellInput.value, checkedCount, 'sell');

    buyLabel.textContent = `${buyInput.value} of ${checkedCount} Signals`;
    sellLabel.textContent = `${sellInput.value} of ${checkedCount} Signals`;
};

window.adjustStrategyThreshold = function (mode, type, delta) {
    const container = document.getElementById(`indicatorChecksContainer_${mode}`);
    if (!container) return;
    const checkedCount = container.querySelectorAll('.strategy-indicator-check:checked').length;
    if (checkedCount === 0) return;

    const input = type === 'buy' ? document.getElementById(`strategyBuyThreshold_${mode}`) : document.getElementById(`strategySellThreshold_${mode}`);
    if (!input) return;

    let val = normalizeSignalThreshold(input.value, checkedCount, type) + delta;
    val = Math.max(1, Math.min(val, checkedCount));
    input.value = val;
    window.updateStrategySignalLabels(mode);
};

window.toggleRiskSection = function () {
    const content = document.getElementById('riskControlsContent');
    const arrow = document.getElementById('riskArrowIcon');
    if (content.classList.contains('hidden')) {
        content.classList.remove('hidden');
        if(arrow) arrow.classList.add('rotate-180');
    } else {
        content.classList.add('hidden');
        if(arrow) arrow.classList.remove('rotate-180');
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
    const currentValue = select.value;

    let html = '';
    html += '<optgroup label="Groq AI (Cloud)">';
    html += '  <option value="llama-3.3-70b-versatile">llama-3.3-70b-versatile (Default)</option>';
    html += '  <option value="mixtral-8x7b-32768">mixtral-8x7b-32768</option>';
    html += '  <option value="llama-3.1-8b-instant">llama-3.1-8b-instant (Fast / Lightweight)</option>';
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
    const sellModeSelect = document.getElementById('strategySellMode');
    const optionsCard = document.getElementById('strategyOptionsCard');

    if (enabled) {
        populateStrategyAiModelSelect();
        section?.classList.remove('hidden');
        if (sellModeSelect) {
            sellModeSelect.disabled = true;
            sellModeSelect.style.opacity = '0.5';
        }
        // Dim and disable indicators
        document.querySelectorAll('.strategy-indicator-check').forEach(chk => {
            chk.disabled = true;
            chk.parentElement.style.opacity = '0.5';
            chk.parentElement.style.pointerEvents = 'none';
        });
        if (optionsCard) {
            optionsCard.style.opacity = '0.5';
            optionsCard.style.pointerEvents = 'none';
        }
    } else {
        section?.classList.add('hidden');
        if (sellModeSelect) {
            sellModeSelect.disabled = false;
            sellModeSelect.style.opacity = '1';
        }
        // Enable indicators
        document.querySelectorAll('.strategy-indicator-check').forEach(chk => {
            chk.disabled = false;
            chk.parentElement.style.opacity = '1';
            chk.parentElement.style.pointerEvents = 'auto';
        });
        if (optionsCard) {
            const symbol = currentStrategySymbol || '';
            const isCrypto = symbol && (
                symbol.toUpperCase().includes('USD') ||
                symbol.toUpperCase().includes('USDT') ||
                symbol.toUpperCase().includes('USDC') ||
                ['BTC', 'ETH', 'LTC', 'SOL', 'DOGE', 'ADA', 'DOT', 'SHIB', 'AVAX'].some(c => symbol.toUpperCase().startsWith(c))
            );
            if (!isCrypto) {
                optionsCard.style.opacity = '1';
                optionsCard.style.pointerEvents = 'auto';
            }
        }
    }
};

window.handleStrategyOptionsToggle = function (enabled) {
    const section = document.getElementById('strategyOptionsSection');
    const riskCard = document.getElementById('riskControlsContent')?.parentElement;
    const autopilotCard = document.getElementById('strategyAiAutopilot')?.closest('.border');

    if (enabled) {
        section?.classList.remove('hidden');
        if (riskCard) {
            riskCard.style.opacity = '0.5';
            riskCard.style.pointerEvents = 'none';
        }
        if (autopilotCard) {
            autopilotCard.style.opacity = '0.5';
            autopilotCard.style.pointerEvents = 'none';
        }
    } else {
        section?.classList.add('hidden');
        const isAiOn = document.getElementById('strategyAiAutopilot')?.checked || false;
        if (!isAiOn) {
            if (riskCard) {
                riskCard.style.opacity = '1';
                riskCard.style.pointerEvents = 'auto';
            }
        }
        if (autopilotCard) {
            autopilotCard.style.opacity = '1';
            autopilotCard.style.pointerEvents = 'auto';
        }
    }
};

window.updateStrategySmartReentryFields = function () {
    const reentryEnabled = document.getElementById('strategyOptionsSmartReentry')?.checked || false;
    const fieldsDiv = document.getElementById('strategySmartReentryFields');
    if (fieldsDiv) {
        if (reentryEnabled) {
            fieldsDiv.classList.remove('hidden');
            const type = document.getElementById('strategyOptionsSmartReentryType')?.value || 'RSI';
            if (type === 'RSI') {
                document.getElementById('strategyOptionsRsiCallContainer')?.classList.remove('hidden');
                document.getElementById('strategyOptionsRsiPutContainer')?.classList.remove('hidden');
                document.getElementById('strategyOptionsVwapCallContainer')?.classList.add('hidden');
                document.getElementById('strategyOptionsVwapPutContainer')?.classList.add('hidden');
            } else {
                document.getElementById('strategyOptionsRsiCallContainer')?.classList.add('hidden');
                document.getElementById('strategyOptionsRsiPutContainer')?.classList.add('hidden');
                document.getElementById('strategyOptionsVwapCallContainer')?.classList.remove('hidden');
                document.getElementById('strategyOptionsVwapPutContainer')?.classList.remove('hidden');
            }
        } else {
            fieldsDiv.classList.add('hidden');
        }
    }
};

window.checkStrategyCryptoOptionStatus = function (symbol) {
    const isCrypto = symbol && (
        symbol.toUpperCase().includes('USD') ||
        symbol.toUpperCase().includes('USDT') ||
        symbol.toUpperCase().includes('USDC') ||
        ['BTC', 'ETH', 'LTC', 'SOL', 'DOGE', 'ADA', 'DOT', 'SHIB', 'AVAX'].some(c => symbol.toUpperCase().startsWith(c))
    );
    const btnOpt = document.getElementById('btnOptionsMode');
    const tooltip = document.getElementById('optionsCryptoDisableTooltip');

    if (isCrypto) {
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

window.handleStrategyAction = async function () {
    if (strategyModalMode === 'deploy') {
        await confirmAndDeployBot();
    } else {
        await saveTickerSettings();
    }
};

async function saveTickerSettings() {
    if (!currentStrategySymbol) return;

    const mode = window.activeTradingMode || 'regular';

    let buyThresholdVal = parseInt(document.getElementById(`strategyBuyThreshold_${mode}`).value, 10);
    let sellThresholdVal = parseInt(document.getElementById(`strategySellThreshold_${mode}`).value, 10);

    const indicators = [];
    const container = document.getElementById(`indicatorChecksContainer_${mode}`);
    if (container) {
        container.querySelectorAll('.strategy-indicator-check:checked').forEach(chk => {
            indicators.push(chk.value);
        });
    }

    buyThresholdVal = normalizeSignalThreshold(buyThresholdVal, indicators.length, 'buy');
    sellThresholdVal = normalizeSignalThreshold(sellThresholdVal, indicators.length, 'sell');

    const data = {
        ticker: currentStrategySymbol,
        add_to_watchlist: document.getElementById('strategyAddWatchlist')?.checked || false,
        settings: {
            ...getStrategyModeSettings(mode),
            amount: parseFloat((mode === 'options' ? document.getElementById('strategyOptionsTradeAmount')?.value : document.getElementById('strategyCapital')?.value) || '') || null,
            timeframe: document.getElementById(`strategyTimeframe_${mode}`).value || null,
            min_buy_signals: buyThresholdVal,
            min_sell_signals: sellThresholdVal,
            sell_mode: document.getElementById(`strategySellMode_${mode}`)?.value || 'hybrid',
            indicators: indicators,
            paused: window._strategyPaused || false,
            ai_autopilot: document.getElementById('strategyAiAutopilot')?.checked || false,
            ai_model: document.getElementById('strategyAiModel')?.value || 'llama-3.3-70b-versatile',
            risk_per_trade: (document.getElementById(mode === 'regular' ? 'strategyEnableRisk_regular' : 'strategyEnableRisk_options')?.checked) ? parseFloat(document.getElementById(mode === 'regular' ? 'strategyRiskPerTrade' : 'strategyOptionsRiskPerTrade')?.value || 2.0) / 100.0 : null,
            max_daily_drawdown: (document.getElementById(mode === 'regular' ? 'strategyEnableRisk_regular' : 'strategyEnableRisk_options')?.checked) ? parseFloat(document.getElementById(mode === 'regular' ? 'strategyMaxDailyDrawdown' : 'strategyOptionsMaxDailyDrawdown')?.value || (mode === 'regular' ? 5.0 : 50.0)) / 100.0 : null,
            max_position_pct: (document.getElementById(mode === 'regular' ? 'strategyEnableRisk_regular' : 'strategyEnableRisk_options')?.checked) ? parseFloat(document.getElementById(mode === 'regular' ? 'strategyMaxPositionPct' : 'strategyOptionsMaxPositionPct')?.value || 100.0) / 100.0 : null,
            atr_stop_multiplier: parseFloat(document.getElementById('strategyAtrStopMult')?.value || 2.0),
            atr_trail_multiplier: parseFloat(document.getElementById('strategyAtrTrailMult')?.value || 3.0),
            take_profit_multiplier: parseFloat(document.getElementById('strategyAtrTpMult')?.value || 4.0),
            enable_cooldown: document.getElementById(`strategyEnableCooldown_${mode}`)?.checked || false,
            cooldown_minutes: getCooldownMinutesValue(mode),
            options_trading: mode === 'options',
            options_type: document.getElementById('strategyOptionsType')?.value || 'both',
            options_trade_amount: parseFloat(document.getElementById('strategyOptionsTradeAmount')?.value || 100),
            options_expiry_days: parseInt(document.getElementById('strategyOptionsExpiryDays')?.value || 7),
            options_strike_type: document.getElementById('strategyOptionsStrikeType')?.value || 'ATM',
            options_exit_strategy: document.getElementById('strategySellMode_options')?.value || 'hybrid',
            options_stop_loss_pct: parseFloat(document.getElementById('strategyOptionsSlPct')?.value || 50) / 100.0,
            options_take_profit_pct: parseFloat(document.getElementById('strategyOptionsTpPct')?.value || 100) / 100.0,
            enable_signal_reset: document.getElementById(mode === 'regular' ? 'strategyRegularSignalReset' : 'strategyOptionsSignalReset')?.checked || false,
            enable_smart_reentry: document.getElementById(mode === 'regular' ? 'strategyRegularSmartReentry' : 'strategyOptionsSmartReentry')?.checked || false,
            smart_reentry_type: document.getElementById(mode === 'regular' ? 'strategyRegularSmartReentryType' : 'strategyOptionsSmartReentryType')?.value || 'RSI',
            smart_reentry_rsi_call_max: parseInt(document.getElementById(mode === 'regular' ? 'strategyRegularRsiCallMax' : 'strategyOptionsRsiCallMax')?.value || 70),
            smart_reentry_rsi_put_min: parseInt(document.getElementById(mode === 'regular' ? 'strategyRegularRsiPutMin' : 'strategyOptionsRsiPutMin')?.value || 30),
            smart_reentry_vwap_call_max_dist_pct: parseFloat(document.getElementById(mode === 'regular' ? 'strategyRegularVwapCallMaxPct' : 'strategyOptionsVwapCallMaxPct')?.value || 0.5),
            smart_reentry_vwap_put_max_dist_pct: parseFloat(document.getElementById(mode === 'regular' ? 'strategyRegularVwapPutMaxPct' : 'strategyOptionsVwapPutMaxPct')?.value || 0.5)
        }
    };

    try {
        if (mode === 'regular' && window.updateMarketSessionNotice) {
            const sessionCheck = await window.updateMarketSessionNotice();
            if (sessionCheck && sessionCheck.allowed === false) {
                alert(sessionCheck.message || 'This ticker is not eligible for the selected market session.');
                return;
            }
        }
        const headers = await getAuthHeaders();
        const response = await fetch(`${API_BASE}/api/settings/ticker`, {
            method: 'POST',
            headers: headers,
            body: JSON.stringify(data)
        });
        if (response.ok) {
            // Optimistically update the cache so instant navigation works flawlessly
            if (window.lastBotsData && window.lastBotsData.ticker_settings) {
                window.lastBotsData.ticker_settings[currentStrategySymbol] = {
                    ...window.lastBotsData.ticker_settings[currentStrategySymbol],
                    ...data.settings
                };
            }
            closeStrategyModal();
            fetchBotsData(); // let it refresh in background
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
        fetchBotsData();
    } catch (e) {
        console.error('Error toggling watchlist:', e);
    }
};
// ──────────────────────────────────────────────
// Main Fetch Loop
// ──────────────────────────────────────────────
async function fetchBotsData() {
    try {
        const headers = await getAuthHeaders();
        const url = `${API_BASE}/api/dashboard?mode=fast&source=bots&_t=${Date.now()}`;
        const response = await fetch(url, { headers, cache: 'no-store' });
        const data = await response.json();

        // Cache for modal access
        window.lastBotsData = data;
        window.lastDashboardData = data;

        applyBotsData(data);
    } catch (error) {
        console.error('[bots] Fetch error:', error);
    }
}

function applyBotsData(data) {
    if (draggedItem) {
        console.log('[bots] Drag in progress, skipping UI apply to avoid layout disruption.');
        return;
    }
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
    }

    // Portfolio cards
        if (document.getElementById('globalMaxDrawdown')) {
        if (document.activeElement !== document.getElementById('globalMaxDrawdown')) {
            let dd = data.indicator_parameters?.MAX_DAILY_DRAWDOWN;
            if (dd !== undefined && dd !== null) document.getElementById('globalMaxDrawdown').value = Math.round(dd * 100);
        }
    }
    if (document.getElementById('globalHardStop')) {
        if (document.activeElement !== document.getElementById('globalHardStop')) {
            let hs = data.indicator_parameters?.GLOBAL_HARD_STOP_PCT;
            if (hs !== undefined && hs !== null) document.getElementById('globalHardStop').value = Math.round(hs * 100);
        }
    }

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

    // Active bots list
    renderTradelist(data.botScans || data.watchlistScans, data.tradelist, data.tickerAmounts);

    // Auto-select first active bot if none selected
    if (!selectedTicker && data.tradelist && data.tradelist.length > 0) {
        selectedTicker = data.tradelist[0];
    }

    // Try to mirror global AI analysis if it exists, to prevent duplicate queries
    let mirrored = false;
    try {
        const globalAiStr = localStorage.getItem('latest_global_ai_analysis');
        if (globalAiStr) {
            const globalAi = JSON.parse(globalAiStr);
            if ((Date.now() - globalAi.timestamp) < AI_INDICATOR_CACHE_TTL) {
                renderAIIndicatorCard(globalAi.data, true);
                mirrored = true;
            }
        }
    } catch (e) { }

    // Only fetch if we didn't mirror the global AI indicator
    if (selectedTicker && !mirrored) {
        fetchAIIndicator(selectedTicker, currentBackendTf);
    }

    // Execution log
    latestScanHistory = data.recentTrades || [];
    latestOrderHistory = data.orderHistory || [];
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
    'Botbulls4 News AI': { key: 'ENABLE_AI_SENTIMENT', desc: 'AI News Analysis', icon: '🧠' },
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

    const mode = window.activeTradingMode || 'regular';

    // Gather Settings
    const capital = (mode === 'options' ? document.getElementById('strategyOptionsTradeAmount')?.value : document.getElementById('strategyCapital')?.value) || 100;
    const threshold = document.getElementById(`strategyBuyThreshold_${mode}`).value;
    const sellThreshold = document.getElementById(`strategySellThreshold_${mode}`).value;
    const sellMode = document.getElementById(`strategySellMode_${mode}`)?.value || 'hybrid';
    const timeframe = document.getElementById(`strategyTimeframe_${mode}`).value;

    // Gather Indicators
    const indicators = [];
    const container = document.getElementById(`indicatorChecksContainer_${mode}`);
    if (container) {
        container.querySelectorAll('.strategy-indicator-check').forEach(chk => {
            if (chk.checked) indicators.push(chk.value);
        });
    }
    const normalizedBuyThreshold = normalizeSignalThreshold(threshold, indicators.length, 'buy');
    const normalizedSellThreshold = normalizeSignalThreshold(sellThreshold, indicators.length, 'sell');

    const addWatchlistEl = document.getElementById('strategyAddWatchlist');
    const addToWatchlist = addWatchlistEl ? addWatchlistEl.checked : false;

    const symbol = currentStrategySymbol;
    const tradelistContainer = document.getElementById('tradelistContainer');

    if (mode === 'regular' && window.updateMarketSessionNotice) {
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
            threshold: normalizedBuyThreshold,
            sell_threshold: normalizedSellThreshold,
            sell_mode: sellMode,
            indicators: indicators,
            timeframe: timeframe,
            paused: window._strategyPaused || false,
            ai_autopilot: document.getElementById('strategyAiAutopilot')?.checked || false,
            ai_model: document.getElementById('strategyAiModel')?.value || 'llama-3.3-70b-versatile',
            risk_per_trade: (document.getElementById(mode === 'regular' ? 'strategyEnableRisk_regular' : 'strategyEnableRisk_options')?.checked) ? parseFloat(document.getElementById(mode === 'regular' ? 'strategyRiskPerTrade' : 'strategyOptionsRiskPerTrade')?.value || 2.0) / 100.0 : null,
            max_daily_drawdown: (document.getElementById(mode === 'regular' ? 'strategyEnableRisk_regular' : 'strategyEnableRisk_options')?.checked) ? parseFloat(document.getElementById(mode === 'regular' ? 'strategyMaxDailyDrawdown' : 'strategyOptionsMaxDailyDrawdown')?.value || (mode === 'regular' ? 5.0 : 50.0)) / 100.0 : null,
            max_position_pct: (document.getElementById(mode === 'regular' ? 'strategyEnableRisk_regular' : 'strategyEnableRisk_options')?.checked) ? parseFloat(document.getElementById(mode === 'regular' ? 'strategyMaxPositionPct' : 'strategyOptionsMaxPositionPct')?.value || 100.0) / 100.0 : null,
            atr_stop_multiplier: parseFloat(document.getElementById('strategyAtrStopMult')?.value || 2.0),
            atr_trail_multiplier: parseFloat(document.getElementById('strategyAtrTrailMult')?.value || 3.0),
            take_profit_multiplier: parseFloat(document.getElementById('strategyAtrTpMult')?.value || 4.0),
            enable_cooldown: document.getElementById(`strategyEnableCooldown_${mode}`)?.checked || false,
            cooldown_minutes: getCooldownMinutesValue(mode),
            options_trading: mode === 'options',
            options_type: document.getElementById('strategyOptionsType')?.value || 'both',
            options_trade_amount: parseFloat(document.getElementById('strategyOptionsTradeAmount')?.value || 100),
            options_expiry_days: parseInt(document.getElementById('strategyOptionsExpiryDays')?.value || 7),
            options_strike_type: document.getElementById('strategyOptionsStrikeType')?.value || 'ATM',
            options_exit_strategy: document.getElementById('strategySellMode_options')?.value || 'hybrid',
            options_stop_loss_pct: parseFloat(document.getElementById('strategyOptionsSlPct')?.value || 50) / 100.0,
            options_take_profit_pct: parseFloat(document.getElementById('strategyOptionsTpPct')?.value || 100) / 100.0,
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
            await fetchBotsData();
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
                    const { data, fetchedAt } = JSON.parse(cachedRaw);
                    const ageMs = Date.now() - fetchedAt;
                    if (ageMs < 60000) { // Cache valid for 60 seconds
                        console.log(`[bots] Using prefetched cache (${Math.round(ageMs / 1000)}s old) for instant render.`);
                        window.lastBotsData = data;
                        window.lastDashboardData = data;
                        applyBotsData(data); // Render immediately from cache
                        usedCache = true;
                    }
                }
            } catch (e) {
                console.warn('[bots] Could not read bots cache:', e);
            }

            // Always fetch fresh data — immediately if no cache, delayed if cache was used
            fetchPortfolioHistory();
            if (!usedCache) {
                fetchBotsData(); // Full load if no cache
            } else {
                // Soft refresh after a short delay when cache was used
                setTimeout(() => fetchBotsData(), 2000);
            }
            loadOllamaModels();

            setInterval(() => {
                fetchBotsData();
                fetchPortfolioHistory();
            }, REFRESH_INTERVAL);
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
    "Strategy Confidence": "Strategy Confidence combines multiple indicator signals into a single probability score. Purpose: Measures the overall conviction of a bullish or bearish trend.",
    "BotBulls1": "WaveTrend momentum oscillator + Money Flow analysis. Detects reversals when momentum and institutional volume flow align at oversold/overbought extremes.",
    "BotBulls2": "Adaptive ATR trailing stop with trend-smoothed EMA and reversal zone detection. Fires on trend flips confirmed by multi-condition confluence scoring (1-4 strength rating).",
    "BotBulls3": "Heikin-Ashi noise filter + ATR-based trailing stop. Generates buy/sell alerts only on confirmed momentum flips — removing false signals from choppy markets.",
    "Botbulls4 News AI": "Analyzes current news headlines and assigns a bullish/bearish score based on natural language processing of market sentiment."
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
    'Botbulls4 News AI': ['SENTIMENT_BULLISH_PERCENTAGE', 'SENTIMENT_BEARISH_PERCENTAGE'],
    'BotBulls1': ['BOTBULLS1_WT_CHANNEL', 'BOTBULLS1_WT_AVERAGE', 'BOTBULLS1_MFI_CONFIRM'],
    'BotBulls2': ['BOTBULLS2_ATR_MULT', 'BOTBULLS2_TREND_TRACER_PERIOD', 'BOTBULLS2_REVERSAL_ZONE_PERIOD'],
    'BotBulls3': ['BOTBULLS3_ATR_PERIOD', 'BOTBULLS3_ATR_MULT']
};

const INDICATOR_DEFAULTS = {
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

    let currentParams = {};
    let overrides = {};

    if (currentStrategySymbol) {
        const tickerSettings = (window.lastBotsData?.ticker_settings || {})[currentStrategySymbol] || {};
        const tickerOverrides = tickerSettings.indicator_overrides || {};
        overrides = tickerOverrides;
        // Do NOT merge with global indicator_parameters. Fall back to pure system defaults.
        currentParams = { ...tickerOverrides };
    } else {
        currentParams = window.lastBotsData?.indicator_parameters || {};
        overrides = window.lastBotsData?.indicator_overrides || {};
    }

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
    currentEditingIndicator = null;
}

function resetIndicatorSettingsToDefaults() {
    if (!currentEditingIndicator) return;
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
        let url = `${API_BASE}/api/settings/indicators?context=bots`;
        if (currentStrategySymbol) {
            url += `&ticker=${encodeURIComponent(currentStrategySymbol)}`;
        }

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
        console.error("[chart] Error rendering portfolio history:", e);
    }
}

// ──────────────────────────────────────────────
// premium AI indicator & news sentiment briefing engine (decoupled & cached)
// ──────────────────────────────────────────────
window.ollamaModels = [];
async function loadOllamaModels() {
    try {
        const headers = await getAuthHeaders();
        const res = await fetch(`${API_BASE}/api/ollama/models`, { headers });
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

const AI_INDICATOR_CACHE_TTL = 10 * 60 * 1000; // 10 minutes
let _aiIndicatorFetchId = 0; // prevents stale response rendering

function getAICache(key) {
    try {
        const cachedStr = localStorage.getItem(`ai_cache_${key}`);
        if (cachedStr) {
            const cached = JSON.parse(cachedStr);
            if ((Date.now() - cached.timestamp) < AI_INDICATOR_CACHE_TTL) {
                return cached;
            }
            localStorage.removeItem(`ai_cache_${key}`);
        }
    } catch (e) { console.error('Cache read error', e); }
    return null;
}

function setAICache(key, data) {
    try {
        const payload = {
            data: data,
            timestamp: Date.now()
        };
        localStorage.setItem(`ai_cache_${key}`, JSON.stringify(payload));
        localStorage.setItem(`latest_global_ai_analysis`, JSON.stringify(payload));
    } catch (e) { console.error('Cache write error', e); }
}

async function fetchAIIndicator(ticker, timeframe, force = false) {
    if (!ticker) return;
    const selectedModel = localStorage.getItem('selected_ai_model') || 'llama-3.3-70b-versatile';
    const key = `${ticker}|${timeframe}|${selectedModel}`;

    // Check client-side session cache first (unless forced)
    if (!force) {
        const cached = getAICache(key);
        if (cached) {
            renderAIIndicatorCard(cached.data, true);
            return;
        }
    }

    // Show loading skeleton
    _showAIIndicatorLoading(ticker);

    const fetchId = ++_aiIndicatorFetchId;
    try {
        const headers = await getAuthHeaders();
        const url = `${API_BASE}/api/ai-indicator?ticker=${encodeURIComponent(ticker)}&timeframe=${encodeURIComponent(timeframe)}&model=${encodeURIComponent(selectedModel)}${force ? '&force=true' : ''}`;
        const res = await fetch(url, { headers });
        if (!res.ok) throw new Error(`HTTP ${res.status}`);
        const data = await res.json();

        // Guard against stale responses
        if (fetchId !== _aiIndicatorFetchId) return;
        if (data.error) {
            if (selectedModel !== 'llama-3.3-70b-versatile') {
                console.warn(`[ai-indicator] Model ${selectedModel} failed. Falling back to default Groq model.`);
                localStorage.setItem('selected_ai_model', 'llama-3.3-70b-versatile');
                fetchAIIndicator(ticker, timeframe, force);
                return;
            }
            _showAIIndicatorError(data.error, ticker);
            return;
        }

        setAICache(key, data);
        renderAIIndicatorCard(data, false);
    } catch (e) {
        if (fetchId === _aiIndicatorFetchId) {
            console.error('[ai-indicator] Fetch error:', e);
            if (selectedModel !== 'llama-3.3-70b-versatile') {
                console.warn(`[ai-indicator] Fetch failed for ${selectedModel}. Falling back to default Groq model.`);
                localStorage.setItem('selected_ai_model', 'llama-3.3-70b-versatile');
                fetchAIIndicator(ticker, timeframe, force);
                return;
            }
            _showAIIndicatorError('Could not load AI analysis', ticker);
        }
    }
}

function _getAIModelSelectHtml(selectedModel, isError = false) {
    let optionsHtml = '';
    optionsHtml += '<optgroup label="Groq AI (Cloud)">';
    const groqModels = [
        { value: 'llama-3.3-70b-versatile', label: 'Llama 3.3 70B' },
        { value: 'mixtral-8x7b-32768', label: 'Mixtral 8x7B' },
        { value: 'llama-3.1-8b-instant', label: 'Llama 3.1 8B (Fast)' }
    ];
    groqModels.forEach(m => {
        const sel = selectedModel === m.value ? 'selected' : '';
        optionsHtml += `<option value="${m.value}" ${sel}>${m.label}</option>`;
    });
    optionsHtml += '</optgroup>';

    optionsHtml += '<optgroup label="Ollama (Local)">';
    if (window.ollamaModels && window.ollamaModels.length > 0) {
        window.ollamaModels.forEach(m => {
            const val = `ollama/${m}`;
            const sel = selectedModel === val ? 'selected' : '';
            optionsHtml += `<option value="${val}" ${sel}>${m}</option>`;
        });
    } else {
        optionsHtml += `<option disabled>No local models found</option>`;
    }
    optionsHtml += '</optgroup>';

    const borderCls = isError ? 'border-red-200 text-red-900 focus:ring-red-400' : 'border-amber-200/50 text-amber-900 focus:ring-amber-400';
    return `
        <select onchange="changeAIIndicatorModel(this.value)" class="bg-white/80 backdrop-blur border ${borderCls} text-[10px] font-black px-2 py-0.5 rounded-lg shadow-sm focus:ring-1 outline-none cursor-pointer">
            ${optionsHtml}
        </select>
    `;
}

window.changeAIIndicatorModel = async function (model) {
    localStorage.setItem('selected_ai_model', model);
    try {
        const headers = await getAuthHeaders();
        await fetch(`${API_BASE}/api/settings/indicators?context=dashboard`, {
            method: 'POST',
            headers: headers,
            body: JSON.stringify({ AI_INDICATOR_MODEL: model })
        });
    } catch (e) {
        console.error('[ai-indicator] Failed to save model settings to backend:', e);
    }
    const ticker = selectedTicker || 'TSLA';
    const timeframe = currentBackendTf || '1Hour';
    _showAIIndicatorLoading(ticker);
    try {
        await fetchAIIndicator(ticker, timeframe, false);
    } catch (err) {
        console.error(err);
    }
};

function _showAIIndicatorLoading(ticker) {
    const el = document.getElementById('aiIndicatorCard');
    if (!el) return;

    const selectedModel = localStorage.getItem('selected_ai_model') || 'llama-3.3-70b-versatile';
    const isOllamaActive = selectedModel.startsWith('ollama/') || selectedModel.startsWith('ollama:');
    const badgeHtml = isOllamaActive
        ? `<span class="px-1.5 py-0.5 rounded text-[0.55rem] font-black bg-gradient-to-r from-indigo-500 to-blue-600 text-white border border-indigo-600/50 shadow-sm">OLLAMA</span>`
        : `<span class="px-1.5 py-0.5 rounded text-[0.55rem] font-black bg-gradient-to-r from-amber-200 to-amber-300 text-amber-800 border border-amber-400/50 shadow-sm">GROQ AI</span>`;
    const selectHtml = _getAIModelSelectHtml(selectedModel, false);

    el.className = 'ai-indicator-card ai-indicator-loading border-amber-200/60 bg-gradient-to-br from-amber-50/50 to-orange-50/30';
    el.innerHTML = `
        <div class="ai-orb bg-gradient-to-r from-amber-400 to-orange-500 opacity-20"></div>
        <div class="flex flex-wrap items-center justify-between gap-y-2 mb-3 relative z-10">
            <div class="flex flex-wrap items-center gap-1.5 sm:gap-2">
                <span class="text-base drop-shadow-sm">🤖</span>
                <span class="font-black text-xs text-amber-900 tracking-wide uppercase">BotBulls AI Analysis - ${ticker || '---'}</span>
                ${badgeHtml}
            </div>
            <div class="flex items-center gap-2 relative z-30">
                ${selectHtml}
                <button onclick="openAIInfoModal()" title="How AI analysis works" class="w-5 h-5 rounded-full flex items-center justify-center text-[10px] font-bold text-amber-700 hover:text-amber-900 bg-amber-100 hover:bg-amber-200 transition-colors border border-amber-300/50 shadow-sm">
                    ℹ
                </button>
                <button disabled class="w-5 h-5 rounded-full flex items-center justify-center text-xs text-amber-300 bg-amber-50 border border-amber-200 animate-spin">
                    ↻
                </button>
            </div>
        </div>
        <div class="space-y-2 animate-pulse relative z-10">
            <div class="h-8 bg-amber-200/60 rounded-lg w-2/3"></div>
            <div class="h-4 bg-amber-200/40 rounded w-full"></div>
            <div class="h-4 bg-amber-200/40 rounded w-4/5"></div>
            <div class="h-4 bg-amber-200/40 rounded w-3/5"></div>
        </div>
        <p class="text-[0.6rem] text-amber-700 mt-3 flex items-center gap-1 font-bold relative z-10">
            <svg class="h-3 w-3 animate-spin text-amber-600" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                <path stroke-linecap="round" stroke-linejoin="round" stroke-width="3" d="M12 4v4m0 8v4m4-12h-4m4 8h-4" />
            </svg>
            Analyzing market catalysts and news sentiment...
        </p>
    `;
}

function _showAIIndicatorError(err, ticker) {
    const el = document.getElementById('aiIndicatorCard');
    if (!el) return;

    const selectedModel = localStorage.getItem('selected_ai_model') || 'llama-3.3-70b-versatile';
    const selectHtml = _getAIModelSelectHtml(selectedModel, true);

    el.className = 'ai-indicator-card ai-indicator-error relative overflow-hidden p-4 rounded-xl border transition-all duration-300';
    el.innerHTML = `
        <div class="flex flex-wrap items-center justify-between gap-y-2 mb-3 relative z-10">
            <div class="flex flex-wrap items-center gap-1.5 sm:gap-2">
                <span class="text-base">🤖</span>
                <span class="font-black text-xs text-red-800 tracking-wide uppercase">BotBulls AI Analysis - ${ticker || '---'}</span>
                <span class="px-1.5 py-0.5 rounded text-[0.55rem] font-bold bg-red-100 text-red-600 border border-red-300/50">Error</span>
            </div>
            <div class="flex items-center gap-2 relative z-30">
                ${selectHtml}
                <button onclick="openAIInfoModal()" title="How AI analysis works" class="w-5 h-5 rounded-full flex items-center justify-center text-[10px] font-bold text-red-500 hover:text-red-700 bg-red-100/50 hover:bg-red-100 transition-colors border border-red-300/30">
                    ℹ
                </button>
                <button id="aiRefreshBtn" onclick="refreshAIIndicator()" title="Force refresh AI analysis" class="w-5 h-5 rounded-full flex items-center justify-center text-xs text-red-500 hover:text-red-700 bg-red-100/50 hover:bg-red-100 transition-colors border border-red-300/30">
                    ↻
                </button>
            </div>
        </div>
        <p class="text-[0.6rem] text-red-500 relative z-10">${err || 'Could not load AI analysis'}</p>
    `;
}

function renderAIIndicatorCard(data, fromCache = false) {
    const el = document.getElementById('aiIndicatorCard');
    if (!el) return;

    const signal = data.signal || 'HOLD';
    const confidence = data.confidence || 0;
    const confPct = Math.round(confidence * 100);
    const summary = data.summary || '';
    const catalyst = data.key_catalyst || '';
    const risks = data.risk_factors || '';
    const regime = data.volatility_regime || 'NORMAL';
    const posUsd = data.position_size_usd;
    const posPct = data.position_size_pct;
    const leverage = data.leverage || '1x';
    const headlineCount = data.headline_count || 0;
    const cached = fromCache || data.cached || false;

    const sentSummary = data.sentiment_summary || '';
    const sentKeyFactor = data.sentiment_key_factor || 'Mixed Catalysts';
    const sentConfidence = data.sentiment_confidence != null ? data.sentiment_confidence : confidence;
    const sentConfPct = Math.round(sentConfidence * 100);

    const fmtPrice = (v) => (v == null || parseFloat(v) === 0) ? '—' : (parseFloat(v) < 10 ? `$${parseFloat(v).toFixed(4)}` : `$${parseFloat(v).toFixed(2)}`);
    const fmtUsd = (v) => (v == null || parseFloat(v) === 0) ? '—' : `$${parseFloat(v).toLocaleString('en-US', { minimumFractionDigits: 0, maximumFractionDigits: 0 })}`;

    const confBarWidth = `${confPct}%`;
    const confBarColor = confPct >= 70 ? 'bg-emerald-500' : confPct >= 45 ? 'bg-amber-500' : 'bg-rose-500';
    const confTextColor = confPct >= 70 ? 'text-emerald-600' : confPct >= 45 ? 'text-amber-600' : 'text-rose-600';

    const sentScore = data.sentiment_score != null ? parseFloat(data.sentiment_score) : 0.0;
    const sentPct = Math.round(((sentScore + 1.0) / 2.0) * 100);
    const sentBarWidth = `${sentPct}%`;
    let sentBarColor = 'bg-slate-400';
    let sentTextColor = 'text-slate-500';

    if (sentPct > 75) {
        sentBarColor = 'bg-emerald-500';
        sentTextColor = 'text-emerald-600';
    } else if (sentPct > 55) {
        sentBarColor = 'bg-amber-500';
        sentTextColor = 'text-amber-600';
    } else if (sentPct < 25) {
        sentBarColor = 'bg-rose-500';
        sentTextColor = 'text-rose-600';
    } else if (sentPct < 45) {
        sentBarColor = 'bg-amber-500';
        sentTextColor = 'text-amber-600';
    }

    const sigColors = {
        BUY: { bg: 'ai-indicator-buy', badge: 'bg-emerald-100 text-emerald-700 border-emerald-300/50', icon: '▲', iconCls: 'text-emerald-600', regimeCls: 'text-emerald-600' },
        SELL: { bg: 'ai-indicator-sell', badge: 'bg-rose-100 text-rose-700 border-rose-300/50', icon: '▼', iconCls: 'text-rose-600', regimeCls: 'text-rose-600' },
        HOLD: { bg: 'ai-indicator-hold', badge: 'bg-slate-100 text-slate-700 border-slate-300/50', icon: '●', iconCls: 'text-slate-500', regimeCls: 'text-slate-500' }
    };
    const colors = sigColors[signal] || sigColors['HOLD'];

    const cachedBadge = cached
        ? `<span class="text-[0.5rem] text-amber-600 opacity-60 ml-1">cached</span>`
        : '';

    const selectedModel = localStorage.getItem('selected_ai_model') || 'llama-3.3-70b-versatile';
    const isOllamaActive = selectedModel.startsWith('ollama/') || selectedModel.startsWith('ollama:');
    const badgeHtml = isOllamaActive
        ? `<span class="px-1.5 py-0.5 rounded text-[0.55rem] font-black bg-gradient-to-r from-indigo-500 to-blue-600 text-white border border-indigo-600/50 shadow-sm">OLLAMA</span>`
        : `<span class="px-1.5 py-0.5 rounded text-[0.55rem] font-black bg-gradient-to-r from-amber-200 to-amber-300 text-amber-800 border border-amber-400/50 shadow-sm">GROQ AI</span>`;
    const selectHtml = _getAIModelSelectHtml(selectedModel, false);

    el.className = `ai-indicator-card ${colors.bg} relative overflow-hidden p-4 rounded-xl border border-amber-200/60 bg-gradient-to-br from-amber-50/50 to-orange-50/30 transition-all duration-300`;
    el.innerHTML = `
        <div class="ai-orb bg-gradient-to-r from-amber-400 to-orange-500 opacity-20"></div>
        <div class="flex flex-wrap items-center justify-between gap-y-2 mb-3 relative z-10">
            <div class="flex flex-wrap items-center gap-1.5 sm:gap-2">
                <span class="text-base drop-shadow-sm">🤖</span>
                <span class="font-black text-xs text-amber-900 tracking-wide uppercase" id="aiIndicatorHeader">BotBulls AI Analysis - ${data.ticker || selectedTicker || '---'}</span>
                ${badgeHtml}
                ${cachedBadge}
            </div>
            <div class="flex items-center gap-2 relative z-30">
                ${selectHtml}
                <button onclick="openAIInfoModal()" title="How AI analysis works" class="w-5 h-5 rounded-full flex items-center justify-center text-[10px] font-bold text-amber-700 hover:text-amber-900 bg-amber-100 hover:bg-amber-200 transition-colors border border-amber-300/50 shadow-sm">
                    ℹ
                </button>
                <button id="aiRefreshBtn" onclick="refreshAIIndicator()" title="Force refresh AI analysis" class="w-5 h-5 rounded-full flex items-center justify-center text-xs text-amber-700 hover:text-amber-900 bg-amber-100 hover:bg-amber-200 transition-colors border border-amber-300/50 shadow-sm">
                    ↻
                </button>
            </div>
        </div>

        <div class="flex items-center justify-between mb-3 relative z-10">
            <div class="flex items-center gap-2 text-left">
                <div>
                    <div class="text-[0.55rem] text-slate-400 uppercase font-black tracking-wider">Trade Signal</div>
                    <div class="flex items-center gap-1.5 mt-0.5">
                        <span class="px-2 py-0.5 text-xs font-black rounded-full border ${colors.badge} flex items-center gap-1">
                            ${signal} <span class="text-[9px] ${colors.iconCls}">${colors.icon}</span>
                        </span>
                    </div>
                </div>
            </div>
            <div class="text-right">
                <div class="text-[0.55rem] text-slate-400 uppercase font-black tracking-wider">Volatility Regime</div>
                <div class="text-xs font-black ${colors.regimeCls}">${regime}</div>
            </div>
        </div>

        <div class="mb-3.5 relative z-10 text-left">
            <div class="text-[0.55rem] text-slate-500 font-extrabold uppercase tracking-wider mb-1">Decision Confidence</div>
            <div class="flex items-center gap-2">
                <div class="flex-1 bg-slate-200/50 rounded-full h-1.5 overflow-hidden">
                    <div class="h-1.5 rounded-full ${confBarColor} transition-all duration-500" style="width: ${confBarWidth}"></div>
                </div>
                <span class="text-[10px] font-black leading-none ${confTextColor} px-1.5 py-0.5 rounded bg-white/60 border border-slate-200/50 shadow-sm">${confPct}%</span>
            </div>
        </div>

        <div class="mb-3.5 relative z-10 text-left">
            <div class="text-[0.55rem] text-slate-500 font-extrabold uppercase tracking-wider mb-1">News Sentiment Score</div>
            <div class="flex items-center gap-2">
                <div class="flex-1 bg-slate-200/50 rounded-full h-1.5 overflow-hidden">
                    <div class="h-1.5 rounded-full ${sentBarColor} transition-all duration-500" style="width: ${sentBarWidth}"></div>
                </div>
                <span class="text-[10px] font-black leading-none ${sentTextColor} px-1.5 py-0.5 rounded bg-white/60 border border-slate-200/50 shadow-sm">${sentPct}%</span>
            </div>
        </div>

        <div class="grid grid-cols-3 gap-2 mb-3.5 relative z-10">
            <div class="p-1.5 rounded-lg bg-white/40 border border-slate-200/50 text-center">
                <div class="text-[0.5rem] text-slate-400 uppercase tracking-wider mb-0.5">Entry Price</div>
                <div class="text-[0.65rem] font-black text-slate-800">${fmtPrice(data.entry_price !== undefined ? data.entry_price : data.price)}</div>
            </div>
            <div class="p-1.5 rounded-lg bg-white/40 border border-slate-200/50 text-center">
                <div class="text-[0.5rem] text-slate-400 uppercase tracking-wider mb-0.5">Stop Loss</div>
                <div class="text-[0.65rem] font-black text-rose-600">${fmtPrice(data.stop_loss)}</div>
            </div>
            <div class="p-1.5 rounded-lg bg-white/40 border border-slate-200/50 text-center">
                <div class="text-[0.5rem] text-slate-400 uppercase tracking-wider mb-0.5">Sell Target</div>
                <div class="text-[0.65rem] font-black text-emerald-600">${fmtPrice(data.sell_target !== undefined ? data.sell_target : data.target_price)}</div>
            </div>
        </div>

        <div class="flex items-center gap-2 mb-3 relative z-10 p-1.5 rounded-lg bg-white/40 border border-violet-200/60">
            <div class="flex-1 text-center">
                <div class="text-[0.5rem] text-violet-500 uppercase tracking-wider mb-0.5">Position Size</div>
                <div class="text-[0.6rem] font-black text-violet-900">${(posUsd == null || parseFloat(posUsd) === 0) ? '—' : `${fmtUsd(posUsd)} <span class="text-violet-500 font-normal">(${Math.round(posPct * 100)}%)</span>`}</div>
            </div>
            <div class="w-px h-6 bg-violet-200/60"></div>
            <div class="flex-1 text-center">
                <div class="text-[0.5rem] text-violet-500 uppercase tracking-wider mb-0.5">Leverage</div>
                <div class="text-[0.6rem] font-black text-violet-900">${leverage}</div>
            </div>
        </div>

        <div class="mb-3 p-2 rounded-lg bg-fuchsia-50/50 border border-fuchsia-200/50 text-left relative z-10">
            <span class="text-[0.52rem] font-black text-fuchsia-600 uppercase tracking-widest block mb-0.5">🔥 Key Market Factor</span>
            <p class="text-[0.65rem] font-bold text-fuchsia-800 leading-snug">${sentKeyFactor}</p>
        </div>

        ${sentSummary ? `
        <div class="mb-3 p-2.5 rounded-lg bg-white/50 border border-purple-200/50 text-left relative z-10">
            <span class="text-[0.52rem] font-black text-purple-600 uppercase tracking-widest block mb-0.5">📰 AI News Briefing & Catalyst Summary</span>
            <p class="text-[0.6rem] text-violet-700 leading-relaxed font-medium">${sentSummary}</p>
        </div>
        ` : ''}

        ${summary ? `
        <div class="mb-3 p-3 rounded-lg bg-white/30 border border-violet-200/50 text-left relative z-10 shadow-sm">
            <span class="text-[0.52rem] font-black text-violet-600 uppercase tracking-widest block mb-1">📝 Comprehensive Trade Thesis Summary</span>
            <p class="text-[0.62rem] text-violet-700 leading-relaxed font-semibold">${summary}</p>
        </div>
        ` : ''}

        <div class="grid grid-cols-2 gap-2 mb-3 relative z-10">
            <div class="p-2.5 rounded-lg bg-emerald-50/40 border border-emerald-200/40 text-left shadow-sm">
                <span class="text-[0.52rem] font-black text-emerald-600 uppercase tracking-widest block mb-1">⚡ Primary Catalyst</span>
                <p class="text-[0.6rem] text-emerald-800 leading-snug font-bold">${catalyst || 'Confluence alignment'}</p>
            </div>
            <div class="p-2.5 rounded-lg bg-rose-50/40 border border-rose-200/40 text-left shadow-sm">
                <span class="text-[0.52rem] font-black text-rose-600 uppercase tracking-widest block mb-1">⚠ Main Risk Factors</span>
                <p class="text-[0.6rem] text-rose-800 leading-snug font-bold">${risks || 'Trend invalidation'}</p>
            </div>
        </div>

        <div class="flex items-center justify-between pt-2 border-t border-violet-200/30 text-[8px] text-violet-400 relative z-10">
            <span>${headlineCount} headlines analyzed</span>
            <span>Advisory only — not investment advice</span>
        </div>
    `;
}

window.refreshAIIndicator = async function () {
    const refreshBtn = document.getElementById('aiRefreshBtn');
    if (refreshBtn) {
        refreshBtn.disabled = true;
        refreshBtn.classList.add('animate-spin');
    }
    const ticker = selectedTicker;
    const timeframe = currentBackendTf;
    try {
        await fetchAIIndicator(ticker, timeframe, true);
    } catch (err) {
        console.error(err);
    } finally {
        if (refreshBtn) {
            refreshBtn.disabled = false;
            refreshBtn.classList.remove('animate-spin');
        }
    }
};

window.openAIInfoModal = function () {
    const modal = document.getElementById('aiInfoModal');
    if (modal) modal.classList.remove('hidden');
};

window.closeAIInfoModal = function () {
    const modal = document.getElementById('aiInfoModal');
    if (modal) modal.classList.add('hidden');
};



window.toggleModeActive = function (mode) {
    const regBox = document.getElementById('regularTradingBox');
    const optBox = document.getElementById('optionsTradingBox');
    const pill = document.getElementById('modeTogglePill');
    const btnReg = document.getElementById('btnRegularMode');
    const btnOpt = document.getElementById('btnOptionsMode');
    
    // Prevent options if disabled
    if (mode === 'options' && btnOpt && btnOpt.disabled) {
        return;
    }

    if (mode === 'regular') {
        window.activeTradingMode = 'regular';
        if (pill) pill.style.transform = 'translateX(0)';
        if (btnReg) { btnReg.classList.remove('text-indigo-400'); btnReg.classList.add('text-indigo-950'); }
        if (btnOpt) { btnOpt.classList.remove('text-indigo-950'); btnOpt.classList.add('text-indigo-400'); }
        if (regBox) regBox.classList.remove('hidden');
        if (optBox) optBox.classList.add('hidden');
        window.updateStrategySignalLabels('regular');
    } else {
        window.activeTradingMode = 'options';
        if (pill) pill.style.transform = 'translateX(100%)';
        if (btnReg) { btnReg.classList.remove('text-indigo-950'); btnReg.classList.add('text-indigo-400'); }
        if (btnOpt) { btnOpt.classList.remove('text-indigo-400'); btnOpt.classList.add('text-indigo-950'); }
        if (optBox) optBox.classList.remove('hidden');
        if (regBox) regBox.classList.add('hidden');
        window.updateStrategySignalLabels('options');
    }

    if (typeof window.updateSwingSettingsVisibility === 'function') {
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



window.updateRegularExitFields = function() {
    const mode = document.getElementById('strategySellMode_regular')?.value || 'indicator';
    const showIndicator = ['indicator', 'hybrid', 'hybrid_pct', 'hybrid_pivot', 'hybrid_high_low', 'hybrid_swing'].includes(mode);
    const showCustom    = mode === 'custom_sltp';
    const showATR       = ['sltp', 'hybrid'].includes(mode);
    const showPct       = ['percentage', 'hybrid_pct'].includes(mode);
    
    if(document.getElementById('exitFieldsIndicator')) document.getElementById('exitFieldsIndicator').classList.toggle('hidden', !showIndicator);
    if(document.getElementById('exitFieldsCustom_regular')) document.getElementById('exitFieldsCustom_regular').classList.toggle('hidden', !showCustom);
    if(document.getElementById('exitFieldsATR')) document.getElementById('exitFieldsATR').classList.toggle('hidden', !showATR);
    if(document.getElementById('exitFieldsPct')) document.getElementById('exitFieldsPct').classList.toggle('hidden', !showPct);
    updateSwingSettingsVisibility('regular');

    if (window.updateLivePreview_bots) window.updateLivePreview_bots('regular', 'exit');
};

window.updateRegularEntryFields = function() {
    const mode = document.getElementById('strategyBuyMode_regular')?.value || 'indicator';
    const showIndicator = ['indicator', 'hybrid_atr', 'hybrid_pct', 'hybrid_pivot', 'hybrid_high_low', 'hybrid_swing'].includes(mode);
    const showCustom    = mode === 'custom_price';
    const showATR       = ['atr', 'hybrid_atr'].includes(mode);
    const showPct       = ['percentage', 'hybrid_pct'].includes(mode);
    const showPivot     = ['pivot_points', 'hybrid_pivot'].includes(mode);
    const showHL        = ['high_low', 'hybrid_high_low'].includes(mode);
    const showSwing     = ['swing_points', 'hybrid_swing'].includes(mode);
    if(document.getElementById('strategyBuyFields_regular')) document.getElementById('strategyBuyFields_regular').classList.toggle('hidden', !showIndicator);
    if(document.getElementById('strategyCustomBuyFields_regular')) document.getElementById('strategyCustomBuyFields_regular').classList.toggle('hidden', !showCustom);
    if(document.getElementById('strategyEntryAtrFields_regular')) document.getElementById('strategyEntryAtrFields_regular').classList.toggle('hidden', !showATR);
    if(document.getElementById('strategyEntryPctFields_regular')) document.getElementById('strategyEntryPctFields_regular').classList.toggle('hidden', !showPct);
    if(document.getElementById('strategyEntryPivotFields_regular')) document.getElementById('strategyEntryPivotFields_regular').classList.toggle('hidden', !showPivot);
    if(document.getElementById('strategyEntryHighLowFields_regular')) document.getElementById('strategyEntryHighLowFields_regular').classList.toggle('hidden', !showHL);
    if(document.getElementById('strategySwingEntrySettings_regular')) document.getElementById('strategySwingEntrySettings_regular').classList.toggle('hidden', !showSwing);
    updateSwingSettingsVisibility('regular');
    if (window.updateLivePreview_bots) window.updateLivePreview_bots('regular', 'entry');
};

window.updateOptionsEntryFields = function() {
    const mode = document.getElementById('strategyBuyMode_options')?.value || 'indicator';
    const showIndicator = ['indicator', 'hybrid_atr', 'hybrid_pct', 'hybrid_pivot', 'hybrid_high_low', 'hybrid_swing'].includes(mode);
    const showCustom    = mode === 'custom_price';
    const showATR       = ['atr', 'hybrid_atr'].includes(mode);
    const showPct       = ['percentage', 'hybrid_pct'].includes(mode);
    const showPivot     = ['pivot_points', 'hybrid_pivot'].includes(mode);
    const showHL        = ['high_low', 'hybrid_high_low'].includes(mode);
    const showSwing     = ['swing_points', 'hybrid_swing'].includes(mode);
    if(document.getElementById('strategyBuyFields_options')) document.getElementById('strategyBuyFields_options').classList.toggle('hidden', !showIndicator);
    if(document.getElementById('strategyCustomBuyFields_options')) document.getElementById('strategyCustomBuyFields_options').classList.toggle('hidden', !showCustom);
    if(document.getElementById('strategyEntryAtrFields_options')) document.getElementById('strategyEntryAtrFields_options').classList.toggle('hidden', !showATR);
    if(document.getElementById('strategyEntryPctFields_options')) document.getElementById('strategyEntryPctFields_options').classList.toggle('hidden', !showPct);
    if(document.getElementById('strategyEntryPivotFields_options')) document.getElementById('strategyEntryPivotFields_options').classList.toggle('hidden', !showPivot);
    if(document.getElementById('strategyEntryHighLowFields_options')) document.getElementById('strategyEntryHighLowFields_options').classList.toggle('hidden', !showHL);
    if(document.getElementById('strategySwingEntrySettings_options')) document.getElementById('strategySwingEntrySettings_options').classList.toggle('hidden', !showSwing);
    updateSwingSettingsVisibility('options');
    if (window.updateLivePreview_bots) window.updateLivePreview_bots('options', 'entry');
};

window.updateOptionsExitFields = function() {
    const mode = document.getElementById('strategySellMode_options')?.value || 'hybrid';
    
    const showIndicator = ['indicator', 'hybrid', 'hybrid_pct', 'hybrid_pivot', 'hybrid_high_low', 'hybrid_swing'].includes(mode);
    const showCustom    = mode === 'custom_sltp';
    const showATR       = ['sltp', 'hybrid'].includes(mode);
    const showPct       = ['percentage', 'hybrid_pct'].includes(mode);
    
    if(document.getElementById('exitFieldsIndicator_options')) document.getElementById('exitFieldsIndicator_options').classList.toggle('hidden', !showIndicator);
    if(document.getElementById('exitFieldsCustom_options')) document.getElementById('exitFieldsCustom_options').classList.toggle('hidden', !showCustom);
    if(document.getElementById('exitFieldsATR_options')) document.getElementById('exitFieldsATR_options').classList.toggle('hidden', !showATR);
    if(document.getElementById('exitFieldsPct_options')) document.getElementById('exitFieldsPct_options').classList.toggle('hidden', !showPct);
    updateSwingSettingsVisibility('options');

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
    const sym = String(symbol || '').toUpperCase();
    return sym.includes('USD') || sym.includes('USDT') || sym.includes('USDC') || ['BTC', 'ETH', 'XRP', 'DOGE', 'LTC', 'ADA', 'SOL'].includes(sym);
}

function getStrategyMarketSession(mode = 'regular') {
    return getMarketSessionForStrategy(mode);
}

function buildSwingEntryPreviewHtml(mode, isOptions, symbol, simPrice) {
    const buyMode = document.getElementById(isOptions ? 'strategyBuyMode_options' : 'strategyBuyMode_regular')?.value || 'indicator';
    const entryMode = document.getElementById(`strategySwingEntryMode_${mode}`)?.value || 'bounce_retest';
    const lookbackDays = getSwingLookbackDaysValue(mode, 'entry');
    const marketSession = getStrategyMarketSession(mode);
    const priceHistory = window.currentAtrPreviewData?.price_history || [];
    const swingCtx = computeSwingPreviewContext(priceHistory, simPrice, lookbackDays, marketSession, isPreviewCryptoSymbol(symbol));
    const support = swingCtx.nearest_support;
    const resistance = swingCtx.nearest_resistance;
    const modeLabel = buyMode === 'hybrid_swing' ? 'Hybrid (Indicator + Swing)' : 'Swing Points';
    const parts = [`[Entry Strategy] ${modeLabel}`, `[Swing Entry] ${entryMode === 'breakout_retest' ? 'Breakout / Retest' : 'Bounce / Retest'} | ${lookbackDays}D`];

    if (buyMode === 'hybrid_swing') {
        parts.push('[Hybrid] Swing setup or indicator buy threshold');
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
        if (resistance && touchZone(latest, resistance.price, tolerance) && latest.close > resistance.price + tolerance && isBullishConfirmation(latest, prev)) {
            reason = `Breakout retest confirmed above ${formatSwingLevel(resistance.price)} (${resistance.touches} touches)`;
        } else if (support && touchZone(latest, support.price, tolerance) && latest.close < support.price - tolerance && isBearishConfirmation(latest, prev)) {
            reason = `Breakdown retest confirmed below ${formatSwingLevel(support.price)} (${support.touches} touches)`;
        } else if (resistance) {
            reason = `Waiting for breakout retest near ${formatSwingLevel(resistance.price)}`;
        }
    } else {
        if (support && touchZone(latest, support.price, tolerance) && latest.close > support.price + (tolerance * 0.1) && isBullishConfirmation(latest, prev)) {
            reason = `Bounce confirmed from ${formatSwingLevel(support.price)} (${support.touches} touches)`;
        } else if (resistance && touchZone(latest, resistance.price, tolerance) && latest.close < resistance.price - (tolerance * 0.1) && isBearishConfirmation(latest, prev)) {
            reason = `Rejection confirmed from ${formatSwingLevel(resistance.price)} (${resistance.touches} touches)`;
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
    const lookbackDays = getSwingLookbackDaysValue(mode, 'exit');
    const marketSession = getStrategyMarketSession(mode);
    const priceHistory = window.currentAtrPreviewData?.price_history || [];
    const swingCtx = computeSwingPreviewContext(priceHistory, simPrice, lookbackDays, marketSession, isPreviewCryptoSymbol(symbol));
    const support = swingCtx.nearest_support;
    const resistance = swingCtx.nearest_resistance;
    const modeLabel = sellMode === 'hybrid_swing' ? 'Hybrid (Indicator + Swing)' : 'Swing Points';
    const parts = [`[Exit Strategy] ${modeLabel}`, `[Swing Exit] ${lookbackDays}D`];

    if (sellMode === 'hybrid_swing') {
        parts.push('[Hybrid] Swing setup or indicator sell threshold');
    }

    if (!swingCtx.candles.length) {
        parts.push('Awaiting swing levels...');
        return parts.join(' | ');
    }

    const isLong = true;
    const tpZone = isLong ? resistance : support;
    const slZone = isLong ? support : resistance;
    parts.push(tpZone ? `Take Profit: ${formatSwingLevel(tpZone.price)} (${tpZone.touches} touches)` : 'Take Profit: N/A');
    parts.push(slZone ? `Stop Loss: ${formatSwingLevel(slZone.price)} (${slZone.touches} touches)` : 'Stop Loss: N/A');
    if (tpZone) parts.push(`TP Strength: ${tpZone.score.toFixed(2)}`);
    if (slZone) parts.push(`SL Strength: ${slZone.score.toFixed(2)}`);
    return parts.join(' | ');
}

window.fetchLiveAtrForPreview = async function(ticker, overrideTf) {
    if (!ticker) return;
    try {
        const headers = await getAuthHeaders();
        const activeMode = window.activeTradingMode || 'regular';
        const tf = overrideTf || document.getElementById(`strategyTimeframe_${activeMode}`)?.value || '5Min';
        
        const entryBoxRegular = document.getElementById('livePreviewEntryBox_regular');
        const exitBoxRegular = document.getElementById('livePreviewExitBox_regular');
        const entryBoxOptions = document.getElementById('livePreviewEntryBox_options');
        const exitBoxOptions = document.getElementById('livePreviewExitBox_options');
        const loadingHtml = `&#8987; Fetching live market data for ${ticker} (${tf})...`;
        if (entryBoxRegular && !entryBoxRegular.classList.contains('hidden')) entryBoxRegular.innerHTML = loadingHtml;
        if (exitBoxRegular && !exitBoxRegular.classList.contains('hidden')) exitBoxRegular.innerHTML = loadingHtml;
        if (entryBoxOptions && !entryBoxOptions.classList.contains('hidden')) entryBoxOptions.innerHTML = loadingHtml;
        if (exitBoxOptions && !exitBoxOptions.classList.contains('hidden')) exitBoxOptions.innerHTML = loadingHtml;
        
        const res = await fetch(`${API_BASE}/api/chart/${ticker}?timeframe=${tf}&source=bots`, { headers });
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
        console.error("Failed to fetch preview data", e);
    }
};

function buildEntryPreviewHtml(mode, isOptions, symbol, simPrice, simAtr, priorDay, tf, suffix) {
    const buyMode = document.getElementById(isOptions ? 'strategyBuyMode_options' : 'strategyBuyMode_regular')?.value || 'indicator';
    const modeLabel = buyMode.replace(/_/g, ' ').replace(/\b\w/g, ch => ch.toUpperCase());
    const basePrefix = `🔮 Live Preview: ${symbol} (Entry: ${modeLabel}) | Price: $${simPrice.toFixed(2)}`;
    const entryForecast = window.currentAtrPreviewData?.entry_forecast || '';
    const swingEntryForecast = window.currentAtrPreviewData?.swing_entry_forecast || '';

    if (buyMode === 'custom_price') {
        const targetEl = document.getElementById(`strategyCustomBuyPrice_${isOptions ? 'options' : 'regular'}`);
        const target = parseFloat(targetEl?.value || 0);
        return `${basePrefix}<br/>Target Entry: $${target.toFixed(2)}`;
    }

    if (['atr', 'hybrid_atr'].includes(buyMode)) {
        const multEl = document.getElementById(`strategyEntryAtrMult_${isOptions ? 'options' : 'regular'}`);
        const mult = parseFloat(multEl?.value || 2);
        const style = document.getElementById(`strategyEntryAtrStyle_${isOptions ? 'options' : 'regular'}`)?.value || 'pullback_reclaim';
        const longLevel = style === 'breakout_momentum' ? simPrice + (simAtr * mult) : simPrice - (simAtr * mult);
        const styleLabel = style === 'breakout_momentum' ? 'Breakout / Momentum' : 'Pullback / Reclaim';
        const longLabel = style === 'breakout_momentum' ? 'Buy Above' : 'Buy Below';
        return `${basePrefix}<br/>${longLabel}: $${longLevel.toFixed(2)}<br/>${styleLabel} | ATR x ${mult.toFixed(1)}${buyMode.startsWith('hybrid_') ? ' | Hybrid fallback: indicator signals' : ''}`;
    }

    if (['percentage', 'hybrid_pct'].includes(buyMode)) {
        const pctEl = document.getElementById(`strategyEntryPctMove_${isOptions ? 'options' : 'regular'}`);
        const pct = parseFloat(pctEl?.value || 5);
        const style = document.getElementById(`strategyEntryPctStyle_${isOptions ? 'options' : 'regular'}`)?.value || 'dip_reclaim';
        const longLevel = style === 'breakout_momentum' ? simPrice * (1 + pct / 100) : simPrice * (1 - pct / 100);
        const styleLabel = style === 'breakout_momentum' ? 'Breakout / Momentum' : 'Dip / Reclaim';
        const longLabel = style === 'breakout_momentum' ? 'Buy Above' : 'Buy Below';
        return `${basePrefix}<br/>${longLabel}: $${longLevel.toFixed(2)}<br/>${styleLabel} | ${pct.toFixed(1)}% move${buyMode.startsWith('hybrid_') ? ' | Hybrid fallback: indicator signals' : ''}`;
    }

    if (['pivot_points', 'hybrid_pivot'].includes(buyMode)) {
        if (!priorDay) {
            return `${basePrefix}<br/>Awaiting prior day data...`;
        }
        const h = priorDay.high, l = priorDay.low, c = priorDay.close;
        const p = (h + l + c) / 3;
        const r1 = (p * 2) - l;
        const s1 = (p * 2) - h;
        return `${basePrefix}<br/>Pivot: $${p.toFixed(2)}<br/>Support (S1): $${s1.toFixed(2)} &nbsp;|&nbsp; Resistance (R1): $${r1.toFixed(2)}${buyMode.startsWith('hybrid_') ? '<br/>Hybrid fallback: indicator signals' : ''}`;
    }

    if (['high_low', 'hybrid_high_low'].includes(buyMode)) {
        if (!priorDay) {
            return `${basePrefix}<br/>Awaiting prior day data...`;
        }
        return `${basePrefix}<br/>Prior High: $${priorDay.high.toFixed(2)} &nbsp;|&nbsp; Prior Low: $${priorDay.low.toFixed(2)}${buyMode.startsWith('hybrid_') ? '<br/>Hybrid fallback: indicator signals' : ''}`;
    }

    if (['swing_points', 'hybrid_swing'].includes(buyMode)) {
        return `${basePrefix}<br/>${buildSwingEntryPreviewHtml(mode, isOptions, symbol, simPrice)}`;
    }

    if (buyMode === 'indicator') {
        return '';
    }

    const buyThreshold = document.getElementById(`strategyBuyThreshold_${mode}`)?.value || '4';
    const sellThreshold = document.getElementById(`strategySellThreshold_${mode}`)?.value || '4';
    return `${basePrefix}<br/>Buy Threshold: ${buyThreshold} &nbsp;|&nbsp; Sell Threshold: ${sellThreshold}<br/>${entryForecast || 'Awaiting indicator confluence...'}`;
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
                html = `&#128302; Live Preview: ${symbol} (Prior Day Pivots) | Price: $${simPrice.toFixed(2)}<br/>
                        Floor Pivot: $${p.toFixed(2)}<br/>
                        Stop Loss (S1): $${s1.toFixed(2)} &nbsp;|&nbsp; Take Profit (R1): $${r1.toFixed(2)}`;
            } else if (showHL) {
                html = `&#128302; Live Preview: ${symbol} (Prior Day High/Low) | Price: $${simPrice.toFixed(2)}<br/>
                        Stop Loss (Prior Low): $${priorDay.low.toFixed(2)} &nbsp;|&nbsp; Take Profit (Prior High): $${priorDay.high.toFixed(2)}`;
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

                const slPrice = simPrice - slDist;
        const tpPrice = simPrice + tpDist;
        
        const slPctCalc = simPrice > 0 ? ((slDist / simPrice) * 100).toFixed(2) : '0.00';
        const tpPctCalc = simPrice > 0 ? ((tpDist / simPrice) * 100).toFixed(2) : '0.00';
        
        let label = isLive 
            ? `&#128302; Live Preview: ${symbol} (${tf} Timeframe) &nbsp;|&nbsp; Price: $${simPrice.toFixed(2)}${showATR ? ` &nbsp;|&nbsp; ATR: $${simAtr.toFixed(2)}` : ''}` 
            : `&#128302; Example Preview (Assuming $100 Entry${showATR ? ' & $2 ATR' : ''})`;
            
        html = `${label} <br/> Stop Loss: $${slPrice.toFixed(2)} (-${slPctCalc}%) &nbsp;|&nbsp; Take Profit: $${tpPrice.toFixed(2)} (+${tpPctCalc}%)`;
        
        if (hasTrail) {
            if (showATR) {
                html += `<br/>Trail Distance: $${trailDist.toFixed(2)} (ATR * ${document.getElementById('strategyAtrTrailMult'+suffix)?.value || '0'})`;
            } else {
                html += `<br/>Trail Distance: $${trailDist.toFixed(2)} (${document.getElementById(isOptions ? 'strategyOptionsTrailPct' : 'strategyRegularTrailPct')?.value || '0'}%)`;
            }
        }
    }

    exitBox.innerHTML = html;
    exitBox.classList.remove('hidden');
};

const oldOpenStrategyModalUnified = window.openStrategyModal;
if(oldOpenStrategyModalUnified && !window._livePreviewPatchedUnified) {
    window._livePreviewPatchedUnified = true;
    window.openStrategyModal = function(symbol, existingBot) {
        window.currentAtrPreviewData = { price: 100, atr: 2.0, symbol: symbol, isLive: false };
        oldOpenStrategyModalUnified(symbol, existingBot);
        if (symbol) window.fetchLiveAtrForPreview(symbol);
        
        if (window.updateLivePreview_bots) {
            window.updateLivePreview_bots('regular', 'both');
            window.updateLivePreview_bots('options', 'both');
        }
    };
}


window.toggleRiskControls = function(enabled, mode) {
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
async function saveGlobalRiskSettings() {
    const maxDrawdownEl = document.getElementById('globalMaxDrawdown');
    const hardStopEl = document.getElementById('globalHardStop');
    
    if (!maxDrawdownEl || !hardStopEl) return;
    
    const payload = {
        MAX_DAILY_DRAWDOWN: maxDrawdownEl.value ? parseInt(maxDrawdownEl.value, 10) : null,
        GLOBAL_HARD_STOP_PCT: hardStopEl.value ? parseInt(hardStopEl.value, 10) : null
    };
    
    try {
        const headers = await getAuthHeaders();
        const response = await fetch(`${API_BASE}/api/settings/risk?context=dashboard`, {
            method: 'POST',
            headers: headers,
            body: JSON.stringify(payload)
        });
        if (!response.ok) throw new Error('Failed to save risk settings');
        console.log('[bots] Global Risk Settings Auto-saved');
    } catch (e) {
        console.error('[bots] Error saving global risk settings:', e);
    }
}

document.addEventListener('DOMContentLoaded', () => {
    const dd = document.getElementById('globalMaxDrawdown');
    const hs = document.getElementById('globalHardStop');
    if (dd) dd.addEventListener('change', saveGlobalRiskSettings);
    if (hs) hs.addEventListener('change', saveGlobalRiskSettings);
});
