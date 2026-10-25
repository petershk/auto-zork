import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import commands
from maze import Maze
from video import gen_clips, scene
from video.graph import derive, load_manifest, merge_manifest, save_manifest
from video.media_providers import FalVideoProvider
from video.models import Manifest

OUTSIDE = {"WEST-OF-HOUSE", "NORTH-OF-HOUSE", "EAST-OF-HOUSE"}


class FakeVideo:
    def __init__(self):
        self.calls = []

    def duration(self, seconds):
        return max(4, seconds)

    def first_last_frame(self, *, first, last, prompt, duration_s, resolution="720p", audio, seed=None):
        self.calls.append((first.name, last.name, duration_s, audio))
        return b"mp4", 42


def outside_manifest():
    keyframes, edges, _ = derive("fal:test", OUTSIDE)
    manifest = merge_manifest(Manifest(), keyframes, edges)
    for k in manifest.keyframes:
        k.image_path, k.status = f"assets/keyframes/{k.id}.png", "generated"
    for e in manifest.actions:
        e.motion_prompt = "The camera moves."
    return manifest


class ClipGenerationTests(unittest.TestCase):
    def test_select_takes_exact_ids_then_prefixes(self):
        manifest = outside_manifest()
        edges, unknown = gen_clips.select(manifest, ["west_of_house__to__north_of_house", "east_of_house__d567e5__to", "nope"])
        ids = [e.id for e in edges]
        self.assertIn("west_of_house__to__north_of_house", ids)
        self.assertTrue(any(i.startswith("east_of_house__d567e5__to") for i in ids))
        self.assertEqual(unknown, ["nope"])

    def test_clips_start_and_end_on_their_keyframes_within_budget(self):
        manifest, video, written = outside_manifest(), FakeVideo(), {}
        edges, _ = gen_clips.select(manifest, ["west_of_house__to__west_of_house__9a9c97", "west_of_house__9a9c97__take_leaflet"])
        failures = gen_clips.run(manifest, video, edges, lambda audio: 0.3, 100, workers=1,
                                 write=lambda p, d: written.update({p.name: d}), log=lambda _: None)
        self.assertEqual(failures, 0)
        self.assertIn(("west_of_house.png", "west_of_house__9a9c97.png", 5, True), video.calls)
        self.assertIn(("west_of_house__9a9c97.png", "west_of_house__9a9c97.png", 5, True), video.calls)  # cosmetic
        self.assertEqual(set(written), {"west_of_house__to__west_of_house__9a9c97__1.mp4", "west_of_house__9a9c97__take_leaflet__1.mp4"})
        self.assertAlmostEqual(manifest.spent_usd, 3.0)
        over = outside_manifest()
        edges, _ = gen_clips.select(over, ["west_of_house__to__north_of_house"])
        self.assertEqual(gen_clips.run(over, FakeVideo(), edges, lambda audio: 0.3, 1.0, log=lambda _: None), 1)

    def test_problems_name_missing_pictures_and_prompts(self):
        manifest = outside_manifest()
        frames = {k.id: k for k in manifest.keyframes}
        edge = next(e for e in manifest.actions if e.id == "west_of_house__to__north_of_house")
        self.assertIsNone(gen_clips.problems(edge, frames))
        frames["north_of_house"].status = "pending"
        self.assertIn("north_of_house", gen_clips.problems(edge, frames))

    def test_seedance_lengths_are_rounded_to_what_it_accepts(self):
        provider = FalVideoProvider("seedance-2.0", client=object())
        self.assertEqual((provider.duration(3), provider.duration(6), provider.duration(20)), (4, 6, 15))
        with self.assertRaises(ValueError):
            FalVideoProvider("kling-3.0", client=object())


class TurnClipTests(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        path = Path(folder.name) / "manifest.json"
        manifest = outside_manifest()
        for e in manifest.actions:
            if e.id in ("west_of_house__to__west_of_house__9a9c97", "west_of_house__9a9c97__take_leaflet",
                        "west_of_house__9a9c97__to__north_of_house"):
                for clip in e.clips:
                    clip.path, clip.status = f"assets/clips/{e.id}__1.mp4", "generated"
        save_manifest(manifest, path)
        for e in manifest.actions:  # the made clips are on disk here, so this app serves them itself
            for clip in e.clips:
                if clip.path:
                    (Path(folder.name) / clip.path).parent.mkdir(parents=True, exist_ok=True)
                    (Path(folder.name) / clip.path).write_bytes(b"mp4")
        for name, value in (("MANIFEST_FILE", path), ("load_manifest", lambda: load_manifest(path)),
                            ("VIDEO_DIR", Path(folder.name))):
            patcher = patch.object(scene, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        scene._cache["mtime"] = None

    def play(self, turns, game, text):
        commands.execute(game, text)
        return turns.after(game, text)

    def test_each_turn_plays_the_clip_between_its_keyframes(self):
        game, turns = Maze(), scene.Turns()
        turns.reset(game)
        opened = self.play(turns, game, "open mailbox")
        self.assertEqual(opened["id"], "west_of_house__to__west_of_house__9a9c97")
        self.assertTrue(opened["sources"][0].startswith("/video/assets/clips/west_of_house__to__west_of_house__9a9c97__1.mp4"))
        took = self.play(turns, game, "take the leaflet")
        self.assertEqual(took["id"], "west_of_house__9a9c97__take_leaflet")
        self.assertIsNone(self.play(turns, game, "inventory"))  # nothing visible happened and there is no clip
        walked = self.play(turns, game, "north")
        self.assertEqual((walked["from"], walked["to"]), ("west_of_house__9a9c97", "north_of_house"))
        self.assertIsNone(self.play(turns, game, "east"))  # no clip made for this edge yet: the picture just changes
        self.assertEqual(turns.sequence, 3)

    def test_the_scene_lists_finished_idle_loops_for_the_picture_on_show(self):
        manifest = load_manifest(scene.MANIFEST_FILE)
        idle = next(e for e in manifest.actions if e.id == "west_of_house__idle_1")
        idle.clips[0].path, idle.clips[0].status = "assets/clips/west_of_house__idle_1__1.mp4", "generated"
        manifest.keyframes[0].image_path = "assets/keyframes/west_of_house.png"
        save_manifest(manifest, scene.MANIFEST_FILE)
        scene._cache["mtime"] = None
        shown = scene.scene(Maze())
        self.assertEqual(len(shown["idle"]), 1)  # not on disk here, so it comes from the public media bucket
        self.assertEqual(shown["idle"][0], f"{scene.ASSET_URL}/assets/clips/west_of_house__idle_1__1.mp4")
        game = Maze()
        game.position = "NORTH-OF-HOUSE"
        self.assertEqual(scene.scene(game)["idle"], [])  # its second loop and this room's loops are not made

    def test_take_words_and_item_names_are_understood(self):
        game = Maze()
        game.item_open["MAILBOX"] = True
        game.take("ADVERTISEMENT")
        for command in ("take leaflet", "get the leaflet", "pick up leaflet", "take ADVERTISEMENT", "grab mail"):
            self.assertEqual(scene._take_verb(game, command), "take leaflet", command)
        self.assertIsNone(scene._take_verb(game, "open mailbox"))


if __name__ == "__main__":
    unittest.main()
