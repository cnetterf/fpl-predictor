import unittest

import external_predictions


class ExternalPredictionTests(unittest.TestCase):
    def test_fpl_cat_normalizes_official_ids_and_points(self):
        normalized = external_predictions.normalize_fpl_cat({
            "gameweek": 11,
            "deadline": "2026-11-21T11:00:00Z",
            "computed_at": "2026-11-19T19:30:00Z",
            "players": [{
                "id": 411, "name": "Haaland", "team": "MCI", "position": "FWD",
                "xp": 6.46, "expected_minutes": 82,
            }],
        })
        self.assertEqual(normalized["gameweek"], 11)
        self.assertEqual(normalized["players"][0]["player_id"], 411)
        self.assertEqual(normalized["players"][0]["predicted_total_points"], 6.46)

    def test_sideline_rows_sum_double_gameweek_points(self):
        normalized = external_predictions.normalize_sideline_quant({
            "gameweek": 11,
            "deadline": "2026-11-21T11:00:00Z",
            "generated_at": "2026-11-19T03:30:00Z",
            "rows": [
                {"player_id": 1, "web_name": "Example", "club": "AAA", "position": "MID", "xP": 3.1, "e_mins": 70},
                {"player_id": 1, "web_name": "Example", "club": "AAA", "position": "MID", "xP": 2.2, "e_mins": 60},
            ],
        })
        self.assertEqual(len(normalized["players"]), 1)
        self.assertAlmostEqual(normalized["players"][0]["predicted_total_points"], 5.3)
        self.assertEqual(normalized["players"][0]["inputs"]["predicted_minutes_per_fixture"], 130)

    def test_feed_validation_rejects_post_deadline_data(self):
        normalized = {
            "gameweek": 11,
            "deadline_at": "2026-11-21T11:00:00Z",
            "captured_at": "2026-11-21T11:00:00Z",
            "players": [{"player_id": player_id} for player_id in range(100)],
        }
        with self.assertRaisesRegex(ValueError, "pre-deadline"):
            external_predictions.validate_feed(
                "example", normalized, 11, "2026-11-21T11:00:00Z",
            )

    def test_one_failed_provider_does_not_block_another(self):
        payloads = {
            external_predictions.SOURCES["fpl_cat"]["url"]: {
                "gameweek": 11,
                "deadline": "2026-11-21T11:00:00Z",
                "computed_at": "2026-11-19T19:30:00Z",
                "players": [
                    {"id": player_id, "name": f"P{player_id}", "team": "AAA", "position": "MID", "xp": 2, "expected_minutes": 60}
                    for player_id in range(100)
                ],
            },
        }

        def loader(url):
            if url not in payloads:
                raise OSError("offline")
            return payloads[url]

        captured, warnings = external_predictions.fetch_external_sources(
            11, "2026-11-21T11:00:00Z", loader=loader,
        )
        self.assertIn("fpl_cat", captured)
        self.assertNotIn("sideline_quant", captured)
        self.assertEqual(len(warnings), 1)


if __name__ == "__main__":
    unittest.main()
