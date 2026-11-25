"""П'ять перевірок ЛР 6 на імітації брокера + реальна база SQLite у файлі.

Для кожної перевірки фіксуються очікувана кількість бізнес-результатів
(повернених відвідувань) і фактичний стан сховища.
"""
import logging
from datetime import datetime, timedelta

import pytest

from fitshed.booking.adapters.in_memory import InMemoryGroupClassRepository
from fitshed.booking.application.booking_service import BookingService
from fitshed.booking.domain import GroupClassCancelled
from fitshed.messaging.consumer import RefundConsumer
from fitshed.messaging.envelope import InvalidMessage, from_domain, parse
from fitshed.messaging.failpoints import Failpoints, SimulatedCrash
from fitshed.messaging.in_memory_broker import InMemoryBroker
from fitshed.messaging.refunds import RefundStore

NOW = datetime(2026, 12, 1, 9, 0)
BALANCES = {"anna": 4, "bohdan": 0, "vira": 7}
EXPECTED_BALANCES = {"anna": 5, "bohdan": 1, "vira": 8}


def cancelled_class_event(event_id="evt-1"):
    """Публікатор: скасовує заняття через доменне ядро і формує подію."""
    service = BookingService(InMemoryGroupClassRepository(), clock=lambda: NOW)
    start = NOW + timedelta(days=1)
    service.schedule_class("cls-1", "Пілатес", "trainer-olena", start, start + timedelta(hours=1), 3)
    for name in ("anna", "bohdan", "vira", "halyna"):   # halyna — у листі очікування
        service.book("cls-1", name)
    [event] = [e for e in service.cancel_class("cls-1", "Тренер захворів") if isinstance(e, GroupClassCancelled)]
    return from_domain(service.get_class("cls-1"), event, event_id)


@pytest.fixture
def db(tmp_path):
    path = tmp_path / "memberships.db"
    store = RefundStore(path)
    store.seed(BALANCES)
    store.close()
    return path


def consumer(db, failpoints=""):
    """Новий екземпляр споживача з тим самим файлом бази — як після перезапуску процесу."""
    fp = Failpoints(failpoints)
    return RefundConsumer(RefundStore(db, fp), fp, retry_delays=(0, 0), sleep=lambda s: None)


def drain(broker, c):
    results = []
    while (delivery := broker.get()) is not None:
        results.append(c.process(delivery))
    return results


# ---------- схема події ----------
def test_event_round_trip_keeps_only_booked_clients():
    event = cancelled_class_event()
    parsed = parse(event.to_json())
    assert parsed == event
    assert parsed.booked_client_ids == ("anna", "bohdan", "vira")   # без листа очікування


@pytest.mark.parametrize("body, reason", [
    (b"not json", "JSON"),
    (b'{"event_type": "GroupClassCancelled"}', "event_id"),
    (cancelled_class_event().to_json().replace(b'"schema_version": 1', b'"schema_version": 2'), "версія схеми"),
    (cancelled_class_event().to_json().replace(b"GroupClassCancelled", b"ClassRenamed"), "тип події"),
])
def test_invalid_messages_are_rejected_with_reason(body, reason):
    with pytest.raises(InvalidMessage, match=reason):
        parse(body)


# ---------- Перевірка 1: успішна обробка ----------
def test_1_success(db):
    broker = InMemoryBroker()
    broker.publish(cancelled_class_event().to_json())
    c = consumer(db)

    assert drain(broker, c) == ["applied"]

    state = c.store.snapshot()
    assert len(state["refunds"]) == 3                       # очікувано 3 повернення
    assert state["balances"] == EXPECTED_BALANCES
    assert state["processed_events"] == ["evt-1"]
    assert broker.unacked == 0 and not broker.queue


# ---------- Перевірка 2: дві публікації з однаковим event_id ----------
def test_2_duplicate_publication(db):
    broker = InMemoryBroker()
    event = cancelled_class_event()
    broker.publish(event.to_json())
    broker.publish(event.to_json())
    c = consumer(db)

    assert drain(broker, c) == ["applied", "duplicate"]

    state = c.store.snapshot()
    assert len(state["refunds"]) == 3                       # не 6
    assert state["balances"] == EXPECTED_BALANCES
    assert broker.unacked == 0                              # дублікат теж підтверджено


