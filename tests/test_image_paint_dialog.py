import os
import tempfile
import unittest
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QPainter
from PySide6.QtWidgets import QApplication

from gamedesigner.ui.image_paint_dialog import ImagePaintDialog, PaintCanvas


class ImagePaintDialogTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def test_layers_pressure_and_transparent_export(self) -> None:
        canvas = PaintCanvas()
        canvas.layers[0].image.fill(Qt.transparent)
        canvas.add_layer()
        canvas.set_active_layer(1)
        canvas.set_brush_color(QColor("#FF0000"))
        canvas.set_brush_size(20)
        canvas.set_pressure_enabled(True)

        self.assertLess(canvas._stroke_width(0.1), canvas._stroke_width(1.0))
        canvas.set_pressure_enabled(False)
        self.assertEqual(canvas._stroke_width(0.1), canvas._stroke_width(1.0))

        canvas._draw_line(QPointF(24, 24), QPointF(24, 24), 1.0)
        transparent = canvas.export_image(transparent_background=True)
        opaque = canvas.export_image(transparent_background=False)

        self.assertEqual(transparent.pixelColor(0, 0).alpha(), 0)
        self.assertEqual(opaque.pixelColor(0, 0).alpha(), 255)
        self.assertGreater(transparent.pixelColor(24, 24).alpha(), 0)

    def test_selection_transform_moves_and_scales_pixels(self) -> None:
        canvas = PaintCanvas()
        canvas.layers[0].image.fill(Qt.transparent)
        painter = QPainter(canvas.active_layer().image)
        painter.fillRect(10, 10, 12, 12, QColor("#00AAFF"))
        painter.end()

        canvas.selection_rect = QRectF(10, 10, 12, 12)
        canvas._lift_selection()
        self.assertIsNotNone(canvas.floating_selection)
        canvas.floating_rect = QRectF(40, 40, 24, 24)
        canvas.commit_selection()
        image = canvas.export_image(transparent_background=True)

        self.assertEqual(image.pixelColor(12, 12).alpha(), 0)
        self.assertGreater(image.pixelColor(48, 48).alpha(), 0)

    def test_dialog_exposes_layer_preset_and_export_controls(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp) / "paint.png"
            dialog = ImagePaintDialog(output_path=output)
            dialog.canvas.add_layer()
            dialog.layer_list.setCurrentRow(1)
            self.assertEqual(dialog.canvas.active_layer_index, 0)

            dialog.preset_combo.setCurrentText("马克")
            self.assertEqual(dialog.canvas.brush_size, 24)
            self.assertEqual(dialog.canvas.brush_opacity, 46)
            self.assertFalse(dialog.canvas.pressure_enabled)
            self.assertTrue(dialog.transparent_export_check.isChecked())
            dialog._save_and_accept()

            self.assertTrue(output.exists())
            self.assertEqual(dialog.result_path, str(output))
            dialog.deleteLater()


if __name__ == "__main__":
    unittest.main()
