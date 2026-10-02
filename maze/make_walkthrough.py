"""Rebuilds walkthrough.py: a perfect, scripted run of the game (350 points, adventure complete).

The route is written below as goals ("go to the Loud Room, say echo, take the bar"). This script
plays it on the real game, finds the paths itself, and records every command it issues as
explicit steps in walkthrough.py. Run it again after changing the map or the rules:

    uv run python make_walkthrough.py          # rewrite walkthrough.py
    uv run python make_walkthrough.py --check  # fail if walkthrough.py is out of date

The game's dice (the thief's wandering) are seeded, so the recorded steps replay identically.
"""
import sys
from collections import deque
from pathlib import Path

from maze import Maze

SEED = 7
OUTPUT = Path(__file__).with_name("walkthrough.py")


class Recorder:
    """Plays the game and records the commands it sends."""

    def __init__(self):
        self.world = Maze()
        self.world._random.seed(SEED)
        self.sections = []
        self.steps = None

    def section(self, label):
        self.steps = []
        self.sections.append((label, self.steps))

    def do(self, tool, *args):
        w = self.world
        if tool == "move":
            result = w.move(args[0])
        elif tool == "interact":
            result = w.interact(args[0], args[1], args[2] if len(args) > 2 else "")
        elif tool == "put":
            result = w.put(args[0], args[1])
        elif tool == "open":
            result = w.open_container(args[0])
        elif tool == "close":
            result = w.close_container(args[0])
        elif tool in ("take", "drop", "examine"):
            result = getattr(w, tool)(args[0])
        elif tool == "wait":
            result = w.wait()
        else:
            raise ValueError(f"Unknown step {tool!r}")
        if result.get("success", True) is False:
            raise RuntimeError(f"Step {tool} {args} failed in {w.position}: {result.get('error')}")
        self.steps.append((tool, *args))
        return result

    def path(self, goal):
        """Shortest route to a room, honouring the gates that are open right now."""
        w = self.world
        start, saved = w.position, w.position
        seen = {start: None}
        queue = deque([start])
        while queue:
            room = queue.popleft()
            w.position = room
            for direction, target in w.rooms[room]["exits"].items():
                if target in seen or w.gate_error(direction):
                    continue
                seen[target] = (room, direction)
                if target == goal:
                    w.position = saved
                    route = []
                    while seen[target]:
                        target, direction = seen[target][0], seen[target][1]
                        route.append(direction)
                    return route[::-1]
                queue.append(target)
        w.position = saved
        return [] if start == goal else None

    def goto(self, goal):
        route = self.path(goal)
        if route is None:
            raise RuntimeError(f"No route from {self.world.position} to {goal}")
        for direction in route:
            self.do("move", direction)

    def deposit(self, *items):
        self.goto("LIVING-ROOM")
        for item in items:
            self.do("put", item, "TROPHY-CASE")

    def meet_thief(self):
        """Walk to wherever the (seeded, random) thief is, waiting when he moves away."""
        w = self.world
        for attempt in range(60):
            if w.thief_position is None:
                raise RuntimeError("The thief is already gone")
            if w.position == w.thief_position:
                return
            try:
                self.goto(w.thief_position)
            except RuntimeError:
                self.do("wait")
            else:
                if w.position != w.thief_position and attempt % 3 == 2:
                    self.do("wait")
        raise RuntimeError("Could not catch the thief")


