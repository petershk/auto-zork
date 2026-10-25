import unittest
from unittest.mock import patch

import autoplay
import commands
import web_app
from maze import Maze


def typed(step):
    """The way a person would type one recorded walkthrough step."""
    tool, *args = step
    if tool == "move":
        return args[0]
    if tool in ("take", "drop", "examine", "open", "close"):
        return f"{tool} {args[0]}"
    if tool == "put":
        return f"put {args[0]} in {args[1]}"
    if tool == "wait":
        return "wait"
    action, target, *item = args            # interact
    item = item[0] if item else ""
    if action == "say":
        return f"say {item}"                # the room says who is listening
    if action == "give":
        return f"give {item} to {target}"
    return f"{action} {target}"


class ParseTests(unittest.TestCase):
    def setUp(self):
        self.world = Maze()

    def parse(self, text):
        return commands.parse(self.world, text)

    def test_movement_in_every_spelling(self):
        for text in ("north", "n", "go north", "walk n", "move north", "  NORTH  "):
            self.assertEqual(self.parse(text), ("move", ("north",)), text)
        self.assertEqual(self.parse("sw"), ("move", ("southwest",)))
        self.assertEqual(self.parse("in"), ("move", ("in",)))
        self.assertEqual(self.parse("enter barrow"), ("move", ("in",)))
        self.assertEqual(self.parse("climb up"), ("move", ("up",)))

    def test_free_form_item_commands_ignore_articles(self):
        self.assertEqual(self.parse("take the brass lantern"), ("take", ("brass lantern",)))
        self.assertEqual(self.parse("pick up lamp"), ("take", ("lamp",)))
        self.assertEqual(self.parse("get the egg from the nest"), ("take", ("egg",)))
        self.assertEqual(self.parse("drop sword"), ("drop", ("sword",)))
        self.assertEqual(self.parse("x map"), ("examine", ("map",)))
        self.assertEqual(self.parse("look at the map"), ("examine", ("map",)))
        self.assertEqual(self.parse("put the egg in the trophy case"), ("put", ("egg", "trophy case")))
        self.assertEqual(self.parse("open window"), ("open_container", ("window",)))
        self.assertEqual(self.parse("shut case"), ("close_container", ("case",)))

    def test_commands_that_need_no_noun(self):
        self.assertEqual(self.parse("look"), ("observe", ()))
        self.assertEqual(self.parse("look around"), ("observe", ()))
        for text, tool in (("i", "inventory"), ("inventory", "inventory"), ("score", "score"), ("z", "wait"),
                           ("hint", "hint"), ("help", "help")):
            self.assertEqual(self.parse(text), (tool, ()), text)

    def test_puzzle_verbs_pick_the_target_and_the_tool_correctly(self):
        self.assertEqual(self.parse("attack troll with sword"), ("interact", ("attack", "troll", "sword")))
        self.assertEqual(self.parse("kill the troll"), ("interact", ("attack", "troll", "")))
        self.assertEqual(self.parse("give egg to thief"), ("interact", ("give", "thief", "egg")))
        self.assertEqual(self.parse("push yellow button"), ("interact", ("push", "yellow button", "")))
        self.assertEqual(self.parse("move rug"), ("interact", ("move", "rug", "")))
        self.world.position = "DOME-ROOM"
        self.assertEqual(self.parse("tie rope to railing"), ("interact", ("tie", "railing", "rope")))
        self.world.position = "END-OF-RAINBOW"
        self.assertEqual(self.parse("wave sceptre at rainbow"), ("interact", ("wave", "rainbow", "sceptre")))
        self.world.position = "GRATING-CLEARING"
        self.assertEqual(self.parse("unlock grate with keys"), ("interact", ("unlock", "grate", "keys")))

    def test_say_and_default_targets_follow_the_room(self):
        self.world.position = "LOUD-ROOM"
        self.assertEqual(self.parse("say echo"), ("interact", ("say", "room", "echo")))
        self.world.position = "CYCLOPS-ROOM"
        self.assertEqual(self.parse("say odysseus"), ("interact", ("say", "cyclops", "odysseus")))
        self.assertEqual(self.parse("say odysseus to cyclops"), ("interact", ("say", "cyclops", "odysseus")))
        self.world.position = "SOUTH-TEMPLE"
        self.assertEqual(self.parse("pray"), ("interact", ("pray", "altar", "")))
        self.world.position = "SANDY-CAVE"
        self.assertEqual(self.parse("dig"), ("interact", ("dig", "sand", "")))

    def test_unclear_commands_get_a_helpful_error(self):
        for text, fragment in (("", "Type a command"), ("take", "Take what"), ("put egg", "where"), ("say", "Say what"),
                               ("dance wildly", "don't know how"), ("reset", "Reset button")):
            with self.assertRaises(commands.CommandError) as caught:
                self.parse(text)
            self.assertIn(fragment, str(caught.exception), text)

    def test_a_verb_with_nothing_to_act_on_gets_the_games_own_answer(self):
        result = commands.execute(self.world, "pray")
        self.assertFalse(result["success"])
        self.assertIn("Nothing happens", result["error"])


