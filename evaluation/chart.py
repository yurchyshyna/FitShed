"""Графік інтегральних оцінок J для двох наборів ваг (потрібен matplotlib).

    pip install matplotlib
    python evaluation/chart.py
"""
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

from mcda import evaluate, load  # noqa: E402

OUT = Path(__file__).parents[1] / "docs" / "lab4" / "scores.png"


def main() -> None:
    rows, config = load()
    ev = evaluate(rows, config)
    ids = [r["id"] for r in rows]
    sets = list(ev["results"])
    colors = {"W1": "#1f7a72", "W2": "#c9822b"}
    width = 0.36

    fig, ax = plt.subplots(figsize=(8, 4.2), dpi=150)
    for i, key in enumerate(sets):
        xs = [k + (i - 0.5) * width for k in range(len(ids))]
        values = [ev["results"][key]["scores"][a] for a in ids]
        bars = ax.bar(xs, values, width, label=f"{key}: {ev['results'][key]['title']}", color=colors.get(key))
        for bar, value in zip(bars, values):
            ax.text(bar.get_x() + bar.get_width() / 2, value + 0.015, f"{value:.3f}",
                    ha="center", va="bottom", fontsize=9)

    short = {"A": "A. Одна VM\n(Docker Compose)", "B": "B. VM +\nкерована PostgreSQL", "C": "C. Kubernetes +\nкерована PostgreSQL"}
    ax.set_xticks(range(len(ids)), [short.get(a, a) for a in ids], fontsize=9)
    ax.set_ylabel("J (менше — краще)")
    ax.set_ylim(0, 1)
    ax.spines[["top", "right"]].set_visible(False)
    ax.legend(frameon=False, fontsize=8, loc="upper left")
    ax.set_title("Інтегральні оцінки альтернатив розміщення FitShed", fontsize=11)
    fig.tight_layout()
    fig.savefig(OUT)
    print(f"Збережено {OUT}")


if __name__ == "__main__":
    main()
