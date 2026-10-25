import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from video import gen_keyframes, review
from video.config import SCENE_STYLE
from video.graph import derive, load_manifest, merge_manifest, save_manifest
from video.models import Manifest


class FakeImages:
    price_per_image = 0.15

    def __init__(self):
        self.calls = []

    def generate(self, prompt, *, aspect="16:9", seed=None, reference_images=()):
        self.calls.append(("generate", prompt, [p.name for p in reference_images]))
        return b"png"

    def edit(self, base, instruction, *, seed=None):
        self.calls.append(("edit", instruction, [base.name]))
        return b"png"


def house_manifest():
    keyframes, edges, _ = derive("fal:test", {"WEST-OF-HOUSE", "EAST-OF-HOUSE", "NORTH-OF-HOUSE"})
    manifest = merge_manifest(Manifest(), keyframes, edges)
    for k in manifest.keyframes:
        if k.derived_from:
            k.edit_prompt = "Same image; the window is open. Change nothing else."
        else:
            k.image_prompt = f"First-person view of {k.room_name}."
    return manifest


class KeyframeGenerationTests(unittest.TestCase):
    def test_rooms_from_text_alone_then_edits(self):
        manifest, images, written = house_manifest(), FakeImages(), {}
        failures = gen_keyframes.run(manifest, images, gen_keyframes.todo(manifest), 100, workers=1,
                                     write=lambda path, data: written.update({path.name: data}), log=lambda _: None)
        self.assertEqual(failures, 0)
        kinds = [(c[0], c[2]) for c in images.calls]
        self.assertEqual([k for k in kinds if k[0] == "generate"], [("generate", [])] * 3)  # never from another room's picture
        self.assertEqual(kinds[-1], ("edit", ["east_of_house.png"]))
        self.assertTrue(images.calls[0][1].endswith(SCENE_STYLE + "."))
        self.assertTrue(all(k.status == "generated" and k.image_path for k in manifest.keyframes))
        self.assertAlmostEqual(manifest.spent_usd, 0.15 * len(manifest.keyframes))
        self.assertEqual(gen_keyframes.todo(manifest), [])

    def test_budget_and_missing_parents_stop_generation(self):
        manifest, images = house_manifest(), FakeImages()
        failures = gen_keyframes.run(manifest, images, gen_keyframes.todo(manifest), 0.2, workers=1,
                                     write=lambda *_: None, log=lambda _: None)
        self.assertEqual(len(images.calls), 1)
        self.assertEqual(failures, len(manifest.keyframes) - 1)
        self.assertLessEqual(manifest.spent_usd, 0.2)


class ReviewPageTests(unittest.TestCase):
    def setUp(self):
        self.manifest = house_manifest()
        self.manifest.keyframes[0].image_path, self.manifest.keyframes[0].status = "assets/keyframes/x.png", "generated"
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.path = Path(folder.name) / "manifest.json"
        save_manifest(self.manifest, self.path)
        for name, value in (("load_manifest", lambda: load_manifest(self.path)),
                            ("save_manifest", lambda m: save_manifest(m, self.path))):
            patcher = patch.object(review, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        self.client = review.app.test_client()

    def test_page_shows_activities_as_start_and_end_frames(self):
        html = self.client.get("/").get_data(as_text=True)
        self.assertIn("open window", html)
        self.assertIn("/assets/keyframes/x.png", html)

    def test_approve_and_reject(self):
        first, second = self.manifest.keyframes[0].id, self.manifest.keyframes[1].id
        self.assertEqual(self.client.post(f"/api/keyframes/{first}", json={"status": "approved"}).status_code, 200)
        self.assertEqual(self.client.post(f"/api/keyframes/{second}", json={"status": "approved"}).status_code, 400)
        self.assertEqual(self.client.post(f"/api/keyframes/{second}", json={"status": "rejected"}).status_code, 200)
        statuses = {k.id: k.status for k in review.load_manifest().keyframes}
        self.assertEqual((statuses[first], statuses[second]), ("approved", "rejected"))


if __name__ == "__main__":
    unittest.main()
