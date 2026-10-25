"""Local review page: every activity as start frame -> end frame, every keyframe and treasure picture with
Approve / Reject, and a tool to mark where the trophy case's shelves are so treasures can be layered into it.

Rejecting marks a picture for video.gen_keyframes or video.gen_layers to make again. Run from the maze folder:
  uv run python -m video.review          # http://127.0.0.1:5001
"""
from __future__ import annotations

import os
import threading

from flask import Flask, abort, jsonify, render_template_string, request, send_from_directory

from video.config import ASSET_URL
from video.graph import load_manifest, save_manifest
from video.gen_keyframes import VIDEO_DIR, versioned
from video.layers import LAYERED_FIXTURES, placed, slots

app = Flask(__name__)


def media(path):
    """A picture's URL: served from disk here when it is here, else from the public media bucket."""
    return f"/{versioned(path)}" if (VIDEO_DIR / path).exists() else f"{ASSET_URL}/{path}"


app.jinja_env.filters["media"] = media
_lock = threading.Lock()

PAGE = """<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>Keyframe review</title>
<style>
  :root { --bg:#14161a; --panel:#1d2026; --line:#2c3038; --text:#e4e6ea; --muted:#9aa0aa; --ok:#5fb37a; --bad:#d9675f; --pend:#c9a54a; }
  * { box-sizing:border-box; }
  body { margin:0; background:var(--bg); color:var(--text); font:14px/1.45 system-ui, sans-serif; }
  header { position:sticky; top:0; z-index:2; background:var(--bg); border-bottom:1px solid var(--line); padding:12px 16px;
           display:flex; gap:16px; align-items:center; flex-wrap:wrap; }
  h1 { font-size:16px; margin:0; } .muted { color:var(--muted); }
  nav a { color:var(--text); margin-right:12px; } main { padding:16px; max-width:1500px; margin:auto; }
  h2 { font-size:15px; margin:28px 0 10px; border-bottom:1px solid var(--line); padding-bottom:6px; }
  .frame { position:relative; background:#000; aspect-ratio:16/9; border-radius:6px; overflow:hidden; }
  .frame > img { width:100%; height:100%; object-fit:fill; display:block; }
  .frame .slot { position:absolute; object-fit:contain; object-position:bottom center; }
  .frame div.slot { border:1px dashed #fff6; font-size:9px; color:#fffa; overflow:hidden; }
  .frame .region { position:absolute; border:2px solid #4fc3f7; pointer-events:none; }
  .frame.marking { cursor:crosshair; outline:2px solid #4fc3f7; }
  .layer { background:repeating-conic-gradient(#2a2d33 0 25%, #212329 0 50%) 0 0/16px 16px; aspect-ratio:1; border-radius:6px; }
  .layer img { width:100%; height:100%; object-fit:contain; }
  .frame .empty { position:absolute; inset:0; padding:10px; font-size:12px; color:var(--muted); overflow:auto; }
  .badge { position:absolute; top:6px; left:6px; font-size:11px; padding:2px 6px; border-radius:4px; background:#000a; }
  .approved { color:var(--ok); } .rejected { color:var(--bad); } .pending, .generated { color:var(--pend); }
  .edge { display:grid; grid-template-columns:1fr minmax(180px, 0.8fr) 1fr; gap:12px; align-items:center;
          background:var(--panel); border:1px solid var(--line); border-radius:8px; padding:10px; margin-bottom:10px; }
  .edge .mid { text-align:center; } .edge .cmd { font-weight:600; } .edge .motion { font-size:12px; color:var(--muted); }
  .cap { font-size:12px; color:var(--muted); margin-top:4px; }
  .grid { display:grid; grid-template-columns:repeat(auto-fill, minmax(320px, 1fr)); gap:14px; }
  .card { background:var(--panel); border:1px solid var(--line); border-radius:8px; padding:10px; }
  .card .prompt { font-size:12px; color:var(--muted); max-height:7.5em; overflow:auto; margin:6px 0; }
  button { background:#2a2f38; color:var(--text); border:1px solid var(--line); border-radius:5px; padding:5px 10px; cursor:pointer; }
  button.ok:hover { border-color:var(--ok); } button.bad:hover { border-color:var(--bad); }
  @media (max-width:700px) { .edge { grid-template-columns:1fr; } }
</style></head><body>
<header><h1>Keyframe review</h1>
  <span class="muted">{{ made }} of {{ frames|length }} keyframes made, {{ approved }} approved. Spent ${{ '%.2f'|format(spent) }}.</span>
  <nav><a href="#activities">Activities</a><a href="#keyframes">Keyframes</a><a href="#layers">Treasures</a></nav></header>
<main>
{% macro frame(k, preview=False) %}
  <div class="frame" data-id="{{ k.id }}">{% if k.image_path %}<img src="{{ k.image_path|media }}" alt="{{ k.room_name }}" loading="lazy">
  {% else %}<div class="empty">{{ k.image_prompt or k.edit_prompt or 'No prompt yet' }}</div>{% endif %}
  {% if preview %}{% for fixture, box in k.layer_regions.items() %}
    <div class="region" style="left:{{ box[0]*100 }}%;top:{{ box[1]*100 }}%;width:{{ (box[2]-box[0])*100 }}%;height:{{ (box[3]-box[1])*100 }}%"></div>
  {% endfor %}{% for s in previews[k.id] %}{% if s.src %}<img class="slot" src="{{ s.src }}" alt="{{ s.id }}"
      style="left:{{ s.left }}%;top:{{ s.top }}%;width:{{ s.width }}%;height:{{ s.height }}%">{% else %}<div class="slot"
      style="left:{{ s.left }}%;top:{{ s.top }}%;width:{{ s.width }}%;height:{{ s.height }}%">{{ s.id }}</div>{% endif %}{% endfor %}{% endif %}
  <span class="badge {{ k.status }}">{{ k.status }}</span></div>
  <div class="cap">{{ k.room_name }}{% if k.derived_from %} (changed){% endif %} &middot; {{ k.id }}</div>
{% endmacro %}
<section id="activities">
{% for room, edges in groups %}
  <h2>{{ room }}</h2>
  {% for e in edges %}<div class="edge">
    <div>{{ frame(frames[e.start_frame]) }}</div>
    <div class="mid"><div class="cmd">{{ e.verbs|join(', ') }} &rarr;</div><div class="motion">{{ e.motion_prompt or '' }}</div></div>
    <div>{{ frame(frames[e.end_frame]) }}</div></div>{% endfor %}
{% endfor %}
</section>
<section id="keyframes"><h2>Keyframes</h2><div class="grid">
{% for k in frames.values() %}<div class="card" id="k-{{ k.id }}">{{ frame(k, k.id in previews) }}
  <div class="prompt">{{ k.edit_prompt or k.image_prompt or '' }}</div>
  <button class="ok" onclick="mark('{{ k.id }}','approved')">Approve</button>
  <button class="bad" onclick="mark('{{ k.id }}','rejected')">Reject (make again)</button>
  {% for fixture in markable.get(k.id, []) %}<button onclick="startMarking('{{ k.id }}','{{ fixture }}')">Mark {{ fixture|lower|replace('-', ' ') }} shelves</button>{% endfor %}
  </div>{% endfor %}
</div></section>
<section id="layers"><h2>Treasures (layered into the trophy case)</h2><div class="grid">
{% for layer in layers %}<div class="card"><div class="layer">{% if layer.image_path %}<img src="{{ layer.image_path|media }}" alt="{{ layer.name }}" loading="lazy">{% endif %}</div>
  <div class="cap"><span class="{{ layer.status }}">{{ layer.status }}</span> &middot; {{ layer.name }} &middot; {{ layer.id }}</div>
  <button class="ok" onclick="markLayer('{{ layer.id }}','approved')">Approve</button>
  <button class="bad" onclick="markLayer('{{ layer.id }}','rejected')">Reject (make again)</button></div>{% endfor %}
</div></section></main>
<script>
async function post(url, body) {
  const r = await fetch(url, {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify(body)});
  if (r.ok) location.reload(); else alert((await r.json()).error);
}
const mark = (id, status) => post('/api/keyframes/' + encodeURIComponent(id), {status});
const markLayer = (id, status) => post('/api/layers/' + encodeURIComponent(id), {status});
// Drag a box over the fixture's shelves on the keyframe; the treasures' slots are laid out inside it.
function startMarking(id, fixture) {
  const frame = document.querySelector('#k-' + CSS.escape(id) + ' .frame');
  frame.classList.add('marking');
  let start = null, box = null;
  const at = e => { const r = frame.getBoundingClientRect();
    return [Math.min(1, Math.max(0, (e.clientX - r.left) / r.width)), Math.min(1, Math.max(0, (e.clientY - r.top) / r.height))]; };
  frame.onpointerdown = e => { e.preventDefault(); start = at(e); box = document.createElement('div'); box.className = 'region'; frame.appendChild(box); };
  frame.onpointermove = e => { if (!start) return; const [x, y] = at(e);
    Object.assign(box.style, {left: Math.min(x, start[0]) * 100 + '%', top: Math.min(y, start[1]) * 100 + '%',
                              width: Math.abs(x - start[0]) * 100 + '%', height: Math.abs(y - start[1]) * 100 + '%'}); };
  frame.onpointerup = e => { if (!start) return; const [x, y] = at(e);
    const region = [Math.min(x, start[0]), Math.min(y, start[1]), Math.max(x, start[0]), Math.max(y, start[1])];
    start = null; post('/api/keyframes/' + encodeURIComponent(id) + '/region', {fixture, region}); };
}
</script></body></html>"""


