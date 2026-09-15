"""Capture current EPL market odds for the static Gameweek view.

Only a compact consensus and audit fields are published.  The API key remains
in the local environment or GitHub Actions secret and is never sent to clients.
"""

import json
import math
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from statistics import median
from urllib.parse import urlencode
from urllib.request import Request, urlopen


ROOT = Path(__file__).resolve().parent
OUTPUT_PATH = ROOT / "data" / "market_odds.json"
ODDS_API_URL = "https://api.the-odds-api.com/v4/sports/soccer_epl/odds"
MIN_BOOKMAKERS = 3

TEAM_ALIASES = {
    "ars": "arsenal",
    "avl": "astonvilla",
    "bou": "bournemouth",
    "bre": "brentford",
    "bha": "brighton",
    "brightonandhovealbion": "brighton",
    "brightonhovealbion": "brighton",
    "che": "chelsea",
    "cov": "coventrycity",
    "coventry": "coventrycity",
    "cry": "crystalpalace",
    "eve": "everton",
    "ful": "fulham",
    "hul": "hullcity",
    "hull": "hullcity",
    "ips": "ipswichtown",
    "ipswich": "ipswichtown",
    "lee": "leeds",
    "leedsunited": "leeds",
    "liv": "liverpool",
    "mci": "mancity",
    "manchestercity": "mancity",
    "mun": "manutd",
    "manutd": "manutd",
    "manunited": "manutd",
    "manchesterunited": "manutd",
    "new": "newcastle",
    "newcastleunited": "newcastle",
    "nfo": "nottinghamforest",
    "nottmforest": "nottinghamforest",
    "nottinghamforest": "nottinghamforest",
    "sun": "sunderland",
    "tot": "spurs",
    "spurs": "spurs",
    "tottenham": "spurs",
    "tottenhamhotspur": "spurs",
    "westham": "westhamunited",
    "westhamunited": "westhamunited",
    "wolves": "wolverhamptonwanderers",
    "wolverhampton": "wolverhamptonwanderers",
    "wolverhamptonwanderers": "wolverhamptonwanderers",
}


def now_utc():
    return datetime.now(timezone.utc)


def parse_time(value):
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None


def team_key(name):
    normalized = re.sub(r"[^a-z0-9]", "", str(name or "").lower())
    return TEAM_ALIASES.get(normalized, normalized)


def normalized_probabilities(prices):
    implied = [1 / float(price) for price in prices if float(price) > 1]
    if len(implied) != len(prices) or not implied:
        return None
    total = sum(implied)
    return [value / total for value in implied]


def poisson_probabilities(rate, maximum=12):
    values = [math.exp(-rate)]
    for goals in range(1, maximum + 1):
        values.append(values[-1] * rate / goals)
    return values


def score_probabilities(home_rate, away_rate, total_line):
    home = poisson_probabilities(home_rate)
    away = poisson_probabilities(away_rate)
    home_win = draw = away_win = over = under = 0.0
    for home_goals, home_probability in enumerate(home):
        for away_goals, away_probability in enumerate(away):
            probability = home_probability * away_probability
            if home_goals > away_goals:
                home_win += probability
            elif home_goals == away_goals:
                draw += probability
            else:
                away_win += probability
            if home_goals + away_goals > total_line:
                over += probability
            else:
                under += probability
    return home_win, draw, away_win, over, under


def fit_goal_rates(h2h, totals, total_line):
    """Fit independent-Poisson goal rates to de-vigged 1X2 and O/U prices."""
    target = (*h2h, *totals)

    def loss(home_rate, away_rate):
        predicted = score_probabilities(home_rate, away_rate, total_line)
        return sum((actual - expected) ** 2 for actual, expected in zip(target, predicted))

    best = (float("inf"), 1.4, 1.4)
    # A coarse global pass prevents a local solution in lopsided fixtures.
    for home_step in range(2, 33):
        for away_step in range(2, 33):
            home_rate = home_step / 10
            away_rate = away_step / 10
            candidate = (loss(home_rate, away_rate), home_rate, away_rate)
            if candidate < best:
                best = candidate
    # Coordinate refinement produces stable display values without SciPy.
    for step in (0.05, 0.01):
        for _ in range(20):
            improved = best
            for home_offset, away_offset in ((step, 0), (-step, 0), (0, step), (0, -step)):
                home_rate = max(0.05, best[1] + home_offset)
                away_rate = max(0.05, best[2] + away_offset)
                candidate = (loss(home_rate, away_rate), home_rate, away_rate)
                if candidate < improved:
                    improved = candidate
            if improved == best:
                break
            best = improved
    return {"home_xg": round(best[1], 4), "away_xg": round(best[2], 4), "fit_error": round(best[0], 8)}


