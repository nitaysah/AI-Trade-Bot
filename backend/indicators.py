"""
Multi-Indicator Technical Analysis Engine.

Calculates a full suite of professional-grade indicators.
Exposes calculate_indicators_for_df and _generate_signals for backtesting.
"""

from datetime import datetime, timedelta
import hashlib
import threading
import time
import warnings
import pandas as pd
import numpy as np
import ta as ta_lib
import config as global_config
from indicator_catalog import (
    EXTENSION_CALCULATION_COLUMNS,
    EXTENSION_CHART_FIELDS,
    EXTENSION_CHART_GROUPS,
    EXTENSION_INDICATOR_TOGGLE_KEYS,
)
from user_config import active_evaluation_context, get_user_config
from swing_strategy import detect_swing_entry
from botbulls5_patterns import calculate_botbulls5

from alpaca.data.historical import StockHistoricalDataClient, CryptoHistoricalDataClient
from alpaca.data.requests import StockBarsRequest, CryptoBarsRequest
from alpaca.data.timeframe import TimeFrame, TimeFrameUnit
from broker_factory import get_alpaca_clients

# The indicator engine intentionally builds a wide frame in phases. Pandas
# emits one fragmentation warning per assigned column, which can overwhelm the
# runtime log even though calculations remain correct. Keep actionable warnings
# while silencing this known implementation-detail noise and a ta-library
# positional-index deprecation outside our code.
warnings.filterwarnings(
    "ignore",
    message=r"DataFrame is highly fragmented.*",
    category=pd.errors.PerformanceWarning,
)
warnings.filterwarnings(
    "ignore",
    message=r"Series.__setitem__ treating keys as positions is deprecated.*",
    category=FutureWarning,
    module=r"ta\.trend",
)

wb_data_provider = None

# Bot chart previews often request the same ticker/timeframe with a different
# indicator selection. Keep the provider candle fetch independent from the
# indicator-result cache so changing a setting does not trigger another
# Webull request. The short TTL keeps preview candles fresh while removing the
# repeated network round-trip during interactive edits.
_preview_bars_cache = {}
_preview_bars_fetch_locks = {}
_preview_bars_cache_guard = threading.Lock()


def _preview_bars_cache_ttl(timeframe):
    tf = str(timeframe or "").upper()
    if tf in {"30SEC", "1MIN"}:
        return 8.0
    if tf in {"2MIN", "3MIN", "5MIN"}:
        return 15.0
    return 30.0


def _is_crypto_ticker(ticker):
    clean = str(ticker or "").upper().replace("/", "").replace("-", "")
    return clean.endswith(("USD", "USDT", "USDC")) or clean in {
        "BTC", "ETH", "XRP", "DOGE", "LTC", "ADA", "SOL",
    }


def _filter_market_session_df(df, ticker, timeframe, settings=None):
    """Filter candles before indicator calculation so UI, signals, and geometry agree."""
    if df is None or df.empty or _is_crypto_ticker(ticker):
        return df
    tf = str(timeframe or "").upper()
    if tf in {"1D", "1DAY", "DAY"}:
        return df
    session = str((settings or {}).get("market_session", "") or "").strip().lower()
    if not session or session in {"extended", "24h", "crypto24", "all"}:
        return df
    try:
        timestamps = pd.DatetimeIndex(df.index)
        if timestamps.tz is None:
            eastern = timestamps.tz_localize(
                "America/New_York",
                ambiguous="infer",
                nonexistent="shift_forward",
            )
        else:
            eastern = timestamps.tz_convert("America/New_York")
        minutes = eastern.hour * 60 + eastern.minute
        regular_mask = (
            (eastern.dayofweek < 5)
            & (minutes >= 9 * 60 + 30)
            & (minutes < 16 * 60)
        )
        return df.loc[regular_mask].copy()
    except Exception as exc:
        print(f"[indicators] Market-session filter failed for {ticker}: {type(exc).__name__}")
        return df.iloc[:0].copy()


def _botbulls5_live_evaluation_start(df, ticker, settings=None):
    """Return a warm-up-aware start position for live chart pattern scans.

    Equities evaluate the latest New York trading date plus 160 earlier bars.
    Crypto evaluates a rolling 24-hour window plus the same warm-up. The
    history itself is not sliced, so pattern timestamps can still anchor to
    candles that began before the latest day. Backtests never enable this flag.
    """
    if (
        df is None
        or df.empty
        or not isinstance(settings, dict)
        or not settings.get("_botbulls5_recent_session_only")
    ):
        return None

    warmup_bars = 160
    try:
        timestamps = pd.DatetimeIndex(df.index)
        if timestamps.empty:
            return None
        if _is_crypto_ticker(ticker):
            latest = timestamps[-1]
            cutoff = latest - pd.Timedelta(days=1)
            visible_positions = np.flatnonzero(timestamps >= cutoff)
        else:
            if timestamps.tz is None:
                eastern = timestamps.tz_localize(
                    "America/New_York",
                    ambiguous="infer",
                    nonexistent="shift_forward",
                )
            else:
                eastern = timestamps.tz_convert("America/New_York")
            visible_positions = np.flatnonzero(eastern.date == eastern[-1].date())
        visible_start = int(visible_positions[0]) if len(visible_positions) else len(df) - 1
    except Exception as exc:
        print(f"[indicators] BotBulls5 live-window selection failed for {ticker}: {exc}")
        visible_start = len(df) - 1

    return max(0, visible_start - warmup_bars)


def _selected_chart_field_keys(enabled_indicators, buy_mode="", sell_mode=""):
    """Return the candle fields required by the visible indicators/strategy."""
    if enabled_indicators is None:
        return None
    groups = {
        "RSI": {"rsi"},
        "MACD": {"macd_line", "macd_signal", "macd_hist"},
        "EMA Cross": {"ema_fast", "ema_slow"},
        "ADX Trend": {"adx", "di_plus", "di_minus"},
        "Supertrend": {"supertrend", "supertrend_up"},
        "Bollinger": {"boll_upper", "boll_middle", "boll_lower"},
        "VWAP": {"vwap"},
        "Auto-AVWAP": {"auto_avwap"},
        "SMA": {"sma", "sma_secondary"},
        "Prior Day High/Low": {
            "daily_high_low_high", "daily_high_low_low",
            "daily_high_low_high_trigger", "daily_high_low_low_trigger",
            "daily_high_low_bullish", "daily_high_low_bearish",
        },
        "Pivot Points (S/R)": {
            "pivot_p", "pivot_r1", "pivot_s1", "pivot_r2", "pivot_s2",
            "pivot_r3", "pivot_s3", "pivot_r4", "pivot_s4", "pivot_r5", "pivot_s5",
            "pivot_bullish", "pivot_bearish",
        },
        "Volume Profile": {
            "volume_profile_poc", "volume_profile_vah", "volume_profile_val",
            "volume_profile_bullish", "volume_profile_bearish",
        },
        "Swing Points": {
            "swing_support_1", "swing_support_2", "swing_support_3", "swing_support_4", "swing_support_5",
            "swing_resistance_1", "swing_resistance_2", "swing_resistance_3", "swing_resistance_4", "swing_resistance_5",
            "swing_bullish", "swing_bearish",
        },
        "Mystic Pulse": {"mystic_bull", "mystic_bear"},
        "Candle Patterns": {"cdl_name", "cdl_bullish", "cdl_bearish"},
    }
    groups.update({name: set(fields) for name, fields in EXTENSION_CHART_GROUPS.items()})
    keys = set()
    for indicator_name in enabled_indicators:
        keys.update(groups.get(str(indicator_name), set()))
    modes = {str(buy_mode or ""), str(sell_mode or "")}
    if modes & {"high_low", "hybrid_high_low"}:
        keys.update(groups["Prior Day High/Low"])
    if modes & {"pivot_points", "hybrid_pivot"}:
        keys.update(groups["Pivot Points (S/R)"])
    if modes & {"swing_points", "hybrid_swing"}:
        keys.update(groups["Swing Points"])
    return keys


def _prune_chart_record(record, selected_keys):
    if selected_keys is None:
        return record
    base_keys = {"time", "open", "high", "low", "close", "volume"}
    allowed = base_keys | selected_keys
    return {key: value for key, value in record.items() if key in allowed}


def _extension_chart_fields(row, safe_value, selected_keys=None):
    """Serialize only selected standard-extension values with stable API keys."""
    return {
        api_key: safe_value(row.get(column))
        for api_key, column in EXTENSION_CHART_FIELDS.items()
        if selected_keys is None or api_key in selected_keys
    }


def _wilder_atr(high, low, close, period):
    """Return a causal Welles Wilder ATR with the textbook SMA seed.

    The first available ATR is the arithmetic mean of the first ``period`` true
    ranges. Each following value uses Wilder's recursive smoothing. Keeping
    this calculation in one place prevents the chart indicator and strategy
    risk logic from drifting apart when a user changes the ATR period.
    """
    length = max(2, int(period))
    previous_close = close.shift(1)
    true_range = pd.concat([
        high - low,
        (high - previous_close).abs(),
        (low - previous_close).abs(),
    ], axis=1).max(axis=1, skipna=True)

    values = np.full(len(true_range), np.nan, dtype=float)
    if len(true_range) < length:
        return pd.Series(values, index=close.index, dtype=float)

    ranges = true_range.to_numpy(dtype=float)
    seed = ranges[:length].mean()
    if not np.isfinite(seed):
        return pd.Series(values, index=close.index, dtype=float)
    # Plain-array recursion: the previous Series.iloc loop cost ~240 ms per call on 1,200 bars (it ran twice per
    # evaluation). A non-finite range leaves NaN and, because the recursion needs the prior value, so does everything after.
    values[length - 1] = seed
    previous = float(seed)
    for position in range(length, len(ranges)):
        current = ranges[position]
        if not np.isfinite(current):
            break
        previous = ((previous * (length - 1)) + current) / length
        values[position] = previous
    return pd.Series(values, index=close.index, dtype=float)


def _indicator_period(value, fallback, minimum=1, maximum=2_000):
    """Normalize a configurable indicator period before passing it to math libraries."""
    try:
        resolved = int(float(value))
    except (TypeError, ValueError):
        resolved = int(fallback)
    return min(int(maximum), max(int(minimum), resolved))


def _setting_enabled(value, fallback=True):
    """Normalize bool-like persisted settings without treating the string '0' as true."""
    if value is None or value == "":
        return bool(fallback)
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"0", "false", "no", "off"}:
            return False
        if normalized in {"1", "true", "yes", "on"}:
            return True
    return bool(value)


def _required_indicator_history_bars(settings=None):
    """Return enough source bars to warm up selected long-lookback indicators."""
    if not isinstance(settings, dict):
        enabled = None
        sell_mode = ""
    else:
        raw_enabled = settings.get("enabled_indicators")
        enabled = (
            {str(value) for value in raw_enabled if value}
            if isinstance(raw_enabled, (list, tuple, set))
            else None
        )
        sell_mode = str(settings.get("sell_mode") or "").strip().lower()

    periods = []
    rsi_selected = (
        enabled is None
        or "RSI" in enabled
        or bool((settings or {}).get("_require_rsi_dependency", False))
    )
    if rsi_selected:
        # Wilder RSI needs ``period`` price changes, or period + 1 prices.
        periods.append(
            _indicator_period(
                _resolve_param("RSI_PERIOD", 14, settings=settings),
                14,
                minimum=2,
                maximum=500,
            ) + 1
        )

    macd_selected = enabled is None or "MACD" in enabled
    if macd_selected:
        macd_fast, macd_slow, macd_signal = _macd_periods(settings=settings)
        # MACD signal/histogram becomes available after the slow EMA and
        # signal EMA have both completed their warm-up windows.
        periods.append(macd_slow + macd_signal - 1)

    ema_selected = enabled is None or "EMA Cross" in enabled
    if ema_selected or sell_mode == "trend_rider":
        periods.append(
            _indicator_period(_resolve_param("EMA_FAST", 9, settings=settings), 9, maximum=500)
        )
        ema_use_second_line = _setting_enabled(
            _resolve_param("EMA_USE_SECOND_LINE", None, settings=settings),
            True,
        )
        if ema_use_second_line or sell_mode == "trend_rider":
            periods.append(
                _indicator_period(_resolve_param("EMA_SLOW", 21, settings=settings), 21, maximum=500)
            )

    sma_selected = enabled is None or "SMA" in enabled
    if sma_selected or sell_mode == "trend_rider":
        periods.append(
            _indicator_period(_resolve_param("SMA_PERIOD", 200, settings=settings), 200, maximum=500)
        )
        sma_use_second_line = _setting_enabled(
            _resolve_param("SMA_USE_SECOND_LINE", None, settings=settings),
            True,
        )
        if sma_selected and sma_use_second_line:
            periods.append(
                _indicator_period(_resolve_param("SMA_SECOND_PERIOD", 50, settings=settings), 50, maximum=500)
            )
    preset_bars = _strategy_preset_history_bars(settings)
    if preset_bars:
        periods.append(preset_bars)
    if not periods:
        return 0
    # The extra bars let the returned chart show a usable line segment rather
    # than only the first point at the end of the warm-up window.
    return max(periods) + 50


def _strategy_preset_history_bars(settings):
    """Completed bars a strategy preset needs before it may signal (0 if none)."""
    if not isinstance(settings, dict) or not settings.get("strategy_preset_enabled"):
        return 0
    try:
        import strategy_presets
        enabled, preset_id, variant_id = strategy_presets.normalize_strategy_preset(settings)
        return strategy_presets.warmup_bars(preset_id, variant_id) if enabled else 0
    except Exception:
        return 0


def _reconcile_live_splits(df, ticker):
    """Return ``(frame, quality)`` with live bars on a split-adjusted basis.

    Known split events are classified against the data (raw vs already
    adjusted) so nothing is adjusted twice. If corporate-action data is
    unavailable, a split-like jump marks the frame ``DEGRADED`` rather than
    guessing; consumers that cannot tolerate that (strategy presets) block new
    entries. Never raises: a lookup failure must not break live analysis.
    """
    try:
        from corporate_actions import reconcile_split_adjustment, suspect_split_jumps
        from corporate_actions_service import get_split_events

        first_day = pd.Timestamp(df.index[0]).date()
        lookup = get_split_events(ticker, start=first_day)
        if lookup.available:
            adjusted, quality, _ = reconcile_split_adjustment(df, lookup.events)
            return adjusted, quality
        return df, ("DEGRADED" if suspect_split_jumps(df) else "OK")
    except Exception as exc:
        print(f"[indicators] Split check skipped for {ticker}: {type(exc).__name__}")
        return df, "OK"


def _wilder_rsi(close, period):
    """Return textbook Wilder RSI with an arithmetic seed and causal warm-up.

    The first RSI requires ``period`` completed price changes. Earlier rows
    remain NaN so charts and backtests cannot receive a future RSI value.
    """
    length = _indicator_period(period, 14, minimum=2, maximum=500)
    values = pd.to_numeric(close, errors="coerce").to_numpy(dtype=float)
    rsi = pd.Series(np.nan, index=close.index, dtype=float)
    if len(values) <= length:
        return rsi

    deltas = np.diff(values)
    gains = np.where(deltas > 0, deltas, 0.0)
    losses = np.where(deltas < 0, -deltas, 0.0)
    if not np.isfinite(deltas[:length]).all():
        return rsi

    average_gain = float(gains[:length].mean())
    average_loss = float(losses[:length].mean())

    def reading(avg_gain, avg_loss):
        if avg_gain == 0.0 and avg_loss == 0.0:
            return 50.0
        if avg_loss == 0.0:
            return 100.0
        if avg_gain == 0.0:
            return 0.0
        relative_strength = avg_gain / avg_loss
        return 100.0 - (100.0 / (1.0 + relative_strength))

    rsi.iloc[length] = reading(average_gain, average_loss)
    for position in range(length + 1, len(values)):
        delta = deltas[position - 1]
        if not np.isfinite(delta):
            continue
        gain = max(float(delta), 0.0)
        loss = max(float(-delta), 0.0)
        average_gain = ((average_gain * (length - 1)) + gain) / length
        average_loss = ((average_loss * (length - 1)) + loss) / length
        rsi.iloc[position] = reading(average_gain, average_loss)
    return rsi


def _macd_periods(settings=None, fast=None, slow=None, signal=None):
    """Normalize MACD periods and always treat the lower EMA as the fast EMA."""
    fast_period = _indicator_period(
        _resolve_param("MACD_FAST", 12, settings=settings) if fast is None else fast,
        12,
        minimum=2,
        maximum=500,
    )
    slow_period = _indicator_period(
        _resolve_param("MACD_SLOW", 26, settings=settings) if slow is None else slow,
        26,
        minimum=2,
        maximum=500,
    )
    signal_period = _indicator_period(
        _resolve_param("MACD_SIGNAL", 9, settings=settings) if signal is None else signal,
        9,
        minimum=2,
        maximum=500,
    )
    return min(fast_period, slow_period), max(fast_period, slow_period), signal_period


def _rsi_thresholds(oversold, overbought):
    """Clamp RSI thresholds and consistently treat the lower value as oversold."""
    try:
        first = min(100.0, max(0.0, float(oversold)))
    except (TypeError, ValueError):
        first = 30.0
    try:
        second = min(100.0, max(0.0, float(overbought)))
    except (TypeError, ValueError):
        second = 70.0
    return min(first, second), max(first, second)


def _wilder_dmi_series(high, low, close, period):
    """Return standard Wilder ADX, +DI, and -DI without false warm-up zeros.

    Wilder smoothing starts +DI/-DI after one complete period and ADX after a
    second complete period. Values before those points remain NaN so chart
    renderers do not draw a misleading line from zero into the first valid
    reading.
    """
    length = _indicator_period(period, 14, minimum=2, maximum=500)
    index = close.index
    count = len(close)
    adx = pd.Series(np.nan, index=index, dtype=float)
    di_plus = pd.Series(np.nan, index=index, dtype=float)
    di_minus = pd.Series(np.nan, index=index, dtype=float)
    if count < length:
        return adx, di_plus, di_minus

    high_values = pd.to_numeric(high, errors="coerce").to_numpy(dtype=float)
    low_values = pd.to_numeric(low, errors="coerce").to_numpy(dtype=float)
    close_values = pd.to_numeric(close, errors="coerce").to_numpy(dtype=float)
    true_range = np.full(count, np.nan, dtype=float)
    plus_dm = np.zeros(count, dtype=float)
    minus_dm = np.zeros(count, dtype=float)

    if np.isfinite(high_values[0]) and np.isfinite(low_values[0]):
        true_range[0] = high_values[0] - low_values[0]
    for position in range(1, count):
        if not all(np.isfinite(value) for value in (
            high_values[position], low_values[position], close_values[position - 1]
        )):
            continue
        true_range[position] = max(
            high_values[position] - low_values[position],
            abs(high_values[position] - close_values[position - 1]),
            abs(low_values[position] - close_values[position - 1]),
        )
        up_move = high_values[position] - high_values[position - 1]
        down_move = low_values[position - 1] - low_values[position]
        plus_dm[position] = up_move if up_move > down_move and up_move > 0 else 0.0
        minus_dm[position] = down_move if down_move > up_move and down_move > 0 else 0.0

    seed_position = length - 1
    initial_tr = true_range[:length]
    if not np.isfinite(initial_tr).all():
        return adx, di_plus, di_minus

    smoothed_tr = np.full(count, np.nan, dtype=float)
    smoothed_plus = np.full(count, np.nan, dtype=float)
    smoothed_minus = np.full(count, np.nan, dtype=float)
    smoothed_tr[seed_position] = initial_tr.sum()
    smoothed_plus[seed_position] = plus_dm[:length].sum()
    smoothed_minus[seed_position] = minus_dm[:length].sum()
    for position in range(length, count):
        if not np.isfinite(true_range[position]):
            continue
        smoothed_tr[position] = (
            smoothed_tr[position - 1]
            - (smoothed_tr[position - 1] / length)
            + true_range[position]
        )
        smoothed_plus[position] = (
            smoothed_plus[position - 1]
            - (smoothed_plus[position - 1] / length)
            + plus_dm[position]
        )
        smoothed_minus[position] = (
            smoothed_minus[position - 1]
            - (smoothed_minus[position - 1] / length)
            + minus_dm[position]
        )

    valid_tr = np.isfinite(smoothed_tr) & (smoothed_tr > 0)
    plus_values = np.full(count, np.nan, dtype=float)
    minus_values = np.full(count, np.nan, dtype=float)
    plus_values[valid_tr] = 100.0 * smoothed_plus[valid_tr] / smoothed_tr[valid_tr]
    minus_values[valid_tr] = 100.0 * smoothed_minus[valid_tr] / smoothed_tr[valid_tr]
    di_plus.iloc[:] = plus_values
    di_minus.iloc[:] = minus_values

    denominator = plus_values + minus_values
    dx = np.full(count, np.nan, dtype=float)
    valid_dx = np.isfinite(denominator) & (denominator > 0)
    dx[valid_dx] = 100.0 * np.abs(plus_values[valid_dx] - minus_values[valid_dx]) / denominator[valid_dx]
    flat_dx = np.isfinite(plus_values) & np.isfinite(minus_values) & (denominator == 0)
    dx[flat_dx] = 0.0

    first_adx_position = (2 * length) - 2
    if first_adx_position < count:
        seed_dx = dx[seed_position:first_adx_position + 1]
        if len(seed_dx) == length and np.isfinite(seed_dx).all():
            adx.iloc[first_adx_position] = float(seed_dx.mean())
            for position in range(first_adx_position + 1, count):
                if not np.isfinite(dx[position]) or not np.isfinite(adx.iloc[position - 1]):
                    continue
                adx.iloc[position] = (
                    (adx.iloc[position - 1] * (length - 1)) + dx[position]
                ) / length
    return adx, di_plus, di_minus


def _session_vwap_series(df, high, low, close, volume, ticker=None, regular_only=False):
    """Calculate session VWAP using the exchange-local calendar date.

    Webull's VWAP is a cumulative intraday typical-price/volume average. The
    session key must use New York time for equities; using a raw UTC date can
    incorrectly reset the line during an evening/extended-hours boundary.
    """
    typical_price = (high + low + close) / 3.0
    outside_regular = None
    if regular_only:
        # Only 09:30-16:00 ET bars build the line; pre/post-market bars show no VWAP instead of a diluted one.
        outside_regular = pd.Series(~_regular_hours_mask(df, ticker), index=df.index)
        volume = volume.where(~outside_regular, 0.0)
    pv = typical_price * volume
    session_key = pd.Series(_exchange_session_dates_for_df(df, ticker), index=df.index)
    session_pv = pv.groupby(session_key).cumsum()
    session_volume = volume.groupby(session_key).cumsum()
    vwap = session_pv / session_volume.replace(0, np.nan)
    # Zero-volume bars carry the session's last valid VWAP instead of faking the close; only an
    # unseeded session start stays NaN (reported as "unavailable", never as a signal).
    vwap = vwap.groupby(session_key).ffill()
    if outside_regular is not None:
        vwap = vwap.where(~outside_regular)
    # Session VWAP resets every session, so on daily-or-larger bars it is one typical price per bar and says
    # nothing about position vs. the session. Mark it unavailable instead of voting.
    if len(df.index) > 2:
        try:
            median_gap = pd.Series(df.index).diff().median()
            if pd.notna(median_gap) and median_gap >= pd.Timedelta(hours=20):
                vwap = vwap * np.nan
        except (TypeError, ValueError):
            pass
    return vwap


def _auto_avwap_series(df, high, low, close, volume, ticker=None, session_vwap=None, param=None):
    """Calculate the legacy volume-spike anchored AVWAP separately from VWAP."""
    param = param or (lambda _key, fallback=None: fallback)
    typical_price = (high + low + close) / 3.0
    pv = typical_price * volume
    cumulative_pv = pv.cumsum().to_numpy()
    cumulative_volume = volume.cumsum().to_numpy()
    volume_ma_period = max(2, int(float(param("VOL_MA_PERIOD", 20))))
    volume_spike_multiplier = max(0.0, float(param("VOL_SPIKE_MULTIPLIER", 1.5)))
    # A spike needs a full volume average behind it (no partial-window means in the first bars).
    volume_ma = volume.rolling(window=volume_ma_period, min_periods=volume_ma_period).mean()
    spike_condition = volume > (volume_ma * volume_spike_multiplier)
    index_series = pd.Series(range(len(df)), index=df.index)
    anchor_mode = str(param("AUTO_AVWAP_ANCHOR", "volume_spike") or "volume_spike").lower()
    if anchor_mode == "session_open":
        session_key = pd.Series(_exchange_session_dates_for_df(df, ticker), index=df.index)
        anchor_condition = session_key != session_key.shift(1)
        anchor_indices = index_series.where(anchor_condition).ffill()
    elif anchor_mode == "swing":
        # Anchor at the latest confirmed swing high/low. A pivot is only known `strength` bars later, so the anchor
        # is switched on at the confirmation bar and never looks ahead.
        strength = max(2, int(float(param("AUTO_AVWAP_SWING_STRENGTH", 5) or 5)))
        highs, lows = high.to_numpy(dtype=float), low.to_numpy(dtype=float)
        anchor_at = np.full(len(df), np.nan)
        for pivot in range(strength, len(df) - strength):
            window_high = highs[pivot - strength: pivot + strength + 1]
            window_low = lows[pivot - strength: pivot + strength + 1]
            if highs[pivot] >= window_high.max() or lows[pivot] <= window_low.min():
                anchor_at[pivot + strength] = pivot
        anchor_indices = pd.Series(anchor_at, index=df.index).ffill()
    else:
        anchor_indices = index_series.where(spike_condition).ffill()
    session_values = (session_vwap if session_vwap is not None else _session_vwap_series(
        df, high, low, close, volume, ticker=ticker
    )).to_numpy()
    close_values = close.to_numpy()
    values = np.empty(len(df), dtype=float)
    for current_index, anchor_index in enumerate(anchor_indices.to_numpy()):
        if pd.isna(anchor_index):
            values[current_index] = session_values[current_index]
            continue
        anchor_index = int(anchor_index)
        anchor_pv = cumulative_pv[anchor_index - 1] if anchor_index > 0 else 0.0
        anchor_volume = cumulative_volume[anchor_index - 1] if anchor_index > 0 else 0.0
        total_volume = cumulative_volume[current_index] - anchor_volume
        values[current_index] = (
            (cumulative_pv[current_index] - anchor_pv) / total_volume
            if total_volume > 0 else close_values[current_index]
        )
    return pd.Series(values, index=df.index), anchor_indices


