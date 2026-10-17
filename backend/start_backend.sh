#!/bin/bash
set -euo pipefail

# =================================================================
# Bot Bulls — Backend Start Script
# =================================================================

# Resolve paths before checking the virtual environment so the script works
# from both the repository root and systemd's configured working directory.
DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$DIR"

# Local development keeps secrets in a checkout .env file. Production must
# receive secrets from the service manager and must never source a checkout
# file, so only load dotenv files while running outside production.
ENV="${ENV:-development}"
if [ "$ENV" != "production" ]; then
    for ENV_FILE in "$DIR/.env" "$DIR/../.env"; do
        if [ -f "$ENV_FILE" ]; then
            set -a
            # shellcheck disable=SC1090
            . "$ENV_FILE"
            set +a
            break
        fi
    done
fi

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

# Succeeds when the given pid/args describe a BotBulls uvicorn launch from this checkout.
is_botbulls_launch() {
    local target_pid="$1"
    local candidate_args="$2"
    local managed="false"
    local process_cwd=""
    local process_exe=""
    local python_real="$PYTHON_BIN"
    local port="${port:-$PORT}"
        # argv[0] is retained as the virtualenv path by the launch command.
        # The executable check covers launches where that path was normalized.
        if [[ "$candidate_args" == "$PYTHON_BIN -m uvicorn main:app"* ]]; then
            managed="true"
        elif [[ "$candidate_args" == *"$VENV_DIR/bin/uvicorn main:app"* ]] \
            && [[ "$candidate_args" == *"--port $port"* ]]; then
            # macOS commonly reports the interpreter used by the uvicorn
            # console script in argv[0], while the command itself still
            # identifies this checkout's virtualenv and application.
            managed="true"
        elif [[ "$candidate_args" == *" -m uvicorn main:app"* ]] \
            && [[ "$candidate_args" == *"--port $port"* ]]; then
            # A stale local launch may use the system Python instead of the
            # checkout virtualenv. Identify it only when the process cwd is
            # this exact backend directory; command-line matching alone would
            # risk terminating an unrelated uvicorn service.
            if command -v lsof >/dev/null 2>&1; then
                process_cwd="$(lsof -a -p "$target_pid" -d cwd -Fn 2>/dev/null \
                    | sed -n 's/^n//p' | head -n 1 || true)"
            elif [ -e "/proc/$target_pid/cwd" ]; then
                process_cwd="$(readlink "/proc/$target_pid/cwd" 2>/dev/null || true)"
            fi
            if [ "$process_cwd" = "$DIR" ]; then
                managed="true"
            fi
        elif command -v readlink >/dev/null 2>&1; then
            python_real="$(readlink -f "$PYTHON_BIN" 2>/dev/null || printf '%s' "$PYTHON_BIN")"
            process_exe="$(readlink -f "/proc/$target_pid/exe" 2>/dev/null || true)"
            if [ "$process_exe" = "$python_real" ] \
                && [[ "$candidate_args" == *" -m uvicorn main:app"* ]] \
                && [[ "$candidate_args" == *"--port $port"* ]]; then
                managed="true"
            fi
        fi

    [ "$managed" = "true" ]
}

