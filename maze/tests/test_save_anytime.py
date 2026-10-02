import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import saved_games
import web_app


class SaveAnytimeTests(unittest.TestCase):
    def test_saving_works_while_the_agent_is_running_but_restoring_does_not(self):
        client = web_app.app.test_client()
        with tempfile.TemporaryDirectory() as folder, patch.object(saved_games, "SAVE_DIR", Path(folder) / "saves"), \
                patch.dict(web_app._agent_state, {"running": True}):
            saved = client.post("/api/save", json={"name": "mid-run"})
            self.assertEqual(saved.status_code, 200, saved.json)
            self.assertTrue((Path(folder) / "saves" / "mid-run" / "state.json").exists())
            self.assertEqual(client.post("/api/restore", json={"name": "mid-run"}).status_code, 409)

    def test_page_has_an_always_enabled_save_button(self):
        page = web_app.app.test_client().get("/").get_data(as_text=True)
        self.assertIn('id="quick-save"', page)
        self.assertIn("control.id === 'quick-save'", page)


if __name__ == "__main__":
    unittest.main()


class SaveCompatibilityTests(unittest.TestCase):
    def test_save_with_the_trolls_axe_round_trips(self):
        from maze import Maze
        world = Maze()
        world.item_locations["AXE"] = "TROLL-ROOM"  # combat adds this key during the troll fight
        with tempfile.TemporaryDirectory() as folder, patch.object(saved_games, "SAVE_DIR", Path(folder)):
            saved_games.save("after-troll", world)
            self.assertEqual([s["name"] for s in saved_games.list_saves()], ["after-troll"])
            restored, _ = saved_games.load("after-troll")
            self.assertEqual(restored.item_locations["AXE"], "TROLL-ROOM")

    def test_unreadable_saves_are_listed_with_a_reason(self):
        with tempfile.TemporaryDirectory() as folder, patch.object(saved_games, "SAVE_DIR", Path(folder)):
            (Path(folder) / "broken").mkdir()
            (Path(folder) / "broken" / "state.json").write_text("{}", encoding="utf-8")
            listed = saved_games.list_saves()
            self.assertEqual(listed[0]["name"], "broken")
            self.assertIn("error", listed[0])
