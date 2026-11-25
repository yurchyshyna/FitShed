"""Локальний сервер FitShed Booking API (FastAPI + uvicorn).

Запуск з папки backend/src:
    python -m fitshed.api.server              # http://127.0.0.1:8000
    python -m fitshed.api.server --port 8080
Документація, яку генерує FastAPI: http://127.0.0.1:8000/docs

Сховище — адаптер у пам'яті з ЛР 2, тому після зупинки дані зникають.
Сервер працює з одним процесом (workers = 1): кілька процесів мали б окремі сховища в пам'яті.
"""
from __future__ import annotations

import argparse
from collections.abc import Callable
from datetime import datetime

import uvicorn

from fitshed.booking.adapters.in_memory import InMemoryGroupClassRepository
from fitshed.booking.application.booking_service import BookingService

from .app import create_app


def build_service(clock: Callable[[], datetime] = datetime.now, repo=None) -> BookingService:
    return BookingService(repo or InMemoryGroupClassRepository(), clock)


def main() -> None:
    parser = argparse.ArgumentParser(description="Локальний сервер FitShed Booking API")
    parser.add_argument("--port", type=int, default=8000)
    args = parser.parse_args()
    print(f"FitShed Booking API: http://127.0.0.1:{args.port}  (документація: /docs, Ctrl+C — зупинити)")
    uvicorn.run(create_app(build_service()), host="127.0.0.1", port=args.port, workers=1)


if __name__ == "__main__":
    main()
