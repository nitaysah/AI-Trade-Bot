from __future__ import annotations

import argparse
import contextlib
import io
import json
import math
import os
import random
import time
from concurrent.futures import ProcessPoolExecutor, wait, FIRST_COMPLETED
from datetime import datetime, timedelta
from itertools import combinations, product

from backtester import Backtester
from data_manager import get_historical_data
from indicators import calculate_indicators_for_df


TICKER = "SPY"
TIMEFRAME = "15Min"
DAYS = 60
CAPITAL = 1000.0
TRADE_AMOUNT = 1000.0
MARKET_SESSION = "Extended"
MAX_TRADES = 50

ALL_INDICATORS = [
    "RSI",
    "MACD",
    "EMA Cross",
    "ADX Trend",
    "Supertrend",
    "Bollinger",
    "VWAP",
    "SMA",
    "Mystic Pulse",
    "Candle Patterns",
    "Prior Day High/Low",
    "Pivot Points (S/R)",
    "Volume Profile",
    "Swing Points",
    "BotBulls1",
    "BotBulls2",
    "BotBulls3",
]

CURATED_INDICATOR_SETS = [
    ["RSI", "Bollinger", "VWAP"],
    ["RSI", "Bollinger", "VWAP", "Supertrend"],
    ["RSI", "Bollinger", "VWAP", "BotBulls2"],
    ["RSI", "Bollinger", "VWAP", "BotBulls3"],
    ["RSI", "Bollinger", "VWAP", "BotBulls2", "BotBulls3"],
    ["MACD", "EMA Cross", "ADX Trend", "Supertrend", "VWAP"],
    ["EMA Cross", "Supertrend", "SMA", "VWAP"],
    ["Supertrend", "VWAP", "BotBulls2", "BotBulls3"],
    ["Mystic Pulse", "Candle Patterns", "RSI", "VWAP"],
    ["Prior Day High/Low", "Pivot Points (S/R)", "VWAP", "RSI"],
    ["Pivot Points (S/R)", "Volume Profile", "VWAP", "Supertrend"],
    ["Swing Points", "VWAP", "RSI", "Supertrend"],
    ["Swing Points", "Pivot Points (S/R)", "Volume Profile", "VWAP"],
    ["BotBulls1", "BotBulls2", "BotBulls3", "VWAP"],
    ["BotBulls1", "Mystic Pulse", "RSI", "VWAP"],
    ["BotBulls2", "EMA Cross", "Supertrend", "ADX Trend"],
    ["BotBulls3", "Heikin Ashi", "Supertrend"][:2] + ["VWAP"],  # keeps max 3 and avoids unknown HA signal name
]

PARAMETER_PROFILES = [
    {
        "name": "tight_atr",
        "atr_stop_multiplier": 0.8,
        "atr_trail_multiplier": 1.2,
        "atr_take_profit_multiplier": 2.4,
        "regular_stop_loss_pct": 0.012,
        "regular_take_profit_pct": 0.03,
        "regular_trailing_pct": 0.008,
        "entry_atr_multiplier": 0.8,
        "entry_pct_move": 0.6,
        "swing_entry_lookback_days": 5,
        "swing_exit_lookback_days": 5,
        "swing_entry_mode": "bounce_retest",
        "daily_high_low_buffer_pct": 0.03,
    },
    {
        "name": "balanced",
        "atr_stop_multiplier": 1.2,
        "atr_trail_multiplier": 1.8,
        "atr_take_profit_multiplier": 3.2,
        "regular_stop_loss_pct": 0.018,
        "regular_take_profit_pct": 0.045,
        "regular_trailing_pct": 0.012,
        "entry_atr_multiplier": 1.2,
        "entry_pct_move": 1.0,
        "swing_entry_lookback_days": 10,
        "swing_exit_lookback_days": 10,
        "swing_entry_mode": "bounce_retest",
        "daily_high_low_buffer_pct": 0.05,
    },
    {
        "name": "runner",
        "atr_stop_multiplier": 1.6,
        "atr_trail_multiplier": 2.4,
        "atr_take_profit_multiplier": 4.5,
        "regular_stop_loss_pct": 0.025,
        "regular_take_profit_pct": 0.07,
        "regular_trailing_pct": 0.018,
        "entry_atr_multiplier": 1.5,
        "entry_pct_move": 1.5,
        "swing_entry_lookback_days": 20,
        "swing_exit_lookback_days": 20,
        "swing_entry_mode": "breakout_retest",
        "daily_high_low_buffer_pct": 0.08,
    },
]

