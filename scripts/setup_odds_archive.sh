#!/usr/bin/env bash
# One-time setup of the read-only credential the local odds-archive sync uses.
#
# The Lambda writes to the archive with its own execution role, which never
# expires. This script creates the other half: a dedicated IAM user that can
# only *read* that one bucket, with a long-lived access key stored as a named
# profile. That is what lets the sync run unattended, without depending on an
# interactive `aws login` session that expires after a few hours.
#
# The secret is never printed. It goes from the API response straight into
# `aws configure set`, so it cannot land in your scrollback or a transcript.
#
# Run this once, after ./scripts/deploy_lambda.sh has created the bucket.
set -euo pipefail

USER_NAME="odds-archive-sync"
PROFILE="odds-sync"
POLICY_NAME="read-odds-archive"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

command -v aws >/dev/null || { echo "AWS CLI not found. Install it first." >&2; exit 1; }

cd "$REPO_ROOT"
[ -f .env ] || { echo "Missing .env — copy .env.example first." >&2; exit 1; }
set -a
source .env
set +a

if [ -z "${ODDS_ARCHIVE_BUCKET:-}" ]; then
  echo "ODDS_ARCHIVE_BUCKET is not set in .env — nothing to grant access to." >&2
  exit 1
fi

REGION="$(aws configure get region || echo us-east-1)"

# --- 1) The IAM user ---
echo "==> Checking IAM user $USER_NAME..."
if aws iam get-user --user-name "$USER_NAME" >/dev/null 2>&1; then
  echo "    Already exists."
else
  aws iam create-user --user-name "$USER_NAME" >/dev/null
  echo "    Created."
fi

# --- 2) Read-only on this one bucket, and nothing else ---
# put-user-policy overwrites by name, so re-running re-asserts the policy
# rather than accumulating copies. ListBucket is on the bucket itself,
# GetObject on its contents -- `aws s3 sync` needs both.
echo "==> Granting read-only access to $ODDS_ARCHIVE_BUCKET..."
aws iam put-user-policy \
  --user-name "$USER_NAME" \
  --policy-name "$POLICY_NAME" \
  --policy-document "{
    \"Version\": \"2012-10-17\",
    \"Statement\": [
      {
        \"Effect\": \"Allow\",
        \"Action\": \"s3:ListBucket\",
        \"Resource\": \"arn:aws:s3:::$ODDS_ARCHIVE_BUCKET\"
      },
      {
        \"Effect\": \"Allow\",
        \"Action\": \"s3:GetObject\",
        \"Resource\": \"arn:aws:s3:::$ODDS_ARCHIVE_BUCKET/*\"
      }
    ]
  }"

# --- 3) The access key ---
# IAM allows at most two keys per user, and a forgotten spare is a credential
# you are not tracking. Refuse rather than quietly minting another.
EXISTING="$(aws iam list-access-keys --user-name "$USER_NAME" \
  --query 'AccessKeyMetadata[].AccessKeyId' --output text)"
if [ -n "$EXISTING" ]; then
  echo
  echo "    $USER_NAME already has an access key: $EXISTING"
  echo "    Not creating a second one. If the local profile is missing or you"
  echo "    want to rotate, delete the old key first:"
  echo
  echo "      aws iam delete-access-key --user-name $USER_NAME --access-key-id $EXISTING"
  echo "      $0"
  echo
  exit 0
fi

echo "==> Creating access key and storing it as profile '$PROFILE'..."
# Captured into a variable and piped into `aws configure set` -- never echoed.
KEY_JSON="$(aws iam create-access-key --user-name "$USER_NAME" \
  --query 'AccessKey.[AccessKeyId,SecretAccessKey]' --output text)"
aws configure set --profile "$PROFILE" aws_access_key_id "$(echo "$KEY_JSON" | cut -f1)"
aws configure set --profile "$PROFILE" aws_secret_access_key "$(echo "$KEY_JSON" | cut -f2)"
aws configure set --profile "$PROFILE" region "$REGION"
unset KEY_JSON
chmod 600 "$HOME/.aws/credentials"
echo "    Stored in ~/.aws/credentials (0600)."

# --- 4) Verify, allowing for IAM propagation ---
# A freshly minted key is not immediately valid everywhere; a few seconds of
# InvalidAccessKeyId is normal and is not a misconfiguration.
echo "==> Verifying the new credential can read the bucket..."
for attempt in 1 2 3 4 5 6; do
  if env -u AWS_ACCESS_KEY_ID -u AWS_SECRET_ACCESS_KEY -u AWS_SESSION_TOKEN \
      aws --profile "$PROFILE" s3 ls "s3://$ODDS_ARCHIVE_BUCKET/" >/dev/null 2>&1; then
    echo "    OK."
    echo
    echo "Done. The sync now runs without an interactive login:"
    echo "    ./scripts/sync_odds_archive.sh"
    exit 0
  fi
  sleep 5
done

echo "    Still failing after 30s. The key is stored; try the sync by hand:" >&2
echo "      aws --profile $PROFILE s3 ls s3://$ODDS_ARCHIVE_BUCKET/" >&2
exit 1
