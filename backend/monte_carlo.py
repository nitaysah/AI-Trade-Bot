"""
Monte Carlo Simulation for Backtesting.

Takes a completed trade list and randomly shuffles the trade order N times
to build a distribution of possible outcomes. This reveals whether the
strategy's backtest performance is a genuine edge or just lucky sequencing.

Key outputs:
- Distribution of Sharpe Ratios across all simulations
- Overfitting risk classification (LOW / MEDIUM / HIGH)
- Fan chart data (downsampled equity curves for each simulation)
"""

import numpy as np
import random


def _compute_sharpe(returns: np.ndarray, n_per_year: int = 252) -> float:
    """Annualised Sharpe from a trade-level return series."""
    if len(returns) < 2 or returns.std() == 0:
        return 0.0
    return float((returns.mean() / returns.std()) * np.sqrt(n_per_year))


def _compute_max_drawdown(equity_curve: np.ndarray) -> float:
    """Peak-to-trough max drawdown as a positive percentage."""
    peak = equity_curve[0]
    max_dd = 0.0
    for v in equity_curve:
        if v > peak:
            peak = v
        dd = (peak - v) / peak * 100 if peak > 0 else 0
        if dd > max_dd:
            max_dd = dd
    return max_dd


def _replay_equity(pl_list: list, initial_capital: float) -> np.ndarray:
    """Replay an ordered P/L list into an equity curve array."""
    equity = initial_capital
    curve = [equity]
    for pl in pl_list:
        equity += pl
        curve.append(equity)
    return np.array(curve)


def run_monte_carlo(
    trades: list,
    initial_capital: float,
    n_simulations: int = 1000,
    max_fan_curves: int = 200,
) -> dict:
    """
    Run Monte Carlo simulation on a completed trade list.

    Args:
        trades:          List of trade dicts with at least {'pl', 'entry_cost'} keys.
        initial_capital: Starting capital for equity replay.
        n_simulations:   Number of random shuffles to run.
        max_fan_curves:  Max number of equity curves to return for charting.

    Returns:
        dict with summary stats, overfitting risk, and fan chart data.
    """
    if not trades or len(trades) < 3:
        return {"error": "Need at least 3 trades to run Monte Carlo simulation."}

    # Extract P/L and per-trade returns
    pl_values    = [t.get("pl", 0.0) for t in trades]
    entry_costs  = [t.get("entry_cost", initial_capital) for t in trades]
    returns_base = np.array([
        pl / ec if ec > 0 else 0.0
        for pl, ec in zip(pl_values, entry_costs)
    ])

    # Original (actual) backtest metrics
    original_equity = _replay_equity(pl_values, initial_capital)
    original_sharpe = _compute_sharpe(returns_base)
    original_max_dd = _compute_max_drawdown(original_equity)

    # ── Run Simulations ──────────────────────────────────────────────────────
    sim_sharpes  = []
    sim_max_dds  = []
    fan_curves   = []  # downsampled equity curves for charting

    rng = random.Random(42)  # deterministic seed for reproducibility

    for i in range(n_simulations):
        shuffled = pl_values[:]
        rng.shuffle(shuffled)

        sim_equity  = _replay_equity(shuffled, initial_capital)
        sim_returns = np.array([
            pl / ec if ec > 0 else 0.0
            for pl, ec in zip(shuffled, entry_costs)
        ])

        sim_sharpes.append(_compute_sharpe(sim_returns))
        sim_max_dds.append(_compute_max_drawdown(sim_equity))

        # Collect equity curves (downsampled) for the fan chart
        if i < max_fan_curves:
            # Downsample to max 100 points per curve
            step = max(1, len(sim_equity) // 100)
            fan_curves.append([round(float(v), 2) for v in sim_equity[::step]])

    # ── Compute Percentile Statistics ────────────────────────────────────────
    sharpes_arr = np.array(sim_sharpes)
    dds_arr     = np.array(sim_max_dds)

    percentile_rank = float(np.mean(sharpes_arr <= original_sharpe)) * 100

    # Overfitting Risk Classification
    if percentile_rank <= 75:
        overfitting_risk = "LOW"
        risk_description = "Strategy edge appears robust — original result sits near the median of simulations."
    elif percentile_rank <= 90:
        overfitting_risk = "MEDIUM"
        risk_description = "Possible overfitting detected — original result is in the upper quartile of simulations."
    else:
        overfitting_risk = "HIGH"
        risk_description = "Very likely overfit — original result is an outlier vs random trade ordering."

    # Downsample the original equity curve for charting
    step = max(1, len(original_equity) // 100)
    original_curve_downsampled = [round(float(v), 2) for v in original_equity[::step]]

    return {
        "n_simulations": n_simulations,
        "original_sharpe": round(original_sharpe, 2),
        "original_max_dd": round(original_max_dd, 2),
        "median_sharpe": round(float(np.median(sharpes_arr)), 2),
        "mean_sharpe": round(float(sharpes_arr.mean()), 2),
        "best_5pct_sharpe": round(float(np.percentile(sharpes_arr, 95)), 2),
        "worst_5pct_sharpe": round(float(np.percentile(sharpes_arr, 5)), 2),
        "median_max_dd": round(float(np.median(dds_arr)), 2),
        "best_5pct_max_dd": round(float(np.percentile(dds_arr, 5)), 2),
        "percentile_rank": round(percentile_rank, 1),
        "overfitting_risk": overfitting_risk,
        "risk_description": risk_description,
        "original_equity_curve": original_curve_downsampled,
        "fan_curves": fan_curves,
        "initial_capital": initial_capital,
    }
