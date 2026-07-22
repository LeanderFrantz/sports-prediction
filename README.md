# Sports Prediction Engine

This project provides a flexible framework for analyzing sports betting odds, calculating metrics like Expected Value (EV), and identifying potentially profitable betting opportunities using various statistical strategies.

## Structure

- `data/`: Stores CSV files with historical or cached odds data.
- `src/`: Core functionality.
  - `fetch_odds_api.py`: Manages communication with "The Odds API" to fetch live odds.
  - `strategies/`: Implements various betting strategies.
    - `ev_logarithmic.py`: Calculates Expected Value (EV) by assessing true probabilities derived from Pinnacle odds using a logarithmic function model. This approach leverages the "wisdom of crowds" reflected in market-leading odds to identify inefficiencies in soft bookmakers' lines. It is designed to handle both 2-outcome (e.g., tennis, basketball) and 3-outcome (e.g., football) sports.
- `tests/`: Project tests (to be populated).

## Usage

To run the EV analysis using the logarithmic strategy:

```bash
python3 -m src.strategies.ev_logarithmic <limit> --sport <sport>
```

Example (Top 5 football bets):
```bash
python3 -m src.strategies.ev_logarithmic 5 --sport football
```

## Prerequisites

- `config.json` file in the root directory containing your API key:
  ```json
  {
    "api_key": "YOUR_API_KEY_HERE"
  }
  ```
- Python 3 with required dependencies (`requests`, `pandas`).
