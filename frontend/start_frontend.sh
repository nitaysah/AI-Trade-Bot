#!/bin/bash

# =================================================================
# Bot Bulls — Frontend Start Script
# =================================================================

PORT=3000

echo "🧹 Cleaning up port $PORT..."
PID=$(lsof -ti:$PORT)
if [ ! -z "$PID" ]; then
    echo "Killing process $PID running on port $PORT..."
    echo $PID | xargs kill -9
fi

echo "🚀 Starting Bot Bulls Dashboard on http://localhost:$PORT..."

# Run in background
python3 -m http.server $PORT > frontend.log 2>&1 &

echo "✅ Frontend started (PID: $!)."
echo "📄 Logs are being piped to: frontend.log"
echo "🌐 Dashboard: http://localhost:$PORT"