def _wilder_psar(high, low, close, step=0.02, max_step=0.2):
    """Wilder's Parabolic SAR (TradingView/TA-Lib convention).

    The first trend is taken from the first two closes; the SAR never enters the prior two bars' range and flips
    when price touches it. The reversal bar's SAR is the prior extreme point."""
    h, l, c = (np.asarray(x, dtype=float) for x in (high, low, close))
    n = len(c)
    out = np.full(n, np.nan)
    if n < 2:
        return pd.Series(out, index=close.index)
    step = max(1e-4, float(step))
    max_step = max(step, float(max_step))
    up = c[1] >= c[0]
    sar = l[0] if up else h[0]
    ep = h[0] if up else l[0]
    af = step
    out[0] = sar
    for i in range(1, n):
        sar = sar + af * (ep - sar)
        if up:
            sar = min(sar, l[i - 1], l[i - 2] if i >= 2 else l[i - 1])
            if l[i] < sar:
                up, sar, ep, af = False, ep, l[i], step
            elif h[i] > ep:
                ep, af = h[i], min(af + step, max_step)
        else:
            sar = max(sar, h[i - 1], h[i - 2] if i >= 2 else h[i - 1])
            if h[i] > sar:
                up, sar, ep, af = True, ep, h[i], step
            elif l[i] < ep:
                ep, af = l[i], min(af + step, max_step)
        out[i] = sar
    return pd.Series(out, index=close.index)


def _finite_or_none(value):
    """float(value), or None when it is NaN/inf, so API payloads stay JSON-safe."""
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if np.isfinite(number) else None


def _flag(value):
    """Interpret a stored on/off setting (bool, number or string)."""
    return str(value).strip().lower() in ("1", "true", "yes", "on", "1.0")


def _atr_period(value, fallback=14):
    """Normalize a user-supplied ATR length without accepting invalid input."""
    try:
        resolved = int(float(value))
    except (TypeError, ValueError):
        resolved = int(fallback)
    return min(2_000, max(2, resolved))


def _atr_range_multiplier(value, fallback=1.0):
    """Normalize the display-only ATR range width to its supported bounds."""
    try:
        resolved = float(value)
    except (TypeError, ValueError):
        resolved = float(fallback)
    if not np.isfinite(resolved):
        resolved = float(fallback)
    return min(10.0, max(0.1, resolved))


def _atr_expansion_threshold(value, fallback=5.0):
    """Normalize the ATR baseline-expansion threshold expressed in percent."""
    try:
        resolved = float(value)
    except (TypeError, ValueError):
        resolved = float(fallback)
    if not np.isfinite(resolved):
        resolved = float(fallback)
    return min(100.0, max(0.0, resolved))


def _get_wb_data_provider():
    global wb_data_provider
    if wb_data_provider is None:
        from webull_broker import WebullBroker
        wb_data_provider = WebullBroker()
    return wb_data_provider

# ---------------------------------------------------------------------------
# ---------------------------------------------------------------------------
# Mystic Pulse V2.0 Utilities
# ---------------------------------------------------------------------------
def _wilders_smooth_seeded(series, length):
    """Wilder smoothing (running sum form) seeded with the sum of the first ``length`` valid values.

    Values before the first complete window stay NaN, so nothing is reported from a partly warmed-up calculation."""
    values = np.asarray(series, dtype=float)
    result = np.full(len(values), np.nan)
    length = max(1, int(length))
    finite = np.flatnonzero(np.isfinite(values))
    if finite.size == 0:
        return result
    start = int(finite[0])
    seed_end = start + length - 1
    if seed_end >= len(values) or not np.isfinite(values[start:seed_end + 1]).all():
        return result
    result[seed_end] = values[start:seed_end + 1].sum()
    for i in range(seed_end + 1, len(values)):
        if not np.isfinite(values[i]):
            continue
        result[i] = result[i - 1] - (result[i - 1] / length) + values[i]
    return result


def _wilders_smooth(series, length):
    """Replicates Pine Script smoothed logic."""
    smooth = np.zeros(len(series))
    for i in range(len(series)):
        if i == 0:
            smooth[i] = series[i]
        else:
            if length == 0:
                smooth[i] = series[i]
            else:
                smooth[i] = smooth[i-1] - (smooth[i-1] / length) + series[i]
    return smooth

def _get_mystic_v2_presets(timeframe):
    """Maps Alpaca timeframes to V2.0 adaptive presets."""
    defaults = {
        '1m':  {'adx': 21, 'smth': 3},
        '5m':  {'adx': 18, 'smth': 2},
        '15m': {'adx': 14, 'smth': 1},
        '30m': {'adx': 12, 'smth': 1},
        '1h':  {'adx': 9,  'smth': 1},
        '4h':  {'adx': 7,  'smth': 1},
        '1d':  {'adx': 5,  'smth': 1}
    }
    key = '1h'
    if timeframe == '1Min': key = '1m'
    elif timeframe == '2Min': key = '5m'
    elif timeframe == '3Min': key = '5m'
    elif timeframe == '5Min': key = '5m'
    elif timeframe == '10Min': key = '15m'
    elif timeframe == '15Min': key = '15m'
    elif timeframe == '30Min': key = '30m'
    elif timeframe == '1Hour': key = '1h'
    elif timeframe == '2Hour': key = '1h'
    elif timeframe == '4Hour': key = '4h'
    elif timeframe == '1Day': key = '1d'
    return defaults.get(key, defaults['1h'])

def _timeframe_seconds(timeframe):
    tf = str(timeframe or "").upper().strip()
    if tf in ("1D", "1DAY", "DAY", "D"):
        return 86400
    import re
    match = re.search(r"\d+", tf)
    value = int(match.group()) if match else 5
    if "HOUR" in tf or tf.endswith("H"):
        return value * 3600
    if "SEC" in tf or tf.endswith("S"):
        return value
    if "MIN" in tf or tf.endswith("M"):
        return value * 60
    return 300

def _session_dates_for_df(df, ticker=None):
    """Return canonical America/Chicago calendar-session dates for a frame.

    Live provider frames are timezone-aware (normally UTC). Historical Yahoo
    frames are normalized by data_manager to naive Eastern wall-clock times
    for equities and naive UTC times for crypto. Keeping that distinction in
    one helper prevents extended-hours premarket bars from being assigned to
    the wrong session.
    """
    timestamps = pd.DatetimeIndex(df.index)
    if timestamps.tz is None:
        local_tz = "UTC" if _is_crypto_ticker(ticker) else "America/New_York"
        timestamps = timestamps.tz_localize(
            local_tz,
            ambiguous="infer",
            nonexistent="shift_forward",
        )
    # Crypto trades around the clock; its daily bar is the UTC day, not the Chicago equity date.
    session_tz = "UTC" if _is_crypto_ticker(ticker) else "America/Chicago"
    return timestamps.tz_convert(session_tz).date


def _regular_hours_mask(df, ticker=None):
    """True for bars inside the 09:30-16:00 New York regular session (all True for crypto or daily-or-larger bars)."""
    if df is None or len(df) == 0:
        return np.ones(0, dtype=bool)
    if _is_crypto_ticker(ticker):
        return np.ones(len(df), dtype=bool)
    timestamps = pd.DatetimeIndex(df.index)
    if len(timestamps) > 2:
        median_gap = pd.Series(timestamps).diff().median()
        if pd.notna(median_gap) and median_gap >= pd.Timedelta(hours=20):
            return np.ones(len(df), dtype=bool)
    if timestamps.tz is None:
        timestamps = timestamps.tz_localize("America/New_York", ambiguous="infer", nonexistent="shift_forward")
    else:
        timestamps = timestamps.tz_convert("America/New_York")
    minutes = timestamps.hour * 60 + timestamps.minute
    return np.asarray((minutes >= 570) & (minutes < 960))


def _exchange_session_dates_for_df(df, ticker=None):
    """Return the exchange-local calendar date used by session VWAP."""
    timestamps = pd.DatetimeIndex(df.index)
    if timestamps.tz is None:
        local_tz = "UTC" if _is_crypto_ticker(ticker) else "America/New_York"
        timestamps = timestamps.tz_localize(
            local_tz,
            ambiguous="infer",
            nonexistent="shift_forward",
        )
    else:
        local_tz = "UTC" if _is_crypto_ticker(ticker) else "America/New_York"
        timestamps = timestamps.tz_convert(local_tz)
    return timestamps.date


def _completed_candles_for_live_chart(df, timeframe, safety_delay_seconds=5, ticker=None):
    """Return only candles that are fully closed for live dashboard/bot views."""
    if df is None or df.empty:
        return df

    seconds = _timeframe_seconds(timeframe)
    if seconds <= 0:
        return df.iloc[:0]

    index = df.index
    if getattr(index, "tz", None) is None:
        local_tz = "UTC" if _is_crypto_ticker(ticker) else "America/New_York"
        index = index.tz_localize(local_tz, ambiguous="infer", nonexistent="shift_forward")
    else:
        index = index.tz_convert("UTC")

    now_utc = pd.Timestamp.now(tz="UTC") - pd.Timedelta(seconds=safety_delay_seconds)
    completed_mask = (index + pd.Timedelta(seconds=seconds)) <= now_utc
    return df.loc[completed_mask].copy()


def _evaluation_candle_metadata(chart_df, timeframe):
    """Describe the committed candle used by live evaluation."""
    if chart_df is None or chart_df.empty:
        return {
            "candle_open_ts": None,
            "candle_close_ts": None,
            "candle_key": None,
            "candle_status": "waiting",
        }

    try:
        candle_open_ts = _unix_timestamp(chart_df.index[-1])
        candle_close_ts = candle_open_ts + _timeframe_seconds(timeframe)
        if candle_close_ts > time.time():
            return {
                "candle_open_ts": candle_open_ts,
                "candle_close_ts": candle_close_ts,
                "candle_key": None,
                "candle_status": "waiting",
            }
        return {
            "candle_open_ts": candle_open_ts,
            "candle_close_ts": candle_close_ts,
            "candle_key": f"{str(timeframe or '').upper()}:{candle_open_ts}",
            "candle_status": "closed",
        }
    except Exception:
        return {
            "candle_open_ts": None,
            "candle_close_ts": None,
            "candle_key": None,
            "candle_status": "waiting",
        }


def _provider_time_to_datetime(values):
    """Normalize provider timestamps delivered as epochs or date strings.

    Webull can return epoch timestamps as strings.  Calling ``pd.to_datetime``
    directly on those strings makes pandas interpret them as years instead of
    epoch seconds, which silently empties the completed-candle probe.  Parse
    numeric-looking values as one epoch unit and retain ISO/date parsing for
    genuinely textual timestamps.
    """
    raw = pd.Series(values)
    numeric = pd.to_numeric(raw, errors="coerce")
    finite = numeric.dropna()
    result = pd.Series(pd.NaT, index=raw.index, dtype="datetime64[ns, UTC]")
    if not finite.empty:
        unit = "ms" if float(finite.abs().median()) > 1e11 else "s"
        numeric_mask = numeric.notna()
        result.loc[numeric_mask] = pd.to_datetime(
            numeric.loc[numeric_mask], unit=unit, utc=True, errors="coerce"
        )
    non_numeric_mask = numeric.isna()
    if non_numeric_mask.any():
        result.loc[non_numeric_mask] = pd.to_datetime(
            raw.loc[non_numeric_mask], utc=True, errors="coerce"
        )
    return result


def get_latest_completed_candle_metadata(ticker, timeframe="5Min", market_session="extended"):
    """Fetch only enough Webull data to identify the newest closed candle.

    This is intentionally separate from ``get_full_analysis``. Screener can
    poll this small probe each interval and skip the 1,200-bar indicator
    calculation when the completed candle key is unchanged.
    """
    tf_map = {
        "30Sec": "s30", "1Min": "m1", "2Min": "m2", "3Min": "m3",
        "5Min": "m5", "10Min": "m10", "15Min": "m15", "30Min": "m30",
        "1Hour": "m60", "2Hour": "m120", "4Hour": "m240", "1Day": "D",
    }
    timeframe_key = str(timeframe or "5Min")
    is_crypto = _is_crypto_ticker(ticker)
    requested_session = str(market_session or "extended").lower()
    provider_sessions = "RTH" if not is_crypto and requested_session == "regular" else "RTH,PRE,ATH"
    bars = _get_wb_data_provider().get_bars(
        ticker,
        timespan=tf_map.get(timeframe_key, "m5"),
        count=3,
        trading_sessions=provider_sessions,
    )
    if isinstance(bars, list) and bars and isinstance(bars[0], dict) and "result" in bars[0]:
        bars = bars[0].get("result") or []
    if not isinstance(bars, list) or not bars:
        return {"candle_open_ts": None, "candle_close_ts": None, "candle_key": None, "candle_status": "unavailable"}
    frame = pd.DataFrame(bars)
    if frame.empty or "time" not in frame.columns:
        return {"candle_open_ts": None, "candle_close_ts": None, "candle_key": None, "candle_status": "unavailable"}
    try:
        frame["timestamp"] = _provider_time_to_datetime(frame["time"])
        frame = frame.set_index("timestamp").sort_index()
        frame = frame[~frame.index.duplicated(keep="last")]
        completed = _completed_candles_for_live_chart(frame, timeframe_key, safety_delay_seconds=5, ticker=ticker)
        return _evaluation_candle_metadata(completed, timeframe_key)
    except Exception:
        return {"candle_open_ts": None, "candle_close_ts": None, "candle_key": None, "candle_status": "unavailable"}


def _live_chart_display_limit(timeframe, ticker=None):
    """Cap serialized candles at the provider limit for the selected asset."""
    tf = str(timeframe or "").upper()
    # Webull stock M1 supports up to 1,650 bars; its other stock intervals
    # support up to 1,200. Webull crypto is capped at 1,200 for every interval.
    if tf == "1MIN" and not _is_crypto_ticker(ticker):
        return 1650
    return 1200


def _validate_live_candle_data(df, ticker, timeframe, required_bars=0):
    """Reject incomplete history before it can influence a live signal.

    Crypto is continuous, so a large interval means bars are missing. Equity
    sessions legitimately jump overnight, on weekends, and at holidays; for
    those symbols only gaps within the same exchange-local calendar day are
    considered data defects.
    """
    if df is None or df.empty:
        return "No candle history was returned by the provider."
    try:
        required = int(required_bars or 0)
    except (TypeError, ValueError):
        required = 0
    if required > 0 and len(df) < required:
        return f"Only {len(df)} completed candles returned; {required} are required for indicator warm-up."

    required_columns = {"Open", "High", "Low", "Close", "Volume"}
    if not required_columns.issubset(set(df.columns)):
        missing = ", ".join(sorted(required_columns - set(df.columns)))
        return f"Candle history is missing required fields: {missing}."
    numeric = df[list(required_columns)].apply(pd.to_numeric, errors="coerce")
    window = numeric.tail(min(len(numeric), max(required, 30)))
    if not np.isfinite(window.to_numpy(dtype=float)).all():
        return "Candle history contains missing or non-finite OHLCV values in the analysis window."
    prices = window[["Open", "High", "Low", "Close"]]
    if (prices <= 0).any().any() or (window["Volume"] < 0).any():
        return "Candle history contains nonpositive prices or negative volume."
    if (
        (window["High"] < prices.max(axis=1)).any()
        or (window["Low"] > prices.min(axis=1)).any()
    ):
        return "Candle high/low values do not contain the open and close."

    try:
        timestamps = pd.DatetimeIndex(df.index)
        if timestamps.hasnans or timestamps.has_duplicates or not timestamps.is_monotonic_increasing:
            return "Candle timestamps must be present, unique, and chronological."
        if timestamps.tz is None:
            timestamps = timestamps.tz_localize("UTC" if _is_crypto_ticker(ticker) else "America/New_York")
        else:
            timestamps = timestamps.tz_convert("UTC")
        seconds = timestamps.astype("int64").to_numpy(dtype="int64") / 1_000_000_000
        deltas = seconds[1:] - seconds[:-1]
        tf_seconds = {
            "30SEC": 30, "1MIN": 60, "2MIN": 120, "3MIN": 180,
            "5MIN": 300, "10MIN": 600, "15MIN": 900, "30MIN": 1800,
            "1HOUR": 3600, "2HOUR": 7200, "4HOUR": 14400, "1DAY": 86400,
        }.get(str(timeframe or "").upper())
        if tf_seconds and len(deltas):
            # Inspect the latest history window; older gaps cannot affect the
            # current rolling indicators once sufficient warm-up is present.
            recent_start = max(0, len(deltas) - 250)
            recent_deltas = deltas[recent_start:]
            is_crypto = _is_crypto_ticker(ticker)
            if is_crypto:
                bad_gap = recent_deltas > (tf_seconds * 1.5)
            else:
                eastern = timestamps.tz_convert("America/New_York")
                same_day = eastern[1:].date == eastern[:-1].date
                same_day = same_day[recent_start:]
                bad_gap = (recent_deltas > (tf_seconds * 1.5)) & same_day
            if bool(bad_gap.any()):
                largest = float(recent_deltas[bad_gap].max())
                return f"Candle history has a {largest:.0f}s gap where {tf_seconds}s was expected."
    except Exception as exc:
        return f"Candle timestamp validation failed: {type(exc).__name__}."
    return ""


def _bounded_chart_frame(chart_df, settings=None):
    if chart_df is None or chart_df.empty:
        return chart_df
    try:
        limit = int((settings or {}).get("_chart_display_bars", 0) or 0)
    except (TypeError, ValueError):
        limit = 0
    if limit <= 0 or len(chart_df) <= limit:
        return chart_df
    return chart_df.tail(limit)


def _prior_session_ohlc_from_df(chart_df, ticker=None):
    """Derive prior-session OHLC from the full completed calculation frame."""
    if chart_df is None or chart_df.empty:
        return None
    try:
        session_key = pd.Series(_session_dates_for_df(chart_df, ticker), index=chart_df.index)
        sessions = chart_df.groupby(session_key).agg({"High": "max", "Low": "min", "Close": "last"})
        if len(sessions) < 2:
            return None
        prior_date = sessions.index[-2]
        prior = sessions.iloc[-2]
        return {
            "date": str(prior_date),
            "high": float(prior["High"]),
            "low": float(prior["Low"]),
            "close": float(prior["Close"]),
        }
    except Exception as exc:
        print(f"[indicators] Prior-session OHLC summary failed: {exc}")
        return None

def _volume_profile_distribution(session_df, rows=48, value_area_pct=70.0):
    """Build an OHLCV approximation of a session volume-at-price profile.

    The provider supplies candle totals rather than trade-at-price records, so
    exact tape-level volume cannot be reconstructed. Each candle's volume is
    allocated across the price bins touched by its high-low range. POC, VAH,
    and VAL are all derived from this same bin array and the array is returned
    for the chart's VPVR renderer.
    """
    if session_df is None or session_df.empty:
        return None

    try:
        rows = int(float(rows))
    except Exception:
        rows = 48
    rows = min(120, max(12, rows))

    try:
        value_area_pct = float(value_area_pct)
    except Exception:
        value_area_pct = 70.0
    value_area_pct = min(95.0, max(50.0, value_area_pct)) / 100.0

    low_price = float(pd.to_numeric(session_df["Low"], errors="coerce").min())
    high_price = float(pd.to_numeric(session_df["High"], errors="coerce").max())
    if not np.isfinite(low_price) or not np.isfinite(high_price) or high_price <= low_price:
        close_vals = pd.to_numeric(session_df["Close"], errors="coerce").dropna()
        if close_vals.empty:
            return None
        price = float(close_vals.iloc[-1])
        return {
            "poc": price,
            "vah": price,
            "val": price,
            "session_low": price,
            "session_high": price,
            "rows": 1,
            "value_area_pct": value_area_pct * 100.0,
            "total_volume": 0.0,
            "bins": [{
                "low": price,
                "high": price,
                "price": price,
                "volume": 0.0,
                "buy_volume": 0.0,
                "sell_volume": 0.0,
            }],
        }

    edges = np.linspace(low_price, high_price, rows + 1)
    centers = (edges[:-1] + edges[1:]) / 2.0
    profile = np.zeros(rows, dtype=float)
    buy_profile = np.zeros(rows, dtype=float)
    sell_profile = np.zeros(rows, dtype=float)

    for _, row in session_df.iterrows():
        try:
            lo = float(row.get("Low", np.nan))
            hi = float(row.get("High", np.nan))
            close = float(row.get("Close", np.nan))
            open_price = float(row.get("Open", np.nan))
            vol = float(row.get("Volume", 0.0))
        except (TypeError, ValueError):
            continue
        if not np.isfinite(vol) or vol <= 0:
            continue
        is_buy_volume = (
            np.isfinite(open_price) and np.isfinite(close) and close >= open_price
        )
        if not np.isfinite(lo) or not np.isfinite(hi) or hi <= lo:
            price = close if np.isfinite(close) else (lo if np.isfinite(lo) else np.nan)
            if not np.isfinite(price):
                continue
            idx = int(np.clip(np.searchsorted(edges, price, side="right") - 1, 0, rows - 1))
            profile[idx] += vol
            if is_buy_volume:
                buy_profile[idx] += vol
            else:
                sell_profile[idx] += vol
            continue

        overlaps = np.maximum(0.0, np.minimum(edges[1:], hi) - np.maximum(edges[:-1], lo))
        overlap_sum = overlaps.sum()
        if overlap_sum > 0:
            allocation = vol * (overlaps / overlap_sum)
            profile += allocation
            if is_buy_volume:
                buy_profile += allocation
            else:
                sell_profile += allocation
        else:
            idx = int(np.clip(np.searchsorted(edges, close, side="right") - 1, 0, rows - 1))
            profile[idx] += vol
            if is_buy_volume:
                buy_profile[idx] += vol
            else:
                sell_profile[idx] += vol

    total_volume = profile.sum()
    if total_volume <= 0:
        return None

    poc_idx = int(np.argmax(profile))
    target_volume = total_volume * value_area_pct
    included_volume = profile[poc_idx]
    low_idx = high_idx = poc_idx

    # Market Profile / CME value-area rule: compare the next TWO rows above with the next TWO below and add the
    # larger pair, until the target share of volume is inside the area.
    while included_volume < target_volume and (low_idx > 0 or high_idx < rows - 1):
        up_rows = profile[high_idx + 1: high_idx + 3]
        down_rows = profile[max(0, low_idx - 2): low_idx][::-1]
        up_volume = float(up_rows.sum()) if len(up_rows) else -1.0
        down_volume = float(down_rows.sum()) if len(down_rows) else -1.0
        if up_volume >= down_volume and len(up_rows):
            included_volume += up_volume
            high_idx += len(up_rows)
        elif len(down_rows):
            included_volume += down_volume
            low_idx -= len(down_rows)
        else:
            break

    poc = float(centers[poc_idx])
    val = float(edges[low_idx])
    vah = float(edges[high_idx + 1])
    bins = [
        {
            "low": float(edges[idx]),
            "high": float(edges[idx + 1]),
            "price": float(centers[idx]),
            "volume": float(profile[idx]),
            "buy_volume": float(buy_profile[idx]),
            "sell_volume": float(sell_profile[idx]),
        }
        for idx in range(rows)
    ]
    return {
        "poc": poc,
        "vah": vah,
        "val": val,
        "session_low": low_price,
        "session_high": high_price,
        "rows": rows,
        "value_area_pct": value_area_pct * 100.0,
        "total_volume": float(total_volume),
        "bins": bins,
    }


def _volume_profile_levels(session_df, rows=48, value_area_pct=70.0):
    """Return POC, VAH, and VAL from the shared volume profile bins."""
    profile = _volume_profile_distribution(session_df, rows=rows, value_area_pct=value_area_pct)
    if not profile:
        return (np.nan, np.nan, np.nan)
    return (float(profile["poc"]), float(profile["vah"]), float(profile["val"]))

def _is_crypto_symbol(symbol):
    clean = str(symbol or "").upper().replace("/", "").replace("-", "")
    return clean.endswith(("USD", "USDT", "USDC")) or clean in {"BTC", "ETH", "XRP", "DOGE", "LTC", "ADA", "SOL"}

def _unix_timestamp(t):
    """Convert a pandas Timestamp to UNIX epoch seconds, assuming naive means America/New_York."""
    try:
        if t.tz is None:
            t = t.tz_localize("America/New_York")
        return int(t.timestamp())
    except Exception:
        return int(t.timestamp()) if hasattr(t, "timestamp") else int(t)

def _df_row_to_swing_candle(index_value, row):
    try:
        ts = _unix_timestamp(index_value)
    except Exception:
        ts = index_value
    return {
        "time": ts,
        "open": float(row.get("Open", np.nan)),
        "high": float(row.get("High", np.nan)),
        "low": float(row.get("Low", np.nan)),
        "close": float(row.get("Close", np.nan)),
        "volume": float(row.get("Volume", 0.0) or 0.0),
    }

