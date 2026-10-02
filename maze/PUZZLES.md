# Zork puzzle adaptation

Start `uv run python web_app.py` from `maze`; it prints http://127.0.0.1:5000 and opens it in your browser
(set `MAZE_NO_BROWSER=1` to stop that).
Reset starts a fresh adventure. The dashboard shows the room, map, inventory,
score/turns/load, and hints used alongside a live agent activity panel. Configure
lets you replace the agent's system instructions for the next run; restoring
defaults is available. This changes instructions, not the game's completion rule.

The desktop layout places a Zork-style text transcript beside the regional map,
inventory, and agent panel. Switch to Whole map for the complete schematic.
Manual commands and MCP calls share the transcript, including failures, readable
text and hints. It retains the latest 300 commands; Reset or a new agent run
starts a fresh transcript. Browser polling records no extra game commands.
The terminal uses green CRT styling. New terminal output and reported agent
reasoning type in progressively; the Typing effect checkbox switches to instant
display. Reduced-motion preferences default to instant display. This is a display
animation of received text and does not add game turns or model requests.

The MCP server exposes fourteen tools, including `interact(action, target,
item="")` and `hint()`. `look` returns a global `allowed_actions` vocabulary,
`nearby_features`, and `blocked_exits`. It does not pair features with successful
actions or include solution hints. The vocabulary is the supported subset of
Zork-style commands, not the complete original parser. Ordinary descriptions
and failed-action responses no longer explain the solution.

Explicit hint requests return the next unfinished goal based on progress (flags set,
items taken, treasures deposited), not the current room, followed by up to two other
open leads. Hints notice dropped equipment and say where it now is. See `hints.py`.
Every request, including repeats, increments `hints_used`. Hints cost no points or turns; reset clears the counter. Manual
requests and MCP requests use the same state. No hints are requested automatically.

Implemented chains: kitchen window; rug/trapdoor; sword/troll; cyclops name or
food/water; leaves/key/grating; dam buttons/wrench/reservoir; rope/dome;
sceptre/rainbow; bell/matches/candles/book ritual with a six-turn deadline;
praying at the altar to carry the coffin out of the temple (the stairs are too narrow for it);
garlic/bat; coal/machine/screwdriver/diamond; shovel/buried scarab;
thief/egg/canary/bauble; sword/thief/chalice (the thief defends his lair); echo/platinum bar; and supply
basket/narrow mine passage. All recognized interactions consume a turn,
including failures. Reset restores gates, hidden treasures, and generated items.

Troll combat uses the original melee tables with random rolls; the player can be wounded,
knocked out or killed, which ends the game until a save is restored or the world is reset.
Darkness, boat inflation, the original parser and item theft by the thief are not implemented.
The starting objective is treasure hunting and depositing treasures in the trophy
case. At 350 points the ancient map appears and unlocks the final route.
Examining it reveals the Stone Barrow destination. Walking southwest from West of House reaches the barrow,
and going in through the stone door shows the original closing message and your final score: that, not just
arriving at the door, completes the adventure (`at_goal`), after which only a reset does anything.
Merely reaching the Treasure Room does not stop the agent.
The final location is not highlighted on the map until the endgame unlocks. The available total is 350:
the mine's 13-point bonus is adapted to lowering the lantern in the basket,
since light simulation is omitted. This bonus is awarded only once.

Original source: https://github.com/historicalsource/zork1/blob/master/1actions.zil
and the local `zork1_source.zil`, under `ZORK_LICENSE.txt`.
`Maze(puzzles=False)` preserves the earlier navigation mode for graph and
carrying-rule tests. Puzzle tests use the default adventure mode.


The thief starts in the Treasure Room and randomly follows one underground passage every four consuming game turns. Passive state polling, SCORE, and HINT do not move him. His current position is revealed only through nearby features and room prose when the player encounters him. Arrivals and departures appear in the transcript. GIVE EGG and ATTACK THIEF work in any room where he is present; interacting with him is resolved before he can leave. A sword defeats him deterministically and stops his roaming until reset.

As in the original, entering the Treasure Room summons the thief to defend it ("You hear a scream of anguish..."), he will not leave while you are standing there, and the chalice cannot be taken while he is: "You'd be stabbed in the back first." The chalice therefore needs the thief dead. He does not yet steal items or use the full combat system.

Rules matched to the original source (1actions.zil):
- The trap door crashes shut and is barred the first time you go down to the cellar. From below it is "locked from above". The ways home are the cyclops passage (Odysseus), the grating, or the chimney. Opening it again from the living room works and no longer slams.
- The studio chimney only takes you up if you carry the lamp and at most two things, and not empty-handed.
- The dam's bolt toggles the sluice gates. The reservoir drains, or refills, eight turns later. If it refills while you stand in the reservoir you drown. Refilling hides the trunk again.
- Ringing the bell at the Entrance to Hades makes it red hot: it falls, any candles you carry drop and go out, and it cannot be picked up for twenty turns. Light the candles within five turns (the flames flicker), then read the book within two turns. Matches are not yet used up.
