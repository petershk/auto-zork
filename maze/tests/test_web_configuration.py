import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import web_app


class ConfigurationTests(unittest.TestCase):
    def test_custom_directives_reach_agent_config_without_api_call(self):
        client = web_app.app.test_client()
        directives = "Explore carefully and avoid requesting hints."
        saved = dict(web_app._agent_state)
        try:
            web_app._agent_state["running"] = False
            with tempfile.TemporaryDirectory() as folder, patch.dict(os.environ, {"MAZE_AGENT_API_KEY": "test-only"}),                     patch.object(web_app, "ENV_FILE", Path(folder) / ".env"),                     patch.object(web_app, "GPTAgent") as agent, patch.object(web_app.threading, "Thread") as thread:
                agent.return_value.check_connection.return_value = {"ok": True}
                response = client.post("/api/agent/run", json={"system_prompt": directives, "max_steps": 50})
                self.assertTrue(response.json["started"])
                config = thread.call_args.kwargs["args"][0]
                self.assertEqual(config.system_prompt, directives)
                self.assertEqual(config.max_steps, 50)
                self.assertEqual(agent.call_args.kwargs["config"].system_prompt, directives)
        finally:
            web_app._agent_state.clear()
            web_app._agent_state.update(saved)

    def test_configuration_defaults_and_dashboard_render(self):
        response = web_app.app.test_client().get("/")
        self.assertEqual(response.status_code, 200)
        page = response.get_data(as_text=True)
        for text in ("Configure agent", "Agent directives and instructions", "Ask for hint", "Action timeline", "World map", "Hints:"):
            self.assertIn(text, page)
        self.assertIn("Explore, discover treasures, and deposit them in the trophy case.", page)


if __name__ == "__main__":
    unittest.main()


class RememberedSelectionTests(unittest.TestCase):
    def test_successful_run_remembers_provider_and_model_but_not_the_key(self):
        client = web_app.app.test_client()
        saved = dict(web_app._agent_state)
        try:
            web_app._agent_state["running"] = False
            with tempfile.TemporaryDirectory() as folder, patch.dict(os.environ, {"XAI_API_KEY": "k"}, clear=True),                     patch.object(web_app, "ENV_FILE", Path(folder) / ".env"),                     patch.object(web_app, "GPTAgent") as agent, patch.object(web_app.threading, "Thread"):
                agent.return_value.check_connection.return_value = {"ok": True}
                self.assertEqual(client.post("/api/agent/run", json={"provider": "xai", "model": "grok-x"}).status_code, 200)
                from dotenv import dotenv_values
                saved_values = dotenv_values(Path(folder) / ".env")
                self.assertEqual(saved_values, {"MAZE_AGENT_PROVIDER": "xai", "MAZE_AGENT_MODEL_XAI": "grok-x"})
                from agent import AgentConfig
                self.assertEqual((AgentConfig().provider, AgentConfig().model), ("xai", "grok-x"))
        finally:
            web_app._agent_state.clear()
            web_app._agent_state.update(saved)

    def test_provider_is_guessed_from_saved_keys(self):
        from agent import AgentConfig
        with patch.dict(os.environ, {"MAZE_AGENT_API_KEY": "generic", "GEMINI_API_KEY": "g"}, clear=True):
            self.assertEqual(AgentConfig().provider, "google")
        with patch.dict(os.environ, {"MAZE_AGENT_API_KEY": "generic"}, clear=True):
            self.assertEqual(AgentConfig().provider, "openai")
        with patch.dict(os.environ, {"GEMINI_API_KEY": "g", "MAZE_AGENT_PROVIDER": "anthropic"}, clear=True):
            self.assertEqual(AgentConfig(api_key="k").provider, "anthropic")


class AgentIdentityTests(unittest.TestCase):
    def test_status_names_the_provider_and_model_of_the_run(self):
        client = web_app.app.test_client()
        saved = dict(web_app._agent_state)
        try:
            web_app._agent_state["running"] = False
            with tempfile.TemporaryDirectory() as folder, patch.dict(os.environ, {"XAI_API_KEY": "k"}, clear=True), \
                    patch.object(web_app, "ENV_FILE", Path(folder) / ".env"), \
                    patch.object(web_app, "GPTAgent") as agent, patch.object(web_app.threading, "Thread"):
                agent.return_value.check_connection.return_value = {"ok": True}
                client.post("/api/agent/run", json={"provider": "xai", "model": "grok-x"})
                self.assertEqual(client.get("/api/agent/status").json["agent"],
                                 {"provider": "xai", "label": "xAI (Grok)", "model": "grok-x"})
            page = client.get("/").get_data(as_text=True)
            self.assertIn('id="agent-identity"', page)
        finally:
            web_app._agent_state.clear()
            web_app._agent_state.update(saved)


class AgentPanelLayoutTests(unittest.TestCase):
    def test_hint_is_a_modal_and_feeds_are_expandable_panels(self):
        page = web_app.app.test_client().get("/").get_data(as_text=True)
        self.assertIn('id="give-hint"', page)
        self.assertIn('<dialog id="hint-dialog"', page)
        self.assertNotIn("viewer-hint-controls", page)
        for panel, feed in (("reasoning-panel", "reasoning"), ("log-panel", "log")):
            self.assertRegex(page, rf'<details class="agent-feed" id="{panel}" open><summary>[^<]+</summary><div id="{feed}">')


class StartupMessageTests(unittest.TestCase):
    def test_announces_the_address_and_opens_the_browser_unless_disabled(self):
        with patch.dict(os.environ, {}, clear=True), patch.object(web_app.threading, "Timer") as timer, \
                patch("builtins.print") as printed:
            web_app.announce_and_open("http://127.0.0.1:5000")
            self.assertIn("http://127.0.0.1:5000", printed.call_args.args[0])
            timer.assert_called_once()
            self.assertEqual(timer.call_args.kwargs["args"], ["http://127.0.0.1:5000"])
        with patch.dict(os.environ, {"MAZE_NO_BROWSER": "1"}), patch.object(web_app.threading, "Timer") as timer, \
                patch("builtins.print"):
            web_app.announce_and_open("http://127.0.0.1:5000")
            timer.assert_not_called()


class PageScriptTests(unittest.TestCase):
    @unittest.skipUnless(__import__("shutil").which("node"), "Node.js is not installed")
    def test_the_served_page_script_is_valid_javascript(self):
        """The page is one big Python string, so a stray escape can silently break every button."""
        import re
        import subprocess
        page = web_app.app.test_client().get("/").get_data(as_text=True)
        script = re.search(r"<script>(.*)</script>", page, re.S).group(1)
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "page.js"
            path.write_text(script, encoding="utf-8")
            result = subprocess.run(["node", "--check", str(path)], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
