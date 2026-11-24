"""Агрегат «Абонемент». Власник даних про залишок відвідувань — лише цей контекст."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date


class MembershipError(Exception):
    """Бізнес-причина, з якої відвідування списати не можна."""


class NoVisitsLeftError(MembershipError):
    pass


class MembershipExpiredError(MembershipError):
    pass


@dataclass
class Membership:
    """Абонемент на кількість відвідувань з датою закінчення.

    Інваріант: залишок відвідувань ніколи не буває від'ємним.
    """

    membership_id: str
    client_id: str
    visits_left: int
    valid_until: date
    debited_for: list[str] = field(default_factory=list)  # заняття, за які вже списано

    def debit_visit(self, class_id: str, today: date) -> None:
        if today > self.valid_until:
            raise MembershipExpiredError(f"Абонемент діяв до {self.valid_until}")
        if self.visits_left <= 0:
            raise NoVisitsLeftError("На абонементі не залишилося відвідувань")
        self.visits_left -= 1
        self.debited_for.append(class_id)
