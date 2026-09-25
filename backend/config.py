"""
Centralized configuration for the AI Trading Bot.
All strategy parameters, risk rules, and API keys in one place.
"""
import os
# pyrefly: ignore [missing-import]
from dotenv import load_dotenv
from indicator_catalog import EXTENSION_INDICATOR_DEFAULTS, EXTENSION_INDICATOR_TOGGLE_KEYS

_BASE_DIR = os.path.dirname(__file__)
_PROJECT_ROOT = os.path.dirname(_BASE_DIR)
_ENVIRONMENT = os.getenv("ENV", "production").strip().lower()

# Local dotenv files are a development convenience only. Production must use
# the process environment or a managed secret injector, never a checkout file.
if _ENVIRONMENT in {"development", "test"}:
    load_dotenv(os.path.join(_PROJECT_ROOT, ".env"))
    load_dotenv(os.path.join(_BASE_DIR, ".env"))

# ──────────────────────────────────────────────
# Timezone
# ──────────────────────────────────────────────
TIMEZONE = "US/Central"

# ──────────────────────────────────────────────
BROKER_TYPE = os.getenv("BROKER_TYPE", "alpaca")  # "alpaca" or "webull"

# ──────────────────────────────────────────────
# API Keys — Alpaca
# ──────────────────────────────────────────────
GROQ_API_KEY = os.getenv("GROQ_API_KEY", "")
ALPACA_API_KEY = os.getenv("ALPACA_API_KEY", "")
ALPACA_SECRET_KEY = os.getenv("ALPACA_SECRET_KEY", "")

# Set to True for paper trading, False for live
ALPACA_PAPER = True

# ──────────────────────────────────────────────
# API Keys — Webull
# ──────────────────────────────────────────────
WEBULL_APP_KEY = os.getenv("WEBULL_APP_KEY", "")
WEBULL_APP_SECRET = os.getenv("WEBULL_APP_SECRET", "")
WEBULL_ACCOUNT_ID = os.getenv("WEBULL_ACCOUNT_ID", "")  # Auto-discovered if blank
WEBULL_DATA_ONLY = os.getenv("WEBULL_DATA_ONLY", "true").lower() == "true"  # If True, skip trading/account calls
WEBULL_LIVE_TRADING_ENABLED = os.getenv("WEBULL_LIVE_TRADING_ENABLED", "false").lower() == "true"
WEBULL_SINGLE_TENANT_MODE = os.getenv("WEBULL_SINGLE_TENANT_MODE", "false").lower() == "true"
# Live broker access is fail-closed unless the operator explicitly binds the
# one allowed authenticated Firebase UID to this process. Paper/simulation
# workflows do not use this setting.
BROKER_LIVE_TENANT_UID = os.getenv("BROKER_LIVE_TENANT_UID", "").strip()
# SHA-256 prefix of the one broker account allowed for live writes. The raw
# account identifier is never logged or returned by the application.
BROKER_LIVE_ACCOUNT_FINGERPRINT = os.getenv("BROKER_LIVE_ACCOUNT_FINGERPRINT", "").strip().lower()

# ──────────────────────────────────────────────
# API Keys — Massive
# ──────────────────────────────────────────────
MASSIVE_API_KEY = os.getenv("MASSIVE_API_KEY", "")
MASSIVE_BASE_URL = os.getenv("MASSIVE_BASE_URL", "https://api.massive.com")

# ──────────────────────────────────────────────
# Watchlist — Tickers to scan
# ──────────────────────────────────────────────
WATCHLIST = ["TSLA", "AAPL", "MSFT", "AMZN", "BTCUSD"]

# ──────────────────────────────────────────────
# Trade List — Tickers the bot is allowed to trade
# ──────────────────────────────────────────────
TRADELIST = ["BTCUSD"]
DEFAULT_TIMEFRAME = "1Hour"
SCAN_INTERVAL_SECONDS = 60
OPTIONS_SCAN_INTERVAL_SECONDS = 10

# ──────────────────────────────────────────────
# Bot Safety Controls
# ──────────────────────────────────────────────
TRADE_COOLDOWN_SECONDS = 300    # Wait N seconds after a trade before re-evaluating same ticker
MARKET_HOURS_ONLY = True        # If True, stocks only trade during market hours (9:30-16:00 ET)
MAX_DAILY_DRAWDOWN = 0.05       # Halt all new bot entries after a 5% daily equity drawdown
GLOBAL_HARD_STOP_PCT = 0.10     # Halt all new bot entries after a 10% loss from equity high-water mark

# ──────────────────────────────────────────────
# Ticker Amounts — Overrides for specific trade sizes (in USD)
# If a ticker is here, it uses this amount instead of RISK_PER_TRADE %
# ──────────────────────────────────────────────
TICKER_AMOUNTS = {} 

# Ticker Settings — Per-asset overrides (amount, risk_per_trade, atr_stop_multiplier)
# Example: {"BTCUSD": {"amount": 100, "atr_stop_multiplier": 3.0}}
TICKER_SETTINGS = {}

