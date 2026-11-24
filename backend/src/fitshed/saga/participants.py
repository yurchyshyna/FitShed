"""Учасники Saga. Кожен володіє лише своїми даними і має власний стан.

- BookingParticipant — контекст «Розклад і запис», дані: заняття та записи.
- MembershipParticipant — контекст «Абонементи», дані: абонементи та залишки відвідувань.

Обидва ідемпотентні: результат кожної операції запам'ятовується за operation_id,
і повторна команда повертає той самий результат без нового бізнес-ефекту.
"""
from __future__ import annotations

from collections.abc import Callable
from datetime import datetime

from fitshed.booking.domain import ClientId, DomainError, GroupClassRepository
from fitshed.memberships.domain import Membership, MembershipError

from .contracts import (
    Command,
    DebitVisit,
    OperationConflictError,
    ReleaseSpot,
    Reply,
    ReserveSpot,
    TemporaryFailure,
)
from .journal import Journal


class Participant:
    name = "учасник"

    def __init__(self, journal: Journal) -> None:
        self._journal = journal
        self._processed: dict[str, tuple[Command, Reply]] = {}
        self._failures: dict[str, int] = {}  # тип команди → скільки разів поспіль «впасти»

    def fail_next(self, command_type: str, times: int = 1) -> None:
        """Ін'єкція відмови для перевірок: наступні `times` команд цього типу дадуть технічний збій."""
        self._failures[command_type] = times

    def handle(self, cmd: Command) -> Reply:
        # 1. Ідемпотентність: таку операцію вже виконано — повертаємо збережений результат.
        if cmd.operation_id in self._processed:
            previous_cmd, previous_reply = self._processed[cmd.operation_id]
            if previous_cmd != cmd:
                raise OperationConflictError(f"{cmd.operation_id} уже використано з іншими даними")
            self._journal.log(cmd.saga_id, self.name,
                              f"{cmd.type}: повтор, повертаю попередній результат без нового ефекту",
                              cmd.operation_id)
            return previous_reply

        # 2. Імітація технічного збою. Нічого не виконано й не запам'ятано, тому повтор можливий.
        if self._failures.get(cmd.type, 0) > 0:
            self._failures[cmd.type] -= 1
            self._journal.log(cmd.saga_id, self.name, f"{cmd.type}: технічний збій (сховище недоступне)",
                              cmd.operation_id)
            raise TemporaryFailure(f"{self.name}: {cmd.type} не виконано")

        # 3. Виконання. Бізнес-відмову теж запам'ятовуємо: повтор дасть ту саму відмову.
        reply = self._execute(cmd)
        self._processed[cmd.operation_id] = (cmd, reply)
        outcome = "успішно" if reply.ok else f"відмова: {reply.reason}"
        self._journal.log(cmd.saga_id, self.name, f"{cmd.type}: {outcome}", cmd.operation_id)
        return reply

    def _execute(self, cmd: Command) -> Reply:
        raise NotImplementedError


class BookingParticipant(Participant):
    name = "Запис"

    def __init__(self, repository: GroupClassRepository, clock: Callable[[], datetime],
                 journal: Journal) -> None:
        super().__init__(journal)
        self._repo = repository
        self._clock = clock

    def _execute(self, cmd: Command) -> Reply:
        if isinstance(cmd, ReserveSpot):
            group_class = self._repo.get(cmd.class_id)
            if group_class.free_places == 0:
                return Reply(cmd.operation_id, False, "немає вільних місць")
            try:
                group_class.book(ClientId(cmd.client_id), self._clock())
            except DomainError as error:
                return Reply(cmd.operation_id, False, str(error))
            self._repo.save(group_class)
            return Reply(cmd.operation_id, True)

        if isinstance(cmd, ReleaseSpot):
            group_class = self._repo.get(cmd.class_id)
            if ClientId(cmd.client_id) in group_class.booked:
                group_class.cancel_booking(ClientId(cmd.client_id), self._clock())
                self._repo.save(group_class)
            # Якщо клієнта вже немає серед записаних, мета компенсації досягнута.
            return Reply(cmd.operation_id, True)

        raise TypeError(f"{self.name} не обробляє {cmd.type}")


class MembershipParticipant(Participant):
    name = "Абонементи"

    def __init__(self, memberships: dict[str, Membership], clock: Callable[[], datetime],
                 journal: Journal) -> None:
        super().__init__(journal)
        self._memberships = memberships  # власне сховище учасника
        self._clock = clock

    def get(self, membership_id: str) -> Membership:
        return self._memberships[membership_id]

    def _execute(self, cmd: Command) -> Reply:
        if isinstance(cmd, DebitVisit):
            membership = self._memberships.get(cmd.membership_id)
            if membership is None:
                return Reply(cmd.operation_id, False, "абонемент не знайдено")
            try:
                membership.debit_visit(cmd.class_id, self._clock().date())
            except MembershipError as error:
                return Reply(cmd.operation_id, False, str(error))
            return Reply(cmd.operation_id, True)

        raise TypeError(f"{self.name} не обробляє {cmd.type}")
