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
    QToolButton#bindingToolButton {{
        background: {colors["panel_alt"]};
        color: {colors["text_muted"]};
        border: 1px solid {colors["hairline"]};
        border-radius: 8px;
        padding: 0;
        font-weight: 600;
    }}
    QToolButton#bindingToolButton:hover {{
        background: {colors["control_hover"]};
        border-color: {colors["accent"]};
        color: {colors["text"]};
    }}
    QToolButton#bindingToolButton:checked {{
        background: {colors["blue_soft"]};
        color: {colors["blue"]};
        border-color: {colors["blue"]};
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
    QToolButton#colorPickButton, QToolButton#alignToolButton {{
        background: {colors["panel_alt"]};
        color: {colors["text"]};
        border: 1px solid {colors["hairline"]};
        border-radius: 8px;
        padding: 0;
    }}
    QToolButton#colorPickButton:hover, QToolButton#alignToolButton:hover {{
        background: {colors["control_hover"]};
        border-color: {colors["accent"]};
    }}
    QToolButton#alignToolButton:checked {{
        background: {colors["control"]};
        border-color: {colors["blue"]};
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
        alternate-background-color: {colors["window"]};
        color: {colors["text"]};
        border: 1px solid {colors["hairline"]};
        border-radius: 12px;
        outline: 0;
    }}
    QTreeWidget::item, QTableWidget::item {{
        padding: 5px 7px;
    }}
    QListWidget::item {{
        padding: 8px;
        border-radius: 8px;
    }}
    QListWidget::item:selected, QTreeWidget::item:selected, QTableWidget::item:selected {{
        background: {colors["accent"]};
        color: #FFFFFF;
    }}
    QHeaderView {{
        background: {colors["panel"]};
    }}
    QHeaderView::section {{
        background: {colors["panel_alt"]};
        color: {colors["text"]};
        border: 0;
        border-right: 1px solid {colors["hairline"]};
        border-bottom: 1px solid {colors["hairline"]};
        padding: 7px;
        font-weight: 600;
    }}
    QHeaderView::section:vertical {{
        color: {colors["text_muted"]};
        font-weight: 400;
    }}
    QTableCornerButton::section {{
        background: {colors["panel_alt"]};
        border: 0;
        border-right: 1px solid {colors["hairline"]};
        border-bottom: 1px solid {colors["hairline"]};
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
    QWidget#canvasFunctionBar {{
        background: {colors["window"]};
        border-bottom: 1px solid {colors["hairline"]};
        min-height: 34px;
    }}
    QStackedWidget#aiAssistantStack {{
        background: {colors["window"]};
        border-left: 1px solid {colors["hairline"]};
    }}
    QWidget#aiAssistantPanel {{
        background: {colors["panel"]};
        border-left: 1px solid {colors["hairline"]};
    }}
    QWidget#aiAssistantCollapsed {{
        background: {colors["window"]};
        border-left: 1px solid {colors["hairline"]};
    }}
    QLabel#aiAssistantTitle {{
        color: {colors["text"]};
        font-weight: 700;
        font-size: 10pt;
    }}
    QLabel#aiAssistantActivityLabel {{
        color: {colors["text_muted"]};
        font-size: 9pt;
        padding: 0 2px 2px 2px;
    }}
    QProgressBar#aiAssistantBusyBar {{
        background: {colors["panel_alt"]};
        border: 0;
        border-radius: 2px;
        min-height: 4px;
        max-height: 4px;
    }}
    QProgressBar#aiAssistantBusyBar::chunk {{
        background: {colors["blue"]};
        border-radius: 2px;
    }}
    QPlainTextEdit#aiAssistantActivityLog {{
        background: {colors["panel_alt"]};
        color: {colors["text_muted"]};
        border: 1px solid {colors["hairline"]};
        border-radius: 8px;
        padding: 8px;
        font-family: Consolas, "Cascadia Mono", monospace;
        font-size: 8pt;
    }}
    QToolButton#aiAssistantCollapseButton {{
        background: transparent;
        color: {colors["text_muted"]};
        border: 1px solid transparent;
        border-radius: 7px;
        padding: 3px 8px;
    }}
    QToolButton#aiAssistantCollapseButton:hover {{
        background: {colors["control_hover"]};
        color: {colors["text"]};
        border-color: {colors["hairline"]};
    }}
    QToolButton#aiAssistantHandleButton {{
        background: {colors["control"]};
        color: {colors["text"]};
        border: 1px solid {colors["hairline"]};
        border-radius: 8px;
        padding: 9px 4px;
        font-weight: 600;
        min-width: 30px;
    }}
    QToolButton#aiAssistantHandleButton:hover {{
        background: {colors["control_hover"]};
        border-color: {colors["accent"]};
    }}
    QWidget#nodePreviewPanel {{
        background: {colors["panel"]};
        border: 1px solid {colors["hairline"]};
        border-radius: 12px;
    }}
    QToolButton#nodePreviewHandleButton {{
        background: {colors["control"]};
        color: {colors["text"]};
        border: 1px solid {colors["hairline"]};
        border-radius: 10px;
        padding: 0;
        min-width: 44px;
        min-height: 44px;
        font-weight: 600;
    }}
    QToolButton#nodePreviewHandleButton:hover {{
        background: {colors["control_hover"]};
        border-color: {colors["accent"]};
    }}
    QToolButton#nodePreviewHandleButton:checked {{
        background: {colors["blue_soft"]};
        color: {colors["blue"]};
        border-color: {colors["blue"]};
    }}
    QLabel#nodePreviewTypeLabel {{
        color: {colors["text_muted"]};
        font-size: 9pt;
        font-weight: 600;
    }}
    QLabel#nodePreviewTitle {{
        color: {colors["text"]};
        font-size: 10pt;
        font-weight: 700;
    }}
    QToolButton#nodePreviewCloseButton {{
        background: transparent;
        color: {colors["text_muted"]};
        border: 1px solid transparent;
        border-radius: 7px;
        padding: 0;
    }}
    QToolButton#nodePreviewCloseButton:hover {{
        background: {colors["control_hover"]};
        color: {colors["text"]};
        border-color: {colors["hairline"]};
    }}
    QToolButton#nodePreviewMiniButton {{
        background: {colors["control"]};
        color: {colors["text_muted"]};
        border: 1px solid {colors["hairline"]};
        border-radius: 7px;
        padding: 4px 9px;
        font-size: 9pt;
    }}
    QToolButton#nodePreviewMiniButton:hover {{
        background: {colors["control_hover"]};
        color: {colors["text"]};
        border-color: {colors["accent"]};
    }}
    QScrollArea#nodePreviewScroll {{
        background: transparent;
        border: 0;
    }}
    QWidget#nodePreviewBody {{
        background: transparent;
    }}
    QFrame#nodePreviewCard, QFrame#nodePreviewCanvasFrame, QFrame#nodePreviewVisualCanvas, QFrame#nodePreviewImageFrame {{
        background: {colors["panel_alt"]};
        border: 1px solid {colors["hairline"]};
        border-radius: 10px;
    }}
    QFrame#nodePreviewVisualField {{
        background: {colors["panel"]};
        border: 1px solid {colors["hairline"]};
        border-radius: 9px;
    }}
    QLabel#nodePreviewEmptyLabel {{
        color: {colors["text_muted"]};
        padding: 18px;
    }}
    QLabel#nodePreviewFieldName {{
        color: {colors["text"]};
        font-weight: 700;
    }}
    QLabel#nodePreviewFieldType, QLabel#nodePreviewMetaKey {{
        color: {colors["text_muted"]};
    }}
    QLabel#nodePreviewMetaValue, QLabel#nodePreviewValue, QLabel#nodePreviewSmallText {{
        color: {colors["text"]};
    }}
    QTextBrowser#nodePreviewBrowser {{
        background: {colors["panel_alt"]};
        color: {colors["text"]};
        border: 1px solid {colors["hairline"]};
        border-radius: 9px;
        padding: 8px;
    }}
    QLabel#canvasFunctionLabel {{
        color: {colors["text_muted"]};
        padding-right: 6px;
        font-size: 9pt;
    }}
    QToolButton#canvasFunctionButton, QToolButton#canvasFunctionToggle, QToolButton#canvasFunctionSubToggle, QToolButton#canvasFunctionNavButton {{
        background: transparent;
        color: {colors["text_muted"]};
        border: 1px solid transparent;
        border-radius: 8px;
        padding: 4px 10px;
    }}
    QToolButton#canvasFunctionNavButton {{
        background: {colors["panel"]};
        color: {colors["text"]};
        border-color: {colors["hairline"]};
    }}
    QToolButton#canvasFunctionSubToggle {{
        padding: 4px 8px;
    }}
    QToolButton#canvasFunctionButton:hover, QToolButton#canvasFunctionToggle:hover, QToolButton#canvasFunctionSubToggle:hover, QToolButton#canvasFunctionNavButton:hover {{
        background: {colors["control_hover"]};
        color: {colors["text"]};
        border-color: {colors["hairline"]};
    }}
    QToolButton#canvasFunctionToggle:checked, QToolButton#canvasFunctionSubToggle:checked {{
        background: {colors["control"]};
        color: {colors["text"]};
        border-color: {colors["accent"]};
    }}
    QSpinBox#canvasFunctionSpin {{
        min-width: 88px;
        max-width: 112px;
        padding-right: 18px;
    }}
    """
