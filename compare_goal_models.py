"""Like-for-like 2025-26 goal-model comparison on market-backtest fixtures."""

import json
import math
from pathlib import Path

import historical_market_backtest as historical
import server


ROOT = Path(__file__).resolve().parent
MARKET_PATH = ROOT / "data" / "market_backtests" / "2025-26.json"

# Historical 3x3 fixture-total targets recovered from the prior analysis.
GRID = ((3.09, 3.10, 3.27), (2.80, 2.75, 2.84), (2.99, 2.71, 2.77))


def negative_log_likelihood(goals, rate):
    return rate - goals * math.log(max(rate, 1e-9)) + math.lgamma(goals + 1)


def summary(rows):
    team_pairs = (
        [(row["home_xg"], row["home_goals"]) for row in rows]
        + [(row["away_xg"], row["away_goals"]) for row in rows]
    )
    return {
        "team_mae": sum(abs(xg - goals) for xg, goals in team_pairs) / len(team_pairs),
        "fixture_mae": sum(
            abs(row["home_xg"] + row["away_xg"] - row["home_goals"] - row["away_goals"])
            for row in rows
        ) / len(rows),
        "nll": sum(
            negative_log_likelihood(goals, xg) for xg, goals in team_pairs
        ) / len(team_pairs),
    }


def tier(ratings, team):
    ordered = sorted(ratings, key=ratings.get, reverse=True)
    return min(2, ordered.index(team) * 3 // len(ordered))


def result_row(home_xg, away_xg, fixture):
    return {
        "home_xg": home_xg,
        "away_xg": away_xg,
        "home_goals": fixture["home_goals"],
        "away_goals": fixture["away_goals"],
    }


def main():
    market = json.loads(MARKET_PATH.read_text())
    market_by_fixture = {
        (row["home_team"], row["away_team"], row["kickoff_time"]): (
            row["home_xg"], row["away_xg"],
        )
        for gameweek in market["gameweeks"].values()
        for row in gameweek["fixtures"]
    }
    fixtures = [
        row for gameweek in historical.historical_fixtures().values() for row in gameweek
    ]
    fixtures.sort(key=lambda row: row["kickoff_time"])
    teams = sorted({
        team
        for fixture in fixtures
        for team in (fixture["home_team"], fixture["away_team"])
    })
    ratings = {team: 1500.0 for team in teams}
    outputs = {name: [] for name in ("naive", "grid_full", "grid_50", "market")}

    for fixture in fixtures:
        home_team = fixture["home_team"]
        away_team = fixture["away_team"]
        key = (home_team, away_team, fixture["kickoff_time"].isoformat())
        if key in market_by_fixture:
            base = server.elo_fixture_values(ratings[home_team], ratings[away_team])
            target = GRID[tier(ratings, home_team)][tier(ratings, away_team)]
            multipliers = {
                "naive": 1.0,
                "grid_full": target / 2.8,
                "grid_50": 1 + 0.5 * (target / 2.8 - 1),
            }
            for name, multiplier in multipliers.items():
                outputs[name].append(result_row(
                    base["home_xg"] * multiplier,
                    base["away_xg"] * multiplier,
                    fixture,
                ))
            market_home_xg, market_away_xg = market_by_fixture[key]
            outputs["market"].append(result_row(
                market_home_xg, market_away_xg, fixture,
            ))

        # Sequential Elo reconstruction, K=20, neutral 1500 initial state.
        expected = 1 / (
            1 + 10 ** (-(ratings[home_team] + 100 - ratings[away_team]) / 400)
        )
        actual = (
            1 if fixture["home_goals"] > fixture["away_goals"]
            else 0.5 if fixture["home_goals"] == fixture["away_goals"]
            else 0
        )
        delta = 20 * (actual - expected)
        ratings[home_team] += delta
        ratings[away_team] -= delta

    for name, rows in outputs.items():
        metrics = {key: round(value, 4) for key, value in summary(rows).items()}
        print(name, len(rows), metrics)


if __name__ == "__main__":
    main()
