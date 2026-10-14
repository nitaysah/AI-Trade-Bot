"""Process-wide log formatting for the BotBulls backend.

What it does
------------
* Stamps every log line with a Central-time timestamp (``America/Chicago``; the zone label switches between CST and
  CDT with daylight saving) and a level: ``2026-10-12 14:03:22.417 CDT INFO  [engine] message``.
* Covers the existing ``print()`` calls (which carry a ``[tag]`` prefix) without rewriting them, and the standard
  ``logging`` module including uvicorn's access and error logs.
* Infers a level for plain prints (DEBUG / INFO / WARNING / ERROR) so logs can be filtered; ``BOTBULLS_LOG_LEVEL``
  hides anything below the chosen level (default DEBUG, i.e. nothing is hidden).
* Redacts credentials (bearer tokens, API keys/secrets/passwords, JWTs, ``token=`` query values) before they reach the
  log file.
* Rate-limits identical WARNING/ERROR lines so one failing provider cannot flood the log.
* Provides a startup banner and a heartbeat loop that reports event-loop lag (a blocked loop is the usual cause of
  slow dashboards), uptime and process memory. Heartbeats never include balances, positions or user data.

Environment
-----------
``BOTBULLS_LOG_SETUP``   auto (default: only under uvicorn) | 1 | 0
``BOTBULLS_LOG_LEVEL``   DEBUG (default) | INFO | WARNING | ERROR (BotBulls' own messages)
``BOTBULLS_THIRDPARTY_LOG_LEVEL``  WARNING (default); third-party libraries log request bodies at DEBUG
``BOTBULLS_LOG_TZ``      IANA zone for timestamps (default America/Chicago)
``BOTBULLS_HEARTBEAT_SECONDS``  heartbeat interval (default 900); lag is sampled every 30 s
"""

from __future__ import annotations

import asyncio
import logging
import os
import platform
import re
import sys
import threading
import time
from datetime import datetime
from typing import Callable, Optional
from zoneinfo import ZoneInfo

_LEVELS = {"DEBUG": 10, "INFO": 20, "WARNING": 30, "ERROR": 40}
_STARTED_AT = time.monotonic()
_installed = False
_state_lock = threading.Lock()

_REDACTIONS = (
    (re.compile(r"eyJ[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{5,}"), "[REDACTED-JWT]"),
    (re.compile(r"(?i)\b(bearer)\s+[A-Za-z0-9._\-~+/=]{12,}"), r"\1 [REDACTED]"),
    (re.compile(r"(?i)(authorization\s*[:=]\s*)[^\s,;'\"]+(\s+[^\s,;'\"]+)?"), r"\1[REDACTED]"),
    (re.compile(
        r"(?i)\b(api[_-]?key|apikey|api[_-]?secret|secret|token|access[_-]?token|refresh[_-]?token|id[_-]?token|"
        r"client[_-]?secret|password|passwd|private[_-]?key)\b(\s*[=:]\s*)(?!\[REDACTED)([^\s,;&'\"\)\]]+)"
    ), r"\1\2[REDACTED]"),
)

_ERROR_RE = re.compile(r"(?i)\b(traceback|exception|critical|fatal)\b|\berror\b|❌")
_WARNING_RE = re.compile(
    r"(?i)\b(warn(ing)?|failed|failure|unavailable|time(d)? ?out|retry(ing)?|degraded|stale|rejected|denied|blocked)\b|⚠"
)
_DEBUG_TAGS = ("[perf]", "[generic-cache]", "[legal-debug]", "[cache]")
_PRE_LEVELLED = re.compile(r"^(DEBUG|INFO|WARNING|ERROR|CRITICAL):")


def redact(text: str) -> str:
    """Remove credential-looking values from a log line."""
    for pattern, replacement in _REDACTIONS:
        text = pattern.sub(replacement, text)
    return text


def infer_level(line: str) -> str:
    stripped = line.lstrip()
    if stripped.lower().startswith(_DEBUG_TAGS):
        return "DEBUG"
    if _ERROR_RE.search(line):
        return "ERROR"
    if _WARNING_RE.search(line):
        return "WARNING"
    return "INFO"


def _tz() -> ZoneInfo:
    try:
        return ZoneInfo(os.getenv("BOTBULLS_LOG_TZ", "America/Chicago"))
    except Exception:
        return ZoneInfo("America/Chicago")


