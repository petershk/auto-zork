import unittest
from types import SimpleNamespace
from unittest.mock import Mock
import httpx
from openai import BadRequestError
from agent import AgentConfig, GPTAgent
from context_memory import ContextMemory


def action(identifier):
    return SimpleNamespace(id=identifier, output=[SimpleNamespace(type="function_call", name="move", arguments='{"direction":"north","reasoning":"Explore the passage."}', call_id=identifier)])


class ContextTests(unittest.TestCase):
    def agent(self, responses, interval=12):
        obj=GPTAgent.__new__(GPTAgent)
        obj.config=AgentConfig(api_key="unused", max_steps=2, context_refresh_steps=interval)
        obj.tools=[]
        obj.executor=Mock(side_effect=[{"success":True,"at_goal":False,"room_id":"NORTH","room_name":"North room","inventory":["LAMP"],"score":10}, {"success":True,"at_goal":True}])
        obj._client=SimpleNamespace(responses=SimpleNamespace(create=Mock(side_effect=responses)))
        return obj

    def test_context_error_retries_without_repeating_game_action(self):
        request=httpx.Request("POST","https://api.openai.com/v1/responses")
        error=BadRequestError("Your input exceeds the context window of this model.", response=httpx.Response(400,request=request),body={"code":"context_length_exceeded"})
        obj=self.agent([action("first"),error,action("second"),SimpleNamespace(output_text="Complete")])
        events=[]
        result=obj.run("Find treasures",on_step=events.append)
        self.assertTrue(result["success"])
        self.assertEqual(result["context_refreshes"],1)
        self.assertEqual(obj.executor.call_count,2)
        retry=obj._client.responses.create.call_args_list[2].kwargs
        self.assertIsNone(retry["previous_response_id"])
        self.assertEqual([i.get("type") for i in retry["input"]],[None])
        self.assertIn("LAMP",retry["input"][0]["content"])
        self.assertIn("Find treasures",retry["input"][0]["content"])
        self.assertTrue(any("context limit reached" in e.get("content","") for e in events))

    def test_scheduled_refresh_starts_a_fresh_chain(self):
        obj=self.agent([action("first"),action("second"),SimpleNamespace(output_text="Complete")],interval=1)
        result=obj.run("Explore")
        self.assertTrue(result["success"])
        self.assertEqual(result["context_refreshes"],1)
        self.assertIsNone(obj._client.responses.create.call_args_list[1].kwargs["previous_response_id"])

    def test_unrelated_api_errors_are_not_retried(self):
        error=BadRequestError("Invalid model",response=httpx.Response(400,request=httpx.Request("POST","http://localhost")),body={"code":"invalid_model"})
        obj=self.agent([error])
        with self.assertLogs(level="ERROR"):
            result=obj.run("Explore")
        self.assertFalse(result["success"])
        obj._client.responses.create.assert_called_once()
        obj.executor.assert_not_called()

    def test_memory_is_bounded_and_records_observed_passages_only(self):
        memory=ContextMemory("Explore")
        memory.observe("look",{}, {"room_id":"A","exits":["north"]})
        memory.observe("move",{"direction":"north"},{"success":False,"room_id":"A","error":"Blocked"})
        self.assertNotIn("observed_destinations",memory.rooms["A"])
        memory.observe("move",{"direction":"north"},{"success":True,"room_id":"B"})
        self.assertEqual(memory.rooms["A"]["observed_destinations"],{"north":"B"})
        for n in range(150):
            memory.observe("look",{}, {"room_id":str(n),"description":"x"*10000,"grid":{"secret":"hidden"}})
        self.assertLessEqual(len(memory.rooms),80)
        self.assertLessEqual(len(memory.notes),10)
        self.assertLess(len(memory.input()[0]["content"]),19000)
        self.assertNotIn("hidden",memory.input()[0]["content"])


if __name__=="__main__": unittest.main()


class LeftItemMemoryTests(unittest.TestCase):
    def test_memory_tracks_set_down_items_and_survives_restore(self):
        from context_memory import ContextMemory
        memory = ContextMemory("goal")
        memory.observe("drop", {"item": "wrench"}, {"success": True, "item_id": "WRENCH", "room_id": "RESERVOIR-NORTH"})
        memory.observe("drop", {"item": "lamp"}, {"success": False, "error": "no", "item_id": "LAMP", "room_id": "X"})
        self.assertEqual(dict(memory.left_items), {"WRENCH": "RESERVOIR-NORTH"})
        restored = ContextMemory.restore(memory.snapshot())
        self.assertIn("WRENCH", restored.input()[0]["content"])
        restored.observe("take", {"item": "wrench"}, {"success": True, "item_id": "WRENCH", "room_id": "RESERVOIR-NORTH"})
        self.assertEqual(dict(restored.left_items), {})
