"""Локальний HTTP-сервер FitShed на стандартній бібліотеці Python.

Запуск з папки backend/src:
    python -m fitshed.api.server            # http://127.0.0.1:8000
    python -m fitshed.api.server --port 8080

Сервер однопотоковий (HTTPServer, а не ThreadingHTTPServer): запити обробляються
по черзі. Сховище — адаптер у пам'яті з ЛР 2, тому після зупинки дані зникають.
"""
from __future__ import annotations

import argparse
import json
from collections.abc import Callable
from datetime import datetime
from http.server import BaseHTTPRequestHandler, HTTPServer

from fitshed.booking.adapters.in_memory import InMemoryGroupClassRepository
from fitshed.booking.application.booking_service import BookingService

from .http_api import BookingHttpApi


def build_api(clock: Callable[[], datetime] = datetime.now, repo=None) -> BookingHttpApi:
    return BookingHttpApi(BookingService(repo or InMemoryGroupClassRepository(), clock))


def make_handler(api: BookingHttpApi, quiet: bool = False) -> type[BaseHTTPRequestHandler]:
    class Handler(BaseHTTPRequestHandler):
        def _dispatch(self) -> None:
            length = int(self.headers.get("Content-Length") or 0)
            response = api.handle(self.command, self.path, self.rfile.read(length))
            payload = b"" if response.body is None else json.dumps(response.body, ensure_ascii=False).encode()
            self.send_response(response.status)
            self.send_header("Content-Type", f"{response.content_type}; charset=utf-8")
            self.send_header("Content-Length", str(len(payload)))
            for name, value in response.headers.items():
                self.send_header(name, value)
            self.end_headers()
            self.wfile.write(payload)

        do_GET = do_POST = do_PUT = do_PATCH = do_DELETE = _dispatch

        def log_message(self, fmt: str, *args) -> None:
            if not quiet:
                super().log_message(fmt, *args)

    return Handler


def make_server(api: BookingHttpApi, host: str = "127.0.0.1", port: int = 8000, quiet: bool = False) -> HTTPServer:
    return HTTPServer((host, port), make_handler(api, quiet))


def main() -> None:
    parser = argparse.ArgumentParser(description="Локальний сервер FitShed Booking API")
    parser.add_argument("--port", type=int, default=8000)
    args = parser.parse_args()
    server = make_server(build_api(), port=args.port)
    print(f"FitShed Booking API: http://127.0.0.1:{args.port}  (Ctrl+C — зупинити)")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
