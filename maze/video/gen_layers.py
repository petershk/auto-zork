"""Generate the transparent treasure pictures that are layered into the trophy case.

Each one is made from a text prompt on a plain background, then its background is removed and the picture is cut to
the object's outline, so it fills its slot. Anything generated or approved is skipped. Run from the maze folder after
video.expand_actions:
  uv run python -m video.gen_layers            # shows the cost and asks first
  uv run python -m video.gen_layers --yes --only CHALICE
  uv run python -m video.gen_layers --retrim   # cut existing pictures to their outline again; costs nothing
"""
from __future__ import annotations

import argparse
import io
import sys
import tempfile
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from video.config import load_config
from video.gen_keyframes import VIDEO_DIR, write_file
from video.graph import load_manifest, save_manifest
from video.media_providers import BACKGROUND_REMOVAL, FalImageProvider

LAYER_DIR = "assets/layers"
MAX_SIDE = 640        # a shelf slot is never bigger than this on screen


def trim(png: bytes) -> bytes:
    """The picture cut to its visible pixels (ignoring the faint halo background removal leaves) and scaled down."""
    from PIL import Image

    image = Image.open(io.BytesIO(png)).convert("RGBA")
    box = image.getchannel("A").point(lambda a: 255 if a > 24 else 0).getbbox()
    if box:
        image = image.crop(box)
    image.thumbnail((MAX_SIDE, MAX_SIDE))
    out = io.BytesIO()
    image.save(out, "PNG", optimize=True)
    return out.getvalue()


def todo(manifest, only=None):
    return [layer for layer in manifest.layers if layer.status in ("pending", "rejected") and (not only or only == layer.id)]


def price(provider):
    return provider.price_per_image + BACKGROUND_REMOVAL[1]


def make(provider, layer, write):
    with tempfile.TemporaryDirectory() as folder:
        plain = Path(folder) / f"{layer.id}.png"
        plain.write_bytes(provider.generate(layer.prompt, aspect="1:1"))
        data = trim(provider.remove_background(plain))
    path = f"{LAYER_DIR}/{layer.id}.png"
    write(VIDEO_DIR / path, data)
    layer.image_path, layer.status = path, "generated"


def run(manifest, provider, layers, max_spend, workers=4, save=None, write=write_file, log=print):
    """Make the layers without passing the budget; returns the number not made."""
    lock, failures = threading.Lock(), 0

    def one(layer):
        nonlocal failures
        with lock:
            if manifest.spent_usd + price(provider) > max_spend:
                log(f"skipped {layer.id}: MAX_SPEND_USD would be passed")
                failures += 1
                return
            manifest.spent_usd += price(provider)  # counted up front: a failed call may still be billed
        try:
            make(provider, layer, write)
            log(f"made {layer.id}")
        except Exception as exc:  # noqa: BLE001 - report and carry on; a rerun picks it up
            with lock:
                failures += 1
            log(f"failed {layer.id}: {exc}")
        if save:
            with lock:
                save(manifest)

    with ThreadPoolExecutor(max_workers=workers) as pool:
        list(pool.map(one, layers))
    return failures


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--only", help="only this item id, e.g. CHALICE")
    parser.add_argument("--yes", action="store_true", help="do not ask before spending")
    parser.add_argument("--retrim", action="store_true", help="cut existing pictures to their outline again")
    args = parser.parse_args(argv)
    config = load_config()
    manifest = load_manifest()
    if args.retrim:
        done = [layer for layer in manifest.layers if layer.image_path and (VIDEO_DIR / layer.image_path).exists()]
        for layer in done:
            path = VIDEO_DIR / layer.image_path
            path.write_bytes(trim(path.read_bytes()))
        print(f"Trimmed {len(done)} pictures.")
        return 0
    layers = todo(manifest, args.only)
    if not layers:
        print("Nothing to make: every layer is generated or approved." if manifest.layers else
              "No layers in manifest.json: run uv run python -m video.expand_actions first.")
        return 0
    provider = FalImageProvider(config.image_model)
    print(f"{len(layers)} treasure pictures to make, about ${len(layers) * price(provider):.2f}. "
          f"Spent so far ${manifest.spent_usd:.2f} of MAX_SPEND_USD=${config.max_spend_usd:.2f}.")
    if not args.yes and input("Generate them? [y/N] ").strip().lower() != "y":
        print("Nothing spent.")
        return 1
    failures = run(manifest, provider, layers, config.max_spend_usd, save=save_manifest)
    save_manifest(manifest)
    print(f"Done: {len(layers) - failures} made, {failures} not made. Spent ${manifest.spent_usd:.2f} in total.")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
