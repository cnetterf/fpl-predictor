#!/usr/bin/env python3
"""Audit 2025-26 official FPL fixture difficulty against realised GD and xGD."""

import csv
import math
import statistics
import zipfile
from collections import Counter, defaultdict
from io import TextIOWrapper
from pathlib import Path


ROOT = Path(__file__).resolve().parent
ARCHIVE_PATH = ROOT / "archives" / "2025-26" / "fpl-2025-26-raw-sources.zip"
OUTPUT_DIR = ROOT / "analysis" / "fdr_2025_26"
ARCHIVE_ROOT = "fpl-2025-26-raw-sources"


def read_zip_csv(archive, relative_path):
    with archive.open(f"{ARCHIVE_ROOT}/{relative_path}") as source:
        return list(csv.DictReader(TextIOWrapper(source, encoding="utf-8")))


def as_int(value):
    return int(float(value))


def as_float(value):
    return float(value)


def quantile(values, probability):
    ordered = sorted(values)
    if not ordered:
        return math.nan
    position = (len(ordered) - 1) * probability
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return ordered[lower]
    weight = position - lower
    return ordered[lower] * (1 - weight) + ordered[upper] * weight


def rank_quintiles(rows, value_key, group_key=None):
    groups = defaultdict(list)
    for row in rows:
        groups[row[group_key] if group_key else "all"].append(row)
    result = {}
    for group_rows in groups.values():
        # Higher GD/xGD is easier, so the best observations receive difficulty 1.
        ordered = sorted(
            group_rows,
            key=lambda row: (-row[value_key], row["gameweek"], row["fixture_id"], row["team"]),
        )
        size = len(ordered)
        for index, row in enumerate(ordered):
            result[row["row_id"]] = min(5, math.floor(index * 5 / size) + 1)
    return result


def average(values):
    return sum(values) / len(values) if values else math.nan


def median(values):
    return statistics.median(values) if values else math.nan


def rank_values(values):
    indexed = sorted(enumerate(values), key=lambda item: item[1])
    ranks = [0.0] * len(values)
    cursor = 0
    while cursor < len(indexed):
        end = cursor + 1
        while end < len(indexed) and indexed[end][1] == indexed[cursor][1]:
            end += 1
        rank = (cursor + end - 1) / 2 + 1
        for position in range(cursor, end):
            ranks[indexed[position][0]] = rank
        cursor = end
    return ranks


def pearson(left, right):
    left_mean = average(left)
    right_mean = average(right)
    numerator = sum((x - left_mean) * (y - right_mean) for x, y in zip(left, right))
    left_ss = sum((x - left_mean) ** 2 for x in left)
    right_ss = sum((y - right_mean) ** 2 for y in right)
    denominator = math.sqrt(left_ss * right_ss)
    return numerator / denominator if denominator else 0.0


def spearman(left, right):
    return pearson(rank_values(left), rank_values(right))


def add_pre_match_baselines(rows, prior_matches=5, initial_league_xg=1.4):
    team_totals = defaultdict(float)
    team_counts = defaultdict(int)
    league_total = 0.0
    league_count = 0
    rows_by_gameweek = defaultdict(list)
    for row in rows:
        rows_by_gameweek[row["gameweek"]].append(row)
    for gameweek in sorted(rows_by_gameweek):
        league_mean = league_total / league_count if league_count else initial_league_xg
        for row in rows_by_gameweek[gameweek]:
            team = row["team"]
            row["pre_match_xg_baseline"] = (
                team_totals[team] + prior_matches * league_mean
            ) / (team_counts[team] + prior_matches)
        for row in rows_by_gameweek[gameweek]:
            team_totals[row["team"]] += row["xg"]
            team_counts[row["team"]] += 1
            league_total += row["xg"]
            league_count += 1


def isotonic_decreasing(values, weights):
    blocks = []
    for index, (value, weight) in enumerate(zip(values, weights)):
        blocks.append({"start": index, "end": index, "weight": weight, "value": value})
        while len(blocks) >= 2 and blocks[-2]["value"] < blocks[-1]["value"]:
            right = blocks.pop()
            left = blocks.pop()
            total_weight = left["weight"] + right["weight"]
            blocks.append({
                "start": left["start"],
                "end": right["end"],
                "weight": total_weight,
                "value": (left["value"] * left["weight"] + right["value"] * right["weight"]) / total_weight,
            })
    fitted = [1.0] * len(values)
    for block in blocks:
        for index in range(block["start"], block["end"] + 1):
            fitted[index] = block["value"]
    return fitted


