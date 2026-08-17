"""
Historical data manager for backtesting.

Downloads data from Yahoo Finance and caches locally in backend/data/.
Handles timezone normalisation so the rest of the codebase never sees
tz-aware timestamps.
"""

import os
import json
import pandas as pd
import yfinance as yf
from datetime import datetime, timedelta

try:
    import config
except ImportError:
    config = None

DATA_DIR = os.path.realpath(os.path.join(os.path.dirname(__file__), "data"))
os.makedirs(DATA_DIR, exist_ok=True)


def _strip_tz(idx):
    """Remove timezone info from a DatetimeIndex, if present."""
    if hasattr(idx, "tz") and idx.tz is not None:
        return idx.tz_localize(None)
    # Already naive, or not a DatetimeIndex at all
    return idx


def _resample_df(df, timeframe):
    """Resample DataFrame to the correct timeframe frequency if needed."""
    if df is None or df.empty:
        return df

    resample_rules = {
        "3Min": "3min",
        "10Min": "10min",
        "2Hour": "2h",
        "4Hour": "4h",
    }

    rule = resample_rules.get(timeframe)
    if not rule:
        return df

    # Standardise column names to lowercase for robust matching during resampling
    rename_cols = {}
    for col in df.columns:
        if col.lower() != col:
            rename_cols[col] = col.lower()
    if rename_cols:
        df = df.rename(columns=rename_cols)

    # Build safe aggregation dict based on columns present in df
    agg_dict = {}
    for col, agg_func in [
        ("open", "first"),
        ("high", "max"),
        ("low", "min"),
        ("close", "last"),
        ("adj_close", "last"),
        ("volume", "sum")
    ]:
        if col in df.columns:
            agg_dict[col] = agg_func

    if not agg_dict:
        return df

    print(f"[data] Resampling data to {timeframe} using rule '{rule}'...")
    try:
        # Pandas resample requires a DatetimeIndex
        resampled = df.resample(rule).agg(agg_dict).dropna()
        return resampled
    except Exception as exc:
        print(f"[data] Resampling failed for {timeframe}: {exc}")
        return df


