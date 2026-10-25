"""Generate video clips: each one starts on its edge's first keyframe and ends on its last (first-and-last-frame).

Only the edges you name are made, after showing the cost and asking (or --yes). Anything generated or approved is
skipped. Both keyframes of an edge need pictures and the edge needs a motion prompt (video.embellish). From maze/:
  uv run python -m video.gen_clips --only west_of_house__to__north_of_house --dry-run
  uv run python -m video.gen_clips --only west_of_house__to__north_of_house,west_of_house__9a9c97__take_leaflet
An --only entry is an exact edge id, or, when nothing has that id, every edge whose id starts with it; --only @file
reads the entries from a file, one per line.
"""
from __future__ import annotations

import argparse
import sys
import threading
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor

from video.config import STYLE, VIEWER, load_config
from video.gen_keyframes import VIDEO_DIR, has_image, write_file
from video.graph import load_manifest, save_manifest
from video.media_providers import FalVideoProvider
from video.normalize import normalize_clip

CLIP_DIR = "assets/clips"


def select(manifest, only):
    """The edges named by --only (exact ids, else prefixes), in manifest order."""
    ids = {e.id for e in manifest.actions}
    chosen, unknown = set(), []
    for entry in only:
        found = {entry} if entry in ids else {i for i in ids if i.startswith(entry)}
        chosen |= found
        if not found:
            unknown.append(entry)
    return [e for e in manifest.actions if e.id in chosen], unknown


def problems(edge, frames):
    """Why an edge cannot be made yet, or None."""
    missing = [f for f in dict.fromkeys([edge.start_frame, *edge.via, edge.end_frame]) if not has_image(frames[f])]
    if missing:
        return f"no picture yet for {', '.join(missing)}"
    if not edge.motion_prompt:
        return "no motion prompt: run video.embellish"
    return None


def clip_seconds(edge, provider):
    return provider.duration(edge.duration_s)


def run(manifest, provider, edges, price, max_spend, workers=3, save=None, write=write_file, log=print, after=None):
    """Make every pending clip of the edges without passing the budget; returns the number not made. `after(edge, clip)`
    runs in the worker as soon as a clip is saved (main uses it to normalize, so that happens while others render)."""
    frames = {k.id: k for k in manifest.keyframes}
    lock, failures = threading.Lock(), 0
    work = [(e, n, clip) for e in edges for n, clip in enumerate(e.clips, 1) if clip.status in ("pending", "rejected")]

    def one(job):
        nonlocal failures
        edge, n, clip = job
        cost = clip_seconds(edge, provider) * price(edge.audio)
        with lock:
            if manifest.spent_usd + cost > max_spend:
                log(f"skipped {edge.id}: MAX_SPEND_USD would be passed")
                failures += 1
                return
            manifest.spent_usd += cost  # counted up front: a failed call may still be billed
        try:
            data, seed = provider.first_last_frame(
                first=VIDEO_DIR / frames[clip.from_].image_path, last=VIDEO_DIR / frames[clip.to].image_path,
                prompt=f"{edge.motion_prompt} {VIEWER} {STYLE}.", duration_s=edge.duration_s, audio=edge.audio)
            path = f"{CLIP_DIR}/{edge.id}__{n}.mp4"
            write(VIDEO_DIR / path, data)
            # A remade clip's old original would otherwise be cropped back over the new download (normalize.py).
            (VIDEO_DIR / path).with_suffix(".orig.mp4").unlink(missing_ok=True)
            clip.path, clip.seed, clip.status = path, seed, "generated"
            clip.first_frame_score = clip.last_frame_score = None
            log(f"made {edge.id} ({clip_seconds(edge, provider)} s)")
            if after:
                after(edge, clip)
        except Exception as exc:  # noqa: BLE001 - report and carry on; a rerun picks it up
            with lock:
                failures += 1
            log(f"failed {edge.id}: {exc}")
        if save:
            with lock:
                save(manifest)

    with ThreadPoolExecutor(max_workers=workers) as pool:
        list(pool.map(one, work))
    return failures


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--only", required=True, help="comma-separated edge ids or id prefixes, or @file with one per line")
    parser.add_argument("--yes", action="store_true", help="do not ask before spending")
    parser.add_argument("--dry-run", action="store_true", help="list what would be made, call nothing")
    args = parser.parse_args(argv)
    config = load_config()
    manifest = load_manifest()
    frames = {k.id: k for k in manifest.keyframes}
    only = Path(args.only[1:]).read_text(encoding="utf-8").split() if args.only.startswith("@") else args.only.split(",")
    edges, unknown = select(manifest, [x.strip() for x in only if x.strip()])
    if unknown:
        print(f"No edge matches: {', '.join(unknown)}")
    ready = []
    for e in edges:
        why = problems(e, frames)
        pending = sum(c.status in ("pending", "rejected") for c in e.clips)
        if why:
            print(f"  cannot make {e.id}: {why}")
        elif pending:
            ready.append(e)
    provider = FalVideoProvider(config.video_model)
    seconds = sum(clip_seconds(e, provider) for e in ready for c in e.clips if c.status in ("pending", "rejected"))
    cost = sum(clip_seconds(e, provider) * config.price_per_second(e.audio)
               for e in ready for c in e.clips if c.status in ("pending", "rejected"))
    for e in ready:
        print(f"  {e.id:<52} {clip_seconds(e, provider)} s  {'audio' if e.audio else 'silent'}  {e.motion_prompt}")
    if not ready:
        print("Nothing to make.")
        return 0
    print(f"{len(ready)} clips, {seconds} s of video with {config.video_model}, about ${cost:.2f}. "
          f"Spent so far ${manifest.spent_usd:.2f} of MAX_SPEND_USD=${config.max_spend_usd:.2f}.")
    if args.dry_run:
        return 0
    if not args.yes and input("Generate them? [y/N] ").strip().lower() != "y":
        print("Nothing spent.")
        return 1
    def normalize(edge, clip):  # crop each new clip to the view the player shows (see normalize.py)
        box, measured = normalize_clip(clip, frames)
        clip.fit, clip.normalized = list(measured), bool(box)
        if not box:
            print(f"  {edge.id} is framed too tightly to line up with the stills: make it again")

    failures = run(manifest, provider, ready, config.price_per_second, config.max_spend_usd,
                   workers=config.clip_concurrency, save=save_manifest, after=normalize)
    save_manifest(manifest)
    print(f"Done: {len(ready) - failures} made, {failures} not made. Spent ${manifest.spent_usd:.2f} in total. "
          "Check the seams with: uv run python -m video.check_seams")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
