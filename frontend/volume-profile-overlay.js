/*
 * Right-anchored Volume Profile / VPVR overlay.
 *
 * Lightweight Charts does not provide a price-axis histogram series, so this
 * small canvas is layered over the price pane. It consumes the backend bins
 * that also produce POC, VAH, and VAL. When candle data is supplied, the
 * histogram is calculated from the currently visible candles, including the
 * in-progress candle, and follows the visible range while the user pans or
 * zooms. The server profile remains a fallback for charts that have not yet
 * loaded candles.
 */
(function () {
    'use strict';

    function clamp(value, min, max) {
        return Math.min(max, Math.max(min, value));
    }

    function normalizeProfile(profile) {
        if (!profile || profile.enabled === false || !Array.isArray(profile.bins)) return null;
        const bins = profile.bins
            .map((bin) => ({
                low: Number(bin?.low),
                high: Number(bin?.high),
                price: Number(bin?.price),
                volume: Number(bin?.volume),
                buyVolume: Number(bin?.buy_volume),
                sellVolume: Number(bin?.sell_volume),
            }))
            .filter((bin) => (
                Number.isFinite(bin.low)
                && Number.isFinite(bin.high)
                && Number.isFinite(bin.price)
                && Number.isFinite(bin.volume)
                && bin.volume >= 0
                && bin.high >= bin.low
            ));
        if (!bins.length) return null;
        return {
            bins,
            poc: Number(profile.poc),
            vah: Number(profile.vah),
            val: Number(profile.val),
            source: profile.source || '',
            startTime: Number(profile.start_time ?? profile.startTime),
            endTime: Number(profile.end_time ?? profile.endTime),
            barsCount: Number(profile.bars_count ?? profile.barsCount),
        };
    }

    function normalizeCandles(candles) {
        if (!Array.isArray(candles)) return [];
        return candles.map((candle) => ({
            time: Number(candle?.time),
            open: Number(candle?.open),
            high: Number(candle?.high),
            low: Number(candle?.low),
            close: Number(candle?.close),
            volume: Number(candle?.volume),
        })).filter((candle) => (
            Number.isFinite(candle.time)
            && Number.isFinite(candle.open)
            && Number.isFinite(candle.high)
            && Number.isFinite(candle.low)
            && Number.isFinite(candle.close)
            && Number.isFinite(candle.volume)
            && candle.high >= candle.low
        ));
    }

    function buildVisibleProfile(candles, chart, fallbackProfile) {
        if (!candles.length) return null;

        let from = 0;
        let to = candles.length - 1;
        try {
            const range = chart.timeScale().getVisibleLogicalRange?.();
            if (range && Number.isFinite(range.from) && Number.isFinite(range.to)) {
                from = Math.max(0, Math.floor(range.from));
                to = Math.min(candles.length - 1, Math.ceil(range.to));
            }
        } catch (_) {}
        if (to < from) return null;

        const visible = candles.slice(from, to + 1).filter((candle) => candle.volume > 0);
        if (!visible.length) return null;

        const rowsValue = Number(fallbackProfile?.rows);
        const rows = Number.isFinite(rowsValue) ? Math.min(120, Math.max(12, Math.round(rowsValue))) : 48;
        const valueAreaValue = Number(fallbackProfile?.value_area_pct);
        const valueAreaPct = Number.isFinite(valueAreaValue)
            ? Math.min(95, Math.max(50, valueAreaValue))
            : 70;
        const sessionLow = Math.min(...visible.map((candle) => candle.low));
        const sessionHigh = Math.max(...visible.map((candle) => candle.high));
        const totalVolume = visible.reduce((sum, candle) => sum + candle.volume, 0);
        if (!(totalVolume > 0) || !Number.isFinite(sessionLow) || !Number.isFinite(sessionHigh)) return null;

        if (sessionHigh <= sessionLow) {
            return {
                enabled: true,
                source: 'visible_range_current_candle',
                bars_count: visible.length,
                start_time: visible[0].time,
                end_time: visible[visible.length - 1].time,
                poc: sessionLow,
                vah: sessionLow,
                val: sessionLow,
                session_low: sessionLow,
                session_high: sessionHigh,
                rows: 1,
                value_area_pct: valueAreaPct,
                total_volume: totalVolume,
                bins: [{ low: sessionLow, high: sessionHigh, price: sessionLow, volume: totalVolume, buy_volume: totalVolume, sell_volume: 0 }],
            };
        }

        const step = (sessionHigh - sessionLow) / rows;
        const edges = Array.from({ length: rows + 1 }, (_, index) => sessionLow + step * index);
        edges[edges.length - 1] = sessionHigh;
        const bins = Array.from({ length: rows }, (_, index) => ({
            low: edges[index],
            high: edges[index + 1],
            price: (edges[index] + edges[index + 1]) / 2,
            volume: 0,
            buy_volume: 0,
            sell_volume: 0,
        }));

        visible.forEach((candle) => {
            const isBuy = candle.close >= candle.open;
            const overlaps = bins.map((bin) => Math.max(0, Math.min(bin.high, candle.high) - Math.max(bin.low, candle.low)));
            const overlapTotal = overlaps.reduce((sum, value) => sum + value, 0);
            if (overlapTotal > 0) {
                overlaps.forEach((overlap, index) => {
                    if (!(overlap > 0)) return;
                    const allocation = candle.volume * (overlap / overlapTotal);
                    bins[index].volume += allocation;
                    bins[index][isBuy ? 'buy_volume' : 'sell_volume'] += allocation;
                });
                return;
            }
            const rawIndex = Math.floor((candle.close - sessionLow) / step);
            const index = Math.min(rows - 1, Math.max(0, rawIndex));
            bins[index].volume += candle.volume;
            bins[index][isBuy ? 'buy_volume' : 'sell_volume'] += candle.volume;
        });

        const pocIndex = bins.reduce((best, bin, index) => bin.volume > bins[best].volume ? index : best, 0);
        const targetVolume = totalVolume * (valueAreaPct / 100);
        let includedVolume = bins[pocIndex].volume;
        let lowIndex = pocIndex;
        let highIndex = pocIndex;
        while (includedVolume < targetVolume && (lowIndex > 0 || highIndex < rows - 1)) {
            const leftVolume = lowIndex > 0 ? bins[lowIndex - 1].volume : -1;
            const rightVolume = highIndex < rows - 1 ? bins[highIndex + 1].volume : -1;
            if (rightVolume >= leftVolume && highIndex < rows - 1) {
                highIndex += 1;
                includedVolume += bins[highIndex].volume;
            } else if (lowIndex > 0) {
                lowIndex -= 1;
                includedVolume += bins[lowIndex].volume;
            } else {
                break;
            }
        }

        return {
            enabled: true,
            source: 'visible_range_current_candle',
            bars_count: visible.length,
            start_time: visible[0].time,
            end_time: visible[visible.length - 1].time,
            poc: bins[pocIndex].price,
            vah: bins[highIndex].high,
            val: bins[lowIndex].low,
            session_low: sessionLow,
            session_high: sessionHigh,
            rows,
            value_area_pct: valueAreaPct,
            total_volume: totalVolume,
            bins,
        };
    }

    function attach({ chart, priceSeries, container, profile = null, candles = [], enabled = false, onProfileChange = null } = {}) {
        if (!chart || !priceSeries || !container) return null;

        const canvas = document.createElement('canvas');
        canvas.className = 'volume-profile-overlay-canvas';
        canvas.setAttribute('aria-hidden', 'true');
        canvas.style.position = 'absolute';
        canvas.style.inset = '0';
        canvas.style.zIndex = '3';
        canvas.style.pointerEvents = 'none';
        container.style.position = 'relative';
        container.appendChild(canvas);

        const state = {
            fallbackProfile: normalizeProfile(profile),
            candles: normalizeCandles(candles),
            profile: null,
            enabled: Boolean(enabled),
            destroyed: false,
            resizeObserver: null,
            onProfileChange: typeof onProfileChange === 'function' ? onProfileChange : null,
        };

        function refreshProfile() {
            const visibleProfile = buildVisibleProfile(state.candles, chart, state.fallbackProfile);
            state.profile = normalizeProfile(visibleProfile) || state.fallbackProfile;
            if (state.profile && state.onProfileChange) {
                try { state.onProfileChange(state.profile, state.enabled); } catch (_) {}
            }
        }

        function rightPriceScaleWidth() {
            try {
                const width = Number(chart.priceScale('right')?.width?.());
                if (Number.isFinite(width) && width > 0) return width;
            } catch (_) {}
            return window.innerWidth <= 640 ? 52 : 68;
        }

        function draw() {
            if (state.destroyed) return;
            refreshProfile();
            const width = Math.max(1, container.clientWidth || 1);
            const height = Math.max(1, container.clientHeight || 1);
            const dpr = Math.max(1, Math.min(2, window.devicePixelRatio || 1));
            if (canvas.width !== Math.round(width * dpr) || canvas.height !== Math.round(height * dpr)) {
                canvas.width = Math.round(width * dpr);
                canvas.height = Math.round(height * dpr);
                canvas.style.width = `${width}px`;
                canvas.style.height = `${height}px`;
            }

            const context = canvas.getContext('2d');
            if (!context) return;
            context.setTransform(dpr, 0, 0, dpr, 0, 0);
            context.clearRect(0, 0, width, height);

            if (!state.enabled || !state.profile) return;

            const bins = state.profile.bins;
            const maxVolume = Math.max(...bins.map(bin => bin.volume), 0);
            if (!(maxVolume > 0)) return;

            const axisWidth = rightPriceScaleWidth();
            const plotRight = Math.max(20, width - axisWidth - 4);
            const profileWidth = clamp(width <= 520 ? width * 0.24 : width * 0.18, 72, 148);
            const barRight = plotRight;
            const barLeftLimit = Math.max(4, barRight - profileWidth);
            const vah = Number.isFinite(state.profile.vah) ? state.profile.vah : Infinity;
            const val = Number.isFinite(state.profile.val) ? state.profile.val : -Infinity;
            const poc = Number.isFinite(state.profile.poc) ? state.profile.poc : NaN;
            const hasSplitVolume = bins.some(bin => (
                Number.isFinite(bin.buyVolume) && bin.buyVolume >= 0
                && Number.isFinite(bin.sellVolume) && bin.sellVolume >= 0
                && (bin.buyVolume + bin.sellVolume) > 0
            ));

            bins.forEach((bin) => {
                const yHigh = priceSeries.priceToCoordinate(bin.high);
                const yLow = priceSeries.priceToCoordinate(bin.low);
                if (!Number.isFinite(yHigh) || !Number.isFinite(yLow)) return;

                const y = Math.min(yHigh, yLow);
                const barHeight = Math.max(2, Math.abs(yLow - yHigh) - 1);
                const barWidth = profileWidth * (bin.volume / maxVolume);
                if (!(barWidth > 0)) return;
                const x = barRight - barWidth;
                const inValueArea = bin.price >= val && bin.price <= vah;
                const isPoc = Number.isFinite(poc) && bin.price >= Math.min(bin.low, poc) && bin.price <= Math.max(bin.high, poc);

                context.fillStyle = isPoc
                    ? 'rgba(234, 88, 12, 0.72)'
                    : (inValueArea ? 'rgba(59, 130, 246, 0.38)' : 'rgba(100, 116, 139, 0.25)');
                context.fillRect(x, y, barWidth, barHeight);

                if (hasSplitVolume) {
                    const buy = clamp((Number(bin.buyVolume) || 0) / bin.volume, 0, 1);
                    const buyWidth = barWidth * buy;
                    context.fillStyle = isPoc
                        ? 'rgba(249, 115, 22, 0.78)'
                        : 'rgba(16, 185, 129, 0.46)';
                    context.fillRect(barRight - buyWidth, y, buyWidth, barHeight);
                    context.fillStyle = isPoc
                        ? 'rgba(194, 65, 12, 0.72)'
                        : 'rgba(239, 68, 68, 0.32)';
                    context.fillRect(x, y, Math.max(0, barWidth - buyWidth), barHeight);
                }

                context.strokeStyle = 'rgba(255, 255, 255, 0.32)';
                context.lineWidth = 0.5;
                context.strokeRect(x + 0.25, y + 0.25, Math.max(0, barWidth - 0.5), Math.max(0, barHeight - 0.5));
            });

            // Keep a subtle separator between the profile bars and price-scale labels.
            context.strokeStyle = 'rgba(100, 116, 139, 0.24)';
            context.lineWidth = 1;
            context.beginPath();
            context.moveTo(barRight + 1, 0);
            context.lineTo(barRight + 1, height);
            context.stroke();
        }

        function setProfile(nextProfile, nextEnabled = state.enabled) {
            state.fallbackProfile = normalizeProfile(nextProfile);
            state.enabled = Boolean(nextEnabled);
            draw();
        }

        function setCandles(nextCandles) {
            state.candles = normalizeCandles(nextCandles);
            draw();
        }

        function destroy() {
            state.destroyed = true;
            if (state.resizeObserver) state.resizeObserver.disconnect();
            try { chart.timeScale().unsubscribeVisibleLogicalRangeChange(draw); } catch (_) {}
            try { chart.timeScale().unsubscribeVisibleTimeRangeChange(draw); } catch (_) {}
            canvas.remove();
        }

        try { chart.timeScale().subscribeVisibleLogicalRangeChange(draw); } catch (_) {}
        try { chart.timeScale().subscribeVisibleTimeRangeChange(draw); } catch (_) {}
        if (typeof ResizeObserver !== 'undefined') {
            state.resizeObserver = new ResizeObserver(draw);
            state.resizeObserver.observe(container);
        } else {
            window.addEventListener('resize', draw);
            state.resizeObserver = { disconnect: () => window.removeEventListener('resize', draw) };
        }
        draw();

        return { setProfile, setCandles, draw, destroy };
    }

    window.VolumeProfileOverlay = { attach, normalizeProfile, buildVisibleProfile };
})();
