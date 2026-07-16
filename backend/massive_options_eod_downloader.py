"""
Download Massive.com options end-of-day data for a stock's option chain.

Examples:
  python massive_options_eod_downloader.py --underlying AAPL --date 2026-07-08
  python massive_options_eod_downloader.py --underlying TSLA --date 2026-07-08 --contract-type call
  python massive_options_eod_downloader.py --preset mag7_etf --date 2026-07-08 --dte-min 0 --dte-max 10

Environment:
  MASSIVE_API_KEY=...
  MASSIVE_BASE_URL=https://api.massive.com
"""

from __future__ import annotations

import argparse
import os
import re
import time
from datetime import datetime, date, timedelta, time as dtime
from pathlib import Path
from typing import Any, Iterable
from zoneinfo import ZoneInfo
from threading import Lock

import pandas as pd
import requests
from dotenv import load_dotenv

import config as global_config

ET = ZoneInfo("America/New_York")
PROJECT_ROOT = Path(__file__).resolve().parent.parent
OUTPUT_DIR = Path(__file__).resolve().parent / "data" / "massive_options_eod"
DEFAULT_UNDERLYINGS = ["AAPL", "MSFT", "AMZN", "NVDA", "META", "GOOGL", "TSLA", "SPY", "QQQ"]
PRESETS = {
    "mag7_etf": DEFAULT_UNDERLYINGS,
    "mag7": ["AAPL", "MSFT", "AMZN", "NVDA", "META", "GOOGL", "TSLA"],
}

load_dotenv(PROJECT_ROOT / ".env")
load_dotenv(Path(__file__).resolve().parent / ".env")


def _business_day_default() -> str:
    current = datetime.now(ET).date() - timedelta(days=1)
    while current.weekday() >= 5:
        current -= timedelta(days=1)
    return current.isoformat()


def _schedule_day_default(now: datetime | None = None) -> str:
    """Return the market date associated with the next close-time run."""
    now = now or datetime.now(ET)
    candidate = now.date()
    if now.timetz() >= datetime.combine(now.date(), dtime(16, 30), tzinfo=ET).timetz():
        candidate += timedelta(days=1)
    while candidate.weekday() >= 5:
        candidate += timedelta(days=1)
    return candidate.isoformat()


def _ensure_date(value: str | None, fallback: str | None = None) -> str:
    candidate = value or fallback
    if not candidate:
        raise ValueError("A date value is required.")
    try:
        return datetime.strptime(candidate, "%Y-%m-%d").date().isoformat()
    except ValueError as exc:
        raise ValueError(f"Invalid date '{candidate}'. Use YYYY-MM-DD.") from exc


def _safe_slug(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]+", "_", value.strip())


def _output_path_for(underlying: str, target_date: str, dte_min: int, dte_max: int, atm_window: int, output_dir: Path) -> Path:
    suffix = f"{underlying.upper()}_{target_date}_dte{dte_min}to{dte_max}_atm{atm_window}"
    return output_dir / f"{suffix}.csv"


def _date_only(value: str) -> datetime.date:
    return datetime.strptime(value, "%Y-%m-%d").date()


def _dte(as_of: str, expiration_date: str | None) -> int | None:
    if not expiration_date:
        return None
    try:
        return (_date_only(expiration_date) - _date_only(as_of)).days
    except Exception:
        return None


def _is_trading_day(day: date) -> bool:
    return day.weekday() < 5


def _next_market_close_run(now: datetime | None = None) -> datetime:
    """Return the next scheduled run time at 4:30pm ET on a weekday."""
    now = now or datetime.now(ET)
    run_time = dtime(16, 30)
    candidate = now.date()
    if now.timetz() >= datetime.combine(now.date(), run_time, tzinfo=ET).timetz():
        candidate += timedelta(days=1)
    while not _is_trading_day(candidate):
        candidate += timedelta(days=1)
    return datetime.combine(candidate, run_time, tzinfo=ET)


def _coerce_results(payload: Any) -> list[dict[str, Any]]:
    if isinstance(payload, dict):
        results = payload.get("results", [])
        if isinstance(results, list):
            return [r for r in results if isinstance(r, dict)]
    if isinstance(payload, list):
        return [r for r in payload if isinstance(r, dict)]
    return []


def _ms_to_et_timestamp(value: Any) -> str | None:
    if value is None:
        return None
    try:
        ts = pd.to_datetime(int(value), unit="ms", utc=True).tz_convert(ET)
        return ts.isoformat()
    except Exception:
        return None


