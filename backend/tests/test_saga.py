"""Перевірки Saga «запис на заняття з абонементом» (ЛР 3)."""
import pytest

from fitshed.saga.contracts import DebitVisit, OperationConflictError, ReleaseSpot, ReserveSpot
from fitshed.saga.orchestrator import BookingSagaOrchestrator, SagaStatus
from fitshed.saga.simulation import CLASS_ID, CLIENT_ID, MEMBERSHIP_ID, build


def start(sim, saga_id="saga-1"):
    return sim.orchestrator.start(saga_id, CLASS_ID, CLIENT_ID, MEMBERSHIP_ID)


# ---------- Перевірка 1: успішний процес ----------
def test_success_reserves_spot_and_debits_visit():
    sim = build(visits_left=5)
    assert sim.snapshot() == {"записані на заняття": [], "вільних місць": 3, "відвідувань на абонементі": 5}

    saga = start(sim)

    assert saga.status is SagaStatus.COMPLETED
    assert sim.snapshot() == {"записані на заняття": ["anna"], "вільних місць": 2, "відвідувань на абонементі": 4}


# ---------- Перевірка 2: відмова другого кроку з компенсацією ----------
def test_second_step_failure_releases_spot():
    sim = build(visits_left=0)
    before = sim.snapshot()

    saga = start(sim)

    assert saga.status is SagaStatus.COMPENSATED
    assert "відвідувань" in saga.reason
    assert sim.snapshot() == before  # обидва учасники повернулися до початкового стану


def test_first_step_rejection_needs_no_compensation():
    sim = build(visits_left=5, places=1)
    # Єдине місце вже зайняв інший клієнт
    sim.booking.handle(ReserveSpot("x:reserve-spot", "x", CLASS_ID, "bohdan"))

    saga = start(sim)

    assert saga.status is SagaStatus.REJECTED
    assert sim.snapshot()["відвідувань на абонементі"] == 5  # до абонементів справа не дійшла


# ---------- Перевірка 3: повтор команд без повторного ефекту ----------
def test_repeated_saga_and_commands_have_no_extra_effect():
    sim = build(visits_left=5)
    start(sim, "saga-3")
    after_first = sim.snapshot()

    again = start(sim, "saga-3")
    r1 = sim.booking.handle(ReserveSpot("saga-3:reserve-spot", "saga-3", CLASS_ID, CLIENT_ID))
    r2 = sim.memberships.handle(DebitVisit("saga-3:debit-visit", "saga-3", MEMBERSHIP_ID, CLASS_ID))

    assert again.status is SagaStatus.COMPLETED
    assert r1.ok and r2.ok
    assert sim.snapshot() == after_first
    assert sim.memberships.get(MEMBERSHIP_ID).debited_for == [CLASS_ID]  # списано один раз


def test_repeated_compensation_has_no_extra_effect():
    sim = build(visits_left=0)
    start(sim, "saga-r")
    reply = sim.booking.handle(ReleaseSpot("saga-r:release-spot", "saga-r", CLASS_ID, CLIENT_ID))
    assert reply.ok
    assert sim.snapshot()["вільних місць"] == 3


def test_same_operation_id_with_different_data_is_rejected():
    sim = build(visits_left=5)
    sim.memberships.handle(DebitVisit("op-1", "saga-x", MEMBERSHIP_ID, CLASS_ID))
    with pytest.raises(OperationConflictError):
        sim.memberships.handle(DebitVisit("op-1", "saga-x", MEMBERSHIP_ID, "yoga-1"))


def test_temporary_failure_of_second_step_is_retried_with_same_operation_id():
    sim = build(visits_left=5)
    sim.memberships.fail_next("DebitVisit", times=2)

    saga = start(sim)

    assert saga.status is SagaStatus.COMPLETED
    assert sim.snapshot()["відвідувань на абонементі"] == 4  # три спроби, але списано один раз


# ---------- Перевірка 4: помилка компенсації з подальшим повтором ----------
def test_failed_compensation_waits_and_succeeds_on_retry():
    sim = build(visits_left=0)
    before = sim.snapshot()
    sim.booking.fail_next("ReleaseSpot", times=1)

    saga = start(sim)
    assert saga.status is SagaStatus.COMPENSATION_PENDING
    assert sim.snapshot()["записані на заняття"] == ["anna"]  # тимчасова неузгодженість

    retried = sim.orchestrator.retry_pending()

    assert [s.saga_id for s in retried] == ["saga-1"]
    assert saga.status is SagaStatus.COMPENSATED
    assert saga.compensation_attempts == 2
    assert sim.snapshot() == before


def test_compensation_stays_pending_while_failures_continue():
    sim = build(visits_left=0)
    sim.booking.fail_next("ReleaseSpot", times=2)
    saga = start(sim)
    sim.orchestrator.retry_pending()
    assert saga.status is SagaStatus.COMPENSATION_PENDING
    sim.orchestrator.retry_pending()
    assert saga.status is SagaStatus.COMPENSATED


# ---------- Обмеження після перезапуску ----------
def test_restart_of_orchestrator_loses_pending_compensation():
    """Ілюстрація обмеження: стан оркестратора лише в пам'яті."""
    sim = build(visits_left=0)
    sim.booking.fail_next("ReleaseSpot", times=1)
    start(sim)

    # «Перезапуск»: новий оркестратор, дані учасників нібито збереглися.
    restarted = BookingSagaOrchestrator(sim.booking, sim.memberships, sim.journal)

    assert restarted.retry_pending() == []  # про незавершену компенсацію ніхто не пам'ятає
    assert sim.snapshot()["записані на заняття"] == ["anna"]  # місце лишилося зайнятим
