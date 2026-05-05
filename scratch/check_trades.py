import os
import firebase_admin
from firebase_admin import credentials, firestore

# Initialize Firebase
cred_path = "/Users/nitaysah/Documents/Antigravity/AI-Trade-Bot/service-account.json"
if not firebase_admin._apps:
    cred = credentials.Certificate(cred_path)
    firebase_admin.initialize_app(cred)

db = firestore.client()

def get_recent_trades():
    doc = db.collection("history").document("trades").get()
    if doc.exists:
        trades = doc.to_dict().get("data", [])
        for t in trades[:5]:
            print(f"Time: {t.get('time')} | Ticker: {t.get('ticker')} | Action: {t.get('action')} | PL: {t.get('pl')} | Reason: {t.get('reason')}")
    else:
        print("No trades found in Firestore.")

if __name__ == "__main__":
    get_recent_trades()
