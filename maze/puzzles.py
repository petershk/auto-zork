"""Deterministic adaptations of Zork I's puzzles (see ZORK_LICENSE.txt).

This is a teaching adventure, not an implementation of the ZIL parser or
random combat/death system. All actions are checked by the world, not the LLM.
"""
from hints import progress_hints

# Each room exposes local actions, rather than requiring guessed API verbs.
SCENES = {
    "SHAFT-ROOM": [("lower", "basket", "Put supplies in RAISED-BASKET and lower it before taking the narrow passage."), ("raise", "basket", "Raise supplies from the lower shaft.")],
    "LOWER-SHAFT": [("lower", "basket", "Lower the supply basket."), ("raise", "basket", "Send supplies up the shaft before squeezing out.")],
    "EAST-OF-HOUSE": [("open", "window", "The kitchen window is slightly ajar.")],
    "KITCHEN": [("open", "window", "The window leads outside.")],
    "LIVING-ROOM": [("move", "rug", "The rug conceals something."), ("open", "trapdoor", "Lift the rug first.")],
    "CELLAR": [("open", "trapdoor", "The trapdoor leads upstairs.")],
    "TROLL-ROOM": [("attack", "troll", "A troll guards the passages. You need a weapon.")],
    "CYCLOPS-ROOM": [("say", "cyclops", "The cyclops fears the name of the hero who blinded his ancestor."), ("give", "cyclops", "He is hungry; food followed by water will put him to sleep.")],
    "GRATING-CLEARING": [("move", "leaves", "Leaves hide a grating."), ("unlock", "grate", "A skeleton key fits the lock."), ("open", "grate", "The grating leads underground.")],
    "GRATING-ROOM": [("unlock", "grate", "A skeleton key fits the lock."), ("open", "grate", "The grating leads outside.")],
    "DAM-ROOM": [("turn", "bolt", "The bolt requires a wrench; first enable the controls in Maintenance Room.")],
    "MAINTENANCE-ROOM": [("push", "yellow button", "Enable the dam controls."), ("push", "brown button", "Disable the dam controls.")],
    "DOME-ROOM": [("tie", "railing", "A rope can secure the descent.")],
    "ARAGAIN-FALLS": [("wave", "rainbow", "A royal sceptre might make the rainbow solid.")],
    "END-OF-RAINBOW": [("wave", "rainbow", "A royal sceptre might make the rainbow solid.")],
    "SOUTH-TEMPLE": [("pray", "altar", "The stairs are too narrow for the coffin, but praying at the altar carries you out of the temple with everything you hold.")],
    "ENTRANCE-TO-HADES": [("ring", "bell", "Ring the bell, light the candles, then read the book within six turns."), ("light", "candles", "The ritual requires burning candles."), ("read", "book", "Complete the ritual before the bell's resonance fades.")],
    "BAT-ROOM": [("ward", "bat", "Carried garlic keeps the bat away.")],
    "MACHINE-ROOM": [("turn", "switch", "Put coal in the machine, close it, then turn its switch with a screwdriver.")],
    "SANDY-CAVE": [("dig", "sand", "A shovel can uncover buried treasure. Dig carefully three times.")],
    "TREASURE-ROOM": [("give", "thief", "The thief can open a carried jeweled egg."), ("attack", "thief", "A sword defeats this deterministic encounter and frees the chalice.")],
    "FOREST-1": [("wind", "canary", "Wind the mechanical canary outdoors to attract a songbird.")],
    "FOREST-2": [("wind", "canary", "Wind the mechanical canary outdoors to attract a songbird.")],
    "FOREST-3": [("wind", "canary", "Wind the mechanical canary outdoors to attract a songbird.")],
    "PATH": [("wind", "canary", "Wind the mechanical canary outdoors to attract a songbird.")],
    "LOUD-ROOM": [("say", "room", "The room repeats your words. Say echo to quiet it.")],
}


