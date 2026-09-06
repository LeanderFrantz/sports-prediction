"""
ev_logarithmic.py
==================
CLI entry point for EV analysis. Fetches (or loads) odds, prints the
positive-EV bets to stdout, and auto-saves fetched odds to CSV.

The actual matching/EV/Kelly logic lives in ev_core.py and is shared with
ev_service.py (the Lambda / Telegram path).
"""

import argparse
import ast
import json
import logging
import os

import pandas as pd
from dotenv import load_dotenv

from ..fetch_odds_api import SPORTS_CONFIG, fetch_odds_for_sport
from ..telegram_notifier import TelegramNotifier
from .ev_core import (
    DEFAULT_DISPLAY_TIMEZONE,
    DEFAULT_EV_THRESHOLD,
    DEFAULT_KELLY_FRACTION,
    DEFAULT_MAX_FAIR_ODDS,
    find_positive_ev_bets,
)

logger = logging.getLogger(__name__)

# Populate ODDS_API_KEY / TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_IDS from .env.
# These are read at call time, so this no longer has to run before the imports
# above.
load_dotenv()


def load_odds_from_csv(file_path: str) -> list[dict]:
    """
    Load odds data from a legacy CSV file.

    Retained for CSVs saved before the switch to newline-delimited JSON; new
    saves go through save_odds(). The bookmakers column holds a repr() of the
    original nested structure, so it has to be parsed back out row by row.

    :param file_path: Path to the CSV file.
    :return: A list of match dictionaries.
    """
    df = pd.read_csv(file_path)
    odds_data = []
    for idx, row in df.iterrows():
        raw = row["bookmakers"]
        # An empty or truncated cell reads back as NaN and used to raise,
        # losing the whole file over one bad row.
        if not isinstance(raw, str):
            logger.warning("Row %s has no bookmakers data; skipping.", idx)
            continue
        try:
            bookmakers = ast.literal_eval(raw)
        except (ValueError, SyntaxError) as e:
            logger.warning("Row %s has unparseable bookmakers data (%s); skipping.", idx, e)
            continue
        odds_data.append(
            {
                "id": row["id"],
                "sport_key": row["sport_key"],
                "sport_title": row["sport_title"],
                "commence_time": row["commence_time"],
                "home_team": row["home_team"],
                "away_team": row["away_team"],
                "bookmakers": bookmakers,
            }
        )
    return odds_data


def load_odds(file_path: str) -> list[dict]:
    """
    Load odds data from a saved file, newline-delimited JSON or legacy CSV.

    :param file_path: Path to the file; a .csv suffix selects the legacy reader.
    :return: A list of match dictionaries.
    """
    if file_path.lower().endswith(".csv"):
        return load_odds_from_csv(file_path)

    odds_data = []
    with open(file_path, encoding="utf-8") as fh:
        for lineno, line in enumerate(fh, 1):
            line = line.strip()
            if not line:
                continue
            try:
                odds_data.append(json.loads(line))
            except json.JSONDecodeError as e:
                logger.warning("Line %d is not valid JSON (%s); skipping.", lineno, e)
    return odds_data


def save_odds(odds_data: list[dict], file_path: str) -> None:
    """
    Save odds data as newline-delimited JSON, one match per line.

    The API response is already JSON, so this round-trips losslessly and
    without the repr()/literal_eval() dance the CSV format required.

    :param odds_data: List of match dictionaries to save.
    :param file_path: Path to the destination file.
    """
    directory = os.path.dirname(file_path)
    if directory:
        os.makedirs(directory, exist_ok=True)

    with open(file_path, "w", encoding="utf-8") as fh:
        for match in odds_data:
            fh.write(json.dumps(match, ensure_ascii=False) + "\n")
    logger.info("Odds successfully saved to %s.", file_path)


