"""API Gateway FitShed (ЛР 7) на Starlette.

Відповідальність шлюзу — лише спільні політики:
  маршрутизація, request_id, rate limiting, таймаути, повтори безпечних читань,
  circuit breaker і однаковий формат помилок шлюзу.
Бізнес-правила (місця, записи, переходи стану) лишаються в Booking API (ЛР 5) і доменному ядрі.

Маршрути:
  /api/classes[/...]                         → Booking API (ЛР 5)          /classes[/...]
  GET  /api/clients/{id}/membership          → сервіс абонементів (заглушка) /memberships/{id}
  POST /api/clients/{id}/membership/debits   → сервіс абонементів            /memberships/{id}/debits
  GET  /gateway/health | /gateway/state, POST /gateway/reset — службові
"""
from __future__ import annotations

import hashlib
import json
import logging
import re
import time
import uuid
from dataclasses import dataclass
from pathlib import Path

import requests
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.routing import Route

from .resilience import CircuitBreaker, RetryPolicy, TokenBucketLimiter, TransientError, run_with_retry

log = logging.getLogger("fitshed.gateway")
DEFAULT_CONFIG = Path(__file__).resolve().parents[3] / "config" / "gateway.json"
REQUEST_ID = re.compile(r"^[A-Za-z0-9._-]{1,64}$")
PASS_HEADERS = ("content-type", "location", "retry-after")


@dataclass
class GatewayConfig:
    booking_api: str
    membership_service: str
    read_retry: RetryPolicy
    write_timeout: float
    breaker: dict
    rate_capacity: int
    rate_refill: float
    key_header: str
    breaker_enabled: bool = True

    @classmethod
    def load(cls, path: Path = DEFAULT_CONFIG, **overrides) -> "GatewayConfig":
        raw = json.loads(Path(path).read_text(encoding="utf-8"))
        r = raw["read_retry"]
        config = cls(
            booking_api=raw["upstreams"]["booking_api"],
            membership_service=raw["upstreams"]["membership_service"],
            read_retry=RetryPolicy(r["per_try_timeout"], r["deadline"], r["max_attempts"], r["base_delay"],
                                   r["max_delay"], frozenset(r["retryable_statuses"])),
            write_timeout=raw["write_timeout"],
            breaker=raw["breaker"],
            rate_capacity=raw["rate_limit"]["capacity"],
            rate_refill=raw["rate_limit"]["refill_per_second"],
            key_header=raw["rate_limit"]["key_header"],
        )
        for key, value in overrides.items():
            setattr(config, key, value)
        return config

    @classmethod
    def unprotected(cls, base: "GatewayConfig") -> "GatewayConfig":
        """Профіль «до захисту» для порівняння: одна спроба, довге очікування, без breaker."""
        return cls(base.booking_api, base.membership_service,
                   RetryPolicy(per_try_timeout=10.0, deadline=10.0, max_attempts=1),
                   10.0, base.breaker, 10_000, 10_000.0, base.key_header, breaker_enabled=False)


@dataclass
class UpstreamResponse:
    status: int
    body: bytes
    headers: dict[str, str]


def problem(status: int, code: str, detail: str, request_id: str, headers: dict | None = None) -> JSONResponse:
    h = {"X-Request-ID": request_id, **(headers or {})}
    return JSONResponse({"status": status, "code": code, "detail": detail, "request_id": request_id},
                        status_code=status, headers=h, media_type="application/problem+json")