def fit_fdr_factors(rows):
    raw, weights = [], []
    for fdr in range(1, 6):
        group = [row for row in rows if row["official_fdr"] == fdr]
        denominator = sum(row["pre_match_xg_baseline"] ** 2 for row in group)
        numerator = sum(row["pre_match_xg_baseline"] * row["xg"] for row in group)
        raw.append(numerator / denominator if denominator else 1.0)
        weights.append(denominator or 1.0)
    fitted = isotonic_decreasing(raw, weights)
    neutral = fitted[2] or 1.0
    return {fdr: fitted[fdr - 1] / neutral for fdr in range(1, 6)}


def fit_team_fdr_factors(rows, prior_matches=10):
    pooled = fit_fdr_factors(rows)
    result = {}
    for team in sorted({row["team"] for row in rows}):
        team_rows = [row for row in rows if row["team"] == team]
        raw, counts = {}, {}
        for fdr in range(1, 6):
            group = [row for row in team_rows if row["official_fdr"] == fdr]
            denominator = sum(row["pre_match_xg_baseline"] ** 2 for row in group)
            numerator = sum(row["pre_match_xg_baseline"] * row["xg"] for row in group)
            raw[fdr] = numerator / denominator if denominator else pooled[fdr]
            counts[fdr] = len(group)
        neutral = raw[3] or 1.0
        shrunk = [
            (counts[fdr] * (raw[fdr] / neutral) + prior_matches * pooled[fdr])
            / (counts[fdr] + prior_matches)
            for fdr in range(1, 6)
        ]
        fitted = isotonic_decreasing(shrunk, [counts[fdr] + prior_matches for fdr in range(1, 6)])
        fitted_neutral = fitted[2] or 1.0
        result[team] = {fdr: fitted[fdr - 1] / fitted_neutral for fdr in range(1, 6)}
    return result


def evaluate_factor_method(rows, factors_by_row, method, evaluation_window):
    evaluated = []
    for row in rows:
        factor = factors_by_row(row)
        predicted_xg = row["pre_match_xg_baseline"] * factor
        evaluated.append({
            "evaluation_window": evaluation_window,
            "method": method,
            "gameweek": row["gameweek"],
            "team": row["team"],
            "opponent": row["opponent"],
            "official_fdr": row["official_fdr"],
            "baseline_xg": row["pre_match_xg_baseline"],
            "factor": factor,
            "predicted_xg": predicted_xg,
            "actual_xg": row["xg"],
            "actual_goals": row["goals_for"],
            "xg_error": predicted_xg - row["xg"],
            "goal_error": predicted_xg - row["goals_for"],
        })
    return evaluated


def summarize_factor_backtest(predictions):
    summaries = []
    groups = defaultdict(list)
    for row in predictions:
        groups[(row["evaluation_window"], row["method"])].append(row)
    for (window, method), rows in groups.items():
        xg_errors = [row["xg_error"] for row in rows]
        goal_errors = [row["goal_error"] for row in rows]
        summaries.append({
            "evaluation_window": window,
            "method": method,
            "observations": len(rows),
            "xg_mae": average([abs(value) for value in xg_errors]),
            "xg_rmse": math.sqrt(average([value ** 2 for value in xg_errors])),
            "goal_mae": average([abs(value) for value in goal_errors]),
            "goal_rmse": math.sqrt(average([value ** 2 for value in goal_errors])),
            "mean_predicted_xg": average([row["predicted_xg"] for row in rows]),
            "mean_actual_xg": average([row["actual_xg"] for row in rows]),
        })
    return sorted(summaries, key=lambda row: (row["evaluation_window"], row["xg_mae"]))


def fmt(value, digits=3):
    if value is None or (isinstance(value, float) and math.isnan(value)):
        return ""
    return f"{value:.{digits}f}"


