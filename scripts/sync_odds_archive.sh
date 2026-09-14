#!/usr/bin/env bash
# Mirror the S3 odds archive to a local directory for notebook analysis.
#
# `aws s3 sync` is state-based: it compares both sides and transfers only what
# is missing. Missed runs therefore cost nothing — the next one catches up on
# everything at once — so this is safe to run on any schedule, by hand, or from
# the first cell of a notebook.
#
# Runs as the read-only `odds-sync` profile created by setup_odds_archive.sh,
# not your interactive session, so it keeps working while that is expired.
set -euo pipefail

PROFILE="${ODDS_SYNC_PROFILE:-odds-sync}"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

log() { echo "$(date -u '+%Y-%m-%dT%H:%M:%SZ') $*"; }

# launchd starts jobs with a minimal PATH that excludes Homebrew, so resolve
# the binary rather than relying on the shell finding it.
AWS_BIN="${AWS_BIN:-$(command -v aws 2>/dev/null || true)}"
if [ -z "$AWS_BIN" ]; then
  for candidate in /opt/homebrew/bin/aws /usr/local/bin/aws; do
    if [ -x "$candidate" ]; then AWS_BIN="$candidate"; break; fi
  done
fi
if [ -z "$AWS_BIN" ]; then
  log "ERROR: aws CLI not found (PATH=$PATH). Set AWS_BIN to its absolute path."
  exit 1
fi

cd "$REPO_ROOT"
if [ -f .env ]; then
  set -a
  # shellcheck disable=SC1091
  source .env
  set +a
fi

if [ -z "${ODDS_ARCHIVE_BUCKET:-}" ]; then
  log "ERROR: ODDS_ARCHIVE_BUCKET is not set (.env or environment)."
  exit 1
fi

# Default destination sits in the repo. ~/Documents is TCC-protected on macOS,
# so a background agent writing here can be denied with "Operation not
# permitted" -- set ODDS_ARCHIVE_LOCAL_DIR to somewhere unprotected (e.g.
# ~/Library/Application Support/sports-prediction) if that happens.
DEST="${ODDS_ARCHIVE_LOCAL_DIR:-$REPO_ROOT/data/raw}"

if ! mkdir -p "$DEST" 2>/dev/null; then
  log "ERROR: cannot create $DEST — if this is under ~/Documents, macOS TCC is"
  log "       most likely blocking it. Set ODDS_ARCHIVE_LOCAL_DIR elsewhere."
  exit 1
fi

log "Syncing s3://$ODDS_ARCHIVE_BUCKET/raw -> $DEST (profile: $PROFILE)"

# Environment credentials would silently outrank the profile, so clear them.
# --no-progress matters for more than tidiness: the progress display is
# written without a trailing newline, so it runs into the next "download:"
# line and defeats any attempt to count them.
if ! OUTPUT=$(env -u AWS_ACCESS_KEY_ID -u AWS_SECRET_ACCESS_KEY -u AWS_SESSION_TOKEN \
    "$AWS_BIN" --profile "$PROFILE" s3 sync --no-progress \
    "s3://$ODDS_ARCHIVE_BUCKET/raw" "$DEST" 2>&1); then
  log "ERROR: sync failed"
  log "$OUTPUT"
  exit 1
fi

DOWNLOADED=$(printf '%s\n' "$OUTPUT" | grep -c '^download:' || true)
TOTAL=$(find "$DEST" -name '*.json.gz' -type f | wc -l | tr -d ' ')
log "Done: $DOWNLOADED new object(s), $TOTAL snapshot(s) held locally."
