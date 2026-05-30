from __future__ import annotations

import hashlib
import json
from collections import deque
from dataclasses import dataclass
from datetime import datetime
from io import BytesIO
from pathlib import Path
from typing import Any

from PIL import Image, ImageOps

from .pixel_refiner_dataset import PixelRefinerSourceRecord, add_source_record, targets_dir
from .pixel_refiner_pair_generation import build_pairs_from_targets, infer_target_context

try:
    import numpy as np
except Exception:  # pragma: no cover - fallback keeps the tool usable in minimal envs.
    np = None


@dataclass(frozen=True)
class CharacterCropConfig:
    source_root: Path
    output_source_id: str
    title: str = ""
    author: str = ""
    url: str = ""
    license: str = "Derived single-character crops from user-authorized training source"
    license_url: str = ""
    rights_basis: str = ""
    alpha_threshold: int = 8
    min_width: int = 48
    min_height: int = 48
    min_area: int = 700
    max_width: int = 512
    max_height: int = 512
    margin: int = 8
    max_crops: int = 0
    max_crops_per_image: int = 24
    build_pairs: bool = False
    dry_run: bool = False


def extract_character_crops(config: CharacterCropConfig) -> dict[str, Any]:
    source_root = Path(config.source_root)
    if not source_root.exists():
        raise FileNotFoundError(source_root)
    output_source_id = _safe_name(config.output_source_id)
    if not output_source_id:
        raise ValueError("output_source_id is required.")

    output_root = targets_dir() / output_source_id
    alpha_threshold = max(0, min(255, int(config.alpha_threshold)))
    min_width = max(1, int(config.min_width))
    min_height = max(1, int(config.min_height))
    min_area = max(1, int(config.min_area))
    max_width = max(min_width, int(config.max_width))
    max_height = max(min_height, int(config.max_height))
    margin = max(0, int(config.margin))
    max_total = max(0, int(config.max_crops))
    max_per_image = max(1, int(config.max_crops_per_image))

    stats: dict[str, Any] = {
        "ok": True,
        "source_root": str(source_root),
        "output_source_id": output_source_id,
        "output_root": str(output_root),
        "images_seen": 0,
        "components_seen": 0,
        "crops_created": 0,
        "crops_skipped_small": 0,
        "crops_skipped_large": 0,
        "crops_skipped_duplicate": 0,
        "crops_skipped_existing": 0,
        "build_pairs": {},
        "dry_run": bool(config.dry_run),
        "sample_crops": [],
        "errors": [],
    }

    if not config.dry_run:
        add_source_record(
            PixelRefinerSourceRecord(
                source_id=output_source_id,
                title=config.title or f"{source_root.name} single-character crops",
                author=config.author,
                url=config.url,
                license=config.license,
                license_url=config.license_url,
                ai_training_allowed=True,
                category="",
                notes=f"derived_from={source_root}; rights_basis={config.rights_basis}; connected_alpha_crops=true",
            )
        )

    seen_hashes: set[str] = set()
    for source_path in sorted(path for path in source_root.rglob("*.png") if path.is_file()):
        if max_total > 0 and stats["crops_created"] >= max_total:
            break
        stats["images_seen"] += 1
        try:
            with Image.open(source_path) as loaded:
                image = ImageOps.exif_transpose(loaded).convert("RGBA")
        except Exception as exc:  # pragma: no cover - surfaced through returned stats
            stats["errors"].append({"path": str(source_path), "error": f"{type(exc).__name__}: {exc}"})
            continue

        components = _connected_alpha_components(image, alpha_threshold=alpha_threshold)
        stats["components_seen"] += len(components)
        _, fallback_category = _infer_context(source_path, source_root)
        kept_for_image = 0
        for component in sorted(components, key=lambda item: (-item["area"], item["y0"], item["x0"])):
            if max_total > 0 and stats["crops_created"] >= max_total:
                break
            if kept_for_image >= max_per_image:
                break
            x0, y0, x1, y1 = _expanded_bbox(component, image.width, image.height, margin=margin)
            width = x1 - x0
            height = y1 - y0
            area = int(component["area"])
            if width < min_width or height < min_height or area < min_area:
                stats["crops_skipped_small"] += 1
                continue
            if width > max_width or height > max_height:
                stats["crops_skipped_large"] += 1
                continue
            crop = image.crop((x0, y0, x1, y1)).convert("RGBA")
            data = _png_bytes(crop)
            digest = hashlib.sha256(data).hexdigest()
            if digest in seen_hashes:
                stats["crops_skipped_duplicate"] += 1
                continue
            seen_hashes.add(digest)
            category = _category_for_crop(width, height, fallback=fallback_category)
            output_path = (
                output_root
                / _safe_name(category)
                / f"{_safe_name(source_path.stem)}_c{x0:04d}_{y0:04d}_{width}x{height}_{digest[:10]}.png"
            )
            if output_path.exists():
                stats["crops_skipped_existing"] += 1
                continue
            if not config.dry_run:
                output_path.parent.mkdir(parents=True, exist_ok=True)
                output_path.write_bytes(data)
                output_path.with_name(f"{output_path.stem}_metadata.json").write_text(
                    json.dumps(
                        {
                            "kind": "target_character_crop",
                            "source_id": output_source_id,
                            "category": category,
                            "original_target": str(source_path),
                            "x": x0,
                            "y": y0,
                            "width": width,
                            "height": height,
                            "alpha_area": area,
                            "created_at": datetime.now().isoformat(timespec="seconds"),
                        },
                        ensure_ascii=False,
                        indent=2,
                    )
                    + "\n",
                    encoding="utf-8",
                )
            stats["crops_created"] += 1
            kept_for_image += 1
            if len(stats["sample_crops"]) < 25:
                stats["sample_crops"].append(str(output_path))

    if config.build_pairs and not config.dry_run and stats["crops_created"] > 0:
        stats["build_pairs"] = build_pairs_from_targets(
            output_root,
            source_id=output_source_id,
            skip_existing=True,
        )

    stats["errors"] = stats["errors"][:100]
    return stats


