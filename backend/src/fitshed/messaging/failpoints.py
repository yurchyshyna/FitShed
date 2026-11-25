"""Керовані точки збою для перевірок.

Задаються змінною оточення FITSHED_FAILPOINT (через кому) або в тестах напряму:
  before_commit             — «падіння» після бізнес-змін, але до COMMIT;
  after_commit_before_ack   — «падіння» після COMMIT, але до ACK брокеру;
  temporary_error:N         — перші N спроб запису в сховище дають тимчасову помилку.
Кожна точка спрацьовує один раз на процес (temporary_error — N разів).
"""
from __future__ import annotations

import os
import sqlite3


class SimulatedCrash(BaseException):
    """Імітація аварійного завершення процесу. BaseException, щоб її не «проковтнули» звичайні except."""


class Failpoints:
    def __init__(self, spec: str | None = None) -> None:
        spec = os.environ.get("FITSHED_FAILPOINT", "") if spec is None else spec
        self._active: set[str] = set()
        self._temporary_left = 0
        for item in filter(None, (s.strip() for s in spec.split(","))):
            if item.startswith("temporary_error"):
                self._temporary_left = int(item.partition(":")[2] or 1)
            else:
                self._active.add(item)

    def hit(self, name: str) -> None:
        if name in self._active:
            self._active.discard(name)
            raise SimulatedCrash(name)

    def maybe_temporary_error(self) -> None:
        if self._temporary_left > 0:
            self._temporary_left -= 1
            raise sqlite3.OperationalError("database is locked (імітація тимчасової помилки)")
