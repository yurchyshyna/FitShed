"""Перевірка порту сховища, адаптера в пам'яті та прикладного сервісу."""
from datetime import datetime, timedelta

import pytest

from fitshed.booking.adapters.in_memory import InMemoryGroupClassRepository
from fitshed.booking.application.booking_service import BookingService
from fitshed.booking.domain import (
    ClientBooked,
    ClientId,
    GroupClassNotFoundError,
    GroupClassRepository,
)


@pytest.fixture
def repo() -> GroupClassRepository:
    # Тест працює з портом; адаптер можна замінити на будь-який інший.
    return InMemoryGroupClassRepository()


def test_saved_class_can_be_loaded(repo, pilates, now):
    pilates.book(ClientId("anna"), now)
    repo.save(pilates)

    loaded = repo.get("pilates-0412")

    assert loaded is not pilates  # це окрема копія, як з бази даних
    assert loaded.title == "Пілатес"
    assert loaded.booked == (ClientId("anna"),)
    assert loaded.slot == pilates.slot
    assert loaded.pull_events() == []  # події не зберігаються разом з агрегатом


def test_changes_are_not_stored_until_save(repo, pilates, now):
    repo.save(pilates)
    loaded = repo.get("pilates-0412")
    loaded.book(ClientId("anna"), now)

    assert repo.get("pilates-0412").booked == ()


def test_loading_unknown_class_raises(repo):
    with pytest.raises(GroupClassNotFoundError):
        repo.get("no-such-class")


def test_booking_service_works_through_port(repo, now):
    service = BookingService(repo, clock=lambda: now)
    start = now + timedelta(days=2)
    service.schedule_class("yoga-1", "Йога", "trainer-iryna", start, start + timedelta(hours=1), places=10)

    events = service.book("yoga-1", "anna")

    assert events == [ClientBooked("yoga-1", now, ClientId("anna"))]
    assert repo.get("yoga-1").booked == (ClientId("anna"),)


def test_domain_package_does_not_import_infrastructure():
    """Доменне ядро не повинне залежати від адаптерів, FastAPI чи бази даних."""
    import pathlib

    domain_dir = pathlib.Path(__file__).parents[1] / "src" / "fitshed" / "booking" / "domain"
    forbidden = ("adapters", "application", "fastapi", "sqlalchemy", "psycopg")
    for file in domain_dir.glob("*.py"):
        text = file.read_text(encoding="utf-8")
        for word in forbidden:
            assert f"import {word}" not in text and f"from {word}" not in text, f"{file.name} імпортує {word}"
            assert f"fitshed.booking.{word}" not in text, f"{file.name} залежить від {word}"
