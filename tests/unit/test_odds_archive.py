import gzip
import json
from datetime import datetime, timezone

from src import odds_archive
from src.odds_archive import archive_snapshot, build_envelope, build_key

CAPTURED_AT = datetime(2026, 9, 18, 15, 0, 0, tzinfo=timezone.utc)
EVENTS = [{"id": "abc", "home_team": "A", "away_team": "B", "bookmakers": []}]


class _FakeS3:
    """Records put_object calls instead of talking to AWS."""

    def __init__(self, raises=None):
        self.raises = raises
        self.calls = []

    def put_object(self, **kwargs):
        if self.raises:
            raise self.raises
        self.calls.append(kwargs)
        return {}


def _patch_s3(monkeypatch, client):
    monkeypatch.setattr(odds_archive, "_s3_client", lambda: client)
    return client


def test_key_partitions_by_sport_and_date():
    assert build_key("football", CAPTURED_AT) == (
        "raw/sport=football/dt=2026-09-18/scan=20260918T150000Z.json.gz"
    )


def test_envelope_keeps_the_response_untouched():
    # What the strategy derives is a function of the model and thresholds, and
    # both will change. Only the raw form is worth keeping.
    envelope = build_envelope(EVENTS, "football", CAPTURED_AT)

    assert envelope["events"] == EVENTS
    assert envelope["event_count"] == 1
    assert envelope["captured_at"] == "2026-09-18T15:00:00Z"


def test_envelope_records_which_books_were_asked_for():
    # The response cannot distinguish "this book was not offering the bet"
    # from "this book was never in the request", and the answer changes the
    # day ODDS_BOOKMAKERS replaces the region filter.
    regions_scan = build_envelope(EVENTS, "football", CAPTURED_AT, regions="eu,uk")
    books_scan = build_envelope(
        EVENTS, "football", CAPTURED_AT, bookmakers="pinnacle,tipico_de"
    )

    assert regions_scan["filters"] == {"regions": "eu,uk", "bookmakers": None}
    assert books_scan["filters"] == {
        "regions": None,
        "bookmakers": "pinnacle,tipico_de",
    }


def test_envelope_records_failed_leagues():
    # A league that failed to fetch is not a league with no fixtures.
    errors = [{"league": "soccer_epl", "error": "429 Too Many Requests"}]
    envelope = build_envelope(EVENTS, "football", CAPTURED_AT, errors=errors)

    assert envelope["errors"] == errors


def test_archive_snapshot_writes_gzipped_json(monkeypatch):
    s3 = _patch_s3(monkeypatch, _FakeS3())

    key = archive_snapshot(EVENTS, "football", regions="eu", bucket="a-bucket")

    assert key.startswith("raw/sport=football/dt=")
    (call,) = s3.calls
    assert call["Bucket"] == "a-bucket"
    assert call["Key"] == key
    assert call["ContentType"] == "application/gzip"

    written = json.loads(gzip.decompress(call["Body"]).decode("utf-8"))
    assert written["events"] == EVENTS
    assert written["filters"]["regions"] == "eu"


def test_archive_snapshot_is_disabled_without_a_bucket(monkeypatch):
    monkeypatch.delenv(odds_archive.ARCHIVE_BUCKET_ENV, raising=False)
    s3 = _patch_s3(monkeypatch, _FakeS3())

    assert archive_snapshot(EVENTS, "football") is None
    assert s3.calls == []


def test_archive_snapshot_reads_the_bucket_from_the_environment(monkeypatch):
    monkeypatch.setenv(odds_archive.ARCHIVE_BUCKET_ENV, "from-env")
    s3 = _patch_s3(monkeypatch, _FakeS3())

    archive_snapshot(EVENTS, "football")

    assert s3.calls[0]["Bucket"] == "from-env"


def test_archive_failure_never_propagates(monkeypatch, caplog):
    # The notification is the product and the archive is a by-product; losing
    # one must not cost the other.
    _patch_s3(monkeypatch, _FakeS3(raises=RuntimeError("AccessDenied")))

    with caplog.at_level("ERROR"):
        assert archive_snapshot(EVENTS, "football", bucket="a-bucket") is None

    assert "Failed to archive" in caplog.text


def test_missing_boto3_never_propagates(monkeypatch, caplog):
    # boto3 comes from the Lambda runtime and is absent from the zip and from
    # a plain CLI install, so an ImportError here is a realistic path.
    def _no_boto3():
        raise ImportError("No module named 'boto3'")

    monkeypatch.setattr(odds_archive, "_s3_client", _no_boto3)

    with caplog.at_level("ERROR"):
        assert archive_snapshot(EVENTS, "football", bucket="a-bucket") is None


def test_an_empty_scan_is_still_archived(monkeypatch):
    # "A scan ran and found nothing" is a fact worth keeping; errors is what
    # separates it from "every league failed".
    s3 = _patch_s3(monkeypatch, _FakeS3())

    archive_snapshot([], "football", errors=[{"league": "x", "error": "boom"}],
                     bucket="a-bucket")

    written = json.loads(gzip.decompress(s3.calls[0]["Body"]).decode("utf-8"))
    assert written["event_count"] == 0
    assert written["errors"][0]["league"] == "x"


def _write_snapshot(tmp_path, name, events, **envelope_kwargs):
    path = tmp_path / name
    path.parent.mkdir(parents=True, exist_ok=True)
    envelope = build_envelope(events, "football", CAPTURED_AT, **envelope_kwargs)
    with gzip.open(path, "wt", encoding="utf-8") as fh:
        json.dump(envelope, fh)
    return path


