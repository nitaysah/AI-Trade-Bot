from __future__ import annotations

import contextlib
import io
import json
import os
import random
import time
from concurrent.futures import ProcessPoolExecutor, as_completed
from datetime import datetime, timedelta

from backtester import Backtester
from spy_grid_30m_sim import _build_signal_cache, _fetch_data


TICKER = "SPY"
TIMEFRAME = "5Min"
DAYS = 60
CAPITAL = 1000.0
TRADE_AMOUNT = 1000.0
MARKET_SESSION = "Extended"
TRADE_DIRECTION_MODE = "both"
ALLOCATION_MODE = "compound"
START = datetime.now().replace(microsecond=0) - timedelta(days=DAYS)
END = datetime.now().replace(microsecond=0)

_WORKER_DF = None
_WORKER_SIGNAL_CACHE = None


FAMILIES = {
    "core": ["RSI", "Bollinger", "VWAP"],
    "core_plus_swing": ["RSI", "Bollinger", "VWAP", "Swing Points"],
    "core_plus_pivot": ["RSI", "Bollinger", "VWAP", "Pivot Points (S/R)"],
    "core_plus_bb3": ["RSI", "Bollinger", "VWAP", "BotBulls3"],
}

PARAM_PROFILES = [
    {
        "name": "tight",
        "atr_stop_multiplier": 0.70,
        "atr_trail_multiplier": 1.0,
        "atr_take_profit_multiplier": 3.0,
    },
    {
        "name": "current",
        "atr_stop_multiplier": 0.85,
        "atr_trail_multiplier": 1.2,
        "atr_take_profit_multiplier": 4.0,
    },
    {
        "name": "runner",
        "atr_stop_multiplier": 1.00,
        "atr_trail_multiplier": 1.5,
        "atr_take_profit_multiplier": 4.5,
    },
]

EXIT_MODES = ["sltp", "hybrid"]
THRESHOLDS = [1, 2]
SMART_REENTRY = [
    ("off", False, "RSI"),
    ("RSI", True, "RSI"),
]
COOLDOWN = [False, True]


def _init_worker(active_df, signal_cache):
    global _WORKER_DF, _WORKER_SIGNAL_CACHE
    _WORKER_DF = active_df
    _WORKER_SIGNAL_CACHE = signal_cache


def _run_case(case):
    family_name, indicators, param_profile, exit_mode, threshold, smart_label, smart_enabled, cooldown_enabled = case
    kwargs = dict(
        ticker=TICKER,
        timeframe=TIMEFRAME,
        start_date=START,
        end_date=END,
        initial_capital=CAPITAL,
        threshold=threshold,
        sell_threshold=threshold,
        enabled_indicators=indicators,
        risk_per_trade=1.0,
        max_pos_pct=1.0,
        ext_hours=True,
        buy_mode="indicator",
        custom_buy_price=0.0,
        sell_mode=exit_mode,
        atr_stop_multiplier=param_profile["atr_stop_multiplier"],
        atr_trail_multiplier=param_profile["atr_trail_multiplier"],
        atr_take_profit_multiplier=param_profile["atr_take_profit_multiplier"],
        enable_atr_trail=True,
        ai_autopilot=False,
        ai_interval=10,
        ai_model="llama-3.3-70b-versatile",
        enable_cooldown=cooldown_enabled,
        cooldown_minutes=None,
        regular_stop_loss_pct=0.05,
        regular_take_profit_pct=0.10,
        regular_trailing_pct=0.03,
        enable_pct_trail=False,
        cancel_event=None,
        entry_atr_style="pullback_reclaim",
        entry_pct_style="dip_reclaim",
        market_session=MARKET_SESSION,
        allocation_mode=ALLOCATION_MODE,
        trade_direction_mode=TRADE_DIRECTION_MODE,
        trade_amount=TRADE_AMOUNT,
        custom_stop_loss_price=0.0,
        custom_take_profit_price=0.0,
        enable_signal_reset=False,
        enable_smart_reentry=smart_enabled,
        smart_reentry_type=smart_label,
        smart_reentry_rsi_call_max=70.0,
        smart_reentry_rsi_put_min=30.0,
        smart_reentry_vwap_call_max_dist_pct=0.5,
        smart_reentry_vwap_put_max_dist_pct=0.5,
        enable_risk=False,
        entry_atr_multiplier=param_profile["atr_stop_multiplier"],
        entry_pct_move=1.0,
        swing_entry_mode="bounce_retest",
        swing_entry_lookback_days=5,
        swing_exit_lookback_days=5,
        daily_high_low_buffer_pct=0.05,
    )
    bt = Backtester(**kwargs)
    with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
        res = bt.run(precomputed_df=_WORKER_DF, precomputed_signal_cache=_WORKER_SIGNAL_CACHE, fast_metrics=True)
    summary = res.get("summary", {})
    return {
        "family": family_name,
        "indicators": indicators,
        "settings": {
            "family": family_name,
            "indicators": indicators,
            "parameter_profile": param_profile["name"],
            "exit_mode": exit_mode,
            "threshold": threshold,
            "sell_threshold": threshold,
            "smart_reentry": smart_label,
            "enable_smart_reentry": smart_enabled,
            "enable_cooldown": cooldown_enabled,
            "allocation_mode": ALLOCATION_MODE,
            "trade_direction_mode": TRADE_DIRECTION_MODE,
            "atr_stop_multiplier": param_profile["atr_stop_multiplier"],
            "atr_trail_multiplier": param_profile["atr_trail_multiplier"],
            "atr_take_profit_multiplier": param_profile["atr_take_profit_multiplier"],
        },
        "summary": summary,
        "trades": res.get("trades", []),
    }


