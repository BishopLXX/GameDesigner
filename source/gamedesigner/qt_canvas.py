from __future__ import annotations

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
    QGraphicsScene,
    QGraphicsView,
    QMenu,
)

from .models import BlueprintGroup, CanvasData, Edge, Node, NodeField, NodeTemplate, ProjectData
from .qt_theme import palette


NODE_DEFAULT_WIDTH = 310.0
NODE_MIN_WIDTH = 260.0
NODE_MIN_HEIGHT = 92.0
NODE_MAX_NATURAL_WIDTH = 680.0
HEADER_HEIGHT = 52.0
ROW_GAP = 7.0
ROW_TOP = HEADER_HEIGHT + 6.0
RESIZE_HANDLE = 20.0
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
IMAGE_SOURCE_CACHE_LIMIT = 96
IMAGE_SCALED_CACHE_LIMIT = 192
INTERACTION_PREVIEW_DELAY_MS = 140
_MISSING_PIXMAP = object()


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
        if self.view.can_move_nodes():
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
        if changed:
            self.view.refresh_group_membership()
            self.view.projectChanged.emit()
        super().mouseReleaseEvent(event)

    def itemChange(self, change: QGraphicsItem.GraphicsItemChange, value):  # type: ignore[override]
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
        self._pressed_pos = QPointF()
        self._moved = False
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
        fallback = "画" if self.node.node_type == "画布" else "链" if self.node.node_type == "超链接" else self.node.title
        text = (self.node.display_icon() or fallback or "节").strip()[:8]
        painter.setPen(QColor(colors["accent"]))
        text_rect = rect.adjusted(12, 22, -12, -8)
        self._draw_adaptive_center_text(painter, text_rect, text, zoom, 20, 84)

    def _paint_compact_mode(self, painter: QPainter, colors: dict[str, str], rect: QRectF, zoom: float) -> None:
        fallback = "画" if self.node.node_type == "画布" else "链" if self.node.node_type == "超链接" else self.node.title[:1]
        icon = (self.node.display_icon() or fallback or "节").strip()[:2]
        painter.setPen(QColor(colors["accent"]))
        icon_rect = QRectF(18, 28, 58, max(28, rect.height() - 42))
        icon_font_size = self._fit_font_size(icon, icon_rect, int(18 / max(zoom, 0.18)), 12, 52)
        font = _font(icon_font_size, True)
        painter.setFont(font)
        painter.drawText(icon_rect, Qt.AlignCenter, icon)
        painter.setPen(QColor(colors["node_text"]))
        title_rect = QRectF(84, 28, rect.width() - 102, max(28, rect.height() - 42))
        self._draw_adaptive_center_text(painter, title_rect, self.node.title, zoom, 13, 42)

    def _paint_detail_mode(self, painter: QPainter, colors: dict[str, str], rect: QRectF) -> None:
        icon = self.node.display_icon() or ("画" if self.node.node_type == "画布" else "链" if self.node.node_type == "超链接" else "")
        title = f"{icon}  {self.node.title}" if icon else self.node.title
        painter.setPen(QColor(colors["node_text"]))
        title_font = _font(12, True)
        painter.setFont(title_font)
        painter.drawText(QRectF(18, 28, rect.width() - 92, 22), Qt.AlignLeft | Qt.AlignVCenter, title)
        painter.setPen(QColor(colors["node_muted"]))
        painter.setFont(_font(8))
        meta = (
            "画布"
            if self.node.node_type == "画布"
            else self.node.link_format.upper()
            if self.node.node_type == "超链接"
            else f"{len(self.node.fields)} 项"
        )
        painter.drawText(QRectF(rect.width() - 72, 30, 54, 18), Qt.AlignRight | Qt.AlignVCenter, meta)

        if not self.node.fields:
            painter.setPen(QColor(colors["node_muted"]))
            painter.setFont(_font(10))
            painter.drawText(QRectF(18, HEADER_HEIGHT + 18, rect.width() - 36, 24), Qt.AlignLeft, "暂无数据字段")
            return

        visual_fields = [field for field in self.node.fields if field.has_visual_layout()]
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
            painter.setPen(QColor(colors["node_muted"]))
            painter.setFont(_font(8))
            type_x = row.x() + 10 + name_w + 10
            painter.drawText(
                QRectF(type_x, row.y() + 8, type_w, row.height() - 16),
                WRAP_FLAGS,
                field.data_type,
            )
            painter.setPen(QColor(colors["node_text"]))
            painter.setFont(_font(9))
            value_x = type_x + type_w + 10
            painter.drawText(
                QRectF(value_x, row.y() + 8, row.right() - value_x - 10, row.height() - 16),
                WRAP_FLAGS,
                field.value or " ",
            )
            y += row_h + ROW_GAP

    def _paint_visual_fields(self, painter: QPainter, colors: dict[str, str], fields: list[NodeField]) -> None:
        content_w = max([field.x + field.width for field in fields] + [self.width - 28]) + 20
        content_h = max([field.y + field.height for field in fields] + [self.height - HEADER_HEIGHT - 24]) + 20
        available_w = max(1.0, self.width - 28)
        available_h = max(1.0, self.height - HEADER_HEIGHT - 18)
        scale_x = min(available_w / content_w, 3.0)
        scale_y = min(available_h / content_h, 3.0)
        for field in fields:
            x = 14 + field.x * scale_x
            y = HEADER_HEIGHT + field.y * scale_y
            w = max(24, field.width * scale_x)
            h = max(22, field.height * scale_y)
            card = QRectF(x, y, w, h)
            path = QPainterPath()
            path.addRoundedRect(card, 9, 9)
            painter.fillPath(path, QColor(field.bg_color or "#FFFFFF"))
            painter.setPen(QPen(QColor("#DADAE0"), 1))
            painter.drawPath(path)
            is_image = field.data_type == "图片"
            if is_image and field.image_path:
                target = card.adjusted(8, 8, -8, -8)
                if self.view.is_interaction_preview():
                    pixmap = self.view._source_image_pixmap(field.image_path)
                    if pixmap is not None:
                        image_rect = self._fit_pixmap_rect(target, pixmap.width(), pixmap.height())
                        painter.save()
                        painter.setRenderHint(QPainter.SmoothPixmapTransform, False)
                        painter.drawPixmap(image_rect.toRect(), pixmap, pixmap.rect())
                        painter.restore()
                else:
                    pixmap = self.view._scaled_image_pixmap(
                        field.image_path,
                        int(max(1, target.width())),
                        int(max(1, target.height())),
                    )
                    if pixmap is not None:
                        image_rect = self._fit_pixmap_rect(target, pixmap.width(), pixmap.height())
                        painter.drawPixmap(image_rect.toRect(), pixmap, pixmap.rect())
            elif is_image:
                painter.setPen(QColor(colors["node_muted"]))
                painter.setFont(_font(9))
                painter.drawText(card.adjusted(9, 8, -9, -8), Qt.AlignCenter | Qt.TextWordWrap, "选择图片")
            text = field.value if is_image else (field.value or field.name)
            if not text:
                continue
            painter.setPen(QColor(field.text_color or colors["node_text"]))
            font = _font(max(8, min(48, int(field.font_size * min(scale_x, scale_y)))))
            painter.setFont(font)
            painter.drawText(card.adjusted(9, 8, -9, -8), _field_text_flags(field), text)

    def _paint_resize_handle(self, painter: QPainter, colors: dict[str, str], rect: QRectF) -> None:
        painter.setPen(QPen(QColor(colors["blue"]), 1.4))
        for offset in (6, 11, 16):
            painter.drawLine(
                QPointF(rect.right() - offset, rect.bottom() - 4),
                QPointF(rect.right() - 4, rect.bottom() - offset),
            )

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
        if not self.view.can_resize_nodes():
            self.setCursor(Qt.OpenHandCursor if self.view.can_move_nodes() else Qt.PointingHandCursor)
            super().hoverMoveEvent(event)
            return
        self.view.hover_node_id = self.node.id if self._on_resize_handle(event.pos()) else None
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
                self.setCursor(Qt.SizeFDiagCursor)
                event.accept()
                return
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
        if self._resizing:
            delta = event.scenePos() - self._resize_origin
            self.prepareGeometryChange()
            self.width = max(NODE_MIN_WIDTH, self._resize_size[0] + delta.x())
            requested_height = max(NODE_MIN_HEIGHT, self._resize_size[1] + delta.y())
            if any(field.has_visual_layout() for field in self.node.fields):
                self.height = requested_height
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
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event) -> None:  # type: ignore[override]
        self._resizing = False
        self.setCursor(Qt.OpenHandCursor)
        self.view.snap_guides.clear()
        self.view.viewport().update()
        if self._moved:
            self.view.refresh_group_membership()
            self.view.projectChanged.emit()
        super().mouseReleaseEvent(event)

    def itemChange(self, change: QGraphicsItem.GraphicsItemChange, value):  # type: ignore[override]
        if change == QGraphicsItem.ItemPositionChange and self.scene() and not self.view.rebuilding:
            pos = value
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
            self.node.x = self.pos().x()
            self.node.y = self.pos().y()
            self.view.update_edges_for_node(self.node.id)
        return super().itemChange(change, value)

    def refresh(self) -> None:
        self.prepareGeometryChange()
        self._sync_size()
        self.update()

    def _sync_size(self) -> None:
        visual_fields = [field for field in self.node.fields if field.has_visual_layout()]
        if visual_fields:
            natural_w = max([field.x + field.width for field in visual_fields] + [NODE_DEFAULT_WIDTH]) + 34
            natural_h = HEADER_HEIGHT + max([field.y + field.height for field in visual_fields] + [120]) + 24
            self.width = max(NODE_MIN_WIDTH, self.node.width if self.node.width > 0 else min(NODE_MAX_NATURAL_WIDTH, natural_w))
            self.height = max(NODE_MIN_HEIGHT, self.node.height if self.node.height > 0 else natural_h)
            return
        natural_width = self._natural_detail_width()
        self.width = max(NODE_MIN_WIDTH, self.node.width if self.node.width > 0 else natural_width)
        natural_height = self._natural_detail_height(self.width)
        self.height = max(NODE_MIN_HEIGHT, self.node.height if self.node.height > 0 else natural_height)

    def _natural_detail_width(self) -> float:
        title = f"{self.node.icon}  {self.node.title}" if self.node.icon else self.node.title
        title_width = QFontMetrics(_font(12, True)).horizontalAdvance(title) + 118
        name_w, type_w = self._row_column_widths()
        value_font = QFontMetrics(_font(9))
        value_widths = [
            min(max(value_font.horizontalAdvance(field.value or " "), 170), 420)
            for field in self.node.fields
        ]
        value_width = max(value_widths + [170])
        natural = max(NODE_DEFAULT_WIDTH, title_width, name_w + type_w + value_width + 50)
        return min(NODE_MAX_NATURAL_WIDTH, natural)

    def _natural_detail_height(self, width: float) -> float:
        if not self.node.fields:
            return HEADER_HEIGHT + 66
        name_w, type_w = self._row_column_widths()
        content_height = sum(
            self._row_height(field, width, name_w, type_w) + ROW_GAP
            for field in self.node.fields
        )
        return ROW_TOP + content_height + 10

    def _row_column_widths(self) -> tuple[float, float]:
        name_metrics = QFontMetrics(_font(9, True))
        type_metrics = QFontMetrics(_font(8))
        name_w = max([name_metrics.horizontalAdvance(field.name) + 18 for field in self.node.fields] + [92])
        type_w = max([type_metrics.horizontalAdvance(field.data_type) + 16 for field in self.node.fields] + [54])
        return min(max(82.0, name_w), 132.0), min(max(52.0, type_w), 88.0)

    def _row_height(self, field: NodeField, width: float, name_w: float, type_w: float) -> float:
        value_w = max(40.0, width - 20 - 10 - name_w - 10 - type_w - 20)
        name_h = self._wrapped_height(field.name, _font(9, True), name_w)
        type_h = self._wrapped_height(field.data_type, _font(8), type_w)
        value_h = self._wrapped_height(field.value or " ", _font(9), value_w)
        return max(40.0, name_h + 16, type_h + 16, value_h + 16)

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


