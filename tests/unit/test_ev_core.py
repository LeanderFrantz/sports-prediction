import pytest
from src.strategies.ev_core import solve_logarithmic_pure


def test_solve_logarithmic_pure_fair_odds():
    # 50/50 odds (+ bookie markup)
    odds = [1.9, 1.9]
    true_odds, true_probs = solve_logarithmic_pure(odds)

    # Fair odds should be 2.0, probs should be 0.5
    assert true_odds[0] == pytest.approx(2.0, abs=0.1)
    assert true_odds[1] == pytest.approx(2.0, abs=0.1)
    assert true_probs[0] == pytest.approx(0.5, abs=0.1)
    assert true_probs[1] == pytest.approx(0.5, abs=0.1)


def test_solve_logarithmic_pure_3_outcomes():
    # Example 3-outcome odds (e.g., Football)
    odds = [2.0, 3.0, 4.0]
    _, true_probs = solve_logarithmic_pure(odds)

    # Probs should sum to 1.0
    assert sum(true_probs) == pytest.approx(1.0, abs=1e-5)
