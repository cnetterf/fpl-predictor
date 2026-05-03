import json
from pathlib import Path

import server


OUTPUT_PATH = Path(__file__).resolve().parent / "data" / "static_predictions.json"
HORIZONS = range(1, 7)


def main():
    predictions = {}
    latest_generated_at = None

    for horizon in HORIZONS:
        payload = server.APP.get_predictions(horizon, "ALL")
        predictions[str(horizon)] = payload["players"]
        latest_generated_at = payload["generated_at"]

    output = {
        "generated_at": latest_generated_at,
        "source_last_fetch_at": server.APP.cache.data.get("last_fetch_at"),
        "last_prediction_at": server.APP.cache.data.get("last_prediction_at"),
        "predictions": predictions,
    }

    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT_PATH.write_text(json.dumps(output, indent=2))
    print(f"Wrote static predictions to {OUTPUT_PATH}")


if __name__ == "__main__":
    main()

