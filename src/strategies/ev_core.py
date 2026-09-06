"""
ev_core.py
==========
Shared EV-matching logic used by both the CLI (ev_logarithmic.py) and the
service layer (ev_service.py, consumed by the Lambda handler / Telegram
notifier). Pure functions only — no printing, no file I/O, no API calls —
so both entry points stay in sync by construction.
"""

import logging
import math
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

logger = logging.getLogger(__name__)

# Betting exchanges, whose quoted odds are pre-commission and so overstate the
# real return by roughly the commission rate (~2-5%). Comparing them against
# Pinnacle's fair odds produces a systematic phantom edge, so they are skipped
# rather than treated as fixed-odds bookmakers. Extend this if you enable
# regions carrying exchanges not listed here.
EXCLUDED_BOOKMAKER_KEYS = {
    "betfair_ex_uk",
    "betfair_ex_eu",
    "betfair_ex_au",
    "matchbook",
    "smarkets",
}

# Slack allowed when checking that a set of probabilities sums to 1.0.
_PROB_SUM_TOLERANCE = 1e-9


def _parse_commence_time(commence_time: str | None) -> datetime | None:
    """Parse an ISO 8601 UTC commence_time as returned by The Odds API."""
    if not commence_time:
        return None
    try:
        return datetime.fromisoformat(str(commence_time).replace("Z", "+00:00"))
    except (ValueError, TypeError):
        return None


def _format_kickoff(commence_time: str | None) -> str | None:
    """Format an ISO 8601 UTC commence_time (as returned by The Odds API) as a
    readable Europe/Berlin local time string, e.g. 'Fri, 28 Aug 18:30'."""
    dt_utc = _parse_commence_time(commence_time)
    if dt_utc is None:
        return None
    try:
        return dt_utc.astimezone(ZoneInfo("Europe/Berlin")).strftime("%a, %d %b %H:%M")
    except (ValueError, TypeError):
        return None


def solve_logarithmic_pure(odds_bookmaker: list[float]) -> tuple[list[float], list[float]]:
    """
    Calculate fair probabilities based on Pinnacle odds using the Logarithmic Function Model.

    Supports both 2-outcome (e.g., Tennis, Basketball) and 3-outcome (e.g., Football) bets.
    Finds k (1/n) such that sum(p_i ^ k) = 1.0.

    :param odds_bookmaker: A list of decimal odds from the bookmaker.
    :return: A tuple containing fair odds and fair probabilities.
    :raises ValueError: If the odds are empty, not all finite and > 1.0, imply
                        no bookmaker margin, or the solver fails to converge.
    """
    if not odds_bookmaker:
        raise ValueError("At least one outcome is required")
    if any(not math.isfinite(float(o)) or float(o) <= 1.0 for o in odds_bookmaker):
        raise ValueError(f"All odds must be finite and > 1.0, got: {odds_bookmaker}")

    probs = [1.0 / float(o) for o in odds_bookmaker]

    # The search below is bracketed at k >= 1, i.e. it can only *remove* vig.
    # If the implied probabilities already sum to less than 1 there is no vig
    # to remove -- the line is stale, or the market is incomplete -- and the
    # search would silently return the raw probabilities, inflating every
    # downstream EV. Fail loudly instead; callers skip the match.
    overround = sum(probs)
    if overround < 1.0 - _PROB_SUM_TOLERANCE:
        raise ValueError(
            f"Implied probabilities must sum to >= 1.0 (no margin to remove), "
            f"got {overround:.6f} for odds: {odds_bookmaker}"
        )

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

    total = sum(true_probs)
    if abs(total - 1.0) > _PROB_SUM_TOLERANCE:
        raise ValueError(
            f"Solver did not converge: fair probabilities sum to {total:.9f} "
            f"(k={k_val:.6f}) for odds: {odds_bookmaker}"
        )

    true_odds = [1.0 / tp for tp in true_probs]
    return true_odds, true_probs


