"""Контракти команд і відповідей між оркестратором та учасниками.

Кожна команда має тип (назва класу), operation_id (ідентифікатор саме цієї операції)
та saga_id (до якого процесу вона належить).
"""
from dataclasses import dataclass


@dataclass(frozen=True)
class Command:
    operation_id: str
    saga_id: str

    @property
    def type(self) -> str:
        return type(self).__name__


@dataclass(frozen=True)
class ReserveSpot(Command):
    """Крок 1. Учасник «Розклад і запис»: зайняти місце на занятті."""
    class_id: str
    client_id: str


@dataclass(frozen=True)
class DebitVisit(Command):
    """Крок 2. Учасник «Абонементи»: списати одне відвідування."""
    membership_id: str
    class_id: str


@dataclass(frozen=True)
class ReleaseSpot(Command):
    """Компенсація кроку 1. Учасник «Розклад і запис»: звільнити місце."""
    class_id: str
    client_id: str


@dataclass(frozen=True)
class Reply:
    """Відповідь учасника. ok=False означає бізнес-відмову (повторювати безглуздо)."""
    operation_id: str
    ok: bool
    reason: str = ""


class TemporaryFailure(Exception):
    """Технічний збій (недоступне сховище, таймаут). Операцію не виконано, її можна повторити."""


class OperationConflictError(Exception):
    """Той самий operation_id прийшов з іншими даними — це помилка відправника."""
