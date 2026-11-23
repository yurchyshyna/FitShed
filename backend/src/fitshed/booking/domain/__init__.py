"""Доменне ядро контексту «Розклад і запис». Не залежить від бази даних, API чи UI."""
from .errors import (
    DomainError,
    ForbiddenTransitionError,
    GroupClassNotFoundError,
    InvalidValueError,
    InvariantViolationError,
)
from .events import (
    BookingCancelled,
    ClientAddedToWaitlist,
    ClientBooked,
    ClientPromotedFromWaitlist,
    DomainEvent,
    GroupClassCancelled,
)
from .group_class import ALLOWED_TRANSITIONS, ClassStatus, GroupClass
from .ports import GroupClassRepository
from .value_objects import Capacity, ClientId, TimeSlot

__all__ = [
    "ALLOWED_TRANSITIONS", "BookingCancelled", "Capacity", "ClassStatus", "ClientAddedToWaitlist",
    "ClientBooked", "ClientId", "ClientPromotedFromWaitlist", "DomainError", "DomainEvent",
    "ForbiddenTransitionError", "GroupClass", "GroupClassCancelled", "GroupClassNotFoundError",
    "GroupClassRepository", "InvalidValueError", "InvariantViolationError", "TimeSlot",
]
