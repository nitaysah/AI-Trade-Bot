/*
 * Drag-and-drop ordering for the Bots and Screener cards (Arrange mode).
 *
 * In Arrange mode the cards shrink to their header row, so many fit on screen. Grab a card anywhere (or its grip handle) and
 * drag it up or down past as many cards as you like: the card follows your finger or mouse, the others make room as you cross
 * them, the page scrolls by itself when you hold a card near the top or bottom edge, and the new order is saved ONCE when you
 * let go. The order on screen changes at once; the save happens in the background and is undone (with a message) if the
 * server refuses it.
 *
 *  - Mouse / pen: press on a card and move: it starts after a few pixels, so a plain click still works.
 *  - Touch: press and HOLD a card for a moment (it buzzes where supported), then drag. Moving the finger before that is an
 *    ordinary scroll, so the page still scrolls with a finger on a card. The grip handle starts a drag at once.
 *  - Buttons, links and fields inside a card are never dragged from (the star, settings and remove buttons keep working).
 *  - Escape or a cancelled touch puts the card back. The handle is also a button: focus it and press Up / Down.
 *
 * Display and ordering only: it never touches orders, settings or scans. Each page supplies what to do with the new order.
 */
(function () {
    'use strict';
    if (window.CardReorder) return;

    const MAX_SCROLL_STEP = 32;    // px per frame at the very edge (about 1900 px a second)
    const CLICK_SUPPRESS_PX = 4;   // movement that counts as a drag (so a drop does not also "click" the card)
    const MOUSE_START_PX = 4;      // mouse / pen: movement before a press on the card becomes a drag
    const HOLD_MS = 220;           // touch: how long to hold a card before it lifts
    const HOLD_SLOP_PX = 8;        // touch: movement during the hold that means "this is a scroll", not a drag
    const KEY_COMMIT_DELAY_MS = 500;

    let dragging = false;
    let committing = 0;
    let announcer = null;

    function announce(message) {
        if (!announcer) {
            announcer = document.createElement('div');
            announcer.setAttribute('role', 'status');
            announcer.setAttribute('aria-live', 'polite');
            announcer.style.cssText = 'position:absolute;width:1px;height:1px;overflow:hidden;clip:rect(0 0 0 0);white-space:nowrap;';
            document.body.appendChild(announcer);
        }
        announcer.textContent = message;
    }

    function attach(container, options = {}) {
        if (!container || container.dataset.cardReorderAttached === '1') return;
        container.dataset.cardReorderAttached = '1';
        const itemSelector = options.itemSelector || '.watchlist-item';
        const handleSelector = options.handleSelector || '[data-reorder-handle]';
        const ignoreSelector = options.ignoreSelector || 'button, a, input, select, textarea, label, summary, [data-no-reorder]';
        const wholeCard = options.wholeCard !== false;
        const idOf = options.getId || ((element) => element.dataset.ticker);
        const isEnabled = options.isEnabled || (() => true);
        const onCommit = options.onCommit || (() => {});

        const items = () => Array.from(container.children).filter((element) => element.matches(itemSelector) && idOf(element));
        const order = () => items().map(idOf);
        const neighbour = (element, direction) => {
            let node = direction < 0 ? element.previousElementSibling : element.nextElementSibling;
            while (node && !(node.matches(itemSelector) && idOf(node))) {
                node = direction < 0 ? node.previousElementSibling : node.nextElementSibling;
            }
            return node;
        };

        function restoreOrder(ids) {
            const byId = new Map(items().map((element) => [idOf(element), element]));
            ids.forEach((id) => { const element = byId.get(id); if (element) container.appendChild(element); });
        }

        async function commit(newOrder, previousOrder) {
            committing += 1;
            try {
                await onCommit(newOrder, previousOrder);
            } catch (error) {
                console.error('[reorder] Saving the new order failed; putting the cards back.', error);
                restoreOrder(previousOrder);
                announce('The new order could not be saved. The cards were put back.');
                try { if (typeof options.onRevert === 'function') options.onRevert(previousOrder, error); } catch (_) { /* never blocks the revert */ }
            } finally {
                committing -= 1;
            }
        }

        // ── Pointer drag ───────────────────────────────────────────────────────────────────────────────────────────
        let pending = null;   // a press on a card that may still become a drag (touch hold / mouse movement)

        const cancelPending = () => {
            if (!pending) return;
            clearTimeout(pending.timer);
            document.removeEventListener('pointermove', pending.onMove, true);
            document.removeEventListener('pointerup', pending.onEnd, true);
            document.removeEventListener('pointercancel', pending.onEnd, true);
            pending = null;
        };

        container.addEventListener('pointerdown', (event) => {
            if (dragging || !isEnabled()) return;
            if (event.pointerType === 'mouse' && event.button !== 0) return;
            const target = event.target;
            if (!target || !target.closest) return;
            const handle = target.closest(handleSelector);
            const item = target.closest(itemSelector);
            if (!item || item.parentElement !== container || !idOf(item)) return;

            if (handle && container.contains(handle)) {
                // The grip lifts the card at once, for any pointer.
                event.preventDefault();
                startDrag(item, handle, event.pointerId, event.clientY);
                return;
            }
            if (!wholeCard) return;
            if (target.closest(ignoreSelector)) return;   // a button / link / field inside the card keeps its own job

            cancelPending();
            const press = { id: event.pointerId, x: event.clientX, y: event.clientY, type: event.pointerType, lastY: event.clientY };
            const begin = () => {
                const captureEl = item;
                cancelPending();
                if (typeof navigator.vibrate === 'function') { try { navigator.vibrate(12); } catch (_) { /* optional */ } }
                startDrag(item, captureEl, press.id, press.lastY);
            };
            press.onMove = (moveEvent) => {
                if (moveEvent.pointerId !== press.id) return;
                press.lastY = moveEvent.clientY;
                const far = Math.hypot(moveEvent.clientX - press.x, moveEvent.clientY - press.y);
                if (press.type === 'mouse') {
                    if (far > MOUSE_START_PX) begin();
                } else if (far > HOLD_SLOP_PX) {
                    cancelPending();      // the finger moved before the hold finished: an ordinary scroll
                }
            };
            press.onEnd = (endEvent) => { if (endEvent.pointerId === press.id) cancelPending(); };
            if (press.type !== 'mouse') press.timer = setTimeout(begin, HOLD_MS);
            pending = press;
            document.addEventListener('pointermove', press.onMove, true);
            document.addEventListener('pointerup', press.onEnd, true);
            document.addEventListener('pointercancel', press.onEnd, true);
        });

        // A long press on a touch screen would otherwise open the browser's context menu / text selection.
        container.addEventListener('contextmenu', (event) => {
            if (isEnabled() && (pending || dragging)) event.preventDefault();
        });

        function startDrag(item, captureEl, pointerId, startClientY) {
            dragging = true;
            const initialOrder = order();
            const startClientYValue = startClientY;
            const startScrollY = window.scrollY;
            const startOffsetTop = item.offsetTop;
            let lastClientY = startClientYValue;
            let moved = false;
            let frame = 0;
            let finished = false;

            try { captureEl.setPointerCapture(pointerId); } catch (_) { /* some browsers refuse for synthetic events */ }
            item.classList.add('card-reorder-dragging');
            document.body.classList.add('card-reorder-active');
            item.style.position = 'relative';
            item.style.transition = 'none';
            item.style.willChange = 'transform';

            // While a card is held the page must not scroll under the finger (the page scrolls itself near the edges instead).
            const blockTouchScroll = (touchEvent) => { if (touchEvent.cancelable) touchEvent.preventDefault(); };
            document.addEventListener('touchmove', blockTouchScroll, { passive: false });

            function place() {
                // Follow the pointer in page coordinates (so scrolling while dragging does not make the card jump), minus
                // how far the card's own slot has moved because of swaps.
                const pointerDelta = (window.scrollY + lastClientY) - (startScrollY + startClientYValue);
                const slotShift = item.offsetTop - startOffsetTop;
                item.style.transform = `translateY(${pointerDelta - slotShift}px)`;
            }

            // A swap moves the NEIGHBOUR past the held card, never the held card itself: taking the held element out of the page
            // (even for an instant) makes the browser drop the finger / mouse and the drag dies after the first swap.
            function swapIfNeeded() {
                for (let guard = 0; guard < 80; guard += 1) {
                    const rect = item.getBoundingClientRect();
                    const centre = rect.top + rect.height / 2;
                    const previous = neighbour(item, -1);
                    const next = neighbour(item, 1);
                    let swapped = null;
                    if (previous) {
                        const box = previous.getBoundingClientRect();
                        if (centre < box.top + box.height / 2) { container.insertBefore(previous, item.nextElementSibling); swapped = { node: previous, before: box.top }; }
                    }
                    if (!swapped && next) {
                        const box = next.getBoundingClientRect();
                        if (centre > box.top + box.height / 2) { container.insertBefore(next, item); swapped = { node: next, before: box.top }; }
                    }
                    if (!swapped) return;
                    place();
                    // Glide the card that moved aside instead of letting it jump.
                    const delta = swapped.before - swapped.node.getBoundingClientRect().top;
                    if (delta && typeof swapped.node.animate === 'function') {
                        swapped.node.animate([{ transform: `translateY(${delta}px)` }, { transform: 'translateY(0)' }], { duration: 120, easing: 'ease-out' });
                    }
                }
            }

            function step() {
                frame = 0;
                if (finished) return;
                // Auto-scroll when the held card is near the top or bottom of the screen; faster the closer to the edge.
                const height = window.innerHeight;
                const edge = Math.min(170, Math.max(90, height * 0.22));
                let scrollBy = 0;
                if (lastClientY < edge) scrollBy = -Math.ceil(MAX_SCROLL_STEP * Math.pow(1 - Math.max(lastClientY, 0) / edge, 1.4));
                else if (lastClientY > height - edge) scrollBy = Math.ceil(MAX_SCROLL_STEP * Math.pow(1 - Math.max(height - lastClientY, 0) / edge, 1.4));
                if (scrollBy) window.scrollBy(0, scrollBy);
                place();
                swapIfNeeded();
                if (scrollBy) frame = requestAnimationFrame(step);
            }
            const schedule = () => { if (!frame && !finished) frame = requestAnimationFrame(step); };

            function onMove(event) {
                if (event.pointerId !== pointerId) return;
                lastClientY = event.clientY;
                if (Math.abs(lastClientY - startClientYValue) > CLICK_SUPPRESS_PX) moved = true;
                if (event.cancelable) event.preventDefault();
                place();
                swapIfNeeded();
                schedule();
            }

            function cleanup() {
                finished = true;
                if (frame) cancelAnimationFrame(frame);
                captureEl.removeEventListener('pointermove', onMove);
                captureEl.removeEventListener('pointerup', onUp);
                captureEl.removeEventListener('pointercancel', onCancel);
                captureEl.removeEventListener('lostpointercapture', onCancel);
                document.removeEventListener('pointermove', onMove, true);
                document.removeEventListener('pointerup', onUp, true);
                document.removeEventListener('pointercancel', onCancel, true);
                document.removeEventListener('keydown', onKey, true);
                document.removeEventListener('touchmove', blockTouchScroll, { passive: false });
                try { captureEl.releasePointerCapture(pointerId); } catch (_) { /* already released */ }
                document.body.classList.remove('card-reorder-active');
            }

            function settle(afterSettle) {
                // Slide the card from where it was dropped into its slot, then clear the temporary styles.
                item.style.transition = 'transform 140ms ease-out';
                item.style.transform = 'translateY(0)';
                let done = false;
                const clear = () => {
                    if (done) return;
                    done = true;
                    item.removeEventListener('transitionend', clear);
                    item.style.transition = '';
                    item.style.transform = '';
                    item.style.position = '';
                    item.style.willChange = '';
                    item.classList.remove('card-reorder-dragging');
                    dragging = false;
                    afterSettle();
                };
                item.addEventListener('transitionend', clear);
                setTimeout(clear, 220);
            }

            function onUp(event) {
                if (event.pointerId !== pointerId || finished) return;
                cleanup();
                if (moved) {
                    // The pointer-up that ends a drag must not also click the card underneath.
                    const swallow = (clickEvent) => { clickEvent.stopPropagation(); clickEvent.preventDefault(); };
                    document.addEventListener('click', swallow, { capture: true, once: true });
                    setTimeout(() => document.removeEventListener('click', swallow, true), 400);
                }
                const finalOrder = order();
                settle(() => {
                    if (finalOrder.join('|') !== initialOrder.join('|')) {
                        announce(`${idOf(item)} moved to position ${finalOrder.indexOf(idOf(item)) + 1} of ${finalOrder.length}.`);
                        commit(finalOrder, initialOrder);
                    }
                });
            }

            function onCancel(event) {
                if (finished || (event.pointerId !== undefined && event.pointerId !== pointerId)) return;
                cleanup();
                restoreOrder(initialOrder);
                settle(() => {});
            }

            function onKey(event) {
                if (event.key !== 'Escape') return;
                event.preventDefault();
                onCancel({ pointerId });
            }

            // With pointer capture the events arrive on the capture element; listening on the document too covers browsers
            // where capture was refused (the handlers ignore other pointers and a finished drag).
            captureEl.addEventListener('pointermove', onMove);
            captureEl.addEventListener('pointerup', onUp);
            captureEl.addEventListener('pointercancel', onCancel);
            captureEl.addEventListener('lostpointercapture', onCancel);
            document.addEventListener('pointermove', onMove, true);
            document.addEventListener('pointerup', onUp, true);
            document.addEventListener('pointercancel', onCancel, true);
            document.addEventListener('keydown', onKey, true);
            place();
            swapIfNeeded();
        }

        // ── Keyboard: focus the handle, then Up / Down arrow keys ─────────────────────────────────────────────────
        let keyTimer = 0;
        let keyBaseline = null;
        container.addEventListener('keydown', (event) => {
            if (dragging || !isEnabled()) return;
            if (event.key !== 'ArrowUp' && event.key !== 'ArrowDown') return;
            const handle = event.target.closest && event.target.closest(handleSelector);
            if (!handle) return;
            const item = handle.closest(itemSelector);
            if (!item || item.parentElement !== container) return;
            event.preventDefault();
            const target = neighbour(item, event.key === 'ArrowUp' ? -1 : 1);
            if (!target) return;
            if (!keyBaseline) keyBaseline = order();
            if (event.key === 'ArrowUp') container.insertBefore(item, target);
            else container.insertBefore(item, target.nextElementSibling);
            handle.focus();
            const now = order();
            announce(`${idOf(item)} moved to position ${now.indexOf(idOf(item)) + 1} of ${now.length}.`);
            clearTimeout(keyTimer);
            keyTimer = setTimeout(() => {
                const baseline = keyBaseline;
                keyBaseline = null;
                const final = order();
                if (baseline && final.join('|') !== baseline.join('|')) commit(final, baseline);
            }, KEY_COMMIT_DELAY_MS);
        });
    }

    window.CardReorder = {
        attach,
        isDragging: () => dragging,
        // True while a drag is in progress or a new order is still being saved: the page holds back re-renders so a poll
        // cannot undo the order that is on screen.
        isBusy: () => dragging || committing > 0,
    };
})();
