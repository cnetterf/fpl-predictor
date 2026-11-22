"""Archive raw historical EPL odds snapshots for reproducible analysis.

The archive deliberately excludes the API key. Each compressed file contains
the raw provider response plus the public query parameters needed to interpret
it. Existing valid snapshots are reused unless ``--force`` is supplied.
"""

import argparse
import csv
import gzip
import hashlib
import io
import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.request import Request, urlopen

import historical_market_backtest as historical
import market_odds
import server


ROOT = Path(__file__).resolve().parent
RAW_ROOT = ROOT / "data" / "market_backtests" / "raw"
DEFAULT_OUTPUT_DIR = RAW_ROOT / "2025-26"
AVAILABLE_SEASONS = tuple(f"{year}-{str(year + 1)[-2:]}" for year in range(2020, 2026))
FPL_ARCHIVE_BASE_URL = "https://raw.githubusercontent.com/vaastav/Fantasy-Premier-League/master/data"


def compact_json_bytes(payload):
    return json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8")


def write_snapshot(path, payload):
    raw = compact_json_bytes(payload)
    compressed = gzip.compress(raw, compresslevel=9, mtime=0)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(compressed)
    return {
        "uncompressed_bytes": len(raw),
        "compressed_bytes": len(compressed),
        "sha256": hashlib.sha256(compressed).hexdigest(),
    }


def read_snapshot(path):
    return json.loads(gzip.decompress(path.read_bytes()))


def download_bytes(url, opener=urlopen):
    request = Request(url, headers={"User-Agent": "FPL-Predictor/1.0"})
    with opener(request, timeout=30) as response:
        return response.read()


def cached_source_csv(season, filename, *, opener=urlopen):
    season_root = RAW_ROOT / season
    output_path = season_root / f"source-{filename}.gz"
    url = f"{FPL_ARCHIVE_BASE_URL}/{season}/{filename}"
    if output_path.exists():
        raw = gzip.decompress(output_path.read_bytes())
    else:
        raw = download_bytes(url, opener=opener)
        season_root.mkdir(parents=True, exist_ok=True)
        output_path.write_bytes(gzip.compress(raw, compresslevel=9, mtime=0))
    return raw, {
        "url": url,
        "file": output_path.name,
        "sha256": hashlib.sha256(raw).hexdigest(),
        "bytes": len(raw),
    }


def historical_fixtures_for_season(season, *, opener=urlopen):
    teams_raw, teams_source = cached_source_csv(season, "teams.csv", opener=opener)
    fixtures_raw, fixtures_source = cached_source_csv(season, "fixtures.csv", opener=opener)
    teams = {
        row["id"]: row["name"]
        for row in csv.DictReader(io.StringIO(teams_raw.decode("utf-8-sig")))
    }
    grouped = {}
    for row in csv.DictReader(io.StringIO(fixtures_raw.decode("utf-8-sig"))):
        if row.get("finished") != "True" or not row.get("event") or not row.get("kickoff_time"):
            continue
        gameweek = int(row["event"])
        grouped.setdefault(gameweek, []).append({
            "home_team": teams[row["team_h"]],
            "away_team": teams[row["team_a"]],
            "home_goals": int(row["team_h_score"]),
            "away_goals": int(row["team_a_score"]),
            "kickoff_time": market_odds.parse_time(row["kickoff_time"]),
        })
    fixture_count = sum(map(len, grouped.values()))
    invalid_gameweeks = sorted(set(grouped).difference(range(1, 39)))
    if fixture_count != 380 or invalid_gameweeks:
        raise RuntimeError(
            f"Expected 380 fixtures assigned within gameweeks 1-38 for {season}; "
            f"found {fixture_count} fixtures and invalid gameweeks {invalid_gameweeks}."
        )
    return grouped, {
        "teams": teams_source,
        "fixtures": fixtures_source,
        "empty_gameweeks": sorted(set(range(1, 39)).difference(grouped)),
    }


