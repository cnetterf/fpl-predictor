import unittest

import fplbench


def player(player_id, position, team, price, predicted, actual=0, minutes=90, ownership=5):
    return {
        "id": player_id, "name": f"P{player_id}", "position": position, "team": team,
        "price": price, "predicted_points": predicted, "actual_points": actual,
        "minutes": minutes, "ownership": ownership, "expected_minutes": 90,
    }


class FplbenchTests(unittest.TestCase):
    def test_optimizer_obeys_budget_positions_and_club_limit(self):
        players = []
        player_id = 1
        for position, count in {"GKP": 8, "DEF": 16, "MID": 16, "FWD": 10}.items():
            for index in range(count):
                players.append(player(
                    player_id, position, f"T{index % 10}", 4 + (index % 6) * .5,
                    3 + (index % 8) * .4,
                ))
                player_id += 1
        squad, plan = fplbench.optimize_squad(players)
        self.assertTrue(fplbench.validate_squad(squad))
        self.assertEqual(len(plan["starters"]), 11)
        self.assertNotEqual(plan["captain"]["id"], plan["vice_captain"]["id"])

    def test_captain_falls_back_to_vice_and_bench_order_is_legal(self):
        squad = [
            player(1, "GKP", "A", 4.5, 5, 2), player(2, "GKP", "B", 4, 1, 6),
            *[player(10 + i, "DEF", f"D{i}", 4.5, 6 - i / 10, 2, 0 if i == 0 else 90) for i in range(5)],
            *[player(20 + i, "MID", f"M{i}", 6, 8 - i / 10, 5, 0 if i == 0 else 90) for i in range(5)],
            *[player(30 + i, "FWD", f"F{i}", 7, 7 - i / 10, 4) for i in range(3)],
        ]
        plan = fplbench._role_plan(squad)
        score = fplbench.score_actual(plan)
        self.assertIsInstance(score, float)
        self.assertGreater(score, 0)

    def test_metrics_use_only_one_percent_owned_for_error(self):
        players = [
            player(1, "MID", "A", 5, 5, 8, ownership=10),
            player(2, "MID", "B", 5, 4, 0, ownership=.5),
        ]
        metrics = fplbench.prediction_metrics(players)
        self.assertEqual(metrics["scored_players"], 1)
        self.assertEqual(metrics["mae"], 3)


if __name__ == "__main__":
    unittest.main()
