import csv
import json
import math
import os
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from io import StringIO
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, urlparse
from urllib.request import Request, urlopen


ROOT = Path(__file__).resolve().parent
STATIC_FILES = {
    "/": ROOT / "index.html",
    "/index.html": ROOT / "index.html",
    "/app.js": ROOT / "app.js",
}
CACHE_DIR = ROOT / "data"
CACHE_PATH = CACHE_DIR / "cache.json"
DATA_REFRESH_HOURS = 12
PREDICTION_REFRESH_HOURS = 6
ELO_INSIGHTS_BASE = "https://raw.githubusercontent.com/olbauday/FPL-Core-Insights/main/data"


def load_env():
    env_path = ROOT / ".env"
    if not env_path.exists():
        return
    for line in env_path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip())


def now_utc():
    return datetime.now(timezone.utc)


def parse_timestamp(value):
    if not value:
        return None
    return datetime.fromisoformat(value)


def clamp(value, low, high):
    return max(low, min(value, high))


class DataCache:
    def __init__(self, path):
        self.path = path
        self.lock = threading.Lock()
        self.data = self._load()

    def _load(self):
        if self.path.exists():
            return json.loads(self.path.read_text())
        return {
            "bootstrap": None,
            "element_summaries": {},
            "elo_insights": None,
            "last_fetch_at": None,
            "last_prediction_at": None,
        }

    def save(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(self.data, indent=2))

    def get_bootstrap(self):
        return self.data.get("bootstrap")

    def set_bootstrap(self, payload):
        self.data["bootstrap"] = payload
        self.data["last_fetch_at"] = now_utc().isoformat()

    def get_summary(self, player_id):
        return self.data["element_summaries"].get(str(player_id))

    def set_summary(self, player_id, payload):
        self.data["element_summaries"][str(player_id)] = payload
        self.data["last_fetch_at"] = now_utc().isoformat()

    def get_elo_insights(self):
        return self.data.get("elo_insights")

    def set_elo_insights(self, payload):
        self.data["elo_insights"] = payload
        self.data["last_fetch_at"] = now_utc().isoformat()

    def set_prediction_timestamp(self):
        self.data["last_prediction_at"] = now_utc().isoformat()

    def source_data_stale(self):
        last_fetch = parse_timestamp(self.data.get("last_fetch_at"))
        if last_fetch is None:
            return True
        return now_utc() - last_fetch >= timedelta(hours=DATA_REFRESH_HOURS)

    def prediction_stale(self):
        last_prediction = parse_timestamp(self.data.get("last_prediction_at"))
        if last_prediction is None:
            return True
        return now_utc() - last_prediction >= timedelta(hours=PREDICTION_REFRESH_HOURS)

    def has_prediction_inputs(self):
        return bool(self.get_bootstrap()) and bool(self.data.get("element_summaries"))


class FPLClient:
    def __init__(self, api_base):
        self.api_base = api_base.rstrip("/")
        self.user_agent = "FPLModelPrototype/1.0"

    def _get_json(self, path):
        url = f"{self.api_base}/{path.lstrip('/')}"
        request = Request(url, headers={"User-Agent": self.user_agent})
        with urlopen(request, timeout=30) as response:
            return json.loads(response.read().decode("utf-8"))

    def get_bootstrap(self):
        return self._get_json("bootstrap-static/")

    def get_element_summary(self, player_id):
        return self._get_json(f"element-summary/{player_id}/")


