from __future__ import annotations

from .theme_dark import PALETTE as DARK_PALETTE
from .theme_light import PALETTE as LIGHT_PALETTE

THEMES: dict[str, dict[str, str]] = {
    "dark": DARK_PALETTE,
    "light": LIGHT_PALETTE,
}


def palette(name: str) -> dict[str, str]:
    return THEMES.get(name, THEMES["dark"])


def stylesheet(name: str) -> str:
    colors = palette(name)
    return f"""
    QMainWindow, QDialog {{
        background: {colors["window"]};
        color: {colors["text"]};
        font-family: "Noto Sans SC", "Microsoft YaHei UI", "Microsoft YaHei", "Segoe UI", sans-serif;
        font-size: 10pt;
    }}
    QWidget {{
        color: {colors["text"]};
        font-family: "Noto Sans SC", "Microsoft YaHei UI", "Microsoft YaHei", "Segoe UI", sans-serif;
    }}
    QMenuBar {{
        background: {colors["window"]};
        color: {colors["text"]};
        padding: 4px 8px;
    }}
    QMenuBar::item {{
        padding: 7px 12px;
        border-radius: 8px;
    }}
    QMenuBar::item:selected {{
        background: {colors["control_hover"]};
    }}
    QMenu {{
        background: {colors["panel"]};
        color: {colors["text"]};
        border: 1px solid {colors["hairline"]};
        border-radius: 10px;
        padding: 7px;
    }}
    QMenu::item {{
        padding: 8px 28px 8px 12px;
        border-radius: 7px;
    }}
    QMenu::item:selected {{
        background: {colors["accent"]};
        color: #FFFFFF;
    }}
    QTabWidget::pane {{
        border: 0;
        top: -1px;
    }}
    QTabBar::tab {{
        background: {colors["panel"]};
        color: {colors["text_muted"]};
        border: 1px solid {colors["hairline"]};
        border-bottom: 0;
        padding: 6px 8px 6px 13px;
        margin-right: 2px;
        border-top-left-radius: 8px;
        border-top-right-radius: 8px;
    }}
    QTabBar::tab:selected {{
        background: {colors["panel_alt"]};
        color: {colors["text"]};
        border-color: {colors["accent"]};
    }}
    QWidget#tabCloseHolder {{
        background: transparent;
        border: 0;
        padding: 0;
        margin: 0;
    }}
    QToolButton#tabCloseButton {{
        background: transparent;
        color: {colors["text"]};
        border: 0;
        border-radius: 7px;
        padding: 0;
        font-size: 11pt;
        font-weight: 500;
    }}
    QToolButton#tabCloseButton:hover {{
        background: {colors["control_hover"]};
        color: {colors["text"]};
        border: 0;
    }}
    QToolBar {{
        background: {colors["window"]};
        border: 0;
        spacing: 8px;
        padding: 8px 10px;
    }}
    QToolButton, QPushButton {{
        background: {colors["control"]};
        color: {colors["text"]};
        border: 1px solid {colors["hairline"]};
        border-radius: 10px;
        padding: 8px 13px;
    }}
    QToolButton:hover, QPushButton:hover {{
        background: {colors["control_hover"]};
        border-color: {colors["accent"]};
    }}
    QPushButton#accentButton, QToolButton#accentButton {{
        background: {colors["accent"]};
        border-color: {colors["accent"]};
        color: #FFFFFF;
    }}
    QPushButton#accentButton:hover, QToolButton#accentButton:hover {{
        background: {colors["accent_hover"]};
    }}
    QToolButton#compactToolButton {{
        background: {colors["control"]};
        color: {colors["text"]};
        border: 1px solid {colors["hairline"]};
        border-radius: 8px;
        padding: 5px 9px;
    }}
    QToolButton#compactToolButton:hover {{
        background: {colors["control_hover"]};
        border-color: {colors["accent"]};
    }}
    QToolButton#compactToolButton::menu-indicator {{
        image: none;
        width: 0;
    }}
    QToolButton#exportPinButton {{
        background: transparent;
        color: {colors["text_muted"]};
        border: 1px solid transparent;
        border-radius: 7px;
        padding: 0;
    }}
    QToolButton#exportPinButton:hover {{
        background: {colors["control_hover"]};
        border-color: {colors["hairline"]};
    }}
    QToolButton#exportPinButton:checked {{
        background: {colors["control"]};
        color: {colors["text"]};
        border-color: {colors["accent"]};
    }}
    QLineEdit, QTextEdit, QPlainTextEdit, QComboBox, QSpinBox, QDoubleSpinBox {{
        background: {colors["panel_alt"]};
        color: {colors["text"]};
        border: 1px solid {colors["hairline"]};
        border-radius: 9px;
        padding: 7px 9px;
        selection-background-color: {colors["blue"]};
    }}
    QLineEdit:focus, QTextEdit:focus, QPlainTextEdit:focus, QComboBox:focus,
    QSpinBox:focus, QDoubleSpinBox:focus {{
        border-color: {colors["blue"]};
    }}
    QLabel {{
        color: {colors["text"]};
    }}
    QGroupBox {{
        border: 1px solid {colors["hairline"]};
        border-radius: 12px;
        margin-top: 18px;
        padding: 12px 10px 10px 10px;
        background: {colors["panel"]};
    }}
    QGroupBox::title {{
        subcontrol-origin: margin;
        left: 12px;
        padding: 0 6px;
        color: {colors["text_muted"]};
    }}
    QListWidget, QTreeWidget, QTableWidget {{
        background: {colors["panel"]};
        color: {colors["text"]};
        border: 1px solid {colors["hairline"]};
        border-radius: 12px;
        outline: 0;
    }}
    QListWidget::item {{
        padding: 8px;
        border-radius: 8px;
    }}
    QListWidget::item:selected {{
        background: {colors["accent"]};
        color: #FFFFFF;
    }}
    QStatusBar {{
        background: {colors["window"]};
        color: {colors["text_muted"]};
        border-top: 1px solid {colors["hairline"]};
    }}
    QScrollBar:vertical, QScrollBar:horizontal {{
        background: transparent;
        border: 0;
        width: 11px;
        height: 11px;
    }}
    QScrollBar::handle:vertical, QScrollBar::handle:horizontal {{
        background: {colors["hairline"]};
        border-radius: 5px;
        min-height: 30px;
        min-width: 30px;
    }}
    QScrollBar::add-line, QScrollBar::sub-line {{
        width: 0;
        height: 0;
    }}
    QWidget#topBar {{
        background: {colors["window"]};
        border-bottom: 1px solid {colors["hairline"]};
        min-height: 30px;
        max-height: 30px;
    }}
    QLabel#appIcon {{
        background: transparent;
        border: 0;
        padding: 0;
    }}
    QToolButton#topMenuButton, QToolButton#topActionButton, QToolButton#windowButton, QToolButton#closeButton {{
        background: transparent;
        color: {colors["text"]};
        border: 0;
        border-radius: 6px;
        padding: 3px 8px;
        min-height: 20px;
    }}
    QToolButton#topActionButton {{
        color: {colors["text_muted"]};
    }}
    QToolButton#windowButton, QToolButton#closeButton {{
        padding: 0;
        color: {colors["text_muted"]};
    }}
    QToolButton#topMenuButton:hover, QToolButton#topActionButton:hover,
    QToolButton#windowButton:hover, QToolButton#closeButton:hover {{
        background: {colors["control_hover"]};
        border: 0;
    }}
    QToolButton#topActionButton:checked {{
        background: {colors["control"]};
        color: {colors["text"]};
    }}
    QToolButton#topMenuButton::menu-indicator {{
        image: none;
        width: 0;
    }}
    QWidget#canvasNav {{
        background: {colors["panel"]};
        border: 1px solid {colors["hairline"]};
        border-radius: 10px;
    }}
    QToolButton#canvasNavButton {{
        background: transparent;
        color: {colors["text"]};
        border: 0;
        border-radius: 7px;
        padding: 4px 9px;
    }}
    QToolButton#canvasNavButton:hover {{
        background: {colors["control_hover"]};
    }}
    """