def _add_swing_point_columns(df, ticker, settings, param_getter, timeframe=None):
    """Populate rolling swing support/resistance columns without lookahead."""
    for col in (
        "Swing_Support_1", "Swing_Support_2", "Swing_Support_3", "Swing_Support_4", "Swing_Support_5",
        "Swing_Resistance_1", "Swing_Resistance_2", "Swing_Resistance_3", "Swing_Resistance_4", "Swing_Resistance_5",
        "Swing_Bullish", "Swing_Bearish",
    ):
        df[col] = False if col in {"Swing_Bullish", "Swing_Bearish"} else np.nan

    try:
        lookback_days = max(1, min(365, int(float(param_getter("SWING_POINTS_LOOKBACK_DAYS", 20)))))
    except Exception:
        lookback_days = 20
    try:
        level_count = max(1, min(5, int(float(param_getter("SWING_POINTS_LEVEL_COUNT", 1)))))
    except Exception:
        level_count = 1

    try:
        pivot_strength = max(1, min(10, int(float(param_getter("SWING_POINTS_STRENGTH", 2)))))
    except Exception:
        pivot_strength = 2

    entry_mode = str(param_getter("SWING_POINTS_ENTRY_MODE", "bounce_retest") or "bounce_retest")
    if entry_mode not in {"bounce_retest", "breakout_retest"}:
        entry_mode = "bounce_retest"

    market_session = "Regular"
    if isinstance(settings, dict):
        market_session = settings.get("market_session") or settings.get("MARKET_SESSION") or market_session
    is_crypto = _is_crypto_symbol(ticker)
    if is_crypto:
        market_session = "Crypto24"

    history = []
    support_cols = [f"Swing_Support_{i}" for i in range(1, 6)]
    resistance_cols = [f"Swing_Resistance_{i}" for i in range(1, 6)]

    eval_context = active_evaluation_context.get()
    settings_source = str(settings.get("source", "") if isinstance(settings, dict) else "").lower()
    
    # Check if Swing Points were explicitly enabled, otherwise we only need recent history for Confluence Grid
    enabled_inds = settings.get("enabled_indicators") if isinstance(settings, dict) else None
    explicitly_enabled = enabled_inds is not None and "Swing Points" in enabled_inds
    is_backtest_context = (eval_context == "backtest" or settings_source == "backtest") and explicitly_enabled
    
    calc_df = df

    if not is_backtest_context and len(df) > 600:
        seconds = max(60, _timeframe_seconds(timeframe))
        minutes = max(1.0, seconds / 60.0)
        session_name = str(market_session or "Regular").strip().lower()
        if is_crypto or session_name in {"crypto24", "24h", "all"}:
            bars_per_day = max(1, int(round(1440 / minutes)))
        elif session_name in {"extended", "extended_hours"}:
            bars_per_day = max(1, int(round(960 / minutes)))
        else:
            bars_per_day = max(1, int(round(390 / minutes)))

        # Keep enough candles for the requested structural lookback, while
        # bounding the live rolling calculation for low timeframes. The chart
        # response itself is capped separately; the extra warmup here keeps
        # the first displayed Swing level from starting with a cold context.
        # Measure the real bars per session from the data itself: a fixed 390-minute guess made the live window
        # shorter than the lookback whenever pre/post-market bars were present, so live and backtest disagreed.
        try:
            observed = pd.Series(1, index=df.index).groupby(_exchange_session_dates_for_df(df, ticker)).sum()
            bars_per_day = max(bars_per_day, int(observed.quantile(0.9))) if len(observed) else bars_per_day
        except Exception:
            pass
        requested_bars = max(240, lookback_days * bars_per_day)
        display_limit = _live_chart_display_limit(timeframe, ticker)
        live_window_bars = max(display_limit, min(5000, requested_bars))
        calc_df = df.iloc[-live_window_bars:]

    from swing_strategy import detect_swing_entry, _normalize_candle
    
    # Calculate a safe window size for history to prevent O(N^2) slowdown on large data.
    # 24h market at 1Min = 1440 bars/day. We add a buffer of 500.
    max_history_len = int(lookback_days * 1500) + 500
    
    calc_records = calc_df.to_dict(orient="records")
    calc_times = calc_df.index
    if is_backtest_context:
        swing_evaluation_start = 0
    else:
        try:
            output_limit = int((settings or {}).get("_chart_display_bars", 0) or 0)
        except (TypeError, ValueError):
            output_limit = 0
        output_limit = output_limit or _live_chart_display_limit(timeframe, ticker)
        # Build warmup history for the selected lookback, but only calculate
        # Swing levels for candles that can reach the live chart payload.
        swing_evaluation_start = max(0, len(calc_records) - output_limit)

    for i, row in enumerate(calc_records):
        idx = calc_times[i]
        candle = _df_row_to_swing_candle(idx, row)
        candle = _normalize_candle(candle)
        if not candle or not all(np.isfinite(candle.get(k, np.nan)) for k in ("open", "high", "low", "close")):
            continue
        history.append(candle)
        # Trim history if it exceeds our safe sliding window
        if len(history) > max_history_len * 2:
            history = history[-max_history_len:]

        if i < swing_evaluation_start:
            continue
            
        decision = detect_swing_entry(
            history[-max_history_len:],
            current_price=candle["close"],
            entry_mode=entry_mode,
            lookback_days=lookback_days,
            market_session=market_session,
            is_crypto=is_crypto,
            pivot_strength=pivot_strength,
        )
        ctx = decision.get("context") or {}
        current_price = candle["close"]

        def _level_price(level):
            try:
                return float(level.get("price", np.nan))
            except Exception:
                return np.nan

        supports = sorted(
            [
                lvl for lvl in (ctx.get("support_levels") or [])
                if np.isfinite(_level_price(lvl)) and _level_price(lvl) <= current_price
            ],
            key=lambda lvl: (abs(_level_price(lvl) - current_price), -float(lvl.get("score", 0.0))),
        )
        resistances = sorted(
            [
                lvl for lvl in (ctx.get("resistance_levels") or [])
                if np.isfinite(_level_price(lvl)) and _level_price(lvl) >= current_price
            ],
            key=lambda lvl: (abs(_level_price(lvl) - current_price), -float(lvl.get("score", 0.0))),
        )
        for pos, level in enumerate(supports[:level_count]):
            try:
                df.at[idx, support_cols[pos]] = float(level.get("price"))
            except Exception:
                pass
        for pos, level in enumerate(resistances[:level_count]):
            try:
                df.at[idx, resistance_cols[pos]] = float(level.get("price"))
            except Exception:
                pass
        action = decision.get("action", "HOLD")
        if action == "BUY":
            df.at[idx, "Swing_Bullish"] = True
        elif action == "SELL":
            df.at[idx, "Swing_Bearish"] = True

# Alpaca clients (Dynamic access)
# ---------------------------------------------------------------------------
_client_cache = {}

def get_alpaca_clients():
    """Returns initialized Alpaca clients for the active user's credentials (cached per key pair)."""
    global _client_cache
    usr_cfg = get_user_config()
    api_key = getattr(usr_cfg, 'ALPACA_API_KEY', '').strip()
    secret_key = getattr(usr_cfg, 'ALPACA_SECRET_KEY', '').strip()
    
    if not api_key or api_key.startswith("your_alpaca"):
        return None, None
        
    cache_key = (api_key, secret_key)
    if cache_key not in _client_cache:
        print("[indicators] Initializing Alpaca clients")
        s_client = StockHistoricalDataClient(api_key, secret_key)
        c_client = CryptoHistoricalDataClient(api_key, secret_key)
        _client_cache[cache_key] = (s_client, c_client)
        
    return _client_cache[cache_key]

_data_cache = {}

def _coerce_param_value(value, fallback=None):
    try:
        if fallback is not None:
            return type(fallback)(value)
        return value
    except Exception:
        return value


def _bot_swing_param_alias(key, settings=None):
    """Map bot Swing entry/exit strategy settings onto the visual Swing indicator."""
    if not isinstance(settings, dict):
        return None

    swing_modes = {"swing_points", "hybrid_swing"}
    buy_mode = str(settings.get("buy_mode") or "").strip().lower()
    sell_mode = str(settings.get("sell_mode") or settings.get("options_exit_strategy") or "").strip().lower()

    if key == "SWING_POINTS_ENTRY_MODE" and buy_mode in swing_modes:
        return settings.get("swing_entry_mode") or settings.get("SWING_POINTS_ENTRY_MODE")

    if key == "SWING_POINTS_LOOKBACK_DAYS":
        if buy_mode in swing_modes:
            return (
                settings.get("swing_entry_lookback_days")
                or settings.get("swing_lookback_days")
                or settings.get("SWING_POINTS_LOOKBACK_DAYS")
            )
        if sell_mode in swing_modes:
            return (
                settings.get("swing_exit_lookback_days")
                or settings.get("swing_lookback_days")
                or settings.get("SWING_POINTS_LOOKBACK_DAYS")
            )

    return None


def _bot_swing_settings_source(settings=None):
    if not isinstance(settings, dict):
        return "indicator_settings"
    swing_modes = {"swing_points", "hybrid_swing"}
    buy_mode = str(settings.get("buy_mode") or "").strip().lower()
    sell_mode = str(settings.get("sell_mode") or settings.get("options_exit_strategy") or "").strip().lower()
    if buy_mode in swing_modes:
        return "bot_entry_strategy"
    if sell_mode in swing_modes:
        return "bot_exit_strategy"
    return "indicator_settings"


def _resolve_param(key, fallback=None, settings=None):
    if settings and isinstance(settings, dict):
        alias_val = _bot_swing_param_alias(key, settings)
        if alias_val is not None and alias_val != "":
            return _coerce_param_value(alias_val, fallback)

        candidates = [
            settings,
            settings.get("indicator_overrides") if isinstance(settings.get("indicator_overrides"), dict) else {},
            settings.get("indicator_parameters") if isinstance(settings.get("indicator_parameters"), dict) else {},
        ]
        for source in candidates:
            val = source.get(key)
            if val is not None and val != "":
                return _coerce_param_value(val, fallback)
    return getattr(get_user_config(), key, fallback)