class FPLEloInsightsClient:
    def __init__(self, base_url):
        self.base_url = base_url.rstrip("/")
        self.user_agent = "FPLModelPrototype/1.0"

    def _get_csv(self, path):
        url = f"{self.base_url}/{path.lstrip('/')}"
        request = Request(url, headers={"User-Agent": self.user_agent})
        with urlopen(request, timeout=30) as response:
            text = response.read().decode("utf-8")
        return list(csv.DictReader(StringIO(text)))

    def get_dataset(self, bootstrap):
        season = self._season_slug(bootstrap)
        finished_gameweeks = [event["id"] for event in bootstrap.get("events", []) if event.get("finished")]
        if not finished_gameweeks:
            return {
                "season": season,
                "latest_finished_gw": None,
                "team_strengths": {},
                "player_overrides": {},
                "history_by_player": {},
            }

        latest_finished_gw = max(finished_gameweeks)
        teams = self._get_csv(f"{season}/teams.csv")
        latest_rows = self._get_csv(f"{season}/By%20Gameweek/GW{latest_finished_gw}/player_gameweek_stats.csv")

        history_by_player = {}
        for gw in finished_gameweeks:
            rows = self._get_csv(f"{season}/By%20Gameweek/GW{gw}/player_gameweek_stats.csv")
            for row in rows:
                player_id = row.get("id")
                if not player_id:
                    continue
                history_by_player.setdefault(player_id, []).append(self._history_row(row))

        return {
            "season": season,
            "latest_finished_gw": latest_finished_gw,
            "team_strengths": {str(row["id"]): row for row in teams if row.get("id")},
            "player_overrides": {str(row["id"]): row for row in latest_rows if row.get("id")},
            "history_by_player": history_by_player,
        }

    def _season_slug(self, bootstrap):
        years = []
        for event in bootstrap.get("events", []):
            deadline = event.get("deadline_time")
            if not deadline:
                continue
            years.append(datetime.fromisoformat(deadline.replace("Z", "+00:00")).year)
        if not years:
            current_year = now_utc().year
            return f"{current_year - 1}-{current_year}"
        return f"{min(years)}-{max(years)}"

    def _history_row(self, row):
        def as_float(key):
            return float(row.get(key) or 0)

        def as_int(key):
            return int(float(row.get(key) or 0))

        return {
            "round": as_int("gw"),
            "minutes": as_int("minutes"),
            "starts": as_int("starts"),
            "expected_goals": as_float("expected_goals"),
            "expected_assists": as_float("expected_assists"),
            "goals_scored": as_int("goals_scored"),
            "assists": as_int("assists"),
            "yellow_cards": as_int("yellow_cards"),
            "bonus": as_int("bonus"),
            "recoveries": as_float("recoveries"),
            "team_h_score": 0,
            "team_a_score": 0,
        }


