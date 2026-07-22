"""
storage.py — SQLite database layer for the Laptop Monitoring System.

Creates and manages the `screenshots` table.
Used by capture.py (writes) and dashboard.py (reads).
"""

import sqlite3
import threading
from pathlib import Path


class Database:

    def __init__(self, db_path: str = "monitor.db"):
        self.db_path = db_path
        self._lock   = threading.Lock()
        self._init_db()
        print(f"[Storage]  Database ready: {db_path}")

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self):
        with self._lock:
            conn = self._connect()
            conn.execute("""
                CREATE TABLE IF NOT EXISTS screenshots (
                    id             INTEGER PRIMARY KEY AUTOINCREMENT,
                    timestamp      TEXT    NOT NULL,
                    active_window  TEXT,
                    highlight      TEXT,
                    ocr_text       TEXT,
                    image_path     TEXT
                )
            """)
            conn.commit()
            conn.close()

    # ── Write ─────────────────────────────────────────────────

    def insert(self, timestamp: str, active_window: str,
               highlight: str, ocr_text: str, image_path: str = "") -> int:
        with self._lock:
            conn = self._connect()
            cur  = conn.execute(
                """INSERT INTO screenshots
                   (timestamp, active_window, highlight, ocr_text, image_path)
                   VALUES (?, ?, ?, ?, ?)""",
                (timestamp, active_window, highlight, ocr_text, image_path)
            )
            conn.commit()
            row_id = cur.lastrowid
            conn.close()
            return row_id

    def update_highlight(self, row_id: int, highlight: str):
        """Called by capture.py when the async AI result arrives."""
        with self._lock:
            conn = self._connect()
            conn.execute(
                "UPDATE screenshots SET highlight = ? WHERE id = ?",
                (highlight, row_id)
            )
            conn.commit()
            conn.close()

    # ── Read ──────────────────────────────────────────────────

    def get_recent(self, limit: int = 50) -> list[dict]:
        with self._lock:
            conn = self._connect()
            rows = conn.execute(
                """SELECT * FROM screenshots
                   ORDER BY id DESC LIMIT ?""",
                (limit,)
            ).fetchall()
            conn.close()
            return [dict(r) for r in rows]

    def search(self, query: str, limit: int = 100) -> list[dict]:
        with self._lock:
            conn  = self._connect()
            like  = f"%{query}%"
            rows  = conn.execute(
                """SELECT * FROM screenshots
                   WHERE  active_window LIKE ?
                      OR  highlight     LIKE ?
                      OR  ocr_text      LIKE ?
                   ORDER BY id DESC LIMIT ?""",
                (like, like, like, limit)
            ).fetchall()
            conn.close()
            return [dict(r) for r in rows]

    def get_stats(self) -> dict:
        with self._lock:
            conn  = self._connect()
            total = conn.execute("SELECT COUNT(*) FROM screenshots").fetchone()[0]
            first = conn.execute("SELECT MIN(timestamp) FROM screenshots").fetchone()[0]
            last  = conn.execute("SELECT MAX(timestamp) FROM screenshots").fetchone()[0]
            conn.close()
            return {"total": total, "first": first, "last": last}
