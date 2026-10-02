"""Flask web app that visualizes the maze in maze.py and can "load" a GPT
agent from agent.py to solve it live. The world (maze.py) and the agent
(agent.py) stay decoupled: this app just wires the world's tool schema and
executor into whichever agent it loads.
"""

from __future__ import annotations

import json
import logging
import threading
import webbrowser
import asyncio
import os
import sys
from pathlib import Path
from mcp import Client, StdioServerParameters

from dotenv import load_dotenv, set_key
from flask import Flask, jsonify, render_template_string, request

import maze
import saved_games
from context_memory import ContextMemory
from agent import AgentConfig, GPTAgent, list_models, DEFAULT_SYSTEM_PROMPT
import autoplay
from providers import PROVIDERS, find_api_key, key_env_name, model_env_name

ENV_FILE = Path(__file__).resolve().with_name(".env")
load_dotenv(ENV_FILE)

# Quiet down the dev server's per-request access log (the page polls /api/state
# every second, which would otherwise spam the terminal).
logging.getLogger("werkzeug").setLevel(logging.WARNING)

app = Flask(__name__)

# Shared world state plus a lock, since the manual API and the background
# agent thread can both touch the maze at the same time.
_lock = threading.Lock()
_agent_state = {
    "running": False, "log": [], "result": None,
    "mcp_status": "disconnected", "mcp_tools": [],
    "usage": None,
    "viewer_hints": 0,
    "memory": None, "can_continue": False,
    "agent": None,  # provider and model of the current or most recent run
}
_stop_event = threading.Event()
_pending_viewer_hints: list[str] = []
_transcript = []
_transcript_sequence = 0
# Progress of the scripted perfect run (autoplay.py); the thread below updates it under _lock.
_autoplay = {"running": False, "finished": False, "completed": False, "step": 0, "speed": "normal",
             "total": autoplay.TOTAL, "label": "", "score": 0, "error": None}
_autoplay_stop = threading.Event()
AUTOPLAY_DELAYS = {"slow": 1.0, "normal": 0.35, "fast": 0.05}
# Every hint given: kind is "player" (you asked the game), "agent" (the agent asked the
# game) or "viewer" (you told the agent). Call _log_hint with _lock held.
_hint_log: list[dict] = []
_hint_sequence = 0


def _log_hint(kind: str, text: str, delivered: bool | None = None) -> None:
    global _hint_sequence
    world = maze._maze
    _hint_sequence += 1
    _hint_log.append({"id": _hint_sequence, "kind": kind, "text": text, "delivered": delivered,
                      "room": world.rooms[world.position]["name"], "turn": world.turns})
    del _hint_log[:-200]


def _game_text(result):
    return "\n\n".join([*result.get("encounter_events", []), _game_text_body(result)])


def _game_text_body(result):
    """Player-facing prose for the terminal; tool JSON stays in agent details."""
    if result.get("error"):
        return result["error"]
    if result.get("hint"):
        return result["hint"]
    if result.get("item"):
        item = result["item"]
        return "\n\n".join(filter(None, (item["name"], item.get("description"), item.get("readable_text"))))
    lines = []
    if result.get("message"):
        lines.append(result["message"])
    if result.get("room_name") and not (result.get("adventure_complete") and result.get("encounter_events")):
        # (the closing message ends the story, so the room is not described again after it)
        lines.extend((result["room_name"], result["description"]))
        names = [i["name"] for i in result.get("items", []) if i.get("location") == result["room_id"]]
        if names:
            lines.append("You see " + ", ".join(names) + ".")
        for container in result.get("items", []):
            if container.get("location") == result["room_id"] and container.get("contents"):
                lines.append(f"The {container['name']} contains: " + ", ".join(c["name"] for c in container["contents"]) + ".")
        lines.append("Exits: " + (", ".join(result.get("exits", [])) or "none") + ".")
    elif "score" in result:
        lines.append(f"Your score is {result['score']} of {result['score_max']} in {result['turns']} turns. Hints: {result['hints_used']}.")
    announcement = result.get("announcement")
    if announcement and not any(announcement in entry["text"] for entry in _transcript):
        lines.append(announcement)
    return "\n\n".join(lines) or "Done."


@app.after_request
def no_store_page(response):
    if request.path == "/":
        response.headers["Cache-Control"] = "no-store"
    return response


@app.after_request
def record_game_command(response):
    global _transcript_sequence
    path = request.path
    is_command = request.method == "POST" and (path in ("/api/move", "/api/look", "/api/reset", "/api/wait", "/api/hint", "/api/interact", "/api/inventory") or path.startswith("/api/items/"))
    if not (is_command or path == "/api/score") or not response.is_json or response.status_code != 200:
        return response
    result = response.get_json()
    body = request.get_json(silent=True) or {}
    command = path.rsplit("/", 1)[-1].replace("_container", "")
    if path == "/api/move": command = body.get("direction", "move")
    elif path == "/api/interact": command = " ".join(body.get(k, "") for k in ("action", "target", "item")).strip()
    elif path.startswith("/api/items/"): command += " " + body.get("item", "") + (" in " + body["container"] if body.get("container") else "")
    with _lock:
        if path == "/api/reset": _transcript.clear()
        _transcript_sequence += 1
        output = _game_text(result)
        if path == "/api/inventory":
            output = "You are carrying:\n" + ("\n".join("  " + i["name"] for i in result.get("inventory", [])) or "  Nothing.")
        _transcript.append({"id": _transcript_sequence, "command": command, "text": output})
        del _transcript[:-300]
    return response

