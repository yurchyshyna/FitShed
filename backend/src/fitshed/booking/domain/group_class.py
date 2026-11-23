"""Агрегат «Групове заняття» — корінь, що захищає правила запису."""
from __future__ import annotations

from datetime import datetime
from enum import Enum

from .errors import ForbiddenTransitionError, InvalidValueError, InvariantViolationError
from .events import (
    BookingCancelled,
    ClientAddedToWaitlist,
    ClientBooked,
    ClientPromotedFromWaitlist,
    DomainEvent,
    GroupClassCancelled,
)
from .value_objects import Capacity, ClientId, TimeSlot

LATE_CANCELLATION_HOURS = 3


class ClassStatus(Enum):
    SCHEDULED = "заплановане"
    CANCELLED = "скасоване"
    COMPLETED = "завершене"


# Дозволені переходи стану. Скасоване й завершене заняття є кінцевими станами.
ALLOWED_TRANSITIONS: dict[ClassStatus, set[ClassStatus]] = {
    ClassStatus.SCHEDULED: {ClassStatus.CANCELLED, ClassStatus.COMPLETED},
    ClassStatus.CANCELLED: set(),
    ClassStatus.COMPLETED: set(),
}


class GroupClass:
    """Групове заняття з обмеженою кількістю місць і листом очікування.

    Інваріанти:
      1. Кількість записаних клієнтів не перевищує кількість місць.
      2. Клієнт присутній на занятті не більше одного разу:
         або серед записаних, або в листі очікування.
      3. Записи змінюються лише в запланованому занятті і до його початку.
    """

    def __init__(self, class_id: str, title: str, trainer_id: str,
                 slot: TimeSlot, capacity: Capacity) -> None:
        if not title.strip():
            raise InvalidValueError("Назва заняття не може бути порожньою")
        self.id = class_id
        self.title = title
        self.trainer_id = trainer_id
        self.slot = slot
        self.capacity = capacity
        self.status = ClassStatus.SCHEDULED
        self._booked: list[ClientId] = []
        self._waitlist: list[ClientId] = []
        self._events: list[DomainEvent] = []

    # ---------- стан для читання ----------
    @property
    def booked(self) -> tuple[ClientId, ...]:
        return tuple(self._booked)

    @property
    def waitlist(self) -> tuple[ClientId, ...]:
        return tuple(self._waitlist)

    @property
    def free_places(self) -> int:
        return self.capacity.value - len(self._booked)

    # ---------- поведінка ----------
    def book(self, client: ClientId, now: datetime) -> None:
        """Записує клієнта; якщо місць немає, ставить у лист очікування."""
        self._ensure_bookings_can_change(now)
        if client in self._booked or client in self._waitlist:
            raise InvariantViolationError("Клієнт уже записаний на це заняття або стоїть у листі очікування")

        if self.free_places > 0:
            self._booked.append(client)
            self._record(ClientBooked(self.id, now, client))
        else:
            self._waitlist.append(client)
            self._record(ClientAddedToWaitlist(self.id, now, client, position=len(self._waitlist)))
        self._check_invariants()

    def cancel_booking(self, client: ClientId, now: datetime) -> None:
        """Скасовує запис клієнта і переводить першого з листа очікування на звільнене місце."""
        self._ensure_bookings_can_change(now)
        if client in self._waitlist:
            self._waitlist.remove(client)
            return
        if client not in self._booked:
            raise InvariantViolationError("Клієнт не записаний на це заняття")

        self._booked.remove(client)
        late = self.slot.hours_until_start(now) < LATE_CANCELLATION_HOURS
        self._record(BookingCancelled(self.id, now, client, late=late))

        if self._waitlist:
            promoted = self._waitlist.pop(0)
            self._booked.append(promoted)
            self._record(ClientPromotedFromWaitlist(self.id, now, promoted))
        self._check_invariants()

    def change_capacity(self, new_capacity: Capacity, now: datetime) -> None:
        """Змінює кількість місць, наприклад при переносі в менший зал."""
        if self.status is not ClassStatus.SCHEDULED:
            raise ForbiddenTransitionError("Змінювати кількість місць можна лише в запланованому занятті")
        if new_capacity.value < len(self._booked):
            raise InvariantViolationError(
                f"На занятті вже {len(self._booked)} записаних, не можна зменшити кількість місць до {new_capacity.value}"
            )
        self.capacity = new_capacity
        # Якщо місць побільшало, переводимо людей з листа очікування.
        while self.free_places > 0 and self._waitlist:
            promoted = self._waitlist.pop(0)
            self._booked.append(promoted)
            self._record(ClientPromotedFromWaitlist(self.id, now, promoted))
        self._check_invariants()

    def cancel(self, reason: str, now: datetime) -> None:
        """Скасовує заняття (тренер захворів, зал зачинено)."""
        self._transition_to(ClassStatus.CANCELLED)
        affected = tuple(self._booked) + tuple(self._waitlist)
        self._record(GroupClassCancelled(self.id, now, reason, affected))

    def complete(self, now: datetime) -> None:
        """Позначає заняття завершеним; можливо лише після його закінчення."""
        if now < self.slot.end:
            raise ForbiddenTransitionError("Заняття ще не закінчилося")
        self._transition_to(ClassStatus.COMPLETED)

    def pull_events(self) -> list[DomainEvent]:
        """Віддає накопичені події й очищає список (їх забирає прикладний сервіс)."""
        events, self._events = self._events, []
        return events

    # ---------- внутрішні перевірки ----------
    def _transition_to(self, new_status: ClassStatus) -> None:
        if new_status not in ALLOWED_TRANSITIONS[self.status]:
            raise ForbiddenTransitionError(
                f"Перехід «{self.status.value}» → «{new_status.value}» заборонено"
            )
        self.status = new_status

    def _ensure_bookings_can_change(self, now: datetime) -> None:
        if self.status is not ClassStatus.SCHEDULED:
            raise ForbiddenTransitionError(f"Заняття {self.status.value}, змінювати записи не можна")
        if now >= self.slot.start:
            raise ForbiddenTransitionError("Заняття вже почалося, змінювати записи не можна")

    def _check_invariants(self) -> None:
        # Запобіжник: якщо через помилку в коді правило порушено, краще впасти, ніж зберегти.
        if len(self._booked) > self.capacity.value:
            raise InvariantViolationError("Записаних більше, ніж місць")
        everyone = self._booked + self._waitlist
        if len(everyone) != len(set(everyone)):
            raise InvariantViolationError("Клієнт присутній на занятті двічі")

    def _record(self, event: DomainEvent) -> None:
        self._events.append(event)
