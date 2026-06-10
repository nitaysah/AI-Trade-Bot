import yfinance as yf
import time
import sys
import datetime
import pytz
import math

STRIKE = 745.0
EXPIRY = "2026-06-05"
TP_SPY = 745.20
SL_SPY = 743.00
TP_OPT = 1.85
SL_OPT = 0.60

def norm_cdf(x):
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))

def bs_call(S, K, T, r, sigma):
    if T <= 0:
        return max(0.0, S - K)
    if sigma <= 0:
        return max(0.0, S - K)
    d1 = (math.log(S / K) + (r + 0.5 * sigma ** 2) * T) / (sigma * math.sqrt(T))
    d2 = (math.log(S / K) + (r - 0.5 * sigma ** 2) * T) / (sigma * math.sqrt(T))
    return (S * norm_cdf(d1) - K * math.exp(-r * T) * norm_cdf(d2))

def find_iv(S, K, T, r, target_price):
    if target_price <= 0:
        return 0.1
    low = 0.001
    high = 3.0
    for _ in range(40):
        mid = (low + high) / 2.0
        price = bs_call(S, K, T, r, mid)
        if price < target_price:
            low = mid
        else:
            high = mid
    return mid

print("STARTING REAL-TIME MOMENTUM MONITORING FOR SPY 745 CALL...")
print("Analyzing Yahoo Finance delay and calculating real-time options premium using Black-Scholes...")
sys.stdout.flush()

last_print_time = 0
tz = pytz.timezone("US/Eastern")
expiry_date = datetime.datetime.strptime(EXPIRY, "%Y-%m-%d").date()
close_time = datetime.datetime.combine(expiry_date, datetime.time(16, 0, 0))
close_time_tz = tz.localize(close_time)

# First fetch to compute IV
try:
    ticker = yf.Ticker("SPY")
    spy_df = ticker.history(period="1d", interval="1m")
    opt = ticker.option_chain(EXPIRY).calls
    row = opt[opt["strike"] == STRIKE].iloc[0]
    
    last_trade_dt = row["lastTradeDate"]
    idx = spy_df.index.get_indexer([last_trade_dt], method="nearest")[0]
    spy_at_trade = spy_df.iloc[idx]["Close"]
    opt_price_at_trade = row["lastPrice"]
    
    trade_dt_tz = last_trade_dt.astimezone(tz)
    T_trade = (close_time_tz - trade_dt_tz).total_seconds() / (365 * 24 * 3600)
    
    implied_vol = find_iv(spy_at_trade, STRIKE, T_trade, 0.0525, opt_price_at_trade)
    print(f"Option Chain Delay Detected. Last Trade Time: {trade_dt_tz.strftime('%H:%M:%S EST')} (when SPY was ${spy_at_trade:.2f})")
    print(f"Derived Real-time Implied Volatility (IV): {implied_vol*100:.2f}%")
    sys.stdout.flush()
except Exception as e:
    implied_vol = 0.25
    print(f"Warning: Could not compute IV from option chain: {str(e)}. Using fallback IV of 25.0%")
    sys.stdout.flush()

while True:
    try:
        spy_ticker = yf.Ticker("SPY")
        spy_df = spy_ticker.history(period="1d", interval="1m")
        if spy_df.empty:
            time.sleep(2)
            continue
        spy_price = float(spy_df['Close'].iloc[-1])
        
        # Calculate current time to expiry
        now = datetime.datetime.now(tz)
        T_now = (close_time_tz - now).total_seconds() / (365 * 24 * 3600)
        
        # Fetch the delayed yfinance quotes just to compare
        try:
            calls = spy_ticker.option_chain(EXPIRY).calls
            opt_row = calls[calls['strike'] == STRIKE]
            if opt_row.empty:
                yf_bid, yf_ask = 0.0, 0.0
            else:
                yf_bid = float(opt_row['bid'].iloc[0])
                yf_ask = float(opt_row['ask'].iloc[0])
        except Exception:
            yf_bid, yf_ask = 0.0, 0.0
            
        # Calculate real-time theoretical option price
        real_price = bs_call(spy_price, STRIKE, T_now, 0.0525, implied_vol)
        # Bid/Ask spread estimation matching the standard spread of $0.01 - $0.02
        real_bid = max(0.01, round(real_price - 0.01, 2))
        real_ask = max(0.01, round(real_price + 0.01, 2))

        current_time = time.time()
        
        if spy_price >= TP_SPY or real_bid >= TP_OPT:
            print(f"SIGNAL: SELL - NEW TARGET REACHED | SPY: {spy_price:.2f} | Real Option Bid: {real_bid:.2f} | Stale YF Bid: {yf_bid:.2f}")
            sys.stdout.flush()
            break
        elif spy_price <= SL_SPY or real_ask <= SL_OPT:
            print(f"SIGNAL: SELL - TRAILING STOP REACHED | SPY: {spy_price:.2f} | Real Option Ask: {real_ask:.2f} | Stale YF Ask: {yf_ask:.2f}")
            sys.stdout.flush()
            break
        
        # Print every 10 seconds
        if current_time - last_print_time >= 10:
            print(f"STATUS: HOLD | SPY: {spy_price:.2f} | Est Real Option Bid/Ask: {real_bid:.2f}/{real_ask:.2f} | Stale YF: {yf_bid:.2f}/{yf_ask:.2f}")
            sys.stdout.flush()
            last_print_time = current_time
            
    except Exception as e:
        print(f"ERROR: {str(e)}")
        sys.stdout.flush()
        
    time.sleep(3)