def _add_trend_rider_columns(df, close, high, low, period, param):
    """Regular "Trend Rider" indicator: an ATR trailing line that flips with the trend, a smoothed trend tracer,
    ±2σ reversal zones and a 0-4 confluence score. Uses only completed history (recursive, no lookahead) and its own
    ATR / RSI(14) so it does not depend on any other indicator's settings."""
    atr_values = _wilder_atr(high, low, close, period("TREND_RIDER_ATR_PERIOD", 14)).to_numpy(dtype=float)
    multiplier = float(param("TREND_RIDER_ATR_MULT", 2.0))
    prices = close.to_numpy(dtype=float)
    count = len(prices)
    trail = np.full(count, np.nan)
    trend_up = np.ones(count, dtype=bool)
    valid = np.flatnonzero(~np.isnan(atr_values))
    if valid.size:
        start = int(valid[0])
        trail[start] = prices[start] - atr_values[start] * multiplier
        for i in range(start + 1, count):
            loss = atr_values[i] * multiplier if not np.isnan(atr_values[i]) else 0.0
            if prices[i] > trail[i - 1] and prices[i - 1] > trail[i - 1]:
                trail[i], trend_up[i] = max(trail[i - 1], prices[i] - loss), True
            elif prices[i] < trail[i - 1] and prices[i - 1] < trail[i - 1]:
                trail[i], trend_up[i] = min(trail[i - 1], prices[i] + loss), False
            elif prices[i] > trail[i - 1]:
                trail[i], trend_up[i] = prices[i] - loss, True
            else:
                trail[i], trend_up[i] = prices[i] + loss, False
    tracer_period = period("TREND_RIDER_TRACER_PERIOD", 50)
    tracer = 2 * ta_lib.trend.ema_indicator(close, window=max(tracer_period // 2, 2)) - ta_lib.trend.ema_indicator(close, window=tracer_period)
    zone_period = period("TREND_RIDER_ZONE_PERIOD", 50)
    zone_mean = close.rolling(window=zone_period).mean()
    zone_std = close.rolling(window=zone_period).std()
    zone_k = max(0.5, float(param("TREND_RIDER_ZONE_STD", 2.0)))  # sample sigma (ddof=1), like pandas/Pine stdev-sample
    zone_upper, zone_lower = zone_mean + zone_k * zone_std, zone_mean - zone_k * zone_std
    rsi = _wilder_rsi(close, period("TREND_RIDER_RSI_PERIOD", 14))
    rsi_low, rsi_high = float(param("TREND_RIDER_RSI_LOW", 40)), float(param("TREND_RIDER_RSI_HIGH", 70))
    score = (
        trend_up.astype(int)
        + ((close > tracer) & tracer.notna()).astype(int).to_numpy()
        + ((close < zone_upper) & zone_upper.notna()).astype(int).to_numpy()
        + ((rsi > rsi_low) & (rsi < rsi_high)).astype(int).to_numpy()
    )
    df["TR_Trail"] = trail
    df["TR_Trend"] = trend_up
    df["TR_Tracer"] = tracer
    df["TR_Zone_Upper"] = zone_upper
    df["TR_Zone_Lower"] = zone_lower
    df["TR_Score"] = score


def _add_ha_ut_columns(df, close, high, low, period, param):
    """Regular "HA+UT" indicator: a UT-style ATR trailing stop computed on Heikin-Ashi candles. The trail only uses
    completed history; the vote is the side of the trail the Heikin-Ashi close is on."""
    multiplier = float(param("HA_UT_ATR_MULT", 1.0))
    atr_period = period("HA_UT_ATR_PERIOD", 10)
    open_ = df["Open"].to_numpy(dtype=float)
    highs, lows, closes = high.to_numpy(dtype=float), low.to_numpy(dtype=float), close.to_numpy(dtype=float)
    count = len(df)
    ha_close = (open_ + highs + lows + closes) / 4
    ha_open = np.empty(count)
    ha_open[0] = (open_[0] + closes[0]) / 2
    for i in range(1, count):
        ha_open[i] = (ha_open[i - 1] + ha_close[i - 1]) / 2
    ha_high = np.maximum(highs, np.maximum(ha_open, ha_close))
    ha_low = np.minimum(lows, np.minimum(ha_open, ha_close))
    # UT Bot convention: ATR comes from the real candles, only the source price is Heikin-Ashi.
    atr_period = max(2, int(atr_period))
    atr = _wilder_atr(high, low, close, atr_period).to_numpy(dtype=float)
    loss = atr * multiplier
    trail = np.full(count, np.nan)
    above = np.ones(count, dtype=bool)
    valid = np.flatnonzero(~np.isnan(loss))
    start = int(valid[0]) if len(valid) else 0
    if len(valid):
        trail[start] = ha_close[start] - loss[start]
        for i in range(start + 1, count):
            step = loss[i] if not np.isnan(loss[i]) else 0.0
            if ha_close[i] > trail[i - 1] and ha_close[i - 1] > trail[i - 1]:
                trail[i], above[i] = max(trail[i - 1], ha_close[i] - step), True
            elif ha_close[i] < trail[i - 1] and ha_close[i - 1] < trail[i - 1]:
                trail[i], above[i] = min(trail[i - 1], ha_close[i] + step), False
            elif ha_close[i] > trail[i - 1]:
                trail[i], above[i] = ha_close[i] - step, True
            else:
                trail[i], above[i] = ha_close[i] + step, False
    df["HAUT_Close"] = ha_close
    df["HAUT_Trail"] = trail
    df["HAUT_Above"] = above


def _add_chart_pattern_columns(df, active):
    """The regular "Chart Patterns" indicator: a bullish/bearish flag and a pattern name per completed candle.

    It reads the confirmed-breakout signal of the causal pattern engine (flags, pennants, wedges), so nothing from a
    forming candle ever votes. Event columns, never forward-filled: a pattern shows on the candle that confirmed it."""
    if not active or "BB5_Signal" not in df.columns:
        df["CP_Bullish"] = False
        df["CP_Bearish"] = False
        df["CP_Name"] = "Neutral"
        df["CP_Quality"] = 0.0
        return df
    signal = pd.to_numeric(df["BB5_Signal"], errors="coerce").fillna(0)
    pattern = df["BB5_Signal_Pattern"] if "BB5_Signal_Pattern" in df.columns else df.get("BB5_Pattern", "NONE")
    labels = pd.Series(pattern, index=df.index).astype(str).str.replace("_", " ", regex=False).str.title()
    df["CP_Bullish"] = signal > 0
    df["CP_Bearish"] = signal < 0
    df["CP_Name"] = np.where(signal != 0, labels, "Neutral")
    df["CP_Quality"] = pd.to_numeric(df.get("BB5_Signal_Confidence", 0.0), errors="coerce").fillna(0.0)
    return df


def _add_candlestick_columns(df, reliable_only=False):
    """Candle-pattern columns shared by the fast and full indicator paths.

    Patterns follow the usual TA-Lib / Bulkowski definitions: candle sizes are measured against the average body and
    range of the previous 10 candles (never the current one), reversal patterns need a preceding trend, and Morning /
    Evening Star need a long first and third candle. A bar where bullish and bearish patterns both fire is neutral.
    ``reliable_only`` keeps only the patterns Bulkowski rates as reliable reversals (engulfing, hammer, shooting star,
    piercing/dark cloud, stars, three soldiers/crows, three inside/outside) and drops harami, tweezer, hanging man,
    inverted hammer, marubozu and the doji variants from the vote. Spinning tops are named but never vote."""
    o, h, l, c = df["Open"], df["High"], df["Low"], df["Close"]
    body = (c - o).abs()
    rng = h - l
    top = pd.concat([o, c], axis=1).max(axis=1)
    bottom = pd.concat([o, c], axis=1).min(axis=1)
    lower_wick = bottom - l
    upper_wick = h - top
    avg_body = body.rolling(10, min_periods=10).mean().shift(1)
    avg_range = rng.rolling(10, min_periods=10).mean().shift(1)
    sma10 = c.rolling(10, min_periods=10).mean()

    def at(series, k):
        return series.shift(k, fill_value=False) if series.dtype == bool else series.shift(k)

    bull = c > o
    bear = c < o
    long_body = body > avg_body
    short_body = body < avg_body

    def long_at(k):
        return at(long_body, k)

    def short_at(k):
        return at(short_body, k)

    def down_before(k):
        return ((c.shift(k) < sma10.shift(k)) & (c.shift(k) < c.shift(k + 5))).fillna(False)

    def up_before(k):
        return ((c.shift(k) > sma10.shift(k)) & (c.shift(k) > c.shift(k + 5))).fillna(False)

    known = avg_body.notna() & avg_range.notna() & (rng > 0)
    doji = known & (body <= avg_range * 0.1)
    bull_marubozu = known & long_body & bull & (body >= rng * 0.9)
    bear_marubozu = known & long_body & bear & (body >= rng * 0.9)

    hammer_shape = known & short_body & (lower_wick >= body * 2) & (lower_wick >= rng * 0.5) & (upper_wick <= rng * 0.1)
    shooting_shape = known & short_body & (upper_wick >= body * 2) & (upper_wick >= rng * 0.5) & (lower_wick <= rng * 0.1)
    hammer = hammer_shape & down_before(1)
    hanging_man = hammer_shape & up_before(1)
    shooting_star = shooting_shape & up_before(1)
    inverted_hammer = shooting_shape & down_before(1)

    prior_bear, prior_bull = at(bear, 1), at(bull, 1)
    prior_long = long_at(1)
    bull_eng = known & prior_bear & bull & long_body & (c >= o.shift(1)) & (o <= c.shift(1)) & (body > body.shift(1)) & down_before(2)
    bear_eng = known & prior_bull & bear & long_body & (c <= o.shift(1)) & (o >= c.shift(1)) & (body > body.shift(1)) & up_before(2)
    bull_harami = known & prior_bear & prior_long & bull & (o > c.shift(1)) & (c < o.shift(1)) & (body < body.shift(1)) & down_before(2)
    bear_harami = known & prior_bull & prior_long & bear & (o < c.shift(1)) & (c > o.shift(1)) & (body < body.shift(1)) & up_before(2)
    piercing_line = known & prior_bear & prior_long & bull & (o < l.shift(1)) & (c > (o.shift(1) + c.shift(1)) / 2) & (c < o.shift(1)) & down_before(2)
    dark_cloud = known & prior_bull & prior_long & bear & (o > h.shift(1)) & (c < (o.shift(1) + c.shift(1)) / 2) & (c > o.shift(1)) & up_before(2)

    first_mid = (o.shift(2) + c.shift(2)) / 2
    morning_star = known & at(bear, 2) & long_at(2) & short_at(1) & bull & long_body & (c > first_mid) & down_before(3)
    evening_star = known & at(bull, 2) & long_at(2) & short_at(1) & bear & long_body & (c < first_mid) & up_before(3)

    three_white_soldiers = (
        known & bull & at(bull, 1) & at(bull, 2) & long_body & long_at(1) & long_at(2)
        & (c > c.shift(1)) & (c.shift(1) > c.shift(2))
        & (o > o.shift(1)) & (o < c.shift(1)) & (o.shift(1) > o.shift(2)) & (o.shift(1) < c.shift(2))
        & (upper_wick < body * 0.2) & ((h.shift(1) - top.shift(1)) < body.shift(1) * 0.2)
    )
    three_black_crows = (
        known & bear & at(bear, 1) & at(bear, 2) & long_body & long_at(1) & long_at(2)
        & (c < c.shift(1)) & (c.shift(1) < c.shift(2))
        & (o < o.shift(1)) & (o > c.shift(1)) & (o.shift(1) < o.shift(2)) & (o.shift(1) > c.shift(2))
        & (lower_wick < body * 0.2) & ((bottom.shift(1) - l.shift(1)) < body.shift(1) * 0.2)
    )

    tweezer_bot = known & ((l - l.shift(1)).abs() <= rng * 0.05) & prior_bear & bull & down_before(2)
    tweezer_top = known & ((h - h.shift(1)).abs() <= rng * 0.05) & prior_bull & bear & up_before(2)
    three_inside_up = at(bull_harami, 1) & bull & (c > o.shift(2))
    three_inside_down = at(bear_harami, 1) & bear & (c < o.shift(2))
    three_outside_up = at(bull_eng, 1) & bull & (c > c.shift(1))
    three_outside_down = at(bear_eng, 1) & bear & (c < c.shift(1))
    dragonfly_doji = doji & (lower_wick >= rng * 0.6) & (upper_wick <= rng * 0.1) & down_before(1)
    gravestone_doji = doji & (upper_wick >= rng * 0.6) & (lower_wick <= rng * 0.1) & up_before(1)

    spinning_top = known & ~doji & short_body & (upper_wick >= body) & (lower_wick >= body) & (body < rng * 0.4)
    reliable_bull = bull_eng | hammer | morning_star | piercing_line | three_white_soldiers | three_inside_up | three_outside_up
    reliable_bear = bear_eng | shooting_star | evening_star | dark_cloud | three_black_crows | three_inside_down | three_outside_down
    if reliable_only:
        bullish, bearish = reliable_bull, reliable_bear
    else:
        bullish = reliable_bull | inverted_hammer | bull_marubozu | dragonfly_doji | bull_harami | tweezer_bot
        bearish = reliable_bear | hanging_man | bear_marubozu | gravestone_doji | bear_harami | tweezer_top
    conflict = bullish & bearish
    df["cdl_bullish"] = bullish & ~conflict
    df["cdl_bearish"] = bearish & ~conflict
    df["cdl_name"] = "Neutral"
    for mask, label in (
        (spinning_top, "Spinning Top"), (doji, "Doji"), (bull_marubozu, "Bull Marubozu"), (bear_marubozu, "Bear Marubozu"), (hammer, "Hammer"),
        (inverted_hammer, "Inverted Hammer"), (shooting_star, "Shooting Star"), (hanging_man, "Hanging Man"),
        (dragonfly_doji, "Dragonfly Doji"), (gravestone_doji, "Gravestone Doji"), (bull_harami, "Bull Harami"),
        (bear_harami, "Bear Harami"), (tweezer_bot, "Tweezer Bottom"), (tweezer_top, "Tweezer Top"),
        (bull_eng, "Bull Engulfing"), (bear_eng, "Bear Engulfing"), (piercing_line, "Piercing Line"),
        (dark_cloud, "Dark Cloud Cover"), (morning_star, "Morning Star"), (evening_star, "Evening Star"),
        (three_white_soldiers, "3 White Soldiers"), (three_black_crows, "3 Black Crows"),
        (three_inside_up, "3 Inside Up"), (three_inside_down, "3 Inside Down"),
        (three_outside_up, "3 Outside Up"), (three_outside_down, "3 Outside Down"),
    ):
        df.loc[mask, "cdl_name"] = label
    df.loc[conflict, "cdl_name"] = "Mixed signals"


_add_fast_candlestick_columns = _add_candlestick_columns


def get_full_analysis(ticker, timeframe="5Min", data_source="alpaca", settings=None):
    """
    Fetch live data from Alpaca or Webull, compute indicators, and return a result dict.
    Supports both Stocks and Crypto.
    """

    import time
    start_t = time.perf_counter()
    provider_ms = 0.0  # time spent inside the market-data provider call(s); the rest of fetch_time is local preparation
    try:
        df = None
        required_indicator_history_bars = _required_indicator_history_bars(settings)

        # --- Mode 1: Webull Data (High Fidelity for Dashboard) ---
        if data_source == "webull":
            tf_map = {
                "30Sec": "s30", "1Min": "m1", "2Min": "m2", "3Min": "m3",
                "5Min": "m5", "10Min": "m10", "15Min": "m15", "30Min": "m30",
                "1Hour": "m60", "2Hour": "m120", "4Hour": "m240", "1Day": "D"
            }
            wb_tf = tf_map.get(timeframe, "m5")
            live_count_map = {
                "30Sec": 1200,
                "1Min": 2500,
                "2Min": 1200,
                "3Min": 1200,
                "5Min": 1200,
                "10Min": 1200,
                "15Min": 1200,
                "30Min": 1200,
            }
            requested_count = live_count_map.get(timeframe, 1200)
            if required_indicator_history_bars:
                requested_count = max(requested_count, required_indicator_history_bars)
            requested_session = str((settings or {}).get("market_session", "extended") or "extended").lower()
            is_crypto_request = _is_crypto_ticker(ticker)
            provider_sessions = "RTH" if not is_crypto_request and requested_session == "regular" else "RTH,PRE,ATH"
            # A regular-session M1 request needs fewer raw candles because the
            # provider omits PRE/AFTER bars. Extended M1 keeps 2,500 so today,
            # the complete prior session, and warm-up bars are all available.
            if timeframe == "1Min" and provider_sessions == "RTH":
                requested_count = 1200 if is_crypto_request else 1650
            # Apply the preview cap after the regular-session adjustment above;
            # otherwise the 1-minute regular-session branch silently expands
            # the request back to 1,650 bars.
            if isinstance(settings, dict) and settings.get("_chart_preview"):
                try:
                    preview_bars = int(settings.get("_chart_display_bars", 800) or 800)
                except (TypeError, ValueError):
                    preview_bars = 800
                requested_count = min(requested_count, max(300, min(preview_bars, 800)))
            preview_cache_key = None
            preview_fetch_lock = None
            if isinstance(settings, dict) and settings.get("_chart_preview"):
                preview_cache_key = (
                    str(ticker or "").upper(),
                    str(timeframe or "").upper(),
                    provider_sessions,
                    int(requested_count),
                )
                with _preview_bars_cache_guard:
                    cached_preview_bars = _preview_bars_cache.get(preview_cache_key)
                    if cached_preview_bars and (time.monotonic() - cached_preview_bars[0]) < _preview_bars_cache_ttl(timeframe):
                        bars = cached_preview_bars[1]
                    else:
                        bars = None
                    preview_fetch_lock = _preview_bars_fetch_locks.setdefault(
                        preview_cache_key,
                        threading.Lock(),
                    )
                if bars is None:
                    # Serialize only the preview fetch for this exact market
                    # window. A second settings edit waits for the first fetch,
                    # then reuses its result instead of opening another Webull
                    # request in parallel.
                    with preview_fetch_lock:
                        with _preview_bars_cache_guard:
                            cached_preview_bars = _preview_bars_cache.get(preview_cache_key)
                            if cached_preview_bars and (time.monotonic() - cached_preview_bars[0]) < _preview_bars_cache_ttl(timeframe):
                                bars = cached_preview_bars[1]
                        if bars is None:
                            _provider_started = time.perf_counter()
                            bars = _get_wb_data_provider().get_bars(
                                ticker,
                                timespan=wb_tf,
                                count=requested_count,
                                trading_sessions=provider_sessions,
                            )
                            provider_ms += (time.perf_counter() - _provider_started) * 1000
                            if bars:
                                with _preview_bars_cache_guard:
                                    _preview_bars_cache[preview_cache_key] = (time.monotonic(), bars)
            else:
                _provider_started = time.perf_counter()
                bars = _get_wb_data_provider().get_bars(
                    ticker,
                    timespan=wb_tf,
                    count=requested_count,
                    trading_sessions=provider_sessions,
                )
                provider_ms += (time.perf_counter() - _provider_started) * 1000
            # Normalize the crypto envelope before checking individual bars.
            if isinstance(bars, list) and bars and isinstance(bars[0], dict) and "result" in bars[0]:
                bars = bars[0].get("result") or []
            if bars:
                if isinstance(bars, list) and len(bars) > 0:
                    # Check for dummy bar with None time
                    if isinstance(bars[0], dict) and bars[0].get('time') is None:
                        print(f"[indicators] Webull returned dummy bar for {ticker}. Falling back to Alpaca.")
                        bars = None
                
            if bars: # Check again after potential fallback
                # Handle crypto response structure: [{'symbol': '...', 'result': [...]}]
                if isinstance(bars, list) and len(bars) > 0 and isinstance(bars[0], dict) and 'result' in bars[0]:
                    bars = bars[0]['result']
                    print(f"[indicators] Extracted result list. New len: {len(bars)}")
                
                df = pd.DataFrame(bars)
                
                # Handle epoch timestamps delivered as numbers or strings,
                # plus providers that return ISO timestamps.
                df['timestamp'] = _provider_time_to_datetime(df['time'])
                    
                df.set_index('timestamp', inplace=True)
                df.columns = [c.capitalize() for c in df.columns]
                df = df.sort_index() # Ensure chronological order for indicators
                print(f"[indicators] Webull bars fetched for {ticker}: {len(df)} rows. First time: {df.index[0]}, Last time: {df.index[-1]}")
                # Shared validation runs after session/completed-candle
                # filtering for both Webull and Alpaca below.
            else:
                print(f"[indicators] Webull data failed for {ticker}. Falling back to Alpaca.")
                data_source = "alpaca"

        # --- Mode 2: Alpaca Data (Execution Feed) ---
        if data_source == "alpaca":
            # Initialize clients
            stock_client, crypto_client = get_alpaca_clients()
            
            clean_ticker = ticker.upper().replace("/", "")
            is_crypto = any(clean_ticker.endswith(base) for base in ["USD", "USDT", "USDC"])
            
            request_ticker = ticker
            if is_crypto:
                for base in ["USDT", "USDC", "USD"]:
                    if clean_ticker.endswith(base):
                        request_ticker = clean_ticker.replace(base, f"/{base}")
                        break

            if is_crypto:
                client = crypto_client
                request_class = CryptoBarsRequest
            else:
                client = stock_client
                request_class = StockBarsRequest

            if not client:
                print(f"[indicators] ERROR: Alpaca client not initialized.")
                return None

            end_date = datetime.now()
            usr_cfg = get_user_config()
            api_key = getattr(usr_cfg, 'ALPACA_API_KEY', '').strip()
            # Scope the in-memory bar cache per account. The API key ID identifies the account (one secret per key ID),
            # so the secret itself is never part of the key.
            credential_scope = api_key
            cache_key = (credential_scope, ticker.upper(), timeframe.upper())

            # Timeframe -> Alpaca object + lookback
            tf_str = timeframe.upper()
            if tf_str in ["30SEC", "1MIN"]:
                tf_obj = TimeFrame.Minute
                lookback_days = 2
            elif tf_str == "2MIN":
                tf_obj = TimeFrame(2, TimeFrameUnit.Minute)
                lookback_days = 3
            elif tf_str == "3MIN":
                tf_obj = TimeFrame(3, TimeFrameUnit.Minute)
                lookback_days = 3
            elif tf_str == "5MIN":
                tf_obj = TimeFrame(5, TimeFrameUnit.Minute)
                lookback_days = 10
            elif tf_str == "10MIN":
                tf_obj = TimeFrame(10, TimeFrameUnit.Minute)
                lookback_days = 15
            elif tf_str == "15MIN":
                tf_obj = TimeFrame(15, TimeFrameUnit.Minute)
                lookback_days = 20
            elif tf_str == "30MIN":
                tf_obj = TimeFrame(30, TimeFrameUnit.Minute)
                lookback_days = 35
            elif tf_str in ["1HOUR", "60MIN"]:
                tf_obj = TimeFrame.Hour
                lookback_days = 100
            elif tf_str == "2HOUR":
                tf_obj = TimeFrame(2, TimeFrameUnit.Hour)
                lookback_days = 200
            elif tf_str == "4HOUR":
                tf_obj = TimeFrame(4, TimeFrameUnit.Hour)
                lookback_days = 400
            elif tf_str in ["1D", "1DAY"]:
                tf_obj = TimeFrame.Day
                lookback_days = 3650
            else:
                tf_obj = TimeFrame(5, TimeFrameUnit.Minute)
                lookback_days = 5

            if required_indicator_history_bars:
                interval_minutes = {
                    "30SEC": 1, "1MIN": 1, "2MIN": 2, "3MIN": 3,
                    "5MIN": 5, "10MIN": 10, "15MIN": 15, "30MIN": 30,
                    "1HOUR": 60, "60MIN": 60, "2HOUR": 120, "4HOUR": 240,
                }.get(tf_str)
                if interval_minutes:
                    if is_crypto:
                        bars_per_day = max(1.0, 1440.0 / interval_minutes)
                        indicator_lookback_days = int(np.ceil(required_indicator_history_bars / bars_per_day)) + 1
                    else:
                        bars_per_trading_day = max(1.0, 390.0 / interval_minutes)
                        trading_days = required_indicator_history_bars / bars_per_trading_day
                        indicator_lookback_days = int(np.ceil(trading_days * 7.0 / 5.0)) + 3
                    lookback_days = max(lookback_days, indicator_lookback_days)

            # Incremental cache
            if (
                cache_key in _data_cache
                and (
                    not required_indicator_history_bars
                    or len(_data_cache[cache_key][1]) >= required_indicator_history_bars
                )
            ):
                last_bar_time, cached_df = _data_cache[cache_key]
                start_date = last_bar_time
            else:
                start_date = end_date - timedelta(days=lookback_days)
                cached_df = None

            request_params = request_class(
                symbol_or_symbols=request_ticker,
                timeframe=tf_obj,
                start=start_date,
                end=end_date,
            )
            
            if not is_crypto:
                request_params.feed = "iex"

            bars = client.get_stock_bars(request_params) if not is_crypto else client.get_crypto_bars(request_params)
            df = bars.df
            if df is not None and not df.empty:
                df = df[~df.index.duplicated(keep='last')] # Remove duplicates immediately
                if isinstance(df.index, pd.MultiIndex):
                    df = df.xs(request_ticker, level=0)
                
                if cached_df is not None:
                    df = pd.concat([cached_df, df])
                
                df = df[~df.index.duplicated(keep='last')]
                df = df.sort_index()
                _data_cache[cache_key] = (df.index[-1], df)

        if df is None or df.empty:
            return None

        # Put live bars on a split-adjusted basis (splits and reverse splits)
        # so a split never looks like a crash or a spike to any strategy.
        data_quality = "OK"
        if not _is_crypto_ticker(ticker):
            df, data_quality = _reconcile_live_splits(df, ticker)

        # --- Indicator Calculations ---
        # Standardise column names (Webull/Alpaca fallback)
        df.columns = [c.capitalize() for c in df.columns]
        close = df["Close"]
        high = df["High"]
        low = df["Low"]
        open_ = df["Open"]
        volume = df["Volume"]

        if len(df) < 30:
            return None

        calculation_settings = dict(settings or {})
        calculation_settings.setdefault("_chart_display_bars", _live_chart_display_limit(timeframe, ticker))
        calc_start = time.perf_counter()
        res = calculate_indicators_for_df(df, timeframe, ticker, settings=calculation_settings)
        if isinstance(res, dict):
            res["data_quality"] = data_quality
        end_t = time.perf_counter()
        print(f"[perf] {ticker} {timeframe} fetch_time: {(calc_start - start_t)*1000:.1f}ms (provider {provider_ms:.1f}ms) | calc_time: {(end_t - calc_start)*1000:.1f}ms")
        return res

    except Exception as exc:
        print(f"[indicators] Fetch error: {exc}")
        return None


# ---------------------------------------------------------------------------
# Core engine: calculate_indicators_for_df
# ---------------------------------------------------------------------------
class _SkipUnselectedIndicator(Exception):
    """Internal control flow used to bypass an unselected calculation block."""


def _add_standard_extension_columns(df, close, high, low, volume, param, enabled_indicators=None):
    """Add only the requested classical indicator columns.

    ``enabled_indicators=None`` preserves the legacy full-suite behavior for
    callers that explicitly need every indicator.  Live dashboard, bot preview,
    and manual-backtest requests pass a concrete set so enabling one extension
    does not calculate the other 35.
    """
    def period(key, fallback):
        return max(2, int(float(param(key, fallback))))

    selected = None if enabled_indicators is None else set(enabled_indicators)

    def wants(name):
        return selected is None or name in selected

    if wants("ATR Volatility"):
        atr_period = _atr_period(param("ATR_PERIOD", 14))
        atr_baseline_period = _atr_period(param("ATR_BASELINE_PERIOD", 20))
        atr_range_multiplier = _atr_range_multiplier(param("ATR_RANGE_MULTIPLIER", 1.0))
        atr = _wilder_atr(high, low, close, atr_period)
        df["ATR_Ext"] = atr
        df["ATR_Ext_Avg"] = atr.rolling(atr_baseline_period, min_periods=atr_baseline_period).mean()
        # These bands use prior completed values, avoiding look-ahead bias.
        df["ATR_Range_Upper"] = close.shift(1) + (atr.shift(1) * atr_range_multiplier)
        df["ATR_Range_Lower"] = close.shift(1) - (atr.shift(1) * atr_range_multiplier)

    if wants("Trend Rider"):
        _add_trend_rider_columns(df, close, high, low, period, param)

    if wants("HA+UT"):
        _add_ha_ut_columns(df, close, high, low, period, param)

    if wants("Stochastic"):
        stoch_window = period("STOCH_PERIOD", 14)
        stoch_range_low = low.rolling(stoch_window).min()
        stoch_range_high = high.rolling(stoch_window).max()
        stoch_raw = 100.0 * (close - stoch_range_low) / (stoch_range_high - stoch_range_low).replace(0, np.nan)
        df["Stoch_K"] = stoch_raw.rolling(period("STOCH_SMOOTH_K", 3)).mean()
        df["Stoch_D"] = df["Stoch_K"].rolling(period("STOCH_SMOOTH_D", 3)).mean()

    if wants("Stochastic RSI"):
        stoch_rsi = ta_lib.momentum.StochRSIIndicator(
            close,
            window=period("STOCH_RSI_PERIOD", 14),
            smooth1=period("STOCH_RSI_SMOOTH_K", 3),
            smooth2=period("STOCH_RSI_SMOOTH_D", 3),
        )
        df["Stoch_RSI_K"] = stoch_rsi.stochrsi_k() * 100.0
        df["Stoch_RSI_D"] = stoch_rsi.stochrsi_d() * 100.0

    if wants("CCI"):
        df["CCI_Ext"] = ta_lib.trend.CCIIndicator(high, low, close, window=period("CCI_PERIOD", 20)).cci()
    if wants("Williams %R"):
        df["Williams_R"] = ta_lib.momentum.WilliamsRIndicator(high, low, close, lbp=period("WILLIAMS_R_PERIOD", 14)).williams_r()
    if wants("Rate of Change"):
        df["ROC_Ext"] = ta_lib.momentum.ROCIndicator(close, window=period("ROC_PERIOD", 12)).roc()
    if wants("Momentum"):
        df["Momentum_Ext"] = close - close.shift(period("MOMENTUM_PERIOD", 10))
    if wants("Money Flow Index"):
        df["MFI_Ext"] = ta_lib.volume.MFIIndicator(high, low, close, volume, window=period("MFI_PERIOD", 14)).money_flow_index()
    if wants("Ultimate Oscillator"):
        df["Ultimate_Osc"] = ta_lib.momentum.UltimateOscillator(
            high, low, close,
            window1=period("UO_SHORT", 7), window2=period("UO_MEDIUM", 14), window3=period("UO_LONG", 28),
        ).ultimate_oscillator()
    if wants("Awesome Oscillator"):
        df["Awesome_Osc"] = ta_lib.momentum.AwesomeOscillatorIndicator(
            high, low, window1=period("AO_FAST", 5), window2=period("AO_SLOW", 34)
        ).awesome_oscillator()
    if wants("Chande Momentum Oscillator"):
        cmo_delta = close.diff()
        cmo_length = period("CMO_PERIOD", 14)
        cmo_gains = cmo_delta.clip(lower=0).rolling(cmo_length, min_periods=cmo_length).sum()
        cmo_losses = (-cmo_delta.clip(upper=0)).rolling(cmo_length, min_periods=cmo_length).sum()
        df["CMO"] = 100.0 * (cmo_gains - cmo_losses) / (cmo_gains + cmo_losses).replace(0, np.nan)
    if wants("True Strength Index"):
        tsi = ta_lib.momentum.TSIIndicator(
            close, window_slow=period("TSI_SLOW", 25), window_fast=period("TSI_FAST", 13)
        ).tsi()
        df["TSI_Ext"] = tsi
        df["TSI_Signal"] = tsi.ewm(span=period("TSI_SIGNAL", 13), adjust=False).mean()
    if wants("TRIX"):
        trix = ta_lib.trend.TRIXIndicator(close, window=period("TRIX_PERIOD", 15)).trix()
        df["TRIX_Ext"] = trix
        df["TRIX_Signal"] = trix.ewm(span=period("TRIX_SIGNAL", 9), adjust=False).mean()
    if wants("Know Sure Thing"):
        kst = ta_lib.trend.KSTIndicator(
            close,
            roc1=period("KST_ROC1", 10), roc2=period("KST_ROC2", 15),
            roc3=period("KST_ROC3", 20), roc4=period("KST_ROC4", 30),
            window1=period("KST_SMA1", 10), window2=period("KST_SMA2", 10),
            window3=period("KST_SMA3", 10), window4=period("KST_SMA4", 15),
            nsig=period("KST_SIGNAL", 9),
        )
        df["KST_Ext"] = kst.kst()
        df["KST_Signal"] = kst.kst_sig()
    if wants("Percentage Price Oscillator"):
        ppo_fast, ppo_slow = period("PPO_FAST", 12), period("PPO_SLOW", 26)
        if ppo_fast > ppo_slow:  # fast >= slow inverts the oscillator; keep the standard orientation
            ppo_fast, ppo_slow = ppo_slow, ppo_fast
        if ppo_fast == ppo_slow:
            ppo_slow = ppo_fast + 1
        ppo = ta_lib.momentum.PercentagePriceOscillator(
            close,
            window_slow=ppo_slow, window_fast=ppo_fast, window_sign=period("PPO_SIGNAL", 9),
        )
        df["PPO_Ext"] = ppo.ppo()
        df["PPO_Signal"] = ppo.ppo_signal()
        df["PPO_Hist"] = ppo.ppo_hist()
    if wants("Aroon"):
        aroon = ta_lib.trend.AroonIndicator(high, low, window=period("AROON_PERIOD", 25))
        df["Aroon_Up"] = aroon.aroon_up()
        df["Aroon_Down"] = aroon.aroon_down()
    if wants("Vortex Indicator"):
        vortex = ta_lib.trend.VortexIndicator(high, low, close, window=period("VORTEX_PERIOD", 14))
        df["Vortex_Pos"] = vortex.vortex_indicator_pos()
        df["Vortex_Neg"] = vortex.vortex_indicator_neg()
    if wants("Ichimoku Cloud"):
        ichimoku = ta_lib.trend.IchimokuIndicator(
            high, low,
            window1=period("ICHIMOKU_CONVERSION", 9), window2=period("ICHIMOKU_BASE", 26),
            window3=period("ICHIMOKU_SPAN_B", 52), visual=False,
        )
        df["Ichimoku_Conversion"] = ichimoku.ichimoku_conversion_line()
        df["Ichimoku_Base"] = ichimoku.ichimoku_base_line()
        df["Ichimoku_Span_A"] = ichimoku.ichimoku_a()
        df["Ichimoku_Span_B"] = ichimoku.ichimoku_b()
        # The cloud drawn at the current bar is the span calculated one displacement (the base period) earlier, and the
        # Chikou span compares today's close with the close one displacement ago. Signals use these, not the projected
        # (undisplaced) spans, so the vote matches what the chart shows.
        ichimoku_shift = period("ICHIMOKU_BASE", 26)
        df["Ichimoku_Cloud_A"] = df["Ichimoku_Span_A"].shift(ichimoku_shift)
        df["Ichimoku_Cloud_B"] = df["Ichimoku_Span_B"].shift(ichimoku_shift)
        df["Ichimoku_Chikou_Ref"] = close.shift(ichimoku_shift)
    if wants("Parabolic SAR"):
        df["PSAR"] = _wilder_psar(
            high, low, close, float(param("PSAR_STEP", 0.02)), float(param("PSAR_MAX_STEP", 0.2))
        )
    if wants("Donchian Channels"):
        donchian_window = period("DONCHIAN_PERIOD", 20)
        df["Donchian_Upper_Prior"] = high.rolling(donchian_window).max().shift(1)
        df["Donchian_Lower_Prior"] = low.rolling(donchian_window).min().shift(1)
    if wants("Keltner Channels"):
        keltner_window = period("KELTNER_PERIOD", 20)
        keltner_multiplier = float(param("KELTNER_MULTIPLIER", 2.0))
        keltner_mid = close.ewm(span=keltner_window, adjust=False).mean()
        keltner_atr = ta_lib.volatility.AverageTrueRange(
            high, low, close, window=period("KELTNER_ATR_PERIOD", 10)
        ).average_true_range()
        df["Keltner_Mid"] = keltner_mid
        df["Keltner_Upper"] = keltner_mid + (keltner_atr * keltner_multiplier)
        df["Keltner_Lower"] = keltner_mid - (keltner_atr * keltner_multiplier)
    if wants("Choppiness Index"):
        chop_window = period("CHOP_PERIOD", 14)
        prev_close = close.shift(1)
        true_range = pd.concat(
            [(high - low), (high - prev_close).abs(), (low - prev_close).abs()], axis=1
        ).max(axis=1)
        chop_range = high.rolling(chop_window).max() - low.rolling(chop_window).min()
        chop_ratio = true_range.rolling(chop_window).sum() / chop_range.replace(0, np.nan)
        df["Choppiness"] = 100.0 * np.log10(chop_ratio) / np.log10(chop_window)
        # Direction for the regime vote comes from the net move over the same window, so the vote never depends on
        # another indicator being selected.
        df["Chop_Direction_Ref"] = close.shift(chop_window)
    if wants("DEMA"):
        dema_window = period("DEMA_PERIOD", 21)
        dema_ema1 = close.ewm(span=dema_window, adjust=False).mean()
        df["DEMA"] = (2.0 * dema_ema1) - dema_ema1.ewm(span=dema_window, adjust=False).mean()
        df.iloc[:2 * (dema_window - 1), df.columns.get_loc("DEMA")] = np.nan  # TA-Lib lookback
    if wants("TEMA"):
        tema_window = period("TEMA_PERIOD", 21)
        tema_ema1 = close.ewm(span=tema_window, adjust=False).mean()
        tema_ema2 = tema_ema1.ewm(span=tema_window, adjust=False).mean()
        tema_ema3 = tema_ema2.ewm(span=tema_window, adjust=False).mean()
        df["TEMA"] = (3.0 * tema_ema1) - (3.0 * tema_ema2) + tema_ema3
        df.iloc[:3 * (tema_window - 1), df.columns.get_loc("TEMA")] = np.nan  # TA-Lib lookback

    def weighted_ma(series, window):
        weights = np.arange(1, window + 1, dtype=float)
        return series.rolling(window).apply(lambda values: np.dot(values, weights) / weights.sum(), raw=True)

    if wants("Hull Moving Average"):
        hma_window = period("HMA_PERIOD", 9)
        hma_half = weighted_ma(close, max(1, hma_window // 2))
        hma_full = weighted_ma(close, hma_window)
        df["HMA"] = weighted_ma((2.0 * hma_half) - hma_full, max(1, int(round(np.sqrt(hma_window)))))
    if wants("Weighted Moving Average"):
        df["WMA_Ext"] = weighted_ma(close, period("WMA_PERIOD", 20))
    if wants("VWMA"):
        vwma_window = period("VWMA_PERIOD", 20)
        df["VWMA_Ext"] = (
            (close * volume).rolling(vwma_window).sum()
            / volume.rolling(vwma_window).sum().replace(0, np.nan)
        )
    if wants("Arnaud Legoux Moving Average"):
        alma_window = period("ALMA_PERIOD", 9)
        alma_offset = float(param("ALMA_OFFSET", 0.85))
        alma_sigma = max(0.01, float(param("ALMA_SIGMA", 6.0)))
        alma_positions = np.arange(alma_window, dtype=float)
        alma_center = np.floor(alma_offset * (alma_window - 1))
        alma_scale = alma_window / alma_sigma
        alma_weights = np.exp(-((alma_positions - alma_center) ** 2) / (2.0 * alma_scale ** 2))
        alma_weights /= alma_weights.sum()
        df["ALMA"] = close.rolling(alma_window).apply(lambda values: np.dot(values, alma_weights), raw=True)
    if wants("Least Squares Moving Average"):
        lsma_window = period("LSMA_PERIOD", 25)
        lsma_offset = int(float(param("LSMA_OFFSET", 0)))
        lsma_x = np.arange(lsma_window, dtype=float)
        lsma_x_mean = lsma_x.mean()
        lsma_denominator = np.square(lsma_x - lsma_x_mean).sum()

        def regression_endpoint(values):
            values_mean = values.mean()
            slope = np.dot(lsma_x - lsma_x_mean, values - values_mean) / lsma_denominator
            return values_mean + slope * ((lsma_window - 1 + lsma_offset) - lsma_x_mean)

        df["LSMA"] = close.rolling(lsma_window).apply(regression_endpoint, raw=True)

    if wants("On-Balance Volume"):
        # Granville's OBV: add the volume on an up close, subtract it on a down close, add nothing when unchanged.
        obv = (np.sign(close.diff()).fillna(0.0) * volume).cumsum()
        df["OBV_Ext"] = obv
        df["OBV_Signal"] = obv.ewm(span=period("OBV_SIGNAL_PERIOD", 20), adjust=False).mean()
    if wants("Chaikin Money Flow"):
        df["CMF_Ext"] = ta_lib.volume.ChaikinMoneyFlowIndicator(
            high, low, close, volume, window=period("CMF_PERIOD", 20)
        ).chaikin_money_flow()
    if wants("Accumulation/Distribution") or wants("Chaikin Oscillator"):
        adl = ta_lib.volume.AccDistIndexIndicator(high, low, close, volume).acc_dist_index()
        if wants("Accumulation/Distribution"):
            df["ADL_Ext"] = adl
            df["ADL_Signal"] = adl.ewm(span=period("ADL_SIGNAL_PERIOD", 20), adjust=False).mean()
        if wants("Chaikin Oscillator"):
            df["Chaikin_Osc"] = (
                adl.ewm(span=period("CHAIKIN_FAST", 3), adjust=False).mean()
                - adl.ewm(span=period("CHAIKIN_SLOW", 10), adjust=False).mean()
            )
    if wants("Price Volume Trend"):
        pvt = ta_lib.volume.VolumePriceTrendIndicator(close, volume).volume_price_trend()
        df["PVT_Ext"] = pvt
        df["PVT_Signal"] = pvt.ewm(span=period("PVT_SIGNAL_PERIOD", 20), adjust=False).mean()
    if wants("Ease of Movement"):
        df["EOM_Ext"] = ta_lib.volume.EaseOfMovementIndicator(
            high, low, volume, window=period("EOM_PERIOD", 14)
        ).sma_ease_of_movement()
    if wants("Force Index"):
        df["Force_Index"] = ta_lib.volume.ForceIndexIndicator(
            close, volume, window=period("FORCE_INDEX_PERIOD", 13)
        ).force_index()
    return df


def calculate_indicators_for_df(df, timeframe="5Min", ticker="UNKNOWN", settings=None):
    """
    Compute the requested indicators on *df* and return a result dict.
    Designed to work identically for live analysis and backtesting.
    """
    def _param(key, fallback=None):
        return _resolve_param(key, fallback, settings=settings)

    try:
        if df is None or df.empty:
            return None
        # Normalize ordering without mutating a caller's provider/cache frame.
        # A provider's last observation wins for duplicate timestamps.
        df = df.loc[~df.index.duplicated(keep="last")].sort_index().copy()
        df = _filter_market_session_df(df, ticker, timeframe, settings=settings)
        eval_context = active_evaluation_context.get()
        if eval_context in ("dashboard", "bots", "screener"):
            df = _completed_candles_for_live_chart(df, timeframe, ticker=ticker)
        if df is None or df.empty or len(df) < 30:
            return None
        enabled_indicators = None
        if isinstance(settings, dict) and isinstance(settings.get("enabled_indicators"), (list, tuple, set)):
            enabled_indicators = {str(v) for v in settings.get("enabled_indicators") if v}

        def _needs(*indicator_names):
            return enabled_indicators is None or bool(enabled_indicators.intersection(indicator_names))

        # Public RSI is available only when RSI itself is selected. Other
        # features may request it as a private calculation dependency without
        # turning it into a visible value or confluence vote.
        rsi_is_public = enabled_indicators is None or "RSI" in enabled_indicators
        rsi_is_required = (
            rsi_is_public
            or bool((settings or {}).get("_require_rsi_dependency", False))
        )

        needs_extension_indicators = (
            enabled_indicators is None
            or bool(enabled_indicators & set(EXTENSION_INDICATOR_TOGGLE_KEYS))
        )
        buy_mode = str((settings or {}).get("buy_mode", "") if isinstance(settings, dict) else "").lower()
        sell_mode = str((settings or {}).get("sell_mode", "") if isinstance(settings, dict) else "").lower()
        # Trend Rider privately depends on both EMA lines even when EMA is not
        # selected as a visible confluence vote.
        ema_is_required = _needs("EMA Cross") or sell_mode == "trend_rider"
        # Trend Rider uses the primary SMA as an exit dependency even when the
        # user does not also select SMA as a confluence indicator.
        sma_is_required = _needs("SMA") or sell_mode == "trend_rider"
        selected_chart_keys = _selected_chart_field_keys(enabled_indicators, buy_mode, sell_mode)
        structural_modes = {"pivot_points", "hybrid_pivot", "high_low", "hybrid_high_low", "swing_points", "hybrid_swing"}
        needs_prior_session = (
            enabled_indicators is None
            or bool(enabled_indicators & {"Prior Day High/Low", "Pivot Points (S/R)", "Volume Profile"})
            or buy_mode in structural_modes
            or sell_mode in structural_modes
        )
        needs_swing_points = (
            enabled_indicators is None
            or "Swing Points" in enabled_indicators
            or buy_mode in {"swing_points", "hybrid_swing"}
            or sell_mode in {"swing_points", "hybrid_swing"}
        )
        volume_profile_payload = {
            "enabled": False,
            "source": "prior_session",
            "bins": [],
        }

        # --- Column normalisation ------------------------------------------
        for col in ("Open", "High", "Low", "Close", "Volume"):
            lc = col.lower()
            if lc in df.columns and col not in df.columns:
                df[col] = df[lc]

        df = df.loc[:, ~df.columns.duplicated()]
        for col in ("Open", "High", "Low", "Close", "Volume"):
            df[col] = pd.to_numeric(df[col].squeeze(), errors="coerce")

        if eval_context in ("dashboard", "bots", "screener"):
            quality_error = _validate_live_candle_data(
                df, ticker, timeframe,
                required_bars=max(30, _required_indicator_history_bars(settings)),
            )
            if quality_error:
                print(f"[indicators] Data-quality gate blocked {ticker}: {quality_error}")
                return None

        close = df["Close"]
        high = df["High"]
        low = df["Low"]
        volume = df["Volume"]

        def _init_structural_columns():
            df["Daily_HighLow_High"] = np.nan
            df["Daily_HighLow_Low"] = np.nan
            df["Daily_HighLow_High_Trigger"] = np.nan
            df["Daily_HighLow_Low_Trigger"] = np.nan
            df["Daily_HighLow_Bullish"] = False
            df["Daily_HighLow_Bearish"] = False
            df["Volume_Profile_POC"] = np.nan
            df["Volume_Profile_VAH"] = np.nan
            df["Volume_Profile_VAL"] = np.nan
            df["Volume_Profile_Bullish"] = False
            df["Volume_Profile_Bearish"] = False
            for pivot_col in (
                "Pivot_P",
                "Pivot_R1", "Pivot_S1",
                "Pivot_R2", "Pivot_S2",
                "Pivot_R3", "Pivot_S3",
                "Pivot_R4", "Pivot_S4",
                "Pivot_R5", "Pivot_S5",
            ):
                df[pivot_col] = np.nan
            df["Pivot_Bullish"] = False
            df["Pivot_Bearish"] = False

        # Previous-session levels are only needed by structural indicators/strategies.
        try:
            if not needs_prior_session:
                _init_structural_columns()
            else:
                session_dates = _session_dates_for_df(df, ticker)
                session_key = pd.Series(session_dates, index=df.index)
                level_mask = np.ones(len(df), dtype=bool)
                if str(_param("LEVELS_REGULAR_SESSION_ONLY", 0)).lower() in ("1", "true", "yes", "on"):
                    rth_mask = _regular_hours_mask(df, ticker)
                    if rth_mask.any():
                        level_mask = rth_mask
                daily_levels = df[level_mask].groupby(session_key[level_mask]).agg(
                    {"High": "max", "Low": "min", "Close": "last"}
                )
                prior_high_by_session = daily_levels["High"].shift(1)
                prior_low_by_session = daily_levels["Low"].shift(1)
                prior_close_by_session = daily_levels["Close"].shift(1)
                df["Daily_HighLow_High"] = session_key.map(prior_high_by_session).astype(float)
                df["Daily_HighLow_Low"] = session_key.map(prior_low_by_session).astype(float)
                df["Pivot_P"] = (df["Daily_HighLow_High"] + df["Daily_HighLow_Low"] + session_key.map(prior_close_by_session).astype(float)) / 3.0
                df["Pivot_R1"] = (2.0 * df["Pivot_P"]) - df["Daily_HighLow_Low"]
                df["Pivot_S1"] = (2.0 * df["Pivot_P"]) - df["Daily_HighLow_High"]
                prior_range = df["Daily_HighLow_High"] - df["Daily_HighLow_Low"]
                df["Pivot_R2"] = df["Pivot_P"] + prior_range
                df["Pivot_S2"] = df["Pivot_P"] - prior_range
                df["Pivot_R3"] = df["Daily_HighLow_High"] + (2.0 * (df["Pivot_P"] - df["Daily_HighLow_Low"]))
                df["Pivot_S3"] = df["Daily_HighLow_Low"] - (2.0 * (df["Daily_HighLow_High"] - df["Pivot_P"]))
                df["Pivot_R4"] = df["Pivot_R3"] + prior_range
                df["Pivot_S4"] = df["Pivot_S3"] - prior_range
                df["Pivot_R5"] = df["Pivot_R4"] + prior_range
                df["Pivot_S5"] = df["Pivot_S4"] - prior_range
                pivot_type = str(_param("PIVOT_TYPE", "classic")).lower()
                if pivot_type in ("fibonacci", "camarilla"):
                    prior_close = session_key.map(prior_close_by_session).astype(float)
                    if pivot_type == "fibonacci":
                        df["Pivot_R1"] = df["Pivot_P"] + 0.382 * prior_range
                        df["Pivot_R2"] = df["Pivot_P"] + 0.618 * prior_range
                        df["Pivot_R3"] = df["Pivot_P"] + 1.000 * prior_range
                        df["Pivot_S1"] = df["Pivot_P"] - 0.382 * prior_range
                        df["Pivot_S2"] = df["Pivot_P"] - 0.618 * prior_range
                        df["Pivot_S3"] = df["Pivot_P"] - 1.000 * prior_range
                    else:
                        df["Pivot_R1"] = prior_close + prior_range * 1.1 / 12
                        df["Pivot_R2"] = prior_close + prior_range * 1.1 / 6
                        df["Pivot_R3"] = prior_close + prior_range * 1.1 / 4
                        df["Pivot_S1"] = prior_close - prior_range * 1.1 / 12
                        df["Pivot_S2"] = prior_close - prior_range * 1.1 / 6
                        df["Pivot_S3"] = prior_close - prior_range * 1.1 / 4
                    for extra in ("Pivot_R4", "Pivot_S4", "Pivot_R5", "Pivot_S5"):
                        df[extra] = np.nan  # the extension levels are only defined for the classic variant

                if enabled_indicators is None or "Volume Profile" in enabled_indicators:
                    vp_rows = _param("VOLUME_PROFILE_ROWS", 48)
                    vp_value_area_pct = _param("VOLUME_PROFILE_VALUE_AREA_PCT", 70.0)
                    profile_by_session = {}
                    for session_date, session_df in df[level_mask].groupby(session_key[level_mask]):
                        profile = _volume_profile_distribution(
                            session_df,
                            rows=vp_rows,
                            value_area_pct=vp_value_area_pct,
                        )
                        if profile:
                            profile_by_session[session_date] = profile
                    profile_df = pd.DataFrame.from_dict(
                        {
                            session_date: (
                                profile["poc"],
                                profile["vah"],
                                profile["val"],
                            )
                            for session_date, profile in profile_by_session.items()
                        },
                        orient="index",
                        columns=["Volume_Profile_POC", "Volume_Profile_VAH", "Volume_Profile_VAL"],
                    ).sort_index()
                    prior_profile_df = profile_df.shift(1)
                    df["Volume_Profile_POC"] = session_key.map(prior_profile_df["Volume_Profile_POC"]).astype(float)
                    df["Volume_Profile_VAH"] = session_key.map(prior_profile_df["Volume_Profile_VAH"]).astype(float)
                    df["Volume_Profile_VAL"] = session_key.map(prior_profile_df["Volume_Profile_VAL"]).astype(float)
                    session_dates_sorted = sorted(profile_by_session)
                    current_session = session_key.iloc[-1] if len(session_key) else None
                    prior_sessions = [
                        session_date for session_date in session_dates_sorted
                        if current_session is not None and session_date < current_session
                    ]
                    if prior_sessions:
                        profile_session = prior_sessions[-1]
                        volume_profile_payload = {
                            **profile_by_session[profile_session],
                            "enabled": True,
                            "source": "prior_session",
                            "session_date": str(profile_session),
                        }
                else:
                    df["Volume_Profile_POC"] = np.nan
                    df["Volume_Profile_VAH"] = np.nan
                    df["Volume_Profile_VAL"] = np.nan

                hl_buffer = max(0.0, float(_param("DAILY_HIGH_LOW_BUFFER_PCT", 0.0))) / 100.0
                hl_buffer_atr = max(0.0, float(_param("DAILY_HIGH_LOW_BUFFER_ATR", 0.0) or 0.0))
                atr_buffer = (
                    _wilder_atr(high, low, close, 14).shift(1) * hl_buffer_atr if hl_buffer_atr > 0 else 0.0
                )  # previous bar's ATR: known before this candle
                high_trigger = df["Daily_HighLow_High"] * (1.0 + hl_buffer) + atr_buffer
                low_trigger = df["Daily_HighLow_Low"] * (1.0 - hl_buffer) - atr_buffer
                has_buffer = hl_buffer > 0 or hl_buffer_atr > 0
                df["Daily_HighLow_High_Trigger"] = high_trigger if has_buffer else np.nan
                df["Daily_HighLow_Low_Trigger"] = low_trigger if has_buffer else np.nan
                prev_close_series = close.shift(1)
                df["Daily_HighLow_Bullish"] = ((close > high_trigger) & (prev_close_series <= high_trigger)).fillna(False)
                df["Daily_HighLow_Bearish"] = ((close < low_trigger) & (prev_close_series >= low_trigger)).fillna(False)
                pivot_tolerance_pct = max(0.0, float(_param("PIVOT_TOLERANCE_PCT", 0.0))) / 100.0
                pivot_tolerance = close * pivot_tolerance_pct
                df["Pivot_Bullish"] = (
                    ((low <= df["Pivot_S1"] + pivot_tolerance) & (close > df["Pivot_S1"]))
                    | ((close > df["Pivot_P"]) & (prev_close_series <= df["Pivot_P"]))
                ).fillna(False)
                df["Pivot_Bearish"] = (
                    ((high >= df["Pivot_R1"] - pivot_tolerance) & (close < df["Pivot_R1"]))
                    | ((close < df["Pivot_P"]) & (prev_close_series >= df["Pivot_P"]))
                ).fillna(False)
                if enabled_indicators is None or "Volume Profile" in enabled_indicators:
                    vp_tolerance_pct = max(0.0, float(_param("VOLUME_PROFILE_TOLERANCE_PCT", 0.15))) / 100.0
                    vp_tol = close * vp_tolerance_pct
                    vp_confirm = max(1, min(5, int(float(_param("VOLUME_PROFILE_CONFIRMATION_CANDLES", 1)))))
                    vp_accepted_above = (
                        (close > df["Volume_Profile_VAH"] + vp_tol)
                        .rolling(vp_confirm, min_periods=vp_confirm)
                        .sum()
                        .ge(vp_confirm)
                    )
                    vp_accepted_below = (
                        (close < df["Volume_Profile_VAL"] - vp_tol)
                        .rolling(vp_confirm, min_periods=vp_confirm)
                        .sum()
                        .ge(vp_confirm)
                    )
                    df["Volume_Profile_Bullish"] = (
                        ((close > df["Volume_Profile_POC"]) & (prev_close_series <= df["Volume_Profile_POC"]))
                        | ((low <= df["Volume_Profile_VAL"] + vp_tol) & (close > df["Volume_Profile_VAL"]))
                        | vp_accepted_above
                    ).fillna(False)
                    df["Volume_Profile_Bearish"] = (
                        ((close < df["Volume_Profile_POC"]) & (prev_close_series >= df["Volume_Profile_POC"]))
                        | ((high >= df["Volume_Profile_VAH"] - vp_tol) & (close < df["Volume_Profile_VAH"]))
                        | vp_accepted_below
                    ).fillna(False)
                else:
                    df["Volume_Profile_Bullish"] = False
                    df["Volume_Profile_Bearish"] = False
        except Exception as hl_exc:
            print(f"[indicators] Prior-session levels error: {hl_exc}")
            _init_structural_columns()

        if needs_swing_points:
            _add_swing_point_columns(df, ticker, settings, _param, timeframe=timeframe)

        # --- RSI -----------------------------------------------------------
        if rsi_is_required:
            rsi_period = _indicator_period(_param("RSI_PERIOD", 14), 14, minimum=2, maximum=500)
            df["RSI"] = _wilder_rsi(close, rsi_period)
        else:
            df["RSI"] = np.nan

        # --- EMA -----------------------------------------------------------
        if ema_is_required:
            ema_fast_period = _indicator_period(_param("EMA_FAST", 9), 9, maximum=500)
            ema_slow_period = _indicator_period(_param("EMA_SLOW", 21), 21, maximum=500)
            df["EMA_Fast"] = ta_lib.trend.ema_indicator(close, window=ema_fast_period)
            df["EMA_Slow"] = ta_lib.trend.ema_indicator(close, window=ema_slow_period)
        else:
            df["EMA_Fast"] = close
            df["EMA_Slow"] = close

        # --- SMA -----------------------------------------------------------
        if sma_is_required:
            df["SMA"] = ta_lib.trend.sma_indicator(
                close,
                window=_indicator_period(_param("SMA_PERIOD", 200), 200, maximum=500),
            )
            df["SMA_Secondary"] = ta_lib.trend.sma_indicator(
                close,
                window=_indicator_period(_param("SMA_SECOND_PERIOD", 50), 50, maximum=500),
            )
        else:
            df["SMA"] = close
            df["SMA_Secondary"] = close

        # --- MACD ----------------------------------------------------------
        if _needs("MACD"):
            macd_fast_period, macd_slow_period, macd_signal_period = _macd_periods(
                fast=_param("MACD_FAST", 12),
                slow=_param("MACD_SLOW", 26),
                signal=_param("MACD_SIGNAL", 9),
            )
            macd_ind = ta_lib.trend.MACD(
                close,
                window_fast=macd_fast_period,
                window_slow=macd_slow_period,
                window_sign=macd_signal_period,
            )
            df["MACD_Line"] = macd_ind.macd()
            df["MACD_Signal"] = macd_ind.macd_signal()
            df["MACD_Hist"] = macd_ind.macd_diff()
        else:
            df["MACD_Line"] = 0.0
            df["MACD_Signal"] = 0.0
            df["MACD_Hist"] = 0.0

        # --- Bollinger Bands -----------------------------------------------
        if _needs("Bollinger"):
            boll_ind = ta_lib.volatility.BollingerBands(
                close, window=_indicator_period(_param("BOLL_PERIOD", 20), 20, minimum=2, maximum=500),
                window_dev=max(0.1, float(_param("BOLL_STD_DEV", 2.0)))
            )
            df["BOLL_Upper"] = boll_ind.bollinger_hband()
            df["BOLL_Lower"] = boll_ind.bollinger_lband()
            df["BOLL_Middle"] = boll_ind.bollinger_mavg()
        else:
            df["BOLL_Upper"] = close
            df["BOLL_Lower"] = close
            df["BOLL_Middle"] = close

        if needs_extension_indicators:
            df = _add_standard_extension_columns(
                df,
                close,
                high,
                low,
                volume,
                _param,
                enabled_indicators=enabled_indicators,
            )
            # The extension suite adds many columns; consolidate the frame once
            # so later structural calculations do not operate on a
            # highly fragmented DataFrame.
            close = df["Close"]
            high = df["High"]
            low = df["Low"]
            volume = df["Volume"]

        deferred_indicator_names = {
            "EMA Cross", "ADX Trend", "Supertrend", "Mystic Pulse",
            "Chart Patterns",
        }
        simple_fast_path = (
            enabled_indicators is not None
            and not bool(enabled_indicators & deferred_indicator_names)
            and buy_mode in {"", "indicator"}
            and sell_mode in {"", "sltp", "percentage", "custom_sltp", "custom_underlying_sltp"}
        )
        if simple_fast_path:
            # Standard indicators already calculated above can return without
            # entering the trend-loop and pattern-engine blocks.
            try:
                needs_session_vwap = _needs("VWAP")
                needs_auto_avwap = _needs("Auto-AVWAP")
                if not (needs_session_vwap or needs_auto_avwap):
                    raise _SkipUnselectedIndicator
                session_vwap = _session_vwap_series(
                    df, df["High"], df["Low"], df["Close"], df["Volume"], ticker=ticker,
                    regular_only=_flag(_param("VWAP_REGULAR_SESSION_ONLY", 0)),
                )
                auto_avwap, current_anchor_idx = _auto_avwap_series(
                    df, df["High"], df["Low"], df["Close"], df["Volume"],
                    ticker=ticker, session_vwap=session_vwap, param=_param,
                )
                df["VWAP"] = session_vwap if needs_session_vwap else close.copy()
                df["Auto_AVWAP"] = auto_avwap if needs_auto_avwap else close.copy()
                df["Anchor_Date"] = current_anchor_idx.map(
                    lambda idx: df.index[int(idx)] if not pd.isna(idx) else pd.NaT
                ) if needs_auto_avwap else pd.NaT
            except _SkipUnselectedIndicator:
                df["VWAP"] = close.copy()
                df["Auto_AVWAP"] = close.copy()
                df["Anchor_Date"] = pd.NaT
            except Exception as e:
                print(f"[indicators] Fast AVWAP calc error: {e}")
                df["VWAP"] = close.copy()
                df["Auto_AVWAP"] = close.copy()
                df["Anchor_Date"] = pd.NaT

            atr_period = _atr_period(_param("ATR_PERIOD", 14))
            df["ATR"] = _wilder_atr(high, low, close, atr_period)
            _add_fast_candlestick_columns(df, reliable_only=_flag(_param("CANDLE_RELIABLE_ONLY", 0)))
            # The normalized signal builder expects these legacy columns, but
            # their signals are filtered out because they were not selected.
            df["Supertrend"] = close
            df["Supertrend_Trend"] = True
            df["ADX"] = 0.0
            df["DI_Plus"] = 0.0
            df["DI_Minus"] = 0.0
            df["Bull_Pulse"] = 0
            df["Bear_Pulse"] = 0

            for col in ["RSI", "BOLL_Upper", "BOLL_Lower", "BOLL_Middle", "VWAP", "Auto_AVWAP", "ATR"]:
                df[col] = df[col].ffill()

            eval_context = active_evaluation_context.get()
            if eval_context in ("dashboard", "bots", "screener"):
                chart_df = _completed_candles_for_live_chart(df, timeframe, ticker=ticker)
                latest = chart_df.iloc[-1]
                prev = chart_df.iloc[-2] if len(chart_df) > 1 else latest
                recent_bars = chart_df.tail(5)
            else:
                chart_df = df
                latest = df.iloc[-1]
                prev = df.iloc[-2] if len(df) > 1 else latest
                recent_bars = df.tail(5)

            signals = _generate_signals(latest, prev, settings=settings, recent_bars=recent_bars, chart_latest=latest)

            def _safe_fast(val):
                try:
                    v = float(val)
                    return None if (v != v or v == float('inf') or v == float('-inf')) else v
                except Exception:
                    return None

            internal_rsi = _safe_fast(latest.get("RSI"))

            display_chart_df = _bounded_chart_frame(chart_df, settings=settings)
            prior_day_ohlc = _prior_session_ohlc_from_df(chart_df, ticker=ticker)
            price_history = [
                _prune_chart_record({
                    "time": _unix_timestamp(t),
                    "open": _safe_fast(row["Open"]),
                    "high": _safe_fast(row["High"]),
                    "low": _safe_fast(row["Low"]),
                    "close": _safe_fast(row["Close"]),
                    "volume": _safe_fast(row["Volume"]),
                    **_extension_chart_fields(row, _safe_fast, selected_chart_keys),
                    "sma": _safe_fast(row.get("SMA")),
                    "sma_secondary": _safe_fast(row.get("SMA_Secondary")),
                    "vwap": _safe_fast(row.get("VWAP")),
                    "auto_avwap": _safe_fast(row.get("Auto_AVWAP")),
                    "boll_upper": _safe_fast(row.get("BOLL_Upper")),
                    "boll_middle": _safe_fast(row.get("BOLL_Middle")),
                    "boll_lower": _safe_fast(row.get("BOLL_Lower")),
                    "rsi": _safe_fast(row.get("RSI")),
                    "macd_line": _safe_fast(row.get("MACD_Line")),
                    "macd_signal": _safe_fast(row.get("MACD_Signal")),
                    "macd_hist": _safe_fast(row.get("MACD_Hist")),
                    "daily_high_low_high": _safe_fast(row.get("Daily_HighLow_High")),
                    "daily_high_low_low": _safe_fast(row.get("Daily_HighLow_Low")),
                    "daily_high_low_high_trigger": _safe_fast(row.get("Daily_HighLow_High_Trigger")),
                    "daily_high_low_low_trigger": _safe_fast(row.get("Daily_HighLow_Low_Trigger")),
                    "daily_high_low_bullish": bool(row.get("Daily_HighLow_Bullish", False)),
                    "daily_high_low_bearish": bool(row.get("Daily_HighLow_Bearish", False)),
                    "pivot_p": _safe_fast(row.get("Pivot_P")),
                    "pivot_r1": _safe_fast(row.get("Pivot_R1")),
                    "pivot_s1": _safe_fast(row.get("Pivot_S1")),
                    "pivot_r2": _safe_fast(row.get("Pivot_R2")),
                    "pivot_s2": _safe_fast(row.get("Pivot_S2")),
                    "pivot_r3": _safe_fast(row.get("Pivot_R3")),
                    "pivot_s3": _safe_fast(row.get("Pivot_S3")),
                    "pivot_r4": _safe_fast(row.get("Pivot_R4")),
                    "pivot_s4": _safe_fast(row.get("Pivot_S4")),
                    "pivot_r5": _safe_fast(row.get("Pivot_R5")),
                    "pivot_s5": _safe_fast(row.get("Pivot_S5")),
                    "pivot_bullish": bool(row.get("Pivot_Bullish", False)),
                    "pivot_bearish": bool(row.get("Pivot_Bearish", False)),
                    "volume_profile_poc": _safe_fast(row.get("Volume_Profile_POC")),
                    "volume_profile_vah": _safe_fast(row.get("Volume_Profile_VAH")),
                    "volume_profile_val": _safe_fast(row.get("Volume_Profile_VAL")),
                    "swing_support_1": _safe_fast(row.get("Swing_Support_1")),
                    "swing_support_2": _safe_fast(row.get("Swing_Support_2")),
                    "swing_support_3": _safe_fast(row.get("Swing_Support_3")),
                    "swing_support_4": _safe_fast(row.get("Swing_Support_4")),
                    "swing_support_5": _safe_fast(row.get("Swing_Support_5")),
                    "swing_resistance_1": _safe_fast(row.get("Swing_Resistance_1")),
                    "swing_resistance_2": _safe_fast(row.get("Swing_Resistance_2")),
                    "swing_resistance_3": _safe_fast(row.get("Swing_Resistance_3")),
                    "swing_resistance_4": _safe_fast(row.get("Swing_Resistance_4")),
                    "swing_resistance_5": _safe_fast(row.get("Swing_Resistance_5")),
                    "cdl_name": str(row.get("cdl_name", "Neutral")),
                    "cdl_bullish": bool(row.get("cdl_bullish", False)),
                    "cdl_bearish": bool(row.get("cdl_bearish", False)),
                }, selected_chart_keys) for t, row in display_chart_df.iterrows()
            ]
            return {
                "ticker": ticker,
                "price": float(latest["Close"]),
                "atr": round(float(latest["ATR"]), 8),
                "rsi": round(internal_rsi, 2) if rsi_is_public and internal_rsi is not None else None,
                "_internal_rsi": internal_rsi,
                "vwap": _finite_or_none(latest["VWAP"]),  # NaN while VWAP is unavailable (daily bars, pre-open)
                "bullish_count": sum(s.get("weight", 1) for s in signals.values() if s["signal"] == "BULLISH"),
                "bearish_count": sum(s.get("weight", 1) for s in signals.values() if s["signal"] == "BEARISH"),
                "signals": signals,
                "price_history": price_history,
                "prior_day_ohlc": prior_day_ohlc,
                "volume_profile": volume_profile_payload,
                "candle_meta": {
                    "calculation_bars": len(chart_df),
                    "display_bars": len(display_chart_df),
                    "display_limit": int((settings or {}).get("_chart_display_bars", 0) or 0),
                    **_evaluation_candle_metadata(chart_df, timeframe),
                },
                "df": df,
            }

        # --- Supertrend (TradingView-compatible: Wilder ATR with SMA seed) ---
        st_period = max(2, int(float(_param("SUPERTREND_PERIOD", 10))))
        st_atr = _wilder_atr(high, low, close, st_period)

        hl2 = (high + low) / 2.0
        st_mult = float(_param("SUPERTREND_MULTIPLIER", 3.0))
        basic_ub = hl2 + st_mult * st_atr
        basic_lb = hl2 - st_mult * st_atr

        n = len(df)
        final_ub = np.empty(n)
        final_lb = np.empty(n)
        trend = np.empty(n, dtype=bool)
        st_vals = np.empty(n)

        # Convert to numpy for speed
        close_arr = close.to_numpy()
        bub = basic_ub.to_numpy()
        blb = basic_lb.to_numpy()

        # Find first non-NaN index to start calculation
        start_idx = 0
        for idx, val in enumerate(bub):
            if not np.isnan(val):
                start_idx = idx
                break

        final_ub[:] = np.nan
        final_lb[:] = np.nan
        trend[:] = True
        st_vals[:] = np.nan

        if start_idx < n:
            final_ub[start_idx] = bub[start_idx]
            final_lb[start_idx] = blb[start_idx]
            trend[start_idx] = True
            st_vals[start_idx] = blb[start_idx]

        for i in range(start_idx + 1, n):
            # Upper band
            if bub[i] < final_ub[i - 1] or close_arr[i - 1] > final_ub[i - 1]:
                final_ub[i] = bub[i]
            else:
                final_ub[i] = final_ub[i - 1]

            # Lower band
            if blb[i] > final_lb[i - 1] or close_arr[i - 1] < final_lb[i - 1]:
                final_lb[i] = blb[i]
            else:
                final_lb[i] = final_lb[i - 1]

            # Trend flip
            if trend[i - 1]:
                trend[i] = not (close_arr[i] < final_lb[i])
            else:
                trend[i] = close_arr[i] > final_ub[i]

            st_vals[i] = final_lb[i] if trend[i] else final_ub[i]

        df["Supertrend"] = st_vals
        df["Supertrend_Trend"] = trend

        # ---------------------------------------------------------
        # 4. AVWAP / AUTO-ANCHORED VWAP (Institutional Smart Line - Rolling Model)
        # ---------------------------------------------------------
        try:
            needs_session_vwap = _needs("VWAP")
            needs_auto_avwap = _needs("Auto-AVWAP")
            if not (needs_session_vwap or needs_auto_avwap):
                raise _SkipUnselectedIndicator
            session_vwap = _session_vwap_series(
                df, df["High"], df["Low"], df["Close"], df["Volume"], ticker=ticker,
                regular_only=_flag(_param("VWAP_REGULAR_SESSION_ONLY", 0)),
            )
            auto_avwap, current_anchor_idx = _auto_avwap_series(
                df, df["High"], df["Low"], df["Close"], df["Volume"],
                ticker=ticker, session_vwap=session_vwap, param=_param,
            )
            df["VWAP"] = session_vwap if needs_session_vwap else close.copy()
            df["Auto_AVWAP"] = auto_avwap if needs_auto_avwap else close.copy()
            df["Anchor_Date"] = current_anchor_idx.map(
                lambda idx: df.index[int(idx)] if not pd.isna(idx) else pd.NaT
            ) if needs_auto_avwap else pd.NaT

        except _SkipUnselectedIndicator:
            df["VWAP"] = close.copy()
            df["Auto_AVWAP"] = close.copy()
            df["Anchor_Date"] = pd.NaT
        except Exception as e:
            print(f"[indicators] AVWAP Calc Error: {e}")
            df["VWAP"] = close.copy()
            df["Auto_AVWAP"] = close.copy()
            df["Anchor_Date"] = pd.NaT

        # --- ATR (Wilder's Smoothing) --------------------------------------
        atr_period = _atr_period(_param("ATR_PERIOD", 14))
        df["ATR"] = _wilder_atr(high, low, close, atr_period)

        # --- ADX (Standard Welles Wilder ADX / DI+ / DI-) -------------------
        if _needs("ADX Trend"):
            adx_period = _indicator_period(_param("ADX_PERIOD", 14), 14, minimum=2, maximum=500)
            df["ADX"], df["DI_Plus"], df["DI_Minus"] = _wilder_dmi_series(
                high, low, close, adx_period
            )
        else:
            df["ADX"] = np.nan
            df["DI_Plus"] = np.nan
            df["DI_Minus"] = np.nan

        # --- Mystic Pulse V2.0 (Adaptive DMI + Persistence Engine) ----------
        presets = _get_mystic_v2_presets(timeframe)
        adx_len = presets['adx']
        try:
            adx_override = int(float(_param("MYSTIC_PULSE_ADX_LENGTH", 0) or 0))
        except (TypeError, ValueError):
            adx_override = 0
        if adx_override >= 2:
            adx_len = min(100, adx_override)  # e.g. 14 = Wilder's standard length
        smth = presets['smth']

        # 1. Pre-Smoothing (Adaptive SMA Filter)
        if smth > 1:
            work_close = df["Close"].rolling(window=smth).mean()
            work_high = df["High"].rolling(window=smth).mean()
            work_low = df["Low"].rolling(window=smth).mean()
        else:
            work_close = df["Close"]
            work_high = df["High"]
            work_low = df["Low"]

        # 2. Components (TR, +DM, -DM)
        tr = pd.concat([
            work_high - work_low,
            (work_high - work_close.shift(1)).abs(),
            (work_low - work_close.shift(1)).abs()
        ], axis=1).max(axis=1, skipna=False)

        up_move = work_high - work_high.shift(1)
        dn_move = work_low.shift(1) - work_low
        moves_known = up_move.notna() & dn_move.notna()
        plus_dm = np.where(moves_known, np.where((up_move > dn_move) & (up_move > 0), up_move, 0.0), np.nan)
        minus_dm = np.where(moves_known, np.where((dn_move > up_move) & (dn_move > 0), dn_move, 0.0), np.nan)

        # 3. Wilder's Smoothing: seeded with the sum of the first adx_len values, NaN until a full window exists
        smoothed_tr = _wilders_smooth_seeded(tr.values, adx_len)
        smoothed_pdm = _wilders_smooth_seeded(plus_dm, adx_len)
        smoothed_mdm = _wilders_smooth_seeded(minus_dm, adx_len)

        # 4. DI Plus/Minus
        denom = np.where(smoothed_tr == 0, 1.0, smoothed_tr)
        df["plus_di"] = (smoothed_pdm / denom) * 100
        df["minus_di"] = (smoothed_mdm / denom) * 100

        # 5. Persistence Counter (V2 Logic)
        pos_count = np.zeros(len(df))
        neg_count = np.zeros(len(df))
        c_pos, c_neg = 0, 0
        di_plus = df["plus_di"].values
        di_minus = df["minus_di"].values

        for i in range(1, len(df)):
            if not np.isnan(di_plus[i]) and not np.isnan(di_plus[i-1]) and di_plus[i] > di_plus[i-1] and di_plus[i] > di_minus[i]:
                c_pos += 1
                c_neg = 0
            elif not np.isnan(di_minus[i]) and not np.isnan(di_minus[i-1]) and di_minus[i] > di_minus[i-1] and di_minus[i] > di_plus[i]:
                c_neg += 1
                c_pos = 0
            pos_count[i] = c_pos
            neg_count[i] = c_neg

        df["Bull_Pulse"] = pos_count
        df["Bear_Pulse"] = neg_count

        # 6. Normalization for Strategy Entry (Matching the "Neon" visuals)
        collect_length = 100
        df['max_p'] = df['Bull_Pulse'].rolling(collect_length).max()
        df['max_n'] = df['Bear_Pulse'].rolling(collect_length).max()

        # 7. Signal Logic (Threshold based on MYSTIC_PULSE_THRESHOLD parameter)
        mystic_thresh = float(_param("MYSTIC_PULSE_THRESHOLD", 5))
        df["mp_buy_trigger"] = (df["Bull_Pulse"] >= mystic_thresh) & (df["Bull_Pulse"].shift(1) < mystic_thresh)
        df["mp_sell_trigger"] = (df["Bear_Pulse"] >= mystic_thresh) & (df["Bear_Pulse"].shift(1) < mystic_thresh)

        # --- Candlestick Patterns (shared with the fast path) ---------------
        _add_candlestick_columns(df, reliable_only=_flag(_param("CANDLE_RELIABLE_ONLY", 0)))

        # ═══════════════════════════════════════════════════════════════════
        # Chart Patterns causal multi-pattern engine
        # ═══════════════════════════════════════════════════════════════════

        # --- Chart Patterns: causal multi-pattern engine ---
        chart_patterns_active = enabled_indicators is None or "Chart Patterns" in enabled_indicators
        try:
            if chart_patterns_active:
                df = calculate_botbulls5(
                    df,
                    _param,
                    evaluation_start=_botbulls5_live_evaluation_start(
                        df,
                        ticker,
                        settings=settings,
                    ),
                )
            else:
                df["BB5_Upper"] = np.nan
                df["BB5_Lower"] = np.nan
                df["BB5_Target"] = np.nan
                df["BB5_Invalidation"] = np.nan
                df["BB5_Breakout_Level"] = np.nan
                df["BB5_Upper_Start"] = np.nan
                df["BB5_Lower_Start"] = np.nan
                df["BB5_Formation_Start_Time"] = np.nan
                df["BB5_Formation_End_Time"] = np.nan
                df["BB5_Pole_Start_Time"] = np.nan
                df["BB5_Pole_End_Time"] = np.nan
                df["BB5_Formation_Bars"] = 0
                df["BB5_Confidence"] = 0.0
                df["BB5_Direction"] = 0
                df["BB5_Reasons"] = ""
                df["BB5_Breakout_Bull"] = False
                df["BB5_Breakout_Bear"] = False
                df["BB5_Signal"] = 0
                df["BB5_Signal_Confidence"] = 0.0
                df["BB5_Pattern"] = "NONE"
                df["BB5_State"] = "NONE"
                df["BB5_Signal_Pattern"] = "NONE"
        except Exception as e:
            print(f"[indicators] BotBulls5 error: {e}")
            df["BB5_Upper"] = np.nan
            df["BB5_Lower"] = np.nan
            df["BB5_Target"] = np.nan
            df["BB5_Invalidation"] = np.nan
            df["BB5_Breakout_Level"] = np.nan
            df["BB5_Upper_Start"] = np.nan
            df["BB5_Lower_Start"] = np.nan
            df["BB5_Formation_Start_Time"] = np.nan
            df["BB5_Formation_End_Time"] = np.nan
            df["BB5_Pole_Start_Time"] = np.nan
            df["BB5_Pole_End_Time"] = np.nan
            df["BB5_Formation_Bars"] = 0
            df["BB5_Confidence"] = 0.0
            df["BB5_Direction"] = 0
            df["BB5_Reasons"] = ""
            df["BB5_Breakout_Bull"] = False
            df["BB5_Breakout_Bear"] = False
            df["BB5_Signal"] = 0
            df["BB5_Signal_Confidence"] = 0.0
            df["BB5_Pattern"] = "NONE"
            df["BB5_State"] = "NONE"
            df["BB5_Signal_Pattern"] = "NONE"
        df = _add_chart_pattern_columns(df, chart_patterns_active)

        # --- Build result --------------------------------------------------
        # Forward-fill indicator warm-up gaps, but never back-fill prior-session
        # levels. Back-filling PDH/PDL or POC/VAH/VAL would leak future sessions
        # into earlier candles and distort backtests.
        indicator_cols = [
            "RSI", "EMA_Fast", "EMA_Slow", "SMA", "SMA_Secondary",
            "MACD_Line", "MACD_Signal", "MACD_Hist",
            "BOLL_Upper", "BOLL_Lower", "BOLL_Middle",
            "Supertrend", "ATR", "VWAP", "Auto_AVWAP",
            "ADX", "DI_Plus", "DI_Minus",
            "Daily_HighLow_High", "Daily_HighLow_Low",
            "Daily_HighLow_High_Trigger", "Daily_HighLow_Low_Trigger",
            "Volume_Profile_POC", "Volume_Profile_VAH", "Volume_Profile_VAL",
            "Swing_Support_1", "Swing_Support_2", "Swing_Support_3", "Swing_Support_4", "Swing_Support_5",
            "Swing_Resistance_1", "Swing_Resistance_2", "Swing_Resistance_3", "Swing_Resistance_4", "Swing_Resistance_5",
        ]
        indicator_cols.extend(EXTENSION_CALCULATION_COLUMNS)
        # Warm-up indicators must never be back-filled. Doing so leaks a future
        # computed value into earlier strategy bars and draws a false flat/vertical
        # line before the indicator actually exists.
        for col in indicator_cols:
            if col in df.columns:
                df[col] = df[col].ffill()

        eval_context = active_evaluation_context.get()
        if eval_context in ("dashboard", "bots", "screener"):
            chart_df = _completed_candles_for_live_chart(df, timeframe, ticker=ticker)
            latest = chart_df.iloc[-1]  # Dashboard and bots use the latest fully closed/completed candle.
            prev = chart_df.iloc[-2] if len(chart_df) > 1 else latest
            recent_bars = chart_df.tail(5)
        else:
            chart_df = df
            latest = df.iloc[-1]  # Backtest/custom analysis can use the current row
            prev = df.iloc[-2]
            recent_bars = df.tail(5)

        signal_chart_latest = latest if eval_context in ("dashboard", "bots", "screener") else df.iloc[-1]
        signals = _generate_signals(latest, prev, settings=settings, recent_bars=recent_bars, chart_latest=signal_chart_latest)

        def _safe(val):
            """Return float or None, never NaN/Inf."""
            try:
                v = float(val)
                return None if (v != v or v == float('inf') or v == float('-inf')) else v
            except Exception:
                return None

        internal_rsi = _safe(latest.get("RSI"))

        display_chart_df = _bounded_chart_frame(chart_df, settings=settings)
        prior_day_ohlc = _prior_session_ohlc_from_df(chart_df, ticker=ticker)
        chart_records = display_chart_df.to_dict(orient="records")
        chart_times = [_unix_timestamp(t) for t in display_chart_df.index]

        return {
            "ticker": ticker,
            "price": float(latest["Close"]),
            "atr": round(float(latest["ATR"]), 8),
            "rsi": round(internal_rsi, 2) if rsi_is_public and internal_rsi is not None else None,
            "_internal_rsi": internal_rsi,
            "vwap": _finite_or_none(latest["VWAP"]) if "VWAP" in latest else float(latest["Close"]),
            "bullish_count": sum(
                s.get("weight", 1) for s in signals.values() if s["signal"] == "BULLISH"
            ),
            "bearish_count": sum(
                s.get("weight", 1) for s in signals.values() if s["signal"] == "BEARISH"
            ),
            "signals": signals,
            "prior_day_ohlc": prior_day_ohlc,
            "volume_profile": volume_profile_payload,
            "candle_meta": {
                "calculation_bars": len(chart_df),
                "display_bars": len(display_chart_df),
                "display_limit": int((settings or {}).get("_chart_display_bars", 0) or 0),
                **_evaluation_candle_metadata(chart_df, timeframe),
            },
            "price_history": [
                _prune_chart_record({
                    "time": chart_times[i],
                    "open": _safe(row["Open"]),
                    "high": _safe(row["High"]),
                    "low": _safe(row["Low"]),
                    "close": _safe(row["Close"]),
                    "volume": _safe(row["Volume"]),
                    **_extension_chart_fields(row, _safe, selected_chart_keys),
                    # Overlay indicators (drawn on price chart)
                    "ema_fast":      _safe(row.get("EMA_Fast")),
                    "ema_slow":      _safe(row.get("EMA_Slow")),
                    "sma":           _safe(row.get("SMA")),
                    "sma_secondary": _safe(row.get("SMA_Secondary")),
                    "vwap":          _safe(row.get("VWAP")),
                    "auto_avwap":    _safe(row.get("Auto_AVWAP")),
                    "supertrend":    _safe(row.get("Supertrend")),
                    "supertrend_up": bool(row.get("Supertrend_Trend", True)),
                    "boll_upper":    _safe(row.get("BOLL_Upper")),
                    "boll_middle":   _safe(row.get("BOLL_Middle")),
                    "boll_lower":    _safe(row.get("BOLL_Lower")),
                    "daily_high_low_high": _safe(row.get("Daily_HighLow_High")),
                    "daily_high_low_low":  _safe(row.get("Daily_HighLow_Low")),
                    "daily_high_low_high_trigger": _safe(row.get("Daily_HighLow_High_Trigger")),
                    "daily_high_low_low_trigger":  _safe(row.get("Daily_HighLow_Low_Trigger")),
                    "daily_high_low_bullish": bool(row.get("Daily_HighLow_Bullish", False)),
                    "daily_high_low_bearish": bool(row.get("Daily_HighLow_Bearish", False)),
                    "pivot_p":       _safe(row.get("Pivot_P")),
                    "pivot_r1":      _safe(row.get("Pivot_R1")),
                    "pivot_s1":      _safe(row.get("Pivot_S1")),
                    "pivot_r2":      _safe(row.get("Pivot_R2")),
                    "pivot_s2":      _safe(row.get("Pivot_S2")),
                    "pivot_r3":      _safe(row.get("Pivot_R3")),
                    "pivot_s3":      _safe(row.get("Pivot_S3")),
                    "pivot_r4":      _safe(row.get("Pivot_R4")),
                    "pivot_s4":      _safe(row.get("Pivot_S4")),
                    "pivot_r5":      _safe(row.get("Pivot_R5")),
                    "pivot_s5":      _safe(row.get("Pivot_S5")),
                    "pivot_bullish": bool(row.get("Pivot_Bullish", False)),
                    "pivot_bearish": bool(row.get("Pivot_Bearish", False)),
                    "volume_profile_poc": _safe(row.get("Volume_Profile_POC")),
                    "volume_profile_vah": _safe(row.get("Volume_Profile_VAH")),
                    "volume_profile_val": _safe(row.get("Volume_Profile_VAL")),
                    "volume_profile_bullish": bool(row.get("Volume_Profile_Bullish", False)),
                    "volume_profile_bearish": bool(row.get("Volume_Profile_Bearish", False)),
                    "swing_support_1": _safe(row.get("Swing_Support_1")),
                    "swing_support_2": _safe(row.get("Swing_Support_2")),
                    "swing_support_3": _safe(row.get("Swing_Support_3")),
                    "swing_support_4": _safe(row.get("Swing_Support_4")),
                    "swing_support_5": _safe(row.get("Swing_Support_5")),
                    "swing_resistance_1": _safe(row.get("Swing_Resistance_1")),
                    "swing_resistance_2": _safe(row.get("Swing_Resistance_2")),
                    "swing_resistance_3": _safe(row.get("Swing_Resistance_3")),
                    "swing_resistance_4": _safe(row.get("Swing_Resistance_4")),
                    "swing_resistance_5": _safe(row.get("Swing_Resistance_5")),
                    "swing_bullish": bool(row.get("Swing_Bullish", False)),
                    "swing_bearish": bool(row.get("Swing_Bearish", False)),
                    # Oscillators (drawn in sub-pane)
                    "rsi":           _safe(row.get("RSI")),
                    "macd_line":     _safe(row.get("MACD_Line")),
                    "macd_signal":   _safe(row.get("MACD_Signal")),
                    "macd_hist":     _safe(row.get("MACD_Hist")),
                    "mystic_bull":   _safe(row.get("Bull_Pulse")),
                    "mystic_bear":   _safe(row.get("Bear_Pulse")),
                    "adx":           _safe(row.get("ADX")),
                    "di_plus":       _safe(row.get("DI_Plus")),
                    "di_minus":      _safe(row.get("DI_Minus")),
                    "cdl_name":      str(row.get("cdl_name", "Neutral")),
                    "cdl_bullish":   bool(row.get("cdl_bullish", False)),
                    "cdl_bearish":   bool(row.get("cdl_bearish", False)),
                    "cp_name":         str(row.get("CP_Name", "Neutral")),
                    "cp_bullish":      bool(row.get("CP_Bullish", False)),
                    "cp_bearish":      bool(row.get("CP_Bearish", False)),
                    "cp_quality":      _safe(row.get("CP_Quality")),
                }, selected_chart_keys) for i, row in enumerate(chart_records)
            ],
            "df": df,
        }

    except Exception as exc:
        print(f"[indicators] Calc error for {ticker}: {exc}")
        return None


# ---------------------------------------------------------------------------
# Signal generation (used by live engine AND backtester)
# ---------------------------------------------------------------------------
def _generate_standard_extension_signals(latest, prev, param):
    """Generate one normalized confluence vote for each classical extension."""
    def value(row, key, fallback=np.nan):
        try:
            result = float(row.get(key, fallback))
            return result if np.isfinite(result) else float(fallback)
        except (TypeError, ValueError):
            return float(fallback)

    def vote(condition_bull, condition_bear):
        if condition_bull:
            return "BULLISH"
        if condition_bear:
            return "BEARISH"
        return "NEUTRAL"

    def signal(name, verdict, reason, details=None):
        reason_text = str(reason)
        if "nan" in reason_text.lower() or "inf" in reason_text.lower():
            reason_text = "Awaiting sufficient history"
            verdict = "NEUTRAL"
        payload = {"signal": verdict, "reason": reason_text, "weight": 1}
        if isinstance(details, dict):
            payload.update(details)
        return name, payload

    price = value(latest, "Close", 0.0)
    prev_price = value(prev, "Close", price)
    entries = []

    atr = value(latest, "ATR_Ext")
    atr_avg = value(latest, "ATR_Ext_Avg", atr)
    atr_expansion_pct = _atr_expansion_threshold(param("ATR_EXPANSION_THRESHOLD_PCT", 5.0))
    atr_elevated = (
        np.isfinite(atr)
        and np.isfinite(atr_avg)
        and atr > atr_avg * (1.0 + (atr_expansion_pct / 100.0))
    )
    atr_range_multiplier = _atr_range_multiplier(param("ATR_RANGE_MULTIPLIER", 1.0))
    range_low = price - (atr * atr_range_multiplier) if np.isfinite(atr) else np.nan
    range_high = price + (atr * atr_range_multiplier) if np.isfinite(atr) else np.nan
    # ATR measures how far price moves, not which way, so it is shown as a volatility regime and never votes bullish or
    # bearish. Use it to size positions and set stops, or as a filter next to directional indicators.
    entries.append(signal(
        "ATR Volatility",
        "NEUTRAL",
        (
            f"{'Volatility expanding' if atr_elevated else 'Volatility normal'} • "
            f"ATR {atr:.3f} vs baseline {atr_avg:.3f} "
            f"({((atr / atr_avg) - 1.0) * 100:+.1f}%) • next-bar {atr_range_multiplier:g}× range "
            f"{range_low:.3f}-{range_high:.3f}"
        ) if np.isfinite(atr) and np.isfinite(atr_avg) else "Awaiting ATR / baseline history",
        {
            "value": float(atr) if np.isfinite(atr) else None,
            "baseline": float(atr_avg) if np.isfinite(atr_avg) else None,
            "range_low": float(range_low) if np.isfinite(range_low) else None,
            "range_high": float(range_high) if np.isfinite(range_high) else None,
            "period": int(_atr_period(param("ATR_PERIOD", 14))),
        },
    ))

    stoch_k, stoch_d = value(latest, "Stoch_K"), value(latest, "Stoch_D")
    prev_stoch_k, prev_stoch_d = value(prev, "Stoch_K"), value(prev, "Stoch_D")
    stoch_os, stoch_ob = float(param("STOCH_OVERSOLD", 20)), float(param("STOCH_OVERBOUGHT", 80))
    if _flag(param("STOCH_CROSS_ZONE_ONLY", 1)):
        # Textbook rule: a %K/%D cross only counts when it happens in the oversold/overbought zone.
        stoch_bull = stoch_k > stoch_d and prev_stoch_k <= prev_stoch_d and min(prev_stoch_k, stoch_k) <= stoch_os
        stoch_bear = stoch_k < stoch_d and prev_stoch_k >= prev_stoch_d and max(prev_stoch_k, stoch_k) >= stoch_ob
    else:
        stoch_bull = stoch_k > stoch_d and (prev_stoch_k <= prev_stoch_d or stoch_k <= stoch_os)
        stoch_bear = stoch_k < stoch_d and (prev_stoch_k >= prev_stoch_d or stoch_k >= stoch_ob)
    entries.append(signal("Stochastic", vote(stoch_bull, stoch_bear), f"%K {stoch_k:.1f} / %D {stoch_d:.1f}"))

    srsi_k, srsi_d = value(latest, "Stoch_RSI_K"), value(latest, "Stoch_RSI_D")
    prev_srsi_k, prev_srsi_d = value(prev, "Stoch_RSI_K"), value(prev, "Stoch_RSI_D")
    srsi_os = float(param("STOCH_RSI_OVERSOLD", 20))
    srsi_ob = float(param("STOCH_RSI_OVERBOUGHT", 80))
    if _flag(param("STOCH_RSI_CROSS_ZONE_ONLY", 1)):
        srsi_bull = srsi_k > srsi_d and prev_srsi_k <= prev_srsi_d and min(prev_srsi_k, srsi_k) <= srsi_os
        srsi_bear = srsi_k < srsi_d and prev_srsi_k >= prev_srsi_d and max(prev_srsi_k, srsi_k) >= srsi_ob
    else:
        srsi_bull = srsi_k > srsi_d and (prev_srsi_k <= prev_srsi_d or srsi_k <= srsi_os)
        srsi_bear = srsi_k < srsi_d and (prev_srsi_k >= prev_srsi_d or srsi_k >= srsi_ob)
    entries.append(signal("Stochastic RSI", vote(srsi_bull, srsi_bear), f"%K {srsi_k:.1f} / %D {srsi_d:.1f}"))

    cci = value(latest, "CCI_Ext")
    prev_cci = value(prev, "CCI_Ext", cci)
    cci_threshold = abs(float(param("CCI_THRESHOLD", 100)))
    entries.append(signal(
        "CCI",
        vote(
            cci < -cci_threshold and cci > prev_cci,
            cci > cci_threshold and cci < prev_cci,
        ),
        f"CCI {cci:.1f} ({cci - prev_cci:+.1f})",
    ))

    williams = value(latest, "Williams_R")
    prev_williams = value(prev, "Williams_R", williams)
    williams_os = float(param("WILLIAMS_R_OVERSOLD", -80))
    williams_ob = float(param("WILLIAMS_R_OVERBOUGHT", -20))
    entries.append(signal(
        "Williams %R",
        vote(williams <= williams_os and williams > prev_williams, williams >= williams_ob and williams < prev_williams),
        f"%R {williams:.1f}",
    ))

    roc = value(latest, "ROC_Ext")
    roc_band = abs(float(param("ROC_THRESHOLD_PCT", 0.0)))
    entries.append(signal("Rate of Change", vote(roc > roc_band, roc < -roc_band), f"ROC {roc:.2f}%"))
    momentum = value(latest, "Momentum_Ext")
    entries.append(signal(
        "Momentum",
        vote(momentum > 0, momentum < 0),
        f"Momentum {momentum:.3f} ({'above' if momentum > 0 else 'below' if momentum < 0 else 'at'} zero line)",
    ))

    mfi = value(latest, "MFI_Ext")
    prev_mfi = value(prev, "MFI_Ext", mfi)
    mfi_os, mfi_ob = float(param("MFI_OVERSOLD", 20)), float(param("MFI_OVERBOUGHT", 80))
    entries.append(signal(
        "Money Flow Index",
        vote(mfi <= mfi_os and mfi > prev_mfi, mfi >= mfi_ob and mfi < prev_mfi),
        f"MFI {mfi:.1f}",
    ))

    ultimate = value(latest, "Ultimate_Osc")
    prev_ultimate = value(prev, "Ultimate_Osc", ultimate)
    uo_os, uo_ob = float(param("UO_OVERSOLD", 30)), float(param("UO_OVERBOUGHT", 70))
    entries.append(signal(
        "Ultimate Oscillator",
        vote(ultimate <= uo_os and ultimate > prev_ultimate, ultimate >= uo_ob and ultimate < prev_ultimate),
        f"Ultimate {ultimate:.1f} (oversold ≤ {uo_os:g} / overbought ≥ {uo_ob:g}, vote on the turn)",
    ))
    awesome = value(latest, "Awesome_Osc")
    entries.append(signal("Awesome Oscillator", vote(awesome > 0, awesome < 0), f"AO {awesome:.3f}"))
    cmo = value(latest, "CMO")
    prev_cmo = value(prev, "CMO", cmo)
    cmo_threshold = abs(float(param("CMO_THRESHOLD", 50)))
    entries.append(signal(
        "Chande Momentum Oscillator",
        vote(cmo <= -cmo_threshold and cmo > prev_cmo, cmo >= cmo_threshold and cmo < prev_cmo),
        f"CMO {cmo:.1f} (oversold ≤ -{cmo_threshold:g} / overbought ≥ {cmo_threshold:g}, vote on the turn)",
    ))

    tsi, tsi_signal = value(latest, "TSI_Ext"), value(latest, "TSI_Signal")
    entries.append(signal("True Strength Index", vote(tsi > tsi_signal, tsi < tsi_signal), f"TSI {tsi:.2f} / signal {tsi_signal:.2f}"))
    trix, trix_signal = value(latest, "TRIX_Ext"), value(latest, "TRIX_Signal")
    entries.append(signal("TRIX", vote(trix > trix_signal, trix < trix_signal), f"TRIX {trix:.3f} / signal {trix_signal:.3f}"))
    kst, kst_signal = value(latest, "KST_Ext"), value(latest, "KST_Signal")
    entries.append(signal("Know Sure Thing", vote(kst > kst_signal, kst < kst_signal), f"KST {kst:.2f} / signal {kst_signal:.2f}"))
    ppo_hist = value(latest, "PPO_Hist")
    entries.append(signal("Percentage Price Oscillator", vote(ppo_hist > 0, ppo_hist < 0), f"PPO histogram {ppo_hist:.3f}%"))

    aroon_up, aroon_down = value(latest, "Aroon_Up"), value(latest, "Aroon_Down")
    aroon_threshold = float(param("AROON_THRESHOLD", 70))
    entries.append(signal(
        "Aroon",
        vote(aroon_up >= aroon_threshold and aroon_up > aroon_down, aroon_down >= aroon_threshold and aroon_down > aroon_up),
        f"Up {aroon_up:.1f} / Down {aroon_down:.1f}",
    ))
    vortex_pos, vortex_neg = value(latest, "Vortex_Pos"), value(latest, "Vortex_Neg")
    entries.append(signal("Vortex Indicator", vote(vortex_pos > vortex_neg, vortex_pos < vortex_neg), f"VI+ {vortex_pos:.3f} / VI- {vortex_neg:.3f}"))

    conversion = value(latest, "Ichimoku_Conversion")
    base = value(latest, "Ichimoku_Base")
    span_a, span_b = value(latest, "Ichimoku_Span_A"), value(latest, "Ichimoku_Span_B")
    cloud_a, cloud_b = value(latest, "Ichimoku_Cloud_A"), value(latest, "Ichimoku_Cloud_B")
    chikou_ref = value(latest, "Ichimoku_Chikou_Ref")
    cloud_known = np.isfinite(cloud_a) and np.isfinite(cloud_b)
    cloud_top = max(cloud_a, cloud_b) if cloud_known else np.nan
    cloud_bottom = min(cloud_a, cloud_b) if cloud_known else np.nan
    # Standard strong signal: price clear of the displaced cloud, Tenkan on the right side of Kijun, the projected
    # (future) cloud pointing the same way, and the Chikou span (today's close vs the close one displacement ago) clear.
    ichimoku_bull = (
        cloud_known
        and price > cloud_top
        and conversion > base
        and span_a > span_b
        and price > chikou_ref
    )
    ichimoku_bear = (
        cloud_known
        and price < cloud_bottom
        and conversion < base
        and span_a < span_b
        and price < chikou_ref
    )
    entries.append(signal(
        "Ichimoku Cloud",
        vote(ichimoku_bull, ichimoku_bear),
        (
            f"Price {price:.2f} / conversion {conversion:.2f} / base {base:.2f} / "
            f"cloud {cloud_bottom:.2f}-{cloud_top:.2f}"
        ),
    ))
    psar = value(latest, "PSAR")
    entries.append(signal("Parabolic SAR", vote(price > psar, price < psar), f"SAR {psar:.2f}"))
    donchian_upper = value(latest, "Donchian_Upper_Prior")
    donchian_lower = value(latest, "Donchian_Lower_Prior")
    entries.append(signal(
        "Donchian Channels",
        vote(price > donchian_upper, price < donchian_lower),
        f"Prior channel {donchian_lower:.2f}-{donchian_upper:.2f}",
    ))
    keltner_mid = value(latest, "Keltner_Mid")
    keltner_upper, keltner_lower = value(latest, "Keltner_Upper"), value(latest, "Keltner_Lower")
    if str(param("KELTNER_VOTE_MODE", "breakout")).lower() == "midline":
        entries.append(signal("Keltner Channels", vote(price > keltner_mid, price < keltner_mid), f"Price {price:.2f} / center {keltner_mid:.2f}"))
    else:
        entries.append(signal(
            "Keltner Channels",
            vote(price > keltner_upper, price < keltner_lower),
            f"Price {price:.2f} / bands {keltner_lower:.2f}-{keltner_upper:.2f}",
        ))

    choppiness = value(latest, "Choppiness")
    chop_trend = float(param("CHOP_TREND_THRESHOLD", 38.2))
    chop_range = float(param("CHOP_RANGE_THRESHOLD", 61.8))
    chop_is_directional = choppiness < chop_trend
    chop_reference = value(latest, "Chop_Direction_Ref")
    entries.append(signal(
        "Choppiness Index",
        vote(chop_is_directional and price > chop_reference, chop_is_directional and price < chop_reference),
        f"Choppiness {choppiness:.1f}" + (" (ranging)" if choppiness > chop_range else "")
        + (" (trending)" if chop_is_directional else ""),
    ))

    for name, column in (
        ("DEMA", "DEMA"),
        ("TEMA", "TEMA"),
        ("Hull Moving Average", "HMA"),
        ("Weighted Moving Average", "WMA_Ext"),
        ("VWMA", "VWMA_Ext"),
        ("Arnaud Legoux Moving Average", "ALMA"),
        ("Least Squares Moving Average", "LSMA"),
    ):
        average = value(latest, column)
        entries.append(signal(name, vote(price > average, price < average), f"Price {price:.2f} / average {average:.2f}"))

    obv, obv_signal = value(latest, "OBV_Ext"), value(latest, "OBV_Signal")
    entries.append(signal("On-Balance Volume", vote(obv > obv_signal, obv < obv_signal), f"OBV vs signal {obv - obv_signal:+.0f}"))
    cmf = value(latest, "CMF_Ext")
    entries.append(signal("Chaikin Money Flow", vote(cmf > 0, cmf < 0), f"CMF {cmf:.3f}"))
    adl, adl_signal = value(latest, "ADL_Ext"), value(latest, "ADL_Signal")
    entries.append(signal("Accumulation/Distribution", vote(adl > adl_signal, adl < adl_signal), f"A/D vs signal {adl - adl_signal:+.0f}"))
    chaikin = value(latest, "Chaikin_Osc")
    entries.append(signal("Chaikin Oscillator", vote(chaikin > 0, chaikin < 0), f"Chaikin {chaikin:.0f}"))
    pvt, pvt_signal = value(latest, "PVT_Ext"), value(latest, "PVT_Signal")
    entries.append(signal("Price Volume Trend", vote(pvt > pvt_signal, pvt < pvt_signal), f"PVT vs signal {pvt - pvt_signal:+.0f}"))
    eom = value(latest, "EOM_Ext")
    entries.append(signal("Ease of Movement", vote(eom > 0, eom < 0), f"EOM {eom:.3f}"))
    force = value(latest, "Force_Index")
    entries.append(signal("Force Index", vote(force > 0, force < 0), f"Force {force:.0f}"))

    tr_trail = value(latest, "TR_Trail")
    if np.isfinite(tr_trail):
        tr_up = bool(latest.get("TR_Trend", True))
        tr_prev_up = bool(prev.get("TR_Trend", tr_up))
        tracer = value(latest, "TR_Tracer")
        tracer_text = "" if not np.isfinite(tracer) else (", price above tracer" if price > tracer else ", price below tracer")
        flip = " | FLIP ↑" if tr_up and not tr_prev_up else (" | FLIP ↓" if not tr_up and tr_prev_up else "")
        entries.append(signal(
            "Trend Rider", vote(tr_up, not tr_up),
            f"Trend Rider {'BUY' if tr_up else 'SELL'} (trail {'up' if tr_up else 'down'} {tr_trail:.2f}, "
            f"confluence {int(value(latest, 'TR_Score', 0))}/4{tracer_text}){flip}",
        ))
    else:
        entries.append(signal("Trend Rider", "NEUTRAL", "Awaiting sufficient history"))
    haut_trail = value(latest, "HAUT_Trail")
    if np.isfinite(haut_trail):
        haut_up = bool(latest.get("HAUT_Above", True))
        haut_prev_up = bool(prev.get("HAUT_Above", haut_up))
        flip = " | FLIP ↑" if haut_up and not haut_prev_up else (" | FLIP ↓" if not haut_up and haut_prev_up else "")
        entries.append(signal(
            "HA+UT", vote(haut_up, not haut_up),
            f"HA+UT {'BUY' if haut_up else 'SELL'} (Heikin-Ashi {'above' if haut_up else 'below'} trail {haut_trail:.2f}){flip}",
        ))
    else:
        entries.append(signal("HA+UT", "NEUTRAL", "Awaiting sufficient history"))
    return dict(entries)


def _generate_rsi_signal(current_rsi, previous_rsi, oversold=30, overbought=70, period=14):
    """Create the shared RSI exhaustion-turn vote without warm-up leakage."""
    length = _indicator_period(period, 14, minimum=2, maximum=500)
    oversold_level, overbought_level = _rsi_thresholds(oversold, overbought)
    if oversold_level >= overbought_level:
        return {
            "signal": "NEUTRAL",
            "reason": "Choose different RSI oversold and overbought levels",
            "weight": 1,
        }
    if not np.isfinite(current_rsi) or not np.isfinite(previous_rsi):
        return {
            "signal": "NEUTRAL",
            "reason": f"RSI {length} warming up; awaiting {length + 1} completed candles",
            "weight": 1,
        }
    if current_rsi < oversold_level and current_rsi > previous_rsi:
        signal_value = "BULLISH"
        reason = f"Oversold and rising {current_rsi:.1f} (below {oversold_level:g})"
    elif current_rsi > overbought_level and current_rsi < previous_rsi:
        signal_value = "BEARISH"
        reason = f"Overbought and falling {current_rsi:.1f} (above {overbought_level:g})"
    elif current_rsi < oversold_level:
        signal_value = "NEUTRAL"
        reason = f"Oversold but not rising {current_rsi:.1f}"
    elif current_rsi > overbought_level:
        signal_value = "NEUTRAL"
        reason = f"Overbought but not falling {current_rsi:.1f}"
    else:
        signal_value = "NEUTRAL"
        reason = f"Inside RSI range {current_rsi:.1f}"
    return {"signal": signal_value, "reason": reason, "weight": 1}


def _generate_macd_signal(
    histogram,
    previous_histogram,
    fast_period=12,
    slow_period=26,
    signal_period=9,
):
    """Create one MACD vote from histogram sign with accurate state text."""
    fast, slow, signal = _macd_periods(
        fast=fast_period,
        slow=slow_period,
        signal=signal_period,
    )
    if fast == slow:
        return {
            "signal": "NEUTRAL",
            "reason": "Choose different MACD fast and slow EMA periods",
            "weight": 1,
        }
    if not np.isfinite(histogram):
        required_candles = slow + signal - 1
        return {
            "signal": "NEUTRAL",
            "reason": f"MACD warming up; awaiting {required_candles} completed candles",
            "weight": 1,
        }

    previous_ready = np.isfinite(previous_histogram)
    crossed_up = previous_ready and histogram > 0 and previous_histogram <= 0
    crossed_down = previous_ready and histogram < 0 and previous_histogram >= 0
    if histogram > 0:
        reason = (
            "Bullish MACD crossover: histogram crossed above zero"
            if crossed_up
            else f"Positive MACD histogram {histogram:.4f}"
        )
        return {"signal": "BULLISH", "reason": reason, "weight": 1}
    if histogram < 0:
        reason = (
            "Bearish MACD crossover: histogram crossed below zero"
            if crossed_down
            else f"Negative MACD histogram {histogram:.4f}"
        )
        return {"signal": "BEARISH", "reason": reason, "weight": 1}
    return {
        "signal": "NEUTRAL",
        "reason": "MACD histogram is at zero",
        "weight": 1,
    }


def _generate_vwap_signal(price, prev_close, vwap, prev_vwap, bull_candle, label):
    """Price versus the volume-weighted line, treated the same way on both sides.

    Above the line is bullish and below it is bearish; the reason text says whether price just crossed, is bouncing
    off the line (within 1.5%) or is simply holding its side. A missing line is neutral."""
    if not (np.isfinite(price) and np.isfinite(vwap)) or vwap <= 0:
        return {"signal": "NEUTRAL", "reason": f"{label} unavailable", "weight": 1}
    is_near = abs(price - vwap) / vwap < 0.015
    if price >= vwap:
        crossed = prev_close < prev_vwap
        bounced = is_near and bull_candle
        reason = (
            f"{label} breakout ({vwap:.1f})" if crossed
            else f"{label} bounce ({vwap:.1f})" if bounced
            else f"Holding above {label} ({vwap:.1f})"
        )
        return {"signal": "BULLISH", "reason": reason, "weight": 1}
    crossed = prev_close >= prev_vwap
    rejected = is_near and not bull_candle
    reason = (
        f"{label} breakdown ({vwap:.1f})" if crossed
        else f"{label} rejection ({vwap:.1f})" if rejected
        else f"Below {label} ({vwap:.1f})"
    )
    return {"signal": "BEARISH", "reason": reason, "weight": 1}


def _bollinger_vote(price, lower, upper, param):
    """Band-position vote. 'reversion' (default): outer ``BOLL_ZONE_PCT`` % of the band width is bullish at the
    bottom and bearish at the top. 'breakout': a close beyond the band follows the move."""
    if not (np.isfinite(price) and np.isfinite(lower) and np.isfinite(upper)) or upper <= lower:
        return {"signal": "NEUTRAL", "reason": "Bollinger unavailable", "weight": 1}
    width = upper - lower
    if str(param("BOLL_VOTE_MODE", "reversion")).lower() == "breakout":
        if price > upper:
            return {"signal": "BULLISH", "reason": f"Breakout above {upper:.1f}", "weight": 1}
        if price < lower:
            return {"signal": "BEARISH", "reason": f"Breakdown below {lower:.1f}", "weight": 1}
        return {"signal": "NEUTRAL", "reason": "In Channel", "weight": 1}
    zone = min(0.5, max(0.01, float(param("BOLL_ZONE_PCT", 20.0) or 20.0) / 100.0))
    if price <= lower + width * zone:
        return {"signal": "BULLISH", "reason": f"Near Low {lower:.1f}", "weight": 1}
    if price >= upper - width * zone:
        return {"signal": "BEARISH", "reason": f"Near High {upper:.1f}", "weight": 1}
    return {"signal": "NEUTRAL", "reason": "In Channel", "weight": 1}


def _generate_moving_average_signal(
    *,
    average_type,
    price,
    primary_value,
    previous_primary,
    primary_period,
    use_second_line,
    secondary_value=np.nan,
    previous_secondary=np.nan,
    secondary_period=None,
    separation_pct=0.0,
):
    """Return one vote for either price-vs-line or fast-vs-slow MA mode.

    ``separation_pct`` (two-line mode only) keeps the vote neutral while the lines are closer than that percent of
    the slow line, which stops the state vote from whipsawing in ranges. 0 keeps the plain alignment vote."""
    average_type = str(average_type or "MA").upper()
    primary_period = _indicator_period(primary_period, 1, maximum=500)
    primary_ready = np.isfinite(primary_value)

    if not use_second_line:
        if not primary_ready:
            return {
                "signal": "NEUTRAL",
                "reason": f"{average_type} {primary_period} warming up; awaiting enough completed candles",
                "weight": 1,
                "line_mode": 1,
            }
        return {
            "signal": "BULLISH" if price >= primary_value else "BEARISH",
            "reason": (
                f"Price above {average_type} {primary_period} ({primary_value:.2f})"
                if price >= primary_value
                else f"Price below {average_type} {primary_period} ({primary_value:.2f})"
            ),
            "weight": 1,
            "line_mode": 1,
        }

    secondary_period = _indicator_period(secondary_period, primary_period, maximum=500)
    if primary_period == secondary_period:
        return {
            "signal": "NEUTRAL",
            "reason": f"Choose different {average_type} periods for two-line crossover mode",
            "weight": 1,
            "line_mode": 2,
        }
    if not primary_ready or not np.isfinite(secondary_value):
        required_period = max(primary_period, secondary_period)
        return {
            "signal": "NEUTRAL",
            "reason": f"{average_type} crossover warming up; awaiting {required_period} completed candles",
            "weight": 1,
            "line_mode": 2,
        }

    if primary_period < secondary_period:
        fast_value, fast_previous, fast_period = primary_value, previous_primary, primary_period
        slow_value, slow_previous, slow_period = secondary_value, previous_secondary, secondary_period
    else:
        fast_value, fast_previous, fast_period = secondary_value, previous_secondary, secondary_period
        slow_value, slow_previous, slow_period = primary_value, previous_primary, primary_period

    previous_ready = np.isfinite(fast_previous) and np.isfinite(slow_previous)
    crossed_up = previous_ready and fast_value > slow_value and fast_previous <= slow_previous
    crossed_down = previous_ready and fast_value < slow_value and fast_previous >= slow_previous
    if separation_pct and slow_value and abs(fast_value - slow_value) / abs(slow_value) * 100.0 < separation_pct:
        return {
            "signal": "NEUTRAL",
            "reason": f"{average_type} {fast_period}/{slow_period} within {separation_pct:g}% buffer",
            "weight": 1,
            "line_mode": 2,
        }
    if fast_value > slow_value:
        return {
            "signal": "BULLISH",
            "reason": (
                f"Bullish {average_type} crossover: {fast_period} crossed above {slow_period}"
                if crossed_up
                else f"Bullish {average_type} alignment: {fast_period} above {slow_period}"
            ),
            "weight": 1,
            "line_mode": 2,
        }
    if fast_value < slow_value:
        return {
            "signal": "BEARISH",
            "reason": (
                f"Bearish {average_type} crossover: {fast_period} crossed below {slow_period}"
                if crossed_down
                else f"Bearish {average_type} alignment: {fast_period} below {slow_period}"
            ),
            "weight": 1,
            "line_mode": 2,
        }
    return {
        "signal": "NEUTRAL",
        "reason": f"{average_type} {fast_period} and {slow_period} are equal",
        "weight": 1,
        "line_mode": 2,
    }


def _generate_signals(latest, prev, settings=None, recent_bars=None, chart_latest=None):
    """
    Read indicator values from a single bar and return signal verdicts.
    """
    def _param(key, fallback=None):
        return _resolve_param(key, fallback, settings=settings)

    try:
        enabled = None
        if isinstance(settings, dict) and isinstance(settings.get("enabled_indicators"), (list, tuple, set)):
            enabled = {str(v) for v in settings.get("enabled_indicators") if v}
        if enabled is not None and not enabled:
            return {}

        if enabled and enabled.issubset({"RSI", "Bollinger", "VWAP", "Auto-AVWAP"}):
            rsi_val = float(latest["RSI"])
            prev_rsi = float(prev.get("RSI", rsi_val))
            price = float(latest["Close"])
            boll_lo = float(latest["BOLL_Lower"])
            boll_hi = float(latest["BOLL_Upper"])
            vwap = float(latest["VWAP"])
            prev_vwap = float(prev.get("VWAP", vwap))
            auto_avwap = float(latest.get("Auto_AVWAP", vwap))
            prev_auto_avwap = float(prev.get("Auto_AVWAP", auto_avwap))
            bull_candle = bool(latest.get("cdl_bullish", False))
            prev_close = float(prev.get("Close", price))
            rsi_os = float(_param("RSI_OVERSOLD", 30))
            rsi_ob = float(_param("RSI_OVERBOUGHT", 70))
            rsi_period = _indicator_period(_param("RSI_PERIOD", 14), 14, minimum=2, maximum=500)

            signals = {}
            if "RSI" in enabled:
                signals["RSI"] = _generate_rsi_signal(
                    rsi_val,
                    prev_rsi,
                    oversold=rsi_os,
                    overbought=rsi_ob,
                    period=rsi_period,
                )
            if "Bollinger" in enabled:
                signals["Bollinger"] = _bollinger_vote(price, boll_lo, boll_hi, _param)
            if "VWAP" in enabled:
                signals["VWAP"] = _generate_vwap_signal(
                    price, prev_close, vwap, prev_vwap, bull_candle, "Session VWAP"
                )
            if "Auto-AVWAP" in enabled:
                signals["Auto-AVWAP"] = _generate_vwap_signal(
                    price, prev_close, auto_avwap, prev_auto_avwap, bull_candle, "Auto-AVWAP"
                )
            return signals

        rsi_val = float(latest["RSI"])
        prev_rsi = float(prev.get("RSI", rsi_val))
        macd_h = float(latest["MACD_Hist"])
        ema_f = float(latest["EMA_Fast"])
        ema_s = float(latest["EMA_Slow"])
        prev_ema_f = float(prev.get("EMA_Fast", ema_f))
        prev_ema_s = float(prev.get("EMA_Slow", ema_s))
        ema_primary_period = _indicator_period(_param("EMA_FAST", 9), 9, maximum=500)
        ema_secondary_period = _indicator_period(_param("EMA_SLOW", 21), 21, maximum=500)
        ema_use_second_line = _setting_enabled(_param("EMA_USE_SECOND_LINE", None), True)
        st_bull = bool(latest["Supertrend_Trend"])
        price = float(latest["Close"])
        boll_lo = float(latest["BOLL_Lower"])
        boll_hi = float(latest["BOLL_Upper"])
        bull_p = int(latest["Bull_Pulse"])
        bear_p = int(latest["Bear_Pulse"])

        # Smart AVWAP Dynamics
        vwap = float(latest["VWAP"])
        prev_vwap = float(prev.get("VWAP", vwap))
        bull_candle = bool(latest.get("cdl_bullish", False))
        prev_close = float(prev.get("Close", price))
        auto_avwap = float(latest.get("Auto_AVWAP", vwap))
        prev_auto_avwap = float(prev.get("Auto_AVWAP", auto_avwap))

        try:
            sma_val = float(latest.get("SMA", np.nan))
        except (TypeError, ValueError):
            sma_val = np.nan
        try:
            prev_sma_val = float(prev.get("SMA", sma_val))
        except (TypeError, ValueError):
            prev_sma_val = np.nan
        try:
            sma_secondary_val = float(latest.get("SMA_Secondary", np.nan))
        except (TypeError, ValueError):
            sma_secondary_val = np.nan
        try:
            prev_sma_secondary_val = float(prev.get("SMA_Secondary", sma_secondary_val))
        except (TypeError, ValueError):
            prev_sma_secondary_val = np.nan
        sma_period = _indicator_period(_param("SMA_PERIOD", 200), 200, maximum=500)
        sma_secondary_period = _indicator_period(_param("SMA_SECOND_PERIOD", 50), 50, maximum=500)
        sma_use_second_line = _setting_enabled(_param("SMA_USE_SECOND_LINE", None), True)
        dhl_high = float(latest.get("Daily_HighLow_High", np.nan))
        dhl_low = float(latest.get("Daily_HighLow_Low", np.nan))
        dhl_buffer_pct = max(0.0, float(_param("DAILY_HIGH_LOW_BUFFER_PCT", 0.0)))
        dhl_ready = np.isfinite(dhl_high) and np.isfinite(dhl_low) and dhl_high > dhl_low > 0
        if dhl_ready:
            dhl_high_trigger = dhl_high * (1.0 + dhl_buffer_pct / 100.0)
            dhl_low_trigger = dhl_low * (1.0 - dhl_buffer_pct / 100.0)
            # The calculation columns already hold the final triggers (percent and ATR buffers); prefer them so the
            # vote and the chart lines can never disagree.
            stored_high = float(latest.get("Daily_HighLow_High_Trigger", np.nan))
            stored_low = float(latest.get("Daily_HighLow_Low_Trigger", np.nan))
            if np.isfinite(stored_high) and np.isfinite(stored_low):
                dhl_high_trigger, dhl_low_trigger = stored_high, stored_low
                dhl_buffer_pct = dhl_buffer_pct or max(
                    (stored_high / dhl_high - 1.0) * 100.0, (1.0 - stored_low / dhl_low) * 100.0
                )
            dhl_bull = price > dhl_high_trigger and prev_close <= dhl_high_trigger
            dhl_bear = price < dhl_low_trigger and prev_close >= dhl_low_trigger
            dhl_buffer_note = f" + {dhl_buffer_pct:.2f}% buffer" if dhl_buffer_pct else ""
            if dhl_bull:
                dhl_signal = "BULLISH"
                dhl_reason = f"Prior high breakout above {dhl_high_trigger:.2f}{dhl_buffer_note}"
            elif dhl_bear:
                dhl_signal = "BEARISH"
                dhl_reason = f"Prior low breakdown below {dhl_low_trigger:.2f}{dhl_buffer_note}"
            else:
                dhl_signal = "NEUTRAL"
                if price > dhl_high_trigger:
                    dhl_reason = f"Above prior high trigger {dhl_high_trigger:.2f}; breakout occurred on an earlier candle"
                elif price < dhl_low_trigger:
                    dhl_reason = f"Below prior low trigger {dhl_low_trigger:.2f}; breakdown occurred on an earlier candle"
                elif dhl_buffer_pct:
                    dhl_reason = f"Inside buffered trigger range H {dhl_high_trigger:.2f} / L {dhl_low_trigger:.2f} (PDH {dhl_high:.2f} / PDL {dhl_low:.2f})"
                else:
                    dhl_reason = f"Inside prior range H {dhl_high:.2f} / L {dhl_low:.2f}"
        else:
            dhl_signal = "NEUTRAL"
            dhl_reason = "Awaiting prior session high/low"

        pivot_p = float(latest.get("Pivot_P", np.nan))
        pivot_r1 = float(latest.get("Pivot_R1", np.nan))
        pivot_s1 = float(latest.get("Pivot_S1", np.nan))
        pivot_r2 = float(latest.get("Pivot_R2", np.nan))
        pivot_s2 = float(latest.get("Pivot_S2", np.nan))
        pivot_r3 = float(latest.get("Pivot_R3", np.nan))
        pivot_s3 = float(latest.get("Pivot_S3", np.nan))
        pivot_r4 = float(latest.get("Pivot_R4", np.nan))
        pivot_s4 = float(latest.get("Pivot_S4", np.nan))
        pivot_r5 = float(latest.get("Pivot_R5", np.nan))
        pivot_s5 = float(latest.get("Pivot_S5", np.nan))
        pivot_level_count = min(5, max(1, int(float(_param("PIVOT_LEVEL_COUNT", 1)))))
        pivot_tolerance_pct = max(0.0, float(_param("PIVOT_TOLERANCE_PCT", 0.0)))
        pivot_ready = np.isfinite(pivot_p) and np.isfinite(pivot_r1) and np.isfinite(pivot_s1) and pivot_r1 > pivot_p > pivot_s1
        if pivot_ready:
            pivot_tolerance = max(price * (pivot_tolerance_pct / 100.0), 0.0)
            low_now = float(latest.get("Low", price))
            high_now = float(latest.get("High", price))
            pivot_bull = ((low_now <= pivot_s1 + pivot_tolerance) and price > pivot_s1) or (price > pivot_p and prev_close <= pivot_p)
            pivot_bear = ((high_now >= pivot_r1 - pivot_tolerance) and price < pivot_r1) or (price < pivot_p and prev_close >= pivot_p)
            if pivot_bull:
                pivot_signal = "BULLISH"
                pivot_reason = f"Pivot support/reclaim confirmed | P {pivot_p:.2f}, S1 {pivot_s1:.2f}, R1 {pivot_r1:.2f}, tolerance {pivot_tolerance_pct:.2f}%"
            elif pivot_bear:
                pivot_signal = "BEARISH"
                pivot_reason = f"Pivot resistance/rejection confirmed | P {pivot_p:.2f}, S1 {pivot_s1:.2f}, R1 {pivot_r1:.2f}, tolerance {pivot_tolerance_pct:.2f}%"
            else:
                pivot_signal = "NEUTRAL"
                extra_levels = f", S{pivot_level_count}/R{pivot_level_count} active" if pivot_level_count > 1 else ""
                pivot_reason = f"Between pivot S/R levels | P {pivot_p:.2f}, S1 {pivot_s1:.2f}, R1 {pivot_r1:.2f}{extra_levels}"
        else:
            pivot_signal = "NEUTRAL"
            pivot_reason = "Awaiting prior session pivot levels"

        vp_poc = float(latest.get("Volume_Profile_POC", np.nan))
        vp_vah = float(latest.get("Volume_Profile_VAH", np.nan))
        vp_val = float(latest.get("Volume_Profile_VAL", np.nan))
        vp_tolerance_pct = max(0.0, float(_param("VOLUME_PROFILE_TOLERANCE_PCT", 0.15)))
        vp_confirm = max(1, min(5, int(float(_param("VOLUME_PROFILE_CONFIRMATION_CANDLES", 1)))))
        vp_ready = np.isfinite(vp_poc) and np.isfinite(vp_vah) and np.isfinite(vp_val) and vp_vah >= vp_poc >= vp_val > 0
        if vp_ready:
            vp_tol = max(price * (vp_tolerance_pct / 100.0), 0.0)
            low_now = float(latest.get("Low", price))
            high_now = float(latest.get("High", price))
            recent_closes = []
            if recent_bars is not None and len(recent_bars) > 0:
                try:
                    recent_closes = [float(v) for v in recent_bars["Close"].tail(vp_confirm).tolist()]
                except Exception:
                    recent_closes = []
            if len(recent_closes) < vp_confirm:
                recent_closes = [price]
            accepted_above_value = len(recent_closes) >= vp_confirm and all(c > vp_vah + vp_tol for c in recent_closes[-vp_confirm:])
            accepted_below_value = len(recent_closes) >= vp_confirm and all(c < vp_val - vp_tol for c in recent_closes[-vp_confirm:])
            poc_reclaim = price > vp_poc and prev_close <= vp_poc
            poc_loss = price < vp_poc and prev_close >= vp_poc
            val_bounce = low_now <= vp_val + vp_tol and price > vp_val
            vah_rejection = high_now >= vp_vah - vp_tol and price < vp_vah

            if accepted_above_value:
                vp_signal = "BULLISH"
                vp_reason = f"Accepted above VAH {vp_vah:.2f} after {vp_confirm} candle(s) | POC {vp_poc:.2f}"
            elif accepted_below_value:
                vp_signal = "BEARISH"
                vp_reason = f"Accepted below VAL {vp_val:.2f} after {vp_confirm} candle(s) | POC {vp_poc:.2f}"
            elif poc_reclaim:
                vp_signal = "BULLISH"
                vp_reason = f"POC reclaim above {vp_poc:.2f} | VAH {vp_vah:.2f} / VAL {vp_val:.2f}"
            elif poc_loss:
                vp_signal = "BEARISH"
                vp_reason = f"POC loss below {vp_poc:.2f} | VAH {vp_vah:.2f} / VAL {vp_val:.2f}"
            elif val_bounce:
                vp_signal = "BULLISH"
                vp_reason = f"VAL support bounce near {vp_val:.2f} | POC {vp_poc:.2f}"
            elif vah_rejection:
                vp_signal = "BEARISH"
                vp_reason = f"VAH rejection near {vp_vah:.2f} | POC {vp_poc:.2f}"
            else:
                vp_signal = "NEUTRAL"
                vp_reason = f"Inside value area | VAL {vp_val:.2f} / POC {vp_poc:.2f} / VAH {vp_vah:.2f}"
        else:
            vp_signal = "NEUTRAL"
            vp_reason = "Awaiting prior-session volume profile"

        swing_support = float(latest.get("Swing_Support_1", np.nan))
        swing_resistance = float(latest.get("Swing_Resistance_1", np.nan))
        swing_level_count = max(1, min(5, int(float(_param("SWING_POINTS_LEVEL_COUNT", 1)))))
        swing_mode = str(_param("SWING_POINTS_ENTRY_MODE", "bounce_retest") or "bounce_retest")
        swing_lookback = max(1, min(365, int(float(_param("SWING_POINTS_LOOKBACK_DAYS", 20)))))
        swing_ready = np.isfinite(swing_support) or np.isfinite(swing_resistance)
        if bool(latest.get("Swing_Bullish", False)):
            swing_signal = "BULLISH"
            swing_reason = f"Swing support/reclaim confirmed | Support {swing_support:.2f}" if np.isfinite(swing_support) else "Swing bullish structure confirmed"
        elif bool(latest.get("Swing_Bearish", False)):
            swing_signal = "BEARISH"
            swing_reason = f"Swing resistance/rejection confirmed | Resistance {swing_resistance:.2f}" if np.isfinite(swing_resistance) else "Swing bearish structure confirmed"
        elif swing_ready:
            parts = []
            if np.isfinite(swing_support):
                parts.append(f"Support {swing_support:.2f}")
            if np.isfinite(swing_resistance):
                parts.append(f"Resistance {swing_resistance:.2f}")
            swing_signal = "NEUTRAL"
            swing_reason = f"Watching swing zones | {' / '.join(parts)} | {swing_lookback}D {swing_mode.replace('_', ' ')}"
        else:
            swing_signal = "NEUTRAL"
            swing_reason = "Awaiting swing support/resistance levels"

        adx_val = float(latest.get("ADX", 0))
        di_plus = float(latest.get("DI_Plus", 0))
        di_minus = float(latest.get("DI_Minus", 0))

        prev_adx = float(prev.get("ADX", adx_val))
        try:
            adx_threshold = min(100.0, max(0.0, float(_param("ADX_TRENDING_THRESHOLD", 25))))
        except (TypeError, ValueError):
            adx_threshold = 25.0
        adx_ready = all(np.isfinite(value) for value in (adx_val, di_plus, di_minus))
        is_trending = adx_ready and adx_val >= adx_threshold

        # ADX Trend signal generator
        if not adx_ready:
            adx_signal = "NEUTRAL"
            adx_reason = "ADX warming up; awaiting enough completed candles"
        elif is_trending:
            strength_change = adx_val - prev_adx if np.isfinite(prev_adx) else 0.0
            strength_state = "strengthening" if strength_change > 0 else "weakening" if strength_change < 0 else "steady"
            if di_plus > di_minus:
                adx_signal = "BULLISH"
                adx_reason = f"Strong bullish trend: ADX {adx_val:.1f} ({strength_state}), +DI {di_plus:.1f} > -DI {di_minus:.1f}"
            elif di_minus > di_plus:
                adx_signal = "BEARISH"
                adx_reason = f"Strong bearish trend: ADX {adx_val:.1f} ({strength_state}), -DI {di_minus:.1f} > +DI {di_plus:.1f}"
            else:
                adx_signal = "NEUTRAL"
                adx_reason = f"Strong trend but no DI direction: ADX {adx_val:.1f}"
        else:
            adx_signal = "NEUTRAL"
            directional_hint = "+DI above -DI" if di_plus > di_minus else "-DI above +DI" if di_minus > di_plus else "DI lines equal"
            adx_reason = f"Weak/ranging: ADX {adx_val:.1f} below {adx_threshold:.1f} ({directional_hint})"

        ema_vote = _generate_moving_average_signal(
            average_type="EMA",
            price=price,
            primary_value=ema_f,
            previous_primary=prev_ema_f,
            primary_period=ema_primary_period,
            use_second_line=ema_use_second_line,
            secondary_value=ema_s,
            previous_secondary=prev_ema_s,
            secondary_period=ema_secondary_period,
            separation_pct=max(0.0, float(_param("EMA_CROSS_BUFFER_PCT", 0.0) or 0.0)),
        )
        sma_vote = _generate_moving_average_signal(
            average_type="SMA",
            price=price,
            primary_value=sma_val,
            previous_primary=prev_sma_val,
            primary_period=sma_period,
            use_second_line=sma_use_second_line,
            secondary_value=sma_secondary_val,
            previous_secondary=prev_sma_secondary_val,
            secondary_period=sma_secondary_period,
        )

        # Check Candle Patterns on the latest/new candle (analyzed using previous 5 bars during formula calculation)
        cdl_bar = chart_latest if chart_latest is not None else latest
        p_name = str(cdl_bar.get("cdl_name", "Neutral"))
        is_bull = bool(cdl_bar.get("cdl_bullish", False))
        is_bear = bool(cdl_bar.get("cdl_bearish", False))
        if (is_bull or is_bear) and p_name not in ("Neutral", "None", ""):
            cdl_signal = "BULLISH" if is_bull else "BEARISH"
            cdl_reason = f"Pattern: {p_name}"
        else:
            cdl_signal = "NEUTRAL"
            cdl_reason = f"Pattern: {p_name if p_name not in ('Neutral', '', 'None') else 'None'}"

        cp_is_bull = bool(latest.get("CP_Bullish", False))
        cp_is_bear = bool(latest.get("CP_Bearish", False))
        cp_name = str(latest.get("CP_Name", "Neutral") or "Neutral")
        cp_quality = float(latest.get("CP_Quality", 0) or 0)
        cp_signal = "BULLISH" if cp_is_bull and not cp_is_bear else "BEARISH" if cp_is_bear and not cp_is_bull else "NEUTRAL"

        rsi_os = float(_param("RSI_OVERSOLD", 30))
        rsi_ob = float(_param("RSI_OVERBOUGHT", 70))
        rsi_period = _indicator_period(_param("RSI_PERIOD", 14), 14, minimum=2, maximum=500)
        macd_fast_period, macd_slow_period, macd_signal_period = _macd_periods(
            fast=_param("MACD_FAST", 12),
            slow=_param("MACD_SLOW", 26),
            signal=_param("MACD_SIGNAL", 9),
        )
        bb5_direction = int(latest.get("BB5_Signal", 0) or 0)
        bb5_state = str(latest.get("BB5_State", "NONE") or "NONE")
        bb5_pattern = str(
            latest.get("BB5_Signal_Pattern", latest.get("BB5_Pattern", "NONE")) or "NONE"
        )
        if bb5_pattern == "NONE":
            bb5_pattern = str(latest.get("BB5_Pattern", "NONE") or "NONE")
        bb5_confidence = float(
            latest.get("BB5_Signal_Confidence", latest.get("BB5_Confidence", 0)) or 0
        )
        bb5_pattern_label = bb5_pattern.replace("_", " ").title()
        if bb5_direction:
            bb5_reason = (
                f"{bb5_pattern_label} breakout confirmed "
                f"({bb5_confidence:.0f}% setup quality)"
            )
        elif bb5_state == "FORMING" and bb5_pattern != "NONE":
            bb5_reason = (
                f"{bb5_pattern_label} forming; awaiting completed-candle breakout "
                f"({bb5_confidence:.0f}% setup quality)"
            )
        elif bb5_state == "FAILED":
            bb5_reason = f"{bb5_pattern_label} invalidated; no active vote"
        else:
            bb5_reason = "No qualified pattern on the latest completed candle"
        if cp_signal != "NEUTRAL":
            cp_reason = f"Pattern: {cp_name} breakout confirmed ({cp_quality:.0f}% setup quality)"
        elif bb5_state == "FORMING" and bb5_pattern != "NONE":
            cp_reason = f"Pattern: {bb5_pattern_label} forming; awaiting completed-candle breakout ({bb5_confidence:.0f}% setup quality)"
        else:
            cp_reason = "Pattern: None"
        signals = {
            "RSI": _generate_rsi_signal(
                rsi_val,
                prev_rsi,
                oversold=rsi_os,
                overbought=rsi_ob,
                period=rsi_period,
            ),
            "MACD": _generate_macd_signal(
                macd_h,
                float(prev.get("MACD_Hist", np.nan)),
                fast_period=macd_fast_period,
                slow_period=macd_slow_period,
                signal_period=macd_signal_period,
            ),
            "EMA Cross": ema_vote,
            "ADX Trend": {
                "signal": adx_signal,
                "reason": adx_reason,
                "weight": 1,
            },
            "Supertrend": {
                "signal": "BULLISH" if st_bull else "BEARISH",
                "reason": f"{'Above' if st_bull else 'Below'} {float(latest['Supertrend']):.1f}",
                "weight": 1,
            },
            "Bollinger": _bollinger_vote(price, boll_lo, boll_hi, _param),
            "VWAP": _generate_vwap_signal(
                price, prev_close, vwap, prev_vwap, bull_candle, "Session VWAP"
            ),
            "Auto-AVWAP": _generate_vwap_signal(
                price, prev_close, auto_avwap, prev_auto_avwap, bull_candle, "Auto-AVWAP"
            ),
            "SMA": sma_vote,
            "Prior Day High/Low": {
                "signal": dhl_signal,
                "reason": dhl_reason,
                "weight": 1,
                "levels": {
                    "high": dhl_high if dhl_ready else None,
                    "low": dhl_low if dhl_ready else None,
                    "high_trigger": dhl_high_trigger if dhl_ready else None,
                    "low_trigger": dhl_low_trigger if dhl_ready else None,
                    "buffer_pct": dhl_buffer_pct if dhl_ready else None,
                },
            },
            "Pivot Points (S/R)": {
                "signal": pivot_signal,
                "reason": pivot_reason,
                "weight": 1,
                "levels": {
                    "pivot": pivot_p if pivot_ready else None,
                    "r1": pivot_r1 if pivot_ready else None,
                    "s1": pivot_s1 if pivot_ready else None,
                    "r2": pivot_r2 if pivot_ready and pivot_level_count >= 2 and np.isfinite(pivot_r2) else None,
                    "s2": pivot_s2 if pivot_ready and pivot_level_count >= 2 and np.isfinite(pivot_s2) else None,
                    "r3": pivot_r3 if pivot_ready and pivot_level_count >= 3 and np.isfinite(pivot_r3) else None,
                    "s3": pivot_s3 if pivot_ready and pivot_level_count >= 3 and np.isfinite(pivot_s3) else None,
                    "r4": pivot_r4 if pivot_ready and pivot_level_count >= 4 and np.isfinite(pivot_r4) else None,
                    "s4": pivot_s4 if pivot_ready and pivot_level_count >= 4 and np.isfinite(pivot_s4) else None,
                    "r5": pivot_r5 if pivot_ready and pivot_level_count >= 5 and np.isfinite(pivot_r5) else None,
                    "s5": pivot_s5 if pivot_ready and pivot_level_count >= 5 and np.isfinite(pivot_s5) else None,
                    "level_count": pivot_level_count if pivot_ready else None,
                    "tolerance_pct": pivot_tolerance_pct if pivot_ready else None,
                },
            },
            "Volume Profile": {
                "signal": vp_signal,
                "reason": vp_reason,
                "weight": 1,
                "levels": {
                    "poc": vp_poc if vp_ready else None,
                    "vah": vp_vah if vp_ready else None,
                    "val": vp_val if vp_ready else None,
                    "value_area_pct": float(_param("VOLUME_PROFILE_VALUE_AREA_PCT", 70.0)) if vp_ready else None,
                    "tolerance_pct": vp_tolerance_pct if vp_ready else None,
                    "confirmation_candles": vp_confirm if vp_ready else None,
                },
            },
            "Swing Points": {
                "signal": swing_signal,
                "reason": swing_reason,
                "weight": 1,
                "levels": {
                    "support_1": float(latest.get("Swing_Support_1", np.nan)) if np.isfinite(float(latest.get("Swing_Support_1", np.nan))) else None,
                    "support_2": float(latest.get("Swing_Support_2", np.nan)) if swing_level_count >= 2 and np.isfinite(float(latest.get("Swing_Support_2", np.nan))) else None,
                    "support_3": float(latest.get("Swing_Support_3", np.nan)) if swing_level_count >= 3 and np.isfinite(float(latest.get("Swing_Support_3", np.nan))) else None,
                    "support_4": float(latest.get("Swing_Support_4", np.nan)) if swing_level_count >= 4 and np.isfinite(float(latest.get("Swing_Support_4", np.nan))) else None,
                    "support_5": float(latest.get("Swing_Support_5", np.nan)) if swing_level_count >= 5 and np.isfinite(float(latest.get("Swing_Support_5", np.nan))) else None,
                    "resistance_1": float(latest.get("Swing_Resistance_1", np.nan)) if np.isfinite(float(latest.get("Swing_Resistance_1", np.nan))) else None,
                    "resistance_2": float(latest.get("Swing_Resistance_2", np.nan)) if swing_level_count >= 2 and np.isfinite(float(latest.get("Swing_Resistance_2", np.nan))) else None,
                    "resistance_3": float(latest.get("Swing_Resistance_3", np.nan)) if swing_level_count >= 3 and np.isfinite(float(latest.get("Swing_Resistance_3", np.nan))) else None,
                    "resistance_4": float(latest.get("Swing_Resistance_4", np.nan)) if swing_level_count >= 4 and np.isfinite(float(latest.get("Swing_Resistance_4", np.nan))) else None,
                    "resistance_5": float(latest.get("Swing_Resistance_5", np.nan)) if swing_level_count >= 5 and np.isfinite(float(latest.get("Swing_Resistance_5", np.nan))) else None,
                    "level_count": swing_level_count if swing_ready else None,
                    "lookback_days": swing_lookback if swing_ready else None,
                    "entry_mode": swing_mode if swing_ready else None,
                    "settings_source": _bot_swing_settings_source(settings) if swing_ready else None,
                },
            },
            "Mystic Pulse": {
                "signal": (
                    "BULLISH" if bull_p >= float(_param("MYSTIC_PULSE_THRESHOLD", 5))
                    else "BEARISH" if bear_p >= float(_param("MYSTIC_PULSE_THRESHOLD", 5))
                    else "NEUTRAL"
                ),
                "reason": f"Strength {int(bull_p if bull_p > bear_p else bear_p)}/{int(float(_param('MYSTIC_PULSE_THRESHOLD', 5)))}",
                "weight": 1,
            },
            "Candle Patterns": {
                "signal": cdl_signal,
                "reason": cdl_reason,
                "weight": 1,
            },
            # Chart Patterns: flags, pennants and wedges, one vote on the completed candle that confirms the breakout.
            "Chart Patterns": {
                "signal": cp_signal,
                "reason": cp_reason,
                "weight": 1,
                "pattern": cp_name,
                "quality": cp_quality,
            },
        }
        signals.update(_generate_standard_extension_signals(latest, prev, _param))
        if enabled is not None:
            signals = {name: data for name, data in signals.items() if name in enabled}
        return signals

    except Exception:
        return {}
