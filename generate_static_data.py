import argparse
import csv
import gzip
import io
import json
import math
import shutil
import subprocess
from datetime import datetime
from pathlib import Path
from urllib.request import urlopen

import server


OUTPUT_PATH = Path(__file__).resolve().parent / "data" / "static_predictions.json"
PREDICTION_WINDOWS_DIR = Path(__file__).resolve().parent / "data" / "prediction_windows"
BACKTEST_OUTPUT_PATH = Path(__file__).resolve().parent / "data" / "static_backtest.json"
BACKTEST_SEASONS_PATH = Path(__file__).resolve().parent / "data" / "backtest_seasons.json"
BACKTEST_SEASONS_DIR = Path(__file__).resolve().parent / "data" / "backtests"
PREDICTION_SNAPSHOTS_PATH = Path(__file__).resolve().parent / "data" / "prediction_snapshots.json"
PREDICTION_SNAPSHOTS_DIR = Path(__file__).resolve().parent / "data" / "prediction_snapshots"
PREDICTION_SNAPSHOT_RESULTS_DIR = Path(__file__).resolve().parent / "data" / "prediction_snapshot_results"
TEAM_METADATA_PATH = Path(__file__).resolve().parent / "data" / "team_metadata.json"
HORIZONS = range(1, 7)
SOURCES = {
    "official": "Official FPL",
    "elo": "FPL-Core player stats",
}
RECOVERED_PREDICTION_BENCHMARKS = (
    {
        "gameweek": 2,
        "commit": "de867f168da4bd5c53b6f3e95f563977bf80f2e6",
        "deadline_at": "2026-08-28T17:30:00Z",
    },
    {
        "gameweek": 3,
        "commit": "ab6778d9dc4dd9a00626c1cbb17501925f03b534",
        "deadline_at": "2026-09-04T17:30:00Z",
    },
)
# FPL-Core's per-GW playerstats file preserves the official ownership snapshot
# for that GW. The ref is deliberately a commit, not main, so this historical
# import remains reproducible if the source repository moves on.
GW3_OWNERSHIP_SOURCE = {
    "gameweek": 3,
    "commit": "5c3904de9be564bead5a860772ff4a432fbcd606",
    "url": "https://raw.githubusercontent.com/olbauday/FPL-Core-Insights/5c3904de9be564bead5a860772ff4a432fbcd606/data/2026-2027/By%20Gameweek/GW3/playerstats.csv",
}
RECOVERED_GAMEWEEK_FORECAST_METRICS = {
    # The published pre-deadline window is retained in Git for GW4, while the
    # short-lived live window is intentionally removed after kickoff.  This
    # lets the Gameweek view keep showing the exact, immutable forecast.
    4: "67731104",
}


def compact_season_key(season_slug):
    start_year, end_year = season_slug.split("-", 1)
    return f"{start_year}-{end_year[-2:]}"


def write_team_metadata(bootstrap):
    """Publish stable club IDs and badge codes once for use across the frontend."""
    payload = {
        "schema_version": 1,
        "teams": [
            {
                "id": team.get("id"),
                "short_name": team.get("short_name"),
                "name": team.get("name"),
                "badge_code": team.get("code"),
            }
            for team in bootstrap.get("teams", [])
        ],
    }
    TEAM_METADATA_PATH.parent.mkdir(parents=True, exist_ok=True)
    TEAM_METADATA_PATH.write_text(json.dumps(payload, separators=(",", ":")))


def load_backtest_manifest():
    if BACKTEST_SEASONS_PATH.exists():
        return json.loads(BACKTEST_SEASONS_PATH.read_text())
    return {"schema_version": 1, "default_season": None, "seasons": []}


def load_prediction_snapshot_manifest():
    if PREDICTION_SNAPSHOTS_PATH.exists():
        return json.loads(PREDICTION_SNAPSHOTS_PATH.read_text())
    return {"schema_version": 1, "seasons": {}}


def parse_snapshot_timestamp(value):
    if not value:
        return None
    return datetime.fromisoformat(str(value).replace("Z", "+00:00"))


def compact_snapshot_players(players, ownership_by_player):
    """Keep the immutable benchmark small while retaining the audit essentials."""
    return [
        [
            player.get("player_id"),
            player.get("player_name"),
            player.get("team"),
            player.get("position"),
            round(float(player.get("predicted_total_points") or 0), 3),
            round(float((player.get("inputs") or {}).get("predicted_minutes_per_fixture") or 0), 2),
            float(ownership_by_player.get(str(player.get("player_id")), 0)),
        ]
        for player in players
    ]


