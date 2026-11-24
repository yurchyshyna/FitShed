"""Контрактна перевірка на боці провайдера.

Ті самі взаємодії з файлу очікувань відтворюються на СПРАВЖНЬОМУ сервері
(HTTP-адаптер + BookingService + доменне ядро + сховище в пам'яті).
Для кожної взаємодії спершу готується стан провайдера (provider_state).
"""
import json
import urllib.error
import urllib.request
from datetime import datetime, timedelta

import pytest

from contract_support import check_body, interactions, running
from fitshed.api.server import build_api, make_handler
from fitshed.booking.adapters.in_memory import InMemoryGroupClassRepository
from fitshed.booking.application.booking_service import BookingService

NOW = datetime(2026, 12, 1, 9, 0)


def prepare_state(service: BookingService, state: str) -> None:
    """Стани провайдера, на які посилається файл очікувань."""
    start = datetime(2030, 1, 10, 10, 0)

    def pilates():
        service.schedule_class("cls-test", "Пілатес", "trainer-olena", start, start + timedelta(hours=1), 3)

    if state == "немає занять":
        return
    if state == "заняття cls-test на 3 місця без записів":
        pilates()
    elif state == "заняття cls-test на 3 місця, записана anna":
        pilates()
        service.book("cls-test", "anna")
    elif state == "заповнене заняття cls-test на 3 місця":
        pilates()
        for name in ("anna", "bohdan", "vira"):
            service.book("cls-test", name)
    elif state == "скасоване заняття cls-test":
        pilates()
        service.cancel_class("cls-test", "Ремонт залу")
    else:
        raise AssertionError(f"Невідомий стан провайдера: {state}")


def send(url: str, method: str, path: str, body: dict | None) -> tuple[int, dict]:
    data = None if body is None else json.dumps(body).encode()
    request = urllib.request.Request(url + path, data=data, method=method,
                                     headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(request, timeout=5) as response:
            return response.status, json.loads(response.read())
    except urllib.error.HTTPError as error:
        return error.code, json.loads(error.read())


@pytest.mark.parametrize("interaction", interactions(), ids=lambda i: i["id"])
def test_provider_honours_client_expectations(interaction):
    repo = InMemoryGroupClassRepository()
    service = BookingService(repo, clock=lambda: NOW)
    prepare_state(service, interaction["provider_state"])
    api = build_api(clock=lambda: NOW, repo=repo)

    with running(make_handler(api, quiet=True)) as url:
        req = interaction["request"]
        status, body = send(url, req["method"], req["path"], req.get("body"))

    expected = interaction["response"]
    problems = [] if status == expected["status"] else [f"статус {status}, очікувався {expected['status']}"]
    problems += check_body(body, expected["body"])
    assert not problems, f"«{interaction['description']}»: " + "; ".join(problems)
