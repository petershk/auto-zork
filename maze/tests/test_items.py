"""Item state and Flask contract checks; no model requests."""
import unittest
from unittest.mock import patch

from maze import Maze
import web_app


class ItemTests(unittest.TestCase):
    def setUp(self):
        self.world = Maze(puzzles=False)

    def ids(self, key="items"):
        return {item["id"] for item in self.world.look()[key]}

    def kitchen(self):
        for direction in ("north", "east", "in"):
            self.assertTrue(self.world.move(direction)["success"])

    def test_mailbox_hides_leaflet_until_open_and_examine_reads_it(self):
        self.assertIn("MAILBOX", self.ids())
        self.assertNotIn("ADVERTISEMENT", self.ids())
        self.assertFalse(self.world.take("leaflet")["success"])
        self.assertTrue(self.world.open_container("mailbox")["success"])
        self.assertIn("ADVERTISEMENT", self.ids())
        result = self.world.examine("leaflet")
        self.assertIn("WELCOME TO ZORK", result["item"]["readable_text"])
        self.assertTrue(self.world.take("leaflet")["success"])
        self.assertIn("ADVERTISEMENT", self.ids("inventory"))
        self.assertNotIn("ADVERTISEMENT", self.ids())
        self.assertTrue(self.world.drop("ADVERTISEMENT")["success"])
        self.assertIn("ADVERTISEMENT", self.ids())

    def test_fixed_and_remote_items_cannot_be_taken(self):
        self.assertFalse(self.world.take("MAILBOX")["success"])
        self.assertFalse(self.world.take("LAMP")["success"])
        self.assertEqual(self.ids("inventory"), set())

    def test_portable_container_carries_contents_and_put_prevents_cycles(self):
        self.kitchen()
        self.assertTrue(self.world.take("SANDWICH-BAG")["success"])
        self.assertTrue(self.world.open_container("SANDWICH-BAG")["success"])
        self.assertTrue(self.world.examine("LUNCH")["success"])
        self.assertFalse(self.world.put("SANDWICH-BAG", "SANDWICH-BAG")["success"])
        self.world.move("west")
        self.assertTrue(self.world.drop("SANDWICH-BAG")["success"])
        self.assertIn("LUNCH", self.ids())
        self.assertEqual(self.world.item_locations["LUNCH"], "SANDWICH-BAG")
        self.assertTrue(self.world.take("LAMP")["success"])
        self.assertFalse(self.world.put("LAMP", "SANDWICH-BAG")["success"])
        self.assertTrue(self.world.close_container("SANDWICH-BAG")["success"])
        self.assertFalse(self.world.take("LUNCH")["success"])
        self.assertTrue(self.world.take("SANDWICH-BAG")["success"])
        self.assertTrue(self.world.open_container("SANDWICH-BAG")["success"])
        self.assertTrue(self.world.take("LUNCH")["success"])

    def test_transparent_closed_bottle_can_be_inspected_but_not_reached_inside(self):
        self.kitchen()
        self.assertIn("WATER", self.ids())
        self.assertTrue(self.world.examine("WATER")["success"])
        self.assertFalse(self.world.take("WATER")["success"])
        self.assertTrue(self.world.open_container("BOTTLE")["success"])
        self.assertTrue(self.world.take("WATER")["success"])

    def test_reset_restores_locations_and_container_states(self):
        initial = dict(self.world.item_locations)
        revision = self.world.revision
        self.world.open_container("MAILBOX")
        self.world.take("ADVERTISEMENT")
        self.world.reset()
        self.assertEqual(self.world.item_locations, initial)
        self.assertFalse(self.world.item_open["MAILBOX"])
        self.assertEqual(self.ids("inventory"), set())
        self.assertGreater(self.world.revision, revision)

    def test_flask_item_actions_share_the_world_and_validate_arguments(self):
        with patch.object(web_app.maze, "_maze", self.world):
            client = web_app.app.test_client()
            self.assertEqual(client.post("/api/items/take", json={"item": 42}).status_code, 400)
            self.assertEqual(client.post("/api/items/unknown", json={"item": "MAILBOX"}).status_code, 404)
            self.assertTrue(client.post("/api/items/open_container", json={"item": "MAILBOX"}).get_json()["success"])
            self.assertTrue(client.post("/api/items/take", json={"item": "ADVERTISEMENT"}).get_json()["success"])
            state = client.get("/api/state").get_json()
            self.assertEqual(state["inventory"][0]["id"], "ADVERTISEMENT")
            self.assertEqual(client.get("/api/inventory").get_json()["inventory"], state["inventory"])


if __name__ == "__main__":
    unittest.main()