def compact_snapshot_fixture_metrics(players, gameweek):
    """Keep the per-fixture forecast needed by the Gameweek view after kickoff."""
    fixtures = {}
    player_goal_sums = {}
    for player in players:
        for fixture in player.get("fixtures", []):
            if int(fixture.get("event") or 0) != int(gameweek):
                continue
            model = fixture.get("fixture_model") or {}
            home = player.get("team") if fixture.get("home") else fixture.get("opponent")
            away = fixture.get("opponent") if fixture.get("home") else player.get("team")
            if not home or not away:
                continue
            key = f"{home}:{away}"
            if key not in fixtures:
                home_xg = float((model.get("team_xg") if fixture.get("home") else model.get("opponent_xg")) or 0)
                away_xg = float((model.get("opponent_xg") if fixture.get("home") else model.get("team_xg")) or 0)
                fixtures[key] = [
                    home,
                    away,
                    round(home_xg, 4),
                    round(away_xg, 4),
                    round(float(model.get("team_clean_sheet_probability") or math.exp(-away_xg)), 6) if fixture.get("home") else round(math.exp(-away_xg), 6),
                    round(math.exp(-home_xg), 6) if fixture.get("home") else round(float(model.get("team_clean_sheet_probability") or math.exp(-home_xg)), 6),
                ]
            if float((player.get("inputs") or {}).get("predicted_minutes_per_fixture") or 0) >= 20:
                team = player.get("team")
                if team:
                    player_goal_sums[team] = player_goal_sums.get(team, 0) + float(fixture.get("predicted_goals") or 0)
    return {
        "fixtures": list(fixtures.values()),
        "player_goal_sums": [[team, round(value, 4)] for team, value in sorted(player_goal_sums.items())],
    }


def actual_points_by_event():
    actual_by_event = {}
    for player_id, summary in (server.APP.cache.data.get("element_summaries", {}) or {}).items():
        for match in summary.get("history", []):
            event = match.get("round")
            if event is not None:
                actual_by_event.setdefault(str(event), {})[str(player_id)] = match.get("total_points", 0)
    return actual_by_event


def official_actual_points_for_event(gameweek, cached_actual=None):
    """Prefer the event-level Official FPL feed over a potentially stale player cache."""
    try:
        payload = server.APP.client._get_json(f"event/{gameweek}/live/")
        points = {
            str(row.get("id")): row.get("stats", {}).get("total_points")
            for row in payload.get("elements", [])
            if row.get("id") is not None and row.get("stats", {}).get("total_points") is not None
        }
        if points:
            return points
    except (AttributeError, OSError, ValueError):
        pass
    return (cached_actual or {}).get(str(gameweek), {})


def write_snapshot_results(season_key, gameweek, snapshot, actual):
    result = {
        "schema_version": 1,
        "season": season_key,
        "gameweek": int(gameweek),
        "sources": {
            source: {
                "actual_points": [[row[0], actual.get(str(row[0]))] for row in source_payload.get("players", [])]
            }
            for source, source_payload in snapshot.get("sources", {}).items()
        },
    }
    relative_path = f"{season_key}/gw-{gameweek}.json.gz"
    target = PREDICTION_SNAPSHOT_RESULTS_DIR / relative_path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(gzip.compress(json.dumps(result, separators=(",", ":")).encode("utf-8"), mtime=0))
    return f"./data/prediction_snapshot_results/{relative_path}"


def snapshot_results_are_complete(entry):
    """A completed benchmark must have one actual-points value per forecast player."""
    results_url = entry.get("results_url")
    if not results_url:
        return False
    path = PREDICTION_SNAPSHOT_RESULTS_DIR / results_url.removeprefix("./data/prediction_snapshot_results/")
    if not path.exists():
        return False
    try:
        payload = json.loads(gzip.decompress(path.read_bytes()))
    except (OSError, json.JSONDecodeError):
        return False
    for source in payload.get("sources", {}).values():
        rows = source.get("actual_points", [])
        if not rows or any(len(row) < 2 or row[1] is None for row in rows):
            return False
    return True


