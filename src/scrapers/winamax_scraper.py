"""
winamax_scraper.py
==================
Scraper für Winamax-Quoten ohne Headless-Browser.

Strategie: Winamax bettet sämtliche Daten (Spiele, Wetten, Quoten, Ergebnisse)
als `window.PRELOADED_STATE` JSON direkt ins HTML ein. Ein einfacher HTTP-GET
auf die Sportseite liefert alles — kein Playwright, kein Selenium nötig.

URL-Schema:
  Alle Sportarten:  https://www.winamax.fr/paris-sportifs/sports
  Nur Fußball:      https://www.winamax.fr/paris-sportifs/sports/1
  Ein Wettbewerb:   https://www.winamax.fr/paris-sportifs/sports/1/competitions/96

Quoten-Format: ganzzahlig in "Cent-Basis" (600 = 6.00, 160 = 1.60)
"""

import json
import logging
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional

import requests

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Konstanten
# ---------------------------------------------------------------------------
BASE_URL = "https://www.winamax.fr/paris-sportifs/sports"

SPORT_IDS = {
    "football": 1,
    "basketball": 2,
    "baseball": 3,
    "hockey": 4,
    "tennis": 5,
    "handball": 6,
    "rugby": 12,
}

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "fr-FR,fr;q=0.9,en;q=0.8",
    "Referer": "https://www.winamax.fr/",
}


# ---------------------------------------------------------------------------
# Datenklassen
# ---------------------------------------------------------------------------
@dataclass
class Outcome:
    outcome_id: int
    bet_id: int
    label: str
    available: bool
    odds: Optional[float] = None  # als Dezimalquote (z.B. 6.00)


@dataclass
class Bet:
    bet_id: int
    label: str
    outcomes: list = field(default_factory=list)  # list[Outcome]


@dataclass
class Match:
    match_id: int
    title: str
    competitor1: str
    competitor2: str
    sport_id: int
    tournament_id: int
    category_id: int
    match_start: Optional[datetime]
    status: str
    main_bet: Optional[Bet] = None
    more_bets_count: int = 0
    is_outright: bool = False


# ---------------------------------------------------------------------------
# Scraper-Kernfunktionen
# ---------------------------------------------------------------------------

def _parse_winamax_odds(raw) -> float:
    """Gibt die Quote direkt als Float zurueck."""
    return float(raw)


def _extract_preloaded_state(html: str) -> dict:
    """Extrahiert das PRELOADED_STATE-JSON-Objekt aus dem HTML."""
    marker = "var PRELOADED_STATE = "
    start_idx = html.find(marker)
    if start_idx < 0:
        raise ValueError("PRELOADED_STATE nicht im HTML gefunden.")
    start_idx += len(marker)

    # Balancierte Klammern zaehlen, um das Ende des JSON zu finden
    depth = 0
    end_idx = start_idx
    for i, ch in enumerate(html[start_idx:], start_idx):
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                end_idx = i + 1
                break

    raw_json = html[start_idx:end_idx]
    return json.loads(raw_json)


def fetch_winamax_page(url: str, session: requests.Session) -> dict:
    """
    Laedt eine Winamax-Sportseite und gibt das geparste PRELOADED_STATE zurueck.
    Wirft requests.HTTPError bei HTTP-Fehlern.
    """
    resp = session.get(url, headers=HEADERS, timeout=20)
    resp.raise_for_status()
    return _extract_preloaded_state(resp.text)


