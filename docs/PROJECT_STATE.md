# FPL Model Project State

Last reviewed: 27 September 2026

This is a concise handoff for future Codex chats. It can become stale, so verify it against Git, the generated-data metadata, and GitHub Actions before relying on dates or status.

## Current state

- Repository: `/Users/craig/Documents/FPL-model`
- Branch: `main`
- Latest verified data refresh at review: the automated refresh of 17 September 2026.
- The local checkout was fast-forwarded to `origin/main` on 17 September 2026.

## Published data at review

- Static predictions generated: 15 September 2026 at 17:17 UTC
- Source fetch: 15 September 2026 at 17:11 UTC
- Latest completed gameweek in both player-stat sources: GW4
- Available prediction range: GW5-GW38
- ClubElo effective date: 15 September 2026
- ClubElo method: direct ranking-page fetch
- ClubElo fallback used: no
- Primary or secondary source warnings: none

Read these values from `data/static_predictions.json` again whenever freshness matters.

## Important completed work

### Watch List signals and penalty allocation — working tree, pending verification/publication

- Watch List candidate cards use the compact agreed layout: wider cards retain a single metadata line, `Form` shows three emerging-signal squares plus an established-form square, and hover/focus exposes a compact last-three versus prior-three xG/xA breakdown. Fixture tailwinds and Watch actions share the footer.
- The live finishing-adjustment bounds are now `0.70–1.43`, replacing the previous provisional `0.50–1.25` bounds.
- Official FPL's ranked club penalty order is used to show each available player's conditional penalty-taker chance. A player projected for fewer than 15 minutes is excluded; remaining ordered takers are normalised to 100%. A small penalty xG slice is reallocated within the existing team goal forecast, so total team xG is preserved.
- Box-shot form is deliberately not proxied from xG: it remains unavailable until the cached Understat match-event enrichment is added. The remaining reliable NPxG/penalty-incidence work is recorded in `TODO.md`.
- The fixture-specific goalkeeper save model was tested and deliberately declined: a 2025–26 holdout reduced save-points MAE only from `0.5783` to `0.5693` per start. It is recorded under “Tried and decided against” in `TODO.md`.

### Backtest result integrity and live Gameweek metadata — `8885766a` and later refreshes

- GW3 benchmark actuals are now reconciled from Official FPL's event-level live feed when the cached player histories are incomplete. The generator rewrites a completed results snapshot containing missing actuals rather than treating it as final.
- `data/team_metadata.json` publishes the 20 stable team IDs, short names, and badge codes for frontend reuse.
- The Gameweek tab overlays live Official FPL fixture metadata on static model data for current, past, and future gameweeks. It can therefore show current/final scores, in-play minutes, badge logos, and confirmed kickoff times even if static fixture metadata is incomplete.
- The Your Team benchmark now shows team/position per player and a compact gameweek-by-gameweek predicted/actual/difference history. GW1 remains explicitly unavailable because no defensible pre-deadline forecast exists.
- Live Gameweek forecasts now persist a compact, immutable fixture-metrics record with each pre-deadline prediction snapshot. This keeps team xG, CS%, and likely-player goal sums visible after the temporary prediction window closes; GW4 was recovered from the verifiable pre-deadline Git window at `67731104`.
- Gameweek scores now sit beneath each team name, while Predictor and Backtest team filters use the Lineup kit treatment. The backtest GW strip aligns one-decimal P/A/D values and applies modest green/red only to the difference.
- Team filters now use a single shared selected-state background rather than twenty separate green tiles.
- The Gameweek tab now defaults to the next GW from the local calendar day after every fixture in the current GW has finished, while its arrows retain access to the completed GW. Watch List rows and candidates show price; loaded Lineup players are excluded from new-candidate slots and shown separately when they would have qualified.

### Current market-goals capture — active locally, pending first scheduled publication

- `market_odds.py` derives publishable market home/away xG from a de-vigged median consensus of available UK/EU 1X2 and O/U 2.5 quotes from The Odds API.
- The static Gameweek view shows model xG, market xG, and player-goal sums side by side. It preserves the final scheduled pre-deadline market capture and retains it if a later source request fails.
- The first successful local capture was 15 September 2026 at 20:59 UTC: 20 fixtures across GW5-GW6, with 10-19 supporting bookmakers per fixture. It is derived data only; the client never receives the API key.
- `ODDS_API_KEY` is configured locally and as a GitHub Actions secret. GW1-GW4 have no market capture and deliberately show as unavailable; historical/import backtesting remains deferred.

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

- Add leakage-safe historical fixture normalisation.
- Backtest the agreed `0.70–1.43` finishing bounds.
- Complete NPxG/penalty-incidence calibration with a reliable event source; current taker shares are an interim allocation layer.

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
