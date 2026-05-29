from __future__ import annotations

import hashlib
import json
import random
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import replace
from datetime import datetime
from io import BytesIO
from pathlib import Path
from typing import Any, Callable

from PIL import Image, ImageFilter, ImageOps

from .image_ai import AiGeneratedImage, AiImageRequest, build_ai_image_request, generate_ai_images
from .pixel_refiner_dataset import build_pair_record, dataset_dir, generated_inputs_dir, load_pair_records, targets_dir
from .pixel_refiner_pair_generation import infer_target_context, sha256_file
from .storage import AppSettings, load_settings


AI_PSEUDO_INPUT_KIND = "ai_pseudo"
AI_PSEUDO_MANIFEST_NAME = "ai_pseudo_pairs.jsonl"
AI_PSEUDO_PROMPT = """Create a pseudo-AI pixel-art version of the attached true pixel-art character.

Use the reference image as the composition, pose, costume, silhouette, and color-family guide. Keep it as a single character or action sprite on a transparent or plain background. Preserve the approximate framing and proportions.

Important: this output should look like an AI-generated imitation of pixel art, not a perfect hand-authored sprite. It may have slightly soft edges, too many colors, uneven pixel clusters, noisy outlines, fuzzy alpha, or over-smoothed shading, while still clearly matching the reference character.

Do not add extra characters, text, logos, UI, scenery, or a new pose."""

AiImageGenerator = Callable[[AiImageRequest], list[AiGeneratedImage]]


def generate_ai_pseudo_pairs(
    target_root: str | Path | None = None,
    *,
    source_id: str = "",
    category: str = "",
    limit: int = 5,
    variants_per_target: int = 1,
    prompt: str = "",
    background: str = "auto",
    alpha_mode: str = "soft_target_mask",
    request_timeout: int = 90,
    workers: int = 4,
    skip_existing: bool = True,
    dry_run: bool = False,
    settings: AppSettings | None = None,
    generator: AiImageGenerator = generate_ai_images,
) -> dict[str, Any]:
    root = Path(target_root) if target_root else targets_dir()
    if not root.exists():
        raise FileNotFoundError(root)
    existing_counts = _build_ai_pseudo_existing_counts()
    existing_counts_snapshot = dict(existing_counts)
    selected = select_target_paths(
        root,
        source_id=source_id,
        category=category,
        limit=limit,
        skip_existing=skip_existing,
        variants_per_target=variants_per_target,
        existing_counts=existing_counts,
    )
    stats: dict[str, Any] = {
        "target_root": str(root),
        "targets_selected": len(selected),
        "targets_attempted": len(selected),
        "targets_processed": 0,
        "images_requested": 0,
        "images_saved": 0,
        "pairs_created": 0,
        "pairs_skipped_existing": 0,
        "dry_run": dry_run,
        "target_paths": [str(path) for path in selected[:50]],
        "errors": [],
    }
    if dry_run or not selected:
        return stats

    effective_settings = settings or load_settings()
    manifest_path = dataset_dir() / "manifests" / AI_PSEUDO_MANIFEST_NAME
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    worker_count = max(1, min(int(workers), len(selected)))
    with ThreadPoolExecutor(max_workers=worker_count, thread_name_prefix="pixel-refiner-ai-pseudo") as executor:
        future_map = {
            executor.submit(
                _generate_ai_pseudo_target_images,
                target_path,
                root=root,
                settings=effective_settings,
                source_id=source_id,
                category=category,
                variants_per_target=variants_per_target,
                prompt=prompt,
                background=background,
                alpha_mode=alpha_mode,
                request_timeout=request_timeout,
                skip_existing=skip_existing,
                generator=generator,
                existing_counts=existing_counts_snapshot,
            ): target_path
            for target_path in selected
        }
        for future in as_completed(future_map):
            target_path = future_map[future]
            try:
                result = future.result()
            except Exception as exc:  # pragma: no cover - surfaced in CLI stats
                stats["errors"].append(
                    {
                        "target": str(target_path),
                        "error": f"{type(exc).__name__}: {exc}",
                    }
                )
                continue

            stats["images_requested"] += int(result.get("images_requested", 0))
            stats["pairs_skipped_existing"] += int(result.get("pairs_skipped_existing", 0))
            if result.get("errors"):
                stats["errors"].extend(result["errors"])

            images = result.get("images") or []
            if not images:
                continue

            target_saved = False
            inferred_source_id = str(result.get("source_id") or "")
            inferred_category = str(result.get("category") or "")
            request = result.get("request")
            prompt_text = str(result.get("prompt") or "")
            target_sha = str(result.get("target_sha256") or "")
            for variant_index, image in images:
                try:
                    input_path = save_ai_pseudo_input(
                        image,
                        target_path=target_path,
                        source_id=inferred_source_id,
                        category=inferred_category,
                        variant_index=int(variant_index),
                        alpha_mode=alpha_mode,
                    )
                    record = build_pair_record(
                        target_path=target_path,
                        input_path=input_path,
                        source_id=inferred_source_id,
                        category=inferred_category,
                        input_kind=AI_PSEUDO_INPUT_KIND,
                        prompt=prompt_text,
                    )
                    stats["images_saved"] += 1
                    stats["pairs_created"] += 1
                    target_saved = True
                    if target_sha:
                        existing_counts[target_sha] = existing_counts.get(target_sha, 0) + 1
                    append_manifest(
                        manifest_path,
                        {
                            "created_at": datetime.now().isoformat(timespec="seconds"),
                            "target_path": str(target_path),
                            "input_path": str(input_path),
                            "pair_id": record.pair_id,
                            "source_id": inferred_source_id,
                            "category": inferred_category,
                            "input_kind": AI_PSEUDO_INPUT_KIND,
                            "variant_index": int(variant_index),
                            "model": request.model if request is not None else "",
                            "provider": request.provider if request is not None else "",
                            "size": request.size if request is not None else "",
                            "quality": request.quality if request is not None else "",
                            "background": request.background if request is not None else _normalized_background(background),
                            "alpha_mode": alpha_mode,
                            "prompt_sha256": hashlib.sha256(prompt_text.encode("utf-8")).hexdigest(),
                        },
                    )
                except Exception as exc:  # pragma: no cover - surfaced in CLI stats
                    stats["errors"].append(
                        {
                            "target": str(target_path),
                            "variant_index": int(variant_index),
                            "error": f"{type(exc).__name__}: {exc}",
                        }
                    )
            if target_saved:
                stats["targets_processed"] += 1
    stats["errors"] = stats["errors"][:200]
    return stats


