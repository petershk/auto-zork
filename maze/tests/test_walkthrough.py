import unittest
from pathlib import Path
from unittest.mock import patch

import web_app

import autoplay
import make_walkthrough
from maze import Maze


class WalkthroughTests(unittest.TestCase):
    def test_the_scripted_run_finishes_the_game_with_every_point(self):
        outcome = autoplay.play(Maze())
        self.assertIsNone(outcome["error"])
        self.assertTrue(outcome["completed"], outcome)
        self.assertEqual((outcome["score"], outcome["steps"]), (350, len(autoplay.STEPS)))

    def test_the_same_world_can_run_it_twice(self):
        world = Maze()
        for _ in range(2):
            self.assertTrue(autoplay.play(world)["completed"])

    def test_every_step_succeeds_and_the_player_never_dies(self):
        world = Maze()
        failures = []
        autoplay.play(world, lambda n, step, result: failures.append((n, step, result.get("error")))
                      if result.get("success", True) is False else None)
        self.assertEqual(failures, [])
        self.assertFalse(world.dead)

    def test_run_ends_with_the_endgame_state(self):
        world = Maze()
        autoplay.play(world)
        look = world.look()
        self.assertTrue(look["at_goal"])
        self.assertEqual(look["objective"], "Your adventure is complete.")
        self.assertIn("WON-FLAG", world.flags)

    def test_walkthrough_file_matches_the_route_in_make_walkthrough(self):
        text = Path(make_walkthrough.OUTPUT).read_text(encoding="utf-8")
        self.assertEqual(text, make_walkthrough.render(make_walkthrough.record()),
                         "walkthrough.py is out of date: run make_walkthrough.py")

    def test_every_step_has_a_label_and_a_transcript_command(self):
        self.assertEqual(len(autoplay.LABELS), len(autoplay.STEPS))
        self.assertEqual(autoplay.command_text(("put", "BAR", "TROPHY-CASE")), "put BAR in TROPHY-CASE")
        self.assertEqual(autoplay.command_text(("move", "north")), "north")
        self.assertEqual(autoplay.command_text(("interact", "say", "room", "echo")), "interact say room echo")


class AutoplayWebTests(unittest.TestCase):
    def setUp(self):
        self.client = web_app.app.test_client()
        self.transcript = list(web_app._transcript)
        for patcher in (patch("maze._maze", Maze()), patch.dict(web_app._agent_state), patch.dict(web_app._autoplay)):
            patcher.start()
            self.addCleanup(patcher.stop)
        self.addCleanup(lambda: web_app._transcript.__setitem__(slice(None), self.transcript))

    def start(self, speed="fast"):
        with patch.object(web_app.threading, "Thread") as thread:
            response = self.client.post("/api/autoplay/start", json={"speed": speed})
        return response, thread

    def test_the_web_app_plays_the_whole_run_and_shows_it_in_the_transcript(self):
        response, thread = self.start()
        self.assertEqual(response.status_code, 200)
        self.assertEqual(thread.call_args.kwargs["args"], (web_app.AUTOPLAY_DELAYS["fast"],))
        self.assertTrue(self.client.get("/api/agent/status").json["autoplay"]["running"])
        web_app._run_autoplay(0)  # what the background thread does, run here to completion
        status = self.client.get("/api/agent/status").json["autoplay"]
        self.assertEqual((status["running"], status["finished"], status["completed"], status["error"]), (False, True, True, None))
        self.assertEqual((status["step"], status["total"], status["score"]), (len(autoplay.STEPS), len(autoplay.STEPS), 350))
        # the transcript keeps the latest 300 commands, so the opening steps have scrolled off
        commands = [entry["command"] for entry in web_app._transcript]
        self.assertEqual(commands, [autoplay.command_text(step) for step in autoplay.STEPS][-300:])
        self.assertIn("examine MAP", commands)

    def test_stop_ends_the_run_part_way(self):
        self.start()
        web_app._autoplay_stop.set()
        web_app._run_autoplay(0)
        status = self.client.get("/api/agent/status").json["autoplay"]
        self.assertEqual((status["running"], status["completed"], status["step"]), (False, False, 0))

    def test_rejects_bad_speed_a_running_agent_and_conflicting_actions(self):
        self.assertEqual(self.start("warp")[0].status_code, 400)
        web_app._agent_state["running"] = True
        self.assertEqual(self.start()[0].status_code, 409)
        web_app._agent_state["running"] = False
        web_app._autoplay["running"] = True
        self.assertEqual(self.start()[0].status_code, 409)
        self.assertEqual(self.client.post("/api/reset").status_code, 409)
        self.assertEqual(self.client.post("/api/agent/run", json={}).status_code, 409)
        self.assertEqual(self.client.post("/api/restore", json={"name": "x"}).status_code, 409)

    def test_page_has_the_controls_and_keeps_stop_and_save_usable(self):
        page = self.client.get("/").get_data(as_text=True)
        for text in ('id="autoplay-start"', 'id="autoplay-speed"', 'id="autoplay-stop"', 'id="autoplay-status"',
                     "control.id === 'autoplay-stop'", "control.id === 'quick-save'"):
            self.assertIn(text, page)


