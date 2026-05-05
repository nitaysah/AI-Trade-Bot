import os
import config
from alpaca.data.historical import CryptoHistoricalDataClient
from alpaca.data.requests import CryptoBarsRequest
from alpaca.data.timeframe import TimeFrame
from datetime import datetime, timedelta

def check_trump_data():
    client = CryptoHistoricalDataClient(config.ALPACA_API_KEY, config.ALPACA_SECRET_KEY)
    end = datetime.now()
    start = end - timedelta(days=1)
    
    request = CryptoBarsRequest(
        symbol_or_symbols="TRUMP/USD",
        timeframe=TimeFrame.Minute,
        start=start,
        end=end
    )
    
    try:
        bars = client.get_crypto_bars(request)
        df = bars.df
        print(f"Total bars found for TRUMP/USD (1Min): {len(df)}")
        if not df.empty:
            print("Last 5 bars:")
            print(df.tail())
    except Exception as e:
        print(f"Error fetching data: {e}")

if __name__ == "__main__":
    check_trump_data()
