import asyncio
import itertools
from datetime import timedelta
import pandas as pd
from data_manager import get_historical_data
from indicators import calculate_indicators_for_df
from backtester import Backtester
import concurrent.futures

MAX_OPTIMIZER_COMBINATIONS = 128
MAX_OPTIMIZER_CONCURRENCY = 4

def run_single_backtest(params):
    """Runs a single backtest and returns its sharpe ratio or a fallback score."""
    kwargs, precomputed_df, df_len = params
    
    bt = Backtester(**kwargs)
    try:
        # Run backtest passing the precomputed dataframe
        res = bt.run(precomputed_df=precomputed_df)
        
        if "error" in res:
            return kwargs, -999.0, res
            
        summary = res.get("summary", {})
        
        # Use Sharpe Ratio primarily, fallback to ROI if Sharpe is invalid/zero
        sharpe = summary.get("sharpe_ratio", 0.0)
        roi = summary.get("roi_pct", 0.0)
        
        score = sharpe if sharpe != 0.0 else (roi / 100.0)
        
        # Severe penalty for strategies that barely trade
        if summary.get("total_trades", 0) < max(2, df_len / 500):
            score -= 5.0
            
        return kwargs, score, res
    except Exception as e:
        import traceback
        print(f"[optimizer] Worker Exception: {e}\n{traceback.format_exc()}")
        return kwargs, -999.0, {"error": str(e)}

