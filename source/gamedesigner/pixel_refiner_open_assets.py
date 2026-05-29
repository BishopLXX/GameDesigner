from __future__ import annotations

import argparse
import concurrent.futures
import hashlib
import json
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import datetime
from io import BytesIO
from pathlib import Path
from typing import Any, Iterable

from PIL import Image, ImageOps

from .pixel_refiner_dataset import (
    PixelRefinerSourceRecord,
    add_source_record,
    dataset_dir,
    ensure_dataset_dirs,
    targets_dir,
)


FREE_GAME_SPRITES_SOURCE_ID = "freegamesprites_cc0"
FREE_GAME_SPRITES_BASE_URL = "https://freegamesprites.com"
FREE_GAME_SPRITES_DEFAULT_CATEGORIES = ("characters", "enemies")
FREE_GAME_SPRITES_LICENSE_URL = "https://creativecommons.org/publicdomain/zero/1.0/"
USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) GameDesignerPixelDataset/2.0"
ATTR_URL_RE = re.compile(r"""(?is)\b(?:src|href)\s*=\s*(['"])(.*?)\1""")
IMAGE_URL_RE = re.compile(
    r"""(?is)https?://freegamesprites\.com/images/ai-sprites/generated/[^"'<> ]+\.(?:webp|png)"""
)
CHARACTER_LIKE_TERMS = frozenset(
    {
        "abomination",
        "alien",
        "angel",
        "archer",
        "armor",
        "assassin",
        "bat",
        "bear",
        "beast",
        "boar",
        "brawler",
        "brute",
        "cat",
        "centaur",
        "cerberus",
        "chimera",
        "cloaked",
        "crawler",
        "creature",
        "cultist",
        "cyborg",
        "demon",
        "devil",
        "dire",
        "dog",
        "dragon",
        "drake",
        "drone",
        "druid",
        "dryad",
        "elemental",
        "elf",
        "eye",
        "fairy",
        "feral",
        "fighter",
        "fish",
        "frog",
        "gargoyle",
        "ghost",
        "ghoul",
        "giant",
        "gnoll",
        "goblin",
        "golem",
        "guard",
        "hag",
        "harpy",
        "hound",
        "hunter",
        "imp",
        "knight",
        "kobold",
        "lizard",
        "lurker",
        "mage",
        "manticore",
        "mech",
        "minotaur",
        "monster",
        "mutant",
        "necromancer",
        "ninja",
        "ogre",
        "orc",
        "pilot",
        "pirate",
        "priest",
        "raider",
        "rat",
        "reaper",
        "robot",
        "rogue",
        "samurai",
        "scorpion",
        "serpent",
        "shade",
        "shadow",
        "shaman",
        "skeleton",
        "slime",
        "snake",
        "soldier",
        "sorcerer",
        "spawn",
        "specter",
        "spider",
        "spirit",
        "sprite",
        "suit",
        "troll",
        "undead",
        "vampire",
        "warrior",
        "were",
        "werewolf",
        "witch",
        "wizard",
        "wolf",
        "wraith",
        "wyvern",
        "zombie",
    }
)
NON_CHARACTER_TERMS = frozenset(
    {
        "altar",
        "amulet",
        "anvil",
        "barrel",
        "barricade",
        "blast",
        "bomb",
        "bonfire",
        "book",
        "boulder",
        "building",
        "campfire",
        "cannon",
        "cart",
        "chest",
        "coin",
        "crystal",
        "door",
        "egg",
        "explosion",
        "fire",
        "flame",
        "gate",
        "hut",
        "lantern",
        "mine",
        "missile",
        "obelisk",
        "orb",
        "portal",
        "potion",
        "projectile",
        "rock",
        "rune",
        "ship",
        "shrine",
        "sign",
        "skull",
        "spike",
        "statue",
        "stone",
        "tower",
        "trap",
        "treasure",
        "turret",
        "vehicle",
        "wagon",
        "watch",
    }
)
HARD_REJECT_TERMS = frozenset(
    {
        "effect",
        "effects",
        "lowres",
        "projectile",
        "vfx",
    }
)


