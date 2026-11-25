"""Політика доступу Booking API (ЛР 8): «роль → операція → умова доступу».

Перевірка ролі недостатня: для зміни об'єкта ще перевіряється, що він належить користувачу.
  - заняттям володіє тренер, чий sub дорівнює GroupClass.trainer_id;
  - записом володіє клієнт, чий sub дорівнює client_id запису.
Ця перевірка робиться тут, у компоненті-власнику даних, а не в шлюзі.
Модуль не залежить від FastAPI й від бібліотеки JWT: отримує вже перевіреного Principal.

| Операція                   | admin      | trainer                         | client                           |
|----------------------------|------------|---------------------------------|----------------------------------|
| створити заняття           | будь-яке   | лише з trainer_id = sub         | заборонено                       |
| читати заняття             | повністю   | будь-яке; список записаних —    | будь-яке; у списках лише він сам |
|                            |            | лише у своєму занятті           |                                  |
| записати клієнта           | будь-кого  | заборонено                      | лише себе (client_id = sub)      |
| скасувати заняття          | будь-яке   | лише своє (trainer_id = sub)    | заборонено                       |
"""
from __future__ import annotations

from typing import Any

from fitshed.auth.tokens import Principal
from fitshed.booking.domain import GroupClass


class Forbidden(Exception):
    """Користувач автентифікований, але права на цю дію чи цей об'єкт немає → HTTP 403."""


def can_create_class(p: Principal, trainer_id: str) -> None:
    if p.role == "admin":
        return
    if p.role == "trainer" and trainer_id == p.sub:
        return
    if p.role == "trainer":
        raise Forbidden("тренер може створювати заняття лише для себе")
    raise Forbidden("роль не дозволяє створювати заняття")


def can_book(p: Principal, client_id: str) -> None:
    if p.role == "admin":
        return
    if p.role == "client" and client_id == p.sub:
        return
    if p.role == "client":
        raise Forbidden("клієнт може записувати лише себе")
    raise Forbidden("роль не дозволяє записувати клієнтів")


def can_cancel_class(p: Principal, group_class: GroupClass) -> None:
    if p.role == "admin":
        return
    if p.role == "trainer" and group_class.trainer_id == p.sub:
        return
    if p.role == "trainer":
        raise Forbidden("тренер може скасовувати лише власні заняття")
    raise Forbidden("роль не дозволяє скасовувати заняття")


def visible_class(p: Principal, group_class: GroupClass, representation: dict[str, Any]) -> dict[str, Any]:
    """Найменші привілеї при читанні: чужі імена клієнтів бачать лише адміністратор і тренер заняття."""
    if p.role == "admin" or (p.role == "trainer" and group_class.trainer_id == p.sub):
        return representation
    own = [c for c in representation["booked"] if c == p.sub]
    own_wait = [c for c in representation["waitlist"] if c == p.sub]
    return {**representation, "booked": own, "waitlist": own_wait}
