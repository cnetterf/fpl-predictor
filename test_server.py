import math
import unittest
from datetime import timedelta
from urllib.error import URLError

from server import (
    App,
    CLUBELO_TEAM_KEYS,
    ClubEloClient,
    FPLEloInsightsClient,
    Predictor,
    actual_defensive_contribution_points,
    elo_fixture_values,
    now_utc,
    validated_fpl_proxy_path,
)


class ClubEloClientTests(unittest.TestCase):
    def setUp(self):
        self.client = ClubEloClient("http://api.example.test", "https://example.test/Ranking")
        self.bootstrap = {
            "teams": [
                {"id": team_id, "short_name": short_name}
                for team_id, short_name in enumerate(CLUBELO_TEAM_KEYS, start=1)
            ],
        }

    def test_maps_all_current_fpl_teams_to_clubelo_keys(self):
        club_ratings = [
            (club_key, 1700 + team_id)
            for team_id, club_key in enumerate(CLUBELO_TEAM_KEYS.values(), start=1)
        ]

        ratings = self.client._validated_ratings(club_ratings, self.bootstrap)

        self.assertEqual(len(ratings), 20)
        self.assertEqual(ratings["1"], 1701)
        self.assertEqual(ratings["20"], 1720)

    def test_rejects_partial_clubelo_coverage(self):
        with self.assertRaisesRegex(ValueError, "received 1"):
            self.client._validated_ratings([("Arsenal", 2011)], self.bootstrap)

    def test_ranking_page_fallback_extracts_one_coherent_dated_snapshot(self):
        rows = ",".join(
            repr([
                f'<td><a href="/ENG"><img alt="ENG"></a><a href="/{club_key}">{short_name}</a></td>',
                str(1700 + team_id),
            ])
            for team_id, (short_name, club_key) in enumerate(CLUBELO_TEAM_KEYS.items(), start=1)
        )
        html = f'<h1><a href="/2026-09-02/Ranking">Ranking</a></h1><script>const eloData = [{rows}];</script>'

        def fake_get_text(url, timeout=15):
            if url.startswith("http://api.example.test"):
                raise TimeoutError("API timed out")
            return html

        self.client._get_text = fake_get_text
        snapshot = self.client.get_snapshot(self.bootstrap)

        self.assertEqual(snapshot["effective_date"], "2026-09-02")
        self.assertEqual(snapshot["method"], "clubelo_ranking")
        self.assertEqual(len(snapshot["ratings"]), 20)
        self.assertFalse(snapshot["used_fallback"])


class FPLEloInsightsClientTests(unittest.TestCase):
    def test_player_dataset_does_not_depend_on_team_elo_column(self):
        client = FPLEloInsightsClient("https://example.test/data")
        bootstrap = {
            "events": [{"id": 1, "finished": True, "deadline_time": "2026-08-22T10:00:00Z"}],
        }
        team_rows = [{"id": str(team_id), "elo": ""} for team_id in range(1, 21)]
        player_rows = [{"id": "1", "gw": "1", "minutes": "90", "expected_goals": "0.5"}]
        client._get_csv = lambda path: team_rows if path.endswith("teams.csv") else player_rows

        dataset = client.get_dataset(bootstrap)

        self.assertEqual(dataset["latest_finished_gw"], 1)
        self.assertEqual(dataset["history_by_player"]["1"][0]["expected_goals"], 0.5)


