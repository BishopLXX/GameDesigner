from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime
from io import BytesIO
from pathlib import Path
from typing import Any

from PIL import Image, ImageOps

from .pixel_refiner_dataset import PixelRefinerSourceRecord, add_source_record, targets_dir
from .pixel_refiner_pair_generation import build_pairs_from_targets, infer_target_context


@dataclass(frozen=True)
class PatchExpansionConfig:
    source_root: Path
    output_source_id: str
    title: str = ""
    author: str = ""
    url: str = ""
    license: str = "Derived patch targets from user-authorized training source"
    license_url: str = ""
    rights_basis: str = ""
    patch_size: int = 64
    overlap: int = 16
    max_patches: int = 0
    max_patches_per_image: int = 12
    min_alpha_coverage: float = 0.03
    min_unique_colors: int = 8
    build_pairs: bool = False
    dry_run: bool = False


def expand_target_patches(config: PatchExpansionConfig) -> dict[str, Any]:
    source_root = Path(config.source_root)
    if not source_root.exists():
        raise FileNotFoundError(source_root)
    output_source_id = _safe_name(config.output_source_id)
    if not output_source_id:
        raise ValueError("output_source_id is required.")

    patch_size = max(8, int(config.patch_size))
    overlap = max(0, min(int(config.overlap), patch_size - 1))
    max_total = max(0, int(config.max_patches))
    max_per_image = max(1, int(config.max_patches_per_image))
    min_alpha_coverage = max(0.0, min(float(config.min_alpha_coverage), 1.0))
    min_unique_colors = max(1, int(config.min_unique_colors))
    output_root = targets_dir() / output_source_id

    stats: dict[str, Any] = {
        "ok": True,
        "source_root": str(source_root),
        "output_source_id": output_source_id,
        "output_root": str(output_root),
        "patch_size": patch_size,
        "overlap": overlap,
        "images_seen": 0,
        "images_too_small": 0,
        "patch_candidates": 0,
        "patches_created": 0,
        "patches_skipped_existing": 0,
        "patches_skipped_duplicate": 0,
        "build_pairs": {},
        "dry_run": bool(config.dry_run),
        "sample_patches": [],
        "errors": [],
    }

    if not config.dry_run:
        add_source_record(
            PixelRefinerSourceRecord(
                source_id=output_source_id,
                title=config.title or f"{source_root.name} overlapping {patch_size}px patches",
                author=config.author,
                url=config.url,
                license=config.license,
                license_url=config.license_url,
                ai_training_allowed=True,
                category="",
                notes=f"derived_from={source_root}; rights_basis={config.rights_basis}; patch_size={patch_size}; overlap={overlap}",
            )
        )

    seen_patch_hashes: set[str] = set()
    for target_path in sorted(path for path in source_root.rglob("*.png") if path.is_file()):
        if max_total > 0 and stats["patches_created"] >= max_total:
            break
        stats["images_seen"] += 1
        try:
            with Image.open(target_path) as loaded:
                image = ImageOps.exif_transpose(loaded).convert("RGBA")
        except Exception as exc:  # pragma: no cover - surfaced through stats for bad user files
            stats["errors"].append({"path": str(target_path), "error": f"{type(exc).__name__}: {exc}"})
            continue
        if image.width < patch_size or image.height < patch_size:
            stats["images_too_small"] += 1
            continue

        _, category = _infer_context(target_path, source_root)
        candidates = _rank_patch_candidates(
            image,
            patch_size=patch_size,
            overlap=overlap,
            min_alpha_coverage=min_alpha_coverage,
            min_unique_colors=min_unique_colors,
        )
        stats["patch_candidates"] += len(candidates)
        kept_for_image = 0
        for candidate in candidates:
            if max_total > 0 and stats["patches_created"] >= max_total:
                break
            if kept_for_image >= max_per_image:
                break
            x, y = int(candidate["x"]), int(candidate["y"])
            patch = image.crop((x, y, x + patch_size, y + patch_size))
            png_bytes = _png_bytes(patch)
            digest = hashlib.sha256(png_bytes).hexdigest()
            if digest in seen_patch_hashes:
                stats["patches_skipped_duplicate"] += 1
                continue
            seen_patch_hashes.add(digest)
            output_path = (
                output_root
                / _safe_name(category or "uncategorized")
                / f"{_safe_name(target_path.stem)}_p{x:04d}_{y:04d}_{digest[:10]}.png"
            )
            if output_path.exists():
                stats["patches_skipped_existing"] += 1
                continue
            if not config.dry_run:
                output_path.parent.mkdir(parents=True, exist_ok=True)
                output_path.write_bytes(png_bytes)
                output_path.with_name(f"{output_path.stem}_metadata.json").write_text(
                    json.dumps(
                        {
                            "kind": "target_patch",
                            "source_id": output_source_id,
                            "category": category,
                            "original_target": str(target_path),
                            "x": x,
                            "y": y,
                            "patch_size": patch_size,
                            "overlap": overlap,
                            "score": candidate["score"],
                            "alpha_coverage": candidate["alpha_coverage"],
                            "unique_colors": candidate["unique_colors"],
                            "created_at": datetime.now().isoformat(timespec="seconds"),
                        },
                        ensure_ascii=False,
                        indent=2,
                    )
                    + "\n",
                    encoding="utf-8",
                )
            stats["patches_created"] += 1
            kept_for_image += 1
            if len(stats["sample_patches"]) < 25:
                stats["sample_patches"].append(str(output_path))

    if config.build_pairs and not config.dry_run and stats["patches_created"] > 0:
        stats["build_pairs"] = build_pairs_from_targets(
            output_root,
            source_id=output_source_id,
            skip_existing=True,
        )

    stats["errors"] = stats["errors"][:100]
    return stats


