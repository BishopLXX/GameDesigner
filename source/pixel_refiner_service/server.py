from __future__ import annotations

import argparse
import json
import os
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import Lock
from time import perf_counter
from typing import Any

from . import __version__
from .backend import RefineJob, build_backend, parse_target_size
from .errors import ModelPackageError, PixelRefinerServiceError, RequestValidationError
from .manifest import DEFAULT_MODEL_ID, ModelManifest, default_model_dir, load_model_manifest


DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8765
MAX_REQUEST_BYTES = 1024 * 1024
HEALTH_PATH = "/v1/health"
STATS_PATH = "/v1/stats"
REFINE_PATH = "/v1/pixel/refine"


class PixelRefinerService:
    def __init__(self, model_dir: Path | None = None, *, model_id: str = DEFAULT_MODEL_ID) -> None:
        self.model_dir = model_dir or default_model_dir()
        self.model_id = model_id or DEFAULT_MODEL_ID
        self._manifest: ModelManifest | None = None
        self._backend = None
        self._startup_error = ""
        self._started_at = _timestamp()
        self._stats_lock = Lock()
        self._request_count = 0
        self._last_request_at = ""
        self._last_input_path = ""
        self._last_output_dir = ""
        self._last_target_size = ""
        self._last_output_paths: list[str] = []
        self._last_error = ""
        self._last_duration_ms = 0
        self.reload()

    def reload(self) -> None:
        self._manifest = None
        self._backend = None
        self._startup_error = ""
        try:
            manifest = load_model_manifest(self.model_dir, expected_id=self.model_id)
            self._backend = build_backend(manifest)
            self._manifest = manifest
        except PixelRefinerServiceError as exc:
            self._startup_error = str(exc)

    @property
    def ready(self) -> bool:
        return self._manifest is not None and self._backend is not None

    def health(self) -> dict[str, Any]:
        manifest = self._manifest
        return {
            "ok": self.ready,
            "service": "GameDesigner Pixel Refiner",
            "version": __version__,
            "model": manifest.id if manifest else self.model_id,
            "model_dir": str(self.model_dir),
            "runtime": manifest.runtime if manifest else "",
            "message": "" if self.ready else self._startup_error,
        }

    def stats(self) -> dict[str, Any]:
        with self._stats_lock:
            return {
                **self.health(),
                "started_at": self._started_at,
                "request_count": self._request_count,
                "last_request_at": self._last_request_at,
                "last_input_path": self._last_input_path,
                "last_output_dir": self._last_output_dir,
                "last_target_size": self._last_target_size,
                "last_output_paths": list(self._last_output_paths),
                "last_error": self._last_error,
                "last_duration_ms": self._last_duration_ms,
            }

    def refine(self, payload: dict[str, Any]) -> dict[str, Any]:
        started = perf_counter()
        try:
            if not self.ready or self._backend is None or self._manifest is None:
                raise ModelPackageError(self._startup_error or "Pixel Refiner 模型包尚未就绪。")
            model = payload.get("model") if isinstance(payload.get("model"), dict) else {}
            requested_model_id = str(model.get("id") or self.model_id).strip()
            if requested_model_id and requested_model_id != self._manifest.id:
                raise RequestValidationError(f"当前服务加载的是 {self._manifest.id}，请求的是 {requested_model_id}。")
            job = _job_from_payload(payload, self._manifest)
            outputs = self._backend.refine(job)
            response = {
                "ok": True,
                "model": self._manifest.id,
                "outputs": [{"path": str(output.path), "label": output.label} for output in outputs],
                "checks": {
                    "transparent_png": True,
                    "grid_aligned": True,
                },
            }
            self._record_request(payload, response=response, duration_ms=_elapsed_ms(started))
            return response
        except Exception as exc:
            self._record_request(payload, error=str(exc), duration_ms=_elapsed_ms(started))
            raise

    def _record_request(
        self,
        payload: dict[str, Any],
        *,
        response: dict[str, Any] | None = None,
        error: str = "",
        duration_ms: int,
    ) -> None:
        outputs = response.get("outputs") if isinstance(response, dict) else []
        output_paths = [
            str(item.get("path") or "")
            for item in outputs
            if isinstance(item, dict) and str(item.get("path") or "").strip()
        ]
        with self._stats_lock:
            self._request_count += 1
            self._last_request_at = _timestamp()
            self._last_input_path = str(payload.get("input_path") or "")
            self._last_output_dir = str(payload.get("output_dir") or "")
            self._last_target_size = str(payload.get("target_size") or "")
            self._last_output_paths = output_paths
            self._last_error = error
            self._last_duration_ms = int(duration_ms)
        print(
            json.dumps(
                {
                    "event": "refine",
                    "ok": not bool(error),
                    "request_count": self._request_count,
                    "input_path": self._last_input_path,
                    "output_paths": output_paths,
                    "target_size": self._last_target_size,
                    "duration_ms": duration_ms,
                    "error": error,
                },
                ensure_ascii=False,
            ),
            flush=True,
        )



