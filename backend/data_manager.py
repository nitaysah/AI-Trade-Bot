"""
Historical data manager for backtesting.

Downloads data from Yahoo Finance and caches locally in backend/data/.
Handles timezone normalisation so the rest of the codebase never sees
tz-aware timestamps.
"""

import os
import re
import json
import threading
import pandas as pd
import yfinance as yf
from datetime import datetime, timedelta, timezone

try:
    import config
except ImportError:
    config = None

DATA_DIR = os.path.realpath(os.path.join(os.path.dirname(__file__), "data"))
os.makedirs(DATA_DIR, exist_ok=True)

# Cached files carry this tag. Everything the app caches is split-adjusted
# (prices divided by later split ratios, dividends NOT added back). Files
# written before this tag existed have an unknown adjustment basis and are
# ignored rather than merged.
ADJUSTMENT_BASIS = "split"
CACHE_TAG = "_adjsplit"


_cache_lock = threading.Lock()
_CACHE_TICKER_RE = re.compile(r"^[A-Z0-9.\-]{1,15}$")
_CACHE_TIMEFRAME_RE = re.compile(r"^[A-Za-z0-9]{1,10}$")


def _cached_candle_files(ticker):
    """Candle-cache files for ``ticker`` as ``[(path, timeframe, provider, is_meta)]``.

    Only files this module writes match (``<TICKER>_<timeframe>[_yahoo]_adjsplit.csv`` and its
    ``.meta.json``); settings, databases and legacy untagged files in the same folder never do."""
    symbol = str(ticker or "").strip().upper()
    if not _CACHE_TICKER_RE.fullmatch(symbol):
        return []
    found = []
    prefix = f"{symbol}_"
    for name in os.listdir(DATA_DIR):
        if not name.startswith(prefix):
            continue
        is_meta = name.endswith(f"{CACHE_TAG}.csv.meta.json")
        if not (is_meta or name.endswith(f"{CACHE_TAG}.csv")):
            continue
        middle = name[len(prefix):-len(f"{CACHE_TAG}.csv.meta.json" if is_meta else f"{CACHE_TAG}.csv")]
        provider = "alpaca"
        if middle.endswith("_yahoo"):
            middle, provider = middle[:-len("_yahoo")], "yahoo"
        if not _CACHE_TIMEFRAME_RE.fullmatch(middle):
            continue
        path = os.path.realpath(os.path.join(DATA_DIR, name))
        if path.startswith(DATA_DIR + os.sep):
            found.append((path, middle, provider, is_meta))
    return found


def list_cached_data(ticker):
    """Cached candle sets for a ticker (what a backtest would reuse instead of downloading)."""
    entries = []
    for path, timeframe, provider, is_meta in _cached_candle_files(ticker):
        if is_meta:
            continue
        meta = _read_meta(f"{path}.meta.json")
        try:
            stat = os.stat(path)
        except OSError:
            continue
        entries.append({
            "timeframe": timeframe,
            "requested_provider": provider,
            "data_source": meta.get("data_source") or provider,
            "feed": meta.get("feed"),
            "fetched_at": meta.get("fetched_at"),
            "modified_at": datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc).isoformat(),
            "size_kb": round(stat.st_size / 1024.0, 1),
        })
    entries.sort(key=lambda item: (item["timeframe"], item["requested_provider"]))
    return entries


def purge_cached_data(ticker):
    """Delete the cached candle files (all timeframes, both providers) for one ticker.

    The next backtest downloads fresh data: Alpaca first, Yahoo as the fallback. Returns the
    timeframes removed and the number of files deleted."""
    removed_files, timeframes = 0, set()
    with _cache_lock:
        for path, timeframe, _provider, is_meta in _cached_candle_files(ticker):
            try:
                os.remove(path)
            except FileNotFoundError:
                continue
            removed_files += 1
            if not is_meta:
                timeframes.add(timeframe)
    return {"files_removed": removed_files, "timeframes": sorted(timeframes)}


def _stamp_attrs(frame, *, source, feed=None, quality="OK", notes=None, splits_checked=True):
    """Attach provenance to a frame (survives slicing only when re-applied)."""
    frame.attrs["data_source"] = source
    frame.attrs["feed"] = feed
    frame.attrs["adjustment"] = ADJUSTMENT_BASIS
    frame.attrs["data_quality"] = quality
    frame.attrs["split_notes"] = list(notes or [])
    frame.attrs["splits_checked"] = bool(splits_checked)
    return frame


