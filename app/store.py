"""Domain store: plain SQLite. Profile tables (taste, wardrobe, spend) plus what the
runtime remembers between location updates (saved spots, nudges sent, locations seen).

Separate from LangGraph's own checkpointer. Run `python -m app.store --selftest`
to exercise it against a throwaway database.
"""
import argparse
import json
import os
import sqlite3
import tempfile
from datetime import datetime, timezone

DB_PATH = os.getenv("OO_DB", "store.db")

# How far one rating moves each of the venue's tags: 5 stars = +0.2, 1 star = -0.2.
RATING_STEP = 0.1
NEUTRAL_RATING = 3

SCHEMA = """
CREATE TABLE IF NOT EXISTS taste (
    id      INTEGER PRIMARY KEY CHECK (id = 1),
    tags    TEXT NOT NULL,
    ratings TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS wardrobe (
    id        TEXT PRIMARY KEY,
    colour    TEXT NOT NULL,
    formality TEXT NOT NULL,
    fabric    TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS spend (
    id       INTEGER PRIMARY KEY AUTOINCREMENT,
    kind     TEXT NOT NULL CHECK (kind IN ('alloc', 'spent')),
    category TEXT NOT NULL,
    amount   REAL NOT NULL,
    note     TEXT,
    at       TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS saved (
    id    TEXT PRIMARY KEY,
    name  TEXT NOT NULL,
    lat   REAL NOT NULL,
    lng   REAL NOT NULL,
    hours TEXT
);
CREATE TABLE IF NOT EXISTS nudges (
    id       INTEGER PRIMARY KEY AUTOINCREMENT,
    venue_id TEXT NOT NULL,
    at       TEXT NOT NULL,
    trigger  TEXT
);
CREATE TABLE IF NOT EXISTS kv (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS locations (
    id      INTEGER PRIMARY KEY AUTOINCREMENT,
    at      TEXT NOT NULL,
    lat     REAL NOT NULL,
    lng     REAL NOT NULL,
    weather TEXT
);
"""


def connect(path: str = DB_PATH) -> sqlite3.Connection:
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    # databases created before nudges recorded their trigger
    if "trigger" not in {r["name"] for r in conn.execute("PRAGMA table_info(nudges)")}:
        conn.execute("ALTER TABLE nudges ADD COLUMN trigger TEXT")
    return conn


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


# --- taste ---

def load_taste(conn: sqlite3.Connection) -> dict:
    row = conn.execute("SELECT tags, ratings FROM taste WHERE id = 1").fetchone()
    if row is None:
        return {"tags": {}, "ratings": []}
    return {"tags": json.loads(row["tags"]), "ratings": json.loads(row["ratings"])}


def save_taste(conn: sqlite3.Connection, taste: dict) -> None:
    conn.execute(
        "INSERT INTO taste (id, tags, ratings) VALUES (1, ?, ?) "
        "ON CONFLICT(id) DO UPDATE SET tags = excluded.tags, ratings = excluded.ratings",
        (json.dumps(taste.get("tags", {})), json.dumps(taste.get("ratings", []))),
    )
    conn.commit()


def add_rating(conn: sqlite3.Connection, venue: str, tags: list, rating: int) -> dict:
    """Record a 1-5 rating and nudge the weight of each of the venue's tags."""
    taste = load_taste(conn)
    delta = (rating - NEUTRAL_RATING) * RATING_STEP
    for tag in tags:
        weight = taste["tags"].get(tag, 0.0) + delta
        taste["tags"][tag] = round(max(-1.0, min(1.0, weight)), 3)
    taste["ratings"].append({"venue": venue, "tags": tags, "rating": rating, "at": _now()})
    save_taste(conn, taste)
    return taste


# --- wardrobe ---

def save_wardrobe(conn: sqlite3.Connection, items: list) -> None:
    conn.execute("DELETE FROM wardrobe")
    conn.executemany(
        "INSERT INTO wardrobe (id, colour, formality, fabric) "
        "VALUES (:id, :colour, :formality, :fabric)",
        items,
    )
    conn.commit()


def load_wardrobe(conn: sqlite3.Connection) -> list:
    rows = conn.execute("SELECT id, colour, formality, fabric FROM wardrobe ORDER BY id")
    return [dict(r) for r in rows]


# --- spend ---

def set_alloc(conn: sqlite3.Connection, alloc: dict) -> None:
    conn.execute("DELETE FROM spend WHERE kind = 'alloc'")
    conn.executemany(
        "INSERT INTO spend (kind, category, amount, at) VALUES ('alloc', ?, ?, ?)",
        [(category, amount, _now()) for category, amount in alloc.items()],
    )
    conn.commit()


def add_spend(conn: sqlite3.Connection, category: str, amount: float, note: str = "") -> None:
    conn.execute(
        "INSERT INTO spend (kind, category, amount, note, at) VALUES ('spent', ?, ?, ?, ?)",
        (category, amount, note, _now()),
    )
    conn.commit()


