# FPL Model Project State

Last reviewed: 12 September 2026

This is a concise handoff for future Codex chats. It can become stale, so verify it against Git, the generated-data metadata, and GitHub Actions before relying on dates or status.

## Current state

- Repository: `/Users/craig/Documents/FPL-model`
- Branch: `main`
- Latest commit at review: `67731104` (`Refresh static FPL data`)
- The local checkout was fast-forwarded to `origin/main` on 12 September 2026.
- The latest eight observed `Refresh Static FPL Data` workflow runs were successful, through the run completed at 04:57 UTC on 12 September 2026.

## Published data at review

- Static predictions generated: 12 September 2026 at 04:57 UTC
- Source fetch: 12 September 2026 at 04:51 UTC
- Latest completed gameweek in both player-stat sources: GW3
- Available prediction range: GW4-GW38
- ClubElo effective date: 11 September 2026
- ClubElo method: direct ranking-page fetch
- ClubElo fallback used: no
- Primary or secondary source warnings: none

Read these values from `data/static_predictions.json` again whenever freshness matters.

## Important completed work

### Backtest result integrity and live Gameweek metadata — pending commit

- GW3 benchmark actuals are now reconciled from Official FPL's event-level live feed when the cached player histories are incomplete. The generator rewrites a completed results snapshot containing missing actuals rather than treating it as final.
- `data/team_metadata.json` publishes the 20 stable team IDs, short names, and badge codes for frontend reuse.
- The Gameweek tab overlays live Official FPL fixture metadata on static model data for current, past, and future gameweeks. It can therefore show current/final scores, in-play minutes, badge logos, and confirmed kickoff times even if static fixture metadata is incomplete.
- The Your Team benchmark now shows team/position per player and a compact gameweek-by-gameweek predicted/actual/difference history. GW1 remains explicitly unavailable because no defensible pre-deadline forecast exists.
- Live Gameweek forecasts now persist a compact, immutable fixture-metrics record with each pre-deadline prediction snapshot. This keeps team xG, CS%, and likely-player goal sums visible after the temporary prediction window closes; GW4 was recovered from the verifiable pre-deadline Git window at `67731104`.
- Gameweek scores now sit beneath each team name, while Predictor and Backtest team filters use the Lineup kit treatment. The backtest GW strip aligns one-decimal P/A/D values and applies modest green/red only to the difference.

### Data refresh reliability — `32c62391`

- Team ratings now come directly from ClubElo instead of depending on FPL-Core Insights for Elo.
- ClubElo retrieval validates a complete, coherent, dated 20-team set.
- A verified Elo snapshot can be retained for up to 30 days when ClubElo is temporarily unavailable, with an amber site warning.
- FPL-Core player statistics refresh independently and cannot block fresh Official FPL predictions.
- The workflow retries generation and validation three times and preserves the last verified publication if all attempts fail.

### Predictor and lineup interface — `b621f4b5`

- The predictor table includes current player price.
- The predictor has a min/max price filter in £0.5m increments.
- Filters and reload controls sit to the left of the team grid; teams use a five-row by four-column desktop layout.
- The lineup player popup defaults to a points breakdown and can toggle to potential replacements.
- The lineup popup uses the gameweek range selected on the lineup page.

## Data-source terminology

- **Official FPL**: default player statistics and live FPL metadata.
- **FPL-Core player stats**: optional comparison player statistics, principally from `player_gameweek_stats.csv`.
- **ClubElo**: team ratings used for fixture-strength modelling by both player-stat variants.
- The internal source key `elo` names the FPL-Core comparison variant. It should not be interpreted as a separate Elo provider.

## Deferred model work

`TODO.md` is authoritative. Its current themes are:

- Replace the goalkeeper historical save-points proxy with a fixture-specific shots-on-target/save distribution model.
- Add leakage-safe historical fixture normalisation.
- Calibrate or remove the provisional finishing-adjustment lower bound.
- Separate non-penalty and penalty goal forecasting using a reliable NPxG source.

No next implementation task has been selected in this handoff.

## Start-of-session check

A future chat should:

1. Read `AGENTS.md`, this file, `README.md`, and the relevant part of `TODO.md`.
2. Check the working tree and fetch `origin`.
3. Compare local `main` with `origin/main`; automated refresh commits commonly make local `main` stale.
4. Check recent workflow runs and generated-data metadata when the request concerns live data.
5. Give the user a brief refresher before discussing a new substantial model change.

## Updating this file

Replace outdated status instead of accumulating a diary. Record only information that helps the next chat orient itself, and include commit IDs for completed milestones. Git history remains the detailed record.
