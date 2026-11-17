"""SQLite: настройки и история заходов."""
from __future__ import annotations

import sqlite3
from pathlib import Path


class Database:
    def __init__(self, path: Path | str) -> None:
        self.conn = sqlite3.connect(str(path))
        self.conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS settings (
                key   TEXT PRIMARY KEY,
                value TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS sessions (
                id       INTEGER PRIMARY KEY AUTOINCREMENT,
                start_ts REAL NOT NULL,
                end_ts   REAL NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_sessions_end ON sessions (end_ts);
            """
        )
        self.conn.commit()

    # ---------- настройки ----------
    def get(self, key: str, default: str = "") -> str:
        row = self.conn.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
        return row[0] if row else default

    def set(self, key: str, value: object) -> None:
        self.conn.execute(
            "INSERT INTO settings (key, value) VALUES (?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (key, str(value)),
        )
        self.conn.commit()

    def delete(self, key: str) -> None:
        self.conn.execute("DELETE FROM settings WHERE key = ?", (key,))
        self.conn.commit()

    # ---------- история заходов ----------
    def add_session(self, start_ts: float, end_ts: float) -> None:
        self.conn.execute("INSERT INTO sessions (start_ts, end_ts) VALUES (?, ?)", (start_ts, end_ts))
        self.conn.commit()

    def sessions_since(self, since_ts: float) -> list[tuple[float, float]]:
        rows = self.conn.execute(
            "SELECT start_ts, end_ts FROM sessions WHERE end_ts >= ? ORDER BY end_ts", (since_ts,)
        ).fetchall()
        return [(r[0], r[1]) for r in rows]

    def last_sessions(self, limit: int = 10) -> list[tuple[float, float]]:
        rows = self.conn.execute(
            "SELECT start_ts, end_ts FROM sessions ORDER BY end_ts DESC LIMIT ?", (limit,)
        ).fetchall()
        return [(r[0], r[1]) for r in rows]
