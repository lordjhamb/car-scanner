"""SQLite storage with forward-only migrations.

One connection per request (see ``api.deps.get_db``). SQLite in WAL mode handles
the read-mostly, single-machine workload of this app comfortably.
"""
from __future__ import annotations

import json
import sqlite3
import time
from pathlib import Path

MIGRATIONS: list[str] = [
    # 1 - initial schema
    """
    CREATE TABLE users (
        id INTEGER PRIMARY KEY,
        email TEXT NOT NULL UNIQUE COLLATE NOCASE,
        name TEXT NOT NULL DEFAULT '',
        password_hash TEXT NOT NULL,
        unit_pref TEXT NOT NULL DEFAULT 'mi' CHECK (unit_pref IN ('mi','km')),
        created_at REAL NOT NULL
    );
    CREATE TABLE sessions (
        id INTEGER PRIMARY KEY,
        user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        token_hash TEXT NOT NULL UNIQUE,
        csrf_token TEXT NOT NULL,
        created_at REAL NOT NULL,
        expires_at REAL NOT NULL,
        user_agent TEXT NOT NULL DEFAULT ''
    );
    CREATE TABLE cars (
        id INTEGER PRIMARY KEY,
        user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        vin TEXT NOT NULL,
        make TEXT NOT NULL DEFAULT '',
        model TEXT NOT NULL DEFAULT '',
        year INTEGER,
        engine TEXT NOT NULL DEFAULT '',
        nickname TEXT NOT NULL DEFAULT '',
        created_at REAL NOT NULL,
        updated_at REAL NOT NULL,
        UNIQUE (user_id, vin)
    );
    CREATE TABLE scans (
        id INTEGER PRIMARY KEY,
        user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        car_id INTEGER NOT NULL REFERENCES cars(id) ON DELETE CASCADE,
        kind TEXT NOT NULL,
        odometer_km REAL,
        data TEXT NOT NULL,
        created_at REAL NOT NULL
    );
    CREATE INDEX scans_car ON scans(car_id, created_at);
    CREATE TABLE runs (
        id INTEGER PRIMARY KEY,
        user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        car_id INTEGER REFERENCES cars(id) ON DELETE CASCADE,
        goal TEXT NOT NULL,
        plan TEXT NOT NULL,
        results TEXT NOT NULL DEFAULT '{}',
        status TEXT NOT NULL DEFAULT 'running',
        created_at REAL NOT NULL,
        updated_at REAL NOT NULL
    );
    CREATE TABLE photos (
        id INTEGER PRIMARY KEY,
        user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        car_id INTEGER NOT NULL REFERENCES cars(id) ON DELETE CASCADE,
        slot TEXT NOT NULL,
        filename TEXT NOT NULL,
        content_type TEXT NOT NULL,
        size INTEGER NOT NULL,
        width INTEGER, height INTEGER,
        created_at REAL NOT NULL
    );
    CREATE TABLE reports (
        id INTEGER PRIMARY KEY,
        user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        car_id INTEGER NOT NULL REFERENCES cars(id) ON DELETE CASCADE,
        kind TEXT NOT NULL,            -- mileage | photos
        data TEXT NOT NULL,
        created_at REAL NOT NULL
    );
    CREATE TABLE service_records (
        id INTEGER PRIMARY KEY,
        user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        car_id INTEGER NOT NULL REFERENCES cars(id) ON DELETE CASCADE,
        date TEXT NOT NULL,
        km REAL NOT NULL,
        note TEXT NOT NULL DEFAULT '',
        created_at REAL NOT NULL
    );
    CREATE TABLE ai_settings (
        user_id INTEGER PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
        provider TEXT NOT NULL DEFAULT 'claude',
        claude_model TEXT NOT NULL DEFAULT '',
        openai_model TEXT NOT NULL DEFAULT '',
        anthropic_key TEXT,            -- Fernet-encrypted
        openai_key TEXT,               -- Fernet-encrypted
        perms TEXT NOT NULL DEFAULT '{}',
        memory_enabled INTEGER NOT NULL DEFAULT 1,
        hide_vin INTEGER NOT NULL DEFAULT 0,
        monthly_limit_usd REAL
    );
    CREATE TABLE conversations (
        id INTEGER PRIMARY KEY,
        user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        car_id INTEGER REFERENCES cars(id) ON DELETE SET NULL,
        title TEXT NOT NULL DEFAULT 'New conversation',
        created_at REAL NOT NULL,
        updated_at REAL NOT NULL
    );
    CREATE TABLE messages (
        id INTEGER PRIMARY KEY,
        conversation_id INTEGER NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
        role TEXT NOT NULL CHECK (role IN ('user','assistant')),
        text TEXT NOT NULL,
        meta TEXT NOT NULL DEFAULT '{}',   -- tool log, sources, plan, pending action ids, provider
        created_at REAL NOT NULL
    );
    CREATE TABLE memories (
        id INTEGER PRIMARY KEY,
        user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        car_id INTEGER REFERENCES cars(id) ON DELETE CASCADE,
        fact TEXT NOT NULL,
        created_at REAL NOT NULL
    );
    CREATE TABLE pending_actions (
        id INTEGER PRIMARY KEY,
        user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        car_id INTEGER REFERENCES cars(id) ON DELETE CASCADE,
        kind TEXT NOT NULL,
        payload TEXT NOT NULL,
        status TEXT NOT NULL DEFAULT 'pending',
        created_at REAL NOT NULL
    );
    """,
]


def connect(path: Path | str) -> sqlite3.Connection:
    conn = sqlite3.connect(str(path), timeout=10, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA busy_timeout = 5000")
    return conn


def migrate(path: Path | str) -> int:
    conn = connect(path)
    try:
        conn.execute("PRAGMA journal_mode = WAL")
        version = conn.execute("PRAGMA user_version").fetchone()[0]
        for i, sql in enumerate(MIGRATIONS[version:], start=version + 1):
            conn.executescript("BEGIN;" + sql + f"; PRAGMA user_version = {i}; COMMIT;")
        return conn.execute("PRAGMA user_version").fetchone()[0]
    finally:
        conn.close()


def now() -> float:
    return time.time()


def dumps(obj) -> str:
    return json.dumps(obj, default=str, separators=(",", ":"))


def loads(s: str | None, default=None):
    if not s:
        return default
    try:
        return json.loads(s)
    except ValueError:
        return default


def row(r: sqlite3.Row | None) -> dict | None:
    return dict(r) if r is not None else None
