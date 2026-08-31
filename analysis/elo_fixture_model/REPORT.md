# Elo fixture model

## Live model

Team ratings are refreshed from the `elo` column in FPL Core Insights
`data/<season>/teams.csv`. A refresh is accepted only when all 20 current FPL
teams have positive numeric ratings. An invalid refresh retains the last valid
snapshot and surfaces a warning; it never silently falls back to Official FPL
fixture difficulty.

For home rating `H` and away rating `A`:

1. `effective H = H + 100`
2. `delta = effective H - A`
3. `home factor = 1 + 0.55 × tanh(delta / 400)`
4. `away factor = 1 + 0.55 × tanh(-delta / 400)`
5. `home xG = 1.40 × home factor`; `away xG = 1.40 × away factor`
6. `home xCS = exp(-away xG)`; `away xCS = exp(-home xG)`

The two fixture factors always sum to 2.0 and the two xG values always sum to
2.80. The attack factor replaces both the former Official FPL FDR mapping and
the separate home/away multiplier. It scales player xG and xA after those rates
have already been adjusted for expected minutes.

Player clean-sheet probability is `team xCS × P(player reaches 60 minutes)`.
The eligibility probability is the share of the player's six sampled team
fixtures in which the player reached 60 minutes, including zero-minute
non-appearances.

## Arsenal v Hull check

With raw ratings Arsenal 2064 and Hull 1533:

- Effective Arsenal Elo: 2164
- Delta: 631
- Arsenal factor: 1.505016; Hull factor: 0.494984
- Arsenal xG: 2.107022; Hull xG: 0.692978
- Arsenal xCS: 50.008%; Hull xCS: 12.160%

## Snapshots and backtests

Each successful generation stores the current ratings with the gameweek from
which they are valid in `data/elo_snapshots.json`. Forward predictions use the
newest valid snapshot fetched at generation time. Backtests use only a snapshot
whose effective gameweek is no later than the backtest start. Where no such
historical Elo snapshot exists, the existing pre-window reconstructed team
strength model remains the explicit leakage-safe fallback; today's Elo is never
applied retroactively.

## Retained future applications

- Team probabilities of scoring 0, 1, 2, or 3+ goals.
- Captaincy ceiling and multi-goal probabilities.
- Expected goals-conceded point deductions.
- Goalkeeper save forecasts when a shots-on-target model is available.
- Opponent-adjusted team and player form.
- Multi-gameweek schedule-strength summaries.
- Analysis of actual performance against Elo expectation.
