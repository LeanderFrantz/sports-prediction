"""
winamax_scraper.py
==================
Scraper for Winamax odds without a headless browser.

Strategy: Winamax embeds all data (matches, bets, odds, results)
directly into the HTML as `window.PRELOADED_STATE` JSON. A simple HTTP-GET
to the sports page provides everything — no Playwright or Selenium needed.

URL Scheme:
  All sports:       https://www.winamax.fr/paris-sportifs/sports
  Soccer only:      https://www.winamax.fr/paris-sportifs/sports/1
  A competition:    https://www.winamax.fr/paris-sportifs/sports/1/competitions/96

Odds format: Integers on a "cent basis" (600 = 6.00, 160 = 1.60)
"""

import json
import logging
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional, Any

import requests

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
BASE_URL = "https://www.winamax.fr/paris-sportifs/sports"

SPORT_IDS = {
    "football": 1,
    "basketball": 2,
    "baseball": 3,
    "hockey": 4,
    "tennis": 5,
    "handball": 6,
    "rugby": 12,
}

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "fr-FR,fr;q=0.9,en;q=0.8",
    "Referer": "https://www.winamax.fr/",
}


# ---------------------------------------------------------------------------
# Data Classes
# ---------------------------------------------------------------------------
@dataclass
class Outcome:
    outcome_id: int
    bet_id: int
    label: str
    available: bool
    odds: Optional[float] = None  # as decimal (e.g. 6.00)


@dataclass
class Bet:
    bet_id: int
    label: str
    outcomes: list[Outcome] = field(default_factory=list)


@dataclass
class Match:
    match_id: int
    title: str
    competitor1: str
    competitor2: str
    sport_id: int
    tournament_id: int
    category_id: int
    match_start: Optional[datetime]
    status: str
    main_bet: Optional[Bet] = None
    more_bets_count: int = 0
    is_outright: bool = False


# ---------------------------------------------------------------------------
# Core Scraper Functions
# ---------------------------------------------------------------------------

def _parse_winamax_odds(raw: Any) -> float:
    """
    Parse the odds value.

    :param raw: The raw odds value from JSON.
    :return: The odds as a float.
    """
    return float(raw)


def _extract_preloaded_state(html: str) -> dict:
    """
    Extract the PRELOADED_STATE JSON object from HTML.

    :param html: The raw HTML content.
    :return: The parsed state dictionary.
    :raises ValueError: If PRELOADED_STATE is not found.
    """
    marker = "var PRELOADED_STATE = "
    start_idx = html.find(marker)
    if start_idx < 0:
        raise ValueError("PRELOADED_STATE not found in HTML.")
    start_idx += len(marker)

    payload = html[start_idx:].lstrip()
    state, _ = json.JSONDecoder().raw_decode(payload)
    return state


def fetch_winamax_page(url: str, session: requests.Session) -> dict:
    """
    Load a Winamax sports page and return the parsed PRELOADED_STATE.

    :param url: The URL to fetch.
    :param session: The requests session to use.
    :return: The parsed state dictionary.
    :raises requests.HTTPError: If the request fails.
    """
    resp = session.get(url, headers=HEADERS, timeout=20)
    resp.raise_for_status()
    return _extract_preloaded_state(resp.text)


