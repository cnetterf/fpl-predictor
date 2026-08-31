import unittest

from server import Predictor, elo_fixture_values


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

    def test_defensive_contribution_rate_is_scaled_by_expected_minutes(self):
        matches = [
            {"minutes": 90, "recoveries": 8}
            for _ in range(6)
        ]

        result = self.predictor._predict_defensive_contribution_context(
            {"element_type": 3},
            matches,
            45,
        )

        self.assertEqual(result["recoveries_per_90"], 8)
        self.assertEqual(result["expected_recoveries"], 4)
        self.assertEqual(result["points_per_fixture"], 0.5)

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
        self.assertEqual(result["bonus_per_90"], 1.6)
        self.assertAlmostEqual(result["points_per_fixture"], 1.422, places=3)


if __name__ == "__main__":
    unittest.main()
