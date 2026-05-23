from __future__ import annotations

from PySide6.QtWidgets import QWidget

from .storage import AppSettings, save_settings


def restore_window_layout(widget: QWidget, key: str) -> None:
    settings = _window_settings(widget)
    if settings is None:
        return
    layout = settings.window_layouts.get(key)
    if not layout:
        return
    width = max(180, int(layout.get("width", 0)))
    height = max(120, int(layout.get("height", 0)))
    if width > 0 and height > 0:
        widget.resize(width, height)
    if "x" in layout and "y" in layout:
        widget.move(int(layout["x"]), int(layout["y"]))


def save_window_layout(widget: QWidget, key: str, *, persist: bool = True) -> None:
    settings = _window_settings(widget)
    if settings is None:
        return
    geometry = widget.geometry()
    settings.window_layouts[key] = {
        "x": float(geometry.x()),
        "y": float(geometry.y()),
        "width": float(geometry.width()),
        "height": float(geometry.height()),
    }
    if persist:
        save_settings(settings)


def _window_settings(widget: QWidget) -> AppSettings | None:
    current: QWidget | None = widget
    while current is not None:
        settings = getattr(current, "settings", None)
        if isinstance(settings, AppSettings):
            return settings
        current = current.parentWidget()
    window = widget.window()
    settings = getattr(window, "settings", None)
    if isinstance(settings, AppSettings):
        return settings
    return None
