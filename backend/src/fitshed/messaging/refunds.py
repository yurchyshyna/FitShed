"""Споживач у контексті «Абонементи»: повертає по одному відвідуванню кожному,
хто був записаний на скасоване заняття.

Бізнес-результат і event_id записуються в ОДНІЙ транзакції SQLite:
  - processed_events.event_id — первинний ключ (реєстр оброблених подій);
  - visit_refunds має UNIQUE(class_id, client_id) — друга лінія захисту,
    якщо ту саму подію опублікували повторно з іншим event_id.
Файл бази переживає перезапуск споживача.
"""
from __future__ import annotations

import sqlite3
from datetime import datetime
from pathlib import Path

from .envelope import ClassCancelledEvent
from .failpoints import Failpoints

SCHEMA = """
CREATE TABLE IF NOT EXISTS memberships (
    client_id   TEXT PRIMARY KEY,
    visits_left INTEGER NOT NULL CHECK (visits_left >= 0)
);
CREATE TABLE IF NOT EXISTS processed_events (
    event_id     TEXT PRIMARY KEY,
    event_type   TEXT NOT NULL,
    processed_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS visit_refunds (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    event_id    TEXT NOT NULL REFERENCES processed_events(event_id),
    class_id    TEXT NOT NULL,
    client_id   TEXT NOT NULL,
    refunded_at TEXT NOT NULL,
    UNIQUE (class_id, client_id)
);
CREATE TABLE IF NOT EXISTS failed_messages (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    event_id  TEXT,
    body      TEXT NOT NULL,
    reason    TEXT NOT NULL,
    attempts  INTEGER NOT NULL,
    failed_at TEXT NOT NULL
);
"""


class RefundStore:
    def __init__(self, path: str | Path, failpoints: Failpoints | None = None) -> None:
        self.path = str(path)
        self._failpoints = failpoints or Failpoints()
        # isolation_level=None: транзакціями керуємо явно (BEGIN ... COMMIT)
        self._db = sqlite3.connect(self.path, isolation_level=None, timeout=1)
        self._db.executescript(SCHEMA)

    def close(self) -> None:
        self._db.close()

    def seed(self, balances: dict[str, int]) -> None:
        self._db.executemany("INSERT OR REPLACE INTO memberships VALUES (?, ?)", balances.items())

    def apply(self, event: ClassCancelledEvent) -> str:
        """Повертає «applied» або «duplicate». При будь-якому винятку транзакція відкочується."""
        now = datetime.now().isoformat(timespec="seconds")
        self._failpoints.maybe_temporary_error()
        self._db.execute("BEGIN IMMEDIATE")
        try:
            try:
                self._db.execute("INSERT INTO processed_events VALUES (?, ?, ?)",
                                 (event.event_id, "GroupClassCancelled", now))
            except sqlite3.IntegrityError:
                self._db.execute("ROLLBACK")
                return "duplicate"

            for client in event.booked_client_ids:
                inserted = self._db.execute(
                    "INSERT OR IGNORE INTO visit_refunds (event_id, class_id, client_id, refunded_at) "
                    "VALUES (?, ?, ?, ?)", (event.event_id, event.class_id, client, now)).rowcount
                if inserted:  # повернення для цього клієнта й заняття ще не було
                    self._db.execute("INSERT OR IGNORE INTO memberships VALUES (?, 0)", (client,))
                    self._db.execute("UPDATE memberships SET visits_left = visits_left + 1 WHERE client_id = ?",
                                     (client,))

            self._failpoints.hit("before_commit")
            self._db.execute("COMMIT")
            return "applied"
        except BaseException:
            if self._db.in_transaction:
                self._db.execute("ROLLBACK")
            raise

    def record_failure(self, event_id: str | None, body: bytes, reason: str, attempts: int) -> None:
        self._db.execute("INSERT INTO failed_messages (event_id, body, reason, attempts, failed_at) VALUES (?, ?, ?, ?, ?)",
                         (event_id, body.decode("utf-8", errors="replace"), reason, attempts,
                          datetime.now().isoformat(timespec="seconds")))

    # ---------- читання стану для перевірок ----------
    def snapshot(self) -> dict:
        q = lambda sql: self._db.execute(sql).fetchall()  # noqa: E731
        return {
            "processed_events": [r[0] for r in q("SELECT event_id FROM processed_events ORDER BY event_id")],
            "refunds": [tuple(r) for r in q("SELECT class_id, client_id FROM visit_refunds ORDER BY id")],
            "balances": dict(q("SELECT client_id, visits_left FROM memberships ORDER BY client_id")),
            "failed": [tuple(r) for r in q("SELECT event_id, reason, attempts FROM failed_messages ORDER BY id")],
        }
