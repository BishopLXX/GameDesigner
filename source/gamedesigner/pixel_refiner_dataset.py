from __future__ import annotations

import csv
import hashlib
import json
import shutil
import uuid
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from PIL import Image, ImageOps

from .storage import APP_NAME
from .paths import pixel_refiner_data_root


PIXEL_REFINER_DATASET_VERSION = "character_large_v1"
PIXEL_REFINER_DATASET_ROOT_NAME = "pixel_refiner"
PIXEL_REFINER_SOURCE_CSV = "licensed_sources.csv"
PIXEL_REFINER_INDEX_FILE = "index.jsonl"
PIXEL_REFINER_TARGETS_DIR = "targets"
PIXEL_REFINER_INPUTS_DIR = "inputs"
PIXEL_REFINER_PAIRS_DIR = "pairs"
PIXEL_REFINER_EVAL_DIR = "eval"
PIXEL_REFINER_GENERATED_DIR = "generated_inputs"
PIXEL_REFINER_METADATA_FILE = "metadata.json"
PIXEL_REFINER_FEEDBACK_SOURCE_ID = "gamedesigner_feedback"
PIXEL_REFINER_SOFTWARE_INPUT_KIND = "software_candidate"
PIXEL_REFINER_FEEDBACK_LICENSE = "User-provided local training pair"
PIXEL_REFINER_DEFAULT_FEEDBACK_CATEGORY = "character_portrait"


@dataclass(frozen=True)
class PixelRefinerSourceRecord:
    source_id: str
    title: str
    author: str
    url: str
    license: str
    license_url: str
    ai_training_allowed: bool
    category: str
    notes: str = ""


@dataclass(frozen=True)
class PixelRefinerPairRecord:
    pair_id: str
    target_path: Path
    input_path: Path
    source_id: str
    category: str
    target_sha256: str
    input_sha256: str
    width: int
    height: int
    created_at: str
    input_kind: str
    prompt: str = ""
    license: str = ""
    notes: str = ""


@dataclass(frozen=True)
class PixelRefinerPairIngestResult:
    record: PixelRefinerPairRecord
    created: bool


def global_dataset_root() -> Path:
    return pixel_refiner_data_root(PIXEL_REFINER_DATASET_VERSION)


def dataset_dir() -> Path:
    return global_dataset_root()


def targets_dir() -> Path:
    return dataset_dir() / PIXEL_REFINER_TARGETS_DIR


def inputs_dir() -> Path:
    return dataset_dir() / PIXEL_REFINER_INPUTS_DIR


def pairs_dir() -> Path:
    return dataset_dir() / PIXEL_REFINER_PAIRS_DIR


def eval_dir() -> Path:
    return dataset_dir() / PIXEL_REFINER_EVAL_DIR


def generated_inputs_dir() -> Path:
    return dataset_dir() / PIXEL_REFINER_GENERATED_DIR


def source_csv_path() -> Path:
    return dataset_dir() / PIXEL_REFINER_SOURCE_CSV


def index_path() -> Path:
    return dataset_dir() / PIXEL_REFINER_INDEX_FILE


def ensure_dataset_dirs() -> None:
    for folder in (dataset_dir(), targets_dir(), inputs_dir(), pairs_dir(), eval_dir(), generated_inputs_dir()):
        folder.mkdir(parents=True, exist_ok=True)


def add_source_record(record: PixelRefinerSourceRecord) -> None:
    ensure_dataset_dirs()
    path = source_csv_path()
    existing = load_source_records()
    existing = [item for item in existing if item.source_id != record.source_id]
    existing.append(record)
    _write_source_records(existing, path)


def load_source_records(path: str | Path | None = None) -> list[PixelRefinerSourceRecord]:
    source_path = Path(path) if path else source_csv_path()
    if not source_path.exists():
        return []
    records: list[PixelRefinerSourceRecord] = []
    with source_path.open("r", encoding="utf-8", newline="") as file:
        reader = csv.DictReader(file)
        for row in reader:
            source_id = str(row.get("source_id") or "").strip()
            if not source_id:
                continue
            records.append(
                PixelRefinerSourceRecord(
                    source_id=source_id,
                    title=str(row.get("title") or "").strip(),
                    author=str(row.get("author") or "").strip(),
                    url=str(row.get("url") or "").strip(),
                    license=str(row.get("license") or "").strip(),
                    license_url=str(row.get("license_url") or "").strip(),
                    ai_training_allowed=_coerce_bool(row.get("ai_training_allowed")),
                    category=str(row.get("category") or "").strip(),
                    notes=str(row.get("notes") or "").strip(),
                )
            )
    return records


