"""Video settings from maze/.env (see .env.example). Secrets stay in .env and are never committed."""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parent.parent / ".env")

# Appended to every image and motion prompt so the whole game keeps one look.
STYLE = "painterly realism, 1980s fantasy book illustration, muted colors, no people, no text, no signature"
# Appended to every picture made from text. Pictures are never drawn from a reference image (the model copies its
# buildings and objects into the new place), so this spells the look out in full instead.
SCENE_STYLE = ("digital oil painting in the manner of a 1980s fantasy book illustration: painterly realism, soft visible "
               "brushwork, finely detailed textures of wood, stone, moss and earth, desaturated earthy palette, gentle "
               "atmospheric haze, natural light from sources in the scene, wide eye-level view, no people, no text, "
               "no signature")
# Added to every scene (keyframe) prompt: the camera is the player's eyes, and the player is never in the picture.
VIEWER = "The player's own hands, arms and body never appear in the picture."
# The player shows every still cropped to this centred zoom, and every clip is cropped to match (see normalize.py):
# video models reframe by a few percent, so the stills give up a margin to make clips and stills line up exactly.
DISPLAY_ZOOM = 1.12
# Where the game loads pictures and clips that are not on this machine (video/assets is not in git): the public
# auto-zork-media bucket on Cloudflare R2. Set VIDEO_ASSET_URL to serve them from elsewhere.
ASSET_URL = os.environ.get("VIDEO_ASSET_URL", "https://pub-0bd051fddf4c41f19f7ecf337545922b.r2.dev").rstrip("/")

# USD per second of 720p output (Kling: without audio, with audio), from fal.ai's model pages, checked 2026-10-24.
# LTX-2.3 has no 720p tier on fal; its lowest is 1080p. Override with VIDEO_PRICE_PER_SECOND.
VIDEO_PRICES = {
    "seedance-2.0": (0.3034, 0.3034),
    "kling-3.0": (0.112, 0.168),
    "ltx-2.3": (0.06, 0.06),
}


@dataclass(frozen=True)
class VideoConfig:
    video_model: str
    image_model: str
    llm_provider: str
    llm_model: str
    rooms: set[str] | None                # engine room ids or area names to film; None films every room
    max_spend_usd: float
    clip_concurrency: int
    speculation_concurrency: int
    seam_threshold: float
    price_override: float | None

    @property
    def provider(self) -> str:
        return f"fal:{self.video_model}"

    def price_per_second(self, audio: bool) -> float:
        if self.price_override is not None:
            return self.price_override
        try:
            silent, with_audio = VIDEO_PRICES[self.video_model]
        except KeyError:
            raise ValueError(f"No price known for VIDEO_MODEL={self.video_model!r}; set VIDEO_PRICE_PER_SECOND.") from None
        return with_audio if audio else silent


def load_config() -> VideoConfig:
    override = os.environ.get("VIDEO_PRICE_PER_SECOND", "").strip()
    rooms = {r.strip().upper() for r in os.environ.get("VIDEO_ROOMS", "").split(",") if r.strip()}
    return VideoConfig(
        video_model=os.environ.get("VIDEO_MODEL", "seedance-2.0"),
        image_model=os.environ.get("IMAGE_MODEL", "nano-banana-pro"),
        llm_provider=os.environ.get("LLM_PROVIDER", "anthropic"),
        llm_model=os.environ.get("LLM_MODEL", "claude-haiku-5-5"),
        rooms=rooms or None,
        max_spend_usd=float(os.environ.get("MAX_SPEND_USD", "100")),
        clip_concurrency=int(os.environ.get("CLIP_CONCURRENCY", "3")),
        speculation_concurrency=int(os.environ.get("SPECULATION_CONCURRENCY", "2")),
        seam_threshold=float(os.environ.get("SEAM_THRESHOLD", "0.85")),
        price_override=float(override) if override else None,
    )
