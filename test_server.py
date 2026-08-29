import unittest

from server import Predictor


class PredictorMinutesPointsTests(unittest.TestCase):
    def setUp(self):
        self.predictor = Predictor.__new__(Predictor)

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


if __name__ == "__main__":
    unittest.main()