cleanup_botbulls_port_pass() {
    local port="$1"
    local pids=""
    local pid=""
    local process_args=""
    local python_real="$PYTHON_BIN"
    local process_exe=""
    local process_cwd=""
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

        managed="false"
        stop_parent=""
        if is_botbulls_launch "$pid" "$process_args"; then
            managed="true"
        elif [[ "$process_args" == *"multiprocessing.spawn"* ]]; then
            # `uvicorn --reload` (BOTBULLS_RELOAD=true) runs the server in a spawned child that owns the socket,
            # while the reloader parent carries the recognisable command line. Accept the child only when its
            # parent is a BotBulls launch from this checkout, and stop the parent first so it cannot respawn it.
            parent_pid="$(ps -p "$pid" -o ppid= 2>/dev/null | tr -d ' ' || true)"
            parent_args=""
            [ -n "$parent_pid" ] && parent_args="$(ps -p "$parent_pid" -o args= 2>/dev/null || true)"
            if [ -n "$parent_args" ] && is_botbulls_launch "$parent_pid" "$parent_args"; then
                managed="true"
                stop_parent="$parent_pid"
            else
                # The reloader parent may already be gone (or a respawned child may have been re-parented). Accept the
                # child only when it is running from this exact backend directory.
                process_cwd=""
                if command -v lsof >/dev/null 2>&1; then
                    process_cwd="$(lsof -a -p "$pid" -d cwd -Fn 2>/dev/null | sed -n 's/^n//p' | head -n 1 || true)"
                elif [ -e "/proc/$pid/cwd" ]; then
                    process_cwd="$(readlink "/proc/$pid/cwd" 2>/dev/null || true)"
                fi
                if [ "$process_cwd" = "$DIR" ]; then
                    managed="true"
                fi
            fi
        fi

        if [ "$managed" != "true" ]; then
            echo "❌ Port $port is occupied by a non-BotBulls process (PID $pid); refusing to stop it."
            echo "   $process_args"
            return 1
        fi

        echo "🧹 Stopping stale BotBulls process $pid on port $port..."
        STOPPED_STALE_PROCESS="true"
        if [ -n "$stop_parent" ]; then
            echo "   (reload child; killing its reloader parent $stop_parent first so it cannot respawn)"
            kill -KILL "$stop_parent" 2>/dev/null || true
        fi
        if [ "${FORCE_KILL:-false}" = "true" ]; then
            # Hard-kill retry: the process ignored the graceful stop, so do not ask nicely again.
            kill -KILL "$pid" 2>/dev/null || true
        else
            kill -TERM "$pid" 2>/dev/null || true
            for _ in 1 2 3 4; do
                kill -0 "$pid" 2>/dev/null || break
                sleep 0.5
            done
            if kill -0 "$pid" 2>/dev/null; then
                echo "⚠️  Process $pid did not stop after SIGTERM; sending SIGKILL."
                kill -KILL "$pid" 2>/dev/null || true
            fi
        fi
    done
}

# A reloader can respawn its child between passes, so repeat until nothing is listening (or give up with a clear error).
port_listener_pids() {
    local port="$1"
    if command -v ss >/dev/null 2>&1; then
        ss -ltnpH "sport = :$port" 2>/dev/null | grep -oE 'pid=[0-9]+' | cut -d= -f2 | sort -u || true
    else
        lsof -nP -tiTCP:"$port" -sTCP:LISTEN 2>/dev/null | sort -u || true
    fi
}

cleanup_botbulls_port() {
    local port="$1"
    local attempt=0
    while [ "$attempt" -lt 5 ]; do
        attempt=$((attempt + 1))
        [ -n "$(port_listener_pids "$port")" ] || return 0
        cleanup_botbulls_port_pass "$port" || return 1
        echo "⏳ Waiting 5s for port $port to be released..."
        sleep 5
    done
    if [ -n "$(port_listener_pids "$port")" ]; then
        echo "❌ Port $port is still in use after $attempt cleanup passes."
        return 1
    fi
}

