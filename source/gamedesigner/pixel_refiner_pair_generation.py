from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

from PIL import Image, ImageFilter, ImageOps

from io import BytesIO
from .pixel_refiner_dataset import build_pair_record, generated_inputs_dir, load_pair_records, targets_dir


PAIR_INPUT_METHODS = (
    "soft_bilinear",
    "alpha_fringe",
    "palette_drift",
    "lost_detail",
    "dirty_outline",
)


def generate_training_inputs_from_target(
    target_path: str | Path,
    *,
    source_id: str,
    category: str,
    methods: tuple[str, ...] = PAIR_INPUT_METHODS,
) -> list[tuple[Path, str]]:
    target = Path(target_path)
    if not target.is_file():
        raise FileNotFoundError(target)
    with Image.open(target) as loaded:
        image = ImageOps.exif_transpose(loaded).convert("RGBA")
    folder = generated_inputs_dir() / _safe_name(category or "uncategorized") / _safe_name(source_id)
    folder.mkdir(parents=True, exist_ok=True)
    results: list[tuple[Path, str]] = []
    for method in methods:
        generated = _generate_method(image, method)
        buffer = BytesIO()
        generated.save(buffer, format="PNG", optimize=True)
        data = buffer.getvalue()
        digest = hashlib.sha256(data).hexdigest()[:10]
        path = folder / f"{target.stem}_{method}_{digest}.png"
        path.write_bytes(data)
        results.append((path, method))
    return results


def build_pairs_from_targets(
    target_root: str | Path | None = None,
    *,
    source_id: str = "",
    category: str = "",
    methods: tuple[str, ...] = PAIR_INPUT_METHODS,
    skip_existing: bool = True,
) -> dict[str, Any]:
    root = Path(target_root) if target_root else targets_dir()
    if not root.exists():
        raise FileNotFoundError(root)

    existing_pairs = {
        (record.target_sha256, record.input_sha256)
        for record in load_pair_records()
    }
    stats: dict[str, Any] = {
        "target_root": str(root),
        "targets_seen": 0,
        "targets_matched": 0,
        "inputs_generated": 0,
        "pairs_created": 0,
        "pairs_skipped_existing": 0,
        "errors": [],
    }

    target_paths = sorted(path for path in root.rglob("*.png") if path.is_file())
    for target_path in target_paths:
        stats["targets_seen"] += 1
        inferred_source_id, inferred_category = infer_target_context(target_path, root)
        if source_id and inferred_source_id != source_id:
            continue
        if category and inferred_category != category:
            continue
        stats["targets_matched"] += 1
        generated_inputs = generate_training_inputs_from_target(
            target_path,
            source_id=inferred_source_id,
            category=inferred_category,
            methods=methods,
        )
        stats["inputs_generated"] += len(generated_inputs)
        target_sha = sha256_file(target_path)
        for input_path, input_kind in generated_inputs:
            try:
                input_sha = sha256_file(input_path)
                pair_key = (target_sha, input_sha)
                if skip_existing and pair_key in existing_pairs:
                    stats["pairs_skipped_existing"] += 1
                    continue
                build_pair_record(
                    target_path=target_path,
                    input_path=input_path,
                    source_id=inferred_source_id,
                    category=inferred_category,
                    input_kind=input_kind,
                )
                existing_pairs.add(pair_key)
                stats["pairs_created"] += 1
            except Exception as exc:  # pragma: no cover - reported through returned stats
                stats["errors"].append(
                    {
                        "target": str(target_path),
                        "input": str(input_path),
                        "error": f"{type(exc).__name__}: {exc}",
                    }
                )
    stats["errors"] = stats["errors"][:200]
    return stats


def _generate_method(image: Image.Image, method: str) -> Image.Image:
    if method == "soft_bilinear":
        scale = 4
        enlarged = image.resize((image.width * scale, image.height * scale), Image.Resampling.BILINEAR)
        return enlarged.resize(image.size, Image.Resampling.BILINEAR).convert("RGBA")
    if method == "alpha_fringe":
        base = image.convert("RGBA")
        alpha = base.getchannel("A").filter(ImageFilter.GaussianBlur(radius=0.8))
        base.putalpha(alpha)
        return base
    if method == "palette_drift":
        rgb = image.convert("RGBA")
        pixels = []
        data = list(rgb.get_flattened_data()) if hasattr(rgb, "get_flattened_data") else list(rgb.getdata())
        for red, green, blue, alpha in data:
            if alpha <= 0:
                pixels.append((0, 0, 0, 0))
                continue
            pixels.append((min(255, red + 10), max(0, green - 6), min(255, blue + 4), alpha))
        rgb.putdata(pixels)
        return rgb
    if method == "lost_detail":
        if image.width <= 2 or image.height <= 2:
            return image.copy()
        small = image.resize((max(1, image.width // 2), max(1, image.height // 2)), Image.Resampling.BOX)
        return small.resize(image.size, Image.Resampling.NEAREST).convert("RGBA")
    if method == "dirty_outline":
        blurred = image.filter(ImageFilter.GaussianBlur(radius=0.5)).convert("RGBA")
        original = image.convert("RGBA")
        result = Image.blend(blurred, original, 0.75)
        return result.convert("RGBA")
    return image.copy()


def _safe_name(value: str) -> str:
    cleaned = "".join(char if char.isalnum() or char in {"-", "_"} else "_" for char in str(value or "").strip())
    return cleaned.strip("_") or "item"


def infer_target_context(target_path: str | Path, target_root: str | Path | None = None) -> tuple[str, str]:
    path = Path(target_path)
    root = Path(target_root) if target_root else None
    if root is not None:
        try:
            relative = path.relative_to(root)
            if len(relative.parts) >= 3:
                return relative.parts[0], relative.parts[1]
        except ValueError:
            pass
    parts = path.parts
    if "targets" in parts:
        index = parts.index("targets")
        if len(parts) > index + 2:
            return parts[index + 1], parts[index + 2]
    if len(path.parents) >= 2:
        return path.parent.parent.name, path.parent.name
    return "unknown", "uncategorized"


def sha256_file(path: str | Path) -> str:
    digest = hashlib.sha256()
    source = Path(path)
    with source.open("rb") as file:
        for chunk in iter(lambda: file.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()
