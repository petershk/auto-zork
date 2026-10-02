"""Progress-based hints: the next unfinished goal, whatever room the player is standing in.

Each step reads only game state (flags, items taken, treasures deposited), so a hint
stays correct when the player wanders off, drops equipment or does things out of order.
"""
from dataclasses import dataclass
from typing import Callable


@dataclass(frozen=True)
class Step:
    key: str
    done: Callable
    text: Callable  # world -> str


# ---- state helpers -------------------------------------------------------------------

def got(w, item):
    """Has the player ever taken this item (or does the player hold it now)?"""
    return item in w.discovered_items or w._has(item)


def have(w, item):
    return w._has(item)


def flag(name):
    return lambda w: name in w.flags


def room_of(w, item, start=None):
    """Name of the room an item is in now, 'your inventory', or None if out of play."""
    location = start or w.item_locations.get(item)
    while location in w.items:
        location = w.item_locations[location]
    if location == "inventory":
        return "your inventory"
    return w.rooms[location]["name"] if location in w.rooms else None


def origin(w, item):
    return room_of(w, item, w.items[item]["location"])


def in_case(w, item):
    location = w.item_locations.get(item)
    while location in w.items:
        if location == "TROPHY-CASE":
            return True
        location = w.item_locations[location]
    return False


def treasures(w):
    return [i for i, d in w.items.items() if d["treasure"] and d["takeable"]]


def names(w, items):
    return ", ".join(w.items[i]["name"] for i in items)


def requirement(w, items, purpose):
    """Why the player cannot proceed yet for lack of equipment, or None when equipped."""
    never = [i for i in items if not got(w, i)]
    away = [i for i in items if got(w, i) and not have(w, i)]
    parts = []
    if never:
        parts.append("find " + "; ".join(f"{w.items[i]['name']} (first seen in {origin(w, i) or 'an unknown place'})" for i in never))
    if away:
        parts.append("pick up again " + "; ".join(f"{w.items[i]['name']} (now in {room_of(w, i) or 'an unknown place'})" for i in away))
    return f"To {purpose} you need to " + ", then ".join(parts) + "." if parts else None


def gated(items, purpose, ready):
    """Text for a step: the equipment requirement while unmet, otherwise `ready`."""
    return lambda w: requirement(w, items, purpose) or ready(w) if callable(ready) else requirement(w, items, purpose) or ready


# ---- the chain, in a sensible playing order -------------------------------------------

def _cyclops(w):
    lead = ("The trap door was barred behind you, so the way home is the cyclops passage. "
            if "trapdoor-slammed" in w.flags and "TRAP-DOOR" not in w.flags else "")
    return lead + "The cyclops fears the name of the hero who blinded his ancestor. Failing that, feed him lunch then water."


def _grate(w):
    if "leaves" not in w.flags:
        return "Leaves in the grating clearing (north of the forest path) hide a grating: move them."
    if "grate-unlocked" not in w.flags:
        return requirement(w, ["KEYS"], "unlock the grating") or "Unlock the grating with the skeleton key."
    return "The grating is unlocked: open it to go underground."


def _dam(w):
    if "gates-open" in w.flags:
        return "The sluice gates are open. The reservoir takes about eight turns to drain: wait, then cross it."
    need = requirement(w, ["WRENCH"], "drain the reservoir")
    if need:
        return need
    if "dam-enabled" not in w.flags:
        return "Push the yellow button in the Maintenance Room to enable the dam controls."
    return "The controls are enabled and you hold the wrench: turn the bolt in the Dam Room, then wait about eight turns."


def _machine(w):
    need = requirement(w, ["COAL", "SCREWDRIVER"], "make a diamond")
    if need:
        return need
    if "diamond-made" not in w.flags:
        return "In the Machine Room put the coal in the machine, close it, then turn the switch with the screwdriver."
    return "Open the machine and take the diamond."


def _egg(w):
    if not got(w, "EGG"):
        return "A bird's nest in the large tree (up from the forest path) holds a jewelled egg: take it."
    if "egg-opened" not in w.flags:
        return "You cannot open the egg yourself, but the thief is skilled at it. Find him underground and give him the egg."
    return "The egg is open: take the golden canary from it."


def _coffin(w):
    if have(w, "COFFIN"):
        return ("You hold the coffin, but it will not fit down the altar stairs. Pray at the altar to be carried "
                "out of the temple, then put it in the trophy case.")
    return "The gold coffin lies in the Egyptian Room, down from the temple. Take it, and the sceptre inside it."


