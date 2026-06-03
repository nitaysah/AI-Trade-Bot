import sys
import os
import datetime
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from indicators import get_full_analysis

# Update to match user's current chart state
ticker = "TSLA"
timeframe = "1Hour"

analysis = get_full_analysis(ticker, timeframe, "webull")
bars = analysis["price_history"]

signals = []
prev_above = None

capital = 1000.0
shares = 0.0
in_position = False
trades_taken = 0

md_lines = []
md_lines.append(f"# {ticker} {timeframe} BotBulls3 Signal History")
md_lines.append("This document tracks every single BotBulls3 (UT Bot) buy and sell signal for the last 1,200 candles on the 1-Hour timeframe, simulating a $1,000 initial investment.")
md_lines.append("")
md_lines.append("## Trade Log")
md_lines.append("| Date (UTC) | Signal | Price | Capital/Shares |")
md_lines.append("|---|---|---|---|")

for bar in bars:
    time_str = datetime.datetime.fromtimestamp(bar['time'], tz=datetime.timezone.utc).strftime('%Y-%m-%d %H:%M')
    is_above = bar.get('ut_above', True)
    
    if prev_above is not None and is_above != prev_above:
        signal_type = "BUY" if is_above else "SELL"
        price = bar['close']
        
        if signal_type == "BUY" and not in_position:
            shares = capital / price
            capital = 0.0
            in_position = True
            trades_taken += 1
            md_lines.append(f"| {time_str} | 🟢 **BUY** | ${price:.2f} | Bought {shares:.4f} shares |")
            
        elif signal_type == "SELL" and in_position:
            capital = shares * price
            shares = 0.0
            in_position = False
            trades_taken += 1
            md_lines.append(f"| {time_str} | 🔴 **SELL** | ${price:.2f} | **${capital:.2f}** |")
    
    prev_above = is_above

final_price = bars[-1]['close']
if in_position:
    final_portfolio_value = shares * final_price
else:
    final_portfolio_value = capital

profit_pct = ((final_portfolio_value - 1000.0) / 1000.0) * 100

md_lines.insert(3, "## Final Performance")
md_lines.insert(4, f"- **Starting Capital:** $1,000.00")
md_lines.insert(5, f"- **Ending Portfolio Value:** **${final_portfolio_value:.2f}**")
md_lines.insert(6, f"- **Net Return:** {profit_pct:+.2f}%")
md_lines.insert(7, f"- **Total Trades Executed:** {trades_taken}")
md_lines.insert(8, f"- **Currently Holding:** {'Yes' if in_position else 'No (Cash)'}")
md_lines.insert(9, "")

artifact_path = "/Users/nitaysah/.gemini/antigravity-ide/brain/14ababac-c92f-49e8-8fd2-fc1bdd26f4f4/signal_history.md"
with open(artifact_path, "w") as f:
    f.write("\n".join(md_lines))

print(f"Artifact created at {artifact_path}")

