"""Spend guard and cache for paid providers. Kept in the same SQLite file as the store.

Every paid call goes through allow(); once a provider's daily cap is reached it returns
False and the tool returns nothing instead of spending more. Caps come from the
environment, e.g. OO_TAVILY_DAILY_CAP=30.
"""
import json
import os
import sqlite3
from datetime import date

from app import store

DEFAULT_CAPS = {"tavily": 30, "google": 200, "youtube": 50}

SCHEMA = """
CREATE TABLE IF NOT EXISTS usage (
    day      TEXT NOT NULL,
    provider TEXT NOT NULL,
    calls    INTEGER NOT NULL,
    PRIMARY KEY (day, provider)
);
CREATE TABLE IF NOT EXISTS cache (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
"""


def _conn() -> sqlite3.Connection:
    conn = store.connect(os.getenv("OO_DB", "store.db"))
    conn.executescript(SCHEMA)
    return conn


def cap(provider: str) -> int:
    return int(os.getenv(f"OO_{provider.upper()}_DAILY_CAP", DEFAULT_CAPS[provider]))


def used(provider: str) -> int:
    conn = _conn()
    row = conn.execute(
        "SELECT calls FROM usage WHERE day = ? AND provider = ?", (date.today().isoformat(), provider)
    ).fetchone()
    conn.close()
    return row["calls"] if row else 0


def allow(provider: str) -> bool:
    """Count one call against today's cap. False means the cap is reached: do not call."""
    if used(provider) >= cap(provider):
        return False
    conn = _conn()
    conn.execute(
        "INSERT INTO usage (day, provider, calls) VALUES (?, ?, 1) "
        "ON CONFLICT(day, provider) DO UPDATE SET calls = calls + 1",
        (date.today().isoformat(), provider),
    )
    conn.commit()
    conn.close()
    return True


def cached(key: str):
    conn = _conn()
    row = conn.execute("SELECT value FROM cache WHERE key = ?", (key,)).fetchone()
    conn.close()
    return json.loads(row["value"]) if row else None


def remember(key: str, value) -> None:
    conn = _conn()
    conn.execute("INSERT OR REPLACE INTO cache (key, value) VALUES (?, ?)", (key, json.dumps(value)))
    conn.commit()
    conn.close()
