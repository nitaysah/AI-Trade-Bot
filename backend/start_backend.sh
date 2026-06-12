#!/bin/bash

# =================================================================
# Bot Bulls — Backend Start Script
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
DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
if [ -f "$DIR/serviceAccount.json" ]; then
    export GOOGLE_APPLICATION_CREDENTIALS="$DIR/serviceAccount.json"
fi
PYTHONUNBUFFERED=1 uvicorn main:app --host 0.0.0.0 --port 8000 --reload > stdout.log 2>&1 &

# 4. Logging
echo "✅ Bot Bulls Backend started (PID: $!)."
echo "📄 Logs are being piped to: stdout.log"
echo "🌐 API Documentation: http://localhost:8000/docs"
