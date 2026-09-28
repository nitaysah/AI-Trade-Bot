(function initializeAuthGuard() {
    const AUTH_GUARD_TIMEOUT_MS = 12000;
    let settled = false;
    let timeoutId = null;

    function clearGuardTimeout() {
        if (!timeoutId) return;
        window.clearTimeout(timeoutId);
        timeoutId = null;
    }

    function release() {
        if (settled) return;
        settled = true;
        clearGuardTimeout();
        document.documentElement.classList.remove('auth-pending');
    }

    function redirectToSignIn(reason = '') {
        if (settled) return;
        settled = true;
        clearGuardTimeout();
        const target = new URL('index.html', window.location.href);
        if (reason) target.searchParams.set('auth_error', reason);
        window.location.replace(target.toString());
    }

    timeoutId = window.setTimeout(() => {
        console.error('[auth] Firebase session check timed out; returning to sign-in.');
        redirectToSignIn('session_check_timeout');
    }, AUTH_GUARD_TIMEOUT_MS);

    window.BotBullsAuthGuard = { release, redirectToSignIn };
})();
