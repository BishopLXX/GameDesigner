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
        padding: 9px 18px;
        margin-right: 6px;
        border-top-left-radius: 12px;
        border-top-right-radius: 12px;
    }}
    QTabBar::tab:selected {{
        background: {colors["panel_alt"]};
        color: {colors["text"]};
        border-color: {colors["accent"]};
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
    """
