"""HTTP API на FastAPI — транспортний адаптер доменного ядра «Розклад і запис».

Адаптер лише приймає HTTP-запит, перевіряє його структуру (схеми Pydantic),
викликає BookingService і перекладає доменні помилки в коди HTTP.
Бізнес-правила залишаються в агрегаті GroupClass.

Контракт задано в backend/openapi.yaml (design-first). FastAPI додатково генерує
власний опис на /openapi.json; тест test_routes_match_openapi_contract стежить,
щоб маршрути застосунку й контракту не розійшлися.
"""
from __future__ import annotations

import uuid
from collections.abc import Callable

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from fitshed.booking.application.booking_service import BookingService
from fitshed.booking.domain import (
    ClientAddedToWaitlist,
    ForbiddenTransitionError,
    GroupClassNotFoundError,
    InvalidValueError,
    InvariantViolationError,
)

from .schemas import Cancellation, NewBooking, NewClass, class_to_json, describe_validation_errors

PROBLEM_JSON = "application/problem+json"
TITLES = {400: "Некоректні вхідні дані", 404: "Ресурс не знайдено", 405: "Метод не дозволено",
          409: "Конфлікт стану"}


def problem(status: int, code: str, detail: str, title: str | None = None,
            headers: dict[str, str] | None = None) -> JSONResponse:
    """Відповідь з помилкою у форматі RFC 9457 (схема Problem в openapi.yaml)."""
    return JSONResponse(
        status_code=status,
        content={"status": status, "code": code, "title": title or TITLES.get(status, "Помилка"), "detail": detail},
        media_type=PROBLEM_JSON,
        headers=headers,
    )


# Описи помилок для автоматично згенерованої документації FastAPI (/docs)
_ERR = {400: {"description": "Некоректні вхідні дані"}, 404: {"description": "Заняття не знайдено"},
        409: {"description": "Конфлікт стану або порушене правило"}}


def create_app(service: BookingService,
               id_factory: Callable[[], str] = lambda: f"cls-{uuid.uuid4().hex[:8]}") -> FastAPI:
    app = FastAPI(title="FitShed Booking API", version="1.0.0",
                  description="Ресурс «групове заняття». Контракт: backend/openapi.yaml")

    # ---------- переклад помилок у коди HTTP ----------
    @app.exception_handler(RequestValidationError)
    async def invalid_request(_: Request, exc: RequestValidationError) -> JSONResponse:
        return problem(400, "invalid_input", describe_validation_errors(list(exc.errors())))

    @app.exception_handler(InvalidValueError)
    async def invalid_value(_: Request, exc: InvalidValueError) -> JSONResponse:
        return problem(400, "invalid_input", str(exc))

    @app.exception_handler(GroupClassNotFoundError)
    async def not_found(_: Request, exc: GroupClassNotFoundError) -> JSONResponse:
        return problem(404, "not_found", str(exc))

    @app.exception_handler(ForbiddenTransitionError)
    async def forbidden_transition(_: Request, exc: ForbiddenTransitionError) -> JSONResponse:
        return problem(409, "invalid_state", str(exc))

    @app.exception_handler(InvariantViolationError)
    async def rule_violation(_: Request, exc: InvariantViolationError) -> JSONResponse:
        return problem(409, "rule_violation", str(exc), title="Порушено бізнес-правило")

    @app.exception_handler(StarletteHTTPException)
    async def http_error(request: Request, exc: StarletteHTTPException) -> JSONResponse:
        # Невідомий шлях (404) і непідтримуваний метод (405, з заголовком Allow)
        if exc.status_code == 404:
            return problem(404, "not_found", f"Шлях {request.url.path} не існує")
        if exc.status_code == 405:
            return problem(405, "invalid_input", "Метод не підтримується для цього шляху", headers=exc.headers)
        return problem(exc.status_code, "invalid_input", str(exc.detail), headers=exc.headers)

    # ---------- операції ----------
    # Звичайні (не async) функції: FastAPI виконує їх у пулі потоків.

    @app.post("/classes", status_code=201, operation_id="createClass", responses={400: _ERR[400]})
    def create_class(body: NewClass) -> JSONResponse:
        class_id = id_factory()
        # Допустимість часу й кількості місць перевіряє доменне ядро (TimeSlot, Capacity)
        service.schedule_class(class_id, body.title, body.trainer_id, body.start, body.end, body.capacity)
        return JSONResponse(status_code=201, content=class_to_json(service.get_class(class_id)),
                            headers={"Location": f"/classes/{class_id}"})

    @app.get("/classes/{class_id}", operation_id="getClass", responses={404: _ERR[404]})
    def get_class(class_id: str) -> JSONResponse:
        return JSONResponse(class_to_json(service.get_class(class_id)))

    @app.post("/classes/{class_id}/bookings", status_code=201, operation_id="bookClass",
              responses={400: _ERR[400], 404: _ERR[404], 409: _ERR[409]})
    def book(class_id: str, body: NewBooking) -> JSONResponse:
        events = service.book(class_id, body.client_id)
        waitlisted = next((e for e in events if isinstance(e, ClientAddedToWaitlist)), None)
        return JSONResponse(status_code=201, content={
            "class_id": class_id,
            "client_id": body.client_id,
            "result": "waitlisted" if waitlisted else "booked",
            "waitlist_position": waitlisted.position if waitlisted else None,
        })

    @app.post("/classes/{class_id}/cancellation", operation_id="cancelClass",
              responses={400: _ERR[400], 404: _ERR[404], 409: _ERR[409]})
    def cancel(class_id: str, body: Cancellation) -> JSONResponse:
        service.cancel_class(class_id, body.reason)
        return JSONResponse(class_to_json(service.get_class(class_id)))

    return app
