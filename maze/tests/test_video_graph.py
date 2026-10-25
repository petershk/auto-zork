import unittest
from types import SimpleNamespace

import commands
from maze import Maze
from video.embellish import Embellisher, jobs, parse_reply, run, MotionPrompt
from video.graph import current_keyframe, derive, find_edge, merge_manifest
from video.models import Manifest

HOUSE = {"FOREST AND OUTSIDE OF HOUSE", "HOUSE"}


class FakeLLM:
    """Answers every request with prompts that name the request, so tests can see what went where."""

    def __init__(self):
        self.calls = 0
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self.create))

    def create(self, model, messages, max_tokens):
        self.calls += 1
        user = messages[-1]["content"]
        if '"edit_prompt"' in user:
            reply = '{"edit_prompt": "Same image; changed. Change nothing else.", "idle_prompt": "Dust. The camera is almost still."}'
        elif '"image_prompt"' in user:
            reply = '```json\n{"image_prompt": "First-person view.", "idle_prompt": "Grass sways. The camera is almost still."}\n```'
        else:
            reply = '{"motion_prompt": "The camera walks on."}'
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=reply))])


class VideoGraphTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.keyframes, cls.edges, cls.unreached = derive("fal:test")
        cls.house = derive("fal:test", HOUSE)

    def test_whole_game_is_derived(self):
        self.assertEqual(self.unreached, [])
        ids = [e.id for e in self.edges]
        self.assertEqual(len(ids), len(set(ids)))
        self.assertEqual({k.room for k in self.keyframes}, set(Maze().rooms))

    def test_state_changes_become_variants_of_the_room(self):
        window = next(k for k in self.keyframes if k.room == "EAST-OF-HOUSE" and k.derived_from)
        self.assertEqual(window.derived_from, "east_of_house")
        self.assertIn("open kitchen window", window.scenery)
        opening = next(e for e in self.edges if e.start_frame == "east_of_house" and e.end_frame == window.id)
        self.assertEqual((opening.type, opening.verbs), ("action", ["open window"]))

    def test_the_thief_does_not_make_keyframes(self):
        self.assertFalse([k for k in self.keyframes if "thief" in k.scenery])

    def test_house_walk_has_a_clip_for_every_visible_change(self):
        _, edges, _ = self.house
        game = Maze()
        before = current_keyframe(game)
        for command in ("north", "east", "open window", "west", "west", "move rug", "east", "up", "down", "east",
                        "north", "west", "west", "east", "south", "east", "west"):
            result = commands.execute(game, command)
            self.assertTrue(result["success"], (command, result.get("error")))
            after = current_keyframe(game)
            if before != after:
                self.assertIsNotNone(find_edge(edges, before, after), f"no clip for {command}: {before} -> {after}")
            before = after

    def test_scope_keeps_only_edges_inside_it(self):
        keyframes, edges, _ = self.house
        keep = {k.id for k in keyframes}
        self.assertEqual({k.area for k in keyframes}, {"Forest And Outside Of House", "House"})
        self.assertTrue(all(e.start_frame in keep and e.end_frame in keep for e in edges))
        north = next(k for k in keyframes if k.id == "north_of_house")
        self.assertEqual(north.reference_frames, [])  # made from text alone, never from another room's picture

    def test_merge_keeps_prompts_and_results_for_unchanged_scenery(self):
        keyframes, edges, _ = derive("fal:test", {"KITCHEN"})
        old = merge_manifest(Manifest(), keyframes, edges)
        old.keyframes[0].image_prompt, old.keyframes[0].status = "A kitchen.", "approved"
        fresh_frames, fresh_edges, _ = derive("fal:test", {"KITCHEN"})
        merged = merge_manifest(old, fresh_frames, fresh_edges)
        self.assertEqual((merged.keyframes[0].image_prompt, merged.keyframes[0].status), ("A kitchen.", "approved"))
        fresh_frames[0].scenery += " It is dusty."
        merged = merge_manifest(old, fresh_frames, fresh_edges)
        self.assertEqual((merged.keyframes[0].image_prompt, merged.keyframes[0].status), (None, "pending"))

    def test_embellish_fills_every_prompt_and_skips_done_work(self):
        keyframes, edges, _ = derive("fal:test", {"EAST-OF-HOUSE", "NORTH-OF-HOUSE"})
        manifest = merge_manifest(Manifest(), keyframes, edges)
        llm = FakeLLM()
        self.assertEqual(run(manifest, Embellisher(llm, "m"), jobs(manifest), log=lambda _: None), 0)
        base = next(k for k in manifest.keyframes if k.id == "east_of_house")
        variant = next(k for k in manifest.keyframes if k.derived_from == "east_of_house")
        self.assertEqual((base.image_prompt, variant.image_prompt), ("First-person view.", None))
        self.assertTrue(variant.edit_prompt.startswith("Same image;"))
        self.assertTrue(all(e.motion_prompt for e in manifest.actions))
        idle = next(e for e in manifest.actions if e.id == "east_of_house__idle_1")
        self.assertEqual(idle.motion_prompt, base.idle_prompt)
        self.assertEqual(jobs(manifest), [])
        self.assertEqual(len(jobs(manifest, force=True, only="north_of_house")), 1 + sum("north_of_house" in e.id and e.type != "idle" for e in manifest.actions))

    def test_parse_reply_rejects_text_without_json(self):
        self.assertEqual(parse_reply('Sure! {"motion_prompt": "Up."}', MotionPrompt).motion_prompt, "Up.")
        with self.assertRaises(ValueError):
            parse_reply("no json here", MotionPrompt)


if __name__ == "__main__":
    unittest.main()
