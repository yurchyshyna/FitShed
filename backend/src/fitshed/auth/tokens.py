"""Перевірка й випуск JWT access token (RFC 7519, рекомендації RFC 8725).

Перевірка = не декодування. Токен приймається лише якщо:
  - алгоритм саме RS256 (інші, зокрема «none» і HS256, відхиляються ще до перевірки підпису);
  - підпис перевіряється відкритим ключем емітента;
  - iss дорівнює очікуваному емітенту, aud містить очікувану аудиторію;
  - exp не минув (допуск розбіжності годинників — 10 с), iat не в майбутньому;
  - є sub і роль з дозволеного переліку.
Ідентичність (sub) і роль беруться ТІЛЬКИ з перевіреного токена, а не із запиту.

Ключі навчальні: генеруються локально (python -m fitshed.auth.keys), лежать у backend/var/keys
(поза Git) і передаються через змінні оточення. У payload немає секретів: його може прочитати будь-хто.
"""
from __future__ import annotations

import os
import time
import uuid
from dataclasses import dataclass
from pathlib import Path

import jwt
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

ALGORITHM = "RS256"
ROLES = frozenset({"admin", "trainer", "client"})
KEYS_DIR = Path(__file__).resolve().parents[3] / "var" / "keys"


class AuthError(Exception):
    """Облікові дані відсутні або невалідні → HTTP 401."""


@dataclass(frozen=True)
class Principal:
    sub: str
    role: str


@dataclass(frozen=True)
class AuthConfig:
    issuer: str = "fitshed-local-issuer"
    audience: str = "fitshed-booking-api"
    public_key_path: Path = KEYS_DIR / "jwt-public.pem"
    private_key_path: Path = KEYS_DIR / "jwt-private.pem"
    leeway_seconds: int = 10

    @classmethod
    def from_env(cls) -> "AuthConfig":
        return cls(
            issuer=os.environ.get("FITSHED_JWT_ISSUER", cls.issuer),
            audience=os.environ.get("FITSHED_JWT_AUDIENCE", cls.audience),
            public_key_path=Path(os.environ.get("FITSHED_JWT_PUBLIC_KEY", cls.public_key_path)),
            private_key_path=Path(os.environ.get("FITSHED_JWT_PRIVATE_KEY", cls.private_key_path)),
        )


class TokenVerifier:
    """Перевіряє access token. Потрібен лише відкритий ключ."""

    def __init__(self, config: AuthConfig, public_key_pem: bytes | None = None) -> None:
        self.config = config
        if public_key_pem is None:
            if not config.public_key_path.exists():
                raise FileNotFoundError(
                    f"Немає відкритого ключа {config.public_key_path}. Згенеруйте ключі: python -m fitshed.auth.keys")
            public_key_pem = config.public_key_path.read_bytes()
        self._public_key = serialization.load_pem_public_key(public_key_pem)

    def verify(self, token: str) -> Principal:
        try:
            header = jwt.get_unverified_header(token)
        except jwt.InvalidTokenError:
            raise AuthError("токен пошкоджений") from None
        if header.get("alg") != ALGORITHM:
            raise AuthError(f"недозволений алгоритм {header.get('alg')!r}")
        try:
            claims = jwt.decode(
                token, self._public_key, algorithms=[ALGORITHM],
                issuer=self.config.issuer, audience=self.config.audience,
                leeway=self.config.leeway_seconds,
                options={"require": ["exp", "iat", "iss", "aud", "sub"]},
            )
        except jwt.ExpiredSignatureError:
            raise AuthError("строк дії токена минув") from None
        except jwt.InvalidIssuerError:
            raise AuthError("неочікуваний емітент") from None
        except jwt.InvalidAudienceError:
            raise AuthError("токен виданий для іншої аудиторії") from None
        except jwt.InvalidSignatureError:
            raise AuthError("підпис не збігається") from None
        except jwt.MissingRequiredClaimError as error:
            raise AuthError(f"немає обов'язкового поля {error.claim}") from None
        except jwt.InvalidTokenError:
            raise AuthError("токен невалідний") from None

        role = claims.get("role")
        if role not in ROLES or not isinstance(claims["sub"], str) or not claims["sub"]:
            raise AuthError("у токені немає дозволеної ролі або sub")
        return Principal(claims["sub"], role)


class LocalIssuer:
    """Навчальний емітент. Лише для локального стенда й тестів: замінює OAuth/OIDC-сервер."""

    def __init__(self, config: AuthConfig, private_key_pem: bytes | None = None) -> None:
        self.config = config
        if private_key_pem is None:
            if not config.private_key_path.exists():
                raise FileNotFoundError(
                    f"Немає закритого ключа {config.private_key_path}. Згенеруйте ключі: python -m fitshed.auth.keys")
            private_key_pem = config.private_key_path.read_bytes()
        self._private_key = serialization.load_pem_private_key(private_key_pem, password=None)

    def issue(self, sub: str, role: str, ttl_seconds: int = 900, *, issuer: str | None = None,
              audience: str | None = None, now: float | None = None) -> str:
        if role not in ROLES:
            raise ValueError(f"невідома роль {role}")
        issued_at = int(now if now is not None else time.time())
        payload = {
            "iss": issuer or self.config.issuer,
            "aud": audience or self.config.audience,
            "sub": sub,
            "role": role,
            "iat": issued_at,
            "exp": issued_at + ttl_seconds,
            "jti": uuid.uuid4().hex,
        }
        return jwt.encode(payload, self._private_key, algorithm=ALGORITHM, headers={"typ": "at+jwt"})


def generate_key_pair() -> tuple[bytes, bytes]:
    """(закритий PEM, відкритий PEM) — нова пара RSA 2048."""
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    private_pem = key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                    serialization.NoEncryption())
    public_pem = key.public_key().public_bytes(serialization.Encoding.PEM,
                                               serialization.PublicFormat.SubjectPublicKeyInfo)
    return private_pem, public_pem


def bearer_token(authorization: str | None) -> str:
    """Дістає токен із заголовка «Authorization: Bearer <токен>»."""
    if not authorization:
        raise AuthError("немає заголовка Authorization")
    scheme, _, token = authorization.partition(" ")
    if scheme.lower() != "bearer" or not token.strip():
        raise AuthError("очікується схема Bearer")
    return token.strip()
