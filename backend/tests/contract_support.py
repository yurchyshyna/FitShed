"""Спільні засоби для контрактних перевірок (ЛР 5): читання файлу очікувань,
порівняння відповіді з правилами, запуск тестової заглушки (http.server)
та справжнього застосунку FastAPI (uvicorn) у фоновому потоці."""
from __future__ import annotations

import json
import threading
import time
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

EXPECTATIONS = Path(__file__).parents[1] / "contracts" / "booking-client.expectations.json"

JSON_TYPES = {"string": str, "integer": int, "array": list, "object": dict, "null": type(None)}


def load_expectations() -> dict:
    return json.loads(EXPECTATIONS.read_text(encoding="utf-8"))


def interactions() -> list[dict]:
    return load_expectations()["interactions"]


def check_body(body: dict, rules: dict) -> list[str]:
    """Повертає список розбіжностей між відповіддю й очікуваннями клієнта."""
    problems = []
    for field, rule in rules.items():
        if field not in body:
            problems.append(f"немає поля «{field}»")
            continue
        value = body[field]
        if "equals" in rule and value != rule["equals"]:
            problems.append(f"«{field}» = {value!r}, очікувалося {rule['equals']!r}")
        if "type" in rule:
            types = rule["type"] if isinstance(rule["type"], list) else [rule["type"]]
            ok = any(isinstance(value, JSON_TYPES[t]) and not (t == "integer" and isinstance(value, bool))
                     for t in types)
            if not ok:
                problems.append(f"«{field}» має тип {type(value).__name__}, очікувався {'/'.join(types)}")
    return problems


def example_body(rules: dict) -> dict:
    """Тіло тестової відповіді для перевірки клієнта: значення з equals або example."""
    return {field: rule.get("equals", rule.get("example")) for field, rule in rules.items()}


@contextmanager
def running(handler_class: type[BaseHTTPRequestHandler]):
    """Запускає сервер на вільному порту й повертає його базову адресу."""
    server = HTTPServer(("127.0.0.1", 0), handler_class)
    thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_address[1]}"
    finally:
        server.shutdown()
        server.server_close()


@contextmanager
def running_app(app):
    """Запускає ASGI-застосунок (FastAPI) справжнім сервером uvicorn на вільному порту."""
    import uvicorn

    config = uvicorn.Config(app, host="127.0.0.1", port=0, log_level="warning", lifespan="off")
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    deadline = time.monotonic() + 10
    while not server.started:
        if not thread.is_alive() or time.monotonic() > deadline:
            raise RuntimeError("uvicorn не запустився")
        time.sleep(0.01)
    port = server.servers[0].sockets[0].getsockname()[1]
    try:
        yield f"http://127.0.0.1:{port}"
    finally:
        server.should_exit = True
        thread.join(timeout=10)
