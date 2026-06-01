"""
Strategy Engine — Signal Confluence & Trade Decisions.

This is the "brain" that orchestrates:
1. Fetching multi-indicator analysis
2. Getting AI sentiment
3. Applying signal confluence logic
4. Running risk management checks
5. Producing a final BUY / SELL / HOLD decision
"""

from datetime import datetime
import pytz
from indicators import get_full_analysis
from ai_indicator import get_ai_sentiment
from risk_manager import RiskManager
import config as global_config
from user_config import get_user_config


from concurrent.futures import ThreadPoolExecutor
import threading

risk_mgr = RiskManager()
executor = ThreadPoolExecutor(max_workers=10) # Dedicated pool for parallel analysis

import time

# --- Evaluation Cache ---
# Stores full trade evaluation results to prevent redundant heavy API calls
# within short windows (e.g. while user is navigating or UI is refreshing)
EVALUATION_CACHE = {} 
EVAL_CACHE_TTL = 45 # seconds
IN_FLIGHT_EVALS = {}
IN_FLIGHT_LOCK = threading.Lock()

def clear_evaluation_cache(ticker: str = None, timeframe: str = None):
    """Clear cached trade evaluations, optionally scoped by ticker/timeframe."""
    if ticker is None and timeframe is None:
        EVALUATION_CACHE.clear()
        return

    ticker_key = ticker.upper() if ticker else None
    timeframe_key = timeframe.upper() if timeframe else None
    for cache_key in list(EVALUATION_CACHE.keys()):
        cached_ticker, cached_timeframe = cache_key[0], cache_key[1]
        if ticker_key and cached_ticker != ticker_key:
            continue
        if timeframe_key and cached_timeframe != timeframe_key:
            continue
        EVALUATION_CACHE.pop(cache_key, None)

def get_now():
    """Returns current time in the configured timezone."""
    tz = pytz.timezone(get_user_config().TIMEZONE)
    return datetime.now(tz)


