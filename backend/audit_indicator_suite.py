#!/usr/bin/env python3
"""Deterministic 50-indicator calculation, signal, guide, and chart audit.

Run from the repository root:
    PYTHONPATH=backend python3 backend/audit_indicator_suite.py
"""

from __future__ import annotations

import json
import math
import subprocess
import sys
from pathlib import Path

import numpy as np
import pandas as pd

from backtester import _chart_indicator_fields as regular_backtest_fields
from indicator_catalog import (
    STANDARD_CHART_GROUPS,
    STANDARD_CHART_PLACEMENT,
    STANDARD_INDICATOR_NAMES,
)
from indicators import (
    _generate_macd_signal,
    _generate_moving_average_signal,
    _generate_rsi_signal,
    _generate_signals,
    _generate_standard_extension_signals,
    _wilder_rsi,
    calculate_indicators_for_df,
)
from options_backtester import _chart_indicator_fields as options_backtest_fields


ROOT = Path(__file__).resolve().parents[1]
VALID_VOTES = {"BULLISH", "BEARISH", "NEUTRAL"}


def synthetic_history(size: int = 700) -> pd.DataFrame:
    """Create multi-session OHLCV data with trends, ranges, and reversals."""
    rng = np.random.default_rng(20260728)
    index = pd.date_range("2026-01-01", periods=size, freq="15min", tz="UTC")
    x = np.arange(size, dtype=float)
    close = 100 + (0.035 * x) + (3.8 * np.sin(x / 13)) + (1.4 * np.sin(x / 37))
    close += rng.normal(0, 0.22, size)
    open_ = np.r_[close[0], close[:-1]] + rng.normal(0, 0.12, size)
    spread = 0.55 + np.abs(rng.normal(0, 0.15, size))
    return pd.DataFrame(
        {
            "Open": open_,
            "High": np.maximum(open_, close) + spread,
            "Low": np.minimum(open_, close) - spread,
            "Close": close,
            "Volume": rng.integers(10_000, 220_000, size),
        },
        index=index,
    )


def has_usable_value(history: list[dict], field: str) -> bool:
    for bar in history:
        if field not in bar:
            continue
        value = bar[field]
        if isinstance(value, bool):
            return True
        if isinstance(value, str):
            return bool(value)
        try:
            if value is not None and math.isfinite(float(value)):
                return True
        except (TypeError, ValueError):
            pass
    return False


def frontend_metadata() -> dict:
    """Evaluate the two dependency-free catalog files with a minimal DOM."""
    script = r"""
const fs=require('fs'),vm=require('vm');
function element(){return {appendChild(){},querySelectorAll(){return[]},addEventListener(){},
classList:{add(){},remove(){},toggle(){}},setAttribute(){},style:{},dataset:{}}}
const ctx={window:{},document:{readyState:'complete',getElementById(){return null},
createElement:element,addEventListener(){}},ResizeObserver:function(){this.observe=()=>{};
this.disconnect=()=>{}},console}; ctx.window=ctx; vm.createContext(ctx);
for(const file of ['frontend/indicator-catalog.js','frontend/indicator-subcharts.js'])
  vm.runInContext(fs.readFileSync(file,'utf8'),ctx,{filename:file});
process.stdout.write(JSON.stringify({
  names:ctx.STANDARD_INDICATOR_NAMES,
  guides:Object.keys(ctx.INDICATOR_GUIDES),
  subcharts:ctx.STANDARD_SUBCHART_INDICATOR_NAMES,
  overlays:ctx.STANDARD_EXTENSION_OVERLAYS.map(x=>x.name),
  shifted:ctx.STANDARD_EXTENSION_OVERLAYS.flatMap(x=>x.series
    .filter(s=>s.shiftPeriodsKey).map(s=>({name:x.name,field:s.field,key:s.shiftPeriodsKey})))
}));
"""
    completed = subprocess.run(
        ["node"],
        cwd=ROOT,
        input=script,
        text=True,
        capture_output=True,
        check=True,
    )
    return json.loads(completed.stdout)


