"""Check that every clip really starts and ends on its keyframes, so clips join without a visible cut.

ffmpeg pulls out each clip's first and last decoded frames; each is compared with the keyframe it should match
(SSIM on small greyscale copies, 0..1) and the scores go into manifest.json. Clips under SEAM_THRESHOLD are listed
for review. Clips already scored are skipped unless --all. Run from the maze folder:
  uv run python -m video.check_seams
"""
from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

from video.config import DISPLAY_ZOOM, load_config
from video.gen_keyframes import VIDEO_DIR
from video.graph import load_manifest, save_manifest

SIZE = (96, 54)       # small enough to ignore compression noise, big enough to see a wrong framing
WINDOW = 6


def _grey(image):
    from PIL import Image, ImageFilter

    image = image.convert("L").resize(SIZE, Image.Resampling.BILINEAR).filter(ImageFilter.GaussianBlur(0.6))
    return list(image.get_flattened_data())


def view(image, zoom, dx=0.0, dy=0.0):
    """The part of an image a camera zoomed in by `zoom` and shifted by (dx, dy) of the size would show."""
    w, h = image.size
    cw, ch = w / zoom, h / zoom
    cx, cy = w * (0.5 + dx), h * (0.5 + dy)
    return image.crop((round(cx - cw / 2), round(cy - ch / 2), round(cx + cw / 2), round(cy + ch / 2)))


def display_view(path):
    """A keyframe as the player shows it: cropped to DISPLAY_ZOOM around its centre."""
    from PIL import Image

    return view(Image.open(path), DISPLAY_ZOOM)


def ssim(a, b) -> float:
    """Mean SSIM over WINDOW x WINDOW blocks of two greyscale images of SIZE."""
    width, height = SIZE
    c1, c2 = (0.01 * 255) ** 2, (0.03 * 255) ** 2
    scores = []
    for top in range(0, height - WINDOW + 1, WINDOW):
        for left in range(0, width - WINDOW + 1, WINDOW):
            xs = [a[(top + y) * width + left + x] for y in range(WINDOW) for x in range(WINDOW)]
            ys = [b[(top + y) * width + left + x] for y in range(WINDOW) for x in range(WINDOW)]
            n = len(xs)
            mx, my = sum(xs) / n, sum(ys) / n
            vx = sum((v - mx) ** 2 for v in xs) / n
            vy = sum((v - my) ** 2 for v in ys) / n
            cov = sum((p - mx) * (q - my) for p, q in zip(xs, ys)) / n
            scores.append(((2 * mx * my + c1) * (2 * cov + c2)) / ((mx * mx + my * my + c1) * (vx + vy + c2)))
    return sum(scores) / len(scores)


def similarity(image_a: Path, image_b: Path) -> float:
    from PIL import Image

    return ssim(_grey(Image.open(image_a)), _grey(Image.open(image_b)))


def seam_score(frame: Path, keyframe: Path) -> float:
    """How closely a clip's frame matches its keyframe as the player shows it."""
    from PIL import Image

    return ssim(_grey(Image.open(frame)), _grey(display_view(keyframe)))


def end_frames(video: Path, folder: Path) -> tuple[Path, Path]:
    """The first and the last decoded frames of a video, as PNG files."""
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        raise SystemExit("ffmpeg is not on PATH; install it to check seams.")
    first, last = folder / "first.png", folder / "last.png"
    run = lambda *args: subprocess.run([ffmpeg, "-v", "error", "-y", *args], check=True)  # noqa: E731
    run("-i", str(video), "-frames:v", "1", str(first))
    run("-sseof", "-1", "-i", str(video), "-update", "1", str(last))  # keeps overwriting: the last frame remains
    return first, last


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--all", action="store_true", help="score clips that already have scores again")
    args = parser.parse_args(argv)
    threshold = load_config().seam_threshold
    manifest = load_manifest()
    frames = {k.id: k for k in manifest.keyframes}
    checked, flagged = 0, []
    for edge in manifest.actions:
        for clip in edge.clips:
            if not clip.path or (clip.first_frame_score is not None and not args.all):
                continue
            with tempfile.TemporaryDirectory() as folder:
                first, last = end_frames(VIDEO_DIR / clip.path, Path(folder))
                clip.first_frame_score = round(seam_score(first, VIDEO_DIR / frames[clip.from_].image_path), 3)
                clip.last_frame_score = round(seam_score(last, VIDEO_DIR / frames[clip.to].image_path), 3)
            checked += 1
            low = min(clip.first_frame_score, clip.last_frame_score) < threshold
            print(f"  {edge.id:<52} start {clip.first_frame_score:.3f}  end {clip.last_frame_score:.3f}"
                  + ("  BELOW THRESHOLD" if low else ""))
            if low:
                flagged.append(edge.id)
    save_manifest(manifest)
    print(f"Checked {checked} clips against SEAM_THRESHOLD={threshold}: "
          + (f"{len(flagged)} to review: {', '.join(flagged)}" if flagged else "all seams pass."))
    return 1 if flagged else 0


if __name__ == "__main__":
    sys.exit(main())
