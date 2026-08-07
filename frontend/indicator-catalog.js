(function () {
    'use strict';

    // Standard/classical indicators only. The formulas and guide copy are
    // independently implemented; premium BotBulls indicators remain separate.
    const catalog = [
        { name: 'RSI', label: 'RSI', category: 'Momentum' },
        { name: 'MACD', label: 'MACD', category: 'Trend' },
        { name: 'EMA Cross', label: 'EMA Cross', category: 'Moving average' },
        { name: 'ADX Trend', label: 'ADX Trend', category: 'Trend strength' },
        { name: 'Supertrend', label: 'Supertrend', category: 'Trend' },
        { name: 'Bollinger', label: 'Bollinger Bands', category: 'Volatility' },
        { name: 'VWAP', label: 'AVWAP', category: 'Volume / price' },
        { name: 'Volume Profile', label: 'Volume Profile', category: 'Volume / structure' },
        { name: 'SMA', label: 'SMA', category: 'Trend filter' },
        { name: 'Prior Day High/Low', label: 'Prior Day High/Low', category: 'Price structure' },
        { name: 'Pivot Points (S/R)', label: 'Pivot Points (S/R)', category: 'Price structure' },
        { name: 'Swing Points', label: 'Swing Points', category: 'Price structure' },
        { name: 'Mystic Pulse', label: 'Mystic Pulse', category: 'Custom momentum' },
        { name: 'Candle Patterns', label: 'Candle Patterns', category: 'Price action' },
        { name: 'ATR Volatility', label: 'ATR Volatility', category: 'Volatility' },
        { name: 'Stochastic', label: 'Stochastic', category: 'Momentum' },
        { name: 'Stochastic RSI', label: 'Stochastic RSI', category: 'Momentum' },
        { name: 'CCI', label: 'CCI', category: 'Momentum' },
        { name: 'Williams %R', label: 'Williams %R', category: 'Momentum' },
        { name: 'Rate of Change', label: 'Rate of Change', category: 'Momentum' },
        { name: 'Momentum', label: 'Momentum', category: 'Momentum' },
        { name: 'Money Flow Index', label: 'Money Flow Index', category: 'Volume / momentum' },
        { name: 'Ultimate Oscillator', label: 'Ultimate Oscillator', category: 'Momentum' },
        { name: 'Awesome Oscillator', label: 'Awesome Oscillator', category: 'Momentum' },
        { name: 'Chande Momentum Oscillator', label: 'Chande Momentum Oscillator', category: 'Momentum' },
        { name: 'True Strength Index', label: 'True Strength Index', category: 'Momentum' },
        { name: 'TRIX', label: 'TRIX', category: 'Momentum / trend' },
        { name: 'Know Sure Thing', label: 'Know Sure Thing', category: 'Momentum / trend' },
        { name: 'Percentage Price Oscillator', label: 'Percentage Price Oscillator', category: 'Momentum / trend' },
        { name: 'Aroon', label: 'Aroon', category: 'Trend' },
        { name: 'Vortex Indicator', label: 'Vortex Indicator', category: 'Trend' },
        { name: 'Ichimoku Cloud', label: 'Ichimoku Cloud', category: 'Trend / structure' },
        { name: 'Parabolic SAR', label: 'Parabolic SAR', category: 'Trend / trailing stop' },
        { name: 'Donchian Channels', label: 'Donchian Channels', category: 'Volatility / breakout' },
        { name: 'Keltner Channels', label: 'Keltner Channels', category: 'Volatility / trend' },
        { name: 'Choppiness Index', label: 'Choppiness Index', category: 'Market regime' },
        { name: 'DEMA', label: 'DEMA', category: 'Moving average' },
        { name: 'TEMA', label: 'TEMA', category: 'Moving average' },
        { name: 'Hull Moving Average', label: 'Hull Moving Average', category: 'Moving average' },
        { name: 'Weighted Moving Average', label: 'Weighted Moving Average', category: 'Moving average' },
        { name: 'VWMA', label: 'VWMA', category: 'Volume / moving average' },
        { name: 'Arnaud Legoux Moving Average', label: 'Arnaud Legoux Moving Average', category: 'Moving average' },
        { name: 'Least Squares Moving Average', label: 'Least Squares Moving Average', category: 'Moving average' },
        { name: 'On-Balance Volume', label: 'On-Balance Volume', category: 'Volume' },
        { name: 'Chaikin Money Flow', label: 'Chaikin Money Flow', category: 'Volume / flow' },
        { name: 'Accumulation/Distribution', label: 'Accumulation/Distribution', category: 'Volume / flow' },
        { name: 'Chaikin Oscillator', label: 'Chaikin Oscillator', category: 'Volume / momentum' },
        { name: 'Price Volume Trend', label: 'Price Volume Trend', category: 'Volume / trend' },
        { name: 'Ease of Movement', label: 'Ease of Movement', category: 'Volume / price' },
        { name: 'Force Index', label: 'Force Index', category: 'Volume / momentum' },
    ];

    const configMap = {
        'RSI': ['RSI_PERIOD', 'RSI_OVERBOUGHT', 'RSI_OVERSOLD'],
        'MACD': ['MACD_FAST', 'MACD_SLOW', 'MACD_SIGNAL'],
        'EMA Cross': ['EMA_FAST', 'EMA_SLOW'],
        'ADX Trend': ['ADX_PERIOD', 'ADX_TRENDING_THRESHOLD'],
        'Supertrend': ['SUPERTREND_PERIOD', 'SUPERTREND_MULTIPLIER'],
        'Bollinger': ['BOLL_PERIOD', 'BOLL_STD_DEV'],
        'VWAP': [],
        'Volume Profile': ['VOLUME_PROFILE_ROWS', 'VOLUME_PROFILE_VALUE_AREA_PCT', 'VOLUME_PROFILE_TOLERANCE_PCT', 'VOLUME_PROFILE_CONFIRMATION_CANDLES'],
        'SMA': ['SMA_PERIOD'],
        'Prior Day High/Low': ['DAILY_HIGH_LOW_BUFFER_PCT'],
        'Pivot Points (S/R)': ['PIVOT_LEVEL_COUNT', 'PIVOT_TOLERANCE_PCT'],
        'Swing Points': ['SWING_POINTS_LOOKBACK_DAYS', 'SWING_POINTS_ENTRY_MODE', 'SWING_POINTS_LEVEL_COUNT'],
        'Mystic Pulse': ['MYSTIC_PULSE_THRESHOLD'],
        'Candle Patterns': [],
        'ATR Volatility': ['ATR_PERIOD', 'ATR_BASELINE_PERIOD', 'ATR_RANGE_MULTIPLIER', 'ATR_EXPANSION_THRESHOLD_PCT'],
        'Stochastic': ['STOCH_PERIOD', 'STOCH_SMOOTH_K', 'STOCH_SMOOTH_D', 'STOCH_OVERBOUGHT', 'STOCH_OVERSOLD'],
        'Stochastic RSI': ['STOCH_RSI_PERIOD', 'STOCH_RSI_SMOOTH_K', 'STOCH_RSI_SMOOTH_D', 'STOCH_RSI_OVERBOUGHT', 'STOCH_RSI_OVERSOLD'],
        'CCI': ['CCI_PERIOD', 'CCI_THRESHOLD'],
        'Williams %R': ['WILLIAMS_R_PERIOD', 'WILLIAMS_R_OVERBOUGHT', 'WILLIAMS_R_OVERSOLD'],
        'Rate of Change': ['ROC_PERIOD'],
        'Momentum': ['MOMENTUM_PERIOD'],
        'Money Flow Index': ['MFI_PERIOD', 'MFI_OVERBOUGHT', 'MFI_OVERSOLD'],
        'Ultimate Oscillator': ['UO_SHORT', 'UO_MEDIUM', 'UO_LONG', 'UO_OVERBOUGHT', 'UO_OVERSOLD'],
        'Awesome Oscillator': ['AO_FAST', 'AO_SLOW'],
        'Chande Momentum Oscillator': ['CMO_PERIOD', 'CMO_THRESHOLD'],
        'True Strength Index': ['TSI_SLOW', 'TSI_FAST', 'TSI_SIGNAL'],
        'TRIX': ['TRIX_PERIOD', 'TRIX_SIGNAL'],
        'Know Sure Thing': ['KST_ROC1', 'KST_ROC2', 'KST_ROC3', 'KST_ROC4', 'KST_SMA1', 'KST_SMA2', 'KST_SMA3', 'KST_SMA4', 'KST_SIGNAL'],
        'Percentage Price Oscillator': ['PPO_FAST', 'PPO_SLOW', 'PPO_SIGNAL'],
        'Aroon': ['AROON_PERIOD', 'AROON_THRESHOLD'],
        'Vortex Indicator': ['VORTEX_PERIOD'],
        'Ichimoku Cloud': ['ICHIMOKU_CONVERSION', 'ICHIMOKU_BASE', 'ICHIMOKU_SPAN_B'],
        'Parabolic SAR': ['PSAR_STEP', 'PSAR_MAX_STEP'],
        'Donchian Channels': ['DONCHIAN_PERIOD'],
        'Keltner Channels': ['KELTNER_PERIOD', 'KELTNER_ATR_PERIOD', 'KELTNER_MULTIPLIER'],
        'Choppiness Index': ['CHOP_PERIOD', 'CHOP_TREND_THRESHOLD', 'CHOP_RANGE_THRESHOLD'],
        'DEMA': ['DEMA_PERIOD'],
        'TEMA': ['TEMA_PERIOD'],
        'Hull Moving Average': ['HMA_PERIOD'],
        'Weighted Moving Average': ['WMA_PERIOD'],
        'VWMA': ['VWMA_PERIOD'],
        'Arnaud Legoux Moving Average': ['ALMA_PERIOD', 'ALMA_OFFSET', 'ALMA_SIGMA'],
        'Least Squares Moving Average': ['LSMA_PERIOD', 'LSMA_OFFSET'],
        'On-Balance Volume': ['OBV_SIGNAL_PERIOD'],
        'Chaikin Money Flow': ['CMF_PERIOD'],
        'Accumulation/Distribution': ['ADL_SIGNAL_PERIOD'],
        'Chaikin Oscillator': ['CHAIKIN_FAST', 'CHAIKIN_SLOW'],
        'Price Volume Trend': ['PVT_SIGNAL_PERIOD'],
        'Ease of Movement': ['EOM_PERIOD'],
        'Force Index': ['FORCE_INDEX_PERIOD'],
    };

    const defaults = {
        RSI_PERIOD: 14, RSI_OVERBOUGHT: 70, RSI_OVERSOLD: 30,
        MACD_FAST: 12, MACD_SLOW: 26, MACD_SIGNAL: 9,
        EMA_FAST: 9, EMA_SLOW: 21,
        ADX_PERIOD: 14, ADX_TRENDING_THRESHOLD: 25,
        SUPERTREND_PERIOD: 10, SUPERTREND_MULTIPLIER: 3,
        BOLL_PERIOD: 20, BOLL_STD_DEV: 2,
        VOLUME_PROFILE_ROWS: 48, VOLUME_PROFILE_VALUE_AREA_PCT: 70,
        VOLUME_PROFILE_TOLERANCE_PCT: 0.15, VOLUME_PROFILE_CONFIRMATION_CANDLES: 1,
        SMA_PERIOD: 200,
        DAILY_HIGH_LOW_BUFFER_PCT: 0,
        PIVOT_LEVEL_COUNT: 1, PIVOT_TOLERANCE_PCT: 0,
        SWING_POINTS_LOOKBACK_DAYS: 20, SWING_POINTS_ENTRY_MODE: 'bounce_retest',
        SWING_POINTS_LEVEL_COUNT: 1,
        MYSTIC_PULSE_THRESHOLD: 5,
        ATR_PERIOD: 14, ATR_BASELINE_PERIOD: 20, ATR_RANGE_MULTIPLIER: 1,
        ATR_EXPANSION_THRESHOLD_PCT: 5,
        ATR_STOP_MULTIPLIER: 2, ATR_TRAIL_MULTIPLIER: 3, ATR_TAKE_PROFIT_MULTIPLIER: 4,
        STOCH_PERIOD: 14, STOCH_SMOOTH_K: 3, STOCH_SMOOTH_D: 3, STOCH_OVERBOUGHT: 80, STOCH_OVERSOLD: 20,
        STOCH_RSI_PERIOD: 14, STOCH_RSI_SMOOTH_K: 3, STOCH_RSI_SMOOTH_D: 3, STOCH_RSI_OVERBOUGHT: 80, STOCH_RSI_OVERSOLD: 20,
        CCI_PERIOD: 20, CCI_THRESHOLD: 100,
        WILLIAMS_R_PERIOD: 14, WILLIAMS_R_OVERBOUGHT: -20, WILLIAMS_R_OVERSOLD: -80,
        ROC_PERIOD: 12, MOMENTUM_PERIOD: 10,
        MFI_PERIOD: 14, MFI_OVERBOUGHT: 80, MFI_OVERSOLD: 20,
        UO_SHORT: 7, UO_MEDIUM: 14, UO_LONG: 28, UO_OVERBOUGHT: 70, UO_OVERSOLD: 30,
        AO_FAST: 5, AO_SLOW: 34,
        CMO_PERIOD: 14, CMO_THRESHOLD: 50,
        TSI_SLOW: 25, TSI_FAST: 13, TSI_SIGNAL: 13,
        TRIX_PERIOD: 15, TRIX_SIGNAL: 9,
        KST_ROC1: 10, KST_ROC2: 15, KST_ROC3: 20, KST_ROC4: 30,
        KST_SMA1: 10, KST_SMA2: 10, KST_SMA3: 10, KST_SMA4: 15, KST_SIGNAL: 9,
        PPO_FAST: 12, PPO_SLOW: 26, PPO_SIGNAL: 9,
        AROON_PERIOD: 25, AROON_THRESHOLD: 70,
        VORTEX_PERIOD: 14,
        ICHIMOKU_CONVERSION: 9, ICHIMOKU_BASE: 26, ICHIMOKU_SPAN_B: 52,
        PSAR_STEP: 0.02, PSAR_MAX_STEP: 0.2,
        DONCHIAN_PERIOD: 20,
        KELTNER_PERIOD: 20, KELTNER_ATR_PERIOD: 10, KELTNER_MULTIPLIER: 2,
        CHOP_PERIOD: 14, CHOP_TREND_THRESHOLD: 38.2, CHOP_RANGE_THRESHOLD: 61.8,
        DEMA_PERIOD: 21, TEMA_PERIOD: 21, HMA_PERIOD: 9, WMA_PERIOD: 20, VWMA_PERIOD: 20,
        ALMA_PERIOD: 9, ALMA_OFFSET: 0.85, ALMA_SIGMA: 6,
        LSMA_PERIOD: 25, LSMA_OFFSET: 0,
        OBV_SIGNAL_PERIOD: 20, CMF_PERIOD: 20, ADL_SIGNAL_PERIOD: 20,
        CHAIKIN_FAST: 3, CHAIKIN_SLOW: 10,
        PVT_SIGNAL_PERIOD: 20, EOM_PERIOD: 14, FORCE_INDEX_PERIOD: 13,
    };

    const labels = {
        RSI_PERIOD: 'RSI Period', RSI_OVERBOUGHT: 'Overbought Level', RSI_OVERSOLD: 'Oversold Level',
        MACD_FAST: 'Fast EMA', MACD_SLOW: 'Slow EMA', MACD_SIGNAL: 'Signal EMA',
        EMA_FAST: 'Fast EMA', EMA_SLOW: 'Slow EMA',
        ADX_PERIOD: 'ADX / DI Period', ADX_TRENDING_THRESHOLD: 'Trend Strength Threshold',
        SUPERTREND_PERIOD: 'ATR Period', SUPERTREND_MULTIPLIER: 'ATR Multiplier',
        BOLL_PERIOD: 'Middle-Band Period', BOLL_STD_DEV: 'Standard-Deviation Multiplier',
        VOLUME_PROFILE_ROWS: 'Price Rows', VOLUME_PROFILE_VALUE_AREA_PCT: 'Value Area %',
        VOLUME_PROFILE_TOLERANCE_PCT: 'Level Tolerance %',
        VOLUME_PROFILE_CONFIRMATION_CANDLES: 'Acceptance Candles',
        SMA_PERIOD: 'SMA Period',
        DAILY_HIGH_LOW_BUFFER_PCT: 'Breakout Buffer %',
        PIVOT_LEVEL_COUNT: 'Visible S/R Levels', PIVOT_TOLERANCE_PCT: 'Level Tolerance %',
        SWING_POINTS_LOOKBACK_DAYS: 'Structure Lookback Days',
        SWING_POINTS_ENTRY_MODE: 'Entry Confirmation Mode',
        SWING_POINTS_LEVEL_COUNT: 'Visible Structure Levels',
        MYSTIC_PULSE_THRESHOLD: 'Directional Score Threshold',
        ATR_PERIOD: 'ATR Period',
        ATR_BASELINE_PERIOD: 'Volatility Baseline Period',
        ATR_RANGE_MULTIPLIER: 'Expected Range Width (ATR)',
        ATR_EXPANSION_THRESHOLD_PCT: 'Expansion Threshold %',
        ATR_STOP_MULTIPLIER: 'Initial Stop (ATR Multiple)',
        ATR_TRAIL_MULTIPLIER: 'Trailing Stop (ATR Multiple)',
        ATR_TAKE_PROFIT_MULTIPLIER: 'Take Profit (ATR Multiple)',
        STOCH_PERIOD: 'Stochastic Period', STOCH_SMOOTH_K: '%K Smoothing', STOCH_SMOOTH_D: '%D Smoothing',
        STOCH_OVERBOUGHT: 'Overbought Level', STOCH_OVERSOLD: 'Oversold Level',
        STOCH_RSI_PERIOD: 'RSI / Stochastic Period', STOCH_RSI_SMOOTH_K: '%K Smoothing', STOCH_RSI_SMOOTH_D: '%D Smoothing',
        STOCH_RSI_OVERBOUGHT: 'Overbought Level', STOCH_RSI_OVERSOLD: 'Oversold Level',
        CCI_PERIOD: 'CCI Period', CCI_THRESHOLD: 'Directional Threshold',
        WILLIAMS_R_PERIOD: 'Williams %R Period', WILLIAMS_R_OVERBOUGHT: 'Overbought Level', WILLIAMS_R_OVERSOLD: 'Oversold Level',
        ROC_PERIOD: 'Rate-of-Change Period', MOMENTUM_PERIOD: 'Momentum Period',
        MFI_PERIOD: 'MFI Period', MFI_OVERBOUGHT: 'Overbought Level', MFI_OVERSOLD: 'Oversold Level',
        UO_SHORT: 'Short Period', UO_MEDIUM: 'Medium Period', UO_LONG: 'Long Period',
        UO_OVERBOUGHT: 'Bullish Threshold', UO_OVERSOLD: 'Bearish Threshold',
        AO_FAST: 'Fast Median-Price Period', AO_SLOW: 'Slow Median-Price Period',
        CMO_PERIOD: 'CMO Period', CMO_THRESHOLD: 'Directional Threshold',
        TSI_SLOW: 'Slow Smoothing', TSI_FAST: 'Fast Smoothing', TSI_SIGNAL: 'Signal Period',
        TRIX_PERIOD: 'TRIX Period', TRIX_SIGNAL: 'Signal Period',
        KST_ROC1: 'ROC 1 Period', KST_ROC2: 'ROC 2 Period', KST_ROC3: 'ROC 3 Period', KST_ROC4: 'ROC 4 Period',
        KST_SMA1: 'ROC 1 Smoothing', KST_SMA2: 'ROC 2 Smoothing', KST_SMA3: 'ROC 3 Smoothing', KST_SMA4: 'ROC 4 Smoothing',
        KST_SIGNAL: 'Signal Period',
        PPO_FAST: 'Fast EMA', PPO_SLOW: 'Slow EMA', PPO_SIGNAL: 'Signal EMA',
        AROON_PERIOD: 'Aroon Period', AROON_THRESHOLD: 'Trend Threshold',
        VORTEX_PERIOD: 'Vortex Period',
        ICHIMOKU_CONVERSION: 'Conversion Period', ICHIMOKU_BASE: 'Base Period', ICHIMOKU_SPAN_B: 'Leading Span B Period',
        PSAR_STEP: 'Acceleration Step', PSAR_MAX_STEP: 'Maximum Acceleration',
        DONCHIAN_PERIOD: 'Channel Period',
        KELTNER_PERIOD: 'EMA Period', KELTNER_ATR_PERIOD: 'ATR Period', KELTNER_MULTIPLIER: 'ATR Multiplier',
        CHOP_PERIOD: 'Choppiness Period', CHOP_TREND_THRESHOLD: 'Trending Below', CHOP_RANGE_THRESHOLD: 'Ranging Above',
        DEMA_PERIOD: 'DEMA Period', TEMA_PERIOD: 'TEMA Period', HMA_PERIOD: 'HMA Period',
        WMA_PERIOD: 'WMA Period', VWMA_PERIOD: 'VWMA Period',
        ALMA_PERIOD: 'ALMA Period', ALMA_OFFSET: 'Gaussian Offset', ALMA_SIGMA: 'Gaussian Sigma',
        LSMA_PERIOD: 'Regression Period', LSMA_OFFSET: 'Regression Offset',
        OBV_SIGNAL_PERIOD: 'OBV Signal EMA', CMF_PERIOD: 'CMF Period', ADL_SIGNAL_PERIOD: 'A/D Signal EMA',
        CHAIKIN_FAST: 'Fast EMA', CHAIKIN_SLOW: 'Slow EMA',
        PVT_SIGNAL_PERIOD: 'PVT Signal EMA', EOM_PERIOD: 'EOM Smoothing', FORCE_INDEX_PERIOD: 'Force Index EMA',
    };

    const decimalKeys = new Set([
        'SUPERTREND_MULTIPLIER', 'BOLL_STD_DEV', 'VOLUME_PROFILE_VALUE_AREA_PCT',
        'VOLUME_PROFILE_TOLERANCE_PCT', 'DAILY_HIGH_LOW_BUFFER_PCT', 'PIVOT_TOLERANCE_PCT',
        'ATR_STOP_MULTIPLIER', 'ATR_TRAIL_MULTIPLIER', 'ATR_TAKE_PROFIT_MULTIPLIER',
        'ATR_RANGE_MULTIPLIER', 'ATR_EXPANSION_THRESHOLD_PCT',
        'PSAR_STEP', 'PSAR_MAX_STEP', 'KELTNER_MULTIPLIER',
        'CHOP_TREND_THRESHOLD', 'CHOP_RANGE_THRESHOLD', 'ALMA_OFFSET', 'ALMA_SIGMA',
    ]);
    const inputAttrs = Object.fromEntries(Object.keys(defaults).map(key => [
        key,
        decimalKeys.has(key) ? 'step="0.01"' : 'step="1" min="1"',
    ]));
    inputAttrs.WILLIAMS_R_OVERBOUGHT = 'step="1" min="-100" max="0"';
    inputAttrs.WILLIAMS_R_OVERSOLD = 'step="1" min="-100" max="0"';
    inputAttrs.LSMA_OFFSET = 'step="1" min="-100" max="100"';
    inputAttrs.ALMA_OFFSET = 'step="0.01" min="0" max="1"';
    inputAttrs.PSAR_STEP = 'step="0.01" min="0.01" max="1"';
    inputAttrs.PSAR_MAX_STEP = 'step="0.01" min="0.01" max="1"';
    inputAttrs.ATR_PERIOD = 'step="1" min="2" max="200"';
    inputAttrs.ATR_BASELINE_PERIOD = 'step="1" min="2" max="500"';
    inputAttrs.ATR_RANGE_MULTIPLIER = 'step="0.1" min="0.1" max="10"';
    inputAttrs.ATR_EXPANSION_THRESHOLD_PCT = 'step="0.5" min="0" max="100"';
    inputAttrs.SWING_POINTS_ENTRY_MODE = '';

    const toggleKeys = {
        'RSI': 'ENABLE_RSI',
        'MACD': 'ENABLE_MACD',
        'EMA Cross': 'ENABLE_EMA',
        'ADX Trend': 'ENABLE_ADX_TREND',
        'Supertrend': 'ENABLE_SUPERTREND',
        'Bollinger': 'ENABLE_BOLLINGER',
        'VWAP': 'ENABLE_VWAP',
        'Volume Profile': 'ENABLE_VOLUME_PROFILE',
        'SMA': 'ENABLE_SMA',
        'Prior Day High/Low': 'ENABLE_DAILY_HIGH_LOW',
        'Pivot Points (S/R)': 'ENABLE_PIVOT_POINTS',
        'Swing Points': 'ENABLE_SWING_POINTS',
        'Mystic Pulse': 'ENABLE_MYSTIC_PULSE',
        'Candle Patterns': 'ENABLE_CANDLE_PATTERNS',
        'ATR Volatility': 'ENABLE_ATR_VOLATILITY',
        'Stochastic': 'ENABLE_STOCHASTIC',
        'Stochastic RSI': 'ENABLE_STOCH_RSI',
        'CCI': 'ENABLE_CCI',
        'Williams %R': 'ENABLE_WILLIAMS_R',
        'Rate of Change': 'ENABLE_ROC',
        'Momentum': 'ENABLE_MOMENTUM',
        'Money Flow Index': 'ENABLE_MFI',
        'Ultimate Oscillator': 'ENABLE_ULTIMATE_OSCILLATOR',
        'Awesome Oscillator': 'ENABLE_AWESOME_OSCILLATOR',
        'Chande Momentum Oscillator': 'ENABLE_CMO',
        'True Strength Index': 'ENABLE_TSI',
        'TRIX': 'ENABLE_TRIX',
        'Know Sure Thing': 'ENABLE_KST',
        'Percentage Price Oscillator': 'ENABLE_PPO',
        'Aroon': 'ENABLE_AROON',
        'Vortex Indicator': 'ENABLE_VORTEX',
        'Ichimoku Cloud': 'ENABLE_ICHIMOKU',
        'Parabolic SAR': 'ENABLE_PSAR',
        'Donchian Channels': 'ENABLE_DONCHIAN',
        'Keltner Channels': 'ENABLE_KELTNER',
        'Choppiness Index': 'ENABLE_CHOPPINESS',
        'DEMA': 'ENABLE_DEMA',
        'TEMA': 'ENABLE_TEMA',
        'Hull Moving Average': 'ENABLE_HMA',
        'Weighted Moving Average': 'ENABLE_WMA',
        'VWMA': 'ENABLE_VWMA',
        'Arnaud Legoux Moving Average': 'ENABLE_ALMA',
        'Least Squares Moving Average': 'ENABLE_LSMA',
        'On-Balance Volume': 'ENABLE_OBV',
        'Chaikin Money Flow': 'ENABLE_CMF',
        'Accumulation/Distribution': 'ENABLE_ADL',
        'Chaikin Oscillator': 'ENABLE_CHAIKIN_OSCILLATOR',
        'Price Volume Trend': 'ENABLE_PVT',
        'Ease of Movement': 'ENABLE_EOM',
        'Force Index': 'ENABLE_FORCE_INDEX',
    };

    const guideText = {
        'RSI': 'Measures the balance of recent gains and losses on a 0–100 scale. It is mainly an exhaustion/timing tool, not a complete trend system.',
        'MACD': 'Measures the spread between fast and slow exponential averages, plus a signal average and histogram. It helps identify momentum direction and changes in trend speed.',
        'EMA Cross': 'Compares a responsive fast EMA with a slower EMA. It is a trend-direction filter that is deliberately suppressed when ADX does not confirm a strengthening trend.',
        'ADX Trend': 'Separates trend strength (ADX) from direction (+DI versus −DI). ADX itself is not bullish or bearish; the DI lines supply direction.',
        'Supertrend': 'Builds an ATR-based trailing trend line around price. It is useful for trend state and trailing-risk context, but can flip repeatedly in a range.',
        'Bollinger': 'Places standard-deviation envelopes around a moving average. The grid uses the outer portions of the channel as mean-reversion alert zones.',
        'VWAP': 'Uses an anchored volume-weighted average price from the latest major volume event. It approximates the average price paid since that anchor.',
        'Volume Profile': 'Builds a prior-session volume-at-price distribution. POC is the highest-volume price; VAH and VAL bound the selected value area.',
        'SMA': 'A simple moving average smooths price equally across its lookback. The long default is intended as a broad regime filter.',
        'Prior Day High/Low': 'Plots the previous completed session’s extremes. These levels are commonly watched for acceptance, breakout, failure, and liquidity tests.',
        'Pivot Points (S/R)': 'Calculates classic floor-trader pivot and support/resistance levels from the previous session’s high, low, and close.',
        'Swing Points': 'Clusters recent local highs and lows into structural support and resistance. It is price-structure logic rather than a mathematical oscillator.',
        'Mystic Pulse': 'Combines the app’s internal momentum, volatility, and volume features into separate bullish and bearish strength scores.',
        'Candle Patterns': 'Classifies the latest completed OHLC formation using nearby candles. A pattern is context, not proof of a reversal by itself.',
        'ATR Volatility': 'Measures the typical movement per completed candle with Wilder’s true-range method, including gaps. ATR is not directional. The chart also plots a causal expected range: each candle’s bands use the prior close plus or minus the prior completed ATR, so the range was available before that candle opened.',
        'Stochastic': 'Compares the close with the recent high-low range. A %K/%D turn from below the oversold level supports a bullish reversal; a turn from above overbought supports a bearish reversal.',
        'Stochastic RSI': 'Applies the stochastic calculation to RSI for a faster momentum reading. Use its threshold turns as timing confirmation, especially with a separate trend filter.',
        'CCI': 'Measures price deviation from its statistical mean. The grid treats a rising oversold reading or a falling overbought reading as an exhaustion-reversal setup.',
        'Williams %R': 'Locates the close within the recent range on a -100 to 0 scale. A rising turn from oversold supports buyers; a falling turn from overbought supports sellers.',
        'Rate of Change': 'Expresses the percentage change from a prior close. Positive values indicate upward momentum and negative values indicate downward momentum.',
        'Momentum': 'Measures the point change from a prior close. The grid compares the current reading with the previous reading to identify improving or weakening momentum.',
        'Money Flow Index': 'Combines price and volume into a bounded momentum oscillator. Turns from oversold or overbought are strongest when price structure confirms them.',
        'Ultimate Oscillator': 'Blends short, medium, and long buying-pressure windows to reduce single-period distortion. High readings favor bullish pressure and low readings favor bearish pressure.',
        'Awesome Oscillator': 'Compares fast and slow averages of median price. Above zero favors bullish momentum; below zero favors bearish momentum.',
        'Chande Momentum Oscillator': 'Compares total gains with total losses on a -100 to +100 scale. Readings beyond the positive or negative threshold identify strong directional momentum.',
        'True Strength Index': 'Double-smooths price change to reduce noise. The TSI line crossing above its signal favors bulls; crossing below favors bears.',
        'TRIX': 'Tracks the rate of change of a triple-smoothed EMA. Its signal-line relationship identifies sustained momentum while filtering short-term noise.',
        'Know Sure Thing': 'Combines four smoothed rate-of-change horizons. KST above its signal supports broad bullish momentum; below supports bearish momentum.',
        'Percentage Price Oscillator': 'Shows the percentage spread between fast and slow EMAs. A positive histogram supports bullish momentum and a negative histogram supports bearish momentum.',
        'Aroon': 'Measures how recently the highest high and lowest low occurred. A strong Aroon Up over Aroon Down favors an uptrend; the reverse favors a downtrend.',
        'Vortex Indicator': 'Compares positive and negative directional movement over a rolling window. VI+ above VI- favors bulls; VI- above VI+ favors bears.',
        'Ichimoku Cloud': 'Combines equilibrium, trend, and support/resistance. Conversion and base lines are current; leading spans are displaced forward on the chart by the base period.',
        'Parabolic SAR': 'Places a trailing reversal level around price. Price above the SAR supports an uptrend; price below supports a downtrend. It can whipsaw in ranges.',
        'Donchian Channels': 'Tracks prior rolling highs and lows. A close beyond the previous upper channel is a bullish breakout; below the previous lower channel is bearish.',
        'Keltner Channels': 'Builds ATR-based envelopes around an EMA. Price relative to the centerline identifies direction while the bands provide volatility context.',
        'Choppiness Index': 'Estimates whether the market is ranging or trending. High readings suppress directional signals; low readings allow the prevailing price trend to count.',
        'DEMA': 'A double exponential moving average designed to reduce EMA lag. Price above the line favors bulls; price below favors bears.',
        'TEMA': 'A triple exponential moving average designed to reduce lag while smoothing price. Price above favors bulls; below favors bears.',
        'Hull Moving Average': 'Uses weighted averages to improve smoothness and responsiveness. Price above the HMA favors an uptrend; below favors a downtrend.',
        'Weighted Moving Average': 'Weights recent closes more heavily than older closes. Price above the WMA supports bullish trend direction; below supports bearish direction.',
        'VWMA': 'Weights closing prices by volume. Price above the VWMA indicates trading above the volume-weighted trend; below indicates the opposite.',
        'Arnaud Legoux Moving Average': 'Uses a shifted Gaussian weighting curve to balance smoothness and responsiveness. Price relative to ALMA provides a trend filter.',
        'Least Squares Moving Average': 'Fits a rolling linear regression and projects its endpoint. Price above the regression estimate favors bulls; below favors bears.',
        'On-Balance Volume': 'Adds volume on up closes and subtracts it on down closes. OBV above its signal EMA supports accumulation; below supports distribution.',
        'Chaikin Money Flow': 'Aggregates close location within each bar weighted by volume. Positive values indicate accumulation pressure; negative values indicate distribution.',
        'Accumulation/Distribution': 'Builds a cumulative volume-flow line from each close location in its range. Above its signal EMA supports accumulation; below supports distribution.',
        'Chaikin Oscillator': 'Measures the difference between fast and slow EMAs of the accumulation/distribution line. Positive values favor accumulation; negative values favor distribution.',
        'Price Volume Trend': 'Cumulates volume adjusted by percentage price change. PVT above its signal EMA supports a bullish volume trend; below supports a bearish one.',
        'Ease of Movement': 'Relates price-range movement to volume. Positive smoothed readings favor upward movement with relative ease; negative readings favor downward movement.',
        'Force Index': 'Combines price change and volume, then smooths the result. Positive force favors buyers; negative force favors sellers.',
    };
    const signalGuide = {
        'RSI': 'Bullish only while RSI is below Oversold and rising; bearish only while above Overbought and falling; otherwise neutral.',
        'MACD': 'Bullish when the MACD histogram is above zero, bearish below zero, and neutral at zero.',
        'EMA Cross': 'When ADX is above its threshold and rising: fast EMA above slow is bullish and below is bearish. Otherwise the vote is neutral.',
        'ADX Trend': 'ADX must be above its threshold and rising. Then +DI above −DI is bullish and −DI above +DI is bearish; otherwise neutral.',
        'Supertrend': 'Bullish while the completed candle is in the upper trend state; bearish while in the lower trend state.',
        'Bollinger': 'Bullish in the lowest 20% of the band width, bearish in the highest 20%, and neutral in the middle 60%.',
        'VWAP': 'Bullish on an AVWAP reclaim or a bullish candle bouncing near it; bearish below AVWAP; otherwise neutral while holding above.',
        'Volume Profile': 'Bullish on acceptance above VAH, a POC reclaim, or VAL bounce. Bearish on acceptance below VAL, POC loss, or VAH rejection. Otherwise neutral.',
        'SMA': 'Price at or above the SMA is bullish; price below it is bearish.',
        'Prior Day High/Low': 'A fresh close through the buffered prior high is bullish; a fresh close through the buffered prior low is bearish; otherwise neutral.',
        'Pivot Points (S/R)': 'A support bounce or pivot reclaim is bullish; a resistance rejection or pivot loss is bearish; otherwise neutral.',
        'Swing Points': 'Confirmed support/reclaim structure is bullish; confirmed resistance/rejection structure is bearish; otherwise neutral.',
        'Mystic Pulse': 'Bullish or bearish when its corresponding score reaches the threshold; otherwise neutral.',
        'Candle Patterns': 'The latest completed recognized pattern supplies bullish or bearish direction; no recognized pattern is neutral.',
        'ATR Volatility': 'ATR must exceed the selected volatility baseline by the selected Expansion Threshold. Then an up close is bullish and a down close is bearish; normal volatility or a flat close is neutral. The range bands are context, not entry or stop orders.',
        'Stochastic': '%K above %D after a bullish cross or while oversold is bullish; %K below %D after a bearish cross or while overbought is bearish; otherwise neutral.',
        'Stochastic RSI': '%K above %D after a bullish cross or while oversold is bullish; the inverse after a bearish cross or while overbought is bearish; otherwise neutral.',
        'CCI': 'Below the negative threshold and rising is bullish; above the positive threshold and falling is bearish; otherwise neutral.',
        'Williams %R': 'Below Oversold and rising is bullish; above Overbought and falling is bearish; otherwise neutral.',
        'Rate of Change': 'Above zero is bullish, below zero is bearish, and zero is neutral.',
        'Momentum': 'A rising reading is bullish, a falling reading is bearish, and an unchanged reading is neutral.',
        'Money Flow Index': 'Below Oversold and rising is bullish; above Overbought and falling is bearish; otherwise neutral.',
        'Ultimate Oscillator': 'Above the upper threshold is bullish, below the lower threshold is bearish, and the middle zone is neutral.',
        'Awesome Oscillator': 'Above zero is bullish, below zero is bearish, and zero is neutral.',
        'Chande Momentum Oscillator': 'Above the positive threshold is bullish, below the negative threshold is bearish, and the middle zone is neutral.',
        'True Strength Index': 'TSI above its signal line is bullish; below is bearish; equal is neutral.',
        'TRIX': 'TRIX above its signal line is bullish; below is bearish; equal is neutral.',
        'Know Sure Thing': 'KST above its signal line is bullish; below is bearish; equal is neutral.',
        'Percentage Price Oscillator': 'A positive histogram is bullish, a negative histogram is bearish, and zero is neutral.',
        'Aroon': 'The dominant line must also reach the Trend Threshold: Aroon Up is bullish and Aroon Down is bearish; otherwise neutral.',
        'Vortex Indicator': 'VI+ above VI− is bullish; VI− above VI+ is bearish; equal is neutral.',
        'Ichimoku Cloud': 'A strict bullish stack requires Span A > Span B, Base > Span A, Conversion > Base, and Price > Conversion. The exact inverse is bearish; all mixed stacks are neutral.',
        'Parabolic SAR': 'Price above the SAR dots is bullish; price below is bearish.',
        'Donchian Channels': 'A close above the prior rolling upper channel is bullish; below the prior lower channel is bearish; inside is neutral.',
        'Keltner Channels': 'Price above the EMA centerline is bullish; below is bearish. Outer bands are volatility context, not extra votes.',
        'Choppiness Index': 'Only a reading below Trending Below can vote. Then price above the Keltner center is bullish and below is bearish; higher choppiness is neutral.',
        'DEMA': 'Price above the average is bullish; below is bearish; equal is neutral.',
        'TEMA': 'Price above the average is bullish; below is bearish; equal is neutral.',
        'Hull Moving Average': 'Price above the average is bullish; below is bearish; equal is neutral.',
        'Weighted Moving Average': 'Price above the average is bullish; below is bearish; equal is neutral.',
        'VWMA': 'Price above the average is bullish; below is bearish; equal is neutral.',
        'Arnaud Legoux Moving Average': 'Price above the average is bullish; below is bearish; equal is neutral.',
        'Least Squares Moving Average': 'Price above the regression estimate is bullish; below is bearish; equal is neutral.',
        'On-Balance Volume': 'OBV above its signal EMA is bullish; below is bearish; equal is neutral.',
        'Chaikin Money Flow': 'Above zero is bullish, below zero is bearish, and zero is neutral.',
        'Accumulation/Distribution': 'A/D above its signal EMA is bullish; below is bearish; equal is neutral.',
        'Chaikin Oscillator': 'Above zero is bullish, below zero is bearish, and zero is neutral.',
        'Price Volume Trend': 'PVT above its signal EMA is bullish; below is bearish; equal is neutral.',
        'Ease of Movement': 'Above zero is bullish, below zero is bearish, and zero is neutral.',
        'Force Index': 'Above zero is bullish, below zero is bearish, and zero is neutral.',
    };
    const subchartNames = new Set([
        'RSI', 'MACD', 'ADX Trend', 'Mystic Pulse',
        'ATR Volatility', 'Stochastic', 'Stochastic RSI', 'CCI', 'Williams %R',
        'Rate of Change', 'Momentum', 'Money Flow Index', 'Ultimate Oscillator',
        'Awesome Oscillator', 'Chande Momentum Oscillator', 'True Strength Index',
        'TRIX', 'Know Sure Thing', 'Percentage Price Oscillator', 'Aroon',
        'Vortex Indicator', 'Choppiness Index', 'On-Balance Volume',
        'Chaikin Money Flow', 'Accumulation/Distribution', 'Chaikin Oscillator',
        'Price Volume Trend', 'Ease of Movement', 'Force Index',
    ]);
    const guides = Object.fromEntries(Object.entries(guideText).map(([name, body]) => [
        name,
        `<div class="space-y-3">`
        + `<p><strong>Purpose:</strong> ${body}</p>`
        + `<p><strong>Where it appears:</strong> ${subchartNames.has(name) ? 'A separate sub-chart below price.' : (name === 'Candle Patterns' ? 'Markers on the main price chart.' : 'The main price chart.')}</p>`
        + `<p><strong>How this app votes:</strong> ${signalGuide[name]}</p>`
        + `<p class="text-xs text-slate-500"><strong>Default settings:</strong> ${(configMap[name] || []).length ? configMap[name].map(key => `${labels[key] || key}: ${defaults[key]}`).join(' · ') : 'No adjustable calculation setting for this indicator.'}</p>`
        + `<p class="text-xs text-slate-500"><strong>Beginner note:</strong> Bullish and bearish are rule-based confluence votes, not guaranteed trades. Confirm with market structure, liquidity, and risk limits; test changes on the same ticker and timeframe before deployment.</p>`
        + `</div>`,
    ]));
    guides.BotBulls5 = `
        <div class="space-y-3">
            <p><strong>Purpose:</strong> BotBulls5 is an original, rules-based chart-pattern engine. It normalizes geometry with ATR so one model can analyze stocks and crypto across timeframes without copying a proprietary indicator formula.</p>
            <p><strong>Patterns covered:</strong><br>
            • <b>Continuation:</b> bull/bear flags, pennants, symmetrical/ascending/descending triangles, rectangles, price channels, measured moves, and cup with handle.<br>
            • <b>Reversal:</b> double/triple tops and bottoms, head-and-shoulders top/bottom, rising/falling wedges, rounding bottom, broadening top, and bump-and-run reversal.<br>
            Direction-neutral structures such as symmetrical triangles, rectangles, channels, and broadening formations do not assume direction; the completed breakout decides the vote.</p>
            <p><strong>What the chart shows:</strong> BotBulls5 now draws the recognizable pattern directly through the candles. Flags, pennants, and continuation wedges include the impulse pole plus their channel boundaries. Triangles, wedges, rectangles, channels, and broadening structures draw their fitted support/resistance boundaries. Double/triple tops and bottoms and head-and-shoulders draw a purple swing outline with an orange dashed neckline. Rounding, cup-and-handle, measured-move, and bump-and-run structures trace their price path. The thick green/red segment identifies the confirmed breakout leg. The green dashed level is a measured objective—not a forecast guarantee—and the red dashed level is structural invalidation.</p>
            <p><strong>Live status in Settings:</strong> Open BotBulls5 Settings to see the current or newest confirmed pattern from the latest trading day, including state, direction, setup-quality confidence, breakout, invalidation, measured target, scored reasons, and detection time. FORMING gives advance context before a breakout; CONFIRMED means the breakout candle has closed. Stock calculations use the same Regular or Extended session selected for the chart.</p>
            <p><strong>Recent-day chart mode:</strong> Dashboard, Bot preview, and Backtest charts show BotBulls5 breakout annotations and the complete formation drawing for the latest trading day (rolling 24 hours for crypto). The drawing includes the detected swing outline or pole, fitted upper/lower boundaries, breakout leg, measured target, and invalidation where applicable. If the latest candle has no active pattern, the newest confirmed setup from that day remains drawn so the breakout decision can be inspected. Prior-day drawings clear automatically. A current pattern may still start before the day when its formation began earlier. Live Dashboard and Bot previews calculate the latest day with 160 hidden warm-up candles for accurate pivots and ATR context. Backtests continue calculating every historical candle so entries, exits, and performance are unchanged.</p>
            <p><strong>How this app votes:</strong> <span class="text-emerald-600 font-bold">BULLISH</span> requires a fresh completed-candle cross above a qualified bullish completion level. <span class="text-rose-600 font-bold">BEARISH</span> requires a fresh completed-candle cross below a qualified bearish completion level. The marker is attached to that same breakout candle after it closes, not moved to the following candle. Swing pivots must already be causally confirmed before the breakout test. A candle already outside the boundary is not relabeled as a new breakout. FORMING, failed, expired, low-quality, and unconfirmed patterns remain NEUTRAL. Invalidation also uses a completed close, preventing one wick from both confirming and cancelling a setup.</p>
            <p><strong>Confidence model:</strong> Scores swing symmetry and spacing, prior-trend context, ATR-normalized height, boundary touches and residual error, retracement, convergence/parallelism, volume contraction, and breakout clearance. Confidence is a setup-quality score—not a win rate or probability of profit.</p>
            <p><strong>Five main settings:</strong><br>
            • <b>Minimum Setup Quality (70):</b> The primary sensitivity control. Raise to 78–85 for fewer, stricter setups; values below 65 are aggressive.<br>
            • <b>Breakout Close Buffer (0.10 ATR):</b> Requires the close to clear support or resistance by an ATR fraction. Raise it to reduce marginal breaks, at the cost of later entries.<br>
            • <b>Structural Lookback (48 bars):</b> Controls the window used for slower reversal, triangle, channel, and rounded structures. Use enough bars for the selected timeframe without making it excessively broad.<br>
            • <b>Confluence Vote Duration (3 bars):</b> Number of completed bars a confirmed pattern may contribute one directional vote. It does not extend the price target or create additional breakouts.<br>
            • <b>Require Breakout Volume (Off):</b> When enabled, every pattern needs reliable volume and at least 1.20× its recent baseline at breakout. Leave off for feeds with missing or unreliable volume; available volume still affects quality when this is off.</p>
            <p><strong>Fixed internal model defaults:</strong> 12-bar impulse, 5–18-bar flag pause, minimum 3 ATR impulse, maximum 50% retracement, maximum width 45% of the impulse, 2-bar confirmed pivots, tight 0.08% equal-level matching with an ATR volatility allowance, 25% convergence, 0.85 consolidation-volume ratio, and 1.20 breakout-volume ratio. All supported pattern families stay enabled. These are intentionally not editable because changing many correlated geometry coefficients encourages curve fitting and makes results inconsistent between tickers.</p>
            <p class="text-xs text-slate-500"><strong>Risk note:</strong> Pattern recognition is probabilistic. Use the invalidation line for structure-aware risk, avoid treating the measured target as guaranteed, and validate settings out of sample for each ticker and timeframe.</p>
        </div>`;

    function escapeHtml(value) {
        return String(value).replace(/[&<>"']/g, char => ({
            '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;',
        })[char]);
    }

    function botBulls5PatternSummary(history, options = {}) {
        const rows = Array.isArray(history) ? history : [];
        const selector = window.StandardIndicatorSubcharts?.selectBotBulls5Pattern;
        const selection = typeof selector === 'function'
            ? selector(rows, options)
            : null;
        if (typeof selector === 'function' && !selection) return null;
        let rowIndex = selection?.index ?? rows.length - 1;
        let row = selection?.row ?? rows[rowIndex];
        if (!row?.bb5_pattern || row.bb5_pattern === 'NONE') return null;
        const latestPatternState = String(row.bb5_state || 'FORMING').toUpperCase();
        if (
            row.bb5_state === 'CONFIRMED_BULLISH'
            || row.bb5_state === 'CONFIRMED_BEARISH'
            || row.bb5_state === 'FAILED'
        ) {
            for (let index = rowIndex; index >= 0; index -= 1) {
                if (
                    rows[index]?.bb5_pattern === row.bb5_pattern
                    && (rows[index]?.bb5_breakout_bull || rows[index]?.bb5_breakout_bear)
                ) {
                    row = rows[index];
                    rowIndex = index;
                    break;
                }
            }
        }

        const patternCode = String(row.bb5_pattern);
        const pattern = patternCode
            .replace(/_(BULLISH|BEARISH)$/, '')
            .toLowerCase()
            .replaceAll('_', ' ')
            .replace(/\b\w/g, letter => letter.toUpperCase());
        const stateCode = latestPatternState;
        const state = stateCode.startsWith('CONFIRMED')
            ? 'Confirmed'
            : stateCode === 'FAILED'
                ? 'Invalidated'
                : 'Forming';
        const directionValue = Number(row.bb5_direction)
            || (row.bb5_breakout_bull ? 1 : row.bb5_breakout_bear ? -1 : 0);
        const directionWord = directionValue > 0 ? 'Bullish' : directionValue < 0 ? 'Bearish' : 'Neutral';
        const continuationPattern = /(FLAG|PENNANT|TRIANGLE|RECTANGLE|PRICE_CHANNEL|MEASURED_MOVE|CONTINUATION)/.test(patternCode);
        const reversalPattern = /(TOP|BOTTOM|HEAD_AND_SHOULDERS|BUMP_AND_RUN|ROUNDING|CUP_WITH_HANDLE|WEDGE)/.test(patternCode);
        const finite = value => value !== null && value !== undefined && value !== '' && Number.isFinite(Number(value))
            ? Number(value)
            : null;
        return {
            pattern,
            state,
            direction: `${directionWord} ${continuationPattern ? 'continuation' : reversalPattern ? 'reversal' : 'breakout'}`,
            confidence: finite(row.bb5_confidence),
            breakout: finite(row.bb5_breakout_level)
                ?? (directionValue >= 0 ? finite(row.bb5_upper) : finite(row.bb5_lower)),
            invalidation: finite(row.bb5_invalidation),
            target: finite(row.bb5_target),
            reasons: String(row.bb5_reasons || 'Qualified ATR-normalized structure; waiting for complete metadata refresh.'),
            time: finite(row.time),
            barsAgo: Math.max(0, rows.length - 1 - rowIndex),
        };
    }

    function renderBotBulls5PatternSummary(container, history, options = {}) {
        if (!container) return;
        const summary = botBulls5PatternSummary(history, options);
        const panel = document.createElement('div');
        panel.className = 'mb-4 rounded-xl border border-indigo-100 bg-gradient-to-br from-indigo-50 to-white p-3 text-xs text-slate-700';
        panel.dataset.botbulls5Summary = 'true';
        if (!summary) {
            panel.innerHTML = `
                <div class="font-black text-indigo-950 uppercase tracking-wide">Live pattern status</div>
                <p class="mt-1 text-slate-500">No qualified BotBulls5 pattern is present in the loaded chart range. Enable BotBulls5 and refresh the chart to populate forming or confirmed setup details.</p>`;
            container.prepend(panel);
            return;
        }
        const price = value => value === null
            ? '—'
            : `$${value.toLocaleString(undefined, { minimumFractionDigits: value < 10 ? 4 : 2, maximumFractionDigits: value < 10 ? 4 : 2 })}`;
        const detectedAt = summary.time === null
            ? '—'
            : new Date(summary.time * 1000).toLocaleString([], { dateStyle: 'medium', timeStyle: 'short' });
        const recency = summary.barsAgo === 0
            ? 'latest loaded candle'
            : `${summary.barsAgo} bar${summary.barsAgo === 1 ? '' : 's'} ago`;
        panel.innerHTML = `
            <div class="flex items-center justify-between gap-2">
                <div class="font-black text-indigo-950">${escapeHtml(summary.pattern)} — ${escapeHtml(summary.state)}</div>
                <span class="rounded-full bg-indigo-100 px-2 py-0.5 text-[0.6rem] font-black uppercase tracking-wide text-indigo-700">Latest-day chart result</span>
            </div>
            <div class="mt-2 grid grid-cols-2 gap-x-3 gap-y-1">
                <div><span class="text-slate-400">Direction:</span> <span class="font-bold">${escapeHtml(summary.direction)}</span></div>
                <div><span class="text-slate-400">Confidence:</span> <span class="font-bold">${summary.confidence === null ? '—' : `${summary.confidence.toFixed(0)}%`}</span></div>
                <div><span class="text-slate-400">Breakout:</span> <span class="font-bold">${price(summary.breakout)}</span></div>
                <div><span class="text-slate-400">Invalidation:</span> <span class="font-bold">${price(summary.invalidation)}</span></div>
                <div class="col-span-2"><span class="text-slate-400">Measured target:</span> <span class="font-bold">${price(summary.target)}</span></div>
            </div>
            <div class="mt-2"><span class="text-slate-400">Reasons:</span> ${escapeHtml(summary.reasons).replaceAll(';', ',')}</div>
            <div class="mt-2 border-t border-indigo-100 pt-2 text-[0.65rem] text-slate-500">Detected on ${escapeHtml(detectedAt)} · ${escapeHtml(recency)}. Confirmed markers are attached to the breakout candle after that candle closes; they are not shifted to the next candle.</div>
            <div class="mt-1 text-[0.6rem] text-slate-400">Confidence is setup quality, not probability of profit. Targets and invalidation levels are planning references, not guarantees.</div>`;
        container.prepend(panel);
    }

    function buildStrategyCard(item, mode, checkClass, changeHandler) {
        const hasSettings = (configMap[item.name] || []).length > 0;
        const safeName = escapeHtml(item.name);
        const safeLabel = escapeHtml(item.label);
        const safeCategory = escapeHtml(item.category);
        const card = document.createElement('div');
        card.className = 'indicator-card group p-2.5 border border-white/20 rounded-2xl cursor-pointer flex items-center justify-between shadow-sm transition-all';
        card.dataset.indicatorWorkspaceName = item.name;
        if (catalog.indexOf(item) >= 10) {
            card.hidden = true;
            card.classList.add('indicator-workspace-hidden');
            card.setAttribute('aria-hidden', 'true');
        }
        card.setAttribute('onclick', 'toggleIndicatorCard(this, event)');
        card.innerHTML = `
            <div class="flex flex-col min-w-0">
                <span class="font-black text-indigo-900 uppercase text-[0.6rem] sm:text-xs tracking-tight leading-tight">${safeLabel}
                    <button type="button" onclick="event.stopPropagation(); event.preventDefault(); openInfoModal('${safeName.replace(/'/g, "\\'")}')" class="inline-flex ml-1 align-middle text-indigo-300 hover:text-indigo-600 transition-colors" title="${safeLabel} guide" aria-label="Open ${safeLabel} guide">
                        <svg xmlns="http://www.w3.org/2000/svg" class="h-3.5 w-3.5" fill="none" viewBox="0 0 24 24" stroke="currentColor"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M13 16h-1v-4h-1m1-4h.01M21 12a9 9 0 11-18 0 9 9 0 0118 0z"/></svg>
                    </button>
                </span>
                <span class="text-[0.6rem] text-indigo-400 font-bold uppercase tracking-widest">${safeCategory}</span>
            </div>
            <div class="flex items-center gap-2">
                ${hasSettings ? `<button type="button" onclick="event.stopPropagation(); event.preventDefault(); openIndicatorSettings('${safeName.replace(/'/g, "\\'")}')" class="opacity-70 hover:opacity-100 transition-opacity p-0.5 rounded hover:bg-indigo-50 text-indigo-500" title="${safeLabel} Settings" aria-label="Open ${safeLabel} settings"><svg xmlns="http://www.w3.org/2000/svg" class="h-3.5 w-3.5" fill="none" viewBox="0 0 24 24" stroke="currentColor"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2.5" d="M10.325 4.317c.426-1.756 2.924-1.756 3.35 0a1.724 1.724 0 002.573 1.066c1.543-.94 3.31.826 2.37 2.37a1.724 1.724 0 001.065 2.572c1.756.426 1.756 2.924 0 3.35a1.724 1.724 0 00-1.066 2.573c.94 1.543-.826 3.31-2.37 2.37a1.724 1.724 0 00-2.572 1.065c-.426 1.756-2.924 1.756-3.35 0a1.724 1.724 0 00-2.573-1.066c-1.543.94-3.31-.826-2.37-2.37a1.724 1.724 0 00-1.065-2.572c-1.756-.426-1.756-2.924 0-3.35a1.724 1.724 0 001.066-2.573c-.94-1.543.826-3.31 2.37-2.37.996.608 2.296.07 2.572-1.065z"/><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2.5" d="M15 12a3 3 0 11-6 0 3 3 0 016 0z"/></svg></button>` : ''}
                <input type="checkbox" class="strategy-indicator-check ${checkClass} w-5 h-5 accent-indigo-600 rounded" value="${safeName}" onclick="event.stopPropagation()" onchange="${changeHandler}('${mode}')">
            </div>`;
        return card;
    }

    function ensureStandardIndicatorCards() {
        const targets = [
            { id: 'indicatorChecksContainer_regular', mode: 'regular', checkClass: 'strategy-indicator-check-regular', handler: 'updateStrategySignalLabels' },
            { id: 'indicatorChecksContainer_options', mode: 'options', checkClass: 'strategy-indicator-check-options', handler: 'updateStrategySignalLabels' },
            { id: 'indicatorChecksContainer_bt_regular', mode: 'regular', checkClass: 'bt-indicator-check-regular', handler: 'updateSignalLabels' },
            { id: 'indicatorChecksContainer_bt_options', mode: 'options', checkClass: 'bt-indicator-check-options', handler: 'updateSignalLabels' },
        ];
        targets.forEach(target => {
            const container = document.getElementById(target.id);
            if (!container) return;
            const present = new Set(Array.from(container.querySelectorAll('input.strategy-indicator-check')).map(input => input.value));
            catalog.forEach(item => {
                if (present.has(item.name)) return;
                container.appendChild(buildStrategyCard(item, target.mode, target.checkClass, target.handler));
            });
            const standardNames = new Set(catalog.map(item => item.name));
            const defaultShown = new Set(catalog.slice(0, 10).map(item => item.name));
            container.querySelectorAll('.indicator-card').forEach(card => {
                const name = card.querySelector('input.strategy-indicator-check')?.value;
                if (!standardNames.has(name)) return;
                const isShown = defaultShown.has(name);
                card.hidden = !isShown;
                card.classList.toggle('indicator-workspace-hidden', !isShown);
                card.setAttribute('aria-hidden', isShown ? 'false' : 'true');
            });
        });
    }

    window.STANDARD_INDICATOR_CATALOG = Object.freeze(catalog.map(item => Object.freeze({ ...item })));
    window.STANDARD_INDICATOR_NAMES = Object.freeze(catalog.map(item => item.name));
    window.STANDARD_INDICATOR_CONFIG_MAP = Object.freeze(configMap);
    window.STANDARD_INDICATOR_DEFAULTS = Object.freeze(defaults);
    window.STANDARD_INDICATOR_PARAM_LABELS = Object.freeze(labels);
    window.STANDARD_INDICATOR_INPUT_ATTRS = Object.freeze(inputAttrs);
    window.STANDARD_INDICATOR_TOGGLE_KEYS = Object.freeze(toggleKeys);
    window.INDICATOR_GUIDES = Object.freeze(guides);
    window.ensureStandardIndicatorCards = ensureStandardIndicatorCards;
    window.getBotBulls5PatternSummary = botBulls5PatternSummary;
    window.renderBotBulls5PatternSummary = renderBotBulls5PatternSummary;

    ensureStandardIndicatorCards();
    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', ensureStandardIndicatorCards, { once: true });
    }
})();
