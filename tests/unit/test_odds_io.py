import json

from src.strategies.ev_logarithmic import load_odds, load_odds_from_csv, save_odds

MATCH = {
    "id": "abc123",
    "sport_key": "soccer_epl",
    "sport_title": "EPL",
    "commence_time": "2099-01-01T12:00:00Z",
    "home_team": "Brighton & Hove Albion",
    "away_team": "Tottenham",
    "bookmakers": [
        {
            "key": "pinnacle",
            "title": "Pinnacle",
            "markets": [
                {"key": "h2h", "outcomes": [{"name": "Tottenham", "price": 2.5}]}
            ],
        }
    ],
}


def test_save_and_load_round_trips_losslessly(tmp_path):
    path = tmp_path / "odds.jsonl"

    save_odds([MATCH, MATCH], str(path))

    assert load_odds(str(path)) == [MATCH, MATCH]


def test_save_creates_missing_directories(tmp_path):
    path = tmp_path / "nested" / "dir" / "odds.jsonl"

    save_odds([MATCH], str(path))

    assert path.exists()


def test_load_skips_malformed_lines(tmp_path):
    path = tmp_path / "odds.jsonl"
    path.write_text(json.dumps(MATCH) + "\nnot json\n\n" + json.dumps(MATCH) + "\n")

    assert load_odds(str(path)) == [MATCH, MATCH]


def test_legacy_csv_skips_rows_with_missing_bookmakers(tmp_path):
    # An empty bookmakers cell reads back as NaN and used to raise, losing the
    # whole file over one bad row.
    path = tmp_path / "odds.csv"
    path.write_text(
        "id,sport_key,sport_title,commence_time,home_team,away_team,bookmakers\n"
        "1,soccer_epl,EPL,2099-01-01T12:00:00Z,A,B,\n"
        "2,soccer_epl,EPL,2099-01-01T12:00:00Z,C,D,\"[{'key': 'pinnacle'}]\"\n"
    )

    loaded = load_odds(str(path))

    assert [m["home_team"] for m in loaded] == ["C"]


def test_load_dispatches_on_suffix(tmp_path):
    path = tmp_path / "odds.jsonl"
    save_odds([MATCH], str(path))

    assert load_odds(str(path))[0]["home_team"] == "Brighton & Hove Albion"
