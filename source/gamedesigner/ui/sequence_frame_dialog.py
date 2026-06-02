from __future__ import annotations

import copy
from collections import deque
from dataclasses import dataclass
import tempfile
import uuid
from pathlib import Path
from typing import Callable

from PySide6.QtCore import QRect, QSize, Qt, QThread, QTimer, Signal
from PySide6.QtGui import QColor, QIcon, QImage, QImageWriter, QPainter, QPixmap
from PySide6.QtWidgets import (
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QListView,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QLineEdit,
    QSpinBox,
    QVBoxLayout,
    QWidget,
    QDialog,
)

from .image_paint_dialog import ImagePaintDialog
from ..image_ai import AiImageError, build_ai_image_request, generate_ai_images
from ..pixel_art import api_ai_image_size, is_valid_gpt_image_2_size
from ..storage import AppSettings
from ..window_layouts import restore_window_layout, save_window_layout


PIXEL_ART_METADATA_KEY = "GameDesignerPixelArt"
IMAGE_DROP_EXTENSIONS = {".png", ".jpg", ".jpeg", ".bmp", ".gif", ".webp"}
GPT_IMAGE_2_MIN_PIXELS = 655_360
GPT_IMAGE_2_MAX_PIXELS = 8_294_400
GPT_IMAGE_2_MAX_EDGE = 3_840
GPT_IMAGE_2_MAX_ASPECT_RATIO = 3.0


@dataclass(frozen=True)
class GenerationTemplate:
    image: QImage
    sheet_rect: QRect
    api_size: QSize
    sheet_size: QSize
    content_frame_size: QSize


@dataclass(frozen=True)
class FrameGenerationJob:
    index: int
    prompt: str
    template_path: Path
    template: GenerationTemplate
    source_has_transparency: bool


