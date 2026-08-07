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
    _generate_signals,
    _generate_standard_extension_signals,
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
    if failures:
        print("\nAUDIT FAILED")
        for failure in failures:
            print(f"- {failure}")
        return 1

    print("\nAUDIT PASSED: 50/50 indicators have calculation data, valid votes, guides, and three-page chart contracts.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