PAGE = """
<!doctype html>
<html>
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Zork I navigation maze</title>
<style>
  [hidden] { display: none !important; }
  * { box-sizing: border-box; }
  :root { color-scheme: dark; --font-ui: system-ui, -apple-system, "Segoe UI", sans-serif; --font-mono: Consolas, "Liberation Mono", monospace; }
  body { margin: 0; background: #10151b; color: #d8e1ea; font: 13px/1.5 var(--font-ui); height: 100dvh; display: flex; flex-direction: column; }
  header { display: flex; align-items: center; flex-wrap: wrap; gap: 12px; padding: 10px 16px; border-bottom: 1px solid #354251; }
  header h2 { font-size: 18px; margin: 0; white-space: nowrap; }
  header p { margin: 0; color: #98abbd; font-size: 12px; }
  #player-stats { margin: 0; padding: 8px 16px; background: #1d2b39; font-size: 12px; display: flex; align-items: center; flex-wrap: wrap; gap: 8px 16px; }
  .rank-badge { padding: 2px 9px; border: 1px solid #7f7049; border-radius: 4px; color: #f1d285; background: #302e24; }

  main { flex: 1; min-height: 0; display: grid; grid-template-columns: minmax(300px, 1.3fr) minmax(280px, 1fr) minmax(300px, 1fr); grid-template-rows: minmax(0, 1.3fr) minmax(0, 1fr); gap: 10px; padding: 10px; }
  .panel { min-width: 0; min-height: 0; border: 1px solid #344251; border-radius: 7px; padding: 12px; background: #171f29; overflow: auto; }
  .panel h3 { font-size: 14px; font-weight: 600; margin: 0 0 10px; }
  .save-bar { display: flex; gap: 8px; align-items: center; flex-wrap: wrap; margin: 6px 0; padding: 6px 8px; background: #1b2a1f; border: 1px solid #3f7a52; border-radius: 6px; }
  .save-bar input { width: 9em; }
  #quick-save { background: #2e7d4a; color: #fff; font-weight: 600; border: 0; border-radius: 4px; padding: 6px 14px; cursor: pointer; }
  #quick-save:hover { background: #38975a; }
  .save-bar small { flex: 1 1 12em; color: #b8d8c2; }
  #notebook h4 { margin: 8px 0 3px; font-size: 12px; color: #9fb3c8; text-transform: uppercase; letter-spacing: .04em; }
  .notebook-list { max-height: 170px; overflow: auto; font-size: 12px; }
  .notebook-list .note { padding: 3px 0 3px 8px; border-left: 3px solid #455566; margin-bottom: 3px; }
  .notebook-list .note.player { border-color: #3f9bd1; } .notebook-list .note.agent { border-color: #f3ce72; } .notebook-list .note.viewer { border-color: #6fcf8e; }
  .notebook-list .note small { display: block; color: #92a3b5; }
  #agent-identity { font-size: 12px; font-weight: 400; color: #bcd; background: #223244; border: 1px solid #3a516b; border-radius: 10px; padding: 1px 9px; margin-left: 6px; vertical-align: middle; }
  #agent-identity.live { color: #dff6e4; background: #1f4a2e; border-color: #3f9a5c; }
  .autoplay { display: inline-flex; gap: 4px; align-items: center; margin-left: 10px; padding-left: 10px; border-left: 1px solid #435b72; }
  #autoplay-status { display: block; min-height: 1.4em; margin: 2px 0; color: #b8d8c2; }
  #world-panel { grid-column: 1; grid-row: 1 / span 2; display: flex; flex-direction: column; }
  #map-panel { grid-column: 2; grid-row: 1; display: flex; flex-direction: column; }
  #inventory-panel { grid-column: 2; grid-row: 2; }
  #agent-panel { grid-column: 3; grid-row: 1 / span 2; display: flex; flex-direction: column; }
  .panel-heading { display: flex; justify-content: space-between; gap: 10px; color: #a7bbc9; }
  #transcript { flex: 1; min-height: 140px; overflow: auto; padding: 12px; margin-bottom: 10px; background: #0b1015; font: 14px/1.65 var(--font-mono); white-space: pre-wrap; overflow-wrap: anywhere; color: #e1dfca; }
  .transcript-entry { margin-bottom: 22px; }
  .terminal-command { color: #89c9b1; margin-bottom: 8px; }
  #room-details { max-height: 160px; overflow: auto; }
  #room-description, #objective { line-height: 1.5; }
  #room-details p { margin: 8px 0; }
  #exits { display: inline; }
  #item-result { white-space: pre-wrap; max-height: 90px; overflow: auto; margin: 6px 0 0; color: #a8c9e5; }
  #item-result:empty, #announcement:empty, #agent-error:empty, #run-result:empty { display: none; }
  #puzzles { display: flex; flex-wrap: wrap; gap: 4px; }
  #puzzles input { width: 100%; }
  #puzzles p { width: 100%; margin: 4px 0; }
  #grid { flex: 1; min-height: 100px; overflow: auto; margin-top: 8px; background: #111820; }
  #grid svg { display: block; width: 100%; height: 100%; touch-action: none; cursor: grab; }
  #grid { overflow: hidden; }
  .map-controls { flex-wrap: wrap; }
  #map-focus { font-size: 12px; line-height: 1.4; margin: 8px 0 0; max-height: 50px; overflow: auto; color: #a0b4c7; }
  .map-controls { display: flex; gap: 5px; margin-bottom: 6px; }
  #item-panels { display: flex; flex-direction: column; gap: 10px; }
  .agent-controls { display: flex; align-items: center; flex-wrap: wrap; gap: 3px; }
  .item-panel { min-width: 0; }
  .item-card { padding: 7px 0; border-bottom: 1px solid #30404f; }
  .item-card small { display: block; margin: 3px 0; color: #94a9bc; font-size: 12px; }
  button, input, select, textarea { font: inherit; color: #e5edf5; background: #233548; border: 1px solid #435b72; border-radius: 4px; padding: 5px 7px; max-width: 100%; }
  button { cursor: pointer; margin: 2px; min-height: 32px; }
  button:hover:not(:disabled) { background: #35516a; }
  button:disabled { color: #92989e; background: #30343a; border-color: #484d53; cursor: not-allowed; box-shadow: none; text-shadow: none; }
  input:disabled, select:disabled, textarea:disabled { color: #92989e; background: #30343a; border-color: #484d53; cursor: not-allowed; }
  button:focus-visible, input:focus-visible, textarea:focus-visible, select:focus-visible { outline: 2px solid #8bd1ff; outline-offset: 2px; }
  input, textarea { background: #101720; }
  details { flex-shrink: 0; }
  details > summary { cursor: pointer; padding: 8px; color: #b8cbdc; background: #1c2936; border: 1px solid #354959; border-radius: 4px; min-height: 36px; }
  details > summary:hover { background: #273b4b; }
  details > summary:focus-visible { outline: 2px solid #8bd1ff; outline-offset: 2px; }
  details[open] > summary { margin-bottom: 8px; }
  #room-details { margin-bottom: 8px; }
  #room-details[open] > summary, #usage-details[open] > summary { position: sticky; top: 0; z-index: 1; }
  .sound-toggle { display: inline-flex; align-items: center; gap: 6px; white-space: nowrap; font-size: 12px; }
  #sound-volume { width: 100px; padding: 0; }
  #volume-value { min-width: 34px; }
  #api-key-status { font-size: 12px; color: #a9bfd0; margin: 6px 0; }
  #api-key-save-status { display: block; line-height: 1.5; color: #a9bfd0; }
  #agent-panel > :not(.agent-feed) { flex-shrink: 0; }
  .agent-feed { margin: 4px 0; display: flex; flex-direction: column; min-height: 0; }
  .agent-feed > summary { cursor: pointer; font-weight: 600; font-size: 14px; padding: 5px 2px; flex-shrink: 0; }
  .agent-feed[open] { min-height: 190px; }
  #agent-panel { overflow-y: auto; }
  #reasoning-panel[open] { flex: 1 1 0; } #log-panel[open] { flex: 1.2 1 0; }
  /* Browsers that wrap a details body in ::details-content need that box to be the flex item. */
  .agent-feed::details-content { display: flex; flex-direction: column; flex: 1 1 0; min-height: 0; }
  .agent-feed > :not(summary) { min-height: 0; }
  #hint-dialog { width: min(520px, calc(100vw - 24px)); padding: 0; border: 1px solid #536d83; border-radius: 9px; background: #171f29; color: #d8e1ea; font: inherit; box-shadow: 0 20px 80px #000a; }
  #hint-dialog::backdrop { background: #0009; }
  #hint-dialog small { display: block; margin-top: 8px; line-height: 1.5; color: #a9bfd0; }
  #trophy-meter { height: 12px; accent-color: #e2bb61; }
  #put-destination { width: 100%; margin-top: 4px; }
  .dialog-heading { display: flex; align-items: center; justify-content: space-between; gap: 16px; padding: 12px 18px; border-bottom: 1px solid #435b72; }
  .dialog-heading h3 { margin: 0; font-size: 16px; }
  .dialog-body { padding: 8px 18px 18px; overflow: auto; overscroll-behavior: contain; }
  .dialog-body details { margin-top: 14px; }
  .dialog-body p { margin: 8px 0; }
  #configure { width: min(620px, calc(100vw - 24px)); max-height: 90dvh; padding: 0; border: 1px solid #536d83; border-radius: 9px; background: #171f29; color: #d8e1ea; font: inherit; box-shadow: 0 20px 80px #000a; }
  #configure[open] { display: flex; flex-direction: column; }
  #hint-dialog[open] { display: flex; flex-direction: column; }
  #configure::backdrop { background: #0009; }
  .dialog-heading { flex-shrink: 0; }

  .config-field { display: block; margin: 8px 0; }
  .config-field input, .config-field textarea, .config-field select { display: block; width: 100%; margin-top: 4px; }
  textarea { resize: vertical; }
  #mcp-status { color: #98adbd; }
  #mcp-status[data-status="connected"] { color: #7ddd91; }
  #mcp-status[data-status="connecting"] { color: #f3ce72; }
  #mcp-status[data-status="error"], #agent-error { color: #f18c8c; }
  #mcp-tools { display: none; }
  #agent-status { margin-left: 5px; color: #87c7ee; }
  #agent-current { background: #213448; border-left: 2px solid #87c7ee; padding: 8px; margin: 8px 0; line-height: 1.4; max-height: 100px; overflow: auto; flex-shrink: 0; }
  #reasoning { flex: 1; min-height: 60px; overflow: auto; padding: 8px; background: #101720; white-space: pre-wrap; font-size: 13px; line-height: 1.6; margin-bottom: 12px; }
  #log { flex: 1; min-height: 60px; overflow: auto; background: #101720; padding: 0 8px; }
  .event { padding: 8px 0; border-bottom: 1px solid #304254; font-size: 12px; }
  .event small { display: block; color: #a9bdd0; margin-top: 4px; }
  .event pre { white-space: pre-wrap; overflow-wrap: anywhere; font: 12px/1.5 var(--font-mono); }
  #run-result { white-space: pre-wrap; color: #aaddba; }
  #usage-summary { font-size: 12px; line-height: 1.5; padding: 7px; margin: 7px 0; background: #101720; color: #abc3d4; }
  #usage-details { flex-shrink: 0; max-height: 130px; overflow: auto; font-size: 12px; }
  #usage-calls { white-space: pre-wrap; }
  #transcript { background: #041009; color: #79fba1; text-shadow: 0 0 5px #46ec7c55; border: 1px solid #245639; box-shadow: inset 0 0 35px #000a; background-image: repeating-linear-gradient(to bottom, transparent 0, transparent 2px, #0003 3px, #0003 4px); }
  .terminal-command { color: #b0ffc8; }
  #reasoning { font-family: var(--font-mono); color: #a9d9ef; }
  .typing::after { content: '▍'; color: #a4ffc0; animation: cursor-blink .8s steps(1) infinite; text-shadow: 0 0 8px currentColor; }
  #reasoning .typing::after { color: #a9d9ef; }
  .reasoning-entry { margin-bottom: 14px; }
  .typing-toggle { margin-left: auto; white-space: nowrap; font-size: 12px; color: #99b9a5; }
  @keyframes cursor-blink { 50% { opacity: 0; } }
  @media (prefers-reduced-motion: reduce) { .typing::after { animation: none; } }
  @media (max-width: 1100px) { body { height: auto; min-height: 100dvh; } header p { display: none; } main { grid-template-columns: minmax(0, 1fr) minmax(0, 1fr); grid-template-rows: 600px 360px; flex: auto; } #world-panel { grid-column: 1; grid-row: 1; } #agent-panel { grid-column: 2; grid-row: 1; } #map-panel { grid-column: 1; grid-row: 2; } #inventory-panel { grid-column: 2; grid-row: 2; } }
  @media (max-width: 620px) { header { gap: 8px 12px; padding: 10px; } header h2 { width: 100%; } .typing-toggle { margin-left: 0; } main { display: flex; flex-direction: column; } #world-panel, #agent-panel { height: 550px; } #map-panel, #inventory-panel { height: 350px; } }
</style>
</head>
<body>
  <header><h2>Zork · Agent explorer</h2>
  <p>Explore the Great Underground Empire. Discover treasures and deposit them in the trophy case.</p>
  <label class="typing-toggle"><input id="typing-enabled" type="checkbox" checked onchange="setTypingEnabled(this.checked)"> Typing effect</label>
  <label class="sound-toggle"><input id="sound-muted" type="checkbox" onchange="setAudioLevel()"> Mute</label>
  <label class="sound-toggle">Volume <input id="sound-volume" type="range" min="0" max="100" value="65" aria-label="Sound volume" oninput="setAudioLevel()"><output id="volume-value">65%</output></label>
  </header><div id="player-stats"><span id="score-display">Score: ?</span><span class="rank-badge">Rank: <strong id="player-rank">?</strong></span><span id="player-counters"></span></div><main>
  <section class="panel" id="world-panel">
  <div class="panel-heading"><h3>Adventure</h3><span id="current-room"></span></div>
  <div class="save-bar"><input id="save-name" value="quick-save" maxlength="48" aria-label="Save name" autocomplete="off"><button id="quick-save" autocomplete="off" onclick="saveGame()" title="Save now (Ctrl+S), even while the agent is running">Save game</button><small id="save-status" role="status">Save any time with Ctrl+S, even while the agent runs.</small></div>
  <div id="transcript" role="log" aria-label="Game transcript" aria-live="polite"></div>
  <details id="room-details"><summary>Current room & objective</summary>
  <h3 id="room-name"></h3>
  <p id="room-description"></p>
  <p id="objective"></p><p id="announcement" role="status"></p>
  <p id="status"></p>
  </details>
  <div><span id="exits"></span> <button onclick="waitTurn()">Wait</button> <button onclick="requestHint()">Ask for hint</button> <button onclick="doReset()">Reset</button>
    <span class="autoplay"><button id="autoplay-start" onclick="startAutoplay()" title="Watch a perfect scripted run of the whole game">Auto play</button><select id="autoplay-speed" aria-label="Auto play speed"><option value="slow">Slow</option><option value="normal" selected>Normal</option><option value="fast">Fast</option></select><button id="autoplay-stop" onclick="stopAutoplay()" disabled>Stop</button></span></div>
  <small id="autoplay-status" role="status"></small>
  <details><summary>Interact with this room</summary><div id="puzzles"></div></details>
  <details id="save-controls"><summary>Save & restore</summary>
    <label class="config-field">Saved game<select id="saved-game"></select></label>
    <button onclick="restoreGame()">Restore game</button>
    <small>Saves include the agent's game memory. Stop the agent before restoring. An "autosave" slot is written whenever an agent run ends.</small>
  </details>
  <p id="item-result" role="status"></p></section>
  <section class="panel" id="inventory-panel">
  <div id="item-panels">
    <section class="item-panel"><h3>Inventory</h3><div id="inventory-items"></div>
      <div id="put-controls"><label for="put-destination">Put items into / onto:</label> <select id="put-destination"></select></div>
    </section>
    <details class="item-panel"><summary>Items here</summary><div id="room-items"></div></details>
    <section class="item-panel"><h3>Trophy case</h3>
      <p id="trophy-progress" role="status"></p>
      <progress id="trophy-meter" aria-label="Treasures stored in trophy case" value="0" max="1" style="width:100%"></progress>
      <div id="trophy-treasures"></div>
    </section>
  </div>
  </section><section class="panel" id="map-panel"><h3>World map</h3>
  <div class="map-controls"><select id="map-mode" aria-label="Map view" onchange="mapCamera=null; lastRenderKey=null; render(lastState)"><option value="region">Nearby rooms</option><option value="world">Whole map</option></select><button onclick="selectedRoom=null; mapCamera=null; lastRenderKey=null; render(lastState)">Follow player</button><button aria-label="Zoom in" onclick="zoomMap(.8)">+</button><button aria-label="Zoom out" onclick="zoomMap(1.25)">-</button><button onclick="mapCamera=null; lastRenderKey=null; render(lastState)">Fit view</button></div>
  <small>North is up; east is right · Drag to pan; scroll/pinch to zoom. Blue: player. Labeled arrows show actual passages, including unusual maze connections.</small>
  <div id="grid"></div>
  <p id="map-focus"></p>

  </section><section class="panel" id="agent-panel"><h3>Agent live <span id="agent-identity" title="Provider and model"></span></h3>
  <div class="agent-controls"><button onclick="runAgent()">Run Agent</button><button id="continue-agent" onclick="runAgent(true)" disabled>Continue Agent</button><button id="stop-agent" onclick="stopAgent()">Stop Agent</button><button id="give-hint" onclick="openHintDialog()" disabled title="Send the running agent a hint">Give Hint</button><button onclick="openConfiguration()">Configure agent</button>
  <span id="agent-status">Idle</span>
  </div>
  <span id="mcp-status" data-status="disconnected" role="status">MCP: disconnected</span>
  <small id="mcp-tools"></small>
  <details id="notebook"><summary>Hints &amp; agent notes</summary>
    <h4>Hints given</h4><div id="hint-history" class="notebook-list"><small>No hints yet.</small></div>
    <h4>What the agent can see</h4><div id="agent-sees" class="notebook-list"><small>Nothing yet. The agent remembers its goal, the rooms it mapped, your hints and its recent actions.</small></div>
    <h4>Agent notes</h4><div id="agent-notes" class="notebook-list"><small>The agent's recent actions and clues appear here.</small></div>
  </details>
  <dialog id="hint-dialog" aria-labelledby="hint-title">
    <div class="dialog-heading"><h3 id="hint-title">Give the agent a hint</h3><button id="close-hint" onclick="document.getElementById('hint-dialog').close()" aria-label="Close hint dialog">Close</button></div>
    <div class="dialog-body">
      <p>Your advice is delivered with the agent's next decision and kept in its memory. It is listed under Hints &amp; agent notes.</p>
      <label class="config-field">Your advice<textarea id="viewer-hint" aria-label="Give the agent a hint" rows="4" maxlength="1000" placeholder="Try to open the window" disabled></textarea></label>
      <button id="send-viewer-hint" onclick="sendViewerHint()" disabled>Send hint</button>
      <small id="viewer-hint-status" role="status">Hints can be sent while the agent is running. Ctrl+Enter sends.</small>
    </div>
  </dialog>
  <p id="usage-summary">Tokens and estimated cost will appear when a run starts.</p>
  <details id="usage-details"><summary>Token & cost details</summary><div id="usage-calls"></div></details>
  <p id="api-key-status" role="status"></p>
  <dialog id="configure" aria-labelledby="configure-title">
    <div class="dialog-heading"><h3 id="configure-title">Configure agent</h3><button id="close-configure" onclick="document.getElementById('configure').close()" aria-label="Close configuration">Close</button></div>
    <div class="dialog-body">
    <p id="api-key-dialog-status" role="status"></p>
    <label class="config-field">Provider<select id="provider" onchange="onProviderChange()">{% for p in providers %}<option value="{{ p.id }}"{% if p.id == provider %} selected{% endif %}>{{ p.label }}</option>{% endfor %}</select></label>
    <button onclick="listModels()">Load Models</button>
    <label class="config-field">Model<select id="model" onchange="document.getElementById('custom-model-field').hidden = this.value !== '__custom__'"><option value="">Use configured default ({{ default_model }})</option><option value="__custom__">Enter a model ID...</option></select></label>
    <label id="custom-model-field" class="config-field" hidden>Model ID<input id="custom-model" placeholder="Model ID"></label>
    <label class="config-field">Base URL<input id="base_url" placeholder="base_url (optional)" size="28"></label>
    <label class="config-field">API key<input id="api_key" placeholder="Enter your provider's API key" size="20" type="password" autocomplete="off" oninput="updateKeyStatus()"></label>
    <button onclick="saveApiKey()">Save API key locally</button>
    <small id="api-key-save-status" role="status">Use the key for this session, or save it in this installation's private .env file for future sessions. Saved keys are stored as plain text on this computer.</small>
    <label class="config-field">Maximum steps<input id="max_steps" placeholder="max_steps" type="number" min="1" value="2000"></label>
    <label class="config-field">Refresh context every N steps<input id="context_refresh_steps" type="number" min="1" value="12"></label>
    <details><summary>Agent directives and instructions</summary>
    <label class="config-field">Instructions<textarea id="system_prompt" rows="9">{{ default_system_prompt }}</textarea></label>
    <button onclick="document.getElementById('system_prompt').value = document.getElementById('default-instructions').content.textContent">Restore default instructions</button>
    <p>Instructions apply to the next run. Empty instructions use the default. Run Agent starts a new game. Continue Agent uses the current game and its observed memory. The game determines when the adventure is complete.</p>
    <template id="default-instructions">{{ default_system_prompt }}</template></details>
    <details><summary>Cost estimate rates (optional)</summary>
      <p>USD per million tokens. Leave all blank for supported OpenAI standard rates. Other providers need all three rates for a cost estimate.</p>
      <label class="config-field">Input<input id="input_price" type="number" min="0" step="any" placeholder="Automatic"></label>
      <label class="config-field">Cached input<input id="cached_input_price" type="number" min="0" step="any" placeholder="Automatic"></label>
      <label class="config-field">Output<input id="output_price" type="number" min="0" step="any" placeholder="Automatic"></label>
    </details>
    </div>
  </dialog>
  <p id="agent-error"></p>
  <p id="agent-current" role="status">Ready to explore. Configure the agent, then start a run.</p>
  <details class="agent-feed" id="reasoning-panel" open><summary>Agent’s reported reasoning</summary><div id="reasoning"></div></details>
  <details class="agent-feed" id="log-panel" open><summary>Action timeline</summary><div id="log"></div></details>
  <p id="run-result"></p></section></main>

<script>
let selectedRoom = null;
let lastState = null;
let lastRenderKey = null;
let lastTranscriptKey = null;
let typingEnabled = !window.matchMedia('(prefers-reduced-motion: reduce)').matches;
let autoplayInstant = false;  // auto play outruns the typing animation, so show its text at once
document.getElementById('typing-enabled').checked = typingEnabled;
const typingViews = new Set();
let agentRunning = false;
let canContinue = false;
let hasSavedKey = {{ has_api_key | tojson }};
const savedKeys = {{ saved_keys | tojson }};
const providerInfo = {{ provider_info | tojson }};
function currentProvider() { return document.getElementById('provider').value; }
function defaultModelLabel() { return `Use configured default (${providerInfo[currentProvider()].model})`; }
function onProviderChange() {
  const info = providerInfo[currentProvider()];
  hasSavedKey = !!savedKeys[currentProvider()];
  document.getElementById('base_url').placeholder = info.base_url || 'base_url (optional)';
  document.getElementById('api_key').value = '';
  const dropdown = document.getElementById('model');
  dropdown.replaceChildren(new Option(defaultModelLabel(), ''), new Option('Enter a model ID...', '__custom__'));
  document.getElementById('custom-model-field').hidden = true;
  updateKeyStatus();
}
function updateKeyStatus() {
  const entered = document.getElementById('api_key').value.trim();
  document.getElementById('api-key-status').hidden = hasSavedKey;
  document.getElementById('api-key-status').textContent = hasSavedKey ? 'API key configured.' : entered ? 'API key entered for this session. You can save it locally.' : 'API key needed for the AI agent. Enter your key under Configure agent. Manual play is available without a key.';
  const dialogStatus = document.getElementById('api-key-dialog-status');
  dialogStatus.textContent = document.getElementById('api-key-status').textContent;
  dialogStatus.hidden = hasSavedKey;
}
function openConfiguration() {
  const dialog = document.getElementById('configure');
  if (!dialog.open) dialog.showModal();
}
for (const id of ['configure', 'hint-dialog']) document.getElementById(id).addEventListener('click', event => {
  const dialog = event.currentTarget;
  const box = dialog.getBoundingClientRect();
  if (event.target === dialog && (event.clientX < box.left || event.clientX > box.right || event.clientY < box.top || event.clientY > box.bottom)) dialog.close();
});
function openHintDialog() {
  const dialog = document.getElementById('hint-dialog');
  if (!dialog.open) dialog.showModal();
  document.getElementById('viewer-hint').focus();
}
document.getElementById('viewer-hint').addEventListener('keydown', event => {
  if (event.key === 'Enter' && (event.ctrlKey || event.metaKey)) { event.preventDefault(); sendViewerHint(); }
});
function promptForKey() {
  openConfiguration();
  document.getElementById('api_key').focus();
  updateKeyStatus();
}
async function saveApiKey() {
  const input = document.getElementById('api_key');
  const status = document.getElementById('api-key-save-status');
  if (!input.value.trim()) { status.textContent = 'Enter an API key to save.'; promptForKey(); return; }
  try {
    const response = await fetch('/api/agent/key', {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({api_key:input.value.trim(), provider:currentProvider()})});
    const result = await response.json();
    status.textContent = response.ok ? 'API key saved locally. It will be used for future sessions.' : result.error;
    if (response.ok) { hasSavedKey = true; savedKeys[currentProvider()] = true; input.value = ''; updateKeyStatus(); }
  } catch (_) { status.textContent = 'Could not save the API key. Try again.'; }
}
let autoplayRunning = false;
function updateControls(running = agentRunning) {
  agentRunning = running;
  const busy = running || autoplayRunning;
  document.querySelectorAll('main button, main input, main select, main textarea').forEach(control => {
    if (control.id === 'close-configure' || control.id === 'close-hint' || control.id === 'quick-save' || control.id === 'save-name' || control.closest('#map-panel')) return;
    if (control.id === 'continue-agent') { control.disabled = busy || !canContinue; return; }
    if (control.id === 'autoplay-stop') { control.disabled = !autoplayRunning; return; }
    control.disabled = ['stop-agent', 'give-hint', 'viewer-hint', 'send-viewer-hint'].includes(control.id) ? !running : busy;
  });
}
new MutationObserver(() => updateControls()).observe(document.querySelector('main'), {childList: true, subtree: true});
let audioContext = null;
let lastTypingSound = 0;
let previousScore = null;
function setAudioLevel() {
  document.getElementById('volume-value').textContent = document.getElementById('sound-volume').value + '%';
  unlockAudio();
}


function unlockAudio() {
  if (document.getElementById('sound-muted').checked) return;
  const AudioContextClass = window.AudioContext || window.webkitAudioContext;
  if (!AudioContextClass) return;
  try { audioContext ||= new AudioContextClass(); audioContext.resume().catch(() => {}); } catch (_) { /* Text remains usable when audio is unavailable. */ }
}
document.addEventListener('pointerdown', unlockAudio);
document.addEventListener('keydown', unlockAudio);

function tone(frequency, duration, volume, delay = 0, shape = 'sine') {
  if (document.getElementById('sound-muted').checked || audioContext?.state !== 'running') return;
  volume *= Number(document.getElementById('sound-volume').value) / 100;
  if (!volume) return;
  const start = audioContext.currentTime + delay;
  const oscillator = audioContext.createOscillator();
  const gain = audioContext.createGain();
  oscillator.type = shape; oscillator.frequency.value = frequency;
  gain.gain.setValueAtTime(0, start);
  gain.gain.linearRampToValueAtTime(volume, start + .003);
  gain.gain.exponentialRampToValueAtTime(.0001, start + duration);
  oscillator.connect(gain); gain.connect(audioContext.destination);
  oscillator.start(start); oscillator.stop(start + duration + .01);
  oscillator.onended = () => { oscillator.disconnect(); gain.disconnect(); };
}

function typingSound(now) {
  if (now - lastTypingSound < 75) return;
  lastTypingSound = now;
  tone(160 + Math.random() * 70, .025, .09, 0, 'triangle');
}

function finishTyping(panel) {
  if (panel._typingFrame) cancelAnimationFrame(panel._typingFrame);
  panel._typingFrame = null;
  for (const job of panel._typingJobs || []) {
    job.node.textContent = job.text;
    job.node.classList.remove('typing');
  }
  panel._typingJobs = [];
}

function setTypingEnabled(enabled) {
  typingEnabled = enabled;
  if (!enabled) for (const panel of typingViews) finishTyping(panel);
}

function queueTyping(panel, node, text, instant = false) {
  typingViews.add(panel);
  if (!typingEnabled || instant || autoplayInstant) { node.textContent = text; return; }
  panel._typingJobs ||= [];
  panel._typingJobs.push({node, text, index: 0, elapsed: 0});
  if (panel._typingFrame) return;
  let previous = performance.now();
  const advance = now => {
    const job = panel._typingJobs[0];
    if (!job) { panel._typingFrame = null; return; }
    const follow = panel.scrollTop + panel.clientHeight >= panel.scrollHeight - 45;
    job.node.classList.add('typing');
    job.elapsed += Math.min(60, now - previous);
    previous = now;
    // Keep a busy agent's output readable without building a long animation backlog.
    const rate = panel._typingJobs.length > 3 ? 600 : 160;
    job.index = Math.min(job.text.length, Math.floor(job.elapsed * rate / 1000));
    if (job.index > job.node.textContent.length && panel.id === 'transcript') typingSound(now);
    job.node.textContent = job.text.slice(0, job.index);
    if (follow) panel.scrollTop = panel.scrollHeight;
    if (job.index >= job.text.length) {
      job.node.classList.remove('typing');
      panel._typingJobs.shift();
    }
    panel._typingFrame = requestAnimationFrame(advance);
  };
  panel._typingFrame = requestAnimationFrame(advance);
}

function syncTypedEntries(panel, entries, build) {
  const previous = panel._typedEntries || [];
  const appended = previous.length <= entries.length && previous.every((entry, i) => entry.key === entries[i].key);
  if (!appended) { finishTyping(panel); panel.replaceChildren(); }
  const start = appended ? previous.length : 0;
  // A rebuild (first load, or the 300-entry window sliding) shows old entries at once; only the newest types out.
  const instantRebuild = previous.length === 0 || !appended;
  for (let i = start; i < entries.length; i++) build(entries[i], instantRebuild && i < entries.length - 1);
  panel._typedEntries = entries.map(entry => ({key: entry.key}));
}

function renderTranscript(state) {
  const entries = state.transcript || [];
  const key = JSON.stringify(entries);
  if (key === lastTranscriptKey) return;
  lastTranscriptKey = key;
  const panel = document.getElementById('transcript');
  const following = panel.scrollTop + panel.clientHeight >= panel.scrollHeight - 45;
  syncTypedEntries(panel, entries.map(entry => ({...entry, key: JSON.stringify(entry)})), (entry, instant) => {
    const block = document.createElement('div'); block.className = 'transcript-entry';
    const command = document.createElement('div'); command.className = 'terminal-command';
    const output = document.createElement('div');
    block.append(command, output); panel.appendChild(block);
    if (entry.command) queueTyping(panel, command, '> ' + entry.command, instant);
    else command.remove();
    queueTyping(panel, output, entry.text, instant);
  });
  if (following) panel.scrollTop = panel.scrollHeight;
}

async function requestHint() {
  const response = await fetch("/api/hint", {method: "POST", headers: {"X-Maze-Source": "ui"}});
  const result = await response.json();
  document.getElementById("item-result").textContent = result.hint;
  await refresh();
  await pollAgent();
}

async function refresh() {
  const res = await fetch('/api/state');
  const state = await res.json();
  render(state);
  const panel = document.getElementById('puzzles');
  const puzzleKey = JSON.stringify([state.room_id, state.allowed_actions, state.nearby_features]);
  if (panel.dataset.key === puzzleKey) return;
  panel.dataset.key = puzzleKey;
  panel.replaceChildren();
  const verbs = document.createElement('select');
  verbs.setAttribute('aria-label', 'Interaction command');
  // Everyday item verbs go to the item tools; the rest are the room's puzzle actions.
  const itemVerbs = {take: 'take', examine: 'examine', drop: 'drop', put: 'put', close: 'close_container', open: 'open_container'};
  const everyday = ['take', 'examine', 'drop', 'put', 'close'];
  for (const verb of [...everyday, ...(state.allowed_actions || []).filter(v => !everyday.includes(v))]) {
    const option = document.createElement('option'); option.value = verb; option.textContent = verb; verbs.appendChild(option);
  }
  const target = document.createElement('input'); target.placeholder = 'Object or feature, e.g. map'; target.setAttribute('aria-label', 'Interaction target');
  const item = document.createElement('input'); item.placeholder = 'Spoken words, an item to give, or the container for put (optional)'; item.setAttribute('aria-label', 'Interaction item, words or container');
  const button = document.createElement('button'); button.textContent = 'Try command';
  button.onclick = async () => {
    if (itemVerbs[verbs.value]) { await itemAction(itemVerbs[verbs.value], target.value.trim(), item.value.trim()); return; }
    const response = await fetch('/api/interact', {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({action: verbs.value, target: target.value, item: item.value})});
    const result = await response.json();
    document.getElementById('item-result').textContent = result.item
      ? result.item.name + ': ' + result.item.description + (result.item.readable_text ? '\\n\\n' + result.item.readable_text : '')
      : (result.message || result.error);
    refresh();
  };
  const features = document.createElement('p'); features.textContent = 'Nearby: ' + (state.nearby_features || []).join(', ');
  panel.append(verbs, target, item, button, features);
}

function render(state) {
  lastState = state;
  const trophy = state.trophy_case;
  if (trophy) {
    document.getElementById('trophy-progress').textContent = `${trophy.count}/${trophy.total} treasures stored | ${trophy.points}/${trophy.max_points} deposit points`;
    const meter = document.getElementById('trophy-meter'); meter.max = trophy.total || 1; meter.value = trophy.count;
    const list = document.getElementById('trophy-treasures');
    const key = JSON.stringify(trophy.treasures);
    if (list.dataset.key !== key) {
      list.dataset.key = key; list.replaceChildren();
      if (!trophy.count) list.textContent = 'The trophy case is empty.';
      for (const item of trophy.treasures) {
        const row = document.createElement('div'); row.textContent = `${item.name} (+${item.points} points)`; list.appendChild(row);
      }
    }
  }
  if (previousScore !== null && previousScore !== state.score) {
    tone(880, .24, .045); tone(1320, .30, .025, .07);
  }
  previousScore = state.score;
  renderTranscript(state);
  document.getElementById('current-room').textContent = state.room_name;
  // Preserve buttons and map focus while polling an unchanged world.
  const renderKey = JSON.stringify([state.room_id, state.revision, selectedRoom]);
  if (renderKey === lastRenderKey) return;
  lastRenderKey = renderKey;
  document.getElementById('room-name').textContent = state.room_name;
  document.getElementById('room-description').textContent = state.description;
  document.getElementById('objective').textContent = state.objective;
  document.getElementById('announcement').textContent = state.announcement || '';
  renderItems(state);
  document.getElementById('status').textContent =
    `Room: ${state.room_id} | moves: ${state.moves_made} | visited: ${state.grid.visited_count}/${state.grid.rooms.length} | at goal: ${state.at_goal}`;
  document.getElementById('score-display').textContent = `Score: ${state.score}/${state.score_max}`;
  document.getElementById('player-rank').textContent = state.rank;
  document.getElementById('player-counters').textContent = `Hints: ${state.hints_used} | Turns: ${state.turns} | Load: ${state.carrying.weight}/${state.carrying.max_weight} | Loose items: ${state.carrying.loose_items}`
    + (state.carrying.fumble_chance_percent ? ` | Pickup fumble chance: ${state.carrying.fumble_chance_percent}%` : '');
  if (state.combat) document.getElementById('player-counters').textContent += ` | Health: ${state.combat.health} (${state.combat.strength}/${state.combat.max_strength})`;
  const exits = document.getElementById('exits');
  exits.innerHTML = '';
  for (const direction of state.exits) {
    const button = document.createElement('button');
    button.textContent = direction;
    button.onclick = () => { selectedRoom = null; move(direction); };
    exits.appendChild(button);
  }

  const grid = document.getElementById('grid');
  const scrollTop = grid.scrollTop, scrollLeft = grid.scrollLeft;
  grid.innerHTML = '';
  const ns = 'http://www.w3.org/2000/svg';
  function element(tag, attributes, text) {
    const node = document.createElementNS(ns, tag);
    for (const [key, value] of Object.entries(attributes)) node.setAttribute(key, value);
    if (text !== undefined) node.textContent = text;
    return node;
  }
  const svg = element('svg', {width:'100%', height:'100%', role:'img', 'aria-label':'Compass schematic of Zork room connections'});
  const defs = element('defs', {});
  const marker = element('marker', {id:'arrow', viewBox:'0 0 10 10', refX:9, refY:5, markerWidth:7, markerHeight:7, orient:'auto'});
  marker.appendChild(element('path', {d:'M 0 0 L 10 5 L 0 10 z', fill:'#f3ce72'})); defs.appendChild(marker); svg.appendChild(defs);
  const rooms = new Map(state.grid.rooms.map(room => [room.id, room]));
  const focused = rooms.get(selectedRoom || state.room_id);
  if (!mapCamera || mapCamera.focus !== focused.id || mapCamera.mode !== document.getElementById('map-mode').value) {
    const nearby = [focused, ...Object.values(focused.exits).map(id => rooms.get(id))];
    const visible = document.getElementById('map-mode').value === 'world' ? [...rooms.values()] : nearby;
    const x = Math.min(...visible.map(r=>r.x))-80, y = Math.min(...visible.map(r=>r.y))-80;
    const w = Math.max(...visible.map(r=>r.x))+216-x, h = Math.max(...visible.map(r=>r.y))+128-y;
    mapCamera = {x,y,w,h,focus:focused.id,mode:document.getElementById('map-mode').value};
    if (mapCamera.mode === 'region') {
      // Long exits must not shrink the room labels: cap the view width and centre on the room.
      const aspect = (grid.clientHeight / grid.clientWidth) || .75;
      if (mapCamera.w > REGION_MAX_WIDTH || mapCamera.h > REGION_MAX_WIDTH * aspect) {
        mapCamera.w = REGION_MAX_WIDTH; mapCamera.h = REGION_MAX_WIDTH * aspect;
        mapCamera.x = focused.x + 68 - mapCamera.w / 2; mapCamera.y = focused.y + 24 - mapCamera.h / 2;
      }
    }
  }
  svg.setAttribute('viewBox', `${mapCamera.x} ${mapCamera.y} ${mapCamera.w} ${mapCamera.h}`);
  // Level bands: faint backdrops labelled by depth, so floors read as separate maps.
  const bandLeft = Math.min(...state.grid.rooms.map(r=>r.x)) - 30, bandWidth = state.grid.width - bandLeft + 10;
  for (const band of state.grid.levels || []) {
    svg.appendChild(element('rect', {x:bandLeft, y:band.y, width:bandWidth, height:band.height, rx:8, fill:band.level < 0 ? '#161d27' : '#1a2230', stroke:'#2b3644', 'stroke-width':1}));
    svg.appendChild(element('text', {x:bandLeft+10, y:band.y+20, fill:'#7f8fa3', 'font-size':14}, band.name.toUpperCase()));
  }
  // Background connections provide context; gold arrows identify the selected room's exits.
  for (const room of rooms.values()) for (const targetId of Object.values(room.exits)) {
    const target=rooms.get(targetId);
    svg.appendChild(element('line', {x1:room.x+68,y1:room.y+24,x2:target.x+68,y2:target.y+24,stroke:'#455566','stroke-width':1,opacity:.45}));
  }
  const groupedExits = new Map();
  for (const [direction,target] of Object.entries(focused.exits)) {
    if (!groupedExits.has(target)) groupedExits.set(target, []);
    groupedExits.get(target).push(direction);
  }
  for (const [targetId, directions] of groupedExits) {
    const direction = directions.join(' / ');
    const target = rooms.get(targetId);
    const x1 = focused.x + 68, y1 = focused.y + 24;
    const x2 = target.x + 68, y2 = target.y + 24;
    const dx = x2 - x1, dy = y2 - y1;
    const distance = Math.hypot(dx, dy) || 1;
    const inset = Math.min(34, distance / 3);
    const path = targetId === focused.id
      ? `M ${x1} ${focused.y} C ${x1-60} ${focused.y-38}, ${x1+60} ${focused.y-38}, ${x1+15} ${focused.y}`
      : `M ${x1} ${y1} L ${x2-dx/distance*inset} ${y2-dy/distance*inset}`;
    const edge = element('path', {d: path, fill: 'none', stroke: '#f3ce72', 'stroke-width': 3, 'stroke-dasharray': directions.some(d=>['up','down','in','out'].includes(d)) ? '8 5' : '', 'marker-end': 'url(#arrow)', opacity: focused.id === state.room_id && !directions.some(d=>state.exits.includes(d)) ? .35 : .85});
    edge.appendChild(element('title', {}, `${direction}: ${focused.name} to ${target.name}`));
    svg.appendChild(edge);
    const label = element('text', {x:(x1+x2)/2, y:(y1+y2)/2-7, fill:'#ffe39c', 'font-size':16, 'text-anchor':'middle', stroke:'#111820', 'stroke-width':4, 'paint-order':'stroke'}, direction.toUpperCase());
    svg.appendChild(label);
  }
  const colors = {agent: '#176e98', goal: '#82552a', start: '#315b45', visited: '#334c68', floor: '#303844'};
  for (const room of state.grid.rooms) {
    const group = element('g', {tabindex: 0, role: 'button', 'aria-label': `Inspect ${room.name}, ${room.id}`, style: 'cursor:pointer'});
    group.appendChild(element('title', {}, `${room.name} (${room.id})`));
    group.appendChild(element('rect', {x: room.x, y: room.y, width: 136, height: 48, rx: 5,
      fill: colors[room.status], stroke: room.id === focused.id ? '#f3ce72' : '#627083', 'stroke-width': room.id === focused.id ? 2 : 1}));
    const words = room.name.split(' ');
    const lines = [''];
    for (const word of words) {
      if ((lines[lines.length-1] + word).length > 19 && lines[lines.length-1]) lines.push('');
      lines[lines.length-1] += word + ' ';
    }
    lines.slice(0, 2).forEach((line, index) => group.appendChild(element('text', {x: room.x + 7, y: room.y + 15 + index*13, fill: '#fff', 'font-size': 11}, line.trim())));
    const suffix = /-(\\d+)$/.exec(room.id);
    if (suffix) group.appendChild(element('text', {x: room.x + 126, y: room.y + 42, fill: '#ced7e2', 'font-size': 10, 'text-anchor': 'end'}, '#' + suffix[1]));
    const select = () => { if (mapDragged) return; selectedRoom = room.id; mapCamera=null; lastRenderKey=null; render(lastState); };
    group.onclick = select;
    group.onkeydown = event => { if (event.key === 'Enter' || event.key === ' ') { event.preventDefault(); select(); } };
    svg.appendChild(group);
  }
  grid.appendChild(svg);
  installMapGestures(svg);
  document.getElementById('map-focus').textContent =
    `Inspecting ${focused.name} (${focused.id}): ` + Object.entries(focused.exits).map(([direction, id]) => `${direction} → ${rooms.get(id).name} (${id})`).join('; ');
}

let mapCamera = null, mapDragged = false;
function applyMapCamera() {
  const svg=document.querySelector('#grid svg');
  if (svg && mapCamera) svg.setAttribute('viewBox', `${mapCamera.x} ${mapCamera.y} ${mapCamera.w} ${mapCamera.h}`);
}
const REGION_MAX_WIDTH = 760;  // map units; keeps 11px room names legible in the side panel
function zoomMap(factor, cx=.5, cy=.5) {
  if (!mapCamera) return;
  const widest = mapCamera.mode === 'region' ? Math.max(REGION_MAX_WIDTH, mapCamera.w) : 20000;
  factor=Math.max(180/mapCamera.w, Math.min(widest/mapCamera.w, factor));
  mapCamera.x+=mapCamera.w*(1-factor)*cx; mapCamera.y+=mapCamera.h*(1-factor)*cy;
  mapCamera.w*=factor; mapCamera.h*=factor; applyMapCamera();
}
function installMapGestures(svg) {
  const points=new Map(); let pinch=0;
  svg.addEventListener('wheel', event=>{event.preventDefault(); const r=svg.getBoundingClientRect(); zoomMap(event.deltaY>0?1.12:1/1.12,(event.clientX-r.left)/r.width,(event.clientY-r.top)/r.height);}, {passive:false});
  svg.onpointerdown=event=>{mapDragged=false; points.set(event.pointerId,{x:event.clientX,y:event.clientY}); svg.setPointerCapture(event.pointerId); pinch=0;};
  svg.onpointermove=event=>{
    const old=points.get(event.pointerId); if (!old) return;
    const dx=event.clientX-old.x, dy=event.clientY-old.y;
    points.set(event.pointerId,{x:event.clientX,y:event.clientY});
    if (Math.abs(dx)+Math.abs(dy)>2) mapDragged=true;
    if(points.size===2) { const [a,b]=[...points.values()]; const distance=Math.hypot(a.x-b.x,a.y-b.y); if(pinch) zoomMap(pinch/distance); pinch=distance; }
    else { const scale=Math.max(mapCamera.w/svg.clientWidth,mapCamera.h/svg.clientHeight); mapCamera.x-=dx*scale; mapCamera.y-=dy*scale; applyMapCamera(); }
  };
  svg.onpointerup=svg.onpointercancel=event=>{points.delete(event.pointerId); pinch=0;};
}

function renderItems(state) {
  const destination = document.getElementById('put-destination');
  const previous = destination.value;
  destination.innerHTML = '';
  for (const item of [...state.items, ...state.inventory]) {
    if (!item.container || !item.open || !item.accessible) continue;
    const option = document.createElement('option');
    option.value = item.id;
    option.textContent = item.name;
    destination.appendChild(option);
  }
  document.getElementById('put-controls').hidden = !destination.options.length;
  if (Array.from(destination.options).some(option => option.value === previous)) destination.value = previous;
  function panel(id, items, carried) {
    const container = document.getElementById(id);
    container.innerHTML = '';
    if (!items.length) container.textContent = carried ? 'You are carrying nothing.' : 'No items here.';
    for (const item of items) {
      const card = document.createElement('div');
      card.className = 'item-card';
      const label = document.createElement('strong');
      label.textContent = item.name + (item.treasure ? ' ★' : '');
      card.appendChild(label);
      const detail = document.createElement('small');
      detail.textContent = item.id + ` · weight ${item.weight}` + (item.container ? (item.surface ? ' · surface' : item.open ? ' · open' : ' · closed') + ` · capacity ${item.contents_weight}/${item.capacity}` : '')
        + (!carried && item.location !== state.room_id ? ` · in/on ${item.location}` : '');
      card.appendChild(detail);
      function button(text, action, target = item.id) {
        const node = document.createElement('button');
        node.textContent = text;
        node.setAttribute('aria-label', `${text} ${target}`);
        node.onclick = () => itemAction(action, target);
        card.appendChild(node);
      }
      button('Examine', 'examine');
      if (carried) {
        button('Drop', 'drop');
        if (destination.options.length) button('Put', 'put');
      } else if (item.takeable && item.accessible) button('Take', 'take');
      if (item.container && !item.surface && item.accessible) button(item.open ? 'Close' : 'Open', item.open ? 'close_container' : 'open_container');
      if (item.contents && item.contents.length) {
        const contents = document.createElement('small');
        contents.textContent = 'Contents: ' + item.contents.map(child => child.name).join(', ');
        card.appendChild(contents);
        if (carried && item.open) {
          for (const child of item.contents) {
            button(`Examine ${child.name}`, 'examine', child.id);
            button(`Take ${child.name}`, 'take', child.id);
          }
        }
      }
      container.appendChild(card);
    }
  }
  panel('room-items', state.items, false);
  panel('inventory-items', state.inventory, true);
}

async function itemAction(action, item, container) {
  const body = {item};
  if (action === 'put') body.container = container || document.getElementById('put-destination').value;
  const response = await fetch(`/api/items/${action}`, {
    method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(body)
  });
  const result = await response.json();
  const output = document.getElementById('item-result');
  output.style.color = result.success ? '#ddd' : '#e66';
  if (result.item) {
    output.textContent = result.item.name + ': ' + result.item.description
      + (result.item.readable_text ? '\\n\\n' + result.item.readable_text : '');
  } else output.textContent = result.message || result.error || 'Done.';
  refresh();
}

async function move(direction) {
  await fetch('/api/move', {
    method: 'POST', headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({direction})
  });
  refresh();
}

async function waitTurn() {
  await fetch('/api/wait', {method: 'POST'});
  refresh();
}

async function doReset() {
  selectedRoom = null;
  document.getElementById('item-result').textContent = '';
  await fetch('/api/reset', {method: 'POST'});
  refresh();
}

async function listModels() {
  if (!hasSavedKey && !document.getElementById('api_key').value.trim()) { promptForKey(); return; }
  const body = {
    provider: currentProvider(),
    base_url: document.getElementById('base_url').value || undefined,
    api_key: document.getElementById('api_key').value || undefined,
  };
  document.getElementById('agent-error').textContent = '';
  const res = await fetch('/api/agent/models', {
    method: 'POST', headers: {'Content-Type': 'application/json'},
    body: JSON.stringify(body)
  });
  const data = await res.json();
  if (!data.ok) {
    document.getElementById('agent-error').textContent = 'Error: ' + data.error;
    return;
  }
  const dropdown = document.getElementById('model');
  const previous = dropdown.value;
  dropdown.replaceChildren(new Option(defaultModelLabel(), ''));
  for (const id of [...new Set(data.models)].sort()) dropdown.add(new Option(id, id));
  dropdown.add(new Option('Enter a model ID...', '__custom__'));
  if ([...dropdown.options].some(option => option.value === previous)) dropdown.value = previous;
  document.getElementById('custom-model-field').hidden = dropdown.value !== '__custom__';
}

async function runAgent(resume = false) {
  if (!hasSavedKey && !document.getElementById('api_key').value.trim()) { promptForKey(); return; }
  const body = {
    resume,
    system_prompt: document.getElementById('system_prompt').value || undefined,
    input_price: document.getElementById('input_price').value === '' ? undefined : Number(document.getElementById('input_price').value),
    cached_input_price: document.getElementById('cached_input_price').value === '' ? undefined : Number(document.getElementById('cached_input_price').value),
    output_price: document.getElementById('output_price').value === '' ? undefined : Number(document.getElementById('output_price').value),
    provider: currentProvider(),
    model: (document.getElementById('model').value === '__custom__' ? document.getElementById('custom-model').value.trim() : document.getElementById('model').value) || undefined,
    base_url: document.getElementById('base_url').value || undefined,
    api_key: document.getElementById('api_key').value || undefined,
    max_steps: parseInt(document.getElementById('max_steps').value) || undefined,
    context_refresh_steps: parseInt(document.getElementById('context_refresh_steps').value) || undefined,
  };
  document.getElementById('agent-error').textContent = '';
  document.getElementById('agent-status').textContent = 'starting...';
  const res = await fetch('/api/agent/run', {
    method: 'POST', headers: {'Content-Type': 'application/json'},
    body: JSON.stringify(body)
  });
  const data = await res.json();
  if (!res.ok || !data.started) {
    if (data.requires_api_key) { hasSavedKey = false; promptForKey(); }
    document.getElementById('agent-error').textContent = 'Error: ' + (data.error || 'failed to start');
    document.getElementById('agent-status').textContent = '';
    return;
  }
  updateControls(true);
  document.getElementById('agent-status').textContent = 'running...';
}

async function stopAgent() {
  await fetch('/api/agent/stop', {method: 'POST'});
  document.getElementById('agent-status').textContent = 'stopping...';
}

async function sendViewerHint() {
  const input = document.getElementById('viewer-hint');
  const status = document.getElementById('viewer-hint-status');
  const hint = input.value.trim();
  if (!hint) { status.textContent = 'Enter a hint first.'; return; }
  try {
    const response = await fetch('/api/agent/hint', {
      method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({hint})
    });
    const result = await response.json();
    status.textContent = response.ok ? 'Queued for the next decision: ' + hint : result.error;
    if (response.ok) {
      if (input.value.trim() === hint) input.value = '';
      document.getElementById('hint-dialog').close();
      pollAgent();
    }
  } catch (_) { status.textContent = 'Could not send the hint. Try again.'; }
}

function notebookEntries(container, entries, empty) {
  const key = JSON.stringify(entries);
  if (container.dataset.key === key) return;
  container.dataset.key = key; container.replaceChildren();
  if (!entries.length) { const note = document.createElement('small'); note.textContent = empty; container.appendChild(note); return; }
  for (const entry of entries) {
    const row = document.createElement('div'); row.className = 'note ' + (entry.kind || '');
    row.append(document.createTextNode(entry.text));
    if (entry.meta) { const meta = document.createElement('small'); meta.textContent = entry.meta; row.appendChild(meta); }
    container.appendChild(row);
  }
}
function renderNotebook(state) {
  const who = {player: 'You asked the game', agent: 'Agent asked the game', viewer: 'You told the agent'};
  notebookEntries(document.getElementById('hint-history'), [...(state.hint_log || [])].reverse().map(h => ({
    kind: h.kind, text: h.text,
    meta: `${who[h.kind]} · turn ${h.turn} · ${h.room}` + (h.kind === 'viewer' ? (h.delivered ? ' · agent has seen it' : ' · queued for the agent') : h.kind === 'player' ? ' · agent did not see this' : ''),
  })), 'No hints yet.');
  const memory = state.memory;
  const sees = [];
  if (memory) {
    sees.push({text: 'Goal: ' + memory.goal, meta: `Mapped ${Object.keys(memory.rooms || {}).length} rooms · now at ${memory.current_room || 'unknown'}`});
    for (const hint of [...(memory.viewer_hints || [])].reverse()) sees.push({kind: 'viewer', text: hint, meta: "Your hint, kept in the agent's memory (last 5)"});
  }
  notebookEntries(document.getElementById('agent-sees'), sees, 'Nothing yet. The agent remembers its goal, the rooms it mapped, your hints and its recent actions.');
  const notes = [...((memory && memory.notes) || [])].reverse().map(n => {
    const args = Object.values(n.args || {}).join(', ');
    const outcome = n.hint || n.error || n.message || n.text || '';
    return {kind: n.hint ? 'agent' : '', text: `${n.tool}(${args})` + (outcome ? ' → ' + (typeof outcome === 'string' ? outcome : JSON.stringify(outcome)) : ''), meta: n.reason || ''};
  });
  notebookEntries(document.getElementById('agent-notes'), notes, "The agent's recent actions and clues appear here.");
}
function renderAutoplay(a) {
  autoplayRunning = !!(a && a.running);
  autoplayInstant = !!(a && a.running && a.speed !== 'slow');
  const status = document.getElementById('autoplay-status');
  if (!a || (!a.running && !a.finished)) { status.textContent = ''; return; }
  if (a.running) status.textContent = `Auto play ${a.step}/${a.total} · ${a.label} · score ${a.score}`;
  else if (a.error) status.textContent = 'Auto play stopped: ' + a.error;
  else if (a.completed) status.textContent = `Auto play complete: ${a.score}/350 points, adventure finished in ${a.step} steps. Reset to play again.`;
  else status.textContent = `Auto play stopped at step ${a.step}/${a.total}. Reset to start over.`;
}
async function startAutoplay() {
  const status = document.getElementById('autoplay-status');
  if (lastState && lastState.turns > 0 && !confirm('Auto play starts a new game and replaces your current one. Save first if you want to keep it. Continue?')) return;
  try {
    const response = await fetch('/api/autoplay/start', {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({speed: document.getElementById('autoplay-speed').value})});
    const result = await response.json();
    if (!response.ok) { status.textContent = result.error; return; }
    lastRenderKey = null; lastTranscriptKey = null;
    await pollAgent();
  } catch (_) { status.textContent = 'Could not start auto play. Try again.'; }
}
async function stopAutoplay() {
  await fetch('/api/autoplay/stop', {method: 'POST'});
}
function showAgentIdentity(state) {
  const badge = document.getElementById('agent-identity');
  const run = state.agent;
  if (run) {
    badge.textContent = `${run.label} · ${run.model}`;
    badge.classList.toggle('live', !!state.running);
    badge.title = state.running ? 'Playing now' : 'Most recent run';
    return;
  }
  const select = document.getElementById('provider');
  const modelChoice = document.getElementById('model');
  const custom = document.getElementById('custom-model').value.trim();
  const model = modelChoice.value === '__custom__' ? custom : modelChoice.value || providerInfo[select.value].model;
  badge.textContent = `${select.selectedOptions[0].textContent} · ${model || '?'}`;
  badge.classList.remove('live'); badge.title = 'Selected; not started yet';
}
async function pollAgent() {
  const res = await fetch('/api/agent/status');
  const state = await res.json();
  canContinue = !!state.can_continue;
  renderAutoplay(state.autoplay);
  updateControls(state.running);
  const usage = state.usage;
  const money = cost => cost === null || cost === undefined ? 'unavailable' : '$' + cost.toFixed(6);
  if (usage) {
    document.getElementById('usage-summary').textContent = `In: ${usage.input_tokens.toLocaleString()} (${usage.cached_input_tokens.toLocaleString()} cached) · Out: ${usage.output_tokens.toLocaleString()} · Est: ${money(usage.estimated_cost_usd)}`;
    document.getElementById('usage-calls').textContent = `Requests: ${usage.requests} · Total tokens: ${usage.total_tokens.toLocaleString()}\\nUncached input: ${usage.uncached_input_tokens.toLocaleString()} · Cache hit: ${usage.cache_hit_percent.toFixed(1)}%\\nReasoning: ${usage.reasoning_tokens.toLocaleString()} (included in output) · Cache writes: ${usage.cache_write_tokens}\\nMissing usage: ${usage.missing_usage_requests} · Unpriced: ${usage.unpriced_requests}\\nKnown subtotal: ${money(usage.known_cost_usd)}\\nIncludes connection check and completion. Text-token estimate; excludes taxes and billing adjustments.\\n\\n` + usage.calls.map(c => `${c.phase}${c.step ? ' ' + c.step : ''} · ${c.model}: ${c.input_tokens ?? '?'} in / ${c.cached_input_tokens ?? '?'} cached / ${c.output_tokens ?? '?'} out · ${money(c.estimated_cost_usd)}${c.unpriced_reason ? ' ? ' + c.unpriced_reason : ''}`).join('\\n');
  }

  const mcpStatus = document.getElementById('mcp-status');
  mcpStatus.dataset.status = state.mcp_status;
  mcpStatus.textContent = `MCP: ${state.mcp_status}`;
  document.getElementById('mcp-tools').textContent =
    state.mcp_tools.length ? ` | Tools: ${state.mcp_tools.join(', ')}` : '';

  const reasoning = document.getElementById('reasoning');
  const decisions = state.log.filter(e => e.type === 'reasoning');
  syncTypedEntries(reasoning, decisions.map((e, i) => ({key: JSON.stringify([i, e]), text: `[${e.step}] ${e.content}`})), (entry, instant) => {
    const output = document.createElement('div'); output.className = 'reasoning-entry'; reasoning.appendChild(output);
    queueTyping(reasoning, output, entry.text, instant);
  });

  const toolEvents = state.log.filter(e => e.type === 'tool');
  const timeline = document.getElementById('log');
  const timelineKey = JSON.stringify(toolEvents);
  if (timeline.dataset.key !== timelineKey) {
    const follow = timeline.scrollTop + timeline.clientHeight >= timeline.scrollHeight - 30;
    timeline.dataset.key = timelineKey; timeline.replaceChildren();
    for (const event of toolEvents) {
      const row = document.createElement('div'); row.className = 'event';
      const label = document.createElement('strong'); label.textContent = `Step ${event.step} ? ${event.tool}(${Object.values(event.args || {}).join(', ')})`;
      const summary = document.createElement('small'); const r = event.result || {};
      summary.textContent = r.error || r.hint || r.message || (r.room_name ? `At ${r.room_name}` : 'Complete');
      const detail = document.createElement('details'); const heading = document.createElement('summary'); heading.textContent = 'Full tool result';
      const raw = document.createElement('pre'); raw.textContent = JSON.stringify(r, null, 2); detail.append(heading, raw); row.append(label, summary, detail); timeline.appendChild(row);
    }
    if (follow) timeline.scrollTop = timeline.scrollHeight;
  }
  document.getElementById('agent-current').textContent = state.running ? 'Agent is playing · decisions and commands appear below.' : (state.result ? 'Run finished.' : 'Ready to explore.');
  document.getElementById('run-result').textContent = state.result ? (state.result.message || state.result.reason || JSON.stringify(state.result)) : '';

  renderNotebook(state);
  showAgentIdentity(state);
  refresh();
  if (state.running) {
    document.getElementById('agent-status').textContent = 'running...';
  } else {
    document.getElementById('agent-status').textContent = state.result ? 'Finished' : 'Idle';
  }
}

setInterval(refresh, 1000);
async function loadSaves() {
  const response = await fetch('/api/saves'); const data = await response.json();
  const dropdown = document.getElementById('saved-game'); const previous = dropdown.value;
  dropdown.replaceChildren();
  for (const saved of data.saves || []) {
    const option = new Option(saved.error ? `${saved.name}: cannot be loaded (${saved.error})` : `${saved.name}: ${saved.room} (${saved.score} points)`, saved.name);
    option.disabled = !!saved.error;
    dropdown.add(option);
  }
  if (!dropdown.value) { const first = [...dropdown.options].find(option => !option.disabled); if (first) dropdown.value = first.value; }
  if ([...dropdown.options].some(option => option.value === previous)) dropdown.value = previous;
}
async function saveGame() {
  const status = document.getElementById('save-status');
  try {
    const response = await fetch('/api/save', {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({name:document.getElementById('save-name').value.trim()})});
    const result = await response.json();
    status.textContent = response.ok ? `Saved "${result.saved.name}" at ${new Date().toLocaleTimeString()} (${result.saved.room}, ${result.saved.score} points).` : result.error;
    if (response.ok) await loadSaves();
  } catch (_) { status.textContent = 'Could not save the game. Try again.'; }
}
document.addEventListener('keydown', event => {
  if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === 's') { event.preventDefault(); saveGame(); }
});
async function restoreGame() {
  const status = document.getElementById('save-status'); const name = document.getElementById('saved-game').value;
  if (!name) { status.textContent = 'Choose a saved game first.'; return; }
  try {
    const response = await fetch('/api/restore', {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({name})});
    const result = await response.json();
    status.textContent = response.ok ? 'Restored. Select Continue Agent to resume without resetting the game.' : result.error;
    if (response.ok) { lastRenderKey = null; lastTranscriptKey = null; await refresh(); await pollAgent(); }
  } catch (_) { status.textContent = 'Could not restore the game. Try again.'; }
}
loadSaves();
updateKeyStatus();
if (!hasSavedKey) openConfiguration();
refresh();
// Poll even while idle, so refreshing the page shows an existing connection.
setInterval(pollAgent, 500);
pollAgent();
</script>
</body>
</html>
"""


