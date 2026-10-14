"""Malik leveraged-ETF trend allocator: pure rules shared by Backtest, Screener and Bots.

The signal is computed on an unleveraged benchmark (QQQ, SOXX or IWM). Orders go
to a leveraged ETF the user picks (bull leg) and, in "Long + inverse" mode, an
inverse ETF (bear leg; bought long, never shorted). The strategy holds a target
percentage of its sleeve in each leg and rebalances once per trading day at a
chosen time before the close.

Timing model (no lookahead): at decision time T on day d the indicators use the
completed daily closes through d-1 plus one provisional price P (the benchmark's
price at T) standing in for day d. Nothing after T is used.

Research status: the 2026-10-09 pre-registered study (scratch/malik_research)
found that no pair/direction passed its gate. The strategies are offered for
research and paper trading with that warning attached; see ``RESEARCH``.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

import numpy as np
import pandas as pd

import nyse_calendar

KIND = "allocation"
DIRECTION_LONG_ONLY = "long_only"
DIRECTION_INVERSE = "long_inverse"
DIRECTIONS = (DIRECTION_LONG_ONLY, DIRECTION_INVERSE)
REBALANCE_BAND = 0.05  # trade a non-zero leg only when it is off target by more than 5% of the sleeve
MIN_ORDER_NOTIONAL = 1.0  # Alpaca's smallest notional order
MIN_COMPLETED_CLOSES = 250
WARMUP_BARS = 270  # completed daily bars requested so the 250-day average is always complete

EXEC_CLOSE_MINUS_15 = "close-15"
EXEC_DEFAULT = EXEC_CLOSE_MINUS_15
# "T-N" choices: N minutes before the close, every 15 minutes from T-15 to T-375 (9:45 AM ET on a normal day).
EXEC_RELATIVE_STEPS = tuple(range(15, 376, 15))
EXEC_EARLIEST = time(9, 35)
EXEC_MIN_BEFORE_CLOSE = timedelta(minutes=5)

STATUS_READY = "READY"
STATUS_WAITING = "WAITING_DATA"


@dataclass(frozen=True)
class EtfOption:
    symbol: str
    leverage: int  # +3, +2 bull; -3, -2 inverse
    label: str


@dataclass(frozen=True)
class AllocationSpec:
    signal: str
    bull: Tuple[EtfOption, ...]
    bear: Tuple[EtfOption, ...]


# ProShares Ultra Semiconductors trades as ``USD``; the app treats tickers ending in
# "USD" as crypto, so it is deliberately not offered (see the Malik plan).
SPECS: Dict[str, AllocationSpec] = {
    "malik_qqq": AllocationSpec(
        "QQQ",
        (EtfOption("TQQQ", 3, "TQQQ · 3x Nasdaq-100"), EtfOption("QLD", 2, "QLD · 2x Nasdaq-100")),
        (EtfOption("SQQQ", -3, "SQQQ · -3x Nasdaq-100"), EtfOption("QID", -2, "QID · -2x Nasdaq-100")),
    ),
    "malik_soxx": AllocationSpec(
        "SOXX",
        (EtfOption("SOXL", 3, "SOXL · 3x Semiconductors"),),
        (EtfOption("SOXS", -3, "SOXS · -3x Semiconductors"), EtfOption("SSG", -2, "SSG · -2x Semiconductors")),
    ),
    "malik_iwm": AllocationSpec(
        "IWM",
        (EtfOption("TNA", 3, "TNA · 3x Russell 2000"), EtfOption("UWM", 2, "UWM · 2x Russell 2000")),
        (EtfOption("TZA", -3, "TZA · -3x Russell 2000"), EtfOption("TWM", -2, "TWM · -2x Russell 2000")),
    ),
}

# Out-of-sample results (2019-01..2026-09, 15 min before close, long-only, 3x ETF) from the
# pre-registered study. Shown to the user next to every Malik strategy.
RESEARCH: Dict[str, Dict[str, Any]] = {
    "malik_qqq": {"cagr_pct": 34.1, "max_dd_pct": -48.6, "mar": 0.70, "sma_rule_mar": 0.78, "passed": False},
    "malik_soxx": {"cagr_pct": 27.9, "max_dd_pct": -72.0, "mar": 0.39, "sma_rule_mar": 0.57, "passed": False},
    "malik_iwm": {"cagr_pct": -4.2, "max_dd_pct": -69.8, "mar": -0.06, "sma_rule_mar": 0.10, "passed": False},
}
RESEARCH_REPORT_URL = "https://claude.ai/artifact/Cgabc1xqjreDyxxnsETHPZ"


def research_note(preset_id: str) -> str:
    r = RESEARCH.get(preset_id)
    if not r:
        return ""
    return (
        "Did not pass the research gate. Out of sample (2019–2026, long-only, 3x): "
        f"{r['cagr_pct']:.1f}% a year, worst drawdown {r['max_dd_pct']:.1f}%, return/drawdown {r['mar']:.2f} "
        f"vs {r['sma_rule_mar']:.2f} for a simple 250-day rule. Inverse ETFs lowered results in every pair. "
        "Use for research and paper trading."
    )


@dataclass(frozen=True)
class Params:
    slow: int = 250
    fast: int = 50
    decel_roc: float = -2.0
    decel_acc: float = -1.5
    consol_roc: float = -1.0
    crash_roc: float = -1.5
    w_bull: float = 1.0
    w_decel: float = 0.30
    w_consol: float = 0.50
    w_def: float = 0.20
    w_crash: float = 0.60
    w_chop: float = 0.20


DEFAULT_PARAMS = Params()

REGIMES = {
    "BULL_RIDER": "Bull rider",
    "DECEL_TRIM": "Momentum trim",
    "CONSOLIDATION": "Consolidation",
    "DEFENSIVE": "Defensive",
    "BEAR_CRASH": "Bear crash",
    "BEAR_CHOP": "Bear chop",
    "CASH": "Cash",
}


def regime_table(params: Params = DEFAULT_PARAMS) -> List[Dict[str, Any]]:
    """What the strategy holds in each regime (single source for the UI's explanation)."""
    p = params
    return [
        {"id": "BULL_RIDER", "label": REGIMES["BULL_RIDER"], "bull_pct": round(p.w_bull * 100), "bear_pct": 0,
         "condition": f"Price above the {p.slow}-day and {p.fast}-day averages, momentum not fading."},
        {"id": "DECEL_TRIM", "label": REGIMES["DECEL_TRIM"], "bull_pct": round(p.w_decel * 100), "bear_pct": 0,
         "condition": f"Above both averages, but the 10-day change is below {p.decel_roc:g}% and still slowing."},
        {"id": "CONSOLIDATION", "label": REGIMES["CONSOLIDATION"], "bull_pct": round(p.w_consol * 100), "bear_pct": 0,
         "condition": f"Above the {p.slow}-day, at or below the {p.fast}-day, 10-day change {p.consol_roc:g}% or better."},
        {"id": "DEFENSIVE", "label": REGIMES["DEFENSIVE"], "bull_pct": round(p.w_def * 100), "bear_pct": 0,
         "condition": f"Above the {p.slow}-day, at or below the {p.fast}-day, 10-day change worse than {p.consol_roc:g}%."},
        {"id": "BEAR_CRASH", "label": REGIMES["BEAR_CRASH"], "bull_pct": 0, "bear_pct": round(p.w_crash * 100),
         "condition": f"At or below the {p.slow}-day and below the {p.fast}-day, 10-day change below {p.crash_roc:g}%."},
        {"id": "BEAR_CHOP", "label": REGIMES["BEAR_CHOP"], "bull_pct": 0, "bear_pct": round(p.w_chop * 100),
         "condition": f"At or below the {p.slow}-day and below the {p.fast}-day, 10-day change {p.crash_roc:g}% or better."},
        {"id": "CASH", "label": REGIMES["CASH"], "bull_pct": 0, "bear_pct": 0,
         "condition": f"At or below the {p.slow}-day average but at or above the {p.fast}-day."},
    ]


def get_spec(preset_id: Any) -> Optional[AllocationSpec]:
    return SPECS.get(str(preset_id or "").strip().lower())


def is_allocation_preset(preset_id: Any) -> bool:
    return get_spec(preset_id) is not None


def all_leg_symbols() -> Tuple[str, ...]:
    out = []
    for spec in SPECS.values():
        out.extend(o.symbol for o in spec.bull + spec.bear)
    return tuple(out)


# ---------------------------------------------------------------------------
# Settings
# ---------------------------------------------------------------------------

def normalize_direction(value: Any) -> str:
    v = str(value or "").strip().lower()
    return v if v in DIRECTIONS else DIRECTION_LONG_ONLY


def resolve_legs(preset_id: Any, settings: Mapping[str, Any]) -> Dict[str, Optional[str]]:
    """Bull/bear symbols the bot trades. The bear leg is None in long-only mode."""
    spec = get_spec(preset_id)
    if spec is None:
        return {"bull": None, "bear": None}
    s = settings or {}
    direction = normalize_direction(s.get("strategy_preset_variant"))
    bull_syms = [o.symbol for o in spec.bull]
    bear_syms = [o.symbol for o in spec.bear]
    bull = str(s.get("allocation_bull_etf") or "").upper()
    bear = str(s.get("allocation_bear_etf") or "").upper()
    return {
        "bull": bull if bull in bull_syms else bull_syms[0],
        "bear": (bear if bear in bear_syms else bear_syms[0]) if direction == DIRECTION_INVERSE else None,
    }


def normalize_exec_time(value: Any) -> Optional[str]:
    """``close-N`` (T-N, N in 15-minute steps) or an ``HH:MM`` exchange (ET) custom time; else None."""
    v = str(value or "").strip().lower().replace("t-", "close-")
    if v in ("", EXEC_CLOSE_MINUS_15):
        return EXEC_CLOSE_MINUS_15
    if v.startswith("close-"):
        try:
            minutes = int(v.split("-", 1)[1])
        except ValueError:
            return None
        return f"close-{minutes}" if minutes in EXEC_RELATIVE_STEPS else None
    try:
        hh, mm = v.split(":")
        t = time(int(hh), int(mm))
    except (ValueError, TypeError):
        return None
    latest = (datetime.combine(date(2000, 1, 3), nyse_calendar.REGULAR_CLOSE) - EXEC_MIN_BEFORE_CLOSE).time()
    if not (EXEC_EARLIEST <= t <= latest):
        return None
    return f"{t.hour:02d}:{t.minute:02d}"


def exec_time_options() -> List[Dict[str, str]]:
    """T-15 (default) to T-375 in 15-minute steps; clock times shown for a normal 4:00 PM close."""
    opts = []
    close = datetime(2000, 1, 3, 16, 0)
    for minutes in EXEC_RELATIVE_STEPS:
        et = close - timedelta(minutes=minutes)
        ct = et - timedelta(hours=1)
        label = f"T-{minutes} · {ct.strftime('%-I:%M %p')} CT"
        if minutes == 15:
            label += " · Recommended"
        opts.append({"value": f"close-{minutes}", "label": label})
    return opts


def decision_time(day: date, setting: Any) -> Optional[Dict[str, Any]]:
    """ET-aware decision time on ``day`` (None on a holiday/weekend).

    A fixed time that falls inside the last 5 minutes or after an early close runs at
    close - 15 instead, and says so."""
    bounds = nyse_calendar.session_bounds(day)
    if bounds is None:
        return None
    open_et, close_et = bounds
    norm = normalize_exec_time(setting) or EXEC_CLOSE_MINUS_15
    fallback = close_et - timedelta(minutes=15)
    if norm.startswith("close-"):
        # T-N follows the actual close (early-close days move with it); never before 5 minutes after the open.
        at = close_et - timedelta(minutes=int(norm.split("-", 1)[1]))
        earliest = open_et + timedelta(minutes=5)
        return {"at": max(at, earliest), "adjusted": at < earliest, "close": close_et}
    hh, mm = (int(x) for x in norm.split(":"))
    at = datetime.combine(day, time(hh, mm), tzinfo=nyse_calendar.EASTERN)
    if at > close_et - EXEC_MIN_BEFORE_CLOSE or at < open_et + timedelta(minutes=5):
        return {"at": fallback, "adjusted": True, "close": close_et}
    return {"at": at, "adjusted": False, "close": close_et}


def validate_settings(preset_id: Any, settings: Mapping[str, Any], ticker: str = "") -> List[str]:
    spec = get_spec(preset_id)
    if spec is None:
        return []
    s = settings or {}
    errors = []
    if ticker and str(ticker).upper() != spec.signal:
        errors.append(f"This strategy reads {spec.signal}; create the bot on {spec.signal} and pick the ETF below.")
    bull = str(s.get("allocation_bull_etf") or "").upper()
    if bull and bull not in {o.symbol for o in spec.bull}:
        errors.append(f"{bull} is not a bull ETF for {spec.signal}.")
    bear = str(s.get("allocation_bear_etf") or "").upper()
    if bear and bear not in {o.symbol for o in spec.bear}:
        errors.append(f"{bear} is not an inverse ETF for {spec.signal}.")
    if s.get("allocation_exec_time") not in (None, "") and normalize_exec_time(s.get("allocation_exec_time")) is None:
        errors.append("Execution time must be between 9:35 AM and 3:55 PM ET.")
    return errors


def normalized_settings(preset_id: Any, settings: Mapping[str, Any]) -> Dict[str, Any]:
    """The allocation fields with defaults filled in (stored on the bot)."""
    legs = resolve_legs(preset_id, settings)
    spec = get_spec(preset_id)
    s = settings or {}
    bear_default = spec.bear[0].symbol if spec else None
    bear = str(s.get("allocation_bear_etf") or "").upper()
    return {
        "allocation_bull_etf": legs["bull"],
        "allocation_bear_etf": bear if spec and bear in {o.symbol for o in spec.bear} else bear_default,
        "allocation_exec_time": normalize_exec_time(s.get("allocation_exec_time")) or EXEC_DEFAULT,
    }


# ---------------------------------------------------------------------------
# Regime
# ---------------------------------------------------------------------------

def indicator_values(closes: Sequence[float], price: float, params: Params = DEFAULT_PARAMS) -> Optional[Dict[str, float]]:
    """Indicators at decision time from completed closes (oldest..newest, through d-1) plus P."""
    c = np.asarray([float(x) for x in closes], dtype=float)
    if len(c) < max(MIN_COMPLETED_CLOSES, params.slow) or not np.isfinite(c[-params.slow:]).all():
        return None
    if not (isinstance(price, (int, float)) and math.isfinite(price) and price > 0):
        return None
    sma_slow = (c[-(params.slow - 1):].sum() + price) / params.slow
    sma_fast = (c[-(params.fast - 1):].sum() + price) / params.fast
    roc10 = (price / c[-10] - 1.0) * 100.0
    roc5 = (price / c[-5] - 1.0) * 100.0
    return {"price": float(price), "sma_slow": float(sma_slow), "sma_fast": float(sma_fast),
            "roc10": float(roc10), "roc5": float(roc5), "acc": float(roc5 - roc10 / 2.0)}


def classify(values: Mapping[str, float], direction: str, params: Params = DEFAULT_PARAMS) -> Tuple[str, Dict[str, float]]:
    """Regime and target weights {"bull": w, "bear": w} for one decision."""
    inverse = normalize_direction(direction) == DIRECTION_INVERSE
    p, ss, sf, r10, acc = values["price"], values["sma_slow"], values["sma_fast"], values["roc10"], values["acc"]
    if p > ss and p > sf:
        if r10 < params.decel_roc and acc < params.decel_acc:
            return "DECEL_TRIM", {"bull": params.w_decel, "bear": 0.0}
        return "BULL_RIDER", {"bull": params.w_bull, "bear": 0.0}
    if p > ss:
        if r10 >= params.consol_roc:
            return "CONSOLIDATION", {"bull": params.w_consol, "bear": 0.0}
        return "DEFENSIVE", {"bull": params.w_def, "bear": 0.0}
    if p < sf:
        if r10 < params.crash_roc:
            return "BEAR_CRASH", {"bull": 0.0, "bear": params.w_crash if inverse else 0.0}
        return "BEAR_CHOP", {"bull": 0.0, "bear": params.w_chop if inverse else 0.0}
    return "CASH", {"bull": 0.0, "bear": 0.0}


def evaluate(closes: Sequence[float], price: float, direction: str, params: Params = DEFAULT_PARAMS) -> Dict[str, Any]:
    """One decision. Never returns a target when inputs are incomplete (status WAITING_DATA)."""
    values = indicator_values(closes, price, params)
    if values is None:
        n = len(closes) if closes is not None else 0
        return {"status": STATUS_WAITING, "regime": None, "regime_label": "Warming up", "weights": None, "values": {},
                "reason": f"Waiting for data: {n}/{MIN_COMPLETED_CLOSES} completed daily closes and a current price."}
    regime, weights = classify(values, direction, params)
    above = "above" if values["price"] > values["sma_slow"] else "at or below"
    reason = (f"{REGIMES[regime]}: price {values['price']:.2f} is {above} the 250-day average {values['sma_slow']:.2f}; "
              f"50-day {values['sma_fast']:.2f}; 10-day change {values['roc10']:+.2f}%.")
    return {"status": STATUS_READY, "regime": regime, "regime_label": REGIMES[regime], "weights": weights,
            "values": {k: round(v, 4) for k, v in values.items()}, "reason": reason}


def regime_history(closes: pd.Series, provisional: pd.Series, direction: str, params: Params = DEFAULT_PARAMS) -> pd.DataFrame:
    """Vectorised decisions for every day in ``provisional`` (index = decision day).

    ``closes`` are completed daily closes indexed by session date; the decision on day d
    uses closes strictly before d plus provisional[d]."""
    closes = closes.sort_index()
    prev = pd.DataFrame(index=closes.index)
    prev["sum_slow"] = closes.rolling(params.slow - 1).sum().shift(1)
    prev["sum_fast"] = closes.rolling(params.fast - 1).sum().shift(1)
    prev["c10"] = closes.shift(10)
    prev["c5"] = closes.shift(5)
    prev["n_hist"] = np.arange(len(closes))
    df = pd.DataFrame({"P": provisional}).join(prev, how="inner")
    P = df["P"]
    df["sma_slow"] = (df["sum_slow"] + P) / params.slow
    df["sma_fast"] = (df["sum_fast"] + P) / params.fast
    df["roc10"] = (P / df["c10"] - 1) * 100
    df["roc5"] = (P / df["c5"] - 1) * 100
    df["acc"] = df["roc5"] - df["roc10"] / 2
    regimes, wb, wr = [], [], []
    for row in df.itertuples():
        vals = (row.P, row.sma_slow, row.sma_fast, row.roc10, row.acc)
        if row.n_hist < max(MIN_COMPLETED_CLOSES, params.slow) or not all(np.isfinite(v) for v in vals):
            regimes.append(None); wb.append(np.nan); wr.append(np.nan)
            continue
        regime, w = classify({"price": row.P, "sma_slow": row.sma_slow, "sma_fast": row.sma_fast,
                              "roc10": row.roc10, "acc": row.acc}, direction, params)
        regimes.append(regime); wb.append(w["bull"]); wr.append(w["bear"])
    df["regime"], df["w_bull"], df["w_bear"] = regimes, wb, wr
    return df


# ---------------------------------------------------------------------------
# Rebalancing
# ---------------------------------------------------------------------------

def sleeve_value(allocation_mode: str, base_amount: float, realized_pl: float, legs_market_value: float, legs_cost: float) -> float:
    """Dollars the strategy allocates. Fixed: the bot's amount. Compound: amount + realized + unrealized P/L."""
    base = max(0.0, float(base_amount or 0.0))
    if str(allocation_mode or "fixed").lower() != "compound":
        return base
    return max(0.0, base + float(realized_pl or 0.0) + float(legs_market_value or 0.0) - float(legs_cost or 0.0))


def rebalance_orders(
    targets: Mapping[str, float],
    held_qty: Mapping[str, float],
    prices: Mapping[str, float],
    sleeve: float,
    band: float = REBALANCE_BAND,
) -> List[Dict[str, Any]]:
    """Orders that move each leg to ``weight * sleeve``. Sells first.

    A leg whose target is 0 is closed in full; a non-zero leg trades only when it is off
    target by more than ``band * sleeve``. Prices must be positive for every leg involved."""
    orders = []
    for sym in sorted(set(targets) | {s for s, q in held_qty.items() if q > 0}):
        w = float(targets.get(sym, 0.0) or 0.0)
        q = float(held_qty.get(sym, 0.0) or 0.0)
        px = float(prices.get(sym) or 0.0)
        if px <= 0:
            if q > 0 or w > 0:
                raise ValueError(f"no price for {sym}")
            continue
        held = q * px
        target = w * sleeve
        diff = target - held
        if w <= 0 and q > 0:
            orders.append({"symbol": sym, "side": "SELL", "qty": q, "notional": held, "close_all": True,
                           "target_value": 0.0, "held_value": held})
        elif w > 0 and abs(diff) > band * sleeve and abs(diff) >= MIN_ORDER_NOTIONAL:
            if diff < 0:
                sell_q = min(q, -diff / px)
                orders.append({"symbol": sym, "side": "SELL", "qty": sell_q, "notional": sell_q * px, "close_all": sell_q >= q - 1e-9,
                               "target_value": target, "held_value": held})
            else:
                orders.append({"symbol": sym, "side": "BUY", "qty": diff / px, "notional": diff, "close_all": False,
                               "target_value": target, "held_value": held})
    orders.sort(key=lambda o: 0 if o["side"] == "SELL" else 1)
    return orders


# ---------------------------------------------------------------------------
# Simulation (Backtest)
# ---------------------------------------------------------------------------

@dataclass
class SimLeg:
    qty: float = 0.0
    avg: float = 0.0
    opened_at: Any = None


def simulate(
    plan: pd.DataFrame,
    marks: Mapping[str, pd.Series],
    *,
    bull: str,
    bear: Optional[str],
    initial_capital: float,
    allocation_mode: str = "fixed",
    base_amount: Optional[float] = None,
    max_position_fraction: Optional[float] = None,
    band: float = REBALANCE_BAND,
    calendar: Optional[pd.DatetimeIndex] = None,
) -> Dict[str, Any]:
    """Run the allocator over ``plan`` (one row per decision).

    ``plan`` columns: fill_day (Timestamp), decision_time (str), regime, w_bull, w_bear,
    fill_bull, fill_bear (fill prices). ``marks[symbol]`` = daily closes for marking.
    Returns daily equity, exposure, regimes, fills and round-trip trades (average cost)."""
    base = float(base_amount) if base_amount and base_amount > 0 else float(initial_capital)
    cash = float(initial_capital)
    legs = {bull: SimLeg()}
    if bear:
        legs[bear] = SimLeg()
    realized = 0.0
    fills, trades, daily = [], [], []
    by_day = {pd.Timestamp(r.fill_day).normalize(): r for r in plan.itertuples() if pd.notna(r.fill_day)}
    days = calendar if calendar is not None else pd.DatetimeIndex(sorted(by_day))
    mark_series = {s: marks[s].reindex(days).ffill() for s in legs}
    last_regime = None
    skipped = 0
    for day in days:
        row = by_day.get(day)
        if row is not None and np.isfinite(row.w_bull) and np.isfinite(row.w_bear):
            last_regime = row.regime
            prices = {bull: float(row.fill_bull)}
            if bear:
                prices[bear] = float(row.fill_bear) if np.isfinite(row.fill_bear) else 0.0
            need = [s for s, leg in legs.items() if leg.qty > 0]
            if row.w_bull > 0:
                need.append(bull)
            if bear and row.w_bear > 0:
                need.append(bear)
            if any(not (prices.get(s) or 0) > 0 for s in need):
                skipped += 1
            else:
                mv = sum(leg.qty * prices[s] for s, leg in legs.items() if leg.qty > 0)
                cost = sum(leg.qty * leg.avg for leg in legs.values())
                sleeve = sleeve_value(allocation_mode, base, realized, mv, cost)
                targets = {bull: row.w_bull}
                if bear:
                    targets[bear] = row.w_bear
                nav = cash + mv
                orders = rebalance_orders(targets, {s: l.qty for s, l in legs.items()}, prices, sleeve, band)
                for o in orders:
                    sym, leg, px = o["symbol"], legs[o["symbol"]], prices[o["symbol"]]
                    if o["side"] == "SELL":
                        q = leg.qty if o["close_all"] else min(leg.qty, o["qty"])
                        pl = (px - leg.avg) * q
                        cash += q * px
                        realized += pl
                        trades.append({"symbol": sym, "entry_time": leg.opened_at, "exit_time": day, "qty": q,
                                       "entry_price": leg.avg, "exit_price": px, "entry_cost": q * leg.avg,
                                       "exit_value": q * px, "pl": pl, "pl_pct": (px / leg.avg - 1) * 100 if leg.avg else 0.0,
                                       "reason": f"{REGIMES.get(row.regime, row.regime)}: target {targets.get(sym, 0) * 100:.0f}%"})
                        leg.qty -= q
                        if leg.qty <= 1e-9:
                            leg.qty, leg.avg, leg.opened_at = 0.0, 0.0, None
                        fills.append({"day": day, "decision_time": row.decision_time, "symbol": sym, "side": "SELL", "qty": q,
                                      "price": px, "notional": q * px, "regime": row.regime})
                    else:
                        want = o["notional"]
                        limit = "target"
                        if max_position_fraction:
                            cap = max_position_fraction * nav - leg.qty * px
                            if cap < want:
                                want, limit = max(0.0, cap), "max_position"
                        if cash < want:
                            want, limit = cash, "cash"
                        if want < MIN_ORDER_NOTIONAL:
                            continue
                        q = want / px
                        leg.avg = (leg.avg * leg.qty + px * q) / (leg.qty + q)
                        leg.qty += q
                        leg.opened_at = leg.opened_at or day
                        cash -= want
                        fills.append({"day": day, "decision_time": row.decision_time, "symbol": sym, "side": "BUY", "qty": q,
                                      "price": px, "notional": want, "regime": row.regime, "limit": limit})
        mv_close = sum(leg.qty * float(mark_series[s].loc[day]) for s, leg in legs.items() if leg.qty > 0)
        nav = cash + mv_close
        daily.append({"day": day, "equity": nav, "exposure": mv_close / nav if nav > 0 else 0.0, "regime": last_regime,
                      **{f"w_{s}": (legs[s].qty * float(mark_series[s].loc[day]) / nav if nav > 0 and legs[s].qty > 0 else 0.0) for s in legs}})
    return {"daily": pd.DataFrame(daily).set_index("day") if daily else pd.DataFrame(), "fills": fills, "trades": trades,
            "open_legs": {s: {"qty": l.qty, "avg": l.avg, "opened_at": l.opened_at} for s, l in legs.items() if l.qty > 0},
            "cash": cash, "realized_pl": realized, "skipped_days": skipped}