def write_prediction_snapshot(season_key, gameweek, deadline_at, captured_at, source_players, bootstrap):
    """Record the last successfully generated forecast before a GW deadline.

    A later generation before the deadline replaces the prior one. A generation at
    or after the deadline never changes the benchmark: it was not information that
    could have informed a manager's selection.
    """
    captured = parse_snapshot_timestamp(captured_at)
    deadline = parse_snapshot_timestamp(deadline_at)
    if captured is None or deadline is None or captured >= deadline:
        return None

    ownership_by_player = {
        str(player.get("id")): player.get("selected_by_percent", 0)
        for player in bootstrap.get("elements", [])
    }
    relative_path = f"{season_key}/gw-{gameweek}.json.gz"
    payload = {
        "schema_version": 1,
        "season": season_key,
        "gameweek": int(gameweek),
        "deadline_at": deadline_at,
        "captured_at": captured_at,
        "benchmark_policy": "last_successful_prediction_refresh_before_deadline",
        "sources": {
            source: {
                "players": compact_snapshot_players(players, ownership_by_player),
                "fixture_metrics": compact_snapshot_fixture_metrics(players, gameweek),
            }
            for source, players in source_players.items()
        },
    }

    manifest = load_prediction_snapshot_manifest()
    season = manifest.setdefault("seasons", {}).setdefault(season_key, {"gameweeks": {}})
    existing = season["gameweeks"].get(str(gameweek))
    existing_captured = parse_snapshot_timestamp((existing or {}).get("captured_at"))
    if existing_captured is not None and existing_captured >= captured:
        return existing

    target = PREDICTION_SNAPSHOTS_DIR / relative_path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(gzip.compress(json.dumps(payload, separators=(",", ":")).encode("utf-8"), mtime=0))
    entry = {
        "deadline_at": deadline_at,
        "captured_at": captured_at,
        "data_url": f"./data/prediction_snapshots/{relative_path}",
        "status": "pending",
    }
    season["gameweeks"][str(gameweek)] = entry
    PREDICTION_SNAPSHOTS_PATH.parent.mkdir(parents=True, exist_ok=True)
    PREDICTION_SNAPSHOTS_PATH.write_text(json.dumps(manifest, separators=(",", ":")))
    print(f"Recorded pre-deadline prediction benchmark for {season_key} GW{gameweek}")
    return entry


def reconcile_prediction_snapshots(bootstrap):
    """Attach official outcomes in companion files without ever mutating forecasts."""
    manifest = load_prediction_snapshot_manifest()
    season_key = compact_season_key(server.bootstrap_season_slug(bootstrap))
    season = manifest.get("seasons", {}).get(season_key)
    if not season:
        return

    actual_by_event = actual_points_by_event()

    finished = {str(event.get("id")) for event in bootstrap.get("events", []) if event.get("finished")}
    changed = False
    for gameweek, entry in season.get("gameweeks", {}).items():
        if gameweek not in finished or snapshot_results_are_complete(entry):
            continue
        snapshot_path = PREDICTION_SNAPSHOTS_DIR / entry["data_url"].removeprefix("./data/prediction_snapshots/")
        if not snapshot_path.exists():
            continue
        snapshot = json.loads(gzip.decompress(snapshot_path.read_bytes()))
        actual = official_actual_points_for_event(gameweek, actual_by_event)
        snapshot_player_ids = {
            str(row[0])
            for source in snapshot.get("sources", {}).values()
            for row in source.get("players", [])
        }
        if not snapshot_player_ids.issubset(actual):
            print(f"Waiting for complete official actuals before reconciling {season_key} GW{gameweek}")
            continue
        entry["results_url"] = write_snapshot_results(season_key, gameweek, snapshot, actual)
        entry["status"] = "complete"
        changed = True
        print(f"Reconciled prediction benchmark for {season_key} GW{gameweek}")
    if changed:
        PREDICTION_SNAPSHOTS_PATH.write_text(json.dumps(manifest, separators=(",", ":")))


