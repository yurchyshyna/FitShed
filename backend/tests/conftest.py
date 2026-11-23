from datetime import datetime, timedelta

import pytest

from fitshed.booking.domain import Capacity, GroupClass, TimeSlot

# Фіксований «зараз», щоб тести не залежали від реального годинника.
NOW = datetime(2026, 12, 1, 10, 0)
START = NOW + timedelta(days=1)  # заняття завтра о 10:00


@pytest.fixture
def now() -> datetime:
    return NOW


@pytest.fixture
def pilates() -> GroupClass:
    """Пілатес на 3 місця завтра з 10:00 до 11:00."""
    return GroupClass(
        class_id="pilates-0412",
        title="Пілатес",
        trainer_id="trainer-olena",
        slot=TimeSlot(START, START + timedelta(hours=1)),
        capacity=Capacity(3),
    )