def select_target_paths(
    target_root: str | Path,
    *,
    source_id: str = "",
    category: str = "",
    limit: int = 5,
    skip_existing: bool = True,
    variants_per_target: int = 1,
    existing_counts: dict[str, int] | None = None,
) -> list[Path]:
    root = Path(target_root)
    paths = sorted(path for path in root.rglob("*.png") if path.is_file())
    counts = existing_counts if existing_counts is not None else _build_ai_pseudo_existing_counts()
    buckets: dict[int, list[Path]] = {}
    for path in paths:
        inferred_source_id, inferred_category = infer_target_context(path, root)
        if source_id and inferred_source_id != source_id:
            continue
        if category and inferred_category != category:
            continue
        target_sha = sha256_file(path)
        existing_count = counts.get(target_sha, 0)
        if skip_existing and existing_count >= variants_per_target:
            continue
        buckets.setdefault(existing_count, []).append(path)
    rng = random.Random()
    selected: list[Path] = []
    for existing_count in sorted(buckets):
        bucket = buckets[existing_count]
        rng.shuffle(bucket)
        selected.extend(bucket)
    if limit > 0:
        return selected[:limit]
    return selected


def count_existing_ai_pseudo_pairs(target_path: str | Path) -> int:
    target_sha = sha256_file(target_path)
    return sum(
        1
        for record in load_pair_records()
        if record.target_sha256 == target_sha and record.input_kind == AI_PSEUDO_INPUT_KIND
    )


