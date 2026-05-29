from __future__ import annotations

import base64
from io import BytesIO
import json
import math
import mimetypes
import os
import shutil
import subprocess
import tempfile
import urllib.error
import urllib.parse
import urllib.request
import uuid
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

from PySide6.QtGui import QImage
from PIL import Image, ImageFilter, ImageOps
from PIL.PngImagePlugin import PngInfo

from .ai_presets import normalize_ai_credentials
from .pixel_art import (
    AI_IMAGE_SIZE_PRESETS,
    PIXEL_ART_ALPHA_THRESHOLD,
    PIXEL_ART_CELL_ALPHA_COVERAGE_THRESHOLD,
    PIXEL_ART_SAMPLE_BLOCK,
    api_ai_image_size,
    normalized_ai_image_size,
    normalized_pixel_output_size,
    pixel_art_palette_limit,
    pixel_output_size_dimensions,
)
from .pixel_refiner import (
    DEFAULT_PIXEL_REFINER_CANDIDATES,
    DEFAULT_PIXEL_REFINER_MODEL_ID,
    DEFAULT_PIXEL_REFINER_STRENGTH,
    PixelRefinerError,
    PixelRefinerRequest,
    normalize_pixel_refiner_service_url,
    refine_pixel_art_with_service,
)
from .storage import AppSettings, project_bundle_dir


AI_IMAGE_DIR = "ai_images"
AI_IMAGE_REFERENCES_DIR = "references"
AI_IMAGE_CACHE_DIR = "cache"
AI_IMAGE_MODEL_PRESETS = [
    "gpt-image-1.5",
    "chatgpt-image-latest",
    "gpt-image-1",
    "gpt-image-1-mini",
    "gpt-image-2",
    "dall-e-2",
    "dall-e-3",
]
AI_IMAGE_QUALITY_PRESETS = ["auto", "low", "medium", "high"]
AI_IMAGE_BACKGROUND_PRESETS = ["auto", "transparent", "opaque"]
AI_IMAGE_OUTPUT_FORMAT_PRESETS = ["png", "webp", "jpeg"]
AI_IMAGE_PROVIDERS = {"openai", "compatible"}
PIXEL_ART_OUTLINE_LUMA_MAX = 72
PIXEL_ART_OUTLINE_CELL_COVERAGE_THRESHOLD = 0.18
PIXEL_ART_ACCENT_CELL_COVERAGE_THRESHOLD = 0.18
PIXEL_ART_ACCENT_SATURATION_THRESHOLD = 58
PIXEL_ART_ACCENT_CHANNEL_THRESHOLD = 172
PIXEL_ART_SINGLE_PIXEL_FEATURE_COVERAGE_THRESHOLD = 0.10
PIXEL_DOWNSCALE_CANDIDATE_LIMIT = 5
PIXEL_DOWNSCALE_CANDIDATE_METHODS = (
    "grid",
    "box",
    "lanczos",
    "nearest",
    "palette_box",
)
ASEPRITE_WINDOWS_CANDIDATE_PATHS = (
    r"C:\Program Files\Aseprite\Aseprite.exe",
    r"C:\Program Files (x86)\Aseprite\Aseprite.exe",
    r"C:\Program Files (x86)\Steam\steamapps\common\Aseprite\Aseprite.exe",
)
PIXEL_ART_METADATA_KEY = "GameDesignerPixelArt"
PIXEL_SOURCE_METADATA_KEY = "GameDesignerPixelSource"
PIXEL_SOURCE_DIR = "sources"


class AiImageError(RuntimeError):
    pass


@dataclass(frozen=True)
class AiImageReference:
    path: Path
    width: int
    height: int


@dataclass(frozen=True)
class AiImageRequest:
    api_key: str
    base_url: str
    provider: str
    model: str
    prompt: str
    reference_paths: list[Path]
    size: str = "auto"
    quality: str = "auto"
    background: str = "auto"
    count: int = 1
    output_format: str = "png"
    timeout: int = 180


@dataclass(frozen=True)
class AiGeneratedImage:
    data: bytes
    output_format: str
    revised_prompt: str = ""


@dataclass(frozen=True)
class CachedAiImage:
    path: Path
    width: int
    height: int
    revised_prompt: str = ""


@dataclass(frozen=True)
class PixelDownscaleCandidate:
    image: CachedAiImage
    method: str
    label: str
    used_aseprite: bool = False


def normalized_ai_image_provider(value: str) -> str:
    return value if value in AI_IMAGE_PROVIDERS else "openai"


def build_ai_image_request(
    settings: AppSettings,
    prompt: str,
    reference_paths: list[str | Path] | None = None,
) -> AiImageRequest:
    provider = normalized_ai_image_provider(getattr(settings, "ai_image_provider", "openai"))
    configured_key, configured_base_url = normalize_ai_credentials(
        str(getattr(settings, "ai_image_api_key", "") or ""),
        str(getattr(settings, "ai_image_base_url", "") or ""),
    )
    base_url = configured_base_url
    if provider == "openai" or not base_url:
        base_url = "https://api.openai.com/v1"
    else:
        base_url = _normalized_compatible_base_url(base_url)
    api_key = configured_key or os.getenv("OPENAI_API_KEY", "").strip()
    if not api_key:
        raise AiImageError("请先在生图设置里填写 OpenAI 或兼容服务的 API Key。")
    requested_background = _coerce_choice(
        str(getattr(settings, "ai_image_background", "auto") or "auto"),
        AI_IMAGE_BACKGROUND_PRESETS,
    )
    output_format = _coerce_choice(
        str(getattr(settings, "ai_image_output_format", "png") or "png"),
        AI_IMAGE_OUTPUT_FORMAT_PRESETS,
    )
    model = str(getattr(settings, "ai_image_model", "") or "").strip() or AI_IMAGE_MODEL_PRESETS[0]
    requested_size = normalized_ai_image_size(getattr(settings, "ai_image_size", "auto"))
    request_size = api_ai_image_size(requested_size, model=model, provider=provider)
    return AiImageRequest(
        api_key=api_key,
        base_url=base_url.rstrip("/"),
        provider=provider,
        model=model,
        prompt=prompt.strip(),
        reference_paths=[Path(path) for path in (reference_paths or [])],
        size=request_size,
        quality=_coerce_choice(str(getattr(settings, "ai_image_quality", "auto") or "auto"), AI_IMAGE_QUALITY_PRESETS),
        background=requested_background,
        count=_coerce_count(getattr(settings, "ai_image_count", 1)),
        output_format=output_format,
        timeout=180,
    )


