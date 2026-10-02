import unittest
from types import SimpleNamespace
from agent import AgentConfig, GPTAgent
import web_app


class ViewerHintsTests(unittest.TestCase):
    def setUp(self):
        self.saved=dict(web_app._agent_state)
        self.pending=list(web_app._pending_viewer_hints)
        self.stopped=web_app._stop_event.is_set()
        self.addCleanup(self.restore)
        web_app._agent_state["running"]=True
        web_app._agent_state["viewer_hints"]=0
        web_app._pending_viewer_hints.clear()
        web_app._stop_event.clear()
        self.client=web_app.app.test_client()

    def restore(self):
        web_app._agent_state.clear(); web_app._agent_state.update(self.saved)
        web_app._pending_viewer_hints[:]=self.pending
        if self.stopped: web_app._stop_event.set()
        else: web_app._stop_event.clear()

    def test_queue_and_drain_without_consuming_game_turn(self):
        turns=web_app.maze.look()["turns"]
        response=self.client.post("/api/agent/hint",json={"hint":" Try to open the window "})
        self.assertEqual(response.status_code,200)
        self.assertEqual(web_app._drain_viewer_hints(),["Try to open the window"])
        self.assertEqual(web_app._drain_viewer_hints(),[])
        self.assertEqual(web_app.maze.look()["turns"],turns)
        self.assertEqual(web_app._agent_state["viewer_hints"],1)

    def test_invalid_idle_stopping_and_full_queue(self):
        for body in ({"hint":" "},{"hint":5},{"hint":"x"*1001},[]):
            self.assertEqual(self.client.post("/api/agent/hint",json=body).status_code,400)
        web_app._agent_state["running"]=False
        self.assertEqual(self.client.post("/api/agent/hint",json={"hint":"Open"}).status_code,409)
        web_app._agent_state["running"]=True
        web_app._stop_event.set()
        self.assertEqual(self.client.post("/api/agent/hint",json={"hint":"Open"}).status_code,409)
        web_app._stop_event.clear()
        web_app._pending_viewer_hints[:]=["queued"]*10
        self.assertEqual(self.client.post("/api/agent/hint",json={"hint":"Open"}).status_code,429)

    def test_hint_injected_into_model_and_preserved_on_refresh(self):
        from unittest.mock import Mock
        agent=GPTAgent.__new__(GPTAgent)
        agent.config=AgentConfig(api_key="unused",max_steps=2,context_refresh_steps=1)
        agent.tools=[]
        agent.executor=Mock(return_value={"room_id":"HOUSE","at_goal":False})
        call=SimpleNamespace(type="function_call",name="look",arguments="{}",call_id="look")
        create=Mock(side_effect=[SimpleNamespace(id="first",output=[call]),SimpleNamespace(id="second",status="completed",output=[])])
        agent._client=SimpleNamespace(responses=SimpleNamespace(create=create))
        events=[]
        agent.run("Explore",on_step=events.append,get_hints=Mock(side_effect=[["Try to open the window"],[]]))
        first=create.call_args_list[0].kwargs["input"]
        self.assertEqual(first[-1]["content"],"Viewer hint: Try to open the window")
        fresh=create.call_args_list[1].kwargs
        self.assertIsNone(fresh["previous_response_id"])
        self.assertIn("Try to open the window",fresh["input"][0]["content"])
        self.assertTrue(any("Viewer hint received" in e.get("content","") for e in events))


if __name__=="__main__": unittest.main()
