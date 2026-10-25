"""Generate keyframe images (the start and end frames of every clip) from the prompts in manifest.json.

Order matters: a room's first state comes from its text prompt alone (SCENE_STYLE keeps every room in one look;
reference images are not used, because the model copies their buildings and objects), and later states are edits of
the first. Anything generated or approved is skipped;
pending and rejected keyframes are made. Run from the maze folder after video.expand_actions and video.embellish:
  uv run python -m video.gen_keyframes            # shows the cost and asks first
  uv run python -m video.gen_keyframes --yes --only east_of_house
"""
from __future__ import annotations

import argparse
import sys
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from video.config import SCENE_STYLE, VIEWER, load_config
from video.graph import load_manifest, save_manifest
from video.media_providers import FalImageProvider

VIDEO_DIR = Path(__file__).parent
KEYFRAME_DIR = "assets/keyframes"


def todo(manifest, only=None):
    """Keyframes to make: pending or rejected ones, plus every look edited from one of those (an edit of an old
    picture would no longer match its remade parent)."""
    chosen = {k.id for k in manifest.keyframes if k.status in ("pending", "rejected") and (not only or only in k.id)}
    grew = True
    while grew:
        before = len(chosen)
        chosen |= {k.id for k in manifest.keyframes if k.derived_from in chosen}
        grew = len(chosen) > before
    return [k for k in manifest.keyframes if k.id in chosen]


def versioned(path: str) -> str:
    """An asset path with its file's modification time attached, so browsers reload a picture that was remade."""
    try:
        return f"{path}?v={int((VIDEO_DIR / path).stat().st_mtime)}"
    except OSError:
        return path


def has_image(k):
    return bool(k.image_path) and k.status in ("generated", "approved")


def make(provider, k, frames, write):
    if k.derived_from:
        parent = frames[k.derived_from]
        if not has_image(parent):
            raise RuntimeError(f"its first state {parent.id} has no image yet")
        data = provider.edit(VIDEO_DIR / parent.image_path, k.edit_prompt)
    else:
        if not k.image_prompt:
            raise RuntimeError("no image prompt: run video.embellish")
        data = provider.generate(f"{k.image_prompt} {VIEWER} {SCENE_STYLE}.")
    path = f"{KEYFRAME_DIR}/{k.id}.png"
    write(VIDEO_DIR / path, data)
    k.image_path, k.status = path, "generated"


def write_file(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)


def run(manifest, provider, keyframes, max_spend, workers=4, save=None, write=write_file, log=print):
    """Make keyframes wave by wave, each wave being those whose parent has a picture by then, without
    passing the budget; returns the number not made."""
    frames = {k.id: k for k in manifest.keyframes}
    lock, failures = threading.Lock(), 0
    for k in keyframes:
        k.status = "pending"  # being remade: its old picture must not be edited from by its own children

    def ready(k):
        return not k.derived_from or has_image(frames[k.derived_from])

    def one(k):
        nonlocal failures
        with lock:
            if manifest.spent_usd + provider.price_per_image > max_spend:
                log(f"skipped {k.id}: MAX_SPEND_USD would be passed")
                failures += 1
                return
            manifest.spent_usd += provider.price_per_image  # counted up front: a failed call may still be billed
        try:
            make(provider, k, frames, write)
            log(f"made {k.id}")
        except Exception as exc:  # noqa: BLE001 - report and carry on; a rerun picks it up
            with lock:
                failures += 1
            log(f"failed {k.id}: {exc}")
        if save:
            with lock:
                save(manifest)

    waiting = list(keyframes)
    while waiting:
        wave = [k for k in waiting if ready(k)]
        if not wave:
            for k in waiting:
                log(f"not made {k.id}: {k.derived_from} has no picture")
            return failures + len(waiting)
        made = {k.id for k in wave}
        waiting = [k for k in waiting if k.id not in made]
        with ThreadPoolExecutor(max_workers=workers) as pool:
            list(pool.map(one, wave))
    return failures


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--only", help="only keyframes whose id contains this text")
    parser.add_argument("--yes", action="store_true", help="do not ask before spending")
    parser.add_argument("--dry-run", action="store_true", help="list what would be made, call nothing")
    args = parser.parse_args(argv)
    config = load_config()
    manifest = load_manifest()
    provider = FalImageProvider(config.image_model) if not args.dry_run else None
    keyframes = todo(manifest, args.only)
    if not keyframes:
        print("Nothing to make: every keyframe is generated or approved.")
        return 0
    price = provider.price_per_image if provider else 0.15
    cost = len(keyframes) * price
    print(f"{len(keyframes)} keyframes to make with {config.image_model}, about ${cost:.2f}. "
          f"Spent so far ${manifest.spent_usd:.2f} of MAX_SPEND_USD=${config.max_spend_usd:.2f}.")
    for k in keyframes:
        print(f"  {k.id:<32} " + (f"edit of {k.derived_from}" if k.derived_from else
                                    "from text"))
    if args.dry_run:
        return 0
    if not args.yes and input("Generate them? [y/N] ").strip().lower() != "y":
        print("Nothing spent.")
        return 1
    failures = run(manifest, provider, keyframes, config.max_spend_usd, save=save_manifest)
    save_manifest(manifest)
    print(f"Done: {len(keyframes) - failures} made, {failures} not made. Spent ${manifest.spent_usd:.2f} in total. "
          "Review them with: uv run python -m video.review")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
