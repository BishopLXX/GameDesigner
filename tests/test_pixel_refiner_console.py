import argparse
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QMimeData, QUrl
from PySide6.QtWidgets import QApplication

from gamedesigner.pixel_refiner import PixelRefinerOutput, PixelRefinerResult
from pixel_refiner_service_window import (
    V41_MODEL_ID,
    PixelRefinerServiceWindow,
    _first_supported_image_path_from_mime,
    _fit_target_size,
    _is_supported_image_path,
    _latest_training_monitor_path,
    _safe_stem,
    _training_status_from_monitor_path,
    new_training_event_log,
)
from pixel_refiner_test_runner import run_refine_test


class PixelRefinerConsoleTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

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

    def test_v41_selection_uses_real_failure_training_defaults(self) -> None:
        window = PixelRefinerServiceWindow(
            model_dir=Path("D:/GameDesignerData/pixel_refiner/models/pixel-refiner-v4"),
            host="127.0.0.1",
            port=8765,
            auto_start=False,
        )
        index = window.model_id_combo.findData(V41_MODEL_ID)
        window.model_id_combo.setCurrentIndex(index)

        self.assertEqual(window.current_model_id(), V41_MODEL_ID)
        self.assertEqual(window.current_architecture(), "pixel-hard-v4")
        self.assertTrue(window.model_dir_edit.text().endswith(r"pixel-refiner-v4.1-real-failures"))
        self.assertEqual(window.patch_spin.value(), 64)
        self.assertEqual(window.software_candidate_weight_spin.value(), 32.0)
        self.assertEqual(window.ai_pseudo_weight_spin.value(), 16.0)

        with mock.patch.object(window, "_start_training_process") as start_training:
            window.start_training(retrain=False)

        args = start_training.call_args.args[0]
        self.assertIn("--software-candidate-weight", args)
        self.assertEqual(args[args.index("--software-candidate-weight") + 1], "32.0")
        self.assertEqual(args[args.index("--ai-pseudo-weight") + 1], "16.0")
        self.assertEqual(args[args.index("--features") + 1], "96")
        self.assertEqual(args[args.index("--internal-scale") + 1], "2")
        self.assertEqual(args[args.index("--tile-overlap") + 1], "16")
        window.deleteLater()

    def test_latest_training_monitor_prefers_new_run_over_legacy_model_log(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            old_run = root / "20260530_v4_gold_small"
            new_run = root / "20260530_151821_v42_ai_pseudo_then_train"
            old_run.mkdir()
            new_run.mkdir()
            old_log = old_run / "train_events.jsonl"
            new_log = new_run / "orchestrator.log"
            old_log.write_text('{"event":"epoch_end","epoch":4}\n', encoding="utf-8")
            new_log.write_text(
                "[2026-05-30 15:18:21] Desired ai_pseudo pairs: 300\n"
                "[2026-05-30 15:18:22] Current ai_pseudo pairs: 26\n",
                encoding="utf-8",
            )
            os.utime(old_log, (1_000_000_000, 1_000_000_000))
            os.utime(new_log, (1_000_000_100, 1_000_000_100))

            self.assertEqual(_latest_training_monitor_path(root), new_log)

    def test_orchestrator_monitor_shows_ai_pseudo_generation_progress(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            run = Path(folder)
            log = run / "orchestrator.log"
            log.write_text(
                "[2026-05-30 15:18:21] Desired ai_pseudo pairs: 300\n"
                "[2026-05-30 15:18:22] Current ai_pseudo pairs: 26\n"
                "[2026-05-30 15:18:22] Generating batch 1, limit=40, current=26\n",
                encoding="utf-8",
            )

            status = _training_status_from_monitor_path(log, run / "model")

            self.assertIn("正在补 GPT 对照组", status["label"])
            self.assertGreater(float(status["progress"]), 0.0)

    def test_new_training_event_log_is_run_scoped_not_legacy_model_bound(self) -> None:
        path = new_training_event_log(V41_MODEL_ID, mode="train")

        self.assertEqual(path.name, "train_events.jsonl")
        self.assertIn("pixel-refiner-v4_1-real-failures_train", path.parent.name)
        self.assertNotIn("20260530_v41_real_failures", str(path))


if __name__ == "__main__":
    unittest.main()
