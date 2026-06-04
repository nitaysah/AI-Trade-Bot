import sys
import asyncio
from indicators import get_full_analysis
from trader import get_confluence_decision

def main():
    ticker = "SPY"
    timeframe = "5m"
    
    print(f"Fetching Live Data for {ticker} ({timeframe})...")
    analysis = get_full_analysis(ticker, timeframe=timeframe, data_source="yfinance")
    if not analysis:
        print("Failed to get analysis.")
        return
        
    print(f"Current Price: {analysis.get('price')}")
    signals = analysis.get("signals", {})
    
    print("--- Indicators ---")
    print(f"Supertrend (10/3.0): {signals.get('Supertrend (10/3.0)')}")
    print(f"EMA Trend: {signals.get('EMA Trend')}")
    print(f"MACD: {signals.get('MACD')}")
    print(f"RSI: {analysis.get('rsi')}")
    
    decision = get_confluence_decision(ticker, analysis, use_bot_settings=True)
    print("\n--- Bot Decision ---")
    print(f"Action: {decision.get('action')}")
    print(f"Reason: {decision.get('reason')}")

if __name__ == "__main__":
    main()
