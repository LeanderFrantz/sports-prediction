import pytest
from src.strategies.ev_core import find_positive_ev_bets, solve_logarithmic_pure


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


def test_solve_logarithmic_pure_zero_margin_is_accepted():
    # A perfectly fair book (implied probs sum to exactly 1.0) needs no
    # de-vigging and must pass through unchanged.
    true_odds, true_probs = solve_logarithmic_pure([2.0, 2.0])

    assert sum(true_probs) == pytest.approx(1.0, abs=1e-9)
    assert true_odds == pytest.approx([2.0, 2.0], abs=1e-9)


def test_solve_logarithmic_pure_rejects_negative_margin():
    # Implied probs sum to 0.909 — the model can only remove vig (k >= 1), so
    # there is no k that normalises this. It used to return the raw
    # probabilities unchanged, inflating every downstream EV.
    with pytest.raises(ValueError, match="sum to >= 1.0"):
        solve_logarithmic_pure([2.2, 2.2])


def test_solve_logarithmic_pure_rejects_invalid_odds():
    with pytest.raises(ValueError):
        solve_logarithmic_pure([])
    with pytest.raises(ValueError):
        solve_logarithmic_pure([1.0, 2.0])
    with pytest.raises(ValueError):
        solve_logarithmic_pure([float("inf"), 2.0])


def test_find_positive_ev_bets_skips_match_with_unsolvable_pinnacle_line():
    match = {
        "home_team": "A",
        "away_team": "B",
        "sport_title": "Test League",
        "bookmakers": [
            {
                "key": "pinnacle",
                "title": "Pinnacle",
                "markets": [
                    {
                        "key": "h2h",
                        "outcomes": [
                            {"name": "A", "price": 2.2},
                            {"name": "B", "price": 2.2},
                        ],
                    }
                ],
            },
            {
                "key": "bk1",
                "title": "Book1",
                "markets": [
                    {
                        "key": "h2h",
                        "outcomes": [
                            {"name": "A", "price": 2.0},
                            {"name": "B", "price": 2.0},
                        ],
                    }
                ],
            },
        ],
    }

    assert find_positive_ev_bets([match]) == []


def test_find_positive_ev_bets_skips_malformed_outcomes_without_crashing():
    # An outcome missing "price" (and one with a junk price) used to raise
    # KeyError/ValueError out of the whole scan, losing every other match.
    broken = {
        "home_team": "A",
        "away_team": "B",
        "sport_title": "Test League",
        "bookmakers": [
            {
                "key": "pinnacle",
                "title": "Pinnacle",
                "markets": [
                    {
                        "key": "h2h",
                        "outcomes": [{"name": "A"}, {"name": "B", "price": "n/a"}],
                    }
                ],
            }
        ],
    }
    healthy = {
        "home_team": "C",
        "away_team": "D",
        "sport_title": "Test League",
        "bookmakers": [
            {
                "key": "pinnacle",
                "title": "Pinnacle",
                "markets": [
                    {
                        "key": "h2h",
                        "outcomes": [
                            {"name": "C", "price": 1.9},
                            {"name": "D", "price": 1.9},
                        ],
                    }
                ],
            },
            {
                "key": "bk1",
                "title": "Book1",
                "markets": [
                    {
                        "key": "h2h",
                        "outcomes": [
                            {"name": "C", "price": 2.1},
                            {"name": "D", "price": 1.8},
                        ],
                    }
                ],
            },
        ],
    }

    bets = find_positive_ev_bets([broken, healthy])

    # The malformed match is skipped; the healthy one still comes through.
    assert [b["match"] for b in bets] == ["C - D"]


def _match(home, away, commence_time=None, books=()):
    """Build a match with a Pinnacle line plus the given (key, title, home_price)."""
    bookmakers = [
        {
            "key": "pinnacle",
            "title": "Pinnacle",
            "markets": [
                {
                    "key": "h2h",
                    "outcomes": [
                        {"name": home, "price": 1.9},
                        {"name": away, "price": 1.9},
                    ],
                }
            ],
        }
    ]
    for key, title, price in books:
        bookmakers.append(
            {
                "key": key,
                "title": title,
                "markets": [
                    {
                        "key": "h2h",
                        "outcomes": [
                            {"name": home, "price": price},
                            {"name": away, "price": 1.8},
                        ],
                    }
                ],
            }
        )
    match = {
        "home_team": home,
        "away_team": away,
        "sport_title": "Test League",
        "bookmakers": bookmakers,
    }
    if commence_time:
        match["commence_time"] = commence_time
    return match


def test_find_positive_ev_bets_excludes_commission_based_exchanges():
    # Exchange odds are pre-commission, so they show a phantom edge against
    # Pinnacle's fair odds.
    match = _match(
        "A",
        "B",
        books=[
            ("smarkets", "Smarkets", 2.10),
            ("matchbook", "Matchbook", 2.11),
            ("betfair_ex_uk", "Betfair", 2.12),
            ("betrivers", "BetRivers", 2.05),
        ],
    )

    bets = find_positive_ev_bets([match])

    assert [b["bookmaker_key"] for b in bets] == ["betrivers"]