def _connected_alpha_components(image: Image.Image, *, alpha_threshold: int) -> list[dict[str, int]]:
    if np is not None:
        return _projection_alpha_components(image, alpha_threshold=alpha_threshold)
    alpha = image.getchannel("A")
    width, height = image.size
    pixels = alpha.load()
    seen = bytearray(width * height)
    components: list[dict[str, int]] = []
    for y in range(height):
        row_offset = y * width
        for x in range(width):
            index = row_offset + x
            if seen[index] or int(pixels[x, y]) <= alpha_threshold:
                continue
            queue: deque[tuple[int, int]] = deque([(x, y)])
            seen[index] = 1
            x0 = x1 = x
            y0 = y1 = y
            area = 0
            while queue:
                cx, cy = queue.popleft()
                area += 1
                if cx < x0:
                    x0 = cx
                elif cx > x1:
                    x1 = cx
                if cy < y0:
                    y0 = cy
                elif cy > y1:
                    y1 = cy
                for ny in range(max(0, cy - 1), min(height, cy + 2)):
                    neighbor_row = ny * width
                    for nx in range(max(0, cx - 1), min(width, cx + 2)):
                        neighbor_index = neighbor_row + nx
                        if seen[neighbor_index] or int(pixels[nx, ny]) <= alpha_threshold:
                            continue
                        seen[neighbor_index] = 1
                        queue.append((nx, ny))
            components.append({"x0": x0, "y0": y0, "x1": x1 + 1, "y1": y1 + 1, "area": area})
    return components


def _projection_alpha_components(image: Image.Image, *, alpha_threshold: int) -> list[dict[str, int]]:
    mask = np.asarray(image.getchannel("A")) > int(alpha_threshold)
    if not bool(mask.any()):
        return []
    height, width = mask.shape
    x_runs = _runs_from_counts(mask.sum(axis=0), min_count=max(2, height // 256))
    components: list[dict[str, int]] = []
    for x0, x1 in x_runs:
        x_slice = mask[:, x0:x1]
        y_runs = _runs_from_counts(x_slice.sum(axis=1), min_count=max(2, (x1 - x0) // 256))
        for y0, y1 in y_runs:
            local = mask[y0:y1, x0:x1]
            if not bool(local.any()):
                continue
            ys, xs = np.nonzero(local)
            actual_x0 = x0 + int(xs.min())
            actual_x1 = x0 + int(xs.max()) + 1
            actual_y0 = y0 + int(ys.min())
            actual_y1 = y0 + int(ys.max()) + 1
            components.append(
                {
                    "x0": actual_x0,
                    "y0": actual_y0,
                    "x1": actual_x1,
                    "y1": actual_y1,
                    "area": int(local.sum()),
                }
            )
    return components


def _runs_from_counts(counts: Any, *, min_count: int) -> list[tuple[int, int]]:
    active = [int(value) >= min_count for value in counts]
    runs: list[tuple[int, int]] = []
    start: int | None = None
    for index, value in enumerate(active):
        if value and start is None:
            start = index
        elif not value and start is not None:
            runs.append((start, index))
            start = None
    if start is not None:
        runs.append((start, len(active)))
    return runs


def _expanded_bbox(component: dict[str, int], width: int, height: int, *, margin: int) -> tuple[int, int, int, int]:
    x0 = max(0, int(component["x0"]) - margin)
    y0 = max(0, int(component["y0"]) - margin)
    x1 = min(width, int(component["x1"]) + margin)
    y1 = min(height, int(component["y1"]) + margin)
    return x0, y0, x1, y1


def _category_for_crop(width: int, height: int, *, fallback: str) -> str:
    if height >= width * 1.35 and height >= 128:
        return "character_portrait"
    if width >= height * 1.45:
        return "side_scroller_action_character"
    if fallback in {"character_portrait", "character_sprite", "side_scroller_action_character"}:
        return fallback
    return "character_sprite"


def _png_bytes(image: Image.Image) -> bytes:
    buffer = BytesIO()
    image.save(buffer, format="PNG", optimize=True)
    return buffer.getvalue()


def _infer_context(target_path: Path, source_root: Path) -> tuple[str, str]:
    try:
        relative = target_path.relative_to(source_root)
        if len(relative.parts) >= 2:
            return source_root.name, relative.parts[0]
    except ValueError:
        pass
    return infer_target_context(target_path)


def _safe_name(value: str) -> str:
    cleaned = "".join(char if char.isalnum() or char in {"-", "_"} else "_" for char in str(value or "").strip())
    return cleaned.strip("_") or "item"