class EdgeItem(QGraphicsPathItem):
    def __init__(self, edge: Edge, source: QGraphicsItem, target: QGraphicsItem, view: "NodeGraphView") -> None:
        super().__init__()
        self.edge = edge
        self.source = source
        self.target = target
        self.view = view
        self.setZValue(0)
        self.setFlags(QGraphicsItem.ItemIsSelectable)
        self.setAcceptHoverEvents(True)
        self.update_path()

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

        dx = end.x() - start.x()
        dy = end.y() - start.y()
        path = QPainterPath(start)
        if abs(dx) >= abs(dy):
            pull = max(70.0, min(abs(dx) * 0.45, 190.0))
            direction = 1.0 if dx >= 0 else -1.0
            c1 = QPointF(start.x() + pull * direction, start.y())
            c2 = QPointF(end.x() - pull * direction, end.y())
        else:
            pull = max(70.0, min(abs(dy) * 0.45, 190.0))
            direction = 1.0 if dy >= 0 else -1.0
            c1 = QPointF(start.x(), start.y() + pull * direction)
            c2 = QPointF(end.x(), end.y() - pull * direction)
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
        if self.edge.label:
            point = self.path().pointAtPercent(0.5)
            painter.setPen(QColor(colors["edge_label"]))
            painter.setFont(_font(9))
            painter.drawText(QRectF(point.x() - 80, point.y() - 28, 160, 20), Qt.AlignCenter, self.edge.label)

    def shape(self) -> QPainterPath:
        stroker = QPainterPathStroker()
        stroker.setWidth(16)
        return stroker.createStroke(self.path())

    def mousePressEvent(self, event) -> None:  # type: ignore[override]
        if event.button() == Qt.LeftButton:
            self.view.select_edge(self.edge.id)
            event.accept()
            return
        super().mousePressEvent(event)

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
        return path.pointAtPercent(0.97)

    def _anchor(self, rect: QRectF, other: QRectF) -> QPointF:
        center = rect.center()
        other_center = other.center()
        dx = other_center.x() - center.x()
        dy = other_center.y() - center.y()
        if abs(dx) >= abs(dy):
            return QPointF(rect.right() if dx >= 0 else rect.left(), center.y())
        return QPointF(center.x(), rect.bottom() if dy >= 0 else rect.top())

    def _orthogonal_points(
        self,
        start: QPointF,
        end: QPointF,
        source_rect: QRectF,
        target_rect: QRectF,
    ) -> list[QPointF]:
        dx = end.x() - start.x()
        dy = end.y() - start.y()
        if abs(dx) >= abs(dy):
            mid_x = (start.x() + end.x()) / 2
            if abs(dx) < 80:
                if start.x() >= source_rect.center().x():
                    mid_x = max(source_rect.right(), target_rect.right()) + 58
                else:
                    mid_x = min(source_rect.left(), target_rect.left()) - 58
            return [
                start,
                QPointF(mid_x, start.y()),
                QPointF(mid_x, end.y()),
                end,
            ]

        mid_y = (start.y() + end.y()) / 2
        if abs(dy) < 80:
            if start.y() >= source_rect.center().y():
                mid_y = max(source_rect.bottom(), target_rect.bottom()) + 58
            else:
                mid_y = min(source_rect.top(), target_rect.top()) - 58
        return [
            start,
            QPointF(start.x(), mid_y),
            QPointF(end.x(), mid_y),
            end,
        ]