def signal_rule_probes() -> list[str]:
    """Exercise the highest-risk directional rules with controlled snapshots."""
    failures: list[str] = []

    base = {
        "Close": 100.0,
        "BOLL_Lower": 90.0,
        "BOLL_Upper": 110.0,
        "VWAP": 100.0,
        "cdl_bullish": False,
    }
    rsi_bull = _generate_signals(
        pd.Series({**base, "RSI": 25.0}),
        pd.Series({**base, "RSI": 20.0}),
        settings={"enabled_indicators": ["RSI"]},
    )
    rsi_bear = _generate_signals(
        pd.Series({**base, "RSI": 75.0}),
        pd.Series({**base, "RSI": 80.0}),
        settings={"enabled_indicators": ["RSI"]},
    )
    if rsi_bull.get("RSI", {}).get("signal") != "BULLISH":
        failures.append("RSI rising-oversold probe")
    if rsi_bear.get("RSI", {}).get("signal") != "BEARISH":
        failures.append("RSI falling-overbought probe")

    defaults = lambda _key, fallback=None: fallback
    neutral = {
        field: 0.0
        for fields in STANDARD_CHART_GROUPS.values()
        for field in fields
    }
    neutral.update(
        {
            "Close": 100.0,
            "Keltner_Mid": 100.0,
            "Ichimoku_Conversion": 100.0,
            "Ichimoku_Base": 100.0,
            "Ichimoku_Span_A": 100.0,
            "Ichimoku_Span_B": 100.0,
        }
    )

    probes = [
        ("CCI", {"CCI_Ext": -120.0}, {"CCI_Ext": -140.0}, "BULLISH"),
        ("CCI", {"CCI_Ext": 120.0}, {"CCI_Ext": 140.0}, "BEARISH"),
        ("Momentum", {"Momentum_Ext": 2.0}, {"Momentum_Ext": 1.0}, "BULLISH"),
        ("Momentum", {"Momentum_Ext": -2.0}, {"Momentum_Ext": -1.0}, "BEARISH"),
        (
            "Ichimoku Cloud",
            {
                "Close": 115.0,
                "Ichimoku_Conversion": 110.0,
                "Ichimoku_Base": 105.0,
                "Ichimoku_Span_A": 102.0,
                "Ichimoku_Span_B": 98.0,
            },
            {},
            "BULLISH",
        ),
        (
            "Ichimoku Cloud",
            {
                "Close": 85.0,
                "Ichimoku_Conversion": 90.0,
                "Ichimoku_Base": 95.0,
                "Ichimoku_Span_A": 98.0,
                "Ichimoku_Span_B": 102.0,
            },
            {},
            "BEARISH",
        ),
    ]
    for name, latest_changes, previous_changes, expected in probes:
        latest = pd.Series({**neutral, **latest_changes})
        previous = pd.Series({**neutral, **previous_changes})
        actual = _generate_standard_extension_signals(latest, previous, defaults)
        if actual.get(name, {}).get("signal") != expected:
            failures.append(f"{name} {expected.lower()} probe")
    return failures


def ema_adx_rule_probes(dataframe: pd.DataFrame) -> list[str]:
    """Keep EMA direction independent from the separate ADX strength vote."""
    failures: list[str] = []
    latest = dataframe.iloc[-1].copy()
    previous = dataframe.iloc[-2].copy()
    latest["EMA_Fast"], latest["EMA_Slow"] = 105.0, 100.0
    previous["EMA_Fast"], previous["EMA_Slow"] = 99.0, 100.0
    latest["DI_Plus"], latest["DI_Minus"] = 28.0, 18.0
    latest["ADX"], previous["ADX"] = 20.0, 22.0
    signals = _generate_signals(
        latest,
        previous,
        settings={"enabled_indicators": ["EMA Cross", "ADX Trend"]},
    )
    if signals.get("EMA Cross", {}).get("signal") != "BULLISH":
        failures.append("EMA bullish crossover must not be suppressed by weak ADX")
    if signals.get("ADX Trend", {}).get("signal") != "NEUTRAL":
        failures.append("ADX below threshold must remain neutral")

    latest["ADX"], previous["ADX"] = 30.0, 35.0
    signals = _generate_signals(
        latest,
        previous,
        settings={"enabled_indicators": ["EMA Cross", "ADX Trend"]},
    )
    if signals.get("ADX Trend", {}).get("signal") != "BULLISH":
        failures.append("Strong declining ADX must retain +DI/-DI direction")
    return failures


