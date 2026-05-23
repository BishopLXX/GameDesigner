from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from PySide6.QtCore import QEvent, QPointF, QRect, QRectF, Qt, Signal
from PySide6.QtGui import QColor, QImage, QPainter, QPen
from PySide6.QtWidgets import (
    QCheckBox,
    QColorDialog,
    QComboBox,
    QDialog,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QMessageBox,
    QPushButton,
    QSlider,
    QSpinBox,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from ..window_layouts import restore_window_layout, save_window_layout


BRUSH_PRESETS = {
    "铅笔": {"size": 4, "opacity": 82, "pressure": True},
    "墨线": {"size": 9, "opacity": 100, "pressure": True},
    "马克": {"size": 24, "opacity": 46, "pressure": False},
    "平刷": {"size": 42, "opacity": 36, "pressure": False},
    "细节": {"size": 2, "opacity": 100, "pressure": True},
}


@dataclass
class PaintLayer:
    name: str
    image: QImage
    visible: bool = True
    opacity: float = 1.0

    def copy(self) -> "PaintLayer":
        return PaintLayer(self.name, self.image.copy(), self.visible, self.opacity)


class PaintCanvas(QWidget):
    changed = Signal()
    layersChanged = Signal()

    def __init__(self, image: QImage | None = None, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        base_image = self._convert_image(image) if image is not None and not image.isNull() else self._blank_image()
        self.layers: list[PaintLayer] = [PaintLayer("图层 1", base_image)]
        self.active_layer_index = 0
        self.brush_color = QColor("#1D1D1F")
        self.brush_size = 8
        self.brush_opacity = 100
        self.pressure_enabled = True
        self.tool = "brush"
        self.selection_rect: QRectF | None = None
        self.floating_selection: QImage | None = None
        self.floating_rect: QRectF | None = None
        self._last_image_pos: QPointF | None = None
        self._last_pressure = 1.0
        self._select_start: QPointF | None = None
        self._drag_start: QPointF | None = None
        self._drag_origin: QRectF | None = None
        self._transform_mode: str | None = None
        self._history: list[tuple[list[PaintLayer], int]] = []
        self._redo: list[tuple[list[PaintLayer], int]] = []
        self.setMinimumSize(720, 520)
        self.setMouseTracking(True)

    @property
    def image(self) -> QImage:
        return self.export_image(transparent_background=True)

    @image.setter
    def image(self, image: QImage) -> None:
        self.layers = [PaintLayer("图层 1", self._convert_image(image) if not image.isNull() else self._blank_image())]
        self.active_layer_index = 0
        self.clear_selection(push_history=False)
        self.layersChanged.emit()
        self.update()

    def open_image(self, path: str | Path) -> bool:
        image = QImage(str(path))
        if image.isNull():
            return False
        self._push_history()
        self.layers = [PaintLayer(Path(path).stem or "图层 1", self._convert_image(image))]
        self.active_layer_index = 0
        self.clear_selection(push_history=False)
        self._redo.clear()
        self.layersChanged.emit()
        self.update()
        self.changed.emit()
        return True

    def set_tool(self, tool: str) -> None:
        if tool not in {"brush", "eraser", "select"}:
            tool = "brush"
        if self.tool == "select" and tool != "select":
            self.commit_selection()
        self.tool = tool
        self.update()

    def set_brush_size(self, size: int) -> None:
        self.brush_size = max(1, int(size))

    def set_brush_opacity(self, opacity: int) -> None:
        self.brush_opacity = max(1, min(100, int(opacity)))

    def set_pressure_enabled(self, enabled: bool) -> None:
        self.pressure_enabled = bool(enabled)

    def set_brush_color(self, color: QColor) -> None:
        if color.isValid():
            self.brush_color = color

    def apply_brush_preset(self, name: str) -> None:
        preset = BRUSH_PRESETS.get(name)
        if not preset:
            return
        self.set_brush_size(int(preset["size"]))
        self.set_brush_opacity(int(preset["opacity"]))
        self.set_pressure_enabled(bool(preset["pressure"]))

    def active_layer(self) -> PaintLayer:
        if not self.layers:
            self.layers = [PaintLayer("图层 1", self._blank_image())]
            self.active_layer_index = 0
        self.active_layer_index = max(0, min(self.active_layer_index, len(self.layers) - 1))
        return self.layers[self.active_layer_index]

    def set_active_layer(self, index: int) -> None:
        if not self.layers:
            return
        self.commit_selection()
        self.active_layer_index = max(0, min(index, len(self.layers) - 1))
        self.layersChanged.emit()
        self.update()

    def add_layer(self) -> None:
        self.commit_selection()
        self._push_history()
        index = self.active_layer_index + 1
        self.layers.insert(index, PaintLayer(f"图层 {len(self.layers) + 1}", self._blank_layer_image()))
        self.active_layer_index = index
        self._redo.clear()
        self.layersChanged.emit()
        self.update()
        self.changed.emit()

    def duplicate_layer(self) -> None:
        if not self.layers:
            return
        self.commit_selection()
        self._push_history()
        copy_layer = self.active_layer().copy()
        copy_layer.name = f"{copy_layer.name} 副本"
        self.layers.insert(self.active_layer_index + 1, copy_layer)
        self.active_layer_index += 1
        self._redo.clear()
        self.layersChanged.emit()
        self.update()
        self.changed.emit()

    def delete_layer(self) -> None:
        if len(self.layers) <= 1:
            return
        self.commit_selection()
        self._push_history()
        del self.layers[self.active_layer_index]
        self.active_layer_index = max(0, min(self.active_layer_index, len(self.layers) - 1))
        self._redo.clear()
        self.layersChanged.emit()
        self.update()
        self.changed.emit()

    def move_layer(self, offset: int) -> None:
        target = self.active_layer_index + offset
        if target < 0 or target >= len(self.layers):
            return
        self.commit_selection()
        self._push_history()
        layer = self.layers.pop(self.active_layer_index)
        self.layers.insert(target, layer)
        self.active_layer_index = target
        self._redo.clear()
        self.layersChanged.emit()
        self.update()
        self.changed.emit()

    def toggle_active_layer_visible(self) -> None:
        self._push_history()
        layer = self.active_layer()
        layer.visible = not layer.visible
        self._redo.clear()
        self.layersChanged.emit()
        self.update()
        self.changed.emit()

    def set_active_layer_opacity(self, value: int) -> None:
        layer = self.active_layer()
        opacity = max(0.0, min(1.0, value / 100.0))
        if abs(layer.opacity - opacity) < 0.001:
            return
        layer.opacity = opacity
        self.layersChanged.emit()
        self.update()
        self.changed.emit()

    def clear(self) -> None:
        self.clear_active_layer()

    def clear_active_layer(self) -> None:
        self.commit_selection()
        self._push_history()
        self.active_layer().image.fill(Qt.transparent)
        self._redo.clear()
        self.update()
        self.changed.emit()

    def clear_selection(self, push_history: bool = True) -> None:
        if self.floating_selection is not None:
            self.commit_selection()
        self.selection_rect = None
        self.floating_selection = None
        self.floating_rect = None
        self._select_start = None
        self._drag_start = None
        self._drag_origin = None
        self._transform_mode = None
        self.update()
        if push_history:
            self.changed.emit()

    def commit_selection(self) -> None:
        if self.floating_selection is not None and self.floating_rect is not None:
            painter = QPainter(self.active_layer().image)
            painter.setRenderHint(QPainter.SmoothPixmapTransform, True)
            painter.drawImage(self.floating_rect, self.floating_selection)
            painter.end()
            self.floating_selection = None
            self.floating_rect = None
            self.selection_rect = None
            self.update()
            self.changed.emit()

    def undo(self) -> None:
        if not self._history:
            return
        self._redo.append(self._snapshot())
        self._restore_snapshot(self._history.pop())
        self.layersChanged.emit()
        self.update()
        self.changed.emit()

    def redo(self) -> None:
        if not self._redo:
            return
        self._history.append(self._snapshot())
        self._restore_snapshot(self._redo.pop())
        self.layersChanged.emit()
        self.update()
        self.changed.emit()

    def export_image(self, transparent_background: bool = True) -> QImage:
        self.commit_selection()
        output = self._blank_layer_image()
        if not transparent_background:
            output.fill(Qt.white)
        painter = QPainter(output)
        painter.setRenderHint(QPainter.SmoothPixmapTransform, True)
        for layer in self.layers:
            if not layer.visible:
                continue
            painter.setOpacity(max(0.0, min(1.0, layer.opacity)))
            painter.drawImage(QPointF(0, 0), layer.image)
        painter.end()
        return output

    def paintEvent(self, _event) -> None:  # type: ignore[override]
        painter = QPainter(self)
        painter.fillRect(self.rect(), QColor("#202026"))
        target = self._image_target_rect()
        self._paint_checkerboard(painter, target)
        painter.setRenderHint(QPainter.SmoothPixmapTransform, True)
        painter.drawImage(target, self._composited_preview())
        if self.floating_selection is not None and self.floating_rect is not None:
            painter.drawImage(self._image_rect_to_widget(self.floating_rect), self.floating_selection)
        painter.setPen(QPen(QColor("#5F6B7A"), 1))
        painter.drawRect(target)
        self._paint_selection(painter)

    def mousePressEvent(self, event) -> None:  # type: ignore[override]
        if event.button() != Qt.LeftButton:
            super().mousePressEvent(event)
            return
        image_pos = self._widget_to_image(event.position())
        if image_pos is None:
            if self.tool == "select":
                self.commit_selection()
                self.clear_selection(push_history=False)
            return
        if self.tool == "select":
            self._begin_selection_interaction(image_pos)
            event.accept()
            return
        self.commit_selection()
        self._push_history()
        self._redo.clear()
        self._last_image_pos = image_pos
        self._last_pressure = self._event_pressure(event)
        self._draw_line(image_pos, image_pos, self._last_pressure)
        event.accept()

    def mouseMoveEvent(self, event) -> None:  # type: ignore[override]
        image_pos = self._widget_to_image(event.position())
        if self.tool == "select" and self._transform_mode:
            if image_pos is not None:
                self._update_selection_interaction(image_pos)
            event.accept()
            return
        if not (event.buttons() & Qt.LeftButton) or self._last_image_pos is None:
            super().mouseMoveEvent(event)
            return
        if image_pos is None:
            return
        pressure = self._event_pressure(event)
        self._draw_line(self._last_image_pos, image_pos, (pressure + self._last_pressure) / 2.0)
        self._last_image_pos = image_pos
        self._last_pressure = pressure
        event.accept()

    def mouseReleaseEvent(self, event) -> None:  # type: ignore[override]
        if event.button() == Qt.LeftButton:
            if self.tool == "select" and self._transform_mode:
                self._finish_selection_interaction()
            self._last_image_pos = None
            self.changed.emit()
            event.accept()
            return
        super().mouseReleaseEvent(event)

    def tabletEvent(self, event) -> None:  # type: ignore[override]
        pos = QPointF(event.position()) if hasattr(event, "position") else QPointF(event.posF())
        image_pos = self._widget_to_image(pos)
        if image_pos is None:
            return
        if self.tool == "select":
            event.accept()
            return
        pressure = self._event_pressure(event)
        event_type = event.type()
        if event_type == QEvent.Type.TabletPress:
            self.commit_selection()
            self._push_history()
            self._redo.clear()
            self._last_image_pos = image_pos
            self._last_pressure = pressure
            self._draw_line(image_pos, image_pos, pressure)
        elif event_type == QEvent.Type.TabletMove and self._last_image_pos is not None:
            self._draw_line(self._last_image_pos, image_pos, (pressure + self._last_pressure) / 2.0)
            self._last_image_pos = image_pos
            self._last_pressure = pressure
        elif event_type == QEvent.Type.TabletRelease:
            self._last_image_pos = None
            self.changed.emit()
        event.accept()

    def keyPressEvent(self, event) -> None:  # type: ignore[override]
        if event.key() in {Qt.Key_Delete, Qt.Key_Backspace} and self.tool == "select":
            self._delete_selection_pixels()
            event.accept()
            return
        super().keyPressEvent(event)

    def _begin_selection_interaction(self, image_pos: QPointF) -> None:
        handle = self._selection_handle_at(image_pos)
        if handle:
            self._lift_selection()
            self._transform_mode = handle
            self._drag_start = image_pos
            self._drag_origin = QRectF(self.floating_rect or self.selection_rect or QRectF())
            return
        rect = self.floating_rect or self.selection_rect
        if rect is not None and rect.contains(image_pos):
            self._lift_selection()
            self._transform_mode = "move"
            self._drag_start = image_pos
            self._drag_origin = QRectF(self.floating_rect or QRectF())
            return
        self.commit_selection()
        self.selection_rect = None
        self.floating_selection = None
        self.floating_rect = None
        self._select_start = image_pos
        self._transform_mode = "select"
        self.update()

    def _update_selection_interaction(self, image_pos: QPointF) -> None:
        if self._transform_mode == "select" and self._select_start is not None:
            self.selection_rect = self._normalized_rect(self._select_start, image_pos)
            self.update()
            return
        if self._drag_start is None or self._drag_origin is None:
            return
        if self._transform_mode == "move":
            dx = image_pos.x() - self._drag_start.x()
            dy = image_pos.y() - self._drag_start.y()
            rect = QRectF(self._drag_origin)
            rect.translate(dx, dy)
            self.floating_rect = self._bounded_rect(rect)
            self.selection_rect = QRectF(self.floating_rect)
            self.update()
            return
        rect = QRectF(self._drag_origin)
        if self._transform_mode == "resize-br":
            rect.setBottomRight(image_pos)
        elif self._transform_mode == "resize-tr":
            rect.setTopRight(image_pos)
        elif self._transform_mode == "resize-bl":
            rect.setBottomLeft(image_pos)
        elif self._transform_mode == "resize-tl":
            rect.setTopLeft(image_pos)
        rect = self._bounded_rect(rect.normalized())
        if rect.width() >= 2 and rect.height() >= 2:
            self.floating_rect = rect
            self.selection_rect = QRectF(rect)
            self.update()

    def _finish_selection_interaction(self) -> None:
        if self._transform_mode == "select" and self.selection_rect is not None:
            if self.selection_rect.width() < 2 or self.selection_rect.height() < 2:
                self.selection_rect = None
        self._select_start = None
        self._drag_start = None
        self._drag_origin = None
        self._transform_mode = None
        self.update()

    def _lift_selection(self) -> None:
        if self.floating_selection is not None or self.selection_rect is None:
            return
        rect = self._pixel_rect(self.selection_rect)
        if rect.isNull() or rect.width() <= 0 or rect.height() <= 0:
            return
        self._push_history()
        self._redo.clear()
        self.floating_selection = self.active_layer().image.copy(rect)
        self.floating_rect = QRectF(rect)
        painter = QPainter(self.active_layer().image)
        painter.setCompositionMode(QPainter.CompositionMode_Clear)
        painter.fillRect(rect, Qt.transparent)
        painter.end()
        self.update()

    def _delete_selection_pixels(self) -> None:
        if self.floating_selection is not None:
            self.floating_selection = None
            self.floating_rect = None
            self.selection_rect = None
            self.update()
            self.changed.emit()
            return
        if self.selection_rect is None:
            return
        self._push_history()
        self._redo.clear()
        painter = QPainter(self.active_layer().image)
        painter.setCompositionMode(QPainter.CompositionMode_Clear)
        painter.fillRect(self._pixel_rect(self.selection_rect), Qt.transparent)
        painter.end()
        self.clear_selection(push_history=False)
        self.changed.emit()

    def _selection_handle_at(self, pos: QPointF) -> str | None:
        rect = self.floating_rect or self.selection_rect
        if rect is None:
            return None
        threshold = max(6.0, min(rect.width(), rect.height()) * 0.08)
        handles = {
            "resize-tl": rect.topLeft(),
            "resize-tr": rect.topRight(),
            "resize-bl": rect.bottomLeft(),
            "resize-br": rect.bottomRight(),
        }
        for name, point in handles.items():
            if abs(pos.x() - point.x()) <= threshold and abs(pos.y() - point.y()) <= threshold:
                return name
        return None

    def _draw_line(self, start: QPointF, end: QPointF, pressure: float = 1.0) -> None:
        painter = QPainter(self.active_layer().image)
        painter.setRenderHint(QPainter.Antialiasing, True)
        width = self._stroke_width(pressure)
        if self.tool == "eraser":
            painter.setCompositionMode(QPainter.CompositionMode_Clear)
            color = QColor(0, 0, 0, 0)
        else:
            painter.setCompositionMode(QPainter.CompositionMode_SourceOver)
            color = QColor(self.brush_color)
            color.setAlpha(max(1, min(255, round(255 * (self.brush_opacity / 100.0)))))
        pen = QPen(color, width, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin)
        painter.setPen(pen)
        if abs(start.x() - end.x()) < 0.01 and abs(start.y() - end.y()) < 0.01:
            painter.drawPoint(start)
        else:
            painter.drawLine(start, end)
        painter.end()
        self.update()

    def _stroke_width(self, pressure: float) -> float:
        if not self.pressure_enabled:
            pressure = 1.0
        pressure = max(0.05, min(1.0, pressure))
        return max(1.0, self.brush_size * (0.22 + 0.78 * pressure))

    def _event_pressure(self, event) -> float:
        if not self.pressure_enabled or not hasattr(event, "pressure"):
            return 1.0
        try:
            return float(event.pressure())
        except (TypeError, ValueError):
            return 1.0

    def _image_target_rect(self) -> QRectF:
        margin = 18.0
        available = QRectF(margin, margin, max(1.0, self.width() - margin * 2), max(1.0, self.height() - margin * 2))
        scale = min(available.width() / self.layers[0].image.width(), available.height() / self.layers[0].image.height())
        width = self.layers[0].image.width() * scale
        height = self.layers[0].image.height() * scale
        return QRectF(
            available.x() + (available.width() - width) / 2,
            available.y() + (available.height() - height) / 2,
            width,
            height,
        )

    def _widget_to_image(self, pos: QPointF) -> QPointF | None:
        target = self._image_target_rect()
        if not target.contains(pos):
            return None
        x = (pos.x() - target.x()) / target.width() * self.layers[0].image.width()
        y = (pos.y() - target.y()) / target.height() * self.layers[0].image.height()
        return QPointF(max(0.0, min(self.layers[0].image.width() - 1.0, x)), max(0.0, min(self.layers[0].image.height() - 1.0, y)))

    def _image_rect_to_widget(self, rect: QRectF) -> QRectF:
        target = self._image_target_rect()
        image = self.layers[0].image
        return QRectF(
            target.x() + rect.x() / image.width() * target.width(),
            target.y() + rect.y() / image.height() * target.height(),
            rect.width() / image.width() * target.width(),
            rect.height() / image.height() * target.height(),
        )

    def _normalized_rect(self, start: QPointF, end: QPointF) -> QRectF:
        return self._bounded_rect(QRectF(start, end).normalized())

    def _bounded_rect(self, rect: QRectF) -> QRectF:
        width = self.layers[0].image.width()
        height = self.layers[0].image.height()
        left = max(0.0, min(width - 1.0, rect.left()))
        top = max(0.0, min(height - 1.0, rect.top()))
        right = max(left + 1.0, min(float(width), rect.right()))
        bottom = max(top + 1.0, min(float(height), rect.bottom()))
        return QRectF(left, top, right - left, bottom - top)

    def _pixel_rect(self, rect: QRectF) -> QRect:
        bounded = self._bounded_rect(rect)
        return QRect(
            int(bounded.left()),
            int(bounded.top()),
            max(1, int(round(bounded.width()))),
            max(1, int(round(bounded.height()))),
        )

    def _paint_selection(self, painter: QPainter) -> None:
        rect = self.floating_rect or self.selection_rect
        if rect is None:
            return
        widget_rect = self._image_rect_to_widget(rect)
        pen = QPen(QColor("#0A84FF"), 1.5, Qt.DashLine)
        painter.setPen(pen)
        painter.setBrush(Qt.NoBrush)
        painter.drawRect(widget_rect)
        painter.setPen(Qt.NoPen)
        painter.setBrush(QColor("#0A84FF"))
        size = 7.0
        for point in (widget_rect.topLeft(), widget_rect.topRight(), widget_rect.bottomLeft(), widget_rect.bottomRight()):
            painter.drawRect(QRectF(point.x() - size / 2, point.y() - size / 2, size, size))

    def _paint_checkerboard(self, painter: QPainter, rect: QRectF) -> None:
        size = 16
        top = int(rect.top())
        left = int(rect.left())
        for y in range(top, int(rect.bottom()) + size, size):
            for x in range(left, int(rect.right()) + size, size):
                odd = ((x - left) // size + (y - top) // size) % 2
                painter.fillRect(x, y, size, size, QColor("#E7E7EA" if odd else "#F7F7F9"))

    def _push_history(self) -> None:
        self._history.append(self._snapshot())
        if len(self._history) > 40:
            self._history.pop(0)

    def _snapshot(self) -> tuple[list[PaintLayer], int]:
        return ([layer.copy() for layer in self.layers], self.active_layer_index)

    def _restore_snapshot(self, snapshot: tuple[list[PaintLayer], int]) -> None:
        layers, active_index = snapshot
        self.layers = [layer.copy() for layer in layers] or [PaintLayer("图层 1", self._blank_image())]
        self.active_layer_index = max(0, min(active_index, len(self.layers) - 1))
        self.clear_selection(push_history=False)

    def _composited_preview(self) -> QImage:
        output = self._blank_layer_image()
        painter = QPainter(output)
        for layer in self.layers:
            if not layer.visible:
                continue
            painter.setOpacity(max(0.0, min(1.0, layer.opacity)))
            painter.drawImage(QPointF(0, 0), layer.image)
        painter.end()
        return output

    def _blank_layer_image(self) -> QImage:
        if self.layers:
            return self._blank_image(self.layers[0].image.width(), self.layers[0].image.height())
        return self._blank_image()

    def _blank_image(self, width: int = 1024, height: int = 768) -> QImage:
        image = QImage(max(1, width), max(1, height), QImage.Format_ARGB32_Premultiplied)
        image.fill(Qt.transparent)
        return image

    def _convert_image(self, image: QImage) -> QImage:
        return image.convertToFormat(QImage.Format_ARGB32_Premultiplied)


class ImagePaintDialog(QDialog):
    def __init__(
        self,
        parent: QWidget | None = None,
        initial_path: str | Path | None = None,
        output_path: str | Path | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("绘制图片")
        self.setModal(True)
        self.result_path: str | None = None
        self.output_path = Path(output_path) if output_path else None
        image = QImage(str(initial_path)) if initial_path else QImage()
        self.canvas = PaintCanvas(image if not image.isNull() else None, self)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 14, 14, 14)
        layout.setSpacing(10)
        layout.addLayout(self._toolbar())

        body = QHBoxLayout()
        body.setSpacing(10)
        body.addWidget(self.canvas, 1)
        body.addWidget(self._layer_panel())
        layout.addLayout(body, 1)

        self.canvas.layersChanged.connect(self._refresh_layers)
        self.canvas.layersChanged.connect(self._sync_layer_opacity)
        self._refresh_layers()
        self.resize(1080, 740)
        restore_window_layout(self, "image_paint_dialog")

    def _toolbar(self) -> QHBoxLayout:
        layout = QHBoxLayout()
        layout.setSpacing(8)
        self.brush_button = self._tool_button("画笔", "brush")
        self.eraser_button = self._tool_button("橡皮", "eraser")
        self.select_button = self._tool_button("选区", "select")
        self.brush_button.setChecked(True)
        layout.addWidget(self.brush_button)
        layout.addWidget(self.eraser_button)
        layout.addWidget(self.select_button)

        self.preset_combo = QComboBox()
        self.preset_combo.addItems(list(BRUSH_PRESETS.keys()))
        self.preset_combo.currentTextChanged.connect(self._apply_preset)
        layout.addWidget(self.preset_combo)

        color_button = QPushButton("颜色")
        color_button.clicked.connect(self._pick_color)
        layout.addWidget(color_button)
        layout.addWidget(QLabel("大小"))
        self.size_spin = QSpinBox()
        self.size_spin.setRange(1, 192)
        self.size_spin.setValue(self.canvas.brush_size)
        self.size_spin.valueChanged.connect(self.canvas.set_brush_size)
        layout.addWidget(self.size_spin)
        layout.addWidget(QLabel("不透明"))
        self.opacity_spin = QSpinBox()
        self.opacity_spin.setRange(1, 100)
        self.opacity_spin.setSuffix("%")
        self.opacity_spin.setValue(self.canvas.brush_opacity)
        self.opacity_spin.valueChanged.connect(self.canvas.set_brush_opacity)
        layout.addWidget(self.opacity_spin)
        self.pressure_check = QCheckBox("压感")
        self.pressure_check.setChecked(self.canvas.pressure_enabled)
        self.pressure_check.toggled.connect(self.canvas.set_pressure_enabled)
        layout.addWidget(self.pressure_check)
        self.transparent_export_check = QCheckBox("透明背景")
        self.transparent_export_check.setChecked(True)
        layout.addWidget(self.transparent_export_check)
        layout.addStretch(1)

        open_button = QPushButton("打开图片")
        open_button.clicked.connect(self._open_image)
        undo_button = QPushButton("撤销")
        undo_button.clicked.connect(self.canvas.undo)
        redo_button = QPushButton("重做")
        redo_button.clicked.connect(self.canvas.redo)
        clear_selection_button = QPushButton("清选区")
        clear_selection_button.clicked.connect(lambda: self.canvas.clear_selection())
        clear_button = QPushButton("清空图层")
        clear_button.clicked.connect(self.canvas.clear_active_layer)
        save_button = QPushButton("保存并使用")
        save_button.setObjectName("accentButton")
        save_button.clicked.connect(self._save_and_accept)
        cancel_button = QPushButton("取消")
        cancel_button.clicked.connect(self.reject)
        for button in (open_button, undo_button, redo_button, clear_selection_button, clear_button, save_button, cancel_button):
            layout.addWidget(button)
        return layout

    def _layer_panel(self) -> QWidget:
        panel = QWidget(self)
        panel.setFixedWidth(190)
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)
        layout.addWidget(QLabel("图层"))
        self.layer_list = QListWidget(panel)
        self.layer_list.currentRowChanged.connect(self._select_layer_visual_row)
        layout.addWidget(self.layer_list, 1)

        self.layer_opacity = QSlider(Qt.Horizontal, panel)
        self.layer_opacity.setRange(0, 100)
        self.layer_opacity.setValue(100)
        self.layer_opacity.valueChanged.connect(self.canvas.set_active_layer_opacity)
        layout.addWidget(QLabel("图层不透明"))
        layout.addWidget(self.layer_opacity)

        row1 = QHBoxLayout()
        add_button = QPushButton("新增")
        duplicate_button = QPushButton("复制")
        add_button.clicked.connect(self.canvas.add_layer)
        duplicate_button.clicked.connect(self.canvas.duplicate_layer)
        row1.addWidget(add_button)
        row1.addWidget(duplicate_button)
        layout.addLayout(row1)

        row2 = QHBoxLayout()
        delete_button = QPushButton("删除")
        visible_button = QPushButton("显隐")
        delete_button.clicked.connect(self.canvas.delete_layer)
        visible_button.clicked.connect(self.canvas.toggle_active_layer_visible)
        row2.addWidget(delete_button)
        row2.addWidget(visible_button)
        layout.addLayout(row2)

        row3 = QHBoxLayout()
        up_button = QPushButton("上移")
        down_button = QPushButton("下移")
        up_button.clicked.connect(lambda: self.canvas.move_layer(1))
        down_button.clicked.connect(lambda: self.canvas.move_layer(-1))
        row3.addWidget(up_button)
        row3.addWidget(down_button)
        layout.addLayout(row3)
        return panel

    def _tool_button(self, text: str, tool: str) -> QToolButton:
        button = QToolButton(self)
        button.setObjectName("compactToolButton")
        button.setText(text)
        button.setCheckable(True)
        button.clicked.connect(lambda _checked=False, tool=tool: self._select_tool(tool))
        return button

    def _select_tool(self, tool: str) -> None:
        self.canvas.set_tool(tool)
        for button, expected in (
            (self.brush_button, "brush"),
            (self.eraser_button, "eraser"),
            (self.select_button, "select"),
        ):
            button.blockSignals(True)
            button.setChecked(tool == expected)
            button.blockSignals(False)

    def _apply_preset(self, name: str) -> None:
        self.canvas.apply_brush_preset(name)
        self.size_spin.blockSignals(True)
        self.opacity_spin.blockSignals(True)
        self.pressure_check.blockSignals(True)
        self.size_spin.setValue(self.canvas.brush_size)
        self.opacity_spin.setValue(self.canvas.brush_opacity)
        self.pressure_check.setChecked(self.canvas.pressure_enabled)
        self.size_spin.blockSignals(False)
        self.opacity_spin.blockSignals(False)
        self.pressure_check.blockSignals(False)

    def _refresh_layers(self) -> None:
        self.layer_list.blockSignals(True)
        self.layer_list.clear()
        for index in range(len(self.canvas.layers) - 1, -1, -1):
            layer = self.canvas.layers[index]
            marker = "●" if layer.visible else "○"
            active = "  ✓" if index == self.canvas.active_layer_index else ""
            self.layer_list.addItem(f"{marker} {layer.name}{active}")
        visual_row = len(self.canvas.layers) - 1 - self.canvas.active_layer_index
        self.layer_list.setCurrentRow(max(0, visual_row))
        self.layer_list.blockSignals(False)

    def _select_layer_visual_row(self, row: int) -> None:
        if row < 0:
            return
        self.canvas.set_active_layer(len(self.canvas.layers) - 1 - row)

    def _sync_layer_opacity(self) -> None:
        opacity = round(self.canvas.active_layer().opacity * 100)
        self.layer_opacity.blockSignals(True)
        self.layer_opacity.setValue(opacity)
        self.layer_opacity.blockSignals(False)

    def _pick_color(self) -> None:
        color = QColorDialog.getColor(self.canvas.brush_color, self, "选择画笔颜色")
        if color.isValid():
            self.canvas.set_brush_color(color)

    def _open_image(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self,
            "打开图片继续绘制",
            str(Path.home()),
            "图片 (*.png *.jpg *.jpeg *.bmp *.gif);;所有文件 (*.*)",
        )
        if not path:
            return
        if not self.canvas.open_image(path):
            QMessageBox.warning(self, "无法打开图片", "图片读取失败。")

    def _save_and_accept(self) -> None:
        path = self.output_path
        if path is None:
            selected, _ = QFileDialog.getSaveFileName(
                self,
                "保存绘制图片",
                str(Path.home() / "绘制图片.png"),
                "PNG 图片 (*.png)",
            )
            if not selected:
                return
            path = Path(selected)
        path.parent.mkdir(parents=True, exist_ok=True)
        export = self.canvas.export_image(transparent_background=self.transparent_export_check.isChecked())
        if not export.save(str(path), "PNG"):
            QMessageBox.warning(self, "保存失败", "无法保存绘制图片。")
            return
        self.result_path = str(path)
        self.accept()

    def done(self, result: int) -> None:  # type: ignore[override]
        save_window_layout(self, "image_paint_dialog")
        super().done(result)