def generate_ai_images(request: AiImageRequest) -> list[AiGeneratedImage]:
    if not request.prompt:
        raise AiImageError("请输入生图描述。")
    if request.reference_paths:
        body, content_type = _multipart_body(request)
        raw = _post(
            f"{request.base_url}/images/edits",
            request.api_key,
            body,
            content_type,
            timeout=request.timeout,
        )
    else:
        payload = _json_payload(request)
        raw = _post(
            f"{request.base_url}/images/generations",
            request.api_key,
            json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            "application/json",
            timeout=request.timeout,
        )
    return _parse_images_response(raw, request.output_format)


def save_ai_image_reference(project_path: str | Path, source_path: str | Path) -> AiImageReference:
    source = Path(source_path)
    image = QImage(str(source))
    if image.isNull():
        raise AiImageError(f"无法读取参考图：{source}")
    folder = project_bundle_dir(project_path) / AI_IMAGE_DIR / AI_IMAGE_REFERENCES_DIR
    folder.mkdir(parents=True, exist_ok=True)
    suffix = source.suffix.lower() if source.suffix.lower() in {".png", ".jpg", ".jpeg", ".webp"} else ".png"
    target = folder / f"ref_{_timestamp()}_{uuid.uuid4().hex[:8]}{suffix}"
    if source.resolve() == target.resolve():
        copied = source
    else:
        shutil.copyfile(source, target)
        copied = target
    return AiImageReference(path=copied, width=image.width(), height=image.height())


def save_ai_image_reference_from_qimage(project_path: str | Path, image: QImage) -> AiImageReference:
    if image.isNull():
        raise AiImageError("剪贴板图片为空。")
    folder = project_bundle_dir(project_path) / AI_IMAGE_DIR / AI_IMAGE_REFERENCES_DIR
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"ref_clipboard_{_timestamp()}_{uuid.uuid4().hex[:8]}.png"
    if not image.save(str(path), "PNG"):
        raise AiImageError(f"无法保存参考图：{path}")
    return AiImageReference(path=path, width=image.width(), height=image.height())


def ai_image_cache_dir(project_path: str | Path, cache_key: str = "") -> Path:
    folder = project_bundle_dir(project_path) / AI_IMAGE_DIR / AI_IMAGE_CACHE_DIR
    normalized_key = _safe_cache_key(cache_key)
    if normalized_key:
        return folder / normalized_key
    return folder


def cache_generated_ai_image(
    project_path: str | Path,
    image: AiGeneratedImage,
    *,
    index: int = 1,
    cache_key: str = "",
    pixel_mode: bool = False,
    pixel_output_size: str = "auto",
) -> CachedAiImage:
    folder = _ai_image_cache_folder(project_path, cache_key, pixel_mode=pixel_mode, pixel_output_size=pixel_output_size)
    folder.mkdir(parents=True, exist_ok=True)
    extension = "png" if pixel_mode else _safe_output_extension(image.output_format)
    path = folder / f"cache_{_timestamp()}_{index}_{uuid.uuid4().hex[:8]}.{extension}"
    data = _pixel_art_image_bytes(image.data, pixel_output_size=pixel_output_size) if pixel_mode else image.data
    path.write_bytes(data)
    if pixel_mode:
        _save_pixel_source_image(image.data, path, folder)
    return cached_ai_image_from_path(path, revised_prompt=image.revised_prompt)


def generate_pixel_downscale_candidates(
    project_path: str | Path,
    source_path: str | Path,
    *,
    cache_key: str = "",
    pixel_output_size: str = "auto",
    include_aseprite: bool = True,
    aseprite_cli_path: str | Path | None = None,
) -> list[PixelDownscaleCandidate]:
    target_size = pixel_output_size_dimensions(pixel_output_size)
    if target_size is None:
        raise AiImageError("请先设置明确的像素输出尺寸，例如 128x128、256x256 或 256x384。")
    source = pixel_source_path_for_candidate(source_path)
    if not source.exists():
        raise AiImageError(f"源图不存在：{source}")
    folder = _ai_image_cache_folder(project_path, cache_key, pixel_mode=True, pixel_output_size=pixel_output_size)
    folder.mkdir(parents=True, exist_ok=True)
    candidates: list[PixelDownscaleCandidate] = []
    with Image.open(source) as loaded:
        image = ImageOps.exif_transpose(loaded).convert("RGBA")
        for method in PIXEL_DOWNSCALE_CANDIDATE_METHODS:
            candidate = _pixel_downscale_candidate_image(image, method, target_size)
            if candidate is None:
                continue
            candidates.append(
                _save_pixel_downscale_candidate(
                    folder,
                    candidate,
                    method=method,
                    label=_pixel_downscale_candidate_label(method),
                )
            )
    if include_aseprite:
        aseprite_candidate = _generate_aseprite_downscale_candidate(
            source,
            folder,
            target_size,
            aseprite_cli_path=aseprite_cli_path,
        )
        if aseprite_candidate is not None:
            candidates.append(aseprite_candidate)
    return candidates[:PIXEL_DOWNSCALE_CANDIDATE_LIMIT + 1]


