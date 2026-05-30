from __future__ import annotations

import json
import shutil
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable

from PIL import Image, ImageDraw, ImageOps

from .paths import pixel_refiner_eval_root
from .pixel_refiner_dataset import PixelRefinerPairRecord, load_pair_records
from .pixel_refiner import PixelRefinerRequest, refine_pixel_art_with_service


EVAL_MANIFEST_FILE = "fixed_eval_manifest.jsonl"
DEFAULT_EVAL_LIMIT = 32
INPUT_KIND_PRIORITY = {
    "software_candidate": 0,
    "ai_pseudo": 1,
    "dirty_outline": 2,
    "soft_bilinear": 3,
    "alpha_fringe": 4,
    "palette_drift": 5,
    "lost_detail": 6,
}


@dataclass(frozen=True)
class EvalSuiteItem:
    item_id: str
    input_path: Path
    reference_path: Path
    source_id: str
    category: str
    input_kind: str
    pair_id: str
    width: int
    height: int


def eval_root() -> Path:
    return pixel_refiner_eval_root()


def fixed_inputs_dir() -> Path:
    return eval_root() / "fixed_inputs"


def references_dir() -> Path:
    return eval_root() / "references"


def eval_runs_dir() -> Path:
    return eval_root() / "runs"


def fixed_eval_manifest_path() -> Path:
    return eval_root() / EVAL_MANIFEST_FILE


def ensure_eval_dirs() -> None:
    for folder in (eval_root(), fixed_inputs_dir(), references_dir(), eval_runs_dir()):
        folder.mkdir(parents=True, exist_ok=True)


def build_fixed_eval_suite(
    *,
    limit: int = DEFAULT_EVAL_LIMIT,
    source_id: str = "",
    category: str = "",
    input_kind: str = "",
    rebuild: bool = True,
) -> dict[str, Any]:
    ensure_eval_dirs()
    if rebuild:
        _clear_pngs(fixed_inputs_dir())
        _clear_pngs(references_dir())
        if fixed_eval_manifest_path().exists():
            fixed_eval_manifest_path().unlink()

    records = _select_eval_records(
        load_pair_records(),
        limit=max(1, int(limit)),
        source_id=str(source_id or "").strip(),
        category=str(category or "").strip(),
        input_kind=str(input_kind or "").strip(),
    )
    items: list[EvalSuiteItem] = []
    for index, record in enumerate(records, start=1):
        stem = _safe_eval_stem(index, record)
        input_path = fixed_inputs_dir() / f"{stem}_input.png"
        reference_path = references_dir() / f"{stem}_target.png"
        shutil.copy2(record.input_path, input_path)
        shutil.copy2(record.target_path, reference_path)
        items.append(
            EvalSuiteItem(
                item_id=stem,
                input_path=input_path,
                reference_path=reference_path,
                source_id=record.source_id,
                category=record.category,
                input_kind=record.input_kind,
                pair_id=record.pair_id,
                width=record.width,
                height=record.height,
            )
        )

    _write_eval_manifest(items)
    return {
        "ok": True,
        "eval_root": str(eval_root()),
        "manifest": str(fixed_eval_manifest_path()),
        "fixed_inputs": str(fixed_inputs_dir()),
        "references": str(references_dir()),
        "items": len(items),
        "source_ids": sorted({item.source_id for item in items}),
        "categories": _count_by(item.category for item in items),
        "input_kinds": _count_by(item.input_kind for item in items),
    }


def load_fixed_eval_suite() -> list[EvalSuiteItem]:
    manifest = fixed_eval_manifest_path()
    if not manifest.is_file():
        return []
    items: list[EvalSuiteItem] = []
    with manifest.open("r", encoding="utf-8") as file:
        for line in file:
            raw = line.strip()
            if not raw:
                continue
            try:
                data = json.loads(raw)
            except json.JSONDecodeError:
                continue
            try:
                items.append(
                    EvalSuiteItem(
                        item_id=str(data.get("item_id") or ""),
                        input_path=Path(str(data.get("input_path") or "")),
                        reference_path=Path(str(data.get("reference_path") or "")),
                        source_id=str(data.get("source_id") or ""),
                        category=str(data.get("category") or ""),
                        input_kind=str(data.get("input_kind") or ""),
                        pair_id=str(data.get("pair_id") or ""),
                        width=int(data.get("width") or 0),
                        height=int(data.get("height") or 0),
                    )
                )
            except (TypeError, ValueError):
                continue
    return items