def _outcome_prices(market: dict) -> dict[str, float]:
    """
    Build an {outcome name: decimal price} dict from an h2h market.

    Outcomes missing a name or a usable numeric price are skipped rather than
    raising: a single malformed record from the API would otherwise abort the
    whole scan, and callers already handle a market with missing outcomes.
    """
    prices: dict[str, float] = {}
    for oc in market.get("outcomes", []):
        name = oc.get("name")
        price = oc.get("price")
        if not name or price is None:
            continue
        try:
            prices[name] = float(price)
        except (TypeError, ValueError):
            logger.warning("Skipping outcome %r with non-numeric price %r", name, price)
    return prices


def _find_draw_key(odds_dict: dict) -> str | None:
    """Find the draw outcome key in a bookmaker's odds dict, if present."""
    return next(
        (
            k
            for k in odds_dict.keys()
            if "draw" in k.lower() or k.lower() == "draw" or k.lower() == "match nul"
        ),
        None,
    )


def _find_pinnacle_odds(match: dict) -> tuple[list[float] | None, list[str], list[str]]:
    """
    Locate Pinnacle's h2h odds for a match, in home/[draw]/away order.

    :param match: A match dict as returned by fetch_odds_for_sport.
    :return: (odds, outcome names, display labels) — odds is None if not found.
    """
    home_team = match.get("home_team")
    away_team = match.get("away_team")

    for bm in match.get("bookmakers", []):
        if bm.get("key") != "pinnacle":
            continue

        h2h_market = next((m for m in bm.get("markets", []) if m.get("key") == "h2h"), None)
        if not h2h_market:
            continue

        odds_dict = _outcome_prices(h2h_market)
        draw_key = _find_draw_key(odds_dict)

        if draw_key:
            # 3 Outcomes: Home, Draw, Away
            if home_team in odds_dict and away_team in odds_dict:
                return (
                    [odds_dict[home_team], odds_dict[draw_key], odds_dict[away_team]],
                    [home_team, "Draw", away_team],
                    ["Home (1)", "Draw (X)", "Away (2)"],
                )
        else:
            # 2 Outcomes: Home, Away
            if home_team in odds_dict and away_team in odds_dict:
                return (
                    [odds_dict[home_team], odds_dict[away_team]],
                    [home_team, away_team],
                    ["Home (1)", "Away (2)"],
                )
        break

    return None, [], []


