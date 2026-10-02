import unittest
from collections import deque
from unittest.mock import patch

from maze import Maze


class PuzzleTests(unittest.TestCase):
    def setUp(self):
        self.world = Maze()

    def carry(self, *items):
        for item in items:
            self.world.item_locations[item] = "inventory"

    def act(self, action, target, item=""):
        result = self.world.interact(action, target, item)
        self.assertTrue(result["success"], result.get("error"))
        return result

    def test_house_and_reset(self):
        w = self.world
        w.move("north"); w.move("east")
        self.assertFalse(w.move("in")["success"])
        self.act("open", "window")
        self.assertTrue(w.move("in")["success"])
        w.move("west")
        self.assertFalse(w.move("down")["success"])
        self.assertFalse(w.interact("open", "trapdoor")["success"])
        self.act("move", "rug"); self.act("open", "trapdoor")
        self.assertTrue(w.move("down")["success"])
        w.reset()
        self.assertFalse(w.flags)
        self.assertEqual(w.turns, 0)

    def test_lift_rug_and_trapdoor_name_variants(self):
        for target in ("trapdoor", "trap door", "TRAP-DOOR", "the trap door"):
            with self.subTest(target=target):
                w = Maze(); w.position = "LIVING-ROOM"
                self.assertIn("lift", w.look()["allowed_actions"])
                self.assertFalse(w.interact("open", target)["success"])
                self.assertTrue(w.interact("lift", "the rug")["success"])
                self.assertTrue(w.interact("open", target)["success"])
                self.assertIn("down", w.look()["exits"])
                self.assertTrue(w.move("down")["success"])
                self.assertEqual(w.position, "CELLAR")

    def prepare(self, flags=(), taken=(), carrying=()):
        """Jump the world to a point in the game: flags set, items taken, some still carried."""
        w = self.world
        w.flags |= set(flags)
        w.discovered_items |= set(taken) | set(carrying)
        for item in carrying:
            w.item_locations[item] = "inventory"
        return w

    EARLY = ("KITCHEN-WINDOW", "TRAP-DOOR", "TROLL-FLAG")

    def test_hints_follow_progress_not_location(self):
        w = self.world; w.position = "DAM-ROOM"
        self.assertIn("kitchen window", w.hint()["hint"])  # a fresh game starts at the window, wherever you stand
        w.flags.add("KITCHEN-WINDOW")
        self.assertIn("lantern and the sword", w.hint()["hint"])
        self.prepare(carrying=("LAMP", "SWORD"))
        self.assertIn("move the rug", w.hint()["hint"])
        self.act("move", "rug") if w.position == "LIVING-ROOM" else w.flags.add("rug")
        w.flags.add("TRAP-DOOR")
        self.assertNotIn("move the rug", w.hint()["hint"])
        self.assertIn("troll", w.hint()["hint"])

    def test_hint_notices_a_dropped_wrench_and_where_it_is(self):
        w = self.prepare(flags=self.EARLY + ("shaft-supplied", "CYCLOPS-FLAG", "GRATE"), taken=("BAR", "WRENCH"),
                         carrying=("LAMP", "SWORD"))
        w.item_locations["WRENCH"] = "RESERVOIR-NORTH"
        w.position = "DAM-ROOM"
        text = w.hint()["hint"]
        self.assertIn("wrench", text)
        self.assertIn("Reservoir North", text)
        w.item_locations["WRENCH"] = "inventory"
        self.assertIn("yellow button", w.hint()["hint"])
        w.flags.add("dam-enabled")
        self.assertIn("turn the bolt", w.hint()["hint"])

    def test_hint_asks_to_deposit_carried_treasure(self):
        w = self.prepare(flags=self.EARLY, carrying=("LAMP", "SWORD", "BAR"))
        w.position = "TROLL-ROOM"
        self.assertIn("trophy case", w.hint()["hint"])

    def test_hints_are_optional_counted_free_and_reset(self):
        w = self.prepare(flags=self.EARLY + ("shaft-supplied", "CYCLOPS-FLAG", "GRATE", "LOW-TIDE", "DOME-FLAG", "RAINBOW-FLAG"),
                         taken=("BAR", "TRUNK", "POT-OF-GOLD"), carrying=("LAMP", "SWORD", "BELL", "CANDLES", "MATCH", "BOOK"))
        w.item_locations["COFFIN"] = "TROPHY-CASE"
        w.discovered_items.add("COFFIN")
        w.position = "ENTRANCE-TO-HADES"
        state = w.look()
        self.assertNotIn("puzzle_actions", state)
        self.assertNotIn("hint", state)
        self.assertNotIn("six turns", state["description"])
        self.assertIsInstance(state["allowed_actions"][0], str)
        before = w.score()
        for count in (1, 2):
            result = w.hint()
            self.assertEqual(result["hints_used"], count)
            self.assertIn("ring the bell", result["hint"])
        self.assertEqual(w.score()["turns"], before["turns"])
        self.assertEqual(w.score()["score"], before["score"])
        import web_app
        with patch("maze._maze", w):
            client = web_app.app.test_client()
            self.assertEqual(client.post("/api/hint").json["hints_used"], 3)
            self.assertEqual(client.get("/api/state").json["hints_used"], 3)
        self.assertEqual(w.reset()["hints_used"], 0)

    def test_normal_failure_does_not_explain_solution(self):
        w = self.world; w.position = "DAM-ROOM"
        result = w.interact("turn", "bolt")
        self.assertFalse(result["success"])
        self.assertNotIn("WRENCH", str(result))
        self.assertNotIn("yellow", str(result))
        w.position = "LIVING-ROOM"
        self.assertNotIn("trapdoor", w.look()["nearby_features"])
        self.act("move", "rug")
        self.assertIn("trapdoor", w.look()["nearby_features"])

    def test_treasure_room_route_does_not_end_adventure(self):
        w = self.world
        w.move("north"); w.move("east"); self.act("open", "window")
        w.move("in"); w.move("west"); self.assertTrue(w.take("SWORD")["success"])
        self.act("move", "rug"); self.act("open", "trapdoor"); w.move("down"); w.move("north")
        self.act("attack", "troll")
        queue = deque([(w.position, [])]); seen = {w.position}
        while queue:
            room, path = queue.popleft()
            if room == "CYCLOPS-ROOM": break
            for direction, target in w.rooms[room]["exits"].items():
                condition = w.rooms[room].get("conditions", {}).get(direction)
                if condition and condition not in w.flags:
                    continue
                if target not in seen and target != w.goal:
                    seen.add(target); queue.append((target, path + [direction]))
        for direction in path:
            self.assertTrue(w.move(direction)["success"])
        self.assertFalse(w.move("up")["success"])
        self.act("say", "cyclops", "Odysseus")
        result = w.move("up")
        self.assertEqual(result["room_id"], "TREASURE-ROOM")
        self.assertFalse(result["at_goal"])

    def test_gate_equipment_and_locality(self):
        w = self.world; w.position = "GRATING-CLEARING"
        self.assertFalse(w.interact("unlock", "grate")["success"])
        self.act("move", "leaves"); self.carry("KEYS")
        self.act("unlock", "grate"); self.act("open", "grate")
        self.assertTrue(w.move("down")["success"])
        self.assertFalse(w.interact("open", "window")["success"])
        w.position = "DOME-ROOM"; self.carry("ROPE"); self.act("tie", "railing")
        self.assertTrue(w.move("down")["success"])
        w.position = "ARAGAIN-FALLS"; self.carry("SCEPTRE"); self.act("wave", "rainbow")
        self.assertIn("RAINBOW-FLAG", w.flags)

    def test_dam_dig_and_machine(self):
        w = self.world; w.position = "DAM-ROOM"; self.carry("WRENCH")
        self.assertFalse(w.interact("turn", "bolt")["success"])
        w.position = "MAINTENANCE-ROOM"; self.act("push", "yellow button")
        w.position = "DAM-ROOM"; self.act("turn", "bolt")
        self.assertIn("gates-open", w.flags)
        for _ in range(7): w.wait()
        self.assertNotIn("LOW-TIDE", w.flags)     # the reservoir takes eight turns to drain
        w.wait()
        self.assertIn("LOW-TIDE", w.flags)
        self.assertNotIn("TRUNK", w.hidden_items)
        w.position = "SANDY-CAVE"; self.carry("SHOVEL")
        self.assertFalse(w.take("SCARAB")["success"])
        for _ in range(3): self.act("dig", "sand")
        self.assertTrue(w.take("SCARAB")["success"])
        w.position = "MACHINE-ROOM"; self.carry("SCREWDRIVER", "COAL")
        self.assertFalse(w.interact("turn", "switch")["success"])
        self.assertTrue(w.open_container("MACHINE")["success"])
        self.assertTrue(w.put("COAL", "MACHINE")["success"])
        self.assertTrue(w.close_container("MACHINE")["success"])
        self.act("turn", "switch")
        self.assertFalse(w.take("DIAMOND")["success"])
        w.open_container("MACHINE")
        self.assertTrue(w.take("DIAMOND")["success"])
        w.reset(); self.assertEqual(w.item_locations["DIAMOND"], "offstage")

    def test_ritual_follows_the_original_timing(self):
        w = self.world; w.position = "ENTRANCE-TO-HADES"
        self.carry("BELL", "CANDLES", "MATCH", "BOOK")
        self.assertIn("ceremony", w.interact("read", "book")["error"])
        self.act("ring", "bell")                     # the bell turns red hot and drops the candles
        self.assertEqual((w.item_locations["BELL"], w.item_locations["CANDLES"]), ("offstage", "ENTRANCE-TO-HADES"))
        for _ in range(6): w.wait()                  # too slow: the wraiths recover
        w.take("CANDLES"); self.act("light", "candles")
        self.assertFalse(w.interact("read", "book")["success"])
        for _ in range(25):                          # the bell cools after twenty turns
            if w.item_locations["BELL"] == "ENTRANCE-TO-HADES": break
            w.wait()
        self.assertTrue(w.take("BELL")["success"])
        self.act("ring", "bell"); w.take("CANDLES")
        self.assertIn("flicker", self.act("light", "candles")["message"])
        self.act("read", "book")
        self.assertTrue(w.move("south")["success"])

    def test_the_book_must_be_read_within_two_turns_of_the_flames(self):
        w = self.world; w.position = "ENTRANCE-TO-HADES"
        self.carry("BELL", "CANDLES", "MATCH", "BOOK")
        self.act("ring", "bell"); w.take("CANDLES"); self.act("light", "candles")
        w.wait(); w.wait()
        self.assertFalse(w.interact("read", "book")["success"])

    def test_egg_basket_and_http(self):
        w = self.world; self.carry("EGG"); w.position = "TREASURE-ROOM"
        self.assertFalse(w.open_container("EGG")["success"])
        self.act("give", "thief", "EGG")
        self.assertTrue(w.take("CANARY")["success"])
        w.position = "PATH"; self.act("wind", "canary")
        self.assertTrue(w.take("BAUBLE")["success"])
        self.assertFalse(w.interact("wind", "canary")["success"])
        w.position = "SHAFT-ROOM"; self.carry("COAL")
        self.assertTrue(w.put("COAL", "RAISED-BASKET")["success"])
        self.act("lower", "basket")
        self.assertTrue(w._inside("COAL", "LOWER-SHAFT"))
        import web_app
        with patch("maze._maze", w):
            client = web_app.app.test_client()
            self.assertEqual(client.post("/api/interact", json={"action": 7, "target": "basket"}).status_code, 400)
            self.assertTrue(client.post("/api/interact", json={"action": "raise", "target": "basket"}).json["success"])