def parse_matches(state: dict) -> list:
    """
    Wandelt den PRELOADED_STATE in eine Liste von Match-Objekten um,
    inklusive der Hauptwette (1X2) mit Quoten.
    """
    raw_matches: dict = state.get("matches", {})
    raw_bets: dict = state.get("bets", {})
    raw_outcomes: dict = state.get("outcomes", {})
    raw_odds: dict = state.get("odds", {})

    matches = []
    for mid_str, m in raw_matches.items():
        # Startzeit parsen
        match_start = None
        ts = m.get("matchStart")
        if ts:
            try:
                match_start = datetime.fromtimestamp(ts)
            except (TypeError, ValueError):
                pass

        # Hauptwette (1X2) zusammenbauen
        main_bet = None
        main_bet_id = m.get("mainBetId")
        if main_bet_id and str(main_bet_id) in raw_bets:
            bet_data = raw_bets[str(main_bet_id)]
            outcome_ids = bet_data.get("outcomes", [])
            outcomes = []
            for oid in outcome_ids:
                oid_str = str(oid)
                if oid_str in raw_outcomes:
                    od = raw_outcomes[oid_str]
                    raw_odd = raw_odds.get(oid_str)
                    outcomes.append(
                        Outcome(
                            outcome_id=oid,
                            bet_id=main_bet_id,
                            label=od.get("label", ""),
                            available=od.get("available", False),
                            odds=_parse_winamax_odds(raw_odd) if raw_odd else None,
                        )
                    )
            main_bet = Bet(
                bet_id=main_bet_id,
                label=bet_data.get("betName", ""),
                outcomes=outcomes,
            )

        matches.append(
            Match(
                match_id=int(mid_str),
                title=m.get("title", ""),
                competitor1=m.get("competitor1Name", ""),
                competitor2=m.get("competitor2Name", ""),
                sport_id=m.get("sportId", -1),
                tournament_id=m.get("tournamentId", -1),
                category_id=m.get("categoryId", -1),
                match_start=match_start,
                status=m.get("status", ""),
                main_bet=main_bet,
                more_bets_count=m.get("moreBets", 0),
                is_outright=m.get("isOutright", False),
            )
        )

    return matches


# ---------------------------------------------------------------------------
# Oeffentliche API
# ---------------------------------------------------------------------------

def get_football_matches(competition_id: Optional[int] = None) -> list:
    """
    Laedt alle Fussball-Spiele von Winamax.

    Args:
        competition_id: Optional - schraenkt auf einen Wettbewerb ein
                        (z.B. 96 fuer Premier League).
                        None = alle Wettbewerbe.

    Returns:
        Liste von Match-Objekten mit Quoten.
    """
    if competition_id:
        url = f"{BASE_URL}/{SPORT_IDS['football']}/competitions/{competition_id}"
    else:
        url = f"{BASE_URL}/{SPORT_IDS['football']}"

    with requests.Session() as session:
        logger.info("Lade Winamax-Seite: %s", url)
        state = fetch_winamax_page(url, session)
        matches = parse_matches(state)
        logger.info("Gefunden: %d Spiele", len(matches))
        return matches


def get_all_sports_overview() -> dict:
    """
    Gibt einen Ueberblick ueber alle verfuegbaren Sportarten und deren Match-Anzahl.
    """
    url = f"{BASE_URL}"
    with requests.Session() as session:
        state = fetch_winamax_page(url, session)

    sports = state.get("sports", {})
    return {
        v.get("sportName", sid): {
            "sport_id": sid,
            "main_matches": v.get("mainMatchCount", 0),
            "live_matches": v.get("liveMatchCount", 0),
        }
        for sid, v in sports.items()
    }


# ---------------------------------------------------------------------------
# CLI / Demo
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")

    print("=" * 70)
    print("WINAMAX SCRAPER - Demo")
    print("=" * 70)

    # 1) Sports-Uebersicht
    print("\n Sportarten-Uebersicht:")
    try:
        overview = get_all_sports_overview()
        for name, info in list(overview.items())[:10]:
            print(
                f"  {name:20s} | Main: {info['main_matches']:4d} | Live: {info['live_matches']:3d}"
            )
    except Exception as e:
        print(f"  Fehler: {e}")

    # 2) Fussball-Matches mit Quoten
    print("\n Fussball-Matches (erste 10):")
    try:
        matches = get_football_matches()
        print(f"  Insgesamt: {len(matches)} Spiele\n")
        for m in matches[:10]:
            start_str = (
                m.match_start.strftime("%d.%m %H:%M") if m.match_start else "?"
            )
            odds_str = "-"
            if m.main_bet and m.main_bet.outcomes:
                odds_vals = [
                    f"{o.label}: {o.odds:.2f}" if o.odds else f"{o.label}: n/v"
                    for o in m.main_bet.outcomes
                ]
                odds_str = " | ".join(odds_vals)
            print(f"  [{start_str}] {m.title}")
            print(f"           Quoten: {odds_str}")
            print(f"           Weitere Wetten: {m.more_bets_count}")
            print()
    except Exception as e:
        print(f"  Fehler: {e}")
        raise
