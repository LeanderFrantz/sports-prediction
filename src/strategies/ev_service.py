"""
ev_service.py
=============
Service layer for EV analysis, consumed by the Lambda handler / Telegram
notifier. Fetches odds via The Odds API and returns structured data instead
of printing.

The actual matching/EV/Kelly logic lives in ev_core.py and is shared with
ev_logarithmic.py (the CLI path).
"""

import logging

from ..fetch_odds_api import SPORTS_CONFIG, fetch_odds_for_sport
from .ev_core import find_positive_ev_bets

logger = logging.getLogger(__name__)


def get_positive_ev_bets(
    sport: str,
    kelly_fraction: float = 0.25,
    ev_threshold: float = 0.0,
    max_fair_odds: float | None = None,
) -> list[dict]:
    """
    Fetch odds for a sport and return all positive-EV bets.

    :param sport: Sport identifier (must be a key in SPORTS_CONFIG).
    :param kelly_fraction: Fractional Kelly multiplier (0-1).
    :param ev_threshold: Minimum EV% to include in results.
    :param max_fair_odds: If set, skip outcomes with Pinnacle-derived fair
                           odds >= this value (see ev_core.find_positive_ev_bets).
    :return: List of bet dicts sorted by ev_percent descending.
    :raises ValueError: If kelly_fraction is out of range or sport is invalid.
    """
    if sport.lower() not in SPORTS_CONFIG:
        raise ValueError(f"Invalid sport '{sport}'. Allowed: {list(SPORTS_CONFIG.keys())}")

    result = fetch_odds_for_sport(sport)
    odds_data = result["data"]
    errors = result["errors"]

    if errors:
        for err in errors:
            logger.warning("Error in league %s: %s", err["league"], err["error"])

    if not odds_data:
        logger.warning("No data available for %s.", sport)
        return []

    logger.info(
        "Loaded %d %s matches. Starting EV calculation...",
        len(odds_data),
        sport.capitalize(),
    )

    bets = find_positive_ev_bets(
        odds_data, kelly_fraction=kelly_fraction, ev_threshold=ev_threshold, max_fair_odds=max_fair_odds
    )

    logger.info(
        "Found %d positive-EV bets for %s (threshold: %.2f%%).",
        len(bets),
        sport,
        ev_threshold,
    )
    return bets


def get_all_positive_ev_bets(
    sports: list[str] | None = None,
    kelly_fraction: float = 0.25,
    ev_threshold: float = 0.0,
    max_fair_odds: float | None = None,
) -> list[dict]:
    """
    Run EV analysis across multiple sports and return combined results.

    :param sports: List of sport identifiers. Defaults to all configured sports.
    :param kelly_fraction: Fractional Kelly multiplier (0-1).
    :param ev_threshold: Minimum EV% to include in results.
    :param max_fair_odds: If set, skip outcomes with Pinnacle-derived fair
                           odds >= this value (see ev_core.find_positive_ev_bets).
    :return: Combined list of bet dicts sorted by ev_percent descending.
    """
    if sports is None:
        sports = list(SPORTS_CONFIG.keys())

    all_bets: list[dict] = []
    for sport in sports:
        try:
            bets = get_positive_ev_bets(sport, kelly_fraction, ev_threshold, max_fair_odds)
            all_bets.extend(bets)
        except Exception as e:
            logger.error("Failed to analyze %s: %s", sport, e)

    all_bets.sort(key=lambda x: x["ev_percent"], reverse=True)
    return all_bets
