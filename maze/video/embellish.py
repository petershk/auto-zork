"""Write the image and motion prompts by embellishing the game's own room descriptions with an LLM.

A room's first state gets an image prompt; its later states get an edit instruction against the first, so the
room keeps its shape. Every keyframe gets an idle prompt and every edge a motion prompt. Results go into
manifest.json; items that already have prompts are skipped unless --force. Run from the maze folder:
  uv run python -m video.embellish --dry-run     # show the first request, call nothing
  uv run python -m video.embellish --only living_room
Uses LLM_PROVIDER (default anthropic) and LLM_MODEL, with that provider's key from maze/.env.
"""
from __future__ import annotations

import argparse
import json
import sys
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed

from pydantic import BaseModel, ValidationError

from maze import Maze
from providers import find_api_key, provider_for
from video.config import SCENE_STYLE, VIEWER, load_config
from video.graph import fixtures_here, load_manifest, save_manifest, underground_rooms
from video.layers import LAYERED_FIXTURES

SYSTEM = f"""You write prompts for an image model and a first-and-last-frame video model that illustrate a text adventure, \
one scene at a time. Views are first-person at eye level, 16:9, with no people and no text in the picture.
Keep every fixed object and exit the game's description mentions, and do not add important objects it does not; you may \
add atmosphere, materials, light, weather and small details. Never show loose, portable things (lamps, lanterns, weapons, \
tools, food, bottles, treasures, books): players pick those up, so the scene shows only the room and its fixed objects. \
Above ground: muted greens and greys, soft overcast light. Indoors and underground: muted browns and greys, dim warm light, \
unless the description suggests otherwise.
{VIEWER} Never mention them.
Do not add style words: this suffix is appended to every prompt later: "{SCENE_STYLE}".
Reply with one JSON object and nothing else."""


class BasePrompts(BaseModel):
    image_prompt: str
    idle_prompt: str


class VariantPrompts(BaseModel):
    edit_prompt: str
    idle_prompt: str


class MotionPrompt(BaseModel):
    motion_prompt: str


IDLE_RULE = ('"idle_prompt": small ambient motion only for a 5 second loop that starts and ends on this exact image, '
             'ending with "The camera is almost still."')


def layered_notes(room):
    """What a room's picture must show so objects can be layered into its fixtures (the empty trophy case)."""
    return " ".join(f["note"] for f in LAYERED_FIXTURES.values() if f["room"] == room)


def base_request(k, exits, closed=()):
    notes = layered_notes(k.room)
    where = "underground" if k.room in underground_rooms() else "above ground"
    return (f"Room: {k.room_name} (area: {k.area}; {where})\nGame description: {k.scenery}\n"
            f"Fixed objects here: {', '.join(k.fixtures) or 'none'}\nExits: {exits or 'none'}\n"
            + (f"Shown firmly closed: {', '.join(closed)}\n" if closed else "")
            + (f"Must be shown exactly so: {notes}\n" if notes else "") + "\n"
            'Return {"image_prompt": ..., "idle_prompt": ...}. "image_prompt": a vivid, concrete description of what the '
            f"player sees, 60 to 110 words, beginning \"First-person view at eye level, 16:9.\" {IDLE_RULE}", BasePrompts)


def variant_request(k, base):
    return (f"Room: {k.room_name}\nThe existing image shows: {base.scenery}\nThe room now looks like this: {k.scenery}\n\n"
            'Return {"edit_prompt": ..., "idle_prompt": ...}. "edit_prompt": an instruction for an image editing model, '
            'beginning "Same image;", describing only what changed, ending "Change nothing else." ' + IDLE_RULE, VariantPrompts)


def motion_request(e, start, end):
    if e.cosmetic:
        return (f"Scene: {start.room_name}. {start.scenery}\nThe player typed: {', '.join(e.verbs)}\n\n"
                'Return {"motion_prompt": ...}: one or two sentences of camera motion for a 5 second clip that starts '
                "and ends on exactly this image, so nothing in the scene may change and nothing appears or disappears. "
                "Suggest the action by the camera alone (for example it leans in towards the object, pauses, and "
                "settles back to where it started).", MotionPrompt)
    how = ("The player moved by typing: " if e.type == "move" else "The player typed: ") + ", ".join(e.verbs)
    return (f"First frame: {start.room_name}. {start.scenery}\nLast frame: {end.room_name}. {end.scenery}\n{how}\n\n"
            'Return {"motion_prompt": ...}: one or two sentences describing only the motion between the first and the '
            "last frame, from the camera's point of view (for example \"The camera turns left and walks through the "
            'doorway into ..."). Up and down mean stairs, ladders, chimneys or slopes as the scenes suggest.', MotionPrompt)


def parse_reply(text, model):
    """The JSON object in a reply (models sometimes wrap it in a code fence), validated."""
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end < start:
        raise ValueError(f"No JSON object in reply: {text[:200]!r}")
    return model.model_validate(json.loads(text[start:end + 1]))


