import unittest
from unittest.mock import patch

import web_app


class HintLogTests(unittest.TestCase):
    def setUp(self):
        self.client = web_app.app.test_client()
        self.client.post("/api/reset")

    def test_hints_are_logged_by_who_asked_and_viewer_hints_track_delivery(self):
        self.client.post("/api/hint", headers={"X-Maze-Source": "ui"})
        self.client.post("/api/hint")  # the agent reaches this endpoint through the MCP server
        log = self.client.get("/api/agent/status").json["hint_log"]
        self.assertEqual([h["kind"] for h in log], ["player", "agent"])
        self.assertTrue(all(h["text"] and h["room"] for h in log))
        with patch.dict(web_app._agent_state, {"running": True}):
            web_app._stop_event.clear()
            self.assertEqual(self.client.post("/api/agent/hint", json={"hint": "try the window"}).status_code, 200)
            viewer = self.client.get("/api/agent/status").json["hint_log"][-1]
            self.assertEqual((viewer["kind"], viewer["delivered"]), ("viewer", False))
            self.assertEqual(web_app._drain_viewer_hints(), ["try the window"])
            self.assertTrue(self.client.get("/api/agent/status").json["hint_log"][-1]["delivered"])

    def test_reset_clears_the_log_and_page_has_the_notebook(self):
        self.client.post("/api/hint", headers={"X-Maze-Source": "ui"})
        self.client.post("/api/reset")
        self.assertEqual(self.client.get("/api/agent/status").json["hint_log"], [])
        page = self.client.get("/").get_data(as_text=True)
        for text in ('id="hint-history"', 'id="agent-sees"', 'id="agent-notes"', "X-Maze-Source"):
            self.assertIn(text, page)


if __name__ == "__main__":
    unittest.main()
