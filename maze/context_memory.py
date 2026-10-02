"""Bounded, observation-only handoff for long tool-playing sessions."""
import json
from collections import OrderedDict, deque
from copy import deepcopy


def small(value, depth=0):
    if depth > 4:
        return str(value)[:180]
    if isinstance(value, str):
        return value[:450]
    if isinstance(value, dict):
        return {k: small(v, depth + 1) for k, v in list(value.items())[:30] if k not in ("grid", "transcript")}
    if isinstance(value, list):
        return [small(v, depth + 1) for v in value[:20]]
    return value


class ContextMemory:
    def __init__(self, goal):
        self.goal = goal
        self.rooms = OrderedDict()
        self.notes = deque(maxlen=10)
        self.latest = {}
        self.current_room = None
        self.viewer_hints = deque(maxlen=5)
        self.left_items = OrderedDict()  # item id -> room it was set down in

    def observe(self, name, args, result, reasoning=None):
        room = result.get("room_id")
        if room:
            record = self.rooms.setdefault(room, {})
            record.update({k: small(result[k]) for k in ("room_name", "exits") if k in result})
            if name == "move" and self.current_room and room != self.current_room and result.get("success", True):
                self.rooms.setdefault(self.current_room, {}).setdefault("observed_destinations", {})[args.get("direction", "?")] = room
            self.current_room = room
            self.rooms.move_to_end(room)
            while len(self.rooms) > 80:
                self.rooms.popitem(last=False)
        item = result.get("item_id")
        if item and result.get("success"):
            if name == "take":
                self.left_items.pop(item, None)
            elif name in ("drop", "put"):
                self.left_items[item] = room or self.current_room
                self.left_items.move_to_end(item)
                while len(self.left_items) > 40:
                    self.left_items.popitem(last=False)
        self.latest.update({k: small(v) for k, v in result.items() if k not in ("grid", "transcript", "message", "error", "hint")})
        note = {"tool": name, "args": small(args)}
        if reasoning:
            note["reason"] = str(reasoning)[:400]
        for key in ("success", "message", "error", "hint", "examined", "text", "announcement"):
            if key in result:
                note[key] = small(result[key])
        self.notes.append(note)

    def snapshot(self):
        return deepcopy({"goal": self.goal, "rooms": dict(self.rooms), "notes": list(self.notes),
                         "latest": self.latest, "current_room": self.current_room,
                         "viewer_hints": list(self.viewer_hints), "left_items": dict(self.left_items)})

    @classmethod
    def restore(cls, data):
        if not isinstance(data, dict) or not isinstance(data.get("goal"), str):
            raise ValueError("Invalid saved agent memory.")
        if not isinstance(data.get("rooms", {}), dict) or not isinstance(data.get("latest", {}), dict):
            raise ValueError("Invalid saved observations.")
        if not isinstance(data.get("notes", []), list) or not isinstance(data.get("viewer_hints", []), list):
            raise ValueError("Invalid saved action history.")
        memory = cls(data["goal"][:8000])
        memory.rooms = OrderedDict(list(deepcopy(data.get("rooms", {})).items())[-80:])
        memory.latest = deepcopy(data.get("latest", {}))
        memory.notes.extend(deepcopy(data.get("notes", []))[-10:])
        memory.viewer_hints.extend(str(h)[:1000] for h in data.get("viewer_hints", [])[-5:])
        memory.current_room = data.get("current_room")
        left = data.get("left_items", {})
        if isinstance(left, dict):
            memory.left_items = OrderedDict((str(k)[:80], str(v)[:80]) for k, v in list(left.items())[-40:])
        return memory

    def input(self):
        # Bounded excerpts are deliberately labelled so they are not treated
        # as complete history or secret knowledge about unexplored rooms.
        text = ("Continue the same game autonomously. The world has not been reset. "
                "This is a bounded memory of your actual tool observations; older details may be omitted. "
                "Use tools to recheck uncertain facts. Execute your next action.\n"
                "Goal: " + self.goal[:1500] + "\n"
                "Recent viewer hints: " + json.dumps(list(self.viewer_hints), ensure_ascii=True) + "\n"
                "Items you set down and may need again (item id: room id): " + json.dumps(dict(self.left_items), ensure_ascii=True) + "\n"
                "Latest observations: " + json.dumps(self.latest, ensure_ascii=True)[:6500] + "\n"
                "Observed map: " + json.dumps(self.rooms, ensure_ascii=True)[:6000] + "\n"
                "Recent actions and clues: " + json.dumps(list(self.notes), ensure_ascii=True)[-5000:])
        return [{"role": "user", "content": text}]


def context_error(exc):
    body = getattr(exc, "body", None)
    if isinstance(body, dict):
        error = body.get("error", body)
        if isinstance(error, dict) and error.get("code") == "context_length_exceeded":
            return True
    return "context_length_exceeded" in str(exc) or "exceeds the context window" in str(exc).lower()
