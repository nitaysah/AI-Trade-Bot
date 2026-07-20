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

import warnings
warnings.filterwarnings("ignore", category=DeprecationWarning, message=".*utcnow.*")
warnings.filterwarnings("ignore", category=DeprecationWarning, module=".*webull.*")

from fastapi import FastAPI, Query, Header, HTTPException, Depends, Request
from fastapi.encoders import jsonable_encoder
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
from contextlib import asynccontextmanager, contextmanager, suppress
from pydantic import BaseModel
from typing import Optional, Any
import asyncio
import threading
from datetime import datetime, timedelta
import pytz
import firebase_admin
from firebase_admin import auth, credentials, firestore
import time
import os
import re
import json
import yfinance as yf
from cryptography.fernet import Fernet
import base64
import hashlib

LEGAL_TERMS_VERSION = "BB-2026.07"
TERMS_REQUIRED_STATUS = 428

from trader import evaluate_trade, get_risk_manager, clear_evaluation_cache, get_confluence_decision
from indicators import get_full_analysis
from data_manager import get_historical_data
from engine import FIXED_LEVEL_EXIT_MODES, UserManager, UserEngine
from swing_strategy import build_swing_exit, detect_swing_entry, format_swing_preview, build_entry_signal, format_entry_preview
from options_direction import validate_options_type, normalize_options_type, VALID_OPTION_DIRECTIONS, options_type_label
from prior_day_high_low import (
    buffered_prior_day_levels,
    dynamic_high_low_exit_levels as _buffered_dynamic_high_low_exit_levels,
    prior_day_ohlc_from_history as _shared_prior_day_ohlc_from_history,
    resolve_high_low_buffer_pct,
)
import config as global_config
from user_config import (
    set_user_config,
    get_user_config,
    set_draft_ticker_settings,
    reset_draft_ticker_settings,
)
from ai_indicator import get_ai_indicator, get_ai_sentiment, clear_ai_cache
from cache_manager import TTLCacheStore, stable_cache_key
from macro_actuals import enrich_macro_calendar_events

# Global threading event for cancelling backtests on page reload
global_backtest_cancel = threading.Event()

# ──────────────────────────────────────────────
# Secret Encryption Vault
# ──────────────────────────────────────────────
class Vault:
    """Handles bank-grade encryption for sensitive API keys."""
    def __init__(self):
        secret_key = os.getenv("VAULT_SECRET_KEY")
        if secret_key:
            try:
                self.fernet = Fernet(secret_key.encode())
                print("[vault] Running in HIGH-SECURITY mode (VAULT_SECRET_KEY loaded)")
            except Exception as e:
                print(f"[vault] ERROR: Invalid VAULT_SECRET_KEY format ({e}). Falling back to project ID.")
                self._load_fallback()
        else:
            print("[vault] WARNING: Running in fallback mode. Set VAULT_SECRET_KEY for production.")
            self._load_fallback()

    def _load_fallback(self):
        project_id = os.getenv("GOOGLE_CLOUD_PROJECT", "trading-bot-engine-df3de")
        seed = project_id.encode()
        key_hash = hashlib.sha256(seed).digest()
        self.fernet = Fernet(base64.urlsafe_b64encode(key_hash))

    def encrypt(self, plain_text: str) -> str:
        if not plain_text: return ""
        return self.fernet.encrypt(plain_text.encode()).decode()

    def decrypt(self, cipher_text: str) -> str:
        if not cipher_text: return ""
        try:
            decrypted = self.fernet.decrypt(cipher_text.encode()).decode()
            return decrypted.strip()
        except Exception as e:
            print(f"[vault] DECRYPTION FAILED: {e}")
            return ""

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
        cred_path = os.path.join(os.path.dirname(__file__), "serviceAccount.json")
        if os.path.exists(cred_path):
            cred = credentials.Certificate(cred_path)
            firebase_admin.initialize_app(cred)
        else:
            firebase_admin.initialize_app(options={'projectId': 'bot-bulls'})
        print("[main] Firebase Admin initialized.")
    except Exception as e:
        print(f"[main] Firebase Admin init error: {e}")

db = firestore.client()
user_manager = UserManager(db)
_webull_market_data_broker = None
_chart_analysis_cache = TTLCacheStore(maxsize=256)
_quote_cache = TTLCacheStore(maxsize=512)
_dashboard_broker_cache = TTLCacheStore(maxsize=64)

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
    "ENABLE_BOTBULLS1",
    "ENABLE_BOTBULLS2",
    "ENABLE_BOTBULLS3",
]

DASHBOARD_SIGNAL_TO_TOGGLE = {
    "RSI": "ENABLE_RSI",
    "MACD": "ENABLE_MACD",
    "EMA Cross": "ENABLE_EMA",
    "Supertrend": "ENABLE_SUPERTREND",
    "Bollinger": "ENABLE_BOLLINGER",
    "VWAP": "ENABLE_VWAP",
    "Mystic Pulse": "ENABLE_MYSTIC_PULSE",
    "Botbulls4 News AI": "ENABLE_AI_SENTIMENT",
    "Candle Patterns": "ENABLE_CANDLE_PATTERNS",
    "ADX Trend": "ENABLE_ADX_TREND",
    "SMA": "ENABLE_SMA",
    "Prior Day High/Low": "ENABLE_DAILY_HIGH_LOW",
    "Pivot Points (S/R)": "ENABLE_PIVOT_POINTS",
    "Volume Profile": "ENABLE_VOLUME_PROFILE",
    "Swing Points": "ENABLE_SWING_POINTS",
    "BotBulls1": "ENABLE_BOTBULLS1",
    "BotBulls2": "ENABLE_BOTBULLS2",
    "BotBulls3": "ENABLE_BOTBULLS3",
}

