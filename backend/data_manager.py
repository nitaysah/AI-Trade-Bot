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

try:
    import config
    from webull_broker import WebullBroker
except ImportError:
    config = None
    WebullBroker = None

DATA_DIR = os.path.realpath(os.path.join(os.path.dirname(__file__), "data"))
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
    *end_date*.  Uses a local CSV cache; downloads from Webull (if configured)
    or Yahoo Finance on a cache miss.
    """
    import re
    # Strict sanitization to prevent path traversal
    clean_ticker = re.sub(r'[^A-Za-z0-9.\-]', '', ticker).upper()
    clean_timeframe = re.sub(r'[^A-Za-z0-9]', '', timeframe)
    filename = os.path.basename(f"{clean_ticker}_{clean_timeframe}.csv")
    
    # Resolve absolute path and restrict to DATA_DIR
    filepath = os.path.realpath(os.path.join(DATA_DIR, filename))
    if not filepath.startswith(DATA_DIR + os.sep):
        print(f"[security] Blocked path traversal attempt: {filepath}")
        return None

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
    # 2. Download from Webull (if WEBULL_APP_KEY is configured)
    # ------------------------------------------------------------------
    webull_success = False
    new_df = None

    if config and getattr(config, "WEBULL_APP_KEY", ""):
        try:
            print(f"[data] Attempting Webull fetch for {ticker} ({timeframe})...")
            wb_map = {
                "30Sec": "s30",
                "1Min": "m1",
                "2Min": "m2",
                "3Min": "m3",
                "5Min": "m5",
                "10Min": "m10",
                "15Min": "m15",
                "30Min": "m30",
                "1Hour": "h1",
                "2Hour": "h2",
                "4Hour": "h4",
                "1Day": "d1",
            }
            timespan = wb_map.get(timeframe, "d1")
            
            # Calculate dynamic count based on requested start/end range
            diff_days = max(1, (end_date - start_date).days)
            multiplier_map = {
                "30Sec": 2880,
                "1Min": 1440,
                "2Min": 720,
                "3Min": 480,
                "5Min": 2880 if ticker.endswith("USD") else 78,
                "10Min": 144,
                "15Min": 96,
                "30Min": 48,
                "1Hour": 24,
                "2Hour": 12,
                "4Hour": 6,
                "1Day": 1,
            }
            count = (diff_days * multiplier_map.get(timeframe, 1)) + 30
            if count > 1200:
                print(f'[data] Requested {int(count)} bars exceeds Webull 1200 limit. Bypassing to Yahoo.')
                raise Exception('Count exceeds Webull limit')
            count = max(100, int(count))
            
            wb = WebullBroker()
            # Fetch exactly the calculated dynamic bar count
            bars = wb.get_bars(ticker, timespan=timespan, count=count)
            
            if bars:
                new_df = pd.DataFrame(bars)
                # Convert time (epoch seconds) to datetime index
                new_df["time"] = pd.to_datetime(new_df["time"], unit="s")
                new_df.set_index("time", inplace=True)
                new_df.index = _strip_tz(new_df.index)
                
                # Standardise to lowercase column names
                new_df = new_df.rename(columns={
                    "open": "open",
                    "high": "high",
                    "low": "low",
                    "close": "close",
                    "volume": "volume"
                })
                new_df["adj_close"] = new_df["close"] # Compatibility
                
                webull_success = True
                print(f"[data] Webull fetch successful. Retrieved {len(new_df)} bars.")
        except Exception as wb_exc:
            print(f"[data] Webull download failed: {wb_exc}. Falling back to Yahoo...")

    # ------------------------------------------------------------------
    # 3. Fallback: Download from Yahoo Finance
    # ------------------------------------------------------------------
    if not webull_success:
        interval_map = {
            "30Sec": "1m",
            "1Min": "1m",
            "2Min": "2m",
            "3Min": "5m",
            "5Min": "5m",
            "10Min": "15m",
            "15Min": "15m",
            "30Min": "30m",
            "1Hour": "1h",
            "2Hour": "1h",
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
        except Exception as exc:
            print(f"[data] Yahoo Download error: {exc}")
            return df

    # ------------------------------------------------------------------
    # 4. Save and return merged results
    # ------------------------------------------------------------------
    if new_df is not None and not new_df.empty:
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
    if df is not None:
        return df.loc[start_date:end_date]
    return None