STEPS = [
    Step("window", flag("KITCHEN-WINDOW"),
         lambda w: "Find a way into the white house: the kitchen window on the back (east) side can be opened."),
    Step("gear", lambda w: got(w, "LAMP") and got(w, "SWORD"),
         lambda w: "Take the brass lantern and the sword from the living room, west of the kitchen."),
    Step("trapdoor", lambda w: "TRAP-DOOR" in w.flags or "trapdoor-slammed" in w.flags,
         lambda w: "In the living room, move the rug and open the trapdoor beneath it to reach the cellar."),
    Step("troll", lambda w: w.troll_strength == 0 or "TROLL-FLAG" in w.flags,
         gated(["SWORD"], "fight the troll that blocks the Troll Room passages below the cellar",
               "A troll blocks the passages in the Troll Room. Attack it while holding the sword.")),
    Step("cyclops", flag("CYCLOPS-FLAG"), _cyclops),
    Step("bar", lambda w: got(w, "BAR"),
         lambda w: "In the Loud Room say 'echo' to quiet the roar, then take the platinum bar."),
    Step("shaft", flag("shaft-supplied"),
         gated(["LAMP"], "earn the mine-shaft bonus", "Put the lantern in the basket in the Shaft Room and lower it.")),
    Step("grate", flag("GRATE"), _grate),
    Step("dam", flag("LOW-TIDE"), _dam),
    Step("trunk", lambda w: got(w, "TRUNK"),
         lambda w: "The reservoir has drained: take the trunk of jewels from the reservoir bed."),
    Step("dome", flag("DOME-FLAG"),
         gated(["ROPE"], "descend from the dome", "Tie the rope to the railing in the Dome Room.")),
    Step("coffin", lambda w: in_case(w, "COFFIN"), _coffin),
    Step("rainbow", flag("RAINBOW-FLAG"),
         gated(["SCEPTRE"], "make the rainbow solid", "Wave the sceptre at the rainbow near Aragain Falls.")),
    Step("pot", lambda w: got(w, "POT-OF-GOLD"),
         lambda w: "The rainbow is solid: the pot of gold waits at its end."),
    Step("hades", flag("LLD-FLAG"),
         gated(["BELL", "CANDLES", "MATCH", "BOOK"], "open the gate to Hades",
               "At the Entrance to Hades ring the bell (it drops any candles you carry), pick the candles up and light them with the match, then read the book at once.")),
    Step("skull", lambda w: got(w, "SKULL"),
         lambda w: "The gate is open: the crystal skull lies in the Land of the Living Dead."),
    Step("bat", lambda w: got(w, "JADE"),
         gated(["GARLIC"], "get past the vampire bat", "Carry the garlic into the Bat Room, then take the jade figurine.")),
    Step("diamond", lambda w: got(w, "DIAMOND"), _machine),
    Step("scarab", lambda w: got(w, "SCARAB"),
         gated(["SHOVEL"], "dig up the scarab", "Dig in the sand of the Sandy Cave three times.")),
    Step("egg", lambda w: got(w, "CANARY"), _egg),
    Step("chalice", lambda w: got(w, "CHALICE"),
         lambda w: "The chalice in the Treasure Room is guarded while the thief is there: defeat him with the sword or wait until he leaves."),
    Step("bauble", flag("bauble-created"),
         gated(["CANARY"], "attract the songbird", "Wind the canary outdoors, in the forest, to receive a bauble.")),
]


EQUIPMENT = ("LAMP", "SWORD", "WRENCH", "SCREWDRIVER", "ROPE", "KEYS", "SHOVEL", "GARLIC", "COAL",
             "BELL", "CANDLES", "MATCH", "BOOK", "BOTTLE", "LUNCH", "SANDWICH-BAG")


def left_behind(w):
    """Equipment the player once held that now lies elsewhere, with where it is."""
    away = [(i, room_of(w, i)) for i in EQUIPMENT if i in w.items and got(w, i) and not have(w, i) and room_of(w, i)]
    return ("Equipment you set down: " + "; ".join(f"{w.items[i]['name']} in {room}" for i, room in away) + ".") if away else None


def carrying_treasure(w):
    held = [i for i in treasures(w) if have(w, i) and not in_case(w, i) and i != "COFFIN"]
    return f"You are carrying treasure ({names(w, held)}): put it in the trophy case in the living room." if held else None


def remaining_treasure(w):
    missing = [i for i in treasures(w) if not in_case(w, i)]
    unfound = [i for i in missing if not got(w, i)]
    if not missing:
        return None
    text = f"{len(missing)} treasures are not yet in the trophy case."
    if unfound:
        text += " Not yet found: " + ", ".join(w.items[i]["name"] for i in unfound) + ". Explore rooms you have not visited."
    return text


def progress_hints(w, limit=3):
    """Next goal first, then up to limit-1 other open leads, from game state alone."""
    if "ADVENTURE-COMPLETE" in w.flags:
        return ["Your adventure is complete. Reset to play again."]
    messages = []
    basics_done = all(step.done(w) for step in STEPS[:3])
    deposit = carrying_treasure(w) if basics_done else None
    if deposit:
        messages.append(deposit)
    for step in STEPS:
        if not step.done(w):
            messages.append(step.text(w))
    if not messages:
        messages.append("All known puzzle chains are complete.")
    extra = remaining_treasure(w)
    if extra and len(messages) < limit:
        messages.append(extra)
    messages = messages[:limit]
    if "WON-FLAG" in w.flags:
        messages.insert(0, "Examine the ancient map in the trophy case, then walk to the Stone Barrow "
                           "(southwest from West of House) and go in through the stone door.")
    reminder = left_behind(w)
    if reminder:
        messages.append(reminder)
    return messages