def moving_average_mode_probes() -> list[str]:
    """Verify one-line price mode and two-line crossover mode each emit one vote."""
    failures: list[str] = []
    ema_one_line = _generate_moving_average_signal(
        average_type="EMA",
        price=99.0,
        primary_value=100.0,
        previous_primary=98.0,
        primary_period=9,
        use_second_line=False,
    )
    if ema_one_line.get("signal") != "BEARISH" or ema_one_line.get("line_mode") != 1:
        failures.append("EMA one-line mode must use price versus primary EMA")

    sma_cross = _generate_moving_average_signal(
        average_type="SMA",
        price=100.0,
        primary_value=100.0,
        previous_primary=100.0,
        primary_period=200,
        use_second_line=True,
        secondary_value=105.0,
        previous_secondary=99.0,
        secondary_period=50,
    )
    if sma_cross.get("signal") != "BULLISH" or "crossover" not in sma_cross.get("reason", "").lower():
        failures.append("SMA two-line mode must detect the lower-period bullish crossover")
    if sma_cross.get("weight") != 1 or sma_cross.get("line_mode") != 2:
        failures.append("SMA two-line mode must contribute exactly one vote")

    equal_periods = _generate_moving_average_signal(
        average_type="EMA",
        price=100.0,
        primary_value=100.0,
        previous_primary=99.0,
        primary_period=20,
        use_second_line=True,
        secondary_value=101.0,
        previous_secondary=100.0,
        secondary_period=20,
    )
    if equal_periods.get("signal") != "NEUTRAL":
        failures.append("Equal two-line moving-average periods must remain neutral")
    return failures


