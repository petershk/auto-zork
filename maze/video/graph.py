"""The clip graph, derived from the game: keyframes are rooms as the engine describes them, edges are commands.

A keyframe is the room's description plus what is visibly open or changed there (see scenery()). derive() replays
the scripted perfect run (walkthrough.py). At each new look of the world it also tries, on copies of the game,
every move reachable from where the player stands and everything that changes how a room looks (opening the
mailbox, the trophy case, the trap door ...), so the graph holds every room change and every visible state change
around that route. The engine stays the source of truth at runtime: a turn's clip is the edge from the keyframe
before the command to the keyframe after it.
"""
from __future__ import annotations

import copy
import functools
import hashlib
import json
from pathlib import Path

from autoplay import apply_step, command_text, start
from maze import Maze
from puzzles import SCENES
from video.layers import merge_layers
from video.models import Action, Clip, Keyframe, Manifest
from walkthrough import STEPS

DATA = Path(__file__).with_name("data")
MANIFEST_FILE = DATA / "manifest.json"
MOVE_SECONDS, ACTION_SECONDS, IDLE_SECONDS, IDLE_VARIANTS = 6, 5, 5, 2


def slug(room: str) -> str:
    return room.lower().replace("-", "_")


def _copy(game):
    """A copy of the game to try something on. The room and item definitions and the map layout never change after
    the game is built, so copies share them (copying them made deriving the graph about five times slower)."""
    shared = (game.rooms, game.items, game.coordinates, game.levels)
    return copy.deepcopy(game, {id(data): data for data in shared})


def _flag(name):
    return lambda game: name in game.flags


# What changes how a room looks without changing the game's description of it: room -> [(test, sentence)].
# Loose items, the thief and short-lived effects (the hot bell, burning candles) are deliberately not here:
# keyframes never show takeable items, and the rest are cosmetic clips. See video/README.md.
ROOM_STATES = {
    "LIVING-ROOM": [(_flag("TRAP-DOOR"), "The trap door stands open, revealing a dark staircase leading down.")],
    "CELLAR": [(_flag("TRAP-DOOR"), "At the top of the stairs the trap door stands open to the room above.")],
    "KITCHEN": [(_flag("KITCHEN-WINDOW"), "The window to the yard stands wide open.")],
    "GRATING-ROOM": [(_flag("GRATE"), "The grating overhead stands open to the daylight.")],
    "DAM-ROOM": [(_flag("dam-enabled"), "A green bubble glows on the control panel."),
                 (_flag("gates-open"), "The sluice gates stand open and water roars through them."),
                 (_flag("LOW-TIDE"), "Behind the dam the reservoir is almost empty, a wide bed of mud.")],
    "RESERVOIR": [(_flag("LOW-TIDE"), "The water is gone; the basin is a wide, muddy bed.")],
    "RESERVOIR-NORTH": [(_flag("LOW-TIDE"), "The reservoir has drained to a muddy bed that can be walked across.")],
    "RESERVOIR-SOUTH": [(_flag("LOW-TIDE"), "The reservoir has drained to a muddy bed that can be walked across.")],
    "DOME-ROOM": [(_flag("DOME-FLAG"), "A rope tied to the railing hangs down into the chamber below.")],
    "TORCH-ROOM": [(_flag("DOME-FLAG"), "A rope dangles from the railing of the dome high above.")],
    "ARAGAIN-FALLS": [(_flag("RAINBOW-FLAG"), "The rainbow has turned solid, a shimmering bridge over the falls.")],
    "END-OF-RAINBOW": [(_flag("RAINBOW-FLAG"), "The rainbow has turned solid, a shimmering bridge over the falls.")],
    "ON-RAINBOW": [(_flag("RAINBOW-FLAG"), "The rainbow is solid underfoot.")],
    "ENTRANCE-TO-HADES": [(_flag("LLD-FLAG"), "The spirits are gone and the way into the Land of the Dead stands open.")],
    "SHAFT-ROOM": [(lambda game: game.item_locations.get("RAISED-BASKET") != "SHAFT-ROOM",
                    "The basket has been lowered; its chain runs down into the shaft.")],
    "LOWER-SHAFT": [(lambda game: game.item_locations.get("RAISED-BASKET") == "LOWER-SHAFT",
                     "A basket on a chain rests at the bottom of the shaft.")],
    "SANDY-CAVE": [(lambda game: 0 < game.dig_count < 3, "A shallow hole has been dug in the sand."),
                   (lambda game: game.dig_count >= 3, "A deep hole has been dug in the sand.")],
}
SCENE_ACTIONS_TRIED = ("open", "move", "lift", "push", "turn", "unlock", "lower", "raise", "tie", "wave", "dig")