ALLOWED_ACTIONS = ("open", "move", "lift", "unlock", "attack", "say", "give", "push", "turn", "tie", "wave", "ring", "light", "read", "ward", "dig", "wind", "lower", "raise", "pray")


class PuzzleWorld:
    def puzzle_reset(self):
        self.hints_used = 0
        self.flags = set()
        self.dig_count = 0
        self.bell_turn = None
        self.ritual_turn = None       # turn the flames flickered; the book can be read for two turns after
        self.bell_cool_turn = None    # when the red-hot bell can be picked up again
        self.dam_event = None         # "drain" or "fill", due at dam_turn
        self.dam_turn = None
        self.story_events = []
        self.hidden_items = {i for i, item in self.items.items() if item.get("hidden")}
        self.endgame_announcement = None
        self.hidden_items.add("MAP")
        self.item_locations["LOWERED-BASKET"] = "offstage"
        self.thief_position = "TREASURE-ROOM" if self.puzzles else None
        self.thief_events = []
        self.combat_reset()

    def advance_clocks(self):
        """Timed events from the original game's interrupt queue (the dam and the hot bell)."""
        if not self.puzzles:
            return
        if self.dam_event and self.turns >= self.dam_turn:
            event, self.dam_event, self.dam_turn = self.dam_event, None, None
            banks = ("RESERVOIR-NORTH", "RESERVOIR-SOUTH")
            if event == "drain":
                self.flags.add("LOW-TIDE")
                self.hidden_items.discard("TRUNK")
                if self.position in banks:
                    self.story_events.append("The water level is now quite low here and you could easily cross over to the other side.")
                elif self.position == "DEEP-CANYON":
                    self.story_events.append("The roar of rushing water is quieter now.")
            else:
                self.flags.discard("LOW-TIDE")
                if self.item_locations.get("TRUNK") == "RESERVOIR":
                    self.hidden_items.add("TRUNK")
                if self.position == "RESERVOIR":
                    self.dead = True
                    self.story_events.append("You are lifted up by the rising river! You try to swim, but the currents are too strong. "
                                             "You come closer, closer to the awesome structure of Flood Control Dam #3. The dam beckons to you. "
                                             "You tumble over the dam toward your certain doom among the rocks at its base.")
                elif self.position == "DEEP-CANYON":
                    self.story_events.append("A sound, like that of flowing water, starts to come from below.")
                elif self.position == "LOUD-ROOM":
                    self.position = self._random.choice(["DAMP-CAVE", "ROUND-ROOM", "DEEP-CANYON"])
                    self.visited.add(self.position)
                    self.story_events.append("All of a sudden, an alarmingly loud roaring sound fills the room. Filled with fear, you scramble away.")
                elif self.position in banks:
                    self.story_events.append("You notice that the water level has risen to the point that it is impossible to cross.")
        if self.bell_cool_turn is not None and self.turns >= self.bell_cool_turn:
            self.bell_cool_turn = None
            self.item_locations["BELL"] = "ENTRANCE-TO-HADES"
            if self.position == "ENTRANCE-TO-HADES":
                self.story_events.append("The bell appears to have cooled down.")

    def on_enter(self, source, direction, destination):
        """Things that happen when the player arrives somewhere (the original rooms' M-ENTER code)."""
        if not self.puzzles:
            return
        if destination == "CELLAR" and "TRAP-DOOR" in self.flags and "trapdoor-slammed" not in self.flags:
            self.flags.discard("TRAP-DOOR")
            self.flags.add("trapdoor-slammed")
            self.story_events.append("The trap door crashes shut, and you hear someone barring it.")
        if source == "STUDIO" and direction == "up" and "TRAP-DOOR" not in self.flags:
            self.flags.discard("trapdoor-slammed")  # the next trip down will slam it again
        if destination == "TREASURE-ROOM" and "thief-defeated" not in self.flags and not self.dead:
            if self.thief_position != "TREASURE-ROOM":
                self.story_events.append("You hear a scream of anguish as you violate the robber's hideaway. "
                                         "Using passages unknown to you, he rushes to its defense.")
                self.thief_position = "TREASURE-ROOM"

    def thief_present(self):
        return self.puzzles and "thief-defeated" not in self.flags and self.thief_position == self.position

    def advance_thief(self):
        """Walk one directed underground passage every four game turns."""
        self.thief_events = []
        if not self.puzzles or "thief-defeated" in self.flags or self.turns % 4:
            return
        if self.thief_position == "TREASURE-ROOM" == self.position:
            return  # he will not leave his lair while you are standing in it
        old = self.thief_position
        candidates = list(dict.fromkeys(
            room for room in self.rooms[old]["exits"].values()
            if self.rooms[room]["area"] not in ("House", "Forest And Outside Of House", "River Area")
            and room != "STONE-BARROW" and room != old
        ))
        if not candidates:
            return
        self.thief_position = self._random.choice(candidates)
        if old == self.position:
            self.thief_events.append("The thief slips away into another passage.")
        elif self.thief_position == self.position:
            self.thief_events.append("A suspicious thief slips into the room.")

    def puzzle_actions(self):
        return list(ALLOWED_ACTIONS) if self.puzzles else []

    def nearby_features(self):
        targets = list(dict.fromkeys(t for _, t, _ in SCENES.get(self.position, [])))
        targets = [target for target in targets if target != "thief"]
        if self.thief_present():
            targets.append("thief")
        if self.troll_strength == 0:
            targets = [target for target in targets if target != "troll"]
        targets = [t for t in targets if t not in ("bell", "candles", "book", "canary")]
        if self.position == "LIVING-ROOM" and "rug" not in self.flags:
            targets.remove("trapdoor")
        if self.position == "GRATING-CLEARING" and "leaves" not in self.flags:
            targets.remove("grate")
        return targets if self.puzzles else []

    def hint(self):
        """Optional assistance is counted separately and does not consume a turn.

        Hints follow the player's progress (flags, items taken, treasures deposited), not the room.
        """
        self.hints_used += 1
        self.revision += 1
        if not self.puzzles:
            return {"success": True, "hint": "Explore the room, examine objects, and read anything you find.", **self.look()}
        first, *rest = progress_hints(self)
        text = first + (" Other open leads: " + " | ".join(rest) if rest else "")
        return {"success": True, "hint": text, **self.look()}

    def room_description(self):
        descriptions = {
            "EAST-OF-HOUSE": "Behind the white house is a small kitchen window, slightly ajar.",
            "KITCHEN": "You are in the kitchen of the white house. A table stands here. A doorway leads west and stairs lead upward. A window faces the yard.",
            "LIVING-ROOM": "You are in the living room. There is a trophy case, a large rug, and a nailed-shut door to the west.",
            "GRATING-CLEARING": "You are in a clearing. A pile of leaves lies on the ground.",
            "GRATING-ROOM": "Tree roots wind through the ceiling. A metal grating is overhead.",
            "CYCLOPS-ROOM": "An enormous cyclops occupies this cavern. A staircase leads upward.",
            "TROLL-ROOM": "A menacing troll guards the passages through this chamber.",
            "RESERVOIR-SOUTH": "You stand on the southern bank of an underground reservoir.",
            "RESERVOIR-NORTH": "You stand on the northern bank of an underground reservoir.",
            "RESERVOIR": "Steep rocky banks surround the reservoir basin.",
            "DOME-ROOM": "You stand high in a stone dome. A railing borders a steep drop to the chamber below.",
            "ARAGAIN-FALLS": "The river plunges into a waterfall. A rainbow shines in the mist.",
            "ENTRANCE-TO-HADES": "A supernatural barrier and restless spirits guard the entrance to the Land of the Dead.",
            "BAT-ROOM": "A large bat hangs from the ceiling of this mine chamber.",
        }
        text = descriptions.get(self.position, self.rooms[self.position]["description"])
        if self.position == "TROLL-ROOM":
            if self.troll_strength == 0:
                text = "The troll is gone. The passages through this chamber are clear."
            elif self.troll_strength < 0:
                text = "An unconscious troll is sprawled on the floor. The passages are open."
        if self.position == "EAST-OF-HOUSE" and "KITCHEN-WINDOW" in self.flags:
            text = "Behind the white house is an open kitchen window."
        if self.position == "LIVING-ROOM" and "rug" in self.flags:
            text += " A trapdoor is exposed beneath the moved rug."
        if self.thief_present():
            text += " A suspicious thief carrying a large bag watches you closely."
        return text

    def gate_error(self, direction):
        if not self.puzzles:
            return None
        if self.position == "CELLAR" and direction == "up" and "TRAP-DOOR" not in self.flags:
            return "The door is locked from above."
        if self.position == "STUDIO" and direction == "up":
            held = self._carried_items()
            if not held:
                return "Going up empty-handed is a bad idea."
            if len(held) > 2 or self.item_locations.get("LAMP") != "inventory":
                return "You can't get up there with what you're carrying."
        condition = self.rooms[self.position].get("conditions", {}).get(direction)
        if condition == "WON-FLAG":
            return None if "WON-FLAG" in self.flags else "There is no passage that way."
        if condition == "COFFIN-CURE":
            if self._inside("COFFIN", "inventory"):
                return "You haven't a prayer of getting the coffin down there."
            return None
        if condition == "EMPTY-HANDED":
            return "You must drop your possessions before squeezing through." if self._carried_items() else None
        if condition == "DEFLATE":
            return None  # Boat inflation is not simulated in this adaptation.
        if condition and condition not in self.flags:
            return "The passage is blocked."
        if self.position == "LIVING-ROOM" and direction == "down" and "TRAP-DOOR" not in self.flags:
            return "There is no open passage down."
        if self.position == "GRATING-CLEARING" and direction == "down" and "GRATE" not in self.flags:
            return "There is no open passage down."
        if self.position == "BAT-ROOM" and not self._has("GARLIC"):
            return "The bat blocks your path."
        return None

    def _has(self, item):
        return item in self.items and self._visible(item, "inventory", True)

    def interact(self, action, target, item=""):
        stopped = self._dead_result()
        if stopped:
            return stopped
        # Interacting with the observed thief gets a chance before he moves.
        self._tick(advance_thief=target.strip().lower() not in ("thief", "the thief"))
        before = self._score()
        try:
            if not self.puzzles:
                raise ValueError("Puzzles are disabled in navigation mode.")
            action, target = action.strip().lower(), target.strip().lower()
            target = " ".join(target.replace("-", " ").split())
            if target.startswith("the "):
                target = target[4:]
            if target == "trap door":
                target = "trapdoor"
            if action == "lift" and target in ("rug", "leaves"):
                action = "move"
            actions = [(a, t) for a, t, _ in SCENES.get(self.position, []) if t != "thief"]
            if self.thief_present():
                actions.extend([("give", "thief"), ("attack", "thief")])
            if (action, target) not in actions:
                if action == "read":
                    # Reading something readable is the same as examining it.
                    try:
                        found = self._find_item(target)
                    except ValueError:
                        found = None
                    if found and self.items[found]["readable_text"]:
                        return {"success": True, "item": {**self._item_view(found), "description": self.items[found]["description"],
                                                          "readable_text": self.items[found]["readable_text"]}, **self.look()}
                if target == "thief":
                    raise ValueError("There is no thief here.")
                elsewhere = [self.rooms[room]["name"] for room, scenes in SCENES.items()
                             if room in self.visited and (action, target) in [(a, t) for a, t, _ in scenes]]
                raise ValueError("Nothing happens with that command here."
                                 + (f" You have seen a place where that works: {', '.join(elsewhere)}." if elsewhere else
                                    " Interactions belong to specific places; check each room's description and exits."))
            def need(item_id):
                if not self._has(item_id):
                    raise ValueError("You do not have the necessary equipment accessible.")
            def flag(value):
                self.flags.add(value)
            if target == "basket":
                self.item_locations["RAISED-BASKET"] = "LOWER-SHAFT" if action == "lower" else "SHAFT-ROOM"
                if action == "lower" and self._inside("LAMP", "LOWER-SHAFT"):
                    flag("shaft-supplied")
                message = f"The basket is {'lowered' if action == 'lower' else 'raised'} with all its contents."
            elif target == "window":
                flag("KITCHEN-WINDOW")
                message = "The kitchen window opens."
            elif target in ("rug", "leaves"):
                flag(target)
                if target == "rug": self.hidden_items.discard("TRAP-DOOR")
                message = f"You move the {target}, revealing a {'trapdoor' if target == 'rug' else 'grating'}."
            elif target == "trapdoor":
                if self.position == "CELLAR":
                    raise ValueError("The door is locked from above.")
                if self.position == "LIVING-ROOM" and "rug" not in self.flags:
                    raise ValueError("You cannot reach a trapdoor here.")
                flag("TRAP-DOOR")
                message = "The trapdoor opens."
            elif target == "grate":
                if self.position == "GRATING-CLEARING" and "leaves" not in self.flags:
                    raise ValueError("You cannot reach a grating here.")
                if action == "unlock":
                    need("KEYS"); flag("grate-unlocked")
                    message = "The skeleton key unlocks the grate."
                else:
                    if "grate-unlocked" not in self.flags:
                        raise ValueError("The grate is locked.")
                    flag("GRATE"); message = "The grate opens."
            elif target == "altar":
                self.position = "FOREST-1"
                self.moves_made += 1
                self.visited.add("FOREST-1")
                message = "Time passes... and you find yourself in a forest."
            elif target == "troll" and action == "attack":
                message = self.attack_troll(item)
            elif target == "thief" and action == "attack":
                need("SWORD")
                flag("TROLL-FLAG" if target == "troll" else "thief-defeated")
                if target == "thief":
                    self.thief_position = None
                message = f"Your sword defeats the {target}; the encounter is deterministic in this adaptation."
            elif target == "cyclops":
                if action == "say":
                    if item.strip().lower() not in ("odysseus", "ulysses"):
                        raise ValueError("That name does not frighten the cyclops.")
                    flag("MAGIC-FLAG"); flag("CYCLOPS-FLAG")
                    message = "The terrified cyclops breaks through the east wall and flees."
                else:
                    supplied = self._find_item(item)
                    need(supplied)
                    if supplied == "LUNCH":
                        flag("cyclops-fed")
                    elif supplied == "WATER" and "cyclops-fed" in self.flags:
                        flag("CYCLOPS-FLAG")
                    else:
                        raise ValueError("The cyclops refuses your offering.")
                    self.item_locations[supplied] = "offstage"
                    message = "The cyclops eats." if supplied == "LUNCH" else "The cyclops drinks and falls asleep."
            elif target.endswith("button"):
                if target == "yellow button": flag("dam-enabled")
                else: self.flags.discard("dam-enabled")
                message = "The dam controls are enabled." if "dam-enabled" in self.flags else "The dam controls are disabled."
            elif target == "bolt":
                need("WRENCH")
                if "dam-enabled" not in self.flags: raise ValueError("The bolt won't turn with your best effort.")
                if "gates-open" in self.flags:
                    self.flags.discard("gates-open")
                    self.dam_event, self.dam_turn = "fill", self.turns + 8
                    message = "The sluice gates close and water starts to collect behind the dam."
                else:
                    flag("gates-open")
                    self.dam_event, self.dam_turn = "drain", self.turns + 8
                    message = "The sluice gates open and water pours through the dam."
            elif target == "railing":
                need("ROPE"); self.item_locations["ROPE"] = self.position; flag("DOME-FLAG")
                message = "You tie the rope securely to the railing."
            elif target == "rainbow":
                need("SCEPTRE"); flag("RAINBOW-FLAG")
                self.hidden_items.discard("POT-OF-GOLD")
                message = "The rainbow becomes a solid bridge."
            elif target == "bell":
                need("BELL")
                if "LLD-FLAG" in self.flags:
                    message = "Ding, dong."
                else:
                    self.bell_turn = self.turns
                    self.ritual_turn = None
                    self.item_locations["BELL"] = "offstage"   # red hot, on the floor, for twenty turns
                    self.bell_cool_turn = self.turns + 20
                    message = ("The bell suddenly becomes red hot and falls to the ground. The wraiths, as if paralyzed, stop their "
                               "jeering and slowly turn to face you. On their ashen faces, the expression of a long-forgotten terror takes shape.")
                    if self._inside("CANDLES", "inventory"):
                        self.item_locations["CANDLES"] = self.position
                        self.flags.discard("ritual-candles")
                        message += " In your confusion, the candles drop to the ground (and they are out)."
            elif target == "candles":
                need("CANDLES"); need("MATCH")
                flag("ritual-candles"); message = "The candles burn."
                bell_live = self.bell_turn is not None and self.turns - self.bell_turn <= 5
                if bell_live and self.ritual_turn is None and "LLD-FLAG" not in self.flags:
                    self.ritual_turn = self.turns
                    message += (" The flames flicker wildly and appear to dance. The earth beneath your feet trembles, and your legs "
                                "nearly buckle beneath you. The spirits cower at your unearthly power.")
            elif target == "book":
                need("BOOK")
                if "LLD-FLAG" in self.flags:
                    raise ValueError("The spirits have already fled.")
                if self.ritual_turn is None or self.turns - self.ritual_turn > 2:
                    raise ValueError("You must perform the ceremony." if self._has("CANDLES") else "You aren't equipped for an exorcism.")
                flag("LLD-FLAG"); self.ritual_turn = None
                message = ("Each word of the prayer reverberates through the hall in a deafening confusion. As the last word fades, a voice, "
                           "loud and commanding, speaks: \"Begone, fiends!\" A heart-stopping scream fills the cavern, and the spirits, "
                           "sensing a greater power, flee through the walls.")
            elif target == "bat":
                need("GARLIC"); message = "The smell of garlic keeps the bat at bay."
            elif target == "switch":
                need("SCREWDRIVER")
                if self.item_locations["COAL"] != "MACHINE" or self.item_open["MACHINE"]:
                    raise ValueError("The machine does not produce anything.")
                self.item_locations["COAL"] = "offstage"; self.item_locations["DIAMOND"] = "MACHINE"
                flag("diamond-made"); message = "The machine compresses the coal into a huge diamond. Open it to retrieve the diamond."
            elif target == "sand":
                need("SHOVEL"); self.dig_count += 1
                if self.dig_count >= 3: self.hidden_items.discard("SCARAB")
                message = "You uncover a jeweled scarab." if self.dig_count >= 3 else "You dig deeper into the sand."
            elif target == "thief":
                if self._find_item(item) != "EGG": raise ValueError("The thief refuses your offering.")
                need("EGG")
                if "thief-defeated" in self.flags: raise ValueError("The thief is gone.")
                self.item_open["EGG"] = True; flag("egg-opened")
                message = "The thief expertly opens the egg and returns it, revealing a mechanical canary."
            elif target == "canary":
                need("CANARY")
                if "bauble-created" in self.flags: raise ValueError("The songbird has already left its gift.")
                self.item_locations["BAUBLE"] = self.position; flag("bauble-created")
                message = "The canary sings. A songbird drops a brass bauble at your feet."
            elif target == "room":
                if item.strip().lower() != "echo": raise ValueError("Try saying echo.")
                flag("echo-quiet"); message = "The roaring echoes subside."
            return {"success": True, "message": message, "score_delta": self._score() - before, **self.look()}
        except ValueError as exc:
            return {"success": False, "error": str(exc), **self.look()}
