import argparse
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from PySide6.QtCore import QMimeData, QUrl

from gamedesigner.pixel_refiner import PixelRefinerOutput, PixelRefinerResult
from pixel_refiner_service_window import _first_supported_image_path_from_mime, _fit_target_size, _is_supported_image_path, _safe_stem
from pixel_refiner_test_runner import run_refine_test


class PixelRefinerConsoleTests(unittest.TestCase):
    def test_fit_target_size_preserves_small_images_and_clamps_large_edges(self) -> None:
        self.assertEqual(_fit_target_size(256, 384), (256, 384))
        self.assertEqual(_fit_target_size(2048, 1024), (1024, 512))

    def test_safe_stem_removes_path_unsafe_characters(self) -> None:
        self.assertEqual(_safe_stem("dragon hero:run/01"), "dragon_hero_run_01")
        self.assertEqual(_safe_stem(""), "image")

    def test_supported_image_drag_mime_returns_first_local_image(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            text_path = root / "notes.txt"
            image_path = root / "hero.PNG"
            text_path.write_text("ignore", encoding="utf-8")
            image_path.write_bytes(b"fake")

            mime = QMimeData()
            mime.setUrls([QUrl.fromLocalFile(str(text_path)), QUrl.fromLocalFile(str(image_path))])

            self.assertTrue(_is_supported_image_path(image_path))
            self.assertEqual(_first_supported_image_path_from_mime(mime), str(image_path))

    def test_non_image_drag_mime_is_ignored(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            text_path = Path(folder) / "notes.txt"
            text_path.write_text("ignore", encoding="utf-8")
            mime = QMimeData()
            mime.setUrls([QUrl.fromLocalFile(str(text_path))])

            self.assertFalse(_is_supported_image_path(text_path))
            self.assertEqual(_first_supported_image_path_from_mime(mime), "")

    def test_test_runner_calls_same_service_client_and_returns_outputs(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            input_path = root / "input.png"
            output_dir = root / "outputs"
            refined_path = output_dir / "refined_1.png"
            input_path.write_bytes(b"fake input")
            output_dir.mkdir()
            refined_path.write_bytes(b"fake png")
            args = argparse.Namespace(
                service_url="http://127.0.0.1:8765",
                input=str(input_path),
                output_dir=str(output_dir),
                target_size="256x384",
                model_id="pixel-refiner-v2",
                model_dir=str(root / "model"),
                alpha_mode="preserve",
                palette_limit=64,
                strength=0.45,
                return_candidates=1,
                timeout=5,
            )

            fake_result = PixelRefinerResult(
                outputs=[PixelRefinerOutput(path=refined_path, label="标准")],
                model="pixel-refiner-v2",
                checks={"grid_aligned": True},
            )
            with mock.patch("pixel_refiner_test_runner.refine_pixel_art_with_service", return_value=fake_result) as refine:
                payload = run_refine_test(args)

            self.assertTrue(payload["ok"])
            self.assertEqual(payload["outputs"][0]["path"], str(refined_path))
            self.assertEqual(payload["outputs"][0]["bytes"], len(b"fake png"))
            request = refine.call_args.args[0]
            self.assertEqual(request.target_size, "256x384")
            self.assertEqual(request.model_id, "pixel-refiner-v2")
            self.assertEqual(request.return_candidates, 1)


if __name__ == "__main__":
    unittest.main()
