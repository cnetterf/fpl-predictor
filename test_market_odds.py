import json
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

import market_odds


class FakeResponse:
    def __init__(self, payload):
        self.payload = payload

    def read(self):
        return json.dumps(self.payload).encode("utf-8")

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False


def sample_event(home_price=1.8):
    bookmaker = lambda key: {
        "key": key,
        "last_update": "2026-09-15T12:00:00Z",
        "markets": [
            {"key": "h2h", "outcomes": [
                {"name": "Arsenal", "price": home_price},
                {"name": "Draw", "price": 3.8},
                {"name": "Aston Villa", "price": 4.6},
            ]},
            {"key": "totals", "outcomes": [
                {"name": "Over", "price": 1.95, "point": 2.5},
                {"name": "Under", "price": 1.95, "point": 2.5},
            ]},
        ],
    }
    return {"home_team": "Arsenal", "away_team": "Aston Villa", "bookmakers": [
        bookmaker("pinnacle"), bookmaker("betfair_ex_uk"), bookmaker("williamhill"),
    ]}


class MarketOddsTests(unittest.TestCase):
    def setUp(self):
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.output_path = Path(self.temporary_directory.name) / "market_odds.json"
        self.bootstrap = {
            "events": [{"id": 5, "deadline_time": "2026-09-18T17:30:00Z"}],
            "teams": [],
        }
        self.fixtures = {"5": [{
            "fixture_id": 99,
            "home_team": "ARS",
            "away_team": "AVL",
            "kickoff_time": "2026-09-19T14:00:00Z",
        }]}

    def tearDown(self):
        self.temporary_directory.cleanup()

    def opener(self, _request, timeout):
        self.assertEqual(timeout, 30)
        return FakeResponse([sample_event()])

    def test_capture_builds_a_de_vigged_three_bookmaker_consensus(self):
        payload = market_odds.refresh_current_market_odds(
            self.bootstrap,
            self.fixtures,
            api_key="test-key",
            captured_at=datetime(2026, 9, 15, tzinfo=timezone.utc),
            opener=self.opener,
            output_path=self.output_path,
        )

        fixture = payload["gameweeks"]["5"]["fixtures"]["99"]
        self.assertEqual(payload["fetch_status"], "available")
        self.assertEqual(fixture["status"], "available")
        self.assertEqual(fixture["bookmaker_count"], 3)
        self.assertEqual(fixture["total_line"], 2.5)
        self.assertGreater(fixture["home_xg"], fixture["away_xg"])
        self.assertAlmostEqual(fixture["home_win_probability"] + fixture["draw_probability"] + fixture["away_win_probability"], 1, places=5)

    def test_post_deadline_refresh_keeps_the_final_pre_deadline_capture(self):
        first = market_odds.refresh_current_market_odds(
            self.bootstrap, self.fixtures, api_key="test-key",
            captured_at=datetime(2026, 9, 15, tzinfo=timezone.utc), opener=self.opener,
            output_path=self.output_path,
        )
        original = first["gameweeks"]["5"]["fixtures"]["99"]["captured_at"]

        def later_opener(_request, timeout):
            return FakeResponse([sample_event(home_price=1.2)])

        second = market_odds.refresh_current_market_odds(
            self.bootstrap, self.fixtures, api_key="test-key",
            captured_at=datetime(2026, 9, 19, tzinfo=timezone.utc), opener=later_opener,
            output_path=self.output_path,
        )
        fixture = second["gameweeks"]["5"]["fixtures"]["99"]
        self.assertEqual(fixture["captured_at"], original)

    def test_missing_key_publishes_a_safe_unconfigured_status(self):
        payload = market_odds.refresh_current_market_odds(
            self.bootstrap, self.fixtures, api_key="",
            captured_at=datetime(2026, 9, 15, tzinfo=timezone.utc), output_path=self.output_path,
        )
        self.assertEqual(payload["fetch_status"], "not_configured")
        self.assertFalse(payload["gameweeks"])


if __name__ == "__main__":
    unittest.main()