def record():
    r = Recorder()
    w = r.world

    r.section("Open the window, gear up and defeat the troll")
    r.goto("EAST-OF-HOUSE")
    r.do("interact", "open", "window")
    r.goto("KITCHEN")
    r.do("open", "SANDWICH-BAG")
    r.do("take", "GARLIC")
    r.goto("LIVING-ROOM")
    r.do("take", "LAMP")
    r.do("take", "SWORD")
    r.do("interact", "move", "rug")
    r.do("interact", "open", "trapdoor")
    r.do("open", "TROPHY-CASE")
    r.goto("ATTIC")
    r.do("take", "ROPE")
    r.goto("TROLL-ROOM")
    r.do("interact", "attack", "troll")

    r.section("The cyclops opens the way home (the trap door was barred behind you)")
    r.goto("CYCLOPS-ROOM")
    r.do("interact", "say", "cyclops", "odysseus")
    r.goto("LIVING-ROOM")
    r.do("interact", "open", "trapdoor")  # from above it opens again, and no longer slams

    r.section("Platinum bar from the Loud Room and the painting")
    r.goto("LIVING-ROOM")
    for item in ("SWORD", "LAMP", "ROPE"):  # heavy: leave them at home for now
        r.do("drop", item)
    r.goto("LOUD-ROOM")
    r.do("interact", "say", "room", "echo")
    r.do("take", "BAR")
    r.goto("GALLERY")
    r.do("take", "PAINTING")
    r.deposit("BAR", "PAINTING")

    r.section("Skeleton key, bag of coins and the grating")
    r.goto("MAZE-5")
    r.do("take", "KEYS")
    r.do("take", "BAG-OF-COINS")
    r.goto("GRATING-ROOM")
    r.do("interact", "unlock", "grate")
    r.do("interact", "open", "grate")
    r.deposit("BAG-OF-COINS")

    r.section("The dam, the drained reservoir and Atlantis")
    r.goto("DAM-LOBBY")
    r.do("take", "MATCH")
    r.goto("MAINTENANCE-ROOM")
    r.do("take", "WRENCH")
    r.do("take", "SCREWDRIVER")
    r.do("interact", "push", "yellow button")
    r.goto("DAM-ROOM")
    r.do("interact", "turn", "bolt")
    while "LOW-TIDE" not in w.flags:  # the reservoir takes eight turns to drain
        r.do("wait")
    r.goto("RESERVOIR")
    r.do("take", "TRUNK")
    r.goto("ATLANTIS-ROOM")
    r.do("take", "TRIDENT")
    r.deposit("TRUNK", "TRIDENT")

    r.section("The dome, the torch, the Hades ritual and the crystal skull")
    r.goto("LIVING-ROOM")
    for item in ("GARLIC", "SCREWDRIVER", "KEYS", "WRENCH"):  # fewer loose items to carry
        r.do("drop", item)
    r.do("take", "ROPE")
    r.goto("DOME-ROOM")
    r.do("interact", "tie", "railing")
    r.goto("TORCH-ROOM")
    r.do("take", "TORCH")
    r.goto("NORTH-TEMPLE")
    r.do("take", "BELL")
    r.goto("SOUTH-TEMPLE")
    r.do("take", "CANDLES")
    r.do("take", "BOOK")
    r.goto("ENTRANCE-TO-HADES")
    r.do("interact", "ring", "bell")  # the bell turns red hot and the candles drop, unlit
    r.do("take", "CANDLES")
    r.do("interact", "light", "candles")
    r.do("interact", "read", "book")
    r.goto("LAND-OF-LIVING-DEAD")
    r.do("take", "SKULL")
    r.deposit("TORCH", "SKULL")

    r.section("The Egyptian coffin (praying at the altar carries it out of the temple)")
    r.goto("LIVING-ROOM")
    for item in ("BOOK", "CANDLES", "MATCH"):  # ritual finished (the bell is still cooling at Hades)
        r.do("drop", item)
    r.goto("EGYPT-ROOM")
    r.do("open", "COFFIN")
    r.do("take", "SCEPTRE")
    r.do("take", "COFFIN")
    r.goto("SOUTH-TEMPLE")
    r.do("interact", "pray", "altar")
    r.deposit("COFFIN")

    r.section("The rainbow and the pot of gold")
    r.goto("END-OF-RAINBOW")
    r.do("interact", "wave", "rainbow")
    r.do("take", "POT-OF-GOLD")
    r.deposit("POT-OF-GOLD", "SCEPTRE")

    r.section("The bat, the mine, the bracelet and the diamond")
    r.goto("LIVING-ROOM")
    for item in ("GARLIC", "SCREWDRIVER", "LAMP"):
        r.do("take", item)
    r.goto("BAT-ROOM")
    r.do("take", "JADE")
    r.goto("GAS-ROOM")
    r.do("take", "BRACELET")
    r.goto("DEAD-END-5")
    r.do("take", "COAL")
    r.goto("SHAFT-ROOM")
    for item in ("LAMP", "SCREWDRIVER", "COAL"):
        r.do("put", item, "RAISED-BASKET")
    r.do("interact", "lower", "basket")
    for item in ("GARLIC", "JADE", "BRACELET"):  # the narrow passage needs empty hands
        r.do("drop", item)
    r.goto("LOWER-SHAFT")
    for item in ("COAL", "SCREWDRIVER", "LAMP"):
        r.do("take", item)
    r.goto("MACHINE-ROOM")
    r.do("open", "MACHINE")
    r.do("put", "COAL", "MACHINE")
    r.do("close", "MACHINE")
    r.do("interact", "turn", "switch")
    r.do("open", "MACHINE")
    r.do("take", "DIAMOND")
    r.goto("LOWER-SHAFT")
    for item in ("DIAMOND", "LAMP", "SCREWDRIVER"):
        r.do("put", item, "RAISED-BASKET")
    r.do("interact", "raise", "basket")
    r.goto("SHAFT-ROOM")
    for item in ("DIAMOND", "GARLIC", "JADE", "BRACELET", "LAMP", "SCREWDRIVER"):
        r.do("take", item)
    r.deposit("DIAMOND", "JADE", "BRACELET")

    r.section("The river, the buoy and the buried scarab")
    r.goto("LIVING-ROOM")
    for item in ("GARLIC", "LAMP", "SCREWDRIVER"):
        r.do("drop", item)
    r.goto("RIVER-4")
    r.do("open", "BUOY")
    r.do("take", "EMERALD")
    r.goto("SANDY-BEACH")
    r.do("take", "SHOVEL")
    r.goto("SANDY-CAVE")
    for _ in range(3):
        r.do("interact", "dig", "sand")
    r.do("take", "SCARAB")
    r.deposit("EMERALD", "SCARAB")

    r.section("The egg, the thief and the sword")
    r.goto("LIVING-ROOM")
    r.do("drop", "SHOVEL")
    r.do("take", "SWORD")
    r.goto("UP-A-TREE")
    r.do("take", "EGG")
    r.meet_thief()
    r.do("interact", "give", "thief", "EGG")  # only the thief can open the egg
    r.do("interact", "attack", "thief")

    r.section("The chalice, the canary and the bauble")
    r.goto("TREASURE-ROOM")
    r.do("take", "CHALICE")
    r.goto("PATH")
    r.do("take", "CANARY")
    r.do("interact", "wind", "canary")
    r.do("take", "BAUBLE")
    r.deposit("EGG", "CANARY", "BAUBLE", "CHALICE")

    r.section("The ancient map, the Stone Barrow and the end")
    r.do("examine", "MAP")
    r.do("take", "MAP")  # a real player keeps the map: it is how they know where the barrow is
    r.goto("STONE-BARROW")
    r.do("move", "in")  # through the stone door: the adventure ends

    final = w.look()
    if not final.get("at_goal") or w._score() != 350 or w.dead:
        raise RuntimeError(f"Walkthrough did not finish: score {w._score()}, at_goal {final.get('at_goal')}, dead {w.dead}")
    return r.sections


def render(sections):
    out = ['"""Premade steps for a perfect run: 350 points and the adventure complete.',
           "",
           "Generated by make_walkthrough.py. Do not edit by hand: change the route there and run it again.",
           "Each step is (tool, *arguments) and maps onto the game's tools: move, take, drop, examine, open,",
           'close, put, wait and interact. Replay them with autoplay.py after seeding the dice with SEED.',
           '"""', "", f"SEED = {SEED}", "", "SECTIONS = ["]
    for label, steps in sections:
        out.append(f"    ({label!r}, [")
        out.extend(f"        {step!r}," for step in steps)
        out.append("    ]),")
    out += ["]", "", "STEPS = [step for _, steps in SECTIONS for step in steps]", ""]
    return "\n".join(out)


if __name__ == "__main__":
    text = render(record())
    if "--check" in sys.argv:
        if OUTPUT.read_text(encoding="utf-8") != text:
            sys.exit("walkthrough.py is out of date: run make_walkthrough.py")
        print("walkthrough.py is up to date")
    else:
        OUTPUT.write_text(text, encoding="utf-8")
        steps = sum(len(s) for _, s in record())
        print(f"Wrote {OUTPUT.name}: {steps} steps")