def get_confluence_decision(ticker, analysis_results, ai_sentiment_score=0.0, ai_sentiment_confidence=0.0, use_bot_settings=False):
    """
    Pure decision logic based on technical and AI signals.
    Respects global ENABLE_ toggles for technical indicators.
    """
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
        'BotBulls1': 'ENABLE_BOTBULLS1',
        'BotBulls2': 'ENABLE_BOTBULLS2',
        'BotBulls3': 'ENABLE_BOTBULLS3',
    }

    raw_signals = analysis_results.get('signals', {})
    filtered_signals = {}
    
    bullish_count = 0
    bearish_count = 0

    # 1. Process Technical Indicators
    uc = get_user_config()
    t_settings = getattr(uc, 'TICKER_SETTINGS', {}).get(ticker, {}) if use_bot_settings else {}
    allowed_indicators = t_settings.get('indicators') if use_bot_settings else None

    for name, data in raw_signals.items():
        # If allowed_indicators is set, only include those. Otherwise use get_user_config().ENABLE_X toggles.
        is_enabled = False
        if allowed_indicators is not None:
            is_enabled = name in allowed_indicators
        else:
            # Fallback to global config toggles (e.g. EMA Cross -> ENABLE_EMA_CROSS)
            toggle_key = SIGNAL_TO_TOGGLE.get(name)
            is_enabled = getattr(uc, toggle_key, True) if toggle_key else True

        # Add to output signals with enabled status
        filtered_signals[name] = {**data, 'enabled': is_enabled}
        
        # Only count towards decision if enabled
        if is_enabled:
            weight = data.get('weight', 1)
            if data.get('signal') == 'BULLISH':
                bullish_count += weight
            elif data.get('signal') == 'BEARISH':
                bearish_count += weight

    # 2. Factor in AI sentiment as a signal (only if enabled)
    ai_sentiment_enabled_for_bot = True
    if allowed_indicators is not None:
        ai_sentiment_enabled_for_bot = 'Sentiment AI' in allowed_indicators

    if ai_sentiment_enabled_for_bot and getattr(uc, 'ENABLE_AI_SENTIMENT', True):
        ai_enabled = True
        if ai_sentiment_score >= uc.SENTIMENT_BULLISH_THRESHOLD and ai_sentiment_confidence >= 0.4:
            bullish_count += 1
            filtered_signals['News Sentiment'] = {
                'value': ai_sentiment_score,
                'signal': 'BULLISH',
                'reason': "Positive catalysts detected",
                'enabled': True
            }
        elif ai_sentiment_score <= uc.SENTIMENT_BEARISH_THRESHOLD and ai_sentiment_confidence >= 0.4:
            bearish_count += 1
            filtered_signals['News Sentiment'] = {
                'value': ai_sentiment_score,
                'signal': 'BEARISH',
                'reason': "Negative catalysts detected",
                'enabled': True
            }
        else:
            filtered_signals['News Sentiment'] = {
                'value': ai_sentiment_score,
                'signal': 'NEUTRAL',
                'reason': "Mixed or weak news flow",
                'enabled': True
            }
    else:
        filtered_signals['News Sentiment'] = {
            'value': ai_sentiment_score,
            'signal': 'NEUTRAL',
            'reason': "News Sentiment Disabled",
            'enabled': False
        }

    # 3. Determine final action using per-ticker thresholds if available
    min_buy = t_settings.get('min_buy_signals', uc.MIN_BULLISH_SIGNALS)
    min_sell = t_settings.get('min_sell_signals', uc.MIN_BEARISH_SIGNALS)

    action = "HOLD"
    reason = "Neutral"

    if bullish_count >= min_buy and bearish_count >= min_sell:
        # Both thresholds met: pick the stronger one
        if bullish_count > bearish_count:
            action = "BUY"
            bullish_names = [k for k, v in filtered_signals.items() if v.get('signal') == 'BULLISH' and v.get('enabled')]
            reason = f"BUY Triggered (Conflict Resolved): {bullish_count}B > {bearish_count}S. Signals: {', '.join(bullish_names)}"
        elif bearish_count > bullish_count:
            action = "SELL"
            bearish_names = [k for k, v in filtered_signals.items() if v.get('signal') == 'BEARISH' and v.get('enabled')]
            reason = f"SELL Triggered (Conflict Resolved): {bearish_count}S > {bullish_count}B. Signals: {', '.join(bearish_names)}"
        else:
            action = "HOLD"
            reason = f"HOLD (Conflict Tie): Bullish ({bullish_count}) and Bearish ({bearish_count}) signals are equal and above threshold."
    elif bullish_count >= min_buy:
        action = "BUY"
        bullish_names = [k for k, v in filtered_signals.items() if v.get('signal') == 'BULLISH' and v.get('enabled')]
        reason = f"BUY Triggered: {bullish_count} bullish signals ({', '.join(bullish_names)})"
    elif bearish_count >= min_sell:
        action = "SELL"
        bearish_names = [k for k, v in filtered_signals.items() if v.get('signal') == 'BEARISH' and v.get('enabled')]
        reason = f"SELL Triggered: {bearish_count} bearish signals ({', '.join(bearish_names)})"
    
    if ticker in uc.TRADELIST:
        print(f"[trader] {ticker} Decision: {action} ({bullish_count}B/{bearish_count}S, need {min_buy}B/{min_sell}S) - {reason}")

    return {
        "action": action,
        "reason": reason,
        "bullish_count": bullish_count,
        "bearish_count": bearish_count,
        "signals": filtered_signals
    }