def market_outcomes(bookmaker, market_key):
    return next((market for market in bookmaker.get("markets", []) if market.get("key") == market_key), None)


def bookmaker_quote(event, home_team, away_team):
    home_key = team_key(home_team)
    away_key = team_key(away_team)
    rows = []
    for bookmaker in event.get("bookmakers", []):
        h2h = market_outcomes(bookmaker, "h2h")
        totals = market_outcomes(bookmaker, "totals")
        if not h2h or not totals:
            continue
        h2h_prices = {team_key(row.get("name")): row.get("price") for row in h2h.get("outcomes", [])}
        try:
            h2h_probability = normalized_probabilities([
                h2h_prices[home_key], h2h_prices["draw"], h2h_prices[away_key],
            ])
        except (KeyError, TypeError, ValueError):
            continue
        grouped_totals = {}
        for outcome in totals.get("outcomes", []):
            point = outcome.get("point")
            name = str(outcome.get("name") or "").lower()
            if point is None or name not in {"over", "under"}:
                continue
            grouped_totals.setdefault(float(point), {})[name] = outcome.get("price")
        # A half-goal line avoids an unresolved push probability in this first version.
        valid_lines = [
            line for line, prices in grouped_totals.items()
            if abs(line % 1 - 0.5) < 0.001 and {"over", "under"}.issubset(prices)
        ]
        if not valid_lines:
            continue
        line = min(valid_lines, key=lambda value: abs(value - 2.5))
        try:
            total_probability = normalized_probabilities([
                grouped_totals[line]["over"], grouped_totals[line]["under"],
            ])
        except (TypeError, ValueError):
            continue
        if not h2h_probability or not total_probability:
            continue
        rows.append({
            "bookmaker": bookmaker.get("key") or bookmaker.get("title") or "unknown",
            "h2h": h2h_probability,
            "total_line": line,
            "totals": total_probability,
            "updated_at": bookmaker.get("last_update") or h2h.get("last_update") or totals.get("last_update"),
        })
    return rows


def median_consensus(quotes):
    if not quotes:
        return None
    if len(quotes) < MIN_BOOKMAKERS:
        return {
            "status": "insufficient_coverage",
            "bookmaker_count": len(quotes),
        }
    # Preserve each bookmaker's own totals line; early markets often split
    # between 2.5 and 3.5, and forcing a single line discards valid evidence.
    fitted = [fit_goal_rates(quote["h2h"], quote["totals"], quote["total_line"]) for quote in quotes]
    h2h = [median([quote["h2h"][index] for quote in quotes]) for index in range(3)]
    totals = [median([quote["totals"][index] for quote in quotes]) for index in range(2)]
    return {
        "status": "available",
        "bookmaker_count": len(quotes),
        "bookmakers": sorted(quote["bookmaker"] for quote in quotes),
        "total_line": median([quote["total_line"] for quote in quotes]),
        "home_win_probability": round(h2h[0], 6),
        "draw_probability": round(h2h[1], 6),
        "away_win_probability": round(h2h[2], 6),
        "over_probability": round(totals[0], 6),
        "under_probability": round(totals[1], 6),
        "home_xg": round(median([item["home_xg"] for item in fitted]), 4),
        "away_xg": round(median([item["away_xg"] for item in fitted]), 4),
        "fit_error": round(median([item["fit_error"] for item in fitted]), 8),
    }


def fetch_events(api_key, opener=urlopen):
    query = urlencode({
        "apiKey": api_key,
        "regions": "uk,eu",
        "markets": "h2h,totals",
        "oddsFormat": "decimal",
        "dateFormat": "iso",
    })
    request = Request(f"{ODDS_API_URL}?{query}", headers={"User-Agent": "FPL-Predictor/1.0"})
    with opener(request, timeout=30) as response:
        return json.loads(response.read().decode("utf-8"))


def load_existing(output_path=OUTPUT_PATH):
    if not output_path.exists():
        return {"schema_version": 1, "provider": "The Odds API", "gameweeks": {}}
    try:
        return json.loads(output_path.read_text())
    except (OSError, json.JSONDecodeError):
        return {"schema_version": 1, "provider": "The Odds API", "gameweeks": {}}


