"""
ev_logarithmic.py
==================
CLI entry point for EV analysis. Fetches (or loads) odds, prints the
positive-EV bets to stdout, and auto-saves fetched odds to CSV.

The actual matching/EV/Kelly logic lives in ev_core.py and is shared with
ev_service.py (the Lambda / Telegram path).
"""

import argparse
import logging
import ast

import pandas as pd
from dotenv import load_dotenv

# Load ODDS_API_KEY / TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_IDS from .env before
# the project modules below read them from the environment.
load_dotenv()

from ..fetch_odds_api import SPORTS_CONFIG, fetch_odds_for_sport
from ..telegram_notifier import TelegramNotifier
from .ev_core import find_positive_ev_bets

logger = logging.getLogger(__name__)


def load_odds_from_csv(file_path: str) -> list[dict]:
    """
    Load odds data from a CSV file.

    :param file_path: Path to the CSV file.
    :return: A list of match dictionaries.
    """
    df = pd.read_csv(file_path)
    odds_data = []
    for _, row in df.iterrows():
        match = {
            "id": row["id"],
            "sport_key": row["sport_key"],
            "sport_title": row["sport_title"],
            "commence_time": row["commence_time"],
            "home_team": row["home_team"],
            "away_team": row["away_team"],
            "bookmakers": ast.literal_eval(row["bookmakers"]),
        }
        odds_data.append(match)
    return odds_data


def save_odds_to_csv(odds_data: list[dict], file_path: str) -> None:
    """
    Save loaded odds data to a CSV file.

    :param odds_data: List of match dictionaries to save.
    :param file_path: Path to the destination CSV file.
    """
    # Convert data to a flat format for CSV
    rows = []
    for match in odds_data:
        rows.append(
            {
                "id": match["id"],
                "sport_key": match["sport_key"],
                "sport_title": match["sport_title"],
                "commence_time": match["commence_time"],
                "home_team": match["home_team"],
                "away_team": match["away_team"],
                "bookmakers": str(match["bookmakers"]),
            }
        )

    df = pd.DataFrame(rows)
    df.to_csv(file_path, index=False)
    logger.info(f"Odds successfully saved to {file_path}.")


def analyze_evs(
    sport: str,
    limit: int = None,
    data_file: str = None,
    kelly_fraction: float = 0.25,
    telegram: bool = False,
) -> None:
    """
    Analyze expected value (EV) for a given sport or data file and print results.

    :param sport: The sport identifier to fetch and analyze.
    :param limit: Maximum number of positive EV bets to display.
    :param data_file: Optional path to a CSV file to skip API fetching.
    :param kelly_fraction: The fractional Kelly multiplier to use.
    :param telegram: If True, also send the displayed bets to Telegram
                      (requires TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_IDS).
    """
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s"
    )

    if data_file:
        logger.info(f"Loading odds from file: {data_file}...")
        odds_data = load_odds_from_csv(data_file)
    else:
        result = fetch_odds_for_sport(sport)
        odds_data = result["data"]
        errors = result["errors"]

        if errors:
            for err in errors:
                logger.warning(f"Error in league {err['league']}: {err['error']}")

        if not odds_data:
            logger.error(f"No data available for {sport}.")
            return

        # Auto-save if no file was specified
        save_odds_to_csv(odds_data, f"data/{sport.lower()}_odds_data.csv")

    logger.info(
        f"Loaded {len(odds_data)} {sport.capitalize()} matches in total. Starting EV calculation..."
    )

    positive_ev_bets = find_positive_ev_bets(odds_data, kelly_fraction=kelly_fraction)

    # Limit anwenden
    display_bets = positive_ev_bets[:limit] if limit is not None else positive_ev_bets
    limit_title = f" (Top {limit})" if limit is not None else " (Alle)"

    print("\n" + "=" * 80)
    print(f" MATCHES WITH POSITIVE EXPECTED VALUE (EV){limit_title} — Logarithmic Model")
    print("=" * 80)
    if not display_bets:
        print(" No bets with positive EV found.")
    else:
        for idx, bet in enumerate(display_bets, 1):
            if bet.get("kickoff"):
                print(f"   📅 {bet['kickoff']}")
            print(f"{idx}. [{bet['league']}] {bet['match']}")
            print(f"   Tip: {bet['outcome']} ({bet['type']})")
            print(
                f"   Bookmaker: {bet['bookmaker']} | Odds: {bet['bookmaker_odds']} "
                f"(Fair: {bet['fair_odds']}, Pinnacle: {bet['pinnacle_odds']})"
            )
            print(f"   EXPECTED VALUE (EV): +{bet['ev_percent']}%")
            print(
                f"   Suggested fractional ({kelly_fraction:.2%}) Kelly: +{bet['kelly_suggested']}%"
            )
            print("-" * 80)

    if telegram:
        try:
            notifier = TelegramNotifier()
            notifier.notify_bets(
                bets=display_bets,
                threshold=0.0,
                sports=[sport],
                kelly_fraction=kelly_fraction,
            )
            logger.info("Sent %d bet(s) to Telegram.", len(display_bets))
        except Exception as e:
            logger.error("Failed to send Telegram notification: %s", e)


def main():
    parser = argparse.ArgumentParser(
        description="Calculate positive EVs for various sports."
    )
    parser.add_argument(
        "limit",
        type=int,
        nargs="?",
        default=None,
        help="Number of top bets to display (e.g. 10 for Top 10)",
    )
    parser.add_argument(
        "--sport",
        type=str,
        default="football",
        choices=list(SPORTS_CONFIG.keys()),
        help="The sport to analyze (Default: football)",
    )
    parser.add_argument(
        "--data-file",
        type=str,
        default=None,
        help="Path to a CSV file with match data (instead of API call)",
    )
    parser.add_argument(
        "--kelly-fraction",
        type=float,
        default=0.25,
        help="Fraction for Kelly Criterion (Default: 0.25)",
    )
    parser.add_argument(
        "--telegram",
        action="store_true",
        help="Also send the displayed bets to Telegram (default: off; "
        "requires TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_IDS in .env)",
    )
    args = parser.parse_args()
    analyze_evs(args.sport, args.limit, args.data_file, args.kelly_fraction, args.telegram)


if __name__ == "__main__":
    main()