@app.get("/")
def index():
    config = AgentConfig()
    return render_template_string(
        PAGE, default_system_prompt=DEFAULT_SYSTEM_PROMPT, default_model=config.model,
        has_api_key=bool(config.api_key and config.api_key.strip()),
        provider=config.provider, providers=list(PROVIDERS.values()),
        saved_keys={pid: bool(find_api_key(pid)) for pid in PROVIDERS},
        provider_info={pid: {"model": AgentConfig(provider=pid).model, "base_url": p.base_url} for pid, p in PROVIDERS.items()})


@app.get("/api/state")
def get_state():
    with _lock:
        state = maze.look()
        state["grid"] = maze.grid_view()
        state["trophy_case"] = maze.trophy_case_progress()
        if not _transcript:
            _transcript.append({"id": 0, "command": "", "text": "ZORK\nThe Great Underground Empire\n\nWelcome, adventurer. Explore the world yourself or select Run Agent to begin."})
        state["transcript"] = list(_transcript)
    return jsonify(state)


@app.post("/api/move")
def post_move():
    direction = request.get_json(force=True).get("direction", "")
    with _lock:
        result = maze.move(direction)
    return jsonify(result)


@app.post("/api/reset")
def post_reset():
    with _lock:
        if _autoplay["running"]:
            return jsonify({"success": False, "error": "Stop auto play before resetting."}), 409
        result = maze.reset()
        _hint_log.clear()
    return jsonify(result)