INDICATOR_PARAMETER_KEYS = [
    "BOTBULLS1_WT_CHANNEL",
    "BOTBULLS1_WT_AVERAGE",
    "BOTBULLS1_MFI_CONFIRM",
    "BOTBULLS1_WT_OVERSOLD",
    "BOTBULLS1_WT_OVERBOUGHT",
    "BOTBULLS1_MFI_BUY_RECLAIM",
    "BOTBULLS1_MFI_SELL_REJECT",
    "BOTBULLS1_CONFIRM_LOOKBACK",
    "BOTBULLS1_REQUIRE_MFI_CONFIRM",
    "BOTBULLS1_REQUIRE_CANDLE_CONFIRM",
    "BOTBULLS1_REQUIRE_PRICE_CONFIRM",
    "BOTBULLS1_PRICE_CONFIRM_EMA",
    "BOTBULLS1_PRICE_CONFIRM_LOOKBACK",
    "BOTBULLS2_ATR_MULT",
    "BOTBULLS2_TREND_TRACER_PERIOD",
    "BOTBULLS2_REVERSAL_ZONE_PERIOD",
    "BOTBULLS3_ATR_PERIOD",
    "BOTBULLS3_ATR_MULT",
    "RSI_PERIOD",
    "RSI_OVERBOUGHT",
    "RSI_OVERSOLD",
    "EMA_FAST",
    "EMA_SLOW",
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


def _cache_settings_hash(eng: UserEngine, ticker: Optional[str] = None, context: str = "dashboard") -> str:
    ticker_key = (ticker or "").upper()
    ctx_data = eng.config.contexts.get(context, {}) if getattr(eng.config, "contexts", None) else {}
    payload = {
        "ctx": context,
        "active_toggles": eng.config.active_toggles,
        "active_parameters": eng.config.active_parameters,
        "ticker_settings": eng.config.TICKER_SETTINGS.get(ticker_key, {}) if ticker_key else {},
        "ctx_toggles": ctx_data.get("toggles", {}),
        "ctx_params": ctx_data.get("parameters", {}),
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
    return source if source in {"dashboard", "bots", "backtest"} else "dashboard"


def _enabled_indicator_names_from_toggles(toggles: dict) -> list[str]:
    """Return confluence indicator names that should be computed for the active context."""
    return [
        name for name, toggle_key in DASHBOARD_SIGNAL_TO_TOGGLE.items()
        if name != "Botbulls4 News AI" and bool((toggles or {}).get(toggle_key, True))
    ]


def _chart_analysis_settings(eng: UserEngine, ticker: str, prefer_bot: bool = False) -> dict:
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
        return settings

    settings = dict(eng.config.active_parameters or {})
    settings["enabled_indicators"] = _enabled_indicator_names_from_toggles(eng.config.active_toggles)
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
        return "$0.00"
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


def _normalize_draft_bot_settings(ticker: str, draft_settings: dict, eng: UserEngine) -> dict:
    ticker_key = _normalize_quote_symbol(ticker)
    base_settings = eng.config.TICKER_SETTINGS.get(ticker_key, {})
    settings = dict(base_settings) if isinstance(base_settings, dict) else {}
    incoming = draft_settings if isinstance(draft_settings, dict) else {}

    for key, value in incoming.items():
        if value is None:
            settings.pop(key, None)
        else:
            settings[key] = value

    if "options_type" in settings:
        settings["options_type"] = normalize_options_type(settings.get("options_type", "calls_only"), default="calls_only")

    indicators = settings.get("indicators")
    if isinstance(indicators, list):
        deduped = []
        seen = set()
        for indicator in indicators:
            name = str(indicator or "").strip()
            if not name or name in seen:
                continue
            deduped.append(name)
            seen.add(name)
        settings["indicators"] = deduped

    overrides = settings.get("indicator_overrides")
    if isinstance(overrides, dict):
        settings["indicator_overrides"] = {
            str(key): value for key, value in overrides.items()
            if key is not None and value not in (None, "")
        }

    if _is_crypto_symbol(ticker_key) or "market_session" in settings:
        settings["market_session"] = _normalize_market_session_for_symbol(ticker_key, settings.get("market_session"))

    settings["source"] = "bots"
    _normalize_bot_signal_thresholds(settings, eng)
    return settings


def _log_background_task_result(task: asyncio.Task, reason: str):
    if task.cancelled():
        print(f"[settings] Background save cancelled ({reason})")
        return
    exc = task.exception()
    if exc:
        print(f"[settings] Background save failed ({reason}): {exc}")


def _queue_settings_save(eng: UserEngine, reason: str = "settings"):
    async def _save():
        await eng.save_settings()

    task = asyncio.create_task(_save())
    task.add_done_callback(lambda t: _log_background_task_result(t, reason))
    return task


def _get_cached_chart_analysis(ticker: str, timeframe: str, eng: UserEngine, force: bool = False, source: str = "dashboard", settings: dict = None):
    ticker_key = _normalize_quote_symbol(ticker)
    timeframe_key = timeframe or get_user_config().DEFAULT_TIMEFRAME
    settings_hash = _cache_settings_hash(eng, ticker_key, source)
    cache_key = stable_cache_key(eng.uid, source, ticker_key, timeframe_key, "webull", settings_hash, 1)
    cached, age = _chart_analysis_cache.get_with_age(cache_key, _chart_cache_ttl(timeframe_key))
    if cached and not force:
        return cached["analysis"], True, age

    # Coalesce duplicate chart requests without letting an old timed-out worker
    # block fresh UI requests. asyncio.wait_for cannot cancel the thread doing
    # indicator work, so a blocking lock here can create 30s head-of-line stalls.
    fetch_lock = _chart_analysis_cache.with_inflight_lock(stable_cache_key("chart_fetch", cache_key))
    lock_acquired = fetch_lock.acquire(blocking=False)
    if not lock_acquired:
        stale_cached, stale_age = _chart_analysis_cache.peek_with_age(cache_key)
        if stale_cached:
            return stale_cached["analysis"], True, stale_age
        # Another request is already doing the Webull + indicator work. Wait a
        # short window for it to populate cache instead of launching duplicate
        # calculations that can make both browser requests time out.
        wait_deadline = time.time() + 8.0
        while time.time() < wait_deadline:
            if fetch_lock.acquire(blocking=False):
                try:
                    cached, age = _chart_analysis_cache.peek_with_age(cache_key)
                    if cached:
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
        cached, age = _chart_analysis_cache.get_with_age(cache_key, _chart_cache_ttl(timeframe_key))
        # If another request just forced a refresh, reuse it instead of hitting
        # Webull again. This keeps candles fresh while preventing stampedes.
        if cached and (not force or (age is not None and age < 2.0)):
            return cached["analysis"], True, age

        analysis = get_full_analysis(ticker_key, timeframe=timeframe_key, data_source="webull", settings=settings)
        if analysis:
            _chart_analysis_cache.set(cache_key, {"analysis": analysis})
        return analysis, False, 0.0
    finally:
        fetch_lock.release()



def _get_prior_day_ohlc(price_history):
    return _shared_prior_day_ohlc_from_history(price_history)


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
        print(f"[dashboard] Exit protection target check failed for {symbol}: {exc}")
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
    ai_sentiment_key_factor: str = "Technical signals",
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
    prior_day = _get_prior_day_ohlc(analysis.get("price_history", []))
    return {
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
        "total_signals": len(analysis.get("signals", {})),
        "atr": analysis.get("atr", 0.0),
        "rsi": analysis.get("rsi", 50.0),
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
    }


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
        ai_sentiment_key_factor=ai_payload.get("key_factor", "Technical signals") or "Technical signals",
    )
    formatted_scan = _format_scan_for_ui(scan, use_global_settings=(not use_bot_settings))
    active_settings = settings if isinstance(settings, dict) else {}
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

    total_ms = ((time.perf_counter() - started) * 1000) if started is not None else 0.0
    return {
        "ticker": ticker,
        "timeframe": timeframe,
        "cached": cached,
        "cache_age_seconds": round(cache_age or 0.0, 1),
        "primaryScan": formatted_scan,
        "signals": formatted_scan.get("signals", {}),
        "priceHistory": scan.get("price_history", []),
        "price_history": scan.get("price_history", []),
        "action": formatted_scan.get("action", "HOLD"),
        "reason": formatted_scan.get("reason", ""),
        "bullish_count": formatted_scan.get("bullish_count", 0),
        "bearish_count": formatted_scan.get("bearish_count", 0),
        "total_signals": formatted_scan.get("total_signals", 0),
        "preview_meta": preview_meta or {},
        "performance": {
            "total_ms": total_ms,
            "cache_age_seconds": round(cache_age or 0.0, 1),
            "cached": cached,
        }
    }


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

            if provider is None:
                provider = _get_webull_market_data_broker()

            if provider is not None:
                try:
                    fresh = provider.get_latest_prices(misses) or {}
                    for ticker, price in fresh.items():
                        cache_key = stable_cache_key(eng.uid, _normalize_quote_symbol(ticker), "quote", 1)
                        _quote_cache.set(cache_key, price)
                        result[_normalize_quote_symbol(ticker)] = price
                except Exception as e:
                    print(f"[dashboard] Live quote fetch failed for {misses}: {type(e).__name__}: {e!r}")

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
        if webull_provider is not None:
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
            except Exception as e:
                print(f"[dashboard] Live quote fetch failed for {clean} via {source}: {type(e).__name__}: {e!r}")

    cached, age = _quote_cache.peek_with_age(cache_key)
    if cached is not None and age is not None and age < stale_max_age:
        return {"price": cached, "cache_age": age, "source": "stale_cache", "stale": True}

    return {"price": None, "cache_age": None, "source": "unavailable", "stale": True}


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
        print(f"[dashboard] Webull quote client unavailable: {e}")
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
        print(f"[dashboard] Live Webull price fetch failed: {e}")
        return {}

async def verify_token(authorization: str = Header(None)):
    if not authorization:
        raise HTTPException(status_code=401, detail="Missing Authorization header")
    if not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Invalid Authorization format")
    token = authorization.split("Bearer ")[1]
    if token == "dev-token":
        if os.getenv("ENV") == "development":
            return {"uid": "dev-user", "email": "dev@example.com"}
        else:
            raise HTTPException(status_code=401, detail="Development backdoor is disabled in production")
    try:
        return auth.verify_id_token(token)
    except Exception as e:
        raise HTTPException(status_code=401, detail="Invalid or expired authentication credentials")

async def get_user_engine(request: Request, user: dict = Depends(verify_token)) -> UserEngine:
    eng = user_manager.get_engine(user['uid'])
    if not getattr(eng, 'loaded_from_cloud', False):
        async with eng.load_lock:
            if not eng.loaded_from_cloud:
                await eng.load_from_cloud()
                eng.loaded_from_cloud = True

    path = request.url.path
    query = request.query_params
    legal_endpoint = path.startswith("/api/legal/")
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
    is_backtest_execution = path in {"/api/backtest", "/api/options/backtest", "/api/backtest/optimize"}
    is_chart_only_request = (
        path.startswith("/api/chart/")
        or path == "/api/signals/"
        or path.startswith("/api/signals/")
        or path == "/api/ai-indicator"
        or path == "/api/quote/"
        or path.startswith("/api/quote/")
        or path == "/api/market-data"
    )

    if not eng.bot_running and not is_backtest_status and not is_backtest_execution and not is_chart_only_request:
        async with eng.load_lock:
            if not eng.bot_running:
                eng.task = asyncio.create_task(eng.trading_loop())
    set_user_config(eng.config)
    return eng

def get_now():
    """Returns current time in the configured timezone."""
    tz = pytz.timezone(get_user_config().TIMEZONE)
    return datetime.now(tz)

class AlpacaConfig(BaseModel):
    api_key: str
    secret_key: str
    paper: bool = True


class LegalAcceptance(BaseModel):
    terms_version: str
    paper_trading_acknowledged: bool = True


@asynccontextmanager
async def lifespan(app: FastAPI):
    await user_manager.start_all()
    generic_cards_task = asyncio.create_task(_generic_dashboard_card_cache_loop())
    try:
        yield
    finally:
        generic_cards_task.cancel()
        with suppress(asyncio.CancelledError):
            await generic_cards_task
        await user_manager.stop_all()

app = FastAPI(
    title="Bot Bulls",
    description="Automated AI-driven quantitative trading platform",
    version="2.0.0",
    lifespan=lifespan
)


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
        "acceptance_source": "web_google_sign_in",
    }
    await eng.save_settings()
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
)


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

@app.get("/")
def root():
    return {"message": "Bot Bulls API is running!", "version": "2.0.0"}


@app.post("/api/alpaca_config")
async def update_alpaca_config(cfg: AlpacaConfig, eng: UserEngine = Depends(get_user_engine)):
    success = eng.broker.connect(cfg.api_key, cfg.secret_key, cfg.paper)
    if success:
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
        return {"status": "error", "message": "Failed to connect to Alpaca. Check your keys."}


@app.delete("/api/alpaca_config")
def unlink_alpaca(eng: UserEngine = Depends(get_user_engine)):
    # Delete from cloud
    if db:
        try:
            db.collection("users").document(eng.uid).collection("settings").document("alpaca").delete(timeout=2.0)
        except Exception as e:
            print(f"[api] Error deleting alpaca settings: {e}")
    
    # Reset broker to simulation
    eng.broker.simulation_mode = True
    eng.broker.client = None
    
    # Clear config module state
    eng.config.ALPACA_API_KEY = ""
    eng.config.ALPACA_SECRET_KEY = ""
    return {"status": "success", "message": "Alpaca account unlinked. Switched to simulation mode."}


