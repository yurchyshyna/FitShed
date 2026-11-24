"""Діагностичний журнал виконання.

Це НЕ Event Sourcing: стан учасників зберігається окремо, а журнал лише
пояснює людині, що відбувалося. З нього стан не відновлюється.
"""
from dataclasses import dataclass


@dataclass(frozen=True)
class JournalEntry:
    seq: int
    saga_id: str
    actor: str
    message: str
    operation_id: str = ""

    def __str__(self) -> str:
        op = f" [{self.operation_id}]" if self.operation_id else ""
        return f"{self.seq:02d}. {self.saga_id} | {self.actor:<11} | {self.message}{op}"


class Journal:
    def __init__(self) -> None:
        self.entries: list[JournalEntry] = []

    def log(self, saga_id: str, actor: str, message: str, operation_id: str = "") -> None:
        self.entries.append(JournalEntry(len(self.entries) + 1, saga_id, actor, message, operation_id))

    def lines(self) -> list[str]:
        return [str(e) for e in self.entries]
