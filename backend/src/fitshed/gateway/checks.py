"""П'ять сценаріїв ЛР 7 і окрема перевірка операції зміни стану.

Для кожного сценарію фіксуються код відповіді, тривалість і кількість фактичних викликів залежності.

Запуск з backend/src:
    python -m fitshed.gateway.checks
    python -m fitshed.gateway.checks --save ../../docs/lab7/checks.txt
"""
from __future__ import annotations

import argparse
import io
import logging
import sys
import time
from contextlib import redirect_stdout
from pathlib import Path

from .app import GatewayConfig
from .bench import Bench, Measured, running_bench


def row(label: str, m: Measured) -> str:
    extra = f" code={m.code}" if m.code else ""
    extra += f" Retry-After={m.retry_after}" if m.retry_after else ""
    return f"  {label:<44} HTTP {m.status}  {m.ms:7.0f} мс  викликів залежності: {m.upstream_calls}{extra}"


def verdict(checks: list[tuple[str, bool]]) -> bool:
    for text, ok in checks:
        print(f"  [{'OK' if ok else 'FAIL'}] {text}")
    return all(ok for _, ok in checks)


def scenario_1_normal(b: Bench) -> bool:
    m = b.request()
    print(row("звичайний запит", m))
    return verdict([("200, один виклик залежності", m.status == 200 and m.upstream_calls == 1)])


def scenario_2_hang(b: Bench) -> bool:
    p = b.config.read_retry
    b.stub(mode="delay_next", count=1, delay=p.deadline * 2)
    a = b.request()
    print(row("перша спроба зависла, повтор успішний", a))
    b.stub(mode="hang", delay=p.deadline * 3)
    h = b.request()
    print(row("залежність зависла повністю", h))
    b.stub(mode="normal")
    return verdict([
        ("зависла спроба обірвана таймаутом, повтор дав 200 у межах deadline",
         a.status == 200 and a.upstream_calls == 2 and a.ms < p.deadline * 1000),
        (f"повне зависання: 504 не пізніше deadline ({p.deadline:.1f} с), а не через {p.deadline * 3:.0f} с",
         h.status == 504 and h.ms < p.deadline * 1000 + 300),
        ("кількість спроб обмежена", 1 <= h.upstream_calls <= p.max_attempts),
    ])


def scenario_3_transient(b: Bench) -> bool:
    b.stub(mode="fail_next", count=2, status=503)
    m = b.request()
    print(row("дві тимчасові помилки 503, потім успіх", m))
    b.stub(mode="fail_next", count=1, status=500)
    n = b.request()
    print(row("помилка 500 (не тимчасова)", n))
    b.stub(mode="normal")
    return verdict([("503 повторено: результат 200 після 3 викликів", m.status == 200 and m.upstream_calls == 3),
                    ("500 не повторюється: 1 виклик", n.status == 500 and n.upstream_calls == 1)])


def scenario_4_breaker(b: Bench) -> bool:
    threshold, open_timeout = b.config.breaker["failure_threshold"], b.config.breaker["open_timeout"]
    b.stub(mode="outage", status=503)
    print(f"  залежність недоступна (outage 503); поріг відкриття {threshold} відмов, open_timeout {open_timeout} с")
    results = []
    while b.breaker()["state"] != "open" and len(results) < 10:
        results.append(b.request())
        print(row(f"запит {len(results)} (breaker {b.breaker()['state']})", results[-1]))
    calls_when_opened = b.stub_calls()
    rejected = [b.request() for _ in range(5)]
    for i, m in enumerate(rejected, 1):
        print(row(f"breaker open, запит {i}", m))
    calls_after_rejected = b.stub_calls()

    b.stub(mode="normal")
    print(f"  залежність відновлено; очікування {open_timeout} с до half-open")
    time.sleep(open_timeout + 0.1)
    probe = b.request()
    print(row("проба в half-open", probe))
    after = b.request()
    print(row("після закриття breaker", after))
    transitions = b.breaker()["transitions"]
    print(f"  переходи стану: {transitions}")
    return verdict([
        ("breaker відкрився після серії відмов", b.breaker()["state"] == "closed" and ["closed", "open"] in transitions),
        ("у стані open відповідь 503 circuit_open без звернення до залежності",
         all(m.status == 503 and m.code == "circuit_open" and m.upstream_calls == 0 for m in rejected)
         and calls_after_rejected == calls_when_opened),
        ("відмова в стані open швидка (< 50 мс)", max(m.ms for m in rejected) < 50),
        ("одна проба в half-open, після неї closed",
         probe.status == 200 and probe.upstream_calls == 1 and ["open", "half_open"] in transitions
         and ["half_open", "closed"] in transitions),
        ("після відновлення запити проходять", after.status == 200),
    ])


