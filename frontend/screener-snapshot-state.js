/* Pure snapshot rules shared by the Screener UI and local regression tests. */
(function (root) {
    'use strict';
    const isCurrent = scan => scan?.evaluation_status === 'valid' && scan?.data_status === 'current';
    const enabled = settings => settings?.enabled ?? !settings?.paused;
    const needsAttention = (scan, settings) => enabled(settings) &&
        !isCurrent(scan) && !['session_closed', 'paused'].includes(scan?.data_status);
    const sequence = snapshot => Number(snapshot?.snapshot_seq) || 0;
    function accepts(snapshot, partitions) {
        const targets = snapshot.partition === 'all' || !snapshot.partition
            ? ['regular', 'options'] : [snapshot.partition];
        return targets.every(partition => sequence(snapshot) >= sequence(partitions[partition]));
    }
    function summary(snapshot) {
        const symbols = snapshot.tradelist || [];
        const scans = snapshot.botScans || snapshot.bot_scans || {};
        const settings = snapshot.ticker_settings || {};
        const active = symbols.filter(symbol => enabled(settings[symbol]));
        const current = active.map(symbol => scans[symbol]).filter(isCurrent);
        const degraded = active.filter(symbol => ['degraded', 'stale_reference'].includes(scans[symbol]?.data_status)).length;
        const attention = active.filter(symbol => needsAttention(scans[symbol], settings[symbol])).length;
        const base = snapshot.screener_summary || {};
        const monitoring = { ...base.monitoring };
        if (degraded && monitoring.status === 'healthy') monitoring.status = 'degraded';
        return {
            ...base, monitoring,
            profiles: { total: symbols.length, enabled: active.length, paused: symbols.length - active.length },
            setups: {
                bullish: current.filter(scan => scan.direction === 'BULLISH').length,
                bearish: current.filter(scan => scan.direction === 'BEARISH').length,
                neutral: current.filter(scan => scan.direction === 'NEUTRAL').length,
                valid: current.length, unavailable: active.length - current.length, degraded, attention
            }
        };
    }
    function asReference(snapshot) {
        const result = JSON.parse(JSON.stringify(snapshot));
        const scans = result.botScans || result.bot_scans || {};
        for (const scan of Object.values(scans)) {
            if (scan.data_status !== 'paused') {
                scan.data_status = 'stale_reference';
                scan.stale_reason = 'Showing saved analysis; waiting for a fresh snapshot.';
            }
        }
        result.bot_scans = result.botScans = scans;
        return result;
    }
    const api = { isCurrent, enabled, needsAttention, sequence, accepts, summary, asReference,
        cacheKey: uid => uid ? `screenerDataCache:${encodeURIComponent(uid)}` : null };
    if (typeof module !== 'undefined' && module.exports) module.exports = api;
    else root.ScreenerSnapshotState = api;
})(typeof window === 'undefined' ? globalThis : window);
