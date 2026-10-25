"""Upload the finished pictures, clips and treasure layers to the public media bucket, so the game and the review page
can show them on machines that do not have video/assets (it is not in git). Uses Cloudflare's wrangler CLI, logged in
to the account that owns the bucket. Files already uploaded (listed in --done) are skipped. From maze/:
  uv run python -m video.publish_media --dry-run
  uv run python -m video.publish_media
After remaking a picture or clip, upload it again with --all (or remove its line from the --done file).
"""
from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from video.gen_keyframes import VIDEO_DIR
from video.graph import load_manifest

BUCKET = "auto-zork-media"
DONE_FILE = VIDEO_DIR / "assets" / "published.txt"
TYPES = {".png": "image/png", ".jpg": "image/jpeg", ".mp4": "video/mp4"}


def finished(manifest):
    """Asset paths of every generated or approved keyframe, clip and layer (never the uncropped .orig.mp4 copies)."""
    paths = [k.image_path for k in manifest.keyframes if k.image_path and k.status in ("generated", "approved")]
    paths += [c.path for e in manifest.actions for c in e.clips if c.path and c.status in ("generated", "approved")]
    paths += [layer.image_path for layer in manifest.layers if layer.image_path]
    return sorted(set(paths))


def wrangler_command():
    """wrangler installed globally, else through npx; None when neither is there."""
    if found := shutil.which("wrangler"):
        return [found]
    if npx := shutil.which("npx"):
        return [npx, "--no-install", "wrangler"]
    return None


def upload(wrangler, bucket, path):
    local = VIDEO_DIR / path
    result = subprocess.run([*wrangler, "r2", "object", "put", f"{bucket}/{path}", "--remote", "--file", str(local),
                             "--content-type", TYPES[local.suffix], "--cache-control", "public, max-age=86400"],
                            capture_output=True, text=True, encoding="utf-8", errors="replace")
    return result.returncode == 0, result.stderr.strip()[-300:]


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--bucket", default=BUCKET)
    parser.add_argument("--done", type=Path, default=DONE_FILE, help="list of paths already uploaded")
    parser.add_argument("--all", action="store_true", help="upload again even if listed in --done")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)
    done = set() if args.all or not args.done.exists() else set(args.done.read_text(encoding="utf-8").split())
    todo = [p for p in finished(load_manifest()) if p not in done and (VIDEO_DIR / p).exists()]
    print(f"{len(todo)} files to upload to {args.bucket}.")
    if args.dry_run or not todo:
        return 0
    if not (wrangler := wrangler_command()):
        print("wrangler not found: npm install -g wrangler, then wrangler login")
        return 1

    def one(path):
        ok, error = upload(wrangler, args.bucket, path)
        print(("uploaded " if ok else "FAILED ") + path + ("" if ok else f": {error}"), flush=True)
        if ok:
            with args.done.open("a", encoding="utf-8") as f:
                f.write(path + "\n")
        return ok

    with ThreadPoolExecutor(6) as pool:
        results = list(pool.map(one, todo))
    print(f"Done: {sum(results)} uploaded, {results.count(False)} failed.")
    return 0 if all(results) else 1


if __name__ == "__main__":
    sys.exit(main())