def fixture_coverage(response, fixtures):
    events = market_odds.event_lookup(response.get("data", []))
    expected = {
        (market_odds.team_key(row["home_team"]), market_odds.team_key(row["away_team"]))
        for row in fixtures
    }
    matched = sorted(expected.intersection(events))
    missing = sorted(expected.difference(events))
    return len(matched), [f"{home}:{away}" for home, away in missing]


def archive(
    *,
    output_dir=DEFAULT_OUTPUT_DIR,
    season="2025-26",
    lead_hours=24,
    fixtures_by_gameweek=None,
    source_files=None,
    force=False,
    fetcher=historical.fetch_snapshot,
):
    server.load_env()
    api_key = os.environ.get("ODDS_API_KEY_PAID")

    if fixtures_by_gameweek is None:
        if season == "2025-26":
            fixtures_by_gameweek = historical.historical_fixtures()
            source_files = source_files or {
                "fixtures": {"source": "archives/2025-26/fpl-2025-26-raw-sources.zip"},
                "teams": {"source": "archives/2025-26/fpl-2025-26-raw-sources.zip"},
            }
        else:
            fixtures_by_gameweek, source_files = historical_fixtures_for_season(season)
    manifest_path = output_dir / "manifest.json"
    try:
        previous_manifest = json.loads(manifest_path.read_text()) if manifest_path.exists() else {}
    except (OSError, json.JSONDecodeError):
        previous_manifest = {}
    verified_at = datetime.now(timezone.utc).isoformat()
    manifest = {
        "schema_version": 1,
        "provider": "The Odds API",
        "season": season,
        "archive_type": "raw_historical_odds_snapshots",
        "source_files": source_files or {},
        "query": {
            "sport": "soccer_epl",
            "regions": ["uk"],
            "markets": ["h2h", "totals"],
            "odds_format": "decimal",
            "lead_hours": lead_hours,
            "benchmark": f"{lead_hours} hour(s) before each gameweek's first kickoff",
        },
        "created_at": previous_manifest.get("created_at") or previous_manifest.get("archived_at") or verified_at,
        "empty_gameweeks": list((source_files or {}).get("empty_gameweeks", [])),
        "gameweeks": {},
    }

    credits_used = 0
    latest_remaining = None
    for gameweek, fixtures in sorted(fixtures_by_gameweek.items()):
        requested_at = min(row["kickoff_time"] for row in fixtures) - timedelta(hours=lead_hours)
        filename = f"gw{gameweek:02d}.json.gz"
        output_path = output_dir / filename
        response = None
        usage = {"last": "0", "remaining": latest_remaining}
        reused = False

        if output_path.exists() and not force:
            try:
                stored = read_snapshot(output_path)
                if stored.get("requested_at") == requested_at.isoformat() and "data" in stored.get("response", {}):
                    response = stored["response"]
                    reused = True
            except (OSError, ValueError, json.JSONDecodeError):
                response = None

        if response is None:
            if not api_key:
                raise RuntimeError(
                    f"ODDS_API_KEY_PAID is required to fetch missing snapshot {season} {filename}."
                )
            response, usage = fetcher(api_key, requested_at)
            credits_used += int(usage.get("last") or 0)
            latest_remaining = usage.get("remaining")
            stored = {
                "schema_version": 1,
                "provider": "The Odds API",
                "requested_at": requested_at.isoformat(),
                "query": {
                    "sport": "soccer_epl",
                    "regions": ["uk"],
                    "markets": ["h2h", "totals"],
                    "odds_format": "decimal",
                },
                "response": response,
            }
            file_details = write_snapshot(output_path, stored)
        else:
            compressed = output_path.read_bytes()
            file_details = {
                "uncompressed_bytes": len(gzip.decompress(compressed)),
                "compressed_bytes": len(compressed),
                "sha256": hashlib.sha256(compressed).hexdigest(),
            }

        matched, missing = fixture_coverage(response, fixtures)
        manifest["gameweeks"][str(gameweek)] = {
            "requested_at": requested_at.isoformat(),
            "returned_at": response.get("timestamp"),
            "file": filename,
            "raw_event_count": len(response.get("data", [])),
            "expected_fixture_count": len(fixtures),
            "matched_fixture_count": matched,
            "missing_fixtures": missing,
            "reused": reused,
            **file_details,
        }
        print(
            f"GW{gameweek}: {matched}/{len(fixtures)} fixtures; "
            f"{file_details['compressed_bytes']:,} compressed bytes; "
            f"{'reused' if reused else str(usage.get('last') or 0) + ' credits'}"
        )

    incomplete = {
        gameweek: row["missing_fixtures"]
        for gameweek, row in manifest["gameweeks"].items()
        if row["matched_fixture_count"] != row["expected_fixture_count"]
    }
    previous_credits = int(
        previous_manifest.get("credits_used_to_create_archive")
        or previous_manifest.get("credits_used_this_run")
        or 0
    )
    manifest.update({
        "last_verified_at": verified_at,
        "gameweek_count": len(manifest["gameweeks"]),
        "expected_fixture_count": sum(row["expected_fixture_count"] for row in manifest["gameweeks"].values()),
        "matched_fixture_count": sum(row["matched_fixture_count"] for row in manifest["gameweeks"].values()),
        "compressed_bytes": sum(row["compressed_bytes"] for row in manifest["gameweeks"].values()),
        "credits_used_this_run": credits_used,
        "credits_used_to_create_archive": previous_credits or credits_used,
        "credits_remaining_after_run": latest_remaining or previous_manifest.get("credits_remaining_after_run"),
        "status": "complete" if not incomplete else "complete_with_coverage_gaps",
        "incomplete_gameweeks": incomplete,
    })
    output_dir.mkdir(parents=True, exist_ok=True)
    manifest_path.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n")
    represented_gameweeks = set(map(int, manifest["gameweeks"])) | set(manifest["empty_gameweeks"])
    if represented_gameweeks != set(range(1, 39)):
        raise RuntimeError(
            "Historical raw archive does not account for every gameweek; "
            f"represented {sorted(represented_gameweeks)}."
        )
    return manifest


