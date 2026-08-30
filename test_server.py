import unittest

from server import Predictor


class PredictorMinutesPointsTests(unittest.TestCase):
    def setUp(self):
        self.predictor = Predictor.__new__(Predictor)
        self.predictor.teams = {1: {"short_name": "FUL"}}
        self.predictor.positions = {4: "FWD"}
        self.predictor.team_position_xg_per90 = {"FUL:FWD": 0.25, "*:FWD": 0.2}
        self.predictor.team_position_xa_per90 = {"FUL:FWD": 0.12, "*:FWD": 0.1}

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

    def test_clean_sheet_context_keeps_fixture_calculation_inputs(self):
        self.predictor.teams = {
            1: {"short_name": "AAA"},
            2: {"short_name": "BBB"},
        }
        self.predictor.team_strengths = {
            "1": {"strength_defence_home": 120},
            "2": {"strength_attack_away": 100},
        }

        result = self.predictor._predict_clean_sheet_context(
            {"team": 1},
            [{"event": 3, "is_home": True, "team_h": 1, "team_a": 2}],
        )

        self.assertEqual(result["probability_per_fixture"], 0.65)
        self.assertEqual(result["fixtures"][0]["opponent"], "BBB")
        self.assertEqual(result["fixtures"][0]["own_defence_strength"], 120)
        self.assertEqual(result["fixtures"][0]["opponent_attack_strength"], 100)

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


if __name__ == "__main__":
    unittest.main()
