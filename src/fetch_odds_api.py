"""
fetch_odds_api.py
=================
Script to fetch odds for Pinnacle and Winamax via The Odds API.
"""

import logging
import os
import threading
from concurrent.futures import ThreadPoolExecutor

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

logger = logging.getLogger(__name__)

BASE_URL = "https://api.the-odds-api.com/v4/sports"

# Per-request socket timeout. Kept well under the Lambda timeout so a single
# hanging league can't consume the whole budget.
REQUEST_TIMEOUT = 10

# Leagues are fetched concurrently; a modest pool is plenty for ~13 requests.
MAX_FETCH_WORKERS = 8

# The Odds API bills each request as (markets x regions), so four regions costs
# four credits per league -- 52 for a 13-league football scan. Naming
# bookmakers explicitly instead bills as a single region, cutting that to 13,
# but only returns the books you name. Both are configurable: ODDS_REGIONS
# narrows the regions, ODDS_BOOKMAKERS switches to an explicit list and takes
# precedence over regions.
DEFAULT_REGIONS = "eu,uk,us,au"

# Pinnacle is the fair-odds source the whole pipeline is built on; an explicit
# bookmaker list without it can price nothing.
FAIR_ODDS_BOOKMAKER = "pinnacle"

_thread_local = threading.local()


def _get_session() -> requests.Session:
    """
    Return this thread's requests Session, with retries on transient failures.

    Sessions are cached per thread rather than shared, because requests.Session
    is not guaranteed thread-safe. On a warm Lambda container the session (and
    its TLS connection) is reused across invocations.
    """
    session = getattr(_thread_local, "session", None)
    if session is None:
        session = requests.Session()
        retry = Retry(
            total=2,
            backoff_factor=0.5,
            status_forcelist=(429, 500, 502, 503, 504),
            allowed_methods=frozenset(["GET"]),
        )
        session.mount("https://", HTTPAdapter(max_retries=retry))
        _thread_local.session = session
    return session


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


def _resolve_odds_filters(
    regions: str | None = None, bookmakers: str | None = None
) -> tuple[str | None, str | None]:
    """
    Decide the regions/bookmakers filter for a request, from args then env.

    Returns (regions, bookmakers) with exactly one of them set: The Odds API
    treats bookmakers as taking precedence over regions, and billing counts it
    as a single region, so sending both only risks confusion.
    """
    if bookmakers is None:
        bookmakers = os.environ.get("ODDS_BOOKMAKERS", "").strip() or None

    if bookmakers:
        keys = [b.strip() for b in bookmakers.split(",") if b.strip()]
        if FAIR_ODDS_BOOKMAKER not in keys:
            logger.warning(
                "Adding '%s' to the bookmaker filter: it is the fair-odds "
                "source and nothing can be priced without it.",
                FAIR_ODDS_BOOKMAKER,
            )
            keys.insert(0, FAIR_ODDS_BOOKMAKER)
        return None, ",".join(keys)

    if regions is None:
        regions = os.environ.get("ODDS_REGIONS", "").strip() or DEFAULT_REGIONS
    return regions, None


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
    :raises RuntimeError: If ODDS_API_KEY is not set in the environment.
    :return: A list of odds data.
    """
    # Read the key at call time, not at import time: the Lambda sets it in the
    # environment, while the CLI populates it from .env, and an import-time
    # snapshot would miss anything loaded after this module is first imported.
    api_key = os.environ.get("ODDS_API_KEY")
    if not api_key:
        raise RuntimeError(
            "ODDS_API_KEY is not set. Export it or put it in .env "
            "(see .env.example)."
        )

    url = f"{BASE_URL}/{sport_key}/odds/"
    params = {
        "apiKey": api_key,
        "markets": markets,
    }
    # bookmakers takes precedence over regions at the API, and is billed as a
    # single region, so only one of the two is ever sent.
    if bookmakers:
        params["bookmakers"] = bookmakers
    elif regions:
        params["regions"] = regions

    logger.info(
        "Fetching odds from URL: %s with regions=%s, markets=%s, bookmakers=%s",
        url, regions, markets, bookmakers,
    )
    response = _get_session().get(url, params=params, timeout=REQUEST_TIMEOUT)

    # Output header to check remaining requests
    remaining_requests = response.headers.get("x-requests-remaining")
    used_requests = response.headers.get("x-requests-used")
    if remaining_requests:
        logger.info(
            f"API Limits - Used: {used_requests} | Remaining: {remaining_requests}"
        )

    response.raise_for_status()
    return response.json()


def fetch_odds_for_sport(
    sport_name: str, regions: str | None = None, bookmakers: str | None = None
) -> dict:
    """
    Fetch odds for all configured leagues of a sport and return successes and errors.

    :param sport_name: The name of the sport to fetch data for.
    :param regions: Comma-separated regions. Defaults to ODDS_REGIONS, then
                     DEFAULT_REGIONS.
    :param bookmakers: Comma-separated Odds API bookmaker keys. Defaults to
                     ODDS_BOOKMAKERS. When set it replaces the region filter and
                     the request is billed as a single region.
    :raises ValueError: If the sport name is invalid.
    :return: A dictionary containing 'data' (list of odds) and 'errors' (list of failures).
    """
    all_data = []
    errors = []
    regions, bookmakers = _resolve_odds_filters(regions, bookmakers)

    league_keys = SPORTS_CONFIG.get(sport_name.lower())
    if not league_keys:
        raise ValueError(f"Invalid sport. Allowed: {list(SPORTS_CONFIG.keys())}")

    # One credit per market per region; an explicit bookmaker list bills as one.
    credits = len(league_keys) * (len(regions.split(",")) if regions else 1)
    logger.info(
        "Fetching %d %s league(s) with %s — about %d API credit(s).",
        len(league_keys),
        sport_name,
        f"regions={regions}" if regions else f"bookmakers={bookmakers}",
        credits,
    )

    # Fetched concurrently: sequentially, ~13 football leagues at the per-request
    # timeout can exceed the Lambda's own timeout and kill the run mid-scan.
    # Results are collected in league order so output stays deterministic.
    with ThreadPoolExecutor(max_workers=min(MAX_FETCH_WORKERS, len(league_keys))) as pool:
        futures = [
            (
                league,
                pool.submit(
                    get_odds_from_api,
                    sport_key=league,
                    regions=regions,
                    markets="h2h",
                    bookmakers=bookmakers,
                ),
            )
            for league in league_keys
        ]
        for league, future in futures:
            try:
                all_data.extend(future.result())
            except Exception as e:
                logger.error("Error loading %s: %s", league, e)
                errors.append({"league": league, "error": str(e)})

    return {"data": all_data, "errors": errors}