class RefreshIndependenceTests(unittest.TestCase):
    def test_fallback_requires_a_complete_snapshot_captured_within_30_days(self):
        bootstrap = {
            "events": [{"deadline_time": "2026-08-22T10:00:00Z"}],
            "teams": [{"id": team_id} for team_id in range(1, 21)],
        }
        complete_ratings = {str(team_id): 1700 + team_id for team_id in range(1, 21)}
        valid_snapshot = {
            "captured_at": (now_utc() - timedelta(days=2)).isoformat(),
            "ratings": complete_ratings,
        }
        app = App.__new__(App)
        app.elo_snapshots = {
            "seasons": {
                "2026-2026": [
                    {
                        "captured_at": (now_utc() - timedelta(days=31)).isoformat(),
                        "ratings": complete_ratings,
                    },
                    {
                        "captured_at": (now_utc() - timedelta(days=1)).isoformat(),
                        "ratings": {"1": 1701},
                    },
                    valid_snapshot,
                ],
            },
        }

        self.assertIs(app._latest_elo_snapshot(bootstrap), valid_snapshot)
        app.elo_snapshots["seasons"]["2026-2026"] = app.elo_snapshots["seasons"]["2026-2026"][:2]
        self.assertIsNone(app._latest_elo_snapshot(bootstrap))

    def test_missing_fpl_core_data_does_not_fail_primary_refresh(self):
        bootstrap = {
            "elements": [],
            "events": [{"id": 1, "finished": True, "deadline_time": "2026-08-22T10:00:00Z"}],
            "teams": [{"id": team_id} for team_id in range(1, 21)],
        }

        class FakeCache:
            def __init__(self):
                self.elo_insights = None
                self.saved = False

            def set_bootstrap(self, payload):
                self.bootstrap = payload

            def get_elo_insights(self):
                return self.elo_insights

            def set_elo_insights(self, payload):
                self.elo_insights = payload

            def save(self):
                self.saved = True

        class FakeFPLClient:
            def get_bootstrap(self):
                return bootstrap

        class UnavailableFPLCoreClient:
            def get_dataset(self, _bootstrap):
                raise URLError("secondary source unavailable")

        app = App.__new__(App)
        app.cache = FakeCache()
        app.client = FakeFPLClient()
        app.elo_client = UnavailableFPLCoreClient()
        app._refresh_team_elo_snapshot = lambda _bootstrap: {
            "effective_date": "2026-09-02",
            "captured_at": "2026-09-04T12:00:00+00:00",
            "source_url": "https://clubelo.com/Ranking",
            "method": "clubelo_ranking",
            "ratings": {str(team_id): 1700 + team_id for team_id in range(1, 21)},
            "used_fallback": False,
        }

        self.assertIsNone(app._refresh_data())
        self.assertTrue(app.cache.saved)
        self.assertEqual(len(app.cache.elo_insights["team_elo_ratings"]), 20)
        self.assertEqual(app.cache.elo_insights["history_by_player"], {})
        self.assertIn("unavailable", app.cache.elo_insights["fpl_core_warning"])


class FplProxyPathTests(unittest.TestCase):
    def test_allows_only_lineup_api_paths(self):
        self.assertEqual(validated_fpl_proxy_path("bootstrap-static"), "bootstrap-static")
        self.assertEqual(validated_fpl_proxy_path("entry/123"), "entry/123")
        self.assertEqual(validated_fpl_proxy_path("entry/123/history"), "entry/123/history")
        self.assertEqual(
            validated_fpl_proxy_path("entry/123/event/4/picks"),
            "entry/123/event/4/picks",
        )

    def test_rejects_arbitrary_upstream_paths(self):
        with self.assertRaises(ValueError):
            validated_fpl_proxy_path("fixtures")