def gameweek_fixture_metadata(available_gameweeks):
    """Build static fixture timing and club metadata from the verified cache."""
    bootstrap = server.APP.cache.get_bootstrap() or {}
    teams = {int(team["id"]): team for team in bootstrap.get("teams", [])}
    available = {int(gameweek) for gameweek in available_gameweeks}
    fixtures_by_event = {}
    for summary in (server.APP.cache.data.get("element_summaries", {}) or {}).values():
        for fixture in summary.get("fixtures", []):
            event = int(fixture.get("event") or 0)
            fixture_id = fixture.get("id")
            if event not in available or fixture_id is None:
                continue
            rows = fixtures_by_event.setdefault(event, {})
            if fixture_id in rows:
                continue
            home_id = fixture.get("team_h")
            away_id = fixture.get("team_a")
            rows[fixture_id] = {
                "event": event,
                "fixture_id": fixture_id,
                "kickoff_time": fixture.get("kickoff_time"),
                "home_team": teams.get(home_id, {}).get("short_name"),
                "away_team": teams.get(away_id, {}).get("short_name"),
                "home_team_id": home_id,
                "away_team_id": away_id,
                "home_badge_code": teams.get(home_id, {}).get("code"),
                "away_badge_code": teams.get(away_id, {}).get("code"),
            }
    deadlines = {int(event["id"]): event.get("deadline_time") for event in bootstrap.get("events", [])}
    return {
        str(event): {
            "deadline_time": deadlines.get(event),
            "fixtures": sorted(rows.values(), key=lambda item: item.get("kickoff_time") or ""),
        }
        for event, rows in fixtures_by_event.items()
    }


def refresh_fixture_metadata():
    """Update only static fixture metadata without rebuilding prediction windows."""
    if not OUTPUT_PATH.exists():
        raise RuntimeError("Static predictions manifest is missing.")
    output = json.loads(OUTPUT_PATH.read_text())
    output["gameweeks"] = gameweek_fixture_metadata(output.get("available_gameweeks", []))
    output["prediction_snapshots_url"] = "./data/prediction_snapshots.json"
    output["schema_version"] = max(int(output.get("schema_version", 1)), 3)
    OUTPUT_PATH.write_text(json.dumps(output, separators=(",", ":")))
    print(f"Updated fixture metadata in {OUTPUT_PATH}")


def record_prediction_snapshot_from_published_windows():
    """Archive the current one-GW publication without rebuilding every window."""
    if not OUTPUT_PATH.exists():
        raise RuntimeError("Static predictions manifest is missing.")
    output = json.loads(OUTPUT_PATH.read_text())
    available = output.get("available_gameweeks", [])
    if not available:
        return
    gameweek = int(available[0])
    source_players = {}
    for source, source_data in output.get("sources", {}).items():
        relative_path = source_data.get("windows", {}).get(str(gameweek), {}).get(str(gameweek))
        if not relative_path:
            continue
        path = PREDICTION_WINDOWS_DIR / relative_path
        if not path.exists():
            continue
        source_players[source] = json.loads(gzip.decompress(path.read_bytes())).get("players", [])
    bootstrap = server.APP.cache.get_bootstrap() or {}
    deadline_at = next(
        (event.get("deadline_time") for event in bootstrap.get("events", []) if event.get("id") == gameweek),
        None,
    )
    write_prediction_snapshot(
        compact_season_key(server.bootstrap_season_slug(bootstrap)),
        gameweek,
        deadline_at,
        output.get("source_last_fetch_at"),
        source_players,
        bootstrap,
    )
    reconcile_prediction_snapshots(bootstrap)
    write_team_metadata(bootstrap)
    output["prediction_snapshots_url"] = "./data/prediction_snapshots.json"
    output["schema_version"] = max(int(output.get("schema_version", 1)), 4)
    OUTPUT_PATH.write_text(json.dumps(output, separators=(",", ":")))
    print("Updated published prediction benchmark metadata")


