/* Smart Re-entry Controls: a master toggle in the box header, and plain checkboxes for the three rules inside.
 *
 * The master switches the whole group on or off. Off collapses the box to its header and switches the three rules
 * off (their previous choices are remembered and come back when the master is turned on again). On shows the box,
 * where each rule (Signal Reset, Smart Re-entry Indicators, Re-Entry Cooldown) is a checkbox the user ticks or not.
 * The three inputs keep their ids and values, so Bots, Backtest and the strategy-preset lock read them as before. */
(function () {
    'use strict';

    const BOXES = [
        { box: 'strategyReentryOption_regular', signal: 'strategyRegularSignalReset', smart: 'strategyRegularSmartReentry', cooldown: 'strategyEnableCooldown_regular' },
        { box: 'strategyReentryOption_options', signal: 'strategyOptionsSignalReset', smart: 'strategyOptionsSmartReentry', cooldown: 'strategyEnableCooldown_options' },
    ];
    const nativeChecked = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'checked');

    function byId(id) { return document.getElementById(id); }
    const anyChecked = inputs => inputs.some(input => nativeChecked.get.call(input));

    function setNative(input, value) {
        if (nativeChecked.get.call(input) === value) return false;
        nativeChecked.set.call(input, value);
        input.dispatchEvent(new Event('change', { bubbles: true }));  // run the page's own onchange handlers
        return true;
    }

    function ruleName(input) {
        const row = input.closest('label')?.parentElement;
        const text = row?.querySelector('label.block')?.textContent || '';
        return text.replace(/\s+/g, ' ').trim();
    }

    function init(cfg) {
        const box = byId(cfg.box);
        const inputs = [byId(cfg.signal), byId(cfg.smart), byId(cfg.cooldown)];
        if (!box || inputs.some(input => !input) || box.dataset.reentryMaster === '1') return !!box && box.dataset.reentryMaster === '1';
        const header = box.firstElementChild;
        const sample = inputs[0].closest('label.relative');  // an existing switch, cloned so the master looks like the other toggles
        if (!header || !sample) return false;
        box.dataset.reentryMaster = '1';

        const cooldownWrap = inputs[2].closest('[id^="strategyCooldownOption"]');
        box.classList.add('reentry-box');
        header.classList.add('reentry-head');

        const masterLabel = sample.cloneNode(true);
        const master = masterLabel.querySelector('input');
        master.id = `${cfg.box}_master`;
        master.removeAttribute('onchange');
        master.removeAttribute('checked');
        master.setAttribute('aria-label', 'Enable Smart Re-entry Controls');
        nativeChecked.set.call(master, anyChecked(inputs));
        masterLabel.classList.add('reentry-master');
        header.appendChild(masterLabel);

        inputs.forEach(input => {
            input.closest('label.relative')?.classList.add('reentry-check');
            if (!input.getAttribute('aria-label')) input.setAttribute('aria-label', ruleName(input) || 'Re-entry rule');
        });

        let saved = null;      // rule choices remembered while the master is off
        let manualOn = false;  // master turned on by the user with nothing ticked yet

        function paint() {
            const off = !nativeChecked.get.call(master);
            box.classList.toggle('reentry-off', off);
            cooldownWrap?.classList.toggle('reentry-collapsed', off);
            master.setAttribute('aria-expanded', String(!off));
        }

        master.addEventListener('change', () => {
            if (nativeChecked.get.call(master)) {
                manualOn = !anyChecked(inputs);
                if (saved) inputs.forEach((input, index) => setNative(input, !!saved[index]));
                saved = null;
                manualOn = manualOn && !anyChecked(inputs);
            } else {
                saved = inputs.map(input => nativeChecked.get.call(input));
                inputs.forEach(input => setNative(input, false));
                manualOn = false;
            }
            paint();
        });

        // A rule switched on by the user while the master is off cannot happen (the box is collapsed), but loading a
        // saved bot sets the inputs from code: follow those writes so the master matches what was loaded.
        let reconcileQueued = false;
        function reconcile() {
            reconcileQueued = false;
            manualOn = false;
            nativeChecked.set.call(master, anyChecked(inputs));
            saved = null;
            paint();
        }
        inputs.forEach(input => {
            Object.defineProperty(input, 'checked', {
                configurable: true,
                get() { return nativeChecked.get.call(this); },
                set(value) {
                    nativeChecked.set.call(this, value);
                    if (!reconcileQueued) { reconcileQueued = true; queueMicrotask(reconcile); }
                },
            });
            // The strategy-preset lock disables the rules; the master follows.
            new MutationObserver(() => { master.disabled = inputs.every(item => item.disabled); })
                .observe(input, { attributes: true, attributeFilter: ['disabled'] });
        });
        master.disabled = inputs.every(item => item.disabled);
        paint();
        return true;
    }

    function initAll() { return BOXES.map(init).filter(Boolean).length; }
    window.BotBullsReentryMaster = { init: initAll };
    if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', initAll);
    else initAll();
    window.addEventListener('load', initAll);
})();
