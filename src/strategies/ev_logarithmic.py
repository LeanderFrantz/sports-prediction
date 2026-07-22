import argparse
import logging
import math
import ast
import pandas as pd

from ..fetch_odds_api import SPORTS_CONFIG, fetch_odds_for_sport

logger = logging.getLogger(__name__)


def solve_logarithmic_pure(odds_bookmaker: list[float]) -> tuple[list[float], list[float]]:
    """
    Calculate fair probabilities based on Pinnacle odds using the Logarithmic Function Model.

    Supports both 2-outcome (e.g., Tennis, Basketball) and 3-outcome (e.g., Football) bets.
    Finds k (1/n) such that sum(p_i ^ k) = 1.0.

    :param odds_bookmaker: A list of decimal odds from the bookmaker.
    :return: A tuple containing fair odds and fair probabilities.
    :raises ValueError: If any odds are <= 1.0.
    """
    if any(o <= 1.0 for o in odds_bookmaker):
        raise ValueError(f"All odds must be > 1.0, got: {odds_bookmaker}")
    probs = [1.0 / float(o) for o in odds_bookmaker]

    low = 1.0
    high = 20.0

    # Expand boundaries if needed
    while sum(p**high for p in probs) > 1.0:
        high *= 2.0

    for _ in range(100):
        mid = (low + high) / 2.0
        s = sum(p**mid for p in probs)
        if s > 1.0:
            low = mid
        else:
            high = mid

    k_val = (low + high) / 2.0
    true_probs = [p**k_val for p in probs]
    true_odds = [1.0 / tp for tp in true_probs]
    return true_odds, true_probs


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
    sport: str, limit: int = None, data_file: str = None, kelly_fraction: float = 0.25
) -> None:
    """
    Analyze expected value (EV) for a given sport or data file.

    :param sport: The sport identifier to fetch and analyze.
    :param limit: Maximum number of positive EV bets to display.
    :param data_file: Optional path to a CSV file to skip API fetching.
    :param kelly_fraction: The fractional Kelly multiplier to use.
    """
    if not math.isfinite(kelly_fraction) or not 0 <= kelly_fraction <= 1:
        raise ValueError("kelly_fraction must be finite and between 0 and 1")
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
        if not data_file:
            save_odds_to_csv(odds_data, f"data/{sport.lower()}_odds_data.csv")

    logger.info(
        f"Loaded {len(odds_data)} {sport.capitalize()} matches in total. Starting EV calculation..."
    )

    positive_ev_bets = []

    for match in odds_data:
        home_team = match.get("home_team")
        away_team = match.get("away_team")
        sport_title = match.get("sport_title")

        # 1) Find Pinnacle odds
        pinnacle_odds = None
        outcomes_order = []  # Order of teams for the match

        for bm in match.get("bookmakers", []):
            if bm.get("key") == "pinnacle":
                # Explicitly find h2h market
                h2h_market = next(
                    (m for m in bm.get("markets", []) if m.get("key") == "h2h"), None
                )
                if not h2h_market:
                    continue

                outcomes = h2h_market.get("outcomes", [])

                # Build odds dynamically based on number of outcomes (2 or 3)
                odds_dict = {oc["name"]: oc["price"] for oc in outcomes}

                # Search for Draw key
                draw_key = next(
                    (
                        k
                        for k in odds_dict.keys()
                        if "draw" in k.lower()
                        or k.lower() == "draw"
                        or k.lower() == "match nul"
                    ),
                    None,
                )

                # Determine order
                if draw_key:
                    # 3 Outcomes: Home, Draw, Away
                    if home_team in odds_dict and away_team in odds_dict:
                        pinnacle_odds = [
                            odds_dict[home_team],
                            odds_dict[draw_key],
                            odds_dict[away_team],
                        ]
                        outcomes_order = [home_team, "Draw", away_team]
                        labels = ["Home (1)", "Draw (X)", "Away (2)"]
                else:
                    # 2 Outcomes: Home, Away
                    if home_team in odds_dict and away_team in odds_dict:
                        pinnacle_odds = [odds_dict[home_team], odds_dict[away_team]]
                        outcomes_order = [home_team, away_team]
                        labels = ["Home (1)", "Away (2)"]
                break

        if not pinnacle_odds:
            continue

        # 2) Calculate Fair Odds using Logarithmic Function Model
        try:
            fair_odds, fair_probs = solve_logarithmic_pure(pinnacle_odds)
        except Exception as e:
            logger.error(
                f"Error calculating EV for {home_team} - {away_team}: {e}"
            )
            continue

        # 3) Compare with all other bookmakers
        seen_bets = set()
        # Excluded exchange keys
        excluded_keys = {"h2h_lay", "betfair_ex_uk", "betfair_ex_eu", "betfair_ex_au"}

        for bm in match.get("bookmakers", []):
            bm_key = bm.get("key")
            if bm_key == "pinnacle" or bm_key in excluded_keys:
                continue

            # Explicitly find h2h market
            h2h_market = next(
                (m for m in bm.get("markets", []) if m.get("key") == "h2h"), None
            )
            if not h2h_market:
                continue

            outcomes = h2h_market.get("outcomes", [])
            odds_dict = {oc["name"]: oc["price"] for oc in outcomes}

            # Does this bookie have all odds for our outcomes?
            bookie_odds = []
            valid = True
            for name in outcomes_order:
                # Flexible matching for Draw
                if name == "Draw":
                    draw_key = next(
                        (
                            k
                            for k in odds_dict.keys()
                            if "draw" in k.lower()
                            or k.lower() == "draw"
                            or k.lower() == "match nul"
                        ),
                        None,
                    )
                    if draw_key:
                        bookie_odds.append(odds_dict[draw_key])
                    else:
                        valid = False
                elif name in odds_dict:
                    bookie_odds.append(odds_dict[name])
                else:
                    valid = False

            if not valid or len(bookie_odds) != len(pinnacle_odds):
                continue

            # Calculate EV for each outcome
            for i in range(len(pinnacle_odds)):
                b_odd = bookie_odds[i]
                f_prob = fair_probs[i]
                f_odd = fair_odds[i]

                ev = (f_prob * b_odd) - 1.0

                if ev > 0.0:  # Positive EV!
                    # Deduplication logic: Identify bet uniquely
                    bet_key = (f"{home_team} - {away_team}", outcomes_order[i], b_odd)
                    if bet_key in seen_bets:
                        continue
                    seen_bets.add(bet_key)

                    # Kelly Criterion
                    # b = odds - 1 (net profit)
                    # p = fair_prob
                    # q = 1 - p (loss probability)
                    # Kelly = (p * b - q) / b
                    b = b_odd - 1
                    kelly = (f_prob * b - (1 - f_prob)) / b
                    kelly_frac = kelly * kelly_fraction * 100

                    positive_ev_bets.append(
                        {
                            "league": sport_title,
                            "match": f"{home_team} - {away_team}",
                            "outcome": outcomes_order[i],
                            "type": labels[i],
                            "bookmaker": bm.get("title"),
                            "bookmaker_key": bm_key,
                            "bookmaker_odds": b_odd,
                            "fair_odds": round(f_odd, 2),
                            "ev_percent": round(ev * 100, 2),
                            "kelly_suggested": round(max(0.0, kelly_frac), 2),
                        }
                    )

    # Sortiere nach hoechstem EV
    positive_ev_bets.sort(key=lambda x: x["ev_percent"], reverse=True)

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
            print(f"{idx}. [{bet['league']}] {bet['match']}")
            print(f"   Tip: {bet['outcome']} ({bet['type']})")
            print(
                f"   Bookmaker: {bet['bookmaker']} | Odds: {bet['bookmaker_odds']} (Fair: {bet['fair_odds']})"
            )
            print(f"   EXPECTED VALUE (EV): +{bet['ev_percent']}%")
            print(
                f"   Suggested fractional ({kelly_fraction:.2%}) Kelly: +{bet['kelly_suggested']}%"
            )
            print("-" * 80)


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
    args = parser.parse_args()
    analyze_evs(args.sport, args.limit, args.data_file, args.kelly_fraction)


if __name__ == "__main__":
    main()
