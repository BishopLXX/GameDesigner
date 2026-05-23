from __future__ import annotations

from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QPainter, QPixmap

from .models import NodeField


IMAGE_FIT_MODES = ["stretch", "contain", "cover", "nine_slice"]


def draw_field_pixmap(
    painter: QPainter,
    pixmap: QPixmap,
    target: QRectF,
    field: NodeField,
    *,
    smooth: bool = True,
) -> None:
    if pixmap.isNull() or target.width() <= 0 or target.height() <= 0:
        return
    painter.save()
    painter.setRenderHint(QPainter.SmoothPixmapTransform, smooth)
    fit = field.image_fit if field.image_fit in IMAGE_FIT_MODES else "stretch"
    if fit == "contain":
        _draw_contain(painter, pixmap, target)
    elif fit == "cover":
        _draw_cover(painter, pixmap, target)
    elif fit == "nine_slice":
        _draw_nine_slice(painter, pixmap, target, field)
    else:
        painter.drawPixmap(target.toRect(), pixmap, pixmap.rect())
    painter.restore()


def _draw_contain(painter: QPainter, pixmap: QPixmap, target: QRectF) -> None:
    scaled = pixmap.scaled(
        max(1, int(target.width())),
        max(1, int(target.height())),
        Qt.KeepAspectRatio,
        Qt.SmoothTransformation,
    )
    x = target.x() + (target.width() - scaled.width()) / 2
    y = target.y() + (target.height() - scaled.height()) / 2
    painter.drawPixmap(int(x), int(y), scaled)


def _draw_cover(painter: QPainter, pixmap: QPixmap, target: QRectF) -> None:
    scaled = pixmap.scaled(
        max(1, int(target.width())),
        max(1, int(target.height())),
        Qt.KeepAspectRatioByExpanding,
        Qt.SmoothTransformation,
    )
    source_x = max(0, int((scaled.width() - target.width()) / 2))
    source_y = max(0, int((scaled.height() - target.height()) / 2))
    source = scaled.rect().adjusted(source_x, source_y, -source_x, -source_y)
    source.setWidth(min(source.width(), int(target.width())))
    source.setHeight(min(source.height(), int(target.height())))
    painter.drawPixmap(target.toRect(), scaled, source)


def _draw_nine_slice(painter: QPainter, pixmap: QPixmap, target: QRectF, field: NodeField) -> None:
    source_w = pixmap.width()
    source_h = pixmap.height()
    if source_w <= 1 or source_h <= 1:
        painter.drawPixmap(target.toRect(), pixmap, pixmap.rect())
        return

    left = max(0, min(int(field.slice_left), source_w // 2))
    right = max(0, min(int(field.slice_right), source_w - left))
    top = max(0, min(int(field.slice_top), source_h // 2))
    bottom = max(0, min(int(field.slice_bottom), source_h - top))
    if left + right >= source_w or top + bottom >= source_h:
        painter.drawPixmap(target.toRect(), pixmap, pixmap.rect())
        return

    dest_left = min(float(left), target.width() / 2)
    dest_right = min(float(right), target.width() - dest_left)
    dest_top = min(float(top), target.height() / 2)
    dest_bottom = min(float(bottom), target.height() - dest_top)

    sx = [0, left, source_w - right, source_w]
    sy = [0, top, source_h - bottom, source_h]
    dx = [target.left(), target.left() + dest_left, target.right() - dest_right, target.right()]
    dy = [target.top(), target.top() + dest_top, target.bottom() - dest_bottom, target.bottom()]

    for row in range(3):
        for column in range(3):
            src = QRectF(sx[column], sy[row], sx[column + 1] - sx[column], sy[row + 1] - sy[row])
            dst = QRectF(dx[column], dy[row], dx[column + 1] - dx[column], dy[row + 1] - dy[row])
            if src.width() <= 0 or src.height() <= 0 or dst.width() <= 0 or dst.height() <= 0:
                continue
            painter.drawPixmap(dst, pixmap, src)
