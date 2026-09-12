# FPL Model Project Instructions

These instructions apply to the entire repository.

## Start every task

1. Read `README.md`, `docs/PROJECT_STATE.md`, and any relevant section of `TODO.md` before proposing or making changes.
2. Treat `docs/PROJECT_STATE.md` as an orientation aid, not as the source of truth. Verify current facts from the working tree, Git history, generated-data metadata, and GitHub Actions when relevant.
3. Run `git status --short --branch` and fetch `origin` before editing. The data-refresh workflow commits to `main` twice daily, so a local checkout is often behind. If the tree is clean and only behind, fast-forward it. If local changes exist, preserve them and do not overwrite or discard them.
4. Briefly tell the user about any important change since the recorded project-state date, especially new data, failed automation, or an unexpected dirty working tree.

## Working with the user

- The user often returns after 7-10 days. Start with a short plain-language refresher when it would help: current state, what changed, and the immediate decision or next step.
- Lead with outcomes and explain model implications in ordinary language before implementation detail.
- If the user asks to discuss, plan, review, or ask clarifying questions before building, do not edit files until they explicitly authorize implementation (often with “ok go”).
- Do not commit, push, trigger a workflow, deploy, or otherwise publish changes unless the user explicitly asks.
- Ask only questions whose answers would materially change the result. Make safe, documented assumptions for routine details.

## Project architecture and data rules

- The application is a GitHub Pages-compatible static frontend in `index.html` and `app.js`.
- Prediction and backtest logic lives mainly in `server.py` and `backtest_model.py`.
- `generate_static_data.py` produces the published manifests and compressed prediction windows under `data/`; `validate_static_data.py` validates the primary published artifacts.
- Official FPL is the default player-stat source. FPL-Core Insights supplies the optional comparison player statistics.
- Both prediction sources use the same team-strength input fetched directly from ClubElo. The internal source key `elo` refers to the FPL-Core player-stat comparison; it does not mean that its team Elo ratings come from FPL-Core.
- ClubElo must resolve to one coherent, dated, complete 20-team snapshot. A previously verified complete snapshot may be reused for no more than 30 days and must be surfaced as a warning.
- A lagging or unavailable FPL-Core player-stat source must not block fresh Official FPL predictions.
- Never hand-edit generated JSON or `.json.gz` prediction files. Change the generator/model, regenerate, and validate.
- The scheduled workflow is `.github/workflows/refresh-static-data.yml` and runs twice daily. It retries generation and validation up to three times, then leaves the last verified publication in place on failure.

## Verification

Use checks proportionate to the change:

- Python tests: `python3 -m unittest test_server.py test_generate_static_data.py`
- Published-data validation: `python3 validate_static_data.py`
- Fresh data generation, only when the task requires it: `python3 generate_static_data.py`
- Local application: `python3 server.py`
- For frontend changes, inspect the affected flows in a browser at desktop and mobile widths, and check for console errors.
- Review `git diff --check` and the final diff before handing work back.

Do not regenerate the large static dataset merely to test an unrelated documentation or frontend-only change.

## Keeping future chats effective

- Update `docs/PROJECT_STATE.md` after a material milestone, data-source change, newly discovered blocker, or change to the recommended next work.
- Keep stable rules here and time-sensitive facts in `docs/PROJECT_STATE.md`.
- Keep the state file concise. Link to commits, source files, or `TODO.md` rather than copying long implementation histories.
- See `WORKING_WITH_CODEX.md` for the user-facing workflow for returning to the project or starting a focused new chat.