@app.get("/api/alpaca_status")
def get_alpaca_status(eng: UserEngine = Depends(get_user_engine)):
    """Lightweight broker status for pages that do not need a full dashboard scan."""
    has_keys = bool(getattr(eng.config, "ALPACA_API_KEY", ""))
    broker_simulation = bool(getattr(eng.broker, "simulation_mode", True))
    return {
        "status": "success",
        "simulation": (not has_keys) or broker_simulation,
        "has_keys": has_keys,
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
        print(f"[api] Portfolio history error/timeout ({e}) for period={alpaca_period}, timeframe={timeframe}. Using fallback data.")
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
GENERIC_DASHBOARD_CARD_CACHE_SCHEMA_VERSION = 2
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
        print(f"[generic-cache] Could not read shared cache file: {e}")
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
    "AAPL MSFT GOOG NVDA META TSLA AVGO AMD QCOM NFLX AMZN ADBE CRM INTC CSCO ORCL "
    "JPM BAC WFC MS GS V MA AXP BLK C SCHW HDB "
    "LLY UNH JNJ MRK ABBV PFE WMT COST PG KO PEP NKE MCD EL "
    "XOM CVX COP OXY SLB CAT GE HON UNP LMT DE MMM "
    "BTC-USD ETH-USD SOL-USD ADA-USD XRP-USD DOGE-USD DOT-USD LINK-USD LTC-USD NEAR-USD BCH-USD AVAX-USD"
)
MARKET_SECTOR_SUMMARY_GROUPS = {
    "tech": {
        "label": "Technology",
        "benchmark": "XLK",
        "tickers": ["AAPL", "MSFT", "GOOG", "NVDA", "META", "TSLA", "AVGO", "AMD", "QCOM", "NFLX", "AMZN", "ADBE", "CRM", "INTC", "CSCO", "ORCL"],
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
    "AAPL MSFT GOOG GOOGL NVDA META TSLA AVGO AMD QCOM NFLX AMZN ADBE CRM INTC CSCO ORCL "
    "JPM BAC WFC MS GS V MA AXP BLK C SCHW "
    "LLY UNH JNJ MRK ABBV PFE ISRG TMO ABT DHR "
    "WMT COST PG KO PEP NKE MCD HD LOW TGT "
    "XOM CVX COP OXY SLB EOG CAT GE HON UNP LMT DE MMM "
    "NEE DUK SO PLD AMT LIN"
)

def _fetch_market_data_sync():
    # This card data is generic market context, not user/account data.
    import yfinance as yf

    # 1. Fetch Sector Data
    sector_tickers = MARKET_SECTOR_TICKERS
    df_sector = yf.download(sector_tickers, period="5d", progress=False)

    sector_data = {}
    for ticker in sector_tickers.split():
        try:
            valid_closes = df_sector['Close'][ticker].dropna()
            if len(valid_closes) >= 2:
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
        df_breadth = yf.download(" ".join(combined_list), period="1y", progress=False)
        for ticker in combined_list:
            try:
                if len(combined_list) == 1:
                    valid_closes = df_breadth['Close'].dropna()
                    valid_highs = df_breadth['High'].dropna()
                    valid_lows = df_breadth['Low'].dropna()
                else:
                    valid_closes = df_breadth['Close'][ticker].dropna()
                    valid_highs = df_breadth['High'][ticker].dropna()
                    valid_lows = df_breadth['Low'][ticker].dropna()

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
    if (
        not force
        and _MARKET_DATA_CACHE
        and now - _MARKET_DATA_LAST_FETCH < GENERIC_DASHBOARD_CARD_CACHE_TTL_SECONDS
    ):
        return _MARKET_DATA_CACHE
    if not force:
        persisted_data, persisted_at = _load_persisted_generic_card("market_data")
        if persisted_data:
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
            print(f"Error fetching market data: {e}")
            import traceback
            traceback.print_exc()
            if _MARKET_DATA_CACHE:
                stale_data = dict(_MARKET_DATA_CACHE)
                stale_data["cache"] = {
                    **stale_data.get("cache", {}),
                    "stale": True,
                    "last_error": str(e),
                }
                return stale_data
            raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/market-data")
async def get_market_data(user: dict = Depends(verify_token)):
    return await _get_cached_market_data(force=False)


@app.get("/api/quote/{ticker}")
async def get_live_quote(ticker: str, eng: UserEngine = Depends(get_user_engine)):
    """Lightweight quote-only endpoint for visual active-chart candle updates."""
    clean = _normalize_quote_symbol(ticker)
    if not re.fullmatch(r"[A-Z0-9.\-]{1,15}", clean):
        raise HTTPException(status_code=400, detail="Invalid ticker format")

    quote = await asyncio.to_thread(_get_cached_quote_detail, eng, clean, 10, 300)
    price = quote.get("price")
    if price is None:
        raise HTTPException(status_code=404, detail=f"Quote unavailable for {clean}")

    return {
        "ticker": clean,
        "price": float(price),
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
        # Cache the remote calendar separately because the provider changes slowly.
        if now - _macro_calendar_cache["timestamp"] < 3600 and _macro_calendar_cache["data"]:
            return _macro_calendar_cache["data"]

        import requests
        try:
            url = "https://nfs.faireconomy.media/ff_calendar_thisweek.json"
            headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'}
            r = requests.get(url, headers=headers, timeout=5)
            r.raise_for_status()
            data = r.json()

            # Expand to High/Medium USD and High major global economies
            def is_relevant(e):
                if e.get('country') == 'USD' and e.get('impact') in ['High', 'Medium']:
                    return True
                if e.get('country') in ['EUR', 'GBP', 'CNY', 'JPY'] and e.get('impact') == 'High':
                    return True
                return False

            result = [e for e in data if is_relevant(e)]

            # Sort events chronologically by date string
            def get_timestamp(ev):
                try:
                    return datetime.fromisoformat(ev.get('date', '')).timestamp()
                except Exception:
                    return 0
            result.sort(key=get_timestamp)
            result = enrich_macro_calendar_events(result)

            _macro_calendar_cache["data"] = result
            _macro_calendar_cache["timestamp"] = now
            return result
        except Exception as e:
            print(f"Error fetching forex calendar: {e}")
            return _macro_calendar_cache["data"]

    indicators_task = asyncio.to_thread(get_macro_data, False)
    calendar_task = asyncio.to_thread(fetch_calendar)
    indicators, calendar = await asyncio.gather(indicators_task, calendar_task)
    return {
        "status": "success",
        "indicators": indicators,
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
            print(f"Error fetching macro events: {e}")
            if _MACRO_EVENTS_CACHE:
                stale_data = dict(_MACRO_EVENTS_CACHE)
                stale_data["cache"] = {
                    **stale_data.get("cache", {}),
                    "stale": True,
                    "last_error": str(e),
                }
                return stale_data
            return {"status": "error", "message": str(e), "indicators": {}, "calendar": []}


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
            print(f"Error fetching Fear & Greed: {e}")
            if _FEAR_GREED_CACHE:
                stale_data = dict(_FEAR_GREED_CACHE)
                stale_data["cache"] = {
                    **stale_data.get("cache", {}),
                    "stale": True,
                    "last_error": str(e),
                }
                return stale_data
            return {
                "status": "error",
                "message": str(e),
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
        fear_greed, market_data, macro_events = await asyncio.gather(
            _get_cached_fear_greed(force=False),
            _get_cached_market_data(force=False),
            _get_cached_macro_events(force=False),
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
        print(f"Error fetching generic dashboard cards: {e}")
        return {"status": "error", "message": str(e)}


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
        "indicator": "Indicator",
        "custom_sltp": "Custom SL/TP",
        "custom_underlying_sltp": "Custom Underlying SL/TP",
        "sltp": "ATR Stop / Take Profit",
        "hybrid": "Hybrid (Indicator + ATR)",
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
    "hybrid_pct",
    "hybrid_pivot",
    "hybrid_high_low",
    "hybrid_swing",
}


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


def _scan_log_strategy_context(settings: dict, position: dict) -> dict:
    phase = "EXIT" if position else "ENTRY"
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


def _signal_weight_for_direction(signals: dict, selected_indicators: list[str], direction: str) -> int:
    selected = set(selected_indicators or [])
    total = 0
    for name, data in (signals or {}).items():
        if selected and name not in selected:
            continue
        if str((data or {}).get("signal") or "NEUTRAL").upper() == direction:
            total += int((data or {}).get("weight", 1) or 1)
    return total


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


def _smart_reentry_blocker_text(settings: dict, formatted_scan: dict, target_direction: str, eng: UserEngine) -> str:
    if not settings.get("enable_smart_reentry", getattr(eng.config, "ENABLE_SMART_REENTRY", False)):
        return ""

    reason_text = str(formatted_scan.get("reason") or "")
    if reason_text.startswith("Smart Re-entry"):
        return reason_text

    mode = str(settings.get("smart_reentry_type", getattr(eng.config, "SMART_REENTRY_TYPE", "RSI")) or "RSI").upper()
    target_direction = str(target_direction or "BULLISH").upper()

    if mode == "RSI":
        rsi = _entry_forecast_float(formatted_scan.get("rsi"), 0.0)
        if rsi <= 0:
            return "Smart re-entry RSI waiting: live RSI unavailable."
        if target_direction == "BEARISH":
            min_rsi = _entry_forecast_float(
                settings.get("smart_reentry_rsi_put_min", getattr(eng.config, "SMART_REENTRY_RSI_PUT_MIN", 30)),
                30.0,
            )
            if rsi < min_rsi:
                return f"Smart re-entry RSI waiting: PUT/SELL blocked while RSI {rsi:.1f} < {min_rsi:.1f}; needs +{(min_rsi - rsi):.1f} RSI."
        else:
            max_rsi = _entry_forecast_float(
                settings.get("smart_reentry_rsi_call_max", getattr(eng.config, "SMART_REENTRY_RSI_CALL_MAX", 70)),
                70.0,
            )
            if rsi > max_rsi:
                return f"Smart re-entry RSI waiting: BUY blocked while RSI {rsi:.1f} > {max_rsi:.1f}; needs -{(rsi - max_rsi):.1f} RSI."

    elif mode == "VWAP":
        current = _entry_forecast_float(formatted_scan.get("price_raw"), 0.0)
        vwap = _entry_forecast_float(formatted_scan.get("vwap"), 0.0)
        if current <= 0 or vwap <= 0:
            return "Smart re-entry VWAP waiting: live price/VWAP unavailable."
        dist_pct = abs(current - vwap) / vwap * 100.0
        max_dist = _entry_forecast_float(
            settings.get(
                "smart_reentry_vwap_put_max_dist_pct" if target_direction == "BEARISH" else "smart_reentry_vwap_call_max_dist_pct",
                getattr(eng.config, "SMART_REENTRY_VWAP_PUT_MAX_DIST_PCT", 0.5) if target_direction == "BEARISH" else getattr(eng.config, "SMART_REENTRY_VWAP_CALL_MAX_DIST_PCT", 0.5),
            ),
            0.5,
        )
        if dist_pct > max_dist:
            side_label = "PUT/SELL" if target_direction == "BEARISH" else "BUY"
            return f"Smart re-entry VWAP waiting: {side_label} blocked while price is {dist_pct:.2f}% from VWAP; needs <= {max_dist:.2f}%."

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
    mode = settings.get("buy_mode", "indicator")
    buy_threshold = _get_threshold_setting(settings, ("min_buy_signals", "threshold", "buy_threshold"), 1)
    pool_text = _indicator_pool_text(settings)
    parts = [_entry_strategy_label(mode)]

    is_option = bool(settings.get("options_trading"))
    if is_option:
        option_label = _option_direction_label(settings)
        parts.append(f"[Options] {option_label}")

    if mode in {"indicator", "hybrid", "hybrid_atr", "hybrid_pct", "hybrid_pivot", "hybrid_high_low", "hybrid_swing"}:
        if is_option:
            if option_label == "CALLS_ONLY":
                parts.append(f"Opens CALL when {buy_threshold} enabled bullish signals confirm")
            elif option_label == "PUTS_ONLY":
                parts.append(f"Opens PUT when {buy_threshold} enabled bearish signals confirm")
            else:
                parts.append(f"Opens CALL on {buy_threshold} bullish signals or PUT on {buy_threshold} bearish signals")
        else:
            parts.append(f"Opens long when {buy_threshold} enabled bullish signals confirm")

    if mode == "custom_price":
        parts.append(f"[Trigger] {_entry_forecast_money(settings.get('custom_buy_price'))}")
    elif mode in {"atr", "hybrid_atr"}:
        atr_val = _entry_forecast_float((scan or {}).get("atr"), 0.0)
        mult = _entry_forecast_float(settings.get("entry_atr_multiplier", settings.get("atr_stop_multiplier", 2.0)), 2.0)
        parts.append(f"[ATR Trigger] x{mult:.1f}")
        if atr_val > 0:
            parts.append(f"[ATR Snapshot] {_entry_forecast_money(atr_val)}")
    elif mode in {"percentage", "hybrid_pct"}:
        pct = _entry_forecast_float(settings.get("entry_pct_move", 1.0), 1.0)
        parts.append(f"[Move] {pct:.1f}%")
    elif mode in {"pivot_points", "hybrid_pivot"}:
        prior = (scan or {}).get("prior_day_ohlc") or {}
        if prior:
            parts.append(f"[Pivot Day High] {_entry_forecast_money(prior.get('high'))}")
            parts.append(f"[Pivot Day Low] {_entry_forecast_money(prior.get('low'))}")
    elif mode in {"high_low", "hybrid_high_low"}:
        prior = (scan or {}).get("prior_day_ohlc") or {}
        levels = buffered_prior_day_levels(prior, resolve_high_low_buffer_pct(settings)) if prior else None
        if levels:
            parts.append(f"[High Trigger] {_entry_forecast_money(levels.get('high_trigger'))}")
            parts.append(f"[Low Trigger] {_entry_forecast_money(levels.get('low_trigger'))}")
            parts.append(f"[PDH/PDL] {_entry_forecast_money(levels.get('high'))} / {_entry_forecast_money(levels.get('low'))}")
            if levels.get("buffer_pct"):
                parts.append(f"[Buffer] {levels.get('buffer_pct', 0.0):.2f}%")
    elif mode in {"swing_points", "hybrid_swing"}:
        lookback = int(settings.get("swing_entry_lookback_days", settings.get("swing_lookback_days", 20)) or 20)
        swing_mode = str(settings.get("swing_entry_mode", "bounce_retest")).replace("_", " ").title()
        parts.append(f"[Swing] {swing_mode} | {lookback}D")

    if mode in {"indicator", "hybrid", "hybrid_atr", "hybrid_pct", "hybrid_pivot", "hybrid_high_low", "hybrid_swing"}:
        parts.append(f"[Pool] {pool_text}")
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

    parts = [_exit_strategy_label(sell_mode)]
    parts.append(f"[Position] {direction_label}")
    if avg_price > 0:
        parts.append(f"[Entry] {_format_quote_price(ticker, avg_price)}")

    if sell_mode in {"indicator", "hybrid", "hybrid_pct", "hybrid_pivot", "hybrid_high_low", "hybrid_swing"}:
        if direction_label == "PUT":
            parts.append(f"Exits when {buy_threshold} enabled bullish signals confirm")
        else:
            parts.append(f"Exits when {sell_threshold} enabled bearish signals confirm")
        parts.append(f"[Pool] {_indicator_pool_text(settings)}")

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
            parts.append(f"[ATR SL] {_format_quote_price(ticker, sl_price)}")
            parts.append(f"[ATR TP] {_format_quote_price(ticker, tp_price)}")
            parts.append(f"[ATR Snapshot] {_entry_forecast_money(atr_val)}")

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
        parts.append(f"[Pct SL] {_format_quote_price(ticker, sl_price)}")
        parts.append(f"[Pct TP] {_format_quote_price(ticker, tp_price)}")

    if sell_mode == "custom_sltp":
        parts.append(f"[Custom SL] {_entry_forecast_money(settings.get('custom_stop_loss_price'))}")
        parts.append(f"[Custom TP] {_entry_forecast_money(settings.get('custom_take_profit_price'))}")

    if sell_mode == "custom_underlying_sltp":
        parts.append(f"[Underlying SL] {_entry_forecast_money(settings.get('custom_underlying_stop_loss_price'))}")
        parts.append(f"[Underlying TP] {_entry_forecast_money(settings.get('custom_underlying_take_profit_price'))}")

    if sell_mode in {"pivot_points", "hybrid_pivot", "high_low", "hybrid_high_low"}:
        prior = (scan or {}).get("prior_day_ohlc") or {}
        if prior:
            parts.append(f"[Prior High] {_entry_forecast_money(prior.get('high'))}")
            parts.append(f"[Prior Low] {_entry_forecast_money(prior.get('low'))}")

    if sell_mode in {"swing_points", "hybrid_swing"}:
        lookback = int(settings.get("swing_exit_lookback_days", settings.get("swing_lookback_days", 20)) or 20)
        parts.append(f"[Swing Exit] {lookback}D structural stop/target")

    return " | ".join(parts)


def _clear_forecast_exit_snapshots(settings: dict):
    settings.pop("forecast_exit_snapshots", None)
    settings.pop("forecast_entry_snapshot", None)


def _ensure_entry_forecast_snapshot(settings: dict, scan: dict, ticker: str) -> tuple[str, bool]:
    settings_hash = _forecast_settings_hash(settings)
    snapshot = settings.get("forecast_entry_snapshot") or {}
    if snapshot.get("text") and snapshot.get("settings_hash") == settings_hash and "[Entry Strategy]" not in str(snapshot.get("text")) and "[Exit Strategy]" not in str(snapshot.get("text")):
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
    settings_hash = _forecast_settings_hash(settings)
    snapshot_key = _position_snapshot_key(position, ticker)
    snapshots = settings.setdefault("forecast_exit_snapshots", {})
    snapshot = snapshots.get(snapshot_key) or {}
    entry_price = _entry_forecast_float(position.get("avg_price") or position.get("avg_entry_price"), 0.0)
    qty = _entry_forecast_float(position.get("qty"), 0.0)
    if (
        snapshot.get("text")
        and snapshot.get("settings_hash") == settings_hash
        and abs(_entry_forecast_float(snapshot.get("entry_price"), 0.0) - entry_price) < 1e-8
        and abs(_entry_forecast_float(snapshot.get("qty"), 0.0) - qty) < 1e-8
        and "[Entry Strategy]" not in str(snapshot.get("text"))
        and "[Exit Strategy]" not in str(snapshot.get("text"))
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
) -> dict:
    phase, target_direction, required_count, strategy_context = _resolve_scan_log_target(settings, formatted_scan, position)
    selected_indicators = _selected_indicator_names(settings)
    has_indicator_pool = bool(selected_indicators)
    strategy_uses_indicators = bool(strategy_context["uses_indicators"])
    show_indicator_leg = strategy_uses_indicators and has_indicator_pool
    uses_structural = bool(strategy_context["uses_structural"])
    strategy_label = strategy_context["label"]
    decision = str(formatted_scan.get("action") or "HOLD").upper()

    if settings.get("options_trading") or settings.get("is_option"):
        opt_status = eng.get_options_market_status()
        if not opt_status["is_active_window"]:
            return {
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
            }
    elif not _is_crypto_symbol(ticker):
        market_session = str(settings.get("market_session", "Regular")).strip().lower()
        stock_status = eng.get_stock_market_status(market_session)
        if not stock_status["is_active_window"]:
            session_label = stock_status["market_session_label"]
            return {
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

    if not is_crypto and eng.config.get("MARKET_HOURS_ONLY", True):
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

    if strategy_uses_indicators and not has_indicator_pool:
        blockers.append(f"No indicator pool selected for {strategy_label}.")

    live_levels = (
        _scan_log_exit_levels(ticker, formatted_scan, settings, position, exit_protection, eng=eng)
        if phase == "EXIT"
        else _scan_log_entry_levels(ticker, formatted_scan, settings, target_direction)
    )

    if phase == "ENTRY":
        if settings.get("enable_cooldown", True) and ticker in eng.last_trade_timestamps:
            tf_seconds = max(1, _timeframe_to_seconds(str(settings.get("timeframe") or "5Min")))
            cooldown_minutes = settings.get("cooldown_minutes")
            cooldown_secs = int(cooldown_minutes * 60) if cooldown_minutes is not None else int(tf_seconds * 4)
            elapsed = (eng.get_now() - eng.last_trade_timestamps[ticker]).total_seconds()
            if elapsed < cooldown_secs:
                blockers.append(f"Re-entry cooldown active ({_format_duration_compact(cooldown_secs - elapsed)} remaining).")

        if settings.get("enable_signal_reset", getattr(eng.config, "ENABLE_SIGNAL_RESET", False)):
            locked_dir = eng.signal_locked_direction.get(ticker)
            if locked_dir:
                blockers.append(f"Waiting for signal reset on stale {locked_dir} signal.")

        smart_reentry_text = _smart_reentry_blocker_text(settings, formatted_scan, target_direction, eng)
        if smart_reentry_text:
            blockers.append(smart_reentry_text)

        if settings.get("enable_risk_management", True):
            max_positions = int(getattr(eng.config, "MAX_OPEN_POSITIONS", 5) or 5)
            if len(positions or []) >= max_positions:
                blockers.append(f"Max positions reached ({len(positions or [])}/{max_positions}).")

    status = "WAITING_EXIT" if phase == "EXIT" else "WAITING_ENTRY"
    if blockers:
        status = "BLOCKED"
    elif decision in {"BUY", "SELL"} and (not show_indicator_leg or remaining_count == 0):
        status = "READY"
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

    confirmed_text = ", ".join(
        f"🟢 {item['name']} {item['signal']}" + (f" ({item['reason']})" if item["reason"] else "")
        for item in indicators if item["status"] == "CONFIRMED"
    )
    waiting_text = ", ".join(
        f"🟡 {item['name']} {item['signal']}" + (f" ({item['reason']})" if item["reason"] else "")
        for item in indicators if item["status"] == "WAITING"
    )
    opposing_text = ", ".join(
        f"🔴 {item['name']} {item['signal']}" + (f" ({item['reason']})" if item["reason"] else "")
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
    watch_parts = [f"{phase.title()} watch"]
    if current_for_label > 0:
        watch_parts.append(f"Current {_format_quote_price(current_symbol, current_for_label)}")
    if live_levels:
        watch_parts.extend(live_levels[:2])
    elif uses_structural:
        watch_parts.append(str(formatted_scan.get("reason") or "Waiting for next live level"))
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

    return {
        "summary": " | ".join(summary_parts),
        "detail": " | ".join(detail_parts),
        "status": status,
        "phase": phase,
        "decision": decision,
        "target_direction": target_direction,
        "strategy_mode": strategy_context["mode"],
        "strategy_label": strategy_label,
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
        "indicators": indicators,
        "updated_at": formatted_scan.get("time") or get_now().isoformat(),
        "is_option": bool(settings.get("options_trading", settings.get("is_option", False))),
        "refresh_rate_sec": getattr(eng.config, 'OPTIONS_SCAN_INTERVAL_SECONDS', 10) if settings.get("options_trading", settings.get("is_option", False)) else eng.config.SCAN_INTERVAL_SECONDS,
    }

@app.get("/api/dashboard")
async def get_dashboard(request: Request, mode: str = "fast", ticker: str = None, timeframe: str = None, source: str = "dashboard", eng: UserEngine = Depends(get_user_engine)):
    """
    Main dashboard endpoint — returns everything the UI needs in one call.
    """
    from user_config import active_evaluation_context
    ctx = source if source in ["dashboard", "bots", "backtest"] else "dashboard"
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
            print(f"[dashboard] Broker {label} unavailable: {type(e).__name__}: {e or repr(e)}")
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
        positions = cached_broker.get("positions") or []
        orders = cached_broker.get("orders") or []
        profit_data = cached_broker.get("profit_data") or (0.0, 0.0)
        orders_history = cached_broker.get("orders_history") or []
    else:
        cached_account = cached_broker.get("account") if cached_broker else None
        account_task = _broker_call("account", eng.broker.get_account_info, cached_account or account_fallback, timeout=3.0)
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
            _dashboard_broker_cache.set(broker_cache_key, {
                "account": account,
                "positions": positions,
                "orders": orders,
                "profit_data": profit_data,
                "orders_history": orders_history,
            })
    total_profit, total_profit_pct = profit_data
    
    account_ms = (time.perf_counter() - account_start) * 1000
    has_broker_keys = bool(eng.config.ALPACA_API_KEY)
    broker_connected = has_broker_keys and not account.get('simulation', True)
    broker_status = "connected" if broker_connected else ("retrying" if has_broker_keys else "not_configured")
    broker_status_reason = account.get("broker_status_reason") or account.get("error") or ""
    
    # Active bot status check
    is_active_bot = primary_ticker in eng.config.TRADELIST
    
    # Check cache first for primary ticker scan
    primary_scan = None
    prefer_bot_for_primary = is_active_bot and ctx == "bots"
    if prefer_bot_for_primary and ctx == "bots":
        bot_tf = eng.config.TICKER_SETTINGS.get(primary_ticker, {}).get('timeframe')
        if bot_tf:
            timeframe = bot_tf
    
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
            print(f"[dashboard] Scan error for primary ticker {primary_ticker}: {e}")
            primary_scan = None

    if not primary_scan:
        primary_scan = {
            "time": get_now().isoformat(),
            "ticker": primary_ticker,
            "action": "HOLD",
            "price": "$0.00",
            "price_raw": 0.0,
            "reason": "Scanning in background...",
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
            "rsi": 50.0,
            "position_sizing": {},
            "risk_status": {},
            "is_custom": False
        }

    await _repair_bot_signal_thresholds_if_needed(eng)

    # Format the primary scan cleanly
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

    if sentiment_score > 0.3:
        sentiment_label = "Bullish"
    elif sentiment_score < -0.3:
        sentiment_label = "Bearish"
    else:
        sentiment_label = "Neutral"

    # Fetch live Webull prices, caching inactive watchlist tickers for 60 seconds to save API calls
    fast_tickers = list(dict.fromkeys(eng.config.TRADELIST + [primary_ticker]))
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
        print(f"[dashboard] Quote API error: {type(e).__name__}: {e!r}")
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
            "action": "HOLD",
            "reason": "",
            "bullish_count": 0,
            "bearish_count": 0,
            "total_signals": 0,
            "signals": {}
        }, t)

    formatted_primary = _inject_price(formatted_primary, primary_ticker)

    # --- Inject exit_forecast into positions ---
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
            strategy_overrides = active_ctx.get("parameters", {})
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
                if s.endswith('.'): s += '00'
                elif len(s.split('.')[1]) == 1: s += '0'
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
            print(f"[dashboard] Error forecasting exit for {p.get('symbol')}: {e}")
            traceback.print_exc()
            p['exit_forecast'] = "Forecast Error"

    # Build the final payload
    bot_scans_dict = {}
    settings_dirty = False
    for t in eng.config.TRADELIST:
        bot_tf = eng.config.TICKER_SETTINGS.get(t, {}).get('timeframe', eng.config.DEFAULT_TIMEFRAME)
        scan = eng._pick_scan(t, bot_tf, prefer_bot=True) or eng._pick_scan(t, bot_tf, prefer_bot=True, ignore_freshness=True) or {}
        ui_scan = _inject_price(_format_scan_for_ui(scan, use_global_settings=False), t)
        t_settings = eng.config.TICKER_SETTINGS.get(t, {})
        is_opt = t_settings.get('options_trading', t_settings.get('is_option', False))
        active_position = _matching_position_for_ticker(positions, t, t_settings)
        if active_position is None and t_settings.get("forecast_exit_snapshots"):
            _clear_forecast_exit_snapshots(t_settings)
            settings_dirty = True
        entry_static, entry_dirty = _ensure_entry_forecast_snapshot(t_settings, scan or ui_scan, t)
        exit_static, exit_dirty = _ensure_exit_forecast_snapshot(t_settings, scan or ui_scan, active_position, t)
        settings_dirty = settings_dirty or entry_dirty or exit_dirty

        ui_scan["forecast_entry_static"] = str(entry_static or "").replace("[Entry Strategy] ", "").replace("[Exit Strategy] ", "")
        ui_scan["forecast_exit_static"] = str(exit_static or "").replace("[Entry Strategy] ", "").replace("[Exit Strategy] ", "")
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
        "has_keys": bool(eng.config.ALPACA_API_KEY),
        "capital": f"${account['equity']:.2f}",
        "cash": f"${account['cash']:.2f}",
        "openPositions": str(len(positions)),
        "positionsList": ", ".join(p['symbol'] for p in positions) if positions else "No positions",
        "dailyPL": f"{daily_pl_sign}${daily_pl:.2f} ({daily_pl_sign}{daily_pl_pct:.1f}%)",
        "totalProfit": f"{total_pl_sign}${total_profit:.2f} ({total_pl_sign}{total_profit_pct:.1f}%)",
        "aiSentiment": f"{sentiment_label} ({sentiment_score})",
        "sentiment_confidence": sentiment_confidence,
        "sentiment_summary": primary_scan.get("sentiment_summary", ""),
        "sentiment_key_factor": primary_scan.get("sentiment_key_factor", "N/A"),
        "account": account,
        "positions": positions,
        "open_orders": orders,
        "pendingOrders": orders,
        "orders_history": orders_history,
        "orderHistory": orders_history,
        "trade_log": ui_trade_log,
        "recentTrades": ui_trade_log,
        
        "watchlistScans": {
            t: _price_only_scan(t)
            for t in eng.config.WATCHLIST
        },
        "botScans": bot_scans_dict,

        # Strategy Signals (primary ticker)
        "primaryTicker": primary_ticker,
        "primaryScan": formatted_primary,
        "signals": formatted_primary.get('signals', {}),
        "priceHistory": [],

        # Risk Management
        "risk": risk_mgr.get_risk_status(account['equity']),
        "ticker_settings": eng.config.TICKER_SETTINGS,

        # Bot Meta
        "botRunning": eng.bot_running,
        "brokerConnected": broker_connected,
        "brokerStatus": broker_status,
        "brokerStatusReason": broker_status_reason,
        "has_keys": has_broker_keys,
        "lastScan": last_scan_time or "Starting...",
        "indicator_settings": eng.config.active_toggles,
        "indicator_parameters": eng.config.active_parameters,
        "indicator_overrides": eng.config.contexts.get(ctx, {}).get("parameters", {}),
        "indicator_toggle_overrides": eng.config.contexts.get(ctx, {}).get("toggles", {}),
        "strategyTimeframe": timeframe,
        "watchlist": eng.config.WATCHLIST,
        "tradelist": eng.config.TRADELIST,

        # Performance Timings
        "performance": {
            "total_ms": (time.perf_counter() - overall_start) * 1000,
            "account_ms": account_ms,
            "quote_ms": quote_ms,
            "order_history_ms": order_history_ms,
            "eval_ms": primary_scan.get('perf_ms', 0),
            "cached": primary_scan.get('cached', False)
        },
        "scanInterval": eng.config.SCAN_INTERVAL_SECONDS,
        "optionsScanInterval": getattr(eng.config, 'OPTIONS_SCAN_INTERVAL_SECONDS', 10),

        # Safety Controls
        "maxOpenPositions": eng.config.get('MAX_OPEN_POSITIONS', 5),
        "tradeCooldownSeconds": eng.config.get('TRADE_COOLDOWN_SECONDS', 300),
        "marketHoursOnly": eng.config.get('MARKET_HOURS_ONLY', True),

        "debug_logs": cloud_restore_log
    }

    # Dashboard heartbeats should not carry chart candles. The active chart owns
    # candles through /api/chart so heavy refreshes can update signals without
    # forcing a full chart redraw or shipping large payloads.
    if "price_history" in payload["primaryScan"]:
        del payload["primaryScan"]["price_history"]
    for scan_map in (payload["watchlistScans"], payload["botScans"]):
        for v in scan_map.values():
            if "price_history" in v:
                del v["price_history"]

    if mode == "fast":
        payload["signals"] = {}
        payload["primaryScan"]["signals"] = {}
        for k, v in payload["watchlistScans"].items():
            v["signals"] = {}
        for k, v in payload["botScans"].items():
            v["signals"] = {}

    return payload


@app.get("/api/chart/{ticker}")
async def get_chart_data(
    ticker: str,
    timeframe: str = None,
    force: bool = False,
    source: str = "dashboard",
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

    with _analysis_request_context(analysis_source, ticker, prefer_bot):
        chart_settings = _chart_analysis_settings(eng, ticker, prefer_bot)
        try:
            analysis, cached, cache_age = await asyncio.wait_for(
                asyncio.to_thread(_get_cached_chart_analysis, ticker, timeframe, eng, force, analysis_source, chart_settings),
                timeout=30.0,
            )
        except asyncio.TimeoutError:
            raise HTTPException(status_code=504, detail=f"Chart refresh timed out for {ticker}")
        if not analysis:
            raise HTTPException(status_code=404, detail=f"Could not load chart data for {ticker}")
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


@app.post("/api/bots/chart-preview")
async def get_bot_chart_preview(data: dict, eng: UserEngine = Depends(get_user_engine)):
    """Draft-safe chart preview for bot settings without persisting ticker settings."""
    ticker = _normalize_quote_symbol(data.get("ticker", ""))
    if not ticker or not re.fullmatch(r"[A-Z0-9.\-]{1,15}", ticker):
        return {"error": "Invalid ticker format"}

    draft_settings = _normalize_draft_bot_settings(ticker, data.get("settings", {}), eng)
    timeframe = data.get("timeframe") or draft_settings.get("timeframe") or eng.config.DEFAULT_TIMEFRAME
    force = bool(data.get("force", True))
    started = time.perf_counter()
    overlay_token = set_draft_ticker_settings({ticker: draft_settings})

    try:
        with _analysis_request_context("bots", ticker, True):
            try:
                analysis, cached, cache_age = await asyncio.wait_for(
                    asyncio.to_thread(_get_cached_chart_analysis, ticker, timeframe, eng, force, "bots", draft_settings),
                    timeout=30.0,
                )
            except asyncio.TimeoutError:
                raise HTTPException(status_code=504, detail=f"Chart preview timed out for {ticker}")

            if not analysis:
                raise HTTPException(status_code=404, detail=f"Could not load chart preview for {ticker}")

            ai_data = {}
            ai_status = "disabled"
            ai_enabled = (
                isinstance(draft_settings.get("indicators"), list)
                and "Botbulls4 News AI" in draft_settings.get("indicators", [])
                and getattr(eng.config, "ENABLE_AI_SENTIMENT", True)
            )
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
                    print(f"[preview] AI sentiment unavailable for {ticker}: {exc}")
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


@app.get("/api/ollama/models")
async def get_ollama_models(eng: UserEngine = Depends(get_user_engine)):
    """
    Queries the local Ollama instance for pulled models.
    Returns a list of model names or an empty list if offline.
    """
    import requests
    host = getattr(eng.config, "OLLAMA_HOST", "http://localhost:11434")
    try:
        response = await asyncio.to_thread(
            requests.get,
            f"{host.rstrip('/')}/api/tags",
            timeout=1.5
        )
        if response.status_code == 200:
            models_data = response.json().get("models", [])
            return {
                "status": "online",
                "models": [m["name"] for m in models_data]
            }
    except Exception as e:
        print(f"[api] Ollama not reachable at {host}: {e}")
    return {"status": "offline", "models": []}


@app.get("/api/ai-indicator")
async def get_ai_indicator_endpoint(
    ticker: str,
    timeframe: str = "1Hour",
    model: str = None,
    force: bool = False,
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

    if not getattr(eng.config, "ENABLE_AI_INDICATOR", True):
        return {"error": "AI Indicator is disabled in settings", "signal": "HOLD", "confidence": 0}

    started = time.perf_counter()

    if force:
        clear_ai_cache(ticker, timeframe)

    try:
        account = await asyncio.wait_for(
            asyncio.to_thread(eng.broker.get_account_info),
            timeout=3.0,
        )
    except asyncio.TimeoutError:
        account = {"cash": 10000.0, "equity": 10000.0}
    available_cash = float(account.get("cash", 10000.0))
    account_equity = float(account.get("equity", 10000.0))

    try:
        result = await asyncio.wait_for(
            asyncio.to_thread(
                get_ai_indicator,
                ticker,
                timeframe,
                available_cash,
                account_equity,
                model,
            ),
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
        print(f"[api] AI indicator error for {ticker}: {e}")
        return {
            "error": str(e),
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
        chart_settings = _chart_analysis_settings(eng, ticker, prefer_bot)
        try:
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
    return {
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
    }


@app.get("/api/scan/{ticker}")
def scan_ticker(ticker: str, timeframe: str = "1Hour", eng: UserEngine = Depends(get_user_engine)):
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

    result = evaluate_trade(
        ticker.upper(), 
        account_equity=account['equity'], 
        available_cash=avail_cash,
        timeframe=timeframe
    )
    if result:
        eng._record_scan(result)
        return _format_scan_for_ui(result)
    return {"error": f"Could not analyze {ticker}"}


@app.post("/api/backtest/cancel")
async def cancel_backtest():
    """Signals the global cancellation event to abort running backtests."""
    global_backtest_cancel.set()
    return {"status": "cancelled", "message": "Backtest cancelled successfully"}

@app.post("/api/backtest")
async def run_backtest(data: dict, eng: UserEngine = Depends(get_user_engine)):
    """Runs a historical backtest for a ticker."""
    global_backtest_cancel.clear()
    from user_config import active_evaluation_context, active_ticker_context
    active_evaluation_context.set("backtest")
    # Apply indicator overrides for this backtest run
    eng.config.contexts.get("backtest", {}).get("parameters", {}).clear()
    eng.config.contexts.get("backtest", {}).get("toggles", {}).clear()
    for k, v in data.get("indicator_overrides", {}).items():
        eng.config.set(k, v)
    daily_high_low_buffer_pct = resolve_high_low_buffer_pct(data.get("indicator_overrides", {}))

    ticker = data.get("ticker", "AAPL").upper()
    active_ticker_context.set(ticker)
    
    if not re.fullmatch(r"[A-Z0-9.\-]{1,15}", ticker):
        return {"status": "error", "message": "Invalid ticker format"}
    timeframe = data.get("timeframe", "1Day")
    days = int(data.get("days", 30))
    capital = float(data.get("capital", 1000.0))
    allocation_mode = data.get("allocation_mode", "fixed")
    trade_amount = float(data.get("trade_amount", capital))
    threshold = int(data.get("threshold", 5))
    sell_threshold = int(data.get("sell_threshold", 3))
    indicators = data.get("indicators", []) 
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
        enable_signal_reset=bool(data.get("enable_signal_reset", False)),
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
        cooldown_minutes=int(data["cooldown_minutes"]) if data.get("cooldown_minutes") is not None else None,
        enable_risk=enable_risk,
        daily_high_low_buffer_pct=daily_high_low_buffer_pct,
        cancel_event=global_backtest_cancel
    )
    
    try:
        results = await asyncio.to_thread(bt.run)
        if "error" in results:
            return {"status": "error", "message": results["error"]}
        return {"status": "success", "results": results}
    except Exception as e:
        print(f"[api] Backtest crash: {e}")
        return {"status": "error", "message": "An internal error occurred during backtest simulation."}



@app.post("/api/backtest/optimize")
async def run_optimization_endpoint(data: dict, eng: UserEngine = Depends(get_user_engine)):
    """Runs an Auto-Optimization grid search with In-Sample/Out-of-Sample splitting."""
    global_backtest_cancel.clear()
    from user_config import active_evaluation_context, active_ticker_context
    active_evaluation_context.set("backtest")
    # Apply indicator overrides for this backtest run
    eng.config.contexts.get("backtest", {}).get("parameters", {}).clear()
    eng.config.contexts.get("backtest", {}).get("toggles", {}).clear()
    for k, v in data.get("indicator_overrides", {}).items():
        eng.config.set(k, v)
    daily_high_low_buffer_pct = resolve_high_low_buffer_pct(data.get("indicator_overrides", {}))

    ticker = data.get("ticker", "AAPL").upper()
    active_ticker_context.set(ticker)
    
    if not re.fullmatch(r"[A-Z0-9.\-]{1,15}", ticker):
        return {"status": "error", "message": "Invalid ticker format"}
        
    timeframe = data.get("timeframe", "1Day")
    days = int(data.get("days", 90))
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
        "enabled_indicators": data.get("indicators", []),
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
        "enable_signal_reset": bool(data.get("enable_signal_reset", False)),
        "enable_smart_reentry": bool(data.get("enable_smart_reentry", False)),
        "smart_reentry_type": data.get("smart_reentry_type", "RSI"),
        "smart_reentry_rsi_call_max": float(data.get("smart_reentry_rsi_call_max", 70.0)),
        "smart_reentry_rsi_put_min": float(data.get("smart_reentry_rsi_put_min", 30.0)),
        "smart_reentry_vwap_call_max_dist_pct": float(data.get("smart_reentry_vwap_call_max_dist_pct", 0.5)),
        "smart_reentry_vwap_put_max_dist_pct": float(data.get("smart_reentry_vwap_put_max_dist_pct", 0.5)),
        "daily_high_low_buffer_pct": daily_high_low_buffer_pct,
    }
    
    from optimizer import run_optimization
    try:
        results = await run_optimization(base_kwargs, split_pct)
        if "error" in results:
            return {"status": "error", "message": results["error"]}
        return {"status": "success", "results": results}
    except Exception as e:
        import traceback
        print(f"[api] Auto-Optimize crash: {e}\n{traceback.format_exc()}")
        return {"status": "error", "message": "Optimization engine crashed."}

@app.post("/api/options/backtest")
async def run_options_backtest(data: dict, eng: UserEngine = Depends(get_user_engine)):
    """Runs a historical options backtest."""
    global_backtest_cancel.clear()
    from user_config import active_evaluation_context, active_ticker_context
    active_evaluation_context.set("backtest")
    # Apply indicator overrides for this backtest run
    eng.config.contexts.get("backtest", {}).get("parameters", {}).clear()
    eng.config.contexts.get("backtest", {}).get("toggles", {}).clear()
    for k, v in data.get("indicator_overrides", {}).items():
        eng.config.set(k, v)
    daily_high_low_buffer_pct = resolve_high_low_buffer_pct(data.get("indicator_overrides", {}))

    ticker = data.get("ticker", "AAPL").upper()
    active_ticker_context.set(ticker)
    
    if not re.fullmatch(r"[A-Z0-9.\-]{1,15}", ticker):
        return {"status": "error", "message": "Invalid ticker format"}
        
    timeframe = data.get("timeframe", "1Day")
    days = int(data.get("days", 30))
    capital = float(data.get("capital", 1000.0))
    trade_amount = float(data.get("trade_amount", 100.0))
    expiry_days = int(data.get("expiry_days", 7))
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
    indicators = data.get("indicators", [])
    if threshold <= 0 and indicators:
        threshold = 1
    if sell_threshold <= 0 and indicators:
        sell_threshold = 1
    ext_hours = data.get("ext_hours", True)
    r = float(data.get("r", 0.05))
    enable_risk = bool(data.get("enable_risk", True))
    print(f"[options-backtest] Running {ticker} | {timeframe} | {days}d | BuyMode: {data.get('buy_mode','indicator')} (Thresh: {threshold}) | SellMode: {data.get('sell_mode','atr')} | Risk: {enable_risk}")

    enable_signal_reset = bool(data.get("enable_signal_reset", False))
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
        cancel_event=global_backtest_cancel,
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
        daily_high_low_buffer_pct=daily_high_low_buffer_pct
    )

    
    try:
        results = await asyncio.to_thread(opt_bt.run)
        if "error" in results:
            return {"status": "error", "message": results["error"]}
        return {"status": "success", "results": results}
    except Exception as e:
        import traceback
        traceback.print_exc()
        print(f"[api] Options Backtest crash: {e}")
        return {"status": "error", "message": "An internal error occurred during options backtest simulation."}


@app.post("/api/backtest/monte_carlo")
async def run_monte_carlo_endpoint(data: dict, eng: UserEngine = Depends(get_user_engine)):
    """
    Monte Carlo simulation on a completed trade list.
    Accepts the raw trades array from a previous backtest result — no re-running needed.
    Returns overfitting risk classification, Sharpe distribution, and fan chart data.
    """
    trades = data.get("trades", [])
    capital = float(data.get("capital", 1000.0))
    n_simulations = int(data.get("n_simulations", 1000))

    if not trades:
        return {"status": "error", "message": "No trades provided for Monte Carlo simulation."}
    if len(trades) < 3:
        return {"status": "error", "message": "Need at least 3 trades to run Monte Carlo simulation."}

    # Cap simulations for safety
    n_simulations = min(n_simulations, 5000)

    from monte_carlo import run_monte_carlo
    try:
        result = await asyncio.to_thread(run_monte_carlo, trades, capital, n_simulations)
        if "error" in result:
            return {"status": "error", "message": result["error"]}
        return {"status": "success", "result": result}
    except Exception as e:
        print(f"[api] Monte Carlo error: {e}")
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

    # Handle liquidation if requested and position exists
    if deploy_mode == "liquidate" and symbol in eng.config.TRADELIST:
        try:
            eng.broker.cancel_orders_for_ticker(symbol)
            eng.broker.close_position(symbol)
        except Exception as e:
            print(f"[api] Error during liquidation for {symbol}: {e}")
        if symbol in eng.config.TRADELIST:
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
        "indicators": data.get("indicators", []),
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
            "sell_mode": data.get("sell_mode"),
            "risk_per_trade": min(max(float(data.get("risk_per_trade", 0.5)), 0.0), 0.5),
            "max_position_pct": data.get("max_position_pct"),
            "atr_stop_multiplier": data.get("atr_stop_multiplier"),
            "atr_trail_multiplier": data.get("atr_trail_multiplier"),
            "take_profit_multiplier": data.get("atr_take_profit_multiplier"),
            "regular_stop_loss_pct": data.get("regular_stop_loss_pct"),
            "regular_take_profit_pct": data.get("regular_take_profit_pct"),
            "regular_trailing_pct": data.get("regular_trailing_pct"),
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
        
    from option_selector import get_option_chain
    chain = get_option_chain(
        underlying=ticker,
        expiration_limit_days=expiry_days,
        current_price=current_price,
        client=eng.broker.client
    )
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
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Could not load option trade history: {e}")

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
    
    # 1. Download Price History
    for tf, days in timeframes:
        start_date = end_date - timedelta(days=days)
        df = get_historical_data(ticker, tf, start_date, end_date)
        status = "Success" if df is not None and not df.empty else "No Data"
        log.append(f"{tf}: {status}")
        
    # 2. Download Metadata
    try:
        t = yf.Ticker(ticker)
        info = t.info
        
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
        print(f"[api] Metadata extraction error: {e}")
        log.append("Metadata Error: Could not save metadata securely")
        
    return {"ticker": ticker, "status": log}


@app.get("/api/settings/indicators")
def get_indicator_settings(context: str = "dashboard", eng: UserEngine = Depends(get_user_engine)):
    """Returns all indicator toggles grouped by category."""
    from user_config import active_evaluation_context
    token = active_evaluation_context.set(context)
    try:
        return {
        "Momentum": {
            "ENABLE_RSI": {"label": "RSI", "description": "Relative Strength Index — Overbought / Oversold", "enabled": getattr(eng.config, "ENABLE_RSI", True)},
            "ENABLE_MACD": {"label": "MACD", "description": "Moving Average Convergence Divergence", "enabled": getattr(eng.config, "ENABLE_MACD", True)},
        },
        "Trend": {
            "ENABLE_EMA": {"label": "EMA Cross", "description": "Exponential Moving Average Crossover (9/21)", "enabled": getattr(eng.config, "ENABLE_EMA", True)},
            "ENABLE_SUPERTREND": {"label": "Supertrend", "description": "Supertrend Indicator (10, 3)", "enabled": getattr(eng.config, "ENABLE_SUPERTREND", True)},
            "ENABLE_BOLLINGER": {"label": "Bollinger", "description": "Bollinger Bands (20, 2σ)", "enabled": getattr(eng.config, "ENABLE_BOLLINGER", True)},
            "ENABLE_ADX_TREND": {"label": "ADX Trend", "description": "Wilder's ADX (14-period) Trend Strength Filter", "enabled": getattr(eng.config, "ENABLE_ADX_TREND", True)},
            "ENABLE_SMA": {"label": "SMA 200", "description": "Simple Moving Average (200-period) institutional filter", "enabled": getattr(eng.config, "ENABLE_SMA", True)},
            "ENABLE_DAILY_HIGH_LOW": {"label": "Prior Day High/Low", "description": "Uses the previous completed trading session's high and low as breakout/breakdown levels. Buffer is entered as percent points.", "enabled": getattr(eng.config, "ENABLE_DAILY_HIGH_LOW", True)},
            "ENABLE_PIVOT_POINTS": {"label": "Pivot Points (S/R)", "description": "Classic prior-session pivot, support, and resistance levels. Bullish on support/pivot reclaim; bearish on resistance rejection or pivot breakdown.", "enabled": getattr(eng.config, "ENABLE_PIVOT_POINTS", True)},
            "ENABLE_SWING_POINTS": {"label": "Swing Points", "description": "Rolling swing support/resistance clusters. Bullish on support bounce or resistance reclaim; bearish on resistance rejection or support loss.", "enabled": getattr(eng.config, "ENABLE_SWING_POINTS", True)},
        },
        "Volume": {
            "ENABLE_VWAP": {"label": "VWAP", "description": "Volume Weighted Average Price", "enabled": getattr(eng.config, "ENABLE_VWAP", True)},
            "ENABLE_VOLUME_PROFILE": {"label": "Volume Profile", "description": "Prior-session POC/VAH/VAL volume-at-price levels. Bullish on POC reclaim/value acceptance; bearish on POC loss/value rejection.", "enabled": getattr(eng.config, "ENABLE_VOLUME_PROFILE", True)},
        },
        "Custom": {
            "ENABLE_MYSTIC_PULSE": {"label": "Mystic Pulse", "description": "DMI-based Consecutive Trend Strength", "enabled": getattr(eng.config, "ENABLE_MYSTIC_PULSE", True)},
            "ENABLE_AI_SENTIMENT": {"label": "Botbulls4 News AI", "description": "Groq-powered News Sentiment Analysis", "enabled": getattr(eng.config, "ENABLE_AI_SENTIMENT", True)},
            "ENABLE_CANDLE_PATTERNS": {"label": "Candle Patterns", "description": "Engulfing, Hammer, Shooting Star patterns", "enabled": getattr(eng.config, "ENABLE_CANDLE_PATTERNS", True)},
        },
    }
    finally:
        active_evaluation_context.reset(token)


@app.post("/api/settings/risk")
async def update_risk_settings(settings: dict, context: str = "dashboard", eng: UserEngine = Depends(get_user_engine)):
    """Updates risk management parameters."""
    from user_config import active_evaluation_context
    token = active_evaluation_context.set(context)
    try:
        for key, value in settings.items():
            if key in ['MAX_DAILY_DRAWDOWN', 'RISK_PER_TRADE', 'MAX_POSITION_PCT', 'GLOBAL_HARD_STOP_PCT']:
                if isinstance(value, (int, float)) and value > 1:
                    value = value / 100.0
            elif value is None:
                value = None # Explicit pass for null values
            eng.config.set(key, value)
    finally:
        active_evaluation_context.reset(token)
            
    await eng.save_settings()
    print(f"[settings] Updated risk parameters ({context}) for {eng.uid}: {', '.join(settings.keys())}")
    return {"status": "success", "settings": settings}


@app.post("/api/settings/ticker_amount")
async def update_ticker_amount(data: dict, eng: UserEngine = Depends(get_user_engine)):
    """Updates the allocated trade amount for a specific ticker."""
    ticker = data.get("ticker", "").upper()
    amount = data.get("amount")
    
    if ticker:
        if amount is None or amount == "":
            if ticker in eng.config.TICKER_AMOUNTS:
                del eng.config.TICKER_AMOUNTS[ticker]
        else:
            try:
                eng.config.TICKER_AMOUNTS[ticker] = float(amount)
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
    ticker = data.get("ticker", "").upper()
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
    ticker = data.get("ticker", "").upper()
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
    if ticker in eng.config.TRADELIST:
        eng.config.TRADELIST.remove(ticker)
        await eng.save_settings()
        print(f"[settings] Removed {ticker} from active tradelist (Bot Deactivated) for {eng.uid}")
        clear_evaluation_cache(ticker, None)
        _clear_chart_analysis_cache(ticker, None)
        
    res_cancel = eng.broker.cancel_orders_for_ticker(ticker)
    print(f"[tradelist] Cancelled pending orders for {ticker}: {res_cancel}")

    if liquidate:
        res = eng.broker.close_position(ticker)
        print(f"[tradelist] Liquidating {ticker}: {res}")

    return {"status": "success", "tradelist": eng.config.TRADELIST}


@app.get("/api/search/{query}")
def search_symbols(query: str, eng: UserEngine = Depends(get_user_engine)):
    """Searches for tradeable assets using the modular eng.broker."""
    if len(query) < 1:
        return []
    return eng.broker.search_assets(query)


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


@app.post("/api/bots/create")
async def create_bot(data: dict, eng: UserEngine = Depends(get_user_engine)):
    """Creates a new active bot with custom settings (adds to watchlist, tradelist, and TICKER_SETTINGS)."""
    symbol = data.get("symbol", "").upper().strip()
    if not symbol:
        return {"status": "error", "message": "Symbol is required"}
        
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
        
    if "capital" in data and data["capital"] is not None:
        eng.config.TICKER_SETTINGS[symbol]["amount"] = float(data["capital"])
        
    if "threshold" in data and data["threshold"] is not None:
        eng.config.TICKER_SETTINGS[symbol]["min_buy_signals"] = int(data["threshold"])
    if "sell_threshold" in data and data["sell_threshold"] is not None:
        eng.config.TICKER_SETTINGS[symbol]["min_sell_signals"] = int(data["sell_threshold"])
    
    if "timeframe" in data:
        eng.config.TICKER_SETTINGS[symbol]["timeframe"] = data["timeframe"]
        
    if "sell_mode" in data:
        eng.config.TICKER_SETTINGS[symbol]["sell_mode"] = data["sell_mode"]
    else:
        eng.config.TICKER_SETTINGS[symbol]["sell_mode"] = "indicator"

    passthrough_settings = [
        "buy_mode",
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
    ]
    for key in passthrough_settings:
        if key in data:
            value = data[key]
            if value is None or value == "default":
                eng.config.TICKER_SETTINGS[symbol].pop(key, None)
            else:
                eng.config.TICKER_SETTINGS[symbol][key] = value
    if eng.config.TICKER_SETTINGS[symbol].get("smart_reentry_type"):
        eng.config.TICKER_SETTINGS[symbol]["smart_reentry_type"] = str(eng.config.TICKER_SETTINGS[symbol]["smart_reentry_type"]).upper()
    if eng.config.TICKER_SETTINGS[symbol].get("enable_signal_reset") is False:
        eng._clear_signal_reset_lock(symbol, eng.config.TICKER_SETTINGS[symbol])
    eng.config.TICKER_SETTINGS[symbol]["market_session"] = _normalize_market_session_for_symbol(
        symbol,
        eng.config.TICKER_SETTINGS[symbol].get("market_session")
    )
    eng.config.TICKER_SETTINGS[symbol]["market_session"] = market_session_check["market_session"]

    allocation_mode = str(eng.config.TICKER_SETTINGS[symbol].get("allocation_mode", "fixed") or "fixed").lower()
    if allocation_mode == "compound" and not eng.config.TICKER_SETTINGS[symbol].get("compound_amount"):
        eng.config.TICKER_SETTINGS[symbol]["compound_amount"] = float(eng.config.TICKER_SETTINGS[symbol].get("amount", data.get("capital", 0)) or 0)
    if allocation_mode != "compound":
        eng.config.TICKER_SETTINGS[symbol].pop("compound_amount", None)
        
    if "indicators" in data and isinstance(data["indicators"], list):
        eng.config.TICKER_SETTINGS[symbol]["indicators"] = data["indicators"]
    _normalize_bot_signal_thresholds(eng.config.TICKER_SETTINGS[symbol], eng)

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
        eng.config.TICKER_SETTINGS[symbol]["options_trade_amount"] = float(data["options_trade_amount"])
    options_allocation_mode = str(eng.config.TICKER_SETTINGS[symbol].get("options_allocation_mode", eng.config.TICKER_SETTINGS[symbol].get("allocation_mode", "fixed")) or "fixed").lower()
    if options_allocation_mode == "compound" and not eng.config.TICKER_SETTINGS[symbol].get("options_compound_amount"):
        eng.config.TICKER_SETTINGS[symbol]["options_compound_amount"] = float(eng.config.TICKER_SETTINGS[symbol].get("options_trade_amount", data.get("options_trade_amount", 0)) or 0)
    if options_allocation_mode != "compound":
        eng.config.TICKER_SETTINGS[symbol].pop("options_compound_amount", None)
    if "options_expiry_days" in data and data["options_expiry_days"] is not None:
        eng.config.TICKER_SETTINGS[symbol]["options_expiry_days"] = int(data["options_expiry_days"])
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

    if "ai_autopilot" in data:
        eng.config.TICKER_SETTINGS[symbol]["ai_autopilot"] = bool(data["ai_autopilot"])
    if "ai_model" in data:
        eng.config.TICKER_SETTINGS[symbol]["ai_model"] = data["ai_model"]

    if "paused" in data:
        eng.config.TICKER_SETTINGS[symbol]["paused"] = bool(data["paused"])
        if not data["paused"]:
            eng.config.TICKER_SETTINGS[symbol]["pause_reason"] = ""
            eng.config.TICKER_SETTINGS[symbol]["realized_pl"] = 0.0
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
        try:
            timeframe = data.get("timeframe") or eng.config.TICKER_SETTINGS.get(symbol, {}).get('timeframe') or eng.config.DEFAULT_TIMEFRAME
            is_crypto = _is_crypto_symbol(symbol)
            account = await asyncio.wait_for(asyncio.to_thread(eng.broker.get_account_info), timeout=2.5)
            avail_cash = account.get('non_marginable_buying_power') or account['cash'] if is_crypto else account['cash']
            
            initial_scan = await asyncio.wait_for(
                asyncio.to_thread(
                    evaluate_trade,
                    symbol,
                    account_equity=account['equity'],
                    available_cash=avail_cash,
                    timeframe=timeframe,
                    use_bot_settings=True,
                    ignore_cache=True
                ),
                timeout=3.5
            )
            if initial_scan:
                eng._record_bot_scan(initial_scan)
                _ensure_entry_forecast_snapshot(eng.config.TICKER_SETTINGS[symbol], initial_scan, symbol)
                positions = await asyncio.wait_for(asyncio.to_thread(eng.broker.get_positions), timeout=2.5)
                active_pos = _matching_position_for_ticker(positions, symbol, eng.config.TICKER_SETTINGS[symbol])
                _ensure_exit_forecast_snapshot(eng.config.TICKER_SETTINGS[symbol], initial_scan, active_pos, symbol)
                print(f"[bots/create] Background warm up scan completed for new bot {symbol}")
        except Exception as e:
            print(f"[bots/create] Background warm up scan failed for {symbol}: {e}")

        if not eng.config.TICKER_SETTINGS[symbol].get("forecast_entry_snapshot"):
            _ensure_entry_forecast_snapshot(eng.config.TICKER_SETTINGS[symbol], {}, symbol)
            
        await eng.save_settings()
        if eng.force_scan_trigger:
            eng.force_scan_trigger.set()

    asyncio.create_task(_background_warmup_and_save())
    return {"status": "success", "symbol": symbol, "watchlist": eng.config.WATCHLIST, "tradelist": eng.config.TRADELIST, "settings": eng.config.TICKER_SETTINGS[symbol]}


@app.post("/api/cancel_order")
async def cancel_order(data: dict, eng: UserEngine = Depends(get_user_engine)):
    """Cancel an active order on Alpaca by ID."""
    order_id = data.get("order_id")
    if not order_id:
        return {"status": "error", "message": "order_id is required"}
    result = eng.broker.cancel_order_by_id(order_id)
    if result.get("success"):
        return {"status": "success", "message": f"Order {order_id} cancelled successfully."}
    else:
        return {"status": "error", "message": result.get("error", "Failed to cancel order.")}


@app.post("/api/settings/indicators")
async def update_indicators(updates: dict, context: str = "dashboard", ticker: str = None, eng: UserEngine = Depends(get_user_engine)):
    """Update indicator toggles or parameters dynamically. Instant in-memory + persists to Firestore."""
    reset_confluence = bool(updates.pop("reset_confluence", False) or updates.pop("__reset_confluence", False))
    reset_all = bool(updates.pop("reset_all", False))
    
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
                    if isinstance(default_val, int): v = int(v)
                    elif isinstance(default_val, float): v = float(v)
                except Exception:
                    pass
                eng.config.TICKER_SETTINGS[ticker]['indicator_overrides'][k] = v
    else:
        from user_config import active_evaluation_context
        token = active_evaluation_context.set(context)
        try:
            if reset_all:
                ctx = context if context in ("dashboard", "bots", "backtest") else "dashboard"
                ctx_data = eng.config.contexts.setdefault(ctx, {"toggles": {}, "parameters": {}})
                ctx_data["parameters"].clear()
                ctx_data["toggles"].clear()
                for k in INDICATOR_PARAMETER_KEYS:
                    eng.config.set(k, None)
            elif reset_confluence:
                ctx = context if context in ("dashboard", "bots", "backtest") else "dashboard"
                ctx_data = eng.config.contexts.setdefault(ctx, {"toggles": {}, "parameters": {}})
                ctx_data["parameters"] = {}
                ctx_data["toggles"] = {
                    **{
                        k: v for k, v in ctx_data.get("toggles", {}).items()
                        if k not in DASHBOARD_CONFLUENCE_TOGGLE_KEYS
                    },
                    **{k: False for k in DASHBOARD_CONFLUENCE_TOGGLE_KEYS},
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
                            if isinstance(default_val, int):
                                eng.config.set(k, int(v))
                            elif isinstance(default_val, float):
                                eng.config.set(k, float(v))
                            else:
                                eng.config.set(k, v)
                        except Exception as e:
                            print(f"[settings] Type conversion error for {k}: {e}")
                            eng.config.set(k, v)
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
        print(f"[settings] Reset dashboard confluence for {eng.uid} ({context})")
        return {
            "status": "success",
            "reset": True,
            "save": "queued",
            "toggles": {k: False for k in DASHBOARD_CONFLUENCE_TOGGLE_KEYS},
            "parameters_reset": INDICATOR_PARAMETER_KEYS,
        }

    print(f"[settings] Updated indicator settings for {eng.uid}: {', '.join(updates.keys())}")
    return {"status": "success", "save": "queued"}


@app.post("/api/settings/ticker")
async def update_ticker_settings(data: dict, eng: UserEngine = Depends(get_user_engine)):
    """Update settings for a specific ticker."""
    ticker = data.get("ticker", "").upper()
    settings = data.get("settings", {})
    if not ticker: return {"status": "error"}

    if ticker not in eng.config.TICKER_SETTINGS:
        eng.config.TICKER_SETTINGS[ticker] = {}
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
    eng.config.TICKER_SETTINGS[ticker]["market_session"] = market_session_check["market_session"]

    current_settings = eng.config.TICKER_SETTINGS[ticker]
    if current_settings.get("smart_reentry_type"):
        current_settings["smart_reentry_type"] = str(current_settings["smart_reentry_type"]).upper()
    if current_settings.get("enable_signal_reset") is False:
        eng._clear_signal_reset_lock(ticker, current_settings)
    if current_settings.get("options_trading") and current_settings.get("sell_mode"):
        current_settings["options_exit_strategy"] = current_settings["sell_mode"]
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
        eng.config.TICKER_SETTINGS[ticker]['realized_pl'] = 0.0
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
        try:
            timeframe = current_settings.get('timeframe') or eng.config.DEFAULT_TIMEFRAME
            is_crypto = _is_crypto_symbol(ticker)
            account = await asyncio.wait_for(asyncio.to_thread(eng.broker.get_account_info), timeout=2.5)
            avail_cash = account.get('non_marginable_buying_power') or account['cash'] if is_crypto else account['cash']

            refreshed_scan = await asyncio.wait_for(
                asyncio.to_thread(
                    evaluate_trade,
                    ticker,
                    account_equity=account['equity'],
                    available_cash=avail_cash,
                    timeframe=timeframe,
                    use_bot_settings=True,
                    ignore_cache=True
                ),
                timeout=3.5
            )
            if refreshed_scan:
                eng._record_bot_scan(refreshed_scan)
                _ensure_entry_forecast_snapshot(current_settings, refreshed_scan, ticker)
                positions = await asyncio.wait_for(asyncio.to_thread(eng.broker.get_positions), timeout=2.5)
                active_pos = _matching_position_for_ticker(positions, ticker, current_settings)
                _ensure_exit_forecast_snapshot(current_settings, refreshed_scan, active_pos, ticker)
        except Exception as e:
            print(f"[settings/update] Background warm up scan failed for {ticker}: {e}")

        if not current_settings.get("forecast_entry_snapshot"):
            _ensure_entry_forecast_snapshot(current_settings, {}, ticker)

        await eng.save_settings()
        if eng.force_scan_trigger:
            eng.force_scan_trigger.set()

    asyncio.create_task(_background_warmup_and_save())
    return {"status": "success"}

@app.delete("/api/settings/ticker/{ticker}")
async def reset_ticker_settings(ticker: str, eng: UserEngine = Depends(get_user_engine)):
    """Reset a ticker to global defaults."""
    ticker = ticker.upper()
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
    
    return {
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
        return {}, "unavailable", f"Alpaca option data client unavailable: {exc}"

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
            last_error = f"{type(exc).__name__}: {exc}"
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
        'Botbulls4 News AI': 'ENABLE_AI_SENTIMENT',
        'Candle Patterns': 'ENABLE_CANDLE_PATTERNS',
        'ADX Trend': 'ENABLE_ADX_TREND',
        'SMA': 'ENABLE_SMA',
        'Prior Day High/Low': 'ENABLE_DAILY_HIGH_LOW',
        'Pivot Points (S/R)': 'ENABLE_PIVOT_POINTS',
        'Volume Profile': 'ENABLE_VOLUME_PROFILE',
        'Swing Points': 'ENABLE_SWING_POINTS',
        'BotBulls1': 'ENABLE_BOTBULLS1',
        'BotBulls2': 'ENABLE_BOTBULLS2',
        'BotBulls3': 'ENABLE_BOTBULLS3',
    }

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
        elif name == 'Botbulls4 News AI':
            enabled = getattr(uc, 'ENABLE_AI_SENTIMENT', True)
        elif allowed_indicators is not None:
            enabled = name in allowed_indicators
        else:
            enabled = getattr(uc, toggle_key, True) if toggle_key else True
            
        all_signals[name] = {**data, 'enabled': enabled, 'toggle_key': toggle_key if name != 'Botbulls4 News AI' else 'ENABLE_AI_SENTIMENT'}

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
        "rsi": scan.get("rsi", 0),
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
        "exit_monitoring": scan.get("exit_monitoring", False),
        "exit_signal_label": scan.get("exit_signal_label", ""),
        "exit_signal_count": scan.get("exit_signal_count", 0),
        "exit_signal_threshold": scan.get("exit_signal_threshold", 0),
        "exit_signal_direction": scan.get("exit_signal_direction", ""),
        "exit_watch_type": scan.get("exit_watch_type", ""),
        "exit_watch_details": scan.get("exit_watch_details", {}),
        "option_intent": scan.get("option_intent", "HOLD"),
        "has_position": scan.get("has_position", False),
    }

@app.get("/api/debug-doge")
async def get_debug_doge():
    if not user_manager.engines: return {"error": "no engines"}
    eng = list(user_manager.engines.values())[0]
    
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
