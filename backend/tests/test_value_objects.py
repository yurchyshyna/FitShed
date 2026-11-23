"""Перевірка об'єктів-значень: коректне створення та недопустимі значення."""
from datetime import datetime, timedelta

import pytest

from fitshed.booking.domain import Capacity, ClientId, InvalidValueError, TimeSlot

START = datetime(2026, 12, 2, 10, 0)


def test_time_slot_is_created_when_end_is_after_start():
    slot = TimeSlot(START, START + timedelta(minutes=55))
    assert slot.end > slot.start


@pytest.mark.parametrize("end", [START, START - timedelta(minutes=30)])
def test_time_slot_rejects_end_not_after_start(end):
    with pytest.raises(InvalidValueError, match="пізніше за початок"):
        TimeSlot(START, end)


def test_time_slot_rejects_too_long_class():
    with pytest.raises(InvalidValueError, match="3 годин"):
        TimeSlot(START, START + timedelta(hours=4))


def test_value_objects_are_equal_by_value():
    # Два окремі об'єкти з однаковими даними — це одне й те саме значення.
    assert TimeSlot(START, START + timedelta(hours=1)) == TimeSlot(START, START + timedelta(hours=1))
    assert ClientId("anna") == ClientId("anna")


@pytest.mark.parametrize("places", [0, -5, 41])
def test_capacity_rejects_invalid_number_of_places(places):
    with pytest.raises(InvalidValueError):
        Capacity(places)


def test_client_id_cannot_be_empty():
    with pytest.raises(InvalidValueError):
        ClientId("   ")