def _generate_ai_pseudo_target_images(
    target_path: str | Path,
    *,
    root: Path,
    settings: AppSettings,
    source_id: str,
    category: str,
    variants_per_target: int,
    prompt: str,
    background: str,
    alpha_mode: str,
    request_timeout: int,
    skip_existing: bool,
    generator: AiImageGenerator,
    existing_counts: dict[str, int],
) -> dict[str, Any]:
    path = Path(target_path)
    inferred_source_id, inferred_category = infer_target_context(path, root)
    target_sha = sha256_file(path)
    existing_count = existing_counts.get(target_sha, 0)
    if skip_existing and existing_count >= variants_per_target:
        return {
            "target_path": str(path),
            "target_sha256": target_sha,
            "source_id": inferred_source_id,
            "category": inferred_category,
            "prompt": "",
            "request": None,
            "images_requested": 0,
            "pairs_skipped_existing": existing_count,
            "images": [],
            "errors": [],
        }

    needed = max(1, variants_per_target - existing_count if skip_existing else variants_per_target)
    request = build_ai_image_request(
        settings,
        build_prompt(path, inferred_category, prompt=prompt),
        [path],
    )
    request = replace(
        request,
        count=needed,
        output_format="png",
        timeout=max(1, int(request_timeout)),
        background=_normalized_background(background),
    )

    images: list[tuple[int, AiGeneratedImage]] = []
    errors: list[dict[str, Any]] = []
    requested_total = 0
    remaining = needed
    next_variant_index = existing_count
    prompt_text = request.prompt
    while remaining > 0:
        request_for_call = replace(request, count=remaining)
        requested_total += _request_count_like(request_for_call)
        try:
            generated = generator(request_for_call)
        except Exception as exc:
            errors.append(
                {
                    "target": str(path),
                    "error": f"{type(exc).__name__}: {exc}",
                }
            )
            break
        if not generated:
            errors.append(
                {
                    "target": str(path),
                    "error": "ValueError: 图像生成服务未返回任何图片。",
                }
            )
            break
        take = min(remaining, len(generated))
        for image in generated[:take]:
            next_variant_index += 1
            images.append((next_variant_index, image))
        remaining -= take
        if take < len(generated):
            break

    return {
        "target_path": str(path),
        "target_sha256": target_sha,
        "source_id": inferred_source_id,
        "category": inferred_category,
        "prompt": prompt_text,
        "request": request,
        "images_requested": requested_total,
        "pairs_skipped_existing": 0,
        "images": images,
        "errors": errors,
    }


def _request_count_like(request: AiImageRequest) -> int:
    if request.model in {"dall-e-3", "gpt-image-2"}:
        return 1
    return max(1, int(request.count))


def _build_ai_pseudo_existing_counts() -> dict[str, int]:
    counts: dict[str, int] = {}
    for record in load_pair_records():
        if record.input_kind != AI_PSEUDO_INPUT_KIND:
            continue
        counts[record.target_sha256] = counts.get(record.target_sha256, 0) + 1
    return counts


def build_prompt(target_path: str | Path, category: str, *, prompt: str = "") -> str:
    path = Path(target_path)
    with Image.open(path) as loaded:
        width, height = loaded.size
    category_hint = "side-scroller action sprite" if category == "side_scroller_action_character" else "character portrait sprite"
    custom = str(prompt or "").strip()
    base = custom or AI_PSEUDO_PROMPT
    return f"{base}\n\nTarget type: {category_hint}. Target canvas: {width}x{height} pixels."


def save_ai_pseudo_input(
    generated: AiGeneratedImage,
    *,
    target_path: str | Path,
    source_id: str,
    category: str,
    variant_index: int,
    alpha_mode: str = "soft_target_mask",
) -> Path:
    target = Path(target_path)
    with Image.open(target) as loaded_target:
        target_image = ImageOps.exif_transpose(loaded_target).convert("RGBA")
    pseudo = normalize_generated_to_target(
        generated.data,
        target_image=target_image,
        alpha_mode=alpha_mode,
    )
    buffer = BytesIO()
    pseudo.save(buffer, format="PNG", optimize=True)
    data = buffer.getvalue()
    digest = hashlib.sha256(data).hexdigest()[:10]
    folder = generated_inputs_dir() / _safe_name(category or "uncategorized") / _safe_name(source_id) / AI_PSEUDO_INPUT_KIND
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"{target.stem}_{AI_PSEUDO_INPUT_KIND}_{variant_index:02d}_{digest}.png"
    path.write_bytes(data)
    return path


def normalize_generated_to_target(
    data: bytes,
    *,
    target_image: Image.Image,
    alpha_mode: str = "soft_target_mask",
) -> Image.Image:
    with Image.open(BytesIO(data)) as loaded:
        generated = ImageOps.exif_transpose(loaded).convert("RGBA")
    if generated.size != target_image.size:
        generated = generated.resize(target_image.size, Image.Resampling.BILINEAR)
    mode = alpha_mode if alpha_mode in {"preserve", "target_mask", "soft_target_mask"} else "soft_target_mask"
    if mode == "target_mask":
        generated.putalpha(target_image.getchannel("A"))
    elif mode == "soft_target_mask":
        generated.putalpha(target_image.getchannel("A").filter(ImageFilter.GaussianBlur(radius=0.7)))
    return generated.convert("RGBA")


def append_manifest(path: Path, row: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as file:
        file.write(json.dumps(row, ensure_ascii=False) + "\n")


def _safe_name(value: str) -> str:
    cleaned = "".join(char if char.isalnum() or char in {"-", "_"} else "_" for char in str(value or "").strip())
    return cleaned.strip("_") or "item"


def _normalized_background(value: str) -> str:
    text = str(value or "").strip()
    if text in {"transparent", "opaque", "auto"}:
        return text
    return "auto"