@dataclass(frozen=True)
class FreeGameSpritesAsset:
    slug: str
    page_url: str
    image_url: str
    site_category: str
    target_category: str


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Collect CC0 256x256 pixel character assets from FreeGameSprites into the Pixel Refiner dataset."
    )
    parser.add_argument(
        "--category",
        action="append",
        dest="categories",
        help="FreeGameSprites category slug. Defaults to characters and enemies. Can be passed more than once.",
    )
    parser.add_argument("--max-pages-per-category", type=int, default=80)
    parser.add_argument("--max-assets", type=int, default=2500)
    parser.add_argument("--workers", type=int, default=12)
    parser.add_argument("--timeout", type=int, default=30)
    parser.add_argument("--retries", type=int, default=3)
    parser.add_argument("--delay", type=float, default=0.08)
    parser.add_argument("--min-width", type=int, default=128)
    parser.add_argument("--min-height", type=int, default=128)
    parser.add_argument(
        "--loose-filter",
        action="store_true",
        help="Disable strict character-like slug filtering. By default, props/effects/traps are skipped.",
    )
    parser.add_argument("--overwrite", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)

    stats = collect_freegamesprites_cc0(
        categories=tuple(args.categories or FREE_GAME_SPRITES_DEFAULT_CATEGORIES),
        max_pages_per_category=max(1, int(args.max_pages_per_category)),
        max_assets=max(0, int(args.max_assets)),
        workers=max(1, int(args.workers)),
        timeout=max(1, int(args.timeout)),
        retries=max(1, int(args.retries)),
        delay=max(0.0, float(args.delay)),
        min_width=max(1, int(args.min_width)),
        min_height=max(1, int(args.min_height)),
        strict_character_filter=not bool(args.loose_filter),
        overwrite=bool(args.overwrite),
        dry_run=bool(args.dry_run),
    )
    print(json.dumps(stats, ensure_ascii=False, indent=2))
    return 0


