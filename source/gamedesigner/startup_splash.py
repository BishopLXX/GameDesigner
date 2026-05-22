from __future__ import annotations

from PySide6.QtCore import QRectF, QSize, Qt
from PySide6.QtGui import QColor, QFont, QPainter, QPainterPath, QPen, QPixmap
from PySide6.QtWidgets import QSplashScreen


STARTUP_SPLASH_SIZE = QSize(520, 190)


class StartupSplash(QSplashScreen):
    def __init__(self) -> None:
        super().__init__()
        self._progress = 0
        self._message = "准备启动..."
        self.setWindowFlag(Qt.WindowStaysOnTopHint, True)
        self._apply_pixmap()

    @property
    def progress(self) -> int:
        return self._progress

    @property
    def message(self) -> str:
        return self._message

    def set_progress(self, value: int, message: str | None = None) -> None:
        progress = max(0, min(100, int(value)))
        text = self._message
        if message is not None:
            stripped = message.strip()
            if stripped:
                text = stripped
        if progress == self._progress and text == self._message:
            return
        self._progress = progress
        self._message = text
        self._apply_pixmap()
        self.repaint()

    def _apply_pixmap(self) -> None:
        self.setPixmap(_build_splash_pixmap(self._progress, self._message))


def build_startup_splash() -> StartupSplash:
    return StartupSplash()


def _build_splash_pixmap(progress: int, message: str) -> QPixmap:
    pixmap = QPixmap(STARTUP_SPLASH_SIZE)
    pixmap.fill(Qt.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.Antialiasing, True)

    rect = QRectF(10, 10, STARTUP_SPLASH_SIZE.width() - 20, STARTUP_SPLASH_SIZE.height() - 20)
    shadow = QPainterPath()
    shadow.addRoundedRect(rect.adjusted(0, 8, 0, 8), 22, 22)
    shadow_color = QColor("#000000")
    shadow_color.setAlpha(46)
    painter.fillPath(shadow, shadow_color)

    panel = QPainterPath()
    panel.addRoundedRect(rect, 22, 22)
    painter.fillPath(panel, QColor("#15151B"))
    painter.setPen(QPen(QColor("#2E2E38"), 1))
    painter.drawPath(panel)

    icon_rect = QRectF(38, 46, 56, 56)
    icon_path = QPainterPath()
    icon_path.addRoundedRect(icon_rect, 14, 14)
    painter.fillPath(icon_path, QColor("#0A84FF"))
    painter.setPen(QColor("#FFFFFF"))
    icon_font = QFont()
    icon_font.setPointSize(20)
    icon_font.setBold(True)
    painter.setFont(icon_font)
    painter.drawText(icon_rect, Qt.AlignCenter, "GD")

    title_font = QFont()
    title_font.setPointSize(18)
    title_font.setBold(True)
    painter.setFont(title_font)
    painter.setPen(QColor("#F5F5F7"))
    painter.drawText(QRectF(118, 47, 300, 34), Qt.AlignLeft | Qt.AlignVCenter, "GameDesigner")

    progress_font = QFont()
    progress_font.setPointSize(9)
    painter.setFont(progress_font)
    painter.setPen(QColor("#6F6F7A"))
    painter.drawText(QRectF(426, 49, 52, 20), Qt.AlignRight | Qt.AlignVCenter, f"{progress:>3d}%")

    message_font = QFont()
    message_font.setPointSize(10)
    painter.setFont(message_font)
    painter.setPen(QColor("#A1A1AA"))
    painter.drawText(QRectF(120, 84, 350, 26), Qt.AlignLeft | Qt.AlignVCenter, message)

    bar_rect = QRectF(120, 126, 314, 5)
    bar_bg = QPainterPath()
    bar_bg.addRoundedRect(bar_rect, 2.5, 2.5)
    painter.fillPath(bar_bg, QColor("#30303B"))
    fill_width = bar_rect.width() * (progress / 100.0)
    if fill_width > 0:
        bar_fg = QPainterPath()
        bar_fg.addRoundedRect(QRectF(bar_rect.x(), bar_rect.y(), fill_width, bar_rect.height()), 2.5, 2.5)
        painter.fillPath(bar_fg, QColor("#0A84FF"))
    painter.end()
    return pixmap
