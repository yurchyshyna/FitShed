"""Сценарії ЛР 7 як автоматичні тести: справжні HTTP-виклики через шлюз до керованої заглушки.

Часи скорочено (таймаут 0,2 с, deadline 0,8 с, open_timeout 0,5 с), щоб тести йшли швидко;
логіка та ж, що й у python -m fitshed.gateway.checks з робочою конфігурацією.
"""
import logging

import pytest

from fitshed.gateway import checks
from fitshed.gateway.app import GatewayConfig
from fitshed.gateway.bench import running_bench
from fitshed.gateway.resilience import RetryPolicy

FAST = {
    "read_retry": RetryPolicy(per_try_timeout=0.2, deadline=0.8, max_attempts=3, base_delay=0.05, max_delay=0.2),
    "write_timeout": 0.5,
    "breaker": {"failure_threshold": 5, "open_timeout": 0.5, "half_open_max_calls": 1},
}


@pytest.mark.parametrize("title, scenario, overrides", checks.SCENARIOS, ids=[
    "normal", "hang-within-deadline", "transient-retry", "breaker", "rate-limit", "write-not-retried"])
def test_scenario(title, scenario, overrides, capsys):
    with running_bench(GatewayConfig.load(), **FAST, **overrides) as bench:
        ok = scenario(bench)
    assert ok, capsys.readouterr().out


def test_request_id_is_propagated_and_logged_without_client_data(caplog):
    caplog.set_level(logging.INFO, logger="fitshed.gateway")
    with running_bench(GatewayConfig.load(), **FAST) as bench:
        import requests
        r = requests.get(bench.url + "/api/clients/anna-secret-id/membership",
                         headers={"X-Request-ID": "req-test-42", "X-Api-Key": "super-secret-key"})
        assert r.headers["x-request-id"] == "req-test-42"
        assert "req-test-42" in bench.stub_stats()["request_ids"]
    text = caplog.text
    assert "request_id=req-test-42" in text
    assert "anna-secret-id" not in text and "super-secret-key" not in text


def test_invalid_request_id_is_replaced():
    with running_bench(GatewayConfig.load(), **FAST) as bench:
        import requests
        r = requests.get(bench.url + "/api/clients/anna/membership", headers={"X-Request-ID": "bad id; drop"})
        assert r.headers["x-request-id"] != "bad id; drop"
        assert len(r.headers["x-request-id"]) == 16
