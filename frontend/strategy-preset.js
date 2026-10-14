/*
 * Strategy preset picker shared by Bots, Screener and Backtest.
 *
 * A strategy is a fixed, published rule set that owns its entries, exits and
 * indicator values. While one is active the manual Entry/Exit controls,
 * thresholds and indicator boxes are locked. Trading Timeframe, Market Session
 * and (only when the strategy itself defines them) Sizing Method / Amount Per
 * Trade stay the user's own editable fields: picking a strategy from the dropdown
 * fills them with the strategy's values and labels them "Set by strategy". If the
 * user changes one, the label turns amber ("Manual") and says what the strategy
 * defines; the choice always stands. Turning the toggle on or off never changes
 * them. Only values the engine cannot run (24h and timeframes the data providers do not offer) are disabled. Risk limits are
 * never touched.
 *
 * The catalog comes from GET /api/strategy-presets (single source of truth).
 * The panel mounts into <div id="strategyPresetPanel_regular" data-page="bots|screener|backtest">.
 */
(function () {
    'use strict';
    if (window.StrategyPreset) return;

    const PANEL_ID = 'strategyPresetPanel_regular';
    const MANUAL_BOX_ID = 'manualStrategyBox_regular';
    const IDS = {
        buyMode: 'strategyBuyMode_regular',
        sellMode: 'strategySellMode_regular',
        buyThreshold: 'strategyBuyThreshold_regular',
        sellThreshold: 'strategySellThreshold_regular',
        timeframe: 'strategyTimeframe_regular',
        session: 'strategyMarketSession_regular',
        allocationMode: 'strategyAllocationMode_regular',
    };
    const AMOUNT_IDS = ['strategyCapital', 'strategyTradeAmount_regular'];
    // The manual re-entry rules do not apply to a strategy (it owns its entries and exits): while one is active
    // they are switched off and greyed out, and the user's own choices come back when it is turned off.
    // The Screener has no such rules (its cooldown is an alert cooldown), so it is left alone.
    const REENTRY_TOGGLES = [
        { id: 'strategyEnableCooldown_regular', backup: 'enable_cooldown' },
        { id: 'strategyRegularSignalReset', backup: 'enable_signal_reset' },
        { id: 'strategyRegularSmartReentry', backup: 'enable_smart_reentry' },
    ];
    const INDICATOR_CONTAINERS = ['indicatorChecksContainer_regular', 'indicatorChecksContainer_bt_regular'];
    const FIELD_WRAPPERS = ['updateRegularEntryFields', 'updateRegularExitFields', 'updateBtRegularEntryFields', 'updateBtRegularExitFields'];
    const CAPTIONS = {
        bots: 'This bot trades the strategy’s entries and exits (paper by default). Sizing and risk stay yours.',
        screener: 'The Screener sends BUY and SELL (exit) alerts from this strategy. It never places orders.',
        backtest: 'Backtest uses the same strategy engine as Bots and Screener. Orders fill at the next open.',
    };
    const ALLOCATION_CAPTIONS = {
        bots: 'This bot holds a target % of the chosen leveraged ETF and rebalances it once a day (paper by default). Sizing and risk stay yours.',
        screener: 'The Screener alerts when the regime and target % change. It never places orders.',
        backtest: 'Backtest uses the same allocation rules as Bots and Screener, deciding at the execution time.',
    };
    const SCHEDULE_CAPTIONS = {
        bots: 'This bot buys at your Buy time and sells at your Sell time, every trading day (paper by default). Sizing and risk stay yours.',
        screener: 'The Screener alerts at your Buy time and Sell time. It never places orders.',
        backtest: 'Backtest buys and sells at the same times as Bots, using the open of the intraday bar at each time.',
    };
    // Session Rider: Exit controls do not apply (no stop-loss, take-profit or trailing exits).
    const SCHEDULE_LOCKED_EXIT_IDS = ['exitFieldsATR', 'exitFieldsPct', 'exitFieldsCustom_regular', 'exitFieldsIndicator', 'exitFieldsHighLow', 'exitFieldsPivot'];
    // The select boxes follow the Trade Allocation fields of the page they sit on (text size, padding and fill), so the
    // strategy fields look like the neighbouring ones: Backtest uses text-sm/base, Bots the smaller phone text. The
    // Screener has no Trade Allocation box and keeps the original look. It is an object so the value is read at render
    // time, once the page is known.
    const SELECT_BASE = 'w-full backdrop-blur-md rounded-2xl font-black text-indigo-950 tracking-tight leading-tight shadow-sm focus:bg-white/80 focus:border-purple-400/80 focus:ring-2 focus:ring-purple-400/20 outline-none cursor-pointer appearance-none transition-all disabled:opacity-50';
    const SELECT_CLASS = {
        toString() {
            if (state.page === 'backtest') return `${SELECT_BASE} bg-white/40 border border-white/40 pl-2 sm:pl-4 pr-7 sm:pr-10 py-2 sm:py-3 text-sm sm:text-base`;
            if (state.page === 'bots') return `${SELECT_BASE} bg-white/40 border border-white/40 pl-2 sm:pl-4 pr-7 sm:pr-10 py-2 sm:py-3 text-[0.55rem] sm:text-xs`;
            return `${SELECT_BASE} bg-white/60 border border-white/50 pl-3 pr-9 py-2.5 sm:py-3 text-[0.65rem] sm:text-xs`;
        },
    };
    const CHEVRON = '<div class="absolute inset-y-0 right-0 flex items-center pr-3 pointer-events-none"><svg xmlns="http://www.w3.org/2000/svg" class="h-4 w-4 text-indigo-500" fill="none" viewBox="0 0 24 24" stroke="currentColor"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2.5" d="M19 9l-7 7-7-7"/></svg></div>';

    const state = {
        manualOff: false, // Screener only: Indicators switched off without a Strategy taking over (Event-Driven, or alerts paused)
        reentrySaved: {},
        page: 'bots',
        catalog: null,
        loadError: '',
        enabled: false,
        presetId: '',
        variantId: '',
        policy: 'unmanaged',
        // Malik allocation strategies: chosen ETF legs and execution time ('close-15', 'HH:MM' or a custom 'HH:MM').
        alloc: { bull: '', bear: '', exec: 'close-15', custom: false },
        // Session Rider: buy/sell times ('close-5', 'open+5', 'HH:MM' ET) and options.
        sched: { buy: 'close-5', sell: 'open+5', buyCustom: false, sellCustom: false, skipWeekend: false, lossPause: 3 },
        saved: {},
        internal: false,
        root: null,
    };
    let readyResolve;
    const ready = new Promise((resolve) => { readyResolve = resolve; });

    const esc = (value) => String(value == null ? '' : value)
        .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');
    const el = (id) => document.getElementById(id);

    function apiBase() {
        if (window.BACKTEST_API_BASE) return window.BACKTEST_API_BASE;
        try { if (typeof API_BASE !== 'undefined') return API_BASE; } catch (e) { /* not defined on this page */ }
        return '';
    }

    let loadInFlight = null;
    let retryTimer = null;
    let retryCount = 0;

    function retryDelays() {
        return Array.isArray(window.__spRetryDelays) ? window.__spRetryDelays : [1500, 3000, 6000, 12000, 20000];
    }

    /* The page may not be signed in yet when this script starts (Firebase restores
     * the session asynchronously), so a failed load retries by itself, and again as
     * soon as sign-in completes. */
    /* Resolve when Firebase has restored (or ruled out) a signed-in user, with a timeout. */
    function waitForAuth(timeoutMs = 8000) {
        const auth = window.auth;
        if (!auth || auth.currentUser) return Promise.resolve();
        return new Promise((resolve) => {
            const timer = setTimeout(resolve, timeoutMs);
            const done = () => { clearTimeout(timer); resolve(); };
            try {
                if (typeof auth.authStateReady === 'function') auth.authStateReady().then(done, done);
                else if (typeof auth.onAuthStateChanged === 'function') auth.onAuthStateChanged(done);
                else done();
            } catch (error) { done(); }
        });
    }

    function loadCatalog() {
        if (loadInFlight) return loadInFlight;
        loadInFlight = (async () => {
            try {
                await waitForAuth();
                const headers = typeof getAuthHeaders === 'function' ? await getAuthHeaders() : { 'Content-Type': 'application/json' };
                const response = await fetch(`${apiBase()}/api/strategy-presets`, { headers, cache: 'no-store' });
                if (!response.ok) throw new Error(response.status === 404 ? 'HTTP 404 (the server needs the latest backend version)' : `HTTP ${response.status}`);
                const data = await response.json();
                if (!Array.isArray(data.presets) || !data.presets.length) throw new Error('empty catalog');
                state.catalog = data.presets;
                state.loadError = '';
                try { document.dispatchEvent(new CustomEvent('strategy-preset-catalog-ready')); } catch (e) { /* optional listener */ }
                retryCount = 0;
                if (retryTimer) { clearTimeout(retryTimer); retryTimer = null; }
            } catch (error) {
                state.catalog = null;
                const reason = String((error && error.message) || 'request failed');
                state.loadError = `Strategy list unavailable (${reason}).`;
                console.warn('[strategy-preset] catalog unavailable:', reason);
                scheduleRetry();
            } finally {
                loadInFlight = null;
                render();
            }
        })();
        return loadInFlight;
    }

    function scheduleRetry() {
        const delays = retryDelays();
        if (retryTimer || retryCount >= delays.length) return;
        retryTimer = setTimeout(() => {
            retryTimer = null;
            retryCount += 1;
            loadCatalog();
        }, delays[retryCount]);
    }

    function retryNow() {
        if (retryTimer) { clearTimeout(retryTimer); retryTimer = null; }
        retryCount = 0;
        state.loadError = '';
        render();
        return loadCatalog();
    }

    function watchSignIn() {
        try {
            const auth = window.auth;
            if (auth && typeof auth.onAuthStateChanged === 'function') {
                auth.onAuthStateChanged((user) => { if (user && !state.catalog) retryNow(); });
            }
        } catch (error) { /* optional */ }
    }

    const selectedPreset = () => (state.catalog || []).find((p) => p.id === state.presetId) || null;
    const selectedVariant = () => {
        const preset = selectedPreset();
        if (!preset) return null;
        return preset.variants.find((v) => v.id === state.variantId) || preset.variants.find((v) => v.id === preset.default_variant) || preset.variants[0];
    };

    /* ── Locking the manual controls ─────────────────────────────────────── */
    function lockControl(control, on) {
        if (!control) return;
        if (on) {
            if (control.dataset.strategyLocked !== '1') {
                control.dataset.strategyLocked = '1';
                control.dataset.strategyWasDisabled = control.disabled ? '1' : '0';
            }
            control.disabled = true;
            control.classList.add('opacity-50', 'cursor-not-allowed');
            control.setAttribute('aria-disabled', 'true');
        } else if (control.dataset.strategyLocked === '1') {
            control.disabled = control.dataset.strategyWasDisabled === '1';
            control.classList.remove('opacity-50', 'cursor-not-allowed');
            control.removeAttribute('aria-disabled');
            delete control.dataset.strategyLocked;
            delete control.dataset.strategyWasDisabled;
        }
    }

    function lockReentryToggles(on) {
        if (state.page === 'screener') return;
        REENTRY_TOGGLES.forEach(({ id }) => {
            const input = el(id);
            if (!input) return;
            const toggle = input.closest('label');
            if (on) {
                if (!(id in state.reentrySaved)) state.reentrySaved[id] = !!input.checked;
                lockControl(input, true);
                input.title = 'Off while a strategy is active: the strategy decides its own re-entries.';
                if (toggle) toggle.classList.add('opacity-50', 'cursor-not-allowed');
                if (input.checked) {
                    input.checked = false;
                    state.internal = true;
                    try { input.dispatchEvent(new Event('change', { bubbles: true })); } finally { state.internal = false; }
                }
            } else if (id in state.reentrySaved) {
                lockControl(input, false);
                input.removeAttribute('title');
                if (toggle) toggle.classList.remove('opacity-50', 'cursor-not-allowed');
                if (input.checked !== state.reentrySaved[id]) {
                    input.checked = state.reentrySaved[id];
                    state.internal = true;
                    try { input.dispatchEvent(new Event('change', { bubbles: true })); } finally { state.internal = false; }
                }
                delete state.reentrySaved[id];
            }
        });
    }

    function lockScheduleExits(on) {
        SCHEDULE_LOCKED_EXIT_IDS.forEach((id) => {
            const box = el(id);
            if (!box) return;
            box.classList.toggle('opacity-50', on);
            box.classList.toggle('pointer-events-none', on);
            if (on) {
                box.setAttribute('aria-disabled', 'true');
                box.title = 'Set by strategy: Session Rider has no stop-loss, take-profit or trailing exits; it sells at your Sell time.';
            } else {
                box.removeAttribute('aria-disabled');
                box.removeAttribute('title');
            }
            box.querySelectorAll('input, select, button').forEach((control) => lockControl(control, on));
        });
    }

    function applyLock(forceOn) {
        const on = forceOn === undefined ? state.enabled : !!forceOn;
        lockScheduleExits(on && isSchedule(selectedPreset()));
        lockReentryToggles(on);
        // The indicator pool, Entry and Exit controls are not greyed out or disabled: while a Strategy is active the
        // whole Entry / Exit box is collapsed instead (see syncManualBox), so they cannot be used.
        syncManualBox();
    }

    /* ── Timeframe / market session: the user's fields, constrained by the strategy ── */
    function setSelectValue(control, value) {
        if (!control || value == null) return false;
        const wanted = String(value);
        if (!Array.from(control.options || []).some((option) => option.value === wanted)) return false;
        if (control.value !== wanted) {
            control.value = wanted;
            state.internal = true;
            try { control.dispatchEvent(new Event('change', { bubbles: true })); } finally { state.internal = false; }
        }
        return true;
    }

    /* kind: 'set' (purple, value is what the strategy defines) | 'manual' (amber, user changed it) | null. */
    function badge(control, kind, text) {
        const wrapper = control && (control.closest('.space-y-2') || control.parentElement);
        const label = wrapper && wrapper.querySelector('label');
        if (!label) return;
        const existing = label.querySelector('.strategy-set-badge');
        if (existing) existing.remove();
        if (!kind) return;
        const tag = document.createElement('span');
        const tone = kind === 'set'
            ? 'bg-purple-100 text-purple-700 border-purple-200'
            : 'bg-amber-100 text-amber-700 border-amber-300';
        tag.className = `strategy-set-badge ml-2 px-1.5 py-0.5 rounded-md border text-[0.5rem] font-black tracking-wider normal-case ${tone}`;
        tag.dataset.kind = kind;
        tag.textContent = text;
        label.appendChild(tag);
    }

    const sameValue = (a, b) => String(a).toLowerCase() === String(b).toLowerCase();

    /* Disable only what the engine cannot run; if the current value is one of those, fall back silently. */
    function constrainSelect(control, allowed, fallback) {
        if (!control) return;
        const isAllowed = (value) => allowed.some((item) => sameValue(item, value));
        Array.from(control.options).forEach((option) => {
            if (!isAllowed(option.value)) {
                if (option.dataset.spDisabled !== '1') {
                    option.dataset.spDisabled = '1';
                    option.dataset.spWasDisabled = option.disabled ? '1' : '0';
                }
                option.disabled = true;
            } else if (option.dataset.spDisabled === '1') {
                option.disabled = option.dataset.spWasDisabled === '1';
                delete option.dataset.spDisabled;
                delete option.dataset.spWasDisabled;
            }
        });
        if (!isAllowed(control.value)) {
            const target = Array.from(control.options).find((option) => isAllowed(option.value) && sameValue(option.value, fallback))
                || Array.from(control.options).find((option) => isAllowed(option.value));
            if (target) setSelectValue(control, target.value);
        }
    }

    /* What the chosen strategy AND sub-option define (variant values, else the strategy's own). */
    function defs() {
        const preset = selectedPreset();
        const variant = selectedVariant() || {};
        if (!preset) return null;
        const timeframes = variant.supported_timeframes || preset.supported_timeframes || [];
        return {
            timeframes,
            sessions: variant.supported_sessions || preset.supported_sessions || ['Regular'],
            defaultTf: variant.default_timeframe || preset.default_timeframe || timeframes[0],
            sizing: variant.suggested_sizing || preset.suggested_sizing || {},
            scanPolicy: variant.scan_policy || preset.scan_policy || {},
        };
    }

    function applyConstraints() {
        const preset = selectedPreset();
        if (!preset) return;
        const d = defs();
        constrainSelect(el(IDS.timeframe), preset.allowed_timeframes || d.timeframes, d.defaultTf);
        constrainSelect(el(IDS.session), preset.allowed_sessions || d.sessions, d.sessions[0]);
    }

    function amountControl() {
        for (const id of AMOUNT_IDS) { if (el(id)) return el(id); }
        return null;
    }

    function setAmount(control, value) {
        if (!control || value == null) return;
        control.value = String(value);
        state.internal = true;
        try {
            control.dispatchEvent(new Event('input', { bubbles: true }));
            control.dispatchEvent(new Event('change', { bubbles: true }));
        } finally { state.internal = false; }
    }

    /* Picking a strategy from the dropdown fills the fields the strategy defines. */
    function applyRecommended() {
        const preset = selectedPreset();
        if (!preset) return;
        const d = defs();
        setSelectValue(el(IDS.timeframe), d.defaultTf);
        setSelectValue(el(IDS.session), d.sessions[0]);
        const sizing = d.sizing;
        if (sizing.allocation_mode) setSelectValue(el(IDS.allocationMode), sizing.allocation_mode);
        if (sizing.trade_amount != null) setAmount(amountControl(), sizing.trade_amount);
    }

    /* Purple "Set by strategy" while a field holds what the strategy defines; amber "Manual" once the user differs. */
    function refreshBadges() {
        const preset = state.enabled ? selectedPreset() : null;
        const d = preset ? defs() : null;
        const fields = [
            [el(IDS.timeframe), d && d.timeframes, (v) => String(v), (list) => list.join(' / ')],
            [el(IDS.session), d && d.sessions, (v) => String(v), (list) => list.join(' / ')],
        ];
        fields.forEach(([control, defined, norm, pretty]) => {
            if (!control) return;
            if (!preset || !defined || !defined.length) { badge(control, null); return; }
            const same = defined.some((item) => sameValue(item, norm(control.value)));
            badge(control, same ? 'set' : 'manual', same ? 'Set by strategy' : `Manual \u00b7 strategy: ${pretty(defined)}`);
        });
        const sizing = (d && d.sizing) || {};
        const mode = el(IDS.allocationMode);
        if (mode) {
            if (!sizing.allocation_mode) badge(mode, null);
            else if (sameValue(mode.value, sizing.allocation_mode)) badge(mode, 'set', 'Set by strategy');
            else badge(mode, 'manual', `Manual \u00b7 strategy: ${sizing.allocation_mode === 'compound' ? 'Compound' : 'Fixed'}`);
        }
        const amount = amountControl();
        if (amount) {
            if (sizing.trade_amount == null) badge(amount, null);
            else if (Number(amount.value) === Number(sizing.trade_amount)) badge(amount, 'set', 'Set by strategy');
            else badge(amount, 'manual', `Manual \u00b7 strategy: $${sizing.trade_amount}`);
        }
    }

    /* Re-evaluate labels and the "untested" warning when the user edits a strategy-related field. */
    function onUserFieldChange(event) {
        if (state.internal || !state.enabled) return;
        const id = event.target && event.target.id;
        if (id === IDS.timeframe || id === IDS.session) { render(); return; }
        if (id === IDS.allocationMode || AMOUNT_IDS.includes(id)) { refreshBadges(); refreshAllocationPlan(); refreshSchedulePlan(); }
    }

    /* Bots only: the scan timing the strategy imposes (the Backtest has no scan refresh, the Screener has its own). */
    function scanTimingNote() {
        const d = defs();
        if (state.page !== 'bots' || !d || d.scanPolicy.mode !== 'per_candle') return '';
        const timeframe = el(IDS.timeframe) ? el(IDS.timeframe).value : '';
        if ((d.scanPolicy.timeframes || []).includes(timeframe)) {
            return '<p>Scan timing is set by this strategy: while flat, the bot evaluates once per completed daily candle instead of every refresh. An open position or pending order is still watched at the normal refresh while the market is open.</p>';
        }
        return `<p class="text-amber-600">This strategy sets once-per-candle scanning for ${esc((d.scanPolicy.timeframes || []).join(', '))} only; with ${esc(timeframe || 'this timeframe')} the normal refresh is used.</p>`;
    }

    const isAllocation = (preset) => !!(preset && preset.kind === 'allocation' && preset.allocation);
    const isSchedule = (preset) => !!(preset && preset.kind === 'schedule' && preset.schedule);
    const HHMM = /^\d{2}:\d{2}$/;

    function scheduleDefaults(preset) {
        const c = preset.schedule;
        state.sched = { buy: c.default_buy || 'close-5', sell: c.default_sell || 'open+5', buyCustom: false, sellCustom: false,
            skipWeekend: false, lossPause: c.loss_pause_default || 3 };
    }

    /* Minutes after midnight ET on a regular 9:30-16:00 day. */
    function nominalMinutes(v) {
        const s = String(v || '');
        let m = /^open\+(\d)$/.exec(s);
        if (m) return 570 + Number(m[1]);
        m = /^close-(\d)$/.exec(s);
        if (m) return 960 - Number(m[1]);
        m = /^(\d{2}):(\d{2})$/.exec(s);
        return m ? Number(m[1]) * 60 + Number(m[2]) : null;
    }

    function clockLabel(minutes, offset) {
        const t = minutes - (offset || 0);
        const h = Math.floor(t / 60), mm = t % 60;
        return `${((h + 11) % 12) + 1}:${String(mm).padStart(2, '0')} ${h >= 12 ? 'PM' : 'AM'}`;
    }

    /* Custom times are typed in Central time (what the user sees) and stored in Eastern time (what the server uses). */
    function shiftHHMM(value, minutes) {
        const m = /^(\d{2}):(\d{2})$/.exec(String(value || ''));
        if (!m) return value;
        const t = (((Number(m[1]) * 60 + Number(m[2]) + minutes) % 1440) + 1440) % 1440;
        return `${String(Math.floor(t / 60)).padStart(2, '0')}:${String(t % 60).padStart(2, '0')}`;
    }
    const etToCtInput = (value) => shiftHHMM(value, -60);
    const ctToEtInput = (value) => shiftHHMM(value, 60);

    function etCt(v) {
        const m = nominalMinutes(v);
        return m == null ? '' : `${clockLabel(m, 60)} CT`;
    }

    function scheduleMode() {
        const b = nominalMinutes(state.sched.buy), s = nominalMinutes(state.sched.sell);
        if (b == null || s == null || b === s) return null;
        return b < s ? 'intraday' : 'overnight';
    }

    function scheduleTimeBox(preset, side) {
        const c = preset.schedule;
        const value = state.sched[side];
        const known = c.time_options.map((o) => o.value);
        const custom = state.sched[`${side}Custom`] || !known.includes(value);
        const label = side === 'buy' ? 'Buy time' : 'Sell time';
        const options = c.time_options.map((o) => `<option value="${esc(o.value)}"${!custom && o.value === value ? ' selected' : ''}>${esc(o.label)}${o.tight ? ' · tighter timing' : ''}</option>`).join('')
            + `<option value="custom"${custom ? ' selected' : ''}>Custom time…</option>`;
        const customValue = custom && HHMM.test(value) ? value : (side === 'buy' ? '15:50' : '09:40');
        const tight = (c.time_options.find((o) => o.value === value) || {}).tight;
        return `
            <div class="space-y-1.5 min-w-0">
                <label for="strategySched_${side}_regular" class="block text-[0.6rem] font-black text-indigo-400 uppercase tracking-widest">${label}</label>
                <div class="relative"><select id="strategySched_${side}_regular" data-sp="sr-${side}" class="${SELECT_CLASS}">${options}</select>${CHEVRON}</div>
                ${custom ? `<div class="flex flex-wrap items-center gap-2">
                    <input id="strategySchedCustom_${side}_regular" data-sp="sr-${side}-custom" type="time" min="${esc(etToCtInput(c.custom_earliest))}" max="${esc(etToCtInput(c.custom_latest))}" step="60" value="${esc(etToCtInput(customValue))}"
                        aria-label="Custom ${label.toLowerCase()}, Central Time" class="min-h-[44px] w-36 bg-white/60 border border-white/50 rounded-2xl px-3 text-xs font-black text-indigo-950 focus:border-purple-400/80 focus:ring-2 focus:ring-purple-400/20 outline-none">
                    <span id="strategySchedEcho_${side}_regular" class="text-[0.6rem] font-black text-indigo-500">= ${esc(etCt(customValue))}</span>
                </div>
                <p class="text-[0.58rem] font-bold text-indigo-400">Central Time, ${esc(clockLabel(nominalMinutes(c.custom_earliest), 60))} to ${esc(clockLabel(nominalMinutes(c.custom_latest), 60))}, any minute.</p>` : ''}
                ${tight ? '<p class="text-[0.58rem] font-bold text-amber-600">Tighter timing: more missed buys and wider spreads in the first and last minutes.</p>' : ''}
            </div>`;
    }

    function scheduleHtml(preset) {
        const c = preset.schedule;
        const mode = scheduleMode();
        const modeLine = mode === 'overnight'
            ? 'Mode: <b>Overnight</b> · buys at the Buy time and sells at the Sell time of the next trading day.'
            : mode === 'intraday'
                ? 'Mode: <b>Intraday</b> · buys and sells the same day; flat overnight.'
                : '<span class="text-rose-600">Buy time and Sell time must be different.</span>';
        return `
            <div class="grid grid-cols-1 sm:grid-cols-2 gap-3">
                ${scheduleTimeBox(preset, 'buy')}
                ${scheduleTimeBox(preset, 'sell')}
            </div>
            <div class="grid grid-cols-1 sm:grid-cols-2 gap-3 items-start">
                <label class="flex items-start gap-2 min-h-[44px] cursor-pointer">
                    <input id="strategySchedWeekend_regular" data-sp="sr-weekend" type="checkbox" class="mt-1 h-4 w-4 accent-purple-600"${state.sched.skipWeekend ? ' checked' : ''}>
                    <span class="text-[0.62rem] font-bold text-indigo-700 leading-snug">Skip buys before weekends and holidays<br><span class="text-indigo-400">Overnight mode: avoids holding over 2+ day gaps.</span></span>
                </label>
            </div>
            <div class="text-[0.62rem] sm:text-[0.68rem] font-bold leading-snug text-indigo-700 space-y-1">
                <p>${modeLine}</p>
            </div>`;
    }

    function schedulePlanHtml(preset) {
        const mode = scheduleMode();
        const control = amountControl();
        const amount = control ? Number(String(control.value).replace(/[^0-9.]/g, '')) : 0;
        const amountText = amount > 0 ? `$${amount.toLocaleString(undefined, { maximumFractionDigits: 2 })}` : 'your Amount';
        const buy = etCt(state.sched.buy), sell = etCt(state.sched.sell);
        const holdText = mode === 'overnight'
            ? `Buys ${amountText} at ${buy} and sells it all at ${sell} the next trading day${state.sched.skipWeekend ? ' (no buy before a weekend or holiday)' : ', including over weekends and holidays'}.`
            : mode === 'intraday' ? `Buys ${amountText} at ${buy} and sells it all at ${sell} the same day.` : 'Pick two different times.';
        const pageNote = {
            bots: 'Market orders in regular hours (paper by default). A buy happens only in the 5 minutes after the Buy time; a missed window is skipped, never bought late. A missed sell is done as soon as possible. The sell depends on this app running; there is no broker stop order.',
            screener: 'Alerts at the Buy time and the Sell time (also before the open). The badge shows where the schedule is, not your positions.',
            backtest: 'Fills at the open of the 5-minute bar at each time (1-minute bars for other minutes; Alpaca data from 2016). Compared with buy-and-hold and its overnight/intraday parts. No fees, slippage or dividends in the headline; a cost table is shown separately.',
        }[state.page] || '';
        return `
            <div id="strategySchedPlan_regular" class="sp-definition space-y-1 text-[0.62rem] sm:text-[0.68rem] font-bold leading-snug text-indigo-700 break-words">
                <p><span class="text-indigo-400 uppercase tracking-wider">What it does:</span> ${esc(holdText)}</p>
                <p><span class="text-indigo-400 uppercase tracking-wider">Exits:</span> No stop-loss, take-profit or trailing exits; Exit controls are locked. Overnight gaps are fully exposed.</p>
                <p>${esc(pageNote)}</p>
                <p class="text-amber-600">Untested strategy: the first and last minutes have wider spreads, and Backtest results include no costs. Check the Backtest cost table before relying on it.</p>
            </div>`;
    }

    function refreshSchedulePlan() {
        const box = el('strategySchedPlan_regular');
        if (!box || !isSchedule(selectedPreset())) return;
        box.outerHTML = schedulePlanHtml(selectedPreset());
    }

    function allocationDefaults(preset) {
        const a = preset.allocation;
        state.alloc = {
            bull: (a.bull_options[0] || {}).symbol || '',
            bear: (a.bear_options[0] || {}).symbol || '',
            exec: a.default_exec_time || 'close-15',
            custom: false,
        };
    }

    function allocationHtml(preset) {
        const a = preset.allocation;
        const inverse = state.variantId === 'long_inverse';
        const option = (o, selected) => `<option value="${esc(o.symbol)}"${o.symbol === selected ? ' selected' : ''}>${esc(o.label)}</option>`;
        const knownTimes = a.exec_time_options.map((o) => o.value);
        const custom = state.alloc.custom || !knownTimes.includes(state.alloc.exec);
        const timeOptions = a.exec_time_options.map((o) => `<option value="${esc(o.value)}"${!custom && o.value === state.alloc.exec ? ' selected' : ''}>${esc(o.label)}</option>`).join('')
            + `<option value="custom"${custom ? ' selected' : ''}>Custom time…</option>`;
        const customValue = custom && /^\d{2}:\d{2}$/.test(state.alloc.exec) ? state.alloc.exec : '14:30';
        const tickerField = state.page === 'backtest' ? document.getElementById('btTicker') : null;
        const ticker = String((tickerField ? tickerField.value : window.currentStrategySymbol) || '').trim().toUpperCase();
        const tickerWarning = ticker && ticker !== a.signal
            ? `<p class="text-amber-600">This strategy reads ${esc(a.signal)}. ${state.page === 'backtest' ? 'Backtest' : 'Create the bot on'} ${esc(a.signal)} and pick the ETF here; it will not run on ${esc(ticker)}.</p>` : '';
        return `
            <div class="grid grid-cols-1 sm:grid-cols-2 gap-3">
                <div class="space-y-1.5">
                    <label for="strategyAllocBull_regular" class="block text-[0.6rem] font-black text-indigo-400 uppercase tracking-widest">Bull ETF (bought in uptrends)</label>
                    <div class="relative"><select id="strategyAllocBull_regular" data-sp="alloc-bull" class="${SELECT_CLASS}">${a.bull_options.map((o) => option(o, state.alloc.bull)).join('')}</select>${CHEVRON}</div>
                </div>
                ${inverse ? `<div class="space-y-1.5">
                    <label for="strategyAllocBear_regular" class="block text-[0.6rem] font-black text-indigo-400 uppercase tracking-widest">Inverse ETF (bought in bear regimes)</label>
                    <div class="relative"><select id="strategyAllocBear_regular" data-sp="alloc-bear" class="${SELECT_CLASS}">${a.bear_options.map((o) => option(o, state.alloc.bear)).join('')}</select>${CHEVRON}</div>
                </div>` : ''}
                <div class="space-y-1.5 ${inverse ? 'sm:col-span-2' : ''}">
                    <label for="strategyAllocExec_regular" class="block text-[0.6rem] font-black text-indigo-400 uppercase tracking-widest">Execution time · T = market close</label>
                    <div class="flex flex-col sm:flex-row gap-2">
                        <div class="relative flex-1 min-w-0"><select id="strategyAllocExec_regular" data-sp="alloc-exec" class="${SELECT_CLASS}">${timeOptions}</select>${CHEVRON}</div>
                        ${custom ? `<input id="strategyAllocExecCustom_regular" data-sp="alloc-exec-custom" type="time" min="08:35" max="14:55" step="60" value="${esc(etToCtInput(customValue))}"
                            aria-label="Custom execution time, Central Time" class="min-h-[44px] sm:w-36 bg-white/60 border border-white/50 rounded-2xl px-3 text-xs font-black text-indigo-950 focus:border-purple-400/80 focus:ring-2 focus:ring-purple-400/20 outline-none">` : ''}
                    </div>
                    <p class="text-[0.58rem] font-bold text-indigo-400">${custom
                        ? 'Custom time in Central Time, between 8:35 AM and 2:55 PM. On early-close days it runs at T-15.'
                        : 'Runs once a day. T-15 = 15 minutes before the close (recommended). On early-close days T-N moves with the close (T-15 = 11:45 AM CT).'}</p>
                </div>
            </div>
            ${tickerWarning ? `<div class="text-[0.62rem] sm:text-[0.68rem] font-bold leading-snug">${tickerWarning}</div>` : ''}`;
    }

    /* What the strategy holds in each regime, in % and in dollars of the Amount field, and when it buys or sells. */
    function allocationPlanHtml(preset) {
        const a = preset.allocation;
        const inverse = state.variantId === 'long_inverse';
        const bull = state.alloc.bull || (a.bull_options[0] || {}).symbol || 'bull ETF';
        const bear = state.alloc.bear || (a.bear_options[0] || {}).symbol || 'inverse ETF';
        const control = amountControl();
        const amount = Math.max(0, Number(control && control.value) || 0);
        const compound = el(IDS.allocationMode) && el(IDS.allocationMode).value === 'compound';
        const usd = (pct) => (amount > 0 ? `$${Math.round(amount * pct / 100).toLocaleString()}` : '');
        const band = Number(a.rebalance_band_pct || 5);
        const rows = (a.regimes || []).map((r) => {
            const bearPct = inverse ? r.bear_pct : 0;
            const holds = r.bull_pct > 0 ? `${r.bull_pct}% ${bull}` : (bearPct > 0 ? `${bearPct}% ${bear}` : '0% \u00b7 cash');
            const pct = r.bull_pct || bearPct;
            const cashPct = 100 - pct;
            const tone = r.bull_pct > 0 ? 'text-emerald-700 bg-emerald-50 border-emerald-200'
                : (bearPct > 0 ? 'text-rose-700 bg-rose-50 border-rose-200' : 'text-slate-600 bg-slate-50 border-slate-200');
            return `<div class="py-1.5 border-b border-indigo-100/70 last:border-0 min-w-0">
                <div class="flex flex-wrap items-center gap-x-2 gap-y-1">
                    <span class="font-black text-indigo-900">${esc(r.label)}</span>
                    <span class="px-1.5 py-0.5 rounded-md border ${tone} font-black">${esc(holds)}${amount > 0 && pct > 0 ? ` \u00b7 ${esc(usd(pct))}` : ''}</span>
                    ${pct > 0 && cashPct > 0 ? `<span class="text-indigo-400">rest in cash${amount > 0 ? ` (${esc(usd(cashPct))})` : ''}</span>` : ''}
                </div>
                <p class="text-indigo-500 font-semibold">${esc(r.condition)}</p>
            </div>`;
        }).join('');
        const ex = (from, to) => (amount > 0 ? ` (${usd(from - to > 0 ? from - to : to - from)} of ${usd(100)})` : '');
        const rules = [
            `<b>Buys</b> when the target goes up, e.g. Cash \u2192 Bull rider buys 100% ${esc(bull)}${esc(ex(0, 100))}; Defensive 20% \u2192 Consolidation 50% buys 30% more${esc(ex(20, 50))}.`,
            `<b>Sells</b> when the target goes down, e.g. Bull rider 100% \u2192 Momentum trim 30% sells 70%${esc(ex(100, 30))} and keeps 30%; a 0% target sells the whole ${esc(bull)} position.`,
            `<b>Holds</b> when a leg is within ${band}% of its target${amount > 0 ? ` (${esc(usd(band))})` : ''}, so small price moves don't trade. Bigger drift is trimmed or topped up once a day.`,
            compound
                ? '<b>Compound:</b> the % is of your amount plus all profit or loss so far, so gains stay invested.'
                : `<b>Fixed:</b> the % is of your amount${amount > 0 ? ` (${esc(usd(100))})` : ''}; gains above it are sold back to the target, so exposure stays the same size.`,
        ];
        if (inverse) rules.push(`<b>Switching to bear:</b> it sells all ${esc(bull)} first, then buys ${esc(bear)} once the sale has filled.`);
        return `<div id="strategyAllocPlan_regular" class="rounded-2xl border border-indigo-100 bg-white/60 px-3 py-2 text-[0.6rem] sm:text-[0.66rem] leading-snug break-words space-y-1.5">
            <p class="font-black text-indigo-400 uppercase tracking-widest text-[0.55rem]">What it holds, buys and sells${amount > 0 ? ` \u00b7 % of ${esc(usd(100))}` : ''}</p>
            <div>${rows}</div>
            <ul class="space-y-1 text-indigo-700 font-semibold list-disc pl-4">${rules.map((r) => `<li>${r}</li>`).join('')}</ul>
        </div>`;
    }

    function refreshAllocationPlan() {
        const preset = selectedPreset();
        const box = el('strategyAllocPlan_regular');
        if (!box || !isAllocation(preset)) return;
        box.outerHTML = allocationPlanHtml(preset);
    }

    function allocationDefinitionHtml(preset, variant) {
        const a = preset.allocation;
        const r = a.research || {};
        const pageNote = {
            bots: 'Bots rebalance the chosen ETF once a day at the execution time with market orders (paper by default), sells before buys. There are no stop orders: it depends on the app running at that time. Sizing (Amount, Fixed/Compound) and Max Position Size still apply.',
            screener: 'The Screener alerts when the confirmed regime changes (from completed daily closes). During the session it also shows the regime the live price points to. It never places orders.',
            backtest: 'Backtest decides at the execution time from completed daily closes plus the price at that time, fills at the next 15-minute bar, and compares against buy-and-hold and a simple 250-day rule. No fees, slippage or dividends.',
        }[state.page] || '';
        return `
            <p><span class="text-indigo-400 uppercase tracking-wider">Reads:</span> ${esc(a.signal)} (completed daily closes plus the current price). <span class="text-indigo-400 uppercase tracking-wider">Trades:</span> the ETF(s) above.</p>
            <p><span class="text-indigo-400 uppercase tracking-wider">Decides:</span> ${esc(preset.entry_rule)}</p>
            <p><span class="text-indigo-400 uppercase tracking-wider">Reduces:</span> ${esc(preset.exit_rule)}</p>
            <p>${esc(variant.description)} ${esc(pageNote)}</p>
            <p class="text-amber-600">${esc(a.research_note)}${a.research_report_url ? ` <a href="${esc(a.research_report_url)}" target="_blank" rel="noopener" class="underline">Research report</a>` : ''}</p>`;
    }

    function untestedWarning() {
        const d = defs();
        if (!d) return '';
        const timeframe = el(IDS.timeframe) ? el(IDS.timeframe).value : '';
        const session = el(IDS.session) ? el(IDS.session).value : '';
        const notes = [];
        if (timeframe && !d.timeframes.includes(timeframe)) notes.push(`timeframe ${timeframe} (designed for ${d.timeframes.join(', ')})`);
        if (session && !d.sessions.some((item) => sameValue(item, session))) notes.push(`${session} session (designed for ${d.sessions.join(', ')})`);
        return notes.length ? `Not what this strategy was designed for: ${notes.join('; ')}. It still runs, but its behaviour and results there are untested.` : '';
    }

    function releaseConstraints() {
        [IDS.timeframe, IDS.session].forEach((id) => {
            const control = el(id);
            if (!control) return;
            badge(control, null);
            Array.from(control.options).forEach((option) => {
                if (option.dataset.spDisabled === '1') {
                    option.disabled = option.dataset.spWasDisabled === '1';
                    delete option.dataset.spDisabled;
                    delete option.dataset.spWasDisabled;
                }
            });
        });
    }

    /* ── Rendering ───────────────────────────────────────────────────────── */
    function presetOptionsHtml() {
        const families = {};
        state.catalog.forEach((preset) => { (families[preset.family] = families[preset.family] || []).push(preset); });
        return Object.keys(families).map((family) => (
            `<optgroup label="${esc(family)}">${families[family].map((p) => (
                `<option value="${esc(p.id)}"${p.id === state.presetId ? ' selected' : ''}>${esc(p.label)}</option>`
            )).join('')}</optgroup>`
        )).join('');
    }

    function render() {
        const root = state.root;
        if (!root) return;
        const loading = !state.catalog && !state.loadError;
        const unavailable = !!state.loadError;
        const preset = selectedPreset();
        const variant = selectedVariant();
        const toggleDisabled = unavailable || loading;
        const switchOn = state.enabled && !toggleDisabled;
        let body = '';
        if (unavailable) {
            body = `<div class="flex flex-wrap items-center gap-2"><p class="text-[0.62rem] sm:text-[0.68rem] font-bold text-rose-600 break-words">${esc(state.loadError)}</p>
                <button type="button" data-sp="retry" class="min-h-[36px] px-3 rounded-xl bg-white/70 border border-indigo-200 text-indigo-700 text-[0.6rem] font-black uppercase tracking-widest">Retry</button></div>`;
        } else if (state.enabled && preset && variant) {
            const policy = state.page === 'bots' ? `
                <div class="space-y-1.5">
                    <label for="strategyPresetPolicy_regular" class="block text-[0.6rem] font-black text-indigo-400 uppercase tracking-widest">${isAllocation(preset) ? 'If you already hold the ETF' : 'If this ticker already has a position'}</label>
                    <div class="relative"><select id="strategyPresetPolicy_regular" data-sp="policy" class="${SELECT_CLASS}">
                        <option value="unmanaged"${state.policy === 'unmanaged' ? ' selected' : ''}>Leave it alone · Recommended</option>
                        <option value="adopt"${state.policy === 'adopt' ? ' selected' : ''}>${isAllocation(preset) ? 'Adopt it (the strategy rebalances it)' : isSchedule(preset) ? 'Adopt it (sold at the next Sell time)' : 'Adopt it (strategy exits apply)'}</option>
                    </select>${CHEVRON}</div>
                </div>` : '';
            body = `
                <div class="grid grid-cols-1 sm:grid-cols-2 gap-3">
                    <div class="space-y-1.5">
                        <label for="strategyPresetSelect_regular" class="block text-[0.6rem] font-black text-indigo-400 uppercase tracking-widest">Strategy</label>
                        <div class="relative"><select id="strategyPresetSelect_regular" data-sp="preset" class="${SELECT_CLASS}">${presetOptionsHtml()}</select>${CHEVRON}</div>
                    </div>
                    ${isSchedule(preset) ? '' : `<div class="space-y-1.5">
                        <label for="strategyPresetVariant_regular" class="block text-[0.6rem] font-black text-indigo-400 uppercase tracking-widest">${isAllocation(preset) ? 'Direction' : 'Variant'}</label>
                        <div class="relative"><select id="strategyPresetVariant_regular" data-sp="variant" class="${SELECT_CLASS}">${preset.variants.map((v) => `<option value="${esc(v.id)}"${v.id === variant.id ? ' selected' : ''}>${esc(v.label)}</option>`).join('')}</select>${CHEVRON}</div>
                    </div>`}
                </div>
                ${isAllocation(preset) ? allocationHtml(preset) + allocationPlanHtml(preset) : ''}
                ${isSchedule(preset) ? scheduleHtml(preset) : ''}
                ${policy}
                ${isSchedule(preset) ? schedulePlanHtml(preset) : isAllocation(preset) ? `<div class="sp-definition space-y-1 text-[0.62rem] sm:text-[0.68rem] font-bold leading-snug text-indigo-700 break-words">${allocationDefinitionHtml(preset, variant)}</div>` : `
                <div class="sp-definition space-y-1 text-[0.62rem] sm:text-[0.68rem] font-bold leading-snug text-indigo-700 break-words">
                    <p><span class="text-indigo-400 uppercase tracking-wider">Buy:</span> ${esc(preset.entry_rule)}</p>
                    <p><span class="text-indigo-400 uppercase tracking-wider">Sell:</span> ${esc(preset.exit_rule)}</p>
                    <p>${esc(variant.description)} Long only. Needs ${esc(variant.warmup_bars)} completed daily bars of history. A signal is decided on a completed candle and executed at the next regular session open.</p>
                    <p>Entry and Exit controls, thresholds and indicator boxes are locked while this strategy is active. Choosing a strategy or sub-option fills the fields it defines (${esc(defs().timeframes.join(', '))} \u00b7 ${esc(defs().sessions.join(', '))} session${Object.keys(defs().sizing).length ? ', sizing' : ''}) and marks them \u201cSet by strategy\u201d. You can change any of them; a changed field turns amber. ${state.page === 'screener' ? '' : 'Cooldown, Signal Reset and Smart Re-entry are switched off while a strategy is active.'} Sizing and Risk Management remain your own.</p>
                    ${scanTimingNote()}
                    ${untestedWarning() ? `<p class="text-amber-600">${esc(untestedWarning())}</p>` : ''}
                </div>`}`;
        } else if (!loading) {
            body = '<p class="sp-definition text-[0.62rem] sm:text-[0.68rem] font-bold text-indigo-500">Turn on to trade a predefined strategy instead of choosing Entry and Exit settings yourself.</p>';
        }
        root.innerHTML = `
            <div class="rounded-2xl border border-white/40 bg-white/20 backdrop-blur-md p-3 sm:p-4 space-y-3 shadow-sm">
                <div class="flex items-center justify-between gap-3">
                    <div class="flex items-center gap-3 min-w-0">
                        <div class="h-5 w-1.5 rounded-full bg-gradient-to-b from-indigo-500 to-purple-600 flex-shrink-0"></div>
                        <label id="strategyPresetTitle_regular" class="text-sm font-black text-indigo-900 uppercase tracking-widest settings-section-title">Strategy Presets</label>
                    </div>
                    <button type="button" id="strategyPresetToggle_regular" data-sp="toggle" role="switch" aria-checked="${switchOn}" aria-labelledby="strategyPresetTitle_regular"
                        ${toggleDisabled ? 'disabled' : ''} class="relative flex items-center justify-end min-h-[44px] min-w-[56px] -my-4 flex-shrink-0 disabled:opacity-50 focus:outline-none">
                        <span class="relative inline-flex h-7 w-12 items-center rounded-full transition-colors ${switchOn ? 'bg-purple-600' : 'bg-indigo-200'}">
                            <span class="inline-block h-5 w-5 rounded-full bg-white shadow transform transition-transform ${switchOn ? 'translate-x-6' : 'translate-x-1'}"></span>
                        </span>
                    </button>
                </div>
                <p class="text-[0.6rem] font-black text-indigo-500 leading-snug">${esc((state.enabled && isAllocation(preset) ? ALLOCATION_CAPTIONS[state.page] : state.enabled && isSchedule(preset) ? SCHEDULE_CAPTIONS[state.page] : CAPTIONS[state.page]) || CAPTIONS.bots)}</p>
                ${loading ? '<p class="text-[0.62rem] font-bold text-indigo-400">Loading strategies…</p>' : ''}
                ${body}
            </div>`;
        refreshBadges();
        syncManualBox();
    }

    /* ── Manual Entry / Exit box ─────────────────────────────────────────────
     * The user's own indicators, Entry and Exit rules live in one sub-box with its own
     * switch. It is the opposite of the Strategy switch: turning one on turns the other off, so exactly one of
     * the two is always in charge. The sections are moved, not copied, so every id and handler keeps working. */
    function mountManualBox() {
        if (el(MANUAL_BOX_ID) || !state.root || !state.root.parentElement) return;
        const kids = Array.from(state.root.parentElement.children).filter((node) => node !== state.root);
        const holds = (node, ids) => ids.some((id) => node.id === id || node.querySelector('#' + id));
        const find = (ids) => kids.find((node) => holds(node, ids));
        const sections = [
            find(['strategyIndicatorSelectedCount_regular', 'indicatorChecksContainer_regular', 'indicatorChecksContainer_bt_regular']),
            find([IDS.buyMode]),
            find([IDS.sellMode]),
        ].filter((node, index, all) => node && all.indexOf(node) === index);
        if (sections.length < 3) return;
        const box = document.createElement('section');
        box.id = MANUAL_BOX_ID;
        box.className = 'rounded-2xl border border-white/40 bg-white/20 backdrop-blur-md p-3 sm:p-4 space-y-3 shadow-sm';
        box.innerHTML = `
            <div class="flex items-center justify-between gap-3">
                <div class="flex items-center gap-3 min-w-0">
                    <div class="h-5 w-1.5 rounded-full bg-gradient-to-b from-emerald-400 to-indigo-500 flex-shrink-0"></div>
                    <label id="manualStrategyTitle_regular" class="text-sm font-black text-indigo-900 uppercase tracking-widest settings-section-title">Entry / Exit Strategy</label>
                </div>
                <button type="button" id="manualStrategyToggle_regular" role="switch" aria-labelledby="manualStrategyTitle_regular"
                    class="relative flex items-center justify-end min-h-[44px] min-w-[56px] -my-4 flex-shrink-0 disabled:opacity-50 focus:outline-none">
                    <span data-manual="track" class="relative inline-flex h-7 w-12 items-center rounded-full transition-colors bg-indigo-200">
                        <span data-manual="knob" class="inline-block h-5 w-5 rounded-full bg-white shadow transform transition-transform translate-x-1"></span>
                    </span>
                </button>
            </div>
            <p id="manualStrategyCaption_regular" class="text-[0.6rem] font-black text-indigo-500 leading-snug"></p>
            <div id="manualStrategyBody_regular" class="space-y-4"></div>`;
        box.classList.add('transition-all');
        // Screener: Event-Driven sits between Strategy Presets and Entry / Exit Strategy.
        const anchor = (state.page === 'screener' && el('screenerEventBox_regular')) || state.root;
        anchor.insertAdjacentElement('afterend', box);
        const body = box.querySelector('#manualStrategyBody_regular');
        sections.forEach((node) => body.appendChild(node));
        box.querySelector('#manualStrategyToggle_regular').addEventListener('click', onManualToggle);
    }

    function onManualToggle() {
        const toggle = el('manualStrategyToggle_regular');
        if (!toggle || toggle.disabled) return;
        if (state.page === 'screener') {
            // Three independent alert sources: this switch never hands control to the Strategy.
            if (state.enabled) { setEnabled(false); setManualOff(false); }
            else setManualOff(!state.manualOff);
            return;
        }
        if (state.enabled) setEnabled(false);      // your own rules on: the Strategy turns off
        else if (state.catalog) setEnabled(true);  // your own rules off: the Strategy takes over
    }

    function syncManualBox() {
        const toggle = el('manualStrategyToggle_regular');
        if (!toggle) return;
        const on = !state.enabled && !state.manualOff;
        toggle.setAttribute('aria-checked', String(on));
        // Switching your own rules off hands control to the Strategy, which needs its catalog.
        toggle.disabled = on && !state.catalog && state.page !== 'screener';
        toggle.title = toggle.disabled ? 'Strategies are still loading or unavailable.' : '';
        const track = toggle.querySelector('[data-manual="track"]');
        const knob = toggle.querySelector('[data-manual="knob"]');
        if (track) { track.classList.toggle('bg-purple-600', on); track.classList.toggle('bg-indigo-200', !on); }
        if (knob) { knob.classList.toggle('translate-x-6', on); knob.classList.toggle('translate-x-1', !on); }
        const caption = el('manualStrategyCaption_regular');
        if (caption) {
            caption.textContent = state.page === 'screener'
                ? (on
                    ? 'Alerts come from your own indicators, entry and exit rules. Turning this on switches the other alert sources off.'
                    : 'Turn this on to alert from your own indicators; the other alert sources switch off.')
                : on
                    ? 'Your own indicators, entry and exit rules are in use. Turning this off switches Strategy Presets on.'
                    : 'Collapsed while a Strategy Preset is active. Turn this on to use your own rules; Strategy Presets switch off.';
        }
        // Collapsed when off, expanded when on. A hidden section cannot be focused, clicked or edited.
        const body = el('manualStrategyBody_regular');
        if (body) body.hidden = !on;
        toggle.setAttribute('aria-expanded', String(on));
    }

    /* ── State changes ───────────────────────────────────────────────────── */
    function notify() {
        document.dispatchEvent(new CustomEvent('strategy-preset-change', { detail: collect() }));
        try { window.ScreenerAlertSource?.sync?.(); } catch (e) { /* optional page module */ }
    }

    function clearBadges() {
        [el(IDS.timeframe), el(IDS.session), el(IDS.allocationMode), amountControl()].forEach((control) => badge(control, null));
    }

    function setEnabled(on) {
        if (on === state.enabled) return;
        if (on) {
            if (!state.catalog) return;
            if (!selectedPreset()) { state.presetId = state.catalog[0].id; state.variantId = state.catalog[0].default_variant; }
            if (isAllocation(selectedPreset()) && !state.alloc.bull) allocationDefaults(selectedPreset());
            state.enabled = true;
            state.manualOff = false;
            if (isSchedule(selectedPreset())) applyLock(true);
            applyConstraints();
            applyLock(true);
        } else {
            state.enabled = false;
            releaseConstraints();
            clearBadges();
            applyLock(false);
            FIELD_WRAPPERS.forEach((name) => { try { if (typeof window[name] === 'function') window[name](); } catch (e) { /* page handler optional */ } });
        }
        render();
        notify();
    }

    function onChange(event) {
        const target = event.target.closest('[data-sp]');
        if (!target) return;
        const kind = target.dataset.sp;
        if (kind === 'preset') {
            state.presetId = target.value;
            const preset = selectedPreset();
            state.variantId = preset ? preset.default_variant : '';
            if (isAllocation(preset)) allocationDefaults(preset);
            if (isSchedule(preset)) scheduleDefaults(preset);
            applyRecommended();
            applyConstraints();
            applyLock(true);
            render();
            notify();
        } else if (kind === 'variant') {
            state.variantId = target.value;
            applyRecommended();
            applyConstraints();
            render();
            notify();
        } else if (kind === 'alloc-bull' || kind === 'alloc-bear') {
            state.alloc[kind === 'alloc-bull' ? 'bull' : 'bear'] = target.value;
            refreshAllocationPlan();
            notify();
        } else if (kind === 'alloc-exec') {
            state.alloc.custom = target.value === 'custom';
            state.alloc.exec = state.alloc.custom ? (/^\d{2}:\d{2}$/.test(state.alloc.exec) && state.alloc.exec !== 'close-15' ? state.alloc.exec : '14:30') : target.value;
            render();
            notify();
        } else if (kind === 'alloc-exec-custom') {
            const execEt = ctToEtInput(target.value);
            if (/^\d{2}:\d{2}$/.test(target.value) && execEt >= '09:35' && execEt <= '15:55') {
                state.alloc.exec = execEt;
                target.setCustomValidity('');
            } else {
                target.setCustomValidity('Pick a time between 8:35 AM and 2:55 PM CT.');
                target.reportValidity();
            }
            notify();
        } else if (kind === 'sr-buy' || kind === 'sr-sell') {
            const side = kind === 'sr-buy' ? 'buy' : 'sell';
            state.sched[`${side}Custom`] = target.value === 'custom';
            if (target.value === 'custom') {
                if (!HHMM.test(state.sched[side])) state.sched[side] = side === 'buy' ? '15:50' : '09:40';
            } else {
                state.sched[side] = target.value;
            }
            render();
            notify();
        } else if (kind === 'sr-buy-custom' || kind === 'sr-sell-custom') {
            const side = kind === 'sr-buy-custom' ? 'buy' : 'sell';
            const c = (selectedPreset() || {}).schedule || {};
            const timeEt = ctToEtInput(target.value);
            if (HHMM.test(target.value) && timeEt >= (c.custom_earliest || '09:31') && timeEt <= (c.custom_latest || '15:59')) {
                state.sched[side] = timeEt;
                target.setCustomValidity('');
                render();
            } else {
                target.setCustomValidity(`Pick a time between ${clockLabel(nominalMinutes(c.custom_earliest || '09:31'), 60)} and ${clockLabel(nominalMinutes(c.custom_latest || '15:59'), 60)} CT.`);
                target.reportValidity();
            }
            notify();
        } else if (kind === 'sr-weekend') {
            state.sched.skipWeekend = !!target.checked;
            refreshSchedulePlan();
            notify();
        } else if (kind === 'sr-loss') {
            const c = (selectedPreset() || {}).schedule || {};
            const n = Math.round(Number(target.value));
            if (Number.isFinite(n) && n >= (c.loss_pause_min || 3) && n <= (c.loss_pause_max || 50)) {
                state.sched.lossPause = n;
                target.setCustomValidity('');
            } else {
                target.setCustomValidity(`Use a whole number from ${c.loss_pause_min || 3} to ${c.loss_pause_max || 50}.`);
                target.reportValidity();
            }
            notify();
        } else if (kind === 'policy') {
            if (target.value === 'adopt' && !window.confirm('Adopting an existing position lets the strategy manage it. If the strategy’s exit condition is already true, it will sell that position at the next regular session open. Continue?')) {
                target.value = state.policy;
                return;
            }
            state.policy = target.value;
            notify();
        }
    }

    function onClick(event) {
        if (event.target.closest('[data-sp="retry"]')) { retryNow(); return; }
        const toggle = event.target.closest('[data-sp="toggle"]');
        if (!toggle || toggle.disabled) return;
        if (state.page === 'screener' && state.enabled) {
            // Turning the only active alert source off leaves alerts paused.
            setEnabled(false);
            setManualOff(true);
            return;
        }
        setEnabled(!state.enabled);
    }

    /* Screener only: Indicators off without a Strategy taking over (Event-Driven or paused). */
    function setManualOff(off) {
        if (state.page !== 'screener') return;
        state.manualOff = !!off;
        if (off && state.enabled) setEnabled(false);
        syncManualBox();
        notify();
    }

    /* ── Public API ──────────────────────────────────────────────────────── */
    function collect() {
        if (!state.enabled || !selectedPreset()) {
            return { strategy_preset_enabled: false, strategy_preset: 'none' };
        }
        const payload = {
            strategy_preset_enabled: true,
            strategy_preset: state.presetId,
            strategy_preset_variant: state.variantId,
        };
        if (state.page === 'bots') payload.strategy_preset_position_policy = state.policy;
        if (isAllocation(selectedPreset())) {
            payload.allocation_bull_etf = state.alloc.bull;
            payload.allocation_bear_etf = state.alloc.bear;
            payload.allocation_exec_time = state.alloc.exec || 'close-15';
        }
        if (isSchedule(selectedPreset())) {
            payload.session_buy_time = state.sched.buy;
            payload.session_sell_time = state.sched.sell;
            payload.session_skip_weekend = !!state.sched.skipWeekend;
            payload.session_loss_pause_after = state.sched.lossPause;
        }
        return payload;
    }

    function reset() {
        resetInner();
        try { window.ScreenerAlertSource?.reset?.(); } catch (e) { /* optional page module */ }
    }

    function resetInner() {
        state.manualOff = false;
        if (state.enabled) {
            state.enabled = false;
            releaseConstraints();
            applyLock(false);
        }
        clearBadges();
        state.policy = 'unmanaged';
        state.reentrySaved = {};
        render();
    }

    async function restore(settings) {
        await restoreInner(settings);
        try { window.ScreenerAlertSource?.restore?.(settings); } catch (e) { /* optional page module */ }
    }

    async function restoreInner(settings) {
        await ready;
        resetInner();
        if (!settings || !settings.strategy_preset_enabled || !state.catalog) return;
        const preset = state.catalog.find((p) => p.id === settings.strategy_preset);
        if (!preset) return;
        const backup = settings._strategy_manual_backup || {};
        state.presetId = preset.id;
        state.variantId = (preset.variants.find((v) => v.id === settings.strategy_preset_variant) || {}).id || preset.default_variant;
        state.policy = settings.strategy_preset_position_policy === 'adopt' ? 'adopt' : 'unmanaged';
        if (isAllocation(preset)) {
            allocationDefaults(preset);
            const a = preset.allocation;
            if (a.bull_options.some((o) => o.symbol === settings.allocation_bull_etf)) state.alloc.bull = settings.allocation_bull_etf;
            if (a.bear_options.some((o) => o.symbol === settings.allocation_bear_etf)) state.alloc.bear = settings.allocation_bear_etf;
            if (settings.allocation_exec_time) {
                state.alloc.exec = settings.allocation_exec_time;
                state.alloc.custom = !a.exec_time_options.some((o) => o.value === settings.allocation_exec_time);
            }
        }
        if (isSchedule(preset)) {
            scheduleDefaults(preset);
            const known = preset.schedule.time_options.map((o) => o.value);
            if (settings.session_buy_time) { state.sched.buy = settings.session_buy_time; state.sched.buyCustom = !known.includes(settings.session_buy_time); }
            if (settings.session_sell_time) { state.sched.sell = settings.session_sell_time; state.sched.sellCustom = !known.includes(settings.session_sell_time); }
            state.sched.skipWeekend = !!settings.session_skip_weekend;
            if (settings.session_loss_pause_after) state.sched.lossPause = Number(settings.session_loss_pause_after) || state.sched.lossPause;
        }
        // The saved mode is the strategy sentinel, which is not a selectable option:
        // show the user's own (locked) Entry/Exit choices from the backup instead.
        setSelectValue(el(IDS.buyMode), backup.buy_mode);
        setSelectValue(el(IDS.sellMode), backup.sell_mode);
        if (el(IDS.buyThreshold) && backup.min_buy_signals != null) el(IDS.buyThreshold).value = backup.min_buy_signals;
        if (el(IDS.sellThreshold) && backup.min_sell_signals != null) el(IDS.sellThreshold).value = backup.min_sell_signals;
        state.enabled = true;
        // The user's own re-entry toggles live in the saved backup (the saved settings hold them switched off).
        state.reentrySaved = {};
        REENTRY_TOGGLES.forEach(({ id, backup: key }) => {
            if (backup[key] !== undefined && el(id)) state.reentrySaved[id] = !!backup[key];
        });
        // The saved timeframe and session are the user's own values and stand as loaded.
        applyConstraints();
        applyLock(true);
        render();
    }

    function wrapPageHandlers() {
        FIELD_WRAPPERS.forEach((name) => {
            const original = window[name];
            if (typeof original !== 'function' || original.__strategyWrapped) return;
            const wrapped = function () {
                const result = original.apply(this, arguments);
                if (state.enabled) applyLock(true);
                return result;
            };
            wrapped.__strategyWrapped = true;
            window[name] = wrapped;
        });
        ['openStrategyModal'].forEach((name) => {
            const original = window[name];
            if (typeof original !== 'function' || original.__strategyWrapped || state.page === 'backtest') return;
            const wrapped = async function (symbol, mode) {
                const result = await original.apply(this, arguments);
                try {
                    if (mode === 'edit' && typeof getSavedBotSettings === 'function') await restore(getSavedBotSettings(symbol));
                    else reset();
                } catch (error) {
                    console.warn('[strategy-preset] restore failed:', error && error.message);
                }
                return result;
            };
            wrapped.__strategyWrapped = true;
            window[name] = wrapped;
        });
    }

    async function mount() {
        const root = el(PANEL_ID);
        if (!root) return;
        state.root = root;
        state.page = root.dataset.page || 'bots';
        mountManualBox();
        root.addEventListener('change', onChange);
        document.addEventListener('change', onUserFieldChange);
        document.addEventListener('input', onUserFieldChange);
        root.addEventListener('click', onClick);
        render();
        wrapPageHandlers();
        watchSignIn();
        await loadCatalog();
        if (!state.catalog) {
            // Keep waiting (retries continue in the background) so restore() still works once it loads.
            const wait = setInterval(() => { if (state.catalog) { clearInterval(wait); readyResolve(); render(); } }, 400);
        } else {
            readyResolve();
        }
        render();
    }

    window.StrategyPreset = {
        mount,
        isEnabled: () => state.enabled,
        collect,
        restore,
        reset,
        applyLock,
        ready: () => ready,
        // Read-only definition for display (Screener cards): label, summary, entry/exit rule, variant. null until the catalog loads.
        describe(presetId, variantId) {
            const preset = (state.catalog || []).find((p) => p.id === presetId);
            if (!preset) return null;
            const variant = (preset.variants || []).find((v) => v.id === (variantId || preset.default_variant)) || null;
            return {
                id: preset.id, label: preset.label, summary: preset.summary, kind: preset.kind,
                entry_rule: preset.entry_rule, exit_rule: preset.exit_rule, direction: preset.direction,
                variant: variant ? { id: variant.id, label: variant.label, description: variant.description, warmup_bars: variant.warmup_bars } : null,
                timeframes: (variant && variant.supported_timeframes) || preset.supported_timeframes || [],
                sessions: (variant && variant.supported_sessions) || preset.supported_sessions || [],
            };
        },
        isManualOff: () => state.manualOff,
        setManualOff,
        setEnabled,
    };

    if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', mount);
    else mount();
})();
