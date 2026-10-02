"""Check the imported room graph, navigation, and browser/MCP observations."""
from collections import deque
import unittest

from maze import Maze


class ZorkMazeTests(unittest.TestCase):
    def test_graph_is_complete_and_every_room_is_reachable(self):
        world = Maze(puzzles=False)
        self.assertEqual(len(world.rooms), 110)
        seen = {world.start}
        pending = deque([world.start])
        while pending:
            room_id = pending.popleft()
            room = world.rooms[room_id]
            self.assertTrue(room["description"])
            self.assertTrue(room["name"])
            for target in room["exits"].values():
                self.assertIn(target, world.rooms)
                if target not in seen:
                    seen.add(target)
                    pending.append(target)
        self.assertEqual(seen, set(world.rooms))

    def test_house_and_procedural_exit_match_source(self):
        world = Maze(puzzles=False)
        for direction, expected in [
            ("north", "NORTH-OF-HOUSE"),
            ("east", "EAST-OF-HOUSE"),
            ("in", "KITCHEN"),
            ("west", "LIVING-ROOM"),
            ("down", "CELLAR"),
        ]:
            result = world.move(direction)
            self.assertTrue(result["success"])
            self.assertEqual(result["room_id"], expected)
            self.assertTrue(result["description"])
        self.assertEqual(world.moves_made, 5)

    def test_blocked_move_and_reset(self):
        world = Maze(puzzles=False)
        result = world.move("east")
        self.assertFalse(result["success"])
        self.assertEqual(world.moves_made, 0)
        world.move("n")
        result = world.reset()
        self.assertEqual(result["room_id"], "WEST-OF-HOUSE")
        self.assertEqual(world.visited, {world.start})
        self.assertEqual(world.moves_made, 0)

    def test_goal_is_reached_through_real_exits(self):
        world = Maze(puzzles=False)
        pending = deque([(world.start, [])])
        seen = {world.start}
        route = None
        while pending:
            room, path = pending.popleft()
            if room == world.goal:
                route = path
                break
            for direction, target in world.rooms[room]["exits"].items():
                if target not in seen:
                    seen.add(target)
                    pending.append((target, path + [direction]))
        self.assertIsNotNone(route)
        for direction in route:
            result = world.move(direction)
            self.assertTrue(result["success"])
        self.assertTrue(result["at_goal"])
        self.assertEqual(result["room_name"], "Treasure Room")

    def test_map_is_browser_only_and_keeps_directed_exits(self):
        world = Maze(puzzles=False)
        self.assertNotIn("rooms", world.look())
        self.assertNotIn("grid", world.look())
        view = world.grid_view()
        self.assertEqual(view["kind"], "rooms")
        self.assertEqual(len(view["rooms"]), 110)
        self.assertEqual(world.rooms["MAZE-2"]["exits"]["down"], "MAZE-4")
        self.assertNotEqual(world.rooms["MAZE-4"]["exits"].get("up"), "MAZE-2")
        self.assertEqual(world.rooms["STUDIO"]["exits"]["up"], "KITCHEN")
        self.assertNotIn("down", world.rooms["KITCHEN"]["exits"])


if __name__ == "__main__":
    unittest.main()
