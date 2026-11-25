"""П'ять перевірок ЛР 6 на СПРАВЖНЬОМУ RabbitMQ.

Споживач запускається окремим процесом, тому збій «до commit» і «після commit до ACK»
є справжнім аварійним завершенням процесу, а повторну доставку виконує сам брокер.

Запуск (RabbitMQ уже працює: docker compose up -d), з папки backend/src:
    python -m fitshed.messaging.rabbit_checks
    python -m fitshed.messaging.rabbit_checks --save ../../docs/lab6/rabbitmq-checks.txt
"""
from __future__ import annotations

import argparse
import io
import os
import subprocess
import sys
import tempfile
import time
from contextlib import redirect_stdout
from pathlib import Path

from . import rabbitmq
from .publish import INVALID_BODY, cancel_and_build_event
from .refunds import RefundStore

SRC = Path(__file__).resolve().parents[2]
BALANCES = {"anna": 4, "bohdan": 0, "vira": 7}
EXPECTED = {"anna": 5, "bohdan": 1, "vira": 8}


class Bench:
    def __init__(self) -> None:
        self.db = Path(tempfile.mkdtemp(prefix="fitshed-lab6-")) / "memberships.db"
        self.connection = rabbitmq.connect()
        self.channel = self.connection.channel()
        rabbitmq.declare_topology(self.channel)
        self.publisher = rabbitmq.RabbitPublisher(self.connection)

    def reset(self) -> None:
        if self.db.exists():
            self.db.unlink()
        store = RefundStore(self.db)
        store.seed(BALANCES)
        store.close()
        self.channel.queue_purge(rabbitmq.QUEUE)
        self.channel.queue_purge(rabbitmq.DLQ)

    def state(self) -> dict:
        store = RefundStore(self.db)
        snapshot = store.snapshot()
        store.close()
        snapshot["queue"] = rabbitmq.queue_depth(self.channel, rabbitmq.QUEUE)
        snapshot["dead_letter_queue"] = rabbitmq.queue_depth(self.channel, rabbitmq.DLQ)
        return snapshot

    def consume(self, failpoint: str = "") -> tuple[int, str]:
        env = {**os.environ, "FITSHED_CONSUMER_DB": str(self.db), "FITSHED_FAILPOINT": failpoint,
               "PYTHONIOENCODING": "utf-8"}
        result = subprocess.run([sys.executable, "-m", "fitshed.messaging.consume", "--idle", "2"],
                                cwd=SRC, env=env, capture_output=True, text=True, encoding="utf-8", timeout=60)
        time.sleep(1)  # дати брокеру помітити обрив з'єднання й повернути повідомлення в чергу
        return result.returncode, result.stdout + result.stderr


def show(title: str, state: dict) -> None:
    print(f"{title}:")
    print(f"  повернень відвідувань: {len(state['refunds'])}  {state['refunds']}")
    print(f"  баланси абонементів:  {state['balances']}")
    print(f"  оброблені event_id:   {state['processed_events']}")
    print(f"  записи про помилки:   {state['failed']}")
    print(f"  повідомлень у черзі: {state['queue']}, у черзі помилок: {state['dead_letter_queue']}")


def log_lines(output: str) -> None:
    for line in output.strip().splitlines():
        if "fitshed." in line:
            print(f"    {line}")


def verdict(checks: list[tuple[str, bool]]) -> bool:
    print("Автоматична перевірка:")
    for text, ok in checks:
        print(f"  [{'OK' if ok else 'FAIL'}] {text}")
    return all(ok for _, ok in checks)


def check_1(b: Bench) -> bool:
    b.reset()
    show("Початковий стан", b.state())
    event = cancel_and_build_event("cls-c1", "evt-check-1")
    b.publisher.publish(event.to_json(), event.event_id)
    code, out = b.consume()
    print(f"Споживач (код {code}):"); log_lines(out)
    s = b.state(); show("Кінцевий стан", s)
    return verdict([("очікувано 3 повернення, фактично %d" % len(s["refunds"]), len(s["refunds"]) == 3),
                    ("баланси +1 для anna, bohdan, vira", s["balances"] == EXPECTED),
                    ("черга порожня (ACK надіслано)", s["queue"] == 0)])


def check_2(b: Bench) -> bool:
    b.reset()
    show("Початковий стан", b.state())
    event = cancel_and_build_event("cls-c2", "evt-check-2")
    b.publisher.publish(event.to_json(), event.event_id)
    b.publisher.publish(event.to_json(), event.event_id)
    code, out = b.consume()
    print(f"Споживач (код {code}):"); log_lines(out)
    s = b.state(); show("Кінцевий стан", s)
    return verdict([("дві доставки, але очікувано 3 повернення, фактично %d" % len(s["refunds"]), len(s["refunds"]) == 3),
                    ("друга доставка розпізнана як дублікат", "outcome=duplicate" in out),
                    ("баланси збільшено лише один раз", s["balances"] == EXPECTED),
                    ("черга порожня", s["queue"] == 0)])