def ingest_target_image(
    source_path: str | Path,
    *,
    source_id: str,
    category: str,
    title: str = "",
    license: str = "",
    prompt: str = "",
    notes: str = "",
) -> Path:
    ensure_dataset_dirs()
    source = Path(source_path)
    if not source.is_file():
        raise FileNotFoundError(source)
    target_folder = targets_dir() / _safe_name(category or "uncategorized")
    target_folder.mkdir(parents=True, exist_ok=True)
    checksum = _sha256_file(source)
    target_name = f"{_safe_name(source_id)}_{checksum[:12]}.png"
    target_path = target_folder / target_name
    _copy_png_asset(source, target_path)
    _write_metadata(
        target_path.with_name(f"{target_path.stem}_{PIXEL_REFINER_METADATA_FILE}"),
        {
            "kind": "target",
            "source_id": source_id,
            "title": title,
            "category": category,
            "license": license,
            "prompt": prompt,
            "notes": notes,
            "sha256": checksum,
            "created_at": _timestamp(),
        },
    )
    return target_path


def ingest_generated_input_image(
    source_path: str | Path,
    *,
    source_id: str,
    category: str,
    input_kind: str,
    prompt: str = "",
    notes: str = "",
) -> Path:
    ensure_dataset_dirs()
    source = Path(source_path)
    if not source.is_file():
        raise FileNotFoundError(source)
    input_folder = generated_inputs_dir() / _safe_name(category or "uncategorized") / _safe_name(input_kind)
    input_folder.mkdir(parents=True, exist_ok=True)
    checksum = _sha256_file(source)
    input_name = f"{_safe_name(source_id)}_{checksum[:12]}.png"
    input_path = input_folder / input_name
    _copy_png_asset(source, input_path)
    _write_metadata(
        input_path.with_name(f"{input_path.stem}_{PIXEL_REFINER_METADATA_FILE}"),
        {
            "kind": "input",
            "source_id": source_id,
            "category": category,
            "input_kind": input_kind,
            "prompt": prompt,
            "notes": notes,
            "sha256": checksum,
            "created_at": _timestamp(),
        },
    )
    return input_path


def build_pair_record(
    *,
    target_path: str | Path,
    input_path: str | Path,
    source_id: str,
    category: str,
    input_kind: str,
    prompt: str = "",
    license: str = "",
    notes: str = "",
) -> PixelRefinerPairRecord:
    target = Path(target_path)
    source = Path(input_path)
    if not target.is_file():
        raise FileNotFoundError(target)
    if not source.is_file():
        raise FileNotFoundError(source)
    target_size = _read_png_size(target)
    input_size = _read_png_size(source)
    if target_size != input_size:
        raise ValueError("训练对的输入和目标尺寸必须一致。")
    ensure_dataset_dirs()
    pair_id = uuid.uuid4().hex
    pair_folder = pairs_dir() / _safe_name(category or "uncategorized") / pair_id
    pair_folder.mkdir(parents=True, exist_ok=True)
    copied_target = pair_folder / "target.png"
    copied_input = pair_folder / "input.png"
    shutil.copy2(target, copied_target)
    shutil.copy2(source, copied_input)
    record = PixelRefinerPairRecord(
        pair_id=pair_id,
        target_path=copied_target,
        input_path=copied_input,
        source_id=source_id,
        category=category,
        target_sha256=_sha256_file(copied_target),
        input_sha256=_sha256_file(copied_input),
        width=target_size[0],
        height=target_size[1],
        created_at=_timestamp(),
        input_kind=input_kind,
        prompt=prompt,
        license=license,
        notes=notes,
    )
    _append_index(record)
    _write_metadata(
        pair_folder / PIXEL_REFINER_METADATA_FILE,
        {
            "pair_id": record.pair_id,
            "source_id": record.source_id,
            "category": record.category,
            "input_kind": record.input_kind,
            "prompt": record.prompt,
            "license": record.license,
            "notes": record.notes,
            "width": record.width,
            "height": record.height,
            "target_sha256": record.target_sha256,
            "input_sha256": record.input_sha256,
            "created_at": record.created_at,
        },
    )
    return record


