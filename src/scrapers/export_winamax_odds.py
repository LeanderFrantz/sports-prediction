"""
export_winamax_odds.py
======================
Script to fetch Winamax odds for any sport and save as a JSON file.
"""

import argparse
import json
import logging
from datetime import datetime
from src.scrapers.winamax_scraper import (
    fetch_winamax_page,
    parse_matches,
    Match,
    SPORT_IDS,
    BASE_URL,
)
import requests

logger = logging.getLogger(__name__)


def match_to_dict(m: Match) -> dict:
    """
    Convert a Match object to a serializable dictionary.

    :param m: The Match object to convert.
    :return: A dictionary representation of the match.
    """
    match_dict = {
        "match_id": m.match_id,
        "title": m.title,
        "competitor1": m.competitor1,
        "competitor2": m.competitor2,
        "sport_id": m.sport_id,
        "tournament_id": m.tournament_id,
        "category_id": m.category_id,
        "match_start": m.match_start.isoformat() if m.match_start else None,
        "status": m.status,
        "more_bets_count": m.more_bets_count,
        "odds": None,
    }

    if m.main_bet and m.main_bet.outcomes:
        odds_dict = {}
        for outcome in m.main_bet.outcomes:
            odds_dict[outcome.label] = outcome.odds
        match_dict["odds"] = odds_dict

    return match_dict


def get_matches_for_sport(sport_name: str) -> list[Match]:
    """
    Load all matches for the specified sport.

    :param sport_name: The name of the sport to fetch.
    :return: A list of Match objects.
    :raises ValueError: If the sport name is invalid.
    """
    sport_name_lower = sport_name.lower()
    if sport_name_lower not in SPORT_IDS:
        raise ValueError(
            f"Invalid sport '{sport_name}'. Choose from: {list(SPORT_IDS.keys())}"
        )

    sport_id = SPORT_IDS[sport_name_lower]
    url = f"{BASE_URL}/{sport_id}"

    with requests.Session() as session:
        logger.info("Loading Winamax page for %s (ID: %d): %s", sport_name, sport_id, url)
        state = fetch_winamax_page(url, session)
        matches = parse_matches(state)
        # Filter to keep only matches of the chosen sport (excluding outrights/seasonal bets)
        filtered_matches = [
            m for m in matches if m.sport_id == sport_id and not m.is_outright
        ]
        logger.info("Found: %d matches (after filtering)", len(filtered_matches))
        return filtered_matches


def export_odds_to_json(sport: str, output_path: str = None) -> None:
    """
    Fetch matches for the desired sport and save them.

    :param sport: The name of the sport.
    :param output_path: Optional path to the destination JSON file.
    """
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s"
    )

    if not output_path:
        output_path = f"winamax_{sport.lower()}_odds.json"

    try:
        matches = get_matches_for_sport(sport)
        serialized_matches = [match_to_dict(m) for m in matches]

        export_data = {
            "scraped_at": datetime.now().isoformat(),
            "provider": "winamax",
            "sport": sport.lower(),
            "matches_count": len(serialized_matches),
            "matches": serialized_matches,
        }

        with open(output_path, "w", encoding="utf-8") as f:
            json.dump(export_data, f, ensure_ascii=False, indent=2)

        print(
            f"\n[SUCCESS] {len(matches)} odds for {sport.capitalize()} were exported to: {output_path}"
        )

    except Exception:
        logger.exception("Error exporting odds")
        raise


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Winamax odds scraper & exporter")
    parser.add_argument(
        "sport",
        type=str,
        nargs="?",
        default="football",
        help=f"The desired sport. Options: {', '.join(SPORT_IDS.keys())} (Default: football)",
    )
    parser.add_argument(
        "--output",
        type=str,
        help="Path to the output file (Default: winamax_<sport>_odds.json)",
    )

    args = parser.parse_args()
    export_odds_to_json(args.sport, args.output)
