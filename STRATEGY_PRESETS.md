# Strategy Presets

A strategy preset is a fixed, published rule set that decides entries and exits by itself. It is independent of the Entry/Exit strategy families, the indicator boxes, their thresholds and per-indicator overrides. It is available on **Bots**, **Screener** and **Backtest** through one shared picker (`frontend/strategy-preset.js`) and one shared decision path (`backend/strategy_presets.py`).

Status: implemented and unit/integration tested with mocked brokers. **Not** validated with live orders and **not** proven profitable. See "Before any live use".

## Catalog (v1: stocks and ETFs, long only, daily bars)

| Strategy | Variants | Buy | Sell |
|---|---|---|---|
| Golden Cross (`golden_cross`) | 50/200 SMA (default), 50/200 EMA, Fast 20/50 SMA | fast average crosses above the slow one on a completed bar | fast average is below the slow one |
| Supertrend + 200 EMA (`supertrend_ema200`) | ATR 10 x 3 (default), ATR 7 x 2 | Supertrend flips bullish and the close is above the 200 EMA | Supertrend is bearish, or the close is below the 200 EMA |

Common rules:
- Signals use **completed daily candles only**. A cross is detected with a carried sign, so a touch is not a cross; one signal per cross. Entries need a **fresh** signal.
- Exits are **state based**, so they survive restarts and missed scans.
- Missing data, warmup (up to ~600 bars for the EMA 200), degraded data or an open candle give `WAITING_DATA`/`DEGRADED`: never a neutral BUY or SELL.
- Each position has a protective stop at 3 x ATR(14) below the broker average cost. The ATR is stored at entry (as a distance), so the stop stays correct across stock splits.
- The designed timeframe is 1 Day. Intraday timeframes are selectable but flagged untested: Alpaca and Webull align hourly bars differently, so the same signal could differ between backtest and live. Intraday backtests keep only regular-session bars when the Regular session is chosen.

## What the strategy sets, and what stays yours