def fit_image_to_frame(image: QImage, frame_size: QSize, *, pixel_mode: bool = False) -> QImage:
    width = max(1, int(frame_size.width()))
    height = max(1, int(frame_size.height()))
    output = QImage(width, height, QImage.Format_ARGB32_Premultiplied)
    output.fill(Qt.transparent)
    if image.isNull():
        return output
    source = image.convertToFormat(QImage.Format_ARGB32_Premultiplied)
    if source.width() == width and source.height() == height:
        return source.copy()
    scaled = source.scaled(
        width,
        height,
        Qt.KeepAspectRatio,
        Qt.FastTransformation if pixel_mode else Qt.SmoothTransformation,
    )
    painter = QPainter(output)
    painter.setRenderHint(QPainter.SmoothPixmapTransform, not pixel_mode)
    painter.drawImage((width - scaled.width()) // 2, (height - scaled.height()) // 2, scaled)
    painter.end()
    return output


def scale_image_to_exact_frame(image: QImage, frame_size: QSize, *, pixel_mode: bool = False) -> QImage:
    width = max(1, int(frame_size.width()))
    height = max(1, int(frame_size.height()))
    output = QImage(width, height, QImage.Format_ARGB32_Premultiplied)
    output.fill(Qt.transparent)
    if image.isNull():
        return output
    source = image.convertToFormat(QImage.Format_ARGB32_Premultiplied)
    if source.width() == width and source.height() == height:
        return source.copy()
    return source.scaled(
        width,
        height,
        Qt.IgnoreAspectRatio,
        Qt.FastTransformation if pixel_mode else Qt.SmoothTransformation,
    )


def build_horizontal_spritesheet(
    frames: list[QImage],
    *,
    pixel_mode: bool = False,
    frame_size: QSize | None = None,
) -> QImage:
    valid_frames = [frame for frame in frames if not frame.isNull()]
    if not valid_frames:
        return QImage()
    base_size = frame_size or QSize(valid_frames[0].width(), valid_frames[0].height())
    frame_width = max(1, int(base_size.width()))
    frame_height = max(1, int(base_size.height()))
    output = QImage(frame_width * len(valid_frames), frame_height, QImage.Format_ARGB32_Premultiplied)
    output.fill(Qt.transparent)
    painter = QPainter(output)
    painter.setRenderHint(QPainter.SmoothPixmapTransform, not pixel_mode)
    for index, frame in enumerate(valid_frames):
        normalized = fit_image_to_frame(frame, QSize(frame_width, frame_height), pixel_mode=pixel_mode)
        painter.drawImage(index * frame_width, 0, normalized)
    painter.end()
    return output


def save_spritesheet(image: QImage, path: str | Path, *, pixel_mode: bool = False) -> bool:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    writer = QImageWriter(str(target), b"PNG")
    if pixel_mode:
        writer.setText(PIXEL_ART_METADATA_KEY, "1")
    return writer.write(image)


def split_horizontal_spritesheet(
    image: QImage,
    frame_count: int,
    frame_size: QSize,
    *,
    pixel_mode: bool = False,
    align_content: bool = False,
    cleanup_background: bool = False,
) -> list[QImage]:
    if image.isNull():
        return []
    count = max(1, int(frame_count))
    source = image.convertToFormat(QImage.Format_ARGB32_Premultiplied)
    segment_width = max(1, source.width() // count)
    frames: list[QImage] = []
    for index in range(count):
        x = min(source.width() - segment_width, index * segment_width)
        segment = source.copy(x, 0, segment_width, source.height())
        frames.append(segment)
    if align_content:
        return align_frame_content(frames, frame_size, pixel_mode=pixel_mode)
    normalized_frames = [fit_image_to_frame(frame, frame_size, pixel_mode=pixel_mode) for frame in frames]
    if cleanup_background:
        return [clear_connected_corner_background(frame) for frame in normalized_frames]
    return normalized_frames


def build_generation_template_spritesheet(
    frames: list[QImage],
    frame_count: int,
    frame_size: QSize,
    *,
    pixel_mode: bool = False,
) -> QImage:
    valid_frames = [frame for frame in frames if not frame.isNull()]
    if not valid_frames:
        return QImage()
    count = max(1, int(frame_count))
    template_frames: list[QImage] = []
    for index in range(count):
        source = valid_frames[index] if index < len(valid_frames) else valid_frames[-1]
        template_frames.append(fit_image_to_frame(source, frame_size, pixel_mode=pixel_mode))
    return build_horizontal_spritesheet(template_frames, pixel_mode=pixel_mode, frame_size=frame_size)


def four_multiple_size(size: QSize) -> QSize:
    return QSize(_ceil_to_multiple(max(1, size.width()), 4), _ceil_to_multiple(max(1, size.height()), 4))


def bordered_sheet_size(frame_count: int, content_frame_size: QSize, *, grid: int = 1) -> QSize:
    count = max(1, int(frame_count))
    content_width = max(1, int(content_frame_size.width()))
    content_height = max(1, int(content_frame_size.height()))
    grid_size = max(1, int(grid))
    return QSize(count * content_width + (count + 1) * grid_size, content_height + grid_size * 2)


def build_bordered_generation_template_spritesheet(
    frames: list[QImage],
    frame_count: int,
    frame_size: QSize,
    *,
    pixel_mode: bool = False,
    grid: int = 1,
) -> tuple[QImage, QSize]:
    valid_frames = [frame for frame in frames if not frame.isNull()]
    if not valid_frames:
        return QImage(), QSize()
    count = max(1, int(frame_count))
    grid_size = max(1, int(grid))
    content_size = four_multiple_size(frame_size)
    sheet_size = bordered_sheet_size(count, content_size, grid=grid_size)
    output = QImage(sheet_size, QImage.Format_ARGB32_Premultiplied)
    output.fill(Qt.transparent)
    painter = QPainter(output)
    painter.fillRect(0, 0, sheet_size.width(), grid_size, QColor("#000000"))
    painter.fillRect(0, sheet_size.height() - grid_size, sheet_size.width(), grid_size, QColor("#000000"))
    for index in range(count + 1):
        x = index * (content_size.width() + grid_size)
        painter.fillRect(x, 0, grid_size, sheet_size.height(), QColor("#000000"))
    painter.setRenderHint(QPainter.SmoothPixmapTransform, not pixel_mode)
    for index in range(count):
        source = valid_frames[index] if index < len(valid_frames) else valid_frames[-1]
        normalized = scale_image_to_exact_frame(source, content_size, pixel_mode=pixel_mode)
        x = grid_size + index * (content_size.width() + grid_size)
        painter.drawImage(x, grid_size, normalized)
    painter.end()
    return output, content_size


def extract_bordered_template_frames(
    sheet: QImage,
    frame_count: int,
    content_frame_size: QSize,
    output_frame_size: QSize,
    *,
    pixel_mode: bool = False,
    cleanup_background: bool = False,
    grid: int = 1,
) -> list[QImage]:
    if sheet.isNull():
        return []
    count = max(1, int(frame_count))
    grid_size = max(1, int(grid))
    content_size = QSize(max(1, content_frame_size.width()), max(1, content_frame_size.height()))
    expected_size = bordered_sheet_size(count, content_size, grid=grid_size)
    source = sheet.convertToFormat(QImage.Format_ARGB32_Premultiplied)
    if source.size() != expected_size:
        source = source.scaled(
            expected_size,
            Qt.IgnoreAspectRatio,
            Qt.FastTransformation if pixel_mode else Qt.SmoothTransformation,
        )
    frames: list[QImage] = []
    for index in range(count):
        x = grid_size + index * (content_size.width() + grid_size)
        crop = source.copy(x, grid_size, content_size.width(), content_size.height())
        frame = scale_image_to_exact_frame(crop, output_frame_size, pixel_mode=pixel_mode)
        if cleanup_background:
            frame = clear_connected_corner_background(frame)
        frames.append(frame)
    return frames


def build_api_generation_template(
    sheet: QImage,
    settings: AppSettings | None,
    *,
    content_frame_size: QSize | None = None,
    pixel_mode: bool = False,
    scale_up_to_canvas: bool = False,
) -> GenerationTemplate:
    if sheet.isNull():
        return GenerationTemplate(QImage(), QRect(), QSize(), QSize(), QSize())
    source = sheet.convertToFormat(QImage.Format_ARGB32_Premultiplied)
    sheet_size = QSize(source.width(), source.height())
    template_content_size = content_frame_size or sheet_size
    api_size = sequence_api_canvas_size(sheet_size, settings)
    if api_size.width() <= 0 or api_size.height() <= 0:
        return GenerationTemplate(QImage(), QRect(), QSize(), sheet_size, template_content_size)
    canvas = QImage(api_size, QImage.Format_ARGB32_Premultiplied)
    canvas.fill(Qt.transparent)
    draw = source
    if scale_up_to_canvas or source.width() > api_size.width() or source.height() > api_size.height():
        draw = source.scaled(
            api_size.width(),
            api_size.height(),
            Qt.KeepAspectRatio,
            Qt.FastTransformation if pixel_mode else Qt.SmoothTransformation,
        )
    x = (api_size.width() - draw.width()) // 2
    y = (api_size.height() - draw.height()) // 2
    painter = QPainter(canvas)
    painter.setRenderHint(QPainter.SmoothPixmapTransform, not pixel_mode)
    painter.drawImage(x, y, draw)
    painter.end()
    return GenerationTemplate(canvas, QRect(x, y, draw.width(), draw.height()), api_size, sheet_size, template_content_size)


def sequence_api_canvas_size(sheet_size: QSize, settings: AppSettings | None) -> QSize:
    width = max(1, int(sheet_size.width()))
    height = max(1, int(sheet_size.height()))
    model = str(getattr(settings, "ai_image_model", "") or "").strip().lower()
    provider = str(getattr(settings, "ai_image_provider", "") or "").strip().lower()
    requested = f"{width}x{height}"
    if model == "gpt-image-2":
        return _gpt_image_2_canvas_size(width, height)
    if provider != "compatible" and api_ai_image_size(requested, model=model, provider=provider) == requested:
        return QSize(width, height)
    return QSize(width, height)


def _gpt_image_2_canvas_size(width: int, height: int) -> QSize:
    width = _ceil_to_multiple(max(1, int(width)), 16)
    height = _ceil_to_multiple(max(1, int(height)), 16)
    if width / height > GPT_IMAGE_2_MAX_ASPECT_RATIO:
        height = _ceil_to_multiple(round(width / GPT_IMAGE_2_MAX_ASPECT_RATIO), 16)
    if height / width > GPT_IMAGE_2_MAX_ASPECT_RATIO:
        width = _ceil_to_multiple(round(height / GPT_IMAGE_2_MAX_ASPECT_RATIO), 16)
    while width * height < GPT_IMAGE_2_MIN_PIXELS:
        if width <= height:
            width = min(GPT_IMAGE_2_MAX_EDGE, width + 16)
            if height / width > GPT_IMAGE_2_MAX_ASPECT_RATIO:
                height = _ceil_to_multiple(round(width * GPT_IMAGE_2_MAX_ASPECT_RATIO), 16)
        else:
            height = min(GPT_IMAGE_2_MAX_EDGE, height + 16)
            if width / height > GPT_IMAGE_2_MAX_ASPECT_RATIO:
                width = _ceil_to_multiple(round(height * GPT_IMAGE_2_MAX_ASPECT_RATIO), 16)
        if width >= GPT_IMAGE_2_MAX_EDGE and height >= GPT_IMAGE_2_MAX_EDGE:
            break
    if width > GPT_IMAGE_2_MAX_EDGE or height > GPT_IMAGE_2_MAX_EDGE or width * height > GPT_IMAGE_2_MAX_PIXELS:
        scale = min(
            GPT_IMAGE_2_MAX_EDGE / max(width, height),
            (GPT_IMAGE_2_MAX_PIXELS / max(1, width * height)) ** 0.5,
        )
        width = _floor_to_multiple(max(16, int(width * scale)), 16)
        height = _floor_to_multiple(max(16, int(height * scale)), 16)
    if not is_valid_gpt_image_2_size(f"{width}x{height}"):
        height = _ceil_to_multiple(max(height, round(width / GPT_IMAGE_2_MAX_ASPECT_RATIO)), 16)
    return QSize(width, height)


def _ceil_to_multiple(value: int, multiple: int) -> int:
    return max(multiple, ((max(1, int(value)) + multiple - 1) // multiple) * multiple)


def _floor_to_multiple(value: int, multiple: int) -> int:
    return max(multiple, (max(1, int(value)) // multiple) * multiple)


def sequence_request_background(settings: AppSettings | None, template_has_transparency: bool) -> str:
    background = str(getattr(settings, "ai_image_background", "auto") or "auto").strip() or "auto"
    model = str(getattr(settings, "ai_image_model", "") or "").strip().lower()
    if model == "gpt-image-2" and background == "transparent":
        return "auto"
    if template_has_transparency:
        return "auto" if model == "gpt-image-2" else "transparent"
    return background


def image_has_transparency(image: QImage) -> bool:
    if image.isNull():
        return False
    source = image.convertToFormat(QImage.Format_ARGB32_Premultiplied)
    for y in range(source.height()):
        for x in range(source.width()):
            if source.pixelColor(x, y).alpha() < 255:
                return True
    return False


def clear_connected_corner_background(
    image: QImage,
    *,
    tolerance: int = 24,
    alpha_threshold: int = 8,
) -> QImage:
    return clear_edge_background_artifacts(image, tolerance=tolerance, alpha_threshold=alpha_threshold)


def clear_edge_background_artifacts(
    image: QImage,
    *,
    tolerance: int = 24,
    alpha_threshold: int = 8,
) -> QImage:
    if image.isNull():
        return QImage()
    output = image.convertToFormat(QImage.Format_ARGB32_Premultiplied)
    width = output.width()
    height = output.height()
    if width <= 0 or height <= 0:
        return output
    foreground_mask, _rect = _foreground_mask_and_rect(
        output,
        background_tolerance=tolerance,
        alpha_threshold=alpha_threshold,
    )
    if foreground_mask is None:
        return output
    transparent = QColor(0, 0, 0, 0)
    for y in range(height):
        row = y * width
        for x in range(width):
            if not foreground_mask[row + x]:
                output.setPixelColor(x, y, transparent)
    return output


def align_frame_content(frames: list[QImage], frame_size: QSize, *, pixel_mode: bool = False) -> list[QImage]:
    records: list[tuple[QImage, QRect | None]] = [
        (frame, foreground_content_rect(frame)) for frame in frames if not frame.isNull()
    ]
    boxes = [box for _frame, box in records if box is not None and box.width() > 0 and box.height() > 0]
    if not boxes:
        return [fit_image_to_frame(frame, frame_size, pixel_mode=pixel_mode) for frame, _box in records]
    target_width = max(1, frame_size.width())
    target_height = max(1, frame_size.height())
    max_content_width = max(1, max(box.width() for box in boxes))
    max_content_height = max(1, max(box.height() for box in boxes))
    scale = min(target_width / max_content_width, target_height / max_content_height)
    bottom_margin = min(max(0, frame.height() - box.bottom() - 1) for frame, box in records if box is not None)
    target_bottom = max(0, target_height - bottom_margin - 1)
    aligned: list[QImage] = []
    for frame, box in records:
        output = QImage(target_width, target_height, QImage.Format_ARGB32_Premultiplied)
        output.fill(Qt.transparent)
        if box is None:
            aligned.append(output)
            continue
        crop = frame.copy(box)
        scaled_width = max(1, min(target_width, round(crop.width() * scale)))
        scaled_height = max(1, min(target_height, round(crop.height() * scale)))
        scaled = crop.scaled(
            scaled_width,
            scaled_height,
            Qt.KeepAspectRatio,
            Qt.FastTransformation if pixel_mode else Qt.SmoothTransformation,
        )
        painter = QPainter(output)
        painter.setRenderHint(QPainter.SmoothPixmapTransform, not pixel_mode)
        x = (target_width - scaled.width()) // 2
        y = max(0, min(target_height - scaled.height(), target_bottom - scaled.height() + 1))
        painter.drawImage(x, y, scaled)
        painter.end()
        aligned.append(output)
    return aligned


def stabilize_frame_anchors(frames: list[QImage], frame_size: QSize, *, pixel_mode: bool = False) -> list[QImage]:
    normalized = [fit_image_to_frame(frame, frame_size, pixel_mode=pixel_mode) for frame in frames if not frame.isNull()]
    records: list[tuple[QImage, QRect | None]] = [
        (frame, foreground_content_rect(frame)) for frame in normalized
    ]
    target_box = next((box for _frame, box in records if box is not None and box.width() > 0 and box.height() > 0), None)
    if target_box is None:
        return normalized
    target_center_x = target_box.center().x()
    target_bottom = target_box.bottom()
    target_width = max(1, frame_size.width())
    target_height = max(1, frame_size.height())
    stabilized: list[QImage] = []
    for frame, box in records:
        if box is None:
            stabilized.append(frame.copy())
            continue
        dx = target_center_x - box.center().x()
        dy = target_bottom - box.bottom()
        if dx == 0 and dy == 0:
            stabilized.append(frame.copy())
            continue
        output = QImage(target_width, target_height, QImage.Format_ARGB32_Premultiplied)
        output.fill(Qt.transparent)
        painter = QPainter(output)
        painter.setRenderHint(QPainter.SmoothPixmapTransform, not pixel_mode)
        painter.drawImage(dx, dy, frame)
        painter.end()
        stabilized.append(output)
    return register_frame_alignment(stabilized, frame_size, pixel_mode=pixel_mode)


def register_frame_alignment(frames: list[QImage], frame_size: QSize, *, pixel_mode: bool = False) -> list[QImage]:
    normalized = [fit_image_to_frame(frame, frame_size, pixel_mode=pixel_mode) for frame in frames if not frame.isNull()]
    if len(normalized) <= 1:
        return normalized
    target_width = max(1, frame_size.width())
    target_height = max(1, frame_size.height())
    reference_mask, _rect = _foreground_mask_and_rect(
        normalized[0].convertToFormat(QImage.Format_ARGB32_Premultiplied)
    )
    if reference_mask is None:
        return normalized
    aligned = [normalized[0].copy()]
    for frame in normalized[1:]:
        frame_mask, _frame_rect = _foreground_mask_and_rect(frame.convertToFormat(QImage.Format_ARGB32_Premultiplied))
        if frame_mask is None:
            aligned.append(frame.copy())
            continue
        dx, dy = _best_registration_offset(reference_mask, frame_mask, target_width, target_height)
        if dx == 0 and dy == 0:
            aligned.append(frame.copy())
            continue
        output = QImage(target_width, target_height, QImage.Format_ARGB32_Premultiplied)
        output.fill(Qt.transparent)
        painter = QPainter(output)
        painter.setRenderHint(QPainter.SmoothPixmapTransform, not pixel_mode)
        painter.drawImage(dx, dy, frame)
        painter.end()
        aligned.append(output)
    return aligned


def _best_registration_offset(reference_mask: bytearray, frame_mask: bytearray, width: int, height: int) -> tuple[int, int]:
    min_edge = max(1, min(width, height))
    max_shift = max(6, min(24, round(min_edge * 0.06)))
    stride = 1 if min_edge <= 96 else 2 if min_edge <= 192 else 4 if min_edge <= 512 else 6
    reference_samples = _sample_mask_offsets(reference_mask, width, height, stride)
    frame_samples = _sample_mask_offsets(frame_mask, width, height, stride)
    if len(reference_samples) < 8 or len(frame_samples) < 8:
        return 0, 0
    zero_score = _registration_score(reference_mask, frame_mask, reference_samples, frame_samples, width, height, 0, 0)
    best_score = zero_score
    best_dx = 0
    best_dy = 0
    for dy in range(-max_shift, max_shift + 1):
        for dx in range(-max_shift, max_shift + 1):
            if dx == 0 and dy == 0:
                continue
            score = _registration_score(
                reference_mask,
                frame_mask,
                reference_samples,
                frame_samples,
                width,
                height,
                dx,
                dy,
            )
            if score > best_score or (
                score == best_score and abs(dx) + abs(dy) < abs(best_dx) + abs(best_dy)
            ):
                best_score = score
                best_dx = dx
                best_dy = dy
    required_gain = max(3, round(min(len(reference_samples), len(frame_samples)) * 0.006))
    if best_score < zero_score + required_gain:
        return 0, 0
    return best_dx, best_dy


def _sample_mask_offsets(mask: bytearray, width: int, height: int, stride: int) -> list[int]:
    samples: list[int] = []
    step = max(1, int(stride))
    for y in range(0, height, step):
        row = y * width
        for x in range(0, width, step):
            offset = row + x
            if mask[offset]:
                samples.append(offset)
    return samples


def _registration_score(
    reference_mask: bytearray,
    frame_mask: bytearray,
    reference_samples: list[int],
    frame_samples: list[int],
    width: int,
    height: int,
    dx: int,
    dy: int,
) -> int:
    score = _shifted_overlap(frame_mask, reference_samples, width, height, dx, dy)
    score += _shifted_overlap(reference_mask, frame_samples, width, height, -dx, -dy)
    score -= (abs(dx) + abs(dy)) * 2
    return score


def _shifted_overlap(mask: bytearray, samples: list[int], width: int, height: int, dx: int, dy: int) -> int:
    overlap = 0
    for offset in samples:
        x = offset % width
        y = offset // width
        target_x = x - dx
        target_y = y - dy
        if target_x < 0 or target_y < 0 or target_x >= width or target_y >= height:
            continue
        if mask[target_y * width + target_x]:
            overlap += 1
    return overlap


def foreground_content_rect(image: QImage, *, background_tolerance: int = 18, alpha_threshold: int = 8) -> QRect | None:
    if image.isNull():
        return None
    source = image.convertToFormat(QImage.Format_ARGB32_Premultiplied)
    _mask, rect = _foreground_mask_and_rect(
        source,
        background_tolerance=background_tolerance,
        alpha_threshold=alpha_threshold,
    )
    return rect


def _foreground_mask_and_rect(
    source: QImage,
    *,
    background_tolerance: int = 18,
    alpha_threshold: int = 8,
) -> tuple[bytearray | None, QRect | None]:
    width = source.width()
    height = source.height()
    if width <= 0 or height <= 0:
        return None, None
    backgrounds = [
        source.pixelColor(0, 0),
        source.pixelColor(width - 1, 0),
        source.pixelColor(0, height - 1),
        source.pixelColor(width - 1, height - 1),
    ]
    edge_background = _edge_background_mask(
        source,
        backgrounds,
        background_tolerance=background_tolerance,
        alpha_threshold=alpha_threshold,
    )
    candidates = bytearray(width * height)
    for y in range(height):
        row = y * width
        for x in range(width):
            offset = row + x
            color = source.pixelColor(x, y)
            if color.alpha() <= alpha_threshold or edge_background[offset]:
                continue
            if _is_background_pixel(color, backgrounds, background_tolerance, alpha_threshold):
                continue
            candidates[offset] = 1
    foreground = bytearray(width * height)
    visited = bytearray(width * height)
    components: list[tuple[list[int], tuple[int, int, int, int], int, int]] = []
    for y in range(height):
        for x in range(width):
            start = y * width + x
            if not candidates[start] or visited[start]:
                continue
            stack = [start]
            visited[start] = 1
            pixels: list[int] = []
            min_x = width
            min_y = height
            max_x = -1
            max_y = -1
            artifact_pixels = 0
            while stack:
                offset = stack.pop()
                pixels.append(offset)
                px = offset % width
                py = offset // width
                min_x = min(min_x, px)
                min_y = min(min_y, py)
                max_x = max(max_x, px)
                max_y = max(max_y, py)
                if _is_neutral_background_artifact(source.pixelColor(px, py), alpha_threshold):
                    artifact_pixels += 1
                for neighbor in (
                    offset - 1 if px > 0 else -1,
                    offset + 1 if px < width - 1 else -1,
                    offset - width if py > 0 else -1,
                    offset + width if py < height - 1 else -1,
                ):
                    if neighbor >= 0 and candidates[neighbor] and not visited[neighbor]:
                        visited[neighbor] = 1
                        stack.append(neighbor)
            components.append((pixels, (min_x, min_y, max_x, max_y), len(pixels), artifact_pixels))
    if not components:
        return foreground, None
    largest_area = max(area for _pixels, _box, area, _artifact in components)
    area_floor = max(2, min(64, round(width * height * 0.00025), round(largest_area * 0.005)))
    min_x = width
    min_y = height
    max_x = -1
    max_y = -1
    for pixels, box, area, artifact_pixels in components:
        if area < area_floor:
            continue
        if area > 0 and artifact_pixels / area >= 0.86:
            continue
        for offset in pixels:
            foreground[offset] = 1
        min_x = min(min_x, box[0])
        min_y = min(min_y, box[1])
        max_x = max(max_x, box[2])
        max_y = max(max_y, box[3])
    if max_x < min_x or max_y < min_y:
        return foreground, None
    rect = QRect(min_x, min_y, max_x - min_x + 1, max_y - min_y + 1)
    return foreground, rect


def _edge_background_mask(
    image: QImage,
    backgrounds: list[QColor],
    *,
    background_tolerance: int,
    alpha_threshold: int,
) -> bytearray:
    width = image.width()
    height = image.height()
    visited = bytearray(width * height)
    background = bytearray(width * height)
    queue: deque[tuple[int, int]] = deque()
    for x in range(width):
        queue.append((x, 0))
        queue.append((x, height - 1))
    for y in range(height):
        queue.append((0, y))
        queue.append((width - 1, y))
    while queue:
        x, y = queue.popleft()
        if x < 0 or y < 0 or x >= width or y >= height:
            continue
        offset = y * width + x
        if visited[offset]:
            continue
        visited[offset] = 1
        color = image.pixelColor(x, y)
        if not _is_edge_background_pixel(color, backgrounds, background_tolerance, alpha_threshold):
            continue
        background[offset] = 1
        queue.append((x + 1, y))
        queue.append((x - 1, y))
        queue.append((x, y + 1))
        queue.append((x, y - 1))
    return background


def _is_edge_background_pixel(
    color: QColor,
    backgrounds: list[QColor],
    tolerance: int,
    alpha_threshold: int,
) -> bool:
    if color.alpha() <= alpha_threshold:
        return True
    if _is_background_pixel(color, backgrounds, tolerance, alpha_threshold):
        return True
    return _is_neutral_background_artifact(color, alpha_threshold)


def _is_neutral_background_artifact(color: QColor, alpha_threshold: int) -> bool:
    if color.alpha() <= alpha_threshold:
        return True
    spread = max(color.red(), color.green(), color.blue()) - min(color.red(), color.green(), color.blue())
    luma = (color.red() * 299 + color.green() * 587 + color.blue() * 114) // 1000
    return spread <= 18 and (luma <= 40 or luma >= 205)


def _is_background_pixel(
    color: QColor,
    backgrounds: list[QColor],
    tolerance: int,
    alpha_threshold: int,
) -> bool:
    if color.alpha() <= alpha_threshold:
        return True
    for background in backgrounds:
        if background.alpha() <= alpha_threshold and color.alpha() <= alpha_threshold:
            return True
        if (
            abs(color.red() - background.red()) <= tolerance
            and abs(color.green() - background.green()) <= tolerance
            and abs(color.blue() - background.blue()) <= tolerance
            and abs(color.alpha() - background.alpha()) <= tolerance
        ):
            return True
    return False


def build_animation_generation_prompt(
    user_prompt: str,
    *,
    frame_count: int,
    frame_width: int,
    frame_height: int,
    sheet_width: int,
    sheet_height: int,
    api_canvas_width: int | None = None,
    api_canvas_height: int | None = None,
    pixel_mode: bool = False,
) -> str:
    style_rules = (
        "Use professional pixel-art animation rules: crisp square pixels, nearest-neighbor look, limited palette, "
        "no blur, no painterly gradients, no anti-aliased outlines, consistent silhouette and grid-aligned motion."
        if pixel_mode
        else "Use clean game art animation rules: consistent character/object identity, stable camera, readable silhouette, coherent motion."
    )
    return (
        f"The attached reference image is the exact locked sprite-sheet template to edit in place. "
        f"Keep the full output canvas exactly {api_canvas_width or sheet_width}x{api_canvas_height or sheet_height}. "
        f"The active sprite-sheet region is centered inside the canvas and exactly {sheet_width}x{sheet_height}: "
        f"exactly {frame_count} equal cells in one horizontal row. "
        f"The template uses 1-pixel pure black grid lines around and between cells. Keep those black grid lines perfectly straight, 1 pixel thick, and unchanged. "
        f"Each drawable inner cell is exactly {frame_width}x{frame_height}. Do not crop, resize, add margins, add separators, add borders, or change the cell grid. "
        f"Keep any padding outside the active sprite-sheet region transparent and unchanged. "
        f"Never let artwork cross outside a black-framed cell. The anchor point of the main subject is the center of each inner cell; keep that anchor centered and stable in every frame. "
        f"Preserve transparent background outside the artwork when present; never fill empty areas with black or white blocks. "
        f"Frame 1 should stay closest to the supplied pose. Modify the later repeated cells into animation frames only inside their fixed cells. "
        f"Animation request: {user_prompt.strip()}. "
        f"{style_rules} Return the sprite sheet only."
    )


def build_animation_frame_generation_prompt(
    user_prompt: str,
    *,
    frame_index: int,
    frame_count: int,
    final_frame_width: int,
    final_frame_height: int,
    active_frame_width: int,
    active_frame_height: int,
    api_canvas_width: int,
    api_canvas_height: int,
    pixel_mode: bool = False,
) -> str:
    index = max(1, int(frame_index))
    count = max(1, int(frame_count))
    phase = round((index - 1) * 100 / count) if count > 1 else 0
    style_rules = (
        "Pixel-art rules: use crisp square pixel clusters, hard edges, limited palette, nearest-neighbor readability, "
        "no blur, no painterly gradients, no anti-aliased outlines, no subpixel soft shading, and keep all details aligned to the pixel grid."
        if pixel_mode
        else "Game-art rules: keep the same subject identity, stable camera, readable silhouette, consistent lighting, and coherent motion."
    )
    first_frame_rule = (
        "This is frame 1, so keep it closest to the supplied reference pose while preparing the motion cycle."
        if index == 1
        else "Change the pose/expression/secondary motion only as needed for this phase; do not redesign the subject."
    )
    return (
        "The attached image is a single animation-frame edit canvas, not a sprite sheet. "
        f"Return exactly one frame image for Frame {index} of {count}, phase {phase}% of the animation cycle. "
        f"Keep the full output canvas exactly {api_canvas_width}x{api_canvas_height}. "
        f"The active frame region is centered inside that canvas and is exactly {active_frame_width}x{active_frame_height}; "
        f"draw only inside this active region. The app will crop that active region and downsample it to the final frame size {final_frame_width}x{final_frame_height}. "
        "Do not create a grid, border, contact sheet, multi-pose sheet, extra cells, text labels, margins, or separators. "
        "Keep the main subject's anchor point centered and stable, with feet/base/contact point matching the reference when present. "
        "Preserve transparent background outside the artwork when present. "
        f"{first_frame_rule} Animation request: {user_prompt.strip()}. "
        f"{style_rules}"
    )


def build_sequence_frame_generation_jobs(
    settings: AppSettings,
    user_prompt: str,
    source_frames: list[QImage],
    frame_count: int,
    frame_size: QSize,
    *,
    pixel_mode: bool = False,
    temp_dir: Path | None = None,
) -> list[FrameGenerationJob]:
    valid_frames = [frame for frame in source_frames if not frame.isNull()]
    if not valid_frames:
        raise AiImageError("请先导入或拖入一张参考图片。")
    count = max(1, int(frame_count))
    output_size = QSize(max(1, frame_size.width()), max(1, frame_size.height()))
    target_dir = temp_dir or (Path(tempfile.gettempdir()) / "gamedesigner_sequence_frames")
    target_dir.mkdir(parents=True, exist_ok=True)
    batch_id = uuid.uuid4().hex[:8]
    jobs: list[FrameGenerationJob] = []
    for index in range(count):
        source = valid_frames[index] if index < len(valid_frames) else valid_frames[-1]
        normalized = fit_image_to_frame(source, output_size, pixel_mode=pixel_mode)
        source_has_transparency = image_has_transparency(normalized)
        template = build_api_generation_template(
            normalized,
            settings,
            content_frame_size=output_size,
            pixel_mode=pixel_mode,
            scale_up_to_canvas=True,
        )
        if template.image.isNull():
            raise AiImageError("无法生成逐帧 AI 参考模板。")
        path = target_dir / f"sequence_frame_{batch_id}_{index + 1:02d}.png"
        if not save_spritesheet(template.image, path, pixel_mode=pixel_mode):
            raise AiImageError("无法保存逐帧 AI 参考模板。")
        prompt = build_animation_frame_generation_prompt(
            user_prompt,
            frame_index=index + 1,
            frame_count=count,
            final_frame_width=output_size.width(),
            final_frame_height=output_size.height(),
            active_frame_width=template.sheet_rect.width(),
            active_frame_height=template.sheet_rect.height(),
            api_canvas_width=template.api_size.width(),
            api_canvas_height=template.api_size.height(),
            pixel_mode=pixel_mode,
        )
        jobs.append(
            FrameGenerationJob(
                index=index,
                prompt=prompt,
                template_path=path,
                template=template,
                source_has_transparency=source_has_transparency,
            )
        )
    return jobs


def generate_sequence_frames_with_ai(
    settings: AppSettings,
    jobs: list[FrameGenerationJob],
    *,
    frame_size: QSize,
    pixel_mode: bool = False,
    progress: Callable[[str], None] | None = None,
    image_generator: Callable = generate_ai_images,
) -> list[QImage]:
    if not jobs:
        raise AiImageError("没有可生成的序列帧任务。")
    output_size = QSize(max(1, frame_size.width()), max(1, frame_size.height()))
    frames: list[QImage] = []
    total = len(jobs)
    for position, job in enumerate(jobs, start=1):
        request_settings = copy.copy(settings)
        request_settings.ai_image_count = 1
        request_settings.ai_image_output_format = "png"
        request_settings.ai_image_size = (
            f"{max(1, job.template.api_size.width())}x{max(1, job.template.api_size.height())}"
        )
        request_settings.ai_image_background = sequence_request_background(
            request_settings,
            job.source_has_transparency,
        )
        if progress is not None:
            progress(f"第 {position}/{total} 帧 prompt：\n{job.prompt}")
            if job.source_has_transparency and request_settings.ai_image_background != "transparent":
                progress("当前模型不支持透明背景参数，已改用自动背景；返回后会清理边角背景。")
        request = build_ai_image_request(request_settings, job.prompt, [job.template_path])
        if progress is not None:
            progress(f"正在调用 {request.model} 生成第 {position}/{total} 帧...")
        images = image_generator(request)
        if not images:
            raise AiImageError(f"生图服务没有返回第 {position} 帧。")
        returned = QImage.fromData(images[0].data)
        if returned.isNull():
            raise AiImageError(f"生图服务返回的第 {position} 帧无法读取。")
        active = crop_returned_api_canvas(returned, job.template.sheet_rect, job.template.api_size)
        frame = scale_image_to_exact_frame(active, output_size, pixel_mode=pixel_mode)
        if job.source_has_transparency:
            frame = clear_connected_corner_background(frame)
        frames.append(frame)
    if any(job.source_has_transparency for job in jobs):
        frames = align_frame_content(frames, output_size, pixel_mode=pixel_mode)
    return stabilize_frame_anchors(frames, output_size, pixel_mode=pixel_mode)


class SequenceFrameGenerationThread(QThread):
    succeeded = Signal(object)
    failed = Signal(str)
    progress = Signal(str)

    def __init__(
        self,
        settings: AppSettings,
        jobs: list[FrameGenerationJob],
        *,
        frame_size: QSize,
        pixel_mode: bool = False,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.settings = copy.copy(settings)
        self.jobs = list(jobs)
        self.frame_size = QSize(max(1, frame_size.width()), max(1, frame_size.height()))
        self.pixel_mode = bool(pixel_mode)

    def run(self) -> None:  # type: ignore[override]
        try:
            self.progress.emit("正在构建逐帧 AI 生图请求...")
            frames = generate_sequence_frames_with_ai(
                self.settings,
                self.jobs,
                frame_size=self.frame_size,
                pixel_mode=self.pixel_mode,
                progress=self.progress.emit,
            )
            if not frames:
                raise AiImageError("AI 没有返回可用的序列帧。")
        except Exception as exc:
            self.failed.emit(str(exc))
            return
        self.succeeded.emit(frames)


def crop_returned_api_canvas(image: QImage, sheet_rect: QRect, api_size: QSize) -> QImage:
    if image.isNull() or sheet_rect.isNull() or api_size.width() <= 0 or api_size.height() <= 0:
        return image
    x_scale = image.width() / max(1, api_size.width())
    y_scale = image.height() / max(1, api_size.height())
    rect = QRect(
        round(sheet_rect.x() * x_scale),
        round(sheet_rect.y() * y_scale),
        max(1, round(sheet_rect.width() * x_scale)),
        max(1, round(sheet_rect.height() * y_scale)),
    )
    rect = rect.intersected(QRect(0, 0, image.width(), image.height()))
    if rect.isEmpty():
        return image
    return image.copy(rect)


class AnimationPreviewWidget(QWidget):
    filesDropped = Signal(list)

    def __init__(self, *, pixel_mode: bool = False, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.pixel_mode = bool(pixel_mode)
        self.frames: list[QImage] = []
        self.current_index = 0
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._advance)
        self.setAcceptDrops(True)
        self.setMinimumSize(420, 300)

    def set_frames(self, frames: list[QImage]) -> None:
        self.frames = [frame.copy() for frame in frames if not frame.isNull()]
        self.current_index = max(0, min(self.current_index, len(self.frames) - 1))
        self.update()

    def set_current_index(self, index: int) -> None:
        self.current_index = max(0, min(int(index), max(0, len(self.frames) - 1)))
        self.update()

    def set_fps(self, fps: int) -> None:
        if self._timer.isActive():
            self._timer.start(self._interval(fps))

    def set_playing(self, playing: bool, fps: int) -> None:
        if playing and self.frames:
            self._timer.start(self._interval(fps))
        else:
            self._timer.stop()

    def is_playing(self) -> bool:
        return self._timer.isActive()

    def paintEvent(self, _event) -> None:  # type: ignore[override]
        painter = QPainter(self)
        painter.fillRect(self.rect(), QColor("#202026"))
        self._paint_checkerboard(painter)
        if self.frames:
            frame = self.frames[self.current_index % len(self.frames)]
            pixmap = QPixmap.fromImage(frame)
            if not pixmap.isNull():
                scaled = pixmap.scaled(
                    self.width(),
                    self.height(),
                    Qt.KeepAspectRatio,
                    Qt.FastTransformation if self.pixel_mode else Qt.SmoothTransformation,
                )
                x = (self.width() - scaled.width()) // 2
                y = (self.height() - scaled.height()) // 2
                painter.setRenderHint(QPainter.SmoothPixmapTransform, not self.pixel_mode)
                painter.drawPixmap(x, y, scaled)
        else:
            painter.setPen(QColor("#C7CBD1"))
            painter.drawText(self.rect(), Qt.AlignCenter, "导入图片后预览动画")
        painter.end()

    def dragEnterEvent(self, event) -> None:  # type: ignore[override]
        if image_paths_from_drop_event(event):
            event.acceptProposedAction()
            return
        super().dragEnterEvent(event)

    def dragMoveEvent(self, event) -> None:  # type: ignore[override]
        if image_paths_from_drop_event(event):
            event.acceptProposedAction()
            return
        super().dragMoveEvent(event)

    def dropEvent(self, event) -> None:  # type: ignore[override]
        paths = image_paths_from_drop_event(event)
        if paths:
            self.filesDropped.emit(paths)
            event.acceptProposedAction()
            return
        super().dropEvent(event)

    def _advance(self) -> None:
        if not self.frames:
            return
        self.current_index = (self.current_index + 1) % len(self.frames)
        self.update()

    def _interval(self, fps: int) -> int:
        return max(16, round(1000 / max(1, int(fps))))

    def _paint_checkerboard(self, painter: QPainter) -> None:
        tile = 18
        c1 = QColor("#2B2D33")
        c2 = QColor("#343740")
        for y in range(0, self.height(), tile):
            for x in range(0, self.width(), tile):
                painter.fillRect(x, y, tile, tile, c1 if ((x // tile) + (y // tile)) % 2 == 0 else c2)


class FrameListWidget(QListWidget):
    filesDropped = Signal(list)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setAcceptDrops(True)

    def dragEnterEvent(self, event) -> None:  # type: ignore[override]
        if image_paths_from_drop_event(event):
            event.acceptProposedAction()
            return
        super().dragEnterEvent(event)

    def dragMoveEvent(self, event) -> None:  # type: ignore[override]
        if image_paths_from_drop_event(event):
            event.acceptProposedAction()
            return
        super().dragMoveEvent(event)

    def dropEvent(self, event) -> None:  # type: ignore[override]
        paths = image_paths_from_drop_event(event)
        if paths:
            self.filesDropped.emit(paths)
            event.acceptProposedAction()
            return
        super().dropEvent(event)


def image_paths_from_drop_event(event) -> list[str]:
    mime = event.mimeData()
    if not mime.hasUrls():
        return []
    paths: list[str] = []
    for url in mime.urls():
        path = Path(url.toLocalFile())
        if path.suffix.lower() in IMAGE_DROP_EXTENSIONS and path.exists():
            paths.append(str(path))
    return paths


class SequenceFrameDialog(QDialog):
    def __init__(
        self,
        parent: QWidget | None = None,
        *,
        pixel_mode: bool = False,
        initial_path: str | Path | None = None,
        output_path: str | Path | None = None,
        settings: AppSettings | None = None,
    ) -> None:
        super().__init__(parent)
        self.pixel_mode = bool(pixel_mode)
        self.output_path = Path(output_path) if output_path else None
        self.settings = settings
        self.result_path: str | None = None
        self.source_frames: list[QImage] = []
        self.frames: list[QImage] = []
        self.base_image = QImage()
        self.base_image_path: Path | None = None
        self._generation_thread: SequenceFrameGenerationThread | None = None
        self._last_dir = Path(initial_path).parent if initial_path else Path.home()

        self.setWindowTitle("像素序列帧动画" if self.pixel_mode else "序列帧动画")
        self.setModal(True)
        self.setAcceptDrops(True)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 14, 14, 14)
        layout.setSpacing(10)
        layout.addLayout(self._toolbar())

        prompt_row = QVBoxLayout()
        prompt_row.setSpacing(6)
        prompt_row.addWidget(QLabel("动画描述", self))
        self.prompt_edit = QPlainTextEdit(self)
        self.prompt_edit.setPlaceholderText("例如：角色向右走 6 帧，身体轻微上下起伏，手臂自然摆动")
        self.prompt_edit.setFixedHeight(72)
        self.prompt_edit.setAcceptDrops(False)
        prompt_row.addWidget(self.prompt_edit)
        layout.addLayout(prompt_row)

        self.log_edit = QPlainTextEdit(self)
        self.log_edit.setReadOnly(True)
        self.log_edit.setFixedHeight(118)
        self.log_edit.setPlaceholderText("AI 调用日志会显示在这里")
        self.log_edit.setAcceptDrops(False)
        layout.addWidget(self.log_edit)

        output_row = QHBoxLayout()
        output_row.setSpacing(8)
        output_row.addWidget(QLabel("导出位置", self))
        self.output_path_edit = QLineEdit(self)
        self.output_path_edit.setPlaceholderText("选择导出的 PNG 文件路径")
        if self.output_path is not None:
            self.output_path_edit.setText(str(self.output_path))
        output_row.addWidget(self.output_path_edit, 1)
        choose_output_button = QPushButton("选择", self)
        choose_output_button.clicked.connect(self._choose_output_path)
        output_row.addWidget(choose_output_button)
        layout.addLayout(output_row)

        self.preview = AnimationPreviewWidget(pixel_mode=self.pixel_mode, parent=self)
        self.preview.filesDropped.connect(self._load_dropped_images)
        self.fps_spin.valueChanged.connect(self.preview.set_fps)
        layout.addWidget(self.preview, 1)

        self.frame_list = FrameListWidget(self)
        self.frame_list.setViewMode(QListView.IconMode)
        self.frame_list.setFlow(QListView.LeftToRight)
        self.frame_list.setMovement(QListView.Static)
        self.frame_list.setWrapping(False)
        self.frame_list.setIconSize(QSize(82, 82))
        self.frame_list.setGridSize(QSize(102, 112))
        self.frame_list.setFixedHeight(124)
        self.frame_list.currentRowChanged.connect(self._select_frame)
        self.frame_list.filesDropped.connect(self._load_dropped_images)
        layout.addWidget(self.frame_list)

        self.status_label = QLabel("导入一张图片开始制作序列帧", self)
        self.status_label.setObjectName("mutedLabel")
        layout.addWidget(self.status_label)

        if initial_path:
            self.load_image(initial_path)
        self.resize(920, 680)
        restore_window_layout(self, "pixel_sequence_frame_dialog" if self.pixel_mode else "sequence_frame_dialog")

    def load_image(self, path: str | Path) -> bool:
        image = QImage(str(path))
        if image.isNull():
            return False
        source_path = Path(path)
        self._last_dir = source_path.parent
        self.base_image_path = source_path
        self.base_image = image.convertToFormat(QImage.Format_ARGB32_Premultiplied)
        if hasattr(self, "log_edit"):
            self._append_log(f"已导入参考图：{source_path}")
        self.width_spin.blockSignals(True)
        self.height_spin.blockSignals(True)
        self.width_spin.setValue(max(1, self.base_image.width()))
        self.height_spin.setValue(max(1, self.base_image.height()))
        self.width_spin.blockSignals(False)
        self.height_spin.blockSignals(False)
        self._seed_frames()
        return True

    def _toolbar(self) -> QHBoxLayout:
        layout = QHBoxLayout()
        layout.setSpacing(8)

        open_button = QPushButton("导入图片", self)
        open_button.clicked.connect(self._open_image)
        layout.addWidget(open_button)

        layout.addWidget(QLabel("帧数", self))
        self.frame_count_spin = QSpinBox(self)
        self.frame_count_spin.setRange(1, 48)
        self.frame_count_spin.setValue(6)
        self.frame_count_spin.valueChanged.connect(self._sync_frame_count)
        layout.addWidget(self.frame_count_spin)

        layout.addWidget(QLabel("帧宽", self))
        self.width_spin = QSpinBox(self)
        self.width_spin.setRange(1, 4096)
        self.width_spin.setValue(256 if self.pixel_mode else 512)
        self.width_spin.valueChanged.connect(self._refresh_frames)
        layout.addWidget(self.width_spin)

        layout.addWidget(QLabel("帧高", self))
        self.height_spin = QSpinBox(self)
        self.height_spin.setRange(1, 4096)
        self.height_spin.setValue(256 if self.pixel_mode else 512)
        self.height_spin.valueChanged.connect(self._refresh_frames)
        layout.addWidget(self.height_spin)

        seed_button = QPushButton("铺基准帧", self)
        seed_button.clicked.connect(self._seed_frames)
        layout.addWidget(seed_button)

        self.ai_generate_button = QPushButton("AI生成帧", self)
        self.ai_generate_button.clicked.connect(self._generate_frames_with_ai)
        layout.addWidget(self.ai_generate_button)

        duplicate_button = QPushButton("复制帧", self)
        duplicate_button.clicked.connect(self._duplicate_frame)
        layout.addWidget(duplicate_button)

        edit_button = QPushButton("编辑帧", self)
        edit_button.clicked.connect(self._edit_frame)
        layout.addWidget(edit_button)

        delete_button = QPushButton("删除帧", self)
        delete_button.clicked.connect(self._delete_frame)
        layout.addWidget(delete_button)

        layout.addWidget(QLabel("FPS", self))
        self.fps_spin = QSpinBox(self)
        self.fps_spin.setRange(1, 60)
        self.fps_spin.setValue(8 if self.pixel_mode else 12)
        layout.addWidget(self.fps_spin)

        self.play_button = QPushButton("播放", self)
        self.play_button.setCheckable(True)
        self.play_button.toggled.connect(self._toggle_playback)
        layout.addWidget(self.play_button)

        save_button = QPushButton("导出横向图", self)
        save_button.setObjectName("accentButton")
        save_button.clicked.connect(self._export_spritesheet)
        layout.addWidget(save_button)

        close_button = QPushButton("关闭", self)
        close_button.clicked.connect(self.accept)
        layout.addWidget(close_button)
        return layout

    def _open_image(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self,
            "导入序列帧基准图片",
            str(self._last_dir),
            "图片 (*.png *.jpg *.jpeg *.bmp *.gif);;所有文件 (*.*)",
        )
        if not path:
            return
        if not self.load_image(path):
            QMessageBox.warning(self, "导入失败", "无法读取这张图片。")

    def _load_dropped_images(self, paths: list[str]) -> None:
        valid_images: list[tuple[Path, QImage]] = []
        for text in paths:
            path = Path(text)
            image = QImage(str(path))
            if not image.isNull():
                valid_images.append((path, image.convertToFormat(QImage.Format_ARGB32_Premultiplied)))
        if not valid_images:
            QMessageBox.warning(self, "导入失败", "拖入的文件里没有可读取的图片。")
            return
        self._last_dir = valid_images[0][0].parent
        self.base_image_path = valid_images[0][0]
        self.base_image = valid_images[0][1].copy()
        self._append_log(f"已拖入参考图：{self.base_image_path}")
        if len(valid_images) == 1:
            self.width_spin.blockSignals(True)
            self.height_spin.blockSignals(True)
            self.width_spin.setValue(max(1, self.base_image.width()))
            self.height_spin.setValue(max(1, self.base_image.height()))
            self.width_spin.blockSignals(False)
            self.height_spin.blockSignals(False)
            self._seed_frames()
            return
        self.source_frames = [image.copy() for _path, image in valid_images]
        self.frame_count_spin.blockSignals(True)
        self.width_spin.blockSignals(True)
        self.height_spin.blockSignals(True)
        self.frame_count_spin.setValue(len(self.source_frames))
        self.width_spin.setValue(max(1, self.source_frames[0].width()))
        self.height_spin.setValue(max(1, self.source_frames[0].height()))
        self.frame_count_spin.blockSignals(False)
        self.width_spin.blockSignals(False)
        self.height_spin.blockSignals(False)
        self._refresh_frames(select_row=0)

    def _generate_frames_with_ai(self) -> None:
        if self._generation_thread is not None:
            return
        if self.settings is None:
            QMessageBox.information(self, "AI生成帧", "当前没有可用的 AI 生图设置。")
            return
        if self.base_image.isNull():
            QMessageBox.information(self, "AI生成帧", "请先导入或拖入一张参考图片。")
            return
        user_prompt = self.prompt_edit.toPlainText().strip()
        if not user_prompt:
            QMessageBox.information(self, "AI生成帧", "请先填写动画描述。")
            self.prompt_edit.setFocus(Qt.OtherFocusReason)
            return
        try:
            jobs = self._generation_frame_jobs(user_prompt)
        except AiImageError as exc:
            QMessageBox.warning(self, "AI生成帧", str(exc) or "无法生成逐帧参考模板。")
            return
        if not jobs:
            QMessageBox.warning(self, "AI生成帧", "无法生成逐帧参考模板。")
            return
        self._append_log(f"动画描述：{user_prompt}")
        self._append_log("生成策略：逐帧单图编辑；AI 只生成当前帧，横向序列帧由本地算法等宽拼接。")
        self._append_log(
            f"最终帧尺寸：{self.width_spin.value()}x{self.height_spin.value()}；"
            f"生成帧数：{len(jobs)}；"
            f"每帧会独立提交参考图、裁回目标尺寸，再本地拼成横向图。"
        )
        self.ai_generate_button.setEnabled(False)
        self.ai_generate_button.setText("AI生成中...")
        self.status_label.setText("正在调用 AI 逐帧生成序列帧...")
        thread = SequenceFrameGenerationThread(
            self.settings,
            jobs,
            frame_size=self._frame_size(),
            pixel_mode=self.pixel_mode,
            parent=self,
        )
        thread.succeeded.connect(self._ai_generation_succeeded)
        thread.failed.connect(self._ai_generation_failed)
        thread.progress.connect(self._append_log)
        thread.finished.connect(self._ai_generation_finished)
        self._generation_thread = thread
        thread.start()

    def _seed_frames(self) -> None:
        source = self.base_image if not self.base_image.isNull() else self._blank_frame()
        self.source_frames = [source.copy() for _ in range(self.frame_count_spin.value())]
        self._refresh_frames(select_row=0)

    def _sync_frame_count(self, value: int) -> None:
        if value <= 0:
            return
        if not self.source_frames:
            self._refresh_frames(select_row=0)
            return
        target = max(1, int(value))
        while len(self.source_frames) < target:
            self.source_frames.append(self.source_frames[-1].copy())
        if len(self.source_frames) > target:
            del self.source_frames[target:]
        self._refresh_frames(select_row=min(self._current_row(), target - 1))

    def _duplicate_frame(self) -> None:
        if not self.source_frames:
            self._seed_frames()
            return
        row = self._current_row()
        self.source_frames.insert(row + 1, self.source_frames[row].copy())
        self.frame_count_spin.blockSignals(True)
        self.frame_count_spin.setValue(len(self.source_frames))
        self.frame_count_spin.blockSignals(False)
        self._refresh_frames(select_row=row + 1)

    def _delete_frame(self) -> None:
        if len(self.source_frames) <= 1:
            return
        row = self._current_row()
        del self.source_frames[row]
        self.frame_count_spin.blockSignals(True)
        self.frame_count_spin.setValue(len(self.source_frames))
        self.frame_count_spin.blockSignals(False)
        self._refresh_frames(select_row=max(0, row - 1))

    def _edit_frame(self) -> None:
        if not self.frames:
            self._seed_frames()
        row = self._current_row()
        with tempfile.TemporaryDirectory() as folder:
            temp_dir = Path(folder)
            input_path = temp_dir / "frame.png"
            output_path = temp_dir / "edited.png"
            self.source_frames[row].save(str(input_path), "PNG")
            dialog = ImagePaintDialog(self, initial_path=input_path, output_path=output_path)
            if dialog.exec() != ImagePaintDialog.Accepted or not dialog.result_path:
                return
            edited = QImage(dialog.result_path)
            if edited.isNull():
                QMessageBox.warning(self, "编辑失败", "无法读取编辑后的帧。")
                return
            self.source_frames[row] = edited.convertToFormat(QImage.Format_ARGB32_Premultiplied)
        self._refresh_frames(select_row=row)

    def _export_spritesheet(self) -> None:
        if not self.frames:
            QMessageBox.information(self, "序列帧动画", "请先导入图片并制作帧。")
            return
        path = self._selected_output_path()
        if path is None:
            return
        export_frames = self._normalized_frames_for_export()
        sheet = build_horizontal_spritesheet(export_frames, pixel_mode=self.pixel_mode, frame_size=self._frame_size())
        if sheet.isNull() or not save_spritesheet(sheet, path, pixel_mode=self.pixel_mode):
            QMessageBox.warning(self, "导出失败", "无法保存横向序列帧图。")
            return
        self.source_frames = [frame.copy() for frame in export_frames]
        self._refresh_frames(select_row=self._current_row())
        self.result_path = str(path)
        self.output_path = path
        self.output_path_edit.setText(str(path))
        self.status_label.setText(f"已导出：{path}")

    def _ai_generation_succeeded(self, frames: object) -> None:
        generated_frames = [frame for frame in list(frames) if isinstance(frame, QImage) and not frame.isNull()]
        if not generated_frames:
            QMessageBox.warning(self, "AI生成帧", "AI 没有返回可用的序列帧。")
            return
        self.source_frames = [frame.copy() for frame in generated_frames]
        self.frame_count_spin.blockSignals(True)
        self.frame_count_spin.setValue(len(self.source_frames))
        self.frame_count_spin.blockSignals(False)
        self._refresh_frames(select_row=0)
        self.status_label.setText("AI 序列帧已生成，可预览或导出横向图。")
        self._append_log(f"已按固定帧格切出 {len(self.source_frames)} 帧。")

    def _ai_generation_failed(self, message: str) -> None:
        QMessageBox.warning(self, "AI生成帧", message or "AI 生成序列帧失败。")
        self.status_label.setText(message or "AI 生成序列帧失败。")
        self._append_log(message or "AI 生成序列帧失败。")

    def _ai_generation_finished(self) -> None:
        self._generation_thread = None
        self.ai_generate_button.setEnabled(True)
        self.ai_generate_button.setText("AI生成帧")

    def _toggle_playback(self, playing: bool) -> None:
        self.play_button.setText("暂停" if playing else "播放")
        self.preview.set_playing(playing, self.fps_spin.value())

    def _select_frame(self, row: int) -> None:
        if row < 0:
            return
        self.preview.set_current_index(row)

    def _refresh_frames(self, _value: int | None = None, *, select_row: int | None = None) -> None:
        size = self._frame_size()
        previous_row = self._current_row()
        self.frames = [fit_image_to_frame(frame, size, pixel_mode=self.pixel_mode) for frame in self.source_frames]
        self.preview.set_frames(self.frames)
        self.frame_list.blockSignals(True)
        self.frame_list.clear()
        icon_size = self.frame_list.iconSize()
        for index, frame in enumerate(self.frames):
            thumb = QPixmap.fromImage(frame).scaled(
                icon_size,
                Qt.KeepAspectRatio,
                Qt.FastTransformation if self.pixel_mode else Qt.SmoothTransformation,
            )
            item = QListWidgetItem(QIcon(thumb), f"{index + 1}")
            self.frame_list.addItem(item)
        if self.frames:
            target_row = previous_row if select_row is None else select_row
            self.frame_list.setCurrentRow(max(0, min(target_row, len(self.frames) - 1)))
        self.frame_list.blockSignals(False)
        if self.frames:
            self.preview.set_current_index(self.frame_list.currentRow())
        self._update_status()

    def _update_status(self) -> None:
        if not self.frames:
            self.status_label.setText("导入一张图片开始制作序列帧")
            return
        size = self._frame_size()
        sheet_width = size.width() * len(self.frames)
        self.status_label.setText(
            f"{len(self.frames)} 帧    单帧 {size.width()}×{size.height()}    导出 {sheet_width}×{size.height()}"
        )

    def _current_row(self) -> int:
        if not self.source_frames:
            return 0
        row = self.frame_list.currentRow()
        return max(0, min(row if row >= 0 else 0, len(self.source_frames) - 1))

    def _frame_size(self) -> QSize:
        return QSize(max(1, self.width_spin.value()), max(1, self.height_spin.value()))

    def _blank_frame(self) -> QImage:
        image = QImage(self._frame_size(), QImage.Format_ARGB32_Premultiplied)
        image.fill(Qt.transparent)
        return image

    def _normalized_frames_for_export(self) -> list[QImage]:
        size = self._frame_size()
        frames = [fit_image_to_frame(frame, size, pixel_mode=self.pixel_mode) for frame in self.source_frames]
        if any(image_has_transparency(frame) for frame in frames):
            frames = [clear_edge_background_artifacts(frame) for frame in frames]
        return stabilize_frame_anchors(frames, size, pixel_mode=self.pixel_mode)

    def _choose_output_path(self) -> None:
        default_name = "像素序列帧.png" if self.pixel_mode else "序列帧.png"
        current = self.output_path_edit.text().strip() or str(self._last_dir / default_name)
        selected, _ = QFileDialog.getSaveFileName(
            self,
            "选择导出位置",
            current,
            "PNG 图片 (*.png)",
        )
        if selected:
            self.output_path_edit.setText(selected)

    def _selected_output_path(self) -> Path | None:
        text = self.output_path_edit.text().strip()
        if text:
            path = Path(text)
            if path.suffix.lower() != ".png":
                path = path.with_suffix(".png")
            path.parent.mkdir(parents=True, exist_ok=True)
            return path
        default_name = "像素序列帧.png" if self.pixel_mode else "序列帧.png"
        selected, _ = QFileDialog.getSaveFileName(
            self,
            "导出横向序列帧图",
            str(self._last_dir / default_name),
            "PNG 图片 (*.png)",
        )
        if not selected:
            return None
        path = Path(selected)
        self.output_path_edit.setText(str(path))
        path.parent.mkdir(parents=True, exist_ok=True)
        return path

    def _append_log(self, message: str) -> None:
        text = str(message or "").strip()
        if not text:
            return
        if not self.log_edit.toPlainText().strip():
            self.log_edit.setPlainText(text)
        else:
            self.log_edit.appendPlainText(f"\n{text}")
        self.log_edit.verticalScrollBar().setValue(self.log_edit.verticalScrollBar().maximum())

    def _generation_frame_jobs(self, user_prompt: str) -> list[FrameGenerationJob]:
        if self.base_image.isNull():
            return []
        if self.settings is None:
            raise AiImageError("当前没有可用的 AI 生图设置。")
        frame_count = self.frame_count_spin.value()
        frames = self.source_frames if self.source_frames else [self.base_image.copy() for _ in range(frame_count)]
        temp_dir = Path(tempfile.gettempdir()) / "gamedesigner_sequence_frames"
        return build_sequence_frame_generation_jobs(
            self.settings,
            user_prompt,
            frames,
            frame_count,
            self._frame_size(),
            pixel_mode=self.pixel_mode,
            temp_dir=temp_dir,
        )

    def _generation_template_path(self) -> tuple[Path | None, GenerationTemplate]:
        if self.base_image.isNull():
            return None, GenerationTemplate(QImage(), QRect(), QSize(), QSize(), QSize())
        frame_count = self.frame_count_spin.value()
        frame_size = self._frame_size()
        frames = self.source_frames if self.source_frames else [self.base_image.copy() for _ in range(frame_count)]
        sheet, content_frame_size = build_bordered_generation_template_spritesheet(
            frames,
            frame_count,
            frame_size,
            pixel_mode=self.pixel_mode,
        )
        if sheet.isNull():
            return None, GenerationTemplate(QImage(), QRect(), QSize(), QSize(), QSize())
        template = build_api_generation_template(
            sheet,
            self.settings,
            content_frame_size=content_frame_size,
            pixel_mode=self.pixel_mode,
        )
        if template.image.isNull():
            return None, template
        temp_dir = Path(tempfile.gettempdir()) / "gamedesigner_sequence_frames"
        temp_dir.mkdir(parents=True, exist_ok=True)
        path = temp_dir / f"sequence_frame_template_{uuid.uuid4().hex[:8]}.png"
        if not save_spritesheet(template.image, path, pixel_mode=self.pixel_mode):
            return None, template
        return path, template

    def dragEnterEvent(self, event) -> None:  # type: ignore[override]
        if image_paths_from_drop_event(event):
            event.acceptProposedAction()
            return
        super().dragEnterEvent(event)

    def dragMoveEvent(self, event) -> None:  # type: ignore[override]
        if image_paths_from_drop_event(event):
            event.acceptProposedAction()
            return
        super().dragMoveEvent(event)

    def dropEvent(self, event) -> None:  # type: ignore[override]
        paths = image_paths_from_drop_event(event)
        if paths:
            self._load_dropped_images(paths)
            event.acceptProposedAction()
            return
        super().dropEvent(event)

    def done(self, result: int) -> None:  # type: ignore[override]
        if self._generation_thread is not None:
            QMessageBox.information(self, "AI生成帧", "AI 正在生成序列帧，请等待完成后再关闭。")
            return
        self.preview.set_playing(False, self.fps_spin.value())
        save_window_layout(self, "pixel_sequence_frame_dialog" if self.pixel_mode else "sequence_frame_dialog")
        super().done(result)