STRATEGY_FAMILIES = [
    {
        "name": "indicator_dynamic",
        "entry_modes": ["indicator"],
        "exit_modes": ["indicator", "hybrid", "sltp", "percentage", "hybrid_pct"],
    },
    {
        "name": "atr_entry",
        "entry_modes": ["atr", "hybrid_atr"],
        "exit_modes": ["sltp", "hybrid", "trend_rider"],
    },
    {
        "name": "pct_entry",
        "entry_modes": ["percentage", "hybrid_pct"],
        "exit_modes": ["percentage", "hybrid_pct", "sltp", "hybrid"],
    },
    {
        "name": "pivot_sr",
        "entry_modes": ["pivot_points", "hybrid_pivot"],
        "exit_modes": ["pivot_points", "hybrid_pivot", "sltp", "hybrid"],
    },
    {
        "name": "prior_high_low",
        "entry_modes": ["high_low", "hybrid_high_low"],
        "exit_modes": ["high_low", "hybrid_high_low", "sltp", "hybrid"],
    },
    {
        "name": "swing_points",
        "entry_modes": ["swing_points", "hybrid_swing"],
        "exit_modes": ["swing_points", "hybrid_swing", "sltp", "hybrid"],
    },
    {
        "name": "trend_rider",
        "entry_modes": ["indicator"],
        "exit_modes": ["trend_rider"],
    },
]

SMART_REENTRY_MODES = [
    ("off", False, "RSI"),
    ("RSI", True, "RSI"),
    ("VWAP", True, "VWAP"),
]

_WORKER_DF = None
_WORKER_SIGNAL_CACHE = None


def _normalize_indicator_set(indicators):
    cleaned = []
    for indicator in indicators:
        if indicator in ALL_INDICATORS and indicator not in cleaned:
            cleaned.append(indicator)
    return tuple(cleaned[:5])


def build_indicator_sets(seed: int, max_sets: int):
    rng = random.Random(seed)
    indicator_sets = {_normalize_indicator_set(item) for item in CURATED_INDICATOR_SETS}
    indicator_sets = {item for item in indicator_sets if item}

    combo_pool = []
    for size in range(2, 6):
        combo_pool.extend(combinations(ALL_INDICATORS, size))
    rng.shuffle(combo_pool)
    for combo in combo_pool:
        indicator_sets.add(tuple(combo))
        if len(indicator_sets) >= max_sets:
            break

    return sorted(indicator_sets, key=lambda item: (len(item), item))


def fetch_data(ticker: str, timeframe: str, start_date: datetime, end_date: datetime, market_session: str):
    padded_start = start_date - timedelta(days=60)
    raw_df = get_historical_data(ticker, timeframe, padded_start, end_date, force_yahoo=True)
    if raw_df is None or raw_df.empty:
        raise RuntimeError("No historical data returned for grid simulation.")

    result = calculate_indicators_for_df(raw_df, timeframe, ticker, settings={"market_session": market_session})
    if not result or "df" not in result:
        raise RuntimeError("Indicator calculation failed for grid simulation.")

    full_df = result["df"]
    if hasattr(full_df.index, "tz") and full_df.index.tz is not None:
        full_df.index = full_df.index.tz_localize(None)
    active_df = full_df.loc[start_date:end_date]
    if active_df.empty:
        raise RuntimeError("No active bars after slicing 60-day window.")
    return active_df


