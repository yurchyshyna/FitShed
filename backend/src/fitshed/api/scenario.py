"""Завершений клієнтський сценарій через API.

Спершу запустіть сервер в окремому терміналі (з папки backend/src):
    python -m fitshed.api.server
Потім:
    python -m fitshed.api.scenario
    python -m fitshed.api.scenario --url http://127.0.0.1:8000

Сценарій: адміністратор створює заняття на 3 місця → четверо клієнтів записуються
(четвертий потрапляє в лист очікування) → повторний запис відхиляється (409) →
адміністратор скасовує заняття → запис на скасоване заняття відхиляється (409).
"""
from __future__ import annotations

import argparse
import sys
from datetime import datetime, timedelta

from .client import ApiError, FitShedClient


def run(client: FitShedClient) -> None:
    start = (datetime.now() + timedelta(days=1)).replace(hour=10, minute=0, second=0, microsecond=0)
    pilates = client.create_class("Пілатес", "trainer-olena", start.isoformat(),
                                  (start + timedelta(hours=1)).isoformat(), 3)
    print(f"1. Створено заняття {pilates.class_id}: {pilates.title}, вільних місць {pilates.free_places}")

    for name in ("anna", "bohdan", "vira", "halyna"):
        b = client.book(pilates.class_id, name)
        where = "отримав(ла) місце" if b.result == "booked" else f"у листі очікування, позиція {b.waitlist_position}"
        print(f"2. Запис {b.client_id}: {where}")

    try:
        client.book(pilates.class_id, "anna")
    except ApiError as error:
        print(f"3. Повторний запис anna відхилено: {error.status} {error.code} — {error.detail}")

    state = client.get_class(pilates.class_id)
    print(f"4. Стан: {state.status}, записані {state.booked}, вільних місць {state.free_places}")

    cancelled = client.cancel_class(pilates.class_id, "Тренер захворів")
    print(f"5. Заняття скасовано, статус: {cancelled.status}")

    try:
        client.book(pilates.class_id, "dmytro")
    except ApiError as error:
        print(f"6. Запис на скасоване заняття відхилено: {error.status} {error.code} — {error.detail}")


def main() -> int:
    parser = argparse.ArgumentParser(description="Клієнтський сценарій FitShed")
    parser.add_argument("--url", default="http://127.0.0.1:8000")
    args = parser.parse_args()
    sys.stdout.reconfigure(encoding="utf-8")
    try:
        run(FitShedClient(args.url))
    except OSError as error:
        print(f"Не вдалося підключитися до {args.url}: {error}. Чи запущено сервер?")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
