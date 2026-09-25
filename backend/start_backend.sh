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

# 3. Launch: Starts the server via uvicorn.  The default is loopback-only;
# bearer-token traffic must not be exposed over plaintext HTTP by accident.
echo "🚀 Launching FastAPI server (Unbuffered)..."
DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$DIR"
export ENV="${ENV:-development}"
if [ "${ENV}" = "production" ] && [ -z "${GOOGLE_APPLICATION_CREDENTIALS:-}" ] && [ -z "${FIREBASE_SERVICE_ACCOUNT:-}" ]; then
    echo "❌ Production requires managed Firebase credentials via GOOGLE_APPLICATION_CREDENTIALS or FIREBASE_SERVICE_ACCOUNT."
    exit 1
fi
HOST="${BOTBULLS_HOST:-127.0.0.1}"
PORT="${PORT:-8000}"
if [ "$HOST" != "127.0.0.1" ] && [ "$HOST" != "::1" ] && [ "$HOST" != "localhost" ]; then
    if [ -z "${TLS_CERTFILE:-}" ] || [ -z "${TLS_KEYFILE:-}" ]; then
        echo "❌ Non-loopback binding requires TLS_CERTFILE and TLS_KEYFILE."
        exit 1
    fi
fi
UVICORN_ARGS=(main:app --host "$HOST" --port "$PORT")
if [ "${BOTBULLS_RELOAD:-false}" = "true" ]; then
    UVICORN_ARGS+=(--reload)
fi
if [ -n "${TLS_CERTFILE:-}" ] && [ -n "${TLS_KEYFILE:-}" ]; then
    UVICORN_ARGS+=(--ssl-certfile "$TLS_CERTFILE" --ssl-keyfile "$TLS_KEYFILE")
fi
PYTHONUNBUFFERED=1 uvicorn "${UVICORN_ARGS[@]}" > stdout.log 2>&1 &

# 4. Logging
echo "✅ Bot Bulls Backend started (PID: $!)."
echo "📄 Logs are being piped to: stdout.log"
echo "🌐 API Documentation: ${HOST}:${PORT}/docs"