class Gateway:
    def __init__(self, config: GatewayConfig) -> None:
        self.config = config
        b = config.breaker
        self.breakers = {name: CircuitBreaker(name, b["failure_threshold"], b["open_timeout"], b["half_open_max_calls"])
                         for name in ("booking_api", "membership_service")}
        self.limiter = TokenBucketLimiter(config.rate_capacity, config.rate_refill)

    # ---------- спільне ----------
    @staticmethod
    def request_id(request: Request) -> str:
        incoming = request.headers.get("x-request-id", "")
        return incoming if REQUEST_ID.match(incoming) else uuid.uuid4().hex[:16]

    def rate_limited(self, request: Request, request_id: str) -> Response | None:
        raw_key = request.headers.get(self.config.key_header) or (request.client.host if request.client else "unknown")
        allowed, retry_after = self.limiter.try_acquire(raw_key)
        if allowed:
            return None
        # У журнал — лише хеш ключа: сам ключ може бути секретом або персональним даним
        key_hash = hashlib.sha256(raw_key.encode()).hexdigest()[:8]
        log.warning("request_id=%s rate_limited key_hash=%s retry_after=%.2fs", request_id, key_hash, retry_after)
        return problem(429, "rate_limited", "Забагато запитів, повторіть пізніше", request_id,
                       {"Retry-After": str(max(1, round(retry_after + 0.5)))})

    @staticmethod
    def _call(method: str, url: str, body: bytes, request_id: str, timeout: float) -> UpstreamResponse:
        try:
            r = requests.request(method, url, data=body or None, timeout=(timeout, timeout),
                                 headers={"X-Request-ID": request_id, "Content-Type": "application/json"})
        except requests.Timeout:
            raise TransientError("timeout") from None
        except requests.ConnectionError:
            raise TransientError("connection_error") from None
        return UpstreamResponse(r.status_code, r.content, {k.lower(): v for k, v in r.headers.items()})

    def forward(self, request_id: str, route: str, upstream: str, method: str, url: str,
                body: bytes, safe_to_retry: bool) -> Response:
        """Виклик залежності з політиками. Для небезпечних операцій — одна спроба."""
        breaker = self.breakers[upstream]
        policy = self.config.read_retry if safe_to_retry else RetryPolicy(
            per_try_timeout=self.config.write_timeout, deadline=self.config.write_timeout, max_attempts=1)
        started = time.monotonic()

        def classify(resp: UpstreamResponse) -> str:
            if resp.status in policy.retryable_statuses:
                return "retryable"
            return "ok" if resp.status < 400 else "final"

        def before_attempt() -> bool:
            return breaker.allow() if self.config.breaker_enabled else True

        def on_attempt(a) -> None:
            status = a.result.status if a.result is not None else "-"
            failed = a.outcome in ("timeout", "connection_error", "retryable") or (
                a.result is not None and a.result.status >= 500)
            if self.config.breaker_enabled:
                breaker.record_failure() if failed else breaker.record_success()
            log.info("request_id=%s route=%r upstream=%s attempt=%d outcome=%s status=%s elapsed_ms=%d",
                     request_id, route, upstream, a.number, a.outcome, status, a.elapsed * 1000)

        result, attempts, reason = run_with_retry(
            lambda timeout: self._call(method, url, body, request_id, timeout), classify, policy,
            retry_allowed=safe_to_retry, before_attempt=before_attempt, on_attempt=on_attempt)

        elapsed_ms = (time.monotonic() - started) * 1000
        calls = len(attempts)
        log.info("request_id=%s route=%r result=%s upstream_calls=%d elapsed_ms=%d breaker=%s",
                 request_id, route, reason, calls, elapsed_ms, breaker.state.value)
        extra = {"X-Upstream-Attempts": str(calls)}

        if reason == "breaker_open" and calls == 0:
            return problem(503, "circuit_open", f"Залежність {upstream} тимчасово недоступна, виклик не виконувався",
                           request_id, {**extra, "Retry-After": str(max(1, round(breaker.retry_after() + 0.5)))})
        if result is not None and reason in ("ok", "final", "exhausted", "not_retryable", "deadline", "breaker_open"):
            headers = {k: v for k, v in result.headers.items() if k in PASS_HEADERS}
            return Response(result.body, status_code=result.status, headers={**headers, **extra, "X-Request-ID": request_id})
        last = attempts[-1].outcome if attempts else "none"
        if last == "connection_error":
            return problem(502, "upstream_unreachable", f"Залежність {upstream} не відповідає", request_id, extra)
        detail = "Залежність не відповіла вчасно"
        if not safe_to_retry:
            detail += "; операцію не повторено, вона могла виконатися — перевірте стан перед повтором"
        return problem(504, "upstream_timeout", detail, request_id, extra)

    # ---------- маршрути ----------
    def membership(self, request: Request) -> Response:
        rid = self.request_id(request)
        if limited := self.rate_limited(request, rid):
            return limited
        cid = request.path_params["client_id"]
        return self.forward(rid, "GET /api/clients/{id}/membership", "membership_service", "GET",
                            f"{self.config.membership_service}/memberships/{cid}", b"", safe_to_retry=True)

    def debit(self, request: Request) -> Response:
        rid = self.request_id(request)
        if limited := self.rate_limited(request, rid):
            return limited
        cid = request.path_params["client_id"]
        body = self._read_body(request)
        return self.forward(rid, "POST /api/clients/{id}/membership/debits", "membership_service", "POST",
                            f"{self.config.membership_service}/memberships/{cid}/debits", body, safe_to_retry=False)

    def classes(self, request: Request) -> Response:
        rid = self.request_id(request)
        if limited := self.rate_limited(request, rid):
            return limited
        rest = request.path_params.get("rest", "")
        path = "/classes" + (f"/{rest}" if rest else "")
        return self.forward(rid, f"{request.method} /api/classes/...", "booking_api", request.method,
                            f"{self.config.booking_api}{path}", self._read_body(request),
                            safe_to_retry=request.method == "GET")

    @staticmethod
    def _read_body(request: Request) -> bytes:
        # Starlette читає тіло асинхронно; у синхронному обробнику беремо його з кешу, заповненого middleware
        return request.scope.get("fitshed.body", b"")

    def state(self, _: Request) -> JSONResponse:
        return JSONResponse({name: {"state": b.state.value, "transitions": b.transitions}
                             for name, b in self.breakers.items()})

    def reset(self, _: Request) -> JSONResponse:
        for b in self.breakers.values():
            b.reset()
        self.limiter.reset()
        log.info("gateway reset")
        return JSONResponse({"reset": True})


class BodyCache:
    """ASGI-middleware: заздалегідь читає тіло запиту, щоб синхронні обробники мали до нього доступ."""

    def __init__(self, app) -> None:
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        chunks, more = [], True
        while more:
            message = await receive()
            chunks.append(message.get("body", b""))
            more = message.get("more_body", False)
        scope["fitshed.body"] = b"".join(chunks)

        async def replay():
            return {"type": "http.request", "body": scope["fitshed.body"], "more_body": False}
        await self.app(scope, replay, send)


def create_gateway(config: GatewayConfig | None = None) -> tuple[Starlette, Gateway]:
    gw = Gateway(config or GatewayConfig.load())
    routes = [
        Route("/gateway/health", lambda r: JSONResponse({"status": "ok"})),
        Route("/gateway/state", gw.state),
        Route("/gateway/reset", gw.reset, methods=["POST"]),
        Route("/api/clients/{client_id}/membership", gw.membership, methods=["GET"]),
        Route("/api/clients/{client_id}/membership/debits", gw.debit, methods=["POST"]),
        Route("/api/classes", gw.classes, methods=["GET", "POST"]),
        Route("/api/classes/{rest:path}", gw.classes, methods=["GET", "POST"]),
    ]
    app = Starlette(routes=routes)
    app.add_middleware(BodyCache)
    return app, gw
