"""Локальний навчальний емітент: видає access token синтетичному користувачу.

Запуск з backend/src (спершу python -m fitshed.auth.keys):
    python -m fitshed.auth.issue --sub trainer-olena --role trainer
    python -m fitshed.auth.issue --sub anna --role client --ttl 60

Синтетичні користувачі стенда:
    admin-iryna (admin), trainer-olena і trainer-taras (trainer),
    anna, bohdan, vira, halyna, dmytro (client)
"""
from __future__ import annotations

import argparse
import sys

from .tokens import ROLES, AuthConfig, LocalIssuer


def main() -> int:
    parser = argparse.ArgumentParser(description="Навчальний емітент access token")
    parser.add_argument("--sub", required=True)
    parser.add_argument("--role", required=True, choices=sorted(ROLES))
    parser.add_argument("--ttl", type=int, default=900, help="строк дії в секундах")
    args = parser.parse_args()
    print(LocalIssuer(AuthConfig.from_env()).issue(args.sub, args.role, args.ttl))
    return 0


if __name__ == "__main__":
    sys.exit(main())
