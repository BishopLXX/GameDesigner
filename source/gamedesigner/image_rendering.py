from __future__ import annotations

from collections import OrderedDict
from pathlib import Path

from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QImageReader, QPainter, QPixmap

from .models import NodeField


IMAGE_FIT_MODES = ["stretch", "contain", "cover", "nine_slice"]
IMAGE_SOURCE_CACHE_LIMIT = 32
IMAGE_SCALED_CACHE_LIMIT = 64
_MISSING_PIXMAP = object()


class PixmapCache:
    def __init__(
        self,
        *,
        source_limit: int = IMAGE_SOURCE_CACHE_LIMIT,
        scaled_limit: int = IMAGE_SCALED_CACHE_LIMIT,
    ) -> None:
        self.source_limit = max(1, int(source_limit))
        self.scaled_limit = max(1, int(scaled_limit))
        self._source_pixmap_cache: OrderedDict[str, QPixmap | None] = OrderedDict()
        self._scaled_pixmap_cache: OrderedDict[tuple[str, int, int, str, bool], QPixmap | None] = OrderedDict()

    def clear(self) -> None:
        self._source_pixmap_cache.clear()
        self._scaled_pixmap_cache.clear()

    def source(self, path: str) -> QPixmap | None:
        cache_key = path.strip()
        if not cache_key:
            return None
        cached = self._source_pixmap_cache.get(cache_key, _MISSING_PIXMAP)
        if cached is not _MISSING_PIXMAP:
            self._source_pixmap_cache.move_to_end(cache_key)
            return cached
        pixmap = self._load_source(cache_key)
        self._remember(self._source_pixmap_cache, cache_key, pixmap, self.source_limit)
        return pixmap

    def scaled(
        self,
        path: str,
        width: int,
        height: int,
        mode: str = "contain",
        *,
        smooth: bool | None = None,
    ) -> QPixmap | None:
        source = self.source(path)
        if source is None:
            return None
        if smooth is None:
            smooth = not is_pixel_art_image_path(path)
        normalized_mode = mode if mode in {"stretch", "contain", "cover"} else "contain"
        cache_key = (path.strip(), max(1, width), max(1, height), normalized_mode, bool(smooth))
        cached = self._scaled_pixmap_cache.get(cache_key, _MISSING_PIXMAP)
        if cached is not _MISSING_PIXMAP:
            self._scaled_pixmap_cache.move_to_end(cache_key)
            return cached
        aspect_mode = (
            Qt.IgnoreAspectRatio
            if normalized_mode == "stretch"
            else Qt.KeepAspectRatioByExpanding
            if normalized_mode == "cover"
            else Qt.KeepAspectRatio
        )
        transform_mode = Qt.SmoothTransformation if smooth else Qt.FastTransformation
        pixmap = source.scaled(cache_key[1], cache_key[2], aspect_mode, transform_mode)
        self._remember(self._scaled_pixmap_cache, cache_key, pixmap, self.scaled_limit)
        return pixmap

    def _load_source(self, path: str) -> QPixmap | None:
        pixmap = QPixmap(path)
        if pixmap.isNull():
            return None
        return pixmap

    def _remember(self, cache: OrderedDict, key, pixmap, limit: int) -> None:
        cache[key] = pixmap
        cache.move_to_end(key)
        while len(cache) > limit:
            cache.popitem(last=False)


def draw_field_pixmap(
    painter: QPainter,
    pixmap: QPixmap,
    target: QRectF,
    field: NodeField,
    *,
    smooth: bool = True,
    scaled_pixmap: QPixmap | None = None,
) -> None:
    if pixmap.isNull() or target.width() <= 0 or target.height() <= 0:
        return
    painter.save()
    pixel_art = is_pixel_art_image_path(field.image_path)
    painter.setRenderHint(QPainter.SmoothPixmapTransform, smooth and not pixel_art)
    fit = field.image_fit if field.image_fit in IMAGE_FIT_MODES else "stretch"
    if fit == "contain":
        _draw_contain(painter, pixmap, target, scaled_pixmap, pixel_art=pixel_art)
    elif fit == "cover":
        _draw_cover(painter, pixmap, target, scaled_pixmap, pixel_art=pixel_art)
    elif fit == "nine_slice":
        _draw_nine_slice(painter, pixmap, target, field)
    else:
        if scaled_pixmap is not None and not scaled_pixmap.isNull():
            painter.drawPixmap(target.toRect(), scaled_pixmap, scaled_pixmap.rect())
        else:
            painter.drawPixmap(target.toRect(), pixmap, pixmap.rect())
    painter.restore()


def is_pixel_art_image_path(path: str) -> bool:
    text = str(path or "").strip()
    if not text:
        return False
    reader = QImageReader(text)
    if reader.text("GameDesignerPixelArt").strip() == "1":
        return True
    parts = {part.lower() for part in Path(text).parts}
    if "pixel" in parts:
        return True
    return False


def _draw_contain(
    painter: QPainter,
    pixmap: QPixmap,
    target: QRectF,
    scaled_pixmap: QPixmap | None = None,
    *,
    pixel_art: bool = False,
) -> None:
    scaled = scaled_pixmap or pixmap.scaled(
        max(1, int(target.width())),
        max(1, int(target.height())),
        Qt.KeepAspectRatio,
        Qt.FastTransformation if pixel_art else Qt.SmoothTransformation,
    )
    x = target.x() + (target.width() - scaled.width()) / 2
    y = target.y() + (target.height() - scaled.height()) / 2
    painter.drawPixmap(int(x), int(y), scaled)


def _draw_cover(
    painter: QPainter,
    pixmap: QPixmap,
    target: QRectF,
    scaled_pixmap: QPixmap | None = None,
    *,
    pixel_art: bool = False,
) -> None:
    scaled = scaled_pixmap or pixmap.scaled(
        max(1, int(target.width())),
        max(1, int(target.height())),
        Qt.KeepAspectRatioByExpanding,
        Qt.FastTransformation if pixel_art else Qt.SmoothTransformation,
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
