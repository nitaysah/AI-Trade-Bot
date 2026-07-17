from __future__ import annotations

import contextlib
import io
import json
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
TRADE_DIRECTION_MODE = "long"
ALLOCATION_MODE = "fixed"
START = datetime.now().replace(microsecond=0) - timedelta(days=DAYS)
END = datetime.now().replace(microsecond=0)

_WORKER_DF = None
_WORKER_SIGNAL_CACHE = None


FAMILIES = {
    "core_lean": {
        "indicators": ["RSI", "Bollinger", "VWAP"],
        "exit_modes": ["sltp", "hybrid"],
    },
    "swing_long": {
        "indicators": ["Swing Points", "VWAP", "RSI"],
        "exit_modes": ["swing_points", "hybrid_swing"],
    },
    "pivot_long": {
        "indicators": ["Pivot Points (S/R)", "VWAP", "RSI"],
        "exit_modes": ["pivot_points", "hybrid_pivot"],
    },
    "high_low_long": {
        "indicators": ["Prior Day High/Low", "VWAP", "RSI"],
        "exit_modes": ["high_low", "hybrid_high_low"],
    },
    "trend_long": {
        "indicators": ["EMA Cross", "Supertrend", "VWAP", "SMA"],
        "exit_modes": ["trend_rider"],
    },
}

THRESHOLDS = [3, 4, 5]
SMART_REENTRY = [
    ("off", False, "RSI"),
    ("RSI", True, "RSI"),
]
COOLDOWN_MINUTES = [60, 120]


def _init_worker(active_df, signal_cache):
    global _WORKER_DF, _WORKER_SIGNAL_CACHE
    _WORKER_DF = active_df
    _WORKER_SIGNAL_CACHE = signal_cache


def _run_case(case):
    family_name, family, exit_mode, threshold, smart_label, smart_enabled, cooldown_minutes = case
    kwargs = dict(
        ticker=TICKER,
        timeframe=TIMEFRAME,
        start_date=START,
        end_date=END,
        initial_capital=CAPITAL,
        threshold=threshold,
        sell_threshold=threshold,
        enabled_indicators=family["indicators"],
        risk_per_trade=1.0,
        max_pos_pct=1.0,
        ext_hours=True,
        buy_mode="indicator",
        custom_buy_price=0.0,
        sell_mode=exit_mode,
        atr_stop_multiplier=1.0,
        atr_trail_multiplier=1.5,
        atr_take_profit_multiplier=4.0,
        enable_atr_trail=True,
        ai_autopilot=False,
        ai_interval=10,
        ai_model="llama-3.3-70b-versatile",
        enable_cooldown=True,
        cooldown_minutes=cooldown_minutes,
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
        enable_signal_reset=True,
        enable_smart_reentry=smart_enabled,
        smart_reentry_type=smart_label,
        smart_reentry_rsi_call_max=70.0,
        smart_reentry_rsi_put_min=30.0,
        smart_reentry_vwap_call_max_dist_pct=0.5,
        smart_reentry_vwap_put_max_dist_pct=0.5,
        enable_risk=False,
        entry_atr_multiplier=1.0,
        entry_pct_move=1.0,
        swing_entry_mode="bounce_retest",
        swing_entry_lookback_days=20,
        swing_exit_lookback_days=20,
        daily_high_low_buffer_pct=0.05,
    )
    bt = Backtester(**kwargs)
    with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
        res = bt.run(precomputed_df=_WORKER_DF, precomputed_signal_cache=_WORKER_SIGNAL_CACHE, fast_metrics=True)
    summary = res.get("summary", {})
    return {
        "family": family_name,
        "settings": {
            "family": family_name,
            "indicators": family["indicators"],
            "exit_mode": exit_mode,
            "threshold": threshold,
            "smart_reentry": smart_label,
            "enable_smart_reentry": smart_enabled,
            "enable_signal_reset": True,
            "cooldown_minutes": cooldown_minutes,
            "allocation_mode": ALLOCATION_MODE,
            "trade_direction_mode": TRADE_DIRECTION_MODE,
        },
        "summary": summary,
        "trades": res.get("trades", []),
    }


def main():
    print("[low-trade] Loading SPY data and signals...")
    active_df = _fetch_data(TICKER, TIMEFRAME, START, END, MARKET_SESSION)
    needed = sorted({ind for family in FAMILIES.values() for ind in family["indicators"]})
    signal_cache = _build_signal_cache(active_df, needed)
    print(f"[low-trade] Loaded {len(active_df)} bars; signal cache ready.")

    tasks = []
    for family_name, family in FAMILIES.items():
        for exit_mode in family["exit_modes"]:
            for threshold in THRESHOLDS:
                for smart_label, smart_enabled, _ in SMART_REENTRY:
                    for cooldown_minutes in COOLDOWN_MINUTES:
                        tasks.append((family_name, family, exit_mode, threshold, smart_label, smart_enabled, cooldown_minutes))

    print(f"[low-trade] Testing {len(tasks)} combinations with 4 workers...")
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
                print(f"[low-trade] Worker failed: {exc}")
            if completed == 1 or completed == len(futures) or completed % 12 == 0:
                elapsed = time.time() - started
                rate = completed / elapsed if elapsed > 0 else 0.0
                remaining = len(futures) - completed
                eta_sec = remaining / rate if rate > 0 else 0.0
                filtered = [r for r in results if int(r.get("summary", {}).get("total_trades", 999999)) <= 50]
                best_roi = max((float(r.get("summary", {}).get("roi_pct", 0.0)) for r in filtered), default=0.0)
                best_equity = max((float(r.get("summary", {}).get("final_equity", CAPITAL)) for r in filtered), default=CAPITAL)
                print(
                    f"[low-trade] Progress {completed}/{len(futures)} ({completed / len(futures) * 100:.1f}%) | "
                    f"ETA {timedelta(seconds=int(eta_sec))} | Best ROI <=50 trades {best_roi:.2f}% | "
                    f"Best equity ${best_equity:.2f}",
                    flush=True,
                )

    results.sort(key=lambda r: (int(r["summary"].get("total_trades", 10**9)) <= 50, float(r["summary"].get("roi_pct", 0.0)), -int(r["summary"].get("total_trades", 10**9))), reverse=True)
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
    out_path = "backend/low_trade_validation.json"
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(output, f, indent=2, default=str)

    eligible = [r for r in results if int(r["summary"].get("total_trades", 10**9)) <= 50]
    print("\n=== Top 5 <= 50 Trades ===")
    for idx, row in enumerate(eligible[:5], 1):
        s = row["summary"]
        print(
            f"[{idx}] {row['family']} | {row['settings']['exit_mode']} | th={row['settings']['threshold']} | "
            f"smart={row['settings']['smart_reentry']} | cooldown={row['settings']['cooldown_minutes']}m | "
            f"ROI {s.get('roi_pct', 0.0):.2f}% | Equity ${s.get('final_equity', 0.0):.2f} | Trades {s.get('total_trades', 0)}"
        )
    if not eligible:
        print("No combinations finished with 50 or fewer trades.")
    print(f"\n[low-trade] Results saved to {out_path}")


if __name__ == "__main__":
    main()
