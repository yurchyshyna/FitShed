"""Алгоритм обробки однієї доставки — спільний для RabbitMQ і для імітації брокера.

    розібрати → (невалідне) записати причину, відправити в чергу помилок
              → записати результат і event_id в одній транзакції (з обмеженими повторами)
              → ACK лише після COMMIT
"""
from __future__ import annotations

import logging
import sqlite3
import time
from collections.abc import Callable, Sequence
from typing import Protocol

from .envelope import InvalidMessage, parse
from .failpoints import Failpoints
from .refunds import RefundStore

log = logging.getLogger("fitshed.consumer")

DEFAULT_RETRY_DELAYS = (0.5, 1.0)   # 3 спроби: одразу, через 0,5 с, через 1 с
TEMPORARY_ERRORS = (sqlite3.OperationalError,)


class Delivery(Protocol):
    body: bytes
    redelivered: bool

    def ack(self) -> None: ...
    def reject_to_dead_letter(self) -> None: ...


class RefundConsumer:
    def __init__(self, store: RefundStore, failpoints: Failpoints | None = None,
                 retry_delays: Sequence[float] = DEFAULT_RETRY_DELAYS,
                 sleep: Callable[[float], None] = time.sleep) -> None:
        self.store = store
        self.failpoints = failpoints or Failpoints()
        self.retry_delays = list(retry_delays)
        self.sleep = sleep

    def process(self, delivery: Delivery) -> str:
        """Повертає результат: applied, duplicate, invalid або failed."""
        try:
            event = parse(delivery.body)
        except InvalidMessage as error:
            self.store.record_failure(None, delivery.body, f"невалідне повідомлення: {error}", attempts=1)
            delivery.reject_to_dead_letter()
            log.warning("event_id=- outcome=invalid reason=%r -> черга помилок", str(error))
            return "invalid"

        attempts = 0
        while True:
            attempts += 1
            try:
                outcome = self.store.apply(event)
                break
            except TEMPORARY_ERRORS as error:
                if attempts > len(self.retry_delays):
                    self.store.record_failure(event.event_id, delivery.body,
                                              f"тимчасова помилка, спроби вичерпано: {error}", attempts)
                    delivery.reject_to_dead_letter()
                    log.error("event_id=%s outcome=failed attempts=%d reason=%r -> черга помилок",
                              event.event_id, attempts, str(error))
                    return "failed"
                delay = self.retry_delays[attempts - 1]
                log.warning("event_id=%s attempt=%d temporary_error=%r retry_in=%.1fs",
                            event.event_id, attempts, str(error), delay)
                self.sleep(delay)

        log.info("event_id=%s redelivered=%s outcome=%s class_id=%s clients=%d",
                 event.event_id, delivery.redelivered, outcome, event.class_id, len(event.booked_client_ids))
        self.failpoints.hit("after_commit_before_ack")
        delivery.ack()
        return outcome
