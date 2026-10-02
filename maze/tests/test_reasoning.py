"""Richer reasoning output, checked without any API requests."""
import unittest
from types import SimpleNamespace
from unittest.mock import Mock

from agent import DEFAULT_SYSTEM_PROMPT, REASONING_PROPERTY, AgentConfig, GPTAgent
from chat_adapter import ChatResponses


def agent_for(**config):
    agent = GPTAgent.__new__(GPTAgent)
    agent.config = AgentConfig(api_key="unused", **config)
    return agent


class ReasoningRequestTests(unittest.TestCase):
    def test_prompt_and_tool_field_ask_for_observation_plan_and_why(self):
        self.assertIn("what you just observed", DEFAULT_SYSTEM_PROMPT)
        self.assertNotIn("one brief sentence", DEFAULT_SYSTEM_PROMPT)
        description = REASONING_PROPERTY["reasoning"]["description"]
        for word in ("observed", "plan", "why"):
            self.assertIn(word, description)

    def test_reasoning_summaries_are_requested_only_from_openai_reasoning_models(self):
        wanted = {"reasoning": {"summary": "auto"}}
        self.assertEqual(agent_for(provider="openai", model="gpt-5")._reasoning_options(), wanted)
        self.assertEqual(agent_for(provider="openai", model="o3-mini")._reasoning_options(), wanted)
        self.assertEqual(agent_for(provider="openai", model="gpt-4o-mini")._reasoning_options(), {})
        self.assertEqual(agent_for(provider="xai", model="grok-4")._reasoning_options(), {})
        self.assertEqual(agent_for(provider="custom", model="gpt-5")._reasoning_options(), {})
        self.assertEqual(agent_for(provider="openai", model="gpt-5", base_url="https://proxy.example/v1")._reasoning_options(), {})


class ReasoningDisplayTests(unittest.TestCase):
    def test_thinking_summary_is_shown_before_the_actions_it_led_to(self):
        agent = agent_for(provider="openai", model="gpt-5", max_steps=1)
        agent.tools = []
        agent.executor = Mock(return_value={"success": True, "at_goal": True})
        thinking = SimpleNamespace(type="reasoning", summary=[SimpleNamespace(type="summary_text", text="The window is the way in."),
                                                              SimpleNamespace(type="summary_text", text="Open it first.")])
        call = SimpleNamespace(type="function_call", name="move", arguments='{"direction":"west","reasoning":"Enter the kitchen."}', call_id="c1")
        create = Mock(side_effect=[SimpleNamespace(id="r1", output=[thinking, call]), SimpleNamespace(output_text="Done.")])
        agent._client = SimpleNamespace(responses=SimpleNamespace(create=create))
        events = []
        agent.run("Explore", on_step=events.append)
        self.assertEqual(create.call_args_list[0].kwargs["reasoning"], {"summary": "auto"})
        notes = [e["content"] for e in events if e["type"] == "reasoning"]
        self.assertEqual(notes[0], "(thinking) The window is the way in.\nOpen it first.")
        self.assertEqual(notes[1], "[move] Enter the kitchen.")

    def test_models_without_summaries_are_called_without_the_reasoning_option(self):
        agent = agent_for(provider="openai", model="gpt-4o-mini", max_steps=1)
        agent.tools = []
        agent.executor = Mock(return_value={"success": True, "at_goal": True})
        call = SimpleNamespace(type="function_call", name="move", arguments="{}", call_id="c1")
        create = Mock(side_effect=[SimpleNamespace(id="r1", output=[call]), SimpleNamespace(output_text="Done.")])
        agent._client = SimpleNamespace(responses=SimpleNamespace(create=create))
        agent.run("Explore")
        self.assertNotIn("reasoning", create.call_args_list[0].kwargs)

    def test_chat_providers_that_return_reasoning_text_get_it_shown(self):
        message = SimpleNamespace(content="Going north.", tool_calls=[], model_extra={"reasoning": "The troll room is north."})
        completion = SimpleNamespace(choices=[SimpleNamespace(message=message)], usage=None)
        create = Mock(return_value=completion)
        responses = ChatResponses(SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create))))
        response = responses.create(model="m", input="hi")
        reasoning = [item for item in response.output if item.type == "reasoning"]
        self.assertEqual(reasoning[0].summary[0].text, "The troll room is north.")
        self.assertEqual([item.type for item in response.output], ["reasoning", "message"])


if __name__ == "__main__":
    unittest.main()