def _rank_patch_candidates(
    image: Image.Image,
    *,
    patch_size: int,
    overlap: int,
    min_alpha_coverage: float,
    min_unique_colors: int,
) -> list[dict[str, float | int]]:
    stride = max(1, patch_size - overlap)
    xs = _positions(image.width, patch_size, stride)
    ys = _positions(image.height, patch_size, stride)
    candidates: list[dict[str, float | int]] = []
    for y in ys:
        for x in xs:
            patch = image.crop((x, y, x + patch_size, y + patch_size))
            alpha_coverage = _alpha_coverage(patch)
            if alpha_coverage < min_alpha_coverage:
                continue
            unique_colors = _unique_opaque_colors(patch)
            if unique_colors < min_unique_colors:
                continue
            color_score = min(unique_colors / 96.0, 1.0)
            center_score = _center_alpha_score(patch)
            score = alpha_coverage * 2.0 + color_score + center_score
            candidates.append(
                {
                    "x": x,
                    "y": y,
                    "score": round(score, 6),
                    "alpha_coverage": round(alpha_coverage, 6),
                    "unique_colors": unique_colors,
                }
            )
    return sorted(candidates, key=lambda item: (-float(item["score"]), int(item["y"]), int(item["x"])))


def _positions(length: int, patch_size: int, stride: int) -> list[int]:
    if length <= patch_size:
        return [0]
    positions = list(range(0, max(1, length - patch_size + 1), stride))
    last = length - patch_size
    if positions[-1] != last:
        positions.append(last)
    return positions


def _alpha_coverage(patch: Image.Image) -> float:
    histogram = patch.getchannel("A").histogram()
    opaque = sum(histogram[8:])
    return opaque / float(patch.width * patch.height)


def _unique_opaque_colors(patch: Image.Image) -> int:
    colors: set[tuple[int, int, int, int]] = set()
    data = patch.get_flattened_data() if hasattr(patch, "get_flattened_data") else patch.getdata()
    for red, green, blue, alpha in data:
        if alpha >= 8:
            colors.add((red, green, blue, alpha))
    return len(colors)


def _center_alpha_score(patch: Image.Image) -> float:
    margin_x = max(1, patch.width // 4)
    margin_y = max(1, patch.height // 4)
    center = patch.crop((margin_x, margin_y, patch.width - margin_x, patch.height - margin_y))
    return _alpha_coverage(center)


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
