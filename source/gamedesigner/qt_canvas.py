from __future__ import annotations

import json
import math
from collections import OrderedDict
from dataclasses import dataclass

from PySide6.QtCore import QEvent, QPoint, QPointF, QRect, QRectF, Qt, QTimer, Signal
from PySide6.QtGui import (
    QAction,
    QBrush,
    QColor,
    QCursor,
    QFont,
    QFontMetrics,
    QPainter,
    QPainterPath,
    QPainterPathStroker,
    QPen,
    QPixmap,
    QPolygonF,
)
from PySide6.QtWidgets import (
    QApplication,
    QGraphicsItem,
    QGraphicsObject,
    QGraphicsPathItem,
    QGraphicsProxyWidget,
    QGraphicsScene,
    QGraphicsView,
    QInputDialog,
    QMenu,
    QPlainTextEdit,
)

from .data_canvas import (
    DATA_CANVAS_MARGIN_X,
    DATA_CANVAS_MARGIN_Y,
    DATA_CANVAS_THUMBNAIL_HEADER_HEIGHT,
    DATA_CANVAS_THUMBNAIL_ROW_HEIGHT,
    apply_template_to_node,
    layout_data_canvas,
    reorder_data_canvas_node,
)
from .image_rendering import draw_field_pixmap
from .models import (
    NOTE_DEFAULT_HEIGHT,
    NOTE_DEFAULT_WIDTH,
    NOTE_MIN_HEIGHT,
    NOTE_MIN_WIDTH,
    BlueprintGroup,
    CanvasData,
    DesignNote,
    Edge,
    Node,
    NodeField,
    NodeTemplate,
    ProjectData,
    new_id,
)
from .node_visuals import (
    VISUAL_NODE_HEADER_HEIGHT,
    VISUAL_NODE_MIN_HEIGHT,
    VISUAL_NODE_MIN_WIDTH,
    visual_node_size,
)
from .qt_theme import palette


NODE_DEFAULT_WIDTH = 310.0
NODE_MIN_WIDTH = 260.0
NODE_MIN_HEIGHT = 92.0
NODE_MAX_NATURAL_WIDTH = 680.0
HEADER_HEIGHT = VISUAL_NODE_HEADER_HEIGHT
ROW_GAP = 7.0
ROW_TOP = HEADER_HEIGHT + 6.0
RESIZE_HANDLE = 20.0
CONNECTION_HANDLE_RADIUS = 4.5
CONNECTION_HANDLE_HIT_RADIUS = 11.0
EDGE_BEND_HANDLE_RADIUS = 5.5
EDGE_BEND_HANDLE_HIT_RADIUS = 12.0
ORTHOGONAL_STUB_LENGTH = 58.0
ORTHOGONAL_ROUTE_MERGE_DISTANCE = 18.0
ORTHOGONAL_ENDPOINT_AXIS_SNAP_DISTANCE = 48.0
ORTHOGONAL_SEGMENT_MERGE_DISTANCE = 14.0
GROUP_MIN_WIDTH = 220.0
GROUP_MIN_HEIGHT = 120.0
GROUP_HEADER_HEIGHT = 34.0
SNAP_UNIT = 20.0
GRID_SNAP_THRESHOLD = 6.0
ALIGN_SNAP_THRESHOLD = 10.0
ALIGN_PROXIMITY = 460.0
SCENE_EXTENT = 500000.0
SCENE_MARGIN = 20000.0
WRAP_FLAGS = Qt.AlignLeft | Qt.AlignTop | Qt.TextWordWrap | Qt.TextWrapAnywhere
RIGHT_DRAG_MENU_THRESHOLD = 8
CONNECTION_AUTO_PAN_MARGIN = 46
CONNECTION_AUTO_PAN_MIN_STEP = 5
CONNECTION_AUTO_PAN_MAX_STEP = 34
CONNECTION_AUTO_PAN_INTERVAL_MS = 16
EDGE_LABEL_OFFSET = 16.0
EDGE_LABEL_HEIGHT = 22.0
EDGE_LABEL_MIN_WIDTH = 38.0
EDGE_LABEL_MAX_WIDTH = 180.0
EDGE_LABEL_PADDING_X = 10.0
IMAGE_SOURCE_CACHE_LIMIT = 96
IMAGE_SCALED_CACHE_LIMIT = 192
INTERACTION_PREVIEW_DELAY_MS = 140
_MISSING_PIXMAP = object()
NOTE_MIME_TYPE = "application/x-gamedesigner-design-note"


class InlineNodeFieldEditor(QPlainTextEdit):
    editingFinished = Signal(bool)

    def __init__(self, single_line: bool = False) -> None:
        super().__init__()
        self.single_line = single_line
        if single_line:
            self.setLineWrapMode(QPlainTextEdit.NoWrap)

    def keyPressEvent(self, event) -> None:  # type: ignore[override]
        if event.key() in (Qt.Key_Return, Qt.Key_Enter):
            if not self.single_line and event.modifiers() & Qt.ShiftModifier:
                super().keyPressEvent(event)
                return
            self.editingFinished.emit(True)
            event.accept()
            return
        if event.key() == Qt.Key_Escape:
            self.editingFinished.emit(False)
            event.accept()
            return
        super().keyPressEvent(event)

    def focusOutEvent(self, event) -> None:  # type: ignore[override]
        self.editingFinished.emit(True)
        super().focusOutEvent(event)


def _safe_color(value: str, fallback: str) -> QColor:
    color = QColor(value)
    return color if color.isValid() else QColor(fallback)


def _font(size: int, bold: bool = False) -> QFont:
    font = QFont()
    font.setPointSize(size)
    font.setBold(bold)
    return font


def _field_text_flags(field: NodeField) -> Qt.AlignmentFlag:
    h_flags = {
        "left": Qt.AlignLeft,
        "center": Qt.AlignHCenter,
        "right": Qt.AlignRight,
    }
    v_flags = {
        "top": Qt.AlignTop,
        "center": Qt.AlignVCenter,
        "bottom": Qt.AlignBottom,
    }
    return (
        Qt.TextWordWrap
        | Qt.TextWrapAnywhere
        | h_flags.get(field.text_h_align, Qt.AlignLeft)
        | v_flags.get(field.text_v_align, Qt.AlignTop)
    )


@dataclass
class SnapGuide:
    axis: str
    value: float
    label: str
    kind: str


class BlueprintGroupItem(QGraphicsObject):
    def __init__(self, group: BlueprintGroup, view: "NodeGraphView") -> None:
        super().__init__()
        self.group = group
        self.view = view
        self._resizing = False
        self._resize_origin = QPointF()
        self._resize_size = (0.0, 0.0)
        self._last_pos = QPointF(group.x, group.y)
        self.setPos(group.x, group.y)
        flags = QGraphicsItem.ItemIsSelectable
        if self.view.can_move_groups():
            flags |= QGraphicsItem.ItemIsMovable | QGraphicsItem.ItemSendsGeometryChanges
        self.setFlags(flags)
        self.setAcceptHoverEvents(True)
        self.setZValue(-1)

    def boundingRect(self) -> QRectF:
        return QRectF(0, 0, max(GROUP_MIN_WIDTH, self.group.width), max(GROUP_MIN_HEIGHT, self.group.height))

    def shape(self) -> QPainterPath:
        rect = self.boundingRect()
        path = QPainterPath()
        path.addRect(QRectF(0, 0, rect.width(), GROUP_HEADER_HEIGHT + 4))
        path.addRect(QRectF(rect.right() - RESIZE_HANDLE, rect.bottom() - RESIZE_HANDLE, RESIZE_HANDLE, RESIZE_HANDLE))
        return path

    def paint(self, painter: QPainter, _option, _widget=None) -> None:  # type: ignore[override]
        colors = palette(self.view.theme)
        painter.setRenderHint(QPainter.Antialiasing, True)
        rect = self.boundingRect()
        base = self._display_color()
        body = QColor(base)
        body.setAlpha(92 if self.view.theme == "dark" else 48)
        header = QColor(base.lighter(118 if self.view.theme == "dark" else 104))
        header.setAlpha(236)
        outline = QColor(colors["blue"] if self.isSelected() else base.darker(122).name())
        outline.setAlpha(220 if self.isSelected() else 150)

        path = QPainterPath()
        path.addRoundedRect(rect.adjusted(0.5, 0.5, -0.5, -0.5), 3, 3)
        painter.fillPath(path, body)
        painter.setPen(QPen(outline, 2.0 if self.isSelected() else 1.2))
        painter.drawPath(path)
        painter.fillRect(QRectF(0, 0, rect.width(), GROUP_HEADER_HEIGHT), header)
        painter.setPen(QColor("#FFFFFF"))
        painter.setFont(_font(12, True))
        painter.drawText(QRectF(12, 4, rect.width() - 24, GROUP_HEADER_HEIGHT - 7), Qt.AlignLeft | Qt.AlignVCenter, self.group.title)
        painter.setPen(QPen(QColor("#B7B7BD"), 1))
        for offset in (6, 10, 14):
            painter.drawLine(
                QPointF(rect.right() - offset, rect.bottom() - 3),
                QPointF(rect.right() - 3, rect.bottom() - offset),
            )

    def mouseDoubleClickEvent(self, event) -> None:  # type: ignore[override]
        if event.button() == Qt.LeftButton:
            self.view.groupEditRequested.emit(self.group.id)
            event.accept()
            return
        super().mouseDoubleClickEvent(event)

    def hoverMoveEvent(self, event) -> None:  # type: ignore[override]
        self.setCursor(Qt.SizeFDiagCursor if self._on_resize_handle(event.pos()) else Qt.OpenHandCursor)
        super().hoverMoveEvent(event)

    def mousePressEvent(self, event) -> None:  # type: ignore[override]
        if event.button() == Qt.LeftButton:
            if not self.isSelected():
                self.view.select_group(self.group.id)
            self._last_pos = self.pos()
            if not self.view.read_only and self._on_resize_handle(event.pos()):
                self._resizing = True
                self._resize_origin = event.scenePos()
                self._resize_size = (self.group.width, self.group.height)
                event.accept()
                return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event) -> None:  # type: ignore[override]
        if self._resizing:
            delta = event.scenePos() - self._resize_origin
            self.prepareGeometryChange()
            self.group.width = max(GROUP_MIN_WIDTH, self._resize_size[0] + delta.x())
            self.group.height = max(GROUP_MIN_HEIGHT, self._resize_size[1] + delta.y())
            self.view.update_edges_for_endpoint(self.group.id)
            self.update()
            event.accept()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event) -> None:  # type: ignore[override]
        changed = self._resizing or self.pos() != self._last_pos
        self._resizing = False
        self.setCursor(Qt.OpenHandCursor)
        self.view.snap_guides.clear()
        self.view.viewport().update()
        if changed:
            self.view.refresh_group_membership()
            self.view.projectChanged.emit()
        super().mouseReleaseEvent(event)

    def itemChange(self, change: QGraphicsItem.GraphicsItemChange, value):  # type: ignore[override]
        if change == QGraphicsItem.ItemPositionChange and self.scene() and not self.view.rebuilding:
            pos = value
            if QApplication.keyboardModifiers() & Qt.ControlModifier:
                self.view.snap_guides.clear()
                return pos
            return self.view.snap_position(self, pos)
        if change == QGraphicsItem.ItemPositionHasChanged and not self.view.rebuilding:
            new_pos = self.pos()
            delta = new_pos - self._last_pos
            self.group.x = new_pos.x()
            self.group.y = new_pos.y()
            if not self._resizing and (abs(delta.x()) > 0.001 or abs(delta.y()) > 0.001):
                self.view.move_nodes_in_group(self.group.id, delta)
            self.view.update_edges_for_endpoint(self.group.id)
            self._last_pos = QPointF(new_pos)
            self.view._update_scene_rect()
        return super().itemChange(change, value)

    def _on_resize_handle(self, pos: QPointF) -> bool:
        rect = self.boundingRect()
        return pos.x() >= rect.right() - RESIZE_HANDLE and pos.y() >= rect.bottom() - RESIZE_HANDLE

    def _display_color(self) -> QColor:
        raw = (self.group.color or "").strip().lower()
        if raw in {"", "#3a3a3f"}:
            return QColor("#5678A6" if self.view.theme == "dark" else "#486A96")
        return _safe_color(self.group.color, "#486A96")


