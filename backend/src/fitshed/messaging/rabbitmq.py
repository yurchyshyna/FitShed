"""Адаптер RabbitMQ (pika). Єдиний модуль проєкту, що залежить від бібліотеки брокера.

Топологія (усе durable):
  exchange  fitshed.events (topic)   --booking.class.cancelled-->  queue memberships.class-cancelled
  exchange  fitshed.dlx    (direct)  --memberships.class-cancelled.dlq-->  queue memberships.class-cancelled.dlq
Основна черга має x-dead-letter-exchange = fitshed.dlx, тому basic_nack(requeue=False)
переносить повідомлення в чергу помилок, а не повертає в ту саму чергу.
"""
from __future__ import annotations

import logging
import os
from collections.abc import Iterator

import pika

log = logging.getLogger("fitshed.rabbitmq")

AMQP_URL = os.environ.get("FITSHED_AMQP_URL", "amqp://fitshed:fitshed-local-only@localhost:5672/%2F")
EXCHANGE = "fitshed.events"
DLX = "fitshed.dlx"
ROUTING_KEY = "booking.class.cancelled"
QUEUE = "memberships.class-cancelled"
DLQ = "memberships.class-cancelled.dlq"


def connect(url: str = AMQP_URL) -> pika.BlockingConnection:
    return pika.BlockingConnection(pika.URLParameters(url))


def declare_topology(channel) -> None:
    channel.exchange_declare(exchange=EXCHANGE, exchange_type="topic", durable=True)
    channel.exchange_declare(exchange=DLX, exchange_type="direct", durable=True)
    channel.queue_declare(queue=QUEUE, durable=True, arguments={
        "x-dead-letter-exchange": DLX,
        "x-dead-letter-routing-key": DLQ,
    })
    channel.queue_bind(queue=QUEUE, exchange=EXCHANGE, routing_key=ROUTING_KEY)
    channel.queue_declare(queue=DLQ, durable=True)
    channel.queue_bind(queue=DLQ, exchange=DLX, routing_key=DLQ)


def queue_depth(channel, queue: str) -> int:
    return channel.queue_declare(queue=queue, passive=True).method.message_count


class RabbitPublisher:
    """Публікує persistent-повідомлення з publisher confirms."""

    def __init__(self, connection: pika.BlockingConnection) -> None:
        self._channel = connection.channel()
        declare_topology(self._channel)
        self._channel.confirm_delivery()

    def publish(self, body: bytes, event_id: str | None) -> None:
        """Повертається лише після підтвердження брокера (інакше виняток NackError/UnroutableError).

        Підтвердження означає: брокер прийняв повідомлення й поставив у durable-чергу.
        Воно НЕ означає, що споживач уже повернув відвідування.
        """
        self._channel.basic_publish(
            exchange=EXCHANGE,
            routing_key=ROUTING_KEY,
            body=body,
            properties=pika.BasicProperties(
                content_type="application/json",
                delivery_mode=2,            # persistent: повідомлення пишеться на диск
                message_id=event_id,
                type="GroupClassCancelled",
            ),
            mandatory=True,                 # якщо черги для routing key немає — помилка, а не тиха втрата
        )
        log.info("event_id=%s published confirmed=True", event_id or "-")


class RabbitDelivery:
    def __init__(self, channel, method, body: bytes) -> None:
        self._channel, self._tag = channel, method.delivery_tag
        self.body, self.redelivered = body, bool(method.redelivered)

    def ack(self) -> None:
        self._channel.basic_ack(delivery_tag=self._tag)

    def reject_to_dead_letter(self) -> None:
        self._channel.basic_nack(delivery_tag=self._tag, requeue=False)


def deliveries(connection: pika.BlockingConnection, idle_seconds: float) -> Iterator[RabbitDelivery]:
    """Видає доставки з ручним ACK, поки черга не простоює idle_seconds."""
    channel = connection.channel()
    declare_topology(channel)
    channel.basic_qos(prefetch_count=1)     # одне непідтверджене повідомлення за раз
    try:
        for method, _properties, body in channel.consume(QUEUE, auto_ack=False, inactivity_timeout=idle_seconds):
            if method is None:
                break
            yield RabbitDelivery(channel, method, body)
    finally:
        if channel.is_open:
            channel.cancel()
