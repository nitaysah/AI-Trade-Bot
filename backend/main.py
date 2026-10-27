"""
AI Trading Bot — FastAPI Backend.

Production-grade trading engine with:
- Multi-ticker watchlist scanning
- Background scheduler for automated evaluation
- Full REST API for the dashboard
- Alpaca broker integration
- Risk management enforcement
- MULTI-TENANT ARCHITECTURE
"""

import log_setup
log_setup.install()  # Central-time stamps, levels, secret redaction for every print()/logging line (no-op under tests)

import warnings
warnings.filterwarnings("ignore", category=DeprecationWarning, message=".*utcnow.*")
warnings.filterwarnings("ignore", category=DeprecationWarning, module=".*webull.*")

from fastapi import FastAPI, Query, Header, HTTPException, Depends, Request, Response
from fastapi.encoders import jsonable_encoder
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
from contextlib import asynccontextmanager, contextmanager, suppress, redirect_stdout, redirect_stderr
from pydantic import BaseModel
from typing import Optional, Any
import asyncio
import logging
from types import SimpleNamespace
from screener_tasks import ScreenerWorkBusy
import copy
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
import pytz
import firebase_admin
from firebase_admin import auth, firestore
import time
import os
import re
import json
import io
import yfinance as yf
from cryptography.fernet import Fernet
import base64
import hashlib
import hmac
import math
import numbers
import random
import secrets
from urllib.parse import urlparse

LEGAL_TERMS_VERSION = "BB-2026.07"
TERMS_REQUIRED_STATUS = 428

# Development authentication is intentionally loopback-only and uses a fresh,
# process-scoped token.  The browser obtains it from the loopback endpoint
# below; no predictable bearer value is shipped in frontend assets.
_DEV_AUTH_TOKEN = os.getenv("DEV_AUTH_TOKEN", "").strip()
if os.getenv("ENV", "").lower() == "development" and not _DEV_AUTH_TOKEN:
    _DEV_AUTH_TOKEN = secrets.token_urlsafe(32)

from trader import evaluate_trade, get_risk_manager, clear_evaluation_cache, get_confluence_decision
from indicators import calculate_indicators_for_df, get_full_analysis, get_latest_completed_candle_metadata
from data_manager import get_historical_data
import strategy_presets
import preset_runtime
import session_schedule
import bot_scan_interval
import strategy_scan_log
from engine import FIXED_LEVEL_EXIT_MODES, UserManager, UserEngine, _continuous_grid_forecast_state
from swing_strategy import build_swing_exit, detect_swing_entry, format_swing_preview, build_entry_signal, format_entry_preview
from options_direction import validate_options_type, normalize_options_type, VALID_OPTION_DIRECTIONS, options_type_label
from prior_day_high_low import (
    buffered_prior_day_levels,
    dynamic_high_low_exit_levels as _buffered_dynamic_high_low_exit_levels,
    prior_day_ohlc_from_history as _shared_prior_day_ohlc_from_history,
    resolve_high_low_buffer_pct,
)
from indicator_catalog import (
    EXTENSION_INDICATOR_DEFAULTS,
    EXTENSION_INDICATOR_TOGGLE_KEYS,
    STANDARD_INDICATOR_NAMES,
)
import config as global_config
from firebase_credentials import load_firebase_credential
from user_config import (
    set_user_config,
    get_user_config,
    set_draft_ticker_settings,
    reset_draft_ticker_settings,
    active_evaluation_context,
    _active_user_config,
)
from screener_service import (
    candle_key_regressed,
    ScreenerManager,
    ScreenerUserState,
    evaluate_screener_profile,
    normalize_screener_sms_phone,
    normalize_screener_symbol,
    profile_revision,
)
from screener_strategy import (
    assess_event_catalyst,
    assess_swing_technical,
    assess_strategy_preset_technical,
    alert_source_label,
    alert_source_policy,
    combine_strategy_qualification,
    derive_alert_source,
    direction_watch_directions,
    direction_watch_label,
    direction_watch_mode,
    normalize_alert_source,
    normalize_screening_strategy_mode,
    screening_strategy_label,
    screening_strategy_logic,
)
from ai_indicator import (
    build_screener_news_watch,
    get_ai_indicator,
    get_ai_sentiment,
    clear_ai_cache,
    AI_CANDLE_TIMEFRAME,
    AI_TIMEFRAME_SPECS,
    normalize_ai_model_name,
    normalize_ai_timeframe,
)
from earnings_service import (
    DATA_VERSION as EARNINGS_DATA_VERSION,
    build_earnings_watch,
    get_earnings_watch_detail,
    with_current_countdown as earnings_with_current_countdown,
)
from fundamental_service import (
    REPORT_VERSION as FUNDAMENTAL_REPORT_VERSION,
    build_fundamental_report,
    get_fundamental_report_detail,
)
from cache_manager import TTLCacheStore, stable_cache_key
from macro_context import backdrop as macro_backdrop, why_it_matters
from macro_actuals import enrich_macro_calendar_events

# Backtest cancellation is scoped to the authenticated user.  A process-global
# event would let one caller cancel another user's simulation (and, if exposed
# without auth, would provide a trivial denial-of-service endpoint).
_backtest_cancel_events = {}
_backtest_cancel_events_lock = threading.Lock()
_backtest_cancel_last_seen = {}
_BACKTEST_CANCEL_MAX_USERS = 1024
_BACKTEST_CANCEL_IDLE_SECONDS = 3600.0
_MONTE_CARLO_MAX_CONCURRENT = 2
_MONTE_CARLO_QUEUE_TIMEOUT_SECONDS = 0.25
_monte_carlo_semaphore = asyncio.Semaphore(_MONTE_CARLO_MAX_CONCURRENT)
_BACKTEST_MAX_CONCURRENT = 2
_BACKTEST_QUEUE_TIMEOUT_SECONDS = 0.25
_backtest_semaphore = asyncio.Semaphore(_BACKTEST_MAX_CONCURRENT)
_ANALYSIS_MAX_CONCURRENT = 8
_ANALYSIS_MAX_PER_USER = 2
_ANALYSIS_QUEUE_TIMEOUT_SECONDS = 0.25
# A user opening a chart waits briefly for a free slot instead of failing at once, and charts have their own per-user lane so
# background work (AI indicator, warm-ups) cannot take both of the user's slots and block the chart.
_ANALYSIS_CHART_QUEUE_TIMEOUT_SECONDS = 5.0
_ANALYSIS_CHART_LANE = "chart"
_analysis_semaphore = asyncio.Semaphore(_ANALYSIS_MAX_CONCURRENT)
_analysis_user_semaphores = {}
_analysis_user_last_seen = {}
_ANALYSIS_USER_MAX = 1024
_ANALYSIS_USER_IDLE_SECONDS = 3600.0
_analysis_rate_lock = threading.Lock()
_analysis_rate_windows = {}
_ANALYSIS_RATE_WINDOW_SECONDS = 60.0
_ANALYSIS_RATE_MAX_REQUESTS = 30
_ticker_warmup_tasks = {}
_symbol_search_rate_lock = threading.Lock()
_symbol_search_rate_windows = {}
_SYMBOL_SEARCH_MAX_USERS = 1024
_SYMBOL_SEARCH_WINDOW_SECONDS = 10.0
_SYMBOL_SEARCH_MAX_REQUESTS = 20
_SYMBOL_SEARCH_IDLE_SECONDS = 3600.0
_PROVIDER_EXECUTOR = ThreadPoolExecutor(max_workers=8, thread_name_prefix="provider")
_PROVIDER_CALL_TIMEOUT_SECONDS = 8.0


def _enforce_symbol_search_rate_limit(uid: str):
    """Bound upstream symbol-search work per authenticated tenant."""
    user_key = str(uid or "").strip()
    if not user_key:
        raise HTTPException(status_code=401, detail="Authenticated user required")
    now = time.monotonic()
    with _symbol_search_rate_lock:
        if user_key not in _symbol_search_rate_windows and len(_symbol_search_rate_windows) >= _SYMBOL_SEARCH_MAX_USERS:
            stale_keys = [
                key for key, (last_seen, _) in _symbol_search_rate_windows.items()
                if now - last_seen > _SYMBOL_SEARCH_IDLE_SECONDS
            ]
            for stale_key in stale_keys:
                _symbol_search_rate_windows.pop(stale_key, None)
                if len(_symbol_search_rate_windows) < _SYMBOL_SEARCH_MAX_USERS:
                    break
        if user_key not in _symbol_search_rate_windows and len(_symbol_search_rate_windows) >= _SYMBOL_SEARCH_MAX_USERS:
            raise HTTPException(status_code=429, detail="Symbol-search capacity is temporarily full; retry shortly.")
        _, prior_events = _symbol_search_rate_windows.get(user_key, (now, []))
        recent_events = [
            event_time for event_time in prior_events
            if now - event_time < _SYMBOL_SEARCH_WINDOW_SECONDS
        ]
        if len(recent_events) >= _SYMBOL_SEARCH_MAX_REQUESTS:
            raise HTTPException(status_code=429, detail="Too many symbol-search requests; retry shortly.")
        recent_events.append(now)
        _symbol_search_rate_windows[user_key] = (now, recent_events)


def _consume_provider_future(future):
    """Observe late provider failures after a request-level timeout."""
    try:
        future.result()
    except Exception:
        pass


def _safe_exception_type(exc: BaseException) -> str:
    """Return diagnostic class information without exposing provider payloads."""
    return type(exc).__name__


async def _run_bounded_provider_call(uid: str, operation: str, func, timeout: float = _PROVIDER_CALL_TIMEOUT_SECONDS):
    """Run blocking provider work in a bounded pool with per-user admission."""
    async with _bounded_analysis_work(uid, operation):
        loop = asyncio.get_running_loop()
        future = loop.run_in_executor(_PROVIDER_EXECUTOR, func)
        future.add_done_callback(_consume_provider_future)
        return await asyncio.wait_for(asyncio.shield(future), timeout=timeout)


@asynccontextmanager
async def _bounded_analysis_work(uid: str, operation: str, queue_timeout: float = _ANALYSIS_QUEUE_TIMEOUT_SECONDS, lane: str = ""):
    """Bound expensive analysis/provider work globally and per authenticated user.

    ``lane`` gives a kind of work its own per-user concurrency limit (the request-rate window and the global limit stay
    shared); ``queue_timeout`` is how long to wait for a free slot before answering 429.
    """
    user_key = str(uid or "").strip()
    if not user_key:
        raise HTTPException(status_code=401, detail="Authenticated user required")
    semaphore_key = f"{user_key}:{lane}" if lane else user_key
    now = time.monotonic()
    with _analysis_rate_lock:
        if user_key not in _analysis_rate_windows and len(_analysis_rate_windows) >= _ANALYSIS_USER_MAX:
            stale_keys = [
                key for key, (last_seen, _) in _analysis_rate_windows.items()
                if now - last_seen > _ANALYSIS_USER_IDLE_SECONDS
            ]
            for stale_key in stale_keys:
                _analysis_rate_windows.pop(stale_key, None)
                if len(_analysis_rate_windows) < _ANALYSIS_USER_MAX:
                    break
        if user_key not in _analysis_rate_windows and len(_analysis_rate_windows) >= _ANALYSIS_USER_MAX:
            raise HTTPException(status_code=429, detail="Analysis request capacity is temporarily full; retry shortly.")
        _, prior_requests = _analysis_rate_windows.get(user_key, (now, []))
        recent_requests = [
            request_time for request_time in prior_requests
            if now - request_time < _ANALYSIS_RATE_WINDOW_SECONDS
        ]
        if len(recent_requests) >= _ANALYSIS_RATE_MAX_REQUESTS:
            raise HTTPException(status_code=429, detail=f"Too many {operation} requests; retry shortly.")
        recent_requests.append(now)
        _analysis_rate_windows[user_key] = (now, recent_requests)
    if semaphore_key not in _analysis_user_semaphores and len(_analysis_user_semaphores) >= _ANALYSIS_USER_MAX:
        stale = sorted(
            (seen, key) for key, seen in _analysis_user_last_seen.items()
            if now - seen > _ANALYSIS_USER_IDLE_SECONDS
        )
        for _, stale_key in stale:
            _analysis_user_semaphores.pop(stale_key, None)
            _analysis_user_last_seen.pop(stale_key, None)
            if len(_analysis_user_semaphores) < _ANALYSIS_USER_MAX:
                break
    if semaphore_key not in _analysis_user_semaphores and len(_analysis_user_semaphores) >= _ANALYSIS_USER_MAX:
        raise HTTPException(status_code=429, detail="Analysis user capacity is temporarily full; retry shortly.")
    user_semaphore = _analysis_user_semaphores.setdefault(
        semaphore_key,
        asyncio.Semaphore(_ANALYSIS_MAX_PER_USER),
    )
    _analysis_user_last_seen[semaphore_key] = now
    try:
        await asyncio.wait_for(
            _analysis_semaphore.acquire(),
            timeout=queue_timeout,
        )
    except asyncio.TimeoutError:
        raise HTTPException(status_code=429, detail=f"Too much {operation} work is already running; retry shortly.")
    try:
        try:
            await asyncio.wait_for(
                user_semaphore.acquire(),
                timeout=queue_timeout,
            )
        except asyncio.TimeoutError:
            raise HTTPException(status_code=429, detail=f"Too much {operation} work is already running for this account; retry shortly.")
        try:
            yield
        finally:
            user_semaphore.release()
    finally:
        _analysis_semaphore.release()


def _get_backtest_cancel_event(uid: str) -> threading.Event:
    key = str(uid or "").strip()
    if not key:
        raise ValueError("Backtest cancellation requires an authenticated user id")
    with _backtest_cancel_events_lock:
        now = time.monotonic()
        if key not in _backtest_cancel_events and len(_backtest_cancel_events) >= _BACKTEST_CANCEL_MAX_USERS:
            stale = sorted(
                (seen, stale_key) for stale_key, seen in _backtest_cancel_last_seen.items()
                if now - seen > _BACKTEST_CANCEL_IDLE_SECONDS
            )
            for _, stale_key in stale:
                _backtest_cancel_events.pop(stale_key, None)
                _backtest_cancel_last_seen.pop(stale_key, None)
                if len(_backtest_cancel_events) < _BACKTEST_CANCEL_MAX_USERS:
                    break
        if key not in _backtest_cancel_events and len(_backtest_cancel_events) >= _BACKTEST_CANCEL_MAX_USERS:
            raise HTTPException(status_code=429, detail="Backtest cancellation capacity is temporarily full; retry shortly.")
        event = _backtest_cancel_events.get(key)
        if event is None:
            event = threading.Event()
            _backtest_cancel_events[key] = event
        _backtest_cancel_last_seen[key] = now
        return event

# ──────────────────────────────────────────────
# Secret Encryption Vault
# ──────────────────────────────────────────────
class Vault:
    """Versioned envelope encryption with an explicit previous-key rotation window."""
    def __init__(self):
        active_key = os.getenv("VAULT_SECRET_KEY", "").strip()
        previous_key = os.getenv("VAULT_SECRET_KEY_PREVIOUS", "").strip()
        self.active_version = os.getenv("VAULT_SECRET_KEY_VERSION", "2").strip()
        self.previous_version = os.getenv("VAULT_SECRET_KEY_PREVIOUS_VERSION", "1").strip()
        if not active_key:
            raise RuntimeError("VAULT_SECRET_KEY is required in every environment")
        try:
            self.fernet = Fernet(active_key.encode())
            self.previous_fernet = Fernet(previous_key.encode()) if previous_key else None
        except Exception as exc:
            raise RuntimeError("VAULT_SECRET_KEY is invalid") from exc
        if not re.fullmatch(r"[A-Za-z0-9._-]{1,32}", self.active_version):
            raise RuntimeError("VAULT_SECRET_KEY_VERSION is invalid")
        if self.previous_fernet and not re.fullmatch(r"[A-Za-z0-9._-]{1,32}", self.previous_version):
            raise RuntimeError("VAULT_SECRET_KEY_PREVIOUS_VERSION is invalid")
        print(f"[vault] Versioned encryption key loaded (active={self.active_version})")

    def encrypt(self, plain_text: str) -> str:
        if not plain_text: return ""
        token = self.fernet.encrypt(plain_text.encode()).decode()
        return f"v{self.active_version}:{token}"

    def decrypt(self, cipher_text: str) -> str:
        if not cipher_text: return ""
        raw = str(cipher_text)
        version = None
        token = raw
        match = re.fullmatch(r"v([A-Za-z0-9._-]{1,32}):(.*)", raw)
        if match:
            version, token = match.groups()
        candidates = []
        if version in (None, self.active_version):
            candidates.append(self.fernet)
        if self.previous_fernet and version in (None, self.previous_version):
            candidates.append(self.previous_fernet)
        for key in candidates:
            try:
                return key.decrypt(token.encode()).decode().strip()
            except Exception:
                continue
        print("[vault] Decryption failed for stored credential material")
        return ""

    def needs_reencrypt(self, cipher_text: str) -> bool:
        raw = str(cipher_text or "")
        match = re.fullmatch(r"v([A-Za-z0-9._-]{1,32}):.*", raw)
        return not match or match.group(1) != self.active_version

    def reencrypt(self, cipher_text: str) -> str:
        plain_text = self.decrypt(cipher_text)
        return self.encrypt(plain_text) if plain_text else ""

    def self_test(self):
        test_str = "alpaca_test_123"
        encrypted = self.encrypt(test_str)
        decrypted = self.decrypt(encrypted)
        if test_str == decrypted:
            print("[vault] Self-test PASSED.")
        else:
            print("[vault] Self-test FAILED! Encryption/Decryption mismatch.")

vault = Vault()
vault.self_test()

# Initialize Firebase Admin
if not firebase_admin._apps:
    try:
        cred = load_firebase_credential()
        if cred:
            firebase_admin.initialize_app(cred)
        elif os.getenv("ENV", "production").strip().lower() == "test":
            # Tests must never depend on a copied service-account key. The
            # Firestore emulator/mocks can use anonymous credentials while
            # production continues to require managed credential injection.
            from google.auth.credentials import AnonymousCredentials
            firebase_admin.initialize_app(
                AnonymousCredentials(),
                options={"projectId": "bot-bulls"},
            )
        else:
            firebase_admin.initialize_app(options={'projectId': 'bot-bulls'})
        print("[main] Firebase Admin initialized.")
    except Exception as e:
        print(f"[main] Firebase Admin init error: {_safe_exception_type(e)}")

db = firestore.client()
user_manager = UserManager(db)
screener_manager = ScreenerManager(db)
_webull_market_data_broker = None
_chart_analysis_cache = TTLCacheStore(maxsize=256)
_quote_cache = TTLCacheStore(maxsize=512)
_webull_unavailable_quote_cache = TTLCacheStore(maxsize=512)
_dashboard_broker_cache = TTLCacheStore(maxsize=64)
_bots_broker_cache = TTLCacheStore(maxsize=64)
_ai_indicator_endpoint_cache = TTLCacheStore(maxsize=128)
_quote_refresh_tasks = {}

DASHBOARD_CONFLUENCE_TOGGLE_KEYS = [
    "ENABLE_RSI",
    "ENABLE_MACD",
    "ENABLE_EMA",
    "ENABLE_SUPERTREND",
    "ENABLE_BOLLINGER",
    "ENABLE_VWAP",
    "ENABLE_MYSTIC_PULSE",
    "ENABLE_AI_SENTIMENT",
    "ENABLE_CANDLE_PATTERNS",
    "ENABLE_ADX_TREND",
    "ENABLE_SMA",
    "ENABLE_DAILY_HIGH_LOW",
    "ENABLE_PIVOT_POINTS",
    "ENABLE_VOLUME_PROFILE",
    "ENABLE_SWING_POINTS",
]
DASHBOARD_CONFLUENCE_TOGGLE_KEYS.extend(EXTENSION_INDICATOR_TOGGLE_KEYS.values())

DASHBOARD_SIGNAL_TO_TOGGLE = {
    "RSI": "ENABLE_RSI",
    "MACD": "ENABLE_MACD",
    "EMA Cross": "ENABLE_EMA",
    "Supertrend": "ENABLE_SUPERTREND",
    "Bollinger": "ENABLE_BOLLINGER",
    "VWAP": "ENABLE_VWAP",
    "Mystic Pulse": "ENABLE_MYSTIC_PULSE",
    "Candle Patterns": "ENABLE_CANDLE_PATTERNS",
    "ADX Trend": "ENABLE_ADX_TREND",
    "SMA": "ENABLE_SMA",
    "Prior Day High/Low": "ENABLE_DAILY_HIGH_LOW",
    "Pivot Points (S/R)": "ENABLE_PIVOT_POINTS",
    "Volume Profile": "ENABLE_VOLUME_PROFILE",
    "Swing Points": "ENABLE_SWING_POINTS",
}
DASHBOARD_SIGNAL_TO_TOGGLE.update(EXTENSION_INDICATOR_TOGGLE_KEYS)
DASHBOARD_INDICATOR_DISPLAY_LIMIT = 15
MAX_WATCHLIST_ITEMS = 100
MAX_ACTIVE_BOTS = 50
MAX_TRADE_ALLOCATION = 1_000_000.0
MAX_SCREENER_PROFILES = 100
MAX_BACKTEST_DAYS = 1825
MAX_OPTIMIZATION_DAYS = 365
MAX_OPTIONS_BACKTEST_DAYS = 365
MAX_REPLAY_CANDLES = 10000
DASHBOARD_DEFAULT_DISPLAYED_INDICATORS = [
    "RSI", "EMA Cross", "SMA", "VWAP", "ADX Trend", "Supertrend",
    "ATR Volatility", "Volume Profile", "Candle Patterns", "Prior Day High/Low",
    "Swing Points", "Pivot Points (S/R)", "MACD", "Bollinger", "Chart Patterns",
]
DASHBOARD_PREVIOUS_TWELVE_DEFAULT = {
    "RSI", "EMA Cross", "SMA", "VWAP", "ADX Trend", "Supertrend", "ATR Volatility", "Volume Profile",
    "Candle Patterns", "Prior Day High/Low", "Swing Points", "Pivot Points (S/R)",
}
DASHBOARD_LEGACY_DEFAULT_SETS = (
    {"RSI", "MACD", "EMA Cross", "ADX Trend", "Supertrend", "Bollinger", "VWAP", "Volume Profile", "SMA", "Prior Day High/Low"},
    {"RSI", "MACD", "EMA Cross", "SMA", "Bollinger", "VWAP", "ADX Trend", "Supertrend", "Stochastic", "ATR Volatility"},
)
from removed_indicators import strip_removed  # noqa: E402

DASHBOARD_STANDARD_INDICATOR_SET = set(STANDARD_INDICATOR_NAMES)
DASHBOARD_STANDARD_TOGGLE_TO_SIGNAL = {
    toggle_key: name
    for name, toggle_key in DASHBOARD_SIGNAL_TO_TOGGLE.items()
    if name in DASHBOARD_STANDARD_INDICATOR_SET
}


def _unique_standard_indicator_names(values) -> list[str]:
    result = []
    for value in values if isinstance(values, list) else []:
        name = str(value or "").strip()
        if name in DASHBOARD_STANDARD_INDICATOR_SET and name not in result:
            result.append(name)
    return result


def _complete_dashboard_displayed_indicators(values) -> list[str]:
    displayed = _unique_standard_indicator_names(values)
    if (len(displayed) == 10 and any(set(displayed) == legacy for legacy in DASHBOARD_LEGACY_DEFAULT_SETS)) \
            or set(displayed) == DASHBOARD_PREVIOUS_TWELVE_DEFAULT:
        return list(DASHBOARD_DEFAULT_DISPLAYED_INDICATORS)
    return (displayed or list(DASHBOARD_DEFAULT_DISPLAYED_INDICATORS))[:DASHBOARD_INDICATOR_DISPLAY_LIMIT]


def _dashboard_indicator_workspace_payload(eng: UserEngine) -> dict:
    raw = getattr(eng.config, "dashboard_indicator_workspace", {})
    configured = isinstance(raw, dict) and "displayed_indicators" in raw
    if configured:
        displayed = _complete_dashboard_displayed_indicators(raw.get("displayed_indicators"))
        stored_enabled = set(_unique_standard_indicator_names(raw.get("enabled_indicators")))
        enabled = [name for name in displayed if name in stored_enabled]
        favorites = _unique_standard_indicator_names(raw.get("favorites"))
    else:
        displayed = list(DASHBOARD_DEFAULT_DISPLAYED_INDICATORS)
        enabled_from_toggles = set(_enabled_indicator_names_from_toggles(eng.config.active_toggles))
        enabled = [name for name in displayed if name in enabled_from_toggles]
        favorites = []
    payload = {
        "version": 1,
        "configured": configured,
        "displayed_indicators": displayed,
        "enabled_indicators": enabled,
        "favorites": favorites,
    }
    if not configured:
        payload["legacy_enabled_indicators"] = [
            name for name in STANDARD_INDICATOR_NAMES
            if name in enabled_from_toggles
        ]
    return payload


def _sync_dashboard_workspace_from_toggle_updates(eng: UserEngine, toggle_keys) -> None:
    """Keep legacy Dashboard toggle writes inside the authoritative workspace."""
    state = _dashboard_indicator_workspace_payload(eng)
    if not state.get("configured"):
        return
    displayed = state["displayed_indicators"]
    displayed_set = set(displayed)
    enabled_set = set(state["enabled_indicators"])
    active_toggles = eng.config.active_toggles
    for toggle_key in set(toggle_keys or []):
        indicator_name = DASHBOARD_STANDARD_TOGGLE_TO_SIGNAL.get(toggle_key)
        if not indicator_name:
            continue
        should_enable = bool(active_toggles.get(toggle_key, False)) and indicator_name in displayed_set
        if should_enable:
            enabled_set.add(indicator_name)
        else:
            enabled_set.discard(indicator_name)
            if bool(active_toggles.get(toggle_key, False)):
                eng.config.set(toggle_key, False)
    eng.config.dashboard_indicator_workspace = {
        "version": 1,
        "displayed_indicators": displayed,
        "enabled_indicators": [name for name in displayed if name in enabled_set],
        "favorites": state["favorites"],
    }

INDICATOR_PARAMETER_KEYS = [
    "BOTBULLS5_BREAKOUT_BUFFER_ATR",
    "BOTBULLS5_MIN_CONFIDENCE",
    "BOTBULLS5_SIGNAL_HOLD_BARS",
    "BOTBULLS5_REQUIRE_VOLUME",
    "BOTBULLS5_STRUCTURE_LOOKBACK",
    "RSI_PERIOD",
    "RSI_OVERBOUGHT",
    "RSI_OVERSOLD",
    "EMA_FAST",
    "EMA_SLOW",
    "EMA_USE_SECOND_LINE",
    "MACD_FAST",
    "MACD_SLOW",
    "MACD_SIGNAL",
    "BOLL_PERIOD",
    "BOLL_STD_DEV",
    "SUPERTREND_PERIOD",
    "SUPERTREND_MULTIPLIER",
    "MYSTIC_PULSE_THRESHOLD",
    "ADX_PERIOD",
    "ADX_TRENDING_THRESHOLD",
    "SMA_PERIOD",
    "SMA_SECOND_PERIOD",
    "SMA_USE_SECOND_LINE",
    "DAILY_HIGH_LOW_BUFFER_PCT",
    "PIVOT_LEVEL_COUNT",
    "PIVOT_TOLERANCE_PCT",
    "VOLUME_PROFILE_ROWS",
    "VOLUME_PROFILE_VALUE_AREA_PCT",
    "VOLUME_PROFILE_TOLERANCE_PCT",
    "VOLUME_PROFILE_CONFIRMATION_CANDLES",
    "SWING_POINTS_LOOKBACK_DAYS",
    "SWING_POINTS_ENTRY_MODE",
    "SWING_POINTS_LEVEL_COUNT",
    "ATR_PERIOD",
    "ATR_STOP_MULTIPLIER",
    "ATR_TRAIL_MULTIPLIER",
    "ATR_TAKE_PROFIT_MULTIPLIER",
    "MIN_BULLISH_SIGNALS",
    "MIN_BEARISH_SIGNALS",
    "SENTIMENT_BULLISH_PERCENTAGE",
    "SENTIMENT_BEARISH_PERCENTAGE",
]
INDICATOR_PARAMETER_KEYS.extend(
    key for key in EXTENSION_INDICATOR_DEFAULTS if key not in INDICATOR_PARAMETER_KEYS
)


def _cache_settings_hash(
    eng: UserEngine,
    ticker: Optional[str] = None,
    context: str = "dashboard",
    request_settings: dict = None,
) -> str:
    ticker_key = (ticker or "").upper()
    ctx_data = eng.config.contexts.get(context, {}) if getattr(eng.config, "contexts", None) else {}
    payload = {
        "ctx": context,
        "active_toggles": eng.config.active_toggles,
        "active_parameters": eng.config.active_parameters,
        "ticker_settings": eng.config.TICKER_SETTINGS.get(ticker_key, {}) if ticker_key else {},
        "ctx_toggles": ctx_data.get("toggles", {}),
        "ctx_params": ctx_data.get("parameters", {}),
        "request_settings": request_settings if isinstance(request_settings, dict) else {},
    }
    return hashlib.sha256(json.dumps(payload, sort_keys=True, default=str).encode()).hexdigest()[:16]


def _chart_cache_ttl(timeframe: str) -> int:
    tf = (timeframe or "").upper()
    if tf in ("30SEC", "1MIN"):
        return 10
    if tf in ("2MIN", "3MIN", "5MIN"):
        return 20
    if tf in ("10MIN", "15MIN", "30MIN"):
        return 45
    if tf in ("1HOUR", "60MIN"):
        return 120
    if tf in ("2HOUR", "4HOUR"):
        return 300
    return 900


def _normalize_analysis_context(source: str = "dashboard") -> str:
    return source if source in {"dashboard", "bots", "screener", "backtest"} else "dashboard"


def _enabled_indicator_names_from_toggles(toggles: dict) -> list[str]:
    """Return confluence indicator names that should be computed for the active context."""
    return [
        name for name, toggle_key in DASHBOARD_SIGNAL_TO_TOGGLE.items()
        if bool((toggles or {}).get(toggle_key, True))
    ]


def _chart_analysis_settings(
    eng: UserEngine,
    ticker: str,
    prefer_bot: bool = False,
    market_session: str = None,
) -> dict:
    """Build indicator settings for chart analysis without losing the enabled filter."""
    ticker_key = _normalize_quote_symbol(ticker)
    if prefer_bot:
        settings = dict(eng.config.TICKER_SETTINGS.get(ticker_key, {}) or {})
        indicators = settings.get("indicators")
        if isinstance(indicators, list):
            settings["enabled_indicators"] = [
                str(indicator) for indicator in indicators
                if str(indicator or "").strip()
            ]
        settings["market_session"] = _normalize_market_session_for_symbol(
            ticker_key,
            settings.get("market_session", market_session),
        )
        settings["_botbulls5_recent_session_only"] = True
        return settings

    settings = dict(eng.config.active_parameters or {})
    settings["enabled_indicators"] = _enabled_indicator_names_from_toggles(eng.config.active_toggles)
    settings["market_session"] = _normalize_market_session_for_symbol(
        ticker_key,
        market_session,
    )
    settings["_botbulls5_recent_session_only"] = True
    return settings


@contextmanager
def _analysis_request_context(source: str = "dashboard", ticker: str = None, use_bot_settings: bool = False):
    from user_config import active_evaluation_context, active_ticker_context

    eval_token = active_evaluation_context.set(_normalize_analysis_context(source))
    ticker_token = active_ticker_context.set(_normalize_quote_symbol(ticker)) if use_bot_settings and ticker else None
    try:
        yield
    finally:
        if ticker_token is not None:
            active_ticker_context.reset(ticker_token)
        active_evaluation_context.reset(eval_token)


def _format_scan_price(price) -> str:
    try:
        p = float(price)
    except (TypeError, ValueError):
        return "N/A"
    if not math.isfinite(p):
        return "N/A"
    return f"${p:.4f}" if p < 10 else f"${p:.2f}"


def _clear_chart_analysis_cache(ticker: str = None, timeframe: str = None):
    ticker_key = _normalize_quote_symbol(ticker) if ticker else None
    timeframe_key = timeframe.upper() if timeframe else None
    _chart_analysis_cache.delete_matching(
        lambda k: (
            (not ticker_key or k[2] == ticker_key)
            and (not timeframe_key or k[3] == timeframe_key)
        )
    )


_GRID_DEFAULT_ENTRY_LEVEL_PCTS = (1.0, 2.0, 3.0, 4.0, 5.0)
_GRID_DEFAULT_EXIT_LEVEL_PCTS = (1.0, 2.0, 3.0, 4.0, 5.0)
_GRID_DEFAULT_ATR_LEVEL_MULTS = (2.0, 4.0, 6.0, 8.0, 10.0)
_GRID_CONFIGURATION_KEYS = (
    "continuous_grid_ladder_version",
    "continuous_grid_adaptive_mode",
    "continuous_grid_adaptation_strength",
    "continuous_grid_min_edge_pct",
    "continuous_grid_trailing_mode",
    "pct_grid_regime_mode",
    "pct_grid_runner_pct",
    "pct_grid_runner_trail_pct",
    "pct_grid_bullish_trend_threshold",
    "grid_continuous_mode",
    "grid_buy_allocation",
    "grid_max_levels",
    "grid_reclaim_buffer_pct",
    "grid_entry_l1_pct",
    "grid_entry_l2_pct",
    "grid_entry_l3_pct",
    "grid_entry_l4_pct",
    "grid_entry_l5_pct",
    "grid_entry_l1_amount",
    "grid_entry_l2_amount",
    "grid_entry_l3_amount",
    "grid_entry_l4_amount",
    "grid_entry_l5_amount",
    "grid_entry_l1_buy_pct",
    "grid_entry_l2_buy_pct",
    "grid_entry_l3_buy_pct",
    "grid_entry_l4_buy_pct",
    "grid_entry_l5_buy_pct",
    "grid_entry_l1_enabled",
    "grid_entry_l2_enabled",
    "grid_entry_l3_enabled",
    "grid_entry_l4_enabled",
    "grid_entry_l5_enabled",
    "exit_grid_continuous_mode",
    "exit_grid_sell_pct",
    "exit_grid_max_levels",
    "grid_exit_l1_pct",
    "grid_exit_l2_pct",
    "grid_exit_l3_pct",
    "grid_exit_l4_pct",
    "grid_exit_l5_pct",
    "grid_exit_l1_sell_pct",
    "grid_exit_l2_sell_pct",
    "grid_exit_l3_sell_pct",
    "grid_exit_l4_sell_pct",
    "grid_exit_l5_sell_pct",
    "grid_exit_l1_enabled",
    "grid_exit_l2_enabled",
    "grid_exit_l3_enabled",
    "grid_exit_l4_enabled",
    "grid_exit_l5_enabled",
    "atr_grid_continuous_mode",
    "exit_atr_grid_continuous_mode",
    "grid_atr_entry_l1_mult", "grid_atr_entry_l2_mult", "grid_atr_entry_l3_mult", "grid_atr_entry_l4_mult", "grid_atr_entry_l5_mult",
    "grid_atr_entry_l1_buy_pct", "grid_atr_entry_l2_buy_pct", "grid_atr_entry_l3_buy_pct", "grid_atr_entry_l4_buy_pct", "grid_atr_entry_l5_buy_pct",
    "grid_atr_entry_l1_enabled", "grid_atr_entry_l2_enabled", "grid_atr_entry_l3_enabled", "grid_atr_entry_l4_enabled", "grid_atr_entry_l5_enabled",
    "grid_atr_exit_l1_mult", "grid_atr_exit_l2_mult", "grid_atr_exit_l3_mult", "grid_atr_exit_l4_mult", "grid_atr_exit_l5_mult",
    "grid_atr_exit_l1_sell_pct", "grid_atr_exit_l2_sell_pct", "grid_atr_exit_l3_sell_pct", "grid_atr_exit_l4_sell_pct", "grid_atr_exit_l5_sell_pct",
    "grid_atr_exit_l1_enabled", "grid_atr_exit_l2_enabled", "grid_atr_exit_l3_enabled", "grid_atr_exit_l4_enabled", "grid_atr_exit_l5_enabled",
)
_GRID_RUNTIME_STATE_KEYS = (
    "grid_cycle_active",
    "grid_flat_reentry_armed",
    "entry_grid_level",
    "entry_grid_anchor_price",
    "entry_grid_anchor_atr",
    "entry_grid_armed_level",
    "entry_grid_last_fill_price",
    "exit_grid_level",
    "exit_grid_anchor_price",
    "exit_grid_anchor_atr",
    "exit_grid_anchor_qty",
    "exit_grid_last_fill_price",
    "entry_grid_trend_score",
    "entry_grid_volatility_ratio",
    "entry_grid_high_water_price",
    "exit_grid_trend_score",
    "exit_grid_volatility_ratio",
    "exit_grid_high_water_price",
    "exit_grid_armed_level",
    "exit_grid_runner_active",
    "exit_grid_runner_high_water_price",
    "entry_grid_pending_order_id",
    "entry_grid_pending_level",
    "entry_grid_pending_before_qty",
    "entry_grid_pending_fill_price",
    "entry_grid_pending_atr",
    "entry_grid_pending_missing_scans",
    "exit_grid_pending_order_id",
    "exit_grid_pending_level",
    "exit_grid_pending_before_qty",
    "exit_grid_pending_fill_price",
    "exit_grid_pending_atr",
    "exit_grid_pending_entry_price",
    "exit_grid_pending_final_level",
    "exit_grid_pending_runner_exit",
    "exit_grid_pending_missing_scans",
    "grid_level",
    "grid_last_execution_price",
)


def _grid_bool(value) -> bool:
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "on"}
    return bool(value)


def _normalize_risk_management_setting(value):
    """Normalize only explicit boolean forms; reject false-like numerics."""
    if isinstance(value, bool):
        return value
    if isinstance(value, numbers.Number) and not isinstance(value, bool):
        if value in (0, 1):
            return bool(value)
        raise HTTPException(status_code=400, detail="enable_risk_management must be true or false")
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"1", "true", "yes", "on"}:
            return True
        if normalized in {"0", "false", "no", "off"}:
            return False
    raise HTTPException(status_code=400, detail="enable_risk_management must be true or false")


def _restart_compounding_when_amount_changed(current: dict, prior: dict, incoming: dict) -> None:
    """Compound mode: editing the starting Amount restarts compounding at the new Amount.

    ``compound_amount`` is only seeded when missing, so without this an edited Amount was silently
    ignored while the old compounded value kept being traded. An unchanged Amount (every save
    re-sends it) leaves the running compounded value alone."""
    for mode_key, amount_key, compound_key in (
        ("allocation_mode", "amount", "compound_amount"),
        ("options_allocation_mode", "options_trade_amount", "options_compound_amount"),
    ):
        if incoming.get(amount_key) in (None, "", "default") or compound_key in incoming:
            continue
        mode = str(current.get(mode_key, current.get("allocation_mode", "fixed")) or "fixed").lower()
        if mode != "compound":
            continue
        try:
            new_amount = float(incoming[amount_key])
            old_amount = float((prior or {}).get(amount_key) or 0.0)
        except (TypeError, ValueError):
            continue
        if math.isfinite(new_amount) and new_amount > 0 and abs(new_amount - old_amount) > 0.004:
            current[compound_key] = new_amount


def _validate_trade_allocation(value, field_name: str = "trade allocation") -> float:
    try:
        normalized = float(value)
    except (TypeError, ValueError):
        raise HTTPException(status_code=400, detail=f"{field_name} must be numeric")
    if not math.isfinite(normalized) or not 0 < normalized <= MAX_TRADE_ALLOCATION:
        raise HTTPException(
            status_code=400,
            detail=f"{field_name} must be finite and between 0 and {int(MAX_TRADE_ALLOCATION)}",
        )
    return normalized


def _normalize_percentage_strategy_settings(settings: dict) -> dict:
    """Keep percentage strategy inputs finite and economically valid."""
    if not isinstance(settings, dict):
        return settings

    def normalize_if_present(key, fallback, minimum, maximum):
        if key not in settings:
            return
        try:
            value = float(settings.get(key, fallback))
        except (TypeError, ValueError):
            value = float(fallback)
        if not math.isfinite(value):
            value = float(fallback)
        settings[key] = min(maximum, max(minimum, value))

    # entry_pct_move is stored in display-percent units. Exit settings are
    # stored as decimal fractions (5% == 0.05).
    normalize_if_present("entry_pct_move", 5.0, 0.1, 100.0)
    normalize_if_present("regular_stop_loss_pct", 0.05, 0.001, 0.99)
    normalize_if_present("regular_take_profit_pct", 0.10, 0.001, 10.0)
    normalize_if_present("regular_trailing_pct", 0.03, 0.0, 0.99)
    normalize_if_present("options_stop_loss_pct", 0.15, 0.001, 0.99)
    normalize_if_present("options_take_profit_pct", 0.30, 0.001, 10.0)
    normalize_if_present("options_trailing_pct", 0.10, 0.0, 0.99)
    return settings


def _normalize_continuous_grid_settings(settings: dict) -> dict:
    """Sanitize one ticker's independent scale-in/scale-out configuration."""
    if not isinstance(settings, dict):
        return settings
    _normalize_percentage_strategy_settings(settings)

    # Grid trading currently operates on stock/crypto positions. Silently
    # disable stale options flags as a backend safety boundary.
    options_trading = _grid_bool(settings.get("options_trading", False)) or _grid_bool(
        settings.get("is_option", False)
    )
    settings["grid_continuous_mode"] = (
        _grid_bool(settings.get("grid_continuous_mode", False)) and not options_trading
    )
    settings["exit_grid_continuous_mode"] = (
        _grid_bool(settings.get("exit_grid_continuous_mode", False)) and not options_trading
    )
    settings["atr_grid_continuous_mode"] = (
        _grid_bool(settings.get("atr_grid_continuous_mode", False)) and not options_trading
    )
    settings["exit_atr_grid_continuous_mode"] = (
        _grid_bool(settings.get("exit_atr_grid_continuous_mode", False)) and not options_trading
    )
    # A PCT ladder without an explicit smart-mode value is an old saved bot.
    # Upgrade that case to the quant profile; an explicit false remains a
    # deliberate request for the legacy balanced/static behavior.
    pct_pair_requested = bool(
        _grid_bool(settings.get("grid_continuous_mode", False))
        and _grid_bool(settings.get("exit_grid_continuous_mode", False))
    )
    adaptive_default = pct_pair_requested and "continuous_grid_adaptive_mode" not in settings
    settings["continuous_grid_adaptive_mode"] = _grid_bool(
        settings.get("continuous_grid_adaptive_mode", adaptive_default)
    ) and not options_trading
    settings["continuous_grid_trailing_mode"] = _grid_bool(
        settings.get("continuous_grid_trailing_mode", settings["continuous_grid_adaptive_mode"])
    ) and settings["continuous_grid_adaptive_mode"] and not options_trading
    buy_mode = str(settings.get("buy_mode", "indicator") or "indicator").lower()
    sell_mode = str(settings.get("sell_mode", "indicator") or "indicator").lower()
    if settings["grid_continuous_mode"] or settings["exit_grid_continuous_mode"]:
        valid_adaptive_pair = bool(
            settings["grid_continuous_mode"]
            and settings["exit_grid_continuous_mode"]
            and buy_mode in {"percentage", "hybrid_pct"}
            and sell_mode in {"percentage", "hybrid_pct"}
        )
        if not valid_adaptive_pair:
            # The v2 ladder is one atomic strategy. Disabling an invalid or
            # half-configured pair prevents a hidden full-size initial entry
            # followed by additional scale-in exposure.
            settings["grid_continuous_mode"] = False
            settings["exit_grid_continuous_mode"] = False

    if settings["atr_grid_continuous_mode"] or settings["exit_atr_grid_continuous_mode"]:
        valid_atr_pair = bool(
            settings["atr_grid_continuous_mode"]
            and settings["exit_atr_grid_continuous_mode"]
            and buy_mode in {"atr", "hybrid_atr"}
            and sell_mode in {"sltp", "hybrid", "atr", "hybrid_atr"}
        )
        if not valid_atr_pair:
            settings["atr_grid_continuous_mode"] = False
            settings["exit_atr_grid_continuous_mode"] = False

    # Only the ladder matching the selected strategy modes may execute. Saved
    # values for the inactive ladder remain intact for a later mode switch.
    if settings["atr_grid_continuous_mode"] and settings["exit_atr_grid_continuous_mode"]:
        settings["grid_continuous_mode"] = False
        settings["exit_grid_continuous_mode"] = False
    elif settings["grid_continuous_mode"] and settings["exit_grid_continuous_mode"]:
        settings["atr_grid_continuous_mode"] = False
        settings["exit_atr_grid_continuous_mode"] = False

    def bounded_number(key, fallback, minimum, maximum):
        try:
            value = float(settings.get(key, fallback))
        except (TypeError, ValueError):
            value = float(fallback)
        if not math.isfinite(value):
            value = float(fallback)
        settings[key] = min(maximum, max(minimum, value))

    bounded_number("exit_grid_sell_pct", 20.0, 1.0, 100.0)
    bounded_number("grid_reclaim_buffer_pct", 0.15, 0.0, 2.0)
    bounded_number("continuous_grid_adaptation_strength", 50.0, 0.0, 100.0)
    bounded_number("continuous_grid_min_edge_pct", 0.35, 0.0, 10.0)
    mode = str(settings.get("pct_grid_regime_mode", "auto") or "auto").strip().lower()
    settings["pct_grid_regime_mode"] = mode if mode in {"auto", "balanced", "runner"} else "auto"
    bounded_number("pct_grid_runner_pct", 25.0, 0.0, 60.0)
    bounded_number("pct_grid_runner_trail_pct", 3.0, 0.1, 25.0)
    bounded_number("pct_grid_bullish_trend_threshold", 0.35, 0.0, 0.95)

    legacy_level_counts = {}
    for key in ("grid_max_levels", "exit_grid_max_levels"):
        try:
            value = int(settings.get(key, 5))
        except (TypeError, ValueError):
            value = 5
        settings[key] = min(5, max(1, value))
        legacy_level_counts[key] = settings[key]

    enabled_levels = {}
    for side in ("entry", "exit"):
        prefix = "grid_entry" if side == "entry" else "grid_exit"
        legacy_key = "grid_max_levels" if side == "entry" else "exit_grid_max_levels"
        has_explicit_selection = any(f"{prefix}_l{index}_enabled" in settings for index in range(1, 6))
        active = []
        for index in range(1, 6):
            key = f"{prefix}_l{index}_enabled"
            is_enabled = (
                _grid_bool(settings.get(key, True))
                if has_explicit_selection
                else index <= legacy_level_counts[legacy_key]
            )
            settings[key] = is_enabled
            if is_enabled:
                active.append(index)
        if not active:
            settings[f"{prefix}_l1_enabled"] = True
            active = [1]
        enabled_levels[side] = active
    # The new ladder always contains five visible rows; per-level checkboxes
    # now determine participation instead of a contiguous level-count selector.
    settings["grid_max_levels"] = 5
    settings["exit_grid_max_levels"] = 5

    allocation_mode = str(settings.get("allocation_mode", "fixed") or "fixed").lower()
    allocation = settings.get("compound_amount") if allocation_mode == "compound" else settings.get("amount")
    try:
        allocation = float(allocation or settings.get("amount") or 0.0)
    except (TypeError, ValueError):
        allocation = 0.0
    migrated_buy_pcts = {}
    default_buy_pcts = (40.0, 25.0, 15.0, 10.0, 10.0)
    for index in range(1, 6):
        buy_pct_key = f"grid_entry_l{index}_buy_pct"
        raw_pct = settings.get(buy_pct_key)
        if raw_pct is None and allocation > 0:
            try:
                legacy_amount = float(settings.get(f"grid_entry_l{index}_amount"))
                raw_pct = legacy_amount / allocation * 100.0
            except (TypeError, ValueError):
                raw_pct = None
        try:
            raw_pct = float(default_buy_pcts[index - 1] if raw_pct is None else raw_pct)
        except (TypeError, ValueError):
            raw_pct = default_buy_pcts[index - 1]
        if not math.isfinite(raw_pct):
            raw_pct = default_buy_pcts[index - 1]
        migrated_buy_pcts[index] = min(100.0, max(1.0, raw_pct))

    # A full entry ladder represents at most one additional bot allocation.
    # Preserve the relative weighting of legacy dollar levels while preventing
    # migration from silently multiplying the bot's intended capital.
    total_buy_pct = sum(migrated_buy_pcts[index] for index in enabled_levels["entry"])
    if total_buy_pct > 100.0:
        for index in enabled_levels["entry"]:
            migrated_buy_pcts[index] = round(migrated_buy_pcts[index] * 100.0 / total_buy_pct, 4)
    for index in range(1, 6):
        settings[f"grid_entry_l{index}_buy_pct"] = migrated_buy_pcts[index]

    try:
        ladder_version = int(settings.get("continuous_grid_ladder_version", 0) or 0)
    except (TypeError, ValueError):
        ladder_version = 0
    if ladder_version < 2:
        def legacy_values(keys, fallbacks):
            values = []
            for key, fallback in zip(keys, fallbacks):
                try:
                    value = float(settings.get(key, fallback))
                except (TypeError, ValueError):
                    value = float(fallback)
                values.append(value)
            return values

        old_exit_triggers = legacy_values(
            [f"grid_exit_l{index}_pct" for index in range(1, 6)],
            (2.0, 4.0, 6.0, 8.0, 10.0),
        )
        if all(abs(value - expected) < 0.000001 for value, expected in zip(old_exit_triggers, (2, 4, 6, 8, 10))):
            for index, value in enumerate(_GRID_DEFAULT_EXIT_LEVEL_PCTS, start=1):
                settings[f"grid_exit_l{index}_pct"] = value

        old_exit_sizes = legacy_values(
            [f"grid_exit_l{index}_sell_pct" for index in range(1, 6)],
            (25.0, 25.0, 25.0, 25.0, 100.0),
        )
        legacy_size_patterns = (
            (25.0, 25.0, 25.0, 25.0, 100.0),
            (25.0, 25.0, 25.0, 25.0, 25.0),
        )
        has_explicit_exit_sizes = any(
            f"grid_exit_l{index}_sell_pct" in settings for index in range(1, 6)
        )
        if has_explicit_exit_sizes and any(
            all(abs(value - expected) < 0.000001 for value, expected in zip(old_exit_sizes, pattern))
            for pattern in legacy_size_patterns
        ):
            for index in range(1, 6):
                settings[f"grid_exit_l{index}_sell_pct"] = (10.0, 10.0, 15.0, 20.0, 20.0)[index - 1]
            settings["exit_grid_sell_pct"] = 20.0
    settings["continuous_grid_ladder_version"] = 3

    default_sell_pcts = (10.0, 10.0, 15.0, 20.0, 20.0)
    legacy_sell_pct = settings.get("exit_grid_sell_pct", 20.0)
    for index in range(1, 6):
        bounded_number(
            f"grid_exit_l{index}_sell_pct",
            default_sell_pcts[index - 1] if f"grid_exit_l{index}_sell_pct" not in settings else legacy_sell_pct,
            1.0,
            100.0,
        )
    total_sell_pct = sum(
        settings[f"grid_exit_l{index}_sell_pct"] for index in enabled_levels["exit"]
    )
    if total_sell_pct > 100.0:
        for index in enabled_levels["exit"]:
            settings[f"grid_exit_l{index}_sell_pct"] = round(
                settings[f"grid_exit_l{index}_sell_pct"] * 100.0 / total_sell_pct,
                4,
            )

    settings.pop("grid_buy_allocation", None)
    for index in range(1, 6):
        settings.pop(f"grid_entry_l{index}_amount", None)

    for side in ("entry", "exit"):
        prefix = "grid_entry" if side == "entry" else "grid_exit"
        maximum = 50.0 if side == "entry" else 100.0
        defaults = (
            _GRID_DEFAULT_ENTRY_LEVEL_PCTS
            if side == "entry"
            else _GRID_DEFAULT_EXIT_LEVEL_PCTS
        )
        values = []
        for index, fallback in enumerate(defaults, start=1):
            try:
                value = float(settings.get(f"{prefix}_l{index}_pct", fallback))
            except (TypeError, ValueError):
                value = fallback
            values.append(value)
        valid = all(
            math.isfinite(value)
            and 0.1 <= value <= maximum
            and (index == 0 or value > values[index - 1])
            for index, value in enumerate(values)
        )
        if not valid:
            values = list(defaults)
        for index, value in enumerate(values, start=1):
            settings[f"{prefix}_l{index}_pct"] = value

    if settings.get("grid_continuous_mode"):
        first_entry_level = enabled_levels["entry"][0]
        settings["entry_pct_move"] = settings[f"grid_entry_l{first_entry_level}_pct"]
        settings["entry_pct_style"] = "dip_reclaim"
    if settings.get("exit_grid_continuous_mode"):
        settings["enable_pct_trail"] = False

    atr_enabled_levels = {}
    for side in ("entry", "exit"):
        prefix = f"grid_atr_{side}"
        explicit = any(f"{prefix}_l{index}_enabled" in settings for index in range(1, 6))
        active = []
        values = []
        for index, fallback in enumerate(_GRID_DEFAULT_ATR_LEVEL_MULTS, start=1):
            enabled_key = f"{prefix}_l{index}_enabled"
            settings[enabled_key] = _grid_bool(settings.get(enabled_key, True)) if explicit else True
            if settings[enabled_key]:
                active.append(index)
            bounded_number(f"{prefix}_l{index}_mult", fallback, 0.1, 100.0)
            values.append(settings[f"{prefix}_l{index}_mult"])
            size_suffix = "buy_pct" if side == "entry" else "sell_pct"
            bounded_number(f"{prefix}_l{index}_{size_suffix}", 20.0, 1.0, 100.0)
        if not active:
            settings[f"{prefix}_l1_enabled"] = True
            active = [1]
        # Validate the executable ladder only. Disabled rungs may retain their
        # saved values, and an invalid active sequence must not silently erase
        # those unrelated settings. Restore defaults only for the active side
        # when its enabled multipliers are not strictly increasing.
        active_values = [settings[f"{prefix}_l{index}_mult"] for index in active]
        if not all(active_values[pos] > active_values[pos - 1] for pos in range(1, len(active_values))):
            for index in active:
                settings[f"{prefix}_l{index}_mult"] = _GRID_DEFAULT_ATR_LEVEL_MULTS[index - 1]
        size_suffix = "buy_pct" if side == "entry" else "sell_pct"
        total = sum(settings[f"{prefix}_l{index}_{size_suffix}"] for index in active)
        if total > 100.0:
            for index in active:
                key = f"{prefix}_l{index}_{size_suffix}"
                settings[key] = round(settings[key] * 100.0 / total, 4)
        atr_enabled_levels[side] = active

    if settings.get("atr_grid_continuous_mode"):
        first = atr_enabled_levels["entry"][0]
        settings["entry_atr_multiplier"] = settings[f"grid_atr_entry_l{first}_mult"]
        settings["entry_atr_style"] = "pullback_reclaim"
    # Keep fixed ATR preferences intact while the ladder is active. Execution
    # bypasses them at runtime, so disabling the ladder restores the user's
    # prior stop/target/trail choices instead of silently overwriting them.

    # This legacy field was displayed beside entry allocation but was never
    # used by execution. Exit sizing has one canonical setting above.
    settings.pop("grid_sell_pct", None)
    return settings


def _continuous_grid_config_snapshot(settings: dict) -> tuple:
    return tuple((key, settings.get(key)) for key in _GRID_CONFIGURATION_KEYS)


def _reset_continuous_grid_runtime(settings: dict):
    for key in _GRID_RUNTIME_STATE_KEYS:
        settings.pop(key, None)


# Server-owned strategy-preset / corporate-action state. A client may never set
# these: they record ownership of a position, the last acted-on signal, the stored
# stop and split-reconciliation progress.
_STRATEGY_PRESET_SERVER_KEYS = frozenset({
    preset_runtime.OWNS_KEY,
    preset_runtime.LAST_ENTRY_KEY,
    preset_runtime.STOP_KEY,
    preset_runtime.PAUSE_KEY,
    preset_runtime.LAST_CORPORATE_ACTION_KEY,
    preset_runtime.LAST_QTY_KEY,
    "_strategy_manual_backup",
    "_corporate_action_review_logged",
    "strategy_preset_catalog_version",
    "allocation_state",  # Malik allocation ledger/pending orders: server-owned
    "session_state",  # Session Rider entry/exit markers and arming time: server-owned
})

_ALLOCATION_SETTING_KEYS = ("allocation_bull_etf", "allocation_bear_etf", "allocation_exec_time")
_SESSION_SETTING_KEYS = session_schedule.SETTING_KEYS


def _arm_session_bot(settings: dict) -> None:
    """A Session Rider bot only enters in a buy window that opens after it was saved or re-enabled."""
    if not strategy_presets.is_schedule_active(settings):
        return
    state = settings.get("session_state") if isinstance(settings.get("session_state"), dict) else {}
    settings["session_state"] = {**state, "armed_at": datetime.now(timezone.utc).isoformat()}


def _validate_allocation_conflicts(eng, ticker: str, merged: dict) -> None:
    """A Malik bot trades its ETF legs; no other bot may trade the same symbol (400)."""
    import allocation_strategy
    import allocation_executor
    if not strategy_presets.is_allocation_active(merged):
        legs_in_use = {}
    else:
        legs = allocation_strategy.resolve_legs(merged.get("strategy_preset"), merged)
        legs_in_use = {s for s in legs.values() if s}
    for other, settings in (eng.config.TICKER_SETTINGS or {}).items():
        if other == ticker or other not in eng.config.TRADELIST or not isinstance(settings, dict):
            continue
        if other in legs_in_use:
            raise HTTPException(status_code=400, detail=f"{other} already has its own bot; a Malik bot cannot trade it as an ETF leg.")
        if allocation_executor.is_allocation_bot(settings):
            other_legs = {s for s in allocation_strategy.resolve_legs(settings.get("strategy_preset"), settings).values() if s}
            if ticker in other_legs:
                raise HTTPException(status_code=400, detail=f"The {other} Malik bot trades {ticker}; remove it first or pick another ETF there.")
            clash = legs_in_use & other_legs
            if clash:
                raise HTTPException(status_code=400, detail=f"{', '.join(sorted(clash))} is already traded by the {other} Malik bot.")


def _strip_strategy_preset_server_keys(payload) -> None:
    if isinstance(payload, dict):
        for key in _STRATEGY_PRESET_SERVER_KEYS:
            payload.pop(key, None)


def _strategy_preset_in_play(*candidates) -> bool:
    return any(
        isinstance(item, dict) and (item.get("strategy_preset_enabled") or "_strategy_manual_backup" in item)
        for item in candidates
    )


def _bot_scan_interval_defaults(eng, settings: dict) -> int:
    if settings.get("options_trading", settings.get("is_option", False)):
        return int(getattr(eng.config, "OPTIONS_SCAN_INTERVAL_SECONDS", 10))
    return int(getattr(eng.config, "SCAN_INTERVAL_SECONDS", 60))


def _bot_scan_refresh_seconds(eng, settings: dict, has_position: bool, has_order: bool) -> int:
    """Seconds between scans of one bot, as the engine will really use them (display only).

    The engine caps a bot's own interval at the engine default while it has an order working or a fill pending, or holds a
    position whose exit is price-based. The card's countdown must apply the same rule or it would promise a slower scan
    than happens.
    """
    busy = bot_scan_interval.needs_fast_scan(
        settings, has_position=has_position, has_order=has_order,
        is_option=bool(settings.get("options_trading") or settings.get("is_option")),
    )
    return bot_scan_interval.effective_seconds(settings, _bot_scan_interval_defaults(eng, settings), busy=busy)


def _validate_bot_scan_interval(merged: dict) -> None:
    """Reject an invalid per-bot scan interval (400) before any state changes."""
    error = bot_scan_interval.validate(merged)
    if error:
        raise HTTPException(status_code=400, detail=error)


def _validate_strategy_preset_request(merged: dict, ticker: str) -> None:
    """Reject a strategy preset on something it cannot run (400), before any state changes."""
    enabled, _, _ = strategy_presets.normalize_strategy_preset(merged)
    if not enabled:
        return
    errors = strategy_presets.validate_strategy_preset(
        merged,
        ticker=ticker,
        options_trading=bool(merged.get("options_trading") or merged.get("is_option")),
        is_crypto=_is_crypto_symbol(ticker),
    )
    policy = str(merged.get("strategy_preset_position_policy") or "unmanaged").lower()
    if policy not in {"adopt", "unmanaged"}:
        errors.append("strategy_preset_position_policy must be 'adopt' or 'unmanaged'.")
    if errors:
        raise HTTPException(status_code=400, detail=errors[0])


def _resolve_strategy_preset(settings: dict, ticker: str, prior: Optional[dict] = None) -> dict:
    """Apply or clear a strategy preset on a settings dict (returns a new dict).

    Enabling backs up the manual Entry/Exit setup and writes the strategy's
    allow-listed overrides (timeframe, session); disabling restores them. Risk
    and sizing fields are never touched. Settings without any preset state are
    returned unchanged.
    """
    if not _strategy_preset_in_play(settings, prior):
        return settings
    _validate_strategy_preset_request(settings, ticker)
    enabled, _, _ = strategy_presets.normalize_strategy_preset(settings)
    if enabled:
        settings["strategy_preset_position_policy"] = str(
            settings.get("strategy_preset_position_policy") or "unmanaged"
        ).lower()
    return strategy_presets.apply_strategy_preset(settings, prior)


def _normalize_draft_bot_settings(ticker: str, draft_settings: dict, eng: UserEngine) -> dict:
    ticker_key = _normalize_quote_symbol(ticker)
    base_settings = eng.config.TICKER_SETTINGS.get(ticker_key, {})
    settings = dict(base_settings) if isinstance(base_settings, dict) else {}
    incoming = draft_settings if isinstance(draft_settings, dict) else {}
    incoming = dict(incoming)
    _strip_strategy_preset_server_keys(incoming)

    for key, value in incoming.items():
        if value is None:
            settings.pop(key, None)
        else:
            settings[key] = value
    settings = _resolve_strategy_preset(settings, ticker_key, base_settings if isinstance(base_settings, dict) else None)

    if "options_type" in settings:
        settings["options_type"] = normalize_options_type(settings.get("options_type", "calls_only"), default="calls_only")

    indicators = settings.get("indicators")
    if isinstance(indicators, list):
        deduped = []
        seen = set()
        for indicator in strip_removed(indicators)[0]:
            name = str(indicator or "").strip()
            if not name or name in seen:
                continue
            deduped.append(name)
            seen.add(name)
        settings["indicators"] = deduped
        # Indicator calculations consume enabled_indicators, while bot
        # persistence and the frontend use indicators. Keep one canonical
        # selection for draft chart previews so the chart, confluence cards,
        # and threshold counts all evaluate the same pool.
        settings["enabled_indicators"] = list(deduped)

    overrides = settings.get("indicator_overrides")
    if isinstance(overrides, dict):
        settings["indicator_overrides"] = {
            str(key): value for key, value in overrides.items()
            if key is not None and value not in (None, "")
        }

    if _is_crypto_symbol(ticker_key) or "market_session" in settings:
        settings["market_session"] = _normalize_market_session_for_symbol(ticker_key, settings.get("market_session"))

    _normalize_continuous_grid_settings(settings)
    settings["source"] = "bots"
    _normalize_bot_signal_thresholds(settings, eng)
    return settings


def _log_background_task_result(task: asyncio.Task, reason: str):
    if task.cancelled():
        print(f"[settings] Background save cancelled ({reason})")
        return
    exc = task.exception()
    if exc:
        print(f"[settings] Background save failed ({reason}): {_safe_exception_type(exc)}")


def _queue_settings_save(eng: UserEngine, reason: str = "settings"):
    async def _save():
        await eng.save_settings()

    task = asyncio.create_task(_save())
    task.add_done_callback(lambda t: _log_background_task_result(t, reason))
    return task


def _get_chart_analysis_fallback(
    ticker: str,
    timeframe: str,
    settings: dict = None,
    force_refresh: bool = False,
):
    """Use cached/Yahoo OHLCV only when the primary live providers return no chart."""
    timeframe_key = timeframe or "5Min"
    lookback_days = {
        "30Sec": 7,
        "1Min": 7,
        "2Min": 7,
        "3Min": 7,
        "5Min": 60,
        "10Min": 60,
        "15Min": 60,
        "30Min": 60,
        "1Hour": 730,
        "2Hour": 730,
        "4Hour": 730,
        "1Day": 3650,
    }.get(timeframe_key, 60)
    end_date = datetime.now()
    start_date = end_date - timedelta(days=lookback_days)
    try:
        fallback_df = get_historical_data(
            ticker,
            timeframe_key,
            start_date,
            end_date,
            force_yahoo=True,
            force_refresh=force_refresh,
        )
        if fallback_df is None or fallback_df.empty or len(fallback_df) < 30:
            return None
        fallback_settings = dict(settings or {})
        fallback_settings.setdefault("_chart_display_bars", 1650 if timeframe_key == "1Min" else 1200)
        analysis = calculate_indicators_for_df(
            fallback_df,
            timeframe_key,
            ticker,
            settings=fallback_settings,
        )
        if analysis:
            analysis["chart_data_source"] = "yahoo_fallback"
        return analysis
    except Exception as exc:
        print(f"[chart] Historical fallback failed for {ticker} {timeframe_key}: {_safe_exception_type(exc)}")
        return None


def _get_cached_chart_analysis(ticker: str, timeframe: str, eng: UserEngine, force: bool = False, source: str = "dashboard", settings: dict = None):
    ticker_key = _normalize_quote_symbol(ticker)
    timeframe_key = timeframe or get_user_config().DEFAULT_TIMEFRAME
    settings_hash = _cache_settings_hash(eng, ticker_key, source, settings)
    cache_key = stable_cache_key(eng.uid, source, ticker_key, timeframe_key, "webull", settings_hash, 3)
    cache_ttl = _chart_cache_ttl(timeframe_key)
    if isinstance(settings, dict) and settings.get("_chart_preview"):
        # The preview refresh timer is 30s for short timeframes. Keep one
        # provider result warm across that timer so the UI does not fetch and
        # recalculate the same draft twice before the next candle matters.
        preview_ttl = 45 if str(timeframe_key).upper() in {"30SEC", "1MIN", "2MIN", "3MIN", "5MIN"} else 60
        cache_ttl = max(cache_ttl, preview_ttl)
    cached, age = _chart_analysis_cache.peek_with_age(cache_key)
    if cached and not force and age is not None and age < cache_ttl:
        return cached["analysis"], True, age

    # Coalesce duplicate chart requests without letting an old timed-out worker
    # block fresh UI requests. asyncio.wait_for cannot cancel the thread doing
    # indicator work, so a blocking lock here can create 30s head-of-line stalls.
    fetch_lock = _chart_analysis_cache.with_inflight_lock(stable_cache_key("chart_fetch", cache_key))
    lock_acquired = fetch_lock.acquire(blocking=False)
    if not lock_acquired:
        stale_cached, stale_age = _chart_analysis_cache.peek_with_age(cache_key)
        if stale_cached and not force:
            return stale_cached["analysis"], True, stale_age
        if stale_cached and force:
            # A forced refresh is already in flight for this exact symbol,
            # timeframe, source, and settings. Keep the chart usable while
            # that worker publishes the fresh result to the same cache.
            return stale_cached["analysis"], True, stale_age
        # Another request is already doing the Webull + indicator work. Wait a
        # short window for it to populate cache instead of launching duplicate
        # calculations that can make both browser requests time out.
        # Indicator calculation can take 20–25s on a cold 1,200-bar request.
        # Stay inside the 30s route timeout so duplicate requests can observe
        # the result instead of incorrectly becoming a 404 at eight seconds.
        wait_deadline = time.time() + 28.0
        while time.time() < wait_deadline:
            if fetch_lock.acquire(blocking=False):
                try:
                    cached, age = _chart_analysis_cache.peek_with_age(cache_key)
                    if cached and (
                        not force
                        or (age is not None and age < 2.0)
                    ):
                        return cached["analysis"], True, age
                    break
                finally:
                    fetch_lock.release()
            time.sleep(0.1)

        stale_cached, stale_age = _chart_analysis_cache.peek_with_age(cache_key)
        if stale_cached:
            return stale_cached["analysis"], True, stale_age
        return None, False, 0.0

    try:
        cached, age = _chart_analysis_cache.peek_with_age(cache_key)
        # If another request just forced a refresh, reuse it instead of hitting
        # Webull again. This keeps candles fresh while preventing stampedes.
        if cached and (
            (not force and age is not None and age < cache_ttl)
            or (force and age is not None and age < 2.0)
        ):
            return cached["analysis"], True, age

        analysis = get_full_analysis(ticker_key, timeframe=timeframe_key, data_source="webull", settings=settings)
        if not analysis:
            stale_cached, stale_age = _chart_analysis_cache.peek_with_age(cache_key)
            if stale_cached:
                print(f"[chart] Live providers unavailable for {ticker_key}; serving stale chart cache")
                return stale_cached["analysis"], True, stale_age
            analysis = _get_chart_analysis_fallback(
                ticker_key,
                timeframe_key,
                settings,
                force_refresh=force,
            )
        if analysis:
            _chart_analysis_cache.set(cache_key, {"analysis": analysis})
        return analysis, False, 0.0
    finally:
        fetch_lock.release()
        _chart_analysis_cache.release_inflight_lock(stable_cache_key("chart_fetch", cache_key), fetch_lock)



def _get_prior_day_ohlc(price_history):
    return _shared_prior_day_ohlc_from_history(price_history)


def _sanitize_json_values(value):
    """Return a JSON-safe copy, replacing non-finite numeric values with None.

    Indicator calculations can legitimately contain NaN/Inf while an indicator
    is warming up or a provider returns an incomplete bar. Starlette's JSON
    response encoder rejects those values, so sanitize at the API boundary
    instead of allowing a single incomplete field to turn a chart into a 500.
    """
    if isinstance(value, dict):
        return {key: _sanitize_json_values(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_sanitize_json_values(item) for item in value]
    # numbers.Real also covers numpy floating/integer scalar values returned by
    # pandas calculations. Keep booleans as booleans (bool is a Real subclass).
    if isinstance(value, numbers.Real) and not isinstance(value, bool):
        numeric = float(value)
        if not math.isfinite(numeric):
            return None
        if isinstance(value, numbers.Integral):
            return int(value)
        return numeric if not isinstance(value, float) else value
    return value


def _has_usable_chart_history(analysis: dict) -> bool:
    """Whether the latest chart candle has a finite close."""
    if not isinstance(analysis, dict):
        return False
    history = analysis.get("price_history")
    if not isinstance(history, (list, tuple)):
        return False
    if not history:
        return False
    latest = history[-1]
    if not isinstance(latest, dict):
        return False
    close = latest.get("close")
    try:
        return close is not None and math.isfinite(float(close))
    except (TypeError, ValueError):
        return False


def _next_level_above(levels, current_price, fallback_step):
    valid = sorted([float(v) for v in levels if v is not None and float(v) > current_price])
    if valid:
        return valid[0]
    step = abs(float(fallback_step or 0.0))
    return current_price + step if step > 0 else None


def _nearest_level_below(levels, current_price):
    valid = sorted([float(v) for v in levels if v is not None and float(v) < current_price], reverse=True)
    return valid[0] if valid else None


def _dynamic_pivot_exit_levels(prev_h, prev_l, prev_c, current_price):
    day_range = max(float(prev_h) - float(prev_l), 0.0)
    if day_range <= 0:
        return None
    p_val = (float(prev_h) + float(prev_l) + float(prev_c)) / 3.0
    r1 = (2 * p_val) - float(prev_l)
    s1 = (2 * p_val) - float(prev_h)
    r2 = p_val + day_range
    s2 = p_val - day_range
    r3 = float(prev_h) + 2 * (p_val - float(prev_l))
    s3 = float(prev_l) - 2 * (float(prev_h) - p_val)
    tp = _next_level_above([r1, r2, r3, r3 + day_range], current_price, day_range)
    sl = _nearest_level_below([r3, r2, r1, p_val, s1, s2, s3], current_price)
    return {"tp": tp, "sl": sl}


def _dynamic_high_low_exit_levels(prev_h, prev_l, current_price, buffer_pct=0.0):
    return _buffered_dynamic_high_low_exit_levels(prev_h, prev_l, current_price, buffer_pct)


def _format_exit_protection_status(
    eng: UserEngine,
    symbol: str,
    is_option: bool,
    is_crypto: bool,
    settings: dict = None,
    scan: dict = None,
    position: dict = None,
):
    settings = settings or {}
    scan = scan or {}
    position = position or {}
    sell_mode = str(settings.get("sell_mode") or "indicator")
    if sell_mode not in FIXED_LEVEL_EXIT_MODES:
        return {"label": "Exit Protection", "status": f"Not Used | Watcher | {sell_mode} exit", "active": False, "protection_type": "watcher"}
    if settings.get("exit_protection_enabled", True) is False:
        return {"label": "Exit Protection", "status": "Disabled | Watcher | bot setting off", "active": False, "protection_type": "watcher"}

    if is_option:
        return {"label": "Exit Protection", "status": "Pending | Watcher | awaiting option/underlying targets", "active": False, "protection_type": "watcher"}

    try:
        targets = eng._build_exit_protection_targets(symbol, scan, position, settings)
    except Exception as exc:
        print(f"[dashboard] Exit protection target check failed for {symbol}: {_safe_exception_type(exc)}")
        targets = None
    if not targets:
        return {"label": "Exit Protection", "status": "Pending | Watcher | awaiting TP/SL targets", "active": False, "protection_type": "watcher"}

    take_profit = float(targets.get("take_profit") or 0.0)
    stop_loss = float(targets.get("stop_loss") or 0.0)
    if take_profit > 0 and stop_loss > 0:
        return {
            "label": "Exit Protection",
            "status": f"Active | Watcher | TP ${take_profit:.2f} | SL ${stop_loss:.2f}",
            "active": True,
            "take_profit": take_profit,
            "stop_loss": stop_loss,
            "protection_type": "watcher",
        }

    return {"label": "Exit Protection", "status": "Pending | Watcher | awaiting TP/SL targets", "active": False, "protection_type": "watcher"}


def _technical_scan_from_analysis(
    ticker: str,
    timeframe: str,
    analysis: dict,
    use_bot_settings: bool = False,
    ai_sentiment_score: float = 0.0,
    ai_sentiment_confidence: float = 0.0,
    ai_sentiment_summary: str = "",
    ai_sentiment_key_factor: str = "Indicator signals",
):
    if not analysis:
        return None

    decision = get_confluence_decision(
        ticker,
        analysis,
        ai_sentiment_score=ai_sentiment_score,
        ai_sentiment_confidence=ai_sentiment_confidence,
        use_bot_settings=use_bot_settings
    )
    price = analysis.get("price", 0.0)
    prior_day = analysis.get("prior_day_ohlc") or _get_prior_day_ohlc(analysis.get("price_history", []))
    return _sanitize_json_values({
        "prior_day_ohlc": prior_day,
        "time": get_now().isoformat(),
        "ticker": _normalize_quote_symbol(ticker),
        "timeframe": timeframe,
        "action": decision.get("action", "HOLD"),
        "reason": decision.get("reason", ""),
        "price": _format_scan_price(price),
        "price_raw": price,
        "signals": decision.get("signals", {}),
        "bullish_count": decision.get("bullish_count", 0),
        "bearish_count": decision.get("bearish_count", 0),
        "total_signals": sum(
            1 for signal in decision.get("signals", {}).values()
            if (signal or {}).get("enabled", True)
        ),
        "atr": analysis.get("atr", 0.0),
        "rsi": analysis.get("rsi"),
        "sentiment_score": ai_sentiment_score,
        "sentiment_confidence": ai_sentiment_confidence,
        "sentiment_summary": ai_sentiment_summary,
        "sentiment_key_factor": ai_sentiment_key_factor,
        "news_sentiment_pct": decision.get("news_sentiment_pct", round(((float(ai_sentiment_score) + 1.0) / 2.0) * 100.0, 1)),
        "news_sentiment_direction": decision.get("news_sentiment_direction", "NEUTRAL"),
        "news_alignment": decision.get("news_alignment", "ALIGNED"),
        "news_alignment_applied": decision.get("news_alignment_applied", False),
        "news_alignment_reason": decision.get("news_alignment_reason", ""),
        "model_action": decision.get("model_action", decision.get("action", "HOLD")),
        "position_sizing": {},
        "price_history": analysis.get("price_history", []),
        "risk_status": {},
        "is_custom": False,
    })


def _build_chart_payload(
    ticker: str,
    timeframe: str,
    analysis: dict,
    settings: dict,
    eng: UserEngine,
    *,
    use_bot_settings: bool = False,
    cached: bool = False,
    cache_age: float = 0.0,
    started: float = None,
    ai_data: dict = None,
    preview_meta: dict = None,
):
    ai_payload = ai_data or {}
    scan = _technical_scan_from_analysis(
        ticker,
        timeframe,
        analysis,
        use_bot_settings=use_bot_settings,
        ai_sentiment_score=float(ai_payload.get("score", 0.0) or 0.0),
        ai_sentiment_confidence=float(ai_payload.get("confidence", 0.0) or 0.0),
        ai_sentiment_summary=ai_payload.get("summary", "") or "",
        ai_sentiment_key_factor=ai_payload.get("key_factor", "Indicator signals") or "Indicator signals",
    )
    formatted_scan = _format_scan_for_ui(scan, use_global_settings=(not use_bot_settings))
    active_settings = settings if isinstance(settings, dict) else {}
    selected_indicators = active_settings.get("indicators")
    if isinstance(selected_indicators, list):
        source_signals = formatted_scan.get("signals", {}) or {}
        selected_signals = {}
        seen_indicators = set()
        for raw_indicator_name in selected_indicators:
            indicator_name = str(raw_indicator_name or "").strip()
            if not indicator_name or indicator_name in seen_indicators:
                continue
            seen_indicators.add(indicator_name)
            source_signal = source_signals.get(indicator_name)
            if isinstance(source_signal, dict):
                selected_signals[indicator_name] = {
                    **source_signal,
                    "enabled": True,
                }
                continue
            selected_signals[indicator_name] = {
                "signal": "NEUTRAL",
                "reason": "Awaiting sufficient market data",
                "weight": 1,
                "enabled": True,
            }
        formatted_scan["signals"] = selected_signals
        formatted_scan["bullish_count"] = sum(
            signal.get("weight", 1)
            for signal in selected_signals.values()
            if signal.get("signal") == "BULLISH"
        )
        formatted_scan["bearish_count"] = sum(
            signal.get("weight", 1)
            for signal in selected_signals.values()
            if signal.get("signal") == "BEARISH"
        )
        formatted_scan["total_signals"] = len(selected_signals)
    is_opt = active_settings.get('options_trading', False)
    if active_settings:
        formatted_scan["entry_forecast"] = _build_entry_forecast(
            active_settings,
            scan,
            is_option=is_opt,
            options_type=active_settings.get("options_type", "calls_only"),
            eng=eng,
        )
        formatted_scan["swing_entry_forecast"] = _build_swing_forecast(
            active_settings,
            scan,
            mode="entry",
            is_long=True,
            is_option=is_opt,
            options_type=active_settings.get("options_type", "calls_only"),
            eng=eng,
        )
        formatted_scan["swing_exit_forecast"] = _build_swing_forecast(
            active_settings,
            scan,
            mode="exit",
            is_long=True,
            is_option=is_opt,
            options_type=active_settings.get("options_type", "calls_only"),
            eng=eng,
        )
    else:
        formatted_scan["entry_forecast"] = ""
        formatted_scan["swing_entry_forecast"] = ""
        formatted_scan["swing_exit_forecast"] = ""

    # Send candles exactly once. Previously the same list was serialized in
    # primaryScan.price_history, priceHistory, and price_history, tripling the
    # chart response size without adding any information.
    formatted_scan.pop("price_history", None)
    price_history = scan.get("price_history", [])
    total_ms = ((time.perf_counter() - started) * 1000) if started is not None else 0.0
    return _sanitize_json_values({
        "ticker": ticker,
        "timeframe": timeframe,
        "cached": cached,
        "cache_age_seconds": round(cache_age or 0.0, 1),
        "primaryScan": formatted_scan,
        "signals": formatted_scan.get("signals", {}),
        "priceHistory": price_history,
        "action": formatted_scan.get("action", "HOLD"),
        "reason": formatted_scan.get("reason", ""),
        "bullish_count": formatted_scan.get("bullish_count", 0),
        "bearish_count": formatted_scan.get("bearish_count", 0),
        "total_signals": formatted_scan.get("total_signals", 0),
        "preview_meta": preview_meta or {},
        "candle_meta": analysis.get("candle_meta", {}),
        "volumeProfile": analysis.get("volume_profile") or {},
        "performance": {
            "total_ms": total_ms,
            "cache_age_seconds": round(cache_age or 0.0, 1),
            "cached": cached,
        }
    })


def _normalize_quote_symbol(symbol: str) -> str:
    return str(symbol or "").upper().replace("/", "").strip()


def _is_crypto_symbol(symbol: str) -> bool:
    clean = _normalize_quote_symbol(symbol)
    return clean.endswith(("USD", "USDT", "USDC")) or clean in {"BTC", "ETH", "XRP", "DOGE", "LTC", "ADA", "SOL"}


def _normalize_market_session_for_symbol(symbol: str, session: str = None) -> str:
    session_text = str(session or "").strip()
    if _is_crypto_symbol(symbol):
        return "24h"
    if session_text.lower() in {"", "default"}:
        return "Regular"
    if session_text.lower() == "crypto24":
        return "24h"
    return session_text


def _normalize_screener_market_session(symbol: str, session: str = None, is_options: bool = False) -> str:
    """Return a session the independent Screener can evaluate with current data.

    Crypto has continuous candles.  The stock Screener's free provider path has
    regular and pre/post-market bars only; it must never present a stock 24/5
    setting as live overnight analysis.  Legacy stock ``24h`` profiles are
    migrated to Extended instead of silently retaining a misleading label.
    """
    if _is_crypto_symbol(symbol):
        return "24h"
    if is_options:
        return "Regular"
    value = str(session or "Extended").strip().lower()
    if value in {"extended", "all", "24h", "crypto24", "24/5", "overnight"}:
        return "Extended"
    return "Regular"


def _validate_market_session_for_symbol(symbol: str, market_session: str, eng: UserEngine) -> dict:
    session = _normalize_market_session_for_symbol(symbol, market_session)
    check = eng.broker.check_market_session_allowed(symbol, session)
    if not check.get("success") or not check.get("allowed"):
        message = check.get("message") or check.get("error") or f"{symbol} is not eligible for {session} trading."
        raise HTTPException(status_code=400, detail=message)
    return {**check, "market_session": session}


def _format_quote_price(symbol: str, price) -> str:
    try:
        price_float = float(price)
    except (TypeError, ValueError):
        return ""
    clean = _normalize_quote_symbol(symbol)
    is_crypto = any(clean.endswith(base) for base in ["USD", "USDT", "USDC"])

    if is_crypto:
        decimals = 8
    elif price_float < 1.0:
        decimals = 4
    else:
        decimals = 2

    res = f"${price_float:.{decimals}f}"
    if is_crypto and '.' in res:
        res = res.rstrip('0').rstrip('.')
        if not res[1:]: return "$0" # if it was $0.00000000 -> $
    return res


def _get_stale_latest_prices(eng: UserEngine, tickers: list, max_age: int = 300) -> dict:
    clean_tickers = list(dict.fromkeys(_normalize_quote_symbol(t) for t in tickers if _normalize_quote_symbol(t)))
    result = {}
    for ticker in clean_tickers:
        cache_key = stable_cache_key(eng.uid, ticker, "quote", 1)
        cached, age = _quote_cache.peek_with_age(cache_key)
        if cached is not None and age is not None and age < max_age:
            result[ticker] = cached
    return result


def _get_cached_latest_prices(eng: UserEngine, tickers: list, max_age: int = 3, stale_max_age: int = 300) -> dict:
    clean_tickers = list(dict.fromkeys(_normalize_quote_symbol(t) for t in tickers if _normalize_quote_symbol(t)))
    if not clean_tickers:
        return {}

    result = {}
    misses = []

    for ticker in clean_tickers:
        cache_key = stable_cache_key(eng.uid, ticker, "quote", 1)
        cached, age = _quote_cache.peek_with_age(cache_key)
        if cached is not None and age is not None and age < max_age:
            result[ticker] = cached
        else:
            misses.append(ticker)

    if misses:
        fetch_key = stable_cache_key(eng.uid, "quote_provider", 1)
        with _quote_cache.with_inflight_lock(fetch_key):
            # Another request may have refreshed the same symbols while this
            # request was waiting for the lock.
            still_missing = []
            for ticker in misses:
                cache_key = stable_cache_key(eng.uid, ticker, "quote", 1)
                cached, age = _quote_cache.peek_with_age(cache_key)
                if cached is not None and age is not None and age < max_age:
                    result[ticker] = cached
                else:
                    still_missing.append(ticker)
            misses = still_missing

            if not misses:
                return result

            provider = (
                eng.broker
                if hasattr(eng.broker, "get_latest_prices") and getattr(eng.broker, "market_client", None)
                else None
            )
            provider_source = "broker" if provider is not None else "webull"
            if provider is None:
                provider = _get_webull_market_data_broker()

            if provider is not None:
                provider_misses = (
                    [ticker for ticker in misses if not _webull_unavailable_quote_cache.get(ticker, ttl_seconds=900)]
                    if provider_source == "webull" else misses
                )
                try:
                    fresh = provider.get_latest_prices(provider_misses) if provider_misses else {}
                    for ticker, price in fresh.items():
                        cache_key = stable_cache_key(eng.uid, _normalize_quote_symbol(ticker), "quote", 1)
                        _quote_cache.set(cache_key, price)
                        result[_normalize_quote_symbol(ticker)] = price
                except Exception as e:
                    print(f"[dashboard] Live quote fetch failed for {provider_misses}: {_safe_exception_type(e)}")
                if provider_source == "webull":
                    for ticker in provider_misses:
                        if ticker not in result:
                            _webull_unavailable_quote_cache.set(ticker, True)

            # Yahoo is the fallback for symbols that Webull does not list,
            # including valid equities such as DJT. This runs in the existing
            # worker thread and is cached with the same quote TTL.
            for ticker in misses:
                if ticker not in result:
                    yahoo_price = _get_yahoo_latest_price(ticker)
                    if yahoo_price is not None:
                        cache_key = stable_cache_key(eng.uid, ticker, "quote", 1)
                        _quote_cache.set(cache_key, yahoo_price)
                        result[ticker] = yahoo_price

            for ticker in misses:
                if ticker not in result:
                    cache_key = stable_cache_key(eng.uid, ticker, "quote", 1)
                    cached, age = _quote_cache.peek_with_age(cache_key)
                    if cached is not None and age is not None and age < stale_max_age:
                        result[ticker] = cached

    return result

def _lookup_quote_price(latest_prices: dict, symbol: str):
    clean = _normalize_quote_symbol(symbol)
    for key in (symbol, clean):
        if key in latest_prices:
            return latest_prices[key]
    return None


_previous_close_cache = TTLCacheStore(maxsize=512)
_PREVIOUS_CLOSE_TTL_SECONDS = 1800


def _quote_reference_session(now: datetime, crypto: bool = False):
    """The trading date whose change the chart header reports (the session the live price belongs to).

    Stocks: today from 04:00 ET (pre-market) on a trading day, otherwise the most recent trading day. Crypto trades
    continuously, so it is the current UTC day."""
    if crypto:
        return now.astimezone(timezone.utc).date()
    from nyse_calendar import is_trading_day
    et = now.astimezone(pytz.timezone("America/New_York"))
    day = et.date() if et.hour >= 4 else et.date() - timedelta(days=1)
    for _ in range(10):
        if is_trading_day(day):
            break
        day -= timedelta(days=1)
    return day


def _previous_close_from_daily(rows, session_date):
    """Previous regular-session close from ascending ``(date, close)`` daily rows.

    When the daily series already has a bar for the reference session, the previous close is the bar before it. When it
    does not yet (pre-market), the last bar is the previous session. This never depends on the chart timeframe."""
    clean = []
    for day, close in rows or []:
        try:
            value = float(close)
        except (TypeError, ValueError):
            continue
        if math.isfinite(value) and value > 0:
            clean.append((day, value))
    if not clean:
        return None
    if clean[-1][0] >= session_date:
        return clean[-2][1] if len(clean) >= 2 else None
    return clean[-1][1]


def _quote_change_fields(price, previous_close) -> dict:
    """previous_close / change / change_pct for a live price, or all None when either input is unusable."""
    try:
        px = float(price)
        prev = float(previous_close)
    except (TypeError, ValueError):
        return {"previous_close": None, "change": None, "change_pct": None}
    if not (math.isfinite(px) and math.isfinite(prev)) or px <= 0 or prev <= 0:
        return {"previous_close": None, "change": None, "change_pct": None}
    change = px - prev
    return {"previous_close": prev, "change": change, "change_pct": change / prev * 100.0}


def _daily_rows_from_provider(clean: str):
    import indicators as _ind
    crypto = _is_crypto_symbol(clean)
    bars = _ind._get_wb_data_provider().get_bars(
        clean, timespan="D", count=10, trading_sessions="RTH,PRE,ATH" if crypto else "RTH",
    )
    if isinstance(bars, list) and bars and isinstance(bars[0], dict) and "result" in bars[0]:
        bars = bars[0].get("result") or []
    if not isinstance(bars, list) or not bars:
        return []
    times = _ind._provider_time_to_datetime([b.get("time") for b in bars if isinstance(b, dict)])
    tz = timezone.utc if crypto else pytz.timezone("America/New_York")
    rows = []
    for stamp, bar in zip(times, [b for b in bars if isinstance(b, dict)]):
        if stamp is None or str(stamp) == "NaT":
            continue
        rows.append((stamp.astimezone(tz).date(), bar.get("close")))
    return sorted(rows, key=lambda r: r[0])


def _daily_rows_from_yahoo(clean: str):
    import requests
    response = requests.get(
        f"https://query1.finance.yahoo.com/v8/finance/chart/{clean}",
        params={"range": "10d", "interval": "1d"},
        headers={"User-Agent": "BotBulls/1.0"},
        timeout=3.0,
    )
    response.raise_for_status()
    result = (response.json().get("chart") or {}).get("result") or []
    if not result:
        return []
    stamps = result[0].get("timestamp") or []
    quote_rows = (result[0].get("indicators") or {}).get("quote") or []
    closes = (quote_rows[0] if quote_rows else {}).get("close") or []
    tz = timezone.utc if _is_crypto_symbol(clean) else pytz.timezone("America/New_York")
    return sorted(
        ((datetime.fromtimestamp(int(t), tz=timezone.utc).astimezone(tz).date(), c) for t, c in zip(stamps, closes) if c is not None),
        key=lambda r: r[0],
    )


def _get_previous_close(symbol: str):
    """Previous regular-session close for the live-quote change, cached per symbol (it changes once a day)."""
    clean = _normalize_quote_symbol(symbol)
    if not clean:
        return None
    cached = _previous_close_cache.get(clean, ttl_seconds=_PREVIOUS_CLOSE_TTL_SECONDS)
    if cached is not None:
        return cached or None
    session = _quote_reference_session(datetime.now(timezone.utc), _is_crypto_symbol(clean))
    value = None
    for fetch in (_daily_rows_from_provider, _daily_rows_from_yahoo):
        try:
            value = _previous_close_from_daily(fetch(clean), session)
        except Exception as exc:
            print(f"[dashboard] Previous close via {fetch.__name__} failed for {clean}: {_safe_exception_type(exc)}")
            value = None
        if value:
            break
    # Cache a miss as 0.0 so an unsupported symbol does not hit the providers on every poll.
    _previous_close_cache.set(clean, value or 0.0)
    return value


def _get_yahoo_latest_price(symbol: str):
    """Fetch one quote from Yahoo when Webull does not support the symbol."""
    clean = _normalize_quote_symbol(symbol)
    if not clean:
        return None
    try:
        import requests
        response = requests.get(
            f"https://query1.finance.yahoo.com/v8/finance/chart/{clean}",
            params={"range": "1d", "interval": "1m", "includePrePost": "true"},
            headers={"User-Agent": "BotBulls/1.0"},
            timeout=3.0,
        )
        response.raise_for_status()
        result = (response.json().get("chart") or {}).get("result") or []
        if not result:
            return None
        meta = result[0].get("meta") or {}
        candidates = [meta.get("regularMarketPrice"), meta.get("postMarketPrice")]
        indicators = result[0].get("indicators") or {}
        quote_rows = indicators.get("quote") or []
        closes = (quote_rows[0] if quote_rows else {}).get("close") or []
        candidates.extend(reversed(closes))
        for candidate in candidates:
            try:
                price = float(candidate)
            except (TypeError, ValueError):
                continue
            if math.isfinite(price) and price > 0:
                return price
    except Exception:
        # The caller will retain a stale cache (when available) or return the
        # normal unavailable response. Do not turn a fallback outage into a
        # second noisy provider error.
        return None
    return None


def _get_cached_quote_detail(eng: UserEngine, symbol: str, max_age: int = 10, stale_max_age: int = 300) -> dict:
    clean = _normalize_quote_symbol(symbol)
    cache_key = stable_cache_key(eng.uid, clean, "quote", 1)
    cached, age = _quote_cache.peek_with_age(cache_key)
    if cached is not None and age is not None and age < max_age:
        return {"price": cached, "cache_age": age, "source": "cache", "stale": False}

    with _quote_cache.with_inflight_lock(stable_cache_key(eng.uid, "quote_provider", 1)):
        cached, age = _quote_cache.peek_with_age(cache_key)
        if cached is not None and age is not None and age < max_age:
            return {"price": cached, "cache_age": age, "source": "cache", "stale": False}

        providers = []
        webull_provider = _get_webull_market_data_broker()
        webull_unavailable = _webull_unavailable_quote_cache.get(clean, ttl_seconds=900)
        if webull_provider is not None and not webull_unavailable:
            providers.append(("webull", webull_provider))

        broker_provider = (
            eng.broker
            if hasattr(eng.broker, "get_latest_prices") and getattr(eng.broker, "market_client", None)
            else None
        )
        if broker_provider is not None and broker_provider is not webull_provider:
            providers.append(("broker", broker_provider))

        for source, provider in providers:
            try:
                fresh = provider.get_latest_prices([clean]) or {}
                price = _lookup_quote_price(fresh, clean)
                if price is not None:
                    _quote_cache.set(cache_key, price)
                    return {"price": price, "cache_age": 0.0, "source": source, "stale": False}
                if source == "webull":
                    # Unsupported symbols such as DJT should not generate the
                    # same Webull 417 on every refresh while Yahoo supplies a
                    # valid fallback quote.
                    _webull_unavailable_quote_cache.set(clean, True)
            except Exception as e:
                if source == "webull":
                    _webull_unavailable_quote_cache.set(clean, True)
                print(f"[dashboard] Live quote fetch failed for {clean} via {source}: {_safe_exception_type(e)}")

        yahoo_price = _get_yahoo_latest_price(clean)
        if yahoo_price is not None:
            _quote_cache.set(cache_key, yahoo_price)
            return {"price": yahoo_price, "cache_age": 0.0, "source": "yahoo", "stale": False}

    cached, age = _quote_cache.peek_with_age(cache_key)
    if cached is not None and age is not None and age < stale_max_age:
        return {"price": cached, "cache_age": age, "source": "stale_cache", "stale": True}

    return {"price": None, "cache_age": None, "source": "unavailable", "stale": True}


def _start_quote_refresh_task(eng: UserEngine, symbol: str):
    """Run at most one provider refresh per user/symbol without blocking cached responses."""
    clean = _normalize_quote_symbol(symbol)
    task_key = stable_cache_key(eng.uid, clean, "quote_refresh", 1)
    existing = _quote_refresh_tasks.get(task_key)
    if existing is not None and not existing.done():
        return existing

    async def _refresh():
        try:
            return await asyncio.to_thread(_get_cached_quote_detail, eng, clean, 0, 300)
        finally:
            _quote_refresh_tasks.pop(task_key, None)

    task = asyncio.create_task(_refresh())
    _quote_refresh_tasks[task_key] = task
    return task


def _get_webull_market_data_broker():
    """Return a data-only Webull broker for realtime quotes, independent of the trading broker."""
    global _webull_market_data_broker

    if _webull_market_data_broker and getattr(_webull_market_data_broker, "market_client", None):
        return _webull_market_data_broker

    try:
        from webull_broker import WebullBroker
        broker = WebullBroker(data_only=True)
        if getattr(broker, "market_client", None):
            _webull_market_data_broker = broker
            return broker
    except Exception as e:
        print(f"[dashboard] Webull quote client unavailable: {type(e).__name__}")
    return None


def _fetch_latest_watchlist_prices(eng: UserEngine, tickers: list) -> dict:
    if not tickers:
        return {}

    clean_tickers = list(dict.fromkeys(_normalize_quote_symbol(t) for t in tickers if _normalize_quote_symbol(t)))
    provider = (
        eng.broker
        if hasattr(eng.broker, "get_latest_prices") and getattr(eng.broker, "market_client", None)
        else None
    )

    if provider is None:
        provider = _get_webull_market_data_broker()

    if provider is None:
        return {}

    try:
        return provider.get_latest_prices(clean_tickers) or {}
    except Exception as e:
        print(f"[dashboard] Live Webull price fetch failed: {type(e).__name__}")
        return {}

async def verify_token(request: Request, authorization: str = Header(None)):
    if not authorization:
        raise HTTPException(status_code=401, detail="Missing Authorization header")
    if not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Invalid Authorization format")
    token = authorization[len("Bearer "):].strip()
    if not token or len(token) > 4096 or " " in token:
        raise HTTPException(status_code=401, detail="Invalid Authorization format")
    if _DEV_AUTH_TOKEN and hmac.compare_digest(token, _DEV_AUTH_TOKEN):
        client_host = request.client.host if request.client else ""
        if os.getenv("ENV", "").lower() == "development" and client_host in {"127.0.0.1", "::1", "localhost"}:
            return {"uid": "dev-user", "email": "dev@example.com"}
        else:
            raise HTTPException(status_code=401, detail="Development authentication is loopback-only")
    try:
        claims = auth.verify_id_token(token)
        return claims
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=401, detail="Invalid or expired authentication credentials")

def _legal_debug_uid(user: dict) -> str:
    """Return a non-reversible correlation value for legal-flow diagnostics."""
    uid = str((user or {}).get("uid") or "")
    return hashlib.sha256(uid.encode("utf-8")).hexdigest()[:12] if uid else "unknown"

async def get_user_engine(request: Request, user: dict = Depends(verify_token)) -> UserEngine:
    path = request.url.path
    query = request.query_params
    legal_endpoint = path.startswith("/api/legal/")
    legal_started = time.perf_counter() if legal_endpoint else None
    legal_uid = _legal_debug_uid(user) if legal_endpoint else ""
    if legal_endpoint:
        print(f"[legal-debug] start path={path} uid_hash={legal_uid}", flush=True)
    eng = user_manager.get_engine(user['uid'])
    cloud_load_needed = not getattr(eng, 'loaded_from_cloud', False) and not (
        legal_endpoint and getattr(eng, 'legal_settings_loaded', False)
    )
    if cloud_load_needed:
        async with eng.load_lock:
            cloud_load_needed = not eng.loaded_from_cloud and not (
                legal_endpoint and getattr(eng, 'legal_settings_loaded', False)
            )
            if cloud_load_needed:
                try:
                    await eng.load_from_cloud(connect_broker=not legal_endpoint)
                except Exception as exc:
                    if legal_endpoint:
                        elapsed_ms = (time.perf_counter() - legal_started) * 1000
                        print(f"[legal-debug] load-error path={path} uid_hash={legal_uid} elapsed_ms={elapsed_ms:.0f} error={type(exc).__name__}", flush=True)
                    raise
                if legal_endpoint:
                    elapsed_ms = (time.perf_counter() - legal_started) * 1000
                    print(f"[legal-debug] load-complete path={path} uid_hash={legal_uid} elapsed_ms={elapsed_ms:.0f} status={eng.alpaca_load_status}", flush=True)

    acceptance = getattr(eng.config, "legal_acceptance", {}) or {}
    if not legal_endpoint and acceptance.get("terms_version") != LEGAL_TERMS_VERSION:
        raise HTTPException(
            status_code=TERMS_REQUIRED_STATUS,
            detail={
                "code": "TERMS_ACCEPTANCE_REQUIRED",
                "terms_version": LEGAL_TERMS_VERSION,
                "message": "Accept the current BotBulls agreement before using protected platform features.",
            },
        )
    is_backtest_status = (
        path == "/api/alpaca_status"
        or (
            path == "/api/dashboard"
            and query.get("source") == "backtest"
            and not query.get("ticker")
            and not query.get("timeframe")
        )
    )
    is_backtest_execution = path in {"/api/backtest", "/api/options/backtest", "/api/backtest/optimize", "/api/backtest/replay/data"}
    is_chart_only_request = (
        path.startswith("/api/chart/")
        or path == "/api/signals/"
        or path.startswith("/api/signals/")
        or path == "/api/ai-indicator"
        or path == "/api/quote/"
        or path.startswith("/api/quote/")
        or path == "/api/market-data"
    )
    is_debug_endpoint = path == "/api/debug-doge"

    # Legal acceptance is part of sign-in/account setup.  It must not start
    # the user's trading loop as a side effect of recording consent.
    if not legal_endpoint and not eng.bot_running and not is_backtest_status and not is_backtest_execution and not is_chart_only_request and not is_debug_endpoint:
        async with eng.task_start_lock:
            if not eng.bot_running and not (eng.task and not eng.task.done()):
                eng.task = asyncio.create_task(eng.trading_loop())
    set_user_config(eng.config)
    if legal_endpoint:
        elapsed_ms = (time.perf_counter() - legal_started) * 1000
        print(f"[legal-debug] ready path={path} uid_hash={legal_uid} elapsed_ms={elapsed_ms:.0f}", flush=True)
    return eng


async def get_screener_state(user: dict = Depends(verify_token)) -> ScreenerUserState:
    """Load Screener state without creating or starting a trading engine."""
    state = screener_manager.get_state(user["uid"])
    await state.load()
    set_user_config(state.config)
    return state

def get_now():
    """Returns current time in the configured timezone."""
    tz = pytz.timezone(get_user_config().TIMEZONE)
    return datetime.now(tz)

class AlpacaConfig(BaseModel):
    api_key: str
    secret_key: str
    paper: bool = True
    live_trading_acknowledged: bool = False


class LegalAcceptance(BaseModel):
    terms_version: str
    paper_trading_acknowledged: bool = True


async def _scan_screener_state(
    state: ScreenerUserState,
    symbols: list[str] = None,
    force_scan: bool = False,
) -> None:
    """Refresh Screener analysis only; explicit requests may force one run."""
    if state.scan_lock.locked():
        return
    async with state.scan_lock:
        if not await state.try_acquire_process_scan_lock():
            # Another backend worker owns this user's scan lease. Its result
            # will be persisted for the next scheduler/read cycle.
            return
        # The file lock only serializes workers. Reloading here makes the
        # durable notification and candle ledger authoritative for dedupe.
        try:
            await state.refresh_from_persistence()
        except BaseException:
            await state.release_process_scan_lock()
            raise
        # The event is a pending-request marker. Clear it only after the lock
        # is acquired so a settings save that races an in-flight normal scan
        # is retried by the scheduler instead of being lost.
        if force_scan:
            state.force_scan.clear()
        requested_symbols = list(state.order) if symbols is None else symbols
        selected = []
        seen_symbols = set()
        for raw_symbol in requested_symbols:
            symbol = normalize_screener_symbol(raw_symbol)
            if symbol and symbol in state.profiles and symbol not in seen_symbols:
                selected.append(symbol)
                seen_symbols.add(symbol)
        user_token = set_user_config(state.config)
        context_token = active_evaluation_context.set("screener")
        cycle_error = False
        valid_scan_completed = False
        try:
            for raw_symbol in selected:
                symbol = normalize_screener_symbol(raw_symbol)
                settings = copy.deepcopy(state.profiles.get(symbol, {}))
                if not symbol or not settings or not bool(settings.get("enabled", not settings.get("paused", False))):
                    continue
                alert_source = derive_alert_source(settings)
                if alert_source == "none":
                    # Alerts paused for this ticker: no candles, no evaluation,
                    # no notification. The card shows the paused state from the
                    # saved profile; resuming starts a fresh baseline.
                    continue
                scan_profile_revision = state.profile_revision(symbol)
                timeframe = settings.get("timeframe") or state.page_settings.get("chart_timeframe") or state.config.DEFAULT_TIMEFRAME
                screening_mode = alert_source_policy(alert_source)
                evaluation_state = state.evaluation_state.setdefault(symbol, {})
                direction_watch = _screener_direction_watch_config(
                    settings,
                    evaluation_state,
                    state.notification_settings,
                )
                # Keep the effective global fallback beside the evaluation
                # ledger so cached/degraded card rebuilds show the same watch
                # mode without needing to re-read a delivery settings request.
                evaluation_state.update({
                    "direction_watch_mode": direction_watch["mode"],
                    "direction_watch_label": direction_watch["label"],
                    "direction_watch_directions": direction_watch["directions"],
                    "direction_watch_bullish_enabled": direction_watch["bullish_enabled"],
                    "direction_watch_bearish_enabled": direction_watch["bearish_enabled"],
                })
                scan_generation = evaluation_state.get("generation")
                scan_interval = _screener_scan_interval_seconds(settings)
                # Failure paths must also respect the configured cadence.
                evaluation_state["next_due_at"] = time.time() + scan_interval
                session_status = _screener_market_status(symbol, settings)
                if strategy_presets.is_schedule_active(settings):
                    # Session Rider: clock-only, before the closed-market gate; no candles, no indicators.
                    _screener_schedule_scan(state, symbol, settings, evaluation_state, scan_interval)
                    continue
                evaluation_state.pop("next_schedule_at", None)  # no longer a schedule profile
                probe_key = ""
                event_only_closed = (
                    not session_status["is_active_window"]
                    and screening_mode == "event"
                )
                # One narrow exception to the closed-session gate: a profile that has never had a valid scan (just
                # created, or its settings were just saved and the cached scan was cleared) gets ONE evaluation of the
                # last completed candles, so its card shows the indicator/strategy evidence from the last session
                # instead of staying blank until the market opens (possibly a whole weekend). It is analysis only and
                # seeds the baseline silently (no alert). Once a valid scan exists the gate below applies again.
                _cached_for_gate = state.latest_scans.get(symbol) or {}
                first_baseline_due = bool(evaluation_state.get("baseline_required")) and not (
                    _cached_for_gate.get("evaluation_status") == "valid"
                    and (_cached_for_gate.get("candle_meta") or {}).get("candle_key")
                )
                if not session_status["is_active_window"] and not event_only_closed and not first_baseline_due:
                    # Match Bots: do not fetch candles or run technical
                    # evaluation while the configured market session is shut.
                    # A force request must not bypass this gate. Event-only
                    # profiles are handled below from their cached scan and
                    # refreshed news state without touching indicators.
                    # Keep the latest price/analysis in place so reopening the
                    # session can restore it until a new candle is available.
                    next_due_at = time.time() + scan_interval
                    cached_scan = copy.deepcopy(
                        state.latest_scans.get(symbol) or _empty_screener_scan(symbol, settings)
                    )
                    scan_log = cached_scan.setdefault("scan_log", {})
                    is_options = bool(settings.get("options_trading") or settings.get("is_option"))
                    session_label = session_status["market_session_label"]
                    scan_log.update({
                        "summary": f"🛑 {session_label} Market Closed. Starts scanning 2m before open ({session_status['next_open_str']}).",
                        "detail": f"Starts scanning in {_format_duration_compact(session_status['seconds_to_pre_open'])} (2m prior to market opening).",
                        "status": "MARKET_CLOSED",
                        "decision": "HOLD",
                        "market_session": session_label,
                        "is_options_market_closed": is_options,
                        "is_stock_market_closed": not is_options,
                        "seconds_to_open": session_status["seconds_to_open"],
                        "seconds_to_pre_open": session_status["seconds_to_pre_open"],
                        "next_open_str": session_status["next_open_str"],
                        # Keep the configured scan interval separate from the
                        # countdown until the next market session opens.
                        "refresh_rate_sec": scan_interval,
                        "next_due_at": next_due_at,
                        "session_paused": True,
                        "direction_watch_mode": direction_watch["mode"],
                        "direction_watch_label": direction_watch["label"],
                        "watch_directions": direction_watch["directions"],
                    })
                    cached_scan["scan_log"] = scan_log
                    state.latest_scans[symbol] = cached_scan
                    evaluation_state["next_due_at"] = next_due_at
                    evaluation_state["last_data_check_at"] = datetime.now(pytz.UTC).isoformat()
                    evaluation_state["session_paused"] = True
                    continue
                evaluation_state["session_paused"] = bool(event_only_closed)
                previous_candle_key = str(evaluation_state.get("last_candle_key") or "")
                if (
                    not event_only_closed
                    and previous_candle_key
                    and not evaluation_state.get("baseline_required")
                    and not force_scan
                ):
                    # Poll only the newest bars after the first valid analysis.
                    # A repeated completed-candle key must not trigger another
                    # 1,200-bar fetch and full indicator calculation.
                    try:
                        state.health["candle_probe_count"] = int(state.health.get("candle_probe_count", 0) or 0) + 1
                        latest_meta = await _run_candle_probe(
                            state, symbol, timeframe, settings.get("market_session", "extended"),
                        )
                    except Exception as exc:
                        latest_meta = {"candle_key": None, "candle_status": "unavailable"}
                        print(f"[screener] Candle probe failed for {symbol}: {_safe_exception_type(exc)}")
                    if not _screener_profile_is_current(state, symbol, scan_profile_revision, scan_generation):
                        continue
                    probe_key = str(latest_meta.get("candle_key") or "")
                    next_due_at = time.time() + scan_interval
                    if probe_key:
                        evaluation_state["probe_failures"] = 0
                    if probe_key and not _screener_candle_is_usable(
                        latest_meta, timeframe, session_status["is_active_window"]
                    ):
                        cycle_error = True
                        cached_scan = copy.deepcopy(
                            state.latest_scans.get(symbol) or _empty_screener_scan(symbol, settings)
                        )
                        cached_scan["data_status"] = "degraded"
                        cached_scan["stale_reason"] = "The market-data provider returned a stale completed candle."
                        state.latest_scans[symbol] = cached_scan
                        evaluation_state["next_due_at"] = next_due_at
                        evaluation_state["last_data_check_at"] = datetime.now(pytz.UTC).isoformat()
                        state.health["last_error"] = (
                            f"Stale completed-candle probe for {symbol}; retained last valid analysis"
                        )
                        continue
                    if not probe_key or (
                        probe_key == previous_candle_key
                        and state.latest_scans.get(symbol, {}).get("data_status") in {None, "current"}
                    ):
                        cached_scan = state.latest_scans.get(symbol)
                        if isinstance(cached_scan, dict) and isinstance(cached_scan.get("scan_log"), dict):
                            cached_scan = copy.deepcopy(cached_scan)
                            if cached_scan["scan_log"].get("status") == "MARKET_CLOSED":
                                cached_scan["scan_log"] = _build_screener_scan_log(
                                    symbol,
                                    cached_scan,
                                    settings,
                                    evaluation_state,
                                )
                            cached_scan["scan_log"]["next_due_at"] = next_due_at
                            cached_scan["scan_log"]["refresh_rate_sec"] = scan_interval
                            cached_scan["scan_log"]["last_data_check_at"] = datetime.now(pytz.UTC).isoformat()
                            cached_scan["scan_log"]["no_new_completed_candle"] = bool(probe_key)
                            state.latest_scans[symbol] = cached_scan
                        evaluation_state["next_due_at"] = next_due_at
                        evaluation_state["last_data_check_at"] = datetime.now(pytz.UTC).isoformat()
                        if not probe_key:
                            failures = int(evaluation_state.get("probe_failures", 0) or 0) + 1
                            evaluation_state["probe_failures"] = failures
                            # Retry soon rather than after the whole scan interval (which can be 30 minutes).
                            evaluation_state["next_due_at"] = time.time() + min(scan_interval, _probe_retry_delay(failures))
                            if failures >= _PROBE_DEGRADE_AFTER:
                                cycle_error = True
                                state.health["last_error"] = (
                                    f"Completed-candle probe unavailable for {symbol} ({failures} attempts in a row); "
                                    "retained last valid analysis"
                                )
                                _mark_screener_scan_degraded(
                                    state, symbol, settings,
                                    "Completed-candle probe unavailable; showing the last valid analysis.",
                                )
                        else:
                            state.health["same_candle_skips"] = int(state.health.get("same_candle_skips", 0) or 0) + 1
                            print(f"[screener] Skip: {symbol} no new completed candle (candle_key={previous_candle_key})")
                        continue
                try:
                    if event_only_closed:
                        # Event-only monitoring remains live outside the
                        # configured market session, but it must not fetch
                        # candles or recalculate indicators. Reuse the last
                        # completed-candle result only as display context;
                        # the event leg below is refreshed independently.
                        scan = copy.deepcopy(
                            state.latest_scans.get(symbol)
                            or _empty_screener_scan(symbol, settings)
                        )
                        scan["event_only_market_closed"] = True
                    else:
                        state.health["full_evaluation_count"] = int(state.health.get("full_evaluation_count", 0) or 0) + 1
                        scan = await screener_manager.work_tracker.run(
                            (state.uid, symbol, "technical"),
                            screener_manager.evaluation_semaphore,
                            evaluate_screener_profile,
                            state.uid,
                            {**copy.deepcopy(settings), "symbol": symbol},
                            copy.deepcopy(state.config.contexts.get("screener", {})),
                            timeout=45.0,
                        )
                    if not isinstance(scan, dict):
                        scan = {"evaluation_status": "error", "reason": "Screener evaluation returned no result."}
                    # A settings save or profile disable may race an in-flight
                    # provider call. Never publish that obsolete result.
                    if not _screener_profile_is_current(state, symbol, scan_profile_revision, scan_generation):
                        print(f"[screener] Discarded obsolete result for {symbol}")
                        continue
                    evaluation_status = str(scan.get("evaluation_status") or "waiting").lower()
                    raw_candle_meta = scan.get("candle_meta") if isinstance(scan.get("candle_meta"), dict) else {}
                    candle_key = str(raw_candle_meta.get("candle_key") or "")
                    if (evaluation_status != "valid" or not candle_key) and not event_only_closed:
                        cycle_error = True
                        state.health["last_error"] = (
                            f"Screener data unavailable for {symbol}; retained last valid analysis"
                        )
                        _schedule_evaluation_retry(evaluation_state, scan_interval)
                        _mark_screener_scan_degraded(state, symbol, settings, "Evaluation did not produce valid completed-candle analysis.")
                        state.health["data_quality_rejections"] = int(state.health.get("data_quality_rejections", 0) or 0) + 1
                        continue
                    stale_candle = (
                        not event_only_closed
                        and not _screener_candle_is_usable(
                            raw_candle_meta, timeframe, session_status["is_active_window"]
                        )
                    )
                    if stale_candle:
                        cycle_error = True
                        state.health["last_error"] = (
                            f"Stale or invalid completed candle for {symbol}; retained last valid analysis"
                        )
                        _schedule_evaluation_retry(evaluation_state, scan_interval)
                        _mark_screener_scan_degraded(state, symbol, settings, "Completed candle is outside the freshness window.")
                        state.health["data_quality_rejections"] = int(state.health.get("data_quality_rejections", 0) or 0) + 1
                        continue
                    if probe_key:
                        probe_ts = _screener_candle_key_timestamp(probe_key)
                        evaluation_ts = _screener_candle_key_timestamp(candle_key)
                        if probe_ts > 0 and (evaluation_ts <= 0 or evaluation_ts < probe_ts):
                            cycle_error = True
                            cached_scan = copy.deepcopy(
                                state.latest_scans.get(symbol) or _empty_screener_scan(symbol, settings)
                            )
                            cached_scan["data_status"] = "degraded"
                            cached_scan["stale_reason"] = "Full analysis returned an older candle than the completed-candle probe."
                            _schedule_evaluation_retry(evaluation_state, scan_interval)
                            cached_scan.setdefault("scan_log", {})["next_due_at"] = evaluation_state.get("next_due_at")
                            state.latest_scans[symbol] = cached_scan
                            state.health["data_quality_rejections"] = int(state.health.get("data_quality_rejections", 0) or 0) + 1
                            state.health["last_error"] = (
                                f"Probe/full-analysis candle mismatch for {symbol}; retained last valid analysis"
                            )
                            continue
                    current_candle_ts = _screener_candle_key_timestamp(candle_key)
                    previous_observed_key = str(
                        evaluation_state.get("last_observed_candle_key")
                        or evaluation_state.get("last_candle_key")
                        or ""
                    )
                    # Only candles of the same timeframe are comparable (see candle_key_regressed). An event-only
                    # scan while the session is closed reuses the cached card and needs no candle at all, so a
                    # missing candle key there (a profile that was just saved) is not an out-of-order candle.
                    if not event_only_closed and (
                        current_candle_ts <= 0 or candle_key_regressed(previous_observed_key, candle_key)
                    ):
                        retry_count = int(evaluation_state.get("out_of_order_candle_retries", 0) or 0) + 1
                        evaluation_state["out_of_order_candle_retries"] = retry_count
                        # Probe and full-analysis requests can observe different
                        # provider snapshots. Retry briefly before surfacing a
                        # degraded card, so a transient LTCUSD race does not
                        # look like a persistent market-data failure.
                        if retry_count < 3:
                            evaluation_state["next_due_at"] = time.time() + 10
                            state.health["last_error"] = (
                                f"Out-of-order completed candle for {symbol}; retrying market-data fetch"
                            )
                            continue
                        cycle_error = True
                        # Keep retrying soon instead of waiting a whole scan interval: 30s, 60s, 120s, 240s,
                        # then every 5 minutes, with jitter. The counter is cleared by the next valid scan,
                        # so the degraded notice clears itself once the provider returns a current candle.
                        backoff = min(300.0, 30.0 * (2 ** min(max(retry_count - 3, 0), 4)))
                        evaluation_state["next_due_at"] = time.time() + backoff * (1.0 + random.uniform(0.0, 0.2))
                        cached_scan = copy.deepcopy(
                            state.latest_scans.get(symbol) or _empty_screener_scan(symbol, settings)
                        )
                        cached_scan["data_status"] = "degraded"
                        cached_scan["stale_reason"] = "An older completed candle arrived after a newer scan."
                        state.latest_scans[symbol] = cached_scan
                        state.health["last_error"] = (
                            f"Out-of-order completed candle for {symbol}; retained last valid analysis"
                        )
                        continue
                    evaluation_state.pop("out_of_order_candle_retries", None)
                    evaluation_state.pop("evaluation_failures", None)   # a valid analysis ends the quick-retry streak
                    formatted = _format_scan_for_ui(scan or {}, use_global_settings=False)
                    formatted["evaluation_status"] = evaluation_status
                    formatted["event_only_market_closed"] = event_only_closed
                    direction = scan.get("direction", "NEUTRAL")
                    formatted["candle_meta"] = scan.get("candle_meta", {})
                    formatted["atr_levels"] = scan.get("atr_levels", {})

                    scan_uses_preset = isinstance(scan.get("strategy_preset"), dict)
                    formatted["strategy_preset"] = copy.deepcopy(scan.get("strategy_preset")) if scan_uses_preset else None

                    # Anti-Chasing / Overextension Quality Guard (indicator setups only; a
                    # strategy preset owns its rules and is not re-filtered)
                    if direction in ("BULLISH", "BEARISH") and not scan_uses_preset:
                        blocker = _smart_reentry_blocker_text(settings, formatted, direction, None)
                        if blocker:
                            scan["direction"] = "NEUTRAL"
                            direction = "NEUTRAL"
                            existing_reason = formatted.get("reason", "")
                            formatted["reason"] = f"⚠️ {blocker}" + (f" | {existing_reason}" if existing_reason else "")
                            scan["reason"] = formatted["reason"]

                    # The Screener can now use the existing technical scan as
                    # one leg of an explicit policy. News is refreshed by the
                    # independent watcher and is read here only; this path
                    # never fetches news or places an order as a side effect.
                    technical_assessment = (
                        copy.deepcopy(scan.get("technical_qualification") or {
                            "eligible": False,
                            "direction": "NEUTRAL",
                            "status": "MARKET_CLOSED",
                            "reason": "Indicator evaluation is paused while the market is closed.",
                        })
                        if event_only_closed
                        else (assess_strategy_preset_technical(scan) if scan_uses_preset else assess_swing_technical(scan, settings))
                    )
                    event_assessment = assess_event_catalyst(
                        state.news_watch.get(symbol) or {},
                        max_age_days=settings.get("event_max_age_days", 10),
                        min_confidence=settings.get("event_min_confidence", 0.55),
                    )
                    strategy_qualification = combine_strategy_qualification(
                        screening_mode,
                        technical_assessment,
                        event_assessment,
                    )
                    raw_technical_direction = direction
                    direction = strategy_qualification.get("direction", "NEUTRAL")
                    scan.update({
                        "technical_direction": raw_technical_direction,
                        "screening_strategy_mode": screening_mode,
                        "alert_source": alert_source,
                        "technical_qualification": technical_assessment,
                        "event_qualification": event_assessment,
                        "strategy_qualification": strategy_qualification,
                        "direction": direction,
                        "direction_watch_mode": direction_watch["mode"],
                        "direction_watch_label": direction_watch["label"],
                        "watch_directions": direction_watch["directions"],
                        "action": {"BULLISH": "BUY", "BEARISH": "SELL"}.get(direction, "HOLD"),
                    })
                    if direction != raw_technical_direction:
                        combined_reason = str(strategy_qualification.get("reason") or "Strategy policy is waiting.")
                        original_reason = str(formatted.get("reason") or scan.get("reason") or "")
                        scan["reason"] = f"{combined_reason} | Indicator: {original_reason}" if original_reason else combined_reason
                        formatted["reason"] = scan["reason"]
                    formatted["screening_strategy_mode"] = screening_mode
                    formatted["alert_source"] = alert_source
                    formatted["technical_direction"] = raw_technical_direction
                    formatted["technical_qualification"] = technical_assessment
                    formatted["event_qualification"] = event_assessment
                    formatted["strategy_qualification"] = strategy_qualification
                    formatted["direction"] = direction
                    formatted["direction_watch_mode"] = direction_watch["mode"]
                    formatted["direction_watch_label"] = direction_watch["label"]
                    formatted["watch_directions"] = direction_watch["directions"]
                    formatted["action"] = {"BULLISH": "BUY", "BEARISH": "SELL"}.get(direction, "HOLD")
                    bullish = int(formatted.get("bullish_count", 0) or 0)
                    bearish = int(formatted.get("bearish_count", 0) or 0)
                    formatted["ticker"] = symbol
                    formatted["timeframe"] = timeframe
                    formatted["log_type"] = "Screener"
                    formatted["scanned_at"] = datetime.now(pytz.UTC).isoformat()
                    # Structural exits are evaluated against the current
                    # virtual Screener cycle before committing this candle.
                    # If an ATR/PCT/level trigger fires while the indicator
                    # direction is still bullish, record a bearish transition
                    # so Alerts and Signal Activity cannot miss the exit.
                    new_exit_event = {}
                    exit_event = {}
                    sell_mode = str(
                        settings.get("sell_mode")
                        or settings.get("options_exit_strategy")
                        or "indicator"
                    ).strip().lower()
                    current_candle_key = str((formatted.get("candle_meta") or {}).get("candle_key") or "")
                    if direction == "BULLISH" and not bool(evaluation_state.get("virtual_position")):
                        # Freeze structural levels at the candle that starts
                        # the virtual cycle. Later quote checks may move only
                        # the live price and an explicitly configured trailing
                        # stop; they do not recompute the target from a new
                        # candle or a changed profile.
                        entry_price = _entry_forecast_float(formatted.get("price_raw"), 0.0)
                        entry_state = copy.deepcopy(evaluation_state)
                        entry_state.update({
                            "virtual_position": True,
                            "virtual_entry_price": entry_price,
                            "virtual_entry_atr": _entry_forecast_float(formatted.get("atr"), 0.0),
                            "virtual_entry_candle_key": current_candle_key,
                            "virtual_exit_mode": sell_mode,
                            "virtual_exit_settings": copy.deepcopy(settings),
                            "virtual_peak_price": entry_price,
                        })
                        snapshot_levels = _screener_exit_level_status(
                            settings,
                            formatted,
                            entry_state,
                            sell_mode,
                            price_override=entry_price,
                        )
                        scan["screener_exit_snapshot"] = {
                            "mode": sell_mode,
                            "stop": snapshot_levels.get("stop"),
                            "target": snapshot_levels.get("target"),
                            "atr": entry_state.get("virtual_entry_atr"),
                            "available": bool(snapshot_levels.get("available")),
                        }
                    stored_exit_key = str(evaluation_state.get("last_exit_event_candle_key") or "")
                    current_candle_key = str((formatted.get("candle_meta") or {}).get("candle_key") or "")
                    stored_exit_event = evaluation_state.get("last_exit_event")
                    if (
                        stored_exit_key
                        and stored_exit_key == current_candle_key
                        and isinstance(stored_exit_event, dict)
                        and stored_exit_event.get("triggered")
                    ):
                        exit_event = copy.deepcopy(stored_exit_event)
                    if evaluation_state.get("virtual_position") and sell_mode not in ("indicator", "strategy_preset"):
                        candidate_exit = _screener_exit_level_status(
                            settings,
                            formatted,
                            evaluation_state,
                            sell_mode,
                        )
                        if candidate_exit.get("triggered"):
                            new_exit_event = candidate_exit
                            exit_event = candidate_exit
                    if settings.get("options_trading"):
                        try:
                            exp_days = max(0, min(365, int(settings.get("options_expiry_days", 3))))
                        except (TypeError, ValueError):
                            exp_days = 3
                        strike_type = settings.get("options_strike_type", "ATM")
                        opt_side = "CALL" if direction == "BULLISH" else ("PUT" if direction == "BEARISH" else None)
                        formatted["options_play"] = {
                            "side": opt_side,
                            "expiry_days": exp_days,
                            "strike_type": strike_type,
                            "summary": f"{symbol} ~{exp_days}D {strike_type} {opt_side}" if opt_side else f"~{exp_days}D {strike_type} (Pending Setup)",
                        }
                    evaluation_for_commit = scan
                    if new_exit_event:
                        evaluation_for_commit = copy.deepcopy(scan)
                        evaluation_for_commit["direction"] = "BEARISH"
                        evaluation_for_commit["action"] = "SELL"
                        evaluation_for_commit["atr_levels"] = exit_event
                        evaluation_for_commit["screener_exit_event"] = True
                        evaluation_for_commit["reason"] = str(
                            exit_event.get("text")
                            or evaluation_for_commit.get("reason")
                            or "Configured structural exit reached."
                        )
                    # Keep the lifecycle/price state from before this
                    # transition for the historical alert explanation. The
                    # directional alert watch itself is independent; this
                    # snapshot only preserves any analysis-only structural
                    # exit context for the alert that just fired.
                    alert_snapshot_state = copy.deepcopy(evaluation_state)
                    alert_event_direction = "BEARISH" if exit_event else direction
                    alert_snapshot_state["virtual_position"] = alert_event_direction == "BEARISH"
                    alert_snapshot_state["last_actionable_direction"] = (
                        "BULLISH" if alert_event_direction == "BEARISH" else ""
                    )
                    notification = state.commit_evaluation(symbol, evaluation_for_commit)
                    # commit_evaluation may rebuild a state ledger after a
                    # profile revision change. Re-attach the effective watch
                    # metadata so the next cached scan still reflects the
                    # user's bullish/bearish switches.
                    evaluation_state.update({
                        "direction_watch_mode": direction_watch["mode"],
                        "direction_watch_label": direction_watch["label"],
                        "direction_watch_directions": direction_watch["directions"],
                        "direction_watch_bullish_enabled": direction_watch["bullish_enabled"],
                        "direction_watch_bearish_enabled": direction_watch["bearish_enabled"],
                    })
                    event_direction = alert_event_direction
                    print(
                        f"[screener] Scan: {symbol} direction={direction} "
                        f"candle_close_ts={(scan.get('candle_meta') or {}).get('candle_close_ts')} "
                        f"alert={'created' if notification else 'none'}"
                        f" event_direction={event_direction}"
                    )
                    evaluation_state["next_due_at"] = time.time() + scan_interval
                    alert_scan_log = _build_screener_scan_log(
                        symbol,
                        formatted,
                        settings,
                        alert_snapshot_state,
                        alert_created=bool(notification),
                        exit_event=exit_event,
                    )
                    formatted["scan_log"] = _build_screener_scan_log(
                        symbol,
                        formatted,
                        settings,
                        evaluation_state,
                        alert_created=bool(notification),
                        exit_event=exit_event,
                    )
                    # Preserve the exact scan-log snapshot that accompanied a
                    # newly created alert. Signal Activity is historical, so
                    # it must remain explainable even after the live card has
                    # moved to a later candle.
                    if notification:
                        notification_reason = str(
                            exit_event.get("text")
                            if exit_event
                            else strategy_qualification.get("reason")
                            or evaluation_for_commit.get("reason")
                            or formatted.get("reason")
                            or "Signal transition detected."
                        )
                        notification.update({
                            "event_direction": event_direction,
                            "timeframe": timeframe,
                            # Keep the delivered alert's reason tied to the
                            # selected Screener policy. The raw technical
                            # explanation remains available separately.
                            "reason": notification_reason,
                            "scan_reason": str(formatted.get("reason") or ""),
                            "screening_strategy_mode": screening_mode,
                            "screening_strategy_label": screening_strategy_label(screening_mode),
                            "alert_source": alert_source,
                            "alert_source_label": alert_source_label(alert_source),
                            "strategy_qualification": copy.deepcopy(strategy_qualification),
                            "event_qualification": copy.deepcopy(event_assessment),
                            "alert_basis": "technical_exit_monitor" if exit_event else "screening_policy",
                            "scan_log": copy.deepcopy(alert_scan_log),
                            "candle_meta": copy.deepcopy(formatted.get("candle_meta") or {}),
                            "atr_levels": copy.deepcopy(
                                evaluation_for_commit.get("atr_levels")
                                or formatted.get("atr_levels")
                                or {}
                            ),
                        })
                    # A force scan may run outside the selected stock session
                    # to refresh settings immediately. Keep the market-closed
                    # banner and stopped regular timer visible while exposing
                    # the freshly evaluated result underneath that state.
                    if force_scan and not session_status["is_active_window"]:
                        is_options_market = bool(settings.get("options_trading") or settings.get("is_option"))
                        formatted["scan_log"].update({
                            "market_session": session_status["market_session_label"],
                            "is_options_market_closed": is_options_market,
                            "is_stock_market_closed": not is_options_market,
                            "seconds_to_open": session_status["seconds_to_open"],
                            "seconds_to_pre_open": session_status["seconds_to_pre_open"],
                            "next_open_str": session_status["next_open_str"],
                            "session_paused": True,
                            "force_scan_completed": True,
                        })
                    formatted["data_status"] = "current"
                    state.latest_scans[symbol] = formatted
                    valid_scan_completed = True
                    if notification:
                        state.health["email_queued"] = sum(item.get("status") == "queued" for item in state.email_outbox)
                        state.health["sms_queued"] = sum(item.get("status") == "queued" for item in state.sms_outbox)
                    state.activity.insert(0, {
                        "time": formatted["scanned_at"],
                        "ticker": symbol,
                        "action": event_direction,
                        "price": formatted.get("price", ""),
                        "reason": evaluation_for_commit.get("reason", formatted.get("reason", "")),
                        "scan_reason": str(formatted.get("reason") or ""),
                        "screening_strategy_mode": screening_mode,
                        "screening_strategy_label": screening_strategy_label(screening_mode),
                        "screening_strategy_logic": screening_strategy_logic(screening_mode),
                        "alert_source": alert_source,
                        "alert_source_label": alert_source_label(alert_source),
                        "strategy_qualification": copy.deepcopy(strategy_qualification),
                        "event_qualification": copy.deepcopy(event_assessment),
                        "bullish_count": bullish,
                        "bearish_count": bearish,
                        "total_signals": formatted.get("total_signals", 0),
                        "timeframe": timeframe,
                        "log_type": "Screener",
                        "scan_log_status": formatted["scan_log"].get("status"),
                        "scan_log_summary": formatted["scan_log"].get("summary"),
                        "target_direction": formatted["scan_log"].get("target_direction"),
                        "virtual_position": formatted["scan_log"].get("virtual_position", False),
                    })
                    state.activity = state.activity[:100]
                except asyncio.TimeoutError:
                    if not _screener_profile_is_current(state, symbol, scan_profile_revision, scan_generation):
                        continue
                    cycle_error = True
                    print(f"[screener] Scan timed out for {symbol}")
                    state.health["last_error"] = f"Scan timed out for {symbol}"
                    _schedule_evaluation_retry(evaluation_state, scan_interval)
                    _mark_screener_scan_degraded(state, symbol, settings, "Analysis timed out; showing the last valid scan until a retry succeeds.")
                except Exception as exc:
                    if not _screener_profile_is_current(state, symbol, scan_profile_revision, scan_generation):
                        continue
                    cycle_error = True
                    print(f"[screener] Scan failed for {symbol}: {_safe_exception_type(exc)}")
                    state.health["last_error"] = _safe_exception_type(exc)
                    _schedule_evaluation_retry(evaluation_state, scan_interval, busy=isinstance(exc, ScreenerWorkBusy))
                    _mark_screener_scan_degraded(state, symbol, settings, "Analysis is unavailable; showing the last valid scan until a retry succeeds.")
            state.health.update({
                "status": "degraded" if cycle_error else "healthy",
                "last_scan_at": time.time(),
                "last_success_at": time.time() if valid_scan_completed else state.health.get("last_success_at"),
                "consecutive_failures": 0 if not cycle_error else int(state.health.get("consecutive_failures", 0) or 0) + 1,
            })
            if not cycle_error and valid_scan_completed:
                state.health["last_error"] = ""
            await state.save()
        finally:
            active_evaluation_context.reset(context_token)
            _active_user_config.reset(user_token)
            await state.release_process_scan_lock()


def _screener_price_watch_interval_seconds(settings: dict) -> int:
    """Return a bounded quote-watch cadence independent of candle cadence."""
    try:
        return max(2, min(60, int((settings or {}).get("price_watch_interval_seconds", 5))))
    except (TypeError, ValueError):
        return 5


def _screener_quote_age_label(value: Any) -> str:
    """Format provider/cache age without letting malformed metadata break a scan."""
    try:
        age = float(value)
        return f"{age:.1f}s"
    except (TypeError, ValueError, OverflowError):
        return "unavailable"


_SCREENER_PRICE_EXIT_MODES = {
    "atr",
    "sltp",
    "hybrid",
    "hybrid_atr",
    "percentage",
    "hybrid_pct",
    "pivot_points",
    "hybrid_pivot",
    "high_low",
    "hybrid_high_low",
    "swing_points",
    "hybrid_swing",
    "custom_sltp",
    "custom_underlying_sltp",
}
_SCREENER_PRICE_ENTRY_MODES = {"custom_price"}


def _screener_price_event_key(state: dict, symbol: str, mode: str, level_name: str) -> str:
    """Create a stable event identity for one virtual entry and level."""
    payload = "|".join([
        str(state.get("generation") or "1"),
        normalize_screener_symbol(symbol),
        str(state.get("virtual_entry_candle_key") or state.get("last_candle_key") or ""),
        str(mode or ""),
        str(level_name or ""),
    ])
    return hashlib.sha256(payload.encode()).hexdigest()[:32]


def _screener_price_entry_event_key(
    state: dict,
    symbol: str,
    mode: str,
    direction: str,
    target_price: float,
) -> str:
    """Create a stable identity for one custom-price entry opportunity."""
    payload = "|".join([
        str(state.get("generation") or "1"),
        normalize_screener_symbol(symbol),
        str(state.get("last_observed_candle_key") or state.get("last_candle_key") or "live"),
        str(mode or ""),
        str(direction or ""),
        f"{float(target_price):.10f}",
    ])
    return hashlib.sha256(payload.encode()).hexdigest()[:32]


_SCREENER_PRICE_WATCH_LOG_INTERVAL_SECONDS = 300.0
_screener_price_watch_last_logged: dict = {}


def _log_screener_price_watch_failure(uid: str, symbol: str, failure: str) -> None:
    """One WARNING per user/symbol/reason every few minutes, so a stuck quote is explainable without flooding the log."""
    key = (uid, symbol, failure)
    now = time.monotonic()
    last = _screener_price_watch_last_logged.get(key)
    if last is not None and now - last < _SCREENER_PRICE_WATCH_LOG_INTERVAL_SECONDS:
        return
    if len(_screener_price_watch_last_logged) > 500:
        _screener_price_watch_last_logged.clear()
    _screener_price_watch_last_logged[key] = now
    print(f"[screener] WARNING Price watch quote failed for {symbol}: {failure} (no entry/exit is evaluated from this tick)")


async def _watch_screener_price_levels_state(
    state: ScreenerUserState,
    symbols: Optional[list] = None,
    force_scan: bool = False,
) -> None:
    """Check virtual exit levels from fresh quotes between candle scans.

    This watcher is analysis-only. It never submits orders and never changes
    the completed-candle direction. A valid quote may close the virtual cycle
    through ``commit_price_event`` when a configured stop/target is reached.
    """
    if state.scan_lock.locked() or state.price_watch_lock.locked():
        state.health["price_watch_skips"] = int(state.health.get("price_watch_skips", 0) or 0) + 1
        return
    async with state.price_watch_lock:
        if not await state.try_acquire_process_scan_lock():
            state.health["price_watch_skips"] = int(state.health.get("price_watch_skips", 0) or 0) + 1
            return
        context_token = active_evaluation_context.set("screener")
        changed = False
        try:
            await state.refresh_from_persistence()
            selected = symbols or list(state.order)
            now_ts = time.time()
            now_iso = datetime.now(pytz.UTC).isoformat()
            for raw_symbol in selected:
                symbol = normalize_screener_symbol(raw_symbol)
                settings = copy.deepcopy(state.profiles.get(symbol, {}))
                if not symbol or not settings or not bool(settings.get("enabled", not settings.get("paused", False))):
                    continue
                evaluation_state = state.evaluation_state.setdefault(symbol, {})
                interval = _screener_price_watch_interval_seconds(settings)
                if now_ts < float(evaluation_state.get("next_price_watch_at", 0) or 0):
                    continue
                evaluation_state["next_price_watch_at"] = now_ts + interval
                evaluation_state["last_price_watch_at"] = now_ts
                state.health["price_probe_count"] = int(state.health.get("price_probe_count", 0) or 0) + 1
                state.health["last_price_watch_at"] = now_ts
                changed = True

                sell_mode = str(
                    settings.get("sell_mode")
                    or settings.get("options_exit_strategy")
                    or "indicator"
                ).strip().lower()
                buy_mode = str(settings.get("buy_mode") or "indicator").strip().lower()
                watch_custom_entry = (
                    buy_mode in _SCREENER_PRICE_ENTRY_MODES
                    and not bool(evaluation_state.get("virtual_position"))
                )
                watch_price_exit = (
                    bool(evaluation_state.get("virtual_position"))
                    and sell_mode in _SCREENER_PRICE_EXIT_MODES
                )
                session_status = _screener_market_status(symbol, settings)
                if not watch_custom_entry and not watch_price_exit:
                    evaluation_state["last_price_watch"] = {
                        "status": "IDLE",
                        "checked_at": now_iso,
                        "reason": "No custom-price entry or active price-based exit to monitor.",
                        "interval_seconds": interval,
                    }
                    latest_scan = copy.deepcopy(state.latest_scans.get(symbol) or {})
                    latest_scan["price_watch"] = copy.deepcopy(evaluation_state["last_price_watch"])
                    state.latest_scans[symbol] = latest_scan
                    continue
                if not session_status.get("is_active_window") and not force_scan:
                    evaluation_state["last_price_watch"] = {
                        "status": "MARKET_CLOSED",
                        "checked_at": now_iso,
                        "reason": "Quote monitoring is outside the configured market session.",
                        "interval_seconds": interval,
                        "next_open_str": session_status.get("next_open_str"),
                    }
                    latest_scan = copy.deepcopy(state.latest_scans.get(symbol) or {})
                    latest_scan["price_watch"] = copy.deepcopy(evaluation_state["last_price_watch"])
                    state.latest_scans[symbol] = latest_scan
                    continue

                revision = state.profile_revision(symbol)
                generation = evaluation_state.get("generation")
                try:
                    quote = await screener_manager.work_tracker.run(
                        (state.uid, symbol, "quote"),
                        screener_manager.evaluation_semaphore,
                        _get_cached_quote_detail,
                        SimpleNamespace(uid=state.uid, broker=None), symbol, 0, 30,
                        timeout=8.0,
                    )
                except Exception as exc:
                    # Say WHY (queue wait, timeout, a still-running earlier lookup, provider error) instead of a bare
                    # "unavailable"; the exception type is safe to show and log, the message may not be.
                    quote_failure = _safe_exception_type(exc) or "error"
                    quote = {"stale": True, "source": "unavailable", "failure": quote_failure}
                    _log_screener_price_watch_failure(state.uid, symbol, quote_failure)
                if not _screener_profile_is_current(state, symbol, revision, generation):
                    continue
                quote_price = _entry_forecast_float(quote.get("price"), 0.0)
                quote_age = quote.get("cache_age")
                quote_stale = bool(quote.get("stale"))
                quote_source = str(quote.get("source") or "unavailable")
                if quote_stale or quote_price <= 0 or not math.isfinite(quote_price):
                    state.health["price_data_rejections"] = int(state.health.get("price_data_rejections", 0) or 0) + 1
                    evaluation_state["last_price_watch"] = {
                        "status": "STALE" if quote_stale else "UNAVAILABLE",
                        "checked_at": now_iso,
                        "source": quote_source,
                        "quote_age_seconds": quote_age,
                        "interval_seconds": interval,
                        "failure": quote.get("failure") or ("no_quote" if quote_source == "unavailable" else "stale_quote"),
                    }
                    latest_scan = copy.deepcopy(state.latest_scans.get(symbol) or {})
                    latest_scan["price_watch"] = copy.deepcopy(evaluation_state["last_price_watch"])
                    state.latest_scans[symbol] = latest_scan
                    continue

                latest_scan = copy.deepcopy(state.latest_scans.get(symbol) or {})
                if watch_custom_entry:
                    custom_target = _entry_forecast_float(settings.get("custom_buy_price"), 0.0)
                    is_put_entry = bool(settings.get("options_trading")) and normalize_options_type(
                        settings.get("options_type"), default="calls_only"
                    ) == "puts_only"
                    entry_direction = "BEARISH" if is_put_entry else "BULLISH"
                    entry_target_valid = custom_target > 0 and math.isfinite(custom_target)
                    entry_event_key = (
                        _screener_price_entry_event_key(
                            evaluation_state, symbol, buy_mode, entry_direction, custom_target
                        )
                        if entry_target_valid else ""
                    )
                    if entry_event_key and evaluation_state.get("last_price_entry_key") == entry_event_key:
                        price_watch = {
                            "status": "ENTRY_RECORDED",
                            "checked_at": now_iso,
                            "price": quote_price,
                            "source": quote_source,
                            "quote_age_seconds": quote_age,
                            "interval_seconds": interval,
                            "mode": buy_mode,
                            "entry_target": custom_target,
                            "text": "Custom price entry already recorded for this completed-candle opportunity.",
                        }
                        evaluation_state["last_price_watch"] = price_watch
                        latest_scan["price_watch"] = copy.deepcopy(price_watch)
                        state.latest_scans[symbol] = latest_scan
                        continue
                    entry_triggered = entry_target_valid and (
                        quote_price >= custom_target if is_put_entry else quote_price <= custom_target
                    )
                    if not entry_target_valid:
                        price_watch = {
                            "status": "UNAVAILABLE",
                            "checked_at": now_iso,
                            "price": quote_price,
                            "source": quote_source,
                            "quote_age_seconds": quote_age,
                            "interval_seconds": interval,
                            "mode": buy_mode,
                            "entry_target": None,
                            "text": "Custom price entry is unavailable until a positive target price is configured.",
                        }
                    elif not entry_triggered:
                        relation = "above" if is_put_entry else "below"
                        price_watch = {
                            "status": "WAITING_FOR_ENTRY",
                            "checked_at": now_iso,
                            "price": quote_price,
                            "source": quote_source,
                            "quote_age_seconds": quote_age,
                            "interval_seconds": interval,
                            "mode": buy_mode,
                            "entry_target": custom_target,
                            "text": f"Custom {entry_direction.title()} entry waits for price {relation} {custom_target:.6f}.",
                        }
                    else:
                        entry_state = copy.deepcopy(evaluation_state)
                        entry_state.update({
                            "virtual_position": entry_direction == "BULLISH",
                            "virtual_entry_price": quote_price,
                            "virtual_entry_atr": _entry_forecast_float(latest_scan.get("atr"), 0.0),
                            "virtual_entry_candle_key": str(
                                evaluation_state.get("last_observed_candle_key")
                                or evaluation_state.get("last_candle_key")
                                or f"PRICE:{entry_event_key}"
                            ),
                            "virtual_exit_mode": sell_mode,
                            "virtual_exit_settings": copy.deepcopy(settings),
                            "virtual_peak_price": quote_price,
                        })
                        entry_exit_snapshot = {}
                        if entry_direction == "BULLISH" and sell_mode in _SCREENER_PRICE_EXIT_MODES:
                            entry_exit_status = _screener_exit_level_status(
                                settings,
                                latest_scan,
                                entry_state,
                                sell_mode,
                                price_override=quote_price,
                            )
                            entry_exit_snapshot = {
                                "mode": sell_mode,
                                "stop": entry_exit_status.get("stop"),
                                "target": entry_exit_status.get("target"),
                                "atr": entry_state.get("virtual_entry_atr"),
                                "available": bool(entry_exit_status.get("available")),
                            }
                        event = {
                            "price_event_key": entry_event_key,
                            "event_type": "PRICE_ENTRY",
                            "event_direction": entry_direction,
                            "level_name": "target",
                            "trigger_price": quote_price,
                            "target_price": custom_target,
                            "mode": buy_mode,
                            "strategy_label": "Custom Price",
                            "atr": entry_state.get("virtual_entry_atr"),
                            "atr_levels": entry_exit_snapshot,
                            "exit_snapshot": entry_exit_snapshot,
                            "price_source": quote_source,
                            "quote_age_seconds": quote_age,
                            "observed_at": now_iso,
                            "text": (
                                f"Custom {entry_direction.title()} price entry reached at {quote_price:.6f} "
                                f"(target {custom_target:.6f}). Live price trigger; source {quote_source}, "
                                f"quote age {_screener_quote_age_label(quote_age)}."
                            ),
                        }
                        state.health["price_entry_trigger_count"] = int(
                            state.health.get("price_entry_trigger_count", 0) or 0
                        ) + 1
                        snapshot_state = copy.deepcopy(evaluation_state)
                        notification = state.commit_price_entry(symbol, event, now_ts=now_ts)
                        price_watch = {
                            "status": "ENTRY_TRIGGERED",
                            "checked_at": now_iso,
                            "price": quote_price,
                            "source": quote_source,
                            "quote_age_seconds": quote_age,
                            "interval_seconds": interval,
                            "mode": buy_mode,
                            "entry_target": custom_target,
                            "text": event["text"],
                        }
                        evaluation_state["last_price_watch"] = price_watch
                        latest_scan["price_watch"] = copy.deepcopy(price_watch)
                        latest_scan["live_price"] = quote_price
                        latest_scan["live_price_source"] = quote_source
                        latest_scan["live_price_checked_at"] = now_iso
                        latest_scan["price_event"] = copy.deepcopy(event)
                        latest_scan["scan_log"] = _build_screener_scan_log(
                            symbol,
                            latest_scan,
                            settings,
                            snapshot_state,
                            alert_created=bool(notification),
                            price_event=event,
                        )
                        state.latest_scans[symbol] = latest_scan
                        state.activity.insert(0, {
                            "time": now_iso,
                            "ticker": symbol,
                            "action": entry_direction,
                            "event_direction": entry_direction,
                            "event_type": "PRICE_ENTRY",
                            "price": quote_price,
                            "reason": event.get("text", "Custom price entry reached."),
                            "bullish_count": 0,
                            "bearish_count": 0,
                            "total_signals": 0,
                            "timeframe": latest_scan.get("timeframe") or settings.get("timeframe"),
                            "log_type": "Screener",
                            "scan_log_status": latest_scan["scan_log"].get("status"),
                            "scan_log_summary": latest_scan["scan_log"].get("summary"),
                            "price_event_key": entry_event_key,
                        })
                        state.activity = state.activity[:100]
                        changed = True
                        continue
                    evaluation_state["last_price_watch"] = price_watch
                    latest_scan["price_watch"] = copy.deepcopy(price_watch)
                    state.latest_scans[symbol] = latest_scan
                    continue

                exit_status = _screener_exit_level_status(
                    settings,
                    latest_scan,
                    evaluation_state,
                    sell_mode,
                    price_override=quote_price,
                )
                price_watch = {
                    "status": "TRIGGERED" if exit_status.get("triggered") else "WATCHING",
                    "checked_at": now_iso,
                    "price": quote_price,
                    "source": quote_source,
                    "quote_age_seconds": quote_age,
                    "interval_seconds": interval,
                    "mode": sell_mode,
                    "levels": {
                        "stop": exit_status.get("stop"),
                        "target": exit_status.get("target"),
                    },
                    "text": exit_status.get("text", ""),
                }
                evaluation_state["last_price_watch"] = price_watch

                if not exit_status.get("triggered"):
                    latest_scan["price_watch"] = copy.deepcopy(price_watch)
                    state.latest_scans[symbol] = latest_scan
                    continue
                level_name = "stop" if quote_price <= _entry_forecast_float(exit_status.get("stop"), 0.0) else "target"
                event_key = _screener_price_event_key(evaluation_state, symbol, sell_mode, level_name)
                event = {
                    **exit_status,
                    "price_event_key": event_key,
                    "event_type": "PRICE_EXIT",
                    "level_name": level_name,
                    "trigger_price": quote_price,
                    "mode": sell_mode,
                    "strategy_label": {
                        "atr": "ATR",
                        "sltp": "ATR",
                        "hybrid": "ATR",
                        "hybrid_atr": "ATR",
                        "percentage": "Percentage",
                        "hybrid_pct": "Percentage",
                        "pivot_points": "Pivot Points",
                        "hybrid_pivot": "Pivot Points",
                        "high_low": "Prior Day High/Low",
                        "hybrid_high_low": "Prior Day High/Low",
                        "swing_points": "Swing Structure",
                        "hybrid_swing": "Swing Structure",
                        "custom_sltp": "Custom SL/TP",
                        "custom_underlying_sltp": "Custom Underlying SL/TP",
                    }.get(sell_mode, sell_mode),
                    "atr": evaluation_state.get("virtual_entry_atr") or latest_scan.get("atr"),
                    "atr_levels": {
                        "stop": exit_status.get("stop"),
                        "target": exit_status.get("target"),
                        "active_mode": sell_mode,
                    },
                    "price_source": quote_source,
                    "quote_age_seconds": quote_age,
                    "observed_at": now_iso,
                    "text": f"{exit_status.get('text', 'Configured price level reached.')} Live price trigger; source {quote_source}, quote age {_screener_quote_age_label(quote_age)}.",
                }
                state.health["price_trigger_count"] = int(state.health.get("price_trigger_count", 0) or 0) + 1
                snapshot_state = copy.deepcopy(evaluation_state)
                notification = state.commit_price_event(symbol, event, now_ts=now_ts)
                latest_scan["price_watch"] = price_watch
                latest_scan["live_price"] = quote_price
                latest_scan["live_price_source"] = quote_source
                latest_scan["live_price_checked_at"] = now_iso
                latest_scan["price_event"] = copy.deepcopy(event)
                latest_scan["scan_log"] = _build_screener_scan_log(
                    symbol,
                    latest_scan,
                    settings,
                    snapshot_state,
                    alert_created=bool(notification),
                    exit_event=event,
                )
                state.latest_scans[symbol] = latest_scan
                state.activity.insert(0, {
                    "time": now_iso,
                    "ticker": symbol,
                    "action": "BEARISH",
                    "event_direction": "BEARISH",
                    "event_type": "PRICE_EXIT",
                    "price": quote_price,
                    "reason": event.get("text", "Configured price level reached."),
                    "bullish_count": 0,
                    "bearish_count": 0,
                    "total_signals": 0,
                    "timeframe": latest_scan.get("timeframe") or settings.get("timeframe"),
                    "log_type": "Screener",
                    "scan_log_status": latest_scan["scan_log"].get("status"),
                    "scan_log_summary": latest_scan["scan_log"].get("summary"),
                    "price_event_key": event_key,
                })
                state.activity = state.activity[:100]
                changed = True
            if changed:
                await state.save()
        finally:
            active_evaluation_context.reset(context_token)
            await state.release_process_scan_lock()


async def _watch_screener_news_state(state: ScreenerUserState, symbols: list[str] = None) -> None:
    """Refresh ticker news independently from completed-candle signal scans."""
    if state.news_lock.locked():
        return
    async with state.news_lock:
        selected = symbols or list(state.order)
        set_user_config(state.config)
        context_token = active_evaluation_context.set("screener")

        async def _refresh_symbol(symbol: str):
            settings = state.profiles.get(symbol, {})
            previous = copy.deepcopy(state.news_watch.get(symbol) or {})
            refresh_seconds = _screener_news_refresh_interval_seconds(settings)
            model = settings.get("ai_model") or getattr(state.config, "AI_INDICATOR_MODEL", None)
            try:
                async with screener_manager.news_semaphore:
                    payload = await asyncio.wait_for(
                        asyncio.to_thread(
                            build_screener_news_watch,
                            symbol,
                            previous,
                            model,
                            refresh_seconds,
                        ),
                        timeout=50.0,
                    )
                return symbol, payload
            except Exception as exc:
                now_ts = time.time()
                failures = int(previous.get("failure_count") or 0) + 1
                retry_seconds = min(900, 60 * (2 ** min(failures - 1, 4)))
                payload = dict(previous)
                payload.update({
                    "enabled": True,
                    "status": "stale" if previous.get("initialized") else "unavailable",
                    "ticker": symbol,
                    "checked_at": datetime.now(pytz.UTC).isoformat(),
                    "failure_count": failures,
                    "new_count": 0,
                    "error": f"{_safe_exception_type(exc)}",
                    "next_check_at": now_ts + retry_seconds,
                    "refresh_interval_seconds": refresh_seconds,
                })
                print(f"[screener] News watch failed for {symbol}: {_safe_exception_type(exc)}")
                return symbol, payload

        try:
            active_symbols = [
                normalize_screener_symbol(raw_symbol)
                for raw_symbol in selected
                if normalize_screener_symbol(raw_symbol)
                and bool(state.profiles.get(normalize_screener_symbol(raw_symbol), {}).get("enabled", True))
            ]
            if not active_symbols:
                return
            event_scan_symbols = []
            results = await asyncio.gather(
                *(_refresh_symbol(symbol) for symbol in active_symbols),
                return_exceptions=False,
            )
            for symbol, payload in results:
                payload["refresh_interval_seconds"] = _screener_news_refresh_interval_seconds(
                    state.profiles.get(symbol, {})
                )
                previous = state.news_watch.get(symbol) or {}
                feed_changed = bool(
                    previous.get("initialized")
                    and payload.get("initialized")
                    and payload.get("feed_fingerprint")
                    and payload.get("feed_fingerprint") != previous.get("feed_fingerprint")
                )
                state.news_watch[symbol] = payload
                # Only the Event-Driven source reacts to a new headline; the
                # other sources show news as context and never re-scan on it.
                news_alert_source = derive_alert_source(state.profiles.get(symbol, {}))
                if feed_changed and int(payload.get("new_count") or 0) > 0 and news_alert_source == "event":
                    # The news worker owns headline freshness.  The follow-up
                    # refresh lets event-only profiles update immediately;
                    # closed-market technical/combined/discovery profiles are
                    # still stopped by _scan_screener_state's session gate.
                    event_scan_symbols.append(symbol)
            await state.save()
            for symbol in event_scan_symbols:
                asyncio.create_task(_scan_screener_state(state, [symbol], force_scan=True))
        finally:
            active_evaluation_context.reset(context_token)


def _screener_news_refresh_due(state: ScreenerUserState, symbol: str, now_ts: float) -> bool:
    """Return whether a ticker needs an initial or schema-complete news refresh."""
    watch = state.news_watch.get(symbol) or {}
    try:
        next_check_at = float(watch.get("next_check_at", 0) or 0)
    except (TypeError, ValueError, OverflowError):
        next_check_at = 0.0
    if now_ts >= next_check_at:
        return True
    # Legacy records can be marked current while lacking the AI field needed by
    # the Event leg. Retry those once the provider backoff is not active;
    # provider-empty, unavailable, and stale records respect next_check_at.
    if str(watch.get("status") or "").lower() == "current":
        return not bool(watch.get("initialized") and watch.get("ai_analysis"))
    return False


async def _watch_screener_earnings_state(state: ScreenerUserState, symbols: list[str] = None) -> None:
    """Refresh structured earnings risk independently from technical scans."""
    if state.earnings_lock.locked():
        return
    async with state.earnings_lock:
        selected = symbols or list(state.order)
        set_user_config(state.config)

        async def _refresh_symbol(symbol: str):
            settings = state.profiles.get(symbol, {})
            previous = copy.deepcopy(state.earnings_watch.get(symbol) or {})
            try:
                async with screener_manager.earnings_semaphore:
                    payload = await asyncio.wait_for(
                        asyncio.to_thread(
                            build_earnings_watch,
                            symbol,
                            previous,
                            bool(settings.get("options_trading")),
                            False,
                        ),
                        timeout=50.0,
                    )
                return symbol, payload
            except Exception as exc:
                now_ts = time.time()
                payload = dict(previous)
                payload.update({
                    "enabled": True,
                    "status": "stale" if previous.get("initialized") else "unavailable",
                    "ticker": symbol,
                    "checked_at": datetime.now(pytz.UTC).isoformat(),
                    "error": f"{_safe_exception_type(exc)}",
                    "next_check_at": now_ts + 1800,
                })
                print(f"[screener] Earnings watch failed for {symbol}: {_safe_exception_type(exc)}")
                return symbol, payload

        active_symbols = [
            normalize_screener_symbol(raw_symbol)
            for raw_symbol in selected
            if normalize_screener_symbol(raw_symbol)
            and bool(state.profiles.get(normalize_screener_symbol(raw_symbol), {}).get("enabled", True))
            and bool(state.profiles.get(normalize_screener_symbol(raw_symbol), {}).get("earnings_watch_enabled", True))
            and not _is_crypto_symbol(normalize_screener_symbol(raw_symbol))
        ]
        if not active_symbols:
            return
        results = await asyncio.gather(
            *(_refresh_symbol(symbol) for symbol in active_symbols),
            return_exceptions=False,
        )
        for symbol, payload in results:
            state.earnings_watch[symbol] = payload
        await state.save()


async def _watch_screener_fundamental_state(state: ScreenerUserState, symbols: list[str] = None) -> None:
    """Refresh 24-hour fundamental reports independently from technical scans."""
    if state.fundamental_lock.locked():
        return
    async with state.fundamental_lock:
        selected = symbols or list(state.order)
        set_user_config(state.config)

        async def _refresh_symbol(symbol: str):
            settings = state.profiles.get(symbol, {})
            previous = copy.deepcopy(state.fundamental_reports.get(symbol) or {})
            try:
                async with screener_manager.earnings_semaphore:
                    payload = await asyncio.wait_for(asyncio.to_thread(
                        build_fundamental_report,
                        symbol,
                        previous,
                        bool(settings.get("options_trading")),
                        False,
                    ), timeout=50.0)
                return symbol, payload
            except Exception as exc:
                payload = dict(previous)
                payload.update({
                    "ticker": symbol,
                    "status": "stale" if previous.get("status") == "current" else "unavailable",
                    "checked_at": datetime.now(pytz.UTC).isoformat(),
                    "error": f"{_safe_exception_type(exc)}",
                    "next_check_at": time.time() + 1800,
                })
                print(f"[screener] Fundamental report failed for {symbol}: {_safe_exception_type(exc)}")
                return symbol, payload

        active_symbols = [
            normalize_screener_symbol(raw_symbol)
            for raw_symbol in selected
            if normalize_screener_symbol(raw_symbol)
            and bool(state.profiles.get(normalize_screener_symbol(raw_symbol), {}).get("enabled", True))
            and bool(state.profiles.get(normalize_screener_symbol(raw_symbol), {}).get("fundamental_watch_enabled", True))
            and not _is_crypto_symbol(normalize_screener_symbol(raw_symbol))
        ]
        if not active_symbols:
            return
        results = await asyncio.gather(*(_refresh_symbol(symbol) for symbol in active_symbols), return_exceptions=False)
        for symbol, payload in results:
            state.fundamental_reports[symbol] = payload
        await state.save()


def _screener_due_at(evaluation_state: dict) -> float:
    """When a symbol is next due: its scan interval, or earlier for a Session Rider event."""
    due = float((evaluation_state or {}).get("next_due_at", 0) or 0)
    schedule_at = (evaluation_state or {}).get("next_schedule_at")
    try:
        return min(due, float(schedule_at)) if schedule_at else due
    except (TypeError, ValueError):
        return due


def _screener_schedule_scan(state, symbol: str, settings: dict, evaluation_state: dict, scan_interval: int,
                            now_et: Optional[datetime] = None) -> None:
    """Session Rider on the Screener: phase from the clock alone, and one alert per buy/sell time.

    Runs before the closed-market gate (a pre-open sell time still alerts) and never fetches candles,
    never touches the broker and does not depend on the Bots engine."""
    from trader import schedule_preset_info
    cfg = session_schedule.normalized_settings(settings)
    now_et = now_et or datetime.now(pytz.timezone("America/New_York"))
    info = schedule_preset_info(symbol, settings, now_et)
    sched = info.get("schedule") or {}
    if cfg["mode"] is not None:
        mode = "overnight" if cfg["mode"] == session_schedule.MODE_OVERNIGHT else "intraday"
        for ev in session_schedule.due_alerts(now_et, cfg):
            at_label = session_schedule.fmt_et_ct(ev["at"])
            if ev["event"] == "BUY":
                nxt = session_schedule.sell_event(datetime.fromisoformat(ev["entry_session"]).date(), cfg)["at"]
                text = f"Buy time reached ({mode} schedule). The schedule sells {symbol} at {session_schedule.fmt_et_ct(nxt)}."
            else:
                text = f"Sell time reached ({mode} schedule): the schedule exits {symbol} now."
            state.commit_schedule_event(symbol, {"event": ev["event"], "key": ev["key"], "at_label": at_label,
                                                 "mode": cfg["mode"], "entry_session": ev["entry_session"], "text": text})
    next_event = session_schedule.next_event_time(now_et, cfg) if cfg["mode"] else None
    next_due_at = time.time() + scan_interval
    cached_scan = copy.deepcopy(state.latest_scans.get(symbol) or _empty_screener_scan(symbol, settings))
    cached_scan["strategy_preset"] = info
    cached_scan["action"] = "HOLD"
    cached_scan["reason"] = info.get("reason")
    scan_log = cached_scan.setdefault("scan_log", {})
    scan_log.update({
        "summary": info.get("reason") or "Session Rider schedule",
        "detail": (f"Buy {sched.get('buy_label') or '—'} · Sell {sched.get('sell_label') or '—'}. "
                   "Alerts fire at the scheduled times. Analysis only; no orders."),
        "status": "SCHEDULE",
        "decision": "HOLD",
        # Clock-only: there is no candle evaluation to wait for, so the default "initial evaluation pending" blocker
        # of an empty scan must not be shown, and the card gets a real summary instead.
        "blockers": [],
        "final_summary": (f"Watch summary — {info.get('reason') or 'Session Rider schedule'} "
                          f"Buy {sched.get('buy_label') or '—'} · Sell {sched.get('sell_label') or '—'}."),
        "refresh_rate_sec": scan_interval,
        "next_due_at": next_due_at,
        "session_paused": False,
        "schedule": sched,
        # The card's "Refreshing" state ends when a scan reports a time. A clock-only scan has no candle, so say
        # explicitly when it ran; otherwise the card waited for a candle that never comes.
        "updated_at": datetime.now(pytz.UTC).isoformat(),
    })
    cached_scan["scan_log"] = scan_log
    state.latest_scans[symbol] = cached_scan
    evaluation_state["next_due_at"] = next_due_at
    evaluation_state["next_schedule_at"] = next_event.timestamp() if next_event else None
    evaluation_state["session_paused"] = False
    evaluation_state["last_data_check_at"] = datetime.now(pytz.UTC).isoformat()


async def _screener_scheduler_loop() -> None:
    """Independent scheduler that survives Bots-page and trading-loop disablement."""
    await screener_manager.discover_active_users()
    next_discovery = time.monotonic() + 60
    discovery_task: Optional[asyncio.Task] = None

    async def _refresh_registry() -> None:
        try:
            await screener_manager.discover_active_users()
        except Exception as exc:  # the scheduler must keep running on the last known registry
            print(f"[ScreenerManager] Registry refresh error: {type(exc).__name__}: {exc}")

    while True:
        cycle_started = time.monotonic()
        if cycle_started >= next_discovery and (discovery_task is None or discovery_task.done()):
            # Background refresh: a slow registry read must not stall scans and alerts for its whole duration.
            discovery_task = asyncio.create_task(_refresh_registry())
            next_discovery = cycle_started + 60
        states = list(screener_manager.states.values())
        if states:
            due_work = []
            due_price_work = []
            due_news_work = []
            due_earnings_work = []
            due_fundamental_work = []
            now = time.time()
            for state in states:
                due_symbols = [
                    symbol for symbol in state.order
                    if bool(state.profiles.get(symbol, {}).get("enabled", True))
                    and now >= _screener_due_at(state.evaluation_state.get(symbol, {}))
                ]
                if state.force_scan.is_set() and not state.scan_lock.locked():
                    due_work.append(_scan_screener_state(state, due_symbols or list(state.order), force_scan=True))
                elif due_symbols:
                    due_work.append(_scan_screener_state(state, due_symbols))
                due_price_symbols = [
                    symbol
                    for symbol in state.order
                    if bool(state.profiles.get(symbol, {}).get("enabled", True))
                    and now >= float(state.evaluation_state.get(symbol, {}).get("next_price_watch_at", 0) or 0)
                ]
                if due_price_symbols:
                    due_price_work.append(_watch_screener_price_levels_state(state, due_price_symbols))
                due_news_symbols = [
                    symbol for symbol in state.order
                    if bool(state.profiles.get(symbol, {}).get("enabled", True))
                    and _screener_news_refresh_due(state, symbol, now)
                ]
                if due_news_symbols:
                    due_news_work.append(_watch_screener_news_state(state, due_news_symbols))
                due_earnings_symbols = [
                    symbol for symbol in state.order
                    if bool(state.profiles.get(symbol, {}).get("enabled", True))
                    and bool(state.profiles.get(symbol, {}).get("earnings_watch_enabled", True))
                    and not _is_crypto_symbol(symbol)
                    and (now >= float(state.earnings_watch.get(symbol, {}).get("next_check_at", 0) or 0) or (state.earnings_watch.get(symbol) or {}).get("initialized") and (state.earnings_watch.get(symbol) or {}).get("data_version") != EARNINGS_DATA_VERSION)
                ]
                if due_earnings_symbols:
                    due_earnings_work.append(_watch_screener_earnings_state(state, due_earnings_symbols))
                due_fundamental_symbols = [
                    symbol for symbol in state.order
                    if bool(state.profiles.get(symbol, {}).get("enabled", True))
                    and bool(state.profiles.get(symbol, {}).get("fundamental_watch_enabled", True))
                    and not _is_crypto_symbol(symbol)
                    and (now >= float(state.fundamental_reports.get(symbol, {}).get("next_check_at", 0) or 0) or (state.fundamental_reports.get(symbol) or {}).get("status") == "current" and (state.fundamental_reports.get(symbol) or {}).get("report_version") != FUNDAMENTAL_REPORT_VERSION)
                ]
                if due_fundamental_symbols:
                    due_fundamental_work.append(_watch_screener_fundamental_state(state, due_fundamental_symbols))
            if due_work or due_price_work or due_news_work or due_earnings_work or due_fundamental_work:
                await asyncio.gather(
                    *(due_work + due_price_work + due_news_work + due_earnings_work + due_fundamental_work),
                    return_exceptions=True,
                )
        elapsed = time.monotonic() - cycle_started
        # Price-level monitoring defaults to a five-second cadence; candle
        # scans retain their per-profile intervals and are still gated by a
        # completed-candle probe.
        await asyncio.sleep(max(1.0, 5.0 - elapsed))


def _smtp_settings() -> dict:
    return {
        "host": os.getenv("SCREENER_SMTP_HOST") or os.getenv("SMTP_HOST", ""),
        "port": int(os.getenv("SCREENER_SMTP_PORT") or os.getenv("SMTP_PORT", "587")),
        "username": os.getenv("SCREENER_SMTP_USERNAME") or os.getenv("SMTP_USERNAME", ""),
        "password": os.getenv("SCREENER_SMTP_PASSWORD") or os.getenv("SMTP_PASSWORD", ""),
        "from_email": os.getenv("SCREENER_SMTP_FROM") or os.getenv("SMTP_FROM", ""),
        "use_tls": str(os.getenv("SCREENER_SMTP_USE_TLS", os.getenv("SMTP_USE_TLS", "true"))).lower() not in {"0", "false", "no"},
    }


def _send_screener_email_sync(recipient: str, subject: str, body_text: str, body_html: str, message_id: str) -> None:
    import smtplib
    from email.message import EmailMessage

    smtp = _smtp_settings()
    if not smtp["host"] or not smtp["from_email"]:
        raise RuntimeError("Screener SMTP is not configured")
    message = EmailMessage()
    message["Subject"] = subject
    message["From"] = smtp["from_email"]
    message["To"] = recipient
    message["Message-ID"] = message_id
    message.set_content(body_text)
    message.add_alternative(body_html, subtype="html")
    with smtplib.SMTP(smtp["host"], smtp["port"], timeout=15) as connection:
        connection.ehlo()
        if smtp["use_tls"]:
            connection.starttls()
            connection.ehlo()
        if smtp["username"]:
            connection.login(smtp["username"], smtp["password"])
        connection.send_message(message)


def _twilio_sms_settings() -> dict:
    """Read SMS provider credentials without returning them to the UI."""
    return {
        "account_sid": os.getenv("TWILIO_ACCOUNT_SID", "").strip(),
        "auth_token": os.getenv("TWILIO_AUTH_TOKEN", "").strip(),
        "from_number": normalize_screener_sms_phone(os.getenv("TWILIO_FROM_NUMBER", "")),
    }


def _sms_provider_configured() -> bool:
    settings = _twilio_sms_settings()
    return bool(settings["account_sid"] and settings["auth_token"] and settings["from_number"])


def _send_screener_sms_sync(recipient: str, body: str) -> None:
    """Send one SMS via Twilio's HTTPS API; callers run this off the event loop."""
    import urllib.parse
    import urllib.request

    settings = _twilio_sms_settings()
    if not settings["account_sid"] or not settings["auth_token"] or not settings["from_number"]:
        raise RuntimeError("Screener SMS provider is not configured")
    destination = normalize_screener_sms_phone(recipient)
    if not destination:
        raise ValueError("A valid E.164 SMS phone number is required")
    payload = urllib.parse.urlencode({
        "To": destination,
        "From": settings["from_number"],
        "Body": body[:1200],
    }).encode("utf-8")
    credentials = base64.b64encode(
        f"{settings['account_sid']}:{settings['auth_token']}".encode("utf-8")
    ).decode("ascii")
    request = urllib.request.Request(
        f"https://api.twilio.com/2010-04-01/Accounts/{settings['account_sid']}/Messages.json",
        data=payload,
        headers={
            "Authorization": f"Basic {credentials}",
            "Content-Type": "application/x-www-form-urlencoded",
        },
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=15) as response:
        if not 200 <= int(response.getcode() or 0) < 300:
            raise RuntimeError("Screener SMS provider returned a non-success status")


async def _verified_email_for_user(uid: str) -> str:
    try:
        record = await asyncio.to_thread(auth.get_user, uid)
        email = str(getattr(record, "email", "") or "").strip()
        return email if bool(getattr(record, "email_verified", False)) else ""
    except Exception as exc:
        print(f"[screener-email] Could not resolve verified email for {uid}: {_safe_exception_type(exc)}")
        return ""


def _screener_policy_alert_details(notification: dict) -> dict:
    """Extract the selected Screener policy and evidence for alert delivery."""
    scan_log = notification.get("scan_log") if isinstance(notification.get("scan_log"), dict) else {}
    mode = normalize_screening_strategy_mode(
        notification.get("screening_strategy_mode")
        or scan_log.get("screening_strategy_mode")
        or (notification.get("settings_snapshot") or {}).get("strategy_mode", "combined")
    )
    label = str(
        notification.get("screening_strategy_label")
        or scan_log.get("screening_strategy_label")
        or screening_strategy_label(mode)
    )
    qualification = notification.get("strategy_qualification")
    if not isinstance(qualification, dict):
        qualification = scan_log.get("strategy_qualification") if isinstance(scan_log.get("strategy_qualification"), dict) else {}
    event = notification.get("event_qualification")
    if not isinstance(event, dict):
        event = scan_log.get("event_qualification") if isinstance(scan_log.get("event_qualification"), dict) else {}
    event_parts = []
    if event:
        event_direction = str(event.get("direction") or "NEUTRAL").upper()
        event_status = "qualified" if event.get("eligible") else "waiting"
        event_parts.append(f"{event_status} {event_direction} event leg")
        if event.get("headline"):
            event_parts.append(f"headline: {event.get('headline')}")
        if event.get("source"):
            event_parts.append(f"source: {event.get('source')}")
        if event.get("age_hours") is not None:
            try:
                event_parts.append(f"age: {float(event['age_hours']):.1f}h")
            except (TypeError, ValueError):
                pass
        if event.get("sentiment_pct") is not None:
            event_parts.append(f"sentiment: {float(event['sentiment_pct']):.0f}%")
        if event.get("confidence") is not None:
            try:
                event_parts.append(f"confidence: {float(event['confidence']):.0%}")
            except (TypeError, ValueError):
                pass
    return {
        "mode": mode,
        "label": label,
        "qualification": qualification,
        "qualification_reason": str(qualification.get("reason") or "").strip(),
        "event_text": "; ".join(event_parts),
        "alert_basis": str(notification.get("alert_basis") or "screening_policy"),
    }


def _email_content(notification: dict, recipient: str) -> tuple[str, str, str]:
    import html
    symbol = html.escape(str(notification.get("symbol", "")))
    event_type = str(notification.get("event_type") or notification.get("alert_kind") or "").upper()
    if event_type == "SCHEDULE":
        sched = notification.get("schedule_event") or {}
        verb = "Buy" if str(sched.get("event")).upper() == "BUY" else "Sell"
        reason_raw = str(notification.get("reason") or "")
        subject = f"BotBulls Screener: {symbol} Session Rider {verb.lower()} time"
        text = (f"{symbol}: Session Rider scheduled {verb.lower()} time reached ({sched.get('at_label') or ''}).\n\n"
                f"{reason_raw}\n\nThis is an analysis alert only; no trade was placed.")
        html_body = (f"<h2>{symbol} · Session Rider {verb.lower()} time</h2><p>{html.escape(str(sched.get('at_label') or ''))}</p>"
                     f"<p>{html.escape(reason_raw)}</p><p>This is an analysis alert only; no trade was placed.</p>"
                     f"<p><a href=\"{html.escape(os.getenv('SCREENER_PUBLIC_URL', 'http://localhost:8000/screener.html'))}\">Open Screener</a></p>")
        return subject, text, html_body
    price_event = notification.get("price_event") or {}
    if not isinstance(price_event, dict):
        price_event = {}
    price_event_type = event_type if event_type in {"PRICE_EXIT", "PRICE_ENTRY"} else str(price_event.get("event_type") or "").upper()
    is_price_event = price_event_type in {"PRICE_EXIT", "PRICE_ENTRY"}
    if is_price_event:
        is_price_entry = price_event_type == "PRICE_ENTRY"
        event_label = "Price Entry" if is_price_entry else "Price Exit"
        level = html.escape(str(price_event.get("level_name") or "level").strip().title())
        strategy = html.escape(str(price_event.get("strategy_label") or price_event.get("mode") or "Price").strip())
        trigger_price_raw = price_event.get("trigger_price", notification.get("price", "Unavailable"))
        trigger_price = html.escape(str(trigger_price_raw))
        observed_at = html.escape(str(price_event.get("observed_at") or notification.get("created_at") or "Unavailable"))
        source = html.escape(str(price_event.get("price_source") or "live quote"))
        quote_age_raw = price_event.get("quote_age_seconds")
        quote_age = html.escape(
            f"{float(quote_age_raw):.1f}s" if isinstance(quote_age_raw, (int, float)) else str(quote_age_raw or "unavailable")
        )
        reason_raw = str(notification.get("reason") or f"{strategy} {level.lower()} reached by live price.")
        reason = html.escape(reason_raw)
        target_price_raw = price_event.get("target_price")
        target_line = f"Target price: {target_price_raw}\n" if is_price_entry and target_price_raw is not None else ""
        target_html = f"<br><strong>Target price:</strong> {html.escape(str(target_price_raw))}" if is_price_entry and target_price_raw is not None else ""
        subject = f"BotBulls Screener: {symbol} {event_label} ({level})"
        text = (
            f"{symbol} reached a configured live-price {'entry' if is_price_entry else 'exit'} level.\n\n"
            f"Level: {level}\nStrategy: {strategy}\nTrigger price: {trigger_price_raw}\n"
            f"{target_line}"
            f"Observed: {price_event.get('observed_at') or notification.get('created_at') or 'Unavailable'}\n"
            f"Quote source: {price_event.get('price_source') or 'live quote'}\nQuote age: {quote_age_raw if quote_age_raw is not None else 'unavailable'}\n"
            f"Reason: {reason_raw}\n\n"
            f"This is an analysis alert only; no trade was placed."
        )
        html_body = (
            f"<h2>{symbol} {event_label}</h2>"
            f"<p><strong>Level:</strong> {level}<br><strong>Strategy:</strong> {strategy}<br>"
            f"<strong>Trigger price:</strong> {trigger_price}{target_html}<br><strong>Observed:</strong> {observed_at}<br>"
            f"<strong>Quote source:</strong> {source}<br><strong>Quote age:</strong> {quote_age}</p>"
            f"<p>{reason}</p>"
            f"<p>This is an analysis alert only; no trade was placed.</p>"
            f"<p><a href=\"{html.escape(os.getenv('SCREENER_PUBLIC_URL', 'http://localhost:8000/screener.html'))}\">Open Screener</a></p>"
        )
        return subject, text, html_body
    policy = _screener_policy_alert_details(notification)
    raw_direction = str(notification.get("direction", "Neutral"))
    direction = html.escape(raw_direction)
    reason_raw = policy["qualification_reason"] or str(notification.get("reason", ""))
    reason = html.escape(reason_raw)
    price = html.escape(str(notification.get("price", "Unavailable")))
    atr_levels = notification.get("atr_levels") or {}
    stop = html.escape(str(atr_levels.get("stop", "Unavailable")))
    target = html.escape(str(atr_levels.get("target", "Unavailable")))
    policy_label = html.escape(policy["label"])
    policy_reason = html.escape(policy["qualification_reason"] or "")
    event_text = html.escape(policy["event_text"])
    basis_text = html.escape(policy["alert_basis"].replace("_", " ").title())
    policy_lines_text = (
        f"Screening policy: {policy['label']}\n"
        f"Alert basis: {policy['alert_basis'].replace('_', ' ').title()}\n"
        f"Qualification: {policy['qualification_reason'] or reason_raw}\n"
        + (f"Event evidence: {policy['event_text']}\n" if policy["event_text"] else "")
    )
    policy_lines_html = (
        f"<strong>Screening policy:</strong> {policy_label}<br>"
        f"<strong>Alert basis:</strong> {basis_text}<br>"
        f"<strong>Qualification:</strong> {policy_reason or reason}<br>"
        + (f"<strong>Event evidence:</strong> {event_text}<br>" if event_text else "")
    )
    subject = f"BotBulls Screener: {symbol} {policy['label']} {direction.title()} setup"
    text = (
        f"{symbol} has a {direction} Screener setup.\n\n"
        f"{policy_lines_text}"
        f"Price: {price}\nReason: {notification.get('reason', '')}\n"
        f"ATR reference stop: {atr_levels.get('stop', 'Unavailable')}\n"
        f"ATR reference target: {atr_levels.get('target', 'Unavailable')}\n"
        f"This is an analysis alert only; no trade was placed."
    )
    html_body = (
        f"<h2>{symbol} {direction.title()} setup</h2>"
        f"<p>{policy_lines_html}<strong>Price:</strong> {price}</p><p>{reason}</p>"
        f"<p><strong>ATR informational stop:</strong> {stop}<br>"
        f"<strong>ATR informational target:</strong> {target}</p>"
        f"<p>This is an analysis alert only; no trade was placed.</p>"
        f"<p><a href=\"{html.escape(os.getenv('SCREENER_PUBLIC_URL', 'http://localhost:8000/screener.html'))}\">Open Screener</a></p>"
    )
    return subject, text, html_body


def _sms_content(notification: dict) -> str:
    symbol = str(notification.get("symbol") or "Ticker")
    event_type = str(notification.get("event_type") or notification.get("alert_kind") or "").upper()
    if event_type == "SCHEDULE":
        sched = notification.get("schedule_event") or {}
        verb = "BUY" if str(sched.get("event")).upper() == "BUY" else "SELL"
        return (f"BotBulls {symbol} Session Rider {verb} time ({sched.get('at_label') or ''}). "
                "Analysis only; no order was placed.")
    price_event = notification.get("price_event") or {}
    if not isinstance(price_event, dict):
        price_event = {}
    price_event_type = event_type if event_type in {"PRICE_EXIT", "PRICE_ENTRY"} else str(price_event.get("event_type") or "").upper()
    if price_event_type in {"PRICE_EXIT", "PRICE_ENTRY"}:
        is_price_entry = price_event_type == "PRICE_ENTRY"
        event_label = "PRICE ENTRY" if is_price_entry else "PRICE EXIT"
        level = str(price_event.get("level_name") or "level").strip().title()
        strategy = str(price_event.get("strategy_label") or price_event.get("mode") or "Price").strip()
        trigger_price = price_event.get("trigger_price", notification.get("price"))
        source = str(price_event.get("price_source") or "live quote")
        quote_age = price_event.get("quote_age_seconds")
        age_text = f" age {float(quote_age):.1f}s" if isinstance(quote_age, (int, float)) else ""
        reason = " ".join(str(notification.get("reason") or f"{strategy} {level.lower()} reached by live price.").split())
        target_price = price_event.get("target_price")
        target_text = f" target ${target_price}." if is_price_entry and target_price is not None else "."
        return (
            f"BotBulls {symbol} {event_label}: {level} at ${trigger_price}{target_text} "
            f"{reason} ({source}{age_text}). Analysis only; no order was placed."
        )
    policy = _screener_policy_alert_details(notification)
    direction = str(notification.get("direction") or "Signal").title()
    price = notification.get("price")
    price_text = f" Price: ${price}" if price is not None else ""
    reason = " ".join(str(policy["qualification_reason"] or notification.get("reason") or "").split())
    suffix = f" {reason}" if reason else ""
    event_suffix = f" Event: {policy['event_text']}." if policy["event_text"] else ""
    return (
        f"BotBulls {symbol} {direction} screener alert. Policy: {policy['label']}. "
        f"Basis: {policy['alert_basis'].replace('_', ' ')}.{price_text}{suffix}{event_suffix} "
        "Analysis only; no order was placed."
    )


def _reclaim_expired_screener_leases(outbox: list[dict], now: float) -> None:
    """Return work abandoned by a crashed delivery worker to the retry queue."""
    for item in outbox:
        if item.get("status") != "sending":
            continue
        try:
            lease_until = float(item.get("lease_until") or 0)
        except (TypeError, ValueError, OverflowError):
            lease_until = 0
        if lease_until <= now:
            item.update({
                "status": "retrying",
                "next_attempt_at": now,
                "lease_until": None,
                "recovered_at": now,
            })


async def _process_screener_email_outbox_body(state: ScreenerUserState) -> None:
    now = time.time()
    _reclaim_expired_screener_leases(state.email_outbox, now)
    def _timestamp(value, fallback=0.0):
        try:
            if isinstance(value, str):
                return datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()
            return float(value or fallback)
        except (TypeError, ValueError, OverflowError):
            return float(fallback)
    smtp = _smtp_settings()
    if not smtp["host"] or not smtp["from_email"]:
        return
    for item in list(state.email_outbox):
        if item.get("status") not in {"queued", "retrying"} or float(item.get("next_attempt_at", 0) or 0) > now:
            continue
        item["status"] = "sending"
        item["lease_until"] = now + 120
        notification = next((entry for entry in state.notifications if entry.get("id") == item.get("notification_id")), None)
        if not notification and not item.get("test"):
            item.update({"status": "skipped", "skip_reason": "notification_missing"})
            continue
        if item.get("test"):
            notification = {"symbol": "Screener", "direction": "Test", "reason": "Test email delivery", "price": "N/A", "atr_levels": {}}
        else:
            profile = state.profiles.get(notification.get("symbol"), {})
            opposite_exists = any(
                other.get("symbol") == notification.get("symbol")
                and other.get("direction") in {"BULLISH", "BEARISH"}
                and other.get("direction") != notification.get("direction")
                and _timestamp(other.get("created_at")) > _timestamp(notification.get("created_at"))
                for other in state.notifications
            )
            age_seconds = now - _timestamp(notification.get("created_at"), now)
            if not profile or not bool(profile.get("enabled", True)):
                item.update({"status": "skipped", "skip_reason": "profile_disabled_or_deleted"})
                notification["email_status"] = "skipped"
                notification["email_skip_reason"] = item["skip_reason"]
                continue
            if not bool(profile.get("email_enabled", state.notification_settings.get("email_enabled"))):
                item.update({"status": "skipped", "skip_reason": "email_disabled"})
                notification["email_status"] = "skipped"
                notification["email_skip_reason"] = item["skip_reason"]
                continue
            if opposite_exists or age_seconds > 1800:
                item.update({"status": "skipped", "skip_reason": "superseded_or_expired"})
                notification["email_status"] = "skipped"
                notification["email_skip_reason"] = item["skip_reason"]
                continue
        recipient = await _verified_email_for_user(state.uid)
        if not recipient:
            item.update({"status": "skipped", "skip_reason": "verified_email_required"})
            if not item.get("test") and notification:
                notification["email_status"] = "skipped"
                notification["email_skip_reason"] = item["skip_reason"]
            continue
        subject, text_body, html_body = _email_content(notification, recipient)
        item["lease_until"] = time.time() + 120
        if not await state.save():
            item.update(status="retrying", lease_until=None, next_attempt_at=time.time() + 60,
                        error="durable_claim_failed")
            return
        try:
            domain = smtp["from_email"].split("@")[-1] or "localhost"
            await asyncio.to_thread(
                _send_screener_email_sync,
                recipient,
                subject,
                text_body,
                html_body,
                f"<{item.get('id')}@{domain}>",
            )
            item.update({"status": "sent", "sent_at": time.time(), "lease_until": None})
            if not item.get("test") and notification:
                notification["email_status"] = "sent"
        except Exception as exc:
            attempts = int(item.get("attempts", 0) or 0) + 1
            item["attempts"] = attempts
            if attempts >= 4:
                item.update({"status": "failed", "failed_at": time.time(), "error": _safe_exception_type(exc), "lease_until": None})
                if not item.get("test") and notification:
                    notification["email_status"] = "failed"
            else:
                item.update({"status": "retrying", "next_attempt_at": now + (60, 300, 900)[attempts - 1], "error": _safe_exception_type(exc), "lease_until": None})
                if not item.get("test") and notification:
                    notification["email_status"] = "retrying"
        if not await state.save():
            # Do not dispatch more messages while delivery acknowledgments
            # cannot be persisted. Recovery may still see an ambiguous send.
            return
    state.health["email_queued"] = sum(item.get("status") in {"queued", "sending", "retrying"} for item in state.email_outbox)
    state.health["email_failed"] = sum(item.get("status") == "failed" for item in state.email_outbox)
    await state.save()


async def _process_screener_sms_outbox_body(state: ScreenerUserState) -> None:
    """Deliver queued SMS alerts with the same bounded retry policy as email."""
    now = time.time()
    _reclaim_expired_screener_leases(state.sms_outbox, now)
    provider_ready = _sms_provider_configured()
    for item in list(state.sms_outbox):
        if item.get("status") not in {"queued", "retrying"} or float(item.get("next_attempt_at", 0) or 0) > now:
            continue
        notification = next((entry for entry in state.notifications if entry.get("id") == item.get("notification_id")), None)
        if not notification:
            item.update({"status": "skipped", "skip_reason": "notification_missing"})
            continue
        if not bool(state.notification_settings.get("sms_enabled")):
            item.update({"status": "skipped", "skip_reason": "sms_disabled"})
            notification.update({"sms_status": "skipped", "sms_skip_reason": item["skip_reason"]})
            continue
        if not provider_ready:
            item.update({"status": "skipped", "skip_reason": "sms_provider_not_configured"})
            notification.update({"sms_status": "setup_required", "sms_skip_reason": item["skip_reason"]})
            continue
        profile = state.profiles.get(notification.get("symbol"), {})
        if not profile or not bool(profile.get("enabled", True)):
            item.update({"status": "skipped", "skip_reason": "profile_disabled_or_deleted"})
            notification.update({"sms_status": "skipped", "sms_skip_reason": item["skip_reason"]})
            continue
        def _notification_timestamp(value, fallback=0.0):
            try:
                if isinstance(value, str):
                    return datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()
                return float(value or fallback)
            except (TypeError, ValueError, OverflowError):
                return float(fallback)
        opposite_exists = any(
            other.get("symbol") == notification.get("symbol")
            and other.get("direction") in {"BULLISH", "BEARISH"}
            and other.get("direction") != notification.get("direction")
            and _notification_timestamp(other.get("created_at")) > _notification_timestamp(notification.get("created_at"))
            for other in state.notifications
        )
        age_seconds = now - _notification_timestamp(notification.get("created_at"), now)
        if opposite_exists or age_seconds > 1800:
            item.update({"status": "skipped", "skip_reason": "superseded_or_expired"})
            notification.update({"sms_status": "skipped", "sms_skip_reason": item["skip_reason"]})
            continue
        recipient = normalize_screener_sms_phone(item.get("to"))
        if not recipient:
            item.update({"status": "skipped", "skip_reason": "valid_phone_required"})
            notification.update({"sms_status": "setup_required", "sms_skip_reason": item["skip_reason"]})
            continue
        item["status"] = "sending"
        item["lease_until"] = time.time() + 120
        if not await state.save():
            item.update(status="retrying", lease_until=None, next_attempt_at=time.time() + 60,
                        error="durable_claim_failed")
            return
        try:
            await asyncio.to_thread(_send_screener_sms_sync, recipient, _sms_content(notification))
            item.update({"status": "sent", "sent_at": time.time(), "lease_until": None})
            notification["sms_status"] = "sent"
        except Exception as exc:
            attempts = int(item.get("attempts", 0) or 0) + 1
            item["attempts"] = attempts
            if attempts >= 4:
                item.update({"status": "failed", "failed_at": time.time(), "error": _safe_exception_type(exc), "lease_until": None})
                notification["sms_status"] = "failed"
            else:
                item.update({"status": "retrying", "next_attempt_at": now + (60, 300, 900)[attempts - 1], "error": _safe_exception_type(exc), "lease_until": None})
                notification["sms_status"] = "retrying"
        if not await state.save():
            return
    state.health["sms_queued"] = sum(item.get("status") in {"queued", "sending", "retrying"} for item in state.sms_outbox)
    state.health["sms_failed"] = sum(item.get("status") == "failed" for item in state.sms_outbox)
    await state.save()


async def _run_screener_delivery_worker(state: ScreenerUserState, worker) -> None:
    """Serialize outbox claims with scans and reload their durable status."""
    if state.scan_lock.locked():
        return
    async with state.scan_lock:
        if not await state.try_acquire_process_scan_lock():
            return
        try:
            await state.refresh_from_persistence()
            await worker(state)
        finally:
            await state.release_process_scan_lock()


async def _process_screener_email_outbox(state: ScreenerUserState) -> None:
    await _run_screener_delivery_worker(state, _process_screener_email_outbox_body)


async def _process_screener_sms_outbox(state: ScreenerUserState) -> None:
    await _run_screener_delivery_worker(state, _process_screener_sms_outbox_body)


async def _screener_delivery_loop() -> None:
    while True:
        states = list(screener_manager.states.values())
        if states:
            delivery_work = [
                work
                for state in states
                for work in (_process_screener_email_outbox(state), _process_screener_sms_outbox(state))
            ]
            await asyncio.gather(*delivery_work, return_exceptions=True)
        await asyncio.sleep(10)


@asynccontextmanager
async def lifespan(app: FastAPI):
    log_setup.wrap_uvicorn_handlers()
    log_setup.banner(app.version)
    await user_manager.start_all()
    heartbeat_task = asyncio.create_task(log_setup.heartbeat_loop(
        lambda: {"screener_states": len(screener_manager.states)}
    ))
    generic_cards_task = asyncio.create_task(_generic_dashboard_card_cache_loop())
    screener_task = asyncio.create_task(_screener_scheduler_loop())
    screener_delivery_task = asyncio.create_task(_screener_delivery_loop())
    try:
        yield
    finally:
        print("[startup] BotBulls backend shutting down")
        heartbeat_task.cancel()
        with suppress(asyncio.CancelledError):
            await heartbeat_task
        screener_delivery_task.cancel()
        with suppress(asyncio.CancelledError):
            await screener_delivery_task
        screener_task.cancel()
        with suppress(asyncio.CancelledError):
            await screener_task
        generic_cards_task.cancel()
        with suppress(asyncio.CancelledError):
            await generic_cards_task
        await user_manager.stop_all()

app = FastAPI(
    title="Bot Bulls",
    description="Automated AI-driven quantitative trading platform",
    version="2.0.0",
    lifespan=lifespan,
    # Interactive API docs and the full schema are for local development only (fail-closed: any other ENV hides them).
    docs_url="/docs" if os.getenv("ENV", "").strip().lower() == "development" else None,
    redoc_url="/redoc" if os.getenv("ENV", "").strip().lower() == "development" else None,
    openapi_url="/openapi.json" if os.getenv("ENV", "").strip().lower() == "development" else None,
)


@app.get("/health", include_in_schema=False)
async def health_check():
    return {
        "status": "ok",
        "service": "bot-bulls-api",
        "version": app.version,
        "screener_states": len(screener_manager.states),
        "timestamp": datetime.now(pytz.UTC).isoformat(),
    }


@app.get("/api/legal/acceptance")
async def get_legal_acceptance(user: dict = Depends(verify_token), eng: UserEngine = Depends(get_user_engine)):
    acceptance = getattr(eng.config, "legal_acceptance", {}) or {}
    return {
        "required_version": LEGAL_TERMS_VERSION,
        "accepted": acceptance.get("terms_version") == LEGAL_TERMS_VERSION,
        "acceptance": acceptance,
    }


@app.post("/api/legal/acceptance")
async def record_legal_acceptance(
    payload: LegalAcceptance,
    user: dict = Depends(verify_token),
    eng: UserEngine = Depends(get_user_engine),
):
    if payload.terms_version != LEGAL_TERMS_VERSION:
        raise HTTPException(status_code=409, detail={
            "code": "TERMS_VERSION_OUTDATED",
            "required_version": LEGAL_TERMS_VERSION,
        })
    if not payload.paper_trading_acknowledged:
        raise HTTPException(status_code=400, detail="Paper/live trading acknowledgement is required.")

    accepted_at = datetime.now(pytz.UTC).isoformat()
    eng.config.legal_acceptance = {
        "terms_version": LEGAL_TERMS_VERSION,
        "accepted_at": accepted_at,
        "accepted_by_uid": eng.uid,
        "accepted_email": user.get("email", ""),
        "paper_trading_acknowledged": True,
        "acceptance_source": "web_sign_in",
    }
    started = time.perf_counter()
    print(f"[legal-debug] save-start uid_hash={_legal_debug_uid(user)}", flush=True)
    try:
        await eng.save_settings()
    except Exception as exc:
        elapsed_ms = (time.perf_counter() - started) * 1000
        print(f"[legal-debug] save-error uid_hash={_legal_debug_uid(user)} elapsed_ms={elapsed_ms:.0f} error={type(exc).__name__}", flush=True)
        raise
    elapsed_ms = (time.perf_counter() - started) * 1000
    print(f"[legal-debug] save-complete uid_hash={_legal_debug_uid(user)} elapsed_ms={elapsed_ms:.0f}", flush=True)
    return {
        "status": "success",
        "required_version": LEGAL_TERMS_VERSION,
        "acceptance": eng.config.legal_acceptance,
    }

allowed_origins = [
    "https://bot-bulls.web.app",
    "https://bot-bulls.firebaseapp.com",
    "https://trading-bot-engine-df3de.firebaseapp.com",
    "https://trading-bot-engine-df3de.web.app",
    "https://207-211-184-22.sslip.io",
]

if os.getenv("ENV") != "production":
    allowed_origins.extend([
        "http://localhost:5000",
        "http://localhost:3000",
        "http://localhost:5500",
        "http://127.0.0.1:3000",
        "http://127.0.0.1:5000",
        "http://127.0.0.1:5500"
    ])

app.add_middleware(
    CORSMiddleware,
    allow_origins=allowed_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
    # Lets the pages read the server's own timing, so the "Heavy snapshot" telemetry can say how much was the backend.
    expose_headers=["X-Snapshot-Time-MS"],
    max_age=3600,
)


_REQUEST_ID_RE = re.compile(r"^[A-Za-z0-9._\-]{1,64}$")
_SLOW_REQUEST_SECONDS = 5.0
_request_log = logging.getLogger("http")


@app.middleware("http")
async def request_trace_middleware(request: Request, call_next):
    """Attach a request id, and log slow or crashed API requests (path only, never the query string or headers)."""
    incoming = request.headers.get("x-request-id", "")
    request_id = incoming if _REQUEST_ID_RE.match(incoming) else secrets.token_hex(6)
    started = time.perf_counter()
    try:
        response = await call_next(request)
    except Exception:
        _request_log.exception(
            "unhandled error rid=%s %s %s after %.0fms",
            request_id, request.method, request.url.path, (time.perf_counter() - started) * 1000,
        )
        raise
    elapsed = time.perf_counter() - started
    response.headers["X-Request-ID"] = request_id
    if request.url.path.startswith("/api/") and elapsed >= _SLOW_REQUEST_SECONDS:
        _request_log.warning(
            "slow request rid=%s %s %s status=%s took %.1fs",
            request_id, request.method, request.url.path, response.status_code, elapsed,
        )
    return response


@app.middleware("http")
async def reset_analysis_context_middleware(request: Request, call_next):
    from user_config import active_evaluation_context, active_ticker_context

    eval_token = active_evaluation_context.set("dashboard")
    ticker_token = active_ticker_context.set(None)
    try:
        return await call_next(request)
    finally:
        active_ticker_context.reset(ticker_token)
        active_evaluation_context.reset(eval_token)


@app.middleware("http")
async def security_headers_and_request_limits(request: Request, call_next):
    # Bound JSON/form submissions before they reach parsers or strategy
    # handlers. Workload-specific endpoints have tighter limits below.
    max_body_bytes = 2 * 1024 * 1024
    content_length = request.headers.get("content-length")
    try:
        if content_length and int(content_length) > max_body_bytes:
            return Response("Request body too large", status_code=413)
    except ValueError:
        return Response("Invalid Content-Length", status_code=400)

    # Content-Length is optional and can be forged. Count streamed ASGI body
    # chunks as they arrive so chunked requests cannot bypass the same limit.
    original_receive = request._receive
    received_bytes = 0

    async def limited_receive():
        nonlocal received_bytes
        message = await original_receive()
        if message.get("type") == "http.request":
            received_bytes += len(message.get("body", b"") or b"")
            if received_bytes > max_body_bytes:
                raise ValueError("request body exceeds configured limit")
        return message

    request._receive = limited_receive

    try:
        response = await call_next(request)
    except ValueError as exc:
        if str(exc) == "request body exceeds configured limit":
            return Response("Request body too large", status_code=413)
        raise
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("X-Frame-Options", "DENY")
    response.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
    response.headers.setdefault("Permissions-Policy", "camera=(), microphone=(), geolocation=()")
    if os.getenv("ENV", "").strip().lower() != "development" and (
        request.url.scheme == "https" or request.headers.get("x-forwarded-proto", "").lower() == "https"
    ):
        response.headers.setdefault("Strict-Transport-Security", "max-age=31536000")
    if request.url.path.startswith("/api/"):
        response.headers.setdefault("Cache-Control", "no-store")
    return response

@app.get("/")
def root():
    return {"message": "Bot Bulls API is running!", "version": "2.0.0"}


@app.get("/api/dev-token")
async def get_development_token(request: Request):
    """Return the ephemeral development token only to a loopback client."""
    client_host = request.client.host if request.client else ""
    if (
        os.getenv("ENV", "").lower() != "development"
        or client_host not in {"127.0.0.1", "::1", "localhost"}
        or not _DEV_AUTH_TOKEN
    ):
        raise HTTPException(status_code=404, detail="Not found")
    return {"token": _DEV_AUTH_TOKEN}


@app.post("/api/alpaca_config")
async def update_alpaca_config(cfg: AlpacaConfig, eng: UserEngine = Depends(get_user_engine)):
    if not cfg.paper and not cfg.live_trading_acknowledged:
        raise HTTPException(
            status_code=400,
            detail="A fresh live-trading acknowledgement is required before connecting a live account.",
        )
    if not cfg.paper and not getattr(eng.broker, "live_tenant_authorized", False):
        raise HTTPException(
            status_code=403,
            detail="Live trading is disabled until the operator binds this account to the authorized tenant.",
        )
    try:
        async with eng.broker_connect_lock:
            success = await asyncio.wait_for(
                asyncio.to_thread(eng.broker.connect, cfg.api_key, cfg.secret_key, cfg.paper),
                timeout=15.0,
            )
    except asyncio.TimeoutError:
        eng.live_trading_armed = False
        eng.broker.live_trading_armed = False
        raise HTTPException(
            status_code=504,
            detail="Broker connection timed out; no order was submitted.",
        )
    if success:
        eng.live_trading_armed = bool(not cfg.paper and cfg.live_trading_acknowledged)
        eng.broker.live_trading_armed = eng.live_trading_armed
        # Persist to cloud (Encrypted)
        data = {
            "api_key": vault.encrypt(cfg.api_key),
            "secret_key": vault.encrypt(cfg.secret_key),
            "paper": cfg.paper,
            "updated_at": firestore.SERVER_TIMESTAMP
        }
        def _sync_save():
            db.collection("users").document(eng.uid).collection("settings").document("alpaca").set(data)
        await asyncio.to_thread(_sync_save)

        # Update config module state
        eng.config.ALPACA_API_KEY = cfg.api_key
        eng.config.ALPACA_SECRET_KEY = cfg.secret_key
        eng.config.ALPACA_PAPER = cfg.paper

        return {"status": "success", "message": "Connected to Alpaca."}
    else:
        eng.live_trading_armed = False
        eng.broker.live_trading_armed = False
        return {"status": "error", "message": "Failed to connect to Alpaca. Check your keys."}


@app.delete("/api/alpaca_config")
def unlink_alpaca(eng: UserEngine = Depends(get_user_engine)):
    # Delete from cloud
    if db:
        try:
            db.collection("users").document(eng.uid).collection("settings").document("alpaca").delete(timeout=2.0)
        except Exception as e:
            print(f"[api] Error deleting alpaca settings: {_safe_exception_type(e)}")

    # Reset broker to simulation and drop all credential-bearing SDK state.
    clear_credentials = getattr(eng.broker, "clear_credentials", None)
    if callable(clear_credentials):
        clear_credentials()
    else:
        eng.broker.simulation_mode = True
        eng.broker.client = None
        eng.broker.live_trading_armed = False
    eng.live_trading_armed = False
    try:
        from broker_factory import invalidate_alpaca_client_cache
        invalidate_alpaca_client_cache()
    except Exception as exc:
        print(f"[api] Alpaca data-client cache cleanup failed: {type(exc).__name__}")

    # Clear config module state
    eng.config.ALPACA_API_KEY = ""
    eng.config.ALPACA_SECRET_KEY = ""
    return {"status": "success", "message": "Alpaca account unlinked. Switched to simulation mode."}


@app.get("/api/alpaca_status")
def get_alpaca_status(eng: UserEngine = Depends(get_user_engine)):
    """Lightweight broker status for pages that do not need a full dashboard scan."""
    has_keys = bool(getattr(eng.config, "ALPACA_API_KEY", ""))
    broker_simulation = bool(getattr(eng.broker, "simulation_mode", True))
    account = {}
    if has_keys and not broker_simulation:
        try:
            raw_account = eng.broker.get_account_info() or {}
            account = {
                "equity": raw_account.get("equity"),
                "cash": raw_account.get("cash"),
                "buying_power": raw_account.get("buying_power"),
                "daily_pl": raw_account.get("daily_pl"),
                "daily_pl_pct": raw_account.get("daily_pl_pct"),
            }
        except Exception as exc:
            print(f"[api] Alpaca account summary unavailable: {_safe_exception_type(exc)}")
    return {
        "status": "success",
        "simulation": (not has_keys) or broker_simulation,
        "has_keys": has_keys,
        "account": account,
        "broker_connected": has_keys and not broker_simulation,
        "broker_status": "connected" if has_keys and not broker_simulation else ("retrying" if has_keys else "not_configured"),
        "broker_status_reason": getattr(eng, "alpaca_load_error", "") or "",
        "alpaca_load_status": getattr(eng, "alpaca_load_status", "unknown"),
    }


@app.get("/api/portfolio/history")
async def get_portfolio_history(
    period: str = "1M",
    timeframe: str = "1D",
    eng: UserEngine = Depends(get_user_engine)
):
    """
    Get portfolio history for the current user's broker connection.
    Maps period '1Y' to Alpaca-compatible '1A'.
    """
    alpaca_period = "1A" if period.upper() == "1Y" else ("all" if period.upper() == "ALL" else period)
    try:
        data = await asyncio.wait_for(
            asyncio.to_thread(eng.broker.get_portfolio_history, period=alpaca_period, timeframe=timeframe),
            timeout=12.0,
        )
        return data
    except (asyncio.TimeoutError, Exception) as e:
        print(f"[api] Portfolio history error/timeout ({_safe_exception_type(e)}) for period={alpaca_period}, timeframe={timeframe}. Using fallback data.")
        try:
            if hasattr(eng.broker, '_ph_cache') and (alpaca_period.lower(), timeframe.lower()) in eng.broker._ph_cache:
                return eng.broker._ph_cache[(alpaca_period.lower(), timeframe.lower())]['data']
            old_sim = eng.broker.simulation_mode
            eng.broker.simulation_mode = True
            fallback_data = eng.broker.get_portfolio_history(period=alpaca_period, timeframe=timeframe)
            eng.broker.simulation_mode = old_sim
            return fallback_data
        except Exception as fallback_err:
            print(f"[api] Fallback generation failed: {fallback_err}")
            return {"timestamp": [], "equity": [], "profit_loss": [], "profit_loss_pct": [], "base_value": 0}
import time as time_module
GENERIC_DASHBOARD_CARD_CACHE_MIN_TTL_SECONDS = 300

def _parse_generic_dashboard_card_cache_ttl():
    raw_value = os.getenv("GENERIC_DASHBOARD_CARD_CACHE_TTL_SECONDS", "3600")
    try:
        ttl = int(raw_value)
    except (TypeError, ValueError):
        print(f"[generic-cache] Invalid GENERIC_DASHBOARD_CARD_CACHE_TTL_SECONDS={raw_value!r}; using 3600")
        ttl = 3600
    if ttl < GENERIC_DASHBOARD_CARD_CACHE_MIN_TTL_SECONDS:
        print(
            "[generic-cache] GENERIC_DASHBOARD_CARD_CACHE_TTL_SECONDS "
            f"{ttl}s is too low; using {GENERIC_DASHBOARD_CARD_CACHE_MIN_TTL_SECONDS}s"
        )
        ttl = GENERIC_DASHBOARD_CARD_CACHE_MIN_TTL_SECONDS
    return ttl

GENERIC_DASHBOARD_CARD_CACHE_TTL_SECONDS = _parse_generic_dashboard_card_cache_ttl()
GENERIC_DASHBOARD_CARD_CACHE_SCHEMA_VERSION = 3
GENERIC_DASHBOARD_CARD_CACHE_DIR = os.path.join(os.path.dirname(__file__), ".cache")
GENERIC_DASHBOARD_CARD_CACHE_FILE = os.path.join(GENERIC_DASHBOARD_CARD_CACHE_DIR, "generic_dashboard_cards.json")
GENERIC_DASHBOARD_CARD_CACHE_LOCK_FILE = os.path.join(GENERIC_DASHBOARD_CARD_CACHE_DIR, "generic_dashboard_cards.lock")
_MARKET_DATA_CACHE = None
_MARKET_DATA_LAST_FETCH = 0
_MARKET_DATA_LOCK = asyncio.Lock()
_MACRO_EVENTS_CACHE = None
_MACRO_EVENTS_LAST_FETCH = 0
_MACRO_EVENTS_LOCK = asyncio.Lock()
_FEAR_GREED_CACHE = None
_FEAR_GREED_LAST_FETCH = 0
_FEAR_GREED_LOCK = asyncio.Lock()

@contextmanager
def _generic_dashboard_card_disk_lock():
    os.makedirs(GENERIC_DASHBOARD_CARD_CACHE_DIR, exist_ok=True)
    with open(GENERIC_DASHBOARD_CARD_CACHE_LOCK_FILE, "a+") as lock_file:
        try:
            import fcntl
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)
        except Exception:
            # Best-effort fallback on platforms without fcntl. Atomic replace still protects readers.
            pass
        try:
            yield
        finally:
            try:
                import fcntl
                fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)
            except Exception:
                pass


def _read_generic_dashboard_card_disk_cache():
    try:
        with open(GENERIC_DASHBOARD_CARD_CACHE_FILE, "r", encoding="utf-8") as cache_file:
            data = json.load(cache_file)
            return data if isinstance(data, dict) else {}
    except FileNotFoundError:
        return {}
    except Exception as e:
        print(f"[generic-cache] Could not read shared cache file: {_safe_exception_type(e)}")
        return {}


def _write_generic_dashboard_card_disk_cache(cache_doc):
    os.makedirs(GENERIC_DASHBOARD_CARD_CACHE_DIR, exist_ok=True)
    tmp_path = f"{GENERIC_DASHBOARD_CARD_CACHE_FILE}.{os.getpid()}.tmp"
    with open(tmp_path, "w", encoding="utf-8") as cache_file:
        json.dump(jsonable_encoder(cache_doc), cache_file, separators=(",", ":"))
    os.replace(tmp_path, GENERIC_DASHBOARD_CARD_CACHE_FILE)


def _load_persisted_generic_card(card_key):
    cache_doc = _read_generic_dashboard_card_disk_cache()
    entry = cache_doc.get(card_key)
    if not isinstance(entry, dict):
        return None, 0
    if int(entry.get("schema_version") or 0) != GENERIC_DASHBOARD_CARD_CACHE_SCHEMA_VERSION:
        return None, 0
    fetched_at_epoch = float(entry.get("fetched_at_epoch") or 0)
    data = entry.get("data")
    if data and time_module.time() - fetched_at_epoch < GENERIC_DASHBOARD_CARD_CACHE_TTL_SECONDS:
        return data, fetched_at_epoch
    return None, 0


def _save_persisted_generic_card(card_key, data):
    with _generic_dashboard_card_disk_lock():
        cache_doc = _read_generic_dashboard_card_disk_cache()
        cache_doc[card_key] = {
            "schema_version": GENERIC_DASHBOARD_CARD_CACHE_SCHEMA_VERSION,
            "fetched_at_epoch": time_module.time(),
            "data": data,
        }
        _write_generic_dashboard_card_disk_cache(cache_doc)


def _mark_generic_card_from_shared_cache(data):
    if not isinstance(data, dict):
        return data
    marked = dict(data)
    marked["cache"] = {
        **marked.get("cache", {}),
        "scope": "server-shared",
        "shared_snapshot": True,
    }
    return marked

MARKET_SECTOR_TICKERS = (
    "XLK XLF XLV XLE "
    "AAPL MSFT GOOG NVDA META TSLA AVGO AMD QCOM NFLX AMZN ADBE CRM INTC CSCO ORCL IBM UBER ABNB PLTR MU TXN NOW SHOP "
    "JPM BAC WFC MS GS V MA AXP BLK C SCHW HDB "
    "LLY UNH JNJ MRK ABBV PFE WMT COST PG KO PEP NKE MCD EL "
    "XOM CVX COP OXY SLB CAT GE HON UNP LMT DE MMM "
    "BTC-USD ETH-USD SOL-USD ADA-USD XRP-USD DOGE-USD DOT-USD LINK-USD LTC-USD NEAR-USD BCH-USD AVAX-USD"
)
MARKET_SECTOR_SUMMARY_GROUPS = {
    "tech": {
        "label": "Technology",
        "benchmark": "XLK",
        "tickers": ["AAPL", "MSFT", "GOOG", "NVDA", "META", "TSLA", "AVGO", "AMD", "QCOM", "NFLX", "AMZN", "ADBE", "CRM", "INTC", "CSCO", "ORCL", "IBM", "UBER", "ABNB", "PLTR", "MU", "TXN", "NOW", "SHOP"],
    },
    "financials": {
        "label": "Financials",
        "benchmark": "XLF",
        "tickers": ["JPM", "BAC", "WFC", "MS", "GS", "V", "MA", "AXP", "BLK", "C", "SCHW", "HDB"],
    },
    "healthcare": {
        "label": "Healthcare & Retail",
        "benchmark": "XLV",
        "tickers": ["LLY", "UNH", "JNJ", "MRK", "ABBV", "PFE", "WMT", "COST", "PG", "KO", "PEP", "NKE", "MCD", "EL"],
    },
    "energy": {
        "label": "Energy & Industrials",
        "benchmark": "XLE",
        "tickers": ["XOM", "CVX", "COP", "OXY", "SLB", "CAT", "GE", "HON", "UNP", "LMT", "DE", "MMM"],
    },
    "crypto": {
        "label": "Crypto",
        "benchmark": "BTC-USD",
        "tickers": ["BTC-USD", "ETH-USD", "SOL-USD", "ADA-USD", "XRP-USD", "DOGE-USD", "DOT-USD", "LINK-USD", "LTC-USD", "NEAR-USD", "BCH-USD", "AVAX-USD"],
    },
}
MARKET_BREADTH_UNIVERSE = (
    "AAPL MSFT GOOG GOOGL NVDA META TSLA AVGO AMD QCOM NFLX AMZN ADBE CRM INTC CSCO ORCL IBM UBER ABNB PLTR MU TXN NOW SHOP "
    "JPM BAC WFC MS GS V MA AXP BLK C SCHW "
    "LLY UNH JNJ MRK ABBV PFE ISRG TMO ABT DHR "
    "WMT COST PG KO PEP NKE MCD HD LOW TGT "
    "XOM CVX COP OXY SLB EOG CAT GE HON UNP LMT DE MMM "
    "NEE DUK SO PLD AMT LIN"
)


def _download_generic_yahoo(tickers, period):
    """Download generic dashboard prices without letting one bad symbol fail the batch.

    Yahoo occasionally logs ``possibly delisted`` when one symbol in a large
    multi-ticker request is temporarily empty. That message is not proof that
    the security is delisted. Keep provider diagnostics out of the request
    log, retry missing symbols individually, and let the caller decide whether
    a symbol should be treated as unavailable.
    """
    requested = list(dict.fromkeys(str(ticker).strip().upper() for ticker in (tickers or []) if str(ticker).strip()))
    if not requested:
        return None
    try:
        # ``threads=False`` avoids intermittent partial batches/rate-limit
        # races. Explicitly keep raw OHLC values consistent across retries.
        with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
            return yf.download(
                requested,
                period=period,
                progress=False,
                threads=False,
                auto_adjust=False,
                group_by="column",
            )
    except Exception as exc:
        print(f"[generic-cache] Yahoo batch request failed ({period}): {_safe_exception_type(exc)}")
        return None


def _generic_yahoo_series(frame, field, ticker):
    """Extract one symbol/field from either a single- or multi-ticker frame."""
    try:
        if frame is None or getattr(frame, "empty", True):
            return None
        block = frame[field] if field in frame else None
        # yfinance has emitted both (field, ticker) and (ticker, field)
        # MultiIndex layouts across releases; support either shape.
        if block is None and hasattr(frame, "columns") and getattr(frame.columns, "nlevels", 1) == 2:
            levels = [list(frame.columns.get_level_values(index)) for index in range(2)]
            if field in levels[1]:
                block = frame.xs(field, axis=1, level=1)
        if block is None:
            return None
        if hasattr(block, "columns"):
            if ticker in block.columns:
                block = block[ticker]
            elif str(ticker).upper() in block.columns:
                block = block[str(ticker).upper()]
            elif len(block.columns) == 1:
                block = block.iloc[:, 0]
            else:
                return None
        series = block.dropna()
        return series if len(series) else None
    except Exception:
        return None


def _generic_yahoo_series_map(tickers, period, fields=("Close",)):
    """Return valid Yahoo series and retry only symbols missing from the batch."""
    requested = list(dict.fromkeys(str(ticker).strip().upper() for ticker in (tickers or []) if str(ticker).strip()))
    batch = _download_generic_yahoo(requested, period)
    result = {field: {} for field in fields}
    missing = set(requested)
    for ticker in requested:
        extracted = {
            field: _generic_yahoo_series(batch, field, ticker)
            for field in fields
        }
        complete = True
        for field, series in extracted.items():
            if series is not None and len(series):
                result[field][ticker] = series
            else:
                complete = False
        if complete:
            missing.discard(ticker)

    # A single-symbol retry fixes transient Yahoo batch omissions such as HON
    # without multiplying requests for symbols that already succeeded.
    for ticker in sorted(missing):
        single = _download_generic_yahoo([ticker], period)
        recovered = False
        for field in fields:
            series = _generic_yahoo_series(single, field, ticker)
            if series is not None and len(series):
                result[field][ticker] = series
                recovered = True
        if not recovered:
            print(f"[generic-cache] No Yahoo price data for {ticker} ({period}); omitting symbol")
    return result

def _fetch_market_data_sync():
    # This card data is generic market context, not user/account data.
    import yfinance as yf

    # 1. Fetch Sector Data
    sector_tickers = MARKET_SECTOR_TICKERS.split()
    sector_closes = _generic_yahoo_series_map(sector_tickers, "5d", fields=("Close",))["Close"]

    sector_data = {}
    for ticker in sector_tickers:
        try:
            valid_closes = sector_closes.get(ticker)
            if valid_closes is not None and len(valid_closes) >= 2:
                current_price = valid_closes.iloc[-1]
                prev_price = valid_closes.iloc[-2]
                pct_change = ((current_price / prev_price) - 1) * 100
                sector_data[ticker] = round(pct_change, 2)
            else:
                sector_data[ticker] = 0.0
        except Exception:
            sector_data[ticker] = 0.0

    sector_summary = {}
    for key, group in MARKET_SECTOR_SUMMARY_GROUPS.items():
        moves = [
            sector_data[ticker] for ticker in group["tickers"]
            if ticker in sector_data
        ]
        positive_count = sum(1 for value in moves if value > 0)
        negative_count = sum(1 for value in moves if value < 0)
        flat_count = max(0, len(moves) - positive_count - negative_count)
        directional_count = positive_count + negative_count
        participation_pct = round((positive_count / directional_count) * 100) if directional_count else 50
        avg_change = round(sum(moves) / len(moves), 2) if moves else 0.0
        benchmark = group["benchmark"]
        benchmark_change = sector_data.get(benchmark)
        sector_summary[key] = {
            "label": group["label"],
            "benchmark": benchmark,
            "benchmark_change_pct": round(float(benchmark_change), 2) if benchmark_change is not None else None,
            "avg_constituent_change_pct": avg_change,
            "positive_count": positive_count,
            "negative_count": negative_count,
            "flat_count": flat_count,
            "participation_pct": participation_pct,
            "analyzed_count": len(moves),
        }

    # 2. Market Breadth: fixed benchmark universe only.
    combined_list = MARKET_BREADTH_UNIVERSE.split()

    advancing = 0
    declining = 0
    unchanged = 0
    new_highs = 0
    new_lows = 0
    above_50 = 0
    above_200 = 0
    ma50_count = 0
    ma200_count = 0
    tickers_list = []

    if len(combined_list) > 0:
        breadth_series = _generic_yahoo_series_map(
            combined_list,
            "1y",
            fields=("Close", "High", "Low"),
        )
        for ticker in combined_list:
            try:
                valid_closes = breadth_series["Close"].get(ticker)
                valid_highs = breadth_series["High"].get(ticker)
                valid_lows = breadth_series["Low"].get(ticker)
                if valid_closes is None or valid_highs is None or valid_lows is None:
                    raise ValueError("no complete OHLC series")

                if len(valid_closes) >= 2:
                    curr = valid_closes.iloc[-1]
                    prev = valid_closes.iloc[-2]

                    is_adv = bool(curr > prev)
                    is_dec = bool(curr < prev)
                    if is_adv:
                        advancing += 1
                    elif is_dec:
                        declining += 1
                    else:
                        unchanged += 1

                    prior_highs = valid_highs.iloc[:-1]
                    prior_lows = valid_lows.iloc[:-1]
                    latest_high = valid_highs.iloc[-1] if len(valid_highs) else curr
                    latest_low = valid_lows.iloc[-1] if len(valid_lows) else curr
                    year_high = prior_highs.max() if len(prior_highs) else valid_highs.max()
                    year_low = prior_lows.min() if len(prior_lows) else valid_lows.min()

                    is_high = bool(latest_high >= year_high)
                    is_low = bool(latest_low <= year_low)
                    if is_high:
                        new_highs += 1
                    if is_low:
                        new_lows += 1

                    above_50_status = None
                    if len(valid_closes) >= 50:
                        ma50_count += 1
                        sma50 = float(valid_closes.iloc[-50:].mean())
                        above_50_status = bool(curr > sma50)
                        if above_50_status:
                            above_50 += 1

                    above_200_status = None
                    if len(valid_closes) >= 200:
                        ma200_count += 1
                        sma200 = float(valid_closes.iloc[-200:].mean())
                        above_200_status = bool(curr > sma200)
                        if above_200_status:
                            above_200 += 1

                    tickers_list.append({
                        "ticker": ticker,
                        "price": round(float(curr), 2),
                        "change_pct": round(float(((curr / prev) - 1) * 100), 2) if prev else 0.0,
                        "above_50": above_50_status,
                        "above_200": above_200_status,
                        "is_high": is_high,
                        "is_low": is_low
                    })
            except Exception:
                pass

    directional_total = advancing + declining
    advance_pct = round((advancing / directional_total) * 100) if directional_total else 50
    above_50_pct = round((above_50 / ma50_count) * 100) if ma50_count else 0
    above_200_pct = round((above_200 / ma200_count) * 100) if ma200_count else 0
    high_low_total = new_highs + new_lows
    high_low_pct = round(50 + (((new_highs - new_lows) / high_low_total) * 50)) if high_low_total else 50
    high_low_pct = max(0, min(100, high_low_pct))
    breadth_score = round(
        (advance_pct * 0.35)
        + (above_50_pct * 0.30)
        + (above_200_pct * 0.25)
        + (high_low_pct * 0.10)
    )
    breadth_data = {
        "advancing": advancing,
        "declining": declining,
        "unchanged": unchanged,
        "advance_pct": advance_pct,
        "new_highs": new_highs,
        "new_lows": new_lows,
        "above_50_pct": above_50_pct,
        "above_200_pct": above_200_pct,
        "high_low_pct": high_low_pct,
        "breadth_score": breadth_score,
        "breadth_score_components": {
            "advance_pct_weight": 35,
            "above_50_pct_weight": 30,
            "above_200_pct_weight": 25,
            "high_low_pct_weight": 10,
        },
        "universe": "Fixed US large-cap benchmark universe",
        "universe_count": len(combined_list),
        "analyzed_count": len(tickers_list),
        "tickers": tickers_list
    }

    return {
        "sectors": sector_data,
        "sector_summary": sector_summary,
        "breadth": breadth_data,
        "cache": {
            "scope": "server",
            "refresh_interval_seconds": GENERIC_DASHBOARD_CARD_CACHE_TTL_SECONDS,
            "fetched_at": datetime.utcnow().isoformat() + "Z",
        }
    }


async def _get_cached_market_data(force: bool = False):
    global _MARKET_DATA_CACHE, _MARKET_DATA_LAST_FETCH
    now = time_module.time()
    expected_tech_count = len(MARKET_SECTOR_SUMMARY_GROUPS.get("tech", {}).get("tickers", []))
    if _MARKET_DATA_CACHE and not force:
        tech_summary = (_MARKET_DATA_CACHE.get("sector_summary") or {}).get("tech", {})
        if tech_summary.get("analyzed_count", 0) < expected_tech_count:
            force = True
    if (
        not force
        and _MARKET_DATA_CACHE
        and now - _MARKET_DATA_LAST_FETCH < GENERIC_DASHBOARD_CARD_CACHE_TTL_SECONDS
    ):
        return _MARKET_DATA_CACHE
    if not force:
        persisted_data, persisted_at = _load_persisted_generic_card("market_data")
        if persisted_data:
            tech_summary = (persisted_data.get("sector_summary") or {}).get("tech", {})
            if tech_summary.get("analyzed_count", 0) >= expected_tech_count:
                _MARKET_DATA_CACHE = _mark_generic_card_from_shared_cache(persisted_data)
                _MARKET_DATA_LAST_FETCH = persisted_at
                return _MARKET_DATA_CACHE

    async with _MARKET_DATA_LOCK:
        now = time_module.time()
        if (
            not force
            and _MARKET_DATA_CACHE
            and now - _MARKET_DATA_LAST_FETCH < GENERIC_DASHBOARD_CARD_CACHE_TTL_SECONDS
        ):
            return _MARKET_DATA_CACHE
        if not force:
            persisted_data, persisted_at = _load_persisted_generic_card("market_data")
            if persisted_data:
                tech_summary = (persisted_data.get("sector_summary") or {}).get("tech", {})
                if tech_summary.get("analyzed_count", 0) >= expected_tech_count:
                    _MARKET_DATA_CACHE = _mark_generic_card_from_shared_cache(persisted_data)
                    _MARKET_DATA_LAST_FETCH = persisted_at
                    return _MARKET_DATA_CACHE
        try:
            data = await asyncio.to_thread(_fetch_market_data_sync)
            _MARKET_DATA_CACHE = data
            _MARKET_DATA_LAST_FETCH = time_module.time()
            await asyncio.to_thread(_save_persisted_generic_card, "market_data", data)
            print("[generic-cache] Refreshed market breadth + sector heatmap cache")
            return data
        except Exception as e:
            print(f"Error fetching market data: {_safe_exception_type(e)}")
            import traceback
            traceback.print_exc()
            if _MARKET_DATA_CACHE:
                stale_data = dict(_MARKET_DATA_CACHE)
                stale_data["cache"] = {
                    **stale_data.get("cache", {}),
                    "stale": True,
                    "last_error": "Market data temporarily unavailable",
                }
                return stale_data
            raise HTTPException(status_code=503, detail="Market data temporarily unavailable")


@app.get("/api/market-data")
async def get_market_data(user: dict = Depends(verify_token), refresh: bool = False):
    return await _get_cached_market_data(force=refresh)


@app.get("/api/quote/{ticker}")
async def get_live_quote(ticker: str, eng: UserEngine = Depends(get_user_engine)):
    """Lightweight quote-only endpoint for visual active-chart candle updates."""
    clean = _normalize_quote_symbol(ticker)
    if not re.fullmatch(r"[A-Z0-9.\-]{1,15}", clean):
        raise HTTPException(status_code=400, detail="Invalid ticker format")

    cache_key = stable_cache_key(eng.uid, clean, "quote", 1)
    cached, cache_age = _quote_cache.peek_with_age(cache_key)
    if cached is not None and cache_age is not None and cache_age < 10:
        quote = {"price": cached, "cache_age": cache_age, "source": "cache", "stale": False}
    else:
        refresh_task = _start_quote_refresh_task(eng, clean)
        if cached is not None and cache_age is not None and cache_age < 300:
            # Keep the dashboard responsive while the provider refreshes this
            # quote once in the background.
            quote = {
                "price": cached,
                "cache_age": cache_age,
                "source": "stale_cache_refreshing",
                "stale": True,
            }
        else:
            try:
                quote = await asyncio.wait_for(asyncio.shield(refresh_task), timeout=3.8)
            except asyncio.TimeoutError:
                raise HTTPException(status_code=503, detail=f"Quote refresh pending for {clean}")

    price = quote.get("price")
    if price is None:
        raise HTTPException(status_code=404, detail=f"Quote unavailable for {clean}")

    # The day change is measured from the previous regular-session close, so it is the same for every chart timeframe.
    # It is cached for 30 minutes; the first request may wait briefly for it, never long enough to delay the price.
    try:
        previous_close = await asyncio.wait_for(asyncio.to_thread(_get_previous_close, clean), timeout=2.0)
    except Exception:
        previous_close = None

    return {
        "ticker": clean,
        "price": float(price),
        **_quote_change_fields(price, previous_close),
        "price_display": _format_quote_price(clean, price),
        "server_time": get_now().isoformat(),
        "cache_age_seconds": round(float(quote.get("cache_age") or 0.0), 1),
        "stale": bool(quote.get("stale")),
        "source": quote.get("source") or "unavailable",
    }


_macro_calendar_cache = {"data": [], "timestamp": 0}

async def _fetch_macro_events_uncached():
    from macro_service import get_macro_data

    def fetch_calendar():
        global _macro_calendar_cache
        import time
        now = time.time()
        # Cache the remote calendars separately because the providers change slowly.
        if now - _macro_calendar_cache["timestamp"] < 3600 and _macro_calendar_cache["data"]:
            return _macro_calendar_cache["data"]

        import requests
        import macro_schedule
        headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'}

        # Expand to High/Medium USD and High major global economies
        def is_relevant(e):
            if e.get('country') == 'USD' and e.get('impact') in ['High', 'Medium']:
                return True
            if e.get('country') in ['EUR', 'GBP', 'CNY', 'JPY'] and e.get('impact') == 'High':
                return True
            return False

        def fetch_feed_week(name):
            r = requests.get(f"https://nfs.faireconomy.media/ff_calendar_{name}.json", headers=headers, timeout=5)
            r.raise_for_status()
            return [e for e in r.json() if is_relevant(e)]

        # All downloads run in parallel, so a slow source costs one timeout rather than the sum of all of them.
        # shutdown(wait=False): on an early return the official fetch finishes in the background and warms its cache.
        today_et = datetime.now(macro_schedule.EASTERN).date()
        pool = ThreadPoolExecutor(max_workers=3, thread_name_prefix="macro-calendar")
        try:
            week_futures = {name: pool.submit(fetch_feed_week, name) for name in ("thisweek", "nextweek")}
            official_future = pool.submit(macro_schedule.fetch_official_events, today_et)
            feed_events = []
            feed_ok = False
            for name, future in week_futures.items():
                try:
                    feed_events += future.result()
                    feed_ok = feed_ok or name == "thisweek"
                except Exception as e:
                    # The next-week file is only published late in the week, so a 404 there is normal.
                    if name == "thisweek":
                        print(f"Error fetching forex calendar: {_safe_exception_type(e)}")

            # The forecast feed is the primary source. When it fails, keep serving the last good calendar rather than
            # rebuilding from official schedules alone (the weekly claims rule always yields an event, so "nothing
            # new" can never be detected from the official side).
            if not feed_ok and _macro_calendar_cache["data"]:
                return _macro_calendar_cache["data"]

            try:
                official_events, official_status = official_future.result()
            except Exception as e:
                official_events, official_status = [], {"all": f"unavailable ({_safe_exception_type(e)})"}
        finally:
            pool.shutdown(wait=False)
        for source_name, state in official_status.items():
            if state != "ok":
                print(f"Official macro schedule {source_name}: {state}")
        # Same impact policy as the feed: official releases outside High/Medium are not shown.
        official_events = [e for e in official_events if is_relevant(e)]
        # Date ranges each source just returned in full; stored future entries missing there were moved or cancelled.
        coverage = macro_schedule.fetch_coverage(today_et, feed_events if feed_ok else None, official_status)

        history_path = os.path.join(GENERIC_DASHBOARD_CARD_CACHE_DIR, "macro_calendar_history.json")
        stored = [
            e for e in macro_schedule.load_history(history_path)
            if isinstance(e, dict) and (not e.get("scheduled_only") or is_relevant(e))
        ]
        if not feed_ok and not stored:
            return _macro_calendar_cache["data"]

        try:
            def get_timestamp(ev):
                try:
                    return datetime.fromisoformat(ev.get('date', '')).timestamp()
                except Exception:
                    return 0
            merged = macro_schedule.merge_official_into_calendar(feed_events, official_events)
            # Official-schedule entries have no forecast; the calendar-source enrichment would
            # mislabel them (e.g. one CPI row standing in for both m/m and y/y), so keep them apart.
            enrichable = [e for e in merged if not e.get("scheduled_only")]
            scheduled = [e for e in merged if e.get("scheduled_only")]
            fresh = enrich_macro_calendar_events(enrichable) + scheduled
            combined = macro_schedule.merge_history(stored, fresh, coverage=coverage)
            result = macro_schedule.prune_stored_for_display(combined)
            result.sort(key=get_timestamp)
            for event in result:
                event["why"] = why_it_matters(event.get("title", ""))
                if event.get("scheduled_only") and not event.get("actual") and event.get("actual_status") is None:
                    event["actual_status"] = "not_available"
                    event["actual_unavailable_reason"] = "Date from the official release schedule; no forecast or actual in this feed."
            if not feed_ok:
                # Cold start during a feed outage: serve stored history plus official dates, but do not persist
                # this partial view and retry the feed in 5 minutes instead of the usual hour.
                _macro_calendar_cache["data"] = result
                _macro_calendar_cache["timestamp"] = now - 3600 + 300
                return result
            try:
                macro_schedule.save_history(history_path, combined)
            except Exception as e:
                print(f"Error saving macro calendar history: {_safe_exception_type(e)}")

            _macro_calendar_cache["data"] = result
            _macro_calendar_cache["timestamp"] = now
            return result
        except Exception as e:
            print(f"Error building macro calendar: {_safe_exception_type(e)}")
            return _macro_calendar_cache["data"]

    indicators_task = asyncio.to_thread(get_macro_data, False)
    calendar_task = asyncio.to_thread(fetch_calendar)
    indicators, calendar = await asyncio.gather(indicators_task, calendar_task)
    return {
        "status": "success",
        "indicators": indicators,
        "backdrop": macro_backdrop(indicators if isinstance(indicators, dict) else {}),
        "calendar": calendar,
        "cache": {
            "scope": "server",
            "refresh_interval_seconds": GENERIC_DASHBOARD_CARD_CACHE_TTL_SECONDS,
            "fetched_at": datetime.utcnow().isoformat() + "Z",
        }
    }


async def _get_cached_macro_events(force: bool = False):
    global _MACRO_EVENTS_CACHE, _MACRO_EVENTS_LAST_FETCH
    now = time_module.time()
    if (
        not force
        and _MACRO_EVENTS_CACHE
        and now - _MACRO_EVENTS_LAST_FETCH < GENERIC_DASHBOARD_CARD_CACHE_TTL_SECONDS
    ):
        return _MACRO_EVENTS_CACHE
    if not force:
        persisted_data, persisted_at = _load_persisted_generic_card("macro_events")
        if persisted_data:
            _MACRO_EVENTS_CACHE = _mark_generic_card_from_shared_cache(persisted_data)
            _MACRO_EVENTS_LAST_FETCH = persisted_at
            return _MACRO_EVENTS_CACHE

    async with _MACRO_EVENTS_LOCK:
        now = time_module.time()
        if (
            not force
            and _MACRO_EVENTS_CACHE
            and now - _MACRO_EVENTS_LAST_FETCH < GENERIC_DASHBOARD_CARD_CACHE_TTL_SECONDS
        ):
            return _MACRO_EVENTS_CACHE
        if not force:
            persisted_data, persisted_at = _load_persisted_generic_card("macro_events")
            if persisted_data:
                _MACRO_EVENTS_CACHE = _mark_generic_card_from_shared_cache(persisted_data)
                _MACRO_EVENTS_LAST_FETCH = persisted_at
                return _MACRO_EVENTS_CACHE
        try:
            data = await _fetch_macro_events_uncached()
            _MACRO_EVENTS_CACHE = data
            _MACRO_EVENTS_LAST_FETCH = time_module.time()
            await asyncio.to_thread(_save_persisted_generic_card, "macro_events", data)
            print("[generic-cache] Refreshed macro events cache")
            return data
        except Exception as e:
            print(f"Error fetching macro events: {_safe_exception_type(e)}")
            if _MACRO_EVENTS_CACHE:
                stale_data = dict(_MACRO_EVENTS_CACHE)
                stale_data["cache"] = {
                    **stale_data.get("cache", {}),
                    "stale": True,
                    "last_error": "Macro calendar temporarily unavailable",
                }
                return stale_data
            return {"status": "error", "message": "Macro calendar temporarily unavailable", "indicators": {}, "calendar": []}


def _fetch_fear_greed_sync():
    import requests

    url = "https://feargreedchart.com/api/"
    headers = {'User-Agent': 'Mozilla/5.0 (compatible; BotBulls/2.0)'}
    response = requests.get(url, headers=headers, timeout=8)
    response.raise_for_status()
    raw = response.json()
    score_block = raw.get("score") or {}
    return {
        "status": "success",
        "score": score_block.get("score"),
        "components": score_block.get("components") or [],
        "raw_score": score_block,
        "source": "feargreedchart.com",
        "cache": {
            "scope": "server",
            "refresh_interval_seconds": GENERIC_DASHBOARD_CARD_CACHE_TTL_SECONDS,
            "fetched_at": datetime.utcnow().isoformat() + "Z",
        }
    }


async def _get_cached_fear_greed(force: bool = False):
    global _FEAR_GREED_CACHE, _FEAR_GREED_LAST_FETCH
    now = time_module.time()
    if (
        not force
        and _FEAR_GREED_CACHE
        and now - _FEAR_GREED_LAST_FETCH < GENERIC_DASHBOARD_CARD_CACHE_TTL_SECONDS
    ):
        return _FEAR_GREED_CACHE
    if not force:
        persisted_data, persisted_at = _load_persisted_generic_card("fear_greed")
        if persisted_data:
            _FEAR_GREED_CACHE = _mark_generic_card_from_shared_cache(persisted_data)
            _FEAR_GREED_LAST_FETCH = persisted_at
            return _FEAR_GREED_CACHE

    async with _FEAR_GREED_LOCK:
        now = time_module.time()
        if (
            not force
            and _FEAR_GREED_CACHE
            and now - _FEAR_GREED_LAST_FETCH < GENERIC_DASHBOARD_CARD_CACHE_TTL_SECONDS
        ):
            return _FEAR_GREED_CACHE
        if not force:
            persisted_data, persisted_at = _load_persisted_generic_card("fear_greed")
            if persisted_data:
                _FEAR_GREED_CACHE = _mark_generic_card_from_shared_cache(persisted_data)
                _FEAR_GREED_LAST_FETCH = persisted_at
                return _FEAR_GREED_CACHE
        try:
            data = await asyncio.to_thread(_fetch_fear_greed_sync)
            _FEAR_GREED_CACHE = data
            _FEAR_GREED_LAST_FETCH = time_module.time()
            await asyncio.to_thread(_save_persisted_generic_card, "fear_greed", data)
            print("[generic-cache] Refreshed Fear & Greed cache")
            return data
        except Exception as e:
            print(f"Error fetching Fear & Greed: {_safe_exception_type(e)}")
            if _FEAR_GREED_CACHE:
                stale_data = dict(_FEAR_GREED_CACHE)
                stale_data["cache"] = {
                    **stale_data.get("cache", {}),
                    "stale": True,
                    "last_error": _safe_exception_type(e),
                }
                return stale_data
            return {
                "status": "error",
                "message": "Fear & Greed data temporarily unavailable",
                "score": None,
                "components": [],
                "source": "feargreedchart.com",
            }


async def _refresh_generic_dashboard_card_caches(force: bool = False):
    results = await asyncio.gather(
        _get_cached_fear_greed(force=force),
        _get_cached_market_data(force=force),
        _get_cached_macro_events(force=force),
        return_exceptions=True,
    )
    for name, result in zip(("fear_greed", "market_data", "macro_events"), results):
        if isinstance(result, Exception):
            print(f"[generic-cache] {name} refresh failed: {result}")


async def _generic_dashboard_card_cache_loop():
    await asyncio.sleep(2)
    while True:
        await _refresh_generic_dashboard_card_caches(force=False)
        await asyncio.sleep(GENERIC_DASHBOARD_CARD_CACHE_TTL_SECONDS)


@app.get("/api/macro-events")
async def get_macro_events(user: dict = Depends(verify_token)):
    return await _get_cached_macro_events(force=False)


@app.get("/api/fear-greed")
async def get_fear_greed(user: dict = Depends(verify_token)):
    return await _get_cached_fear_greed(force=False)


@app.get("/api/generic-dashboard-cards")
async def get_generic_dashboard_cards(user: dict = Depends(verify_token)):
    try:
        # A cold cache makes one card wait on a slow upstream. Answer within a bound with whatever is ready; the
        # shielded refresh keeps running and fills the shared cache, and the page asks again for any missing card.
        async def bounded(coro):
            task = asyncio.ensure_future(coro)
            try:
                return await asyncio.wait_for(asyncio.shield(task), timeout=3.5)
            except asyncio.TimeoutError:
                return None

        fear_greed, market_data, macro_events = await asyncio.gather(
            bounded(_get_cached_fear_greed(force=False)),
            bounded(_get_cached_market_data(force=False)),
            bounded(_get_cached_macro_events(force=False)),
        )
        return {
            "status": "success",
            "fear_greed": fear_greed,
            "market_data": market_data,
            "macro_events": macro_events,
            "cache": {
                "scope": "server",
                "refresh_interval_seconds": GENERIC_DASHBOARD_CARD_CACHE_TTL_SECONDS,
                "served_at": datetime.utcnow().isoformat() + "Z",
            }
        }
    except Exception as e:
        print(f"Error fetching generic dashboard cards: {_safe_exception_type(e)}")
        return {"status": "error", "message": "Dashboard cards temporarily unavailable"}


def _get_threshold_setting(settings, keys, default_value):
    for key in keys:
        value = (settings or {}).get(key)
        if value is None or value == "":
            continue
        try:
            return int(value)
        except (TypeError, ValueError):
            continue
    try:
        return int(default_value)
    except (TypeError, ValueError):
        return 1


def _normalize_signal_threshold(value, selected_count, default_value):
    if selected_count <= 0:
        return 0

    try:
        threshold = int(value)
    except (TypeError, ValueError):
        threshold = 0

    try:
        default_threshold = int(default_value)
    except (TypeError, ValueError):
        default_threshold = 1

    if threshold <= 0:
        threshold = min(default_threshold, selected_count)

    return max(1, min(threshold, selected_count))


def _normalize_bot_signal_thresholds(settings, eng):
    indicators = settings.get("indicators")
    if not isinstance(indicators, list):
        return False

    before = (settings.get("min_buy_signals"), settings.get("min_sell_signals"))
    selected_count = len(indicators)
    buy_default = getattr(eng.config, "MIN_BULLISH_SIGNALS", getattr(get_user_config(), "MIN_BULLISH_SIGNALS", 1))
    sell_default = getattr(eng.config, "MIN_BEARISH_SIGNALS", getattr(get_user_config(), "MIN_BEARISH_SIGNALS", 1))
    settings["min_buy_signals"] = _normalize_signal_threshold(settings.get("min_buy_signals"), selected_count, buy_default)
    settings["min_sell_signals"] = _normalize_signal_threshold(settings.get("min_sell_signals"), selected_count, sell_default)
    return before != (settings.get("min_buy_signals"), settings.get("min_sell_signals"))


async def _repair_bot_signal_thresholds_if_needed(eng):
    repaired = False
    for settings in eng.config.TICKER_SETTINGS.values():
        if _normalize_bot_signal_thresholds(settings, eng):
            repaired = True
    if repaired:
        _queue_settings_save(eng, "repair bot signal thresholds")
        print("[settings] Repaired zero/invalid bot signal thresholds")


def _build_indicator_forecast(settings, scan_data, mode="entry", is_long=True, is_option=False, options_type="calls_only", eng=None):
    if not scan_data: return ""
    options_type = normalize_options_type(options_type, default="calls_only")
    if is_option and options_type not in VALID_OPTION_DIRECTIONS:
        return "Legacy Both direction disabled. Choose CALL or PUT."
    signals = scan_data.get('signals', {})

    # active_signals filter only those with enabled=True (set by trader.py)
    active_signals = {k: v for k, v in signals.items() if v.get('enabled', False)}

    if not active_signals:
        return ""

    parts = []

    cfg = getattr(eng, "config", None)
    user_cfg = get_user_config()
    buy_default = getattr(cfg, 'MIN_BULLISH_SIGNALS', getattr(user_cfg, 'MIN_BULLISH_SIGNALS', 1))
    sell_default = getattr(cfg, 'MIN_BEARISH_SIGNALS', getattr(user_cfg, 'MIN_BEARISH_SIGNALS', 1))
    buy_threshold = _get_threshold_setting(settings, ('min_buy_signals', 'threshold', 'buy_threshold'), buy_default)
    sell_threshold = _get_threshold_setting(settings, ('min_sell_signals', 'sell_threshold'), sell_default)

    if mode == "entry":
        target = "BULLISH"
        if is_option and options_type == "puts_only":
            target = "BEARISH"
        target_label = "PUT" if target == "BEARISH" and is_option else "CALL" if is_option else target
        entry_threshold = sell_threshold if target == "BEARISH" else buy_threshold

        current_count = 0
        for name, data in active_signals.items():
            sig = data.get('signal', 'NEUTRAL')
            if sig == target:
                current_count += data.get('weight', 1)

        rem = max(0, entry_threshold - current_count)
        parts.append(f"[{target_label} ENTRY TARGET: {entry_threshold} | REMAINING: {rem}]")

        for name, data in active_signals.items():
            sig = data.get('signal', 'NEUTRAL')
            reason = data.get('reason', '')
            if sig == target:
                icon = "🔴" if sig == "BEARISH" else "✅"
                parts.append(f"{icon} {name} {sig} ({reason})")
            else:
                parts.append(f"⏳ {name} {sig} ({reason})")
    else:
        target = "BEARISH" if is_long else "BULLISH"
        current_count = 0
        for name, data in active_signals.items():
            if data.get('signal') == target:
                current_count += data.get('weight', 1)

        rem = max(0, sell_threshold - current_count)
        parts.append(f"[{target} EXIT TARGET: {sell_threshold} | REMAINING: {rem}]")

        for name, data in active_signals.items():
            sig = data.get('signal', 'NEUTRAL')
            reason = data.get('reason', '')
            if sig == target:
                icon = "🔴" if sig == "BEARISH" else "✅"
                parts.append(f"{icon} {name} {sig} ({reason})")
            else:
                parts.append(f"⏳ {name} {sig} ({reason})")

    return " | ".join(parts)


def _build_swing_forecast(settings, scan_data, mode="entry", is_long=True, is_option=False, options_type="calls_only", eng=None):
    if not scan_data:
        return ""

    price_history = scan_data.get("price_history", [])
    current_price = scan_data.get("price_raw") or scan_data.get("price") or 0.0
    try:
        current_price = float(str(current_price).replace("$", "").replace(",", ""))
    except Exception:
        current_price = 0.0

    market_session = settings.get("market_session", "Regular")
    is_crypto = any(str(scan_data.get("ticker", "")).upper().endswith(base) for base in ["USD", "USDT", "USDC"]) or str(scan_data.get("ticker", "")).upper() in ["BTC", "ETH", "XRP", "DOGE", "LTC", "ADA", "SOL"]

    if mode == "entry":
        lookback_days = int(
            settings.get(
                "swing_entry_lookback_days",
                settings.get("swing_lookback_days", 20),
            ) or 20
        )
        entry_mode = settings.get("swing_entry_mode", "bounce_retest")
        swing_decision = detect_swing_entry(
            price_history,
            current_price=current_price,
            entry_mode=entry_mode,
            lookback_days=lookback_days,
            market_session=market_session,
            is_crypto=is_crypto,
        )
        ctx = swing_decision.get("context", {})
        support = ctx.get("nearest_support")
        resistance = ctx.get("nearest_resistance")
        parts = [
            f"🔮 Live Preview: {scan_data.get('ticker', 'Example')} (Entry Swing) | Price: ${current_price:.2f}",
            f"[SWING ENTRY: {entry_mode.upper()} | LOOKBACK: {lookback_days}D]",
            f"Support: ${support['price']:.2f}" if support else "Support: N/A",
            f"Resistance: ${resistance['price']:.2f}" if resistance else "Resistance: N/A",
            swing_decision.get("reason", "Swing Points: waiting"),
        ]
        if is_option:
            parts.insert(1, f"Options: {options_type.upper()}")
        return " | ".join(parts)

    lookback_days = int(
        settings.get(
            "swing_exit_lookback_days",
            settings.get("swing_lookback_days", 20),
        ) or 20
    )
    swing_exit = build_swing_exit(
        price_history,
        current_price=current_price,
        is_long=is_long,
        lookback_days=lookback_days,
        market_session=market_session,
        is_crypto=is_crypto,
    )
    symbol = scan_data.get("ticker", "")
    return format_swing_preview(
        symbol=symbol,
        swing_exit=swing_exit,
        current_price=current_price,
        is_long=is_long,
        is_option=is_option,
    )


def _entry_forecast_float(value, default=0.0):
    try:
        return float(str(value).replace("$", "").replace(",", ""))
    except (TypeError, ValueError):
        return default


def _normalize_pct_setting(value, default=0.0):
    pct = _entry_forecast_float(value, default)
    if pct is None:
        pct = default
    if pct > 1:
        pct = pct / 100.0
    return max(0.0, pct)


def _option_pct_exit_settings(settings):
    return (
        _normalize_pct_setting(settings.get('options_stop_loss_pct', settings.get('regular_stop_loss_pct', 0.10)), 0.10),
        _normalize_pct_setting(settings.get('options_take_profit_pct', settings.get('regular_take_profit_pct', 0.20)), 0.20),
        _normalize_pct_setting(settings.get('options_trailing_pct', settings.get('regular_trailing_pct', 0.10)), 0.10),
    )


def _entry_forecast_money(value):
    value = _entry_forecast_float(value, None)
    return "N/A" if value is None else f"${value:.2f}"


def _entry_card_prior_day(scan_data, entry_signal):
    prior = (scan_data or {}).get("prior_day_ohlc")
    if prior:
        return prior

    ctx = (entry_signal or {}).get("context") or {}
    candles = ctx.get("candles") or []
    return _get_prior_day_ohlc(candles)


def _entry_strategy_label(buy_mode):
    labels = {
        "strategy_preset": "Strategy",
        "ai_autopilot": "AI Autopilot",
        "indicator": "Indicator",
        "custom_price": "Custom Price",
        "atr": "ATR",
        "hybrid_atr": "Hybrid ATR",
        "percentage": "Percentage",
        "hybrid_pct": "Hybrid Percentage",
        "pivot_points": "Pivot Points",
        "hybrid_pivot": "Hybrid Pivot",
        "high_low": "Prior Day High/Low",
        "hybrid_high_low": "Hybrid High/Low",
        "swing_points": "Swing Points",
        "hybrid_swing": "Hybrid (Indicator + Swing)",
    }
    return labels.get(buy_mode or "indicator", str(buy_mode or "indicator").replace("_", " ").title())


def _exit_strategy_label(sell_mode):
    labels = {
        "strategy_preset": "Strategy",
        "ai_autopilot": "AI Autopilot",
        "indicator": "Indicator",
        "custom_sltp": "Custom SL/TP",
        "custom_underlying_sltp": "Custom Underlying SL/TP",
        "atr": "ATR",
        "sltp": "ATR Stop / Take Profit",
        "hybrid": "Hybrid (Indicator + ATR)",
        "hybrid_atr": "Hybrid (Indicator + ATR)",
        "percentage": "Percentage",
        "hybrid_pct": "Hybrid (Indicator + Percentage)",
        "pivot_points": "Pivot Points",
        "hybrid_pivot": "Hybrid (Indicator + Pivot)",
        "high_low": "Prior Day High/Low",
        "hybrid_high_low": "Hybrid (Indicator + High/Low)",
        "swing_points": "Swing Points",
        "hybrid_swing": "Hybrid (Indicator + Swing)",
        "premium_pct": "Premium Percentage",
    }
    return labels.get(sell_mode or "indicator", str(sell_mode or "indicator").replace("_", " ").title())


def _format_entry_card_forecast(settings, scan_data, entry_signal, current_price, buy_mode, is_option=False, options_type="calls_only"):
    options_type = normalize_options_type(options_type, default="calls_only")
    if is_option and options_type not in VALID_OPTION_DIRECTIONS:
        return "Legacy Both direction disabled. Choose CALL or PUT."
    ctx = (entry_signal or {}).get("context") or {}
    reason = (entry_signal or {}).get("reason") or "Entry strategy: waiting for confirmation"
    action = (entry_signal or {}).get("action", "HOLD")
    status = "Ready" if action in ("BUY", "SELL") else "Waiting"
    mode = buy_mode or "indicator"
    parts = [_entry_strategy_label(mode)]

    if is_option:
        parts.append(f"[Options] {options_type_label(options_type)}")

    if mode == "custom_price":
        target = settings.get("custom_buy_price", (entry_signal or {}).get("trigger_price"))
        label = "PUT Target Entry At/Above" if is_option and options_type == "puts_only" else "CALL Target Entry At/Below" if is_option else "Custom Entry"
        parts.extend([
            f"[{label}] {_entry_forecast_money(target)}",
            f"[Status] {status}",
            reason,
        ])
        return " | ".join(parts)

    if mode in ("atr", "hybrid_atr"):
        atr_val = _entry_forecast_float(scan_data.get("atr"), 0.0)
        if atr_val <= 0:
            atr_val = _entry_forecast_float(ctx.get("average_range"), 0.0)
        mult = _entry_forecast_float(settings.get("entry_atr_multiplier", settings.get("atr_stop_multiplier", 2.0)), 2.0)
        style = settings.get("entry_atr_style", "pullback_reclaim")
        prev_close = current_price
        candles = ctx.get("candles") or []
        if len(candles) >= 2:
            prev_close = _entry_forecast_float(candles[-2].get("close"), current_price)
        offset = max(atr_val * max(mult, 0.1), current_price * 0.001)
        long_level = prev_close + offset if style == "breakout_momentum" else prev_close - offset
        short_level = prev_close - offset if style == "breakout_momentum" else prev_close + offset
        label = "ATR Buy Above" if style == "breakout_momentum" else "ATR Buy Below"
        style_label = "Breakout / Momentum" if style == "breakout_momentum" else "Pullback / Reclaim"
        if mode == "hybrid_atr":
            parts.append("[Hybrid] indicator + ATR setup required")
        if is_option:
            call_label = "CALL Above" if style == "breakout_momentum" else "CALL Reclaim From"
            put_label = "PUT Below" if style == "breakout_momentum" else "PUT Rejection From"
            if options_type == "calls_only":
                parts.append(f"[{call_label}] {_entry_forecast_money(long_level)}")
            elif options_type == "puts_only":
                parts.append(f"[{put_label}] {_entry_forecast_money(short_level)}")
            parts.extend([
                f"[Style] {style_label}",
                f"[ATR] x{mult:.1f}",
                reason,
            ])
        else:
            parts.extend([
                f"[{label}] {_entry_forecast_money(long_level)}",
                f"[Style] {style_label}",
                f"[ATR] x{mult:.1f}",
                reason,
            ])
        return " | ".join(parts)

    if mode in ("percentage", "hybrid_pct"):
        pct = _entry_forecast_float(settings.get("entry_pct_move", max(1.0, _entry_forecast_float(settings.get("regular_stop_loss_pct"), 0.05) * 100.0)), 1.0)
        style = settings.get("entry_pct_style", "dip_reclaim")
        prev_close = current_price
        candles = ctx.get("candles") or []
        if len(candles) >= 2:
            prev_close = _entry_forecast_float(candles[-2].get("close"), current_price)
        long_level = prev_close * (1.0 + (pct / 100.0)) if style == "breakout_momentum" else prev_close * (1.0 - (pct / 100.0))
        short_level = prev_close * (1.0 - (pct / 100.0)) if style == "breakout_momentum" else prev_close * (1.0 + (pct / 100.0))
        label = "PCT Buy Above" if style == "breakout_momentum" else "PCT Buy Below"
        style_label = "Breakout / Momentum" if style == "breakout_momentum" else ("Reversal / Reclaim" if is_option else "Dip / Reclaim")
        if mode == "hybrid_pct":
            parts.append("[Hybrid] indicator + percentage setup required")
        if is_option:
            call_label = "CALL Above" if style == "breakout_momentum" else "CALL Reclaim From"
            put_label = "PUT Below" if style == "breakout_momentum" else "PUT Rejection From"
            if options_type == "calls_only":
                parts.append(f"[{call_label}] {_entry_forecast_money(long_level)}")
            elif options_type == "puts_only":
                parts.append(f"[{put_label}] {_entry_forecast_money(short_level)}")
            parts.extend([
                f"[Style] {style_label}",
                f"[Move] {pct:.1f}%",
                reason,
            ])
        else:
            parts.extend([
                f"[{label}] {_entry_forecast_money(long_level)}",
                f"[Style] {style_label}",
                f"[Move] {pct:.1f}%",
                reason,
            ])
        return " | ".join(parts)

    if mode in ("pivot_points", "hybrid_pivot"):
        prior = _entry_card_prior_day(scan_data, entry_signal)
        if mode == "hybrid_pivot":
            parts.append("[Hybrid] indicator + pivot setup required")
        if prior:
            high = _entry_forecast_float(prior.get("high"))
            low = _entry_forecast_float(prior.get("low"))
            close = _entry_forecast_float(prior.get("close"))
            pivot = (high + low + close) / 3.0
            r1 = (2 * pivot) - low
            s1 = (2 * pivot) - high
            parts.extend([
                f"[Pivot] {_entry_forecast_money(pivot)}",
                f"[CALL Support S1] {_entry_forecast_money(s1)}" if is_option and options_type == "calls_only" else f"[PUT Resistance R1] {_entry_forecast_money(r1)}" if is_option and options_type == "puts_only" else f"[Buy Support S1] {_entry_forecast_money(s1)}",
                f"[CALL Breakout R1] {_entry_forecast_money(r1)}" if is_option and options_type == "calls_only" else f"[PUT Breakdown Pivot] {_entry_forecast_money(pivot)}" if is_option and options_type == "puts_only" else f"[Breakout R1] {_entry_forecast_money(r1)}",
                reason,
            ])
        else:
            parts.extend(["[Pivot Entry] Awaiting prior day data", reason])
        return " | ".join(parts)

    if mode in ("high_low", "hybrid_high_low"):
        prior = _entry_card_prior_day(scan_data, entry_signal)
        if mode == "hybrid_high_low":
            parts.append("[Hybrid] indicator + high/low setup required")
        levels = buffered_prior_day_levels(prior, resolve_high_low_buffer_pct(settings)) if prior else None
        if levels:
            trigger_label = (
                f"[CALL Breakout] Trigger {_entry_forecast_money(levels.get('high_trigger'))}"
                if is_option and options_type == "calls_only"
                else f"[PUT Breakdown] Trigger {_entry_forecast_money(levels.get('low_trigger'))}"
                if is_option and options_type == "puts_only"
                else f"[Buy Breakout] Trigger {_entry_forecast_money(levels.get('high_trigger'))}"
            )
            parts.extend([
                trigger_label,
                f"[PDH] {_entry_forecast_money(levels.get('high'))}",
                f"[PDL] {_entry_forecast_money(levels.get('low'))}",
                f"[Buffer] {levels.get('buffer_pct', 0.0):.2f}%",
                reason,
            ])
        else:
            parts.extend(["[High/Low Entry] Awaiting prior day data", reason])
        return " | ".join(parts)

    if mode in ("swing_points", "hybrid_swing"):
        support = ctx.get("nearest_support")
        resistance = ctx.get("nearest_resistance")
        entry_mode = settings.get("swing_entry_mode", "bounce_retest").replace("_", " ").title()
        lookback = settings.get("swing_entry_lookback_days", settings.get("swing_lookback_days", 20))
        if mode == "hybrid_swing":
            parts.append("[Hybrid] indicator + swing setup required")
        parts.append(f"[Swing Entry] {entry_mode} | {lookback}D")
        parts.append(f"[Support] {_entry_forecast_money(support.get('price'))}" if support else "[Support] N/A")
        parts.append(f"[Resistance] {_entry_forecast_money(resistance.get('price'))}" if resistance else "[Resistance] N/A")
        parts.append(reason)
        return " | ".join(parts)

    return reason


def _build_entry_forecast(settings, scan_data, is_option=False, options_type="calls_only", eng=None, for_card=False):
    if not scan_data:
        return ""
    options_type = normalize_options_type(options_type, default="calls_only")
    if is_option and options_type not in VALID_OPTION_DIRECTIONS:
        return "Legacy Both direction disabled. Choose CALL or PUT."

    price_history = scan_data.get("price_history", [])
    current_price = scan_data.get("price_raw") or scan_data.get("price") or 0.0
    try:
        current_price = float(str(current_price).replace("$", "").replace(",", ""))
    except Exception:
        current_price = 0.0

    buy_mode = settings.get("buy_mode", "indicator")
    if buy_mode == "indicator":
        indicator_forecast = _build_indicator_forecast(
            settings,
            scan_data,
            mode="entry",
            is_long=True,
            is_option=is_option,
            options_type=options_type,
            eng=eng,
        )
        if for_card:
            parts = [_entry_strategy_label(buy_mode)]
            if is_option:
                parts.append(f"[Options] {options_type_label(options_type)}")
            if indicator_forecast:
                parts.append(indicator_forecast)
            return " | ".join(parts)
        return indicator_forecast

    lookback_days = int(
        settings.get(
            "swing_entry_lookback_days",
            settings.get("swing_lookback_days", 20),
        ) or 20
    )
    market_session = settings.get("market_session", "Regular")
    is_crypto = any(str(scan_data.get("ticker", "")).upper().endswith(base) for base in ["USD", "USDT", "USDC"]) or str(scan_data.get("ticker", "")).upper() in ["BTC", "ETH", "XRP", "DOGE", "LTC", "ADA", "SOL"]
    entry_signal = build_entry_signal(
        price_history,
        current_price=current_price,
        entry_mode=buy_mode,
        lookback_days=lookback_days,
        market_session=market_session,
        is_crypto=is_crypto,
        atr=scan_data.get("atr", 0.0),
        custom_buy_price=settings.get("custom_buy_price", 0.0),
        entry_atr_mult=float(settings.get("entry_atr_multiplier", settings.get("atr_stop_multiplier", 2.0)) or 2.0),
        entry_pct_move=float(settings.get("entry_pct_move", max(1.0, float(settings.get("regular_stop_loss_pct", 0.05)) * 100.0)) or 1.0),
        entry_atr_style=settings.get("entry_atr_style", "pullback_reclaim"),
        entry_pct_style=settings.get("entry_pct_style", "dip_reclaim"),
        swing_entry_mode=settings.get("swing_entry_mode", "bounce_retest"),
        desired_direction=("BULLISH" if is_option and options_type == "calls_only" else "BEARISH" if is_option and options_type == "puts_only" else None),
        high_low_buffer_pct=resolve_high_low_buffer_pct(settings),
    )
    if for_card:
        return _format_entry_card_forecast(
            settings,
            scan_data,
            entry_signal,
            current_price,
            buy_mode,
            is_option=is_option,
            options_type=options_type,
        )

    return format_entry_preview(
        symbol=scan_data.get("ticker", "Example"),
        entry_signal=entry_signal,
        current_price=current_price,
        entry_mode=buy_mode,
        is_option=is_option,
        high_low_buffer_pct=resolve_high_low_buffer_pct(settings),
    )


_FORECAST_HASH_KEYS = (
    "ai_autopilot",
    "buy_mode",
    "sell_mode",
    "options_exit_strategy",
    "options_trading",
    "options_type",
    "timeframe",
    "indicators",
    "min_buy_signals",
    "min_sell_signals",
    "threshold",
    "sell_threshold",
    "custom_buy_price",
    "custom_stop_loss_price",
    "custom_take_profit_price",
    "custom_underlying_stop_loss_price",
    "custom_underlying_take_profit_price",
    "entry_atr_multiplier",
    "entry_pct_move",
    "entry_atr_style",
    "entry_pct_style",
    "swing_entry_mode",
    "swing_entry_lookback_days",
    "swing_exit_lookback_days",
    "atr_stop_multiplier",
    "atr_trail_multiplier",
    "take_profit_multiplier",
    "regular_stop_loss_pct",
    "regular_take_profit_pct",
    "regular_trailing_pct",
    "options_stop_loss_pct",
    "options_take_profit_pct",
    "options_trailing_pct",
    "enable_atr_trail",
    "enable_pct_trail",
    "market_session",
    *_GRID_CONFIGURATION_KEYS,
)

_PURE_STRUCTURAL_ENTRY_MODES = {
    "swing_points",
    "atr",
    "percentage",
    "pivot_points",
    "high_low",
    "custom_price",
}

_PURE_STRUCTURAL_EXIT_MODES = {
    "swing_points",
    "atr",
    "sltp",
    "percentage",
    "pivot_points",
    "high_low",
    "custom_sltp",
    "custom_underlying_sltp",
    "trend_rider",
}

_HYBRID_ENTRY_MODES = {
    "hybrid_atr",
    "hybrid_pct",
    "hybrid_pivot",
    "hybrid_high_low",
    "hybrid_swing",
}

_HYBRID_EXIT_MODES = {
    "hybrid",
    "hybrid_atr",
    "hybrid_pct",
    "hybrid_pivot",
    "hybrid_high_low",
    "hybrid_swing",
}


def _screener_scan_interval_seconds(settings: dict) -> int:
    default = 10 if (settings or {}).get("options_trading") else 1800
    raw = (settings or {}).get("scan_interval_seconds")
    auto = (settings or {}).get("scan_interval_auto", True)
    if auto:
        return max(10, min(14400, _screener_timeframe_seconds((settings or {}).get("timeframe", "30Min"))))
    try:
        return max(10, min(14400, int(raw if raw not in (None, "") else default)))
    except (TypeError, ValueError):
        return default


def _screener_timeframe_seconds(timeframe: str) -> int:
    value = str(timeframe or "15Min").strip().lower()
    match = re.fullmatch(r"(\d+)\s*(sec|s|min|m|hour|h|day|d|week|w)", value)
    if not match:
        return 15 * 60
    amount = max(1, int(match.group(1)))
    unit = match.group(2)
    if unit in {"sec", "s"}:
        return amount
    if unit in {"hour", "h"}:
        return amount * 3600
    if unit in {"day", "d"}:
        return amount * 86400
    if unit in {"week", "w"}:
        return amount * 7 * 86400
    return amount * 60


def _screener_news_refresh_interval_seconds(settings: dict) -> int:
    """News cadence follows the Scan Interval (auto = the candle cadence), within the news bounds.

    The older per-ticker news_refresh_interval_* fields are no longer read; they stay in stored profiles
    untouched so existing profile revisions do not change.
    """
    return max(60, min(86400, _screener_scan_interval_seconds(settings or {})))


def _screener_candle_key_timestamp(candle_key: str) -> float:
    try:
        value = float(str(candle_key or "").rsplit(":", 1)[-1])
        return value if math.isfinite(value) else 0.0
    except (TypeError, ValueError, OverflowError):
        return 0.0


# --- Completed-candle probe resilience ---------------------------------------------------------------------------
# A probe is one small provider request. Failures are usually transient (a busy provider, a slow response), so:
#   * it runs in its own worker slots (never queued behind full evaluations),
#   * it is retried once with jitter before it counts as a failure,
#   * a failed probe is retried soon (10 s, 20 s, 40 s ... capped at 2 min) instead of after a whole scan interval,
#   * the card is only marked degraded after several consecutive failures. The snapshot independently marks an
#     analysis stale once its candle is older than the freshness limit, so a stale result is never shown as current.
_PROBE_ATTEMPTS = 2
_PROBE_TIMEOUT_SECONDS = 12.0
_PROBE_DEGRADE_AFTER = 3


def _probe_retry_delay(consecutive_failures: int) -> float:
    base = min(120.0, 10.0 * (2 ** max(0, int(consecutive_failures) - 1)))
    return base * (0.85 + 0.3 * random.random())


# A failed full analysis (timeout, provider error, no valid or an out-of-date candle) retries soon with bounded backoff, like
# the probe above, instead of waiting a whole scan interval (up to hours on a 1H profile) with a warning on the card. After
# _EVAL_RETRY_LIMIT quick retries it falls back to the normal cadence so a permanently broken symbol cannot hammer the provider.
_EVAL_RETRY_LIMIT = 5
_EVAL_BUSY_RETRY_SECONDS = 15.0


def _evaluation_retry_delay(failures: int) -> float:
    base = min(300.0, 30.0 * (2 ** max(0, int(failures) - 1)))
    return base * (0.85 + 0.3 * random.random())


def _schedule_evaluation_retry(evaluation_state: dict, scan_interval: float, *, busy: bool = False) -> int:
    """Set the next attempt after a failed full analysis. Returns the consecutive failure count.

    ``busy`` means an earlier attempt for this symbol is still running (not a new failure): look again shortly, without
    counting it, so the retry does not pile onto work that is already in flight.
    """
    now = time.time()
    if busy:
        evaluation_state["next_due_at"] = now + min(float(scan_interval), _EVAL_BUSY_RETRY_SECONDS * (0.85 + 0.3 * random.random()))
        return int(evaluation_state.get("evaluation_failures", 0) or 0)
    failures = int(evaluation_state.get("evaluation_failures", 0) or 0) + 1
    evaluation_state["evaluation_failures"] = failures
    delay = _evaluation_retry_delay(failures) if failures <= _EVAL_RETRY_LIMIT else float(scan_interval)
    evaluation_state["next_due_at"] = now + min(float(scan_interval), delay)
    return failures


async def _run_candle_probe(state, symbol, timeframe, market_session):
    """Return the newest completed-candle metadata, retrying once. Raises the last error if every attempt fails."""
    last_error: Optional[BaseException] = None
    for attempt in range(_PROBE_ATTEMPTS):
        try:
            meta = await screener_manager.work_tracker.run(
                (state.uid, symbol, "probe"),
                screener_manager.probe_semaphore,
                get_latest_completed_candle_metadata,
                symbol, timeframe, market_session,
                timeout=_PROBE_TIMEOUT_SECONDS,
            )
            if isinstance(meta, dict) and meta.get("candle_key"):
                return meta
            last_error = RuntimeError("provider returned no completed candle")
        except Exception as exc:  # TimeoutError, ScreenerWorkBusy, provider errors
            last_error = exc
        if attempt + 1 < _PROBE_ATTEMPTS:
            await asyncio.sleep(0.5 + random.random())
    raise last_error or RuntimeError("candle probe failed")


def _screener_candle_is_usable(candle_meta: dict, timeframe: str, active_window: bool) -> bool:
    """Reject missing, future, and implausibly stale completed candles."""
    if candle_meta.get("candle_status", "closed") != "closed":
        return False
    close_value = candle_meta.get("candle_close_ts")
    try:
        if isinstance(close_value, str):
            close_ts = datetime.fromisoformat(close_value.replace("Z", "+00:00")).timestamp()
        else:
            close_ts = float(close_value)
            if close_ts > 10_000_000_000:
                close_ts /= 1000.0
    except (TypeError, ValueError, OverflowError):
        return False
    if not math.isfinite(close_ts) or close_ts <= 0:
        return False
    now_ts = time.time()
    if close_ts > now_ts:
        return False
    timeframe_seconds = _screener_timeframe_seconds(timeframe)
    # During an active session, a provider should publish the last closed bar
    # promptly. Outside the session, allow weekends/holidays but never accept
    # an old cached candle measured in weeks or years.
    max_age = (
        max(300, timeframe_seconds * 3)
        if active_window
        else max(5 * 86400, timeframe_seconds * 2)
    )
    return now_ts - close_ts <= max_age


_SCREENER_EXCHANGE_CALENDAR = None


def _screener_exchange_session(day) -> Optional[dict]:
    """Return the official XNYS session when exchange-calendars is available."""
    global _SCREENER_EXCHANGE_CALENDAR
    try:
        if _SCREENER_EXCHANGE_CALENDAR is None:
            import exchange_calendars
            _SCREENER_EXCHANGE_CALENDAR = exchange_calendars.get_calendar("XNYS")
        session_label = day.isoformat()
        if not _SCREENER_EXCHANGE_CALENDAR.is_session(session_label):
            return {"is_trading_day": False}
        market_open = _SCREENER_EXCHANGE_CALENDAR.session_open(session_label).tz_convert("US/Eastern")
        market_close = _SCREENER_EXCHANGE_CALENDAR.session_close(session_label).tz_convert("US/Eastern")
        return {
            "is_trading_day": True,
            "open_hour": int(market_open.hour),
            "open_minute": int(market_open.minute),
            "close_hour": int(market_close.hour),
            "close_minute": int(market_close.minute),
        }
    except Exception:
        return None


def _screener_is_us_trading_day(day) -> bool:
    """Use the official XNYS calendar with a deterministic holiday fallback."""
    exchange_session = _screener_exchange_session(day)
    if exchange_session is not None:
        return bool(exchange_session.get("is_trading_day"))
    try:
        from option_selector import is_us_market_trading_day
        return bool(is_us_market_trading_day(day))
    except Exception:
        return day.weekday() < 5


def _screener_next_us_trading_day(day):
    candidate = day + timedelta(days=1)
    while not _screener_is_us_trading_day(candidate):
        candidate += timedelta(days=1)
    return candidate


def _screener_early_close_minutes(day) -> Optional[int]:
    """Return the official close, with standard NYSE-rule fallback dates."""
    exchange_session = _screener_exchange_session(day)
    if exchange_session is not None:
        if not exchange_session.get("is_trading_day"):
            return None
        close_minutes = int(exchange_session["close_hour"]) * 60 + int(exchange_session["close_minute"])
        return close_minutes if close_minutes < 16 * 60 else None
    if not _screener_is_us_trading_day(day):
        return None
    # Thanksgiving Friday.
    first = datetime(day.year, 11, 1).date()
    thanksgiving = first + timedelta(days=(3 - first.weekday()) % 7 + 21)
    if day == thanksgiving + timedelta(days=1):
        return 13 * 60
    # Christmas Eve and the observed Independence Day eve when it is a
    # weekday. These dates cover the regular-session early-close rules used by
    # the stock/options profile windows.
    if day.month == 12 and day.day == 24:
        return 13 * 60
    if day.month == 7 and day.day == 3:
        return 13 * 60
    return None


def _screener_localize_eastern(tz, day, hour: int, minute: int):
    """Build a wall-clock exchange time with the correct offset for its date."""
    return tz.localize(
        datetime(day.year, day.month, day.day, hour, minute, 0),
        is_dst=None,
    )


def _screener_market_status(symbol: str, settings: dict, now=None) -> dict:
    """Mirror exchange session windows, holidays, and common early closes."""
    tz = pytz.timezone("US/Eastern")
    et_now = tz.normalize(now.astimezone(tz)) if now is not None else datetime.now(tz)
    if _is_crypto_symbol(symbol):
        return {
            "is_active_window": True,
            "is_open": True,
            "market_session_label": "24/7",
            "seconds_to_open": 0,
            "seconds_to_pre_open": 0,
            "next_open_str": "24/7",
        }

    is_options = bool((settings or {}).get("options_trading") or (settings or {}).get("is_option"))
    if is_options:
        open_hour, open_minute = 9, 30
        scan_hour, scan_minute = 9, 28
        close_hour, close_minute = 16, 0
        session_label = "Options"
    else:
        session = _normalize_screener_market_session(symbol, (settings or {}).get("market_session"))
        is_extended = session == "Extended"
        open_hour, open_minute = (4, 0) if is_extended else (9, 30)
        scan_hour, scan_minute = (3, 58) if is_extended else (9, 28)
        close_hour, close_minute = (20, 0) if is_extended else (16, 0)
        session_label = "Extended" if is_extended else "Regular"

    today = et_now.date()
    early_close = None if session_label == "Extended" else _screener_early_close_minutes(today)
    if early_close is not None:
        close_hour, close_minute = divmod(early_close, 60)
    scan_start = _screener_localize_eastern(tz, today, scan_hour, scan_minute)
    market_open = _screener_localize_eastern(tz, today, open_hour, open_minute)
    market_close = _screener_localize_eastern(tz, today, close_hour, close_minute)
    trading_day = _screener_is_us_trading_day(today)
    is_active_window = trading_day and scan_start <= et_now <= market_close
    is_open = trading_day and market_open <= et_now <= market_close

    next_open = market_open
    next_scan = scan_start
    if not trading_day or et_now > market_close:
        next_day = _screener_next_us_trading_day(today)
        next_open = _screener_localize_eastern(tz, next_day, open_hour, open_minute)
        next_scan = _screener_localize_eastern(tz, next_day, scan_hour, scan_minute)
    elif et_now < scan_start:
        next_open = market_open
        next_scan = scan_start

    open_day_name = next_open.strftime("%A") if next_open.date() != today else "Today"
    central_hour = (open_hour - 1) % 24     # Central time is one hour behind Eastern
    meridiem = "AM" if central_hour < 12 else "PM"
    hour_label = ((central_hour - 1) % 12) + 1
    time_label = f"{hour_label}:{open_minute:02d} {meridiem}"
    return {
        "is_active_window": is_active_window,
        "is_open": is_open,
        "market_session_label": session_label,
        "seconds_to_open": max(0, int((next_open - et_now).total_seconds())),
        "seconds_to_pre_open": max(0, int((next_scan - et_now).total_seconds())),
        "next_open_str": f"{open_day_name} at {time_label} CT",
        "early_close": early_close is not None,
    }


# Research watches refresh on their own schedules; flipping one must not force a technical rescan on save.
_SCREENER_WATCH_TOGGLE_KEYS = frozenset({"news_watch_enabled", "earnings_watch_enabled", "fundamental_watch_enabled"})


def _screener_profile_fingerprint(settings: dict, extra_ignored=frozenset()) -> str:
    """Hash persisted profile inputs while ignoring generated metadata."""
    ignored = {"revision", "profile_revision", "started_at"} | set(extra_ignored)
    clean = {
        key: value for key, value in (settings or {}).items()
        if key not in ignored
    }
    return hashlib.sha256(json.dumps(clean, sort_keys=True, default=str).encode()).hexdigest()


def _forecast_settings_hash(settings: dict) -> str:
    payload = {key: settings.get(key) for key in _FORECAST_HASH_KEYS if key in settings}
    return hashlib.sha256(json.dumps(payload, sort_keys=True, default=str).encode()).hexdigest()[:16]


def _signal_reason_text(data: dict) -> str:
    if not isinstance(data, dict):
        return ""
    reason = str(data.get("reason") or data.get("description") or "").strip()
    if reason:
        return reason
    value = data.get("value")
    if value is None or value == "":
        return ""
    if isinstance(value, float):
        return f"{value:.2f}"
    return str(value)


def _format_duration_compact(seconds: float) -> str:
    remaining = max(0, int(seconds))
    hours, rem = divmod(remaining, 3600)
    minutes, secs = divmod(rem, 60)
    if hours:
        return f"{hours}h {minutes}m"
    if minutes:
        return f"{minutes}m {secs}s"
    return f"{secs}s"


def _timeframe_to_seconds(timeframe: str) -> int:
    tf = str(timeframe or "").upper().strip()
    if tf in {"1D", "1DAY", "DAY"}:
        return 86400
    match = re.search(r"\d+", tf)
    value = int(match.group()) if match else 5
    if "HOUR" in tf or tf.endswith("H"):
        return value * 3600
    if "MIN" in tf or tf.endswith("M"):
        return value * 60
    if "SEC" in tf or tf.endswith("S"):
        return value
    return 300


def _option_direction_label(settings: dict) -> str:
    option_type = normalize_options_type(settings.get("options_type", "calls_only"), default="calls_only")
    if option_type == "calls_only":
        return "CALLS_ONLY"
    if option_type == "puts_only":
        return "PUTS_ONLY"
    return "LEGACY_BOTH_DISABLED"


def _indicator_pool_text(settings: dict) -> str:
    indicators = [str(name).strip() for name in (settings.get("indicators") or []) if str(name).strip()]
    return ", ".join(indicators) if indicators else "System Defaults"


def _selected_indicator_names(settings: dict) -> list[str]:
    indicators = settings.get("indicators")
    if not isinstance(indicators, list):
        return []
    return [str(name).strip() for name in indicators if str(name).strip()]


def _screener_direction_watch_config(
    settings: dict | None = None,
    evaluation_state: dict | None = None,
    notification_settings: dict | None = None,
) -> dict:
    """Resolve the independent bullish/bearish alert watch for one ticker.

    A ticker-level preference, when present, wins over the user's global
    delivery preference.  The resulting mode is descriptive only: it does
    not create a brokerage position and it never changes the selected
    indicator/event qualification policy.
    """
    settings = settings if isinstance(settings, dict) else {}
    evaluation_state = evaluation_state if isinstance(evaluation_state, dict) else {}
    notification_settings = notification_settings if isinstance(notification_settings, dict) else {}

    def enabled(direction: str) -> bool:
        key = f"alert_{direction.lower()}_enabled"
        global_key = f"{direction.lower()}_enabled"
        if key in settings:
            return bool(settings.get(key))
        state_key = f"direction_watch_{global_key}"
        if state_key in evaluation_state:
            return bool(evaluation_state.get(state_key))
        return bool(notification_settings.get(global_key, True))

    bullish_enabled = enabled("BULLISH")
    bearish_enabled = enabled("BEARISH")
    mode = direction_watch_mode(bullish_enabled, bearish_enabled)
    return {
        "mode": mode,
        "label": direction_watch_label(mode),
        "directions": direction_watch_directions(mode),
        "bullish_enabled": bullish_enabled,
        "bearish_enabled": bearish_enabled,
    }


def _scan_log_strategy_context(settings: dict, position: dict) -> dict:
    phase = "EXIT" if position else "ENTRY"

    # AI Autopilot owns the entry/exit decision. It must not inherit the
    # regular indicator-pool defaults or be blocked for having no indicators.
    if settings.get("ai_autopilot"):
        return {
            "phase": phase,
            "mode": "ai_autopilot",
            "label": "AI Autopilot",
            "uses_indicators": False,
            "uses_structural": False,
            "is_hybrid": False,
        }

    # A strategy preset owns entries and exits with its own indicator values; it
    # must not be blocked for having no indicator pool or labelled as a structure mode.
    if strategy_presets.is_strategy_preset_active(settings) and not settings.get("options_trading"):
        return {
            "phase": phase,
            "mode": strategy_presets.PRESET_MODE,
            "label": f"Strategy: {strategy_presets.strategy_preset_label(settings)}",
            "uses_indicators": False,
            "uses_structural": False,
            "is_hybrid": False,
        }

    mode = (
        settings.get("sell_mode") or settings.get("options_exit_strategy") or "indicator"
        if phase == "EXIT"
        else settings.get("buy_mode", "indicator")
    )
    mode = str(mode or "indicator")

    if phase == "EXIT":
        uses_indicators = mode == "indicator" or mode in _HYBRID_EXIT_MODES
        uses_structural = mode != "indicator"
        label = _exit_strategy_label(mode)
    else:
        uses_indicators = mode == "indicator" or mode in _HYBRID_ENTRY_MODES
        uses_structural = mode != "indicator"
        label = _entry_strategy_label(mode)

    return {
        "phase": phase,
        "mode": mode,
        "label": label,
        "uses_indicators": uses_indicators,
        "uses_structural": uses_structural,
        "is_hybrid": uses_indicators and uses_structural,
    }


def _scan_log_condition_logic(strategy_context: dict) -> str:
    """Return the explicit conjunction used by a hybrid scan setup."""
    if not strategy_context.get("is_hybrid"):
        return ""
    mode = str(strategy_context.get("mode") or "").lower()
    component = {
        "hybrid": "ATR",
        "hybrid_atr": "ATR",
        "hybrid_pct": "percentage",
        "hybrid_pivot": "pivot",
        "hybrid_high_low": "prior-day high/low",
        "hybrid_swing": "swing structure",
    }.get(mode, "structural")
    if strategy_context.get("phase") == "ENTRY":
        return f"Hybrid entry rule: BOTH indicator and {component} conditions must be true."
    return f"Hybrid exit rule: EITHER indicator or {component} condition can trigger the exit."


def _signal_weight_for_direction(signals: dict, selected_indicators: list[str], direction: str) -> int:
    selected = set(selected_indicators or [])
    total = 0
    for name, data in (signals or {}).items():
        if selected and name not in selected:
            continue
        if str((data or {}).get("signal") or "NEUTRAL").upper() == direction:
            total += int((data or {}).get("weight", 1) or 1)
    return total


def _screener_exit_level_status(
    settings: dict,
    scan: dict,
    state: dict,
    mode: str,
    price_override: Optional[float] = None,
) -> dict:
    """Evaluate virtual long exit levels without reading a bot or placing an order.

    ``price_override`` is used by the independent quote watcher. The level
    inputs remain anchored to the saved virtual cycle and completed history;
    only the observed price may update between candles.
    """
    frozen_settings = state.get("virtual_exit_settings")
    if not isinstance(frozen_settings, dict) or not frozen_settings:
        frozen_settings = settings or {}
    frozen_mode = str(state.get("virtual_exit_mode") or mode or "indicator").strip().lower()
    mode = frozen_mode
    price = _entry_forecast_float(
        price_override if price_override is not None else scan.get("price_raw"),
        0.0,
    )
    entry = _entry_forecast_float(state.get("virtual_entry_price"), 0.0)
    atr = _entry_forecast_float(
        state.get("virtual_entry_atr") or scan.get("atr"),
        0.0,
    )
    precision = 6 if 0 < price < 1 else 2

    def pending(message: str) -> dict:
        return {"available": False, "triggered": False, "text": message}

    def levels(stop: float, target: float, label: str) -> dict:
        if price <= 0 or stop <= 0 or target <= 0:
            return pending(f"{label} levels are unavailable until valid price data arrives.")
        stop_distance_pct = abs(price - stop) / price * 100.0
        target_distance_pct = abs(target - price) / price * 100.0
        if price <= stop:
            outcome = (
                f"{label}: SL reached (${price:.{precision}f} <= ${stop:.{precision}f}); "
                f"TP ${target:.{precision}f} ({target_distance_pct:.2f}% away)."
            )
        elif price >= target:
            outcome = (
                f"{label}: TP reached (${price:.{precision}f} >= ${target:.{precision}f}); "
                f"SL ${stop:.{precision}f} ({stop_distance_pct:.2f}% away)."
            )
        else:
            nearest = "SL" if stop_distance_pct <= target_distance_pct else "TP"
            outcome = (
                f"{label}: price ${price:.{precision}f}; "
                f"SL ${stop:.{precision}f} ({stop_distance_pct:.2f}% away); "
                f"TP ${target:.{precision}f} ({target_distance_pct:.2f}% away); "
                f"nearest exit {nearest}."
            )
        return {
            "available": True,
            "triggered": price <= stop or price >= target,
            "text": outcome,
            "price": price,
            "stop": stop,
            "target": target,
            "stop_distance_pct": stop_distance_pct,
            "target_distance_pct": target_distance_pct,
            "nearest_level": "SL" if stop_distance_pct <= target_distance_pct else "TP",
        }

    frozen_levels = state.get("virtual_exit_levels")
    frozen_stop = _entry_forecast_float(
        frozen_levels.get("stop") if isinstance(frozen_levels, dict) else None,
        0.0,
    )
    frozen_target = _entry_forecast_float(
        frozen_levels.get("target") if isinstance(frozen_levels, dict) else None,
        0.0,
    )

    if mode in {"sltp", "hybrid", "atr", "hybrid_atr"}:
        if entry <= 0 or atr <= 0:
            return pending("Waiting for the virtual entry price and completed-candle ATR to evaluate the ATR exit.")
        stop = frozen_stop if frozen_stop > 0 else entry - atr * _entry_forecast_float(frozen_settings.get("atr_stop_multiplier"), 2.0)
        # Missing settings use the same safe default shown by the Screener
        # card: ATR trailing is enabled unless explicitly disabled.
        if frozen_settings.get("enable_atr_trail", True) is not False:
            peak = max(entry, _entry_forecast_float(state.get("virtual_peak_price"), entry), price)
            candidate_trailing_stop = peak - atr * _entry_forecast_float(frozen_settings.get("atr_trail_multiplier"), 3.0)
            prior_trailing_stop = _entry_forecast_float(state.get("virtual_trailing_stop"), 0.0)
            # A trailing stop may tighten as the virtual peak rises, but it
            # must never loosen because a later ATR value expands.
            stop = max(stop, candidate_trailing_stop, prior_trailing_stop)
            state["virtual_trailing_stop"] = stop
        else:
            state["virtual_trailing_stop"] = None
        target = frozen_target if frozen_target > 0 else entry + atr * _entry_forecast_float(frozen_settings.get("take_profit_multiplier"), 4.0)
        return levels(stop, target, "ATR exit")

    if mode in {"percentage", "hybrid_pct"}:
        if entry <= 0:
            return pending("Waiting for the virtual entry price to evaluate the percentage exit.")
        stop = frozen_stop if frozen_stop > 0 else entry * (1 - _entry_forecast_float(frozen_settings.get("regular_stop_loss_pct"), 0.05))
        if frozen_settings.get("enable_pct_trail"):
            peak = max(entry, _entry_forecast_float(state.get("virtual_peak_price"), entry), price)
            stop = max(stop, peak * (1 - _entry_forecast_float(frozen_settings.get("regular_trailing_pct"), 0.03)))
        target = frozen_target if frozen_target > 0 else entry * (1 + _entry_forecast_float(frozen_settings.get("regular_take_profit_pct"), 0.10))
        return levels(stop, target, "Percentage exit")

    if mode in {"custom_sltp", "custom_underlying_sltp"}:
        if frozen_stop > 0 and frozen_target > 0:
            return levels(frozen_stop, frozen_target, "Custom SL/TP")
        stop_key = "custom_underlying_stop_loss_price" if mode == "custom_underlying_sltp" else "custom_stop_loss_price"
        target_key = "custom_underlying_take_profit_price" if mode == "custom_underlying_sltp" else "custom_take_profit_price"
        return levels(_entry_forecast_float(frozen_settings.get(stop_key), 0.0), _entry_forecast_float(frozen_settings.get(target_key), 0.0), "Custom SL/TP")

    if frozen_stop > 0 and frozen_target > 0 and mode in {
        "pivot_points", "hybrid_pivot", "high_low", "hybrid_high_low",
        "swing_points", "hybrid_swing",
    }:
        level_label = (
            "Pivot exit" if "pivot" in mode
            else "High/low exit" if "high_low" in mode
            else "Swing exit"
        )
        return levels(frozen_stop, frozen_target, level_label)

    prior = _get_prior_day_ohlc(scan.get("price_history") or [])
    if mode in {"pivot_points", "hybrid_pivot"}:
        if not prior or price <= 0:
            return pending("Waiting for prior-day OHLC data to evaluate the pivot exit.")
        level_set = _dynamic_pivot_exit_levels(prior["high"], prior["low"], prior["close"], price)
        return levels(level_set["sl"], level_set["tp"], "Pivot exit") if level_set else pending("Pivot exit levels are unavailable.")
    if mode in {"high_low", "hybrid_high_low"}:
        if not prior or price <= 0:
            return pending("Waiting for prior-day OHLC data to evaluate the high/low exit.")
        level_set = _dynamic_high_low_exit_levels(prior["high"], prior["low"], price, resolve_high_low_buffer_pct(frozen_settings))
        return levels(level_set["sl"], level_set["tp"], "High/low exit") if level_set else pending("High/low exit levels are unavailable.")
    if mode in {"swing_points", "hybrid_swing"}:
        exit_levels = build_swing_exit(scan.get("price_history") or [], current_price=price, is_long=True,
            lookback_days=int(settings.get("swing_exit_lookback_days", settings.get("swing_lookback_days", 20)) or 20),
            market_session=frozen_settings.get("market_session", "Regular"), is_crypto=False)
        return levels(exit_levels.get("stop_loss") or 0, exit_levels.get("take_profit") or 0, "Swing exit")
    if mode == "trend_rider":
        return pending("Trend Rider has no standalone Screener exit evaluator; no exit condition is being inferred.")
    if mode == "ai_autopilot":
        return pending("AI Autopilot exit is evaluated by its live position workflow; the Screener remains analysis-only.")
    if mode == "strategy_preset":
        return pending("The strategy owns its exit; a SELL (exit) alert is sent when its exit condition is met.")
    return {"available": True, "triggered": False, "text": "Indicator exit is waiting for bearish confirmation."}


_STRUCTURAL_EXIT_MODES = frozenset({
    "sltp", "atr", "hybrid", "hybrid_atr", "percentage", "hybrid_pct", "custom_sltp", "custom_underlying_sltp",
    "pivot_points", "hybrid_pivot", "high_low", "hybrid_high_low", "swing_points", "hybrid_swing",
})


def _screener_exit_evidence(settings: dict, formatted_scan: dict, exit_levels: Optional[dict], position_state: Optional[dict] = None) -> dict:
    """Exit-side evidence for a structural exit mode, shown in every watching state (not only after an entry alert).

    With a live virtual position the position's own levels are reported (``reference`` False). Otherwise the same
    level calculator is applied with the current completed-candle price as a hypothetical entry, so the card can show
    where the exit would sit (``reference`` True). Indicator, strategy and event exits return ``{}`` (their evidence
    is the bearish indicator count, the strategy state and the news respectively). Analysis only: no orders.
    """
    mode = str((settings or {}).get("sell_mode") or "indicator").strip().lower()
    if mode not in _STRUCTURAL_EXIT_MODES:
        return {}
    if isinstance(exit_levels, dict) and exit_levels.get("text"):
        return {**exit_levels, "mode": mode, "reference": False}
    # A virtual position is open (a Buy alert started it): show ITS frozen stop and target against the current price, not a
    # hypothetical entry at the current price. Evaluated on a copy, so the trailing-stop bookkeeping never leaks out.
    if isinstance(position_state, dict) and position_state.get("virtual_position"):
        entry = _entry_forecast_float(position_state.get("virtual_entry_price"), 0.0)
        if entry > 0:
            frozen = position_state.get("virtual_exit_settings") if isinstance(position_state.get("virtual_exit_settings"), dict) else None
            live = dict(_screener_exit_level_status(settings, formatted_scan, copy.deepcopy(position_state), mode))
            live.update({"mode": mode, "reference": False, "entry_price": entry})
            entry_atr = _entry_forecast_float(position_state.get("virtual_entry_atr"), 0.0)
            cfg = frozen or settings
            if entry_atr > 0 and mode in {"sltp", "atr", "hybrid", "hybrid_atr"}:
                live.update({
                    "atr": entry_atr,
                    "atr_stop_multiplier": _entry_forecast_float(cfg.get("atr_stop_multiplier"), 2.0),
                    "atr_trail_multiplier": _entry_forecast_float(cfg.get("atr_trail_multiplier"), 3.0),
                    "atr_trail_enabled": cfg.get("enable_atr_trail", True) is not False,
                    "take_profit_multiplier": _entry_forecast_float(cfg.get("take_profit_multiplier"), 4.0),
                })
            return live
    price = _entry_forecast_float((formatted_scan or {}).get("price_raw"), 0.0)
    atr = _entry_forecast_float((formatted_scan or {}).get("atr"), 0.0)
    if price <= 0:
        return {"mode": mode, "reference": True, "available": False, "triggered": False,
                "text": "Waiting for a valid completed-candle price to show the exit levels."}
    # A throwaway state: the calculator may write trailing-stop bookkeeping into it, which must never reach the profile.
    scratch = {"virtual_entry_price": price, "virtual_entry_atr": atr}
    result = dict(_screener_exit_level_status(settings, formatted_scan, scratch, mode))
    result.update({"mode": mode, "reference": True})
    if atr > 0 and mode in {"sltp", "atr", "hybrid", "hybrid_atr"}:
        result.update({
            "atr": atr,
            "atr_stop_multiplier": _entry_forecast_float(settings.get("atr_stop_multiplier"), 2.0),
            "atr_trail_multiplier": _entry_forecast_float(settings.get("atr_trail_multiplier"), 3.0),
            "atr_trail_enabled": settings.get("enable_atr_trail", True) is not False,
            "take_profit_multiplier": _entry_forecast_float(settings.get("take_profit_multiplier"), 4.0),
        })
    return result


def _scan_distance_text(symbol: str, current_price: float, target_price: float, label: str) -> str:
    current = _entry_forecast_float(current_price, 0.0)
    target = _entry_forecast_float(target_price, 0.0)
    if current <= 0 or target <= 0:
        return ""
    diff = target - current
    pct = abs(diff) / current * 100.0
    direction = "above" if diff > 0 else "below"
    return f"{label} {_format_quote_price(symbol, target)} ({pct:.2f}% {direction})"


def _nearest_scan_level(symbol: str, current_price: float, levels: list[dict]) -> str:
    current = _entry_forecast_float(current_price, 0.0)
    if current <= 0:
        return ""
    candidates = []
    for level in levels or []:
        target = _entry_forecast_float(level.get("price"), 0.0)
        if target <= 0:
            continue
        candidates.append((abs(target - current) / current, level))
    if not candidates:
        return ""
    _, level = min(candidates, key=lambda item: item[0])
    return _scan_distance_text(symbol, current, level.get("price"), f"Nearest {level.get('label', 'level')}")


def _scan_reference_price(formatted_scan: dict, current: float) -> float:
    history = (formatted_scan or {}).get("price_history") or []
    if len(history) >= 2:
        prev_close = _entry_forecast_float(history[-2].get("close"), 0.0)
        if prev_close > 0:
            return prev_close
    return current


def _continuous_grid_side_forecast_text(ticker: str, side: str, state: dict, current_price: float) -> str:
    if not state or not state.get("enabled"):
        return ""
    is_entry = side == "entry"
    side_label = "Adaptive Buy Grid" if is_entry else "Adaptive Sell Grid"
    anchor_label = "buy anchor" if is_entry else "average-cost anchor"
    anchor = _entry_forecast_float(state.get("anchor_price"), 0.0)
    progress = f"{int(state.get('completed_count') or 0)}/{int(state.get('enabled_count') or 0)} enabled rungs completed"
    status = str(state.get("status") or "monitoring")

    if status == "waiting_position":
        return f"{side_label} | Waiting for an open position to establish the sell anchor | Progress {progress}"
    if status == "waiting_initial_entry":
        level = int(state.get("next_level") or state.get("display_level") or 0)
        allocation_pct = _entry_forecast_float(state.get("allocation_pct"), 0.0)
        allocation_amount = _entry_forecast_float(state.get("allocation_amount"), 0.0)
        allocation = f"Buy {allocation_pct:.1f}%"
        if allocation_amount > 0:
            allocation += f" ({_format_quote_price(ticker, allocation_amount)})"
        return (
            f"{side_label} | Waiting for the initial {str(state.get('grid_kind') or 'PCT').upper()} signal; first fill is "
            f"L{level} {allocation}, not a full allocation | Progress {progress}"
        )
    if status == "runner_monitoring":
        trigger = _entry_forecast_float(state.get("trigger_price"), 0.0)
        peak = _entry_forecast_float(state.get("trailing_peak_price"), 0.0)
        runner_pct = _entry_forecast_float(state.get("runner_pct"), 0.0)
        trail_pct = _entry_forecast_float(state.get("runner_trail_pct"), 0.0)
        distance = _scan_distance_text(ticker, current_price, trigger, "Distance") if current_price > 0 else ""
        return (
            f"{side_label} | Runner monitoring: retain {runner_pct:.1f}% of ladder-start position; "
            f"trail {trail_pct:.2f}% below peak {_format_quote_price(ticker, peak)}; "
            f"close runner at/below {_format_quote_price(ticker, trigger)} | Progress {progress}"
            + (f" | {distance}" if distance else "")
        )
    if status == "no_levels":
        return f"{side_label} | No enabled levels"
    if status == "all_levels_complete":
        return f"{side_label} | All enabled levels completed | {anchor_label.title()} {_format_quote_price(ticker, anchor)} | Progress {progress}"

    level = state.get("pending_level") if status == "order_pending" else state.get("display_level")
    level = int(level or state.get("next_level") or 0)
    from_level = int(state.get("from_level") or level)
    rung_label = f"L{from_level}–L{level}" if from_level < level else f"L{level}"
    trigger = _entry_forecast_float(state.get("trigger_price"), 0.0)
    level_pct = _entry_forecast_float(state.get("level_pct"), 0.0)
    base_level_pct = _entry_forecast_float(state.get("base_level_pct"), level_pct)
    allocation_pct = _entry_forecast_float(state.get("allocation_pct"), 0.0)
    trailing_sell = bool((not is_entry) and state.get("trailing_enabled") and state.get("armed_level"))
    action = "Buy at/below" if is_entry else ("Sell at/below" if trailing_sell else "Sell at/above")
    allocation = f"Buy {allocation_pct:.1f}%"
    if is_entry:
        allocation_amount = _entry_forecast_float(state.get("allocation_amount"), 0.0)
        if allocation_amount > 0:
            allocation += f" ({_format_quote_price(ticker, allocation_amount)})"
    else:
        allocation = f"Sell {allocation_pct:.1f}% of ladder-start position"
    pending = " | Order pending" if status == "order_pending" else ""
    distance = _scan_distance_text(ticker, current_price, trigger, "Distance") if current_price > 0 else ""
    unit = "x ATR" if state.get("grid_kind") == "atr" else "%"
    move = f"{'-' if is_entry else '+'}{level_pct:.2f}{unit}"
    smart = ""
    if state.get("smart_dynamic"):
        factors = state.get("adaptive_factors") or {}
        trend = _entry_forecast_float(factors.get("trend_score"), 0.0)
        volatility = _entry_forecast_float(factors.get("volatility_ratio"), 1.0)
        regime = "uptrend" if trend > 0.15 else ("downtrend" if trend < -0.15 else "balanced")
        base = f"{base_level_pct:.2f}{unit} base → " if abs(base_level_pct - level_pct) > 0.0001 else ""
        smart = f" | Smart {base}{level_pct:.2f}{unit}; {regime} {trend:+.2f}, vol {volatility:.2f}x"
        if state.get("grid_kind") == "pct" and state.get("pct_regime_profile_active"):
            if is_entry:
                smart += " | Bull profile buy weights 40/25/15/10/10"
            else:
                smart += f" | Bull profile sell weights 10/10/15/20/20; {_entry_forecast_float(state.get('pct_runner_pct_configured'), 25.0):.0f}% runner"
    if state.get("trailing_enabled"):
        peak = _entry_forecast_float(state.get("trailing_peak_price"), 0.0)
        activation = _entry_forecast_float(state.get("activation_price"), 0.0)
        if not is_entry and state.get("armed_level") and peak > 0:
            smart += f" | Trailing sell armed at L{int(state.get('armed_level'))}; peak {_format_quote_price(ticker, peak)}"
        elif not is_entry and activation > 0:
            smart += f" | Activates {_format_quote_price(ticker, activation)}, then trails peak"
        elif is_entry and peak > 0:
            smart += f" | Buy levels ratchet from high-water {_format_quote_price(ticker, peak)}"
    return (
        f"{side_label} | {'Pending' if status == 'order_pending' else 'Next'} {rung_label}: {action} {_format_quote_price(ticker, trigger)} ({move})"
        f" | {allocation} | {anchor_label.title()} {_format_quote_price(ticker, anchor)}"
        f" | Progress {progress}{smart}{pending}{f' | {distance}' if distance else ''}"
    )


def _build_continuous_grid_forecast(settings: dict, formatted_scan: dict, position: dict, ticker: str) -> dict:
    current = _entry_forecast_float((formatted_scan or {}).get("price_raw"), 0.0)
    if current <= 0 and position:
        current = _entry_forecast_float(position.get("current_price"), 0.0)
    average = _entry_forecast_float(
        (position or {}).get("avg_price") or (position or {}).get("avg_entry_price"),
        0.0,
    )
    qty = _entry_forecast_float((position or {}).get("qty"), 0.0)
    forecast = _continuous_grid_forecast_state(
        settings,
        current_price=current,
        has_position=bool(position),
        average_entry_price=average,
        position_qty=qty,
        current_atr=_entry_forecast_float((formatted_scan or {}).get("atr"), 0.0),
        price_history=(formatted_scan or {}).get("price_history") or [],
    )
    if not forecast.get("enabled"):
        return forecast
    forecast["entry"]["text"] = _continuous_grid_side_forecast_text(
        ticker, "entry", forecast.get("entry"), current
    )
    forecast["exit"]["text"] = _continuous_grid_side_forecast_text(
        ticker, "exit", forecast.get("exit"), current
    )
    return forecast


def _continuous_grid_scan_levels(ticker: str, forecast: dict) -> list[str]:
    if not forecast or not forecast.get("enabled"):
        return []
    current = _entry_forecast_float(forecast.get("current_price"), 0.0)
    lines = []
    for side, label in (("entry", "Next buy"), ("exit", "Next sell")):
        state = forecast.get(side) or {}
        if not state.get("enabled"):
            continue
        status = str(state.get("status") or "")
        if status == "waiting_position":
            lines.append("Sell ladder waiting for a position")
            continue
        if status == "waiting_initial_entry":
            level = int(state.get("next_level") or state.get("display_level") or 0)
            allocation_pct = _entry_forecast_float(state.get("allocation_pct"), 0.0)
            amount = _entry_forecast_float(state.get("allocation_amount"), 0.0)
            amount_text = f"/{_format_quote_price(ticker, amount)}" if amount > 0 else ""
            lines.append(
                f"Initial buy L{level}: waiting for percentage signal, then buy "
                f"{allocation_pct:.1f}%{amount_text}"
            )
            continue
        if status == "all_levels_complete":
            lines.append(f"{label.title()} ladder: all enabled rungs completed")
            continue
        level = int((state.get("pending_level") if status == "order_pending" else state.get("display_level")) or state.get("next_level") or 0)
        from_level = int(state.get("from_level") or level)
        rung_label = f"L{from_level}–L{level}" if from_level < level else f"L{level}"
        trigger = _entry_forecast_float(state.get("trigger_price"), 0.0)
        allocation_pct = _entry_forecast_float(state.get("allocation_pct"), 0.0)
        if level <= 0 or trigger <= 0:
            continue
        allocation = f"buy {allocation_pct:.1f}%"
        if side == "entry":
            amount = _entry_forecast_float(state.get("allocation_amount"), 0.0)
            if amount > 0:
                allocation += f"/{_format_quote_price(ticker, amount)}"
        else:
            allocation = f"sell {allocation_pct:.1f}%"
        pending = ", order pending" if status == "order_pending" else ""
        distance_label = f"{label} {rung_label}"
        if side == "exit" and state.get("trailing_enabled") and state.get("armed_level"):
            distance_label = f"Trailing sell {rung_label}"
        distance = _scan_distance_text(ticker, current, trigger, distance_label)
        trailing_note = " after profit activation" if side == "exit" and state.get("trailing_enabled") and state.get("armed_level") else ""
        lines.append(f"{distance} ({allocation}{trailing_note}{pending})" if distance else f"{distance_label} ({allocation}{trailing_note}{pending})")
    return lines


def _active_trailing_stop(eng: UserEngine, symbol: str):
    trailing = getattr(eng, "last_trailing_stops", {}).get(symbol)
    if isinstance(trailing, dict):
        trailing = trailing.get("trailing_stop_price")
    trailing = _entry_forecast_float(trailing, 0.0)
    return trailing if trailing > 0 else None


def _scan_log_entry_levels(ticker: str, formatted_scan: dict, settings: dict, target_direction: str) -> list[str]:
    mode = str(settings.get("buy_mode", "indicator") or "indicator")
    current = _entry_forecast_float(formatted_scan.get("price_raw"), 0.0)
    if current <= 0:
        return []

    lines = []
    option_side = normalize_options_type(settings.get("options_type", "calls_only"), default="calls_only")
    is_option = bool(settings.get("options_trading"))
    if is_option and option_side not in VALID_OPTION_DIRECTIONS:
        return ["Legacy Both direction disabled. Choose CALL or PUT."]

    def include_bullish():
        return not is_option or option_side == "calls_only" or target_direction == "BULLISH"

    def include_bearish():
        return is_option and (option_side == "puts_only" or target_direction == "BEARISH")

    if mode == "custom_price":
        custom_price = _entry_forecast_float(settings.get("custom_buy_price"), 0.0)
        text = _scan_distance_text(ticker, current, custom_price, "Custom entry")
        return [text] if text else []

    if mode in {"atr", "hybrid_atr"}:
        if settings.get("atr_grid_continuous_mode") and settings.get("exit_atr_grid_continuous_mode"):
            forecast = _build_continuous_grid_forecast(settings, formatted_scan, None, ticker)
            return _continuous_grid_scan_levels(ticker, forecast)
        atr = _entry_forecast_float(formatted_scan.get("atr"), 0.0)
        mult = _entry_forecast_float(settings.get("entry_atr_multiplier", settings.get("atr_stop_multiplier", 2.0)), 2.0)
        style = settings.get("entry_atr_style", "pullback_reclaim")
        if atr <= 0:
            return []
        ref_price = _scan_reference_price(formatted_scan, current)
        offset = max(atr * max(mult, 0.1), ref_price * 0.001)
        bull_level = ref_price + offset if style == "breakout_momentum" else ref_price - offset
        bear_level = ref_price - offset if style == "breakout_momentum" else ref_price + offset
        if include_bullish():
            text = _scan_distance_text(ticker, current, bull_level, "CALL/long trigger" if is_option else "Entry trigger")
            if text:
                lines.append(text)
        if include_bearish():
            text = _scan_distance_text(ticker, current, bear_level, "PUT trigger")
            if text:
                lines.append(text)
        return lines

    if mode in {"percentage", "hybrid_pct"} and settings.get("grid_continuous_mode"):
        forecast = _build_continuous_grid_forecast(settings, formatted_scan, None, ticker)
        return _continuous_grid_scan_levels(ticker, forecast)

    if mode in {"percentage", "hybrid_pct"}:
        pct = _entry_forecast_float(settings.get("entry_pct_move", 1.0), 1.0) / 100.0
        style = settings.get("entry_pct_style", "dip_reclaim")
        ref_price = _scan_reference_price(formatted_scan, current)
        bull_level = ref_price * (1.0 + pct) if style == "breakout_momentum" else ref_price * (1.0 - pct)
        bear_level = ref_price * (1.0 - pct) if style == "breakout_momentum" else ref_price * (1.0 + pct)
        if include_bullish():
            text = _scan_distance_text(ticker, current, bull_level, "CALL/long trigger" if is_option else "Entry trigger")
            if text:
                lines.append(text)
        if include_bearish():
            text = _scan_distance_text(ticker, current, bear_level, "PUT trigger")
            if text:
                lines.append(text)
        return lines

    prior = (formatted_scan or {}).get("prior_day_ohlc") or {}
    if mode in {"pivot_points", "hybrid_pivot"} and prior:
        high = _entry_forecast_float(prior.get("high"), 0.0)
        low = _entry_forecast_float(prior.get("low"), 0.0)
        close = _entry_forecast_float(prior.get("close"), 0.0)
        if high > 0 and low > 0 and close > 0:
            pivot = (high + low + close) / 3.0
            r1 = (2 * pivot) - low
            s1 = (2 * pivot) - high
            nearest = _nearest_scan_level(ticker, current, [
                {"label": "pivot support", "price": s1},
                {"label": "pivot breakout", "price": r1},
            ])
            return [nearest] if nearest else []

    if mode in {"high_low", "hybrid_high_low"} and prior:
        levels = buffered_prior_day_levels(prior, resolve_high_low_buffer_pct(settings))
        if not levels:
            return []
        nearest = _nearest_scan_level(ticker, current, [
            {"label": "prior high trigger", "price": levels.get("high_trigger")},
            {"label": "prior low trigger", "price": levels.get("low_trigger")},
        ])
        return [nearest] if nearest else []

    return []


def _scan_log_exit_levels(ticker: str, formatted_scan: dict, settings: dict, position: dict, exit_protection: dict = None, eng: UserEngine = None) -> list[str]:
    if not position:
        return []

    symbol = position.get("symbol") or ticker
    mode = str(settings.get("sell_mode") or settings.get("options_exit_strategy") or "indicator")
    is_option = bool(settings.get("options_trading")) or bool(position.get("is_option")) or len(str(symbol)) >= 15
    direction = _position_direction_label(position)
    avg_price = _entry_forecast_float(position.get("avg_price") or position.get("avg_entry_price"), 0.0)
    premium_now = _entry_forecast_float(position.get("current_price"), 0.0)
    underlying_now = _entry_forecast_float(formatted_scan.get("price_raw"), 0.0)
    lines = []

    if (
        not is_option
        and (
            (mode in {"percentage", "hybrid_pct"} and (settings.get("grid_continuous_mode") or settings.get("exit_grid_continuous_mode")))
            or (mode in {"sltp", "hybrid", "atr", "hybrid_atr"} and (settings.get("atr_grid_continuous_mode") or settings.get("exit_atr_grid_continuous_mode")))
        )
    ):
        forecast = _build_continuous_grid_forecast(settings, formatted_scan, position, ticker)
        return _continuous_grid_scan_levels(ticker, forecast)

    if exit_protection:
        tp = _entry_forecast_float(exit_protection.get("take_profit"), 0.0)
        sl = _entry_forecast_float(exit_protection.get("stop_loss"), 0.0)
        if tp > 0 and premium_now > 0:
            text = _scan_distance_text(symbol, premium_now, tp, "TP")
            if text:
                lines.append(text)
        if sl > 0 and premium_now > 0:
            text = _scan_distance_text(symbol, premium_now, sl, "SL")
            if text:
                lines.append(text)
        if lines:
            return lines

    if is_option:
        if mode in {"premium_pct", "percentage", "hybrid_pct"} and avg_price > 0 and premium_now > 0:
            sl_pct, tp_pct, _trail_pct = _option_pct_exit_settings(settings)
            sl_price = avg_price * (1.0 - sl_pct)
            trailing = _active_trailing_stop(eng, symbol) if settings.get("enable_pct_trail", True) else None
            if trailing:
                sl_price = trailing
            lines.append(_scan_distance_text(symbol, premium_now, avg_price * (1.0 + tp_pct), "Premium TP"))
            lines.append(_scan_distance_text(symbol, premium_now, sl_price, "Premium SL"))
        if mode in {"sltp", "hybrid"} and underlying_now > 0:
            atr = _entry_forecast_float(formatted_scan.get("atr"), 0.0)
            entry_underlying = _entry_forecast_float(settings.get("options_underlying_entry_price"), underlying_now)
            sl_mult = _entry_forecast_float(settings.get("atr_stop_multiplier", 2.0), 2.0)
            tp_mult = _entry_forecast_float(settings.get("take_profit_multiplier", 4.0), 4.0)
            if atr > 0 and entry_underlying > 0:
                if direction == "PUT":
                    tp = entry_underlying - (atr * tp_mult)
                    sl = entry_underlying + (atr * sl_mult)
                else:
                    tp = entry_underlying + (atr * tp_mult)
                    sl = entry_underlying - (atr * sl_mult)
                lines.append(_scan_distance_text(ticker, underlying_now, tp, "Underlying TP"))
                lines.append(_scan_distance_text(ticker, underlying_now, sl, "Underlying SL"))
        if mode == "custom_underlying_sltp" and underlying_now > 0:
            lines.append(_scan_distance_text(ticker, underlying_now, settings.get("custom_underlying_take_profit_price"), "Underlying TP"))
            lines.append(_scan_distance_text(ticker, underlying_now, settings.get("custom_underlying_stop_loss_price"), "Underlying SL"))
        if mode == "custom_sltp" and premium_now > 0:
            lines.append(_scan_distance_text(symbol, premium_now, settings.get("custom_take_profit_price"), "Premium TP"))
            lines.append(_scan_distance_text(symbol, premium_now, settings.get("custom_stop_loss_price"), "Premium SL"))
        prior = (formatted_scan or {}).get("prior_day_ohlc") or {}
        if mode in {"pivot_points", "hybrid_pivot"} and underlying_now > 0 and prior:
            high = _entry_forecast_float(prior.get("high"), 0.0)
            low = _entry_forecast_float(prior.get("low"), 0.0)
            close = _entry_forecast_float(prior.get("close"), 0.0)
            if high > 0 and low > 0 and close > 0:
                pivot = (high + low + close) / 3.0
                r1 = (2 * pivot) - low
                s1 = (2 * pivot) - high
                if direction == "PUT":
                    lines.append(_scan_distance_text(ticker, underlying_now, s1, "Underlying Pivot TP"))
                    lines.append(_scan_distance_text(ticker, underlying_now, r1, "Underlying Pivot SL"))
                else:
                    lines.append(_scan_distance_text(ticker, underlying_now, r1, "Underlying Pivot TP"))
                    lines.append(_scan_distance_text(ticker, underlying_now, s1, "Underlying Pivot SL"))
        if mode in {"high_low", "hybrid_high_low"} and underlying_now > 0 and prior:
            levels = buffered_prior_day_levels(prior, resolve_high_low_buffer_pct(settings))
            if levels:
                if direction == "PUT":
                    lines.append(_scan_distance_text(ticker, underlying_now, levels.get("low_trigger"), "Underlying H/L TP"))
                    lines.append(_scan_distance_text(ticker, underlying_now, levels.get("high_trigger"), "Underlying H/L SL"))
                else:
                    lines.append(_scan_distance_text(ticker, underlying_now, levels.get("high_trigger"), "Underlying H/L TP"))
                    lines.append(_scan_distance_text(ticker, underlying_now, levels.get("low_trigger"), "Underlying H/L SL"))
        if mode in {"swing_points", "hybrid_swing"} and underlying_now > 0:
            swing_exit = build_swing_exit(
                (formatted_scan or {}).get("price_history", []),
                current_price=underlying_now,
                is_long=(direction != "PUT"),
                lookback_days=int(settings.get("swing_exit_lookback_days", settings.get("swing_lookback_days", 20)) or 20),
                market_session=settings.get("market_session", "Regular"),
                is_crypto=_is_crypto_symbol(ticker),
            )
            lines.append(_scan_distance_text(ticker, underlying_now, swing_exit.get("take_profit"), "Underlying Swing TP"))
            lines.append(_scan_distance_text(ticker, underlying_now, swing_exit.get("stop_loss"), "Underlying Swing SL"))
    else:
        current = premium_now or underlying_now
        if mode in {"sltp", "hybrid"} and avg_price > 0 and current > 0:
            atr = _entry_forecast_float(formatted_scan.get("atr"), 0.0)
            if atr > 0:
                sl_mult = _entry_forecast_float(settings.get("atr_stop_multiplier", 2.0), 2.0)
                tp_mult = _entry_forecast_float(settings.get("take_profit_multiplier", 4.0), 4.0)
                sl_price = avg_price - (atr * sl_mult)
                trailing = _active_trailing_stop(eng, symbol) if settings.get("enable_atr_trail", True) else None
                if trailing:
                    sl_price = trailing
                lines.append(_scan_distance_text(symbol, current, avg_price + (atr * tp_mult), "TP"))
                lines.append(_scan_distance_text(symbol, current, sl_price, "SL"))
        if mode in {"percentage", "hybrid_pct"} and avg_price > 0 and current > 0:
            sl_pct = _normalize_pct_setting(settings.get("regular_stop_loss_pct", 0.05), 0.05)
            tp_pct = _normalize_pct_setting(settings.get("regular_take_profit_pct", 0.10), 0.10)
            sl_price = avg_price * (1.0 - sl_pct)
            trailing = _active_trailing_stop(eng, symbol) if settings.get("enable_pct_trail", True) else None
            if trailing:
                sl_price = trailing
            lines.append(_scan_distance_text(symbol, current, avg_price * (1.0 + tp_pct), "TP"))
            lines.append(_scan_distance_text(symbol, current, sl_price, "SL"))
        if mode == "custom_sltp" and current > 0:
            lines.append(_scan_distance_text(symbol, current, settings.get("custom_take_profit_price"), "TP"))
            lines.append(_scan_distance_text(symbol, current, settings.get("custom_stop_loss_price"), "SL"))
        prior = (formatted_scan or {}).get("prior_day_ohlc") or {}
        if mode in {"pivot_points", "hybrid_pivot"} and current > 0 and prior:
            high = _entry_forecast_float(prior.get("high"), 0.0)
            low = _entry_forecast_float(prior.get("low"), 0.0)
            close = _entry_forecast_float(prior.get("close"), 0.0)
            if high > 0 and low > 0 and close > 0:
                levels = _dynamic_pivot_exit_levels(high, low, close, current)
                if levels:
                    lines.append(_scan_distance_text(symbol, current, levels.get("tp"), "Pivot TP"))
                    lines.append(_scan_distance_text(symbol, current, levels.get("sl"), "Pivot SL"))
        if mode in {"high_low", "hybrid_high_low"} and current > 0 and prior:
            high = _entry_forecast_float(prior.get("high"), 0.0)
            low = _entry_forecast_float(prior.get("low"), 0.0)
            if high > 0 and low > 0:
                levels = _dynamic_high_low_exit_levels(high, low, current, resolve_high_low_buffer_pct(settings))
                if levels:
                    lines.append(_scan_distance_text(symbol, current, levels.get("tp"), "H/L TP"))
                    lines.append(_scan_distance_text(symbol, current, levels.get("sl"), "H/L SL"))
        if mode in {"swing_points", "hybrid_swing"} and current > 0:
            swing_exit = build_swing_exit(
                (formatted_scan or {}).get("price_history", []),
                current_price=current,
                is_long=True,
                lookback_days=int(settings.get("swing_exit_lookback_days", settings.get("swing_lookback_days", 20)) or 20),
                market_session=settings.get("market_session", "Regular"),
                is_crypto=_is_crypto_symbol(symbol),
            )
            lines.append(_scan_distance_text(symbol, current, swing_exit.get("take_profit"), "Swing TP"))
            lines.append(_scan_distance_text(symbol, current, swing_exit.get("stop_loss"), "Swing SL"))

    return [line for line in lines if line]


def _smart_reentry_blocker_text(settings: dict, formatted_scan: dict, target_direction: str, eng: Optional[UserEngine] = None) -> str:
    cfg = getattr(eng, "config", None) if eng is not None else None
    enabled = settings.get("enable_smart_reentry")
    if enabled is None:
        enabled = settings.get("anti_chasing_enabled")
    if enabled is None and cfg is not None:
        enabled = getattr(cfg, "ENABLE_SMART_REENTRY", False)
    if not bool(enabled):
        return ""

    reason_text = str(formatted_scan.get("reason") or "")
    if reason_text.startswith("Smart Re-entry") or reason_text.startswith("Anti-chasing"):
        return reason_text

    mode = str(settings.get("smart_reentry_type", getattr(cfg, "SMART_REENTRY_TYPE", "RSI") if cfg else "RSI") or "RSI").upper()
    target_direction = str(target_direction or "BULLISH").upper()

    if mode == "RSI":
        rsi = _entry_forecast_float(formatted_scan.get("rsi"), 0.0)
        if rsi <= 0:
            return "Anti-chasing RSI waiting: live RSI unavailable."
        if target_direction == "BEARISH":
            min_rsi = _entry_forecast_float(
                settings.get("smart_reentry_rsi_put_min", getattr(cfg, "SMART_REENTRY_RSI_PUT_MIN", 30) if cfg else 30),
                30.0,
            )
            if rsi < min_rsi:
                return f"Anti-chasing RSI waiting: SELL blocked while RSI {rsi:.1f} < {min_rsi:.1f} (Oversold); needs +{(min_rsi - rsi):.1f} RSI."
        else:
            max_rsi = _entry_forecast_float(
                settings.get("smart_reentry_rsi_call_max", getattr(cfg, "SMART_REENTRY_RSI_CALL_MAX", 70) if cfg else 70),
                70.0,
            )
            if rsi > max_rsi:
                return f"Anti-chasing RSI waiting: BUY blocked while RSI {rsi:.1f} > {max_rsi:.1f} (Overbought); needs -{(rsi - max_rsi):.1f} RSI."

    elif mode == "VWAP":
        current = _entry_forecast_float(formatted_scan.get("price_raw"), 0.0)
        vwap = _entry_forecast_float(formatted_scan.get("vwap"), 0.0)
        if current <= 0 or vwap <= 0:
            return "Anti-chasing AVWAP waiting: live price/AVWAP unavailable."
        dist_pct = abs(current - vwap) / vwap * 100.0
        default_dist = getattr(cfg, "SMART_REENTRY_VWAP_PUT_MAX_DIST_PCT", 0.5) if (cfg and target_direction == "BEARISH") else (getattr(cfg, "SMART_REENTRY_VWAP_CALL_MAX_DIST_PCT", 0.5) if cfg else 0.5)
        max_dist = _entry_forecast_float(
            settings.get(
                "smart_reentry_vwap_put_max_dist_pct" if target_direction == "BEARISH" else "smart_reentry_vwap_call_max_dist_pct",
                default_dist,
            ),
            0.5,
        )
        if dist_pct > max_dist:
            side_label = "SELL" if target_direction == "BEARISH" else "BUY"
            return f"Anti-chasing AVWAP waiting: {side_label} blocked while price is {dist_pct:.2f}% from AVWAP; needs <= {max_dist:.2f}%."

    return ""


def _matching_position_for_ticker(positions: list, ticker: str, settings: dict):
    ticker_norm = _normalize_quote_symbol(ticker)
    if not settings.get("options_trading"):
        return next(
            (p for p in positions or [] if _normalize_quote_symbol(p.get("symbol", "")) == ticker_norm and float(p.get("qty", 0) or 0) > 0),
            None,
        )

    active_contract = _normalize_quote_symbol(settings.get("options_active_contract", ""))
    if active_contract:
        for pos in positions or []:
            if _normalize_quote_symbol(pos.get("symbol", "")) == active_contract and float(pos.get("qty", 0) or 0) > 0:
                return pos

    try:
        from option_selector import parse_occ_symbol
    except Exception:
        parse_occ_symbol = None

    for pos in positions or []:
        symbol = _normalize_quote_symbol(pos.get("symbol", ""))
        if float(pos.get("qty", 0) or 0) <= 0:
            continue
        if parse_occ_symbol:
            parsed = parse_occ_symbol(symbol)
            if parsed and parsed.get("underlying") == ticker_norm:
                return pos
        if symbol.startswith(ticker_norm) and len(symbol) >= 15:
            return pos
    return None


def _position_snapshot_key(position: dict, ticker: str) -> str:
    if position and position.get("symbol"):
        return _normalize_quote_symbol(position.get("symbol"))
    return _normalize_quote_symbol(ticker)


def _position_direction_label(position: dict) -> str:
    if not position:
        return "LONG"
    symbol = _normalize_quote_symbol(position.get("symbol", ""))
    option_type = str(position.get("option_type") or "").upper()
    if option_type in {"CALL", "PUT"}:
        return option_type
    if len(symbol) >= 15:
        suffix = symbol[-9]
        if suffix == "C":
            return "CALL"
        if suffix == "P":
            return "PUT"
    return "LONG"


def _build_static_entry_forecast(settings: dict, scan: dict, ticker: str) -> str:
    if settings.get("ai_autopilot"):
        timeframe = settings.get("timeframe") or "1Hour"
        model = settings.get("ai_model") or "llama-3.3-70b-versatile"
        return f"AI Autopilot | AI evaluates indicators, news, risk, entry, and exit on {timeframe} candles | Model: {model}"

    if strategy_presets.is_strategy_preset_active(settings) and not settings.get("options_trading"):
        preset = strategy_presets.get_preset(settings.get("strategy_preset"))
        label = strategy_presets.strategy_preset_label(settings)
        rule = f" | Buys when: {preset.entry_rule} The order goes out at the next trading window." if preset else ""
        return f"Strategy: {label}{rule}"

    mode = settings.get("buy_mode", "indicator")
    buy_threshold = _get_threshold_setting(settings, ("min_buy_signals", "threshold", "buy_threshold"), 1)
    pool_text = _indicator_pool_text(settings)
    parts = [f"Strategy: {_entry_strategy_label(mode)}"]

    is_option = bool(settings.get("options_trading"))
    if is_option:
        option_label = _option_direction_label(settings)
        parts.append(f"[Options] {option_label}")

    if mode in {"indicator", "hybrid", "hybrid_atr", "hybrid_pct", "hybrid_pivot", "hybrid_high_low", "hybrid_swing"}:
        if is_option:
            if option_label == "CALLS_ONLY":
                parts.append(f"Trigger: Opens CALL when {buy_threshold} enabled bullish signals confirm")
            elif option_label == "PUTS_ONLY":
                parts.append(f"Trigger: Opens PUT when {buy_threshold} enabled bearish signals confirm")
            else:
                parts.append(f"Trigger: Opens CALL on {buy_threshold} bullish signals or PUT on {buy_threshold} bearish signals")
        else:
            parts.append(f"Trigger: Opens long when {buy_threshold} enabled bullish signals confirm")

    if mode == "custom_price":
        parts.append(f"Trigger Price: {_entry_forecast_money(settings.get('custom_buy_price'))}")
    elif mode in {"atr", "hybrid_atr"}:
        mult = _entry_forecast_float(settings.get("entry_atr_multiplier", settings.get("atr_stop_multiplier", 2.0)), 2.0)
        parts.append(f"Trigger: ATR move x{mult:.1f}")
    elif mode in {"percentage", "hybrid_pct"}:
        pct = _entry_forecast_float(settings.get("entry_pct_move", 1.0), 1.0)
        parts.append(f"Trigger: Price move {pct:.1f}%")
    elif mode in {"pivot_points", "hybrid_pivot"}:
        parts.append(f"Trigger: Pivot point S/R breakout")
    elif mode in {"high_low", "hybrid_high_low"}:
        buffer_pct = resolve_high_low_buffer_pct(settings)
        parts.append(f"Trigger: Prior Day High/Low breakout ({buffer_pct:.2f}% buffer)")
    elif mode in {"swing_points", "hybrid_swing"}:
        lookback = int(settings.get("swing_entry_lookback_days", settings.get("swing_lookback_days", 20)) or 20)
        parts.append(f"Trigger: {lookback}D Swing S/R level breakout")

    if mode in {"indicator", "hybrid", "hybrid_atr", "hybrid_pct", "hybrid_pivot", "hybrid_high_low", "hybrid_swing"}:
        parts.append(f"[Indicator Pool] {pool_text}")
    return " | ".join(parts)


def _build_static_exit_forecast(settings: dict, scan: dict, position: dict, ticker: str) -> str:
    if not position:
        return ""

    sell_mode = settings.get("sell_mode") or settings.get("options_exit_strategy") or "indicator"
    avg_price = _entry_forecast_float(position.get("avg_price") or position.get("avg_entry_price"), 0.0)
    is_option = bool(settings.get("options_trading"))
    direction_label = _position_direction_label(position)
    buy_threshold = _get_threshold_setting(settings, ("min_buy_signals", "threshold", "buy_threshold"), 1)
    sell_threshold = _get_threshold_setting(settings, ("min_sell_signals", "sell_threshold"), 1)

    if settings.get("ai_autopilot"):
        parts = ["AI Autopilot Exit", f"[Position] {direction_label}"]
        if avg_price > 0:
            parts.append(f"[Entry] {_format_quote_price(ticker, avg_price)}")
        parts.append("Trigger: AI evaluates trend invalidation, SL/TP, and risk for exit")
        return " | ".join(parts)

    if strategy_presets.is_strategy_preset_active(settings) and not settings.get("options_trading"):
        preset = strategy_presets.get_preset(settings.get("strategy_preset"))
        parts = [f"Strategy exit: {strategy_presets.strategy_preset_label(settings)}", f"[Position] {direction_label}"]
        if avg_price > 0:
            parts.append(f"[Entry] {_format_quote_price(ticker, avg_price)}")
        if preset:
            parts.append(f"Sells when: {preset.exit_rule}")
            parts.append(f"Protective stop {preset.protective_stop_atr_mult:g}x ATR below your average cost (software)")
        return " | ".join(parts)

    parts = [f"Exit Strategy: {_exit_strategy_label(sell_mode)}"]
    parts.append(f"[Position] {direction_label}")
    if avg_price > 0:
        parts.append(f"[Entry] {_format_quote_price(ticker, avg_price)}")

    if sell_mode in {"indicator", "hybrid", "hybrid_pct", "hybrid_pivot", "hybrid_high_low", "hybrid_swing"}:
        if direction_label == "PUT":
            parts.append(f"Trigger: Exits when {buy_threshold} enabled bullish signals confirm")
        else:
            parts.append(f"Trigger: Exits when {sell_threshold} enabled bearish signals confirm")

    if sell_mode in {"sltp", "hybrid"}:
        atr_val = _entry_forecast_float((scan or {}).get("atr"), 0.0)
        stop_mult = _entry_forecast_float(settings.get("atr_stop_multiplier", 2.0), 2.0)
        tp_mult = _entry_forecast_float(settings.get("take_profit_multiplier", 4.0), 4.0)
        if atr_val > 0 and avg_price > 0:
            if direction_label == "PUT":
                sl_price = avg_price + (atr_val * stop_mult)
                tp_price = avg_price - (atr_val * tp_mult)
            else:
                sl_price = avg_price - (atr_val * stop_mult)
                tp_price = avg_price + (atr_val * tp_mult)
            parts.append(f"Trigger SL: {_format_quote_price(ticker, sl_price)}")
            parts.append(f"Trigger TP: {_format_quote_price(ticker, tp_price)}")

    if sell_mode in {"percentage", "hybrid_pct", "premium_pct"} and avg_price > 0:
        if is_option:
            sl_pct = _normalize_pct_setting(settings.get("options_stop_loss_pct", settings.get("regular_stop_loss_pct", 0.10)), 0.10)
            tp_pct = _normalize_pct_setting(settings.get("options_take_profit_pct", settings.get("regular_take_profit_pct", 0.20)), 0.20)
        else:
            sl_pct = _normalize_pct_setting(settings.get("regular_stop_loss_pct", 0.05), 0.05)
            tp_pct = _normalize_pct_setting(settings.get("regular_take_profit_pct", 0.10), 0.10)
        if direction_label == "PUT":
            sl_price = avg_price * (1 + sl_pct)
            tp_price = avg_price * (1 - tp_pct)
        else:
            sl_price = avg_price * (1 - sl_pct)
            tp_price = avg_price * (1 + tp_pct)
        parts.append(f"Trigger SL: {_format_quote_price(ticker, sl_price)} ({sl_pct*100:.1f}%)")
        parts.append(f"Trigger TP: {_format_quote_price(ticker, tp_price)} ({tp_pct*100:.1f}%)")

    if sell_mode == "custom_sltp":
        parts.append(f"Trigger SL: {_entry_forecast_money(settings.get('custom_stop_loss_price'))}")
        parts.append(f"Trigger TP: {_entry_forecast_money(settings.get('custom_take_profit_price'))}")

    if sell_mode == "custom_underlying_sltp":
        parts.append(f"Trigger Underlying SL: {_entry_forecast_money(settings.get('custom_underlying_stop_loss_price'))}")
        parts.append(f"Trigger Underlying TP: {_entry_forecast_money(settings.get('custom_underlying_take_profit_price'))}")

    if sell_mode in {"indicator", "hybrid", "hybrid_pct", "hybrid_pivot", "hybrid_high_low", "hybrid_swing"}:
        parts.append(f"[Indicator Pool] {_indicator_pool_text(settings)}")

    return " | ".join(parts)


def _clear_forecast_exit_snapshots(settings: dict):
    settings.pop("forecast_exit_snapshots", None)
    settings.pop("forecast_entry_snapshot", None)


def _ensure_entry_forecast_snapshot(settings: dict, scan: dict, ticker: str) -> tuple[str, bool]:
    if strategy_presets.is_strategy_preset_active(settings) and not settings.get("options_trading"):
        # A strategy's forecast is a fixed sentence about its rule: never cache it (an old snapshot may carry an unnamed label).
        return _build_static_entry_forecast(settings, scan or {}, ticker), False
    settings_hash = _forecast_settings_hash(settings)
    snapshot = settings.get("forecast_entry_snapshot") or {}
    snapshot_text = str(snapshot.get("text") or "")
    snapshot_matches_mode = bool(settings.get("ai_autopilot")) == ("AI Autopilot" in snapshot_text)
    if snapshot_text and snapshot.get("settings_hash") == settings_hash and snapshot_matches_mode and "[Entry Strategy]" not in snapshot_text and "[Exit Strategy]" not in snapshot_text:
        return snapshot.get("text"), False
    forecast_text = _build_static_entry_forecast(settings, scan or {}, ticker)
    settings["forecast_entry_snapshot"] = {
        "text": forecast_text,
        "settings_hash": settings_hash,
        "created_at": get_now().isoformat(),
    }
    return forecast_text, True


def _ensure_exit_forecast_snapshot(settings: dict, scan: dict, position: dict, ticker: str) -> tuple[str, bool]:
    if not position:
        return "", False
    if strategy_presets.is_strategy_preset_active(settings) and not settings.get("options_trading"):
        return _build_static_exit_forecast(settings, scan or {}, position, ticker), False
    settings_hash = _forecast_settings_hash(settings)
    snapshot_key = _position_snapshot_key(position, ticker)
    snapshots = settings.setdefault("forecast_exit_snapshots", {})
    snapshot = snapshots.get(snapshot_key) or {}
    snapshot_text = str(snapshot.get("text") or "")
    entry_price = _entry_forecast_float(position.get("avg_price") or position.get("avg_entry_price"), 0.0)
    qty = _entry_forecast_float(position.get("qty"), 0.0)
    if (
        snapshot_text
        and snapshot.get("settings_hash") == settings_hash
        and (bool(settings.get("ai_autopilot")) == ("AI Autopilot" in snapshot_text))
        and abs(_entry_forecast_float(snapshot.get("entry_price"), 0.0) - entry_price) < 1e-8
        and abs(_entry_forecast_float(snapshot.get("qty"), 0.0) - qty) < 1e-8
        and "[Entry Strategy]" not in snapshot_text
        and "[Exit Strategy]" not in snapshot_text
    ):
        return snapshot.get("text"), False
    forecast_text = _build_static_exit_forecast(settings, scan or {}, position, ticker)
    snapshots.clear()
    snapshots[snapshot_key] = {
        "text": forecast_text,
        "settings_hash": settings_hash,
        "entry_price": entry_price,
        "qty": qty,
        "created_at": get_now().isoformat(),
    }
    return forecast_text, True


def _strategy_next_scan_at(timing) -> Optional[str]:
    """ISO time a strategy says its next evaluation happens (display only), or None when it has no schedule of its own
    or the time is not in the future (an overdue or due-now evaluation falls back to the normal scan countdown)."""
    try:
        raw = (timing or {}).get("next_evaluation_at")
        if not raw:
            return None
        when = raw if isinstance(raw, datetime) else datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
        if when.tzinfo is None:
            when = when.replace(tzinfo=pytz.UTC)
        if when <= datetime.now(pytz.UTC) + timedelta(seconds=1):
            return None
        return when.isoformat()
    except Exception:
        return None


def _scan_next_due_at(updated_at, refresh_seconds) -> Optional[str]:
    """ISO time of the next scheduled scan (display only), or None when the last scan time is unknown."""
    try:
        base = datetime.fromisoformat(str(updated_at).replace("Z", "+00:00"))
        return (base + timedelta(seconds=max(1, int(refresh_seconds)))).isoformat()
    except Exception:
        return None


def _resolve_scan_log_phase(settings: dict, position: dict) -> tuple[str, str, int, dict]:
    buy_threshold = _get_threshold_setting(settings, ("min_buy_signals", "threshold", "buy_threshold"), 1)
    sell_threshold = _get_threshold_setting(settings, ("min_sell_signals", "sell_threshold"), 1)
    is_option = bool(settings.get("options_trading"))
    context = _scan_log_strategy_context(settings, position)

    if position:
        direction = _position_direction_label(position)
        if is_option and direction == "PUT":
            return "EXIT", "BULLISH", buy_threshold, context
        return "EXIT", "BEARISH", sell_threshold, context

    option_type = normalize_options_type(settings.get("options_type", "calls_only"), default="calls_only")
    if is_option and option_type not in VALID_OPTION_DIRECTIONS:
        return "ENTRY", "DISABLED", buy_threshold, context
    if is_option and option_type == "puts_only":
        return "ENTRY", "BEARISH", sell_threshold, context
    return "ENTRY", "BULLISH", buy_threshold, context


def _resolve_scan_log_target(settings: dict, formatted_scan: dict, position: dict) -> tuple[str, str, int, dict]:
    phase, target_direction, required_count, context = _resolve_scan_log_phase(settings, position)
    return phase, target_direction, required_count, context


def _scan_setup_info(settings: dict, preset_owned: bool) -> dict:
    """What decides this bot's entries and exits, for the card's setup badges."""
    if preset_owned:
        preset = strategy_presets.get_preset(settings.get("strategy_preset"))
        return {"kind": "strategy", "strategy": strategy_presets.strategy_preset_label(settings),
                "entry": preset.entry_rule if preset else "", "exit": preset.exit_rule if preset else ""}
    if settings.get("ai_autopilot"):
        return {"kind": "manual", "entry": "AI Autopilot", "exit": "AI Autopilot"}
    options = bool(settings.get("options_trading") or settings.get("is_option"))
    exit_mode = settings.get("sell_mode") or (settings.get("options_exit_strategy") if options else None) or "indicator"
    return {"kind": "manual", "entry": _entry_strategy_label(settings.get("buy_mode", "indicator")), "exit": _exit_strategy_label(exit_mode)}


def _compact_number(value, fallback, decimals: int = 2) -> str:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return str(fallback)
    return f"{number:.{decimals}f}".rstrip("0").rstrip(".") if "." in f"{number:.{decimals}f}" else f"{number:.{decimals}f}"


def _compact_percent(value, fallback) -> str:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return f"{fallback}%"
    return f"{_compact_number(number * 100 if number <= 1 else number, fallback)}%"


def _setup_style_label(style) -> str:
    names = {"pullback_reclaim": "pullback/reclaim", "breakout_momentum": "breakout/momentum", "dip_reclaim": "dip/reclaim",
             "bounce_retest": "bounce/retest", "rejection_breakdown": "rejection/breakdown"}
    key = str(style or "").lower()
    return names.get(key) or key.replace("_", " ") or "default"


def _scan_setup_config_lines(ticker: str, settings: dict, preset_owned: bool) -> dict:
    """Configured numbers behind a manual bot's entry and exit setup (the Bots card's Entry/Exit Setup). Display only."""
    if preset_owned or settings.get("ai_autopilot"):
        return {"entry": [], "exit": []}
    options = bool(settings.get("options_trading") or settings.get("is_option"))
    buy_mode = str(settings.get("buy_mode") or "indicator")
    sell_mode = str(settings.get("sell_mode") or (settings.get("options_exit_strategy") if options else None) or "indicator")
    names = _selected_indicator_names(settings)
    puts_only = options and normalize_options_type(settings.get("options_type", "calls_only"), default="calls_only") == "puts_only"

    def indicator_line(threshold_keys, word):
        needed = _get_threshold_setting(settings, threshold_keys, 1)
        if not names:
            return "Indicators Config: select at least one indicator to enable this setup"
        return f"Indicators Config: {', '.join(names)} ({needed or '—'} of {len(names)} {word} confirmation{'' if needed == 1 else 's'} needed)"

    entry, exit_ = [], []
    if buy_mode == "indicator" or buy_mode in _HYBRID_ENTRY_MODES:
        entry.append(indicator_line(("min_sell_signals", "sell_threshold") if puts_only else ("min_buy_signals", "threshold", "buy_threshold"),
                                    "bearish" if puts_only else "bullish"))
    if buy_mode in {"atr", "hybrid_atr"}:
        multiplier = settings.get("entry_atr_multiplier", settings.get("atr_stop_multiplier", 2))
        entry.append(f"ATR config: {_compact_number(multiplier, 2)}× volatility · {_setup_style_label(settings.get('entry_atr_style'))}")
    elif buy_mode in {"percentage", "hybrid_pct"}:
        entry.append(f"PCT config: {_compact_number(settings.get('entry_pct_move'), 5)}% move · {_setup_style_label(settings.get('entry_pct_style'))}")
    elif buy_mode == "custom_price" and _entry_forecast_float(settings.get("custom_buy_price"), 0.0) > 0:
        entry.append(f"Price config: buy at or below {_format_quote_price(ticker, settings.get('custom_buy_price'))}")
    elif buy_mode in {"swing_points", "hybrid_swing"}:
        entry.append(f"Structure config: {_compact_number(settings.get('swing_entry_lookback_days', 20), 20, 0)}-day swing lookback · {_setup_style_label(settings.get('swing_entry_mode') or 'bounce_retest')}")

    if sell_mode == "indicator" or sell_mode in _HYBRID_EXIT_MODES:
        exit_.append(indicator_line(("min_sell_signals", "sell_threshold"), "bearish"))
    if sell_mode in {"sltp", "hybrid"}:
        trail_state = "off" if settings.get("enable_atr_trail") is False else "on"
        exit_.append(f"ATR config: SL {_compact_number(settings.get('atr_stop_multiplier', 2), 2)}× · TP {_compact_number(settings.get('take_profit_multiplier', settings.get('atr_take_profit_multiplier', 4)), 4)}× · trail {_compact_number(settings.get('atr_trail_multiplier', 3), 3)}× ({trail_state})")
    elif sell_mode in {"percentage", "hybrid_pct"}:
        prefix = "options" if options else "regular"
        trail_state = "on" if settings.get("enable_pct_trail") is not False else "off"
        exit_.append(f"PCT config: SL {_compact_percent(settings.get(prefix + '_stop_loss_pct'), 10 if options else 5)} · TP {_compact_percent(settings.get(prefix + '_take_profit_pct'), 20 if options else 10)} · trail {_compact_percent(settings.get(prefix + '_trailing_pct'), 5)} ({trail_state})")
    elif sell_mode in {"custom_sltp", "custom_underlying_sltp"}:
        stop = settings.get("custom_stop_loss_price") or settings.get("custom_underlying_stop_loss_price")
        target = settings.get("custom_take_profit_price") or settings.get("custom_underlying_take_profit_price")
        if _entry_forecast_float(stop, 0.0) > 0 or _entry_forecast_float(target, 0.0) > 0:
            exit_.append(f"Price config: SL {_format_quote_price(ticker, stop) if _entry_forecast_float(stop, 0.0) > 0 else 'unset'} · TP {_format_quote_price(ticker, target) if _entry_forecast_float(target, 0.0) > 0 else 'unset'}")
    elif sell_mode in {"swing_points", "hybrid_swing"}:
        exit_.append(f"Structure config: {_compact_number(settings.get('swing_exit_lookback_days', settings.get('swing_lookback_days', 20)), 20, 0)}-day swing lookback · {_setup_style_label(settings.get('swing_exit_mode') or 'rejection_breakdown')}")
    return {"entry": entry, "exit": exit_, "buy_mode": buy_mode, "sell_mode": sell_mode}


def _scan_policy_status(eng: UserEngine, ticker: str, settings: dict, has_position: bool, has_open_order: bool) -> dict | None:
    """Scan timing a strategy imposes on this bot, for the Scan Log (None = the normal refresh)."""
    if settings.get("options_trading") or settings.get("is_option"):
        return None
    policy = strategy_presets.effective_scan_policy(settings)
    if policy is None:
        return None
    marker = (getattr(eng, "preset_scan_markers", None) or {}).get(ticker)
    idle = bool(
        marker
        and not has_position
        and not has_open_order
        and tuple(marker.get("fingerprint") or ()) == preset_runtime.scan_fingerprint(settings)
    )
    if idle:
        label = "Strategy evaluates once per completed daily candle: nothing to do until the next one."
    elif has_position or has_open_order:
        label = "Strategy scan timing: an open position or order is watched at the normal refresh while the session is open."
    else:
        label = "Strategy evaluates once per completed daily candle (the normal refresh does not apply while flat)."
    return {
        "mode": policy["mode"],
        "idle": idle,
        "next_evaluation_at": marker.get("next_evaluation_at") if idle else None,
        "label": label,
    }


def _build_scan_log(
    ticker: str,
    formatted_scan: dict,
    settings: dict,
    position: dict,
    pending_orders: list,
    account: dict,
    previous_scan_log: dict,
    eng: UserEngine,
    positions: list,
    exit_protection: dict = None,
    ignore_market_closed: bool = False,
) -> dict:
    phase, target_direction, required_count, strategy_context = _resolve_scan_log_target(settings, formatted_scan, position)
    preset_owned = strategy_presets.is_strategy_preset_active(settings) and not (settings.get("options_trading") or settings.get("is_option"))
    setup_info = _scan_setup_info(settings, preset_owned)
    forced_scan = formatted_scan.get("forced_scan") if isinstance(formatted_scan.get("forced_scan"), dict) else None
    if forced_scan and forced_scan.get("status") == "failed":
        # The first (forced) evaluation did not complete: say so on the card; the trading loop retries on its next pass.
        return {
            "setup": setup_info,
            "summary": "Initial scan failed, will retry",
            "detail": f"The first evaluation could not complete ({forced_scan.get('message') or 'unknown error'}). The trading loop scans this bot on its next pass.",
            "status": "SCAN_FAILED",
            "phase": phase,
            "decision": "HOLD",
            "target_direction": target_direction,
            "strategy_mode": strategy_context["mode"],
            "strategy_label": strategy_context["label"],
            "blockers": [],
            "indicators": [],
            "required_count": 0, "confirmed_count": 0, "waiting_count": 0, "opposing_count": 0, "remaining_count": 0,
            "forced_scan": forced_scan,
            "strategy_log": None,
            "scan_policy": None,
            "updated_at": formatted_scan.get("time") or get_now().isoformat(),
            "refresh_rate_sec": _bot_scan_refresh_seconds(eng, settings, bool(position), isinstance(settings.get("pending_fill"), dict)),
            "scan_interval_mode": "auto" if bot_scan_interval._truthy(settings.get("scan_interval_auto")) else ("custom" if bot_scan_interval.is_configured(settings) else "default"),
            "is_option": bool(settings.get("options_trading", settings.get("is_option", False))),
        }
    continuous_grid_forecast = _build_continuous_grid_forecast(settings, formatted_scan, position, ticker)
    continuous_grid_enabled = bool(continuous_grid_forecast.get("enabled"))
    selected_indicators = _selected_indicator_names(settings)
    has_indicator_pool = bool(selected_indicators)
    strategy_uses_indicators = bool(strategy_context["uses_indicators"])
    show_indicator_leg = strategy_uses_indicators and has_indicator_pool
    uses_structural = bool(strategy_context["uses_structural"])
    strategy_label = strategy_context["label"]
    condition_logic = _scan_log_condition_logic(strategy_context)
    decision = str(formatted_scan.get("action") or "HOLD").upper()

    if settings.get("options_trading") or settings.get("is_option"):
        opt_status = eng.get_options_market_status()
        if not opt_status["is_active_window"]:
            return {
                "setup": setup_info,
                "summary": f"🛑 Options Market Closed. Starts scanning 2m before open ({opt_status['next_open_str']}).",
                "detail": f"Starts scanning in {_format_duration_compact(opt_status['seconds_to_pre_open'])} (2m prior to market opening).",
                "status": "MARKET_CLOSED",
                "is_options_market_closed": True,
                "phase": phase,
                "decision": "HOLD",
                "target_direction": target_direction,
                "strategy_mode": strategy_context["mode"],
                "strategy_label": strategy_label,
                "uses_indicators": strategy_uses_indicators,
                "uses_structural": uses_structural,
                "show_indicator_leg": show_indicator_leg,
                "seconds_to_open": opt_status["seconds_to_open"],
                "seconds_to_pre_open": opt_status["seconds_to_pre_open"],
                "next_open_str": opt_status["next_open_str"],
                "refresh_rate_sec": max(10, min(3600, opt_status["seconds_to_pre_open"])),
                "is_option": True,
                "continuous_grid_forecast": continuous_grid_forecast,
            }
    elif not _is_crypto_symbol(ticker) and not ignore_market_closed:
        market_session = str(settings.get("market_session", "Regular")).strip().lower()
        stock_status = eng.get_stock_market_status(market_session)
        if not stock_status["is_active_window"]:
            session_label = stock_status["market_session_label"]
            # The last real scan stays visible while the market is closed (the card shows it under the banner).
            last_scan_log = None
            last_raw = formatted_scan.get("last_real_scan")
            if isinstance(last_raw, dict):
                try:
                    last_scan_log = _build_scan_log(
                        ticker, _format_scan_for_ui(last_raw), settings, position, pending_orders, account,
                        {}, eng, positions, exit_protection, ignore_market_closed=True,
                    )
                except Exception as exc:
                    print(f"[scan-log] last scan unavailable for {ticker}: {_safe_exception_type(exc)}")
            return {
                "setup": setup_info,
                "last_scan": last_scan_log,
                "summary": f"🛑 {session_label} Market Closed. Starts scanning 2m before open ({stock_status['next_open_str']}).",
                "detail": f"Starts scanning in {_format_duration_compact(stock_status['seconds_to_pre_open'])} (2m prior to market opening).",
                "status": "MARKET_CLOSED",
                "is_stock_market_closed": True,
                "market_session": session_label,
                "phase": phase,
                "decision": "HOLD",
                "target_direction": target_direction,
                "strategy_mode": strategy_context["mode"],
                "strategy_label": strategy_label,
                "uses_indicators": strategy_uses_indicators,
                "uses_structural": uses_structural,
                "show_indicator_leg": show_indicator_leg,
                "seconds_to_open": stock_status["seconds_to_open"],
                "seconds_to_pre_open": stock_status["seconds_to_pre_open"],
                "next_open_str": stock_status["next_open_str"],
                "refresh_rate_sec": max(60, min(3600, stock_status["seconds_to_pre_open"])),
                "is_option": False,
                "continuous_grid_forecast": continuous_grid_forecast,
            }

    signals = formatted_scan.get("signals", {}) or {}
    indicators = []
    confirmed_weight = 0
    confirmed_count = 0
    waiting_count = 0
    opposing_count = 0

    if show_indicator_leg:
        for name, data in signals.items():
            if name not in selected_indicators:
                continue
            if not data.get("enabled"):
                continue
            signal = str(data.get("signal") or "NEUTRAL").upper()
            reason = _signal_reason_text(data)
            weight = int(data.get("weight", 1) or 1)

            if signal == target_direction:
                status = "CONFIRMED"
                confirmed_weight += weight
                confirmed_count += 1
            elif signal == "NEUTRAL":
                status = "WAITING"
                waiting_count += 1
            else:
                status = "OPPOSING"
                opposing_count += 1

            indicators.append({
                "name": name,
                "signal": signal,
                "status": status,
                "reason": reason,
                "enabled": True,
                "supports_target": status == "CONFIRMED",
                "weight": weight,
            })

    remaining_count = max(0, required_count - confirmed_weight) if show_indicator_leg else 0
    blockers = []
    is_crypto = _is_crypto_symbol(ticker)
    market_session = str(settings.get("market_session", "Regular")).strip().lower()
    session_allows_outside_rth = market_session in {"extended", "24h", "crypto24", "all"}

    if settings.get("paused"):
        blockers.append(settings.get("pause_reason") or "Bot paused.")

    if settings.get("options_trading") and normalize_options_type(settings.get("options_type", "calls_only"), default="calls_only") not in VALID_OPTION_DIRECTIONS:
        blockers.append("Legacy Both direction disabled. Choose CALL or PUT.")

    if formatted_scan.get("price_raw", 0) <= 0:
        blockers.append("Live quote unavailable.")

    # (When the last real scan is shown under the closed-market banner, "market closed" is the banner's job, not a blocker.)
    if not is_crypto and not ignore_market_closed and eng.config.get("MARKET_HOURS_ONLY", True):
        is_opt_check = settings.get("options_trading") or settings.get("is_option")
        if is_opt_check:
            if not eng.get_options_market_status()["is_open"]:
                blockers.append("Market closed for the configured session.")
        else:
            if not eng.get_stock_market_status(market_session)["is_open"]:
                blockers.append("Market closed for the configured session.")

    has_pending_order = False
    ticker_norm = _normalize_quote_symbol(ticker)
    option_contract = _normalize_quote_symbol(settings.get("options_active_contract", ""))
    for order in pending_orders or []:
        symbol = _normalize_quote_symbol(order.get("symbol", ""))
        side = str(order.get("side", "")).lower()
        if option_contract and symbol == option_contract:
            has_pending_order = True
        elif symbol == ticker_norm:
            has_pending_order = True
        elif settings.get("options_trading") and symbol.startswith(ticker_norm) and len(symbol) >= 15 and side == "buy":
            has_pending_order = True
    if has_pending_order:
        blockers.append("Order pending.")
    scan_refresh_seconds = _bot_scan_refresh_seconds(
        eng, settings, bool(position), has_pending_order or isinstance(settings.get("pending_fill"), dict)
    )
    # Display only: how an open position is watched for its exit (a small badge on the Sell Evidence). Strategy presets that
    # own their schedule show their own countdown instead.
    exit_watch = None
    if position and not preset_owned:
        kind = bot_scan_interval.exit_watch_kind(settings, is_option=bool(settings.get("options_trading") or settings.get("is_option")))
        exit_watch = {"kind": kind, "interval_sec": scan_refresh_seconds}

    if strategy_uses_indicators and not has_indicator_pool:
        blockers.append(f"No indicator pool selected for {strategy_label}.")

    live_levels = (
        _continuous_grid_scan_levels(ticker, continuous_grid_forecast)
        if continuous_grid_enabled
        else (
            _scan_log_exit_levels(ticker, formatted_scan, settings, position, exit_protection, eng=eng)
            if phase == "EXIT"
            else _scan_log_entry_levels(ticker, formatted_scan, settings, target_direction)
        )
    )

    is_cooldown_active = False
    is_signal_reset_active = False
    # A strategy owns its entries and exits and switches the manual re-entry rules (cooldown, signal reset,
    # smart re-entry) off, so none of those may show up as a blocker for it.
    if phase == "ENTRY" and preset_owned:
        if settings.get("enable_risk_management", True):
            max_positions = int(getattr(eng.config, "MAX_OPEN_POSITIONS", 5) or 5)
            if len(positions or []) >= max_positions:
                blockers.append(f"Max positions reached ({len(positions or [])}/{max_positions}).")
    elif phase == "ENTRY":
        adaptive_flat_reentry = bool(
            continuous_grid_enabled
            and not position
            and (continuous_grid_forecast.get("entry") or {}).get("flat_reentry_armed")
        )
        if settings.get("enable_cooldown", True) and ticker in eng.last_trade_timestamps:
            tf_seconds = max(1, _timeframe_to_seconds(str(settings.get("timeframe") or "5Min")))
            cooldown_minutes = settings.get("cooldown_minutes")
            cooldown_secs = int(cooldown_minutes * 60) if cooldown_minutes is not None else int(tf_seconds * 4)
            elapsed = (eng.get_now() - eng.last_trade_timestamps[ticker]).total_seconds()
            if elapsed < cooldown_secs:
                rem_secs = max(1, int(cooldown_secs - elapsed))
                blockers.append(f"⏳ Trade Cooldown Active ({_format_duration_compact(rem_secs)} remaining after trade exit).")
                is_cooldown_active = True

        if settings.get("enable_signal_reset", True) and not adaptive_flat_reentry:
            locked_dir = eng._get_signal_reset_lock(ticker, settings)
            if locked_dir:
                blockers.append(f"🔄 Signal Reset Active: Waiting for {locked_dir} signal to reset below threshold.")
                is_signal_reset_active = True

        smart_reentry_text = _smart_reentry_blocker_text(settings, formatted_scan, target_direction, eng)
        if smart_reentry_text:
            blockers.append(smart_reentry_text)

        if settings.get("enable_risk_management", True):
            max_positions = int(getattr(eng.config, "MAX_OPEN_POSITIONS", 5) or 5)
            if len(positions or []) >= max_positions:
                blockers.append(f"Max positions reached ({len(positions or [])}/{max_positions}).")

    strategy_info = formatted_scan.get("strategy_preset") if isinstance(formatted_scan.get("strategy_preset"), dict) else {}
    if preset_owned and str(strategy_info.get("action") or "").upper() in ("BUY", "SELL") and decision == "HOLD" and formatted_scan.get("reason"):
        # The strategy wanted to act but a runtime gate held it back (window closed, signal already acted on,
        # split pause, an unmanaged position...): that gate's own text is the blocker.
        blockers.append(str(formatted_scan.get("reason")))

    status = "WAITING_EXIT" if phase == "EXIT" else "WAITING_ENTRY"
    if is_cooldown_active:
        status = "COOLDOWN"
    elif is_signal_reset_active:
        status = "SIGNAL_RESET"
    elif blockers:
        status = "BLOCKED"
    elif decision in {"BUY", "SELL"} and (not show_indicator_leg or remaining_count == 0):
        status = "READY"
    elif continuous_grid_enabled:
        status = "GRID_MONITORING"
    elif show_indicator_leg and remaining_count <= 1:
        status = "NEAR_EXIT" if phase == "EXIT" else "NEAR_ENTRY"

    if show_indicator_leg and status not in {"BLOCKED", "READY"} and previous_scan_log and previous_scan_log.get("phase") == phase and previous_scan_log.get("target_direction") == target_direction:
        prev_remaining = previous_scan_log.get("remaining_count")
        prev_confirmed_weight = previous_scan_log.get("confirmed_weight", previous_scan_log.get("confirmed_count"))
        if isinstance(prev_remaining, int) and isinstance(prev_confirmed_weight, int):
            if remaining_count < prev_remaining or confirmed_weight > prev_confirmed_weight:
                status = "MOVING_CLOSER"
            elif remaining_count > prev_remaining or confirmed_weight < prev_confirmed_weight:
                status = "MOVING_AWAY"

    confirmed_marker = "🔴" if target_direction == "BEARISH" else "🟢"
    opposing_marker = "🟢" if target_direction == "BEARISH" else "🔴"
    confirmed_text = ", ".join(
        f"{confirmed_marker} {item['name']} {item['signal']}" + (f" ({item['reason']})" if item["reason"] else "")
        for item in indicators if item["status"] == "CONFIRMED"
    )
    waiting_text = ", ".join(
        f"🟡 {item['name']} {item['signal']}" + (f" ({item['reason']})" if item["reason"] else "")
        for item in indicators if item["status"] == "WAITING"
    )
    opposing_text = ", ".join(
        f"{opposing_marker} {item['name']} {item['signal']}" + (f" ({item['reason']})" if item["reason"] else "")
        for item in indicators if item["status"] == "OPPOSING"
    )

    status_label = status.replace("_", " ").title()
    if show_indicator_leg:
        indicator_label = f"{target_direction} {confirmed_weight}/{required_count} confirmed"
        if remaining_count > 0:
            indicator_label += f", needs {remaining_count}"
    else:
        indicator_label = ""

    current_for_label = (
        _entry_forecast_float(position.get("current_price"), 0.0)
        if position
        else _entry_forecast_float(formatted_scan.get("price_raw"), 0.0)
    )
    current_symbol = (position or {}).get("symbol") or ticker
    watch_parts = ["Adaptive grid watch" if continuous_grid_enabled else f"{phase.title()} watch"]
    if current_for_label > 0:
        watch_parts.append(f"Current {_format_quote_price(current_symbol, current_for_label)}")
    if live_levels:
        watch_parts.extend(live_levels[:2])
    elif uses_structural:
        watch_parts.append(str(formatted_scan.get("reason") or "Waiting for next live level"))
    if condition_logic:
        watch_parts.append(condition_logic)
    if indicator_label:
        watch_parts.append("Signals: " + indicator_label)
    progress_label = " | ".join(watch_parts)

    summary_parts = [status_label, decision, progress_label]
    if blockers:
        summary_parts.append(blockers[0])

    detail_parts = []
    if blockers:
        detail_parts.append("Blockers: " + " | ".join(blockers))
    if exit_protection and exit_protection.get("status"):
        detail_parts.append("Protection: " + str(exit_protection.get("status")))
    if live_levels:
        detail_parts.append("Live levels: " + " | ".join(live_levels))
    if confirmed_text:
        detail_parts.append("Confirmed: " + confirmed_text)
    if waiting_text:
        detail_parts.append("Waiting: " + waiting_text)
    if opposing_text:
        detail_parts.append("Opposing: " + opposing_text)
    if not detail_parts:
        detail_parts.append(str(formatted_scan.get("reason") or "Waiting for next heartbeat."))

    scan_policy_info = _scan_policy_status(
        eng, ticker, settings,
        has_position=bool(position),
        has_open_order=any(
            str((o or {}).get("symbol", "")).replace("/", "").upper() == str(ticker).replace("/", "").upper()
            for o in (pending_orders or [])
        ),
    )
    strategy_log = strategy_scan_log.scan_view(settings, formatted_scan, position, scan_policy_info, blockers) if preset_owned else None
    # When a strategy sets its own schedule (Session Rider buy/sell times, Malik's daily decision, a once-per-candle policy)
    # the card counts down to THAT time instead of the 60s refresh. Display only: the engine's own wake-up logic is unchanged.
    next_scan_at = _strategy_next_scan_at((strategy_log or {}).get("timing"))
    if strategy_log:
        status = strategy_log["status"]
        summary_parts = [strategy_log["status_label"], strategy_log["headline"]] + ([blockers[0]] if blockers else [])
        detail_parts = [item for item in ([blockers[0]] if blockers else []) + [strategy_log["headline"]] if item]

    return {
        "setup": setup_info,
        "forced_scan": forced_scan,
        "summary": " | ".join(summary_parts),
        "detail": " | ".join(detail_parts),
        "status": status,
        "phase": phase,
        "decision": decision,
        "target_direction": target_direction,
        "strategy_mode": strategy_context["mode"],
        "strategy_label": strategy_label,
        "condition_logic": condition_logic,
        "uses_indicators": strategy_uses_indicators,
        "uses_structural": uses_structural,
        "show_indicator_leg": show_indicator_leg,
        "progress_label": progress_label,
        "live_levels": live_levels,
        "required_count": required_count,
        "confirmed_count": confirmed_count,
        "confirmed_weight": confirmed_weight,
        "waiting_count": waiting_count,
        "opposing_count": opposing_count,
        "remaining_count": remaining_count,
        "blockers": blockers,
        "exit_protection": exit_protection or {},
        "continuous_grid_forecast": continuous_grid_forecast,
        "indicators": indicators,
        "updated_at": formatted_scan.get("time") or get_now().isoformat(),
        "is_option": bool(settings.get("options_trading", settings.get("is_option", False))),
        "refresh_rate_sec": scan_refresh_seconds,
        "exit_scan": exit_watch,
        "next_scan_at": next_scan_at,
        "next_scan_source": "strategy" if next_scan_at else "interval",
        "scan_interval_capped": scan_refresh_seconds < bot_scan_interval.effective_seconds(settings, _bot_scan_interval_defaults(eng, settings)),
        "scan_interval_mode": "auto" if bot_scan_interval._truthy(settings.get("scan_interval_auto")) else ("custom" if bot_scan_interval.is_configured(settings) else "default"),
        "scan_policy": scan_policy_info,
        "strategy_log": strategy_log,
        # Display-only fields for the Bots card; nothing in the engine reads them.
        "lifecycle": "ORDER_PENDING" if has_pending_order else ("IN_POSITION" if position else "FLAT"),
        "entry_guard": {"reason": str(formatted_scan.get("entry_guard_reason") or "")} if (not position and formatted_scan.get("entry_guard_reason")) else None,
        "setup_config": _scan_setup_config_lines(ticker, settings, bool(preset_owned)),
        "next_due_at": _scan_next_due_at(formatted_scan.get("time"), scan_refresh_seconds),
    }

@app.get("/api/dashboard")
async def get_dashboard(
    request: Request,
    mode: str = "fast",
    ticker: str = None,
    timeframe: str = None,
    source: str = "dashboard",
    include_primary_analysis: bool = False,
    eng: UserEngine = Depends(get_user_engine),
):
    """
    Main dashboard endpoint — returns everything the UI needs in one call.
    """
    from user_config import active_evaluation_context
    ctx = source if source in ["dashboard", "bots", "backtest"] else "dashboard"
    is_dashboard_request = ctx == "dashboard"
    include_bot_payload = ctx == "bots"
    active_evaluation_context.set(ctx)

    if ctx == "backtest" and not ticker and not timeframe:
        has_keys = bool(getattr(eng.config, "ALPACA_API_KEY", ""))
        broker_simulation = bool(getattr(eng.broker, "simulation_mode", True))
        return {
            "simulation": (not has_keys) or broker_simulation,
            "has_keys": has_keys,
            "indicator_overrides": eng.config.contexts.get("backtest", {}).get("parameters", {}),
            "indicator_parameters": eng.config.contexts.get("backtest", {}).get("parameters", {}),
        }

    overall_start = time.perf_counter()
    account_start = time.perf_counter()

    # Track dashboard primary ticker
    if ticker and source == "dashboard":
        eng.dashboard_primary_ticker = ticker.upper()

    # Identify primary ticker
    primary_ticker = (
        ticker.upper() if ticker else (
            eng.config.TRADELIST[0] if eng.config.TRADELIST else (
                eng.config.WATCHLIST[0] if eng.config.WATCHLIST else "MSFT"
            )
        )
    )

    # Fallback/default timeframe logic
    if timeframe is None:
        timeframe = eng.config.TICKER_SETTINGS.get(primary_ticker, {}).get('timeframe') or eng.config.DEFAULT_TIMEFRAME

    # Get broker data concurrently, but never let slow secondary calls mark Alpaca disconnected.
    import asyncio
    broker_cache_key = stable_cache_key(eng.uid, "dashboard_broker_snapshot", 1)
    broker_cache_ttl = 20 if mode == "fast" else 5
    cached_broker, _broker_cache_age = _dashboard_broker_cache.get_with_age(broker_cache_key, broker_cache_ttl)

    async def _broker_call(label, fn, fallback, timeout=3.0):
        try:
            return await asyncio.wait_for(asyncio.to_thread(fn), timeout=timeout)
        except asyncio.TimeoutError:
            print(f"[dashboard] Broker {label} timeout; using fallback")
            return fallback
        except Exception as e:
            print(f"[dashboard] Broker {label} unavailable: {_safe_exception_type(e)}")
            return fallback

    account_fallback = {
        'equity': 0,
        'cash': 0,
        'buying_power': 0,
        'simulation': True,
        'daily_pl': 0,
        'daily_pl_pct': 0,
        'broker_status_reason': 'Account check unavailable',
    }
    if mode == "fast" and cached_broker:
        account = cached_broker.get("account") or account_fallback
        if is_dashboard_request:
            positions, orders, profit_data, orders_history = [], [], (0.0, 0.0), []
        else:
            positions = cached_broker.get("positions") or []
            orders = cached_broker.get("orders") or []
            profit_data = cached_broker.get("profit_data") or (0.0, 0.0)
            orders_history = cached_broker.get("orders_history") or []
    else:
        cached_account = cached_broker.get("account") if cached_broker else None
        account_task = _broker_call("account", eng.broker.get_account_info, cached_account or account_fallback, timeout=3.0)
        if is_dashboard_request:
            account = await account_task
            positions, orders, profit_data, orders_history = [], [], (0.0, 0.0), []
        else:
            positions_task = _broker_call("positions", eng.broker.get_positions, (cached_broker or {}).get("positions", []), timeout=2.5)
            orders_task = _broker_call("open orders", eng.broker.get_open_orders, (cached_broker or {}).get("orders", []), timeout=2.5)
            profit_task = _broker_call("profit", eng.broker.get_all_time_profit, (cached_broker or {}).get("profit_data", (0.0, 0.0)), timeout=2.5)
            history_task = _broker_call("order history", eng.broker.get_order_history, (cached_broker or {}).get("orders_history", []), timeout=2.5)

            account, positions, orders, profit_data, orders_history = await asyncio.gather(
                account_task,
                positions_task,
                orders_task,
                profit_task,
                history_task,
            )
        if not account.get("broker_status_reason"):
            cache_payload = {"account": account}
            if not is_dashboard_request:
                cache_payload.update({
                    "positions": positions,
                    "orders": orders,
                    "profit_data": profit_data,
                    "orders_history": orders_history,
                })
            _dashboard_broker_cache.set(broker_cache_key, cache_payload)
    total_profit, total_profit_pct = profit_data

    account_ms = (time.perf_counter() - account_start) * 1000
    has_broker_keys = bool(eng.config.ALPACA_API_KEY)
    broker_connected = has_broker_keys and not account.get('simulation', True)
    broker_status = "connected" if broker_connected else ("retrying" if has_broker_keys else "not_configured")
    broker_status_reason = account.get("broker_status_reason") or account.get("error") or ""
    if not broker_status_reason and not broker_connected:
        broker_status_reason = getattr(eng, "alpaca_load_error", "") or ""

    # Active bot status check
    is_active_bot = primary_ticker in eng.config.TRADELIST

    # The chart endpoint owns dashboard technical analysis. Callers that only
    # need the dashboard shell can opt out so this endpoint never performs the
    # same indicator calculation a second time.
    primary_scan = None
    prefer_bot_for_primary = is_active_bot and ctx == "bots"
    if prefer_bot_for_primary and ctx == "bots":
        bot_tf = eng.config.TICKER_SETTINGS.get(primary_ticker, {}).get('timeframe')
        if bot_tf:
            timeframe = bot_tf

    if include_primary_analysis:
        cached_scan = eng._pick_scan(primary_ticker, timeframe, prefer_bot=prefer_bot_for_primary)
        if cached_scan:
            primary_scan = {**cached_scan, 'cached': True}

        if mode == "heavy" and not primary_scan and source == "dashboard":
            is_crypto = any(c in primary_ticker for c in ["BTC", "ETH", "LTC", "SOL", "DOGE"]) or "USD" in primary_ticker
            avail_cash = account.get('non_marginable_buying_power') or account['cash'] if is_crypto else account['cash']
            try:
                eval_ctx = "bots" if prefer_bot_for_primary else ctx
                with _analysis_request_context(eval_ctx, primary_ticker, prefer_bot_for_primary):
                    primary_scan = await asyncio.to_thread(
                        evaluate_trade,
                        primary_ticker,
                        account_equity=account['equity'],
                        available_cash=avail_cash,
                        timeframe=timeframe,
                        use_bot_settings=prefer_bot_for_primary
                    )
                if primary_scan:
                    if prefer_bot_for_primary:
                        eng._record_bot_scan(primary_scan)
                    else:
                        eng._record_scan(primary_scan)
            except Exception as e:
                print(f"[dashboard] Scan error for primary ticker {primary_ticker}: {_safe_exception_type(e)}")
                primary_scan = None

    if not primary_scan:
        primary_scan = {
            "time": get_now().isoformat(),
            "ticker": primary_ticker,
            "action": "HOLD",
            "price": "$0.00",
            "price_raw": 0.0,
            "reason": (
                "Indicator analysis is provided by the chart."
                if not include_primary_analysis
                else "Scanning in background..."
            ),
            "signals": {},
            "price_history": [],
            "timeframe": timeframe,
            "sentiment_score": 0.0,
            "sentiment_confidence": 0.0,
            "sentiment_summary": "",
            "sentiment_key_factor": "",
            "bullish_count": 0,
            "bearish_count": 0,
            "total_signals": 0,
            "atr": 0.0,
            "rsi": None,
            "position_sizing": {},
            "risk_status": {},
            "is_custom": False
        }

    if include_bot_payload:
        await _repair_bot_signal_thresholds_if_needed(eng)

    # When analysis is delegated to /api/chart/:ticker, skip _format_scan_for_ui()
    # entirely — the chart endpoint owns all signals/indicators for the dashboard.
    # The price stub will be built after _inject_price() is available below.
    if not include_primary_analysis:
        formatted_primary = None  # sentinel; replaced with price stub after _inject_price is defined
    else:
        # Full analysis included — format and apply the dashboard projection strip.
        formatted_primary = _format_scan_for_ui(primary_scan, use_global_settings=(not prefer_bot_for_primary))

    # Format daily P/L (Today Only)
    daily_pl = account.get('daily_pl', 0.0)
    daily_pl_pct = account.get('daily_pl_pct', 0.0)
    daily_pl_sign = "+" if daily_pl >= 0 else ""

    # Calculate All-Time Profit (Realized + Unrealized) directly from the broker history
    total_pl_sign = "+" if total_profit >= 0 else ""

    # Format trade log (scans/decisions)
    ui_trade_log = [_format_trade_for_ui(trade) for trade in eng.trade_log]
    order_history_ms = 0  # fetched concurrently above

    # Warmup or background scan triggers
    last_scan_time = eng.last_scan_time
    cloud_restore_log = []

    risk_mgr = get_risk_manager()

    sentiment_score = primary_scan.get('sentiment_score', 0)
    sentiment_confidence = primary_scan.get('sentiment_confidence', 0)
    sentiment_pct = primary_scan.get('sentiment_pct', primary_scan.get('news_sentiment_pct'))
    if sentiment_pct is None:
        try:
            sentiment_pct = round(((float(sentiment_score) + 1.0) / 2.0) * 100.0, 1)
        except (TypeError, ValueError):
            sentiment_pct = 50.0

    # Only populate sentiment fields when real AI analysis has actually run.
    # sentinel: sentiment_confidence > 0 or a non-empty summary means the AI card fired.
    _has_real_sentiment = bool(
        sentiment_confidence and float(sentiment_confidence) > 0
    ) or bool(primary_scan.get("sentiment_summary", ""))

    if _has_real_sentiment:
        if primary_scan.get("sentiment_direction") in {"BULLISH", "BEARISH", "NEUTRAL"}:
            sentiment_label = primary_scan.get("sentiment_direction", "NEUTRAL").title()
        elif sentiment_score > 0.3:
            sentiment_label = "Bullish"
        elif sentiment_score < -0.3:
            sentiment_label = "Bearish"
        else:
            sentiment_label = "Neutral"
    else:
        sentiment_label = None

    # Fetch live Webull prices, caching inactive watchlist tickers for 60 seconds to save API calls
    fast_tickers = [primary_ticker] if is_dashboard_request else list(dict.fromkeys(eng.config.TRADELIST + [primary_ticker]))
    slow_tickers = [t for t in eng.config.WATCHLIST if t not in fast_tickers]

    quote_start = time.perf_counter()
    stale_fast_prices = _get_stale_latest_prices(eng, fast_tickers, 300)
    stale_slow_prices = _get_stale_latest_prices(eng, slow_tickers, 900)
    fast_quote_max_age = 10 if mode == "fast" else 3
    quote_timeout = 2.0 if mode == "fast" else 3.0
    try:
        fast_prices, slow_prices = await asyncio.wait_for(
            asyncio.gather(
                asyncio.to_thread(_get_cached_latest_prices, eng, fast_tickers, fast_quote_max_age, 300),
                asyncio.to_thread(_get_cached_latest_prices, eng, slow_tickers, 60, 900)
            ),
            timeout=quote_timeout
        )
    except asyncio.TimeoutError:
        print(f"[dashboard] Quote API timeout after {quote_timeout:.2f}s; using cached prices")
        fast_prices, slow_prices = stale_fast_prices, stale_slow_prices
    except Exception as e:
        print(f"[dashboard] Quote API error: {_safe_exception_type(e)}")
        fast_prices, slow_prices = stale_fast_prices, stale_slow_prices
    latest_prices = {**slow_prices, **fast_prices}
    quote_ms = (time.perf_counter() - quote_start) * 1000

    def _inject_price(scan_ui, t):
        live_price = _lookup_quote_price(latest_prices, t)
        if live_price:
            scan_ui['price'] = _format_quote_price(t, live_price)
            scan_ui['price_raw'] = live_price
        if not scan_ui.get('ticker'):
            scan_ui['ticker'] = t
        return scan_ui

    def _price_only_scan(t):
        return _inject_price({
            "ticker": t,
            "price": "",
            "price_raw": 0.0,
        }, t)

    # Build or update formatted_primary now that _inject_price() is available.
    if not include_primary_analysis:
        formatted_primary = None
    else:
        formatted_primary = _inject_price(formatted_primary, primary_ticker)

    if include_primary_analysis:
        # ── Dashboard primaryScan Projection ──────────────────────────────────
        # Strip fields that are unused by app.js / ai-indicator-card.js in the
        # dashboard context.  Fields only consumed by bots-page drawers, chart
        # series data, or trade-log rows are excluded here to keep the payload
        # lean.  Any field read by the Botbulls4 News-AI indicator card is kept.
        _PRIMARY_SCAN_DASHBOARD_STRIP = frozenset({
            "model_action",          # duplicate of "action"
            "news_alignment",        # base flag; only _applied/_reason are read
            "rsi",                   # served via chart bar series, not primaryScan
            "atr",                   # only bots.js option-sizing panel
            "pl",                    # trade log rows only
            "pl_pct",                # trade log rows only
            "qty",                   # position/trade objects only
            "sell_target_pct",       # percentage variant; dollar value kept
            "leverage_guidance",     # not read in any JS file
            "position_size_pct",     # not read from primaryScan anywhere
            "vwap",                  # chart bar series only
            "forecast_entry_static", # botScans drawer only
            "forecast_exit_static",  # botScans drawer only
            "scan_log",              # bots-page drawer only
            "exit_monitoring",       # trade log rows only
            "exit_signal_label",     # trade log rows only
            "exit_signal_count",     # not read from primaryScan
            "exit_signal_threshold", # not read from primaryScan
            "exit_signal_direction", # not read from primaryScan
            "exit_watch_type",       # not read from primaryScan
            "exit_watch_details",    # not read from primaryScan
            "option_intent",         # not read from primaryScan in app.js
            "has_position",          # not read from primaryScan in app.js
            "prior_day_ohlc",        # only bots.js position calculator
        })
        formatted_primary = {k: v for k, v in formatted_primary.items() if k not in _PRIMARY_SCAN_DASHBOARD_STRIP}

    if positions and not is_dashboard_request:
        # Dashboard cards no longer render broker position projections. Keep this
        # heavier forecast work on Bots/Backtest paths where the UI still uses it.
        from option_selector import parse_occ_symbol
        import re
        for p in positions:
            try:
                symbol = p.get('symbol', '')
                asset_class = p.get('asset_class', '')
                qty = float(p.get('qty', 0))
                if qty == 0:
                    p['exit_forecast'] = ""
                    continue

                # Parse OCC options:
                parsed_occ = parse_occ_symbol(symbol)
                is_option = (parsed_occ is not None) or p.get('is_option', False) or (asset_class == 'option') or (len(symbol) >= 15)
                is_crypto_position = any(str(symbol).upper().replace("/", "").endswith(base) for base in ["USD", "USDT", "USDC"])

                if parsed_occ:
                    underlying = parsed_occ['underlying']
                else:
                    m = re.match(r"^([A-Z]+)", symbol)
                    underlying = m.group(1) if m else symbol

                active_ctx = eng.config.contexts.get(active_evaluation_context.get(), {})
                toggle_overrides = active_ctx.get("toggles", {})

                settings = eng.config.TICKER_SETTINGS.get(underlying, {})
                if is_option:
                    strat = settings.get('sell_mode') or settings.get('options_exit_strategy') or toggle_overrides.get('options_exit_strategy') or eng.config.active_toggles.get('options_exit_strategy', 'hybrid')
                else:
                    strat = settings.get('sell_mode') or toggle_overrides.get('exit_strategy') or eng.config.active_toggles.get('exit_strategy', 'hybrid')

                underlying_tf = settings.get('timeframe') or eng.config.DEFAULT_TIMEFRAME
                underlying_scan = eng._pick_scan(underlying, underlying_tf, prefer_bot=True, ignore_freshness=True)
                if not underlying_scan:
                    underlying_scan = eng.bot_scans.get(underlying) or eng.latest_scans.get(underlying, {})
                p['exit_protection'] = _format_exit_protection_status(
                    eng,
                    symbol,
                    is_option,
                    is_crypto_position,
                    settings=settings,
                    scan=underlying_scan,
                    position=p,
                )

                avg_price = float(p.get('avg_price') or p.get('avg_entry_price') or 0)
                current_price = float(p.get('current_price', 0))

                targets = []
                exit_strategy_prefix = _exit_strategy_label(strat)

                # --- STOCK / CRYPTO ---
                if not is_option:
                    is_long = qty > 0
                    atr = underlying_scan.get('atr', 0)

                    if strat in ('indicator', 'hybrid', 'hybrid_pct', 'hybrid_pivot', 'hybrid_high_low', 'hybrid_swing'):
                        buy_default = getattr(eng.config, 'MIN_BULLISH_SIGNALS', getattr(get_user_config(), 'MIN_BULLISH_SIGNALS', 1))
                        sell_default = getattr(eng.config, 'MIN_BEARISH_SIGNALS', getattr(get_user_config(), 'MIN_BEARISH_SIGNALS', 1))
                        sell_threshold = _get_threshold_setting(settings, ('min_sell_signals', 'sell_threshold'), sell_default)
                        buy_threshold = _get_threshold_setting(settings, ('min_buy_signals', 'threshold', 'buy_threshold'), buy_default)
                        desc_dynamic = _build_indicator_forecast(settings, underlying_scan, mode="exit", is_long=is_long, is_option=is_option, options_type=settings.get("options_type", "calls_only"), eng=eng)
                        if not desc_dynamic:
                            desc_dynamic = f"≥ {sell_threshold} BEARISH Signals" if is_long else f"≥ {buy_threshold} BULLISH Signals"
                        targets.append({"label": "Indicator", "val": float('-inf') if is_long else float('inf'), "type": "cond", "desc": desc_dynamic})

                    if strat in ('swing_points', 'hybrid_swing'):
                        swing_desc = _build_swing_forecast(settings, underlying_scan, mode="exit", is_long=(is_long if not is_option else is_call), is_option=is_option, options_type=settings.get("options_type", "calls_only"), eng=eng)
                        swing_exit = build_swing_exit(
                            underlying_scan.get('price_history', []),
                            current_price=underlying_scan.get('price_raw', 0.0),
                            is_long=(is_long if not is_option else is_call),
                            lookback_days=int(settings.get('swing_exit_lookback_days', settings.get('swing_lookback_days', 20)) or 20),
                            market_session=settings.get('market_session', 'Regular'),
                            is_crypto=any(str(underlying).upper().endswith(base) for base in ["USD", "USDT", "USDC"]) or str(underlying).upper() in ["BTC", "ETH", "XRP", "DOGE", "LTC", "ADA", "SOL"],
                        )
                        if swing_exit.get("take_profit") is not None and swing_exit.get("stop_loss") is not None:
                            targets.append({"label": "Swing TP", "val": swing_exit["take_profit"], "type": "price", "desc": swing_desc})
                            targets.append({"label": "Swing SL", "val": swing_exit["stop_loss"], "type": "price", "desc": swing_desc})

                    if strat == 'custom_sltp':
                        custom_sl = _entry_forecast_float(settings.get('custom_stop_loss_price'), 0.0)
                        custom_tp = _entry_forecast_float(settings.get('custom_take_profit_price'), 0.0)
                        if custom_sl > 0:
                            targets.append({"label": "Custom SL", "val": custom_sl, "type": "price"})
                        if custom_tp > 0:
                            targets.append({"label": "Custom TP", "val": custom_tp, "type": "price"})

                    if strat in ('sltp', 'hybrid') and atr > 0 and avg_price > 0:
                        sl_mult = settings.get('atr_stop_multiplier', getattr(eng.config, 'ATR_STOP_MULTIPLIER', 2.0))
                        tp_mult = settings.get('take_profit_multiplier', getattr(eng.config, 'ATR_TAKE_PROFIT_MULTIPLIER', 4.0))

                        sl_price = avg_price - (atr * sl_mult) if is_long else avg_price + (atr * sl_mult)
                        trailing = eng.last_trailing_stops.get(symbol)
                        if isinstance(trailing, dict):
                            trailing = trailing.get("trailing_stop_price")
                        if settings.get('enable_atr_trail', True) and isinstance(trailing, (int, float)):
                            sl_price = trailing

                        tp_price = avg_price + (atr * tp_mult) if is_long else avg_price - (atr * tp_mult)

                        targets.append({"label": "ATR SL", "val": sl_price, "type": "price"})
                        targets.append({"label": "ATR TP", "val": tp_price, "type": "price"})

                    if strat in ('percentage', 'hybrid_pct') and avg_price > 0:
                        sl_pct = settings.get('regular_stop_loss_pct', 0.05)
                        tp_pct = settings.get('regular_take_profit_pct', 0.10)
                        sl_price = avg_price * (1 - sl_pct) if is_long else avg_price * (1 + sl_pct)

                        trailing = eng.last_trailing_stops.get(symbol)
                        if isinstance(trailing, dict):
                            trailing = trailing.get("trailing_stop_price")
                        if settings.get('enable_pct_trail', True) and isinstance(trailing, (int, float)):
                            sl_price = trailing

                        tp_price = avg_price * (1 + tp_pct) if is_long else avg_price * (1 - tp_pct)

                        targets.append({"label": "Pct SL", "val": sl_price, "type": "price"})
                        targets.append({"label": "Pct TP", "val": tp_price, "type": "price"})

                    if strat in ('pivot_points', 'hybrid_pivot'):
                        last_day = underlying_scan.get('prior_day_ohlc')
                        if not last_day:
                            hist = underlying_scan.get('price_history', [])
                            last_day = hist[-1] if len(hist) > 0 else {}
                        if last_day:
                            h = last_day.get('high')
                            l = last_day.get('low')
                            c = last_day.get('close')
                            if h is not None and l is not None and c is not None:
                                pivot_levels = _dynamic_pivot_exit_levels(h, l, c, current_price)
                                if pivot_levels:
                                    targets.append({"label": "Pivot TP", "val": pivot_levels["tp"], "type": "price"})
                                    targets.append({"label": "Pivot SL", "val": pivot_levels["sl"], "type": "price"})

                    if strat in ('high_low', 'hybrid_high_low'):
                        last_day = underlying_scan.get('prior_day_ohlc')
                        if last_day:
                            dh = last_day.get('high')
                            dl = last_day.get('low')
                            if dh is not None and dl is not None:
                                hl_levels = _dynamic_high_low_exit_levels(dh, dl, current_price, resolve_high_low_buffer_pct(settings))
                                if hl_levels:
                                    targets.append({"label": "H/L TP", "val": hl_levels["tp"], "type": "price"})
                                    targets.append({"label": "H/L SL", "val": hl_levels["sl"], "type": "price"})

                # --- OPTIONS ---
                else:
                    op_type = parsed_occ.get('type', 'CALL') if parsed_occ else 'CALL'
                    is_call = op_type == 'CALL'

                    if strat in ('premium_pct', 'percentage', 'hybrid_pct') and avg_price > 0:
                        sl_pct, tp_pct, trail_pct = _option_pct_exit_settings(settings)
                        sl_price = avg_price * (1.0 - sl_pct)
                        trailing = eng.last_trailing_stops.get(symbol)
                        if settings.get('enable_pct_trail', True) and isinstance(trailing, (int, float)):
                            sl_price = trailing
                        tp_price = avg_price * (1.0 + tp_pct)
                        targets.append({"label": f"Option Pct SL ({sl_pct*100:.1f}% / Trail {trail_pct*100:.1f}%)", "val": sl_price, "type": "price"})
                        targets.append({"label": f"Option Pct TP ({tp_pct*100:.1f}%)", "val": tp_price, "type": "price"})

                    if strat in ('sltp', 'hybrid'):
                        atr = _entry_forecast_float(underlying_scan.get('atr'), 0.0)
                        entry_underlying = _entry_forecast_float(settings.get('options_underlying_entry_price'), underlying_scan.get('price_raw', 0.0))
                        sl_mult = _entry_forecast_float(settings.get('atr_stop_multiplier', getattr(eng.config, 'ATR_STOP_MULTIPLIER', 2.0)), 2.0)
                        tp_mult = _entry_forecast_float(settings.get('take_profit_multiplier', getattr(eng.config, 'ATR_TAKE_PROFIT_MULTIPLIER', 4.0)), 4.0)
                        if atr > 0 and entry_underlying > 0:
                            if is_call:
                                targets.append({"label": f"ATR TP ({tp_mult:.1f}x)", "val": entry_underlying + (atr * tp_mult), "type": "underlying_price"})
                                targets.append({"label": f"ATR SL ({sl_mult:.1f}x)", "val": entry_underlying - (atr * sl_mult), "type": "underlying_price"})
                            else:
                                targets.append({"label": f"ATR TP ({tp_mult:.1f}x)", "val": entry_underlying - (atr * tp_mult), "type": "underlying_price"})
                                targets.append({"label": f"ATR SL ({sl_mult:.1f}x)", "val": entry_underlying + (atr * sl_mult), "type": "underlying_price"})

                    if strat == 'custom_sltp':
                        custom_sl = _entry_forecast_float(settings.get('custom_stop_loss_price'), 0.0)
                        custom_tp = _entry_forecast_float(settings.get('custom_take_profit_price'), 0.0)
                        if custom_sl > 0:
                            targets.append({"label": "Custom SL", "val": custom_sl, "type": "price"})
                        if custom_tp > 0:
                            targets.append({"label": "Custom TP", "val": custom_tp, "type": "price"})

                    if strat == 'custom_underlying_sltp':
                        custom_u_sl = _entry_forecast_float(settings.get('custom_underlying_stop_loss_price'), 0.0)
                        custom_u_tp = _entry_forecast_float(settings.get('custom_underlying_take_profit_price'), 0.0)
                        if custom_u_sl > 0:
                            targets.append({"label": "Custom Underlying SL", "val": custom_u_sl, "type": "underlying_price"})
                        if custom_u_tp > 0:
                            targets.append({"label": "Custom Underlying TP", "val": custom_u_tp, "type": "underlying_price"})

                    if strat in ('pivot_points', 'hybrid_pivot'):
                        last_day = underlying_scan.get('prior_day_ohlc')
                        if not last_day:
                            hist = underlying_scan.get('price_history', [])
                            last_day = hist[-1] if len(hist) > 0 else {}
                        if last_day:
                            h = last_day.get('high')
                            l = last_day.get('low')
                            c = last_day.get('close')
                            if h is not None and l is not None and c is not None:
                                p_val = (h + l + c) / 3
                                r1 = (2 * p_val) - l
                                s1 = (2 * p_val) - h

                                target_price = r1 if is_call else s1
                                sl_target = s1 if is_call else r1
                                targets.append({"label": "Pivot TP", "val": target_price, "type": "underlying_price"})
                                targets.append({"label": "Pivot SL", "val": sl_target, "type": "underlying_price"})

                    if strat in ('high_low', 'hybrid_high_low'):
                        last_day = underlying_scan.get('prior_day_ohlc')
                        levels = buffered_prior_day_levels(last_day, resolve_high_low_buffer_pct(settings)) if last_day else None
                        if levels:
                            target_price = levels["high_trigger"] if is_call else levels["low_trigger"]
                            sl_target = levels["low_trigger"] if is_call else levels["high_trigger"]
                            targets.append({"label": "H/L TP", "val": target_price, "type": "underlying_price"})
                            targets.append({"label": "H/L SL", "val": sl_target, "type": "underlying_price"})

                    if strat in ('indicator', 'hybrid', 'hybrid_pct', 'hybrid_pivot', 'hybrid_high_low', 'hybrid_swing'):
                        buy_default = getattr(eng.config, 'MIN_BULLISH_SIGNALS', getattr(get_user_config(), 'MIN_BULLISH_SIGNALS', 1))
                        sell_default = getattr(eng.config, 'MIN_BEARISH_SIGNALS', getattr(get_user_config(), 'MIN_BEARISH_SIGNALS', 1))
                        sell_threshold = _get_threshold_setting(settings, ('min_sell_signals', 'sell_threshold'), sell_default)
                        buy_threshold = _get_threshold_setting(settings, ('min_buy_signals', 'threshold', 'buy_threshold'), buy_default)
                        desc = f"≥ {sell_threshold} BEARISH Signals" if is_call else f"≥ {buy_threshold} BULLISH Signals"
                        targets.append({"label": "Indicator", "val": float('-inf') if is_call else float('inf'), "type": "cond", "desc": desc})

                    if strat in ('swing_points', 'hybrid_swing'):
                        swing_desc = _build_swing_forecast(settings, underlying_scan, mode="exit", is_long=is_call, is_option=is_option, options_type=settings.get("options_type", "calls_only"), eng=eng)
                        swing_exit = build_swing_exit(
                            underlying_scan.get('price_history', []),
                            current_price=underlying_scan.get('price_raw', 0.0),
                            is_long=is_call,
                            lookback_days=int(settings.get('swing_exit_lookback_days', settings.get('swing_lookback_days', 20)) or 20),
                            market_session=settings.get('market_session', 'Regular'),
                            is_crypto=any(str(underlying).upper().endswith(base) for base in ["USD", "USDT", "USDC"]) or str(underlying).upper() in ["BTC", "ETH", "XRP", "DOGE", "LTC", "ADA", "SOL"],
                        )
                        if swing_exit.get("take_profit") is not None and swing_exit.get("stop_loss") is not None:
                            targets.append({"label": "Swing TP", "val": swing_exit["take_profit"], "type": "underlying_price", "desc": swing_desc})
                            targets.append({"label": "Swing SL", "val": swing_exit["stop_loss"], "type": "underlying_price", "desc": swing_desc})

                if not targets:
                    p['exit_forecast'] = f"{exit_strategy_prefix} | No targets found"
                    continue

                min_pct_dist = float('inf')
                closest_target = None

                for t in targets:
                    t['dist_pct'] = float('inf')
                    if t['type'] == 'price' and current_price > 0:
                        pct = abs(current_price - t['val']) / current_price
                        t['dist_pct'] = pct
                        if pct < min_pct_dist:
                            min_pct_dist = pct
                            closest_target = t
                    elif t['type'] == 'underlying_price':
                        live_u = _lookup_quote_price(latest_prices, underlying) or underlying_scan.get('price_raw', 0)
                        if live_u > 0:
                            pct = abs(live_u - t['val']) / live_u
                            t['dist_pct'] = pct
                            if pct < min_pct_dist:
                                min_pct_dist = pct
                                closest_target = t

                # Sort targets: closest first, conditions/tech at the end
                targets.sort(key=lambda x: x.get('dist_pct', float('inf')))

                def fmt_val(v):
                    if not ("USD" in str(symbol).upper() or "BTC" in str(symbol).upper()):
                        return f"{v:.2f}"
                    s = f"{v:.8f}".rstrip('0')
                    if s.endswith('.'):
                        s += '00'
                    elif len(s.split('.')[1]) == 1:
                        s += '0'
                    return s

                final_strs = [exit_strategy_prefix]
                for t in targets:
                    is_closest = (t == closest_target and strat == 'hybrid' and closest_target is not None)
                    prefix = "★ " if is_closest else ""
                    suffix = " ★" if is_closest else ""

                    if t['type'] == 'cond':
                        final_strs.append(f"{prefix}[{t['label']}] {t['desc']}{suffix}")
                    elif t['type'] == 'price':
                        final_strs.append(f"{prefix}[{t['label']}] ${fmt_val(t['val'])}{suffix}")
                    elif t['type'] == 'underlying_price':
                        final_strs.append(f"{prefix}[U-{t['label']}] ${fmt_val(t['val'])}{suffix}")

                p['exit_forecast'] = " | ".join(final_strs)

                tp_target = next((t for t in targets if 'TP' in str(t.get('label', '')).upper() and t.get('type') in ('price', 'underlying_price')), None)
                sl_target = next((t for t in targets if 'SL' in str(t.get('label', '')).upper() and t.get('type') in ('price', 'underlying_price')), None)
                if tp_target and sl_target:
                    tp_is_underlying = tp_target.get('type') == 'underlying_price'
                    sl_is_underlying = sl_target.get('type') == 'underlying_price'
                    tp_label = "U-TP" if tp_is_underlying else "TP"
                    sl_label = "U-SL" if sl_is_underlying else "SL"
                    protection = {
                        "label": "Exit Protection",
                        "status": f"Active | Watcher | {tp_label} ${fmt_val(tp_target['val'])} | {sl_label} ${fmt_val(sl_target['val'])}",
                        "active": True,
                        "protection_type": "watcher",
                    }
                    if not tp_is_underlying:
                        protection["take_profit"] = float(tp_target['val'])
                    else:
                        protection["underlying_take_profit"] = float(tp_target['val'])
                    if not sl_is_underlying:
                        protection["stop_loss"] = float(sl_target['val'])
                    else:
                        protection["underlying_stop_loss"] = float(sl_target['val'])
                    p['exit_protection'] = protection

            except Exception as e:
                import traceback
                print(f"[dashboard] Error forecasting exit for {p.get('symbol')}: {_safe_exception_type(e)}")
                traceback.print_exc()
                p['exit_forecast'] = "Forecast Error"

    # Bot cards are owned by /api/bots/snapshot. Only retain the legacy bot
    # projection when an older Bots client explicitly calls this endpoint.
    # Dashboard and Backtest requests should not pay to build or transfer it.
    bot_scans_dict = {}
    settings_dirty = False
    if include_bot_payload:
        for t in eng.config.TRADELIST:
            bot_tf = eng.config.TICKER_SETTINGS.get(t, {}).get('timeframe', eng.config.DEFAULT_TIMEFRAME)
            scan = eng._pick_scan(t, bot_tf, prefer_bot=True) or eng._pick_scan(t, bot_tf, prefer_bot=True, ignore_freshness=True) or {}
            ui_scan = _inject_price(_format_scan_for_ui(scan, use_global_settings=False), t)
            t_settings = eng.config.TICKER_SETTINGS.get(t, {})
            is_opt = t_settings.get('options_trading', t_settings.get('is_option', False))
            active_position = _matching_position_for_ticker(positions, t, t_settings)
            continuous_grid_forecast = _build_continuous_grid_forecast(t_settings, ui_scan, active_position, t)
            if continuous_grid_forecast.get("enabled"):
                entry_static = continuous_grid_forecast.get("entry", {}).get("text", "")
                exit_static = continuous_grid_forecast.get("exit", {}).get("text", "")
            else:
                if active_position is None and t_settings.get("forecast_exit_snapshots"):
                    _clear_forecast_exit_snapshots(t_settings)
                    settings_dirty = True
                entry_static, entry_dirty = _ensure_entry_forecast_snapshot(t_settings, scan or ui_scan, t)
                exit_static, exit_dirty = _ensure_exit_forecast_snapshot(t_settings, scan or ui_scan, active_position, t)
                settings_dirty = settings_dirty or entry_dirty or exit_dirty

            ui_scan["forecast_entry_static"] = str(entry_static or "").replace("[Entry Strategy] ", "").replace("[Exit Strategy] ", "")
            ui_scan["forecast_exit_static"] = str(exit_static or "").replace("[Entry Strategy] ", "").replace("[Exit Strategy] ", "")
            ui_scan["continuous_grid_forecast"] = continuous_grid_forecast
            ui_scan['entry_forecast'] = _build_entry_forecast(
                t_settings,
                scan,
                is_option=is_opt,
                options_type=t_settings.get("options_type", "calls_only"),
                eng=eng,
                for_card=True,
            )
            ui_scan["scan_log"] = _build_scan_log(
                ticker=t,
                formatted_scan=ui_scan,
                settings=t_settings,
                position=active_position,
                pending_orders=orders,
                account=account,
                previous_scan_log=eng.scan_log_state.get(t),
                eng=eng,
                positions=positions,
                exit_protection=(active_position or {}).get("exit_protection") if active_position else None,
            )
            eng.scan_log_state[t] = ui_scan["scan_log"]
            if active_position is not None:
                active_position["exit_forecast_static"] = exit_static
            bot_scans_dict[t] = ui_scan

    if settings_dirty:
        _queue_settings_save(eng, "dashboard scan snapshots")

    payload = {
        "simulation": account.get('simulation', True),
        "has_keys": has_broker_keys,
        "watchlistScans": {
            t: _price_only_scan(t)
            for t in eng.config.WATCHLIST
        },
        # Strategy Signals (primary ticker)
        "primaryTicker": primary_ticker,
        "primaryAnalysisIncluded": bool(include_primary_analysis),
        "primaryAnalysisSource": "dashboard" if include_primary_analysis else "chart",
        "primaryScan": formatted_primary,

        # Bot Meta
        "botRunning": eng.bot_running,
        "brokerConnected": broker_connected,
        "brokerStatus": broker_status,
        "brokerStatusReason": broker_status_reason,
        "has_keys": has_broker_keys,
        "lastScan": last_scan_time or "Starting...",
        "indicator_parameters": eng.config.active_parameters,
        "indicator_overrides": eng.config.contexts.get(ctx, {}).get("parameters", {}),
        "indicator_toggle_overrides": eng.config.contexts.get(ctx, {}).get("toggles", {}),
        "strategyTimeframe": timeframe,
        "watchlist": eng.config.WATCHLIST,

        # Performance Timings
        "performance": {
            "total_ms": (time.perf_counter() - overall_start) * 1000,
            "account_ms": account_ms,
            "quote_ms": quote_ms,
            "order_history_ms": order_history_ms,
            "eval_ms": (primary_scan or {}).get('perf_ms', 0),
            "cached": (primary_scan or {}).get('cached', False),
        },
    }

    if _has_real_sentiment:
        payload.update({
            "aiSentiment": f"{sentiment_label} ({float(sentiment_pct):.0f}%)" if sentiment_label else None,
            "sentiment_pct": sentiment_pct,
            "sentiment_confidence": sentiment_confidence,
            "sentiment_summary": primary_scan.get("sentiment_summary", ""),
            "sentiment_key_factor": primary_scan.get("sentiment_key_factor", "N/A"),
        })

    if cloud_restore_log:
        payload["debug_logs"] = cloud_restore_log

    if not is_dashboard_request:
        payload.update({
            "capital": f"${account['equity']:.2f}",
            "cash": f"${account['cash']:.2f}",
            "openPositions": str(len(positions)),
            "positionsList": ", ".join(p['symbol'] for p in positions) if positions else "No positions",
            "dailyPL": f"{daily_pl_sign}${daily_pl:.2f} ({daily_pl_sign}{daily_pl_pct:.2f}%)",
            "totalProfit": f"{total_pl_sign}${total_profit:.2f} ({total_pl_sign}{total_profit_pct:.2f}%)",
            "account": {k: v for k, v in account.items() if not str(k).startswith("_")},
            "positions": positions,
            "pendingOrders": orders,
            "orderHistory": orders_history,
            "recentTrades": ui_trade_log,
            "tradelist": eng.config.TRADELIST,
            "scanInterval": eng.config.SCAN_INTERVAL_SECONDS,
            "optionsScanInterval": getattr(eng.config, 'OPTIONS_SCAN_INTERVAL_SECONDS', 10),
            "maxOpenPositions": eng.config.get('MAX_OPEN_POSITIONS', 5),
            "tradeCooldownSeconds": eng.config.get('TRADE_COOLDOWN_SECONDS', 300),
            "marketHoursOnly": eng.config.get('MARKET_HOURS_ONLY', True),
        })

    if include_bot_payload:
        payload.update({
            "botScans": bot_scans_dict,
            "ticker_settings": eng.config.TICKER_SETTINGS,
            # Compatibility aliases for legacy Bots callers only. Dashboard
            # responses expose one canonical key for each collection.
            "open_orders": orders,
            "orders_history": orders_history,
            "trade_log": ui_trade_log,
        })

    # Dashboard heartbeats never carry chart candles. When primary analysis is
    # omitted, /api/chart owns both the candles and their indicator signals.
    if isinstance(payload.get("primaryScan"), dict) and "price_history" in payload["primaryScan"]:
        del payload["primaryScan"]["price_history"]
    scan_maps = [payload["watchlistScans"]]
    if include_bot_payload:
        scan_maps.append(payload["botScans"])
    for scan_map in scan_maps:
        for v in scan_map.values():
            if isinstance(v, dict) and "price_history" in v:
                del v["price_history"]

    if mode == "fast":
        if "signals" in payload:
            payload["signals"] = {}
        if isinstance(payload.get("primaryScan"), dict):
            payload["primaryScan"]["signals"] = {}
        for k, v in payload["watchlistScans"].items():
            if isinstance(v, dict):
                v["signals"] = {}
        if include_bot_payload:
            for k, v in payload["botScans"].items():
                if isinstance(v, dict):
                    v["signals"] = {}

    return payload


def _inject_price_into_scan(scan_ui: dict, t: str, latest_prices: dict = None) -> dict:
    if not isinstance(scan_ui, dict):
        scan_ui = {}
    if latest_prices:
        live_price = _lookup_quote_price(latest_prices, t) if '_lookup_quote_price' in globals() else latest_prices.get(t)
        if live_price:
            scan_ui['price'] = _format_quote_price(t, live_price) if '_format_quote_price' in globals() else str(live_price)
            scan_ui['price_raw'] = live_price
    if not scan_ui.get('ticker'):
        scan_ui['ticker'] = t
    return scan_ui


@app.get("/api/bots/snapshot")
async def get_bots_snapshot(
    request: Request,
    response: Response,
    partition: str = "all",
    eng: UserEngine = Depends(get_user_engine),
):
    """
    Dedicated lightweight snapshot endpoint for the Bots page.
    Returns partitioned botScans, positions, open_orders, account, and a slim ticker_settings projection.
    Does NOT compute orders_history, trade_log, watchlistScans, or raw chart candles.
    """
    import asyncio
    import hashlib
    import time
    t0 = time.time()
    from user_config import active_evaluation_context
    active_evaluation_context.set("bots")

    requested_partition = str(partition or "all").lower()
    p_norm = requested_partition if requested_partition in {"all", "options", "regular"} else "all"

    account_fallback = {
        "equity": 0.0,
        "cash": 0.0,
        "buying_power": 0.0,
        "daily_pl": 0.0,
        "daily_pl_pct": 0.0,
        "total_pl": 0.0,
        "simulation": True,
        "broker_status_reason": "Account check unavailable",
    }
    broker_cache_key = stable_cache_key(eng.uid, "bots_broker_snapshot", 1)
    cached_broker, _ = _bots_broker_cache.get_with_age(broker_cache_key, 8)

    async def _broker_call(label, fn, fallback, timeout=2.5):
        if not callable(fn):
            return fallback
        try:
            return await asyncio.wait_for(asyncio.to_thread(fn), timeout=timeout)
        except asyncio.TimeoutError:
            print(f"[bots/snapshot] Broker {label} timeout; using cached fallback")
        except Exception as exc:
            print(f"[bots/snapshot] Broker {label} unavailable: {_safe_exception_type(exc)}")
        return fallback

    if not eng.broker:
        raw_account, positions, open_orders, profit_data = account_fallback, [], [], (0.0, 0.0)
    elif cached_broker:
        raw_account = cached_broker.get("account") or account_fallback
        positions = cached_broker.get("positions") or []
        open_orders = cached_broker.get("orders") or []
        profit_data = cached_broker.get("profit_data") or (0.0, 0.0)
    else:
        account_task = _broker_call("account", getattr(eng.broker, "get_account_info", None), account_fallback, 3.0)
        positions_task = _broker_call("positions", getattr(eng.broker, "get_positions", None), [], 2.5)
        orders_task = _broker_call("open orders", getattr(eng.broker, "get_open_orders", None), [], 2.5)
        profit_task = _broker_call("profit", getattr(eng.broker, "get_all_time_profit", None), (0.0, 0.0), 2.5)
        raw_account, positions, open_orders, profit_data = await asyncio.gather(
            account_task, positions_task, orders_task, profit_task
        )
        raw_account = raw_account or account_fallback
        positions = positions or []
        open_orders = open_orders or []
        profit_data = profit_data or (0.0, 0.0)
        if not raw_account.get("broker_status_reason"):
            _bots_broker_cache.set(broker_cache_key, {
                "account": raw_account,
                "positions": positions,
                "orders": open_orders,
                "profit_data": profit_data,
            })

    try:
        total_profit, total_profit_pct = profit_data
    except (TypeError, ValueError):
        total_profit, total_profit_pct = raw_account.get("total_pl", 0.0), 0.0

    account = {
        "equity": raw_account.get("equity", 0.0),
        "cash": raw_account.get("cash", 0.0),
        "buying_power": raw_account.get("buying_power", 0.0),
        "dailyPL": raw_account.get("daily_pl", raw_account.get("dailyPL", 0.0)),
        "dailyPLPct": raw_account.get("daily_pl_pct", raw_account.get("dailyPLPct", 0.0)),
        "totalProfit": total_profit,
        "simulation": bool(raw_account.get("simulation", True)),
        "broker_status_reason": raw_account.get("broker_status_reason") or raw_account.get("error") or "",
    }

    tradelist = list(eng.config.TRADELIST)
    bot_scans_dict = {}
    slim_settings_dict = {}
    settings_dirty = False

    try:
        latest_prices = await asyncio.wait_for(
            asyncio.to_thread(_get_cached_latest_prices, eng, tradelist, 60, 300),
            timeout=1.5
        )
    except Exception as e:
        print(f"[bots/snapshot] Error fetching latest prices: {type(e).__name__}")
        latest_prices = _get_stale_latest_prices(eng, tradelist, 600)

    for t in tradelist:
        t_settings = eng.config.TICKER_SETTINGS.get(t, {})
        is_opt = bool(t_settings.get('options_trading', t_settings.get('is_option', False)))

        if p_norm == "options" and not is_opt:
            continue
        if p_norm == "regular" and is_opt:
            continue

        card_setting_keys = (
            "ai_autopilot", "allocation_mode", "amount", "buy_mode", "compound_amount",
            "entry_trades", "is_option", "last_trade_at", "last_trade_date", "market_session",
            "max_trades", "min_buy_signals", "min_sell_signals", "options_allocation_mode",
            "options_compound_amount", "options_exit_strategy", "options_expiry_days",
            "options_trade_amount", "options_type", "sell_mode", "sell_threshold", "started_at",
            "threshold", "today_trades", "total_trades", "timeframe", "paused", "pause_reason",
            "paused_at", "pause_code", "pause_event_id", "pause_requires_review", "consecutive_losses",
            "is_incubator", "source", "strategy_mode", "bot_type", "asset_class", "is_crypto",
            "grid_continuous_mode", "exit_grid_continuous_mode",
            "atr_grid_continuous_mode", "exit_atr_grid_continuous_mode",
        )
        stored_asset_class = str(t_settings.get("asset_class", "") or "").strip().lower()
        if is_opt:
            canonical_asset_class = "options"
        elif stored_asset_class in {"crypto", "cryptocurrency"}:
            canonical_asset_class = "crypto"
        elif stored_asset_class in {"stock", "equity", "us_equity"}:
            canonical_asset_class = "stock"
        else:
            canonical_asset_class = "crypto" if _is_crypto_symbol(t) else "stock"
        is_crypto_bot = canonical_asset_class == "crypto"
        slim_settings_dict[t] = {key: t_settings.get(key) for key in card_setting_keys if key in t_settings}
        session_schedule = strategy_scan_log.schedule_summary(t_settings)
        if session_schedule:
            slim_settings_dict[t]["session_schedule"] = session_schedule   # display only; not a stored setting
        slim_settings_dict[t].update({
            "timeframe": t_settings.get("timeframe", eng.config.DEFAULT_TIMEFRAME),
            "paused": bool(t_settings.get("paused", False)),
            "pause_reason": t_settings.get("pause_reason", ""),
            "options_trading": is_opt,
            "is_option": bool(t_settings.get("is_option", is_opt)),
            "is_incubator": bool(t_settings.get("is_incubator", False)),
            "source": t_settings.get("source", "user"),
            "market_session": t_settings.get("market_session", "Regular"),
            "bot_type": t_settings.get("bot_type", "options" if is_opt else "standard"),
            "asset_class": canonical_asset_class,
            "is_crypto": is_crypto_bot,
        })

        bot_tf = t_settings.get('timeframe', eng.config.DEFAULT_TIMEFRAME)
        scan = eng._pick_scan(t, bot_tf, prefer_bot=True) or eng._pick_scan(t, bot_tf, prefer_bot=True, ignore_freshness=True) or {}
        ui_scan = _inject_price_into_scan(_format_scan_for_ui(scan, use_global_settings=False), t, latest_prices=latest_prices)

        active_position = _matching_position_for_ticker(positions, t, t_settings)
        continuous_grid_forecast = _build_continuous_grid_forecast(t_settings, ui_scan, active_position, t)
        if continuous_grid_forecast.get("enabled"):
            entry_static = continuous_grid_forecast.get("entry", {}).get("text", "")
            exit_static = continuous_grid_forecast.get("exit", {}).get("text", "")
        else:
            entry_static, entry_dirty = _ensure_entry_forecast_snapshot(t_settings, scan or ui_scan, t)
            exit_static, exit_dirty = _ensure_exit_forecast_snapshot(t_settings, scan or ui_scan, active_position, t)
            settings_dirty = settings_dirty or entry_dirty or exit_dirty
        if isinstance(ui_scan, dict):
            ui_scan["forecast_entry_static"] = str(entry_static or "").replace("[Entry Strategy] ", "").replace("[Exit Strategy] ", "")
            ui_scan["forecast_exit_static"] = str(exit_static or "").replace("[Entry Strategy] ", "").replace("[Exit Strategy] ", "")
            ui_scan["continuous_grid_forecast"] = continuous_grid_forecast
            ui_scan["scan_log"] = _build_scan_log(
                ticker=t,
                formatted_scan=ui_scan,
                settings=t_settings,
                position=active_position,
                pending_orders=open_orders,
                account=account,
                previous_scan_log=eng.scan_log_state.get(t),
                eng=eng,
                positions=positions,
                exit_protection=(active_position or {}).get("exit_protection") if active_position else None,
            )
            eng.scan_log_state[t] = ui_scan["scan_log"]
            # Keep card fields and the compact scan log; chart candles and raw signal maps load elsewhere.
            ui_scan_slim = {
                k: v for k, v in ui_scan.items()
                if k not in ("price_history", "raw_candles", "candles", "df", "signals")
            }
            bot_scans_dict[t] = ui_scan_slim
        else:
            bot_scans_dict[t] = ui_scan

    if settings_dirty:
        _queue_settings_save(eng, "bots snapshot forecasts")

    filtered_positions = positions
    filtered_orders = open_orders

    def _is_option_item(item):
        asset_class = str(item.get("asset_class", "")).lower()
        symbol = re.sub(r"\s+", "", str(item.get("symbol", "")).upper())
        return asset_class in {"option", "us_option"} or bool(re.fullmatch(r"[A-Z]{1,6}\d{6}[CP]\d{8}", symbol))

    if p_norm == "options":
        filtered_positions = [item for item in positions if _is_option_item(item)]
        filtered_orders = [item for item in open_orders if _is_option_item(item)]
    elif p_norm == "regular":
        filtered_positions = [item for item in positions if not _is_option_item(item)]
        filtered_orders = [item for item in open_orders if not _is_option_item(item)]

    pos_sig = "|".join(sorted(f"{p.get('symbol', '')}:{p.get('qty', '')}" for p in positions))
    ord_sig = "|".join(sorted(f"{o.get('id', '')}:{o.get('status', '')}:{o.get('filled_qty', '')}" for o in open_orders))
    latest_trade = (getattr(eng, "trade_log", []) or [None])[0] or {}
    trade_sig = f"{latest_trade.get('time', '')}:{latest_trade.get('ticker', '')}:{latest_trade.get('action', '')}:{latest_trade.get('qty', '')}"
    state_sig = f"{pos_sig}|{ord_sig}|{trade_sig}"
    trade_event_seq = int(hashlib.md5(state_sig.encode()).hexdigest()[:8], 16)

    has_keys = bool(getattr(eng.config, "ALPACA_API_KEY", ""))
    broker_simulation = bool(getattr(eng.broker, "simulation_mode", True)) if eng.broker else True
    broker_connected = has_keys and not account.get('simulation', broker_simulation)
    broker_status = "connected" if broker_connected else ("retrying" if has_keys else "not_configured")

    exec_ms = round((time.time() - t0) * 1000, 1)
    response.headers["X-Snapshot-Time-MS"] = str(exec_ms)

    equity_val = float(account.get("equity", 0.0) or 0.0)
    cash_val = float(account.get("cash", 0.0) or 0.0)
    daily_pl_val = float(account.get("dailyPL", 0.0) or 0.0)
    daily_pl_pct = float(account.get("dailyPLPct", 0.0) or 0.0)

    total_profit = float(total_profit or account.get("totalProfit", 0.0) or 0.0)
    total_profit_pct = float(total_profit_pct or 0.0)
    from trader import get_risk_manager
    global_risk_status = get_risk_manager().get_global_status(
        equity_val if equity_val > 0 else None,
        account_key=raw_account.get("_risk_account_key"),
        daily_reference_equity=raw_account.get("daily_starting_equity"),
    )

    if daily_pl_val > 0:
        daily_pl_str = f"+${abs(daily_pl_val):.2f} (+{abs(daily_pl_pct):.2f}%)"
    elif daily_pl_val < 0:
        daily_pl_str = f"-${abs(daily_pl_val):.2f} (-{abs(daily_pl_pct):.2f}%)"
    else:
        daily_pl_str = "$0.00 (0.00%)"

    if total_profit > 0:
        total_profit_str = f"+${abs(total_profit):.2f} (+{abs(total_profit_pct):.2f}%)"
    elif total_profit < 0:
        total_profit_str = f"-${abs(total_profit):.2f} (-{abs(total_profit_pct):.2f}%)"
    else:
        total_profit_str = "$0.00 (0.00%)"

    return {
        "schema_version": "1.0",
        "partition": p_norm,
        "tradelist": list(bot_scans_dict.keys()),
        "botScans": bot_scans_dict,
        "positions": filtered_positions,
        "open_orders": filtered_orders,
        "pendingOrders": filtered_orders,
        "account": account,
        "capital": f"${equity_val:.2f}" if equity_val > 0 else "---",
        "cash": f"${cash_val:.2f}" if cash_val > 0 else "---",
        "openPositions": str(len(positions)),
        "positionsList": ", ".join(p.get("symbol", "") for p in positions) if positions else "No positions",
        "dailyPL": daily_pl_str,
        "totalProfit": total_profit_str,
        "ticker_settings": slim_settings_dict,
        "indicator_parameters": eng.config.active_parameters,
        "global_risk_status": global_risk_status,
        "strategyTimeframe": getattr(eng.config, "DEFAULT_TIMEFRAME", "5Min"),
        "watchlist": getattr(eng.config, "WATCHLIST", []),
        "botRunning": getattr(eng, "bot_running", True),
        "brokerConnected": broker_connected,
        "brokerStatus": broker_status,
        "brokerStatusReason": account.get("broker_status_reason", ""),
        "scanInterval": eng.config.SCAN_INTERVAL_SECONDS,
        "optionsScanInterval": getattr(eng.config, "OPTIONS_SCAN_INTERVAL_SECONDS", 10),
        "simulation": (not has_keys) or bool(account.get("simulation", broker_simulation)),
        "has_keys": has_keys,
        "snapshot_seq": int(time.time() * 1000),
        "trade_event_seq": trade_event_seq,
        "generated_at": datetime.now(pytz.UTC).isoformat(),
    }


@app.get("/api/strategy-presets")
async def get_strategy_presets(eng: UserEngine = Depends(get_user_engine)):
    """Catalog of strategy presets: the single source of truth for every page's picker.

    Read-only; it never creates orders, settings or notifications.
    """
    return {
        "version": strategy_presets.CATALOG_VERSION,
        "allowed_override_keys": list(strategy_presets.ALLOWED_OVERRIDE_KEYS),
        "presets": strategy_presets.strategy_preset_options(),
    }


@app.get("/api/bots/{ticker}/settings")
async def get_bot_settings(
    ticker: str,
    eng: UserEngine = Depends(get_user_engine),
):
    """
    On-demand endpoint to fetch complete TICKER_SETTINGS dictionary for a single bot.
    Used when opening the Bot Settings modal.
    """
    t = ticker.upper().strip()
    if not re.fullmatch(r"[A-Z0-9.\-]{1,15}", t):
        raise HTTPException(status_code=400, detail="Invalid ticker format")
    if t not in eng.config.TICKER_SETTINGS:
        raise HTTPException(status_code=404, detail=f"Bot settings not found for ticker '{t}'")

    full_settings = eng.config.TICKER_SETTINGS.get(t, {})
    return {
        "ticker": t,
        "settings": full_settings,
        "fetched_at": datetime.now(pytz.UTC).isoformat(),
    }


def _collapse_repeated_strategy_rows(rows: list) -> list:
    """Newest-first log rows: merge runs of identical "watching" checks of the same strategy bot into one row.

    Only strategy rows that did nothing (event WATCHING, same ticker, same reason, same numbers) are merged; the
    newest row is kept with ``repeat_count``. Entries, exits, blocked or data-waiting rows are never merged."""
    collapsed: list = []
    for row in rows:
        strategy = row.get("strategy") if isinstance(row, dict) else None
        previous = collapsed[-1] if collapsed else None
        previous_strategy = previous.get("strategy") if previous else None
        if (
            strategy and previous_strategy
            and strategy.get("event") == "WATCHING" == previous_strategy.get("event")
            and row.get("ticker") == previous.get("ticker")
            and row.get("reason") == previous.get("reason")
            and strategy.get("chip") == previous_strategy.get("chip")
        ):
            previous["repeat_count"] = int(previous.get("repeat_count") or 1) + 1
            continue
        collapsed.append(row)
    return collapsed


@app.get("/api/bots/activity")
async def get_bots_activity(
    response: Response,
    kind: str = "scans",
    limit: int = 50,
    eng: UserEngine = Depends(get_user_engine),
):
    """
    On-demand activity endpoint for execution logs tab on the Bots page.
    Returns recent scans or executed/cancelled order history.
    """
    limit_val = max(1, min(limit, 100))
    kind_norm = kind.lower() if kind in ["scans", "orders"] else "scans"
    response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate"
    response.headers["Pragma"] = "no-cache"

    import asyncio

    if kind_norm == "orders":
        orders_history = []
        try:
            if eng.broker and hasattr(eng.broker, "get_order_history"):
                orders_history = await asyncio.wait_for(
                    asyncio.to_thread(eng.broker.get_order_history, getattr(eng, "trade_log", []), limit_val),
                    timeout=3.0,
                ) or []
            elif hasattr(eng, "order_history"):
                orders_history = eng.order_history[:limit_val]
        except asyncio.TimeoutError:
            print("[bots/activity] Broker order history timeout")
        except Exception as e:
            print(f"[bots/activity] Error fetching order history: {_safe_exception_type(e)}")
        return {
            "schema_version": "1.0",
            "kind": "orders",
            "activity": orders_history[:limit_val],
            "count": min(len(orders_history), limit_val),
            "generated_at": datetime.now(pytz.UTC).isoformat(),
        }

    # Read extra rows so that collapsing repeated strategy checks still fills the page.
    raw_trade_log = (getattr(eng, "trade_log", []) or [])[:limit_val * 3]
    scans_activity = _collapse_repeated_strategy_rows([_format_trade_for_ui(item) for item in raw_trade_log])[:limit_val]
    return {
        "schema_version": "1.0",
        "kind": "scans",
        "activity": scans_activity,
        "count": len(scans_activity),
        "generated_at": datetime.now(pytz.UTC).isoformat(),
    }


@app.get("/api/chart/{ticker}")
async def get_chart_data(
    ticker: str,
    timeframe: str = None,
    force: bool = False,
    source: str = "dashboard",
    session: str = "regular",
    eng: UserEngine = Depends(get_user_engine)
):
    """Fast chart endpoint: OHLCV + overlay indicators + technical confluence only."""
    ticker = _normalize_quote_symbol(ticker)
    if not re.fullmatch(r"[A-Z0-9.\-]{1,15}", ticker):
        return {"error": "Invalid ticker format"}

    timeframe = timeframe or eng.config.TICKER_SETTINGS.get(ticker, {}).get('timeframe') or eng.config.DEFAULT_TIMEFRAME
    prefer_bot = ticker in eng.config.TRADELIST and source == "bots"
    analysis_source = "bots" if prefer_bot else source
    started = time.perf_counter()
    chart_session = _normalize_market_session_for_symbol(ticker, session)

    with _analysis_request_context(analysis_source, ticker, prefer_bot):
        chart_settings = _chart_analysis_settings(
            eng,
            ticker,
            prefer_bot,
            market_session=chart_session,
        )
        try:
            async with _bounded_analysis_work(eng.uid, "chart analysis", _ANALYSIS_CHART_QUEUE_TIMEOUT_SECONDS, _ANALYSIS_CHART_LANE):
                analysis, cached, cache_age = await asyncio.wait_for(
                    asyncio.to_thread(_get_cached_chart_analysis, ticker, timeframe, eng, force, analysis_source, chart_settings),
                    timeout=30.0,
                )
        except asyncio.TimeoutError:
            raise HTTPException(status_code=504, detail=f"Chart refresh timed out for {ticker}")
        if not analysis:
            raise HTTPException(status_code=404, detail=f"Could not load chart data for {ticker}")
        if not _has_usable_chart_history(analysis):
            raise HTTPException(status_code=404, detail=f"No usable chart candles available for {ticker}")
        settings = eng.config.TICKER_SETTINGS.get(ticker, {}) if prefer_bot else {}

    # --- TEMPORARY LOGGING FOR DEBUGGING ---
    print(f"[perf] /api/chart {ticker} {timeframe}: {(time.perf_counter() - started) * 1000:.1f}ms cached={cached}")
    return _build_chart_payload(
        ticker,
        timeframe,
        analysis,
        settings,
        eng,
        use_bot_settings=prefer_bot,
        cached=cached,
        cache_age=cache_age,
        started=started,
    )


# ── Settings-modal mini chart (candles only) ────────────────────────────────
# The full chart endpoints run a whole indicator analysis behind a shared work queue, which made the small preview
# in the settings dialog time out. This path returns only recent candles: its own tiny cache, one fetch per
# (symbol, timeframe) at a time, a separate small concurrency limit, and the last good bars when a refresh fails.
_MINI_CHART_TIMEFRAMES = {"30Sec": "s30", "1Min": "m1", "2Min": "m2", "3Min": "m3", "5Min": "m5", "10Min": "m10",
                          "15Min": "m15", "30Min": "m30", "1Hour": "m60", "2Hour": "m120", "4Hour": "m240", "1Day": "D"}
_MINI_CHART_BARS = 120
_MINI_CHART_CACHE: dict = {}
_MINI_CHART_INFLIGHT: dict = {}
_MINI_CHART_SEMAPHORE = asyncio.Semaphore(4)
_MINI_CHART_WAIT_SECONDS = 25.0
_MINI_CHART_STALE_SECONDS = 900.0


def _mini_chart_ttl(timeframe: str) -> float:
    return 8.0 if timeframe in {"30Sec", "1Min"} else (15.0 if timeframe in {"2Min", "3Min", "5Min"} else 30.0)


def _mini_chart_bars_from_provider(raw) -> list:
    import pandas as pd
    from indicators import _provider_time_to_datetime
    if isinstance(raw, list) and raw and isinstance(raw[0], dict) and "result" in raw[0]:
        raw = raw[0].get("result") or []
    if not isinstance(raw, list) or not raw:
        return []
    df = pd.DataFrame(raw)
    if "time" not in df.columns or df["time"].isna().all():
        return []
    df.columns = [str(c).lower() for c in df.columns]
    stamps = _provider_time_to_datetime(df["time"])
    out, seen = [], set()
    for ts, o, h, l, c, v in zip(stamps, *(pd.to_numeric(df.get(k), errors="coerce") for k in ("open", "high", "low", "close", "volume"))):
        if pd.isna(ts) or any(pd.isna(x) for x in (o, h, l, c)):
            continue
        t = int(ts.timestamp())
        if t in seen or min(o, h, l, c) <= 0:
            continue
        seen.add(t)
        out.append({"time": t, "open": float(o), "high": float(h), "low": float(l), "close": float(c), "volume": 0.0 if pd.isna(v) else float(v)})
    out.sort(key=lambda b: b["time"])
    return out[-_MINI_CHART_BARS:]


def _fetch_mini_chart_bars(symbol: str, timeframe: str) -> tuple:
    """(bars, source): Webull first, then Alpaca/Yahoo through the shared data layer."""
    try:
        from indicators import _get_wb_data_provider
        raw = _get_wb_data_provider().get_bars(symbol, timespan=_MINI_CHART_TIMEFRAMES[timeframe], count=_MINI_CHART_BARS + 40)
        bars = _mini_chart_bars_from_provider(raw)
        if len(bars) >= 5:
            return bars, "webull"
    except Exception as exc:
        print(f"[mini-chart] Webull bars unavailable for {symbol}: {_safe_exception_type(exc)}")
    from data_manager import get_historical_data
    end = datetime.now()
    days = 400 if timeframe == "1Day" else (5 if timeframe in {"30Sec", "1Min", "2Min", "3Min", "5Min"} else 20)
    frame = get_historical_data(symbol, timeframe, end - timedelta(days=days), end, force_yahoo=False)
    if frame is None or frame.empty:
        return [], "none"
    import pandas as pd
    index = pd.DatetimeIndex(frame.index)
    index = index.tz_localize("America/New_York", ambiguous="NaT", nonexistent="shift_forward") if index.tz is None else index
    rows = [{"time": int(t.timestamp()), "open": r.get("open", r.get("Open")), "high": r.get("high", r.get("High")),
             "low": r.get("low", r.get("Low")), "close": r.get("close", r.get("Close")), "volume": r.get("volume", r.get("Volume", 0))}
            for t, (_, r) in zip(index, frame.iterrows()) if not pd.isna(t)]
    return _mini_chart_bars_from_provider(rows), str((frame.attrs or {}).get("data_source") or "alpaca")


def _mini_chart_done(key: tuple, task) -> None:
    if _MINI_CHART_INFLIGHT.get(key) is task:
        _MINI_CHART_INFLIGHT.pop(key, None)
    if not task.cancelled():
        task.exception()  # mark retrieved: a failure with no waiter left must not log "never retrieved"


async def _load_mini_chart(key: tuple) -> dict:
    async with _MINI_CHART_SEMAPHORE:
        bars, source = await asyncio.to_thread(_fetch_mini_chart_bars, *key)
    if not bars:
        raise HTTPException(status_code=404, detail=f"No candles available for {key[0]}")
    entry = {"at": time.monotonic(), "bars": bars, "source": source}
    _MINI_CHART_CACHE[key] = entry
    if len(_MINI_CHART_CACHE) > 300:
        for old in sorted(_MINI_CHART_CACHE, key=lambda k: _MINI_CHART_CACHE[k]["at"])[:100]:
            _MINI_CHART_CACHE.pop(old, None)
    return entry


@app.get("/api/mini-chart/{ticker}")
async def get_mini_chart(ticker: str, timeframe: str = "5Min", user: dict = Depends(verify_token)):
    """Recent candles for the settings-dialog mini chart. Returns cached or stale bars rather than failing slowly."""
    symbol = _normalize_quote_symbol(ticker)
    if not re.fullmatch(r"[A-Z0-9.\-]{1,15}", symbol or ""):
        raise HTTPException(status_code=400, detail="Invalid ticker format")
    if timeframe not in _MINI_CHART_TIMEFRAMES:
        raise HTTPException(status_code=400, detail="Unsupported timeframe")
    key = (symbol, timeframe)
    cached = _MINI_CHART_CACHE.get(key)
    now = time.monotonic()

    def payload(entry: dict, stale: bool) -> dict:
        return {"ticker": symbol, "timeframe": timeframe, "bars": entry["bars"], "source": entry["source"],
                "stale": stale, "age_seconds": round(now - entry["at"], 1)}

    if cached and now - cached["at"] < _mini_chart_ttl(timeframe):
        return payload(cached, False)
    task = _MINI_CHART_INFLIGHT.get(key)
    if task is None or task.done():
        task = asyncio.ensure_future(_load_mini_chart(key))
        _MINI_CHART_INFLIGHT[key] = task
        task.add_done_callback(lambda t, k=key: _mini_chart_done(k, t))
    try:
        entry = await asyncio.wait_for(asyncio.shield(task), timeout=_MINI_CHART_WAIT_SECONDS)
        now = time.monotonic()
        return payload(entry, False)
    except HTTPException:
        raise
    except Exception as exc:
        if cached and now - cached["at"] <= _MINI_CHART_STALE_SECONDS:
            return payload(cached, True)  # the fetch keeps running; the next request gets the fresh bars
        if isinstance(exc, asyncio.TimeoutError):
            raise HTTPException(status_code=504, detail=f"Chart data for {symbol} is taking too long; try again shortly")
        print(f"[mini-chart] {symbol} {timeframe} failed: {_safe_exception_type(exc)}")
        raise HTTPException(status_code=502, detail=f"Chart data for {symbol} is unavailable right now")


@app.post("/api/bots/chart-preview")
async def get_bot_chart_preview(data: dict, eng: UserEngine = Depends(get_user_engine)):
    """Draft-safe chart preview for bot settings without persisting ticker settings."""
    ticker = _normalize_quote_symbol(data.get("ticker", ""))
    if not ticker or not re.fullmatch(r"[A-Z0-9.\-]{1,15}", ticker):
        return {"error": "Invalid ticker format"}

    draft_settings = _normalize_draft_bot_settings(ticker, data.get("settings", {}), eng)
    draft_settings["_botbulls5_recent_session_only"] = True
    # Chart preview needs enough warm-up for the selected indicators, but does
    # not need the full live-chart history. Keeping the draft calculation to a
    # bounded recent window avoids slow multi-page provider requests while
    # preserving the latest candles shown in the preview.
    draft_settings["_chart_preview"] = True
    try:
        preview_display_bars = int(draft_settings.get("_chart_display_bars", 800) or 800)
    except (TypeError, ValueError):
        preview_display_bars = 800
    draft_settings["_chart_display_bars"] = max(300, min(preview_display_bars, 800))
    timeframe = data.get("timeframe") or draft_settings.get("timeframe") or eng.config.DEFAULT_TIMEFRAME
    # The draft settings/timeframe are included in the cache key. A forced
    # provider fetch is unnecessary for normal preview edits and can turn a
    # quick cached response into a 45-second Webull calculation.
    force = bool(data.get("force", False))
    preview_cache_key = stable_cache_key(
        eng.uid,
        "bots",
        ticker,
        timeframe,
        "webull",
        _cache_settings_hash(eng, ticker, "bots", draft_settings),
        3,
    )
    started = time.perf_counter()
    overlay_token = set_draft_ticker_settings({ticker: draft_settings})

    try:
        with _analysis_request_context("bots", ticker, True):
            try:
                async with _bounded_analysis_work(eng.uid, "chart preview", _ANALYSIS_CHART_QUEUE_TIMEOUT_SECONDS, _ANALYSIS_CHART_LANE):
                    analysis, cached, cache_age = await asyncio.wait_for(
                        asyncio.to_thread(_get_cached_chart_analysis, ticker, timeframe, eng, force, "bots", draft_settings),
                        timeout=45.0,
                    )
            except asyncio.TimeoutError:
                # A previous preview is still useful while a slow provider
                # refresh finishes in the background. Return it instead of
                # replacing a working chart with a 504 whenever stale data is
                # available for this exact draft/timeframe.
                stale_cached, stale_age = _chart_analysis_cache.peek_with_age(preview_cache_key)
                if stale_cached and stale_cached.get("analysis"):
                    print(f"[preview] Provider refresh slow for {ticker}; serving stale chart preview")
                    analysis, cached, cache_age = stale_cached["analysis"], True, stale_age
                else:
                    raise HTTPException(status_code=504, detail=f"Chart preview timed out for {ticker}")

            if not analysis:
                raise HTTPException(status_code=404, detail=f"Could not load chart preview for {ticker}")
            if not _has_usable_chart_history(analysis):
                raise HTTPException(status_code=404, detail=f"No usable chart candles available for {ticker}")

            ai_data = {}
            ai_status = "disabled"
            ai_enabled = False  # News AI no longer takes part in bot decisions
            if ai_enabled:
                try:
                    ai_data = await asyncio.wait_for(
                        asyncio.to_thread(get_ai_sentiment, ticker),
                        timeout=4.0,
                    )
                    ai_status = "available"
                except asyncio.TimeoutError:
                    ai_status = "timeout"
                except Exception as exc:
                    print(f"[preview] AI sentiment unavailable for {ticker}: {_safe_exception_type(exc)}")
                    ai_status = "unavailable"

            preview_meta = {
                "settings_source": "draft",
                "candle_mode": "last_closed",
                "trading_mode": "options" if draft_settings.get("options_trading") else "regular",
                "options_direction": draft_settings.get("options_type", "calls_only") if draft_settings.get("options_trading") else "stock",
                "ai_status": ai_status,
            }
            return _build_chart_payload(
                ticker,
                timeframe,
                analysis,
                draft_settings,
                eng,
                use_bot_settings=True,
                cached=cached,
                cache_age=cache_age,
                started=started,
                ai_data=ai_data,
                preview_meta=preview_meta,
            )
    finally:
        reset_draft_ticker_settings(overlay_token)


AI_SETTINGS_KEYS = {
    "AI_INDICATOR_MODEL",
    "AI_DECISION_TIMEFRAME",
    "AI_MIN_SETUP_SCORE",
    "AI_MIN_REWARD_RISK",
    "AI_MAX_ENTRY_RSI",
    "AI_MIN_VOLUME_RATIO",
    "AI_REQUIRE_VOLUME_CONFIRMATION",
    "AI_NEWS_ALIGNMENT_MODE",
    "AI_INDICATOR_CACHE_MINUTES",
}


def _ai_settings_payload(eng: UserEngine, context: str) -> dict:
    token = active_evaluation_context.set(context)
    try:
        return {
            "AI_INDICATOR_MODEL": normalize_ai_model_name(
                getattr(eng.config, "AI_INDICATOR_MODEL", "llama-3.3-70b-versatile")
            ) or "llama-3.3-70b-versatile",
            "AI_DECISION_TIMEFRAME": normalize_ai_timeframe(getattr(eng.config, "AI_DECISION_TIMEFRAME", AI_CANDLE_TIMEFRAME)),
            "AI_MIN_SETUP_SCORE": float(getattr(eng.config, "AI_MIN_SETUP_SCORE", 70)),
            "AI_MIN_REWARD_RISK": float(getattr(eng.config, "AI_MIN_REWARD_RISK", 1.5)),
            "AI_MAX_ENTRY_RSI": float(getattr(eng.config, "AI_MAX_ENTRY_RSI", 72)),
            "AI_MIN_VOLUME_RATIO": float(getattr(eng.config, "AI_MIN_VOLUME_RATIO", 0.8)),
            "AI_REQUIRE_VOLUME_CONFIRMATION": bool(getattr(eng.config, "AI_REQUIRE_VOLUME_CONFIRMATION", False)),
            "AI_NEWS_ALIGNMENT_MODE": str(getattr(eng.config, "AI_NEWS_ALIGNMENT_MODE", "veto_conflicting_entries")),
            "AI_INDICATOR_CACHE_MINUTES": int(getattr(eng.config, "AI_INDICATOR_CACHE_MINUTES", 30)),
            "available_timeframes": list(AI_TIMEFRAME_SPECS.keys()),
            "news_timeframe": None,
            "context": context,
        }
    finally:
        active_evaluation_context.reset(token)


@app.get("/api/settings/ai")
def get_ai_settings(context: str = "dashboard", eng: UserEngine = Depends(get_user_engine)):
    """Return isolated Quant AI settings for one product page."""
    return _ai_settings_payload(eng, _normalize_analysis_context(context))


@app.post("/api/settings/ai")
async def update_ai_settings(updates: dict, context: str = "dashboard", eng: UserEngine = Depends(get_user_engine)):
    """Validate and persist isolated Quant AI decision settings."""
    ctx = _normalize_analysis_context(context)
    clean = {key: value for key, value in (updates or {}).items() if key in AI_SETTINGS_KEYS}
    if "AI_INDICATOR_MODEL" in clean:
        model_name = normalize_ai_model_name(clean["AI_INDICATOR_MODEL"])
        cloud_models = {"openai/gpt-oss-120b", "openai/gpt-oss-20b"}
        if model_name not in cloud_models:
            raise HTTPException(status_code=400, detail="Unsupported AI indicator model")
        clean["AI_INDICATOR_MODEL"] = model_name
    if "AI_DECISION_TIMEFRAME" in clean:
        clean["AI_DECISION_TIMEFRAME"] = normalize_ai_timeframe(clean["AI_DECISION_TIMEFRAME"])
    numeric_ranges = {
        "AI_MIN_SETUP_SCORE": (50.0, 95.0),
        "AI_MIN_REWARD_RISK": (1.0, 5.0),
        "AI_MAX_ENTRY_RSI": (55.0, 85.0),
        "AI_MIN_VOLUME_RATIO": (0.0, 5.0),
        "AI_INDICATOR_CACHE_MINUTES": (1.0, 120.0),
    }
    for key, (minimum, maximum) in numeric_ranges.items():
        if key not in clean:
            continue
        try:
            value = float(clean[key])
        except (TypeError, ValueError):
            raise HTTPException(status_code=400, detail=f"{key} must be numeric")
        if not minimum <= value <= maximum:
            raise HTTPException(status_code=400, detail=f"{key} must be between {minimum:g} and {maximum:g}")
        clean[key] = int(value) if key in {"AI_MIN_SETUP_SCORE", "AI_INDICATOR_CACHE_MINUTES"} else value
    if "AI_REQUIRE_VOLUME_CONFIRMATION" in clean:
        clean["AI_REQUIRE_VOLUME_CONFIRMATION"] = bool(clean["AI_REQUIRE_VOLUME_CONFIRMATION"])
    if "AI_NEWS_ALIGNMENT_MODE" in clean:
        mode = str(clean["AI_NEWS_ALIGNMENT_MODE"]).lower()
        if mode not in {"off", "veto_conflicting_entries"}:
            raise HTTPException(status_code=400, detail="Invalid AI news alignment mode")
        clean["AI_NEWS_ALIGNMENT_MODE"] = mode

    token = active_evaluation_context.set(ctx)
    try:
        for key, value in clean.items():
            eng.config.set(key, value)
    finally:
        active_evaluation_context.reset(token)
    clear_ai_cache()
    _queue_settings_save(eng, "Quant AI settings")
    return {"status": "success", "settings": _ai_settings_payload(eng, ctx)}


@app.get("/api/ai-indicator")
async def get_ai_indicator_endpoint(
    ticker: str,
    timeframe: str = None,
    model: str = None,
    force: bool = False,
    context: str = "dashboard",
    eng: UserEngine = Depends(get_user_engine)
):
    """
    Agentic AI Indicator — Groq/Ollama-powered autonomous market research.
    Returns a BUY/SELL/HOLD signal with position sizing, leverage guidance,
    sell targets, and a detailed reasoning summary.
    """
    ticker = _normalize_quote_symbol(ticker)
    if not re.fullmatch(r"[A-Z0-9.\-]{1,15}", ticker):
        return {"error": "Invalid ticker format"}
    context = _normalize_analysis_context(context)
    # Screener uses /api/screener/ai-indicator. Keep the shared Dashboard,
    # Bots, and Backtest endpoint from starting duplicate BTCUSD AI/news scans.
    if ticker == "BTCUSD":
        return {
            "skipped": True,
            "ticker": ticker,
            "signal": "HOLD",
            "confidence": 0,
            "reason": "BTCUSD AI indicator is available only in Screener.",
        }
    context_token = active_evaluation_context.set(context)
    try:
        configured_timeframe = getattr(eng.config, "AI_DECISION_TIMEFRAME", AI_CANDLE_TIMEFRAME)
    finally:
        active_evaluation_context.reset(context_token)
    timeframe = normalize_ai_timeframe(timeframe or configured_timeframe)

    if not getattr(eng.config, "ENABLE_AI_INDICATOR", True):
        return {"error": "AI Indicator is disabled in settings", "signal": "HOLD", "confidence": 0}

    started = time.perf_counter()

    try:
        account = await asyncio.wait_for(
            asyncio.to_thread(eng.broker.get_account_info),
            timeout=3.0,
        )
    except asyncio.TimeoutError:
        account = {"cash": 10000.0, "equity": 10000.0}
    available_cash = float(account.get("cash", 10000.0))
    account_equity = float(account.get("equity", 10000.0))

    model_key = model or getattr(eng.config, "AI_INDICATOR_MODEL", "llama-3.3-70b-versatile")
    inflight_key = stable_cache_key(eng.uid, "ai_indicator_endpoint", context, ticker, timeframe, model_key)

    def _run_coalesced_ai_indicator():
        ai_lock = _ai_indicator_endpoint_cache.with_inflight_lock(inflight_key)
        try:
            with ai_lock:
                with _analysis_request_context(context, ticker, context == "bots"):
                    if force:
                        clear_ai_cache(ticker, timeframe, include_news=True)
                    return get_ai_indicator(
                        ticker,
                        timeframe,
                        available_cash,
                        account_equity,
                        model,
                    )
        finally:
            _ai_indicator_endpoint_cache.release_inflight_lock(inflight_key, ai_lock)

    try:
        async with _bounded_analysis_work(eng.uid, "AI indicator"):
            result = await asyncio.wait_for(
                asyncio.to_thread(_run_coalesced_ai_indicator),
                timeout=25.0,
            )
        result.setdefault("performance", {})
        result["performance"].update({
            "total_ms": (time.perf_counter() - started) * 1000,
        })
        return result
    except asyncio.TimeoutError:
        print(f"[api] AI indicator timeout for {ticker}")
        return {
            "error": "AI indicator timed out",
            "signal": "HOLD",
            "confidence": 0,
            "timeframe": timeframe,
            "model": model,
        }
    except Exception as e:
        print(f"[api] AI indicator error for {ticker}: {_safe_exception_type(e)}")
        return {
            "error": "AI indicator temporarily unavailable",
            "signal": "HOLD",
            "confidence": 0,
            "summary": "AI Indicator temporarily unavailable.",
            "cached": False,
            "performance": {
                "total_ms": (time.perf_counter() - started) * 1000,
            },
        }


@app.get("/api/signals/{ticker}")
async def get_fast_signals(
    ticker: str,
    timeframe: str = None,
    source: str = "dashboard",
    force: bool = False,
    session: str = "regular",
    eng: UserEngine = Depends(get_user_engine)
):
    """Fast signal endpoint reusing cached chart analysis when available."""
    ticker = _normalize_quote_symbol(ticker)
    if not re.fullmatch(r"[A-Z0-9.\-]{1,15}", ticker):
        return {"error": "Invalid ticker format"}

    timeframe = timeframe or eng.config.TICKER_SETTINGS.get(ticker, {}).get('timeframe') or eng.config.DEFAULT_TIMEFRAME
    prefer_bot = ticker in eng.config.TRADELIST and source == "bots"
    analysis_source = "bots" if prefer_bot else source
    with _analysis_request_context(analysis_source, ticker, prefer_bot):
        chart_settings = _chart_analysis_settings(
            eng,
            ticker,
            prefer_bot,
            market_session=_normalize_market_session_for_symbol(ticker, session),
        )
        try:
            async with _bounded_analysis_work(eng.uid, "signal analysis"):
                analysis, cached, cache_age = await asyncio.wait_for(
                    asyncio.to_thread(_get_cached_chart_analysis, ticker, timeframe, eng, force, analysis_source, chart_settings),
                    timeout=30.0,
                )
        except asyncio.TimeoutError:
            raise HTTPException(status_code=504, detail=f"Signal refresh timed out for {ticker}")
        if not analysis:
            raise HTTPException(status_code=404, detail=f"Could not load signals for {ticker}")

        scan = _technical_scan_from_analysis(ticker, timeframe, analysis, use_bot_settings=prefer_bot)
        formatted_scan = _format_scan_for_ui(scan, use_global_settings=(not prefer_bot))
        if "price_history" in formatted_scan:
            del formatted_scan["price_history"]
    return _sanitize_json_values({
        "ticker": ticker,
        "timeframe": timeframe,
        "cached": cached,
        "cache_age_seconds": round(cache_age or 0.0, 1),
        "primaryScan": formatted_scan,
        "signals": formatted_scan.get("signals", {}),
        "action": formatted_scan.get("action", "HOLD"),
        "reason": formatted_scan.get("reason", ""),
        "bullish_count": formatted_scan.get("bullish_count", 0),
        "bearish_count": formatted_scan.get("bearish_count", 0),
        "total_signals": formatted_scan.get("total_signals", 0),
    })


@app.get("/api/scan/{ticker}")
async def scan_ticker(ticker: str, timeframe: str = "1Hour", eng: UserEngine = Depends(get_user_engine)):
    """On-demand scan of a specific ticker."""
    if not re.fullmatch(r"[A-Z0-9.\-]{1,15}", ticker.upper()):
        return {"error": "Invalid ticker format"}
    account = eng.broker.get_account_info()
    if account.get('simulation', True):
        return {"error": "Alpaca connection required for scanning."}

    now = datetime.now()
    cached_scan = eng._pick_scan(ticker.upper(), timeframe, prefer_bot=False)
    is_recent = False
    if cached_scan and cached_scan.get('timeframe') == timeframe:
        try:
            last_time = datetime.fromisoformat(cached_scan.get('time', ''))
            if (now - last_time).total_seconds() < 30:
                is_recent = True
        except: pass

    if is_recent:
        return _format_scan_for_ui(cached_scan)

    is_crypto = any(c in ticker.upper() for c in ["BTC", "ETH", "LTC", "SOL", "DOGE"]) or "USD" in ticker.upper()
    avail_cash = account.get('non_marginable_buying_power') or account['cash'] if is_crypto else account['cash']

    async with _bounded_analysis_work(eng.uid, "on-demand scan"):
        result = await asyncio.to_thread(
            evaluate_trade,
            ticker.upper(),
            account_equity=account['equity'],
            available_cash=avail_cash,
            timeframe=timeframe,
        )
    if result:
        eng._record_scan(result)
        return _format_scan_for_ui(result)
    return {"error": f"Could not analyze {ticker}"}


@app.post("/api/backtest/cancel")
async def cancel_backtest(eng: UserEngine = Depends(get_user_engine)):
    """Cancel the authenticated user's running backtest, if any."""
    _get_backtest_cancel_event(eng.uid).set()
    return {"status": "cancelled", "message": "Backtest cancelled successfully"}

@app.get("/api/backtest/cache")
async def backtest_cache_status(ticker: str, eng: UserEngine = Depends(get_user_engine)):
    """Which candle sets a backtest of ``ticker`` would reuse from the local cache."""
    symbol = str(ticker or "").strip().upper()
    if not re.fullmatch(r"[A-Z0-9.\-]{1,15}", symbol):
        raise HTTPException(status_code=400, detail="Invalid ticker format")
    from data_manager import list_cached_data
    entries = await asyncio.to_thread(list_cached_data, symbol)
    return {"status": "success", "ticker": symbol, "cached": entries}


@app.post("/api/backtest/cache/purge")
async def backtest_cache_purge(data: dict, eng: UserEngine = Depends(get_user_engine)):
    """Delete the cached candle data of one ticker so the next backtest downloads it again.

    The cache holds public market candles shared by every user, so a purge only costs the next run a
    download (Alpaca first, Yahoo as the fallback). It never touches settings or trade history."""
    symbol = str((data or {}).get("ticker") or "").strip().upper()
    if not re.fullmatch(r"[A-Z0-9.\-]{1,15}", symbol):
        raise HTTPException(status_code=400, detail="Invalid ticker format")
    from data_manager import purge_cached_data
    result = await asyncio.to_thread(purge_cached_data, symbol)
    print(f"[backtest] Cache purged for {symbol}: {result['files_removed']} file(s)")
    return {"status": "success", "ticker": symbol, **result}


@app.post("/api/backtest/replay/data")
async def get_backtest_replay_data(data: dict, eng: UserEngine = Depends(get_user_engine)):
    """Load an independent, indicator-enriched data set for manual backtest replay.

    This endpoint intentionally does not execute a strategy or create strategy
    trades. It only returns the historical candle/indicator payload consumed by
    the backtest page's manual replay chart.
    """
    ticker = str(data.get("ticker", "")).strip().upper()
    if not re.fullmatch(r"[A-Z0-9.\-/]{1,15}", ticker):
        raise HTTPException(status_code=400, detail="Invalid ticker format")
    timeframe = str(data.get("timeframe", "1Hour") or "1Hour")
    allowed_timeframes = {"1Min", "5Min", "10Min", "15Min", "30Min", "1Hour", "4Hour", "1Day"}
    if timeframe not in allowed_timeframes:
        raise HTTPException(status_code=400, detail="Unsupported replay timeframe")
    try:
        days = max(1, min(int(data.get("days", 90) or 90), 1825))
    except (TypeError, ValueError):
        raise HTTPException(status_code=400, detail="days must be an integer")

    requested_source = str(data.get("source") or "alpaca").strip().lower()
    if requested_source == "webull":
        requested_source = "alpaca"
    if requested_source not in {"alpaca", "yahoo"}:
        requested_source = "alpaca"
    force_yahoo = requested_source == "yahoo"

    if force_yahoo:
        if timeframe == "1Min":
            days = min(days, 7)
        elif timeframe in {"5Min", "10Min", "15Min", "30Min"}:
            days = min(days, 60)
        elif timeframe in {"1Hour", "4Hour"}:
            days = min(days, 730)
    else:
        if timeframe in {"1Min", "2Min", "3Min", "5Min", "10Min", "15Min", "30Min", "1Hour", "2Hour", "4Hour"}:
            days = min(days, 366)
        elif timeframe == "1Day":
            days = min(days, 1825)

    indicators = data.get("indicators") if isinstance(data.get("indicators"), list) else []
    indicator_overrides = data.get("indicator_overrides") if isinstance(data.get("indicator_overrides"), dict) else {}
    selected_indicators = [str(value) for value in indicators if value]
    if len(selected_indicators) > DASHBOARD_INDICATOR_DISPLAY_LIMIT * 2:
        raise HTTPException(status_code=413, detail="Too many replay indicators requested")
    market_session = _normalize_market_session_for_symbol(
        ticker,
        data.get("market_session", "Regular"),
    )
    settings = {
        "source": "backtest",
        # Calculate only what the manual chart currently displays. The caller
        # can request a different set on the next one-time refresh.
        "enabled_indicators": selected_indicators,
        "indicator_overrides": indicator_overrides,
        "buy_mode": data.get("buy_mode", ""),
        "sell_mode": data.get("sell_mode", ""),
        "market_session": market_session,
    }
    replay_interval_seconds = {
        "1Min": 60,
        "5Min": 300,
        "10Min": 600,
        "15Min": 900,
        "30Min": 1800,
        "1Hour": 3600,
        "4Hour": 14400,
        "1Day": 86400,
    }.get(timeframe, 0)
    end_date = get_now()
    start_date = end_date - timedelta(days=days)
    try:
        from indicators import calculate_indicators_for_df, _unix_timestamp
        previous_context = active_evaluation_context.get()
        active_evaluation_context.set("backtest")
        async with _bounded_analysis_work(eng.uid, "replay"):
            try:
                raw_df = await asyncio.to_thread(get_historical_data, ticker, timeframe, start_date, end_date, force_yahoo)
                if raw_df is None or raw_df.empty:
                    raise HTTPException(status_code=404, detail=f"No historical data available for {ticker}")
                if 0 < replay_interval_seconds < 86400:
                    completed_cutoff = int(end_date.timestamp())
                    completed_mask = [
                        _unix_timestamp(index_value) + replay_interval_seconds <= completed_cutoff
                        for index_value in raw_df.index
                    ]
                    completed_df = raw_df.loc[completed_mask].copy()
                    if completed_df.empty:
                        raise HTTPException(status_code=404, detail=f"No completed replay candles available for {ticker}")
                    completed_df.attrs.update(getattr(raw_df, "attrs", {}) or {})
                    raw_df = completed_df
                actual_source = str(getattr(raw_df, "attrs", {}).get("data_source") or ("yahoo" if force_yahoo else "alpaca")).lower()
                analysis = await asyncio.to_thread(calculate_indicators_for_df, raw_df, timeframe, ticker, settings)
            finally:
                active_evaluation_context.set(previous_context)
        candles = (analysis or {}).get("price_history", []) if isinstance(analysis, dict) else []
        candles = [candle for candle in candles if isinstance(candle, dict) and candle.get("time") is not None and candle.get("open") is not None and candle.get("close") is not None]
        if 0 < replay_interval_seconds < 86400:
            completed_cutoff = int(end_date.timestamp())
            candles = [
                candle for candle in candles
                if int(float(candle["time"])) + replay_interval_seconds <= completed_cutoff
            ]
        if len(candles) > MAX_REPLAY_CANDLES:
            candles = candles[-MAX_REPLAY_CANDLES:]
        raw_signals = (analysis or {}).get("signals", {}) if isinstance(analysis, dict) else {}
        signals = {
            name: {
                **signal,
                "enabled": (name in selected_indicators),
                "toggle_key": "",
            }
            for name, signal in raw_signals.items()
            if isinstance(signal, dict)
        }
        bullish_count = sum(s.get("weight", 1) for s in signals.values() if s.get("enabled") and s.get("signal") == "BULLISH")
        bearish_count = sum(s.get("weight", 1) for s in signals.values() if s.get("enabled") and s.get("signal") == "BEARISH")
        if not candles:
            raise HTTPException(status_code=404, detail=f"No replay candles available for {ticker}")
        return {
            "status": "success",
            "ticker": ticker,
            "timeframe": timeframe,
            "days": days,
            "data_source": actual_source,
            "requested_source": requested_source,
            "market_session": market_session,
            "session_hours": (
                "24h"
                if _is_crypto_symbol(ticker)
                else ("04:00-20:00 America/New_York" if str(market_session).lower() == "extended" else "09:30-16:00 America/New_York")
            ),
            "candles": candles,
            "candle_count": len(candles),
            "first_candle_time": candles[0]["time"] if candles else None,
            "last_candle_time": candles[-1]["time"] if candles else None,
            "last_candle_close_time": (
                int(float(candles[-1]["time"])) + replay_interval_seconds
                if candles and 0 < replay_interval_seconds < 86400
                else (candles[-1]["time"] if candles else None)
            ),
            "signals": signals,
            "bullish_count": bullish_count,
            "bearish_count": bearish_count,
            "total_signals": sum(1 for s in signals.values() if s.get("enabled")),
            "volume_profile": (analysis or {}).get("volume_profile") or {},
        }
    except Exception as exc:
        print(f"[backtest-replay] Data load failed for {ticker}: {_safe_exception_type(exc)}")
        raise HTTPException(status_code=500, detail="Could not load replay data")


@app.post("/api/backtest")
async def run_backtest(data: dict, eng: UserEngine = Depends(get_user_engine)):
    """Runs a historical backtest for a ticker."""
    data = dict(data or {})
    _normalize_percentage_strategy_settings(data)
    cancel_event = _get_backtest_cancel_event(eng.uid)
    cancel_event.clear()
    from user_config import active_evaluation_context, active_ticker_context
    active_evaluation_context.set("backtest")
    indicator_overrides = data.get("indicator_overrides", {})
    if not isinstance(indicator_overrides, dict):
        indicator_overrides = {}
    daily_high_low_buffer_pct = resolve_high_low_buffer_pct(indicator_overrides)

    ticker = data.get("ticker", "AAPL").upper()
    active_ticker_context.set(ticker)

    if not re.fullmatch(r"[A-Z0-9.\-]{1,15}", ticker):
        return {"status": "error", "message": "Invalid ticker format"}
    _strip_strategy_preset_server_keys(data)
    preset_enabled, preset_id, preset_variant = strategy_presets.normalize_strategy_preset(data)
    if preset_enabled:
        _validate_strategy_preset_request(data, ticker)
    timeframe = data.get("timeframe", "1Day")
    try:
        days = int(data.get("days", 30))
    except (TypeError, ValueError):
        raise HTTPException(status_code=400, detail="days must be a whole number")
    if not 1 <= days <= MAX_BACKTEST_DAYS:
        raise HTTPException(status_code=413, detail=f"days must be between 1 and {MAX_BACKTEST_DAYS}")
    capital = float(data.get("capital", 1000.0))
    allocation_mode = data.get("allocation_mode", "fixed")
    trade_amount = float(data.get("trade_amount", capital))
    threshold = int(data.get("threshold", 5))
    sell_threshold = int(data.get("sell_threshold", 3))
    indicators = strip_removed(data.get("indicators", []))[0]
    if threshold <= 0 and indicators:
        threshold = 1
    if sell_threshold <= 0 and indicators:
        sell_threshold = 1
    ext_hours = data.get("ext_hours", True)
    enable_risk = bool(data.get("enable_risk", True))

    end_date = get_now()
    start_date = end_date - timedelta(days=days)

    sell_mode = data.get("sell_mode", "indicator")
    risk_per_trade = 1.0 if not enable_risk else min(max(float(data.get("risk_per_trade", 0.5)), 0.0), 1.0)
    max_pos_pct = 1.0 if not enable_risk else float(data.get("max_position_pct", 1.0))
    atr_stop_multiplier = float(data.get("atr_stop_multiplier", 2.0))
    atr_trail_multiplier = float(data.get("atr_trail_multiplier", 3.0))
    atr_take_profit_multiplier = float(data.get("atr_take_profit_multiplier", 4.0))
    enable_atr_trail = bool(data.get("enable_atr_trail", True))

    regular_stop_loss_pct = float(data.get("regular_stop_loss_pct", 0.05))
    regular_take_profit_pct = float(data.get("regular_take_profit_pct", 0.10))
    regular_trailing_pct = float(data.get("regular_trailing_pct", 0.03))
    enable_pct_trail = bool(data.get("enable_pct_trail", True))
    custom_stop_loss_price = float(data.get("custom_stop_loss_price") or data.get("custom_underlying_stop_loss_price") or 0.0)
    custom_take_profit_price = float(data.get("custom_take_profit_price") or data.get("custom_underlying_take_profit_price") or 0.0)
    swing_exit_lookback_days = int(data.get("swing_exit_lookback_days", data.get("swing_lookback_days", 20)) or 20)
    entry_atr_multiplier = data.get("entry_atr_multiplier")
    if entry_atr_multiplier is None:
        entry_atr_multiplier = data.get("atr_stop_multiplier", 2.0)
    entry_pct_move = data.get("entry_pct_move")
    if entry_pct_move is None:
        entry_pct_move = max(1.0, regular_stop_loss_pct * 100.0)

    ai_autopilot = data.get("ai_autopilot", False) or (sell_mode == "ai_autopilot")
    ai_interval = int(data.get("ai_interval", 10))
    ai_model = data.get("ai_model", "llama-3.3-70b-versatile")
    continuous_grid_settings = dict(data)
    continuous_grid_settings["amount"] = trade_amount
    continuous_grid_settings["allocation_mode"] = allocation_mode
    _normalize_continuous_grid_settings(continuous_grid_settings)

    from backtester import Backtester
    bt = Backtester(
        ticker=ticker,
        timeframe=timeframe,
        start_date=start_date,
        end_date=end_date,
        initial_capital=capital,
        threshold=threshold,
        sell_threshold=sell_threshold,
        enabled_indicators=indicators,
        allocation_mode=allocation_mode,
        trade_amount=trade_amount,
        risk_per_trade=risk_per_trade,
        max_pos_pct=max_pos_pct,
        ext_hours=ext_hours,
        sell_mode=sell_mode,
        atr_stop_multiplier=atr_stop_multiplier,
        atr_trail_multiplier=atr_trail_multiplier,
        atr_take_profit_multiplier=atr_take_profit_multiplier,
        enable_atr_trail=enable_atr_trail,
        regular_stop_loss_pct=regular_stop_loss_pct,
        regular_take_profit_pct=regular_take_profit_pct,
        regular_trailing_pct=regular_trailing_pct,
        enable_pct_trail=enable_pct_trail,
        custom_stop_loss_price=custom_stop_loss_price,
        custom_take_profit_price=custom_take_profit_price,
        buy_mode=data.get("buy_mode", "indicator"),
        custom_buy_price=float(data.get("custom_buy_price", 0.0)),
        entry_atr_multiplier=float(entry_atr_multiplier),
        entry_pct_move=float(entry_pct_move),
        entry_atr_style=data.get("entry_atr_style", "pullback_reclaim"),
        entry_pct_style=data.get("entry_pct_style", "dip_reclaim"),
        swing_entry_mode=data.get("swing_entry_mode", "bounce_retest"),
        swing_entry_lookback_days=int(data.get("swing_entry_lookback_days", data.get("swing_lookback_days", 20)) or 20),
        swing_exit_lookback_days=swing_exit_lookback_days,
        market_session=data.get("market_session", "Regular"),
        enable_signal_reset=bool(data.get("enable_signal_reset", True)),
        enable_smart_reentry=bool(data.get("enable_smart_reentry", False)),
        smart_reentry_type=data.get("smart_reentry_type", "RSI"),
        smart_reentry_rsi_call_max=float(data.get("smart_reentry_rsi_call_max", 70.0)),
        smart_reentry_rsi_put_min=float(data.get("smart_reentry_rsi_put_min", 30.0)),
        smart_reentry_vwap_call_max_dist_pct=float(data.get("smart_reentry_vwap_call_max_dist_pct", 0.5)),
        smart_reentry_vwap_put_max_dist_pct=float(data.get("smart_reentry_vwap_put_max_dist_pct", 0.5)),
        ai_autopilot=ai_autopilot,
        ai_interval=ai_interval,
        ai_model=ai_model,
        enable_cooldown=bool(data.get("enable_cooldown", True)),
        continuous_grid_settings=continuous_grid_settings,
        cooldown_minutes=int(data["cooldown_minutes"]) if data.get("cooldown_minutes") is not None else None,
        enable_risk=enable_risk,
        daily_high_low_buffer_pct=daily_high_low_buffer_pct,
        indicator_overrides=indicator_overrides,
        post_stop_stabilization_bars=int(data.get("post_stop_stabilization_bars", 3) or 0),
        max_consecutive_losses=int(data.get("max_consecutive_losses", 2) or 0),
        max_daily_loss_pct=float(data.get("max_daily_loss_pct", 0.0) or 0.0),
        enable_liquidity_filter=bool(data.get("enable_liquidity_filter", True)),
        min_relative_volume=float(data.get("min_relative_volume", 0.10) or 0.0),
        cancel_event=cancel_event,
        strategy_preset=preset_id if preset_enabled else None,
        strategy_preset_variant=preset_variant if preset_enabled else None,
        allocation_settings={k: data.get(k) for k in ("allocation_bull_etf", "allocation_bear_etf", "allocation_exec_time")},
        session_settings={k: data.get(k) for k in _SESSION_SETTING_KEYS},
    )

    try:
        try:
            await asyncio.wait_for(_backtest_semaphore.acquire(), timeout=_BACKTEST_QUEUE_TIMEOUT_SECONDS)
        except asyncio.TimeoutError:
            raise HTTPException(status_code=429, detail="Too many backtests are running; retry shortly.")
        try:
            results = await asyncio.to_thread(bt.run)
        finally:
            _backtest_semaphore.release()
        if "error" in results:
            return {"status": "error", "message": results["error"]}
        return {"status": "success", "results": results}
    except Exception as e:
        print(f"[api] Backtest crash: {_safe_exception_type(e)}")
        return {"status": "error", "message": "An internal error occurred during backtest simulation."}



@app.post("/api/backtest/optimize")
async def run_optimization_endpoint(data: dict, eng: UserEngine = Depends(get_user_engine)):
    data = dict(data or {})
    if strategy_presets.is_strategy_preset_active(data):
        # A strategy preset is a fixed, published rule set. Searching its
        # parameters would be curve fitting, so the optimizer refuses.
        raise HTTPException(status_code=400, detail="Strategy presets are fixed rule sets and cannot be optimized.")
    _normalize_percentage_strategy_settings(data)
    """Runs an Auto-Optimization grid search with In-Sample/Out-of-Sample splitting."""
    cancel_event = _get_backtest_cancel_event(eng.uid)
    cancel_event.clear()
    from user_config import active_evaluation_context, active_ticker_context
    active_evaluation_context.set("backtest")
    indicator_overrides = data.get("indicator_overrides", {})
    if not isinstance(indicator_overrides, dict):
        indicator_overrides = {}
    daily_high_low_buffer_pct = resolve_high_low_buffer_pct(indicator_overrides)

    ticker = data.get("ticker", "AAPL").upper()
    active_ticker_context.set(ticker)

    if not re.fullmatch(r"[A-Z0-9.\-]{1,15}", ticker):
        return {"status": "error", "message": "Invalid ticker format"}

    timeframe = data.get("timeframe", "1Day")
    try:
        days = int(data.get("days", 90))
    except (TypeError, ValueError):
        raise HTTPException(status_code=400, detail="days must be a whole number")
    if not 1 <= days <= MAX_OPTIMIZATION_DAYS:
        raise HTTPException(status_code=413, detail=f"days must be between 1 and {MAX_OPTIMIZATION_DAYS}")
    capital = float(data.get("capital", 1000.0))
    split_pct = float(data.get("oos_split_pct", 30.0))

    end_date = get_now()
    start_date = end_date - timedelta(days=days)

    # Base kwargs for the backtester
    base_kwargs = {
        "ticker": ticker,
        "timeframe": timeframe,
        "start_date": start_date,
        "end_date": end_date,
        "initial_capital": capital,
        "enabled_indicators": strip_removed(data.get("indicators", []))[0],
        "allocation_mode": data.get("allocation_mode", "fixed"),
        "trade_amount": float(data.get("trade_amount", capital)),
        "risk_per_trade": min(max(float(data.get("risk_per_trade", 0.5)), 0.0), 0.5),
        "max_pos_pct": float(data.get("max_position_pct", 0.25)),
        "ext_hours": data.get("ext_hours", True),
        "sell_mode": data.get("sell_mode", "indicator"),
        "ai_autopilot": False, # Disable AI autopilot during massive grid search to save Groq limits
        "enable_cooldown": bool(data.get("enable_cooldown", True)),
        "cooldown_minutes": int(data["cooldown_minutes"]) if data.get("cooldown_minutes") is not None else None,
        "buy_mode": data.get("buy_mode", "indicator"),
        "custom_buy_price": float(data.get("custom_buy_price", 0.0)),
        "entry_atr_multiplier": float(data.get("entry_atr_multiplier", data.get("atr_stop_multiplier", 2.0))),
        "entry_pct_move": float(data.get("entry_pct_move", max(1.0, float(data.get("regular_stop_loss_pct", 0.05)) * 100.0))),
        "entry_atr_style": data.get("entry_atr_style", "pullback_reclaim"),
        "entry_pct_style": data.get("entry_pct_style", "dip_reclaim"),
        "swing_entry_mode": data.get("swing_entry_mode", "bounce_retest"),
        "swing_entry_lookback_days": int(data.get("swing_entry_lookback_days", data.get("swing_lookback_days", 20)) or 20),
        "swing_exit_lookback_days": int(data.get("swing_exit_lookback_days", data.get("swing_lookback_days", 20)) or 20),
        "market_session": data.get("market_session", "Regular"),
        "regular_stop_loss_pct": float(data.get("regular_stop_loss_pct", 0.05)),
        "regular_take_profit_pct": float(data.get("regular_take_profit_pct", 0.10)),
        "regular_trailing_pct": float(data.get("regular_trailing_pct", 0.03)),
        "custom_stop_loss_price": float(data.get("custom_stop_loss_price", 0.0)),
        "custom_take_profit_price": float(data.get("custom_take_profit_price", 0.0)),
        "enable_signal_reset": bool(data.get("enable_signal_reset", True)),
        "enable_smart_reentry": bool(data.get("enable_smart_reentry", False)),
        "smart_reentry_type": data.get("smart_reentry_type", "RSI"),
        "smart_reentry_rsi_call_max": float(data.get("smart_reentry_rsi_call_max", 70.0)),
        "smart_reentry_rsi_put_min": float(data.get("smart_reentry_rsi_put_min", 30.0)),
        "smart_reentry_vwap_call_max_dist_pct": float(data.get("smart_reentry_vwap_call_max_dist_pct", 0.5)),
        "smart_reentry_vwap_put_max_dist_pct": float(data.get("smart_reentry_vwap_put_max_dist_pct", 0.5)),
        "daily_high_low_buffer_pct": daily_high_low_buffer_pct,
        "indicator_overrides": indicator_overrides,
        "post_stop_stabilization_bars": int(data.get("post_stop_stabilization_bars", 3) or 0),
        "max_consecutive_losses": int(data.get("max_consecutive_losses", 2) or 0),
        "max_daily_loss_pct": float(data.get("max_daily_loss_pct", 0.0) or 0.0),
        "enable_liquidity_filter": bool(data.get("enable_liquidity_filter", True)),
        "min_relative_volume": float(data.get("min_relative_volume", 0.10) or 0.0),
        "cancel_event": cancel_event,
    }
    continuous_grid_settings = dict(data)
    continuous_grid_settings["amount"] = base_kwargs["trade_amount"]
    continuous_grid_settings["allocation_mode"] = base_kwargs["allocation_mode"]
    _normalize_continuous_grid_settings(continuous_grid_settings)
    base_kwargs["continuous_grid_settings"] = continuous_grid_settings

    from optimizer import run_optimization
    try:
        try:
            await asyncio.wait_for(_backtest_semaphore.acquire(), timeout=_BACKTEST_QUEUE_TIMEOUT_SECONDS)
        except asyncio.TimeoutError:
            raise HTTPException(status_code=429, detail="Too many optimizations are running; retry shortly.")
        try:
            results = await run_optimization(base_kwargs, split_pct)
        finally:
            _backtest_semaphore.release()
        if "error" in results:
            return {"status": "error", "message": results["error"]}
        return {"status": "success", "results": results}
    except Exception as e:
        import traceback
        print(f"[api] Auto-Optimize crash: {_safe_exception_type(e)}")
        return {"status": "error", "message": "Optimization engine crashed."}

@app.post("/api/options/backtest")
async def run_options_backtest(data: dict, eng: UserEngine = Depends(get_user_engine)):
    """Runs a historical options backtest."""
    cancel_event = _get_backtest_cancel_event(eng.uid)
    cancel_event.clear()
    from user_config import active_evaluation_context, active_ticker_context
    active_evaluation_context.set("backtest")
    indicator_overrides = data.get("indicator_overrides", {})
    if not isinstance(indicator_overrides, dict):
        indicator_overrides = {}
    daily_high_low_buffer_pct = resolve_high_low_buffer_pct(indicator_overrides)

    ticker = data.get("ticker", "AAPL").upper()
    active_ticker_context.set(ticker)

    if not re.fullmatch(r"[A-Z0-9.\-]{1,15}", ticker):
        return {"status": "error", "message": "Invalid ticker format"}

    timeframe = data.get("timeframe", "1Day")
    try:
        days = int(data.get("days", 30))
    except (TypeError, ValueError):
        raise HTTPException(status_code=400, detail="days must be a whole number")
    if not 1 <= days <= MAX_OPTIONS_BACKTEST_DAYS:
        raise HTTPException(status_code=413, detail=f"days must be between 1 and {MAX_OPTIONS_BACKTEST_DAYS}")
    capital = float(data.get("capital", 1000.0))
    trade_amount = float(data.get("trade_amount", 100.0))
    try:
        expiry_days = int(data.get("expiry_days", 7))
    except (TypeError, ValueError):
        raise HTTPException(status_code=400, detail="expiry_days must be a whole number")
    if not 0 <= expiry_days <= 365:
        raise HTTPException(status_code=413, detail="expiry_days must be between 0 and 365")
    strike_type = data.get("strike_type", "ATM")
    try:
        options_type = validate_options_type(data.get("options_type", "calls_only"))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    stop_loss_pct = float(data.get("stop_loss_pct", 0.50))
    take_profit_pct = float(data.get("take_profit_pct", 1.00))
    enable_pct_trail = bool(data.get("enable_pct_trail", True))
    enable_atr_trail = bool(data.get("enable_atr_trail", True))
    exit_strategy = data.get("exit_strategy", "hybrid")
    close_before_expiry_hours = float(data.get("close_before_expiry_hours", 2.0))
    threshold = int(data.get("threshold", 5))
    sell_threshold = int(data.get("sell_threshold", 3))
    indicators = strip_removed(data.get("indicators", []))[0]
    if threshold <= 0 and indicators:
        threshold = 1
    if sell_threshold <= 0 and indicators:
        sell_threshold = 1
    ext_hours = data.get("ext_hours", True)
    r = float(data.get("r", 0.05))
    try:
        dividend_yield = min(max(float(data.get("dividend_yield") or 0.0), 0.0), 1.0)
    except (TypeError, ValueError):
        raise HTTPException(status_code=400, detail="dividend_yield must be a percentage expressed as a decimal")
    try:
        raw_option_contract_fee = data.get("option_contract_fee")
        option_contract_fee = min(max(float(0.65 if raw_option_contract_fee is None else raw_option_contract_fee), 0.0), 100.0)
    except (TypeError, ValueError):
        raise HTTPException(status_code=400, detail="option_contract_fee must be a non-negative dollar amount")
    enable_risk = bool(data.get("enable_risk", True))
    print(f"[options-backtest] Running {ticker} | {timeframe} | {days}d | BuyMode: {data.get('buy_mode','indicator')} (Thresh: {threshold}) | SellMode: {data.get('sell_mode','atr')} | Risk: {enable_risk}")

    enable_signal_reset = bool(data.get("enable_signal_reset", True))
    enable_smart_reentry = bool(data.get("enable_smart_reentry", False))
    smart_reentry_type = data.get("smart_reentry_type", "RSI")
    smart_reentry_rsi_call_max = int(data.get("smart_reentry_rsi_call_max", 70))
    smart_reentry_rsi_put_min = int(data.get("smart_reentry_rsi_put_min", 30))
    smart_reentry_vwap_call_max_dist_pct = float(data.get("smart_reentry_vwap_call_max_dist_pct", 0.5))
    smart_reentry_vwap_put_max_dist_pct = float(data.get("smart_reentry_vwap_put_max_dist_pct", 0.5))
    entry_atr_multiplier = data.get("entry_atr_multiplier")
    if entry_atr_multiplier is None:
        entry_atr_multiplier = data.get("atr_stop_multiplier", 2.0)
    entry_pct_move = data.get("entry_pct_move")
    if entry_pct_move is None:
        entry_pct_move = max(1.0, float(data.get("stop_loss_pct", stop_loss_pct)) * 100.0)

    end_date = get_now()
    start_date = end_date - timedelta(days=days)

    from options_backtester import OptionsBacktester
    opt_bt = OptionsBacktester(
        ticker=ticker,
        timeframe=timeframe,
        start_date=start_date,
        end_date=end_date,
        initial_capital=capital,
        trade_amount=trade_amount,
        expiry_days=expiry_days,
        strike_type=strike_type,
        options_type=options_type,
        stop_loss_pct=stop_loss_pct,
        take_profit_pct=take_profit_pct,
        exit_strategy=exit_strategy,
        close_before_expiry_hours=close_before_expiry_hours,
        threshold=threshold,
        sell_threshold=sell_threshold,
        enabled_indicators=indicators,
        ext_hours=ext_hours,
        r=r,
        dividend_yield=dividend_yield,
        option_contract_fee=option_contract_fee,
        cancel_event=cancel_event,
        enable_signal_reset=enable_signal_reset,
        enable_smart_reentry=enable_smart_reentry,
        smart_reentry_type=smart_reentry_type,
        smart_reentry_rsi_call_max=smart_reentry_rsi_call_max,
        smart_reentry_rsi_put_min=smart_reentry_rsi_put_min,
        smart_reentry_vwap_call_max_dist_pct=smart_reentry_vwap_call_max_dist_pct,
        smart_reentry_vwap_put_max_dist_pct=smart_reentry_vwap_put_max_dist_pct,
        enable_cooldown=bool(data.get("enable_cooldown", True)),
        cooldown_minutes=int(data["cooldown_minutes"]) if data.get("cooldown_minutes") is not None else None,
        enable_atr_trail=enable_atr_trail,
        enable_pct_trail=enable_pct_trail,
        buy_mode=data.get("buy_mode", "indicator"),
        custom_buy_price=float(data.get("custom_buy_price", 0.0)),
        custom_stop_loss_price=float(data.get("custom_stop_loss_price", 0.0)),
        custom_take_profit_price=float(data.get("custom_take_profit_price", 0.0)),
        custom_underlying_stop_loss_price=float(data.get("custom_underlying_stop_loss_price", 0.0)),
        custom_underlying_take_profit_price=float(data.get("custom_underlying_take_profit_price", 0.0)),
        entry_atr_multiplier=float(entry_atr_multiplier),
        entry_pct_move=float(entry_pct_move),
        entry_atr_style=data.get("entry_atr_style", "pullback_reclaim"),
        entry_pct_style=data.get("entry_pct_style", "dip_reclaim"),
        swing_entry_mode=data.get("swing_entry_mode", "bounce_retest"),
        swing_entry_lookback_days=int(data.get("swing_entry_lookback_days", data.get("swing_lookback_days", 20)) or 20),
        swing_exit_lookback_days=int(data.get("swing_exit_lookback_days", data.get("swing_lookback_days", 20)) or 20),
        market_session=data.get("market_session", "Regular"),
        sell_mode=data.get("sell_mode", data.get("exit_strategy", "indicator")),
        atr_stop_multiplier=float(data.get("atr_stop_multiplier", 2.0)),
        atr_trail_multiplier=float(data.get("atr_trail_multiplier", 3.0)),
        atr_take_profit_multiplier=float(data.get("atr_take_profit_multiplier", 4.0)),
        regular_stop_loss_pct=float(data.get("stop_loss_pct", stop_loss_pct)),
        regular_take_profit_pct=float(data.get("take_profit_pct", take_profit_pct)),
        regular_trailing_pct=float(data.get("regular_trailing_pct", 0.03)),
        risk_per_trade=1.0 if not enable_risk else min(max(float(data.get("risk_per_trade", 0.5)), 0.0), 1.0),
        max_pos_pct=1.0 if not enable_risk else float(data.get("max_pos_pct", 1.0)),
        enable_risk=enable_risk,
        daily_high_low_buffer_pct=daily_high_low_buffer_pct,
        indicator_overrides=indicator_overrides,
        post_stop_stabilization_bars=int(data.get("post_stop_stabilization_bars", 3) or 0),
        max_consecutive_losses=int(data.get("max_consecutive_losses", 2) or 0),
        max_daily_loss_pct=float(data.get("max_daily_loss_pct", 0.0) or 0.0),
        options_max_entry_spread_pct=float(data.get("options_max_entry_spread_pct", 5.0) or 0.0),
        options_market_open_blackout_minutes=int(data.get("options_market_open_blackout_minutes", 15) or 0),
    )


    try:
        async with _bounded_analysis_work(eng.uid, "options backtest"):
            results = await asyncio.to_thread(opt_bt.run)
        if "error" in results:
            return {"status": "error", "message": results["error"]}
        return {"status": "success", "results": results}
    except Exception as e:
        import traceback
        traceback.print_exc()
        print(f"[api] Options Backtest crash: {_safe_exception_type(e)}")
        return {"status": "error", "message": "An internal error occurred during options backtest simulation."}


@app.post("/api/backtest/monte_carlo")
async def run_monte_carlo_endpoint(data: dict, eng: UserEngine = Depends(get_user_engine)):
    """
    Monte Carlo simulation on a completed trade list.
    Accepts the raw trades array from a previous backtest result — no re-running needed.
    Returns overfitting risk classification, Sharpe distribution, and fan chart data.
    """
    if not isinstance(data, dict):
        raise HTTPException(status_code=400, detail="Request body must be an object.")

    trades = data.get("trades", [])
    if not isinstance(trades, list):
        raise HTTPException(status_code=400, detail="trades must be a list.")

    from monte_carlo import MAX_MONTE_CARLO_SIMULATIONS, MAX_MONTE_CARLO_TRADES, run_monte_carlo

    if len(trades) > MAX_MONTE_CARLO_TRADES:
        raise HTTPException(
            status_code=413,
            detail=f"Too many trades; the maximum is {MAX_MONTE_CARLO_TRADES}."
        )
    if any(not isinstance(trade, dict) for trade in trades):
        raise HTTPException(status_code=400, detail="Each trade must be an object.")

    try:
        capital = float(data.get("capital", 1000.0))
        n_simulations = int(data.get("n_simulations", 1000))
    except (TypeError, ValueError):
        raise HTTPException(status_code=400, detail="capital and n_simulations must be numeric.")

    if not trades:
        return {"status": "error", "message": "No trades provided for Monte Carlo simulation."}
    if len(trades) < 3:
        return {"status": "error", "message": "Need at least 3 trades to run Monte Carlo simulation."}
    if n_simulations < 1:
        raise HTTPException(status_code=400, detail="n_simulations must be at least 1.")

    # Cap simulations for safety
    n_simulations = min(n_simulations, MAX_MONTE_CARLO_SIMULATIONS)

    try:
        try:
            await asyncio.wait_for(
                _monte_carlo_semaphore.acquire(),
                timeout=_MONTE_CARLO_QUEUE_TIMEOUT_SECONDS,
            )
        except asyncio.TimeoutError:
            raise HTTPException(status_code=429, detail="Monte Carlo capacity is temporarily busy; retry shortly.")

        try:
            result = await asyncio.to_thread(run_monte_carlo, trades, capital, n_simulations)
        finally:
            _monte_carlo_semaphore.release()
        if "error" in result:
            return {"status": "error", "message": result["error"]}
        return {"status": "success", "result": result}
    except HTTPException:
        raise
    except Exception as e:
        print(f"[api] Monte Carlo error: {_safe_exception_type(e)}")
        return {"status": "error", "message": "An error occurred during Monte Carlo simulation."}


@app.post("/api/backtest/deploy")
async def deploy_backtest_as_bot(data: dict, eng: UserEngine = Depends(get_user_engine)):
    """
    One-click deploy: Converts a completed backtest's settings into a live bot.
    deploy_mode: "override" (keep existing position) or "liquidate" (close & redeploy)
    """
    symbol = data.get("ticker", "").upper().strip()
    if not symbol:
        return {"status": "error", "message": "Symbol is required"}
    deploy_mode = data.get("deploy_mode", "override")  # "override" | "liquidate"
    is_options = data.get("is_options", False)
    existing_settings = eng.config.TICKER_SETTINGS.get(symbol, {}) or {}
    is_options = bool(
        is_options
        or existing_settings.get("options_trading")
        or existing_settings.get("is_option")
    )

    # Handle liquidation if requested and position exists.  Do not remove the
    # bot or redeploy until broker cancellation/liquidation is confirmed.
    import allocation_executor
    if deploy_mode == "liquidate" and symbol in eng.config.TRADELIST and allocation_executor.is_allocation_bot(existing_settings):
        res = await allocation_executor.liquidate_legs(eng, symbol, existing_settings)
        if not res.get("success"):
            return {"status": "pending", "message": "Redeployment paused until every ETF leg sale is confirmed filled.", "broker": res}
        eng.config.TRADELIST.remove(symbol)
        await eng.save_settings()
    elif deploy_mode == "liquidate" and symbol in eng.config.TRADELIST:
        cancel_result = eng.broker.cancel_orders_for_ticker(symbol)
        if not cancel_result.get("success", False):
            return {
                "status": "pending",
                "message": "Redeployment paused because broker order cancellation was not confirmed.",
                "broker": cancel_result,
            }
        if is_options:
            try:
                from option_selector import parse_occ_symbol
                positions = await asyncio.wait_for(
                    asyncio.to_thread(eng.broker.get_positions),
                    timeout=3.0,
                )
                option_position = next(
                    (
                        position for position in (positions or [])
                        if (
                            parse_occ_symbol(
                                str(position.get("symbol", "")).replace("/", "").upper()
                            )
                            or {}
                        ).get("underlying") == symbol.replace("/", "").upper()
                    ),
                    None,
                )
            except Exception as exc:
                return {
                    "status": "pending",
                    "message": "Redeployment paused because the option position could not be reconciled.",
                    "broker": {"success": False, "error": type(exc).__name__},
                }
            if option_position:
                close_result = await asyncio.wait_for(
                    asyncio.to_thread(
                        eng._close_option_with_limit,
                        option_position,
                        ticker=symbol,
                        result={"level": "redeploy"},
                        t_settings=existing_settings,
                    ),
                    timeout=10.0,
                )
            else:
                close_result = {"success": True, "status": "no_position"}
        else:
            close_result = await asyncio.wait_for(
                asyncio.to_thread(eng.broker.close_position, symbol),
                timeout=10.0,
            )
        no_position = "no position" in str(close_result.get("error", "")).lower()
        close_status = str(close_result.get("status") or "").lower().replace(" ", "_")
        confirmed = bool(getattr(eng.broker, "simulation_mode", False)) or close_status == "filled" or no_position
        if not close_result.get("success", False) and not no_position:
            return {
                "status": "pending",
                "message": "Redeployment paused because liquidation failed or has an unknown broker outcome.",
                "broker": close_result,
            }
        if close_result.get("success", False) and not confirmed:
            return {
                "status": "pending",
                "message": "Redeployment paused until the submitted liquidation is confirmed filled.",
                "broker": close_result,
            }
        eng.config.TRADELIST.remove(symbol)
        await eng.save_settings()

    # Build create_bot payload
    deploy_payload = {
        "symbol": symbol,
        "timeframe": data.get("timeframe"),
        "capital": data.get("trade_amount", data.get("capital")),
        "amount": data.get("trade_amount", data.get("capital")),
        "allocation_mode": data.get("allocation_mode"),
        "threshold": data.get("threshold"),
        "sell_threshold": data.get("sell_threshold"),
        "indicators": strip_removed(data.get("indicators", []))[0],
        "add_to_watchlist": True,
        "buy_mode": data.get("buy_mode"),
        "custom_buy_price": data.get("custom_buy_price"),
        "market_session": data.get("market_session"),
        "swing_entry_mode": data.get("swing_entry_mode"),
        "swing_entry_lookback_days": data.get("swing_entry_lookback_days"),
        "swing_entry_lookback_mode": data.get("swing_entry_lookback_mode"),
        "entry_atr_multiplier": data.get("entry_atr_multiplier"),
        "entry_pct_move": data.get("entry_pct_move"),
        "entry_atr_style": data.get("entry_atr_style"),
        "entry_pct_style": data.get("entry_pct_style"),
        "swing_exit_lookback_days": data.get("swing_exit_lookback_days"),
        "swing_exit_lookback_mode": data.get("swing_exit_lookback_mode"),
    }

    if is_options:
        options_exit_strategy = data.get("sell_mode", data.get("options_exit_strategy", data.get("exit_strategy")))
        try:
            deploy_options_type = validate_options_type(data.get("options_type", "calls_only"))
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc))
        deploy_payload.update({
            "options_trading": True,
            "options_type": deploy_options_type,
            "options_trade_amount": data.get("trade_amount"),
            "options_expiry_days": data.get("expiry_days"),
            "options_strike_type": data.get("strike_type"),
            "sell_mode": options_exit_strategy,
            "options_exit_strategy": options_exit_strategy,
            "options_stop_loss_pct": data.get("stop_loss_pct"),
            "options_take_profit_pct": data.get("take_profit_pct"),
            "options_trailing_pct": data.get("options_trailing_pct", data.get("trailing_pct")),
        })
    else:
        deploy_payload.update({
            "strategy_preset_enabled": data.get("strategy_preset_enabled"),
            "strategy_preset": data.get("strategy_preset"),
            "strategy_preset_variant": data.get("strategy_preset_variant"),
            "strategy_preset_position_policy": data.get("strategy_preset_position_policy"),
            **{k: data.get(k) for k in _ALLOCATION_SETTING_KEYS},
            **{k: data.get(k) for k in _SESSION_SETTING_KEYS},
            "scan_interval_auto": data.get("scan_interval_auto"),
            "scan_interval_seconds": data.get("scan_interval_seconds"),
            "sell_mode": data.get("sell_mode"),
            "risk_per_trade": min(max(float(data.get("risk_per_trade", 0.5)), 0.0), 0.5),
            "max_position_pct": data.get("max_position_pct"),
            "atr_stop_multiplier": data.get("atr_stop_multiplier"),
            "atr_trail_multiplier": data.get("atr_trail_multiplier"),
            "take_profit_multiplier": data.get("atr_take_profit_multiplier"),
            "regular_stop_loss_pct": data.get("regular_stop_loss_pct"),
            "regular_take_profit_pct": data.get("regular_take_profit_pct"),
            "regular_trailing_pct": data.get("regular_trailing_pct"),
            "enable_pct_trail": data.get("enable_pct_trail"),
            "custom_stop_loss_price": data.get("custom_stop_loss_price"),
            "custom_take_profit_price": data.get("custom_take_profit_price"),
            "swing_exit_lookback_days": data.get("swing_exit_lookback_days"),
            "swing_exit_lookback_mode": data.get("swing_exit_lookback_mode"),
            "enable_signal_reset": data.get("enable_signal_reset"),
            "enable_smart_reentry": data.get("enable_smart_reentry"),
            "smart_reentry_type": data.get("smart_reentry_type"),
            "smart_reentry_rsi_call_max": data.get("smart_reentry_rsi_call_max"),
            "smart_reentry_rsi_put_min": data.get("smart_reentry_rsi_put_min"),
            "smart_reentry_vwap_call_max_dist_pct": data.get("smart_reentry_vwap_call_max_dist_pct"),
            "smart_reentry_vwap_put_max_dist_pct": data.get("smart_reentry_vwap_put_max_dist_pct"),
        })
        for grid_key in _GRID_CONFIGURATION_KEYS:
            if grid_key in data:
                deploy_payload[grid_key] = data.get(grid_key)
        if "ai_autopilot" in data:
            deploy_payload["ai_autopilot"] = data.get("ai_autopilot")
            deploy_payload["ai_interval"] = data.get("ai_interval")
            deploy_payload["ai_model"] = data.get("ai_model")

    if "enable_cooldown" in data:
        deploy_payload["enable_cooldown"] = data.get("enable_cooldown")
    if "cooldown_minutes" in data:
        deploy_payload["cooldown_minutes"] = data.get("cooldown_minutes")

    # Call create_bot to initialize/update ticker
    res = await create_bot(deploy_payload, eng)
    if res.get("status") == "success":
        # Store indicator overrides and source badge (per-bot snapshot)
        if symbol not in eng.config.TICKER_SETTINGS:
            eng.config.TICKER_SETTINGS[symbol] = {}

        # Tag as backtest source
        eng.config.TICKER_SETTINGS[symbol]["source"] = "backtest"

        indicator_overrides = data.get("indicator_overrides", {})
        if indicator_overrides:
            eng.config.TICKER_SETTINGS[symbol]["indicator_overrides"] = indicator_overrides

        # Robust sync fix: Ensure symbol is strictly in the tradelist memory
        if symbol not in eng.config.TRADELIST:
            eng.config.TRADELIST.insert(0, symbol)

        await eng.save_settings()
        res["settings"] = eng.config.TICKER_SETTINGS[symbol]

    return res


@app.get("/api/options/chain")
async def get_options_chain_api(ticker: str, expiry_days: int = 7, current_price: float = None, eng: UserEngine = Depends(get_user_engine)):
    """Fetches the options contract chain for a ticker from Alpaca."""
    if not re.fullmatch(r"[A-Z0-9.\-]{1,15}", ticker.upper()):
        return {"error": "Invalid ticker format"}
    try:
        expiry_days = int(expiry_days)
    except (TypeError, ValueError):
        raise HTTPException(status_code=400, detail="expiry_days must be a whole number")
    if not 0 <= expiry_days <= 365:
        raise HTTPException(status_code=413, detail="expiry_days must be between 0 and 365")
    if current_price is not None:
        try:
            current_price = float(current_price)
        except (TypeError, ValueError):
            raise HTTPException(status_code=400, detail="current_price must be numeric")
        if not math.isfinite(current_price) or current_price <= 0:
            raise HTTPException(status_code=400, detail="current_price must be finite and positive")

    from option_selector import get_option_chain
    try:
        chain = await _run_bounded_provider_call(
            eng.uid,
            "options-chain",
            lambda: get_option_chain(
                underlying=ticker,
                expiration_limit_days=expiry_days,
                current_price=current_price,
                client=getattr(eng.broker, "client", None),
            ),
        )
    except asyncio.TimeoutError:
        raise HTTPException(status_code=504, detail="Option-chain provider timed out; retry shortly.")
    except HTTPException:
        raise
    except Exception:
        raise HTTPException(status_code=502, detail="Option-chain provider is temporarily unavailable.")
    return {"status": "success", "chain": chain}


@app.get("/api/options/review")
async def get_options_review(review_date: str = None, eng: UserEngine = Depends(get_user_engine)):
    """Returns today's option fills and current Alpaca quotes/snapshots for trade review."""
    if review_date:
        try:
            review_day = datetime.strptime(review_date, "%Y-%m-%d").date()
        except ValueError:
            raise HTTPException(status_code=400, detail="review_date must be YYYY-MM-DD")
    else:
        review_day = get_now().date()

    try:
        order_history, positions = await asyncio.wait_for(
            asyncio.gather(
                asyncio.to_thread(eng.broker.get_order_history, eng.trade_log, 250),
                asyncio.to_thread(eng.broker.get_positions),
            ),
            timeout=8.0
        )
    except asyncio.TimeoutError:
        raise HTTPException(status_code=504, detail="Timed out loading option trade history")
    except Exception:
        raise HTTPException(status_code=500, detail="Could not load option trade history")

    today_option_orders = []
    for order in order_history or []:
        symbol = _normalize_quote_symbol(order.get("symbol", ""))
        if not _is_option_occ_symbol(symbol):
            continue

        ts = _parse_iso_datetime(order.get("filled_at") or order.get("created_at"))
        if ts and ts.date() == review_day:
            today_option_orders.append(order)

    open_option_positions = []
    for position in positions or []:
        symbol = _normalize_quote_symbol(position.get("symbol", ""))
        if _is_option_occ_symbol(symbol) or position.get("is_option"):
            open_option_positions.append(position)

    quote_symbols = list(dict.fromkeys(
        [
            _normalize_quote_symbol(order.get("symbol", ""))
            for order in today_option_orders
            if _is_option_occ_symbol(order.get("symbol", ""))
        ] + [
            _normalize_quote_symbol(position.get("symbol", ""))
            for position in open_option_positions
            if _is_option_occ_symbol(position.get("symbol", ""))
        ]
    ))

    snapshots, quote_feed, quote_error = await asyncio.to_thread(_fetch_alpaca_option_snapshots, eng, quote_symbols)
    serialized_snapshots = {}
    for sym, snapshot in (snapshots or {}).items():
        serialized = _serialize_option_snapshot(snapshot)
        serialized["quote_feed"] = quote_feed if quote_feed != "unavailable" else None
        serialized_snapshots[_normalize_quote_symbol(sym)] = serialized

    today_rows = []
    realized_today = 0.0
    for order in today_option_orders:
        symbol = _normalize_quote_symbol(order.get("symbol", ""))
        row = _build_option_review_row(order, serialized_snapshots.get(symbol, {}), review_day)
        today_rows.append(row)
        if row.get("realized_pl") is not None:
            try:
                realized_today += float(row["realized_pl"])
            except (TypeError, ValueError):
                pass

    open_rows = []
    for position in open_option_positions:
        symbol = _normalize_quote_symbol(position.get("symbol", ""))
        parsed = None
        try:
            from option_selector import parse_occ_symbol
            parsed = parse_occ_symbol(symbol)
        except Exception:
            parsed = None

        snap = serialized_snapshots.get(symbol, {})
        quote = snap.get("quote", {})
        mid = quote.get("mid")
        avg_price = _float_or_none(position.get("avg_price")) or 0.0
        qty = _float_or_none(position.get("qty")) or 0.0
        cost_basis = _float_or_none(position.get("cost_basis"))
        if cost_basis is None and avg_price and qty:
            cost_basis = avg_price * qty * 100.0

        current_value = None
        if mid is not None and qty > 0:
            current_value = round(mid * qty * 100.0, 2)
        elif _float_or_none(position.get("market_value")) is not None:
            current_value = round(float(position.get("market_value")), 2)

        unrealized_pl = None
        unrealized_pl_pct = None
        if current_value is not None and cost_basis is not None:
            unrealized_pl = round(current_value - cost_basis, 2)
            unrealized_pl_pct = round(((current_value - cost_basis) / cost_basis) * 100.0, 2) if cost_basis > 0 else None

        open_rows.append({
            "symbol": symbol,
            "underlying": parsed["underlying"] if parsed else None,
            "expiration": parsed["expiration"].isoformat() if parsed else None,
            "option_type": parsed["type"] if parsed else None,
            "strike": parsed["strike"] if parsed else None,
            "qty": qty,
            "avg_price": avg_price,
            "current_quote": quote,
            "latest_trade": snap.get("latest_trade", {}),
            "greeks": snap.get("greeks", {}),
            "quote_feed": snap.get("quote_feed"),
            "market_value": current_value,
            "cost_basis": cost_basis,
            "unrealized_pl": unrealized_pl,
            "unrealized_pl_pct": unrealized_pl_pct,
            "days_to_expiry": (parsed["expiration"] - review_day).days if parsed else None,
        })

    today_rows.sort(key=lambda row: row.get("filled_at") or row.get("created_at") or "", reverse=True)
    open_rows.sort(key=lambda row: row.get("symbol", ""))

    summary = {
        "review_date": review_day.isoformat(),
        "today_option_orders": len(today_rows),
        "open_option_positions": len(open_rows),
        "tracked_contracts": len(serialized_snapshots),
        "quote_feed": quote_feed,
        "quote_error": quote_error or None,
        "today_realized_pl": round(realized_today, 2),
    }

    return {
        "status": "success",
        "summary": summary,
        "today_orders": today_rows,
        "open_positions": open_rows,
    }


@app.post("/api/download_all")
async def download_all_data(data: dict, eng: UserEngine = Depends(get_user_engine)):
    """Downloads and caches all available history for a ticker across all timeframes."""
    raw_ticker = str(data.get("ticker", "")).strip().upper()
    if not raw_ticker:
        return {"error": "Ticker required"}
    if not re.fullmatch(r"[A-Z0-9.\-]{1,15}", raw_ticker):
        return {"error": "Invalid ticker format"}
    ticker = raw_ticker

    timeframes = [
        ("30Sec", 7),
        ("1Min", 7),
        ("2Min", 14),
        ("3Min", 14),
        ("5Min", 60),
        ("10Min", 60),
        ("15Min", 60),
        ("30Min", 60),
        ("1Hour", 365),
        ("2Hour", 365),
        ("4Hour", 730),
        ("1Day", 1825)
    ]

    log = []
    end_date = get_now()
    download_deadline = time.monotonic() + 120.0

    # 1. Download Price History
    for tf, days in timeframes:
        if time.monotonic() >= download_deadline:
            raise HTTPException(status_code=504, detail="Historical download exceeded its time budget")
        start_date = end_date - timedelta(days=days)
        async with _bounded_analysis_work(eng.uid, "historical download"):
            df = await asyncio.wait_for(
                asyncio.to_thread(get_historical_data, ticker, tf, start_date, end_date),
                timeout=min(20.0, max(1.0, download_deadline - time.monotonic())),
            )
        status = "Success" if df is not None and not df.empty else "No Data"
        log.append(f"{tf}: {status}")

    # 2. Download Metadata
    try:
        async with _bounded_analysis_work(eng.uid, "historical metadata download"):
            info = await asyncio.wait_for(
                asyncio.to_thread(lambda: yf.Ticker(ticker).info),
                timeout=min(15.0, max(1.0, download_deadline - time.monotonic())),
            )

        clean_ticker = re.sub(r'[^A-Z0-9]', '', ticker)
        safe_ticker = os.path.basename(clean_ticker)
        data_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), "data"))
        if not os.path.exists(data_dir):
            os.makedirs(data_dir)

        info_path = os.path.abspath(os.path.join(data_dir, f"{safe_ticker}_info.json"))
        if not info_path.startswith(data_dir + os.sep):
            return {"error": "Invalid ticker path"}

        with open(info_path, "w") as f:
            json.dump(info, f, indent=4)
        log.append("Metadata: Saved")
    except Exception as e:
        print(f"[api] Metadata extraction error: {_safe_exception_type(e)}")
        log.append("Metadata Error: Could not save metadata securely")

    return {"ticker": ticker, "status": log}


@app.get("/api/settings/dashboard-indicator-workspace")
def get_dashboard_indicator_workspace(eng: UserEngine = Depends(get_user_engine)):
    """Return the authoritative Dashboard regular-indicator workspace."""
    from user_config import active_evaluation_context

    token = active_evaluation_context.set("dashboard")
    try:
        return _dashboard_indicator_workspace_payload(eng)
    finally:
        active_evaluation_context.reset(token)


@app.post("/api/settings/dashboard-indicator-workspace")
async def update_dashboard_indicator_workspace(
    workspace: dict,
    eng: UserEngine = Depends(get_user_engine),
):
    """Atomically save the Dashboard grid, enabled subset, and favorites."""
    required_fields = ("displayed_indicators", "enabled_indicators", "favorites")
    missing = [field for field in required_fields if not isinstance(workspace.get(field), list)]
    if missing:
        raise HTTPException(
            status_code=422,
            detail=f"Dashboard indicator workspace requires list fields: {', '.join(missing)}",
        )

    raw_lists = {field: workspace.get(field, []) for field in required_fields}
    unknown = sorted({
        str(value or "").strip()
        for values in raw_lists.values()
        for value in values
        if str(value or "").strip() not in DASHBOARD_STANDARD_INDICATOR_SET
    })
    if unknown:
        raise HTTPException(
            status_code=422,
            detail=f"Unknown standard indicator(s): {', '.join(unknown)}",
        )

    displayed = _unique_standard_indicator_names(raw_lists["displayed_indicators"])
    if not 1 <= len(displayed) <= DASHBOARD_INDICATOR_DISPLAY_LIMIT:
        raise HTTPException(
            status_code=422,
            detail=f"Dashboard must display between 1 and {DASHBOARD_INDICATOR_DISPLAY_LIMIT} regular indicators",
        )

    requested_enabled = _unique_standard_indicator_names(raw_lists["enabled_indicators"])
    enabled_outside_grid = [name for name in requested_enabled if name not in displayed]
    if enabled_outside_grid:
        raise HTTPException(
            status_code=422,
            detail=f"Enabled indicators must be displayed first: {', '.join(enabled_outside_grid)}",
        )
    enabled_set = set(requested_enabled)
    enabled = [name for name in displayed if name in enabled_set]
    favorites = _unique_standard_indicator_names(raw_lists["favorites"])

    normalized = {
        "version": 1,
        "displayed_indicators": displayed,
        "enabled_indicators": enabled,
        "favorites": favorites,
    }

    from user_config import active_evaluation_context

    token = active_evaluation_context.set("dashboard")
    try:
        previous_enabled = {
            name for name in STANDARD_INDICATOR_NAMES
            if bool(eng.config.active_toggles.get(DASHBOARD_SIGNAL_TO_TOGGLE.get(name), False))
        }
        eng.config.dashboard_indicator_workspace = normalized
        enabled_set = set(enabled)
        for indicator_name in STANDARD_INDICATOR_NAMES:
            toggle_key = DASHBOARD_SIGNAL_TO_TOGGLE.get(indicator_name)
            if toggle_key:
                eng.config.set(toggle_key, indicator_name in enabled_set)
    finally:
        active_evaluation_context.reset(token)

    calculation_changed = previous_enabled != enabled_set
    _queue_settings_save(eng, "dashboard indicator workspace")
    if calculation_changed:
        clear_evaluation_cache()
        clear_ai_cache()
        _clear_chart_analysis_cache(None, None)
        if eng.force_scan_trigger:
            eng.force_scan_trigger.set()

    return {
        "status": "success",
        "save": "queued",
        "configured": True,
        "calculation_changed": calculation_changed,
        **normalized,
    }


@app.get("/api/settings/indicators")
def get_indicator_settings(context: str = "dashboard", eng: UserEngine = Depends(get_user_engine)):
    """Returns all indicator toggles grouped by category."""
    from user_config import active_evaluation_context
    ctx = _normalize_analysis_context(context)
    token = active_evaluation_context.set(ctx)
    try:
        settings_payload = {
        "Momentum": {
            "ENABLE_RSI": {"label": "RSI", "description": "Relative Strength Index — Overbought / Oversold", "enabled": getattr(eng.config, "ENABLE_RSI", True)},
            "ENABLE_MACD": {"label": "MACD", "description": "Moving Average Convergence Divergence", "enabled": getattr(eng.config, "ENABLE_MACD", True)},
        },
        "Trend": {
            "ENABLE_EMA": {"label": "EMA", "description": "Configurable one-line price filter or two-line EMA crossover", "enabled": getattr(eng.config, "ENABLE_EMA", True)},
            "ENABLE_SUPERTREND": {"label": "Supertrend", "description": "Supertrend Indicator (10, 3)", "enabled": getattr(eng.config, "ENABLE_SUPERTREND", True)},
            "ENABLE_BOLLINGER": {"label": "Bollinger", "description": "Bollinger Bands (20, 2σ)", "enabled": getattr(eng.config, "ENABLE_BOLLINGER", True)},
            "ENABLE_ADX_TREND": {"label": "ADX Trend", "description": "Wilder's ADX (14-period) Trend Strength Filter", "enabled": getattr(eng.config, "ENABLE_ADX_TREND", True)},
            "ENABLE_SMA": {"label": "SMA", "description": "Configurable one-line price filter or two-line SMA crossover", "enabled": getattr(eng.config, "ENABLE_SMA", True)},
            "ENABLE_DAILY_HIGH_LOW": {"label": "Prior Day High/Low", "description": "Uses the previous completed trading session's high and low as breakout/breakdown levels. Buffer is entered as percent points.", "enabled": getattr(eng.config, "ENABLE_DAILY_HIGH_LOW", True)},
            "ENABLE_PIVOT_POINTS": {"label": "Pivot Points (S/R)", "description": "Classic prior-session pivot, support, and resistance levels. Bullish on support/pivot reclaim; bearish on resistance rejection or pivot breakdown.", "enabled": getattr(eng.config, "ENABLE_PIVOT_POINTS", True)},
            "ENABLE_SWING_POINTS": {"label": "Swing Points", "description": "Rolling swing support/resistance clusters. Bullish on support bounce or resistance reclaim; bearish on resistance rejection or support loss.", "enabled": getattr(eng.config, "ENABLE_SWING_POINTS", True)},
        },
        "Volume": {
            "ENABLE_VWAP": {"label": "AVWAP", "description": "Anchored Volume Weighted Average Price", "enabled": getattr(eng.config, "ENABLE_VWAP", True)},
            "ENABLE_VOLUME_PROFILE": {"label": "Volume Profile", "description": "Prior-session POC/VAH/VAL volume-at-price levels. Bullish on POC reclaim/value acceptance; bearish on POC loss/value rejection.", "enabled": getattr(eng.config, "ENABLE_VOLUME_PROFILE", True)},
        },
        "Custom": {
            "ENABLE_MYSTIC_PULSE": {"label": "Mystic Pulse", "description": "DMI-based Consecutive Trend Strength", "enabled": getattr(eng.config, "ENABLE_MYSTIC_PULSE", True)},
            "ENABLE_CANDLE_PATTERNS": {"label": "Candle Patterns", "description": "Engulfing, Hammer, Shooting Star patterns", "enabled": getattr(eng.config, "ENABLE_CANDLE_PATTERNS", True)},
        },
        "Premium": {
        },
        }
        settings_payload["Classical Extensions"] = {
            toggle_key: {
                "label": indicator_name,
                "description": "Standard OHLCV quantitative indicator with configurable industry defaults.",
                "enabled": getattr(eng.config, toggle_key, False),
            }
            for indicator_name, toggle_key in EXTENSION_INDICATOR_TOGGLE_KEYS.items()
        }
        return settings_payload
    finally:
        active_evaluation_context.reset(token)


@app.post("/api/settings/risk")
async def update_risk_settings(settings: dict, context: str = "dashboard", eng: UserEngine = Depends(get_user_engine)):
    """Updates risk management parameters."""
    from user_config import active_evaluation_context
    ctx = _normalize_analysis_context(context)
    allowed_risk_keys = {"MAX_DAILY_DRAWDOWN", "RISK_PER_TRADE", "MAX_POSITION_PCT", "GLOBAL_HARD_STOP_PCT"}
    unknown_keys = sorted(set(settings or {}) - allowed_risk_keys)
    if unknown_keys:
        raise HTTPException(status_code=400, detail=f"Unsupported risk setting(s): {', '.join(unknown_keys)}")
    token = active_evaluation_context.set(ctx)
    try:
        normalized_settings = {}
        for key, value in settings.items():
            if key in ['MAX_DAILY_DRAWDOWN', 'RISK_PER_TRADE', 'MAX_POSITION_PCT', 'GLOBAL_HARD_STOP_PCT']:
                if value is not None:
                    try:
                        value = float(value)
                    except (TypeError, ValueError):
                        raise HTTPException(status_code=400, detail=f"{key} must be numeric or null")
                    if not math.isfinite(value):
                        raise HTTPException(status_code=400, detail=f"{key} must be finite")
                    if value < 0:
                        raise HTTPException(status_code=400, detail=f"{key} cannot be negative")
                    if value > 1:
                        value = value / 100.0
                    if value > 1:
                        raise HTTPException(status_code=400, detail=f"{key} cannot exceed 100%")
            eng.config.set(key, value)
            normalized_settings[key] = value
    finally:
        active_evaluation_context.reset(token)

    await eng.save_settings()
    if ctx == "bots":
        # Re-evaluate account risk immediately when the user tightens a
        # global limit instead of waiting for the next 10s/60s bot interval.
        eng.force_scan_trigger.set()
    print(f"[settings] Updated risk parameters ({ctx}) for {eng.uid}: {', '.join(settings.keys())}")
    return {"status": "success", "settings": normalized_settings}


@app.post("/api/settings/risk/reactivate")
async def reactivate_global_risk(eng: UserEngine = Depends(get_user_engine)):
    """Manually reactivate account-wide bot entries after a global risk halt."""
    from trader import get_risk_manager
    from user_config import active_evaluation_context

    token = active_evaluation_context.set("bots")
    try:
        try:
            account = await asyncio.wait_for(asyncio.to_thread(eng.broker.get_account_info), timeout=3.0)
        except Exception as exc:
            raise HTTPException(status_code=503, detail=f"Current account equity is unavailable: {type(exc).__name__}")
        equity = float((account or {}).get("equity", 0.0) or 0.0)
        if equity <= 0:
            raise HTTPException(status_code=503, detail="Current account equity is unavailable; global risk cannot be reactivated safely.")

        status = get_risk_manager().reactivate(
            equity,
            account_key=(account or {}).get("_risk_account_key"),
        )
        eng.last_global_risk_signature = None
        eng.last_global_risk_save_ts = 0
        eng.last_global_halt_cancel_event_id = None
        await eng.save_settings()
        eng.force_scan_trigger.set()
        individually_paused = sum(
            1 for ticker in eng.config.TRADELIST
            if eng.config.TICKER_SETTINGS.get(ticker, {}).get("paused", False)
        )
        return {
            "status": "success",
            "message": "Global risk halt cleared. Eligible bots can evaluate new entries again.",
            "global_risk_status": status,
            "individually_paused_bots": individually_paused,
        }
    finally:
        active_evaluation_context.reset(token)


@app.post("/api/settings/ticker_amount")
async def update_ticker_amount(data: dict, eng: UserEngine = Depends(get_user_engine)):
    """Updates the allocated trade amount for a specific ticker."""
    ticker = data.get("ticker", "").upper()
    amount = data.get("amount")

    if ticker:
        if not re.fullmatch(r"[A-Z0-9.\-]{1,15}", ticker):
            raise HTTPException(status_code=400, detail="Invalid ticker format")
        if amount is None or amount == "":
            if ticker in eng.config.TICKER_AMOUNTS:
                del eng.config.TICKER_AMOUNTS[ticker]
        else:
            try:
                normalized_amount = float(amount)
                if not math.isfinite(normalized_amount) or not 0 < normalized_amount <= 1_000_000:
                    raise ValueError
                eng.config.TICKER_AMOUNTS[ticker] = normalized_amount
            except:
                return {"status": "error", "message": "Invalid amount"}

        await eng.save_settings()
        print(f"[settings] {ticker}: Allocated trade amount updated for {eng.uid}")
        return {"status": "success", "ticker_amounts": eng.config.TICKER_AMOUNTS}
    return {"status": "error", "message": "Ticker required"}


@app.post("/api/settings/timeframe")
async def update_timeframe(data: dict, eng: UserEngine = Depends(get_user_engine)):
    """Updates the default trading timeframe and triggers a re-scan."""
    new_tf = data.get("timeframe")
    if new_tf in ["30Sec", "1Min", "2Min", "3Min", "5Min", "10Min", "15Min", "30Min", "1Hour", "2Hour", "4Hour", "1Day"]:
        eng.config.DEFAULT_TIMEFRAME = new_tf
        eng.latest_scans = {
            ticker: scan for ticker, scan in eng.latest_scans.items()
            if scan.get('timeframe') == new_tf
        }
        eng.bot_scans = {
            ticker: scan for ticker, scan in eng.bot_scans.items()
            if scan.get('timeframe') == new_tf
        }
        eng.latest_scans_by_tf = {
            tf: scans for tf, scans in eng.latest_scans_by_tf.items()
            if tf == new_tf
        }
        eng.bot_scans_by_tf = {
            tf: scans for tf, scans in eng.bot_scans_by_tf.items()
            if tf == new_tf
        }
        await eng.save_settings()
        print(f"[settings] Global Timeframe synced to {new_tf} for {eng.uid}. Chart data will refresh on next dashboard request.")
        clear_evaluation_cache()
        clear_ai_cache()
        _clear_chart_analysis_cache()

        if eng.force_scan_trigger:
            eng.force_scan_trigger.set()

        return {"status": "success", "timeframe": new_tf}
    return {"status": "error", "message": "Invalid timeframe"}


@app.get("/api/watchlist")
def get_watchlist(eng: UserEngine = Depends(get_user_engine)):
    """Returns the current watchlist."""
    return eng.config.WATCHLIST


@app.post("/api/watchlist")
async def add_to_watchlist(data: dict, eng: UserEngine = Depends(get_user_engine)):
    """Add a ticker to the watchlist."""
    ticker = str(data.get("ticker", "")).upper().strip()
    if ticker and not re.fullmatch(r"[A-Z0-9.\-]{1,15}", ticker):
        raise HTTPException(status_code=400, detail="Invalid ticker format")
    if ticker and len(eng.config.WATCHLIST) >= MAX_WATCHLIST_ITEMS and ticker not in eng.config.WATCHLIST:
        raise HTTPException(status_code=413, detail=f"Watchlist limit is {MAX_WATCHLIST_ITEMS} symbols")
    if ticker and ticker not in eng.config.WATCHLIST:
        eng.config.WATCHLIST.append(ticker)
        await eng.save_settings()
        print(f"[settings] Added {ticker} to watchlist for {eng.uid}")
        clear_evaluation_cache(ticker, None)
        _clear_chart_analysis_cache(ticker, None)
    return {"status": "success", "watchlist": eng.config.WATCHLIST}


@app.delete("/api/watchlist/{ticker}")
async def remove_from_watchlist(ticker: str, eng: UserEngine = Depends(get_user_engine)):
    """Remove a ticker from the watchlist."""
    ticker = ticker.upper()
    if ticker in eng.config.WATCHLIST:
        eng.config.WATCHLIST.remove(ticker)
        print(f"[settings] Removed {ticker} from watchlist")
        await eng.save_settings()
        clear_evaluation_cache(ticker, None)
        _clear_chart_analysis_cache(ticker, None)
        if eng.force_scan_trigger:
            eng.force_scan_trigger.set()
    return {"status": "success", "watchlist": eng.config.WATCHLIST}


@app.get("/api/tradelist")
def get_tradelist(eng: UserEngine = Depends(get_user_engine)):
    """Returns the current active trade list."""
    return eng.config.TRADELIST


@app.post("/api/tradelist")
async def add_to_tradelist(data: dict, eng: UserEngine = Depends(get_user_engine)):
    """Add a ticker to the active trade list."""
    ticker = str(data.get("ticker", "")).upper().strip()
    if not re.fullmatch(r"[A-Z0-9.\-]{1,15}", ticker):
        raise HTTPException(status_code=400, detail="Invalid ticker format")
    if ticker not in eng.config.TRADELIST and len(eng.config.TRADELIST) >= MAX_ACTIVE_BOTS:
        raise HTTPException(status_code=413, detail=f"Active bot limit is {MAX_ACTIVE_BOTS} symbols")
    timeframe = data.get("timeframe", eng.config.DEFAULT_TIMEFRAME)

    if ticker and ticker not in eng.config.TRADELIST:
        eng.config.TRADELIST.insert(0, ticker)

        if ticker not in eng.config.TICKER_SETTINGS:
            eng.config.TICKER_SETTINGS[ticker] = {}
        eng.config.TICKER_SETTINGS[ticker]['timeframe'] = timeframe
        eng.config.TICKER_SETTINGS[ticker]['market_session'] = _normalize_market_session_for_symbol(
            ticker,
            eng.config.TICKER_SETTINGS[ticker].get('market_session')
        )
        print(f"[settings] Activated {ticker} on locked {timeframe} timeframe")

        await eng.save_settings()
        clear_evaluation_cache(ticker, None)
        _clear_chart_analysis_cache(ticker, None)
        if eng.force_scan_trigger:
            eng.force_scan_trigger.set()
    return {"status": "success", "tradelist": eng.config.TRADELIST, "watchlist": eng.config.WATCHLIST}


@app.get("/api/assets/session_eligibility/{symbol}")
def get_asset_session_eligibility(symbol: str, market_session: str = Query("Regular"), eng: UserEngine = Depends(get_user_engine)):
    """Return whether Alpaca allows the selected market session for this asset."""
    symbol = symbol.upper().strip()
    if not symbol:
        return {"status": "error", "allowed": False, "message": "Symbol is required"}
    session = _normalize_market_session_for_symbol(symbol, market_session)
    check = eng.broker.check_market_session_allowed(symbol, session)
    return {
        "status": "success" if check.get("success") else "error",
        "allowed": bool(check.get("allowed")),
        "symbol": check.get("symbol", symbol),
        "market_session": session,
        "overnight_tradable": bool(check.get("overnight_tradable", False)),
        "message": check.get("message") or check.get("error") or "",
    }


@app.get("/api/debug/history")
async def debug_history(eng: UserEngine = Depends(get_user_engine)):
    return {
        "executed_trades_count": 0,
        "trade_log_count": len(eng.trade_log),
        "executed_trades": [],
        "eng.trade_log": eng.trade_log[:10]
    }

@app.delete("/api/tradelist/{ticker}")
async def remove_from_tradelist(ticker: str, liquidate: bool = False, eng: UserEngine = Depends(get_user_engine)):
    """Remove a ticker from the active trade list."""
    ticker = ticker.upper()
    import allocation_executor
    alloc_settings = eng.config.TICKER_SETTINGS.get(ticker) or {}
    if allocation_executor.is_allocation_bot(alloc_settings):
        st = allocation_executor.state_of(alloc_settings)
        if st["pending"]:
            return {"status": "pending", "message": f"Bot remains active: {', '.join(st['pending'])} order(s) are still settling. Try again in a minute.",
                    "tradelist": eng.config.TRADELIST}
        if liquidate:
            res = await allocation_executor.liquidate_legs(eng, ticker, alloc_settings)
            print(f"[tradelist] Liquidating {ticker} legs: {res}")
            if not res.get("success"):
                return {"status": "pending", "message": "Bot remains active until every ETF leg sale is confirmed filled.",
                        "tradelist": eng.config.TRADELIST, "broker": res}
        if ticker in eng.config.TRADELIST:
            eng.config.TRADELIST.remove(ticker)
            await eng.save_settings()
            clear_evaluation_cache(ticker, None)
        return {"status": "success", "tradelist": eng.config.TRADELIST}
    res_cancel = eng.broker.cancel_orders_for_ticker(ticker)
    print(f"[tradelist] Cancelled pending orders for {ticker}: {res_cancel}")
    if not res_cancel.get('success', False):
        return {
            "status": "pending",
            "message": "Bot remains active because broker order cancellation was not confirmed.",
            "tradelist": eng.config.TRADELIST,
            "broker": res_cancel,
        }

    if liquidate:
        res = eng.broker.close_position(ticker)
        print(f"[tradelist] Liquidating {ticker}: {res}")
        no_position = "no position" in str(res.get("error", "")).lower()
        status = str(res.get("status") or "").lower().replace(" ", "_")
        confirmed = bool(getattr(eng.broker, "simulation_mode", False)) or status == "filled" or no_position
        if not res.get("success", False) and not no_position:
            return {
                "status": "pending",
                "message": "Bot remains active because liquidation failed or has an unknown broker outcome.",
                "tradelist": eng.config.TRADELIST,
                "broker": res,
            }
        if res.get("success", False) and not confirmed:
            return {
                "status": "pending",
                "message": "Liquidation was submitted but not confirmed; bot remains active until the fill is visible.",
                "tradelist": eng.config.TRADELIST,
                "broker": res,
            }

    if ticker in eng.config.TRADELIST:
        eng.config.TRADELIST.remove(ticker)
        removed_settings = eng.config.TICKER_SETTINGS.get(ticker)
        if isinstance(removed_settings, dict):
            removed_settings.pop("session_state", None)  # a re-created Session Rider bot starts clean
        await eng.save_settings()
        print(f"[settings] Removed {ticker} from active tradelist (Bot Deactivated) for {eng.uid}")
        clear_evaluation_cache(ticker, None)
        _clear_chart_analysis_cache(ticker, None)

    return {"status": "success", "tradelist": eng.config.TRADELIST}


@app.get("/api/search/{query}")
def search_symbols(query: str, eng: UserEngine = Depends(get_user_engine)):
    """Searches for tradeable assets using the modular eng.broker."""
    _enforce_symbol_search_rate_limit(eng.uid)
    if len(query) < 1:
        return []
    if len(query) > 64:
        raise HTTPException(status_code=400, detail="Search query is too long")
    return eng.broker.search_assets(query)


_COMPANY_NAME_CACHE: dict = {}  # symbol -> (expires_at, name, logo_url); these rarely change, so hits live a week
_COMPANY_NAME_HIT_TTL = 7 * 24 * 3600
_COMPANY_NAME_MISS_TTL = 600


def _finnhub_logo_url(symbol: str) -> str:
    """Company logo URL from Finnhub's profile (stocks only; ETFs and crypto usually have none). The API key stays on
    the server: only the public https image URL is returned. "" when unavailable."""
    api_key = str(getattr(global_config, "FINNHUB_API_KEY", "") or "").strip()
    if not api_key or not re.fullmatch(r"[A-Z0-9.\-]{1,10}", symbol):
        return ""
    try:
        import requests
        res = requests.get("https://finnhub.io/api/v1/stock/profile2", params={"symbol": symbol},
                           headers={"X-Finnhub-Token": api_key}, timeout=5)
        if res.status_code != 200:
            return ""
        logo = str((res.json() or {}).get("logo") or "").strip()
    except Exception:
        return ""
    host = (urlparse(logo).hostname or "").lower()
    return logo if logo.startswith("https://") and (host == "finnhub.io" or host.endswith(".finnhub.io")) else ""


@app.get("/api/company-name/{ticker}")
async def get_company_name(ticker: str, eng: UserEngine = Depends(get_user_engine)):
    """Display name and logo for a symbol (for example "Tesla, Inc." for TSLA). Informational only; "" when unknown."""
    clean = _normalize_quote_symbol(ticker)
    if not re.fullmatch(r"[A-Z0-9.\-]{1,15}", clean):
        raise HTTPException(status_code=400, detail="Invalid ticker format")
    now = time.time()
    cached = _COMPANY_NAME_CACHE.get(clean)
    if cached and cached[0] > now:
        return {"symbol": clean, "name": cached[1], "logo": cached[2]}
    name = ""
    try:
        matches = await asyncio.wait_for(asyncio.to_thread(eng.broker.search_assets, clean), timeout=6)
        for item in matches or []:
            if str(item.get("symbol", "")).upper() == clean:
                name = str(item.get("name") or "").strip()[:80]
                break
    except Exception:
        name = ""
    logo = ""
    if not clean.endswith(("USD", "USDT", "USDC")):
        try:
            logo = await asyncio.wait_for(asyncio.to_thread(_finnhub_logo_url, clean), timeout=6)
        except Exception:
            logo = ""
    if len(_COMPANY_NAME_CACHE) > 2000:
        _COMPANY_NAME_CACHE.clear()
    ttl = _COMPANY_NAME_HIT_TTL if (name or logo) else _COMPANY_NAME_MISS_TTL
    _COMPANY_NAME_CACHE[clean] = (now + ttl, name, logo)
    return {"symbol": clean, "name": name, "logo": logo}


@app.post("/api/bots/reorder")
async def reorder_bots(data: dict, eng: UserEngine = Depends(get_user_engine)):
    """Reorder the priority of active bots."""
    new_order = data.get("tradelist", [])
    if not new_order or not isinstance(new_order, list):
        return {"status": "error", "message": "Invalid tradelist format"}

    # Verify that all provided tickers are currently active bots, preserving order and removing duplicates
    valid_tickers = []
    for t in new_order:
        if t in eng.config.TRADELIST and t not in valid_tickers:
            valid_tickers.append(t)

    # Ensure any tickers currently active that weren't in the new order are appended to the end
    for t in eng.config.TRADELIST:
        if t not in valid_tickers:
            valid_tickers.append(t)

    eng.config.TRADELIST = valid_tickers
    await eng.save_settings()

    return {"status": "success", "tradelist": eng.config.TRADELIST}


FORCED_SCAN_ACCOUNT_TIMEOUT_SECONDS = 10.0
FORCED_SCAN_EVALUATION_TIMEOUT_SECONDS = 20.0


def _record_failed_forced_scan(eng: UserEngine, symbol: str, reason: str, timeframe: str, started, message: str) -> None:
    """Make a failed first scan visible on the card ("Initial scan failed, will retry") instead of leaving nothing.

    A newer real scan (the trading loop may have gotten there first) is never overwritten by the failure."""
    existing = eng.bot_scans.get(symbol)
    if isinstance(existing, dict) and (existing.get("forced_scan") or {}).get("status") != "failed":
        try:
            existing_at = _parse_iso_datetime(existing.get("time"))
            if existing_at is not None and existing_at >= started:
                return
        except Exception:
            pass
    settings = eng.config.TICKER_SETTINGS.get(symbol, {})
    now_iso = get_now().isoformat()
    eng._record_bot_scan({
        "time": now_iso, "ticker": symbol, "action": "HOLD", "option_intent": "HOLD",
        "reason": "Initial scan failed, will retry", "price": "$0.00", "price_raw": 0.0, "timeframe": timeframe,
        "signals": {}, "bullish_count": 0, "bearish_count": 0, "total_signals": 0, "evaluation_status": "failed",
        "buy_mode": settings.get("buy_mode"), "sell_mode": settings.get("sell_mode"),
        "forced_scan": {"reason": reason, "at": now_iso, "status": "failed", "message": message},
    })


async def _run_forced_bot_scan(eng: UserEngine, ticker: str, reason: str, timeframe: str = None):
    """Evaluate one bot once, right now, and store the result on its card tagged as a forced scan.

    Analysis only: this calls the same evaluation the trading loop uses but never places, cancels or changes an
    order. It runs whether the market is open or closed. Failure is recorded visibly, never silently dropped."""
    symbol = str(ticker or "").upper().strip()
    started = get_now()
    settings = eng.config.TICKER_SETTINGS.get(symbol, {})
    tf = timeframe or settings.get("timeframe") or eng.config.DEFAULT_TIMEFRAME
    try:
        is_crypto = _is_crypto_symbol(symbol)
        account = await asyncio.wait_for(asyncio.to_thread(eng.broker.get_account_info), timeout=FORCED_SCAN_ACCOUNT_TIMEOUT_SECONDS)
        avail_cash = account.get('non_marginable_buying_power') or account['cash'] if is_crypto else account['cash']
        scan = await asyncio.wait_for(
            asyncio.to_thread(
                evaluate_trade,
                symbol,
                account_equity=account['equity'],
                available_cash=avail_cash,
                timeframe=tf,
                use_bot_settings=True,
                ignore_cache=True,
            ),
            timeout=FORCED_SCAN_EVALUATION_TIMEOUT_SECONDS,
        )
        if not scan:
            raise RuntimeError("the evaluation returned no result")
        scan = dict(scan)
        scan["forced_scan"] = {"reason": reason, "at": get_now().isoformat(), "status": "ok"}
        eng._record_bot_scan(scan)
        positions = await asyncio.wait_for(asyncio.to_thread(eng.broker.get_positions), timeout=FORCED_SCAN_ACCOUNT_TIMEOUT_SECONDS)
        active_pos = _matching_position_for_ticker(positions, symbol, settings)
        _ensure_entry_forecast_snapshot(settings, scan, symbol)
        _ensure_exit_forecast_snapshot(settings, scan, active_pos, symbol)
        print(f"[forced-scan] {symbol} ({reason}) completed")
        return scan
    except Exception as exc:
        kind = _safe_exception_type(exc)
        print(f"[forced-scan] {symbol} ({reason}) failed: {kind}")
        _record_failed_forced_scan(eng, symbol, reason, tf, started, kind)
        return None


@app.post("/api/bots/create")
async def create_bot(data: dict, eng: UserEngine = Depends(get_user_engine)):
    """Creates a new active bot with custom settings (adds to watchlist, tradelist, and TICKER_SETTINGS)."""
    symbol = data.get("symbol", "").upper().strip()
    if not symbol:
        return {"status": "error", "message": "Symbol is required"}
    if not re.fullmatch(r"[A-Z0-9.\-]{1,15}", symbol):
        raise HTTPException(status_code=400, detail="Invalid symbol format")
    if symbol not in eng.config.TRADELIST and len(eng.config.TRADELIST) >= MAX_ACTIVE_BOTS:
        raise HTTPException(status_code=413, detail=f"Active bot limit is {MAX_ACTIVE_BOTS} symbols")
    _strip_strategy_preset_server_keys(data)
    existing_for_preset = eng.config.TICKER_SETTINGS.get(symbol, {})
    prior_for_preset = copy.deepcopy(existing_for_preset) if isinstance(existing_for_preset, dict) else {}
    # Validate a strategy preset before any state is written, so a rejected
    # request leaves nothing half-created.
    _validate_strategy_preset_request({**prior_for_preset, **{k: v for k, v in data.items() if v is not None}}, symbol)
    _validate_allocation_conflicts(eng, symbol, {**prior_for_preset, **{k: v for k, v in data.items() if v is not None}})
    _validate_bot_scan_interval({**prior_for_preset, **{k: v for k, v in data.items() if v is not None}})
    if "enable_risk_management" in data and data.get("enable_risk_management") is not None:
        data["enable_risk_management"] = _normalize_risk_management_setting(data["enable_risk_management"])
    if data.get("enable_risk_management") is False:
        raise HTTPException(status_code=400, detail="Risk management cannot be disabled for live bots")

    if symbol in eng.config.TRADELIST and not data.get("force_override", False):
        return {
            "status": "error",
            "message": f"An active bot for '{symbol}' is already deployed. Please delete the existing bot first to deploy a new one."
        }

    # Check if the asset is tradable in Alpaca
    tradable_check = eng.broker.check_tradable(symbol)
    if not tradable_check.get("success") or not tradable_check.get("tradable"):
        return {
            "status": "error",
            "message": tradable_check.get("error", f"Ticker '{symbol}' doesn't allow trading in Alpaca or does not exist.")
        }
    market_session_check = _validate_market_session_for_symbol(symbol, data.get("market_session", "Regular"), eng)

    if symbol not in eng.config.TICKER_SETTINGS:
        eng.config.TICKER_SETTINGS[symbol] = {}

    eng.config.TICKER_SETTINGS[symbol]["source"] = "bots"
    broker_asset_class = str(tradable_check.get("asset_class", "") or "").strip().lower()
    if broker_asset_class in {"crypto", "cryptocurrency"}:
        eng.config.TICKER_SETTINGS[symbol]["asset_class"] = "crypto"
    elif broker_asset_class in {"stock", "equity", "us_equity"}:
        eng.config.TICKER_SETTINGS[symbol]["asset_class"] = "stock"
    else:
        eng.config.TICKER_SETTINGS[symbol]["asset_class"] = "crypto" if _is_crypto_symbol(symbol) else "stock"

    if "capital" in data and data["capital"] is not None:
        try:
            capital = float(data["capital"])
        except (TypeError, ValueError):
            raise HTTPException(status_code=400, detail="capital must be numeric")
        if not math.isfinite(capital) or not 0 < capital <= 1_000_000:
            raise HTTPException(status_code=400, detail="capital must be finite and between 0 and 1000000")
        eng.config.TICKER_SETTINGS[symbol]["amount"] = capital

    if "threshold" in data and data["threshold"] is not None:
        eng.config.TICKER_SETTINGS[symbol]["min_buy_signals"] = int(data["threshold"])
    if "sell_threshold" in data and data["sell_threshold"] is not None:
        eng.config.TICKER_SETTINGS[symbol]["min_sell_signals"] = int(data["sell_threshold"])

    if "max_trades" in data and data["max_trades"] is not None:
        max_trades = int(data["max_trades"])
        if max_trades < 0 or max_trades > 100:
            raise HTTPException(status_code=400, detail="max_trades must be between 0 (unlimited) and 100")
        eng.config.TICKER_SETTINGS[symbol]["max_trades"] = max_trades

    if "timeframe" in data:
        eng.config.TICKER_SETTINGS[symbol]["timeframe"] = data["timeframe"]

    if "sell_mode" in data:
        eng.config.TICKER_SETTINGS[symbol]["sell_mode"] = data["sell_mode"]
    else:
        eng.config.TICKER_SETTINGS[symbol]["sell_mode"] = "indicator"

    passthrough_settings = [
        "buy_mode",
        "strategy_preset_enabled",
        "strategy_preset",
        "strategy_preset_variant",
        "strategy_preset_position_policy",
        *_ALLOCATION_SETTING_KEYS,
        *_SESSION_SETTING_KEYS,
        "scan_interval_auto",
        "scan_interval_seconds",
        "earnings_watch_enabled",
        "fundamental_watch_enabled",
        "custom_buy_price",
        "custom_stop_loss_price",
        "custom_take_profit_price",
        "custom_underlying_stop_loss_price",
        "custom_underlying_take_profit_price",
        "allocation_mode",
        "compound_amount",
        "options_allocation_mode",
        "options_compound_amount",
        "market_session",
        "entry_atr_multiplier",
        "entry_pct_move",
        "entry_atr_style",
        "entry_pct_style",
        "swing_entry_mode",
        "swing_entry_lookback_days",
        "swing_entry_lookback_mode",
        "swing_exit_lookback_days",
        "swing_exit_lookback_mode",
        "indicator_overrides",
        "enable_risk_management",
        "enable_atr_trail",
        "enable_pct_trail",
        "regular_stop_loss_pct",
        "regular_take_profit_pct",
        "regular_trailing_pct",
        "enable_cooldown",
        "enable_signal_reset",
        "enable_smart_reentry",
        "smart_reentry_type",
        "smart_reentry_rsi_call_max",
        "smart_reentry_rsi_put_min",
        "smart_reentry_vwap_call_max_dist_pct",
        "smart_reentry_vwap_put_max_dist_pct",
        "post_stop_stabilization_bars",
        "max_consecutive_losses",
        "enable_liquidity_filter",
        "stock_max_spread_pct",
        "crypto_max_spread_pct",
        "max_quote_age_seconds",
        "options_max_entry_spread_pct",
        "options_max_quote_age_seconds",
        "options_min_volume",
        "options_min_open_interest",
        "options_limit_aggression",
        "options_entry_order_timeout_seconds",
        "options_exit_order_timeout_seconds",
        "enable_options_market_open_filter",
        "options_market_open_blackout_minutes",
        *_GRID_CONFIGURATION_KEYS,
    ]
    for key in passthrough_settings:
        if key in data:
            value = data[key]
            if value is None or value == "default":
                eng.config.TICKER_SETTINGS[symbol].pop(key, None)
            else:
                eng.config.TICKER_SETTINGS[symbol][key] = value
    eng.config.TICKER_SETTINGS[symbol]["enable_risk_management"] = True
    # Live bots use one consistent safety policy: pause after exactly three
    # consecutive completed losing trades.
    eng.config.TICKER_SETTINGS[symbol]["max_consecutive_losses"] = 3
    if eng.config.TICKER_SETTINGS[symbol].get("smart_reentry_type"):
        eng.config.TICKER_SETTINGS[symbol]["smart_reentry_type"] = str(eng.config.TICKER_SETTINGS[symbol]["smart_reentry_type"]).upper()
    if eng.config.TICKER_SETTINGS[symbol].get("enable_signal_reset") is False:
        eng._clear_signal_reset_lock(symbol, eng.config.TICKER_SETTINGS[symbol])
    _normalize_continuous_grid_settings(eng.config.TICKER_SETTINGS[symbol])
    _reset_continuous_grid_runtime(eng.config.TICKER_SETTINGS[symbol])
    eng.config.TICKER_SETTINGS[symbol]["market_session"] = _normalize_market_session_for_symbol(
        symbol,
        eng.config.TICKER_SETTINGS[symbol].get("market_session")
    )
    eng.config.TICKER_SETTINGS[symbol]["market_session"] = market_session_check["market_session"]

    _restart_compounding_when_amount_changed(
        eng.config.TICKER_SETTINGS[symbol], prior_for_preset,
        {key: value for key, value in (("amount", data.get("capital")),
                                       ("options_trade_amount", data.get("options_trade_amount"))) if value is not None},
    )
    allocation_mode = str(eng.config.TICKER_SETTINGS[symbol].get("allocation_mode", "fixed") or "fixed").lower()
    if allocation_mode == "compound" and not eng.config.TICKER_SETTINGS[symbol].get("compound_amount"):
        eng.config.TICKER_SETTINGS[symbol]["compound_amount"] = float(eng.config.TICKER_SETTINGS[symbol].get("amount", data.get("capital", 0)) or 0)
    if allocation_mode != "compound":
        eng.config.TICKER_SETTINGS[symbol].pop("compound_amount", None)

    if "indicators" in data and isinstance(data["indicators"], list):
        eng.config.TICKER_SETTINGS[symbol]["indicators"] = data["indicators"]
    _normalize_bot_signal_thresholds(eng.config.TICKER_SETTINGS[symbol], eng)
    # A strategy preset owns entries and exits: back up the manual setup, apply
    # the strategy's allow-listed overrides (timeframe, session) and lock the
    # manual modes. Risk and sizing fields come from the user and are untouched.
    resolved_preset_settings = _resolve_strategy_preset(eng.config.TICKER_SETTINGS[symbol], symbol, prior_for_preset)
    if resolved_preset_settings is not eng.config.TICKER_SETTINGS[symbol]:
        eng.config.TICKER_SETTINGS[symbol].clear()
        eng.config.TICKER_SETTINGS[symbol].update(resolved_preset_settings)
    _arm_session_bot(eng.config.TICKER_SETTINGS[symbol])

    for rk in ["risk_per_trade", "max_daily_drawdown", "max_position_pct", "atr_stop_multiplier", "atr_trail_multiplier", "take_profit_multiplier"]:
        if rk in data:
            if data[rk] is not None:
                eng.config.TICKER_SETTINGS[symbol][rk] = float(data[rk])
            else:
                if rk in eng.config.TICKER_SETTINGS[symbol]:
                    del eng.config.TICKER_SETTINGS[symbol][rk]

    if "cooldown_minutes" in data:
        if data["cooldown_minutes"] is None or data["cooldown_minutes"] == "default":
            if "cooldown_minutes" in eng.config.TICKER_SETTINGS[symbol]:
                del eng.config.TICKER_SETTINGS[symbol]["cooldown_minutes"]
        else:
            eng.config.TICKER_SETTINGS[symbol]["cooldown_minutes"] = int(data["cooldown_minutes"])

    # Options Trading parameters
    if "options_trading" in data:
        eng.config.TICKER_SETTINGS[symbol]["options_trading"] = bool(data["options_trading"])
    if data.get("options_trading") or "options_type" in data:
        try:
            eng.config.TICKER_SETTINGS[symbol]["options_type"] = validate_options_type(data.get("options_type", "calls_only"))
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc))
    if "options_trade_amount" in data:
        if data["options_trade_amount"] is None:
            eng.config.TICKER_SETTINGS[symbol].pop("options_trade_amount", None)
        else:
            eng.config.TICKER_SETTINGS[symbol]["options_trade_amount"] = _validate_trade_allocation(
                data["options_trade_amount"], "options_trade_amount"
            )
    options_allocation_mode = str(eng.config.TICKER_SETTINGS[symbol].get("options_allocation_mode", eng.config.TICKER_SETTINGS[symbol].get("allocation_mode", "fixed")) or "fixed").lower()
    if options_allocation_mode == "compound" and not eng.config.TICKER_SETTINGS[symbol].get("options_compound_amount"):
        eng.config.TICKER_SETTINGS[symbol]["options_compound_amount"] = float(eng.config.TICKER_SETTINGS[symbol].get("options_trade_amount", data.get("options_trade_amount", 0)) or 0)
    if "options_compound_amount" in eng.config.TICKER_SETTINGS[symbol]:
        compound_amount = eng.config.TICKER_SETTINGS[symbol].get("options_compound_amount")
        if compound_amount not in (None, ""):
            eng.config.TICKER_SETTINGS[symbol]["options_compound_amount"] = _validate_trade_allocation(
                compound_amount, "options_compound_amount"
            )
    if options_allocation_mode != "compound":
        eng.config.TICKER_SETTINGS[symbol].pop("options_compound_amount", None)
    if "options_expiry_days" in data and data["options_expiry_days"] is not None:
        try:
            expiry_days = int(data["options_expiry_days"])
        except (TypeError, ValueError):
            raise HTTPException(status_code=400, detail="options_expiry_days must be a whole number")
        if not 0 <= expiry_days <= 365:
            raise HTTPException(status_code=400, detail="options_expiry_days must be between 0 and 365")
        eng.config.TICKER_SETTINGS[symbol]["options_expiry_days"] = expiry_days
    if "options_strike_type" in data:
        eng.config.TICKER_SETTINGS[symbol]["options_strike_type"] = data["options_strike_type"]
    if "options_exit_strategy" in data:
        eng.config.TICKER_SETTINGS[symbol]["options_exit_strategy"] = data["options_exit_strategy"]
    if eng.config.TICKER_SETTINGS[symbol].get("options_trading") and eng.config.TICKER_SETTINGS[symbol].get("sell_mode"):
        eng.config.TICKER_SETTINGS[symbol]["options_exit_strategy"] = eng.config.TICKER_SETTINGS[symbol]["sell_mode"]
    if "options_stop_loss_pct" in data and data["options_stop_loss_pct"] is not None:
        eng.config.TICKER_SETTINGS[symbol]["options_stop_loss_pct"] = float(data["options_stop_loss_pct"])
    if "options_take_profit_pct" in data and data["options_take_profit_pct"] is not None:
        eng.config.TICKER_SETTINGS[symbol]["options_take_profit_pct"] = float(data["options_take_profit_pct"])
    if "options_trailing_pct" in data and data["options_trailing_pct"] is not None:
        eng.config.TICKER_SETTINGS[symbol]["options_trailing_pct"] = float(data["options_trailing_pct"])

    # Re-apply after options_trading is known so stale grid flags can never
    # cross from a regular bot configuration into options execution.
    _normalize_continuous_grid_settings(eng.config.TICKER_SETTINGS[symbol])

    if "ai_autopilot" in data:
        eng.config.TICKER_SETTINGS[symbol]["ai_autopilot"] = bool(data["ai_autopilot"])
    if "ai_model" in data:
        eng.config.TICKER_SETTINGS[symbol]["ai_model"] = data["ai_model"]

    if "paused" in data:
        eng.config.TICKER_SETTINGS[symbol]["paused"] = bool(data["paused"])
        if not data["paused"]:
            eng.config.TICKER_SETTINGS[symbol]["pause_reason"] = ""
            eng.config.TICKER_SETTINGS[symbol].pop("paused_at", None)
            eng.config.TICKER_SETTINGS[symbol].pop("pause_code", None)
            eng.config.TICKER_SETTINGS[symbol].pop("pause_event_id", None)
            eng.config.TICKER_SETTINGS[symbol].pop("pause_requires_review", None)
            eng.config.TICKER_SETTINGS[symbol]["realized_pl"] = 0.0
            eng.config.TICKER_SETTINGS[symbol]["daily_realized_pl"] = 0.0
            eng.config.TICKER_SETTINGS[symbol]["consecutive_losses"] = 0
            eng.config.TICKER_SETTINGS[symbol].pop("post_stop_state", None)
            eng.config.TICKER_SETTINGS[symbol]["entry_trades"] = 0
            eng.config.TICKER_SETTINGS[symbol]["started_at"] = get_now().isoformat()
    else:
        if not eng.config.TICKER_SETTINGS[symbol].get("paused", False):
            if not eng.config.TICKER_SETTINGS[symbol].get("started_at"):
                eng.config.TICKER_SETTINGS[symbol]["started_at"] = get_now().isoformat()

    add_to_watchlist = data.get("add_to_watchlist", False)
    if add_to_watchlist and symbol not in eng.config.WATCHLIST:
        eng.config.WATCHLIST.append(symbol)
    elif not add_to_watchlist and symbol in eng.config.WATCHLIST:
        eng.config.WATCHLIST.remove(symbol)

    if symbol not in eng.config.TRADELIST:
        eng.config.TRADELIST.insert(0, symbol)
        print(f"[settings] Launched new bot for {symbol} with custom settings for {eng.uid}")

    _clear_forecast_exit_snapshots(eng.config.TICKER_SETTINGS[symbol])
    clear_evaluation_cache(symbol, None)
    clear_ai_cache(symbol, None)
    _clear_chart_analysis_cache(symbol, None)

    async def _background_warmup_and_save():
        # One analysis-only evaluation of this bot (never places an order), then ask the trading loop to scan it next.
        try:
            await _run_forced_bot_scan(eng, symbol, "created", data.get("timeframe"))
        finally:
            if not eng.config.TICKER_SETTINGS[symbol].get("forecast_entry_snapshot"):
                _ensure_entry_forecast_snapshot(eng.config.TICKER_SETTINGS[symbol], {}, symbol)
            await eng.save_settings()
            eng.request_forced_scan(symbol, "created")

    asyncio.create_task(_background_warmup_and_save())
    return {"status": "success", "symbol": symbol, "watchlist": eng.config.WATCHLIST, "tradelist": eng.config.TRADELIST, "settings": eng.config.TICKER_SETTINGS[symbol]}


@app.post("/api/cancel_order")
async def cancel_order(data: dict, eng: UserEngine = Depends(get_user_engine)):
    """Cancel an active order on Alpaca by ID."""
    order_id = str(data.get("order_id") or "").strip()
    if not order_id:
        return {"status": "error", "message": "order_id is required"}
    if len(order_id) > 128:
        raise HTTPException(status_code=400, detail="order_id is too long")
    try:
        result = await _run_bounded_provider_call(
            eng.uid,
            "order cancellation",
            lambda: eng.broker.cancel_order_by_id(order_id),
        )
    except asyncio.TimeoutError:
        return {
            "status": "error",
            "message": "Cancellation outcome is unknown; reconcile the order before retrying.",
        }
    except Exception:
        return {"status": "error", "message": "Broker cancellation is temporarily unavailable."}
    if result.get("success"):
        return {"status": "success", "message": f"Order {order_id} cancelled successfully."}
    else:
        return {"status": "error", "message": result.get("error", "Failed to cancel order.")}


@app.post("/api/settings/indicators")
async def update_indicators(updates: dict, context: str = "dashboard", ticker: str = None, eng: UserEngine = Depends(get_user_engine)):
    """Update indicator toggles or parameters dynamically. Instant in-memory + persists to Firestore."""
    ctx = _normalize_analysis_context(context)
    allowed_indicator_keys = set(INDICATOR_PARAMETER_KEYS) | set(DASHBOARD_CONFLUENCE_TOGGLE_KEYS)
    unknown_keys = sorted(set(updates or {}) - allowed_indicator_keys - {
        "reset_confluence", "__reset_confluence", "confluence_toggles", "reset_all"
    })
    if unknown_keys:
        raise HTTPException(status_code=400, detail=f"Unsupported indicator setting(s): {', '.join(unknown_keys)}")
    reset_confluence = bool(updates.pop("reset_confluence", False) or updates.pop("__reset_confluence", False))
    requested_confluence_defaults = updates.pop("confluence_toggles", None)
    reset_all = bool(updates.pop("reset_all", False))
    for period_key in ("EMA_FAST", "EMA_SLOW", "SMA_PERIOD", "SMA_SECOND_PERIOD"):
        raw_value = updates.get(period_key)
        if raw_value is None or raw_value == "":
            continue
        try:
            numeric_period = float(raw_value)
            if not numeric_period.is_integer():
                raise ValueError
            normalized_period = int(numeric_period)
        except (TypeError, ValueError):
            raise HTTPException(status_code=400, detail=f"{period_key} must be a whole number from 1 to 500")
        if normalized_period < 1 or normalized_period > 500:
            raise HTTPException(status_code=400, detail=f"{period_key} must be between 1 and 500")
        updates[period_key] = normalized_period
    for line_toggle_key in ("EMA_USE_SECOND_LINE", "SMA_USE_SECOND_LINE"):
        raw_value = updates.get(line_toggle_key)
        if raw_value is None or raw_value == "":
            continue
        if isinstance(raw_value, str):
            normalized_text = raw_value.strip().lower()
            if normalized_text in {"1", "true", "yes", "on"}:
                normalized_toggle = True
            elif normalized_text in {"0", "false", "no", "off"}:
                normalized_toggle = False
            else:
                raise HTTPException(status_code=400, detail=f"{line_toggle_key} must be true or false")
        else:
            normalized_toggle = bool(raw_value)
        updates[line_toggle_key] = normalized_toggle
    if not isinstance(requested_confluence_defaults, dict):
        requested_confluence_defaults = {}
    reset_confluence_toggles = {
        key: bool(requested_confluence_defaults.get(key, getattr(global_config, key, True)))
        for key in DASHBOARD_CONFLUENCE_TOGGLE_KEYS
    }

    if ticker:
        ticker = ticker.upper()
        if ticker not in eng.config.TICKER_SETTINGS:
            eng.config.TICKER_SETTINGS[ticker] = {}
        if 'indicator_overrides' not in eng.config.TICKER_SETTINGS[ticker]:
            eng.config.TICKER_SETTINGS[ticker]['indicator_overrides'] = {}

        for k, v in updates.items():
            if v == "" or v is None:
                if k in eng.config.TICKER_SETTINGS[ticker]['indicator_overrides']:
                    del eng.config.TICKER_SETTINGS[ticker]['indicator_overrides'][k]
            else:
                try:
                    default_val = getattr(global_config, k, None)
                    if isinstance(default_val, bool): v = bool(v)
                    elif isinstance(default_val, int): v = int(v)
                    elif isinstance(default_val, float): v = float(v)
                except Exception:
                    pass
                eng.config.TICKER_SETTINGS[ticker]['indicator_overrides'][k] = v
    else:
        from user_config import active_evaluation_context
        token = active_evaluation_context.set(ctx)
        try:
            if reset_all:
                ctx_data = eng.config.contexts.setdefault(ctx, {"toggles": {}, "parameters": {}})
                ctx_data["parameters"].clear()
                ctx_data["toggles"].clear()
                for k in INDICATOR_PARAMETER_KEYS:
                    eng.config.set(k, None)
            elif reset_confluence:
                ctx_data = eng.config.contexts.setdefault(ctx, {"toggles": {}, "parameters": {}})
                ctx_data["parameters"] = {}
                ctx_data["toggles"] = {
                    **{
                        k: v for k, v in ctx_data.get("toggles", {}).items()
                        if k not in DASHBOARD_CONFLUENCE_TOGGLE_KEYS
                    },
                    **reset_confluence_toggles,
                }
                for k in INDICATOR_PARAMETER_KEYS:
                    eng.config.set(k, None)
            else:
                for k, v in updates.items():
                    if v == "" or v is None:
                        eng.config.set(k, None)
                    elif k.startswith("ENABLE_"):
                        eng.config.set(k, bool(v))
                    else:
                        try:
                            default_val = getattr(global_config, k, None)
                            if isinstance(default_val, bool):
                                eng.config.set(k, bool(v))
                            elif isinstance(default_val, int):
                                eng.config.set(k, int(v))
                            elif isinstance(default_val, float):
                                eng.config.set(k, float(v))
                            else:
                                eng.config.set(k, v)
                        except Exception as e:
                            print(f"[settings] Type conversion error for {k}: {_safe_exception_type(e)}")
                            eng.config.set(k, v)
            if ctx == "dashboard":
                if reset_all or reset_confluence:
                    workspace_toggle_keys = DASHBOARD_STANDARD_TOGGLE_TO_SIGNAL.keys()
                else:
                    workspace_toggle_keys = updates.keys()
                _sync_dashboard_workspace_from_toggle_updates(eng, workspace_toggle_keys)
        finally:
            active_evaluation_context.reset(token)

    if ticker and ticker in eng.config.TICKER_SETTINGS:
        _clear_forecast_exit_snapshots(eng.config.TICKER_SETTINGS[ticker])

    _queue_settings_save(eng, "indicator settings")
    clear_evaluation_cache()
    clear_ai_cache(ticker, None) if ticker else clear_ai_cache()
    _clear_chart_analysis_cache(ticker, None)
    if eng.force_scan_trigger:
        eng.force_scan_trigger.set()

    if reset_confluence:
        print(f"[settings] Reset dashboard confluence for {eng.uid} ({ctx})")
        return {
            "status": "success",
            "reset": True,
            "save": "queued",
            "toggles": reset_confluence_toggles,
            "parameters_reset": INDICATOR_PARAMETER_KEYS,
        }

    print(f"[settings] Updated indicator settings for {eng.uid} ({ctx}): {', '.join(updates.keys())}")
    return {"status": "success", "save": "queued"}


@app.post("/api/settings/ticker")
async def update_ticker_settings(data: dict, eng: UserEngine = Depends(get_user_engine)):
    """Update settings for a specific ticker."""
    ticker = str(data.get("ticker", "")).strip().upper()
    settings = data.get("settings", {})
    if not ticker: return {"status": "error"}
    if not re.fullmatch(r"[A-Z0-9.\-]{1,15}", ticker):
        raise HTTPException(status_code=400, detail="Invalid ticker format")
    if ticker not in eng.config.TICKER_SETTINGS and len(eng.config.TICKER_SETTINGS) >= MAX_ACTIVE_BOTS:
        raise HTTPException(status_code=413, detail=f"Ticker settings limit is {MAX_ACTIVE_BOTS} symbols")
    if not isinstance(settings, dict):
        raise HTTPException(status_code=400, detail="settings must be an object")
    _strip_strategy_preset_server_keys(settings)
    existing_for_preset = eng.config.TICKER_SETTINGS.get(ticker, {})
    prior_for_preset = copy.deepcopy(existing_for_preset) if isinstance(existing_for_preset, dict) else {}
    merged_for_preset = dict(prior_for_preset)
    for key, value in settings.items():
        if value is None or value == "default":
            merged_for_preset.pop(key, None)
        else:
            merged_for_preset[key] = value
    _validate_strategy_preset_request(merged_for_preset, ticker)
    _validate_allocation_conflicts(eng, ticker, merged_for_preset)
    _validate_bot_scan_interval(merged_for_preset)
    for allocation_key in ("options_trade_amount", "options_compound_amount"):
        if allocation_key in settings and settings[allocation_key] not in (None, "default", ""):
            settings[allocation_key] = _validate_trade_allocation(settings[allocation_key], allocation_key)
    if "enable_risk_management" in settings and settings.get("enable_risk_management") is not None:
        settings["enable_risk_management"] = _normalize_risk_management_setting(settings["enable_risk_management"])
    if settings.get("enable_risk_management") is False:
        raise HTTPException(status_code=400, detail="Risk management cannot be disabled for live bots")

    if ticker not in eng.config.TICKER_SETTINGS:
        eng.config.TICKER_SETTINGS[ticker] = {}
    grid_config_before = _continuous_grid_config_snapshot(eng.config.TICKER_SETTINGS[ticker])
    previously_paused = eng.config.TICKER_SETTINGS.get(ticker, {}).get('paused', False)
    requested_session = settings.get("market_session", eng.config.TICKER_SETTINGS[ticker].get("market_session", "Regular"))
    market_session_check = _validate_market_session_for_symbol(ticker, requested_session, eng)
    if settings.get("options_trading") or "options_type" in settings:
        try:
            settings["options_type"] = validate_options_type(settings.get("options_type", "calls_only"))
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc))
    for k, v in settings.items():
        if v is not None and v != "default":
            eng.config.TICKER_SETTINGS[ticker][k] = v
        elif k in eng.config.TICKER_SETTINGS[ticker]:
            del eng.config.TICKER_SETTINGS[ticker][k]

    # Do not allow stale or client-supplied values to weaken or accelerate the
    # three-loss live-bot pause policy.
    eng.config.TICKER_SETTINGS[ticker]["max_consecutive_losses"] = 3

    if "max_trades" in settings and settings.get("max_trades") is not None:
        try:
            max_trades = int(settings["max_trades"])
        except (TypeError, ValueError):
            raise HTTPException(status_code=400, detail="max_trades must be a whole number")
        if max_trades < 0 or max_trades > 100:
            raise HTTPException(status_code=400, detail="max_trades must be between 0 (unlimited) and 100")
        eng.config.TICKER_SETTINGS[ticker]["max_trades"] = max_trades
    eng.config.TICKER_SETTINGS[ticker]["market_session"] = market_session_check["market_session"]

    resolved_preset_settings = _resolve_strategy_preset(eng.config.TICKER_SETTINGS[ticker], ticker, prior_for_preset)
    if resolved_preset_settings is not eng.config.TICKER_SETTINGS[ticker]:
        eng.config.TICKER_SETTINGS[ticker].clear()
        eng.config.TICKER_SETTINGS[ticker].update(resolved_preset_settings)
    _arm_session_bot(eng.config.TICKER_SETTINGS[ticker])

    current_settings = eng.config.TICKER_SETTINGS[ticker]
    _normalize_continuous_grid_settings(current_settings)
    if grid_config_before != _continuous_grid_config_snapshot(current_settings):
        _reset_continuous_grid_runtime(current_settings)
    if current_settings.get("smart_reentry_type"):
        current_settings["smart_reentry_type"] = str(current_settings["smart_reentry_type"]).upper()
    if current_settings.get("enable_signal_reset") is False:
        eng._clear_signal_reset_lock(ticker, current_settings)
    if current_settings.get("options_trading") and current_settings.get("sell_mode"):
        current_settings["options_exit_strategy"] = current_settings["sell_mode"]
    _restart_compounding_when_amount_changed(current_settings, prior_for_preset, settings)
    allocation_mode = str(current_settings.get("allocation_mode", "fixed") or "fixed").lower()
    if allocation_mode == "compound" and not current_settings.get("compound_amount"):
        current_settings["compound_amount"] = float(current_settings.get("amount", 0) or 0)
    if allocation_mode != "compound":
        current_settings.pop("compound_amount", None)

    options_allocation_mode = str(current_settings.get("options_allocation_mode", current_settings.get("allocation_mode", "fixed")) or "fixed").lower()
    if options_allocation_mode == "compound" and not current_settings.get("options_compound_amount"):
        current_settings["options_compound_amount"] = float(current_settings.get("options_trade_amount", current_settings.get("amount", 0)) or 0)
    if options_allocation_mode != "compound":
        current_settings.pop("options_compound_amount", None)
    _normalize_bot_signal_thresholds(eng.config.TICKER_SETTINGS[ticker], eng)
    _clear_forecast_exit_snapshots(current_settings)

    if settings.get('paused') is False:
        eng.config.TICKER_SETTINGS[ticker]['pause_reason'] = ""
        eng.config.TICKER_SETTINGS[ticker].pop('paused_at', None)
        eng.config.TICKER_SETTINGS[ticker].pop('pause_code', None)
        eng.config.TICKER_SETTINGS[ticker].pop('pause_event_id', None)
        eng.config.TICKER_SETTINGS[ticker].pop('pause_requires_review', None)
        eng.config.TICKER_SETTINGS[ticker]['realized_pl'] = 0.0
        eng.config.TICKER_SETTINGS[ticker]['daily_realized_pl'] = 0.0
        eng.config.TICKER_SETTINGS[ticker]['consecutive_losses'] = 0
        eng.config.TICKER_SETTINGS[ticker].pop('post_stop_state', None)
        # Unpausing starts a new limited run; preserve historical totals but
        # reset the entry counter used by max_trades.
        if previously_paused:
            eng.config.TICKER_SETTINGS[ticker]['entry_trades'] = 0
        if previously_paused or not eng.config.TICKER_SETTINGS[ticker].get('started_at'):
            eng.config.TICKER_SETTINGS[ticker]['started_at'] = get_now().isoformat()

    add_to_watchlist = data.get("add_to_watchlist")
    if add_to_watchlist is True and ticker not in eng.config.WATCHLIST:
        eng.config.WATCHLIST.append(ticker)
    elif add_to_watchlist is False and ticker in eng.config.WATCHLIST:
        eng.config.WATCHLIST.remove(ticker)

    clear_evaluation_cache(ticker, None)
    clear_ai_cache(ticker, None)
    _clear_chart_analysis_cache(ticker, None)

    async def _background_warmup_and_save():
        # One analysis-only evaluation of this bot (never places an order), then ask the trading loop to scan it next
        # instead of waiting for its refresh interval.
        try:
            await _run_forced_bot_scan(eng, ticker, "settings_changed", current_settings.get('timeframe'))
        finally:
            if not current_settings.get("forecast_entry_snapshot"):
                _ensure_entry_forecast_snapshot(current_settings, {}, ticker)
            await eng.save_settings()
            eng.request_forced_scan(ticker, "settings_changed")

    warmup_key = (str(eng.uid), ticker)
    previous_warmup = _ticker_warmup_tasks.get(warmup_key)
    if previous_warmup and not previous_warmup.done():
        previous_warmup.cancel()
    async def _bounded_warmup():
        try:
            async with _bounded_analysis_work(eng.uid, "ticker warmup"):
                await _background_warmup_and_save()
        except asyncio.CancelledError:
            raise
        except HTTPException as exc:
            print(f"[settings/update] Warmup deferred for {ticker}: {exc.detail}")
            await eng.save_settings()
        finally:
            current_task = asyncio.current_task()
            if _ticker_warmup_tasks.get(warmup_key) is current_task:
                _ticker_warmup_tasks.pop(warmup_key, None)
    _ticker_warmup_tasks[warmup_key] = asyncio.create_task(_bounded_warmup())
    return {"status": "success"}

@app.delete("/api/settings/ticker/{ticker}")
async def reset_ticker_settings(ticker: str, eng: UserEngine = Depends(get_user_engine)):
    """Reset a ticker to global defaults."""
    ticker = ticker.upper()
    warmup_key = (str(eng.uid), ticker)
    pending_warmup = _ticker_warmup_tasks.pop(warmup_key, None)
    if pending_warmup and not pending_warmup.done():
        pending_warmup.cancel()
    if ticker in eng.config.TICKER_SETTINGS:
        del eng.config.TICKER_SETTINGS[ticker]
        asyncio.create_task(eng.save_settings())
    clear_evaluation_cache(ticker, None)
    clear_ai_cache(ticker, None)
    _clear_chart_analysis_cache(ticker, None)
    return {"status": "success"}


# ──────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────

def _format_trade_for_ui(trade: dict) -> dict:
    """Formats a trade decision for the frontend table."""
    ticker = trade.get("ticker", "").replace("/", "").upper()
    tf = trade.get("timeframe")
    uc = get_user_config()
    if not tf:
        t_settings = getattr(uc, 'TICKER_SETTINGS', {}).get(ticker, {})
        tf = t_settings.get('timeframe', uc.DEFAULT_TIMEFRAME)

    ticker_settings = getattr(uc, 'TICKER_SETTINGS', {}).get(ticker, {})
    strategy_row = strategy_scan_log.row_view(ticker_settings, trade) if isinstance(ticker_settings, dict) else None
    return {
        "strategy": strategy_row,
        "time": trade.get("time", ""),
        "action": trade.get("action", ""),
        "ticker": ticker,
        "price": trade.get("price", ""),
        "qty": trade.get("qty", "N/A"),
        "total_cost": trade.get("total_cost", 0),
        "fees": trade.get("fees", 0),
        "reason": trade.get("reason", ""),
        "pl": trade.get("pl"),
        "pl_pct": trade.get("pl_pct"),
        "bullish_count": trade.get("bullish_count", 0),
        "bearish_count": trade.get("bearish_count", 0),
        "total_signals": trade.get("total_signals", 0),
        "timeframe": tf,
        "log_type": trade.get("log_type", "Active Bot"),
        "exit_monitoring": trade.get("exit_monitoring", False),
        "exit_signal_label": trade.get("exit_signal_label", ""),
        "exit_signal_count": trade.get("exit_signal_count", 0),
        "exit_signal_threshold": trade.get("exit_signal_threshold", 0),
        "exit_signal_direction": trade.get("exit_signal_direction", ""),
        "exit_watch_type": trade.get("exit_watch_type", ""),
        "exit_watch_details": trade.get("exit_watch_details", {}),
        "option_intent": trade.get("option_intent", "HOLD"),
        "has_position": trade.get("has_position", False),
        "buy_mode": trade.get("buy_mode", ""),
        "sell_mode": trade.get("sell_mode", ""),
        "structure_direction": trade.get("structure_direction", ""),
        "structure_ok": trade.get("structure_ok", False),
    }


def _parse_iso_datetime(value):
    if not value:
        return None

    dt = value if isinstance(value, datetime) else None
    if dt is None:
        text = str(value).strip()
        if not text:
            return None
        try:
            dt = datetime.fromisoformat(text.replace("Z", "+00:00"))
        except ValueError:
            return None

    if dt.tzinfo is None:
        dt = pytz.utc.localize(dt)
    return dt.astimezone(pytz.timezone(get_user_config().TIMEZONE))


def _is_option_occ_symbol(symbol: str) -> bool:
    return bool(re.fullmatch(r"[A-Z]{1,6}\d{6}[CP]\d{8}", _normalize_quote_symbol(symbol)))


def _model_value(obj, *names, default=None):
    if obj is None:
        return default
    if isinstance(obj, dict):
        for name in names:
            if name in obj and obj[name] is not None:
                return obj[name]
    for name in names:
        if hasattr(obj, name):
            value = getattr(obj, name)
            if value is not None:
                return value
    return default


def _float_or_none(value):
    try:
        if value is None or value == "":
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def _mapping_from_response(resp) -> dict:
    if resp is None:
        return {}
    if isinstance(resp, dict):
        return resp
    if hasattr(resp, "items"):
        try:
            return dict(resp.items())
        except Exception:
            pass
    if hasattr(resp, "model_dump"):
        try:
            return resp.model_dump()
        except Exception:
            pass
    if hasattr(resp, "dict"):
        try:
            return resp.dict()
        except Exception:
            pass
    return {}


def _serialize_option_snapshot(snapshot: dict) -> dict:
    latest_quote = _model_value(snapshot, "latest_quote")
    latest_trade = _model_value(snapshot, "latest_trade")
    greeks = _model_value(snapshot, "greeks")

    bid = _float_or_none(_model_value(latest_quote, "bid_price", "bid"))
    ask = _float_or_none(_model_value(latest_quote, "ask_price", "ask"))
    mid = ((bid + ask) / 2.0) if bid is not None and ask is not None else None
    spread = (ask - bid) if bid is not None and ask is not None else None
    spread_pct = ((spread / mid) * 100.0) if spread is not None and mid else None

    return {
        "quote": {
            "bid": bid,
            "ask": ask,
            "bid_size": _float_or_none(_model_value(latest_quote, "bid_size")),
            "ask_size": _float_or_none(_model_value(latest_quote, "ask_size")),
            "timestamp": str(_model_value(latest_quote, "timestamp", "t", default="")) or None,
            "exchange": _model_value(latest_quote, "exchange", "bid_exchange", "ask_exchange"),
            "mid": mid,
            "spread": spread,
            "spread_pct": spread_pct,
        },
        "latest_trade": {
            "price": _float_or_none(_model_value(latest_trade, "price", "trade_price")),
            "size": _float_or_none(_model_value(latest_trade, "size", "trade_size")),
            "timestamp": str(_model_value(latest_trade, "timestamp", "t", default="")) or None,
            "exchange": _model_value(latest_trade, "exchange"),
        },
        "greeks": {
            "delta": _float_or_none(_model_value(greeks, "delta")),
            "gamma": _float_or_none(_model_value(greeks, "gamma")),
            "theta": _float_or_none(_model_value(greeks, "theta")),
            "vega": _float_or_none(_model_value(greeks, "vega")),
            "rho": _float_or_none(_model_value(greeks, "rho")),
            "iv": _float_or_none(_model_value(greeks, "implied_volatility", "iv")),
        },
        "implied_volatility": _float_or_none(_model_value(snapshot, "implied_volatility", "iv")),
        "open_interest": _float_or_none(_model_value(snapshot, "open_interest")),
        "updated_at": str(_model_value(snapshot, "updated_at", "timestamp", default="")) or None,
    }


def _fetch_alpaca_option_snapshots(eng: UserEngine, symbols: list[str]) -> tuple[dict, str, str]:
    clean_symbols = list(dict.fromkeys(_normalize_quote_symbol(s) for s in symbols if _is_option_occ_symbol(s)))
    if not clean_symbols:
        return {}, "unavailable", ""

    api_key = getattr(eng.config, "ALPACA_API_KEY", "").strip()
    api_secret = getattr(eng.config, "ALPACA_SECRET_KEY", "").strip()
    if not api_key or not api_secret or api_key.startswith("your_alpaca"):
        return {}, "unavailable", "Missing Alpaca API credentials."

    try:
        from alpaca.data.enums import OptionsFeed
        from alpaca.data.historical.option import OptionHistoricalDataClient
        from alpaca.data.requests import OptionSnapshotRequest
    except Exception as exc:
        return {}, "unavailable", f"Alpaca option data client unavailable: {_safe_exception_type(exc)}"

    data_client = OptionHistoricalDataClient(api_key, api_secret)
    last_error = ""

    for feed in (OptionsFeed.OPRA, OptionsFeed.INDICATIVE):
        try:
            merged = {}
            for i in range(0, len(clean_symbols), 100):
                batch = clean_symbols[i:i + 100]
                req = OptionSnapshotRequest(symbol_or_symbols=batch, feed=feed)
                resp = data_client.get_option_snapshot(req)
                merged.update(_mapping_from_response(resp))

            if merged:
                return merged, getattr(feed, "value", str(feed)), ""
        except Exception as exc:
            last_error = _safe_exception_type(exc)
            continue

    return {}, "unavailable", last_error


def _build_option_review_row(order: dict, snapshot: dict, review_day) -> dict:
    symbol = _normalize_quote_symbol(order.get("symbol", ""))
    parsed = None
    try:
        from option_selector import parse_occ_symbol
        parsed = parse_occ_symbol(symbol)
    except Exception:
        parsed = None

    quote = snapshot.get("quote", {}) if snapshot else {}
    greeks = snapshot.get("greeks", {}) if snapshot else {}
    latest_trade = snapshot.get("latest_trade", {}) if snapshot else {}

    fill_price = _float_or_none(order.get("filled_avg_price")) or 0.0
    qty = _float_or_none(order.get("filled_qty") or order.get("qty")) or 0.0
    side = str(order.get("side", "")).upper()
    current_mid = quote.get("mid")
    contract_value = (fill_price * qty * 100.0) if fill_price and qty else 0.0

    mtm_pl = None
    mtm_pl_pct = None
    if side == "BUY" and current_mid is not None and fill_price > 0 and qty > 0:
        mtm_pl = round((current_mid - fill_price) * qty * 100.0, 2)
        mtm_pl_pct = round(((current_mid - fill_price) / fill_price) * 100.0, 2)

    return {
        "symbol": symbol,
        "underlying": parsed["underlying"] if parsed else None,
        "expiration": parsed["expiration"].isoformat() if parsed else None,
        "option_type": parsed["type"] if parsed else None,
        "strike": parsed["strike"] if parsed else None,
        "side": side,
        "status": order.get("status"),
        "qty": qty,
        "filled_avg_price": fill_price,
        "filled_at": order.get("filled_at") or order.get("created_at"),
        "created_at": order.get("created_at"),
        "total_cost": order.get("total_cost", contract_value),
        "realized_pl": order.get("pl"),
        "realized_pl_pct": order.get("pl_pct"),
        "current_quote": quote,
        "latest_trade": latest_trade,
        "greeks": greeks,
        "quote_feed": snapshot.get("quote_feed") if snapshot else None,
        "mark_to_market_pl": mtm_pl,
        "mark_to_market_pl_pct": mtm_pl_pct,
        "days_to_expiry": (parsed["expiration"] - review_day).days if parsed else None,
        "review_date": review_day.isoformat(),
        "reason": order.get("reason", ""),
    }


def _format_scan_for_ui(scan: dict, use_global_settings: bool = False) -> dict:
    """Formats a full scan result for the watchlist panel."""
    SIGNAL_TO_TOGGLE = {
        'RSI': 'ENABLE_RSI',
        'MACD': 'ENABLE_MACD',
        'EMA Cross': 'ENABLE_EMA',
        'Supertrend': 'ENABLE_SUPERTREND',
        'Bollinger': 'ENABLE_BOLLINGER',
        'VWAP': 'ENABLE_VWAP',
        'Mystic Pulse': 'ENABLE_MYSTIC_PULSE',
        'Candle Patterns': 'ENABLE_CANDLE_PATTERNS',
        'ADX Trend': 'ENABLE_ADX_TREND',
        'SMA': 'ENABLE_SMA',
        'Prior Day High/Low': 'ENABLE_DAILY_HIGH_LOW',
        'Pivot Points (S/R)': 'ENABLE_PIVOT_POINTS',
        'Volume Profile': 'ENABLE_VOLUME_PROFILE',
        'Swing Points': 'ENABLE_SWING_POINTS',
    }
    SIGNAL_TO_TOGGLE.update(EXTENSION_INDICATOR_TOGGLE_KEYS)

    raw_signals = scan.get("signals", {})
    all_signals = {}
    uc = get_user_config()
    ticker = scan.get("ticker", "")
    t_settings = getattr(uc, 'TICKER_SETTINGS', {}).get(ticker, {}) if not use_global_settings else {}
    allowed_indicators = t_settings.get('indicators') if not use_global_settings else None

    for name, data in raw_signals.items():
        toggle_key = SIGNAL_TO_TOGGLE.get(name, '')
        if 'enabled' in data:
            enabled = data['enabled']
        elif allowed_indicators is not None:
            enabled = name in allowed_indicators
        else:
            enabled = getattr(uc, toggle_key, True) if toggle_key else True

        all_signals[name] = {**data, 'enabled': enabled, 'toggle_key': toggle_key}

    bullish = scan.get('bullish_count')
    if bullish is None:
        bullish = sum(s.get('weight', 1) for s in all_signals.values() if s.get('signal') == 'BULLISH' and s.get('enabled'))

    bearish = scan.get('bearish_count')
    if bearish is None:
        bearish = sum(s.get('weight', 1) for s in all_signals.values() if s.get('signal') == 'BEARISH' and s.get('enabled'))

    active_count = scan.get('total_signals')
    if active_count is None:
        active_count = sum(1 for s in all_signals.values() if s.get('enabled'))

    return {
        "ticker": scan.get("ticker", ""),
        "time": scan.get("time", ""),
        "timeframe": scan.get("timeframe", ""),
        "price": scan.get("price", ""),
        "price_raw": scan.get("price_raw", 0.0),
        "action": scan.get("action", "HOLD"),
        "reason": scan.get("reason", ""),
        "sentiment_score": scan.get("sentiment_score", 0),
        "sentiment_confidence": scan.get("sentiment_confidence", 0),
        "sentiment_summary": scan.get("sentiment_summary", ""),
        "sentiment_key_factor": scan.get("sentiment_key_factor", "N/A"),
        "sentiment_pct": scan.get("news_sentiment_pct", scan.get("sentiment_pct")),
        "sentiment_direction": scan.get("news_sentiment_direction", scan.get("sentiment_direction", "NEUTRAL")),
        "news_alignment": scan.get("news_alignment", "ALIGNED"),
        "news_alignment_applied": scan.get("news_alignment_applied", False),
        "news_alignment_reason": scan.get("news_alignment_reason", ""),
        "model_action": scan.get("model_action", scan.get("action", "HOLD")),
        "bullish_count": bullish,
        "bearish_count": bearish,
        "total_signals": active_count,
        "signals": all_signals,
        "rsi": scan.get("rsi"),
        "atr": scan.get("atr", 0),
        "pl": scan.get("pl"),
        "pl_pct": scan.get("pl_pct"),
        "qty": scan.get("qty"),
        "position_sizing": scan.get("position_sizing", {}),
        "price_history": scan.get("price_history", []),
        "entry_price": scan.get("entry_price"),
        "stop_loss": scan.get("stop_loss"),
        "sell_target": scan.get("sell_target"),
        "sell_target_pct": scan.get("sell_target_pct"),
        "leverage_guidance": scan.get("leverage_guidance"),
        "key_catalyst": scan.get("key_catalyst"),
        "risk_factors": scan.get("risk_factors"),
        "summary": scan.get("summary"),
        "regime": scan.get("regime"),
        "position_size_usd": scan.get("position_size_usd"),
        "position_size_pct": scan.get("position_size_pct"),
        "prior_day_ohlc": scan.get("prior_day_ohlc"),
        "vwap": scan.get("vwap"),
        "forecast_entry_static": scan.get("forecast_entry_static", ""),
        "forecast_exit_static": scan.get("forecast_exit_static", ""),
        "scan_log": scan.get("scan_log"),
        "strategy_preset": scan.get("strategy_preset") if isinstance(scan.get("strategy_preset"), dict) else None,
        "candle_meta": scan.get("candle_meta") if isinstance(scan.get("candle_meta"), dict) else None,
        "last_real_scan": scan.get("last_real_scan") if isinstance(scan.get("last_real_scan"), dict) else None,
        "forced_scan": scan.get("forced_scan") if isinstance(scan.get("forced_scan"), dict) else None,
        "entry_guard_reason": str(scan.get("entry_guard_reason") or "") or None,
        "exit_monitoring": scan.get("exit_monitoring", False),
        "exit_signal_label": scan.get("exit_signal_label", ""),
        "exit_signal_count": scan.get("exit_signal_count", 0),
        "exit_signal_threshold": scan.get("exit_signal_threshold", 0),
        "exit_signal_direction": scan.get("exit_signal_direction", ""),
        "exit_watch_type": scan.get("exit_watch_type", ""),
        "exit_watch_details": scan.get("exit_watch_details", {}),
        "option_intent": scan.get("option_intent", "HOLD"),
        "has_position": scan.get("has_position", False),
        "buy_mode": scan.get("buy_mode", ""),
        "sell_mode": scan.get("sell_mode", ""),
        "structure_direction": scan.get("structure_direction", ""),
        "structure_ok": scan.get("structure_ok", False),
        "evaluation_status": scan.get("evaluation_status", "valid"),
        "direction": scan.get("direction", {"BUY": "BULLISH", "SELL": "BEARISH"}.get(scan.get("action"), "NEUTRAL")),
        "candle_meta": scan.get("candle_meta", {}),
        "atr_levels": scan.get("atr_levels", {}),
    }


# ──────────────────────────────────────────────
# Independent Screener API
# ──────────────────────────────────────────────

async def _dashboard_engine_for_screener(state: ScreenerUserState) -> UserEngine:
    """Load Dashboard settings without starting the Bots trading loop."""
    engine = user_manager.get_engine(state.uid)
    if not getattr(engine, "loaded_from_cloud", False):
        async with engine.load_lock:
            if not engine.loaded_from_cloud:
                await engine.load_from_cloud()
    return engine


async def _set_screener_dashboard_watchlist(
    state: ScreenerUserState,
    symbol: str,
    enabled: bool,
) -> dict:
    """Apply the profile checkbox to the user's persisted Dashboard watchlist.

    This intentionally changes only Dashboard visibility.  It never creates a
    Bot, changes a Screener profile's alerts, or starts an execution loop.
    """
    engine = await _dashboard_engine_for_screener(state)
    ticker = _normalize_quote_symbol(symbol)
    watchlist = engine.config.WATCHLIST
    if not isinstance(watchlist, list):
        watchlist = []
        engine.config.WATCHLIST = watchlist
    normalized_watchlist = []
    for item in watchlist:
        item_symbol = _normalize_quote_symbol(item)
        if item_symbol and item_symbol not in normalized_watchlist:
            normalized_watchlist.append(item_symbol)
    if normalized_watchlist != watchlist:
        engine.config.WATCHLIST = normalized_watchlist
        watchlist = normalized_watchlist

    changed = False
    if enabled and ticker not in watchlist:
        watchlist.append(ticker)
        changed = True
    elif not enabled and ticker in watchlist:
        watchlist.remove(ticker)
        changed = True

    if changed:
        await engine.save_settings()
        clear_evaluation_cache(ticker, None)
        _clear_chart_analysis_cache(ticker, None)
        print(f"[screener] Dashboard watchlist {'enabled' if enabled else 'disabled'} for {ticker}")
    return {
        "enabled": ticker in watchlist,
        "changed": changed,
        "watchlist": list(watchlist),
    }


def _screener_profile_from_request(data: dict, state: ScreenerUserState, symbol: str) -> dict:
    nested = data.get("settings") if isinstance(data.get("settings"), dict) else None
    incoming = dict(nested if nested is not None else data)
    for key in ("symbol", "ticker", "settings", "force_override", "add_to_watchlist"):
        incoming.pop(key, None)
    for key in (
        "capital", "amount", "allocation_mode", "compound_amount", "max_trades",
        "risk_per_trade", "max_daily_drawdown", "max_position_pct", "global_hard_stop_pct",
        "enable_risk_management", "enable_atr_trail", "enable_pct_trail", "regular_stop_loss_pct",
        "regular_take_profit_pct", "regular_trailing_pct",
        "options_trade_amount", "options_exit_strategy",
        "options_stop_loss_pct", "options_take_profit_pct", "options_trailing_pct",
    ):
        incoming.pop(key, None)
    # Alert source (Indicators / Strategy / Event-Driven / none) is exclusive and
    # enforced here, not trusted from the client. Strategy needs a preset; the
    # other real sources switch the preset off so its manual backup is restored.
    # "none" pauses alerts and leaves the preset state alone for a later resume.
    stored_profiles = getattr(state, "profiles", None)
    if not isinstance(stored_profiles, dict):
        stored_profiles = getattr(state.config, "TICKER_SETTINGS", {}) or {}
    prior_profile = stored_profiles.get(symbol) if isinstance(stored_profiles.get(symbol), dict) else {}
    requested_source = normalize_alert_source(incoming.get("alert_source"))
    if incoming.get("alert_source") is not None and requested_source is None:
        raise HTTPException(status_code=400, detail="alert_source must be indicators, strategy, event or none.")
    if requested_source == "strategy":
        preset_on, _, _ = strategy_presets.normalize_strategy_preset({**prior_profile, **incoming})
        if not preset_on:
            raise HTTPException(status_code=400, detail="Choose a strategy preset to use the Strategy alert source.")
    elif requested_source in {"indicators", "event"}:
        # Only switch a preset off when one is actually on; adding the disabled
        # keys to a profile that never had a preset would change its revision
        # and force a pointless baseline reset.
        if strategy_presets.normalize_strategy_preset({**prior_profile, **incoming})[0]:
            incoming["strategy_preset_enabled"] = False
            incoming["strategy_preset"] = "none"
    settings = _normalize_draft_bot_settings(symbol, incoming, state)
    if not settings.get("timeframe"):
        settings["timeframe"] = "5Min" if settings.get("options_trading") else "30Min"
    for key in (
        "amount", "capital", "allocation_mode", "compound_amount", "max_trades",
        "risk_per_trade", "max_daily_drawdown", "max_position_pct", "global_hard_stop_pct",
        "enable_risk_management", "enable_atr_trail", "enable_pct_trail", "regular_stop_loss_pct",
        "regular_take_profit_pct", "regular_trailing_pct",
        "options_trade_amount", "options_exit_strategy",
        "options_stop_loss_pct", "options_take_profit_pct", "options_trailing_pct",
        "enable_signal_reset",
    ):
        settings.pop(key, None)
    for key in list(settings):
        lowered = str(key).lower()
        if any(token in lowered for token in ("broker", "order", "position", "execution", "account")):
            settings.pop(key, None)
    if "threshold" in incoming and "min_buy_signals" not in incoming:
        settings["min_buy_signals"] = incoming["threshold"]
    if "sell_threshold" in incoming and "min_sell_signals" not in incoming:
        settings["min_sell_signals"] = incoming["sell_threshold"]
    settings["source"] = "screener"
    if "paused" in incoming and "enabled" not in incoming:
        settings["enabled"] = not bool(incoming["paused"])
    else:
        settings["enabled"] = bool(settings.get("enabled", not settings.get("paused", False)))
    settings["paused"] = not settings["enabled"]
    settings.setdefault("started_at", datetime.now(pytz.UTC).isoformat())
    settings.setdefault("asset_class", "crypto" if _is_crypto_symbol(symbol) else "stock")
    default_scan_interval = 10 if settings.get("options_trading") else 1800
    raw_scan_interval = incoming.get("scan_interval_seconds", settings.get("scan_interval_seconds"))
    auto_scan_interval = incoming.get(
        "scan_interval_auto",
        settings.get("scan_interval_auto", True),
    )
    settings["scan_interval_auto"] = bool(auto_scan_interval)
    try:
        settings["scan_interval_seconds"] = _screener_scan_interval_seconds({
            **settings,
            "scan_interval_seconds": raw_scan_interval,
            "scan_interval_auto": bool(auto_scan_interval),
        })
    except (TypeError, ValueError):
        settings["scan_interval_seconds"] = default_scan_interval
    try:
        settings["price_watch_interval_seconds"] = max(2, min(60, int(incoming.get("price_watch_interval_seconds", settings.get("price_watch_interval_seconds", 5)))))
    except (TypeError, ValueError):
        settings["price_watch_interval_seconds"] = 5
    settings["news_watch_enabled"] = bool(incoming.get("news_watch_enabled", settings.get("news_watch_enabled", True)))
    settings["earnings_watch_enabled"] = bool(incoming.get("earnings_watch_enabled", settings.get("earnings_watch_enabled", True)))
    settings["fundamental_watch_enabled"] = bool(incoming.get("fundamental_watch_enabled", settings.get("fundamental_watch_enabled", True)))
    # The screening policy (technical/event) is derived from the alert source and
    # kept in strategy_mode for scan logs, revisions and older clients. A client
    # that predates alert_source is mapped from its strategy_mode/preset fields;
    # legacy Combined/Discovery become Indicators (same rule as the load-time
    # migration), and Strategy wins over Event because they are exclusive.
    if requested_source is None:
        probe = dict(settings)
        if "strategy_mode" in incoming or "strategy_preset_enabled" in incoming or "strategy_preset" in incoming:
            probe.pop("alert_source", None)
        requested_source = derive_alert_source(probe)
    elif "alert_source" in incoming:
        settings.pop("migration_notice", None)  # saving an explicit choice acknowledges the conversion
    settings["alert_source"] = requested_source
    settings["strategy_mode"] = alert_source_policy(requested_source)
    # Keep the news watcher available for every Screener ticker so the News
    # Summary card is consistent across the watchlist. The selected policy
    # still decides whether this evidence can qualify an entry.
    settings["news_watch_enabled"] = True
    # News refresh follows the Scan Interval (see _screener_news_refresh_interval_seconds). The legacy fields are
    # only defaulted for new profiles; stored values are left alone so profile revisions stay stable.
    settings.setdefault("news_refresh_interval_auto", True)
    settings.setdefault("news_refresh_interval_seconds", _screener_news_refresh_interval_seconds(settings))
    try:
        settings["event_max_age_days"] = max(
            1,
            min(30, int(incoming.get("event_max_age_days", settings.get("event_max_age_days", 10)))),
        )
    except (TypeError, ValueError):
        settings["event_max_age_days"] = 10
    try:
        settings["event_min_confidence"] = max(
            0.0,
            min(1.0, float(incoming.get("event_min_confidence", settings.get("event_min_confidence", 0.55)))),
        )
    except (TypeError, ValueError):
        settings["event_min_confidence"] = 0.55
    for key in ("email_enabled", "alert_bullish_enabled", "alert_bearish_enabled"):
        if key in incoming:
            settings[key] = bool(incoming[key])
        elif key in settings:
            settings[key] = bool(settings[key])
    if "alert_cooldown_minutes" in incoming or "alert_cooldown_minutes" in settings:
        try:
            settings["alert_cooldown_minutes"] = max(0, min(1440, int(incoming.get("alert_cooldown_minutes", settings.get("alert_cooldown_minutes", 60)))))
        except (TypeError, ValueError):
            settings["alert_cooldown_minutes"] = 60

    # Anti-Chasing / Quality Guard persistence
    for key in ("enable_smart_reentry", "anti_chasing_enabled"):
        if key in incoming:
            settings[key] = bool(incoming[key])
        elif key in settings:
            settings[key] = bool(settings[key])
    if "enable_smart_reentry" in settings:
        settings["anti_chasing_enabled"] = settings["enable_smart_reentry"]
    elif "anti_chasing_enabled" in settings:
        settings["enable_smart_reentry"] = settings["anti_chasing_enabled"]

    if "smart_reentry_type" in incoming or "smart_reentry_type" in settings:
        reentry_type = str(incoming.get("smart_reentry_type", settings.get("smart_reentry_type", "RSI"))).upper()
        settings["smart_reentry_type"] = reentry_type if reentry_type in ("RSI", "VWAP") else "RSI"

    for rsi_key, default_val in (("smart_reentry_rsi_call_max", 70.0), ("smart_reentry_rsi_put_min", 30.0)):
        val = incoming.get(rsi_key, settings.get(rsi_key))
        if val is not None:
            try:
                settings[rsi_key] = max(1.0, min(99.0, float(val)))
            except (TypeError, ValueError):
                settings[rsi_key] = default_val

    for vwap_key, default_dist in (("smart_reentry_vwap_call_max_dist_pct", 0.5), ("smart_reentry_vwap_put_max_dist_pct", 0.5)):
        val = incoming.get(vwap_key, settings.get(vwap_key))
        if val is not None:
            try:
                settings[vwap_key] = max(0.01, min(10.0, float(val)))
            except (TypeError, ValueError):
                settings[vwap_key] = default_dist

    # Target Options Play Settings
    if "options_trading" in incoming:
        settings["options_trading"] = bool(incoming["options_trading"])
    elif "options_trading" in settings:
        settings["options_trading"] = bool(settings["options_trading"])

    if settings.get("options_trading"):
        raw_type = str(incoming.get("options_type", settings.get("options_type", "both"))).strip().lower()
        if raw_type in ("call", "calls", "calls_only"):
            settings["options_type"] = "calls_only"
            settings["alert_bullish_enabled"] = True
            settings["alert_bearish_enabled"] = False
        elif raw_type in ("put", "puts", "puts_only"):
            settings["options_type"] = "puts_only"
            settings["alert_bullish_enabled"] = False
            settings["alert_bearish_enabled"] = True
        else:
            settings["options_type"] = "both"
            settings.setdefault("alert_bullish_enabled", True)
            settings.setdefault("alert_bearish_enabled", True)

        try:
            settings["options_expiry_days"] = max(0, min(365, int(incoming.get("options_expiry_days", settings.get("options_expiry_days", 3)))))
        except (TypeError, ValueError):
            settings["options_expiry_days"] = 3

        strike_raw = str(incoming.get("options_strike_type", settings.get("options_strike_type", "ATM"))).strip().upper()
        settings["options_strike_type"] = strike_raw if strike_raw in ("ATM", "OTM", "ITM") else "ATM"

    settings["market_session"] = _normalize_screener_market_session(
        symbol,
        settings.get("market_session"),
        bool(settings.get("options_trading")),
    )
    settings["revision"] = profile_revision(settings)
    return settings


def _empty_screener_scan(symbol: str, settings: dict) -> dict:
    alert_source = derive_alert_source(settings)
    screening_mode = alert_source_policy(alert_source)
    direction_watch = _screener_direction_watch_config(settings)
    return {
        "ticker": symbol,
        "time": "",
        "timeframe": settings.get("timeframe", "30Min"),
        "price": "---",
        "price_raw": 0.0,
        "action": "HOLD",
        "reason": "Waiting for the first independent Screener scan",
        "bullish_count": 0,
        "bearish_count": 0,
        "total_signals": len(settings.get("indicators", []) or []),
        "signals": {},
        "log_type": "Screener",
        "scan_log": {
            "status": "WAITING_DATA",
            "phase": "WATCH",
            "target_direction": direction_watch["mode"],
            "decision": "NEUTRAL",
            "summary": "Waiting for the first completed-candle Screener evaluation.",
            "detail": "No trade is placed. The independent Screener is establishing its initial signal baseline.",
            "progress_label": f"{direction_watch['label']} watch",
            "direction_watch_mode": direction_watch["mode"],
            "direction_watch_label": direction_watch["label"],
            "watch_directions": direction_watch["directions"],
            "screening_strategy_mode": screening_mode,
            "screening_strategy_label": screening_strategy_label(screening_mode),
            "screening_strategy_logic": screening_strategy_logic(screening_mode),
            "alert_source": alert_source,
            "alert_source_label": alert_source_label(alert_source),
            "indicators": [],
            "blockers": ["Initial completed-candle evaluation pending."],
            "refresh_rate_sec": _screener_scan_interval_seconds(settings),
            "virtual_position": False,
            "position_source": "directional_alert_watch",
            "no_trade_placed": True,
        },
    }


def _build_screener_scan_log(
    symbol: str,
    formatted_scan: dict,
    settings: dict,
    evaluation_state: dict,
    alert_created: bool = False,
    exit_event: Optional[dict] = None,
    price_event: Optional[dict] = None,
) -> dict:
    """Build a position-free scan log for the independent Screener card.

    Directional alerts are watched independently.  The virtual lifecycle is
    retained only for optional analysis-only price-level monitoring; it must
    never force a bullish-first/bearish-second alert sequence or override the
    selected indicator/event qualification policy.
    """
    direction = str(formatted_scan.get("direction") or "NEUTRAL").upper()
    evaluation_status = str(formatted_scan.get("evaluation_status") or "waiting").lower()
    event_only_market_closed = bool(
        formatted_scan.get("event_only_market_closed")
        and normalize_screening_strategy_mode(
            formatted_scan.get("screening_strategy_mode", settings.get("strategy_mode", "combined"))
        ) == "event"
    )
    last_actionable = str(evaluation_state.get("last_actionable_direction") or "").upper()
    virtual_position = bool(evaluation_state.get("virtual_position", last_actionable == "BULLISH"))
    direction_watch = _screener_direction_watch_config(settings, evaluation_state)
    formatted_watch_mode = str(formatted_scan.get("direction_watch_mode") or "").strip().upper()
    if formatted_watch_mode in {"BOTH", "BULLISH", "BEARISH", "NONE"}:
        direction_watch = {
            "mode": formatted_watch_mode,
            "label": str(formatted_scan.get("direction_watch_label") or direction_watch_label(formatted_watch_mode)),
            "directions": list(formatted_scan.get("watch_directions") or direction_watch_directions(formatted_watch_mode)),
            "bullish_enabled": formatted_watch_mode in {"BOTH", "BULLISH"},
            "bearish_enabled": formatted_watch_mode in {"BOTH", "BEARISH"},
        }
    price_event = price_event or (
        exit_event
        if exit_event and str(exit_event.get("event_type") or "").upper() in {"PRICE_EXIT", "PRICE_ENTRY"}
        else None
    )
    price_event_type = str(price_event.get("event_type") or "").upper() if price_event else ""
    is_price_exit_event = price_event_type == "PRICE_EXIT"
    is_price_entry_event = price_event_type == "PRICE_ENTRY"
    exit_event_active = bool(is_price_exit_event and exit_event and exit_event.get("triggered"))
    target_direction = str(price_event.get("event_direction") or "BULLISH").upper() if is_price_entry_event else (
        "BEARISH" if is_price_exit_event or exit_event_active else direction_watch["mode"]
    )
    phase = "ENTRY" if is_price_entry_event else "EXIT" if is_price_exit_event or exit_event_active else "WATCH"
    screening_mode = normalize_screening_strategy_mode(
        formatted_scan.get("screening_strategy_mode", settings.get("strategy_mode", "combined"))
    )
    screening_qualification = (
        formatted_scan.get("strategy_qualification")
        if isinstance(formatted_scan.get("strategy_qualification"), dict)
        else {}
    )
    event_qualification = (
        formatted_scan.get("event_qualification")
        if isinstance(formatted_scan.get("event_qualification"), dict)
        else {}
    )
    technical_qualification = (
        formatted_scan.get("technical_qualification")
        if isinstance(formatted_scan.get("technical_qualification"), dict)
        else {}
    )
    screening_label = screening_strategy_label(screening_mode)
    screening_logic = screening_strategy_logic(screening_mode)
    strategy_context = _scan_log_strategy_context(
        settings,
        {"screener_virtual_signal": True} if is_price_exit_event or exit_event_active else {},
    )
    # Event-only watches must not report the technical/bot leg as active.
    if phase in {"ENTRY", "WATCH"} and screening_mode == "event":
        strategy_context = {
            **strategy_context,
            "mode": "event_driven",
            "label": "Event-Driven",
            "uses_indicators": False,
            "uses_structural": False,
            "is_hybrid": False,
        }
    strategy_mode = str(strategy_context["mode"] or "indicator")
    strategy_label = str(strategy_context["label"] or "Indicator")
    condition_logic = _scan_log_condition_logic(strategy_context)
    if phase in {"ENTRY", "WATCH"} and screening_mode != "technical":
        condition_logic = screening_logic if not condition_logic else f"{screening_logic} {condition_logic}"
    strategy_uses_indicators = bool(strategy_context["uses_indicators"])
    strategy_uses_structural = bool(strategy_context["uses_structural"])
    entry_levels = (
        _scan_log_entry_levels(symbol, formatted_scan, settings, target_direction)
        if phase in {"ENTRY", "WATCH"} and strategy_uses_structural
        else []
    )
    exit_levels = (
        exit_event
        if exit_event_active
        else _screener_exit_level_status(settings, formatted_scan, evaluation_state, strategy_mode)
        if phase == "EXIT" and strategy_uses_structural
        else None
    )
    exit_evidence = (
        _screener_exit_evidence(settings, formatted_scan, exit_levels, evaluation_state)
        if screening_mode != "event" and str(strategy_context["mode"] or "") not in {"event_driven", "strategy_preset"}
        else {}
    )
    bullish_required_count = _get_threshold_setting(
        settings, ("min_buy_signals", "threshold", "buy_threshold"), 1
    ) if strategy_uses_indicators else 0
    bearish_required_count = _get_threshold_setting(
        settings, ("min_sell_signals", "sell_threshold"), 1
    ) if strategy_uses_indicators else 0
    required_count = (
        bearish_required_count
        if target_direction == "BEARISH"
        else bullish_required_count
    ) if strategy_uses_indicators and target_direction != "BOTH" else 0

    selected = settings.get("indicators")
    selected_names = (
        {str(name).strip() for name in selected if str(name or "").strip()}
        if isinstance(selected, list)
        else None
    )
    has_indicator_pool = selected_names is None or bool(selected_names)
    indicators = []
    confirmed_weight = 0
    bullish_confirmed_weight = 0
    bearish_confirmed_weight = 0
    if strategy_uses_indicators:
        for name, raw in (formatted_scan.get("signals") or {}).items():
            if not isinstance(raw, dict) or raw.get("enabled") is False:
                continue
            if selected_names is not None and name not in selected_names:
                continue
            signal = str(raw.get("signal") or "NEUTRAL").upper()
            weight = max(1, int(raw.get("weight", 1) or 1))
            if target_direction == "BOTH":
                if signal == "BULLISH":
                    status = "BULLISH"
                    bullish_confirmed_weight += weight
                elif signal == "BEARISH":
                    status = "BEARISH"
                    bearish_confirmed_weight += weight
                else:
                    status = "WAITING"
            elif signal == "NEUTRAL":
                status = "WAITING"
            else:
                status = "OPPOSING"
            if target_direction != "BOTH" and signal == target_direction:
                status = "CONFIRMED"
                confirmed_weight += weight
            indicators.append({
                "name": name,
                "signal": signal,
                "status": status,
                "reason": str(raw.get("reason") or ""),
                "enabled": True,
                "supports_target": status == "CONFIRMED" or (target_direction == "BOTH" and status in {"BULLISH", "BEARISH"}),
                "weight": weight,
            })

    if target_direction == "BOTH":
        bullish_confirmed_weight = int(bullish_confirmed_weight)
        bearish_confirmed_weight = int(bearish_confirmed_weight)

    remaining_count = max(0, required_count - confirmed_weight)
    bullish_remaining_count = max(0, bullish_required_count - bullish_confirmed_weight)
    bearish_remaining_count = max(0, bearish_required_count - bearish_confirmed_weight)
    blockers = []
    candle_meta = formatted_scan.get("candle_meta") or {}
    if (evaluation_status != "valid" or not candle_meta.get("candle_key")) and not event_only_market_closed:
        blockers.append(str(formatted_scan.get("reason") or "Waiting for sufficient completed-candle market data."))
    if strategy_uses_indicators and not has_indicator_pool:
        blockers.append("No indicators selected. Select at least one indicator to enable this setup.")
    elif strategy_uses_indicators and not indicators:
        blockers.append("No enabled indicators have produced a valid reading yet.")
    elif phase in {"ENTRY", "WATCH"} and strategy_uses_structural and evaluation_status == "valid":
        structural_reason = str(formatted_scan.get("reason") or "").strip()
        if structural_reason:
            blockers.append(structural_reason)
    if settings.get("paused") or settings.get("enabled") is False:
        blockers.insert(0, "Screener profile is paused.")
    anti_chasing_blocked = str(formatted_scan.get("reason") or "").startswith("⚠️ Anti-chasing")
    if anti_chasing_blocked:
        blockers.append(str(formatted_scan.get("reason")))
    if (
        phase in {"ENTRY", "WATCH"}
        and screening_qualification
        and not bool(screening_qualification.get("qualified"))
    ):
        blockers.insert(0, f"{screening_label}: {screening_qualification.get('reason') or 'waiting for policy conditions.'}")

    policy_entry = phase in {"ENTRY", "WATCH"} and screening_mode in {"event", "combined", "discovery"} and bool(screening_qualification)
    policy_qualified = bool(screening_qualification.get("qualified")) if policy_entry else False
    policy_reason = str(
        screening_qualification.get("reason")
        or "waiting for policy conditions."
    ).strip()
    technical_reason = str(
        technical_qualification.get("reason")
        or "Indicator confirmation is not available yet."
    ).strip()
    if strategy_uses_indicators:
        if not has_indicator_pool:
            technical_watch_summary = "No indicators are selected."
        elif target_direction == "BOTH":
            bullish_text = f"{bullish_confirmed_weight}/{bullish_required_count} bullish"
            bearish_text = f"{bearish_confirmed_weight}/{bearish_required_count} bearish"
            needed = []
            if bullish_remaining_count:
                needed.append(f"{bullish_remaining_count} bullish confirmation{'s' if bullish_remaining_count != 1 else ''}")
            if bearish_remaining_count:
                needed.append(f"{bearish_remaining_count} bearish confirmation{'s' if bearish_remaining_count != 1 else ''}")
            technical_watch_summary = (
                f"Both-direction indicators: {bullish_text} and {bearish_text} confirmed"
                + (f"; waiting on {', '.join(needed)} confirmations." if needed else ".")
            )
        elif remaining_count:
            confirmation_word = "confirmation" if remaining_count == 1 else "confirmations"
            confirmation_verb = "is" if remaining_count == 1 else "are"
            technical_watch_summary = (
                f"{target_direction.title()} indicators are {confirmed_weight}/{required_count} confirmed; "
                f"{remaining_count} more {confirmation_word} {confirmation_verb} needed."
            )
        else:
            technical_watch_summary = (
                f"{target_direction.title()} indicators met the configured threshold "
                f"({confirmed_weight}/{required_count} confirmed)."
            )
    else:
        technical_watch_summary = "Indicator confirmation is not required for this policy."
    if strategy_uses_indicators and technical_reason:
        technical_watch_summary = f"{technical_watch_summary} Reason: {technical_reason}"

    event_direction = str(event_qualification.get("direction") or "NEUTRAL").upper()
    event_status = "QUALIFIED" if event_qualification.get("eligible") else "WAITING"
    event_reason = str(event_qualification.get("reason") or "No qualifying catalyst.").strip()
    event_watch_summary = f"{event_status} {event_direction}: {event_reason}"

    if evaluation_status == "error":
        status = "SCAN_ERROR"
        summary = f"Scan error while monitoring for {target_direction.lower()} confirmation."
    elif is_price_entry_event:
        status = "PRICE_ENTRY_TRIGGERED"
        summary = "Live custom-price entry triggered — " + str(
            price_event.get("text") or "configured entry price reached"
        ).rstrip(".") + "."
    elif (evaluation_status != "valid" or not candle_meta.get("candle_key")) and not event_only_market_closed:
        status = "WAITING_DATA"
        summary = f"Waiting for completed-candle data before checking the {strategy_label.lower()} {phase.lower()} setup."
    elif anti_chasing_blocked:
        status = "ANTI_CHASING_BLOCKED"
        summary = str(formatted_scan.get("reason"))
    elif policy_entry:
        status = f"{screening_mode.upper()}_QUALIFIED" if policy_qualified else f"WAITING_{screening_mode.upper()}"
        summary = (
            f"{screening_label} qualified: {policy_reason}"
            if policy_qualified
            else f"{screening_label} waiting: {policy_reason}"
        )
    elif strategy_uses_indicators and not has_indicator_pool:
        status = "NO_INDICATORS"
        summary = f"{strategy_label} {phase.lower()} is unavailable until at least one indicator is selected."
    elif phase == "WATCH" and direction_watch["mode"] != "NONE":
        status = f"WATCHING_{direction_watch['mode']}"
        current_signal = direction.title() if direction in {"BULLISH", "BEARISH"} else "Neutral"
        summary = (
            f"{direction_watch['label']} is active. Current completed-candle result: {current_signal}. "
            "Bullish and bearish confirmations are evaluated independently; the first selected direction that qualifies can alert."
        )
    elif phase == "EXIT" and is_price_exit_event:
        status = "PRICE_EXIT_TRIGGERED"
        summary = f"Live price exit triggered — {str(exit_levels.get('text') or 'configured price level reached.').rstrip('.')}."
    elif phase == "EXIT" and (
        (strategy_uses_indicators and confirmed_weight >= required_count)
        or bool(exit_levels and exit_levels.get("triggered"))
    ):
        status = "EXIT_SETUP_ACTIVE"
        summary = f"{strategy_label} exit condition is active on the latest completed candle."
    elif not strategy_uses_indicators:
        status = f"WAITING_{strategy_mode.upper()}_{phase}"
        summary = f"Waiting for {strategy_label} {phase.lower()} setup on the latest completed candle."
    else:
        status = "ALERTS_DISABLED" if direction_watch["mode"] == "NONE" else f"WAITING_{direction_watch['mode']}"
        summary = (
            "Directional alerts are disabled."
            if direction_watch["mode"] == "NONE"
            else f"Waiting for {direction_watch['label'].lower()} confirmation."
        )

    indicator_progress_label = (
        "No indicator pool selected"
        if strategy_uses_indicators and not has_indicator_pool
        else f"Bullish {bullish_confirmed_weight}/{bullish_required_count} · Bearish {bearish_confirmed_weight}/{bearish_required_count}"
        if strategy_uses_indicators and target_direction == "BOTH"
        else f"{target_direction} {confirmed_weight}/{required_count} confirmed"
        if strategy_uses_indicators
        else f"{strategy_label} {phase.title()} condition"
    )
    if strategy_uses_indicators and target_direction == "BOTH" and (bullish_remaining_count or bearish_remaining_count):
        needed = []
        if bullish_remaining_count:
            needed.append(f"{bullish_remaining_count} bullish")
        if bearish_remaining_count:
            needed.append(f"{bearish_remaining_count} bearish")
        indicator_progress_label += f", needs {', '.join(needed)}"
    elif strategy_uses_indicators and remaining_count:
        indicator_progress_label += f", needs {remaining_count}"
    progress_label = indicator_progress_label
    if entry_levels:
        progress_label += " · " + " | ".join(entry_levels[:2])
    if exit_levels and exit_levels.get("text"):
        progress_label += f" · {exit_levels['text']}"
    if condition_logic:
        progress_label += f" · {condition_logic}"
    detail_parts = [
        (
            f"Current event-driven qualification: {direction.title()}; indicator evaluation is paused while the market is closed."
            if event_only_market_closed
            else f"Current completed-candle direction: {direction.title()}."
        ),
        f"{progress_label.rstrip('.') }.",
        f"Direction watch: {direction_watch['label']}; selected directions are evaluated independently.",
        f"Screening policy: {screening_label}.",
        "No brokerage position or order is used by the Screener.",
    ]
    if alert_created:
        detail_parts.insert(
            0,
            "New live price entry alert created." if is_price_entry_event
            else "New live price exit alert created." if is_price_exit_event
            else f"New {direction.title()} transition alert created.",
        )
    if policy_entry:
        detail_parts.append(
            f"Policy qualification: {'QUALIFIED' if policy_qualified else 'WAITING'} — {policy_reason}."
        )
    if blockers:
        detail_parts.append("Waiting reason: " + " | ".join(blockers))

    if is_price_entry_event:
        trigger_text = str(price_event.get("text") or "configured custom entry price reached").rstrip(". ")
        final_summary = f"Live custom-price entry — {trigger_text}. The entry was triggered by the fresh quote level check."
    elif exit_levels and exit_levels.get("triggered") and is_price_exit_event:
        trigger_text = str(exit_levels.get("text") or "configured price level reached").rstrip(". ")
        final_summary = f"Live price exit — {trigger_text}. The exit was triggered by the fresh quote level check."
    elif exit_levels and exit_levels.get("triggered"):
        trigger_text = str(exit_levels.get("text") or "configured structural exit reached").rstrip(". ")
        if strategy_context.get("is_hybrid") and strategy_uses_indicators:
            final_summary = (
                f"Exit setup active — {trigger_text}. Bearish indicators: "
                f"{confirmed_weight}/{required_count} confirmed; indicator threshold is not required "
                "because the hybrid exit uses OR."
            )
        elif strategy_uses_indicators:
            final_summary = f"Exit setup active — {trigger_text}. Bearish indicators: {confirmed_weight}/{required_count} confirmed."
        else:
            final_summary = f"Exit setup active — {trigger_text}."
    elif phase == "EXIT" and strategy_uses_indicators and confirmed_weight >= required_count:
        if strategy_context.get("is_hybrid"):
            final_summary = (
                f"Exit setup active — bearish indicator threshold reached "
                f"({confirmed_weight}/{required_count} confirmed); ATR is not required "
                "because the hybrid exit uses OR."
            )
        else:
            final_summary = f"Exit setup active — bearish indicator threshold reached ({confirmed_weight}/{required_count} confirmed)."
    elif policy_entry:
        policy_watch_parts = []
        if screening_mode != "event":
            policy_watch_parts.append(f"Indicator: {technical_watch_summary}")
        policy_watch_parts.append(f"Event: {event_watch_summary}")
        if screening_mode == "event":
            policy_watch_parts.append("Indicator leg is not required.")
        elif screening_mode == "combined":
            policy_watch_parts.append("Both legs must qualify in the same direction.")
        final_summary = (
            f"Watch summary — {direction_watch['label']}. " + " ".join(policy_watch_parts)
        )
    elif strategy_uses_indicators and not has_indicator_pool:
        final_summary = (
            f"{phase.title()} unavailable — select at least one indicator before this "
            f"{strategy_label.lower()} setup can be evaluated."
        )
    elif phase == "EXIT" and strategy_uses_indicators:
        if strategy_context.get("is_hybrid"):
            indicator_wait = (
                f"{remaining_count} more confirmation{'s' if remaining_count != 1 else ''} needed for the indicator branch"
                if remaining_count
                else "indicator branch threshold met"
            )
            final_summary = (
                f"Exit watch summary — Bearish indicators are {confirmed_weight}/{required_count} confirmed; "
                f"{indicator_wait}. ATR levels are monitored in parallel, and either the bearish indicator "
                "threshold or an ATR stop/target event can trigger this exit."
            )
        else:
            final_summary = (
                f"Exit watch summary — Bearish indicators are {confirmed_weight}/{required_count} confirmed; "
                f"{remaining_count} more confirmation{'s' if remaining_count != 1 else ''} are needed before "
                "the indicator exit can trigger."
            )
    elif phase == "WATCH" and strategy_uses_indicators:
        if target_direction == "BOTH":
            final_summary = (
                f"Watch summary — Buy & Sell. Bullish indicators are {bullish_confirmed_weight}/{bullish_required_count} confirmed; "
                f"bearish indicators are {bearish_confirmed_weight}/{bearish_required_count} confirmed. "
                "Either selected direction can alert independently when its qualification policy is met."
            )
        elif remaining_count:
            confirmation_word = "confirmation" if remaining_count == 1 else "confirmations"
            confirmation_verb = "is" if remaining_count == 1 else "are"
            final_summary = (
                f"Watch summary — {direction_watch['label']}. {target_direction.title()} indicators are "
                f"{confirmed_weight}/{required_count} confirmed; {remaining_count} more {confirmation_word} "
                f"{confirmation_verb} needed before this direction can alert."
            )
        else:
            final_summary = (
                f"Watch summary — {direction_watch['label']}. {target_direction.title()} indicators met the configured threshold "
                f"({confirmed_weight}/{required_count} confirmed); this direction can alert independently."
            )
    elif phase == "ENTRY" and strategy_uses_indicators:
        if remaining_count:
            final_summary = (
                f"Entry watch summary — Bullish indicators are {confirmed_weight}/{required_count} confirmed; "
                f"{remaining_count} more confirmation{'s' if remaining_count != 1 else ''} are needed before "
                "the entry threshold can trigger."
            )
        else:
            final_summary = (
                f"Entry watch summary — Bullish indicators met the configured threshold "
                f"({confirmed_weight}/{required_count} confirmed); the entry condition can trigger."
            )
    else:
        final_summary = summary

    return {
        "summary": summary,
        "detail": " ".join(detail_parts),
        "status": status,
        "phase": phase,
        "decision": target_direction if is_price_entry_event else direction,
        "target_direction": target_direction,
        "direction_watch_mode": direction_watch["mode"],
        "direction_watch_label": direction_watch["label"],
        "watch_directions": direction_watch["directions"],
        "strategy_mode": strategy_mode,
        "strategy_label": strategy_label,
        "screening_strategy_mode": screening_mode,
        "screening_strategy_label": screening_label,
        "screening_strategy_logic": screening_logic,
        "alert_source": formatted_scan.get("alert_source") or derive_alert_source(settings),
        "alert_source_label": alert_source_label(formatted_scan.get("alert_source") or derive_alert_source(settings)),
        "technical_direction": formatted_scan.get("technical_direction", direction),
        "technical_qualification": formatted_scan.get("technical_qualification", {}),
        "event_qualification": event_qualification,
        "strategy_qualification": screening_qualification,
        "condition_logic": condition_logic,
        "uses_indicators": strategy_uses_indicators,
        "uses_structural": strategy_uses_structural,
        "entry_levels": entry_levels,
        "exit_levels": exit_levels or {},
        "exit_evidence": exit_evidence,
        "required_count": required_count,
        "confirmed_count": sum(item["status"] == "CONFIRMED" for item in indicators),
        "confirmed_weight": confirmed_weight,
        "remaining_count": remaining_count,
        "bullish_required_count": bullish_required_count,
        "bullish_confirmed_weight": bullish_confirmed_weight,
        "bullish_remaining_count": bullish_remaining_count,
        "bearish_required_count": bearish_required_count,
        "bearish_confirmed_weight": bearish_confirmed_weight,
        "bearish_remaining_count": bearish_remaining_count,
        "indicator_progress_label": indicator_progress_label if strategy_uses_indicators else "",
        "progress_label": progress_label,
        "final_summary": final_summary,
        "indicators": indicators,
        "blockers": blockers,
        "updated_at": formatted_scan.get("scanned_at") or datetime.now(pytz.UTC).isoformat(),
        "refresh_rate_sec": _screener_scan_interval_seconds(settings),
        "next_due_at": evaluation_state.get("next_due_at"),
        "candle_key": candle_meta.get("candle_key"),
        "candle_open_ts": candle_meta.get("candle_open_ts"),
        "candle_close_ts": candle_meta.get("candle_close_ts"),
        "event_only_market_closed": event_only_market_closed,
        "virtual_position": virtual_position,
        "exit_event_active": exit_event_active,
        "event_type": price_event_type if price_event_type in {"PRICE_EXIT", "PRICE_ENTRY"} else "CANDLE_SIGNAL",
        "price_watch": formatted_scan.get("price_watch") or {},
        "position_source": "virtual_price_level_watch" if phase == "EXIT" else "directional_alert_watch",
        "no_trade_placed": True,
    }


def _screener_profile_is_current(state, symbol, revision, generation):
    profile = state.profiles.get(symbol, {})
    return bool(
        profile
        and profile.get("enabled", not profile.get("paused", False))
        and state.profile_revision(symbol) == revision
        and state.evaluation_state.get(symbol, {}).get("generation") == generation
    )


def _mark_screener_scan_degraded(state, symbol, settings, reason):
    scan = copy.deepcopy(state.latest_scans.get(symbol) or _empty_screener_scan(symbol, settings))
    scan.update(data_status="degraded", stale_reason=reason)
    scan.setdefault("scan_log", {})["next_due_at"] = state.evaluation_state.get(symbol, {}).get("next_due_at")
    state.latest_scans[symbol] = scan


def _project_screener_scan(state, symbol):
    """Read-only freshness projection; never relabel old data as a new signal."""
    settings = state.profiles.get(symbol, {})
    scan = copy.deepcopy(state.latest_scans.get(symbol) or _empty_screener_scan(symbol, settings))
    if not settings.get("enabled", not settings.get("paused", False)):
        scan["data_status"] = "paused"
    elif (scan.get("scan_log") or {}).get("status") == "SCHEDULE":
        # Session Rider is clock-driven: there is no candle to go stale, so the card is current whenever it ran.
        scan["data_status"] = "current"
    elif scan.get("evaluation_status") != "valid":
        scan.setdefault("data_status", "waiting")
    elif scan.get("data_status") not in {"degraded", "stale_reference"}:
        session = _screener_market_status(symbol, settings)
        if not _screener_candle_is_usable(scan.get("candle_meta") or {}, settings.get("timeframe", "30Min"), session["is_active_window"]):
            scan.update(data_status="degraded", stale_reason="The last completed-candle analysis is no longer current. Waiting for fresh data.")
        else:
            scan["data_status"] = "current" if session["is_active_window"] else "session_closed"
    return scan


def _screener_summary(state: ScreenerUserState, symbols: list[str], scans: Optional[dict] = None) -> dict:
    enabled_symbols = [
        symbol for symbol in symbols
        if bool(state.profiles.get(symbol, {}).get("enabled", True))
    ]
    projected = scans if scans is not None else {symbol: _project_screener_scan(state, symbol) for symbol in enabled_symbols}
    valid_scans = {symbol: projected[symbol] for symbol in enabled_symbols if projected.get(symbol, {}).get("evaluation_status") == "valid" and projected.get(symbol, {}).get("data_status") == "current"}
    tz = pytz.timezone(getattr(state.config, "TIMEZONE", "America/Chicago"))
    today = datetime.now(tz).date()
    alerts_today = 0
    alerts_today_by_direction = {"bullish": 0, "bearish": 0}
    visible_notifications = _screener_visible_notifications(state)
    for item in visible_notifications:
        try:
            created = item.get("created_at")
            if isinstance(created, str):
                created = datetime.fromisoformat(created.replace("Z", "+00:00")).timestamp()
            if created and datetime.fromtimestamp(float(created), tz).date() == today:
                alerts_today += 1
                direction = str(item.get("direction") or "").lower()
                if direction in alerts_today_by_direction:
                    alerts_today_by_direction[direction] += 1
        except (TypeError, ValueError, OverflowError):
            continue
    unread = sum(not bool(item.get("read")) for item in visible_notifications)
    email_queued = sum(item.get("status") in {"queued", "sending", "retrying"} for item in state.email_outbox)
    email_failed = sum(item.get("status") == "failed" for item in state.email_outbox)
    sms_queued = sum(item.get("status") in {"queued", "sending", "retrying"} for item in state.sms_outbox)
    sms_failed = sum(item.get("status") == "failed" for item in state.sms_outbox)
    sms_enabled = bool(state.notification_settings.get("sms_enabled"))
    sms_phone_configured = bool(normalize_screener_sms_phone(state.notification_settings.get("sms_phone_number")))
    health = dict(state.health)
    health.setdefault("status", "starting")
    degraded_count = sum(projected.get(symbol, {}).get("data_status") in {"degraded", "stale_reference"} for symbol in enabled_symbols)
    if degraded_count and health["status"] == "healthy":
        health["status"] = "degraded"
    return {
        "monitoring": health,
        "profiles": {
            "enabled": len(enabled_symbols),
            "total": len(symbols),
            "paused": max(0, len(symbols) - len(enabled_symbols)),
        },
        "setups": {
            "bullish": sum(scan.get("direction") == "BULLISH" for scan in valid_scans.values()),
            "bearish": sum(scan.get("direction") == "BEARISH" for scan in valid_scans.values()),
            "neutral": sum(scan.get("direction") == "NEUTRAL" for scan in valid_scans.values()),
            "valid": len(valid_scans),
            "unavailable": len(enabled_symbols) - len(valid_scans),
            "degraded": degraded_count,
        },
        "alerts_today": alerts_today,
        "alerts_today_by_direction": alerts_today_by_direction,
        "unread_alerts": unread,
        "browser_delivery": {"enabled": bool(state.notification_settings.get("browser_enabled", True))},
        "email_delivery": {
            "enabled": bool(state.notification_settings.get("email_enabled")),
            "queued": email_queued,
            "failed": email_failed,
        },
        "sms_delivery": {
            "enabled": sms_enabled,
            "phone_configured": sms_phone_configured,
            "provider_configured": _sms_provider_configured(),
            "queued": sms_queued,
            "failed": sms_failed,
        },
        "timezone": str(getattr(state.config, "TIMEZONE", "America/Chicago")),
    }


@app.get("/api/screener/snapshot")
async def get_screener_snapshot(
    response: Response,
    partition: str = "all",
    ticker: str = None,
    force: bool = False,
    state: ScreenerUserState = Depends(get_screener_state),
):
    snapshot_started = time.perf_counter()
    partition_norm = partition if partition in {"all", "regular", "options", "ticker"} else "all"
    ticker_norm = normalize_screener_symbol(ticker) if ticker else ""
    # GET must remain a read-only projection. In particular, a browser refresh
    # or preview request must not create a technical alert as a side effect.
    # Use POST /api/screener/scan for an explicit, user-requested scan.

    symbols = []
    for symbol in state.order:
        if partition_norm == "ticker" and symbol != ticker_norm:
            continue
        is_option = bool(state.profiles.get(symbol, {}).get("options_trading", False))
        if partition_norm == "options" and not is_option:
            continue
        if partition_norm == "regular" and is_option:
            continue
        symbols.append(symbol)

    scans = {}
    for symbol in symbols:
        scan = _project_screener_scan(state, symbol)
        # Chart candles are loaded by the dedicated chart-preview endpoint.
        # Keeping them out of the polling snapshot prevents multi-megabyte
        # responses and repeated JSON parsing on every 60-second refresh.
        for heavy_key in ("price_history", "priceHistory", "raw_candles", "candles", "df"):
            scan.pop(heavy_key, None)
        scan["news_watch"] = copy.deepcopy(state.news_watch.get(symbol) or {
            "enabled": True,
            "status": "waiting",
            "ticker": symbol,
            "poll_seconds": 60,
            "new_count": 0,
        })
        if state.profiles.get(symbol, {}).get("earnings_watch_enabled", True) and not _is_crypto_symbol(symbol):
            scan["earnings_watch"] = earnings_with_current_countdown(state.earnings_watch.get(symbol) or {
                "enabled": True,
                "status": "waiting",
                "ticker": symbol,
                "new_count": 0,
            })
        else:
            scan["earnings_watch"] = {"enabled": False, "status": "disabled", "ticker": symbol}
        if state.profiles.get(symbol, {}).get("fundamental_watch_enabled", True) and not _is_crypto_symbol(symbol):
            report = state.fundamental_reports.get(symbol) or {}
            report_status = report.get("status", "waiting")
            try:
                if report_status == "current" and float(report.get("next_check_at", 0) or 0) <= time.time():
                    report_status = "stale"
            except (TypeError, ValueError, OverflowError):
                report_status = "stale" if report_status == "current" else report_status
            scan["fundamental_watch"] = {
                "enabled": True,
                "status": report_status,
                "ticker": symbol,
                "assessment": report.get("assessment", ""),
                "score": report.get("score"),
                "company_name": (report.get("company") or {}).get("name", symbol),
                "checked_at": report.get("checked_at", ""),
                "next_check_at": report.get("next_check_at"),
                "error": report.get("error", ""),
            }
        else:
            scan["fundamental_watch"] = {"enabled": False, "status": "disabled", "ticker": symbol}
        scans[symbol] = scan
    summary = _screener_summary(state, symbols, scans=scans)
    enabled_count = summary["profiles"]["enabled"]
    buy_count = summary["setups"]["bullish"]
    sell_count = summary["setups"]["bearish"]
    neutral_count = summary["setups"]["neutral"]
    dashboard_engine = await _dashboard_engine_for_screener(state)
    dashboard_watchlist = list(getattr(dashboard_engine.config, "WATCHLIST", []) or [])
    response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate"
    response.headers["X-Snapshot-Time-MS"] = str(round((time.perf_counter() - snapshot_started) * 1000, 1))
    # A NaN anywhere in a cached scan (for example an unavailable indicator value) made the JSON encoder raise, and the
    # 500 reached the browser as a CORS error. Sanitise at the API boundary like the other endpoints.
    return _sanitize_json_values({
        "schema_version": "1.0",
        "partition": partition_norm,
        "tradelist": symbols,
        "bot_scans": scans,
        "botScans": scans,
        "ticker_settings": {symbol: state.profiles[symbol] for symbol in symbols},
        "positions": [],
        "pendingOrders": [],
        "open_orders": [],
        "account": {"equity": len(symbols), "cash": enabled_count, "dailyPL": buy_count, "dailyPLPct": 0},
        "capital": str(len(symbols)),
        "cash": str(enabled_count),
        "openPositions": str(sell_count),
        "positionsList": f"{neutral_count} neutral",
        "dailyPL": f"{buy_count} buy",
        "totalProfit": f"{sell_count} sell",
        "has_keys": True,
        "simulation": False,
        "indicator_parameters": state.config.contexts.get("screener", {}).get("parameters", {}),
        "global_risk_status": {},
        "page_settings": state.page_settings,
        "watchlist": dashboard_watchlist,
        "screener_summary": summary,
        "notifications": {
            "unread_count": summary["unread_alerts"],
            "alerts_today": summary["alerts_today"],
        },
        "snapshot_seq": int(time.time() * 1000),
        "trade_event_seq": len(state.activity),
        "generated_at": datetime.now(pytz.UTC).isoformat(),
    })


@app.get("/api/screener/profiles/{ticker}")
async def get_screener_profile(ticker: str, state: ScreenerUserState = Depends(get_screener_state)):
    symbol = normalize_screener_symbol(ticker)
    if not symbol:
        raise HTTPException(status_code=400, detail="Invalid ticker format")
    if symbol not in state.profiles:
        raise HTTPException(status_code=404, detail=f"Screener profile not found for '{symbol}'")
    return {"ticker": symbol, "settings": state.profiles[symbol], "fetched_at": datetime.now(pytz.UTC).isoformat()}


@app.get("/api/screener/earnings/{ticker}")
async def get_screener_earnings(ticker: str, force: bool = False, state: ScreenerUserState = Depends(get_screener_state)):
    symbol = normalize_screener_symbol(ticker)
    if not symbol:
        raise HTTPException(status_code=400, detail="Invalid ticker format")
    settings = state.profiles.get(symbol)
    if not settings:
        raise HTTPException(status_code=404, detail=f"Screener profile not found for '{symbol}'")
    if not settings.get("earnings_watch_enabled", True) or _is_crypto_symbol(symbol):
        return {"enabled": False, "status": "disabled", "ticker": symbol}
    cached = state.earnings_watch.get(symbol)
    if force or not cached or cached.get("status") in {"waiting", "unavailable"} or cached.get("data_version") != EARNINGS_DATA_VERSION:
        cached = await asyncio.wait_for(
            asyncio.to_thread(
                get_earnings_watch_detail,
                symbol,
                None if force else cached,
                bool(settings.get("options_trading")),
            ),
            timeout=50.0,
        )
        state.earnings_watch[symbol] = cached
        await state.save()
    return earnings_with_current_countdown(cached)


@app.get("/api/screener/fundamentals/{ticker}")
async def get_screener_fundamentals(ticker: str, force: bool = False, state: ScreenerUserState = Depends(get_screener_state)):
    symbol = normalize_screener_symbol(ticker)
    if not symbol:
        raise HTTPException(status_code=400, detail="Invalid ticker format")
    settings = state.profiles.get(symbol)
    if not settings:
        raise HTTPException(status_code=404, detail=f"Screener profile not found for '{symbol}'")
    if not settings.get("fundamental_watch_enabled", True) or _is_crypto_symbol(symbol):
        return {"enabled": False, "status": "not_applicable" if _is_crypto_symbol(symbol) else "disabled", "ticker": symbol}
    cached = state.fundamental_reports.get(symbol)
    try:
        cache_due = not cached or float(cached.get("next_check_at", 0) or 0) <= time.time()
    except (TypeError, ValueError, OverflowError):
        cache_due = True
    if force or cache_due or cached.get("status") in {"waiting", "unavailable"} or cached.get("report_version") != FUNDAMENTAL_REPORT_VERSION:
        cached = await asyncio.wait_for(asyncio.to_thread(
            get_fundamental_report_detail,
            symbol,
            None if force else cached,
            bool(settings.get("options_trading")),
        ), timeout=50.0)
        state.fundamental_reports[symbol] = cached
        await state.save()
    return copy.deepcopy(cached)


# ── Bots page research badges (Earnings / Fundamentals) ─────────────────────
# Same public-data services the Screener uses, but keyed to the user's bots and cached on the engine, so a
# bot never needs a Screener profile and the Screener's state is untouched.
_BOT_RESEARCH_SEMAPHORE = asyncio.Semaphore(3)
_BOT_RESEARCH_TIMEOUT = 50.0


def _bot_research_symbol(eng: UserEngine, ticker: str) -> str:
    symbol = normalize_screener_symbol(ticker)
    if not symbol:
        raise HTTPException(status_code=400, detail="Invalid ticker format")
    if symbol not in (eng.config.TICKER_SETTINGS or {}) and symbol not in (eng.config.TRADELIST or []):
        raise HTTPException(status_code=404, detail=f"No bot found for '{symbol}'")
    return symbol


def _bot_research_cache(eng: UserEngine, kind: str) -> dict:
    cache = getattr(eng, "bot_research_cache", None)
    if cache is None:
        cache = eng.bot_research_cache = {"earnings": {}, "fundamentals": {}}
    return cache[kind]


def _bot_research_enabled(eng: UserEngine, symbol: str, key: str) -> bool:
    """Per-bot switch (default on, like the Screener profile): ``earnings_watch_enabled`` / ``fundamental_watch_enabled``."""
    settings = (eng.config.TICKER_SETTINGS or {}).get(symbol) or {}
    return settings.get(key, True) is not False


def _bot_is_options(eng: UserEngine, symbol: str) -> bool:
    settings = (eng.config.TICKER_SETTINGS or {}).get(symbol) or {}
    return bool(settings.get("options_trading"))


async def _bot_earnings(eng: UserEngine, symbol: str, force: bool = False) -> dict:
    cache = _bot_research_cache(eng, "earnings")
    cached = cache.get(symbol)
    try:
        due = not cached or float(cached.get("next_check_at", 0) or 0) <= time.time()
    except (TypeError, ValueError, OverflowError):
        due = True
    if force or due or cached.get("status") in {"waiting", "unavailable"} or cached.get("data_version") != EARNINGS_DATA_VERSION:
        async with _BOT_RESEARCH_SEMAPHORE:
            cache[symbol] = await asyncio.wait_for(
                asyncio.to_thread(build_earnings_watch, symbol, None if force else cached, _bot_is_options(eng, symbol), bool(force)),
                timeout=_BOT_RESEARCH_TIMEOUT)
    return earnings_with_current_countdown(cache[symbol])


@app.get("/api/bots/earnings/{ticker}")
async def get_bot_earnings(ticker: str, force: bool = False, eng: UserEngine = Depends(get_user_engine)):
    symbol = _bot_research_symbol(eng, ticker)
    if _is_crypto_symbol(symbol) or not _bot_research_enabled(eng, symbol, "earnings_watch_enabled"):
        return {"enabled": False, "status": "disabled", "ticker": symbol}
    return await _bot_earnings(eng, symbol, force)


async def _bot_fundamentals(eng: UserEngine, symbol: str, force: bool = False) -> dict:
    cache = _bot_research_cache(eng, "fundamentals")
    cached = cache.get(symbol)
    try:
        due = not cached or float(cached.get("next_check_at", 0) or 0) <= time.time()
    except (TypeError, ValueError, OverflowError):
        due = True
    if force or due or cached.get("status") in {"waiting", "unavailable"} or cached.get("report_version") != FUNDAMENTAL_REPORT_VERSION:
        async with _BOT_RESEARCH_SEMAPHORE:
            cache[symbol] = await asyncio.wait_for(
                asyncio.to_thread(get_fundamental_report_detail, symbol, None if force else cached, _bot_is_options(eng, symbol)),
                timeout=_BOT_RESEARCH_TIMEOUT)
    return copy.deepcopy(cache[symbol])


@app.get("/api/bots/fundamentals/{ticker}")
async def get_bot_fundamentals(ticker: str, force: bool = False, eng: UserEngine = Depends(get_user_engine)):
    symbol = _bot_research_symbol(eng, ticker)
    if _is_crypto_symbol(symbol):
        return {"enabled": False, "status": "not_applicable", "ticker": symbol}
    if not _bot_research_enabled(eng, symbol, "fundamental_watch_enabled"):
        return {"enabled": False, "status": "disabled", "ticker": symbol}
    return await _bot_fundamentals(eng, symbol, force)


_bot_fundamental_tasks: dict = {}


def _fundamental_badge_view(report: Optional[dict]) -> dict:
    """The few fields a badge needs (the full report is fetched only when the dialog opens)."""
    report = report or {}
    return {key: report.get(key) for key in ("status", "assessment", "score")}


async def _bot_fundamentals_for_badge(eng: UserEngine, symbol: str) -> dict:
    """Cached fundamentals for a badge. A missing or due report is built once in the background; the badge shows
    "Checking" until it lands and the next badge refresh picks it up. Never waits more than a few seconds."""
    cache = _bot_research_cache(eng, "fundamentals")
    cached = cache.get(symbol)
    try:
        fresh = bool(cached) and cached.get("status") == "current" and cached.get("report_version") == FUNDAMENTAL_REPORT_VERSION \
            and float(cached.get("next_check_at", 0) or 0) > time.time()
    except (TypeError, ValueError, OverflowError):
        fresh = False
    if fresh:
        return _fundamental_badge_view(cached)
    key = (eng.uid, symbol)
    task = _bot_fundamental_tasks.get(key)
    if task is None or task.done():
        task = asyncio.create_task(_bot_fundamentals(eng, symbol))
        _bot_fundamental_tasks[key] = task

        def _finished(done, key=key):
            if _bot_fundamental_tasks.get(key) is done:
                _bot_fundamental_tasks.pop(key, None)
            if not done.cancelled():
                done.exception()  # retrieved here; a failed build leaves the previous report in place

        task.add_done_callback(_finished)
    try:
        return _fundamental_badge_view(await asyncio.wait_for(asyncio.shield(task), timeout=6.0))
    except Exception:
        return _fundamental_badge_view(cached) if cached else {"status": "waiting"}


@app.get("/api/bots/earnings-badges")
async def get_bot_earnings_badges(eng: UserEngine = Depends(get_user_engine)):
    """Compact earnings badge data for every stock bot (refreshes only what is due; a failure keeps the last value)."""
    symbols = [normalize_screener_symbol(t) for t in dict.fromkeys(eng.config.TRADELIST or [])]
    symbols = [t for t in symbols if t and not _is_crypto_symbol(t) and _bot_research_enabled(eng, t, "earnings_watch_enabled")]

    async def one(symbol: str):
        try:
            return symbol, await _bot_earnings(eng, symbol)
        except Exception as exc:
            print(f"[bots] Earnings badge failed for {symbol}: {_safe_exception_type(exc)}")
            return symbol, _bot_research_cache(eng, "earnings").get(symbol) or {"status": "unavailable", "ticker": symbol}

    results = await asyncio.gather(*(one(s) for s in symbols))
    keep = ("status", "event_date", "last_event_date", "days_to_event", "risk_level", "initialized", "timing")
    fundamental_symbols = [
        sym for sym in dict.fromkeys(normalize_screener_symbol(t) for t in (eng.config.TRADELIST or []))
        if sym and not _is_crypto_symbol(sym) and _bot_research_enabled(eng, sym, "fundamental_watch_enabled")
    ]
    fundamentals = dict(zip(fundamental_symbols, await asyncio.gather(*(_bot_fundamentals_for_badge(eng, sym) for sym in fundamental_symbols))))
    return {"badges": {sym: {k: data.get(k) for k in keep} for sym, data in results}, "fundamentals": fundamentals}


async def _scan_screener_after_save(state: ScreenerUserState, symbol: str) -> None:
    """Two forced scans, one after the other, when a profile is created or its settings change.

    The first scan evaluates the last completed candles (a closed session included, for a profile that has no valid
    scan yet); the second settles the card on the last scan result, and a closed session then keeps that analysis in
    place. They run sequentially in one task so they can never overlap, and a failure of the first is reported by the
    scan itself as degraded data (the second still runs, as a retry).
    """
    for attempt in (1, 2):
        try:
            await _scan_screener_state(state, [symbol], force_scan=True)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            print(f"[screener] Post-save scan {attempt} failed for {symbol}: {_safe_exception_type(exc)}")
        if attempt == 1:
            await asyncio.sleep(2)


@app.post("/api/screener/profiles")
async def save_screener_profile(data: dict, state: ScreenerUserState = Depends(get_screener_state)):
    symbol = normalize_screener_symbol(data.get("symbol") or data.get("ticker"))
    if not symbol:
        raise HTTPException(status_code=400, detail="A valid symbol is required")
    exists = symbol in state.profiles
    if not exists and len(state.profiles) >= MAX_SCREENER_PROFILES:
        raise HTTPException(status_code=413, detail=f"Screener supports a maximum of {MAX_SCREENER_PROFILES} total profiles")
    enabled_profiles = sum(bool(profile.get("enabled", True)) for profile in state.profiles.values())
    incoming_settings = data.get("settings") if isinstance(data.get("settings"), dict) else data
    requested_enabled = bool((incoming_settings or {}).get("enabled", not bool((incoming_settings or {}).get("paused", False))))
    if (not exists or (not bool(state.profiles.get(symbol, {}).get("enabled", False)) and requested_enabled)) and enabled_profiles >= 50:
        raise HTTPException(status_code=400, detail="Screener supports a maximum of 50 enabled profiles")
    if exists and data.get("settings") is None and not data.get("force_override", False):
        return {"status": "error", "message": f"A Screener profile for '{symbol}' already exists."}
    previous_profile = copy.deepcopy(state.profiles.get(symbol) or {})
    previous_revision = state.profile_revision(symbol) if exists else None
    previous_enabled = bool(previous_profile.get("enabled", False)) if exists else False
    previous_news_enabled = bool(previous_profile.get("news_watch_enabled", True)) if exists else False
    previous_news_interval = _screener_news_refresh_interval_seconds(previous_profile) if exists else None
    previous_earnings_enabled = bool(previous_profile.get("earnings_watch_enabled", True)) if exists else False
    previous_fundamental_enabled = bool(previous_profile.get("fundamental_watch_enabled", True)) if exists else False
    next_profile = _screener_profile_from_request(data, state, symbol)
    dashboard_watchlist = None
    if "add_to_watchlist" in data:
        dashboard_watchlist = await _set_screener_dashboard_watchlist(
            state,
            symbol,
            bool(data.get("add_to_watchlist")),
        )
    state.profiles[symbol] = next_profile
    settings_changed = (
        not exists
        or _screener_profile_fingerprint(previous_profile)
        != _screener_profile_fingerprint(state.profiles[symbol])
    )
    now_enabled = bool(state.profiles[symbol].get("enabled", True))
    news_enabled = bool(state.profiles[symbol].get("news_watch_enabled", True))
    earnings_enabled = bool(state.profiles[symbol].get("earnings_watch_enabled", True))
    fundamental_enabled = bool(state.profiles[symbol].get("fundamental_watch_enabled", True))
    technical_scan_changed = (
        not exists
        or previous_revision != state.profile_revision(symbol)
        or (not previous_enabled and now_enabled)
    )
    if technical_scan_changed:
        state.mark_profile_baseline(symbol, "profile_settings_changed" if exists else "profile_activated")
    if symbol not in state.order:
        state.order.insert(0, symbol)   # a new profile goes on top, like a new bot
    if technical_scan_changed:
        state.latest_scans.pop(symbol, None)
    await state.save()
    scan_inputs_changed = (
        not exists
        or _screener_profile_fingerprint(previous_profile, _SCREENER_WATCH_TOGGLE_KEYS)
        != _screener_profile_fingerprint(state.profiles[symbol], _SCREENER_WATCH_TOGGLE_KEYS)
    )
    if scan_inputs_changed and now_enabled and derive_alert_source(state.profiles[symbol]) != "none":
        # (Alerts paused: nothing to scan, so no forced scan is started.)
        # A settings save gets one immediate fresh result, even outside the
        # selected market window. Normal scheduler passes remain session- and
        # completed-candle-gated.
        state.force_scan.set()
        asyncio.create_task(_scan_screener_after_save(state, symbol))
    news_interval_changed = previous_news_interval != _screener_news_refresh_interval_seconds(state.profiles[symbol])
    if news_enabled and (not previous_news_enabled or symbol not in state.news_watch or news_interval_changed):
        asyncio.create_task(_watch_screener_news_state(state, [symbol]))
    if earnings_enabled and (not previous_earnings_enabled or symbol not in state.earnings_watch):
        asyncio.create_task(_watch_screener_earnings_state(state, [symbol]))
    if fundamental_enabled and (not exists or not previous_fundamental_enabled) and not _is_crypto_symbol(symbol):
        asyncio.create_task(_watch_screener_fundamental_state(state, [symbol]))
    return {
        "status": "success",
        "symbol": symbol,
        "tradelist": state.order,
        "settings": state.profiles[symbol],
        "settings_changed": settings_changed,
        "technical_scan_changed": technical_scan_changed,
        "news_watch_enabled": news_enabled,
        "news_refresh_interval_seconds": _screener_news_refresh_interval_seconds(state.profiles[symbol]),
        "earnings_watch_enabled": earnings_enabled,
        "fundamental_watch_enabled": fundamental_enabled,
        "dashboard_watchlist": dashboard_watchlist.get("watchlist", []) if dashboard_watchlist else None,
        "dashboard_watchlist_enabled": dashboard_watchlist.get("enabled") if dashboard_watchlist else None,
    }


@app.get("/api/screener/dashboard-watchlist/{ticker}")
async def get_screener_dashboard_watchlist(ticker: str, state: ScreenerUserState = Depends(get_screener_state)):
    """Return the actual Dashboard watchlist state for a Screener profile form."""
    symbol = normalize_screener_symbol(ticker)
    if not symbol:
        raise HTTPException(status_code=400, detail="Invalid ticker format")
    engine = await _dashboard_engine_for_screener(state)
    watchlist = [_normalize_quote_symbol(item) for item in (engine.config.WATCHLIST or [])]
    return {"symbol": symbol, "enabled": symbol in watchlist, "watchlist": watchlist}


@app.delete("/api/screener/profiles/{ticker}")
async def delete_screener_profile(ticker: str, state: ScreenerUserState = Depends(get_screener_state)):
    symbol = normalize_screener_symbol(ticker)
    if not symbol:
        raise HTTPException(status_code=400, detail="Invalid ticker format")
    state.profiles.pop(symbol, None)
    state.latest_scans.pop(symbol, None)
    state.news_watch.pop(symbol, None)
    state.earnings_watch.pop(symbol, None)
    state.fundamental_reports.pop(symbol, None)
    state.order = [item for item in state.order if item != symbol]
    await state.save()
    return {"status": "success", "symbol": symbol, "tradelist": state.order}


@app.post("/api/screener/profiles/{ticker}/reset")
async def reset_screener_profile(ticker: str, state: ScreenerUserState = Depends(get_screener_state)):
    symbol = normalize_screener_symbol(ticker)
    if not symbol or symbol not in state.profiles:
        raise HTTPException(status_code=404, detail="Screener profile not found")
    state.profiles[symbol] = {
        "source": "screener",
        "enabled": True,
        "paused": False,
        "timeframe": "30Min",
        "asset_class": "crypto" if _is_crypto_symbol(symbol) else "stock",
        "strategy_mode": "combined",
        "event_max_age_days": 10,
        "event_min_confidence": 0.55,
        # Keep the API reset path aligned with the UI's 30-minute Screener
        # defaults. These are starting settings, not an optimized performance
        # claim.
        "indicators": ["EMA Cross", "SMA", "RSI", "ADX Trend"],
        "indicator_overrides": {},
        "min_buy_signals": 3,
        "min_sell_signals": 3,
        "scan_interval_seconds": 1800,
        "scan_interval_auto": True,
        "market_session": "Extended" if not _is_crypto_symbol(symbol) else "24h",
        "price_watch_interval_seconds": 5,
        "news_watch_enabled": True,
        "news_refresh_interval_auto": True,
        "news_refresh_interval_seconds": 1800,
        "earnings_watch_enabled": True,
        "fundamental_watch_enabled": True,
        "started_at": datetime.now(pytz.UTC).isoformat(),
    }
    state.latest_scans.pop(symbol, None)
    state.news_watch.pop(symbol, None)
    state.earnings_watch.pop(symbol, None)
    state.fundamental_reports.pop(symbol, None)
    await state.save()
    # Resetting a profile changes its effective settings, so refresh it once
    # immediately regardless of the selected market session.
    state.force_scan.set()
    asyncio.create_task(_scan_screener_after_save(state, symbol))
    asyncio.create_task(_watch_screener_news_state(state, [symbol]))
    if not _is_crypto_symbol(symbol):
        asyncio.create_task(_watch_screener_earnings_state(state, [symbol]))
        asyncio.create_task(_watch_screener_fundamental_state(state, [symbol]))
    return {"status": "success", "symbol": symbol, "settings": state.profiles[symbol]}


@app.post("/api/screener/reorder")
async def reorder_screener_profiles(data: dict, state: ScreenerUserState = Depends(get_screener_state)):
    requested = data.get("tradelist", data.get("order", []))
    if not isinstance(requested, list):
        raise HTTPException(status_code=400, detail="Invalid profile order")
    ordered = []
    for item in requested:
        symbol = normalize_screener_symbol(item)
        if symbol in state.profiles and symbol not in ordered:
            ordered.append(symbol)
    ordered.extend(symbol for symbol in state.order if symbol in state.profiles and symbol not in ordered)
    state.order = ordered
    await state.save()
    return {"status": "success", "tradelist": state.order}


@app.get("/api/screener/activity")
async def get_screener_activity(kind: str = "scans", limit: int = 50, state: ScreenerUserState = Depends(get_screener_state)):
    activity = [] if kind == "orders" else state.activity[:max(1, min(int(limit or 50), 100))]
    return {"schema_version": "1.0", "kind": kind, "activity": activity, "count": len(activity), "generated_at": datetime.now(pytz.UTC).isoformat()}


@app.get("/api/screener/settings")
async def get_screener_settings(state: ScreenerUserState = Depends(get_screener_state)):
    return {
        "settings": dict(state.notification_settings),
        "page_settings": dict(state.page_settings),
        "health": _screener_summary(state, state.order),
    }


@app.put("/api/screener/settings")
async def update_screener_settings(data: dict, state: ScreenerUserState = Depends(get_screener_state)):
    updates = data.get("settings", data) if isinstance(data, dict) else {}
    requested_sms_phone = normalize_screener_sms_phone(
        updates.get("sms_phone_number", state.notification_settings.get("sms_phone_number"))
    )
    raw_requested_sms_phone = updates.get("sms_phone_number")
    if raw_requested_sms_phone is not None and str(raw_requested_sms_phone).strip() and not requested_sms_phone:
        raise HTTPException(status_code=400, detail="SMS phone number must use E.164 format, for example +12125551234")
    requested_sms_enabled = bool(updates.get("sms_enabled", state.notification_settings.get("sms_enabled")))
    if requested_sms_enabled and not requested_sms_phone:
        raise HTTPException(status_code=400, detail="Add a valid E.164 phone number before enabling SMS alerts")
    allowed = {
        "browser_enabled", "email_enabled", "sms_enabled", "sms_phone_number",
        "bullish_enabled", "bearish_enabled", "cooldown_minutes",
    }
    for key, value in updates.items():
        if key not in allowed:
            continue
        if key == "sms_phone_number":
            state.notification_settings[key] = requested_sms_phone
            continue
        if key.endswith("_enabled"):
            state.notification_settings[key] = bool(value)
        else:
            try:
                state.notification_settings[key] = max(0, min(1440, int(value)))
            except (TypeError, ValueError):
                raise HTTPException(status_code=400, detail="cooldown_minutes must be an integer from 0 to 1440")
    page_updates = data.get("page_settings") if isinstance(data, dict) else None
    if isinstance(page_updates, dict):
        for key in ("scan_interval_seconds", "chart_timeframe", "chart_session"):
            if key in page_updates:
                state.page_settings[key] = page_updates[key]
    await state.save()
    return {"status": "success", "settings": state.notification_settings, "page_settings": state.page_settings}


def _repair_historical_alert_snapshot(state: ScreenerUserState, item: dict) -> dict:
    """Repair pre-fix alerts whose saved explanation used post-transition state."""
    if not isinstance(item, dict):
        return item
    direction = str(item.get("event_direction") or item.get("direction") or "").upper()
    scan_log = item.get("scan_log") if isinstance(item.get("scan_log"), dict) else {}
    target_direction = str(scan_log.get("target_direction") or "").upper()
    if direction not in {"BULLISH", "BEARISH"} or target_direction in {"", direction}:
        return item
    symbol = normalize_screener_symbol(item.get("symbol"))
    saved_settings = item.get("settings_snapshot")
    if not isinstance(saved_settings, dict):
        # Do not rewrite a legacy alert using today's profile: that would make
        # a historical explanation appear precise while silently changing its
        # indicator/settings context. Preserve the stored explanation and make
        # the limitation explicit to the caller.
        legacy = copy.deepcopy(item)
        legacy["historical_reconstructed"] = False
        legacy["historical_snapshot_status"] = "legacy_snapshot_unavailable"
        return legacy
    settings = copy.deepcopy(saved_settings)
    if not settings:
        return item
    signals = item.get("signals") if isinstance(item.get("signals"), dict) else {}
    candle_meta = item.get("candle_meta") if isinstance(item.get("candle_meta"), dict) else {}
    formatted = {
        "ticker": symbol,
        "direction": direction,
        "evaluation_status": "valid",
        "reason": item.get("reason") or "Signal transition detected.",
        "price_raw": item.get("price"),
        "signals": signals,
        "atr_levels": item.get("atr_levels") if isinstance(item.get("atr_levels"), dict) else {},
        "screening_strategy_mode": item.get("screening_strategy_mode") or scan_log.get("screening_strategy_mode"),
        "strategy_qualification": item.get("strategy_qualification") or scan_log.get("strategy_qualification") or {},
        "event_qualification": item.get("event_qualification") or scan_log.get("event_qualification") or {},
        "candle_meta": {
            "candle_key": item.get("candle_key") or candle_meta.get("candle_key"),
            "candle_open_ts": item.get("candle_open_ts") or candle_meta.get("candle_open_ts"),
            "candle_close_ts": item.get("candle_close_ts") or candle_meta.get("candle_close_ts"),
        },
    }
    pre_transition_state = {
        "virtual_position": direction == "BEARISH",
        "last_actionable_direction": "BULLISH" if direction == "BEARISH" else "",
    }
    repaired = copy.deepcopy(item)
    repaired["historical_snapshot_status"] = "repaired_from_saved_snapshot"
    repaired["scan_log"] = _build_screener_scan_log(
        symbol,
        formatted,
        settings,
        pre_transition_state,
        alert_created=True,
    )
    return repaired


_SCREENER_NOTIFICATION_PERIOD_WINDOWS = {"1d": 86400, "1w": 7 * 86400, "1m": 30 * 86400, "1y": 365 * 86400}


def _screener_visible_notifications(state: ScreenerUserState) -> list:
    """Alerts the user can see: delivered in-app and not dismissed.

    Dismissed alerts stay in storage on purpose: the duplicate checks and the email/SMS senders look alerts up by id, so
    deleting one could let the same candle raise the same alert again or strand a queued message."""
    return [
        item for item in state.notifications
        if item.get("browser_status", "delivered") != "disabled" and not item.get("dismissed")
    ]


def _screener_notification_view(state: ScreenerUserState, period: str = "all", read_state: str = "all", unread_only: bool = False):
    """The filtered alert list used by both the list endpoint and "clear all", so they always agree on what is shown."""
    period_value = str(period or "all").lower()
    items = [_repair_historical_alert_snapshot(state, item) for item in _screener_visible_notifications(state)]
    if period_value == "today":
        tz = pytz.timezone(getattr(state.config, "TIMEZONE", "America/Chicago"))
        today = datetime.now(tz).date()

        def _is_today(item: dict) -> bool:
            try:
                created_at = item.get("created_at")
                if isinstance(created_at, str):
                    created_at = datetime.fromisoformat(created_at.replace("Z", "+00:00")).timestamp()
                return bool(created_at) and datetime.fromtimestamp(float(created_at), tz).date() == today
            except (TypeError, ValueError, OverflowError):
                return False

        items = [item for item in items if _is_today(item)]
    elif period_value in _SCREENER_NOTIFICATION_PERIOD_WINDOWS:
        cutoff = time.time() - _SCREENER_NOTIFICATION_PERIOD_WINDOWS[period_value]

        def _is_within_period(item: dict) -> bool:
            try:
                created_at = item.get("created_at")
                if isinstance(created_at, str):
                    created_at = datetime.fromisoformat(created_at.replace("Z", "+00:00")).timestamp()
                return float(created_at or 0) >= cutoff
            except (TypeError, ValueError, OverflowError):
                return False

        items = [item for item in items if _is_within_period(item)]
    elif period_value != "all":
        period_value = "all"
    unread_count = sum(not bool(item.get("read")) for item in items)
    read_count = len(items) - unread_count
    read_state_value = str(read_state or "all").lower()
    if unread_only and read_state_value == "all":
        read_state_value = "unread"
    if read_state_value == "unread":
        items = [item for item in items if not bool(item.get("read"))]
    elif read_state_value == "read":
        items = [item for item in items if bool(item.get("read"))]
    else:
        read_state_value = "all"
    return items, unread_count, read_count, period_value, read_state_value


@app.get("/api/screener/notifications")
async def list_screener_notifications(
    limit: int = 50,
    cursor: str = None,
    unread_only: bool = False,
    period: str = "all",
    read_state: str = "all",
    state: ScreenerUserState = Depends(get_screener_state),
):
    limit_value = max(1, min(int(limit or 50), 50))
    items, unread_count, read_count, period_value, read_state_value = _screener_notification_view(
        state, period, read_state, unread_only
    )
    start = 0
    if cursor:
        for index, item in enumerate(items):
            if item.get("id") == cursor:
                start = index + 1
                break
    page = items[start:start + limit_value]
    next_cursor = page[-1].get("id") if len(items) > start + len(page) else None
    return {
        "notifications": page,
        "next_cursor": next_cursor,
        "unread_count": unread_count,
        "read_count": read_count,
        "total_count": unread_count + read_count,
        "matching_count": len(items),
        "count": len(page),
        "period": period_value,
        "read_state": read_state_value,
        # "Clear all" sends this back so alerts that arrive after the user saw the list are not cleared unseen.
        "as_of": time.time(),
    }


def _screener_notification_created_epoch(item: dict) -> float:
    """created_at as epoch seconds; 0 when missing or unparseable (treated as an old alert)."""
    created_at = item.get("created_at")
    try:
        if isinstance(created_at, str):
            return datetime.fromisoformat(created_at.replace("Z", "+00:00")).timestamp()
        return float(created_at or 0)
    except (TypeError, ValueError, OverflowError):
        return 0.0


@app.post("/api/screener/notifications/dismiss")
async def dismiss_screener_notifications(data: dict = None, state: ScreenerUserState = Depends(get_screener_state)):
    """Hide alerts from the list. Either {"ids": [...]} or an explicit {"all": true, "period": ..., "read_state": ...}
    that clears exactly what the list shows under those filters. An empty or unknown body clears nothing.
    With "as_of" (the list response's value), alerts created after that moment are left alone."""
    payload = data if isinstance(data, dict) else {}
    ids = payload.get("ids")
    if payload.get("all") is True:
        items, *_ = _screener_notification_view(state, payload.get("period", "all"), payload.get("read_state", "all"))
        as_of = payload.get("as_of")
        if as_of is not None:
            if isinstance(as_of, bool) or not isinstance(as_of, (int, float)):
                raise HTTPException(status_code=400, detail="as_of must be a number")
            items = [item for item in items if _screener_notification_created_epoch(item) <= float(as_of)]
        target_ids = {item.get("id") for item in items if item.get("id")}
    elif isinstance(ids, list) and ids:
        if len(ids) > 1000:
            raise HTTPException(status_code=400, detail="Too many alert ids")
        target_ids = {str(value) for value in ids if value}
    else:
        raise HTTPException(status_code=400, detail="Provide alert ids or all=true")
    now = time.time()
    dismissed = 0
    for item in state.notifications:
        if item.get("id") in target_ids and not item.get("dismissed"):
            item["dismissed"] = True
            item["dismissed_at"] = now
            if not item.get("read"):
                item["read"] = True
                item["read_at"] = now
            dismissed += 1
    if dismissed:
        await state.save()
    unread = sum(not bool(item.get("read")) for item in _screener_visible_notifications(state))
    return {"status": "success", "dismissed": dismissed, "unread_count": unread}


@app.post("/api/screener/notifications/read")
async def mark_screener_notifications_read(data: dict = None, state: ScreenerUserState = Depends(get_screener_state)):
    payload = data or {}
    ids = {str(value) for value in payload.get("ids", []) if value}
    cutoff = payload.get("cutoff", time.time())
    try:
        cutoff_value = float(cutoff)
    except (TypeError, ValueError):
        cutoff_value = time.time()
    for item in state.notifications:
        created_at = item.get("created_at", 0)
        try:
            if isinstance(created_at, str):
                created_at = datetime.fromisoformat(created_at.replace("Z", "+00:00")).timestamp()
            created_at = float(created_at or 0)
        except (TypeError, ValueError, OverflowError):
            created_at = 0
        if (ids and item.get("id") in ids) or (not ids and created_at <= cutoff_value):
            item["read"] = True
            item["read_at"] = time.time()
    await state.save()
    return {"status": "success", "unread_count": sum(not bool(item.get("read")) for item in _screener_visible_notifications(state))}


@app.post("/api/screener/email/test")
async def queue_screener_test_email(state: ScreenerUserState = Depends(get_screener_state)):
    smtp = _smtp_settings()
    if not smtp["host"] or not smtp["from_email"]:
        raise HTTPException(status_code=503, detail="Screener SMTP is not configured")
    now = time.time()
    last_test = float(state.health.get("last_test_email_at", 0) or 0)
    if now - last_test < 300:
        raise HTTPException(status_code=429, detail="Test email is limited to once every five minutes")
    test_id = hashlib.sha256(f"test|{state.uid}|{int(now // 300)}".encode()).hexdigest()
    state.health["last_test_email_at"] = now
    state.email_outbox = [item for item in state.email_outbox if item.get("id") != test_id]
    state.email_outbox.append({
        "id": test_id,
        "status": "queued",
        "attempts": 0,
        "next_attempt_at": now,
        "created_at": now,
        "test": True,
    })
    await state.save()
    return {"status": "queued", "message": "Test email queued for the verified Firebase email address."}


@app.post("/api/screener/scan")
async def scan_screener_now(data: dict = None, state: ScreenerUserState = Depends(get_screener_state)):
    """Run an explicit Screener scan immediately, including outside session hours."""
    symbols = (data or {}).get("symbols")
    if isinstance(symbols, list):
        symbols = [symbol for symbol in (normalize_screener_symbol(item) for item in symbols) if symbol in state.profiles]
    else:
        symbols = None
    # Manual scans are explicit user requests. Use the same force path as a
    # settings refresh so a valid bullish/bearish result can create an alert
    # even when the configured stock session is closed. Normal scheduler
    # passes remain session-gated.
    state.force_scan.set()
    await _scan_screener_state(state, symbols, force_scan=True)
    # Explicit scans also evaluate fresh quote-based entries and exits. This
    # uses the same force semantics as the candle path, including outside the
    # configured session; normal scheduler passes remain session-gated.
    await _watch_screener_price_levels_state(state, symbols, force_scan=True)
    return {"status": "success", "scanned": symbols or list(state.order)}


def _screener_chart_response(ticker: str, timeframe: str, analysis: dict, settings: dict) -> dict:
    scan = _technical_scan_from_analysis(ticker, timeframe, analysis, use_bot_settings=True)
    formatted = _format_scan_for_ui(scan, use_global_settings=False)
    selected = settings.get("indicators")
    if isinstance(selected, list):
        formatted["signals"] = {name: formatted.get("signals", {}).get(name, {
            "signal": "NEUTRAL", "reason": "Awaiting sufficient market data", "weight": 1, "enabled": True,
        }) for name in selected}
    formatted.pop("price_history", None)
    return _sanitize_json_values({
        "ticker": ticker,
        "timeframe": timeframe,
        "primaryScan": formatted,
        "signals": formatted.get("signals", {}),
        "priceHistory": scan.get("price_history", []),
        "action": formatted.get("action", "HOLD"),
        "reason": formatted.get("reason", ""),
        "bullish_count": formatted.get("bullish_count", 0),
        "bearish_count": formatted.get("bearish_count", 0),
        "total_signals": formatted.get("total_signals", 0),
        "volumeProfile": analysis.get("volume_profile") or {},
        "candle_meta": analysis.get("candle_meta", {}),
        "preview_meta": {"settings_source": "screener", "candle_mode": "last_closed"},
    })


async def _load_screener_chart(state: ScreenerUserState, ticker: str, timeframe: str, settings: dict, force: bool = False) -> dict:
    set_user_config(state.config)
    overlay_token = set_draft_ticker_settings({ticker: settings})
    try:
        with _analysis_request_context("screener", ticker, True):
            async with _bounded_analysis_work(state.uid, "Screener chart analysis", _ANALYSIS_CHART_QUEUE_TIMEOUT_SECONDS, _ANALYSIS_CHART_LANE):
                analysis, _cached, _age = await asyncio.wait_for(
                    asyncio.to_thread(_get_cached_chart_analysis, ticker, timeframe, state, force, "screener", settings),
                    timeout=45.0,
                )
            if not analysis:
                raise HTTPException(status_code=404, detail=f"Could not load chart data for {ticker}")
            if not _has_usable_chart_history(analysis):
                raise HTTPException(status_code=404, detail=f"No usable chart candles available for {ticker}")
            return _screener_chart_response(ticker, timeframe, analysis, settings)
    finally:
        reset_draft_ticker_settings(overlay_token)


@app.post("/api/screener/chart-preview")
async def get_screener_chart_preview(data: dict, state: ScreenerUserState = Depends(get_screener_state)):
    symbol = normalize_screener_symbol(data.get("ticker"))
    if not symbol:
        raise HTTPException(status_code=400, detail="Invalid ticker format")
    settings = _screener_profile_from_request({"settings": data.get("settings", {})}, state, symbol)
    settings["_chart_preview"] = True
    timeframe = data.get("timeframe") or settings.get("timeframe") or state.config.DEFAULT_TIMEFRAME
    return await _load_screener_chart(state, symbol, timeframe, settings, bool(data.get("force", False)))


@app.get("/api/screener/chart/{ticker}")
async def get_screener_chart(ticker: str, timeframe: str = None, force: bool = False, state: ScreenerUserState = Depends(get_screener_state)):
    symbol = normalize_screener_symbol(ticker)
    if not symbol:
        raise HTTPException(status_code=400, detail="Invalid ticker format")
    settings = dict(state.profiles.get(symbol, {}))
    timeframe = timeframe or settings.get("timeframe") or state.page_settings.get("chart_timeframe") or state.config.DEFAULT_TIMEFRAME
    return await _load_screener_chart(state, symbol, timeframe, settings, force)


@app.get("/api/screener/settings/indicators")
def get_screener_indicator_settings(state: ScreenerUserState = Depends(get_screener_state)):
    return get_indicator_settings("screener", state)


@app.post("/api/screener/settings/indicators")
async def update_screener_indicator_settings(updates: dict, ticker: str = None, state: ScreenerUserState = Depends(get_screener_state)):
    return await update_indicators(dict(updates or {}), "screener", ticker, state)


@app.get("/api/screener/settings/ai")
def get_screener_ai_settings(state: ScreenerUserState = Depends(get_screener_state)):
    return get_ai_settings("screener", state)


@app.post("/api/screener/settings/ai")
async def update_screener_ai_settings(updates: dict, state: ScreenerUserState = Depends(get_screener_state)):
    return await update_ai_settings(dict(updates or {}), "screener", state)


@app.post("/api/screener/settings/risk")
async def update_screener_risk_settings(updates: dict, state: ScreenerUserState = Depends(get_screener_state)):
    raise HTTPException(status_code=405, detail="Screener profiles do not use trading risk or account settings")


@app.post("/api/screener/settings/risk/reactivate")
async def reactivate_screener_settings(state: ScreenerUserState = Depends(get_screener_state)):
    return {"status": "success", "message": "Screener monitoring is active; no broker risk lock applies."}


@app.get("/api/screener/search/{query}")
def search_screener_symbols(query: str, state: ScreenerUserState = Depends(get_screener_state)):
    _enforce_symbol_search_rate_limit(state.uid)
    if not str(query or "").strip():
        return []
    if len(query) > 64:
        raise HTTPException(status_code=400, detail="Search query is too long")
    provider = _get_webull_market_data_broker()
    if provider and hasattr(provider, "search_assets"):
        try:
            return provider.search_assets(query)
        except Exception as exc:
            print(f"[screener] Symbol search provider failed: {_safe_exception_type(exc)}")
    symbol = normalize_screener_symbol(query)
    return [{"symbol": symbol, "name": symbol}] if symbol else []


@app.get("/api/screener/assets/session_eligibility/{ticker}")
def get_screener_session_eligibility(ticker: str, market_session: str = "24h", state: ScreenerUserState = Depends(get_screener_state)):
    """Describe sessions supported by the Screener's configured data path.

    This is intentionally independent of broker trading eligibility.  A stock
    can be tradable overnight while the Screener still lacks timely completed
    overnight bars for indicator alerts on the free feed.
    """
    symbol = normalize_screener_symbol(ticker)
    if not symbol:
        raise HTTPException(status_code=400, detail="Invalid ticker format")
    if _is_crypto_symbol(symbol):
        return {"success": True, "allowed": True, "eligible": True, "symbol": symbol, "market_session": "24h", "message": "Crypto Screener alerts use completed candles 24/7."}
    requested = str(market_session or "Regular").strip().lower()
    if requested in {"24h", "crypto24", "24/5", "overnight"}:
        return {
            "success": True,
            "allowed": False,
            "eligible": False,
            "symbol": symbol,
            "market_session": "Extended",
            "message": "Live 24/5 stock alerts are unavailable: the configured free feed does not provide timely overnight candles for indicator analysis. Use Extended Hours (3:00 AM–7:00 PM CT).",
        }
    session = _normalize_screener_market_session(symbol, market_session)
    return {"success": True, "allowed": True, "eligible": True, "symbol": symbol, "market_session": session, "message": f"{session} stock Screener alerts use completed candles."}


@app.get("/api/screener/history")
def get_screener_history(period: str = "ALL", timeframe: str = "1D", include_dismissed: bool = True,
                         state: ScreenerUserState = Depends(get_screener_state)):
    """Return time-bucketed bullish/bearish alert events for the Screener chart.

    The chart is a history record, so alerts the user dismissed from the list are included by default; the page can
    ask for ``include_dismissed=false`` to make the chart match the visible list. ``cleared_in_range`` reports how many
    dismissed alerts fall in the window either way, so the page can say what the switch would add."""
    central_tz = pytz.timezone("America/Chicago")
    now_dt = datetime.now(central_tz)
    now = now_dt.timestamp()
    period_key = str(period or "ALL").upper()
    window_days = {"1D": 1, "1W": 7, "1M": 30, "1Y": 365, "ALL": 90}.get(period_key, 90)
    # Multi-day chart labels are calendar dates, so use one bucket per local
    # date. A 15-minute 1W bucket would render the same date repeatedly.
    bucket_seconds = {"1D": 60, "1W": 86400, "1M": 3600, "1Y": 86400, "ALL": 86400}.get(period_key, 3600)
    start_dt = (now_dt - timedelta(days=window_days - 1)).replace(hour=0, minute=0, second=0, microsecond=0)
    start = int(start_dt.timestamp())
    buckets = {}
    cleared_in_range = 0
    for item in state.notifications:
        if item.get("browser_status", "delivered") == "disabled":
            continue
        dismissed = bool(item.get("dismissed"))
        item = _repair_historical_alert_snapshot(state, item)
        try:
            created = item.get("created_at", 0)
            if isinstance(created, str):
                created = datetime.fromisoformat(created.replace("Z", "+00:00")).timestamp()
            created = float(created)
        except (TypeError, ValueError, OverflowError):
            continue
        if created < start:
            continue
        if dismissed:
            if str(item.get("direction", "")).lower() in {"bullish", "bearish"}:
                cleared_in_range += 1
            if not include_dismissed:
                continue
        # Signal Activity is an alert timeline: place the dot when the alert
        # was created, while retaining the candle close as audit metadata.
        event_time = created
        candle_close = item.get("candle_close_ts")
        try:
            candle_close = float(candle_close) if candle_close is not None else None
        except (TypeError, ValueError):
            candle_close = None
        key = start + int((event_time - start) // bucket_seconds) * bucket_seconds
        row = buckets.setdefault(key, {"bullish": [], "bearish": []})
        direction = str(item.get("direction", "NEUTRAL")).lower()
        if direction not in {"bullish", "bearish"}:
            continue
        row[direction].append({
            "ticker": normalize_screener_symbol(item.get("symbol")) or str(item.get("symbol") or "Unknown"),
            "timestamp": event_time,
            "candle_close_ts": candle_close,
            "candle_open_ts": item.get("candle_open_ts"),
            "candle_key": item.get("candle_key"),
            "event_direction": str(item.get("event_direction") or direction).upper(),
            "title": item.get("title") or "",
            "timeframe": item.get("timeframe") or "",
            "price": item.get("price"),
            "reason": item.get("reason") or item.get("scan_reason") or "",
            "signals": item.get("signals") or {},
            "scan_log": item.get("scan_log") or {},
            "atr": item.get("atr"),
            "atr_levels": item.get("atr_levels") or {},
        })
    current_bucket = start + int((now - start) // bucket_seconds) * bucket_seconds
    timestamps = list(range(start, current_bucket + bucket_seconds, bucket_seconds))
    # Keep the one-day activity timeline anchored at the actual current Central
    # time, rather than the end of the current one-minute bucket.
    if period_key == "1D" and int(now) > current_bucket:
        timestamps.append(int(now))
    bullish = [len(buckets.get(ts, {}).get("bullish", [])) for ts in timestamps]
    bearish = [len(buckets.get(ts, {}).get("bearish", [])) for ts in timestamps]
    alert_groups = [
        {
            "timestamp": ts,
            "bullish": buckets.get(ts, {}).get("bullish", []),
            "bearish": buckets.get(ts, {}).get("bearish", []),
        }
        for ts in timestamps
    ]
    return {
        "history": [], "data": [], "activity": state.activity[:100],
        "timestamp": timestamps, "bullish": bullish, "bearish": bearish,
        "alert_groups": alert_groups, "bucket_seconds": bucket_seconds, "timezone": "America/Chicago",
        "include_dismissed": bool(include_dismissed), "cleared_in_range": cleared_in_range,
    }


@app.get("/api/screener/provider-config")
def get_screener_provider_config(state: ScreenerUserState = Depends(get_screener_state)):
    return {"configured": True, "connected": True, "has_keys": True, "provider": "shared-market-data"}


@app.post("/api/screener/provider-config")
def save_screener_provider_config(data: dict, state: ScreenerUserState = Depends(get_screener_state)):
    return {"status": "success", "message": "Screener market data is managed independently by the backend."}


@app.post("/api/screener/no-trading")
def reject_screener_trading_action(state: ScreenerUserState = Depends(get_screener_state)):
    raise HTTPException(status_code=405, detail="Trading actions are disabled on the independent Screener page")


@app.get("/api/screener/ai-indicator")
async def get_screener_ai_indicator(
    ticker: str,
    timeframe: str = None,
    model: str = None,
    force: bool = False,
    context: str = "screener",
    state: ScreenerUserState = Depends(get_screener_state),
):
    symbol = normalize_screener_symbol(ticker)
    if not symbol:
        raise HTTPException(status_code=400, detail="Invalid ticker format")
    set_user_config(state.config)
    token = active_evaluation_context.set("screener")
    try:
        timeframe = normalize_ai_timeframe(
            timeframe or getattr(state.config, "AI_DECISION_TIMEFRAME", AI_CANDLE_TIMEFRAME)
        )
        if not getattr(state.config, "ENABLE_AI_INDICATOR", True):
            return {"error": "AI Indicator is disabled in Screener settings", "signal": "HOLD", "confidence": 0}
        if force:
            clear_ai_cache(symbol, timeframe, include_news=True)
        with _analysis_request_context("screener", symbol, True):
            async with _bounded_analysis_work(state.uid, "Screener AI indicator"):
                return await asyncio.wait_for(
                    asyncio.to_thread(get_ai_indicator, symbol, timeframe, 10000.0, 10000.0, model),
                    timeout=25.0,
                )
    except asyncio.TimeoutError:
        return {"error": "AI indicator timed out", "signal": "HOLD", "confidence": 0, "timeframe": timeframe, "model": model}
    except Exception as exc:
        print(f"[screener] AI indicator error for {symbol}: {_safe_exception_type(exc)}")
        return {"error": "AI indicator temporarily unavailable", "signal": "HOLD", "confidence": 0, "summary": "AI Indicator temporarily unavailable."}
    finally:
        active_evaluation_context.reset(token)

@app.get("/api/debug-doge")
async def get_debug_doge(eng: UserEngine = Depends(get_user_engine)):
    """Authenticated developer diagnostic scoped to the current user."""

    from main import _format_trade_for_ui
    ui_trade_log = [_format_trade_for_ui(trade) for trade in eng.trade_log]

    # Find DOGEUSD trade
    doge_trades = [t for t in ui_trade_log if t.get('ticker') == 'DOGEUSD' and t.get('action') == 'BUY']
    if not doge_trades:
        return {"error": "No open DOGEUSD trades found", "trades": ui_trade_log}

    p = doge_trades[0]

    from main import _lookup_quote_price, _format_quote_price, fmt_val
    scan = eng._pick_scan('DOGEUSD', '5Min', prefer_bot=True)
    return {"trade": p, "scan_targets": {"sl": scan.get("stop_loss"), "tp": scan.get("sell_target")}}
    ui_trade_log = [_format_trade_for_ui(trade) for trade in eng.trade_log]

    # Find DOGEUSD trade
    doge_trades = [t for t in ui_trade_log if t.get('ticker') == 'DOGEUSD' and t.get('action') == 'BUY']
    if not doge_trades:
        return {"error": "No open DOGEUSD trades found", "trades": ui_trade_log}

    p = doge_trades[0]

    from main import _lookup_quote_price, _format_quote_price, fmt_val
    scan = eng._pick_scan('DOGEUSD', '5Min', prefer_bot=True)
    return {"trade": p, "scan_targets": {"sl": scan.get("stop_loss"), "tp": scan.get("sell_target")}}
