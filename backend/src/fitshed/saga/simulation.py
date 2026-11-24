"""Збирає локальну симуляцію: два учасники з окремими сховищами, оркестратор і журнал."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta

from fitshed.booking.adapters.in_memory import InMemoryGroupClassRepository
from fitshed.booking.domain import Capacity, GroupClass, TimeSlot
from fitshed.memberships.domain import Membership

from .journal import Journal
from .orchestrator import BookingSagaOrchestrator
from .participants import BookingParticipant, MembershipParticipant

NOW = datetime(2026, 12, 1, 10, 0)
CLASS_ID = "pilates-0412"
CLIENT_ID = "anna"
MEMBERSHIP_ID = "m-anna"


@dataclass
class Simulation:
    journal: Journal
    class_repo: InMemoryGroupClassRepository
    booking: BookingParticipant
    memberships: MembershipParticipant
    orchestrator: BookingSagaOrchestrator

    def snapshot(self) -> dict:
        """Стан обох учасників у зручному для порівняння вигляді."""
        group_class = self.class_repo.get(CLASS_ID)
        membership = self.memberships.get(MEMBERSHIP_ID)
        return {
            "записані на заняття": [c.value for c in group_class.booked],
            "вільних місць": group_class.free_places,
            "відвідувань на абонементі": membership.visits_left,
        }


def build(visits_left: int = 5, places: int = 3) -> Simulation:
    journal = Journal()
    clock = lambda: NOW  # noqa: E731 — фіксований час, щоб результат був відтворюваним

    class_repo = InMemoryGroupClassRepository()
    start = NOW + timedelta(days=1)
    class_repo.save(GroupClass(CLASS_ID, "Пілатес", "trainer-olena",
                               TimeSlot(start, start + timedelta(hours=1)), Capacity(places)))

    memberships = {MEMBERSHIP_ID: Membership(MEMBERSHIP_ID, CLIENT_ID, visits_left, date(2027, 1, 31))}

    booking = BookingParticipant(class_repo, clock, journal)
    membership_participant = MembershipParticipant(memberships, clock, journal)
    orchestrator = BookingSagaOrchestrator(booking, membership_participant, journal)
    return Simulation(journal, class_repo, booking, membership_participant, orchestrator)
