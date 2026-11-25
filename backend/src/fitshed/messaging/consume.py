"""Процес-споживач: читає memberships.class-cancelled і повертає відвідування.

Запуск з папки backend/src (RabbitMQ має працювати):
    python -m fitshed.messaging.consume
    python -m fitshed.messaging.consume --idle 3 --db ../var/memberships.db

Точки збою (для перевірок):
    PowerShell:  $env:FITSHED_FAILPOINT = "after_commit_before_ack"; python -m fitshed.messaging.consume
Після спрацювання процес завершується з кодом 3 БЕЗ ACK і без закриття з'єднання,
як при справжньому падінні; брокер повертає повідомлення в чергу.
"""
from __future__ import annotations

import argparse
import logging
import os
import sys
from pathlib import Path

from .consumer import RefundConsumer
from .failpoints import Failpoints, SimulatedCrash
from .refunds import RefundStore

DEFAULT_DB = Path(__file__).resolve().parents[3] / "var" / "memberships.db"


def setup_logging() -> None:
    sys.stdout.reconfigure(encoding="utf-8")
    logging.basicConfig(level=logging.INFO, stream=sys.stdout,
                        format="%(asctime)s %(levelname)-7s %(name)s %(message)s")


def main() -> int:
    parser = argparse.ArgumentParser(description="Споживач події GroupClassCancelled")
    parser.add_argument("--db", default=os.environ.get("FITSHED_CONSUMER_DB", str(DEFAULT_DB)))
    parser.add_argument("--idle", type=float, default=3.0, help="завершитися, якщо черга порожня стільки секунд")
    args = parser.parse_args()
    setup_logging()

    from . import rabbitmq  # імпорт тут, щоб решта пакета не залежала від pika

    Path(args.db).parent.mkdir(parents=True, exist_ok=True)
    failpoints = Failpoints()
    consumer = RefundConsumer(RefundStore(args.db, failpoints), failpoints)
    connection = rabbitmq.connect()
    processed = 0
    try:
        for delivery in rabbitmq.deliveries(connection, args.idle):
            consumer.process(delivery)
            processed += 1
    except SimulatedCrash as crash:
        logging.getLogger("fitshed.consumer").error("FAILPOINT %s: аварійне завершення без ACK", crash)
        sys.stdout.flush()
        os._exit(3)  # без close(): брокер побачить обрив з'єднання
    connection.close()
    logging.getLogger("fitshed.consumer").info("черга порожня, оброблено доставок: %d", processed)
    return 0


if __name__ == "__main__":
    sys.exit(main())
