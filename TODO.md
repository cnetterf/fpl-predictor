# Model TODO

## Leakage-safe historical fixture normalisation

- Store opponent, venue, and the pre-match team-strength/FDR context alongside every player-match record in both current and prior-season artifacts.
- Convert historical xG and xA into neutral-opponent player propensities before applying the upcoming fixture's Elo factor.
- Do not use current Elo ratings retrospectively in historical samples or backtests.

## Finishing-adjustment calibration

- Change the provisional finishing-adjustment bounds from `0.50–1.25` to `0.70–1.43` (`1 / 0.70`, symmetric around 1.00).
- Backtest no finishing adjustment and the agreed `0.70–1.43` confidence-shrunk adjustment against the current model before relying on it in future seasonal calibration.
- Check calibration by position, xG sample size, and forecast horizon before selecting or removing the bound.
- Confirm that any improvement persists out of sample and is not driven by a small group of extreme underperformers.

## Non-penalty and penalty goal forecasts

- Add a reliable NPxG source rather than inferring penalties from total xG values.
- Model team penalty incidence from opponent penalties conceded and other pre-match covariates.
- Estimate each player's probability of taking a team penalty, including substitutions and shared duties.
- Add expected converted penalty goals separately to the NPxG goal forecast and validate the decomposition in backtests.

## Tried and decided against

### Fixture-specific goalkeeper save model

- A 2025–26 holdout test of a simple fixture-aware save model improved goalkeeper save-point MAE only from `0.5783` to `0.5693` per start (about 1.6%).
- A fuller shots-on-target model would require a new, less reliable shot-event provider and substantial calibration work.
- Save points are a small, infrequent component of the typical FPL decision: the user usually owns one goalkeeper or rotates two primarily on projected clean sheets.
- Decision: retain the current historical save-points-per-90 proxy unless goalkeeper save points become materially more important to the product.
