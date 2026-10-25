"""Types for the video layer: keyframes are the nodes of the clip graph, actions are its edges.

Both are derived from the game itself (see derive.py): a keyframe is one room as the engine describes it in one
state, and an action is a command that took the game from one keyframe to another. The prompts are written by
embellish.py from the engine's descriptions. Everything lives in data/manifest.json.
"""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

Status = Literal["pending", "generated", "approved", "rejected"]


class Keyframe(BaseModel):
    id: str                               # "living_room", or "living_room__3fa2c1" for a later state of it
    room: str                             # engine room id, "LIVING-ROOM"
    room_name: str
    area: str
    scenery: str                          # the engine's description of the room in this state
    fixtures: list[str] = []              # objects fixed in the room (mailbox, trophy case, ...)
    kind: Literal["anchor", "waypoint"] = "anchor"
    image_prompt: str | None = None       # written by embellish.py for keyframes generated from text
    derived_from: str | None = None       # the room's first state, which later states are edited from
    edit_prompt: str | None = None        # written by embellish.py when derived_from is set
    idle_prompt: str | None = None        # ambient motion for this keyframe's idle loops
    reference_frames: list[str] = []      # no longer used: pictures are made from text alone (see SCENE_STYLE)
    layer_regions: dict[str, list[float]] = {}  # fixture id -> [x0, y0, x1, y1] of its shelves, as image fractions
    image_path: str | None = None         # filled in by the build: "assets/keyframes/<id>.png"
    status: Status = "pending"


class Clip(BaseModel):
    from_: str = Field(alias="from")
    to: str
    path: str | None = None               # "assets/clips/<action_id>__<n>.mp4"
    provider: str
    seed: int | None = None
    first_frame_score: float | None = None
    last_frame_score: float | None = None
    fit: list[float] | None = None        # measured [zoom, dx, dy] of the download relative to its keyframes
    normalized: bool = False              # cropped to the display view (normalize.py); the download is kept as .orig.mp4
    status: Status = "pending"

    model_config = {"populate_by_name": True, "serialize_by_alias": True}


class Action(BaseModel):
    """A concrete edge of the clip graph: from start_frame, play clips, land on end_frame.

    The engine decides what a command does and prints its reply; the clip for a turn is the edge from the
    keyframe before the command to the keyframe after it. `verbs` are the game commands seen to cause it.
    """
    id: str
    type: Literal["move", "action", "idle", "dynamic"]
    verbs: list[str] = []
    start_frame: str
    end_frame: str
    via: list[str] = []
    motion_prompt: str | None = None      # written by embellish.py
    duration_s: int
    audio: bool
    clips: list[Clip] = []
    cosmetic: bool = False


class Layer(BaseModel):
    """A transparent picture of one object, drawn over keyframes in a fixed slot (treasures in the trophy case)."""
    id: str                               # engine item id, "CHALICE"
    name: str
    fixture: str                          # the fixture it is shown in, "TROPHY-CASE"
    prompt: str
    image_path: str | None = None         # "assets/layers/<id>.png", with a transparent background
    status: Status = "pending"


class Manifest(BaseModel):
    spent_usd: float = 0.0
    keyframes: list[Keyframe] = []
    actions: list[Action] = []
    layers: list[Layer] = []
