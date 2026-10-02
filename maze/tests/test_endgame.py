"""Exercise the scoring threshold through real put/move/examine commands."""
import unittest
from unittest.mock import patch

from maze import Maze


class EndgameTests(unittest.TestCase):
    def almost_complete(self):
        world = Maze()
        # Fixture represents an expedition returning with the final treasure.
        world.visited = {room for room, data in world.rooms.items() if data["discovery_points"]}
        treasures = {item for item, data in world.items.items() if data["treasure"]}
        world.discovered_items = treasures.copy()
        for item in treasures:
            world.item_locations[item] = "TROPHY-CASE"
        world.item_locations["BAUBLE"] = "inventory"
        world.position = "LIVING-ROOM"
        world.item_open["TROPHY-CASE"] = True
        world.flags.update(("shaft-supplied", "KITCHEN-WINDOW"))
        return world

    def test_last_point_reveals_map_but_does_not_complete_adventure(self):
        world = self.almost_complete()
        self.assertEqual(world.score()["score"], 349)
        self.assertFalse(world.examine("MAP")["success"])
        result = world.put("BAUBLE", "TROPHY-CASE")
        self.assertTrue(result["success"])
        self.assertEqual(result["score"], 350)
        self.assertFalse(result["at_goal"])
        self.assertIn("ancient map", result["announcement"])
        result = world.examine("MAP")
        self.assertTrue(result["success"])
        self.assertIn("Stone Barrow", result["item"]["readable_text"])
        revision = world.revision
        world.look(); world.score(); world.look()
        self.assertEqual(world.revision, revision)
        for direction in ("east", "out", "north", "west", "southwest"):
            result = world.move(direction)
            self.assertTrue(result["success"], result.get("error"))
        self.assertEqual(result["room_id"], "STONE-BARROW")
        self.assertFalse(result["at_goal"])            # arriving at the door is not the end
        self.assertIn("in", result["exits"])
        self.assertIn("stone door", result["objective"])
        result = world.move("in")                      # walking into the barrow is
        self.assertTrue(result["success"], result.get("error"))
        self.assertTrue(result["at_goal"])
        self.assertEqual(result["objective"], "Your adventure is complete.")
        text = " ".join(result["encounter_events"])
        self.assertIn("mastered the first part of the ZORK trilogy", text)
        self.assertIn("rank of Master Adventurer", text)
        self.assertIn("complete", world.move("northeast")["error"])   # only a reset does anything now
        self.assertFalse(world.reset()["at_goal"])

    def test_final_destination_is_not_advertised_at_start(self):
        world = Maze()
        state = world.look()
        self.assertNotIn("Stone Barrow", state["objective"])
        self.assertNotIn("350", str(state["blocked_exits"]))
        self.assertNotIn("Treasure Room", state["objective"])
        self.assertFalse(any(room["status"] == "goal" for room in world.grid_view()["rooms"]))
        self.assertFalse(world.move("southwest")["success"])

    def test_unlock_is_persistent_and_reset_restores_endgame(self):
        world = self.almost_complete()
        world.put("BAUBLE", "TROPHY-CASE")
        world.take("BAUBLE")
        self.assertEqual(world.score()["score"], 349)
        self.assertIn("WON-FLAG", world.flags)
        self.assertTrue(world.examine("MAP")["success"])
        state = world.reset()
        self.assertFalse(state["at_goal"])
        self.assertIsNone(state["announcement"])
        self.assertIn("MAP", world.hidden_items)
        self.assertNotIn("WON-FLAG", world.flags)
        self.assertFalse(world.move("southwest")["success"])

    def test_rainbow_reveals_the_required_gold(self):
        world = Maze()
        world.position = "END-OF-RAINBOW"
        self.assertFalse(world.take("POT-OF-GOLD")["success"])
        world.item_locations["SCEPTRE"] = "inventory"
        self.assertTrue(world.interact("wave", "rainbow")["success"])
        self.assertTrue(world.take("POT-OF-GOLD")["success"])

    def test_http_tools_share_the_endgame_state(self):
        import web_app
        world = self.almost_complete()
        with patch("maze._maze", world):
            client = web_app.app.test_client()
            result = client.post("/api/items/put", json={"item": "BAUBLE", "container": "TROPHY-CASE"}).json
            self.assertEqual(result["score"], 350)
            self.assertFalse(result["at_goal"])
            self.assertIn("ancient map", client.get("/api/state").json["announcement"])
            result = client.post("/api/items/examine", json={"item": "MAP"}).json
            self.assertIn("Stone Barrow", result["item"]["readable_text"])
            for direction in ("east", "out", "north", "west", "southwest"):
                result = client.post("/api/move", json={"direction": direction}).json
                self.assertTrue(result["success"])
            self.assertFalse(result["at_goal"])
            result = client.post("/api/move", json={"direction": "in"}).json
            self.assertTrue(result["at_goal"])
            self.assertIsNone(client.post("/api/reset").json["announcement"])


if __name__ == "__main__":
    unittest.main()
