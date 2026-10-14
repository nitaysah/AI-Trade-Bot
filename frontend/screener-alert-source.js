/* Screener alert source: Indicators / Strategy / Event-Driven are three exclusive toggles.
 *
 * Indicators and Strategy are the two switches strategy-preset.js already owns (its "Entry / Exit
 * Strategy" box and the Strategy Presets box). This module adds the Event-Driven switch and the single
 * source of truth that combines the three. Exactly one source is on, or none (alerts paused).
 * The screening policy kept for older code paths is derived: event -> "event", everything else -> "technical". */
(function () {
    'use strict';

    const SOURCES = ['indicators', 'strategy', 'event', 'none'];
    const LABELS = { indicators: 'Indicators', strategy: 'Strategy', event: 'Event-Driven', none: 'Alerts paused' };
    const state = { event: false };
    const el = (id) => document.getElementById(id);
    const preset = () => window.StrategyPreset;

    function getSource() {
        const sp = preset();
        if (!sp) return state.event ? 'event' : 'indicators';
        if (sp.isEnabled()) return 'strategy';
        if (!sp.isManualOff()) return 'indicators';
        return state.event ? 'event' : 'none';
    }

    function policyFor(source) {
        return source === 'event' ? 'event' : 'technical';
    }

    /* Keep the hidden fields the rest of screener.js reads, and the Event box, in step. */
    function render() {
        const source = getSource();
        const policy = policyFor(source);
        const mode = el('strategyScreeningMode_regular');
        const hidden = el('strategyAlertSource_regular');
        const modeChanged = !!mode && mode.value !== policy;
        if (mode) mode.value = policy;
        if (hidden) hidden.value = source;
        const toggle = el('screenerEventToggle_regular');
        const body = el('screenerEventBody_regular');
        const on = source === 'event';
        if (toggle) {
            toggle.setAttribute('aria-checked', String(on));
            toggle.setAttribute('aria-expanded', String(on));
            const track = toggle.querySelector('[data-event="track"]');
            const knob = toggle.querySelector('[data-event="knob"]');
            if (track) { track.classList.toggle('bg-purple-600', on); track.classList.toggle('bg-indigo-200', !on); }
            if (knob) { knob.classList.toggle('translate-x-6', on); knob.classList.toggle('translate-x-1', !on); }
        }
        if (body) body.hidden = !on;
        const caption = el('screenerEventCaption_regular');
        if (caption) {
            caption.textContent = on
                ? 'Alerts come from fresh news catalysts only. Turning this on switches the other alert sources off.'
                : 'Turn this on to alert from news catalysts instead; the other alert sources switch off.';
        }
        const status = el('screenerAlertSourceStatus_regular');
        if (status) {
            status.textContent = source === 'none'
                ? 'Alerts are paused for this ticker. Turn one source on to resume.'
                : `Alert source: ${LABELS[source]}`;
            status.classList.toggle('text-amber-600', source === 'none');
            status.classList.toggle('text-indigo-700', source !== 'none');
        }
        if (modeChanged) {
            try { window.syncScreenerNewsWatchForMode?.(policy); } catch (e) { /* page handler optional */ }
        }
    }

    /* Called by strategy-preset.js after any of its own switches changed. Choosing Indicators or a
     * Strategy always takes Event-Driven off, so only one source can ever be on. */
    function sync() {
        const sp = preset();
        if (sp && (sp.isEnabled() || !sp.isManualOff())) state.event = false;
        render();
    }

    function onEventToggle() {
        const sp = preset();
        if (state.event && getSource() === 'event') {
            state.event = false;      // the only active source turned off: alerts paused
            render();
            return;
        }
        state.event = true;
        // Indicators off is recorded before a Strategy is switched off, so the sync that the
        // switch-off triggers does not mistake this for "Indicators on" and clear Event-Driven.
        if (sp) sp.setManualOff(true);   // also turns an active Strategy off; notifies and renders
        render();
    }

    /* Restore a saved profile: prefer alert_source, else derive it from the legacy fields. */
    function sourceFromSettings(settings) {
        const s = settings || {};
        const explicit = String(s.alert_source || '').toLowerCase();
        if (SOURCES.includes(explicit)) return explicit;
        if (s.strategy_preset_enabled && s.strategy_preset && s.strategy_preset !== 'none') return 'strategy';
        return String(s.strategy_mode || '').toLowerCase() === 'event' ? 'event' : 'indicators';
    }

    function restore(settings) {
        const sp = preset();
        const source = sourceFromSettings(settings);
        state.event = source === 'event';
        if (sp && (source === 'event' || source === 'none')) sp.setManualOff(true);
        render();
    }

    function reset() {
        state.event = false;
        render();
    }

    function bind() {
        const toggle = el('screenerEventToggle_regular');
        if (toggle && !toggle.dataset.bound) {
            toggle.dataset.bound = '1';
            toggle.addEventListener('click', onEventToggle);
        }
        render();
    }

    window.ScreenerAlertSource = { getSource, policyFor, sync, restore, reset, sourceFromSettings, label: (s) => LABELS[s] || LABELS.indicators };

    if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', bind);
    else bind();
})();
