"""HTTP-адаптер: перекладає HTTP-запити у виклики BookingService і назад.

Тут лише транспорт: розбір шляху, перевірка структури й типів JSON,
перетворення доменних помилок на коди HTTP. Усі бізнес-правила (місткість,
повторний запис, дозволені переходи, допустимий час) лишаються в доменному ядрі.

Модуль не залежить від конкретного веб-сервера: метод handle() приймає
метод, шлях і тіло та повертає статус, заголовки й JSON.
"""
from __future__ import annotations

import json
import re
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from fitshed.booking.application.booking_service import BookingService
from fitshed.booking.domain import (
    ClientAddedToWaitlist,
    ClientBooked,
    ClientId,
    ForbiddenTransitionError,
    GroupClass,
    GroupClassNotFoundError,
    InvalidValueError,
    InvariantViolationError,
)

JSON = "application/json"
PROBLEM_JSON = "application/problem+json"


@dataclass
class HttpResponse:
    status: int
    body: dict[str, Any] | None = None
    headers: dict[str, str] = field(default_factory=dict)
    content_type: str = JSON


class BadInput(Exception):
    """Порушено структуру запиту: немає поля, не той тип, зайве поле, не JSON."""


def problem(status: int, code: str, title: str, detail: str) -> HttpResponse:
    return HttpResponse(status, {"status": status, "code": code, "title": title, "detail": detail},
                        content_type=PROBLEM_JSON)


# ---------- перетворення доменного об'єкта в представлення ресурсу ----------
def class_to_json(gc: GroupClass) -> dict[str, Any]:
    return {
        "class_id": gc.id,
        "title": gc.title,
        "trainer_id": gc.trainer_id,
        "start": gc.slot.start.isoformat(),
        "end": gc.slot.end.isoformat(),
        "capacity": gc.capacity.value,
        "free_places": gc.free_places,
        "status": gc.status.name.lower(),
        "booked": [c.value for c in gc.booked],
        "waitlist": [c.value for c in gc.waitlist],
    }


# ---------- перевірка структури запиту (не бізнес-правил!) ----------
def parse_json(raw: bytes) -> dict[str, Any]:
    try:
        data = json.loads(raw.decode("utf-8") or "null")
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise BadInput("Тіло запиту не є коректним JSON") from None
    if not isinstance(data, dict):
        raise BadInput("Тіло запиту має бути JSON-об'єктом")
    return data


def require(data: dict, fields: dict[str, type]) -> None:
    extra = set(data) - set(fields)
    if extra:
        raise BadInput(f"Невідомі поля: {', '.join(sorted(extra))}")
    for name, expected in fields.items():
        if name not in data:
            raise BadInput(f"Відсутнє обов'язкове поле {name}")
        value = data[name]
        # bool у Python — підклас int, тому відкидаємо його окремо
        if not isinstance(value, expected) or (expected is int and isinstance(value, bool)):
            raise BadInput(f"Поле {name} має бути типу {'integer' if expected is int else 'string'}")
        if expected is str and not value.strip():
            raise BadInput(f"Поле {name} не може бути порожнім")


def parse_datetime(name: str, value: str) -> datetime:
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        raise BadInput(f"Поле {name} має бути датою й часом у форматі ISO 8601") from None


class BookingHttpApi:
    _CLASS = re.compile(r"^/classes/([^/]+)$")
    _BOOKINGS = re.compile(r"^/classes/([^/]+)/bookings$")
    _CANCELLATION = re.compile(r"^/classes/([^/]+)/cancellation$")

    def __init__(self, service: BookingService, id_factory=lambda: f"cls-{uuid.uuid4().hex[:8]}") -> None:
        self._service = service
        self._new_id = id_factory

    def handle(self, method: str, path: str, body: bytes = b"") -> HttpResponse:
        try:
            return self._route(method, path.split("?")[0].rstrip("/") or "/", body)
        except BadInput as error:
            return problem(400, "invalid_input", "Некоректні вхідні дані", str(error))
        except InvalidValueError as error:
            return problem(400, "invalid_input", "Некоректні вхідні дані", str(error))
        except GroupClassNotFoundError as error:
            return problem(404, "not_found", "Ресурс не знайдено", str(error))
        except ForbiddenTransitionError as error:
            return problem(409, "invalid_state", "Конфлікт стану", str(error))
        except InvariantViolationError as error:
            return problem(409, "rule_violation", "Порушено бізнес-правило", str(error))

    def _route(self, method: str, path: str, body: bytes) -> HttpResponse:
        if path == "/classes":
            return self._create_class(body) if method == "POST" else self._not_allowed("POST")
        if m := self._CLASS.match(path):
            return self._get_class(m[1]) if method == "GET" else self._not_allowed("GET")
        if m := self._BOOKINGS.match(path):
            return self._book(m[1], body) if method == "POST" else self._not_allowed("POST")
        if m := self._CANCELLATION.match(path):
            return self._cancel(m[1], body) if method == "POST" else self._not_allowed("POST")
        return problem(404, "not_found", "Ресурс не знайдено", f"Шлях {path} не існує")

    @staticmethod
    def _not_allowed(allowed: str) -> HttpResponse:
        response = problem(405, "invalid_input", "Метод не дозволено", f"Дозволений метод: {allowed}")
        response.headers["Allow"] = allowed
        return response

    # ---------- операції ----------
    def _create_class(self, body: bytes) -> HttpResponse:
        data = parse_json(body)
        require(data, {"title": str, "trainer_id": str, "start": str, "end": str, "capacity": int})
        start = parse_datetime("start", data["start"])
        end = parse_datetime("end", data["end"])
        class_id = self._new_id()
        # Допустимість часу й кількості місць перевіряє доменне ядро (TimeSlot, Capacity)
        self._service.schedule_class(class_id, data["title"], data["trainer_id"], start, end, data["capacity"])
        created = self._service.get_class(class_id)
        return HttpResponse(201, class_to_json(created), {"Location": f"/classes/{class_id}"})

    def _get_class(self, class_id: str) -> HttpResponse:
        return HttpResponse(200, class_to_json(self._service.get_class(class_id)))

    def _book(self, class_id: str, body: bytes) -> HttpResponse:
        data = parse_json(body)
        require(data, {"client_id": str})
        events = self._service.book(class_id, data["client_id"])
        result, position = "booked", None
        for event in events:
            if isinstance(event, ClientAddedToWaitlist):
                result, position = "waitlisted", event.position
            elif isinstance(event, ClientBooked):
                result = "booked"
        return HttpResponse(201, {"class_id": class_id, "client_id": ClientId(data["client_id"]).value,
                                  "result": result, "waitlist_position": position})

    def _cancel(self, class_id: str, body: bytes) -> HttpResponse:
        data = parse_json(body)
        require(data, {"reason": str})
        self._service.cancel_class(class_id, data["reason"])
        return HttpResponse(200, class_to_json(self._service.get_class(class_id)))
