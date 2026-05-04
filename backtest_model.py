import argparse
import math
from pathlib import Path
from statistics import mean

import server


ROOT = Path(__file__).resolve().parent
TOP_N = 20


def parse_args():
    parser = argparse.ArgumentParser(
        description="Backtest Official FPL and Elo Insights predictions against finished gameweeks."
    )
    parser.add_argument(
        "--sources",
        nargs="+",
        default=["official", "elo"],
        choices=["official", "elo"],
        help="Prediction sources to evaluate.",
    )
    parser.add_argument(
        "--horizons",
        nargs="+",
        type=int,
        default=[1, 2, 3, 4, 5, 6],
        help="Horizons in gameweeks to backtest.",
    )
    parser.add_argument(
        "--min-start-gw",
        type=int,
        default=2,
        help="Earliest start gameweek to evaluate.",
    )
    return parser.parse_args()


def to_float(value):
    return float(value or 0)


def to_int(value):
    return int(float(value or 0))


def load_base_context():
    cache = server.DataCache(server.CACHE_PATH)
    bootstrap = cache.get_bootstrap()
    if not bootstrap:
        raise RuntimeError("No cached bootstrap data found. Refresh data first.")
    element_summaries = cache.data["element_summaries"]
    elo_insights = cache.get_elo_insights() or {}
    return bootstrap, element_summaries, elo_insights


def finished_gameweeks(bootstrap):
    return [event["id"] for event in bootstrap.get("events", []) if event.get("finished")]


def official_histories(element_summaries):
    histories = {}
    for player_id, summary in element_summaries.items():
        histories[player_id] = summary.get("history", [])
    return histories


def elo_histories(elo_insights):
    return (
        elo_insights.get("history_by_player", {}),
        elo_insights.get("team_strengths", {}),
        elo_insights.get("latest_finished_gw"),
    )


def build_player_context(player, history_before):
    context = dict(player)
    minutes_total = sum(to_int(match.get("minutes")) for match in history_before)
    starts_total = sum(to_int(match.get("starts")) for match in history_before)
    xg_total = sum(to_float(match.get("expected_goals")) for match in history_before)
    xa_total = sum(to_float(match.get("expected_assists")) for match in history_before)
    recoveries_total = sum(to_float(match.get("recoveries")) for match in history_before)

    context["minutes"] = minutes_total
    context["starts"] = starts_total
    context["expected_goals_per_90"] = (xg_total * 90 / minutes_total) if minutes_total else 0
    context["expected_assists_per_90"] = (xa_total * 90 / minutes_total) if minutes_total else 0
    context["defensive_contribution_per_90"] = (recoveries_total * 90 / minutes_total) if minutes_total else 0
    context["chance_of_playing_next_round"] = 100
    return context


def fixture_from_match(player_team_id, match):
    opponent_team = match.get("opponent_team")
    if opponent_team is None:
        return None
    if match.get("was_home"):
        return {
            "event": match.get("round"),
            "team_h": player_team_id,
            "team_a": opponent_team,
            "is_home": True,
            "difficulty": None,
        }
    return {
        "event": match.get("round"),
        "team_h": opponent_team,
        "team_a": player_team_id,
        "is_home": False,
        "difficulty": None,
    }


def actual_matches(history, start_gw, end_gw):
    return [match for match in history if start_gw <= to_int(match.get("round")) <= end_gw]


def prior_history(history, start_gw):
    return [match for match in history if to_int(match.get("round")) < start_gw]


def rank_map(players, score_key):
    sorted_players = sorted(players, key=score_key, reverse=True)
    return {player["player_id"]: index + 1 for index, player in enumerate(sorted_players)}


def spearman(predictions, actuals):
    if len(predictions) < 2:
        return 0.0
    pred_ranks = rank_map(predictions, lambda item: item["predicted_points"])
    actual_ranks = rank_map(actuals, lambda item: item["actual_points"])
    common_ids = pred_ranks.keys() & actual_ranks.keys()
    n = len(common_ids)
    if n < 2:
        return 0.0
    diff_sq = sum((pred_ranks[player_id] - actual_ranks[player_id]) ** 2 for player_id in common_ids)
    return 1 - (6 * diff_sq) / (n * (n**2 - 1))


def overlap_at_n(predictions, actuals, n=TOP_N):
    pred_ids = {item["player_id"] for item in sorted(predictions, key=lambda item: item["predicted_points"], reverse=True)[:n]}
    actual_ids = {item["player_id"] for item in sorted(actuals, key=lambda item: item["actual_points"], reverse=True)[:n]}
    return len(pred_ids & actual_ids)


