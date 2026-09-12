import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import generate_static_data


class FakeCache:
    data = {"element_summaries": {}}

    def get_bootstrap(self):
        return {
            "events": [
                {"id": 1, "deadline_time": "2026-08-15T10:00:00Z", "finished": False},
                {"id": 2, "deadline_time": "2027-05-23T10:00:00Z", "finished": False},
            ],
            "elements": [{"id": 1, "selected_by_percent": "42.1"}],
        }


class FakeApp:
    cache = FakeCache()

    def get_backtest_window(self, start_gameweek, end_gameweek):
        return {"start_gw": start_gameweek, "end_gw": end_gameweek}


class StaticBacktestGenerationTests(unittest.TestCase):
    def setUp(self):
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary_directory.name)
        self.data_dir = self.root / "data"
        self.seasons_dir = self.data_dir / "backtests"
        self.manifest_path = self.data_dir / "backtest_seasons.json"
        self.output_path = self.data_dir / "static_backtest.json"
        self.snapshots_path = self.data_dir / "prediction_snapshots.json"
        self.snapshots_dir = self.data_dir / "prediction_snapshots"
        self.snapshot_results_dir = self.data_dir / "prediction_snapshot_results"
        self.data_dir.mkdir()

        self.path_patches = [
            patch.object(generate_static_data, "BACKTEST_SEASONS_DIR", self.seasons_dir),
            patch.object(generate_static_data, "BACKTEST_SEASONS_PATH", self.manifest_path),
            patch.object(generate_static_data, "BACKTEST_OUTPUT_PATH", self.output_path),
            patch.object(generate_static_data, "OUTPUT_PATH", self.data_dir / "static_predictions.json"),
            patch.object(generate_static_data, "PREDICTION_SNAPSHOTS_PATH", self.snapshots_path),
            patch.object(generate_static_data, "PREDICTION_SNAPSHOTS_DIR", self.snapshots_dir),
            patch.object(generate_static_data, "PREDICTION_SNAPSHOT_RESULTS_DIR", self.snapshot_results_dir),
        ]
        for path_patch in self.path_patches:
            path_patch.start()

    def tearDown(self):
        for path_patch in self.path_patches:
            path_patch.stop()
        self.temporary_directory.cleanup()

    def write_archived_season(self):
        archived_dir = self.seasons_dir / "2025-26"
        archived_dir.mkdir(parents=True)
        archived_payload = {"available_gameweeks": [2, 3], "windows": {"2-2": {}}}
        (archived_dir / "index.json").write_text(json.dumps(archived_payload))
        manifest = {
            "schema_version": 1,
            "default_season": "2025-26",
            "seasons": [
                {
                    "key": "2025-26",
                    "data_url": "./data/backtests/2025-26/index.json",
                    "windows_base_url": "./data/backtests/2025-26/windows",
                    "archived": True,
                    "recompute_available": False,
                }
            ],
        }
        self.manifest_path.write_text(json.dumps(manifest))
        return archived_payload

    def test_empty_new_season_retains_archived_default(self):
        archived_payload = self.write_archived_season()

        generate_static_data.write_backtest_season({"available_gameweeks": [], "windows": {}})

        self.assertEqual(json.loads(self.output_path.read_text()), archived_payload)
        manifest = json.loads(self.manifest_path.read_text())
        self.assertEqual(manifest["default_season"], "2025-26")
        self.assertEqual([season["key"] for season in manifest["seasons"]], ["2025-26"])

    def test_non_empty_new_season_is_added_without_deleting_archive(self):
        self.write_archived_season()
        current_payload = {"available_gameweeks": [2, 3], "windows": {"2-2": {}, "2-3": {}, "3-3": {}}}

        with patch.object(generate_static_data.server, "APP", FakeApp()):
            generate_static_data.write_backtest_season(current_payload)

        manifest = json.loads(self.manifest_path.read_text())
        self.assertEqual(manifest["default_season"], "2026-27")
        self.assertEqual({season["key"] for season in manifest["seasons"]}, {"2025-26", "2026-27"})
        self.assertTrue((self.seasons_dir / "2025-26" / "index.json").exists())
        self.assertEqual(len(list((self.seasons_dir / "2026-27" / "windows").glob("*.json"))), 3)
        self.assertEqual(json.loads(self.output_path.read_text()), current_payload)

    def test_pre_deadline_snapshot_replaces_only_with_a_later_pre_deadline_refresh(self):
        bootstrap = FakeCache().get_bootstrap()
        sources = {
            "official": [{
                "player_id": 1,
                "player_name": "Example",
                "team": "AAA",
                "position": "MID",
                "predicted_total_points": 4.321,
                "inputs": {"predicted_minutes_per_fixture": 75.555},
            }],
        }
        with patch.object(generate_static_data.server, "APP", FakeApp()):
            generate_static_data.write_prediction_snapshot(
                "2026-27", 2, "2027-05-23T10:00:00+00:00", "2027-05-22T12:00:00+00:00", sources, bootstrap,
            )
            generate_static_data.write_prediction_snapshot(
                "2026-27", 2, "2027-05-23T10:00:00+00:00", "2027-05-23T11:00:00+00:00", sources, bootstrap,
            )

        manifest = json.loads(self.snapshots_path.read_text())
        entry = manifest["seasons"]["2026-27"]["gameweeks"]["2"]
        self.assertEqual(entry["captured_at"], "2027-05-22T12:00:00+00:00")
        snapshot = json.loads(__import__("gzip").decompress((self.snapshots_dir / "2026-27" / "gw-2.json.gz").read_bytes()))
        self.assertEqual(snapshot["sources"]["official"]["players"][0], [1, "Example", "AAA", "MID", 4.321, 75.56, 42.1])

    def test_finished_gameweek_writes_results_without_mutating_snapshot(self):
        bootstrap = FakeCache().get_bootstrap()
        bootstrap["events"][1]["finished"] = True
        sources = {"official": [{
            "player_id": 1,
            "player_name": "Example",
            "team": "AAA",
            "position": "MID",
            "predicted_total_points": 4,
            "inputs": {"predicted_minutes_per_fixture": 70},
        }]}
        app = FakeApp()
        app.cache = FakeCache()
        app.cache.data = {"element_summaries": {"1": {"history": [{"round": 2, "total_points": 9}]}}}
        with patch.object(generate_static_data.server, "APP", app), patch.object(
            generate_static_data.server, "bootstrap_season_slug", return_value="2026-2027",
        ):
            generate_static_data.write_prediction_snapshot(
                "2026-27", 2, "2027-05-23T10:00:00+00:00", "2027-05-22T12:00:00+00:00", sources, bootstrap,
            )
            generate_static_data.reconcile_prediction_snapshots(bootstrap)

        manifest = json.loads(self.snapshots_path.read_text())
        entry = manifest["seasons"]["2026-27"]["gameweeks"]["2"]
        self.assertEqual(entry["status"], "complete")
        self.assertIn("results_url", entry)
        result = json.loads(__import__("gzip").decompress((self.snapshot_results_dir / "2026-27" / "gw-2.json.gz").read_bytes()))
        self.assertEqual(result["sources"]["official"]["actual_points"], [[1, 9]])

    def test_recovered_benchmarks_use_the_gw3_ownership_proxy_and_keep_provenance(self):
        (self.data_dir / "static_predictions.json").write_text(json.dumps({"schema_version": 2}))
        player = {
            "player_id": 1,
            "player_name": "Example",
            "team": "AAA",
            "position": "MID",
            "predicted_total_points": 4.5,
            "inputs": {"predicted_minutes_per_fixture": 75},
        }

        def historic_file(commit, path):
            if path == "data/static_predictions.json":
                return json.dumps({"source_last_fetch_at": "2026-08-28T17:00:00Z"}).encode()
            return __import__("gzip").compress(json.dumps({"players": [player]}).encode())

        app = FakeApp()
        app.cache = FakeCache()
        app.cache.data = {"element_summaries": {"1": {"history": [
            {"round": 2, "total_points": 6}, {"round": 3, "total_points": 8},
        ]}}}
        with patch.object(generate_static_data.server, "APP", app), patch.object(
            generate_static_data.server, "bootstrap_season_slug", return_value="2026-2027",
        ), patch.object(generate_static_data, "git_file_at_commit", side_effect=historic_file):
            generate_static_data.import_recovered_prediction_benchmarks({"1": 31.2})

        manifest = json.loads(self.snapshots_path.read_text())
        entry = manifest["seasons"]["2026-27"]["gameweeks"]["2"]
        self.assertEqual(entry["status"], "complete")
        self.assertEqual(entry["ownership_basis"]["type"], "gw3_proxy")
        snapshot = json.loads(__import__("gzip").decompress((self.snapshots_dir / "2026-27" / "gw-2.json.gz").read_bytes()))
        self.assertEqual(snapshot["sources"]["official"]["players"][0][-1], 31.2)
        result = json.loads(__import__("gzip").decompress((self.snapshot_results_dir / "2026-27" / "gw-3.json.gz").read_bytes()))
        self.assertEqual(result["sources"]["official"]["actual_points"], [[1, 8]])


if __name__ == "__main__":
    unittest.main()
