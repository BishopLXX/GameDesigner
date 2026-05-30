from __future__ import annotations

import argparse
import hashlib
import html
import json
import re
import time
import urllib.parse
import urllib.request
import zipfile
from dataclasses import dataclass
from datetime import datetime
from io import BytesIO
from pathlib import Path
from typing import Any, Iterable

import numpy as np
from PIL import Image, ImageOps, ImageSequence


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "source"
import sys

if str(SOURCE) not in sys.path:
    sys.path.insert(0, str(SOURCE))

from gamedesigner.pixel_refiner_dataset import (  # noqa: E402
    PixelRefinerSourceRecord,
    add_source_record,
    dataset_dir,
    ensure_dataset_dirs,
    targets_dir,
)


SOURCE_ID = "opengameart_cc0_characters"
LICENSE = "CC0"
LICENSE_URL = "https://creativecommons.org/publicdomain/zero/1.0/"
USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) GameDesignerPixelDataset/2.1"
DEFAULT_SEEDS = (
    "https://opengameart.org/content/rpg-character-sprites",
    "https://opengameart.org/content/spritesheet-of-a-man-cc0",
    "https://opengameart.org/content/pixel-character-with-gun",
    "https://opengameart.org/content/128x128-2d-zombies-spritesheet",
    "https://opengameart.org/content/urban-character-pack",
    "https://opengameart.org/content/platformer-art-pixel-redux",
    "https://opengameart.org/content/pixel-phoenix",
    "https://opengameart.org/content/enemy-slave",
    "https://opengameart.org/content/mark-2d-adventure-game-sprite",
    "https://opengameart.org/content/8-bit-character",
    "https://opengameart.org/content/2d-rpg-character-walk-spritesheet",
    "https://opengameart.org/content/cc0-walk-cycles",
    "https://opengameart.org/content/cc0-2d-platform-creatures-characters",
)
ATTR_RE = re.compile(r"""(?is)\b(?:href|src)\s*=\s*(['"])(.*?)\1""")
TITLE_RE = re.compile(r"(?is)<h1[^>]*>(.*?)</h1>")
FILE_EXTENSIONS = {".png", ".gif", ".zip"}
COMMON_CELLS = (
    (16, 16),
    (18, 20),
    (24, 24),
    (24, 32),
    (32, 32),
    (32, 48),
    (32, 64),
    (48, 48),
    (48, 64),
    (64, 64),
    (64, 96),
    (64, 128),
    (70, 70),
    (80, 80),
    (96, 96),
    (128, 128),
)
PAGE_INCLUDE_TERMS = {
    "animated",
    "character",
    "characters",
    "enemy",
    "enemies",
    "girl",
    "hero",
    "knight",
    "man",
    "monster",
    "rpg",
    "sprite",
    "sprites",
    "spritesheet",
    "walk",
    "zombie",
}
PAGE_REJECT_TERMS = {
    "background",
    "font",
    "icon",
    "icons",
    "item",
    "items",
    "music",
    "parallax",
    "sfx",
    "sound",
    "terrain",
    "tile",
    "tiles",
    "tileset",
    "ui",
    "vector",
}


