"""Extract the Zork I room graph from the MIT-licensed source snapshot.

This is a navigation adaptation: conditional exits are open and procedural
exits are resolved below. Item definitions retain original initial locations.
"""
import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent
SOURCE = ROOT / "zork1_source.zil"
DIRECTIONS = {
    "NORTH": "north", "SOUTH": "south", "EAST": "east", "WEST": "west",
    "NE": "northeast", "NW": "northwest", "SE": "southeast", "SW": "southwest",
    "UP": "up", "DOWN": "down", "IN": "in", "OUT": "out", "LAND": "land",
}
PROCEDURAL_EXITS = {
    ("GRATING-CLEARING", "down"): "GRATING-ROOM",
    ("LIVING-ROOM", "down"): "CELLAR",
    ("STUDIO", "up"): "KITCHEN",
    ("MAZE-2", "down"): "MAZE-4",
    ("MAZE-7", "down"): "DEAD-END-1",
    ("MAZE-9", "down"): "MAZE-11",
    ("MAZE-12", "down"): "MAZE-5",
}
# RIVER-LAUNCH table in 1actions.zil. Treat launching the already-provided
# boat as an exit so river rooms are accessible without an inventory system.
BOAT_LAUNCHES = {
    "DAM-BASE": "RIVER-1", "WHITE-CLIFFS-NORTH": "RIVER-3",
    "WHITE-CLIFFS-SOUTH": "RIVER-4", "SHORE": "RIVER-5",
    "SANDY-BEACH": "RIVER-4", "RESERVOIR-SOUTH": "RESERVOIR",
    "RESERVOIR-NORTH": "RESERVOIR", "STREAM-VIEW": "IN-STREAM",
}

# These rooms use action routines instead of static LDESC strings in ZIL.
# Their descriptions below are original prose for this navigation adaptation.
DESCRIPTIONS = {
    "UP-A-TREE": "A sturdy branch supports you above the forest path. A bird's nest rests nearby, and the house can be glimpsed through the leaves.",
    "GRATING-CLEARING": "Sunlight reaches a small clearing between tall trees. An uncovered, open grating leads down into the underground maze.",
    "DEEP-CANYON": "A deep canyon divides the underground rock. Narrow trails follow the edge, and the distant sound of water rises from below.",
    "ENTRANCE-TO-HADES": "A sinister gateway opens beneath the earth. Cold air drifts out of the passage to the Land of the Dead; its supernatural barrier is absent here.",
    "TORCH-ROOM": "A marble pedestal stands beneath a lofty stone dome. An ivory torch illuminates the chamber and the passages beyond.",
    "WEST-OF-HOUSE": "A white house stands at the edge of an open field. Its front door is boarded shut. Forest paths lead around the building, and a stone barrow lies farther away.",
    "EAST-OF-HOUSE": "You stand behind the white house. An open kitchen window offers a way inside; woodland surrounds the yard.",
    "KITCHEN": "Light falls through an open window onto a worn kitchen table. A doorway leads to the living room, and stairs rise toward the attic.",
    "LIVING-ROOM": "A trophy case and a large rug furnish the living room. The trapdoor beneath the rug is open, revealing stairs into the cellar. A western passage is open for this exploration.",
    "CELLAR": "Cool air settles in a rough stone cellar beneath the house. Stairs lead back up; passages continue into the underground tunnels.",
    "GRATING-ROOM": "Tree roots wind through the ceiling of a low chamber. An open metal grating admits daylight from the clearing overhead.",
    "CYCLOPS-ROOM": "A cavernous room bears the marks of an enormous inhabitant. The passages and staircase are unguarded in this navigation version.",
    "RESERVOIR-SOUTH": "You stand on the southern edge of a vast underground reservoir. A dam holds back the water, while an exposed crossing leads north.",
    "RESERVOIR": "The reservoir floor stretches between steep rocky banks. Water has receded enough to expose routes toward either shore and the stream.",
    "RESERVOIR-NORTH": "On the reservoir's northern bank, damp rock shelves overlook the drained basin. A passage leads deeper into the ancient underground complex.",
    "MIRROR-ROOM-1": "A tall mirror catches the dim light of a cold stone chamber. Narrow passages disappear around its edges.",
    "MIRROR-ROOM-2": "A great mirror dominates this chamber, doubling its stone walls and shadowy passage entrances.",
    "LOUD-ROOM": "The thunder of falling water fills a broad chamber. The noise makes every footstep vanish beneath it; the routes remain safe to explore.",
    "DOME-ROOM": "You stand near the top of a soaring stone dome. A secured descent leads down toward the torch room.",
    "DAM-ROOM": "You stand atop Flood Control Dam #3. Old controls overlook the reservoir on one side and the river below on the other.",
    "ARAGAIN-FALLS": "The Frigid River plunges into a roaring waterfall. A solid rainbow bridge spans the mist toward the opposite bank.",
    "BAT-ROOM": "Bats rustle in the dark ceiling of a mine chamber. They leave the passageways clear for this expedition.",
    "MACHINE-ROOM": "An immense abandoned machine occupies this chamber at the bottom of the mine. Its silent controls hint at a lost industrial purpose.",
}


def text_property(block, name):
    match = re.search(r'\(' + name + r'\s+"((?:\\.|[^"\\])*)"\s*\)', block, re.S)
    if not match:
        return None
    return " ".join(match.group(1).replace('\\"', '"').replace("|", "\n").split())


def number_property(block, name, default=0):
    match = re.search(r"\(" + name + r"\s+([0-9]+)\)", block)
    return int(match.group(1)) if match else default


