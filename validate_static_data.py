"""Reject a static publish when its FPL/Elo inputs were not freshly verified."""

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parent
STATIC_PREDICTIONS = ROOT / "data" / "static_predictions.json"
PREDICTION_WINDOWS = ROOT / "data" / "prediction_windows"
MAX_SOURCE_AGE = timedelta(hours=18)


def parse_timestamp(value, label):
    if not value:
        raise RuntimeError(f"Static predictions have no {label}.")
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(timezone.utc)
    except (AttributeError, TypeError, ValueError) as exc:
        raise RuntimeError(f"Static predictions have an invalid {label}: {value!r}") from exc


def main():
    if not STATIC_PREDICTIONS.exists():
        raise RuntimeError("Static predictions were not generated.")

    payload = json.loads(STATIC_PREDICTIONS.read_text())
    warnings = payload.get("refresh_warnings") or []
    if payload.get("used_cached_data") or warnings:
        detail = "; ".join(str(item) for item in warnings) or "cached source data was used"
        raise RuntimeError(f"Refusing to publish stale source data: {detail}")

    source_fetched_at = parse_timestamp(payload.get("source_last_fetch_at"), "source_last_fetch_at")
    source_age = datetime.now(timezone.utc) - source_fetched_at
    if source_age > MAX_SOURCE_AGE:
        raise RuntimeError(
            f"Refusing to publish source data that is {source_age} old; maximum is {MAX_SOURCE_AGE}."
        )

    teams = payload.get("teams") or []
    if len(teams) != 20:
        raise RuntimeError(f"Expected projections for 20 Premier League teams, found {len(teams)}.")
    if not payload.get("available_gameweeks"):
        raise RuntimeError("Static predictions have no available gameweeks.")
    if not any(PREDICTION_WINDOWS.rglob("*.json.gz")):
        raise RuntimeError("Static prediction windows were not generated.")

    print(f"Validated fresh static predictions; source data fetched at {source_fetched_at.isoformat()}.")


if __name__ == "__main__":
    main()
