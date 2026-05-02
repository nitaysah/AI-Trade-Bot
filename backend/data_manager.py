"""
Historical data manager for backtesting.

Downloads data from Yahoo Finance and caches locally in backend/data/.
Handles timezone normalisation so the rest of the codebase never sees
tz-aware timestamps.
"""

import os
import pandas as pd
import yfinance as yf
from datetime import datetime, timedelta

DATA_DIR = os.path.join(os.path.dirname(__file__), "data")
os.makedirs(DATA_DIR, exist_ok=True)


def _strip_tz(idx):
    """Remove timezone info from a DatetimeIndex, if present."""
    if hasattr(idx, "tz") and idx.tz is not None:
        return idx.tz_localize(None)
    # Already naive, or not a DatetimeIndex at all
    return idx


def get_historical_data(ticker, timeframe, start_date, end_date):
    """
    Return a DataFrame of OHLCV data for *ticker* between *start_date* and
    *end_date*.  Uses a local CSV cache; downloads from Yahoo Finance on a
    cache miss.
    """
    filename = f"{ticker}_{timeframe}.csv"
    filepath = os.path.join(DATA_DIR, filename)

    # Force naive datetimes internally
    if hasattr(start_date, "tzinfo") and start_date.tzinfo is not None:
        start_date = start_date.replace(tzinfo=None)
    if hasattr(end_date, "tzinfo") and end_date.tzinfo is not None:
        end_date = end_date.replace(tzinfo=None)

    # ------------------------------------------------------------------
    # 1. Try the local cache
    # ------------------------------------------------------------------
    df = None
    if os.path.exists(filepath):
        try:
            df = pd.read_csv(filepath, index_col=0, parse_dates=True)
            if not df.empty:
                df.index = _strip_tz(df.index)
                file_start = df.index[0]
                file_end = df.index[-1]

                if file_start <= start_date and file_end >= (end_date - timedelta(days=1)):
                    print(f"[data] Cache hit for {ticker}")
                    return df.loc[start_date:end_date]
        except Exception as exc:
            print(f"[data] Cache read error: {exc}")
            df = None

    # ------------------------------------------------------------------
    # 2. Download from Yahoo Finance
    # ------------------------------------------------------------------
    interval_map = {
        "1Min": "1m",
        "5Min": "5m",
        "15Min": "15m",
        "30Min": "30m",
        "1Hour": "1h",
        "4Hour": "1h",
        "1Day": "1d",
    }
    interval = interval_map.get(timeframe, "1d")

    try:
        # Pick a "period" that covers the request
        if interval == "1m":
            period = "7d"
        elif interval in ("5m", "15m", "30m"):
            period = "60d"
        elif interval == "1h":
            period = "730d"
        else:
            period = "max"

        # --- Yahoo Ticker Translation (Fix for Crypto) ---
        yf_ticker = ticker
        clean_ticker = ticker.upper().replace("/", "")
        is_crypto = any(clean_ticker.endswith(base) for base in ["USD", "USDT", "USDC"])
        
        if is_crypto:
            # Yahoo Finance uses SYMBOL-USD (e.g. BTC-USD)
            for base in ["USDT", "USDC", "USD"]:
                if clean_ticker.endswith(base):
                    yf_ticker = clean_ticker.replace(base, f"-{base}")
                    break

        print(f"[data] Downloading {ticker} (via {yf_ticker}) ({interval}) for period: {period}...")
        new_df = yf.download(yf_ticker, period=period, interval=interval, progress=False)

        if new_df is None or new_df.empty:
            return df  # return whatever cache we have, or None

        # Strip timezone immediately
        new_df.index = _strip_tz(new_df.index)

        # Handle MultiIndex columns (newer yfinance versions)
        if isinstance(new_df.columns, pd.MultiIndex):
            new_df.columns = new_df.columns.get_level_values(0)

        # Standardise to lowercase column names
        new_df = new_df.rename(columns={
            "Open": "open",
            "High": "high",
            "Low": "low",
            "Close": "close",
            "Adj Close": "adj_close",
            "Volume": "volume",
        })

        # Merge with any existing cache
        if df is not None and not df.empty:
            combined = pd.concat([df, new_df])
            combined = combined[~combined.index.duplicated(keep="last")].sort_index()
            df = combined
        else:
            df = new_df.sort_index()

        # Persist to disk
        df.to_csv(filepath)

        # Return the requested slice
        return df.loc[start_date:end_date]

    except Exception as exc:
        print(f"[data] Download error: {exc}")
        return df