def generate_pixel_refiner_candidates(
    project_path: str | Path,
    source_path: str | Path,
    *,
    cache_key: str = "",
    pixel_output_size: str = "auto",
    service_url: str = "",
    model_dir: str | Path | None = None,
    model_id: str = DEFAULT_PIXEL_REFINER_MODEL_ID,
    strength: float = DEFAULT_PIXEL_REFINER_STRENGTH,
    candidates: int = DEFAULT_PIXEL_REFINER_CANDIDATES,
) -> list[PixelDownscaleCandidate]:
    target_size = pixel_output_size_dimensions(pixel_output_size)
    if target_size is None:
        raise AiImageError("请先设置明确的像素输出尺寸，例如 128x128、256x256 或 256x384。")
    source = Path(source_path)
    if not source.exists():
        raise AiImageError(f"源图不存在：{source}")
    folder = _ai_image_cache_folder(project_path, cache_key, pixel_mode=True, pixel_output_size=pixel_output_size)
    folder.mkdir(parents=True, exist_ok=True)
    request = PixelRefinerRequest(
        input_path=source,
        output_dir=folder,
        target_size=pixel_output_size,
        palette_limit=pixel_art_palette_limit(*target_size),
        strength=strength,
        return_candidates=candidates,
        model_dir=Path(model_dir) if model_dir else None,
        model_id=model_id,
    )
    try:
        result = refine_pixel_art_with_service(request, service_url=normalize_pixel_refiner_service_url(service_url))
    except PixelRefinerError as exc:
        raise AiImageError(str(exc)) from exc
    refined: list[PixelDownscaleCandidate] = []
    for index, output in enumerate(result.outputs, start=1):
        if output.path.suffix.lower() != ".png":
            raise AiImageError(f"像素修正服务返回了非 PNG 文件：{output.path}")
        _finalize_refined_pixel_output(output.path, target_size)
        image = cached_ai_image_from_path(output.path)
        if image.width <= 0 or image.height <= 0:
            raise AiImageError(f"像素修正服务返回的 PNG 无法读取：{output.path}")
        refined.append(
            PixelDownscaleCandidate(
                image=image,
                method=f"pixel_refiner_{index}",
                label=output.label or f"AI 像素修正 {index}",
            )
        )
    return refined


def load_cached_ai_images(
    project_path: str | Path,
    *,
    limit: int = 80,
    cache_key: str = "",
    pixel_mode: bool = False,
    pixel_output_size: str = "auto",
) -> list[CachedAiImage]:
    folder = _ai_image_cache_folder(project_path, cache_key, pixel_mode=pixel_mode, pixel_output_size=pixel_output_size)
    if not folder.exists():
        return []
    paths = [
        path
        for path in folder.iterdir()
        if path.is_file() and path.suffix.lower() in {".png", ".jpg", ".jpeg", ".webp"}
    ]
    paths.sort(key=lambda path: path.stat().st_mtime)
    if limit > 0:
        paths = paths[-limit:]
    images: list[CachedAiImage] = []
    for path in paths:
        image = cached_ai_image_from_path(path)
        if image.width > 0 and image.height > 0:
            images.append(image)
    return images


def cached_ai_image_from_path(path: str | Path, *, revised_prompt: str = "") -> CachedAiImage:
    image_path = Path(path)
    loaded = QImage(str(image_path))
    if loaded.isNull():
        return CachedAiImage(path=image_path, width=0, height=0, revised_prompt=revised_prompt)
    return CachedAiImage(
        path=image_path,
        width=loaded.width(),
        height=loaded.height(),
        revised_prompt=revised_prompt,
    )


def _json_payload(request: AiImageRequest) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "model": request.model,
        "prompt": request.prompt,
        "n": request.count,
    }
    if request.model == "dall-e-3":
        payload["n"] = 1
    _add_optional_image_params(payload, request)
    return payload


def _multipart_body(request: AiImageRequest) -> tuple[bytes, str]:
    fields: list[tuple[str, str]] = [
        ("model", request.model),
        ("prompt", request.prompt),
        ("n", str(_request_count(request))),
    ]
    payload: dict[str, Any] = {}
    _add_optional_image_params(payload, request)
    fields.extend((key, str(value)) for key, value in payload.items() if key not in {"model", "prompt", "n"})
    image_field = "image" if len(request.reference_paths) == 1 else "image[]"
    files = [(image_field, path) for path in request.reference_paths]
    return _encode_multipart(fields, files)


def _add_optional_image_params(payload: dict[str, Any], request: AiImageRequest) -> None:
    payload["n"] = _request_count(request)
    if request.size != "auto":
        payload["size"] = request.size
    if request.quality != "auto":
        payload["quality"] = request.quality
    if _is_gpt_image_model(request.model):
        payload["output_format"] = request.output_format
        if request.reference_paths and request.model in {"gpt-image-1.5", "gpt-image-1", "gpt-image-1-mini"}:
            payload["input_fidelity"] = "high"
        if request.provider == "compatible":
            payload["response_format"] = "b64_json"
        if request.background != "auto":
            payload["background"] = request.background
    elif request.model == "dall-e-3":
        if request.quality in {"low", "medium", "high"}:
            payload["quality"] = "hd" if request.quality == "high" else "standard"


