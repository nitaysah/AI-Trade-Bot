/* Bots page research badges: Earnings and Fundamentals on each stock bot card.
 *
 * The dialogs, badge wording and formatting are shared with the Screener (research-shared.js). This file only fetches
 * the compact badge data for the user's bots (earnings entry + fundamentals assessment per ticker) and keeps it fresh. */
(function () {
    'use strict';

    BotBullsResearch.configure({
        earningsUrl: (symbol) => `/api/bots/earnings/${symbol}`,
        fundamentalsUrl: (symbol) => `/api/bots/fundamentals/${symbol}`,
        cachePrefix: 'bot',
    });

    const BADGE_REFRESH_MS = 5 * 60 * 1000;
    const badges = { data: {}, fundamentals: {}, loadedAt: 0, loading: false, error: false, failures: 0, polls: 0, timer: null, onChange: null, tickers: [] };
    const RETRY_MS = [4000, 10000, 20000, 40000, 60000];  // after a failed or not-yet-authorized request
    const POLL_MS = 5000, MAX_POLLS = 6;                   // while some stock bot still has no result

    window.openBotEarnings = (ticker) => BotBullsResearch.openEarnings(ticker, badges.data[String(ticker || '').toUpperCase()] || null);
    window.openBotFundamentals = (ticker) => BotBullsResearch.openFundamentals(ticker);

    /* The badges for one stock bot card, each only when its setting is on (crypto has neither). */
    function researchBadgesHtml(ticker, isCryptoBot, settings) {
        if (isCryptoBot) return '';
        const cfg = settings || {};
        return (cfg.fundamental_watch_enabled !== false ? BotBullsResearch.fundamentalsBadge(ticker, badges.fundamentals[ticker] || null) : '')
            + (cfg.earnings_watch_enabled !== false ? BotBullsResearch.earningsBadge(ticker, badges.data[ticker] || null, badges.error) : '');
    }

    const unresolved = () => badges.tickers.filter((t) => {
        const earnings = badges.data[t];
        const fundamentals = badges.fundamentals[t];
        const earningsPending = !earnings || (String(earnings.status || 'waiting').toLowerCase() === 'waiting' && !earnings.initialized);
        const fundamentalsPending = badges.fundamentals && t in badges.fundamentals && String(fundamentals?.status || 'waiting').toLowerCase() === 'waiting';
        return earningsPending || fundamentalsPending;
    });

    function schedule(ms) {
        clearTimeout(badges.timer);
        badges.timer = setTimeout(() => load(true), ms * (0.85 + Math.random() * 0.3));
    }

    function redraw() { if (typeof badges.onChange === 'function') badges.onChange(); }

    async function load(force) {
        if (badges.loading) return;
        if (!force && badges.loadedAt && Date.now() - badges.loadedAt < BADGE_REFRESH_MS) return;
        badges.loading = true;
        try {
            const headers = await getAuthHeaders();
            if (!headers || !headers.Authorization) {
                // Signed-in user not restored yet (cold page load): not an attempt, look again shortly.
                badges.loading = false;
                if (badges.failures < RETRY_MS.length) schedule(RETRY_MS[badges.failures++]);
                return;
            }
            const response = await fetch(`${API_BASE}/api/bots/earnings-badges`, { headers });
            if (!response.ok) throw new Error(`HTTP ${response.status}`);
            const payload = await response.json();
            const before = JSON.stringify([badges.data, badges.fundamentals]);
            badges.data = payload.badges || {};
            badges.fundamentals = payload.fundamentals || {};
            badges.error = false;
            badges.failures = 0;
            badges.loadedAt = Date.now();
            if (JSON.stringify([badges.data, badges.fundamentals]) !== before) redraw();
            // Some bots may still be resolving upstream: look again soon, a few times, then at the normal pace.
            if (unresolved().length && badges.polls < MAX_POLLS) { badges.polls += 1; schedule(POLL_MS); } else badges.polls = 0;
        } catch (error) {
            badges.error = true;
            const wait = RETRY_MS[Math.min(badges.failures, RETRY_MS.length - 1)];
            badges.failures += 1;
            redraw();  // so "Checking…" becomes "Retrying…"
            schedule(wait);
        } finally {
            badges.loading = false;
        }
    }

    /* Called on every card render with the stock bots shown; refreshes at most every few minutes (and retries failures). */
    function ensure(onChange, tickers) {
        badges.onChange = onChange;
        if (Array.isArray(tickers)) badges.tickers = tickers;
        return load(false);
    }

    window.BotResearch = { researchBadgesHtml, ensure };
})();