class TypedPlayTests(unittest.TestCase):
    def test_the_whole_perfect_run_can_be_typed(self):
        world = Maze()
        autoplay.start(world)
        failures = []
        for number, step in enumerate(autoplay.STEPS, 1):
            result = commands.execute(world, typed(step))
            if result.get("success", True) is False:
                failures.append((number, typed(step), result.get("error")))
        self.assertEqual(failures, [])
        self.assertTrue(world.look()["at_goal"])
        self.assertEqual(world._score(), 350)


class CommandEndpointTests(unittest.TestCase):
    def setUp(self):
        self.client = web_app.app.test_client()
        self.transcript = list(web_app._transcript)
        for patcher in (patch("maze._maze", Maze()), patch.dict(web_app._agent_state), patch.dict(web_app._autoplay)):
            patcher.start()
            self.addCleanup(patcher.stop)
        web_app._hint_log.clear()
        self.addCleanup(lambda: web_app._transcript.__setitem__(slice(None), self.transcript))
        self.addCleanup(web_app._hint_log.clear)

    def send(self, text):
        return self.client.post("/api/command", json={"text": text})

    def test_typed_commands_play_the_game_and_appear_in_the_transcript(self):
        self.assertTrue(self.send("north").json["success"])
        self.assertEqual(self.client.get("/api/state").json["room_id"], "NORTH-OF-HOUSE")
        entry = web_app._transcript[-1]
        self.assertEqual(entry["command"], "north")
        self.assertIn("North of House", entry["text"])

    def test_inventory_help_and_unknown_commands(self):
        self.send("i")
        self.assertIn("You are carrying", web_app._transcript[-1]["text"])
        self.assertIn("Nothing", web_app._transcript[-1]["text"])
        helped = self.send("help").json
        self.assertTrue(helped["success"])
        self.assertIn("take lamp", web_app._transcript[-1]["text"])
        unknown = self.send("dance")
        self.assertEqual(unknown.status_code, 200)
        self.assertFalse(unknown.json["success"])
        self.assertIn("don't know how", web_app._transcript[-1]["text"])

    def test_typed_hints_are_counted_and_logged_as_the_players(self):
        self.send("hint")
        self.assertEqual(self.client.get("/api/state").json["hints_used"], 1)
        log = self.client.get("/api/agent/status").json["hint_log"]
        self.assertEqual([h["kind"] for h in log], ["player"])

    def test_refused_while_an_agent_or_auto_play_is_running_and_for_bad_input(self):
        self.assertEqual(self.client.post("/api/command", json={"text": "  "}).status_code, 400)
        self.assertEqual(self.client.post("/api/command", json={"text": "x" * 201}).status_code, 400)
        self.assertEqual(self.client.post("/api/command", json={}).status_code, 400)
        web_app._agent_state["running"] = True
        self.assertEqual(self.send("north").status_code, 409)
        web_app._agent_state["running"] = False
        web_app._autoplay["running"] = True
        self.assertEqual(self.send("north").status_code, 409)

    def test_page_has_a_command_line_that_is_locked_while_something_else_plays(self):
        page = self.client.get("/").get_data(as_text=True)
        for text in ('id="command-input"', 'id="command-line"', "sendCommand(event)", "ArrowUp"):
            self.assertIn(text, page)
        self.assertNotIn("control.id === 'command-input'", page)   # it is disabled by updateControls like the other buttons


if __name__ == "__main__":
    unittest.main()
