import os
import tempfile
import unittest
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QBuffer, QByteArray, QIODevice, QSize
from PySide6.QtGui import QColor, QImage, QPainter
from PySide6.QtWidgets import QApplication

from gamedesigner.image_ai import AiGeneratedImage
from gamedesigner.image_rendering import is_pixel_art_image_path
from gamedesigner.storage import AppSettings
from gamedesigner.ui.sequence_frame_dialog import (
    SequenceFrameDialog,
    align_frame_content,
    bordered_sheet_size,
    build_api_generation_template,
    build_animation_frame_generation_prompt,
    build_bordered_generation_template_spritesheet,
    build_generation_template_spritesheet,
    build_animation_generation_prompt,
    build_horizontal_spritesheet,
    build_sequence_frame_generation_jobs,
    clear_connected_corner_background,
    crop_returned_api_canvas,
    extract_bordered_template_frames,
    four_multiple_size,
    generate_sequence_frames_with_ai,
    image_has_transparency,
    fit_image_to_frame,
    save_spritesheet,
    sequence_api_canvas_size,
    sequence_request_background,
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

    def test_split_ai_spritesheet_aligns_foreground_instead_of_preserving_black_offsets(self) -> None:
        sheet = self._solid_image(20, 10, "#000000")
        painter = QPainter(sheet)
        painter.fillRect(1, 4, 4, 2, QColor("#FF0000"))
        painter.fillRect(15, 4, 4, 2, QColor("#FF0000"))
        painter.end()

        frames = split_horizontal_spritesheet(
            sheet,
            2,
            QSize(10, 10),
            pixel_mode=True,
            align_content=True,
        )

        self.assertEqual(len(frames), 2)
        self.assertEqual(self._opaque_center_x(frames[0]), self._opaque_center_x(frames[1]))
        self.assertEqual(frames[0].pixelColor(0, 0).alpha(), 0)
        self.assertEqual(frames[1].pixelColor(0, 0).alpha(), 0)

    def test_align_frame_content_uses_shared_scale_for_all_frames(self) -> None:
        small = self._solid_image(10, 10, "#000000")
        large = self._solid_image(10, 10, "#000000")
        painter = QPainter(small)
        painter.fillRect(4, 4, 2, 2, QColor("#00FF00"))
        painter.end()
        painter = QPainter(large)
        painter.fillRect(3, 3, 4, 4, QColor("#00FF00"))
        painter.end()

        frames = align_frame_content([small, large], QSize(8, 8), pixel_mode=True)

        self.assertLess(self._opaque_width(frames[0]), self._opaque_width(frames[1]))
        self.assertEqual(self._opaque_center_x(frames[0]), self._opaque_center_x(frames[1]))

    def test_animation_prompt_includes_user_motion_frame_grid_and_pixel_rules(self) -> None:
        prompt = build_animation_generation_prompt(
            "向右走路，手臂摆动",
            frame_count=6,
            frame_width=128,
            frame_height=132,
            sheet_width=775,
            sheet_height=134,
            pixel_mode=True,
        )

        self.assertIn("exactly 6", prompt)
        self.assertIn("128x132", prompt)
        self.assertIn("向右走路", prompt)
        self.assertIn("crisp square pixels", prompt)
        self.assertIn("locked sprite-sheet template", prompt)
        self.assertIn("anchor point", prompt)
        self.assertIn("1-pixel pure black grid", prompt)

    def test_animation_frame_prompt_uses_single_frame_constraints(self) -> None:
        prompt = build_animation_frame_generation_prompt(
            "向右走路，手臂摆动",
            frame_index=2,
            frame_count=6,
            final_frame_width=128,
            final_frame_height=132,
            active_frame_width=816,
            active_frame_height=816,
            api_canvas_width=816,
            api_canvas_height=816,
            pixel_mode=True,
        )

        self.assertIn("single animation-frame edit canvas", prompt)
        self.assertIn("Frame 2 of 6", prompt)
        self.assertIn("128x132", prompt)
        self.assertIn("816x816", prompt)
        self.assertIn("向右走路", prompt)
        self.assertIn("Do not create a grid", prompt)
        self.assertIn("Do not create a grid, border, contact sheet, multi-pose sheet", prompt)
        self.assertNotIn("1-pixel pure black grid", prompt)

    def test_bordered_template_uses_four_multiple_inner_cells_and_one_pixel_grid(self) -> None:
        source = self._solid_image(625, 401, "#3366CC")

        sheet, content_size = build_bordered_generation_template_spritesheet(
            [source],
            4,
            QSize(625, 401),
            pixel_mode=True,
        )

        self.assertEqual(content_size, QSize(628, 404))
        self.assertEqual(four_multiple_size(QSize(625, 401)), QSize(628, 404))
        self.assertEqual(bordered_sheet_size(4, content_size), QSize(2517, 406))
        self.assertEqual(sheet.size(), QSize(2517, 406))
        self.assertEqual(sheet.pixelColor(0, 0).name().upper(), "#000000")
        self.assertEqual(sheet.pixelColor(629, 10).name().upper(), "#000000")
        self.assertEqual(sheet.pixelColor(1, 1).name().upper(), "#3366CC")

    def test_extract_bordered_template_frames_removes_grid_and_restores_output_size(self) -> None:
        source = self._solid_image(625, 401, "#AA5500")
        sheet, content_size = build_bordered_generation_template_spritesheet(
            [source],
            4,
            QSize(625, 401),
            pixel_mode=True,
        )

        frames = extract_bordered_template_frames(
            sheet,
            4,
            content_size,
            QSize(625, 401),
            pixel_mode=True,
        )

        self.assertEqual(len(frames), 4)
        self.assertEqual(frames[0].size(), QSize(625, 401))
        self.assertEqual(frames[0].pixelColor(0, 0).name().upper(), "#AA5500")
        self.assertNotEqual(frames[0].pixelColor(0, 0).name().upper(), "#000000")

    def test_generation_template_spritesheet_repeats_frames_to_fixed_grid(self) -> None:
        red = self._solid_image(4, 4, "#FF0000")
        blue = self._solid_image(4, 4, "#0000FF")

        sheet = build_generation_template_spritesheet([red, blue], 4, QSize(4, 4), pixel_mode=True)

        self.assertEqual(sheet.width(), 16)
        self.assertEqual(sheet.height(), 4)
        self.assertEqual(sheet.pixelColor(0, 0).name().upper(), "#FF0000")
        self.assertEqual(sheet.pixelColor(4, 0).name().upper(), "#0000FF")
        self.assertEqual(sheet.pixelColor(8, 0).name().upper(), "#0000FF")
        self.assertEqual(sheet.pixelColor(12, 0).name().upper(), "#0000FF")

    def test_gpt_image_2_sequence_canvas_wraps_invalid_wide_sheet_size(self) -> None:
        settings = AppSettings(ai_image_provider="compatible", ai_image_model="gpt-image-2")

        api_size = sequence_api_canvas_size(QSize(2500, 401), settings)

        self.assertEqual(api_size.width() % 16, 0)
        self.assertEqual(api_size.height() % 16, 0)
        self.assertGreaterEqual(api_size.width(), 2500)
        self.assertGreaterEqual(api_size.height(), 401)
        self.assertLessEqual(api_size.width() / api_size.height(), 3.0)

    def test_api_generation_template_centers_sheet_and_crop_restores_exact_region(self) -> None:
        settings = AppSettings(ai_image_provider="compatible", ai_image_model="gpt-image-2")
        sheet = self._solid_image(2500, 401, "#123456")

        template = build_api_generation_template(sheet, settings)
        restored = crop_returned_api_canvas(template.image, template.sheet_rect, template.api_size)

        self.assertGreater(template.api_size.height(), sheet.height())
        self.assertEqual(template.sheet_rect.width(), sheet.width())
        self.assertEqual(template.sheet_rect.height(), sheet.height())
        self.assertEqual(restored.width(), sheet.width())
        self.assertEqual(restored.height(), sheet.height())
        self.assertEqual(restored.pixelColor(0, 0).name().upper(), "#123456")

    def test_api_generation_template_can_scale_single_frame_to_model_canvas(self) -> None:
        settings = AppSettings(ai_image_provider="compatible", ai_image_model="gpt-image-2")
        frame = self._solid_image(128, 128, "#4455AA")

        template = build_api_generation_template(frame, settings, scale_up_to_canvas=True)
        restored = crop_returned_api_canvas(template.image, template.sheet_rect, template.api_size)
        exact = fit_image_to_frame(restored, QSize(128, 128), pixel_mode=True)

        self.assertGreater(template.api_size.width(), frame.width())
        self.assertGreater(template.sheet_rect.width(), frame.width())
        self.assertEqual(restored.size(), template.sheet_rect.size())
        self.assertEqual(exact.size(), frame.size())
        self.assertEqual(exact.pixelColor(0, 0).name().upper(), "#4455AA")

    def test_sequence_frame_jobs_use_source_alpha_not_api_padding(self) -> None:
        settings = AppSettings(ai_image_provider="compatible", ai_image_model="gpt-image-2")
        source = self._solid_image(128, 64, "#334455")

        with tempfile.TemporaryDirectory() as tmp:
            jobs = build_sequence_frame_generation_jobs(
                settings,
                "眨眼",
                [source],
                2,
                QSize(128, 64),
                temp_dir=Path(tmp),
            )

        self.assertEqual(len(jobs), 2)
        self.assertFalse(jobs[0].source_has_transparency)
        self.assertTrue(image_has_transparency(jobs[0].template.image))
        self.assertGreater(jobs[0].template.sheet_rect.width(), source.width())

    def test_generate_sequence_frames_with_fake_generator_returns_exact_frames(self) -> None:
        settings = AppSettings(
            ai_image_provider="compatible",
            ai_image_model="gpt-image-2",
            ai_image_api_key="test-key",
            ai_image_base_url="http://example.test/v1",
        )
        source = self._solid_image(32, 32, "#224466")
        colors = ["#CC0000", "#00AA00", "#0000CC"]
        calls = []

        def fake_generator(request):
            calls.append(request)
            width, height = [int(part) for part in request.size.split("x", 1)]
            image = self._solid_image(width, height, colors[len(calls) - 1])
            return [AiGeneratedImage(self._png_bytes(image), "png")]

        with tempfile.TemporaryDirectory() as tmp:
            jobs = build_sequence_frame_generation_jobs(
                settings,
                "向右跳跃",
                [source],
                3,
                QSize(32, 32),
                pixel_mode=True,
                temp_dir=Path(tmp),
            )
            frames = generate_sequence_frames_with_ai(
                settings,
                jobs,
                frame_size=QSize(32, 32),
                pixel_mode=True,
                image_generator=fake_generator,
            )

        sheet = build_horizontal_spritesheet(frames, frame_size=QSize(32, 32), pixel_mode=True)
        self.assertEqual(len(frames), 3)
        self.assertEqual(sheet.size(), QSize(96, 32))
        self.assertEqual(sheet.pixelColor(0, 0).name().upper(), "#CC0000")
        self.assertEqual(sheet.pixelColor(32, 0).name().upper(), "#00AA00")
        self.assertEqual(sheet.pixelColor(64, 0).name().upper(), "#0000CC")
        self.assertEqual([call.prompt for call in calls], [job.prompt for job in jobs])
        self.assertIn("Frame 3 of 3", calls[2].prompt)

    def test_gpt_image_2_sequence_request_does_not_send_transparent_background(self) -> None:
        settings = AppSettings(
            ai_image_provider="compatible",
            ai_image_model="gpt-image-2",
            ai_image_background="transparent",
        )

        background = sequence_request_background(settings, template_has_transparency=True)

        self.assertEqual(background, "auto")

    def test_sequence_request_keeps_transparent_background_for_supported_models(self) -> None:
        settings = AppSettings(
            ai_image_provider="compatible",
            ai_image_model="gpt-image-1.5",
            ai_image_background="auto",
        )

        background = sequence_request_background(settings, template_has_transparency=True)

        self.assertEqual(background, "transparent")

    def test_clear_connected_corner_background_keeps_foreground_and_clears_edge_fill(self) -> None:
        image = self._solid_image(10, 8, "#000000")
        painter = QPainter(image)
        painter.fillRect(3, 2, 3, 3, QColor("#00FF00"))
        painter.end()

        cleaned = clear_connected_corner_background(image)

        self.assertTrue(image_has_transparency(cleaned))
        self.assertEqual(cleaned.pixelColor(0, 0).alpha(), 0)
        self.assertGreater(cleaned.pixelColor(4, 3).alpha(), 0)

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

    def test_dialog_uses_output_path_field_when_exporting(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            source = folder / "source.png"
            chosen = folder / "custom" / "frames.png"
            self._solid_image(3, 2, "#CC3366").save(str(source), "PNG")

            dialog = SequenceFrameDialog(pixel_mode=False, initial_path=source)
            dialog.frame_count_spin.setValue(2)
            dialog._seed_frames()
            dialog.output_path_edit.setText(str(chosen))
            dialog._export_spritesheet()

            self.assertTrue(chosen.exists())
            self.assertEqual(Path(dialog.result_path), chosen)
            self.assertEqual(dialog.output_path_edit.text(), str(chosen))
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
            self.assertIn("已拖入参考图", dialog.log_edit.toPlainText())
            dialog.deleteLater()

    def _solid_image(self, width: int, height: int, color: str) -> QImage:
        image = QImage(width, height, QImage.Format_ARGB32_Premultiplied)
        image.fill(QColor(color))
        return image

    def _png_bytes(self, image: QImage) -> bytes:
        data = QByteArray()
        buffer = QBuffer(data)
        buffer.open(QIODevice.WriteOnly)
        image.save(buffer, "PNG")
        buffer.close()
        return bytes(data)

    def _opaque_center_x(self, image: QImage) -> int:
        xs = [
            x
            for y in range(image.height())
            for x in range(image.width())
            if image.pixelColor(x, y).alpha() > 0
        ]
        return round((min(xs) + max(xs)) / 2) if xs else -1

    def _opaque_width(self, image: QImage) -> int:
        xs = [
            x
            for y in range(image.height())
            for x in range(image.width())
            if image.pixelColor(x, y).alpha() > 0
        ]
        return max(xs) - min(xs) + 1 if xs else 0


if __name__ == "__main__":
    unittest.main()