@app.post("/api/look")
def post_look():
    with _lock:
        result = maze.observe()
    return jsonify(result)


@app.post("/api/wait")
def post_wait():
    with _lock:
        result = maze.wait()
    return jsonify(result)


@app.get("/api/score")
def get_score():
    with _lock:
        result = maze.score()
    return jsonify(result)


@app.route("/api/inventory", methods=["GET", "POST"])
def get_inventory():
    with _lock:
        result = maze.inventory() if request.method == "POST" else maze.look()
    return jsonify(result)


@app.post("/api/items/<action>")
def item_action(action):
    handlers = {"take": maze.take, "drop": maze.drop, "examine": maze.examine,
                "open_container": maze.open_container, "close_container": maze.close_container,
                "put": maze.put}
    if action not in handlers:
        return jsonify({"success": False, "error": "Unknown item action."}), 404
    body = request.get_json(silent=True) or {}
    if not isinstance(body, dict) or not isinstance(body.get("item"), str):
        return jsonify({"success": False, "error": "Provide an item ID or name as a string."}), 400
    arguments = {"item": body["item"]}
    if action == "put":
        if not isinstance(body.get("container"), str):
            return jsonify({"success": False, "error": "Provide a container ID or name as a string."}), 400
        arguments["container"] = body["container"]
    with _lock:
        result = handlers[action](**arguments)
    return jsonify(result)


