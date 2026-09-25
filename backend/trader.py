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
from ai_indicator import get_ai_sentiment, sentiment_percentage, sentiment_direction
from risk_manager import RiskManager
from swing_strategy import detect_swing_entry, build_entry_signal
from options_direction import normalize_options_type, VALID_OPTION_DIRECTIONS
from prior_day_high_low import resolve_high_low_buffer_pct
from indicator_catalog import EXTENSION_INDICATOR_TOGGLE_KEYS
import config as global_config
from user_config import active_evaluation_context, get_user_config


from concurrent.futures import ThreadPoolExecutor
import threading
import hashlib
import json

risk_mgr = RiskManager()
executor = ThreadPoolExecutor(max_workers=10) # Dedicated pool for parallel analysis
# Orchestration waits on these workers. Keeping child jobs in a separate pool
# prevents all orchestration workers from waiting on their own queued jobs.
_calculation_executor = ThreadPoolExecutor(max_workers=10, thread_name_prefix="analysis")

import time

# --- Evaluation Cache ---
# Stores full trade evaluation results to prevent redundant heavy API calls
# within short windows (e.g. while user is navigating or UI is refreshing)
from cache_manager import TTLCacheStore

_evaluation_cache = TTLCacheStore(maxsize=128)
EVAL_CACHE_TTL = 45 # seconds
IN_FLIGHT_EVALS = {}
IN_FLIGHT_LOCK = threading.Lock()

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
    'BotBulls1': 'ENABLE_BOTBULLS1',
    'BotBulls2': 'ENABLE_BOTBULLS2',
    'BotBulls3': 'ENABLE_BOTBULLS3',
    'BotBulls5': 'ENABLE_BOTBULLS5',
}
SIGNAL_TO_TOGGLE.update(EXTENSION_INDICATOR_TOGGLE_KEYS)


def _analysis_settings_for_trade(uc, ticker_settings: dict, use_bot_settings: bool) -> dict:
    """Pass enabled indicators into calculations so disabled UI signals do not all compute."""
    if use_bot_settings:
        settings = dict((ticker_settings or {}).get("indicator_overrides", {}) or {})
        indicators = (ticker_settings or {}).get("indicators")
        if isinstance(indicators, list):
            settings["enabled_indicators"] = [
                str(indicator) for indicator in indicators
                if str(indicator or "").strip()
            ]
        # Keep the scanner and chart preview on the same market/session and
        # strategy dependency inputs. Omitting market_session here made the
        # scanner silently use extended-hours candles while the Screener chart
        # used the profile's Regular session, which can flip a 50/200 SMA vote.
        for key in (
            "market_session",
            "buy_mode",
            "sell_mode",
            "options_trading",
            "options_type",
            "_botbulls5_recent_session_only",
        ):
            if key in (ticker_settings or {}):
                settings[key] = ticker_settings.get(key)
        if (
            bool((ticker_settings or {}).get("enable_smart_reentry", False))
            and str((ticker_settings or {}).get("smart_reentry_type", "RSI")).upper() == "RSI"
        ):
            settings["_require_rsi_dependency"] = True
        return settings

    settings = dict(uc.active_parameters or {})
    toggles = uc.active_toggles or {}
    settings["enabled_indicators"] = [
        name for name, toggle_key in SIGNAL_TO_TOGGLE.items()
        if name != "Botbulls4 News AI" and bool(toggles.get(toggle_key, True))
    ]
    return settings


def _evaluation_cache_key(uc, ticker_key: str, timeframe_key: str, data_source: str, use_bot_settings: bool, account_equity, available_cash, has_position=False):
    """Scope cached evaluations to one user and their active strategy/risk inputs."""
    ticker_settings = getattr(uc, 'TICKER_SETTINGS', {}).get(ticker_key, {})
    payload = {
        "uid": getattr(uc, "uid", "default"),
        "ticker_settings": ticker_settings,
        "active_toggles": getattr(uc, "active_toggles", {}),
        "active_parameters": getattr(uc, "active_parameters", {}),
        "tradelist": getattr(uc, "TRADELIST", []),
        "ticker_amount": getattr(uc, "TICKER_AMOUNTS", {}).get(ticker_key),
        "account_equity": round(float(account_equity or 0), 2),
        "available_cash": round(float(available_cash or 0), 2),
        "has_position": bool(has_position),
        "evaluation_context": active_evaluation_context.get(),
    }
    settings_hash = hashlib.sha256(json.dumps(payload, sort_keys=True, default=str).encode()).hexdigest()[:16]
    return (
        ticker_key,
        timeframe_key,
        data_source,
        bool(use_bot_settings),
        getattr(uc, "uid", "default"),
        settings_hash,
    )

