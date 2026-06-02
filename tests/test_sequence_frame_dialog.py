import os
import tempfile
import unittest
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QSize
from PySide6.QtGui import QColor, QImage
from PySide6.QtWidgets import QApplication

from gamedesigner.image_rendering import is_pixel_art_image_path
from gamedesigner.ui.sequence_frame_dialog import (
    SequenceFrameDialog,
    build_animation_generation_prompt,
    build_horizontal_spritesheet,
    fit_image_to_frame,
    save_spritesheet,
    split_horizontal_spritesheet,
)


class SequenceFrameDialogTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def test_build_horizontal_spritesheet_places_equal_frames_in_one_row(self) -> None:
        red = self._solid_image(4, 5, "#FF0000")
        green = self._solid_image(4, 5, "#00FF00")
        blue = self._solid_image(4, 5, "#0000FF")

        sheet = build_horizontal_spritesheet([red, green, blue], frame_size=QSize(4, 5))

        self.assertEqual(sheet.width(), 12)
        self.assertEqual(sheet.height(), 5)
        self.assertEqual(sheet.pixelColor(0, 0).name().upper(), "#FF0000")
        self.assertEqual(sheet.pixelColor(4, 0).name().upper(), "#00FF00")
        self.assertEqual(sheet.pixelColor(8, 0).name().upper(), "#0000FF")

    def test_fit_image_to_frame_keeps_aspect_and_transparent_padding(self) -> None:
        source = self._solid_image(2, 2, "#3366CC")

        frame = fit_image_to_frame(source, QSize(4, 6), pixel_mode=True)

        self.assertEqual(frame.width(), 4)
        self.assertEqual(frame.height(), 6)
        self.assertEqual(frame.pixelColor(0, 0).alpha(), 0)
        self.assertEqual(frame.pixelColor(0, 1).name().upper(), "#3366CC")

    def test_save_pixel_spritesheet_writes_pixel_metadata(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "pixel_sheet.png"
            image = build_horizontal_spritesheet([self._solid_image(2, 2, "#111111")], pixel_mode=True)

            self.assertTrue(save_spritesheet(image, path, pixel_mode=True))

            self.assertTrue(path.exists())
            self.assertTrue(is_pixel_art_image_path(str(path)))

    def test_split_horizontal_spritesheet_returns_equal_preview_frames(self) -> None:
        red = self._solid_image(3, 2, "#FF0000")
        green = self._solid_image(3, 2, "#00FF00")
        sheet = build_horizontal_spritesheet([red, green], frame_size=QSize(3, 2))

        frames = split_horizontal_spritesheet(sheet, 2, QSize(3, 2), pixel_mode=True)

        self.assertEqual(len(frames), 2)
        self.assertEqual(frames[0].pixelColor(0, 0).name().upper(), "#FF0000")
        self.assertEqual(frames[1].pixelColor(0, 0).name().upper(), "#00FF00")

    def test_animation_prompt_includes_user_motion_frame_grid_and_pixel_rules(self) -> None:
        prompt = build_animation_generation_prompt(
            "向右走路，手臂摆动",
            frame_count=6,
            frame_width=128,
            frame_height=128,
            pixel_mode=True,
        )

        self.assertIn("exactly 6", prompt)
        self.assertIn("128x128", prompt)
        self.assertIn("向右走路", prompt)
        self.assertIn("crisp square pixels", prompt)

    def test_dialog_exports_seeded_frames_to_configured_output_path(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            source = folder / "source.png"
            output = folder / "sheet.png"
            self._solid_image(3, 2, "#CC3366").save(str(source), "PNG")

            dialog = SequenceFrameDialog(pixel_mode=True, initial_path=source, output_path=output)
            dialog.frame_count_spin.setValue(2)
            dialog._seed_frames()
            dialog._export_spritesheet()

            exported = QImage(str(output))
            self.assertEqual(dialog.result_path, str(output))
            self.assertEqual(exported.width(), 6)
            self.assertEqual(exported.height(), 2)
            self.assertTrue(is_pixel_art_image_path(str(output)))
            dialog.deleteLater()

    def test_dialog_loads_dropped_image_paths_without_file_picker(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            first = folder / "first.png"
            second = folder / "second.png"
            self._solid_image(5, 4, "#AA0000").save(str(first), "PNG")
            self._solid_image(5, 4, "#00AA00").save(str(second), "PNG")

            dialog = SequenceFrameDialog(pixel_mode=False)
            dialog._load_dropped_images([str(first), str(second)])

            self.assertEqual(dialog.frame_count_spin.value(), 2)
            self.assertEqual(len(dialog.source_frames), 2)
            self.assertEqual(dialog.width_spin.value(), 5)
            self.assertEqual(dialog.height_spin.value(), 4)
            self.assertEqual(dialog.base_image_path, first)
            dialog.deleteLater()

    def _solid_image(self, width: int, height: int, color: str) -> QImage:
        image = QImage(width, height, QImage.Format_ARGB32_Premultiplied)
        image.fill(QColor(color))
        return image


if __name__ == "__main__":
    unittest.main()
