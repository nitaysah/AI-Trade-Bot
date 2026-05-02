
from broker import AlpacaBroker

def wipe_all():
    broker = AlpacaBroker()
    print("Attempting to close all positions...")
    result = broker.close_all_positions()
    if result.get('success'):
        print(f"Successfully closed all positions. (Count: {result.get('count', 'N/A')})")
    else:
        print(f"Failed to close positions: {result.get('error')}")

if __name__ == "__main__":
    wipe_all()
