import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from PIL import Image

from pixel_refiner_service.backend import (
    RefineOutput,
    _apply_pixel_art_cleanup,
    _apply_strength_preserving_refine,
    _candidate_specs,
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

    def test_single_model_output_expands_to_strength_variants(self) -> None:
        image = Image.new("RGBA", (2, 2), (120, 80, 200, 255))

        variants = _candidate_specs([image], return_candidates=4, strength=0.65)

        self.assertEqual(len(variants), 4)
        self.assertEqual([variant[2] for variant in variants], ["只清理", "保守", "强化", "标准"])
        self.assertEqual(variants[-1][1], 0.65)

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
                load_model_manifest(model_dir)

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
                service = PixelRefinerService(model_dir)

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