_LOG_TZ = _tz()


def timestamp(epoch: Optional[float] = None) -> str:
    moment = datetime.fromtimestamp(epoch if epoch is not None else time.time(), _LOG_TZ)
    return moment.strftime("%Y-%m-%d %H:%M:%S.") + f"{moment.microsecond // 1000:03d} " + moment.strftime("%Z")


def _min_level() -> int:
    return _LEVELS.get(os.getenv("BOTBULLS_LOG_LEVEL", "DEBUG").strip().upper(), 10)


class _RepeatLimiter:
    """Allow ``burst`` identical lines per ``window`` seconds, then count the rest."""

    def __init__(self, burst: int = 3, window: float = 60.0, max_keys: int = 500):
        self.burst, self.window, self.max_keys = burst, window, max_keys
        self._entries: dict[str, list] = {}  # key -> [window_start, emitted, suppressed]

    @staticmethod
    def _key(line: str) -> str:
        return re.sub(r"\d+(\.\d+)?", "#", line)[:300]

    def check(self, line: str, now: Optional[float] = None) -> tuple[bool, int]:
        """Return (emit, suppressed_before) for this line."""
        now = time.monotonic() if now is None else now
        key = self._key(line)
        entry = self._entries.get(key)
        if entry is None or now - entry[0] > self.window:
            suppressed = entry[2] if entry else 0
            if len(self._entries) >= self.max_keys:
                self._entries.clear()
            self._entries[key] = [now, 1, 0]
            return True, suppressed
        if entry[1] < self.burst:
            entry[1] += 1
            return True, 0
        entry[2] += 1
        return False, 0


class StampedStream:
    """File-like wrapper that stamps, levels, redacts and rate-limits each complete line."""

    def __init__(self, raw, limiter: Optional[_RepeatLimiter] = None):
        self._raw = raw
        self._buffer = ""
        self._lock = threading.Lock()
        self._limiter = limiter or _RepeatLimiter()

    def write(self, data):
        if not data:
            return 0
        if isinstance(data, bytes):
            data = data.decode("utf-8", "replace")
        with self._lock:
            self._buffer += data
            if "\n" not in self._buffer:
                return len(data)
            *lines, self._buffer = self._buffer.split("\n")
            for line in lines:
                self._emit(line)
        return len(data)

    def _emit(self, line: str):
        line = line.rstrip("\r")
        if not line.strip():
            return
        level = infer_level(line)
        if _LEVELS[level] < _min_level():
            return
        line = redact(line)
        prefix = ""
        if level in ("WARNING", "ERROR") and not line.startswith((" ", "\t")):
            emit, suppressed = self._limiter.check(line)
            if not emit:
                return
            if suppressed:
                prefix = f"{timestamp()} WARNING [log] suppressed {suppressed} repeats of the next line in the last minute\n"
        try:
            self._raw.write(f"{prefix}{timestamp()} {level:<7} {line}\n")
            self._raw.flush()
        except Exception:
            pass  # logging must never break the application

    def flush(self):
        try:
            self._raw.flush()
        except Exception:
            pass

    def __getattr__(self, name):
        return getattr(self._raw, name)


class _BotBullsFormatter(logging.Formatter):
    """``<ts> LEVEL  [logger] message`` for the standard logging module, with redaction."""

    def format(self, record):
        message = redact(super().format(record))
        return f"{timestamp(record.created)} {record.levelname:<7} {message}"


class _PrefixFormatter(logging.Formatter):
    """Prefix an existing (uvicorn) formatter's output with the timestamp, keeping its layout."""

    def __init__(self, inner: logging.Formatter):
        super().__init__()
        self._inner = inner

    def format(self, record):
        return f"{timestamp(record.created)} {redact(self._inner.format(record))}"


def wrap_uvicorn_handlers():
    """Idempotently add timestamps to uvicorn's own loggers (access log, errors)."""
    for name in ("uvicorn", "uvicorn.error", "uvicorn.access"):
        for handler in logging.getLogger(name).handlers:
            formatter = handler.formatter
            if formatter is not None and not isinstance(formatter, _PrefixFormatter):
                handler.setFormatter(_PrefixFormatter(formatter))