def clear_evaluation_cache(ticker: str = None, timeframe: str = None):
    """Clear cached trade evaluations, optionally scoped by ticker/timeframe."""
    if ticker is None and timeframe is None:
        _evaluation_cache.clear()
        return

    ticker_key = ticker.upper() if ticker else None
    timeframe_key = timeframe.upper() if timeframe else None
    
    _evaluation_cache.delete_matching(
        lambda k: (not ticker_key or k[0] == ticker_key) and (not timeframe_key or k[1] == timeframe_key)
    )

def get_now():
    """Returns current time in the configured timezone."""
    tz = pytz.timezone(get_user_config().TIMEZONE)
    return datetime.now(tz)


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


def get_confluence_decision(
    ticker,
    analysis_results,
    ai_sentiment_score=0.0,
    ai_sentiment_confidence=0.0,
    use_bot_settings=False,
    options_type="calls_only",
    has_position=False,
):
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
        'Prior Day High/Low': 'ENABLE_DAILY_HIGH_LOW',
        'Pivot Points (S/R)': 'ENABLE_PIVOT_POINTS',
        'Volume Profile': 'ENABLE_VOLUME_PROFILE',
        'Swing Points': 'ENABLE_SWING_POINTS',
        'BotBulls1': 'ENABLE_BOTBULLS1',
        'BotBulls2': 'ENABLE_BOTBULLS2',
        'BotBulls3': 'ENABLE_BOTBULLS3',
        'BotBulls5': 'ENABLE_BOTBULLS5',
    }
    SIGNAL_TO_TOGGLE.update(EXTENSION_INDICATOR_TOGGLE_KEYS)

    raw_signals = analysis_results.get('signals', {})
    filtered_signals = {}
    
    bullish_count = 0
    bearish_count = 0

    # 1. Process Technical Indicators
    uc = get_user_config()
    t_settings = getattr(uc, 'TICKER_SETTINGS', {}).get(ticker, {}) if use_bot_settings else {}
    allowed_indicators = t_settings.get('indicators') if use_bot_settings else None
    indicator_entry_modes = {
        'indicator', 'hybrid', 'hybrid_atr', 'hybrid_pct', 'hybrid_pivot',
        'hybrid_high_low', 'hybrid_swing',
    }
    if allowed_indicators is None:
        has_indicator_pool = True  # legacy profiles use the global toggle set
    elif isinstance(allowed_indicators, list):
        has_indicator_pool = any(str(name or '').strip() for name in allowed_indicators)
    else:
        has_indicator_pool = bool(allowed_indicators)

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
        ai_sentiment_enabled_for_bot = 'Botbulls4 News AI' in allowed_indicators

    if ai_sentiment_enabled_for_bot and getattr(uc, 'ENABLE_AI_SENTIMENT', True):
        ai_enabled = True
        
        news_pct = sentiment_percentage(ai_sentiment_score)
        news_dir = sentiment_direction(ai_sentiment_score, ai_sentiment_confidence, uc)

        if news_dir == 'BULLISH':
            bullish_count += 1
            filtered_signals['Botbulls4 News AI'] = {
                'value': ai_sentiment_score,
                'percentage': round(news_pct, 1),
                'sentiment_pct': round(news_pct, 1),
                'sentiment_direction': news_dir,
                'signal': 'BULLISH',
                'reason': f"Positive catalysts detected ({news_pct:.0f}% news sentiment)",
                'enabled': True,
                'premium': True
            }
        elif news_dir == 'BEARISH':
            bearish_count += 1
            filtered_signals['Botbulls4 News AI'] = {
                'value': ai_sentiment_score,
                'percentage': round(news_pct, 1),
                'sentiment_pct': round(news_pct, 1),
                'sentiment_direction': news_dir,
                'signal': 'BEARISH',
                'reason': f"Negative catalysts detected ({news_pct:.0f}% news sentiment)",
                'enabled': True,
                'premium': True
            }
        else:
            filtered_signals['Botbulls4 News AI'] = {
                'value': ai_sentiment_score,
                'percentage': round(news_pct, 1),
                'sentiment_pct': round(news_pct, 1),
                'sentiment_direction': news_dir,
                'signal': 'NEUTRAL',
                'reason': "Mixed or weak news flow",
                'enabled': True,
                'premium': True
            }
    else:
        filtered_signals['Botbulls4 News AI'] = {
            'value': ai_sentiment_score,
            'percentage': round(sentiment_percentage(ai_sentiment_score), 1),
            'sentiment_pct': round(sentiment_percentage(ai_sentiment_score), 1),
            'sentiment_direction': 'NEUTRAL',
            'signal': 'NEUTRAL',
            'reason': "News Sentiment Disabled",
            'enabled': False,
            'premium': True
        }

    min_buy = _get_threshold_setting(t_settings, ('min_buy_signals', 'threshold', 'buy_threshold'), uc.MIN_BULLISH_SIGNALS)
    min_sell = _get_threshold_setting(t_settings, ('min_sell_signals', 'sell_threshold'), uc.MIN_BEARISH_SIGNALS)
    buy_mode = t_settings.get('buy_mode', 'indicator')
    custom_buy_price = t_settings.get('custom_buy_price', 0.0)
    swing_entry_mode = t_settings.get('swing_entry_mode', 'bounce_retest')
    swing_entry_lookback_days = int(t_settings.get('swing_entry_lookback_days', t_settings.get('swing_lookback_days', 20)) or 20)
    entry_atr_mult = float(t_settings.get('entry_atr_multiplier', t_settings.get('atr_stop_multiplier', 2.0)) or 2.0)
    entry_pct_move = float(t_settings.get('entry_pct_move', max(1.0, float(t_settings.get('regular_stop_loss_pct', 0.05)) * 100.0)) or 1.0)
    entry_atr_style = t_settings.get('entry_atr_style', 'pullback_reclaim')
    entry_pct_style = t_settings.get('entry_pct_style', 'dip_reclaim')
    market_session = t_settings.get('market_session', 'Regular')
    is_crypto = any(str(ticker).upper().endswith(base) for base in ["USD", "USDT", "USDC"]) or ticker.upper() in ["BTC", "ETH", "XRP", "DOGE", "LTC", "ADA", "SOL"]
    current_price = analysis_results.get('price', 0.0)
    high_low_buffer_pct = resolve_high_low_buffer_pct(t_settings)
    entry_mode = buy_mode

    action = "HOLD"
    reason = "Neutral"
    option_intent = "HOLD"

    bullish_names = [k for k, v in filtered_signals.items() if v.get('signal') == 'BULLISH' and v.get('enabled')]
    bearish_names = [k for k, v in filtered_signals.items() if v.get('signal') == 'BEARISH' and v.get('enabled')]
    bullish_ready = bullish_count >= min_buy
    bearish_ready = bearish_count >= min_sell
    indicator_setup_blocked = (
        use_bot_settings
        and buy_mode in indicator_entry_modes
        and not has_indicator_pool
    )
    if indicator_setup_blocked:
        indicator_direction = "NONE"
        indicator_reason = "no indicators selected"
        bullish_ready = False
        bearish_ready = False
    elif bullish_ready and bearish_ready:
        if bullish_count > bearish_count:
            indicator_direction = "BULLISH"
            indicator_reason = f"{bullish_count} bullish signals > {bearish_count} bearish signals"
        elif bearish_count > bullish_count:
            indicator_direction = "BEARISH"
            indicator_reason = f"{bearish_count} bearish signals > {bullish_count} bullish signals"
        else:
            indicator_direction = "CONFLICT"
            indicator_reason = f"bullish and bearish signals tied at {bullish_count}"
    elif bullish_ready:
        indicator_direction = "BULLISH"
        indicator_reason = f"{bullish_count} bullish signals ({', '.join(bullish_names)})"
    elif bearish_ready:
        indicator_direction = "BEARISH"
        indicator_reason = f"{bearish_count} bearish signals ({', '.join(bearish_names)})"
    else:
        indicator_direction = "NONE"
        indicator_reason = f"waiting for thresholds ({bullish_count}B/{bearish_count}S, need {min_buy}B/{min_sell}S)"

    is_options_bot = bool(t_settings.get("options_trading")) if use_bot_settings else False
    option_side = normalize_options_type(options_type or t_settings.get("options_type"), default="calls_only")

    if is_options_bot:
        if option_side not in VALID_OPTION_DIRECTIONS:
            action = "HOLD"
            reason = "HOLD: Both direction is disabled. Choose CALL or PUT."
            option_intent = "DISABLED"
            return {
                "action": action,
                "option_intent": option_intent,
                "reason": reason,
                "bullish_count": bullish_count,
                "bearish_count": bearish_count,
                "signals": filtered_signals,
                "indicator_direction": indicator_direction,
                "structure_direction": "NONE",
            }

        desired_direction = "BULLISH" if option_side == "calls_only" else "BEARISH"
        expected_action = "BUY" if desired_direction == "BULLISH" else "SELL"
        contract_label = "CALL" if option_side == "calls_only" else "PUT"
        option_intent_open = "OPEN_CALL" if contract_label == "CALL" else "OPEN_PUT"
        indicator_ok = indicator_direction == desired_direction

        structural_modes = {
            "custom_price", "atr", "percentage", "pivot_points", "high_low", "swing_points",
            "hybrid_atr", "hybrid_pct", "hybrid_pivot", "hybrid_high_low", "hybrid_swing",
        }
        hybrid_modes = {"hybrid_atr", "hybrid_pct", "hybrid_pivot", "hybrid_high_low", "hybrid_swing"}
        structure_direction = "NONE"
        structure_reason = ""
        structure_ok = False

        if buy_mode in structural_modes:
            entry_signal = build_entry_signal(
                analysis_results.get('price_history', []),
                current_price=current_price,
                entry_mode=buy_mode,
                lookback_days=swing_entry_lookback_days,
                market_session=market_session,
                is_crypto=is_crypto,
                atr=analysis_results.get('atr', 0.0),
                custom_buy_price=custom_buy_price,
                entry_atr_mult=entry_atr_mult,
                entry_pct_move=entry_pct_move,
                entry_atr_style=entry_atr_style,
                entry_pct_style=entry_pct_style,
                swing_entry_mode=swing_entry_mode,
                desired_direction=desired_direction,
                high_low_buffer_pct=high_low_buffer_pct,
            )
            structure_direction = entry_signal.get("structure_direction", "NONE")
            structure_reason = entry_signal.get("reason", "")
            structure_ok = entry_signal.get("action") == expected_action and structure_direction == desired_direction

        if buy_mode == "indicator":
            if indicator_ok:
                action = expected_action
                option_intent = option_intent_open
                reason = f"{contract_label} Entry Triggered: {indicator_reason}"
            elif indicator_direction in {"BULLISH", "BEARISH"}:
                action = "HOLD"
                reason = f"HOLD: {indicator_direction.title()} signals ignored because bot direction is {contract_label}."
            else:
                action = "HOLD"
                reason = f"{contract_label} indicator entry waiting: {indicator_reason}"
        elif buy_mode in hybrid_modes:
            mode_upper = str(buy_mode).upper()
            struct_label = 'structure'
            if 'ATR' in mode_upper: struct_label = 'ATR'
            elif 'PCT' in mode_upper: struct_label = 'Pct'
            elif 'PIVOT' in mode_upper: struct_label = 'Pivot'
            elif 'HIGH_LOW' in mode_upper or 'HL' in mode_upper: struct_label = 'H/L'
            elif 'SWING' in mode_upper: struct_label = 'Swing'

            if indicator_ok and structure_ok:
                action = expected_action
                option_intent = option_intent_open
                reason = f"{mode_upper} Triggered: {indicator_reason} AND {structure_reason}"
            elif indicator_ok:
                action = "HOLD"
                reason = f"{mode_upper} Waiting: {contract_label} indicators confirmed, waiting for {contract_label} {struct_label}."
            elif structure_ok:
                action = "HOLD"
                reason = f"{mode_upper} Waiting: {struct_label} confirmed, only waiting for the indicator signals."
            elif structure_direction in {"BULLISH", "BEARISH"} and structure_direction != desired_direction:
                action = "HOLD"
                reason = f"{mode_upper} Waiting: {structure_direction.title()} {struct_label} ignored; waiting for indicator signals and {contract_label} {struct_label}."
            else:
                action = "HOLD"
                reason = f"{mode_upper} Waiting: waiting for indicator signals and {contract_label} {struct_label} confirmation."
        elif buy_mode in structural_modes:
            if structure_ok:
                action = expected_action
                option_intent = option_intent_open
                reason = f"{contract_label} Entry Triggered: {structure_reason}"
            elif structure_direction in {"BULLISH", "BEARISH"} and structure_direction != desired_direction:
                action = "HOLD"
                reason = f"Waiting for Entry: {structure_direction.title()} setup ignored because bot direction is {contract_label}."
            else:
                action = "HOLD"
                reason = f"{contract_label} structural entry waiting: {structure_reason or 'no setup'}"
        else:
            if indicator_ok:
                action = expected_action
                option_intent = option_intent_open
                reason = f"{contract_label} Entry Triggered: {indicator_reason}"
            else:
                action = "HOLD"
                reason = f"{contract_label} entry waiting: {indicator_reason}"

        pass

        return {
            "action": action,
            "option_intent": option_intent,
            "reason": reason,
            "bullish_count": bullish_count,
            "bearish_count": bearish_count,
            "signals": filtered_signals,
            "indicator_direction": indicator_direction,
            "structure_direction": structure_direction,
            "structure_ok": structure_ok,
            "buy_mode": buy_mode,
        }

    non_option_hybrid_modes = {"hybrid_atr", "hybrid_pct", "hybrid_pivot", "hybrid_high_low", "hybrid_swing"}
    if buy_mode in non_option_hybrid_modes:
        entry_signal = build_entry_signal(
            analysis_results.get('price_history', []),
            current_price=current_price,
            entry_mode=buy_mode,
            lookback_days=swing_entry_lookback_days,
            market_session=market_session,
            is_crypto=is_crypto,
            atr=analysis_results.get('atr', 0.0),
            custom_buy_price=custom_buy_price,
            entry_atr_mult=entry_atr_mult,
            entry_pct_move=entry_pct_move,
            entry_atr_style=entry_atr_style,
            entry_pct_style=entry_pct_style,
            swing_entry_mode=swing_entry_mode,
            desired_direction="BULLISH",
            high_low_buffer_pct=high_low_buffer_pct,
        )
        structure_direction = entry_signal.get("structure_direction", "NONE")
        structure_ok = entry_signal.get("action") == "BUY" and structure_direction == "BULLISH"
        indicator_ok = indicator_direction == "BULLISH"

        # Determine clean mode name and structure label
        mode_upper = str(buy_mode).upper()
        struct_label = 'structure'
        if 'ATR' in mode_upper: struct_label = 'ATR'
        elif 'PCT' in mode_upper: struct_label = 'Pct'
        elif 'PIVOT' in mode_upper: struct_label = 'Pivot'
        elif 'HIGH_LOW' in mode_upper or 'HL' in mode_upper: struct_label = 'H/L'
        elif 'SWING' in mode_upper: struct_label = 'Swing'

        if indicator_ok and structure_ok:
            action = "BUY"
            reason = f"{mode_upper} Triggered: {indicator_reason} AND {entry_signal.get('reason', f'bullish {struct_label} confirmed')}"
        elif indicator_direction == "BEARISH":
            action = "SELL"
            reason = f"SELL Triggered: {indicator_reason}"
        elif indicator_ok:
            action = "HOLD"
            reason = f"{mode_upper} Waiting: {indicator_reason} confirmed, waiting for bullish {struct_label}."
        elif structure_ok:
            action = "HOLD"
            reason = f"{mode_upper} Waiting: {struct_label} confirmed, only waiting for the indicator signals."
        elif structure_direction == "BEARISH":
            action = "HOLD"
            reason = f"{mode_upper} Waiting: bearish {struct_label} ignored; waiting for indicator signals and bullish {struct_label}."
        else:
            action = "HOLD"
            reason = f"{mode_upper} Waiting: waiting for indicator signals and {struct_label} confirmation."

        pass

        return {
            "action": action,
            "option_intent": option_intent,
            "reason": reason,
            "bullish_count": bullish_count,
            "bearish_count": bearish_count,
            "signals": filtered_signals,
            "indicator_direction": indicator_direction,
            "structure_direction": structure_direction,
            "structure_ok": structure_ok,
            "buy_mode": buy_mode,
        }

    # Evaluate the standalone Swing Points entry strategy here. Hybrid Swing
    # is handled by the structural hybrid branch above so its BUY decision can
    # require both the indicator threshold and bullish swing structure.
    if buy_mode == 'swing_points':
        swing_decision = detect_swing_entry(
            analysis_results.get('price_history', []),
            current_price=current_price,
            entry_mode=swing_entry_mode,
            lookback_days=swing_entry_lookback_days,
            market_session=market_session,
            is_crypto=is_crypto,
        )
        swing_action = swing_decision.get('action', 'HOLD')
        swing_reason = swing_decision.get('reason', 'Swing Points: no setup')
        action = swing_action if swing_action != 'HOLD' else 'HOLD'
        reason = swing_reason
    price_action_modes = ('custom_price', 'atr', 'hybrid_atr', 'percentage', 'hybrid_pct', 'pivot_points', 'hybrid_pivot', 'high_low', 'hybrid_high_low')
    if action == 'HOLD' and buy_mode in price_action_modes:
        entry_signal = build_entry_signal(
            analysis_results.get('price_history', []),
            current_price=current_price,
            entry_mode=buy_mode,
            lookback_days=swing_entry_lookback_days,
            market_session=market_session,
            is_crypto=is_crypto,
            atr=analysis_results.get('atr', 0.0),
            custom_buy_price=custom_buy_price,
            entry_atr_mult=entry_atr_mult,
            entry_pct_move=entry_pct_move,
            entry_atr_style=entry_atr_style,
            entry_pct_style=entry_pct_style,
            high_low_buffer_pct=high_low_buffer_pct,
        )
        if entry_signal.get('action', 'HOLD') != 'HOLD':
            action = entry_signal['action']
            reason = entry_signal.get('reason', reason)
        elif buy_mode.startswith('hybrid_'):
            # Hybrid families fall back to indicator confluence when the price-action trigger is not active.
            if bullish_count >= min_buy and bearish_count >= min_sell:
                if bullish_count > bearish_count:
                    action = "BUY"
                    bullish_names = [k for k, v in filtered_signals.items() if v.get('signal') == 'BULLISH' and v.get('enabled')]
                    reason = f"BUY Triggered (Conflict Resolved): {bullish_count}B > {bearish_count}S. Signals: {', '.join(bullish_names)}"
                elif bearish_count > bullish_count:
                    action = "SELL"
                    bearish_names = [k for k, v in filtered_signals.items() if v.get('signal') == 'BEARISH' and v.get('enabled')]
                    reason = f"SELL Triggered (Conflict Resolved): {bearish_count}S > {bullish_count}B. Signals: {', '.join(bearish_names)}"
            elif bullish_count >= min_buy:
                action = "BUY"
                bullish_names = [k for k, v in filtered_signals.items() if v.get('signal') == 'BULLISH' and v.get('enabled')]
                reason = f"BUY Triggered: {bullish_count} bullish signals ({', '.join(bullish_names)})"
            elif bearish_count >= min_sell:
                action = "SELL"
                bearish_names = [k for k, v in filtered_signals.items() if v.get('signal') == 'BEARISH' and v.get('enabled')]
                reason = f"SELL Triggered: {bearish_count} bearish signals ({', '.join(bearish_names)})"
    
    # Evaluate Custom Price Entry Strategy
    if action == 'HOLD' and buy_mode == 'custom_price':
        # Ignore indicators for buy signals, only use price drop
        if current_price > 0 and current_price <= custom_buy_price:
            action = "BUY"
            reason = f"BUY Triggered: Price {current_price} crossed below custom target {custom_buy_price}"
        
        # If not buying, we still need to check if we should sell (e.g. if we already own it)
        if action != "BUY" and bearish_count >= min_sell:
            action = "SELL"
            bearish_names = [k for k, v in filtered_signals.items() if v.get('signal') == 'BEARISH' and v.get('enabled')]
            reason = f"SELL Triggered: {bearish_count} bearish signals ({', '.join(bearish_names)})"
    
    # Evaluate Standard Indicator Entry Strategy
    elif action == 'HOLD' and buy_mode not in ('swing_points', 'hybrid_swing', 'custom_price', 'atr', 'hybrid_atr', 'percentage', 'hybrid_pct', 'pivot_points', 'hybrid_pivot', 'high_low', 'hybrid_high_low'):
        if indicator_setup_blocked:
            action = "HOLD"
            reason = "Indicator entry waiting: no indicators selected. Select at least one indicator to enable this setup."
        elif bullish_count >= min_buy and bearish_count >= min_sell:
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
    
    # Dashboard evaluations represent a flat/entry decision.  Do not present
    # a directional trade when high-confidence news points directly the other
    # way.  Bot evaluations retain SELL exits because they may protect an open
    # position; the engine has separate stop/target protection for those exits.
    news_pct = sentiment_percentage(ai_sentiment_score)
    news_dir = sentiment_direction(ai_sentiment_score, ai_sentiment_confidence, uc)
    news_alignment = "ALIGNED"
    news_alignment_reason = "News is neutral or agrees with the composite decision."
    raw_action = action
    alignment_mode = str(getattr(uc, "AI_NEWS_ALIGNMENT_MODE", "veto_conflicting_entries") or "off").lower()
    if (
        not use_bot_settings
        and alignment_mode in {"veto_conflicting_entries", "veto", "guard"}
        and ((action == "SELL" and news_dir == "BULLISH")
             or (action == "BUY" and news_dir == "BEARISH"))
    ):
        action = "HOLD"
        reason = (
            f"HOLD: composite {raw_action} conflicts with {news_pct:.0f}% "
            f"{news_dir.lower()} news sentiment; waiting for confirmation."
        )
        news_alignment = "CONFLICT_VETO"
        news_alignment_reason = reason

    pass

    return {
        "action": action,
        "option_intent": option_intent,
        "reason": reason,
        "bullish_count": bullish_count,
        "bearish_count": bearish_count,
        "signals": filtered_signals,
        "news_sentiment_pct": round(news_pct, 1),
        "news_sentiment_direction": news_dir,
        "news_alignment": news_alignment,
        "news_alignment_applied": news_alignment == "CONFLICT_VETO",
        "news_alignment_reason": news_alignment_reason,
        "model_action": raw_action,
        "indicator_direction": indicator_direction,
        "structure_direction": "NONE",
    }