def git_file_at_commit(commit, path):
    result = subprocess.run(
        ["git", "show", f"{commit}:{path}"],
        cwd=Path(__file__).resolve().parent,
        check=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    return result.stdout


def recover_gameweek_forecast_metrics():
    """Add immutable per-fixture forecast cards to already-captured snapshots."""
    manifest = load_prediction_snapshot_manifest()
    updated = 0
    for season_key, season in manifest.get("seasons", {}).items():
        for gameweek, commit in RECOVERED_GAMEWEEK_FORECAST_METRICS.items():
            entry = season.get("gameweeks", {}).get(str(gameweek))
            if not entry or not entry.get("data_url"):
                continue
            target = PREDICTION_SNAPSHOTS_DIR / entry["data_url"].removeprefix("./data/prediction_snapshots/")
            if not target.exists():
                continue
            payload = json.loads(gzip.decompress(target.read_bytes()))
            changed = False
            for source in SOURCES:
                source_payload = payload.get("sources", {}).get(source)
                if not source_payload:
                    continue
                path = f"data/prediction_windows/{source}/{gameweek}-{gameweek}.json.gz"
                window = json.loads(gzip.decompress(git_file_at_commit(commit, path)))
                source_payload["fixture_metrics"] = compact_snapshot_fixture_metrics(window.get("players", []), gameweek)
                changed = True
            if changed:
                payload["schema_version"] = max(int(payload.get("schema_version", 1)), 2)
                target.write_bytes(gzip.compress(json.dumps(payload, separators=(",", ":")).encode("utf-8"), mtime=0))
                updated += 1
    if updated:
        print(f"Recovered immutable fixture forecasts for {updated} gameweek snapshot(s)")


def load_historical_ownership(url=GW3_OWNERSHIP_SOURCE["url"]):
    with urlopen(url, timeout=30) as response:
        rows = csv.DictReader(io.TextIOWrapper(response, encoding="utf-8"))
        return {
            str(row["id"]): float(row.get("selected_by_percent") or 0)
            for row in rows
            if row.get("id")
        }


def import_recovered_prediction_benchmarks(ownership_by_player=None):
    """Recover verifiable pre-deadline forecasts published in this repo's Git history.

    The official API does not expose historical ownership. For the initial
    directional cohort, the user approved using the archived GW3 ownership
    snapshot for both recovered GW2 and GW3 forecasts. The source and proxy are
    included in every artifact so a later true historical feed can replace it.
    """
    if not OUTPUT_PATH.exists():
        raise RuntimeError("Static predictions manifest is missing.")
    output = json.loads(OUTPUT_PATH.read_text())
    bootstrap = server.APP.cache.get_bootstrap() or {}
    season_key = compact_season_key(server.bootstrap_season_slug(bootstrap))
    ownership_by_player = ownership_by_player or load_historical_ownership()
    if not ownership_by_player:
        raise RuntimeError("Recovered GW3 ownership snapshot was empty.")

    manifest = load_prediction_snapshot_manifest()
    season = manifest.setdefault("seasons", {}).setdefault(season_key, {"gameweeks": {}})
    actual_by_event = actual_points_by_event()
    ownership_basis = {
        "type": "gw3_proxy",
        "source_gameweek": GW3_OWNERSHIP_SOURCE["gameweek"],
        "applied_gameweeks": [item["gameweek"] for item in RECOVERED_PREDICTION_BENCHMARKS],
        "source_url": GW3_OWNERSHIP_SOURCE["url"],
        "source_revision": GW3_OWNERSHIP_SOURCE["commit"],
    }

    for recovered in RECOVERED_PREDICTION_BENCHMARKS:
        gameweek = recovered["gameweek"]
        commit = recovered["commit"]
        historic_manifest = json.loads(git_file_at_commit(commit, "data/static_predictions.json"))
        captured_at = historic_manifest.get("source_last_fetch_at") or historic_manifest.get("generated_at")
        source_payloads = {}
        for source in SOURCES:
            path = f"data/prediction_windows/{source}/{gameweek}-{gameweek}.json.gz"
            try:
                window = json.loads(gzip.decompress(git_file_at_commit(commit, path)))
            except (OSError, subprocess.CalledProcessError) as error:
                raise RuntimeError(f"Could not recover {source} GW{gameweek} forecast from {commit}.") from error
            source_payloads[source] = window.get("players", [])

        relative_path = f"{season_key}/gw-{gameweek}.json.gz"
        payload = {
            "schema_version": 1,
            "season": season_key,
            "gameweek": gameweek,
            "deadline_at": recovered["deadline_at"],
            "captured_at": captured_at,
            "benchmark_policy": "recovered_predeadline_published_forecast",
            "recovered_from_commit": commit,
            "ownership_basis": ownership_basis,
            "sources": {
                source: {"players": compact_snapshot_players(players, ownership_by_player)}
                for source, players in source_payloads.items()
            },
        }
        target = PREDICTION_SNAPSHOTS_DIR / relative_path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(gzip.compress(json.dumps(payload, separators=(",", ":")).encode("utf-8"), mtime=0))
        entry = {
            "deadline_at": recovered["deadline_at"],
            "captured_at": captured_at,
            "data_url": f"./data/prediction_snapshots/{relative_path}",
            "status": "complete",
            "results_url": write_snapshot_results(season_key, gameweek, payload, actual_by_event.get(str(gameweek), {})),
            "benchmark_policy": payload["benchmark_policy"],
            "recovered_from_commit": commit,
            "ownership_basis": ownership_basis,
        }
        season["gameweeks"][str(gameweek)] = entry
        print(f"Recovered pre-deadline prediction benchmark for {season_key} GW{gameweek}")

    PREDICTION_SNAPSHOTS_PATH.parent.mkdir(parents=True, exist_ok=True)
    PREDICTION_SNAPSHOTS_PATH.write_text(json.dumps(manifest, separators=(",", ":")))
    output["prediction_snapshots_url"] = "./data/prediction_snapshots.json"
    output["schema_version"] = max(int(output.get("schema_version", 1)), 4)
    OUTPUT_PATH.write_text(json.dumps(output, separators=(",", ":")))


def write_backtest_season(backtest_output):
    """Publish a non-empty season without removing any finished-season archive."""
    available_gameweeks = backtest_output.get("available_gameweeks", [])
    manifest = load_backtest_manifest()

    if available_gameweeks:
        bootstrap = server.APP.cache.get_bootstrap()
        season_key = compact_season_key(server.bootstrap_season_slug(bootstrap))
        season_dir = BACKTEST_SEASONS_DIR / season_key
        windows_dir = season_dir / "windows"
        temporary_dir = season_dir / "windows.new"
        if temporary_dir.exists():
            shutil.rmtree(temporary_dir)
        temporary_dir.mkdir(parents=True)

        for start_gameweek in available_gameweeks:
            for end_gameweek in available_gameweeks:
                if end_gameweek < start_gameweek:
                    continue
                key = f"{start_gameweek}-{end_gameweek}"
                payload = server.APP.get_backtest_window(start_gameweek, end_gameweek)
                (temporary_dir / f"{key}.json").write_text(json.dumps(payload, separators=(",", ":")))

        if windows_dir.exists():
            shutil.rmtree(windows_dir)
        temporary_dir.rename(windows_dir)
        index_path = season_dir / "index.json"
        index_path.write_text(json.dumps(backtest_output, separators=(",", ":")))

        entry = {
            "key": season_key,
            "label": season_key.replace("-", "–"),
            "data_url": f"./data/backtests/{season_key}/index.json",
            "windows_base_url": f"./data/backtests/{season_key}/windows",
            "archived": False,
            "recompute_available": True,
        }
        seasons = [item for item in manifest.get("seasons", []) if item.get("key") != season_key]
        seasons.append(entry)
        manifest["seasons"] = sorted(seasons, key=lambda item: item["key"], reverse=True)
        manifest["default_season"] = season_key
        print(f"Wrote {len(list(windows_dir.glob('*.json')))} backtest windows for {season_key}")
    else:
        print("No eligible current-season backtest windows; retaining finished-season archives.")

    default_key = manifest.get("default_season")
    default_entry = next((item for item in manifest.get("seasons", []) if item.get("key") == default_key), None)
    if not default_entry and manifest.get("seasons"):
        default_entry = manifest["seasons"][0]
        manifest["default_season"] = default_entry["key"]
    if not default_entry:
        raise RuntimeError("No non-empty backtest season is available to publish.")

    BACKTEST_SEASONS_PATH.write_text(json.dumps(manifest, separators=(",", ":"), ensure_ascii=False))
    default_index = BACKTEST_SEASONS_DIR / default_entry["key"] / "index.json"
    shutil.copyfile(default_index, BACKTEST_OUTPUT_PATH)
    print(f"Published {default_entry['key']} as the default static backtest")


def main():
    source_payloads = {}
    prediction_teams = set()
    total_players = 0
    latest_generated_at = None
    latest_source_fetch_at = None
    latest_prediction_at = None
    used_cached_data = False
    refresh_warnings = []
    available_gameweeks = []
    fixture_model = {}
    source_metadata = {}
    secondary_source_warnings = []
    gameweek_fixtures = {}
    benchmark_source_players = {}

    bootstrap = server.APP.cache.get_bootstrap() or {}
    team_by_id = {int(team["id"]): team for team in bootstrap.get("teams", [])}
    fixture_meta = {}
    for summary in (server.APP.cache.data.get("element_summaries", {}) or {}).values():
        for fixture in summary.get("fixtures", []):
            event = fixture.get("event")
            fixture_id = fixture.get("id")
            if event is None or fixture_id is None or fixture_id in fixture_meta:
                continue
            fixture_meta[fixture_id] = {
                "id": fixture_id,
                "event": event,
                "kickoff_time": fixture.get("kickoff_time"),
                "team_h": fixture.get("team_h"),
                "team_a": fixture.get("team_a"),
            }

    seed_payload = server.APP.get_predictions(1, "ALL", source="official")
    latest_generated_at = seed_payload["generated_at"]
    latest_source_fetch_at = seed_payload.get("source_last_fetch_at")
    latest_prediction_at = seed_payload.get("last_prediction_at")
    used_cached_data = seed_payload.get("used_cached_data", False)
    available_gameweeks = seed_payload.get("available_gameweeks", [])
    fixture_model = seed_payload.get("fixture_model", {})
    available_sources = set(server.APP.available_prediction_sources())
    if seed_payload.get("refresh_warning"):
        refresh_warnings.append(seed_payload["refresh_warning"])

    if PREDICTION_WINDOWS_DIR.exists():
        shutil.rmtree(PREDICTION_WINDOWS_DIR)
    PREDICTION_WINDOWS_DIR.mkdir(parents=True)

    for source_key, source_label in SOURCES.items():
        if source_key not in available_sources:
            continue
        source_windows = {}
        source_dir = PREDICTION_WINDOWS_DIR / source_key
        source_dir.mkdir()
        for start_index, start_gameweek in enumerate(available_gameweeks):
            source_windows[str(start_gameweek)] = {}
            max_horizon = min(6, len(available_gameweeks) - start_index)
            for horizon in range(1, max_horizon + 1):
                end_gameweek = available_gameweeks[start_index + horizon - 1]
                payload = server.APP.get_predictions(horizon, "ALL", start_gameweek, source_key)
                players = payload["players"]
                if horizon == 1 and start_gameweek == available_gameweeks[0]:
                    benchmark_source_players[source_key] = players
                for player in players:
                    for fixture in player.get("fixtures", []):
                        event = fixture.get("event")
                        model = fixture.get("fixture_model") or {}
                        if event is None or not model:
                            continue
                        team_short = player.get("team")
                        opponent_short = fixture.get("opponent")
                        if not team_short or not opponent_short:
                            continue
                        home_short, away_short = (team_short, opponent_short) if fixture.get("home") else (opponent_short, team_short)
                        key = (event, home_short, away_short)
                        row = gameweek_fixtures.setdefault(str(event), {}).setdefault(key, {
                            "event": event,
                            "home_team": home_short,
                            "away_team": away_short,
                            "home_xg": None,
                            "away_xg": None,
                            "fixture_id": None,
                            "kickoff_time": None,
                            "home_team_id": None,
                            "away_team_id": None,
                            "home_badge_code": None,
                            "away_badge_code": None,
                        })
                        if fixture.get("home"):
                            row["home_xg"] = model.get("team_xg")
                            row["away_xg"] = model.get("opponent_xg")
                        else:
                            row["away_xg"] = model.get("team_xg")
                            row["home_xg"] = model.get("opponent_xg")
                        for meta in fixture_meta.values():
                            home = team_by_id.get(meta.get("team_h"), {}).get("short_name")
                            away = team_by_id.get(meta.get("team_a"), {}).get("short_name")
                            if meta.get("event") == event and home == home_short and away == away_short:
                                row["fixture_id"] = meta.get("id")
                                row["kickoff_time"] = meta.get("kickoff_time")
                                row["home_team_id"] = meta.get("team_h")
                                row["away_team_id"] = meta.get("team_a")
                                row["home_badge_code"] = team_by_id.get(meta.get("team_h"), {}).get("code")
                                row["away_badge_code"] = team_by_id.get(meta.get("team_a"), {}).get("code")
                                break
                total_players += len(players)
                prediction_teams.update(player["team"] for player in players if player.get("team"))
                relative_path = f"{source_key}/{start_gameweek}-{end_gameweek}.json.gz"
                source_windows[str(start_gameweek)][str(end_gameweek)] = relative_path
                window_output = {
                    "source": source_key,
                    "start_gameweek": start_gameweek,
                    "end_gameweek": end_gameweek,
                    "players": players,
                }
                (PREDICTION_WINDOWS_DIR / relative_path).write_bytes(
                    gzip.compress(
                        json.dumps(window_output, separators=(",", ":")).encode("utf-8"),
                        mtime=0,
                    )
                )
                latest_generated_at = payload["generated_at"]
                latest_source_fetch_at = payload.get("source_last_fetch_at")
                latest_prediction_at = payload.get("last_prediction_at")
                used_cached_data = used_cached_data or payload.get("used_cached_data", False)
                if payload.get("refresh_warning"):
                    refresh_warnings.append(payload["refresh_warning"])
                if payload.get("source_warning"):
                    secondary_source_warnings.append(payload["source_warning"])
                source_metadata[source_key] = {
                    "latest_gameweek": payload.get("source_latest_gameweek"),
                    "warning": payload.get("source_warning"),
                }
        source_payloads[source_key] = {
            "label": source_label,
            "windows": source_windows,
            **source_metadata.get(source_key, {}),
        }

    if total_players == 0:
        raise RuntimeError("Refusing to write empty static predictions dataset.")

    output = {
        "schema_version": 2,
        "generated_at": latest_generated_at,
        "source_last_fetch_at": latest_source_fetch_at,
        "last_prediction_at": latest_prediction_at,
        "used_cached_data": used_cached_data,
        "refresh_warnings": list(dict.fromkeys(refresh_warnings)),
        "secondary_source_warnings": list(dict.fromkeys(secondary_source_warnings)),
        "available_gameweeks": available_gameweeks,
        "gameweeks": {
            event: {
                "deadline_time": next((item.get("deadline_time") for item in bootstrap.get("events", []) if item.get("id") == int(event)), None),
                "fixtures": sorted(rows.values(), key=lambda item: item.get("kickoff_time") or ""),
            }
            for event, rows in gameweek_fixtures.items()
        },
        "teams": sorted(prediction_teams),
        "default_source": "official",
        "fixture_model": fixture_model,
        "prediction_windows_base_url": "./data/prediction_windows",
        "prediction_snapshots_url": "./data/prediction_snapshots.json",
        "sources": source_payloads,
    }

    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT_PATH.write_text(json.dumps(output, separators=(",", ":")))
    print(f"Wrote static predictions to {OUTPUT_PATH}")
    print(f"Wrote static prediction window files to {PREDICTION_WINDOWS_DIR}")

    if available_gameweeks and benchmark_source_players:
        benchmark_gameweek = available_gameweeks[0]
        deadline_at = next(
            (event.get("deadline_time") for event in bootstrap.get("events", []) if event.get("id") == benchmark_gameweek),
            None,
        )
        write_prediction_snapshot(
            compact_season_key(server.bootstrap_season_slug(bootstrap)),
            benchmark_gameweek,
            deadline_at,
            # This is intentionally the source-refresh time, not this build's
            # completion time: rerunning the static generator alone must not
            # manufacture a later benchmark a manager never received.
            latest_source_fetch_at,
            benchmark_source_players,
            bootstrap,
        )
    reconcile_prediction_snapshots(bootstrap)
    write_team_metadata(bootstrap)

    write_backtest_season(server.APP.get_backtest_dataset())


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--refresh-fixture-metadata", action="store_true")
    parser.add_argument("--record-prediction-snapshot", action="store_true")
    parser.add_argument("--import-recovered-prediction-benchmarks", action="store_true")
    parser.add_argument("--recover-gameweek-forecast-metrics", action="store_true")
    parser.add_argument("--reconcile-prediction-snapshots", action="store_true")
    parser.add_argument("--refresh-team-metadata", action="store_true")
    arguments = parser.parse_args()
    if arguments.refresh_fixture_metadata:
        refresh_fixture_metadata()
    elif arguments.record_prediction_snapshot:
        record_prediction_snapshot_from_published_windows()
    elif arguments.import_recovered_prediction_benchmarks:
        import_recovered_prediction_benchmarks()
    elif arguments.recover_gameweek_forecast_metrics:
        recover_gameweek_forecast_metrics()
    elif arguments.reconcile_prediction_snapshots:
        reconcile_prediction_snapshots(server.APP.client.get_bootstrap())
    elif arguments.refresh_team_metadata:
        write_team_metadata(server.APP.client.get_bootstrap())
    else:
        main()
