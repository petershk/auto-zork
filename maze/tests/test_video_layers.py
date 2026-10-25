import io
import struct
import tempfile
import unittest
import zlib
from pathlib import Path
from unittest.mock import patch

from maze import Maze
from video import gen_layers, review, scene
from video.graph import load_manifest, save_manifest, scenery
from video.layers import TROPHY_CASE, all_layers, displayable, merge_layers, on_display, placed, slots
from video.media_providers import has_alpha
from video.models import Keyframe, Manifest


def png(colour_type):
    header = struct.pack(">IIBBBBB", 1, 1, 8, colour_type, 0, 0, 0)
    chunk = b"IHDR" + header
    return b"\x89PNG\r\n\x1a\n" + struct.pack(">I", len(header)) + chunk + struct.pack(">I", zlib.crc32(chunk))


def cut_out(width=40, height=40, box=(10, 5, 20, 35)):
    """A real transparent PNG with one opaque rectangle, as background removal would return."""
    from PIL import Image

    image = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    image.paste((200, 150, 50, 255), box)
    out = io.BytesIO()
    image.save(out, "PNG")
    return out.getvalue()


class SlotTests(unittest.TestCase):
    def test_every_treasure_and_the_map_has_a_slot_inside_the_case(self):
        layout = slots()
        self.assertEqual(set(layout), set(displayable()))
        self.assertEqual(len(layout), 20)
        for item_id, (x, y, w, h) in layout.items():
            self.assertTrue(0 <= x and x + w <= 1 and 0 <= y and y + h <= 1, item_id)

    def test_slots_on_a_shelf_do_not_overlap_and_big_things_sit_low(self):
        shelves = {}
        for item_id, (x, y, w, h) in slots().items():
            shelves.setdefault(round(y, 6), []).append((x, x + w, item_id))
        self.assertEqual(len(shelves), 3)
        for row in shelves.values():
            row.sort()
            for (_, right, a), (left, _, b) in zip(row, row[1:]):
                self.assertLess(right, left, (a, b))
        bottom = shelves[max(shelves)]
        self.assertIn("COFFIN", [i for *_, i in bottom])
        self.assertEqual(sorted(bottom)[len(bottom) // 2][2], "COFFIN")  # the biggest thing is in the middle

    def test_placed_maps_a_slot_into_the_marked_region(self):
        x, y, w, h = slots()["COFFIN"]
        box = placed([0.5, 0.2, 0.9, 0.6], "COFFIN")
        self.assertAlmostEqual(box["left"], 100 * (0.5 + x * 0.4))
        self.assertAlmostEqual(box["width"], 100 * w * 0.4)
        self.assertAlmostEqual(box["top"], 100 * (0.2 + y * 0.4))


class StateTests(unittest.TestCase):
    def test_fixed_descriptions_follow_the_cyclops_and_the_leaves(self):
        w = Maze()
        self.assertIn("nailed-shut door", w.scenery_description("LIVING-ROOM"))
        w.flags |= {"CYCLOPS-FLAG"}
        self.assertIn("asleep", w.scenery_description("CYCLOPS-ROOM"))
        w.flags |= {"MAGIC-FLAG", "leaves"}
        self.assertIn("cyclops-sized opening", w.scenery_description("CYCLOPS-ROOM"))
        self.assertIn("cyclops-sized hole", w.scenery_description("LIVING-ROOM"))
        self.assertIn("metal grating", w.scenery_description("GRATING-CLEARING"))
        w.flags.add("GRATE")
        self.assertIn("open grating", w.scenery_description("GRATING-CLEARING"))

    def test_scenery_shows_open_fixtures_and_room_states_but_not_loose_items(self):
        w = Maze()
        plain = scenery(w, "LIVING-ROOM")
        w.item_locations["LAMP"] = "inventory"
        self.assertEqual(scenery(w, "LIVING-ROOM"), plain)  # taking the lamp does not change the picture
        w.item_open["TROPHY-CASE"] = True
        w.flags |= {"TRAP-DOOR"}
        self.assertIn("The trophy case is open.", scenery(w, "LIVING-ROOM"))
        self.assertIn("trap door stands open", scenery(w, "LIVING-ROOM"))
        w.item_locations["RAISED-BASKET"] = "LOWER-SHAFT"
        self.assertIn("lowered", scenery(w, "SHAFT-ROOM"))
        self.assertIn("rests at the bottom", scenery(w, "LOWER-SHAFT"))


class SceneLayerTests(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        path = Path(folder.name) / "manifest.json"
        base = Keyframe(id="living_room", room="LIVING-ROOM", room_name="Living Room", area="House", scenery="x",
                        image_path="assets/keyframes/living_room.png", status="generated",
                        layer_regions={TROPHY_CASE: [0.5, 0.2, 0.9, 0.6]})
        changed = Keyframe(id="living_room__abc", room="LIVING-ROOM", room_name="Living Room", area="House", scenery="y",
                           derived_from="living_room", image_path="assets/keyframes/living_room__abc.png")
        layers = all_layers()
        for layer in layers:
            layer.image_path = f"assets/layers/{layer.id}.png"
        save_manifest(Manifest(keyframes=[base, changed], layers=layers), path)
        for target, value in ((scene, "MANIFEST_FILE"), (scene, "load_manifest")):
            patcher = patch.object(target, value, path if value == "MANIFEST_FILE" else (lambda: load_manifest(path)))
            patcher.start()
            self.addCleanup(patcher.stop)
        scene._cache["mtime"] = None

    def test_treasures_in_the_case_are_layered_at_their_slots(self):
        game = Maze()
        game.position = "LIVING-ROOM"
        self.assertEqual(scene.scene(game)["layers"], [])
        game.item_locations["CHALICE"] = TROPHY_CASE
        game.item_locations["LAMP"] = TROPHY_CASE  # not a treasure: it has no slot and is not drawn
        shown = scene.scene(game)["layers"]
        self.assertEqual([s["id"] for s in shown], ["CHALICE"])
        self.assertEqual(shown[0]["src"], scene.asset_url("assets/layers/CHALICE.png"))  # from disk or the media bucket
        self.assertAlmostEqual(shown[0]["left"], placed([0.5, 0.2, 0.9, 0.6], "CHALICE")["left"])
        self.assertEqual(on_display(game), ["CHALICE"])

    def test_later_looks_use_the_region_marked_on_the_first(self):
        frames = {k.id: k for k in load_manifest(scene.MANIFEST_FILE).keyframes}
        self.assertEqual(scene.region(frames, "living_room__abc", TROPHY_CASE), [0.5, 0.2, 0.9, 0.6])
        self.assertIsNone(scene.region(frames, "kitchen", TROPHY_CASE))


class LayerGenerationTests(unittest.TestCase):
    def test_pictures_are_generated_then_cut_out(self):
        calls = []

        class Fake:
            price_per_image = 0.15

            def generate(self, prompt, *, aspect="16:9", seed=None, reference_images=()):
                calls.append(("generate", aspect))
                return b"plain"

            def remove_background(self, image):
                calls.append(("cut", image.name))
                return cut_out()

        manifest = Manifest(layers=all_layers())
        written = {}
        todo = gen_layers.todo(manifest, "CHALICE")
        failures = gen_layers.run(manifest, Fake(), todo, 100, write=lambda p, d: written.update({p.name: d}), log=lambda _: None)
        self.assertEqual(failures, 0)
        self.assertEqual(calls, [("generate", "1:1"), ("cut", "CHALICE.png")])
        self.assertTrue(has_alpha(written["CHALICE.png"]))
        from PIL import Image
        self.assertEqual(Image.open(io.BytesIO(written["CHALICE.png"])).size, (10, 30))  # cut to the object
        chalice = next(x for x in manifest.layers if x.id == "CHALICE")
        self.assertEqual((chalice.status, chalice.image_path), ("generated", "assets/layers/CHALICE.png"))
        self.assertAlmostEqual(manifest.spent_usd, 0.16)
        self.assertEqual(merge_layers(manifest.layers)[[x.id for x in manifest.layers].index("CHALICE")].status, "generated")

    def test_has_alpha(self):
        self.assertTrue(has_alpha(png(6)))
        self.assertFalse(has_alpha(png(2)))
        self.assertFalse(has_alpha(b"not a png"))


class RegionEndpointTests(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.path = Path(folder.name) / "manifest.json"
        base = Keyframe(id="living_room", room="LIVING-ROOM", room_name="Living Room", area="House", scenery="x",
                        image_path="assets/keyframes/living_room.png", status="generated")
        save_manifest(Manifest(keyframes=[base], layers=all_layers()), self.path)
        for name, value in (("load_manifest", lambda: load_manifest(self.path)),
                            ("save_manifest", lambda m: save_manifest(m, self.path))):
            patcher = patch.object(review, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        self.client = review.app.test_client()

    def test_mark_shelves_and_preview_slots(self):
        html = self.client.get("/").get_data(as_text=True)
        self.assertIn("Mark trophy case shelves", html)
        bad = self.client.post("/api/keyframes/living_room/region", json={"fixture": TROPHY_CASE, "region": [0.5, 0.5, 0.5, 0.5]})
        self.assertEqual(bad.status_code, 400)
        ok = self.client.post("/api/keyframes/living_room/region", json={"fixture": TROPHY_CASE, "region": [0.5, 0.2, 0.9, 0.6]})
        self.assertEqual(ok.status_code, 200)
        self.assertEqual(load_manifest(self.path).keyframes[0].layer_regions[TROPHY_CASE], [0.5, 0.2, 0.9, 0.6])
        html = self.client.get("/").get_data(as_text=True)
        self.assertIn(">COFFIN</div>", html)  # an empty slot is outlined until its picture exists


if __name__ == "__main__":
    unittest.main()
