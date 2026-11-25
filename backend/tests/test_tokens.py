"""Перевірка access token і політики доступу без HTTP (ЛР 8)."""
import time
from datetime import datetime, timedelta

import jwt
import pytest

from auth_support import CONFIG, keys, token, verifier
from fitshed.api.authz import Forbidden, can_book, can_cancel_class, can_create_class, visible_class
from fitshed.api.schemas import class_to_json
from fitshed.auth.tokens import AuthError, LocalIssuer, Principal, bearer_token, generate_key_pair
from fitshed.booking.domain import Capacity, ClientId, GroupClass, TimeSlot


# ---------- перевірка токена ----------
def test_valid_token_gives_identity_only_from_claims():
    assert verifier().verify(token("trainer-olena", "trainer")) == Principal("trainer-olena", "trainer")


def test_tampered_signature_is_rejected():
    head, payload, signature = token("anna", "client").split(".")
    forged = f"{head}.{payload}.{signature[:-4]}AAAA"
    with pytest.raises(AuthError, match="підпис"):
        verifier().verify(forged)


def test_payload_swapped_from_other_token_is_rejected():
    """Підміна вмісту (роль client → admin) без нового підпису."""
    head, _, signature = token("anna", "client").split(".")
    _, admin_payload, _ = token("anna", "admin").split(".")
    with pytest.raises(AuthError):
        verifier().verify(f"{head}.{admin_payload}.{signature}")


def test_token_signed_by_other_key_is_rejected():
    other = LocalIssuer(CONFIG, generate_key_pair()[0]).issue("anna", "client")
    with pytest.raises(AuthError, match="підпис"):
        verifier().verify(other)


def test_expired_token_is_rejected():
    old = token("anna", "client", ttl_seconds=60, now=time.time() - 3600)
    with pytest.raises(AuthError, match="строк дії"):
        verifier().verify(old)


def test_wrong_audience_is_rejected():
    with pytest.raises(AuthError, match="аудиторії"):
        verifier().verify(token("anna", "client", audience="fitshed-reports-api"))


def test_wrong_issuer_is_rejected():
    with pytest.raises(AuthError, match="емітент"):
        verifier().verify(token("anna", "client", issuer="https://evil.example"))


@pytest.mark.parametrize("alg", ["none", "HS256"])
def test_other_algorithms_are_rejected(alg):
    payload = {"iss": CONFIG.issuer, "aud": CONFIG.audience, "sub": "anna", "role": "admin",
               "iat": int(time.time()), "exp": int(time.time()) + 600}
    if alg == "none":
        forged = jwt.encode(payload, None, algorithm="none")
    else:
        # HS256 з відкритим ключем як «секретом» — класична атака підміни алгоритму; токен складено вручну
        import base64, hashlib, hmac, json

        def b64(data: bytes) -> str:
            return base64.urlsafe_b64encode(data).rstrip(b"=").decode()
        signing_input = f"{b64(json.dumps({'alg': 'HS256', 'typ': 'JWT'}).encode())}.{b64(json.dumps(payload).encode())}"
        signature = hmac.new(keys()[1], signing_input.encode(), hashlib.sha256).digest()
        forged = f"{signing_input}.{b64(signature)}"
    with pytest.raises(AuthError, match="алгоритм"):
        verifier().verify(forged)


def test_unknown_role_is_rejected():
    private_key = keys()[0]
    payload = {"iss": CONFIG.issuer, "aud": CONFIG.audience, "sub": "x", "role": "superuser",
               "iat": int(time.time()), "exp": int(time.time()) + 600}
    with pytest.raises(AuthError, match="ролі"):
        verifier().verify(jwt.encode(payload, private_key, algorithm="RS256"))


@pytest.mark.parametrize("header", [None, "", "Basic abc", "Bearer ", "token-without-scheme"])
def test_bearer_header_is_required(header):
    with pytest.raises(AuthError):
        bearer_token(header)


# ---------- політика доступу ----------
START = datetime(2030, 1, 10, 10, 0)


def olena_class() -> GroupClass:
    gc = GroupClass("cls-1", "Пілатес", "trainer-olena", TimeSlot(START, START + timedelta(hours=1)), Capacity(3))
    gc.book(ClientId("anna"), START - timedelta(days=1))
    gc.book(ClientId("bohdan"), START - timedelta(days=1))
    return gc


def test_trainer_can_cancel_only_own_class():
    can_cancel_class(Principal("trainer-olena", "trainer"), olena_class())
    with pytest.raises(Forbidden, match="власні"):
        can_cancel_class(Principal("trainer-taras", "trainer"), olena_class())


def test_client_cannot_cancel_and_admin_can():
    with pytest.raises(Forbidden):
        can_cancel_class(Principal("anna", "client"), olena_class())
    can_cancel_class(Principal("admin-iryna", "admin"), olena_class())


def test_client_can_book_only_self():
    can_book(Principal("anna", "client"), "anna")
    with pytest.raises(Forbidden, match="лише себе"):
        can_book(Principal("bohdan", "client"), "anna")
    with pytest.raises(Forbidden):
        can_book(Principal("trainer-olena", "trainer"), "anna")


def test_trainer_creates_classes_only_for_self():
    can_create_class(Principal("trainer-olena", "trainer"), "trainer-olena")
    with pytest.raises(Forbidden):
        can_create_class(Principal("trainer-olena", "trainer"), "trainer-taras")
    with pytest.raises(Forbidden):
        can_create_class(Principal("anna", "client"), "trainer-olena")


def test_read_shows_other_clients_only_to_owner_and_admin():
    gc = olena_class()
    full = class_to_json(gc)
    assert visible_class(Principal("trainer-olena", "trainer"), gc, full)["booked"] == ["anna", "bohdan"]
    assert visible_class(Principal("admin-iryna", "admin"), gc, full)["booked"] == ["anna", "bohdan"]
    assert visible_class(Principal("trainer-taras", "trainer"), gc, full)["booked"] == []
    assert visible_class(Principal("anna", "client"), gc, full)["booked"] == ["anna"]
    assert visible_class(Principal("anna", "client"), gc, full)["free_places"] == 1