def _safe_float(row, key, fallback=0.0):
    try:
        value = row.get(key, fallback)
        if value is None:
            return fallback
        value = float(value)
        if math.isnan(value) or math.isinf(value):
            return fallback
        return value
    except Exception:
        return fallback


def _safe_bool(row, key, fallback=False):
    try:
        value = row.get(key, fallback)
        if value is None:
            return fallback
        return bool(value)
    except Exception:
        return fallback


def _signal(signal, reason="", weight=1, premium=False):
    payload = {"signal": signal, "reason": reason, "weight": weight}
    if premium:
        payload["premium"] = True
    return payload


def build_signal_cache(df, needed_signals):
    needed = set(needed_signals)
    cache = [None] * len(df)
    for i in range(30, len(df)):
        current_bar = df.iloc[i]
        prev_bar = df.iloc[i - 1]
        price = _safe_float(current_bar, "Close")
        prev_close = _safe_float(prev_bar, "Close", price)
        signals = {}

        if "RSI" in needed:
            rsi = _safe_float(current_bar, "RSI", 50.0)
            signals["RSI"] = _signal("BULLISH" if rsi < 30 else "BEARISH" if rsi > 70 else "NEUTRAL", f"RSI {rsi:.1f}")

        if "MACD" in needed:
            macd_h = _safe_float(current_bar, "MACD_Hist")
            signals["MACD"] = _signal("BULLISH" if macd_h > 0 else "BEARISH" if macd_h < 0 else "NEUTRAL", f"MACD Hist {macd_h:.2f}")

        adx = _safe_float(current_bar, "ADX")
        di_plus = _safe_float(current_bar, "DI_Plus")
        di_minus = _safe_float(current_bar, "DI_Minus")
        is_trending = adx > 25

        if "ADX Trend" in needed:
            if is_trending and di_plus > di_minus:
                signals["ADX Trend"] = _signal("BULLISH", f"DI+ {di_plus:.1f} > DI- {di_minus:.1f}")
            elif is_trending and di_minus > di_plus:
                signals["ADX Trend"] = _signal("BEARISH", f"DI- {di_minus:.1f} > DI+ {di_minus:.1f}")
            else:
                signals["ADX Trend"] = _signal("NEUTRAL", f"ADX {adx:.1f}")

        if "EMA Cross" in needed:
            ema_fast = _safe_float(current_bar, "EMA_Fast", price)
            ema_slow = _safe_float(current_bar, "EMA_Slow", price)
            signals["EMA Cross"] = _signal(("BULLISH" if ema_fast > ema_slow else "BEARISH") if is_trending else "NEUTRAL", f"{ema_fast:.1f} vs {ema_slow:.1f}")

        if "Supertrend" in needed:
            st_bull = _safe_bool(current_bar, "Supertrend_Trend", True)
            st_level = _safe_float(current_bar, "Supertrend", price)
            signals["Supertrend"] = _signal("BULLISH" if st_bull else "BEARISH", f"{'Above' if st_bull else 'Below'} {st_level:.1f}")

        if "Bollinger" in needed:
            boll_lo = _safe_float(current_bar, "BOLL_Lower", price)
            boll_hi = _safe_float(current_bar, "BOLL_Upper", price)
            channel = max(boll_hi - boll_lo, 0.0)
            signals["Bollinger"] = _signal(
                "BULLISH" if price <= boll_lo + channel * 0.2 else "BEARISH" if price >= boll_hi - channel * 0.2 else "NEUTRAL",
                f"Band {boll_lo:.1f}/{boll_hi:.1f}",
            )

        if "VWAP" in needed:
            vwap = _safe_float(current_bar, "VWAP", price)
            prev_vwap = _safe_float(prev_bar, "VWAP", vwap)
            near_vwap = vwap > 0 and abs(price - vwap) / vwap < 0.015
            bull_candle = _safe_bool(current_bar, "cdl_bullish", False)
            signals["VWAP"] = _signal(
                "BULLISH" if (price >= vwap and prev_close < prev_vwap) or (price >= vwap and near_vwap and bull_candle) else "BEARISH" if price < vwap else "NEUTRAL",
                f"VWAP {vwap:.1f}",
            )

        if "SMA" in needed:
            sma = _safe_float(current_bar, "SMA", price)
            signals["SMA"] = _signal("BULLISH" if price >= sma else "BEARISH", f"SMA {sma:.1f}")

        if "Prior Day High/Low" in needed:
            if _safe_bool(current_bar, "Daily_HighLow_Bullish", False):
                signals["Prior Day High/Low"] = _signal("BULLISH", "PDH reclaim/breakout")
            elif _safe_bool(current_bar, "Daily_HighLow_Bearish", False):
                signals["Prior Day High/Low"] = _signal("BEARISH", "PDL breakdown/rejection")
            else:
                signals["Prior Day High/Low"] = _signal("NEUTRAL", "Watching PDH/PDL")

        if "Pivot Points (S/R)" in needed:
            if _safe_bool(current_bar, "Pivot_Bullish", False):
                signals["Pivot Points (S/R)"] = _signal("BULLISH", "Pivot reclaim/support")
            elif _safe_bool(current_bar, "Pivot_Bearish", False):
                signals["Pivot Points (S/R)"] = _signal("BEARISH", "Pivot rejection/loss")
            else:
                pivot = _safe_float(current_bar, "Pivot_P", float("nan"))
                signals["Pivot Points (S/R)"] = _signal("NEUTRAL", f"Pivot P {pivot:.1f}" if math.isfinite(pivot) else "Awaiting pivot")

        if "Volume Profile" in needed:
            if _safe_bool(current_bar, "Volume_Profile_Bullish", False):
                signals["Volume Profile"] = _signal("BULLISH", "POC/value reclaim")
            elif _safe_bool(current_bar, "Volume_Profile_Bearish", False):
                signals["Volume Profile"] = _signal("BEARISH", "POC/value loss")
            else:
                signals["Volume Profile"] = _signal("NEUTRAL", "Watching value area")

        if "Swing Points" in needed:
            if _safe_bool(current_bar, "Swing_Bullish", False):
                signals["Swing Points"] = _signal("BULLISH", "Swing bullish structure")
            elif _safe_bool(current_bar, "Swing_Bearish", False):
                signals["Swing Points"] = _signal("BEARISH", "Swing bearish structure")
            else:
                signals["Swing Points"] = _signal("NEUTRAL", "Watching swing zones")

        if "Mystic Pulse" in needed:
            bull_p = _safe_float(current_bar, "Bull_Pulse")
            bear_p = _safe_float(current_bar, "Bear_Pulse")
            signals["Mystic Pulse"] = _signal("BULLISH" if bull_p >= 5 else "BEARISH" if bear_p >= 5 else "NEUTRAL", f"Pulse {max(bull_p, bear_p):.0f}/5")

        if "Candle Patterns" in needed:
            if _safe_bool(current_bar, "cdl_bullish", False):
                signals["Candle Patterns"] = _signal("BULLISH", str(current_bar.get("cdl_name", "Pattern")))
            elif _safe_bool(current_bar, "cdl_bearish", False):
                signals["Candle Patterns"] = _signal("BEARISH", str(current_bar.get("cdl_name", "Pattern")))
            else:
                signals["Candle Patterns"] = _signal("NEUTRAL", "Pattern: None")

        if "BotBulls1" in needed:
            signals["BotBulls1"] = _signal(
                "BULLISH" if _safe_bool(current_bar, "BA1_Buy", False) else "BEARISH" if _safe_bool(current_bar, "BA1_Sell", False) else "NEUTRAL",
                "BB1 WaveTrend/MFI",
                premium=True,
            )

        if "BotBulls2" in needed:
            bb2_bull = _safe_bool(current_bar, "LX_SmartTrend", True)
            signals["BotBulls2"] = _signal("BULLISH" if bb2_bull else "BEARISH", "BB2 SmartTrend", premium=True)

        if "BotBulls3" in needed:
            bb3_bull = _safe_bool(current_bar, "UT_Above", True)
            signals["BotBulls3"] = _signal("BULLISH" if bb3_bull else "BEARISH", "BB3 UT Trail", premium=True)

        cache[i] = signals
    return cache


