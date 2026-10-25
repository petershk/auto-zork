"""Typed commands for manual play: "north", "take lamp", "put egg in case", "attack troll with sword".

The parser turns a line of text into one of the game's tools, so typing and clicking do exactly the same
thing. It is deliberately small: a few verbs and their synonyms, optional articles, and "with/to/at/in"
phrases. Type "help" in the game for the list.
"""
from puzzles import SCENES

DIRECTIONS = {
    "n": "north", "s": "south", "e": "east", "w": "west", "ne": "northeast", "nw": "northwest",
    "se": "southeast", "sw": "southwest", "u": "up", "d": "down",
    **{name: name for name in ("north", "south", "east", "west", "northeast", "northwest", "southeast", "southwest",
                               "up", "down", "in", "out", "land", "launch")},
}
ARTICLES = {"the", "a", "an", "my", "some"}
ITEM_VERBS = {  # typed verb -> (tool, preposition that introduces a second noun)
    "take": "take", "get": "take", "grab": "take", "pick": "take",
    "drop": "drop", "discard": "drop", "leave": "drop",
    "examine": "examine", "x": "examine", "inspect": "examine", "check": "examine",
    "open": "open_container", "close": "close_container", "shut": "close_container",
    "put": "put", "place": "put", "insert": "put", "store": "put",
}
SYNONYMS = {  # typed verb -> the puzzle verb the game knows
    "kill": "attack", "fight": "attack", "hit": "attack", "slay": "attack", "stab": "attack",
    "press": "push", "speak": "say", "shout": "say", "ignite": "light", "burn": "light", "fasten": "tie",
}
PUZZLE_VERBS = {"move", "lift", "unlock", "attack", "say", "give", "push", "turn", "tie", "wave", "ring", "light",
                "read", "ward", "dig", "wind", "lower", "raise", "pray"}
PREPOSITIONS = ("with", "using", "to", "at", "on", "onto", "into", "in", "inside", "from")

HELP = """You can type:
  north, south, east, west, northeast, northwest, southeast, southwest, up, down, in, out (or n, s, e, w, ne, nw, se, sw, u, d)
  look, inventory (i), score, wait (z), hint
  take lamp, drop lamp, examine map, read book, open window, close case, put egg in case
  attack troll with sword, give egg to thief, unlock grate with keys, turn bolt with wrench, wave sceptre
  say odysseus, push yellow button, ring bell, light candles, dig sand, tie rope to railing, pray
Names can be item names or IDs. Words like "the" are ignored. The same actions are available as buttons."""


class CommandError(ValueError):
    """The text could not be understood; the message says what to try."""


def _words(text):
    return [w for w in text.lower().replace(",", " ").split() if w not in ARTICLES]


def _split(words):
    """('a b', 'to', 'c d') for 'a b to c d'; (phrase, None, '') when there is no preposition."""
    for i, word in enumerate(words):
        if word in PREPOSITIONS and i > 0:
            return " ".join(words[:i]), word, " ".join(words[i + 1:])
    return " ".join(words), None, ""


def _targets(world, verb):
    return [t for a, t, _ in SCENES.get(world.position, []) if a == verb]


def parse(world, text):
    """Return (tool, args) for a line of text, or raise CommandError."""
    words = _words(text)
    if not words:
        raise CommandError("Type a command, or 'help' for the list.")
    verb, rest = words[0], words[1:]

    if verb in DIRECTIONS and not rest:
        return "move", (DIRECTIONS[verb],)
    if verb in ("go", "walk", "run", "head", "climb") and rest and rest[0] in DIRECTIONS:
        return "move", (DIRECTIONS[rest[0]],)
    if verb == "enter":
        return "move", ("in",)
    if verb == "move" and len(rest) == 1 and rest[0] in DIRECTIONS and rest[0] not in ("in", "out"):
        return "move", (DIRECTIONS[rest[0]],)

    if verb in ("help", "?", "commands"):
        return "help", ()
    if verb in ("look", "l") and (not rest or rest == ["around"]):
        return "observe", ()
    if verb in ("look", "l") and rest[0] in ("at", "in", "inside", "on"):
        rest = rest[1:]
        if not rest:
            raise CommandError("Look at what?")
        return "examine", (" ".join(rest),)
    if verb in ("inventory", "inv", "i") and not rest:
        return "inventory", ()
    if verb == "score" and not rest:
        return "score", ()
    if verb in ("wait", "z") and not rest:
        return "wait", ()
    if verb == "hint" and not rest:
        return "hint", ()
    if verb in ("reset", "restart", "quit"):
        raise CommandError("Use the Reset button to start a new game.")

    if verb == "pick" and rest and rest[0] == "up":
        rest = rest[1:]
    if verb in ITEM_VERBS:
        tool = ITEM_VERBS[verb]
        first, preposition, second = _split(rest)
        if not first:
            raise CommandError(f"{verb.capitalize()} what?")
        if tool == "put":
            if not second:
                raise CommandError(f"Put {first} where? For example: put {first} in case")
            return "put", (first, second)
        if tool == "take" and preposition == "from":
            return "take", (first,)  # "take coin from case": the item is found wherever it is
        if tool == "open_container" and verb == "open" and preposition == "with":
            return "open_container", (first,)
        return tool, (first,)

    verb = SYNONYMS.get(verb, verb)
    if verb in PUZZLE_VERBS:
        return "interact", _interact_args(world, verb, rest)

    raise CommandError(f"I don't know how to \"{words[0]}\". Type 'help' to see what you can do.")


def _interact_args(world, verb, rest):
    scene_targets = _targets(world, verb)
    first, preposition, second = _split(rest)
    if verb == "say":
        words = " ".join(rest)
        if preposition == "to" and second in scene_targets:
            return ("say", second, first)
        if not words:
            raise CommandError("Say what?")
        return ("say", scene_targets[0] if scene_targets else "room", words)
    if not first:
        if len(set(scene_targets)) == 1:
            return (verb, scene_targets[0], "")
        if not scene_targets:
            return (verb, "", "")  # nothing here to do it to: the game answers "Nothing happens"
        raise CommandError(f"{verb.capitalize()} what?")
    if preposition is None:
        return (verb, first, "")
    # two nouns: the one that is a feature of this room is the target, the other is what you use
    if second in scene_targets and first not in scene_targets:
        return (verb, second, first)
    if first in scene_targets:
        return (verb, first, second)
    # otherwise the preposition decides: "give egg TO thief" targets the thief, "attack troll WITH sword" the troll
    return (verb, second, first) if preposition in ("to", "at", "on", "onto", "into", "in", "inside") else (verb, first, second)


def execute(world, text):
    """Run a typed command on a world. Returns the game's result plus {'command_tool': ...}."""
    try:
        tool, args = parse(world, text)
    except CommandError as exc:
        return {"success": False, "error": str(exc), "command_tool": "error", **world.look()}
    if tool == "help":
        return {"success": True, "message": HELP, "command_tool": "help"}
    if tool == "interact":
        result = world.interact(*args)
    elif tool == "observe":
        result = world.observe()
    else:
        result = getattr(world, tool)(*args)
    return {**result, "command_tool": tool}
