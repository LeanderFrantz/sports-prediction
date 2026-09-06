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


def test_find_positive_ev_bets_prefers_preferred_bookmaker_on_tie():
    # Same outcome at the same price at two books collapses to one bet; the
    # preferred book should be the one reported, whatever the API order.
    match = _match(
        "A",
        "B",
        books=[("betrivers", "BetRivers", 2.10), ("tipico_de", "Tipico", 2.10)],
    )

    bets = find_positive_ev_bets([match])

    assert [b["bookmaker_key"] for b in bets] == ["tipico_de"]


def test_find_positive_ev_bets_falls_back_to_first_when_no_preferred_book():
    match = _match(
        "A",
        "B",
        books=[("betrivers", "BetRivers", 2.10), ("betway", "Betway", 2.10)],
    )

    bets = find_positive_ev_bets([match])

    assert [b["bookmaker_key"] for b in bets] == ["betrivers"]


def test_find_positive_ev_bets_skips_matches_already_started():
    started = _match("A", "B", "2020-01-01T12:00:00Z", [("betrivers", "BR", 2.10)])
    upcoming = _match("C", "D", "2099-01-01T12:00:00Z", [("betrivers", "BR", 2.10)])
    undated = _match("E", "F", None, [("betrivers", "BR", 2.10)])

    bets = find_positive_ev_bets([started, upcoming, undated])

    # The started match is dropped; the undated one is kept, since we can't
    # show it has started.
    assert [b["match"] for b in bets] == ["C - D", "E - F"]

    # ...and opting out brings the started match back.
    all_bets = find_positive_ev_bets([started, upcoming, undated], skip_started=False)
    assert [b["match"] for b in all_bets] == ["A - B", "C - D", "E - F"]


def test_preferred_bookmakers_argument_overrides_the_default():
    match = _match(
        "A",
        "B",
        books=[("betrivers", "BetRivers", 2.10), ("tipico_de", "Tipico", 2.10)],
    )

    bets = find_positive_ev_bets([match], preferred_bookmakers=["betrivers"])

    assert [b["bookmaker_key"] for b in bets] == ["betrivers"]


def test_preferred_bookmakers_respects_list_order():
    match = _match(
        "A",
        "B",
        books=[("winamax_de", "Winamax", 2.10), ("tipico_de", "Tipico", 2.10)],
    )

    first = find_positive_ev_bets(
        [match], preferred_bookmakers=["tipico_de", "winamax_de"]
    )
    second = find_positive_ev_bets(
        [match], preferred_bookmakers=["winamax_de", "tipico_de"]
    )

    assert [b["bookmaker_key"] for b in first] == ["tipico_de"]
    assert [b["bookmaker_key"] for b in second] == ["winamax_de"]


def test_unknown_preferred_bookmaker_key_logs_a_warning(caplog):
    # A typo silently does nothing otherwise -- these are API keys, not titles.
    match = _match("A", "B", books=[("betrivers", "BetRivers", 2.10)])

    with caplog.at_level("WARNING"):
        find_positive_ev_bets([match], preferred_bookmakers=["Tipico"])

    assert "Tipico" in caplog.text
    assert "did not appear in any match" in caplog.text


def test_no_warning_when_preferred_bookmaker_is_present(caplog):
    match = _match("A", "B", books=[("tipico_de", "Tipico", 2.10)])

    with caplog.at_level("WARNING"):
        find_positive_ev_bets([match], preferred_bookmakers=["tipico_de"])

    assert "did not appear" not in caplog.text


def test_competitor_named_like_the_draw_is_not_treated_as_a_draw():
    # A 2-outcome market whose competitor merely contains "draw" used to be
    # read as 3-way, duplicating that competitor into the odds vector.
    match = {
        "home_team": "Drawbridge FC",
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
                            {"name": "Drawbridge FC", "price": 1.9},
                            {"name": "B", "price": 1.9},
                        ],
                    }
                ],
            },
            {
                "key": "betrivers",
                "title": "BetRivers",
                "markets": [
                    {
                        "key": "h2h",
                        "outcomes": [
                            {"name": "Drawbridge FC", "price": 2.10},
                            {"name": "B", "price": 1.80},
                        ],
                    }
                ],
            },
        ],
    }

    bets = find_positive_ev_bets([match])

    assert [b["type"] for b in bets] == ["Home (1)"]
    assert all(b["outcome"] != "Draw" for b in bets)


def test_real_draw_label_is_still_detected():
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
                            {"name": "A", "price": 2.5},
                            {"name": "Draw", "price": 3.4},
                            {"name": "B", "price": 3.0},
                        ],
                    }
                ],
            },
            {
                "key": "betrivers",
                "title": "BetRivers",
                "markets": [
                    {
                        "key": "h2h",
                        "outcomes": [
                            {"name": "A", "price": 2.5},
                            {"name": "Draw", "price": 4.0},
                            {"name": "B", "price": 3.0},
                        ],
                    }
                ],
            },
        ],
    }

    bets = find_positive_ev_bets([match])

    assert [b["outcome"] for b in bets] == ["Draw"]


def test_negative_ev_threshold_admits_negative_ev_bets():
    # A hardcoded ev > 0 gate used to make a negative threshold return nothing,
    # which is exactly the setting you want for calibration.
    # Fair odds are 2.0, so 1.95 on the home side is -2.5% EV.
    match = _match("A", "B", books=[("betrivers", "BetRivers", 1.95)])

    assert find_positive_ev_bets([match]) == []

    bets = find_positive_ev_bets([match], ev_threshold=-5.0)

    assert len(bets) == 1
    assert bets[0]["ev_percent"] == pytest.approx(-2.5, abs=0.01)


def test_display_timezone_defaults_to_berlin():
    match = _match("A", "B", "2026-08-28T16:30:00Z", [("betrivers", "BR", 2.10)])

    bets = find_positive_ev_bets([match], skip_started=False)

    assert bets[0]["kickoff"] == "Fri, 28 Aug 18:30"


def test_display_timezone_can_be_overridden():
    match = _match("A", "B", "2026-08-28T16:30:00Z", [("betrivers", "BR", 2.10)])

    bets = find_positive_ev_bets(
        [match], skip_started=False, display_timezone="America/New_York"
    )

    assert bets[0]["kickoff"] == "Fri, 28 Aug 12:30"


def test_unknown_display_timezone_falls_back_to_utc():
    match = _match("A", "B", "2026-08-28T16:30:00Z", [("betrivers", "BR", 2.10)])

    bets = find_positive_ev_bets(
        [match], skip_started=False, display_timezone="Not/AZone"
    )

    assert bets[0]["kickoff"] == "Fri, 28 Aug 16:30"
