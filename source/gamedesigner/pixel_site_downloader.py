from __future__ import annotations

import argparse
import concurrent.futures
import hashlib
import json
import re
import shutil
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable

from .pixel_refiner_dataset import dataset_dir

try:
    from PIL import Image, ImageOps, ImageSequence
except Exception:  # pragma: no cover - Pillow is a project dependency, but keep the downloader usable.
    Image = None
    ImageOps = None
    ImageSequence = None


DEFAULT_PIXEL_SITE_ROOT = dataset_dir()
DEFAULT_START_URLS = (
    "http://pndsndn.blog79.fc2.com/?all",
    "http://pndsndn.blog79.fc2.com/",
)
DEFAULT_ASSET_HOST_CONTAINS = "blog-imgs-"
DEFAULT_ASSET_PATH_CONTAINS = "/p/n/d/pndsndn/"
DEFAULT_PAGE_HOST = "pndsndn.blog79.fc2.com"
DEFAULT_USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) GameDesignerPixelDataset/1.0"
IMAGE_EXTENSIONS = (".png", ".gif", ".jpg", ".jpeg", ".webp")
ATTR_URL_RE = re.compile(r"""(?is)\b(?:src|href)\s*=\s*(['"])(.*?)\1""")


@dataclass(frozen=True)
class DownloadedImage:
    url: str
    raw_path: Path
    page_urls: tuple[str, ...]
    sha256: str
    byte_count: int
    status: str
    error: str = ""


@dataclass(frozen=True)
class ImageInfo:
    width: int = 0
    height: int = 0
    mode: str = ""
    format: str = ""
    frame_count: int = 1
    has_alpha: bool = False
    error: str = ""