def rsi_macd_rule_probes(dataframe: pd.DataFrame) -> list[str]:
    """Verify textbook RSI, causal warm-up, MACD normalization, and vote text."""
    failures: list[str] = []

    sample = pd.Series([10.0, 11.0, 13.0, 12.0, 14.0])
    sample_rsi = _wilder_rsi(sample, 3)
    if not sample_rsi.iloc[:3].isna().all():
        failures.append("RSI textbook seed must not backfill its warm-up rows")
    if not math.isclose(float(sample_rsi.iloc[3]), 75.0, rel_tol=1e-9):
        failures.append("RSI textbook Wilder seed is incorrect")
    if not math.isclose(float(sample_rsi.iloc[4]), 85.71428571428571, rel_tol=1e-9):
        failures.append("RSI recursive Wilder smoothing is incorrect")

    flat_rsi = _wilder_rsi(pd.Series([10.0] * 8), 3)
    if not (flat_rsi.dropna() == 50.0).all():
        failures.append("Flat-price RSI must remain neutral at 50")

    warm_rsi_vote = _generate_rsi_signal(np.nan, np.nan, period=14)
    if warm_rsi_vote.get("signal") != "NEUTRAL" or "warming up" not in warm_rsi_vote.get("reason", "").lower():
        failures.append("RSI warm-up must return an explicit neutral vote")
    reversed_threshold_vote = _generate_rsi_signal(25.0, 20.0, oversold=70, overbought=30)
    if reversed_threshold_vote.get("signal") != "BULLISH":
        failures.append("RSI reversed thresholds must be normalized lower-to-higher")

    positive_macd = _generate_macd_signal(0.25, 0.30)
    if positive_macd.get("signal") != "BULLISH" or "positive" not in positive_macd.get("reason", "").lower():
        failures.append("MACD positive histogram state text is inaccurate")
    negative_macd = _generate_macd_signal(-0.25, -0.30)
    if negative_macd.get("signal") != "BEARISH" or "negative" not in negative_macd.get("reason", "").lower():
        failures.append("MACD negative histogram state text is inaccurate")
    warm_macd_vote = _generate_macd_signal(np.nan, np.nan)
    if warm_macd_vote.get("signal") != "NEUTRAL" or "warming up" not in warm_macd_vote.get("reason", "").lower():
        failures.append("MACD warm-up must return an explicit neutral vote")
    equal_macd_vote = _generate_macd_signal(0.0, 0.0, fast_period=12, slow_period=12)
    if equal_macd_vote.get("signal") != "NEUTRAL" or "different" not in equal_macd_vote.get("reason", "").lower():
        failures.append("Equal MACD EMA periods must remain neutral")

    if not dataframe["RSI"].iloc[:14].isna().all() or not pd.notna(dataframe["RSI"].iloc[14]):
        failures.append("RSI warm-up must require 14 completed price changes")
    if not dataframe["MACD_Line"].iloc[:25].isna().all() or not pd.notna(dataframe["MACD_Line"].iloc[25]):
        failures.append("MACD line warm-up must remain causal")
    if not dataframe["MACD_Hist"].iloc[:33].isna().all() or not pd.notna(dataframe["MACD_Hist"].iloc[33]):
        failures.append("MACD histogram warm-up must remain causal")

    history = synthetic_history(120)
    normal = calculate_indicators_for_df(
        history.copy(),
        timeframe="15Min",
        ticker="AUDIT",
        settings={
            "enabled_indicators": ["MACD"],
            "MACD_FAST": 12,
            "MACD_SLOW": 26,
            "MACD_SIGNAL": 9,
        },
    )
    reversed_periods = calculate_indicators_for_df(
        history.copy(),
        timeframe="15Min",
        ticker="AUDIT",
        settings={
            "enabled_indicators": ["MACD"],
            "MACD_FAST": 26,
            "MACD_SLOW": 12,
            "MACD_SIGNAL": 9,
        },
    )
    if not normal or not reversed_periods:
        failures.append("MACD reversed-period calculation did not complete")
    else:
        normal_hist = normal["df"]["MACD_Hist"]
        reversed_hist = reversed_periods["df"]["MACD_Hist"]
        if not np.allclose(normal_hist, reversed_hist, equal_nan=True):
            failures.append("MACD must treat the lower EMA period as fast")
    return failures


