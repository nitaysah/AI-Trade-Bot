import re
import datetime
import math
from typing import List, Dict, Any, Optional

try:
    from alpaca.trading.client import TradingClient
    from alpaca.trading.requests import GetOptionContractsRequest
    from alpaca.trading.enums import AssetStatus, ContractType
    from alpaca.data.historical.option import OptionHistoricalDataClient
    from alpaca.data.requests import OptionSnapshotRequest
    ALPACA_AVAILABLE = True
except ImportError:
    ALPACA_AVAILABLE = False

def _observed_date(day: datetime.date) -> datetime.date:
    if day.weekday() == 5:  # Saturday
        return day - datetime.timedelta(days=1)
    if day.weekday() == 6:  # Sunday
        return day + datetime.timedelta(days=1)
    return day

def _nth_weekday(year: int, month: int, weekday: int, n: int) -> datetime.date:
    day = datetime.date(year, month, 1)
    offset = (weekday - day.weekday()) % 7
    return day + datetime.timedelta(days=offset + (n - 1) * 7)

def _last_weekday(year: int, month: int, weekday: int) -> datetime.date:
    if month == 12:
        day = datetime.date(year + 1, 1, 1) - datetime.timedelta(days=1)
    else:
        day = datetime.date(year, month + 1, 1) - datetime.timedelta(days=1)
    return day - datetime.timedelta(days=(day.weekday() - weekday) % 7)

def _easter_date(year: int) -> datetime.date:
    # Anonymous Gregorian algorithm.
    a = year % 19
    b = year // 100
    c = year % 100
    d = b // 4
    e = b % 4
    f = (b + 8) // 25
    g = (b - f + 1) // 3
    h = (19 * a + b - d - g + 15) % 30
    i = c // 4
    k = c % 4
    l = (32 + 2 * e + 2 * i - h - k) % 7
    m = (a + 11 * h + 22 * l) // 451
    month = (h + l - 7 * m + 114) // 31
    day = ((h + l - 7 * m + 114) % 31) + 1
    return datetime.date(year, month, day)

def get_us_market_holidays(year: int) -> set:
    """NYSE-style full-day holidays used for option DTE selection."""
    holidays = {
        _observed_date(datetime.date(year, 1, 1)),              # New Year's Day
        _nth_weekday(year, 1, 0, 3),                            # Martin Luther King Jr. Day
        _nth_weekday(year, 2, 0, 3),                            # Washington's Birthday
        _easter_date(year) - datetime.timedelta(days=2),        # Good Friday
        _last_weekday(year, 5, 0),                              # Memorial Day
        _observed_date(datetime.date(year, 6, 19)),             # Juneteenth
        _observed_date(datetime.date(year, 7, 4)),              # Independence Day
        _nth_weekday(year, 9, 0, 1),                            # Labor Day
        _nth_weekday(year, 11, 3, 4),                           # Thanksgiving Day
        _observed_date(datetime.date(year, 12, 25)),            # Christmas Day
        _observed_date(datetime.date(year + 1, 1, 1)),          # Next New Year's observed in December
    }
    return {d for d in holidays if d.year == year}

def is_us_market_trading_day(day: datetime.date) -> bool:
    if day.weekday() >= 5:
        return False
    return day not in get_us_market_holidays(day.year)

def add_us_market_trading_days(start: datetime.date, trading_days: int) -> datetime.date:
    """Return the date after N valid market sessions, excluding start itself."""
    if trading_days <= 0:
        return start
    current = start
    remaining = int(trading_days)
    while remaining > 0:
        current += datetime.timedelta(days=1)
        if is_us_market_trading_day(current):
            remaining -= 1
    return current

def trading_days_until(start: datetime.date, end: datetime.date) -> int:
    """Count valid market sessions after start through end."""
    if end <= start:
        return 0
    current = start
    count = 0
    while current < end:
        current += datetime.timedelta(days=1)
        if is_us_market_trading_day(current):
            count += 1
    return count

def _safe_float(value, default: float = 0.0) -> float:
    try:
        if value is None:
            return default
        value = float(value)
        if math.isnan(value) or math.isinf(value):
            return default
        return value
    except Exception:
        return default