class NodeGraphView(QGraphicsView):
    selectionChanged = Signal(object, object)
    projectChanged = Signal()
    nodeActivated = Signal(str)
    nodeFolderRequested = Signal(str)
    nodeEditRequested = Signal(str)
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
    createLinkNodeRequested = Signal(float, float, str)
    createGroupRequested = Signal(float, float)
    createTemplateNodeRequested = Signal(float, float, str)
    templateManagerRequested = Signal()
    openProjectRequested = Signal()

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
        self.edge_items: dict[str, EdgeItem] = {}
        self.selected_node_ids: set[str] = set()
        self.selected_group_ids: set[str] = set()
        self.selected_node_id: str | None = None
        self.selected_edge_id: str | None = None
        self.connecting = False
        self.connection_source: str | None = None
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
        self._pan_cursor_override = False
        self._last_pan = QPoint()
        self._right_press_pos = QPoint()
        self._right_drag_pending = False
        self._suppress_context_menu = False
        self._interaction_preview = False
        self._interaction_preview_timer = QTimer(self)
        self._interaction_preview_timer.setSingleShot(True)
        self._interaction_preview_timer.timeout.connect(self._end_interaction_preview)
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
        self.setFocusPolicy(Qt.StrongFocus)
        app = QApplication.instance()
        if app:
            app.installEventFilter(self)
        self.rebuild()

    def can_move_nodes(self) -> bool:
        return not self.read_only or self.allow_node_drag

    def can_resize_nodes(self) -> bool:
        return self.can_move_nodes()

    def eventFilter(self, _watched, event) -> bool:  # type: ignore[override]
        if event.type() in (QEvent.ApplicationDeactivate, QEvent.WindowDeactivate):
            if self._space_panning or self._panning or self._right_drag_pending:
                self._space_panning = False
                self._panning = False
                self._right_drag_pending = False
                self._end_interaction_preview()
                self._refresh_interaction_cursor()
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
        self.edge_items.clear()
        groups = getattr(self.project, "groups", [])
        self.selected_node_ids &= {node.id for node in self.project.nodes}
        self.selected_group_ids &= {group.id for group in groups}
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
        for edge in self.project.valid_edges():
            source = self._endpoint_item(edge.source)
            target = self._endpoint_item(edge.target)
            if source and target:
                edge_item = EdgeItem(edge, source, target, self)
                self.scene_obj.addItem(edge_item)
                self.edge_items[edge.id] = edge_item
        self.rebuilding = False
        self._update_scene_rect()
        self._refresh_interaction_cursor()

    def _update_scene_rect(self) -> None:
        rect = QRectF(-SCENE_EXTENT, -SCENE_EXTENT, SCENE_EXTENT * 2, SCENE_EXTENT * 2)
        for item in self.group_items.values():
            rect = rect.united(item.sceneBoundingRect().adjusted(-SCENE_MARGIN, -SCENE_MARGIN, SCENE_MARGIN, SCENE_MARGIN))
        for item in self.node_items.values():
            rect = rect.united(item.sceneBoundingRect().adjusted(-SCENE_MARGIN, -SCENE_MARGIN, SCENE_MARGIN, SCENE_MARGIN))
        self.scene_obj.setSceneRect(rect)

    def _endpoint_item(self, endpoint_id: str | None) -> QGraphicsItem | None:
        if not endpoint_id:
            return None
        return self.node_items.get(endpoint_id) or self.group_items.get(endpoint_id)

    def center_world(self) -> QPointF:
        return self.mapToScene(self.viewport().rect().center())

    def reset_view(self) -> None:
        self.resetTransform()
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
        if self.read_only:
            return
        if not source_id:
            return
        if not self._endpoint_item(source_id):
            return
        self.connecting = True
        self.connection_source = source_id
        if source_id in self.node_items:
            self.select_node(source_id)
        elif source_id in self.group_items:
            self.select_group(source_id)
        self._refresh_interaction_cursor()
        self.viewport().update()

    def cancel_connection(self) -> None:
        self.connecting = False
        self.connection_source = None
        self._refresh_interaction_cursor()
        self.viewport().update()

    def select_node(self, node_id: str | None) -> None:
        self.selected_node_id = node_id
        self.selected_edge_id = None
        self.selected_node_ids = {node_id} if node_id else set()
        self.selected_group_ids.clear()
        for item_id, item in self.node_items.items():
            item.setSelected(item_id == node_id)
        for item in self.group_items.values():
            item.setSelected(False)
        for item in self.edge_items.values():
            item.setSelected(False)
        self.selectionChanged.emit(node_id, None)
        self.viewport().update()

    def select_nodes(self, node_ids: set[str]) -> None:
        self.selected_node_ids = set(node_ids)
        self.selected_node_id = next(iter(self.selected_node_ids), None)
        self.selected_edge_id = None
        self.selected_group_ids.clear()
        for item_id, item in self.node_items.items():
            item.setSelected(item_id in self.selected_node_ids)
        for item in self.group_items.values():
            item.setSelected(False)
        for item in self.edge_items.values():
            item.setSelected(False)
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
        self.selected_node_ids.clear()
        self.selected_group_ids = {group_id} if group_id else set()
        for item in self.node_items.values():
            item.setSelected(False)
        for item_id, item in self.group_items.items():
            item.setSelected(item_id == group_id)
        for item in self.edge_items.values():
            item.setSelected(False)
        self.selectionChanged.emit(None, None)
        self.viewport().update()

    def select_edge(self, edge_id: str | None) -> None:
        self.selected_node_id = None
        self.selected_edge_id = edge_id
        self.selected_node_ids.clear()
        self.selected_group_ids.clear()
        for item in self.node_items.values():
            item.setSelected(False)
        for item in self.group_items.values():
            item.setSelected(False)
        for item_id, item in self.edge_items.items():
            item.setSelected(item_id == edge_id)
        self.selectionChanged.emit(None, edge_id)
        self.viewport().update()

    def clear_selection(self) -> None:
        self.selected_node_id = None
        self.selected_edge_id = None
        self.selected_node_ids.clear()
        self.selected_group_ids.clear()
        for item in self.node_items.values():
            item.setSelected(False)
        for item in self.group_items.values():
            item.setSelected(False)
        for item in self.edge_items.values():
            item.setSelected(False)
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

    def snap_position(self, moving: NodeItem, target: QPointF) -> QPointF:
        scale = max(0.001, self.transform().m11())
        snapped = QPointF(target)
        guides: list[SnapGuide] = []

        x_result = self._best_grid_snap(target.x(), "x", scale)
        y_result = self._best_grid_snap(target.y(), "y", scale)
        align_x = self._best_align_snap(moving, target, "x", scale)
        align_y = self._best_align_snap(moving, target, "y", scale)
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
        self, moving: NodeItem, target: QPointF, axis: str, scale: float
    ) -> tuple[float, float, SnapGuide] | None:
        moving_w = moving.width
        moving_h = moving.height
        moving_x = {"左": target.x(), "中": target.x() + moving_w / 2, "右": target.x() + moving_w}
        moving_y = {"上": target.y(), "中": target.y() + moving_h / 2, "下": target.y() + moving_h}
        best: tuple[float, float, SnapGuide] | None = None
        for other in self.node_items.values():
            if other is moving:
                continue
            other_x = {"左": other.pos().x(), "中": other.pos().x() + other.width / 2, "右": other.pos().x() + other.width}
            other_y = {"上": other.pos().y(), "中": other.pos().y() + other.height / 2, "下": other.pos().y() + other.height}
            if axis == "x":
                if self._screen_gap(target.y(), target.y() + moving_h, other.pos().y(), other.pos().y() + other.height, scale) > ALIGN_PROXIMITY:
                    continue
                for moving_label, moving_value in moving_x.items():
                    for other_label, other_value in other_x.items():
                        distance = abs(other_value - moving_value) * scale
                        if distance <= ALIGN_SNAP_THRESHOLD:
                            delta = other_value - moving_value
                            guide = SnapGuide("x", other_value, f"X 对齐 {moving_label}/{other_label}", "align")
                            if best is None or distance < best[1]:
                                best = (delta, distance, guide)
            else:
                if self._screen_gap(target.x(), target.x() + moving_w, other.pos().x(), other.pos().x() + other.width, scale) > ALIGN_PROXIMITY:
                    continue
                for moving_label, moving_value in moving_y.items():
                    for other_label, other_value in other_y.items():
                        distance = abs(other_value - moving_value) * scale
                        if distance <= ALIGN_SNAP_THRESHOLD:
                            delta = other_value - moving_value
                            guide = SnapGuide("y", other_value, f"Y 对齐 {moving_label}/{other_label}", "align")
                            if best is None or distance < best[1]:
                                best = (delta, distance, guide)
        return best

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
            source = self._endpoint_item(self.connection_source)
            if source:
                start = self._connection_start(source.sceneBoundingRect(), self.mouse_scene)
                path = self._preview_path(start, self.mouse_scene)
                painter.setPen(QPen(QColor(colors["edge"]), 2.2, Qt.DashLine, Qt.RoundCap, Qt.RoundJoin))
                painter.drawPath(path)

    def wheelEvent(self, event) -> None:  # type: ignore[override]
        self._begin_interaction_preview()
        factor = 1.12 if event.angleDelta().y() > 0 else 1 / 1.12
        current = self.transform().m11()
        target = max(0.18, min(2.8, current * factor))
        self.scale(target / current, target / current)
        self.viewport().update()

    def mousePressEvent(self, event) -> None:  # type: ignore[override]
        self.setFocus()
        self._suppress_context_menu = False
        self.mouse_scene = self.mapToScene(event.position().toPoint())
        if event.button() == Qt.LeftButton and self.connecting:
            target_id = self._endpoint_id_at(event.position().toPoint(), include_group_body=True)
            if target_id and self.connection_source and target_id != self.connection_source:
                source = self.connection_source
                target = target_id
                self.cancel_connection()
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
                self._refresh_interaction_cursor()
                event.accept()
                return
            if (release_pos - self._right_press_pos).manhattanLength() <= RIGHT_DRAG_MENU_THRESHOLD:
                self._suppress_context_menu = True
                self._show_context_menu(release_pos, event.globalPosition().toPoint())
                event.accept()
                return
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
            if self.selected_node_ids:
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

    def contextMenuEvent(self, event) -> None:  # type: ignore[override]
        if self._suppress_context_menu:
            self._suppress_context_menu = False
            event.accept()
            return
        self._show_context_menu(event.pos(), event.globalPos())

    def _show_context_menu(self, view_pos: QPoint, global_pos: QPoint) -> None:
        scene_pos = self.mapToScene(view_pos)
        node = self._node_at(view_pos)
        edge = None if node else self._edge_at(view_pos)
        group_header = None if node or edge else self._group_at(view_pos)
        group_body = None if node or edge or group_header else self._group_body_at(view_pos)
        group = group_header or group_body
        if node:
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
                action = menu.exec(global_pos)
                if action == open_action:
                    self.nodeActivated.emit(node.node.id)
                elif open_folder_action and action == open_folder_action:
                    self.nodeFolderRequested.emit(node.node.id)
                return
            create = menu.addAction("新建项目")
            open_project = menu.addAction("打开项目...")
            menu.addSeparator()
            reset = menu.addAction("重置视图")
            action = menu.exec(global_pos)
            if action == create:
                self.createNodeRequested.emit(scene_pos.x(), scene_pos.y())
            elif action == open_project:
                self.openProjectRequested.emit()
            elif action == reset:
                self.reset_view()
            return
        if node:
            open_canvas = None
            open_link = None
            if node.node.node_type == "画布":
                open_canvas = menu.addAction("打开画布")
            elif node.node.node_type == "超链接":
                open_link = menu.addAction("打开文档")
            edit = menu.addAction("编辑节点")
            connect = menu.addAction("连接")
            menu.addSeparator()
            delete = menu.addAction("删除节点")
            action = menu.exec(global_pos)
            if open_canvas and action == open_canvas:
                self.nodeActivated.emit(node.node.id)
            elif open_link and action == open_link:
                self.nodeActivated.emit(node.node.id)
            elif action == edit:
                self.nodeEditRequested.emit(node.node.id)
            elif action == connect:
                self.start_connection(node.node.id)
            elif action == delete:
                self.nodeDeleteRequested.emit(node.node.id)
            return
        if edge:
            edit_edge = menu.addAction("编辑连接标签")
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
            action = menu.exec(global_pos)
            if action == edit_edge:
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
            connect_group = menu.addAction("连接")
            menu.addSeparator()
            delete_group = menu.addAction("删除蓝图组")
            action = menu.exec(global_pos)
            if self._handle_create_action(action, create_actions, scene_pos):
                return
            if action == edit_group:
                self.groupEditRequested.emit(group_body.group.id)
            elif action == connect_group:
                self.start_connection(group_body.group.id)
            elif action == delete_group:
                self.groupDeleteRequested.emit(group_body.group.id)
            return
        if group:
            edit_group = menu.addAction("重命名蓝图组")
            connect_group = menu.addAction("连接")
            menu.addSeparator()
            delete_group = menu.addAction("删除蓝图组")
            action = menu.exec(global_pos)
            if action == edit_group:
                self.groupEditRequested.emit(group.group.id)
            elif action == connect_group:
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
        action = menu.exec(global_pos)
        if self._handle_create_action(action, create_actions, scene_pos):
            return
        if action == reset:
            self.reset_view()
        elif cancel and action == cancel:
            self.cancel_connection()

    def _build_create_menu(self, menu: QMenu) -> tuple[QMenu, dict[str, object]]:
        create_menu = menu.addMenu("创建")
        create = create_menu.addAction("节点")
        create_canvas = create_menu.addAction("画布节点")
        create_group = create_menu.addAction("蓝图组")
        link_menu = create_menu.addMenu("超链接")
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
        return create_menu, {
            "create": create,
            "create_canvas": create_canvas,
            "create_group": create_group,
            "create_md": create_md,
            "create_txt": create_txt,
            "template_menu": template_menu,
        }

    def _handle_create_action(self, action, create_actions: dict[str, object], scene_pos: QPointF) -> bool:
        if action == create_actions["create"]:
            self.createNodeRequested.emit(scene_pos.x(), scene_pos.y())
            return True
        if action == create_actions["create_canvas"]:
            self.createCanvasNodeRequested.emit(scene_pos.x(), scene_pos.y())
            return True
        if action == create_actions["create_group"]:
            self.createGroupRequested.emit(scene_pos.x(), scene_pos.y())
            return True
        if action == create_actions["create_md"]:
            self.createLinkNodeRequested.emit(scene_pos.x(), scene_pos.y(), "md")
            return True
        if action == create_actions["create_txt"]:
            self.createLinkNodeRequested.emit(scene_pos.x(), scene_pos.y(), "txt")
            return True
        template_menu = create_actions["template_menu"]
        if action and action.parent() is template_menu and action.data():
            self.createTemplateNodeRequested.emit(scene_pos.x(), scene_pos.y(), str(action.data()))
            return True
        return False

    def _node_at(self, pos: QPoint) -> NodeItem | None:
        for item in self.items(pos):
            if isinstance(item, NodeItem):
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

    def _endpoint_id_at(self, pos: QPoint, include_group_body: bool = False) -> str | None:
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
