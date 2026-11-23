"""Перевірка агрегату «Групове заняття»: інваріанти, переходи стану, події."""
from datetime import timedelta

import pytest

from fitshed.booking.domain import (
    BookingCancelled,
    Capacity,
    ClassStatus,
    ClientAddedToWaitlist,
    ClientBooked,
    ClientId,
    ClientPromotedFromWaitlist,
    ForbiddenTransitionError,
    GroupClassCancelled,
    InvalidValueError,
    InvariantViolationError,
    GroupClass,
)

ANNA, BOHDAN, VIRA, HALYNA = (ClientId(n) for n in ("anna", "bohdan", "vira", "halyna"))


# ---------- коректне створення ----------
def test_new_class_is_scheduled_and_empty(pilates):
    assert pilates.status is ClassStatus.SCHEDULED
    assert pilates.booked == ()
    assert pilates.waitlist == ()
    assert pilates.free_places == 3


def test_class_without_title_is_rejected(pilates):
    with pytest.raises(InvalidValueError):
        GroupClass("x", "  ", "trainer", pilates.slot, Capacity(10))


# ---------- успішна операція формує подію ----------
def test_booking_adds_client_and_records_event(pilates, now):
    pilates.book(ANNA, now)

    assert pilates.booked == (ANNA,)
    events = pilates.pull_events()
    assert events == [ClientBooked("pilates-0412", now, ANNA)]
    assert pilates.pull_events() == []  # події віддаються лише один раз


# ---------- інваріант 1: місць не більше, ніж є ----------
def test_when_class_is_full_client_goes_to_waitlist(pilates, now):
    for c in (ANNA, BOHDAN, VIRA):
        pilates.book(c, now)
    pilates.pull_events()

    pilates.book(HALYNA, now)

    assert len(pilates.booked) == 3
    assert pilates.waitlist == (HALYNA,)
    assert pilates.pull_events() == [ClientAddedToWaitlist("pilates-0412", now, HALYNA, position=1)]


def test_capacity_cannot_be_reduced_below_booked_clients(pilates, now):
    for c in (ANNA, BOHDAN, VIRA):
        pilates.book(c, now)

    with pytest.raises(InvariantViolationError, match="не можна зменшити"):
        pilates.change_capacity(Capacity(2), now)
    assert pilates.capacity == Capacity(3)  # стан не змінився


def test_increasing_capacity_promotes_from_waitlist(pilates, now):
    for c in (ANNA, BOHDAN, VIRA, HALYNA):
        pilates.book(c, now)
    pilates.pull_events()

    pilates.change_capacity(Capacity(4), now)

    assert HALYNA in pilates.booked
    assert pilates.waitlist == ()
    assert pilates.pull_events() == [ClientPromotedFromWaitlist("pilates-0412", now, HALYNA)]


# ---------- інваріант 2: клієнт на занятті лише один раз ----------
def test_same_client_cannot_book_twice(pilates, now):
    pilates.book(ANNA, now)
    with pytest.raises(InvariantViolationError, match="уже записаний"):
        pilates.book(ANNA, now)
    assert pilates.booked == (ANNA,)


def test_client_from_waitlist_cannot_book_again(pilates, now):
    for c in (ANNA, BOHDAN, VIRA, HALYNA):
        pilates.book(c, now)
    with pytest.raises(InvariantViolationError):
        pilates.book(HALYNA, now)


# ---------- скасування запису й лист очікування ----------
def test_cancelling_booking_promotes_first_from_waitlist(pilates, now):
    for c in (ANNA, BOHDAN, VIRA, HALYNA):
        pilates.book(c, now)
    pilates.pull_events()

    pilates.cancel_booking(BOHDAN, now)

    assert BOHDAN not in pilates.booked
    assert HALYNA in pilates.booked
    assert pilates.pull_events() == [
        BookingCancelled("pilates-0412", now, BOHDAN, late=False),
        ClientPromotedFromWaitlist("pilates-0412", now, HALYNA),
    ]


def test_cancellation_less_than_3_hours_before_start_is_late(pilates):
    two_hours_before = pilates.slot.start - timedelta(hours=2)
    pilates.book(ANNA, two_hours_before - timedelta(days=1))
    pilates.pull_events()

    pilates.cancel_booking(ANNA, two_hours_before)

    [event] = pilates.pull_events()
    assert event.late is True


# ---------- переходи стану ----------
def test_scheduled_class_can_be_cancelled_and_clients_are_listed(pilates, now):
    pilates.book(ANNA, now)
    pilates.pull_events()

    pilates.cancel("Тренер захворів", now)

    assert pilates.status is ClassStatus.CANCELLED
    assert pilates.pull_events() == [
        GroupClassCancelled("pilates-0412", now, "Тренер захворів", (ANNA,))
    ]


def test_cancelled_class_cannot_be_completed(pilates, now):
    pilates.cancel("Зал на ремонті", now)
    with pytest.raises(ForbiddenTransitionError, match="заборонено"):
        pilates.complete(pilates.slot.end + timedelta(minutes=1))


def test_completed_class_cannot_be_cancelled(pilates):
    pilates.complete(pilates.slot.end + timedelta(minutes=1))
    with pytest.raises(ForbiddenTransitionError):
        pilates.cancel("Запізно", pilates.slot.end + timedelta(hours=1))


def test_class_cannot_be_completed_before_it_ends(pilates):
    with pytest.raises(ForbiddenTransitionError, match="ще не закінчилося"):
        pilates.complete(pilates.slot.start)


def test_cannot_book_cancelled_class(pilates, now):
    pilates.cancel("Тренер захворів", now)
    with pytest.raises(ForbiddenTransitionError):
        pilates.book(ANNA, now)


def test_cannot_book_after_class_has_started(pilates):
    with pytest.raises(ForbiddenTransitionError, match="вже почалося"):
        pilates.book(ANNA, pilates.slot.start + timedelta(minutes=5))


def test_failed_operation_does_not_record_event(pilates, now):
    pilates.book(ANNA, now)
    pilates.pull_events()
    with pytest.raises(InvariantViolationError):
        pilates.book(ANNA, now)
    assert pilates.pull_events() == []
