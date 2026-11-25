"""Схема події GroupClassCancelled (версія 1) і перевірка вхідних повідомлень.

Приклад повідомлення:
{
  "event_id": "5b1f6c1e-6a0e-4c43-9a8e-2f1d7d2a0c11",
  "event_type": "GroupClassCancelled",
  "schema_version": 1,
  "occurred_at": "2026-12-01T09:00:00",
  "aggregate_id": "cls-1a2b3c4d",
  "data": {
    "class_id": "cls-1a2b3c4d",
    "reason": "Тренер захворів",
    "booked_client_ids": ["anna", "bohdan", "vira"]
  }
}
"""
from __future__ import annotations

import json
import uuid
from dataclasses import dataclass
from datetime import datetime

from fitshed.booking.domain import GroupClass, GroupClassCancelled

EVENT_TYPE = "GroupClassCancelled"
SUPPORTED_SCHEMA_VERSIONS = {1}


class InvalidMessage(Exception):
    """Повідомлення неможливо обробити ні зараз, ні після повтору: його місце в черзі помилок."""


@dataclass(frozen=True)
class ClassCancelledEvent:
    event_id: str
    occurred_at: datetime
    class_id: str
    reason: str
    booked_client_ids: tuple[str, ...]
    schema_version: int = 1

    def to_json(self) -> bytes:
        return json.dumps({
            "event_id": self.event_id,
            "event_type": EVENT_TYPE,
            "schema_version": self.schema_version,
            "occurred_at": self.occurred_at.isoformat(),
            "aggregate_id": self.class_id,
            "data": {
                "class_id": self.class_id,
                "reason": self.reason,
                "booked_client_ids": list(self.booked_client_ids),
            },
        }, ensure_ascii=False).encode("utf-8")


def from_domain(group_class: GroupClass, event: GroupClassCancelled, event_id: str | None = None) -> ClassCancelledEvent:
    """Перетворює доменну подію на повідомлення для брокера.

    Повернути відвідування треба лише тим, хто мав місце, а не листу очікування,
    тому список береться зі стану агрегату на момент скасування.
    """
    return ClassCancelledEvent(
        event_id=event_id or str(uuid.uuid4()),
        occurred_at=event.occurred_at,
        class_id=event.class_id,
        reason=event.reason,
        booked_client_ids=tuple(c.value for c in group_class.booked),
    )


def parse(body: bytes) -> ClassCancelledEvent:
    """Перевіряє повідомлення за схемою. Будь-яка невідповідність — InvalidMessage з причиною."""
    try:
        raw = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise InvalidMessage("тіло не є коректним JSON у UTF-8") from None
    if not isinstance(raw, dict):
        raise InvalidMessage("повідомлення має бути JSON-об'єктом")

    for field in ("event_id", "event_type", "schema_version", "occurred_at", "aggregate_id", "data"):
        if field not in raw:
            raise InvalidMessage(f"немає обов'язкового поля {field}")
    if raw["event_type"] != EVENT_TYPE:
        raise InvalidMessage(f"непідтримуваний тип події {raw['event_type']!r}")
    if raw["schema_version"] not in SUPPORTED_SCHEMA_VERSIONS:
        raise InvalidMessage(f"непідтримувана версія схеми {raw['schema_version']!r}")
    if not isinstance(raw["event_id"], str) or not raw["event_id"].strip():
        raise InvalidMessage("event_id має бути непорожнім рядком")
    try:
        occurred_at = datetime.fromisoformat(raw["occurred_at"])
    except (TypeError, ValueError):
        raise InvalidMessage("occurred_at має бути датою й часом ISO 8601") from None

    data = raw["data"]
    if not isinstance(data, dict):
        raise InvalidMessage("data має бути об'єктом")
    clients = data.get("booked_client_ids")
    if not isinstance(clients, list) or not all(isinstance(c, str) and c.strip() for c in clients):
        raise InvalidMessage("data.booked_client_ids має бути списком непорожніх рядків")
    if len(set(clients)) != len(clients):
        raise InvalidMessage("data.booked_client_ids містить повтори")
    if not isinstance(data.get("class_id"), str) or data["class_id"] != raw["aggregate_id"]:
        raise InvalidMessage("data.class_id має збігатися з aggregate_id")

    return ClassCancelledEvent(raw["event_id"], occurred_at, data["class_id"],
                               str(data.get("reason", "")), tuple(clients), raw["schema_version"])