# Areas open to the sky: the forest and house, and the river with its cliffs, beach and falls. Everything else is the
# Great Underground Empire (the map's levels are a layout aid and draw some underground rooms high up).
SURFACE_AREAS = {"Forest And Outside Of House", "House", "River Area"}


@functools.cache
def underground_rooms() -> frozenset[str]:
    """Rooms below ground, for the prompt writer (it cannot tell from "a dead end in the maze")."""
    game = Maze()
    return frozenset(room for room, info in game.rooms.items() if info["area"] not in SURFACE_AREAS)


@functools.cache
def openable_fixtures() -> tuple[str, ...]:
    """Fixed containers that can be opened (mailbox, trophy case, machine, basket)."""
    return tuple(item_id for item_id, item in Maze().items.items()
                 if not item["takeable"] and item["container"] and not item["surface"] and item["capacity"])


def fixtures_here(game, room: str) -> list[str]:
    return [i for i in openable_fixtures() if game.item_locations.get(i) == room and i not in game.hidden_items]


def scenery(game, room: str | None = None) -> str:
    """How a room looks now: the game's description plus what is visibly open or changed there."""
    room = room or game.position
    extra = [f"The {game.items[i]['name']} is open." for i in fixtures_here(game, room) if game.item_open[i]]
    extra += [sentence for test, sentence in ROOM_STATES.get(room, []) if test(game)]
    return " ".join([game.scenery_description(room), *extra])


@functools.cache
def base_sceneries() -> dict[str, str]:
    """Each room as it looks at the start of a game: the state its plain keyframe shows."""
    game = Maze()
    return {room: scenery(game, room) for room in game.rooms}


def keyframe_id(room: str, text: str) -> str:
    if text == base_sceneries()[room]:
        return slug(room)
    return f"{slug(room)}__{hashlib.sha1(text.encode()).hexdigest()[:6]}"


def current_keyframe(game) -> str:
    """The keyframe for where the player is and how that room looks now."""
    return keyframe_id(game.position, scenery(game))


def find_edge(edges: list[Action], start: str, end: str) -> Action | None:
    """The canned clip for a turn that went from keyframe `start` to keyframe `end`, if there is one."""
    if start == end:
        return None  # nothing visible changed: the idle loop keeps playing
    return next((e for e in edges if e.type in ("move", "action") and e.start_frame == start and e.end_frame == end), None)


