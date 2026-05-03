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

    for horizon in HORIZONS:
        payload = server.APP.get_predictions(horizon, "ALL")
        predictions[str(horizon)] = payload["players"]
        latest_generated_at = payload["generated_at"]
        latest_source_fetch_at = payload.get("source_last_fetch_at")
        latest_prediction_at = payload.get("last_prediction_at")
        used_cached_data = used_cached_data or payload.get("used_cached_data", False)
        available_gameweeks = payload.get("available_gameweeks", available_gameweeks)
        if payload.get("refresh_warning"):
            refresh_warnings.append(payload["refresh_warning"])

    total_players = sum(len(players) for players in predictions.values())
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