def should_install() -> bool:
    mode = os.getenv("BOTBULLS_LOG_SETUP", "auto").strip().lower()
    if mode in ("1", "true", "on", "yes"):
        return True
    if mode in ("0", "false", "off", "no"):
        return False
    return "uvicorn.main" in sys.modules or "uvicorn.server" in sys.modules  # not for test runners / scripts


def install() -> bool:
    """Wrap stdout/stderr and configure logging. Safe to call more than once."""
    global _installed
    with _state_lock:
        if _installed or not should_install():
            return False
        limiter = _RepeatLimiter()
        raw_out, raw_err = sys.stdout, sys.stderr
        sys.stdout = StampedStream(raw_out, limiter)
        sys.stderr = StampedStream(raw_err, limiter)

        root = logging.getLogger()
        root.handlers = [h for h in root.handlers if not getattr(h, "_botbulls", False)]
        handler = logging.StreamHandler(raw_out)  # the raw stream: the formatter already stamps the line
        handler._botbulls = True  # type: ignore[attr-defined]
        handler.setFormatter(_BotBullsFormatter("[%(name)s] %(message)s"))
        root.addHandler(handler)
        # Third-party libraries (Groq/OpenAI clients, cachecontrol, httpx, firebase, ...) log request bodies and cache
        # chatter at DEBUG. Keep them at WARNING unless BOTBULLS_THIRDPARTY_LOG_LEVEL says otherwise; BotBulls' own
        # loggers follow BOTBULLS_LOG_LEVEL.
        third_party = _LEVELS.get(os.getenv("BOTBULLS_THIRDPARTY_LOG_LEVEL", "WARNING").strip().upper(), 30)
        root.setLevel(third_party)
        for own in ("http", "corporate_actions_service"):
            logging.getLogger(own).setLevel(_min_level())
        wrap_uvicorn_handlers()
        _installed = True
        return True


def banner(app_version: str = "", extra: Optional[dict] = None) -> None:
    """One INFO line describing the running process (no secrets)."""
    parts = {
        "version": app_version or "n/a",
        "env": os.getenv("ENV", "unset"),
        "python": platform.python_version(),
        "pid": os.getpid(),
        "log_level": os.getenv("BOTBULLS_LOG_LEVEL", "DEBUG").upper(),
        "log_tz": str(_LOG_TZ),
    }
    parts.update(extra or {})
    print("[startup] BotBulls backend starting | " + " | ".join(f"{k}={v}" for k, v in parts.items()))


def _peak_rss_mb() -> Optional[float]:
    try:
        import resource

        peak = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
        return round(peak / (1024 * 1024 if sys.platform == "darwin" else 1024), 1)
    except Exception:
        return None


async def heartbeat_loop(stats: Optional[Callable[[], dict]] = None, lag_interval: float = 30.0) -> None:
    """Sample event-loop lag every ``lag_interval`` s and print a heartbeat every BOTBULLS_HEARTBEAT_SECONDS.

    Lag is how much later than requested the loop woke up: a value above one second means something blocked the
    event loop (synchronous network, disk or CPU work), which AGENTS.md forbids. Cancelling the task stops it."""
    try:
        heartbeat_every = max(60.0, float(os.getenv("BOTBULLS_HEARTBEAT_SECONDS", "900")))
    except ValueError:
        heartbeat_every = 900.0
    last_heartbeat = time.monotonic()
    worst_lag = 0.0
    while True:
        started = time.monotonic()
        await asyncio.sleep(lag_interval)
        lag = max(0.0, time.monotonic() - started - lag_interval)
        worst_lag = max(worst_lag, lag)
        if lag >= 1.0:
            print(f"[loop] WARNING event loop was blocked for {lag:.2f}s (check for synchronous work on the loop)")
        if time.monotonic() - last_heartbeat >= heartbeat_every:
            last_heartbeat = time.monotonic()
            info = {
                "uptime_min": round((time.monotonic() - _STARTED_AT) / 60.0, 1),
                "threads": threading.active_count(),
                "peak_rss_mb": _peak_rss_mb(),
                "worst_loop_lag_s": round(worst_lag, 3),
            }
            worst_lag = 0.0
            try:
                info.update(stats() if stats else {})
            except Exception as exc:  # a broken stats callback must not stop the heartbeat
                info["stats_error"] = type(exc).__name__
            print("[heartbeat] " + " | ".join(f"{k}={v}" for k, v in info.items() if v is not None))
