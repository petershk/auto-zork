"""Derive the clip graph from the game, print it with a cost estimate, and update manifest.json.

Makes no API calls. Run from the maze folder:
  uv run python -m video.expand_actions                    # summary and cost
  uv run python -m video.expand_actions --list             # every edge too
  uv run python -m video.expand_actions --rooms "Forest And Outside Of House,House"
--rooms (or VIDEO_ROOMS in .env) limits the graph to engine room ids or area names; by default every room is filmed.
"""
from __future__ import annotations

import argparse
import sys

from video.config import load_config
from video.graph import MANIFEST_FILE, derive, in_scope, load_manifest, merge_manifest, save_manifest


def estimate(edges, config, statuses=("pending", "rejected")):
    """(clip count, seconds, USD) for the clips that still need generating."""
    clips = [(e, c) for e in edges for c in e.clips if c.status in statuses]
    seconds = sum(e.duration_s for e, _ in clips)
    cost = sum(e.duration_s * config.price_per_second(e.audio) for e, _ in clips)
    return len(clips), seconds, cost


def summary(keyframes, edges):
    counts = {t: sum(e.type == t for e in edges) for t in ("move", "action", "idle")}
    variants = sum(k.derived_from is not None for k in keyframes)
    return (f"{len(keyframes)} keyframes ({variants} state variants) in {len({k.room for k in keyframes})} rooms; "
            f"{len(edges)} edges ({counts['move']} moves, {counts['action']} state changes, {counts['idle']} idle loops)")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--rooms", help="comma-separated engine room ids or area names to film (overrides VIDEO_ROOMS)")
    parser.add_argument("--list", action="store_true", help="print every edge")
    args = parser.parse_args(argv)
    config = load_config()
    rooms = {r.strip().upper() for r in args.rooms.split(",") if r.strip()} if args.rooms else config.rooms

    everything, all_edges, unreached = derive(config.provider)
    keyframes, edges, _ = derive(config.provider, rooms) if rooms else (everything, all_edges, unreached)
    if rooms and (unknown := {r for r in rooms if not any(in_scope(k, {r}) for k in everything)}):
        print(f"Not rooms or areas the game reaches: {', '.join(sorted(unknown))}")
    manifest = merge_manifest(load_manifest(), keyframes, edges)

    if args.list:
        for e in manifest.actions:
            print(f"{e.type:<6} {e.id:<64} {e.duration_s}s  {', '.join(e.verbs)}")
        print()
    print("Whole game: " + summary(everything, all_edges))
    if unreached:
        print(f"Rooms the derivation never reached (no video): {', '.join(unreached)}")
    _, seconds, cost = estimate(all_edges, config)
    print(f"  {seconds} s of video, about ${cost:.2f} with {config.video_model}")
    if rooms:
        print("Selected rooms: " + summary(keyframes, manifest.actions))
    clips, seconds, cost = estimate(manifest.actions, config)
    print(f"Still to generate: {clips} clips, {seconds} s of video. Estimated cost with {config.video_model}: ${cost:.2f}")
    remaining = config.max_spend_usd - manifest.spent_usd
    print(f"Spent so far ${manifest.spent_usd:.2f}; budget MAX_SPEND_USD=${config.max_spend_usd:.2f}, ${remaining:.2f} left."
          + ("" if cost <= remaining else "  The estimate is over the remaining budget."))
    missing = sum(k.image_prompt is None and k.edit_prompt is None for k in manifest.keyframes)
    if missing:
        print(f"{missing} keyframes have no prompt yet: run uv run python -m video.embellish")

    save_manifest(manifest)
    print(f"Wrote {MANIFEST_FILE.name}: {len(manifest.keyframes)} keyframes, {len(manifest.actions)} edges.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
