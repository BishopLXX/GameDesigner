from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .pixel_art import normalized_pixel_output_size
from .paths import pixel_refiner_model_dir


DEFAULT_PIXEL_REFINER_SERVICE_URL = "http://127.0.0.1:8765"
DEFAULT_PIXEL_REFINER_MODEL_ID = "pixel-refiner-v2"
DEFAULT_PIXEL_REFINER_STRENGTH = 0.45
DEFAULT_PIXEL_REFINER_CANDIDATES = 4
PIXEL_REFINER_HEALTH_PATH = "/v1/health"
PIXEL_REFINER_REFINE_PATH = "/v1/pixel/refine"
PIXEL_REFINER_MANIFEST_FILE = "model_manifest.json"
PIXEL_REFINER_PROTOCOL_VERSION = "pixel-refiner-v1"


class PixelRefinerError(RuntimeError):
    pass


@dataclass(frozen=True)
class PixelRefinerRequest:
    input_path: Path
    output_dir: Path
    target_size: str = "auto"
    alpha_mode: str = "preserve"
    palette_limit: int = 0
    strength: float = DEFAULT_PIXEL_REFINER_STRENGTH
    return_candidates: int = DEFAULT_PIXEL_REFINER_CANDIDATES
    model_dir: Path | None = None
    model_id: str = DEFAULT_PIXEL_REFINER_MODEL_ID


@dataclass(frozen=True)
class PixelRefinerOutput:
    path: Path
    label: str = ""
    checks: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class PixelRefinerResult:
    outputs: list[PixelRefinerOutput]
    model: str = ""
    checks: dict[str, Any] = field(default_factory=dict)


def default_pixel_refiner_model_dir() -> Path:
    return pixel_refiner_model_dir(DEFAULT_PIXEL_REFINER_MODEL_ID)


def normalize_pixel_refiner_service_url(value: str | None) -> str:
    text = str(value or "").strip()
    if not text:
        return DEFAULT_PIXEL_REFINER_SERVICE_URL
    if "://" not in text:
        text = f"http://{text}"
    parsed = urllib.parse.urlparse(text)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        return DEFAULT_PIXEL_REFINER_SERVICE_URL
    if any(char.isspace() for char in parsed.netloc):
        return DEFAULT_PIXEL_REFINER_SERVICE_URL
    path = parsed.path.rstrip("/")
    if path == "/v1":
        path = ""
    return urllib.parse.urlunparse(
        parsed._replace(path=path, params="", query="", fragment="")
    ).rstrip("/")


def pixel_refiner_manifest_path(model_dir: str | Path | None = None) -> Path:
    root = Path(str(model_dir or "").strip()) if str(model_dir or "").strip() else default_pixel_refiner_model_dir()
    return root / PIXEL_REFINER_MANIFEST_FILE


def load_pixel_refiner_manifest(model_dir: str | Path | None = None) -> dict[str, Any]:
    path = pixel_refiner_manifest_path(model_dir)
    if not path.exists():
        return {}
    try:
        with path.open("r", encoding="utf-8") as file:
            raw = json.load(file)
    except (OSError, json.JSONDecodeError):
        return {}
    return raw if isinstance(raw, dict) else {}


def pixel_refiner_package_label(model_dir: str | Path | None = None) -> str:
    manifest = load_pixel_refiner_manifest(model_dir)
    package_id = str(manifest.get("id") or manifest.get("name") or DEFAULT_PIXEL_REFINER_MODEL_ID)
    version = str(manifest.get("version") or "").strip()
    return f"{package_id} {version}".strip()


