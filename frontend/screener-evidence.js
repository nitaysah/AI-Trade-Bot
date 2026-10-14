/* Screener Buy Evidence / Sell Evidence.
 *
 * The Last scan result shows both sides of every alert source in the same two sections, titled after what the
 * profile is set up to do ("Buy Evidence · Indicators", "Sell Evidence · ATR", "Buy Evidence · Golden Cross", ...).
 * Each section has a status badge (Confirmed / Waiting / Blocked), an alerts on/off badge, and the evidence lines.
 *
 * Statuses come from the same qualification the alert uses (strategy_qualification.qualified + direction), so a
 * Confirmed badge means that side will alert; a section is never "confirmed" from raw counts alone.
 * Pure display: no network, no orders, no side effects. Depends on screener.js globals sanitizePlainText and
 * formatAssetPrice, which exist by the time a card renders. */
(function () {
    'use strict';

    const esc = (value) => sanitizePlainText(String(value ?? ''));
    const num = (value) => (Number.isFinite(Number(value)) ? Number(value) : null);

    const BUY_MODE_LABELS = {
        indicator: 'Indicators', atr: 'ATR', hybrid_atr: 'ATR + Indicators', percentage: 'Percentage', hybrid_pct: 'Percentage + Indicators',
        pivot_points: 'Pivot', hybrid_pivot: 'Pivot + Indicators', high_low: 'High/Low', hybrid_high_low: 'High/Low + Indicators',
        swing_points: 'Swing', hybrid_swing: 'Swing + Indicators', custom_price: 'Custom price',
    };
    const SELL_MODE_LABELS = {
        indicator: 'Indicators', sltp: 'Fixed levels (ATR)', atr: 'ATR', hybrid: 'ATR + Indicators', hybrid_atr: 'ATR + Indicators',
        percentage: 'Percentage', hybrid_pct: 'Percentage + Indicators', custom_sltp: 'Custom SL/TP', custom_underlying_sltp: 'Custom SL/TP',
        pivot_points: 'Pivot', hybrid_pivot: 'Pivot + Indicators', high_low: 'High/Low', hybrid_high_low: 'High/Low + Indicators',
        swing_points: 'Swing', hybrid_swing: 'Swing + Indicators',
    };
    const BUY_USES_INDICATORS = new Set(['indicator', 'hybrid_atr', 'hybrid_pct', 'hybrid_pivot', 'hybrid_high_low', 'hybrid_swing']);
    const SELL_USES_INDICATORS = new Set(['indicator', 'hybrid', 'hybrid_atr', 'hybrid_pct', 'hybrid_pivot', 'hybrid_high_low', 'hybrid_swing']);
    // Exits decided by price levels alone: bearish indicators are not their trigger (the backend ignores that sell).
    const SELL_LEVELS_ONLY = new Set(['sltp', 'atr', 'percentage', 'custom_sltp', 'custom_underlying_sltp', 'pivot_points', 'high_low', 'swing_points']);

    const TONES = {
        buy: { border: 'border-emerald-100', bg: 'bg-emerald-50/60', title: 'text-emerald-800', confirmed: 'text-emerald-700 bg-emerald-100 border-emerald-300' },
        sell: { border: 'border-rose-100', bg: 'bg-rose-50/60', title: 'text-rose-800', confirmed: 'text-rose-700 bg-rose-100 border-rose-300' },
    };
    TONES.buy.active = 'text-emerald-700 bg-white/70 border-emerald-300';
    TONES.sell.active = 'text-rose-700 bg-white/70 border-rose-300';
    const STATUS_STYLE = {
        WAITING: { text: 'Waiting', cls: 'text-slate-600 bg-slate-100 border-slate-200' },
        BLOCKED: { text: 'Blocked', cls: 'text-amber-800 bg-amber-100 border-amber-300' },
    };

    function dots(indicators, wanted, marker, label) {
        const items = indicators.filter((item) => wanted(String(item.signal || '').toUpperCase()));
        if (!items.length) return '';
        const text = items.map((item) => `${marker} ${item.name} ${String(item.signal || '').toUpperCase()}${item.reason ? ` (${item.reason})` : ''}`).join(', ');
        return `<span class="leading-snug"><strong class="font-bold text-slate-500">${esc(label)}:</strong> ${esc(text)}</span>`;
    }

    function qualificationOf(ctx) {
        const q = (ctx.scanLog && ctx.scanLog.strategy_qualification && typeof ctx.scanLog.strategy_qualification === 'object'
            ? ctx.scanLog.strategy_qualification
            : ctx.scan && ctx.scan.strategy_qualification) || {};
        return { qualified: Boolean(q.qualified ?? q.eligible), direction: String(q.direction || '').toUpperCase() };
    }

    function alertsOn(ctx, side) {
        const watch = String(ctx.watch || 'BOTH').toUpperCase();
        return watch === 'BOTH' || watch === (side === 'buy' ? 'BULLISH' : 'BEARISH');
    }

    /* Bots: how an open position is watched for its exit. A price-based exit (stop, target, trailing, levels) is watched on the
       fast scan; a purely indicator-based exit can only change on a completed candle, so it follows the bot's own interval. */
    function scanEvery(seconds) {
        const total = Math.max(1, Math.round(Number(seconds) || 0));
        if (total <= 90) return `${total}s`;
        if (total < 3600) return `${Math.round(total / 60)}m`;
        return `${Math.round(total / 3600)}h`;
    }
    function exitScanBadge(info) {
        if (!info || !Number(info.interval_sec) || !['price', 'indicator'].includes(info.kind)) return '';
        const price = info.kind === 'price';
        const text = `${price ? 'Price watch' : 'Exit scan'} · ${scanEvery(info.interval_sec)}`;
        const title = price
            ? `The software watcher checks the price for this exit about every ${scanEvery(info.interval_sec)} while the app runs. It is not a broker-side stop and does not guarantee a fill at the stop price.`
            : `This exit only changes when a candle completes, so the bot checks it on its own scan interval (${scanEvery(info.interval_sec)}).`;
        return `<span class="inline-flex rounded border border-slate-200 bg-white/70 px-1.5 py-0.5 text-[0.5rem] sm:text-[0.55rem] font-black uppercase text-slate-600 whitespace-nowrap" data-exit-scan="${info.kind}" title="${esc(title)}">${esc(text)}</span>`;
    }

    function section(ctx, side, setup, status, lines) {
        const tone = TONES[side];
        const style = status === 'CONFIRMED' ? { text: 'Confirmed', cls: tone.confirmed }
            : status === 'ACTIVE' ? { text: 'Active', cls: tone.active }
            : (STATUS_STYLE[status] || STATUS_STYLE.WAITING);
        const on = alertsOn(ctx, side);
        const word = side === 'buy' ? 'Buy' : 'Sell';
        return `<div class="rounded-md border ${tone.border} ${tone.bg} px-1.5 py-0.5 flex flex-col ${ctx.bot ? 'gap-0 sm:gap-0.5' : 'gap-0.5'} text-slate-950 min-w-0" data-evidence-side="${side}" data-evidence-status="${status}">
            <div class="flex items-center gap-1.5 flex-wrap">
                <span class="text-[0.6rem] sm:text-[0.68rem] font-black uppercase tracking-wider ${tone.title}">${word} Evidence</span>
                <span class="inline-flex rounded border border-slate-200 bg-white/70 px-1.5 py-0.5 text-[0.55rem] font-black uppercase text-slate-700">${esc(setup)}</span>
                <span class="inline-flex rounded border px-1.5 py-0.5 text-[0.55rem] font-black uppercase ${style.cls}">${style.text}</span>
                ${ctx.bot && side === 'sell' ? exitScanBadge(ctx.exitScan) : ''}
                ${ctx.bot ? '' : `<span class="ml-auto text-[0.5rem] font-black uppercase tracking-wide ${on ? 'text-slate-500' : 'text-amber-700'}" title="${word} alerts are ${on ? 'on' : 'off'} for this ticker">${word} alerts ${on ? 'on' : 'off'}</span>`}
            </div>
            ${lines.filter(Boolean).join('')}
        </div>`;
    }

    const line = (label, text) => (text ? `<span class="leading-snug"><strong class="font-bold text-slate-500">${esc(label)}:</strong> ${esc(text)}</span>` : '');

    /* ── Indicators ─────────────────────────────────────────────────────── */
    function indicatorSide(ctx, side) {
        const settings = ctx.settings || {};
        const bullish = side === 'buy';
        const qual = qualificationOf(ctx);
        const mode = String((bullish ? settings.buy_mode : settings.sell_mode) || 'indicator').toLowerCase();
        const setup = (bullish ? BUY_MODE_LABELS : SELL_MODE_LABELS)[mode] || (bullish ? 'Indicators' : 'Indicators');
        const usesIndicators = (bullish ? BUY_USES_INDICATORS : SELL_USES_INDICATORS).has(mode);
        const levelsOnly = !bullish && SELL_LEVELS_ONLY.has(mode);
        const want = bullish ? 'BULLISH' : 'BEARISH';
        const have = bullish ? ctx.counts.bullHave : ctx.counts.bearHave;
        const need = bullish ? ctx.counts.bullNeed : ctx.counts.bearNeed;
        const countsMet = need > 0 && have >= need;
        const technical = ctx.technical || {};
        const hasTechnical = Object.prototype.hasOwnProperty.call(technical, 'eligible');
        const qualifiedHere = qual.qualified && qual.direction === want;
        const lines = [];
        let status = 'WAITING';

        if (usesIndicators) {
            const word = bullish ? 'bullish' : 'bearish';
            if (need <= 0) {
                lines.push(line('Indicators', 'none selected — pick indicators to enable this side'));
            } else {
                status = qualifiedHere ? 'CONFIRMED' : (countsMet && hasTechnical ? 'BLOCKED' : 'WAITING');
                const note = qualifiedHere ? ' — threshold met' : (countsMet && hasTechnical ? ' — threshold met, but blocked' : '');
                lines.push(`<span class="font-bold">${have}/${need} ${word} confirmations${esc(note)}</span>`);
                if (status === 'BLOCKED' && technical.reason) lines.push(line('Blocked', String(technical.reason).replace(/[.!?]+$/, '')));
                lines.push(dots(ctx.indicators, (s) => s === want, bullish ? '🟢' : '🔴', bullish ? 'Bullish' : 'Bearish'));
                lines.push(dots(ctx.indicators, (s) => s === (bullish ? 'BEARISH' : 'BULLISH'), bullish ? '🔴' : '🟢', 'Opposing'));
                lines.push(dots(ctx.indicators, (s) => s !== 'BULLISH' && s !== 'BEARISH', '🟡', 'Waiting'));
            }
        }
        // Price-level entries (ATR, percentage, pivot, ...) show their trigger levels; the backend only sends them for those modes.
        if (bullish && Array.isArray(ctx.entryLevels) && ctx.entryLevels.length) {
            ctx.entryLevels.filter(Boolean).forEach((text) => lines.push(`<span class="leading-snug">${esc(text)}</span>`));
        }
        if (!bullish) {
            const exit = ctx.exitEvidence || {};
            if (exit.mode) {
                const money = (value) => `$${formatAssetPrice(Number(value), ctx.isCrypto)}`;
                const pct = (value) => (num(value) !== null ? `${Number(value).toFixed(2)}%` : '');
                const reached = Boolean(exit.triggered) && !exit.reference;
                if (reached) status = 'CONFIRMED';
                if (exit.available && Number(exit.stop) > 0 && Number(exit.target) > 0) {
                    const lead = exit.reference ? 'If entered at the current price'
                        : (num(exit.entry_price) !== null ? `${ctx.bot ? 'Open position' : 'Tracked alert position'} from ${money(exit.entry_price)}` : (ctx.bot ? 'Open position levels' : 'Tracked alert position levels'));
                    lines.push(line(lead, `stop ${money(exit.stop)} (${pct(exit.stop_distance_pct)} away) · target ${money(exit.target)} (${pct(exit.target_distance_pct)} away) · nearest ${exit.nearest_level === 'TP' ? 'target' : 'stop'}`));
                } else if (exit.text) {
                    lines.push(line('Levels', exit.text));
                }
                if (Number(exit.atr) > 0 && exit.atr_stop_multiplier) {
                    lines.push(line('ATR', `${formatAssetPrice(Number(exit.atr), ctx.isCrypto)} · stop ${exit.atr_stop_multiplier}× · trail ${exit.atr_trail_enabled ? `${exit.atr_trail_multiplier}×` : 'off'} · target ${exit.take_profit_multiplier}×`));
                }
                if (levelsOnly) {
                    lines.push(`<span class="leading-snug text-slate-500">${exit.reference
                        ? 'Sell alert fires when these levels are reached after a Buy alert opens a position.'
                        : 'Sell alert fires when price reaches the stop or target.'} Bearish indicators are not used for this exit.</span>`);
                }
            }
            if (levelsOnly && !lines.length) lines.push(line('Exit', 'Waiting for a valid completed-candle price to show the exit levels.'));
        }
        return section(ctx, side, setup, status, lines);
    }

    /* ── Strategy ───────────────────────────────────────────────────────── */
    function strategySide(ctx, side) {
        const bullish = side === 'buy';
        const info = ctx.strategy.info || {};
        const def = ctx.strategy.definition || null;
        const qual = qualificationOf(ctx);
        const want = bullish ? 'BULLISH' : 'BEARISH';
        const name = (def && def.label) || info.label || 'Strategy';
        const state = String(info.state || '').toUpperCase();
        const values = info.values && typeof info.values === 'object' ? info.values : {};
        const lines = [];
        // A strategy publishes a state, and only a CHANGE of state alerts. Age 0 is a new signal (Confirmed); a regime that
        // began earlier is Active, which is true but not news.
        const age0 = num(info.signal_age_bars);
        const inState = qual.qualified && qual.direction === want;
        const status = !inState ? 'WAITING' : (age0 === 0 || age0 === null ? 'CONFIRMED' : 'ACTIVE');

        if (String(info.kind || '') === 'schedule') {
            const sched = info.schedule && typeof info.schedule === 'object' ? info.schedule : {};
            lines.push(line(bullish ? 'Buy time' : 'Sell time', bullish ? sched.buy_label : sched.sell_label));
            return section(ctx, side, name, 'WAITING', lines);
        }
        if (def) lines.push(line(bullish ? 'Entry rule' : 'Exit rule', bullish ? def.entry_rule : def.exit_rule));
        if (state === 'LONG' || state === 'FLAT' || state === 'UNKNOWN') {
            const age = num(info.signal_age_bars);
            const stateText = state === 'UNKNOWN' ? 'no entry or exit event yet in the data window'
                : `${state === 'LONG' ? 'long' : 'flat'}${age === 0 ? ' since the latest candle' : age !== null ? ` for ${age} bar${age === 1 ? '' : 's'}` : ''}`;
            lines.push(line('State', stateText));
        }
        const fast = num(values.fast), slow = num(values.slow), close = num(values.close), st = num(values.st), ema = num(values.ema);
        const signed = (v) => `${v >= 0 ? '+' : ''}${v.toFixed(2)}%`;
        if (fast !== null && slow !== null && slow > 0) {
            const gap = ((fast - slow) / slow) * 100;
            lines.push(line('Now', `fast ${formatAssetPrice(fast, ctx.isCrypto)} · slow ${formatAssetPrice(slow, ctx.isCrypto)} (fast ${signed(gap)} vs slow)`));
            lines.push(line(bullish ? 'Entry check' : 'Exit check', bullish
                ? (state === 'LONG' ? 'already long; a new entry needs a fresh cross above slow' : 'enters on a fresh cross of fast above slow')
                : 'exits when fast falls below slow'));
        } else if (close !== null && st !== null) {
            const dist = ((close - st) / st) * 100;
            lines.push(line('Now', `close ${formatAssetPrice(close, ctx.isCrypto)} · Supertrend ${formatAssetPrice(st, ctx.isCrypto)} (${signed(dist)})${ema !== null ? ` · EMA ${formatAssetPrice(ema, ctx.isCrypto)}` : ''}`));
            lines.push(line(bullish ? 'Entry check' : 'Exit check', bullish
                ? 'needs price above the 200 EMA and the Supertrend turning up'
                : 'exits when the Supertrend flips down'));
        }
        const warm = info.warmup && typeof info.warmup === 'object' ? info.warmup : {};
        if (String(info.status || '').toUpperCase() !== 'READY' && Number(warm.required) > 0) {
            lines.push(`<span class="leading-snug text-amber-700">History: ${Number(warm.available) || 0} of ${Number(warm.required)} completed bars needed</span>`);
        }
        return section(ctx, side, name, status, lines);
    }

    /* ── Event ──────────────────────────────────────────────────────────── */
    function eventSide(ctx, side) {
        const bullish = side === 'buy';
        const ev = ctx.event || {};
        const qual = qualificationOf(ctx);
        const gates = ev.gates && typeof ev.gates === 'object' ? ev.gates : {};
        const buyGate = num(gates.min_bullish_pct) ?? 65;
        const sellGate = num(gates.max_bearish_pct) ?? 35;
        const confGate = Math.round((num(gates.min_confidence) ?? num(ctx.settings.event_min_confidence) ?? 0.55) * 100);
        const days = num(gates.max_age_days) ?? num(ctx.settings.event_max_age_days) ?? 10;
        const want = bullish ? 'BULLISH' : 'BEARISH';
        const status = qual.qualified && qual.direction === want ? 'CONFIRMED' : 'WAITING';
        const sentiment = num(ev.sentiment_pct);
        const confidence = num(ev.confidence);
        const age = num(ev.age_hours);
        const mark = (ok) => (ok ? '✓' : '✗');
        const lines = [];
        if (sentiment === null) {
            lines.push(line('News', 'waiting for a current, directly relevant catalyst'));
        } else {
            const sentOk = bullish ? sentiment >= buyGate : sentiment <= sellGate;
            lines.push(line('Sentiment', `${Math.round(sentiment)}/100 — needs ${bullish ? `${buyGate}+` : `${sellGate} or lower`} ${mark(sentOk)}`));
            if (confidence !== null) lines.push(line('Confidence', `${Math.round(confidence * 100)}% — needs ${confGate}%+ ${mark(confidence * 100 >= confGate)}`));
            if (age !== null) lines.push(line('Catalyst age', `${age < 48 ? `${age.toFixed(1)}h` : `${(age / 24).toFixed(1)}d`} — limit ${days}d ${mark(age <= days * 24)}`));
            const tone = String(ev.direction || '').toUpperCase();
            // The catalyst's overall reason belongs to the side it points at (the analysis text is in the Watch summary).
            if (status !== 'CONFIRMED' && ev.reason && (tone === want || !tone || tone === 'NEUTRAL')) lines.push(`<span class="leading-snug text-slate-500">${esc(ev.reason)}</span>`);
        }
        return section(ctx, side, bullish ? 'News · positive' : 'News · negative', status, lines);
    }

    /** Returns { html } for the two sections, or { html: '' } when the legacy blocks should be used instead. */
    function build(ctx) {
        if (!ctx || !ctx.source) return { html: '' };
        let buy, sell;
        if (ctx.source === 'indicators') {
            buy = indicatorSide(ctx, 'buy');
            sell = indicatorSide(ctx, 'sell');
        } else if (ctx.source === 'strategy') {
            const kind = String((ctx.strategy.info || {}).kind || '');
            if (kind === 'allocation') return { html: '' };   // allocation strategies keep their own card text
            buy = strategySide(ctx, 'buy');
            sell = strategySide(ctx, 'sell');
        } else if (ctx.source === 'event') {
            buy = eventSide(ctx, 'buy');
            sell = eventSide(ctx, 'sell');
        } else {
            return { html: '' };
        }
        return { html: `<div class="grid grid-cols-1 sm:grid-cols-2 gap-1 min-w-0 w-full">${buy}${sell}</div>` };
    }

    /* ── Bots ───────────────────────────────────────────────────────────── */
    // A bot moves one way: flat -> enter -> hold -> exit. The side it is working on is full; the other is a one-line note.
    // Display only: ctx comes from the Bots scan log (see bots-scan-card.js), no new calculations and no side effects.
    function botSide(ctx, side) {
        const bullish = side === 'buy';
        const word = bullish ? 'Buy' : 'Sell';
        const setup = (ctx.setups && ctx.setups[side]) || '';
        if (ctx.active !== 'both' && ctx.active !== side) {
            const tone = TONES[side];
            const note = bullish ? 'Resumes after the exit.' : 'Starts after an entry.';
            return `<div class="rounded-md border border-slate-200 bg-slate-50/60 px-1.5 py-0.5 flex flex-col gap-0.5 text-slate-600 min-w-0 opacity-80" data-evidence-side="${side}" data-evidence-status="INACTIVE">
                <div class="flex items-center gap-1.5 flex-wrap">
                    <span class="text-[0.6rem] sm:text-[0.68rem] font-black uppercase tracking-wider ${tone.title}">${word} Evidence</span>
                    ${setup ? `<span class="inline-flex rounded border border-slate-200 bg-white/70 px-1.5 py-0.5 text-[0.55rem] font-black uppercase text-slate-700">${esc(setup)}</span>` : ''}
                </div>
                <span class="leading-snug">${esc(note)}</span>
                ${((ctx.hint && ctx.hint[side]) || []).filter((row) => row && row[1]).map((row) => line(row[0], row[1])).join('')}
            </div>`;
        }
        const lines = [];
        const custom = ctx.custom && Array.isArray(ctx.custom[side]) ? ctx.custom[side] : null;   // strategy bots: [label, text] rows
        const toneWord = ctx.targetDirection === 'BEARISH' ? 'bearish' : 'bullish';
        const want = toneWord.toUpperCase();
        const marker = (state) => (state === 'CONFIRMED' ? (want === 'BEARISH' ? '🔴' : '🟢') : state === 'OPPOSING' ? (want === 'BEARISH' ? '🟢' : '🔴') : '🟡');
        const group = (state, label) => {
            const items = (ctx.indicators || []).filter((item) => String(item.status || '').toUpperCase() === state);
            if (!items.length) return '';
            return line(label, items.map((item) => `${marker(state)} ${item.name} ${String(item.signal || '').toUpperCase()}${item.reason ? ` (${item.reason})` : ''}`).join(', '));
        };
        if (!custom && ctx.usesIndicators && ctx.required > 0) {
            const need = ctx.remaining > 0 ? ` — needs ${ctx.remaining} more` : ' — threshold met';
            lines.push(`<span class="font-bold">${esc(ctx.confirmed)}/${esc(ctx.required)} ${toneWord} confirmations${esc(need)}</span>`);
            lines.push(group('CONFIRMED', toneWord === 'bearish' ? 'Bearish' : 'Bullish'), group('OPPOSING', 'Opposing'), group('WAITING', 'Waiting'));
            if (ctx.conditionLogic) lines.push(`<span class="leading-snug text-slate-500">${esc(ctx.conditionLogic)}</span>`);
        }
        if (custom) custom.filter((row) => row && row[1]).forEach((row) => lines.push(row[0] ? line(row[0], row[1]) : `<span class="leading-snug">${esc(row[1])}</span>`));
        else (ctx.levels || []).filter(Boolean).forEach((text) => lines.push(`<span class="leading-snug">${esc(text)}</span>`));
        if (!bullish && !custom) {
            const watch = ctx.exitWatch || {};
            const money = (value) => `$${formatAssetPrice(Number(value), ctx.isCrypto)}`;
            const stop = num(watch.stop_loss), target = num(watch.take_profit);
            const haveLevels = Array.isArray(ctx.levels) && ctx.levels.some(Boolean);   // "TP $x (2.06% above)" rows already show the numbers
            const hasNumbers = (stop && stop > 0) || (target && target > 0);
            if (hasNumbers && !haveLevels) {
                lines.push(line('Exit levels', [stop > 0 ? `stop ${money(stop)}` : '', target > 0 ? `target ${money(target)}` : '', watch.trail_enabled ? 'trailing on' : ''].filter(Boolean).join(' · ')));
            } else if (watch.trail_enabled && (hasNumbers || haveLevels)) {
                lines.push(line('Trailing stop', 'on'));
            }
            if (hasNumbers || haveLevels) {
                lines.push('<span class="leading-snug text-slate-500">Software watcher: needs the app running and fresh data; it is not a broker-side stop.</span>');
            }
        }
        (ctx.blockers || []).filter(Boolean).forEach((text) => lines.push(`<span class="leading-snug font-black text-amber-700">⛔ ${esc(text)}</span>`));
        if (ctx.guard) lines.push(`<span class="leading-snug font-black text-amber-700">⛔ ${esc(ctx.guard)}</span>`);
        if (!lines.length) lines.push(line(word, 'waiting for the next scan'));
        return section(Object.assign({}, ctx, { bot: true }), side, setup, ctx.state || 'WAITING', lines);
    }

    /** Bots card: { html } with the Buy and Sell sections (one active, one a note). */
    function buildBot(ctx) {
        if (!ctx || !['buy', 'sell', 'both'].includes(ctx.active)) return { html: '' };
        return { html: `<div class="grid grid-cols-1 sm:grid-cols-2 gap-0.5 sm:gap-1 min-w-0 w-full">${botSide(ctx, 'buy')}${botSide(ctx, 'sell')}</div>` };
    }

    window.ScreenerEvidence = {
        build,
        buildBot,
        sellIsLevelsOnly: (mode) => SELL_LEVELS_ONLY.has(String(mode || '').toLowerCase()),
        sellLabel: (mode) => SELL_MODE_LABELS[String(mode || '').toLowerCase()] || '',
    };
})();
