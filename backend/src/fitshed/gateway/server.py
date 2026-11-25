"""Запуск шлюзу FitShed.

З папки backend/src:
    python -m fitshed.gateway.server                         # http://127.0.0.1:8080, config/gateway.json
    python -m fitshed.gateway.server --unprotected           # профіль «до захисту» (для порівняння)
    python -m fitshed.gateway.server --rate-capacity 1000    # перевизначити ліміт запитів
"""
from __future__ import annotations

import argparse
import logging
import sys

import uvicorn

from .app import DEFAULT_CONFIG, GatewayConfig, create_gateway


def main() -> None:
    parser = argparse.ArgumentParser(description="API Gateway FitShed")
    parser.add_argument("--port", type=int, default=8080)
    parser.add_argument("--config", default=str(DEFAULT_CONFIG))
    parser.add_argument("--unprotected", action="store_true", help="без повторів, breaker і з довгим таймаутом")
    parser.add_argument("--rate-capacity", type=int)
    args = parser.parse_args()

    sys.stdout.reconfigure(encoding="utf-8")
    logging.basicConfig(level=logging.INFO, stream=sys.stdout, format="%(asctime)s %(levelname)-7s %(name)s %(message)s")
    config = GatewayConfig.load(args.config)
    if args.unprotected:
        config = GatewayConfig.unprotected(config)
    if args.rate_capacity:
        config.rate_capacity = args.rate_capacity
    app, _ = create_gateway(config)
    print(f"FitShed Gateway: http://127.0.0.1:{args.port}  (Ctrl+C — зупинити)")
    uvicorn.run(app, host="127.0.0.1", port=args.port, log_level="warning")


if __name__ == "__main__":
    main()