class PredictorMinutesPointsTests(unittest.TestCase):
    def setUp(self):
        self.predictor = Predictor.__new__(Predictor)
        self.predictor.teams = {1: {"short_name": "FUL"}}
        self.predictor.positions = {4: "FWD"}
        self.predictor.team_position_xg_per90 = {"FUL:FWD": 0.25, "*:FWD": 0.2}
        self.predictor.team_position_xa_per90 = {"FUL:FWD": 0.12, "*:FWD": 0.1}
        self.predictor.position_bonus_per90 = {"FWD": 0.3}
        self.predictor.team_elo_ratings = {}

    def test_full_points_at_threshold(self):
        self.assertEqual(self.predictor._predict_minutes_points(80), 2.0)

    def test_full_points_above_threshold(self):
        self.assertEqual(self.predictor._predict_minutes_points(90), 2.0)

    def test_points_are_proportional_below_threshold(self):
        self.assertAlmostEqual(
            self.predictor._predict_minutes_points(61),
            2 * 61 / 90,
        )

    def test_expected_minutes_include_non_appearances(self):
        recent_matches = [
            {"minutes": 61, "starts": 1},
            {"minutes": 12, "starts": 0},
            {"minutes": 0, "starts": 0},
            {"minutes": 0, "starts": 0},
            {"minutes": 0, "starts": 0},
            {"minutes": 0, "starts": 0},
        ]

        result = self.predictor._predict_minutes({}, recent_matches)

        self.assertEqual(result["predicted_minutes"], 12.17)
        self.assertAlmostEqual(
            result["minutes_points_per_fixture"],
            2 * 12.17 / 90,
        )
        self.assertAlmostEqual(result["probability_reaches_60"], 1 / 6)

    def test_clean_sheet_context_uses_elo_poisson_and_60_minute_eligibility(self):
        self.predictor.teams = {
            1: {"short_name": "AAA"},
            2: {"short_name": "BBB"},
        }
        self.predictor.team_elo_ratings = {"1": 2064, "2": 1533}

        result = self.predictor._predict_clean_sheet_context(
            {"team": 1},
            [{"event": 3, "is_home": True, "team_h": 1, "team_a": 2}],
            0.5,
        )

        expected = elo_fixture_values(2064, 1533)
        self.assertAlmostEqual(
            result["probability_per_fixture"],
            round(expected["home_clean_sheet_probability"] * 0.5, 3),
        )
        self.assertEqual(result["fixtures"][0]["opponent"], "BBB")
        self.assertEqual(result["fixtures"][0]["method"], "elo")
        self.assertEqual(result["fixtures"][0]["probability_reaches_60"], 0.5)

    def test_short_xg_history_is_blended_with_missing_fixture_prior(self):
        result = self.predictor._predict_goals(
            {"team": 1, "element_type": 4},
            [{"minutes": 90, "expected_goals": 0.73, "goals_scored": 1}],
            [],
            90,
        )

        self.assertAlmostEqual(result["xg_per_90"], (0.73 + 0.25 * 5) / 6)
        self.assertEqual(result["prior_equivalent_minutes"], 450)
        self.assertAlmostEqual(result["observed_weight"], 1 / 6)
        self.assertLess(result["finishing_adjustment"], result["raw_finishing_adjustment"])

    def test_xg_rate_is_scaled_by_expected_minutes(self):
        matches = [
            {"minutes": 90, "expected_goals": 0.4, "goals_scored": 0}
            for _ in range(6)
        ]

        result = self.predictor._predict_goals(
            {"team": 1, "element_type": 4},
            matches,
            [],
            45,
        )

        self.assertAlmostEqual(result["xg_per_90"], 0.4)
        self.assertAlmostEqual(result["baseline_per_fixture"], 0.2)

    def test_short_xa_history_uses_per90_prior_and_expected_minutes(self):
        result = self.predictor._predict_assists(
            {"team": 1, "element_type": 4},
            [{"minutes": 90, "expected_assists": 0.01, "assists": 0}],
            [],
            45,
        )

        expected_xa_per90 = (0.01 + 0.12 * 5) / 6
        self.assertAlmostEqual(result["xa_per_90"], expected_xa_per90)
        self.assertAlmostEqual(result["baseline_per_fixture"], expected_xa_per90 * 0.5)
        self.assertEqual(result["prior_equivalent_minutes"], 450)

    def test_complete_six_fixture_sample_does_not_use_prior(self):
        matches = [
            {"minutes": 90, "expected_goals": 0.1, "goals_scored": 0}
            for _ in range(6)
        ]

        result = self.predictor._predict_goals(
            {"team": 1, "element_type": 4},
            matches,
            [],
            90,
        )

        self.assertAlmostEqual(result["xg_per_90"], 0.1)
        self.assertEqual(result["prior_equivalent_minutes"], 0)
        self.assertFalse(result["used_team_position_prior"])

    def test_missing_prior_artifact_does_not_shrink_observed_rate_to_zero(self):
        self.predictor.team_position_xg_per90 = {}

        result = self.predictor._predict_goals(
            {"team": 1, "element_type": 4},
            [{"minutes": 90, "expected_goals": 0.73, "goals_scored": 0}],
            [],
            90,
        )

        self.assertAlmostEqual(result["xg_per_90"], 0.73)
        self.assertEqual(result["prior_equivalent_minutes"], 0)

    def test_arsenal_hull_elo_fixture_sample(self):
        result = elo_fixture_values(2064, 1533)

        self.assertEqual(result["effective_home_elo"], 2164)
        self.assertEqual(result["delta_elo"], 631)
        self.assertAlmostEqual(result["home_factor"], 1.5050158127)
        self.assertAlmostEqual(result["away_factor"], 0.4949841873)
        self.assertAlmostEqual(result["home_xg"], 2.1070221378)
        self.assertAlmostEqual(result["away_xg"], 0.6929778622)
        self.assertAlmostEqual(result["home_clean_sheet_probability"], 0.5000847, places=6)
        self.assertAlmostEqual(result["away_clean_sheet_probability"], 0.1215997, places=6)
        self.assertAlmostEqual(result["home_xg"] + result["away_xg"], 2.8)

    def test_historical_strength_fallback_ignores_official_difficulty(self):
        self.predictor.teams = {
            1: {"short_name": "AAA"},
            2: {"short_name": "BBB"},
        }
        self.predictor.team_strengths = {
            "1": {"strength_attack_home": 105},
            "2": {"strength_defence_away": 100},
        }

        result = self.predictor._fixture_attack_factor(
            1,
            [{"is_home": True, "team_h": 1, "team_a": 2, "difficulty": 5}],
        )

        self.assertEqual(result, 1.1)

    def test_defensive_contribution_uses_position_threshold_frequency(self):
        matches = [
            {"minutes": 90, "defensive_contribution": contribution}
            for contribution in (12, 11, 15, 4, 12, 0)
        ]

        result = self.predictor._predict_defensive_contribution_context(
            {"element_type": 3},
            matches,
            45,
        )

        self.assertEqual(result["threshold"], 12)
        self.assertEqual(result["qualifying_fixtures"], 3)
        self.assertEqual(result["points_per_fixture"], 1.0)

    def test_goalkeepers_receive_no_defensive_contribution_points(self):
        result = self.predictor._predict_defensive_contribution_context(
            {"element_type": 1},
            [{"minutes": 90, "defensive_contribution": 20}],
            90,
        )

        self.assertEqual(result["method"], "ineligible_position")
        self.assertEqual(result["points_per_fixture"], 0.0)

    def test_actual_defensive_contribution_eligibility_and_single_threshold(self):
        self.assertEqual(actual_defensive_contribution_points(1, 20), 0)
        self.assertEqual(actual_defensive_contribution_points(2, 10), 2)
        self.assertEqual(actual_defensive_contribution_points(3, 12), 2)
        self.assertEqual(actual_defensive_contribution_points(4, 24), 2)

    def test_goalkeeper_proxy_uses_save_points_and_fixture_xga(self):
        self.predictor.teams = {
            1: {"short_name": "AAA"},
            2: {"short_name": "BBB"},
        }
        self.predictor.team_elo_ratings = {"1": 1800, "2": 1800}
        matches = [
            {"minutes": 90, "saves": saves}
            for saves in (2, 3, 5, 6, 0, 4)
        ]
        fixture = {"event": 3, "is_home": True, "team_h": 1, "team_a": 2}

        result = self.predictor._predict_goalkeeper_context(
            {"team": 1, "element_type": 1},
            matches,
            [fixture],
            90,
        )

        self.assertEqual(result["save_points_sample_total"], 5)
        self.assertAlmostEqual(result["save_points_per_fixture"], 5 / 6, places=3)
        expected_xga = elo_fixture_values(1800, 1800)["away_xg"]
        expected_deduction = expected_xga / 2 - (1 - math.exp(-2 * expected_xga)) / 4
        self.assertAlmostEqual(result["goals_conceded_deductions"][0], expected_deduction, places=3)

    def test_early_bonus_fills_only_missing_slots_with_position_average(self):
        current = [{"minutes": 90, "bonus": 1}]

        result = self.predictor._predict_bonus_context(
            {"element_type": 4},
            current,
            [],
            current,
            90,
            3,
        )

        self.assertEqual(result["missing_sample_fixtures"], 5)
        self.assertEqual(result["position_fill_minutes"], 450)
        self.assertAlmostEqual(result["bonus_per_90"], (1 + 0.3 * 5) / 6, places=3)
        self.assertAlmostEqual(result["points_per_fixture"], 0.417, places=3)

    def test_post_gw6_bonus_blends_season_and_recent_six_player_rates(self):
        current = [
            {"minutes": 90, "bonus": 0}
            for _ in range(4)
        ] + [
            {"minutes": 90, "bonus": 2}
            for _ in range(6)
        ]

        result = self.predictor._predict_bonus_context(
            {"element_type": 4},
            current,
            [],
            current[-6:],
            80,
            20,
        )

        self.assertEqual(result["season_bonus_per_90"], 1.2)
        self.assertEqual(result["recent_six_bonus_per_90"], 2.0)
        self.assertEqual(result["bonus_per_90"], 1.4)
        self.assertAlmostEqual(result["points_per_fixture"], 1.244, places=3)


if __name__ == "__main__":
    unittest.main()