if __name__ == "__main__":
    unittest.main()


class OpenFixtureTests(unittest.TestCase):
    def test_open_container_opens_scene_fixtures(self):
        from maze import Maze
        world = Maze()
        world.position = "EAST-OF-HOUSE"
        result = world.open_container("window")
        self.assertTrue(result.get("success"), result)
        self.assertIn("KITCHEN-WINDOW", world.flags)

    def test_examine_and_close_understand_fixtures(self):
        from maze import Maze
        world = Maze()
        world.position = "EAST-OF-HOUSE"
        result = world.examine("window")
        self.assertTrue(result["success"], result)
        self.assertIn("ajar", result["message"])
        closed = world.close_container("window")
        self.assertFalse(closed["success"])
        self.assertIn("interact", closed["error"])

    def test_failed_interaction_points_to_a_visited_room_where_it_works(self):
        from maze import Maze
        world = Maze()
        world.visited.add("EAST-OF-HOUSE")
        world.position = "WEST-OF-HOUSE"
        result = world.interact("open", "window")
        self.assertFalse(result["success"])
        self.assertIn("Behind House", result["error"])

    def test_hint_lists_equipment_set_down_even_when_another_step_is_next(self):
        from maze import Maze
        w = Maze()
        w.discovered_items |= {"LAMP", "SWORD", "WRENCH"}
        w.item_locations.update({"LAMP": "inventory", "SWORD": "inventory", "WRENCH": "RESERVOIR-NORTH"})
        self.assertIn("wrench in Reservoir North", w.hint()["hint"])