def _fetch_underlying_close(client: MassiveClient, underlying: str, target_date: str) -> float | None:
    """Fetch the stock close for the underlying on the target date."""
    try:
        payload = client.get_json(
            f"/v1/open-close/{underlying.upper().strip()}/{target_date}",
            params={"adjusted": "true"},
        )
        if isinstance(payload, dict):
            for key in ("close", "c"):
                if payload.get(key) is not None:
                    return float(payload[key])
        results = _coerce_results(payload)
        if results:
            for key in ("close", "c"):
                if results[0].get(key) is not None:
                    return float(results[0][key])
    except Exception as exc:
        print(f"[massive] could not fetch {underlying} close for {target_date}: {exc}")
    return None


class MassiveClient:
    def __init__(self, api_key: str, base_url: str, min_interval_seconds: float = 3.0, cooldown_seconds: int = 180):
        self.api_key = api_key.strip()
        self.base_url = base_url.rstrip("/")
        self.session = requests.Session()
        self.min_interval_seconds = float(min_interval_seconds)
        self.cooldown_seconds = int(cooldown_seconds)
        self._lock = Lock()
        self._next_allowed_at = 0.0
        self.session.headers.update(
            {
                "Authorization": f"Bearer {self.api_key}",
                "Accept": "application/json",
                "User-Agent": "AI-Trade-Bot/1.0",
            }
        )

    def get_json(self, path_or_url: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        url = path_or_url if path_or_url.startswith("http") else f"{self.base_url}{path_or_url}"
        backoff = 2.0
        for attempt in range(1, 7):
            try:
                with self._lock:
                    now = time.monotonic()
                    if now < self._next_allowed_at:
                        time.sleep(self._next_allowed_at - now)
                    self._next_allowed_at = time.monotonic() + self.min_interval_seconds

                resp = self.session.get(url, params=params, timeout=30)
                if resp.status_code == 429 or resp.status_code >= 500:
                    retry_after = resp.headers.get("Retry-After")
                    if resp.status_code == 429:
                        cooldown = self.cooldown_seconds
                        if retry_after:
                            try:
                                cooldown = max(cooldown, int(float(retry_after)))
                            except ValueError:
                                pass
                        with self._lock:
                            self._next_allowed_at = time.monotonic() + cooldown
                        print(f"[massive] rate limited (429). Cooling down for {cooldown}s before retrying.")
                        time.sleep(cooldown)
                    raise requests.HTTPError(f"HTTP {resp.status_code}: {resp.text[:300]}", response=resp)
                resp.raise_for_status()
                payload = resp.json()
                if not isinstance(payload, dict):
                    return {"results": _coerce_results(payload)}
                return payload
            except Exception as exc:
                if attempt >= 6:
                    raise
                print(f"[massive] request failed (attempt {attempt}/6): {exc}")
                time.sleep(backoff)
                backoff = min(backoff * 2, 60.0)
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


def _normalise_contracts(results: list[dict[str, Any]]) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    for item in results:
        rows.append(
            {
                "contract_ticker": item.get("ticker"),
                "underlying_ticker": item.get("underlying_ticker"),
                "contract_type": item.get("contract_type"),
                "exercise_style": item.get("exercise_style"),
                "expiration_date": item.get("expiration_date"),
                "strike_price": item.get("strike_price"),
                "shares_per_contract": item.get("shares_per_contract"),
                "primary_exchange": item.get("primary_exchange"),
                "cfi": item.get("cfi"),
                "additional_underlyings": item.get("additional_underlyings"),
            }
        )
    return pd.DataFrame(rows)


def fetch_option_contracts(
    client: MassiveClient,
    underlying: str,
    as_of: str,
    expired: bool = False,
    contract_type: str | None = None,
    expiration_date: str | None = None,
    limit: int = 1000,
) -> pd.DataFrame:
    params: dict[str, Any] = {
        "underlying_ticker": underlying.upper().strip(),
        "as_of": as_of,
        "expired": str(expired).lower(),
        "limit": min(max(int(limit), 1), 1000),
        "sort": "expiration_date",
        "order": "asc",
    }
    if contract_type and contract_type != "both":
        params["contract_type"] = contract_type
    if expiration_date:
        params["expiration_date"] = expiration_date

    results = _fetch_paginated(client, "/v3/reference/options/contracts", params=params)
    return _normalise_contracts(results)


def filter_contracts_by_dte(
    contracts: pd.DataFrame,
    as_of: str,
    dte_min: int,
    dte_max: int,
) -> pd.DataFrame:
    if contracts.empty:
        return contracts

    frame = contracts.copy()
    frame["dte"] = frame["expiration_date"].apply(lambda exp: _dte(as_of, exp))
    frame = frame[frame["dte"].notna()]
    frame = frame[(frame["dte"] >= dte_min) & (frame["dte"] <= dte_max)]
    frame["dte"] = frame["dte"].astype(int)
    return frame


def select_atm_window(
    contracts: pd.DataFrame,
    underlying_close: float | None,
    contracts_each_side: int,
) -> pd.DataFrame:
    if contracts.empty or underlying_close is None:
        return contracts

    selected_frames: list[pd.DataFrame] = []
    for expiration_date, exp_group in contracts.groupby("expiration_date", dropna=False):
        for contract_type, type_group in exp_group.groupby("contract_type", dropna=False):
            working = type_group.copy()
            working["strike_price"] = pd.to_numeric(working["strike_price"], errors="coerce")
            working = working[working["strike_price"].notna()]
            if working.empty:
                continue

            working = working.sort_values("strike_price", kind="stable").reset_index(drop=True)
            atm_idx = (working["strike_price"] - underlying_close).abs().idxmin()
            atm_pos = int(working.index.get_loc(atm_idx))
            start = max(0, atm_pos - contracts_each_side)
            stop = min(len(working), atm_pos + contracts_each_side + 1)
            window = working.iloc[start:stop].copy()
            window["atm_reference_price"] = underlying_close
            window["atm_index"] = atm_pos
            selected_frames.append(window)

    if not selected_frames:
        return contracts.iloc[0:0].copy()

    return pd.concat(selected_frames, ignore_index=True)


def fetch_contract_daily_summary(client: MassiveClient, contract_ticker: str, target_date: str) -> dict[str, Any]:
    payload = client.get_json(
        f"/v1/open-close/{contract_ticker}/{target_date}",
        params={"adjusted": "true"},
    )
    results = _coerce_results(payload)
    if results:
        # The endpoint returns a single object; preserve the first record if wrapped.
        payload = results[0]
    if not isinstance(payload, dict):
        return {}

    return {
        "summary_from": payload.get("from"),
        "summary_symbol": payload.get("symbol"),
        "open": payload.get("open"),
        "high": payload.get("high"),
        "low": payload.get("low"),
        "close": payload.get("close"),
        "preMarket": payload.get("preMarket"),
        "afterHours": payload.get("afterHours"),
        "volume": payload.get("volume"),
        "status": payload.get("status"),
        "timestamp_et": _ms_to_et_timestamp(payload.get("t")),
    }


def _write_frame(df: pd.DataFrame, output_path: Path) -> Path:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    if not df.empty:
        sort_cols = [c for c in ["expiration_date", "strike_price", "contract_ticker"] if c in df.columns]
        if sort_cols:
            df = df.sort_values(sort_cols, kind="stable")
    df.to_csv(output_path, index=False)
    return output_path


def _load_existing_contracts(output_path: Path) -> set[str]:
    if not output_path.exists():
        return set()
    try:
        existing = pd.read_csv(output_path, usecols=["contract_ticker"])
        if "contract_ticker" not in existing.columns:
            return set()
        return {str(v).strip() for v in existing["contract_ticker"].dropna().tolist() if str(v).strip()}
    except Exception:
        return set()


def _append_row(output_path: Path, row: dict[str, Any], write_header: bool) -> None:
    df = pd.DataFrame([row])
    df.to_csv(output_path, mode="a", header=write_header, index=False)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Download Massive.com options EOD data for an underlying stock.")
    parser.add_argument("--underlying", help="Single underlying stock ticker, e.g. AAPL or TSLA.")
    parser.add_argument("--underlyings", nargs="+", help="Multiple underlying tickers, e.g. AAPL MSFT NVDA SPY QQQ.")
    parser.add_argument("--preset", choices=sorted(PRESETS.keys()), default="mag7_etf", help="Convenience preset for common underlyings.")
    parser.add_argument("--date", help="Target trading date for the daily summary (YYYY-MM-DD). Defaults to the most recent weekday.")
    parser.add_argument("--as-of", dest="as_of", help="Contract universe as-of date (YYYY-MM-DD). Defaults to --date.")
    parser.add_argument("--contract-type", choices=["call", "put", "both"], default="both", help="Filter contracts by type.")
    parser.add_argument("--expiration-date", help="Optional contract expiration filter (YYYY-MM-DD).")
    parser.add_argument("--dte-min", type=int, default=0, help="Minimum days-to-expiration to include, inclusive.")
    parser.add_argument("--dte-max", type=int, default=10, help="Maximum days-to-expiration to include, inclusive.")
    parser.add_argument("--atm-window", type=int, default=10, help="Number of strikes to keep above and below ATM for each expiration/type.")
    parser.add_argument("--expired", action="store_true", help="Include expired contracts in the contract universe.")
    parser.add_argument("--limit", type=int, default=1000, help="Max contracts to request per page, capped at 1000.")
    parser.add_argument("--max-contracts", type=int, default=0, help="Optional hard cap on how many contracts to fetch summaries for.")
    parser.add_argument("--delay", type=float, default=0.0, help="Extra seconds to sleep between contract requests.")
    parser.add_argument("--output-dir", default=str(OUTPUT_DIR), help="Directory for CSV output.")
    parser.add_argument("--raw", action="store_true", help="Add a timestamp suffix if the output file already exists.")
    parser.add_argument("--schedule-market-close", action="store_true", help="Wait until 4:30pm ET on the next trading day before downloading.")
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

    underlyings: list[str] = []
    if args.underlying:
        underlyings.append(args.underlying)
    if args.underlyings:
        underlyings.extend(args.underlyings)
    if not underlyings:
        underlyings.extend(PRESETS[args.preset])
    # De-duplicate while preserving order.
    seen = set()
    underlyings = [u.upper().strip() for u in underlyings if u and not (u.upper().strip() in seen or seen.add(u.upper().strip()))]
    if not underlyings:
        print("Provide at least one underlying via --underlying, --underlyings, or a preset.")
        return 2

    date_default = _schedule_day_default() if args.schedule_market_close else _business_day_default()
    target_date = _ensure_date(args.date, date_default)
    as_of = _ensure_date(args.as_of, target_date)

    if args.schedule_market_close:
        next_run = _next_market_close_run()
        wait_seconds = max(0, int((next_run - datetime.now(ET)).total_seconds()))
        print(f"[massive] waiting until {next_run.isoformat()} ET to start download ({wait_seconds} seconds).")
        time.sleep(wait_seconds)

    exit_code = 0
    for underlying in underlyings:
        underlying_close = _fetch_underlying_close(client, underlying, target_date)
        contracts = fetch_option_contracts(
            client=client,
            underlying=underlying,
            as_of=as_of,
            expired=args.expired,
            contract_type=args.contract_type,
            expiration_date=args.expiration_date,
            limit=args.limit,
        )

        if contracts.empty:
            print(f"[massive] no option contracts found for {underlying} as of {as_of}")
            continue

        contracts = filter_contracts_by_dte(contracts, as_of=as_of, dte_min=args.dte_min, dte_max=args.dte_max)
        if contracts.empty:
            print(f"[massive] no {args.dte_min}-{args.dte_max} DTE contracts found for {underlying} as of {as_of}")
            continue

        contracts = select_atm_window(
            contracts=contracts,
            underlying_close=underlying_close,
            contracts_each_side=max(0, int(args.atm_window)),
        )
        if contracts.empty:
            print(f"[massive] no ATM-window contracts found for {underlying} with close={underlying_close}")
            continue

        if args.max_contracts and args.max_contracts > 0:
            contracts = contracts.head(args.max_contracts).copy()

        output_path = _output_path_for(underlying, target_date, args.dte_min, args.dte_max, args.atm_window, output_dir)
        completed_contracts = _load_existing_contracts(output_path)
        if completed_contracts:
            print(f"[massive] resuming {underlying}; {len(completed_contracts)} contracts already saved.")
        write_header = not output_path.exists() or output_path.stat().st_size == 0

        total = len(contracts)
        for idx, (_, contract) in enumerate(contracts.iterrows(), start=1):
            contract_ticker = str(contract.get("contract_ticker") or "").strip()
            if not contract_ticker:
                continue
            if contract_ticker in completed_contracts:
                print(f"[massive] {underlying} {idx}/{total} {contract_ticker} already saved, skipping.")
                continue
            try:
                summary = fetch_contract_daily_summary(client, contract_ticker, target_date)
                row = contract.to_dict()
                row.update(summary)
                row["requested_date"] = target_date
                row["as_of"] = as_of
                row["underlying_close"] = underlying_close
                _append_row(output_path, row, write_header=write_header)
                write_header = False
                completed_contracts.add(contract_ticker)
                print(f"[massive] {underlying} {idx}/{total} {contract_ticker} OK")
            except Exception as exc:
                exit_code = 1
                print(f"[massive] {underlying} {idx}/{total} {contract_ticker} failed: {exc}")
            if args.delay > 0:
                time.sleep(args.delay)

        if output_path.exists():
            try:
                current_rows = len(pd.read_csv(output_path))
                print(f"[massive] saved {current_rows} rows to {output_path}")
            except Exception:
                print(f"[massive] saved data to {output_path}")
        else:
            print(f"[massive] no summaries were downloaded for {underlying}.")
            exit_code = 1

    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
