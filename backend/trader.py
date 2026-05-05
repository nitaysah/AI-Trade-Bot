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


risk_mgr = RiskManager()
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
    for name, data in raw_signals.items():
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
            filtered_signals['AI Sentiment'] = {
                'value': ai_sentiment_score,
                'signal': 'BULLISH',
                'reason': "Positive catalysts detected",
                'enabled': True
            }
        elif ai_sentiment_score <= config.SENTIMENT_BEARISH_THRESHOLD and ai_sentiment_confidence >= 0.4:
            bearish_count += 1
            filtered_signals['AI Sentiment'] = {
                'value': ai_sentiment_score,
                'signal': 'BEARISH',
                'reason': "Negative catalysts detected",
                'enabled': True
            }
        else:
            filtered_signals['AI Sentiment'] = {
                'value': ai_sentiment_score,
                'signal': 'NEUTRAL',
                'reason': "Mixed or weak news flow",
                'enabled': True
            }
    else:
        filtered_signals['AI Sentiment'] = {
            'value': ai_sentiment_score,
            'signal': 'NEUTRAL',
            'reason': "AI Sentiment Disabled",
            'enabled': False
        }

    # 3. Determine final action
    action = "HOLD"
    reason = "Neutral"

    if bullish_count >= config.MIN_BULLISH_SIGNALS:
        action = "BUY"
        bullish_names = [k for k, v in filtered_signals.items() if v.get('signal') == 'BULLISH' and v.get('enabled')]
        reason = f"BUY Triggered: {bullish_count} bullish signals ({', '.join(bullish_names)})"
    elif bearish_count >= config.MIN_BEARISH_SIGNALS:
        action = "SELL"
        bearish_names = [k for k, v in filtered_signals.items() if v.get('signal') == 'BEARISH' and v.get('enabled')]
        reason = f"SELL Triggered: {bearish_count} bearish signals ({', '.join(bearish_names)})"
    
    if ticker in config.TRADELIST:
        print(f"[trader] {ticker} Decision: {action} ({bullish_count}B/{bearish_count}S) - {reason}")

    return {
        "action": action,
        "reason": reason,
        "bullish_count": bullish_count,
        "bearish_count": bearish_count,
        "signals": filtered_signals
    }


def evaluate_trade(ticker: str, account_equity: float = 100000.0, available_cash: float = None, timeframe: str = "5Min"):
    """
    Full evaluation pipeline for a single ticker.
    Combines technical signals + AI sentiment + risk management.
    
    Returns a comprehensive trade decision dict.
    """
    # 1. Get technical analysis
    analysis = get_full_analysis(ticker, timeframe=timeframe)
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

    # 2. Get AI sentiment
    ai_data = get_ai_sentiment(ticker)

    # 3. Get decision from confluence logic
    decision = get_confluence_decision(
        ticker,
        analysis, 
        ai_data['score'], 
        ai_data['confidence']
    )
    action = decision['action']
    reason = decision['reason']

    # 5. Calculate risk parameters
    position_sizing = risk_mgr.calculate_position_size(
        account_equity=account_equity,
        entry_price=analysis['price'],
        atr=analysis['atr'],
        available_cash=available_cash,
        ticker=ticker
    )

    # 6. Check daily drawdown
    can_trade = risk_mgr.check_drawdown(account_equity)
    if not can_trade and action != "HOLD":
        action = "HOLD"
        reason = f"Trading halted: {risk_mgr.halt_reason}"

    return {
        "time": get_now().isoformat(),
        "action": action,
        "ticker": ticker,
        "price": f"${analysis['price']:.2f}",
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


def get_risk_manager():
    """Exposes the risk manager instance."""
    return risk_mgr