if __name__ == "__main__":
    unittest.main()


class MapAtTheEndTests(unittest.TestCase):
    def at_350(self):
        """The world just after the last treasure is deposited, with the map revealed."""
        world = Maze()
        autoplay.start(world)
        for step in autoplay.STEPS:
            if step == ("examine", "MAP"):
                break
            autoplay.apply_step(world, step)
        return world

    def test_the_run_leaves_the_player_holding_the_map(self):
        world = Maze()
        autoplay.start(world)
        for step in autoplay.STEPS[:-1]:               # everything up to going in through the stone door
            autoplay.apply_step(world, step)
        self.assertEqual(world.item_locations["MAP"], "inventory")
        read = world.interact("read", "map")
        self.assertTrue(read["success"], read)
        self.assertIn("Stone Barrow", read["item"]["readable_text"])
        self.assertFalse(world.look()["at_goal"])
        autoplay.apply_step(world, autoplay.STEPS[-1])
        self.assertTrue(world.look()["at_goal"])
        self.assertEqual(world.item_locations["MAP"], "inventory")

    def test_the_run_ends_by_going_in_and_shows_the_original_ending(self):
        self.assertEqual(autoplay.STEPS[-1], ("move", "in"))
        world = Maze()
        last = []
        autoplay.play(world, lambda n, step, result: last.append(result))
        self.assertIn("Inside the Barrow", " ".join(last[-1]["encounter_events"]))
        text = web_app._game_text(last[-1])
        self.assertIn("rank of Master Adventurer", text)
        self.assertNotIn("massive barrow of stone", text)   # no room description after the farewell

    def test_the_map_can_be_taken_by_any_name_including_with_an_article(self):
        for name in ("MAP", "map", "the map", "an ancient map", "parchment"):
            self.assertTrue(self.at_350().take(name)["success"], name)

    def test_an_announcement_is_printed_once_not_under_every_room(self):
        announcement = "An ancient map has appeared in the trophy case."
        result = {"room_name": "Hall", "description": "A hall.", "room_id": "HALL", "items": [], "exits": [],
                  "announcement": announcement}
        saved = list(web_app._transcript)
        try:
            web_app._transcript[:] = []
            first = web_app._game_text(result)
            web_app._transcript.append({"id": 1, "command": "put", "text": first})
            self.assertIn(announcement, first)
            self.assertNotIn(announcement, web_app._game_text(result))
        finally:
            web_app._transcript[:] = saved

    def test_room_text_lists_what_is_in_the_trophy_case(self):
        text = web_app._game_text(self.at_350().look())
        self.assertIn("The trophy case contains:", text)
        self.assertIn("ancient map", text)

    def test_read_works_for_anything_readable_and_not_for_the_rest(self):
        world = Maze()
        world.item_locations["ADVERTISEMENT"] = "inventory"
        self.assertTrue(world.interact("read", "leaflet")["success"])
        self.assertFalse(world.interact("read", "lamp")["success"])

    def test_the_interact_panel_offers_take_and_routes_item_verbs(self):
        page = web_app.app.test_client().get("/").get_data(as_text=True)
        self.assertIn("const everyday = ['take', 'examine', 'drop', 'put', 'close']", page)
        self.assertIn("itemAction(itemVerbs[verbs.value]", page)
