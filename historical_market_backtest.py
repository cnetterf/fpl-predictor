"""Build a bounded historical market-xG backtest from The Odds API.

The first version evaluates fixture score forecasts only.  It deliberately does
not alter player predictions; that needs a separately validated translation
from team market xG to individual-player points.
"""

import argparse
import csv
import json
import math
import os
import zipfile
from datetime import timedelta
from pathlib import Path
from urllib.error import HTTPError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

import market_odds
import server


ROOT = Path(__file__).resolve().parent
RAW_ARCHIVE = ROOT / "archives" / "2025-26" / "fpl-2025-26-raw-sources.zip"
OUTPUT_PATH = ROOT / "data" / "market_backtests" / "2025-26.json"
HISTORICAL_URL = "https://api.the-odds-api.com/v4/historical/sports/soccer_epl/odds"


def archive_csv(member):
    with zipfile.ZipFile(RAW_ARCHIVE) as archive:
        name = next(item for item in archive.namelist() if item.endswith(member))
        return list(csv.DictReader(archive.read(name).decode("utf-8-sig").splitlines()))


def historical_fixtures():
    teams = {row["id"]: row["name"] for row in archive_csv("official-fpl-historical/teams.csv")}
    grouped = {}
    for row in archive_csv("official-fpl-historical/fixtures.csv"):
        if not row.get("finished") == "True" or not row.get("event"):
            continue
        gameweek = int(row["event"])
        grouped.setdefault(gameweek, []).append({
            "home_team": teams[row["team_h"]], "away_team": teams[row["team_a"]],
            "home_goals": int(row["team_h_score"]), "away_goals": int(row["team_a_score"]),
            "kickoff_time": market_odds.parse_time(row["kickoff_time"]),
        })
    return grouped


def fetch_snapshot(key, when):
    api_time = when.strftime("%Y-%m-%dT%H:%M:%SZ")
    query = urlencode({"apiKey": key, "regions": "uk", "markets": "h2h,totals", "oddsFormat": "decimal", "dateFormat": "iso", "date": api_time})
    request = Request(f"{HISTORICAL_URL}?{query}", headers={"User-Agent": "FPL-Predictor/1.0"})
    with urlopen(request, timeout=30) as response:
        return json.loads(response.read().decode("utf-8")), {
            "last": response.headers.get("x-requests-last"), "remaining": response.headers.get("x-requests-remaining"),
        }


def poisson_nll(goals, rate):
    return rate - goals * math.log(max(rate, 1e-9)) + math.lgamma(goals + 1)


def summarise(rows):
    if not rows:
        return {"fixtures": 0}
    pairs = [(row["home_xg"], row["home_goals"]) for row in rows] + [(row["away_xg"], row["away_goals"]) for row in rows]
    return {
        "fixtures": len(rows),
        "team_goal_mae": round(sum(abs(rate - goals) for rate, goals in pairs) / len(pairs), 4),
        "team_goal_poisson_nll": round(sum(poisson_nll(goals, rate) for rate, goals in pairs) / len(pairs), 4),
        "fixture_total_goal_mae": round(sum(abs((row["home_xg"] + row["away_xg"]) - (row["home_goals"] + row["away_goals"])) for row in rows) / len(rows), 4),
    }