def analyze_evs(
    sport: str,
    limit: int | None = None,
    data_file: str | None = None,
    kelly_fraction: float = DEFAULT_KELLY_FRACTION,
    telegram: bool = False,
    preferred_bookmakers: list[str] | None = None,
    ev_threshold: float = DEFAULT_EV_THRESHOLD,
    max_fair_odds: float | None = DEFAULT_MAX_FAIR_ODDS,
    display_timezone: str | None = None,
) -> None:
    """
    Analyze expected value (EV) for a given sport or data file and print results.

    :param sport: The sport identifier to fetch and analyze.
    :param limit: Maximum number of positive EV bets to display.
    :param data_file: Optional path to a CSV file to skip API fetching.
    :param kelly_fraction: The fractional Kelly multiplier to use.
    :param telegram: If True, also send the displayed bets to Telegram
                      (requires TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_IDS).
    :param preferred_bookmakers: Bookmaker keys to favour when a bet ties across
                      books (see ev_core.find_positive_ev_bets). None keeps the
                      default.
    :param ev_threshold: Minimum EV% to display. Defaults to the same value the
                      Lambda applies, so the CLI shows what production sends.
    :param max_fair_odds: Skip outcomes with fair odds >= this. None disables
                      the cap.
    :param display_timezone: IANA name for rendering kickoff times. None keeps
                      the default.
    """
    if data_file:
        logger.info(f"Loading odds from file: {data_file}...")
        odds_data = load_odds(data_file)
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
        save_odds(odds_data, f"data/{sport.lower()}_odds_data.jsonl")

    logger.info(
        f"Loaded {len(odds_data)} {sport.capitalize()} matches in total. Starting EV calculation..."
    )

    # Live fetches skip matches that have already kicked off; a saved CSV is
    # historical by definition, so replaying one keeps every match.
    positive_ev_bets = find_positive_ev_bets(
        odds_data,
        kelly_fraction=kelly_fraction,
        ev_threshold=ev_threshold,
        max_fair_odds=max_fair_odds,
        skip_started=data_file is None,
        preferred_bookmakers=preferred_bookmakers,
        display_timezone=display_timezone,
    )

    # Apply the display limit
    display_bets = positive_ev_bets[:limit] if limit is not None else positive_ev_bets
    limit_title = f" (Top {limit})" if limit is not None else " (all)"

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
    # Configured here rather than in analyze_evs(): that function is importable,
    # and configuring the root logger is the entry point's job, not a library's.
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s"
    )

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
        help="Path to a saved odds file to analyse instead of calling the API "
        "(newline-delimited JSON, or a legacy .csv)",
    )
    parser.add_argument(
        "--kelly-fraction",
        type=float,
        default=DEFAULT_KELLY_FRACTION,
        help=f"Fraction for Kelly Criterion (Default: {DEFAULT_KELLY_FRACTION})",
    )
    parser.add_argument(
        "--ev-threshold",
        type=float,
        default=DEFAULT_EV_THRESHOLD,
        help="Minimum EV%% to display. Defaults to the value the Lambda applies "
        f"({DEFAULT_EV_THRESHOLD}), so the CLI shows what production sends. "
        "Pass 0 to see every positive-EV bet, or a negative value to include "
        "negative-EV bets for calibration.",
    )
    parser.add_argument(
        "--max-fair-odds",
        type=float,
        default=DEFAULT_MAX_FAIR_ODDS,
        help="Skip outcomes whose Pinnacle-derived fair odds are >= this "
        f"(Default: {DEFAULT_MAX_FAIR_ODDS}). Pass 0 to disable the cap.",
    )
    parser.add_argument(
        "--preferred-bookmakers",
        type=str,
        default=None,
        help="Comma-separated Odds API bookmaker keys to favour when a bet ties "
        "across books, most preferred first (e.g. tipico_de,winamax_de). These "
        "are keys, not display titles. Default: PREFERRED_BOOKMAKER_KEYS",
    )
    parser.add_argument(
        "--timezone",
        type=str,
        default=None,
        help="IANA timezone for kickoff times, e.g. Europe/Berlin or UTC "
        f"(Default: {DEFAULT_DISPLAY_TIMEZONE}). An unknown name falls back to UTC.",
    )
    parser.add_argument(
        "--telegram",
        action="store_true",
        help="Also send the displayed bets to Telegram (default: off; "
        "requires TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_IDS in .env)",
    )
    args = parser.parse_args()
    preferred = (
        [b.strip() for b in args.preferred_bookmakers.split(",") if b.strip()] or None
        if args.preferred_bookmakers
        else None
    )
    analyze_evs(
        args.sport,
        args.limit,
        args.data_file,
        args.kelly_fraction,
        args.telegram,
        preferred,
        args.ev_threshold,
        # 0 (or less) disables the cap rather than filtering everything out.
        args.max_fair_odds if args.max_fair_odds > 0 else None,
        args.timezone,
    )


if __name__ == "__main__":
    main()
