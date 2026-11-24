"""Багатометрикове оцінювання альтернатив розміщення FitShed (ЛР 4).

Нормовані втрати:
    критерій мінімізують:  loss = (value - min) / (max - min)
    критерій максимізують: loss = (max - value) / (max - min)
    якщо max == min:       loss = 0 для всіх альтернатив
Інтегральна оцінка: J = Σ w_k · loss_k, ваги невід'ємні, сума = 1. Менше J — краще.

Запуск з кореня репозиторію:
    python evaluation/mcda.py
    python evaluation/mcda.py --save docs/lab4/results.md
"""
from __future__ import annotations

import argparse
import csv
import json
import math
import sys
from pathlib import Path

HERE = Path(__file__).parent


def normalized_losses(values: dict[str, float], direction: str) -> dict[str, float]:
    """Нормовані втрати одного критерію для фіксованої множини альтернатив."""
    if direction not in ("min", "max"):
        raise ValueError(f"Невідомий напрям оптимізації: {direction}")
    low, high = min(values.values()), max(values.values())
    if math.isclose(high, low):
        return {alt: 0.0 for alt in values}
    if direction == "min":
        return {alt: (v - low) / (high - low) for alt, v in values.items()}
    return {alt: (high - v) / (high - low) for alt, v in values.items()}


def check_weights(weights: dict[str, float]) -> None:
    if any(w < 0 for w in weights.values()):
        raise ValueError("Ваги мають бути невід'ємними")
    if not math.isclose(sum(weights.values()), 1.0, abs_tol=1e-9):
        raise ValueError(f"Сума ваг має дорівнювати 1, а не {sum(weights.values())}")


def integral_scores(losses: dict[str, dict[str, float]], weights: dict[str, float]) -> dict[str, float]:
    """losses[критерій][альтернатива] → J[альтернатива]."""
    check_weights(weights)
    alternatives = next(iter(losses.values())).keys()
    return {alt: sum(weights[c] * losses[c][alt] for c in weights) for alt in alternatives}


def best(scores: dict[str, float]) -> list[str]:
    """Усі альтернативи з мінімальною оцінкою (за рівних J повертаються всі)."""
    minimum = min(scores.values())
    return [alt for alt, j in scores.items() if math.isclose(j, minimum, abs_tol=1e-9)]


def load(csv_path: Path = HERE / "alternatives.csv", criteria_path: Path = HERE / "criteria.json"):
    with open(csv_path, encoding="utf-8") as file:
        rows = list(csv.DictReader(file))
    config = json.loads(criteria_path.read_text(encoding="utf-8"))
    return rows, config


def evaluate(rows: list[dict], config: dict) -> dict:
    criteria = config["criteria"]
    raw = {c: {r["id"]: float(r[c]) for r in rows} for c in criteria}
    losses = {c: normalized_losses(raw[c], criteria[c]["direction"]) for c in criteria}
    results = {}
    for key, ws in config["weight_sets"].items():
        scores = integral_scores(losses, ws["weights"])
        results[key] = {"title": ws["title"], "weights": ws["weights"], "scores": scores, "best": best(scores)}
    return {"raw": raw, "losses": losses, "results": results}


def fmt(x: float) -> str:
    return f"{x:.3f}"