def build(*, lead_days=7, max_credits=1000, force=False):
    server.load_env()
    key = os.environ.get("ODDS_API_KEY_PAID")
    if not key:
        raise RuntimeError("ODDS_API_KEY_PAID is not configured.")
    fixtures_by_gw = historical_fixtures()
    existing = json.loads(OUTPUT_PATH.read_text()) if OUTPUT_PATH.exists() else {"schema_version": 1, "season": "2025-26", "gameweeks": {}}
    existing.update({"schema_version": 1, "season": "2025-26", "benchmark": f"UK consensus snapshot {lead_days} days before each GW's first kickoff", "lead_days": lead_days})
    spent = 0
    for gameweek, fixtures in sorted(fixtures_by_gw.items()):
        previous = existing["gameweeks"].get(str(gameweek), {})
        if not force and previous.get("status") == "complete" and len(previous.get("fixtures", [])) == len(fixtures):
            continue
        requested_at = min(row["kickoff_time"] for row in fixtures) - timedelta(days=lead_days)
        if spent + 20 > max_credits:
            break
        try:
            snapshot, usage = fetch_snapshot(key, requested_at)
        except HTTPError as error:
            # Some historical snapshots have no EPL market coverage at the
            # requested early benchmark. Record that transparently and carry
            # on with the rest of the season.
            existing["gameweeks"][str(gameweek)] = {
                "requested_at": requested_at.isoformat(), "fixtures": [],
                "summary": {"fixtures": 0}, "status": "unavailable",
                "error": f"HTTP {error.code}",
            }
            OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
            OUTPUT_PATH.write_text(json.dumps(existing, separators=(",", ":")))
            print(f"GW{gameweek}: unavailable (HTTP {error.code})")
            continue
        spent += int(usage["last"] or 20)
        events = market_odds.event_lookup(snapshot.get("data", []))
        rows = []
        for fixture in fixtures:
            event = events.get((market_odds.team_key(fixture["home_team"]), market_odds.team_key(fixture["away_team"])))
            if not event:
                continue
            consensus = market_odds.median_consensus(market_odds.bookmaker_quote(event, fixture["home_team"], fixture["away_team"]))
            if not consensus or consensus.get("status") != "available":
                continue
            rows.append({
                **fixture,
                "kickoff_time": fixture["kickoff_time"].isoformat(),
                **{key: consensus[key] for key in ("home_xg", "away_xg", "bookmaker_count", "total_line")},
            })
        status = "complete" if len(rows) == len(fixtures) else "insufficient_coverage"
        existing["gameweeks"][str(gameweek)] = {"requested_at": requested_at.isoformat(), "returned_at": snapshot.get("timestamp"), "fixtures": rows, "expected_fixtures": len(fixtures), "summary": summarise(rows), "credits_last": usage["last"], "credits_remaining": usage["remaining"], "status": status}
        OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
        existing["summary"] = summarise([row for block in existing["gameweeks"].values() for row in block["fixtures"]])
        existing["credits_used_this_run"] = spent
        OUTPUT_PATH.write_text(json.dumps(existing, separators=(",", ":")))
        print(f"GW{gameweek}: {len(rows)}/{len(fixtures)} fixtures, {usage['last']} credits, {usage['remaining']} remaining")
    return existing


def diagnose(gameweeks, lead_days=1):
    server.load_env()
    key = os.environ.get("ODDS_API_KEY_PAID")
    fixtures_by_gw = historical_fixtures()
    for gameweek in gameweeks:
        fixtures = fixtures_by_gw[gameweek]
        snapshot, usage = fetch_snapshot(key, min(row["kickoff_time"] for row in fixtures) - timedelta(days=lead_days))
        events = market_odds.event_lookup(snapshot.get("data", []))
        missing = []
        for fixture in fixtures:
            event = events.get((market_odds.team_key(fixture["home_team"]), market_odds.team_key(fixture["away_team"])))
            if not event:
                missing.append(f"{fixture['home_team']} v {fixture['away_team']}: absent")
                continue
            quotes = market_odds.bookmaker_quote(event, fixture["home_team"], fixture["away_team"])
            if len(quotes) < market_odds.MIN_BOOKMAKERS:
                missing.append(f"{fixture['home_team']} v {fixture['away_team']}: {len(quotes)} eligible books")
        print(f"GW{gameweek}: {len(missing)} gaps; {usage['last']} credits; {usage['remaining']} remaining")
        for item in missing:
            print(f"  {item}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--lead-days", type=int, default=7)
    parser.add_argument("--max-credits", type=int, default=1000)
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--diagnose", nargs="+", type=int)
    args = parser.parse_args()
    if args.diagnose:
        diagnose(args.diagnose, lead_days=args.lead_days)
    else:
        result = build(lead_days=args.lead_days, max_credits=args.max_credits, force=args.force)
        print(json.dumps(result.get("summary", {}), indent=2))
