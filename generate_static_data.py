import gzip
import json
import shutil
from pathlib import Path

import server


OUTPUT_PATH = Path(__file__).resolve().parent / "data" / "static_predictions.json"
PREDICTION_WINDOWS_DIR = Path(__file__).resolve().parent / "data" / "prediction_windows"
BACKTEST_OUTPUT_PATH = Path(__file__).resolve().parent / "data" / "static_backtest.json"
BACKTEST_SEASONS_PATH = Path(__file__).resolve().parent / "data" / "backtest_seasons.json"
BACKTEST_SEASONS_DIR = Path(__file__).resolve().parent / "data" / "backtests"
HORIZONS = range(1, 7)
SOURCES = {
    "official": "Official FPL",
    "elo": "Elo Insights",
}


def compact_season_key(season_slug):
    start_year, end_year = season_slug.split("-", 1)
    return f"{start_year}-{end_year[-2:]}"


def load_backtest_manifest():
    if BACKTEST_SEASONS_PATH.exists():
        return json.loads(BACKTEST_SEASONS_PATH.read_text())
    return {"schema_version": 1, "default_season": None, "seasons": []}


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

    seed_payload = server.APP.get_predictions(1, "ALL", source="official")
    latest_generated_at = seed_payload["generated_at"]
    latest_source_fetch_at = seed_payload.get("source_last_fetch_at")
    latest_prediction_at = seed_payload.get("last_prediction_at")
    used_cached_data = seed_payload.get("used_cached_data", False)
    available_gameweeks = seed_payload.get("available_gameweeks", [])
    fixture_model = seed_payload.get("fixture_model", {})
    if seed_payload.get("refresh_warning"):
        refresh_warnings.append(seed_payload["refresh_warning"])

    if PREDICTION_WINDOWS_DIR.exists():
        shutil.rmtree(PREDICTION_WINDOWS_DIR)
    PREDICTION_WINDOWS_DIR.mkdir(parents=True)

    for source_key, source_label in SOURCES.items():
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
                source_metadata[source_key] = {
                    "latest_gameweek": payload.get("source_latest_gameweek"),
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
        "available_gameweeks": available_gameweeks,
        "teams": sorted(prediction_teams),
        "default_source": "official",
        "fixture_model": fixture_model,
        "prediction_windows_base_url": "./data/prediction_windows",
        "sources": source_payloads,
    }

    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT_PATH.write_text(json.dumps(output, separators=(",", ":")))
    print(f"Wrote static predictions to {OUTPUT_PATH}")
    print(f"Wrote static prediction window files to {PREDICTION_WINDOWS_DIR}")

    write_backtest_season(server.APP.get_backtest_dataset())


if __name__ == "__main__":
    main()
