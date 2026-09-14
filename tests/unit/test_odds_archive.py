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