def write_csv(path, rows, fields):
    with path.open("w", newline="", encoding="utf-8") as target:
        writer = csv.DictWriter(target, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def line_chart(path, calibration):
    width, height = 900, 470
    left, right, top, bottom = 80, 35, 45, 70
    plot_width = width - left - right
    plot_height = height - top - bottom
    values = [row[key] for row in calibration for key in ("mean_goal_difference", "mean_xg_difference")]
    bound = max(1.0, math.ceil(max(abs(value) for value in values) * 5) / 5)

    def x_position(fdr):
        return left + (fdr - 1) * plot_width / 4

    def y_position(value):
        return top + (bound - value) * plot_height / (2 * bound)

    colors = {"mean_goal_difference": "#10213f", "mean_xg_difference": "#f97316"}
    labels = {"mean_goal_difference": "Actual GD", "mean_xg_difference": "xGD"}
    svg = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}">',
        '<rect width="100%" height="100%" fill="#fffaf2"/>',
        '<text x="80" y="28" font-family="Georgia,serif" font-size="20" font-weight="700" fill="#10213f">Outcome by official FPL fixture difficulty</text>',
    ]
    for tick in range(-5, 6):
        value = bound * tick / 5
        y = y_position(value)
        svg.append(f'<line x1="{left}" y1="{y:.1f}" x2="{width-right}" y2="{y:.1f}" stroke="#ddd5c8" stroke-width="1"/>')
        svg.append(f'<text x="{left-12}" y="{y+5:.1f}" text-anchor="end" font-family="Georgia,serif" font-size="13" fill="#59647a">{value:.1f}</text>')
    for fdr in range(1, 6):
        x = x_position(fdr)
        svg.append(f'<text x="{x:.1f}" y="{height-34}" text-anchor="middle" font-family="Georgia,serif" font-size="14" fill="#10213f">FDR {fdr}</text>')
    for key in colors:
        points = " ".join(f'{x_position(row["fdr"]):.1f},{y_position(row[key]):.1f}' for row in calibration)
        svg.append(f'<polyline points="{points}" fill="none" stroke="{colors[key]}" stroke-width="4" stroke-linecap="round" stroke-linejoin="round"/>')
        for row in calibration:
            x, y = x_position(row["fdr"]), y_position(row[key])
            svg.append(f'<circle cx="{x:.1f}" cy="{y:.1f}" r="6" fill="{colors[key]}"/>')
    svg.extend([
        '<line x1="615" y1="25" x2="650" y2="25" stroke="#10213f" stroke-width="4"/>',
        '<text x="660" y="30" font-family="Georgia,serif" font-size="13" fill="#10213f">Actual GD</text>',
        '<line x1="755" y1="25" x2="790" y2="25" stroke="#f97316" stroke-width="4"/>',
        '<text x="800" y="30" font-family="Georgia,serif" font-size="13" fill="#10213f">xGD</text>',
        '</svg>',
    ])
    path.write_text("\n".join(svg), encoding="utf-8")


def heatmap(path, factor_rows):
    teams = sorted({row["team"] for row in factor_rows})
    lookup = {(row["team"], row["fdr"]): row["attack_factor_vs_team_median_xg"] for row in factor_rows}
    width, height = 850, 70 + len(teams) * 30
    left, top, cell_width, cell_height = 210, 50, 110, 30

    def color(value):
        value = max(0.5, min(1.5, value))
        if value >= 1:
            amount = (value - 1) / 0.5
            return f"rgb({round(244 - 100 * amount)},{round(239 - 55 * amount)},{round(225 - 100 * amount)})"
        amount = (1 - value) / 0.5
        return f"rgb({round(244 + 5 * amount)},{round(239 - 65 * amount)},{round(225 - 195 * amount)})"

    svg = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}">',
        '<rect width="100%" height="100%" fill="#fffaf2"/>',
        '<text x="20" y="28" font-family="Georgia,serif" font-size="20" font-weight="700" fill="#10213f">Team attacking xG factor versus team median</text>',
    ]
    for fdr in range(1, 6):
        x = left + (fdr - 1) * cell_width
        svg.append(f'<text x="{x + cell_width/2}" y="45" text-anchor="middle" font-family="Georgia,serif" font-size="13" fill="#10213f">FDR {fdr}</text>')
    for row_index, team in enumerate(teams):
        y = top + row_index * cell_height
        svg.append(f'<text x="{left-12}" y="{y+20}" text-anchor="end" font-family="Georgia,serif" font-size="13" fill="#10213f">{team}</text>')
        for fdr in range(1, 6):
            value = lookup.get((team, fdr), math.nan)
            x = left + (fdr - 1) * cell_width
            fill = color(value) if not math.isnan(value) else "#eee8df"
            svg.append(f'<rect x="{x}" y="{y}" width="{cell_width-2}" height="{cell_height-2}" rx="3" fill="{fill}"/>')
            svg.append(f'<text x="{x + (cell_width-2)/2}" y="{y+19}" text-anchor="middle" font-family="Georgia,serif" font-size="12" font-weight="700" fill="#10213f">{fmt(value, 2) or "n/a"}</text>')
    svg.append('</svg>')
    path.write_text("\n".join(svg), encoding="utf-8")