def _strike_profile(strike_type: str) -> Dict[str, float]:
    """Quant defaults for intraday long-option selection."""
    st = str(strike_type or "ATM").upper()
    if st == "ITM":
        return {
            "target_delta": 0.60,
            "min_delta": 0.55,
            "max_delta": 0.72,
            "target_depth_pct": 0.015,
            "max_spread_pct": 0.05,
        }
    if st == "OTM":
        return {
            "target_delta": 0.35,
            "min_delta": 0.25,
            "max_delta": 0.45,
            "target_depth_pct": 0.010,
            "max_spread_pct": 0.05,
        }
    return {
        "target_delta": 0.50,
        "min_delta": 0.43,
        "max_delta": 0.57,
        "target_depth_pct": 0.000,
        "max_spread_pct": 0.05,
    }

def _target_strike_from_profile(current_price: float, option_type: str, strike_type: str, depth_pct: float) -> float:
    st = str(strike_type or "ATM").upper()
    option_type = option_type.upper()
    if st == "ITM":
        return current_price * (1.0 - depth_pct) if option_type == "CALL" else current_price * (1.0 + depth_pct)
    if st == "OTM":
        return current_price * (1.0 + depth_pct) if option_type == "CALL" else current_price * (1.0 - depth_pct)
    return current_price

def _is_requested_moneyness(contract: Dict[str, Any], current_price: float, option_type: str, strike_type: str) -> bool:
    st = str(strike_type or "ATM").upper()
    strike = _safe_float(contract.get("strike"), 0.0)
    option_type = option_type.upper()
    if st == "ITM":
        return strike <= current_price if option_type == "CALL" else strike >= current_price
    if st == "OTM":
        return strike > current_price if option_type == "CALL" else strike < current_price
    return True

def _snapshot_get(snap, *names):
    current = snap
    for name in names:
        if current is None:
            return None
        if isinstance(current, dict):
            current = current.get(name)
        else:
            current = getattr(current, name, None)
    return current

def _annotate_contract_from_snapshot(contract: Dict[str, Any], snapshot) -> Dict[str, Any]:
    annotated = dict(contract)
    delta = _safe_float(_snapshot_get(snapshot, "greeks", "delta"), None)
    bid = _safe_float(_snapshot_get(snapshot, "latest_quote", "bid_price"), 0.0)
    ask = _safe_float(_snapshot_get(snapshot, "latest_quote", "ask_price"), 0.0)
    mid = ((bid + ask) / 2.0) if bid > 0 and ask > 0 else 0.0
    spread_pct = ((ask - bid) / mid) if mid > 0 and ask >= bid else None
    if delta is not None:
        annotated["delta"] = delta
    if bid > 0:
        annotated["bid"] = bid
    if ask > 0:
        annotated["ask"] = ask
    if mid > 0:
        annotated["mid"] = mid
    if spread_pct is not None:
        annotated["spread_pct"] = spread_pct
    return annotated

def _coerce_date(value) -> datetime.date:
    if isinstance(value, datetime.datetime):
        return value.date()
    if isinstance(value, datetime.date):
        return value
    return datetime.datetime.strptime(str(value), "%Y-%m-%d").date()

def parse_occ_symbol(symbol: str) -> Optional[Dict[str, Any]]:
    """
    Parses a standard OCC option symbol.
    Example: SPY260612C00450000 -> SPY, 2026-06-12, CALL, 450.0
    """
    match = re.match(r"^([A-Z]{1,6})(\d{6})([CP])(\d{8})$", symbol)
    if not match:
        return None
    underlying, exp_str, option_type, strike_str = match.groups()
    try:
        expiration = datetime.datetime.strptime(exp_str, "%y%m%d").date()
    except ValueError:
        return None
    strike = float(strike_str) / 1000.0
    return {
        "underlying": underlying,
        "expiration": expiration,
        "type": "CALL" if option_type == 'C' else "PUT",
        "strike": strike
    }

