from __future__ import annotations

import json
import os
import contextlib
import io
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


def _init_worker(active_df, signal_cache):
    global _WORKER_DF, _WORKER_SIGNAL_CACHE
    _WORKER_DF = active_df
    _WORKER_SIGNAL_CACHE = signal_cache


def _run_case(case):
    name, indicators = case
    kwargs = dict(
        ticker=TICKER,
        timeframe=TIMEFRAME,
        start_date=START,
        end_date=END,
        initial_capital=CAPITAL,
        threshold=1,
        sell_threshold=1,
        enabled_indicators=indicators,
        risk_per_trade=1.0,
        max_pos_pct=1.0,
        ext_hours=True,
        buy_mode="indicator",
        custom_buy_price=0.0,
        sell_mode="sltp",
        atr_stop_multiplier=0.85,
        atr_trail_multiplier=1.2,
        atr_take_profit_multiplier=4.0,
        enable_atr_trail=True,
        ai_autopilot=False,
        ai_interval=10,
        ai_model="llama-3.3-70b-versatile",
        enable_cooldown=False,
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
        enable_smart_reentry=True,
        smart_reentry_type="RSI",
        smart_reentry_rsi_call_max=70.0,
        smart_reentry_rsi_put_min=30.0,
        smart_reentry_vwap_call_max_dist_pct=0.5,
        smart_reentry_vwap_put_max_dist_pct=0.5,
        enable_risk=False,
        entry_atr_multiplier=0.85,
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
        "name": name,
        "indicators": indicators,
        "summary": summary,
        "trades": res.get("trades", []),
        "settings": kwargs,
    }


def main():
    print("[manual] Loading data and indicators...")
    active_df = _fetch_data(TICKER, TIMEFRAME, START, END, MARKET_SESSION)
    needed = [
        "RSI",
        "Bollinger",
        "VWAP",
        "BotBulls2",
        "BotBulls3",
        "Swing Points",
        "Pivot Points (S/R)",
        "Prior Day High/Low",
    ]
    signal_cache = _build_signal_cache(active_df, needed)
    print(f"[manual] Loaded {len(active_df)} bars; signal cache ready.")

    candidates = [
        ("core_rsi_boll_vwap", ["RSI", "Bollinger", "VWAP"]),
        ("core_plus_bb2", ["RSI", "Bollinger", "VWAP", "BotBulls2"]),
        ("core_plus_bb3", ["RSI", "Bollinger", "VWAP", "BotBulls3"]),
        ("core_plus_bb2_bb3", ["RSI", "Bollinger", "VWAP", "BotBulls2", "BotBulls3"]),
        ("core_plus_swing", ["RSI", "Bollinger", "VWAP", "Swing Points"]),
        ("core_plus_pivot", ["RSI", "Bollinger", "VWAP", "Pivot Points (S/R)"]),
    ]

    print("[manual] Running targeted validations...")
    results = []
    with ProcessPoolExecutor(max_workers=2, initializer=_init_worker, initargs=(active_df, signal_cache)) as ex:
        futures = {ex.submit(_run_case, case): case[0] for case in candidates}
        completed = 0
        for fut in as_completed(futures):
            completed += 1
            name = futures[fut]
            try:
                row = fut.result()
                results.append(row)
                print(
                    f"[manual] {completed}/{len(candidates)} done: {name} -> ROI {row['summary'].get('roi_pct', 0.0):.2f}% | "
                    f"Equity ${row['summary'].get('final_equity', 0.0):.2f}"
                )
            except Exception as exc:
                print(f"[manual] {name} failed: {exc}")

    results.sort(key=lambda r: (r["summary"].get("roi_pct", 0.0), r["summary"].get("final_equity", 0.0)), reverse=True)
    out = {
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
    with open("backend/manual_targeted_validation.json", "w", encoding="utf-8") as f:
        json.dump(out, f, indent=2, default=str)

    print("\n=== Targeted Results ===")
    for idx, row in enumerate(results, 1):
        s = row["summary"]
        print(
            f"[{idx}] {row['name']} | ROI {s.get('roi_pct', 0.0):.2f}% | Equity ${s.get('final_equity', 0.0):.2f} | "
            f"Trades {s.get('total_trades', 0)} | Win {s.get('win_rate_pct', 0.0):.2f}%"
        )


if __name__ == "__main__":
    main()
