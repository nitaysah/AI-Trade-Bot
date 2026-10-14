(function () {
    'use strict';

    const PAGE_CONTEXT = (() => {
        const page = String(window.location?.pathname || '').split('/').pop().toLowerCase();
        if (page === 'screener.html') return 'screener';
        if (page === 'bots.html') return 'bots';
        if (page === 'backtest.html') return 'backtest';
        return 'dashboard';
    })();
    const STORAGE_KEY = `botbullsIndicatorWorkspace:${PAGE_CONTEXT}:v3`;
    const LEGACY_STORAGE_KEY = `botbullsIndicatorWorkspace:${PAGE_CONTEXT}:v2`;
    const DASHBOARD_SHARED_SCOPE_KEY = 'dashboard:GLOBAL';
    const DISPLAY_LIMIT = 15;
    const FALLBACK_INDICATORS = [
        { name: 'RSI', label: 'RSI', category: 'Momentum' },
        { name: 'MACD', label: 'MACD', category: 'Trend' },
        { name: 'EMA Cross', label: 'EMA Cross', category: 'Moving average' },
        { name: 'ADX Trend', label: 'ADX Trend', category: 'Trend strength' },
        { name: 'Supertrend', label: 'Supertrend', category: 'Trend' },
        { name: 'Bollinger', label: 'Bollinger Bands', category: 'Volatility' },
        { name: 'VWAP', label: 'Session VWAP', category: 'Volume / price' },
        { name: 'Volume Profile', label: 'Volume Profile', category: 'Volume / structure' },
        { name: 'SMA', label: 'SMA', category: 'Trend filter' },
        { name: 'Prior Day High/Low', label: 'Prior Day High/Low', category: 'Price structure' },
        { name: 'Pivot Points (S/R)', label: 'Pivot Points (S/R)', category: 'Price structure' },
        { name: 'Swing Points', label: 'Swing Points', category: 'Price structure' },
        { name: 'Mystic Pulse', label: 'Mystic Pulse', category: 'Custom momentum' },
        { name: 'Candle Patterns', label: 'Candle Patterns', category: 'Price action' },
        { name: 'Chart Patterns', label: 'Chart Patterns', category: 'Price action' },
        { name: 'Trend Rider', label: 'Trend Rider', category: 'Trend' },
        { name: 'HA+UT', label: 'HA+UT', category: 'Trend' },
        { name: 'Auto-AVWAP', label: 'Auto-AVWAP', category: 'Volume / price' },
    ];
    const INDICATORS = Array.isArray(window.STANDARD_INDICATOR_CATALOG)
        && window.STANDARD_INDICATOR_CATALOG.length === 54
        ? window.STANDARD_INDICATOR_CATALOG
        : FALLBACK_INDICATORS;
    // Stable popularity order keeps the selector readable and prevents an
    // indicator from jumping to a different row when it is toggled off/on.
    // The remaining catalog entries retain their canonical catalog order.
    const POPULAR_INDICATOR_NAMES = [
        'RSI',
        'EMA Cross',
        'SMA',
        'VWAP',
        'ADX Trend',
        'Supertrend',
        'ATR Volatility',
        'Volume Profile',
        'Candle Patterns',
        'Chart Patterns',
        'Prior Day High/Low',
        'Swing Points',
        'Pivot Points (S/R)',
        'MACD',
        'Bollinger',
        'Stochastic',
        'Ichimoku Cloud',
        'Parabolic SAR',
        'Donchian Channels',
        'Keltner Channels',
        'Money Flow Index',
        'On-Balance Volume',
        'Aroon',
        'Choppiness Index',
        'Auto-AVWAP',
    ];
    const popularityRank = new Map(POPULAR_INDICATOR_NAMES.map((name, index) => [name, index]));
    const catalogIndex = new Map(INDICATORS.map((item, index) => [item.name, index]));
    const DISPLAY_ORDER = [...INDICATORS].sort((a, b) => (
        (popularityRank.get(a.name) ?? POPULAR_INDICATOR_NAMES.length + catalogIndex.get(a.name))
        - (popularityRank.get(b.name) ?? POPULAR_INDICATOR_NAMES.length + catalogIndex.get(b.name))
    ));
    const KNOWN_NAMES = new Set(INDICATORS.map(item => item.name));
    const DEFAULT_SHOWN = (
        Array.isArray(window.STANDARD_INDICATOR_DEFAULT_NAMES)
            ? window.STANDARD_INDICATOR_DEFAULT_NAMES
            : DISPLAY_ORDER.slice(0, 15).map(item => item.name)
    ).filter(name => KNOWN_NAMES.has(name)).slice(0, DISPLAY_LIMIT);
    const PREVIOUS_TWELVE_DEFAULT = new Set(['RSI', 'EMA Cross', 'SMA', 'VWAP', 'ADX Trend', 'Supertrend', 'ATR Volatility', 'Volume Profile', 'Candle Patterns', 'Prior Day High/Low', 'Swing Points', 'Pivot Points (S/R)']);
    const LEGACY_DEFAULT_SETS = [
        new Set(['RSI', 'MACD', 'EMA Cross', 'ADX Trend', 'Supertrend', 'Bollinger', 'VWAP', 'Volume Profile', 'SMA', 'Prior Day High/Low']),
        new Set(['RSI', 'MACD', 'EMA Cross', 'SMA', 'Bollinger', 'VWAP', 'ADX Trend', 'Supertrend', 'Stochastic', 'ATR Volatility']),
    ];

    let store = loadStore();
    let activeTab = 'all';
    let searchQuery = '';
    let activeSurface = null;
    let activeTickerOverride = '';
    let lastFocusedElement = null;
    let modal = null;
    let surfaces = [];
    let applyQueued = false;

    function uniqueKnownNames(values) {
        const result = [];
        for (const value of Array.isArray(values) ? values : []) {
            if (KNOWN_NAMES.has(value) && !result.includes(value)) result.push(value);
        }
        return result;
    }

    function normalizeShown(values) {
        const shown = uniqueKnownNames(values);
        const isLegacyDefault = shown.length === 10
            && LEGACY_DEFAULT_SETS.some(legacy => shown.every(name => legacy.has(name)));
        const isPreviousTwelve = shown.length === PREVIOUS_TWELVE_DEFAULT.size
            && shown.every(name => PREVIOUS_TWELVE_DEFAULT.has(name));
        if (isLegacyDefault || isPreviousTwelve) return [...DEFAULT_SHOWN];
        return (shown.length ? shown : [...DEFAULT_SHOWN]).slice(0, DISPLAY_LIMIT);
    }

    function normalizeTicker(value) {
        const ticker = String(value || '').trim().toUpperCase();
        if (!ticker || ticker === '---') return '';
        return ticker.replace(/[^A-Z0-9.^/\-]/g, '');
    }

    function getPageTicker() {
        if (activeTickerOverride) return activeTickerOverride;
        if (PAGE_CONTEXT === 'bots') {
            return normalizeTicker(window.currentStrategySymbol);
        }
        if (PAGE_CONTEXT === 'backtest') {
            return normalizeTicker(document.getElementById('btTicker')?.value);
        }
        const labelTicker = normalizeTicker(document.getElementById('chartTickerLabel')?.textContent);
        if (labelTicker) return labelTicker;
        try {
            return normalizeTicker(localStorage.getItem('lastViewedTicker'));
        } catch (error) {
            return '';
        }
    }

    function getSurfaceScopeKey(surface, tickerOverride = '') {
        const contextLabel = surface?.contextLabel || PAGE_CONTEXT;
        // Dashboard uses one indicator workspace for every ticker so its
        // displayed cards stay aligned with the Dashboard-wide backend
        // indicator toggles. Bots and backtest remain ticker-scoped.
        if (PAGE_CONTEXT === 'dashboard' && contextLabel === 'dashboard') {
            return DASHBOARD_SHARED_SCOPE_KEY;
        }
        const ticker = normalizeTicker(tickerOverride) || getPageTicker() || 'DEFAULT';
        return `${contextLabel}:${ticker}`;
    }

    function loadStore() {
        try {
            const saved = JSON.parse(localStorage.getItem(STORAGE_KEY) || 'null');
            if (saved && typeof saved === 'object') {
                const scopes = {};
                Object.entries(saved.scopes || {}).forEach(([scopeKey, scopeState]) => {
                    let shown = Array.isArray(scopeState?.shown)
                        ? normalizeShown(scopeState.shown)
                        : [...DEFAULT_SHOWN];
                    scopes[scopeKey] = {
                        shown,
                    };
                });
                return { scopes, favorites: uniqueKnownNames(saved.favorites) };
            }

            // Preserve page-level favorites from v2. Missing workspaces are
            // initialized by the current page's scope policy.
            const legacy = JSON.parse(localStorage.getItem(LEGACY_STORAGE_KEY) || 'null');
            return {
                scopes: {},
                favorites: uniqueKnownNames(legacy?.favorites),
            };
        } catch (error) {
            return { scopes: {}, favorites: [] };
        }
    }

    function saveStore() {
        try {
            localStorage.setItem(STORAGE_KEY, JSON.stringify(store));
        } catch (error) {
            console.warn('[indicator-workspace] Could not save display preferences.', error);
        }
    }

    function getSurfaceState(surface = activeSurface, tickerOverride = '') {
        const scopeKey = getSurfaceScopeKey(surface, tickerOverride);
        if (!store.scopes[scopeKey]) {
            let shown = [...DEFAULT_SHOWN];

            // Migrate the active ticker's existing Dashboard grid into the
            // new shared scope while preserving the user's selected count.
            if (scopeKey === DASHBOARD_SHARED_SCOPE_KEY) {
                const activeTicker = normalizeTicker(tickerOverride) || getPageTicker();
                const activeTickerScope = activeTicker
                    ? store.scopes[`dashboard:${activeTicker}`]
                    : null;
                const fallbackScope = activeTickerScope || Object.entries(store.scopes)
                    .find(([key]) => key.startsWith('dashboard:') && key !== DASHBOARD_SHARED_SCOPE_KEY)?.[1];
                if (Array.isArray(fallbackScope?.shown)) {
                    shown = normalizeShown(fallbackScope.shown);
                }
            }

            store.scopes[scopeKey] = { shown };
            saveStore();
        }
        return store.scopes[scopeKey];
    }

    function dispatchSelectionChange(surface, previousShown = [], disabledNames = []) {
        if (!surface) return;
        const shown = [...getSurfaceState(surface).shown];
        const removed = [
            ...new Set([
                ...previousShown.filter(name => !shown.includes(name)),
                ...disabledNames,
            ]),
        ];
        window.dispatchEvent(new CustomEvent('indicator-workspace:selection-change', {
            detail: {
                pageContext: PAGE_CONTEXT,
                contextLabel: surface.contextLabel,
                ticker: getPageTicker() || 'DEFAULT',
                shown,
                removed,
                displayLimit: DISPLAY_LIMIT,
            },
        }));
    }

    function dispatchDashboardWorkspaceChange(surface = activeSurface, reason = 'update') {
        if (PAGE_CONTEXT !== 'dashboard' || surface?.contextLabel !== 'dashboard') return;
        window.dispatchEvent(new CustomEvent('indicator-workspace:workspace-change', {
            detail: {
                pageContext: PAGE_CONTEXT,
                contextLabel: 'dashboard',
                reason,
                displayed_indicators: [...getSurfaceState(surface).shown],
                favorites: [...store.favorites],
            },
        }));
    }

    function getCardName(card) {
        return card?.dataset?.indicatorWorkspaceName
            || card?.querySelector?.('input.strategy-indicator-check')?.value
            || '';
    }

    function getSurfaceCards(surface) {
        if (!surface.container) return [];
        if (surface.type === 'dashboard') {
            return INDICATORS.map(item => {
                const card = document.getElementById(`signal-card-${item.name.replace(/\s+/g, '-')}`);
                if (card && card.parentElement === surface.container) {
                    card.dataset.indicatorWorkspaceName = item.name;
                    return card;
                }
                return null;
            }).filter(Boolean);
        }

        return Array.from(surface.container.querySelectorAll('.indicator-card')).filter(card => {
            const name = getCardName(card);
            if (!KNOWN_NAMES.has(name)) return false;
            card.dataset.indicatorWorkspaceName = name;
            return true;
        });
    }

    function orderSurface(cardsByName, surfaceState) {
        const orderedNames = DISPLAY_ORDER.map(item => item.name);
        orderedNames.forEach((name, index) => {
            const card = cardsByName.get(name);
            if (!card) return;
            // Negative values keep the ordered indicators ahead of any other
            // section without detaching/reinserting cards, which would restart
            // Dashboard animations and cause visible flicker.
            card.style.order = String(index - INDICATORS.length);
        });
    }

    function applySurface(surface) {
        const surfaceState = getSurfaceState(surface);
        const cards = getSurfaceCards(surface);
        const cardsByName = new Map(cards.map(card => [getCardName(card), card]));
        cards.forEach(card => {
            const name = getCardName(card);
            const isShown = surfaceState.shown.includes(name);
            // The hidden property survives Dashboard className refreshes, so a
            // removed indicator cannot flash for one frame before refresh().
            card.hidden = !isShown;
            card.classList.toggle('indicator-workspace-hidden', !isShown);
            card.setAttribute('aria-hidden', isShown ? 'false' : 'true');
        });
        orderSurface(cardsByName, surfaceState);

        const disabledNames = [];
        if (surface.type === 'strategy') {
            surface.container.querySelectorAll('.strategy-indicator-check:checked').forEach(checkbox => {
                const name = String(checkbox.value || '').trim();
                if (!KNOWN_NAMES.has(name) || surfaceState.shown.includes(name)) return;
                checkbox.checked = false;
                disabledNames.push(name);
            });
        }
        return disabledNames;
    }

    function updateLaunchButtons() {
        surfaces.forEach(surface => {
            const button = surface.launchButton;
            if (!button) return;
            const surfaceState = getSurfaceState(surface);
            const count = button.querySelector('.iw-launch-count');
            if (count) count.textContent = `${surfaceState.shown.length}/${DISPLAY_LIMIT}`;
            button.setAttribute(
                'aria-label',
                `Add Indicator for ${getSurfaceScopeKey(surface)}. ${surfaceState.shown.length} of ${DISPLAY_LIMIT} shown.`
            );
        });
    }

    function applyAllSurfaces() {
        applyQueued = false;
        surfaces.forEach(surface => {
            const disabledNames = applySurface(surface);
            if (disabledNames.length) dispatchSelectionChange(surface, [], disabledNames);
        });
        updateLaunchButtons();
    }

    function queueApply() {
        if (applyQueued) return;
        applyQueued = true;
        requestAnimationFrame(applyAllSurfaces);
    }

    function makeLaunchButton(surface) {
        const button = document.createElement('button');
        button.type = 'button';
        button.className = 'iw-launch-button';
        button.dataset.indicatorWorkspaceContext = surface.contextLabel;
        button.innerHTML = '<span aria-hidden="true" class="iw-launch-plus">+</span><span>Add Indicator</span><span class="iw-launch-count"></span>';
        button.addEventListener('click', event => {
            if (surface.type === 'backtest-manual-preview') {
                surface.contextLabel = (window.currentBtMode === 'options') ? 'backtest-options' : 'backtest-regular';
            }
            openModal(event, surface);
        });
        surface.launchButton = button;
        return button;
    }

    function attachManualBtPreviewSurface() {
        const container = document.getElementById('manualBtChartPreviewSignalGrid');
        if (!container) return;
        const surface = { type: 'backtest-manual-preview', contextLabel: 'backtest-regular', container };
        surfaces.push(surface);
        const resetButton = document.querySelector('button[onclick*="resetManualBtChartPreviewIndicatorsToDefaults"]');
        const controls = resetButton?.parentElement;
        if (controls && !controls.querySelector('.iw-launch-button')) {
            controls.classList.add('iw-dashboard-controls');
            controls.parentElement?.classList.add('iw-dashboard-heading');
            controls.insertBefore(makeLaunchButton(surface), resetButton);
        }
    }

    function attachDashboardSurface() {
        const container = document.getElementById('signalGrid');
        const panel = document.getElementById('signalConfluencePanel');
        if (!container || !panel) return;

        const surface = { type: 'dashboard', contextLabel: 'dashboard', container };
        surfaces.push(surface);
        const resetButton = panel.querySelector('button[onclick*="resetConfluenceIndicatorsToDefaults"]');
        const controls = resetButton?.parentElement;
        if (controls && !controls.querySelector('.iw-launch-button')) {
            controls.classList.add('iw-dashboard-controls');
            controls.parentElement?.classList.add('iw-dashboard-heading');
            controls.insertBefore(makeLaunchButton(surface), resetButton);
        }
    }

    function attachStrategySurface(container, contextLabel) {
        if (!container) return;
        const surface = { type: 'strategy', contextLabel, container };
        surfaces.push(surface);
        const header = container.previousElementSibling;
        if (header && !header.querySelector('.iw-launch-button')) {
            const previewButton = header.querySelector('button');
            const launchButton = makeLaunchButton(surface);
            if (previewButton) {
                previewButton.parentElement.insertBefore(launchButton, previewButton);
            } else {
                header.appendChild(launchButton);
            }
        }
    }

    function observeSurfaces() {
        surfaces.forEach(surface => {
            const observer = new MutationObserver(queueApply);
            observer.observe(surface.container, { childList: true });
        });
    }

    function observeTickerChanges() {
        if (PAGE_CONTEXT === 'dashboard') {
            const tickerLabel = document.getElementById('chartTickerLabel');
            if (tickerLabel) {
                const observer = new MutationObserver(() => {
                    activeTickerOverride = '';
                    queueApply();
                    surfaces.forEach(surface => dispatchSelectionChange(surface));
                });
                observer.observe(tickerLabel, { childList: true, characterData: true, subtree: true });
            }
        }

        if (PAGE_CONTEXT === 'backtest') {
            const tickerInput = document.getElementById('btTicker');
            tickerInput?.addEventListener('change', () => {
                activeTickerOverride = normalizeTicker(tickerInput.value);
                queueApply();
                surfaces.forEach(surface => dispatchSelectionChange(surface));
            });
            tickerInput?.addEventListener('blur', () => {
                activeTickerOverride = normalizeTicker(tickerInput.value);
                queueApply();
                surfaces.forEach(surface => dispatchSelectionChange(surface));
            });
        }

        window.addEventListener('indicator-workspace:ticker-change', event => {
            const ticker = normalizeTicker(event?.detail?.ticker);
            if (!ticker) return;
            activeTickerOverride = ticker;
            queueApply();
            surfaces.forEach(surface => dispatchSelectionChange(surface));
        });
    }

    function sortedCatalog() {
        return DISPLAY_ORDER
            .filter(item => activeTab === 'all' || store.favorites.includes(item.name))
            .filter(item => {
                const query = searchQuery.trim().toLowerCase();
                return !query || `${item.name} ${item.label} ${item.category}`.toLowerCase().includes(query);
            })
            .slice();
    }

    function toggleShown(name) {
        const surfaceState = getSurfaceState();
        const previousShown = [...surfaceState.shown];
        if (surfaceState.shown.includes(name)) {
            // Keep one regular indicator available so every page always has
            // a usable signal pool.
            if (surfaceState.shown.length <= 1) {
                renderModal();
                return;
            }
            surfaceState.shown = surfaceState.shown.filter(item => item !== name);
        } else {
            if (surfaceState.shown.length >= DISPLAY_LIMIT) return;
            surfaceState.shown = [...surfaceState.shown, name];
        }
        saveStore();
        applyAllSurfaces();
        dispatchSelectionChange(activeSurface, previousShown);
        dispatchDashboardWorkspaceChange(activeSurface, 'displayed-indicators');
        renderModal();
    }

    function toggleFavorite(name) {
        store.favorites = store.favorites.includes(name)
            ? store.favorites.filter(item => item !== name)
            : [...store.favorites, name];
        saveStore();
        dispatchDashboardWorkspaceChange(activeSurface, 'favorites');
        renderModal();
    }

    function renderModal() {
        if (!modal) return;
        const surfaceState = getSurfaceState();
        const scopeKey = getSurfaceScopeKey(activeSurface);
        const [surfaceLabel, ticker] = scopeKey.split(':');
        const list = modal.querySelector('.iw-catalog');
        const count = modal.querySelector('.iw-modal-count');
        const hint = modal.querySelector('.iw-limit-hint');
        const title = modal.querySelector('#iwModalTitle');
        const description = modal.querySelector('#iwModalDescription');
        const footerNote = modal.querySelector('.iw-scope-note');
        const favoritesTab = modal.querySelector('[data-iw-tab="favorites"]');
        const allTab = modal.querySelector('[data-iw-tab="all"]');
        const favoriteCount = store.favorites.length;
        const searchInput = modal.querySelector('.iw-search-input');

        if (PAGE_CONTEXT === 'dashboard' && scopeKey === DASHBOARD_SHARED_SCOPE_KEY) {
            title.textContent = 'Add Indicator — Dashboard';
            description.textContent = `Choose 1 to ${DISPLAY_LIMIT} standard indicators for the Dashboard grid. This same list follows you when you switch tickers.`;
            footerNote.textContent = 'Grid choices and favorites are shared across Dashboard tickers. Hidden indicators are disabled for Dashboard signals and charts.';
        } else {
            title.textContent = `Add Indicator — ${ticker}`;
            description.textContent = `Choose 1 to ${DISPLAY_LIMIT} standard indicators for the ${surfaceLabel.replace('-', ' ')} grid. Other tickers and pages keep their own grid.`;
            footerNote.textContent = `Grid choices are saved only for ${ticker} in ${surfaceLabel.replace('-', ' ')}. Hidden indicators are disabled for signals and charts.`;
        }
        count.textContent = `${surfaceState.shown.length} of ${DISPLAY_LIMIT} shown`;
        count.classList.toggle('iw-count-full', surfaceState.shown.length >= DISPLAY_LIMIT);
        if (surfaceState.shown.length >= DISPLAY_LIMIT) {
            hint.textContent = `Maximum ${DISPLAY_LIMIT} selected — toggle one off before enabling another.`;
        } else if (surfaceState.shown.length <= 1) {
            hint.textContent = 'At least 1 indicator must remain selected.';
        } else {
            hint.textContent = `You can add ${DISPLAY_LIMIT - surfaceState.shown.length} more indicator${DISPLAY_LIMIT - surfaceState.shown.length === 1 ? '' : 's'}.`;
        }
        allTab.innerHTML = `All Indicators <span>${INDICATORS.length}</span>`;
        favoritesTab.innerHTML = `Favorites <span>${favoriteCount}</span>`;
        allTab.classList.toggle('iw-tab-active', activeTab === 'all');
        favoritesTab.classList.toggle('iw-tab-active', activeTab === 'favorites');
        if (searchInput && searchInput.value !== searchQuery) searchInput.value = searchQuery;

        const items = sortedCatalog();
        if (!items.length) {
            const isSearchEmpty = Boolean(searchQuery.trim());
            list.innerHTML = `
                <div class="iw-empty-state">
                    <span aria-hidden="true">${isSearchEmpty ? '⌕' : '☆'}</span>
                    <strong>${isSearchEmpty ? 'No matching indicators' : 'No favorite indicators yet'}</strong>
                    <p>${isSearchEmpty ? 'Try a name or category such as trend, volume, momentum, or volatility.' : 'Star an indicator in the All Indicators tab to keep it here.'}</p>
                    ${isSearchEmpty ? '<button type="button" data-iw-clear-search>Clear search</button>' : '<button type="button" data-iw-show-all>Browse all indicators</button>'}
                </div>`;
            list.querySelector('[data-iw-show-all]')?.addEventListener('click', () => {
                activeTab = 'all';
                renderModal();
            });
            list.querySelector('[data-iw-clear-search]')?.addEventListener('click', () => {
                searchQuery = '';
                renderModal();
                modal.querySelector('.iw-search-input')?.focus();
            });
            return;
        }

        list.innerHTML = '';
        items.forEach(item => {
            const isShown = surfaceState.shown.includes(item.name);
            const isFavorite = store.favorites.includes(item.name);
            const atLimit = surfaceState.shown.length >= DISPLAY_LIMIT;
            const atMinimum = surfaceState.shown.length <= 1;
            const disableToggle = (isShown && atMinimum) || (!isShown && atLimit);
            const toggleLabel = isShown ? 'Shown' : 'Hidden';
            const toggleAria = isShown
                ? `${atMinimum ? 'Keep' : 'Toggle off'} ${item.label}`
                : `Toggle on ${item.label}`;
            const row = document.createElement('div');
            row.className = `iw-catalog-row${isShown ? ' iw-row-shown' : ''}`;
            row.innerHTML = `
                <button type="button" class="iw-favorite-button${isFavorite ? ' iw-favorite-active' : ''}"
                    aria-label="${isFavorite ? 'Remove' : 'Add'} ${item.label} ${isFavorite ? 'from' : 'to'} favorites"
                    aria-pressed="${isFavorite}">
                    <span aria-hidden="true">${isFavorite ? '★' : '☆'}</span>
                </button>
                <div class="iw-indicator-copy">
                    <strong>${item.label}</strong>
                    <span>${item.category}</span>
                </div>
                <label class="iw-toggle">
                    <span class="iw-toggle-label">${toggleLabel}</span>
                    <input type="checkbox" ${isShown ? 'checked' : ''} ${disableToggle ? 'disabled' : ''}
                        aria-label="${toggleAria}">
                    <span class="iw-toggle-track" aria-hidden="true"></span>
                </label>`;
            row.querySelector('.iw-favorite-button').addEventListener('click', () => toggleFavorite(item.name));
            row.querySelector('input').addEventListener('change', () => toggleShown(item.name));
            list.appendChild(row);
        });
    }

    function openModal(event, surface = null) {
        if (!modal) return;
        activeSurface = surface || activeSurface || surfaces[0] || null;
        lastFocusedElement = event?.currentTarget || document.activeElement;
        activeTab = 'all';
        searchQuery = '';
        modal.classList.add('iw-modal-open');
        modal.setAttribute('aria-hidden', 'false');
        document.body.classList.add('iw-modal-lock');
        renderModal();
        setTimeout(() => modal.querySelector('.iw-close-button')?.focus(), 0);
    }

    function closeModal() {
        if (!modal) return;
        modal.classList.remove('iw-modal-open');
        modal.setAttribute('aria-hidden', 'true');
        document.body.classList.remove('iw-modal-lock');
        lastFocusedElement?.focus?.();
    }

    function resetDefaults() {
        const previousShown = [...getSurfaceState().shown];
        getSurfaceState().shown = [...DEFAULT_SHOWN];
        saveStore();
        applyAllSurfaces();
        dispatchSelectionChange(activeSurface, previousShown);
        dispatchDashboardWorkspaceChange(activeSurface, 'reset-defaults');
        renderModal();
    }

    function trapFocus(event) {
        if (event.key === 'Escape') {
            closeModal();
            return;
        }
        if (event.key !== 'Tab' || !modal?.classList.contains('iw-modal-open')) return;
        const focusable = Array.from(modal.querySelectorAll('button:not([disabled]), input:not([disabled])'));
        if (!focusable.length) return;
        const first = focusable[0];
        const last = focusable[focusable.length - 1];
        if (event.shiftKey && document.activeElement === first) {
            event.preventDefault();
            last.focus();
        } else if (!event.shiftKey && document.activeElement === last) {
            event.preventDefault();
            first.focus();
        }
    }

    function createModal() {
        modal = document.createElement('div');
        modal.id = 'indicatorWorkspaceModal';
        modal.className = 'iw-modal';
        modal.setAttribute('aria-hidden', 'true');
        modal.innerHTML = `
            <div class="iw-backdrop" data-iw-close></div>
            <section class="iw-dialog" role="dialog" aria-modal="true" aria-labelledby="iwModalTitle" aria-describedby="iwModalDescription">
                <header class="iw-dialog-header">
                    <div>
                        <p class="iw-eyebrow">Indicator workspace</p>
                        <h2 id="iwModalTitle">Add Indicator</h2>
                        <p id="iwModalDescription">${PAGE_CONTEXT === 'dashboard' ? `Choose 1 to ${DISPLAY_LIMIT} standard indicators for the Dashboard.` : `Choose 1 to ${DISPLAY_LIMIT} standard indicators for this ticker.`}</p>
                    </div>
                    <button type="button" class="iw-close-button" data-iw-close aria-label="Close indicator selector">×</button>
                </header>
                <div class="iw-selection-summary">
                    <span class="iw-modal-count"></span>
                    <span class="iw-limit-hint"></span>
                </div>
                <nav class="iw-tabs" aria-label="Indicator lists">
                    <button type="button" class="iw-tab-active" data-iw-tab="all">All Indicators</button>
                    <button type="button" data-iw-tab="favorites">Favorites <span>0</span></button>
                </nav>
                <label class="iw-search">
                    <span aria-hidden="true">⌕</span>
                    <span class="sr-only">Search indicators</span>
                    <input type="search" class="iw-search-input" placeholder="Search ${INDICATORS.length} indicators by name or category" autocomplete="off">
                </label>
                <div class="iw-catalog"></div>
                <footer class="iw-dialog-footer">
                    <div>
                        <button type="button" class="iw-reset-button">Reset default ${DEFAULT_SHOWN.length}</button>
                        <p class="iw-scope-note">${PAGE_CONTEXT === 'dashboard' ? 'Dashboard choices are shared across tickers.' : 'Grid choices are saved per ticker.'} Hidden indicators are disabled for signals and charts.</p>
                    </div>
                    <button type="button" class="iw-done-button" data-iw-close>Done</button>
                </footer>
            </section>`;
        document.body.appendChild(modal);

        modal.querySelectorAll('[data-iw-close]').forEach(element => element.addEventListener('click', closeModal));
        modal.querySelectorAll('[data-iw-tab]').forEach(button => {
            button.addEventListener('click', () => {
                activeTab = button.dataset.iwTab;
                renderModal();
            });
        });
        modal.querySelector('.iw-reset-button').addEventListener('click', resetDefaults);
        modal.querySelector('.iw-search-input').addEventListener('input', event => {
            searchQuery = event.target.value;
            renderModal();
        });
        modal.addEventListener('keydown', trapFocus);
    }

    function initialize() {
        // v1 used one shared setting across every page. It remains removed so
        // page and ticker scopes cannot inherit the old overlapping state.
        try {
            localStorage.removeItem('botbullsIndicatorWorkspace:v1');
        } catch (error) {}
        createModal();
        attachDashboardSurface();
        attachStrategySurface(document.getElementById('indicatorChecksContainer_regular'), 'bots-regular');
        attachStrategySurface(document.getElementById('indicatorChecksContainer_options'), 'bots-options');
        attachStrategySurface(document.getElementById('indicatorChecksContainer_bt_regular'), 'backtest-regular');
        attachStrategySurface(document.getElementById('indicatorChecksContainer_bt_options'), 'backtest-options');
        attachManualBtPreviewSurface();
        observeSurfaces();
        observeTickerChanges();
        activeSurface = surfaces[0] || null;
        applyAllSurfaces();
        surfaces.forEach(surface => dispatchSelectionChange(surface));
    }

    window.IndicatorWorkspace = {
        open: openModal,
        close: closeModal,
        refresh: queueApply,
        setTicker: ticker => {
            activeTickerOverride = normalizeTicker(ticker);
            queueApply();
            surfaces.forEach(surface => dispatchSelectionChange(surface));
        },
        resetScope: (contextLabel, tickerOverride = '') => {
            const surface = surfaces.find(item => item.contextLabel === contextLabel)
                || { contextLabel: contextLabel || PAGE_CONTEXT };
            const scopeKey = getSurfaceScopeKey(surface, tickerOverride);
            store.scopes[scopeKey] = { shown: [...DEFAULT_SHOWN] };
            saveStore();
            queueApply();
            dispatchSelectionChange(surface);
            dispatchDashboardWorkspaceChange(surface, 'reset-scope');
        },
        setDashboardState: workspace => {
            if (PAGE_CONTEXT !== 'dashboard' || !workspace || typeof workspace !== 'object') return false;
            const surface = surfaces.find(item => item.contextLabel === 'dashboard')
                || { contextLabel: 'dashboard' };
            const displayed = normalizeShown(workspace.displayed_indicators);
            const favorites = uniqueKnownNames(workspace.favorites);
            store.scopes[DASHBOARD_SHARED_SCOPE_KEY] = { shown: displayed };
            store.favorites = favorites;
            saveStore();
            applyAllSurfaces();
            dispatchSelectionChange(surface);
            if (modal?.classList.contains('iw-modal-open')) renderModal();
            return true;
        },
        getShown: (contextLabel = '') => {
            const surface = surfaces.find(item => item.contextLabel === contextLabel)
                || activeSurface
                || surfaces[0]
                || null;
            return [...getSurfaceState(surface).shown];
        },
        getShownForScope: (contextLabel, ticker) => {
            const surface = surfaces.find(item => item.contextLabel === contextLabel)
                || { contextLabel: contextLabel || PAGE_CONTEXT };
            return [...getSurfaceState(surface, ticker).shown];
        },
        isShown: (name, contextLabel = '', ticker = '') => {
            const surface = surfaces.find(item => item.contextLabel === contextLabel)
                || activeSurface
                || surfaces[0]
                || { contextLabel: contextLabel || PAGE_CONTEXT };
            return getSurfaceState(surface, ticker).shown.includes(name);
        },
        getFavorites: () => [...store.favorites],
        getContext: () => PAGE_CONTEXT,
        getScopeKey: (contextLabel = '', ticker = '') => {
            const surface = surfaces.find(item => item.contextLabel === contextLabel)
                || activeSurface
                || surfaces[0]
                || { contextLabel: contextLabel || PAGE_CONTEXT };
            return getSurfaceScopeKey(surface, ticker);
        },
    };

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', initialize);
    } else {
        initialize();
    }
})();