# ══════════════════════════════════════════════
# INDICATOR TOGGLES (Enable / Disable)
# ══════════════════════════════════════════════
ENABLE_RSI = True
ENABLE_MACD = True
ENABLE_EMA = True
ENABLE_SUPERTREND = True
ENABLE_BOLLINGER = True
ENABLE_VWAP = True
ENABLE_MYSTIC_PULSE = True
ENABLE_AI_SENTIMENT = True
ENABLE_CANDLE_PATTERNS = True
ENABLE_ADX_TREND = True
ENABLE_SMA = True
ENABLE_DAILY_HIGH_LOW = True
ENABLE_PIVOT_POINTS = True
ENABLE_VOLUME_PROFILE = True
ENABLE_SWING_POINTS = True
ENABLE_BOTBULLS1 = True
ENABLE_BOTBULLS2 = True
ENABLE_BOTBULLS3 = True
ENABLE_BOTBULLS5 = True

# The added classical catalog is available everywhere but stays disabled in
# global confluence until the user explicitly enables an indicator. Bot and
# backtest selections still activate their chosen indicators directly.
for _toggle_key in EXTENSION_INDICATOR_TOGGLE_KEYS.values():
    globals()[_toggle_key] = False

# AI Indicator (Groq Agentic)
ENABLE_AI_INDICATOR = True
AI_INDICATOR_MODEL = "openai/gpt-oss-120b"
AI_INDICATOR_CACHE_MINUTES = 30
AI_DECISION_TIMEFRAME = "1Hour"
# Deterministic Quant AI gates.  The LLM explains the decision but does not
# override these execution-quality requirements.
AI_MIN_SETUP_SCORE = 70
AI_MIN_REWARD_RISK = 1.5
AI_MAX_ENTRY_RSI = 72.0
AI_MIN_VOLUME_RATIO = 0.80
AI_REQUIRE_VOLUME_CONFIRMATION = False
# News is a directional confirmation factor.  When flat, block a model entry
# that directly contradicts sufficiently confident news; HOLD is safer than
# forcing the opposite side.  Open-position risk exits are still permitted.
AI_NEWS_ALIGNMENT_MODE = "veto_conflicting_entries"
AI_NEWS_MIN_CONFIDENCE = 0.40
OLLAMA_HOST = os.getenv("OLLAMA_HOST", "http://localhost:11434").strip()

# AI Autopilot (uses AI Indicator for autonomous trade decisions in bots & backtest)
AI_AUTOPILOT_ENABLED = False                # Master toggle (per-ticker override via TICKER_SETTINGS)
AI_AUTOPILOT_BACKTEST_INTERVAL = 10         # Call Groq every N bars in backtest mode
AI_AUTOPILOT_MIN_CONFIDENCE = 0.55          # Minimum AI confidence to execute a trade
AI_AUTOPILOT_MODE = "override"              # "override" = AI replaces confluence, "confirm" = AI must agree with confluence


# ──────────────────────────────────────────────
# Technical Indicator Parameters
# ──────────────────────────────────────────────
RSI_PERIOD = 14
RSI_OVERBOUGHT = 70
RSI_OVERSOLD = 30
EMA_FAST = 9
EMA_SLOW = 21
EMA_USE_SECOND_LINE = True
MACD_FAST = 12
MACD_SLOW = 26
MACD_SIGNAL = 9
BOLL_PERIOD = 20
BOLL_STD_DEV = 2.0
SUPERTREND_PERIOD = 10
SUPERTREND_MULTIPLIER = 3.0
ATR_PERIOD = 14
DMI_PERIOD = 14
ADX_PERIOD = 14
ADX_TRENDING_THRESHOLD = 25
MYSTIC_PULSE_THRESHOLD = 5
VOL_MA_PERIOD = 20
VOL_SPIKE_MULTIPLIER = 1.5
SMA_PERIOD = 200
SMA_SECOND_PERIOD = 50
SMA_USE_SECOND_LINE = True
DAILY_HIGH_LOW_BUFFER_PCT = 0.0
PIVOT_LEVEL_COUNT = 1
PIVOT_TOLERANCE_PCT = 0.0
VOLUME_PROFILE_ROWS = 48
VOLUME_PROFILE_VALUE_AREA_PCT = 70.0
VOLUME_PROFILE_TOLERANCE_PCT = 0.15
VOLUME_PROFILE_CONFIRMATION_CANDLES = 1
SWING_POINTS_LOOKBACK_DAYS = 20
SWING_POINTS_ENTRY_MODE = "bounce_retest"
SWING_POINTS_LEVEL_COUNT = 1

# Shared industry defaults for the extended standard-indicator catalog.
globals().update(EXTENSION_INDICATOR_DEFAULTS)

