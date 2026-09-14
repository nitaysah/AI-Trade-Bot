# BotBulls Quant AI Indicator

BotBulls Quant AI is a hybrid decision-support indicator shared by Dashboard, Bots, and Backtest. Deterministic code owns the final BUY, SELL, or HOLD decision. The selected Groq or Ollama model explains the result but cannot override the canonical setup score, signal, entry, stop, target, or reward/risk.

## Decision process

The quantitative layer evaluates independent evidence families so correlated indicators are not counted repeatedly:

- Trend structure: EMA 9/21/50 and SMA 200 alignment
- Momentum: MACD histogram/crossover and RSI condition
- Price structure: break or hold of prior-candle support/resistance
- Participation: volume relative to its 20-candle average
- Context: broad-market and sector trend
- News: a small confirmation weight only
- Data quality: required-field completeness

BUY or SELL requires the configured minimum setup score, at least two confirmations, sufficient data quality, and the configured minimum reward/risk. BUY also observes the maximum entry RSI and optional volume requirement. HOLD means one or more gates did not pass; the card shows the exact missing condition.

BUY sizing is capped by conviction, available cash, maximum portfolio allocation, and the account risk budget divided by stop distance. This prevents the narrative model from selecting arbitrary position size.

The setup score is a rule-based setup-quality measure. It is not a forecast probability or a guarantee that a trade will be profitable.

## Decision timeframe

The settings button on the card supports 1Min, 5Min, 15Min, 30Min, 1Hour, 4Hour, and 1Day decision candles. Only completed candles are used. Dashboard, Bots, and Backtest persist separate settings and caches.

News sentiment intentionally has no candle timeframe. Dashboard and Bots cache it by ticker and selected AI model, then reuse it as limited confirmation across decision intervals. An enabled Screener profile separately checks Yahoo Finance headline metadata every 60 seconds, uses Google News RSS when Yahoo coverage is insufficient, and reruns AI classification only when the article set changes. Historical backtest execution uses each replay bar's actual strategy timeframe and does not receive live news, preventing timeframe relabeling and look-ahead.

## Settings

- AI model: Llama 3.3 70B (recommended), Llama 3.1 8B (fast), or an available local Ollama model
- Minimum setup score: 50–95; default 70
- Minimum reward/risk: 1.0–5.0; default 1.5
- Maximum RSI for a new BUY: 55–85; default 72
- Minimum volume ratio: 0–5x; default 0.80x
- Require volume confirmation: default off
- News conflict handling: block conflicting new entries or information-only
- Analysis cache: 1–120 minutes; default 30

Saving settings clears the page-specific technical decision cache and immediately recalculates the active ticker. The Screener profile setting `Ticker News Watch` controls its independent 60-second watcher. Seen article identities and the last valid four-article summary are stored in local SQLite so a backend restart does not mark old headlines as new. The Quant decision cache does not delay this watcher.

The AI model is selected exclusively from the settings modal to keep the card header uncluttered. Model choices are persisted independently for Dashboard, Bots, and Backtest, just like the remaining Quant AI settings.

## Card layout

The first view prioritizes execution information: final decision, setup score and threshold, technical/news bias, volatility, data quality, current price, stop/invalidation, target, reward/risk, and the next testable trigger. Supporting evidence, opposing evidence, and cautions are shown in separate columns.

The expanded stock-analysis section provides six auditable technical views (trend, momentum, price structure, volume, volatility, and market/sector context), a gate-by-gate pass/block table, ticker-news coverage quality, and separate risk/invalidation and trade-management plans. News analysis uses directly attributable, recent article evidence with title, publisher, publication time, link, and a bounded public metadata preview when available. The AI model selected in settings classifies each article's ticker relevance, bullish/bearish/neutral tone, impact level, and factual rationale before a relevance-weighted aggregation. Incidental articles are visibly marked and excluded. The interface explicitly distinguishes this evidence from unavailable or paywalled full article bodies. Neutral technical bias uses the stronger directional score consistently in both the displayed setup score and failed-gate explanation.

## Safety boundaries

For BUY, the backend requires `stop_loss < entry_price < sell_target`. For SELL, it requires `sell_target < entry_price < stop_loss`. News never creates a trade by itself. LLM output is retained as diagnostic metadata, while executable fields come from the deterministic assessment.
