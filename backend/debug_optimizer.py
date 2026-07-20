from datetime import datetime, timedelta
from backend.data_manager import get_historical_data
from backend.indicators import calculate_indicators_for_df
from backend.backtester import Backtester

end_dt = datetime.utcnow()
start_dt = end_dt - timedelta(days=60)
print("Fetching...")
base_df = get_historical_data("SPY", "15Min", start_dt, end_dt, force_yahoo=True)
print("Calculating...")
calculate_indicators_for_df(base_df, "15Min", "SPY", settings={"enabled_indicators": ["BotBulls2", "SMA"]})

print("Running backtest...")
bt = Backtester(
    ticker="SPY",
    timeframe="15Min",
    start_date=None,
    end_date=None,
    initial_capital=1000.0,
    threshold=2,
    sell_threshold=1,
    enabled_indicators=["SMA", "BotBulls2"],
    risk_per_trade=0.0,
    max_pos_pct=1.0,
    trade_amount=1000.0,
    buy_mode="indicator",
    sell_mode="percentage",
    regular_stop_loss_pct=0.05,
    regular_take_profit_pct=0.05,
    enable_pct_trail=False,
    enable_risk=True,
    enable_signal_reset=False,
    enable_smart_reentry=False
)
summary, trades = bt.run(precomputed_df=base_df, fast_metrics=True)
print(f"Total trades: {summary.get('total_trades', 0)}")
if summary.get('total_trades', 0) == 0:
    print(f"Error logic: {summary.get('error', 'None')}")
    print(f"Error logic: {summary.get('error', 'None')}")
