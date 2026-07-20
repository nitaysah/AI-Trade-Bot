import re

best_roi = -999
best_trades = 0

with open("backend/optimizer_output.txt", "r") as f:
    for line in f:
        if "[backtest] ROI:" in line:
            # [backtest] ROI: -17.09% | Trades: 77 | Win Rate: 16.88%
            match = re.search(r"ROI: ([-0-9.]+)% \| Trades: ([0-9]+)", line)
            if match:
                roi = float(match.group(1))
                trades = int(match.group(2))
                if roi > best_roi:
                    best_roi = roi
                    best_trades = trades

print(f"Best ROI seen so far: {best_roi}% with {best_trades} trades")
