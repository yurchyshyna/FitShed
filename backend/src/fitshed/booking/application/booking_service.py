"""Прикладний сервіс: завантажує агрегат через порт, викликає доменну дію, зберігає.

Сервіс не знає, яке саме сховище використовується: його передають ззовні.
Пізніше той самий сервіс викликатиме FastAPI, а замість сховища в пам'яті
буде адаптер PostgreSQL.
"""
from collections.abc import Callable
from datetime import datetime

from fitshed.booking.domain import (
    Capacity,
    ClientId,
    DomainEvent,
    GroupClass,
    GroupClassRepository,
    TimeSlot,
)


class BookingService:
    def __init__(self, repository: GroupClassRepository, clock: Callable[[], datetime]) -> None:
        self._repo = repository
        self._clock = clock

    def schedule_class(self, class_id: str, title: str, trainer_id: str,
                       start: datetime, end: datetime, places: int) -> None:
        group_class = GroupClass(class_id, title, trainer_id, TimeSlot(start, end), Capacity(places))
        self._repo.save(group_class)

    def get_class(self, class_id: str) -> GroupClass:
        """Читання заняття (додано в ЛР 5 для HTTP API)."""
        return self._repo.get(class_id)

    def book(self, class_id: str, client_id: str) -> list[DomainEvent]:
        group_class = self._repo.get(class_id)
        group_class.book(ClientId(client_id), self._clock())
        self._repo.save(group_class)
        return group_class.pull_events()

    def cancel_booking(self, class_id: str, client_id: str) -> list[DomainEvent]:
        group_class = self._repo.get(class_id)
        group_class.cancel_booking(ClientId(client_id), self._clock())
        self._repo.save(group_class)
        return group_class.pull_events()

    def cancel_class(self, class_id: str, reason: str) -> list[DomainEvent]:
        group_class = self._repo.get(class_id)
        group_class.cancel(reason, self._clock())
        self._repo.save(group_class)
        return group_class.pull_events()
