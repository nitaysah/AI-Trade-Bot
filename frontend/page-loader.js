/*
 * Shows "Refreshing…" with the spinning BotBulls logo in place of the footer while a page is loading, and again as
 * soon as you leave for another page or reload. The footer appears only once the page content is ready.
 * Purely visual: it never delays or blocks navigation, and a fallback timer always brings the footer back.
 */
(function () {
    'use strict';
    const root = document.documentElement;
    const FALLBACK_MS = 15000;
    let timer = null;

    function contentReady() {
        if (document.readyState !== 'complete') return false;
        if (root.classList.contains('auth-pending')) return false;
        const gate = document.getElementById('dashboardContent');
        return !gate || !gate.classList.contains('hidden');
    }

    function markReady() { root.classList.add('page-ready'); }
    function markLoading() { root.classList.remove('page-ready'); }

    function watch() {
        clearInterval(timer);
        const startedAt = Date.now();
        timer = setInterval(() => {
            if (contentReady() || Date.now() - startedAt > FALLBACK_MS) {
                clearInterval(timer);
                markReady();
            }
        }, 150);
    }

    // Leaving the page: show the loader right away. If the navigation turns out not to happen (cancelled, a download),
    // the watcher brings the footer back.
    function leaving() { markLoading(); watch(); }

    document.addEventListener('click', (event) => {
        if (event.defaultPrevented || event.button !== 0 || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return;
        const link = event.target.closest && event.target.closest('a[href]');
        if (!link || (link.target && link.target !== '_self') || link.hasAttribute('download')) return;
        const url = new URL(link.href, location.href);
        if (url.origin !== location.origin || /\.html?$/i.test(url.pathname) === false) return;
        if (url.pathname === location.pathname && url.search === location.search) return;
        leaving();
    }, true);
    window.addEventListener('beforeunload', leaving);
    window.addEventListener('pageshow', (event) => { if (event.persisted) { markLoading(); watch(); } });

    watch();
})();
