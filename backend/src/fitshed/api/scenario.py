"""Завершений клієнтський сценарій через API від імені синтетичних користувачів (ЛР 5, доповнено в ЛР 8).

Підготовка (один раз, з backend/src):
    python -m fitshed.auth.keys
Потім сервер в окремому терміналі:
    python -m fitshed.api.server
І сценарій:
    python -m fitshed.api.scenario
    python -m fitshed.api.scenario --url http://127.0.0.1:8080/api    # через шлюз (ЛР 7)

Токени видає локальний навчальний емітент тим самим закритим ключем, чий відкритий ключ знає сервер.
"""

from __future__ import annotations

import argparse
import sys
from datetime import datetime, timedelta

from fitshed.auth.tokens import AuthConfig, LocalIssuer

from .client import ApiError, FitShedClient


def run(url: str, issuer: LocalIssuer) -> None:
    def as_user(sub: str, role: str) -> FitShedClient:
        return FitShedClient(url, issuer.issue(sub, role, ttl_seconds=300))

    olena = as_user("trainer-olena", "trainer")
    start = (datetime.now() + timedelta(days=1)).replace(
        hour=10, minute=0, second=0, microsecond=0
    )
    pilates = olena.create_class(
        "Пілатес",
        "trainer-olena",
        start.isoformat(),
        (start + timedelta(hours=1)).isoformat(),
        3,
    )
    print(
        f"1. trainer-olena створила заняття {pilates.class_id}: {pilates.title}, вільних місць {pilates.free_places}"
    )

    for name in ("anna", "bohdan", "vira", "halyna"):
        b = as_user(name, "client").book(pilates.class_id, name)
        where = (
            "отримав(ла) місце"
            if b.result == "booked"
            else f"у листі очікування, позиція {b.waitlist_position}"
        )
        print(f"2. {b.client_id} записується сам(а): {where}")

    try:
        as_user("anna", "client").book(pilates.class_id, "anna")
    except ApiError as error:
        print(
            f"3. Повторний запис anna відхилено: {error.status} {error.code} — {error.detail}"
        )

    try:
        as_user("bohdan", "client").book(pilates.class_id, "dmytro")
    except ApiError as error:
        print(
            f"4. bohdan намагається записати dmytro: {error.status} {error.code} — {error.detail}"
        )

    try:
        as_user("trainer-taras", "trainer").cancel_class(
            pilates.class_id, "Не моє заняття"
        )
    except ApiError as error:
        print(
            f"5. trainer-taras намагається скасувати чуже заняття: {error.status} {error.code} — {error.detail}"
        )

    try:
        FitShedClient(url).get_class(pilates.class_id)
    except ApiError as error:
        print(f"6. Запит без токена: {error.status} {error.code}")

    state = olena.get_class(pilates.class_id)
    print(
        f"7. Тренерка бачить: {state.status}, записані {state.booked}, вільних місць {state.free_places}"
    )
    anna_view = as_user("anna", "client").get_class(pilates.class_id)
    print(f"8. Анна бачить у списку записаних лише себе: {anna_view.booked}")

    cancelled = olena.cancel_class(pilates.class_id, "Тренерка захворіла")
    print(f"9. trainer-olena скасувала власне заняття, статус: {cancelled.status}")


def main() -> int:
    parser = argparse.ArgumentParser(description="Клієнтський сценарій FitShed")
    parser.add_argument("--url", default="http://127.0.0.1:8000")
    args = parser.parse_args()
    sys.stdout.reconfigure(encoding="utf-8")
    try:
        issuer = LocalIssuer(AuthConfig.from_env())
    except FileNotFoundError as error:
        print(error)
        return 1
    try:
        run(args.url, issuer)
    except OSError as error:
        print(f"Не вдалося підключитися до {args.url}: {error}. Чи запущено сервер?")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
