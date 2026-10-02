import unittest
from unittest.mock import patch

import maze
import web_app


class TranscriptTests(unittest.TestCase):
    def setUp(self):
        self.world = maze.Maze()
        self.patch = patch("maze._maze", self.world)
        self.patch.start()
        self.addCleanup(self.patch.stop)
        self.saved = list(web_app._transcript)
        self.addCleanup(lambda: web_app._transcript.__setitem__(slice(None), self.saved))
        self.client = web_app.app.test_client()
        self.client.post("/api/reset")

    def test_first_load_has_welcome_without_a_command_or_turn(self):
        web_app._transcript.clear()
        state=self.client.get("/api/state").json
        self.assertEqual(state["transcript"][0]["command"], "")
        self.assertIn("ZORK", state["transcript"][0]["text"])
        self.assertEqual(self.world.turns, 0)

    def test_manual_and_mcp_endpoints_record_player_prose(self):
        self.client.post("/api/items/open_container", json={"item": "MAILBOX"})
        self.client.post("/api/items/examine", json={"item": "ADVERTISEMENT"})
        self.client.post("/api/move", json={"direction": "north"})
        entries = self.client.get("/api/state").json["transcript"]
        self.assertEqual(entries[-1]["command"], "north")
        self.assertIn("North of House", entries[-1]["text"])
        self.assertIn("WELCOME TO ZORK", entries[-2]["text"])
        self.assertNotIn("transcript", self.client.post("/api/look").json)
        count = len(web_app._transcript)
        turns = self.world.turns
        self.client.get("/api/state"); self.client.get("/api/state")
        self.assertEqual(len(web_app._transcript), count)
        self.assertEqual(self.world.turns, turns)

    def test_failures_hints_and_reset_are_visible(self):
        self.client.post("/api/move", json={"direction": "east"})
        self.assertIn("No exit", web_app._transcript[-1]["text"])
        self.client.post("/api/hint")
        self.assertEqual(web_app._transcript[-1]["command"], "hint")
        self.assertEqual(self.world.hints_used, 1)
        self.client.post("/api/reset")
        self.assertEqual(len(web_app._transcript), 1)
        self.assertIn("West of House", web_app._transcript[0]["text"])


if __name__ == "__main__":
    unittest.main()
