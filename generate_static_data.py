import json
from pathlib import Path

import server


OUTPUT_PATH = Path(__file__).resolve().parent / "data" / "static_predictions.json"
HORIZONS = range(1, 7)


def main():
    predictions = {}
    latest_generated_at = None
    latest_source_fetch_at = None
    latest_prediction_at = None
    used_cached_data = False
    refresh_warnings = []
    available_gameweeks = []

    seed_payload = server.APP.get_predictions(1, "ALL")
    latest_generated_at = seed_payload["generated_at"]
    latest_source_fetch_at = seed_payload.get("source_last_fetch_at")
    latest_prediction_at = seed_payload.get("last_prediction_at")
    used_cached_data = seed_payload.get("used_cached_data", False)
    available_gameweeks = seed_payload.get("available_gameweeks", [])
    if seed_payload.get("refresh_warning"):
        refresh_warnings.append(seed_payload["refresh_warning"])

    for start_index, start_gameweek in enumerate(available_gameweeks):
        predictions[str(start_gameweek)] = {}
        max_horizon = min(6, len(available_gameweeks) - start_index)
        for horizon in range(1, max_horizon + 1):
            end_gameweek = available_gameweeks[start_index + horizon - 1]
            payload = server.APP.get_predictions(horizon, "ALL", start_gameweek)
            predictions[str(start_gameweek)][str(end_gameweek)] = payload["players"]
            latest_generated_at = payload["generated_at"]
            latest_source_fetch_at = payload.get("source_last_fetch_at")
            latest_prediction_at = payload.get("last_prediction_at")
            used_cached_data = used_cached_data or payload.get("used_cached_data", False)
            if payload.get("refresh_warning"):
                refresh_warnings.append(payload["refresh_warning"])

    total_players = sum(
        len(players)
        for start_windows in predictions.values()
        for players in start_windows.values()
    )
    if total_players == 0:
        raise RuntimeError("Refusing to write empty static predictions dataset.")

    output = {
        "generated_at": latest_generated_at,
        "source_last_fetch_at": latest_source_fetch_at,
        "last_prediction_at": latest_prediction_at,
        "used_cached_data": used_cached_data,
        "refresh_warnings": refresh_warnings,
        "available_gameweeks": available_gameweeks,
        "predictions": predictions,
    }

    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT_PATH.write_text(f"{json.dumps(output, indent=2)}\n")
    print(f"Wrote static predictions to {OUTPUT_PATH}")


if __name__ == "__main__":
    main()
