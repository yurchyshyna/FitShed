"""Доменні події: факти, які вже сталися з заняттям."""
from dataclasses import dataclass
from datetime import datetime

from .value_objects import ClientId


@dataclass(frozen=True)
class DomainEvent:
    class_id: str
    occurred_at: datetime


@dataclass(frozen=True)
class ClientBooked(DomainEvent):
    client_id: ClientId


@dataclass(frozen=True)
class ClientAddedToWaitlist(DomainEvent):
    client_id: ClientId
    position: int


@dataclass(frozen=True)
class BookingCancelled(DomainEvent):
    client_id: ClientId
    late: bool  # пізнє скасування: контекст «Абонементи» не повертає відвідування


@dataclass(frozen=True)
class ClientPromotedFromWaitlist(DomainEvent):
    client_id: ClientId


@dataclass(frozen=True)
class GroupClassCancelled(DomainEvent):
    reason: str
    affected_clients: tuple[ClientId, ...]
