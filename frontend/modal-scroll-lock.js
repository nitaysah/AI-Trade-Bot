// Keep mobile popup interaction inside the popup instead of allowing the page
// behind it to scroll. This is intentionally page-agnostic because the app
// uses several independently rendered modal implementations.
(() => {
    const modalSelector = '[id$="Modal"], [id$="Drawer"], #consentPanel, #authTransitionScreen, [data-modal-root]';
    const activeRoots = new Set();
    const originalRootStyles = new WeakMap();
    const originalPanelStyles = new WeakMap();
    let bodyLockState = null;

    function isOpen(element) {
        if (!element || element.classList.contains('hidden')) return false;
        const computed = window.getComputedStyle(element);
        return computed.display !== 'none' && computed.visibility !== 'hidden';
    }

    function findPanel(root) {
        const directChildren = Array.from(root.children || []);
        return root.querySelector(':scope > [role="dialog"], :scope > [role="alertdialog"]')
            || directChildren.find(child => !child.classList.contains('absolute'))
            || directChildren[0]
            || null;
    }

    function rememberStyles(element, properties, store) {
        if (!element || store.has(element)) return;
        const saved = {};
        properties.forEach(property => {
            saved[property] = element.style[property] || '';
        });
        store.set(element, saved);
    }

    function restoreStyles(element, store) {
        const saved = store.get(element);
        if (!element || !saved) return;
        Object.entries(saved).forEach(([property, value]) => {
            element.style[property] = value;
        });
        store.delete(element);
    }

    function applyRootStyles(root) {
        const panel = findPanel(root);
        const rootProperties = [
            'overflowY',
            'overscrollBehavior',
            'WebkitOverflowScrolling',
            'alignItems',
            'justifyContent',
            'minHeight',
            'maxHeight',
            'boxSizing',
        ];
        const panelProperties = [
            'maxHeight',
            'marginBlock',
            'flexShrink',
            'overscrollBehavior',
            'WebkitOverflowScrolling',
        ];

        rememberStyles(root, rootProperties, originalRootStyles);
        rememberStyles(panel, panelProperties, originalPanelStyles);

        root.classList.add('modal-scroll-root');
        root.style.overflowY = 'auto';
        root.style.overscrollBehavior = 'contain';
        root.style.setProperty('-webkit-overflow-scrolling', 'touch');

        const isMobile = window.matchMedia?.('(max-width: 768px)').matches;
        const savedRoot = originalRootStyles.get(root) || {};
        const savedPanel = originalPanelStyles.get(panel) || {};
        if (isMobile) {
            root.style.alignItems = 'flex-start';
            root.style.justifyContent = 'flex-start';
            root.style.minHeight = '100dvh';
            root.style.maxHeight = '100dvh';
            root.style.boxSizing = 'border-box';
            if (panel) {
                panel.style.maxHeight = 'calc(100dvh - 1rem)';
                panel.style.marginBlock = 'auto';
                panel.style.flexShrink = '0';
                panel.style.overscrollBehavior = 'contain';
                panel.style.setProperty('-webkit-overflow-scrolling', 'touch');
            }
        } else {
            ['alignItems', 'justifyContent', 'minHeight', 'maxHeight', 'boxSizing'].forEach(property => {
                root.style[property] = savedRoot[property] || '';
            });
            if (panel) {
                ['maxHeight', 'marginBlock', 'flexShrink'].forEach(property => {
                    panel.style[property] = savedPanel[property] || '';
                });
            }
        }
    }

    function restoreRoot(root) {
        root.classList.remove('modal-scroll-root');
        restoreStyles(root, originalRootStyles);
        restoreStyles(findPanel(root), originalPanelStyles);
    }

    function lockBody() {
        if (bodyLockState || !document.body) return;
        bodyLockState = {
            scrollY: window.scrollY || window.pageYOffset || 0,
            body: {
                position: document.body.style.position || '',
                top: document.body.style.top || '',
                left: document.body.style.left || '',
                right: document.body.style.right || '',
                width: document.body.style.width || '',
                overflow: document.body.style.overflow || '',
                overscrollBehavior: document.body.style.overscrollBehavior || '',
            },
            documentOverscrollBehavior: document.documentElement.style.overscrollBehavior || '',
        };
        const scrollY = bodyLockState.scrollY;
        document.documentElement.style.overscrollBehavior = 'none';
        document.body.style.position = 'fixed';
        document.body.style.top = `-${scrollY}px`;
        document.body.style.left = '0';
        document.body.style.right = '0';
        document.body.style.width = '100%';
        document.body.style.overflow = 'hidden';
        document.body.style.overscrollBehavior = 'none';
    }

    function unlockBody() {
        if (!bodyLockState || !document.body) return;
        const state = bodyLockState;
        Object.entries(state.body).forEach(([property, value]) => {
            document.body.style[property] = value;
        });
        document.documentElement.style.overscrollBehavior = state.documentOverscrollBehavior;
        bodyLockState = null;
        window.scrollTo(0, state.scrollY);
    }

    function sync() {
        const openRoots = new Set(
            Array.from(document.querySelectorAll(modalSelector)).filter(isOpen)
        );

        activeRoots.forEach(root => {
            if (!openRoots.has(root)) {
                restoreRoot(root);
                activeRoots.delete(root);
            }
        });
        openRoots.forEach(root => {
            applyRootStyles(root);
            activeRoots.add(root);
        });

        if (activeRoots.size > 0) lockBody();
        else unlockBody();
    }

    function start() {
        sync();
        new MutationObserver(sync).observe(document.body, {
            subtree: true,
            childList: true,
            attributes: true,
            attributeFilter: ['class'],
        });
        window.addEventListener('resize', sync, { passive: true });
        window.addEventListener('orientationchange', sync, { passive: true });
    }

    if (document.body) start();
    else document.addEventListener('DOMContentLoaded', start, { once: true });
})();