@app.post("/api/hint")
def request_hint():
    with _lock:
        result = maze.hint()
        if result.get("hint"):
            # The browser says who it is; the agent reaches this endpoint through the MCP server.
            _log_hint("player" if request.headers.get("X-Maze-Source") == "ui" else "agent", result["hint"])
        return jsonify(result)


@app.post("/api/interact")
def interact():
    body = request.get_json(silent=True)
    if not isinstance(body, dict) or any(not isinstance(body.get(k), str) for k in ("action", "target")) or not isinstance(body.get("item", ""), str):
        return jsonify({"success": False, "error": "Provide action, target and optional item strings."}), 400
    with _lock:
        result = maze.interact(body["action"], body["target"], body.get("item", ""))
    return jsonify(result)


def _run_agent(config: AgentConfig) -> None:
    # Flask starts this regular function in a background thread.
    with _lock:
        _agent_state["mcp_status"] = "connecting"
        _agent_state["mcp_tools"] = []
    try:
        result = asyncio.run(_run_agent_with_mcp(config))
        mcp_status = "disconnected"
    except Exception as exc:
        logging.exception("Agent run crashed unexpectedly")
        result = {"success": False, "reason": f"{type(exc).__name__}: {exc}"}
        mcp_status = "error"

    # Connection/discovery errors must clear the running flag too.
    with _lock:
        _agent_state["result"] = result
        if result.get("memory"):
            _agent_state["memory"] = result["memory"]
            _agent_state["can_continue"] = not any(maze.look().get(k) for k in ("game_over", "at_goal"))
        _agent_state["running"] = False
        try:
            if maze._maze.turns > 0:  # never replace a good autosave with an untouched game
                saved_games.save("autosave", maze._maze, _agent_state["memory"])
        except Exception:  # noqa: BLE001 - an autosave failure must never hide the run result
            logging.exception("Autosave failed")
        _agent_state["mcp_status"] = mcp_status
        _agent_state["mcp_tools"] = []
        _pending_viewer_hints.clear()