class Embellisher:
    def __init__(self, client, model):
        self.client, self.model = client, model

    def ask(self, request):
        prompt, schema = request
        for attempt in range(2):
            completion = self.client.chat.completions.create(
                model=self.model, max_tokens=2000,
                messages=[{"role": "system", "content": SYSTEM}, {"role": "user", "content": prompt}])
            choice = completion.choices[0]
            try:
                return parse_reply(choice.message.content or "", schema)
            except (ValueError, ValidationError) as exc:
                if attempt:
                    raise ValueError(f"{exc} (finish reason: {getattr(choice, 'finish_reason', None)})") from None


def make_client(provider_id):
    from openai import OpenAI
    provider = provider_for(provider_id)
    key = find_api_key(provider_id)
    if not key:
        raise SystemExit(f"No API key for {provider.label}: set {provider.key_env[0] if provider.key_env else 'MAZE_AGENT_API_KEY'} in maze/.env.")
    return OpenAI(api_key=key, base_url=provider.base_url)


def jobs(manifest, force=False, only=None):
    """(kind, item, request) for everything that still needs prompts."""
    game = Maze()
    frames = {k.id: k for k in manifest.keyframes}
    out = []
    for k in manifest.keyframes:
        if only and only not in k.id or not force and (k.image_prompt or k.edit_prompt):
            continue
        if k.derived_from:
            out.append(("variant", k, variant_request(k, frames[k.derived_from])))
        else:
            game.position = k.room  # a room's first state is how it looks at the start, so list only exits open then
            exits = "; ".join(f"{d}: {game.rooms[game.rooms[k.room]['exits'][d]]['name']}" for d in game.exits())
            closed = [game.items[i]["name"] for i in fixtures_here(game, k.room) if not game.item_open[i]]
            out.append(("base", k, base_request(k, exits, closed)))
    for e in manifest.actions:
        if e.type in ("move", "action", "dynamic") and (force or not e.motion_prompt) and (not only or only in e.id):
            out.append(("motion", e, motion_request(e, frames[e.start_frame], frames[e.end_frame])))
    return out


def apply(kind, item, result):
    if kind == "motion":
        item.motion_prompt = result.motion_prompt
        item.clips = [c.model_copy(update={"status": "pending", "path": None}) for c in item.clips]
        return
    if kind == "base":
        item.image_prompt = result.image_prompt
    else:
        item.edit_prompt = result.edit_prompt
    item.idle_prompt = result.idle_prompt
    item.status, item.image_path = "pending", None


def sync_idles(manifest):
    """Idle loops use their keyframe's idle prompt; a changed prompt means the loop must be made again."""
    prompts = {k.id: k.idle_prompt for k in manifest.keyframes}
    for e in manifest.actions:
        if e.type == "idle" and e.motion_prompt != prompts[e.start_frame]:
            e.motion_prompt = prompts[e.start_frame]
            e.clips = [c.model_copy(update={"status": "pending", "path": None}) for c in e.clips]


def run(manifest, embellisher, todo, workers=4, save=None, log=print):
    """Ask for every job, applying results as they arrive; returns the number of failures."""
    lock, failures, done = threading.Lock(), 0, 0
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {pool.submit(embellisher.ask, request): (kind, item) for kind, item, request in todo}
        for future in as_completed(futures):
            kind, item = futures[future]
            try:
                result = future.result()
            except Exception as exc:  # noqa: BLE001 - report and carry on; a rerun picks it up
                failures += 1
                log(f"failed {item.id}: {exc}")
                continue
            with lock:
                apply(kind, item, result)
                done += 1
                if save and done % 10 == 0:
                    sync_idles(manifest)
                    save(manifest)
            log(f"[{done}/{len(todo)}] {kind} {item.id}")
    sync_idles(manifest)
    if save:
        save(manifest)
    return failures


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--only", help="only keyframes and edges whose id contains this text")
    parser.add_argument("--force", action="store_true", help="rewrite prompts that already exist")
    parser.add_argument("--dry-run", action="store_true", help="print the first request and the job count, call nothing")
    parser.add_argument("--workers", type=int, default=4)
    args = parser.parse_args(argv)
    config = load_config()
    manifest = load_manifest()
    if not manifest.keyframes:
        raise SystemExit("manifest.json is empty: run uv run python -m video.expand_actions first.")
    todo = jobs(manifest, args.force, args.only)
    counts = {kind: sum(j[0] == kind for j in todo) for kind in ("base", "variant", "motion")}
    print(f"{len(todo)} requests to {config.llm_provider}/{config.llm_model}: {counts['base']} room images, "
          f"{counts['variant']} state edits, {counts['motion']} motion prompts")
    if args.dry_run or not todo:
        if todo:
            print("\nSYSTEM:\n" + SYSTEM + "\n\nUSER:\n" + todo[0][2][0])
        return 0
    failures = run(manifest, Embellisher(make_client(config.llm_provider), config.llm_model), todo, args.workers, save_manifest)
    print(f"Done: {len(todo) - failures} written, {failures} failed" + (" (run again to retry them)" if failures else ""))
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
