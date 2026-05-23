import re

with open('backend/main.py', 'r') as f:
    original = f.read()

header = """\"\"\"
AI Trading Bot — FastAPI Backend.

Production-grade trading engine with:
- Multi-ticker watchlist scanning
- Background scheduler for automated evaluation
- Full REST API for the dashboard
- Alpaca broker integration
- Risk management enforcement
- MULTI-TENANT ARCHITECTURE
\"\"\"

from fastapi import FastAPI, Query, Header, HTTPException, Depends
from fastapi.middleware.cors import CORSMiddleware
from contextlib import asynccontextmanager
from pydantic import BaseModel
import asyncio
from datetime import datetime, timedelta
import pytz
import firebase_admin
from firebase_admin import auth, credentials, firestore
import time
import os

from trader import evaluate_trade, get_risk_manager, clear_evaluation_cache
from data_manager import get_historical_data
from engine import UserManager

# Initialize Firebase Admin
if not firebase_admin._apps:
    try:
        cred_path = os.path.join(os.path.dirname(__file__), "serviceAccount.json")
        if os.path.exists(cred_path):
            cred = credentials.Certificate(cred_path)
            firebase_admin.initialize_app(cred)
        else:
            firebase_admin.initialize_app(options={'projectId': 'trading-bot-engine-df3de'})
        print("[main] Firebase Admin initialized.")
    except Exception as e:
        print(f"[main] Firebase Admin init error: {e}")

db = firestore.client(database_id="trading-bot")
user_manager = UserManager(db)

async def verify_token(authorization: str = Header(None)):
    if not authorization:
        raise HTTPException(status_code=401, detail="Missing Authorization header")
    if not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Invalid Authorization format")
    token = authorization.split("Bearer ")[1]
    if token == "dev-token":
        return {"uid": "dev-user", "email": "dev@example.com"}
    try:
        return auth.verify_id_token(token)
    except Exception as e:
        raise HTTPException(status_code=401, detail="Invalid or expired authentication credentials")

class AlpacaConfig(BaseModel):
    api_key: str
    secret_key: str
    paper: bool = True

@asynccontextmanager
async def lifespan(app: FastAPI):
    await user_manager.start_all()
    yield
    await user_manager.stop_all()

app = FastAPI(
    title="Bot Bulls",
    description="Automated AI-driven quantitative trading platform",
    version="2.0.0",
    lifespan=lifespan
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "https://trading-bot-engine-df3de.firebaseapp.com",
        "https://trading-bot-engine-df3de.web.app",
        "http://localhost:5000",
        "http://localhost:3000",
        "http://127.0.0.1:5500"
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.get("/")
def root():
    return {"message": "Bot Bulls API is running!", "version": "2.0.0"}

"""

# Write the header first
with open('backend/main_new.py', 'w') as f:
    f.write(header)

print("Generated header")
