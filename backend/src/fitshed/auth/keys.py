"""Генерує навчальну пару ключів для підпису access token.

Запуск з backend/src:
    python -m fitshed.auth.keys            # у backend/var/keys (папка в .gitignore)
    python -m fitshed.auth.keys --force    # перегенерувати (старі токени стануть невалідними)
"""
from __future__ import annotations

import argparse
import sys

from .tokens import AuthConfig, generate_key_pair


def main() -> int:
    parser = argparse.ArgumentParser(description="Генерація навчальних ключів JWT")
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()
    sys.stdout.reconfigure(encoding="utf-8")
    config = AuthConfig.from_env()
    if config.private_key_path.exists() and not args.force:
        print(f"Ключі вже є: {config.private_key_path.parent} (для заміни додайте --force)")
        return 0
    config.private_key_path.parent.mkdir(parents=True, exist_ok=True)
    private_pem, public_pem = generate_key_pair()
    config.private_key_path.write_bytes(private_pem)
    config.public_key_path.write_bytes(public_pem)
    print(f"Створено:\n  {config.private_key_path}  (закритий, лише для емітента)\n  {config.public_key_path}  (відкритий, для API)")
    print("Папка backend/var/ не повинна потрапляти в Git.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