# Premium BotBulls Parameters
BOTBULLS1_WT_CHANNEL = 10
BOTBULLS1_WT_AVERAGE = 21
BOTBULLS1_MFI_CONFIRM = 30
BOTBULLS1_WT_OVERSOLD = -55
BOTBULLS1_WT_OVERBOUGHT = 55
BOTBULLS1_MFI_BUY_RECLAIM = 35
BOTBULLS1_MFI_SELL_REJECT = 65
BOTBULLS1_CONFIRM_LOOKBACK = 3
BOTBULLS1_REQUIRE_MFI_CONFIRM = 1
BOTBULLS1_REQUIRE_CANDLE_CONFIRM = 1
BOTBULLS1_REQUIRE_PRICE_CONFIRM = 1
BOTBULLS1_PRICE_CONFIRM_EMA = 9
BOTBULLS1_PRICE_CONFIRM_LOOKBACK = 4

BOTBULLS2_ATR_MULT = 2.0
BOTBULLS2_TREND_TRACER_PERIOD = 50
BOTBULLS2_REVERSAL_ZONE_PERIOD = 50

BOTBULLS3_ATR_PERIOD = 10
BOTBULLS3_ATR_MULT = 1.0

# BotBulls5 — causal multi-pattern recognition and breakout confirmation.
# Defaults are deliberately balanced: forming patterns are shown on the chart,
# but confluence remains neutral until a completed candle confirms the break.
BOTBULLS5_POLE_LOOKBACK = 12
BOTBULLS5_CONSOLIDATION_MIN_BARS = 5
BOTBULLS5_CONSOLIDATION_MAX_BARS = 18
BOTBULLS5_POLE_ATR_MULT = 3.0
BOTBULLS5_MAX_RETRACE_PCT = 50.0
BOTBULLS5_MAX_WIDTH_PCT = 45.0
BOTBULLS5_PARALLEL_TOLERANCE = 0.35
BOTBULLS5_MIN_CONVERGENCE_PCT = 25.0
BOTBULLS5_BREAKOUT_BUFFER_ATR = 0.10
BOTBULLS5_VOLUME_CONTRACTION_MAX = 0.85
BOTBULLS5_BREAKOUT_VOLUME_MULT = 1.20
BOTBULLS5_MIN_CONFIDENCE = 70.0
BOTBULLS5_SIGNAL_HOLD_BARS = 3
BOTBULLS5_REQUIRE_VOLUME = 0
BOTBULLS5_ALLOW_PENNANTS = 1
BOTBULLS5_ALLOW_WEDGES = 1
BOTBULLS5_STRUCTURE_LOOKBACK = 48
BOTBULLS5_PIVOT_SPAN = 2
BOTBULLS5_LEVEL_TOLERANCE_PCT = 0.08
BOTBULLS5_ALLOW_REVERSALS = 1
BOTBULLS5_ALLOW_TRIANGLES = 1
BOTBULLS5_ALLOW_RANGES = 1
BOTBULLS5_ALLOW_ROUNDED = 1
BOTBULLS5_ALLOW_MEASURED_MOVES = 1

# ──────────────────────────────────────────────
# Strategy — Signal Confluence Thresholds
# ──────────────────────────────────────────────
MIN_BULLISH_SIGNALS = 4
MIN_BEARISH_SIGNALS = 4
SENTIMENT_BULLISH_PERCENTAGE = 70
SENTIMENT_BEARISH_PERCENTAGE = 30

# ──────────────────────────────────────────────
# Risk Management
# ──────────────────────────────────────────────
DEFAULT_TRADE_AMOUNT = 100.0  # Default notional amount per trade in USD
ATR_STOP_MULTIPLIER = 2.0
ATR_TRAIL_MULTIPLIER = 3.0
ATR_TAKE_PROFIT_MULTIPLIER = 4.0

# ──────────────────────────────────────────────
# Options Settings
# ──────────────────────────────────────────────
ENABLE_OPTIONS_TRADING = False
OPTIONS_TRADE_AMOUNT = 100.0         # Fixed USD allocation per options trade
OPTIONS_EXPIRY_DAYS = 7             # Expiry target for selected contracts
OPTIONS_STRIKE_TYPE = "ATM"          # "ATM" ~0.50 delta | "ITM" ~0.60 delta | "OTM" ~0.35 delta
OPTIONS_DELTA_TARGET = 0.50          # Legacy fallback; live selection now uses strike-type profiles
OPTIONS_EXIT_STRATEGY = "hybrid"     # "premium_pct" | "pivot_points" | "high_low" | "hybrid"
OPTIONS_TAKE_PROFIT_PCT = 1.00       # Take profit when premium reaches +100%
OPTIONS_CLOSE_BEFORE_EXPIRY_HOURS = 2.0  # Safe exit buffer before expiration

# Smart Re-entry / Risk filters
ENABLE_SMART_REENTRY = False
SMART_REENTRY_TYPE = "RSI"               # "RSI" | "VWAP"
SMART_REENTRY_RSI_THRESHOLD = 35         # Blocks BUY if RSI > 65, blocks SELL if RSI < 35
SMART_REENTRY_VWAP_PCT = 0.5             # Max allowed distance from VWAP (%)
ENABLE_SIGNAL_RESET = False
