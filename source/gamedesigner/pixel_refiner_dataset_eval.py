from __future__ import annotations

from collections import Counter
from pathlib import Path
from typing import Any

from PIL import Image, ImageOps

from .pixel_refiner_dataset import load_pair_records, summarize_dataset


def evaluate_dataset() -> dict[str, Any]:
    summary = summarize_dataset()
    pairs = load_pair_records()
    size_counter: Counter[str] = Counter()
    category_counter: Counter[str] = Counter()
    input_kind_counter: Counter[str] = Counter()
    for record in pairs:
        size_counter[f"{record.width}x{record.height}"] += 1
        category_counter[record.category] += 1
        input_kind_counter[record.input_kind] += 1
    return {
        **summary,
        "sizes": dict(size_counter),
        "pair_categories": dict(category_counter),
        "input_kinds": dict(input_kind_counter),
        "valid_pairs": sum(1 for record in pairs if _check_pair_images(record.target_path, record.input_path)),
    }


def _check_pair_images(target: Path, input_path: Path) -> bool:
    try:
        with Image.open(target) as loaded_target, Image.open(input_path) as loaded_input:
            target_image = ImageOps.exif_transpose(loaded_target)
            input_image = ImageOps.exif_transpose(loaded_input)
            return target_image.size == input_image.size
    except OSError:
        return False
