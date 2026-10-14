(function () {
    'use strict';

    // Standard/classical indicators only. The formulas and guide copy are
    // independently implemented.
    const catalog = [
        { name: 'RSI', label: 'RSI', category: 'Momentum' },
        { name: 'MACD', label: 'MACD', category: 'Trend' },
        { name: 'EMA Cross', label: 'EMA Cross', category: 'Moving average' },
        { name: 'ADX Trend', label: 'ADX Trend', category: 'Trend strength' },
        { name: 'Supertrend', label: 'Supertrend', category: 'Trend' },
        { name: 'Bollinger', label: 'Bollinger Bands', category: 'Volatility' },
        { name: 'VWAP', label: 'Session VWAP', category: 'Volume / price' },
        { name: 'Volume Profile', label: 'Volume Profile', category: 'Volume / structure' },
        { name: 'SMA', label: 'SMA', category: 'Trend filter' },
        { name: 'Prior Day High/Low', label: 'Prior Day High/Low', category: 'Price structure' },
        { name: 'Pivot Points (S/R)', label: 'Pivot Points (S/R)', category: 'Price structure' },
        { name: 'Swing Points', label: 'Swing Points', category: 'Price structure' },
        { name: 'Mystic Pulse', label: 'Mystic Pulse', category: 'Custom momentum' },
        { name: 'Candle Patterns', label: 'Candle Patterns', category: 'Price action' },
        { name: 'Chart Patterns', label: 'Chart Patterns', category: 'Price action' },
        { name: 'Trend Rider', label: 'Trend Rider', category: 'Trend' },
        { name: 'HA+UT', label: 'HA+UT', category: 'Trend' },
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
        { name: 'Auto-AVWAP', label: 'Auto-AVWAP', category: 'Volume / price' },
    ];

    const configMap = {
        'RSI': ['RSI_PERIOD', 'RSI_OVERBOUGHT', 'RSI_OVERSOLD'],
        'MACD': ['MACD_FAST', 'MACD_SLOW', 'MACD_SIGNAL'],
        'EMA Cross': ['EMA_USE_SECOND_LINE', 'EMA_FAST', 'EMA_SLOW', 'EMA_CROSS_BUFFER_PCT'],
        'ADX Trend': ['ADX_PERIOD', 'ADX_TRENDING_THRESHOLD'],
        'Supertrend': ['SUPERTREND_PERIOD', 'SUPERTREND_MULTIPLIER'],
        'Bollinger': ['BOLL_PERIOD', 'BOLL_STD_DEV', 'BOLL_ZONE_PCT'],
        'VWAP': ['VWAP_REGULAR_SESSION_ONLY'],
        'Auto-AVWAP': ['VOL_MA_PERIOD', 'VOL_SPIKE_MULTIPLIER'],
        'Volume Profile': ['VOLUME_PROFILE_ROWS', 'VOLUME_PROFILE_VALUE_AREA_PCT', 'VOLUME_PROFILE_TOLERANCE_PCT', 'VOLUME_PROFILE_CONFIRMATION_CANDLES', 'LEVELS_REGULAR_SESSION_ONLY'],
        'SMA': ['SMA_USE_SECOND_LINE', 'SMA_PERIOD', 'SMA_SECOND_PERIOD'],
        'Prior Day High/Low': ['DAILY_HIGH_LOW_BUFFER_PCT', 'DAILY_HIGH_LOW_BUFFER_ATR', 'LEVELS_REGULAR_SESSION_ONLY'],
        'Pivot Points (S/R)': ['PIVOT_LEVEL_COUNT', 'PIVOT_TOLERANCE_PCT', 'LEVELS_REGULAR_SESSION_ONLY'],
        'Swing Points': ['SWING_POINTS_LOOKBACK_DAYS', 'SWING_POINTS_ENTRY_MODE', 'SWING_POINTS_LEVEL_COUNT', 'SWING_POINTS_STRENGTH'],
        'Mystic Pulse': ['MYSTIC_PULSE_THRESHOLD', 'MYSTIC_PULSE_ADX_LENGTH'],
        'Candle Patterns': ['CANDLE_RELIABLE_ONLY'],
        'Chart Patterns': ['BOTBULLS5_MIN_CONFIDENCE', 'BOTBULLS5_BREAKOUT_BUFFER_ATR', 'BOTBULLS5_STRUCTURE_LOOKBACK', 'BOTBULLS5_SIGNAL_HOLD_BARS', 'BOTBULLS5_REQUIRE_VOLUME'],
        'Trend Rider': ['TREND_RIDER_ATR_PERIOD', 'TREND_RIDER_ATR_MULT', 'TREND_RIDER_TRACER_PERIOD', 'TREND_RIDER_ZONE_PERIOD', 'TREND_RIDER_ZONE_STD', 'TREND_RIDER_RSI_LOW', 'TREND_RIDER_RSI_HIGH'],
        'HA+UT': ['HA_UT_ATR_PERIOD', 'HA_UT_ATR_MULT'],
        'ATR Volatility': ['ATR_PERIOD', 'ATR_BASELINE_PERIOD', 'ATR_RANGE_MULTIPLIER', 'ATR_EXPANSION_THRESHOLD_PCT'],
        'Stochastic': ['STOCH_PERIOD', 'STOCH_SMOOTH_K', 'STOCH_SMOOTH_D', 'STOCH_OVERBOUGHT', 'STOCH_OVERSOLD', 'STOCH_CROSS_ZONE_ONLY'],
        'Stochastic RSI': ['STOCH_RSI_PERIOD', 'STOCH_RSI_SMOOTH_K', 'STOCH_RSI_SMOOTH_D', 'STOCH_RSI_OVERBOUGHT', 'STOCH_RSI_OVERSOLD', 'STOCH_RSI_CROSS_ZONE_ONLY'],
        'CCI': ['CCI_PERIOD', 'CCI_THRESHOLD'],
        'Williams %R': ['WILLIAMS_R_PERIOD', 'WILLIAMS_R_OVERBOUGHT', 'WILLIAMS_R_OVERSOLD'],
        'Rate of Change': ['ROC_PERIOD', 'ROC_THRESHOLD_PCT'],
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
        EMA_USE_SECOND_LINE: 1, EMA_FAST: 9, EMA_SLOW: 21,
        ADX_PERIOD: 14, ADX_TRENDING_THRESHOLD: 25,
        SUPERTREND_PERIOD: 10, SUPERTREND_MULTIPLIER: 3,
        BOLL_PERIOD: 20, BOLL_STD_DEV: 2,
        VOLUME_PROFILE_ROWS: 48, VOLUME_PROFILE_VALUE_AREA_PCT: 70,
        VOLUME_PROFILE_TOLERANCE_PCT: 0.15, VOLUME_PROFILE_CONFIRMATION_CANDLES: 1,
        SMA_USE_SECOND_LINE: 1, SMA_PERIOD: 200, SMA_SECOND_PERIOD: 50,
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
        TREND_RIDER_ATR_PERIOD: 14, TREND_RIDER_ATR_MULT: 2, TREND_RIDER_TRACER_PERIOD: 50, TREND_RIDER_ZONE_PERIOD: 50,
        HA_UT_ATR_PERIOD: 10, HA_UT_ATR_MULT: 1,
        VOL_MA_PERIOD: 20, VOL_SPIKE_MULTIPLIER: 1.5,
        EMA_CROSS_BUFFER_PCT: 0, BOLL_ZONE_PCT: 20, VWAP_REGULAR_SESSION_ONLY: 0,
        DAILY_HIGH_LOW_BUFFER_ATR: 0, LEVELS_REGULAR_SESSION_ONLY: 0, SWING_POINTS_STRENGTH: 2,
        MYSTIC_PULSE_ADX_LENGTH: 0, CANDLE_RELIABLE_ONLY: 0,
        BOTBULLS5_MIN_CONFIDENCE: 70, BOTBULLS5_BREAKOUT_BUFFER_ATR: 0.1, BOTBULLS5_STRUCTURE_LOOKBACK: 48,
        BOTBULLS5_SIGNAL_HOLD_BARS: 3, BOTBULLS5_REQUIRE_VOLUME: 0,
        TREND_RIDER_ZONE_STD: 2, TREND_RIDER_RSI_LOW: 40, TREND_RIDER_RSI_HIGH: 70,
        STOCH_CROSS_ZONE_ONLY: 1, STOCH_RSI_CROSS_ZONE_ONLY: 1, ROC_THRESHOLD_PCT: 0,
    };

    const labels = {
        RSI_PERIOD: 'RSI Period', RSI_OVERBOUGHT: 'Overbought Level', RSI_OVERSOLD: 'Oversold Level',
        MACD_FAST: 'Fast EMA', MACD_SLOW: 'Slow EMA', MACD_SIGNAL: 'Signal EMA',
        EMA_USE_SECOND_LINE: 'Use Two EMA Lines',
        EMA_FAST: 'Primary EMA Period', EMA_SLOW: 'Secondary EMA Period',
        ADX_PERIOD: 'ADX / DI Period', ADX_TRENDING_THRESHOLD: 'Trend Strength Threshold',
        SUPERTREND_PERIOD: 'ATR Period', SUPERTREND_MULTIPLIER: 'ATR Multiplier',
        BOLL_PERIOD: 'Middle-Band Period', BOLL_STD_DEV: 'Standard-Deviation Multiplier',
        VOLUME_PROFILE_ROWS: 'Price Rows', VOLUME_PROFILE_VALUE_AREA_PCT: 'Value Area %',
        VOLUME_PROFILE_TOLERANCE_PCT: 'Level Tolerance %',
        VOLUME_PROFILE_CONFIRMATION_CANDLES: 'Acceptance Candles',
        SMA_USE_SECOND_LINE: 'Use Two SMA Lines',
        SMA_PERIOD: 'Primary SMA Period',
        SMA_SECOND_PERIOD: 'Secondary SMA Period',
        DAILY_HIGH_LOW_BUFFER_PCT: 'Breakout Buffer %',
        PIVOT_LEVEL_COUNT: 'Visible S/R Levels', PIVOT_TOLERANCE_PCT: 'Level Tolerance %',
        SWING_POINTS_LOOKBACK_DAYS: 'Structure Lookback Days',
        SWING_POINTS_ENTRY_MODE: 'Entry Confirmation Mode',
        SWING_POINTS_LEVEL_COUNT: 'Visible Structure Levels',
        MYSTIC_PULSE_THRESHOLD: 'Directional Score Threshold',
        VOL_MA_PERIOD: 'Volume Average Period', VOL_SPIKE_MULTIPLIER: 'Spike Multiplier',
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
        TREND_RIDER_ATR_PERIOD: 'ATR Period', TREND_RIDER_ATR_MULT: 'ATR Multiplier',
        TREND_RIDER_TRACER_PERIOD: 'Tracer Period', TREND_RIDER_ZONE_PERIOD: 'Zone Period',
        HA_UT_ATR_PERIOD: 'ATR Period', HA_UT_ATR_MULT: 'ATR Multiplier',
        EMA_CROSS_BUFFER_PCT: 'Min Line Gap % (0 = off)', BOLL_ZONE_PCT: 'Edge Zone % of Band',
        VWAP_REGULAR_SESSION_ONLY: 'Regular Hours Only (1 = on)',
        DAILY_HIGH_LOW_BUFFER_ATR: 'Breakout Buffer (ATR)', LEVELS_REGULAR_SESSION_ONLY: 'Levels From Regular Hours (1 = on)',
        SWING_POINTS_STRENGTH: 'Pivot Strength (bars)',
        MYSTIC_PULSE_ADX_LENGTH: 'DMI Length (0 = auto)', CANDLE_RELIABLE_ONLY: 'Reliable Patterns Only (1 = on)',
        BOTBULLS5_MIN_CONFIDENCE: 'Min Pattern Quality', BOTBULLS5_BREAKOUT_BUFFER_ATR: 'Breakout Buffer (ATR)',
        BOTBULLS5_STRUCTURE_LOOKBACK: 'Pattern Lookback Bars', BOTBULLS5_SIGNAL_HOLD_BARS: 'Signal Hold Bars',
        BOTBULLS5_REQUIRE_VOLUME: 'Require Volume (1 = on)',
        TREND_RIDER_ZONE_STD: 'Zone Width (σ)', TREND_RIDER_RSI_LOW: 'RSI Band Low', TREND_RIDER_RSI_HIGH: 'RSI Band High',
        STOCH_CROSS_ZONE_ONLY: 'Cross In Zone Only (1 = on)', STOCH_RSI_CROSS_ZONE_ONLY: 'Cross In Zone Only (1 = on)',
        ROC_THRESHOLD_PCT: 'Dead Band %',
    };

    const decimalKeys = new Set([
        'SUPERTREND_MULTIPLIER', 'BOLL_STD_DEV', 'VOLUME_PROFILE_VALUE_AREA_PCT',
        'VOLUME_PROFILE_TOLERANCE_PCT', 'DAILY_HIGH_LOW_BUFFER_PCT', 'PIVOT_TOLERANCE_PCT',
        'ATR_STOP_MULTIPLIER', 'ATR_TRAIL_MULTIPLIER', 'ATR_TAKE_PROFIT_MULTIPLIER',
        'ATR_RANGE_MULTIPLIER', 'ATR_EXPANSION_THRESHOLD_PCT',
        'VOL_SPIKE_MULTIPLIER',
        'PSAR_STEP', 'PSAR_MAX_STEP', 'KELTNER_MULTIPLIER', 'TREND_RIDER_ATR_MULT', 'HA_UT_ATR_MULT',
        'CHOP_TREND_THRESHOLD', 'CHOP_RANGE_THRESHOLD', 'ALMA_OFFSET', 'ALMA_SIGMA',
        'EMA_CROSS_BUFFER_PCT', 'BOLL_ZONE_PCT', 'DAILY_HIGH_LOW_BUFFER_ATR', 'BOTBULLS5_BREAKOUT_BUFFER_ATR',
        'TREND_RIDER_ZONE_STD', 'ROC_THRESHOLD_PCT',
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
    inputAttrs.TREND_RIDER_ATR_PERIOD = 'step="1" min="2" max="200"';
    inputAttrs.TREND_RIDER_ATR_MULT = 'step="0.1" min="0.1" max="10"';
    inputAttrs.TREND_RIDER_TRACER_PERIOD = 'step="1" min="4" max="500"';
    inputAttrs.TREND_RIDER_ZONE_PERIOD = 'step="1" min="5" max="500"';
    inputAttrs.HA_UT_ATR_PERIOD = 'step="1" min="2" max="200"';
    inputAttrs.HA_UT_ATR_MULT = 'step="0.1" min="0.1" max="10"';
    inputAttrs.ATR_PERIOD = 'step="1" min="2" max="200"';
    inputAttrs.ATR_BASELINE_PERIOD = 'step="1" min="2" max="500"';
    inputAttrs.ATR_RANGE_MULTIPLIER = 'step="0.1" min="0.1" max="10"';
    inputAttrs.ATR_EXPANSION_THRESHOLD_PCT = 'step="0.5" min="0" max="100"';
    inputAttrs.RSI_PERIOD = 'step="1" min="2" max="500"';
    inputAttrs.RSI_OVERBOUGHT = 'step="1" min="0" max="100"';
    inputAttrs.RSI_OVERSOLD = 'step="1" min="0" max="100"';
    inputAttrs.MACD_FAST = 'step="1" min="2" max="500"';
    inputAttrs.MACD_SLOW = 'step="1" min="2" max="500"';
    inputAttrs.MACD_SIGNAL = 'step="1" min="2" max="500"';
    inputAttrs.EMA_FAST = 'step="1" min="1" max="500"';
    inputAttrs.EMA_SLOW = 'step="1" min="2" max="500"';
    inputAttrs.SMA_PERIOD = 'step="1" min="1" max="500"';
    inputAttrs.SMA_SECOND_PERIOD = 'step="1" min="1" max="500"';
    inputAttrs.ADX_PERIOD = 'step="1" min="2" max="500"';
    inputAttrs.ADX_TRENDING_THRESHOLD = 'step="1" min="0" max="100"';
    inputAttrs.VOL_MA_PERIOD = 'step="1" min="2" max="200"';
    inputAttrs.VOL_SPIKE_MULTIPLIER = 'step="0.1" min="0.1" max="10"';
    inputAttrs.SWING_POINTS_ENTRY_MODE = '';
    inputAttrs.EMA_CROSS_BUFFER_PCT = 'step="0.05" min="0" max="5"';
    inputAttrs.BOLL_ZONE_PCT = 'step="1" min="1" max="50"';
    ['VWAP_REGULAR_SESSION_ONLY', 'LEVELS_REGULAR_SESSION_ONLY', 'CANDLE_RELIABLE_ONLY', 'BOTBULLS5_REQUIRE_VOLUME',
     'STOCH_CROSS_ZONE_ONLY', 'STOCH_RSI_CROSS_ZONE_ONLY'].forEach(key => { inputAttrs[key] = 'step="1" min="0" max="1"'; });
    inputAttrs.DAILY_HIGH_LOW_BUFFER_ATR = 'step="0.05" min="0" max="3"';
    inputAttrs.SWING_POINTS_STRENGTH = 'step="1" min="1" max="10"';
    inputAttrs.MYSTIC_PULSE_ADX_LENGTH = 'step="1" min="0" max="100"';
    inputAttrs.BOTBULLS5_MIN_CONFIDENCE = 'step="1" min="40" max="95"';
    inputAttrs.BOTBULLS5_BREAKOUT_BUFFER_ATR = 'step="0.05" min="0" max="1"';
    inputAttrs.BOTBULLS5_STRUCTURE_LOOKBACK = 'step="1" min="20" max="120"';
    inputAttrs.BOTBULLS5_SIGNAL_HOLD_BARS = 'step="1" min="1" max="20"';
    inputAttrs.TREND_RIDER_ZONE_STD = 'step="0.1" min="0.5" max="5"';
    inputAttrs.TREND_RIDER_RSI_LOW = 'step="1" min="0" max="100"';
    inputAttrs.TREND_RIDER_RSI_HIGH = 'step="1" min="0" max="100"';
    inputAttrs.ROC_THRESHOLD_PCT = 'step="0.05" min="0" max="10"';

    const toggleKeys = {
        'RSI': 'ENABLE_RSI',
        'MACD': 'ENABLE_MACD',
        'EMA Cross': 'ENABLE_EMA',
        'ADX Trend': 'ENABLE_ADX_TREND',
        'Supertrend': 'ENABLE_SUPERTREND',
        'Bollinger': 'ENABLE_BOLLINGER',
        'VWAP': 'ENABLE_VWAP',
        'Auto-AVWAP': 'ENABLE_AUTO_AVWAP',
        'Volume Profile': 'ENABLE_VOLUME_PROFILE',
        'SMA': 'ENABLE_SMA',
        'Prior Day High/Low': 'ENABLE_DAILY_HIGH_LOW',
        'Pivot Points (S/R)': 'ENABLE_PIVOT_POINTS',
        'Swing Points': 'ENABLE_SWING_POINTS',
        'Mystic Pulse': 'ENABLE_MYSTIC_PULSE',
        'Candle Patterns': 'ENABLE_CANDLE_PATTERNS',
        'Chart Patterns': 'ENABLE_CHART_PATTERNS',
        'Trend Rider': 'ENABLE_TREND_RIDER',
        'HA+UT': 'ENABLE_HA_UT',
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
        'RSI': 'Uses textbook Wilder smoothing to compare average gains with average losses on a 0–100 scale. The first value requires one full period of completed price changes; earlier candles remain blank instead of receiving a future value.',
        'MACD': 'Subtracts the slower completed-candle EMA from the faster EMA, smooths that MACD line into a signal line, and plots their difference as the histogram. If the two EMA settings are reversed, the lower period is automatically treated as fast.',
        'EMA Cross': 'Dynamic EMA mode uses completed-candle exponential averages. With one line, price is compared with the primary EMA. With two lines, the lower-period EMA is automatically treated as the fast line and compared with the higher-period EMA, regardless of which period field contains the smaller number.',
        'ADX Trend': 'Uses Wilder-smoothed ADX, +DI, and −DI. ADX measures trend strength rather than direction; +DI versus −DI supplies direction only after ADX reaches the configured strength threshold.',
        'Supertrend': 'Builds an ATR-based trailing trend line around price. It is useful for trend state and trailing-risk context, but can flip repeatedly in a range.',
        'Bollinger': 'Places standard-deviation envelopes around a moving average. The grid uses the outer portions of the channel as mean-reversion alert zones.',
        'VWAP': 'Calculates Session VWAP from cumulative typical price × volume divided by cumulative volume, resetting on each exchange-local session date. This is the Webull-comparable session line and is separate from Auto-AVWAP.',
        'Auto-AVWAP': 'Automatically anchors a volume-weighted average at the latest candle whose volume qualifies as a spike versus its rolling volume average. The anchor persists until a newer qualified spike appears, so this structural line will not necessarily match Session VWAP.',
        'Volume Profile': 'Builds a completed prior-session volume-at-price distribution and a matching right-side histogram. POC is the highest-volume price; VAH and VAL bound the selected percentage of accepted volume.',
        'SMA': 'Dynamic SMA mode uses completed-candle simple averages without filling unavailable warm-up bars. With one line, price is compared with the primary SMA. With two lines, the lower-period SMA is automatically treated as the fast line and compared with the higher-period SMA.',
        'Prior Day High/Low': 'Plots the previous completed session’s high and low, plus optional buffered trigger lines. It is an event-based breakout indicator: an old breakout is not repeatedly counted on every later candle.',
        'Pivot Points (S/R)': 'Calculates classic floor-trader Pivot (P) and R1/S1 through R5/S5 from the previous completed session’s high, low, and close. The selected level count controls how much of the ladder is drawn.',
        'Swing Points': 'Clusters rolling local highs and lows into up to five structural support and resistance levels, scored by rejection, volume, range expansion, recency, and repeated touches. It is price-structure logic rather than a mathematical oscillator.',
        'Mystic Pulse': 'Combines the app’s internal momentum, volatility, and volume features into separate bullish and bearish strength scores.',
        'Candle Patterns': 'Classifies causal single- and multi-candle OHLC formations on every completed bar. The chart keeps recognized annotations from the latest trading day (rolling 24 hours for crypto), attached to the final candle that confirms each formation. Every candle used by the formation is emphasized with a dark outline: one bar for single-candle patterns, two for engulfing/harami/tweezer patterns, and three for star/soldier/crow/inside/outside patterns. Historical annotations are context, not proof of a reversal.',
        'Chart Patterns': 'Finds classic chart patterns on completed candles: flags, pennants, wedges, triangles, rectangles and channels, double and triple tops and bottoms, head-and-shoulders, cup with handle and rounding bottoms. A pattern is only called when its setup quality is high enough, and the breakout only counts once a completed candle closes through the pattern edge. Patterns that are still forming are shown as neutral, and a pattern that fails is dropped.',
        'Trend Rider': 'Draws an ATR trailing line that sits below price in an uptrend and above it in a downtrend, flipping only when price closes through it. A smoothed trend tracer, plus upper and lower reversal zones two standard deviations from a moving average, add context, and a 0 to 4 confluence score counts how many of them agree: trail up, price above the tracer, price below the upper zone, and RSI(14) between 40 and 70. All values use completed candles only.',
        'HA+UT': 'Applies an ATR trailing stop to Heikin-Ashi candles, which smooth out noise before the trail is calculated. The trail sits below the Heikin-Ashi close while it is above the trail and flips above price when the close falls through it. A shorter ATR period and a lower multiplier make the trail hug price and flip more often.',
        'ATR Volatility': 'Measures movement per completed candle with Wilder’s true-range method, including gaps. ATR is not directional. Its expected-range bands are causal: each candle uses the prior close plus or minus the prior completed ATR multiplied by the selected width, so those bands were available before the candle opened.',
        'Stochastic': 'Compares the close with the recent high-low range. A %K/%D turn from below the oversold level supports a bullish reversal; a turn from above overbought supports a bearish reversal.',
        'Stochastic RSI': 'Applies the stochastic calculation to RSI for a faster momentum reading. Use its threshold turns as timing confirmation, especially with a separate trend filter.',
        'CCI': 'Measures price deviation from its statistical mean. The grid treats a rising oversold reading or a falling overbought reading as an exhaustion-reversal setup.',
        'Williams %R': 'Locates the close within the recent range on a -100 to 0 scale. A rising turn from oversold supports buyers; a falling turn from overbought supports sellers.',
        'Rate of Change': 'Expresses the percentage change from a prior close. Positive values indicate upward momentum and negative values indicate downward momentum.',
        'Momentum': 'Measures the point change from a prior close. Above zero means price is higher than N bars ago; below zero means lower.',
        'Money Flow Index': 'Combines price and volume into a bounded momentum oscillator. Turns from oversold or overbought are strongest when price structure confirms them.',
        'Ultimate Oscillator': 'Blends short, medium, and long buying-pressure windows to reduce single-period distortion. Readings near 30 mark oversold and near 70 overbought.',
        'Awesome Oscillator': 'Compares fast and slow averages of median price. Above zero favors bullish momentum; below zero favors bearish momentum.',
        'Chande Momentum Oscillator': 'Compares total gains with total losses on a -100 to +100 scale. Readings beyond the positive or negative threshold mark overbought or oversold extremes.',
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
        'On-Balance Volume': 'Adds volume on up closes, subtracts it on down closes and adds nothing when the close is unchanged (Granville). OBV above its signal EMA supports accumulation; below supports distribution.',
        'Chaikin Money Flow': 'Aggregates close location within each bar weighted by volume. Positive values indicate accumulation pressure; negative values indicate distribution.',
        'Accumulation/Distribution': 'Builds a cumulative volume-flow line from each close location in its range. Above its signal EMA supports accumulation; below supports distribution.',
        'Chaikin Oscillator': 'Measures the difference between fast and slow EMAs of the accumulation/distribution line. Positive values favor accumulation; negative values favor distribution.',
        'Price Volume Trend': 'Cumulates volume adjusted by percentage price change. PVT above its signal EMA supports a bullish volume trend; below supports a bearish one.',
        'Ease of Movement': 'Relates price-range movement to volume. Positive smoothed readings favor upward movement with relative ease; negative readings favor downward movement.',
        'Force Index': 'Combines price change and volume, then smooths the result. Positive force favors buyers; negative force favors sellers.',
    };
    const signalGuide = {
        'RSI': 'Bullish only while RSI is below Oversold and rising; bearish only while above Overbought and falling. RSI inside the range, moving deeper into an extreme, equal thresholds, or warm-up is neutral. The indicator contributes at most one vote.',
        'MACD': 'A histogram above zero is one bullish vote and below zero is one bearish vote. A zero-line crossing is identified as a fresh crossover; continued positive or negative histogram remains directional. Equal fast/slow periods, zero, or warm-up is neutral.',
        'EMA Cross': 'One line uses price versus the primary EMA. Two lines use lower-period EMA versus higher-period EMA; a fresh crossing is labeled in the reason and continued alignment keeps its direction. Warm-up is neutral, and EMA always contributes at most one vote.',
        'ADX Trend': 'ADX at or above the strength threshold confirms a trend. Then +DI above −DI is bullish and −DI above +DI is bearish. Below the threshold or during warm-up, the vote is neutral; rising or falling ADX is reported as context rather than used as an extra gate.',
        'Supertrend': 'Bullish while the completed candle is in the upper trend state; bearish while in the lower trend state.',
        'Bollinger': 'Bullish in the lowest part of the band (default 20% of its width, set by Edge Zone % of Band), bearish in the highest part, and neutral in between.',
        'VWAP': 'Bullish on a fresh Session VWAP reclaim or a bullish candle bouncing within 1.5% of it; bearish below Session VWAP; otherwise neutral while merely holding above.',
        'Auto-AVWAP': 'Bullish on a fresh Auto-AVWAP reclaim or a bullish candle bouncing within 1.5% of it; bearish below Auto-AVWAP; otherwise neutral while merely holding above.',
        'Volume Profile': 'Bullish on confirmed acceptance above VAH, a POC reclaim, or a VAL bounce. Bearish on confirmed acceptance below VAL, a POC loss, or a VAH rejection. Otherwise neutral inside value or without a qualifying event.',
        'SMA': 'One line uses price versus the primary SMA. Two lines use lower-period SMA versus higher-period SMA; a fresh crossing is labeled in the reason and continued alignment keeps its direction. Warm-up is neutral, and SMA always contributes at most one vote.',
        'Prior Day High/Low': 'A fresh completed-candle close through the buffered prior high is bullish; a fresh close through the buffered prior low is bearish. Holding beyond a level after an earlier break, remaining inside the range, or missing prior-session history is neutral.',
        'Pivot Points (S/R)': 'An S1 support bounce within tolerance or a fresh Pivot reclaim is bullish; an R1 rejection within tolerance or a fresh Pivot loss is bearish. Otherwise, including missing prior-session history, the vote is neutral.',
        'Swing Points': 'Bounce / Retest mode votes on confirmed support bounces and resistance rejections. Breakout / Retest mode votes on resistance reclaims and support losses. Without confirmed structure, it is neutral.',
        'Mystic Pulse': 'Bullish or bearish when its corresponding score reaches the threshold; otherwise neutral.',
        'Candle Patterns': 'Only the latest completed candle can contribute a bullish or bearish confluence vote. Earlier same-day annotations remain visible for review but do not keep contributing votes; no current recognized pattern is neutral.',
        'Chart Patterns': 'Bullish when a completed candle confirms an upward breakout or reversal from a qualified pattern (for example a bull flag, falling wedge, double bottom or inverse head-and-shoulders); bearish for the mirror-image downside pattern; neutral while a pattern is still forming, after it fails, or when none qualifies. The vote shows the pattern name and its setup quality.',
        'Trend Rider': 'Bullish while the trailing line is below price (uptrend) and bearish while it is above price (downtrend); the reason shows the confluence score and a FLIP note on the candle where the side changes. Neutral until enough history exists for the trail.',
        'HA+UT': 'Bullish while the Heikin-Ashi close is above its trailing stop and bearish while it is below; the reason shows the trail level and a FLIP note on the candle where the side changes. Neutral until enough history exists for the trail.',
        'ATR Volatility': 'ATR never votes: it measures how far price moves, not which way. The card reports whether volatility is expanding (ATR above the baseline by the Expansion Threshold) or normal, and shows the expected next-bar range. Use it for stops, sizing and as a filter beside directional indicators.',
        'Stochastic': 'Bullish when %K crosses above %D while in the oversold zone; bearish when %K crosses below %D while in the overbought zone. Crosses in the middle of the range are neutral (set Cross In Zone Only to 0 for the older, looser rule).',
        'Stochastic RSI': 'Bullish when %K crosses above %D while in the oversold zone; bearish for the mirror-image cross in the overbought zone. Mid-range crosses are neutral. It is fast and overlaps with RSI, so treat it as timing, not as independent evidence.',
        'CCI': 'Below the negative threshold and rising is bullish; above the positive threshold and falling is bearish; otherwise neutral.',
        'Williams %R': 'Below Oversold and rising is bullish; above Overbought and falling is bearish; otherwise neutral.',
        'Rate of Change': 'Above the dead band is bullish, below the negative dead band is bearish, otherwise neutral. The dead band is 0% by default (the zero line).',
        'Momentum': 'Momentum above zero (price above its close N bars ago) is bullish, below zero is bearish, and exactly zero is neutral.',
        'Money Flow Index': 'Below Oversold and rising is bullish; above Overbought and falling is bearish; otherwise neutral.',
        'Ultimate Oscillator': 'Votes on the exhaustion turn, like the other bounded oscillators: at or below the oversold level and rising is bullish; at or above the overbought level and falling is bearish; everywhere else is neutral.',
        'Awesome Oscillator': 'Above zero is bullish, below zero is bearish, and zero is neutral.',
        'Chande Momentum Oscillator': 'Votes on the exhaustion turn: at or below the negative threshold and rising is bullish; at or above the positive threshold and falling is bearish; everywhere else is neutral. No reading is shown until a full window of changes exists.',
        'True Strength Index': 'TSI above its signal line is bullish; below is bearish; equal is neutral.',
        'TRIX': 'TRIX above its signal line is bullish; below is bearish; equal is neutral.',
        'Know Sure Thing': 'KST above its signal line is bullish; below is bearish; equal is neutral.',
        'Percentage Price Oscillator': 'A positive histogram is bullish, a negative histogram is bearish, and zero is neutral.',
        'Aroon': 'The dominant line must also reach the Trend Threshold: Aroon Up is bullish and Aroon Down is bearish; otherwise neutral.',
        'Vortex Indicator': 'VI+ above VI− is bullish; VI− above VI+ is bearish; equal is neutral.',
        'Ichimoku Cloud': 'Bullish needs all four: price above the cloud drawn at this bar (the spans from one base period ago), Conversion above Base, the projected future cloud bullish (Span A above Span B), and today\'s close above the close one base period ago (Chikou clear). The exact inverse is bearish; anything else is neutral.',
        'Parabolic SAR': 'Price above the SAR dots is bullish; price below is bearish. Follows Wilder rules: the SAR never enters the prior two bars range and flips when price touches it.',
        'Donchian Channels': 'A close above the prior rolling upper channel is bullish; below the prior lower channel is bearish; inside is neutral.',
        'Keltner Channels': 'Bullish when the completed candle closes above the upper band; bearish when it closes below the lower band; neutral inside the channel.',
        'Choppiness Index': 'Only a reading below Trending Below can vote. Then a close above the close one period ago is bullish and below it is bearish; higher choppiness is neutral. It does not depend on any other indicator being selected.',
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
        + `<p><strong>Where it appears:</strong> ${subchartNames.has(name) ? 'A separate sub-chart below price.' : (name === 'Candle Patterns' ? 'Confirmation markers and emphasized formation candles on the main price chart.' : (name === 'Chart Patterns' ? 'Breakout markers with the pattern name on the main price chart.' : 'The main price chart.'))}</p>`
        + `<p><strong>How this app votes:</strong> ${signalGuide[name]}</p>`
        + `<p class="text-xs text-slate-500"><strong>Default settings:</strong> ${(configMap[name] || []).length ? configMap[name].map(key => `${labels[key] || key}: ${defaults[key]}`).join(' · ') : 'No adjustable calculation setting for this indicator.'}</p>`
        + `<p class="text-xs text-slate-500"><strong>Beginner note:</strong> Bullish and bearish are rule-based confluence votes, not guaranteed trades. Confirm with market structure, liquidity, and risk limits; test changes on the same ticker and timeframe before deployment.</p>`
        + `</div>`,
    ]));
    function escapeHtml(value) {
        return String(value).replace(/[&<>"']/g, char => ({
            '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;',
        })[char]);
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
            const defaultShown = new Set(
                (window.STANDARD_INDICATOR_DEFAULT_NAMES || catalog.slice(0, 15)).slice(0, 15)
            );
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

    function wireMovingAverageLineToggles(container) {
        if (!container) return;
        const pairs = [
            ['EMA_USE_SECOND_LINE', 'EMA_SLOW'],
            ['SMA_USE_SECOND_LINE', 'SMA_SECOND_PERIOD'],
        ];
        pairs.forEach(([toggleKey, secondaryKey]) => {
            const toggle = container.querySelector(`[data-key="${toggleKey}"]`);
            const secondary = container.querySelector(`[data-key="${secondaryKey}"]`);
            if (!toggle || !secondary) return;
            const sync = () => {
                secondary.disabled = !toggle.checked;
                const wrapper = secondary.closest('.flex-col');
                if (wrapper) {
                    wrapper.classList.toggle('opacity-45', !toggle.checked);
                    wrapper.classList.toggle('pointer-events-none', !toggle.checked);
                }
            };
            toggle.addEventListener('change', sync);
            sync();
        });
    }

    window.STANDARD_INDICATOR_CATALOG = Object.freeze(catalog.map(item => Object.freeze({ ...item })));
    window.STANDARD_INDICATOR_NAMES = Object.freeze(catalog.map(item => item.name));
    window.STANDARD_INDICATOR_DEFAULT_NAMES = Object.freeze([
        'RSI',
        'EMA Cross',
        'SMA',
        'VWAP',
        'ADX Trend',
        'Supertrend',
        'ATR Volatility',
        'Volume Profile',
        'Candle Patterns',
        'Prior Day High/Low',
        'Swing Points',
        'Pivot Points (S/R)',
        'MACD',
        'Bollinger',
        'Chart Patterns',
    ]);
    window.STANDARD_INDICATOR_CONFIG_MAP = Object.freeze(configMap);
    window.STANDARD_INDICATOR_DEFAULTS = Object.freeze(defaults);
    window.STANDARD_INDICATOR_PARAM_LABELS = Object.freeze(labels);
    window.STANDARD_INDICATOR_INPUT_ATTRS = Object.freeze(inputAttrs);
    window.STANDARD_INDICATOR_TOGGLE_KEYS = Object.freeze(toggleKeys);
    window.INDICATOR_GUIDES = Object.freeze(guides);
    window.ensureStandardIndicatorCards = ensureStandardIndicatorCards;
    window.wireMovingAverageLineToggles = wireMovingAverageLineToggles;

    ensureStandardIndicatorCards();
    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', ensureStandardIndicatorCards, { once: true });
    }
})();
