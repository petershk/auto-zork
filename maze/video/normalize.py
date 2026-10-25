"""Make every clip line up exactly with the stills it starts and ends on.

Video models reframe: Seedance returns clips zoomed into their first and last frames by 3-9% (the same amount at
both ends of a clip, but different from clip to clip), so cutting from a still to a clip jumps. The player therefore
shows every keyframe cropped to one fixed view (DISPLAY_ZOOM, centred), and each clip is measured against its
keyframes and cropped with ffmpeg to that same view. The untouched download is kept beside it as <name>.orig.mp4.
Run from the maze folder (gen_clips does this for new clips):
  uv run python -m video.normalize          # every clip not yet normalized
  uv run python -m video.normalize --all    # again, from the kept originals
"""
from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

from video.check_seams import _grey, end_frames, ssim, view
from video.config import DISPLAY_ZOOM
from video.gen_keyframes import VIDEO_DIR
from video.graph import load_manifest, save_manifest


def fit(frame_path, keyframe_path):
    """(zoom, dx, dy, score): how the clip frame is framed relative to its keyframe, coarse search then refined."""
    from PIL import Image

    target = _grey(Image.open(frame_path))
    key = Image.open(keyframe_path)
    score = lambda z, x, y: ssim(target, _grey(view(key, z, x, y)))  # noqa: E731
    zoom = max((1 + 0.01 * i for i in range(0, 21)), key=lambda z: score(z, 0, 0))
    steps = [i * 0.005 for i in range(-6, 7)]
    dx, dy = max(((x, y) for x in steps for y in steps), key=lambda xy: score(zoom, *xy))
    zoom = max((zoom + 0.0025 * i for i in range(-4, 5)), key=lambda z: score(z, dx, dy))
    return zoom, dx, dy, score(zoom, dx, dy)


def crop_box(zoom, dx, dy, display=DISPLAY_ZOOM):
    """The part of the clip (fractions of its frame: left, top, width, height) that shows the display view, or None
    if the clip does not reach that far (it is zoomed in more than the display, or shifted too much)."""
    size = zoom / display
    left, top = 0.5 - dx * zoom - size / 2, 0.5 - dy * zoom - size / 2
    if size > 1 or left < 0 or top < 0 or left + size > 1 or top + size > 1:
        return None
    return left, top, size, size


def normalize_clip(clip, frames):
    """Measure a generated clip and crop it to the display view; returns (box or None, fit)."""
    path = VIDEO_DIR / clip.path
    original = path.with_suffix(".orig.mp4")
    if not original.exists():
        shutil.copyfile(path, original)
    with tempfile.TemporaryDirectory() as folder:
        first, last = end_frames(original, Path(folder))
        a = fit(first, VIDEO_DIR / frames[clip.from_].image_path)
        b = fit(last, VIDEO_DIR / frames[clip.to].image_path)
    zoom, dx, dy = ((a[i] + b[i]) / 2 for i in range(3))  # the same at both ends; average out the noise
    box = crop_box(zoom, dx, dy)
    if box:
        left, top, w, h = box
        ffmpeg = shutil.which("ffmpeg")
        vf = f"crop=iw*{w:.5f}:ih*{h:.5f}:iw*{left:.5f}:ih*{top:.5f},scale=1280:720:flags=lanczos,setsar=1"
        subprocess.run([ffmpeg, "-v", "error", "-y", "-i", str(original), "-vf", vf, "-c:v", "libx264", "-crf", "18",
                        "-preset", "medium", "-pix_fmt", "yuv420p", "-c:a", "copy", str(path)], check=True)
    return box, (round(zoom, 4), round(dx, 4), round(dy, 4))


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--all", action="store_true", help="normalize clips that already are, from their originals")
    args = parser.parse_args(argv)
    if not shutil.which("ffmpeg"):
        raise SystemExit("ffmpeg is not on PATH; install it to normalize clips.")
    manifest = load_manifest()
    frames = {k.id: k for k in manifest.keyframes}
    flagged = []
    for edge in manifest.actions:
        for clip in edge.clips:
            if not clip.path or (clip.normalized and not args.all):
                continue
            box, measured = normalize_clip(clip, frames)
            clip.fit, clip.normalized = list(measured), bool(box)
            clip.first_frame_score = clip.last_frame_score = None
            print(f"  {edge.id:<52} zoom {measured[0]:.3f} shift {measured[1]:+.3f},{measured[2]:+.3f}"
                  + ("" if box else "  CANNOT REACH THE DISPLAY VIEW: make it again"))
            if not box:
                flagged.append(edge.id)
            save_manifest(manifest)
    print(f"Done. {'Make again: ' + ', '.join(flagged) if flagged else 'Every clip lines up with the display view.'} "
          "Check the seams with: uv run python -m video.check_seams")
    return 1 if flagged else 0


if __name__ == "__main__":
    sys.exit(main())
