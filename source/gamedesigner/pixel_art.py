from __future__ import annotations

import re


AI_IMAGE_PIXEL_OUTPUT_SIZE_PRESETS = [
    "auto",
    "128x128",
    "256x256",
    "256x384",
    "384x256",
    "512x512",
]

PIXEL_ART_SAMPLE_BLOCK = 4
PIXEL_ART_ALPHA_THRESHOLD = 128

_PIXEL_OUTPUT_SIZE_RE = re.compile(r"^(\d+)x(\d+)$")


def normalized_pixel_output_size(value: str | None) -> str:
    text = str(value or "").strip().lower()
    if not text or text == "auto":
        return "auto"
    match = _PIXEL_OUTPUT_SIZE_RE.fullmatch(text)
    if match is None:
        return "auto"
    width = max(1, int(match.group(1)))
    height = max(1, int(match.group(2)))
    return f"{width}x{height}"


def pixel_output_size_dimensions(value: str | None) -> tuple[int, int] | None:
    normalized = normalized_pixel_output_size(value)
    if normalized == "auto":
        return None
    width_str, height_str = normalized.split("x", 1)
    return int(width_str), int(height_str)