def _read_meta(meta_path):
    try:
        if os.path.exists(meta_path):
            with open(meta_path, "r", encoding="utf-8") as meta_file:
                return json.load(meta_file) or {}
    except Exception as meta_exc:
        print(f"[data] Cache metadata read error: {meta_exc}")
    return {}


def _cache_predates_split(meta, is_crypto, ticker):
    """True when a split took effect after the cache was fetched.

    Split adjustment is retroactive, so history cached before an ex-date is on
    the wrong basis and must be refetched.
    """
    if is_crypto:
        return False
    fetched_at = meta.get("fetched_at")
    if not fetched_at:
        return True
    try:
        from corporate_actions_service import get_split_events
        lookup = get_split_events(ticker)
        if not lookup.available:
            return False
        fetched_day = datetime.fromisoformat(fetched_at).date()
        today = datetime.utcnow().date()
        return any(fetched_day < event.ex_date <= today for event in lookup.events)
    except Exception as exc:
        print(f"[data] Split freshness check skipped: {type(exc).__name__}")
        return False


def _reconcile_splits(frame, ticker, is_crypto):
    """Return ``(frame, quality, notes, splits_checked)`` on a split-adjusted basis.

    Known events are classified against the data (raw or already adjusted) so
    nothing is adjusted twice. When corporate-action data is unavailable, a
    split-like jump marks the frame DEGRADED instead of guessing.
    """
    if is_crypto or frame is None or frame.empty:
        return frame, "OK", [], True
    from corporate_actions import reconcile_split_adjustment, suspect_split_jumps
    from corporate_actions_service import get_split_events

    first_day = frame.index[0].date() if hasattr(frame.index[0], "date") else None
    lookup = get_split_events(ticker, start=first_day)
    if lookup.available:
        adjusted, quality, notes = reconcile_split_adjustment(frame, lookup.events)
        return adjusted, quality, notes, True
    jumps = suspect_split_jumps(frame)
    quality = "DEGRADED" if jumps else "OK"
    notes = [{"status": "events_unavailable", "suspect_jumps": len(jumps)}]
    return frame, quality, notes, False


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
    filename = os.path.basename(f"{clean_ticker}_{clean_timeframe}{source_suffix}{CACHE_TAG}.csv")
    
    # Resolve absolute path and restrict to DATA_DIR
    filepath = os.path.realpath(os.path.join(DATA_DIR, filename))
    if not filepath.startswith(DATA_DIR + os.sep):
        print(f"[security] Blocked path traversal attempt: {filepath}")
        return None
    meta_path = f"{filepath}.meta.json"
    is_crypto_symbol = any(clean_ticker.endswith(base) for base in ("USD", "USDT", "USDC"))

    # Force naive datetimes internally
    if hasattr(start_date, "tzinfo") and start_date.tzinfo is not None:
        start_date = start_date.replace(tzinfo=None)
    if hasattr(end_date, "tzinfo") and end_date.tzinfo is not None:
        end_date = end_date.replace(tzinfo=None)

    # ------------------------------------------------------------------
    # 1. Try the local cache
    # ------------------------------------------------------------------
    df = None
    cache_meta = {}
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

                cache_meta = _read_meta(meta_path)
                cache_is_stale = _cache_predates_split(cache_meta, is_crypto_symbol, clean_ticker)
                if cache_is_stale:
                    print(f"[data] Cache for {ticker} predates a split; refetching on the split-adjusted basis.")
                    force_refresh = True
                if not force_refresh and file_start <= start_date and file_end >= (end_date - timedelta(days=1)):
                    print(f"[data] Cache hit for {ticker}")
                    cached_source = cache_meta.get("data_source") or ("yahoo" if force_yahoo or source_suffix else "alpaca")
                    sliced = df.loc[start_date:end_date]
                    _stamp_attrs(
                        sliced,
                        source=cached_source,
                        feed=cache_meta.get("feed"),
                        quality=cache_meta.get("data_quality", "OK"),
                        notes=cache_meta.get("split_notes"),
                        splits_checked=cache_meta.get("splits_checked", True),
                    )
                    return sliced
                if cache_is_stale:
                    df = None
        except Exception as exc:
            print(f"[data] Cache read error: {exc}")
            df = None

    # ------------------------------------------------------------------
    # 2. Download from Alpaca (Primary Provider)
    # ------------------------------------------------------------------
    alpaca_success = False
    alpaca_feed = None
    new_df = None

    if not force_yahoo:
        try:
            from broker_factory import get_alpaca_clients
            from alpaca.data.requests import StockBarsRequest, CryptoBarsRequest
            from alpaca.data.timeframe import TimeFrame, TimeFrameUnit
            from alpaca.data.enums import DataFeed, Adjustment

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
                            adjustment=Adjustment.SPLIT,
                        )
                        bars = client.get_stock_bars(req_sip)
                        alpaca_feed = "sip"
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
                            adjustment=Adjustment.SPLIT,
                        )
                        bars = client.get_stock_bars(req_iex)
                        alpaca_feed = "iex"

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
                    alpaca_df.attrs["feed"] = alpaca_feed
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
            # auto_adjust=False keeps ``Close`` split-adjusted but not
            # dividend-adjusted, matching Alpaca's adjustment="split" basis.
            # Daily bars have no pre/post-market sessions.
            new_df = yf.download(
                yf_ticker,
                period=period,
                interval=interval,
                prepost=(interval != "1d"),
                progress=False,
                auto_adjust=False,
            )

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
            # Yahoo's "Adj Close" is dividend-adjusted; the app uses a
            # split-only basis everywhere, so mirror the split-adjusted close.
            if "close" in new_df.columns:
                new_df["adj_close"] = new_df["close"]
            new_df.attrs["data_source"] = "yahoo"
            new_df.attrs["feed"] = "yahoo"
        except Exception as exc:
            print(f"[data] Yahoo Download error: {exc}")
            return None if force_refresh else df

    # ------------------------------------------------------------------
    # 4. Save and return merged results
    # ------------------------------------------------------------------
    if new_df is not None and not new_df.empty:
        fetched_source = new_df.attrs.get("data_source") or ("yahoo" if force_yahoo or not alpaca_success else "alpaca")
        feed = new_df.attrs.get("feed")
        # Put the fresh bars on the split-adjusted basis before they are merged
        # into (or become) the cache.
        new_df, quality, split_notes, splits_checked = _reconcile_splits(new_df.sort_index(), clean_ticker, is_crypto_symbol)
        # Merge with any existing cache
        if df is not None and not df.empty:
            combined = pd.concat([df, new_df])
            combined = combined[~combined.index.duplicated(keep="last")].sort_index()
            df = combined
            if cache_meta.get("data_quality") == "DEGRADED":
                quality = "DEGRADED"
            if cache_meta.get("feed") and feed and cache_meta.get("feed") != feed:
                feed = "mixed"
        else:
            df = new_df.sort_index()

        # Resample the final combined DataFrame before persisting!
        df = _resample_df(df, timeframe)
        _stamp_attrs(df, source=fetched_source, feed=feed, quality=quality, notes=split_notes, splits_checked=splits_checked)
        # Persist to disk
        df.to_csv(filepath)
        try:
            with open(meta_path, "w", encoding="utf-8") as meta_file:
                json.dump({
                    "data_source": fetched_source,
                    "feed": feed,
                    "adjustment": ADJUSTMENT_BASIS,
                    "data_quality": quality,
                    "split_notes": split_notes,
                    "splits_checked": splits_checked,
                    "fetched_at": datetime.utcnow().isoformat(),
                }, meta_file, default=str)
        except Exception as meta_exc:
            print(f"[data] Cache metadata write error: {meta_exc}")

    # Return the requested slice
    if df is not None:
        sliced = df.loc[start_date:end_date]
        _stamp_attrs(
            sliced,
            source=df.attrs.get("data_source") or ("yahoo" if force_yahoo else "alpaca"),
            feed=df.attrs.get("feed"),
            quality=df.attrs.get("data_quality", "OK"),
            notes=df.attrs.get("split_notes"),
            splits_checked=df.attrs.get("splits_checked", True),
        )
        return sliced
    return None
