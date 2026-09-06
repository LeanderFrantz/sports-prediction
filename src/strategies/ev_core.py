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
from functools import lru_cache
from datetime import datetime, timezone, tzinfo
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

# Default bookmakers to favour when the same bet is available at the same odds
# at more than one book -- normally the ones you actually hold an account with.
# Ordered by preference; anything not listed falls back to whichever book the
# API returned first, i.e. no favourite. Callers override this via the
# preferred_bookmakers argument (the Lambda and CLI read it from configuration);
# this module deliberately reads no environment itself.
PREFERRED_BOOKMAKER_KEYS = ["tipico_de", "winamax_de"]

# Application defaults, shared by the CLI and the Lambda so the two cannot
# drift apart. find_positive_ev_bets' own signature defaults stay permissive
# (no threshold, no cap) -- these are what the entry points apply.
DEFAULT_EV_THRESHOLD = 3.0
DEFAULT_MAX_FAIR_ODDS = 5.0
DEFAULT_KELLY_FRACTION = 0.25

# Slack allowed when checking that a set of probabilities sums to 1.0.
_PROB_SUM_TOLERANCE = 1e-9

# Outcome labels used for the draw in a 3-way market, across the bookmakers
# The Odds API returns. Compared exactly, after lowercasing and stripping.
DRAW_OUTCOME_LABELS = {
    "draw",
    "tie",
    "match nul",
    "unentschieden",
    "empate",
    "pareggio",
}

# Kickoff times are displayed in this zone unless a caller overrides it. Like
# the other defaults here, entry points read the override from configuration
# and pass it in: this module reads no environment itself.
DEFAULT_DISPLAY_TIMEZONE = "Europe/Berlin"


@lru_cache(maxsize=None)
def _resolve_display_timezone(name: str) -> tzinfo:
    """
    Resolve a timezone name, falling back to UTC. Cached, so each distinct
    name is looked up once rather than per match.

    ZoneInfo raises ZoneInfoNotFoundError -- a KeyError, not a ValueError -- on
    a runtime with no tzdata, or for a name that does not exist. The deployment
    zip vendors tzdata, but degrading to UTC beats losing every kickoff time,
    or the whole run.
    """
    try:
        return ZoneInfo(name)
    except Exception:
        logger.warning(
            "Timezone %r unavailable (missing tzdata, or unknown name); "
            "showing kickoff times in UTC.",
            name,
        )
        return timezone.utc


def _parse_commence_time(commence_time: str | None) -> datetime | None:
    """Parse an ISO 8601 UTC commence_time as returned by The Odds API."""
    if not commence_time:
        return None
    try:
        return datetime.fromisoformat(str(commence_time).replace("Z", "+00:00"))
    except (ValueError, TypeError):
        return None


def _format_kickoff(commence_time: str | None, tz: tzinfo) -> str | None:
    """Format an ISO 8601 UTC commence_time (as returned by The Odds API) as a
    readable local time string in *tz*, e.g. 'Fri, 28 Aug 18:30'."""
    dt_utc = _parse_commence_time(commence_time)
    if dt_utc is None:
        return None
    try:
        return dt_utc.astimezone(tz).strftime("%a, %d %b %H:%M")
    except Exception:
        # Display-only: never let a formatting failure abort the scan.
        return None