def derive(provider: str, rooms: set[str] | None = None) -> tuple[list[Keyframe], list[Action], list[str]]:
    """(keyframes, edges, rooms never reached). `rooms` limits the graph to those engine room ids or area names."""
    game = Maze()
    start(game)
    frames: dict[str, Keyframe] = {}
    edges: dict[tuple[str, str], Action] = {}

    def frame(g):
        key = current_keyframe(g)
        if key not in frames:
            room = g.rooms[g.position]
            fixtures = [i["name"] for i in g.items.values() if i["location"] == g.position and not i["takeable"] and not i.get("hidden")]
            frames[key] = Keyframe(id=key, room=g.position, room_name=room["name"], area=room["area"],
                                   scenery=scenery(g), fixtures=fixtures)
        return key

    def note(a, b, verb, kind):
        if a == b:
            return
        edge = edges.get((a, b))
        if edge is None:
            edges[(a, b)] = Action(id=f"{a}__to__{b}", type=kind, verbs=[verb], start_frame=a, end_frame=b,
                                   duration_s=MOVE_SECONDS if kind == "move" else ACTION_SECONDS, audio=True,
                                   clips=_segments(a, [], b, provider))
        elif verb not in edge.verbs:
            edge.verbs.append(verb)

    cosmetic: dict[tuple[str, str], Action] = {}

    def note_cosmetic(a, verb):
        if (a, verb) not in cosmetic:
            cosmetic[(a, verb)] = Action(id=f"{a}__{verb.replace(' ', '_')}", type="dynamic", verbs=[verb], start_frame=a,
                                         end_frame=a, duration_s=ACTION_SECONDS, audio=True, cosmetic=True,
                                         clips=_segments(a, [], a, provider))

    def moves_out(g, a):
        for direction in g.exits():
            there = _copy(g)
            if there.move(direction)["success"] and not there.dead:
                note(a, frame(there), direction, "move")

    def moves_in(g, b):
        """Walking into g's room from each neighbour, with the world as it is in g."""
        for neighbour, info in g.rooms.items():
            for direction, destination in info["exits"].items():
                if destination != g.position or neighbour == g.position:
                    continue
                there = _copy(g)
                there.position = neighbour
                a = frame(there)
                if there.move(direction)["success"] and not there.dead and frame(there) == b:
                    note(a, b, direction, "move")

    toggled = set()

    def changes(g):
        """Everything here that changes how the room looks, tried on copies; new looks are explored the same way."""
        a = frame(g)
        if a in toggled:
            return
        toggled.add(a)
        tries = [(f"{'close' if g.item_open[i] else 'open'} {g.items[i]['name']}",
                  lambda t, i=i: t.close_container(i) if t.item_open[i] else t.open_container(i))
                 for i in fixtures_here(g, g.position)]
        tries += [(f"{action} {target}", lambda t, x=action, y=target: t.interact(x, y))
                  for action, target, _ in SCENES.get(g.position, []) if action in SCENE_ACTIONS_TRIED]
        # Picking up something where the game first puts it changes nothing visible (loose items are never drawn),
        # so it becomes a cosmetic clip that starts and ends on this look.
        tries += [(f"take {g.items[i]['name']}", lambda t, i=i: t.take(i)) for i in g.items
                  if g.items[i]["takeable"] and g.item_locations[i] == g.items[i]["location"]
                  and i not in g.hidden_items and g._visible(i, g.position, True)]
        for command, act in tries:
            there = _copy(g)
            if not act(there).get("success") or there.dead or there.position != g.position:
                continue
            b = frame(there)
            if b == a:
                if command.startswith("take "):
                    note_cosmetic(a, command)
                continue
            new = b not in toggled
            note(a, b, command, "action")
            if new:
                moves_out(there, b)
                moves_in(there, b)
                changes(there)

    def explore(g):
        """Every move reachable from g without acting on anything, each tried on a copy, and every change there."""
        queue, seen = [g], {g.position}
        while queue:
            here = queue.pop(0)
            a = frame(here)
            changes(here)
            for direction in here.exits():
                there = _copy(here)
                if not there.move(direction)["success"] or there.dead:
                    continue
                note(a, frame(there), direction, "move")
                if there.position not in seen:
                    seen.add(there.position)
                    queue.append(there)

    looks = set()
    for step in STEPS:
        look = tuple(scenery(game, room) for room in game.rooms)
        if look not in looks:
            looks.add(look)
            explore(game)
        else:  # what the player carries can open other ways (garlic, the lamp), so try the exits here too
            moves_out(game, frame(game))
        before, room = frame(game), game.position
        apply_step(game, step)
        words = [game.items[w]["name"] if w in game.items else w for w in command_text(step).removeprefix("interact ").split()]
        note(before, frame(game), " ".join(words), "move" if game.position != room else "action")

    for room in {k.room for k in frames.values() if k.id != slug(k.room)} - {k.room for k in frames.values() if k.id == slug(k.room)}:
        fresh = Maze()  # every changed look is edited from the room's first look, even if the run never shows it
        fresh.position = room
        frame(fresh)
    keyframes = list(frames.values())
    parents = _edit_parents(keyframes, edges.values())
    for k in keyframes:
        if k.id != slug(k.room):
            k.derived_from = parents.get(k.id, slug(k.room))
    actions = list(edges.values()) + list(cosmetic.values())
    for k in keyframes:
        actions += [Action(id=f"{k.id}__idle_{n}", type="idle", start_frame=k.id, end_frame=k.id,
                           duration_s=IDLE_SECONDS, audio=False, clips=_segments(k.id, [], k.id, provider))
                    for n in range(1, IDLE_VARIANTS + 1)]
    unreached = sorted(set(game.rooms) - {k.room for k in keyframes})
    if rooms:
        keep = {k.id for k in keyframes if in_scope(k, rooms)}
        keyframes = [k for k in keyframes if k.id in keep]
        actions = [e for e in actions if e.start_frame in keep and e.end_frame in keep]
        for k in keyframes:
            if k.derived_from not in keep:
                k.derived_from = None  # its parent is out of scope, so it is made from text
    return keyframes, actions, unreached