def evaluate_trade(ticker: str, account_equity: float = 100000.0, available_cash: float = None, timeframe: str = None, data_source: str = "webull", use_bot_settings: bool = False, ignore_cache: bool = False, has_position: bool = False):
    """
    Full evaluation pipeline for a single ticker.
    Combines technical signals + AI sentiment + risk management.
    """
    uc = get_user_config()
    ticker_settings = getattr(uc, 'TICKER_SETTINGS', {}).get(ticker, {})
    if timeframe is None:
        timeframe = ticker_settings.get('timeframe', uc.DEFAULT_TIMEFRAME)

    # --- Check Evaluation Cache ---
    ticker_key = ticker.upper()
    timeframe_key = timeframe.upper()
    cache_key = _evaluation_cache_key(
        uc,
        ticker_key,
        timeframe_key,
        data_source,
        use_bot_settings,
        account_equity,
        available_cash,
        has_position,
    )
    
    if not ignore_cache:
        ttl = 2.0 if use_bot_settings else EVAL_CACHE_TTL
        cached_entry = _evaluation_cache.get(cache_key, ttl)
        if cached_entry is not None:
            return cached_entry['data']

    start_ts = time.perf_counter()
    analysis = None
    ai_data = {"score": 0.0, "confidence": 0.0}

    import contextvars

    def _compute():
        from user_config import active_ticker_context
        token = active_ticker_context.set(ticker_key) if use_bot_settings else None
        try:
            tech_ctx = contextvars.copy_context()
            analysis_settings = _analysis_settings_for_trade(uc, ticker_settings, use_bot_settings)
            tech_future = _calculation_executor.submit(
                tech_ctx.run,
                get_full_analysis,
                ticker_key,
                timeframe=timeframe,
                data_source=data_source,
                settings=analysis_settings,
            )

            # Determine if AI sentiment is enabled
            allowed_indicators = ticker_settings.get('indicators') if use_bot_settings else None
            ai_sentiment_enabled_for_bot = True
            if allowed_indicators is not None:
                ai_sentiment_enabled_for_bot = 'Botbulls4 News AI' in allowed_indicators

            ai_enabled = ai_sentiment_enabled_for_bot and getattr(uc, 'ENABLE_AI_SENTIMENT', True)

            if ai_enabled:
                ai_ctx = contextvars.copy_context()
                ai_future = _calculation_executor.submit(ai_ctx.run, get_ai_sentiment, ticker_key)
                return tech_future.result(), ai_future.result()
            else:
                dummy_ai_data = {
                    "score": 0.0,
                    "confidence": 0.0,
                    "summary": "AI News Sentiment disabled.",
                    "key_factor": "N/A",
                    "headline_count": 0,
                    "headlines": [],
                    "articles": [],
                    "analysis_model": "disabled"
                }
                return tech_future.result(), dummy_ai_data
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
            "rsi": None,
            "_internal_rsi": None,
            "position_sizing": {},
            "risk_status": {},
            "is_custom": False,
            "structure_ok": False,
            "buy_mode": "",
            "sell_mode": "",
            "evaluation_status": "waiting",
            "candle_meta": {},
        }
        print(f"[perf] evaluate_trade {ticker_key} {timeframe_key}: {(time.perf_counter() - start_ts) * 1000:.1f}ms (no analysis)")
        return out

    decision = get_confluence_decision(
        ticker,
        analysis, 
        ai_data['score'], 
        ai_data['confidence'],
        use_bot_settings=use_bot_settings,
        options_type=ticker_settings.get('options_type', 'calls_only') if ticker_settings else 'calls_only',
        has_position=has_position
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

    # 6. Respect the live global halt without recalculating account risk for
    # every indicator evaluation. The UserEngine performs that account-level
    # check once per live bots loop. Dashboard/backtest analysis must never
    # mutate or inherit the live bot execution gate.
    is_live_bot_context = active_evaluation_context.get() == "bots"
    can_trade = not risk_mgr.trading_halted if is_live_bot_context else True
    # A global halt blocks exposure-increasing entries but must never suppress
    # an existing position's SELL/protective exit.
    entry_action = action == "BUY" or (action == "SELL" and not has_position)
    if not can_trade and entry_action:
        action = "HOLD"
        reason = f"Trading halted: {risk_mgr.halt_reason}"

    result = {
        "time": get_now().isoformat(),
        "action": action,
        "option_intent": decision.get("option_intent", "HOLD"),
        "ticker": ticker,
        "price": f"${analysis['price']:.4f}" if analysis['price'] < 10 else f"${analysis['price']:.2f}",
        "price_raw": analysis['price'],
        "reason": reason,
        "sentiment_score": ai_data['score'],
        "sentiment_confidence": ai_data['confidence'],
        "sentiment_summary": ai_data.get('summary', ai_data.get('sentiment_summary', '')),
        "sentiment_key_factor": ai_data.get('key_factor', ''),
        "news_sentiment_pct": decision.get("news_sentiment_pct", round(sentiment_percentage(ai_data['score']), 1)),
        "news_sentiment_direction": decision.get("news_sentiment_direction", sentiment_direction(ai_data['score'], ai_data['confidence'], uc)),
        "signals": decision['signals'],
        "bullish_count": decision['bullish_count'],
        "bearish_count": decision['bearish_count'],
        "indicator_direction": decision.get("indicator_direction", "NONE"),
        "structure_direction": decision.get("structure_direction", "NONE"),
        "structure_ok": decision.get("structure_ok", False),
        "buy_mode": decision.get("buy_mode", ""),
        "sell_mode": ticker_settings.get("sell_mode", "atr") if ticker_settings else "",
        "total_signals": len(analysis['signals']),
        "atr": analysis['atr'],
        "rsi": analysis.get('rsi'),
        "_internal_rsi": analysis.get('_internal_rsi'),
        "vwap": analysis.get('vwap'),
        "position_sizing": position_sizing,
        "price_history": analysis.get('price_history', []),
        "risk_status": risk_mgr.get_risk_status(account_equity, ticker),
        "is_custom": (ticker.upper() in getattr(uc, 'TICKER_AMOUNTS', {})),
        "timeframe": timeframe,
        "evaluation_status": "valid",
        "candle_meta": analysis.get("candle_meta", {}),
    }

    # Save to cache if not ignored
    if not ignore_cache:
        _evaluation_cache.set(cache_key, {"data": result})
    print(f"[perf] evaluate_trade {ticker_key} {timeframe_key}: {(time.perf_counter() - start_ts) * 1000:.1f}ms")
    return result


def get_risk_manager():
    """Exposes the risk manager instance."""
    return risk_mgr
