"""The Zork I world: a room graph with items, adapted puzzles, combat and a roaming thief.

The module-level functions at the bottom operate on one shared Maze and are what the
web app calls; the MCP server reaches the same world through the web app's HTTP API.
"""

from __future__ import annotations

import json
import random
from puzzles import PuzzleWorld, SCENES
from combat import CombatWorld
from pathlib import Path

ROOM_DIRECTIONS = (
    "north", "south", "east", "west", "northeast", "northwest",
    "southeast", "southwest", "up", "down", "in", "out", "land", "launch",
)


class Maze(PuzzleWorld, CombatWorld):
    """Directed room graph; coordinates are display-only, not movement rules."""

    def __init__(self, puzzles=True):
        self.puzzles = puzzles
        data = json.loads(Path(__file__).with_name("zork_map.json").read_text(encoding="utf-8"))
        self.rooms = data["rooms"]
        self.items = data.get("items", {})
        self._random = random.Random()
        self.max_weight = 100
        self.fumble_threshold = 7
        self.fumble_probability = 8
        self.start = data["start"]
        self.goal = "STONE-BARROW" if self.puzzles else "TREASURE-ROOM"
        if self.puzzles:
            # The original ends when you walk into the barrow (IN, or WEST); show it as an exit.
            self.rooms["STONE-BARROW"]["exits"].setdefault("in", "STONE-BARROW")
        self.title = data["title"]
        self.adaptation = data["adaptation"]
        self.reset()
        from map_layout import layout_with_levels
        self.coordinates, self.map_width, self.map_height, self.levels = layout_with_levels(self.rooms, self.start)
        self.areas = []

    def exits(self) -> list[str]:
        return [d for d in self.rooms[self.position]["exits"] if not self.gate_error(d)]

    def look(self) -> dict:
        if self._turn_pending:
            self._turn_pending = False
            self.finish_combat_turn()
        self._update_endgame()
        room = self.rooms[self.position]
        return {
            "position": self.position, "room_id": self.position,
            "room_name": room["name"], "description": (self.room_description() if self.puzzles else room["description"]),
            "area": room["area"], "exits": self.exits(),
            "at_goal": ("ADVENTURE-COMPLETE" in self.flags) if self.puzzles else self.position == self.goal, "moves_made": self.moves_made,
            "objective": self._objective(),
            "adventure_complete": "ADVENTURE-COMPLETE" in self.flags,
            "announcement": self.endgame_announcement if self.puzzles else None,
            "mode": "Puzzle adventure with randomized troll combat and a roaming thief. Darkness, boat inflation and the original parser are not simulated." if self.puzzles else "Navigation mode.",
            "allowed_actions": self.puzzle_actions(),
            "nearby_features": self.nearby_features(),
            "encounter_events": [*self.story_events, *self.thief_events, *self.combat_events],
            "combat": self.combat_status(), "game_over": self.dead,
            "blocked_exits": {d: self.gate_error(d) for d in room["exits"] if self.gate_error(d)},
            "puzzles_solved": len(self.flags) if self.puzzles else 0,
            "items": [self._item_view(i) for i in self.items if self._visible(i, self.position)],
            "inventory": self._carried_items(),
            "revision": self.revision,
            **self._stats(),
        }

    def move(self, direction: str) -> dict:
        stopped = self._dead_result()
        if stopped:
            return stopped
        aliases = {"n": "north", "s": "south", "e": "east", "w": "west", "ne": "northeast", "nw": "northwest", "se": "southeast", "sw": "southwest", "u": "up", "d": "down"}
        direction = direction.strip().lower()
        direction = aliases.get(direction, direction)
        if direction not in ROOM_DIRECTIONS:
            return {"success": False, "error": "Unknown direction.", **self.look()}
        before = self._score()
        self._tick()
        blocked = self.gate_error(direction)
        if blocked:
            return {"success": False, "error": blocked, **self.look()}
        if self.puzzles and self.position == "STONE-BARROW" and direction in ("in", "west"):
            return self._enter_barrow()
        destination = self.rooms[self.position]["exits"].get(direction)
        if destination is None:
            return {"success": False, "error": f"No exit {direction!r} from this room. Available exits: {self.exits()}.", **self.look()}
        source = self.position
        self.position = destination
        self.moves_made += 1
        self.visited.add(destination)
        self.on_enter(source, direction, destination)
        return {"success": True, "score_delta": self._score() - before, **self.look()}

    def _enter_barrow(self) -> dict:
        """The original's ending (STONE-BARROW-FCN): the sign over the bridge, then the final score."""
        self.flags.add("ADVENTURE-COMPLETE")
        self.revision += 1
        stats = self._stats()
        self.story_events.append("Inside the Barrow\n\nAs you enter the barrow, the door closes inexorably behind you. Around you it is dark, but ahead is an enormous cavern, brightly lit. Through its center runs a wide stream. Spanning the stream is a small wooden footbridge, and beyond a path leads into a dark tunnel. Above the bridge, floating in the air, is a large sign. It reads: All ye who stand before this bridge have completed a great and perilous adventure which has tested your wit and courage. You have mastered the first part of the ZORK trilogy. Those who pass over this bridge must be prepared to undertake an even greater adventure that will severely test your skill and bravery!\n\nThe ZORK trilogy continues with \"ZORK II: The Wizard of Frobozz\" and is completed in \"ZORK III: The Dungeon Master.\"")
        self.story_events.append(f"Your adventure is over. You scored {stats['score']} of {stats['score_max']} points "
                                 f"in {stats['turns']} turns, which gives you the rank of {stats['rank']}.")
        return {"success": True, "score_delta": 0, **self.look()}

    def seed(self, value) -> None:
        """Fix the game's dice (combat, the thief's wandering) so a run can be replayed exactly."""
        self._random.seed(value)

    def reset(self) -> dict:
        self.position = self.start
        self.moves_made = 0
        self.visited = {self.start}
        self.item_locations = {i: item["location"] for i, item in self.items.items()}
        self.item_open = {i: item["open"] for i, item in self.items.items()}
        self.revision = getattr(self, "revision", 0) + 1
        self.turns = 0
        self.discovered_items = set()
        self.puzzle_reset()
        return self.look()

    def _update_endgame(self):
        if self.puzzles and "WON-FLAG" not in self.flags and self._score() >= 350:
            self.flags.add("WON-FLAG")
            self.hidden_items.discard("MAP")
            self.revision += 1
            self.endgame_announcement = "An ancient map has appeared in the trophy case."

    def _objective(self):
        if not self.puzzles:
            return "Reach the Treasure Room."
        if "ADVENTURE-COMPLETE" in self.flags:
            return "Your adventure is complete."
        if "WON-FLAG" in self.flags:
            if self.position == self.goal:
                return "Go in through the stone door to complete your adventure."
            return "Examine the ancient map in the trophy case and follow it to continue your adventure."
        return "Explore the Great Underground Empire, discover treasures, and deposit them in the trophy case."

    def _tick(self, advance_thief=True):
        self.turns += 1
        self.revision += 1
        self.combat_events = []
        self.story_events = []
        self._turn_pending = True
        self.thief_events = []
        self.advance_clocks()
        if advance_thief:
            self.advance_thief()

    def _inside(self, item_id, root):
        location = self.item_locations[item_id]
        while location in self.items:
            if location == root:
                return True
            location = self.item_locations[location]
        return location == root

    def _weight(self, item_id):
        return self.items[item_id]["size"] + self._contents_weight(item_id)

    def _contents_weight(self, container):
        return sum(self._weight(i) for i, location in self.item_locations.items() if location == container)

    def _score_parts(self):
        rooms = sum(self.rooms[i].get("discovery_points", 0) for i in self.visited)
        discoveries = sum(self.items[i]["discovery_points"] for i in self.discovered_items)
        deposits = 0
        for item_id, item in self.items.items():
            location = self.item_locations[item_id]
            while location in self.items:
                if location == "TROPHY-CASE":
                    deposits += item["deposit_points"]
                    break
                location = self.item_locations[location]
        parts = {"rooms": rooms, "item_discoveries": discoveries, "treasures_in_case": deposits}
        if self.puzzles:
            parts["puzzles"] = 13 if "shaft-supplied" in self.flags else 0
        return parts

    def trophy_case_progress(self):
        """Passive viewer summary using the same nested deposits as scoring."""
        treasures = [
            {"id": item_id, "name": item["name"], "points": item["deposit_points"]}
            for item_id, item in self.items.items() if item["deposit_points"] > 0
        ]
        stored = [item for item in treasures if self._inside(item["id"], "TROPHY-CASE")]
        return {"treasures": stored, "count": len(stored), "total": len(treasures),
                "points": sum(item["points"] for item in stored),
                "max_points": sum(item["points"] for item in treasures)}

    def _score(self):
        return sum(self._score_parts().values())

    def _stats(self):
        score = self._score()
        thresholds = [(350, "Master Adventurer"), (331, "Wizard"), (301, "Master"),
                      (201, "Adventurer"), (101, "Junior Adventurer"), (51, "Novice Adventurer"),
                      (26, "Amateur Adventurer"), (0, "Beginner")]
        count = sum(location == "inventory" for location in self.item_locations.values())
        chance = min(100, max(0, count * self.fumble_probability - 1)) if count > self.fumble_threshold else 0
        return {
            "turns": self.turns, "hints_used": self.hints_used, "score": score, "score_max": 350,
            "available_score_max": sum(r.get("discovery_points", 0) for r in self.rooms.values())
                + sum(i["discovery_points"] + i["deposit_points"] for i in self.items.values() if i["takeable"]) + (13 if self.puzzles else 0),
            "score_breakdown": self._score_parts(),
            "rank": next(rank for minimum, rank in thresholds if score >= minimum),
            "carrying": {"weight": self._contents_weight("inventory"), "max_weight": self.max_weight,
                         "loose_items": count, "fumble_threshold": self.fumble_threshold, "fumble_chance_percent": chance},
        }

    def observe(self):
        """Explicit LOOK command consumes a turn; look() is a passive snapshot."""
        stopped = self._dead_result()
        if stopped:
            return stopped
        self._tick()
        return self.look()

    def score(self):
        self._update_endgame()
        return {"success": True, **self._stats()}

    def wait(self):
        stopped = self._dead_result()
        if stopped:
            return stopped
        self._tick()
        return {"success": True, "message": "Time passes.", **self.look()}

    def _visible(self, item_id, root, reachable=False):
        if self.puzzles and item_id in self.hidden_items:
            return False
        location = self.item_locations[item_id]
        while location in self.items:
            parent = self.items[location]
            if self.puzzles and location in self.hidden_items:
                return False
            if not self.item_open[location] and (reachable or not parent["transparent"]):
                return False
            location = self.item_locations[location]
        return location == root

    def _reachable(self, item_id):
        return self._visible(item_id, self.position, True) or self._visible(item_id, "inventory", True)

    def _item_view(self, item_id):
        item = self.items[item_id]
        view = {"id": item_id, "name": item["name"], "takeable": item["takeable"],
                "container": item["container"], "surface": item["surface"],
                "location": self.item_locations[item_id], "accessible": self._reachable(item_id),
                "treasure": item["treasure"]}
        view["weight"] = self._weight(item_id)
        view["size"] = item["size"]
        if item["container"]:
            view["open"] = self.item_open[item_id]
            view["capacity"] = item["capacity"]
            view["contents_weight"] = self._contents_weight(item_id)
            if view["open"] or item["transparent"]:
                view["contents"] = [{"id": i, "name": self.items[i]["name"]}
                                    for i, parent in self.item_locations.items() if parent == item_id and (not self.puzzles or i not in self.hidden_items)]
        return view

    def _carried_items(self):
        return [self._item_view(i) for i, location in self.item_locations.items() if location == "inventory"]

    def _find_item(self, query):
        query = query.strip().lower()
        for article in ("the ", "a ", "an "):
            if query.startswith(article):
                query = query[len(article):].strip()
                break
        visible = [i for i in self.items if self._visible(i, self.position) or self._visible(i, "inventory")]
        # Stable IDs and full names beat ambiguous aliases such as 'knife'.
        exact = [i for i in visible if query in (i.lower(), self.items[i]["name"].lower())]
        matches = exact or [i for i in visible if query in self.items[i]["aliases"]]
        if len(matches) == 1:
            return matches[0]
        if len(matches) > 1:
            raise ValueError(f"Ambiguous item. Use one of these IDs: {matches}.")
        raise ValueError(f"Item {query!r} is not visible here or in your inventory.")

    def inventory(self):
        stopped = self._dead_result()
        if stopped:
            return stopped
        self._tick()
        return {"success": True, **self.look()}

    def _item_action(self, action, item, container=None):
        stopped = self._dead_result()
        if stopped:
            return stopped
        self._tick()
        before = self._score()
        try:
            item_id = self._find_item(item)
            definition = self.items[item_id]
            if self.puzzles:
                if action == "open" and item_id == "EGG" and "egg-opened" not in self.flags:
                    raise ValueError("The delicate egg resists your attempts to open it.")
                if action == "take" and item_id == "CHALICE" and self.thief_present():
                    raise ValueError("You'd be stabbed in the back first.")
                if action == "take" and item_id == "BAR" and self.position == "LOUD-ROOM" and "echo-quiet" not in self.flags:
                    raise ValueError("The deafening noise prevents you from taking the bar.")
            if action == "examine":
                return {"success": True, "item": {**self._item_view(item_id),
                        "description": definition["description"], "readable_text": definition["readable_text"]}, **self.look()}
            if not self._reachable(item_id):
                raise ValueError("Open the containing object before reaching inside.")
            if action == "take":
                if not definition["takeable"]:
                    raise ValueError("That object is fixed in place.")
                if self.item_locations[item_id] == "inventory":
                    raise ValueError("You are already carrying that item.")
                extra_weight = 0 if self._inside(item_id, "inventory") else self._weight(item_id)
                if self._contents_weight("inventory") + extra_weight > self.max_weight:
                    raise ValueError(f"Your load would exceed {self.max_weight} weight units. Drop something first.")
                count = sum(location == "inventory" for location in self.item_locations.values())
                if count > self.fumble_threshold and self._random.randint(1, 100) < count * self.fumble_probability:
                    raise ValueError("You are holding too many loose things and fumble the pickup. Put items in a container or drop something.")
                self.item_locations[item_id] = "inventory"
                self.discovered_items.add(item_id)
                message = f"Taken: {definition['name']}."
            elif action in ("drop", "put"):
                if not self._visible(item_id, "inventory", True):
                    raise ValueError("You are not carrying that item.")
                destination = self.position
                if action == "put":
                    destination = self._find_item(container)
                    if not self.items[destination]["container"]:
                        raise ValueError("That object cannot hold items.")
                    if not self._reachable(destination) or not self.item_open[destination]:
                        raise ValueError("Open the destination container first.")
                    ancestor = destination
                    while ancestor in self.items:
                        if ancestor == item_id:
                            raise ValueError("An item cannot be put inside itself or its own contents.")
                        ancestor = self.item_locations[ancestor]
                    if self.item_locations[item_id] == destination:
                        raise ValueError("That item is already in that container.")
                    # Check every containing ancestor using the projected
                    # placement, then restore it if any capacity is exceeded.
                    previous = self.item_locations[item_id]
                    self.item_locations[item_id] = destination
                    try:
                        ancestor = destination
                        while ancestor in self.items:
                            capacity = self.items[ancestor]["capacity"]
                            if self._contents_weight(ancestor) > capacity:
                                raise ValueError(f"There is not enough room in {self.items[ancestor]['name']} (capacity {capacity}).")
                            ancestor = self.item_locations[ancestor]
                    except ValueError:
                        self.item_locations[item_id] = previous
                        raise
                self.item_locations[item_id] = destination
                message = f"Placed {definition['name']} in {destination}."
            elif action in ("open", "close"):
                if not definition["container"] or definition["surface"] or not definition["capacity"]:
                    raise ValueError("That object cannot be opened or closed.")
                opened = action == "open"
                if self.item_open[item_id] == opened:
                    raise ValueError(f"That container is already {'open' if opened else 'closed'}.")
                self.item_open[item_id] = opened
                message = f"{'Opened' if opened else 'Closed'}: {definition['name']}."
            else:
                raise ValueError("Unknown item action.")
            return {"success": True, "message": message, "item_id": item_id,
                    "score_delta": self._score() - before, **self.look()}
        except ValueError as exc:
            return {"success": False, "error": str(exc), **self.look()}

    def take(self, item: str):
        return self._item_action("take", item)

    def drop(self, item: str):
        return self._item_action("drop", item)

    def examine(self, item: str):
        feature = self._fixture(item)
        if feature:
            stopped = self._dead_result()
            if stopped:
                return stopped
            self._tick()
            note = next(n for a, t, n in SCENES[self.position] if t == feature)
            return {"success": True, "message": note, **self.look()}
        return self._item_action("examine", item)

    def open_container(self, item: str):
        # Fixtures such as the window, grate and trapdoor are scene actions, not items.
        feature = self._fixture(item, "open")
        if feature:
            return self.interact("open", feature)
        return self._item_action("open", item)

    def close_container(self, item: str):
        if self._fixture(item):
            return {"success": False, "error": "That cannot be closed with this tool; use interact "
                    "with an action listed in allowed_actions.", **self.look()}
        return self._item_action("close", item)

    def _fixture(self, query, action=None):
        """Scene feature named by query here, when no item answers to that name."""
        if not self.puzzles or self._item_here(query):
            return None
        feature = " ".join(query.strip().lower().replace("-", " ").split()).removeprefix("the ")
        feature = "trapdoor" if feature == "trap door" else feature
        return feature if any(t == feature and (action is None or a == action)
                              for a, t, _ in SCENES.get(self.position, [])) else None

    def _item_here(self, query):
        try:
            self._find_item(query)
            return True
        except ValueError as exc:
            return "Ambiguous" in str(exc)

    def put(self, item: str, container: str):
        return self._item_action("put", item, container)

    def grid_view(self) -> dict:
        """Browser-only room map, excluded from the MCP look result."""
        nodes = []
        for room_id, room in self.rooms.items():
            status = ("agent" if room_id == self.position else "goal" if room_id == self.goal and (not self.puzzles or "WON-FLAG" in self.flags)
                      else "start" if room_id == self.start else "visited" if room_id in self.visited else "floor")
            x, y = self.coordinates[room_id]
            nodes.append({"id": room_id, "name": room["name"], "description": room["description"],
                          "exits": room["exits"], "status": status, "x": x, "y": y})
        return {"kind": "rooms", "width": self.map_width, "height": self.map_height,
                "areas": self.areas, "levels": self.levels, "rooms": nodes, "visited_count": len(self.visited)}