def _sentences(text):
    return {s.strip() for s in text.split(". ") if s.strip()}


def _edit_parents(keyframes, edges):
    """Each later look's parent: on the shortest chain of single changes from the room's first look, or, for looks
    no change in the room leads to (the cyclops smashes the living room door from elsewhere), the most similar
    look that already has one."""
    by_room: dict[str, list[Action]] = {}
    for e in edges:
        if e.type == "action":
            by_room.setdefault(e.start_frame.split("__")[0], []).append(e)
    parents = {}
    for k in keyframes:
        if k.id != slug(k.room):
            continue
        queue, seen = [k.id], {k.id}
        while queue:
            here = queue.pop(0)
            for e in by_room.get(slug(k.room), []):
                if e.start_frame == here and e.end_frame not in seen:
                    seen.add(e.end_frame)
                    parents[e.end_frame] = here
                    queue.append(e.end_frame)
        looks = {f.id: _sentences(f.scenery) for f in keyframes if f.room == k.room}
        placed = {k.id} | {i for i in looks if i in parents}
        while left := [i for i in looks if i not in placed]:
            child, parent = min(((c, p) for c in left for p in placed), key=lambda cp: len(looks[cp[0]] ^ looks[cp[1]]))
            parents[child] = parent
            placed.add(child)
    return parents


def in_scope(keyframe: Keyframe, scope: set[str]) -> bool:
    """Scope entries are engine room ids ("KITCHEN") or area names ("House"), in any case."""
    return keyframe.room.upper() in scope or keyframe.area.upper() in scope


def _segments(start, via, end, provider):
    frames = [start, *via, end]
    return [Clip(from_=a, to=b, provider=provider) for a, b in zip(frames, frames[1:])]


# manifest.json

def load_manifest(path: Path = MANIFEST_FILE) -> Manifest:
    return Manifest.model_validate_json(path.read_text(encoding="utf-8")) if path.exists() else Manifest()


def save_manifest(manifest: Manifest, path: Path = MANIFEST_FILE) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(manifest.model_dump(mode="json"), indent=2) + "\n", encoding="utf-8")


def merge_manifest(old: Manifest, keyframes: list[Keyframe], edges: list[Action]) -> Manifest:
    """A freshly derived graph, keeping prompts and build results whose description and frames have not changed."""
    old_frames = {k.id: k for k in old.keyframes}
    merged = []
    for k in keyframes:
        prev = old_frames.get(k.id)
        if prev and (prev.scenery, prev.derived_from) == (k.scenery, k.derived_from):
            k = k.model_copy(update={f: getattr(prev, f) for f in
                                     ("image_prompt", "edit_prompt", "idle_prompt", "image_path", "status", "layer_regions")})
        merged.append(k)
    idle_prompts = {k.id: k.idle_prompt for k in merged}
    old_edges = {e.id: e for e in old.actions}
    for edge in edges:
        prev = old_edges.get(edge.id)
        edge.motion_prompt = idle_prompts[edge.start_frame] if edge.type == "idle" else prev.motion_prompt if prev else None
        if prev and prev.motion_prompt == edge.motion_prompt and len(prev.clips) == len(edge.clips):
            edge.clips = [c.model_copy() for c in prev.clips]
    return Manifest(spent_usd=old.spent_usd, keyframes=merged, actions=edges, layers=merge_layers(old.layers))