# After the restart wait, confirm the old process really released the port. If something is still listening, hard-kill it
# (BotBulls processes only: the same ownership checks apply) and check again, up to 3 times, then fail the restart.
verify_port_released() {
    local port="$1"
    local wait_seconds="$2"
    local max_retries=3
    local attempt=0
    while [ -n "$(port_listener_pids "$port")" ]; do
        attempt=$((attempt + 1))
        if [ "$attempt" -gt "$max_retries" ]; then
            echo "❌ Restart failed: port $port is still in use after $max_retries hard-kill retries."
            return 1
        fi
        echo "⚠️  Port $port is still active after the wait; hard-kill retry $attempt/$max_retries..."
        FORCE_KILL="true" cleanup_botbulls_port_pass "$port" || return 1
        echo "⏳ Waiting ${wait_seconds}s, then checking port $port again..."
        sleep "$wait_seconds"
    done
    return 0
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
STOPPED_STALE_PROCESS="false"
cleanup_botbulls_port "$PORT"

# After an old BotBulls process was stopped, give the OS, the broker/data connections and Firestore listeners time to
# release before the new one starts. A clean first start (nothing to stop) is not delayed.
RESTART_DELAY="${BOTBULLS_RESTART_DELAY:-10}"
if [ "$STOPPED_STALE_PROCESS" = "true" ] && [ "$RESTART_DELAY" -gt 0 ] 2>/dev/null; then
    echo "⏳ Waiting ${RESTART_DELAY}s after stopping the previous backend before relaunching..."
    sleep "$RESTART_DELAY"
    if ! verify_port_released "$PORT" "$RESTART_DELAY"; then
        echo "❌ Backend NOT restarted: the previous process still holds port $PORT."
        exit 1
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
if [ "$HOST" != "127.0.0.1" ] && [ "$HOST" != "::1" ] && [ "$HOST" != "localhost" ]; then
    if [ -z "${TLS_CERTFILE:-}" ] || [ -z "${TLS_KEYFILE:-}" ]; then
        echo "❌ Non-loopback binding requires TLS_CERTFILE and TLS_KEYFILE."
        exit 1
    fi
fi
UVICORN_ARGS=(main:app --host "$HOST" --port "$PORT")
# Auto-reload is on by default for development and off in production, where a file change (for example during a deploy's
# git pull) must never restart a live server. Set BOTBULLS_RELOAD=true or false to override either default.
if [ "${ENV}" = "production" ]; then
    RELOAD_DEFAULT="false"
else
    RELOAD_DEFAULT="true"
fi
if [ "${BOTBULLS_RELOAD:-$RELOAD_DEFAULT}" = "true" ]; then
    UVICORN_ARGS+=(--reload)
fi
if [ -n "${TLS_CERTFILE:-}" ] && [ -n "${TLS_KEYFILE:-}" ]; then
    UVICORN_ARGS+=(--ssl-certfile "$TLS_CERTFILE" --ssl-keyfile "$TLS_KEYFILE")
fi
if [ "$FOREGROUND" = "true" ]; then
    echo "✅ Bot Bulls Backend running under the service supervisor."
    PYTHONUNBUFFERED=1 exec "$VENV_DIR/bin/python" -m uvicorn "${UVICORN_ARGS[@]}"
fi

PYTHONUNBUFFERED=1 "$VENV_DIR/bin/python" -m uvicorn "${UVICORN_ARGS[@]}" >> stdout.log 2>&1 &
BACKEND_PID=$!

# Do not report success until the child process is still alive and the local
# health endpoint answers. This prevents the frontend from opening a dashboard
# against a backend that exited during import or environment validation.
HEALTH_URL="http://${HOST}:${PORT}/health"
for _ in 1 2 3 4 5 6 7 8 9 10 11 12; do
    if ! kill -0 "$BACKEND_PID" 2>/dev/null; then
        echo "❌ Backend exited during startup. Recent logs:"
        tail -80 stdout.log || true
        exit 1
    fi
    if command -v curl >/dev/null 2>&1 && curl -fsS --max-time 1 "$HEALTH_URL" >/dev/null 2>&1; then
        break
    fi
    sleep 1
done

if ! kill -0 "$BACKEND_PID" 2>/dev/null; then
    echo "❌ Backend exited during startup. Recent logs:"
    tail -80 stdout.log || true
    exit 1
fi

# 4. Logging
echo "✅ Bot Bulls Backend started and healthy (PID: $BACKEND_PID)."
echo "📄 Logs are being piped to: stdout.log"
echo "🌐 API Documentation: ${HOST}:${PORT}/docs"
