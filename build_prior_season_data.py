#!/usr/bin/env python3
"""Build the compact prior-season player history used for early-season projections."""

import csv
import gzip
import json
import zipfile
from collections import defaultdict
from io import TextIOWrapper
from pathlib import Path


ROOT = Path(__file__).resolve().parent
ARCHIVE_PATH = ROOT / "archives" / "2025-26" / "fpl-2025-26-raw-sources.zip"
OUTPUT_PATH = ROOT / "data" / "prior_season_history.json.gz"
ARCHIVE_ROOT = "fpl-2025-26-raw-sources"
POSITION_NAMES = {1: "GKP", 2: "DEF", 3: "MID", 4: "FWD"}


def read_csv(archive, path):
    with archive.open(f"{ARCHIVE_ROOT}/{path}") as source:
        return list(csv.DictReader(TextIOWrapper(source, encoding="utf-8")))


def as_int(value):
    return int(float(value or 0))


def as_float(value):
    return float(value or 0)


def history_row(row, round_value=None):
    return {
        "round": as_int(round_value if round_value is not None else row.get("round", row.get("gw"))),
        "minutes": as_int(row.get("minutes")),
        "starts": as_int(row.get("starts")),
        "expected_goals": round(as_float(row.get("expected_goals")), 3),
        "expected_assists": round(as_float(row.get("expected_assists")), 3),
        "goals_scored": as_int(row.get("goals_scored")),
        "assists": as_int(row.get("assists")),
        "yellow_cards": as_int(row.get("yellow_cards")),
        "bonus": as_int(row.get("bonus")),
        "recoveries": round(as_float(row.get("recoveries")), 3),
        "prior_season": True,
    }


def baselines(histories, player_metadata, stat):
    totals = defaultdict(lambda: {"minutes": 0, "stat": 0.0})
    for code, matches in histories.items():
        metadata = player_metadata.get(code)
        if not metadata:
            continue
        minutes = sum(match["minutes"] for match in matches)
        stat_total = sum(match[stat] for match in matches)
        if minutes <= 0:
            continue
        for key in (
            f'{metadata["team"]}:{metadata["position"]}',
            f'*:{metadata["position"]}',
        ):
            totals[key]["minutes"] += minutes
            totals[key]["stat"] += stat_total
    return {
        key: round(values["stat"] * 90 / values["minutes"], 4)
        for key, values in totals.items()
        if values["minutes"] > 0
    }


def position_baselines(histories, player_metadata, stat):
    rates = baselines(histories, player_metadata, stat)
    return {
        key.removeprefix("*:"): value
        for key, value in rates.items()
        if key.startswith("*:")
    }


def main():
    with zipfile.ZipFile(ARCHIVE_PATH) as archive:
        players = read_csv(archive, "official-fpl-historical/players_raw.csv")
        teams = read_csv(archive, "official-fpl-historical/teams.csv")
        team_names = {as_int(team["id"]): team["short_name"] for team in teams}
        player_by_id = {}
        player_metadata = {}
        for player in players:
            player_id = str(as_int(player["id"]))
            code = str(as_int(player["code"]))
            metadata = {
                "name": f'{player["first_name"]} {player["second_name"]}',
                "team": team_names.get(as_int(player["team"]), ""),
                "position": POSITION_NAMES[as_int(player["element_type"])],
            }
            player_by_id[player_id] = code
            player_metadata[code] = metadata

        official_histories = defaultdict(list)
        for name in archive.namelist():
            if not name.startswith(f"{ARCHIVE_ROOT}/official-fpl-historical/players/") or not name.endswith("/gw.csv"):
                continue
            with archive.open(name) as source:
                for row in csv.DictReader(TextIOWrapper(source, encoding="utf-8")):
                    code = player_by_id.get(str(as_int(row.get("element"))))
                    if code:
                        official_histories[code].append(history_row(row))

        elo_histories = defaultdict(list)
        for gameweek in range(1, 39):
            rows = read_csv(
                archive,
                f"elo-insights/By Tournament/Premier League/GW{gameweek}/player_gameweek_stats.csv",
            )
            for row in rows:
                code = player_by_id.get(str(as_int(row.get("id"))))
                if code:
                    elo_histories[code].append(history_row(row, gameweek))

    for histories in (official_histories, elo_histories):
        for matches in histories.values():
            matches.sort(key=lambda match: match["round"])

    output = {
        "season": "2025-2026",
        "next_season": "2026-2027",
        "player_metadata": player_metadata,
        "sources": {
            "official": {
                "histories_by_code": dict(official_histories),
                "team_position_xg_per90": baselines(official_histories, player_metadata, "expected_goals"),
                "team_position_xa_per90": baselines(official_histories, player_metadata, "expected_assists"),
                "position_bonus_per90": position_baselines(official_histories, player_metadata, "bonus"),
            },
            "elo": {
                "histories_by_code": dict(elo_histories),
                "team_position_xg_per90": baselines(elo_histories, player_metadata, "expected_goals"),
                "team_position_xa_per90": baselines(elo_histories, player_metadata, "expected_assists"),
                "position_bonus_per90": position_baselines(elo_histories, player_metadata, "bonus"),
            },
        },
    }
    OUTPUT_PATH.write_bytes(gzip.compress(json.dumps(output, separators=(",", ":")).encode("utf-8"), mtime=0))
    print(f"Wrote prior-season data to {OUTPUT_PATH}")
    print(f"Official histories: {len(official_histories)}; Elo histories: {len(elo_histories)}")


if __name__ == "__main__":
    main()
