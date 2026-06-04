import yfinance as yf
import pandas as pd
import pandas_ta as ta

df = yf.download("SPY", period="5d", interval="5m", progress=False)
if isinstance(df.columns, pd.MultiIndex):
    df.columns = df.columns.get_level_values(0)

# Calculate ATR(14)
df.ta.atr(length=14, append=True)
atr_col = [c for c in df.columns if 'ATR' in c][0]

latest = df.iloc[-1]
price = latest['Close']
atr = latest[atr_col]

# BotBulls3 Robinhood rules: TP = 1.0 ATR, SL = 0.5 ATR
tp_price = price + (atr * 1.0)
sl_price = price - (atr * 0.5)

print(f"PRICE={price:.2f}")
print(f"ATR={atr:.2f}")
print(f"TP={tp_price:.2f}")
print(f"SL={sl_price:.2f}")
