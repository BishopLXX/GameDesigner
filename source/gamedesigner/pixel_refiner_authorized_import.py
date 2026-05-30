from __future__ import annotations

import hashlib
import json
import urllib.parse
from dataclasses import dataclass
from datetime import datetime
from io import BytesIO
from pathlib import Path
from typing import Any, Iterable

from PIL import Image, ImageOps, ImageSequence

from .pixel_refiner_dataset import (
    PixelRefinerSourceRecord,
    add_source_record,
    dataset_dir,
    targets_dir,
)
from .pixel_refiner_pair_generation import build_pairs_from_targets
from .pixel_site_downloader import build_manifest_records, crawl_site, download_images, write_jsonl


IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".webp", ".bmp", ".gif"}


@dataclass(frozen=True)
class AuthorizedImportConfig:
    input_dir: Path
    source_id: str
    title: str = ""
    author: str = ""
    url: str = ""
    license: str = "User-authorized local training source"
    license_url: str = ""
    rights_basis: str = ""
    category: str = "auto"
    min_width: int = 64
    min_height: int = 64
    min_area: int = 4096
    max_images: int = 0
    max_gif_frames: int = 24
    build_pairs: bool = False
    dry_run: bool = False


@dataclass(frozen=True)
class AuthorizedSiteCrawlConfig:
    start_urls: tuple[str, ...]
    source_id: str
    title: str = ""
    author: str = ""
    url: str = ""
    license: str = "User-authorized website training source"
    license_url: str = ""
    rights_basis: str = ""
    page_host: str = ""
    asset_host_contains: str = ""
    asset_path_contains: str = ""
    max_pages: int = 500
    max_workers: int = 8
    timeout: int = 30
    retries: int = 3
    delay: float = 0.05
    min_width: int = 64
    min_height: int = 64
    min_area: int = 4096
    allow_opaque_targets: bool = True
    extract_gif_frames: bool = True
    max_gif_frames: int = 24
    overwrite: bool = False
    build_pairs: bool = False
    dry_run: bool = False


def import_authorized_targets(config: AuthorizedImportConfig) -> dict[str, Any]:
    source_id = _safe_name(config.source_id)
    if not source_id:
        raise ValueError("source_id is required.")
    input_dir = Path(config.input_dir)
    if not input_dir.is_dir():
        raise FileNotFoundError(input_dir)

    if not config.dry_run:
        add_source_record(
            PixelRefinerSourceRecord(
                source_id=source_id,
                title=config.title or source_id,
                author=config.author,
                url=config.url,
                license=config.license,
                license_url=config.license_url,
                ai_training_allowed=True,
                category=config.category if config.category != "auto" else "",
                notes=f"rights_basis={config.rights_basis}".strip(),
            )
        )

    manifest_rows: list[dict[str, Any]] = []
    imported_paths: list[Path] = []
    stats: dict[str, Any] = {
        "ok": True,
        "input_dir": str(input_dir),
        "dataset_dir": str(dataset_dir()),
        "source_id": source_id,
        "files_seen": 0,
        "images_seen": 0,
        "targets_imported": 0,
        "rejected": 0,
        "build_pairs": {},
        "manifest": "",
        "dry_run": bool(config.dry_run),
        "errors": [],
    }

    for image_path in _iter_image_files(input_dir):
        if config.max_images > 0 and stats["targets_imported"] >= config.max_images:
            break
        stats["files_seen"] += 1
        try:
            frames = _load_candidate_frames(image_path, max_gif_frames=max(1, int(config.max_gif_frames)))
        except Exception as exc:
            stats["errors"].append({"path": str(image_path), "error": f"{type(exc).__name__}: {exc}"})
            continue
        for frame_index, image in frames:
            if config.max_images > 0 and stats["targets_imported"] >= config.max_images:
                break
            stats["images_seen"] += 1
            decision = _accept_image(
                image,
                min_width=max(1, int(config.min_width)),
                min_height=max(1, int(config.min_height)),
                min_area=max(1, int(config.min_area)),
            )
            category = _category_for_image(image, forced=config.category)
            row: dict[str, Any] = {
                "source_path": str(image_path),
                "frame_index": frame_index,
                "width": image.width,
                "height": image.height,
                "category": category,
                "accepted": decision["accepted"],
                "reason": decision["reason"],
            }
            if not decision["accepted"]:
                stats["rejected"] += 1
                manifest_rows.append(row)
                continue
            target_path = _target_path_for_image(image, source_id=source_id, category=category, source_path=image_path, frame_index=frame_index)
            row["target_path"] = str(target_path)
            if not config.dry_run:
                target_path.parent.mkdir(parents=True, exist_ok=True)
                _save_png(image, target_path)
                metadata_path = target_path.with_name(f"{target_path.stem}_metadata.json")
                metadata_path.write_text(
                    json.dumps(
                        {
                            "kind": "target",
                            "source_id": source_id,
                            "title": image_path.stem,
                            "category": category,
                            "license": config.license,
                            "license_url": config.license_url,
                            "rights_basis": config.rights_basis,
                            "original_path": str(image_path.resolve()),
                            "frame_index": frame_index,
                            "width": image.width,
                            "height": image.height,
                            "created_at": datetime.now().isoformat(timespec="seconds"),
                        },
                        ensure_ascii=False,
                        indent=2,
                    )
                    + "\n",
                    encoding="utf-8",
                )
                imported_paths.append(target_path)
            stats["targets_imported"] += 1
            manifest_rows.append(row)

    if not config.dry_run:
        manifest_path = dataset_dir() / "manifests" / f"{source_id}_authorized_import_{datetime.now().strftime('%Y%m%d_%H%M%S')}.jsonl"
        manifest_path.parent.mkdir(parents=True, exist_ok=True)
        with manifest_path.open("w", encoding="utf-8") as file:
            for row in manifest_rows:
                file.write(json.dumps(row, ensure_ascii=False) + "\n")
        stats["manifest"] = str(manifest_path)
        if config.build_pairs and imported_paths:
            stats["build_pairs"] = build_pairs_from_targets(
                targets_dir() / source_id,
                source_id=source_id,
                skip_existing=True,
            )
    else:
        stats["sample_targets"] = [str(row.get("target_path") or "") for row in manifest_rows if row.get("accepted")][:25]

    stats["errors"] = stats["errors"][:100]
    return stats


