(function () {
    'use strict';

    const defaults = window.STANDARD_INDICATOR_DEFAULTS || {};
    const contexts = new Map();

    const value = (params, key, fallback) => {
        const raw = params?.[key];
        const number = Number(raw);
        return raw !== '' && Number.isFinite(number) ? number : (defaults[key] ?? fallback);
    };
    const fmt = number => Number.isInteger(Number(number)) ? String(Number(number)) : String(Number(number));
    const line = (field, color, title, extra = {}) => ({ field, color, title, type: 'line', ...extra });
    const histogram = (field, title) => ({ field, title, type: 'histogram' });
    const level = (key, fallback, color, title, transform = null) => ({ key, fallback, color, title, transform });
    const fixedLevel = (fixed, color, title) => ({ fixed, color, title });

    // These are the 25 newly-added indicators that belong in a separate pane.
    // RSI, MACD, ADX Trend, and Mystic Pulse already have dedicated panes.
    const definitions = [
        {
            name: 'ATR Volatility', slug: 'atr',
            label: p => `ATR (${fmt(value(p, 'ATR_PERIOD', 14))}) • ${fmt(value(p, 'ATR_BASELINE_PERIOD', 20))}-bar baseline • range ${fmt(value(p, 'ATR_RANGE_MULTIPLIER', 1))}×`,
            series: [line('atr_ext', '#f59e0b', 'ATR', { lineWidth: 2 }), line('atr_ext_avg', '#64748b', 'Baseline', { lineStyle: 2 })],
        },
        {
            name: 'Stochastic', slug: 'stochastic',
            label: p => `Stochastic (${fmt(value(p, 'STOCH_PERIOD', 14))}, ${fmt(value(p, 'STOCH_SMOOTH_K', 3))}, ${fmt(value(p, 'STOCH_SMOOTH_D', 3))}) • OB ${fmt(value(p, 'STOCH_OVERBOUGHT', 80))} / OS ${fmt(value(p, 'STOCH_OVERSOLD', 20))}`,
            series: [line('stoch_k', '#6366f1', '%K', { lineWidth: 2 }), line('stoch_d', '#f97316', '%D')],
            levels: [level('STOCH_OVERBOUGHT', 80, 'rgba(239,68,68,.38)', 'OB'), level('STOCH_OVERSOLD', 20, 'rgba(16,185,129,.38)', 'OS')],
        },
        {
            name: 'Stochastic RSI', slug: 'stoch-rsi',
            label: p => `Stochastic RSI (${fmt(value(p, 'STOCH_RSI_PERIOD', 14))}, ${fmt(value(p, 'STOCH_RSI_SMOOTH_K', 3))}, ${fmt(value(p, 'STOCH_RSI_SMOOTH_D', 3))}) • OB ${fmt(value(p, 'STOCH_RSI_OVERBOUGHT', 80))} / OS ${fmt(value(p, 'STOCH_RSI_OVERSOLD', 20))}`,
            series: [line('stoch_rsi_k', '#8b5cf6', '%K', { lineWidth: 2 }), line('stoch_rsi_d', '#06b6d4', '%D')],
            levels: [level('STOCH_RSI_OVERBOUGHT', 80, 'rgba(239,68,68,.38)', 'OB'), level('STOCH_RSI_OVERSOLD', 20, 'rgba(16,185,129,.38)', 'OS')],
        },
        {
            name: 'CCI', slug: 'cci',
            label: p => `CCI (${fmt(value(p, 'CCI_PERIOD', 20))}) • ±${fmt(value(p, 'CCI_THRESHOLD', 100))}`,
            series: [line('cci_ext', '#8b5cf6', 'CCI', { lineWidth: 2 })],
            levels: [level('CCI_THRESHOLD', 100, 'rgba(239,68,68,.34)', '+Threshold'), level('CCI_THRESHOLD', 100, 'rgba(16,185,129,.34)', '-Threshold', n => -n), fixedLevel(0, 'rgba(100,116,139,.24)', 'Zero')],
        },
        {
            name: 'Williams %R', slug: 'williams-r',
            label: p => `Williams %R (${fmt(value(p, 'WILLIAMS_R_PERIOD', 14))}) • OB ${fmt(value(p, 'WILLIAMS_R_OVERBOUGHT', -20))} / OS ${fmt(value(p, 'WILLIAMS_R_OVERSOLD', -80))}`,
            series: [line('williams_r', '#ec4899', '%R', { lineWidth: 2 })],
            levels: [level('WILLIAMS_R_OVERBOUGHT', -20, 'rgba(239,68,68,.38)', 'OB'), level('WILLIAMS_R_OVERSOLD', -80, 'rgba(16,185,129,.38)', 'OS')],
        },
        {
            name: 'Rate of Change', slug: 'roc',
            label: p => `Rate of Change (${fmt(value(p, 'ROC_PERIOD', 12))})`,
            series: [line('roc_ext', '#0ea5e9', 'ROC', { lineWidth: 2 })],
            levels: [fixedLevel(0, 'rgba(100,116,139,.35)', 'Zero')],
        },
        {
            name: 'Momentum', slug: 'momentum',
            label: p => `Momentum (${fmt(value(p, 'MOMENTUM_PERIOD', 10))})`,
            series: [line('momentum_ext', '#6366f1', 'Momentum', { lineWidth: 2 })],
            levels: [fixedLevel(0, 'rgba(100,116,139,.35)', 'Zero')],
        },
        {
            name: 'Money Flow Index', slug: 'mfi',
            label: p => `Money Flow Index (${fmt(value(p, 'MFI_PERIOD', 14))}) • OB ${fmt(value(p, 'MFI_OVERBOUGHT', 80))} / OS ${fmt(value(p, 'MFI_OVERSOLD', 20))}`,
            series: [line('mfi_ext', '#10b981', 'MFI', { lineWidth: 2 })],
            levels: [level('MFI_OVERBOUGHT', 80, 'rgba(239,68,68,.38)', 'OB'), level('MFI_OVERSOLD', 20, 'rgba(16,185,129,.38)', 'OS')],
        },
        {
            name: 'Ultimate Oscillator', slug: 'ultimate-osc',
            label: p => `Ultimate Oscillator (${fmt(value(p, 'UO_SHORT', 7))}, ${fmt(value(p, 'UO_MEDIUM', 14))}, ${fmt(value(p, 'UO_LONG', 28))}) • OB ${fmt(value(p, 'UO_OVERBOUGHT', 70))} / OS ${fmt(value(p, 'UO_OVERSOLD', 30))}`,
            series: [line('ultimate_osc', '#7c3aed', 'UO', { lineWidth: 2 })],
            levels: [level('UO_OVERBOUGHT', 70, 'rgba(239,68,68,.38)', 'OB'), level('UO_OVERSOLD', 30, 'rgba(16,185,129,.38)', 'OS')],
        },
        {
            name: 'Awesome Oscillator', slug: 'awesome-osc',
            label: p => `Awesome Oscillator (${fmt(value(p, 'AO_FAST', 5))}, ${fmt(value(p, 'AO_SLOW', 34))})`,
            series: [histogram('awesome_osc', 'AO')],
            levels: [fixedLevel(0, 'rgba(100,116,139,.35)', 'Zero')],
        },
        {
            name: 'Chande Momentum Oscillator', slug: 'cmo',
            label: p => `Chande Momentum Oscillator (${fmt(value(p, 'CMO_PERIOD', 14))}) • ±${fmt(value(p, 'CMO_THRESHOLD', 50))}`,
            series: [line('cmo', '#d946ef', 'CMO', { lineWidth: 2 })],
            levels: [level('CMO_THRESHOLD', 50, 'rgba(239,68,68,.34)', '+Threshold'), level('CMO_THRESHOLD', 50, 'rgba(16,185,129,.34)', '-Threshold', n => -n), fixedLevel(0, 'rgba(100,116,139,.24)', 'Zero')],
        },
        {
            name: 'True Strength Index', slug: 'tsi',
            label: p => `True Strength Index (${fmt(value(p, 'TSI_SLOW', 25))}, ${fmt(value(p, 'TSI_FAST', 13))}, ${fmt(value(p, 'TSI_SIGNAL', 13))})`,
            series: [line('tsi_ext', '#4f46e5', 'TSI', { lineWidth: 2 }), line('tsi_signal', '#f97316', 'Signal')],
            levels: [fixedLevel(0, 'rgba(100,116,139,.35)', 'Zero')],
        },
        {
            name: 'TRIX', slug: 'trix',
            label: p => `TRIX (${fmt(value(p, 'TRIX_PERIOD', 15))}, ${fmt(value(p, 'TRIX_SIGNAL', 9))})`,
            series: [line('trix_ext', '#06b6d4', 'TRIX', { lineWidth: 2 }), line('trix_signal', '#f59e0b', 'Signal')],
            levels: [fixedLevel(0, 'rgba(100,116,139,.35)', 'Zero')],
        },
        {
            name: 'Know Sure Thing', slug: 'kst',
            label: p => `Know Sure Thing • Signal ${fmt(value(p, 'KST_SIGNAL', 9))}`,
            series: [line('kst_ext', '#7c3aed', 'KST', { lineWidth: 2 }), line('kst_signal', '#f97316', 'Signal')],
            levels: [fixedLevel(0, 'rgba(100,116,139,.35)', 'Zero')],
        },
        {
            name: 'Percentage Price Oscillator', slug: 'ppo',
            label: p => `Percentage Price Oscillator (${fmt(value(p, 'PPO_FAST', 12))}, ${fmt(value(p, 'PPO_SLOW', 26))}, ${fmt(value(p, 'PPO_SIGNAL', 9))})`,
            series: [histogram('ppo_hist', 'Histogram'), line('ppo_ext', '#06b6d4', 'PPO', { lineWidth: 2 }), line('ppo_signal', '#f97316', 'Signal')],
            levels: [fixedLevel(0, 'rgba(100,116,139,.35)', 'Zero')],
        },
        {
            name: 'Aroon', slug: 'aroon',
            label: p => `Aroon (${fmt(value(p, 'AROON_PERIOD', 25))}) • Threshold ${fmt(value(p, 'AROON_THRESHOLD', 70))}`,
            series: [line('aroon_up', '#10b981', 'Up', { lineWidth: 2 }), line('aroon_down', '#ef4444', 'Down', { lineWidth: 2 })],
            levels: [level('AROON_THRESHOLD', 70, 'rgba(99,102,241,.28)', 'Threshold')],
        },
        {
            name: 'Vortex Indicator', slug: 'vortex',
            label: p => `Vortex Indicator (${fmt(value(p, 'VORTEX_PERIOD', 14))})`,
            series: [line('vortex_pos', '#10b981', 'VI+', { lineWidth: 2 }), line('vortex_neg', '#ef4444', 'VI-', { lineWidth: 2 })],
        },
        {
            name: 'Choppiness Index', slug: 'choppiness',
            label: p => `Choppiness Index (${fmt(value(p, 'CHOP_PERIOD', 14))}) • Trend < ${fmt(value(p, 'CHOP_TREND_THRESHOLD', 38.2))} / Range > ${fmt(value(p, 'CHOP_RANGE_THRESHOLD', 61.8))}`,
            series: [line('choppiness', '#8b5cf6', 'CHOP', { lineWidth: 2 })],
            levels: [level('CHOP_TREND_THRESHOLD', 38.2, 'rgba(16,185,129,.38)', 'Trend'), level('CHOP_RANGE_THRESHOLD', 61.8, 'rgba(239,68,68,.38)', 'Range')],
        },
        {
            name: 'On-Balance Volume', slug: 'obv',
            label: p => `On-Balance Volume • Signal ${fmt(value(p, 'OBV_SIGNAL_PERIOD', 20))}`,
            series: [line('obv_ext', '#4f46e5', 'OBV', { lineWidth: 2 }), line('obv_signal', '#f97316', 'Signal')],
        },
        {
            name: 'Chaikin Money Flow', slug: 'cmf',
            label: p => `Chaikin Money Flow (${fmt(value(p, 'CMF_PERIOD', 20))})`,
            series: [line('cmf_ext', '#10b981', 'CMF', { lineWidth: 2 })],
            levels: [fixedLevel(0, 'rgba(100,116,139,.35)', 'Zero')],
        },
        {
            name: 'Accumulation/Distribution', slug: 'adl',
            label: p => `Accumulation/Distribution • Signal ${fmt(value(p, 'ADL_SIGNAL_PERIOD', 20))}`,
            series: [line('adl_ext', '#0ea5e9', 'A/D', { lineWidth: 2 }), line('adl_signal', '#f59e0b', 'Signal')],
        },
        {
            name: 'Chaikin Oscillator', slug: 'chaikin-osc',
            label: p => `Chaikin Oscillator (${fmt(value(p, 'CHAIKIN_FAST', 3))}, ${fmt(value(p, 'CHAIKIN_SLOW', 10))})`,
            series: [histogram('chaikin_osc', 'Chaikin')],
            levels: [fixedLevel(0, 'rgba(100,116,139,.35)', 'Zero')],
        },
        {
            name: 'Price Volume Trend', slug: 'pvt',
            label: p => `Price Volume Trend • Signal ${fmt(value(p, 'PVT_SIGNAL_PERIOD', 20))}`,
            series: [line('pvt_ext', '#7c3aed', 'PVT', { lineWidth: 2 }), line('pvt_signal', '#f97316', 'Signal')],
        },
        {
            name: 'Ease of Movement', slug: 'eom',
            label: p => `Ease of Movement (${fmt(value(p, 'EOM_PERIOD', 14))})`,
            series: [line('eom_ext', '#06b6d4', 'EOM', { lineWidth: 2 })],
            levels: [fixedLevel(0, 'rgba(100,116,139,.35)', 'Zero')],
        },
        {
            name: 'Force Index', slug: 'force-index',
            label: p => `Force Index (${fmt(value(p, 'FORCE_INDEX_PERIOD', 13))})`,
            series: [histogram('force_index', 'Force')],
            levels: [fixedLevel(0, 'rgba(100,116,139,.35)', 'Zero')],
        },
    ];

    // The remaining newly-added chart indicators belong on the price pane.
    const overlayDefinitions = [
        {
            name: 'ATR Volatility',
            series: [
                { field: 'atr_range_upper', color: 'rgba(245,158,11,.72)', lineWidth: 1, lineStyle: 2, title: 'ATR expected range high' },
                { field: 'atr_range_lower', color: 'rgba(245,158,11,.72)', lineWidth: 1, lineStyle: 2, title: 'ATR expected range low' },
            ],
        },
        {
            name: 'BotBulls5',
            series: [
                { field: 'bb5_upper', startField: 'bb5_upper_start', color: '#f59e0b', lineWidth: 2, title: 'BB5 upper boundary', botbulls5Geometry: true },
                { field: 'bb5_lower', startField: 'bb5_lower_start', color: '#06b6d4', lineWidth: 2, title: 'BB5 lower boundary', botbulls5Geometry: true },
                { field: 'bb5_target', color: 'rgba(16,185,129,.85)', lineWidth: 1, lineStyle: 2, title: 'BB5 measured target', botbulls5Geometry: true },
                { field: 'bb5_invalidation', color: 'rgba(244,63,94,.85)', lineWidth: 1, lineStyle: 2, title: 'BB5 invalidation', botbulls5Geometry: true },
            ],
        },
        {
            name: 'Ichimoku Cloud',
            series: [
                { field: 'ichimoku_conversion', color: '#2563eb', lineWidth: 1.5, title: 'Conversion' },
                { field: 'ichimoku_base', color: '#ef4444', lineWidth: 1.5, title: 'Base' },
                { field: 'ichimoku_span_a', color: 'rgba(16,185,129,.8)', lineWidth: 1, lineStyle: 2, title: 'Leading Span A', shiftPeriodsKey: 'ICHIMOKU_BASE', shiftPeriodsFallback: 26 },
                { field: 'ichimoku_span_b', color: 'rgba(239,68,68,.8)', lineWidth: 1, lineStyle: 2, title: 'Leading Span B', shiftPeriodsKey: 'ICHIMOKU_BASE', shiftPeriodsFallback: 26 },
            ],
        },
        { name: 'Parabolic SAR', series: [{ field: 'psar', color: '#ec4899', lineWidth: 1, lineVisible: false, pointMarkersVisible: true, pointMarkersRadius: 2, title: 'PSAR dots' }] },
        {
            name: 'Donchian Channels',
            series: [
                { field: 'donchian_upper', color: '#f97316', lineWidth: 1.5, title: 'Upper' },
                { field: 'donchian_lower', color: '#06b6d4', lineWidth: 1.5, title: 'Lower' },
            ],
        },
        {
            name: 'Keltner Channels',
            series: [
                { field: 'keltner_upper', color: '#f59e0b', lineWidth: 1.5, title: 'Upper' },
                { field: 'keltner_mid', color: '#6366f1', lineWidth: 1, lineStyle: 2, title: 'Middle' },
                { field: 'keltner_lower', color: '#14b8a6', lineWidth: 1.5, title: 'Lower' },
            ],
        },
        { name: 'DEMA', series: [{ field: 'dema', color: '#2563eb', lineWidth: 2, title: 'DEMA' }] },
        { name: 'TEMA', series: [{ field: 'tema', color: '#7c3aed', lineWidth: 2, title: 'TEMA' }] },
        { name: 'Hull Moving Average', series: [{ field: 'hma', color: '#db2777', lineWidth: 2, title: 'HMA' }] },
        { name: 'Weighted Moving Average', series: [{ field: 'wma_ext', color: '#0891b2', lineWidth: 2, title: 'WMA' }] },
        { name: 'VWMA', series: [{ field: 'vwma_ext', color: '#059669', lineWidth: 2, title: 'VWMA' }] },
        { name: 'Arnaud Legoux Moving Average', series: [{ field: 'alma', color: '#9333ea', lineWidth: 2, title: 'ALMA' }] },
        { name: 'Least Squares Moving Average', series: [{ field: 'lsma', color: '#ea580c', lineWidth: 2, title: 'LSMA' }] },
    ];

    function cleanData(history, field, type) {
        const byTime = new Map();
        (Array.isArray(history) ? history : []).forEach(bar => {
            const time = Number(bar?.time);
            const numeric = Number(bar?.[field]);
            if (!Number.isFinite(time) || !Number.isFinite(numeric)) return;
            const point = { time, value: numeric };
            if (type === 'histogram') {
                point.color = numeric >= 0 ? 'rgba(16,185,129,.62)' : 'rgba(239,68,68,.62)';
            }
            byTime.set(time, point);
        });
        return Array.from(byTime.values()).sort((a, b) => a.time - b.time);
    }

    function botBulls5Point(rows, index, field) {
        const row = rows[index];
        const time = Number(row?.time);
        const valueAtPoint = Number(row?.[field]);
        return Number.isFinite(time) && Number.isFinite(valueAtPoint)
            ? { time, value: valueAtPoint, index }
            : null;
    }

    function botBulls5SourceTime(row) {
        const sourceTime = Number(row?.source_time);
        return Number.isFinite(sourceTime) ? sourceTime : Number(row?.time);
    }

    function botBulls5IndexAtOrAfter(rows, sourceTime, fallbackIndex = 0) {
        const target = Number(sourceTime);
        if (!Number.isFinite(target)) return fallbackIndex;
        const match = rows.findIndex(row => botBulls5SourceTime(row) >= target);
        return match >= 0 ? match : fallbackIndex;
    }

    function botBulls5IndexAtOrBefore(rows, sourceTime, fallbackIndex = 0) {
        const target = Number(sourceTime);
        if (!Number.isFinite(target)) return fallbackIndex;
        for (let index = rows.length - 1; index >= 0; index -= 1) {
            if (botBulls5SourceTime(rows[index]) <= target) return index;
        }
        return fallbackIndex;
    }

    function botBulls5Extrema(rows, startIndex, endIndex) {
        const highs = [];
        const lows = [];
        const span = 2;
        for (let index = startIndex + span; index <= endIndex - span; index += 1) {
            const current = rows[index];
            const high = Number(current?.high);
            const low = Number(current?.low);
            const windowRows = rows.slice(index - span, index + span + 1);
            if (
                Number.isFinite(high)
                && windowRows.every(row => high >= Number(row?.high))
            ) {
                highs.push({ index, time: Number(current.time), value: high, kind: 'high' });
            }
            if (
                Number.isFinite(low)
                && windowRows.every(row => low <= Number(row?.low))
            ) {
                lows.push({ index, time: Number(current.time), value: low, kind: 'low' });
            }
        }
        return { highs, lows };
    }

    function botBulls5StrongPivots(points, count, descending, minimumSpacing = 2) {
        const ranked = [...points].sort((left, right) =>
            descending ? right.value - left.value : left.value - right.value
        );
        const selected = [];
        ranked.forEach(point => {
            if (
                selected.length < count
                && selected.every(existing => Math.abs(existing.index - point.index) >= minimumSpacing)
            ) {
                selected.push(point);
            }
        });
        return selected.sort((left, right) => left.index - right.index);
    }

    function botBulls5OppositeBetween(points, leftIndex, rightIndex, chooseLow) {
        const between = points.filter(point => point.index > leftIndex && point.index < rightIndex);
        if (!between.length) return null;
        return between.reduce((best, point) =>
            chooseLow
                ? (point.value < best.value ? point : best)
                : (point.value > best.value ? point : best)
        );
    }

    function botBulls5AlternatingSwingPath(primary, opposite, chooseLowBetween) {
        const path = [];
        primary.forEach((point, position) => {
            path.push(point);
            if (position >= primary.length - 1) return;
            const between = botBulls5OppositeBetween(
                opposite,
                point.index,
                primary[position + 1].index,
                chooseLowBetween,
            );
            if (between) path.push(between);
        });
        return path.sort((left, right) => left.index - right.index);
    }

    function botBulls5ShoulderPath(highs, lows, inverse) {
        const primary = inverse ? lows : highs;
        const opposite = inverse ? highs : lows;
        let best = null;
        const recent = primary.slice(-8);
        for (let left = 0; left < recent.length - 2; left += 1) {
            for (let middle = left + 1; middle < recent.length - 1; middle += 1) {
                for (let right = middle + 1; right < recent.length; right += 1) {
                    const points = [recent[left], recent[middle], recent[right]];
                    const headQualified = inverse
                        ? points[1].value < points[0].value && points[1].value < points[2].value
                        : points[1].value > points[0].value && points[1].value > points[2].value;
                    if (!headQualified) continue;
                    const headProminence = inverse
                        ? Math.min(points[0].value, points[2].value) - points[1].value
                        : points[1].value - Math.max(points[0].value, points[2].value);
                    const shoulderDifference = Math.abs(points[0].value - points[2].value);
                    const score = headProminence - shoulderDifference;
                    if (!best || score > best.score) best = { points, score };
                }
            }
        }
        if (!best) {
            const fallback = botBulls5StrongPivots(primary, 3, !inverse, 2);
            return botBulls5AlternatingSwingPath(fallback, opposite, !inverse);
        }
        return botBulls5AlternatingSwingPath(best.points, opposite, !inverse);
    }

    function botBulls5SampleClosePath(rows, startIndex, endIndex, maximumPoints = 12) {
        const length = endIndex - startIndex + 1;
        const step = Math.max(1, Math.ceil(length / maximumPoints));
        const points = [];
        for (let index = startIndex; index <= endIndex; index += step) {
            const point = botBulls5Point(rows, index, 'close');
            if (point) points.push(point);
        }
        if (points.at(-1)?.index !== endIndex) {
            const last = botBulls5Point(rows, endIndex, 'close');
            if (last) points.push(last);
        }
        return points;
    }

    function botBulls5RecentEventTimes(history, options = {}) {
        const rows = (Array.isArray(history) ? history : [])
            .filter(row => Number.isFinite(Number(row?.source_time ?? row?.time)));
        const latest = rows.at(-1);
        if (!latest) return new Set();

        const sourceTime = row => Number(row?.source_time ?? row?.time);
        const displayTime = row => Number(row?.time);
        if (options.rolling24Hours) {
            const cutoff = sourceTime(latest) - 86400;
            return new Set(rows
                .filter(row => sourceTime(row) >= cutoff)
                .map(displayTime)
                .filter(Number.isFinite));
        }

        const dayKey = value => {
            const parts = new Intl.DateTimeFormat('en-CA', {
                timeZone: options.timeZone || 'America/New_York',
                year: 'numeric',
                month: '2-digit',
                day: '2-digit',
            }).formatToParts(new Date(value * 1000));
            const values = Object.fromEntries(parts.map(part => [part.type, part.value]));
            return `${values.year}-${values.month}-${values.day}`;
        };
        const latestDay = dayKey(sourceTime(latest));
        return new Set(rows
            .filter(row => dayKey(sourceTime(row)) === latestDay)
            .map(displayTime)
            .filter(Number.isFinite));
    }

    function selectBotBulls5Pattern(history, options = {}) {
        const rows = Array.isArray(history) ? history : [];
        const latestIndex = rows.length - 1;
        const latest = rows[latestIndex];
        if (!Number.isFinite(Number(latest?.time))) return null;

        const recentTimes = botBulls5RecentEventTimes(rows, options);
        const hasPattern = row => (
            Number.isFinite(Number(row?.time))
            && row?.bb5_pattern
            && row.bb5_pattern !== 'NONE'
        );
        if (hasPattern(latest) && recentTimes.has(Number(latest.time))) {
            return { row: latest, index: latestIndex, source: 'current' };
        }

        for (let index = latestIndex; index >= 0; index -= 1) {
            const row = rows[index];
            if (!recentTimes.has(Number(row?.time)) || !hasPattern(row)) continue;
            if (row.bb5_breakout_bull || row.bb5_breakout_bear) {
                return { row, index, source: 'latest-day-breakout' };
            }
        }
        return null;
    }

    function buildBotBulls5PatternDrawing(history, options = {}) {
        const rows = Array.isArray(history) ? history : [];
        const selection = selectBotBulls5Pattern(rows, options);
        if (!selection) return [];
        const endIndex = selection.index;
        if (endIndex < 1) return [];

        const patternRow = selection.row;
        const pattern = String(patternRow.bb5_pattern || '').toUpperCase();
        const formationBars = Math.max(3, Math.round(Number(patternRow.bb5_formation_bars) || 3));
        const fallbackStartIndex = Math.max(0, endIndex - formationBars + 1);
        const startIndex = botBulls5IndexAtOrAfter(
            rows,
            patternRow.bb5_formation_start_time,
            fallbackStartIndex,
        );
        const direction = Number(patternRow.bb5_direction)
            || (patternRow.bb5_breakout_bull ? 1 : patternRow.bb5_breakout_bear ? -1 : 0);
        const bullishColor = direction < 0 ? '#f43f5e' : '#10b981';
        let breakoutIndex = -1;
        const patternState = String(patternRow.bb5_state || '').toUpperCase();
        const confirmedOrFailed = patternState.startsWith('CONFIRMED')
            || patternState === 'FAILED'
            || patternRow.bb5_breakout_bull
            || patternRow.bb5_breakout_bear;
        if (confirmedOrFailed) {
            for (let index = endIndex; index >= Math.max(0, startIndex - 2); index -= 1) {
                if (
                    rows[index]?.bb5_pattern === patternRow.bb5_pattern
                    && (rows[index]?.bb5_breakout_bull || rows[index]?.bb5_breakout_bear)
                ) {
                    breakoutIndex = index;
                    break;
                }
            }
        }
        const drawingEndIndex = breakoutIndex >= 0 ? breakoutIndex : endIndex;
        const layers = [];
        const addLayer = (key, data, options) => {
            const clean = (data || [])
                .filter(point => Number.isFinite(Number(point?.time)) && Number.isFinite(Number(point?.value)))
                .map(point => ({ time: Number(point.time), value: Number(point.value) }))
                .sort((left, right) => left.time - right.time);
            const unique = Array.from(new Map(clean.map(point => [point.time, point])).values());
            if (unique.length >= 2) layers.push({ key, data: unique, ...options });
        };

        const continuationWithPole = /(FLAG|PENNANT|WEDGE_CONTINUATION)/.test(pattern);
        if (continuationWithPole) {
            const fallbackPoleEnd = Math.max(0, startIndex - 1);
            const poleEnd = botBulls5IndexAtOrBefore(
                rows,
                patternRow.bb5_pole_end_time,
                fallbackPoleEnd,
            );
            const poleStart = botBulls5IndexAtOrAfter(
                rows,
                patternRow.bb5_pole_start_time,
                Math.max(0, poleEnd - 11),
            );
            addLayer(
                'pole',
                [
                    botBulls5Point(rows, poleStart, 'close'),
                    botBulls5Point(rows, poleEnd, 'close'),
                ],
                { color: bullishColor, lineWidth: 3, lineStyle: 0 },
            );
        }

        const { highs, lows } = botBulls5Extrema(rows, startIndex, drawingEndIndex);
        let swingPath = [];
        let pathLabel = 'swing-outline';
        if (/DOUBLE_TOP/.test(pattern)) {
            swingPath = botBulls5AlternatingSwingPath(
                highs.slice(-2),
                lows,
                true,
            );
        } else if (/TRIPLE_TOP/.test(pattern)) {
            swingPath = botBulls5AlternatingSwingPath(
                highs.slice(-3),
                lows,
                true,
            );
        } else if (/DOUBLE_BOTTOM/.test(pattern)) {
            swingPath = botBulls5AlternatingSwingPath(
                lows.slice(-2),
                highs,
                false,
            );
        } else if (/TRIPLE_BOTTOM/.test(pattern)) {
            swingPath = botBulls5AlternatingSwingPath(
                lows.slice(-3),
                highs,
                false,
            );
        } else if (/HEAD_AND_SHOULDERS_TOP/.test(pattern)) {
            swingPath = botBulls5ShoulderPath(highs, lows, false);
        } else if (/HEAD_AND_SHOULDERS_BOTTOM/.test(pattern)) {
            swingPath = botBulls5ShoulderPath(highs, lows, true);
        } else if (/(ROUNDING_BOTTOM|CUP_WITH_HANDLE)/.test(pattern)) {
            swingPath = botBulls5SampleClosePath(rows, startIndex, drawingEndIndex, 14);
            pathLabel = 'rounded-outline';
        } else if (/(MEASURED_MOVE|BUMP_AND_RUN)/.test(pattern)) {
            swingPath = botBulls5SampleClosePath(rows, startIndex, drawingEndIndex, 9);
            pathLabel = 'measured-outline';
        }
        const necklinePattern = /(DOUBLE_TOP|TRIPLE_TOP|DOUBLE_BOTTOM|TRIPLE_BOTTOM|HEAD_AND_SHOULDERS|ROUNDING_BOTTOM|CUP_WITH_HANDLE|MEASURED_MOVE|BUMP_AND_RUN)/.test(pattern);
        if (necklinePattern && swingPath.length < 2) {
            swingPath = botBulls5SampleClosePath(rows, startIndex, drawingEndIndex, 10);
        }

        addLayer(
            pathLabel,
            swingPath,
            { color: 'rgba(124,58,237,0.9)', lineWidth: 2, lineStyle: 0 },
        );

        const breakoutLevel = Number(patternRow.bb5_breakout_level);
        if (
            Number.isFinite(breakoutLevel)
            && necklinePattern
        ) {
            addLayer(
                'neckline',
                [
                    { time: Number(rows[startIndex]?.time), value: breakoutLevel },
                    { time: Number(rows[drawingEndIndex]?.time), value: breakoutLevel },
                ],
                { color: '#f97316', lineWidth: 2, lineStyle: 2 },
            );
        }

        if (breakoutIndex >= 1) {
            const previousIndex = Math.max(startIndex, breakoutIndex - 1);
            addLayer(
                'breakout-leg',
                [
                    botBulls5Point(rows, previousIndex, 'close'),
                    botBulls5Point(rows, breakoutIndex, 'close'),
                ],
                { color: bullishColor, lineWidth: 3, lineStyle: 0 },
            );
        }

        return layers;
    }

    function buildOverlayData(history, spec, parameters = {}) {
        const rows = Array.isArray(history) ? history : [];
        if (spec?.botbulls5Geometry) {
            const selection = selectBotBulls5Pattern(rows, parameters);
            if (!selection) return [];
            let endIndex = selection.index;
            const latestPatternRow = selection.row;
            if (
                latestPatternRow?.bb5_state === 'CONFIRMED_BULLISH'
                || latestPatternRow?.bb5_state === 'CONFIRMED_BEARISH'
                || latestPatternRow?.bb5_state === 'FAILED'
            ) {
                for (let index = endIndex; index >= 0; index -= 1) {
                    if (
                        rows[index]?.bb5_pattern === latestPatternRow.bb5_pattern
                        && (rows[index]?.bb5_breakout_bull || rows[index]?.bb5_breakout_bear)
                    ) {
                        endIndex = index;
                        break;
                    }
                }
            }
            const endRow = rows[endIndex];
            const endRawValue = endRow?.[spec.field];
            const endValue = Number(endRawValue);
            if (
                endRawValue === null
                || endRawValue === undefined
                || !Number.isFinite(endValue)
            ) return [];
            const formationBars = Math.max(2, Math.round(Number(endRow.bb5_formation_bars) || 2));
            const startIndex = botBulls5IndexAtOrAfter(
                rows,
                endRow.bb5_formation_start_time,
                Math.max(0, endIndex - formationBars + 1),
            );
            const startRow = rows[startIndex];
            const rawStart = spec.startField ? endRow[spec.startField] : endValue;
            const proposedStart = Number(rawStart);
            const startValue = rawStart !== null && rawStart !== undefined && Number.isFinite(proposedStart)
                ? proposedStart
                : endValue;
            if (!Number.isFinite(Number(startRow?.time)) || !Number.isFinite(Number(endRow?.time))) return [];
            if (Number(startRow.time) === Number(endRow.time)) {
                return [{ time: Number(endRow.time), value: endValue }];
            }
            return [
                { time: Number(startRow.time), value: startValue },
                { time: Number(endRow.time), value: endValue },
            ];
        }
        if (spec?.preserveGaps && !spec?.shiftPeriodsKey) {
            const byTime = new Map();
            rows.forEach(bar => {
                const time = Number(bar?.time);
                if (!Number.isFinite(time)) return;
                const raw = bar?.[spec?.field];
                const numeric = Number(raw);
                byTime.set(
                    time,
                    raw !== null && raw !== undefined && Number.isFinite(numeric)
                        ? { time, value: numeric }
                        : { time },
                );
            });
            return Array.from(byTime.values()).sort((a, b) => a.time - b.time);
        }
        const shift = spec?.shiftPeriodsKey
            ? Math.max(0, Math.round(value(parameters, spec.shiftPeriodsKey, spec.shiftPeriodsFallback || 0)))
            : 0;
        if (!shift) return cleanData(rows, spec?.field, spec?.type);

        const times = rows.map(bar => Number(bar?.time)).filter(Number.isFinite);
        const intervals = [];
        for (let i = 1; i < times.length; i += 1) {
            const delta = times[i] - times[i - 1];
            if (delta > 0) intervals.push(delta);
        }
        intervals.sort((a, b) => a - b);
        const interval = intervals.length ? intervals[Math.floor(intervals.length / 2)] : 60;
        const lastTime = times[times.length - 1];
        const points = [];
        rows.forEach((bar, index) => {
            const numeric = Number(bar?.[spec?.field]);
            if (!Number.isFinite(numeric)) return;
            const targetIndex = index + shift;
            const targetTime = targetIndex < rows.length
                ? Number(rows[targetIndex]?.time)
                : Number(lastTime) + ((targetIndex - rows.length + 1) * interval);
            if (!Number.isFinite(targetTime)) return;
            points.push({ time: targetTime, value: numeric });
        });
        return cleanData(points.map(point => ({ time: point.time, shifted_value: point.value })), 'shifted_value', spec?.type);
    }

    function chartOptions(element, height) {
        return {
            width: Math.max(element.clientWidth || 280, 1),
            height: Math.max(element.clientHeight || height || 85, 1),
            layout: { background: { color: '#fafbff' }, textColor: '#64748b' },
            grid: {
                vertLines: { color: 'rgba(168,85,247,.04)' },
                horzLines: { color: 'rgba(168,85,247,.04)' },
            },
            rightPriceScale: { borderColor: 'rgba(197,203,206,.6)', minimumWidth: 75 },
            timeScale: { visible: false },
        };
    }

    function removePane(state, name) {
        const pane = state.panes.get(name);
        if (!pane) return;
        try { pane.observer?.disconnect(); } catch (error) {}
        try { pane.chart?.remove(); } catch (error) {}
        try { pane.wrapper?.remove(); } catch (error) {}
        state.panes.delete(name);
    }

    function ensureState(context, host) {
        let state = contexts.get(context);
        if (state && state.host !== host) {
            Array.from(state.panes.keys()).forEach(name => removePane(state, name));
            contexts.delete(context);
            state = null;
        }
        if (!state) {
            state = { host, panes: new Map() };
            contexts.set(context, state);
        }
        return state;
    }

    function createPane(state, definition, height) {
        const wrapper = document.createElement('div');
        wrapper.className = 'relative group dynamic-indicator-subchart';
        wrapper.dataset.indicatorSubchart = definition.name;

        const paneElement = document.createElement('div');
        paneElement.className = 'chart-osc-pane dynamic-indicator-subchart-pane';
        paneElement.style.display = 'block';
        paneElement.style.height = `${height}px`;
        paneElement.style.minHeight = `${height}px`;
        paneElement.style.maxHeight = `${height}px`;

        const labelElement = document.createElement('div');
        labelElement.className = 'chart-osc-label dynamic-indicator-subchart-label';
        labelElement.style.display = 'flex';
        const labelText = document.createElement('span');
        labelElement.appendChild(labelText);

        const settingsButton = document.createElement('button');
        settingsButton.type = 'button';
        settingsButton.className = 'dynamic-indicator-subchart-action';
        settingsButton.textContent = '⚙';
        settingsButton.title = `${definition.name} settings`;
        settingsButton.setAttribute('aria-label', `${definition.name} settings`);
        settingsButton.addEventListener('click', event => {
            event.stopPropagation();
            if (typeof window.openIndicatorSettings === 'function') window.openIndicatorSettings(definition.name);
        });
        labelElement.appendChild(settingsButton);

        const guideButton = document.createElement('button');
        guideButton.type = 'button';
        guideButton.className = 'dynamic-indicator-subchart-action';
        guideButton.textContent = 'ⓘ';
        guideButton.title = `${definition.name} guide`;
        guideButton.setAttribute('aria-label', `${definition.name} guide`);
        guideButton.addEventListener('click', event => {
            event.stopPropagation();
            if (typeof window.openInfoModal === 'function') window.openInfoModal(definition.name);
        });
        labelElement.appendChild(guideButton);

        wrapper.appendChild(paneElement);
        wrapper.appendChild(labelElement);
        state.host.appendChild(wrapper);

        const chart = window.LightweightCharts.createChart(paneElement, chartOptions(paneElement, height));
        const series = new Map();
        definition.series.forEach(spec => {
            const common = {
                priceLineVisible: false,
                lastValueVisible: true,
                crosshairMarkerVisible: false,
            };
            const created = spec.type === 'histogram'
                ? chart.addHistogramSeries({ ...common, base: 0 })
                : chart.addLineSeries({
                    ...common,
                    color: spec.color,
                    lineWidth: spec.lineWidth || 1,
                    lineStyle: spec.lineStyle || 0,
                });
            series.set(spec.field, created);
        });

        const observer = typeof ResizeObserver === 'undefined' ? null : new ResizeObserver(entries => {
            if (!entries[0]) return;
            chart.applyOptions({
                width: Math.max(entries[0].contentRect.width, 1),
                height: Math.max(entries[0].contentRect.height, 1),
            });
        });
        observer?.observe(paneElement);

        const pane = { wrapper, paneElement, labelElement, labelText, chart, series, levelSeries: [], observer };
        state.panes.set(definition.name, pane);
        return pane;
    }

    function render(options = {}) {
        const context = String(options.context || '');
        const host = document.getElementById(options.hostId);
        if (!context || !host || typeof window.LightweightCharts === 'undefined') return;
        const state = ensureState(context, host);
        const enabled = new Set(Array.isArray(options.enabledIndicators)
            ? options.enabledIndicators
            : Array.from(options.enabledIndicators || []));
        const activeDefinitions = definitions.filter(definition => enabled.has(definition.name));
        const activeNames = new Set(activeDefinitions.map(definition => definition.name));
        const height = Math.max(70, Number(options.height) || 85);
        const history = Array.isArray(options.history) ? options.history : [];
        const params = { ...defaults, ...(options.parameters || {}) };

        Array.from(state.panes.keys()).forEach(name => {
            if (!activeNames.has(name)) removePane(state, name);
        });

        activeDefinitions.forEach(definition => {
            const pane = state.panes.get(definition.name) || createPane(state, definition, height);
            state.host.appendChild(pane.wrapper);
            pane.labelText.textContent = definition.label(params);
            definition.series.forEach(spec => {
                pane.series.get(spec.field)?.setData(cleanData(history, spec.field, spec.type));
            });

            pane.levelSeries.forEach(series => {
                try { pane.chart.removeSeries(series); } catch (error) {}
            });
            pane.levelSeries = [];
            const times = history.map(bar => Number(bar?.time)).filter(Number.isFinite).sort((a, b) => a - b);
            if (times.length >= 2) {
                (definition.levels || []).forEach(spec => {
                    let levelValue = spec.fixed;
                    if (levelValue === undefined) levelValue = value(params, spec.key, spec.fallback);
                    if (typeof spec.transform === 'function') levelValue = spec.transform(levelValue);
                    if (!Number.isFinite(Number(levelValue))) return;
                    const levelSeries = pane.chart.addLineSeries({
                        color: spec.color,
                        lineWidth: 1,
                        lineStyle: 2,
                        priceLineVisible: false,
                        lastValueVisible: false,
                        crosshairMarkerVisible: false,
                    });
                    levelSeries.setData([
                        { time: times[0], value: Number(levelValue) },
                        { time: times[times.length - 1], value: Number(levelValue) },
                    ]);
                    pane.levelSeries.push(levelSeries);
                });
            }
            try {
                pane.chart.timeScale().fitContent();
                if (options.visibleRange) pane.chart.timeScale().setVisibleLogicalRange(options.visibleRange);
            } catch (error) {}
        });
    }

    function sync(context, range) {
        if (!range) return;
        const state = contexts.get(String(context || ''));
        state?.panes.forEach(pane => {
            try { pane.chart.timeScale().setVisibleLogicalRange(range); } catch (error) {}
        });
    }

    function destroy(context) {
        const state = contexts.get(String(context || ''));
        if (!state) return;
        Array.from(state.panes.keys()).forEach(name => removePane(state, name));
        contexts.delete(String(context || ''));
    }

    window.STANDARD_EXTENSION_SUBCHARTS = Object.freeze(definitions.map(definition => Object.freeze({
        name: definition.name,
        slug: definition.slug,
        fields: Object.freeze(definition.series.map(series => series.field)),
    })));
    window.STANDARD_SUBCHART_INDICATOR_NAMES = Object.freeze([
        'RSI',
        'MACD',
        'ADX Trend',
        'Mystic Pulse',
        ...definitions.map(definition => definition.name),
    ]);
    window.STANDARD_EXTENSION_OVERLAYS = Object.freeze(overlayDefinitions.map(definition => Object.freeze({
        name: definition.name,
        series: Object.freeze(definition.series.map(series => Object.freeze({ ...series }))),
    })));
    window.StandardIndicatorSubcharts = Object.freeze({
        render,
        sync,
        destroy,
        buildOverlayData,
        buildBotBulls5PatternDrawing,
        botBulls5RecentEventTimes,
        selectBotBulls5Pattern,
    });
})();
