"""
Download Massive.com end-of-day stock data to CSV.

Examples:
  python massive_eod_downloader.py --grouped --date 2026-07-08
  python massive_eod_downloader.py --ticker AAPL --start 2026-07-01 --end 2026-07-08
  python massive_eod_downloader.py --tickers AAPL MSFT NVDA --start 2026-07-01 --end 2026-07-08

Environment:
  MASSIVE_API_KEY=...
  MASSIVE_BASE_URL=https://api.massive.com
"""

from __future__ import annotations

import argparse
import os
import re
import sys
import time
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Iterable
from zoneinfo import ZoneInfo

import pandas as pd
import requests
from dotenv import load_dotenv

import config as global_config

ET = ZoneInfo("America/New_York")
PROJECT_ROOT = Path(__file__).resolve().parent.parent
OUTPUT_DIR = Path(__file__).resolve().parent / "data" / "massive_eod"

load_dotenv(PROJECT_ROOT / ".env")
load_dotenv(Path(__file__).resolve().parent / ".env")


def _business_day_default() -> str:
    """Return the most recent completed calendar day, backed up to a weekday."""
    current = datetime.now(ET).date() - timedelta(days=1)
    while current.weekday() >= 5:
        current -= timedelta(days=1)
    return current.isoformat()


def _safe_slug(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]+", "_", value.strip())


def _ensure_date(value: str | None, fallback: str | None = None) -> str:
    candidate = value or fallback
    if not candidate:
        raise ValueError("A date value is required.")
    try:
        return datetime.strptime(candidate, "%Y-%m-%d").date().isoformat()
    except ValueError as exc:
        raise ValueError(f"Invalid date '{candidate}'. Use YYYY-MM-DD.") from exc


def _coerce_results(payload: Any) -> list[dict[str, Any]]:
    if isinstance(payload, dict):
        results = payload.get("results", [])
        if isinstance(results, list):
            return [r for r in results if isinstance(r, dict)]
    if isinstance(payload, list):
        return [r for r in payload if isinstance(r, dict)]
    return []


def _ms_to_et_date(value: Any) -> str | None:
    if value is None:
        return None
    try:
        ts = pd.to_datetime(int(value), unit="ms", utc=True).tz_convert(ET)
        return ts.date().isoformat()
    except Exception:
        return None


def _ms_to_et_timestamp(value: Any) -> str | None:
    if value is None:
        return None
    try:
        ts = pd.to_datetime(int(value), unit="ms", utc=True).tz_convert(ET)
        return ts.isoformat()
    except Exception:
        return None


