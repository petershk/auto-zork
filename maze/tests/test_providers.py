import os
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from agent import AgentConfig
from chat_adapter import ChatResponses


def completion(content=None, calls=()):
    message = SimpleNamespace(content=content, tool_calls=[
        SimpleNamespace(id=f"c{i}", function=SimpleNamespace(name=n, arguments=a)) for i, (n, a) in enumerate(calls)])
    usage = SimpleNamespace(prompt_tokens=10, completion_tokens=4, total_tokens=14,
                            prompt_tokens_details=SimpleNamespace(cached_tokens=3), completion_tokens_details=None)
    return SimpleNamespace(choices=[SimpleNamespace(message=message)], usage=usage)


class ProviderConfigTests(unittest.TestCase):
    def test_each_provider_uses_its_own_key_url_and_model(self):
        env = {"ANTHROPIC_API_KEY": "a", "GEMINI_API_KEY": "g", "XAI_API_KEY": "x", "OPENROUTER_API_KEY": "r"}
        with patch.dict(os.environ, env, clear=True):
            for provider, key in (("anthropic", "a"), ("google", "g"), ("xai", "x"), ("openrouter", "r")):
                config = AgentConfig(provider=provider)
                self.assertEqual(config.api_key, key)
                self.assertIn("/", config.base_url)

    def test_generic_key_is_never_sent_to_a_named_provider(self):
        with patch.dict(os.environ, {"MAZE_AGENT_API_KEY": "openai-key", "MAZE_AGENT_BASE_URL": "https://x.test/v1",
                                     "MAZE_AGENT_MODEL": "gpt-4o"}, clear=True):
            config = AgentConfig(provider="anthropic")
            self.assertIsNone(config.api_key)
            self.assertNotEqual(config.base_url, "https://x.test/v1")
            self.assertTrue(config.model.startswith("claude"))
            self.assertEqual(AgentConfig(provider="openai").api_key, "openai-key")

    def test_unknown_provider_is_rejected(self):
        with self.assertRaises(ValueError):
            AgentConfig(provider="nope", api_key="k")


class ChatAdapterTests(unittest.TestCase):
    def adapter(self, *responses):
        create = Mock(side_effect=responses)
        return ChatResponses(SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))), create

    def test_tool_round_trip_keeps_history_and_maps_usage(self):
        adapter, create = self.adapter(completion(calls=[("move", '{"direction":"north"}')]), completion("done"))
        tools = [{"type": "function", "name": "move", "description": "d", "parameters": {"type": "object"}}]
        first = adapter.create(model="m", instructions="sys", input=[{"role": "user", "content": "go"}], tools=tools)
        call = [i for i in first.output if i.type == "function_call"][0]
        self.assertEqual((call.name, call.call_id), ("move", "c0"))
        self.assertEqual(first.usage.input_tokens_details.cached_tokens, 3)
        self.assertEqual(create.call_args.kwargs["tools"][0]["function"]["name"], "move")
        second = adapter.create(model="m", instructions="sys", previous_response_id=first.id,
                                input=[{"type": "function_call_output", "call_id": "c0", "output": "{}"}])
        roles = [m["role"] for m in create.call_args.kwargs["messages"]]
        self.assertEqual(roles, ["system", "user", "assistant", "tool"])
        self.assertEqual(second.output_text, "done")

    def test_refresh_without_previous_id_starts_fresh_and_failure_keeps_history(self):
        adapter, create = self.adapter(completion("a"), RuntimeError("boom"), completion("b"))
        first = adapter.create(model="m", instructions="sys", input="hi")
        with self.assertRaises(RuntimeError):
            adapter.create(model="m", instructions="sys", previous_response_id=first.id, input="again")
        adapter.create(model="m", instructions="sys", input="fresh")
        self.assertEqual(len(create.call_args.kwargs["messages"]), 2)


if __name__ == "__main__":
    unittest.main()


class SystemPromptClientTests(unittest.TestCase):
    def test_instructions_only_on_first_turn_for_xai(self):
        from chat_adapter import SystemPromptClient
        create = Mock(return_value="r")
        client = SystemPromptClient(SimpleNamespace(responses=SimpleNamespace(create=create), models=None))
        client.responses.create(model="m", instructions="sys", input=[{"role": "user", "content": "go"}], previous_response_id=None)
        first = create.call_args.kwargs
        self.assertNotIn("instructions", first)
        self.assertEqual(first["input"][0], {"role": "system", "content": "sys"})
        client.responses.create(model="m", instructions="sys", input=[], previous_response_id="r1")
        later = create.call_args.kwargs
        self.assertNotIn("instructions", later)
        self.assertEqual(later["input"], [])
        client.responses.create(model="m", input="ping")
        self.assertEqual(create.call_args.kwargs["input"], "ping")
