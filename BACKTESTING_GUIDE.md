# Bot Bulls — Advanced Backtesting & Optimization Guide

Welcome to the Bot Bulls Backtesting Center guide. This document explains the underlying logic of the backtesting engine, how to run simulations, how to use the Auto-Optimizer to prevent overfitting, and best practices for achieving robust trading results before deploying live capital.

---

## 1. Backtesting Engine Logic & Architecture

The backtesting engine is designed to accurately simulate historical market conditions using your custom algorithmic parameters.

### Core Components
- **Data Ingestion**: Fetches historical OHLCV data using Yahoo Finance (yfinance) and optionally Alpaca's historical API. Data is cleaned and filtered for Regular Trading Hours (RTH) if extended hours are disabled.
- **Indicator Engine**: Calculates complex technical indicators (RSI, MACD, EMA Cross, Supertrend, Bollinger Bands, VWAP, etc.) across the entire historical timeframe to prevent iterative lag.
- **Signal Aggregation**: Each bar evaluates whether the enabled indicators agree on a bullish or bearish signal. 
- **Execution Logic**: 
  - If `bullish_signals >= threshold`, a **BUY** order is executed.
  - If `bearish_signals >= sell_threshold` (or Stop-Loss/Take-Profit triggers), a **SELL** order is executed.

### Risk Controls
- **ATR-Based Stop Loss & Take Profit**: Volatility-adjusted stops using the Average True Range (ATR). Instead of a fixed percentage, the stop loss widens in volatile markets and tightens in quiet markets.
- **Risk Per Trade**: Limits the maximum capital exposed on any single trade (e.g., max 2% of total capital).
- **Max Position Size**: Prevents allocating more than a set percentage (e.g., 25%) of the total portfolio into a single asset.

---

## 2. Auto-Optimization & Preventing Overfitting

A common trap in algorithmic trading is "Curve Fitting"—tweaking parameters until the backtest looks perfect, only to watch the strategy fail in live markets. To solve this, Bot Bulls includes an **Auto-Optimizer** using Grid Search and Out-of-Sample (OOS) validation.

### How it Works
1. **Grid Search**: The engine tests hundreds of combinations of parameters (Signal Thresholds, Sell Thresholds, ATR Stop Multipliers, Take Profit Multipliers) simultaneously.
2. **Data Splitting**: The historical data is split into two parts:
   - **In-Sample Data (e.g., 70%)**: Used by the engine to "train" and find the parameter set with the highest Sharpe Ratio.
   - **Out-of-Sample Data (e.g., 30%)**: Unseen "blind" data. The best parameters from the In-Sample phase are tested strictly on this blind data.
3. **Overfit Score**: If a strategy performs exceptionally well In-Sample but fails Out-of-Sample, it is tagged as **🔴 OVERFITTED**. If it maintains its edge, it is tagged as **🟢 ROBUST**.

---

## 3. How to Run a Simulation (User Guide)

### Step 1: Configure Asset & Timeframe
- Open the **Backtest Center** (`http://localhost:3000/backtest.html`).
- **Ticker**: Enter the asset you wish to trade (e.g., `AAPL`, `SPY`, `BTC-USD`).
- **Timeframe & Days**: Select your interval (1Min to 1Day). *Note: Shorter timeframes (like 1Min) are restricted to 7 days of historical data by Yahoo Finance.*

### Step 2: Select Trading Mode
- **Regular Trading**: Trades the underlying stock or crypto asset.
- **Options Trading**: Simulates buying Calls or Puts based on the underlying stock's signals. You can configure Expiry Days, Strike Placement (ITM, ATM, OTM), and Smart Re-entry logic (e.g., buy Call when RSI drops below 30).
- **AI Autopilot**: Delegates the buy/sell decisions entirely to an LLM (e.g., Llama 3) based on raw indicator data.

### Step 3: Choose Indicators & Exits
- Select the indicators to form your **Strategy Matrix** (e.g., MACD + Supertrend).
- Choose your **Exit Strategy**:
  - *Dynamic*: Sells when indicators flip bearish.
  - *Fixed*: Strict Stop-Loss and Take-Profit based on ATR multipliers.
  - *Hybrid*: Whichever triggers first.

### Step 4: Run Auto-Optimizer (Highly Recommended)
- Toggle **"Auto-Optimizer (Prevent Overfitting)"** ON.
- Set the **Out-of-Sample Split** (30% is standard).
- Click **Run Strategy Alpha Simulation**. 
- *Note: Optimization takes slightly longer because it runs hundreds of concurrent backtests in the background.*

---

## 4. Advanced Risk Metrics Explained

Once the simulation finishes, the results modal displays institutional-grade metrics:

- **Sharpe Ratio**: Measures risk-adjusted return. A Sharpe > 1.0 is considered good; > 2.0 is excellent.
- **Profit Factor**: Gross Profit divided by Gross Loss. A value > 1.5 indicates a highly sustainable strategy.
- **Max Drawdown**: The largest peak-to-trough drop in account equity. Crucial for understanding emotional pain tolerance.
- **Win/Loss Ratio**: Average profit of winning trades divided by average loss of losing trades.
- **Monte Carlo Simulation**: Click the Monte Carlo button to run 1,000 randomized permutations of your exact trades. This generates a "Fan Chart" showing the statistical probability of going broke versus becoming highly profitable over time.

---

## 5. How to Get the Best Backtesting Results (Best Practices)

1. **Avoid Over-complication**: Selecting 8 different indicators doesn't guarantee a better strategy; it often causes "Signal Starvation" (the bot never trades because the indicators rarely align perfectly). Stick to 2-4 complementary indicators (e.g., one trend, one momentum, one volume).
2. **Prioritize Sharpe over ROI**: A strategy with 500% ROI but an 80% Max Drawdown is a gamble. Aim for the highest Sharpe Ratio to ensure consistent, compounded growth with minimal drawdowns.
3. **Always Validate Out-of-Sample**: Never deploy a bot based purely on a standard backtest. Always use the Auto-Optimizer. If the Overfit Badge says **🔴 OVERFITTED**, the strategy relies on luck for that specific time period. Do not deploy it.
4. **Test in the Incubator Sandbox**: Once you find a **🟢 ROBUST** strategy, click **"To Incubator"**. This deploys the bot to the Active Bots dashboard in a live, forward-testing paper environment. Monitor it for a week before allocating real capital.
5. **Contextualize the Market**: A strategy tested purely during a raging bull market (e.g., 2021) will fail in a bear market. Backtest across different macroeconomic regimes (extend your "Simulation Days" to cover both crashes and rallies).
