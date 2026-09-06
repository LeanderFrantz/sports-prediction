#!/usr/bin/env bash
# Build the Lambda deployment zip and create/update everything it needs in
# AWS: IAM execution role, the Lambda function itself, and the weekly
# EventBridge schedule that triggers it. Idempotent — re-run after any
# code or .env change to push updates.
#
# Requires: AWS CLI installed and configured (aws configure / SSO) with
# permissions to manage IAM roles, Lambda functions, and EventBridge rules.
set -euo pipefail

# --- Configuration (edit as needed) ---
FUNCTION_NAME="sports-prediction-ev"
ROLE_NAME="sports-prediction-ev-lambda-role"
RUNTIME="python3.12"
HANDLER="lambda_handler.lambda_handler"
# Generous relative to a normal run (leagues are fetched concurrently), but it
# has to cover a slow league plus retries without killing the scan mid-flight.
TIMEOUT=120
MEMORY_SIZE=256
RULE_NAME="sports-prediction-ev-weekly"
# Fridays at 17:00 CEST. EventBridge cron runs in UTC and isn't DST-aware,
# so this is a fixed 15:00 UTC — effectively 16:00 CET once winter time
# kicks in (1h drift twice a year, not worth the extra setup to avoid).
SCHEDULE_EXPRESSION="cron(0 15 ? * FRI *)"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
BUILD_DIR="$REPO_ROOT/build"
ZIP_FILE="$REPO_ROOT/function.zip"

command -v aws >/dev/null || { echo "AWS CLI not found. Install it first." >&2; exit 1; }

# --- Load Lambda runtime config from .env ---
cd "$REPO_ROOT"
if [ ! -f .env ]; then
  echo "Missing .env — copy .env.example and fill in real values first." >&2
  exit 1
fi
set -a
source .env
set +a

for var in ODDS_API_KEY TELEGRAM_BOT_TOKEN TELEGRAM_CHAT_IDS; do
  if [ -z "${!var:-}" ]; then
    echo "Missing required $var in .env" >&2
    exit 1
  fi
done
export EV_THRESHOLD="${EV_THRESHOLD:-1.0}"
export SPORTS="${SPORTS:-football}"
export KELLY_FRACTION="${KELLY_FRACTION:-0.25}"
export MAX_BETS="${MAX_BETS:-25}"
export MAX_FAIR_ODDS="${MAX_FAIR_ODDS:-5.0}"
# Empty is valid: the code falls back to its own default list.
export PREFERRED_BOOKMAKERS="${PREFERRED_BOOKMAKERS:-}"

# --- 1) Build the deployment zip ---
# Only `requests` is needed at runtime — pandas/python-dotenv/pytest are
# CLI/dev-only and never imported by lambda_handler.py's call chain. `tzdata`
# is vendored because the Lambda runtime has no system zoneinfo, and without it
# kickoff times fall back to UTC.
echo "==> Building deployment package..."
rm -rf "$BUILD_DIR" "$ZIP_FILE"
mkdir -p "$BUILD_DIR"
pip install --quiet requests tzdata -t "$BUILD_DIR"
cp lambda_handler.py "$BUILD_DIR/"
cp -r src "$BUILD_DIR/"
find "$BUILD_DIR" -name "__pycache__" -type d -exec rm -rf {} + 2>/dev/null || true
(cd "$BUILD_DIR" && zip -qr "$ZIP_FILE" .)
echo "    Built $(basename "$ZIP_FILE") ($(du -h "$ZIP_FILE" | cut -f1))"

# --- 2) Ensure the IAM execution role exists ---
echo "==> Checking IAM role..."
if ! aws iam get-role --role-name "$ROLE_NAME" >/dev/null 2>&1; then
  aws iam create-role \
    --role-name "$ROLE_NAME" \
    --assume-role-policy-document '{
      "Version": "2012-10-17",
      "Statement": [{
        "Effect": "Allow",
        "Principal": {"Service": "lambda.amazonaws.com"},
        "Action": "sts:AssumeRole"
      }]
    }' >/dev/null
  aws iam attach-role-policy \
    --role-name "$ROLE_NAME" \
    --policy-arn arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole
  echo "    Created $ROLE_NAME, waiting for IAM propagation..."
  sleep 10
else
  echo "    $ROLE_NAME already exists."
fi
ROLE_ARN=$(aws iam get-role --role-name "$ROLE_NAME" --query 'Role.Arn' --output text)

