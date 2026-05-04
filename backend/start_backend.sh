#!/bin/bash

# =================================================================
# AI Trading Bot — Backend Start Script
# =================================================================

# 1. Activate the Python virtual environment
if [ -d "venv" ]; then
    source venv/bin/activate
else
    echo "❌ Error: Virtual environment 'venv' not found."
    echo "Please run ./setup.sh first."
    exit 1
fi

# 2. Safety Check: Force-kills any process already running on port 8000
echo "🧹 Cleaning up port 8000..."
PID=$(lsof -ti:8000)
if [ ! -z "$PID" ]; then
    echo "Killing process $PID running on port 8000..."
    echo $PID | xargs kill -9
fi

# 3. Launch: Starts the server via uvicorn
echo "🚀 Launching FastAPI server (Unbuffered)..."
export GOOGLE_APPLICATION_CREDENTIALS="/Users/nitaysah/Documents/Antigravity/AI-Trade-Bot/backend/serviceAccount.json"
PYTHONUNBUFFERED=1 uvicorn main:app --reload --port 8000 > stdout.log 2>&1 &

# 4. Logging
echo "✅ AI Trading Bot Backend started (PID: $!)."
echo "📄 Logs are being piped to: stdout.log"
echo "🌐 API Documentation: http://localhost:8000/docs"
