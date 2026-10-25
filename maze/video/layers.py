"""Layered fixtures: objects shown inside a fixture are drawn over its keyframes instead of making new keyframes.

The trophy case can hold any mix of 19 treasures (and the ancient map), far too many combinations for keyframes.
Instead its keyframes show the case empty, every treasure has a transparent picture (a Layer) and a fixed slot on
the case's shelves, and the player draws the pictures of whatever is in the case on top of the frame.

Slots are in case-local coordinates (0..1 across the shelf area marked on the keyframe, see Keyframe.layer_regions):
three shelves, biggest treasures at the bottom, each treasure always in the same place.
"""
from __future__ import annotations

import functools

from maze import Maze
from video.config import STYLE
from video.models import Layer

TROPHY_CASE = "TROPHY-CASE"
SHELVES = 3
GAP = 0.02            # space between neighbours on a shelf, as a fraction of the shelf width
FILL = 0.86           # how much of a shelf's height an object's slot may use

# Fixture id -> the room it stands in and what its keyframes must show so the layers fit.
LAYERED_FIXTURES = {
    TROPHY_CASE: {
        "room": "LIVING-ROOM",
        "note": ("The glass-fronted trophy case is seen straight on and fully in view, with three wide, empty wooden "
                 "shelves behind the glass. Its shelves are completely empty."),
    },
}


@functools.cache
def displayable() -> dict[str, str]:
    """Item id -> name for everything with a slot: the treasures, and the map that appears at the end."""
    items = Maze().items
    shown = {i: item["name"] for i, item in items.items() if item["deposit_points"] > 0}
    shown["MAP"] = items["MAP"]["name"]
    return shown


@functools.cache
def slots() -> dict[str, tuple[float, float, float, float]]:
    """Item id -> (x, y, width, height) of its slot inside the case, case-local 0..1, y downwards."""
    items = Maze().items
    widths = {i: items[i]["size"] ** 0.25 for i in displayable()}  # flattened, so small jewels stay readable
    ordered = sorted(widths, key=lambda i: (-widths[i], i))
    # Fill the bottom shelf with the biggest things, then the middle, then the top, about a third of the width each.
    shelves: list[list[str]] = [[]]
    for item_id in ordered:
        if len(shelves) < SHELVES and sum(widths[i] for i in shelves[-1]) >= sum(widths.values()) / SHELVES:
            shelves.append([])
        shelves[-1].append(item_id)
    shelves.reverse()  # row 0 is the top shelf
    scale = min((1 - GAP * (len(shelf) + 1)) / sum(widths[i] for i in shelf) for shelf in shelves)
    height = FILL / SHELVES
    out = {}
    for row, shelf in enumerate(shelves):
        # Largest in the middle, the rest alternating outwards, so each shelf looks arranged.
        arranged: list[str] = []
        for n, item_id in enumerate(shelf):  # shelf is largest first
            arranged.insert(0, item_id) if n % 2 else arranged.append(item_id)
        gap = (1 - sum(widths[i] for i in shelf) * scale) / (len(shelf) + 1)
        x = gap
        for item_id in arranged:
            w = widths[item_id] * scale
            out[item_id] = (x, (row + 1) / SHELVES - height - 0.01, w, height)  # standing on the shelf
            x += w + gap
    return out


def layer_prompt(name: str) -> str:
    return (f"A single {name}, shown whole and upright as it would stand on a museum shelf, seen straight on at eye "
            "level and centred, filling most of the frame, on a plain flat light grey background, no shadow, no other "
            f"objects. Soft warm light. {STYLE}.")


def all_layers() -> list[Layer]:
    return [Layer(id=i, name=name, fixture=TROPHY_CASE, prompt=layer_prompt(name)) for i, name in displayable().items()]


def merge_layers(old: list[Layer]) -> list[Layer]:
    """All layers, keeping pictures whose prompt has not changed."""
    previous = {layer.id: layer for layer in old}
    merged = []
    for layer in all_layers():
        prev = previous.get(layer.id)
        merged.append(prev.model_copy() if prev and prev.prompt == layer.prompt else layer)
    return merged


def on_display(game, fixture: str = TROPHY_CASE) -> list[str]:
    """Items directly inside the fixture that have a slot, in slot order (what the player should see in it)."""
    return [i for i in slots() if game.item_locations.get(i) == fixture and i not in game.hidden_items]


def placed(region: list[float], item_id: str) -> dict:
    """An item's slot inside a fixture region, as percentages of the whole image (for CSS)."""
    x0, y0, x1, y1 = region
    x, y, w, h = slots()[item_id]
    return {"left": 100 * (x0 + x * (x1 - x0)), "top": 100 * (y0 + y * (y1 - y0)),
            "width": 100 * w * (x1 - x0), "height": 100 * h * (y1 - y0)}
