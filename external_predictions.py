"""Permitted external FPL projection feeds used for independent benchmarking."""

import hashlib
import json
from datetime import datetime
from urllib.request import Request, urlopen


SOURCES = {
    "fpl_cat": {
        "label": "FPL Cat",
        "url": "https://fplcat.com/api/v1/projections",
        "attribution_url": "https://fplcat.com",
        "license": "Benchmarking permitted with attribution",
    },
    "sideline_quant": {
        "label": "Sideline Quant",
        "url": "https://sidelinequant.com/data/latest.json",
        "attribution_url": "https://sidelinequant.com",
        "license": "CC BY 4.0",
    },
}

SIDELINE_HISTORY_URL_TEMPLATE = (
    "https://fpl-quant-api.fly.dev/performance/live/gw/{gameweek}/predictions"
)


def parse_timestamp(value):
    if not value:
        return None
    return datetime.fromisoformat(str(value).replace("Z", "+00:00"))


def fetch_json(url, timeout=30):
    request = Request(url, headers={"User-Agent": "cnetterf-fpl-predictor/1.0"})
    with urlopen(request, timeout=timeout) as response:
        return json.load(response)


def normalize_fpl_cat(payload):
    return {
        "gameweek": int(payload.get("gameweek") or 0),
        "deadline_at": payload.get("deadline"),
        "captured_at": payload.get("computed_at"),
        "players": [
            {
                "player_id": row.get("id"),
                "player_name": row.get("name"),
                "team": row.get("team"),
                "position": "GKP" if row.get("position") == "GK" else row.get("position"),
                "predicted_total_points": row.get("xp"),
                "inputs": {"predicted_minutes_per_fixture": row.get("expected_minutes")},
                "components": {},
            }
            for row in payload.get("players", [])
            if row.get("id") is not None and row.get("xp") is not None
        ],
    }


def normalize_sideline_quant(payload):
    grouped = {}
    for row in payload.get("rows", []):
        player_id = row.get("player_id")
        predicted = row.get("xP")
        if player_id is None or predicted is None:
            continue
        player = grouped.setdefault(str(player_id), {
            "player_id": player_id,
            "player_name": row.get("web_name") or row.get("name"),
            "team": row.get("club") or row.get("team"),
            "position": "GKP" if row.get("position") == "GK" else row.get("position"),
            "predicted_total_points": 0.0,
            "inputs": {"predicted_minutes_per_fixture": 0.0},
            "components": {},
        })
        player["predicted_total_points"] += float(predicted or 0)
        player["inputs"]["predicted_minutes_per_fixture"] += float(row.get("e_mins") or 0)
    licence = payload.get("licence") or {}
    return {
        "gameweek": int(payload.get("gameweek") or 0),
        "deadline_at": payload.get("deadline"),
        "captured_at": payload.get("generated_at"),
        "players": list(grouped.values()),
        "license": licence.get("name"),
    }


def normalize_sideline_record(payload):
    """Normalize Sideline Quant's immutable, pre-registered historical rows."""
    columns = payload.get("columns") or []
    grouped = {}
    for raw_row in payload.get("rows", []):
        row = dict(zip(columns, raw_row)) if isinstance(raw_row, list) else raw_row
        player_id = row.get("player_id")
        predicted = row.get("xP")
        if player_id is None or predicted is None:
            continue
        player = grouped.setdefault(str(player_id), {
            "player_id": player_id,
            "predicted_total_points": 0.0,
            "inputs": {"predicted_minutes_per_fixture": 0.0},
            "components": {},
        })
        player["predicted_total_points"] += float(predicted or 0)
        player["inputs"]["predicted_minutes_per_fixture"] += float(row.get("e_mins") or 0)
    return {
        "gameweek": int(payload.get("gameweek") or 0),
        "deadline_at": payload.get("deadline"),
        "captured_at": payload.get("prediction_as_of"),
        "players": list(grouped.values()),
        "sha256": payload.get("sha256"),
    }


def fetch_sideline_record(gameweek, deadline_at, loader=None):
    """Fetch and verify one immutable Sideline Quant pre-deadline record."""
    url = SIDELINE_HISTORY_URL_TEMPLATE.format(gameweek=int(gameweek))
    payload = (loader or (lambda target: fetch_json(target, timeout=120)))(url)
    normalized = normalize_sideline_record(payload)
    validate_feed("sideline_quant", normalized, gameweek, deadline_at)
    expected_hash = normalized.get("sha256")
    encoded_rows = json.dumps(payload.get("rows", []), separators=(",", ":")).encode("utf-8")
    actual_hash = hashlib.sha256(encoded_rows).hexdigest()
    if not expected_hash or actual_hash != expected_hash:
        raise ValueError("sideline_quant historical row hash did not match its pre-registration")
    return {
        "players": normalized["players"],
        "metadata": {
            "external": True,
            "label": SOURCES["sideline_quant"]["label"],
            "captured_at": normalized["captured_at"],
            "source_url": url,
            "attribution_url": SOURCES["sideline_quant"]["attribution_url"],
            "license": SOURCES["sideline_quant"]["license"],
            "benchmark_policy": "provider_preregistered_predeadline_record",
            "sha256": expected_hash,
        },
    }


NORMALIZERS = {
    "fpl_cat": normalize_fpl_cat,
    "sideline_quant": normalize_sideline_quant,
}


def validate_feed(source, normalized, gameweek, deadline_at):
    if int(normalized.get("gameweek") or 0) != int(gameweek):
        raise ValueError(f"{source} returned GW{normalized.get('gameweek')} instead of GW{gameweek}")
    captured = parse_timestamp(normalized.get("captured_at"))
    deadline = parse_timestamp(deadline_at)
    provider_deadline = parse_timestamp(normalized.get("deadline_at"))
    if captured is None or deadline is None or captured >= deadline:
        raise ValueError(f"{source} did not provide a valid pre-deadline capture")
    if provider_deadline is not None and provider_deadline != deadline:
        raise ValueError(f"{source} deadline does not match Official FPL")
    player_ids = {str(row.get("player_id")) for row in normalized.get("players", []) if row.get("player_id") is not None}
    if len(player_ids) < 100:
        raise ValueError(f"{source} returned only {len(player_ids)} uniquely identified players")


def fetch_external_sources(gameweek, deadline_at, loader=fetch_json):
    """Fetch each independent feed without allowing one failure to block another."""
    captured = {}
    warnings = []
    for source, metadata in SOURCES.items():
        try:
            normalized = NORMALIZERS[source](loader(metadata["url"]))
            validate_feed(source, normalized, gameweek, deadline_at)
            captured[source] = {
                "players": normalized["players"],
                "metadata": {
                    "external": True,
                    "label": metadata["label"],
                    "captured_at": normalized["captured_at"],
                    "source_url": metadata["url"],
                    "attribution_url": metadata["attribution_url"],
                    "license": normalized.get("license") or metadata["license"],
                },
            }
        except (OSError, ValueError, TypeError, json.JSONDecodeError) as error:
            warnings.append(f"{metadata['label']} benchmark unavailable: {error}")
    return captured, warnings
