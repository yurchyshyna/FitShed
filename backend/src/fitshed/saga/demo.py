"""Демонстрація чотирьох перевірок Saga.

Запуск з папки backend/src:
    python -m fitshed.saga.demo
    python -m fitshed.saga.demo --save ../../docs/lab3/saga-results.txt   # ще й зберегти у файл

Для кожної перевірки виводиться початковий стан, журнал, кінцевий стан
і результат автоматичної перевірки. Код повернення 1, якщо хоч одна перевірка не пройшла.
"""
from __future__ import annotations

import argparse
import io
import sys
from contextlib import redirect_stdout
from collections.abc import Callable

from .contracts import DebitVisit, ReserveSpot
from .orchestrator import SagaStatus
from .simulation import CLASS_ID, CLIENT_ID, MEMBERSHIP_ID, Simulation, build


def show_state(title: str, state: dict) -> None:
    print(f"{title}:")
    for key, value in state.items():
        print(f"  {key}: {value}")


def check(description: str, condition: bool) -> bool:
    print(f"  [{'OK' if condition else 'FAIL'}] {description}")
    return condition


def scenario_success() -> bool:
    sim = build(visits_left=5)
    before = sim.snapshot()
    show_state("Початковий стан", before)
    saga = sim.orchestrator.start("saga-1", CLASS_ID, CLIENT_ID, MEMBERSHIP_ID)
    return report(sim, before, [
        ("Saga завершилася успішно", saga.status is SagaStatus.COMPLETED),
        ("Анна записана на заняття", sim.snapshot()["записані на заняття"] == ["anna"]),
        ("списано рівно одне відвідування (5 → 4)", sim.snapshot()["відвідувань на абонементі"] == 4),
    ])


def scenario_second_step_fails() -> bool:
    sim = build(visits_left=0)
    before = sim.snapshot()
    show_state("Початковий стан", before)
    saga = sim.orchestrator.start("saga-2", CLASS_ID, CLIENT_ID, MEMBERSHIP_ID)
    return report(sim, before, [
        ("Saga скомпенсована", saga.status is SagaStatus.COMPENSATED),
        ("місце звільнене, записаних немає", sim.snapshot()["записані на заняття"] == []),
        ("вільних місць знову 3", sim.snapshot()["вільних місць"] == 3),
        ("абонемент не змінився", sim.snapshot()["відвідувань на абонементі"] == 0),
    ])


def scenario_repeated_commands() -> bool:
    sim = build(visits_left=5)
    before = sim.snapshot()
    show_state("Початковий стан", before)
    sim.orchestrator.start("saga-3", CLASS_ID, CLIENT_ID, MEMBERSHIP_ID)
    after_first = sim.snapshot()

    # Повтор усієї Saga з тим самим saga_id
    saga = sim.orchestrator.start("saga-3", CLASS_ID, CLIENT_ID, MEMBERSHIP_ID)
    # Повтор окремих команд з тими самими operation_id (наприклад, мережа доставила їх двічі)
    r1 = sim.booking.handle(ReserveSpot("saga-3:reserve-spot", "saga-3", CLASS_ID, CLIENT_ID))
    r2 = sim.memberships.handle(DebitVisit("saga-3:debit-visit", "saga-3", MEMBERSHIP_ID, CLASS_ID))

    return report(sim, before, [
        ("Saga лишилася в стані COMPLETED", saga.status is SagaStatus.COMPLETED),
        ("повторні команди повернули попередній успішний результат", r1.ok and r2.ok),
        ("стан після повторів такий самий, як після першого виконання", sim.snapshot() == after_first),
        ("відвідування списано лише раз (5 → 4)", sim.snapshot()["відвідувань на абонементі"] == 4),
    ])


def scenario_compensation_fails_then_retried() -> bool:
    sim = build(visits_left=0)
    before = sim.snapshot()
    show_state("Початковий стан", before)
    sim.booking.fail_next("ReleaseSpot", times=1)  # перша спроба компенсації впаде

    saga = sim.orchestrator.start("saga-4", CLASS_ID, CLIENT_ID, MEMBERSHIP_ID)
    pending_ok = saga.status is SagaStatus.COMPENSATION_PENDING
    still_reserved = sim.snapshot()["записані на заняття"] == ["anna"]
    print("Проміжний стан після невдалої компенсації:")
    print(f"  статус Saga: {saga.status.name}")
    print(f"  записані на заняття: {sim.snapshot()['записані на заняття']}")

    sim.orchestrator.retry_pending()

    return report(sim, before, [
        ("після першої спроби Saga явно чекає повтору (COMPENSATION_PENDING)", pending_ok),
        ("поки компенсація не виконана, місце ще зайняте", still_reserved),
        ("після повтору Saga скомпенсована", saga.status is SagaStatus.COMPENSATED),
        ("знадобилося дві спроби компенсації", saga.compensation_attempts == 2),
        ("місце звільнене", sim.snapshot()["записані на заняття"] == []),
    ])


def report(sim: Simulation, before: dict, checks: list[tuple[str, bool]]) -> bool:
    print("Журнал:")
    for line in sim.journal.lines():
        print(f"  {line}")
    show_state("Кінцевий стан", sim.snapshot())
    print("Автоматична перевірка:")
    return all([check(text, ok) for text, ok in checks])


SCENARIOS: list[tuple[str, Callable[[], bool]]] = [
    ("Перевірка 1. Успішний процес", scenario_success),
    ("Перевірка 2. Відмова другого кроку з компенсацією", scenario_second_step_fails),
    ("Перевірка 3. Повтор команд без повторного ефекту", scenario_repeated_commands),
    ("Перевірка 4. Помилка компенсації з подальшим повтором", scenario_compensation_fails_then_retried),
]


def run_all() -> bool:
    results = []
    for title, scenario in SCENARIOS:
        print("=" * 78)
        print(title)
        print("=" * 78)
        results.append(scenario())
        print()
    passed = sum(results)
    print(f"Підсумок: пройдено {passed} з {len(results)} перевірок")
    return all(results)


def main() -> int:
    parser = argparse.ArgumentParser(description="Чотири перевірки Saga FitShed")
    parser.add_argument("--save", metavar="ФАЙЛ", help="також зберегти вивід у файл (UTF-8)")
    args = parser.parse_args()

    buffer = io.StringIO()
    with redirect_stdout(buffer):
        ok = run_all()
    output = buffer.getvalue()

    # Кирилиця коректно виводиться незалежно від кодування консолі Windows.
    sys.stdout.reconfigure(encoding="utf-8")
    print(output, end="")
    if args.save:
        with open(args.save, "w", encoding="utf-8") as file:
            file.write(output)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