def test_same_cancellation_with_new_event_id_does_not_refund_twice(db):
    """Друга лінія захисту: UNIQUE(class_id, client_id)."""
    broker = InMemoryBroker()
    broker.publish(cancelled_class_event("evt-1").to_json())
    broker.publish(cancelled_class_event("evt-1-republished").to_json())
    c = consumer(db)
    drain(broker, c)
    assert len(c.store.snapshot()["refunds"]) == 3
    assert c.store.snapshot()["balances"] == EXPECTED_BALANCES


# ---------- Перевірка 3: збій до commit з подальшим повтором ----------
def test_3_crash_before_commit_then_retry(db):
    broker = InMemoryBroker()
    broker.publish(cancelled_class_event().to_json())

    crashed = consumer(db, "before_commit")
    with pytest.raises(SimulatedCrash):
        crashed.process(broker.get())
    broker.consumer_connection_lost()                       # процес «помер» без ACK

    state = consumer(db).store.snapshot()
    assert state["refunds"] == [] and state["processed_events"] == []   # транзакцію відкочено
    assert state["balances"] == BALANCES

    restarted = consumer(db)                                # перезапуск споживача
    delivery = broker.get()
    assert delivery.redelivered is True
    assert restarted.process(delivery) == "applied"
    assert len(restarted.store.snapshot()["refunds"]) == 3
    assert restarted.store.snapshot()["balances"] == EXPECTED_BALANCES


# ---------- Перевірка 4: зупинка після commit до ACK ----------
def test_4_crash_after_commit_before_ack(db):
    broker = InMemoryBroker()
    broker.publish(cancelled_class_event().to_json())

    crashed = consumer(db, "after_commit_before_ack")
    with pytest.raises(SimulatedCrash):
        crashed.process(broker.get())
    assert broker.unacked == 1                              # ACK так і не надіслано
    broker.consumer_connection_lost()

    state = consumer(db).store.snapshot()
    assert len(state["refunds"]) == 3                       # результат уже зафіксовано

    restarted = consumer(db)
    delivery = broker.get()
    assert delivery.redelivered is True                     # повторна доставка...
    assert restarted.process(delivery) == "duplicate"       # ...без повторного ефекту
    assert len(restarted.store.snapshot()["refunds"]) == 3
    assert restarted.store.snapshot()["balances"] == EXPECTED_BALANCES
    assert broker.unacked == 0 and not broker.queue


# ---------- Перевірка 5: невалідне повідомлення в черзі помилок ----------
def test_5_invalid_message_goes_to_dead_letter(db):
    broker = InMemoryBroker()
    broker.publish(b'{"event_type": "GroupClassCancelled", "data": {}}')
    broker.publish(cancelled_class_event().to_json())       # наступне валідне обробляється
    c = consumer(db)

    assert drain(broker, c) == ["invalid", "applied"]

    state = c.store.snapshot()
    assert len(broker.dead_letter) == 1
    assert state["failed"][0][1].startswith("невалідне повідомлення: немає обов'язкового поля event_id")
    assert len(state["refunds"]) == 3                       # невалідне не дало ефекту


# ---------- тимчасові помилки: обмежені повтори ----------
def test_temporary_error_is_retried_and_then_succeeds(db):
    broker = InMemoryBroker()
    broker.publish(cancelled_class_event().to_json())
    c = consumer(db, "temporary_error:2")                   # 2 збої, третя спроба вдала
    assert drain(broker, c) == ["applied"]
    assert len(c.store.snapshot()["refunds"]) == 3


def test_exhausted_retries_store_message_and_reason(db):
    broker = InMemoryBroker()
    broker.publish(cancelled_class_event().to_json())
    c = consumer(db, "temporary_error:5")                   # більше, ніж дозволено спроб
    assert drain(broker, c) == ["failed"]
    state = c.store.snapshot()
    assert state["refunds"] == []
    assert state["failed"] == [("evt-1", state["failed"][0][1], 3)]
    assert "спроби вичерпано" in state["failed"][0][1]
    assert len(broker.dead_letter) == 1 and broker.unacked == 0


def test_log_lines_contain_event_id(db, caplog):
    caplog.set_level(logging.INFO, logger="fitshed.consumer")
    broker = InMemoryBroker()
    broker.publish(cancelled_class_event().to_json())
    drain(broker, consumer(db))
    assert any("event_id=evt-1" in r.getMessage() and "outcome=applied" in r.getMessage() for r in caplog.records)
