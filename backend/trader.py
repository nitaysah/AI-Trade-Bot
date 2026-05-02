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
from indicators import get_full_analysis
from sentiment import get_ai_sentiment
from risk_manager import RiskManager
import config


risk_mgr = RiskManager()


def evaluate_trade(ticker: str, account_equity: float = 100.0, timeframe: str = "5Min"):
    """
    Full evaluation pipeline for a single ticker.
    Combines technical signals + AI sentiment + risk management.
    
    Returns a comprehensive trade decision dict.
    """
    # 1. Get technical analysis
    analysis = get_full_analysis(ticker, timeframe)
    if not analysis:
        return None

def get_confluence_decision(analysis_results, ai_sentiment_score=0.0, ai_sentiment_confidence=0.0):
    """
    Pure decision logic based on technical and AI signals.
    Does not touch live APIs or risk managers.
    """
    bullish_count = analysis_results['bullish_count']
    bearish_count = analysis_results['bearish_count']
    
    signals = analysis_results['signals'].copy()

    # Factor in AI sentiment as a signal (only if enabled)
    if getattr(config, 'ENABLE_AI_SENTIMENT', True):
        if ai_sentiment_score >= config.SENTIMENT_BULLISH_THRESHOLD and ai_sentiment_confidence >= 0.4:
            bullish_count += 1
            signals['AI Sentiment'] = {
                'value': ai_sentiment_score,
                'signal': 'BULLISH',
                'reason': "Positive catalysts detected"
            }
        elif ai_sentiment_score <= config.SENTIMENT_BEARISH_THRESHOLD and ai_sentiment_confidence >= 0.4:
            bearish_count += 1
            signals['AI Sentiment'] = {
                'value': ai_sentiment_score,
                'signal': 'BEARISH',
                'reason': "Negative catalysts detected"
            }
        else:
            signals['AI Sentiment'] = {
                'value': ai_sentiment_score,
                'signal': 'NEUTRAL',
                'reason': "Mixed or weak news flow"
            }
    else:
        # If disabled, still show the value but don't count it towards action
        signals['AI Sentiment'] = {
            'value': ai_sentiment_score,
            'signal': 'NEUTRAL',
            'reason': "AI Sentiment Disabled",
            'enabled': False
        }

    # Determine action
    action = "HOLD"
    reason = "Neutral"

    if bullish_count >= config.MIN_BULLISH_SIGNALS:
        action = "BUY"
        bullish_names = [k for k, v in signals.items() if v.get('signal') == 'BULLISH']
        reason = f"{bullish_count} bullish signals ({', '.join(bullish_names)})"
    elif bearish_count >= config.MIN_BEARISH_SIGNALS:
        action = "SELL"
        bearish_names = [k for k, v in signals.items() if v.get('signal') == 'BEARISH']
        reason = f"{bearish_count} bearish signals ({', '.join(bearish_names)})"

    return {
        "action": action,
        "reason": reason,
        "bullish_count": bullish_count,
        "bearish_count": bearish_count,
        "signals": signals
    }


def evaluate_trade(ticker: str, account_equity: float = 100.0, available_cash: float = None, timeframe: str = "5Min"):
    """
    Full evaluation pipeline for a single ticker.
    Combines technical signals + AI sentiment + risk management.
    
    Returns a comprehensive trade decision dict.
    """
    # 1. Get technical analysis
    analysis = get_full_analysis(ticker, timeframe)
    if not analysis:
        return None

    # 2. Get AI sentiment
    ai_data = get_ai_sentiment(ticker)

    # 3. Get decision from confluence logic
    decision = get_confluence_decision(
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
        "time": datetime.now().strftime("%Y-%m-%d %H:%M"),
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
        "is_custom": (ticker.upper() in getattr(config, 'TICKER_AMOUNTS', {}))
    }


def get_risk_manager():
    """Exposes the risk manager instance."""
    return risk_mgr