def crawl_authorized_site(config: AuthorizedSiteCrawlConfig) -> dict[str, Any]:
    source_id = _safe_name(config.source_id)
    if not source_id:
        raise ValueError("source_id is required.")
    start_urls = tuple(str(url or "").strip() for url in config.start_urls if str(url or "").strip())
    if not start_urls:
        raise ValueError("At least one start URL is required.")
    page_host = str(config.page_host or "").strip() or _host_from_url(start_urls[0])
    if not page_host:
        raise ValueError("page_host could not be inferred from start URL.")

    if not config.dry_run:
        add_source_record(
            PixelRefinerSourceRecord(
                source_id=source_id,
                title=config.title or source_id,
                author=config.author,
                url=config.url or start_urls[0],
                license=config.license,
                license_url=config.license_url,
                ai_training_allowed=True,
                category="",
                notes=f"rights_basis={config.rights_basis}".strip(),
            )
        )

    crawl = crawl_site(
        start_urls,
        page_host=page_host,
        asset_host_contains=str(config.asset_host_contains or ""),
        asset_path_contains=str(config.asset_path_contains or ""),
        max_pages=max(1, int(config.max_pages)),
        timeout=max(1, int(config.timeout)),
        retries=max(1, int(config.retries)),
        delay=max(0.0, float(config.delay)),
        generic_pages=True,
    )
    image_pages: dict[str, set[str]] = crawl["image_pages"]
    page_records: list[dict[str, Any]] = crawl["page_records"]
    stats: dict[str, Any] = {
        "ok": True,
        "source_id": source_id,
        "start_urls": list(start_urls),
        "page_host": page_host,
        "pages": len(page_records),
        "discovered_images": len(image_pages),
        "downloaded_records": 0,
        "accepted_images": 0,
        "target_png_files": 0,
        "failed": 0,
        "raw_dir": str(dataset_dir() / "raw" / source_id),
        "target_dir": str(targets_dir() / source_id),
        "manifest": "",
        "page_manifest": "",
        "build_pairs": {},
        "dry_run": bool(config.dry_run),
    }
    if config.dry_run:
        stats["first_images"] = sorted(image_pages)[:50]
        return stats

    raw_dir = dataset_dir() / "raw" / source_id
    target_dir = targets_dir() / source_id
    rejected_dir = dataset_dir() / "rejected" / source_id
    downloads = download_images(
        image_pages,
        raw_dir=raw_dir,
        timeout=max(1, int(config.timeout)),
        retries=max(1, int(config.retries)),
        max_workers=max(1, int(config.max_workers)),
        overwrite=bool(config.overwrite),
    )
    records = build_manifest_records(
        downloads,
        target_dir=target_dir,
        rejected_dir=rejected_dir,
        min_width=max(1, int(config.min_width)),
        min_height=max(1, int(config.min_height)),
        min_area=max(1, int(config.min_area)),
        require_alpha=not bool(config.allow_opaque_targets),
        export_targets=True,
        extract_gif_frames=bool(config.extract_gif_frames),
        max_gif_frames=max(1, int(config.max_gif_frames)),
    )

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    manifest_path = dataset_dir() / "manifests" / f"{source_id}_authorized_site_images_{timestamp}.jsonl"
    page_manifest_path = dataset_dir() / "manifests" / f"{source_id}_authorized_site_pages_{timestamp}.jsonl"
    write_jsonl(manifest_path, records)
    write_jsonl(page_manifest_path, page_records)
    accepted = [record for record in records if record.get("accepted")]
    target_files = sum(len(record.get("target_paths") or []) for record in records)
    failed = [record for record in records if record.get("download_status") != "ok"]
    stats.update(
        {
            "downloaded_records": len(records),
            "accepted_images": len(accepted),
            "target_png_files": target_files,
            "failed": len(failed),
            "manifest": str(manifest_path),
            "page_manifest": str(page_manifest_path),
        }
    )
    if config.build_pairs and target_files > 0:
        stats["build_pairs"] = build_pairs_from_targets(
            target_dir,
            source_id=source_id,
            skip_existing=True,
        )
    return stats


