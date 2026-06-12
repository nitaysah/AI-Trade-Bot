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
        max_expiry = today + datetime.timedelta(days=expiration_limit_days)
        
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
            contracts.append({
                "symbol": contract.symbol,
                "underlying": contract.underlying_symbol,
                "expiration": contract.expiration_date,  # datetime.date
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
    data_client: Optional[Any] = None
) -> Optional[Dict[str, Any]]:
    """
    Selects the best matching contract from a list of option contracts.
    
    1. Filters by type (CALL or PUT).
    2. Identifies the expiration date closest to target_expiry_days.
    3. Selects the strike price based on strike_type:
       - ATM: closest strike to current_underlying_price.
       - ITM: closest strike inside-the-money (lower for CALL, higher for PUT).
       - OTM: closest strike out-of-the-money (higher for CALL, lower for PUT).
    4. If target_delta is specified and data_client is provided, queries Greeks
       to find the contract closest to target_delta.
    """
    option_type = option_type.upper()
    valid_type = [c for c in contracts if c["type"] == option_type]
    if not valid_type:
        return None

    # Find the unique expiration dates and pick the one closest to target_expiry_days
    today = datetime.date.today()
    expirations = sorted(list(set(c["expiration"] for c in valid_type)))
    if not expirations:
        return None

    # Find closest expiration to target
    target_date = today + datetime.timedelta(days=target_expiry_days)
    selected_expiry = min(expirations, key=lambda d: abs((d - target_date).days))
    
    # Filter contracts to only this expiration
    expiry_contracts = [c for c in valid_type if c["expiration"] == selected_expiry]
    if not expiry_contracts:
        return None

    # If target_delta is specified and we have an OptionHistoricalDataClient
    if target_delta is not None and data_client is not None:
        try:
            # We want to check Greeks for a subset of contracts around the ATM strike
            # Sort by strike distance to find candidates near ATM
            sorted_by_dist = sorted(expiry_contracts, key=lambda c: abs(c["strike"] - current_underlying_price))
            candidates = sorted_by_dist[:6]  # check 6 closest contracts
            candidate_symbols = [c["symbol"] for c in candidates]
            
            # Fetch snapshot for Greek analysis
            req = OptionSnapshotRequest(symbol_or_symbols=candidate_symbols)
            snapshots = data_client.get_option_snapshot(req)
            
            best_contract = None
            best_delta_diff = float("inf")
            
            for c in candidates:
                snap = snapshots.get(c["symbol"])
                if snap and snap.greeks and snap.greeks.delta is not None:
                    # Delta is positive for calls, negative for puts
                    delta_val = abs(float(snap.greeks.delta))
                    diff = abs(delta_val - abs(target_delta))
                    if diff < best_delta_diff:
                        best_delta_diff = diff
                        best_contract = c
            
            if best_contract:
                return best_contract
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
        # Closest ITM (largest strike <= underlying for Call, smallest strike >= underlying for Put)
        if itm_contracts:
            return itm_contracts[-1] if option_type == "CALL" else itm_contracts[0]
    elif strike_type.upper() == "OTM":
        # Closest OTM (smallest strike > underlying for Call, largest strike < underlying for Put)
        if otm_contracts:
            return otm_contracts[0] if option_type == "CALL" else otm_contracts[-1]

    # Default to ATM (closest strike overall)
    return min(expiry_contracts, key=lambda c: abs(c["strike"] - current_underlying_price))
