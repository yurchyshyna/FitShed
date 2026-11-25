"""Керована HTTP-заглушка залежності: «сервіс абонементів» (ЛР 7).

Не є реальною інфраструктурою: поведінку задає перевірка через керувальні ендпоінти.

Бізнес-ендпоінти (їх рахує лічильник викликів):
  GET  /memberships/{client_id}           → 200 {client_id, visits_left, valid_until}
  POST /memberships/{client_id}/debits    → 201 {client_id, debited: 1}   (операція зміни стану)

Керування (не рахуються):
  POST /__control  {"mode": ..., ...}     → задати поведінку
  GET  /__stats                           → кількість викликів, request_id, кількість списань
  POST /__reset                           → лічильники в нуль, режим normal

Режими:
  normal                                   — відповідь одразу
  fail_next   {"count": N, "status": 503}  — наступні N викликів повертають помилку
  delay_next  {"count": N, "delay": 2.0}   — наступні N викликів «зависають» на delay секунд
  outage      {"status": 503}              — усі виклики повертають помилку
  hang        {"delay": 5.0}               — усі виклики зависають
  flaky                                    — детермінований збій за номером виклику:
                                             кожен 5-й зависає на 2 с, кожен 3-й повертає 503

Запуск з backend/src:  python -m fitshed.gateway.stub --port 8001
"""
from __future__ import annotations

import argparse
import json
import logging
import re
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

log = logging.getLogger("fitshed.stub")
MEMBERSHIP = re.compile(r"^/memberships/([^/]+)$")
DEBITS = re.compile(r"^/memberships/([^/]+)/debits$")


class StubState:
    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.reset()

    def reset(self) -> None:
        self.mode, self.params = "normal", {}
        self.calls, self.debits = 0, 0
        self.request_ids: list[str] = []
        self.counter = 0          # для fail_next / delay_next

    def next_behaviour(self, request_id: str) -> tuple[int, float]:
        """Реєструє виклик і повертає (код відповіді, затримка)."""
        with self.lock:
            self.calls += 1
            n = self.calls
            self.request_ids.append(request_id)
            mode, p = self.mode, self.params
            if mode in ("fail_next", "delay_next") and self.counter > 0:
                self.counter -= 1
                return (p.get("status", 503), 0.0) if mode == "fail_next" else (200, p.get("delay", 2.0))
            if mode == "outage":
                return p.get("status", 503), 0.0
            if mode == "hang":
                return 200, p.get("delay", 5.0)
            if mode == "flaky":
                if n % 5 == 0:
                    return 200, 2.0
                if n % 3 == 0:
                    return 503, 0.0
            return 200, 0.0


def make_handler(state: StubState):
    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def _send(self, status: int, body: dict) -> None:
            payload = json.dumps(body, ensure_ascii=False).encode()
            try:
                self.send_response(status)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)
            except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
                pass  # клієнт (шлюз) уже не чекає: його таймаут спрацював раніше

        def _body(self) -> dict:
            length = int(self.headers.get("Content-Length") or 0)
            raw = self.rfile.read(length) if length else b""
            return json.loads(raw) if raw else {}

        def _business(self, kind: str, client_id: str) -> None:
            request_id = self.headers.get("X-Request-ID", "-")
            status, delay = state.next_behaviour(request_id)
            log.info("request_id=%s route=%s status=%d delay=%.1fs", request_id, kind, status, delay)
            if delay:
                time.sleep(delay)
            if status >= 400:
                return self._send(status, {"error": "temporarily_unavailable"})
            if kind == "debit":
                with state.lock:
                    state.debits += 1
                return self._send(201, {"client_id": client_id, "debited": 1})
            self._send(200, {"client_id": client_id, "visits_left": 5, "valid_until": "2027-01-31"})

        def do_GET(self) -> None:
            if self.path == "/__stats":
                with state.lock:
                    return self._send(200, {"calls": state.calls, "debits": state.debits,
                                            "mode": state.mode, "request_ids": state.request_ids[-50:]})
            if m := MEMBERSHIP.match(self.path):
                return self._business("get", m[1])
            self._send(404, {"error": "not_found"})

        def do_POST(self) -> None:
            if self.path == "/__reset":
                self._body()
                with state.lock:
                    state.reset()
                return self._send(200, {"reset": True})
            if self.path == "/__control":
                body = self._body()
                with state.lock:
                    state.mode = body.get("mode", "normal")
                    state.params = body
                    state.counter = int(body.get("count", 0))
                log.info("control mode=%s params=%s", state.mode, body)
                return self._send(200, {"mode": state.mode})
            if m := DEBITS.match(self.path):
                self._body()
                return self._business("debit", m[1])
            self._send(404, {"error": "not_found"})

        def log_message(self, fmt: str, *args) -> None:
            pass  # власний журнал вище

    return Handler


def make_stub(host: str = "127.0.0.1", port: int = 8001) -> tuple[ThreadingHTTPServer, StubState]:
    state = StubState()
    server = ThreadingHTTPServer((host, port), make_handler(state))
    server.daemon_threads = True
    return server, state


def main() -> None:
    parser = argparse.ArgumentParser(description="Керована заглушка сервісу абонементів")
    parser.add_argument("--port", type=int, default=8001)
    args = parser.parse_args()
    sys.stdout.reconfigure(encoding="utf-8")
    logging.basicConfig(level=logging.INFO, stream=sys.stdout, format="%(asctime)s %(name)s %(message)s")
    server, _ = make_stub(port=args.port)
    print(f"Заглушка сервісу абонементів: http://127.0.0.1:{args.port}  (Ctrl+C — зупинити)")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
