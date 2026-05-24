from __future__ import annotations

from pathlib import Path
from typing import Any

from PySide6.QtCore import QByteArray
from PySide6.QtWidgets import QWidget

from .storage import AppSettings, load_project_window_layouts, save_project_window_layouts, save_settings


def restore_window_layout(widget: QWidget, key: str, project_path: str | Path | None = None) -> None:
    project_path = project_path or _window_project_path(widget)
    layout = _project_window_layout(project_path, key)
    if layout is None:
        settings = _window_settings(widget)
        if settings is None:
            return
        layout = settings.window_layouts.get(key)
    _apply_window_layout(widget, layout)


def save_window_layout(
    widget: QWidget,
    key: str,
    *,
    persist: bool = True,
    project_path: str | Path | None = None,
) -> None:
    project_path = project_path or _window_project_path(widget)
    layout = _capture_window_layout(widget)
    if project_path:
        layouts = load_project_window_layouts(project_path)
        layouts[key] = layout
        if persist:
            save_project_window_layouts(project_path, layouts)
        return
    settings = _window_settings(widget)
    if settings is None:
        return
    settings.window_layouts[key] = layout
    if persist:
        save_settings(settings)


def _project_window_layout(project_path: str | Path | None, key: str) -> dict[str, Any] | None:
    if not project_path:
        return None
    return load_project_window_layouts(project_path).get(key)


def _apply_window_layout(widget: QWidget, layout: dict[str, Any] | None) -> None:
    if not layout:
        return
    geometry = str(layout.get("geometry") or "")
    restored_geometry = False
    if geometry:
        try:
            restored_geometry = widget.restoreGeometry(QByteArray.fromBase64(geometry.encode("ascii")))
        except (TypeError, ValueError):
            pass
    width = max(180, int(layout.get("width", 0)))
    height = max(120, int(layout.get("height", 0)))
    if width > 0 and height > 0:
        widget.resize(width, height)
    if "x" in layout and "y" in layout and not restored_geometry:
        widget.move(int(layout["x"]), int(layout["y"]))


def _capture_window_layout(widget: QWidget) -> dict[str, Any]:
    geometry = widget.geometry()
    return {
        "x": float(geometry.x()),
        "y": float(geometry.y()),
        "width": float(geometry.width()),
        "height": float(geometry.height()),
        "geometry": bytes(widget.saveGeometry().toBase64()).decode("ascii"),
    }


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


def _window_project_path(widget: QWidget) -> Path | None:
    current: QWidget | None = widget
    while current is not None:
        project_path = getattr(current, "project_path", None)
        if project_path:
            return Path(project_path)
        current = current.parentWidget()
    window = widget.window()
    project_path = getattr(window, "project_path", None)
    if project_path:
        return Path(project_path)
    return None