async def _run_agent_with_mcp(config: AgentConfig) -> dict:
    server = StdioServerParameters(
        command=sys.executable,
        args=["-m", "maze_mcp"],
        env={"MAZE_GAME_URL": os.environ.get("MAZE_GAME_URL", "http://127.0.0.1:5000")},
    )
    async with Client(server) as client:
        loop = asyncio.get_running_loop()
        discovered = await client.list_tools()
        with _lock:
            _agent_state["mcp_status"] = "connected"
            _agent_state["mcp_tools"] = [tool.name for tool in discovered.tools]

        agent_tools = [
            {
                "type": "function",
                "function": {
                    "name": tool.name,
                    "description": tool.description or "",
                    "parameters": tool.input_schema,
                },
            }
            for tool in discovered.tools
        ]

        def on_step(event: dict) -> None:
            with _lock:
                if event["type"] == "usage":
                    _agent_state["usage"] = event["usage"]
                elif event["type"] == "memory":
                    _agent_state["memory"] = event["memory"]
                else:
                    _agent_state["log"].append(event)

        def executor(name: str, args: dict) -> dict:
            # The agent's worker thread submits calls to this event loop.
            # Do not hold _lock: the Flask routes need it to update the maze.
            future = asyncio.run_coroutine_threadsafe(
                client.call_tool(name, args),
                loop,
            )
            result = future.result()

            if result.is_error:
                raise RuntimeError(f"MCP tool failed: {result.content}")

            if result.structured_content is not None:
                return result.structured_content

            for content in result.content:
                if content.type == "text":
                    return json.loads(content.text)

            raise RuntimeError("MCP tool returned no JSON result")

        # Keep the connection open for the entire run. The worker thread
        # leaves the event loop free to handle the executor's MCP requests.
        agent = GPTAgent(tools=agent_tools, executor=executor, config=config)
        with _lock:
            initial_usage = _agent_state["usage"]
            initial_memory = _agent_state["memory"]
        return await asyncio.to_thread(
            agent.run,
            "Explore the Great Underground Empire, discover treasures, and deposit them in the trophy case. Follow in-game discoveries to complete the adventure.",
            on_step=on_step,
            stop_event=_stop_event,
            initial_usage=initial_usage,
            get_hints=_drain_viewer_hints,
            initial_memory=initial_memory,
        )