class Predictor:
    POSITION_POINTS = {
        1: {"goal": 6, "clean_sheet": 4},
        2: {"goal": 6, "clean_sheet": 4},
        3: {"goal": 5, "clean_sheet": 1},
        4: {"goal": 4, "clean_sheet": 0},
    }

    def __init__(self, bootstrap, element_summaries, history_overrides=None, player_overrides=None, team_strengths=None):
        self.bootstrap = bootstrap
        self.element_summaries = element_summaries
        self.teams = {team["id"]: team for team in bootstrap["teams"]}
        self.positions = {item["id"]: item["singular_name_short"] for item in bootstrap["element_types"]}
        self.history_overrides = history_overrides or {}
        self.player_overrides = player_overrides or {}
        self.team_strengths = team_strengths or {str(team_id): team for team_id, team in self.teams.items()}
        self.current_event_id = next(
            (event["id"] for event in bootstrap.get("events", []) if event.get("is_current")),
            None,
        )

    def predict(self, horizon, position_filter="ALL", start_event_id=None):
        players = []
        for player in self.bootstrap["elements"]:
            if position_filter != "ALL" and self.positions[player["element_type"]] != position_filter:
                continue
            fixtures = self._upcoming_fixtures(player["id"], horizon, start_event_id)
            if not fixtures:
                continue
            summary = self.element_summaries.get(str(player["id"]), {"history": [], "fixtures": []})
            player_context = self._player_context(player)
            history = self.history_overrides.get(str(player["id"]), summary.get("history", []))
            predicted = self._predict_player(player_context, history, fixtures)
            players.append(predicted)

        players.sort(key=lambda item: item["predicted_total_points"], reverse=True)
        return players

    def _upcoming_fixtures(self, player_id, horizon, start_event_id=None):
        summary = self.element_summaries.get(str(player_id), {})
        fixtures = summary.get("fixtures", [])
        first_event = start_event_id or self._next_event_id()
        current_events = sorted(
            {
                fixture["event"]
                for fixture in fixtures
                if fixture.get("event") is not None and fixture["event"] >= first_event
            }
        )
        selected_events = set(current_events[:horizon])
        return [fixture for fixture in fixtures if fixture.get("event") in selected_events]

    def _next_event_id(self):
        for event in self.bootstrap.get("events", []):
            if event.get("is_next"):
                return event["id"]
        unfinished_events = [
            event["id"]
            for event in self.bootstrap.get("events", [])
            if not event.get("finished")
        ]
        if unfinished_events:
            return min(unfinished_events)
        return 1

    def _player_context(self, player):
        override = self.player_overrides.get(str(player["id"]), {})
        merged = dict(player)
        int_fields = {
            "chance_of_playing_next_round",
            "chance_of_playing_this_round",
            "minutes",
            "starts",
        }
        for key in (
            "expected_goals_per_90",
            "expected_assists_per_90",
            "expected_goal_involvements_per_90",
            "expected_goals_conceded_per_90",
            "saves_per_90",
            "clean_sheets_per_90",
            "goals_conceded_per_90",
            "starts_per_90",
            "defensive_contribution_per_90",
            "chance_of_playing_next_round",
            "chance_of_playing_this_round",
            "minutes",
            "starts",
        ):
            if key in override and override[key] not in ("", None):
                value = override[key]
                if key in int_fields:
                    merged[key] = int(float(value))
                else:
                    merged[key] = float(value)
        return merged

    def _predict_player(self, player, history, fixtures):
        position_points = self.POSITION_POINTS[player["element_type"]]
        recent_matches = history[-5:]
        minutes_context = self._predict_minutes(player, recent_matches)
        minutes_prediction = minutes_context["predicted_minutes"]
        goals_context = self._predict_goals(player, recent_matches, fixtures, minutes_prediction)
        goals_per_fixture = goals_context["predicted_goals_per_fixture"]
        assists_context = self._predict_assists(player, recent_matches, fixtures, minutes_prediction)
        assists_per_fixture = assists_context["predicted_assists_per_fixture"]
        cs_per_fixture = self._predict_clean_sheet(player, fixtures)
        defensive_per_fixture = self._predict_defensive_contribution(player, recent_matches)
        bonus_per_fixture = self._predict_bonus(recent_matches, goals_per_fixture, assists_per_fixture, defensive_per_fixture)
        yellows_per_fixture = self._predict_yellows(recent_matches)

        match_count = len(fixtures)
        minutes_points_per_fixture = 2 if minutes_prediction >= 60 else 1 if minutes_prediction > 0 else 0
        total_goals = goals_per_fixture * match_count
        total_assists = assists_per_fixture * match_count
        total_clean_sheets = cs_per_fixture * match_count
        total_defensive = defensive_per_fixture * match_count
        total_bonus = bonus_per_fixture * match_count
        total_yellows = yellows_per_fixture * match_count
        total_minutes_points = minutes_points_per_fixture * match_count

        total_points = (
            total_minutes_points
            + total_goals * position_points["goal"]
            + total_assists * 3
            + total_clean_sheets * position_points["clean_sheet"]
            + total_defensive
            + total_bonus
            - total_yellows
        )

        sample_matches = minutes_context["sample_matches"]

        return {
            "player_id": player["id"],
            "player_name": f"{player['first_name']} {player['second_name']}",
            "team": self.teams[player["team"]]["short_name"],
            "position": self.positions[player["element_type"]],
            "horizon": match_count,
            "fixtures": [
                {
                    "event": fixture.get("event"),
                    "opponent": self.teams[fixture["team_a"] if fixture["is_home"] else fixture["team_h"]]["short_name"],
                    "home": fixture["is_home"],
                    "difficulty": fixture.get(
                        "difficulty",
                        fixture.get("team_h_difficulty") if fixture["is_home"] else fixture.get("team_a_difficulty"),
                    ),
                }
                for fixture in fixtures
            ],
            "predicted_total_points": round(total_points, 2),
            "components": {
                "minutes_points": round(total_minutes_points, 2),
                "goals": round(total_goals, 2),
                "goal_points": round(total_goals * position_points["goal"], 2),
                "assists": round(total_assists, 2),
                "assist_points": round(total_assists * 3, 2),
                "clean_sheets": round(total_clean_sheets, 2),
                "clean_sheet_points": round(total_clean_sheets * position_points["clean_sheet"], 2),
                "defensive_contribution_points": round(total_defensive, 2),
                "bonus_points": round(total_bonus, 2),
                "yellow_cards": round(total_yellows, 2),
                "sub_60_penalty": 0.0,
            },
            "inputs": {
                "predicted_minutes_per_fixture": round(minutes_prediction, 2),
                "minutes_points_per_fixture": minutes_points_per_fixture,
                "minutes_sample": [
                    {
                        "round": match.get("round"),
                        "minutes": match.get("minutes", 0),
                        "starts": match.get("starts", 0),
                    }
                    for match in sample_matches
                ],
                "minutes_base": round(minutes_context["base_minutes"], 2),
                "availability_factor": round(minutes_context["availability_factor"], 3),
                "rotation_factor": round(minutes_context["rotation_factor"], 3),
                "goals_per_fixture": round(goals_per_fixture, 3),
                "goal_model": {
                    "recent_xg_total": round(goals_context["recent_xg_total"], 3),
                    "recent_goals_total": round(goals_context["recent_goals_total"], 3),
                    "sample_size": goals_context["sample_size"],
                    "baseline_per_fixture": round(goals_context["baseline_per_fixture"], 3),
                    "fixture_factor": round(goals_context["fixture_factor"], 3),
                    "finishing_adjustment": round(goals_context["finishing_adjustment"], 3),
                },
                "assists_per_fixture": round(assists_per_fixture, 3),
                "assist_model": {
                    "recent_xa_total": round(assists_context["recent_xa_total"], 3),
                    "recent_assists_total": round(assists_context["recent_assists_total"], 3),
                    "sample_size": assists_context["sample_size"],
                    "baseline_per_fixture": round(assists_context["baseline_per_fixture"], 3),
                    "fixture_factor": round(assists_context["fixture_factor"], 3),
                    "conversion_adjustment": round(assists_context["conversion_adjustment"], 3),
                },
                "clean_sheet_probability_per_fixture": round(cs_per_fixture, 3),
                "defensive_contribution_per_fixture": round(defensive_per_fixture, 3),
                "bonus_per_fixture": round(bonus_per_fixture, 3),
                "yellow_cards_per_fixture": round(yellows_per_fixture, 3),
                "position_goal_points": position_points["goal"],
                "position_clean_sheet_points": position_points["clean_sheet"],
            },
        }

    def _predict_minutes(self, player, recent_matches):
        sample_matches = self._minutes_sample(recent_matches)
        if not sample_matches:
            base = clamp(float(player.get("minutes", 0)) / max(player.get("starts", 1), 1), 0, 90)
        else:
            base = sum(match.get("minutes", 0) for match in sample_matches) / max(len(sample_matches), 1)

        chance_playing = player.get("chance_of_playing_next_round")
        if chance_playing is None:
            availability_factor = 1.0
        else:
            availability_factor = chance_playing / 100

        start_rate = player.get("starts", 0) / max(player.get("minutes", 0) / 90, 1)
        rotation_factor = clamp(start_rate, 0.85, 1.0)
        predicted_minutes = round(clamp(base * availability_factor * rotation_factor, 0, 90), 2)
        return {
            "predicted_minutes": predicted_minutes,
            "base_minutes": base,
            "availability_factor": availability_factor,
            "rotation_factor": rotation_factor,
            "sample_matches": sample_matches,
        }

    def _minutes_sample(self, recent_matches):
        sample_pool = recent_matches[-3:] if len(recent_matches) >= 3 else recent_matches
        filtered = [match for match in sample_pool if not self._ignore_minutes_match(match)]
        return filtered if filtered else sample_pool

    def _ignore_minutes_match(self, match):
        is_unfinished_current_event = (
            self.current_event_id is not None
            and match.get("round") == self.current_event_id
            and match.get("team_h_score") is None
            and match.get("team_a_score") is None
        )
        return (match.get("minutes", 0) or 0) == 0 and is_unfinished_current_event

    def _predict_goals(self, player, recent_matches, fixtures, minutes_prediction):
        minutes = max(minutes_prediction, 1)
        recent_xg = sum(float(match.get("expected_goals", 0) or 0) for match in recent_matches)
        recent_goals = sum(float(match.get("goals_scored", 0) or 0) for match in recent_matches)
        sample_size = max(len(recent_matches), 1)
        xg_rate = recent_xg / sample_size if recent_xg > 0 else float(player.get("expected_goals_per_90", 0) or 0) * (minutes / 90)
        finishing_adjustment = clamp((recent_goals + 1) / (recent_xg + 1), 0.75, 1.25)
        fixture_factor = self._fixture_attack_factor(player["team"], fixtures)
        predicted = round(max(xg_rate * finishing_adjustment * fixture_factor, 0), 3)
        return {
            "predicted_goals_per_fixture": predicted,
            "recent_xg_total": recent_xg,
            "recent_goals_total": recent_goals,
            "sample_size": sample_size,
            "baseline_per_fixture": xg_rate,
            "finishing_adjustment": finishing_adjustment,
            "fixture_factor": fixture_factor,
        }

    def _predict_assists(self, player, recent_matches, fixtures, minutes_prediction):
        minutes = max(minutes_prediction, 1)
        recent_xa = sum(float(match.get("expected_assists", 0) or 0) for match in recent_matches)
        recent_assists = sum(float(match.get("assists", 0) or 0) for match in recent_matches)
        sample_size = max(len(recent_matches), 1)
        xa_rate = recent_xa / sample_size if recent_xa > 0 else float(player.get("expected_assists_per_90", 0) or 0) * (minutes / 90)
        conversion_adjustment = clamp((recent_assists + 1) / (recent_xa + 1), 0.7, 1.2)
        fixture_factor = self._fixture_attack_factor(player["team"], fixtures)
        predicted = round(max(xa_rate * conversion_adjustment * fixture_factor, 0), 3)
        return {
            "predicted_assists_per_fixture": predicted,
            "recent_xa_total": recent_xa,
            "recent_assists_total": recent_assists,
            "sample_size": sample_size,
            "baseline_per_fixture": xa_rate,
            "conversion_adjustment": conversion_adjustment,
            "fixture_factor": fixture_factor,
        }

    def _predict_clean_sheet(self, player, fixtures):
        probabilities = []
        for fixture in fixtures:
            team = self._strength_team(player["team"])
            if fixture["is_home"]:
                own = self._as_float(team.get("strength_defence_home"))
                opp = self._as_float(self._strength_team(fixture["team_a"]).get("strength_attack_away"))
            else:
                own = self._as_float(team.get("strength_defence_away"))
                opp = self._as_float(self._strength_team(fixture["team_h"]).get("strength_attack_home"))
            advantage = clamp((own - opp) / 40, -0.4, 0.4)
            probabilities.append(clamp(0.3 + advantage, 0.05, 0.65))
        if not probabilities:
            return 0.0
        return round(sum(probabilities) / len(probabilities), 3)

    def _predict_defensive_contribution(self, player, recent_matches):
        position = player["element_type"]
        if position not in (1, 2, 3):
            return 0.0
        recoveries = sum(float(match.get("recoveries", 0) or 0) for match in recent_matches)
        sample_size = max(len(recent_matches), 1)
        baseline = recoveries / sample_size
        return round(clamp(baseline / 8, 0, 1.5), 3)

    def _predict_bonus(self, recent_matches, goals_per_fixture, assists_per_fixture, defensive_per_fixture):
        historical_bonus = sum(float(match.get("bonus", 0) or 0) for match in recent_matches)
        sample_size = max(len(recent_matches), 1)
        baseline = historical_bonus / sample_size
        attacking_lift = goals_per_fixture * 1.2 + assists_per_fixture * 0.8
        return round(clamp(baseline * 0.5 + attacking_lift + defensive_per_fixture * 0.4, 0, 3), 3)

    def _predict_yellows(self, recent_matches):
        yellows = sum(float(match.get("yellow_cards", 0) or 0) for match in recent_matches)
        sample_size = max(len(recent_matches), 1)
        return round(clamp(yellows / sample_size, 0, 0.5), 3)

    def _fixture_attack_factor(self, team_id, fixtures):
        factors = []
        for fixture in fixtures:
            team = self._strength_team(team_id)
            if fixture["is_home"]:
                own = self._as_float(team.get("strength_attack_home"))
                opp = self._as_float(self._strength_team(fixture["team_a"]).get("strength_defence_away"))
            else:
                own = self._as_float(team.get("strength_attack_away"))
                opp = self._as_float(self._strength_team(fixture["team_h"]).get("strength_defence_home"))
            factors.append(clamp(1 + (own - opp) / 50, 0.7, 1.3))
        if not factors:
            return 1.0
        return sum(factors) / len(factors)

    def _strength_team(self, team_id):
        return self.team_strengths.get(str(team_id), self.teams[team_id])

    def _as_float(self, value):
        return float(value or 0)