def _bookmaker_preference(bm_key: str | None, preferred: list[str]) -> int:
    """Sort rank for a bookmaker key: lower is more preferred."""
    try:
        return preferred.index(bm_key)
    except ValueError:
        return len(preferred)


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
    """
    Find the draw outcome key in a bookmaker's odds dict, if present.

    Matched exactly against DRAW_OUTCOME_LABELS after normalising, not as a
    substring: a competitor whose name merely contains "draw" used to be taken
    for the draw, turning a 2-outcome market into a 3-outcome one with a
    duplicated competitor and silently bogus probabilities.
    """
    return next((k for k in odds_dict if k.strip().lower() in DRAW_OUTCOME_LABELS), None)


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
    preferred_bookmakers: list[str] | None = None,
    display_timezone: str | None = None,
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
    :param preferred_bookmakers: Bookmaker keys (as used by The Odds API, e.g.
                          "tipico_de", not the display title "Tipico") to favour
                          when the same bet ties across books, most preferred
                          first. Defaults to PREFERRED_BOOKMAKER_KEYS.
    :param display_timezone: IANA name the kickoff times are rendered in, e.g.
                          "Europe/Berlin". Defaults to DEFAULT_DISPLAY_TIMEZONE;
                          an unknown name falls back to UTC with a warning.
    :raises ValueError: If kelly_fraction is out of range.
    :return: List of bet dicts sorted by ev_percent descending.
    """
    if not math.isfinite(kelly_fraction) or not 0 <= kelly_fraction <= 1:
        raise ValueError("kelly_fraction must be finite and between 0 and 1")

    preferred = (
        PREFERRED_BOOKMAKER_KEYS if preferred_bookmakers is None else list(preferred_bookmakers)
    )

    tz = _resolve_display_timezone(display_timezone or DEFAULT_DISPLAY_TIMEZONE)

    positive_ev_bets: list[dict] = []
    seen_bookmaker_keys: set[str] = set()
    now = datetime.now(timezone.utc)

    for match in odds_data:
        home_team = match.get("home_team")
        away_team = match.get("away_team")
        sport_title = match.get("sport_title")
        commence_time = match.get("commence_time")
        kickoff = _format_kickoff(commence_time, tz)

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

        # Identical odds at several books collapse to a single bet below, and
        # the first one seen wins. Sorting preferred books to the front (stably,
        # so everything else keeps API order) makes that winner the one you can
        # actually place the bet with, instead of an arbitrary choice.
        bookmakers = sorted(
            match.get("bookmakers", []),
            key=lambda bm: _bookmaker_preference(bm.get("key"), preferred),
        )

        for bm in bookmakers:
            bm_key = bm.get("key")
            seen_bookmaker_keys.add(bm_key)
            if bm_key == "pinnacle" or bm_key in EXCLUDED_BOOKMAKER_KEYS:
                continue

            h2h_market = next((m for m in bm.get("markets", []) if m.get("key") == "h2h"), None)
            if not h2h_market:
                continue

            odds_dict = _outcome_prices(h2h_market)

            # Does this bookie have all odds for our outcomes? A missing one
            # stops the loop, leaving a short list for the check below --
            # which is what decides, so a separate flag added nothing.
            bookie_odds: list[float] = []
            for name in outcomes_order:
                key = _find_draw_key(odds_dict) if name == "Draw" else name
                if key not in odds_dict:
                    break
                bookie_odds.append(odds_dict[key])

            if len(bookie_odds) != len(pinnacle_odds):
                continue

            for i in range(len(pinnacle_odds)):
                b_odd = bookie_odds[i]
                f_prob = fair_probs[i]
                f_odd = fair_odds[i]

                ev = (f_prob * b_odd) - 1.0

                # ev_threshold is the only gate. It used to be preceded by a
                # hardcoded ev > 0 check, which made a negative threshold --
                # the way you calibrate the model against known-negative bets
                # -- silently return nothing. Compared before rounding, so a
                # bet does not qualify on a rounding artefact.
                if ev * 100 < ev_threshold:
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

    # A misspelled key silently does nothing, so say so. Only meaningful once
    # we have actually seen some bookmakers to compare against.
    if seen_bookmaker_keys:
        unseen = [k for k in preferred if k not in seen_bookmaker_keys]
        if unseen:
            logger.warning(
                "Preferred bookmaker key(s) %s did not appear in any match. "
                "These are The Odds API bookmaker keys (e.g. 'tipico_de'), "
                "not display titles.",
                ", ".join(unseen),
            )

    positive_ev_bets.sort(key=lambda x: x["ev_percent"], reverse=True)
    return positive_ev_bets
