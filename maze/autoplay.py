"""Replays walkthrough.py: a perfect run of the game, for demos and as a happy-path check.

    uv run python autoplay.py            # play it headless and report the result
    uv run python autoplay.py --verbose  # print every step too

The web app's Auto play button uses the same steps. The route lives in make_walkthrough.py.
"""
import sys

from walkthrough import SECTIONS, SEED, STEPS

TOTAL = len(STEPS)
# Section label for each step, so a player can see what the run is doing.
LABELS = [label for label, steps in SECTIONS for _ in steps]


def apply_step(world, step):
    """Run one recorded step on a world and return the game's result."""
    tool, *args = step
    if tool == "move":
        return world.move(args[0])
    if tool == "interact":
        return world.interact(args[0], args[1], args[2] if len(args) > 2 else "")
    if tool == "put":
        return world.put(args[0], args[1])
    if tool == "open":
        return world.open_container(args[0])
    if tool == "close":
        return world.close_container(args[0])
    if tool in ("take", "drop", "examine"):
        return getattr(world, tool)(args[0])
    if tool == "wait":
        return world.wait()
    raise ValueError(f"Unknown walkthrough step {tool!r}")


def command_text(step):
    """The step as a player would type it, for the transcript."""
    tool, *args = step
    if tool == "move":
        return args[0]
    if tool == "put":
        return f"put {args[0]} in {args[1]}"
    return " ".join([tool, *args])


def start(world):
    """Begin a fresh game with the dice the walkthrough was recorded with."""
    world.reset()
    world.seed(SEED)


def finished(world):
    """Did the run end the way the walkthrough promises: 350 points, adventure complete?"""
    return bool(world.look().get("at_goal")) and world._score() == 350 and not world.dead


def play(world, on_step=None):
    """Play the whole walkthrough on a world. Returns {'completed', 'steps', 'score', 'error'}."""
    start(world)
    for number, step in enumerate(STEPS, 1):
        result = apply_step(world, step)
        if on_step:
            on_step(number, step, result)
        if result.get("success", True) is False:
            return {"completed": False, "steps": number, "score": world._score(),
                    "error": f"step {number} {command_text(step)!r} failed: {result.get('error')}"}
    return {"completed": finished(world), "steps": TOTAL, "score": world._score(), "error": None}


if __name__ == "__main__":
    from maze import Maze

    verbose = "--verbose" in sys.argv
    world = Maze()
    last = [None]

    def show(number, step, result):
        if verbose:
            print(f"{number:4}. {command_text(step)}")
        elif LABELS[number - 1] != last[0]:
            last[0] = LABELS[number - 1]
            print(f"{number:4}. {last[0]}  (score {world._score()})")

    outcome = play(world, show)
    if outcome["completed"]:
        print(f"Adventure complete: {outcome['score']} points in {outcome['steps']} steps.")
    else:
        print(f"Walkthrough failed: {outcome['error'] or 'ended before the goal'} (score {outcome['score']})")
        sys.exit(1)