While a strategy is on:
- Locked: Entry strategy, Exit strategy, their thresholds and the indicator boxes (values are kept and come back when the strategy is turned off).
- Trading Timeframe, Market Session and, only when the strategy defines them, **Sizing Method / Amount Per Trade** are **your own editable fields**. Turning the Strategy switch on or off never changes them.
- **Each strategy, and each sub-option (variant), has its own set.** A variant can define its own timeframe, session and trade allocation (`supported_timeframes`, `supported_sessions`, `default_timeframe`, `suggested_sizing` on the variant); anything it leaves out is inherited from the strategy. **Choosing a strategy, or a sub-option, fills the fields that set defines** and labels them purple **"Set by strategy"**. A strategy that defines no capital leaves Sizing Method and Amount alone; no amount is ever invented. In v1 neither strategy defines a capital amount; both define 1 Day and the Regular session.
- You can change any of them. A field that differs from what the strategy defines turns **amber** ("Manual · strategy: 1Day") and your value stands. Changing it back returns the purple label. The amber "untested" warning also names anything the strategy was not designed for (it still runs, but results there are not validated; for example hourly bars can differ between Alpaca backtest data and Webull live data).
- Only values the engine cannot run (24h and timeframes the data providers do not offer) are disabled, and one of them is replaced by the variant's default when the strategy is on. Turning the strategy off removes the labels and keeps whatever the fields hold; only Entry/Exit settings are restored. Server allow-list of anything a strategy may set: `timeframe`, `market_session`, `ext_hours`; the suggested sizing keys are only `allocation_mode` and `trade_amount` and are plain form input (never a risk limit), so every risk and sizing limit still applies.
- **Re-entry rules are off.** A strategy decides its own entries and exits, so while one is active **Re-Entry Cooldown, Signal Reset and Smart Re-entry are switched off and greyed out** on Bots and Backtest (the Screener's cooldown is an alert cooldown and is left alone). The server saves them as off (a client cannot turn them back on), the live engine and the backtest ignore them even for a bot saved before this rule, and a strategy bot has no signal-reset lock. Your own choices are kept in the saved backup and come back when the strategy is turned off. Post-Stop Candles, spread limits, the loss limits and every other risk guard still apply.
- Never touched: sizing method, amount, risk per trade, bot loss limit, max position size and every other risk limit. Position size is the smallest of the amount, the risk-based size (stop = 3 x ATR), the max position and available cash. **Fixed**: every buy uses exactly the Amount, whatever earlier trades made or lost (in the Backtest the caps are measured against the starting capital, so a losing streak cannot shrink later buys). **Compound**: each buy is the starting Amount plus the realized P/L so far; on Bots, editing the starting Amount restarts compounding at the new Amount, while re-saving the same Amount keeps the running value. Risk Per Trade or Max Position Size can still cap a buy below the Amount (the trade reason says which limit decided).

## Execution model (same on all three pages)

- A signal is decided on a **completed** bar and acted on at the **next session open**. Backtest fills at the next bar's open; live Bots send orders only inside the chosen session (Regular 09:30-16:00 ET by default; Extended 04:00-20:00 ET if you pick it; exchange holidays and early closes respected), never overnight.
- The backtest adds **no fees or slippage**; live fills include the opening auction and spread, so live results will be somewhat lower.
- **Scan timing (Bots).** A strategy can define its own scan timing (`scan_policy` on the strategy or on a variant); it overrides the global scan refresh for that bot. Both v1 strategies use `per_candle` on the **1 Day** timeframe: while a bot is **flat with no order pending**, it is evaluated once per completed daily candle (a candle completes 5 minutes after the close) instead of every refresh, because a daily signal cannot change in between. It evaluates again as soon as the next candle completes (the next open for the Regular session; 16:05 ET for Extended). The refresh is **not** reduced when: a position is open or an order is pending (the software protective stop and fill reconciliation need live prices, so they keep the normal refresh while the session is open), data is missing, stale or a day behind, an entry or exit was held back by a guard (it is retried), a split reconcile is pending, the scan is forced (settings saved, "scan now"), the strategy/variant/timeframe/session changed, or the timeframe is not covered (an intraday timeframe keeps the normal refresh). After a restart each bot is evaluated once before it can idle. Bots with no strategy are unaffected. The Scan Log shows "Idle · next evaluation <time>" while a bot is idle. The Screener keeps its own interval (Auto already follows the candle) and the Backtest is bar-based.
- Bots persist the signal key, ownership of the position and the entry ATR when the broker **accepts** the entry order (a live receipt is never "filled" at submission). The client order ID is stable per signal, so a retry cannot double-buy.
- Existing positions: the default leaves a pre-existing position alone; "Adopt" lets the strategy manage it (a strategy exit that is already true will sell at the next open).
- The protective stop is **software only**. It is checked each refresh against the broker's live position price and depends on the process staying up; no broker-side stop or bracket is attached. A data or persistence failure never liquidates by itself.
- Global halts, the loss circuit breaker, cooldown (your setting), concentration and position limits still apply. A halt blocks the BUY but never a strategy exit.

## Logs (Bots)

A bot running a strategy gets its own Scan Log (`backend/strategy_scan_log.py`, rendered by `frontend/strategy-log.js`), because the indicator-style log (votes, thresholds, re-entry rules) does not describe it:
- **Status:** Waiting for data (shows bars have/need), Watching, Idle (shows the next evaluation time), Entry ready, Holding, Exit signal, Blocked, Degraded, Strategy error.
- **Headline in plain words:** for example "Flat · waiting for SMA50 to cross above SMA200", or "Trend is up for 5 bars, but the bot only enters on a fresh signal".
- **The strategy's own numbers:** Golden Cross shows both averages and the gap; Supertrend shows the Supertrend line and direction, EMA200 and the close versus it. The candle date evaluated is shown.
- **Holding:** entry, current price, P/L, the protective stop price and its distance below the price, with the note that it is a software check at each refresh and not a broker order.
- **Blockers are only the real ones** (market window closed, signal already acted on, split pause, a halt, max positions). Cooldown, signal reset and smart re-entry never appear for a strategy bot.
- **Logs table:** strategy rows show an event (Entry, Strategy exit, Stop exit, Watching, Waiting data, Held back, Degraded) and the strategy's numbers instead of "0B / 0S". Identical consecutive "Watching" checks of the same bot collapse into one row with a count (x14); entries, exits, held-back and data-waiting rows are never merged.

### Bots card layout

The scan section of a Bots card follows the Screener card's style (`frontend/bots-scan-card.js`): a row of **setup badges** (one Strategy badge for a strategy bot, or an Entry badge and an Exit badge with the manual setups, plus an amber "Untested settings" chip when a strategy runs on a timeframe or session it was not designed for), a **Last scan result** header with the status and decision chips, and a collapsible panel (remembered per bot) with three sections: **Bots scan summary**, **Entry setup watch** and **Exit setup watch**. The watch that does not apply right now says why (a position is open / no open position). While the market is closed the red banner stays on top and the **last real scan result** stays visible below it instead of disappearing. A strategy bot's top row shows a single Strategy badge with its state instead of indicator-vote badges, and the generic "Forecast" boxes (which repeat the watch sections) are hidden for it. Event/news and price-watch parts of the Screener card do not apply to bots and are not shown.

### Forced scan when a bot is created or its settings are saved

Creating a bot or saving its settings runs **one evaluation of that bot right away** (`_run_forced_bot_scan` in `backend/main.py`): the same evaluation the trading loop uses (live candles, the bot's settings, fresh data), with a 20-second limit (10 seconds for the account call). It is **analysis only**: it never places, cancels or changes an order, and it runs whether the market is open or closed. The result is stored on the card tagged **"Forced scan · created"** or **"Forced scan · settings changed"** with the time. If it fails or times out, the card shows **"Initial scan failed, will retry"** (a newer real scan is never overwritten by the failure). Afterwards the trading loop is asked to evaluate that bot on its **very next pass**, ignoring the refresh interval, the market-closed pause and a strategy's once-per-candle idling (`UserEngine.request_forced_scan`); orders are still decided only by the normal trading rules (trading window, strategy gates, risk limits). Bots read live candles from Webull and place orders on Alpaca; the forced scan reads the same candles and does not order.

### Order execution (Bots, both strategy and manual Entry/Exit)

Bots read live candles from Webull and place orders on Alpaca (paper by default).
- **Fills are confirmed, then booked once.** A market order is normally `accepted` at submission. The engine waits up to 4 seconds (without blocking) for the fill; if it has not arrived the order is saved as *pending* with the bot (it survives a restart) and is booked on a later pass from the broker's real fill price and quantity. Booking covers trade counts and the max-trades limit, realized P/L, compounding, the daily and consecutive-loss limits, the post-stop state, cooldown and signal reset. An order cancelled, expired or rejected without a fill changes nothing (a strategy entry may then act on the same signal again). Until booked, the card shows "⏳ order accepted".
- **Statuses** from the Alpaca SDK are normalized (`OrderStatus.FILLED` -> `filled`).
- **Rejections vs unknown outcomes.** A definite broker rejection (4xx) lets the next attempt use a new client order id; a timeout keeps the same id so a retry can never double-order; a reused id returns the order that already exists.
- **Exits use the live price.** Stops, targets, trailing stops and the strategy's protective stop are checked against the broker's live position price each refresh, not the last completed candle's close. Entry and exit *signals* still use completed candles only.
- **Trailing stops are saved** with the bot and restored after a restart for the same position (a different position never inherits them).
- **Extended session:** during regular hours the bot trades with normal market orders. Outside regular hours Alpaca accepts only limit orders (DAY, `extended_hours`), never OCO/bracket orders, so the bot sends a marketable limit priced from the live quote (ask +0.2% to buy, bid -0.2% to sell). Fractional quantities are allowed there: a fractionable stock buys the fractional amount and a fractional position is sold in full; non-fractionable stocks use whole shares. A close that fills only part of a position is booked as a partial sale, not a completed trade.
- **Non-fractionable stocks** are bought in whole shares (the order is refused with a clear message if the amount buys less than one share).
- **Broker OCO** exit orders are not attempted for fractional positions (Alpaca does not accept them); the watcher protects those positions against the live price.
- **After a server restart** every user's trading loop with active bots resumes at boot, without waiting for the user to open the app; each pass re-reads broker positions and open orders and settles pending fills first.

## Screener

Alerts only; it never places orders. It publishes the strategy **state** (long regime = BULLISH → "BUY signal", flat after an exit = BEARISH → "SELL (exit) signal"). The first evaluation of a new profile is recorded silently unless the signal just happened on the latest completed candle; only a change of state alerts. Preset profiles default to the Technical screening policy (Combined would also require a news catalyst).

## Backtest

- Data source: Alpaca first (the user's linked keys, then the server's keys), Yahoo as the fallback. The results show the source, feed (SIP/IEX) and split adjustment.
- Results show **every variant side by side** on the same data, plus buy and hold, exposure and a warning under 30 trades. Fixed rule sets are never optimized (the optimizer returns 400).
- Preset runs are refused if the split adjustment cannot be verified.
- Candle cache: a backtest reuses cached candles when they cover the requested range, so the same data is not downloaded twice; on a miss it downloads from Alpaca first and falls back to Yahoo. The trash icon in the Target Ticker box (`frontend/backtest-cache.js`) shows what is cached for the ticker and purges it (`GET /api/backtest/cache`, `POST /api/backtest/cache/purge`). The cache holds public candles shared by all users, so a purge only costs the next run a download.
- The Backtest page has no Risk Per Trade / Max Position Size controls: stock backtests run with risk management off, so each buy uses the full Amount (Fixed) or the compounded balance (Compound), with no volatility size cut. The loss-circuit, cooldown, post-stop and liquidity entry guards are separate and still apply. Bots keep all their risk limits.
- Entry guards (cooldown, loss circuit, post-stop stabilization, liquidity) judge the **completed signal bar**, never the bar the order fills on, and the post-stop VWAP/AVWAP reclaim uses real VWAP/Auto-AVWAP values. Validated by `scratch/validate_golden_cross_spy_5min.py`, which re-implements Golden Cross 50/200 in plain pandas and matches the engine trade for trade on cached SPY 5Min data.

## Stock splits and reverse splits (app wide)

- Prices are **split adjusted** on every path (dividends are not added back). Alpaca is asked for `adjustment=split`, Yahoo is requested with `auto_adjust=False`, and raw-versus-adjusted is verified against known split events so nothing is adjusted twice.
- Cached history is tagged with its adjustment basis; legacy untagged files are ignored and a cache fetched before a split is refetched.
- On a split day a bot pauses new entries, rescales saved price levels (custom prices, grid anchors, stored stop ATR) and share quantities by the ratio, checks the broker quantity (one share of tolerance for cash in lieu) and resumes. A mismatch leaves entries paused and retries each scan. The result is written to the bot's trade log ("Corporate Action"). Bots have no email/SMS outbox, so there is no separate notification.

## Adding a strategy

Add one entry to `PRESETS` in `backend/strategy_presets.py` (variants, supported timeframes, allow-listed overrides, rules text) and a branch in `compute_strategy_frame`. The catalog endpoint (`GET /api/strategy-presets`) feeds every page, so no frontend change is needed. Bump `CATALOG_VERSION` when rules change: bots created under an older version pause new entries until re-saved.

## Tests

Run from the repo root with Python 3.13 and `backend/requirements.txt` installed (the system Python 3.9 cannot import `main` and fails 13 unrelated tests):

```
python backend/run_isolated_tests.py "test_*preset*.py"        # strategy tests
python backend/run_isolated_tests.py "test_corporate_actions*.py"  # splits (also test_live_split_adjustment.py, test_data_manager_splits.py)
python backend/run_isolated_tests.py                              # whole suite
```

Coverage: pure strategy math (crossovers, warmup, no lookahead, completed bars, execution windows, sizing), settings apply/restore, split adjustment and split-day reconcile, Backtester fills/stops/gaps, live decision parity with the backtest, the real `trading_loop` with a mocked broker (entry once, duplicate signal, protective stop, strategy exit, unmanaged vs adopted, outside session, halt, stale data, split day), Screener alerts, and the API layer. The UI was exercised in headless Chrome on a harness with the real module (phone and desktop widths).

## Known limitations

- The stop is software only; the backtest has no fees or slippage; Golden Cross is a drawdown-reduction overlay and usually lags buy and hold in strong bull markets.
- Crypto, options and short selling are not supported; intraday timeframes and the Extended session run but are untested.
- The Bots/Screener chart preview does not draw strategy lines yet.
- `strategy_preset` live behaviour has not been run against a real broker. The EMA seed differs slightly between a live window and a backtest window (limited by the 3x warmup).

## Before any live use

Out-of-sample backtests of every variant on several liquid tickers and regimes against buy and hold (return divided by max drawdown), then at least four weeks of paper forward testing. A variant that does not beat buy and hold on that measure out of sample is rejected, not tuned. Live activation requires your explicit authorization.

## Malik leveraged-ETF allocators (Malik QQQ, Malik SOXX, Malik IWM)

**Research status:** none of the 12 pair/direction combinations passed the pre-registered research gate (2026-10-09; `scratch/malik_research/`, report https://claude.ai/artifact/Cgabc1xqjreDyxxnsETHPZ). Out of sample (2019–2026, long-only, 3x, 15 min before the close): QQQ 34.1%/yr with a −48.6% worst drawdown; SOXX 27.9%/yr, −72.0%; IWM −4.2%/yr, −69.8%. A simple 250-day rule matched or beat the 5-regime system. Inverse ETFs lowered results in every pair. The strategies are offered for research and paper trading with that warning shown everywhere.

**What they do.** The bot is created on the benchmark (QQQ, SOXX or IWM) and never trades it. Once a day at the execution time the benchmark's regime is decided from completed daily closes through yesterday plus the current price, and the chosen ETF legs are rebalanced to a target % of the bot's sleeve:

| Regime | Condition | Long-only | Long + inverse |
|---|---|---|---|
| Bull rider | above the 250- and 50-day averages | 100% bull ETF | same |
| Momentum trim | above both, 10-day change < −2% and acceleration < −1.5 | 30% | same |
| Consolidation | above the 250-day, at/below the 50-day, 10-day change ≥ −1% | 50% | same |
| Defensive | as above, 10-day change < −1% | 20% | same |
| Bear crash | at/below the 250-day and below the 50-day, 10-day change < −1.5% | cash | 60% inverse ETF |
| Bear chop | as above, 10-day change ≥ −1.5% | cash | 20% inverse ETF |
| Cash | at/below the 250-day, at/above the 50-day | cash | cash |

- **ETF choices:** QQQ: TQQQ or QLD / SQQQ or QID. SOXX: SOXL / SOXS or SSG. IWM: TNA or UWM / TZA or TWM. `USD` (2x semis) is not offered because the app treats tickers ending in "USD" as crypto in 8 places.
- **Execution time:** 15 minutes before the close (default; 12:45 ET on early-close days), any 15-minute mark from 9:45 to 15:45 ET, or a custom ET time between 9:35 and 15:55. A time after an early close runs at close − 15.
- **Rebalancing:** a non-zero leg trades only when it is more than 5% of the sleeve off target; a 0% leg is closed in full. Sells for every Malik bot run before any buy; buys wait until the sells have filled. Sleeve: Fixed = the bot's amount (gains above it are trimmed back); Compound = amount + realized + unrealized P/L.
- **Shared code:** `backend/allocation_strategy.py` (rules, sizing, simulation) is used by Backtest (`Backtester._run_allocation_strategy`), Screener and Bots (`trader._allocation_preset_info`, `backend/allocation_executor.py`).

**Live bots (`allocation_executor.py`).**
- Runs from the engine loop; the loop sleep is capped so the decision happens at the execution time even with a long scan interval. A durable `allocation_state` (server-owned) keeps the day marker, the ledger of owned shares per leg and pending orders, so a restart neither repeats a decision nor resubmits an order.
- Decision data: Webull completed daily closes (stale or degraded → hold and retry every 30 s until 5 minutes before the close, then log "No rebalance today") and a fresh quote cross-checked with Alpaca (sources more than 0.5% apart → hold).
- Orders: regular-hours market orders with stable client order IDs, fill confirmation, per-leg pending reconciliation, rejection handling. Nothing is ever placed for the benchmark ticker.
- Limits: Max Position Size and buying power cap every buy (logged "capped by …"). A global halt, a paused bot or the bot's daily loss limit block increases; reductions still run. Nothing is liquidated because of a data problem.
- Ownership: the bot trades only the shares it bought. If the broker shows fewer (you sold some, a reverse split), the ledger follows the broker after the shortfall persists for a minute; extra shares you hold are left alone unless the position policy is "adopt" (taken over once).
- Removing the bot with "liquidate" sells the ETF legs it owns (confirmed fills), not the benchmark.
- **No stop orders.** The strategy acts once a day, so it depends on the app running at the execution time; a 3x ETF can lose more than 30% in a day.
- Each bot is independent. QQQ, SOXX and IWM move together, so the combined exposure of several Malik bots is yours to size.

**Screener.** Shows the confirmed regime (last completed close) and, during the session, the regime the live price points to. Alerts fire once per confirmed regime change ("Momentum trim · target 30% TQQQ"), never as BUY/SELL, and never place orders.

**Backtest.** Decides at the execution time from the benchmark's last completed 15-minute bar plus completed daily closes, fills at the next 15-minute bar's open, marks at the daily close. Reports both directions and three benchmarks (buy & hold of the index and of the bull ETF, and the 250-day rule). Custom times are simulated on the 15-minute grid. If intraday bars are unavailable it falls back to deciding on the close and filling at the next open, and says so. No fees, slippage or dividends.

## Session Rider (time-of-day schedule)

One preset (`session_rider`, kind `schedule`; rules in `backend/session_schedule.py`). It buys the bot's own ticker at the **Buy time** and sells it at the **Sell time**. There are no indicators.

- **Times (ET):** Open + 1…5 min, the 15-minute grid 9:45–15:45, Close − 5…1 min, or a custom minute from 9:31 to 15:59. Defaults: buy Close − 5 (3:55 PM ET / 2:55 PM CT), sell Open + 5 (9:35 AM ET / 8:35 AM CT). The 1–4 minute options are marked "tighter timing": they lead to more missed buys and wider spreads.
- **Mode:** a buy earlier than the sell is **intraday** (flat overnight). A buy later than the sell is **overnight**: it sells at the Sell time of the next trading day, holding over weekends and holidays unless "Skip buys before weekends and holidays" is on. Equal times are rejected.
- **Early closes:** the Open + N and Close − N anchors follow the real close (Close − 1 = 12:59 on a 1:00 PM close). A clock time later than close − 5 runs at close − 5. An intraday day whose buy is no longer earlier than its sell is skipped.
- **Orders:** regular-hours market orders only, with no auction orders in this version. Fractional shares are used where the ticker allows them. The bot sells its whole position, the same as every other bot.

**Bots** (`backend/session_executor.py`, run before the per-ticker loop, which skips these bots):
- **Buys** happen only inside the 5 minutes after the Buy time, never later than 30 seconds before the close, and only in a window that opened after the bot was saved or re-enabled. A missed window is skipped (MISSED_ENTRY), never bought late. The engine wakes 20 seconds early and sends the order at the Buy time.
- **What skips a buy (SKIPPED, with the reason):**
  - a pause, a global halt or a daily loss limit;
  - a split being reconciled;
  - the max-open-positions limit;
  - the market being closed (the NYSE calendar plus Alpaca's clock);
  - a stale quote;
  - a position you hold yourself;
- **Sells** are never skipped. Once the Sell time has passed and the market is open, the position is sold, even if the bot is paused or halted. A late sell is logged EXIT_LATE. A rejected sell is retried with a new client order ID (EXIT_RETRY), and the card shows "Exit overdue".
- **Fills** are booked through the engine's normal bookkeeping (trade history, realized P/L, compound amount, daily loss). The session marker is saved before the order is sent, and client order IDs are fixed per session and side, so a restart cannot buy twice. Splits are reconciled for the bot's ticker.
- **No stop-loss, take-profit or trailing exits.** Those Exit controls are greyed out. Overnight gaps are fully exposed. The sell depends on this app running: it is not a broker order.
- **Loss-streak pause:** set per bot ("Pause after losses in a row", default 3, minimum 3, maximum 50). After that many losing trades in a row the bot pauses and waits for your review.

**Screener:** shows where the schedule is ("flat · buy 3:55 PM ET" or "overnight window · sell 9:35 AM ET"). This is the schedule, not your positions. It alerts once at each Buy and Sell time, including before the open; an event more than 10 minutes old is not alerted. Alerts ignore the bullish/bearish filters and obey their own on/off switch (`session_alerts_enabled`). No candles are fetched, no broker is called, and nothing depends on the Bots engine.

**Backtest:** fills at the open of the 5-minute bar that starts at each time (1-minute bars for times off the 5-minute grid). If that bar is missing, it uses the next bar within 5 minutes; otherwise the day is skipped and counted. It needs Alpaca intraday data (from 2016) and refuses a Yahoo fallback, which has only 7–60 days of intraday bars.

The report shows:
- the schedule against buy-and-hold and buy-and-hold's overnight and intraday parts;
- returns by year;
- a cost table at 2, 5 and 10 bps per side (not in the headline);
- the number of round trips;
- "the live bot would have paused N times".

The backtest itself never pauses.

**Known biases:** prices are split-adjusted only. Overnight results are understated, because the ex-dividend drop happens overnight and the dividend is not credited. The first and last minutes of the session are noisy, so live fills can differ from the bar prices.
