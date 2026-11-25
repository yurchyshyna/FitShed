"""Схеми запитів (Pydantic), представлення ресурсу й переклад помилок валідації.

Модуль не імпортує FastAPI: його можна перевіряти окремо.
Схеми перевіряють лише СТРУКТУРУ запиту (поля, типи, формат дати).
Бізнес-правила (кінець пізніше за початок, від 1 до 40 місць тощо) перевіряє доменне ядро.
"""
from __future__ import annotations

from datetime import datetime
from typing import Annotated, Any

from pydantic import BaseModel, BeforeValidator, ConfigDict, StrictInt, StringConstraints

from fitshed.booking.domain import GroupClass


def _iso_datetime(value: Any) -> datetime:
    if not isinstance(value, str):
        raise ValueError("має бути рядком з датою й часом у форматі ISO 8601")
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        raise ValueError("має бути датою й часом у форматі ISO 8601") from None


IsoDateTime = Annotated[datetime, BeforeValidator(_iso_datetime)]
NonEmptyStr = Annotated[str, StringConstraints(strict=True, strip_whitespace=True, min_length=1, max_length=200)]


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class NewClass(_Strict):
    title: NonEmptyStr
    trainer_id: NonEmptyStr
    start: IsoDateTime
    end: IsoDateTime
    capacity: StrictInt   # «3» чи true не приймаються; діапазон 1..40 перевіряє домен (Capacity)


class NewBooking(_Strict):
    client_id: NonEmptyStr


class Cancellation(_Strict):
    reason: NonEmptyStr


def class_to_json(gc: GroupClass) -> dict[str, Any]:
    """Представлення ресурсу «групове заняття» (схема GroupClass в openapi.yaml)."""
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


def describe_validation_errors(errors: list[dict]) -> str:
    """Перетворює помилки Pydantic/FastAPI на одне зрозуміле речення українською."""
    extra = [str(e["loc"][-1]) for e in errors if e["type"] == "extra_forbidden"]
    if extra:
        return f"Невідомі поля: {', '.join(sorted(extra))}"

    error = errors[0]
    kind = error["type"]
    loc = [str(p) for p in error.get("loc", ()) if p != "body"]
    field = loc[0] if loc else None

    if kind == "json_invalid":
        return "Тіло запиту не є коректним JSON"
    if field is None or field.isdigit():
        return "Тіло запиту має бути JSON-об'єктом"
    if kind == "missing":
        return f"Відсутнє обов'язкове поле {field}"
    if kind.startswith("int"):
        return f"Поле {field} має бути типу integer"
    if kind.startswith("string_too_short"):
        return f"Поле {field} не може бути порожнім"
    if kind.startswith("string_too_long"):
        return f"Поле {field} задовге"
    if kind.startswith("string"):
        return f"Поле {field} має бути типу string"
    if kind == "value_error":
        reason = str(error.get("ctx", {}).get("error", error.get("msg", "")))
        return f"Поле {field} {reason}"
    return f"Поле {field}: {error.get('msg', 'некоректне значення')}"