def main():
    print("[refine] Loading SPY data and signals...")
    active_df = _fetch_data(TICKER, TIMEFRAME, START, END, MARKET_SESSION)
    needed = sorted({ind for inds in FAMILIES.values() for ind in inds})
    signal_cache = _build_signal_cache(active_df, needed)
    print(f"[refine] Loaded {len(active_df)} bars; signal cache ready.")

    tasks = []
    for family_name, indicators in FAMILIES.items():
        for param_profile in PARAM_PROFILES:
            for exit_mode in EXIT_MODES:
                for threshold in THRESHOLDS:
                    for smart_label, smart_enabled, _ in SMART_REENTRY:
                        for cooldown_enabled in COOLDOWN:
                            tasks.append(
                                (
                                    family_name,
                                    indicators,
                                    param_profile,
                                    exit_mode,
                                    threshold,
                                    smart_label,
                                    smart_enabled,
                                    cooldown_enabled,
                                )
                            )

    rng = random.Random(77)
    rng.shuffle(tasks)

    print(f"[refine] Testing {len(tasks)} combinations with 4 workers...")
    results = []
    started = time.time()

    with ProcessPoolExecutor(max_workers=4, initializer=_init_worker, initargs=(active_df, signal_cache)) as ex:
        futures = [ex.submit(_run_case, task) for task in tasks]
        completed = 0
        for fut in as_completed(futures):
            completed += 1
            try:
                results.append(fut.result())
            except Exception as exc:
                print(f"[refine] Worker failed: {exc}")
            if completed == 1 or completed == len(futures) or completed % 16 == 0:
                elapsed = time.time() - started
                rate = completed / elapsed if elapsed > 0 else 0.0
                remaining = len(futures) - completed
                eta_sec = remaining / rate if rate > 0 else 0.0
                best_roi = max((float(r.get("summary", {}).get("roi_pct", 0.0)) for r in results), default=0.0)
                best_equity = max((float(r.get("summary", {}).get("final_equity", CAPITAL)) for r in results), default=CAPITAL)
                print(
                    f"[refine] Progress {completed}/{len(futures)} ({completed / len(futures) * 100:.1f}%) | "
                    f"ETA {timedelta(seconds=int(eta_sec))} | Best ROI {best_roi:.2f}% | Best equity ${best_equity:.2f}",
                    flush=True,
                )

    results.sort(key=lambda r: (float(r["summary"].get("roi_pct", 0.0)), float(r["summary"].get("final_equity", 0.0))), reverse=True)

    output = {
        "ticker": TICKER,
        "timeframe": TIMEFRAME,
        "days": DAYS,
        "capital": CAPITAL,
        "trade_amount": TRADE_AMOUNT,
        "market_session": MARKET_SESSION,
        "trade_direction_mode": TRADE_DIRECTION_MODE,
        "allocation_mode": ALLOCATION_MODE,
        "results": results,
    }
    out_path = "backend/refinement_validation.json"
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(output, f, indent=2, default=str)

    print("\n=== Top 5 ===")
    for idx, row in enumerate(results[:5], 1):
        s = row["summary"]
        print(
            f"[{idx}] {row['family']} | {row['settings']['parameter_profile']} | exit={row['settings']['exit_mode']} | "
            f"th={row['settings']['threshold']} | smart={row['settings']['smart_reentry']} | cooldown={row['settings']['enable_cooldown']} | "
            f"ROI {s.get('roi_pct', 0.0):.2f}% | Equity ${s.get('final_equity', 0.0):.2f} | Trades {s.get('total_trades', 0)}"
        )
    print(f"\n[refine] Results saved to {out_path}")


if __name__ == "__main__":
    main()