def get_option_chain(
    underlying: str,
    expiration_limit_days: int = 7,
    current_price: Optional[float] = None,
    client: Optional[Any] = None
) -> List[Dict[str, Any]]:
    """
    Fetches the option chain for a given underlying ticker.
    If client is provided, queries Alpaca API. Otherwise returns empty or mock.
    Filters strike prices within +/- 10% of current_price if provided.
    """
    if not ALPACA_AVAILABLE or client is None:
        print("[options] Alpaca not available or client not provided. Returning empty chain.")
        return []

    try:
        underlying = underlying.upper().replace("/", "")
        today = datetime.date.today()
        target_expiry = add_us_market_trading_days(today, int(expiration_limit_days or 0))
        max_expiry = target_expiry + datetime.timedelta(days=10)
        
        # Build Request Params
        params = {
            "underlying_symbols": [underlying],
            "status": AssetStatus.ACTIVE,
            "expiration_date_gte": today.strftime("%Y-%m-%d"),
            "expiration_date_lte": max_expiry.strftime("%Y-%m-%d"),
            "limit": 10000,
        }

        # Apply strike filters to reduce payload if current price is known
        if current_price and current_price > 0:
            strike_lower = current_price * 0.90
            strike_upper = current_price * 1.10
            params["strike_price_gte"] = f"{strike_lower:.2f}"
            params["strike_price_lte"] = f"{strike_upper:.2f}"

        req = GetOptionContractsRequest(**params)
        res = client.get_option_contracts(req)
        
        contracts = []
        for contract in res.option_contracts:
            expiration = _coerce_date(contract.expiration_date)
            contracts.append({
                "symbol": contract.symbol,
                "underlying": contract.underlying_symbol,
                "expiration": expiration,
                "dte_trading_days": trading_days_until(today, expiration),
                "type": "CALL" if contract.type == ContractType.CALL else "PUT",
                "strike": float(contract.strike_price),
                "multiplier": float(getattr(contract, "size", 100))
            })
        return contracts
    except Exception as e:
        print(f"[options] Error fetching option contracts: {e}")
        return []

