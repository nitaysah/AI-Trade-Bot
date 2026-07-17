(function () {
    const CACHE_KEY = 'alpacaConnectionStatusV1';
    const TTL_MS = 5 * 60 * 1000;

    function normalizeStatus(data) {
        if (!data || typeof data !== 'object') return null;
        const hasKeys = Boolean(data.has_keys);
        const connected = Boolean(data.brokerConnected || data.brokerStatus === 'connected' || (!data.simulation && hasKeys));
        const pending = !connected && hasKeys;
        return {
            connected,
            has_keys: hasKeys,
            simulation: Boolean(data.simulation),
            status: connected ? 'connected' : pending ? 'pending' : 'disconnected',
            reason: data.brokerStatusReason || data.reason || '',
            updatedAt: Date.now(),
        };
    }

    function read() {
        try {
            const cached = JSON.parse(sessionStorage.getItem(CACHE_KEY) || 'null');
            if (!cached || !cached.updatedAt) return null;
            return cached;
        } catch (e) {
            return null;
        }
    }

    function isFresh(cached = read()) {
        return Boolean(cached && Date.now() - cached.updatedAt < TTL_MS);
    }

    function writeFromData(data) {
        const status = normalizeStatus(data);
        if (!status) return null;
        try {
            sessionStorage.setItem(CACHE_KEY, JSON.stringify(status));
        } catch (e) {}
        return status;
    }

    function clear() {
        try {
            sessionStorage.removeItem(CACHE_KEY);
        } catch (e) {}
    }

    function render(status = read()) {
        if (!status) return false;
        const redirectBtn = document.getElementById('alpacaLinkRedirectBtn');
        const statusEl = document.getElementById('alpacaLinkStatus');
        const dotEl = document.getElementById('alpacaStatusDot');
        const textEl = document.getElementById('alpacaStatusText');
        const btnUnlink = document.getElementById('btnUnlinkAlpaca');
        if (!statusEl || !dotEl || !textEl) return false;

        statusEl.classList.remove('hidden');
        statusEl.style.display = 'flex';
        statusEl.title = '';

        if (status.status === 'connected') {
            if (redirectBtn) redirectBtn.classList.add('hidden');
            statusEl.className = 'flex items-center justify-center h-9 text-xs font-black px-5 rounded-full border-2 shadow-sm whitespace-nowrap transition-all duration-500 uppercase tracking-wider bg-emerald-50 text-emerald-600 border-emerald-100';
            dotEl.className = 'h-2 w-2 rounded-full mr-2 bg-emerald-500 animate-pulse shadow-[0_0_8px_rgba(16,185,129,0.8)]';
            textEl.textContent = 'Alpaca';
            if (btnUnlink) btnUnlink.classList.remove('hidden');
            return true;
        }

        if (status.status === 'pending') {
            if (redirectBtn) redirectBtn.classList.add('hidden');
            statusEl.className = 'flex items-center justify-center h-9 text-xs font-black px-5 rounded-full bg-amber-50 text-amber-600 border-2 border-amber-100 shadow-sm whitespace-nowrap transition-all duration-500 uppercase tracking-wider';
            dotEl.className = 'h-2 w-2 rounded-full mr-2 bg-amber-500 animate-pulse';
            textEl.textContent = status.reason ? 'ALPACA CHECK' : 'RETRYING...';
            statusEl.title = status.reason || 'Alpaca keys are saved, but the latest account check did not confirm a live connection yet.';
            if (btnUnlink) btnUnlink.classList.remove('hidden');
            return true;
        }

        if (redirectBtn) {
            redirectBtn.classList.remove('hidden');
            statusEl.classList.add('hidden');
            statusEl.style.display = '';
        } else {
            statusEl.className = 'flex items-center justify-center h-9 text-xs font-black px-5 rounded-full bg-rose-50 text-rose-600 border-2 border-rose-100 shadow-sm whitespace-nowrap transition-all duration-500 uppercase tracking-wider';
            dotEl.className = 'h-2 w-2 rounded-full mr-2 bg-rose-500 animate-pulse';
            textEl.textContent = 'SIMULATION';
            if (btnUnlink) btnUnlink.classList.add('hidden');
        }
        return true;
    }

    window.AlpacaStatusCache = {
        read,
        isFresh,
        writeFromData,
        clear,
        render,
        ttlMs: TTL_MS,
    };
})();