def build_items(source, rooms):
    """Import placed objects, including their containers, excluding NPCs."""
    section = source.split("<ROOM", 1)[0]
    matches = list(re.finditer(r"<OBJECT\s+([A-Z0-9-]+)", section))
    objects = {}
    for index, match in enumerate(matches):
        end = matches[index + 1].start() if index + 1 < len(matches) else len(section)
        block = re.sub(r';"(?:\\.|[^"\\])*"', '', section[match.start():end])
        location = re.search(r"\(IN\s+([A-Z0-9-]+)\)", block)
        flags_match = re.search(r"\(FLAGS\s+([^)]*)\)", block)
        flags = set(flags_match.group(1).split()) if flags_match else set()
        name = text_property(block, "DESC")
        if not name or "ACTORBIT" in flags:
            continue
        synonyms = re.search(r"\(SYNONYM\s+([^)]*)\)", block)
        objects[match.group(1)] = {
            "name": name, "location": location.group(1) if location else "offstage",
            "hidden": "INVISIBLE" in flags,
            "description": text_property(block, "FDESC") or text_property(block, "LDESC") or f"You see a {name}.",
            "readable_text": text_property(block, "TEXT"),
            "aliases": [s.lower() for s in synonyms.group(1).split()] if synonyms else [],
            "takeable": "TAKEBIT" in flags,
            "container": "CONTBIT" in flags,
            "surface": "SURFACEBIT" in flags,
            "open": "OPENBIT" in flags or "SURFACEBIT" in flags,
            "transparent": "TRANSBIT" in flags,
            "treasure": bool(re.search(r"\(TVALUE\s+[1-9][0-9]*\)", block)),
            "size": number_property(block, "SIZE", 5),
            "capacity": number_property(block, "CAPACITY"),
            "discovery_points": number_property(block, "VALUE"),
            "deposit_points": number_property(block, "TVALUE"),
        }

    def placed(item_id, seen=None):
        seen = set() if seen is None else seen
        if item_id in seen or item_id not in objects:
            return False
        seen.add(item_id)
        location = objects[item_id]["location"]
        return location in rooms or placed(location, seen)

    return {item_id: item for item_id, item in objects.items() if placed(item_id) or item_id in ("DIAMOND", "BAUBLE")}


def build():
    source = SOURCE.read_text(encoding="utf-8")
    matches = list(re.finditer(r"<ROOM\s+([A-Z0-9-]+)", source))
    rooms = {}
    for index, match in enumerate(matches):
        room_id = match.group(1)
        end = matches[index + 1].start() if index + 1 < len(matches) else len(source)
        block = source[match.start():end]
        # Comments and routine bodies must not contribute room properties.
        block = re.sub(r';"(?:\\.|[^"\\])*"', '', block)
        block = block.split("<ROUTINE", 1)[0]
        headings = re.findall(r'"SUBTITLE ([^"\n]+)"', source[:match.start()])
        area = headings[-1].title()
        exits = {}
        conditions = {}
        for exit_match in re.finditer(r"\((NORTH|SOUTH|EAST|WEST|NE|NW|SE|SW|UP|DOWN|IN|OUT|LAND)\s+TO\s+([A-Z0-9-]+)", block):
            # FALSE-FLAG is a permanent prohibition, not a puzzle gate.
            if re.match(r"\s+IF\s+FALSE-FLAG\b", block[exit_match.end():]):
                continue
            direction, target = exit_match.groups()
            exits[DIRECTIONS[direction]] = target
            condition = re.match(r"\s+IF\s+([A-Z0-9-]+)(?:\s+IS\s+(OPEN))?", block[exit_match.end():])
            if condition:
                conditions[DIRECTIONS[direction]] = condition.group(1)
        for direction in re.findall(r"\((NORTH|SOUTH|EAST|WEST|NE|NW|SE|SW|UP|DOWN|IN|OUT|LAND)\s+PER\s+[A-Z0-9-]+", block):
            canonical = DIRECTIONS[direction]
            exits[canonical] = PROCEDURAL_EXITS[(room_id, canonical)]
        if room_id in BOAT_LAUNCHES:
            exits["launch"] = BOAT_LAUNCHES[room_id]
        description = DESCRIPTIONS.get(room_id) or text_property(block, "LDESC")
        if not description:
            raise ValueError(f"Missing description: {room_id}")
        rooms[room_id] = {
            "name": text_property(block, "DESC"), "description": description,
            "area": area, "exits": exits, "conditions": conditions,
            "discovery_points": number_property(block, "VALUE"),
        }
    for room_id, room in rooms.items():
        for target in room["exits"].values():
            if target not in rooms:
                raise ValueError(f"Unknown destination: {room_id} -> {target}")
    data = {
        "title": "Zork I navigation maze", "start": "WEST-OF-HOUSE", "goal": "STONE-BARROW",
        "source": "https://github.com/historicalsource/zork1/blob/master/1dungeon.zil",
        "boat_source": "https://github.com/historicalsource/zork1/blob/master/1actions.zil#L2597",
        "license": "ZORK_LICENSE.txt",
        "adaptation": "Stateful puzzle adaptation. Deterministic encounters; no darkness, death, random theft, or boat inflation. Original carrying/treasure points; 13-point shaft bonus adapted to lowering the lantern. Collect treasures; discover the final route through the map at 350 points. See PUZZLES.md.",
        "rules_sources": ["https://github.com/historicalsource/zork1/blob/master/gverbs.zil", "https://github.com/historicalsource/zork1/blob/master/gglobals.zil", "https://github.com/historicalsource/zork1/blob/master/zork1.zil"],
        "rooms": rooms,
        "items": build_items(source, rooms),
    }
    (ROOT / "zork_map.json").write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    print(f"Generated {len(rooms)} rooms, {sum(len(r['exits']) for r in rooms.values())} exits, and {len(data['items'])} placed items/fixtures")


if __name__ == "__main__":
    build()