def parse_matches(state: dict) -> list[Match]:
    """
    Convert PRELOADED_STATE into a list of Match objects.

    :param state: The parsed state dictionary.
    :return: A list of Match objects, including main 1X2 bets.
    """
    raw_matches: dict = state.get("matches", {})
    raw_bets: dict = state.get("bets", {})
    raw_outcomes: dict = state.get("outcomes", {})
    raw_odds: dict = state.get("odds", {})

    matches = []
    for mid_str, m in raw_matches.items():
        # Parse match start
        match_start = None
        ts = m.get("matchStart")
        if ts:
            try:
                match_start = datetime.fromtimestamp(ts)
            except (TypeError, ValueError):
                pass

        # Assemble main bet (1X2)
        main_bet = None
        main_bet_id = m.get("mainBetId")
        if main_bet_id and str(main_bet_id) in raw_bets:
            bet_data = raw_bets[str(main_bet_id)]
            outcome_ids = bet_data.get("outcomes", [])
            outcomes = []
            for oid in outcome_ids:
                oid_str = str(oid)
                if oid_str in raw_outcomes:
                    od = raw_outcomes[oid_str]
                    raw_odd = raw_odds.get(oid_str)
                    outcomes.append(
                        Outcome(
                            outcome_id=oid,
                            bet_id=main_bet_id,
                            label=od.get("label", ""),
                            available=od.get("available", False),
                            odds=_parse_winamax_odds(raw_odd) if raw_odd else None,
                        )
                    )
            main_bet = Bet(
                bet_id=main_bet_id,
                label=bet_data.get("betName", ""),
                outcomes=outcomes,
            )

        matches.append(
            Match(
                match_id=int(mid_str),
                title=m.get("title", ""),
                competitor1=m.get("competitor1Name", ""),
                competitor2=m.get("competitor2Name", ""),
                sport_id=m.get("sportId", -1),
                tournament_id=m.get("tournamentId", -1),
                category_id=m.get("categoryId", -1),
                match_start=match_start,
                status=m.get("status", ""),
                main_bet=main_bet,
                more_bets_count=m.get("moreBets", 0),
                is_outright=m.get("isOutright", False),
            )
        )

    return matches


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def get_football_matches(competition_id: Optional[int] = None) -> list[Match]:
    """
    Load all soccer matches from Winamax.

    :param competition_id: Optional - restricts to a specific competition.
    :return: A list of Match objects with odds.
    """
    if competition_id:
        url = f"{BASE_URL}/{SPORT_IDS['football']}/competitions/{competition_id}"
    else:
        url = f"{BASE_URL}/{SPORT_IDS['football']}"

    with requests.Session() as session:
        logger.info("Loading Winamax page: %s", url)
        state = fetch_winamax_page(url, session)
        matches = parse_matches(state)
        logger.info("Found: %d matches", len(matches))
        return matches


def get_all_sports_overview() -> dict[str, dict[str, Any]]:
    """
    Get an overview of all available sports and their match counts.

    :return: A dictionary mapping sport names to match count data.
    """
    url = f"{BASE_URL}"
    with requests.Session() as session:
        state = fetch_winamax_page(url, session)

    sports = state.get("sports", {})
    return {
        v.get("sportName", sid): {
            "sport_id": sid,
            "main_matches": v.get("mainMatchCount", 0),
            "live_matches": v.get("liveMatchCount", 0),
        }
        for sid, v in sports.items()
    }


# ---------------------------------------------------------------------------
# CLI / Demo
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")

    print("=" * 70)
    print("WINAMAX SCRAPER - Demo")
    print("=" * 70)

    # 1) Sports Overview
    print("\n Sports Overview:")
    try:
        overview = get_all_sports_overview()
        for name, info in list(overview.items())[:10]:
            print(
                f"  {name:20s} | Main: {info['main_matches']:4d} | Live: {info['live_matches']:3d}"
            )
    except Exception as e:
        print(f"  Error: {e}")

    # 2) Soccer Matches with odds
    print("\n Soccer Matches (first 10):")
    try:
        matches = get_football_matches()
        print(f"  Total: {len(matches)} matches\n")
        for m in matches[:10]:
            start_str = (
                m.match_start.strftime("%d.%m %H:%M") if m.match_start else "?"
            )
            odds_str = "-"
            if m.main_bet and m.main_bet.outcomes:
                odds_vals = [
                    f"{o.label}: {o.odds:.2f}" if o.odds else f"{o.label}: n/a"
                    for o in m.main_bet.outcomes
                ]
                odds_str = " | ".join(odds_vals)
            print(f"  [{start_str}] {m.title}")
            print(f"           Odds: {odds_str}")
            print(f"           Additional bets: {m.more_bets_count}")
            print()
    except Exception as e:
        print(f"  Error: {e}")
        raise