def fixture_records(bootstrap, fixtures_by_gameweek):
    teams = {int(team["id"]): team for team in bootstrap.get("teams", []) if team.get("id") is not None}
    deadlines = {int(event["id"]): event.get("deadline_time") for event in bootstrap.get("events", []) if event.get("id") is not None}
    records = []
    for gameweek, fixtures in fixtures_by_gameweek.items():
        for fixture in fixtures:
            home = fixture.get("home_team") or teams.get(fixture.get("home_team_id"), {}).get("short_name")
            away = fixture.get("away_team") or teams.get(fixture.get("away_team_id"), {}).get("short_name")
            if not home or not away:
                continue
            records.append({
                "gameweek": int(gameweek),
                "fixture_id": fixture.get("fixture_id") or fixture.get("id"),
                "home_team": home,
                "away_team": away,
                "kickoff_time": fixture.get("kickoff_time"),
                "deadline_time": deadlines.get(int(gameweek)),
            })
    return records


def event_lookup(events):
    return {
        (team_key(event.get("home_team")), team_key(event.get("away_team"))): event
        for event in events
    }


def refresh_current_market_odds(bootstrap, fixtures_by_gameweek, *, api_key=None, captured_at=None, opener=urlopen, output_path=OUTPUT_PATH):
    """Update future/pre-deadline records, retaining every final benchmark.

    A failed source request never removes a successful prior market capture.
    """
    captured = captured_at or now_utc()
    if captured.tzinfo is None:
        captured = captured.replace(tzinfo=timezone.utc)
    existing = load_existing(output_path)
    existing.setdefault("gameweeks", {})
    existing.update({
        "schema_version": 1,
        "provider": "The Odds API",
        "capture_policy": "latest_successful_scheduled_refresh_before_fpl_deadline",
    })
    # An explicit empty value is useful for deterministic tests and for callers
    # that intentionally disable the optional provider. Only an omitted value
    # should inherit the process environment.
    key = os.environ.get("ODDS_API_KEY") if api_key is None else api_key
    if not key:
        if existing.get("fetch_status") != "not_configured":
            existing.update({
                "fetch_status": "not_configured",
                "fetch_error": "ODDS_API_KEY is not configured.",
                "last_fetch_attempt_at": captured.isoformat(),
            })
            output_path.parent.mkdir(parents=True, exist_ok=True)
            output_path.write_text(json.dumps(existing, separators=(",", ":")))
        return existing
    try:
        events = fetch_events(key, opener=opener)
    except Exception as exc:  # Keep the last verified values visible during an outage.
        if existing.get("fetch_status") != "unavailable":
            existing.update({
                "fetch_status": "unavailable",
                "fetch_error": str(exc),
                "last_fetch_attempt_at": captured.isoformat(),
            })
            output_path.parent.mkdir(parents=True, exist_ok=True)
            output_path.write_text(json.dumps(existing, separators=(",", ":")))
        return existing

    matches = event_lookup(events)
    updated = 0
    for fixture in fixture_records(bootstrap, fixtures_by_gameweek):
        deadline = parse_time(fixture.get("deadline_time"))
        # Do not revise the past-deadline benchmark; managers could not use it.
        if deadline and captured >= deadline:
            continue
        event = matches.get((team_key(fixture["home_team"]), team_key(fixture["away_team"])))
        if not event:
            continue
        consensus = median_consensus(bookmaker_quote(event, fixture["home_team"], fixture["away_team"]))
        if not consensus:
            continue
        gameweek = existing["gameweeks"].setdefault(str(fixture["gameweek"]), {
            "deadline_time": fixture.get("deadline_time"), "fixtures": {},
        })
        fixture_key = str(fixture.get("fixture_id") or f"{fixture['home_team']}:{fixture['away_team']}")
        gameweek["fixtures"][fixture_key] = {
            **fixture,
            **consensus,
            "captured_at": captured.isoformat(),
        }
        updated += 1
    existing.update({
        "fetch_status": "available",
        "fetch_error": None,
        "last_fetch_attempt_at": captured.isoformat(),
        "updated_at": captured.isoformat(),
        "updated_fixture_count": updated,
    })
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(existing, separators=(",", ":")))
    return existing
