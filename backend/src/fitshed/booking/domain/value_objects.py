"""Об'єкти-значення: не мають власної ідентичності й порівнюються за значенням."""
from dataclasses import dataclass
from datetime import datetime, timedelta

from .errors import InvalidValueError

MAX_CLASS_DURATION = timedelta(hours=3)
MAX_CAPACITY = 40  # найбільший зал групових програм у клубі


@dataclass(frozen=True)
class TimeSlot:
    """Час проведення заняття. Кінець завжди пізніше за початок."""

    start: datetime
    end: datetime

    def __post_init__(self) -> None:
        if self.end <= self.start:
            raise InvalidValueError("Кінець заняття має бути пізніше за початок")
        if self.end - self.start > MAX_CLASS_DURATION:
            raise InvalidValueError("Групове заняття не може тривати довше 3 годин")

    def hours_until_start(self, now: datetime) -> float:
        return (self.start - now).total_seconds() / 3600


@dataclass(frozen=True)
class Capacity:
    """Кількість місць на занятті."""

    value: int

    def __post_init__(self) -> None:
        if not 1 <= self.value <= MAX_CAPACITY:
            raise InvalidValueError(f"Кількість місць має бути від 1 до {MAX_CAPACITY}")


@dataclass(frozen=True)
class ClientId:
    """Ідентифікатор клієнта. У цьому контексті потрібен лише ідентифікатор, а не профіль."""

    value: str

    def __post_init__(self) -> None:
        if not self.value.strip():
            raise InvalidValueError("Ідентифікатор клієнта не може бути порожнім")