def run_server(host: str = DEFAULT_HOST, port: int = DEFAULT_PORT, model_dir: Path | None = None, model_id: str = DEFAULT_MODEL_ID) -> None:
    service = PixelRefinerService(model_dir, model_id=model_id)

    class Handler(PixelRefinerRequestHandler):
        pixel_refiner_service = service

    httpd = ThreadingHTTPServer((host, port), Handler)
    print(f"Pixel Refiner listening on http://{host}:{port}", flush=True)
    print(json.dumps(service.health(), ensure_ascii=False), flush=True)
    httpd.serve_forever()


class PixelRefinerRequestHandler(BaseHTTPRequestHandler):
    pixel_refiner_service: PixelRefinerService
    server_version = "GameDesignerPixelRefiner/0.1"

    def do_GET(self) -> None:  # noqa: N802
        path = self.path.rstrip("/")
        if path == HEALTH_PATH:
            self._send_json(200, self.pixel_refiner_service.health())
            return
        if path == STATS_PATH:
            self._send_json(200, self.pixel_refiner_service.stats())
            return
        self._send_json(404, {"ok": False, "message": "Not found"})

    def do_POST(self) -> None:  # noqa: N802
        if self.path.rstrip("/") != REFINE_PATH:
            self._send_json(404, {"ok": False, "message": "Not found"})
            return
        try:
            payload = self._read_json()
            response = self.pixel_refiner_service.refine(payload)
        except PixelRefinerServiceError as exc:
            self._send_json(getattr(exc, "status_code", 400), {"ok": False, "message": str(exc)})
            return
        except Exception as exc:  # pragma: no cover - defensive request boundary.
            self._send_json(500, {"ok": False, "message": f"Pixel Refiner 服务内部错误：{exc}"})
            return
        self._send_json(200, response)

    def log_message(self, format: str, *args) -> None:  # noqa: A002
        if not os.getenv("PIXEL_REFINER_VERBOSE"):
            return
        path = self.path.split("?", 1)[0].rstrip("/")
        if path in {HEALTH_PATH, STATS_PATH}:
            return
        super().log_message(format, *args)

    def _read_json(self) -> dict[str, Any]:
        length = int(self.headers.get("Content-Length") or "0")
        if length <= 0 or length > MAX_REQUEST_BYTES:
            raise RequestValidationError("请求体大小不合法。")
        raw = self.rfile.read(length)
        try:
            payload = json.loads(raw.decode("utf-8-sig"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise RequestValidationError("请求体必须是 JSON。") from exc
        if not isinstance(payload, dict):
            raise RequestValidationError("请求体必须是 JSON object。")
        return payload

    def _send_json(self, status: int, payload: dict[str, Any]) -> None:
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)


def _job_from_payload(payload: dict[str, Any], manifest: ModelManifest) -> RefineJob:
    input_path = Path(str(payload.get("input_path") or "")).expanduser()
    output_dir = Path(str(payload.get("output_dir") or "")).expanduser()
    if not input_path.is_file():
        raise RequestValidationError(f"输入图不存在：{input_path}")
    if not output_dir:
        raise RequestValidationError("缺少 output_dir。")
    target_text = str(payload.get("target_size") or "")
    target_size = parse_target_size(target_text)
    if manifest.target_sizes and target_text not in manifest.target_sizes:
        raise RequestValidationError(f"模型包不支持目标尺寸：{target_text}")
    alpha_mode = str(payload.get("alpha_mode") or "preserve")
    if alpha_mode not in manifest.alpha_modes:
        raise RequestValidationError(f"模型包不支持 alpha_mode：{alpha_mode}")
    output_dir.mkdir(parents=True, exist_ok=True)
    return RefineJob(
        input_path=input_path,
        output_dir=output_dir,
        target_size=target_size,
        alpha_mode=alpha_mode,
        palette_limit=_coerce_int(payload.get("palette_limit"), 0, 512, 0),
        strength=_coerce_float(payload.get("strength"), 0.0, 1.0, 0.45),
        return_candidates=_coerce_int(payload.get("return_candidates"), 1, 8, 4),
    )


def _coerce_int(value: Any, minimum: int, maximum: int, fallback: int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return fallback
    return min(maximum, max(minimum, parsed))


def _coerce_float(value: Any, minimum: float, maximum: float, fallback: float) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return fallback
    return min(maximum, max(minimum, parsed))


def _timestamp() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _elapsed_ms(started: float) -> int:
    return int(max(0.0, perf_counter() - started) * 1000)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="GameDesigner Pixel Refiner local service")
    parser.add_argument("--host", default=DEFAULT_HOST)
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    parser.add_argument("--model-dir", type=Path, default=default_model_dir())
    parser.add_argument("--model-id", default=DEFAULT_MODEL_ID)
    args = parser.parse_args(argv)
    run_server(args.host, args.port, args.model_dir, args.model_id)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
