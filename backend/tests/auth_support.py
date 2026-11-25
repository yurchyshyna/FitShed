"""Синтетичні ключі й токени для тестів (ЛР 8). Ключі генеруються під час тестів і не зберігаються."""
from __future__ import annotations

import functools

from fitshed.auth.tokens import AuthConfig, LocalIssuer, TokenVerifier, generate_key_pair

CONFIG = AuthConfig()


@functools.lru_cache(maxsize=1)
def keys() -> tuple[bytes, bytes]:
    return generate_key_pair()


def issuer() -> LocalIssuer:
    return LocalIssuer(CONFIG, keys()[0])


def verifier() -> TokenVerifier:
    return TokenVerifier(CONFIG, keys()[1])


def token(sub: str, role: str, **kwargs) -> str:
    return issuer().issue(sub, role, **kwargs)


def auth(sub: str, role: str, **kwargs) -> dict[str, str]:
    return {"Authorization": f"Bearer {token(sub, role, **kwargs)}"}


# Синтетичні користувачі стенда
ADMIN = ("admin-iryna", "admin")
OLENA = ("trainer-olena", "trainer")
TARAS = ("trainer-taras", "trainer")
ANNA = ("anna", "client")
BOHDAN = ("bohdan", "client")
