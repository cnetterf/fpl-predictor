# Model TODO

## Goalkeeper save model

- Replace the temporary historical save-points-per-90 proxy with a fixture-specific model.
- Forecast shots on target faced, use opponent attacking strength and goalkeeper/team xGA, and model the distribution of saves so FPL's one-point-per-three-saves threshold is respected.
- Calibrate and backtest the new model before replacing the proxy.

## Leakage-safe historical fixture normalisation

- Store opponent, venue, and the pre-match team-strength/FDR context alongside every player-match record in both current and prior-season artifacts.
- Convert historical xG and xA into neutral-opponent player propensities before applying the upcoming fixture's Elo factor.
- Do not use current Elo ratings retrospectively in historical samples or backtests.

## Non-penalty and penalty goal forecasts

- Add a reliable NPxG source rather than inferring penalties from total xG values.
- Model team penalty incidence from opponent penalties conceded and other pre-match covariates.
- Estimate each player's probability of taking a team penalty, including substitutions and shared duties.
- Add expected converted penalty goals separately to the NPxG goal forecast and validate the decomposition in backtests.