def get_historical_data(ticker, timeframe, start_date, end_date, force_yahoo=False, force_refresh=False):
    """
    Return a DataFrame of OHLCV data for *ticker* between *start_date* and
    *end_date*.  Uses a local CSV cache; downloads from Webull (if configured)
    or Yahoo Finance on a cache miss.  A forced refresh bypasses the local
    cache and does not fall back to stale cached data if the provider fails.
    """
    import re
    # Strict sanitization to prevent path traversal
    clean_ticker = re.sub(r'[^A-Za-z0-9.\-]', '', ticker).upper()
    clean_timeframe = re.sub(r'[^A-Za-z0-9]', '', timeframe)
    source_suffix = "_yahoo" if force_yahoo else ""
    filename = os.path.basename(f"{clean_ticker}_{clean_timeframe}{source_suffix}.csv")
    
    # Resolve absolute path and restrict to DATA_DIR
    filepath = os.path.realpath(os.path.join(DATA_DIR, filename))
    if not filepath.startswith(DATA_DIR + os.sep):
        print(f"[security] Blocked path traversal attempt: {filepath}")
        return None
    meta_path = f"{filepath}.meta.json"

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

                # Cache self-healing: resample if the cached file has hourly/high frequency data
                original_len = len(df)
                df = _resample_df(df, timeframe)
                if len(df) != original_len:
                    print(f"[data] Healing cache for {ticker} ({timeframe}): resampled from {original_len} to {len(df)} bars.")
                    df.to_csv(filepath)

                file_start = df.index[0]
                file_end = df.index[-1]

                if not force_refresh and file_start <= start_date and file_end >= (end_date - timedelta(days=1)):
                    print(f"[data] Cache hit for {ticker}")
                    cached_source = "yahoo" if force_yahoo or source_suffix else "alpaca"
                    try:
                        if os.path.exists(meta_path):
                            with open(meta_path, "r", encoding="utf-8") as meta_file:
                                cached_source = json.load(meta_file).get("data_source") or cached_source
                    except Exception as meta_exc:
                        print(f"[data] Cache metadata read error: {meta_exc}")
                    sliced = df.loc[start_date:end_date]
                    sliced.attrs["data_source"] = cached_source
                    return sliced
        except Exception as exc:
            print(f"[data] Cache read error: {exc}")
            df = None

    # ------------------------------------------------------------------
    # 2. Download from Alpaca (Primary Provider)
    # ------------------------------------------------------------------
    alpaca_success = False
    new_df = None

    if not force_yahoo:
        try:
            from broker_factory import get_alpaca_clients
            from alpaca.data.requests import StockBarsRequest, CryptoBarsRequest
            from alpaca.data.timeframe import TimeFrame, TimeFrameUnit
            from alpaca.data.enums import DataFeed

            s_client, c_client = get_alpaca_clients()

            clean_ticker = re.sub(r'[^A-Za-z0-9.\-]', '', ticker).upper()
            is_crypto = any(clean_ticker.endswith(base) for base in ("USD", "USDT", "USDC"))

            client = c_client if is_crypto else s_client
            if client is not None:
                alpaca_tf_map = {
                    "30Sec": TimeFrame.Minute,
                    "1Min": TimeFrame.Minute,
                    "2Min": TimeFrame(2, TimeFrameUnit.Minute),
                    "3Min": TimeFrame(3, TimeFrameUnit.Minute),
                    "5Min": TimeFrame(5, TimeFrameUnit.Minute),
                    "10Min": TimeFrame(10, TimeFrameUnit.Minute),
                    "15Min": TimeFrame(15, TimeFrameUnit.Minute),
                    "30Min": TimeFrame(30, TimeFrameUnit.Minute),
                    "1Hour": TimeFrame.Hour,
                    "2Hour": TimeFrame(2, TimeFrameUnit.Hour),
                    "4Hour": TimeFrame(4, TimeFrameUnit.Hour),
                    "1Day": TimeFrame.Day,
                }
                tf_obj = alpaca_tf_map.get(timeframe, TimeFrame.Day)

                print(f"[data] Attempting Alpaca fetch for {ticker} ({timeframe}) from {start_date} to {end_date}...")
                if is_crypto:
                    crypto_symbol = clean_ticker
                    for base in ["USDT", "USDC", "USD"]:
                        if clean_ticker.endswith(base) and not clean_ticker.startswith(f"{base}/"):
                            crypto_symbol = clean_ticker.replace(base, f"/{base}")
                            break
                    req = CryptoBarsRequest(
                        symbol_or_symbols=[crypto_symbol],
                        timeframe=tf_obj,
                        start=start_date,
                        end=end_date,
                    )
                    bars = client.get_crypto_bars(req)
                else:
                    bars = None
                    try:
                        req_sip = StockBarsRequest(
                            symbol_or_symbols=[clean_ticker],
                            timeframe=tf_obj,
                            start=start_date,
                            end=end_date,
                            feed=DataFeed.SIP,
                        )
                        bars = client.get_stock_bars(req_sip)
                    except Exception as sip_err:
                        print(f"[data] Alpaca SIP feed error: {sip_err}. Retrying with IEX feed...")
                        bars = None

                    if bars is None or not hasattr(bars, "df") or bars.df is None or bars.df.empty:
                        req_iex = StockBarsRequest(
                            symbol_or_symbols=[clean_ticker],
                            timeframe=tf_obj,
                            start=start_date,
                            end=end_date,
                            feed=DataFeed.IEX,
                        )
                        bars = client.get_stock_bars(req_iex)

                if bars and hasattr(bars, "df") and bars.df is not None and not bars.df.empty:
                    alpaca_df = bars.df.copy()
                    if isinstance(alpaca_df.index, pd.MultiIndex):
                        if "symbol" in alpaca_df.index.names:
                            alpaca_df = alpaca_df.reset_index(level="symbol", drop=True)
                        else:
                            alpaca_df = alpaca_df.droplevel(0)

                    # Timezone handling: convert all feeds to America/New_York naive format
                    if hasattr(alpaca_df.index, "tz") and alpaca_df.index.tz is not None:
                        alpaca_df.index = alpaca_df.index.tz_convert("America/New_York")
                    alpaca_df.index = _strip_tz(alpaca_df.index)

                    # Standardise column names
                    rename_map = {}
                    for col in alpaca_df.columns:
                        rename_map[col] = col.lower()
                    alpaca_df = alpaca_df.rename(columns=rename_map)
                    if "adj_close" not in alpaca_df.columns and "close" in alpaca_df.columns:
                        alpaca_df["adj_close"] = alpaca_df["close"]

                    alpaca_df.attrs["data_source"] = "alpaca"
                    new_df = alpaca_df
                    alpaca_success = True
                    print(f"[data] Alpaca fetch successful. Retrieved {len(new_df)} bars.")
                else:
                    print(f"[data] Alpaca returned no bars for {ticker}. Falling back to Yahoo...")
            else:
                print(f"[data] Alpaca client not available (keys missing). Falling back to Yahoo...")
        except Exception as alpaca_exc:
            print(f"[data] Alpaca download failed for {ticker}: {alpaca_exc}. Falling back to Yahoo...")

    # ------------------------------------------------------------------
    # 3. Fallback: Download from Yahoo Finance
    # ------------------------------------------------------------------
    if not alpaca_success:
        interval_map = {
            "30Sec": "1m",
            "1Min": "1m",
            "2Min": "2m",
            "3Min": "1m",
            "5Min": "5m",
            "10Min": "5m",
            "15Min": "15m",
            "30Min": "30m",
            "1Hour": "1h",
            "2Hour": "1h",
            "4Hour": "1h",
            "1Day": "1d",
        }
        interval = interval_map.get(timeframe, "1d")

        try:
            # Pick a "period" that covers the request dynamically to avoid huge redundant downloads
            diff_days = max(1, (end_date - start_date).days) if end_date and start_date else 60
            
            if interval == "1m":
                period = "7d"
            elif interval in ("5m", "15m", "30m"):
                if diff_days <= 1:
                    period = "1d"
                elif diff_days <= 5:
                    period = "5d"
                elif diff_days <= 30:
                    period = "1mo"
                else:
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

            print(f"[data] Downloading {ticker} (via {yf_ticker}) ({interval}) for period: {period} with extended hours...")
            new_df = yf.download(yf_ticker, period=period, interval=interval, prepost=True, progress=False)

            if new_df is None or new_df.empty:
                return None if force_refresh else df

            # Convert timezone to America/New_York before stripping
            if hasattr(new_df.index, "tz") and new_df.index.tz is not None:
                new_df.index = new_df.index.tz_convert("America/New_York")
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
            new_df.attrs["data_source"] = "yahoo"
        except Exception as exc:
            print(f"[data] Yahoo Download error: {exc}")
            return None if force_refresh else df

    # ------------------------------------------------------------------
    # 4. Save and return merged results
    # ------------------------------------------------------------------
    if new_df is not None and not new_df.empty:
        fetched_source = new_df.attrs.get("data_source") or ("yahoo" if force_yahoo or not alpaca_success else "alpaca")
        # Merge with any existing cache
        if df is not None and not df.empty:
            combined = pd.concat([df, new_df])
            combined = combined[~combined.index.duplicated(keep="last")].sort_index()
            df = combined
        else:
            df = new_df.sort_index()
        df.attrs["data_source"] = fetched_source

        # Resample the final combined DataFrame before persisting!
        df = _resample_df(df, timeframe)
        df.attrs["data_source"] = fetched_source
        # Persist to disk
        df.to_csv(filepath)
        try:
            with open(meta_path, "w", encoding="utf-8") as meta_file:
                json.dump({"data_source": fetched_source}, meta_file)
        except Exception as meta_exc:
            print(f"[data] Cache metadata write error: {meta_exc}")

    # Return the requested slice
    if df is not None:
        sliced = df.loc[start_date:end_date]
        sliced.attrs["data_source"] = df.attrs.get("data_source") or ("yahoo" if force_yahoo else "alpaca")
        return sliced
    return None