@app.get("/")
def index():
    manifest = load_manifest()
    frames = {k.id: k for k in manifest.keyframes}
    groups = {}
    for e in manifest.actions:
        if e.type in ("move", "action"):
            groups.setdefault(frames[e.start_frame].room_name, []).append(e)
    layers = {layer.id: layer for layer in manifest.layers}
    # Fixtures are marked on a room's first look; later looks are edits of it and keep the same framing.
    markable = {k.id: [f for f, info in LAYERED_FIXTURES.items() if info["room"] == k.room]
                for k in frames.values() if k.image_path and not k.derived_from}
    previews = {k.id: [{"id": i, "src": media(layers[i].image_path) if i in layers and layers[i].image_path else None,
                        **placed(box, i)} for box in k.layer_regions.values() for i in slots()]
                for k in frames.values() if k.layer_regions}
    return render_template_string(PAGE, frames=frames, groups=list(groups.items()), spent=manifest.spent_usd,
                                  made=sum(bool(k.image_path) for k in frames.values()),
                                  approved=sum(k.status == "approved" for k in frames.values()),
                                  layers=manifest.layers, markable=markable, previews=previews)


@app.get("/assets/<path:name>")
def assets(name):
    return send_from_directory(VIDEO_DIR / "assets", name)


@app.post("/api/keyframes/<keyframe_id>")
def set_status(keyframe_id):
    status = (request.get_json(silent=True) or {}).get("status")
    if status not in ("approved", "rejected"):
        return jsonify(error="status must be approved or rejected"), 400
    with _lock:
        manifest = load_manifest()
        keyframe = next((k for k in manifest.keyframes if k.id == keyframe_id), None)
        if keyframe is None:
            abort(404)
        if status == "approved" and not keyframe.image_path:
            return jsonify(error="There is no image to approve yet."), 400
        keyframe.status = status
        save_manifest(manifest)
    return jsonify(ok=True)