class TempleTests(unittest.TestCase):
    def setUp(self):
        self.w = Maze()
        self.w.position = "SOUTH-TEMPLE"

    def test_only_the_coffin_is_too_big_for_the_altar_stairs(self):
        w = self.w
        for item in ("BELL", "CANDLES", "BOOK", "TORCH", "SWORD"):
            w.item_locations[item] = "inventory"
        self.assertIn("down", w.exits())
        self.assertTrue(w.move("down")["success"])
        self.assertEqual(w.position, "TINY-CAVE")

    def test_praying_at_the_altar_carries_the_coffin_out(self):
        w = self.w
        w.item_locations["COFFIN"] = "inventory"
        self.assertNotIn("down", w.exits())
        self.assertIn("coffin", w.move("down")["error"])
        result = w.interact("pray", "altar")
        self.assertTrue(result["success"], result)
        self.assertEqual((w.position, w.item_locations["COFFIN"]), ("FOREST-1", "inventory"))
        self.assertIn("pray", w.look()["allowed_actions"])

    def test_hint_explains_how_to_get_the_coffin_out(self):
        w = self.w
        w.flags |= {"KITCHEN-WINDOW", "TRAP-DOOR", "TROLL-FLAG", "shaft-supplied", "CYCLOPS-FLAG", "GRATE", "LOW-TIDE", "DOME-FLAG"}
        w.discovered_items |= {"LAMP", "SWORD", "BAR", "TRUNK", "COFFIN"}
        w.item_locations.update({"LAMP": "inventory", "SWORD": "inventory", "COFFIN": "inventory"})
        self.assertIn("Pray at the altar", w.hint()["hint"])


