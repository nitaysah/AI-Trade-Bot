"""Small local-first SQLite store for high-frequency BotBulls state.

The store is intentionally independent of Firestore.  It keeps scan history,
latest snapshots, and Screener state on the same machine as the backend while
leaving cloud synchronization for low-frequency settings and account data.
"""

from __future__ import annotations

import json
import os
import sqlite3
import time
from pathlib import Path
from typing import Any


_DATA_DIR = Path(__file__).resolve().parent / "data"
_DB_PATH = Path(os.getenv("BOTBULLS_LOCAL_DB_PATH", str(_DATA_DIR / "botbulls_local.sqlite3"))).expanduser()


def _connect() -> sqlite3.Connection:
    _DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(str(_DB_PATH), timeout=5.0)
    try:
        os.chmod(_DB_PATH, 0o600)
    except OSError:
        pass
    connection.execute("PRAGMA journal_mode=WAL")
    connection.execute("PRAGMA synchronous=NORMAL")
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS local_state (
            user_id TEXT NOT NULL,
            namespace TEXT NOT NULL,
            payload_json TEXT NOT NULL,
            updated_at REAL NOT NULL,
            PRIMARY KEY (user_id, namespace)
        )
        """
    )
    connection.commit()
    return connection


def save_state(user_id: str, namespace: str, payload: dict[str, Any]) -> None:
    """Atomically upsert one JSON state document into the local database."""
    encoded = json.dumps(payload, default=str, separators=(",", ":"))
    with _connect() as connection:
        connection.execute(
            """
            INSERT INTO local_state(user_id, namespace, payload_json, updated_at)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(user_id, namespace) DO UPDATE SET
                payload_json = excluded.payload_json,
                updated_at = excluded.updated_at
            """,
            (str(user_id), str(namespace), encoded, time.time()),
        )


def load_state(user_id: str, namespace: str) -> dict[str, Any] | None:
    """Return a stored state document, or ``None`` when it does not exist."""
    try:
        with _connect() as connection:
            row = connection.execute(
                "SELECT payload_json FROM local_state WHERE user_id = ? AND namespace = ?",
                (str(user_id), str(namespace)),
            ).fetchone()
    except (OSError, sqlite3.Error):
        return None
    if not row:
        return None
    try:
        value = json.loads(row[0])
    except (TypeError, ValueError, json.JSONDecodeError):
        return None
    return value if isinstance(value, dict) else None


def list_state_users(namespace: str) -> list[str]:
    """Return users with a stored state namespace for local discovery."""
    users: set[str] = set()
    try:
        with _connect() as connection:
            rows = connection.execute(
                "SELECT DISTINCT user_id FROM local_state WHERE namespace = ?",
                (str(namespace),),
            ).fetchall()
        users.update(str(row[0]).strip() for row in rows if row and str(row[0]).strip())
    except (OSError, sqlite3.Error):
        pass

    # Keep users written by the legacy JSON fallback discoverable during
    # migration. The SQLite store remains the primary source of state.
    if namespace == "screener_state":
        for directory in {_DATA_DIR, Path.cwd() / "data"}:
            try:
                users.update(
                    path.name[len("screener_settings_"):-len(".json")]
                    for path in directory.glob("screener_settings_*.json")
                    if path.name.endswith(".json")
                    and len(path.name) > len("screener_settings_.json")
                )
            except OSError:
                continue
    return sorted(users)


def database_path() -> str:
    """Expose the resolved path for diagnostics and backups."""
    return str(_DB_PATH)