def refine_pixel_art_with_service(
    request: PixelRefinerRequest,
    *,
    service_url: str | None = None,
    timeout: int = 180,
) -> PixelRefinerResult:
    input_path = Path(request.input_path)
    if not input_path.exists():
        raise PixelRefinerError(f"像素修正输入图不存在：{input_path}")
    output_dir = Path(request.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    target_size = normalized_pixel_output_size(request.target_size)
    payload: dict[str, Any] = {
        "input_path": str(input_path.resolve()),
        "output_dir": str(output_dir.resolve()),
        "target_size": target_size,
        "alpha_mode": request.alpha_mode if request.alpha_mode in {"preserve", "predict", "opaque"} else "preserve",
        "palette_limit": max(0, int(request.palette_limit)),
        "strength": max(0.0, min(1.0, float(request.strength))),
        "return_candidates": max(1, min(8, int(request.return_candidates))),
        "model": {
            "id": request.model_id.strip() or DEFAULT_PIXEL_REFINER_MODEL_ID,
            "dir": str(request.model_dir.resolve()) if request.model_dir is not None else "",
        },
        "client": {
            "name": "GameDesigner",
            "protocol": PIXEL_REFINER_PROTOCOL_VERSION,
        },
    }
    endpoint = _join_url(normalize_pixel_refiner_service_url(service_url), PIXEL_REFINER_REFINE_PATH)
    response = _post_json(endpoint, payload, timeout=timeout)
    return _parse_refine_response(response, output_dir)


def check_pixel_refiner_service(
    service_url: str | None = None,
    *,
    timeout: int = 5,
) -> dict[str, Any]:
    endpoint = _join_url(normalize_pixel_refiner_service_url(service_url), PIXEL_REFINER_HEALTH_PATH)
    try:
        response = _get_json(endpoint, timeout=timeout)
    except PixelRefinerError as exc:
        return {"ok": False, "message": str(exc)}
    if not isinstance(response, dict):
        return {"ok": False, "message": "像素修正服务返回了无法识别的健康检查结果。"}
    ok = bool(response.get("ok", True))
    return {**response, "ok": ok}


def _parse_refine_response(payload: Any, output_dir: Path) -> PixelRefinerResult:
    if not isinstance(payload, dict):
        raise PixelRefinerError("像素修正服务返回了无法识别的数据。")
    if payload.get("ok") is False:
        message = str(payload.get("message") or payload.get("error") or "像素修正服务拒绝了请求。")
        raise PixelRefinerError(message)
    raw_outputs = payload.get("outputs")
    if raw_outputs is None:
        raw_outputs = payload.get("images")
    if raw_outputs is None and payload.get("output_path"):
        raw_outputs = [payload.get("output_path")]
    if not isinstance(raw_outputs, list):
        raw_outputs = []
    outputs = [_parse_output_item(item, output_dir) for item in raw_outputs]
    outputs = [output for output in outputs if output is not None]
    if not outputs:
        raise PixelRefinerError("像素修正服务没有返回可用的 PNG 输出。")
    missing = [str(output.path) for output in outputs if not output.path.exists()]
    if missing:
        raise PixelRefinerError(f"像素修正服务返回的输出文件不存在：{missing[0]}")
    checks = payload.get("checks")
    return PixelRefinerResult(
        outputs=outputs,
        model=str(payload.get("model") or payload.get("model_id") or ""),
        checks=checks if isinstance(checks, dict) else {},
    )


def _parse_output_item(item: Any, output_dir: Path) -> PixelRefinerOutput | None:
    if isinstance(item, str):
        path = _resolve_output_path(item, output_dir)
        return PixelRefinerOutput(path=path)
    if not isinstance(item, dict):
        return None
    raw_path = item.get("path") or item.get("output_path") or item.get("file")
    if not isinstance(raw_path, str) or not raw_path.strip():
        return None
    checks = item.get("checks")
    return PixelRefinerOutput(
        path=_resolve_output_path(raw_path, output_dir),
        label=str(item.get("label") or item.get("name") or ""),
        checks=checks if isinstance(checks, dict) else {},
    )


def _resolve_output_path(value: str, output_dir: Path) -> Path:
    path = Path(value)
    if not path.is_absolute():
        path = output_dir / path
    return path


def _join_url(base_url: str, path: str) -> str:
    return f"{base_url.rstrip('/')}/{path.lstrip('/')}"


def _post_json(url: str, payload: dict[str, Any], *, timeout: int) -> dict[str, Any]:
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    request = urllib.request.Request(
        url,
        data=data,
        headers={
            "Content-Type": "application/json",
            "User-Agent": "GameDesigner/Pixel-Refiner",
        },
        method="POST",
    )
    return _request_json(request, timeout=timeout)


def _get_json(url: str, *, timeout: int) -> dict[str, Any]:
    request = urllib.request.Request(
        url,
        headers={"User-Agent": "GameDesigner/Pixel-Refiner"},
        method="GET",
    )
    return _request_json(request, timeout=timeout)


def _request_json(request: urllib.request.Request, *, timeout: int) -> dict[str, Any]:
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            raw = response.read()
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise PixelRefinerError(_format_http_error(exc.code, detail)) from exc
    except urllib.error.URLError as exc:
        raise PixelRefinerError(f"本地像素修正服务连接失败：{exc.reason}") from exc
    try:
        payload = json.loads(raw.decode("utf-8-sig"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise PixelRefinerError("像素修正服务返回了无法解析的 JSON。") from exc
    if not isinstance(payload, dict):
        raise PixelRefinerError("像素修正服务返回了非对象 JSON。")
    return payload


def _format_http_error(code: int, detail: str) -> str:
    message = ""
    try:
        raw = json.loads(detail)
    except json.JSONDecodeError:
        raw = {}
    if isinstance(raw, dict):
        message = str(raw.get("message") or raw.get("error") or "")
    return f"本地像素修正服务请求失败（HTTP {code}）：{message or detail[:500] or '没有错误详情'}"