class NodeItem(QGraphicsObject):
    def __init__(self, node: Node, view: "NodeGraphView") -> None:
        super().__init__()
        self.node = node
        self.view = view
        self.width = NODE_DEFAULT_WIDTH
        self.height = NODE_MIN_HEIGHT
        self._resizing = False
        self._resize_origin = QPointF()
        self._resize_size = (0.0, 0.0)
        self._resize_field_snapshot: list[tuple[NodeField, float, float, float, float, int]] = []
        self._pressed_pos = QPointF()
        self._moved = False
        self._inline_candidate_field: NodeField | None = None
        self._inline_candidate_part = ""
        self._inline_candidate_rect = QRectF()
        self._sync_size()
        self.setPos(node.x, node.y)
        flags = QGraphicsItem.ItemIsSelectable
        if self.view.can_move_nodes():
            flags |= QGraphicsItem.ItemIsMovable | QGraphicsItem.ItemSendsGeometryChanges
        self.setFlags(flags)
        self.setAcceptHoverEvents(True)
        self.setZValue(10)

    def boundingRect(self) -> QRectF:
        return QRectF(0, 0, self.width, self.height)

    def paint(self, painter: QPainter, _option, _widget=None) -> None:  # type: ignore[override]
        colors = palette(self.view.theme)
        zoom = self.view.transform().m11()
        painter.setRenderHint(QPainter.Antialiasing, True)
        rect = self.boundingRect()
        if self._uses_horizontal_thumbnail_row():
            self._paint_horizontal_thumbnail_row(painter, colors, rect)
            return
        path = QPainterPath()
        path.addRoundedRect(rect.adjusted(1, 1, -1, -1), 14, 14)
        painter.fillPath(path, _safe_color(self.node.color, "#FFFFFF"))
        outline_width = 3.0 if self.isSelected() else 2.0
        painter.setPen(QPen(QColor(colors["blue"] if self.isSelected() else colors["accent"]), outline_width))
        painter.drawPath(path)

        painter.save()
        painter.setClipPath(path)
        painter.fillRect(QRectF(1, 1, rect.width() - 2, 18), QColor(colors["node_header"]))
        painter.restore()
        painter.setPen(QPen(QColor(colors["node_header_line"]), 1))
        painter.drawLine(QPointF(12, 25), QPointF(rect.width() - 12, 25))
        self._paint_order_badge(painter, colors, rect)

        if zoom < 0.36:
            self._paint_icon_mode(painter, colors, rect, zoom)
        elif zoom < 0.62:
            self._paint_compact_mode(painter, colors, rect, zoom)
        else:
            self._paint_detail_mode(painter, colors, rect)

        if self.view.can_resize_nodes() and (self.isSelected() or self.view.hover_node_id == self.node.id):
            self._paint_resize_handle(painter, colors, rect)
        if self._shows_connection_handles():
            self._paint_connection_handles(painter)

    def _paint_order_badge(self, painter: QPainter, colors: dict[str, str], rect: QRectF) -> None:
        if self.node.order <= 0:
            return
        text = str(self.node.order)
        metrics = QFontMetrics(_font(8, True))
        width = max(22.0, metrics.horizontalAdvance(text) + 12.0)
        badge = QRectF(rect.right() - width - 10, 4, width, 16)
        path = QPainterPath()
        path.addRoundedRect(badge, 8, 8)
        painter.fillPath(path, QColor(colors["panel_alt"]))
        painter.setPen(QPen(QColor(colors["hairline"]), 1))
        painter.drawPath(path)
        painter.setPen(QColor(colors["text_muted"]))
        painter.setFont(_font(8, True))
        painter.drawText(badge, Qt.AlignCenter, text)

    def _paint_icon_mode(self, painter: QPainter, colors: dict[str, str], rect: QRectF, zoom: float) -> None:
        fallback = "画" if self.node.node_type == "画布" else "链" if self.node.node_type == "超文本" else self.node.title
        text = (self.node.display_icon() or fallback or "节").strip()[:8]
        painter.setPen(QColor(colors["accent"]))
        text_rect = rect.adjusted(12, 22, -12, -8)
        self._draw_adaptive_center_text(painter, text_rect, text, zoom, 20, 84)

    def _paint_compact_mode(self, painter: QPainter, colors: dict[str, str], rect: QRectF, zoom: float) -> None:
        fallback = "画" if self.node.node_type == "画布" else "链" if self.node.node_type == "超文本" else self.node.title[:1]
        icon = (self.node.display_icon() or fallback or "节").strip()[:2]
        painter.setPen(QColor(colors["accent"]))
        icon_rect, title_rect = self._compact_header_rects(rect)
        icon_font_size = self._fit_font_size(icon, icon_rect, int(18 / max(zoom, 0.18)), 12, 52)
        font = _font(icon_font_size, True)
        painter.setFont(font)
        painter.drawText(icon_rect, Qt.AlignCenter, icon)
        painter.setPen(QColor(colors["node_text"]))
        self._draw_adaptive_center_text(painter, title_rect, self.node.title, zoom, 13, 42)

    def _paint_detail_mode(self, painter: QPainter, colors: dict[str, str], rect: QRectF) -> None:
        visual_fields = [field for field in self.node.fields if field.has_visual_layout()]
        painter.setPen(QColor(colors["node_text"]))
        title_font = _font(12, True)
        painter.setFont(title_font)
        icon = self._detail_header_icon()
        icon_rect, title_rect = self._detail_header_rects(rect, bool(visual_fields))
        if icon:
            painter.setPen(QColor(colors["accent"]))
            painter.drawText(icon_rect, Qt.AlignCenter, icon)
        painter.setPen(QColor(colors["node_text"]))
        painter.drawText(title_rect, Qt.AlignLeft | Qt.AlignVCenter, self.node.title)
        if not visual_fields:
            painter.setPen(QColor(colors["node_muted"]))
            painter.setFont(_font(8))
            meta = (
                "画布"
                if self.node.node_type == "画布"
                else self.node.link_format.upper()
                if self.node.node_type == "超文本"
                else f"{len(self.node.fields)} 项"
            )
            painter.drawText(QRectF(rect.width() - 72, 30, 54, 18), Qt.AlignRight | Qt.AlignVCenter, meta)

        if not self.node.fields:
            painter.setPen(QColor(colors["node_muted"]))
            painter.setFont(_font(10))
            painter.drawText(QRectF(18, HEADER_HEIGHT + 18, rect.width() - 36, 24), Qt.AlignLeft, "暂无数据字段")
            return

        if self._uses_horizontal_data_row():
            self._paint_horizontal_data_row(painter, colors)
            return

        if visual_fields:
            self._paint_visual_fields(painter, colors, visual_fields)
        else:
            self._paint_rows(painter, colors)

    def _paint_rows(self, painter: QPainter, colors: dict[str, str]) -> None:
        name_w, type_w = self._row_column_widths()
        y = ROW_TOP
        for field in self.node.fields:
            row_h = self._row_height(field, self.width, name_w, type_w)
            row = QRectF(10, y, self.width - 20, row_h)
            row_path = QPainterPath()
            row_path.addRoundedRect(row, 8, 8)
            painter.fillPath(row_path, QColor("#FAFAFC"))
            painter.setPen(QPen(QColor("#E5E5EA"), 1))
            painter.drawPath(row_path)
            painter.setPen(QColor(colors["accent_dark"]))
            name_font = _font(9, True)
            painter.setFont(name_font)
            painter.drawText(
                QRectF(row.x() + 10, row.y() + 8, name_w, row.height() - 16),
                WRAP_FLAGS,
                field.name,
            )
            painter.setPen(QColor(colors["node_text"]))
            painter.setFont(_font(9))
            value_x = row.x() + 10 + name_w + 14
            painter.drawText(
                QRectF(value_x, row.y() + 8, row.right() - value_x - 10, row.height() - 16),
                WRAP_FLAGS,
                field.value or " ",
            )
            y += row_h + ROW_GAP

    def _paint_horizontal_data_row(self, painter: QPainter, colors: dict[str, str]) -> None:
        segments = self._horizontal_data_segments()
        if not segments:
            return
        name_font = _font(8, True)
        value_font = _font(9)
        name_metrics = QFontMetrics(name_font)
        value_metrics = QFontMetrics(value_font)
        x = 10.0
        y = ROW_TOP
        row_h = 42.0
        for field, label_w, segment_w in segments:
            row = QRectF(x, y, segment_w, row_h)
            row_path = QPainterPath()
            row_path.addRoundedRect(row, 8, 8)
            painter.fillPath(row_path, QColor("#FAFAFC"))
            painter.setPen(QPen(QColor("#E5E5EA"), 1))
            painter.drawPath(row_path)

            label_x = row.x() + 10
            label_rect = QRectF(label_x, row.y() + 8, label_w, row.height() - 16)
            painter.setPen(QColor(colors["node_muted"]))
            painter.setFont(name_font)
            painter.drawText(
                label_rect,
                Qt.AlignLeft | Qt.AlignVCenter | Qt.TextSingleLine,
                name_metrics.elidedText(field.name, Qt.ElideRight, max(1, int(label_rect.width()))),
            )

            value_x = label_rect.right() + 8
            value_rect = QRectF(value_x, row.y() + 8, row.right() - value_x - 10, row.height() - 16)
            painter.setPen(QColor(colors["node_text"]))
            painter.setFont(value_font)
            painter.drawText(
                value_rect,
                Qt.AlignLeft | Qt.AlignVCenter | Qt.TextSingleLine,
                value_metrics.elidedText(self._horizontal_field_value(field), Qt.ElideRight, max(1, int(value_rect.width()))),
            )
            x += segment_w + ROW_GAP

    def _paint_horizontal_thumbnail_row(self, painter: QPainter, colors: dict[str, str], rect: QRectF) -> None:
        columns = self.view.horizontal_thumbnail_columns()
        if not columns:
            return
        background = QColor("#FFFFFF" if self.view.theme == "light" else colors["panel_alt"])
        alternate = QColor("#F5F5F7" if self.view.theme == "light" else colors["panel"])
        fill = alternate if int(max(1, self.node.order)) % 2 == 0 else background
        painter.fillRect(rect.adjusted(0.5, 0.5, -0.5, -0.5), fill)
        if self.isSelected():
            painter.fillRect(rect.adjusted(0.5, 0.5, -0.5, -0.5), QColor(colors["blue_soft"]))

        field_map = {field.id: field for field in self.node.fields}
        x = 0.0
        value_metrics = QFontMetrics(_font(9))
        painter.setFont(_font(9))
        for field_id, _name, _data_type, width in columns:
            cell = QRectF(x, 0, width, rect.height())
            painter.setPen(QPen(QColor(colors["hairline"]), 1))
            painter.drawRect(cell)
            field = field_map.get(field_id)
            value = self._horizontal_field_value(field) if field is not None else " "
            painter.setPen(QColor(colors["text"]))
            painter.drawText(
                cell.adjusted(8, 0, -8, 0),
                Qt.AlignLeft | Qt.AlignVCenter | Qt.TextSingleLine,
                value_metrics.elidedText(value, Qt.ElideRight, max(1, int(cell.width() - 16))),
            )
            x += width
        if self.isSelected():
            painter.setPen(QPen(QColor(colors["blue"]), 2))
            painter.drawRect(rect.adjusted(1, 1, -1, -1))

    def _paint_visual_fields(self, painter: QPainter, colors: dict[str, str], fields: list[NodeField]) -> None:
        for field in fields:
            x = field.x
            y = HEADER_HEIGHT + field.y
            w = max(24.0, field.width)
            h = max(22.0, field.height)
            card = QRectF(x, y, w, h)
            path = QPainterPath()
            path.addRoundedRect(card, 9, 9)
            painter.fillPath(path, QColor(field.bg_color or "#FFFFFF"))
            is_image = field.data_type == "图片"
            if is_image and field.image_path:
                pixmap = self.view._source_image_pixmap(field.image_path)
                if pixmap is not None:
                    painter.save()
                    painter.setClipPath(path)
                    draw_field_pixmap(
                        painter,
                        pixmap,
                        card,
                        field,
                        smooth=not self.view.is_interaction_preview(),
                    )
                    painter.restore()
            elif is_image:
                painter.setPen(QColor(colors["node_muted"]))
                painter.setFont(_font(9))
                painter.drawText(card.adjusted(9, 8, -9, -8), Qt.AlignCenter | Qt.TextWordWrap, "选择图片")
            painter.setPen(QPen(QColor("#DADAE0"), 1))
            painter.drawPath(path)
            if is_image:
                text = field.value
            else:
                text = field.value or field.name
            self._draw_visual_field_text(painter, colors, card.adjusted(10, 9, -10, -9), field, text, 1.0)

    def _draw_visual_field_text(
        self,
        painter: QPainter,
        colors: dict[str, str],
        rect: QRectF,
        field: NodeField,
        text: str,
        scale: float,
    ) -> None:
        if not text and not field.show_label:
            return
        font_size = max(8, min(48, int(field.font_size * scale)))
        text_color = _safe_color(field.text_color or colors["node_text"], colors["node_text"])
        if field.show_label and field.data_type != "图片":
            label = field.name.strip() or "字段"
            value = field.value.strip() or " "
            label_font = _font(font_size, True)
            value_font = _font(font_size)
            label_metrics = QFontMetrics(label_font)
            value_metrics = QFontMetrics(value_font)
            label_width = min(
                max(42.0, float(label_metrics.horizontalAdvance(label) + 8)),
                max(42.0, rect.width() * 0.48),
            )
            label_rect = QRectF(rect.x(), rect.y(), label_width, rect.height())
            value_rect = QRectF(label_rect.right() + 8, rect.y(), max(1.0, rect.right() - label_rect.right() - 8), rect.height())
            label_color = QColor(text_color)
            label_color.setAlpha(178)
            painter.setPen(label_color)
            painter.setFont(label_font)
            painter.drawText(
                label_rect,
                Qt.AlignLeft | Qt.AlignVCenter | Qt.TextSingleLine,
                label_metrics.elidedText(label, Qt.ElideRight, max(1, int(label_rect.width()))),
            )
            painter.setPen(text_color)
            painter.setFont(value_font)
            painter.drawText(
                value_rect,
                Qt.AlignLeft | Qt.AlignVCenter | Qt.TextSingleLine,
                value_metrics.elidedText(value, Qt.ElideRight, max(1, int(value_rect.width()))),
            )
            return
        painter.setPen(text_color)
        painter.setFont(_font(font_size))
        painter.drawText(rect, _field_text_flags(field), text)

    def _paint_resize_handle(self, painter: QPainter, colors: dict[str, str], rect: QRectF) -> None:
        painter.setPen(QPen(QColor(colors["blue"]), 1.4))
        for offset in (6, 11, 16):
            painter.drawLine(
                QPointF(rect.right() - offset, rect.bottom() - 4),
                QPointF(rect.right() - 4, rect.bottom() - offset),
            )

    def _paint_connection_handles(self, painter: QPainter) -> None:
        fill = QColor("#FF496A")
        outline = QColor("#FFFFFF" if self.view.theme == "dark" else "#1D1D1F")
        painter.setBrush(QBrush(fill))
        painter.setPen(QPen(outline, 1.2))
        radius = CONNECTION_HANDLE_RADIUS
        for point in self._connection_handle_points().values():
            painter.drawEllipse(point, radius, radius)

    def _draw_adaptive_center_text(
        self,
        painter: QPainter,
        rect: QRectF,
        text: str,
        zoom: float,
        min_size: int,
        max_size: int,
    ) -> None:
        font_size = self._fit_font_size(text, rect, int(18 / max(zoom, 0.18)), min_size, max_size)
        painter.setFont(_font(font_size, True))
        painter.drawText(rect, Qt.AlignCenter | Qt.TextWordWrap | Qt.TextWrapAnywhere, text or " ")

    def _fit_font_size(
        self,
        text: str,
        rect: QRectF,
        preferred_size: int,
        min_size: int,
        max_size: int,
    ) -> int:
        target = max(min_size, min(max_size, preferred_size))
        bounds = QRect(0, 0, max(1, int(rect.width())), max(1, int(rect.height())))
        for size in range(target, min_size - 1, -1):
            metrics = QFontMetrics(_font(size, True))
            measured = metrics.boundingRect(
                bounds,
                Qt.AlignCenter | Qt.TextWordWrap | Qt.TextWrapAnywhere,
                text or " ",
            )
            if measured.width() <= bounds.width() and measured.height() <= bounds.height():
                return size
        return min_size

    def hoverMoveEvent(self, event) -> None:  # type: ignore[override]
        self.view.hover_node_id = self.node.id
        connection_handle = self._connection_handle_at(event.pos())
        if connection_handle:
            self.setCursor(Qt.CrossCursor)
            self.update()
            super().hoverMoveEvent(event)
            return
        if self._editable_node_text_at(event.pos()) or self._editable_field_at(event.pos()):
            self.setCursor(Qt.IBeamCursor)
            self.update()
            super().hoverMoveEvent(event)
            return
        if not self.view.can_resize_nodes():
            self.setCursor(Qt.OpenHandCursor if self.view.can_move_nodes() else Qt.PointingHandCursor)
            self.update()
            super().hoverMoveEvent(event)
            return
        self.setCursor(Qt.SizeFDiagCursor if self._on_resize_handle(event.pos()) else Qt.OpenHandCursor)
        self.update()
        super().hoverMoveEvent(event)

    def hoverLeaveEvent(self, event) -> None:  # type: ignore[override]
        if self.view.hover_node_id == self.node.id:
            self.view.hover_node_id = None
        self.update()
        super().hoverLeaveEvent(event)

    def mousePressEvent(self, event) -> None:  # type: ignore[override]
        if event.button() == Qt.LeftButton:
            connection_handle = self._connection_handle_at(event.pos())
            if connection_handle:
                self.view.begin_connection_drag(
                    self.node.id,
                    self.mapToScene(self._connection_handle_points()[connection_handle]),
                )
                event.accept()
                return
            if QApplication.keyboardModifiers() & Qt.ControlModifier:
                self.view.toggle_node_selection(self.node.id)
            elif not self.isSelected() or not self.view.has_multi_node_selection():
                self.view.select_node(self.node.id)
            if not self.view.can_move_nodes():
                self.setCursor(Qt.PointingHandCursor)
                event.accept()
                return
            self._pressed_pos = event.scenePos()
            self._moved = False
            if self.view.can_resize_nodes() and self._on_resize_handle(event.pos()):
                self._resizing = True
                self._resize_origin = event.scenePos()
                self._resize_size = (self.width, self.height)
                self._resize_field_snapshot = self._visual_resize_snapshot()
                self.setCursor(Qt.SizeFDiagCursor)
                event.accept()
                return
            node_text_hit = self._editable_node_text_at(event.pos())
            if node_text_hit:
                self._inline_candidate_part = node_text_hit[0]
                self._inline_candidate_rect = node_text_hit[1]
                self._inline_candidate_field = None
            else:
                field_hit = self._editable_field_at(event.pos())
                self._inline_candidate_field = field_hit[0] if field_hit else None
                self._inline_candidate_part = ""
                self._inline_candidate_rect = field_hit[1] if field_hit else QRectF()
        super().mousePressEvent(event)

    def mouseDoubleClickEvent(self, event) -> None:  # type: ignore[override]
        if event.button() == Qt.LeftButton:
            self.unsetCursor()
            self.view.sync_interaction_cursor()
            self.view.nodeActivated.emit(self.node.id)
            event.accept()
            return
        super().mouseDoubleClickEvent(event)

    def mouseMoveEvent(self, event) -> None:  # type: ignore[override]
        if self.view.is_connection_dragging_from(self.node.id):
            self.view.update_connection_drag(event.scenePos())
            event.accept()
            return
        if self._resizing:
            delta = event.scenePos() - self._resize_origin
            self.prepareGeometryChange()
            has_visual_layout = any(field.has_visual_layout() for field in self.node.fields)
            min_width = VISUAL_NODE_MIN_WIDTH if has_visual_layout else NODE_MIN_WIDTH
            min_height = VISUAL_NODE_MIN_HEIGHT if has_visual_layout else NODE_MIN_HEIGHT
            self.width = max(min_width, self._resize_size[0] + delta.x())
            requested_height = max(min_height, self._resize_size[1] + delta.y())
            if has_visual_layout:
                self.height = requested_height
                if not self.view.is_data_canvas():
                    self._scale_visual_fields_for_resize()
            else:
                self.height = max(requested_height, self._natural_detail_height(self.width))
            self.node.width = self.width
            self.node.height = self.height
            self._moved = True
            self.view.update_edges_for_node(self.node.id)
            self.update()
            event.accept()
            return
        if (event.scenePos() - self._pressed_pos).manhattanLength() > 1:
            self._moved = True
            self._inline_candidate_field = None
            self._inline_candidate_part = ""
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event) -> None:  # type: ignore[override]
        if self.view.is_connection_dragging_from(self.node.id):
            self.view.finish_connection_drag(event.scenePos())
            event.accept()
            return
        was_resizing = self._resizing
        self._resizing = False
        self._resize_field_snapshot = []
        self.setCursor(Qt.OpenHandCursor)
        self.view.snap_guides.clear()
        self.view.viewport().update()
        if self._moved:
            if self.view.is_data_canvas() and was_resizing:
                if self.view.resize_data_canvas_template(self.node.id, self.width, self.height):
                    self.view.projectChanged.emit()
            elif self.view.is_data_canvas():
                self.view.commit_data_canvas_node_reorder(self.node.id)
                self.view.projectChanged.emit()
            else:
                self.view.refresh_group_membership()
                self.view.projectChanged.emit()
        elif (
            event.button() == Qt.LeftButton
            and (self._inline_candidate_field is not None or self._inline_candidate_part)
            and not was_resizing
        ):
            if self._inline_candidate_field is not None:
                self.view.start_inline_field_edit(self, self._inline_candidate_field, self._inline_candidate_rect)
            else:
                self.view.start_inline_node_text_edit(self, self._inline_candidate_part, self._inline_candidate_rect)
            event.accept()
            self._inline_candidate_field = None
            self._inline_candidate_part = ""
            self._inline_candidate_rect = QRectF()
            return
        self._inline_candidate_field = None
        self._inline_candidate_part = ""
        self._inline_candidate_rect = QRectF()
        super().mouseReleaseEvent(event)

    def itemChange(self, change: QGraphicsItem.GraphicsItemChange, value):  # type: ignore[override]
        if change == QGraphicsItem.ItemPositionChange and self.scene() and not self.view.rebuilding:
            pos = value
            if self.view.is_data_canvas():
                self.view.snap_guides.clear()
                return pos
            if self.view._moving_group:
                return pos
            if self.view.has_multi_node_selection():
                self.view.snap_guides.clear()
                return pos
            if QApplication.keyboardModifiers() & Qt.ControlModifier:
                self.view.snap_guides.clear()
                return pos
            return self.view.snap_position(self, pos)
        if change == QGraphicsItem.ItemPositionHasChanged and not self.view.rebuilding:
            previous = QPointF(self.node.x, self.node.y)
            self.node.x = self.pos().x()
            self.node.y = self.pos().y()
            delta = QPointF(self.node.x - previous.x(), self.node.y - previous.y())
            if abs(delta.x()) > 0.001 or abs(delta.y()) > 0.001:
                self.view._move_attached_notes(self.node.id, delta)
            self.view.update_edges_for_node(self.node.id)
        return super().itemChange(change, value)

    def refresh(self) -> None:
        self.prepareGeometryChange()
        self._sync_size()
        self.update()

    def _sync_size(self) -> None:
        use_natural_size = self.view.is_data_canvas()
        if self._uses_horizontal_thumbnail_row():
            self.width = self.view.horizontal_thumbnail_table_width()
            self.height = DATA_CANVAS_THUMBNAIL_ROW_HEIGHT
            self.node.width = self.width
            self.node.height = self.height
            return
        visual_fields = [field for field in self.node.fields if field.has_visual_layout()]
        if visual_fields and not self._uses_horizontal_data_row():
            node_width = 0.0 if use_natural_size else self.node.width
            node_height = 0.0 if use_natural_size else self.node.height
            visual_w, visual_h = visual_node_size(visual_fields, node_width, node_height)
            if use_natural_size:
                self.width = visual_w
                self.height = visual_h
                self.node.width = self.width
                self.node.height = self.height
            else:
                self.width = visual_w
                self.height = visual_h
            return
        natural_width = self._natural_detail_width()
        if use_natural_size:
            self.width = max(NODE_MIN_WIDTH, natural_width)
            natural_height = self._natural_detail_height(self.width)
            self.height = max(NODE_MIN_HEIGHT, natural_height)
            self.node.width = self.width
            self.node.height = self.height
            return
        self.width = max(NODE_MIN_WIDTH, self.node.width if self.node.width > 0 else natural_width)
        natural_height = self._natural_detail_height(self.width)
        self.height = max(NODE_MIN_HEIGHT, self.node.height if self.node.height > 0 else natural_height)

    def _natural_detail_width(self) -> float:
        title = f"{self.node.icon}  {self.node.title}" if self.node.icon else self.node.title
        title_width = QFontMetrics(_font(12, True)).horizontalAdvance(title) + 118
        if self._uses_horizontal_data_row():
            content_width = sum(segment_w for _field, _label_w, segment_w in self._horizontal_data_segments())
            content_width += max(0, len(self.node.fields) - 1) * ROW_GAP + 20
            return max(NODE_DEFAULT_WIDTH, title_width, content_width)
        name_w, type_w = self._row_column_widths()
        value_font = QFontMetrics(_font(9))
        value_widths = [
            min(max(value_font.horizontalAdvance(field.value or " "), 170), 420)
            for field in self.node.fields
        ]
        value_width = max(value_widths + [170])
        natural = max(NODE_DEFAULT_WIDTH, title_width, name_w + value_width + 44)
        return min(NODE_MAX_NATURAL_WIDTH, natural)

    def _natural_detail_height(self, width: float) -> float:
        if not self.node.fields:
            return HEADER_HEIGHT + 66
        if self._uses_horizontal_data_row():
            return ROW_TOP + 42.0 + 10
        name_w, type_w = self._row_column_widths()
        content_height = sum(
            self._row_height(field, width, name_w, type_w) + ROW_GAP
            for field in self.node.fields
        )
        return ROW_TOP + content_height + 10

    def _uses_horizontal_data_row(self) -> bool:
        canvas = self.view.active_canvas()
        return bool(
            canvas
            and canvas.is_data_canvas()
            and canvas.data_layout == "horizontal"
            and canvas.data_row_style != "thumbnail"
        )

    def _uses_horizontal_thumbnail_row(self) -> bool:
        canvas = self.view.active_canvas()
        return bool(
            canvas
            and canvas.is_data_canvas()
            and canvas.data_layout == "horizontal"
            and canvas.data_row_style == "thumbnail"
        )

    def _horizontal_data_segments(self) -> list[tuple[NodeField, float, float]]:
        return [(field, label_w, segment_w) for field, label_w, segment_w, _x in self._horizontal_data_segments_with_x()]

    def _horizontal_data_segments_with_x(self) -> list[tuple[NodeField, float, float, float]]:
        name_metrics = QFontMetrics(_font(8, True))
        value_metrics = QFontMetrics(_font(9))
        segments: list[tuple[NodeField, float, float, float]] = []
        x = 10.0
        for field in self.node.fields:
            label_w = min(140.0, max(46.0, float(name_metrics.horizontalAdvance(field.name) + 4)))
            value_w = min(360.0, max(44.0, float(value_metrics.horizontalAdvance(self._horizontal_field_value(field) or " ") + 6)))
            segment_w = max(140.0, min(320.0, label_w + value_w + 28.0))
            segments.append((field, label_w, segment_w, x))
            x += segment_w + ROW_GAP
        return segments

    def _horizontal_field_value(self, field: NodeField) -> str:
        if field.data_type == "图片":
            path = field.image_path.replace("\\", "/").rsplit("/", 1)[-1]
            return path or "图片"
        return field.value or " "

    def _row_column_widths(self) -> tuple[float, float]:
        name_metrics = QFontMetrics(_font(9, True))
        name_w = max([name_metrics.horizontalAdvance(field.name) + 18 for field in self.node.fields] + [92])
        return min(max(82.0, name_w), 148.0), 0.0

    def _row_height(self, field: NodeField, width: float, name_w: float, type_w: float) -> float:
        value_w = max(40.0, width - 20 - 10 - name_w - 14 - 20)
        name_h = self._wrapped_height(field.name, _font(9, True), name_w)
        value_h = self._wrapped_height(field.value or " ", _font(9), value_w)
        return max(40.0, name_h + 16, value_h + 16)

    def _wrapped_height(self, text: str, font: QFont, width: float) -> float:
        metrics = QFontMetrics(font)
        rect = metrics.boundingRect(
            QRect(0, 0, max(1, int(width)), 10000),
            WRAP_FLAGS,
            text,
        )
        return max(float(metrics.height()), float(rect.height()))

    def _fit_pixmap_rect(self, target: QRectF, width: int, height: int) -> QRectF:
        if width <= 0 or height <= 0 or target.width() <= 0 or target.height() <= 0:
            return QRectF()
        scale = min(target.width() / width, target.height() / height)
        draw_width = max(1.0, width * scale)
        draw_height = max(1.0, height * scale)
        return QRectF(
            target.x() + (target.width() - draw_width) / 2,
            target.y() + (target.height() - draw_height) / 2,
            draw_width,
            draw_height,
        )

    def _on_resize_handle(self, pos: QPointF) -> bool:
        rect = self.boundingRect()
        return pos.x() >= rect.right() - RESIZE_HANDLE and pos.y() >= rect.bottom() - RESIZE_HANDLE

    def _editable_node_text_at(self, pos: QPointF) -> tuple[str, QRectF] | None:
        if self.view.read_only or self.view.is_inline_field_editing() or self._uses_horizontal_thumbnail_row():
            return None
        for part, rect in self._editable_node_text_rects():
            if rect.contains(pos):
                return part, rect
        return None

    def _editable_node_text_rects(self) -> list[tuple[str, QRectF]]:
        rect = self.boundingRect()
        zoom = self.view.transform().m11()
        if zoom < 0.36:
            return [("icon", self._icon_mode_header_rect(rect))]
        if zoom < 0.62:
            icon_rect, title_rect = self._compact_header_rects(rect)
            return [("icon", icon_rect), ("title", title_rect)]
        icon_rect, title_rect = self._detail_header_rects(rect, self._has_visual_fields())
        result = [("title", title_rect)]
        if icon_rect.isValid() and icon_rect.width() > 0:
            result.insert(0, ("icon", icon_rect))
        return result

    def _icon_mode_header_rect(self, rect: QRectF) -> QRectF:
        return rect.adjusted(12, 22, -12, -8)

    def _compact_header_rects(self, rect: QRectF) -> tuple[QRectF, QRectF]:
        return (
            QRectF(18, 28, 58, max(28, rect.height() - 42)),
            QRectF(84, 28, rect.width() - 102, max(28, rect.height() - 42)),
        )

    def _detail_header_icon(self) -> str:
        return self.node.display_icon() or ("画" if self.node.node_type == "画布" else "链" if self.node.node_type == "超文本" else "")

    def _detail_header_rects(self, rect: QRectF, has_visual_fields: bool) -> tuple[QRectF, QRectF]:
        title_right_padding = 36 if has_visual_fields else 92
        icon = self._detail_header_icon()
        title_right = max(24.0, rect.width() - title_right_padding)
        if not icon:
            return QRectF(), QRectF(18, 28, max(24.0, title_right - 18), 22)
        metrics = QFontMetrics(_font(12, True))
        icon_width = max(22.0, min(54.0, float(metrics.horizontalAdvance(icon) + 10)))
        icon_rect = QRectF(16, 28, icon_width, 22)
        title_x = icon_rect.right() + 6
        return icon_rect, QRectF(title_x, 28, max(24.0, title_right - title_x), 22)

    def _has_visual_fields(self) -> bool:
        return any(field.has_visual_layout() for field in self.node.fields)

    def field_scene_rect(self, field: NodeField) -> QRectF:
        for candidate, rect in self._editable_field_rects():
            if candidate is field:
                return self.mapRectToScene(rect)
        return QRectF()

    def _editable_field_at(self, pos: QPointF) -> tuple[NodeField, QRectF] | None:
        if self.view.read_only or self.view.is_inline_field_editing():
            return None
        for field, rect in reversed(self._editable_field_rects()):
            if rect.contains(pos):
                return field, rect
        return None

    def _editable_field_rects(self) -> list[tuple[NodeField, QRectF]]:
        if self._uses_horizontal_thumbnail_row():
            return []
        if self._uses_horizontal_data_row():
            return [
                (field, QRectF(x, ROW_TOP, segment_w, 42.0))
                for field, _label_w, segment_w, x in self._horizontal_data_segments_with_x()
                if field.data_type != "图片"
            ]
        visual_fields = [field for field in self.node.fields if field.has_visual_layout()]
        if visual_fields:
            return [
                (
                    field,
                    QRectF(
                        field.x,
                        HEADER_HEIGHT + field.y,
                        max(24.0, field.width),
                        max(22.0, field.height),
                    ),
                )
                for field in visual_fields
                if field.data_type != "图片"
            ]
        name_w, type_w = self._row_column_widths()
        y = ROW_TOP
        rects: list[tuple[NodeField, QRectF]] = []
        for field in self.node.fields:
            row_h = self._row_height(field, self.width, name_w, type_w)
            value_x = 20 + name_w + 14
            rects.append((field, QRectF(value_x, y + 8, self.width - value_x - 20, row_h - 16)))
            y += row_h + ROW_GAP
        return rects

    def _visual_resize_snapshot(self) -> list[tuple[NodeField, float, float, float, float, int]]:
        if self.view.is_data_canvas():
            return []
        return [
            (field, field.x, field.y, field.width, field.height, field.font_size)
            for field in self.node.fields
            if field.has_visual_layout()
        ]

    def _scale_visual_fields_for_resize(self) -> None:
        if not self._resize_field_snapshot:
            return
        origin_width = max(1.0, self._resize_size[0])
        origin_content_height = max(1.0, self._resize_size[1] - HEADER_HEIGHT)
        scale_x = max(0.05, self.width / origin_width)
        scale_y = max(0.05, (self.height - HEADER_HEIGHT) / origin_content_height)
        font_scale = max(0.25, min(4.0, min(scale_x, scale_y)))
        for field, x, y, width, height, font_size in self._resize_field_snapshot:
            field.x = max(0.0, x * scale_x)
            field.y = max(0.0, y * scale_y)
            field.width = max(44.0, width * scale_x)
            field.height = max(34.0, height * scale_y)
            field.font_size = max(8, min(48, int(round(font_size * font_scale))))

    def _shows_connection_handles(self) -> bool:
        return bool(
            self.view.can_create_edges()
            and not self._uses_horizontal_thumbnail_row()
            and (self.isSelected() or self.view.hover_node_id == self.node.id or self.view.connection_source == self.node.id)
        )

    def _connection_handle_points(self) -> dict[str, QPointF]:
        rect = self.boundingRect()
        inset = CONNECTION_HANDLE_RADIUS + 1.0
        return {
            "top": QPointF(rect.center().x(), rect.top() + inset),
            "right": QPointF(rect.right() - inset, rect.center().y()),
            "bottom": QPointF(rect.center().x(), rect.bottom() - inset),
            "left": QPointF(rect.left() + inset, rect.center().y()),
        }

    def _connection_handle_at(self, pos: QPointF) -> str | None:
        if not self.view.can_create_edges() or self._uses_horizontal_thumbnail_row():
            return None
        for side, point in self._connection_handle_points().items():
            delta = point - pos
            if math.hypot(delta.x(), delta.y()) <= CONNECTION_HANDLE_HIT_RADIUS:
                return side
        return None


