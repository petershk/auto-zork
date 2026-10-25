"""Image and video generation behind small interfaces, so models can be swapped through config.

fal endpoint ids and parameters are taken from fal's model pages (checked 2026-10-24), not guessed:
  fal-ai/nano-banana-pro        text to image: prompt, aspect_ratio, resolution, seed, output_format
  fal-ai/nano-banana-pro/edit   edit: prompt, image_urls (one or more), aspect_ratio, resolution, seed
Both return {"images": [{"url": ...}]} and cost $0.15 per 1K/2K image.
  fal-ai/birefnet/v2            background removal: image_url, model, output_format -> {"image": {"url": ...}};
                                billed by compute time (no per-image price is published), counted here as $0.01
FAL_KEY is read from the environment.
"""
from __future__ import annotations

import tempfile
from pathlib import Path
from typing import Protocol

import httpx

IMAGE_MODELS = {  # IMAGE_MODEL -> (text-to-image endpoint, edit endpoint, USD per image)
    "nano-banana-pro": ("fal-ai/nano-banana-pro", "fal-ai/nano-banana-pro/edit", 0.15),
}
BACKGROUND_REMOVAL = ("fal-ai/birefnet/v2", 0.01)


def has_alpha(png: bytes) -> bool:
    """True if a PNG can hold transparency (RGBA, grey+alpha, or a palette with a tRNS chunk)."""
    if png[:8] != b"\x89PNG\r\n\x1a\n":
        return False
    colour_type = png[25]
    return colour_type in (4, 6) or (colour_type == 3 and b"tRNS" in png[:4096])


class ImageProvider(Protocol):
    price_per_image: float

    def generate(self, prompt: str, *, aspect: str = "16:9", seed: int | None = None,
                 reference_images: list[Path] = ()) -> bytes: ...

    def edit(self, base: Path, instruction: str, *, seed: int | None = None) -> bytes: ...

    def remove_background(self, image: Path) -> bytes: ...


class VideoProvider(Protocol):
    def first_last_frame(self, *, first: Path, last: Path, prompt: str, duration_s: int, resolution: str = "720p",
                         audio: bool, seed: int | None = None) -> bytes: ...


class FalImageProvider:
    def __init__(self, model: str, client=None):
        try:
            self.generate_endpoint, self.edit_endpoint, self.price_per_image = IMAGE_MODELS[model]
        except KeyError:
            raise ValueError(f"Unknown IMAGE_MODEL {model!r}. Choose one of: {', '.join(IMAGE_MODELS)}.") from None
        if client is None:
            import fal_client as client
        self.client = client

    def generate(self, prompt, *, aspect="16:9", seed=None, reference_images=()):
        arguments = {"prompt": prompt, "aspect_ratio": aspect, "resolution": "2K", "output_format": "png"}
        if seed is not None:
            arguments["seed"] = seed
        if reference_images:
            arguments["image_urls"] = [self.client.upload_file(path) for path in reference_images]
            return self._run(self.edit_endpoint, arguments)
        return self._run(self.generate_endpoint, arguments)

    def edit(self, base, instruction, *, seed=None):
        arguments = {"prompt": instruction, "image_urls": [self.client.upload_file(base)], "aspect_ratio": "16:9",
                     "resolution": "2K", "output_format": "png"}
        if seed is not None:
            arguments["seed"] = seed
        return self._run(self.edit_endpoint, arguments)

    def remove_background(self, image):
        endpoint, _ = BACKGROUND_REMOVAL
        result = self.client.subscribe(endpoint, arguments={"image_url": self.client.upload_file(image),
                                                            "model": "General Use (Heavy)", "output_format": "png"})
        data = self._download(result["image"]["url"])
        if not has_alpha(data):
            raise RuntimeError("background removal returned an image without transparency")
        return data

    def _run(self, endpoint, arguments):
        result = self.client.subscribe(endpoint, arguments=arguments)
        return self._download(result["images"][0]["url"])

    @staticmethod
    def _download(url):
        response = httpx.get(url, timeout=120)
        response.raise_for_status()
        return response.content


# VIDEO_MODEL -> (endpoint, start image parameter, end image parameter, allowed durations in seconds), from fal's model
# pages (checked 2026-10-24). Seedance: prompt, image_url, end_image_url, duration "4".."15", resolution, aspect_ratio,
# generate_audio -> {"video": {"url": ...}, "seed": ...}. Kling and LTX are not looked up yet, so they refuse to run.
VIDEO_MODELS = {
    "seedance-2.0": ("bytedance/seedance-2.0/image-to-video", "image_url", "end_image_url", tuple(range(4, 16))),
}


FRAME_SIZES = {"480p": (854, 480), "720p": (1280, 720), "1080p": (1920, 1080)}


def fit_frame(source: Path, target: Path, resolution: str = "720p") -> Path:
    """A copy of a keyframe cropped to exactly 16:9 around its centre and scaled to the video's frame size."""
    from PIL import Image

    width, height = FRAME_SIZES[resolution]
    image = Image.open(source).convert("RGB")
    w, h = image.size
    if w * height > h * width:  # too wide: trim the sides
        keep = round(h * width / height)
        image = image.crop(((w - keep) // 2, 0, (w - keep) // 2 + keep, h))
    else:
        keep = round(w * height / width)
        image = image.crop((0, (h - keep) // 2, w, (h - keep) // 2 + keep))
    image.resize((width, height), Image.Resampling.LANCZOS).save(target, "PNG")
    return target


class FalVideoProvider:
    def __init__(self, model: str, client=None):
        if model not in VIDEO_MODELS:
            raise ValueError(f"VIDEO_MODEL {model!r} is not set up yet; look up its fal endpoint and parameters first. "
                             f"Ready: {', '.join(VIDEO_MODELS)}.")
        self.endpoint, self.start_key, self.end_key, self.durations = VIDEO_MODELS[model]
        if client is None:
            import fal_client as client
        self.client = client

    def duration(self, seconds: int) -> int:
        """The nearest length this model accepts."""
        return min(self.durations, key=lambda d: (abs(d - seconds), d))

    def first_last_frame(self, *, first, last, prompt, duration_s, resolution="720p", audio, seed=None):
        # Frames are sent at exactly the clip's own size: given 2752x1536 keyframes (a little wider than 16:9) Seedance
        # reframed every clip, zoomed in 3-9%, so a clip no longer matched the still it starts and ends on.
        with tempfile.TemporaryDirectory() as folder:
            start = self.client.upload_file(fit_frame(first, Path(folder) / "first.png", resolution))
            end = start if last == first else self.client.upload_file(fit_frame(last, Path(folder) / "last.png", resolution))
        arguments = {"prompt": prompt, self.start_key: start, self.end_key: end, "resolution": resolution,
                     "duration": str(self.duration(duration_s)), "aspect_ratio": "16:9", "generate_audio": audio}
        result = self.client.subscribe(self.endpoint, arguments=arguments)
        response = httpx.get(result["video"]["url"], timeout=300)
        response.raise_for_status()
        return response.content, result.get("seed")


class ModelArkVideoProvider:
    """BytePlus ModelArk (Seedance direct). Not implemented yet."""

    def first_last_frame(self, **kwargs):
        raise NotImplementedError("The ModelArk video provider is a stub.")
