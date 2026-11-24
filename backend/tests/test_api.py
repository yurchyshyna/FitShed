"""Поведінка HTTP API через справжній локальний сервер (ЛР 5)."""
import json
import urllib.error
import urllib.request
from datetime import datetime
from pathlib import Path

import pytest

from contract_support import running
from fitshed.api.server import build_api, make_handler

NOW = datetime(2026, 12, 1, 9, 0)
PILATES = {"title": "Пілатес", "trainer_id": "trainer-olena",
           "start": "2026-12-02T10:00:00", "end": "2026-12-02T11:00:00", "capacity": 3}


def call(url, method, path, body=None, raw: bytes | None = None):
    data = raw if raw is not None else (None if body is None else json.dumps(body).encode())
    request = urllib.request.Request(url + path, data=data, method=method,
                                     headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(request, timeout=5) as r:
            return r.status, dict(r.headers), json.loads(r.read())
    except urllib.error.HTTPError as e:
        return e.code, dict(e.headers), json.loads(e.read())


@pytest.fixture
def url():
    with running(make_handler(build_api(clock=lambda: NOW), quiet=True)) as base:
        yield base


# ---------- успішний сценарій ----------
def test_full_scenario_create_book_read_cancel(url):
    status, headers, created = call(url, "POST", "/classes", PILATES)
    assert status == 201
    assert headers["Location"] == f"/classes/{created['class_id']}"
    assert created["status"] == "scheduled" and created["free_places"] == 3
    cid = created["class_id"]

    for name in ("anna", "bohdan", "vira"):
        assert call(url, "POST", f"/classes/{cid}/bookings", {"client_id": name})[0] == 201
    status, _, waitlisted = call(url, "POST", f"/classes/{cid}/bookings", {"client_id": "halyna"})
    assert status == 201 and waitlisted["result"] == "waitlisted" and waitlisted["waitlist_position"] == 1

    status, _, state = call(url, "GET", f"/classes/{cid}")
    assert status == 200
    assert state["booked"] == ["anna", "bohdan", "vira"] and state["waitlist"] == ["halyna"]
    assert state["free_places"] == 0

    status, _, cancelled = call(url, "POST", f"/classes/{cid}/cancellation", {"reason": "Тренер захворів"})
    assert status == 200 and cancelled["status"] == "cancelled"


# ---------- некоректні дані (400) ----------
@pytest.mark.parametrize("body, fragment", [
    ({k: v for k, v in PILATES.items() if k != "title"}, "Відсутнє обов'язкове поле title"),
    ({**PILATES, "capacity": "3"}, "capacity має бути типу integer"),
    ({**PILATES, "capacity": True}, "capacity має бути типу integer"),
    ({**PILATES, "start": "завтра"}, "ISO 8601"),
    ({**PILATES, "colour": "red"}, "Невідомі поля"),
    ({**PILATES, "end": "2026-12-02T09:00:00"}, "пізніше за початок"),   # правило домену (TimeSlot)
    ({**PILATES, "capacity": 41}, "від 1 до 40"),                         # правило домену (Capacity)
])
def test_invalid_class_data_is_rejected_with_reason(url, body, fragment):
    status, headers, problem = call(url, "POST", "/classes", body)
    assert status == 400
    assert headers["Content-Type"].startswith("application/problem+json")
    assert problem["code"] == "invalid_input"
    assert fragment in problem["detail"]


def test_malformed_json_is_rejected(url):
    status, _, problem = call(url, "POST", "/classes", raw=b"{title: ")
    assert status == 400 and "JSON" in problem["detail"]


def test_empty_client_id_is_rejected(url):
    cid = call(url, "POST", "/classes", PILATES)[2]["class_id"]
    status, _, problem = call(url, "POST", f"/classes/{cid}/bookings", {"client_id": "  "})
    assert status == 400 and problem["code"] == "invalid_input"


# ---------- відсутній ресурс (404) ----------
@pytest.mark.parametrize("method, path, body", [
    ("GET", "/classes/cls-missing", None),
    ("POST", "/classes/cls-missing/bookings", {"client_id": "anna"}),
    ("POST", "/classes/cls-missing/cancellation", {"reason": "x"}),
])
def test_missing_class_returns_404(url, method, path, body):
    status, _, problem = call(url, method, path, body)
    assert status == 404 and problem["code"] == "not_found"


# ---------- заборонений перехід стану / конфлікт (409) ----------
def test_cancelling_twice_is_forbidden_transition(url):
    cid = call(url, "POST", "/classes", PILATES)[2]["class_id"]
    call(url, "POST", f"/classes/{cid}/cancellation", {"reason": "Ремонт"})
    status, _, problem = call(url, "POST", f"/classes/{cid}/cancellation", {"reason": "Ще раз"})
    assert status == 409 and problem["code"] == "invalid_state"
    assert "заборонено" in problem["detail"]


def test_booking_cancelled_class_is_conflict(url):
    cid = call(url, "POST", "/classes", PILATES)[2]["class_id"]
    call(url, "POST", f"/classes/{cid}/cancellation", {"reason": "Ремонт"})
    status, _, problem = call(url, "POST", f"/classes/{cid}/bookings", {"client_id": "anna"})
    assert status == 409 and problem["code"] == "invalid_state"


def test_double_booking_is_rule_violation(url):
    cid = call(url, "POST", "/classes", PILATES)[2]["class_id"]
    call(url, "POST", f"/classes/{cid}/bookings", {"client_id": "anna"})
    status, _, problem = call(url, "POST", f"/classes/{cid}/bookings", {"client_id": "anna"})
    assert status == 409 and problem["code"] == "rule_violation"


def test_wrong_method_returns_405(url):
    status, headers, _ = call(url, "DELETE", "/classes/cls-1")
    assert status == 405 and headers["Allow"] == "GET"


# ---------- відповіді відповідають схемам OpenAPI ----------
def test_responses_match_openapi_schemas(url):
    yaml = pytest.importorskip("yaml")
    jsonschema = pytest.importorskip("jsonschema")
    spec = yaml.safe_load((Path(__file__).parents[1] / "openapi.yaml").read_text(encoding="utf-8"))
    assert spec["openapi"] == "3.1.1"
    schemas = spec["components"]["schemas"]

    def validate(instance, name):
        # OpenAPI 3.1 використовує JSON Schema 2020-12; посилання розв'язуємо в межах документа
        schema = {"$ref": f"#/components/schemas/{name}", "components": spec["components"]}
        jsonschema.Draft202012Validator(schema).validate(instance)

    created = call(url, "POST", "/classes", PILATES)[2]
    validate(created, "GroupClass")
    validate(call(url, "POST", f"/classes/{created['class_id']}/bookings", {"client_id": "anna"})[2], "Booking")
    validate(call(url, "GET", "/classes/nope")[2], "Problem")
    assert set(schemas) >= {"GroupClass", "Booking", "Problem", "NewClass", "NewBooking", "Cancellation"}


def test_every_operation_documents_success_and_errors():
    yaml = pytest.importorskip("yaml")
    spec = yaml.safe_load((Path(__file__).parents[1] / "openapi.yaml").read_text(encoding="utf-8"))
    for path, item in spec["paths"].items():
        for method, op in item.items():
            if method == "parameters":
                continue
            codes = set(op["responses"])
            assert codes & {"200", "201", "202"}, f"{method.upper()} {path}: немає успішної відповіді"
            assert codes & {"400", "404", "409"}, f"{method.upper()} {path}: немає опису помилок"