def collect_freegamesprites_cc0(
    *,
    categories: Iterable[str] = FREE_GAME_SPRITES_DEFAULT_CATEGORIES,
    max_pages_per_category: int = 80,
    max_assets: int = 2500,
    workers: int = 12,
    timeout: int = 30,
    retries: int = 3,
    delay: float = 0.08,
    min_width: int = 128,
    min_height: int = 128,
    strict_character_filter: bool = True,
    overwrite: bool = False,
    dry_run: bool = False,
) -> dict[str, Any]:
    ensure_dataset_dirs()
    source_id = FREE_GAME_SPRITES_SOURCE_ID
    raw_dir = dataset_dir() / "raw" / source_id
    manifest_dir = dataset_dir() / "manifests"
    source_dir = dataset_dir() / "sources"
    if not dry_run:
        raw_dir.mkdir(parents=True, exist_ok=True)
        manifest_dir.mkdir(parents=True, exist_ok=True)
        source_dir.mkdir(parents=True, exist_ok=True)
        add_source_record(
            PixelRefinerSourceRecord(
                source_id=source_id,
                title="FreeGameSprites CC0 pixel characters and enemies",
                author="FreeGameSprites.com",
                url=f"{FREE_GAME_SPRITES_BASE_URL}/en/about",
                license="CC0",
                license_url=FREE_GAME_SPRITES_LICENSE_URL,
                ai_training_allowed=True,
                category="character_portrait,side_scroller_action_character",
                notes="FreeGameSprites states its PNG assets are public domain under CC0. Collected only from characters/enemies categories.",
            )
        )

    discovered = crawl_freegamesprites_assets(
        categories=tuple(_safe_slug(item) for item in categories if _safe_slug(item)),
        max_pages_per_category=max_pages_per_category,
        max_assets=max_assets,
        timeout=timeout,
        retries=retries,
        delay=delay,
        strict_character_filter=strict_character_filter,
    )
    if dry_run:
        return {
            "ok": True,
            "dry_run": True,
            "source_id": source_id,
            "assets_discovered": len(discovered),
            "strict_character_filter": strict_character_filter,
            "first_assets": [asset.__dict__ for asset in discovered[:20]],
        }

    records = download_and_export_freegamesprites_assets(
        discovered,
        raw_dir=raw_dir,
        timeout=timeout,
        retries=retries,
        workers=workers,
        min_width=min_width,
        min_height=min_height,
        overwrite=overwrite,
    )
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    manifest_path = manifest_dir / f"{source_id}_images_{timestamp}.jsonl"
    latest_manifest_path = manifest_dir / f"{source_id}_images_latest.jsonl"
    write_jsonl(manifest_path, records)
    write_jsonl(latest_manifest_path, records)
    source_path = source_dir / f"{source_id}.json"
    source_path.write_text(
        json.dumps(
            {
                "source_id": source_id,
                "title": "FreeGameSprites CC0 pixel characters and enemies",
                "url": f"{FREE_GAME_SPRITES_BASE_URL}/en/about",
                "categories": sorted({asset.site_category for asset in discovered}),
                "license": "CC0",
                "license_url": FREE_GAME_SPRITES_LICENSE_URL,
                "rights_basis": "FreeGameSprites about page states assets are public domain/CC0; this collector only imports characters and enemies.",
                "downloaded_at": datetime.now().isoformat(timespec="seconds"),
                "raw_dir": str(raw_dir),
                "target_dir": str(targets_dir() / source_id),
                "manifest": str(latest_manifest_path),
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    accepted = [row for row in records if row.get("accepted")]
    failed = [row for row in records if row.get("status") != "ok"]
    skipped = [row for row in records if row.get("status") == "skipped_existing"]
    return {
        "ok": True,
        "source_id": source_id,
        "assets_discovered": len(discovered),
        "strict_character_filter": strict_character_filter,
        "records": len(records),
        "accepted": len(accepted),
        "failed": len(failed),
        "skipped_existing": len(skipped),
        "raw_dir": str(raw_dir),
        "target_dir": str(targets_dir() / source_id),
        "manifest": str(latest_manifest_path),
        "source": str(source_path),
    }


def crawl_freegamesprites_assets(
    *,
    categories: tuple[str, ...],
    max_pages_per_category: int,
    max_assets: int,
    timeout: int,
    retries: int,
    delay: float,
    strict_character_filter: bool,
) -> list[FreeGameSpritesAsset]:
    seen_listing_pages: set[str] = set()
    seen_asset_pages: set[str] = set()
    seen_images: set[str] = set()
    assets: list[FreeGameSpritesAsset] = []
    for category in categories:
        queue = [f"{FREE_GAME_SPRITES_BASE_URL}/en/category/{category}"]
        pages_seen_in_category = 0
        while queue and pages_seen_in_category < max_pages_per_category:
            page_url = normalize_url(queue.pop(0), FREE_GAME_SPRITES_BASE_URL)
            if page_url in seen_listing_pages:
                continue
            seen_listing_pages.add(page_url)
            pages_seen_in_category += 1
            try:
                html = fetch_text(page_url, timeout=timeout, retries=retries)
            except Exception:
                continue

            for href in extract_attr_urls(html):
                url = normalize_url(href, page_url)
                if is_same_category_page(url, category) and url not in seen_listing_pages and url not in queue:
                    queue.append(url)
                if is_asset_page(url) and url not in seen_asset_pages:
                    seen_asset_pages.add(url)
                    slug = url.rstrip("/").rsplit("/", 1)[-1]
                    if strict_character_filter and not is_character_like_slug(slug):
                        continue
                    image_url = f"{FREE_GAME_SPRITES_BASE_URL}/images/ai-sprites/generated/{slug}.png"
                    if image_url in seen_images:
                        continue
                    seen_images.add(image_url)
                    assets.append(
                        FreeGameSpritesAsset(
                            slug=slug,
                            page_url=url,
                            image_url=image_url,
                            site_category=category,
                            target_category=target_category_for_site_category(category),
                        )
                    )
                    if max_assets and len(assets) >= max_assets:
                        return assets
            if delay:
                time.sleep(delay)
    return assets


def download_and_export_freegamesprites_assets(
    assets: list[FreeGameSpritesAsset],
    *,
    raw_dir: Path,
    timeout: int,
    retries: int,
    workers: int,
    min_width: int,
    min_height: int,
    overwrite: bool,
) -> list[dict[str, Any]]:
    raw_dir.mkdir(parents=True, exist_ok=True)
    target_root = targets_dir() / FREE_GAME_SPRITES_SOURCE_ID
    target_root.mkdir(parents=True, exist_ok=True)
    records: list[dict[str, Any]] = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as executor:
        futures = [
            executor.submit(
                download_and_export_one_freegamesprites_asset,
                asset,
                raw_dir=raw_dir,
                target_root=target_root,
                timeout=timeout,
                retries=retries,
                min_width=min_width,
                min_height=min_height,
                overwrite=overwrite,
            )
            for asset in assets
        ]
        for future in concurrent.futures.as_completed(futures):
            records.append(future.result())
    return sorted(records, key=lambda item: str(item.get("page_url") or ""))


def download_and_export_one_freegamesprites_asset(
    asset: FreeGameSpritesAsset,
    *,
    raw_dir: Path,
    target_root: Path,
    timeout: int,
    retries: int,
    min_width: int,
    min_height: int,
    overwrite: bool,
) -> dict[str, Any]:
    target_dir = target_root / asset.target_category
    default_digest = short_hash(asset.image_url.encode("utf-8"))
    raw_path = raw_dir / f"{asset.slug}_{default_digest}{Path(urllib.parse.urlparse(asset.image_url).path).suffix or '.png'}"
    target_path = target_dir / f"{asset.slug}_{default_digest}.png"
    record: dict[str, Any] = {
        "source_id": FREE_GAME_SPRITES_SOURCE_ID,
        "slug": asset.slug,
        "site_category": asset.site_category,
        "category": asset.target_category,
        "page_url": asset.page_url,
        "image_url": asset.image_url,
        "raw_path": str(raw_path),
        "target_path": str(target_path),
        "status": "",
        "accepted": False,
        "width": 0,
        "height": 0,
        "sha256": "",
        "error": "",
    }
    try:
        if target_path.exists() and raw_path.exists() and not overwrite:
            with Image.open(target_path) as image:
                record.update(
                    {
                        "status": "skipped_existing",
                        "accepted": True,
                        "width": image.width,
                        "height": image.height,
                        "sha256": sha256_file(target_path),
                    }
            )
            return record

        data = b""
        fetched_url = ""
        errors: list[str] = []
        for image_url in freegamesprites_image_url_candidates(asset.image_url):
            try:
                data = fetch_bytes(image_url, timeout=timeout, retries=retries, referer=asset.page_url)
                fetched_url = image_url
                break
            except Exception as exc:
                errors.append(f"{image_url}: {type(exc).__name__}: {exc}")
        if not data or not fetched_url:
            raise RuntimeError("; ".join(errors) or f"Could not fetch {asset.image_url}")
        suffix = Path(urllib.parse.urlparse(fetched_url).path).suffix or ".png"
        digest = short_hash(fetched_url.encode("utf-8"))
        raw_path = raw_dir / f"{asset.slug}_{digest}{suffix}"
        target_path = target_dir / f"{asset.slug}_{digest}.png"
        record.update({"image_url": fetched_url, "raw_path": str(raw_path), "target_path": str(target_path)})
        if target_path.exists() and raw_path.exists() and not overwrite:
            with Image.open(target_path) as image:
                record.update(
                    {
                        "status": "skipped_existing",
                        "accepted": True,
                        "width": image.width,
                        "height": image.height,
                        "sha256": sha256_file(target_path),
                    }
                )
            return record
        raw_path.parent.mkdir(parents=True, exist_ok=True)
        raw_path.write_bytes(data)
        with Image.open(BytesIO(data)) as loaded:
            image = ImageOps.exif_transpose(loaded).convert("RGBA")
        record["width"] = image.width
        record["height"] = image.height
        if image.width < min_width or image.height < min_height:
            record.update({"status": "rejected", "error": f"smaller_than_{min_width}x{min_height}"})
            return record
        if not has_non_empty_alpha(image):
            record.update({"status": "rejected", "error": "empty_or_opaque_alpha"})
            return record
        target_dir.mkdir(parents=True, exist_ok=True)
        image.save(target_path, format="PNG", optimize=True)
        record.update(
            {
                "status": "ok",
                "accepted": True,
                "sha256": sha256_file(target_path),
            }
        )
        return record
    except Exception as exc:
        record.update({"status": "error", "error": f"{type(exc).__name__}: {exc}"})
        return record


def extract_freegamesprites_image_urls(html: str) -> list[str]:
    return sorted(set(match.group(0) for match in IMAGE_URL_RE.finditer(html)))


def freegamesprites_image_url_candidates(primary_url: str) -> list[str]:
    parsed = urllib.parse.urlparse(primary_url)
    path = parsed.path or ""
    stem = path.rsplit(".", 1)[0]
    candidates = [primary_url]
    for suffix in (".png", ".webp"):
        candidate = urllib.parse.urlunparse(parsed._replace(path=f"{stem}{suffix}"))
        if candidate not in candidates:
            candidates.append(candidate)
    return candidates


def extract_attr_urls(html: str) -> list[str]:
    return [match.group(2).strip() for match in ATTR_URL_RE.finditer(html) if match.group(2).strip()]


def is_same_category_page(url: str, category: str) -> bool:
    parsed = urllib.parse.urlparse(url)
    return (
        parsed.netloc == urllib.parse.urlparse(FREE_GAME_SPRITES_BASE_URL).netloc
        and parsed.path == f"/en/category/{category}"
    )


def is_asset_page(url: str) -> bool:
    parsed = urllib.parse.urlparse(url)
    return (
        parsed.netloc == urllib.parse.urlparse(FREE_GAME_SPRITES_BASE_URL).netloc
        and re.fullmatch(r"/en/assets/[a-z0-9-]+", parsed.path or "") is not None
    )


def target_category_for_site_category(category: str) -> str:
    if category == "enemies":
        return "side_scroller_action_character"
    return "character_portrait"


def is_character_like_slug(slug: str) -> bool:
    words = {part for part in re.split(r"[^a-z0-9]+", str(slug or "").lower()) if part}
    if not words:
        return False
    if words & HARD_REJECT_TERMS:
        return False
    has_character = bool(words & CHARACTER_LIKE_TERMS)
    if not has_character:
        return False
    if words & NON_CHARACTER_TERMS and not ((words & CHARACTER_LIKE_TERMS) - {"eye", "shadow", "spirit"}):
        return False
    return True


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
            headers = {"User-Agent": USER_AGENT}
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


def normalize_url(value: str, base_url: str) -> str:
    text = str(value or "").strip()
    if text.startswith("//"):
        text = f"{urllib.parse.urlparse(base_url).scheme or 'https'}:{text}"
    return urllib.parse.urljoin(base_url, text)


def has_non_empty_alpha(image: Image.Image) -> bool:
    alpha = image.getchannel("A")
    extrema = alpha.getextrema()
    return bool(extrema and extrema[1] > 0 and extrema[0] < 255)


def short_hash(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()[:12]


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as file:
        for chunk in iter(lambda: file.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def write_jsonl(path: Path, rows: Iterable[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as file:
        for row in rows:
            file.write(json.dumps(row, ensure_ascii=False) + "\n")


def _safe_slug(value: str) -> str:
    text = str(value or "").strip().lower()
    return "".join(char for char in text if char.isalnum() or char in {"-", "_"})


if __name__ == "__main__":
    raise SystemExit(main())
