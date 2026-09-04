# FPL Points Predictor

This project is a GitHub Pages-friendly prototype for predicting Fantasy Premier League points over any available future gameweek range and backtesting those predictions across finished historical windows. The frontend lets the user switch between the live predictor and an in-app backtest workspace that compares the `Official FPL` and `FPL-Core player stats` sources side by side. Both use direct ClubElo team ratings for fixture strength.

## Features

- Vanilla HTML, CSS, and JavaScript frontend
- Static frontend that runs directly on GitHub Pages
- Python build script using only the standard library
- Official FPL API integration
- Modular prediction pipeline:
  - Minutes prediction from six-match start/sub appearance probabilities and conditional minutes
  - Attacking returns from expected goals and expected assists
  - Clean sheet probability from team and opponent strengths
  - Threshold-based defensive contribution estimate for eligible outfield players
  - Goalkeeper historical save-points proxy and fixture-xGA goals-conceded deductions
  - Bonus point estimate from historical bonus and predicted involvement
  - Yellow card rate from recent history
  - Official-style total points combination
- Multi-gameweek horizon slider across the full available future schedule. Published one-to-six-GW windows load directly; longer selections are composed lazily from the published one-GW windows, avoiding a large static-file expansion.
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
  - Refresh source data twice daily at 00:30 and 12:30 UTC with GitHub Actions
  - Fetch current team ratings directly from ClubElo and validate one coherent, dated 20-team set
  - If ClubElo is unavailable, retain the latest complete Elo snapshot for no more than 30 days and mark the site amber
  - Refresh FPL-Core player statistics independently; a lagging secondary source does not block fresh Official FPL predictions
  - Retry a failed primary refresh up to three times before publishing
  - Leave the last verified site data in place if validation still fails
  - Refresh on the first local build or when the last prediction is over 6 hours old

## Files

- [index.html](/Users/craig/Documents/FPL-model/index.html)
- [app.js](/Users/craig/Documents/FPL-model/app.js)
- [generate_static_data.py](/Users/craig/Documents/FPL-model/generate_static_data.py)
- [data/static_predictions.json](/Users/craig/Documents/FPL-model/data/static_predictions.json)
- [data/static_backtest.json](/Users/craig/Documents/FPL-model/data/static_backtest.json)
- [server.py](/Users/craig/Documents/FPL-model/server.py)
- [.env](/Users/craig/Documents/FPL-model/.env)

## GitHub Pages deployment

This is now set up to run as static HTML on GitHub Pages.

1. Push the repository to GitHub.
2. Enable GitHub Pages for the branch that contains [index.html](/Users/craig/Documents/FPL-model/index.html).
3. Run the `Refresh Static FPL Data` workflow once, or wait for its twice-daily schedule.
4. Open your GitHub Pages URL. The page will read from `data/static_predictions.json`.

The refresh workflow is defined in [.github/workflows/refresh-static-data.yml](/Users/craig/Documents/FPL-model/.github/workflows/refresh-static-data.yml). It fetches fresh Official FPL data and a complete team-rating set directly from ClubElo, rebuilds the JSON files, validates the primary inputs, and commits the update back to the repository. ClubElo is attempted through its daily CSV endpoint and then its ranking page. If both routes fail, the generator may reuse one complete snapshot captured within the previous 30 days; it never mixes ratings from different dates. FPL-Core player statistics are refreshed and labelled separately, so an unavailable or lagging comparison source does not block fresh Official FPL predictions. Fallback or secondary-source warnings appear as amber in the site header. If primary validation still fails after three attempts, the workflow leaves the published data unchanged.

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

1. `Predictor._predict_minutes`: expected minutes are `P(start) * minutes when starting + P(sub appearance) * minutes when used as a substitute`, estimated from the prior six team fixtures. Early-season samples continue into the archived prior season. Minutes points are 2.0 when expected minutes reach `Predictor.FULL_MINUTES_POINTS_THRESHOLD` (currently 80); below that threshold they are `2 * expected minutes / 90`.
2. `Predictor._predict_goals`: player xG per 90 blends 75% long-term player history with 25% latest-six history, retains a team-position fallback for missing early evidence, applies a bounded and confidence-weighted finishing adjustment, and then applies the upcoming fixture's Elo attack factor. Individual forecasts are not capped to team xG; discrepancies above the audit threshold are flagged in player details.
3. `Predictor._predict_assists`: expected-assist rates use the same 75% long-term / 25% latest-six player blend before conversion and fixture adjustments.
4. `Predictor._predict_clean_sheet`: fixture-level clean-sheet probability comes from opponent xG in the team Elo fixture model.
5. `Predictor._predict_defensive_contribution`: goalkeepers receive zero; defenders use the 10-action threshold and midfielders/forwards the 12-action threshold, estimated from empirical threshold frequency in the six-fixture sample.
6. `Predictor._predict_goalkeeper_context`: goalkeeper save points temporarily use historical save points per 90, while goalkeeper/defender goals-conceded deductions use fixture xGA and the expected complete-pairs deduction under a Poisson model.
7. `Predictor._predict_bonus`: through GW6, missing six-fixture slots use the leakage-safe positional fallback; from GW7 onward, the rate blends 75% season-to-date and 25% latest six.
8. `Predictor._predict_yellows`: recent yellow card rate.

See [TODO.md](./TODO.md) for the deferred shots-on-target goalkeeper model, leakage-safe historical opponent normalisation, and NPxG/penalty decomposition.

## Backtest notes

The backtest pipeline now uses a shared engine across the CLI, the static data generator, and the local API.

1. Player features are built only from matches before the selected start gameweek.
2. Historical team strengths are reconstructed from prior finished results to avoid present-day team-strength leakage in backtests.
3. GW1 is excluded from the backtest range because the cache does not store a pre-season snapshot that would make that window defensible.

## Extending the prototype

- Add an Understat provider for xG and xA enrichment.
- Replace heuristics with a trained regression or probabilistic model.
- Add player price, ownership, and expected value views.