def select_contract(
    contracts: List[Dict[str, Any]],
    current_underlying_price: float,
    option_type: str,
    strike_type: str = "ATM",
    target_expiry_days: int = 7,
    target_delta: Optional[float] = None,
    data_client: Optional[Any] = None,
    min_delta: Optional[float] = None,
    max_delta: Optional[float] = None,
    max_spread_pct: Optional[float] = None,
    target_depth_pct: Optional[float] = None,
) -> Optional[Dict[str, Any]]:
    """
    Selects the best matching contract from a list of option contracts.
    
    1. Filters by type (CALL or PUT).
    2. Identifies the expiration date closest to target_expiry_days.
    3. Selects by quant profile when snapshots are available:
       - ATM: target ~0.50 delta.
       - ITM: target ~0.60 delta with same-side moneyness.
       - OTM: target ~0.35 delta with same-side moneyness.
       Liquidity/spread is scored so wide quotes are avoided when possible.
    4. Falls back to strike-depth selection when Greeks are unavailable.
    """
    option_type = option_type.upper()
    strike_type = str(strike_type or "ATM").upper()
    profile = _strike_profile(strike_type)
    target_delta = abs(_safe_float(target_delta, profile["target_delta"]))
    min_delta = abs(_safe_float(min_delta, profile["min_delta"]))
    max_delta = abs(_safe_float(max_delta, profile["max_delta"]))
    max_spread_pct = _safe_float(max_spread_pct, profile["max_spread_pct"])
    target_depth_pct = max(0.0, _safe_float(target_depth_pct, profile["target_depth_pct"]))
    valid_type = [c for c in contracts if c["type"] == option_type]
    if not valid_type:
        return None

    # Find the unique expiration dates and pick the target trading-day DTE.
    today = datetime.date.today()
    expirations = sorted(list(set(_coerce_date(c["expiration"]) for c in valid_type)))
    if not expirations:
        return None

    target_dte = max(0, int(target_expiry_days or 0))
    target_date = add_us_market_trading_days(today, target_dte)
    on_or_after_target = [d for d in expirations if trading_days_until(today, d) >= target_dte]
    if on_or_after_target:
        selected_expiry = min(on_or_after_target, key=lambda d: (trading_days_until(today, d), d))
    else:
        selected_expiry = min(expirations, key=lambda d: abs((d - target_date).days))
    
    # Filter contracts to only this expiration
    expiry_contracts = [c for c in valid_type if _coerce_date(c["expiration"]) == selected_expiry]
    if not expiry_contracts:
        return None

    target_strike = _target_strike_from_profile(current_underlying_price, option_type, strike_type, target_depth_pct)

    # Use live snapshot Greeks/quotes when available. This makes ITM/OTM adaptive
    # across slow ETFs, mega-cap tech, and high-beta names without hard-coded dollars.
    if data_client is not None:
        try:
            sorted_by_dist = sorted(expiry_contracts, key=lambda c: abs(c["strike"] - target_strike))
            candidates = sorted_by_dist[:40]
            candidate_symbols = [c["symbol"] for c in candidates]
            
            # Fetch snapshot for Greek analysis
            req = OptionSnapshotRequest(symbol_or_symbols=candidate_symbols)
            snapshots = data_client.get_option_snapshot(req)
            
            scored = []
            for c in candidates:
                snap = snapshots.get(c["symbol"])
                if not snap:
                    continue
                annotated = _annotate_contract_from_snapshot(c, snap)
                delta_val = abs(_safe_float(annotated.get("delta"), 0.0))
                if delta_val <= 0:
                    continue
                spread_pct = annotated.get("spread_pct")
                in_requested_bucket = _is_requested_moneyness(annotated, current_underlying_price, option_type, strike_type)
                in_delta_band = min_delta <= delta_val <= max_delta
                spread_ok = spread_pct is None or spread_pct <= max_spread_pct
                strike_distance_pct = abs(_safe_float(annotated.get("strike"), target_strike) - target_strike) / max(current_underlying_price, 0.01)

                score = abs(delta_val - target_delta)
                score += 0.35 * strike_distance_pct
                if spread_pct is not None:
                    score += min(spread_pct, 1.0) * 0.25
                if not in_requested_bucket:
                    score += 2.0
                if not in_delta_band:
                    score += 0.75
                if not spread_ok:
                    score += 0.50
                scored.append((score, in_delta_band, spread_ok, in_requested_bucket, annotated))

            if scored:
                # Prefer contracts that satisfy delta, spread, and moneyness; fall back
                # gracefully rather than failing a valid trade because one field is absent.
                scored.sort(key=lambda x: (
                    not x[1],
                    not x[2],
                    not x[3],
                    x[0],
                    abs(_safe_float(x[4].get("strike"), target_strike) - target_strike),
                ))
                selected = scored[0][4]
                selected["selection_reason"] = (
                    f"{strike_type} delta-target {target_delta:.2f}; "
                    f"selected delta {abs(_safe_float(selected.get('delta'), 0.0)):.2f}, "
                    f"spread {(_safe_float(selected.get('spread_pct'), 0.0) * 100.0):.1f}%"
                )
                return selected
        except Exception as e:
            print(f"[options] Error selecting by delta, falling back to strike_type: {e}")

    # Fallback to strike_type selection
    expiry_contracts.sort(key=lambda c: c["strike"])
    
    # Separate into ITM and OTM relative to underlying price
    if option_type == "CALL":
        itm_contracts = [c for c in expiry_contracts if c["strike"] <= current_underlying_price]
        otm_contracts = [c for c in expiry_contracts if c["strike"] > current_underlying_price]
    else: # PUT
        itm_contracts = [c for c in expiry_contracts if c["strike"] >= current_underlying_price]
        otm_contracts = [c for c in expiry_contracts if c["strike"] < current_underlying_price]

    if strike_type.upper() == "ITM":
        # Target a modest ITM depth instead of blindly choosing the nearest strike.
        if itm_contracts:
            return min(itm_contracts, key=lambda c: abs(c["strike"] - target_strike))
    elif strike_type.upper() == "OTM":
        # Target a modest OTM depth while staying close enough for intraday responsiveness.
        if otm_contracts:
            return min(otm_contracts, key=lambda c: abs(c["strike"] - target_strike))

    # Default to ATM (closest strike overall)
    return min(expiry_contracts, key=lambda c: abs(c["strike"] - current_underlying_price))