def scenario_5_rate_limit(b: Bench) -> bool:
    capacity = b.config.rate_capacity
    burst = [b.request(api_key="client-burst") for _ in range(capacity + 3)]
    for i, m in enumerate(burst, 1):
        if i <= 2 or i > capacity - 1:
            print(row(f"запит {i} з {len(burst)} (ключ client-burst)", m))
    other = b.request(api_key="client-other")
    print(row("інший клієнт у той самий момент", other))
    limited = [m for m in burst if m.status == 429]
    return verdict([
        (f"перші {capacity} запитів пройшли", all(m.status == 200 for m in burst[:capacity])),
        ("понад ліміт — 429 з Retry-After", len(limited) == 3 and all(m.code == "rate_limited" and m.retry_after for m in limited)),
        ("відхилені запити не дійшли до залежності", all(m.upstream_calls == 0 for m in limited)),
        ("ліміт рахується окремо для кожного клієнта", other.status == 200),
    ])


def scenario_write(b: Bench) -> bool:
    path = "/api/clients/anna/membership/debits"
    b.stub(mode="fail_next", count=1, status=503)
    f = b.request("POST", path, json_body={"visits": 1})
    print(row("списання, залежність відповіла 503", f))
    debits_before = b.stub_stats()["debits"]
    b.stub(mode="delay_next", count=1, delay=b.config.write_timeout + 1)
    t = b.request("POST", path, json_body={"visits": 1})
    print(row("списання, залежність не відповіла вчасно", t))
    time.sleep(1.5)  # даємо заглушці завершити «зависле» списання
    debits_after = b.stub_stats()["debits"]
    print(f"  списань у залежності до: {debits_before}, після очікування: {debits_after}")
    b.stub(mode="normal")
    return verdict([
        ("503 при списанні не повторено: 1 виклик", f.status == 503 and f.upstream_calls == 1),
        ("таймаут при списанні не повторено: 504, 1 виклик", t.status == 504 and t.upstream_calls == 1),
        ("після таймауту списання все одно відбулося: таймаут не доводить, що дії не було",
         debits_after == debits_before + 1),
    ])


SCENARIOS = [
    ("Сценарій 1. Нормальна відповідь", scenario_1_normal, {}),
    ("Сценарій 2. Зависання залежності в межах deadline", scenario_2_hang, {}),
    ("Сценарій 3. Тимчасова помилка з успішним повтором", scenario_3_transient, {}),
    ("Сценарій 4. Відкриття breaker і перехід через half-open", scenario_4_breaker, {"rate_capacity": 1000}),
    ("Сценарій 5. Перевищення ліміту запитів (429)", scenario_5_rate_limit, {"rate_capacity": 5, "rate_refill": 0.2}),
    ("Додатково. Операція зміни стану не повторюється", scenario_write, {}),
]


def run_all(config_path: str | None = None) -> bool:
    results = []
    for title, scenario, overrides in SCENARIOS:
        config = GatewayConfig.load(config_path) if config_path else GatewayConfig.load()
        print("=" * 100)
        print(title + (f"   (перевизначено: {overrides})" if overrides else ""))
        print("=" * 100)
        with running_bench(config, **overrides) as bench:
            results.append(scenario(bench))
        print()
    print(f"Підсумок: пройдено {sum(results)} з {len(results)} перевірок")
    return all(results)


def main() -> int:
    parser = argparse.ArgumentParser(description="Сценарії ЛР 7")
    parser.add_argument("--save", metavar="ФАЙЛ")
    parser.add_argument("--log", action="store_true", help="показати журнал шлюзу")
    args = parser.parse_args()
    sys.stdout.reconfigure(encoding="utf-8")
    buffer = io.StringIO()
    logging.basicConfig(level=logging.INFO if args.log else logging.ERROR, stream=buffer,
                        format="    log: %(name)s %(message)s")
    with redirect_stdout(buffer):
        ok = run_all()
    print(buffer.getvalue(), end="")
    if args.save:
        Path(args.save).write_text(buffer.getvalue(), encoding="utf-8")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