class NoteItem(QGraphicsObject):
    def __init__(self, note: DesignNote, view: "NodeGraphView", owner_node_id: str = "") -> None:
        super().__init__()
        self.note = note
        self.view = view
        self.owner_node_id = owner_node_id
        self.width = max(NOTE_MIN_WIDTH, note.width or NOTE_DEFAULT_WIDTH)
        self.height = max(NOTE_MIN_HEIGHT, note.height or NOTE_DEFAULT_HEIGHT)
        self._pressed_pos = QPointF()
        self._moved = False
        self._last_pos = QPointF(note.x, note.y)
        self.setPos(note.x, note.y)
        flags = QGraphicsItem.ItemIsSelectable
        if self.view.can_move_notes():
            flags |= QGraphicsItem.ItemIsMovable | QGraphicsItem.ItemSendsGeometryChanges
        self.setFlags(flags)
        self.setAcceptHoverEvents(True)
        self.setZValue(24)

    def boundingRect(self) -> QRectF:
        return QRectF(0, 0, self.width, self.height)

    def shape(self) -> QPainterPath:
        path = QPainterPath()
        path.addRoundedRect(self.boundingRect(), 8, 8)
        for point in self._connection_handle_points().values():
            path.addEllipse(point, CONNECTION_HANDLE_HIT_RADIUS, CONNECTION_HANDLE_HIT_RADIUS)
        return path

    def paint(self, painter: QPainter, _option, _widget=None) -> None:  # type: ignore[override]
        colors = palette(self.view.theme)
        painter.setRenderHint(QPainter.Antialiasing, True)
        rect = self.boundingRect()
        fill = QColor("#FFF3B0" if self.view.theme == "light" else "#2F2A1D")
        header = QColor("#FFE070" if self.view.theme == "light" else "#4A4025")
        border = QColor(colors["blue"] if self.isSelected() else ("#D6B64A" if self.view.theme == "light" else "#75633A"))
        text_color = QColor("#2B2410" if self.view.theme == "light" else colors["text"])
        muted_color = QColor("#685D34" if self.view.theme == "light" else colors["text_muted"])

        path = QPainterPath()
        path.addRoundedRect(rect.adjusted(0.5, 0.5, -0.5, -0.5), 8, 8)
        painter.fillPath(path, fill)
        painter.save()
        painter.setClipPath(path)
        painter.fillRect(QRectF(0, 0, rect.width(), 30), header)
        painter.restore()
        painter.setPen(QPen(border, 2.0 if self.isSelected() else 1.1))
        painter.drawPath(path)

        title = self.note.display_title()
        title_metrics = QFontMetrics(_font(10, True))
        painter.setPen(text_color)
        painter.setFont(_font(10, True))
        painter.drawText(
            QRectF(12, 5, rect.width() - 24, 20),
            Qt.AlignLeft | Qt.AlignVCenter | Qt.TextSingleLine,
            title_metrics.elidedText(title, Qt.ElideRight, max(1, int(rect.width() - 24))),
        )

        content = self.note.content.strip()
        if content:
            painter.setPen(text_color)
            painter.setFont(_font(9))
            painter.drawText(
                rect.adjusted(12, 40, -12, -10),
                Qt.AlignLeft | Qt.AlignTop | Qt.TextWordWrap | Qt.TextWrapAnywhere,
                content,
            )
        else:
            painter.setPen(muted_color)
            painter.setFont(_font(9))
            painter.drawText(rect.adjusted(12, 40, -12, -10), Qt.AlignLeft | Qt.AlignTop, "空便签")

        if self._shows_connection_handles():
            self._paint_connection_handles(painter)

    def hoverMoveEvent(self, event) -> None:  # type: ignore[override]
        self.setCursor(Qt.CrossCursor if self._connection_handle_at(event.pos()) else Qt.OpenHandCursor)
        self.update()
        super().hoverMoveEvent(event)

    def hoverLeaveEvent(self, event) -> None:  # type: ignore[override]
        self.update()
        super().hoverLeaveEvent(event)

    def mousePressEvent(self, event) -> None:  # type: ignore[override]
        if event.button() == Qt.LeftButton:
            connection_handle = self._connection_handle_at(event.pos())
            if connection_handle:
                self.view.begin_connection_drag(
                    self.note.id,
                    self.mapToScene(self._connection_handle_points()[connection_handle]),
                )
                event.accept()
                return
            self.view.select_note(self.note.id, self.owner_node_id)
            if not self.view.can_move_notes():
                event.accept()
                return
            self._pressed_pos = event.scenePos()
            self._moved = False
            self._last_pos = self.pos()
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event) -> None:  # type: ignore[override]
        if self.view.is_connection_dragging_from(self.note.id):
            self.view.update_connection_drag(event.scenePos())
            event.accept()
            return
        if (event.scenePos() - self._pressed_pos).manhattanLength() > 1:
            self._moved = True
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event) -> None:  # type: ignore[override]
        if self.view.is_connection_dragging_from(self.note.id):
            self.view.finish_connection_drag(event.scenePos())
            event.accept()
            return
        self.view.snap_guides.clear()
        self.view.viewport().update()
        if self._moved or self.pos() != self._last_pos:
            self.view.projectChanged.emit()
        super().mouseReleaseEvent(event)

    def mouseDoubleClickEvent(self, event) -> None:  # type: ignore[override]
        if event.button() == Qt.LeftButton:
            self.view.noteEditRequested.emit(self.note.id, self.owner_node_id)
            event.accept()
            return
        super().mouseDoubleClickEvent(event)

    def itemChange(self, change: QGraphicsItem.GraphicsItemChange, value):  # type: ignore[override]
        if change == QGraphicsItem.ItemPositionChange and self.scene() and not self.view.rebuilding:
            if self.view._moving_attached_notes:
                return value
            pos = value
            if QApplication.keyboardModifiers() & Qt.ControlModifier:
                self.view.snap_guides.clear()
                return pos
            return self.view.snap_position(self, pos)
        if change == QGraphicsItem.ItemPositionHasChanged and not self.view.rebuilding:
            self.note.x = self.pos().x()
            self.note.y = self.pos().y()
            self._last_pos = QPointF(self.pos())
            self.view._update_scene_rect()
        return super().itemChange(change, value)

    def _shows_connection_handles(self) -> bool:
        return bool(
            self.view.can_create_edges()
            and (self.isSelected() or self.view.connection_source == self.note.id)
        )

    def _connection_handle_points(self) -> dict[str, QPointF]:
        rect = self.boundingRect()
        inset = CONNECTION_HANDLE_RADIUS + 1.0
        return {
            "top": QPointF(rect.center().x(), rect.top() + inset),
            "right": QPointF(rect.right() - inset, rect.center().y()),
            "bottom": QPointF(rect.center().x(), rect.bottom() - inset),
            "left": QPointF(rect.left() + inset, rect.center().y()),
        }

    def _connection_handle_at(self, pos: QPointF) -> str | None:
        if not self.view.can_create_edges():
            return None
        for side, point in self._connection_handle_points().items():
            delta = point - pos
            if math.hypot(delta.x(), delta.y()) <= CONNECTION_HANDLE_HIT_RADIUS:
                return side
        return None

    def _paint_connection_handles(self, painter: QPainter) -> None:
        fill = QColor("#FF496A")
        outline = QColor("#FFFFFF" if self.view.theme == "dark" else "#1D1D1F")
        painter.setBrush(QBrush(fill))
        painter.setPen(QPen(outline, 1.2))
        radius = CONNECTION_HANDLE_RADIUS
        for point in self._connection_handle_points().values():
            painter.drawEllipse(point, radius, radius)


