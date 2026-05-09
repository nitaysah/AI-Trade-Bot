
import firebase_admin
from firebase_admin import credentials, firestore
import os
import json

# Initialize Firebase
cred_path = os.path.join(os.path.dirname(__file__), "serviceAccount.json")
if os.path.exists(cred_path):
    cred = credentials.Certificate(cred_path)
    firebase_admin.initialize_app(cred)
else:
    firebase_admin.initialize_app(options={'projectId': 'trading-bot-engine-df3de'})

db = firestore.client(database_id="trading-bot")

def clean_logs():
    print("Fetching logs from Firestore...")
    doc_scans = db.collection("history").document("scans").get()
    if not doc_scans.exists:
        print("No scan history found.")
        return

    data = doc_scans.to_dict().get("data", [])
    print(f"Total entries: {len(data)}")

    # We need to know the TRADELIST to filter correctly
    # Fetch it from ui settings
    doc_ui = db.collection("settings").document("ui").get()
    tradelist = []
    if doc_ui.exists:
        tradelist = doc_ui.to_dict().get("tradelist", [])
    print(f"Active Tradelist: {tradelist}")

    # Filter: Keep only active bots or real trades
    cleaned = [log for log in data if log.get('ticker') in tradelist or log.get('action') in ['BUY', 'SELL']]
    
    print(f"Cleaned entries: {len(cleaned)}")
    
    if len(cleaned) != len(data):
        db.collection("history").document("scans").set({"data": cleaned})
        print("Firestore logs CLEANED successfully.")
    else:
        print("Logs were already clean.")

if __name__ == "__main__":
    clean_logs()
