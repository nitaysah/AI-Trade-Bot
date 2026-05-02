"""
Centralized configuration for the AI Trading Bot.
All strategy parameters, risk rules, and API keys in one place.
"""
import os
from dotenv import load_dotenv

load_dotenv()

# ──────────────────────────────────────────────
# API Keys
# ──────────────────────────────────────────────
GROQ_API_KEY = os.getenv("GROQ_API_KEY", "")
ALPACA_API_KEY = os.getenv("ALPACA_API_KEY", "")
ALPACA_SECRET_KEY = os.getenv("ALPACA_SECRET_KEY", "")

# Set to True for paper trading, False for live
ALPACA_PAPER = True

# ──────────────────────────────────────────────
# Watchlist — Tickers to scan
# ──────────────────────────────────────────────
WATCHLIST = ["TSLA", "AAPL", "MSFT", "AMZN", "BTCUSD"]

# ──────────────────────────────────────────────
# Trade List — Tickers the bot is allowed to trade
# ──────────────────────────────────────────────
TRADELIST = ["BTCUSD"]
DEFAULT_TIMEFRAME = "5Min"
SCAN_INTERVAL_SECONDS = 60

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

# ──────────────────────────────────────────────
# Technical Indicator Parameters
# ──────────────────────────────────────────────
RSI_PERIOD = 14
RSI_OVERBOUGHT = 70
RSI_OVERSOLD = 30
EMA_FAST = 9
EMA_SLOW = 21
MACD_FAST = 12
MACD_SLOW = 26
MACD_SIGNAL = 9
BOLL_PERIOD = 20
BOLL_STD_DEV = 2.0
SUPERTREND_PERIOD = 10
SUPERTREND_MULTIPLIER = 3.0
ATR_PERIOD = 14
DMI_PERIOD = 14
MYSTIC_PULSE_THRESHOLD = 5

# ──────────────────────────────────────────────
# Strategy — Signal Confluence Thresholds
# ──────────────────────────────────────────────
MIN_BULLISH_SIGNALS = 5
MIN_BEARISH_SIGNALS = 5
SENTIMENT_BULLISH_THRESHOLD = 0.5
SENTIMENT_BEARISH_THRESHOLD = -0.5

# ──────────────────────────────────────────────
# Risk Management
# ──────────────────────────────────────────────
RISK_PER_TRADE = 0.02
MAX_DAILY_DRAWDOWN = 0.05
MAX_POSITION_PCT = 0.25
ATR_STOP_MULTIPLIER = 2.0
ATR_TRAIL_MULTIPLIER = 3.0
ATR_TAKE_PROFIT_MULTIPLIER = 4.0
