from __future__ import annotations

import argparse
import json
from pathlib import Path

from gamedesigner.pixel_refiner_dataset import (
    PixelRefinerSourceRecord,
    add_source_record,
    build_pair_record,
    dataset_dir,
    ensure_dataset_dirs,
    generated_inputs_dir,
    ingest_generated_input_image,
    ingest_software_candidate_pair,
    ingest_target_image,
    load_pair_records,
    load_source_records,
    summarize_dataset,
    targets_dir,
)
from gamedesigner.pixel_refiner_dataset_eval import evaluate_dataset
from gamedesigner.pixel_refiner_eval_suite import build_fixed_eval_suite, render_fixed_eval_contact_sheet, run_fixed_eval_model
from gamedesigner.pixel_refiner_pair_generation import build_pairs_from_targets, generate_training_inputs_from_target
from gamedesigner.pixel_refiner_character_crops import CharacterCropConfig, extract_character_crops
from gamedesigner.pixel_refiner_patch_expansion import PatchExpansionConfig, expand_target_patches
from gamedesigner.pixel_refiner_authorized_import import (
    AuthorizedImportConfig,
    AuthorizedSiteCrawlConfig,
    crawl_authorized_site,
    import_authorized_targets,
)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="GameDesigner Pixel Refiner training dataset helper")
    subparsers = parser.add_subparsers(dest="command", required=True)

    source_add = subparsers.add_parser("add-source", help="Add a licensed source record")
    source_add.add_argument("--source-id", required=True)
    source_add.add_argument("--title", default="")
    source_add.add_argument("--author", default="")
    source_add.add_argument("--url", default="")
    source_add.add_argument("--license", default="")
    source_add.add_argument("--license-url", default="")
    source_add.add_argument("--category", default="")
    source_add.add_argument("--notes", default="")
    source_add.add_argument("--allowed", action="store_true")

    target_import = subparsers.add_parser("import-target", help="Import a target PNG")
    target_import.add_argument("path")
    target_import.add_argument("--source-id", required=True)
    target_import.add_argument("--category", required=True)
    target_import.add_argument("--title", default="")
    target_import.add_argument("--license", default="")
    target_import.add_argument("--prompt", default="")
    target_import.add_argument("--notes", default="")

    input_import = subparsers.add_parser("import-input", help="Import a generated input PNG")
    input_import.add_argument("path")
    input_import.add_argument("--source-id", required=True)
    input_import.add_argument("--category", required=True)
    input_import.add_argument("--input-kind", required=True)
    input_import.add_argument("--prompt", default="")
    input_import.add_argument("--notes", default="")

    pair_make = subparsers.add_parser("make-pair", help="Create a training pair")
    pair_make.add_argument("--target", required=True)
    pair_make.add_argument("--input", required=True)
    pair_make.add_argument("--source-id", required=True)
    pair_make.add_argument("--category", required=True)
    pair_make.add_argument("--input-kind", required=True)
    pair_make.add_argument("--prompt", default="")
    pair_make.add_argument("--license", default="")
    pair_make.add_argument("--notes", default="")

    software_pair = subparsers.add_parser(
        "import-software-pair",
        help="Import a real GameDesigner software candidate input and matching true pixel target",
    )
    software_pair.add_argument("--input", required=True)
    software_pair.add_argument("--target", required=True)
    software_pair.add_argument("--category", default="character_portrait")
    software_pair.add_argument("--prompt", default="")
    software_pair.add_argument("--notes", default="")
    software_pair.add_argument("--no-skip-existing", action="store_true")

    authorized_targets = subparsers.add_parser(
        "import-authorized-targets",
        help="Import a local authorized artist asset folder, classify character pixel targets, and optionally build training pairs",
    )
    authorized_targets.add_argument("input_dir")
    authorized_targets.add_argument("--source-id", required=True)
    authorized_targets.add_argument("--title", default="")
    authorized_targets.add_argument("--author", default="")
    authorized_targets.add_argument("--url", default="")
    authorized_targets.add_argument("--license", default="User-authorized local training source")
    authorized_targets.add_argument("--license-url", default="")
    authorized_targets.add_argument("--rights-basis", default="")
    authorized_targets.add_argument("--category", default="auto")
    authorized_targets.add_argument("--min-width", type=int, default=64)
    authorized_targets.add_argument("--min-height", type=int, default=64)
    authorized_targets.add_argument("--min-area", type=int, default=4096)
    authorized_targets.add_argument("--max-images", type=int, default=0)
    authorized_targets.add_argument("--max-gif-frames", type=int, default=24)
    authorized_targets.add_argument("--build-pairs", action="store_true")
    authorized_targets.add_argument("--generate-ai-pseudo", action="store_true")
    authorized_targets.add_argument("--ai-pseudo-limit", type=int, default=0)
    authorized_targets.add_argument("--ai-pseudo-variants", type=int, default=1)
    authorized_targets.add_argument("--ai-pseudo-prompt", default="")
    authorized_targets.add_argument("--ai-pseudo-workers", type=int, default=4)
    authorized_targets.add_argument("--request-timeout", type=int, default=90)
    authorized_targets.add_argument(
        "--background",
        choices=["transparent", "opaque", "auto"],
        default="auto",
    )
    authorized_targets.add_argument(
        "--alpha-mode",
        choices=["preserve", "target_mask", "soft_target_mask"],
        default="soft_target_mask",
    )
    authorized_targets.add_argument("--dry-run", action="store_true")

    authorized_site = subparsers.add_parser(
        "crawl-authorized-site",
        help="Crawl an owned/authorized public image site into Pixel Refiner targets, then optionally build training pairs",
    )
    authorized_site.add_argument("--start-url", action="append", required=True)
    authorized_site.add_argument("--source-id", required=True)
    authorized_site.add_argument("--title", default="")
    authorized_site.add_argument("--author", default="")
    authorized_site.add_argument("--url", default="")
    authorized_site.add_argument("--license", default="User-authorized website training source")
    authorized_site.add_argument("--license-url", default="")
    authorized_site.add_argument("--rights-basis", default="")
    authorized_site.add_argument("--page-host", default="")
    authorized_site.add_argument("--asset-host-contains", default="")
    authorized_site.add_argument("--asset-path-contains", default="")
    authorized_site.add_argument("--max-pages", type=int, default=500)
    authorized_site.add_argument("--max-workers", type=int, default=8)
    authorized_site.add_argument("--timeout", type=int, default=30)
    authorized_site.add_argument("--retries", type=int, default=3)
    authorized_site.add_argument("--delay", type=float, default=0.05)
    authorized_site.add_argument("--min-width", type=int, default=64)
    authorized_site.add_argument("--min-height", type=int, default=64)
    authorized_site.add_argument("--min-area", type=int, default=4096)
    authorized_site.add_argument("--require-alpha", action="store_true")
    authorized_site.add_argument("--no-gif-frames", action="store_true")
    authorized_site.add_argument("--max-gif-frames", type=int, default=24)
    authorized_site.add_argument("--overwrite", action="store_true")
    authorized_site.add_argument("--build-pairs", action="store_true")
    authorized_site.add_argument("--generate-ai-pseudo", action="store_true")
    authorized_site.add_argument("--ai-pseudo-limit", type=int, default=0)
    authorized_site.add_argument("--ai-pseudo-variants", type=int, default=1)
    authorized_site.add_argument("--ai-pseudo-prompt", default="")
    authorized_site.add_argument("--ai-pseudo-workers", type=int, default=4)
    authorized_site.add_argument("--request-timeout", type=int, default=90)
    authorized_site.add_argument(
        "--background",
        choices=["transparent", "opaque", "auto"],
        default="auto",
    )
    authorized_site.add_argument(
        "--alpha-mode",
        choices=["preserve", "target_mask", "soft_target_mask"],
        default="soft_target_mask",
    )
    authorized_site.add_argument("--dry-run", action="store_true")

    bulk_generate = subparsers.add_parser("generate-inputs", help="Generate training inputs from a target PNG")
    bulk_generate.add_argument("target")
    bulk_generate.add_argument("--source-id", required=True)
    bulk_generate.add_argument("--category", required=True)

    bulk_pairs = subparsers.add_parser(
        "build-pairs",
        help="Generate inputs for every target PNG and build pair records",
    )
    bulk_pairs.add_argument("--target-root", default="")
    bulk_pairs.add_argument("--source-id", default="")
    bulk_pairs.add_argument("--category", default="")
    bulk_pairs.add_argument("--no-skip-existing", action="store_true")

    patch_expand = subparsers.add_parser(
        "expand-patches",
        help="Slice high-quality target PNGs into overlapping patch targets and optionally build pairs",
    )
    patch_expand.add_argument("--source-root", required=True)
    patch_expand.add_argument("--output-source-id", required=True)
    patch_expand.add_argument("--title", default="")
    patch_expand.add_argument("--author", default="")
    patch_expand.add_argument("--url", default="")
    patch_expand.add_argument("--license", default="Derived patch targets from user-authorized training source")
    patch_expand.add_argument("--license-url", default="")
    patch_expand.add_argument("--rights-basis", default="")
    patch_expand.add_argument("--patch-size", type=int, default=64)
    patch_expand.add_argument("--overlap", type=int, default=16)
    patch_expand.add_argument("--max-patches", type=int, default=0)
    patch_expand.add_argument("--max-patches-per-image", type=int, default=12)
    patch_expand.add_argument("--min-alpha-coverage", type=float, default=0.03)
    patch_expand.add_argument("--min-unique-colors", type=int, default=8)
    patch_expand.add_argument("--build-pairs", action="store_true")
    patch_expand.add_argument("--dry-run", action="store_true")

    crop_extract = subparsers.add_parser(
        "extract-character-crops",
        help="Extract single-character alpha connected-component crops from target sheets",
    )
    crop_extract.add_argument("--source-root", required=True)
    crop_extract.add_argument("--output-source-id", required=True)
    crop_extract.add_argument("--title", default="")
    crop_extract.add_argument("--author", default="")
    crop_extract.add_argument("--url", default="")
    crop_extract.add_argument("--license", default="Derived single-character crops from user-authorized training source")
    crop_extract.add_argument("--license-url", default="")
    crop_extract.add_argument("--rights-basis", default="")
    crop_extract.add_argument("--alpha-threshold", type=int, default=8)
    crop_extract.add_argument("--min-width", type=int, default=48)
    crop_extract.add_argument("--min-height", type=int, default=48)
    crop_extract.add_argument("--min-area", type=int, default=700)
    crop_extract.add_argument("--max-width", type=int, default=512)
    crop_extract.add_argument("--max-height", type=int, default=512)
    crop_extract.add_argument("--margin", type=int, default=8)
    crop_extract.add_argument("--max-crops", type=int, default=0)
    crop_extract.add_argument("--max-crops-per-image", type=int, default=24)
    crop_extract.add_argument("--build-pairs", action="store_true")
    crop_extract.add_argument("--dry-run", action="store_true")

    ai_pseudo = subparsers.add_parser(
        "generate-ai-pseudo",
        help="Use the configured image model to generate pseudo-AI inputs and pair them with true pixel targets",
    )
    ai_pseudo.add_argument("--target-root", default="")
    ai_pseudo.add_argument("--source-id", default="")
    ai_pseudo.add_argument("--category", default="")
    ai_pseudo.add_argument("--limit", type=int, default=5)
    ai_pseudo.add_argument("--variants", type=int, default=1)
    ai_pseudo.add_argument("--prompt", default="")
    ai_pseudo.add_argument(
        "--background",
        choices=["transparent", "opaque", "auto"],
        default="auto",
    )
    ai_pseudo.add_argument("--request-timeout", type=int, default=90)
    ai_pseudo.add_argument("--workers", type=int, default=4)
    ai_pseudo.add_argument("--min-width", type=int, default=0)
    ai_pseudo.add_argument("--min-height", type=int, default=0)
    ai_pseudo.add_argument("--max-width", type=int, default=0)
    ai_pseudo.add_argument("--max-height", type=int, default=0)
    ai_pseudo.add_argument(
        "--alpha-mode",
        choices=["preserve", "target_mask", "soft_target_mask"],
        default="soft_target_mask",
    )
    ai_pseudo.add_argument("--dry-run", action="store_true")
    ai_pseudo.add_argument("--no-skip-existing", action="store_true")

    subparsers.add_parser("summary", help="Print dataset summary")
    subparsers.add_parser("evaluate", help="Print dataset evaluation")
    subparsers.add_parser("list-sources", help="List source records")
    subparsers.add_parser("list-pairs", help="List pair records")

    eval_suite = subparsers.add_parser("build-eval-suite", help="Build a fixed Pixel Refiner visual eval suite")
    eval_suite.add_argument("--limit", type=int, default=32)
    eval_suite.add_argument("--source-id", default="")
    eval_suite.add_argument("--category", default="")
    eval_suite.add_argument("--input-kind", default="")
    eval_suite.add_argument("--keep-existing", action="store_true")

    contact_sheet = subparsers.add_parser("contact-sheet", help="Render the fixed eval suite contact sheet")
    contact_sheet.add_argument("--output", default="")
    contact_sheet.add_argument("--limit", type=int, default=0)
    contact_sheet.add_argument("--cell-size", type=int, default=160)

    fixed_eval_model = subparsers.add_parser("run-fixed-eval", help="Run the current Pixel Refiner service on the fixed eval suite")
    fixed_eval_model.add_argument("--service-url", default="http://127.0.0.1:8765")
    fixed_eval_model.add_argument("--model-dir", default="")
    fixed_eval_model.add_argument("--model-id", default="pixel-refiner-v4")
    fixed_eval_model.add_argument("--output-dir", default="")
    fixed_eval_model.add_argument("--limit", type=int, default=32)
    fixed_eval_model.add_argument("--cell-size", type=int, default=160)
    fixed_eval_model.add_argument("--strength", type=float, default=0.45)
    fixed_eval_model.add_argument("--palette-limit", type=int, default=64)
    fixed_eval_model.add_argument("--alpha-mode", default="preserve")
    fixed_eval_model.add_argument("--return-candidates", type=int, default=1)
    fixed_eval_model.add_argument("--timeout", type=int, default=300)
    fixed_eval_model.add_argument("--no-build-suite-if-empty", action="store_true")

    train_parser = subparsers.add_parser("train", help="Train Pixel Refiner and export an ONNX model package")
    train_parser.add_argument("--output-dir", default="")
    train_parser.add_argument("--model-id", default="pixel-refiner-v4")
    train_parser.add_argument("--architecture", default="")
    train_parser.add_argument("--epochs", type=int, default=2)
    train_parser.add_argument("--steps-per-epoch", type=int, default=800)
    train_parser.add_argument("--batch-size", type=int, default=8)
    train_parser.add_argument("--patch-size", type=int, default=0)
    train_parser.add_argument("--learning-rate", type=float, default=2.0e-4)
    train_parser.add_argument("--features", type=int, default=0)
    train_parser.add_argument("--seed", type=int, default=1337)
    train_parser.add_argument("--device", default="auto")
    train_parser.add_argument("--workers", type=int, default=0)
    train_parser.add_argument("--limit", type=int, default=0)
    train_parser.add_argument("--val-batches", type=int, default=16)
    train_parser.add_argument("--log-interval", type=int, default=25)
    train_parser.add_argument("--category", action="append", default=[])
    train_parser.add_argument("--input-kind", action="append", default=[])
    train_parser.add_argument("--palette-levels", type=int, default=64)
    train_parser.add_argument("--alpha-threshold", type=int, default=128)
    train_parser.add_argument("--pixel-constraint-weight", type=float, default=-1.0)
    train_parser.add_argument("--internal-scale", type=int, default=0)
    train_parser.add_argument("--tile-overlap", type=int, default=-1)
    train_parser.add_argument("--block-consistency-weight", type=float, default=-1.0)
    train_parser.add_argument("--edge-loss-weight", type=float, default=-1.0)
    train_parser.add_argument("--anti-blur-weight", type=float, default=-1.0)
    train_parser.add_argument("--software-candidate-weight", type=float, default=-1.0)
    train_parser.add_argument("--ai-pseudo-weight", type=float, default=-1.0)
    train_parser.add_argument("--grad-clip", type=float, default=1.0)
    train_parser.add_argument("--event-log", default="")
    train_parser.add_argument("--no-amp", action="store_true")

    smoke_parser = subparsers.add_parser("smoke-model", help="Run a local service smoke test against an exported model package")
    smoke_parser.add_argument("--model-dir", default="")
    smoke_parser.add_argument("--model-id", default="pixel-refiner-v4")
    smoke_parser.add_argument("--input", default="")
    smoke_parser.add_argument("--output-dir", default="")

    args = parser.parse_args(argv)
    ensure_dataset_dirs()

    if args.command == "add-source":
        add_source_record(
            PixelRefinerSourceRecord(
                source_id=args.source_id,
                title=args.title,
                author=args.author,
                url=args.url,
                license=args.license,
                license_url=args.license_url,
                ai_training_allowed=bool(args.allowed),
                category=args.category,
                notes=args.notes,
            )
        )
        print(json.dumps({"ok": True, "dataset_dir": str(dataset_dir())}, ensure_ascii=False, indent=2))
        return 0

    if args.command == "import-target":
        path = ingest_target_image(
            args.path,
            source_id=args.source_id,
            category=args.category,
            title=args.title,
            license=args.license,
            prompt=args.prompt,
            notes=args.notes,
        )
        print(json.dumps({"ok": True, "target": str(path)}, ensure_ascii=False, indent=2))
        return 0

    if args.command == "import-input":
        path = ingest_generated_input_image(
            args.path,
            source_id=args.source_id,
            category=args.category,
            input_kind=args.input_kind,
            prompt=args.prompt,
            notes=args.notes,
        )
        print(json.dumps({"ok": True, "input": str(path)}, ensure_ascii=False, indent=2))
        return 0

    if args.command == "make-pair":
        record = build_pair_record(
            target_path=args.target,
            input_path=args.input,
            source_id=args.source_id,
            category=args.category,
            input_kind=args.input_kind,
            prompt=args.prompt,
            license=args.license,
            notes=args.notes,
        )
        print(json.dumps({"ok": True, "pair_id": record.pair_id, "pair_path": str(record.target_path.parent)}, ensure_ascii=False, indent=2))
        return 0

    if args.command == "import-software-pair":
        result = ingest_software_candidate_pair(
            args.input,
            args.target,
            category=args.category,
            prompt=args.prompt,
            notes=args.notes,
            skip_existing=not bool(args.no_skip_existing),
        )
        print(
            json.dumps(
                {
                    "ok": True,
                    "created": result.created,
                    "pair_id": result.record.pair_id,
                    "pair_path": str(result.record.target_path.parent),
                    "input_kind": result.record.input_kind,
                    "category": result.record.category,
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return 0

    if args.command == "import-authorized-targets":
        stats = import_authorized_targets(
            AuthorizedImportConfig(
                input_dir=Path(args.input_dir).expanduser(),
                source_id=str(args.source_id or "").strip(),
                title=str(args.title or "").strip(),
                author=str(args.author or "").strip(),
                url=str(args.url or "").strip(),
                license=str(args.license or "").strip(),
                license_url=str(args.license_url or "").strip(),
                rights_basis=str(args.rights_basis or "").strip(),
                category=str(args.category or "auto").strip() or "auto",
                min_width=max(1, int(args.min_width)),
                min_height=max(1, int(args.min_height)),
                min_area=max(1, int(args.min_area)),
                max_images=max(0, int(args.max_images)),
                max_gif_frames=max(1, int(args.max_gif_frames)),
                build_pairs=bool(args.build_pairs),
                dry_run=bool(args.dry_run),
            )
        )
        if bool(args.generate_ai_pseudo) and not bool(args.dry_run) and int(stats.get("targets_imported", 0)) > 0:
            from gamedesigner.pixel_refiner_ai_pseudo import generate_ai_pseudo_pairs

            pseudo_limit = max(0, int(args.ai_pseudo_limit))
            if pseudo_limit <= 0:
                pseudo_limit = int(stats.get("targets_imported", 0))
            stats["ai_pseudo"] = generate_ai_pseudo_pairs(
                targets_dir() / str(args.source_id or "").strip(),
                source_id=str(args.source_id or "").strip(),
                limit=pseudo_limit,
                variants_per_target=max(1, int(args.ai_pseudo_variants)),
                prompt=str(args.ai_pseudo_prompt or "").strip(),
                background=args.background,
                alpha_mode=args.alpha_mode,
                request_timeout=max(1, int(args.request_timeout)),
                workers=max(1, int(args.ai_pseudo_workers)),
            )
        print(json.dumps(stats, ensure_ascii=False, indent=2))
        return 0

    if args.command == "crawl-authorized-site":
        stats = crawl_authorized_site(
            AuthorizedSiteCrawlConfig(
                start_urls=tuple(str(url or "").strip() for url in (args.start_url or []) if str(url or "").strip()),
                source_id=str(args.source_id or "").strip(),
                title=str(args.title or "").strip(),
                author=str(args.author or "").strip(),
                url=str(args.url or "").strip(),
                license=str(args.license or "").strip(),
                license_url=str(args.license_url or "").strip(),
                rights_basis=str(args.rights_basis or "").strip(),
                page_host=str(args.page_host or "").strip(),
                asset_host_contains=str(args.asset_host_contains or "").strip(),
                asset_path_contains=str(args.asset_path_contains or "").strip(),
                max_pages=max(1, int(args.max_pages)),
                max_workers=max(1, int(args.max_workers)),
                timeout=max(1, int(args.timeout)),
                retries=max(1, int(args.retries)),
                delay=max(0.0, float(args.delay)),
                min_width=max(1, int(args.min_width)),
                min_height=max(1, int(args.min_height)),
                min_area=max(1, int(args.min_area)),
                allow_opaque_targets=not bool(args.require_alpha),
                extract_gif_frames=not bool(args.no_gif_frames),
                max_gif_frames=max(1, int(args.max_gif_frames)),
                overwrite=bool(args.overwrite),
                build_pairs=bool(args.build_pairs),
                dry_run=bool(args.dry_run),
            )
        )
        if bool(args.generate_ai_pseudo) and not bool(args.dry_run) and int(stats.get("target_png_files", 0)) > 0:
            from gamedesigner.pixel_refiner_ai_pseudo import generate_ai_pseudo_pairs

            source_id = str(stats.get("source_id") or args.source_id or "").strip()
            pseudo_limit = max(0, int(args.ai_pseudo_limit))
            if pseudo_limit <= 0:
                pseudo_limit = int(stats.get("target_png_files", 0))
            stats["ai_pseudo"] = generate_ai_pseudo_pairs(
                targets_dir() / source_id,
                source_id=source_id,
                limit=pseudo_limit,
                variants_per_target=max(1, int(args.ai_pseudo_variants)),
                prompt=str(args.ai_pseudo_prompt or "").strip(),
                background=args.background,
                alpha_mode=args.alpha_mode,
                request_timeout=max(1, int(args.request_timeout)),
                workers=max(1, int(args.ai_pseudo_workers)),
            )
        print(json.dumps(stats, ensure_ascii=False, indent=2))
        return 0

    if args.command == "generate-inputs":
        outputs = generate_training_inputs_from_target(
            args.target,
            source_id=args.source_id,
            category=args.category,
        )
        print(json.dumps({"ok": True, "outputs": [{"path": str(path), "method": method} for path, method in outputs]}, ensure_ascii=False, indent=2))
        return 0

    if args.command == "build-pairs":
        stats = build_pairs_from_targets(
            args.target_root or None,
            source_id=str(args.source_id or "").strip(),
            category=str(args.category or "").strip(),
            skip_existing=not bool(args.no_skip_existing),
        )
        print(json.dumps({"ok": True, **stats}, ensure_ascii=False, indent=2))
        return 0

    if args.command == "expand-patches":
        stats = expand_target_patches(
            PatchExpansionConfig(
                source_root=Path(args.source_root),
                output_source_id=str(args.output_source_id or "").strip(),
                title=str(args.title or "").strip(),
                author=str(args.author or "").strip(),
                url=str(args.url or "").strip(),
                license=str(args.license or "").strip(),
                license_url=str(args.license_url or "").strip(),
                rights_basis=str(args.rights_basis or "").strip(),
                patch_size=max(8, int(args.patch_size)),
                overlap=max(0, int(args.overlap)),
                max_patches=max(0, int(args.max_patches)),
                max_patches_per_image=max(1, int(args.max_patches_per_image)),
                min_alpha_coverage=float(args.min_alpha_coverage),
                min_unique_colors=max(1, int(args.min_unique_colors)),
                build_pairs=bool(args.build_pairs),
                dry_run=bool(args.dry_run),
            )
        )
        print(json.dumps(stats, ensure_ascii=False, indent=2))
        return 0

    if args.command == "extract-character-crops":
        stats = extract_character_crops(
            CharacterCropConfig(
                source_root=Path(args.source_root),
                output_source_id=str(args.output_source_id or "").strip(),
                title=str(args.title or "").strip(),
                author=str(args.author or "").strip(),
                url=str(args.url or "").strip(),
                license=str(args.license or "").strip(),
                license_url=str(args.license_url or "").strip(),
                rights_basis=str(args.rights_basis or "").strip(),
                alpha_threshold=max(0, min(255, int(args.alpha_threshold))),
                min_width=max(1, int(args.min_width)),
                min_height=max(1, int(args.min_height)),
                min_area=max(1, int(args.min_area)),
                max_width=max(1, int(args.max_width)),
                max_height=max(1, int(args.max_height)),
                margin=max(0, int(args.margin)),
                max_crops=max(0, int(args.max_crops)),
                max_crops_per_image=max(1, int(args.max_crops_per_image)),
                build_pairs=bool(args.build_pairs),
                dry_run=bool(args.dry_run),
            )
        )
        print(json.dumps(stats, ensure_ascii=False, indent=2))
        return 0

    if args.command == "generate-ai-pseudo":
        from gamedesigner.pixel_refiner_ai_pseudo import generate_ai_pseudo_pairs

        stats = generate_ai_pseudo_pairs(
            args.target_root or None,
            source_id=str(args.source_id or "").strip(),
            category=str(args.category or "").strip(),
            limit=max(0, int(args.limit)),
            variants_per_target=max(1, int(args.variants)),
            prompt=str(args.prompt or "").strip(),
            background=args.background,
            alpha_mode=args.alpha_mode,
            request_timeout=max(1, int(args.request_timeout)),
            workers=max(1, int(args.workers)),
            skip_existing=not bool(args.no_skip_existing),
            dry_run=bool(args.dry_run),
            min_width=max(0, int(args.min_width)),
            min_height=max(0, int(args.min_height)),
            max_width=max(0, int(args.max_width)),
            max_height=max(0, int(args.max_height)),
        )
        print(json.dumps({"ok": True, **stats}, ensure_ascii=False, indent=2))
        return 0

    if args.command == "summary":
        print(json.dumps(summarize_dataset(), ensure_ascii=False, indent=2))
        return 0

    if args.command == "evaluate":
        print(json.dumps(evaluate_dataset(), ensure_ascii=False, indent=2))
        return 0

    if args.command == "list-sources":
        print(json.dumps([record.__dict__ for record in load_source_records()], ensure_ascii=False, indent=2, default=str))
        return 0

    if args.command == "list-pairs":
        print(json.dumps([record.__dict__ for record in load_pair_records()], ensure_ascii=False, indent=2, default=str))
        return 0

    if args.command == "build-eval-suite":
        print(
            json.dumps(
                build_fixed_eval_suite(
                    limit=max(1, int(args.limit)),
                    source_id=str(args.source_id or "").strip(),
                    category=str(args.category or "").strip(),
                    input_kind=str(args.input_kind or "").strip(),
                    rebuild=not bool(args.keep_existing),
                ),
                ensure_ascii=False,
                indent=2,
            )
        )
        return 0

    if args.command == "contact-sheet":
        print(
            json.dumps(
                render_fixed_eval_contact_sheet(
                    output_path=args.output or None,
                    limit=max(0, int(args.limit)),
                    cell_size=max(64, int(args.cell_size)),
                ),
                ensure_ascii=False,
                indent=2,
            )
        )
        return 0

    if args.command == "run-fixed-eval":
        print(
            json.dumps(
                run_fixed_eval_model(
                    service_url=str(args.service_url or "").strip() or "http://127.0.0.1:8765",
                    model_dir=Path(args.model_dir).expanduser() if str(args.model_dir or "").strip() else None,
                    model_id=str(args.model_id or "").strip() or "pixel-refiner-v4",
                    output_dir=Path(args.output_dir).expanduser() if str(args.output_dir or "").strip() else None,
                    limit=max(1, int(args.limit)),
                    cell_size=max(64, int(args.cell_size)),
                    strength=float(args.strength),
                    palette_limit=max(0, int(args.palette_limit)),
                    alpha_mode=str(args.alpha_mode or "preserve").strip() or "preserve",
                    return_candidates=max(1, min(8, int(args.return_candidates))),
                    timeout=max(1, int(args.timeout)),
                    build_suite_if_empty=not bool(args.no_build_suite_if_empty),
                ),
                ensure_ascii=False,
                indent=2,
            )
        )
        return 0

    if args.command == "train":
        from gamedesigner.paths import pixel_refiner_model_dir
        from gamedesigner.pixel_refiner_training import (
            INPUT_KIND_SAMPLE_WEIGHTS,
            MODEL_ID,
            V2_MODEL_ID,
            V3_MODEL_ID,
            V4_MODEL_ID,
            V41_AI_PSEUDO_WEIGHT,
            V41_MODEL_ID,
            V41_SOFTWARE_CANDIDATE_WEIGHT,
            PixelRefinerTrainConfig,
            train_pixel_refiner,
        )

        model_id = str(args.model_id or MODEL_ID).strip() or MODEL_ID
        architecture = str(args.architecture or "").strip()
        if not architecture:
            if model_id.startswith("pixel-refiner-v4"):
                architecture = "pixel-hard-v4"
            elif model_id == V3_MODEL_ID:
                architecture = "pixel-tile-v3"
            else:
                architecture = "unet-naf-v2" if model_id == V2_MODEL_ID else "cnn-v1"
        patch_size = int(args.patch_size)
        if patch_size <= 0:
            patch_size = 64 if model_id == V3_MODEL_ID or model_id.startswith("pixel-refiner-v4") else 128
        default_features = 96 if model_id.startswith("pixel-refiner-v4") else (64 if model_id in {V2_MODEL_ID, V3_MODEL_ID} else 48)
        pixel_constraint_weight = float(args.pixel_constraint_weight)
        if pixel_constraint_weight < 0:
            pixel_constraint_weight = 0.12 if model_id.startswith("pixel-refiner-v4") else 0.08
        tile_overlap = int(args.tile_overlap)
        if tile_overlap < 0:
            tile_overlap = 16 if model_id == V3_MODEL_ID or model_id.startswith("pixel-refiner-v4") else 0
        block_consistency_weight = float(args.block_consistency_weight)
        if block_consistency_weight < 0:
            if model_id.startswith("pixel-refiner-v4"):
                block_consistency_weight = 0.25
            elif model_id == V3_MODEL_ID:
                block_consistency_weight = 0.20
            else:
                block_consistency_weight = 0.0
        edge_loss_weight = float(args.edge_loss_weight)
        if edge_loss_weight < 0:
            edge_loss_weight = 0.55 if model_id.startswith("pixel-refiner-v4") else 0.25
        anti_blur_weight = float(args.anti_blur_weight)
        if anti_blur_weight < 0:
            anti_blur_weight = 0.12 if model_id.startswith("pixel-refiner-v4") else 0.0
        software_candidate_weight = float(args.software_candidate_weight)
        if software_candidate_weight < 0:
            software_candidate_weight = (
                V41_SOFTWARE_CANDIDATE_WEIGHT
                if model_id == V41_MODEL_ID
                else INPUT_KIND_SAMPLE_WEIGHTS["software_candidate"]
            )
        ai_pseudo_weight = float(args.ai_pseudo_weight)
        if ai_pseudo_weight < 0:
            ai_pseudo_weight = (
                V41_AI_PSEUDO_WEIGHT
                if model_id == V41_MODEL_ID
                else INPUT_KIND_SAMPLE_WEIGHTS["ai_pseudo"]
            )

        config = PixelRefinerTrainConfig(
            output_dir=Path(args.output_dir).expanduser() if args.output_dir else pixel_refiner_model_dir(model_id),
            model_id=model_id,
            architecture=architecture,
            epochs=max(1, int(args.epochs)),
            steps_per_epoch=max(1, int(args.steps_per_epoch)),
            batch_size=max(1, int(args.batch_size)),
            patch_size=max(16, patch_size),
            learning_rate=float(args.learning_rate),
            features=max(8, int(args.features)) if int(args.features) > 0 else default_features,
            seed=int(args.seed),
            device=str(args.device or "auto"),
            num_workers=max(0, int(args.workers)),
            limit=max(0, int(args.limit)),
            val_batches=max(1, int(args.val_batches)),
            log_interval=max(1, int(args.log_interval)),
            amp=not bool(args.no_amp),
            categories=tuple(str(item).strip() for item in args.category if str(item).strip()),
            input_kinds=tuple(str(item).strip() for item in args.input_kind if str(item).strip()),
            palette_levels=max(2, int(args.palette_levels)),
            alpha_threshold=max(0, min(255, int(args.alpha_threshold))),
            pixel_constraint_weight=max(0.0, pixel_constraint_weight),
            internal_scale=max(1, int(args.internal_scale) if int(args.internal_scale) > 0 else (2 if model_id == V3_MODEL_ID or model_id.startswith("pixel-refiner-v4") else 1)),
            tile_overlap=max(0, tile_overlap),
            block_consistency_weight=max(0.0, block_consistency_weight),
            edge_loss_weight=max(0.0, edge_loss_weight),
            anti_blur_weight=max(0.0, anti_blur_weight),
            software_candidate_weight=max(0.0, software_candidate_weight),
            ai_pseudo_weight=max(0.0, ai_pseudo_weight),
            grad_clip=max(0.0, float(args.grad_clip)),
            event_log_path=Path(args.event_log).expanduser() if str(args.event_log or "").strip() else None,
        )
        print(json.dumps(train_pixel_refiner(config), ensure_ascii=False, indent=2))
        return 0

    if args.command == "smoke-model":
        from pixel_refiner_service.server import PixelRefinerService
        from pixel_refiner_service.manifest import default_model_dir

        records = load_pair_records()
        first_record = next((record for record in records if record.input_path.is_file()), None)
        input_path = Path(args.input).expanduser() if args.input else (first_record.input_path if first_record else Path())
        if not input_path.is_file():
            raise FileNotFoundError(input_path)
        target_size = f"{first_record.width}x{first_record.height}" if first_record and not args.input else _image_size_text(input_path)
        model_dir = Path(args.model_dir).expanduser() if args.model_dir else default_model_dir()
        output_dir = Path(args.output_dir).expanduser() if args.output_dir else model_dir / "smoke_outputs"
        model_id = str(args.model_id or "pixel-refiner-v4").strip() or "pixel-refiner-v4"
        service = PixelRefinerService(model_dir, model_id=model_id)
        response = service.refine(
            {
                "input_path": str(input_path),
                "output_dir": str(output_dir),
                "target_size": target_size,
                "alpha_mode": "preserve",
                "palette_limit": 128,
                "strength": 0.45,
                "return_candidates": 1,
                "model": {"id": model_id, "dir": str(model_dir)},
            }
        )
        print(json.dumps(response, ensure_ascii=False, indent=2))
        return 0

    return 1


def _image_size_text(path: Path) -> str:
    from PIL import Image, ImageOps

    with Image.open(path) as loaded:
        image = ImageOps.exif_transpose(loaded)
        return f"{image.width}x{image.height}"


if __name__ == "__main__":
    raise SystemExit(main())