def ingest_software_candidate_pair(
    input_path: str | Path,
    target_path: str | Path,
    *,
    category: str = PIXEL_REFINER_DEFAULT_FEEDBACK_CATEGORY,
    prompt: str = "",
    notes: str = "",
    source_id: str = PIXEL_REFINER_FEEDBACK_SOURCE_ID,
    skip_existing: bool = True,
) -> PixelRefinerPairIngestResult:
    input_source = Path(input_path)
    target_source = Path(target_path)
    if not input_source.is_file():
        raise FileNotFoundError(input_source)
    if not target_source.is_file():
        raise FileNotFoundError(target_source)
    input_size = _read_png_size(input_source)
    target_size = _read_png_size(target_source)
    if input_size != target_size:
        raise ValueError(
            "软件反馈训练对的输入图和目标图尺寸必须一致。"
            f" 当前输入是 {input_size[0]}x{input_size[1]}，目标是 {target_size[0]}x{target_size[1]}。"
        )
    category = _safe_name(category or PIXEL_REFINER_DEFAULT_FEEDBACK_CATEGORY)
    source_id = _safe_name(source_id or PIXEL_REFINER_FEEDBACK_SOURCE_ID)
    ensure_feedback_source_record(source_id=source_id, category=category)
    input_sha = _sha256_file(input_source)
    target_sha = _sha256_file(target_source)
    if skip_existing:
        existing = _find_pair_by_hashes(target_sha=target_sha, input_sha=input_sha)
        if existing is not None:
            return PixelRefinerPairIngestResult(record=existing, created=False)

    original_note = (
        f"original_input={input_source.resolve()}\n"
        f"original_target={target_source.resolve()}"
    )
    combined_notes = "\n".join(part for part in (notes.strip(), original_note) if part)
    target_copy = ingest_target_image(
        target_source,
        source_id=source_id,
        category=category,
        title=target_source.stem,
        license=PIXEL_REFINER_FEEDBACK_LICENSE,
        prompt=prompt,
        notes=combined_notes,
    )
    input_copy = ingest_generated_input_image(
        input_source,
        source_id=source_id,
        category=category,
        input_kind=PIXEL_REFINER_SOFTWARE_INPUT_KIND,
        prompt=prompt,
        notes=combined_notes,
    )
    record = build_pair_record(
        target_path=target_copy,
        input_path=input_copy,
        source_id=source_id,
        category=category,
        input_kind=PIXEL_REFINER_SOFTWARE_INPUT_KIND,
        prompt=prompt,
        license=PIXEL_REFINER_FEEDBACK_LICENSE,
        notes=combined_notes,
    )
    return PixelRefinerPairIngestResult(record=record, created=True)


def ensure_feedback_source_record(
    *,
    source_id: str = PIXEL_REFINER_FEEDBACK_SOURCE_ID,
    category: str = PIXEL_REFINER_DEFAULT_FEEDBACK_CATEGORY,
) -> None:
    add_source_record(
        PixelRefinerSourceRecord(
            source_id=_safe_name(source_id or PIXEL_REFINER_FEEDBACK_SOURCE_ID),
            title="GameDesigner feedback pairs",
            author="Local GameDesigner user",
            url="local://gamedesigner-feedback",
            license=PIXEL_REFINER_FEEDBACK_LICENSE,
            license_url="",
            ai_training_allowed=True,
            category=_safe_name(category or PIXEL_REFINER_DEFAULT_FEEDBACK_CATEGORY),
            notes="Pairs captured from real GameDesigner software candidates and user-selected true pixel targets.",
        )
    )


def load_pair_records(path: str | Path | None = None) -> list[PixelRefinerPairRecord]:
    index = Path(path) if path else index_path()
    if not index.exists():
        return []
    records: list[PixelRefinerPairRecord] = []
    with index.open("r", encoding="utf-8") as file:
        for line in file:
            raw = line.strip()
            if not raw:
                continue
            try:
                data = json.loads(raw)
            except json.JSONDecodeError:
                continue
            record = _pair_record_from_dict(data)
            if record is not None:
                records.append(record)
    return records


def _find_pair_by_hashes(*, target_sha: str, input_sha: str) -> PixelRefinerPairRecord | None:
    for record in load_pair_records():
        if record.target_sha256 == target_sha and record.input_sha256 == input_sha:
            return record
    return None


