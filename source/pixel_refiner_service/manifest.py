from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .errors import ModelPackageError
from gamedesigner.paths import pixel_refiner_model_dir


DEFAULT_MODEL_ID = "pixel-refiner-v4"
MANIFEST_FILE = "model_manifest.json"
SUPPORTED_RUNTIMES = {"onnxruntime"}


@dataclass(frozen=True)
class ModelManifest:
    id: str
    version: str
    runtime: str
    weights: Path
    target_sizes: list[str] = field(default_factory=list)
    package_dir: Path = Path()
    alpha_modes: list[str] = field(default_factory=lambda: ["preserve"])
    recommended_vram_mb: int = 0
    pixel_art_cleanup: bool = False
    palette_limit: int = 0
    alpha_threshold: int = 128
    tiled_inference: bool = False
    tile_size: int = 0
    tile_overlap: int = 0
    internal_scale: int = 1
    hard_pixel_output: bool = False
    output_layer_version: str = ""
    palette_strategy: str = "source"
    cluster_cleanup: bool = False


def default_model_dir() -> Path:
    return pixel_refiner_model_dir(DEFAULT_MODEL_ID)


def load_model_manifest(model_dir: str | Path | None = None, *, expected_id: str = DEFAULT_MODEL_ID) -> ModelManifest:
    package_dir = Path(model_dir).expanduser() if model_dir else default_model_dir()
    manifest_path = package_dir / MANIFEST_FILE
    if not manifest_path.is_file():
        raise ModelPackageError(
            f"模型包未安装：没有找到 {manifest_path}。请安装 {expected_id} 独立模型包后重试。"
        )
    try:
        with manifest_path.open("r", encoding="utf-8") as file:
            raw = json.load(file)
    except (OSError, json.JSONDecodeError) as exc:
        raise ModelPackageError(f"模型包 manifest 无法读取：{manifest_path}") from exc
    if not isinstance(raw, dict):
        raise ModelPackageError("模型包 manifest 必须是 JSON object。")
    package_id = str(raw.get("id") or "").strip()
    if not package_id:
        raise ModelPackageError("模型包 manifest 缺少 id。")
    if expected_id and package_id != expected_id:
        raise ModelPackageError(f"模型包 id 不匹配：需要 {expected_id}，实际是 {package_id}。")
    runtime = str(raw.get("runtime") or "").strip().lower()
    if runtime not in SUPPORTED_RUNTIMES:
        raise ModelPackageError(f"暂不支持的模型运行时：{runtime or '未填写'}。")
    weights_value = str(raw.get("weights") or raw.get("weights_path") or "").strip()
    if not weights_value:
        raise ModelPackageError("模型包 manifest 缺少 weights。")
    weights = Path(weights_value)
    if not weights.is_absolute():
        weights = package_dir / weights
    if not weights.is_file():
        raise ModelPackageError(f"模型权重文件不存在：{weights}")
    target_sizes = _string_list(raw.get("target_sizes"))
    alpha_modes = _string_list(raw.get("alpha_modes")) or ["preserve"]
    return ModelManifest(
        id=package_id,
        version=str(raw.get("version") or "").strip(),
        runtime=runtime,
        weights=weights,
        target_sizes=target_sizes,
        package_dir=package_dir,
        alpha_modes=alpha_modes,
        recommended_vram_mb=_coerce_int(raw.get("recommended_vram_mb"), 0),
        pixel_art_cleanup=_coerce_bool(raw.get("pixel_art_cleanup")),
        palette_limit=_coerce_int(raw.get("palette_limit"), 0),
        alpha_threshold=_coerce_int(raw.get("alpha_threshold"), 128),
        tiled_inference=_coerce_bool(raw.get("tiled_inference")),
        tile_size=_coerce_int(raw.get("tile_size"), 0),
        tile_overlap=_coerce_int(raw.get("tile_overlap"), 0),
        internal_scale=max(1, _coerce_int(raw.get("internal_scale"), 1)),
        hard_pixel_output=_coerce_bool(raw.get("hard_pixel_output")),
        output_layer_version=str(raw.get("output_layer_version") or "").strip(),
        palette_strategy=str(raw.get("palette_strategy") or "source").strip() or "source",
        cluster_cleanup=_coerce_bool(raw.get("cluster_cleanup")),
    )


def _string_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(item).strip() for item in value if str(item).strip()]


def _coerce_int(value: Any, fallback: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return fallback


def _coerce_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    text = str(value or "").strip().lower()
    return text in {"1", "true", "yes", "y", "on"}
