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
from sentiment import get_ai_sentiment
from risk_manager import RiskManager
import config


from concurrent.futures import ThreadPoolExecutor

risk_mgr = RiskManager()
executor = ThreadPoolExecutor(max_workers=10) # Dedicated pool for parallel analysis

import time

# --- Evaluation Cache ---
# Stores full trade evaluation results to prevent redundant heavy API calls
# within short windows (e.g. while user is navigating or UI is refreshing)
EVALUATION_CACHE = {} 
EVAL_CACHE_TTL = 15 # seconds

def get_now():
    """Returns current time in the configured timezone."""
    tz = pytz.timezone(config.TIMEZONE)
    return datetime.now(tz)




def get_confluence_decision(ticker, analysis_results, ai_sentiment_score=0.0, ai_sentiment_confidence=0.0):
    """
    Pure decision logic based on technical and AI signals.
    Respects global ENABLE_ toggles for technical indicators.
    """
    # Map Signal Names to Config Keys
    SIGNAL_TO_TOGGLE = {
        'RSI': 'ENABLE_RSI',
        'MACD': 'ENABLE_MACD',
        'EMA Cross': 'ENABLE_EMA',
        'Supertrend': 'ENABLE_SUPERTREND',
        'Bollinger': 'ENABLE_BOLLINGER',
        'VWAP': 'ENABLE_VWAP',
        'Mystic Pulse': 'ENABLE_MYSTIC_PULSE',
        'Candle Patterns': 'ENABLE_CANDLE_PATTERNS',
    }

    raw_signals = analysis_results.get('signals', {})
    filtered_signals = {}
    
    bullish_count = 0
    bearish_count = 0

    # 1. Process Technical Indicators
    t_settings = getattr(config, 'TICKER_SETTINGS', {}).get(ticker, {})
    allowed_indicators = t_settings.get('indicators') # List of names if overridden

    for name, data in raw_signals.items():
        # If allowed_indicators is set, only include those. Otherwise use config.ENABLE_X toggles.
        is_enabled = False
        if allowed_indicators is not None:
            is_enabled = name in allowed_indicators
        else:
            # Fallback to global config toggles (e.g. EMA Cross -> ENABLE_EMA_CROSS)
            toggle_key = SIGNAL_TO_TOGGLE.get(name)
            is_enabled = getattr(config, toggle_key, True) if toggle_key else True

        # Add to output signals with enabled status
        filtered_signals[name] = {**data, 'enabled': is_enabled}
        
        # Only count towards decision if enabled
        if is_enabled:
            if data.get('signal') == 'BULLISH':
                bullish_count += 1
            elif data.get('signal') == 'BEARISH':
                bearish_count += 1

    # 2. Factor in AI sentiment as a signal (only if enabled)
    if getattr(config, 'ENABLE_AI_SENTIMENT', True):
        ai_enabled = True
        if ai_sentiment_score >= config.SENTIMENT_BULLISH_THRESHOLD and ai_sentiment_confidence >= 0.4:
            bullish_count += 1
            filtered_signals['News Sentiment'] = {
                'value': ai_sentiment_score,
                'signal': 'BULLISH',
                'reason': "Positive catalysts detected",
                'enabled': True
            }
        elif ai_sentiment_score <= config.SENTIMENT_BEARISH_THRESHOLD and ai_sentiment_confidence >= 0.4:
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
    t_settings = getattr(config, 'TICKER_SETTINGS', {}).get(ticker, {})
    min_buy = t_settings.get('min_buy_signals', config.MIN_BULLISH_SIGNALS)
    min_sell = t_settings.get('min_sell_signals', config.MIN_BEARISH_SIGNALS)

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
    
    if ticker in config.TRADELIST:
        print(f"[trader] {ticker} Decision: {action} ({bullish_count}B/{bearish_count}S, need {min_buy}B/{min_sell}S) - {reason}")

    return {
        "action": action,
        "reason": reason,
        "bullish_count": bullish_count,
        "bearish_count": bearish_count,
        "signals": filtered_signals
    }


def evaluate_trade(ticker: str, account_equity: float = 100000.0, available_cash: float = None, timeframe: str = None):
    """
    Full evaluation pipeline for a single ticker.
    Combines technical signals + AI sentiment + risk management.
    
    Returns a comprehensive trade decision dict.
    """
    # Priority: 1. Argument, 2. Ticker-specific setting, 3. Global default
    if timeframe is None:
        ticker_settings = getattr(config, 'TICKER_SETTINGS', {}).get(ticker, {})
        timeframe = ticker_settings.get('timeframe', config.DEFAULT_TIMEFRAME)

    # --- Check Evaluation Cache ---
    cache_key = (ticker.upper(), timeframe.upper())
    now_ts = time.time()
    if cache_key in EVALUATION_CACHE:
        entry = EVALUATION_CACHE[cache_key]
        if now_ts - entry['timestamp'] < EVAL_CACHE_TTL:
            # print(f"[trader] Using cached evaluation for {ticker} ({timeframe})")
            return entry['data']

    # 1. Start both Technical Analysis and AI Sentiment IN PARALLEL
    # This prevents AI analysis (slow) from blocking technical signals
    tech_future = executor.submit(get_full_analysis, ticker, timeframe=timeframe)
    ai_future = executor.submit(get_ai_sentiment, ticker)

    # 2. Wait for results
    analysis = tech_future.result()
    ai_data = ai_future.result()

    if not analysis:
        print(f"[trader] WARNING: No technical analysis data for {ticker}")
        return {
            "ticker": ticker,
            "action": "HOLD",
            "reason": "Indicator data missing",
            "signals": {},
            "price_history": [],
            "timeframe": timeframe
        }

    # 3. Get decision from confluence logic
    decision = get_confluence_decision(
        ticker,
        analysis, 
        ai_data['score'], 
        ai_data['confidence']
    )
    action = decision['action']
    reason = decision['reason']

    # 5. Calculate risk parameters (ONLY for active bots or when requested by UI)
    position_sizing = {}
    if ticker in config.TRADELIST:
        position_sizing = risk_mgr.calculate_position_size(
            ticker=ticker,
            entry_price=analysis['price'],
            account_equity=account_equity,
            available_cash=available_cash,
            atr=analysis.get('atr', 0)
        )

    # 6. Check daily drawdown
    can_trade = risk_mgr.check_drawdown(account_equity)
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
        "risk_status": risk_mgr.get_risk_status(account_equity),
        "is_custom": (ticker.upper() in getattr(config, 'TICKER_AMOUNTS', {})),
        "timeframe": timeframe
    }

    # Save to cache
    EVALUATION_CACHE[cache_key] = {
        "data": result,
        "timestamp": time.time()
    }
    return result


def get_risk_manager():
    """Exposes the risk manager instance."""
    return risk_mgr