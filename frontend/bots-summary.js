/*
 * Bots summary: the plain-language explanation under a Bots card's Last scan result.
 *
 * Flat bot: how close it is to an entry (confirmations met, what is still waiting, the entry levels, what blocks it).
 * Bot in a position: what it is holding and what it is waiting for to exit (stop, target, exit signals, scheduled sell).
 * Badge: Bullish / Bearish (the side the bot is waiting to act on) or Holding.
 *
 * Display only. Everything comes from the scan log the server already sends; nothing is calculated or decided here.
 * Output is plain data (strings): the caller escapes it when rendering.
 */
(function () {
    'use strict';
    if (window.BotsSummary) return;

    const num = (value) => (Number.isFinite(Number(value)) ? Number(value) : null);
    const list = (items) => (Array.isArray(items) ? items.filter(Boolean) : []);
    const qtyText = (qty) => {
        const n = num(qty);
        if (n === null) return '';
        return n % 1 === 0 ? String(n) : n.toFixed(n < 1 ? 6 : 4).replace(/0+$/, '').replace(/\.$/, '');
    };
    const money = (value) => (num(value) === null ? '' : `$${Number(value).toFixed(2)}`);

    /* "RSI (RSI 28), MACD (flat)" for the indicators in one state. */
    function names(indicators, state) {
        return list(indicators)
            .filter((item) => String(item.status || '').toUpperCase() === state)
            .map((item) => `${item.name}${item.reason ? ` (${item.reason})` : ` ${String(item.signal || '').toLowerCase()}`}`)
            .join(', ');
    }

    /* Strategy rule texts say "completed daily bar". A bot running on another timeframe says what it really uses. */
    const BAR_WORD = { '1min': '1-minute', '5min': '5-minute', '10min': '10-minute', '15min': '15-minute', '30min': '30-minute', '1hour': '1-hour', '2hour': '2-hour', '4hour': '4-hour' };
    function adaptRule(text, timeframe) {
        const word = BAR_WORD[String(timeframe || '').toLowerCase()];
        return word ? String(text || '').replace(/\bdaily (bar|candle)\b/gi, (match, unit) => `${word} ${unit}`) : String(text || '');
    }

    const TREND = {
        MOVING_CLOSER: 'Moving closer to an entry since the last scan.',
        MOVING_AWAY: 'Moving away from an entry since the last scan.',
    };

    function positionLine(position) {
        if (!position) return '';
        const parts = [`${qtyText(position.qty)} @ ${money(position.avg_price)}`];
        if (num(position.current_price) !== null) parts.push(`now ${money(position.current_price)}`);
        if (num(position.unrealized_pl_pct) !== null) {
            const pl = Number(position.unrealized_pl_pct);
            parts.push(`${pl >= 0 ? '+' : ''}${pl.toFixed(2)}% (${position.unrealized_pl >= 0 ? '+' : '-'}$${Math.abs(Number(position.unrealized_pl) || 0).toFixed(2)})`);
        }
        return parts.join(' · ');
    }

    /* Manual (indicator / structural) bots. */
    function manual(log, opts) {
        const holding = log.phase === 'EXIT' || log.lifecycle === 'IN_POSITION';
        const word = String(log.target_direction || '').toUpperCase() === 'BEARISH' ? 'bearish' : 'bullish';
        const required = num(log.required_count) || 0;
        const have = num(log.confirmed_weight) !== null ? Number(log.confirmed_weight) : (num(log.confirmed_count) || 0);
        const remaining = num(log.remaining_count) || 0;
        const usesIndicators = Boolean(log.show_indicator_leg) && required > 0;
        const blockers = list(log.blockers).concat(log.entry_guard && log.entry_guard.reason ? [log.entry_guard.reason] : []);
        const confirmed = log.status === 'READY';
        const lines = [];
        const levels = list(log.live_levels).join(' · ');
        const watch = opts.exitWatch || {};

        if (!holding) {
            // How close the entry is comes first, then what blocks it: a blocked bot still says how many confirmations it has.
            const progress = usesIndicators
                ? `${have} of ${required} ${word} confirmations${remaining > 0 ? `, ${remaining} more needed` : ''}`
                : '';
            let headline;
            if (confirmed && !blockers.length) headline = usesIndicators ? `Entry conditions met: ${have} of ${required} ${word} confirmations.` : 'Entry conditions met.';
            else if (usesIndicators && remaining <= 0 && blockers.length) headline = `Entry conditions met (${have} of ${required} ${word} confirmations), but blocked: ${blockers[0]}`;
            else if (usesIndicators) headline = `Waiting for an entry: ${progress}.${blockers.length ? ` Blocked: ${blockers[0]}` : ''}`;
            else headline = blockers.length ? `Entry is blocked: ${blockers[0]}` : `Waiting for an entry${log.progress_label ? `: ${log.progress_label}` : '.'}`;
            if (!confirmed && !blockers.length && TREND[log.status]) headline += ` ${TREND[log.status]}`;
            // A flat bot is always looking for an entry, so it never reads as the opposite side. The badge is Waiting until the
            // entry is confirmed, then it names the side it enters on (Bullish for a long, Bearish for a put).
            const label = (side) => (side === 'bearish' ? 'Bearish' : 'Bullish');
            const toneOf = (side) => (side === 'bearish' ? 'sell' : 'buy');
            const badge = confirmed && !blockers.length ? { text: label(word), tone: toneOf(word) } : { text: 'Waiting', tone: 'neutral' };
            if (usesIndicators) {
                const c = names(log.indicators, 'CONFIRMED'), w = names(log.indicators, 'WAITING'), o = names(log.indicators, 'OPPOSING');
                if (c) lines.push([word === 'bearish' ? 'Bearish' : 'Bullish', c]);
                if (w) lines.push(['Waiting', w]);
                if (o) lines.push(['Opposing', o]);
            }
            if (levels) lines.push(['Entry levels', levels]);
            if (log.condition_logic) lines.push(['Rule', log.condition_logic]);
            if (blockers.length > 1) lines.push(['Also blocked by', blockers.slice(1).join(' · ')]);
            return { badge, headline, lines };
        }

        // In a position: what is held and what the bot is waiting for to exit.
        const waitingFor = [];
        const stop = num(watch.stop_loss), target = num(watch.take_profit);
        if (levels) waitingFor.push(levels);
        else if (stop > 0 || target > 0) waitingFor.push([stop > 0 ? `stop ${money(stop)}` : '', target > 0 ? `target ${money(target)}` : ''].filter(Boolean).join(' · '));
        if (usesIndicators) waitingFor.push(`${have} of ${required} bearish confirmations${remaining > 0 ? ` (${remaining} more needed)` : ''}`);
        const position = positionLine(opts.position);
        let headline;
        if (confirmed) headline = 'Exit conditions met: the position is sold on this scan.';
        else headline = `Waiting for an exit${waitingFor.length ? `: ${waitingFor.join(' or ')}.` : '.'}`;
        if (position) lines.push(['Position', position]);
        if (levels) lines.push(['Exit levels', levels]);
        else if (stop > 0 || target > 0) lines.push(['Exit levels', [stop > 0 ? `stop ${money(stop)}` : '', target > 0 ? `target ${money(target)}` : ''].filter(Boolean).join(' · ')]);
        if (watch.trail_enabled) lines.push(['Trailing stop', 'on: the stop rises with price and never moves down']);
        if (usesIndicators) {
            const c = names(log.indicators, 'CONFIRMED'), w = names(log.indicators, 'WAITING'), o = names(log.indicators, 'OPPOSING');
            if (c) lines.push(['Bearish', c]);
            if (w) lines.push(['Waiting', w]);
            if (o) lines.push(['Still bullish', o]);
        }
        if (log.condition_logic) lines.push(['Rule', log.condition_logic]);
        if (levels || stop > 0 || target > 0) lines.push(['Note', 'Stops are watched by software while the app runs with fresh data; they are not broker-side orders.']);
        if (blockers.length) lines.push(['Blocked by', blockers.join(' · ')]);
        return { badge: { text: confirmed ? 'Bearish' : 'Holding', tone: confirmed ? 'sell' : 'hold' }, headline, lines };
    }

    /* The badge already says Holding, so the strategy headline does not repeat it ("Holding · sells when…" -> "Sells when…"). */
    function withoutStatusWord(text) {
        let out = String(text || '').trim();
        out = out.replace(/^(?:Holding(?: overnight| intraday)?|Exit signal|Watching|Idle)\s*·\s*/i, '');
        out = out.replace(/^Holding (\d[\d.,]*) shares?\./i, (match, qty) => `${qty} share${Number(String(qty).replace(/,/g, '')) === 1 ? '' : 's'} held.`);
        return out ? out.charAt(0).toUpperCase() + out.slice(1) : out;
    }

    /* Strategy bots (Golden Cross, Supertrend, Session Rider, Malik): from the strategy's own scan view. */
    function strategy(log, view, opts) {
        const status = String(view.status || '');
        const holding = Boolean(view.position) || /^HOLDING|^EXIT_/.test(status);
        const metrics = list(view.metrics).map((m) => `${m.label} ${m.value}`).join(' · ');
        const rawRules = view.rules || {};
        const rules = { ...rawRules, entry: adaptRule(rawRules.entry, opts.timeframe), exit: adaptRule(rawRules.exit, opts.timeframe) };
        const sched = view.schedule || {};
        const lines = [];
        const blockers = list(log.blockers);
        const confirmed = status === 'ENTRY_READY' || status === 'EXIT_SIGNAL';
        const position = view.position ? `entry ${view.position.entry} · now ${view.position.current}${view.position.pl_pct != null ? ` · ${view.position.pl_pct > 0 ? '+' : ''}${view.position.pl_pct}%` : ''}` : '';

        if (view.kind === 'allocation') {
            const legs = list((view.allocation || {}).legs).map((leg) => `${leg.symbol} target ${leg.target_pct}% · held ${leg.held_pct == null ? '—' : Math.round(leg.held_pct) + '%'}${leg.pending ? ' · order working' : ''}`).join('; ');
            if (legs) lines.push(['Targets vs held', legs]);
            if (metrics) lines.push(['Now', metrics]);
            if ((view.allocation || {}).execution_time) lines.push(['Executes daily', view.allocation.execution_time]);
            if ((view.allocation || {}).next_decision) lines.push(['Next decision', view.allocation.next_decision]);
            if (blockers.length) lines.push(['Blocked by', blockers.join(' · ')]);
            return { badge: { text: view.status_label || 'Scheduled', tone: 'neutral' }, headline: view.headline || '', lines };
        }
        if (view.kind === 'schedule') {
            if (position) lines.push(['Position', position]);
            if (sched.next_label) lines.push([`Next ${String(sched.next_event || '').toLowerCase()}`, sched.next_label]);
            const sell = list(view.exit_watch).find((row) => row.kind === 'time');
            if (holding && sell) lines.push(['Waiting for', `scheduled sell ${sell.value}`]);
        } else {
            // A moving-average strategy: say in words how far the fast line is from crossing the slow one.
            const rawMetrics = list(view.metrics);
            const gap = rawMetrics.find((m) => m.label === 'Gap');
            const gapValue = gap ? parseFloat(String(gap.value).replace('%', '')) : NaN;
            if (!holding && gap && rawMetrics.length >= 3 && Number.isFinite(gapValue)) {
                const fast = rawMetrics[0].label, slow = rawMetrics[1].label, size = Math.abs(gapValue).toFixed(2);
                lines.push(['How close', gapValue < 0
                    ? `${fast} is ${size}% below ${slow}. A buy needs ${fast} to cross above ${slow}.`
                    : `${fast} is already ${size}% above ${slow}. It buys only on a fresh cross, so it waits for the next one.`]);
            }
            if (metrics) lines.push([holding ? 'Exit numbers' : 'Numbers', metrics]);
            if (position) lines.push(['Position', position]);
            list(view.exit_watch).forEach((row) => lines.push([row.label, `${row.value}${row.near_pct > 0 && row.kind !== 'time' ? ` (${row.near_pct}% of the way)` : ''}`]));
            if (holding && rules.exit) lines.push(['Waiting for', rules.exit]);
            if (!holding && rules.entry) lines.push(['Waiting for', rules.entry]);
        }
        if (view.position && view.position.stop_note) lines.push(['Note', view.position.stop_note]);
        if (view.untested) lines.push(['Settings', view.untested]);
        if (blockers.length) lines.push(['Blocked by', blockers.join(' · ')]);
        // Flat: Waiting (it is looking for an entry) until the strategy signals an entry, then Bullish.
        const flatBadge = confirmed && !blockers.length ? { text: 'Bullish', tone: 'buy' } : { text: 'Waiting', tone: 'neutral' };
        return {
            badge: holding
                ? { text: status === 'EXIT_SIGNAL' ? 'Bearish' : 'Holding', tone: status === 'EXIT_SIGNAL' ? 'sell' : 'hold' }
                : flatBadge,
            headline: holding ? withoutStatusWord(view.headline) : (view.headline || ''),
            lines,
        };
    }

    /* log: the (last) scan log; opts: { exitWatch, position, schedule } */
    function build(log, opts) {
        if (!log || typeof log !== 'object') return null;
        const view = log.strategy_log && typeof log.strategy_log === 'object' ? log.strategy_log : null;
        return view ? strategy(log, view, opts || {}) : manual(log, opts || {});
    }

    window.BotsSummary = { build, adaptRule };
})();
