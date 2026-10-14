/*
 * Entry/Exit Setup of a Bots card, in the Screener card's style: a collapsible group with an alert-source badge that
 * holds an Entry Setup block and an Exit Setup block, whether or not the bot holds a position.
 *
 * It replaces the old "Forecast Entry/Exit Strategy" text. What it shows is the bot's configuration: the strategy rule
 * for a strategy bot, otherwise the configured indicators, thresholds and ATR / percentage / swing numbers (built by the
 * server in scan_log.setup_config) plus the mode's explanation. Display only: no network, no orders.
 * Every dynamic string is escaped. Phone first: blocks stack and text wraps; nothing sets a fixed width.
 */
(function () {
    'use strict';
    if (window.BotsSetupWatch) return;

    const STORE_KEY = 'botsSetupWatchExpanded';
    const esc = (value) => String(value == null ? '' : value)
        .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;').replace(/'/g, '&#39;');

    function loadExpanded() {
        try { return JSON.parse(localStorage.getItem(STORE_KEY) || '{}') || {}; } catch (e) { return {}; }
    }
    window.botsSetupWatchExpanded = window.botsSetupWatchExpanded || loadExpanded();

    /* Expand/collapse one card's setup group; the choice is remembered per ticker (collapsed by default, like the Screener). */
    window.toggleBotsSetupWatch = function (panelId, button) {
        const panel = document.getElementById(panelId);
        if (!panel) return;
        const expanded = panel.classList.toggle('hidden') === false;
        const key = button && button.dataset ? button.dataset.setupWatchKey : '';
        if (key) {
            window.botsSetupWatchExpanded[key] = expanded;
            try { localStorage.setItem(STORE_KEY, JSON.stringify(window.botsSetupWatchExpanded)); } catch (e) { /* per-viewer convenience only */ }
        }
        if (button) {
            button.setAttribute('aria-expanded', expanded ? 'true' : 'false');
            const arrow = button.querySelector('[data-setup-watch-arrow]');
            if (arrow) arrow.classList.toggle('rotate-180', expanded);
        }
    };

    const SOURCES = {
        indicators: { label: 'Indicators', icon: '📊', badge: 'bg-indigo-100 text-indigo-800 border-indigo-200' },
        strategy: { label: 'Strategy', icon: '🧭', badge: 'bg-purple-100 text-purple-800 border-purple-200' },
        ai: { label: 'AI Autopilot', icon: '🤖', badge: 'bg-sky-100 text-sky-800 border-sky-200' },
    };

    const line = (label, text) => (text
        ? `<span class="block text-[0.49rem] font-semibold text-slate-600 leading-snug whitespace-normal break-words"><span class="font-black text-slate-700">${esc(label)}:</span> ${esc(text)}</span>`
        : '');
    const note = (text) => (text
        ? `<span class="block text-[0.47rem] font-medium text-slate-500 leading-snug whitespace-normal break-words">${esc(text)}</span>`
        : '');
    const plainLine = (text) => (text
        ? `<span class="block text-[0.49rem] font-semibold text-slate-600 leading-snug whitespace-normal break-words">${esc(text)}</span>`
        : '');

    /* RTH = regular trading hours, ATH = extended (pre-market and after-hours), 24H = around the clock (crypto): the Screener's badge. */
    function sessionBadge(opts) {
        const session = String((opts.settings && opts.settings.market_session) || '').trim().toLowerCase();
        let label = 'RTH';
        if (opts.isCrypto) label = '24H';
        else if (!opts.isOptions && ['extended', '24h', 'crypto24', 'all', '24/5', 'overnight'].includes(session)) label = 'ATH';
        const tone = { '24H': 'bg-fuchsia-50 text-fuchsia-700 border-fuchsia-200', ATH: 'bg-amber-50 text-amber-700 border-amber-200', RTH: 'bg-indigo-50 text-indigo-600 border-indigo-100' }[label];
        const title = { '24H': 'Trades around the clock', ATH: 'Extended hours: pre-market and after-hours, 3:00 AM–7:00 PM CT', RTH: 'Regular trading hours: 8:30 AM–3:00 PM CT' }[label];
        return `<span class="px-1.5 py-0.5 rounded border ${tone} text-[0.48rem] font-black uppercase" title="${esc(title)}" data-bots-session="${label}">${label}</span>`;
    }

    function block(tone, title, accent, lines) {
        const buy = tone === 'buy';
        return `<div class="rounded-lg border ${buy ? 'border-emerald-100 bg-emerald-50/50' : 'border-rose-100 bg-rose-50/50'} px-1.5 py-0.5 min-w-0" title="${esc(accent)}" data-bots-setup-side="${tone}">
            <span class="block text-[0.46rem] font-black uppercase tracking-wider ${buy ? 'text-emerald-700' : 'text-rose-700'}">${esc(title)} - <span class="text-indigo-700">${esc(accent)}</span></span>
            ${lines.filter(Boolean).join('')}
        </div>`;
    }

    function helperText(side, mode, isOptions) {
        if (typeof STRATEGY_HELPER_TEXTS === 'undefined' || !STRATEGY_HELPER_TEXTS) return '';
        const map = STRATEGY_HELPER_TEXTS[isOptions ? `${side}_options` : side] || {};
        return map[mode] || '';
    }

    /* The rule text says "(default 5 minutes before the close)"; a bot with its own times shows those instead. */
    const withoutDefault = (text) => String(text || '').replace(/\s*\(default[^)]*\)/gi, '');
    const scheduleLines = (sch, side) => (sch
        ? (side === 'buy'
            ? [line('Your buy time', sch.buy_label), line('Mode', sch.mode)]
            : [line('Your sell time', sch.sell_label)])
        : []);

    function strategyBlocks(opts) {
        const info = opts.strategyInfo || {};
        const definition = window.StrategyPreset && typeof window.StrategyPreset.describe === 'function'
            ? window.StrategyPreset.describe(info.preset_id, info.variant_id) : null;
        const setup = opts.setup || {};
        const sch = opts.schedule || null;
        const adapt = (text) => (window.BotsSummary ? window.BotsSummary.adaptRule(text, opts.timeframe) : String(text || ''));   // "daily bar" -> the bot's real timeframe
        const rule = sch ? (text) => adapt(withoutDefault(text)) : adapt;
        if (!definition) {
            const accent = setup.strategy || String(info.label || 'Strategy');
            return { accent, buy: scheduleLines(sch, 'buy').concat([line('Entry', rule(setup.entry))]), sell: scheduleLines(sch, 'sell').concat([line('Exit', rule(setup.exit))]) };
        }
        const variant = definition.variant ? `${definition.variant.label}${definition.variant.description ? ` — ${definition.variant.description}` : ''}` : '';
        const designed = definition.timeframes || [];
        const chosen = String(opts.timeframe || '');
        const timeframeText = chosen
            ? (designed.length && !designed.includes(chosen)
                ? `Timeframe ${chosen}. Designed for ${designed.join(', ')}; other timeframes are untested`
                : `Timeframe ${chosen}`)
            : (designed.length ? `Timeframe ${designed.join(', ')}` : '');
        const frame = [
            timeframeText,
            definition.sessions && definition.sessions.length ? `${definition.sessions.join(', ')} session` : '',
            definition.variant && definition.variant.warmup_bars ? `needs ${definition.variant.warmup_bars} completed bars of history` : '',
        ].filter(Boolean).join(' · ');
        return {
            accent: definition.label,
            buy: scheduleLines(sch, 'buy').concat([line('Strategy', definition.summary), line('Entry', rule(definition.entry_rule)), sch ? '' : line('Variant', variant), line('Runs on', frame)]),
            sell: scheduleLines(sch, 'sell').concat([line('Exit', rule(definition.exit_rule)), line('Decided on', 'completed candles only')]),
        };
    }

    function manualBlocks(opts) {
        const setup = opts.setup || {};
        const config = opts.config || {};
        const settings = opts.settings || {};
        const ai = setup.entry === 'AI Autopilot';
        if (ai) {
            const text = '💡 AI Autopilot — Uses the configured AI model to assess the completed-candle setup while honoring the selected risk and execution controls.';
            return { accent: 'AI Autopilot', buy: [note(text)], sell: [note(text)] };
        }
        const buyMode = String(config.buy_mode || settings.buy_mode || 'indicator');
        const sellMode = String(config.sell_mode || settings.sell_mode || 'indicator');
        const lines = (list) => (Array.isArray(list) ? list : []).map(plainLine);
        return {
            accent: '',
            buyAccent: setup.entry || buyMode,
            sellAccent: setup.exit || sellMode,
            buy: lines(config.entry).concat([note(helperText('entry', buyMode, opts.isOptions))]),
            sell: lines(config.exit).concat([note(helperText('exit', sellMode, opts.isOptions))]),
        };
    }

    /*
     * opts: ticker, setup (scan_log.setup), config (scan_log.setup_config), settings (slim ticker settings),
     *       strategyInfo (scan.strategy_preset), isOptions, hasPosition
     */
    function render(opts) {
        if (!opts || !opts.setup) return '';
        const setup = opts.setup;
        const strategy = setup.kind === 'strategy';
        const blocks = strategy ? strategyBlocks(opts) : manualBlocks(opts);
        const aiBot = !strategy && setup.entry === 'AI Autopilot';
        const source = SOURCES[strategy ? 'strategy' : aiBot ? 'ai' : 'indicators'];
        const key = String(opts.ticker || '').toUpperCase();
        const panelId = `bots-setup-watch-${key.replace(/[^a-zA-Z0-9_-]/g, '_')}`;
        const expanded = Boolean(window.botsSetupWatchExpanded[key]);
        const settings = opts.settings || {};
        const putsOnly = opts.isOptions && String(settings.options_type || '') === 'puts_only';
        const buyTitle = opts.isOptions ? (putsOnly ? 'Entry Setup · Put' : 'Entry Setup · Call') : 'Entry Setup';
        const sellTitle = 'Exit Setup';
        return `
            <section class="mt-0.5 sm:mt-1 rounded-lg border border-indigo-100 bg-white/70 overflow-hidden" data-bots-setup-watch="${esc(key)}">
                <button type="button" data-setup-watch-key="${esc(key)}" aria-controls="${esc(panelId)}" aria-expanded="${expanded ? 'true' : 'false'}"
                    class="w-full flex flex-wrap items-center gap-1 sm:gap-1.5 px-1.5 py-0.5 sm:py-1 text-left hover:bg-indigo-50/60 transition-colors"
                    onclick="event.stopPropagation(); window.toggleBotsSetupWatch('${esc(panelId)}', this)">
                    <span class="text-[0.52rem] sm:text-[0.65rem] font-black uppercase tracking-tight sm:tracking-wider text-indigo-800 whitespace-nowrap">Entry/Exit Setup</span>
                    <span class="px-1.5 py-0.5 rounded-md ${source.badge} border font-black text-[0.48rem] uppercase tracking-wide inline-flex items-center gap-0.5 max-w-full break-words" title="Decided by: ${esc(source.label)}"><span aria-hidden="true">${source.icon}</span>${esc(source.label)}</span>
                    ${sessionBadge(opts)}
                    ${opts.timeframeLabel ? `<span class="px-1.5 py-0.5 rounded border bg-indigo-50/50 text-indigo-500 border-indigo-100/50 text-[0.48rem] font-black uppercase whitespace-nowrap" data-bots-timeframe>${esc(opts.timeframeLabel)}</span>` : ''}
                    <svg data-setup-watch-arrow class="ml-auto h-3 w-3 shrink-0 text-indigo-500 transition-transform ${expanded ? 'rotate-180' : ''}" viewBox="0 0 20 20" fill="currentColor" aria-hidden="true"><path fill-rule="evenodd" d="M5.23 7.21a.75.75 0 011.06.02L10 11.168l3.71-3.938a.75.75 0 111.08 1.04l-4.25 4.5a.75.75 0 01-1.08 0l-4.25-4.5a.75.75 0 01.02-1.06z" clip-rule="evenodd" /></svg>
                </button>
                <div id="${esc(panelId)}" class="${expanded ? '' : 'hidden '}border-t border-indigo-100 px-1.5 pb-1 pt-0.5 sm:pb-1.5 sm:pt-1">
                    <div class="grid grid-cols-1 sm:grid-cols-2 gap-0.5 sm:gap-1 items-start" data-setup-columns>
                        ${block('buy', buyTitle, blocks.buyAccent || blocks.accent, blocks.buy)}
                        ${block('sell', sellTitle, blocks.sellAccent || blocks.accent, blocks.sell)}
                    </div>
                </div>
            </section>`;
    }

    window.BotsSetupWatch = { render };
})();
