"""Шлюз перед справжнім Booking API (FastAPI, ЛР 5): маршрутизація й межі відповідальності."""

from datetime import datetime

import pytest
import requests

pytest.importorskip("fastapi")

from auth_support import ANNA, OLENA, TARAS, auth, verifier  # noqa: E402

from fitshed.api.app import create_app  # noqa: E402
from fitshed.api.server import build_service  # noqa: E402
from fitshed.gateway.app import GatewayConfig, create_gateway  # noqa: E402
from fitshed.gateway.bench import serve_asgi  # noqa: E402

NOW = datetime(2026, 12, 1, 9, 0)
PILATES = {
    "title": "Пілатес",
    "trainer_id": "trainer-olena",
    "start": "2026-12-02T10:00:00",
    "end": "2026-12-02T11:00:00",
    "capacity": 3,
}


@pytest.fixture
def gateway_url():
    with serve_asgi(
        create_app(build_service(clock=lambda: NOW), verifier())
    ) as api_url:
        config = GatewayConfig.load()
        config.booking_api = api_url
        app, _ = create_gateway(config)
        with serve_asgi(app) as url:
            yield url


def test_full_booking_flow_through_gateway(gateway_url):
    r = requests.post(
        gateway_url + "/api/classes",
        json=PILATES,
        headers={"X-Request-ID": "gw-flow-1", **auth(*OLENA)},
    )
    assert r.status_code == 201
    assert r.headers["location"].startswith("/classes/")  # заголовок пройшов через шлюз
    assert r.headers["x-request-id"] == "gw-flow-1"
    class_id = r.json()["class_id"]

    booked = requests.post(
        f"{gateway_url}/api/classes/{class_id}/bookings",
        json={"client_id": "anna"},
        headers=auth(*ANNA),
    )
    assert booked.status_code == 201 and booked.json()["result"] == "booked"
    assert booked.headers["x-upstream-attempts"] == "1"

    state = requests.get(f"{gateway_url}/api/classes/{class_id}", headers=auth(*OLENA))
    assert state.status_code == 200 and state.json()["booked"] == ["anna"]


def test_domain_errors_come_from_api_not_gateway(gateway_url):
    """Шлюз не перевіряє бізнес-правил: помилку 400/409 формує Booking API і доменне ядро."""
    bad = requests.post(
        gateway_url + "/api/classes",
        json={**PILATES, "end": "2026-12-02T09:00:00"},
        headers=auth(*OLENA),
    )
    assert bad.status_code == 400 and "пізніше за початок" in bad.json()["detail"]
    assert bad.headers["x-upstream-attempts"] == "1"  # 400 не повторюється

    class_id = requests.post(
        gateway_url + "/api/classes", json=PILATES, headers=auth(*OLENA)
    ).json()["class_id"]
    requests.post(
        f"{gateway_url}/api/classes/{class_id}/bookings",
        json={"client_id": "anna"},
        headers=auth(*ANNA),
    )
    again = requests.post(
        f"{gateway_url}/api/classes/{class_id}/bookings",
        json={"client_id": "anna"},
        headers=auth(*ANNA),
    )
    assert again.status_code == 409 and again.json()["code"] == "rule_violation"
    assert again.headers["x-upstream-attempts"] == "1"  # POST не повторюється


def test_missing_class_is_404_from_api(gateway_url):
    r = requests.get(gateway_url + "/api/classes/cls-missing", headers=auth(*OLENA))
    assert r.status_code == 404 and r.json()["code"] == "not_found"


def test_gateway_does_not_replace_authorization_in_api(gateway_url):
    """Шлюз пропускає токен далі; 401 і 403 формує Booking API — власник даних."""
    missing = requests.get(gateway_url + "/api/classes/cls-1")
    assert missing.status_code == 401 and missing.headers[
        "www-authenticate"
    ].startswith("Bearer")
    assert missing.headers["x-upstream-attempts"] == "1"  # 401 не повторюється

    class_id = requests.post(
        gateway_url + "/api/classes", json=PILATES, headers=auth(*OLENA)
    ).json()["class_id"]
    foreign = requests.post(
        f"{gateway_url}/api/classes/{class_id}/cancellation",
        json={"reason": "x"},
        headers=auth(*TARAS),
    )
    assert foreign.status_code == 403
    assert (
        requests.get(
            f"{gateway_url}/api/classes/{class_id}", headers=auth(*OLENA)
        ).json()["status"]
        == "scheduled"
    )
