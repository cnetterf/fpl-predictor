import csv
import gzip
import json
import math
import os
import re
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
PRIOR_SEASON_PATH = CACHE_DIR / "prior_season_history.json.gz"
ELO_SNAPSHOTS_PATH = CACHE_DIR / "elo_snapshots.json"
DATA_REFRESH_HOURS = 12
PREDICTION_REFRESH_HOURS = 6
ELO_INSIGHTS_BASE = "https://raw.githubusercontent.com/olbauday/FPL-Core-Insights/main/data"
ELO_HOME_ADVANTAGE = 100.0
ELO_FACTOR_DEVIATION = 0.55
ELO_SCALE = 400.0
ELO_GOALS_PER_TEAM = 1.40
FPL_API_BASE = "https://fantasy.premierleague.com/api"


def validated_fpl_proxy_path(value):
    path = str(value or "").strip("/")
    if path == "bootstrap-static":
        return path
    if re.fullmatch(r"entry/\d+", path):
        return path
    if re.fullmatch(r"entry/\d+/event/\d+/picks", path):
        return path
    raise ValueError("Unsupported FPL API path.")


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


def bootstrap_season_slug(bootstrap):
    years = []
    for event in bootstrap.get("events", []):
        deadline = event.get("deadline_time")
        if deadline:
            years.append(datetime.fromisoformat(deadline.replace("Z", "+00:00")).year)
    if not years:
        current_year = now_utc().year
        return f"{current_year - 1}-{current_year}"
    return f"{min(years)}-{max(years)}"


def load_prior_season_data():
    if not PRIOR_SEASON_PATH.exists():
        return {}
    with gzip.open(PRIOR_SEASON_PATH, "rt", encoding="utf-8") as source:
        return json.load(source)


def load_elo_snapshots():
    if not ELO_SNAPSHOTS_PATH.exists():
        return {"schema_version": 1, "seasons": {}}
    return json.loads(ELO_SNAPSHOTS_PATH.read_text())


def elo_fixture_values(home_elo, away_elo):
    effective_home_elo = float(home_elo) + ELO_HOME_ADVANTAGE
    delta_elo = effective_home_elo - float(away_elo)
    home_factor = 1.0 + ELO_FACTOR_DEVIATION * math.tanh(delta_elo / ELO_SCALE)
    away_factor = 1.0 + ELO_FACTOR_DEVIATION * math.tanh(-delta_elo / ELO_SCALE)
    home_xg = ELO_GOALS_PER_TEAM * home_factor
    away_xg = ELO_GOALS_PER_TEAM * away_factor
    return {
        "home_elo": float(home_elo),
        "away_elo": float(away_elo),
        "effective_home_elo": effective_home_elo,
        "delta_elo": delta_elo,
        "home_factor": home_factor,
        "away_factor": away_factor,
        "home_xg": home_xg,
        "away_xg": away_xg,
        "home_clean_sheet_probability": math.exp(-away_xg),
        "away_clean_sheet_probability": math.exp(-home_xg),
    }


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


def actual_defensive_contribution_points(element_type, contribution_total):
    if element_type == 1:
        return 0
    threshold = 10 if element_type == 2 else 12
    return 2 if (contribution_total or 0) >= threshold else 0


def actual_match_component_points(player, match):
    element_type = player["element_type"]
    position_points = Predictor.POSITION_POINTS[element_type]
    minutes = to_int(match.get("minutes"))
    goals_scored = to_int(match.get("goals_scored"))
    assists = to_int(match.get("assists"))
    clean_sheets = to_int(match.get("clean_sheets"))
    bonus = to_int(match.get("bonus"))
    yellow_cards = to_int(match.get("yellow_cards"))
    defensive_contribution = to_int(match.get("defensive_contribution"))
    saves = to_int(match.get("saves"))
    penalties_saved = to_int(match.get("penalties_saved"))
    penalties_missed = to_int(match.get("penalties_missed"))
    own_goals = to_int(match.get("own_goals"))
    red_cards = to_int(match.get("red_cards"))
    goals_conceded = to_int(match.get("goals_conceded"))

    minutes_points = 2 if minutes >= 60 else 1 if minutes > 0 else 0
    goal_points = goals_scored * position_points["goal"]
    assist_points = assists * 3
    clean_sheet_points = clean_sheets * position_points["clean_sheet"]
    defensive_points = actual_defensive_contribution_points(element_type, defensive_contribution)
    yellow_deduction = yellow_cards
    other_points = 0
    if element_type == 1:
        other_points += (saves // 3)
    other_points += penalties_saved * 5
    other_points -= penalties_missed * 2
    other_points -= own_goals * 2
    other_points -= red_cards * 3
    if element_type in (1, 2):
        other_points -= goals_conceded // 2

    component_total = (
        minutes_points
        + goal_points
        + assist_points
        + clean_sheet_points
        + defensive_points
        + bonus
        - yellow_deduction
        + other_points
    )
    residual = to_int(match.get("total_points")) - component_total

    return {
        "minutes_points": minutes_points,
        "goal_points": goal_points,
        "assist_points": assist_points,
        "clean_sheet_points": clean_sheet_points,
        "defensive_contribution_points": defensive_points,
        "bonus_points": bonus,
        "yellow_deduction": yellow_deduction,
        "other_points": other_points + residual,
        "goals": goals_scored,
        "assists": assists,
        "clean_sheets": clean_sheets,
        "bonus": bonus,
        "yellow_cards": yellow_cards,
        "expected_goals": to_float(match.get("expected_goals")),
        "expected_assists": to_float(match.get("expected_assists")),
        "defensive_contribution": defensive_contribution,
    }


def aggregate_actual_components(player, matches):
    totals = {
        "minutes_points": 0.0,
        "goal_points": 0.0,
        "assist_points": 0.0,
        "clean_sheet_points": 0.0,
        "defensive_contribution_points": 0.0,
        "bonus_points": 0.0,
        "yellow_deduction": 0.0,
        "other_points": 0.0,
        "goals": 0.0,
        "assists": 0.0,
        "clean_sheets": 0.0,
        "bonus": 0.0,
        "yellow_cards": 0.0,
        "expected_goals": 0.0,
        "expected_assists": 0.0,
        "defensive_contribution": 0.0,
    }
    for match in matches:
        points = actual_match_component_points(player, match)
        for key in totals:
            totals[key] += points[key]
    for key, value in totals.items():
        totals[key] = round(value, 3)
    return totals


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
        teams = self._get_csv(f"{season}/teams.csv")
        team_elo_ratings = self._validated_team_elos(teams, bootstrap)
        finished_gameweeks = [event["id"] for event in bootstrap.get("events", []) if event.get("finished")]
        if not finished_gameweeks:
            return {
                "season": season,
                "latest_finished_gw": None,
                "team_strengths": {str(row["id"]): row for row in teams if row.get("id")},
                "team_elo_ratings": team_elo_ratings,
                "team_elo_source_url": f"{self.base_url}/{season}/teams.csv",
                "team_elo_fetched_at": now_utc().isoformat(),
                "player_overrides": {},
                "history_by_player": {},
            }

        latest_finished_gw = max(finished_gameweeks)
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
            "team_elo_ratings": team_elo_ratings,
            "team_elo_source_url": f"{self.base_url}/{season}/teams.csv",
            "team_elo_fetched_at": now_utc().isoformat(),
            "player_overrides": {str(row["id"]): row for row in latest_rows if row.get("id")},
            "history_by_player": history_by_player,
        }

    def _validated_team_elos(self, rows, bootstrap):
        expected_ids = {str(team["id"]) for team in bootstrap.get("teams", [])}
        ratings = {}
        for row in rows:
            team_id = str(row.get("id") or "")
            raw_elo = row.get("elo")
            if team_id not in expected_ids or raw_elo in (None, ""):
                continue
            try:
                value = float(raw_elo)
            except (TypeError, ValueError):
                continue
            if not math.isfinite(value) or value <= 0:
                continue
            ratings[team_id] = value
        if len(expected_ids) != 20 or set(ratings) != expected_ids:
            missing = sorted(expected_ids - set(ratings), key=lambda value: int(value))
            raise ValueError(
                "Elo refresh rejected: expected 20 numeric current-team ratings; "
                f"received {len(ratings)} (missing team ids: {', '.join(missing) or 'none'})."
            )
        return ratings

    def _season_slug(self, bootstrap):
        return bootstrap_season_slug(bootstrap)

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
            "defensive_contribution": as_float("defensive_contribution"),
            "saves": as_int("saves"),
            "team_h_score": 0,
            "team_a_score": 0,
        }


