# BotBulls Crypto Backtest — Indicator Setup Summary

## BTCUSD

Dataset: 2026-05-21T05:44:18 → 2026-07-20T05:44:18 | Candles: 5691 | Attempted: 116 | Completed: 116 | Eligible: 70 | Rejected: 46

### #1 indicator_dynamic | indicator entry → indicator exit

- Indicators: RSI, Bollinger, AVWAP, Supertrend
- Profile: crypto_balanced | Direction: both | Buy/Sell thresholds: 3/2
- Risk: SL 4.00% | TP 8.00% | Trail 2.00% | ATR stop/trail/TP 2.5/3.5/6.0
- Controls: signal reset False | smart reentry RSI | cooldown True (60 min) | fee 0.10% entry + 0.10% exit
- Result: ROI 18.83% | Net P/L $188.25 | Trades 48 | Win 58.33% | PF 1.62 | DD 0.0% | Sharpe 2.77 | Sortino 3.81 | Avg hold 27.0h | B&H -16.99%

### #2 indicator_dynamic | indicator entry → indicator exit

- Indicators: Mystic Pulse, Candle Patterns, RSI, AVWAP
- Profile: crypto_balanced | Direction: both | Buy/Sell thresholds: 2/2
- Risk: SL 4.00% | TP 8.00% | Trail 2.00% | ATR stop/trail/TP 2.5/3.5/6.0
- Controls: signal reset True | smart reentry RSI | cooldown True (60 min) | fee 0.10% entry + 0.10% exit
- Result: ROI 13.04% | Net P/L $130.38 | Trades 91 | Win 43.96% | PF 1.27 | DD 0.0% | Sharpe 1.47 | Sortino 3.67 | Avg hold 12.8h | B&H -16.99%

### #3 indicator_dynamic | indicator entry → indicator exit

- Indicators: BotBulls1, Mystic Pulse, RSI, AVWAP
- Profile: crypto_balanced | Direction: both | Buy/Sell thresholds: 2/2
- Risk: SL 4.00% | TP 8.00% | Trail 2.00% | ATR stop/trail/TP 2.5/3.5/6.0
- Controls: signal reset False | smart reentry RSI | cooldown True (60 min) | fee 0.10% entry + 0.10% exit
- Result: ROI 11.64% | Net P/L $116.43 | Trades 96 | Win 43.75% | PF 1.23 | DD 0.0% | Sharpe 1.26 | Sortino 3.07 | Avg hold 12.0h | B&H -16.99%

### #4 prior_high_low | high_low entry → sltp exit

- Indicators: BotBulls3, AVWAP
- Profile: crypto_runner | Direction: both | Buy/Sell thresholds: 2/2
- Risk: SL 6.00% | TP 15.00% | Trail 3.50% | ATR stop/trail/TP 3.5/5.0/8.0
- Controls: signal reset True | smart reentry AVWAP | cooldown False (None min) | fee 0.10% entry + 0.10% exit
- Result: ROI 6.76% | Net P/L $67.65 | Trades 50 | Win 34.0% | PF 1.27 | DD 0.0% | Sharpe 1.4 | Sortino 5.08 | Avg hold 11.6h | B&H -16.95%

### #5 prior_high_low | high_low entry → sltp exit

- Indicators: RSI, Bollinger, AVWAP
- Profile: crypto_runner | Direction: both | Buy/Sell thresholds: 3/2
- Risk: SL 6.00% | TP 15.00% | Trail 3.50% | ATR stop/trail/TP 3.5/5.0/8.0
- Controls: signal reset True | smart reentry AVWAP | cooldown True (60 min) | fee 0.10% entry + 0.10% exit
- Result: ROI 6.6% | Net P/L $66.03 | Trades 48 | Win 33.33% | PF 1.27 | DD 0.0% | Sharpe 1.42 | Sortino 5.21 | Avg hold 11.9h | B&H -16.95%

## ETHUSD

Dataset: 2026-05-21T05:45:00 → 2026-07-20T05:45:00 | Candles: 5689 | Attempted: 116 | Completed: 116 | Eligible: 80 | Rejected: 36

### #1 indicator_dynamic | indicator entry → indicator exit

- Indicators: Mystic Pulse, Candle Patterns, RSI, AVWAP
- Profile: crypto_balanced | Direction: both | Buy/Sell thresholds: 2/2
- Risk: SL 4.00% | TP 8.00% | Trail 2.00% | ATR stop/trail/TP 2.5/3.5/6.0
- Controls: signal reset True | smart reentry RSI | cooldown True (60 min) | fee 0.10% entry + 0.10% exit
- Result: ROI 21.71% | Net P/L $217.08 | Trades 88 | Win 39.77% | PF 1.36 | DD 0.0% | Sharpe 1.92 | Sortino 4.35 | Avg hold 13.6h | B&H -12.91%

### #2 indicator_dynamic | indicator entry → indicator exit

- Indicators: BotBulls1, Mystic Pulse, RSI, AVWAP
- Profile: crypto_balanced | Direction: both | Buy/Sell thresholds: 2/2
- Risk: SL 4.00% | TP 8.00% | Trail 2.00% | ATR stop/trail/TP 2.5/3.5/6.0
- Controls: signal reset False | smart reentry RSI | cooldown True (60 min) | fee 0.10% entry + 0.10% exit
- Result: ROI 16.11% | Net P/L $161.08 | Trades 96 | Win 38.54% | PF 1.27 | DD 0.0% | Sharpe 1.46 | Sortino 2.99 | Avg hold 12.2h | B&H -12.91%

### #3 indicator_dynamic | indicator entry → indicator exit

- Indicators: RSI, Bollinger, AVWAP, Supertrend
- Profile: crypto_balanced | Direction: both | Buy/Sell thresholds: 3/2
- Risk: SL 4.00% | TP 8.00% | Trail 2.00% | ATR stop/trail/TP 2.5/3.5/6.0
- Controls: signal reset False | smart reentry RSI | cooldown True (60 min) | fee 0.10% entry + 0.10% exit
- Result: ROI 4.09% | Net P/L $40.88 | Trades 44 | Win 45.45% | PF 1.08 | DD 0.0% | Sharpe 0.46 | Sortino 0.59 | Avg hold 29.0h | B&H -12.91%

### #4 atr_entry | atr entry → sltp exit

- Indicators: BotBulls3, AVWAP
- Profile: crypto_runner | Direction: both | Buy/Sell thresholds: 2/2
- Risk: SL 6.00% | TP 15.00% | Trail 3.50% | ATR stop/trail/TP 3.5/5.0/8.0
- Controls: signal reset False | smart reentry AVWAP | cooldown False (None min) | fee 0.10% entry + 0.10% exit
- Result: ROI 3.22% | Net P/L $32.21 | Trades 2 | Win 50.0% | PF 7.8 | DD 0.0% | Sharpe 12.26 | Sortino 0.0 | Avg hold 14.4h | B&H -12.91%

### #5 atr_entry | atr entry → sltp exit

- Indicators: RSI, Bollinger, AVWAP
- Profile: crypto_runner | Direction: both | Buy/Sell thresholds: 2/2
- Risk: SL 6.00% | TP 15.00% | Trail 3.50% | ATR stop/trail/TP 3.5/5.0/8.0
- Controls: signal reset False | smart reentry RSI | cooldown False (None min) | fee 0.10% entry + 0.10% exit
- Result: ROI 3.22% | Net P/L $32.21 | Trades 2 | Win 50.0% | PF 7.8 | DD 0.0% | Sharpe 12.26 | Sortino 0.0 | Avg hold 14.4h | B&H -12.91%
