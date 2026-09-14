"""
odds_archive.py
===============
Archives each raw odds snapshot to S3, so the strategy can later be backtested
against odds that were actually obtainable — rather than against a third
party's collection, taken at a different moment from a different set of books.

The Lambda otherwise discards every scan: odds are fetched, compared, and
dropped. A snapshot cannot be backfilled at any price, which drives two rules
here:

- Capture runs *before* the EV analysis, so a bug in the strategy layer cannot
  cost you the snapshot.
- Capture never raises, so a broken archive cannot cost you the notification.

One gzipped object per scan (~68 KB for a full football scan), written to a
key that sorts by sport then date.
"""

import gzip
import json
import logging
import os
from datetime import datetime, timezone

logger = logging.getLogger(__name__)

# Bucket to archive into. Unset disables archiving entirely, which is what
# keeps local CLI runs and the test suite from needing AWS at all.
ARCHIVE_BUCKET_ENV = "ODDS_ARCHIVE_BUCKET"

ARCHIVE_PREFIX = "raw"

# Bumped if the envelope below changes shape, so a reader can tell which
# fields to expect from an object written years ago.
ENVELOPE_SCHEMA_VERSION = 1


def _s3_client():
    """
    Build an S3 client, importing boto3 lazily.

    boto3 is provided by the Lambda runtime and is deliberately absent from the
    deployment zip. Importing it at module scope would also make it a hard
    dependency of the CLI, which has no use for it.
    """
    import boto3

    return boto3.client("s3")


def build_key(sport: str, captured_at: datetime) -> str:
    """
    Build the S3 key for one snapshot.

    The `sport=` / `dt=` segments are Hive-style partitions: convention only
    today, but they are what would let Athena prune by date without a rewrite.
    """
    return (
        f"{ARCHIVE_PREFIX}/sport={sport}/dt={captured_at:%Y-%m-%d}/"
        f"scan={captured_at:%Y%m%dT%H%M%SZ}.json.gz"
    )


def build_envelope(
    odds_data: list[dict],
    sport: str,
    captured_at: datetime,
    regions: str | None = None,
    bookmakers: str | None = None,
    errors: list[dict] | None = None,
) -> dict:
    """
    Wrap a raw API response in the metadata a future backtest needs.

    The response itself goes in `events`, untouched: what the strategy derives
    from it is a function of the model and the thresholds, and both will
    change, so only the raw form is worth keeping.

    Everything else exists to make absence interpretable:

    - `filters` records which books were *asked for*. The response cannot
      distinguish "this book was not offering the bet" from "this book was
      never in the request", and the answer changes the moment ODDS_BOOKMAKERS
      replaces the region filter. Without this, a backtest spanning that
      change is silently wrong.
    - `errors` records leagues that failed to fetch, which is not the same as
      a league with no fixtures.
    """
    return {
        "schema": ENVELOPE_SCHEMA_VERSION,
        "captured_at": captured_at.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "sport": sport,
        "filters": {"regions": regions, "bookmakers": bookmakers},
        "errors": errors or [],
        "event_count": len(odds_data),
        "events": odds_data,
    }


def archive_snapshot(
    odds_data: list[dict],
    sport: str,
    regions: str | None = None,
    bookmakers: str | None = None,
    errors: list[dict] | None = None,
    bucket: str | None = None,
) -> str | None:
    """
    Archive one raw odds snapshot to S3.

    Writes even when the scan returned nothing: "a scan ran at this time and
    found no events" is itself a fact worth keeping, and `errors` is what
    separates that from "every league failed".

    :param odds_data: Raw match list as returned by fetch_odds_for_sport.
    :param sport: Sport key the scan was for.
    :param regions: Region filter actually sent, if any.
    :param bookmakers: Bookmaker filter actually sent, if any.
    :param errors: Per-league failures from the fetch.
    :param bucket: Destination bucket. Defaults to ODDS_ARCHIVE_BUCKET; unset
                    disables archiving.
    :return: The key written, or None if archiving was disabled or failed.
    """
    bucket = bucket if bucket is not None else os.environ.get(ARCHIVE_BUCKET_ENV, "").strip()
    if not bucket:
        logger.debug("%s is not set; skipping odds archive.", ARCHIVE_BUCKET_ENV)
        return None

    captured_at = datetime.now(timezone.utc)
    key = build_key(sport, captured_at)

    try:
        envelope = build_envelope(
            odds_data,
            sport,
            captured_at,
            regions=regions,
            bookmakers=bookmakers,
            errors=errors,
        )
        body = gzip.compress(json.dumps(envelope, ensure_ascii=False).encode("utf-8"))
        _s3_client().put_object(
            Bucket=bucket,
            Key=key,
            Body=body,
            # The key already ends .json.gz. Declaring the gzip as the content
            # type rather than as Content-Encoding keeps clients from helpfully
            # decompressing it behind our back on the way down.
            ContentType="application/gzip",
        )
    except Exception as e:
        # Never fatal: the notification is the product, the archive is a
        # by-product, and losing one must not cost the other.
        logger.error("Failed to archive %s odds to s3://%s/%s: %s", sport, bucket, key, e)
        return None

    logger.info(
        "Archived %d %s event(s) to s3://%s/%s (%.1f KB).",
        len(odds_data),
        sport,
        bucket,
        key,
        len(body) / 1024,
    )
    return key
