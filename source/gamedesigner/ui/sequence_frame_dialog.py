from __future__ import annotations

import tempfile
from pathlib import Path

from PySide6.QtCore import QSize, Qt, QTimer
from PySide6.QtGui import QColor, QIcon, QImage, QImageWriter, QPainter, QPixmap
from PySide6.QtWidgets import (
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QListView,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
    QWidget,
    QDialog,
)

from .image_paint_dialog import ImagePaintDialog
from ..window_layouts import restore_window_layout, save_window_layout


PIXEL_ART_METADATA_KEY = "GameDesignerPixelArt"


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


class AnimationPreviewWidget(QWidget):
    def __init__(self, *, pixel_mode: bool = False, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.pixel_mode = bool(pixel_mode)
        self.frames: list[QImage] = []
        self.current_index = 0
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._advance)
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


class SequenceFrameDialog(QDialog):
    def __init__(
        self,
        parent: QWidget | None = None,
        *,
        pixel_mode: bool = False,
        initial_path: str | Path | None = None,
        output_path: str | Path | None = None,
    ) -> None:
        super().__init__(parent)
        self.pixel_mode = bool(pixel_mode)
        self.output_path = Path(output_path) if output_path else None
        self.result_path: str | None = None
        self.source_frames: list[QImage] = []
        self.frames: list[QImage] = []
        self.base_image = QImage()
        self._last_dir = Path(initial_path).parent if initial_path else Path.home()

        self.setWindowTitle("像素序列帧动画" if self.pixel_mode else "序列帧动画")
        self.setModal(True)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 14, 14, 14)
        layout.setSpacing(10)
        layout.addLayout(self._toolbar())

        self.preview = AnimationPreviewWidget(pixel_mode=self.pixel_mode, parent=self)
        self.fps_spin.valueChanged.connect(self.preview.set_fps)
        layout.addWidget(self.preview, 1)

        self.frame_list = QListWidget(self)
        self.frame_list.setViewMode(QListView.IconMode)
        self.frame_list.setFlow(QListView.LeftToRight)
        self.frame_list.setMovement(QListView.Static)
        self.frame_list.setWrapping(False)
        self.frame_list.setIconSize(QSize(82, 82))
        self.frame_list.setGridSize(QSize(102, 112))
        self.frame_list.setFixedHeight(124)
        self.frame_list.currentRowChanged.connect(self._select_frame)
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
        self._last_dir = Path(path).parent
        self.base_image = image.convertToFormat(QImage.Format_ARGB32_Premultiplied)
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

        seed_button = QPushButton("开始制作", self)
        seed_button.clicked.connect(self._seed_frames)
        layout.addWidget(seed_button)

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

    def done(self, result: int) -> None:  # type: ignore[override]
        self.preview.set_playing(False, self.fps_spin.value())
        save_window_layout(self, "pixel_sequence_frame_dialog" if self.pixel_mode else "sequence_frame_dialog")
        super().done(result)
