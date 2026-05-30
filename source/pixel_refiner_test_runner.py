from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from gamedesigner.pixel_refiner import DEFAULT_PIXEL_REFINER_MODEL_ID, PixelRefinerRequest, refine_pixel_art_with_service


def run_refine_test(args: argparse.Namespace) -> dict[str, Any]:
    input_path = Path(args.input).expanduser()
    output_dir = Path(args.output_dir).expanduser()
    model_dir = Path(args.model_dir).expanduser() if str(args.model_dir or "").strip() else None
    request = PixelRefinerRequest(
        input_path=input_path,
        output_dir=output_dir,
        target_size=str(args.target_size or "").strip(),
        alpha_mode=str(args.alpha_mode or "preserve").strip(),
        palette_limit=max(0, int(args.palette_limit)),
        strength=max(0.0, min(1.0, float(args.strength))),
        return_candidates=max(1, min(8, int(args.return_candidates))),
        model_dir=model_dir,
        model_id=str(args.model_id or "").strip(),
    )
    result = refine_pixel_art_with_service(
        request,
        service_url=str(args.service_url or "").strip(),
        timeout=max(1, int(args.timeout)),
    )
    outputs = []
    for output in result.outputs:
        path = Path(output.path)
        item: dict[str, Any] = {
            "path": str(path),
            "label": output.label,
            "exists": path.is_file(),
        }
        try:
            item["bytes"] = path.stat().st_size
        except OSError:
            item["bytes"] = 0
        outputs.append(item)
    return {
        "ok": True,
        "model": result.model,
        "input_path": str(input_path),
        "output_dir": str(output_dir),
        "target_size": str(args.target_size or ""),
        "outputs": outputs,
        "checks": result.checks,
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run a single Pixel Refiner test request")
    parser.add_argument("--service-url", default="http://127.0.0.1:8765")
    parser.add_argument("--input", required=True)
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--target-size", required=True)
    parser.add_argument("--model-id", default=DEFAULT_PIXEL_REFINER_MODEL_ID)
    parser.add_argument("--model-dir", default="")
    parser.add_argument("--alpha-mode", default="preserve")
    parser.add_argument("--palette-limit", type=int, default=64)
    parser.add_argument("--strength", type=float, default=0.45)
    parser.add_argument("--return-candidates", type=int, default=1)
    parser.add_argument("--timeout", type=int, default=300)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        payload = run_refine_test(args)
    except Exception as exc:
        print(
            json.dumps(
                {
                    "ok": False,
                    "message": str(exc),
                    "type": type(exc).__name__,
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return 1
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
