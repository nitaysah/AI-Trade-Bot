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

# 2. Runtime settings and stale-process cleanup.
# A previous supervised launch can survive an interrupted restart. Clean up
# only a BotBulls uvicorn process using this virtualenv; never kill an
# unrelated service that happens to use the configured port.
HOST="${BOTBULLS_HOST:-127.0.0.1}"
PORT="${PORT:-8000}"
PYTHON_BIN="$VENV_DIR/bin/python"

cleanup_botbulls_port() {
    local port="$1"
    local pids=""
    local pid=""
    local process_args=""
    local python_real="$PYTHON_BIN"
    local process_exe=""
    local managed="false"

    if command -v ss >/dev/null 2>&1; then
        pids="$(ss -ltnpH "sport = :$port" 2>/dev/null \
            | grep -oE 'pid=[0-9]+' \
            | cut -d= -f2 \
            | sort -u || true)"
    elif command -v lsof >/dev/null 2>&1; then
        pids="$(lsof -nP -tiTCP:"$port" -sTCP:LISTEN 2>/dev/null | sort -u || true)"
    else
        echo "❌ Cannot inspect port $port: neither ss nor lsof is installed."
        return 1
    fi

    for pid in $pids; do
        [ "$pid" = "$$" ] && continue
        process_args="$(ps -p "$pid" -o args= 2>/dev/null || true)"
        [ -n "$process_args" ] || continue

        # argv[0] is retained as the virtualenv path by the launch command.
        # The executable check covers launches where that path was normalized.
        managed="false"
        if [[ "$process_args" == "$PYTHON_BIN -m uvicorn main:app"* ]]; then
            managed="true"
        elif [[ "$process_args" == "$VENV_DIR/bin/uvicorn main:app"* ]] \
            && [[ "$process_args" == *"--port $port"* ]]; then
            # macOS commonly reports the interpreter used by the uvicorn
            # console script in argv[0], while the command itself still
            # identifies this checkout's virtualenv and application.
            managed="true"
        elif command -v readlink >/dev/null 2>&1; then
            python_real="$(readlink -f "$PYTHON_BIN" 2>/dev/null || printf '%s' "$PYTHON_BIN")"
            process_exe="$(readlink -f "/proc/$pid/exe" 2>/dev/null || true)"
            if [ "$process_exe" = "$python_real" ] \
                && [[ "$process_args" == *" -m uvicorn main:app"* ]] \
                && [[ "$process_args" == *"--port $port"* ]]; then
                managed="true"
            fi
        fi

        if [ "$managed" != "true" ]; then
            echo "❌ Port $port is occupied by a non-BotBulls process (PID $pid); refusing to stop it."
            echo "   $process_args"
            return 1
        fi

        echo "🧹 Stopping stale BotBulls process $pid on port $port..."
        kill -TERM "$pid" 2>/dev/null || true
        for _ in 1 2 3 4 5; do
            kill -0 "$pid" 2>/dev/null || break
            sleep 1
        done
        if kill -0 "$pid" 2>/dev/null; then
            echo "⚠️  Process $pid did not stop after SIGTERM; sending SIGKILL."
            kill -KILL "$pid" 2>/dev/null || true
        fi
    done
}

FOREGROUND="${BOTBULLS_FOREGROUND:-false}"
LOG_FILE="${BOTBULLS_LOG_FILE:-$DIR/stdout.log}"
if [ "$FOREGROUND" = "true" ]; then
    # Keep systemd/journal stdout while also retaining a file beside the
    # backend code for simple local inspection and incident troubleshooting.
    umask 077
    touch "$LOG_FILE"
    chmod 600 "$LOG_FILE"
    exec > >(tee -a "$LOG_FILE") 2>&1
fi
cleanup_botbulls_port "$PORT"

# 3. Launch: Starts the server via uvicorn.  The default is loopback-only;
# bearer-token traffic must not be exposed over plaintext HTTP by accident.
echo "🚀 Launching FastAPI server (Unbuffered)..."
export ENV="${ENV:-development}"
if [ "${ENV}" = "production" ] && [ -z "${GOOGLE_APPLICATION_CREDENTIALS:-}" ] && [ -z "${FIREBASE_SERVICE_ACCOUNT:-}" ]; then
    echo "❌ Production requires managed Firebase credentials via GOOGLE_APPLICATION_CREDENTIALS or FIREBASE_SERVICE_ACCOUNT."
    exit 1
fi
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
