"""What the player sees right now, for the web app: the keyframe for the game's room and state, layered objects,
and the clip the last turn should play (from the keyframe before the command to the keyframe after it)."""
from __future__ import annotations

import threading

from video.config import ASSET_URL, DISPLAY_ZOOM
from video.gen_keyframes import VIDEO_DIR, versioned
from video.graph import MANIFEST_FILE, current_keyframe, load_manifest
from video.layers import LAYERED_FIXTURES, on_display, placed

_cache = {"mtime": None, "frames": {}, "layers": {}, "edges": {}, "cosmetic": {}, "idles": {}}
_cache_lock = threading.Lock()
TAKE_WORDS = ("take", "get", "grab", "pick")
ARTICLES = ("the", "a", "an", "up")


def _load():
    """Keyframes, layers and clip edges, reloaded only when manifest.json changes (the page polls every second)."""
    try:
        mtime = MANIFEST_FILE.stat().st_mtime
    except FileNotFoundError:
        return _cache
    with _cache_lock:
        if _cache["mtime"] != mtime:
            manifest = load_manifest()
            _cache.update(mtime=mtime, frames={k.id: k for k in manifest.keyframes},
                          layers={layer.id: layer for layer in manifest.layers},
                          edges={(e.start_frame, e.end_frame): e for e in manifest.actions if e.type in ("move", "action")},
                          cosmetic={(e.start_frame, verb): e for e in manifest.actions if e.cosmetic for verb in e.verbs},
                          idles=_idles(manifest))
        return _cache


def _idles(manifest):
    """Keyframe id -> the edge ids of its finished idle loops."""
    idles: dict[str, list] = {}
    for e in manifest.actions:
        if e.type == "idle":
            idles.setdefault(e.start_frame, []).append(e)
    return idles


def asset_url(path):
    """A picture or clip served by this app when it is on disk here, else from the public media bucket."""
    return f"/video/{versioned(path)}" if (VIDEO_DIR / path).exists() else f"{ASSET_URL}/{path}"


def region(frames, keyframe_id, fixture):
    """Where a fixture's shelves are in this keyframe: marked on it or on the look it was edited from."""
    seen = set()
    while keyframe_id in frames and keyframe_id not in seen:
        seen.add(keyframe_id)
        frame = frames[keyframe_id]
        if fixture in frame.layer_regions:
            return frame.layer_regions[fixture]
        keyframe_id = frame.derived_from
    return None


def _playable(edge):
    """The edge's clip files in playing order, if every segment has been made."""
    if edge and edge.clips and all(c.path and c.status in ("generated", "approved") for c in edge.clips):
        return [asset_url(c.path) for c in edge.clips]
    return None


def _take_verb(game, command):
    """"take <item name>" for a pick-up command, whatever words or item id the player used, else None."""
    words = [w for w in command.lower().split() if w not in ARTICLES]
    if len(words) < 2 or words[0] not in TAKE_WORDS:
        return None
    noun = " ".join(words[1:]).removeprefix("up ").strip()
    try:
        return f"take {game.items[game._find_item(noun)]['name']}"
    except (ValueError, KeyError):
        return None


class Turns:
    """Remembers the keyframe the player last saw, and the clip the latest turn should play."""

    def __init__(self):
        self.previous = None
        self.sequence = 0
        self.last = None
        self._lock = threading.Lock()

    def reset(self, game):
        """After a restore or reset: show the new place without a clip."""
        with self._lock:
            self.previous = current_keyframe(game)

    def after(self, game, command):
        """Called once per game command, after it ran."""
        cache = _load()
        now = current_keyframe(game)
        with self._lock:
            before, self.previous = self.previous, now
            if before is None:
                return None
            if before != now:
                edge = cache["edges"].get((before, now))
            else:
                verb = _take_verb(game, command)
                edge = cache["cosmetic"].get((now, verb)) if verb else None
            sources = _playable(edge)
            if not sources:
                return None
            self.sequence += 1
            self.last = {"seq": self.sequence, "id": edge.id, "sources": sources, "from": before, "to": now}
            return self.last


turns = Turns()


def scene(game) -> dict:
    """{keyframe, image, status, layers, clip, zoom, idle}: image is a URL (asset_url), or None when this room has no picture
    yet; layers are the pictures of objects shown inside fixtures here, placed as percentages of the image; clip is
    the latest turn's clip ({seq, sources, ...}; the page plays each seq once); zoom is the crop stills are shown at; idle lists this keyframe's ambient loops (silent)."""
    cache = _load()
    frames, layers = cache["frames"], cache["layers"]
    keyframe_id = current_keyframe(game)
    if turns.previous is None:
        turns.reset(game)
    frame = frames.get(keyframe_id)
    image = asset_url(frame.image_path) if frame and frame.image_path else None
    shown = []
    for fixture, info in LAYERED_FIXTURES.items():
        box = region(frames, keyframe_id, fixture) if image and info["room"] == game.position else None
        if box:
            shown += [{"id": i, "src": asset_url(layers[i].image_path), **placed(box, i)}
                      for i in on_display(game, fixture) if i in layers and layers[i].image_path]
    idle = [src for e in cache["idles"].get(keyframe_id, []) for src in (_playable(e) or [])]
    return {"keyframe": keyframe_id, "image": image, "status": frame.status if frame else None, "layers": shown,
            "clip": turns.last, "zoom": DISPLAY_ZOOM, "idle": idle}