def _init_worker(active_df, signal_cache):
    global _WORKER_DF, _WORKER_SIGNAL_CACHE
    _WORKER_DF = active_df
    _WORKER_SIGNAL_CACHE = signal_cache


def _run_case(params):
    kwargs = dict(params)
    settings = kwargs.pop("settings")
    bt = Backtester(**kwargs)
    with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
        result = bt.run(precomputed_df=_WORKER_DF, precomputed_signal_cache=_WORKER_SIGNAL_CACHE, fast_metrics=True)
    return {
        "settings": settings,
        "summary": result.get("summary", {}),
        "trades": result.get("trades", []),
    }


def _score_result(row, target_roi):
    summary = row.get("summary", {})
    trades = int(summary.get("total_trades", 0) or 0)
    roi = float(summary.get("roi_pct", 0.0) or 0.0)
    profit_factor = float(summary.get("profit_factor", 0.0) or 0.0)
    win_rate = float(summary.get("win_rate_pct", 0.0) or 0.0)
    eligible = 0 < trades <= MAX_TRADES
    target_hit = roi >= target_roi
    return (
        1 if eligible else 0,
        1 if target_hit else 0,
        roi,
        profit_factor if math.isfinite(profit_factor) else 999.0,
        win_rate,
        -trades,
    )


