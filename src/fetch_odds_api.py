"""
fetch_odds_api.py
=================
Skript zum Abrufen von Quoten fuer Pinnacle und Winamax via The Odds API.
"""

import logging
import requests
import json
import os

logger = logging.getLogger(__name__)

# Lade Konfiguration
config_path = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "config.json"
)
try:
    with open(config_path, "r") as f:
        config = json.load(f)
        API_KEY = config.get("api_key")
except FileNotFoundError:
    logger.error(f"Konfigurationsdatei nicht gefunden: {config_path}")
    API_KEY = None

BASE_URL = "https://api.the-odds-api.com/v4/sports"

# Sportarten-Konfiguration
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
    Ruft die Quoten für eine bestimmte Liga von The Odds API ab.
    """
    url = f"{BASE_URL}/{sport_key}/odds/"
    params = {
        "apiKey": API_KEY,
        "regions": regions,
        "markets": markets,
    }
    if bookmakers:
        params["bookmakers"] = bookmakers

    logger.info(f"Rufe Quoten ab von URL: {url} mit Params: {params}")
    response = requests.get(url, params=params, timeout=20)

    # Header zur Überprüfung verbleibender Requests ausgeben
    remaining_requests = response.headers.get("x-requests-remaining")
    used_requests = response.headers.get("x-requests-used")
    if remaining_requests:
        logger.info(
            f"API Limits - Genutzt: {used_requests} | Verbleibend: {remaining_requests}"
        )

    response.raise_for_status()
    return response.json()


def fetch_odds_for_sport(sport_name: str) -> list:
    """Holt die Quoten fuer alle konfigurierten Ligen einer Sportart."""
    all_data = []
    regions = "eu,uk,us,au"

    league_keys = SPORTS_CONFIG.get(sport_name.lower())
    if not league_keys:
        raise ValueError(f"Ungueltige Sportart. Erlaubt: {list(SPORTS_CONFIG.keys())}")

    for league in league_keys:
        try:
            logger.info(f"Lade Quoten fuer {league}...")
            data = get_odds_from_api(
                sport_key=league, regions=regions, markets="h2h", bookmakers=None
            )
            all_data.extend(data)
        except Exception as e:
            logger.error(f"Fehler beim Laden von {league}: {e}")

    return all_data
