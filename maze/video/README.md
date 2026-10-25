# Video layer

Adds video to the game without changing how it plays. The engine (`maze.py`) still decides what every command
does and prints its reply. The video layer shows each room as a still keyframe, and plays a clip that starts on
the keyframe before a command and ends on the keyframe after it. Clips start and end on known images, so any
clip can follow any other without a visible cut.

- **Keyframes** are rooms as the engine describes them (`scenery_description()`), one per visible state. Each
  room's first state is named after the room (`living_room`); a later state gets a short hash suffix
  (`living_room__1c7982`, rug moved). The roaming thief is not part of a keyframe.
- **Edges** are traced from the game itself: `graph.derive()` replays the scripted perfect run and, at each new
  look of the world, tries every move reachable from there on copies of the game. Moves, state changes (open
  window, move rug, the troll gone) and two idle loops per keyframe become clips.
- **Prompts** are the engine's descriptions embellished by an LLM (`embellish.py`): an image prompt for a room's
  first state, an edit instruction for its later states (so the room keeps its shape), an idle prompt, and a
  motion prompt for every edge. A shared style suffix (`config.STYLE`) is appended when generating.
- **What a keyframe shows** (`graph.scenery()`): the room's description, fixtures that are open (mailbox,
  trophy case, machine, basket) and the room states in `graph.ROOM_STATES` (trap door, window, rope, reservoir,
  rainbow, dam, ...). Never loose items: anything a player can pick up is left out of every picture. The thief and
  short-lived effects (hot bell, burning candles) are left for cosmetic clips.
- **Layered fixtures** (`layers.py`): the trophy case's keyframes show it empty. Every treasure has a transparent
  picture (`gen_layers.py`) and a fixed slot on one of three shelves, and the player draws whatever is in the case
  on top. Mark the shelves once on the Living Room's first keyframe in the review page; its later looks reuse it.
- Everything lives in `data/manifest.json`; pictures and clips go to `assets/`, which is not committed. The game
  and the review page serve a file from `assets/` when it is there, and otherwise load it from the public
  `auto-zork-media` bucket on Cloudflare R2 (`VIDEO_ASSET_URL`), so a fresh clone shows everything made so far.

## Steps (run from `maze/`)

1. `uv run python -m video.expand_actions --rooms "Forest And Outside Of House,House"` derives the graph for
   those areas (or set `VIDEO_ROOMS`), prints the edge count and the video cost estimate. No API calls.
2. `uv run python -m video.embellish` writes the prompts (`--dry-run` shows the first request).
3. `uv run python -m video.gen_keyframes` makes the images with `IMAGE_MODEL`, after showing the cost and asking.
4. `uv run python -m video.gen_layers` makes the treasure pictures (asks first).
5. `uv run python -m video.review` opens http://127.0.0.1:5001: every activity as start frame and end frame, every
   keyframe and treasure with Approve / Reject, and "Mark trophy case shelves" with a preview of every slot filled.
   Rejected pictures are made again by steps 3 and 4; remaking a keyframe also remakes every look edited from it.

6. `uv run python -m video.gen_clips --only <edge ids or prefixes, or @file>` makes clips with `VIDEO_MODEL` after
   showing the cost and asking. Each new clip is then cropped to the view the player shows (`normalize.py`): video
   models reframe by a few percent, so stills are shown at `DISPLAY_ZOOM` and every clip is cropped to match.
7. `uv run python -m video.check_seams` scores each clip's first and last frames against its stills (SSIM) and
   lists anything under `SEAM_THRESHOLD`.

8. `uv run python -m video.publish_media` uploads new pictures, clips and treasure layers to that bucket, with an
   800-pixel JPEG copy of each picture in `assets/thumbs/` for galleries (needs Cloudflare's `wrangler`, logged in to
   the account that owns it).

Each step skips finished work, and a rerun of step 1 keeps prompts and images whose descriptions have not changed.

## In the game

The scene panel above the transcript shows the keyframe for the current room and state, with treasures layered into
the trophy case. After each command the server picks the clip from the keyframe before to the keyframe after (or a
cosmetic clip, such as taking the leaflet, when nothing visible changed) and the page plays it once, then settles on
the new still with its idle loop. Rooms or turns without a picture or clip simply show the still, or nothing.