def render_fixed_eval_contact_sheet(
    *,
    output_path: str | Path | None = None,
    limit: int = 0,
    cell_size: int = 160,
) -> dict[str, Any]:
    ensure_eval_dirs()
    items = load_fixed_eval_suite()
    if limit > 0:
        items = items[: max(1, int(limit))]
    if not items:
        return {"ok": False, "error": "fixed eval suite is empty"}

    cell_size = max(64, min(512, int(cell_size)))
    label_height = 28
    padding = 12
    columns = [("input", "input_path"), ("target", "reference_path")]
    sheet_width = padding + len(columns) * (cell_size + padding)
    sheet_height = padding + len(items) * (cell_size + label_height + padding)
    sheet = Image.new("RGB", (sheet_width, sheet_height), (246, 246, 248))
    draw = ImageDraw.Draw(sheet)

    for row, item in enumerate(items):
        top = padding + row * (cell_size + label_height + padding)
        for column, (label, attr) in enumerate(columns):
            left = padding + column * (cell_size + padding)
            path = getattr(item, attr)
            thumb = _thumbnail_on_checkerboard(path, cell_size)
            sheet.paste(thumb, (left, top + label_height))
            draw.text((left, top), f"{item.item_id} {label}", fill=(30, 30, 34))

    target_path = Path(output_path).expanduser() if output_path else eval_runs_dir() / _contact_sheet_name()
    target_path.parent.mkdir(parents=True, exist_ok=True)
    sheet.save(target_path, format="PNG", optimize=True)
    return {
        "ok": True,
        "path": str(target_path),
        "items": len(items),
        "columns": [label for label, _ in columns],
    }


def run_fixed_eval_model(
    *,
    service_url: str = "http://127.0.0.1:8765",
    model_dir: str | Path | None = None,
    model_id: str = "pixel-refiner-v4",
    output_dir: str | Path | None = None,
    limit: int = DEFAULT_EVAL_LIMIT,
    cell_size: int = 160,
    strength: float = 0.45,
    palette_limit: int = 64,
    alpha_mode: str = "preserve",
    return_candidates: int = 1,
    timeout: int = 300,
    build_suite_if_empty: bool = True,
) -> dict[str, Any]:
    ensure_eval_dirs()
    items = load_fixed_eval_suite()
    if not items and build_suite_if_empty:
        build_fixed_eval_suite(limit=max(1, int(limit or DEFAULT_EVAL_LIMIT)))
        items = load_fixed_eval_suite()
    if limit > 0:
        items = items[: max(1, int(limit))]
    if not items:
        return {"ok": False, "error": "fixed eval suite is empty"}

    model_label = _safe_name(model_id or "model")
    run_root = Path(output_dir).expanduser() if output_dir else eval_runs_dir() / f"{model_label}_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
    outputs_root = run_root / "outputs"
    outputs_root.mkdir(parents=True, exist_ok=True)

    results: list[dict[str, Any]] = []
    output_by_item: dict[str, Path] = {}
    errors: list[dict[str, str]] = []
    for item in items:
        item_output_dir = outputs_root / item.item_id
        try:
            result = refine_pixel_art_with_service(
                PixelRefinerRequest(
                    input_path=item.input_path,
                    output_dir=item_output_dir,
                    target_size=_item_target_size(item),
                    alpha_mode=alpha_mode,
                    palette_limit=max(0, int(palette_limit)),
                    strength=max(0.0, min(1.0, float(strength))),
                    return_candidates=max(1, min(8, int(return_candidates))),
                    model_dir=Path(model_dir).expanduser() if model_dir else None,
                    model_id=str(model_id or "").strip() or "pixel-refiner-v4",
                ),
                service_url=service_url,
                timeout=max(1, int(timeout)),
            )
            chosen = result.outputs[-1].path
            output_by_item[item.item_id] = chosen
            results.append(
                {
                    "item_id": item.item_id,
                    "ok": True,
                    "input_path": str(item.input_path),
                    "output_path": str(chosen),
                    "reference_path": str(item.reference_path),
                    "model": result.model,
                    "checks": result.checks,
                }
            )
        except Exception as exc:
            error = {
                "item_id": item.item_id,
                "ok": False,
                "input_path": str(item.input_path),
                "reference_path": str(item.reference_path),
                "type": type(exc).__name__,
                "message": str(exc),
            }
            results.append(error)
            errors.append({"item_id": item.item_id, "message": str(exc)})

    manifest_path = run_root / "model_eval_manifest.jsonl"
    with manifest_path.open("w", encoding="utf-8") as file:
        for result in results:
            file.write(json.dumps(result, ensure_ascii=False) + "\n")

    contact_sheet = run_root / "contact_sheet.png"
    sheet_result = render_model_eval_contact_sheet(
        items=items,
        model_outputs=output_by_item,
        output_path=contact_sheet,
        model_label=str(model_id or "model"),
        cell_size=cell_size,
    )
    return {
        "ok": not errors,
        "model_id": str(model_id or ""),
        "model_dir": str(model_dir or ""),
        "run_dir": str(run_root),
        "manifest": str(manifest_path),
        "contact_sheet": str(contact_sheet),
        "items": len(items),
        "succeeded": len(output_by_item),
        "failed": len(errors),
        "errors": errors[:10],
        "columns": sheet_result.get("columns", []),
    }


