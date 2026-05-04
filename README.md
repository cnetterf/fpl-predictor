# FPL Points Predictor

This project is a GitHub Pages-friendly prototype for predicting Fantasy Premier League points over the next 1 to 6 gameweeks and backtesting those predictions across finished historical windows. The frontend lets the user switch between the live predictor and an in-app backtest workspace that compares the `Official FPL` and `Elo Insights` sources side by side.

## Features

- Vanilla HTML, CSS, and JavaScript frontend
- Static frontend that runs directly on GitHub Pages
- Python build script using only the standard library
- Official FPL API integration
- Modular prediction pipeline:
  - Minutes prediction from recent matches, injury availability, and rotation heuristic
  - Attacking returns from expected goals and expected assists
  - Clean sheet probability from team and opponent strengths
  - Defensive contribution estimate for defensive players
  - Bonus point estimate from historical bonus and predicted involvement
  - Yellow card rate from recent history
  - Official-style total points combination
- Multi-gameweek horizon slider from 1 to 6
- Position filter
- Top picks highlighted
- In-app backtest tab with:
  - full finished-gameweek range slider
  - summary cards and MAE trend chart
  - player, team, position, and source drill-down tables
  - static GitHub Pages mode from `data/static_backtest.json`
  - optional local recompute for the selected backtest window via `server.py`
- Static data refresh automation:
  - Generate `data/static_predictions.json`
  - Generate `data/static_backtest.json`
  - Refresh source data every 12 hours with GitHub Actions
  - Refresh on the first local build or when the last prediction is over 6 hours old

## Files

- [index.html](/Users/craig/Documents/FPL model/index.html)
- [app.js](/Users/craig/Documents/FPL model/app.js)
- [generate_static_data.py](/Users/craig/Documents/FPL model/generate_static_data.py)
- [data/static_predictions.json](/Users/craig/Documents/FPL model/data/static_predictions.json)
- [data/static_backtest.json](/Users/craig/Documents/FPL model/data/static_backtest.json)
- [server.py](/Users/craig/Documents/FPL model/server.py)
- [.env](/Users/craig/Documents/FPL model/.env)

## GitHub Pages deployment

This is now set up to run as static HTML on GitHub Pages.

1. Push the repository to GitHub.
2. Enable GitHub Pages for the branch that contains [index.html](/Users/craig/Documents/FPL model/index.html).
3. Run the `Refresh Static FPL Data` workflow once, or wait for its 12-hour schedule.
4. Open your GitHub Pages URL. The page will read from `data/static_predictions.json`.

The refresh workflow is defined in [.github/workflows/refresh-static-data.yml](/Users/craig/Documents/FPL model/.github/workflows/refresh-static-data.yml). It fetches fresh FPL data, rebuilds the JSON file, and commits the update back to the repository.

## Build locally

1. Ensure Python 3.10+ is installed.
2. Generate fresh static data:

```bash
python3 generate_static_data.py
```

3. Commit the updated `data/static_predictions.json` and `data/static_backtest.json`.
4. Open the GitHub Pages site, or serve the directory with any static file server.

## Optional local API mode

The local API server still exists for development:

```bash
python3 server.py
```

It exposes:

- `GET /api/predictions?horizon=3&position=ALL`
- `GET /api/backtest`
- `GET /api/backtest?start_gw=2&end_gw=6`
- `GET /api/health`

## Environment variables

The project is structured to keep secrets out of the frontend. The default data sources do not require API keys, but the backend uses `.env` so keys can be added later if you extend the data providers.

Current variables:

- `FPL_API_BASE=https://fantasy.premierleague.com/api`
- `UNDERSTAT_ENABLED=false`
- `PORT=8000`

`.env` is ignored by git via `.gitignore`.

## Prediction model notes

The code is organized so each scoring component can be upgraded independently:

1. `Predictor._predict_minutes`: recent 3 to 5 match rolling minutes, availability discount, and start-rate rotation factor.
2. `Predictor._predict_goals`: expected goals rate with simple finishing adjustment and fixture strength factor.
3. `Predictor._predict_assists`: expected assists rate with simple conversion adjustment and fixture strength factor.
4. `Predictor._predict_clean_sheet`: fixture-level clean sheet probability from FPL team strengths.
5. `Predictor._predict_defensive_contribution`: defensive recovery proxy for goalkeeper and defender-style contribution.
6. `Predictor._predict_bonus`: historical bonus blended with attacking and defensive involvement.
7. `Predictor._predict_yellows`: recent yellow card rate.
8. `Predictor._predict_sub_60_penalty`: applies the under-60 deduction when predicted minutes fall below 60.

## Backtest notes

The backtest pipeline now uses a shared engine across the CLI, the static data generator, and the local API.

1. Player features are built only from matches before the selected start gameweek.
2. Historical team strengths are reconstructed from prior finished results to avoid present-day team-strength leakage in backtests.
3. GW1 is excluded from the backtest range because the cache does not store a pre-season snapshot that would make that window defensible.

## Extending the prototype

- Add an Understat provider for xG and xA enrichment.
- Replace heuristics with a trained regression or probabilistic model.
- Add player price, ownership, and expected value views.