def find_positive_ev_bets(
    odds_data: list[dict],
    kelly_fraction: float = 0.25,
    ev_threshold: float = 0.0,
    max_fair_odds: float | None = None,
    skip_started: bool = True,
) -> list[dict]:
    """
    Compare every bookmaker's odds against Pinnacle-derived fair probabilities
    and return all bets with EV% >= ev_threshold, sorted descending by EV%.

    This is the single implementation of the matching/EV/Kelly logic, shared
    by the CLI (ev_logarithmic.py) and the service layer (ev_service.py).

    :param odds_data: List of match dicts as returned by fetch_odds_for_sport,
                       or loaded from a saved CSV.
    :param kelly_fraction: Fractional Kelly multiplier (0-1).
    :param ev_threshold: Minimum EV% to include in results.
    :param max_fair_odds: If set, skip outcomes whose Pinnacle-derived fair
                           odds are >= this value. Longshots (high fair odds)
                           were found in backtesting to underperform their
                           theoretical EV — see notebooks/backtest_ev_strategy.ipynb.
    :param skip_started: Skip matches whose kickoff has already passed. The API
                          returns in-play events, and a live bookmaker line
                          compared against a same-snapshot Pinnacle line is not
                          the strategy this was backtested on. Pass False when
                          replaying saved historical odds.
    :raises ValueError: If kelly_fraction is out of range.
    :return: List of bet dicts sorted by ev_percent descending.
    """
    if not math.isfinite(kelly_fraction) or not 0 <= kelly_fraction <= 1:
        raise ValueError("kelly_fraction must be finite and between 0 and 1")

    positive_ev_bets: list[dict] = []
    now = datetime.now(timezone.utc)

    for match in odds_data:
        home_team = match.get("home_team")
        away_team = match.get("away_team")
        sport_title = match.get("sport_title")
        commence_time = match.get("commence_time")
        kickoff = _format_kickoff(commence_time)

        if skip_started:
            # A match with no parseable kickoff is kept: we can't show it has
            # started, and dropping it would silently lose bettable markets.
            starts_at = _parse_commence_time(commence_time)
            if starts_at is not None and starts_at <= now:
                continue

        try:
            pinnacle_odds, outcomes_order, labels = _find_pinnacle_odds(match)
            if not pinnacle_odds:
                continue
            fair_odds, fair_probs = solve_logarithmic_pure(pinnacle_odds)
        except Exception as e:
            logger.error("Error calculating EV for %s - %s: %s", home_team, away_team, e)
            continue

        if max_fair_odds is not None:
            outcomes_order = [o for o, fo in zip(outcomes_order, fair_odds) if fo < max_fair_odds]
            labels = [lbl for lbl, fo in zip(labels, fair_odds) if fo < max_fair_odds]
            keep_idx = [i for i, fo in enumerate(fair_odds) if fo < max_fair_odds]
            pinnacle_odds = [pinnacle_odds[i] for i in keep_idx]
            fair_odds = [fair_odds[i] for i in keep_idx]
            fair_probs = [fair_probs[i] for i in keep_idx]
            if not pinnacle_odds:
                continue

        seen_bets: set[tuple] = set()

        for bm in match.get("bookmakers", []):
            bm_key = bm.get("key")
            if bm_key == "pinnacle" or bm_key in EXCLUDED_BOOKMAKER_KEYS:
                continue

            h2h_market = next((m for m in bm.get("markets", []) if m.get("key") == "h2h"), None)
            if not h2h_market:
                continue

            odds_dict = _outcome_prices(h2h_market)

            # Does this bookie have all odds for our outcomes?
            bookie_odds: list[float] = []
            valid = True
            for name in outcomes_order:
                if name == "Draw":
                    draw_key = _find_draw_key(odds_dict)
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

            for i in range(len(pinnacle_odds)):
                b_odd = bookie_odds[i]
                f_prob = fair_probs[i]
                f_odd = fair_odds[i]

                ev = (f_prob * b_odd) - 1.0
                if ev <= 0.0:
                    continue

                # Deduplication: identify a bet uniquely
                bet_key = (f"{home_team} - {away_team}", outcomes_order[i], b_odd)
                if bet_key in seen_bets:
                    continue
                seen_bets.add(bet_key)

                # Kelly Criterion: b = odds - 1 (net profit), p = fair_prob
                b = b_odd - 1
                kelly = (f_prob * b - (1 - f_prob)) / b
                kelly_frac = kelly * kelly_fraction * 100
                ev_percent = round(ev * 100, 2)

                if ev_percent < ev_threshold:
                    continue

                positive_ev_bets.append(
                    {
                        "league": sport_title,
                        "match": f"{home_team} - {away_team}",
                        "kickoff": kickoff,
                        "outcome": outcomes_order[i],
                        "type": labels[i],
                        "bookmaker": bm.get("title"),
                        "bookmaker_key": bm_key,
                        "bookmaker_odds": b_odd,
                        "pinnacle_odds": round(pinnacle_odds[i], 2),
                        "fair_odds": round(f_odd, 2),
                        "ev_percent": ev_percent,
                        "kelly_suggested": round(max(0.0, kelly_frac), 2),
                    }
                )

    positive_ev_bets.sort(key=lambda x: x["ev_percent"], reverse=True)
    return positive_ev_bets
