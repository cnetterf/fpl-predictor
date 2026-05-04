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
from statistics import mean
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, urlparse
from urllib.request import Request, urlopen


ROOT = Path(__file__).resolve().parent
STATIC_FILES = {
    "/": ROOT / "index.html",
    "/index.html": ROOT / "index.html",
    "/app.js": ROOT / "app.js",
    "/data/static_predictions.json": ROOT / "data" / "static_predictions.json",
    "/data/static_backtest.json": ROOT / "data" / "static_backtest.json",
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


def to_float(value):
    return float(value or 0)


def to_int(value):
    return int(float(value or 0))


def safe_mean(values, default=0.0):
    values = list(values)
    return mean(values) if values else default


def build_backtest_player_context(player, history_before):
    context = dict(player)
    minutes_total = sum(to_int(match.get("minutes")) for match in history_before)
    starts_total = sum(to_int(match.get("starts")) for match in history_before)
    xg_total = sum(to_float(match.get("expected_goals")) for match in history_before)
    xa_total = sum(to_float(match.get("expected_assists")) for match in history_before)
    recoveries_total = sum(to_float(match.get("recoveries")) for match in history_before)

    context["minutes"] = minutes_total
    context["starts"] = starts_total
    context["expected_goals_per_90"] = (xg_total * 90 / minutes_total) if minutes_total else 0
    context["expected_assists_per_90"] = (xa_total * 90 / minutes_total) if minutes_total else 0
    context["defensive_contribution_per_90"] = (recoveries_total * 90 / minutes_total) if minutes_total else 0
    context["chance_of_playing_next_round"] = 100
    context["chance_of_playing_this_round"] = 100
    return context


def fixture_from_match(player_team_id, match):
    opponent_team = match.get("opponent_team")
    if opponent_team is None:
        return None
    if match.get("was_home"):
        return {
            "event": match.get("round"),
            "team_h": player_team_id,
            "team_a": opponent_team,
            "is_home": True,
            "difficulty": None,
        }
    return {
        "event": match.get("round"),
        "team_h": opponent_team,
        "team_a": player_team_id,
        "is_home": False,
        "difficulty": None,
    }


def actual_matches(history, start_gw, end_gw):
    return [match for match in history if start_gw <= to_int(match.get("round")) <= end_gw]


def prior_history(history, start_gw):
    return [match for match in history if to_int(match.get("round")) < start_gw]


def rank_map(players, score_key):
    sorted_players = sorted(players, key=score_key, reverse=True)
    return {player["player_id"]: index + 1 for index, player in enumerate(sorted_players)}


def spearman_correlation(rows):
    if len(rows) < 2:
        return 0.0
    pred_ranks = rank_map(rows, lambda item: item["predicted_points"])
    actual_ranks = rank_map(rows, lambda item: item["actual_points"])
    common_ids = pred_ranks.keys() & actual_ranks.keys()
    n = len(common_ids)
    if n < 2:
        return 0.0
    diff_sq = sum((pred_ranks[player_id] - actual_ranks[player_id]) ** 2 for player_id in common_ids)
    return 1 - (6 * diff_sq) / (n * (n**2 - 1))


def overlap_at_n(rows, n):
    predicted = sorted(rows, key=lambda item: item["predicted_points"], reverse=True)[:n]
    actual = sorted(rows, key=lambda item: item["actual_points"], reverse=True)[:n]
    return len({item["player_id"] for item in predicted} & {item["player_id"] for item in actual})


def dcg_at_n(rows, n):
    ranked = sorted(rows, key=lambda item: item["predicted_points"], reverse=True)[:n]
    score = 0.0
    for index, item in enumerate(ranked, start=1):
        gain = max(item["actual_points"], 0)
        score += gain / math.log2(index + 1)
    return score


def ndcg_at_n(rows, n):
    ideal = sorted(rows, key=lambda item: item["actual_points"], reverse=True)[:n]
    ideal_score = 0.0
    for index, item in enumerate(ideal, start=1):
        gain = max(item["actual_points"], 0)
        ideal_score += gain / math.log2(index + 1)
    if ideal_score == 0:
        return 0.0
    return dcg_at_n(rows, n) / ideal_score


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


class BacktestEngine:
    MIN_START_GW = 2
    SOURCE_LABELS = {
        "official": "Official FPL",
        "elo": "Elo Insights",
    }

    def __init__(self, bootstrap, element_summaries, elo_insights=None):
        self.bootstrap = bootstrap
        self.element_summaries = element_summaries
        self.elo_insights = elo_insights or {}
        self.players = bootstrap.get("elements", [])
        self.player_index = {str(player["id"]): player for player in self.players}
        self.finished_gameweeks = [
            event["id"]
            for event in bootstrap.get("events", [])
            if event.get("finished")
        ]
        self.official_histories = {
            player_id: summary.get("history", [])
            for player_id, summary in element_summaries.items()
        }
        self.elo_histories = self.elo_insights.get("history_by_player", {}) or {}
        self.available_gameweeks = self._build_available_gameweeks()
        self.historical_strengths = self._build_historical_team_strengths()

    def _build_available_gameweeks(self):
        if not self.finished_gameweeks:
            return []
        available = [gw for gw in sorted(self.finished_gameweeks) if gw >= self.MIN_START_GW]
        if self.elo_histories:
            elo_rounds = sorted(
                {
                    to_int(match.get("round"))
                    for rows in self.elo_histories.values()
                    for match in rows
                    if to_int(match.get("round"))
                }
            )
            if elo_rounds:
                earliest_elo = min(elo_rounds)
                latest_elo = max(elo_rounds)
                available = [gw for gw in available if gw > earliest_elo and gw <= latest_elo]
        return available

    def _dedupe_team_results(self):
        seen = set()
        rows = []
        for player in self.players:
            player_id = str(player["id"])
            team_id = player["team"]
            summary = self.element_summaries.get(player_id, {})
            for match in summary.get("history", []):
                fixture_id = match.get("fixture")
                round_id = to_int(match.get("round"))
                opponent_team = match.get("opponent_team")
                if (
                    fixture_id is None
                    or not round_id
                    or opponent_team is None
                    or match.get("team_h_score") is None
                    or match.get("team_a_score") is None
                ):
                    continue
                key = (fixture_id, team_id)
                if key in seen:
                    continue
                seen.add(key)
                was_home = bool(match.get("was_home"))
                goals_for = to_int(match.get("team_h_score")) if was_home else to_int(match.get("team_a_score"))
                goals_against = to_int(match.get("team_a_score")) if was_home else to_int(match.get("team_h_score"))
                rows.append(
                    {
                        "round": round_id,
                        "team_id": team_id,
                        "opponent_team": opponent_team,
                        "was_home": was_home,
                        "goals_for": goals_for,
                        "goals_against": goals_against,
                    }
                )
        rows.sort(key=lambda item: (item["round"], item["team_id"], item["opponent_team"]))
        return rows

    def _snapshot_strengths(self, cumulative):
        teams = self.bootstrap.get("teams", [])
        home_matches = sum(values["home_matches"] for values in cumulative.values())
        away_matches = sum(values["away_matches"] for values in cumulative.values())

        home_baseline = 1.6
        away_baseline = 1.2
        league_home_avg = (
            (sum(values["home_goals_for"] for values in cumulative.values()) + home_baseline * 20)
            / max(home_matches + 20, 1)
        )
        league_away_avg = (
            (sum(values["away_goals_for"] for values in cumulative.values()) + away_baseline * 20)
            / max(away_matches + 20, 1)
        )

        strengths = {}
        team_prior = 3.0
        for team in teams:
            stats = cumulative[team["id"]]

            home_attack = (stats["home_goals_for"] + league_home_avg * team_prior) / (stats["home_matches"] + team_prior)
            away_attack = (stats["away_goals_for"] + league_away_avg * team_prior) / (stats["away_matches"] + team_prior)
            home_concede = (stats["home_goals_against"] + league_away_avg * team_prior) / (stats["home_matches"] + team_prior)
            away_concede = (stats["away_goals_against"] + league_home_avg * team_prior) / (stats["away_matches"] + team_prior)

            strengths[str(team["id"])] = {
                "id": team["id"],
                "name": team["name"],
                "short_name": team["short_name"],
                "strength_attack_home": round(clamp(1000 * home_attack / max(league_home_avg, 0.2), 700, 1400)),
                "strength_attack_away": round(clamp(1000 * away_attack / max(league_away_avg, 0.2), 700, 1400)),
                "strength_defence_home": round(clamp(1000 * league_away_avg / max(home_concede, 0.2), 700, 1400)),
                "strength_defence_away": round(clamp(1000 * league_home_avg / max(away_concede, 0.2), 700, 1400)),
            }
        return strengths

    def _build_historical_team_strengths(self):
        strengths_by_gw = {}
        results = self._dedupe_team_results()
        results_by_round = {}
        for row in results:
            results_by_round.setdefault(row["round"], []).append(row)

        cumulative = {
            team["id"]: {
                "home_matches": 0,
                "away_matches": 0,
                "home_goals_for": 0.0,
                "home_goals_against": 0.0,
                "away_goals_for": 0.0,
                "away_goals_against": 0.0,
            }
            for team in self.bootstrap.get("teams", [])
        }

        for gw in sorted(self.available_gameweeks):
            strengths_by_gw[gw] = self._snapshot_strengths(cumulative)
            for row in results_by_round.get(gw, []):
                stats = cumulative[row["team_id"]]
                if row["was_home"]:
                    stats["home_matches"] += 1
                    stats["home_goals_for"] += row["goals_for"]
                    stats["home_goals_against"] += row["goals_against"]
                else:
                    stats["away_matches"] += 1
                    stats["away_goals_for"] += row["goals_for"]
                    stats["away_goals_against"] += row["goals_against"]
        return strengths_by_gw

    def _source_histories(self, source):
        return self.official_histories if source == "official" else self.elo_histories

    def _historical_player_overrides(self, histories, start_gw):
        overrides = {}
        for player in self.players:
          player_id = str(player["id"])
          history_before = prior_history(histories.get(player_id, []), start_gw)
          context = build_backtest_player_context(player, history_before)
          overrides[player_id] = {
              "minutes": context["minutes"],
              "starts": context["starts"],
              "expected_goals_per_90": context["expected_goals_per_90"],
              "expected_assists_per_90": context["expected_assists_per_90"],
              "defensive_contribution_per_90": context["defensive_contribution_per_90"],
              "chance_of_playing_next_round": context["chance_of_playing_next_round"],
              "chance_of_playing_this_round": context["chance_of_playing_this_round"],
          }
        return overrides

    def _window_summary(self, rows):
        if not rows:
            return {
                "players": 0,
                "predicted_points": 0.0,
                "actual_points": 0.0,
                "error": 0.0,
                "absolute_error": 0.0,
                "mae": 0.0,
                "rmse": 0.0,
                "spearman": 0.0,
                "top10_overlap": 0.0,
                "top20_overlap": 0.0,
                "ndcg20": 0.0,
            }

        predicted_ranks = rank_map(rows, lambda item: item["predicted_points"])
        actual_ranks = rank_map(rows, lambda item: item["actual_points"])
        for row in rows:
            row["predicted_rank"] = predicted_ranks[row["player_id"]]
            row["actual_rank"] = actual_ranks[row["player_id"]]
            row["rank_error"] = row["predicted_rank"] - row["actual_rank"]

        errors = [row["error"] for row in rows]
        absolute_errors = [row["absolute_error"] for row in rows]
        return {
            "players": len(rows),
            "predicted_points": round(sum(row["predicted_points"] for row in rows), 2),
            "actual_points": round(sum(row["actual_points"] for row in rows), 2),
            "error": round(sum(errors), 2),
            "absolute_error": round(sum(absolute_errors), 2),
            "mae": round(safe_mean(absolute_errors), 3),
            "rmse": round(math.sqrt(safe_mean((error**2 for error in errors))), 3),
            "spearman": round(spearman_correlation(rows), 4),
            "top10_overlap": overlap_at_n(rows, 10),
            "top20_overlap": overlap_at_n(rows, 20),
            "ndcg20": round(ndcg_at_n(rows, 20), 4),
        }

    def evaluate_window(self, source, start_gw, end_gw):
        if start_gw not in self.available_gameweeks or end_gw not in self.available_gameweeks:
            raise ValueError("Requested gameweek window is outside the available backtest range.")
        if end_gw < start_gw:
            raise ValueError("End gameweek must be greater than or equal to the start gameweek.")

        histories = self._source_histories(source)
        player_overrides = self._historical_player_overrides(histories, start_gw)
        predictor = Predictor(
            self.bootstrap,
            self.element_summaries,
            history_overrides=histories,
            player_overrides=player_overrides,
            team_strengths=self.historical_strengths.get(start_gw, {}),
        )

        rows = []
        for player in self.players:
            player_id = str(player["id"])
            source_history = histories.get(player_id, [])
            official_history = self.official_histories.get(player_id, [])
            history_before = prior_history(source_history, start_gw)
            target_matches = actual_matches(official_history, start_gw, end_gw)
            if not target_matches:
                continue

            fixtures = []
            for match in target_matches:
                fixture = fixture_from_match(player["team"], match)
                if fixture:
                    fixtures.append(fixture)
            if not fixtures:
                continue

            player_context = predictor._player_context(player)
            predicted = predictor._predict_player(player_context, history_before, fixtures)
            actual_points = round(sum(to_float(match.get("total_points")) for match in target_matches), 2)
            error = round(predicted["predicted_total_points"] - actual_points, 2)
            rows.append(
                {
                    "player_id": player["id"],
                    "player_name": predicted["player_name"],
                    "team": predicted["team"],
                    "position": predicted["position"],
                    "start_gw": start_gw,
                    "end_gw": end_gw,
                    "predicted_points": predicted["predicted_total_points"],
                    "actual_points": actual_points,
                    "error": error,
                    "absolute_error": round(abs(error), 2),
                }
            )

        summary = self._window_summary(rows)
        return {
            "source": source,
            "label": self.SOURCE_LABELS[source],
            "start_gw": start_gw,
            "end_gw": end_gw,
            "span": end_gw - start_gw + 1,
            "summary": summary,
            "rows": rows,
        }

    def _pack_rows(self, rows):
        return [
            [
                row["player_id"],
                row["predicted_points"],
                row["actual_points"],
                row["error"],
                row["absolute_error"],
                row.get("predicted_rank", 0),
                row.get("actual_rank", 0),
                row.get("rank_error", 0),
            ]
            for row in rows
        ]

    def _pack_window(self, payload):
        return {
            "label": payload["label"],
            "summary": payload["summary"],
            "rows": self._pack_rows(payload["rows"]),
        }

    def _window_audit(self, source_payloads):
        if "official" not in source_payloads or "elo" not in source_payloads:
            return {}
        official_rows = {
            row["player_id"]: row
            for row in source_payloads["official"]["rows"]
        }
        elo_rows = {
            row["player_id"]: row
            for row in source_payloads["elo"]["rows"]
        }
        common_ids = official_rows.keys() & elo_rows.keys()
        if not common_ids:
            return {}

        deltas = [
            abs(official_rows[player_id]["predicted_points"] - elo_rows[player_id]["predicted_points"])
            for player_id in common_ids
        ]
        exact_matches = sum(delta == 0 for delta in deltas)
        different = len(deltas) - exact_matches
        return {
            "common_players": len(deltas),
            "exact_prediction_matches": exact_matches,
            "different_prediction_matches": different,
            "mean_prediction_delta": round(safe_mean(deltas), 3),
            "max_prediction_delta": round(max(deltas), 3),
        }

    def dataset(self, sources=None):
        sources = sources or ["official", "elo"]
        windows = {}
        summary_rows = []
        for start_gw in self.available_gameweeks:
            for end_gw in self.available_gameweeks:
                if end_gw < start_gw:
                    continue
                key = f"{start_gw}-{end_gw}"
                window_payload = {
                    "start_gw": start_gw,
                    "end_gw": end_gw,
                    "span": end_gw - start_gw + 1,
                    "sources": {},
                }
                window_rows_by_source = {}
                for source in sources:
                    if source == "elo" and not self.elo_histories:
                        continue
                    evaluated = self.evaluate_window(source, start_gw, end_gw)
                    window_rows_by_source[source] = evaluated
                    window_payload["sources"][source] = self._pack_window(evaluated)
                    summary_rows.append(
                        {
                            "source": source,
                            "start_gw": start_gw,
                            "end_gw": end_gw,
                            "span": window_payload["span"],
                            **evaluated["summary"],
                        }
                    )
                window_payload["audit"] = self._window_audit(window_rows_by_source)
                if window_payload["sources"]:
                    windows[key] = window_payload

        overview = {}
        for source in sources:
            source_rows = [row for row in summary_rows if row["source"] == source]
            if not source_rows:
                continue
            overview[source] = {
                "windows": len(source_rows),
                "avg_players": round(safe_mean(row["players"] for row in source_rows), 2),
                "avg_mae": round(safe_mean(row["mae"] for row in source_rows), 3),
                "avg_rmse": round(safe_mean(row["rmse"] for row in source_rows), 3),
                "avg_spearman": round(safe_mean(row["spearman"] for row in source_rows), 4),
                "avg_top20_overlap": round(safe_mean(row["top20_overlap"] for row in source_rows), 2),
                "avg_ndcg20": round(safe_mean(row["ndcg20"] for row in source_rows), 4),
            }

        return {
            "generated_at": now_utc().isoformat(),
            "available_gameweeks": self.available_gameweeks,
            "latest_finished_gw": max(self.finished_gameweeks) if self.finished_gameweeks else None,
            "sources": self.SOURCE_LABELS,
            "player_lookup": {
                str(player["id"]): [
                    f"{player['first_name']} {player['second_name']}",
                    next((team["short_name"] for team in self.bootstrap.get("teams", []) if team["id"] == player["team"]), ""),
                    self.bootstrap["element_types"][player["element_type"] - 1]["singular_name_short"],
                ]
                for player in self.players
            },
            "overview": overview,
            "windows": windows,
        }


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

    def get_backtest_dataset(self, recompute=False):
        with self.cache.lock:
            stale_refresh_error = None
            if recompute or self.cache.source_data_stale():
                try:
                    self._refresh_data()
                except (HTTPError, URLError, TimeoutError) as exc:
                    if not self.cache.has_prediction_inputs():
                        raise
                    stale_refresh_error = str(exc)

            bootstrap = self.cache.get_bootstrap()
            if not bootstrap:
                raise RuntimeError("No FPL bootstrap data is available.")

            dataset = self._backtest_engine(bootstrap).dataset()
            dataset["source_last_fetch_at"] = self.cache.data.get("last_fetch_at")
            dataset["used_cached_data"] = stale_refresh_error is not None
            dataset["refresh_warning"] = stale_refresh_error
            dataset["notes"] = [
                "Backtests use only player-match history available before the selected start gameweek.",
                "Historical team strengths are reconstructed from prior finished matches to avoid present-day leakage.",
                "GW1 is excluded because no clean pre-season feature snapshot is stored in the cache.",
            ]
            return dataset

    def get_backtest_window(self, start_gw, end_gw, recompute=False):
        with self.cache.lock:
            stale_refresh_error = None
            if recompute or self.cache.source_data_stale():
                try:
                    self._refresh_data()
                except (HTTPError, URLError, TimeoutError) as exc:
                    if not self.cache.has_prediction_inputs():
                        raise
                    stale_refresh_error = str(exc)

            bootstrap = self.cache.get_bootstrap()
            if not bootstrap:
                raise RuntimeError("No FPL bootstrap data is available.")

            engine = self._backtest_engine(bootstrap)
            payload = {
                "generated_at": now_utc().isoformat(),
                "available_gameweeks": engine.available_gameweeks,
                "latest_finished_gw": max(engine.finished_gameweeks) if engine.finished_gameweeks else None,
                "sources": {},
                "audit": {},
                "source_last_fetch_at": self.cache.data.get("last_fetch_at"),
                "used_cached_data": stale_refresh_error is not None,
                "refresh_warning": stale_refresh_error,
                "notes": [
                    "This window is recomputed locally from cached source histories.",
                    "Historical team strengths are reconstructed using only matches before the start gameweek.",
                ],
            }
            evaluated_by_source = {}
            for source in ("official", "elo"):
                if source == "elo" and not engine.elo_histories:
                    continue
                evaluated = engine.evaluate_window(source, start_gw, end_gw)
                evaluated_by_source[source] = evaluated
                payload["sources"][source] = engine._pack_window(evaluated)
            payload["audit"] = engine._window_audit(evaluated_by_source)
            return payload

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

    def _backtest_engine(self, bootstrap):
        return BacktestEngine(
            bootstrap,
            self.cache.data["element_summaries"],
            self.cache.get_elo_insights(),
        )

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
        if parsed.path == "/api/backtest":
            self._handle_backtest(parsed)
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

    def _handle_backtest(self, parsed):
        query = parse_qs(parsed.query)
        start_gw = int(query.get("start_gw", ["0"])[0] or 0)
        end_gw = int(query.get("end_gw", ["0"])[0] or 0)
        recompute = query.get("recompute", ["0"])[0] == "1"
        try:
            if start_gw and end_gw:
                payload = APP.get_backtest_window(start_gw, end_gw, recompute=recompute)
            else:
                payload = APP.get_backtest_dataset(recompute=recompute)
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
                    "error": "Backtest request failed.",
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
