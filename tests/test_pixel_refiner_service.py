import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from PIL import Image

from pixel_refiner_service.backend import (
    RefineOutput,
    _apply_pixel_art_hard_output_layer,
    _apply_pixel_art_cleanup,
    _apply_strength_preserving_refine,
    _candidate_specs,
    _downscale_pixel_blocks,
    _tile_positions,
)
from pixel_refiner_service.manifest import load_model_manifest
from pixel_refiner_service.server import PixelRefinerService


def _pixels(image: Image.Image) -> list[tuple[int, ...]]:
    if hasattr(image, "get_flattened_data"):
        return [tuple(pixel) for pixel in image.get_flattened_data()]
    return [tuple(pixel) for pixel in image.getdata()]


class PixelRefinerServiceTests(unittest.TestCase):
    def test_strength_preserving_refine_zero_returns_selected_input(self) -> None:
        source = Image.new("RGBA", (3, 3), (240, 96, 48, 255))
        source.putpixel((0, 0), (0, 0, 0, 0))
        source.putpixel((1, 1), (8, 10, 12, 255))
        model = Image.new("RGBA", (3, 3), (120, 120, 120, 255))

        refined = _apply_strength_preserving_refine(source, model, strength=0.0, alpha_mode="preserve")

        self.assertEqual(_pixels(refined), _pixels(source))

    def test_strength_preserving_refine_preserves_alpha_and_dark_outlines(self) -> None:
        source = Image.new("RGBA", (5, 5), (230, 210, 180, 255))
        for index in range(5):
            source.putpixel((index, 2), (10, 12, 14, 255))
        source.putpixel((0, 0), (0, 0, 0, 0))
        model = Image.new("RGBA", (5, 5), (210, 210, 210, 255))

        refined = _apply_strength_preserving_refine(source, model, strength=1.0, alpha_mode="preserve")

        self.assertEqual(refined.getpixel((0, 0))[3], 0)
        self.assertEqual(refined.getpixel((2, 2))[3], 255)
        self.assertLess(refined.getpixel((2, 2))[0], 90)

    def test_pixel_art_cleanup_snaps_to_selected_input_palette(self) -> None:
        source = Image.new("RGBA", (2, 2))
        source.putdata(
            [
                (240, 80, 40, 255),
                (40, 90, 220, 255),
                (240, 80, 40, 255),
                (0, 0, 0, 0),
            ]
        )
        candidate = Image.new("RGBA", (2, 2))
        candidate.putdata(
            [
                (250, 70, 50, 255),
                (30, 100, 210, 255),
                (128, 128, 128, 255),
                (80, 90, 100, 40),
            ]
        )

        cleaned = _apply_pixel_art_cleanup(
            candidate,
            palette_limit=2,
            alpha_threshold=128,
            source_image=source,
        )

        opaque_colors = {
            pixel[:3]
            for pixel in _pixels(cleaned)
            if pixel[3] >= 128
        }
        self.assertLessEqual(opaque_colors, {(240, 80, 40), (40, 90, 220)})
        self.assertEqual(cleaned.getpixel((1, 1)), (0, 0, 0, 0))

    def test_hard_output_layer_quantizes_model_palette_and_removes_isolated_alpha_noise(self) -> None:
        candidate = Image.new("RGBA", (5, 5), (0, 0, 0, 0))
        for x in (3, 4):
            for y in (3, 4):
                candidate.putpixel((x, y), (120 + x, 80 + y, 200, 255))
        candidate.putpixel((0, 0), (10, 20, 30, 255))
        candidate.putpixel((2, 0), (0, 0, 0, 40))

        cleaned = _apply_pixel_art_hard_output_layer(
            candidate,
            palette_limit=2,
            alpha_threshold=128,
            palette_strategy="model_quantized",
            cluster_cleanup=True,
        )

        opaque_colors = {pixel[:3] for pixel in _pixels(cleaned) if pixel[3] >= 128}
        self.assertLessEqual(len(opaque_colors), 2)
        self.assertEqual(cleaned.getpixel((0, 0))[3], 0)
        self.assertEqual(cleaned.getpixel((2, 0))[3], 0)

    def test_single_model_output_expands_to_strength_variants(self) -> None:
        image = Image.new("RGBA", (2, 2), (120, 80, 200, 255))

        variants = _candidate_specs([image], return_candidates=4, strength=0.65)

        self.assertEqual(len(variants), 4)
        self.assertEqual([variant[2] for variant in variants], ["只清理", "保守", "强化", "标准"])
        self.assertEqual(variants[-1][1], 0.65)

    def test_tiled_helpers_keep_pixel_grid_alignment(self) -> None:
        self.assertEqual(_tile_positions(150, 64, 16), [0, 48, 86])
        upscaled = Image.new("RGBA", (4, 4))
        upscaled.putdata(
            [
                (10, 20, 30, 255), (14, 24, 34, 255), (100, 0, 0, 255), (104, 4, 4, 255),
                (12, 22, 32, 255), (16, 26, 36, 255), (102, 2, 2, 255), (106, 6, 6, 255),
                (0, 100, 0, 255), (4, 104, 4, 255), (0, 0, 100, 255), (4, 4, 104, 255),
                (2, 102, 2, 255), (6, 106, 6, 255), (2, 2, 102, 255), (6, 6, 106, 255),
            ]
        )

        downscaled = _downscale_pixel_blocks(upscaled, 2, (2, 2))

        self.assertEqual(downscaled.size, (2, 2))
        self.assertEqual(downscaled.getpixel((0, 0)), (13, 23, 33, 255))
        self.assertEqual(downscaled.getpixel((1, 1)), (3, 3, 103, 255))

    def test_manifest_reads_tiled_v3_fields(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            model_dir = Path(folder) / "pixel-refiner-v3"
            weights_dir = model_dir / "weights"
            weights_dir.mkdir(parents=True)
            (weights_dir / "pixel_refiner_v3.onnx").write_bytes(b"fake")
            (model_dir / "model_manifest.json").write_text(
                json.dumps(
                    {
                        "id": "pixel-refiner-v3",
                        "version": "0.1.0",
                        "runtime": "onnxruntime",
                        "weights": "weights/pixel_refiner_v3.onnx",
                        "tiled_inference": True,
                        "tile_size": 64,
                        "tile_overlap": 16,
                        "internal_scale": 2,
                    }
                ),
                encoding="utf-8",
            )

            manifest = load_model_manifest(model_dir, expected_id="pixel-refiner-v3")

            self.assertTrue(manifest.tiled_inference)
            self.assertEqual(manifest.tile_size, 64)
            self.assertEqual(manifest.tile_overlap, 16)
            self.assertEqual(manifest.internal_scale, 2)

    def test_manifest_reads_v4_hard_output_fields(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            model_dir = Path(folder) / "pixel-refiner-v4"
            weights_dir = model_dir / "weights"
            weights_dir.mkdir(parents=True)
            (weights_dir / "pixel_refiner_v4.onnx").write_bytes(b"fake")
            (model_dir / "model_manifest.json").write_text(
                json.dumps(
                    {
                        "id": "pixel-refiner-v4",
                        "version": "0.1.0",
                        "runtime": "onnxruntime",
                        "weights": "weights/pixel_refiner_v4.onnx",
                        "hard_pixel_output": True,
                        "output_layer_version": "v4-hard-pixel",
                        "palette_strategy": "model_quantized",
                        "cluster_cleanup": True,
                    }
                ),
                encoding="utf-8",
            )

            manifest = load_model_manifest(model_dir, expected_id="pixel-refiner-v4")

            self.assertTrue(manifest.hard_pixel_output)
            self.assertEqual(manifest.output_layer_version, "v4-hard-pixel")
            self.assertEqual(manifest.palette_strategy, "model_quantized")
            self.assertTrue(manifest.cluster_cleanup)

    def test_health_reports_not_ready_when_model_package_is_missing(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            service = PixelRefinerService(Path(folder) / "missing")

            health = service.health()

            self.assertFalse(health["ok"])
            self.assertIn("模型包未安装", health["message"])

    def test_manifest_requires_real_weight_file(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            model_dir = Path(folder) / "pixel-refiner-v2"
            model_dir.mkdir()
            (model_dir / "model_manifest.json").write_text(
                json.dumps(
                    {
                        "id": "pixel-refiner-v2",
                        "version": "0.1.0",
                        "runtime": "onnxruntime",
                        "weights": "weights/pixel_refiner_v2.onnx",
                    }
                ),
                encoding="utf-8",
            )

            with self.assertRaisesRegex(Exception, "模型权重文件不存在"):
                load_model_manifest(model_dir, expected_id="pixel-refiner-v2")

    def test_service_refine_uses_backend_and_returns_output_paths(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            model_dir = root / "pixel-refiner-v2"
            weights_dir = model_dir / "weights"
            weights_dir.mkdir(parents=True)
            (weights_dir / "pixel_refiner_v2.onnx").write_bytes(b"fake")
            (model_dir / "model_manifest.json").write_text(
                json.dumps(
                    {
                        "id": "pixel-refiner-v2",
                        "version": "0.1.0",
                        "runtime": "onnxruntime",
                        "weights": "weights/pixel_refiner_v2.onnx",
                        "target_sizes": ["8x8"],
                        "alpha_modes": ["preserve"],
                    }
                ),
                encoding="utf-8",
            )
            input_path = root / "input.png"
            output_dir = root / "outputs"
            Image.new("RGBA", (8, 8), (120, 80, 200, 255)).save(input_path)

            class FakeBackend:
                def refine(self, job):
                    self.job = job
                    output_dir.mkdir(exist_ok=True)
                    output = job.output_dir / "refined.png"
                    Image.new("RGBA", (8, 8), (20, 40, 80, 255)).save(output)
                    return [RefineOutput(output, "Fake refined")]

            fake_backend = FakeBackend()
            with mock.patch("pixel_refiner_service.server.build_backend", return_value=fake_backend):
                service = PixelRefinerService(model_dir, model_id="pixel-refiner-v2")

            response = service.refine(
                {
                    "input_path": str(input_path),
                    "output_dir": str(output_dir),
                    "target_size": "8x8",
                    "alpha_mode": "preserve",
                    "palette_limit": 48,
                    "strength": 0.5,
                    "return_candidates": 1,
                    "model": {"id": "pixel-refiner-v2", "dir": str(model_dir)},
                }
            )

            self.assertTrue(response["ok"])
            self.assertEqual(response["outputs"][0]["label"], "Fake refined")
            self.assertTrue(Path(response["outputs"][0]["path"]).exists())
            self.assertEqual(fake_backend.job.target_size, (8, 8))
            self.assertEqual(fake_backend.job.palette_limit, 48)
            stats = service.stats()
            self.assertEqual(stats["request_count"], 1)
            self.assertEqual(stats["last_target_size"], "8x8")
            self.assertIn("refined.png", stats["last_output_paths"][0])
            self.assertEqual(stats["last_error"], "")


if __name__ == "__main__":
    unittest.main()