def confusion_markdown(rows, key):
    lines = [
        "| Official FDR \\ Outcome quintile | 1 | 2 | 3 | 4 | 5 |",
        "|---:|---:|---:|---:|---:|---:|",
    ]
    for official_fdr in range(1, 6):
        counts = Counter(row[key] for row in rows if row["official_fdr"] == official_fdr)
        lines.append(f"| {official_fdr} | " + " | ".join(str(counts[level]) for level in range(1, 6)) + " |")
    return "\n".join(lines)


def build_report(rows, calibration, team_breaks, factor_rows, comparisons, global_breaks, factor_summaries, fitted_factors):
    calibration_lines = [
        "| FDR | N | Goals for | Goals against | GD | xG | xGA | xGD | Attack factor |",
        "|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for row in calibration:
        calibration_lines.append(
            f'| {row["fdr"]} | {row["matches"]} | {row["mean_goals_for"]:.2f} | '
            f'{row["mean_goals_against"]:.2f} | {row["mean_goal_difference"]:+.2f} | '
            f'{row["mean_xg"]:.2f} | {row["mean_xga"]:.2f} | {row["mean_xg_difference"]:+.2f} | '
            f'{row["pooled_attack_factor_vs_team_median"]:.2f} |'
        )

    comparison_lines = [
        "| Outcome classification | Exact FDR match | Within one level | Spearman |",
        "|---|---:|---:|---:|",
    ]
    for item in comparisons:
        comparison_lines.append(
            f'| {item["label"]} | {item["exact_match_pct"]:.1f}% | '
            f'{item["within_one_pct"]:.1f}% | {item["spearman"]:.3f} |'
        )

    team_factor_lines = [
        "| Team | FDR 1 | FDR 2 | FDR 3 | FDR 4 | FDR 5 |",
        "|---|---:|---:|---:|---:|---:|",
    ]
    factor_lookup = {(row["team"], row["fdr"]): row["attack_factor_vs_team_median_xg"] for row in factor_rows}
    for team in sorted({row["team"] for row in rows}):
        cells = []
        for fdr in range(1, 6):
            factor_row = next((row for row in factor_rows if row["team"] == team and row["fdr"] == fdr), None)
            cells.append(f'{factor_row["attack_factor_vs_team_median_xg"]:.2f} (n={factor_row["matches"]})' if factor_row else "n/a")
        team_factor_lines.append(f'| {team} | ' + " | ".join(cells) + " |")

    fdr_counts = Counter(row["official_fdr"] for row in rows)
    duplicate_break_teams = sum(
        1 for row in team_breaks
        if len({row["gd_q20"], row["gd_q40"], row["gd_q60"], row["gd_q80"]}) < 4
    )
    backtest_lines = [
        "| Evaluation | Method | N | xG MAE | xG RMSE | Goal MAE | Goal RMSE |",
        "|---|---|---:|---:|---:|---:|---:|",
    ]
    for row in factor_summaries:
        backtest_lines.append(
            f'| {row["evaluation_window"]} | {row["method"]} | {row["observations"]} | '
            f'{row["xg_mae"]:.3f} | {row["xg_rmse"]:.3f} | {row["goal_mae"]:.3f} | {row["goal_rmse"]:.3f} |'
        )

    return f"""# FDR calibration analysis: 2025-26

## Executive summary

This analysis compares official FPL fixture difficulty with realised goal difference (GD) and expected-goal difference (xGD) for all 380 Premier League matches. Each match is represented from both teams' perspectives, producing {len(rows)} observations.

The official ratings show a {'monotonic' if all(calibration[index]['mean_xg_difference'] >= calibration[index + 1]['mean_xg_difference'] for index in range(4)) else 'non-monotonic'} relationship with xGD from FDR 1 through FDR 5. The team-relative xGD classification has {next(item['exact_match_pct'] for item in comparisons if item['label'] == 'Team-relative xGD quintile'):.1f}% exact agreement and {next(item['within_one_pct'] for item in comparisons if item['label'] == 'Team-relative xGD quintile'):.1f}% agreement within one level of official FDR.

![Outcome by FDR](fdr_outcome_calibration.svg)

## Official FDR calibration

{chr(10).join(calibration_lines)}

The attack factor is match xG divided by that team's median xG, then averaged within each official FDR. It is diagnostic only and is not used by the predictor.

Official observation counts: {', '.join(f'FDR {fdr}: {fdr_counts[fdr]}' for fdr in range(1, 6))}.

## Forward-looking factor tests

The team baseline for each fixture uses only that team's earlier xG, shrunk by five matches toward the prior league mean. Because Official FDR incorporates some venue information, the venue tests below are deliberately mild residual adjustments applied on top of the current predictor factors. Monotonic fitted factors are constrained to `FDR1 >= FDR2 >= FDR3 >= FDR4 >= FDR5` and normalised to FDR3 = `1.0`. The team-specific method uses a 10-match prior to shrink each team/FDR cell toward the pooled league factor.

The GW1-19 fit produced: `{', '.join(f'FDR {fdr}: {fitted_factors[fdr]:.3f}' for fdr in range(1, 6))}`.

{chr(10).join(backtest_lines)}

The full-season observed mapping is included only as a hindsight benchmark. It is not a valid candidate for selection because it uses outcomes from the evaluation period.

## Quintile comparison

Global realised-GD quintile boundaries are `{global_breaks['gd']}`. Global xGD boundaries are `{global_breaks['xgd']}`. Lower outcome-quintile numbers mean easier/better realised fixtures, matching FPL's direction.

{chr(10).join(comparison_lines)}

Rank-based quintiles are used for the classifications so every group has similar size. Numeric GD boundaries are highly tied: {duplicate_break_teams} of {len(team_breaks)} teams have fewer than four distinct GD cut points. This confirms that actual GD is too discrete to define five stable production levels by itself.

### Team-relative GD confusion matrix

{confusion_markdown(rows, "team_gd_quintile")}

### Team-relative xGD confusion matrix

{confusion_markdown(rows, "team_xgd_quintile")}

## Team-specific attacking factors

{chr(10).join(team_factor_lines)}

![Team attacking factors](team_attack_factor_heatmap.svg)

These factors are noisy because each team/FDR cell contains only a small number of matches. See `team_fdr_factors.csv` for sample counts, xGA factors, medians and means.

## Interpretation and limitations

- This is an end-of-season calibration analysis, not a leakage-free predictive backtest. The archive contains final official fixture ratings and does not prove that every rating is the value published immediately before kickoff.
- Realised GD and xGD are outcomes. They can test FDR but must not be used as pre-match features for the same fixtures.
- Actual GD is discrete and tie-heavy. xGD provides a more stable ordering but is still noisy at team/FDR level.
- Team-specific cells should be partially pooled toward league-wide factors before implementation. One season is insufficient for unrestricted 20-team by 5-level parameters.
- xG and xGA should be modelled separately. GD alone cannot supply both attacking and clean-sheet adjustments.
- The residual venue candidates test symmetric home/away factors on top of the current predictor's FDR mapping. The selected 1.04 home / 0.96 away adjustment is intentionally small because FDR already contains venue information.

## Files

- `team_fixture_observations.csv`: all 760 team-perspective records.
- `official_fdr_calibration.csv`: league-wide outcomes by official FDR.
- `quintile_comparison.csv`: agreement and rank-correlation summary.
- `quintile_confusion_matrix.csv`: official FDR versus each realised quintile classification.
- `team_quintile_breaks.csv`: each team's numeric GD/xGD cut points.
- `team_fdr_factors.csv`: team-specific attacking and defensive factors.
- `factor_backtest_summary.csv`: holdout and rolling-origin accuracy by factor method.
- `factor_backtest_predictions.csv`: every forward prediction used in those scores.
"""


def main():
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(ARCHIVE_PATH) as archive:
        teams = read_zip_csv(archive, "official-fpl-historical/teams.csv")
        fixtures = read_zip_csv(archive, "official-fpl-historical/fixtures.csv")
        team_by_id = {as_int(team["id"]): team for team in teams}
        team_code_by_id = {team_id: as_int(team["code"]) for team_id, team in team_by_id.items()}

        elo_matches = {}
        for gameweek in range(1, 39):
            relative_path = f"elo-insights/By Tournament/Premier League/GW{gameweek}/matches.csv"
            for match in read_zip_csv(archive, relative_path):
                if not match.get("home_team") or not match.get("away_team"):
                    continue
                key = (gameweek, as_int(match["home_team"]), as_int(match["away_team"]))
                elo_matches[key] = match

    rows = []
    missing_xg = []
    for fixture in fixtures:
        if fixture.get("finished") != "True" or not fixture.get("event"):
            continue
        gameweek = as_int(fixture["event"])
        home_id, away_id = as_int(fixture["team_h"]), as_int(fixture["team_a"])
        key = (gameweek, team_code_by_id[home_id], team_code_by_id[away_id])
        elo = elo_matches.get(key)
        if not elo:
            missing_xg.append(key)
            continue
        fixture_id = as_int(fixture["id"])
        home_score, away_score = as_int(fixture["team_h_score"]), as_int(fixture["team_a_score"])
        home_xg = as_float(elo["home_expected_goals_xg"])
        away_xg = as_float(elo["away_expected_goals_xg"])
        perspectives = (
            (home_id, away_id, True, home_score, away_score, home_xg, away_xg, as_int(fixture["team_h_difficulty"])),
            (away_id, home_id, False, away_score, home_score, away_xg, home_xg, as_int(fixture["team_a_difficulty"])),
        )
        for team_id, opponent_id, is_home, goals_for, goals_against, xg, xga, official_fdr in perspectives:
            rows.append({
                "row_id": f"{fixture_id}:{team_id}",
                "fixture_id": fixture_id,
                "gameweek": gameweek,
                "team": team_by_id[team_id]["name"],
                "team_short": team_by_id[team_id]["short_name"],
                "opponent": team_by_id[opponent_id]["name"],
                "opponent_short": team_by_id[opponent_id]["short_name"],
                "venue": "H" if is_home else "A",
                "official_fdr": official_fdr,
                "goals_for": goals_for,
                "goals_against": goals_against,
                "goal_difference": goals_for - goals_against,
                "xg": xg,
                "xga": xga,
                "xg_difference": xg - xga,
            })

    if missing_xg or len(rows) != 760:
        raise RuntimeError(f"Expected 760 joined team-fixtures; got {len(rows)} with {len(missing_xg)} missing xG matches.")

    global_gd = rank_quintiles(rows, "goal_difference")
    global_xgd = rank_quintiles(rows, "xg_difference")
    team_gd = rank_quintiles(rows, "goal_difference", "team")
    team_xgd = rank_quintiles(rows, "xg_difference", "team")
    for row in rows:
        row["global_gd_quintile"] = global_gd[row["row_id"]]
        row["global_xgd_quintile"] = global_xgd[row["row_id"]]
        row["team_gd_quintile"] = team_gd[row["row_id"]]
        row["team_xgd_quintile"] = team_xgd[row["row_id"]]

    team_baselines = {}
    for team in sorted({row["team"] for row in rows}):
        team_rows = [row for row in rows if row["team"] == team]
        team_baselines[team] = {
            "median_xg": median([row["xg"] for row in team_rows]),
            "median_xga": median([row["xga"] for row in team_rows]),
        }
        for row in team_rows:
            row["xg_factor_vs_team_median"] = row["xg"] / team_baselines[team]["median_xg"]
            row["defensive_factor_vs_team_median"] = team_baselines[team]["median_xga"] / max(row["xga"], 0.05)

    add_pre_match_baselines(rows)

    calibration = []
    for fdr in range(1, 6):
        group = [row for row in rows if row["official_fdr"] == fdr]
        calibration.append({
            "fdr": fdr,
            "matches": len(group),
            "mean_goals_for": average([row["goals_for"] for row in group]),
            "mean_goals_against": average([row["goals_against"] for row in group]),
            "mean_goal_difference": average([row["goal_difference"] for row in group]),
            "mean_xg": average([row["xg"] for row in group]),
            "mean_xga": average([row["xga"] for row in group]),
            "mean_xg_difference": average([row["xg_difference"] for row in group]),
            "pooled_attack_factor_vs_team_median": average([row["xg_factor_vs_team_median"] for row in group]),
            "pooled_defensive_factor_vs_team_median": average([row["defensive_factor_vs_team_median"] for row in group]),
        })

    team_breaks = []
    factor_rows = []
    for team in sorted({row["team"] for row in rows}):
        team_rows = [row for row in rows if row["team"] == team]
        gd_values = [row["goal_difference"] for row in team_rows]
        xgd_values = [row["xg_difference"] for row in team_rows]
        team_breaks.append({
            "team": team,
            **{f"gd_q{int(probability * 100)}": quantile(gd_values, probability) for probability in (0.2, 0.4, 0.6, 0.8)},
            **{f"xgd_q{int(probability * 100)}": quantile(xgd_values, probability) for probability in (0.2, 0.4, 0.6, 0.8)},
        })
        for fdr in range(1, 6):
            group = [row for row in team_rows if row["official_fdr"] == fdr]
            if not group:
                continue
            factor_rows.append({
                "team": team,
                "fdr": fdr,
                "matches": len(group),
                "team_median_xg": team_baselines[team]["median_xg"],
                "mean_xg": average([row["xg"] for row in group]),
                "median_xg": median([row["xg"] for row in group]),
                "attack_factor_vs_team_median_xg": average([row["xg"] for row in group]) / team_baselines[team]["median_xg"],
                "team_median_xga": team_baselines[team]["median_xga"],
                "mean_xga": average([row["xga"] for row in group]),
                "median_xga": median([row["xga"] for row in group]),
                "defensive_factor_vs_team_median_xga": team_baselines[team]["median_xga"] / max(average([row["xga"] for row in group]), 0.05),
                "mean_goal_difference": average([row["goal_difference"] for row in group]),
                "mean_xg_difference": average([row["xg_difference"] for row in group]),
            })

    comparisons = []
    for label, key in (
        ("Global GD quintile", "global_gd_quintile"),
        ("Global xGD quintile", "global_xgd_quintile"),
        ("Team-relative GD quintile", "team_gd_quintile"),
        ("Team-relative xGD quintile", "team_xgd_quintile"),
    ):
        differences = [abs(row["official_fdr"] - row[key]) for row in rows]
        comparisons.append({
            "label": label,
            "exact_matches": sum(value == 0 for value in differences),
            "exact_match_pct": 100 * sum(value == 0 for value in differences) / len(rows),
            "within_one": sum(value <= 1 for value in differences),
            "within_one_pct": 100 * sum(value <= 1 for value in differences) / len(rows),
            "mean_absolute_level_error": average(differences),
            "spearman": spearman([row["official_fdr"] for row in rows], [row[key] for row in rows]),
        })

    confusion_rows = []
    for label, key in (
        ("Global GD quintile", "global_gd_quintile"),
        ("Global xGD quintile", "global_xgd_quintile"),
        ("Team-relative GD quintile", "team_gd_quintile"),
        ("Team-relative xGD quintile", "team_xgd_quintile"),
    ):
        for official_fdr in range(1, 6):
            counts = Counter(row[key] for row in rows if row["official_fdr"] == official_fdr)
            for outcome_quintile in range(1, 6):
                confusion_rows.append({
                    "classification": label,
                    "official_fdr": official_fdr,
                    "outcome_quintile": outcome_quintile,
                    "matches": counts[outcome_quintile],
                })

    neutral_factors = {fdr: 1.0 for fdr in range(1, 6)}
    mild_factors = {1: 1.15, 2: 1.075, 3: 1.0, 4: 0.925, 5: 0.85}
    current_predictor_factors = {1: 1.30, 2: 1.18, 3: 1.00, 4: 0.79, 5: 0.61}
    venue_candidates = (0.00, 0.02, 0.04, 0.06)
    observed_factors = {
        row["fdr"]: row["pooled_attack_factor_vs_team_median"]
        / next(item["pooled_attack_factor_vs_team_median"] for item in calibration if item["fdr"] == 3)
        for row in calibration
    }
    first_half_factors = fit_fdr_factors([row for row in rows if row["gameweek"] <= 19])
    first_half_team_factors = fit_team_fdr_factors([row for row in rows if row["gameweek"] <= 19])
    factor_predictions = []

    holdout_rows = [row for row in rows if row["gameweek"] >= 20]
    for method, factors in (
        ("Neutral 1.0", neutral_factors),
        ("Mild preset", mild_factors),
        ("GW1-19 fitted", first_half_factors),
        ("Full-season observed (hindsight)", observed_factors),
    ):
        factor_predictions.extend(evaluate_factor_method(
            holdout_rows,
            lambda row, mapping=factors: mapping[row["official_fdr"]],
            method,
            "GW20-38 holdout",
        ))
    factor_predictions.extend(evaluate_factor_method(
        holdout_rows,
        lambda row: first_half_team_factors[row["team"]][row["official_fdr"]],
        "GW1-19 fitted team-shrunk",
        "GW20-38 holdout",
    ))
    for adjustment in venue_candidates:
        factor_predictions.extend(evaluate_factor_method(
            holdout_rows,
            lambda row, delta=adjustment: (
                current_predictor_factors[row["official_fdr"]]
                * (1 + delta if row["venue"] == "H" else 1 - delta)
            ),
            f"Current predictor + venue +/-{adjustment:.2f}",
            "GW20-38 holdout",
        ))

    rolling_rows = [row for row in rows if row["gameweek"] >= 10]
    rolling_factors = {
        gameweek: fit_fdr_factors([row for row in rows if row["gameweek"] < gameweek])
        for gameweek in sorted({row["gameweek"] for row in rolling_rows})
    }
    rolling_team_factors = {
        gameweek: fit_team_fdr_factors([row for row in rows if row["gameweek"] < gameweek])
        for gameweek in sorted({row["gameweek"] for row in rolling_rows})
    }
    for method, factor_getter in (
        ("Neutral 1.0", lambda row: 1.0),
        ("Mild preset", lambda row: mild_factors[row["official_fdr"]]),
        ("Expanding fitted", lambda row: rolling_factors[row["gameweek"]][row["official_fdr"]]),
        ("Full-season observed (hindsight)", lambda row: observed_factors[row["official_fdr"]]),
    ):
        factor_predictions.extend(evaluate_factor_method(
            rolling_rows,
            factor_getter,
            method,
            "GW10-38 rolling",
        ))
    factor_predictions.extend(evaluate_factor_method(
        rolling_rows,
        lambda row: rolling_team_factors[row["gameweek"]][row["team"]][row["official_fdr"]],
        "Expanding fitted team-shrunk",
        "GW10-38 rolling",
    ))
    for adjustment in venue_candidates:
        factor_predictions.extend(evaluate_factor_method(
            rolling_rows,
            lambda row, delta=adjustment: (
                current_predictor_factors[row["official_fdr"]]
                * (1 + delta if row["venue"] == "H" else 1 - delta)
            ),
            f"Current predictor + venue +/-{adjustment:.2f}",
            "GW10-38 rolling",
        ))
    factor_summaries = summarize_factor_backtest(factor_predictions)

    observation_fields = [
        "row_id", "fixture_id", "gameweek", "team", "team_short", "opponent", "opponent_short", "venue",
        "official_fdr", "goals_for", "goals_against", "goal_difference", "xg", "xga", "xg_difference",
        "global_gd_quintile", "global_xgd_quintile", "team_gd_quintile", "team_xgd_quintile",
        "pre_match_xg_baseline", "xg_factor_vs_team_median", "defensive_factor_vs_team_median",
    ]
    write_csv(OUTPUT_DIR / "team_fixture_observations.csv", rows, observation_fields)
    write_csv(OUTPUT_DIR / "official_fdr_calibration.csv", calibration, list(calibration[0]))
    write_csv(OUTPUT_DIR / "quintile_comparison.csv", comparisons, list(comparisons[0]))
    write_csv(OUTPUT_DIR / "quintile_confusion_matrix.csv", confusion_rows, list(confusion_rows[0]))
    write_csv(OUTPUT_DIR / "team_quintile_breaks.csv", team_breaks, list(team_breaks[0]))
    write_csv(OUTPUT_DIR / "team_fdr_factors.csv", factor_rows, list(factor_rows[0]))
    write_csv(OUTPUT_DIR / "factor_backtest_predictions.csv", factor_predictions, list(factor_predictions[0]))
    write_csv(OUTPUT_DIR / "factor_backtest_summary.csv", factor_summaries, list(factor_summaries[0]))
    line_chart(OUTPUT_DIR / "fdr_outcome_calibration.svg", calibration)
    heatmap(OUTPUT_DIR / "team_attack_factor_heatmap.svg", factor_rows)

    global_breaks = {
        "gd": [quantile([row["goal_difference"] for row in rows], probability) for probability in (0.2, 0.4, 0.6, 0.8)],
        "xgd": [round(quantile([row["xg_difference"] for row in rows], probability), 3) for probability in (0.2, 0.4, 0.6, 0.8)],
    }
    report = build_report(
        rows, calibration, team_breaks, factor_rows, comparisons, global_breaks,
        factor_summaries, first_half_factors,
    )
    (OUTPUT_DIR / "REPORT.md").write_text(report, encoding="utf-8")
    print(f"Wrote FDR analysis to {OUTPUT_DIR}")


if __name__ == "__main__":
    main()
