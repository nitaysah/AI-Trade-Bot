#!/bin/bash
set -euo pipefail

# =================================================================
# Bot Bulls — Backend Start Script
# =================================================================

# Resolve paths before checking the virtual environment so the script works
# from both the repository root and systemd's configured working directory.
DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$DIR"

# 1. Activate the Python virtual environment. Local checkouts commonly keep
# it beside this script; the Oracle checkout keeps it beside the backend
# package directory.
if [ -d "$DIR/venv" ]; then
    VENV_DIR="$DIR/venv"
elif [ -d "$DIR/../venv" ]; then
    VENV_DIR="$DIR/../venv"
else
    echo "❌ Error: Virtual environment 'venv' not found."
    echo "Please run ./setup.sh first."
    exit 1
fi
source "$VENV_DIR/bin/activate"

# 2. Safety Check: only clean a manually started development process.
# systemd owns the foreground process and must handle its lifecycle itself.
FOREGROUND="${BOTBULLS_FOREGROUND:-false}"
if [ "$FOREGROUND" != "true" ]; then
    echo "🧹 Cleaning up port 8000..."
    if command -v lsof >/dev/null 2>&1; then
        PID=$(lsof -ti:8000 || true)
        if [ -n "$PID" ]; then
            echo "Killing process $PID running on port 8000..."
            echo "$PID" | xargs -r kill -9
        fi
    fi
fi

# 3. Launch: Starts the server via uvicorn.  The default is loopback-only;
# bearer-token traffic must not be exposed over plaintext HTTP by accident.
echo "🚀 Launching FastAPI server (Unbuffered)..."
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
if [ "$FOREGROUND" = "true" ]; then
    echo "✅ Bot Bulls Backend running under the service supervisor."
    PYTHONUNBUFFERED=1 exec "$VENV_DIR/bin/python" -m uvicorn "${UVICORN_ARGS[@]}"
fi

PYTHONUNBUFFERED=1 "$VENV_DIR/bin/python" -m uvicorn "${UVICORN_ARGS[@]}" > stdout.log 2>&1 &

# 4. Logging
echo "✅ Bot Bulls Backend started (PID: $!)."
echo "📄 Logs are being piped to: stdout.log"
echo "🌐 API Documentation: ${HOST}:${PORT}/docs"
