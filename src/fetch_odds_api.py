"""
fetch_odds_api.py
=================
Script to fetch odds for Pinnacle and Winamax via The Odds API.
"""

import logging
import requests
import os

logger = logging.getLogger(__name__)

API_KEY = os.environ.get("ODDS_API_KEY")

BASE_URL = "https://api.the-odds-api.com/v4/sports"

# Sport configuration
SPORTS_CONFIG = {
    "football": [
        "soccer_germany_bundesliga",
        "soccer_germany_bundesliga2",
        "soccer_germany_liga3",
        "soccer_germany_dfb_pokal",
        "soccer_netherlands_eredivisie",
        "soccer_epl",
        "soccer_efl_champ",
        "soccer_spain_la_liga",
        "soccer_italy_serie_a",
        "soccer_france_ligue_one",
        "soccer_uefa_champs_league",
        "soccer_uefa_champs_league_qualification",
        "soccer_usa_mls",
    ],
    "basketball": [
        "basketball_nba",
        "basketball_euroleague",
    ],
    "tennis": [
        "tennis_atp_wimbledon",
        "tennis_wta_wimbledon",
        "tennis_atp_us_open",
        "tennis_wta_us_open",
    ],
}


def get_odds_from_api(
    sport_key: str,
    regions: str = "eu",
    markets: str = "h2h",
    bookmakers: str = "pinnacle,winamax_fr",
) -> list:
    """
    Fetch odds for a specific league from The Odds API.

    :param sport_key: The identifier of the sport/league.
    :param regions: The regions to fetch odds from, defaults to "eu".
    :param markets: The betting markets to fetch, defaults to "h2h".
    :param bookmakers: The bookmakers to fetch odds from, defaults to "pinnacle,winamax_fr".
    :return: A list of odds data.
    """
    url = f"{BASE_URL}/{sport_key}/odds/"
    params = {
        "apiKey": API_KEY,
        "regions": regions,
        "markets": markets,
    }
    if bookmakers:
        params["bookmakers"] = bookmakers

    logger.info(
        "Fetching odds from URL: %s with regions=%s, markets=%s, bookmakers=%s",
        url, regions, markets, bookmakers,
    )
    response = requests.get(url, params=params, timeout=20)

    # Output header to check remaining requests
    remaining_requests = response.headers.get("x-requests-remaining")
    used_requests = response.headers.get("x-requests-used")
    if remaining_requests:
        logger.info(
            f"API Limits - Used: {used_requests} | Remaining: {remaining_requests}"
        )

    response.raise_for_status()
    return response.json()


def fetch_odds_for_sport(sport_name: str) -> dict:
    """
    Fetch odds for all configured leagues of a sport and return successes and errors.

    :param sport_name: The name of the sport to fetch data for.
    :raises ValueError: If the sport name is invalid.
    :return: A dictionary containing 'data' (list of odds) and 'errors' (list of failures).
    """
    all_data = []
    errors = []
    regions = "eu,uk,us,au"

    league_keys = SPORTS_CONFIG.get(sport_name.lower())
    if not league_keys:
        raise ValueError(f"Invalid sport. Allowed: {list(SPORTS_CONFIG.keys())}")

    for league in league_keys:
        try:
            logger.info(f"Loading odds for {league}...")
            data = get_odds_from_api(sport_key=league, regions=regions, markets="h2h", bookmakers=None)
            all_data.extend(data)
        except Exception as e:
            logger.error(f"Error loading {league}: {e}")
            errors.append({"league": league, "error": str(e)})

    return {"data": all_data, "errors": errors}