class EdgeItem(QGraphicsPathItem):
    def __init__(self, edge: Edge, source: QGraphicsItem, target: QGraphicsItem, view: "NodeGraphView") -> None:
        super().__init__()
        self.edge = edge
        self.source = source
        self.target = target
        self.view = view
        self._dragging_bend = False
        self._dragging_route_index: int | None = None
        self._selected_route_index: int | None = None
        self._dragging_bend_changed = False
        self.setZValue(0)
        self.setFlags(QGraphicsItem.ItemIsSelectable)
        self.setAcceptHoverEvents(True)
        self.update_path()

    def boundingRect(self) -> QRectF:  # type: ignore[override]
        rect = super().boundingRect()
        label_rect = self._label_rect()
        if label_rect.isValid():
            rect = rect.united(label_rect.adjusted(-2, -2, 2, 2))
        return rect

    def update_path(self) -> None:
        source_rect = self.source.sceneBoundingRect()
        target_rect = self.target.sceneBoundingRect()
        start = self._anchor(source_rect, target_rect)
        end = self._anchor(target_rect, source_rect)
        style = self.edge.style if self.edge.style in {"curve", "straight", "orthogonal"} else "curve"
        if style == "straight":
            path = QPainterPath(start)
            path.lineTo(end)
            self.setPath(path)
            return
        if style == "orthogonal":
            path = QPainterPath(start)
            points = self._orthogonal_points(start, end, source_rect, target_rect)
            for point in points[1:]:
                path.lineTo(point)
            self.setPath(path)
            return

        c1, c2 = self._curve_control_points(start, end, source_rect, target_rect)
        path = QPainterPath(start)
        path.cubicTo(c1, c2, end)
        self.setPath(path)

    def paint(self, painter: QPainter, _option, _widget=None) -> None:  # type: ignore[override]
        colors = palette(self.view.theme)
        painter.setRenderHint(QPainter.Antialiasing, True)
        selected = self.isSelected()
        if selected:
            painter.setPen(QPen(QColor(colors["edge_selected_glow"]), 9, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
            painter.drawPath(self.path())
        edge_color = QColor(colors["edge_selected"] if selected else colors["edge"])
        painter.setPen(QPen(edge_color, 3.2 if selected else 2.2, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
        painter.drawPath(self.path())
        self._draw_arrow(painter, edge_color)
        if selected and self.edge.style == "orthogonal":
            self._paint_bend_handles(painter, colors)
        if self.edge.label:
            self._paint_label(painter, colors)

    def _paint_label(self, painter: QPainter, colors: dict[str, str]) -> None:
        rect = self._label_rect()
        if not rect.isValid():
            return
        label_font = _font(9)
        metrics = QFontMetrics(label_font)
        fill = QColor(colors["panel"])
        fill.setAlpha(232)
        path = QPainterPath()
        path.addRoundedRect(rect, 7, 7)
        painter.fillPath(path, fill)
        painter.setPen(QPen(QColor(colors["hairline"]), 1))
        painter.drawPath(path)
        painter.setPen(QColor(colors["edge_label"]))
        painter.setFont(label_font)
        text_rect = rect.adjusted(EDGE_LABEL_PADDING_X, 0, -EDGE_LABEL_PADDING_X, 0)
        painter.drawText(
            text_rect,
            Qt.AlignCenter | Qt.TextSingleLine,
            metrics.elidedText(self.edge.label.strip(), Qt.ElideRight, max(1, int(text_rect.width()))),
        )

    def _label_rect(self) -> QRectF:
        text = self.edge.label.strip()
        if not text:
            return QRectF()
        metrics = QFontMetrics(_font(9))
        width = min(
            EDGE_LABEL_MAX_WIDTH,
            max(EDGE_LABEL_MIN_WIDTH, float(metrics.horizontalAdvance(text) + EDGE_LABEL_PADDING_X * 2)),
        )
        center = self._label_center()
        return QRectF(center.x() - width / 2, center.y() - EDGE_LABEL_HEIGHT / 2, width, EDGE_LABEL_HEIGHT)

    def _label_center(self) -> QPointF:
        path = self.path()
        point = path.pointAtPercent(0.5)
        before = path.pointAtPercent(0.48)
        after = path.pointAtPercent(0.52)
        dx = after.x() - before.x()
        dy = after.y() - before.y()
        length = math.hypot(dx, dy)
        if length <= 0.001:
            normal = QPointF(0.0, -1.0)
        else:
            normal = QPointF(-dy / length, dx / length)
            if normal.y() > 0:
                normal = QPointF(-normal.x(), -normal.y())
            if abs(normal.y()) < 0.15:
                normal = QPointF(0.0, -1.0)
        return point + normal * EDGE_LABEL_OFFSET

    def shape(self) -> QPainterPath:
        stroker = QPainterPathStroker()
        stroker.setWidth(16)
        shape = stroker.createStroke(self.path())
        label_rect = self._label_rect()
        if label_rect.isValid():
            shape.addRect(label_rect.adjusted(-3, -3, 3, 3))
        return shape

    def mousePressEvent(self, event) -> None:  # type: ignore[override]
        if event.button() == Qt.LeftButton:
            was_selected = self.isSelected()
            self.view.select_edge(self.edge.id)
            bend_hit = self._bend_handle_at(event.pos())
            if bend_hit is not None:
                route_index, route_point = bend_hit
                if route_index is None:
                    route_index = self._set_route_points([route_point])
                self._dragging_bend = True
                self._dragging_route_index = route_index
                self._selected_route_index = route_index
                self._dragging_bend_changed = False
                self.setCursor(Qt.SizeAllCursor)
                event.accept()
                return
            if was_selected and self.edge.style == "orthogonal":
                inserted_index = self._insert_route_point_at(event.scenePos())
                if inserted_index is not None:
                    self._dragging_bend = True
                    self._dragging_route_index = inserted_index
                    self._selected_route_index = inserted_index
                    self._dragging_bend_changed = True
                    self.setCursor(Qt.SizeAllCursor)
                    self.update_path()
                    event.accept()
                    return
            self._selected_route_index = None
            self.update()
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event) -> None:  # type: ignore[override]
        if self._dragging_bend:
            scene_pos = event.scenePos()
            if self._move_route_point(self._dragging_route_index, scene_pos):
                self._dragging_bend_changed = True
            self.update_path()
            self.update()
            self.view.viewport().update()
            event.accept()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event) -> None:  # type: ignore[override]
        if self._dragging_bend:
            changed = self._dragging_bend_changed
            self._dragging_bend = False
            self._dragging_route_index = None
            self._dragging_bend_changed = False
            self.unsetCursor()
            self.view.select_edge(self.edge.id)
            if changed:
                self.view.projectChanged.emit()
            event.accept()
            return
        super().mouseReleaseEvent(event)

    def hoverMoveEvent(self, event) -> None:  # type: ignore[override]
        if self._bend_handle_at(event.pos()):
            self.setCursor(Qt.SizeAllCursor)
        else:
            self.setCursor(Qt.PointingHandCursor)
        super().hoverMoveEvent(event)

    def hoverLeaveEvent(self, event) -> None:  # type: ignore[override]
        self.unsetCursor()
        super().hoverLeaveEvent(event)

    def _draw_arrow(self, painter: QPainter, color: QColor) -> None:
        path = self.path()
        end = path.pointAtPercent(1)
        before = self._point_before_end(path)
        angle = math.atan2(end.y() - before.y(), end.x() - before.x())
        size = 12.0
        left = QPointF(
            end.x() - math.cos(angle - math.pi / 6) * size,
            end.y() - math.sin(angle - math.pi / 6) * size,
        )
        right = QPointF(
            end.x() - math.cos(angle + math.pi / 6) * size,
            end.y() - math.sin(angle + math.pi / 6) * size,
        )
        painter.setBrush(color)
        painter.setPen(QPen(color, 1))
        painter.drawPolygon(QPolygonF([end, left, right]))

    def _point_before_end(self, path: QPainterPath) -> QPointF:
        if self.edge.style == "orthogonal":
            polygons = path.toSubpathPolygons()
            if polygons:
                points = list(polygons[0])
                if len(points) >= 2:
                    return points[-2]
        if self.edge.style == "curve":
            return path.pointAtPercent(0.995)
        return path.pointAtPercent(0.97)

    def _anchor(self, rect: QRectF, other: QRectF) -> QPointF:
        center = rect.center()
        other_center = other.center()
        dx = other_center.x() - center.x()
        dy = other_center.y() - center.y()
        if abs(dx) >= abs(dy):
            return QPointF(rect.right() if dx >= 0 else rect.left(), center.y())
        return QPointF(center.x(), rect.bottom() if dy >= 0 else rect.top())

    def _curve_control_points(
        self,
        start: QPointF,
        end: QPointF,
        source_rect: QRectF,
        target_rect: QRectF,
    ) -> tuple[QPointF, QPointF]:
        distance = max(1.0, math.hypot(end.x() - start.x(), end.y() - start.y()))
        pull = max(70.0, min(distance * 0.34, 220.0))
        source_vector = self._normalized(start - source_rect.center())
        target_vector = self._normalized(target_rect.center() - end)
        if source_vector is None or target_vector is None:
            dx = end.x() - start.x()
            dy = end.y() - start.y()
            if abs(dx) >= abs(dy):
                direction = 1.0 if dx >= 0 else -1.0
                source_vector = QPointF(direction, 0)
                target_vector = QPointF(direction, 0)
            else:
                direction = 1.0 if dy >= 0 else -1.0
                source_vector = QPointF(0, direction)
                target_vector = QPointF(0, direction)
        c1 = start + source_vector * pull
        c2 = end - target_vector * pull
        return c1, c2

    def _paint_bend_handles(self, painter: QPainter, colors: dict[str, str]) -> None:
        painter.setPen(QPen(QColor("#FFFFFF" if self.view.theme == "dark" else colors["accent_dark"]), 1.4))
        for point, route_index in self._bend_handles():
            painter.setBrush(QBrush(QColor(colors["warning"] if route_index == self._selected_route_index else colors["blue"])))
            painter.drawEllipse(point, EDGE_BEND_HANDLE_RADIUS, EDGE_BEND_HANDLE_RADIUS)

    def _bend_handles(self) -> list[tuple[QPointF, int | None]]:
        if self.edge.style != "orthogonal":
            return []
        route_points = self._route_points()
        if route_points:
            return [(point, index) for index, point in enumerate(route_points)]
        source_rect = self.source.sceneBoundingRect()
        target_rect = self.target.sceneBoundingRect()
        start = self._anchor(source_rect, target_rect)
        end = self._anchor(target_rect, source_rect)
        points = self._orthogonal_points(start, end, source_rect, target_rect)
        if len(points) < 3:
            return []
        handles: list[tuple[QPointF, int | None]] = []
        for index in range(1, len(points) - 1):
            previous = points[index - 1]
            point = points[index]
            next_point = points[index + 1]
            if not self._is_turn(previous, point, next_point):
                continue
            if not any(math.hypot(point.x() - existing.x(), point.y() - existing.y()) < 0.5 for existing, _route_index in handles):
                handles.append((point, None))
        return handles

    def _bend_handle_at(self, pos: QPointF) -> tuple[int | None, QPointF] | None:
        if self.edge.style != "orthogonal" or not self.isSelected():
            return None
        scene_pos = self.mapToScene(pos)
        for point, route_index in self._bend_handles():
            if math.hypot(scene_pos.x() - point.x(), scene_pos.y() - point.y()) <= EDGE_BEND_HANDLE_HIT_RADIUS:
                return route_index, point
        return None

    def delete_selected_route_point(self) -> bool:
        route_index = self._selected_route_index
        return self.delete_route_point(route_index)

    def delete_route_point(self, route_index: int | None) -> bool:
        route_points = self._route_points()
        if route_index is None or route_index < 0 or route_index >= len(route_points):
            return False
        del route_points[route_index]
        self._set_route_points(route_points)
        self._selected_route_index = None
        self.update_path()
        self.update()
        self.view.viewport().update()
        return True

    def route_point_index_at_scene(self, scene_pos: QPointF) -> int | None:
        hit = self._bend_handle_at(self.mapFromScene(scene_pos))
        if hit is None:
            return None
        route_index, _route_point = hit
        return route_index

    def delete_route_point_at_scene(self, scene_pos: QPointF) -> bool:
        return self.delete_route_point(self.route_point_index_at_scene(scene_pos))

    def _insert_route_point_at(self, scene_pos: QPointF) -> int | None:
        source_rect = self.source.sceneBoundingRect()
        target_rect = self.target.sceneBoundingRect()
        start = self._anchor(source_rect, target_rect)
        end = self._anchor(target_rect, source_rect)
        path_points = self._orthogonal_points(start, end, source_rect, target_rect)
        segment_index = self._segment_index_at(path_points, scene_pos)
        if segment_index is None:
            return None
        route_points = self._route_points()
        insert_index = self._route_insert_index_for_segment(segment_index, path_points, route_points)
        route_points.insert(insert_index, QPointF(scene_pos))
        return self._set_route_points(route_points, preferred_index=insert_index)

    def _move_route_point(self, route_index: int | None, scene_pos: QPointF) -> bool:
        if route_index is None:
            return False
        route_points = self._route_points()
        if route_index < 0 or route_index >= len(route_points):
            return False
        current = route_points[route_index]
        if math.hypot(current.x() - scene_pos.x(), current.y() - scene_pos.y()) < 0.5:
            return False
        route_points[route_index] = QPointF(scene_pos)
        merge_index = self._set_route_points(route_points, preferred_index=route_index)
        current_route = self._route_points()
        self._selected_route_index = min(merge_index, max(0, len(current_route) - 1)) if current_route else None
        self._dragging_route_index = self._selected_route_index
        return True

    def _route_points(self) -> list[QPointF]:
        route = getattr(self.edge, "orthogonal_route", [])
        if route:
            return self._normalized_route_points([QPointF(float(point["x"]), float(point["y"])) for point in route])
        if self.edge.orthogonal_bend_x is not None and self.edge.orthogonal_bend_y is not None:
            return self._normalized_route_points([QPointF(self.edge.orthogonal_bend_x, self.edge.orthogonal_bend_y)])
        return []

    def _set_route_points(self, points: list[QPointF], preferred_index: int | None = None) -> int:
        cleaned = [QPointF(point) for point in points]
        selected_index = self._merge_close_route_points(cleaned, preferred_index=preferred_index)
        cleaned = self._snap_route_points_for_endpoints(cleaned)
        selected_index = min(selected_index, max(0, len(cleaned) - 1)) if cleaned else 0
        self.edge.orthogonal_route = [{"x": point.x(), "y": point.y()} for point in cleaned]
        if cleaned:
            self.edge.orthogonal_bend_x = cleaned[0].x()
            self.edge.orthogonal_bend_y = cleaned[0].y()
        else:
            self.edge.orthogonal_bend_x = None
            self.edge.orthogonal_bend_y = None
        return selected_index

    def _merge_close_route_points(self, points: list[QPointF], preferred_index: int | None = None) -> int:
        index = 1
        selected_index = preferred_index if preferred_index is not None else 0
        while index < len(points):
            if self._point_distance(points[index - 1], points[index]) <= ORTHOGONAL_ROUTE_MERGE_DISTANCE:
                merged = self._average_point(points[index - 1], points[index])
                points[index - 1] = merged
                del points[index]
                if selected_index is not None and selected_index >= index:
                    selected_index -= 1
                continue
            index += 1
        return max(0, min(selected_index or 0, len(points) - 1)) if points else 0

    def _merged_route_points(self, points: list[QPointF]) -> list[QPointF]:
        merged = [QPointF(point) for point in points]
        self._merge_close_route_points(merged)
        return merged

    def _normalized_route_points(self, points: list[QPointF]) -> list[QPointF]:
        normalized = self._merged_route_points(points)
        normalized = self._snap_route_points_for_endpoints(normalized)
        self._merge_close_route_points(normalized)
        return normalized

    def _snap_route_points_for_endpoints(self, points: list[QPointF]) -> list[QPointF]:
        if not points:
            return []
        snapped = [QPointF(point) for point in points]
        source_rect = self.source.sceneBoundingRect()
        target_rect = self.target.sceneBoundingRect()
        start = self._anchor(source_rect, target_rect)
        end = self._anchor(target_rect, source_rect)
        source_normal = self._anchor_normal(source_rect, start)
        target_normal = self._anchor_normal(target_rect, end)
        source_stub = start + source_normal * ORTHOGONAL_STUB_LENGTH
        target_stub = end + target_normal * ORTHOGONAL_STUB_LENGTH
        source_axis = self._axis_for_vector(source_normal)
        target_axis = self._axis_for_vector(target_normal)
        self._snap_point_to_axis(snapped[0], source_stub, self._perpendicular_axis(source_axis))
        self._snap_point_to_axis(snapped[-1], target_stub, target_axis, ORTHOGONAL_ENDPOINT_AXIS_SNAP_DISTANCE)
        self._snap_point_to_axis(snapped[-1], target_stub, self._perpendicular_axis(target_axis))
        for index in range(1, len(snapped)):
            previous = snapped[index - 1]
            current = snapped[index]
            if abs(previous.x() - current.x()) <= ORTHOGONAL_ROUTE_MERGE_DISTANCE:
                x = (previous.x() + current.x()) / 2
                previous.setX(x)
                current.setX(x)
            if abs(previous.y() - current.y()) <= ORTHOGONAL_ROUTE_MERGE_DISTANCE:
                y = (previous.y() + current.y()) / 2
                previous.setY(y)
                current.setY(y)
        return snapped

    def _snap_point_to_axis(
        self,
        point: QPointF,
        anchor: QPointF,
        axis: str,
        distance: float = ORTHOGONAL_ROUTE_MERGE_DISTANCE,
    ) -> None:
        if axis == "y" and abs(point.x() - anchor.x()) <= distance:
            point.setX(anchor.x())
        elif axis == "x" and abs(point.y() - anchor.y()) <= distance:
            point.setY(anchor.y())

    def _segment_index_at(self, points: list[QPointF], scene_pos: QPointF) -> int | None:
        if len(points) < 2:
            return None
        scale = max(0.001, self.view.transform().m11())
        threshold = max(7.0, 10.0 / scale)
        best: tuple[float, int] | None = None
        for index in range(len(points) - 1):
            distance = self._distance_to_segment(scene_pos, points[index], points[index + 1])
            if distance <= threshold and (best is None or distance < best[0]):
                best = (distance, index)
        return best[1] if best else None

    def _route_insert_index_for_segment(
        self,
        segment_index: int,
        path_points: list[QPointF],
        route_points: list[QPointF],
    ) -> int:
        if not route_points:
            return 0
        route_path_indices: list[int] = []
        for route_point in route_points:
            path_index = next(
                (
                    index
                    for index, path_point in enumerate(path_points)
                    if math.hypot(path_point.x() - route_point.x(), path_point.y() - route_point.y()) < 0.5
                ),
                len(path_points),
            )
            route_path_indices.append(path_index)
        return sum(1 for path_index in route_path_indices if path_index <= segment_index)

    def _orthogonal_points(
        self,
        start: QPointF,
        end: QPointF,
        source_rect: QRectF,
        target_rect: QRectF,
    ) -> list[QPointF]:
        source_normal = self._anchor_normal(source_rect, start)
        target_normal = self._anchor_normal(target_rect, end)
        source_stub = start + source_normal * ORTHOGONAL_STUB_LENGTH
        target_stub = end + target_normal * ORTHOGONAL_STUB_LENGTH
        route_points = self._route_points() or self._legacy_axis_route_points(source_stub, target_stub)

        source_axis = self._axis_for_vector(source_normal)
        target_axis = self._axis_for_vector(target_normal)
        first_axis = self._perpendicular_axis(source_axis)
        last_axis = self._perpendicular_axis(target_axis)
        points = [start, source_stub]
        current = source_stub
        previous_axis = source_axis

        for route_index, route_point in enumerate(route_points):
            connector_axis = self._perpendicular_axis(previous_axis)
            if route_index == 0 and self._axis_between(current, route_point) == source_axis:
                additions = [route_point]
            else:
                additions = self._orthogonal_connector(current, route_point, first_axis=connector_axis)
            points.extend(additions)
            previous_axis = self._last_segment_axis(points, previous_axis)
            current = route_point

        connector_axis = self._perpendicular_axis(previous_axis) if route_points else first_axis
        if route_points and self._axis_between(current, target_stub) == target_axis:
            points.append(target_stub)
        else:
            points.extend(
                self._orthogonal_connector(
                    current,
                    target_stub,
                    first_axis=connector_axis,
                    last_axis=last_axis,
                )
            )
        points.append(end)
        return self._clean_orthogonal_points(points)

    def _legacy_axis_route_points(self, source_stub: QPointF, target_stub: QPointF) -> list[QPointF]:
        if self.edge.orthogonal_bend_x is not None and self.edge.orthogonal_bend_y is None:
            x = self.edge.orthogonal_bend_x
            return [QPointF(x, source_stub.y()), QPointF(x, target_stub.y())]
        if self.edge.orthogonal_bend_y is not None and self.edge.orthogonal_bend_x is None:
            y = self.edge.orthogonal_bend_y
            return [QPointF(source_stub.x(), y), QPointF(target_stub.x(), y)]
        return []

    def _orthogonal_connector(
        self,
        start: QPointF,
        end: QPointF,
        *,
        first_axis: str | None = None,
        last_axis: str | None = None,
    ) -> list[QPointF]:
        dx = end.x() - start.x()
        dy = end.y() - start.y()
        if abs(dx) < 0.5 and abs(dy) < 0.5:
            return [end]
        direct_axis = "x" if abs(dy) < 0.5 else "y" if abs(dx) < 0.5 else None
        if direct_axis is not None:
            if (first_axis and first_axis != direct_axis) or (last_axis and last_axis != direct_axis):
                return self._detour_connector(start, end, direct_axis, first_axis, last_axis)
            return [end]

        if first_axis and last_axis and first_axis == last_axis:
            if first_axis == "x":
                mid_x = (start.x() + end.x()) / 2
                return [QPointF(mid_x, start.y()), QPointF(mid_x, end.y()), end]
            mid_y = (start.y() + end.y()) / 2
            return [QPointF(start.x(), mid_y), QPointF(end.x(), mid_y), end]
        axis = first_axis or ("x" if abs(dx) >= abs(dy) else "y")
        if last_axis and not first_axis:
            axis = "y" if last_axis == "x" else "x"
        if axis == "x":
            return [QPointF(end.x(), start.y()), end]
        return [QPointF(start.x(), end.y()), end]

    def _detour_connector(
        self,
        start: QPointF,
        end: QPointF,
        direct_axis: str,
        first_axis: str | None,
        last_axis: str | None,
    ) -> list[QPointF]:
        offset = ORTHOGONAL_STUB_LENGTH
        if direct_axis == "x":
            sign = 1.0 if (end.y() - start.y()) >= 0 else -1.0
            y = start.y() + offset * sign
            if first_axis == "x" and last_axis == "x":
                y = (start.y() + end.y()) / 2
            return [QPointF(start.x(), y), QPointF(end.x(), y), end]
        sign = 1.0 if (end.x() - start.x()) >= 0 else -1.0
        x = start.x() + offset * sign
        if first_axis == "y" and last_axis == "y":
            x = (start.x() + end.x()) / 2
        return [QPointF(x, start.y()), QPointF(x, end.y()), end]

    def _clean_orthogonal_points(self, points: list[QPointF]) -> list[QPointF]:
        cleaned: list[QPointF] = []
        for point in points:
            if cleaned and self._point_distance(point, cleaned[-1]) < 0.5:
                continue
            cleaned.append(point)
        changed = True
        while changed and len(cleaned) >= 3:
            changed = False
            reduced: list[QPointF] = [cleaned[0]]
            for index in range(1, len(cleaned) - 1):
                previous = reduced[-1]
                point = cleaned[index]
                next_point = cleaned[index + 1]
                if self._can_remove_orthogonal_point(previous, point, next_point):
                    changed = True
                    continue
                reduced.append(point)
            reduced.append(cleaned[-1])
            cleaned = reduced
        return cleaned

    def _can_remove_orthogonal_point(self, previous: QPointF, point: QPointF, next_point: QPointF) -> bool:
        if not self._is_axis_aligned(previous, next_point):
            return False
        if self._point_distance(previous, point) <= ORTHOGONAL_SEGMENT_MERGE_DISTANCE:
            return True
        if self._point_distance(point, next_point) <= ORTHOGONAL_SEGMENT_MERGE_DISTANCE:
            return True
        return self._is_redundant_orthogonal_point(previous, point, next_point)

    def _is_redundant_orthogonal_point(self, previous: QPointF, point: QPointF, next_point: QPointF) -> bool:
        if abs(previous.x() - point.x()) <= ORTHOGONAL_SEGMENT_MERGE_DISTANCE and abs(point.x() - next_point.x()) <= ORTHOGONAL_SEGMENT_MERGE_DISTANCE:
            return True
        if abs(previous.y() - point.y()) <= ORTHOGONAL_SEGMENT_MERGE_DISTANCE and abs(point.y() - next_point.y()) <= ORTHOGONAL_SEGMENT_MERGE_DISTANCE:
            return True
        return False

    def _is_axis_aligned(self, first: QPointF, second: QPointF) -> bool:
        return abs(first.x() - second.x()) < 0.5 or abs(first.y() - second.y()) < 0.5

    def _point_distance(self, first: QPointF, second: QPointF) -> float:
        return math.hypot(first.x() - second.x(), first.y() - second.y())

    def _average_point(self, first: QPointF, second: QPointF) -> QPointF:
        return QPointF((first.x() + second.x()) / 2, (first.y() + second.y()) / 2)

    def _anchor_normal(self, rect: QRectF, anchor: QPointF) -> QPointF:
        delta = anchor - rect.center()
        if abs(delta.x()) >= abs(delta.y()):
            return QPointF(1.0 if delta.x() >= 0 else -1.0, 0.0)
        return QPointF(0.0, 1.0 if delta.y() >= 0 else -1.0)

    def _axis_for_vector(self, vector: QPointF) -> str:
        return "x" if abs(vector.x()) >= abs(vector.y()) else "y"

    def _perpendicular_axis(self, axis: str) -> str:
        return "y" if axis == "x" else "x"

    def _last_segment_axis(self, points: list[QPointF], fallback: str) -> str:
        if len(points) < 2:
            return fallback
        delta = points[-1] - points[-2]
        if abs(delta.x()) < 0.5 and abs(delta.y()) < 0.5:
            return fallback
        return "x" if abs(delta.x()) >= abs(delta.y()) else "y"

    def _axis_between(self, start: QPointF, end: QPointF) -> str | None:
        if abs(start.x() - end.x()) < 0.5:
            return "y"
        if abs(start.y() - end.y()) < 0.5:
            return "x"
        return None

    def _is_turn(self, previous: QPointF, point: QPointF, next_point: QPointF) -> bool:
        first_axis = self._last_segment_axis([previous, point], "")
        second_axis = self._last_segment_axis([point, next_point], "")
        return bool(first_axis and second_axis and first_axis != second_axis)

    def _distance_to_segment(self, point: QPointF, start: QPointF, end: QPointF) -> float:
        dx = end.x() - start.x()
        dy = end.y() - start.y()
        length_sq = dx * dx + dy * dy
        if length_sq < 0.001:
            return math.hypot(point.x() - start.x(), point.y() - start.y())
        t = ((point.x() - start.x()) * dx + (point.y() - start.y()) * dy) / length_sq
        t = max(0.0, min(1.0, t))
        projection = QPointF(start.x() + dx * t, start.y() + dy * t)
        return math.hypot(point.x() - projection.x(), point.y() - projection.y())

    def _normalized(self, vector: QPointF) -> QPointF | None:
        length = math.hypot(vector.x(), vector.y())
        if length < 0.001:
            return None
        return QPointF(vector.x() / length, vector.y() / length)


class DataCanvasHeaderItem(QGraphicsObject):
    def __init__(self, view: "NodeGraphView") -> None:
        super().__init__()
        self.view = view
        self.setZValue(9)

    def boundingRect(self) -> QRectF:
        return QRectF(0, 0, self.view.horizontal_thumbnail_table_width(), DATA_CANVAS_THUMBNAIL_HEADER_HEIGHT)

    def paint(self, painter: QPainter, _option, _widget=None) -> None:  # type: ignore[override]
        columns = self.view.horizontal_thumbnail_columns()
        if not columns:
            return
        colors = palette(self.view.theme)
        rect = self.boundingRect()
        painter.setRenderHint(QPainter.Antialiasing, False)
        painter.fillRect(rect, QColor(colors["panel"]))

        name_h = 34.0
        type_h = rect.height() - name_h
        x = 0.0
        name_metrics = QFontMetrics(_font(9, True))
        type_metrics = QFontMetrics(_font(8))
        for _field_id, name, data_type, width in columns:
            name_cell = QRectF(x, 0, width, name_h)
            type_cell = QRectF(x, name_h, width, type_h)
            painter.fillRect(name_cell, QColor(colors["panel_alt"]))
            painter.setPen(QPen(QColor(colors["hairline"]), 1))
            painter.drawRect(name_cell)
            painter.drawRect(type_cell)
            painter.setPen(QColor(colors["text"]))
            painter.setFont(_font(9, True))
            painter.drawText(
                name_cell.adjusted(8, 0, -8, 0),
                Qt.AlignLeft | Qt.AlignVCenter | Qt.TextSingleLine,
                name_metrics.elidedText(name or "字段", Qt.ElideRight, max(1, int(width - 16))),
            )
            painter.setPen(QColor(colors["text_muted"]))
            painter.setFont(_font(8))
            painter.drawText(
                type_cell.adjusted(8, 0, -8, 0),
                Qt.AlignLeft | Qt.AlignVCenter | Qt.TextSingleLine,
                type_metrics.elidedText(data_type or "文本", Qt.ElideRight, max(1, int(width - 16))),
            )
            x += width


class NodeGraphView(QGraphicsView):
    selectionChanged = Signal(object, object)
    projectChanged = Signal()
    nodeActivated = Signal(str)
    nodeFolderRequested = Signal(str)
    nodeEditRequested = Signal(str)
    nodeNotesRequested = Signal(str)
    nodeDeleteRequested = Signal(str)
    nodesDeleteRequested = Signal(object)
    groupDeleteRequested = Signal(str)
    groupEditRequested = Signal(str)
    edgeEditRequested = Signal(str)
    edgeDeleteRequested = Signal(str)
    edgeStyleRequested = Signal(str, str)
    edgeCreated = Signal(str, str)
    createNodeRequested = Signal(float, float)
    createCanvasNodeRequested = Signal(float, float)
    createDataCanvasRequested = Signal(float, float)
    createLinkNodeRequested = Signal(float, float, str)
    createGroupRequested = Signal(float, float)
    createNoteRequested = Signal(float, float, object)
    createTemplateNodeRequested = Signal(float, float, str)
    noteEditRequested = Signal(str, object)
    noteDeleteRequested = Signal(str, object)
    dataCanvasLayoutRequested = Signal(str)
    dataCanvasGridRowsRequested = Signal(int)
    dataCanvasTemplateRequested = Signal(str)
    dataCanvasImportRequested = Signal()
    templateManagerRequested = Signal()
    openProjectRequested = Signal()
    aiIterateRequested = Signal()

    def __init__(
        self,
        project: ProjectData | CanvasData,
        theme: str = "dark",
        read_only: bool = False,
        allow_node_drag: bool = False,
        templates: list[NodeTemplate] | None = None,
    ) -> None:
        super().__init__()
        self.project = project
        self.templates = templates if templates is not None else getattr(project, "templates", [])
        self.folder_action_node_ids: set[str] = set()
        self.theme = theme
        self.read_only = read_only
        self.allow_node_drag = allow_node_drag
        self.scene_obj = QGraphicsScene(self)
        self.setScene(self.scene_obj)
        self.node_items: dict[str, NodeItem] = {}
        self.group_items: dict[str, BlueprintGroupItem] = {}
        self.note_items: dict[tuple[str, str], NoteItem] = {}
        self.edge_items: dict[str, EdgeItem] = {}
        self.data_header_item: DataCanvasHeaderItem | None = None
        self.selected_node_ids: set[str] = set()
        self.selected_group_ids: set[str] = set()
        self.selected_node_id: str | None = None
        self.selected_edge_id: str | None = None
        self.selected_note_key: tuple[str, str] | None = None
        self.connecting = False
        self.connection_source: str | None = None
        self.connection_anchor_scene: QPointF | None = None
        self._connection_dragging = False
        self._connection_auto_pan_view_pos: QPoint | None = None
        self._connection_auto_pan_timer = QTimer(self)
        self._connection_auto_pan_timer.setInterval(CONNECTION_AUTO_PAN_INTERVAL_MS)
        self._connection_auto_pan_timer.timeout.connect(self._tick_connection_auto_pan)
        self._pending_drag_edge: tuple[str, str] | None = None
        self._inline_proxy: QGraphicsProxyWidget | None = None
        self._inline_editor: InlineNodeFieldEditor | None = None
        self._inline_field: NodeField | None = None
        self._inline_node: Node | None = None
        self._inline_node_part = ""
        self._inline_original_value = ""
        self.mouse_scene = QPointF()
        self.hover_node_id: str | None = None
        self.snap_guides: list[SnapGuide] = []
        self.rebuilding = False
        self._panning = False
        self._space_panning = False
        self._rubber_selecting = False
        self._rubber_start = QPointF()
        self._rubber_item: QGraphicsPathItem | None = None
        self._moving_group = False
        self._moving_attached_notes = False
        self._pan_cursor_override = False
        self._last_pan = QPoint()
        self._right_press_pos = QPoint()
        self._right_drag_pending = False
        self._suppress_context_menu = False
        self._interaction_preview = False
        self._interaction_preview_timer = QTimer(self)
        self._interaction_preview_timer.setSingleShot(True)
        self._interaction_preview_timer.timeout.connect(self._end_interaction_preview)
        self._cursor_sync_timer = QTimer(self)
        self._cursor_sync_timer.setSingleShot(True)
        self._cursor_sync_timer.timeout.connect(self._apply_scheduled_cursor_sync)
        self._source_pixmap_cache: OrderedDict[str, QPixmap | None] = OrderedDict()
        self._scaled_pixmap_cache: OrderedDict[tuple[str, int, int], QPixmap | None] = OrderedDict()

        self.setRenderHints(QPainter.Antialiasing | QPainter.TextAntialiasing | QPainter.SmoothPixmapTransform)
        self.setViewportUpdateMode(QGraphicsView.FullViewportUpdate)
        self.setTransformationAnchor(QGraphicsView.AnchorUnderMouse)
        self.setResizeAnchor(QGraphicsView.AnchorUnderMouse)
        self.setDragMode(QGraphicsView.NoDrag)
        self.setFrameShape(QGraphicsView.NoFrame)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.setMouseTracking(True)
        self.setAcceptDrops(True)
        self.viewport().setAcceptDrops(True)
        self.setFocusPolicy(Qt.StrongFocus)
        app = QApplication.instance()
        if app:
            app.installEventFilter(self)
        self.rebuild()

    def can_move_nodes(self) -> bool:
        return not self.read_only or self.allow_node_drag

    def can_move_notes(self) -> bool:
        return not self.read_only

    def can_resize_nodes(self) -> bool:
        if self.is_data_canvas():
            canvas = self.active_canvas()
            if canvas is None or canvas.data_layout == "table" or self.uses_horizontal_thumbnail_rows():
                return False
            fields = self._data_canvas_template_fields()
            return self.can_move_nodes() and any(field.has_visual_layout() for field in fields)
        return self.can_move_nodes()

    def can_create_edges(self) -> bool:
        return not self.read_only and not self.is_data_canvas()

    def is_inline_field_editing(self) -> bool:
        return self._inline_proxy is not None

    def start_inline_field_edit(self, item: NodeItem, field: NodeField, local_rect: QRectF | None = None) -> None:
        if self.read_only or field.data_type == "图片":
            return
        self._close_inline_field_editor(commit=True)
        self.select_node(item.node.id)
        rect = item.mapRectToScene(local_rect if local_rect is not None and local_rect.isValid() else item.field_scene_rect(field))
        if local_rect is None or not local_rect.isValid():
            rect = item.field_scene_rect(field)
        if not rect.isValid() or rect.width() <= 0 or rect.height() <= 0:
            return
        editor = InlineNodeFieldEditor()
        editor.setObjectName("inlineCanvasFieldEditor")
        editor.setPlainText(field.value)
        editor.setStyleSheet(
            "QPlainTextEdit#inlineCanvasFieldEditor {"
            "border: 2px solid #0A84FF;"
            "border-radius: 8px;"
            "padding: 6px 10px 6px 6px;"
            f"color: {field.text_color or '#1D1D1F'};"
            f"background: {field.bg_color or '#FFFFFF'};"
            "}"
            "QPlainTextEdit#inlineCanvasFieldEditor QScrollBar:vertical {"
            "background: rgba(0, 0, 0, 18);"
            "border: 0;"
            "border-radius: 8px;"
            "width: 18px;"
            "margin: 4px 3px 4px 3px;"
            "}"
            "QPlainTextEdit#inlineCanvasFieldEditor QScrollBar::handle:vertical {"
            "background: rgba(60, 60, 67, 150);"
            "border-radius: 6px;"
            "min-height: 42px;"
            "}"
            "QPlainTextEdit#inlineCanvasFieldEditor QScrollBar::handle:vertical:hover {"
            "background: rgba(60, 60, 67, 210);"
            "}"
            "QPlainTextEdit#inlineCanvasFieldEditor QScrollBar::add-line:vertical,"
            "QPlainTextEdit#inlineCanvasFieldEditor QScrollBar::sub-line:vertical {"
            "height: 0;"
            "}"
        )
        font = editor.font()
        font.setPointSize(max(8, min(48, field.font_size)))
        editor.setFont(font)
        editor.editingFinished.connect(self._close_inline_field_editor)

        proxy = QGraphicsProxyWidget()
        proxy.setWidget(editor)
        proxy.setZValue(2000)
        proxy.setPos(rect.topLeft())
        proxy.resize(max(46.0, rect.width()), max(32.0, rect.height()))
        self.scene_obj.addItem(proxy)
        self._inline_proxy = proxy
        self._inline_editor = editor
        self._inline_field = field
        self._inline_node = None
        self._inline_node_part = ""
        self._inline_original_value = field.value
        editor.setFocus()
        editor.selectAll()

    def start_inline_node_text_edit(self, item: NodeItem, part: str, local_rect: QRectF | None = None) -> None:
        if self.read_only or part not in {"title", "icon"}:
            return
        self._close_inline_field_editor(commit=True)
        self.select_node(item.node.id)
        if local_rect is None or not local_rect.isValid():
            matches = [rect for candidate, rect in item._editable_node_text_rects() if candidate == part]
            local_rect = matches[0] if matches else QRectF()
        rect = item.mapRectToScene(local_rect)
        if not rect.isValid() or rect.width() <= 0 or rect.height() <= 0:
            return
        initial_value = item.node.title if part == "title" else self._editable_icon_value(item.node)
        editor = InlineNodeFieldEditor(single_line=True)
        editor.setObjectName("inlineCanvasFieldEditor")
        editor.setPlainText(initial_value)
        editor.setStyleSheet(
            "QPlainTextEdit#inlineCanvasFieldEditor {"
            "border: 2px solid #0A84FF;"
            "border-radius: 8px;"
            "padding: 4px 6px;"
            "color: #1D1D1F;"
            "background: #FFFFFF;"
            "}"
        )
        font = editor.font()
        font.setPointSize(12)
        font.setBold(True)
        editor.setFont(font)
        if part == "icon":
            option = editor.document().defaultTextOption()
            option.setAlignment(Qt.AlignCenter)
            editor.document().setDefaultTextOption(option)
        editor.editingFinished.connect(self._close_inline_field_editor)

        proxy = QGraphicsProxyWidget()
        proxy.setWidget(editor)
        proxy.setZValue(2000)
        proxy.setPos(rect.topLeft())
        proxy.resize(max(34.0, rect.width()), max(28.0, rect.height()))
        self.scene_obj.addItem(proxy)
        self._inline_proxy = proxy
        self._inline_editor = editor
        self._inline_field = None
        self._inline_node = item.node
        self._inline_node_part = part
        self._inline_original_value = initial_value
        editor.setFocus()
        editor.selectAll()

    def _editable_icon_value(self, node: Node) -> str:
        if node.icon_from_title:
            return node.display_icon()
        if node.icon:
            return node.icon
        if node.node_type == "画布":
            return "画"
        if node.node_type == "超文本":
            return "链"
        return ""

    def _close_inline_field_editor(self, commit: bool = True) -> None:
        proxy = self._inline_proxy
        editor = self._inline_editor
        field = self._inline_field
        node = self._inline_node
        node_part = self._inline_node_part
        if proxy is None:
            return
        self._inline_proxy = None
        self._inline_editor = None
        self._inline_field = None
        self._inline_node = None
        self._inline_node_part = ""
        self.scene_obj.removeItem(proxy)
        proxy.deleteLater()
        changed = False
        if field is not None:
            new_value = editor.toPlainText() if editor is not None and commit else self._inline_original_value
            if field.value != new_value:
                field.value = new_value
                changed = True
        elif node is not None and node_part:
            raw_value = editor.toPlainText() if editor is not None and commit else self._inline_original_value
            new_value = raw_value.replace("\r", " ").replace("\n", " ").strip()
            if node_part == "title":
                new_value = new_value or self._inline_original_value or "新节点"
                if node.title != new_value:
                    node.title = new_value
                    changed = True
            elif node_part == "icon":
                if node.icon != new_value or node.icon_from_title:
                    node.icon = new_value
                    node.icon_from_title = False
                    changed = True
            if changed and node.id in self.node_items:
                self.node_items[node.id].refresh()
        self._inline_original_value = ""
        self.viewport().update()
        if changed:
            self.projectChanged.emit()

    def close_inline_field_editor_if_outside(self, view_pos: QPoint, *, commit: bool = True) -> bool:
        proxy = self._inline_proxy
        if proxy is None:
            return False
        scene_pos = self.mapToScene(view_pos)
        if proxy.sceneBoundingRect().contains(scene_pos):
            return False
        self._close_inline_field_editor(commit=commit)
        return True

    def can_move_groups(self) -> bool:
        if self.is_data_canvas():
            return False
        return self.can_move_nodes()

    def active_canvas(self) -> CanvasData | None:
        return self.project if isinstance(self.project, CanvasData) else None

    def is_data_canvas(self) -> bool:
        canvas = self.active_canvas()
        return bool(canvas and canvas.is_data_canvas())

    def uses_horizontal_thumbnail_rows(self) -> bool:
        canvas = self.active_canvas()
        return bool(
            canvas
            and canvas.is_data_canvas()
            and canvas.data_layout == "horizontal"
            and canvas.data_row_style == "thumbnail"
        )

    def horizontal_thumbnail_columns(self) -> list[tuple[str, str, str, float]]:
        canvas = self.active_canvas()
        if canvas is None:
            return []
        field_order: list[NodeField] = []
        seen: set[str] = set()
        template = next((item for item in self.templates if item.id == canvas.template_id), None)
        sources = [template.fields if template is not None else []]
        sources.extend(node.fields for node in canvas.nodes)
        for fields in sources:
            for field in fields:
                if field.id in seen:
                    continue
                seen.add(field.id)
                field_order.append(field)

        value_metrics = QFontMetrics(_font(9))
        name_metrics = QFontMetrics(_font(9, True))
        type_metrics = QFontMetrics(_font(8))
        columns: list[tuple[str, str, str, float]] = []
        for field in field_order:
            value_widths: list[int] = []
            for node in canvas.nodes:
                node_field = next((candidate for candidate in node.fields if candidate.id == field.id), None)
                if node_field is None:
                    continue
                value = node_field.image_path.replace("\\", "/").rsplit("/", 1)[-1] if node_field.data_type == "图片" else node_field.value
                value_widths.append(value_metrics.horizontalAdvance(value or " "))
            width = max(
                118.0,
                min(
                    260.0,
                    float(
                        max(
                            [name_metrics.horizontalAdvance(field.name or "字段"), type_metrics.horizontalAdvance(field.data_type or "文本")]
                            + value_widths
                            + [84]
                        )
                        + 24
                    ),
                ),
            )
            columns.append((field.id, field.name or "字段", field.data_type or "文本", width))
        return columns

    def _data_canvas_template(self) -> NodeTemplate | None:
        canvas = self.active_canvas()
        if canvas is None or not canvas.template_id:
            return None
        return next((template for template in self.templates if template.id == canvas.template_id), None)

    def _data_canvas_template_fields(self) -> list[NodeField]:
        template = self._data_canvas_template()
        if template is not None:
            return template.fields
        canvas = self.active_canvas()
        if canvas is None or not canvas.nodes:
            return []
        return canvas.nodes[0].fields

    def horizontal_thumbnail_table_width(self) -> float:
        return max(NODE_MIN_WIDTH, sum(width for _field_id, _name, _data_type, width in self.horizontal_thumbnail_columns()))

    def eventFilter(self, _watched, event) -> bool:  # type: ignore[override]
        if event.type() in (QEvent.ApplicationDeactivate, QEvent.WindowDeactivate):
            if self._space_panning or self._panning or self._right_drag_pending:
                self._space_panning = False
                self._panning = False
                self._right_drag_pending = False
                self._end_interaction_preview()
                self._refresh_interaction_cursor()
            self._stop_connection_auto_pan()
            return False
        if event.type() not in (QEvent.KeyPress, QEvent.KeyRelease):
            return False
        if event.key() != Qt.Key_Space or event.isAutoRepeat():
            return False
        if not self._should_handle_space_pan():
            return False
        if event.type() == QEvent.KeyPress:
            self._space_panning = True
            self._refresh_interaction_cursor()
            event.accept()
            return True
        self._space_panning = False
        self._refresh_interaction_cursor()
        event.accept()
        return True

    def showEvent(self, event) -> None:  # type: ignore[override]
        super().showEvent(event)
        self._schedule_cursor_sync()

    def focusInEvent(self, event) -> None:  # type: ignore[override]
        super().focusInEvent(event)
        self._schedule_cursor_sync()

    def viewportEvent(self, event) -> bool:  # type: ignore[override]
        handled = super().viewportEvent(event)
        if event.type() in (QEvent.Enter, QEvent.Show, QEvent.FocusIn):
            self._schedule_cursor_sync()
        return handled

    def _should_handle_space_pan(self) -> bool:
        if QApplication.activeModalWidget():
            return False
        if not self.isVisible() or not self.window().isActiveWindow():
            return False
        focused = QApplication.focusWidget()
        return self.hasFocus() or self.viewport().underMouse() or bool(focused and self.isAncestorOf(focused))

    def _refresh_interaction_cursor(self) -> None:
        if self._panning:
            self._set_pan_cursor(Qt.ClosedHandCursor)
            return
        if self._space_panning:
            self._set_pan_cursor(Qt.OpenHandCursor)
            return
        self._clear_pan_cursor_override()
        cursor = QCursor(Qt.CrossCursor if self.connecting else Qt.ArrowCursor)
        self.setCursor(cursor)
        self.viewport().setCursor(cursor)

    def sync_interaction_cursor(self) -> None:
        self.hover_node_id = None
        self._refresh_interaction_cursor()
        self.viewport().update()

    def _schedule_cursor_sync(self) -> None:
        if not self._cursor_sync_timer.isActive():
            self._cursor_sync_timer.start(0)

    def _apply_scheduled_cursor_sync(self) -> None:
        if not self.isVisible():
            return
        self.sync_interaction_cursor()

    def _set_pan_cursor(self, shape: Qt.CursorShape) -> None:
        cursor = QCursor(shape)
        self.setCursor(cursor)
        self.viewport().setCursor(cursor)
        self._pan_cursor_override = True

    def _clear_pan_cursor_override(self) -> None:
        if not self._pan_cursor_override:
            return
        self._pan_cursor_override = False

    def set_theme(self, theme: str) -> None:
        self.theme = theme
        for item in self.group_items.values():
            item.update()
        for item in self.node_items.values():
            item.update()
        for item in self.note_items.values():
            item.update()
        for item in self.edge_items.values():
            item.update()
        self.viewport().update()

    def set_project(self, project: ProjectData | CanvasData) -> None:
        self.project = project
        self.clear_selection()
        self._clear_pixmap_caches()
        self.rebuild()

    def set_folder_action_node_ids(self, node_ids: set[str]) -> None:
        self.folder_action_node_ids = set(node_ids)

    def set_templates(self, templates: list[NodeTemplate]) -> None:
        self.templates = templates

    def rebuild(self) -> None:
        self.rebuilding = True
        self.scene_obj.clear()
        self.group_items.clear()
        self.node_items.clear()
        self.note_items.clear()
        self.edge_items.clear()
        self.data_header_item = None
        groups = getattr(self.project, "groups", [])
        self.selected_node_ids &= {node.id for node in self.project.nodes}
        self.selected_group_ids &= {group.id for group in groups}
        if self.selected_note_key and not self._note_for_key(self.selected_note_key):
            self.selected_note_key = None
        for group in groups:
            item = BlueprintGroupItem(group, self)
            self.scene_obj.addItem(item)
            item.setSelected(group.id in self.selected_group_ids)
            self.group_items[group.id] = item
        for node in self.project.nodes:
            item = NodeItem(node, self)
            self.scene_obj.addItem(item)
            item.setSelected(node.id in self.selected_node_ids)
            self.node_items[node.id] = item
        self._rebuild_note_items()
        if self.is_data_canvas():
            canvas = self.active_canvas()
            if canvas is not None and layout_data_canvas(canvas):
                for node in canvas.nodes:
                    item = self.node_items.get(node.id)
                    if item is not None:
                        item.setPos(node.x, node.y)
        if self.uses_horizontal_thumbnail_rows():
            self.data_header_item = DataCanvasHeaderItem(self)
            self.data_header_item.setPos(DATA_CANVAS_MARGIN_X, DATA_CANVAS_MARGIN_Y)
            self.scene_obj.addItem(self.data_header_item)
        for edge in self.project.valid_edges():
            source = self._endpoint_item(edge.source)
            target = self._endpoint_item(edge.target)
            if source and target:
                edge_item = EdgeItem(edge, source, target, self)
                self.scene_obj.addItem(edge_item)
                self.edge_items[edge.id] = edge_item
        self.rebuilding = False
        self.refresh_note_visibility()
        self._update_scene_rect()
        self._refresh_interaction_cursor()

    def _update_scene_rect(self) -> None:
        if self.is_data_canvas():
            item_rects = [
                item.sceneBoundingRect().adjusted(-200, -120, 200, 120)
                for item in [*self.group_items.values(), *self.node_items.values(), *self.note_items.values()]
            ]
            if self.data_header_item is not None:
                item_rects.append(self.data_header_item.sceneBoundingRect().adjusted(-200, -120, 200, 120))
            if not item_rects:
                self.scene_obj.setSceneRect(QRectF(-120, -120, 1280, 960))
                return
            rect = item_rects[0]
            for item_rect in item_rects[1:]:
                rect = rect.united(item_rect)
            self.scene_obj.setSceneRect(rect)
            return
        rect = QRectF(-SCENE_EXTENT, -SCENE_EXTENT, SCENE_EXTENT * 2, SCENE_EXTENT * 2)
        for item in self.group_items.values():
            rect = rect.united(item.sceneBoundingRect().adjusted(-SCENE_MARGIN, -SCENE_MARGIN, SCENE_MARGIN, SCENE_MARGIN))
        for item in self.node_items.values():
            rect = rect.united(item.sceneBoundingRect().adjusted(-SCENE_MARGIN, -SCENE_MARGIN, SCENE_MARGIN, SCENE_MARGIN))
        for item in self.note_items.values():
            rect = rect.united(item.sceneBoundingRect().adjusted(SCENE_MARGIN * -0.05, SCENE_MARGIN * -0.05, SCENE_MARGIN * 0.05, SCENE_MARGIN * 0.05))
        self.scene_obj.setSceneRect(rect)

    def _endpoint_item(self, endpoint_id: str | None) -> QGraphicsItem | None:
        if not endpoint_id:
            return None
        return self.node_items.get(endpoint_id) or self.group_items.get(endpoint_id)

    def _connectable_endpoint_item(self, endpoint_id: str | None) -> QGraphicsItem | None:
        return self._endpoint_item(endpoint_id) or self._note_item_by_id(endpoint_id or "")

    def center_world(self) -> QPointF:
        return self.mapToScene(self.viewport().rect().center())

    def reset_view(self) -> None:
        self.resetTransform()
        if self.is_data_canvas():
            self.centerOn(self.sceneRect().center())
        else:
            self.centerOn(0, 0)
        self.viewport().update()

    def is_interaction_preview(self) -> bool:
        return self._interaction_preview

    def _begin_interaction_preview(self) -> None:
        self._interaction_preview_timer.start(INTERACTION_PREVIEW_DELAY_MS)
        if self._interaction_preview:
            return
        self._interaction_preview = True
        self.viewport().update()

    def _end_interaction_preview(self) -> None:
        self._interaction_preview_timer.stop()
        if not self._interaction_preview:
            return
        self._interaction_preview = False
        self.viewport().update()

    def _clear_pixmap_caches(self) -> None:
        self._source_pixmap_cache.clear()
        self._scaled_pixmap_cache.clear()

    def _load_source_pixmap(self, path: str) -> QPixmap | None:
        pixmap = QPixmap(path)
        if pixmap.isNull():
            return None
        return pixmap

    def _source_image_pixmap(self, path: str) -> QPixmap | None:
        cache_key = path.strip()
        if not cache_key:
            return None
        cached = self._source_pixmap_cache.get(cache_key, _MISSING_PIXMAP)
        if cached is not _MISSING_PIXMAP:
            self._source_pixmap_cache.move_to_end(cache_key)
            return cached
        pixmap = self._load_source_pixmap(cache_key)
        self._remember_pixmap(self._source_pixmap_cache, cache_key, pixmap, IMAGE_SOURCE_CACHE_LIMIT)
        return pixmap

    def _scaled_image_pixmap(self, path: str, width: int, height: int) -> QPixmap | None:
        source = self._source_image_pixmap(path)
        if source is None:
            return None
        cache_key = (path.strip(), max(1, width), max(1, height))
        cached = self._scaled_pixmap_cache.get(cache_key, _MISSING_PIXMAP)
        if cached is not _MISSING_PIXMAP:
            self._scaled_pixmap_cache.move_to_end(cache_key)
            return cached
        pixmap = source.scaled(cache_key[1], cache_key[2], Qt.KeepAspectRatio, Qt.SmoothTransformation)
        self._remember_pixmap(self._scaled_pixmap_cache, cache_key, pixmap, IMAGE_SCALED_CACHE_LIMIT)
        return pixmap

    def _remember_pixmap(self, cache: OrderedDict, key, pixmap, limit: int) -> None:
        cache[key] = pixmap
        cache.move_to_end(key)
        while len(cache) > limit:
            cache.popitem(last=False)

    def start_connection(self, source_id: str | None) -> None:
        if not self.can_create_edges():
            return
        if not source_id:
            return
        if not self._connectable_endpoint_item(source_id):
            return
        self.connecting = True
        self.connection_source = source_id
        self.connection_anchor_scene = None
        self._connection_dragging = False
        if source_id in self.node_items:
            self.select_node(source_id)
        elif source_id in self.group_items:
            self.select_group(source_id)
        self._refresh_interaction_cursor()
        self.viewport().update()

    def begin_connection_drag(self, source_id: str, anchor_scene: QPointF) -> None:
        if not self.can_create_edges():
            return
        if not self._connectable_endpoint_item(source_id):
            return
        self.connecting = True
        self.connection_source = source_id
        self.connection_anchor_scene = QPointF(anchor_scene)
        self._connection_dragging = True
        self.mouse_scene = QPointF(anchor_scene)
        self._connection_auto_pan_view_pos = self.mapFromScene(anchor_scene)
        if source_id in self.node_items:
            self.select_node(source_id)
        elif source_id in self.group_items:
            self.select_group(source_id)
        self._refresh_interaction_cursor()
        self.viewport().update()

    def is_connection_dragging_from(self, source_id: str) -> bool:
        return bool(self.connecting and self._connection_dragging and self.connection_source == source_id)

    def update_connection_drag(self, scene_pos: QPointF) -> None:
        if not self._connection_dragging:
            return
        self.mouse_scene = QPointF(scene_pos)
        self._update_connection_auto_pan(scene_pos)
        self.viewport().update()

    def finish_connection_drag(self, scene_pos: QPointF) -> None:
        if not self._connection_dragging:
            return
        self._stop_connection_auto_pan()
        self.mouse_scene = QPointF(scene_pos)
        source = self.connection_source
        target_id = self._endpoint_id_at(self.mapFromScene(scene_pos), include_group_body=True, include_notes=True)
        self.cancel_connection()
        if source and target_id and target_id != source:
            if not self._route_note_connection(source, target_id):
                self._pending_drag_edge = (source, target_id)
                QTimer.singleShot(0, self._emit_pending_drag_edge)

    def _emit_pending_drag_edge(self) -> None:
        edge = self._pending_drag_edge
        self._pending_drag_edge = None
        if edge is None:
            return
        source, target = edge
        self.edgeCreated.emit(source, target)

    def cancel_connection(self) -> None:
        self.connecting = False
        self.connection_source = None
        self.connection_anchor_scene = None
        self._connection_dragging = False
        self._stop_connection_auto_pan()
        self._refresh_interaction_cursor()
        self.viewport().update()

    def _update_connection_auto_pan(self, scene_pos: QPointF) -> None:
        self._connection_auto_pan_view_pos = self.mapFromScene(scene_pos)
        if self._connection_auto_pan_delta(self._connection_auto_pan_view_pos).isNull():
            self._stop_connection_auto_pan()
            return
        if not self._connection_auto_pan_timer.isActive():
            self._connection_auto_pan_timer.start()

    def _stop_connection_auto_pan(self) -> None:
        if self._connection_auto_pan_timer.isActive():
            self._connection_auto_pan_timer.stop()
        self._connection_auto_pan_view_pos = None

    def _tick_connection_auto_pan(self) -> None:
        if not self._connection_dragging or self._connection_auto_pan_view_pos is None:
            self._stop_connection_auto_pan()
            return
        delta = self._connection_auto_pan_delta(self._connection_auto_pan_view_pos)
        if delta.isNull():
            self._stop_connection_auto_pan()
            return
        horizontal = self.horizontalScrollBar()
        vertical = self.verticalScrollBar()
        old_h = horizontal.value()
        old_v = vertical.value()
        horizontal.setValue(old_h + delta.x())
        vertical.setValue(old_v + delta.y())
        if horizontal.value() == old_h and vertical.value() == old_v:
            self._stop_connection_auto_pan()
            return
        self.mouse_scene = self.mapToScene(self._connection_auto_pan_view_pos)
        self.viewport().update()

    def _connection_auto_pan_delta(self, view_pos: QPoint) -> QPoint:
        rect = self.viewport().rect()
        return QPoint(
            self._connection_auto_pan_axis_delta(view_pos.x(), rect.left(), rect.right()),
            self._connection_auto_pan_axis_delta(view_pos.y(), rect.top(), rect.bottom()),
        )

    def _connection_auto_pan_axis_delta(self, value: int, minimum: int, maximum: int) -> int:
        low_edge = minimum + CONNECTION_AUTO_PAN_MARGIN
        high_edge = maximum - CONNECTION_AUTO_PAN_MARGIN
        distance = 0
        direction = 0
        if value < low_edge:
            distance = low_edge - value
            direction = -1
        elif value > high_edge:
            distance = value - high_edge
            direction = 1
        if not direction:
            return 0
        ratio = min(1.0, max(0.0, distance / CONNECTION_AUTO_PAN_MARGIN))
        step = CONNECTION_AUTO_PAN_MIN_STEP + int((CONNECTION_AUTO_PAN_MAX_STEP - CONNECTION_AUTO_PAN_MIN_STEP) * ratio)
        return direction * step

    def select_node(self, node_id: str | None) -> None:
        self.selected_node_id = node_id
        self.selected_edge_id = None
        self.selected_note_key = None
        self.selected_node_ids = {node_id} if node_id else set()
        self.selected_group_ids.clear()
        for item_id, item in self.node_items.items():
            item.setSelected(item_id == node_id)
        for item in self.group_items.values():
            item.setSelected(False)
        for item in self.note_items.values():
            item.setSelected(False)
        for item in self.edge_items.values():
            item.setSelected(False)
        self.refresh_note_visibility()
        self.selectionChanged.emit(node_id, None)
        self.viewport().update()

    def select_nodes(self, node_ids: set[str]) -> None:
        self.selected_node_ids = set(node_ids)
        self.selected_node_id = next(iter(self.selected_node_ids), None)
        self.selected_edge_id = None
        self.selected_note_key = None
        self.selected_group_ids.clear()
        for item_id, item in self.node_items.items():
            item.setSelected(item_id in self.selected_node_ids)
        for item in self.group_items.values():
            item.setSelected(False)
        for item in self.note_items.values():
            item.setSelected(False)
        for item in self.edge_items.values():
            item.setSelected(False)
        self.refresh_note_visibility()
        self.selectionChanged.emit(self.selected_node_id, None)
        self.viewport().update()

    def toggle_node_selection(self, node_id: str) -> None:
        selected = set(self.selected_node_ids)
        if node_id in selected:
            selected.remove(node_id)
        else:
            selected.add(node_id)
        self.select_nodes(selected)

    def has_multi_node_selection(self) -> bool:
        return len(self.selected_node_ids) > 1

    def select_group(self, group_id: str | None) -> None:
        self.selected_node_id = None
        self.selected_edge_id = None
        self.selected_note_key = None
        self.selected_node_ids.clear()
        self.selected_group_ids = {group_id} if group_id else set()
        for item in self.node_items.values():
            item.setSelected(False)
        for item_id, item in self.group_items.items():
            item.setSelected(item_id == group_id)
        for item in self.note_items.values():
            item.setSelected(False)
        for item in self.edge_items.values():
            item.setSelected(False)
        self.refresh_note_visibility()
        self.selectionChanged.emit(None, None)
        self.viewport().update()

    def select_edge(self, edge_id: str | None) -> None:
        self.selected_node_id = None
        self.selected_edge_id = edge_id
        self.selected_note_key = None
        self.selected_node_ids.clear()
        self.selected_group_ids.clear()
        for item in self.node_items.values():
            item.setSelected(False)
        for item in self.group_items.values():
            item.setSelected(False)
        for item in self.note_items.values():
            item.setSelected(False)
        for item_id, item in self.edge_items.items():
            item.setSelected(item_id == edge_id)
        self.refresh_note_visibility()
        self.selectionChanged.emit(None, edge_id)
        self.viewport().update()

    def select_note(self, note_id: str, owner_node_id: str = "") -> None:
        key = (owner_node_id or "", note_id)
        self.selected_node_id = owner_node_id or None
        self.selected_edge_id = None
        self.selected_note_key = key
        self.selected_node_ids = {owner_node_id} if owner_node_id else set()
        self.selected_group_ids.clear()
        for item_id, item in self.node_items.items():
            item.setSelected(bool(owner_node_id and item_id == owner_node_id))
        for item in self.group_items.values():
            item.setSelected(False)
        for item_key, item in self.note_items.items():
            item.setSelected(item_key == key)
        for item in self.edge_items.values():
            item.setSelected(False)
        self.refresh_note_visibility()
        self.selectionChanged.emit(self.selected_node_id, None)
        self.viewport().update()

    def clear_selection(self) -> None:
        self.selected_node_id = None
        self.selected_edge_id = None
        self.selected_note_key = None
        self.selected_node_ids.clear()
        self.selected_group_ids.clear()
        for item in self.node_items.values():
            item.setSelected(False)
        for item in self.group_items.values():
            item.setSelected(False)
        for item in self.note_items.values():
            item.setSelected(False)
        for item in self.edge_items.values():
            item.setSelected(False)
        self.refresh_note_visibility()
        self.selectionChanged.emit(None, None)
        self.viewport().update()

    def update_edges_for_node(self, node_id: str) -> None:
        self.update_edges_for_endpoint(node_id)

    def update_edges_for_endpoint(self, endpoint_id: str) -> None:
        for edge_item in self.edge_items.values():
            if edge_item.edge.source == endpoint_id or edge_item.edge.target == endpoint_id:
                edge_item.update_path()
        self._update_scene_rect()

    def move_nodes_in_group(self, group_id: str, delta: QPointF) -> None:
        self._moving_group = True
        try:
            for node in self.project.nodes:
                if node.group_id != group_id:
                    continue
                item = self.node_items.get(node.id)
                if not item:
                    continue
                item.setPos(item.pos() + delta)
                node.x = item.pos().x()
                node.y = item.pos().y()
                self.update_edges_for_node(node.id)
        finally:
            self._moving_group = False

    def _move_attached_notes(self, node_id: str, delta: QPointF) -> None:
        self._moving_attached_notes = True
        try:
            for note in self._find_node_notes(node_id):
                key = (node_id, note.id)
                item = self.note_items.get(key)
                if item is None:
                    continue
                item.setPos(item.pos() + delta)
                note.x = item.pos().x()
                note.y = item.pos().y()
        finally:
            self._moving_attached_notes = False

    def _find_node_notes(self, node_id: str) -> list[DesignNote]:
        node = self.project.find_node(node_id)
        return node.notes if node is not None else []

    def refresh_group_membership(self) -> None:
        if self.read_only:
            return
        changed = False
        for node in self.project.nodes:
            item = self.node_items.get(node.id)
            if not item:
                continue
            new_group_id = self._containing_group_id(item.sceneBoundingRect().center())
            if node.group_id != new_group_id:
                node.group_id = new_group_id
                changed = True
        if changed:
            self.viewport().update()

    def resize_data_canvas_template(self, node_id: str, target_width: float, target_height: float) -> bool:
        canvas = self.active_canvas()
        template = self._data_canvas_template()
        if canvas is None or not canvas.is_data_canvas() or template is None:
            return False
        if canvas.data_layout == "table" or self.uses_horizontal_thumbnail_rows():
            return False
        if canvas.find_node(node_id) is None:
            return False
        visual_fields = [field for field in template.fields if field.has_visual_layout()]
        if not visual_fields:
            return False

        content_width = max([field.x + field.width for field in visual_fields] + [1.0])
        content_height = max([field.y + field.height for field in visual_fields] + [1.0])
        target_content_width = max(1.0, target_width - 34.0)
        target_content_height = max(1.0, target_height - HEADER_HEIGHT - 24.0)
        scale_x = target_content_width / max(1.0, content_width)
        scale_y = target_content_height / max(1.0, content_height)
        if abs(scale_x - 1.0) < 0.001 and abs(scale_y - 1.0) < 0.001:
            return False

        font_scale = max(0.25, min(4.0, min(scale_x, scale_y)))
        for field in template.fields:
            if not field.has_visual_layout():
                continue
            field.x = max(0.0, field.x * scale_x)
            field.y = max(0.0, field.y * scale_y)
            field.width = max(44.0, field.width * scale_x)
            field.height = max(34.0, field.height * scale_y)
            field.font_size = max(8, min(48, int(round(field.font_size * font_scale))))

        node_width = max(NODE_MIN_WIDTH, max([field.x + field.width for field in visual_fields] + [NODE_DEFAULT_WIDTH]) + 34.0)
        node_height = max(NODE_MIN_HEIGHT, HEADER_HEIGHT + max([field.y + field.height for field in visual_fields] + [120.0]) + 24.0)
        for node in canvas.nodes:
            apply_template_to_node(node, template, preserve_values=True, force_lock=True)
            node.width = node_width
            node.height = node_height
        layout_data_canvas(canvas)
        self._sync_data_canvas_items()
        self.select_node(node_id)
        return True

    def commit_data_canvas_node_reorder(self, node_id: str) -> bool:
        canvas = self.active_canvas()
        if canvas is None or not canvas.is_data_canvas():
            return False
        node = canvas.find_node(node_id)
        if node is None:
            return False
        changed = reorder_data_canvas_node(canvas, node_id, node.x, node.y)
        self._sync_data_canvas_items()
        return changed

    def _sync_data_canvas_items(self) -> None:
        if not self.is_data_canvas():
            return
        self.rebuilding = True
        try:
            for node in self.project.nodes:
                item = self.node_items.get(node.id)
                if item is None:
                    continue
                item.refresh()
                item.setPos(node.x, node.y)
                item.update()
            for edge_item in self.edge_items.values():
                edge_item.update_path()
        finally:
            self.rebuilding = False
        self._update_scene_rect()
        self.viewport().update()

    def _rebuild_note_items(self) -> None:
        for note in self._canvas_notes():
            self._add_note_item(note, "")
        for node in self.project.nodes:
            for note in node.notes:
                self._add_note_item(note, node.id)

    def _add_note_item(self, note: DesignNote, owner_node_id: str) -> NoteItem:
        item = NoteItem(note, self, owner_node_id)
        self.scene_obj.addItem(item)
        key = (owner_node_id, note.id)
        item.setSelected(self.selected_note_key == key)
        self.note_items[key] = item
        return item

    def _route_note_connection(self, source: str, target: str) -> bool:
        note_item = self._note_item_by_id(source)
        node_item = self.node_items.get(target)
        if note_item is None:
            note_item = self._note_item_by_id(target)
            node_item = self.node_items.get(source)
        if note_item is None or node_item is None:
            return False
        if self._move_note_to_node(note_item.note.id, node_item.node.id):
            self.projectChanged.emit()
            return True
        return False

    def _move_note_to_node(self, note_id: str, node_id: str) -> bool:
        note, owner_id = self._find_note_record(note_id)
        target_node = self.project.find_node(node_id)
        if note is None or target_node is None:
            return False
        if owner_id == node_id:
            return False
        if owner_id:
            source_node = self.project.find_node(owner_id)
            if source_node is not None:
                source_node.notes[:] = [candidate for candidate in source_node.notes if candidate.id != note_id]
        else:
            canvas_notes = self._canvas_notes()
            canvas_notes[:] = [candidate for candidate in canvas_notes if candidate.id != note_id]
        note.pinned = True
        target_node.notes.append(note)
        self.rebuild()
        self.select_note(note.id, target_node.id)
        return True

    def _find_note_record(self, note_id: str) -> tuple[DesignNote | None, str]:
        canvas_note = next((note for note in self._canvas_notes() if note.id == note_id), None)
        if canvas_note is not None:
            return canvas_note, ""
        for node in self.project.nodes:
            note = next((candidate for candidate in node.notes if candidate.id == note_id), None)
            if note is not None:
                return note, node.id
        return None, ""

    def _canvas_notes(self) -> list[DesignNote]:
        canvas = self.active_canvas()
        return canvas.notes if canvas is not None else []

    def _note_for_key(self, key: tuple[str, str]) -> DesignNote | None:
        owner_node_id, note_id = key
        if owner_node_id:
            node = next((candidate for candidate in self.project.nodes if candidate.id == owner_node_id), None)
            if node is None:
                return None
            return next((note for note in node.notes if note.id == note_id), None)
        return next((note for note in self._canvas_notes() if note.id == note_id), None)

    def _note_item_by_id(self, note_id: str) -> NoteItem | None:
        return next((item for (_owner, item_note_id), item in self.note_items.items() if item_note_id == note_id), None)

    def refresh_note_visibility(self) -> None:
        selected_node_id = self.selected_node_id if len(self.selected_node_ids) == 1 else None
        for (owner_node_id, _note_id), item in self.note_items.items():
            item.setVisible(not owner_node_id or owner_node_id == selected_node_id)

    def group_id_at_scene_pos(self, point: QPointF) -> str:
        return self._containing_group_id(point)

    def _containing_group_id(self, point: QPointF) -> str:
        best: tuple[float, str] | None = None
        for group_id, item in self.group_items.items():
            rect = item.sceneBoundingRect()
            if not rect.contains(point):
                continue
            area = rect.width() * rect.height()
            if best is None or area < best[0]:
                best = (area, group_id)
        return best[1] if best else ""

    def snap_position(self, moving: QGraphicsItem, target: QPointF) -> QPointF:
        scale = max(0.001, self.transform().m11())
        snapped = QPointF(target)
        guides: list[SnapGuide] = []
        target_rect = self._snap_rect_for_item(moving, target)

        x_result = self._best_grid_snap(target.x(), "x", scale)
        y_result = self._best_grid_snap(target.y(), "y", scale)
        align_x = self._best_align_snap(moving, target_rect, "x", scale)
        align_y = self._best_align_snap(moving, target_rect, "y", scale)
        if align_x and (not x_result or align_x[1] <= x_result[1]):
            x_result = align_x
        if align_y and (not y_result or align_y[1] <= y_result[1]):
            y_result = align_y
        if x_result:
            snapped.setX(target.x() + x_result[0])
            guides.append(x_result[2])
        if y_result:
            snapped.setY(target.y() + y_result[0])
            guides.append(y_result[2])
        self.snap_guides = guides
        self.viewport().update()
        return snapped

    def _best_grid_snap(self, value: float, axis: str, scale: float) -> tuple[float, float, SnapGuide] | None:
        grid_value = round(value / SNAP_UNIT) * SNAP_UNIT
        distance = abs(grid_value - value) * scale
        if distance > GRID_SNAP_THRESHOLD:
            return None
        return (
            grid_value - value,
            distance,
            SnapGuide(axis, grid_value, f"{axis.upper()} {grid_value:.0f}", "grid"),
        )

    def _best_align_snap(
        self, moving: QGraphicsItem, target_rect: QRectF, axis: str, scale: float
    ) -> tuple[float, float, SnapGuide] | None:
        moving_values = self._snap_axis_values(target_rect, axis)
        best: tuple[float, float, SnapGuide] | None = None
        for other in self._alignment_targets_for(moving):
            if other is moving:
                continue
            other_rect = other.sceneBoundingRect()
            other_values = self._snap_axis_values(other_rect, axis)
            if axis == "x":
                if (
                    self._screen_gap(
                        target_rect.top(),
                        target_rect.bottom(),
                        other_rect.top(),
                        other_rect.bottom(),
                        scale,
                    )
                    > ALIGN_PROXIMITY
                ):
                    continue
                for moving_label, moving_value in moving_values.items():
                    for other_label, other_value in other_values.items():
                        distance = abs(other_value - moving_value) * scale
                        if distance <= ALIGN_SNAP_THRESHOLD:
                            delta = other_value - moving_value
                            guide = SnapGuide("x", other_value, f"X 对齐 {moving_label}/{other_label}", "align")
                            if best is None or distance < best[1]:
                                best = (delta, distance, guide)
            else:
                if (
                    self._screen_gap(
                        target_rect.left(),
                        target_rect.right(),
                        other_rect.left(),
                        other_rect.right(),
                        scale,
                    )
                    > ALIGN_PROXIMITY
                ):
                    continue
                for moving_label, moving_value in moving_values.items():
                    for other_label, other_value in other_values.items():
                        distance = abs(other_value - moving_value) * scale
                        if distance <= ALIGN_SNAP_THRESHOLD:
                            delta = other_value - moving_value
                            guide = SnapGuide("y", other_value, f"Y 对齐 {moving_label}/{other_label}", "align")
                            if best is None or distance < best[1]:
                                best = (delta, distance, guide)
        return best

    def _snap_rect_for_item(self, item: QGraphicsItem, target: QPointF) -> QRectF:
        rect = item.boundingRect()
        return QRectF(target.x(), target.y(), rect.width(), rect.height())

    def _snap_axis_values(self, rect: QRectF, axis: str) -> dict[str, float]:
        if axis == "x":
            return {"左": rect.left(), "中": rect.center().x(), "右": rect.right()}
        return {"上": rect.top(), "中": rect.center().y(), "下": rect.bottom()}

    def _alignment_targets_for(self, moving: QGraphicsItem) -> list[QGraphicsItem]:
        if isinstance(moving, NodeItem):
            return [*self.node_items.values()]
        if isinstance(moving, NoteItem):
            return [*self.node_items.values(), *self.note_items.values()]

        targets: list[QGraphicsItem] = [*self.node_items.values(), *self.group_items.values(), *self.note_items.values()]
        if isinstance(moving, BlueprintGroupItem):
            return [
                item
                for item in targets
                if not (isinstance(item, NodeItem) and item.node.group_id == moving.group.id)
            ]
        return targets

    def _screen_gap(self, start_a: float, end_a: float, start_b: float, end_b: float, scale: float) -> float:
        if end_a < start_b:
            return (start_b - end_a) * scale
        if end_b < start_a:
            return (start_a - end_b) * scale
        return 0.0

    def drawBackground(self, painter: QPainter, rect: QRectF) -> None:  # type: ignore[override]
        colors = palette(self.theme)
        painter.fillRect(rect, QColor(colors["canvas"]))
        step = 80.0
        zoom = max(0.001, self.transform().m11())
        if step * zoom < 24:
            step *= 4
        if step * zoom > 180:
            step /= 2
        left = math.floor(rect.left() / step) * step
        top = math.floor(rect.top() / step) * step
        pen_minor = QPen(QColor(colors["grid"]), 0)
        pen_major = QPen(QColor(colors["grid_major"]), 0)
        x = left
        index = 0
        while x <= rect.right():
            painter.setPen(pen_major if index % 5 == 0 else pen_minor)
            painter.drawLine(QPointF(x, rect.top()), QPointF(x, rect.bottom()))
            x += step
            index += 1
        y = top
        index = 0
        while y <= rect.bottom():
            painter.setPen(pen_major if index % 5 == 0 else pen_minor)
            painter.drawLine(QPointF(rect.left(), y), QPointF(rect.right(), y))
            y += step
            index += 1
        painter.setPen(QPen(QColor(colors["axis"]), 0))
        painter.drawLine(QPointF(0, rect.top()), QPointF(0, rect.bottom()))
        painter.drawLine(QPointF(rect.left(), 0), QPointF(rect.right(), 0))

    def drawForeground(self, painter: QPainter, rect: QRectF) -> None:  # type: ignore[override]
        colors = palette(self.theme)
        for guide in self.snap_guides:
            color = QColor(colors["blue"] if guide.kind == "align" else colors["warning"])
            painter.setPen(QPen(color, 0, Qt.DashLine))
            if guide.axis == "x":
                painter.drawLine(QPointF(guide.value, rect.top()), QPointF(guide.value, rect.bottom()))
                painter.drawText(QPointF(guide.value + 8, rect.top() + 24), guide.label)
            else:
                painter.drawLine(QPointF(rect.left(), guide.value), QPointF(rect.right(), guide.value))
                painter.drawText(QPointF(rect.left() + 18, guide.value - 8), guide.label)

        if self.connecting and self.connection_source:
            source = self._connectable_endpoint_item(self.connection_source)
            if source:
                start = self.connection_anchor_scene or self._connection_start(source.sceneBoundingRect(), self.mouse_scene)
                path = self._preview_path(start, self.mouse_scene)
                painter.setPen(QPen(QColor(colors["edge"]), 2.2, Qt.DashLine, Qt.RoundCap, Qt.RoundJoin))
                painter.drawPath(path)
        self._paint_selected_node_notes(painter, colors)

    def _paint_selected_node_notes(self, painter: QPainter, colors: dict[str, str]) -> None:
        if len(self.selected_node_ids) != 1 or not self.selected_node_id:
            return
        item = self.node_items.get(self.selected_node_id)
        if item is None or not item.node.notes:
            return
        if any(note.pinned for note in item.node.notes):
            return
        text = self._selected_note_preview_text(item.node)
        if not text:
            return
        node_rect = item.sceneBoundingRect()
        zoom = max(0.18, self.transform().m11())
        width = 270.0 / zoom
        height = 118.0 / zoom
        gap = 14.0 / zoom
        bubble = QRectF(node_rect.right() + gap, node_rect.top(), width, height)
        if bubble.right() > self.mapToScene(self.viewport().rect()).boundingRect().right() - gap:
            bubble.moveRight(node_rect.left() - gap)
        path = QPainterPath()
        radius = 10.0 / zoom
        path.addRoundedRect(bubble, radius, radius)
        fill = QColor("#29261B" if self.theme == "dark" else "#FFF7D6")
        fill.setAlpha(244)
        border = QColor("#746B45" if self.theme == "dark" else "#E1C96D")
        painter.fillPath(path, fill)
        painter.setPen(QPen(border, max(1.0 / zoom, 0.6)))
        painter.drawPath(path)
        painter.setPen(QColor(colors["text"] if self.theme == "dark" else colors["accent_dark"]))
        painter.setFont(_font(max(5, min(52, int(9 / zoom)))))
        painter.drawText(
            bubble.adjusted(12.0 / zoom, 10.0 / zoom, -12.0 / zoom, -10.0 / zoom),
            Qt.AlignLeft | Qt.AlignTop | Qt.TextWordWrap | Qt.TextWrapAnywhere,
            text,
        )

    def _selected_note_preview_text(self, node: Node) -> str:
        lines: list[str] = []
        preview_notes = [note for note in node.notes if not note.pinned]
        for note in preview_notes[:2]:
            title = note.display_title()
            content = " ".join(note.content.split())
            if len(content) > 90:
                content = f"{content[:87]}..."
            lines.append(f"{title}: {content}" if content else title)
        if len(preview_notes) > 2:
            lines.append(f"... 还有 {len(preview_notes) - 2} 条便签")
        return "\n".join(line for line in lines if line.strip())

    def wheelEvent(self, event) -> None:  # type: ignore[override]
        if self._scroll_inline_editor_under_mouse(event):
            return
        self._close_inline_field_editor(commit=True)
        self._begin_interaction_preview()
        factor = 1.12 if event.angleDelta().y() > 0 else 1 / 1.12
        current = self.transform().m11()
        target = max(0.18, min(2.8, current * factor))
        self.scale(target / current, target / current)
        self.viewport().update()

    def _scroll_inline_editor_under_mouse(self, event) -> bool:
        if self._inline_proxy is None or self._inline_editor is None:
            return False
        view_pos = event.position().toPoint()
        scene_pos = self.mapToScene(view_pos)
        if not self._inline_proxy.sceneBoundingRect().contains(scene_pos):
            return False
        scrollbar = self._inline_editor.verticalScrollBar()
        pixel_delta = event.pixelDelta().y() if hasattr(event, "pixelDelta") else 0
        if pixel_delta:
            scrollbar.setValue(scrollbar.value() - pixel_delta)
            event.accept()
            return True
        delta = event.angleDelta().y()
        if delta:
            step = scrollbar.singleStep() or 20
            units = max(1, abs(delta) // 120)
            direction = -1 if delta > 0 else 1
            scrollbar.setValue(scrollbar.value() + direction * step * 3 * units)
        event.accept()
        return True

    def mousePressEvent(self, event) -> None:  # type: ignore[override]
        self.setFocus()
        self._suppress_context_menu = False
        self.mouse_scene = self.mapToScene(event.position().toPoint())
        if self.close_inline_field_editor_if_outside(event.position().toPoint(), commit=True):
            if event.button() == Qt.LeftButton and not self.itemAt(event.position().toPoint()):
                self.clear_selection()
                event.accept()
                return
        if event.button() == Qt.LeftButton and self.connecting:
            target_id = self._endpoint_id_at(event.position().toPoint(), include_group_body=True, include_notes=True)
            if target_id and self.connection_source and target_id != self.connection_source:
                source = self.connection_source
                target = target_id
                self.cancel_connection()
                if not self._route_note_connection(source, target):
                    self.edgeCreated.emit(source, target)
            event.accept()
            return
        if event.button() == Qt.MiddleButton or (event.button() == Qt.LeftButton and self._space_panning):
            self._panning = True
            self._last_pan = event.position().toPoint()
            self._begin_interaction_preview()
            self._refresh_interaction_cursor()
            event.accept()
            return
        if event.button() == Qt.RightButton:
            self._right_drag_pending = True
            self._right_press_pos = event.position().toPoint()
            self._refresh_interaction_cursor()
            event.accept()
            return
        if (
            event.button() == Qt.LeftButton
            and not self.read_only
            and not self.itemAt(event.position().toPoint())
        ):
            self._start_rubber_selection(self.mouse_scene)
            event.accept()
            return
        super().mousePressEvent(event)
        if event.button() == Qt.LeftButton and not self.itemAt(event.position().toPoint()):
            self.clear_selection()

    def mouseMoveEvent(self, event) -> None:  # type: ignore[override]
        self.mouse_scene = self.mapToScene(event.position().toPoint())
        if self._rubber_selecting:
            self._update_rubber_selection(self.mouse_scene)
            event.accept()
            return
        if self._right_drag_pending:
            current_pos = event.position().toPoint()
            if not self._panning and (current_pos - self._right_press_pos).manhattanLength() > RIGHT_DRAG_MENU_THRESHOLD:
                self._panning = True
                self._last_pan = self._right_press_pos
                self._begin_interaction_preview()
                self._refresh_interaction_cursor()
            if self._panning:
                self._begin_interaction_preview()
                delta = current_pos - self._last_pan
                self._last_pan = current_pos
                self.horizontalScrollBar().setValue(self.horizontalScrollBar().value() - delta.x())
                self.verticalScrollBar().setValue(self.verticalScrollBar().value() - delta.y())
            event.accept()
            return
        if self._panning:
            self._begin_interaction_preview()
            delta = event.position().toPoint() - self._last_pan
            self._last_pan = event.position().toPoint()
            self.horizontalScrollBar().setValue(self.horizontalScrollBar().value() - delta.x())
            self.verticalScrollBar().setValue(self.verticalScrollBar().value() - delta.y())
            event.accept()
            return
        if self.connecting:
            self.viewport().update()
        if not self.itemAt(event.position().toPoint()):
            self._refresh_interaction_cursor()
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event) -> None:  # type: ignore[override]
        if self._rubber_selecting:
            self._finish_rubber_selection(self.mapToScene(event.position().toPoint()))
            event.accept()
            return
        if event.button() == Qt.RightButton and self._right_drag_pending:
            self._right_drag_pending = False
            release_pos = event.position().toPoint()
            if self._panning:
                self._panning = False
                self._suppress_context_menu = True
                self._refresh_interaction_cursor()
                event.accept()
                return
            if (release_pos - self._right_press_pos).manhattanLength() <= RIGHT_DRAG_MENU_THRESHOLD:
                self._suppress_context_menu = True
                self._show_context_menu(release_pos, event.globalPosition().toPoint())
                event.accept()
                return
            self._suppress_context_menu = True
            event.accept()
            return
        if self._panning:
            self._panning = False
            self._refresh_interaction_cursor()
            event.accept()
            return
        super().mouseReleaseEvent(event)

    def keyPressEvent(self, event) -> None:  # type: ignore[override]
        if event.key() == Qt.Key_Delete and not self.read_only:
            if self.selected_note_key:
                owner_node_id, note_id = self.selected_note_key
                self.noteDeleteRequested.emit(note_id, owner_node_id)
            elif self.selected_node_ids:
                self.nodesDeleteRequested.emit(set(self.selected_node_ids))
            elif self.selected_group_ids:
                self.groupDeleteRequested.emit(next(iter(self.selected_group_ids)))
            elif self.selected_edge_id:
                self.edgeDeleteRequested.emit(self.selected_edge_id)
            event.accept()
            return
        if event.key() == Qt.Key_Space and not event.isAutoRepeat():
            self._space_panning = True
            self._refresh_interaction_cursor()
            event.accept()
            return
        if event.key() == Qt.Key_Escape:
            self.cancel_connection()
            event.accept()
            return
        super().keyPressEvent(event)

    def keyReleaseEvent(self, event) -> None:  # type: ignore[override]
        if event.key() == Qt.Key_Space and not event.isAutoRepeat():
            self._space_panning = False
            self._refresh_interaction_cursor()
            event.accept()
            return
        super().keyReleaseEvent(event)

    def _start_rubber_selection(self, scene_pos: QPointF) -> None:
        self.clear_selection()
        self._rubber_selecting = True
        self._rubber_start = scene_pos
        path = QPainterPath()
        path.addRect(QRectF(scene_pos, scene_pos))
        self._rubber_item = QGraphicsPathItem(path)
        color = QColor(palette(self.theme)["blue"])
        fill = QColor(color)
        fill.setAlpha(34)
        self._rubber_item.setPen(QPen(color, 0, Qt.DashLine))
        self._rubber_item.setBrush(QBrush(fill))
        self._rubber_item.setZValue(1000)
        self.scene_obj.addItem(self._rubber_item)

    def _update_rubber_selection(self, scene_pos: QPointF) -> None:
        if not self._rubber_item:
            return
        rect = QRectF(self._rubber_start, scene_pos).normalized()
        path = QPainterPath()
        path.addRect(rect)
        self._rubber_item.setPath(path)

    def _finish_rubber_selection(self, scene_pos: QPointF) -> None:
        rect = QRectF(self._rubber_start, scene_pos).normalized()
        if self._rubber_item:
            self.scene_obj.removeItem(self._rubber_item)
            self._rubber_item = None
        self._rubber_selecting = False
        if rect.width() < 4 and rect.height() < 4:
            self.clear_selection()
            return
        selected = {
            node_id
            for node_id, item in self.node_items.items()
            if rect.intersects(item.sceneBoundingRect())
        }
        self.select_nodes(selected)

    def dragEnterEvent(self, event) -> None:  # type: ignore[override]
        if not self.read_only and event.mimeData().hasFormat(NOTE_MIME_TYPE):
            event.acceptProposedAction()
            return
        super().dragEnterEvent(event)

    def dragMoveEvent(self, event) -> None:  # type: ignore[override]
        if not self.read_only and event.mimeData().hasFormat(NOTE_MIME_TYPE):
            event.acceptProposedAction()
            return
        super().dragMoveEvent(event)

    def dropEvent(self, event) -> None:  # type: ignore[override]
        if self.read_only or not event.mimeData().hasFormat(NOTE_MIME_TYPE):
            super().dropEvent(event)
            return
        scene_pos = self.mapToScene(event.position().toPoint())
        owner_node_id = ""
        node_item = self._node_at(event.position().toPoint())
        if node_item is not None:
            owner_node_id = node_item.node.id
            node_rect = node_item.sceneBoundingRect()
            scene_pos = QPointF(node_rect.right() + 18.0, node_rect.top())
        raw = bytes(event.mimeData().data(NOTE_MIME_TYPE)).decode("utf-8", errors="ignore")
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            data = {}
        note = DesignNote.from_dict(data if isinstance(data, dict) else {})
        note.id = new_id("note")
        note.pinned = True
        note.x = scene_pos.x()
        note.y = scene_pos.y()
        note.width = max(NOTE_MIN_WIDTH, note.width or NOTE_DEFAULT_WIDTH)
        note.height = max(NOTE_MIN_HEIGHT, note.height or NOTE_DEFAULT_HEIGHT)
        self.create_note_from_data(note, owner_node_id)
        event.acceptProposedAction()

    def create_note_from_data(self, note: DesignNote, owner_node_id: str = "") -> None:
        if owner_node_id:
            node = next((candidate for candidate in self.project.nodes if candidate.id == owner_node_id), None)
            if node is None:
                return
            node.notes.append(note)
        else:
            self._canvas_notes().append(note)
        self.rebuild()
        self.select_note(note.id, owner_node_id)
        self.projectChanged.emit()

    def contextMenuEvent(self, event) -> None:  # type: ignore[override]
        if self._suppress_context_menu:
            self._suppress_context_menu = False
            event.accept()
            return
        self.close_inline_field_editor_if_outside(event.pos(), commit=True)
        self._show_context_menu(event.pos(), event.globalPos())

    def _show_context_menu(self, view_pos: QPoint, global_pos: QPoint) -> None:
        scene_pos = self.mapToScene(view_pos)
        note = self._note_at(view_pos)
        node = None if note else self._node_at(view_pos)
        edge = None if note or node else self._edge_at(view_pos)
        group_header = None if note or node or edge else self._group_at(view_pos)
        group_body = None if note or node or edge or group_header else self._group_body_at(view_pos)
        group = group_header or group_body
        if note:
            self.select_note(note.note.id, note.owner_node_id)
        elif node:
            self.select_node(node.node.id)
        elif edge:
            self.select_edge(edge.edge.id)
        elif group:
            self.select_group(group.group.id)

        menu = QMenu(self)
        if self.read_only:
            if node:
                open_action = menu.addAction("打开")
                open_folder_action = None
                if node.node.id in self.folder_action_node_ids:
                    open_folder_action = menu.addAction("打开项目所在文件夹")
                action = self._exec_context_menu(menu, global_pos)
                if action == open_action:
                    self.nodeActivated.emit(node.node.id)
                elif open_folder_action and action == open_folder_action:
                    self.nodeFolderRequested.emit(node.node.id)
                return
            if note:
                return
            create = menu.addAction("新建项目")
            open_project = menu.addAction("打开项目...")
            menu.addSeparator()
            reset = menu.addAction("重置视图")
            action = self._exec_context_menu(menu, global_pos)
            if action == create:
                self.createNodeRequested.emit(scene_pos.x(), scene_pos.y())
            elif action == open_project:
                self.openProjectRequested.emit()
            elif action == reset:
                self.reset_view()
            return
        if note:
            edit_note = menu.addAction("编辑便签")
            menu.addSeparator()
            delete_note = menu.addAction("删除便签")
            action = self._exec_context_menu(menu, global_pos)
            if action == edit_note:
                self.noteEditRequested.emit(note.note.id, note.owner_node_id)
            elif action == delete_note:
                self.noteDeleteRequested.emit(note.note.id, note.owner_node_id)
            return
        if node:
            open_canvas = None
            open_link = None
            if node.node.node_type == "画布":
                open_canvas = menu.addAction("打开画布")
            elif node.node.node_type == "超文本":
                open_link = menu.addAction("打开文档")
            edit = menu.addAction("编辑节点")
            notes = menu.addAction("便签...")
            create_note = menu.addAction("创建便签")
            ai_menu = menu.addMenu("AI")
            ai_iterate = ai_menu.addAction("迭代助手")
            connect = None if self.is_data_canvas() else menu.addAction("连接")
            menu.addSeparator()
            delete = menu.addAction("删除节点")
            action = self._exec_context_menu(menu, global_pos)
            if open_canvas and action == open_canvas:
                self.nodeActivated.emit(node.node.id)
            elif open_link and action == open_link:
                self.nodeActivated.emit(node.node.id)
            elif action == edit:
                self.nodeEditRequested.emit(node.node.id)
            elif action == notes:
                self.nodeNotesRequested.emit(node.node.id)
            elif action == create_note:
                note_pos = QPointF(node.sceneBoundingRect().right() + 18.0, node.sceneBoundingRect().top())
                self.createNoteRequested.emit(note_pos.x(), note_pos.y(), node.node.id)
            elif action == ai_iterate:
                self.aiIterateRequested.emit()
            elif connect and action == connect:
                self.start_connection(node.node.id)
            elif action == delete:
                self.nodeDeleteRequested.emit(node.node.id)
            return
        if edge:
            route_point_index = edge.route_point_index_at_scene(scene_pos)
            if route_point_index is not None:
                delete_route_point = menu.addAction("删除折点")
                menu.addSeparator()
            else:
                delete_route_point = None
            edit_edge = menu.addAction("编辑连线文本" if edge.edge.label else "添加连线文本")
            style_menu = menu.addMenu("连线样式")
            curve = style_menu.addAction("曲线")
            curve.setCheckable(True)
            curve.setChecked(edge.edge.style == "curve")
            straight = style_menu.addAction("直线")
            straight.setCheckable(True)
            straight.setChecked(edge.edge.style == "straight")
            orthogonal = style_menu.addAction("折直（智能多段直线）")
            orthogonal.setCheckable(True)
            orthogonal.setChecked(edge.edge.style == "orthogonal")
            delete_edge = menu.addAction("删除连线")
            action = self._exec_context_menu(menu, global_pos)
            if delete_route_point and action == delete_route_point:
                if edge.delete_route_point_at_scene(scene_pos):
                    self.projectChanged.emit()
            elif action == edit_edge:
                self.edgeEditRequested.emit(edge.edge.id)
            elif action == curve:
                self.edgeStyleRequested.emit(edge.edge.id, "curve")
            elif action == straight:
                self.edgeStyleRequested.emit(edge.edge.id, "straight")
            elif action == orthogonal:
                self.edgeStyleRequested.emit(edge.edge.id, "orthogonal")
            elif action == delete_edge:
                self.edgeDeleteRequested.emit(edge.edge.id)
            return
        if group_body:
            create_menu, create_actions = self._build_create_menu(menu)
            edit_group = menu.addAction("重命名蓝图组")
            ai_menu = menu.addMenu("AI")
            ai_iterate = ai_menu.addAction("迭代助手")
            connect_group = None if self.is_data_canvas() else menu.addAction("连接")
            menu.addSeparator()
            delete_group = menu.addAction("删除蓝图组")
            action = self._exec_context_menu(menu, global_pos)
            if action == ai_iterate:
                self.aiIterateRequested.emit()
                return
            if self._handle_create_action(action, create_actions, scene_pos):
                return
            if action == edit_group:
                self.groupEditRequested.emit(group_body.group.id)
            elif connect_group and action == connect_group:
                self.start_connection(group_body.group.id)
            elif action == delete_group:
                self.groupDeleteRequested.emit(group_body.group.id)
            return
        if group:
            edit_group = menu.addAction("重命名蓝图组")
            ai_menu = menu.addMenu("AI")
            ai_iterate = ai_menu.addAction("迭代助手")
            connect_group = None if self.is_data_canvas() else menu.addAction("连接")
            menu.addSeparator()
            delete_group = menu.addAction("删除蓝图组")
            action = self._exec_context_menu(menu, global_pos)
            if action == edit_group:
                self.groupEditRequested.emit(group.group.id)
            elif action == ai_iterate:
                self.aiIterateRequested.emit()
            elif connect_group and action == connect_group:
                self.start_connection(group.group.id)
            elif action == delete_group:
                self.groupDeleteRequested.emit(group.group.id)
            return

        _, create_actions = self._build_create_menu(menu)
        menu.addSeparator()
        reset = menu.addAction("重置视图")
        if self.connecting:
            cancel = menu.addAction("取消连接模式")
        else:
            cancel = None
        action = self._exec_context_menu(menu, global_pos)
        if self._handle_create_action(action, create_actions, scene_pos):
            return
        if action == reset:
            self.reset_view()
        elif cancel and action == cancel:
            self.cancel_connection()

    def _exec_context_menu(self, menu: QMenu, global_pos: QPoint):
        return menu.exec(global_pos)

    def _build_create_menu(self, menu: QMenu) -> tuple[QMenu, dict[str, object]]:
        create_menu = menu.addMenu("创建")
        actions: dict[str, object] = {
            "create": create_menu.addAction("数据节点" if self.is_data_canvas() else "节点"),
        }
        if self.is_data_canvas():
            layout_menu = menu.addMenu("排序")
            horizontal = layout_menu.addAction("水平")
            horizontal.setCheckable(True)
            horizontal.setChecked(bool(self.active_canvas() and self.active_canvas().data_layout == "horizontal"))
            grid = layout_menu.addAction("网格")
            grid.setCheckable(True)
            grid.setChecked(bool(self.active_canvas() and self.active_canvas().data_layout == "grid"))
            table = layout_menu.addAction("表格")
            table.setCheckable(True)
            table.setChecked(bool(self.active_canvas() and self.active_canvas().data_layout == "table"))
            grid_rows = layout_menu.addAction(
                f"网格行数：{self.active_canvas().data_grid_rows or '自动'}"
                if self.active_canvas()
                else "网格行数：自动"
            )

            data_template_menu = menu.addMenu("节点模板")
            if self.templates:
                for template in self.templates:
                    action = QAction(template.name, data_template_menu)
                    action.setData(template.id)
                    action.setCheckable(True)
                    action.setChecked(bool(self.active_canvas() and self.active_canvas().template_id == template.id))
                    data_template_menu.addAction(action)
                data_template_menu.addSeparator()
            else:
                empty = data_template_menu.addAction("暂无模板")
                empty.setEnabled(False)
                data_template_menu.addSeparator()
            manage_templates = data_template_menu.addAction("管理模板...")
            import_sheet = menu.addAction("导入 CSV/Excel...")
            create_group = create_menu.addAction("蓝图组")
            actions.update(
                {
                    "layout_horizontal": horizontal,
                    "layout_grid": grid,
                    "layout_table": table,
                    "grid_rows": grid_rows,
                    "create_group": create_group,
                    "import_sheet": import_sheet,
                    "data_template_menu": data_template_menu,
                    "manage_templates": manage_templates,
                }
            )
            return create_menu, actions

        create_canvas = create_menu.addAction("画布节点")
        create_data_canvas = create_menu.addAction("数据画布")
        create_note = create_menu.addAction("便签")
        create_group = create_menu.addAction("蓝图组")
        link_menu = create_menu.addMenu("超文本")
        create_md = link_menu.addAction("Markdown (.md)")
        create_txt = link_menu.addAction("文本 (.txt)")
        template_menu = create_menu.addMenu("按模板创建")
        if self.templates:
            for template in self.templates:
                action = QAction(template.name, template_menu)
                action.setData(template.id)
                template_menu.addAction(action)
        else:
            empty = template_menu.addAction("暂无模板")
            empty.setEnabled(False)
        actions.update(
            {
                "create_canvas": create_canvas,
                "create_data_canvas": create_data_canvas,
                "create_note": create_note,
                "create_group": create_group,
                "create_md": create_md,
                "create_txt": create_txt,
                "template_menu": template_menu,
            }
        )
        return create_menu, actions

    def _handle_create_action(self, action, create_actions: dict[str, object], scene_pos: QPointF) -> bool:
        if action == create_actions["create"]:
            self.createNodeRequested.emit(scene_pos.x(), scene_pos.y())
            return True
        if action == create_actions.get("create_canvas"):
            self.createCanvasNodeRequested.emit(scene_pos.x(), scene_pos.y())
            return True
        if action == create_actions.get("create_data_canvas"):
            self.createDataCanvasRequested.emit(scene_pos.x(), scene_pos.y())
            return True
        if action == create_actions.get("create_note"):
            self.createNoteRequested.emit(scene_pos.x(), scene_pos.y(), None)
            return True
        if action == create_actions.get("create_group"):
            self.createGroupRequested.emit(scene_pos.x(), scene_pos.y())
            return True
        if action == create_actions.get("create_md"):
            self.createLinkNodeRequested.emit(scene_pos.x(), scene_pos.y(), "md")
            return True
        if action == create_actions.get("create_txt"):
            self.createLinkNodeRequested.emit(scene_pos.x(), scene_pos.y(), "txt")
            return True
        if action == create_actions.get("layout_horizontal"):
            self.dataCanvasLayoutRequested.emit("horizontal")
            return True
        if action == create_actions.get("layout_grid"):
            self.dataCanvasLayoutRequested.emit("grid")
            return True
        if action == create_actions.get("layout_table"):
            self.dataCanvasLayoutRequested.emit("table")
            return True
        if action == create_actions.get("grid_rows"):
            current = self.active_canvas().data_grid_rows if self.active_canvas() else 0
            rows, ok = QInputDialog.getInt(
                self,
                "网格行数",
                "每列行数（0 为自动）",
                current,
                0,
                999,
                1,
            )
            if ok:
                self.dataCanvasGridRowsRequested.emit(rows)
            return True
        if action == create_actions.get("import_sheet"):
            self.dataCanvasImportRequested.emit()
            return True
        if action == create_actions.get("manage_templates"):
            self.templateManagerRequested.emit()
            return True
        data_template_menu = create_actions.get("data_template_menu")
        if action and data_template_menu and action.parent() is data_template_menu and action.data():
            self.dataCanvasTemplateRequested.emit(str(action.data()))
            return True
        template_menu = create_actions.get("template_menu")
        if action and action.parent() is template_menu and action.data():
            self.createTemplateNodeRequested.emit(scene_pos.x(), scene_pos.y(), str(action.data()))
            return True
        return False

    def _node_at(self, pos: QPoint) -> NodeItem | None:
        for item in self.items(pos):
            if isinstance(item, NodeItem):
                return item
        return None

    def _note_at(self, pos: QPoint) -> NoteItem | None:
        for item in self.items(pos):
            if isinstance(item, NoteItem):
                return item
        return None

    def _edge_at(self, pos: QPoint) -> EdgeItem | None:
        for item in self.items(pos):
            if isinstance(item, EdgeItem):
                return item
        return None

    def _group_at(self, pos: QPoint) -> BlueprintGroupItem | None:
        for item in self.items(pos):
            if isinstance(item, BlueprintGroupItem):
                return item
        return None

    def _group_body_at(self, pos: QPoint) -> BlueprintGroupItem | None:
        scene_pos = self.mapToScene(pos)
        containing = [
            (item.sceneBoundingRect().width() * item.sceneBoundingRect().height(), item)
            for item in self.group_items.values()
            if item.sceneBoundingRect().contains(scene_pos)
        ]
        if not containing:
            return None
        return min(containing, key=lambda item: item[0])[1]

    def _endpoint_id_at(self, pos: QPoint, include_group_body: bool = False, include_notes: bool = False) -> str | None:
        if include_notes:
            note = self._note_at(pos)
            if note:
                return note.note.id
        node = self._node_at(pos)
        if node:
            return node.node.id
        group = self._group_at(pos)
        if group:
            return group.group.id
        if include_group_body:
            group_body = self._group_body_at(pos)
            if group_body:
                return group_body.group.id
        return None

    def _connection_start(self, rect: QRectF, target: QPointF) -> QPointF:
        center = rect.center()
        dx = target.x() - center.x()
        dy = target.y() - center.y()
        if abs(dx) >= abs(dy):
            return QPointF(rect.right() if dx >= 0 else rect.left(), center.y())
        return QPointF(center.x(), rect.bottom() if dy >= 0 else rect.top())

    def _preview_path(self, start: QPointF, end: QPointF) -> QPainterPath:
        dx = end.x() - start.x()
        dy = end.y() - start.y()
        path = QPainterPath(start)
        if abs(dx) >= abs(dy):
            pull = max(70.0, min(abs(dx) * 0.45, 190.0))
            direction = 1.0 if dx >= 0 else -1.0
            path.cubicTo(QPointF(start.x() + pull * direction, start.y()), QPointF(end.x() - pull * direction, end.y()), end)
        else:
            pull = max(70.0, min(abs(dy) * 0.45, 190.0))
            direction = 1.0 if dy >= 0 else -1.0
            path.cubicTo(QPointF(start.x(), start.y() + pull * direction), QPointF(end.x(), end.y() - pull * direction), end)
        return path