def lead_output_dir(season, lead_hours):
    # Keep the original one-day layout stable for the already-downloaded archive.
    if lead_hours == 24:
        return RAW_ROOT / season
    return RAW_ROOT / season / f"lead-{lead_hours}h"


def archive_collection(seasons, lead_hours_values, *, force=False):
    results = []
    for season in seasons:
        fixtures, sources = historical_fixtures_for_season(season)
        for lead_hours in lead_hours_values:
            print(f"\n=== {season}, lead {lead_hours}h ===", flush=True)
            result = archive(
                output_dir=lead_output_dir(season, lead_hours),
                season=season,
                lead_hours=lead_hours,
                fixtures_by_gameweek=fixtures,
                source_files=sources,
                force=force,
            )
            results.append({
                "season": season,
                "lead_hours": lead_hours,
                "status": result["status"],
                "matched_fixture_count": result["matched_fixture_count"],
                "expected_fixture_count": result["expected_fixture_count"],
                "credits_used_this_run": result["credits_used_this_run"],
                "credits_remaining_after_run": result["credits_remaining_after_run"],
            })
    return results


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--season", action="append", choices=AVAILABLE_SEASONS)
    parser.add_argument("--all-seasons", action="store_true")
    parser.add_argument("--lead-hours", action="append", type=int)
    parser.add_argument("--lead-days", type=int, help="Deprecated convenience alias for one lead time in days.")
    parser.add_argument("--force", action="store_true")
    arguments = parser.parse_args()
    seasons = AVAILABLE_SEASONS if arguments.all_seasons else tuple(arguments.season or ["2025-26"])
    lead_hours_values = arguments.lead_hours or [24]
    if arguments.lead_days is not None:
        lead_hours_values = [arguments.lead_days * 24]
    results = archive_collection(seasons, lead_hours_values, force=arguments.force)
    print(json.dumps(results, indent=2))