def evaluate_source(source, predictor, bootstrap, histories, fixture_histories, horizons, start_gws, max_finished):
    players = bootstrap["elements"]
    summaries = []

    for horizon in horizons:
        window_rows = []
        for start_gw in start_gws:
            end_gw = start_gw + horizon - 1
            if end_gw > max_finished:
                continue

            predictions = []
            actuals = []

            for player in players:
                player_id = str(player["id"])
                full_history = histories.get(player_id, [])
                fixture_history = fixture_histories.get(player_id, [])
                history_before = prior_history(full_history, start_gw)
                target_matches = actual_matches(fixture_history, start_gw, end_gw)
                if not target_matches:
                    continue

                fixtures = []
                for match in target_matches:
                    fixture = fixture_from_match(player["team"], match)
                    if fixture:
                        fixtures.append(fixture)
                if not fixtures:
                    continue

                player_context = build_player_context(player, history_before)
                predicted = predictor._predict_player(player_context, history_before, fixtures)
                actual_points = round(sum(to_float(match.get("total_points")) for match in target_matches), 2)
                predicted_points = predicted["predicted_total_points"]
                error = predicted_points - actual_points

                row = {
                    "player_id": player["id"],
                    "player_name": predicted["player_name"],
                    "team": predicted["team"],
                    "position": predicted["position"],
                    "start_gw": start_gw,
                    "end_gw": end_gw,
                    "predicted_points": predicted_points,
                    "actual_points": actual_points,
                    "error": error,
                }
                predictions.append(row)
                actuals.append(row)
                window_rows.append(row)

            if not predictions:
                continue

            summaries.append(
                {
                    "source": source,
                    "horizon": horizon,
                    "start_gw": start_gw,
                    "end_gw": end_gw,
                    "players": len(predictions),
                    "mae": mean(abs(item["error"]) for item in predictions),
                    "rmse": math.sqrt(mean((item["error"]) ** 2 for item in predictions)),
                    "top20_overlap": overlap_at_n(predictions, actuals),
                    "spearman": spearman(predictions, actuals),
                }
            )

        overall_rows = [row for row in summaries if row["source"] == source and row["horizon"] == horizon]
        if overall_rows:
            yield {
                "source": source,
                "horizon": horizon,
                "windows": len(overall_rows),
                "players_avg": mean(row["players"] for row in overall_rows),
                "mae": mean(row["mae"] for row in overall_rows),
                "rmse": mean(row["rmse"] for row in overall_rows),
                "top20_overlap": mean(row["top20_overlap"] for row in overall_rows),
                "spearman": mean(row["spearman"] for row in overall_rows),
            }


def print_summary(results):
    print("Source      Horizon  Windows  AvgPlayers  MAE    RMSE   Top20Overlap  Spearman")
    for row in results:
        print(
            f"{row['source']:<11} {row['horizon']:<7} {row['windows']:<7} "
            f"{row['players_avg']:<11.1f} {row['mae']:<6.2f} {row['rmse']:<6.2f} "
            f"{row['top20_overlap']:<13.2f} {row['spearman']:.3f}"
        )


def main():
    args = parse_args()
    bootstrap, element_summaries, elo_insights = load_base_context()
    finished_gws = finished_gameweeks(bootstrap)
    max_finished = max(finished_gws)
    start_gws = [gw for gw in finished_gws if gw >= args.min_start_gw]

    official_hist = official_histories(element_summaries)
    elo_hist, elo_team_strengths = ({}, {})
    elo_latest_finished = None
    if "elo" in args.sources:
        elo_hist, elo_team_strengths, elo_latest_finished = elo_histories(elo_insights)

    results = []
    for source in args.sources:
        histories = official_hist if source == "official" else elo_hist
        source_start_gws = start_gws
        source_max_finished = max_finished
        note = None

        if source == "elo":
            if not histories:
                print("Elo cache is empty. Regenerate static data first to populate it.")
                continue
            available_rounds = sorted({to_int(match.get("round")) for rows in histories.values() for match in rows})
            if not available_rounds:
                print("Elo cache has no historical rounds.")
                continue
            earliest_round = min(available_rounds)
            source_start_gws = [gw for gw in start_gws if gw > earliest_round]
            source_max_finished = min(max_finished, max(available_rounds))
            note = f"Elo backtest limited to cached rounds {earliest_round}-{source_max_finished}."

        predictor = server.Predictor(
            bootstrap,
            element_summaries,
            history_overrides=histories,
            team_strengths=elo_team_strengths if source == "elo" else None,
        )
        valid_horizons = [h for h in args.horizons if h >= 1 and h <= 6 and source_start_gws and min(source_start_gws) + h - 1 <= source_max_finished]
        results.extend(evaluate_source(source, predictor, bootstrap, histories, official_hist, valid_horizons, source_start_gws, source_max_finished))
        if note:
            print(note)

    print_summary(results)
    print()
    print("Notes:")
    print("- This backtest uses only prior player-match history before each start GW.")
    print("- It scores against actual FPL points over the requested horizon.")
    print("- Team strength inputs are current cached strengths, so team-level leakage remains.")


if __name__ == "__main__":
    main()
