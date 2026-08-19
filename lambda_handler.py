"""
lambda_handler.py
=================
AWS Lambda entry point for the EV Telegram notification service.

Fetches odds for configured sports, filters by EV threshold,
and sends matching tips to all configured Telegram chats.

All configuration is via environment variables:
  - ODDS_API_KEY        : The Odds API key
  - TELEGRAM_BOT_TOKEN  : Telegram bot HTTP API token
  - TELEGRAM_CHAT_IDS   : Comma-separated target chat/group IDs
  - EV_THRESHOLD        : Minimum EV% to notify (default: 3.0)
  - SPORTS              : Comma-separated sports to scan (default: football)
  - KELLY_FRACTION      : Kelly criterion fraction (default: 0.25)
  - MAX_BETS            : Max number of top bets to send (default: 25)
"""

import json
import logging
import os

from src.strategies.ev_service import get_all_positive_ev_bets
from src.telegram_notifier import TelegramNotifier

# Configure logging for Lambda (CloudWatch)
logger = logging.getLogger()
logger.setLevel(logging.INFO)

# Suppress noisy library loggers
logging.getLogger("urllib3").setLevel(logging.WARNING)
logging.getLogger("requests").setLevel(logging.WARNING)


def lambda_handler(event, context):
    """
    AWS Lambda handler.

    :param event: Lambda event (ignored — triggered by EventBridge cron).
    :param context: Lambda context object.
    :return: Summary dict for CloudWatch logs.
    """
    # --- Read configuration from environment ---
    ev_threshold = float(os.environ.get("EV_THRESHOLD", "3.0"))
    kelly_fraction = float(os.environ.get("KELLY_FRACTION", "0.25"))
    max_bets = int(os.environ.get("MAX_BETS", "25"))

    sports_raw = os.environ.get("SPORTS", "football")
    sports = [s.strip().lower() for s in sports_raw.split(",") if s.strip()]

    logger.info(
        "Starting EV scan — sports=%s, threshold=%.2f%%, kelly=%.2f, max_bets=%d",
        sports, ev_threshold, kelly_fraction, max_bets,
    )

    # --- Run EV analysis ---
    try:
        bets = get_all_positive_ev_bets(
            sports=sports,
            kelly_fraction=kelly_fraction,
            ev_threshold=ev_threshold,
        )
    except Exception as e:
        logger.error("EV analysis failed: %s", e, exc_info=True)
        return {
            "statusCode": 500,
            "body": json.dumps({"error": f"EV analysis failed: {e}"}),
        }

    total_found = len(bets)
    bets_to_send = bets[:max_bets]
    logger.info(
        "Found %d bets exceeding %.2f%% EV, sending top %d.",
        total_found, ev_threshold, len(bets_to_send),
    )

    # --- Send via Telegram ---
    try:
        notifier = TelegramNotifier()
        results = notifier.notify_bets(
            bets=bets_to_send,
            threshold=ev_threshold,
            sports=sports,
            kelly_fraction=kelly_fraction,
            send_if_empty=True,
        )

        # Count successes / failures per chat
        summary = {}
        for chat_id, responses in results.items():
            ok_count = sum(1 for r in responses if r.get("ok"))
            fail_count = len(responses) - ok_count
            summary[chat_id] = {"sent": ok_count, "failed": fail_count}

        logger.info("Telegram delivery summary: %s", summary)

    except Exception as e:
        logger.error("Telegram notification failed: %s", e, exc_info=True)
        return {
            "statusCode": 500,
            "body": json.dumps({"error": f"Telegram notification failed: {e}"}),
        }

    return {
        "statusCode": 200,
        "body": json.dumps({
            "bets_found": total_found,
            "bets_sent": len(bets_to_send),
            "sports": sports,
            "threshold": ev_threshold,
            "telegram": summary,
        }),
    }


# Allow running locally for testing: python lambda_handler.py
if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s - %(levelname)s - %(name)s - %(message)s",
    )
    result = lambda_handler({}, None)
    print(f"\nResult: {result}")
