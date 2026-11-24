"""Контрактна перевірка на боці клієнта (споживача).

Для кожної взаємодії з файлу очікувань піднімається тестова заглушка, яка
повертає ЛИШЕ поля з очікувань. Тест перевіряє, що:
  1) клієнт надсилає саме той запит, що записаний в очікуваннях;
  2) клієнт коректно розбирає відповідь і не потребує полів поза контрактом.
Справжній провайдер тут не запускається.
"""
import json
from http.server import BaseHTTPRequestHandler

import pytest

from contract_support import example_body, interactions, running
from fitshed.api.client import ApiError, FitShedClient


def stub_for(interaction: dict, recorded: list):
    status = interaction["response"]["status"]
    body = example_body(interaction["response"]["body"])

    class Stub(BaseHTTPRequestHandler):
        def _reply(self) -> None:
            length = int(self.headers.get("Content-Length") or 0)
            raw = self.rfile.read(length)
            recorded.append({"method": self.command, "path": self.path,
                             "body": json.loads(raw) if raw else None})
            payload = json.dumps(body, ensure_ascii=False).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json" if status < 400 else "application/problem+json")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        do_GET = do_POST = _reply

        def log_message(self, *args) -> None:
            pass

    return Stub


@pytest.mark.parametrize("interaction", interactions(), ids=lambda i: i["id"])
def test_client_meets_its_expectations(interaction):
    recorded: list = []
    call = interaction["client_call"]
    with running(stub_for(interaction, recorded)) as url:
        client = FitShedClient(url)
        method = getattr(client, call["method"])
        if interaction["response"]["status"] >= 400:
            with pytest.raises(ApiError) as error:
                method(**call["args"])
            assert error.value.status == interaction["response"]["status"]
            assert error.value.code == interaction["response"]["body"]["code"]["equals"]
            result = None
        else:
            result = method(**call["args"])

    expected_request = interaction["request"]
    assert recorded[0]["method"] == expected_request["method"]
    assert recorded[0]["path"] == expected_request["path"]
    assert recorded[0]["body"] == expected_request.get("body")

    # Клієнт має правильно перенести значення з відповіді у свої об'єкти
    if result is not None:
        sample = example_body(interaction["response"]["body"])
        for field, value in sample.items():
            if hasattr(result, field):
                assert getattr(result, field) == value, field
