"""Оркестратор Saga «запис на заняття з абонементом».

Кроки:
  1. ReserveSpot  → «Розклад і запис» займає місце;
  2. DebitVisit   → «Абонементи» списують відвідування.
Якщо крок 2 не вдався, виконується компенсація кроку 1: ReleaseSpot (звільнити місце).
Якщо й компенсація не вдалася, Saga переходить у стан COMPENSATION_PENDING
і чекає на повтор через retry_pending().
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from .contracts import DebitVisit, ReleaseSpot, ReserveSpot, TemporaryFailure
from .journal import Journal
from .participants import BookingParticipant, MembershipParticipant

MAX_STEP_ATTEMPTS = 3


class SagaStatus(Enum):
    STARTED = "розпочато"
    SPOT_RESERVED = "місце зарезервовано"
    COMPLETED = "завершено успішно"
    REJECTED = "відхилено на першому кроці"
    COMPENSATING = "виконується компенсація"
    COMPENSATION_PENDING = "компенсація очікує повтору"
    COMPENSATED = "скомпенсовано"


@dataclass
class SagaState:
    saga_id: str
    class_id: str
    client_id: str
    membership_id: str
    status: SagaStatus = SagaStatus.STARTED
    reason: str = ""
    compensation_attempts: int = 0

    # operation_id кожного кроку однозначно виводиться з saga_id,
    # тому повтор того самого кроку завжди має той самий operation_id.
    @property
    def reserve_op(self) -> str:
        return f"{self.saga_id}:reserve-spot"

    @property
    def debit_op(self) -> str:
        return f"{self.saga_id}:debit-visit"

    @property
    def release_op(self) -> str:
        return f"{self.saga_id}:release-spot"


class BookingSagaOrchestrator:
    def __init__(self, booking: BookingParticipant, memberships: MembershipParticipant,
                 journal: Journal) -> None:
        self._booking = booking
        self._memberships = memberships
        self._journal = journal
        self._sagas: dict[str, SagaState] = {}  # стан оркестратора: лише в пам'яті

    def get(self, saga_id: str) -> SagaState:
        return self._sagas[saga_id]

    def start(self, saga_id: str, class_id: str, client_id: str, membership_id: str) -> SagaState:
        if saga_id in self._sagas:
            state = self._sagas[saga_id]
            self._journal.log(saga_id, "Оркестратор",
                              f"повторний запуск, процес уже у стані «{state.status.value}», нічого не роблю")
            return state

        state = SagaState(saga_id, class_id, client_id, membership_id)
        self._sagas[saga_id] = state
        self._journal.log(saga_id, "Оркестратор", f"старт: клієнт {client_id} → заняття {class_id}")

        # Крок 1
        reply = self._booking.handle(ReserveSpot(state.reserve_op, saga_id, class_id, client_id))
        if not reply.ok:
            self._set(state, SagaStatus.REJECTED, reply.reason)
            return state
        self._set(state, SagaStatus.SPOT_RESERVED)

        # Крок 2 (технічні збої повторюємо з тим самим operation_id)
        debit = DebitVisit(state.debit_op, saga_id, membership_id, class_id)
        reply = None
        for attempt in range(1, MAX_STEP_ATTEMPTS + 1):
            try:
                reply = self._memberships.handle(debit)
                break
            except TemporaryFailure:
                self._journal.log(saga_id, "Оркестратор", f"DebitVisit: спроба {attempt} не вдалася")
        if reply is not None and reply.ok:
            self._set(state, SagaStatus.COMPLETED)
            return state

        state.reason = reply.reason if reply is not None else "Абонементи недоступні"
        self._compensate(state)
        return state

    def retry_pending(self) -> list[SagaState]:
        """Повторює всі компенсації, що очікують. Викликається, наприклад, за розкладом."""
        pending = [s for s in self._sagas.values() if s.status is SagaStatus.COMPENSATION_PENDING]
        for state in pending:
            self._journal.log(state.saga_id, "Оркестратор", "повтор компенсації")
            self._compensate(state)
        return pending

    def _compensate(self, state: SagaState) -> None:
        self._set(state, SagaStatus.COMPENSATING, state.reason)
        state.compensation_attempts += 1
        try:
            self._booking.handle(ReleaseSpot(state.release_op, state.saga_id, state.class_id, state.client_id))
        except TemporaryFailure:
            self._set(state, SagaStatus.COMPENSATION_PENDING, state.reason)
            return
        self._set(state, SagaStatus.COMPENSATED, state.reason)

    def _set(self, state: SagaState, status: SagaStatus, reason: str = "") -> None:
        state.status = status
        state.reason = reason
        suffix = f" (причина: {reason})" if reason else ""
        self._journal.log(state.saga_id, "Оркестратор", f"стан → {status.name}{suffix}")
