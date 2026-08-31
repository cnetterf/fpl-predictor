import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import generate_static_data


class FakeCache:
    def get_bootstrap(self):
        return {
            "events": [
                {"deadline_time": "2026-08-15T10:00:00Z"},
                {"deadline_time": "2027-05-23T10:00:00Z"},
            ]
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
        self.data_dir.mkdir()

        self.path_patches = [
            patch.object(generate_static_data, "BACKTEST_SEASONS_DIR", self.seasons_dir),
            patch.object(generate_static_data, "BACKTEST_SEASONS_PATH", self.manifest_path),
            patch.object(generate_static_data, "BACKTEST_OUTPUT_PATH", self.output_path),
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


if __name__ == "__main__":
    unittest.main()
