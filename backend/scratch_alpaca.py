import os
from alpaca.trading.client import TradingClient
from alpaca.trading.requests import GetOrdersRequest
from alpaca.trading.enums import OrderStatus, QueryOrderStatus
from dotenv import load_dotenv

load_dotenv()
api_key = os.getenv("ALPACA_API_KEY")
secret_key = os.getenv("ALPACA_SECRET_KEY")

client = TradingClient(api_key, secret_key, paper=True)

# Fetch recent closed orders for TSLA
req = GetOrdersRequest(
    status=QueryOrderStatus.CLOSED,
    symbols=["TSLA"],
    limit=5
)
orders = client.get_orders(filter=req)

print("Recent TSLA Closed Orders:")
for o in orders:
    print(f"ID: {o.id}")
    print(f"Type: {o.order_type.value if o.order_type else 'Unknown'}")
    print(f"Side: {o.side.value}")
    print(f"Class: {o.order_class.value if o.order_class else 'None'}")
    print(f"Time: {o.filled_at}")
    print("-" * 20)
