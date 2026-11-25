"""Стенд для перевірок ЛР 7: керована заглушка + шлюз, обидва в одному процесі на вільних портах.

Використовується скриптами checks.py і compare.py та автоматичними тестами.
Лічильники викликів беруться із самої заглушки (/__stats), час — зі стенда (time.perf_counter).
"""
from __future__ import annotations

import threading
import time
from contextlib import contextmanager
from dataclasses import dataclass

import requests
import uvicorn

from .app import GatewayConfig, create_gateway
from .stub import make_stub


@dataclass
class Measured:
    status: int
    ms: float
    upstream_calls: int          # фактичні виклики залежності, за лічильником заглушки
    code: str = ""               # код помилки шлюзу (circuit_open, rate_limited, ...)
    retry_after: str = ""


@contextmanager
def serve_asgi(app):
    config = uvicorn.Config(app, host="127.0.0.1", port=0, log_level="warning", lifespan="off")
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    deadline = time.monotonic() + 10
    while not server.started:
        if not thread.is_alive() or time.monotonic() > deadline:
            raise RuntimeError("шлюз не запустився")
        time.sleep(0.01)
    try:
        yield f"http://127.0.0.1:{server.servers[0].sockets[0].getsockname()[1]}"
    finally:
        server.should_exit = True
        thread.join(timeout=10)


class Bench:
    def __init__(self, stub_url: str, gateway_url: str, gateway, config: GatewayConfig) -> None:
        self.stub_url, self.url, self.gateway, self.config = stub_url, gateway_url, gateway, config

    # ---------- керування заглушкою ----------
    def stub(self, **control) -> None:
        requests.post(f"{self.stub_url}/__control", json=control, timeout=5)

    def stub_stats(self) -> dict:
        return requests.get(f"{self.stub_url}/__stats", timeout=5).json()

    def stub_calls(self) -> int:
        return self.stub_stats()["calls"]

    def reset(self) -> None:
        requests.post(f"{self.stub_url}/__reset", timeout=5)
        requests.post(f"{self.url}/gateway/reset", timeout=5)

    def breaker(self, name: str = "membership_service") -> dict:
        return requests.get(f"{self.url}/gateway/state", timeout=5).json()[name]

    # ---------- запити через шлюз ----------
    def request(self, method: str = "GET", path: str = "/api/clients/anna/membership",
                api_key: str = "client-a", json_body: dict | None = None) -> Measured:
        before = self.stub_calls()
        t0 = time.perf_counter()
        r = requests.request(method, self.url + path, json=json_body, timeout=30,
                             headers={"X-Api-Key": api_key})
        ms = (time.perf_counter() - t0) * 1000
        code = ""
        if r.headers.get("content-type", "").startswith("application/problem+json"):
            code = r.json().get("code", "")
        return Measured(r.status_code, ms, self.stub_calls() - before, code, r.headers.get("retry-after", ""))


@contextmanager
def running_bench(config: GatewayConfig | None = None, **overrides):
    """Піднімає заглушку й шлюз. overrides змінюють поля GatewayConfig (наприклад, rate_capacity)."""
    stub_server, _ = make_stub(port=0)
    stub_url = f"http://127.0.0.1:{stub_server.server_address[1]}"
    threading.Thread(target=stub_server.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True).start()
    config = config or GatewayConfig.load()
    config.membership_service = stub_url
    for key, value in overrides.items():
        setattr(config, key, value)
    app, gateway = create_gateway(config)
    try:
        with serve_asgi(app) as url:
            yield Bench(stub_url, url, gateway, config)
    finally:
        stub_server.shutdown()
        stub_server.server_close()
