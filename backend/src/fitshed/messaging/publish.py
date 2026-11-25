"""Публікатор: скасовує заняття через доменне ядро й публікує подію GroupClassCancelled.

Запуск з папки backend/src:
    python -m fitshed.messaging.publish normal                 # одна подія
    python -m fitshed.messaging.publish duplicate              # та сама подія двічі (однаковий event_id)
    python -m fitshed.messaging.publish invalid                # невалідне повідомлення
    python -m fitshed.messaging.publish normal --event-id evt-42 --class-id cls-42
"""
from __future__ import annotations

import argparse
import logging
import sys
from datetime import datetime, timedelta

from fitshed.booking.adapters.in_memory import InMemoryGroupClassRepository
from fitshed.booking.application.booking_service import BookingService
from fitshed.booking.domain import GroupClassCancelled

from .envelope import ClassCancelledEvent, from_domain

INVALID_BODY = b'{"event_type": "GroupClassCancelled", "data": {}}'


def cancel_and_build_event(class_id: str, event_id: str | None) -> ClassCancelledEvent:
    """Бізнес-операція публікатора: заняття скасовано, з доменної події формується повідомлення.

    Увага: стан публікатора змінено ДО публікації. Якщо процес упаде між цими кроками,
    подія загубиться — цей ризик пояснено в ADR-005 (transactional outbox).
    """
    service = BookingService(InMemoryGroupClassRepository(), clock=datetime.now)
    start = (datetime.now() + timedelta(days=1)).replace(hour=10, minute=0, second=0, microsecond=0)
    service.schedule_class(class_id, "Пілатес", "trainer-olena", start, start + timedelta(hours=1), 3)
    for name in ("anna", "bohdan", "vira", "halyna"):
        service.book(class_id, name)
    [event] = [e for e in service.cancel_class(class_id, "Тренер захворів") if isinstance(e, GroupClassCancelled)]
    return from_domain(service.get_class(class_id), event, event_id)


def main() -> int:
    parser = argparse.ArgumentParser(description="Публікація події GroupClassCancelled")
    parser.add_argument("kind", choices=["normal", "duplicate", "invalid"])
    parser.add_argument("--event-id")
    parser.add_argument("--class-id", default="cls-lab6")
    args = parser.parse_args()
    sys.stdout.reconfigure(encoding="utf-8")
    logging.basicConfig(level=logging.INFO, stream=sys.stdout,
                        format="%(asctime)s %(levelname)-7s %(name)s %(message)s")

    from . import rabbitmq

    connection = rabbitmq.connect()
    publisher = rabbitmq.RabbitPublisher(connection)
    if args.kind == "invalid":
        publisher.publish(INVALID_BODY, None)
    else:
        event = cancel_and_build_event(args.class_id, args.event_id)
        for _ in range(2 if args.kind == "duplicate" else 1):
            publisher.publish(event.to_json(), event.event_id)
    connection.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