# Serialise the environment as JSON rather than the CLI's `Variables={k=v,...}`
# shorthand: that parser splits on commas, so a comma-separated TELEGRAM_CHAT_IDS
# (or any value containing a comma or `=`) makes the deploy fail outright.
# mktemp creates the file 0600 — it holds the API key and bot token.
ENV_FILE="$(mktemp -t sports-prediction-lambda-env)"
trap 'rm -f "$ENV_FILE"' EXIT
python3 - "$ENV_FILE" <<'PYEOF'
import json
import os
import sys

keys = [
    "ODDS_API_KEY",
    "TELEGRAM_BOT_TOKEN",
    "TELEGRAM_CHAT_IDS",
    "EV_THRESHOLD",
    "SPORTS",
    "KELLY_FRACTION",
    "MAX_BETS",
    "MAX_FAIR_ODDS",
    "PREFERRED_BOOKMAKERS",
]
with open(sys.argv[1], "w", encoding="utf-8") as fh:
    json.dump({"Variables": {k: os.environ[k] for k in keys}}, fh)
PYEOF
ENV_VARS="file://$ENV_FILE"

# --- 3) Create or update the Lambda function ---
echo "==> Deploying Lambda function..."
if aws lambda get-function --function-name "$FUNCTION_NAME" >/dev/null 2>&1; then
  aws lambda update-function-code \
    --function-name "$FUNCTION_NAME" \
    --zip-file "fileb://$ZIP_FILE" >/dev/null
  aws lambda wait function-updated --function-name "$FUNCTION_NAME"
  aws lambda update-function-configuration \
    --function-name "$FUNCTION_NAME" \
    --timeout "$TIMEOUT" \
    --memory-size "$MEMORY_SIZE" \
    --environment "$ENV_VARS" >/dev/null
  echo "    Updated existing function."
else
  aws lambda create-function \
    --function-name "$FUNCTION_NAME" \
    --runtime "$RUNTIME" \
    --role "$ROLE_ARN" \
    --handler "$HANDLER" \
    --zip-file "fileb://$ZIP_FILE" \
    --timeout "$TIMEOUT" \
    --memory-size "$MEMORY_SIZE" \
    --environment "$ENV_VARS" >/dev/null
  echo "    Created new function."
fi
FUNCTION_ARN=$(aws lambda get-function --function-name "$FUNCTION_NAME" --query 'Configuration.FunctionArn' --output text)

# --- 4) Cap CloudWatch Logs retention ---
# Lambda's log group defaults to "never expire" retention, so weekly-run logs
# would otherwise accumulate forever. At this volume the cost either way is
# negligible, but capping it is good hygiene. The log group is only
# auto-created on first invocation, so create it explicitly here (harmless
# no-op if it already exists) before setting retention.
echo "==> Capping CloudWatch Logs retention to 30 days..."
LOG_GROUP="/aws/lambda/$FUNCTION_NAME"
aws logs create-log-group --log-group-name "$LOG_GROUP" >/dev/null 2>&1 || true
aws logs put-retention-policy --log-group-name "$LOG_GROUP" --retention-in-days 30

# --- 5) Ensure the weekly EventBridge schedule exists and targets the function ---
echo "==> Ensuring weekly schedule..."
aws events put-rule \
  --name "$RULE_NAME" \
  --schedule-expression "$SCHEDULE_EXPRESSION" \
  --state ENABLED >/dev/null
RULE_ARN=$(aws events describe-rule --name "$RULE_NAME" --query 'Arn' --output text)

# Fails harmlessly if the permission already exists from a previous run.
aws lambda add-permission \
  --function-name "$FUNCTION_NAME" \
  --statement-id "${RULE_NAME}-trigger" \
  --action "lambda:InvokeFunction" \
  --principal events.amazonaws.com \
  --source-arn "$RULE_ARN" >/dev/null 2>&1 || true

aws events put-targets \
  --rule "$RULE_NAME" \
  --targets "Id=1,Arn=$FUNCTION_ARN" >/dev/null

# --- 6) Clean up local build artifacts ---
# The zip is already uploaded to Lambda; no need to keep the local copy or
# the extracted build/ directory around between runs.
rm -rf "$BUILD_DIR" "$ZIP_FILE"

echo "==> Done."
echo "    Function:  $FUNCTION_ARN"
echo "    Schedule:  $SCHEDULE_EXPRESSION ($RULE_NAME)"