@dataclass(frozen=True)
class CandidateDecision:
    accepted: bool
    category: str
    reason: str


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Download pixel-art images from an owned FC2 blog into the Pixel Refiner dataset.")
    parser.add_argument(
        "--start-url",
        action="append",
        dest="start_urls",
        help="Seed URL. Can be passed more than once. Defaults to the pndsndn FC2 archive and home page.",
    )
    parser.add_argument("--output-root", default=str(DEFAULT_PIXEL_SITE_ROOT), help="Dataset root on D drive.")
    parser.add_argument("--source-id", default="pndsndn_fc2", help="Short source id used for folders and manifests.")
    parser.add_argument("--asset-host-contains", default=DEFAULT_ASSET_HOST_CONTAINS)
    parser.add_argument("--asset-path-contains", default=DEFAULT_ASSET_PATH_CONTAINS)
    parser.add_argument("--page-host", default=DEFAULT_PAGE_HOST, help="Only crawl blog-entry pages on this host.")
    parser.add_argument("--max-pages", type=int, default=500)
    parser.add_argument("--max-workers", type=int, default=8)
    parser.add_argument("--timeout", type=int, default=30)
    parser.add_argument("--retries", type=int, default=3)
    parser.add_argument("--delay", type=float, default=0.05, help="Small delay between page fetches.")
    parser.add_argument("--min-width", type=int, default=96)
    parser.add_argument("--min-height", type=int, default=96)
    parser.add_argument("--min-area", type=int, default=10_000)
    parser.add_argument("--allow-opaque-targets", action="store_true", help="Also export non-transparent images into targets.")
    parser.add_argument("--no-target-export", action="store_true", help="Only download raw images and manifests.")
    parser.add_argument("--no-gif-frames", action="store_true", help="Do not export animated GIF frames as target PNGs.")
    parser.add_argument("--max-gif-frames", type=int, default=64)
    parser.add_argument("--overwrite", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)

    root = Path(args.output_root)
    source_id = safe_name(args.source_id)
    start_urls = tuple(args.start_urls or DEFAULT_START_URLS)
    raw_dir = root / "raw" / source_id
    target_dir = root / "targets" / source_id
    manifest_dir = root / "manifests"
    source_dir = root / "sources"
    rejected_dir = root / "rejected" / source_id
    for folder in (raw_dir, target_dir, manifest_dir, source_dir, rejected_dir):
        if not args.dry_run:
            folder.mkdir(parents=True, exist_ok=True)

    crawl = crawl_site(
        start_urls,
        page_host=args.page_host,
        asset_host_contains=args.asset_host_contains,
        asset_path_contains=args.asset_path_contains,
        max_pages=max(1, args.max_pages),
        timeout=max(1, args.timeout),
        retries=max(1, args.retries),
        delay=max(0.0, args.delay),
    )
    image_pages: dict[str, set[str]] = crawl["image_pages"]
    page_records: list[dict[str, Any]] = crawl["page_records"]
    image_urls = sorted(image_pages)

    if args.dry_run:
        print(
            json.dumps(
                {
                    "ok": True,
                    "dry_run": True,
                    "pages": len(page_records),
                    "images": len(image_urls),
                    "output_root": str(root),
                    "first_images": image_urls[:20],
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return 0

    downloads = download_images(
        image_pages,
        raw_dir=raw_dir,
        timeout=max(1, args.timeout),
        retries=max(1, args.retries),
        max_workers=max(1, args.max_workers),
        overwrite=bool(args.overwrite),
    )
    records = build_manifest_records(
        downloads,
        target_dir=target_dir,
        rejected_dir=rejected_dir,
        min_width=max(1, args.min_width),
        min_height=max(1, args.min_height),
        min_area=max(1, args.min_area),
        require_alpha=not bool(args.allow_opaque_targets),
        export_targets=not bool(args.no_target_export),
        extract_gif_frames=not bool(args.no_gif_frames),
        max_gif_frames=max(1, args.max_gif_frames),
    )

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    manifest_path = manifest_dir / f"{source_id}_images_{timestamp}.jsonl"
    page_manifest_path = manifest_dir / f"{source_id}_pages_{timestamp}.jsonl"
    latest_manifest_path = manifest_dir / f"{source_id}_images_latest.jsonl"
    latest_page_manifest_path = manifest_dir / f"{source_id}_pages_latest.jsonl"
    source_path = source_dir / f"{source_id}.json"
    write_jsonl(manifest_path, records)
    write_jsonl(page_manifest_path, page_records)
    shutil.copy2(manifest_path, latest_manifest_path)
    shutil.copy2(page_manifest_path, latest_page_manifest_path)
    source_path.write_text(
        json.dumps(
            {
                "source_id": source_id,
                "title": "pndsndn FC2 pixel art archive",
                "start_urls": list(start_urls),
                "rights_basis": "User stated in this project chat on 2026-05-29 that they are the author and authorize using these assets for this local training dataset.",
                "downloaded_at": datetime.now().isoformat(timespec="seconds"),
                "raw_dir": str(raw_dir),
                "target_dir": str(target_dir),
                "manifest": str(latest_manifest_path),
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )

    accepted = [record for record in records if record.get("accepted")]
    target_files = sum(len(record.get("target_paths") or []) for record in records)
    failed = [record for record in records if record.get("download_status") != "ok"]
    print(
        json.dumps(
            {
                "ok": True,
                "pages": len(page_records),
                "discovered_images": len(image_urls),
                "downloaded_records": len(records),
                "accepted_images": len(accepted),
                "target_png_files": target_files,
                "failed": len(failed),
                "raw_dir": str(raw_dir),
                "target_dir": str(target_dir),
                "manifest": str(latest_manifest_path),
                "source": str(source_path),
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


def crawl_site(
    start_urls: Iterable[str],
    *,
    page_host: str,
    asset_host_contains: str,
    asset_path_contains: str,
    max_pages: int,
    timeout: int,
    retries: int,
    delay: float,
) -> dict[str, Any]:
    queue = [normalize_page_url(url) for url in start_urls]
    seen: set[str] = set()
    image_pages: dict[str, set[str]] = {}
    page_records: list[dict[str, Any]] = []
    while queue and len(seen) < max_pages:
        page_url = queue.pop(0)
        if page_url in seen:
            continue
        seen.add(page_url)
        started = time.perf_counter()
        try:
            html = fetch_text(page_url, timeout=timeout, retries=retries)
            elapsed_ms = int((time.perf_counter() - started) * 1000)
            page_records.append(
                {
                    "url": page_url,
                    "status": "ok",
                    "chars": len(html),
                    "elapsed_ms": elapsed_ms,
                }
            )
        except Exception as exc:
            page_records.append(
                {
                    "url": page_url,
                    "status": "error",
                    "error": f"{type(exc).__name__}: {exc}",
                }
            )
            continue

        for image_url in extract_image_urls(
            html,
            page_url,
            asset_host_contains=asset_host_contains,
            asset_path_contains=asset_path_contains,
        ):
            image_pages.setdefault(image_url, set()).add(page_url)

        for next_url in extract_entry_urls(html, page_url, page_host=page_host):
            if next_url not in seen and next_url not in queue:
                queue.append(next_url)
        if delay:
            time.sleep(delay)
    return {"image_pages": image_pages, "page_records": page_records}


def extract_image_urls(
    html: str,
    base_url: str,
    *,
    asset_host_contains: str = DEFAULT_ASSET_HOST_CONTAINS,
    asset_path_contains: str = DEFAULT_ASSET_PATH_CONTAINS,
) -> list[str]:
    results: set[str] = set()
    for raw in extract_attr_urls(html):
        resolved = normalize_asset_url(raw, base_url)
        if is_image_asset_url(
            resolved,
            asset_host_contains=asset_host_contains,
            asset_path_contains=asset_path_contains,
        ):
            results.add(resolved)
    return sorted(results)


def extract_entry_urls(html: str, base_url: str, *, page_host: str = DEFAULT_PAGE_HOST) -> list[str]:
    results: set[str] = set()
    for raw in extract_attr_urls(html):
        resolved = normalize_page_url(raw, base_url)
        if is_entry_url(resolved, page_host=page_host):
            results.add(resolved)
    return sorted(results)


def extract_attr_urls(html: str) -> list[str]:
    return [match.group(2).strip() for match in ATTR_URL_RE.finditer(html) if match.group(2).strip()]


def normalize_asset_url(value: str, base_url: str) -> str:
    url = normalize_url(value, base_url)
    parsed = urllib.parse.urlparse(url)
    scheme = "https" if parsed.netloc.startswith("blog-imgs-") else parsed.scheme
    return urllib.parse.urlunparse(parsed._replace(scheme=scheme, params="", query="", fragment=""))


def normalize_page_url(value: str, base_url: str | None = None) -> str:
    url = normalize_url(value, base_url or value)
    parsed = urllib.parse.urlparse(url)
    scheme = "http" if parsed.netloc == DEFAULT_PAGE_HOST else parsed.scheme
    return urllib.parse.urlunparse(parsed._replace(scheme=scheme, params="", query=parsed.query, fragment=""))


def normalize_url(value: str, base_url: str) -> str:
    text = str(value or "").strip()
    if text.startswith("//"):
        text = f"{urllib.parse.urlparse(base_url).scheme or 'https'}:{text}"
    return urllib.parse.urljoin(base_url, text)


def is_entry_url(url: str, *, page_host: str = DEFAULT_PAGE_HOST) -> bool:
    parsed = urllib.parse.urlparse(url)
    return parsed.netloc == page_host and re.fullmatch(r"/blog-entry-\d+\.html", parsed.path or "") is not None


def is_image_asset_url(
    url: str,
    *,
    asset_host_contains: str = DEFAULT_ASSET_HOST_CONTAINS,
    asset_path_contains: str = DEFAULT_ASSET_PATH_CONTAINS,
) -> bool:
    parsed = urllib.parse.urlparse(url)
    path = urllib.parse.unquote(parsed.path or "")
    extension = Path(path).suffix.lower()
    return (
        parsed.scheme in {"http", "https"}
        and asset_host_contains in parsed.netloc
        and asset_path_contains in path
        and extension in IMAGE_EXTENSIONS
    )


def download_images(
    image_pages: dict[str, set[str]],
    *,
    raw_dir: Path,
    timeout: int,
    retries: int,
    max_workers: int,
    overwrite: bool,
) -> list[DownloadedImage]:
    raw_dir.mkdir(parents=True, exist_ok=True)
    urls = sorted(image_pages)
    results: list[DownloadedImage] = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = [
            executor.submit(
                download_one_image,
                url,
                page_urls=tuple(sorted(image_pages[url])),
                raw_dir=raw_dir,
                timeout=timeout,
                retries=retries,
                overwrite=overwrite,
            )
            for url in urls
        ]
        for future in concurrent.futures.as_completed(futures):
            results.append(future.result())
    return sorted(results, key=lambda item: item.url)


def download_one_image(
    url: str,
    *,
    page_urls: tuple[str, ...],
    raw_dir: Path,
    timeout: int,
    retries: int,
    overwrite: bool,
) -> DownloadedImage:
    target_path = raw_path_for_url(url, raw_dir)
    try:
        if target_path.exists() and not overwrite:
            data = target_path.read_bytes()
            status = "ok"
        else:
            data = fetch_bytes(url, timeout=timeout, retries=retries, referer=page_urls[0] if page_urls else "")
            target_path.parent.mkdir(parents=True, exist_ok=True)
            temp_path = target_path.with_suffix(target_path.suffix + ".tmp")
            temp_path.write_bytes(data)
            temp_path.replace(target_path)
            status = "ok"
        return DownloadedImage(
            url=url,
            raw_path=target_path,
            page_urls=page_urls,
            sha256=hashlib.sha256(data).hexdigest(),
            byte_count=len(data),
            status=status,
        )
    except Exception as exc:
        return DownloadedImage(
            url=url,
            raw_path=target_path,
            page_urls=page_urls,
            sha256="",
            byte_count=0,
            status="error",
            error=f"{type(exc).__name__}: {exc}",
        )


def build_manifest_records(
    downloads: list[DownloadedImage],
    *,
    target_dir: Path,
    rejected_dir: Path,
    min_width: int,
    min_height: int,
    min_area: int,
    require_alpha: bool,
    export_targets: bool,
    extract_gif_frames: bool,
    max_gif_frames: int,
) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    target_dir.mkdir(parents=True, exist_ok=True)
    rejected_dir.mkdir(parents=True, exist_ok=True)
    for download in downloads:
        info = inspect_image(download.raw_path) if download.status == "ok" else ImageInfo(error=download.error)
        decision = decide_candidate(
            download.raw_path,
            info,
            min_width=min_width,
            min_height=min_height,
            min_area=min_area,
            require_alpha=require_alpha,
        )
        target_paths: list[str] = []
        if export_targets and download.status == "ok" and decision.accepted:
            target_paths = [
                str(path)
                for path in export_target_pngs(
                    download.raw_path,
                    target_dir=target_dir / decision.category,
                    extract_gif_frames=extract_gif_frames,
                    max_gif_frames=max_gif_frames,
                )
            ]
        elif download.status == "ok" and not decision.accepted:
            reject_note = rejected_dir / f"{download.raw_path.stem}.json"
            reject_note.write_text(
                json.dumps(
                    {
                        "url": download.url,
                        "raw_path": str(download.raw_path),
                        "reason": decision.reason,
                        "width": info.width,
                        "height": info.height,
                        "has_alpha": info.has_alpha,
                    },
                    ensure_ascii=False,
                    indent=2,
                )
                + "\n",
                encoding="utf-8",
            )
        records.append(
            {
                "url": download.url,
                "page_urls": list(download.page_urls),
                "raw_path": str(download.raw_path),
                "sha256": download.sha256,
                "bytes": download.byte_count,
                "download_status": download.status,
                "download_error": download.error,
                "width": info.width,
                "height": info.height,
                "mode": info.mode,
                "format": info.format,
                "frame_count": info.frame_count,
                "has_alpha": info.has_alpha,
                "image_error": info.error,
                "accepted": decision.accepted,
                "category": decision.category,
                "reason": decision.reason,
                "target_paths": target_paths,
            }
        )
    return records


def inspect_image(path: Path) -> ImageInfo:
    if Image is None:
        return ImageInfo(error="Pillow is not available")
    try:
        with Image.open(path) as image:
            width, height = image.size
            frame_count = int(getattr(image, "n_frames", 1) or 1)
            return ImageInfo(
                width=width,
                height=height,
                mode=str(image.mode),
                format=str(image.format or ""),
                frame_count=frame_count,
                has_alpha=image_has_alpha(image),
            )
    except Exception as exc:
        return ImageInfo(error=f"{type(exc).__name__}: {exc}")


def image_has_alpha(image: Any) -> bool:
    if Image is None:
        return False
    if image.mode in {"RGBA", "LA"}:
        extrema = image.getchannel("A").getextrema()
        return bool(extrema and extrema[0] < 255)
    if image.mode == "P" and "transparency" in image.info:
        return True
    try:
        converted = image.convert("RGBA")
        extrema = converted.getchannel("A").getextrema()
        return bool(extrema and extrema[0] < 255)
    except Exception:
        return False


def decide_candidate(
    path: Path,
    info: ImageInfo,
    *,
    min_width: int,
    min_height: int,
    min_area: int,
    require_alpha: bool,
) -> CandidateDecision:
    if info.error:
        return CandidateDecision(False, "rejected", info.error)
    if info.width < min_width or info.height < min_height:
        return CandidateDecision(False, "rejected", f"smaller_than_{min_width}x{min_height}")
    if info.width * info.height < min_area:
        return CandidateDecision(False, "rejected", f"area_below_{min_area}")
    if require_alpha and not info.has_alpha:
        return CandidateDecision(False, "rejected", "opaque_or_no_transparency")
    extension = path.suffix.lower()
    if info.frame_count > 1 or extension == ".gif":
        return CandidateDecision(True, "side_scroller_action_character", "accepted_animated_or_gif")
    if info.height >= info.width:
        return CandidateDecision(True, "character_portrait", "accepted_tall_character")
    return CandidateDecision(True, "character_sprite", "accepted_pixel_character")


def export_target_pngs(
    raw_path: Path,
    *,
    target_dir: Path,
    extract_gif_frames: bool,
    max_gif_frames: int,
) -> list[Path]:
    if Image is None or ImageOps is None:
        return []
    target_dir.mkdir(parents=True, exist_ok=True)
    output_paths: list[Path] = []
    digest = short_hash(raw_path.read_bytes())
    with Image.open(raw_path) as image:
        frame_count = int(getattr(image, "n_frames", 1) or 1)
        if image.format == "GIF" and extract_gif_frames and ImageSequence is not None and frame_count > 1:
            for index, frame in enumerate(ImageSequence.Iterator(image)):
                if index >= max_gif_frames:
                    break
                output = target_dir / f"{raw_path.stem}_frame{index:03d}_{digest}.png"
                frame.convert("RGBA").save(output, format="PNG", optimize=True)
                output_paths.append(output)
            return output_paths
        output = target_dir / f"{raw_path.stem}_{digest}.png"
        ImageOps.exif_transpose(image).convert("RGBA").save(output, format="PNG", optimize=True)
        output_paths.append(output)
    return output_paths


def fetch_text(url: str, *, timeout: int, retries: int) -> str:
    data, charset = fetch_url(url, timeout=timeout, retries=retries)
    return data.decode(charset or "utf-8", errors="replace")


def fetch_bytes(url: str, *, timeout: int, retries: int, referer: str = "") -> bytes:
    data, _charset = fetch_url(url, timeout=timeout, retries=retries, referer=referer)
    return data


def fetch_url(url: str, *, timeout: int, retries: int, referer: str = "") -> tuple[bytes, str]:
    last_error: Exception | None = None
    for attempt in range(max(1, retries)):
        try:
            headers = {"User-Agent": DEFAULT_USER_AGENT}
            if referer:
                headers["Referer"] = referer
            request = urllib.request.Request(url, headers=headers)
            with urllib.request.urlopen(request, timeout=timeout) as response:
                data = response.read()
                charset = response.headers.get_content_charset() or "utf-8"
                return data, charset
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            last_error = exc
            if attempt + 1 < retries:
                time.sleep(0.35 * (attempt + 1))
    if last_error is not None:
        raise last_error
    raise RuntimeError(f"Could not fetch {url}")


def raw_path_for_url(url: str, raw_dir: Path) -> Path:
    parsed = urllib.parse.urlparse(url)
    name = urllib.parse.unquote(Path(parsed.path).name) or "image"
    stem = safe_name(Path(name).stem)
    suffix = Path(name).suffix.lower()
    if suffix not in IMAGE_EXTENSIONS:
        suffix = ".img"
    digest = hashlib.sha256(url.encode("utf-8")).hexdigest()[:10]
    return raw_dir / suffix.lstrip(".") / f"{stem}_{digest}{suffix}"


def write_jsonl(path: Path, rows: Iterable[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as file:
        for row in rows:
            file.write(json.dumps(row, ensure_ascii=False) + "\n")


def short_hash(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()[:10]


def safe_name(value: str) -> str:
    cleaned = "".join(char if char.isalnum() or char in {"-", "_"} else "_" for char in str(value or "").strip())
    return cleaned.strip("_") or "item"


if __name__ == "__main__":
    raise SystemExit(main())
