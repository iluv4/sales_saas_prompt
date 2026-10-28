"""SQLite storage for the CRM: leads, emails, activities, meetings and tasks."""

from __future__ import annotations

import sqlite3
import threading
from typing import Any

SCHEMA = """
CREATE TABLE IF NOT EXISTS leads (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    name          TEXT NOT NULL,
    title         TEXT NOT NULL DEFAULT '',
    company       TEXT NOT NULL DEFAULT '',
    email         TEXT NOT NULL DEFAULT '',
    phone         TEXT NOT NULL DEFAULT '',
    website       TEXT NOT NULL DEFAULT '',
    linkedin      TEXT NOT NULL DEFAULT '',
    notes         TEXT NOT NULL DEFAULT '',
    stage         TEXT NOT NULL DEFAULT 'new',
    research      TEXT NOT NULL DEFAULT '',
    slack_channel TEXT NOT NULL DEFAULT '',
    created_at    TEXT NOT NULL,
    updated_at    TEXT NOT NULL
);

-- status: draft | sent | skipped. step 0 is the intro, 1+ are follow-ups.
CREATE TABLE IF NOT EXISTS emails (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    lead_id     INTEGER NOT NULL REFERENCES leads(id),
    step        INTEGER NOT NULL DEFAULT 0,
    subject     TEXT NOT NULL,
    body        TEXT NOT NULL,
    status      TEXT NOT NULL DEFAULT 'draft',
    message_id  TEXT NOT NULL DEFAULT '',
    in_reply_to TEXT NOT NULL DEFAULT '',
    created_at  TEXT NOT NULL,
    sent_at     TEXT
);

-- type: email_sent | reply | call | meeting_booked | stage_change
CREATE TABLE IF NOT EXISTS activities (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    lead_id    INTEGER NOT NULL REFERENCES leads(id),
    type       TEXT NOT NULL,
    outcome    TEXT NOT NULL DEFAULT '',
    notes      TEXT NOT NULL DEFAULT '',
    summary    TEXT NOT NULL DEFAULT '',
    data       TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL
);

-- status: scheduled | canceled
CREATE TABLE IF NOT EXISTS meetings (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    lead_id      INTEGER NOT NULL REFERENCES leads(id),
    starts_at    TEXT NOT NULL,
    duration_min INTEGER NOT NULL DEFAULT 30,
    title        TEXT NOT NULL DEFAULT '',
    status       TEXT NOT NULL DEFAULT 'scheduled',
    created_at   TEXT NOT NULL
);

-- kind: followup_email | call | email | meeting | reply. status: open | done | canceled
CREATE TABLE IF NOT EXISTS tasks (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    lead_id    INTEGER NOT NULL REFERENCES leads(id),
    kind       TEXT NOT NULL,
    title      TEXT NOT NULL,
    due_date   TEXT NOT NULL,
    step       INTEGER NOT NULL DEFAULT 0,
    status     TEXT NOT NULL DEFAULT 'open',
    created_at TEXT NOT NULL,
    done_at    TEXT
);

CREATE INDEX IF NOT EXISTS idx_activities_created ON activities(created_at);
CREATE INDEX IF NOT EXISTS idx_tasks_due ON tasks(status, due_date);
CREATE INDEX IF NOT EXISTS idx_meetings_start ON meetings(starts_at);
"""


class DB:
    """Thread-safe wrapper: Slack handlers, the scheduler and the dashboard share one connection."""

    def __init__(self, path: str = "sales.db"):
        self._conn = sqlite3.connect(path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._lock = threading.Lock()
        with self._lock:
            self._conn.executescript(SCHEMA)
            self._conn.commit()

    def query(self, sql: str, params: tuple | list = ()) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._conn.execute(sql, params).fetchall()
        return [dict(row) for row in rows]

    def one(self, sql: str, params: tuple | list = ()) -> dict[str, Any] | None:
        rows = self.query(sql, params)
        return rows[0] if rows else None

    def scalar(self, sql: str, params: tuple | list = ()) -> Any:
        with self._lock:
            row = self._conn.execute(sql, params).fetchone()
        return row[0] if row else None

    def execute(self, sql: str, params: tuple | list = ()) -> int:
        with self._lock:
            cur = self._conn.execute(sql, params)
            self._conn.commit()
        return cur.rowcount

    def insert(self, table: str, **fields: Any) -> int:
        cols = ", ".join(fields)
        marks = ", ".join("?" for _ in fields)
        with self._lock:
            cur = self._conn.execute(f"INSERT INTO {table} ({cols}) VALUES ({marks})", list(fields.values()))
            self._conn.commit()
        return cur.lastrowid

    def update(self, table: str, row_id: int, **fields: Any) -> None:
        sets = ", ".join(f"{col} = ?" for col in fields)
        self.execute(f"UPDATE {table} SET {sets} WHERE id = ?", [*fields.values(), row_id])

    def get(self, table: str, row_id: int) -> dict[str, Any] | None:
        return self.one(f"SELECT * FROM {table} WHERE id = ?", (row_id,))