class App:
    def __init__(self):
        load_env()
        api_base = os.environ.get("FPL_API_BASE", "https://fantasy.premierleague.com/api")
        elo_base = os.environ.get("FPL_ELO_BASE", ELO_INSIGHTS_BASE)
        self.cache = DataCache(CACHE_PATH)
        self.client = FPLClient(api_base)
        self.elo_client = FPLEloInsightsClient(elo_base)

    def get_predictions(self, horizon, position_filter, start_event_id=None, source="official"):
        with self.cache.lock:
            stale_refresh_error = None
            if self.cache.source_data_stale() or self.cache.prediction_stale():
                try:
                    self._refresh_data()
                except (HTTPError, URLError, TimeoutError) as exc:
                    if not self.cache.has_prediction_inputs():
                        raise
                    stale_refresh_error = str(exc)
            bootstrap = self.cache.get_bootstrap()
            if not bootstrap:
                raise RuntimeError("No FPL bootstrap data is available.")
            available_gameweeks = self._available_gameweeks(bootstrap)
            selected_start_event = start_event_id or available_gameweeks[0]
            predictor = self._predictor_for_source(bootstrap, source)
            results = predictor.predict(horizon, position_filter, selected_start_event)
            self.cache.set_prediction_timestamp()
            self.cache.save()
            return {
                "generated_at": now_utc().isoformat(),
                "source_last_fetch_at": self.cache.data.get("last_fetch_at"),
                "last_prediction_at": self.cache.data.get("last_prediction_at"),
                "horizon": horizon,
                "position_filter": position_filter,
                "start_event_id": selected_start_event,
                "source": source,
                "used_cached_data": stale_refresh_error is not None,
                "refresh_warning": stale_refresh_error,
                "available_gameweeks": available_gameweeks,
                "players": results,
            }

    def _refresh_data(self):
        bootstrap = self.client.get_bootstrap()
        self.cache.set_bootstrap(bootstrap)

        players = bootstrap["elements"]
        max_workers = min(16, max(4, len(players) // 40))
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            futures = {
                executor.submit(self.client.get_element_summary, player["id"]): player["id"]
                for player in players
            }
            for future in as_completed(futures):
                player_id = futures[future]
                summary = future.result()
                self.cache.set_summary(player_id, summary)
        self.cache.set_elo_insights(self.elo_client.get_dataset(bootstrap))
        self.cache.save()

    def _predictor_for_source(self, bootstrap, source):
        if source == "elo":
            elo_data = self.cache.get_elo_insights() or {}
            return Predictor(
                bootstrap,
                self.cache.data["element_summaries"],
                history_overrides=elo_data.get("history_by_player", {}),
                player_overrides=elo_data.get("player_overrides", {}),
                team_strengths=elo_data.get("team_strengths", {}),
            )
        return Predictor(bootstrap, self.cache.data["element_summaries"])

    def _available_gameweeks(self, bootstrap):
        next_event = None
        for event in bootstrap.get("events", []):
            if event.get("is_next"):
                next_event = event["id"]
                break
        if next_event is None:
            unfinished = [event["id"] for event in bootstrap.get("events", []) if not event.get("finished")]
            next_event = min(unfinished) if unfinished else 1
        return list(range(next_event, 39))


APP = App()


class RequestHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        parsed = urlparse(self.path)
        if parsed.path == "/api/predictions":
            self._handle_predictions(parsed)
            return
        if parsed.path == "/api/health":
            self._write_json({"status": "ok", "time": now_utc().isoformat()})
            return
        file_path = STATIC_FILES.get(parsed.path)
        if file_path and file_path.exists():
            self._serve_file(file_path)
            return
        self.send_error(404, "Not found")

    def _handle_predictions(self, parsed):
        query = parse_qs(parsed.query)
        horizon = int(query.get("horizon", ["3"])[0])
        position_filter = query.get("position", ["ALL"])[0]
        start_event_id = int(query.get("start_gw", ["0"])[0] or 0)
        source = query.get("source", ["official"])[0]
        horizon = int(clamp(horizon, 1, 6))
        try:
            payload = APP.get_predictions(horizon, position_filter, start_event_id or None, source)
            self._write_json(payload)
        except (HTTPError, URLError, TimeoutError) as exc:
            self._write_json(
                {
                    "error": "Failed to refresh FPL data.",
                    "detail": str(exc),
                },
                status=502,
            )
        except Exception as exc:
            self._write_json(
                {
                    "error": "Prediction failed.",
                    "detail": str(exc),
                },
                status=500,
            )

    def _serve_file(self, file_path):
        content_type = "text/html; charset=utf-8"
        if file_path.suffix == ".js":
            content_type = "application/javascript; charset=utf-8"
        body = file_path.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _write_json(self, payload, status=200):
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format, *args):
        return


def run():
    port = int(os.environ.get("PORT", "8000"))
    server = ThreadingHTTPServer(("0.0.0.0", port), RequestHandler)
    print(f"Serving on http://localhost:{port}")
    server.serve_forever()


if __name__ == "__main__":
    run()