# Module-level maze instance and thin tool functions, so this file can be
# imported and its functions registered directly as agent tools.
_maze = Maze()


def look() -> dict:
    """Tool: describe the current position and available exits."""
    return _maze.look()


def move(direction: str) -> dict:
    """Tool: follow a named room exit, including stairs and diagonals."""
    return _maze.move(direction)


def reset() -> dict:
    """Tool: reset the maze back to the starting position."""
    return _maze.reset()


def grid_view() -> dict:
    """Full-grid render used by the web visualizer (not exposed to agents as a tool)."""
    return _maze.grid_view()


def trophy_case_progress() -> dict:
    return _maze.trophy_case_progress()


def inventory() -> dict:
    return _maze.inventory()


def observe() -> dict:
    return _maze.observe()


def score() -> dict:
    return _maze.score()


def wait() -> dict:
    return _maze.wait()


def take(item: str) -> dict:
    return _maze.take(item)


def drop(item: str) -> dict:
    return _maze.drop(item)


def examine(item: str) -> dict:
    return _maze.examine(item)


def open_container(item: str) -> dict:
    return _maze.open_container(item)


def close_container(item: str) -> dict:
    return _maze.close_container(item)


def put(item: str, container: str) -> dict:
    return _maze.put(item, container)


def hint() -> dict:
    return _maze.hint()


def interact(action: str, target: str, item: str = "") -> dict:
    return _maze.interact(action, target, item)