def _compact_trade(trade):
    return {
        "entry_time": trade.get("entry_time"),
        "exit_time": trade.get("exit_time"),
        "side": trade.get("side"),
        "entry_price": trade.get("entry_price"),
        "exit_price": trade.get("exit_price"),
        "pl": trade.get("pl"),
        "pl_pct": trade.get("pl_pct"),
        "reason": trade.get("reason"),
    }


def _mode_cost(mode):
    mode = str(mode or "")
    if "swing" in mode:
        return 5
    if "high_low" in mode or "pivot" in mode:
        return 3
    if "hybrid" in mode:
        return 2
    return 1


def _task_cost(task):
    settings = task["settings"]
    return (
        _mode_cost(settings["entry_mode"]) + _mode_cost(settings["exit_mode"]),
        len(settings["indicators"]),
        settings["strategy_family"],
        settings["entry_mode"],
        settings["exit_mode"],
    )


def build_tasks(args, start_date, end_date):
    rng = random.Random(args.seed)
    indicator_sets = build_indicator_sets(args.seed, args.max_indicator_sets)
    include_families = {
        item.strip()
        for item in str(args.include_families or "all").split(",")
        if item.strip()
    }
    tasks = []
    seen = set()
    threshold_pairs_by_size = {
        1: [(1, 1)],
        2: [(1, 1), (2, 1), (2, 2)],
        3: [(1, 1), (2, 1), (2, 2), (3, 2)],
        4: [(2, 1), (2, 2), (3, 2), (4, 3)],
        5: [(2, 1), (3, 2), (4, 3), (5, 3)],
    }

    for family, indicators, params, smart, signal_reset, cooldown, direction in product(
        STRATEGY_FAMILIES,
        indicator_sets,
        PARAMETER_PROFILES,
        SMART_REENTRY_MODES,
        [True, False],
        [True, False],
        ["long", "both"],
    ):
        if "all" not in include_families and family["name"] not in include_families:
            continue
        smart_label, smart_enabled, smart_type = smart
        threshold_pairs = threshold_pairs_by_size.get(len(indicators), [(1, 1)])
        for entry_mode, exit_mode, threshold_pair in product(family["entry_modes"], family["exit_modes"], threshold_pairs):
            threshold, sell_threshold = threshold_pair
            if "hybrid" in entry_mode and threshold > len(indicators):
                continue
            signature = (
                family["name"],
                indicators,
                params["name"],
                smart_label,
                signal_reset,
                cooldown,
                direction,
                entry_mode,
                exit_mode,
                threshold,
                sell_threshold,
            )
            if signature in seen:
                continue
            seen.add(signature)
            kwargs = {
                "ticker": TICKER,
                "timeframe": TIMEFRAME,
                "start_date": start_date,
                "end_date": end_date,
                "initial_capital": CAPITAL,
                "threshold": threshold,
                "sell_threshold": sell_threshold,
                "enabled_indicators": list(indicators),
                "risk_per_trade": 1.0,
                "max_pos_pct": 1.0,
                "ext_hours": True,
                "buy_mode": entry_mode,
                "custom_buy_price": 0.0,
                "sell_mode": exit_mode,
                "enable_atr_trail": True,
                "ai_autopilot": False,
                "ai_interval": 10,
                "ai_model": "llama-3.3-70b-versatile",
                "enable_cooldown": cooldown,
                "cooldown_minutes": args.cooldown_minutes if cooldown else None,
                "enable_pct_trail": True,
                "cancel_event": None,
                "entry_atr_style": "pullback_reclaim",
                "entry_pct_style": "dip_reclaim",
                "market_session": MARKET_SESSION,
                "allocation_mode": "fixed",
                "trade_direction_mode": direction,
                "trade_amount": TRADE_AMOUNT,
                "custom_stop_loss_price": 0.0,
                "custom_take_profit_price": 0.0,
                "enable_signal_reset": signal_reset,
                "enable_smart_reentry": smart_enabled,
                "smart_reentry_type": smart_type,
                "smart_reentry_rsi_call_max": 70.0,
                "smart_reentry_rsi_put_min": 30.0,
                "smart_reentry_vwap_call_max_dist_pct": 0.5,
                "smart_reentry_vwap_put_max_dist_pct": 0.5,
                "enable_risk": False,
                **{k: v for k, v in params.items() if k != "name"},
            }
            kwargs["settings"] = {
                "strategy_family": family["name"],
                "indicators": list(indicators),
                "indicator_count": len(indicators),
                "parameter_profile": params["name"],
                "entry_mode": entry_mode,
                "exit_mode": exit_mode,
                "threshold": threshold,
                "sell_threshold": sell_threshold,
                "smart_reentry": smart_label,
                "enable_smart_reentry": smart_enabled,
                "enable_signal_reset": signal_reset,
                "enable_cooldown": cooldown,
                "cooldown_minutes": args.cooldown_minutes if cooldown else None,
                "trade_direction_mode": direction,
                "trade_amount": TRADE_AMOUNT,
                "capital": CAPITAL,
                "timeframe": TIMEFRAME,
                "days": DAYS,
                "market_session": MARKET_SESSION,
                **{k: v for k, v in params.items() if k != "name"},
            }
            tasks.append(kwargs)

    if args.task_order == "random":
        rng.shuffle(tasks)
    else:
        rng.shuffle(tasks)
        tasks.sort(key=_task_cost)
    if args.max_combos > 0:
        return tasks[:args.max_combos], len(tasks)
    return tasks, len(tasks)


