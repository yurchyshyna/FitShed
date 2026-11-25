"""Перевірки доступу ЛР 8 через справжній HTTP (FastAPI + uvicorn).

Для заборонених дій додатково перевіряється, що захищений стан (заняття в сховищі) не змінився:
стан читається напряму з прикладного сервісу, а не через API.
"""
import json
import time
import urllib.error
import urllib.request
from datetime import datetime

import pytest

from auth_support import ADMIN, ANNA, BOHDAN, OLENA, TARAS, auth, token, verifier
from contract_support import running_app
from fitshed.api.app import create_app
from fitshed.api.server import build_service
from fitshed.booking.domain import ClassStatus, ClientId

NOW = datetime(2026, 12, 1, 9, 0)
PILATES = {"title": "Пілатес", "trainer_id": "trainer-olena",
           "start": "2026-12-02T10:00:00", "end": "2026-12-02T11:00:00", "capacity": 3}


def call(url, method, path, body=None, headers=None):
    data = None if body is None else json.dumps(body).encode()
    request = urllib.request.Request(url + path, data=data, method=method,
                                     headers={"Content-Type": "application/json", **(headers or {})})
    try:
        with urllib.request.urlopen(request, timeout=5) as r:
            return r.status, r.headers, json.loads(r.read())
    except urllib.error.HTTPError as e:
        return e.code, e.headers, json.loads(e.read())


@pytest.fixture
def stand():
    """Заняття тренерки Олени, на яке записана Анна."""
    service = build_service(clock=lambda: NOW)
    ids = iter(f"cls-{n}" for n in range(1, 100))
    with running_app(create_app(service, verifier(), id_factory=lambda: next(ids))) as url:
        status, _, created = call(url, "POST", "/classes", PILATES, auth(*OLENA))
        assert status == 201
        assert call(url, "POST", f"/classes/{created['class_id']}/bookings", {"client_id": "anna"}, auth(*ANNA))[0] == 201
        yield url, service, created["class_id"]


def snapshot(service, class_id):
    gc = service.get_class(class_id)
    return gc.status, gc.booked, gc.waitlist


# ---------- дозволений доступ ----------
def test_owner_trainer_can_cancel_own_class(stand):
    url, service, cid = stand
    status, _, body = call(url, "POST", f"/classes/{cid}/cancellation", {"reason": "Тренерка захворіла"}, auth(*OLENA))
    assert status == 200 and body["status"] == "cancelled"
    assert service.get_class(cid).status is ClassStatus.CANCELLED


def test_admin_can_cancel_any_class(stand):
    url, service, cid = stand
    assert call(url, "POST", f"/classes/{cid}/cancellation", {"reason": "Ремонт"}, auth(*ADMIN))[0] == 200


def test_client_books_self_and_reads_class(stand):
    url, service, cid = stand
    assert call(url, "POST", f"/classes/{cid}/bookings", {"client_id": "bohdan"}, auth(*BOHDAN))[0] == 201
    status, _, body = call(url, "GET", f"/classes/{cid}", headers=auth(*BOHDAN))
    assert status == 200 and body["booked"] == ["bohdan"]      # чужих імен клієнт не бачить
    assert body["free_places"] == 1


# ---------- 401: облікові дані відсутні або невалідні ----------
def tampered() -> str:
    head, payload, signature = token(*OLENA).split(".")
    return f"{head}.{payload}.{signature[:-4]}AAAA"


@pytest.mark.parametrize("case, headers", [
    ("немає токена", {}),
    ("не Bearer", {"Authorization": "Basic b2xlbmE6cXdlcnR5"}),
    ("змінений підпис", {"Authorization": f"Bearer {tampered()}"}),
    ("прострочений", {"Authorization": f"Bearer {token(*OLENA, ttl_seconds=60, now=time.time() - 3600)}"}),
    ("інша аудиторія", {"Authorization": f"Bearer {token(*OLENA, audience='fitshed-reports-api')}"}),
    ("інший емітент", {"Authorization": f"Bearer {token(*OLENA, issuer='https://evil.example')}"}),
], ids=["missing", "not-bearer", "bad-signature", "expired", "wrong-aud", "wrong-iss"])
def test_invalid_credentials_give_401_and_change_nothing(stand, case, headers):
    url, service, cid = stand
    before = snapshot(service, cid)
    status, h, problem = call(url, "POST", f"/classes/{cid}/cancellation", {"reason": "спроба"}, headers)
    assert status == 401, case
    assert problem["code"] == "unauthorized"
    assert h["WWW-Authenticate"].startswith("Bearer")
    assert snapshot(service, cid) == before


def test_401_comes_before_input_validation(stand):
    """Неавтентифікований клієнт не дізнається навіть про формат запиту."""
    url, _, _ = stand
    assert call(url, "POST", "/classes", {"щось": "не те"})[0] == 401


# ---------- 403: роль не дозволяє операцію ----------
def test_client_role_cannot_cancel_class_and_state_is_unchanged(stand):
    url, service, cid = stand
    before = snapshot(service, cid)
    status, _, problem = call(url, "POST", f"/classes/{cid}/cancellation", {"reason": "не хочу"}, auth(*ANNA))
    assert status == 403 and problem["code"] == "forbidden"
    assert snapshot(service, cid) == before
    assert service.get_class(cid).status is ClassStatus.SCHEDULED


def test_trainer_role_cannot_book_clients(stand):
    url, service, cid = stand
    before = snapshot(service, cid)
    assert call(url, "POST", f"/classes/{cid}/bookings", {"client_id": "vira"}, auth(*OLENA))[0] == 403
    assert snapshot(service, cid) == before


# ---------- 403: чужий об'єкт під тим самим типом ролі ----------
def test_other_trainer_cannot_cancel_foreign_class_and_state_is_unchanged(stand):
    url, service, cid = stand
    before = snapshot(service, cid)
    status, _, problem = call(url, "POST", f"/classes/{cid}/cancellation", {"reason": "підміна id"}, auth(*TARAS))
    assert status == 403 and "власні" in problem["detail"]
    assert snapshot(service, cid) == before
    assert service.get_class(cid).status is ClassStatus.SCHEDULED


def test_client_cannot_book_on_behalf_of_other_client_and_state_is_unchanged(stand):
    url, service, cid = stand
    before = snapshot(service, cid)
    status, _, _ = call(url, "POST", f"/classes/{cid}/bookings", {"client_id": "vira"}, auth(*BOHDAN))
    assert status == 403
    assert snapshot(service, cid) == before
    assert ClientId("vira") not in service.get_class(cid).booked


def test_trainer_cannot_create_class_for_other_trainer(stand):
    url, service, _ = stand
    status, _, _ = call(url, "POST", "/classes", {**PILATES, "trainer_id": "trainer-olena"}, auth(*TARAS))
    assert status == 403
    from fitshed.booking.domain import GroupClassNotFoundError
    with pytest.raises(GroupClassNotFoundError):
        service.get_class("cls-2")            # нового заняття не створено


def test_other_trainer_sees_class_without_client_names(stand):
    url, _, cid = stand
    assert call(url, "GET", f"/classes/{cid}", headers=auth(*TARAS))[2]["booked"] == []
    assert call(url, "GET", f"/classes/{cid}", headers=auth(*OLENA))[2]["booked"] == ["anna"]