def remember_selection(provider: str, model: str | None = None) -> None:
    """Persist the last provider (and its model) so the next session resumes there."""
    values = {"MAZE_AGENT_PROVIDER": provider}
    if model:
        values[model_env_name(provider)] = model
    try:
        with _lock:
            if ENV_FILE.is_symlink():
                return
            for name, value in values.items():
                set_key(str(ENV_FILE), name, value)
                os.environ[name] = value
    except OSError:
        logging.warning("Could not remember the provider selection")


@app.post("/api/agent/run")
def post_agent_run():
    with _lock:
        if _agent_state["running"]:
            return jsonify({"started": False, "error": "agent already running"}), 409
        if _autoplay["running"]:
            return jsonify({"started": False, "error": "Stop auto play before running the agent."}), 409

    body = request.get_json(silent=True) or {}
    try:
        resume = body.get("resume", False)
        if type(resume) is not bool:
            raise ValueError("Resume must be true or false.")
        kwargs = {k: v for k, v in body.items() if k != "resume" and v not in (None, "")}
        config = AgentConfig(**kwargs)
    except (AttributeError, TypeError, ValueError) as exc:
        return jsonify({"started": False, "error": str(exc)}), 400

    if not isinstance(config.api_key, str) or not config.api_key.strip():
        return jsonify({"started": False, "requires_api_key": True, "error": "Enter an API key under Configure agent. You can save it locally for future sessions."}), 400

    with _lock:
        if resume and (not _agent_state["can_continue"] or not _agent_state["memory"]):
            return jsonify({"started": False, "error": "Restore a save or play an agent run before continuing."}), 400

    # Validate the model/key/base_url actually work before committing to a full run.
    probe = GPTAgent(tools=[], executor=lambda name, args: {}, config=config)
    check = probe.check_connection()
    if not check["ok"]:
        return jsonify({"started": False, "error": check["error"]}), 400
    remember_selection(config.provider, config.model)

    with _lock:
        if _agent_state["running"]:
            return jsonify({"started": False, "error": "agent already running"}), 409
        if resume:
            memory = ContextMemory.restore(_agent_state["memory"])
            memory.observe("continue_observation", {}, maze.look())
            _agent_state["memory"] = memory.snapshot()
        else:
            maze.reset()
            _hint_log.clear()
            _transcript.clear()
            _agent_state["memory"] = None
        _agent_state["can_continue"] = False
        _agent_state["running"] = True
        _agent_state["log"] = []
        _agent_state["result"] = None
        _agent_state["usage"] = check.get("usage")
        _agent_state["agent"] = {"provider": config.provider, "label": PROVIDERS[config.provider].label,
                                 "model": config.model}
        _agent_state["viewer_hints"] = 0
        _pending_viewer_hints.clear()
    _stop_event.clear()

    threading.Thread(target=_run_agent, args=(config,), daemon=True).start()
    return jsonify({"started": True})