@app.post("/api/keyframes/<keyframe_id>/region")
def set_region(keyframe_id):
    body = request.get_json(silent=True) or {}
    fixture, region = body.get("fixture"), body.get("region")
    if fixture not in LAYERED_FIXTURES:
        return jsonify(error="unknown fixture"), 400
    if not (isinstance(region, list) and len(region) == 4 and all(isinstance(v, (int, float)) and 0 <= v <= 1 for v in region)
            and region[2] - region[0] > 0.02 and region[3] - region[1] > 0.02):
        return jsonify(error="Drag a box over the shelves."), 400
    with _lock:
        manifest = load_manifest()
        keyframe = next((k for k in manifest.keyframes if k.id == keyframe_id), None)
        if keyframe is None:
            abort(404)
        keyframe.layer_regions[fixture] = [round(v, 4) for v in region]
        save_manifest(manifest)
    return jsonify(ok=True)


@app.post("/api/layers/<layer_id>")
def set_layer_status(layer_id):
    status = (request.get_json(silent=True) or {}).get("status")
    if status not in ("approved", "rejected"):
        return jsonify(error="status must be approved or rejected"), 400
    with _lock:
        manifest = load_manifest()
        layer = next((x for x in manifest.layers if x.id == layer_id), None)
        if layer is None:
            abort(404)
        if status == "approved" and not layer.image_path:
            return jsonify(error="There is no picture to approve yet."), 400
        layer.status = status
        save_manifest(manifest)
    return jsonify(ok=True)


if __name__ == "__main__":
    port = int(os.environ.get("VIDEO_REVIEW_PORT", "5001"))
    print(f"Keyframe review: http://127.0.0.1:{port}")
    app.run(host="127.0.0.1", port=port)