class OriginalRulesTests(unittest.TestCase):
    """Rules taken from the original Zork I source (1actions.zil)."""

    def setUp(self):
        self.w = Maze()

    def carry(self, *items):
        for item in items:
            self.w.item_locations[item] = "inventory"

    def test_trap_door_slams_and_is_barred_behind_you_once(self):
        w = self.w; w.position = "LIVING-ROOM"
        self.assertTrue(w.interact("move", "rug")["success"])
        self.assertTrue(w.interact("open", "trapdoor")["success"])
        result = w.move("down")
        self.assertIn("crashes shut", " ".join(result["encounter_events"]))
        self.assertNotIn("TRAP-DOOR", w.flags)
        self.assertEqual(w.move("up")["error"], "The door is locked from above.")
        self.assertEqual(w.interact("open", "trapdoor")["error"], "The door is locked from above.")
        w.position = "LIVING-ROOM"                   # (home by the cyclops passage in the real game)
        self.assertTrue(w.interact("open", "trapdoor")["success"])
        again = w.move("down")
        self.assertNotIn("crashes shut", " ".join(again["encounter_events"]))
        self.assertTrue(w.move("up")["success"])

    def test_chimney_needs_the_lamp_and_at_most_two_things(self):
        w = self.w; w.position = "STUDIO"
        self.assertIn("empty-handed", w.move("up")["error"])
        self.carry("LAMP", "SWORD", "ROPE")
        self.assertIn("what you're carrying", w.move("up")["error"])
        w.item_locations["ROPE"] = w.item_locations["SWORD"] = "offstage"
        w.item_locations["LAMP"] = "offstage"; self.carry("SWORD", "ROPE")
        self.assertIn("what you're carrying", w.move("up")["error"])     # two things, but no lamp
        self.carry("LAMP"); w.item_locations["ROPE"] = "offstage"
        w.flags.add("trapdoor-slammed")
        self.assertTrue(w.move("up")["success"])
        self.assertEqual(w.position, "KITCHEN")
        self.assertNotIn("trapdoor-slammed", w.flags)  # the next trip down slams it again

    def test_the_thief_defends_his_lair_and_the_chalice_needs_him_dead(self):
        w = self.w; w.position = "CYCLOPS-ROOM"
        w.flags |= {"MAGIC-FLAG", "CYCLOPS-FLAG"}
        w.thief_position = "ROUND-ROOM"
        result = w.move("up")
        self.assertIn("scream of anguish", " ".join(result["encounter_events"]))
        self.assertEqual(w.thief_position, "TREASURE-ROOM")
        self.assertIn("stabbed in the back", w.take("CHALICE")["error"])
        for _ in range(12): w.wait()                 # he does not leave while you are there
        self.assertEqual(w.thief_position, "TREASURE-ROOM")
        self.carry("SWORD")
        self.assertTrue(w.interact("attack", "thief")["success"])
        self.assertTrue(w.take("CHALICE")["success"])

    def test_no_scream_once_the_thief_is_dead(self):
        w = self.w; w.position = "CYCLOPS-ROOM"
        w.flags |= {"MAGIC-FLAG", "CYCLOPS-FLAG", "thief-defeated"}; w.thief_position = None
        self.assertNotIn("scream", " ".join(w.move("up")["encounter_events"]))

    def test_closing_the_gates_refills_the_reservoir_and_drowns_anyone_in_it(self):
        w = self.w
        w.flags |= {"dam-enabled", "gates-open", "LOW-TIDE"}; w.hidden_items.discard("TRUNK")
        w.position = "DAM-ROOM"; self.carry("WRENCH")
        self.assertIn("close", w.interact("turn", "bolt")["message"])
        w.position = "RESERVOIR"
        for _ in range(8): w.wait()
        self.assertTrue(w.dead)
        self.assertNotIn("LOW-TIDE", w.flags)

    def test_refilling_hides_the_trunk_again_and_old_saves_still_load(self):
        w = self.w
        w.flags |= {"dam-enabled", "gates-open", "LOW-TIDE"}; w.hidden_items.discard("TRUNK")
        w.position = "DAM-ROOM"; self.carry("WRENCH")
        w.interact("turn", "bolt")
        for _ in range(8): w.wait()
        self.assertIn("TRUNK", w.hidden_items)
        import saved_games
        record = saved_games.world_snapshot(w)
        for key in saved_games.NEWER_FIELDS: del record["fields"][key]    # a save from before these fields existed
        restored = saved_games.restore_world(record)
        self.assertEqual((restored.dam_event, restored.ritual_turn), (None, None))