def evaluate_trade(ticker: str, account_equity: float = 100000.0, available_cash: float = None, timeframe: str = None, data_source: str = "webull", use_bot_settings: bool = False):
    """
    Full evaluation pipeline for a single ticker.
    Combines technical signals + AI sentiment + risk management.
    """
    uc = get_user_config()
    if timeframe is None:
        ticker_settings = getattr(uc, 'TICKER_SETTINGS', {}).get(ticker, {})
        timeframe = ticker_settings.get('timeframe', uc.DEFAULT_TIMEFRAME)

    # --- Check Evaluation Cache ---
    ticker_key = ticker.upper()
    timeframe_key = timeframe.upper()
    cache_key = (ticker_key, timeframe_key, data_source, use_bot_settings)
    now_ts = time.time()
    if cache_key in EVALUATION_CACHE:
        entry = EVALUATION_CACHE[cache_key]
        if now_ts - entry['timestamp'] < EVAL_CACHE_TTL:
            return entry['data']

    start_ts = time.perf_counter()
    analysis = None
    ai_data = {"score": 0.0, "confidence": 0.0}

    import contextvars

    def _compute():
        from user_config import active_ticker_context
        token = active_ticker_context.set(ticker_key) if use_bot_settings else None
        try:
            tech_ctx = contextvars.copy_context()
            ai_ctx = contextvars.copy_context()
            tech_future = executor.submit(tech_ctx.run, get_full_analysis, ticker_key, timeframe=timeframe, data_source=data_source)
            ai_future = executor.submit(ai_ctx.run, get_ai_sentiment, ticker_key)
            return tech_future.result(), ai_future.result()
        finally:
            if token: active_ticker_context.reset(token)

    with IN_FLIGHT_LOCK:
        shared_future = IN_FLIGHT_EVALS.get(cache_key)
        if shared_future is None:
            compute_ctx = contextvars.copy_context()
            shared_future = executor.submit(compute_ctx.run, _compute)
            IN_FLIGHT_EVALS[cache_key] = shared_future

    try:
        analysis, ai_data = shared_future.result()
    finally:
        with IN_FLIGHT_LOCK:
            if IN_FLIGHT_EVALS.get(cache_key) is shared_future:
                IN_FLIGHT_EVALS.pop(cache_key, None)

    if not analysis:
        print(f"[trader] WARNING: No technical analysis data for {ticker}")
        out = {
            "time": get_now().isoformat(),
            "ticker": ticker,
            "action": "HOLD",
            "price": "$0.00",
            "price_raw": 0.0,
            "reason": "Indicator data missing",
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
        print(f"[perf] evaluate_trade {ticker_key} {timeframe_key}: {(time.perf_counter() - start_ts) * 1000:.1f}ms (no analysis)")
        return out

    # 3. Get decision from confluence logic
    decision = get_confluence_decision(
        ticker,
        analysis, 
        ai_data['score'], 
        ai_data['confidence'],
        use_bot_settings=use_bot_settings
    )
    action = decision['action']
    reason = decision['reason']

    # 5. Calculate risk parameters (ONLY for active bots or when requested by UI)
    position_sizing = {}
    if ticker in uc.TRADELIST:
        trade_side = 'short' if action == 'SELL' else 'long'
        position_sizing = risk_mgr.calculate_position_size(
            ticker=ticker,
            entry_price=analysis['price'],
            account_equity=account_equity,
            available_cash=available_cash,
            atr=analysis.get('atr', 0),
            side=trade_side
        )

    # 6. Check daily drawdown
    can_trade = risk_mgr.check_drawdown(account_equity, ticker=ticker)
    if not can_trade and action != "HOLD":
        action = "HOLD"
        reason = f"Trading halted: {risk_mgr.halt_reason}"

    result = {
        "time": get_now().isoformat(),
        "action": action,
        "ticker": ticker,
        "price": f"${analysis['price']:.4f}" if analysis['price'] < 10 else f"${analysis['price']:.2f}",
        "price_raw": analysis['price'],
        "reason": reason,
        "sentiment_score": ai_data['score'],
        "sentiment_confidence": ai_data['confidence'],
        "sentiment_summary": ai_data.get('summary', ai_data.get('sentiment_summary', '')),
        "sentiment_key_factor": ai_data.get('key_factor', ''),
        "signals": decision['signals'],
        "bullish_count": decision['bullish_count'],
        "bearish_count": decision['bearish_count'],
        "total_signals": len(analysis['signals']),
        "atr": analysis['atr'],
        "rsi": analysis.get('rsi', 50),
        "position_sizing": position_sizing,
        "price_history": analysis.get('price_history', []),
        "risk_status": risk_mgr.get_risk_status(account_equity, ticker),
        "is_custom": (ticker.upper() in getattr(uc, 'TICKER_AMOUNTS', {})),
        "timeframe": timeframe
    }

    # Save to cache
    EVALUATION_CACHE[cache_key] = {
        "data": result,
        "timestamp": time.time()
    }
    print(f"[perf] evaluate_trade {ticker_key} {timeframe_key}: {(time.perf_counter() - start_ts) * 1000:.1f}ms")
    return result


def get_risk_manager():
    """Exposes the risk manager instance."""
    return risk_mgr
