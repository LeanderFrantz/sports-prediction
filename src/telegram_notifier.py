"""
telegram_notifier.py
====================
Sends formatted betting tips to one or more Telegram chats via the Bot API.

Configuration is read from environment variables:
  - TELEGRAM_BOT_TOKEN:  The HTTP API token for your bot.
  - TELEGRAM_CHAT_IDS:   Comma-separated list of numeric chat/group IDs
                         (e.g. "123456789,-100987654321").
"""

import logging
import os

import requests

logger = logging.getLogger(__name__)

# Telegram API limits
MAX_MESSAGE_LENGTH = 4096


class TelegramNotifier:
    """Sends formatted EV bet notifications to one or more Telegram chats."""

    def __init__(
        self,
        bot_token: str | None = None,
        chat_ids: list[str] | None = None,
    ) -> None:
        """
        Initialise the notifier.

        :param bot_token: Telegram bot token. Falls back to TELEGRAM_BOT_TOKEN env var.
        :param chat_ids: List of Telegram chat IDs. Falls back to TELEGRAM_CHAT_IDS
                         env var (comma-separated).
        :raises ValueError: If either value is missing.
        """
        self.bot_token = bot_token or os.environ.get("TELEGRAM_BOT_TOKEN")

        if chat_ids is not None:
            self.chat_ids = chat_ids
        else:
            raw = os.environ.get("TELEGRAM_CHAT_IDS", "")
            self.chat_ids = [cid.strip() for cid in raw.split(",") if cid.strip()]

        if not self.bot_token:
            raise ValueError(
                "Telegram bot token is required. Set TELEGRAM_BOT_TOKEN env var "
                "or pass bot_token explicitly."
            )
        if not self.chat_ids:
            raise ValueError(
                "At least one Telegram chat ID is required. Set TELEGRAM_CHAT_IDS "
                "env var (comma-separated) or pass chat_ids explicitly."
            )

        self.api_base = f"https://api.telegram.org/bot{self.bot_token}"

    # ------------------------------------------------------------------
    # Low-level sending
    # ------------------------------------------------------------------

    def _send_to_chat(self, chat_id: str, text: str, parse_mode: str = "HTML") -> dict:
        """
        Send a single text message to one chat.

        :param chat_id: Target chat ID.
        :param text: Message text (may contain HTML formatting).
        :param parse_mode: Telegram parse mode ("HTML" or "Markdown").
        :return: Telegram API response as dict.
        :raises requests.HTTPError: On non-2xx response.
        """
        url = f"{self.api_base}/sendMessage"
        payload = {
            "chat_id": chat_id,
            "text": text,
            "parse_mode": parse_mode,
            "disable_web_page_preview": True,
        }

        response = requests.post(url, json=payload, timeout=15)
        response.raise_for_status()

        result = response.json()
        if not result.get("ok"):
            logger.error("Telegram API error for chat %s: %s", chat_id, result)
        return result

    def send_message(
        self, text: str, parse_mode: str = "HTML"
    ) -> dict[str, list[dict]]:
        """
        Send a single text message to all configured chats.

        :param text: Message text.
        :param parse_mode: Telegram parse mode.
        :return: Dict mapping chat_id → list of API responses.
        """
        results: dict[str, list[dict]] = {}
        for chat_id in self.chat_ids:
            try:
                results[chat_id] = [self._send_to_chat(chat_id, text, parse_mode)]
            except Exception as e:
                logger.error("Failed to send to chat %s: %s", chat_id, e)
                results[chat_id] = [{"ok": False, "error": str(e)}]
        return results

    def send_long_message(
        self, text: str, parse_mode: str = "HTML"
    ) -> dict[str, list[dict]]:
        """
        Send a message to all configured chats, automatically splitting
        if it exceeds Telegram's 4096-character limit.

        Splits on newline boundaries to avoid breaking formatting.

        :param text: Full message text.
        :param parse_mode: Telegram parse mode.
        :return: Dict mapping chat_id → list of API responses.
        """
        if len(text) <= MAX_MESSAGE_LENGTH:
            return self.send_message(text, parse_mode)

        chunks = _split_text(text, MAX_MESSAGE_LENGTH)
        all_results: dict[str, list[dict]] = {cid: [] for cid in self.chat_ids}

        for chunk in chunks:
            for chat_id in self.chat_ids:
                try:
                    resp = self._send_to_chat(chat_id, chunk, parse_mode)
                    all_results[chat_id].append(resp)
                except Exception as e:
                    logger.error("Failed to send chunk to chat %s: %s", chat_id, e)
                    all_results[chat_id].append({"ok": False, "error": str(e)})

        return all_results

    # ------------------------------------------------------------------
    # Formatting
    # ------------------------------------------------------------------

    @staticmethod
    def format_bet(bet: dict, index: int, kelly_fraction: float = 0.25) -> str:
        """
        Format a single bet dict into an HTML string for Telegram.

        :param bet: Bet dictionary as returned by ev_service.
        :param index: 1-based display index.
        :param kelly_fraction: Kelly fraction used (for display only).
        :return: Formatted HTML string.
        """
        league = bet.get("league", "")
        emoji = _sport_emoji(league)

        return (
            f"🎯 <b>Positive EV Bet #{index}</b>\n"
            f"{emoji} {league}\n"
            f"<b>{bet['match']}</b>\n"
            f"Tip: {bet['outcome']} ({bet['type']})\n"
            f"📊 Bookmaker: {bet['bookmaker']} | "
            f"Odds: {bet['bookmaker_odds']} (Fair: {bet['fair_odds']})\n"
            f"📈 EV: <b>+{bet['ev_percent']}%</b>\n"
            f"💰 Kelly ({kelly_fraction:.0%}): {bet['kelly_suggested']}%"
        )

    @staticmethod
    def format_header(total_bets: int, threshold: float, sports: list[str]) -> str:
        """
        Format the header block for a notification batch.

        :param total_bets: Number of bets being sent.
        :param threshold: EV threshold used.
        :param sports: Sports that were scanned.
        :return: Formatted HTML header string.
        """
        sports_str = ", ".join(s.capitalize() for s in sports)
        return (
            f"🔔 <b>EV Alert — {total_bets} tip{'s' if total_bets != 1 else ''} found</b>\n"
            f"Sports: {sports_str}\n"
            f"Threshold: ≥ {threshold}% EV\n"
            f"{'━' * 28}"
        )

    @staticmethod
    def format_no_bets_message(threshold: float, sports: list[str]) -> str:
        """
        Format a "no bets found" message.

        :param threshold: EV threshold used.
        :param sports: Sports that were scanned.
        :return: Formatted HTML string.
        """
        sports_str = ", ".join(s.capitalize() for s in sports)
        return (
            f"📭 <b>No tips found</b>\n"
            f"Sports: {sports_str}\n"
            f"Threshold: ≥ {threshold}% EV\n\n"
            f"No positive-EV bets exceeded the threshold right now."
        )

    # ------------------------------------------------------------------
    # High-level: send a batch of bets
    # ------------------------------------------------------------------

    def notify_bets(
        self,
        bets: list[dict],
        threshold: float = 0.0,
        sports: list[str] | None = None,
        kelly_fraction: float = 0.25,
        send_if_empty: bool = True,
    ) -> dict[str, list[dict]]:
        """
        Format and send a batch of positive-EV bets to all configured Telegram chats.

        :param bets: List of bet dicts from ev_service.
        :param threshold: EV threshold (for display in header).
        :param sports: Sports scanned (for display in header).
        :param kelly_fraction: Kelly fraction used (for display).
        :param send_if_empty: Whether to send a message when no bets are found.
        :return: Dict mapping chat_id → list of Telegram API responses.
        """
        if sports is None:
            sports = ["football"]

        if not bets:
            if send_if_empty:
                msg = self.format_no_bets_message(threshold, sports)
                return self.send_long_message(msg)
            return {}

        # Build the full message
        parts = [self.format_header(len(bets), threshold, sports)]
        for idx, bet in enumerate(bets, 1):
            parts.append("")  # blank line separator
            parts.append(self.format_bet(bet, idx, kelly_fraction))

        full_message = "\n".join(parts)
        logger.info(
            "Sending %d bets to %d Telegram chat(s) (%d chars).",
            len(bets),
            len(self.chat_ids),
            len(full_message),
        )
        return self.send_long_message(full_message)


# ------------------------------------------------------------------
# Private helpers
# ------------------------------------------------------------------


def _split_text(text: str, max_length: int) -> list[str]:
    """
    Split text into chunks of at most *max_length* characters,
    preferring newline boundaries.
    """
    chunks: list[str] = []
    while len(text) > max_length:
        split_pos = text.rfind("\n", 0, max_length)
        if split_pos == -1:
            split_pos = max_length
        chunks.append(text[:split_pos])
        text = text[split_pos:].lstrip("\n")
    if text:
        chunks.append(text)
    return chunks


def _sport_emoji(league_name: str) -> str:
    """Return a contextual emoji based on the league/sport name."""
    name = league_name.lower()
    if any(
        kw in name
        for kw in (
            "soccer",
            "football",
            "bundesliga",
            "premier",
            "liga",
            "serie",
            "ligue",
            "efl",
            "mls",
            "eredivisie",
            "dfb",
            "uefa",
        )
    ):
        return "⚽"
    if any(kw in name for kw in ("basketball", "nba", "euroleague")):
        return "🏀"
    if any(kw in name for kw in ("tennis", "atp", "wta", "wimbledon", "open")):
        return "🎾"
    return "🏟️"
