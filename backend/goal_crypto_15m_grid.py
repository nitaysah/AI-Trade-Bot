from __future__ import annotations

import argparse
import contextlib
import io
import json
import math
import multiprocessing as mp
import os
import random
import time
from concurrent.futures import ProcessPoolExecutor, wait, FIRST_COMPLETED
from datetime import datetime, timedelta
from itertools import combinations, product

from backtester import Backtester
from data_manager import get_historical_data
from indicators import calculate_indicators_for_df

TIMEFRAME = "15Min"
DAYS = 60
CAPITAL = 1000.0
TRADE_AMOUNT = 1000.0
MARKET_SESSION = "24/7"
MAX_TRADES = 100
ENTRY_FEE_PCT = 0.001
EXIT_FEE_PCT = 0.001

ALL_INDICATORS = [
    "RSI", "MACD", "EMA Cross", "ADX Trend", "Supertrend", "Bollinger", 
    "VWAP", "SMA", "Mystic Pulse", "Candle Patterns", "Prior Day High/Low",
    "Pivot Points (S/R)", "Volume Profile", "Swing Points", "BotBulls1", "BotBulls2", "BotBulls3"
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
    ["BotBulls3", "VWAP"],
]

PARAMETER_PROFILES = [
    {
        "name": "crypto_tight",
        "atr_stop_multiplier": 1.5,
        "atr_trail_multiplier": 2.5,
        "atr_take_profit_multiplier": 4.0,
        "regular_stop_loss_pct": 0.02,
        "regular_take_profit_pct": 0.05,
        "regular_trailing_pct": 0.015,
        "entry_atr_multiplier": 1.0,
        "entry_pct_move": 1.5,
        "swing_entry_lookback_days": 10,
        "swing_exit_lookback_days": 10,
        "swing_entry_mode": "bounce_retest",
        "daily_high_low_buffer_pct": 0.03,
    },
    {
        "name": "crypto_balanced",
        "atr_stop_multiplier": 2.5,
        "atr_trail_multiplier": 3.5,
        "atr_take_profit_multiplier": 6.0,
        "regular_stop_loss_pct": 0.04,
        "regular_take_profit_pct": 0.08,
        "regular_trailing_pct": 0.02,
        "entry_atr_multiplier": 1.5,
        "entry_pct_move": 2.0,
        "swing_entry_lookback_days": 20,
        "swing_exit_lookback_days": 20,
        "swing_entry_mode": "bounce_retest",
        "daily_high_low_buffer_pct": 0.05,
    },
    {
        "name": "crypto_runner",
        "atr_stop_multiplier": 3.5,
        "atr_trail_multiplier": 5.0,
        "atr_take_profit_multiplier": 8.0,
        "regular_stop_loss_pct": 0.06,
        "regular_take_profit_pct": 0.15,
        "regular_trailing_pct": 0.035,
        "entry_atr_multiplier": 2.0,
        "entry_pct_move": 3.0,
        "swing_entry_lookback_days": 30,
        "swing_exit_lookback_days": 30,
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

def _run_case_isolated(task, active_df, signal_cache, queue):
    try:
        global _WORKER_DF, _WORKER_SIGNAL_CACHE
        _WORKER_DF = active_df
        _WORKER_SIGNAL_CACHE = signal_cache
        queue.put({"ok": True, "row": _run_case(task)})
    except Exception as exc:
        queue.put({"ok": False, "error": str(exc)})

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
    for size in range(2, 5):
        combo_pool.extend(combinations(ALL_INDICATORS, size))
    rng.shuffle(combo_pool)
    for combo in combo_pool:
        indicator_sets.add(tuple(combo))
        if len(indicator_sets) >= max_sets:
            break
    return sorted(indicator_sets, key=lambda item: (len(item), item))

def fetch_data(ticker: str, timeframe: str, start_date: datetime, end_date: datetime, market_session: str):
    raw_df = get_historical_data(ticker, timeframe, start_date, end_date, force_yahoo=False)
    if raw_df is None or raw_df.empty:
        raise RuntimeError(f"No historical data returned for {ticker}.")
    result = calculate_indicators_for_df(raw_df, timeframe, ticker, settings={"market_session": market_session})
    if not result or "df" not in result:
        raise RuntimeError("Indicator calculation failed.")
    full_df = result["df"]
    if hasattr(full_df.index, "tz") and full_df.index.tz is not None:
        full_df.index = full_df.index.tz_localize(None)
    active_df = full_df.loc[start_date:end_date]
    if active_df.empty:
        raise RuntimeError("No active bars after slicing.")
    return active_df

def _safe_float(row, key, fallback=0.0):
    try:
        value = row.get(key, fallback)
        if value is None: return fallback
        value = float(value)
        if math.isnan(value) or math.isinf(value): return fallback
        return value
    except Exception:
        return fallback

def _safe_bool(row, key, fallback=False):
    try:
        value = row.get(key, fallback)
        if value is None: return fallback
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
            if is_trending and di_plus > di_minus: signals["ADX Trend"] = _signal("BULLISH", f"DI+ {di_plus:.1f}")
            elif is_trending and di_minus > di_plus: signals["ADX Trend"] = _signal("BEARISH", f"DI- {di_minus:.1f}")
            else: signals["ADX Trend"] = _signal("NEUTRAL", f"ADX {adx:.1f}")
        if "EMA Cross" in needed:
            ema_fast = _safe_float(current_bar, "EMA_Fast", price)
            ema_slow = _safe_float(current_bar, "EMA_Slow", price)
            signals["EMA Cross"] = _signal(("BULLISH" if ema_fast > ema_slow else "BEARISH") if is_trending else "NEUTRAL", "")
        if "Supertrend" in needed:
            st_bull = _safe_bool(current_bar, "Supertrend_Trend", True)
            signals["Supertrend"] = _signal("BULLISH" if st_bull else "BEARISH", "")
        if "Bollinger" in needed:
            boll_lo = _safe_float(current_bar, "BOLL_Lower", price)
            boll_hi = _safe_float(current_bar, "BOLL_Upper", price)
            channel = max(boll_hi - boll_lo, 0.0)
            signals["Bollinger"] = _signal("BULLISH" if price <= boll_lo + channel * 0.2 else "BEARISH" if price >= boll_hi - channel * 0.2 else "NEUTRAL", "")
        if "VWAP" in needed:
            vwap = _safe_float(current_bar, "VWAP", price)
            prev_vwap = _safe_float(prev_bar, "VWAP", vwap)
            signals["VWAP"] = _signal("BULLISH" if (price >= vwap and prev_close < prev_vwap) else "BEARISH" if price < vwap else "NEUTRAL", "")
        if "SMA" in needed:
            sma = _safe_float(current_bar, "SMA", price)
            signals["SMA"] = _signal("BULLISH" if price >= sma else "BEARISH", "")
        if "Prior Day High/Low" in needed:
            if _safe_bool(current_bar, "Daily_HighLow_Bullish", False): signals["Prior Day High/Low"] = _signal("BULLISH", "")
            elif _safe_bool(current_bar, "Daily_HighLow_Bearish", False): signals["Prior Day High/Low"] = _signal("BEARISH", "")
            else: signals["Prior Day High/Low"] = _signal("NEUTRAL", "")
        if "Pivot Points (S/R)" in needed:
            if _safe_bool(current_bar, "Pivot_Bullish", False): signals["Pivot Points (S/R)"] = _signal("BULLISH", "")
            elif _safe_bool(current_bar, "Pivot_Bearish", False): signals["Pivot Points (S/R)"] = _signal("BEARISH", "")
            else: signals["Pivot Points (S/R)"] = _signal("NEUTRAL", "")
        if "Volume Profile" in needed:
            if _safe_bool(current_bar, "Volume_Profile_Bullish", False): signals["Volume Profile"] = _signal("BULLISH", "")
            elif _safe_bool(current_bar, "Volume_Profile_Bearish", False): signals["Volume Profile"] = _signal("BEARISH", "")
            else: signals["Volume Profile"] = _signal("NEUTRAL", "")
        if "Swing Points" in needed:
            if _safe_bool(current_bar, "Swing_Bullish", False): signals["Swing Points"] = _signal("BULLISH", "")
            elif _safe_bool(current_bar, "Swing_Bearish", False): signals["Swing Points"] = _signal("BEARISH", "")
            else: signals["Swing Points"] = _signal("NEUTRAL", "")
        if "Mystic Pulse" in needed:
            bull_p = _safe_float(current_bar, "Bull_Pulse")
            bear_p = _safe_float(current_bar, "Bear_Pulse")
            signals["Mystic Pulse"] = _signal("BULLISH" if bull_p >= 5 else "BEARISH" if bear_p >= 5 else "NEUTRAL", "")
        if "Candle Patterns" in needed:
            if _safe_bool(current_bar, "cdl_bullish", False): signals["Candle Patterns"] = _signal("BULLISH", "")
            elif _safe_bool(current_bar, "cdl_bearish", False): signals["Candle Patterns"] = _signal("BEARISH", "")
            else: signals["Candle Patterns"] = _signal("NEUTRAL", "")
        if "BotBulls1" in needed:
            signals["BotBulls1"] = _signal("BULLISH" if _safe_bool(current_bar, "BA1_Buy", False) else "BEARISH" if _safe_bool(current_bar, "BA1_Sell", False) else "NEUTRAL", "", premium=True)
        if "BotBulls2" in needed:
            bb2_bull = _safe_bool(current_bar, "LX_SmartTrend", True)
            signals["BotBulls2"] = _signal("BULLISH" if bb2_bull else "BEARISH", "", premium=True)
        if "BotBulls3" in needed:
            bb3_bull = _safe_bool(current_bar, "UT_Above", True)
            signals["BotBulls3"] = _signal("BULLISH" if bb3_bull else "BEARISH", "", premium=True)
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

def _run_case_with_timeout(task, active_df, signal_cache, timeout_seconds):
    ctx = mp.get_context("fork") if "fork" in mp.get_all_start_methods() else mp.get_context()
    queue = ctx.Queue(maxsize=1)
    proc = ctx.Process(target=_run_case_isolated, args=(task, active_df, signal_cache, queue))
    proc.start()
    proc.join(timeout_seconds)
    if proc.is_alive():
        proc.terminate()
        proc.join(2)
        return {
            "ok": False,
            "error": f"case_timeout_after_{timeout_seconds}s",
            "settings": task.get("settings", {}),
        }
    try:
        payload = queue.get_nowait()
    except Exception:
        return {"ok": False, "error": "case_exited_without_result", "settings": task.get("settings", {})}
    if payload.get("ok"):
        return {"ok": True, "row": payload.get("row")}
    return {"ok": False, "error": payload.get("error", "case_failed"), "settings": task.get("settings", {})}

def _score_result(row):
    summary = row.get("summary", {})
    trades = int(summary.get("total_trades", 0) or 0)
    net_pl = float(summary.get("total_pl", 0.0) or 0.0)
    roi = float(summary.get("roi_pct", 0.0) or 0.0)
    profit_factor = float(summary.get("profit_factor", 0.0) or 0.0)
    win_rate = float(summary.get("win_rate_pct", 0.0) or 0.0)
    eligible = 0 < trades <= MAX_TRADES
    return (
        1 if eligible else 0,
        net_pl,
        roi,
        profit_factor if math.isfinite(profit_factor) else 999.0,
        win_rate,
        -trades,
    )

def _compact_trade(trade):
    net_pl = trade.get("pl")
    fees = trade.get("fees", 0.0) or 0.0
    try:
        gross_pl = round(float(net_pl or 0.0) + float(fees or 0.0), 2)
    except Exception:
        gross_pl = net_pl
    return {
        "entry_time": trade.get("entry_time"),
        "exit_time": trade.get("exit_time"),
        "side": trade.get("side"),
        "entry_price": trade.get("entry_price"),
        "exit_price": trade.get("exit_price"),
        "qty": trade.get("qty"),
        "gross_pl": gross_pl,
        "pl_pct": trade.get("pl_pct"),
        "fees": fees,
        "net_pl": net_pl,
        "stop_loss": trade.get("stop_loss"),
        "take_profit": trade.get("take_profit"),
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

def _diverse_sample(tasks, limit):
    if limit <= 0 or len(tasks) <= limit:
        return tasks
    buckets = {}
    for task in tasks:
        settings = task["settings"]
        key = (
            settings.get("trade_direction_mode"),
            settings.get("strategy_family"),
            tuple(settings.get("indicators", [])),
            settings.get("parameter_profile"),
        )
        buckets.setdefault(key, []).append(task)
    for bucket in buckets.values():
        bucket.sort(key=_task_cost)
    selected = []
    keys = sorted(
        buckets,
        key=lambda item: (
            _task_cost(buckets[item][0]),
            str(item[0]),
            str(item[1]),
            len(item[2]),
            str(item[3]),
        ),
    )
    while len(selected) < limit and keys:
        next_keys = []
        for key in keys:
            bucket = buckets.get(key) or []
            if not bucket:
                continue
            selected.append(bucket.pop(0))
            if bucket:
                next_keys.append(key)
            if len(selected) >= limit:
                break
        keys = next_keys
    return selected

def build_tasks(args, ticker, start_date, end_date):
    rng = random.Random(args.seed)
    indicator_sets = build_indicator_sets(args.seed, args.max_indicator_sets)
    include_families = {
        item.strip()
        for item in str(getattr(args, "include_families", "all") or "all").split(",")
        if item.strip()
    }
    tasks = []
    seen = set()
    threshold_pairs_by_size = {
        1: [(1, 1)],
        2: [(1, 1), (2, 1), (2, 2)],
        3: [(2, 1), (2, 2), (3, 2)],
        4: [(2, 2), (3, 2)],
        5: [(3, 2)],
    }
    
    for family, indicators, params, smart, signal_reset, cooldown, direction in product(
        STRATEGY_FAMILIES, indicator_sets, PARAMETER_PROFILES, SMART_REENTRY_MODES, [True, False], [True, False], ["long", "both"]
    ):
        if "all" not in include_families and family["name"] not in include_families:
            continue
        smart_label, smart_enabled, smart_type = smart
        threshold_pairs = threshold_pairs_by_size.get(len(indicators), [(1, 1)])
        for entry_mode, exit_mode, threshold_pair in product(family["entry_modes"], family["exit_modes"], threshold_pairs):
            threshold, sell_threshold = threshold_pair
            if "hybrid" in entry_mode and threshold > len(indicators): continue
            
            signature = (family["name"], indicators, params["name"], smart_label, signal_reset, cooldown, direction, entry_mode, exit_mode, threshold, sell_threshold)
            if signature in seen: continue
            seen.add(signature)
            
            kwargs = {
                "ticker": ticker,
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
                "cooldown_minutes": 60 if cooldown else None,
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
                "enable_risk": False,
                "max_completed_trades": MAX_TRADES + 1,
                **{k: v for k, v in params.items() if k != "name"},
            }
            kwargs["settings"] = {
                "strategy_family": family["name"],
                "ticker": ticker,
                "indicators": list(indicators),
                "indicator_count": len(indicators),
                "parameter_profile": params["name"],
                "entry_mode": entry_mode,
                "exit_mode": exit_mode,
                "threshold": threshold,
                "sell_threshold": sell_threshold,
                "smart_reentry": smart_label,
                "enable_signal_reset": signal_reset,
                "enable_cooldown": cooldown,
                "cooldown_minutes": 60 if cooldown else None,
                "trade_direction_mode": direction,
                "capital": CAPITAL,
                "trade_amount": TRADE_AMOUNT,
                "timeframe": TIMEFRAME,
                "days": DAYS,
                "market_session": MARKET_SESSION,
                "entry_fee_pct": ENTRY_FEE_PCT,
                "exit_fee_pct": EXIT_FEE_PCT,
                **{k: v for k, v in params.items() if k != "name"},
            }
            tasks.append(kwargs)
            
    rng.shuffle(tasks)
    tasks.sort(key=_task_cost)
    return _diverse_sample(tasks, args.max_combos), len(tasks)

def run_grid_for_ticker(args, ticker):
    end_date = datetime.now().replace(microsecond=0)
    start_date = end_date - timedelta(days=DAYS)
    print(f"\n--- Running grid for {ticker} ---", flush=True)
    
    active_df = fetch_data(ticker, TIMEFRAME, start_date, end_date, MARKET_SESSION)
    tasks, _ = build_tasks(args, ticker, start_date, end_date)
    needed = sorted({indicator for task in tasks for indicator in task["enabled_indicators"]})
    signal_cache = build_signal_cache(active_df, needed)
    
    started = time.time()
    deadline = started + float(getattr(args, "max_hours", 1.0)) * 3600.0
    completed, submitted = 0, 0
    results, errors = [], []
    pending = set()
    next_task = iter(tasks)
    top_eligible = []
    
    started = time.time()
    
    case_timeout = float(getattr(args, "case_timeout_seconds", 0.0) or 0.0)
    if case_timeout > 0:
        for task in tasks:
            if time.time() >= deadline:
                break
            payload = _run_case_with_timeout(task, active_df, signal_cache, case_timeout)
            completed += 1
            if payload.get("ok"):
                row = payload["row"]
                s = row.get("summary", {})
                trades = int(s.get("total_trades", 0) or 0)
                row["eligible"] = 0 < trades <= MAX_TRADES
                results.append(row)
                if row["eligible"]:
                    top_eligible.append(row)
                    top_eligible.sort(key=lambda item: _score_result(item), reverse=True)
                    top_eligible = top_eligible[:20]
            else:
                errors.append({
                    "error": payload.get("error", "case_failed"),
                    "settings": payload.get("settings", task.get("settings", {})),
                })
            if completed == 1 or completed == len(tasks) or completed % 10 == 0:
                elapsed = time.time() - started
                print(f"[{ticker}] Progress: {completed}/{len(tasks)} ({completed / len(tasks) * 100:.1f}%) elapsed={timedelta(seconds=int(elapsed))}", flush=True)
    else:
        with ProcessPoolExecutor(max_workers=args.workers, initializer=_init_worker, initargs=(active_df, signal_cache)) as executor:
            def submit_more():
                nonlocal submitted
                while len(pending) < args.workers * 2 and submitted < len(tasks) and time.time() < deadline:
                    try: task = next(next_task)
                    except StopIteration: return
                    pending.add(executor.submit(_run_case, task))
                    submitted += 1
                    
            submit_more()
            while pending:
                done, pending = wait(pending, timeout=1, return_when=FIRST_COMPLETED)
                for future in done:
                    completed += 1
                    try:
                        row = future.result()
                        s = row.get("summary", {})
                        trades = int(s.get("total_trades", 0) or 0)
                        row["eligible"] = 0 < trades <= MAX_TRADES
                        results.append(row)
                        if row["eligible"]:
                            top_eligible.append(row)
                            top_eligible.sort(key=lambda item: _score_result(item), reverse=True)
                            top_eligible = top_eligible[:20]
                    except Exception as exc:
                        errors.append(str(exc))
                if time.time() < deadline:
                    submit_more()
                if completed == 1 or completed == len(tasks) or (completed > 0 and completed % 25 == 0):
                    elapsed = time.time() - started
                    print(f"[{ticker}] Progress: {completed}/{len(tasks)} ({completed / len(tasks) * 100:.1f}%) elapsed={timedelta(seconds=int(elapsed))}", flush=True)
                
    results.sort(key=lambda item: _score_result(item), reverse=True)
    eligible = [row for row in results if row.get("eligible")]
    unique_top = []
    seen_top = set()
    for row in eligible:
        settings = row.get("settings", {})
        signature = (
            settings.get("strategy_family"),
            tuple(settings.get("indicators", [])),
            settings.get("parameter_profile"),
            settings.get("entry_mode"),
            settings.get("exit_mode"),
            settings.get("threshold"),
            settings.get("sell_threshold"),
            settings.get("smart_reentry"),
            settings.get("enable_signal_reset"),
            settings.get("enable_cooldown"),
            settings.get("trade_direction_mode"),
        )
        if signature in seen_top:
            continue
        seen_top.add(signature)
        unique_top.append(row)
    all_compact_trades = []
    for row in eligible:
        for trade in row.get("trades", []):
            compact = _compact_trade(trade)
            compact["strategy_settings"] = row.get("settings", {})
            all_compact_trades.append(compact)
    all_compact_trades.sort(key=lambda item: float(item.get("net_pl") or 0.0), reverse=True)
    
    start_price = float(active_df.iloc[0]["Close"])
    end_price = float(active_df.iloc[-1]["Close"])
    buy_hold_roi = ((end_price - start_price) / start_price) * 100.0 if start_price > 0 else 0.0

    payload = {
        "objective": "BTCUSD/ETHUSD 15Min 60-day 24/7 crypto grid search with 0.1% entry fee and 0.1% exit fee; test at least 10 custom non-default indicator/settings variants and reject overtrading configs with >100 completed trades per ticker.",
        "ticker": ticker,
        "timeframe": TIMEFRAME,
        "days": DAYS,
        "capital": CAPITAL,
        "trade_amount": TRADE_AMOUNT,
        "market_session": MARKET_SESSION,
        "entry_fee_pct": ENTRY_FEE_PCT,
        "exit_fee_pct": EXIT_FEE_PCT,
        "max_trades": MAX_TRADES,
        "start": start_date.isoformat(),
        "end": end_date.isoformat(),
        "bars": len(active_df),
        "attempted_count": len(tasks),
        "completed_count": completed,
        "error_count": len(errors),
        "eligible_count": len(eligible),
        "unique_eligible_count": len(unique_top),
        "buy_hold_roi_pct": round(buy_hold_roi, 4),
        "execution_time_seconds": round(time.time() - started, 2),
        "best_trades": all_compact_trades[:5],
        "top_results": [
            {
                **row,
                "trades": [_compact_trade(trade) for trade in row.get("trades", [])],
                "best_trades": sorted(
                    [_compact_trade(trade) for trade in row.get("trades", [])],
                    key=lambda item: float(item.get("net_pl") or 0.0),
                    reverse=True,
                )[:5],
            }
            for row in unique_top[:25]
        ],
        "all_results": [
            {
                "settings": row.get("settings", {}),
                "summary": row.get("summary", {}),
                "eligible": row.get("eligible", False),
            }
            for row in results
        ],
        "errors": errors[:20],
    }
    output_path = f"backend/goal_crypto_results_new_{ticker}.json"
    with open(output_path, "w") as f:
        json.dump(payload, f, indent=2)
    print(f"Saved {ticker} to {output_path}", flush=True)

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--max-combos", type=int, default=150)
    parser.add_argument("--max-indicator-sets", type=int, default=20)
    parser.add_argument("--workers", type=int, default=max(2, min(8, os.cpu_count() or 4)))
    parser.add_argument("--seed", type=int, default=123)
    parser.add_argument("--max-hours", type=float, default=1.0)
    parser.add_argument("--tickers", default="BTCUSD,ETHUSD")
    parser.add_argument("--include-families", default="all")
    parser.add_argument("--case-timeout-seconds", type=float, default=0.0)
    args = parser.parse_args()
    
    for ticker in [item.strip().upper() for item in args.tickers.split(",") if item.strip()]:
        try:
            run_grid_for_ticker(args, ticker)
        except Exception as e:
            print(f"Error running grid for {ticker}: {e}")

if __name__ == "__main__":
    main()