def write_output(path, payload):
    tmp_path = f"{path}.tmp"
    with open(tmp_path, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=2, default=str)
    os.replace(tmp_path, path)


def main():
    parser = argparse.ArgumentParser(description="Goal runner: SPY 15Min 60-day grid simulation")
    parser.add_argument("--max-combos", type=int, default=1200)
    parser.add_argument("--max-indicator-sets", type=int, default=140)
    parser.add_argument("--workers", type=int, default=max(2, min(6, os.cpu_count() or 4)))
    parser.add_argument("--seed", type=int, default=71517)
    parser.add_argument("--max-hours", type=float, default=7.5)
    parser.add_argument("--progress-every", type=int, default=25)
    parser.add_argument("--cooldown-minutes", type=int, default=60)
    parser.add_argument("--include-families", default="all", help="Comma-separated strategy family names or 'all'.")
    parser.add_argument("--task-order", default="fast_first", choices=["fast_first", "random"])
    parser.add_argument("--output", default="backend/goal_spy_15m_grid_results.json")
    args = parser.parse_args()

    end_date = datetime.now().replace(microsecond=0)
    start_date = end_date - timedelta(days=DAYS)
    print(f"[goal-grid] Loading {TICKER} {TIMEFRAME} data for {DAYS} days ({MARKET_SESSION})...")
    active_df = fetch_data(TICKER, TIMEFRAME, start_date, end_date, MARKET_SESSION)
    print(f"[goal-grid] Loaded {len(active_df)} bars. First={active_df.index[0]} Last={active_df.index[-1]}")

    tasks, full_grid_count = build_tasks(args, start_date, end_date)
    needed = sorted({indicator for task in tasks for indicator in task["enabled_indicators"]})
    print(f"[goal-grid] Full generated grid={full_grid_count:,}; running sampled/bounded tasks={len(tasks):,}")
    print(f"[goal-grid] Indicator universe in sample ({len(needed)}): {', '.join(needed)}")
    print("[goal-grid] Building lightweight signal cache...")
    signal_cache = build_signal_cache(active_df, needed)

    start_price = float(active_df.iloc[0].get("Open", active_df.iloc[0]["Close"]))
    end_price = float(active_df.iloc[-1]["Close"])
    buy_hold_roi = ((end_price - start_price) / start_price) * 100.0 if start_price > 0 else 0.0
    target_roi = buy_hold_roi * 1.30 if buy_hold_roi > 0 else buy_hold_roi + abs(buy_hold_roi) * 0.30
    target_equity = CAPITAL * (1.0 + target_roi / 100.0)
    print(f"[goal-grid] Buy/hold ROI={buy_hold_roi:.2f}% | target >=30% above hold={target_roi:.2f}% (${target_equity:.2f})")
    print(f"[goal-grid] Eligibility: 1 <= trades <= {MAX_TRADES}; max runtime={args.max_hours:.2f}h; workers={args.workers}")

    started = time.time()
    deadline = started + args.max_hours * 3600.0
    completed = 0
    submitted = 0
    results = []
    errors = []
    pending = set()
    next_task = iter(tasks)
    top_eligible = []

    def submit_more(executor):
        nonlocal submitted
        while len(pending) < args.workers * 2 and submitted < len(tasks) and time.time() < deadline:
            try:
                task = next(next_task)
            except StopIteration:
                return
            future = executor.submit(_run_case, task)
            pending.add(future)
            submitted += 1

    with ProcessPoolExecutor(max_workers=args.workers, initializer=_init_worker, initargs=(active_df, signal_cache)) as executor:
        submit_more(executor)
        while pending:
            done, pending = wait(pending, timeout=5, return_when=FIRST_COMPLETED)
            if not done:
                elapsed = time.time() - started
                print(f"[goal-grid] Still running... completed={completed}/{len(tasks)} submitted={submitted}; elapsed={timedelta(seconds=int(elapsed))}", flush=True)
                if time.time() >= deadline:
                    print("[goal-grid] Runtime cap reached; stopping new submissions and waiting for in-flight tasks.")
                continue

            for future in done:
                completed += 1
                try:
                    row = future.result()
                    summary = row.get("summary", {})
                    trade_count = int(summary.get("total_trades", 0) or 0)
                    row["eligible"] = 0 < trade_count <= MAX_TRADES
                    row["target_hit"] = row["eligible"] and float(summary.get("roi_pct", 0.0) or 0.0) >= target_roi
                    results.append(row)
                    if row["eligible"]:
                        top_eligible.append(row)
                        top_eligible.sort(key=lambda item: _score_result(item, target_roi), reverse=True)
                        top_eligible = top_eligible[:20]
                except Exception as exc:
                    errors.append(str(exc))

            if time.time() < deadline:
                submit_more(executor)

            if completed == 1 or completed == len(tasks) or completed % args.progress_every == 0:
                elapsed = time.time() - started
                rate = completed / elapsed if elapsed > 0 else 0.0
                remaining = len(tasks) - completed
                eta = remaining / rate if rate > 0 else 0.0
                best = top_eligible[0] if top_eligible else None
                best_msg = "none yet"
                if best:
                    s = best["summary"]
                    best_msg = f"ROI {float(s.get('roi_pct', 0.0)):.2f}% | trades {int(s.get('total_trades', 0))} | family {best['settings']['strategy_family']}"
                print(
                    f"[goal-grid] Progress {completed}/{len(tasks)} ({completed / max(len(tasks), 1) * 100:.1f}%) | "
                    f"submitted={submitted} | elapsed={timedelta(seconds=int(elapsed))} | ETA={timedelta(seconds=int(eta))} | best<=50: {best_msg}",
                    flush=True,
                )

            if time.time() >= deadline and submitted >= len(tasks):
                continue

    results.sort(key=lambda item: _score_result(item, target_roi), reverse=True)
    eligible = [row for row in results if row.get("eligible")]
    target_hits = [row for row in eligible if row.get("target_hit")]
    payload = {
        "objective": "SPY 15Min 60-day regular-stock extended-hours grid, max 5 indicators, hybrid allowed, smart re-entry tested, <=50 trades accepted",
        "ticker": TICKER,
        "timeframe": TIMEFRAME,
        "days": DAYS,
        "capital": CAPITAL,
        "trade_amount": TRADE_AMOUNT,
        "market_session": MARKET_SESSION,
        "max_trades": MAX_TRADES,
        "start": start_date.isoformat(),
        "end": end_date.isoformat(),
        "bars": len(active_df),
        "full_grid_count": full_grid_count,
        "attempted_count": len(tasks),
        "completed_count": completed,
        "error_count": len(errors),
        "buy_hold_roi_pct": round(buy_hold_roi, 4),
        "target_roi_pct": round(target_roi, 4),
        "target_equity": round(target_equity, 2),
        "eligible_count": len(eligible),
        "target_hit_count": len(target_hits),
        "top_results": [
            {
                **row,
                "trades": [_compact_trade(trade) for trade in row.get("trades", [])],
            }
            for row in eligible[:25]
        ],
        "errors": errors[:20],
    }
    write_output(args.output, payload)

    print("\n=== Top 5 Eligible Strategies (<=50 trades) ===")
    if not eligible:
        print("No eligible strategies found with <=50 trades.")
    for idx, row in enumerate(eligible[:5], 1):
        s = row["summary"]
        settings = row["settings"]
        print(
            f"[{idx}] ROI {float(s.get('roi_pct', 0.0)):.2f}% | Equity ${float(s.get('final_equity', CAPITAL)):.2f} | "
            f"Trades {int(s.get('total_trades', 0))} | Win {float(s.get('win_rate_pct', 0.0)):.2f}% | "
            f"PF {s.get('profit_factor')} | TargetHit={row.get('target_hit')}"
        )
        print(
            f"    Settings: family={settings['strategy_family']} entry={settings['entry_mode']} exit={settings['exit_mode']} "
            f"threshold={settings['threshold']}/{settings['sell_threshold']} smart={settings['smart_reentry']} "
            f"reset={settings['enable_signal_reset']} cooldown={settings['enable_cooldown']} direction={settings['trade_direction_mode']} "
            f"params={settings['parameter_profile']}"
        )
        print(f"    Indicators: {', '.join(settings['indicators'])}")
        for trade in row.get("trades", [])[:5]:
            compact = _compact_trade(trade)
            print(
                f"    Trade: {compact['side']} {compact['entry_time']} -> {compact['exit_time']} "
                f"{compact['entry_price']} to {compact['exit_price']} | P/L ${compact['pl']} ({compact['pl_pct']}%) | {compact['reason']}"
            )
        if len(row.get("trades", [])) > 5:
            print(f"    ... {len(row.get('trades', [])) - 5} more trades in JSON output")
    print(f"\n[goal-grid] Results saved to {args.output}")


if __name__ == "__main__":
    main()