def summarize_dataset() -> dict[str, Any]:
    records = load_pair_records()
    target_count = len(list(targets_dir().rglob("*.png"))) if targets_dir().exists() else 0
    generated_input_count = len(list(generated_inputs_dir().rglob("*.png"))) if generated_inputs_dir().exists() else 0
    external_input_count = len(list(inputs_dir().rglob("*.png"))) if inputs_dir().exists() else 0
    input_count = generated_input_count + external_input_count
    pair_count = len(records)
    categories = sorted({record.category for record in records if record.category})
    return {
        "dataset_dir": str(dataset_dir()),
        "targets": target_count,
        "inputs": input_count,
        "generated_inputs": generated_input_count,
        "external_inputs": external_input_count,
        "pairs": pair_count,
        "categories": categories,
        "source_records": len(load_source_records()),
    }


def _pair_record_from_dict(raw: Any) -> PixelRefinerPairRecord | None:
    if not isinstance(raw, dict):
        return None
    try:
        return PixelRefinerPairRecord(
            pair_id=str(raw.get("pair_id") or ""),
            target_path=Path(str(raw.get("target_path") or "")),
            input_path=Path(str(raw.get("input_path") or "")),
            source_id=str(raw.get("source_id") or ""),
            category=str(raw.get("category") or ""),
            target_sha256=str(raw.get("target_sha256") or ""),
            input_sha256=str(raw.get("input_sha256") or ""),
            width=int(raw.get("width") or 0),
            height=int(raw.get("height") or 0),
            created_at=str(raw.get("created_at") or ""),
            input_kind=str(raw.get("input_kind") or ""),
            prompt=str(raw.get("prompt") or ""),
            license=str(raw.get("license") or ""),
            notes=str(raw.get("notes") or ""),
        )
    except (TypeError, ValueError):
        return None


def _append_index(record: PixelRefinerPairRecord) -> None:
    with index_path().open("a", encoding="utf-8") as file:
        file.write(
            json.dumps(
                {
                    "pair_id": record.pair_id,
                    "target_path": str(record.target_path),
                    "input_path": str(record.input_path),
                    "source_id": record.source_id,
                    "category": record.category,
                    "target_sha256": record.target_sha256,
                    "input_sha256": record.input_sha256,
                    "width": record.width,
                    "height": record.height,
                    "created_at": record.created_at,
                    "input_kind": record.input_kind,
                    "prompt": record.prompt,
                    "license": record.license,
                    "notes": record.notes,
                },
                ensure_ascii=False,
            )
            + "\n"
        )


def _write_source_records(records: list[PixelRefinerSourceRecord], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as file:
        writer = csv.DictWriter(
            file,
            fieldnames=[
                "source_id",
                "title",
                "author",
                "url",
                "license",
                "license_url",
                "ai_training_allowed",
                "category",
                "notes",
            ],
        )
        writer.writeheader()
        for record in records:
            writer.writerow(
                {
                    "source_id": record.source_id,
                    "title": record.title,
                    "author": record.author,
                    "url": record.url,
                    "license": record.license,
                    "license_url": record.license_url,
                    "ai_training_allowed": "1" if record.ai_training_allowed else "0",
                    "category": record.category,
                    "notes": record.notes,
                }
            )


def _write_metadata(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _copy_png_asset(source: Path, target: Path) -> None:
    _read_png_size(source)
    if target.resolve() == source.resolve():
        return
    shutil.copy2(source, target)


def _read_png_size(path: Path) -> tuple[int, int]:
    try:
        with Image.open(path) as loaded:
            image = ImageOps.exif_transpose(loaded)
            return image.width, image.height
    except OSError as exc:
        raise ValueError(f"无法读取 PNG：{path}") from exc


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as file:
        for chunk in iter(lambda: file.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _safe_name(value: str) -> str:
    cleaned = "".join(char if char.isalnum() or char in {"-", "_"} else "_" for char in str(value or "").strip())
    return cleaned.strip("_") or "item"


def _coerce_bool(value: Any) -> bool:
    text = str(value or "").strip().lower()
    return text in {"1", "true", "yes", "y", "on"}


def _timestamp() -> str:
    return datetime.now().strftime("%Y%m%d_%H%M%S")
