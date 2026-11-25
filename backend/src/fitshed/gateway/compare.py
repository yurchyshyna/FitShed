"""Серії з 30 однакових запитів до й після захисту за однаково заданого збою (ЛР 7).

«До захисту» — той самий шлюз у профілі unprotected: одна спроба, очікування до 10 с, без breaker.
«Після» — конфігурація config/gateway.json (таймаут, deadline, повтори, breaker).
Ліміт запитів в обох випадках піднято, щоб він не впливав на результат.

Профілі збою заглушки:
  flaky  — детермінований: кожен 5-й виклик зависає на 2 с, кожен 3-й повертає 503;
  outage — залежність повністю недоступна (503).

Запуск з backend/src:
    python -m fitshed.gateway.compare
    python -m fitshed.gateway.compare --save ../../docs/lab7/comparison.md

Зміна одного параметра (ЛР 8): порівняти поточну політику з варіантом, де змінено один параметр:
    python -m fitshed.gateway.compare --vary max_attempts=2 --save ../../docs/lab8/param-change.md
    python -m fitshed.gateway.compare --vary per_try_timeout=0.3
"""

from __future__ import annotations

import argparse
import dataclasses
import statistics
import sys
from pathlib import Path

from .app import GatewayConfig
from .bench import running_bench

REQUESTS = 30
FAILURES = {
    "flaky": (
        "нестабільна залежність: кожен 5-й виклик зависає на 2 с, кожен 3-й повертає 503",
        {"mode": "flaky"},
    ),
    "outage": (
        "повна недоступність залежності: кожен виклик повертає 503",
        {"mode": "outage", "status": 503},
    ),
}


def series(protected: bool, failure: dict, retry_overrides: dict | None = None) -> dict:
    base = GatewayConfig.load()
    config = base if protected else GatewayConfig.unprotected(base)
    if retry_overrides:
        config.read_retry = dataclasses.replace(config.read_retry, **retry_overrides)
    with running_bench(config, rate_capacity=10_000, rate_refill=10_000.0) as bench:
        bench.stub(**failure)
        calls_before = bench.stub_calls()
        results = [bench.request() for _ in range(REQUESTS)]
        upstream_calls = bench.stub_calls() - calls_before
        transitions = bench.breaker()["transitions"] if protected else []
    times = [m.ms for m in results]
    codes: dict[int, int] = {}
    for m in results:
        codes[m.status] = codes.get(m.status, 0) + 1
    return {
        "successes": sum(m.status == 200 for m in results),
        "failures": sum(m.status != 200 for m in results),
        "codes": dict(sorted(codes.items())),
        "upstream_calls": upstream_calls,
        "median_ms": statistics.median(times),
        "max_ms": max(times),
        "total_s": sum(times) / 1000,
        "breaker_transitions": len(transitions),
    }


def report() -> str:
    lines = [
        f"# Порівняння серій із {REQUESTS} запитів до та після захисту",
        "",
        "Виміряно на локальному стенді (заглушка + шлюз в одному процесі, запити послідовні).",
        "Це результат навчального досліду, а не оцінка доступності чи продуктивності в реальній системі.",
        "",
    ]
    for key, (description, failure) in FAILURES.items():
        before, after = series(False, failure), series(True, failure)
        lines += [
            f"## Збій «{key}»: {description}",
            "",
            "| Показник | До захисту | Після захисту |",
            "|---|---|---|",
        ]
        for label, field, fmt in [
            ("Успішних відповідей (200)", "successes", "{}"),
            ("Відмов", "failures", "{}"),
            ("Коди відповідей", "codes", "{}"),
            ("Фактичних викликів залежності", "upstream_calls", "{}"),
            ("Медіана часу відповіді, мс", "median_ms", "{:.0f}"),
            ("Максимальний час відповіді, мс", "max_ms", "{:.0f}"),
            ("Сумарний час серії, с", "total_s", "{:.1f}"),
            ("Переходів стану breaker", "breaker_transitions", "{}"),
        ]:
            lines.append(
                f"| {label} | {fmt.format(before[field])} | {fmt.format(after[field])} |"
            )
        lines.append("")
    return "\n".join(lines)


def vary_report(name: str, value: float) -> str:
    """Та сама серія «після захисту» з поточним і зміненим значенням одного параметра повторів."""
    current = getattr(GatewayConfig.load().read_retry, name)
    lines = [
        f"# Зміна одного параметра: {name} = {current} → {value}",
        "",
        f"Інші параметри — з config/gateway.json. По {REQUESTS} послідовних запитів на локальному стенді.",
        "",
    ]
    for key, (description, failure) in FAILURES.items():
        a, b = series(True, failure), series(True, failure, {name: value})
        lines += [
            f"## Збій «{key}»: {description}",
            "",
            f"| Показник | {name} = {current} | {name} = {value} |",
            "|---|---|---|",
        ]
        for label, field, fmt in [
            ("Успішних відповідей (200)", "successes", "{}"),
            ("Відмов", "failures", "{}"),
            ("Коди відповідей", "codes", "{}"),
            ("Фактичних викликів залежності", "upstream_calls", "{}"),
            ("Медіана часу відповіді, мс", "median_ms", "{:.0f}"),
            ("Максимальний час відповіді, мс", "max_ms", "{:.0f}"),
            ("Сумарний час серії, с", "total_s", "{:.1f}"),
            ("Переходів стану breaker", "breaker_transitions", "{}"),
        ]:
            lines.append(
                f"| {label} | {fmt.format(a[field])} | {fmt.format(b[field])} |"
            )
        lines.append("")
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description="Порівняння серій до та після захисту")
    parser.add_argument("--save", metavar="ФАЙЛ")
    parser.add_argument(
        "--vary",
        metavar="ПАРАМЕТР=ЗНАЧЕННЯ",
        help="змінити один параметр повторів: max_attempts, per_try_timeout, deadline, base_delay",
    )
    args = parser.parse_args()
    sys.stdout.reconfigure(encoding="utf-8")
    if args.vary:
        name, _, raw = args.vary.partition("=")
        value = int(raw) if name == "max_attempts" else float(raw)
        text = vary_report(name, value)
    else:
        text = report()
    print(text)
    if args.save:
        Path(args.save).write_text(text + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