def load_budget(conn: sqlite3.Connection) -> dict:
    budget = {"alloc": {}, "spent": {}}
    rows = conn.execute("SELECT kind, category, SUM(amount) AS total FROM spend GROUP BY kind, category")
    for r in rows:
        budget[r["kind"]][r["category"]] = r["total"]
    return budget


# --- runtime memory: saved spots, nudges sent, locations seen ---
# Timestamps are ISO 8601 strings supplied by the caller, so simulated time works too.

def save_spots(conn: sqlite3.Connection, spots: list) -> None:
    conn.executemany(
        "INSERT OR REPLACE INTO saved (id, name, lat, lng, hours) VALUES (?, ?, ?, ?, ?)",
        [(s["id"], s["name"], s["lat"], s["lng"], s.get("hours")) for s in spots],
    )
    conn.commit()


def load_saved(conn: sqlite3.Connection) -> list:
    return [dict(r) for r in conn.execute("SELECT id, name, lat, lng, hours FROM saved ORDER BY id")]


def add_nudge(conn: sqlite3.Connection, venue_id: str, at: str, trigger: str | None = None) -> None:
    conn.execute("INSERT INTO nudges (venue_id, at, trigger) VALUES (?, ?, ?)", (venue_id, at, trigger))
    conn.commit()


def load_nudges(conn: sqlite3.Connection) -> list:
    return [dict(r) for r in conn.execute("SELECT venue_id, at, trigger FROM nudges ORDER BY id")]


def add_location(conn: sqlite3.Connection, at: str, lat: float, lng: float, weather: str) -> None:
    conn.execute(
        "INSERT INTO locations (at, lat, lng, weather) VALUES (?, ?, ?, ?)", (at, lat, lng, weather)
    )
    conn.commit()


def load_locations(conn: sqlite3.Connection, limit: int = 2000) -> list:
    """The most recent location updates, oldest first."""
    rows = conn.execute(
        "SELECT at, lat, lng, weather FROM locations ORDER BY id DESC LIMIT ?", (limit,)
    ).fetchall()
    return [dict(r) for r in reversed(rows)]


def last_location(conn: sqlite3.Connection) -> dict | None:
    row = conn.execute("SELECT at, lat, lng, weather FROM locations ORDER BY id DESC LIMIT 1").fetchone()
    return dict(row) if row else None


def set_kv(conn: sqlite3.Connection, key: str, value) -> None:
    conn.execute("INSERT OR REPLACE INTO kv (key, value) VALUES (?, ?)", (key, json.dumps(value)))
    conn.commit()


def get_kv(conn: sqlite3.Connection, key: str, default=None):
    row = conn.execute("SELECT value FROM kv WHERE key = ?", (key,)).fetchone()
    return json.loads(row["value"]) if row else default


def forget(conn: sqlite3.Connection) -> None:
    """Wipe everything held about this traveller."""
    for table in ("taste", "wardrobe", "spend", "saved", "nudges", "kv", "locations"):
        conn.execute(f"DELETE FROM {table}")
    conn.commit()


def load_profile(conn: sqlite3.Connection) -> dict:
    """The three profile keys of RoveState, ready to merge into the graph input."""
    return {
        "taste": load_taste(conn),
        "budget": load_budget(conn),
        "wardrobe": load_wardrobe(conn),
    }


def selftest() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        conn = connect(os.path.join(tmp, "selftest.db"))

        save_taste(conn, {"tags": {"beach": 0.6, "trek": 0.4}, "ratings": []})
        before = load_taste(conn)
        assert before == {"tags": {"beach": 0.6, "trek": 0.4}, "ratings": []}, before

        add_rating(conn, "Dudhsagar Falls", ["trek", "waterfall"], 5)
        after = load_taste(conn)
        assert after["tags"]["trek"] == 0.6, after
        assert after["tags"]["waterfall"] == 0.2, after
        assert after["tags"]["beach"] == 0.6, after
        assert len(after["ratings"]) == 1, after

        linen = {"id": "linen-shirt", "colour": "white", "formality": "smart", "fabric": "linen"}
        save_wardrobe(conn, [linen])
        assert load_wardrobe(conn) == [linen]

        set_alloc(conn, {"food": 4000, "activities": 6000})
        add_spend(conn, "food", 850, "fish thali")
        add_spend(conn, "food", 400)
        assert load_budget(conn) == {
            "alloc": {"food": 4000, "activities": 6000},
            "spent": {"food": 1250},
        }

        assert set(load_profile(conn)) == {"taste", "budget", "wardrobe"}
        conn.close()
    print("store selftest ok")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--selftest", action="store_true")
    if parser.parse_args().selftest:
        selftest()