def _is_gpt_image_model(model: str) -> bool:
    return model.startswith("gpt-image") or model == "chatgpt-image-latest"


def _request_count(request: AiImageRequest) -> int:
    if request.model in {"dall-e-3", "gpt-image-2"}:
        return 1
    return request.count


def _normalized_compatible_base_url(base_url: str) -> str:
    cleaned = base_url.strip().rstrip("/")
    parsed = urllib.parse.urlparse(cleaned)
    if parsed.scheme and parsed.netloc and parsed.path in {"", "/"}:
        return urllib.parse.urlunparse(parsed._replace(path="/v1"))
    return cleaned


def _post(url: str, api_key: str, data: bytes, content_type: str, *, timeout: int = 180) -> bytes:
    request = urllib.request.Request(
        url,
        data=data,
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": content_type,
            "User-Agent": "GameDesigner/AI-Image",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=max(1, int(timeout))) as response:
            return response.read()
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise AiImageError(_format_api_error(exc.code, detail)) from exc
    except urllib.error.URLError as exc:
        raise AiImageError(f"生图服务连接失败：{exc.reason}") from exc


def _format_api_error(code: int, detail: str) -> str:
    try:
        raw = json.loads(detail)
    except json.JSONDecodeError:
        raw = {}
    message = ""
    if isinstance(raw, dict):
        error = raw.get("error")
        if isinstance(error, dict):
            message = str(error.get("message") or "")
        elif isinstance(error, str):
            message = error
    return f"生图 API 请求失败（HTTP {code}）：{message or detail[:500] or '没有错误详情'}"