def check_3(b: Bench) -> bool:
    b.reset()
    show("Початковий стан", b.state())
    event = cancel_and_build_event("cls-c3", "evt-check-3")
    b.publisher.publish(event.to_json(), event.event_id)
    code1, out1 = b.consume("before_commit")
    print(f"Споживач із точкою збою before_commit (код {code1}):"); log_lines(out1)
    mid = b.state(); show("Стан після збою", mid)
    code2, out2 = b.consume()
    print(f"Споживач після перезапуску (код {code2}):"); log_lines(out2)
    s = b.state(); show("Кінцевий стан", s)
    return verdict([("процес аварійно завершився (код 3)", code1 == 3),
                    ("після збою повернень 0: транзакцію відкочено", len(mid["refunds"]) == 0),
                    ("повідомлення повернулося в чергу", mid["queue"] == 1),
                    ("повторна доставка позначена redelivered=True", "redelivered=True" in out2),
                    ("після повтору очікувано 3 повернення, фактично %d" % len(s["refunds"]), len(s["refunds"]) == 3),
                    ("баланси збільшено один раз", s["balances"] == EXPECTED)])


def check_4(b: Bench) -> bool:
    b.reset()
    show("Початковий стан", b.state())
    event = cancel_and_build_event("cls-c4", "evt-check-4")
    b.publisher.publish(event.to_json(), event.event_id)
    code1, out1 = b.consume("after_commit_before_ack")
    print(f"Споживач із точкою збою after_commit_before_ack (код {code1}):"); log_lines(out1)
    mid = b.state(); show("Стан після збою", mid)
    code2, out2 = b.consume()
    print(f"Споживач після перезапуску (код {code2}):"); log_lines(out2)
    s = b.state(); show("Кінцевий стан", s)
    return verdict([("процес аварійно завершився після commit (код 3)", code1 == 3),
                    ("результат уже зафіксовано: 3 повернення", len(mid["refunds"]) == 3),
                    ("без ACK повідомлення повернулося в чергу", mid["queue"] == 1),
                    ("повторна доставка розпізнана як дублікат", "redelivered=True outcome=duplicate" in out2),
                    ("очікувано 3 повернення, фактично %d" % len(s["refunds"]), len(s["refunds"]) == 3),
                    ("баланси збільшено один раз, черга порожня", s["balances"] == EXPECTED and s["queue"] == 0)])


def check_5(b: Bench) -> bool:
    b.reset()
    show("Початковий стан", b.state())
    b.publisher.publish(INVALID_BODY, None)
    code, out = b.consume()
    print(f"Споживач (код {code}):"); log_lines(out)
    s = b.state(); show("Кінцевий стан", s)
    return verdict([("невалідне повідомлення в черзі помилок", s["dead_letter_queue"] == 1),
                    ("причину збережено для розбору", len(s["failed"]) == 1 and "event_id" in s["failed"][0][1]),
                    ("очікувано 0 повернень, фактично %d" % len(s["refunds"]), len(s["refunds"]) == 0),
                    ("основна черга порожня, споживач не зациклився", s["queue"] == 0)])


CHECKS = [
    ("Перевірка 1. Успішна обробка", check_1),
    ("Перевірка 2. Дві публікації з однаковим event_id", check_2),
    ("Перевірка 3. Збій до commit з подальшим повтором", check_3),
    ("Перевірка 4. Зупинка після commit до ACK з повторною доставкою", check_4),
    ("Перевірка 5. Невалідне повідомлення в черзі помилок", check_5),
]


def run_all() -> bool:
    bench = Bench()
    results = []
    for title, check in CHECKS:
        print("=" * 78); print(title); print("=" * 78)
        results.append(check(bench)); print()
    bench.connection.close()
    print(f"Підсумок: пройдено {sum(results)} з {len(results)} перевірок")
    return all(results)


def main() -> int:
    parser = argparse.ArgumentParser(description="П'ять перевірок ЛР 6 на RabbitMQ")
    parser.add_argument("--save", metavar="ФАЙЛ")
    args = parser.parse_args()
    sys.stdout.reconfigure(encoding="utf-8")
    buffer = io.StringIO()
    with redirect_stdout(buffer):
        ok = run_all()
    print(buffer.getvalue(), end="")
    if args.save:
        Path(args.save).write_text(buffer.getvalue(), encoding="utf-8")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