def _event(home, away, books):
    return {
        "id": f"{home}-{away}",
        "sport_title": "Bundesliga",
        "sport_key": "soccer_germany_bundesliga",
        "commence_time": "2026-09-18T18:30:00Z",
        "home_team": home,
        "away_team": away,
        "bookmakers": [
            {
                "key": key,
                "last_update": "2026-09-18T14:59:00Z",
                "markets": [
                    {
                        "key": "h2h",
                        "outcomes": [
                            {"name": home, "price": price},
                            {"name": away, "price": 3.0},
                        ],
                    }
                ],
            }
            for key, price in books
        ],
    }


def test_iter_quotes_flattens_to_one_row_per_price(tmp_path):
    _write_snapshot(tmp_path, "sport=football/dt=2026-09-18/scan=a.json.gz",
                    [_event("A", "B", [("tipico_de", 2.1), ("bet365", 2.05)])])

    quotes = list(odds_archive.iter_quotes(tmp_path))

    assert len(quotes) == 4  # 2 books x 2 outcomes
    assert {q["bookmaker"] for q in quotes} == {"tipico_de", "bet365"}
    assert quotes[0]["league"] == "Bundesliga"


def test_iter_quotes_carries_the_filters_onto_every_row(tmp_path):
    # Flattening throws the envelope away, so a backtest spanning a change of
    # filter would otherwise lose the one field that makes absence readable.
    _write_snapshot(tmp_path, "sport=football/dt=2026-09-18/scan=a.json.gz",
                    [_event("A", "B", [("tipico_de", 2.1)])], regions="eu,uk")

    quotes = list(odds_archive.iter_quotes(tmp_path))

    assert all(q["filter_regions"] == "eu,uk" for q in quotes)
    assert all(q["filter_bookmakers"] is None for q in quotes)


def test_iter_quotes_reads_every_snapshot_in_order(tmp_path):
    _write_snapshot(tmp_path, "sport=football/dt=2026-09-18/scan=a.json.gz",
                    [_event("A", "B", [("tipico_de", 2.1)])])
    _write_snapshot(tmp_path, "sport=football/dt=2026-09-19/scan=b.json.gz",
                    [_event("C", "D", [("tipico_de", 1.8)])])

    assert [q["event_id"] for q in odds_archive.iter_quotes(tmp_path)] == ["A-B", "A-B", "C-D", "C-D"]


def test_iter_quotes_skips_non_h2h_markets(tmp_path):
    event = _event("A", "B", [("tipico_de", 2.1)])
    event["bookmakers"][0]["markets"].append(
        {"key": "totals", "outcomes": [{"name": "Over", "price": 1.9}]}
    )
    _write_snapshot(tmp_path, "sport=football/dt=2026-09-18/scan=a.json.gz", [event])

    assert {q["outcome"] for q in odds_archive.iter_quotes(tmp_path)} == {"A", "B"}


def test_iter_quotes_on_an_empty_archive(tmp_path):
    assert list(odds_archive.iter_quotes(tmp_path)) == []


def test_load_snapshots_is_one_row_per_market(tmp_path):
    # A single outcome is not a unit you can do anything with: de-vigging
    # needs every price in the market at once.
    _write_snapshot(tmp_path, "sport=football/dt=2026-09-18/scan=a.json.gz",
                    [_event("A", "B", [("tipico_de", 2.1), ("bet365", 2.05)])])

    frame = odds_archive.load_snapshots(tmp_path)

    assert len(frame) == 2  # two books, not four outcomes
    assert frame["home_price"].tolist() == [2.1, 2.05]
    assert frame["away_price"].tolist() == [3.0, 3.0]
    # captured_at 15:00:00, last_update 14:59:00 -> one minute stale.
    assert frame["staleness"].iloc[0].total_seconds() == 60


def test_iter_markets_pairs_the_draw_with_home_and_away(tmp_path):
    event = _event("A", "B", [("tipico_de", 2.1)])
    event["bookmakers"][0]["markets"][0]["outcomes"].append(
        {"name": "Unentschieden", "price": 3.4}
    )
    _write_snapshot(tmp_path, "sport=football/dt=2026-09-18/scan=a.json.gz", [event])

    (row,) = list(odds_archive.iter_markets(tmp_path))

    # Matched against ev_core's labels, so a German-language draw resolves.
    assert (row["home_price"], row["draw_price"], row["away_price"]) == (2.1, 3.4, 3.0)
    assert row["n_outcomes"] == 3


def test_iter_markets_leaves_the_draw_empty_on_a_2_way_market(tmp_path):
    # Real: a book quoted h2h with no draw on a football match 41 other books
    # priced 3-way. ev_core skips it; the reader should show it, not hide it.
    _write_snapshot(tmp_path, "sport=football/dt=2026-09-18/scan=a.json.gz",
                    [_event("A", "B", [("tipico_de", 2.1)])])

    (row,) = list(odds_archive.iter_markets(tmp_path))

    assert row["draw_price"] is None
    assert row["n_outcomes"] == 2


def test_pandas_is_not_imported_at_module_scope():
    # odds_archive is on the Lambda's import path and pandas is not in the
    # deployment zip, so a module-scope import would break every scan.
    source = (odds_archive.__file__)
    with open(source, encoding="utf-8") as fh:
        top_level = [
            line for line in fh
            if line.startswith("import pandas") or line.startswith("from pandas")
        ]
    assert top_level == []