def main() -> int:
    names = list(STANDARD_INDICATOR_NAMES)
    failures: list[str] = []
    result = calculate_indicators_for_df(
        synthetic_history(),
        timeframe="15Min",
        ticker="AUDIT",
        settings={"enabled_indicators": names},
    )
    if not result:
        print("FAIL: indicator calculation returned no result", file=sys.stderr)
        return 1

    history = result["price_history"]
    signals = result["signals"]
    dataframe = result["df"]
    dataframe_row = dataframe.iloc[-1]
    frontend = frontend_metadata()
    frontend_names = set(frontend["names"])
    guides = set(frontend["guides"])
    subcharts = set(frontend["subcharts"])
    overlays = set(frontend["overlays"])

    renderer_sources = {
        page: (ROOT / "frontend" / filename).read_text()
        for page, filename in {
            "Dashboard": "app.js",
            "Bots": "bots.js",
            "Backtest": "backtest.js",
        }.items()
    }
    for page, source in renderer_sources.items():
        if "STANDARD_EXTENSION_OVERLAYS" not in source or "StandardIndicatorSubcharts" not in source:
            failures.append(f"{page}: shared 50-indicator renderer missing")

    print(f"{'Indicator':38} {'Chart':9} {'Calc':5} {'Vote':5} {'Guide':5} {'3 pages':7}")
    print("-" * 82)
    for name in names:
        expected_fields = STANDARD_CHART_GROUPS[name]
        calculation_ok = all(has_usable_value(history, field) for field in expected_fields)
        vote_ok = name in signals and signals[name].get("signal") in VALID_VOTES
        guide_ok = name in guides
        regular_fields = regular_backtest_fields(dataframe_row, 6, [name])
        options_fields = options_backtest_fields(dataframe_row, 6, [name])
        backtests_ok = all(field in regular_fields and field in options_fields for field in expected_fields)
        placement = STANDARD_CHART_PLACEMENT[name]
        frontend_place_ok = name in (subcharts if placement == "subchart" else overlays)
        if name in {
            "EMA Cross", "Supertrend", "Bollinger", "VWAP", "Volume Profile",
            "SMA", "Prior Day High/Low", "Pivot Points (S/R)", "Swing Points",
            "Candle Patterns",
        }:
            frontend_place_ok = True  # Dedicated legacy main-chart renderers.
        pages_ok = backtests_ok and frontend_place_ok and name in frontend_names

        row_status = (
            "PASS" if calculation_ok else "FAIL",
            "PASS" if vote_ok else "FAIL",
            "PASS" if guide_ok else "FAIL",
            "PASS" if pages_ok else "FAIL",
        )
        print(f"{name:38} {placement:9} {row_status[0]:5} {row_status[1]:5} {row_status[2]:5} {row_status[3]:7}")
        if not all((calculation_ok, vote_ok, guide_ok, pages_ok)):
            failures.append(
                f"{name}: calc={calculation_ok}, vote={vote_ok}, guide={guide_ok}, pages={pages_ok}"
            )

    shifted = {(item["name"], item["field"], item["key"]) for item in frontend["shifted"]}
    expected_shifted = {
        ("Ichimoku Cloud", "ichimoku_span_a", "ICHIMOKU_BASE"),
        ("Ichimoku Cloud", "ichimoku_span_b", "ICHIMOKU_BASE"),
    }
    if not expected_shifted.issubset(shifted):
        failures.append("Ichimoku leading spans are not configured for forward displacement")

    for column, lower, upper in (
        ("RSI", 0, 100),
        ("Stoch_K", 0, 100),
        ("Stoch_RSI_K", 0, 100),
        ("Williams_R", -100, 0),
        ("MFI_Ext", 0, 100),
        ("Ultimate_Osc", 0, 100),
        ("Aroon_Up", 0, 100),
        ("Aroon_Down", 0, 100),
        ("ADX", 0, 100),
        ("DI_Plus", 0, 100),
        ("DI_Minus", 0, 100),
    ):
        values = dataframe[column].dropna()
        if values.empty or not values.between(lower, upper).all():
            failures.append(f"{column}: values outside expected [{lower}, {upper}] range")

    for upper, lower in (
        ("BOLL_Upper", "BOLL_Middle"),
        ("BOLL_Middle", "BOLL_Lower"),
        ("Keltner_Upper", "Keltner_Mid"),
        ("Keltner_Mid", "Keltner_Lower"),
        ("Donchian_Upper_Prior", "Donchian_Lower_Prior"),
    ):
        pair = dataframe[[upper, lower]].dropna()
        if pair.empty or not (pair[upper] >= pair[lower]).all():
            failures.append(f"{upper}/{lower}: channel ordering failed")

    failures.extend(signal_rule_probes())
    failures.extend(ema_adx_rule_probes(dataframe))
    failures.extend(moving_average_mode_probes())
    failures.extend(rsi_macd_rule_probes(dataframe))
    if not dataframe["EMA_Slow"].iloc[:20].isna().all():
        failures.append("EMA slow warm-up values were back-filled into earlier candles")
    if not dataframe["SMA"].iloc[:199].isna().all():
        failures.append("Primary SMA warm-up values were back-filled into earlier candles")
    if not dataframe["SMA_Secondary"].iloc[:49].isna().all():
        failures.append("Secondary SMA warm-up values were back-filled into earlier candles")
    if not dataframe["ADX"].iloc[:26].isna().all():
        failures.append("ADX warm-up values were back-filled into earlier candles")
    if failures:
        print("\nAUDIT FAILED")
        for failure in failures:
            print(f"- {failure}")
        return 1

    print("\nAUDIT PASSED: 50/50 indicators have calculation data, valid votes, guides, and three-page chart contracts.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