def render_model_eval_contact_sheet(
    *,
    items: list[EvalSuiteItem],
    model_outputs: dict[str, Path],
    output_path: str | Path,
    model_label: str,
    cell_size: int = 160,
) -> dict[str, Any]:
    if not items:
        return {"ok": False, "error": "no eval items"}

    cell_size = max(64, min(512, int(cell_size)))
    label_height = 34
    padding = 12
    columns = [
        ("input", "input_path"),
        (model_label or "model", "model_output"),
        ("target", "reference_path"),
    ]
    sheet_width = padding + len(columns) * (cell_size + padding)
    sheet_height = padding + len(items) * (cell_size + label_height + padding)
    sheet = Image.new("RGB", (sheet_width, sheet_height), (246, 246, 248))
    draw = ImageDraw.Draw(sheet)

    for row, item in enumerate(items):
        top = padding + row * (cell_size + label_height + padding)
        for column, (label, attr) in enumerate(columns):
            left = padding + column * (cell_size + padding)
            if attr == "model_output":
                path = model_outputs.get(item.item_id)
                thumb = _thumbnail_on_checkerboard(path, cell_size) if path else _failed_thumbnail(cell_size)
            else:
                path = getattr(item, attr)
                thumb = _thumbnail_on_checkerboard(path, cell_size)
            sheet.paste(thumb, (left, top + label_height))
            draw.text((left, top), _contact_label(item, label), fill=(30, 30, 34))

    target_path = Path(output_path).expanduser()
    target_path.parent.mkdir(parents=True, exist_ok=True)
    sheet.save(target_path, format="PNG", optimize=True)
    return {
        "ok": True,
        "path": str(target_path),
        "items": len(items),
        "columns": [label for label, _ in columns],
    }


def _select_eval_records(
    records: list[PixelRefinerPairRecord],
    *,
    limit: int,
    source_id: str,
    category: str,
    input_kind: str,
) -> list[PixelRefinerPairRecord]:
    filtered = [
        record
        for record in records
        if record.input_path.is_file()
        and record.target_path.is_file()
        and (not source_id or record.source_id == source_id)
        and (not category or record.category == category)
        and (not input_kind or record.input_kind == input_kind)
    ]
    filtered.sort(
        key=lambda record: (
            record.category,
            INPUT_KIND_PRIORITY.get(record.input_kind, 99),
            -(record.width * record.height),
            record.pair_id,
        )
    )
    category_order = sorted(
        {record.category for record in filtered},
        key=lambda value: (0 if value == "side_scroller_action_character" else 1, value),
    )
    kind_order = sorted({record.input_kind for record in filtered}, key=lambda value: INPUT_KIND_PRIORITY.get(value, 99))
    by_bucket: dict[tuple[str, str], list[PixelRefinerPairRecord]] = {
        (category_name, kind_name): [
            record
            for record in filtered
            if record.category == category_name and record.input_kind == kind_name
        ]
        for category_name in category_order
        for kind_name in kind_order
    }
    selected: list[PixelRefinerPairRecord] = []
    seen_targets: set[str] = set()
    while len(selected) < limit:
        progressed = False
        for category_name in category_order:
            for kind_name in kind_order:
                bucket = by_bucket.get((category_name, kind_name), [])
                while bucket:
                    record = bucket.pop(0)
                    if record.target_sha256 in seen_targets:
                        continue
                    selected.append(record)
                    seen_targets.add(record.target_sha256)
                    progressed = True
                    break
                if len(selected) >= limit:
                    break
            if len(selected) >= limit:
                break
        if not progressed:
            break
    if len(selected) < limit:
        seen_pairs = {record.pair_id for record in selected}
        for record in filtered:
            if record.pair_id in seen_pairs:
                continue
            selected.append(record)
            if len(selected) >= limit:
                break
    return selected


