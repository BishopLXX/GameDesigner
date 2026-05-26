from __future__ import annotations

import base64
import json
import mimetypes
import os
import shutil
import urllib.error
import urllib.parse
import urllib.request
import uuid
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from PySide6.QtGui import QImage

from .storage import AppSettings, project_bundle_dir


AI_IMAGE_DIR = "ai_images"
AI_IMAGE_REFERENCES_DIR = "references"
AI_IMAGE_GENERATED_DIR = "generated"
AI_IMAGE_MODEL_PRESETS = [
    "gpt-image-1.5",
    "chatgpt-image-latest",
    "gpt-image-1",
    "gpt-image-1-mini",
    "gpt-image-2",
    "dall-e-3",
]
AI_IMAGE_SIZE_PRESETS = [
    "auto",
    "1024x1024",
    "1536x1024",
    "1024x1536",
]
AI_IMAGE_QUALITY_PRESETS = ["auto", "low", "medium", "high"]
AI_IMAGE_BACKGROUND_PRESETS = ["auto", "transparent", "opaque"]
AI_IMAGE_OUTPUT_FORMAT_PRESETS = ["png", "webp", "jpeg"]
AI_IMAGE_PROVIDERS = {"openai", "compatible"}


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


@dataclass(frozen=True)
class AiGeneratedImage:
    data: bytes
    output_format: str
    revised_prompt: str = ""


@dataclass(frozen=True)
class SavedAiImage:
    path: Path
    width: int
    height: int
    revised_prompt: str = ""


def normalized_ai_image_provider(value: str) -> str:
    return value if value in AI_IMAGE_PROVIDERS else "openai"


def build_ai_image_request(
    settings: AppSettings,
    prompt: str,
    reference_paths: list[str | Path] | None = None,
) -> AiImageRequest:
    provider = normalized_ai_image_provider(getattr(settings, "ai_image_provider", "openai"))
    base_url = str(getattr(settings, "ai_image_base_url", "") or "").strip()
    if provider == "openai" or not base_url:
        base_url = "https://api.openai.com/v1"
    else:
        base_url = _normalized_compatible_base_url(base_url)
    api_key = str(getattr(settings, "ai_image_api_key", "") or "").strip() or os.getenv("OPENAI_API_KEY", "").strip()
    if not api_key:
        raise AiImageError("请先在生图设置里填写 OpenAI 或兼容服务的 API Key。")
    model = str(getattr(settings, "ai_image_model", "") or "").strip() or AI_IMAGE_MODEL_PRESETS[0]
    return AiImageRequest(
        api_key=api_key,
        base_url=base_url.rstrip("/"),
        provider=provider,
        model=model,
        prompt=prompt.strip(),
        reference_paths=[Path(path) for path in (reference_paths or [])],
        size=_coerce_choice(str(getattr(settings, "ai_image_size", "auto") or "auto"), AI_IMAGE_SIZE_PRESETS),
        quality=_coerce_choice(str(getattr(settings, "ai_image_quality", "auto") or "auto"), AI_IMAGE_QUALITY_PRESETS),
        background=_coerce_choice(
            str(getattr(settings, "ai_image_background", "auto") or "auto"),
            AI_IMAGE_BACKGROUND_PRESETS,
        ),
        count=_coerce_count(getattr(settings, "ai_image_count", 1)),
        output_format=_coerce_choice(
            str(getattr(settings, "ai_image_output_format", "png") or "png"),
            AI_IMAGE_OUTPUT_FORMAT_PRESETS,
        ),
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
        )
    else:
        payload = _json_payload(request)
        raw = _post(
            f"{request.base_url}/images/generations",
            request.api_key,
            json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            "application/json",
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


def save_generated_ai_image(
    project_path: str | Path,
    image: AiGeneratedImage,
    *,
    index: int = 1,
) -> SavedAiImage:
    folder = project_bundle_dir(project_path) / AI_IMAGE_DIR / AI_IMAGE_GENERATED_DIR
    folder.mkdir(parents=True, exist_ok=True)
    extension = _safe_output_extension(image.output_format)
    path = folder / f"image_{_timestamp()}_{index}_{uuid.uuid4().hex[:8]}.{extension}"
    path.write_bytes(image.data)
    loaded = QImage(str(path))
    return SavedAiImage(
        path=path,
        width=loaded.width() if not loaded.isNull() else 0,
        height=loaded.height() if not loaded.isNull() else 0,
        revised_prompt=image.revised_prompt,
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


def _post(url: str, api_key: str, data: bytes, content_type: str) -> bytes:
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
        with urllib.request.urlopen(request, timeout=180) as response:
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
    for name, path in files:
        if not path.exists():
            raise AiImageError(f"参考图不存在：{path}")
        mime = mimetypes.guess_type(str(path))[0] or "application/octet-stream"
        chunks.append(f"--{boundary}\r\n".encode("utf-8"))
        chunks.append(
            (
                f'Content-Disposition: form-data; name="{name}"; filename="{path.name}"\r\n'
                f"Content-Type: {mime}\r\n\r\n"
            ).encode("utf-8")
        )
        chunks.append(path.read_bytes())
        chunks.append(b"\r\n")
    chunks.append(f"--{boundary}--\r\n".encode("utf-8"))
    return b"".join(chunks), f"multipart/form-data; boundary={boundary}"


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


def _extension_from_url(url: str) -> str:
    suffix = Path(url.split("?", 1)[0]).suffix.lower().lstrip(".")
    if suffix == "jpeg":
        return "jpeg"
    return suffix if suffix in {"png", "webp", "jpg", "jpeg"} else ""


def _timestamp() -> str:
    return datetime.now().strftime("%Y%m%d_%H%M%S")