async def run_optimization(base_kwargs, split_pct=30):
    """
    Runs an Auto-Optimization grid search.
    split_pct: Percentage of days to use for Out-of-Sample testing.
    """
    start_date = base_kwargs["start_date"]
    end_date = base_kwargs["end_date"]
    
    # Force naive datetimes to match pandas index
    if hasattr(start_date, "tzinfo") and start_date.tzinfo is not None:
        start_date = start_date.replace(tzinfo=None)
    if hasattr(end_date, "tzinfo") and end_date.tzinfo is not None:
        end_date = end_date.replace(tzinfo=None)
    
    base_kwargs["start_date"] = start_date
    base_kwargs["end_date"] = end_date
    
    enabled_indicators = base_kwargs.get("enabled_indicators", [])
    print(f"[optimizer] Starting grid search for {base_kwargs['ticker']} | TF: {base_kwargs['timeframe']} | Indicators: {enabled_indicators}")
    
    # 1. Fetch massive padded dataframe ONCE
    padded_start = start_date - timedelta(days=100)
    raw_df = get_historical_data(
        base_kwargs["ticker"], 
        base_kwargs["timeframe"], 
        padded_start, 
        end_date, 
        force_yahoo=True
    )
    
    if raw_df is None or raw_df.empty:
        print("[optimizer] Error: No data found for the selected range.")
        return {"error": "No data found for the selected range."}
        
    # RTH Filter
    clean_ticker = base_kwargs["ticker"].upper().replace("/", "")
    is_crypto = any(clean_ticker.endswith(base) for base in ["USD", "USDT", "USDC"])
    if not is_crypto and not base_kwargs.get("ext_hours", True) and base_kwargs["timeframe"] != "1Day":
        try:
            time_idx = raw_df.index.time
            rth_mask = (time_idx >= pd.to_datetime("09:30:00").time()) & (time_idx <= pd.to_datetime("16:00:00").time())
            raw_df = raw_df[rth_mask]
        except Exception:
            pass

    # 2. Calculate Indicators ONCE
    indicator_settings = {
        "DAILY_HIGH_LOW_BUFFER_PCT": base_kwargs.get("daily_high_low_buffer_pct", 0.0),
    }
    result = calculate_indicators_for_df(raw_df, base_kwargs["timeframe"], base_kwargs["ticker"], settings=indicator_settings)
    if not result:
        print("[optimizer] Error: Failed to calculate indicators.")
        return {"error": "Failed to calculate indicators."}
    
    full_df = result['df']
    
    # Strip timezone from the full_df index if present (safety net)
    if hasattr(full_df.index, 'tz') and full_df.index.tz is not None:
        full_df.index = full_df.index.tz_localize(None)
    
    # 3. Slice and Split
    active_df = full_df.loc[start_date:end_date]
    if active_df.empty:
        print(f"[optimizer] Error: No data available in range {start_date} to {end_date}. full_df span: {full_df.index[0]} to {full_df.index[-1]}")
        return {"error": "No data available in the requested range."}
        
    total_bars = len(active_df)
    oos_bars = int(total_bars * (split_pct / 100.0))
    is_bars = total_bars - oos_bars
    
    if is_bars < 10 or oos_bars < 5:
        print(f"[optimizer] Error: Not enough data. total_bars={total_bars}, is_bars={is_bars}, oos_bars={oos_bars}")
        return {"error": "Not enough data to split into In-Sample and Out-of-Sample reliably."}
        
    df_insample = active_df.iloc[:is_bars]
    df_outsample = active_df.iloc[is_bars:]
    
    print(f"[optimizer] Data split: {total_bars} total bars -> IS: {is_bars}, OOS: {oos_bars}")
    
    # 4. Define Parameter Grid
    num_inds = len(enabled_indicators)
    if num_inds == 0:
        return {"error": "You must enable at least one indicator to use the Auto-Optimizer."}
        
    # Dynamically scale thresholds so they are always reachable
    # threshold = minimum number of BULLISH signals needed to BUY
    # sell_threshold = minimum number of BEARISH signals needed to SELL
    # These can NEVER exceed num_inds, or no trades will ever fire
    if num_inds == 1:
        thresholds = [1]
        sell_thresholds = [1]
    elif num_inds == 2:
        thresholds = [1, 2]
        sell_thresholds = [1, 2]
    elif num_inds == 3:
        thresholds = [1, 2, 3]
        sell_thresholds = [1, 2, 3]
    elif num_inds <= 5:
        thresholds = [1, 2, 3, num_inds]
        sell_thresholds = [1, 2, 3]
    else:
        # For 6+ indicators, test a reasonable spread
        thresholds = [1, 2, max(3, num_inds // 2), num_inds]
        sell_thresholds = [1, 2, max(3, num_inds // 2)]

    # Deduplicate and sort
    thresholds = sorted(list(set(thresholds)))
    sell_thresholds = sorted(list(set(sell_thresholds)))

    grid = {
        "threshold": thresholds,
        "sell_threshold": sell_thresholds,
        "atr_stop_multiplier": [1.5, 2.0, 2.5],
        "atr_take_profit_multiplier": [2.0, 3.0, 4.0]
    }
    
    # Generate all combinations
    keys, values = zip(*grid.items())
    combinations = [dict(zip(keys, v)) for v in itertools.product(*values)]
    if len(combinations) > MAX_OPTIMIZER_COMBINATIONS:
        return {"error": f"Optimization grid is limited to {MAX_OPTIMIZER_COMBINATIONS} combinations."}
    
    print(f"[optimizer] Grid: {num_inds} indicators -> buy thresholds {thresholds}, sell thresholds {sell_thresholds}")
    print(f"[optimizer] Testing {len(combinations)} parameter combinations...")
    
    tasks = []
    for combo in combinations:
        kw = base_kwargs.copy()
        kw.update(combo)
        
        # Override dates for In-Sample
        kw["start_date"] = df_insample.index[0]
        kw["end_date"] = df_insample.index[-1]
        
        tasks.append((kw, df_insample, is_bars))
        
    # 5. Execute Grid Search
    # Using to_thread (ThreadPool) to avoid pickling issues with threading.Event in kwargs and DataFrame overhead
    semaphore = asyncio.Semaphore(MAX_OPTIMIZER_CONCURRENCY)

    async def _run_bounded(task):
        async with semaphore:
            return await asyncio.to_thread(run_single_backtest, task)

    results = await asyncio.gather(*[_run_bounded(task) for task in tasks])
        
    # 6. Find Best Parameter Set
    best_score = -9999.0
    best_params = None
    best_is_res = None
    
    valid_count = 0
    error_count = 0
    for kw, score, res in results:
        if "error" in res:
            error_count += 1
        else:
            valid_count += 1
        if score > best_score:
            best_score = score
            best_params = kw
            best_is_res = res
    
    print(f"[optimizer] Results: {valid_count} successful, {error_count} errors out of {len(results)} total")
            
    if best_params is None or best_is_res is None or "error" in best_is_res:
        print(f"[optimizer] FAILED: All {len(results)} combos returned errors. Best score: {best_score}")
        return {"error": "Optimization failed to find a valid strategy. Try enabling more indicators or using a longer time period."}
        
    print(f"[optimizer] Best combo: thresh={best_params.get('threshold')}, sell_thresh={best_params.get('sell_threshold')}, ATR_SL={best_params.get('atr_stop_multiplier')}, ATR_TP={best_params.get('atr_take_profit_multiplier')} | Score: {best_score:.3f}")
    
    # 7. Run Out-of-Sample Validation
    oos_kw = base_kwargs.copy()
    oos_kw.update({k: best_params[k] for k in grid.keys()})
    oos_kw["start_date"] = df_outsample.index[0]
    oos_kw["end_date"] = df_outsample.index[-1]
    
    bt_oos = Backtester(**oos_kw)
    oos_res = await asyncio.to_thread(bt_oos.run, precomputed_df=df_outsample)
    
    if "error" in oos_res:
        print(f"[optimizer] OOS validation failed: {oos_res['error']}")
        return {"error": f"Out-of-Sample validation failed: {oos_res['error']}"}
        
    # 8. Compute Overfit Score
    is_sharpe = best_is_res["summary"].get("sharpe_ratio", 0)
    oos_sharpe = oos_res["summary"].get("sharpe_ratio", 0)
    
    if is_sharpe > 0 and oos_sharpe > 0:
        overfit_score = oos_sharpe / is_sharpe
    elif is_sharpe <= 0 and oos_sharpe > 0:
        overfit_score = 1.5 # Robust improvement
    else:
        overfit_score = 0.0 # Failed
    
    print(f"[optimizer] SUCCESS! IS Sharpe: {is_sharpe}, OOS Sharpe: {oos_sharpe}, Overfit Score: {overfit_score:.2f}")
        
    return {
        "status": "success",
        "insample_results": best_is_res,
        "outsample_results": oos_res,
        "best_parameters": {k: best_params[k] for k in grid.keys()},
        "overfit_score": round(overfit_score, 2),
        "split_pct": split_pct
    }
