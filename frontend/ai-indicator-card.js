/**
 * ai-indicator-card.js
 * Shared, unified UI component and model selector for the BotBulls AI Analysis card (#aiIndicatorCard).
 * Used by dashboard.html, bots.html, and backtest.html to ensure 100% streamlined presentation across all pages.
 */

const AI_MODEL_MIGRATIONS_SHARED = {
    'mixtral-8x7b-32768': 'llama-3.3-70b-versatile',
    'mistral-saba-24b': 'llama-3.3-70b-versatile',
    'openai/gpt-oss-120b': 'llama-3.3-70b-versatile',
    'openai/gpt-oss-20b': 'llama-3.1-8b-instant'
};

function normalizeAIModel(model) {
    const value = String(model || '').trim() || 'llama-3.3-70b-versatile';
    return AI_MODEL_MIGRATIONS_SHARED[value] || value;
}
window.normalizeAIModel = normalizeAIModel;
window.normalizeSelectedAIModel = normalizeAIModel;
window.normalizeBotAIModel = normalizeAIModel;

function getAIIndicatorPageContext() {
    const page = String(window.location?.pathname || '').split('/').pop().toLowerCase();
    if (page === 'bots.html') return 'bots';
    if (page === 'backtest.html') return 'backtest';
    return 'dashboard';
}
window.getAIIndicatorPageContext = getAIIndicatorPageContext;

function getAIDetailedAnalysisStorageKey(context = getAIIndicatorPageContext()) {
    return `ai_detailed_analysis_expanded:${context}`;
}

var aiDetailedAnalysisExpanded = (() => {
    try {
        return localStorage.getItem(getAIDetailedAnalysisStorageKey()) === 'true';
    } catch (error) {
        return false;
    }
})();

function setAIDetailedAnalysisExpanded(expanded) {
    aiDetailedAnalysisExpanded = Boolean(expanded);
    window.aiDetailedAnalysisExpanded = aiDetailedAnalysisExpanded;
    try {
        localStorage.setItem(getAIDetailedAnalysisStorageKey(), String(aiDetailedAnalysisExpanded));
    } catch (error) { }
}
window.setAIDetailedAnalysisExpanded = setAIDetailedAnalysisExpanded;

function rememberAIDetailedAnalysisState(container = document.getElementById('aiIndicatorCard')) {
    const details = container?.querySelector('[data-ai-detailed-analysis]');
    if (details) setAIDetailedAnalysisExpanded(details.open);
    return aiDetailedAnalysisExpanded;
}
window.rememberAIDetailedAnalysisState = rememberAIDetailedAnalysisState;

const AI_DECISION_TIMEFRAMES = ['1Min', '5Min', '15Min', '30Min', '1Hour', '4Hour', '1Day'];
const AI_INDICATOR_CACHE_VERSION = 'ai-quant-v6-news-relevance';
const AI_SETTINGS_DEFAULTS = {
    AI_INDICATOR_MODEL: 'llama-3.3-70b-versatile',
    AI_DECISION_TIMEFRAME: '1Hour',
    AI_MIN_SETUP_SCORE: 70,
    AI_MIN_REWARD_RISK: 1.5,
    AI_MAX_ENTRY_RSI: 72,
    AI_MIN_VOLUME_RATIO: 0.8,
    AI_REQUIRE_VOLUME_CONFIRMATION: false,
    AI_NEWS_ALIGNMENT_MODE: 'veto_conflicting_entries',
    AI_INDICATOR_CACHE_MINUTES: 30
};
let _aiSettingsRequest = null;
let _aiSettingsLoadedAt = 0;

function getAISettingsStorageKey(context = getAIIndicatorPageContext()) {
    return `quant_ai_settings:${context}`;
}

function getLocalAIIndicatorSettings(context = getAIIndicatorPageContext()) {
    try {
        const saved = JSON.parse(localStorage.getItem(getAISettingsStorageKey(context)) || '{}');
        return {
            ...AI_SETTINGS_DEFAULTS,
            ...saved,
            AI_INDICATOR_MODEL: normalizeAIModel(
                saved.AI_INDICATOR_MODEL || localStorage.getItem(getAIModelStorageKey(context))
            )
        };
    } catch (e) {
        return {
            ...AI_SETTINGS_DEFAULTS,
            AI_INDICATOR_MODEL: normalizeAIModel(localStorage.getItem(getAIModelStorageKey(context)))
        };
    }
}
window.getLocalAIIndicatorSettings = getLocalAIIndicatorSettings;

function setLocalAIIndicatorSettings(settings, context = getAIIndicatorPageContext()) {
    const clean = {
        ...AI_SETTINGS_DEFAULTS,
        ...(settings || {}),
        AI_INDICATOR_MODEL: normalizeAIModel(
            settings?.AI_INDICATOR_MODEL || localStorage.getItem(getAIModelStorageKey(context))
        )
    };
    localStorage.setItem(getAISettingsStorageKey(context), JSON.stringify(clean));
    setSelectedAIModelForContext(clean.AI_INDICATOR_MODEL, context);
    return clean;
}

async function loadAIIndicatorSettings(force = false) {
    if (!force && _aiSettingsLoadedAt && Date.now() - _aiSettingsLoadedAt < 60_000) {
        return getLocalAIIndicatorSettings();
    }
    if (_aiSettingsRequest && !force) return _aiSettingsRequest;
    const context = getAIIndicatorPageContext();
    _aiSettingsRequest = (async () => {
        try {
            let headers = {};
            if (typeof getAuthHeaders === 'function') headers = await getAuthHeaders();
            const apiBase = typeof API_BASE !== 'undefined' ? API_BASE : (window.BACKTEST_API_BASE || '');
            const response = await fetch(`${apiBase}/api/settings/ai?context=${encodeURIComponent(context)}`, { headers });
            if (!response.ok) throw new Error(`HTTP ${response.status}`);
            const settings = setLocalAIIndicatorSettings(await response.json(), context);
            _aiSettingsLoadedAt = Date.now();
            return settings;
        } catch (error) {
            console.warn('[ai-indicator] Using locally cached Quant AI settings:', error);
            return getLocalAIIndicatorSettings(context);
        } finally {
            _aiSettingsRequest = null;
        }
    })();
    return _aiSettingsRequest;
}
window.loadAIIndicatorSettings = loadAIIndicatorSettings;

function getAIDecisionTimeframe() {
    return normalizeAIIndicatorTimeframe(getLocalAIIndicatorSettings().AI_DECISION_TIMEFRAME);
}
window.getAIDecisionTimeframe = getAIDecisionTimeframe;

function getAIModelStorageKey(context = getAIIndicatorPageContext()) {
    return `selected_ai_model:${context}`;
}
window.getAIModelStorageKey = getAIModelStorageKey;

function getSelectedAIModelForContext(context = getAIIndicatorPageContext()) {
    return normalizeAIModel(localStorage.getItem(getAIModelStorageKey(context)));
}
window.getSelectedAIModelForContext = getSelectedAIModelForContext;

function setSelectedAIModelForContext(model, context = getAIIndicatorPageContext()) {
    const normalized = normalizeAIModel(model);
    localStorage.setItem(getAIModelStorageKey(context), normalized);
    return normalized;
}
window.setSelectedAIModelForContext = setSelectedAIModelForContext;

function _getAIModelOptionsHtml(selectedModel) {
    selectedModel = normalizeAIModel(selectedModel);
    let optionsHtml = '';
    optionsHtml += '<optgroup label="Groq AI (Cloud)">';
    const groqModels = [
        { value: 'llama-3.3-70b-versatile', label: 'Llama 3.3 70B (Recommended)' },
        { value: 'llama-3.1-8b-instant', label: 'Llama 3.1 8B (Fast)' }
    ];
    groqModels.forEach(m => {
        const sel = selectedModel === m.value ? 'selected' : '';
        optionsHtml += `<option value="${m.value}" ${sel}>${m.label}</option>`;
    });
    optionsHtml += '</optgroup>';

    optionsHtml += '<optgroup label="Ollama (Local)">';
    const localModels = Array.isArray(window.ollamaModels) ? [...window.ollamaModels] : [];
    const selectedLocalName = selectedModel.startsWith('ollama/')
        ? selectedModel.slice('ollama/'.length)
        : (selectedModel.startsWith('ollama:') ? selectedModel.slice('ollama:'.length) : '');
    if (selectedLocalName && !localModels.includes(selectedLocalName)) localModels.unshift(selectedLocalName);
    if (localModels.length > 0) {
        localModels.forEach(m => {
            const val = `ollama/${m}`;
            const selectedValue = selectedModel === `ollama:${m}` ? selectedModel : val;
            const sel = selectedModel === val || selectedModel === `ollama:${m}` ? 'selected' : '';
            const unavailable = m === selectedLocalName && !(window.ollamaModels || []).includes(m) ? ' (saved)' : '';
            optionsHtml += `<option value="${escapeAIHtml(selectedValue)}" ${sel}>${escapeAIHtml(m)}${unavailable}</option>`;
        });
    } else {
        optionsHtml += `<option disabled>No local models found</option>`;
    }
    optionsHtml += '</optgroup>';
    return optionsHtml;
}
window._getAIModelOptionsHtml = _getAIModelOptionsHtml;