def _iter_image_files(root: Path) -> Iterable[Path]:
    return sorted(path for path in root.rglob("*") if path.is_file() and path.suffix.lower() in IMAGE_EXTENSIONS)


def _load_candidate_frames(path: Path, *, max_gif_frames: int) -> list[tuple[int, Image.Image]]:
    with Image.open(path) as loaded:
        loaded = ImageOps.exif_transpose(loaded)
        frames: list[tuple[int, Image.Image]] = []
        if getattr(loaded, "is_animated", False):
            for index, frame in enumerate(ImageSequence.Iterator(loaded)):
                if index >= max_gif_frames:
                    break
                frames.append((index, frame.convert("RGBA")))
        else:
            frames.append((0, loaded.convert("RGBA")))
    return frames


def _accept_image(image: Image.Image, *, min_width: int, min_height: int, min_area: int) -> dict[str, Any]:
    if image.width < min_width or image.height < min_height:
        return {"accepted": False, "reason": "too_small"}
    if image.width * image.height < min_area:
        return {"accepted": False, "reason": "area_too_small"}
    alpha = image.getchannel("A")
    if alpha.getbbox() is None:
        return {"accepted": False, "reason": "fully_transparent"}
    return {"accepted": True, "reason": "accepted"}


def _category_for_image(image: Image.Image, *, forced: str) -> str:
    if forced and forced != "auto":
        return _safe_name(forced)
    width = max(1, image.width)
    height = max(1, image.height)
    if width > height * 1.25:
        return "side_scroller_action_character"
    if height > width * 1.20:
        return "character_portrait"
    return "character_portrait" if max(width, height) >= 384 else "character_sprite"


def _target_path_for_image(
    image: Image.Image,
    *,
    source_id: str,
    category: str,
    source_path: Path,
    frame_index: int,
) -> Path:
    digest = _image_sha256(image)[:12]
    frame_suffix = f"_frame{frame_index:03d}" if frame_index > 0 else ""
    filename = f"{_safe_name(source_path.stem)}{frame_suffix}_{digest}.png"
    return targets_dir() / source_id / _safe_name(category) / filename


def _image_sha256(image: Image.Image) -> str:
    buffer = BytesIO()
    image.save(buffer, format="PNG", optimize=True)
    return hashlib.sha256(buffer.getvalue()).hexdigest()


def _save_png(image: Image.Image, path: Path) -> None:
    image.convert("RGBA").save(path, format="PNG", optimize=True)


def _safe_name(value: str) -> str:
    cleaned = "".join(char if char.isalnum() or char in {"-", "_"} else "_" for char in str(value or "").strip())
    return cleaned.strip("_") or "item"


def _host_from_url(url: str) -> str:
    parsed = urllib.parse.urlparse(str(url or "").strip())
    return parsed.netloc