class Predictor:
    FULL_MINUTES_POINTS_THRESHOLD = 80
    ATTACKING_RATE_SAMPLE_FIXTURES = 6
    FULL_FIXTURE_MINUTES = 90
    LONG_TERM_ATTACKING_WEIGHT = 0.75
    RECENT_ATTACKING_WEIGHT = 0.25
    FINISHING_ADJUSTMENT_MIN = 0.50
    FINISHING_ADJUSTMENT_MAX = 1.25
    TEAM_XG_WARNING_RATIO = 1.15
    POSITION_POINTS = {
        1: {"goal": 6, "clean_sheet": 4},
        2: {"goal": 6, "clean_sheet": 4},
        3: {"goal": 5, "clean_sheet": 1},
        4: {"goal": 4, "clean_sheet": 0},
    }

    def __init__(
        self,
        bootstrap,
        element_summaries,
        history_overrides=None,
        player_overrides=None,
        team_strengths=None,
        team_elo_ratings=None,
        prior_histories=None,
        team_position_xg_per90=None,
        team_position_xa_per90=None,
        position_bonus_per90=None,
    ):
        self.bootstrap = bootstrap
        self.element_summaries = element_summaries
        self.teams = {team["id"]: team for team in bootstrap["teams"]}
        self.positions = {item["id"]: item["singular_name_short"] for item in bootstrap["element_types"]}
        self.history_overrides = history_overrides or {}
        self.player_overrides = player_overrides or {}
        self.team_strengths = team_strengths or {str(team_id): team for team_id, team in self.teams.items()}
        self.team_elo_ratings = {
            str(team_id): float(rating)
            for team_id, rating in (team_elo_ratings or {}).items()
        }
        self.prior_histories = prior_histories or {}
        self.team_position_xg_per90 = team_position_xg_per90 or {}
        self.team_position_xa_per90 = team_position_xa_per90 or {}
        self.position_bonus_per90 = position_bonus_per90 or {}
        self.current_event_id = next(
            (event["id"] for event in bootstrap.get("events", []) if event.get("is_current")),
            None,
        )

    def predict(self, horizon, position_filter="ALL", start_event_id=None):
        players = []
        for player in self.bootstrap["elements"]:
            fixtures = self._upcoming_fixtures(player["id"], horizon, start_event_id)
            if not fixtures:
                continue
            summary = self.element_summaries.get(str(player["id"]), {"history": [], "fixtures": []})
            player_context = self._player_context(player)
            history = self.history_overrides.get(str(player["id"]), summary.get("history", []))
            prior_history = self.prior_histories.get(str(player.get("code")), [])
            predicted = self._predict_player(player_context, history, fixtures, prior_history)
            players.append(predicted)

        self._apply_team_xg_audit(players)
        if position_filter != "ALL":
            players = [player for player in players if player["position"] == position_filter]
        players.sort(key=lambda item: item["predicted_total_points"], reverse=True)
        return players

    def _apply_team_xg_audit(self, players):
        fixture_totals = {}
        for player in players:
            for fixture in player.get("fixtures", []):
                key = (player["team"], fixture.get("event"), fixture.get("opponent"), fixture.get("home"))
                fixture_totals[key] = fixture_totals.get(key, 0.0) + to_float(fixture.get("predicted_goals"))

        for player in players:
            audits = []
            for fixture in player.get("fixtures", []):
                key = (player["team"], fixture.get("event"), fixture.get("opponent"), fixture.get("home"))
                player_sum = fixture_totals.get(key, 0.0)
                team_xg = to_float((fixture.get("fixture_model") or {}).get("team_xg"))
                ratio = player_sum / team_xg if team_xg > 0 else 0.0
                warning = team_xg > 0 and ratio > self.TEAM_XG_WARNING_RATIO
                fixture["team_xg_audit_warning"] = warning
                audits.append({
                    "event": fixture.get("event"),
                    "opponent": fixture.get("opponent"),
                    "home": fixture.get("home"),
                    "summed_player_xg": round(player_sum, 3),
                    "team_xg": round(team_xg, 3),
                    "ratio": round(ratio, 3),
                    "warning": warning,
                })
            goal_model = player.get("inputs", {}).get("goal_model", {})
            goal_model["team_xg_audit"] = audits
            goal_model["team_xg_warning"] = any(item["warning"] for item in audits)

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

    def _predict_player(self, player, history, fixtures, prior_history=None):
        position_points = self.POSITION_POINTS[player["element_type"]]
        current_matches = [match for match in list(history or []) if not self._ignore_minutes_match(match)]
        combined_history = [*list(prior_history or []), *list(history or [])]
        long_term_matches = [match for match in combined_history if not self._ignore_minutes_match(match)]
        recent_matches = long_term_matches[-6:]
        calculation_gameweek = max(
            self._next_event_id() or 1,
            max((to_int(match.get("round")) for match in current_matches), default=0) + 1,
        )
        minutes_context = self._predict_minutes(player, recent_matches)
        minutes_prediction = minutes_context["predicted_minutes"]
        goals_context = self._predict_goals(
            player,
            recent_matches,
            fixtures,
            minutes_prediction,
            long_term_matches,
        )
        goals_per_fixture = goals_context["predicted_goals_per_fixture"]
        assists_context = self._predict_assists(
            player,
            recent_matches,
            fixtures,
            minutes_prediction,
            long_term_matches,
        )
        assists_per_fixture = assists_context["predicted_assists_per_fixture"]
        clean_sheet_context = self._predict_clean_sheet_context(
            player,
            fixtures,
            minutes_context["probability_reaches_60"],
        )
        cs_per_fixture = clean_sheet_context["probability_per_fixture"]
        defensive_context = self._predict_defensive_contribution_context(player, recent_matches, minutes_prediction)
        defensive_per_fixture = defensive_context["points_per_fixture"]
        bonus_context = self._predict_bonus_context(
            player,
            current_matches,
            list(prior_history or []),
            recent_matches,
            minutes_prediction,
            calculation_gameweek,
        )
        bonus_per_fixture = bonus_context["points_per_fixture"]
        goalkeeper_context = self._predict_goalkeeper_context(
            player,
            recent_matches,
            fixtures,
            minutes_prediction,
        )
        save_points_per_fixture = goalkeeper_context["save_points_per_fixture"]
        goals_conceded_deductions = goalkeeper_context["goals_conceded_deductions"]
        yellows_per_fixture = self._predict_yellows(recent_matches)

        recent_sample_size = max(len(recent_matches), 1)
        recent_yellows_total = sum(to_float(match.get("yellow_cards")) for match in recent_matches)

        match_count = len(fixtures)
        minutes_points_per_fixture = minutes_context["minutes_points_per_fixture"]
        total_goals = goals_per_fixture * match_count
        total_assists = assists_per_fixture * match_count
        total_clean_sheets = cs_per_fixture * match_count
        total_defensive = defensive_per_fixture * match_count
        total_bonus = bonus_per_fixture * match_count
        total_save_points = save_points_per_fixture * match_count
        total_goals_conceded_deduction = sum(goals_conceded_deductions)
        total_yellows = yellows_per_fixture * match_count
        total_minutes_points = minutes_points_per_fixture * match_count

        total_points = (
            total_minutes_points
            + total_goals * position_points["goal"]
            + total_assists * 3
            + total_clean_sheets * position_points["clean_sheet"]
            + total_defensive
            + total_bonus
            + total_save_points
            - total_goals_conceded_deduction
            - total_yellows
        )

        def allocate_fixture_rates(raw_rates, target_total):
            raw_total = sum(raw_rates)
            if raw_total > 0:
                return [rate * target_total / raw_total for rate in raw_rates]
            if raw_rates:
                return [target_total / len(raw_rates) for _ in raw_rates]
            return []

        fixture_elo_contexts = [self._fixture_model_context(player["team"], fixture) for fixture in fixtures]
        fixture_attack_factors = [context["attack_factor"] for context in fixture_elo_contexts]
        fixture_goal_rates = allocate_fixture_rates(
            [
                goals_context["baseline_per_fixture"]
                * goals_context["finishing_adjustment"]
                * factor
                for factor in fixture_attack_factors
            ],
            total_goals,
        )
        fixture_assist_rates = allocate_fixture_rates(
            [
                assists_context["baseline_per_fixture"]
                * assists_context["conversion_adjustment"]
                * factor
                for factor in fixture_attack_factors
            ],
            total_assists,
        )
        fixture_clean_sheet_probabilities = allocate_fixture_rates(
            [detail["probability"] for detail in clean_sheet_context["fixtures"]],
            total_clean_sheets,
        )
        fixture_predictions = []
        for index, fixture in enumerate(fixtures):
            goal_rate = fixture_goal_rates[index]
            assist_rate = fixture_assist_rates[index]
            clean_sheet_probability = fixture_clean_sheet_probabilities[index]
            fixture_model = {
                key: round(value, 4) if isinstance(value, float) else value
                for key, value in fixture_elo_contexts[index].items()
            }
            predicted_points = (
                minutes_points_per_fixture
                + goal_rate * position_points["goal"]
                + assist_rate * 3
                + clean_sheet_probability * position_points["clean_sheet"]
                + defensive_per_fixture
                + bonus_per_fixture
                + save_points_per_fixture
                - goals_conceded_deductions[index]
                - yellows_per_fixture
            )
            fixture_predictions.append({
                "event": fixture.get("event"),
                "opponent": self.teams[fixture["team_a"] if fixture["is_home"] else fixture["team_h"]]["short_name"],
                "home": fixture["is_home"],
                "difficulty": fixture.get(
                    "difficulty",
                    fixture.get("team_h_difficulty") if fixture["is_home"] else fixture.get("team_a_difficulty"),
                ),
                "predicted_points": round(predicted_points, 2),
                "predicted_goals": round(goal_rate, 3),
                "predicted_assists": round(assist_rate, 3),
                "bonus_points": bonus_per_fixture,
                "save_points": save_points_per_fixture,
                "goals_conceded_deduction": goals_conceded_deductions[index],
                "yellow_card_deduction": yellows_per_fixture,
                "combined_attack_factor": round(fixture_attack_factors[index], 3),
                "fixture_model": fixture_model,
            })

        sample_matches = minutes_context["sample_matches"]

        return {
            "player_id": player["id"],
            "player_name": f"{player['first_name']} {player['second_name']}",
            "team": self.teams[player["team"]]["short_name"],
            "position": self.positions[player["element_type"]],
            "horizon": match_count,
            "fixtures": fixture_predictions,
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
                "save_points": round(total_save_points, 2),
                "goals_conceded_deduction": round(total_goals_conceded_deduction, 2),
                "yellow_cards": round(total_yellows, 2),
                "sub_60_penalty": 0.0,
            },
            "inputs": {
                "predicted_minutes_per_fixture": round(minutes_prediction, 2),
                "minutes_points_per_fixture": minutes_points_per_fixture,
                "minutes_points_full_threshold": self.FULL_MINUTES_POINTS_THRESHOLD,
                "minutes_sample": [
                    {
                        "round": match.get("round"),
                        "minutes": match.get("minutes", 0),
                        "starts": match.get("starts", 0),
                        "prior_season": bool(match.get("prior_season")),
                        "expected_goals": round(to_float(match.get("expected_goals")), 3),
                        "goals_scored": to_int(match.get("goals_scored")),
                        "expected_assists": round(to_float(match.get("expected_assists")), 3),
                        "assists": to_int(match.get("assists")),
                        "recoveries": round(to_float(match.get("recoveries")), 3),
                        "defensive_contribution": round(to_float(match.get("defensive_contribution")), 3),
                        "saves": to_int(match.get("saves")),
                        "bonus": round(to_float(match.get("bonus")), 3),
                        "yellow_cards": to_int(match.get("yellow_cards")),
                    }
                    for match in sample_matches
                ],
                "start_probability": round(minutes_context["start_probability"], 3),
                "minutes_if_starting": round(minutes_context["minutes_if_starting"], 2),
                "sub_appearance_probability": round(minutes_context["sub_appearance_probability"], 3),
                "minutes_if_substitute": round(minutes_context["minutes_if_substitute"], 2),
                "probability_reaches_60": round(minutes_context["probability_reaches_60"], 3),
                "goals_per_fixture": round(goals_per_fixture, 3),
                "goal_model": {
                    "recent_xg_total": round(goals_context["recent_xg_total"], 3),
                    "recent_goals_total": round(goals_context["recent_goals_total"], 3),
                    "sample_size": goals_context["sample_size"],
                    "baseline_per_fixture": round(goals_context["baseline_per_fixture"], 3),
                    "xg_per_90": round(goals_context["xg_per_90"], 3),
                    "used_team_position_fallback": goals_context["used_team_position_fallback"],
                    "used_team_position_prior": goals_context["used_team_position_prior"],
                    "observed_xg_per_90": round(goals_context["observed_xg_per_90"], 3),
                    "prior_xg_per_90": round(goals_context["prior_xg_per_90"], 3),
                    "prior_equivalent_minutes": goals_context["prior_equivalent_minutes"],
                    "missing_sample_fixtures": goals_context["missing_sample_fixtures"],
                    "observed_weight": round(goals_context["observed_weight"], 3),
                    "prior_weight": round(goals_context["prior_weight"], 3),
                    "long_term_xg_total": round(goals_context["long_term_xg_total"], 3),
                    "long_term_goals_total": round(goals_context["long_term_goals_total"], 3),
                    "long_term_minutes_total": goals_context["long_term_minutes_total"],
                    "long_term_xg_per_90": round(goals_context["long_term_xg_per_90"], 3),
                    "long_term_weight": goals_context["long_term_weight"],
                    "recent_weight": goals_context["recent_weight"],
                    "recent_finishing_adjustment": round(goals_context["recent_finishing_adjustment"], 3),
                    "long_term_finishing_adjustment": round(goals_context["long_term_finishing_adjustment"], 3),
                    "finishing_confidence": round(goals_context["finishing_confidence"], 3),
                    "finishing_adjustment_min": self.FINISHING_ADJUSTMENT_MIN,
                    "finishing_adjustment_max": self.FINISHING_ADJUSTMENT_MAX,
                    "fixture_factor": round(goals_context["fixture_factor"], 3),
                    "raw_finishing_adjustment": round(goals_context["raw_finishing_adjustment"], 3),
                    "finishing_adjustment": round(goals_context["finishing_adjustment"], 3),
                },
                "assists_per_fixture": round(assists_per_fixture, 3),
                "assist_model": {
                    "recent_xa_total": round(assists_context["recent_xa_total"], 3),
                    "recent_assists_total": round(assists_context["recent_assists_total"], 3),
                    "sample_size": assists_context["sample_size"],
                    "baseline_per_fixture": round(assists_context["baseline_per_fixture"], 3),
                    "xa_per_90": round(assists_context["xa_per_90"], 3),
                    "observed_xa_per_90": round(assists_context["observed_xa_per_90"], 3),
                    "prior_xa_per_90": round(assists_context["prior_xa_per_90"], 3),
                    "prior_equivalent_minutes": assists_context["prior_equivalent_minutes"],
                    "missing_sample_fixtures": assists_context["missing_sample_fixtures"],
                    "observed_weight": round(assists_context["observed_weight"], 3),
                    "prior_weight": round(assists_context["prior_weight"], 3),
                    "long_term_xa_total": round(assists_context["long_term_xa_total"], 3),
                    "long_term_minutes_total": assists_context["long_term_minutes_total"],
                    "long_term_xa_per_90": round(assists_context["long_term_xa_per_90"], 3),
                    "long_term_weight": assists_context["long_term_weight"],
                    "recent_weight": assists_context["recent_weight"],
                    "used_team_position_prior": assists_context["used_team_position_prior"],
                    "used_team_position_fallback": assists_context["used_team_position_fallback"],
                    "fixture_factor": round(assists_context["fixture_factor"], 3),
                    "raw_conversion_adjustment": round(assists_context["raw_conversion_adjustment"], 3),
                    "conversion_adjustment": round(assists_context["conversion_adjustment"], 3),
                },
                "clean_sheet_probability_per_fixture": round(cs_per_fixture, 3),
                "clean_sheet_model": clean_sheet_context,
                "defensive_contribution_per_fixture": round(defensive_per_fixture, 3),
                "defensive_contribution_model": defensive_context,
                "bonus_per_fixture": round(bonus_per_fixture, 3),
                "bonus_model": bonus_context,
                "goalkeeper_model": goalkeeper_context,
                "yellow_cards_per_fixture": round(yellows_per_fixture, 3),
                "yellow_card_model": {
                    "recent_yellow_cards_total": round(recent_yellows_total, 3),
                    "sample_size": recent_sample_size,
                },
                "position_goal_points": position_points["goal"],
                "position_clean_sheet_points": position_points["clean_sheet"],
            },
        }

    def _predict_minutes(self, player, recent_matches):
        sample_matches = list(recent_matches[-6:])
        match_count = len(sample_matches)
        starts = [match for match in sample_matches if to_int(match.get("starts")) > 0]
        substitute_appearances = [
            match for match in sample_matches
            if to_int(match.get("starts")) == 0 and to_int(match.get("minutes")) > 0
        ]
        reaches_60 = [match for match in sample_matches if to_int(match.get("minutes")) >= 60]
        start_probability = len(starts) / match_count if match_count else 0.0
        sub_appearance_probability = len(substitute_appearances) / match_count if match_count else 0.0
        minutes_if_starting = safe_mean((to_int(match.get("minutes")) for match in starts))
        minutes_if_substitute = safe_mean((to_int(match.get("minutes")) for match in substitute_appearances))
        predicted_minutes = round(clamp(
            start_probability * minutes_if_starting
            + sub_appearance_probability * minutes_if_substitute,
            0,
            90,
        ), 2)
        minutes_points_per_fixture = self._predict_minutes_points(predicted_minutes)
        return {
            "predicted_minutes": predicted_minutes,
            "minutes_points_per_fixture": minutes_points_per_fixture,
            "start_probability": start_probability,
            "minutes_if_starting": minutes_if_starting,
            "sub_appearance_probability": sub_appearance_probability,
            "minutes_if_substitute": minutes_if_substitute,
            "probability_reaches_60": len(reaches_60) / match_count if match_count else 0.0,
            "sample_matches": sample_matches,
        }

    def _predict_minutes_points(self, predicted_minutes):
        minutes = clamp(predicted_minutes, 0, 90)
        if minutes >= self.FULL_MINUTES_POINTS_THRESHOLD:
            return 2.0
        return 2.0 * minutes / 90

    def _ignore_minutes_match(self, match):
        is_unfinished_current_event = (
            self.current_event_id is not None
            and match.get("round") == self.current_event_id
            and match.get("team_h_score") is None
            and match.get("team_a_score") is None
        )
        return (match.get("minutes", 0) or 0) == 0 and is_unfinished_current_event

    def _team_position_rate(self, player, baselines):
        team = self.teams[player["team"]]["short_name"]
        position = self.positions[player["element_type"]]
        return baselines.get(
            f"{team}:{position}",
            baselines.get(f"*:{position}", 0.0),
        )

    def _attacking_rate_context(
        self,
        player,
        recent_matches,
        stat_field,
        baselines,
        long_term_matches=None,
    ):
        recent_total = sum(to_float(match.get(stat_field)) for match in recent_matches)
        recent_minutes = sum(to_int(match.get("minutes")) for match in recent_matches)
        observed_rate_per90 = recent_total * 90 / recent_minutes if recent_minutes > 0 else 0.0
        team = self.teams[player["team"]]["short_name"]
        position = self.positions[player["element_type"]]
        prior_available = f"{team}:{position}" in baselines or f"*:{position}" in baselines
        prior_rate_per90 = self._team_position_rate(player, baselines)
        missing_fixtures = max(self.ATTACKING_RATE_SAMPLE_FIXTURES - len(recent_matches), 0)
        prior_equivalent_minutes = (
            missing_fixtures * self.FULL_FIXTURE_MINUTES
            if prior_available
            else 0
        )
        evidence_minutes = recent_minutes + prior_equivalent_minutes

        if evidence_minutes > 0:
            blended_rate_per90 = (
                recent_total + prior_rate_per90 * prior_equivalent_minutes / 90
            ) * 90 / evidence_minutes
        else:
            blended_rate_per90 = prior_rate_per90

        observed_weight = recent_minutes / evidence_minutes if evidence_minutes > 0 else 0.0
        recent_blended_rate_per90 = blended_rate_per90
        long_term_matches = list(long_term_matches or recent_matches)
        long_term_total = sum(to_float(match.get(stat_field)) for match in long_term_matches)
        long_term_minutes = sum(to_int(match.get("minutes")) for match in long_term_matches)
        long_term_missing_fixtures = max(
            self.ATTACKING_RATE_SAMPLE_FIXTURES - len(long_term_matches),
            0,
        )
        long_term_prior_minutes = (
            long_term_missing_fixtures * self.FULL_FIXTURE_MINUTES
            if prior_available
            else 0
        )
        long_term_evidence_minutes = long_term_minutes + long_term_prior_minutes
        long_term_rate_per90 = (
            (
                long_term_total
                + prior_rate_per90 * long_term_prior_minutes / 90
            ) * 90 / long_term_evidence_minutes
            if long_term_evidence_minutes > 0
            else recent_blended_rate_per90
        )
        if long_term_minutes > 0 and recent_minutes > 0:
            blended_rate_per90 = (
                self.LONG_TERM_ATTACKING_WEIGHT * long_term_rate_per90
                + self.RECENT_ATTACKING_WEIGHT * recent_blended_rate_per90
            )
            long_term_weight = self.LONG_TERM_ATTACKING_WEIGHT
            recent_weight = self.RECENT_ATTACKING_WEIGHT
        elif long_term_minutes > 0:
            blended_rate_per90 = long_term_rate_per90
            long_term_weight = 1.0
            recent_weight = 0.0
        else:
            blended_rate_per90 = recent_blended_rate_per90
            long_term_weight = 0.0
            recent_weight = 1.0

        return {
            "recent_total": recent_total,
            "recent_minutes": recent_minutes,
            "observed_rate_per90": observed_rate_per90,
            "prior_rate_per90": prior_rate_per90,
            "prior_equivalent_minutes": prior_equivalent_minutes,
            "missing_fixtures": missing_fixtures,
            "observed_weight": observed_weight,
            "prior_weight": 1 - observed_weight,
            "blended_rate_per90": blended_rate_per90,
            "recent_blended_rate_per90": recent_blended_rate_per90,
            "long_term_total": long_term_total,
            "long_term_minutes": long_term_minutes,
            "long_term_rate_per90": long_term_rate_per90,
            "long_term_weight": long_term_weight,
            "recent_weight": recent_weight,
        }

    def _predict_goals(
        self,
        player,
        recent_matches,
        fixtures,
        minutes_prediction,
        long_term_matches=None,
    ):
        minutes = clamp(minutes_prediction, 0, 90)
        rate_context = self._attacking_rate_context(
            player,
            recent_matches,
            "expected_goals",
            self.team_position_xg_per90,
            long_term_matches,
        )
        recent_xg = rate_context["recent_total"]
        recent_goals = sum(float(match.get("goals_scored", 0) or 0) for match in recent_matches)
        sample_size = max(len(recent_matches), 1)
        used_team_position_fallback = rate_context["recent_minutes"] <= 0
        xg_per_90 = rate_context["blended_rate_per90"]
        xg_rate = xg_per_90 * (minutes / 90)
        long_term_matches = list(long_term_matches or recent_matches)
        long_term_xg = sum(to_float(match.get("expected_goals")) for match in long_term_matches)
        long_term_goals = sum(to_float(match.get("goals_scored")) for match in long_term_matches)
        recent_finishing_adjustment = clamp(
            (recent_goals + 1) / (recent_xg + 1),
            self.FINISHING_ADJUSTMENT_MIN,
            self.FINISHING_ADJUSTMENT_MAX,
        )
        long_term_finishing_adjustment = clamp(
            (long_term_goals + 1) / (long_term_xg + 1),
            self.FINISHING_ADJUSTMENT_MIN,
            self.FINISHING_ADJUSTMENT_MAX,
        )
        raw_finishing_adjustment = (
            self.LONG_TERM_ATTACKING_WEIGHT * long_term_finishing_adjustment
            + self.RECENT_ATTACKING_WEIGHT * recent_finishing_adjustment
        )
        finishing_confidence = clamp(
            rate_context["long_term_minutes"]
            / (self.ATTACKING_RATE_SAMPLE_FIXTURES * self.FULL_FIXTURE_MINUTES),
            0,
            1,
        )
        finishing_adjustment = 1 + finishing_confidence * (raw_finishing_adjustment - 1)
        fixture_factor = self._fixture_attack_factor(player["team"], fixtures)
        predicted = round(max(xg_rate * finishing_adjustment * fixture_factor, 0), 3)
        return {
            "predicted_goals_per_fixture": predicted,
            "recent_xg_total": recent_xg,
            "recent_goals_total": recent_goals,
            "sample_size": sample_size,
            "baseline_per_fixture": xg_rate,
            "xg_per_90": xg_per_90,
            "used_team_position_fallback": used_team_position_fallback,
            "used_team_position_prior": rate_context["prior_equivalent_minutes"] > 0,
            "observed_xg_per_90": rate_context["observed_rate_per90"],
            "prior_xg_per_90": rate_context["prior_rate_per90"],
            "prior_equivalent_minutes": rate_context["prior_equivalent_minutes"],
            "missing_sample_fixtures": rate_context["missing_fixtures"],
            "observed_weight": rate_context["observed_weight"],
            "prior_weight": rate_context["prior_weight"],
            "long_term_xg_total": long_term_xg,
            "long_term_goals_total": long_term_goals,
            "long_term_minutes_total": rate_context["long_term_minutes"],
            "long_term_xg_per_90": rate_context["long_term_rate_per90"],
            "long_term_weight": rate_context["long_term_weight"],
            "recent_weight": rate_context["recent_weight"],
            "recent_finishing_adjustment": recent_finishing_adjustment,
            "long_term_finishing_adjustment": long_term_finishing_adjustment,
            "finishing_confidence": finishing_confidence,
            "raw_finishing_adjustment": raw_finishing_adjustment,
            "finishing_adjustment": finishing_adjustment,
            "fixture_factor": fixture_factor,
        }

    def _predict_assists(
        self,
        player,
        recent_matches,
        fixtures,
        minutes_prediction,
        long_term_matches=None,
    ):
        minutes = clamp(minutes_prediction, 0, 90)
        rate_context = self._attacking_rate_context(
            player,
            recent_matches,
            "expected_assists",
            self.team_position_xa_per90,
            long_term_matches,
        )
        recent_xa = rate_context["recent_total"]
        recent_assists = sum(float(match.get("assists", 0) or 0) for match in recent_matches)
        sample_size = max(len(recent_matches), 1)
        xa_rate_per90 = rate_context["blended_rate_per90"]
        xa_rate = xa_rate_per90 * (minutes / 90)
        raw_conversion_adjustment = clamp((recent_assists + 1) / (recent_xa + 1), 0.7, 1.2)
        conversion_adjustment = 1 + rate_context["observed_weight"] * (raw_conversion_adjustment - 1)
        fixture_factor = self._fixture_attack_factor(player["team"], fixtures)
        predicted = round(max(xa_rate * conversion_adjustment * fixture_factor, 0), 3)
        return {
            "predicted_assists_per_fixture": predicted,
            "recent_xa_total": recent_xa,
            "recent_assists_total": recent_assists,
            "sample_size": sample_size,
            "baseline_per_fixture": xa_rate,
            "xa_per_90": xa_rate_per90,
            "observed_xa_per_90": rate_context["observed_rate_per90"],
            "prior_xa_per_90": rate_context["prior_rate_per90"],
            "prior_equivalent_minutes": rate_context["prior_equivalent_minutes"],
            "missing_sample_fixtures": rate_context["missing_fixtures"],
            "observed_weight": rate_context["observed_weight"],
            "prior_weight": rate_context["prior_weight"],
            "long_term_xa_total": rate_context["long_term_total"],
            "long_term_minutes_total": rate_context["long_term_minutes"],
            "long_term_xa_per_90": rate_context["long_term_rate_per90"],
            "long_term_weight": rate_context["long_term_weight"],
            "recent_weight": rate_context["recent_weight"],
            "used_team_position_prior": rate_context["prior_equivalent_minutes"] > 0,
            "used_team_position_fallback": rate_context["recent_minutes"] <= 0,
            "raw_conversion_adjustment": raw_conversion_adjustment,
            "conversion_adjustment": conversion_adjustment,
            "fixture_factor": fixture_factor,
        }

    def _predict_clean_sheet_context(self, player, fixtures, probability_reaches_60=1.0):
        probabilities = []
        fixture_details = []
        for fixture in fixtures:
            model = self._fixture_model_context(player["team"], fixture)
            team_probability = model["team_clean_sheet_probability"]
            player_probability = team_probability * probability_reaches_60
            opponent_team_id = fixture["team_a"] if fixture["is_home"] else fixture["team_h"]
            probabilities.append(player_probability)
            detail = {
                "event": fixture.get("event"),
                "opponent": self.teams[opponent_team_id]["short_name"],
                "home": fixture["is_home"],
                "method": model["method"],
                "team_probability": round(team_probability, 4),
                "probability_reaches_60": round(probability_reaches_60, 4),
                "probability": round(player_probability, 4),
                "opponent_xg": round(model["opponent_xg"], 4),
            }
            fixture_details.append(detail)
        if not probabilities:
            return {"probability_per_fixture": 0.0, "fixtures": []}
        return {
            "probability_per_fixture": round(sum(probabilities) / len(probabilities), 3),
            "fixtures": fixture_details,
        }

    def _predict_clean_sheet(self, player, fixtures):
        return self._predict_clean_sheet_context(player, fixtures)["probability_per_fixture"]

    def _predict_defensive_contribution_context(self, player, recent_matches, minutes_prediction):
        position = player["element_type"]
        if position == 1:
            return {
                "method": "ineligible_position",
                "threshold": None,
                "qualifying_fixtures": 0,
                "sample_size": len(recent_matches),
                "expected_minutes": round(minutes_prediction, 2),
                "points_per_fixture": 0.0,
            }
        threshold = 10 if position == 2 else 12
        contributions = [to_float(match.get("defensive_contribution")) for match in recent_matches]
        qualifying_fixtures = sum(value >= threshold for value in contributions)
        sample_size = len(recent_matches)
        points_per_fixture = round(2 * qualifying_fixtures / sample_size, 3) if sample_size else 0.0
        return {
            "method": "empirical_threshold_frequency",
            "threshold": threshold,
            "qualifying_fixtures": qualifying_fixtures,
            "sample_size": sample_size,
            "recent_contributions": contributions,
            "expected_minutes": round(minutes_prediction, 2),
            "points_per_fixture": points_per_fixture,
        }

    def _predict_defensive_contribution(self, player, recent_matches, minutes_prediction=None):
        if minutes_prediction is None:
            minutes_prediction = self._predict_minutes(player, recent_matches)["predicted_minutes"]
        return self._predict_defensive_contribution_context(
            player,
            recent_matches,
            minutes_prediction,
        )["points_per_fixture"]

    def _predict_goalkeeper_context(self, player, recent_matches, fixtures, minutes_prediction):
        position = player["element_type"]
        eligible_for_conceded_deduction = position in (1, 2)
        save_points_total = 0.0
        save_minutes_total = 0
        if position == 1:
            save_points_total = sum(to_int(match.get("saves")) // 3 for match in recent_matches)
            save_minutes_total = sum(to_int(match.get("minutes")) for match in recent_matches)
        save_points_per_90 = (
            save_points_total * 90 / save_minutes_total
            if save_minutes_total > 0
            else 0.0
        )
        save_points_per_fixture = round(
            save_points_per_90 * clamp(minutes_prediction, 0, 90) / 90,
            3,
        )

        deductions = []
        fixture_details = []
        for fixture in fixtures:
            model = self._fixture_model_context(player["team"], fixture)
            opponent_xg = to_float(model.get("opponent_xg"))
            expected_goals_conceded = (
                opponent_xg * clamp(minutes_prediction, 0, 90) / 90
                if eligible_for_conceded_deduction
                else 0.0
            )
            expected_deduction = (
                expected_goals_conceded / 2
                - (1 - math.exp(-2 * expected_goals_conceded)) / 4
                if eligible_for_conceded_deduction
                else 0.0
            )
            expected_deduction = round(max(expected_deduction, 0.0), 3)
            deductions.append(expected_deduction)
            opponent_team_id = fixture["team_a"] if fixture["is_home"] else fixture["team_h"]
            fixture_details.append({
                "event": fixture.get("event"),
                "opponent": self.teams[opponent_team_id]["short_name"],
                "home": fixture["is_home"],
                "opponent_xg": round(opponent_xg, 3),
                "expected_goals_conceded": round(expected_goals_conceded, 3),
                "expected_deduction": expected_deduction,
            })

        return {
            "save_method": "historical_save_points_per_90" if position == 1 else "not_goalkeeper",
            "save_points_sample_total": round(save_points_total, 3),
            "save_minutes_sample_total": save_minutes_total,
            "save_points_per_90": round(save_points_per_90, 3),
            "save_points_per_fixture": save_points_per_fixture,
            "goals_conceded_method": "poisson_complete_pairs" if eligible_for_conceded_deduction else "ineligible_position",
            "goals_conceded_deductions": deductions,
            "goals_conceded_deduction_per_fixture": round(safe_mean(deductions), 3),
            "fixtures": fixture_details,
            "expected_minutes": round(minutes_prediction, 2),
        }

    def _bonus_rate_context(self, matches):
        bonus_total = sum(to_float(match.get("bonus")) for match in matches)
        minutes_total = sum(to_int(match.get("minutes")) for match in matches)
        bonus_per_90 = bonus_total * 90 / minutes_total if minutes_total > 0 else None
        return {
            "bonus_total": bonus_total,
            "minutes_total": minutes_total,
            "sample_size": len(matches),
            "bonus_per_90": bonus_per_90,
        }

    def _predict_bonus_context(
        self,
        player,
        current_matches,
        prior_history,
        recent_matches,
        minutes_prediction,
        calculation_gameweek,
    ):
        position = self.positions[player["element_type"]]
        position_prior_available = position in self.position_bonus_per90
        position_prior_per_90 = to_float(self.position_bonus_per90.get(position))
        prior_player_context = self._bonus_rate_context(list(prior_history)[-6:])
        if prior_player_context["bonus_per_90"] is not None:
            fallback_per_90 = prior_player_context["bonus_per_90"]
            fallback_source = "previous-season player rate"
        elif position_prior_available:
            fallback_per_90 = position_prior_per_90
            fallback_source = f"previous-season league {position} average"
        else:
            fallback_per_90 = 0.0
            fallback_source = "zero because no player or positional history is available"

        if calculation_gameweek <= 6:
            recent_context = self._bonus_rate_context(recent_matches)
            missing_sample_fixtures = max(self.ATTACKING_RATE_SAMPLE_FIXTURES - len(recent_matches), 0)
            position_fill_minutes = (
                missing_sample_fixtures * self.FULL_FIXTURE_MINUTES
                if position_prior_available
                else 0
            )
            evidence_minutes = recent_context["minutes_total"] + position_fill_minutes
            if evidence_minutes > 0:
                bonus_per_90 = (
                    recent_context["bonus_total"]
                    + position_prior_per_90 * position_fill_minutes / 90
                ) * 90 / evidence_minutes
            else:
                bonus_per_90 = fallback_per_90
            method = "early_six_fixture_position_fill"
            season_context = self._bonus_rate_context(current_matches)
            recent_six_context = recent_context
        else:
            season_context = self._bonus_rate_context(current_matches)
            recent_six_context = self._bonus_rate_context(list(current_matches)[-6:])
            season_rate = (
                season_context["bonus_per_90"]
                if season_context["bonus_per_90"] is not None
                else fallback_per_90
            )
            recent_six_rate = (
                recent_six_context["bonus_per_90"]
                if recent_six_context["bonus_per_90"] is not None
                else fallback_per_90
            )
            bonus_per_90 = 0.75 * season_rate + 0.25 * recent_six_rate
            missing_sample_fixtures = 0
            position_fill_minutes = 0
            method = "season_75_recent_six_25"

        points_per_fixture = round(clamp(
            bonus_per_90 * clamp(minutes_prediction, 0, 90) / 90,
            0,
            3,
        ), 3)
        return {
            "method": method,
            "calculation_gameweek": calculation_gameweek,
            "expected_minutes": round(minutes_prediction, 2),
            "bonus_per_90": round(bonus_per_90, 3),
            "points_per_fixture": points_per_fixture,
            "position": position,
            "position_prior_per_90": round(position_prior_per_90, 3),
            "missing_sample_fixtures": missing_sample_fixtures,
            "position_fill_minutes": position_fill_minutes,
            "prior_player_bonus_per_90": (
                round(prior_player_context["bonus_per_90"], 3)
                if prior_player_context["bonus_per_90"] is not None
                else None
            ),
            "fallback_bonus_per_90": round(fallback_per_90, 3),
            "fallback_source": fallback_source,
            "early_sample_bonus_total": round(sum(to_float(match.get("bonus")) for match in recent_matches), 3),
            "early_sample_minutes_total": sum(to_int(match.get("minutes")) for match in recent_matches),
            "early_sample_size": len(recent_matches),
            "season_bonus_total": round(season_context["bonus_total"], 3),
            "season_minutes_total": season_context["minutes_total"],
            "season_sample_size": season_context["sample_size"],
            "season_bonus_per_90": round(
                season_context["bonus_per_90"] if season_context["bonus_per_90"] is not None else fallback_per_90,
                3,
            ),
            "recent_six_bonus_total": round(recent_six_context["bonus_total"], 3),
            "recent_six_minutes_total": recent_six_context["minutes_total"],
            "recent_six_sample_size": recent_six_context["sample_size"],
            "recent_six_bonus_per_90": round(
                recent_six_context["bonus_per_90"] if recent_six_context["bonus_per_90"] is not None else fallback_per_90,
                3,
            ),
        }

    def _predict_yellows(self, recent_matches):
        yellows = sum(float(match.get("yellow_cards", 0) or 0) for match in recent_matches)
        sample_size = max(len(recent_matches), 1)
        return round(clamp(yellows / sample_size, 0, 0.5), 3)

    def _fixture_model_context(self, team_id, fixture):
        home_id = fixture["team_h"]
        away_id = fixture["team_a"]
        home_elo = self.team_elo_ratings.get(str(home_id))
        away_elo = self.team_elo_ratings.get(str(away_id))
        if home_elo is not None and away_elo is not None:
            values = elo_fixture_values(home_elo, away_elo)
            is_home = team_id == home_id
            return {
                "method": "elo",
                "attack_factor": values["home_factor"] if is_home else values["away_factor"],
                "team_xg": values["home_xg"] if is_home else values["away_xg"],
                "opponent_xg": values["away_xg"] if is_home else values["home_xg"],
                "team_clean_sheet_probability": (
                    values["home_clean_sheet_probability"]
                    if is_home else values["away_clean_sheet_probability"]
                ),
                "elo_home_raw": round(values["home_elo"], 1),
                "elo_away_raw": round(values["away_elo"], 1),
                "elo_home_effective": round(values["effective_home_elo"], 1),
                "elo_delta": round(values["delta_elo"], 1),
            }

        # Leakage-safe historical fallback: these strengths are reconstructed
        # from results available before the backtest window. Official FDR is
        # deliberately never used by the new fixture model.
        team = self._strength_team(team_id)
        if fixture["is_home"]:
            own_attack = self._as_float(team.get("strength_attack_home"))
            opponent = self._strength_team(away_id)
            opponent_defence = self._as_float(opponent.get("strength_defence_away"))
            own_defence = self._as_float(team.get("strength_defence_home"))
            opponent_attack = self._as_float(opponent.get("strength_attack_away"))
        else:
            own_attack = self._as_float(team.get("strength_attack_away"))
            opponent = self._strength_team(home_id)
            opponent_defence = self._as_float(opponent.get("strength_defence_home"))
            own_defence = self._as_float(team.get("strength_defence_away"))
            opponent_attack = self._as_float(opponent.get("strength_attack_home"))
        attack_factor = clamp(1 + (own_attack - opponent_defence) / 50, 0.7, 1.3)
        defence_advantage = clamp((own_defence - opponent_attack) / 40, -0.4, 0.4)
        clean_sheet_probability = clamp(0.3 + defence_advantage, 0.05, 0.65)
        return {
            "method": "historical_strength_fallback",
            "attack_factor": attack_factor,
            "team_xg": ELO_GOALS_PER_TEAM * attack_factor,
            "opponent_xg": -math.log(clean_sheet_probability),
            "team_clean_sheet_probability": clean_sheet_probability,
        }

    def _fixture_attack_factor(self, team_id, fixtures):
        factors = [self._fixture_model_context(team_id, fixture)["attack_factor"] for fixture in fixtures]
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

    def __init__(self, bootstrap, element_summaries, elo_insights=None, prior_season_data=None, elo_snapshots=None):
        self.bootstrap = bootstrap
        self.element_summaries = element_summaries
        self.elo_insights = elo_insights or {}
        self.prior_season_data = prior_season_data or {}
        self.elo_snapshots = elo_snapshots or {"seasons": {}}
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

    def _elo_ratings_for_gameweek(self, start_gw):
        season = bootstrap_season_slug(self.bootstrap)
        snapshots = self.elo_snapshots.get("seasons", {}).get(season, [])
        eligible = [
            snapshot for snapshot in snapshots
            if to_int(snapshot.get("effective_from_gw")) <= start_gw
            and len(snapshot.get("ratings", {})) == 20
        ]
        if not eligible:
            return {}
        selected = max(eligible, key=lambda item: to_int(item.get("effective_from_gw")))
        return selected.get("ratings", {})

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
            team_elo_ratings=self._elo_ratings_for_gameweek(start_gw),
            prior_histories=self.prior_season_data.get("sources", {}).get(source, {}).get("histories_by_code", {}),
            team_position_xg_per90=self.prior_season_data.get("sources", {}).get(source, {}).get("team_position_xg_per90", {}),
            team_position_xa_per90=self.prior_season_data.get("sources", {}).get(source, {}).get("team_position_xa_per90", {}),
            position_bonus_per90=self.prior_season_data.get("sources", {}).get(source, {}).get("position_bonus_per90", {}),
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
            predicted = predictor._predict_player(
                player_context,
                history_before,
                fixtures,
                predictor.prior_histories.get(str(player.get("code")), []),
            )
            actual_points = round(sum(to_float(match.get("total_points")) for match in target_matches), 2)
            actual_components = aggregate_actual_components(player, target_matches)
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
                    "predicted_components": {
                        "minutes_points": predicted["components"]["minutes_points"],
                        "goal_points": predicted["components"]["goal_points"],
                        "assist_points": predicted["components"]["assist_points"],
                        "clean_sheet_points": predicted["components"]["clean_sheet_points"],
                        "defensive_contribution_points": predicted["components"]["defensive_contribution_points"],
                        "bonus_points": predicted["components"]["bonus_points"],
                        "yellow_deduction": predicted["components"]["yellow_cards"],
                        "other_points": round(
                            predicted["components"]["save_points"]
                            - predicted["components"]["goals_conceded_deduction"],
                            3,
                        ),
                    },
                    "predicted_stats": {
                        "goals": predicted["components"]["goals"],
                        "assists": predicted["components"]["assists"],
                        "clean_sheets": predicted["components"]["clean_sheets"],
                        "bonus": predicted["components"]["bonus_points"],
                        "yellow_cards": predicted["components"]["yellow_cards"],
                        "expected_goals": round(predicted["inputs"]["goals_per_fixture"] * len(fixtures), 3),
                        "expected_assists": round(predicted["inputs"]["assists_per_fixture"] * len(fixtures), 3),
                        "defensive_contribution": predicted["components"]["defensive_contribution_points"],
                    },
                    "actual_components": actual_components,
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
                row["predicted_components"]["minutes_points"],
                row["predicted_components"]["goal_points"],
                row["predicted_components"]["assist_points"],
                row["predicted_components"]["clean_sheet_points"],
                row["predicted_components"]["defensive_contribution_points"],
                row["predicted_components"]["bonus_points"],
                row["predicted_components"]["yellow_deduction"],
                row["predicted_components"]["other_points"],
                row["actual_components"]["minutes_points"],
                row["actual_components"]["goal_points"],
                row["actual_components"]["assist_points"],
                row["actual_components"]["clean_sheet_points"],
                row["actual_components"]["defensive_contribution_points"],
                row["actual_components"]["bonus_points"],
                row["actual_components"]["yellow_deduction"],
                row["actual_components"]["other_points"],
                row["predicted_stats"]["goals"],
                row["predicted_stats"]["assists"],
                row["predicted_stats"]["clean_sheets"],
                row["predicted_stats"]["bonus"],
                row["predicted_stats"]["yellow_cards"],
                row["predicted_stats"]["expected_goals"],
                row["predicted_stats"]["expected_assists"],
                row["predicted_stats"]["defensive_contribution"],
                row["actual_components"]["goals"],
                row["actual_components"]["assists"],
                row["actual_components"]["clean_sheets"],
                row["actual_components"]["bonus"],
                row["actual_components"]["yellow_cards"],
                row["actual_components"]["expected_goals"],
                row["actual_components"]["expected_assists"],
                row["actual_components"]["defensive_contribution"],
            ]
            for row in rows
        ]

    def _pack_window(self, payload, include_rows=True):
        packed = {
            "label": payload["label"],
            "summary": payload["summary"],
            "attribution": self._window_attribution(payload["rows"]),
        }
        if include_rows:
            packed["rows"] = self._pack_rows(payload["rows"])
        return packed

    def _window_attribution(self, rows):
        totals = {
            "predicted_points": 0.0,
            "actual_points": 0.0,
            "predicted_components": {
                "minutes_points": 0.0,
                "goal_points": 0.0,
                "assist_points": 0.0,
                "clean_sheet_points": 0.0,
                "defensive_contribution_points": 0.0,
                "bonus_points": 0.0,
                "yellow_deduction": 0.0,
                "other_points": 0.0,
            },
            "actual_components": {
                "minutes_points": 0.0,
                "goal_points": 0.0,
                "assist_points": 0.0,
                "clean_sheet_points": 0.0,
                "defensive_contribution_points": 0.0,
                "bonus_points": 0.0,
                "yellow_deduction": 0.0,
                "other_points": 0.0,
            },
            "predicted_stats": {
                "goals": 0.0,
                "assists": 0.0,
                "clean_sheets": 0.0,
                "bonus": 0.0,
                "yellow_cards": 0.0,
                "expected_goals": 0.0,
                "expected_assists": 0.0,
                "defensive_contribution": 0.0,
            },
            "actual_stats": {
                "goals": 0.0,
                "assists": 0.0,
                "clean_sheets": 0.0,
                "bonus": 0.0,
                "yellow_cards": 0.0,
                "expected_goals": 0.0,
                "expected_assists": 0.0,
                "defensive_contribution": 0.0,
            },
        }
        for row in rows:
            totals["predicted_points"] += row["predicted_points"]
            totals["actual_points"] += row["actual_points"]
            for key in totals["predicted_components"]:
                totals["predicted_components"][key] += row["predicted_components"][key]
                totals["actual_components"][key] += row["actual_components"][key]
            for key in totals["predicted_stats"]:
                totals["predicted_stats"][key] += row["predicted_stats"][key]
                totals["actual_stats"][key] += row["actual_components"][key] if key in row["actual_components"] else row["actual_components"].get(key, 0)
        for namespace in ("predicted_components", "actual_components", "predicted_stats", "actual_stats"):
            for key, value in totals[namespace].items():
                totals[namespace][key] = round(value, 3)
        totals["predicted_points"] = round(totals["predicted_points"], 2)
        totals["actual_points"] = round(totals["actual_points"], 2)
        return totals

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
                    window_payload["sources"][source] = self._pack_window(evaluated, include_rows=False)
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
        self.prior_season_data = load_prior_season_data()
        self.elo_snapshots = load_elo_snapshots()
        self.client = FPLClient(api_base)
        self.elo_client = FPLEloInsightsClient(elo_base)

    def get_predictions(self, horizon, position_filter, start_event_id=None, source="official"):
        with self.cache.lock:
            stale_refresh_error = None
            if self.cache.source_data_stale() or self.cache.prediction_stale():
                try:
                    stale_refresh_error = self._refresh_data()
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
                "source_latest_gameweek": self._source_latest_gameweek(source),
                "used_cached_data": stale_refresh_error is not None,
                "refresh_warning": stale_refresh_error,
                "fixture_model": {
                    "method": "team_elo_tanh",
                    "elo_source_url": (self.cache.get_elo_insights() or {}).get("team_elo_source_url"),
                    "elo_fetched_at": (self.cache.get_elo_insights() or {}).get("team_elo_fetched_at"),
                    "home_advantage": ELO_HOME_ADVANTAGE,
                    "deviation_cap": ELO_FACTOR_DEVIATION,
                    "scale": ELO_SCALE,
                    "goals_per_team": ELO_GOALS_PER_TEAM,
                },
                "available_gameweeks": available_gameweeks,
                "players": results,
            }

    def get_backtest_dataset(self, recompute=False):
        with self.cache.lock:
            stale_refresh_error = None
            if recompute or self.cache.source_data_stale():
                try:
                    stale_refresh_error = self._refresh_data()
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
                    stale_refresh_error = self._refresh_data()
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
        elo_warning = None
        try:
            elo_dataset = self.elo_client.get_dataset(bootstrap)
            self.cache.set_elo_insights(elo_dataset)
            self._record_elo_snapshot(bootstrap, elo_dataset)
        except (HTTPError, URLError, TimeoutError, ValueError) as exc:
            cached_elo = self.cache.get_elo_insights() or {}
            expected_ids = {str(team["id"]) for team in bootstrap.get("teams", [])}
            if (
                cached_elo.get("season") != bootstrap_season_slug(bootstrap)
                or set(cached_elo.get("team_elo_ratings", {})) != expected_ids
            ):
                raise
            elo_warning = f"{exc} Retained the last valid 20-team Elo snapshot."
        self.cache.save()
        return elo_warning

    def _record_elo_snapshot(self, bootstrap, elo_dataset):
        ratings = elo_dataset.get("team_elo_ratings", {})
        if len(ratings) != 20:
            return
        season = bootstrap_season_slug(bootstrap)
        effective_from_gw = self._available_gameweeks(bootstrap)[0]
        seasons = self.elo_snapshots.setdefault("seasons", {})
        snapshots = seasons.setdefault(season, [])
        existing = next(
            (item for item in snapshots if to_int(item.get("effective_from_gw")) == effective_from_gw),
            None,
        )
        payload = {
            "effective_from_gw": effective_from_gw,
            "captured_at": elo_dataset.get("team_elo_fetched_at") or now_utc().isoformat(),
            "source_url": elo_dataset.get("team_elo_source_url"),
            "ratings": {str(key): float(value) for key, value in ratings.items()},
        }
        if existing is not None:
            if existing.get("ratings") == payload["ratings"]:
                return
            snapshots[snapshots.index(existing)] = payload
        else:
            snapshots.append(payload)
            snapshots.sort(key=lambda item: to_int(item.get("effective_from_gw")))
        ELO_SNAPSHOTS_PATH.write_text(json.dumps(self.elo_snapshots, indent=2) + "\n")

    def _current_team_elos(self):
        ratings = (self.cache.get_elo_insights() or {}).get("team_elo_ratings", {})
        bootstrap = self.cache.get_bootstrap() or {}
        expected_ids = {str(team["id"]) for team in bootstrap.get("teams", [])}
        if len(expected_ids) != 20 or set(ratings) != expected_ids:
            raise RuntimeError(
                "No valid 20-team Elo snapshot is available. Refresh source data before predicting."
            )
        return ratings

    def _predictor_for_source(self, bootstrap, source):
        prior_source = self._prior_source(bootstrap, source)
        if source == "elo":
            elo_data = self.cache.get_elo_insights() or {}
            return Predictor(
                bootstrap,
                self.cache.data["element_summaries"],
                history_overrides=elo_data.get("history_by_player", {}),
                player_overrides=elo_data.get("player_overrides", {}),
                team_strengths=elo_data.get("team_strengths", {}),
                team_elo_ratings=self._current_team_elos(),
                prior_histories=prior_source.get("histories_by_code", {}),
                team_position_xg_per90=prior_source.get("team_position_xg_per90", {}),
                team_position_xa_per90=prior_source.get("team_position_xa_per90", {}),
                position_bonus_per90=prior_source.get("position_bonus_per90", {}),
            )
        return Predictor(
            bootstrap,
            self.cache.data["element_summaries"],
            prior_histories=prior_source.get("histories_by_code", {}),
            team_position_xg_per90=prior_source.get("team_position_xg_per90", {}),
            team_position_xa_per90=prior_source.get("team_position_xa_per90", {}),
            position_bonus_per90=prior_source.get("position_bonus_per90", {}),
            team_elo_ratings=self._current_team_elos(),
        )

    def _source_latest_gameweek(self, source):
        if source == "elo":
            return (self.cache.get_elo_insights() or {}).get("latest_finished_gw")
        bootstrap = self.cache.get_bootstrap() or {}
        current_player_ids = {str(player["id"]) for player in bootstrap.get("elements", [])}
        rounds = [
            to_int(match.get("round"))
            for player_id, summary in self.cache.data.get("element_summaries", {}).items()
            if str(player_id) in current_player_ids
            for match in summary.get("history", [])
            if match.get("round") is not None
        ]
        return max(rounds, default=None)

    def _prior_source(self, bootstrap, source):
        if self.prior_season_data.get("next_season") != bootstrap_season_slug(bootstrap):
            return {}
        return self.prior_season_data.get("sources", {}).get(source, {})

    def _backtest_engine(self, bootstrap):
        return BacktestEngine(
            bootstrap,
            self.cache.data["element_summaries"],
            self.cache.get_elo_insights(),
            # The archive seeds live 2026-27 predictions. It cannot seed a
            # 2025-26 backtest without leaking later matches from that season.
            {},
            self.elo_snapshots,
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
        if parsed.path == "/api/fpl":
            self._handle_fpl_proxy(parsed)
            return
        if parsed.path == "/api/health":
            self._write_json({"status": "ok", "time": now_utc().isoformat()})
            return
        file_path = STATIC_FILES.get(parsed.path)
        if file_path and file_path.exists():
            self._serve_file(file_path)
            return
        if parsed.path.startswith("/data/"):
            candidate = (ROOT / parsed.path.lstrip("/")).resolve()
            if candidate.is_file() and str(candidate).startswith(str((ROOT / "data").resolve())):
                self._serve_file(candidate)
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

    def _handle_fpl_proxy(self, parsed):
        query = parse_qs(parsed.query)
        try:
            path = validated_fpl_proxy_path(query.get("path", [""])[0])
            request = Request(
                f"{FPL_API_BASE}/{path}/",
                headers={"User-Agent": "FPL-Predictor-Lineup/1.0"},
            )
            with urlopen(request, timeout=15) as response:
                payload = json.load(response)
            self._write_json(payload)
        except ValueError as exc:
            self._write_json({"error": str(exc)}, status=400)
        except HTTPError as exc:
            status = exc.code if 400 <= exc.code < 500 else 502
            self._write_json({"error": "Official FPL request failed.", "detail": str(exc)}, status=status)
        except (URLError, TimeoutError) as exc:
            self._write_json({"error": "Official FPL request failed.", "detail": str(exc)}, status=502)

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
