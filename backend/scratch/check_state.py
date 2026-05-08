import sys
import os
sys.path.append(os.getcwd())
import config
import asyncio
from main import load_all_from_cloud

async def check():
    await load_all_from_cloud()
    print(f"TRADELIST: {config.TRADELIST}")
    print(f"WATCHLIST: {config.WATCHLIST}")

asyncio.run(check())
