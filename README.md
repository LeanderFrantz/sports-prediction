# Sports Prediction Engine

A positive-EV sports betting engine. It fetches odds from ~60 bookmakers via
[The Odds API](https://the-odds-api.com/), treats Pinnacle's prices as the
market's best estimate of the truth, and flags bets elsewhere that pay more than
those probabilities justify.

Results go to stdout via the CLI, or to Telegram on a weekly schedule via AWS
Lambda.

## How it works

Bookmaker odds carry a margin ("vig"): the implied probabilities of a market sum
to more than 1. Pinnacle runs a thin margin and moves its lines on sharp money,
so its prices are the best cheap proxy for true probabilities — once the margin
is removed.

1. Take Pinnacle's h2h odds for a match and convert them to implied
   probabilities `p_i = 1 / odds_i`, which sum to more than 1.
2. Solve for the exponent `k` where `sum(p_i^k) = 1` by binary search. This is
   the logarithmic (power) de-vig method: it removes proportionally more margin
   from longshots than from favourites, which matches how books actually price.
   The result is the **fair probability** of each outcome.
3. For every other bookmaker, `EV = fair_probability × their_odds - 1`. Anything
   above the threshold is a bet where you are being paid more than the risk.
4. Size it with fractional Kelly: `(p·b - (1-p)) / b`, scaled down (0.25 by
   default) because the fair probabilities are an estimate, not gospel.

Two filters reflect what actually survives contact with reality:

- **Betting exchanges are excluded.** Betfair, Smarkets and Matchbook quote odds
  before commission, so measuring them against fair odds invents an edge that
  vanishes when you settle.
- **Longshots are capped** (`MAX_FAIR_ODDS`, default 5.0). Backtesting showed
  high-fair-odds bets underperforming their theoretical EV — see
  `notebooks/backtest_ev_strategy.ipynb`.

Matches that have already kicked off are skipped: the API returns in-play
events, and a live line compared against a same-snapshot Pinnacle line is not
this strategy.

## Setup

```bash
pip install -r requirements.txt
cp .env.example .env      # then fill in the real values
```

You need an API key from [the-odds-api.com](https://the-odds-api.com/). The
Telegram variables are only needed for the notification path.

## Usage

Fetch live odds, print the bets, and cache the raw response to
`data/<sport>_odds_data.jsonl`:

```bash
python3 -m src.strategies.ev_logarithmic --sport football
python3 -m src.strategies.ev_logarithmic 5 --sport football   # top 5 only
```

Re-analyse a saved file without spending API credits:

```bash
python3 -m src.strategies.ev_logarithmic --data-file data/football_odds_data.jsonl
```

By default the CLI applies the same EV threshold and longshot cap the Lambda
does, so what you see is what production would send. To widen it back out:

```bash
# every positive-EV bet, no longshot cap
python3 -m src.strategies.ev_logarithmic --ev-threshold 0 --max-fair-odds 0

# include negative-EV bets, for calibrating the model
python3 -m src.strategies.ev_logarithmic --ev-threshold -5
```

Other flags: `--kelly-fraction`, `--preferred-bookmakers`, `--timezone`,
`--telegram` (also send the displayed bets to Telegram). `--help` lists them all.

Run the Lambda handler locally — this fetches odds *and* sends a real Telegram
message:

```bash
python3 lambda_handler.py
```

## Configuration

Everything is environment variables, read from `.env` locally and from the
function's configuration on Lambda. See `.env.example`.

| Variable | Default | Purpose |
| --- | --- | --- |
| `ODDS_API_KEY` | — | The Odds API key. Required. |
| `TELEGRAM_BOT_TOKEN` | — | Bot token. Required for the Telegram path. |
| `TELEGRAM_CHAT_IDS` | — | Comma-separated chat/group IDs. |
| `SPORTS` | `football` | Comma-separated sports to scan. |
| `EV_THRESHOLD` | `3.0` | Minimum EV% to report. |
| `MAX_FAIR_ODDS` | `5.0` | Skip outcomes with fair odds at or above this. |
| `KELLY_FRACTION` | `0.25` | Fractional Kelly multiplier. |
| `MAX_BETS` | `25` | Cap on how many bets a notification carries. |
| `PREFERRED_BOOKMAKERS` | `tipico_de,winamax_de` | Books that win a tie, most preferred first. |
| `DISPLAY_TIMEZONE` | `Europe/Berlin` | Timezone for kickoff times. |
| `ODDS_REGIONS` | `eu,uk,us,au` | Regions to request. |
| `ODDS_BOOKMAKERS` | unset | Explicit bookmaker list; replaces `ODDS_REGIONS`. |

### A note on API credits

The Odds API bills each request as `markets × regions`. With four regions, a
13-league football scan costs **52 credits**. Narrowing `ODDS_REGIONS` cuts that
proportionally, and setting `ODDS_BOOKMAKERS` instead is billed as a single
region — about 13 credits — but returns only the books you name. Pinnacle is
added to that list automatically, since nothing can be priced without it. Each
run logs its estimated cost.

`PREFERRED_BOOKMAKERS` and `ODDS_BOOKMAKERS` take The Odds API's bookmaker
**keys**, not display titles: `tipico_de`, not `Tipico`. A key that never
appears in the fetched data is logged as a warning rather than failing.

## Deployment

```bash
./scripts/deploy_lambda.sh
```

Idempotent — re-run it after any code or `.env` change. It builds a zip
(`requests` and `tzdata` only; `pandas`, `python-dotenv` and `pytest` are
CLI/dev-only and never imported by the handler's call chain), then creates or
updates the IAM role, the Lambda function, a 30-day CloudWatch log retention
policy, and the weekly EventBridge schedule.

A plain zip keeps this in the AWS free tier and avoids ECR entirely.

Trigger a run by hand:

```bash
aws lambda invoke --function-name sports-prediction-ev --log-type Tail \
  --query LogResult --output text /tmp/response.json | base64 -d
```

## Tests

```bash
python3 -m pytest tests/
python3 -m pytest tests/unit/test_ev_core.py::test_solve_logarithmic_pure_fair_odds
```

## Layout

```
src/
  fetch_odds_api.py          The Odds API client: concurrent per-league fetches,
                             retries on transient failures, region/bookmaker filters
  telegram_notifier.py       Formats and sends bets, chunked under Telegram's 4096-char limit
  strategies/
    ev_core.py               The matching, de-vig, EV and Kelly logic. Pure functions:
                             no printing, no I/O, no environment. Change it here and
                             both entry points pick it up.
    ev_logarithmic.py        CLI entry point: fetches or loads odds, prints, caches to disk
    ev_service.py            Service entry point: returns structured data instead of printing
  scrapers/                  Legacy. Scrapes Winamax's site directly; not wired into the EV
                             pipeline, which gets Winamax through The Odds API anyway.
lambda_handler.py            AWS entry point: reads config, calls ev_service, notifies
scripts/deploy_lambda.sh     Build and deploy
data/                        Cached odds dumps (gitignored; regenerated by every CLI run)
notebooks/                   Backtests
```
