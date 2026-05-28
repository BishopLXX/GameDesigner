from __future__ import annotations

import re


AI_IMAGE_PIXEL_OUTPUT_SIZE_PRESETS = [
    "auto",
    "128x128",
    "192x256",
    "256x256",
    "256x320",
    "256x384",
    "384x256",
    "512x512",
]

PIXEL_ART_SAMPLE_BLOCK = 4
PIXEL_ART_ALPHA_THRESHOLD = 128
PIXEL_ART_CELL_ALPHA_COVERAGE_THRESHOLD = 0.18
PIXEL_ART_MAX_COLORS = 192
PIXEL_ART_MIN_PALETTE_COLORS = 48

_PIXEL_OUTPUT_SIZE_RE = re.compile(r"^(\d+)x(\d+)$")
_AI_IMAGE_SIZE_RE = re.compile(r"^(\d+)x(\d+)$")


AI_IMAGE_SIZE_PRESETS = [
    "auto",
    "816x816",
    "768x864",
    "864x768",
    "896x736",
    "736x896",
    "1024x1024",
    "1536x1024",
    "1024x1536",
    "1792x1024",
    "1024x1792",
    "512x512",
    "256x256",
]

GPT_IMAGE_LEGACY_SIZES = {"1024x1024", "1536x1024", "1024x1536", "auto"}
DALL_E_2_IMAGE_SIZES = {"256x256", "512x512", "1024x1024", "auto"}
DALL_E_3_IMAGE_SIZES = {"1024x1024", "1792x1024", "1024x1792", "auto"}
GPT_IMAGE_2_MIN_PIXELS = 655_360
GPT_IMAGE_2_MAX_PIXELS = 8_294_400
GPT_IMAGE_2_MAX_EDGE = 3_840
GPT_IMAGE_2_MAX_ASPECT_RATIO = 3.0


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


def normalized_ai_image_size(value: str | None) -> str:
    text = str(value or "").strip().lower()
    if not text or text == "auto":
        return "auto"
    match = _AI_IMAGE_SIZE_RE.fullmatch(text)
    if match is None:
        return "auto"
    width = max(1, int(match.group(1)))
    height = max(1, int(match.group(2)))
    return f"{width}x{height}"


def ai_image_size_dimensions(value: str | None) -> tuple[int, int] | None:
    normalized = normalized_ai_image_size(value)
    if normalized == "auto":
        return None
    width_str, height_str = normalized.split("x", 1)
    return int(width_str), int(height_str)


def api_ai_image_size(value: str | None, *, model: str = "", provider: str = "openai") -> str:
    normalized = normalized_ai_image_size(value)
    if normalized == "auto":
        return "auto"
    model_key = str(model or "").strip().lower()
    if provider == "compatible":
        return normalized
    if model_key == "dall-e-2":
        return normalized if normalized in DALL_E_2_IMAGE_SIZES else "auto"
    if model_key == "dall-e-3":
        return normalized if normalized in DALL_E_3_IMAGE_SIZES else "auto"
    if model_key == "gpt-image-2":
        return normalized if is_valid_gpt_image_2_size(normalized) else "auto"
    if model_key.startswith("gpt-image") or model_key == "chatgpt-image-latest":
        return normalized if normalized in GPT_IMAGE_LEGACY_SIZES else "auto"
    return normalized


def is_valid_gpt_image_2_size(value: str | None) -> bool:
    dimensions = ai_image_size_dimensions(value)
    if dimensions is None:
        return True
    width, height = dimensions
    if width <= 0 or height <= 0:
        return False
    if width % 16 != 0 or height % 16 != 0:
        return False
    if max(width, height) > GPT_IMAGE_2_MAX_EDGE:
        return False
    if min(width, height) <= 0 or max(width, height) / min(width, height) > GPT_IMAGE_2_MAX_ASPECT_RATIO:
        return False
    pixels = width * height
    return GPT_IMAGE_2_MIN_PIXELS <= pixels <= GPT_IMAGE_2_MAX_PIXELS


def pixel_art_palette_limit(width: int, height: int) -> int:
    pixels = max(1, int(width) * int(height))
    if pixels <= 32 * 32:
        return PIXEL_ART_MIN_PALETTE_COLORS
    if pixels <= 64 * 64:
        return 96
    if pixels <= 128 * 128:
        return 128
    if pixels <= 256 * 256:
        return 160
    return PIXEL_ART_MAX_COLORS