function _showAIIndicatorLoading(ticker) {
    const el = document.getElementById('aiIndicatorCard');
    if (!el) return;
    rememberAIDetailedAnalysisState(el);
    const selectedModel = getSelectedAIModelForContext();
    const isOllamaActive = selectedModel.startsWith('ollama/') || selectedModel.startsWith('ollama:');
    const badgeHtml = isOllamaActive
        ? `<span class="px-1.5 py-0.5 rounded text-[0.55rem] font-black bg-gradient-to-r from-indigo-500 to-blue-600 text-white border border-indigo-600/50 shadow-sm">OLLAMA</span>`
        : `<span class="px-1.5 py-0.5 rounded text-[0.55rem] font-black bg-gradient-to-r from-amber-200 to-amber-300 text-amber-800 border border-amber-400/50 shadow-sm">GROQ AI</span>`;
    el.className = 'ai-indicator-card ai-indicator-loading border-amber-200/60 bg-gradient-to-br from-amber-50/50 to-orange-50/30 p-4 rounded-xl relative overflow-hidden transition-all duration-300';
    el.innerHTML = `
        <div class="ai-orb bg-gradient-to-r from-amber-400 to-orange-500 opacity-20"></div>
        <div class="flex flex-col gap-1.5 mb-3 relative z-10">
            <div class="flex min-w-0 flex-1 items-center gap-1.5 sm:gap-2">
                <img src="assets/bot_bulls_logo.png" alt="BotBulls" class="h-4 w-4 sm:h-5 sm:w-5 rounded-md border border-amber-200/80 bg-white/80 object-cover shadow-sm shrink-0">
                <span class="flex-1 min-w-0 overflow-hidden text-ellipsis font-black text-xs text-amber-900 tracking-wide uppercase whitespace-nowrap">BotBulls AI Analysis - ${ticker || '---'}</span>
                <div class="ai-analysis-actions flex items-center gap-1.5 sm:gap-2 relative z-30 shrink-0">
                    <button onclick="openAISettingsModal()" title="Quant AI decision settings" class="w-5 h-5 sm:w-6 sm:h-6 rounded-full flex items-center justify-center text-[11px] font-bold text-amber-700 hover:text-amber-900 bg-amber-100 hover:bg-amber-200 transition-colors border border-amber-300/50 shadow-sm shrink-0">⚙</button>
                    <button onclick="openAIInfoModal()" title="How AI analysis works" class="w-5 h-5 sm:w-6 sm:h-6 rounded-full flex items-center justify-center text-[10px] font-bold text-amber-700 hover:text-amber-900 bg-amber-100 hover:bg-amber-200 transition-colors border border-amber-300/50 shadow-sm shrink-0">ℹ</button>
                    <button disabled class="w-5 h-5 sm:w-6 sm:h-6 rounded-full flex items-center justify-center text-xs text-amber-300 bg-amber-50 border border-amber-200 animate-spin shrink-0">↻</button>
                </div>
            </div>
            <div class="flex flex-nowrap items-center justify-between gap-2 overflow-x-auto pb-1 ai-hdr-scroll" style="scrollbar-width: none; -ms-overflow-style: none;">
                <style>.ai-hdr-scroll::-webkit-scrollbar { display: none; }</style>
                <div class="flex flex-nowrap items-center gap-1.5 sm:gap-2 shrink-0">
                    <div class="shrink-0 flex">${badgeHtml}</div>
                </div>
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
window._showAIIndicatorLoading = _showAIIndicatorLoading;

function _showAIIndicatorError(err, ticker) {
    const el = document.getElementById('aiIndicatorCard');
    if (!el) return;
    rememberAIDetailedAnalysisState(el);
    el.className = 'ai-indicator-card ai-indicator-error relative overflow-hidden p-4 rounded-xl border transition-all duration-300';
    el.innerHTML = `
        <div class="flex flex-col gap-1.5 mb-3 relative z-10">
            <div class="flex min-w-0 flex-1 items-center gap-1.5 sm:gap-2">
                <img src="assets/bot_bulls_logo.png" alt="BotBulls" class="h-4 w-4 sm:h-5 sm:w-5 rounded-md border border-red-200/80 bg-white/80 object-cover shadow-sm shrink-0">
                <span class="flex-1 min-w-0 overflow-hidden text-ellipsis font-black text-xs text-red-800 tracking-wide uppercase whitespace-nowrap">BotBulls AI Analysis - ${ticker || '---'}</span>
                <div class="ai-analysis-actions flex items-center gap-1.5 sm:gap-2 relative z-30 shrink-0">
                    <button onclick="openAISettingsModal()" title="Quant AI decision settings" class="w-5 h-5 sm:w-6 sm:h-6 rounded-full flex items-center justify-center text-[11px] font-bold text-red-600 bg-red-100/50 hover:bg-red-100 border border-red-300/30 shrink-0">⚙</button>
                    <button onclick="openAIInfoModal()" title="How AI analysis works" class="w-5 h-5 sm:w-6 sm:h-6 rounded-full flex items-center justify-center text-[10px] font-bold text-red-500 hover:text-red-700 bg-red-100/50 hover:bg-red-100 transition-colors border border-red-300/30 shrink-0">ℹ</button>
                    <button id="aiRefreshBtn" onclick="refreshAIIndicator()" title="Force refresh AI analysis" class="w-5 h-5 sm:w-6 sm:h-6 rounded-full flex items-center justify-center text-xs text-red-500 hover:text-red-700 bg-red-100/50 hover:bg-red-100 transition-colors border border-red-300/30 shrink-0">↻</button>
                </div>
            </div>
            <div class="flex flex-nowrap items-center justify-between gap-2 overflow-x-auto pb-1 ai-hdr-scroll" style="scrollbar-width: none; -ms-overflow-style: none;">
                <style>.ai-hdr-scroll::-webkit-scrollbar { display: none; }</style>
                <div class="flex flex-nowrap items-center gap-1.5 sm:gap-2 shrink-0">
                    <span class="px-1.5 py-0.5 rounded text-[0.55rem] font-bold bg-red-100 text-red-600 border border-red-300/50 shrink-0 whitespace-nowrap">Error</span>
                </div>
            </div>
        </div>
        <p class="text-[0.6rem] text-red-500 relative z-10">${err || 'Could not load AI analysis'}</p>
    `;
}
window._showAIIndicatorError = _showAIIndicatorError;

function escapeAIHtml(value) {
    return String(value ?? '').replace(/[&<>'"]/g, char => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', "'": '&#39;', '"': '&quot;' }[char]));
}

function formatAIPrice(value) {
    const number = Number(value);
    if (!Number.isFinite(number) || number <= 0) return '—';
    return `$${number.toLocaleString(undefined, { minimumFractionDigits: number < 10 ? 4 : 2, maximumFractionDigits: number < 10 ? 6 : 2 })}`;
}

function safeAIArticleUrl(value) {
    try {
        const url = new URL(String(value || ''), window.location.origin);
        return ['http:', 'https:'].includes(url.protocol) ? url.href : '';
    } catch (error) {
        return '';
    }
}

function formatAINewsAge(article) {
    const hours = Number(article?.age_hours);
    if (Number.isFinite(hours)) {
        if (hours < 1) return 'Less than 1 hour ago';
        if (hours < 24) return `${Math.round(hours)}h ago`;
        return `${Math.round(hours / 24)}d ago`;
    }
    const published = Date.parse(article?.published_at || '');
    return Number.isFinite(published) ? new Date(published).toLocaleDateString() : 'Publication time unavailable';
}

function renderAIIndicatorCard(data, fromCache = false) {
    const el = document.getElementById('aiIndicatorCard');
    if (!el || !data) return;
    rememberAIDetailedAnalysisState(el);
    const signal = String(data.signal || 'HOLD').toUpperCase();
    const setupScore = Math.round(Number(data.setup_score ?? ((data.confidence || 0) * 100)));
    const minimumScore = Math.round(Number(data.minimum_setup_score ?? 70));
    const dataQuality = Math.round(Number(data.data_quality_score ?? 0));
    const technicalBias = String(data.technical_bias || 'NEUTRAL').toUpperCase();
    const regime = String(data.volatility_regime || data.vix_regime || 'UNKNOWN').toUpperCase();
    const sentScore = normalizeCachedAISentimentScore(data.sentiment_score) ?? 0;
    const sentPct = Math.round(normalizeAISentimentPct(data.sentiment_pct, sentScore));
    const newsDirection = getAISentimentDirectionFromPct(sentPct, {}, data.sentiment_direction);
    const sentConfidence = Math.round(Number(data.sentiment_confidence || 0) * 100);
    const rr = Number(data.reward_risk || 0);
    const minRr = Number(data.minimum_reward_risk || 1.5);
    const cached = fromCache || data.cached || false;
    const selectedModel = getSelectedAIModelForContext();
    const usedModelRaw = String(data.model || selectedModel);
    const usedModel = escapeAIHtml(usedModelRaw);
    const activeTf = escapeAIHtml(data.decision_timeframe || data.candle_timeframe || getAIDecisionTimeframe());
    const ticker = escapeAIHtml(data.ticker || getAIActiveTicker('---'));
    const generated = Number(data.generated_at) ? new Date(Number(data.generated_at) * 1000).toLocaleString() : '';
    const cacheAge = Number(data.cache_age_seconds);
    const freshness = cached ? `Cached${Number.isFinite(cacheAge) ? ` ${Math.max(0, Math.round(cacheAge / 60))}m` : ''}` : (generated ? `Updated ${generated}` : 'Fresh analysis');
    const signalStyle = signal === 'BUY'
        ? { badge: 'bg-emerald-100 text-emerald-700 border-emerald-300', icon: '▲', bar: 'bg-emerald-500' }
        : signal === 'SELL'
            ? { badge: 'bg-rose-100 text-rose-700 border-rose-300', icon: '▼', bar: 'bg-rose-500' }
            : { badge: 'bg-slate-100 text-slate-700 border-slate-300', icon: '●', bar: 'bg-slate-500' };
    const decisionLabel = signal === 'HOLD'
        ? (String(data.position_state || '').toUpperCase() === 'LONG' ? 'HOLD POSITION' : 'WAIT — NO NEW ENTRY')
        : signal;
    const bullishEvidence = Array.isArray(data.bullish_evidence) ? data.bullish_evidence : [];
    const bearishEvidence = Array.isArray(data.bearish_evidence) ? data.bearish_evidence : [];
    const cautions = Array.isArray(data.cautions) ? data.cautions : [];
    const evidenceList = (items, icon, empty) => items.length
        ? items.map(item => `<li class="flex gap-1.5"><span>${icon}</span><span>${escapeAIHtml(item)}</span></li>`).join('')
        : `<li class="text-slate-400">${empty}</li>`;
    const technicalDetails = data.technical_details && typeof data.technical_details === 'object'
        ? data.technical_details
        : {};
    const technicalSections = [
        ['Trend structure', technicalDetails.trend],
        ['Momentum', technicalDetails.momentum],
        ['Price structure', technicalDetails.structure],
        ['Volume / participation', technicalDetails.participation],
        ['Volatility', technicalDetails.volatility],
        ['Market / sector context', technicalDetails.market_context],
    ].filter(([, value]) => value);
    const technicalDetailsHtml = technicalSections.length
        ? technicalSections.map(([label, value]) => `
            <div class="rounded-lg border border-slate-200 bg-white p-2.5">
                <div class="text-[9px] font-black uppercase tracking-wide text-slate-500 mb-1">${escapeAIHtml(label)}</div>
                <p class="text-[10px] font-semibold text-slate-700 leading-relaxed">${escapeAIHtml(value)}</p>
            </div>`).join('')
        : `<div class="sm:col-span-2 rounded-lg border border-slate-200 bg-white p-2.5 text-[10px] text-slate-600">${escapeAIHtml(data.summary || 'Detailed technical inputs will appear after the next recalculation.')}</div>`;
    const gateChecks = Array.isArray(data.gate_checks) ? data.gate_checks : [];
    const gateChecksHtml = gateChecks.length
        ? gateChecks.map(gate => {
            const status = String(gate.status || 'INFO').toUpperCase();
            const style = status === 'PASS'
                ? 'border-emerald-200 bg-emerald-50 text-emerald-800'
                : status === 'BLOCK'
                    ? 'border-rose-200 bg-rose-50 text-rose-800'
                    : 'border-slate-200 bg-slate-50 text-slate-700';
            const icon = status === 'PASS' ? '✓' : status === 'BLOCK' ? '×' : 'i';
            return `<div class="rounded-lg border ${style} p-2">
                <div class="flex items-center justify-between gap-2">
                    <span class="text-[9px] font-black">${icon} ${escapeAIHtml(gate.label || gate.key || 'Gate')}</span>
                    <span class="text-[8px] font-black uppercase">${escapeAIHtml(status)}</span>
                </div>
                <div class="mt-1 text-[9px] font-semibold">Current ${escapeAIHtml(gate.current || '—')} · Required ${escapeAIHtml(gate.required || '—')}</div>
            </div>`;
        }).join('')
        : `<div class="text-[10px] text-slate-500">Gate-by-gate diagnostics will appear after the next recalculation.</div>`;
    const fallbackHeadlines = Array.isArray(data.news_headlines) ? data.news_headlines.filter(Boolean).slice(0, 5) : [];
    const newsArticles = Array.isArray(data.news_articles) && data.news_articles.length
        ? data.news_articles.filter(article => article && article.title).slice(0, 5)
        : fallbackHeadlines.map(title => ({ title, sentiment: 'NEUTRAL', impact: 'LOW', source: 'Source unavailable' }));
    const newsArticlesHtml = newsArticles.length
        ? `<div class="mt-2 space-y-2">${newsArticles.map(article => {
            const articleSentiment = String(article.sentiment || 'NEUTRAL').toUpperCase();
            const articleImpact = String(article.impact || 'LOW').toUpperCase();
            const articleRelevance = String(article.relevance || 'DIRECT').toUpperCase();
            const displaySentiment = articleRelevance === 'IRRELEVANT' ? 'IGNORED' : articleSentiment;
            const sentimentStyle = articleRelevance === 'IRRELEVANT'
                ? 'bg-slate-100 text-slate-500 border-slate-200'
                : articleSentiment === 'BULLISH'
                ? 'bg-emerald-100 text-emerald-700 border-emerald-200'
                : articleSentiment === 'BEARISH'
                    ? 'bg-rose-100 text-rose-700 border-rose-200'
                    : 'bg-slate-100 text-slate-600 border-slate-200';
            const relevanceStyle = articleRelevance === 'DIRECT'
                ? 'bg-purple-100 text-purple-700 border-purple-200'
                : articleRelevance === 'CONTEXT'
                    ? 'bg-blue-50 text-blue-700 border-blue-200'
                    : 'bg-slate-50 text-slate-500 border-slate-200';
            const articleUrl = safeAIArticleUrl(article.url);
            const titleHtml = articleUrl
                ? `<a href="${escapeAIHtml(articleUrl)}" target="_blank" rel="noopener noreferrer" class="font-black text-purple-900 hover:text-purple-600 hover:underline">${escapeAIHtml(article.title)}</a>`
                : `<span class="font-black text-purple-900">${escapeAIHtml(article.title)}</span>`;
            return `<article class="rounded-lg border border-purple-100 bg-white/85 p-2.5">
                <div class="flex flex-wrap items-center justify-between gap-2 mb-1">
                    <div class="text-[8px] font-bold text-slate-500">${escapeAIHtml(article.source || 'Unknown source')} · ${escapeAIHtml(formatAINewsAge(article))}</div>
                    <div class="flex items-center gap-1">
                        <span class="rounded border px-1.5 py-0.5 text-[8px] font-black ${relevanceStyle}">${escapeAIHtml(articleRelevance)}</span>
                        <span class="rounded border px-1.5 py-0.5 text-[8px] font-black ${sentimentStyle}">${escapeAIHtml(displaySentiment)}</span>
                        <span class="rounded border border-slate-200 bg-slate-50 px-1.5 py-0.5 text-[8px] font-black text-slate-500">${escapeAIHtml(articleImpact)} impact</span>
                    </div>
                </div>
                <div class="text-[10px] leading-relaxed">${titleHtml}</div>
                ${article.excerpt ? `<p class="mt-1 text-[9px] text-slate-600 leading-relaxed">${escapeAIHtml(article.excerpt)}</p>` : ''}
                ${article.reason ? `<p class="mt-1.5 text-[9px] text-purple-800 font-semibold"><span class="font-black">AI read:</span> ${escapeAIHtml(article.reason)}</p>` : ''}
            </article>`;
        }).join('')}</div>`
        : `<p class="mt-2 text-[9px] text-slate-500">No recent, directly attributable article evidence was returned. Treat the news score as limited evidence.</p>`;
    const newsCoverage = String(data.news_coverage_quality || (Number(data.headline_count || 0) >= 3 ? 'MODERATE' : 'LIMITED')).toUpperCase();
    const newsAnalysisModel = String(data.news_analysis_model || usedModelRaw || 'unknown');
    const isOllamaActive = selectedModel.startsWith('ollama/') || selectedModel.startsWith('ollama:');
    const providerBadge = usedModelRaw === 'deterministic-quant'
        ? '<span class="px-1.5 py-0.5 rounded text-[0.55rem] font-black bg-slate-700 text-white">QUANT CORE</span>'
        : isOllamaActive
        ? '<span class="px-1.5 py-0.5 rounded text-[0.55rem] font-black bg-indigo-600 text-white">OLLAMA</span>'
        : '<span class="px-1.5 py-0.5 rounded text-[0.55rem] font-black bg-amber-200 text-amber-800 border border-amber-300">GROQ AI</span>';

    el.className = 'ai-indicator-card relative overflow-hidden p-4 rounded-xl border border-amber-200/70 bg-gradient-to-br from-amber-50/60 to-white transition-all duration-300';
    el.innerHTML = `
        <div class="flex flex-col gap-1.5 mb-3 relative z-10">
            <div class="flex min-w-0 flex-1 items-center gap-1.5 sm:gap-2">
                <img src="assets/bot_bulls_logo.png" alt="BotBulls" class="h-4 w-4 sm:h-5 sm:w-5 rounded-md border border-amber-200/80 bg-white/80 object-cover shadow-sm shrink-0">
                <span class="flex-1 min-w-0 overflow-hidden text-ellipsis font-black text-xs text-amber-900 tracking-wide uppercase whitespace-nowrap" id="aiIndicatorHeader">BotBulls Quant AI — ${ticker}</span>
                <div class="ai-analysis-actions flex items-center gap-1.5 sm:gap-2 relative z-30 shrink-0">
                    <button onclick="openAISettingsModal()" title="Quant AI decision settings" class="w-5 h-5 sm:w-6 sm:h-6 flex items-center justify-center rounded-full text-[11px] sm:text-[12px] font-bold text-amber-700 bg-amber-100 hover:bg-amber-200 border border-amber-300 shrink-0">⚙</button>
                    <button onclick="openAIInfoModal()" title="How Quant AI works" class="w-5 h-5 sm:w-6 sm:h-6 flex items-center justify-center rounded-full text-[10px] font-bold text-amber-700 bg-amber-100 hover:bg-amber-200 border border-amber-300 shrink-0">ℹ</button>
                    <button id="aiRefreshBtn" onclick="refreshAIIndicator()" title="Refresh technical decision and ticker news" class="w-5 h-5 sm:w-6 sm:h-6 flex items-center justify-center rounded-full text-[11px] sm:text-xs text-amber-700 bg-amber-100 hover:bg-amber-200 border border-amber-300 shrink-0">↻</button>
                </div>
            </div>
            <div class="flex flex-nowrap items-center justify-between gap-2 overflow-x-auto pb-1 ai-hdr-scroll" style="scrollbar-width: none; -ms-overflow-style: none;">
                <style>.ai-hdr-scroll::-webkit-scrollbar { display: none; }</style>
                <div class="flex flex-nowrap items-center gap-1.5 sm:gap-2 shrink-0">
                    <div class="shrink-0 flex">${providerBadge}</div>
                    <span class="px-1.5 py-0.5 rounded text-[0.5rem] font-bold bg-white text-slate-600 border border-slate-200 shrink-0 whitespace-nowrap">${usedModel}</span>
                    <span class="px-1.5 py-0.5 rounded text-[0.5rem] font-bold bg-fuchsia-50 text-fuchsia-700 border border-fuchsia-200 shrink-0 whitespace-nowrap">Decision ${activeTf}</span>
                    <span class="text-[0.5rem] text-slate-500 shrink-0 whitespace-nowrap">${escapeAIHtml(freshness)}</span>
                </div>
            </div>
        </div>

        <div class="ai-summary-metrics grid grid-cols-2 md:grid-cols-5 gap-2 mb-3 relative z-10">
            <div class="col-span-2 md:col-span-1 rounded-xl border p-2.5 ${signalStyle.badge}">
                <div class="text-[0.52rem] uppercase font-black tracking-wider opacity-70">Decision</div>
                <div class="text-xs font-black mt-1">${escapeAIHtml(decisionLabel)} ${signalStyle.icon}</div>
            </div>
            <div class="rounded-xl border border-slate-200 bg-white/80 p-2.5">
                <div class="text-[0.52rem] uppercase font-black tracking-wider text-slate-500">Setup score</div>
                <div class="text-xs font-black text-slate-800 mt-1">${setupScore}/100 <span class="text-[9px] text-slate-400">min ${minimumScore}</span></div>
                <div class="mt-1.5 h-1 rounded bg-slate-200 overflow-hidden"><div class="h-1 ${signalStyle.bar}" style="width:${Math.max(0, Math.min(100, setupScore))}%"></div></div>
            </div>
            <div class="rounded-xl border border-slate-200 bg-white/80 p-2.5">
                <div class="text-[0.52rem] uppercase font-black tracking-wider text-slate-500">Technical bias</div>
                <div class="text-xs font-black text-slate-800 mt-1">${escapeAIHtml(technicalBias)}</div>
                <div class="text-[9px] text-slate-500 mt-1">Bull ${Number(data.bullish_score || 0).toFixed(0)} · Bear ${Number(data.bearish_score || 0).toFixed(0)}</div>
            </div>
            <div class="rounded-xl border border-slate-200 bg-white/80 p-2.5">
                <div class="text-[0.52rem] uppercase font-black tracking-wider text-slate-500">News · no timeframe</div>
                <div class="text-xs font-black text-slate-800 mt-1">${sentPct}/100 ${escapeAIHtml(newsDirection)}</div>
                <div class="text-[9px] text-slate-500 mt-1">Classifier confidence ${sentConfidence}%</div>
            </div>
            <div class="rounded-xl border border-slate-200 bg-white/80 p-2.5">
                <div class="text-[0.52rem] uppercase font-black tracking-wider text-slate-500">Risk context</div>
                <div class="text-xs font-black text-slate-800 mt-1">${escapeAIHtml(regime)} volatility</div>
                <div class="text-[9px] text-slate-500 mt-1">Data quality ${dataQuality}%</div>
            </div>
        </div>

        <div class="ai-trade-plan rounded-xl border border-indigo-200 bg-indigo-50/55 p-3 mb-3 relative z-10">
            <div class="flex items-center justify-between gap-2 mb-2">
                <span class="text-[0.58rem] font-black text-indigo-700 uppercase tracking-wider">Trade plan</span>
                <span class="text-[9px] font-bold ${rr >= minRr ? 'text-emerald-700' : 'text-amber-700'}">R:R ${rr.toFixed(2)} · minimum ${minRr.toFixed(2)}${Number(data.position_size_usd || 0) > 0 ? ` · planned ${formatAIPrice(data.position_size_usd)}` : ''}</span>
            </div>
            <div class="grid grid-cols-2 md:grid-cols-4 gap-2 mb-2">
                <div><div class="text-[9px] text-slate-500">Current / Entry</div><div class="text-xs font-black text-slate-800">${formatAIPrice(data.entry_price || data.price)}</div></div>
                <div><div class="text-[9px] text-slate-500">Invalidation / Stop</div><div class="text-xs font-black text-rose-700">${formatAIPrice(data.stop_loss)}</div></div>
                <div><div class="text-[9px] text-slate-500">Measured target</div><div class="text-xs font-black text-emerald-700">${formatAIPrice(data.sell_target)}</div></div>
                <div><div class="text-[9px] text-slate-500">Setup quality</div><div class="text-xs font-black text-slate-800">${escapeAIHtml(data.setup_quality || 'NO_TRADE')}</div></div>
            </div>
            <div class="rounded-lg bg-white/75 border border-indigo-100 p-2 text-[10px] text-indigo-900 font-semibold"><span class="font-black">Trigger:</span> ${escapeAIHtml(data.entry_trigger || data.wait_reason || 'Wait for configured confirmation gates.')}</div>
            <div class="mt-1.5 text-[9px] text-slate-600"><span class="font-black">Invalidation:</span> ${escapeAIHtml(data.invalidation_condition || 'Not available')}</div>
        </div>

        <div class="ai-evidence-grid grid grid-cols-1 md:grid-cols-3 gap-2 mb-3 relative z-10">
            <div class="rounded-xl border border-emerald-200 bg-emerald-50/55 p-2.5"><div class="text-[0.54rem] font-black text-emerald-700 uppercase mb-1.5">Supports long</div><ul class="space-y-1 text-[9px] text-emerald-900 font-semibold">${evidenceList(bullishEvidence, '✓', 'No bullish confirmation')}</ul></div>
            <div class="rounded-xl border border-rose-200 bg-rose-50/55 p-2.5"><div class="text-[0.54rem] font-black text-rose-700 uppercase mb-1.5">Supports sell / avoid</div><ul class="space-y-1 text-[9px] text-rose-900 font-semibold">${evidenceList(bearishEvidence, '▼', 'No bearish confirmation')}</ul></div>
            <div class="rounded-xl border border-amber-200 bg-amber-50/55 p-2.5"><div class="text-[0.54rem] font-black text-amber-700 uppercase mb-1.5">Blocks or cautions</div><ul class="space-y-1 text-[9px] text-amber-900 font-semibold">${evidenceList(cautions, '⚠', 'No additional caution')}</ul></div>
        </div>

        <div class="rounded-lg border ${data.news_alignment_applied ? 'border-amber-300 bg-amber-50' : 'border-slate-200 bg-slate-50'} p-2 mb-2 text-[9px] text-slate-700 font-semibold relative z-10"><span class="font-black">News alignment:</span> ${escapeAIHtml(data.news_alignment_reason || 'News is confirmation only and does not override the quantitative decision.')}</div>

        <details data-ai-detailed-analysis class="rounded-xl border border-violet-200 bg-white/70 p-3 mb-2 relative z-10"${aiDetailedAnalysisExpanded ? ' open' : ''} ontoggle="setAIDetailedAnalysisExpanded(this.open)">
            <summary class="cursor-pointer text-[10px] font-black text-violet-700">Detailed stock analysis — technical, gates, news and risk</summary>
            <div class="mt-3 space-y-3">
                <section class="rounded-xl border border-violet-200 bg-violet-50/40 p-3">
                    <div class="flex flex-wrap items-center justify-between gap-2 mb-2">
                        <div>
                            <div class="text-[10px] font-black text-violet-800 uppercase tracking-wide">Technical bias · ${escapeAIHtml(technicalBias)}</div>
                            <div class="text-[9px] text-violet-600 mt-0.5">Completed ${activeTf} candle · Bull ${Number(data.bullish_score || 0).toFixed(0)} vs Bear ${Number(data.bearish_score || 0).toFixed(0)}</div>
                        </div>
                        <span class="rounded-full bg-white border border-violet-200 px-2 py-1 text-[9px] font-black text-violet-700">${setupScore}/100 setup</span>
                    </div>
                    <p class="rounded-lg bg-white/80 border border-violet-100 p-2 text-[10px] text-slate-700 font-semibold leading-relaxed mb-2">${escapeAIHtml(data.summary || '')}</p>
                    <div class="grid grid-cols-1 sm:grid-cols-2 gap-2">${technicalDetailsHtml}</div>
                </section>

                <section class="rounded-xl border border-slate-200 bg-slate-50/70 p-3">
                    <div class="text-[10px] font-black text-slate-800 uppercase tracking-wide mb-2">Decision gates · why the signal passed or waited</div>
                    <div class="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-2">${gateChecksHtml}</div>
                    <div class="mt-2 rounded-lg border border-indigo-100 bg-indigo-50 p-2 text-[10px] text-indigo-900 font-semibold"><span class="font-black">Next confirmation:</span> ${escapeAIHtml(data.entry_trigger || data.wait_reason || 'Wait for configured confirmation gates.')}</div>
                </section>

                <section class="rounded-xl border border-purple-200 bg-purple-50/45 p-3">
                    <div class="flex flex-wrap items-center justify-between gap-2">
                        <div class="text-[10px] font-black text-purple-800 uppercase tracking-wide">News briefing · ${escapeAIHtml(newsDirection)}</div>
                        <div class="text-[9px] font-black text-purple-700">${sentPct}/100 tone · ${sentConfidence}% confidence · ${escapeAIHtml(newsCoverage)} coverage</div>
                    </div>
                    <div class="mt-1 text-[8px] font-bold text-purple-600">Article classifier: ${escapeAIHtml(newsAnalysisModel)}</div>
                    <p class="mt-2 text-[10px] text-slate-700 font-semibold leading-relaxed">${escapeAIHtml(data.sentiment_summary || 'No detailed ticker-news summary is available.')}</p>
                    <div class="mt-2 rounded-lg border border-purple-100 bg-white/80 p-2 text-[10px] text-purple-900"><span class="font-black">Primary news factor:</span> ${escapeAIHtml(data.sentiment_key_factor || 'Mixed or insufficient evidence')}</div>
                    <div class="mt-2 text-[9px] font-black uppercase tracking-wide text-slate-500">Article-by-article sentiment (${Number(data.headline_count || 0)} analyzed)</div>
                    ${newsArticlesHtml}
                    <p class="mt-2 text-[9px] text-slate-500">Analysis uses each article’s title, publisher, publication time, and available public preview—not unavailable or paywalled body text. News has no chart timeframe; its score is not the probability that price will rise and cannot create a trade by itself.</p>
                </section>

                <section class="grid grid-cols-1 sm:grid-cols-2 gap-2">
                    <div class="rounded-xl border border-rose-200 bg-rose-50/60 p-3 text-[10px] text-rose-900 leading-relaxed"><div class="font-black uppercase tracking-wide mb-1">Risk and invalidation</div><p>${escapeAIHtml(data.risk_factors || data.invalidation_condition || 'No specific risk condition available.')}</p><p class="mt-1.5 font-semibold">${escapeAIHtml(data.invalidation_condition || '')}</p></div>
                    <div class="rounded-xl border border-emerald-200 bg-emerald-50/60 p-3 text-[10px] text-emerald-900 leading-relaxed"><div class="font-black uppercase tracking-wide mb-1">Trade management</div><p>${escapeAIHtml(data.exit_plan || 'Wait for a valid entry, stop and target structure.')}</p></div>
                </section>
            </div>
        </details>
        <div class="flex items-center justify-between pt-2 border-t border-slate-200 text-[8px] text-slate-500 relative z-10"><span>${Number(data.headline_count || 0)} ticker news articles · news cached independently</span><span>Setup score is not a profit probability · advisory only</span></div>`;
}
window.renderAIIndicatorCard = renderAIIndicatorCard;

window.openAIInfoModal = function () {
    const modal = document.getElementById('aiInfoModal');
    if (modal) modal.classList.remove('hidden');
};

window.closeAIInfoModal = function () {
    const modal = document.getElementById('aiInfoModal');
    if (modal) modal.classList.add('hidden');
};

function ensureAISettingsModal() {
    let modal = document.getElementById('aiSettingsModal');
    if (modal) return modal;
    modal = document.createElement('div');
    modal.id = 'aiSettingsModal';
    modal.className = 'fixed inset-0 bg-slate-900/60 backdrop-blur-sm z-[260] hidden flex items-center justify-center p-4';
    modal.innerHTML = `
        <div class="w-full max-w-2xl max-h-[90vh] overflow-y-auto bg-white rounded-2xl shadow-2xl border border-amber-200">
            <div class="sticky top-0 bg-white/95 backdrop-blur px-5 py-4 border-b border-slate-200 flex items-center justify-between z-10">
                <div>
                    <h3 class="text-sm font-black text-slate-900">Quant AI Decision Settings</h3>
                    <p id="aiSettingsContextLabel" class="text-[10px] text-slate-500 mt-0.5"></p>
                </div>
                <button onclick="closeAISettingsModal()" class="w-8 h-8 rounded-full bg-slate-100 text-slate-600 hover:bg-slate-200 font-black">×</button>
            </div>
            <form id="aiSettingsForm" class="p-5 space-y-5" onsubmit="saveAIIndicatorSettings(event)">
                <div class="rounded-xl bg-amber-50 border border-amber-200 p-3">
                    <label class="block text-xs font-black text-amber-900 mb-1">AI model</label>
                    <select id="aiSettingModel" class="w-full rounded-lg border border-amber-200 bg-white px-3 py-2 text-sm font-bold text-slate-800">
                        ${_getAIModelOptionsHtml(getSelectedAIModelForContext())}
                    </select>
                    <p class="text-[10px] text-amber-800 mt-1.5">Choose the cloud or local model used to explain the quantitative decision. The deterministic quant rules still own BUY, SELL, HOLD, entry, stop and target.</p>
                </div>
                <div class="rounded-xl bg-indigo-50 border border-indigo-200 p-3">
                    <label class="block text-xs font-black text-indigo-900 mb-1">Decision candle timeframe</label>
                    <select id="aiSettingTimeframe" class="w-full rounded-lg border border-indigo-200 bg-white px-3 py-2 text-sm font-bold text-slate-800">
                        ${AI_DECISION_TIMEFRAMES.map(tf => `<option value="${tf}">${tf}</option>`).join('')}
                    </select>
                    <p class="text-[10px] text-indigo-700 mt-1.5">Trend, momentum, structure and risk levels use this completed-candle interval. News remains ticker-based and has no timeframe.</p>
                </div>
                <div class="grid grid-cols-1 sm:grid-cols-2 gap-3">
                    <label class="rounded-xl border border-slate-200 p-3">
                        <span class="block text-xs font-black text-slate-800">Minimum setup score</span>
                        <input id="aiSettingScore" type="number" min="50" max="95" step="1" class="mt-2 w-full rounded-lg border border-slate-200 px-3 py-2 text-sm font-bold">
                        <span class="block text-[9px] text-slate-500 mt-1">Higher values create fewer, stricter signals.</span>
                    </label>
                    <label class="rounded-xl border border-slate-200 p-3">
                        <span class="block text-xs font-black text-slate-800">Minimum reward / risk</span>
                        <input id="aiSettingRewardRisk" type="number" min="1" max="5" step="0.1" class="mt-2 w-full rounded-lg border border-slate-200 px-3 py-2 text-sm font-bold">
                        <span class="block text-[9px] text-slate-500 mt-1">BUY/SELL is blocked when available R:R is lower.</span>
                    </label>
                    <label class="rounded-xl border border-slate-200 p-3">
                        <span class="block text-xs font-black text-slate-800">Maximum RSI for new BUY</span>
                        <input id="aiSettingMaxRsi" type="number" min="55" max="85" step="1" class="mt-2 w-full rounded-lg border border-slate-200 px-3 py-2 text-sm font-bold">
                        <span class="block text-[9px] text-slate-500 mt-1">Prevents chasing overextended momentum.</span>
                    </label>
                    <label class="rounded-xl border border-slate-200 p-3">
                        <span class="block text-xs font-black text-slate-800">Minimum volume ratio</span>
                        <input id="aiSettingVolume" type="number" min="0" max="5" step="0.05" class="mt-2 w-full rounded-lg border border-slate-200 px-3 py-2 text-sm font-bold">
                        <span class="block text-[9px] text-slate-500 mt-1">Compared with the 20-candle average.</span>
                    </label>
                    <label class="rounded-xl border border-slate-200 p-3 flex items-center justify-between gap-3">
                        <span><span class="block text-xs font-black text-slate-800">Require volume confirmation</span><span class="block text-[9px] text-slate-500 mt-1">If enabled, missing/light volume blocks a trade.</span></span>
                        <input id="aiSettingRequireVolume" type="checkbox" class="h-5 w-5 rounded border-slate-300 text-indigo-600">
                    </label>
                    <label class="rounded-xl border border-slate-200 p-3">
                        <span class="block text-xs font-black text-slate-800">News conflict handling</span>
                        <select id="aiSettingNewsMode" class="mt-2 w-full rounded-lg border border-slate-200 px-3 py-2 text-sm font-bold">
                            <option value="veto_conflicting_entries">Block conflicting new entries</option>
                            <option value="off">Information only</option>
                        </select>
                        <span class="block text-[9px] text-slate-500 mt-1">News has limited weight and never creates a trade.</span>
                    </label>
                    <label class="rounded-xl border border-slate-200 p-3 sm:col-span-2">
                        <span class="block text-xs font-black text-slate-800">Analysis cache (minutes)</span>
                        <input id="aiSettingCache" type="number" min="1" max="120" step="1" class="mt-2 w-full rounded-lg border border-slate-200 px-3 py-2 text-sm font-bold">
                        <span class="block text-[9px] text-slate-500 mt-1">A manual refresh bypasses this cache and refreshes both technical context and ticker news.</span>
                    </label>
                </div>
                <div id="aiSettingsMessage" class="hidden text-xs rounded-lg px-3 py-2"></div>
                <div class="flex items-center justify-between gap-3 pt-2">
                    <button type="button" onclick="resetAISettingsForm()" class="px-4 py-2 rounded-lg bg-slate-100 text-slate-700 text-xs font-black hover:bg-slate-200">Use recommended defaults</button>
                    <button id="aiSettingsSaveBtn" type="submit" class="px-5 py-2 rounded-lg bg-indigo-600 text-white text-xs font-black hover:bg-indigo-700 shadow">Save & recalculate</button>
                </div>
            </form>
        </div>`;
    modal.addEventListener('click', event => { if (event.target === modal) closeAISettingsModal(); });
    document.body.appendChild(modal);
    return modal;
}

function populateAISettingsForm(settings) {
    const modelSelect = document.getElementById('aiSettingModel');
    const selectedModel = normalizeAIModel(settings.AI_INDICATOR_MODEL || getSelectedAIModelForContext());
    modelSelect.innerHTML = _getAIModelOptionsHtml(selectedModel);
    modelSelect.value = selectedModel;
    document.getElementById('aiSettingTimeframe').value = normalizeAIIndicatorTimeframe(settings.AI_DECISION_TIMEFRAME);
    document.getElementById('aiSettingScore').value = settings.AI_MIN_SETUP_SCORE;
    document.getElementById('aiSettingRewardRisk').value = settings.AI_MIN_REWARD_RISK;
    document.getElementById('aiSettingMaxRsi').value = settings.AI_MAX_ENTRY_RSI;
    document.getElementById('aiSettingVolume').value = settings.AI_MIN_VOLUME_RATIO;
    document.getElementById('aiSettingRequireVolume').checked = settings.AI_REQUIRE_VOLUME_CONFIRMATION === true;
    document.getElementById('aiSettingNewsMode').value = settings.AI_NEWS_ALIGNMENT_MODE;
    document.getElementById('aiSettingCache').value = settings.AI_INDICATOR_CACHE_MINUTES;
}

window.openAISettingsModal = async function () {
    const modal = ensureAISettingsModal();
    modal.classList.remove('hidden');
    document.getElementById('aiSettingsContextLabel').textContent = `${getAIIndicatorPageContext().toUpperCase()} settings — isolated from the other pages`;
    populateAISettingsForm(await loadAIIndicatorSettings(true));
};

window.closeAISettingsModal = function () {
    document.getElementById('aiSettingsModal')?.classList.add('hidden');
};

window.resetAISettingsForm = function () {
    populateAISettingsForm(AI_SETTINGS_DEFAULTS);
};

window.saveAIIndicatorSettings = async function (event) {
    event?.preventDefault();
    const context = getAIIndicatorPageContext();
    const updates = {
        AI_INDICATOR_MODEL: normalizeAIModel(document.getElementById('aiSettingModel').value),
        AI_DECISION_TIMEFRAME: document.getElementById('aiSettingTimeframe').value,
        AI_MIN_SETUP_SCORE: Number(document.getElementById('aiSettingScore').value),
        AI_MIN_REWARD_RISK: Number(document.getElementById('aiSettingRewardRisk').value),
        AI_MAX_ENTRY_RSI: Number(document.getElementById('aiSettingMaxRsi').value),
        AI_MIN_VOLUME_RATIO: Number(document.getElementById('aiSettingVolume').value),
        AI_REQUIRE_VOLUME_CONFIRMATION: document.getElementById('aiSettingRequireVolume').checked,
        AI_NEWS_ALIGNMENT_MODE: document.getElementById('aiSettingNewsMode').value,
        AI_INDICATOR_CACHE_MINUTES: Number(document.getElementById('aiSettingCache').value)
    };
    const saveButton = document.getElementById('aiSettingsSaveBtn');
    const message = document.getElementById('aiSettingsMessage');
    saveButton.disabled = true;
    saveButton.textContent = 'Saving…';
    try {
        let headers = { 'Content-Type': 'application/json' };
        if (typeof getAuthHeaders === 'function') headers = await getAuthHeaders();
        const apiBase = typeof API_BASE !== 'undefined' ? API_BASE : (window.BACKTEST_API_BASE || '');
        const response = await fetch(`${apiBase}/api/settings/ai?context=${encodeURIComponent(context)}`, {
            method: 'POST', headers, body: JSON.stringify(updates)
        });
        const payload = await response.json();
        if (!response.ok) throw new Error(payload.detail || `HTTP ${response.status}`);
        const savedSettings = setLocalAIIndicatorSettings(payload.settings || updates, context);
        setSelectedAIModelForContext(savedSettings.AI_INDICATOR_MODEL, context);
        Object.keys(localStorage).forEach(key => {
            if (key.startsWith(`ai_cache_${context}|`)) localStorage.removeItem(key);
        });
        localStorage.removeItem(`latest_ai_analysis:${context}`);
        message.className = 'text-xs rounded-lg px-3 py-2 bg-emerald-50 text-emerald-700 border border-emerald-200';
        message.textContent = 'Settings saved. Recalculating the current ticker…';
        const ticker = getAIActiveTicker('SPY');
        await fetchAIIndicator(ticker, updates.AI_DECISION_TIMEFRAME, true);
        closeAISettingsModal();
    } catch (error) {
        message.className = 'text-xs rounded-lg px-3 py-2 bg-rose-50 text-rose-700 border border-rose-200';
        message.textContent = error.message || 'Could not save settings.';
    } finally {
        saveButton.disabled = false;
        saveButton.textContent = 'Save & recalculate';
    }
};

var AI_INDICATOR_CACHE_TTL = 30 * 60 * 1000; // 30 minutes
var _aiIndicatorFetchId = window._aiIndicatorFetchId || 0; // prevents stale response rendering
var aiIndicatorInFlightKey = window.aiIndicatorInFlightKey || null;
var aiIndicatorInFlightPromise = window.aiIndicatorInFlightPromise || null;
var aiIndicatorAbortController = window.aiIndicatorAbortController || null;
var AI_FETCH_TIMEOUT_MS = window.AI_FETCH_TIMEOUT_MS || 25000;

function normalizeAIIndicatorTicker(ticker) {
    const normalized = (ticker || '').toString().trim().toUpperCase();
    return normalized && normalized !== 'NULL' && normalized !== 'UNDEFINED' ? normalized : null;
}
window.normalizeAIIndicatorTicker = normalizeAIIndicatorTicker;

function getAIActiveTicker(fallback = 'SPY') {
    const candidates = [
        typeof selectedTicker !== 'undefined' ? selectedTicker : null,
        typeof activeTicker !== 'undefined' ? activeTicker : null,
        window.activeTicker,
        window.selectedTicker,
    ];
    return candidates.map(normalizeAIIndicatorTicker).find(Boolean) || fallback;
}
window.getAIActiveTicker = getAIActiveTicker;

function normalizeAIIndicatorTimeframe(timeframe) {
    const raw = String(timeframe || '1Hour').trim();
    const aliases = {
        '1m': '1Min', '1min': '1Min', '1minute': '1Min',
        '5m': '5Min', '5min': '5Min', '5minute': '5Min',
        '15m': '15Min', '15min': '15Min', '15minute': '15Min',
        '30m': '30Min', '30min': '30Min', '30minute': '30Min',
        '1h': '1Hour', '1hr': '1Hour', '1hour': '1Hour', '60min': '1Hour',
        '4h': '4Hour', '4hr': '4Hour', '4hour': '4Hour',
        '1d': '1Day', '1day': '1Day', 'day': '1Day', 'daily': '1Day'
    };
    if (AI_DECISION_TIMEFRAMES.includes(raw)) return raw;
    return aliases[raw.toLowerCase().replace(/\s+/g, '')] || '1Hour';
}
window.normalizeAIIndicatorTimeframe = normalizeAIIndicatorTimeframe;

function getAIIndicatorCacheKey(ticker, timeframe, model) {
    const normalizedTicker = normalizeAIIndicatorTicker(ticker) || 'BTCUSD';
    const normalizedTimeframe = normalizeAIIndicatorTimeframe(timeframe);
    const normalizedModel = normalizeAIModel(model);
    return `${getAIIndicatorPageContext()}|${normalizedTicker}|${normalizedTimeframe}|${normalizedModel}|${AI_INDICATOR_CACHE_VERSION}`;
}
window.getAIIndicatorCacheKey = getAIIndicatorCacheKey;

function getLegacyAIIndicatorCacheKeys(ticker, timeframe, model) {
    const normalizedTicker = normalizeAIIndicatorTicker(ticker) || 'BTCUSD';
    const normalizedTimeframe = normalizeAIIndicatorTimeframe(timeframe);
    const normalizedModel = normalizeAIModel(model);
    return ['dashboard', 'bots', 'backtest'].map(context =>
        `${context}|${normalizedTicker}|${normalizedTimeframe}|${normalizedModel}|ai-hourly-v2`
    );
}

function isAICacheFresh(cached) {
    const ttlMinutes = Number(getLocalAIIndicatorSettings().AI_INDICATOR_CACHE_MINUTES || 30);
    return cached && cached.timestamp && (Date.now() - cached.timestamp) < ttlMinutes * 60 * 1000;
}
window.isAICacheFresh = isAICacheFresh;

function isAICacheForContext(cached, ticker, timeframe, model) {
    if (!isAICacheFresh(cached)) return false;
    if (cached?.data?.setup_score == null) return false;
    if (cached.version !== AI_INDICATOR_CACHE_VERSION) return false;
    if (!cached?.data?.technical_details || !Array.isArray(cached?.data?.gate_checks)) return false;
    const expectedKey = getAIIndicatorCacheKey(ticker, timeframe, model);
    if (cached.key === expectedKey) return true;
    // Accept entries written by the previous page-scoped cache format and
    // migrate them when they are read. Metadata remains the source of truth.
    return (
        normalizeAIIndicatorTicker(cached.ticker || cached.data?.ticker) === normalizeAIIndicatorTicker(ticker) &&
        normalizeAIIndicatorTimeframe(cached.timeframe) === normalizeAIIndicatorTimeframe(timeframe) &&
        cached.model === normalizeAIModel(model)
    );
}
window.isAICacheForContext = isAICacheForContext;

function getAICache(key) {
    try {
        const candidateKeys = [key];
        const parts = String(key).split('|');
        if (parts.length >= 4) {
            const [, ticker, timeframe, model] = parts;
            const version = parts[4] || AI_INDICATOR_CACHE_VERSION;
            ['dashboard', 'bots', 'backtest'].forEach(context => {
                const crossPageKey = `${context}|${ticker}|${timeframe}|${model}|${version}`;
                if (!candidateKeys.includes(crossPageKey)) candidateKeys.push(crossPageKey);
            });
            candidateKeys.push(...getLegacyAIIndicatorCacheKeys(ticker, timeframe, model));
        }
        for (const candidateKey of candidateKeys) {
            const cachedStr = localStorage.getItem(`ai_cache_${candidateKey}`);
            if (!cachedStr) continue;
            const cached = JSON.parse(cachedStr);
            if (!isAICacheFresh(cached)) {
                if (candidateKey === key) localStorage.removeItem(`ai_cache_${candidateKey}`);
                continue;
            }
            if (candidateKey !== key) {
                const migrated = { ...cached, key };
                localStorage.setItem(`ai_cache_${key}`, JSON.stringify(migrated));
                return migrated;
            }
            return cached;
        }
    } catch (e) { console.error('Cache read error', e); }
    return null;
}
window.getAICache = getAICache;

function getStaleAICache(key) {
    try {
        const candidateKeys = [key];
        const parts = String(key).split('|');
        if (parts.length === 4) {
            candidateKeys.push(...getLegacyAIIndicatorCacheKeys(parts[0], parts[1], parts[2]));
        }
        for (const candidateKey of candidateKeys) {
            const cachedStr = localStorage.getItem(`ai_cache_${candidateKey}`);
            if (!cachedStr) continue;
            const cached = JSON.parse(cachedStr);
            if (cached?.data) return { ...cached, key };
        }
    } catch (e) { console.error('Stale AI cache read error', e); }
    return null;
}

function setAICache(key, data, meta = {}) {
    try {
        const payload = {
            data: data,
            timestamp: Date.now(),
            version: AI_INDICATOR_CACHE_VERSION,
            key,
            ticker: normalizeAIIndicatorTicker(meta.ticker || data?.ticker),
            timeframe: meta.timeframe,
            model: normalizeAIModel(meta.model)
        };
        localStorage.setItem(`ai_cache_${key}`, JSON.stringify(payload));
        localStorage.setItem(`latest_ai_analysis:${getAIIndicatorPageContext()}`, JSON.stringify(payload));
    } catch (e) { console.error('Cache write error', e); }
}
window.setAICache = setAICache;

function normalizeCachedAISentimentScore(rawScore) {
    const value = Number(rawScore);
    if (!Number.isFinite(value)) return null;
    if (value >= -1 && value <= 1) return value;
    if (value >= 0 && value <= 100) return (value / 100) * 2 - 1;
    return Math.max(-1, Math.min(1, value));
}
window.normalizeCachedAISentimentScore = normalizeCachedAISentimentScore;

function normalizeAISentimentPct(rawPct, rawScore) {
    const pct = Number(rawPct);
    if (Number.isFinite(pct)) return Math.max(0, Math.min(100, pct));
    const score = normalizeCachedAISentimentScore(rawScore);
    if (score === null) return 50;
    return Math.max(0, Math.min(100, ((score + 1.0) / 2.0) * 100));
}
window.normalizeAISentimentPct = normalizeAISentimentPct;

function getAISentimentDirectionFromPct(pct, params = {}, fallback = null) {
    if (fallback && ['BULLISH', 'BEARISH', 'NEUTRAL'].includes(String(fallback).toUpperCase())) {
        return String(fallback).toUpperCase();
    }
    const bullishThresh = Number(params.SENTIMENT_BULLISH_PERCENTAGE ?? params.sentiment_bullish_percentage ?? 70);
    const bearishThresh = Number(params.SENTIMENT_BEARISH_PERCENTAGE ?? params.sentiment_bearish_percentage ?? 30);
    const value = Number(pct);
    if (Number.isFinite(value) && value >= bullishThresh) return 'BULLISH';
    if (Number.isFinite(value) && value <= bearishThresh) return 'BEARISH';
    return 'NEUTRAL';
}
window.getAISentimentDirectionFromPct = getAISentimentDirectionFromPct;

function getCachedAIAnalysisForTicker(ticker, timeframe) {
    const tf = getAIDecisionTimeframe();
    const normalizedTicker = normalizeAIIndicatorTicker(ticker);
    if (!normalizedTicker) return null;
    const selectedModel = getSelectedAIModelForContext();
    const exact = getAICache(getAIIndicatorCacheKey(normalizedTicker, tf, selectedModel));
    if (exact?.data?.sentiment_score != null) return exact;

    try {
        const globalAi = JSON.parse(localStorage.getItem(`latest_ai_analysis:${getAIIndicatorPageContext()}`) || 'null');
        if (
            globalAi?.data?.sentiment_score != null &&
            isAICacheForContext(globalAi, normalizedTicker, tf, selectedModel)
        ) {
            return globalAi;
        }
    } catch (e) {}

    try {
        const prefix = `ai_cache_${getAIIndicatorPageContext()}|${normalizedTicker}|`;
        for (let i = localStorage.length - 1; i >= 0; i -= 1) {
            const key = localStorage.key(i);
            if (!key || !key.startsWith(prefix)) continue;
            const cached = JSON.parse(localStorage.getItem(key) || 'null');
            if (
                cached?.data?.sentiment_score != null &&
                isAICacheForContext(cached, normalizedTicker, tf, selectedModel)
            ) return cached;
        }
    } catch (e) {}
    return null;
}
window.getCachedAIAnalysisForTicker = getCachedAIAnalysisForTicker;

function getCachedNewsAISentiment(ticker, timeframe) {
    const tf = getAIDecisionTimeframe();
    const cached = getCachedAIAnalysisForTicker(ticker, tf);
    const data = cached?.data;
    if (!data) return null;
    const score = normalizeCachedAISentimentScore(data.sentiment_score);
    if (score === null) return null;
    const pct = Math.round(normalizeAISentimentPct(data.sentiment_pct, data.sentiment_score));
    return {
        score,
        pct,
        direction: getAISentimentDirectionFromPct(pct, {}, data.sentiment_direction),
        reason: data.sentiment_key_factor || data.key_catalyst || 'Mixed Catalysts',
        summary: data.sentiment_summary || data.summary || '',
        confidence: Number(data.sentiment_confidence ?? data.confidence ?? 0),
    };
}
window.getCachedNewsAISentiment = getCachedNewsAISentiment;

function applyCachedNewsAISignal(signal, options = {}) {
    if (!signal) return signal;
    const ticker = options.ticker || (typeof selectedTicker !== 'undefined' ? selectedTicker : null);
    const tf = options.timeframe || (typeof currentBackendTf !== 'undefined' && currentBackendTf ? currentBackendTf : '1Hour');
    const sentiment = getCachedNewsAISentiment(ticker, tf);
    if (!sentiment) return signal;
    signal.value = sentiment.score;
    signal.percentage = sentiment.pct;
    signal.sentiment_pct = sentiment.pct;
    signal.sentiment_direction = sentiment.direction;
    signal.reason = sentiment.reason;
    signal.cached = true;
    signal.sentiment_summary = sentiment.summary;
    signal.sentiment_confidence = sentiment.confidence;
    const params = options.params || (typeof window.lastDashboardData !== 'undefined' ? window.lastDashboardData?.indicator_parameters : null) || {};
    const bullishThresh = params.SENTIMENT_BULLISH_PERCENTAGE ?? 70;
    const bearishThresh = params.SENTIMENT_BEARISH_PERCENTAGE ?? 30;
    signal.signal = sentiment.pct >= bullishThresh ? 'BULLISH' : sentiment.pct <= bearishThresh ? 'BEARISH' : 'NEUTRAL';
    return signal;
}
window.applyCachedNewsAISignal = applyCachedNewsAISignal;

function renderCachedAIIndicatorForTicker(ticker, timeframe) {
    const t = ticker || (typeof selectedTicker !== 'undefined' ? selectedTicker : null);
    const tf = getAIDecisionTimeframe();
    const cached = getCachedAIAnalysisForTicker(t, tf);
    if (!cached?.data) return false;
    renderAIIndicatorCard(cached.data, true);
    if (typeof window.onAIIndicatorUpdated === 'function') {
        window.onAIIndicatorUpdated(cached.data, true);
    }
    return true;
}
window.renderCachedAIIndicatorForTicker = renderCachedAIIndicatorForTicker;

async function fetchAIIndicator(ticker, timeframe, force = false) {
    if (!ticker) return;
    const aiSettings = await loadAIIndicatorSettings(false);
    const tf = normalizeAIIndicatorTimeframe(aiSettings.AI_DECISION_TIMEFRAME || timeframe || '1Hour');
    const normalizedTicker = normalizeAIIndicatorTicker(ticker);
    const selectedModel = getSelectedAIModelForContext();
    const key = getAIIndicatorCacheKey(normalizedTicker, tf, selectedModel);

    if (!force && aiIndicatorInFlightKey === key && aiIndicatorInFlightPromise) {
        // The original caller owns rendering/error handling. Joined refreshes
        // must not surface the same rejected request as unhandled promises.
        return aiIndicatorInFlightPromise.catch(() => null);
    }
    if (force && aiIndicatorAbortController) {
        aiIndicatorAbortController.abort();
    }

    // Check client-side cache first (unless forced)
    if (!force) {
        const cached = getAICache(key);
        if (cached && cached.data) {
            renderAIIndicatorCard(cached.data, true);
            if (typeof window.onAIIndicatorUpdated === 'function') {
                window.onAIIndicatorUpdated(cached.data, true);
            }
            return cached.data;
        }
    }

    // Show loading skeleton
    _showAIIndicatorLoading(normalizedTicker);

    const fetchId = ++_aiIndicatorFetchId;
    aiIndicatorInFlightKey = key;
    aiIndicatorAbortController = new AbortController();
    aiIndicatorInFlightPromise = (async () => {
        let headers = {};
        if (typeof getAuthHeaders === 'function') {
            try { headers = await getAuthHeaders(); } catch(e) {}
        }
        const apiBase = typeof API_BASE !== 'undefined' ? API_BASE : '';
        const context = getAIIndicatorPageContext();
        const url = `${apiBase}/api/ai-indicator?ticker=${encodeURIComponent(normalizedTicker)}&timeframe=${encodeURIComponent(tf)}&model=${encodeURIComponent(selectedModel)}&context=${encodeURIComponent(context)}${force ? '&force=true' : ''}`;
        
        let res;
        if (typeof fetchWithTimeout === 'function') {
            res = await fetchWithTimeout(url, { headers, signal: aiIndicatorAbortController.signal }, AI_FETCH_TIMEOUT_MS);
        } else {
            res = await fetch(url, { headers, signal: aiIndicatorAbortController.signal });
        }
        if (!res.ok) throw new Error(`HTTP ${res.status}`);
        const data = await res.json();

        // Guard against stale responses
        if (fetchId !== _aiIndicatorFetchId) return data;
        if (data.error) {
            const stale = getStaleAICache(key);
            if (stale?.data) {
                const fallback = { ...stale.data, cached: true, cache_stale: true };
                renderAIIndicatorCard(fallback, true);
                if (typeof window.onAIIndicatorUpdated === 'function') {
                    window.onAIIndicatorUpdated(fallback, true);
                }
                console.warn(`[ai-indicator] ${data.error}; showing the last cached analysis.`);
                return fallback;
            }
            if (selectedModel !== 'llama-3.3-70b-versatile') {
                console.warn(`[ai-indicator] Model ${selectedModel} failed. Falling back to default Groq model.`);
                setSelectedAIModelForContext('llama-3.3-70b-versatile');
                return fetchAIIndicator(ticker, tf, force);
            }
            _showAIIndicatorError(data.error, normalizedTicker);
            return;
        }

        setAICache(key, data, { ticker: normalizedTicker, timeframe: tf, model: selectedModel });
        renderAIIndicatorCard(data, false);
        if (typeof window.onAIIndicatorUpdated === 'function') {
            window.onAIIndicatorUpdated(data, false);
        }
        return data;
    })();

    try {
        return await aiIndicatorInFlightPromise;
    } catch (e) {
        if (fetchId === _aiIndicatorFetchId) {
            if (e.name === 'AbortError') {
                return null;
            } else if (e.name === 'TimeoutError') {
                console.warn(`[ai-indicator] Fetch timed out after ${AI_FETCH_TIMEOUT_MS}ms.`);
            } else {
                console.error('[ai-indicator] Fetch error:', e);
            }
            if (selectedModel !== 'llama-3.3-70b-versatile') {
                console.warn(`[ai-indicator] Fetch failed for ${selectedModel}. Falling back to default Groq model.`);
                setSelectedAIModelForContext('llama-3.3-70b-versatile');
                return fetchAIIndicator(ticker, tf, force);
            }
            const stale = getStaleAICache(key);
            if (stale?.data) {
                const fallback = { ...stale.data, cached: true, cache_stale: true };
                renderAIIndicatorCard(fallback, true);
                if (typeof window.onAIIndicatorUpdated === 'function') {
                    window.onAIIndicatorUpdated(fallback, true);
                }
                console.warn('[ai-indicator] Request failed; showing the last cached analysis.');
                return fallback;
            }
            _showAIIndicatorError('Could not load AI analysis', normalizedTicker);
        }
    } finally {
        if (fetchId === _aiIndicatorFetchId) {
            aiIndicatorInFlightKey = null;
            aiIndicatorInFlightPromise = null;
            aiIndicatorAbortController = null;
        }
    }
}
window.fetchAIIndicator = fetchAIIndicator;

window.refreshAIIndicator = async function () {
    const refreshBtn = document.getElementById('aiRefreshBtn');
    if (refreshBtn) {
        refreshBtn.disabled = true;
        refreshBtn.classList.add('animate-spin');
    }
    try {
        const ticker = getAIActiveTicker('SPY');
        await loadAIIndicatorSettings(true);
        const tf = getAIDecisionTimeframe();
        await fetchAIIndicator(ticker, tf, true);
    } finally {
        if (refreshBtn) {
            refreshBtn.disabled = false;
            refreshBtn.classList.remove('animate-spin');
        }
    }
};
