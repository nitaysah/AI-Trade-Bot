import yfinance as yf
import pandas as pd
import pandas_ta as ta

df = yf.download("SPY", period="5d", interval="5m", progress=False)
if df.empty:
    print("Failed to download data.")
    exit()

# Flatten MultiIndex if necessary
if isinstance(df.columns, pd.MultiIndex):
    df.columns = df.columns.get_level_values(0)

# Calculate ATR(14) just for reference
df.ta.atr(length=14, append=True)

# Supertrend 10/3.0
sti = ta.supertrend(df['High'], df['Low'], df['Close'], length=10, multiplier=3.0)
df = pd.concat([df, sti], axis=1)
# Column is typically 'SUPERTd_10_3.0' which is 1 (bullish) or -1 (bearish)

latest = df.iloc[-1]
trend_dir = latest.get('SUPERTd_10_3.0', 0)
price = latest['Close']
print(f"Current SPY 5m Price: ${price:.2f}")

if trend_dir == 1:
    print("Supertrend (10/3.0): BULLISH (BUY SIGNAL/HOLD)")
elif trend_dir == -1:
    print("Supertrend (10/3.0): BEARISH (SELL SIGNAL)")
else:
    print("Supertrend: UNKNOWN")
