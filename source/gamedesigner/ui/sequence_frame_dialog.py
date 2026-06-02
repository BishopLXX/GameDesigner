from __future__ import annotations

import copy
import tempfile
from pathlib import Path

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
    QSpinBox,
    QVBoxLayout,
    QWidget,
    QDialog,
)

from .image_paint_dialog import ImagePaintDialog
from ..image_ai import AiImageError, build_ai_image_request, generate_ai_images
from ..storage import AppSettings
from ..window_layouts import restore_window_layout, save_window_layout


PIXEL_ART_METADATA_KEY = "GameDesignerPixelArt"
IMAGE_DROP_EXTENSIONS = {".png", ".jpg", ".jpeg", ".bmp", ".gif", ".webp"}


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
    return [fit_image_to_frame(frame, frame_size, pixel_mode=pixel_mode) for frame in frames]


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
        painter.drawImage((target_width - scaled.width()) // 2, (target_height - scaled.height()) // 2, scaled)
        painter.end()
        aligned.append(output)
    return aligned


def foreground_content_rect(image: QImage, *, background_tolerance: int = 18, alpha_threshold: int = 8) -> QRect | None:
    if image.isNull():
        return None
    source = image.convertToFormat(QImage.Format_ARGB32_Premultiplied)
    width = source.width()
    height = source.height()
    if width <= 0 or height <= 0:
        return None
    backgrounds = [
        source.pixelColor(0, 0),
        source.pixelColor(width - 1, 0),
        source.pixelColor(0, height - 1),
        source.pixelColor(width - 1, height - 1),
    ]
    min_x = width
    min_y = height
    max_x = -1
    max_y = -1
    for y in range(height):
        for x in range(width):
            color = source.pixelColor(x, y)
            if _is_background_pixel(color, backgrounds, background_tolerance, alpha_threshold):
                continue
            min_x = min(min_x, x)
            min_y = min(min_y, y)
            max_x = max(max_x, x)
            max_y = max(max_y, y)
    if max_x < min_x or max_y < min_y:
        return None
    rect = QRect(min_x, min_y, max_x - min_x + 1, max_y - min_y + 1)
    if rect.width() >= width * 0.92 and rect.height() >= height * 0.92:
        return QRect(0, 0, width, height)
    return rect


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
    pixel_mode: bool = False,
) -> str:
    style_rules = (
        "Use professional pixel-art animation rules: crisp square pixels, nearest-neighbor look, limited palette, "
        "no blur, no painterly gradients, no anti-aliased outlines, consistent silhouette and grid-aligned motion."
        if pixel_mode
        else "Use clean game art animation rules: consistent character/object identity, stable camera, readable silhouette, coherent motion."
    )
    return (
        f"Create one horizontal sprite sheet with exactly {frame_count} equal-sized animation frames in a single row. "
        f"Each frame cell is {frame_width}x{frame_height}. Keep frame boundaries evenly spaced and do not add text, labels, margins, or UI. "
        f"The input image is the visual reference for identity, proportions, palette, and style. "
        f"Animation request: {user_prompt.strip()}. "
        f"{style_rules} Return the sprite sheet only."
    )


class SequenceFrameGenerationThread(QThread):
    succeeded = Signal(object)
    failed = Signal(str)
    progress = Signal(str)

    def __init__(
        self,
        settings: AppSettings,
        prompt: str,
        reference_path: Path | None,
        *,
        frame_count: int,
        frame_size: QSize,
        pixel_mode: bool = False,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.settings = copy.copy(settings)
        self.settings.ai_image_count = 1
        self.settings.ai_image_output_format = "png"
        self.prompt = prompt
        self.reference_path = reference_path
        self.frame_count = max(1, int(frame_count))
        self.frame_size = QSize(max(1, frame_size.width()), max(1, frame_size.height()))
        self.pixel_mode = bool(pixel_mode)

    def run(self) -> None:  # type: ignore[override]
        try:
            self.progress.emit("正在构建 AI 生图请求...")
            request = build_ai_image_request(
                self.settings,
                self.prompt,
                [self.reference_path] if self.reference_path and self.reference_path.exists() else [],
            )
            self.progress.emit(f"正在调用 {request.model} 生成横向序列帧图...")
            images = generate_ai_images(request)
            if not images:
                raise AiImageError("生图服务没有返回序列帧图片。")
            self.progress.emit("已收到 AI 图片，正在按帧数切割并对齐主体...")
            sheet = QImage.fromData(images[0].data)
            if sheet.isNull():
                raise AiImageError("生图服务返回的序列帧图片无法读取。")
            frames = split_horizontal_spritesheet(
                sheet,
                self.frame_count,
                self.frame_size,
                pixel_mode=self.pixel_mode,
                align_content=True,
            )
            if not frames:
                raise AiImageError("无法从 AI 返回图片中切出序列帧。")
        except Exception as exc:
            self.failed.emit(str(exc))
            return
        self.succeeded.emit(frames)


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
        reference_path = self._reference_image_path()
        prompt = build_animation_generation_prompt(
            user_prompt,
            frame_count=self.frame_count_spin.value(),
            frame_width=self.width_spin.value(),
            frame_height=self.height_spin.value(),
            pixel_mode=self.pixel_mode,
        )
        self._append_log(f"动画描述：{user_prompt}")
        self._append_log(f"发送给 AI 的 prompt：\n{prompt}")
        self.ai_generate_button.setEnabled(False)
        self.ai_generate_button.setText("AI生成中...")
        self.status_label.setText("正在调用 AI 生成横向序列帧图...")
        thread = SequenceFrameGenerationThread(
            self.settings,
            prompt,
            reference_path,
            frame_count=self.frame_count_spin.value(),
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
        path = self.output_path
        if path is None:
            default_name = "像素序列帧.png" if self.pixel_mode else "序列帧.png"
            selected, _ = QFileDialog.getSaveFileName(
                self,
                "导出横向序列帧图",
                str(self._last_dir / default_name),
                "PNG 图片 (*.png)",
            )
            if not selected:
                return
            path = Path(selected)
        sheet = build_horizontal_spritesheet(self.source_frames, pixel_mode=self.pixel_mode, frame_size=self._frame_size())
        if sheet.isNull() or not save_spritesheet(sheet, path, pixel_mode=self.pixel_mode):
            QMessageBox.warning(self, "导出失败", "无法保存横向序列帧图。")
            return
        self.result_path = str(path)
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
        self._append_log(f"已切出并对齐 {len(self.source_frames)} 帧。")

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

    def _append_log(self, message: str) -> None:
        text = str(message or "").strip()
        if not text:
            return
        if not self.log_edit.toPlainText().strip():
            self.log_edit.setPlainText(text)
        else:
            self.log_edit.appendPlainText(f"\n{text}")
        self.log_edit.verticalScrollBar().setValue(self.log_edit.verticalScrollBar().maximum())

    def _reference_image_path(self) -> Path | None:
        if self.base_image_path and self.base_image_path.exists():
            return self.base_image_path
        if self.base_image.isNull():
            return None
        temp_dir = Path(tempfile.gettempdir()) / "gamedesigner_sequence_frames"
        temp_dir.mkdir(parents=True, exist_ok=True)
        path = temp_dir / "sequence_frame_reference.png"
        if self.base_image.save(str(path), "PNG"):
            return path
        return None

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
