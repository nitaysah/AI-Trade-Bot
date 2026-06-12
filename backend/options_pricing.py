import math
import numpy as np
import pandas as pd
from typing import Union, List

def norm_cdf(x: float) -> float:
    """Cumulative distribution function for the standard normal distribution."""
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))

def norm_pdf(x: float) -> float:
    """Probability density function for the standard normal distribution."""
    return (1.0 / math.sqrt(2.0 * math.pi)) * math.exp(-0.5 * x**2)

def black_scholes(
    S: float,
    K: float,
    T: float,
    r: float,
    sigma: float,
    option_type: str = "CALL",
    q: float = 0.0  # Dividend yield
) -> float:
    """
    Calculates the theoretical Black-Scholes price of an option.
    S: Underlying Asset Price
    K: Strike Price
    T: Time to Expiration in Years (e.g., DTE / 365.0)
    r: Risk-free Interest Rate (e.g., 0.05 for 5%)
    sigma: Implied/Historical Volatility (e.g., 0.20 for 20%)
    option_type: "CALL" or "PUT"
    """
    option_type = option_type.upper()
    
    # Boundary / Safety checks
    if S <= 0 or K <= 0:
        return 0.0
    if T <= 0.00001:  # Extremely close to or at expiration
        if option_type == "CALL":
            return max(0.0, S - K)
        else:
            return max(0.0, K - S)
    if sigma <= 0.00001:
        # Volatility is zero: intrinsic value discounted
        discount_r = math.exp(-r * T)
        discount_q = math.exp(-q * T)
        if option_type == "CALL":
            return max(0.0, S * discount_q - K * discount_r)
        else:
            return max(0.0, K * discount_r - S * discount_q)

    d1 = (math.log(S / K) + (r - q + 0.5 * sigma**2) * T) / (sigma * math.sqrt(T))
    d2 = d1 - sigma * math.sqrt(T)

    discount_r = math.exp(-r * T)
    discount_q = math.exp(-q * T)
    if option_type == "CALL":
        return S * discount_q * norm_cdf(d1) - K * discount_r * norm_cdf(d2)
    else:
        return K * discount_r * norm_cdf(-d2) - S * discount_q * norm_cdf(-d1)

def calculate_delta(
    S: float,
    K: float,
    T: float,
    r: float,
    sigma: float,
    option_type: str = "CALL"
) -> float:
    """
    Calculates the option Delta.
    Call Delta: N(d1)
    Put Delta: N(d1) - 1
    """
    option_type = option_type.upper()
    if S <= 0 or K <= 0:
        return 0.0
    if T <= 0.00001:
        if option_type == "CALL":
            return 1.0 if S > K else 0.0
        else:
            return -1.0 if S < K else 0.0
    if sigma <= 0.00001:
        if option_type == "CALL":
            return 1.0 if S > K else 0.0
        else:
            return -1.0 if S < K else 0.0

    d1 = (math.log(S / K) + (r + 0.5 * sigma**2) * T) / (sigma * math.sqrt(T))
    
    if option_type == "CALL":
        return norm_cdf(d1)
    else:
        return norm_cdf(d1) - 1.0

def calculate_theta(
    S: float,
    K: float,
    T: float,
    r: float,
    sigma: float,
    option_type: str = "CALL"
) -> float:
    """
    Calculates the option Theta (annualized decay rate).
    To get daily theta, divide the returned value by 365.0.
    """
    option_type = option_type.upper()
    if S <= 0 or K <= 0 or T <= 0.00001 or sigma <= 0.00001:
        return 0.0

    d1 = (math.log(S / K) + (r + 0.5 * sigma**2) * T) / (sigma * math.sqrt(T))
    d2 = d1 - sigma * math.sqrt(T)

    discount = math.exp(-r * T)
    pdf_d1 = norm_pdf(d1)
    
    term1 = -(S * pdf_d1 * sigma) / (2.0 * math.sqrt(T))
    
    if option_type == "CALL":
        term2 = -r * K * discount * norm_cdf(d2)
        return term1 + term2
    else:
        term2 = r * K * discount * norm_cdf(-d2)
        return term1 - term2

def calculate_historical_volatility(
    prices: Union[List[float], np.ndarray, pd.Series],
    window: int = 20,
    annualization_factor: float = 252.0
) -> float:
    """
    Calculates the historical annualized volatility of price history.
    prices: Sequence of closing prices
    window: Rolling window size (number of bars)
    annualization_factor: 252 for daily data, or adjusted for intraday bars.
    """
    if len(prices) < 2:
        return 0.15  # Fallback baseline IV

    if isinstance(prices, (list, np.ndarray)):
        prices = pd.Series(prices)

    # Calculate log returns
    log_returns = np.log(prices / prices.shift(1))
    
    # Calculate standard deviation
    rolling_std = log_returns.rolling(window=window).std()
    
    # Get last value or default
    last_std = rolling_std.iloc[-1]
    if pd.isna(last_std) or last_std <= 0:
        # Fallback to absolute standard deviation of all returns
        std_all = log_returns.std()
        last_std = std_all if not pd.isna(std_all) and std_all > 0 else 0.01

    # Annualize the standard deviation
    annualized_vol = last_std * math.sqrt(annualization_factor)
    return max(0.01, annualized_vol)