def _write_eval_manifest(items: list[EvalSuiteItem]) -> None:
    fixed_eval_manifest_path().parent.mkdir(parents=True, exist_ok=True)
    with fixed_eval_manifest_path().open("w", encoding="utf-8") as file:
        for item in items:
            file.write(
                json.dumps(
                    {
                        "item_id": item.item_id,
                        "input_path": str(item.input_path),
                        "reference_path": str(item.reference_path),
                        "source_id": item.source_id,
                        "category": item.category,
                        "input_kind": item.input_kind,
                        "pair_id": item.pair_id,
                        "width": item.width,
                        "height": item.height,
                    },
                    ensure_ascii=False,
                )
                + "\n"
            )


def _thumbnail_on_checkerboard(path: Path, cell_size: int) -> Image.Image:
    canvas = _checkerboard(cell_size, cell_size)
    try:
        with Image.open(path) as loaded:
            image = ImageOps.exif_transpose(loaded).convert("RGBA")
    except OSError:
        return canvas.convert("RGB")
    image.thumbnail((cell_size, cell_size), Image.Resampling.NEAREST)
    left = (cell_size - image.width) // 2
    top = (cell_size - image.height) // 2
    canvas.alpha_composite(image, (left, top))
    return canvas.convert("RGB")


def _failed_thumbnail(cell_size: int) -> Image.Image:
    image = Image.new("RGB", (cell_size, cell_size), (248, 240, 240))
    draw = ImageDraw.Draw(image)
    draw.rectangle((0, 0, cell_size - 1, cell_size - 1), outline=(210, 72, 72), width=2)
    draw.line((10, 10, cell_size - 10, cell_size - 10), fill=(210, 72, 72), width=2)
    draw.line((cell_size - 10, 10, 10, cell_size - 10), fill=(210, 72, 72), width=2)
    draw.text((12, max(12, cell_size // 2 - 8)), "failed", fill=(120, 36, 36))
    return image


def _item_target_size(item: EvalSuiteItem) -> str:
    if item.width > 0 and item.height > 0:
        return f"{item.width}x{item.height}"
    try:
        with Image.open(item.input_path) as loaded:
            image = ImageOps.exif_transpose(loaded)
            return f"{image.width}x{image.height}"
    except OSError:
        return "auto"


def _contact_label(item: EvalSuiteItem, label: str) -> str:
    prefix = f"{item.item_id} " if label == "input" else ""
    text = f"{prefix}{label}"
    return text[:42]


def _checkerboard(width: int, height: int, step: int = 8) -> Image.Image:
    image = Image.new("RGBA", (width, height), (255, 255, 255, 255))
    draw = ImageDraw.Draw(image)
    for top in range(0, height, step):
        for left in range(0, width, step):
            color = (226, 226, 230, 255) if ((left // step) + (top // step)) % 2 == 0 else (246, 246, 248, 255)
            draw.rectangle((left, top, min(width, left + step) - 1, min(height, top + step) - 1), fill=color)
    return image


def _safe_eval_stem(index: int, record: PixelRefinerPairRecord) -> str:
    parts = [
        f"{index:03d}",
        record.category,
        record.input_kind,
        record.pair_id[:8],
    ]
    return "_".join(_safe_name(part) for part in parts if part)


def _safe_name(value: str) -> str:
    cleaned = "".join(char if char.isalnum() or char in {"-", "_"} else "_" for char in str(value or "").strip())
    return cleaned.strip("_") or "item"


def _clear_pngs(folder: Path) -> None:
    if not folder.exists():
        return
    for path in folder.glob("*.png"):
        path.unlink()


def _count_by(values: Iterable[str]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for value in values:
        key = str(value or "unknown")
        counts[key] = counts.get(key, 0) + 1
    return dict(sorted(counts.items()))


def _contact_sheet_name() -> str:
    return f"fixed_eval_contact_{datetime.now().strftime('%Y%m%d_%H%M%S')}.png"