def report(rows: list[dict], config: dict, ev: dict) -> str:
    criteria = config["criteria"]
    names = {r["id"]: r["name"] for r in rows}
    ids = [r["id"] for r in rows]
    out = []

    out.append("## Вихідні показники\n")
    out.append("| Альтернатива | " + " | ".join(f"{criteria[c]['name']}, {criteria[c]['unit']}" for c in criteria) + " |")
    out.append("|---|" + "---|" * len(criteria))
    for a in ids:
        out.append(f"| {a}. {names[a]} | " + " | ".join(f"{ev['raw'][c][a]:g}" for c in criteria) + " |")
    out.append("| **Напрям** | " + " | ".join(criteria[c]["direction"] for c in criteria) + " |")
    out.append("| **Походження** | " + " | ".join(criteria[c]["origin"] for c in criteria) + " |")

    out.append("\n## Нормовані втрати\n")
    out.append("| Альтернатива | " + " | ".join(criteria[c]["name"] for c in criteria) + " |")
    out.append("|---|" + "---|" * len(criteria))
    for a in ids:
        out.append(f"| {a} | " + " | ".join(fmt(ev["losses"][c][a]) for c in criteria) + " |")

    for key, res in ev["results"].items():
        out.append(f"\n## Інтегральна оцінка J, набір ваг {key}: {res['title']}\n")
        out.append("Ваги: " + ", ".join(f"{criteria[c]['name']} = {w:.2f}" for c, w in res["weights"].items()) + "\n")
        out.append("| Альтернатива | Розрахунок | J | Місце |")
        out.append("|---|---|---|---|")
        ranking = sorted(ids, key=lambda a: res["scores"][a])
        for a in ids:
            terms = " + ".join(f"{res['weights'][c]:.2f}·{fmt(ev['losses'][c][a])}" for c in res["weights"])
            out.append(f"| {a} | {terms} | **{fmt(res['scores'][a])}** | {ranking.index(a) + 1} |")
        winners = res["best"]
        if len(winners) == 1:
            out.append(f"\nНайкраща альтернатива: **{winners[0]}** ({names[winners[0]]}).")
        else:
            out.append(f"\nОднакова мінімальна оцінка в альтернатив: **{', '.join(winners)}**.")

    out.append(sensitivity_to_weights(ev, config))
    out.append(sensitivity_to_candidates(rows, config, exclude="C"))
    return "\n".join(out) + "\n"


def sensitivity_to_weights(ev: dict, config: dict, base: str = "W1", source: str = "cost",
                           target: str = "rto", steps=(0.0, 0.05, 0.10, 0.15, 0.20)) -> str:
    """Поступово переносимо вагу з одного критерію на інший і дивимося, чи зміниться вибір."""
    criteria = config["criteria"]
    out = [f"\n## Чутливість до ваг: перенесення ваги з «{criteria[source]['name']}» "
           f"на «{criteria[target]['name']}» у наборі {base}\n",
           f"| w({source}) | w({target}) | " + " | ".join(f"J({a})" for a in ev["raw"][source]) + " | Вибір |",
           "|---|---|" + "---|" * len(ev["raw"][source]) + "---|"]
    for d in steps:
        w = dict(config["weight_sets"][base]["weights"])
        w[source] = round(w[source] - d, 10)
        w[target] = round(w[target] + d, 10)
        scores = integral_scores(ev["losses"], w)
        out.append(f"| {w[source]:.2f} | {w[target]:.2f} | " + " | ".join(fmt(j) for j in scores.values())
                   + f" | {', '.join(best(scores))} |")
    return "\n".join(out)


def sensitivity_to_candidates(rows: list[dict], config: dict, exclude: str) -> str:
    """Перераховуємо оцінки без однієї альтернативи: min і max нормування змінюються."""
    reduced = evaluate([r for r in rows if r["id"] != exclude], config)
    out = [f"\n## Чутливість до множини кандидатів: без альтернативи {exclude}\n",
           "| Набір ваг | " + " | ".join(f"J({r['id']})" for r in rows if r["id"] != exclude) + " | Вибір |",
           "|---|" + "---|" * (len(rows) - 1) + "---|"]
    for key, res in reduced["results"].items():
        out.append(f"| {key} | " + " | ".join(fmt(j) for j in res["scores"].values())
                   + f" | {', '.join(res['best'])} |")
    return "\n".join(out)


def main() -> int:
    parser = argparse.ArgumentParser(description="Багатометрикове оцінювання альтернатив FitShed")
    parser.add_argument("--save", metavar="ФАЙЛ", help="зберегти звіт у Markdown (UTF-8)")
    args = parser.parse_args()

    rows, config = load()
    text = report(rows, config, evaluate(rows, config))
    sys.stdout.reconfigure(encoding="utf-8")
    print(text)
    if args.save:
        Path(args.save).write_text(text, encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
