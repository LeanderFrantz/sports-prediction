from src.telegram_notifier import TelegramNotifier


def _bet(**overrides):
    bet = {
        "league": "EPL",
        "match": "Brighton & Hove Albion - Tottenham",
        "kickoff": "Fri, 12 Sep 20:30",
        "outcome": "Brighton & Hove Albion",
        "type": "Home (1)",
        "bookmaker": "Bet<365>",
        "bookmaker_odds": 2.1,
        "fair_odds": 1.95,
        "pinnacle_odds": 1.9,
        "ev_percent": 5.0,
        "kelly_suggested": 2.4,
    }
    bet.update(overrides)
    return bet


def test_format_bet_escapes_html_in_names():
    # Unescaped "&" / "<" make Telegram reject the whole message with a 400,
    # losing every bet in the chunk.
    text = TelegramNotifier.format_bet(_bet(), 1)

    assert "Brighton &amp; Hove Albion" in text
    assert "Bet&lt;365&gt;" in text
    assert "Brighton & Hove" not in text
    # Our own markup must survive escaping.
    assert "<b>Brighton &amp; Hove Albion - Tottenham</b>" in text


def test_format_headers_escape_sport_names():
    assert "&amp;" in TelegramNotifier.format_header(1, 3.0, ["foot&ball"])
    assert "&amp;" in TelegramNotifier.format_no_bets_message(3.0, ["foot&ball"])