def _parse_images_response(raw: bytes, output_format: str) -> list[AiGeneratedImage]:
    raw_format = _image_format_from_bytes(raw)
    if raw_format:
        return [AiGeneratedImage(data=raw, output_format=raw_format)]
    try:
        payload = json.loads(raw.decode("utf-8-sig"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise AiImageError(
            "生图服务返回了无法解析的数据。"
            f"返回摘要：{_response_preview(raw)}"
        ) from exc
    data = _response_image_items(payload)
    if not data:
        raise AiImageError(f"生图服务没有返回图片数据。返回摘要：{_response_preview(raw)}")
    images: list[AiGeneratedImage] = []
    for item in data:
        if not isinstance(item, dict):
            continue
        revised_prompt = str(item.get("revised_prompt") or item.get("revisedPrompt") or "")
        b64 = _first_string(item, ("b64_json", "base64", "image_base64", "image", "data"))
        if isinstance(b64, str) and b64:
            try:
                images.append(
                    AiGeneratedImage(
                        data=base64.b64decode(_strip_data_url(b64)),
                        output_format=output_format,
                        revised_prompt=revised_prompt,
                    )
                )
                continue
            except ValueError:
                pass
        url = _first_string(item, ("url", "image_url", "imageUrl"))
        if isinstance(url, str) and url:
            images.append(
                AiGeneratedImage(
                    data=_download_image_url(url),
                    output_format=_extension_from_url(url) or output_format,
                    revised_prompt=revised_prompt,
                )
            )
    if not images:
        raise AiImageError(f"生图服务返回了空图片结果。返回摘要：{_response_preview(raw)}")
    return images


def _response_image_items(payload: Any) -> list[dict[str, Any]]:
    if isinstance(payload, dict):
        data = payload.get("data")
        if isinstance(data, list):
            return [item for item in data if isinstance(item, dict)]
        images = payload.get("images")
        if isinstance(images, list):
            return [_image_item_from_any(item) for item in images]
        output = payload.get("output")
        if isinstance(output, list):
            return _image_items_from_output(output)
        if any(key in payload for key in ("b64_json", "base64", "url", "image_url")):
            return [payload]
    if isinstance(payload, list):
        return [_image_item_from_any(item) for item in payload]
    return []


def _image_items_from_output(output: list[Any]) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    for entry in output:
        if not isinstance(entry, dict):
            continue
        if any(key in entry for key in ("b64_json", "base64", "url", "image_url")):
            items.append(entry)
        content = entry.get("content")
        if isinstance(content, list):
            items.extend(_image_item_from_any(item) for item in content)
    return items


def _image_item_from_any(item: Any) -> dict[str, Any]:
    if isinstance(item, dict):
        return item
    if isinstance(item, str):
        if item.startswith("http://") or item.startswith("https://"):
            return {"url": item}
        return {"b64_json": item}
    return {}


def _first_string(item: dict[str, Any], keys: tuple[str, ...]) -> str:
    for key in keys:
        value = item.get(key)
        if isinstance(value, str) and value:
            return value
    return ""


def _image_format_from_bytes(raw: bytes) -> str:
    if raw.startswith(b"\x89PNG\r\n\x1a\n"):
        return "png"
    if raw.startswith(b"\xff\xd8\xff"):
        return "jpeg"
    if raw.startswith(b"RIFF") and raw[8:12] == b"WEBP":
        return "webp"
    return ""


def _response_preview(raw: bytes) -> str:
    text = raw[:500].decode("utf-8", errors="replace").strip()
    if not text:
        return "空响应"
    return " ".join(text.split())


def _download_image_url(url: str) -> bytes:
    request = urllib.request.Request(url, headers={"User-Agent": "GameDesigner/AI-Image"})
    with urllib.request.urlopen(request, timeout=180) as response:
        return response.read()


def _encode_multipart(fields: list[tuple[str, str]], files: list[tuple[str, Path]]) -> tuple[bytes, str]:
    boundary = f"----GameDesigner{uuid.uuid4().hex}"
    chunks: list[bytes] = []
    for name, value in fields:
        chunks.append(f"--{boundary}\r\n".encode("utf-8"))
        chunks.append(f'Content-Disposition: form-data; name="{name}"\r\n\r\n'.encode("utf-8"))
        chunks.append(str(value).encode("utf-8"))
        chunks.append(b"\r\n")
    for index, (name, path) in enumerate(files, start=1):
        if not path.exists():
            raise AiImageError(f"参考图不存在：{path}")
        mime = mimetypes.guess_type(str(path))[0] or "application/octet-stream"
        filename = _multipart_safe_filename(path, index)
        encoded_name = urllib.parse.quote(path.name, safe="")
        chunks.append(f"--{boundary}\r\n".encode("utf-8"))
        chunks.append(
            (
                f'Content-Disposition: form-data; name="{name}"; filename="{filename}"; '
                f"filename*=UTF-8''{encoded_name}\r\n"
                f"Content-Type: {mime}\r\n\r\n"
            ).encode("utf-8")
        )
        chunks.append(path.read_bytes())
        chunks.append(b"\r\n")
    chunks.append(f"--{boundary}--\r\n".encode("utf-8"))
    return b"".join(chunks), f"multipart/form-data; boundary={boundary}"


def _multipart_safe_filename(path: Path, index: int) -> str:
    suffix = path.suffix.lower()
    if suffix not in {".png", ".jpg", ".jpeg", ".webp"}:
        suffix = mimetypes.guess_extension(mimetypes.guess_type(str(path))[0] or "") or ".png"
    return f"reference_{max(1, index)}{suffix}"


def _coerce_choice(value: str, choices: list[str]) -> str:
    return value if value in choices else choices[0]


def _coerce_count(value: Any) -> int:
    try:
        return min(10, max(1, int(value)))
    except (TypeError, ValueError):
        return 1


def _strip_data_url(value: str) -> str:
    if "," in value and value.lower().startswith("data:"):
        return value.split(",", 1)[1]
    return value


def _safe_output_extension(value: str) -> str:
    value = value.lower().strip().lstrip(".")
    if value == "jpeg":
        return "jpg"
    return value if value in {"png", "webp", "jpg"} else "png"


def _ai_image_cache_folder(
    project_path: str | Path,
    cache_key: str,
    *,
    pixel_mode: bool = False,
    pixel_output_size: str = "auto",
) -> Path:
    folder = ai_image_cache_dir(project_path, cache_key)
    if not pixel_mode:
        return folder
    folder = folder / "pixel"
    size_key = normalized_pixel_output_size(pixel_output_size)
    if size_key != "auto":
        folder = folder / size_key
    return folder


def _pixel_art_image_bytes(raw: bytes, *, pixel_output_size: str = "auto") -> bytes:
    with Image.open(BytesIO(raw)) as source:
        source = ImageOps.exif_transpose(source).convert("RGBA")
        if pixel_output_size_dimensions(pixel_output_size) is None:
            sampled = source.copy()
        else:
            sampled = _pixel_grid_sample(source, pixel_output_size=pixel_output_size)
        alpha = sampled.getchannel("A").point(
            lambda value: 255 if value >= PIXEL_ART_ALPHA_THRESHOLD else 0,
            mode="L",
        )
        sampled.putalpha(alpha)
        return _image_to_png_bytes(sampled, pixel_art=True)


def _pixel_downscale_candidate_image(
    image: Image.Image,
    method: str,
    target_size: tuple[int, int],
) -> Image.Image | None:
    target_width, target_height = target_size
    if target_width <= 0 or target_height <= 0:
        return None
    if method == "grid":
        return _pixel_grid_sample(image, pixel_output_size=f"{target_width}x{target_height}")
    if method == "box":
        result = _resize_contain_to_canvas(image, target_size, Image.Resampling.BOX)
    elif method == "lanczos":
        result = _resize_contain_to_canvas(image, target_size, Image.Resampling.LANCZOS)
    elif method == "nearest":
        result = _resize_contain_to_canvas(image, target_size, Image.Resampling.NEAREST)
    elif method == "palette_box":
        softened = image.filter(ImageFilter.MedianFilter(size=3)) if min(image.size) >= 3 else image
        result = _resize_contain_to_canvas(softened, target_size, Image.Resampling.BOX)
        _apply_palette_quantization(result, max(16, pixel_art_palette_limit(target_width, target_height) // 2))
    else:
        return None
    _finalize_pixel_candidate(result)
    return result


def _resize_contain_to_canvas(
    image: Image.Image,
    target_size: tuple[int, int],
    resample: Image.Resampling,
) -> Image.Image:
    target_width, target_height = target_size
    source_width, source_height = image.size
    if source_width <= 0 or source_height <= 0:
        return Image.new("RGBA", target_size, (0, 0, 0, 0))
    scale = min(target_width / source_width, target_height / source_height)
    resized_width = max(1, min(target_width, int(round(source_width * scale))))
    resized_height = max(1, min(target_height, int(round(source_height * scale))))
    resized = image.resize((resized_width, resized_height), resample=resample)
    result = Image.new("RGBA", target_size, (0, 0, 0, 0))
    result.alpha_composite(resized, ((target_width - resized_width) // 2, (target_height - resized_height) // 2))
    return result


def _finalize_pixel_candidate(image: Image.Image) -> None:
    alpha = image.getchannel("A").point(
        lambda value: 255 if value >= PIXEL_ART_ALPHA_THRESHOLD else 0,
        mode="L",
    )
    image.putalpha(alpha)


def _finalize_refined_pixel_output(path: Path, target_size: tuple[int, int]) -> None:
    try:
        with Image.open(path) as loaded:
            image = ImageOps.exif_transpose(loaded).convert("RGBA")
    except OSError as exc:
        raise AiImageError(f"像素修正服务返回的 PNG 无法读取：{path}") from exc
    if image.size != target_size:
        image = _center_fit_pixel_image(image, target_size)
    _finalize_pixel_candidate(image)
    _apply_palette_quantization(image, pixel_art_palette_limit(*target_size))
    path.write_bytes(_image_to_png_bytes(image, pixel_art=True))


def _save_pixel_downscale_candidate(
    folder: Path,
    image: Image.Image,
    *,
    method: str,
    label: str,
    used_aseprite: bool = False,
) -> PixelDownscaleCandidate:
    safe_method = "".join(char if char.isalnum() or char in {"-", "_"} else "_" for char in method.strip()) or "candidate"
    safe_label = _sanitize_filename_piece(label) if label else ""
    name_parts = ["candidate", _timestamp(), safe_method]
    if safe_label and safe_label != safe_method:
        name_parts.append(safe_label)
    name_parts.append(uuid.uuid4().hex[:8])
    path = folder / ("_".join(name_parts) + ".png")
    path.write_bytes(_image_to_png_bytes(image, pixel_art=True))
    return PixelDownscaleCandidate(
        image=cached_ai_image_from_path(path),
        method=safe_method,
        label=label,
        used_aseprite=used_aseprite,
    )


def _save_pixel_source_image(raw: bytes, cached_path: Path, cache_folder: Path) -> None:
    try:
        with Image.open(BytesIO(raw)) as source:
            source = ImageOps.exif_transpose(source).convert("RGBA")
    except OSError:
        return
    source_folder = cache_folder / PIXEL_SOURCE_DIR
    source_folder.mkdir(parents=True, exist_ok=True)
    source_path = source_folder / f"{cached_path.stem}_source.png"
    try:
        buffer = BytesIO()
        info = PngInfo()
        info.add_text(PIXEL_SOURCE_METADATA_KEY, "1")
        source.save(buffer, format="PNG", optimize=True, pnginfo=info)
        source_path.write_bytes(buffer.getvalue())
    except OSError:
        return


def pixel_source_path_for_candidate(cached_path: str | Path) -> Path:
    path = Path(cached_path)
    source_path = path.parent / PIXEL_SOURCE_DIR / f"{path.stem}_source.png"
    if source_path.exists():
        return source_path
    return path


def _pixel_downscale_candidate_label(method: str) -> str:
    return {
        "grid": "卷积采样",
        "box": "BOX 缩小",
        "lanczos": "Lanczos 缩小",
        "nearest": "Nearest 缩小",
        "palette_box": "中值+调色板",
        "aseprite": "Aseprite 缩小",
    }.get(method, method)


def _generate_aseprite_downscale_candidate(
    source_path: Path,
    folder: Path,
    target_size: tuple[int, int],
    *,
    aseprite_cli_path: str | Path | None = None,
) -> PixelDownscaleCandidate | None:
    aseprite = find_aseprite_executable(aseprite_cli_path)
    if aseprite is None:
        return None
    target_width, target_height = target_size
    try:
        with Image.open(source_path) as source:
            source_width, source_height = source.size
    except OSError:
        return None
    if source_width <= 0 or source_height <= 0 or target_width <= 0 or target_height <= 0:
        return None
    scale = min(target_width / source_width, target_height / source_height)
    if scale <= 0:
        return None
    with tempfile.TemporaryDirectory(prefix="gamedesigner_aseprite_") as temp_folder:
        temp_output = Path(temp_folder) / "aseprite_candidate.png"
        command = [
            str(aseprite),
            "-b",
            str(source_path),
            "--scale",
            f"{scale:.8f}",
            "--save-as",
            str(temp_output),
        ]
        try:
            subprocess.run(
                command,
                check=True,
                capture_output=True,
                timeout=45,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
        except (OSError, subprocess.CalledProcessError, subprocess.TimeoutExpired):
            return None
        if not temp_output.exists():
            return None
        try:
            with Image.open(temp_output) as loaded:
                generated = ImageOps.exif_transpose(loaded).convert("RGBA")
        except OSError:
            return None
        if generated.size != target_size:
            generated = _center_fit_pixel_image(generated, target_size)
        _finalize_pixel_candidate(generated)
        return _save_pixel_downscale_candidate(
            folder,
            generated,
            method="aseprite",
            label=_pixel_downscale_candidate_label("aseprite"),
            used_aseprite=True,
        )


def find_aseprite_executable(override_path: str | Path | None = None) -> Path | None:
    configured = normalize_aseprite_cli_path(override_path)
    if configured:
        return Path(configured)
    found = shutil.which("aseprite") or shutil.which("aseprite.exe")
    if found:
        return Path(found)
    for raw_path in ASEPRITE_WINDOWS_CANDIDATE_PATHS:
        path = Path(raw_path)
        if path.exists():
            return path
    return None


def normalize_aseprite_cli_path(value: str | Path | None) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    path = Path(text)
    if not path.exists():
        return ""
    suffix = path.suffix.lower()
    if suffix not in {".exe", ".app", ""} and path.name.lower() not in {"aseprite", "aseprite.exe"}:
        return ""
    return str(path)


def _sanitize_filename_piece(value: str) -> str:
    cleaned = "".join(char if char.isalnum() or char in {"-", "_"} else "_" for char in str(value or "").strip())
    return cleaned.strip("_")[:40]


def _center_fit_pixel_image(image: Image.Image, target_size: tuple[int, int]) -> Image.Image:
    target_width, target_height = target_size
    fitted = image
    if fitted.width > target_width or fitted.height > target_height:
        left = max(0, (fitted.width - target_width) // 2)
        top = max(0, (fitted.height - target_height) // 2)
        fitted = fitted.crop((left, top, left + min(target_width, fitted.width), top + min(target_height, fitted.height)))
    result = Image.new("RGBA", target_size, (0, 0, 0, 0))
    result.alpha_composite(fitted, ((target_width - fitted.width) // 2, (target_height - fitted.height) // 2))
    return result


def _pixel_grid_sample(image: Image.Image, *, pixel_output_size: str = "auto") -> Image.Image:
    target_size = pixel_output_size_dimensions(pixel_output_size)
    if target_size is None:
        block = max(1, int(PIXEL_ART_SAMPLE_BLOCK))
        width = max(1, image.width // block)
        height = max(1, image.height // block)
        target_size = width, height
    target_width, target_height = target_size
    if target_width <= 0 or target_height <= 0:
        return image.copy()
    target_width = min(target_width, image.width)
    target_height = min(target_height, image.height)
    block = max(1, math.ceil(max(image.width / target_width, image.height / target_height)))
    sampled_width = max(1, min(target_width, math.ceil(image.width / block)))
    sampled_height = max(1, min(target_height, math.ceil(image.height / block)))
    result = Image.new("RGBA", (target_width, target_height), (0, 0, 0, 0))
    pixels = result.load()
    x_pad = max(0, (target_width - sampled_width) // 2)
    y_pad = max(0, (target_height - sampled_height) // 2)
    for y in range(sampled_height):
        top = min(image.height - 1, y * block)
        bottom = min(image.height, top + block)
        for x in range(sampled_width):
            left = min(image.width - 1, x * block)
            right = min(image.width, left + block)
            cell = image.crop((left, top, right, bottom))
            pixels[x + x_pad, y + y_pad] = _dominant_cell_color(
                cell,
                min_alpha_coverage=PIXEL_ART_CELL_ALPHA_COVERAGE_THRESHOLD,
            )
    _apply_palette_quantization(result, pixel_art_palette_limit(target_width, target_height))
    return result


def _dominant_cell_color(
    cell: Image.Image,
    *,
    min_alpha_coverage: float = 0.0,
) -> tuple[int, int, int, int]:
    rgba = cell.convert("RGBA")
    if hasattr(rgba, "get_flattened_data"):
        pixels = list(rgba.get_flattened_data())
    else:  # pragma: no cover - Pillow compatibility fallback.
        pixels = list(rgba.getdata())
    opaque = [pixel for pixel in pixels if pixel[3] >= PIXEL_ART_ALPHA_THRESHOLD]
    if not opaque:
        return 0, 0, 0, 0
    cell_area = max(1, len(pixels))
    opaque_coverage = len(opaque) / cell_area
    dark_pixels = _clustered_cell_feature_pixels(
        rgba,
        lambda pixel: _pixel_luminance(pixel) <= PIXEL_ART_OUTLINE_LUMA_MAX,
        min_coverage=PIXEL_ART_OUTLINE_CELL_COVERAGE_THRESHOLD,
    )
    if dark_pixels:
        return _median_cell_color(dark_pixels)
    if opaque_coverage < max(0.0, min(1.0, min_alpha_coverage)):
        return 0, 0, 0, 0
    accent_pixels = _clustered_cell_feature_pixels(
        rgba,
        _is_pixel_art_accent_pixel,
        min_coverage=PIXEL_ART_ACCENT_CELL_COVERAGE_THRESHOLD,
    )
    if accent_pixels:
        return _median_cell_color(accent_pixels)
    return _trimmed_average_cell_color(opaque)


def _pixel_luminance(pixel: tuple[int, int, int, int]) -> float:
    return 0.2126 * pixel[0] + 0.7152 * pixel[1] + 0.0722 * pixel[2]


def _is_pixel_art_accent_pixel(pixel: tuple[int, int, int, int]) -> bool:
    red, green, blue, alpha = pixel
    if alpha < PIXEL_ART_ALPHA_THRESHOLD:
        return False
    highest = max(red, green, blue)
    lowest = min(red, green, blue)
    if highest >= 224 and _pixel_luminance(pixel) >= 168:
        return True
    return (
        highest >= PIXEL_ART_ACCENT_CHANNEL_THRESHOLD
        and highest - lowest >= PIXEL_ART_ACCENT_SATURATION_THRESHOLD
        and _pixel_luminance(pixel) >= 80
    )


def _clustered_cell_feature_pixels(
    cell: Image.Image,
    predicate: Callable[[tuple[int, int, int, int]], bool],
    *,
    min_coverage: float,
) -> list[tuple[int, int, int, int]]:
    rgba = cell.convert("RGBA")
    if hasattr(rgba, "get_flattened_data"):
        raw_pixels = [tuple(pixel[:4]) for pixel in rgba.get_flattened_data()]
    else:  # pragma: no cover - Pillow compatibility fallback.
        raw_pixels = [tuple(pixel[:4]) for pixel in rgba.getdata()]
    width, height = rgba.size
    feature_indices = [
        index
        for index, pixel in enumerate(raw_pixels)
        if pixel[3] >= PIXEL_ART_ALPHA_THRESHOLD and predicate(pixel)
    ]
    if not feature_indices:
        return []
    cell_area = max(1, width * height)
    feature_coverage = len(feature_indices) / cell_area
    feature_set = set(feature_indices)
    clustered_indices = [
        index
        for index in feature_indices
        if _has_neighboring_feature(index, width, height, feature_set)
    ]
    if clustered_indices and len(clustered_indices) / cell_area >= max(0.0, min_coverage):
        return [raw_pixels[index] for index in clustered_indices]
    if len(feature_indices) == 1 and feature_coverage >= PIXEL_ART_SINGLE_PIXEL_FEATURE_COVERAGE_THRESHOLD:
        return [raw_pixels[feature_indices[0]]]
    return []


def _has_neighboring_feature(index: int, width: int, height: int, feature_indices: set[int]) -> bool:
    x = index % width
    y = index // width
    for neighbor_y in range(max(0, y - 1), min(height, y + 2)):
        for neighbor_x in range(max(0, x - 1), min(width, x + 2)):
            neighbor_index = neighbor_y * width + neighbor_x
            if neighbor_index != index and neighbor_index in feature_indices:
                return True
    return False


def _median_cell_color(pixels: list[tuple[int, int, int, int]]) -> tuple[int, int, int, int]:
    channels = list(zip(*pixels, strict=False))
    return tuple(int(sorted(channel)[len(channel) // 2]) for channel in channels)  # type: ignore[return-value]


def _trimmed_average_cell_color(pixels: list[tuple[int, int, int, int]]) -> tuple[int, int, int, int]:
    ordered = sorted(pixels, key=_pixel_luminance)
    trim = len(ordered) // 8
    if trim > 0 and len(ordered) - trim * 2 >= 3:
        ordered = ordered[trim:-trim]
    alpha_sum = sum(max(1, pixel[3]) for pixel in ordered)
    if alpha_sum <= 0:
        return 0, 0, 0, 0
    red = sum(pixel[0] * max(1, pixel[3]) for pixel in ordered) / alpha_sum
    green = sum(pixel[1] * max(1, pixel[3]) for pixel in ordered) / alpha_sum
    blue = sum(pixel[2] * max(1, pixel[3]) for pixel in ordered) / alpha_sum
    alpha = sum(pixel[3] for pixel in ordered) / max(1, len(ordered))
    return int(round(red)), int(round(green)), int(round(blue)), int(round(alpha))


def _apply_palette_quantization(image: Image.Image, limit: int) -> None:
    limit = max(1, int(limit))
    if hasattr(image, "get_flattened_data"):
        raw_pixels = list(image.get_flattened_data())
    else:  # pragma: no cover - Pillow compatibility fallback.
        raw_pixels = list(image.getdata())
    opaque_pixels = [
        tuple(pixel[:3])
        for pixel in raw_pixels
        if len(pixel) >= 4 and pixel[3] >= PIXEL_ART_ALPHA_THRESHOLD
    ]
    if len({pixel for pixel in opaque_pixels}) <= limit:
        return
    quantized_rgb = _quantized_palette_image(opaque_pixels, limit)
    if not quantized_rgb:
        return
    replacements = iter(quantized_rgb)
    quantized: list[tuple[int, int, int, int]] = []
    for pixel in raw_pixels:
        if len(pixel) < 4 or pixel[3] < PIXEL_ART_ALPHA_THRESHOLD:
            quantized.append((0, 0, 0, 0))
            continue
        quantized.append((*next(replacements), 255))
    image.putdata(quantized)


def _quantized_palette_image(
    colors: list[tuple[int, int, int]],
    limit: int,
) -> list[tuple[int, int, int]]:
    if not colors:
        return []
    swatch = Image.new("RGB", (len(colors), 1))
    swatch.putdata(colors)
    quantized = swatch.quantize(
        colors=max(1, int(limit)),
        method=Image.Quantize.MEDIANCUT,
        dither=Image.Dither.NONE,
    )
    if hasattr(quantized, "get_flattened_data"):
        return [tuple(pixel[:3]) for pixel in quantized.convert("RGB").get_flattened_data()]
    return [tuple(pixel[:3]) for pixel in quantized.convert("RGB").getdata()]


def _image_to_png_bytes(image: Image.Image, *, pixel_art: bool = False) -> bytes:
    buffer = BytesIO()
    info = PngInfo()
    save_kwargs: dict[str, Any] = {"format": "PNG", "optimize": True}
    if pixel_art:
        info.add_text(PIXEL_ART_METADATA_KEY, "1")
        save_kwargs["pnginfo"] = info
    image.save(buffer, **save_kwargs)
    return buffer.getvalue()


def _safe_cache_key(cache_key: str) -> str:
    value = str(cache_key or "").strip()
    if not value:
        return ""
    safe = "".join(char if char.isalnum() or char in {"-", "_"} else "_" for char in value)
    return safe[:80]


def _extension_from_url(url: str) -> str:
    suffix = Path(url.split("?", 1)[0]).suffix.lower().lstrip(".")
    if suffix == "jpeg":
        return "jpeg"
    return suffix if suffix in {"png", "webp", "jpg", "jpeg"} else ""


def _timestamp() -> str:
    return datetime.now().strftime("%Y%m%d_%H%M%S")