def _run_autoplay(delay: float) -> None:
    """Play the walkthrough one step at a time, so the transcript and map show it live."""
    global _transcript_sequence
    error = None
    try:
        for number, step in enumerate(autoplay.STEPS, 1):
            if _autoplay_stop.is_set():
                break
            with _lock:
                world = maze._maze
                result = autoplay.apply_step(world, step)
                _transcript_sequence += 1
                _transcript.append({"id": _transcript_sequence, "command": autoplay.command_text(step), "text": _game_text(result)})
                del _transcript[:-300]
                _autoplay.update(step=number, label=autoplay.LABELS[number - 1], score=world._score())
                if result.get("success", True) is False:
                    error = f"{autoplay.command_text(step)!r} did not work: {result.get('error')}"
                    break
            _autoplay_stop.wait(delay)
    except Exception as exc:  # noqa: BLE001 - report it to the player instead of dying silently
        logging.exception("Auto play crashed")
        error = f"{type(exc).__name__}: {exc}"
    with _lock:
        _autoplay.update(running=False, finished=True, error=error,
                         completed=error is None and autoplay.finished(maze._maze))


@app.post("/api/autoplay/start")
def post_autoplay_start():
    body = request.get_json(silent=True)
    speed = body.get("speed", "normal") if isinstance(body, dict) else "normal"
    if speed not in AUTOPLAY_DELAYS:
        return jsonify({"error": "Speed must be slow, normal or fast."}), 400
    with _lock:
        if _agent_state["running"]:
            return jsonify({"error": "Stop the agent before starting auto play."}), 409
        if _autoplay["running"]:
            return jsonify({"error": "Auto play is already running."}), 409
        autoplay.start(maze._maze)
        _transcript.clear()
        _hint_log.clear()
        _pending_viewer_hints.clear()
        _agent_state.update({"memory": None, "can_continue": False, "log": [], "result": None, "usage": None, "viewer_hints": 0})
        _autoplay.update(running=True, finished=False, completed=False, step=0, speed=speed,
                         label=autoplay.LABELS[0], score=0, error=None)
        _autoplay_stop.clear()
    threading.Thread(target=_run_autoplay, args=(AUTOPLAY_DELAYS[speed],), daemon=True).start()
    with _lock:
        return jsonify(dict(_autoplay))


@app.post("/api/autoplay/stop")
def post_autoplay_stop():
    _autoplay_stop.set()
    return jsonify({"stopping": True})


@app.post("/api/agent/stop")
def post_agent_stop():
    _stop_event.set()
    return jsonify({"stopping": True})


@app.get("/api/saves")
def get_saves():
    try:
        return jsonify({"saves": saved_games.list_saves()})
    except (OSError, ValueError):
        return jsonify({"saves": [], "error": "Could not read local saves."}), 400


@app.post("/api/save")
def post_save():
    body = request.get_json(silent=True)
    name = body.get("name") if isinstance(body, dict) else None
    try:
        with _lock:
            saved = saved_games.save(name, maze._maze, _agent_state["memory"])
        return jsonify({"saved": saved})
    except (OSError, ValueError, TypeError, KeyError):
        return jsonify({"error": "Could not save. Use a name of 1–48 letters, numbers, underscores or hyphens and a writable saves folder."}), 400


@app.post("/api/restore")
def post_restore():
    global _transcript_sequence
    body = request.get_json(silent=True)
    name = body.get("name") if isinstance(body, dict) else None
    try:
        with _lock:
            if _agent_state["running"]:
                return jsonify({"error": "Stop the agent before restoring."}), 409
            if _autoplay["running"]:
                return jsonify({"error": "Stop auto play before restoring."}), 409
            world, memory = saved_games.load(name)
            maze._maze = world
            _agent_state.update({"memory": memory, "can_continue": not world.dead,
                                 "log": [], "result": None, "usage": None, "viewer_hints": 0})
            _pending_viewer_hints.clear()
            _hint_log.clear()
            _transcript.clear()
            _transcript_sequence += 1
            _transcript.append({"id": _transcript_sequence, "command": "restore " + name, "text": _game_text(world.look())})
        return jsonify({"restored": True})
    except (OSError, ValueError, TypeError, KeyError, RecursionError):
        return jsonify({"error": "Could not restore this save. The file may be missing, incompatible, or invalid."}), 400


def _drain_viewer_hints() -> list[str]:
    with _lock:
        hints = list(_pending_viewer_hints)
        _pending_viewer_hints.clear()
        for entry in _hint_log:
            if entry["kind"] == "viewer":
                entry["delivered"] = True
    return hints


@app.post("/api/agent/hint")
def post_viewer_hint():
    body = request.get_json(silent=True)
    hint = body.get("hint") if isinstance(body, dict) else None
    if not isinstance(hint, str) or not hint.strip() or len(hint) > 1000:
        return jsonify({"error": "Enter a hint of 1 to 1000 characters."}), 400
    with _lock:
        if not _agent_state["running"] or _stop_event.is_set():
            return jsonify({"error": "Start an agent run before sending hints."}), 409
        if len(_pending_viewer_hints) >= 10:
            return jsonify({"error": "Ten hints are already queued. Wait for the next decision."}), 429
        _pending_viewer_hints.append(hint.strip())
        _log_hint("viewer", hint.strip(), delivered=False)
        _agent_state["viewer_hints"] += 1
    return jsonify({"queued": True})


@app.get("/api/agent/status")
def get_agent_status():
    with _lock:
        return jsonify({**_agent_state, "hint_log": list(_hint_log), "autoplay": dict(_autoplay)})


@app.post("/api/agent/models")
def post_agent_models():
    body = request.get_json(silent=True) or {}
    kwargs = {k: v for k, v in body.items() if k in ("provider", "base_url", "api_key") and v not in (None, "")}
    try:
        config = AgentConfig(**kwargs)
    except (TypeError, ValueError) as exc:
        return jsonify({"ok": False, "error": str(exc)}), 400
    if not isinstance(config.api_key, str) or not config.api_key.strip():
        return jsonify({"ok": False, "requires_api_key": True, "error": "Enter an API key under Configure agent to load models."}), 400
    return jsonify(list_models(config))


@app.post("/api/agent/key")
def save_api_key():
    body = request.get_json(silent=True)
    key = body.get("api_key") if isinstance(body, dict) else None
    provider = (body.get("provider") if isinstance(body, dict) else None) or "openai"
    if provider not in PROVIDERS:
        return jsonify({"error": "Choose a supported provider."}), 400
    env_name = key_env_name(provider)
    if not isinstance(key, str) or not key.strip() or len(key) > 1000 or any(char in key for char in ("\n", "\r", "\x00")):
        return jsonify({"error": "Enter a valid API key."}), 400
    try:
        with _lock:
            if ENV_FILE.is_symlink():
                return jsonify({"error": "Cannot save to a linked .env file."}), 400
            set_key(str(ENV_FILE), env_name, key.strip())
            os.environ[env_name] = key.strip()
    except OSError:
        return jsonify({"error": "Could not save the API key. Check that the game folder is writable."}), 500
    remember_selection(provider)
    return jsonify({"saved": True})


def announce_and_open(url: str, open_browser: bool = True) -> None:
    """Tell the player where the game is and, unless disabled, open it in their browser."""
    print(f"\n  Zork Agent Explorer is running at {url}\n"
          "  Open that address in a browser to play. Press Ctrl+C here to stop.\n", flush=True)
    if open_browser and not os.environ.get("MAZE_NO_BROWSER"):
        # Wait for the server to start accepting connections before opening the page.
        threading.Timer(1.0, webbrowser.open, args=[url]).start()


if __name__ == "__main__":
    port = int(os.environ.get("MAZE_PORT", "5000"))
    announce_and_open(f"http://127.0.0.1:{port}")
    app.run(host="127.0.0.1", port=port, debug=False, threaded=True, use_reloader=False)