@dataclass(frozen=True)
class PageAsset:
    page_url: str
    title: str
    file_url: str


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Collect CC0 OpenGameArt pixel character frames into Pixel Refiner targets.")
    parser.add_argument("--max-targets", type=int, default=1000)
    parser.add_argument("--max-pages", type=int, default=220)
    parser.add_argument("--max-depth", type=int, default=1)
    parser.add_argument("--timeout", type=int, default=40)
    parser.add_argument("--delay", type=float, default=0.15)
    parser.add_argument("--overwrite", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--seed", action="append", default=[])
    args = parser.parse_args(argv)
    stats = collect_opengameart_cc0_character_frames(
        seeds=tuple(args.seed) or DEFAULT_SEEDS,
        max_targets=max(1, int(args.max_targets)),
        max_pages=max(1, int(args.max_pages)),
        max_depth=max(0, int(args.max_depth)),
        timeout=max(1, int(args.timeout)),
        delay=max(0.0, float(args.delay)),
        overwrite=bool(args.overwrite),
        dry_run=bool(args.dry_run),
    )
    print(json.dumps(stats, ensure_ascii=False, indent=2))
    return 0


def collect_opengameart_cc0_character_frames(
    *,
    seeds: Iterable[str],
    max_targets: int,
    max_pages: int,
    max_depth: int,
    timeout: int,
    delay: float,
    overwrite: bool,
    dry_run: bool,
) -> dict[str, Any]:
    ensure_dataset_dirs()
    raw_dir = dataset_dir() / "raw" / SOURCE_ID
    manifest_dir = dataset_dir() / "manifests"
    source_dir = dataset_dir() / "sources"
    target_root = targets_dir() / SOURCE_ID / "side_scroller_action_character"
    if not dry_run:
        for folder in (raw_dir, manifest_dir, source_dir, target_root):
            folder.mkdir(parents=True, exist_ok=True)
        add_source_record(
            PixelRefinerSourceRecord(
                source_id=SOURCE_ID,
                title="OpenGameArt CC0 pixel character frames",
                author="OpenGameArt contributors",
                url="; ".join(seeds),
                license=LICENSE,
                license_url=LICENSE_URL,
                ai_training_allowed=True,
                category="side_scroller_action_character,character_sprite",
                notes=(
                    "Collector imports only pages whose metadata includes CC0 and character/sprite terms. "
                    "Frames are extracted from PNG/GIF/ZIP files and filtered for hard-alpha low-palette pixel art."
                ),
            )
        )

    page_assets, crawled_pages = discover_page_assets(
        seeds=tuple(seeds),
        max_pages=max_pages,
        max_depth=max_depth,
        timeout=timeout,
        delay=delay,
    )
    if dry_run:
        return {
            "ok": True,
            "dry_run": True,
            "source_id": SOURCE_ID,
            "pages_crawled": len(crawled_pages),
            "assets_discovered": len(page_assets),
            "first_assets": [asset.__dict__ for asset in page_assets[:20]],
        }

    records: list[dict[str, Any]] = []
    target_hashes = existing_target_hashes(target_root)
    accepted_count = 0
    for asset in page_assets:
        if accepted_count >= max_targets:
            break
        try:
            record = process_page_asset(
                asset,
                raw_dir=raw_dir,
                target_root=target_root,
                max_remaining=max_targets - accepted_count,
                timeout=timeout,
                overwrite=overwrite,
                target_hashes=target_hashes,
            )
            accepted_count += int(record.get("accepted_frames", 0))
            records.append(record)
        except Exception as exc:
            records.append(
                {
                    "page_url": asset.page_url,
                    "title": asset.title,
                    "file_url": asset.file_url,
                    "status": "error",
                    "accepted_frames": 0,
                    "error": f"{type(exc).__name__}: {exc}",
                }
            )
        if delay:
            time.sleep(delay)

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    manifest_path = manifest_dir / f"{SOURCE_ID}_frames_{timestamp}.jsonl"
    latest_manifest_path = manifest_dir / f"{SOURCE_ID}_frames_latest.jsonl"
    write_jsonl(manifest_path, records)
    write_jsonl(latest_manifest_path, records)
    source_path = source_dir / f"{SOURCE_ID}.json"
    source_path.write_text(
        json.dumps(
            {
                "source_id": SOURCE_ID,
                "license": LICENSE,
                "license_url": LICENSE_URL,
                "downloaded_at": datetime.now().isoformat(timespec="seconds"),
                "seeds": list(seeds),
                "pages_crawled": len(crawled_pages),
                "raw_dir": str(raw_dir),
                "target_dir": str(target_root),
                "manifest": str(latest_manifest_path),
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    return {
        "ok": True,
        "source_id": SOURCE_ID,
        "pages_crawled": len(crawled_pages),
        "assets_discovered": len(page_assets),
        "files_processed": len(records),
        "accepted_frames": accepted_count,
        "target_dir": str(target_root),
        "raw_dir": str(raw_dir),
        "manifest": str(latest_manifest_path),
        "errors": [record for record in records if record.get("status") == "error"][:20],
    }


def discover_page_assets(
    *,
    seeds: tuple[str, ...],
    max_pages: int,
    max_depth: int,
    timeout: int,
    delay: float,
) -> tuple[list[PageAsset], set[str]]:
    queue: list[tuple[str, int]] = [(normalize_url(seed, "https://opengameart.org"), 0) for seed in seeds]
    seen_pages: set[str] = set()
    seen_files: set[str] = set()
    assets: list[PageAsset] = []
    while queue and len(seen_pages) < max_pages:
        page_url, depth = queue.pop(0)
        page_url = strip_fragment(page_url)
        if page_url in seen_pages or not is_opengameart_page(page_url):
            continue
        seen_pages.add(page_url)
        try:
            page_html = fetch_text(page_url, timeout=timeout)
        except Exception:
            continue
        title = extract_title(page_html) or page_url.rsplit("/", 1)[-1]
        text = html_to_text(page_html).lower()
        if "cc0" in text and page_is_character_like(title, text):
            for file_url in extract_file_urls(page_html, page_url):
                if file_url in seen_files:
                    continue
                seen_files.add(file_url)
                assets.append(PageAsset(page_url=page_url, title=title, file_url=file_url))
        if depth < max_depth:
            for link in extract_internal_art_links(page_html, page_url):
                if link not in seen_pages and all(link != queued[0] for queued in queue):
                    queue.append((link, depth + 1))
        if delay:
            time.sleep(delay)
    return assets, seen_pages


def process_page_asset(
    asset: PageAsset,
    *,
    raw_dir: Path,
    target_root: Path,
    max_remaining: int,
    timeout: int,
    overwrite: bool,
    target_hashes: set[str],
) -> dict[str, Any]:
    data = fetch_bytes(asset.file_url, timeout=timeout, referer=asset.page_url)
    suffix = Path(urllib.parse.urlparse(asset.file_url).path).suffix.lower() or ".bin"
    raw_name = f"{safe_name(asset.title)}_{short_hash(asset.file_url.encode('utf-8'))}{suffix}"
    raw_path = raw_dir / raw_name
    raw_path.write_bytes(data)
    images = image_items_from_download(data, suffix=suffix, title=asset.title)
    accepted_paths: list[str] = []
    rejected_frames = 0
    frame_index = 0
    for item_name, image in images:
        for frame in extract_candidate_frames(image):
            if len(accepted_paths) >= max_remaining:
                break
            frame_index += 1
            normalized = normalize_frame(frame)
            if normalized is None:
                rejected_frames += 1
                continue
            digest = image_digest(normalized)
            if digest in target_hashes and not overwrite:
                rejected_frames += 1
                continue
            target_name = f"{safe_name(asset.title)}_{safe_name(Path(item_name).stem)}_{frame_index:04d}_{digest[:10]}.png"
            target_path = target_root / target_name
            target_path.parent.mkdir(parents=True, exist_ok=True)
            normalized.save(target_path, format="PNG", optimize=True)
            target_hashes.add(digest)
            accepted_paths.append(str(target_path))
        if len(accepted_paths) >= max_remaining:
            break
    return {
        "status": "ok",
        "page_url": asset.page_url,
        "title": asset.title,
        "file_url": asset.file_url,
        "raw_path": str(raw_path),
        "download_bytes": len(data),
        "images_seen": len(images),
        "accepted_frames": len(accepted_paths),
        "rejected_frames": rejected_frames,
        "target_paths": accepted_paths[:50],
    }


def image_items_from_download(data: bytes, *, suffix: str, title: str) -> list[tuple[str, Image.Image]]:
    images: list[tuple[str, Image.Image]] = []
    if suffix == ".zip":
        with zipfile.ZipFile(BytesIO(data)) as archive:
            for name in archive.namelist():
                if Path(name).suffix.lower() not in {".png", ".gif"}:
                    continue
                try:
                    item_data = archive.read(name)
                    images.extend(load_image_frames(item_data, name=name))
                except Exception:
                    continue
        return images
    return load_image_frames(data, name=title + suffix)


def load_image_frames(data: bytes, *, name: str) -> list[tuple[str, Image.Image]]:
    result: list[tuple[str, Image.Image]] = []
    with Image.open(BytesIO(data)) as loaded:
        loaded = ImageOps.exif_transpose(loaded)
        if getattr(loaded, "is_animated", False):
            for index, frame in enumerate(ImageSequence.Iterator(loaded)):
                result.append((f"{name}_frame_{index:03d}", frame.convert("RGBA")))
        else:
            result.append((name, loaded.convert("RGBA")))
    return result


def extract_candidate_frames(image: Image.Image) -> list[Image.Image]:
    rgba = image.convert("RGBA")
    if not has_visible_pixels(rgba):
        return []
    frames: list[Image.Image] = []
    frames.extend(segment_by_transparent_gutters(rgba))
    frames.extend(slice_common_grids(rgba))
    if not frames and max(rgba.size) <= 512:
        frames.append(rgba)
    dedup: dict[str, Image.Image] = {}
    for frame in frames:
        cropped = crop_to_alpha(frame)
        if cropped is None:
            continue
        if min(cropped.size) < 8 or max(cropped.size) < 14:
            continue
        if max(cropped.size) > 256:
            continue
        dedup.setdefault(image_digest(cropped), cropped)
    return list(dedup.values())


def segment_by_transparent_gutters(image: Image.Image) -> list[Image.Image]:
    alpha = np.asarray(image.getchannel("A"), dtype=np.uint8)
    mask = alpha > 10
    row_ranges = bool_ranges(mask.any(axis=1), min_gap=1)
    frames: list[Image.Image] = []
    for top, bottom in row_ranges:
        if bottom - top > 256:
            continue
        submask = mask[top:bottom, :]
        for left, right in bool_ranges(submask.any(axis=0), min_gap=1):
            if right - left > 256:
                continue
            frames.append(image.crop((left, top, right, bottom)))
    return frames


def slice_common_grids(image: Image.Image) -> list[Image.Image]:
    width, height = image.size
    if width < 32 or height < 32:
        return []
    frames: list[Image.Image] = []
    alpha = np.asarray(image.getchannel("A"), dtype=np.uint8)
    for cell_w, cell_h in COMMON_CELLS:
        if width % cell_w != 0 or height % cell_h != 0:
            continue
        cell_count = (width // cell_w) * (height // cell_h)
        if cell_count < 2 or cell_count > 1500:
            continue
        nonempty = 0
        cells: list[Image.Image] = []
        for top in range(0, height, cell_h):
            for left in range(0, width, cell_w):
                cell_alpha = alpha[top : top + cell_h, left : left + cell_w]
                if int((cell_alpha > 10).sum()) < 12:
                    continue
                nonempty += 1
                cells.append(image.crop((left, top, left + cell_w, top + cell_h)))
        if nonempty >= 3:
            frames.extend(cells)
    return frames


def normalize_frame(frame: Image.Image) -> Image.Image | None:
    cropped = crop_to_alpha(frame.convert("RGBA"))
    if cropped is None:
        return None
    if not looks_like_pixel_art(cropped):
        return None
    crop_w, crop_h = cropped.size
    canvas_w, canvas_h = (256, 384) if crop_h / max(1, crop_w) > 1.35 else (256, 256)
    max_w = canvas_w - 32
    max_h = canvas_h - 32
    scale = max(1, min(max_w // crop_w, max_h // crop_h))
    scale = min(scale, 12)
    out_w = max(1, crop_w * scale)
    out_h = max(1, crop_h * scale)
    if out_w > max_w or out_h > max_h:
        ratio = min(max_w / crop_w, max_h / crop_h)
        out_w = max(1, int(round(crop_w * ratio)))
        out_h = max(1, int(round(crop_h * ratio)))
        resample = Image.Resampling.NEAREST
    else:
        resample = Image.Resampling.NEAREST
    scaled = cropped.resize((out_w, out_h), resample)
    canvas = Image.new("RGBA", (canvas_w, canvas_h), (0, 0, 0, 0))
    canvas.alpha_composite(scaled, ((canvas_w - out_w) // 2, canvas_h - out_h - 16))
    return canvas


def looks_like_pixel_art(image: Image.Image) -> bool:
    rgba = image.convert("RGBA")
    array = np.asarray(rgba, dtype=np.uint8)
    alpha = array[:, :, 3]
    visible = alpha > 10
    visible_count = int(visible.sum())
    if visible_count < 40:
        return False
    soft_alpha = ((alpha > 10) & (alpha < 245)).sum()
    if soft_alpha / max(1, visible_count) > 0.12:
        return False
    rgb = array[:, :, :3][visible]
    if rgb.size <= 0:
        return False
    quantized = (rgb // 8).astype(np.uint8)
    unique_colors = len({tuple(item) for item in quantized.reshape(-1, 3)})
    if unique_colors > 180:
        return False
    return True


def crop_to_alpha(image: Image.Image) -> Image.Image | None:
    alpha = np.asarray(image.getchannel("A"), dtype=np.uint8)
    ys, xs = np.where(alpha > 10)
    if len(xs) == 0 or len(ys) == 0:
        return None
    left, right = int(xs.min()), int(xs.max()) + 1
    top, bottom = int(ys.min()), int(ys.max()) + 1
    padding = 2
    left = max(0, left - padding)
    top = max(0, top - padding)
    right = min(image.width, right + padding)
    bottom = min(image.height, bottom + padding)
    return image.crop((left, top, right, bottom))


def bool_ranges(values: np.ndarray, *, min_gap: int) -> list[tuple[int, int]]:
    ranges: list[tuple[int, int]] = []
    start: int | None = None
    gap = 0
    last_true = -1
    for index, value in enumerate(bool(item) for item in values):
        if value:
            if start is None:
                start = index
            gap = 0
            last_true = index
            continue
        if start is not None:
            gap += 1
            if gap > min_gap:
                ranges.append((start, last_true + 1))
                start = None
                gap = 0
    if start is not None:
        ranges.append((start, last_true + 1))
    return ranges


def page_is_character_like(title: str, text: str) -> bool:
    combined = f"{title} {text}".lower()
    if any(term in combined for term in PAGE_REJECT_TERMS) and not any(
        term in combined for term in {"character", "sprite", "walk", "enemy", "monster"}
    ):
        return False
    return any(term in combined for term in PAGE_INCLUDE_TERMS)


def extract_internal_art_links(page_html: str, base_url: str) -> list[str]:
    links: list[str] = []
    for raw in extract_attr_urls(page_html):
        url = strip_fragment(normalize_url(raw, base_url))
        parsed = urllib.parse.urlparse(url)
        if parsed.netloc not in {"opengameart.org", "lpc.opengameart.org"}:
            continue
        if re.search(r"/(?:content|node)/[^?#]+", parsed.path):
            links.append(url)
    return sorted(set(links))


def extract_file_urls(page_html: str, base_url: str) -> list[str]:
    urls: list[str] = []
    for raw in extract_attr_urls(page_html):
        url = normalize_url(raw, base_url)
        parsed = urllib.parse.urlparse(url)
        decoded_path = urllib.parse.unquote(parsed.path).lower()
        suffix = Path(decoded_path).suffix.lower()
        if suffix not in FILE_EXTENSIONS:
            continue
        if "/sites/default/files/" not in decoded_path:
            continue
        if "/styles/" in decoded_path:
            continue
        if any(term in decoded_path for term in ("license_images", "preview", "screenshot", "logo", "banner", "thumb")):
            continue
        urls.append(strip_fragment(url))
    return sorted(set(urls))


def extract_attr_urls(page_html: str) -> list[str]:
    return [html.unescape(match.group(2)) for match in ATTR_RE.finditer(page_html)]


def extract_title(page_html: str) -> str:
    match = TITLE_RE.search(page_html)
    if not match:
        return ""
    return clean_text(match.group(1))


def html_to_text(page_html: str) -> str:
    text = re.sub(r"(?is)<script.*?</script>|<style.*?</style>", " ", page_html)
    text = re.sub(r"(?is)<[^>]+>", " ", text)
    return clean_text(text)


def clean_text(value: str) -> str:
    return re.sub(r"\s+", " ", html.unescape(value)).strip()


def fetch_text(url: str, *, timeout: int) -> str:
    data = fetch_bytes(url, timeout=timeout)
    return data.decode("utf-8", errors="replace")


def fetch_bytes(url: str, *, timeout: int, referer: str = "") -> bytes:
    request = urllib.request.Request(
        url,
        headers={
            "User-Agent": USER_AGENT,
            "Accept": "*/*",
            **({"Referer": referer} if referer else {}),
        },
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return response.read()


def normalize_url(value: str, base_url: str) -> str:
    return urllib.parse.urljoin(base_url, html.unescape(str(value or "").strip()))


def strip_fragment(url: str) -> str:
    parsed = urllib.parse.urlparse(url)
    return urllib.parse.urlunparse(parsed._replace(fragment=""))


def is_opengameart_page(url: str) -> bool:
    parsed = urllib.parse.urlparse(url)
    return parsed.scheme in {"http", "https"} and parsed.netloc in {"opengameart.org", "lpc.opengameart.org"}


def existing_target_hashes(root: Path) -> set[str]:
    hashes: set[str] = set()
    if not root.exists():
        return hashes
    for path in root.rglob("*.png"):
        try:
            hashes.add(sha256_file(path))
        except OSError:
            pass
    return hashes


def has_visible_pixels(image: Image.Image) -> bool:
    alpha = np.asarray(image.getchannel("A"), dtype=np.uint8)
    return int((alpha > 10).sum()) > 0


def image_digest(image: Image.Image) -> str:
    buffer = BytesIO()
    image.save(buffer, format="PNG", optimize=True)
    return hashlib.sha256(buffer.getvalue()).hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as file:
        for chunk in iter(lambda: file.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def short_hash(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()[:10]


def safe_name(value: str) -> str:
    cleaned = "".join(char if char.isalnum() or char in {"-", "_"} else "_" for char in str(value or "").strip())
    cleaned = re.sub(r"_+", "_", cleaned).strip("_")
    return cleaned[:80] or "item"


def write_jsonl(path: Path, rows: Iterable[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as file:
        for row in rows:
            file.write(json.dumps(row, ensure_ascii=False) + "\n")


if __name__ == "__main__":
    raise SystemExit(main())
