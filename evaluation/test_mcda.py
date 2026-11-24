"""Перевірка формул багатометрикового оцінювання (ЛР 4)."""
import pytest

from mcda import best, evaluate, integral_scores, load, normalized_losses


def test_minimized_criterion_best_value_has_zero_loss():
    losses = normalized_losses({"A": 25, "B": 55, "C": 140}, "min")
    assert losses["A"] == 0.0 and losses["C"] == 1.0
    assert losses["B"] == pytest.approx(30 / 115)


def test_maximized_criterion_best_value_has_zero_loss():
    losses = normalized_losses({"A": 150, "B": 300, "C": 2000}, "max")
    assert losses["C"] == 0.0 and losses["A"] == 1.0
    assert losses["B"] == pytest.approx(1700 / 1850)


def test_equal_values_give_zero_loss_for_everyone():
    assert normalized_losses({"A": 3, "B": 3, "C": 3}, "min") == {"A": 0.0, "B": 0.0, "C": 0.0}


def test_unknown_direction_is_rejected():
    with pytest.raises(ValueError):
        normalized_losses({"A": 1, "B": 2}, "best")


@pytest.mark.parametrize("weights", [{"x": 0.5, "y": 0.6}, {"x": 1.2, "y": -0.2}])
def test_invalid_weights_are_rejected(weights):
    losses = {"x": {"A": 0.0, "B": 1.0}, "y": {"A": 1.0, "B": 0.0}}
    with pytest.raises(ValueError):
        integral_scores(losses, weights)


def test_ties_return_all_best_alternatives():
    assert best({"A": 0.4, "B": 0.4, "C": 0.9}) == ["A", "B"]


def test_scores_are_between_zero_and_one():
    rows, config = load()
    ev = evaluate(rows, config)
    for res in ev["results"].values():
        assert all(0.0 <= j <= 1.0 for j in res["scores"].values())


def test_changing_candidate_set_changes_normalization():
    """Додавання дорожчої альтернативи змінює нормовані втрати решти."""
    without = normalized_losses({"A": 25, "B": 55}, "min")
    with_c = normalized_losses({"A": 25, "B": 55, "C": 140}, "min")
    assert without["B"] == 1.0
    assert with_c["B"] < without["B"]
