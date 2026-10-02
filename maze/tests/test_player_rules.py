import unittest
from unittest.mock import patch

from maze import Maze
import web_app


class PlayerRulesTests(unittest.TestCase):
    def setUp(self):
        self.world = Maze(puzzles=False)

    def living_room(self):
        for direction in ("north", "east", "in", "west"):
            self.assertTrue(self.world.move(direction)["success"])

    def test_turns_include_failed_actions_and_observations_but_not_polling_or_score(self):
        self.world.look()
        self.world.score()
        self.assertEqual(self.world.turns, 0)
        self.world.move("east")  # boarded door
        self.world.take("MAILBOX")  # fixed fixture
        self.world.observe()
        self.world.inventory()
        self.world.examine("MAILBOX")
        self.world.wait()
        self.assertEqual(self.world.turns, 6)
        self.assertEqual(self.world.moves_made, 0)
        self.world.reset()
        self.assertEqual(self.world.turns, 0)

    def test_load_counts_nested_contents_and_failure_does_not_move_item(self):
        for item in ("TRUNK", "COFFIN", "LAMP", "SCREWDRIVER", "MATCH"):
            self.world.item_locations[item] = self.world.start
        self.assertTrue(self.world.take("TRUNK")["success"])
        self.assertTrue(self.world.take("COFFIN")["success"])
        self.assertTrue(self.world.take("SCREWDRIVER")["success"])
        self.assertTrue(self.world.take("MATCH")["success"])
        self.assertEqual(self.world.look()["carrying"]["weight"], 100)
        before = self.world._score()
        result = self.world.take("LAMP")
        self.assertFalse(result["success"])
        self.assertIn("100", result["error"])
        self.assertEqual(self.world.item_locations["LAMP"], self.world.start)
        self.assertEqual(self.world._score(), before)
        self.world.drop("TRUNK")
        self.assertTrue(self.world.take("LAMP")["success"])
        self.assertEqual(self.world.look()["carrying"]["weight"], 80)

    def test_container_capacity_and_inventory_contents_weight_are_preserved(self):
        self.world.open_container("MAILBOX")
        self.world.take("ADVERTISEMENT")
        for direction in ("north", "east", "in"):
            self.world.move(direction)
        self.world.take("SANDWICH-BAG")
        self.world.open_container("SANDWICH-BAG")
        weight = self.world.look()["carrying"]["weight"]
        self.assertFalse(self.world.put("ADVERTISEMENT", "SANDWICH-BAG")["success"])
        self.assertEqual(self.world.item_locations["ADVERTISEMENT"], "inventory")
        self.assertEqual(self.world.look()["carrying"]["weight"], weight)
        self.assertTrue(self.world.take("LUNCH")["success"])
        self.assertEqual(self.world.look()["carrying"]["weight"], weight)
        self.assertTrue(self.world.put("ADVERTISEMENT", "SANDWICH-BAG")["success"])
        self.assertEqual(self.world.look()["carrying"]["weight"], weight)

    def test_fumble_uses_original_loose_item_threshold(self):
        candidates = sorted((i for i in self.world.items if self.world.items[i]["takeable"] and not self.world.items[i]["container"]), key=lambda i: self.world.items[i]["size"])[:9]
        for item in candidates:
            self.world.item_locations[item] = self.world.start
        with patch.object(self.world._random, "randint", return_value=100):
            for item in candidates[:8]:
                self.assertTrue(self.world.take(item)["success"])
        with patch.object(self.world._random, "randint", return_value=1):
            result = self.world.take(candidates[8])
            self.assertFalse(result["success"])
            self.assertIn("fumble", result["error"])
        self.assertEqual(self.world.item_locations[candidates[8]], self.world.start)

    def test_discovery_and_case_points_cannot_be_farmed(self):
        self.living_room()
        self.assertEqual(self.world.score()["score"], 10)  # kitchen discovery
        self.world.item_locations["PAINTING"] = "LIVING-ROOM"
        self.assertTrue(self.world.take("PAINTING")["success"])
        discovered_score = self.world.score()["score"]
        self.assertEqual(discovered_score, 14)
        self.world.drop("PAINTING")
        self.world.take("PAINTING")
        self.assertEqual(self.world.score()["score"], discovered_score)
        self.world.open_container("TROPHY-CASE")
        self.assertTrue(self.world.put("PAINTING", "TROPHY-CASE")["success"])
        self.assertEqual(self.world.score()["score"], discovered_score + 6)
        self.world.take("PAINTING")
        self.assertEqual(self.world.score()["score"], discovered_score)
        self.world.put("PAINTING", "TROPHY-CASE")
        self.assertEqual(self.world.score()["score"], discovered_score + 6)
        self.world.move("east")
        self.world.move("west")
        self.assertEqual(self.world.score()["score"], discovered_score + 6)
        self.world.reset()
        self.assertEqual(self.world.score()["score"], 0)

    def test_browser_polling_is_free_and_explicit_mcp_observations_cost_turns(self):
        with patch.object(web_app.maze, "_maze", self.world):
            client = web_app.app.test_client()
            for _ in range(3):
                client.get("/api/state")
                client.get("/api/score")
                client.get("/api/inventory")
            self.assertEqual(self.world.turns, 0)
            client.post("/api/look")
            client.post("/api/inventory")
            client.post("/api/wait")
            self.assertEqual(self.world.turns, 3)


if __name__ == "__main__":
    unittest.main()
