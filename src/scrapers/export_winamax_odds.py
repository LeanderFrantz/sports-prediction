"""
export_winamax_odds.py
======================
Skript zum Abrufen von Winamax-Quoten fuer beliebige Sportarten und Speichern als JSON-Datei.
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
    """Konvertiert ein Match-Objekt in ein serialisierbares Dictionary."""
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


def get_matches_for_sport(sport_name: str) -> list:
    """Lädt alle Matches für die angegebene Sportart."""
    sport_name_lower = sport_name.lower()
    if sport_name_lower not in SPORT_IDS:
        raise ValueError(
            f"Ungueltige Sportart '{sport_name}'. Waehle aus: {list(SPORT_IDS.keys())}"
        )

    sport_id = SPORT_IDS[sport_name_lower]
    url = f"{BASE_URL}/{sport_id}"

    with requests.Session() as session:
        logger.info(f"Lade Winamax-Seite fuer {sport_name} (ID: {sport_id}): {url}")
        state = fetch_winamax_page(url, session)
        matches = parse_matches(state)
        # Filtern, um nur Matches der gewählten Sportart (ohne Langzeit-/Saisonwetten) zu behalten
        filtered_matches = [
            m for m in matches if m.sport_id == sport_id and not m.is_outright
        ]
        logger.info(f"Gefunden: {len(filtered_matches)} Spiele (nach Filterung)")
        return filtered_matches


def export_odds_to_json(sport: str, output_path: str = None):
    """Ruft die Matches der gewünschten Sportart ab und speichert sie."""
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
            f"\n[ERFOLG] {len(matches)} Quoten für {sport.capitalize()} wurden exportiert nach: {output_path}"
        )

    except Exception as e:
        logger.error(f"Fehler beim Exportieren der Quoten: {e}", exc_info=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Winamax Quoten Scraper & Exporter")
    parser.add_argument(
        "sport",
        type=str,
        nargs="?",
        default="football",
        help=f"Die gewünschte Sportart. Optionen: {', '.join(SPORT_IDS.keys())} (Standard: football)",
    )
    parser.add_argument(
        "--output",
        type=str,
        help="Pfad zur Ausgabedatei (Standard: winamax_<sport>_odds.json)",
    )

    args = parser.parse_args()
    export_odds_to_json(args.sport, args.output)
