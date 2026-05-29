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
from gamedesigner.pixel_refiner_pair_generation import build_pairs_from_targets, generate_training_inputs_from_target


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

    train_parser = subparsers.add_parser("train", help="Train Pixel Refiner and export an ONNX model package")
    train_parser.add_argument("--output-dir", default="")
    train_parser.add_argument("--model-id", default="pixel-refiner-v2")
    train_parser.add_argument("--architecture", default="")
    train_parser.add_argument("--epochs", type=int, default=2)
    train_parser.add_argument("--steps-per-epoch", type=int, default=800)
    train_parser.add_argument("--batch-size", type=int, default=8)
    train_parser.add_argument("--patch-size", type=int, default=128)
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
    train_parser.add_argument("--pixel-constraint-weight", type=float, default=0.08)
    train_parser.add_argument("--no-amp", action="store_true")

    smoke_parser = subparsers.add_parser("smoke-model", help="Run a local service smoke test against an exported model package")
    smoke_parser.add_argument("--model-dir", default="")
    smoke_parser.add_argument("--model-id", default="pixel-refiner-v2")
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

    if args.command == "train":
        from gamedesigner.paths import pixel_refiner_model_dir
        from gamedesigner.pixel_refiner_training import MODEL_ID, V2_MODEL_ID, PixelRefinerTrainConfig, train_pixel_refiner

        model_id = str(args.model_id or MODEL_ID).strip() or MODEL_ID
        architecture = str(args.architecture or "").strip()
        if not architecture:
            architecture = "unet-naf-v2" if model_id == V2_MODEL_ID else "cnn-v1"

        config = PixelRefinerTrainConfig(
            output_dir=Path(args.output_dir).expanduser() if args.output_dir else pixel_refiner_model_dir(model_id),
            model_id=model_id,
            architecture=architecture,
            epochs=max(1, int(args.epochs)),
            steps_per_epoch=max(1, int(args.steps_per_epoch)),
            batch_size=max(1, int(args.batch_size)),
            patch_size=max(16, int(args.patch_size)),
            learning_rate=float(args.learning_rate),
            features=max(8, int(args.features)) if int(args.features) > 0 else (64 if model_id == V2_MODEL_ID else 48),
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
            pixel_constraint_weight=max(0.0, float(args.pixel_constraint_weight)),
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
        model_id = str(args.model_id or "pixel-refiner-v2").strip() or "pixel-refiner-v2"
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
