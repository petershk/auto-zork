"""Verify completion handoff without making any API requests."""
import json
import unittest
from types import SimpleNamespace
from unittest.mock import Mock

from agent import AgentConfig, GPTAgent


class CompletionTests(unittest.TestCase):
    def make_agent(self, final):
        agent = GPTAgent.__new__(GPTAgent)
        agent.config = AgentConfig(api_key="unused", max_steps=1)
        agent.tools = []
        agent.executor = Mock(return_value={"success": True, "at_goal": True})
        calls = [SimpleNamespace(type="function_call", name="move", arguments='{"direction":"east"}', call_id=name) for name in ("first", "second")]
        create = Mock(side_effect=[SimpleNamespace(id="response_1", output=calls), final])
        agent._client = SimpleNamespace(responses=SimpleNamespace(create=create))
        return agent, create

    def test_final_result_is_delivered_and_further_actions_are_skipped(self):
        agent, create = self.make_agent(SimpleNamespace(output_text="I reached the goal!"))
        events = []
        result = agent.run("Solve the maze", on_step=events.append)
        self.assertTrue(result["success"])
        self.assertEqual(result["message"], "I reached the goal!")
        agent.executor.assert_called_once()
        final_request = create.call_args.kwargs
        self.assertNotIn("tools", final_request)
        self.assertEqual(final_request["previous_response_id"], "response_1")
        outputs = final_request["input"][:2]
        self.assertEqual([o["call_id"] for o in outputs], ["first", "second"])
        self.assertTrue(json.loads(outputs[0]["output"])["at_goal"])
        self.assertIn("Skipped", json.loads(outputs[1]["output"])["error"])
        self.assertEqual(events[-1]["content"], result["message"])

    def test_announcement_failure_preserves_success(self):
        agent, _ = self.make_agent(RuntimeError("announcement unavailable"))
        with self.assertLogs(level="ERROR"):
            result = agent.run("Solve the maze")
        self.assertTrue(result["success"])
        self.assertIn("announcement unavailable", result["announcement_error"])

    def test_guide_response_gets_one_continuation_then_resumes_tools(self):
        agent, _ = self.make_agent(SimpleNamespace(output_text="Complete."))
        agent.config.max_steps = 3
        guide = SimpleNamespace(id="guide", status="completed", output=[SimpleNamespace(type="message", content=[SimpleNamespace(type="output_text", text="What would you like to do next?")])])
        action = SimpleNamespace(id="action", output=[SimpleNamespace(type="function_call", name="move", arguments='{"direction":"north"}', call_id="move")])
        create = Mock(side_effect=[guide, action, SimpleNamespace(output_text="Complete.")])
        agent._client.responses.create = create
        events = []
        result = agent.run("Explore", on_step=events.append)
        self.assertTrue(result["success"])
        self.assertEqual(result["steps"], 2)
        continuation = create.call_args_list[1].kwargs
        self.assertEqual(continuation["previous_response_id"], "guide")
        self.assertIn("execute it with a tool call", continuation["input"][0]["content"])
        agent.executor.assert_called_once_with("move", {"direction": "north"})

    def test_repeated_text_only_responses_stop_with_clear_reason(self):
        agent, _ = self.make_agent(None)
        agent.config.max_steps = 5
        create = Mock(side_effect=[SimpleNamespace(id=str(i), status="completed", output=[]) for i in range(2)])
        agent._client.responses.create = create
        result = agent.run("Explore")
        self.assertFalse(result["success"])
        self.assertIn("continuation attempt", result["reason"])
        self.assertEqual(create.call_count, 2)
        agent.executor.assert_not_called()

    def test_continuation_respects_step_budget(self):
        agent, _ = self.make_agent(None)
        agent._client.responses.create = Mock(return_value=SimpleNamespace(id="idle", status="completed", output=[]))
        result = agent.run("Explore")
        self.assertEqual(result["reason"], "max_steps reached")
        agent._client.responses.create.assert_called_once()


if __name__ == "__main__":
    unittest.main()