class MassiveClient:
    def __init__(self, api_key: str, base_url: str):
        self.api_key = api_key.strip()
        self.base_url = base_url.rstrip("/")
        self.session = requests.Session()
        self.session.headers.update(
            {
                "Authorization": f"Bearer {self.api_key}",
                "Accept": "application/json",
                "User-Agent": "AI-Trade-Bot/1.0",
            }
        )

    def get_json(self, path_or_url: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        url = path_or_url if path_or_url.startswith("http") else f"{self.base_url}{path_or_url}"
        backoff = 1.0
        for attempt in range(1, 5):
            try:
                resp = self.session.get(url, params=params, timeout=30)
                if resp.status_code == 429 or resp.status_code >= 500:
                    raise requests.HTTPError(
                        f"HTTP {resp.status_code}: {resp.text[:300]}", response=resp
                    )
                resp.raise_for_status()
                payload = resp.json()
                if not isinstance(payload, dict):
                    return {"results": _coerce_results(payload)}
                return payload
            except Exception as exc:
                if attempt >= 4:
                    raise
                print(f"[massive] request failed (attempt {attempt}/4): {exc}")
                time.sleep(backoff)
                backoff *= 2
        raise RuntimeError("Unreachable")


def _fetch_paginated(client: MassiveClient, path: str, params: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    payload = client.get_json(path, params=params)
    all_results = _coerce_results(payload)
    next_url = payload.get("next_url") if isinstance(payload, dict) else None

    while next_url:
        payload = client.get_json(next_url)
        all_results.extend(_coerce_results(payload))
        next_url = payload.get("next_url") if isinstance(payload, dict) else None

    return all_results


def download_grouped_daily(client: MassiveClient, target_date: str, include_otc: bool = False, adjusted: bool = True) -> pd.DataFrame:
    path = f"/v2/aggs/grouped/locale/us/market/stocks/{target_date}"
    payload = client.get_json(
        path,
        params={
            "adjusted": str(adjusted).lower(),
            "include_otc": str(include_otc).lower(),
        },
    )
    results = _coerce_results(payload)
    rows: list[dict[str, Any]] = []
    for item in results:
        rows.append(
            {
                "ticker": item.get("T"),
                "trade_date": _ms_to_et_date(item.get("t")) or target_date,
                "timestamp_et": _ms_to_et_timestamp(item.get("t")),
                "open": item.get("o"),
                "high": item.get("h"),
                "low": item.get("l"),
                "close": item.get("c"),
                "volume": item.get("v"),
                "vwap": item.get("vw"),
                "transactions": item.get("n"),
                "adjusted": payload.get("adjusted") if isinstance(payload, dict) else adjusted,
                "source": "grouped_daily",
            }
        )
    return pd.DataFrame(rows)


def download_ticker_daily_range(
    client: MassiveClient,
    ticker: str,
    start_date: str,
    end_date: str,
    adjusted: bool = True,
) -> pd.DataFrame:
    ticker = ticker.upper().strip()
    path = f"/v2/aggs/ticker/{ticker}/range/1/day/{start_date}/{end_date}"
    results = _fetch_paginated(
        client,
        path,
        params={"adjusted": str(adjusted).lower()},
    )

    rows: list[dict[str, Any]] = []
    for item in results:
        rows.append(
            {
                "ticker": ticker,
                "trade_date": _ms_to_et_date(item.get("t")),
                "timestamp_et": _ms_to_et_timestamp(item.get("t")),
                "open": item.get("o"),
                "high": item.get("h"),
                "low": item.get("l"),
                "close": item.get("c"),
                "volume": item.get("v"),
                "vwap": item.get("vw"),
                "transactions": item.get("n"),
                "adjusted": adjusted,
                "source": "ticker_range",
            }
        )
    return pd.DataFrame(rows)


def download_ticker_prev(client: MassiveClient, ticker: str, adjusted: bool = True) -> pd.DataFrame:
    ticker = ticker.upper().strip()
    path = f"/v2/aggs/ticker/{ticker}/prev"
    payload = client.get_json(path, params={"adjusted": str(adjusted).lower()})
    results = _coerce_results(payload)
    rows: list[dict[str, Any]] = []
    for item in results:
        rows.append(
            {
                "ticker": ticker,
                "trade_date": _ms_to_et_date(item.get("t")),
                "timestamp_et": _ms_to_et_timestamp(item.get("t")),
                "open": item.get("o"),
                "high": item.get("h"),
                "low": item.get("l"),
                "close": item.get("c"),
                "volume": item.get("v"),
                "vwap": item.get("vw"),
                "transactions": item.get("n"),
                "adjusted": payload.get("adjusted") if isinstance(payload, dict) else adjusted,
                "source": "ticker_prev",
            }
        )
    return pd.DataFrame(rows)


def _write_frame(df: pd.DataFrame, output_path: Path) -> Path:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    if not df.empty:
        sort_cols = [c for c in ["ticker", "trade_date", "timestamp_et"] if c in df.columns]
        if sort_cols:
            df = df.sort_values(sort_cols, kind="stable")
    df.to_csv(output_path, index=False)
    return output_path


def _load_tickers(args: argparse.Namespace) -> list[str]:
    tickers: list[str] = []
    if args.ticker:
        tickers.append(args.ticker)
    tickers.extend(args.tickers or [])
    if args.tickers_file:
        file_path = Path(args.tickers_file)
        if not file_path.exists():
            raise FileNotFoundError(f"Ticker file not found: {file_path}")
        for line in file_path.read_text().splitlines():
            symbol = line.strip()
            if symbol and not symbol.startswith("#"):
                tickers.append(symbol)
    deduped: list[str] = []
    seen = set()
    for ticker in tickers:
        clean = ticker.strip().upper()
        if clean and clean not in seen:
            seen.add(clean)
            deduped.append(clean)
    return deduped


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Download Massive.com end-of-day stock data.")
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--grouped", action="store_true", help="Download the full U.S. market daily summary for one date.")
    mode.add_argument("--ticker", help="Download daily bars for a single ticker.")
    mode.add_argument("--tickers", nargs="+", help="Download daily bars for multiple tickers.")
    parser.add_argument("--tickers-file", help="Optional file with one ticker per line.")
    parser.add_argument("--date", help="Target trading date for grouped mode (YYYY-MM-DD). Defaults to the most recent weekday.")
    parser.add_argument("--start", help="Start date for ticker mode (YYYY-MM-DD). Defaults to --date.")
    parser.add_argument("--end", help="End date for ticker mode (YYYY-MM-DD). Defaults to the most recent weekday.")
    parser.add_argument("--output-dir", default=str(OUTPUT_DIR), help="Directory for CSV output.")
    parser.add_argument("--include-otc", action="store_true", help="Include OTC symbols in grouped mode.")
    parser.add_argument("--raw", action="store_true", help="Do not overwrite existing output files; add a timestamp suffix.")
    parser.add_argument("--use-prev", action="store_true", help="Use the previous-day endpoint instead of the daily range endpoint for ticker mode.")
    return parser


def main(argv: Iterable[str] | None = None) -> int:
    args = build_parser().parse_args(list(argv) if argv is not None else None)

    api_key = os.getenv("MASSIVE_API_KEY") or getattr(global_config, "MASSIVE_API_KEY", "")
    base_url = os.getenv("MASSIVE_BASE_URL") or getattr(global_config, "MASSIVE_BASE_URL", "https://api.massive.com")
    if not api_key:
        print("Missing MASSIVE_API_KEY. Add it to backend/.env or the project-root .env.")
        return 2

    client = MassiveClient(api_key=api_key, base_url=base_url)
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    if args.grouped:
        target_date = _ensure_date(args.date, _business_day_default())
        df = download_grouped_daily(client, target_date=target_date, include_otc=args.include_otc)
        suffix = f"{target_date}.csv"
        output_path = output_dir / f"massive_grouped_{suffix}"
        if args.raw and output_path.exists():
            output_path = output_dir / f"massive_grouped_{target_date}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv"
        written = _write_frame(df, output_path)
        print(f"[massive] saved {len(df)} rows to {written}")
        return 0

    tickers = _load_tickers(args)
    if not tickers:
        print("Provide at least one ticker via --ticker, --tickers, or --tickers-file.")
        return 2

    start_date = _ensure_date(args.start, args.date or _business_day_default())
    end_date = _ensure_date(args.end, args.date or _business_day_default())

    exit_code = 0
    for ticker in tickers:
        try:
            if args.use_prev:
                df = download_ticker_prev(client, ticker)
            else:
                df = download_ticker_daily_range(client, ticker, start_date=start_date, end_date=end_date)

            if df.empty:
                print(f"[massive] no rows returned for {ticker}")
                continue

            suffix = f"{start_date}_to_{end_date}" if not args.use_prev else "prev"
            output_path = output_dir / f"massive_{_safe_slug(ticker)}_{suffix}.csv"
            if args.raw and output_path.exists():
                output_path = output_dir / f"massive_{_safe_slug(ticker)}_{suffix}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv"
            written = _write_frame(df, output_path)
            print(f"[massive] saved {len(df)} rows for {ticker} to {written}")
        except Exception as exc:
            exit_code = 1
            print(f"[massive] failed for {ticker}: {exc}")

    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
