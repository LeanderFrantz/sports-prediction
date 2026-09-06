import pytest

from src.fetch_odds_api import DEFAULT_REGIONS, _resolve_odds_filters


@pytest.fixture(autouse=True)
def _clear_env(monkeypatch):
    monkeypatch.delenv("ODDS_REGIONS", raising=False)
    monkeypatch.delenv("ODDS_BOOKMAKERS", raising=False)


def test_defaults_to_the_configured_regions():
    assert _resolve_odds_filters() == (DEFAULT_REGIONS, None)


def test_regions_can_be_narrowed_via_env():
    import os

    os.environ["ODDS_REGIONS"] = "eu,uk"
    assert _resolve_odds_filters() == ("eu,uk", None)


def test_explicit_bookmakers_replace_regions():
    # Naming bookmakers is billed as a single region, so regions must not also
    # be sent -- the API would ignore them and the intent would be unclear.
    regions, bookmakers = _resolve_odds_filters(bookmakers="pinnacle,tipico_de")

    assert regions is None
    assert bookmakers == "pinnacle,tipico_de"


def test_pinnacle_is_forced_into_an_explicit_bookmaker_list():
    # Without Pinnacle there are no fair odds and nothing can be priced.
    _, bookmakers = _resolve_odds_filters(bookmakers="tipico_de,winamax_de")

    assert bookmakers.split(",")[0] == "pinnacle"
    assert "tipico_de" in bookmakers
