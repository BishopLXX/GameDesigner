from __future__ import annotations

import copy
from ctypes import wintypes
import re
import sys
from pathlib import Path
from typing import Any, Callable

from PySide6.QtCore import QPoint, QPointF, QRect, QSize, Qt, QTimer, QUrl, Signal
from PySide6.QtGui import (
    QAction,
    QCloseEvent,
    QCursor,
    QDesktopServices,
    QFontMetrics,
    QIcon,
    QKeySequence,
    QPixmap,
    QShortcut,
)
from PySide6.QtWidgets import (
    QApplication,
    QButtonGroup,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QMenu,
    QMessageBox,
    QSpinBox,
    QSizePolicy,
    QStatusBar,
    QTabBar,
    QTabWidget,
    QToolButton,
    QInputDialog,
    QStackedLayout,
    QStackedWidget,
    QWidget,
    QVBoxLayout,
)

from .ai_canvas_tools import (
    AI_READ_ONLY_TOOL_NAMES,
    execute_read_only_ai_canvas_tool,
    format_ai_tool_results,
    validate_ai_canvas_tool_call,
)
from .canvas_io import import_canvas_sheet
from .image_canvas import (
    IMAGE_CANVAS_EDIT_EDGE_LABEL,
    IMAGE_CANVAS_IMAGE_FIELD,
    apply_image_output_result,
    build_image_canvas_request,
    convert_output_node_to_reference,
    edge_with_label,
    find_image_output_node,
    new_image_output_node_from_previous,
)
from .data_canvas import apply_template_to_node, data_canvas_template, sync_data_canvas, sync_locked_template_nodes
from .models import (
    EDGE_STYLES,
    BlueprintGroup,
    CanvasData,
    Edge,
    Node,
    NodeField,
    NodeTemplate,
    ProjectData,
    DesignNote,
    default_label_node,
    default_image_canvas_nodes,
    default_pixel_canvas_nodes,
    default_project,
    new_id,
)
from .project_history import ProjectHistory, ProjectSnapshot
from .qt_canvas import NodeGraphView
from .qt_fonts import configure_fonts
from .ui.node_preview_panel import NodePreviewPanel
from .ui.sequence_frame_dialog import SequenceFrameDialog
from .qt_theme import stylesheet
from .node_visuals import visual_node_size
from .storage import (
    PROJECT_SUFFIX,
    LEGACY_PROJECT_SUFFIX,
    default_project_path,
    load_project,
    load_settings,
    save_project,
    save_settings,
    project_bundle_dir,
)


AI_CHILD_NODE_GAP_X = 100.0
AI_CHILD_NODE_GAP_Y = 44.0
AI_NODE_FALLBACK_WIDTH = 310.0
AI_NODE_FALLBACK_HEIGHT = 140.0
EDGE_LABEL_MAX_LENGTH = 24
AI_REFERENCE_COLOR_TERMS = [
    "绿色",
    "黄色",
    "红色",
    "蓝色",
    "紫色",
    "橙色",
    "黑色",
    "白色",
    "灰色",
    "金色",
    "银色",
    "粉色",
    "青色",
    "棕色",
    "绿",
    "黄",
    "红",
    "蓝",
    "紫",
    "橙",
    "黑",
    "白",
    "灰",
    "金",
    "银",
    "粉",
    "青",
    "棕",
]
from .ui.data_canvas_table import DataCanvasTableWidget
from .window_layouts import restore_window_layout, save_window_layout


WELCOME_PROJECT_NAME = "开始"
WELCOME_NEW_NODE_ID = "welcome_new_project"
WELCOME_GUIDE_NODE_ID = "welcome_guide"
WELCOME_NO_RECENT_NODE_ID = "welcome_no_recent"
WELCOME_LAYOUT_NODE_IDS = {WELCOME_GUIDE_NODE_ID, WELCOME_NEW_NODE_ID, WELCOME_NO_RECENT_NODE_ID}
TAB_MIN_WIDTH = 86
TAB_MAX_WIDTH = 340
WM_NCHITTEST = 0x0084
HTCAPTION = 2
HTLEFT = 10
HTRIGHT = 11
HTTOP = 12
HTTOPLEFT = 13
HTTOPRIGHT = 14
HTBOTTOM = 15
HTBOTTOMLEFT = 16
HTBOTTOMRIGHT = 17
RESIZE_BORDER = 6


def _app_icon_path() -> Path | None:
    candidates: list[Path] = []
    frozen_dir = getattr(sys, "_MEIPASS", None)
    if frozen_dir:
        candidates.append(Path(frozen_dir) / "icon.png")
    if getattr(sys, "frozen", False):
        executable_dir = Path(sys.executable).resolve().parent
        candidates.extend([executable_dir / "icon.png", executable_dir.parent / "icon.png"])
    project_root = Path(__file__).resolve().parents[2]
    candidates.append(project_root / "icon.png")
    for candidate in candidates:
        if candidate.exists():
            return candidate
    return None


class ProjectPage(QWidget):
    parentJumpRequested = Signal()
    returnCloseRequested = Signal()
    resetViewRequested = Signal()
    previewCanvasOpenRequested = Signal(str)
    dataLayoutRequested = Signal(str)
    dataGridRowsRequested = Signal(int)
    dataRowStyleRequested = Signal(str)
    imageGenerateRequested = Signal()
    imageRetouchRequested = Signal()

    def __init__(
        self,
        project: ProjectData,
        path: Path | None,
        dirty: bool,
        theme: str,
        canvas_data: CanvasData | None = None,
        source_canvas_id: str = "",
        is_welcome: bool = False,
        welcome_actions: dict[str, str] | None = None,
    ) -> None:
        super().__init__()
        self.project = project
        self.project.ensure_canvas_structure()
        self.canvas_data = canvas_data or self.project.root_canvas()
        self.canvas_id = self.canvas_data.id
        self.source_canvas_id = source_canvas_id
        self.path = path
        self.dirty = dirty
        self.theme = theme
        self.is_welcome = is_welcome
        self.welcome_actions = welcome_actions or {}
        self.selected_node_id: str | None = None
        self.selected_edge_id: str | None = None
        self.active_template_id: str | None = None
        self.canvas = NodeGraphView(
            self.canvas_data,
            self.theme,
            read_only=is_welcome,
            allow_node_drag=is_welcome,
            templates=self.project.templates,
        )
        self.table_view = DataCanvasTableWidget(self)
        self.table_view.set_canvas(self.project, self.canvas_data)
        self.table_view.setVisible(self.canvas_data.is_data_canvas() and self.canvas_data.data_layout == "table")
        if is_welcome:
            self.canvas.set_folder_action_node_ids({
                node_id for node_id, action in self.welcome_actions.items() if action != "new"
            })
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        self.function_bar = QWidget(self)
        self.function_bar.setObjectName("canvasFunctionBar")
        function_layout = QHBoxLayout(self.function_bar)
        function_layout.setContentsMargins(10, 8, 10, 8)
        function_layout.setSpacing(8)
        self.parent_button = QToolButton(self.function_bar)
        self.parent_button.setObjectName("canvasFunctionNavButton")
        self.parent_button.setText("回跳")
        self.parent_button.setToolTip("跳到父画布")
        self.parent_button.setAutoRaise(True)
        self.parent_button.clicked.connect(self.parentJumpRequested.emit)
        self.return_button = QToolButton(self.function_bar)
        self.return_button.setObjectName("canvasFunctionNavButton")
        self.return_button.setText("退回")
        self.return_button.setToolTip("保存并关闭当前画布，回到上一个画布")
        self.return_button.setAutoRaise(True)
        self.return_button.clicked.connect(self.returnCloseRequested.emit)
        function_layout.addWidget(self.parent_button)
        function_layout.addWidget(self.return_button)
        function_layout.addStretch(1)

        self.layout_button_group = QButtonGroup(self.function_bar)
        self.layout_button_group.setExclusive(True)
        self.horizontal_layout_button = self._function_toggle_button("水平", "水平排序显示", "horizontal")
        self.grid_layout_button = self._function_toggle_button("网格", "网格卡片显示", "grid")
        self.table_layout_button = self._function_toggle_button("表格", "表格预览显示", "table")
        function_layout.addWidget(self.horizontal_layout_button)
        self.row_style_button_group = QButtonGroup(self.function_bar)
        self.row_style_button_group.setExclusive(True)
        self.independent_row_button = self._row_style_toggle_button("独立", "每条数据显示为独立卡片", "independent")
        self.thumbnail_row_button = self._row_style_toggle_button("缩略", "以表格缩略行显示数据", "thumbnail")
        function_layout.addWidget(self.independent_row_button)
        function_layout.addWidget(self.thumbnail_row_button)
        function_layout.addWidget(self.grid_layout_button)
        self.grid_rows_spin = QSpinBox(self.function_bar)
        self.grid_rows_spin.setObjectName("canvasFunctionSpin")
        self.grid_rows_spin.setMinimum(0)
        self.grid_rows_spin.setMaximum(999)
        self.grid_rows_spin.setSingleStep(1)
        self.grid_rows_spin.setToolTip("网格每列行数，0 为自动")
        self.grid_rows_spin.setPrefix("行数 ")
        self.grid_rows_spin.valueChanged.connect(self._emit_grid_rows_requested)
        function_layout.addWidget(self.grid_rows_spin)
        function_layout.addWidget(self.table_layout_button)
        self.reset_view_button = QToolButton(self.function_bar)
        self.reset_view_button.setObjectName("canvasFunctionButton")
        self.reset_view_button.setText("重置")
        self.reset_view_button.setToolTip("重置视图")
        self.reset_view_button.setAutoRaise(True)
        self.reset_view_button.clicked.connect(self.resetViewRequested.emit)
        function_layout.addWidget(self.reset_view_button)
        self.image_generate_button = QToolButton(self.function_bar)
        self.image_generate_button.setObjectName("canvasFunctionButton")
        self.image_generate_button.setText("生图")
        self.image_generate_button.setToolTip("生成当前生图画布输出")
        self.image_generate_button.setAutoRaise(True)
        self.image_generate_button.clicked.connect(self.imageGenerateRequested.emit)
        function_layout.addWidget(self.image_generate_button)
        self.image_retouch_button = QToolButton(self.function_bar)
        self.image_retouch_button.setObjectName("canvasFunctionButton")
        self.image_retouch_button.setText("修图")
        self.image_retouch_button.setToolTip("把当前输出转成参考图并创建新的输出")
        self.image_retouch_button.setAutoRaise(True)
        self.image_retouch_button.clicked.connect(self.imageRetouchRequested.emit)
        function_layout.addWidget(self.image_retouch_button)
        layout.addWidget(self.function_bar)
        self.content = QWidget(self)
        self.content_layout = QStackedLayout(self.content)
        self.content_layout.setContentsMargins(0, 0, 0, 0)
        self.content_layout.setStackingMode(QStackedLayout.StackAll)
        self.content_layout.addWidget(self.canvas)
        self.content_layout.addWidget(self.table_view)
        layout.addWidget(self.content)
        self.preview_button = QToolButton(self.content)
        self.preview_button.setObjectName("nodePreviewHandleButton")
        self.preview_button.setText("预览")
        self.preview_button.setToolTip("打开节点预览")
        self.preview_button.setCheckable(True)
        self.preview_button.setAutoRaise(True)
        self.preview_button.clicked.connect(self._toggle_node_preview)
        self.preview_panel = NodePreviewPanel(self.content)
        self.preview_panel.setVisible(False)
        self.preview_panel.closeRequested.connect(self._hide_node_preview)
        self.preview_panel.openCanvasRequested.connect(self.previewCanvasOpenRequested.emit)
        self.refresh_canvas_nav()
        self.refresh_active_template()
        self.refresh_canvas_mode()
        self._position_preview_overlay()

    def resizeEvent(self, event) -> None:  # type: ignore[override]
        super().resizeEvent(event)
        self._position_preview_overlay()

    def _function_toggle_button(self, text: str, tooltip: str, layout_value: str) -> QToolButton:
        button = QToolButton(self.function_bar)
        button.setObjectName("canvasFunctionToggle")
        button.setText(text)
        button.setToolTip(tooltip)
        button.setCheckable(True)
        button.setAutoRaise(True)
        button.clicked.connect(lambda checked=False, value=layout_value: self._request_layout(value, checked))
        self.layout_button_group.addButton(button)
        return button

    def _request_layout(self, layout_value: str, checked: bool) -> None:
        if checked:
            self.dataLayoutRequested.emit(layout_value)

    def _row_style_toggle_button(self, text: str, tooltip: str, style_value: str) -> QToolButton:
        button = QToolButton(self.function_bar)
        button.setObjectName("canvasFunctionSubToggle")
        button.setText(text)
        button.setToolTip(tooltip)
        button.setCheckable(True)
        button.setAutoRaise(True)
        button.clicked.connect(lambda checked=False, value=style_value: self._request_row_style(value, checked))
        self.row_style_button_group.addButton(button)
        return button

    def _request_row_style(self, style_value: str, checked: bool) -> None:
        if checked:
            self.dataRowStyleRequested.emit(style_value)

    def _emit_grid_rows_requested(self, rows: int) -> None:
        if self.grid_rows_spin.signalsBlocked():
            return
        self.dataGridRowsRequested.emit(rows)

    def refresh_active_template(self) -> None:
        ids = [template.id for template in self.project.templates]
        if self.canvas_data.is_data_canvas() and self.canvas_data.template_id in ids:
            self.active_template_id = self.canvas_data.template_id
        elif self.active_template_id not in ids:
            self.active_template_id = ids[0] if ids else None
        self.canvas.set_templates(self.project.templates)
        self.table_view.set_canvas(self.project, self.canvas_data)

    def refresh_canvas_nav(self) -> None:
        show_nav = bool(not self.is_welcome and self.canvas_data.parent_canvas_id)
        self.parent_button.setVisible(show_nav)
        self.return_button.setVisible(show_nav)

    def refresh_canvas_mode(self) -> None:
        show_table = bool(self.canvas_data.is_data_canvas() and self.canvas_data.data_layout == "table")
        self.canvas.setVisible(not show_table)
        self.table_view.setVisible(show_table)
        self.preview_button.setVisible(not self.is_welcome)
        if self.is_welcome:
            self.preview_panel.setVisible(False)
            self.preview_button.setChecked(False)
        self._position_preview_overlay()
        self.table_view.set_canvas(self.project, self.canvas_data)
        self.function_bar.setVisible(not self.is_welcome)
        is_data_canvas = self.canvas_data.is_data_canvas()
        self.horizontal_layout_button.setVisible(is_data_canvas)
        self.grid_layout_button.setVisible(is_data_canvas)
        self.table_layout_button.setVisible(is_data_canvas)
        show_row_style = bool(is_data_canvas and self.canvas_data.data_layout == "horizontal")
        self.independent_row_button.setVisible(show_row_style)
        self.thumbnail_row_button.setVisible(show_row_style)
        show_grid_rows = bool(is_data_canvas and self.canvas_data.data_layout == "grid")
        self.grid_rows_spin.setVisible(show_grid_rows)
        self.grid_rows_spin.blockSignals(True)
        self.grid_rows_spin.setValue(max(0, int(self.canvas_data.data_grid_rows)))
        self.grid_rows_spin.blockSignals(False)
        self.horizontal_layout_button.setChecked(is_data_canvas and self.canvas_data.data_layout == "horizontal")
        self.grid_layout_button.setChecked(is_data_canvas and self.canvas_data.data_layout == "grid")
        self.table_layout_button.setChecked(is_data_canvas and self.canvas_data.data_layout == "table")
        self.independent_row_button.setChecked(show_row_style and self.canvas_data.data_row_style != "thumbnail")
        self.thumbnail_row_button.setChecked(show_row_style and self.canvas_data.data_row_style == "thumbnail")
        show_image_tools = bool(self.canvas_data.is_image_canvas())
        self.image_generate_button.setVisible(show_image_tools)
        self.image_retouch_button.setVisible(show_image_tools)

    def _position_preview_overlay(self) -> None:
        if not hasattr(self, "preview_panel"):
            return
        button_size = 44
        margin = 12
        gap = 8
        width = min(520, max(360, self.content.width() // 3))
        height = min(660, max(320, self.content.height() - button_size - margin * 3 - gap))
        self.preview_panel.setFixedSize(width, height)
        x = max(margin, self.content.width() - width - margin)
        y = max(margin, self.content.height() - height - button_size - margin - gap)
        self.preview_panel.move(x, y)
        self.preview_panel.raise_()
        if hasattr(self, "preview_button"):
            self.preview_button.setFixedSize(button_size, button_size)
            self.preview_button.move(max(margin, self.content.width() - button_size - margin), max(margin, self.content.height() - button_size - margin))
            self.preview_button.raise_()

    def _toggle_node_preview(self, checked: bool) -> None:
        if checked:
            self.show_selected_node_preview()
        else:
            self._hide_node_preview()

    def show_selected_node_preview(self) -> None:
        node_id = self.selected_node_id
        if not node_id:
            self.preview_panel.clear_preview()
            self.preview_panel.setVisible(True)
            self.preview_button.setChecked(True)
            self._position_preview_overlay()
            return
        self.show_node_preview(node_id)

    def show_node_preview(self, node_id: str) -> None:
        node = self.canvas_data.find_node(node_id)
        if node is None:
            self._hide_node_preview()
            return
        self.preview_panel.set_theme(self.theme)
        self.preview_panel.set_node(self.project, self.canvas_data, node, self.path, self.theme)
        self.preview_panel.setVisible(True)
        self.preview_button.setChecked(True)
        self._position_preview_overlay()

    def _hide_node_preview(self) -> None:
        self.preview_panel.clear_preview()
        self.preview_panel.setVisible(False)
        self.preview_button.setChecked(False)
        self._position_preview_overlay()


class CompactTitleBar(QWidget):
    def __init__(self, window: "GameDesignerApp", icon_path: Path | None) -> None:
        super().__init__(window)
        self.window = window
        self._drag_offset: QPoint | None = None
        self.setObjectName("topBar")

        layout = QHBoxLayout(self)
        layout.setContentsMargins(6, 2, 6, 2)
        layout.setSpacing(2)

        icon = QLabel(self)
        icon.setObjectName("appIcon")
        icon.setFixedSize(22, 22)
        icon.setAlignment(Qt.AlignCenter)
        icon.setAttribute(Qt.WA_TransparentForMouseEvents)
        if icon_path:
            pixmap = QPixmap(str(icon_path))
            if not pixmap.isNull():
                icon.setPixmap(pixmap.scaled(16, 16, Qt.KeepAspectRatio, Qt.SmoothTransformation))
        layout.addWidget(icon)

        layout.addWidget(self._menu_button("文件", window.file_menu))
        layout.addWidget(self._menu_button("编辑", window.edit_menu))
        layout.addWidget(self._menu_button("视图", window.view_menu))
        layout.addWidget(self._menu_button("AI", window.ai_menu))
        layout.addStretch(1)
        layout.addWidget(self._action_button(window.reset_view_action, "重置"))
        layout.addWidget(self._action_button(window.dark_mode_action, "夜间"))
        layout.addSpacing(4)
        layout.addWidget(self._window_button("-", window.showMinimized))
        self.window_mode_button = self._window_button("全屏", self._toggle_maximized)
        self.window_mode_button.setToolTip("切换到全屏模式")
        layout.addWidget(self.window_mode_button)
        layout.addWidget(self._window_button("×", window.close, close=True))

    def set_title(self, title: str) -> None:
        self.setToolTip(title)

    def _menu_button(self, text: str, menu: QMenu) -> QToolButton:
        button = QToolButton(self)
        button.setObjectName("topMenuButton")
        button.setText(text)
        button.setMenu(menu)
        button.setPopupMode(QToolButton.InstantPopup)
        button.setAutoRaise(True)
        button.setToolButtonStyle(Qt.ToolButtonTextOnly)
        button.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)
        return button

    def _action_button(self, action: QAction, text: str) -> QToolButton:
        button = QToolButton(self)
        button.setObjectName("topActionButton")
        button.setText(text)
        button.setToolTip(action.text())
        button.setAutoRaise(True)
        button.setToolButtonStyle(Qt.ToolButtonTextOnly)
        button.setIconSize(QSize(14, 14))
        button.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)
        if action.isCheckable():
            button.setCheckable(True)
            button.setChecked(action.isChecked())
            action.toggled.connect(button.setChecked)
        button.clicked.connect(lambda _checked=False, action=action: action.trigger())
        return button

    def _window_button(self, text: str, callback, close: bool = False) -> QToolButton:
        button = QToolButton(self)
        button.setObjectName("closeButton" if close else "windowButton")
        button.setText(text)
        button.setAutoRaise(True)
        button.setFixedSize(34, 24)
        button.clicked.connect(lambda _checked=False, callback=callback: callback())
        return button

    def _toggle_maximized(self) -> None:
        self.window.toggle_window_mode()

    def set_fullscreen_mode(self, enabled: bool) -> None:
        self.window_mode_button.setText("窗口" if enabled else "全屏")
        self.window_mode_button.setToolTip("切换到窗口模式" if enabled else "切换到全屏模式")

    def mousePressEvent(self, event) -> None:  # type: ignore[override]
        if event.button() == Qt.LeftButton:
            self._drag_offset = event.globalPosition().toPoint() - self.window.frameGeometry().topLeft()
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event) -> None:  # type: ignore[override]
        if self._drag_offset is not None and event.buttons() & Qt.LeftButton:
            if not self.window.isMaximized() and not self.window.is_window_fullscreen():
                self.window.move(event.globalPosition().toPoint() - self._drag_offset)
            event.accept()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event) -> None:  # type: ignore[override]
        self._drag_offset = None
        super().mouseReleaseEvent(event)

    def mouseDoubleClickEvent(self, event) -> None:  # type: ignore[override]
        if event.button() == Qt.LeftButton:
            self._toggle_maximized()
            event.accept()
            return
        super().mouseDoubleClickEvent(event)


class AdaptiveTabBar(QTabBar):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setExpanding(False)
        self.setUsesScrollButtons(True)
        self.setElideMode(Qt.ElideRight)

    def tabSizeHint(self, index: int) -> QSize:  # type: ignore[override]
        size = super().tabSizeHint(index)
        text_width = QFontMetrics(self.font()).horizontalAdvance(self.tabText(index))
        target_width = max(TAB_MIN_WIDTH, min(TAB_MAX_WIDTH, text_width + 58))
        size.setWidth(max(size.width(), target_width))
        size.setHeight(max(size.height(), 30))
        return size


class GameDesignerApp(QMainWindow):
    def __init__(self, startup_progress: Callable[[int, str], None] | None = None) -> None:
        super().__init__()
        self._startup_progress = startup_progress
        self.setWindowFlag(Qt.FramelessWindowHint, True)
        self._report_startup_progress(38, "读取本地设置...")
        self.settings = load_settings()
        self.theme = self.settings.theme if self.settings.theme in {"dark", "light"} else "dark"
        self._closing_app = False
        self._restoring_history = False
        self._window_fullscreen = False
        self._normal_window_geometry: QRect | None = None
        self._project_histories: dict[int, ProjectHistory] = {}
        self._copied_nodes: list[dict[str, Any]] = []
        self._copied_groups: list[dict[str, Any]] = []
        self._copied_edges: list[dict[str, Any]] = []
        self._paste_serial = 0
        self._last_edge_style = self.settings.last_edge_style if self.settings.last_edge_style in EDGE_STYLES else "curve"
        self._report_startup_progress(48, "准备字体和主题...")
        configure_fonts()
        self.setWindowTitle("GameDesigner - 游戏设计师")
        self.icon_path = _app_icon_path()
        if self.icon_path:
            self.setWindowIcon(QIcon(str(self.icon_path)))
        self.resize(1360, 860)
        self.setMinimumSize(1020, 640)
        restored_layout = self.settings.window_layouts.get("main_window", {})
        restore_window_layout(self, "main_window")
        self._normal_window_geometry = QRect(self.geometry())
        self.setStyleSheet(stylesheet(self.theme))

        self.tabs = QTabWidget()
        self.tabs.setTabBar(AdaptiveTabBar(self.tabs))
        self.tabs.setTabsClosable(False)
        self.tabs.setMovable(True)
        self.tabs.currentChanged.connect(lambda _index: self._on_current_tab_changed())
        self.tabs.tabCloseRequested.connect(self._close_tab)
        self.ai_assistant_panel = None
        self.ai_assistant_expanded = False
        self.ai_assistant_stack = QStackedWidget(self)
        self.ai_assistant_stack.setObjectName("aiAssistantStack")
        self.ai_assistant_stack.setFixedWidth(42)
        self.ai_assistant_collapsed = self._build_ai_assistant_collapsed_handle()
        self.ai_assistant_stack.addWidget(self.ai_assistant_collapsed)
        self.ai_assistant_stack.setCurrentWidget(self.ai_assistant_collapsed)

        self.workspace = QWidget(self)
        workspace_layout = QHBoxLayout(self.workspace)
        workspace_layout.setContentsMargins(0, 0, 0, 0)
        workspace_layout.setSpacing(0)
        workspace_layout.addWidget(self.tabs, 1)
        workspace_layout.addWidget(self.ai_assistant_stack)
        self.setCentralWidget(self.workspace)

        self.status = QStatusBar()
        self.setStatusBar(self.status)
        self.status.hide()

        self._report_startup_progress(62, "构建菜单与画布...")
        self._build_actions()
        self._build_menu()
        self._build_toolbar()
        if bool(restored_layout.get("fullscreen")):
            self._enter_window_fullscreen()
        self._bind_shortcuts()
        self._report_startup_progress(82, "加载开始页...")
        self._load_start_project()
        self._update_title()
        self._report_startup_progress(92, "整理启动状态...")

    def _report_startup_progress(self, progress: int, message: str) -> None:
        if self._startup_progress is not None:
            self._startup_progress(progress, message)

    def _build_actions(self) -> None:
        self.new_action = QAction("新建项目", self)
        self.new_action.setShortcut(QKeySequence.New)
        self.new_action.triggered.connect(self._new_project)

        self.open_action = QAction("打开项目...", self)
        self.open_action.setShortcut(QKeySequence.Open)
        self.open_action.triggered.connect(self._open_project)

        self.save_action = QAction("保存当前画布", self)
        self.save_action.setShortcut(QKeySequence.Save)
        self.save_action.triggered.connect(lambda: self._save_project())

        self.save_as_action = QAction("导出 GDC...", self)
        self.save_as_action.setShortcut(QKeySequence.SaveAs)
        self.save_as_action.triggered.connect(lambda: self._save_as_project())

        self.close_tab_action = QAction("关闭当前画布", self)
        self.close_tab_action.setShortcut(QKeySequence.Close)
        self.close_tab_action.triggered.connect(self._close_current_tab)

        self.project_settings_action = QAction("项目设置...", self)
        self.project_settings_action.triggered.connect(self._edit_project_settings)

        self.import_action = QAction("导入 GDC...", self)
        self.import_action.setShortcut(QKeySequence("Ctrl+I"))
        self.import_action.triggered.connect(self._import_gdc)

        self.export_action = QAction("导出所有画布 CSV...", self)
        self.export_action.setShortcut(QKeySequence("Ctrl+E"))
        self.export_action.triggered.connect(lambda: self._export_all_canvas_csv("created"))

        self.add_node_action = QAction("新增节点", self)
        self.add_node_action.setShortcut(QKeySequence("N"))
        self.add_node_action.triggered.connect(self._add_node)

        self.edit_action = QAction("编辑选中项", self)
        self.edit_action.setShortcut(QKeySequence(Qt.Key_Return))
        self.edit_action.triggered.connect(self._edit_selected)

        self.delete_action = QAction("删除选中项", self)
        self.delete_action.setShortcut(QKeySequence.Delete)
        self.delete_action.triggered.connect(self._delete_selected)

        self.copy_action = QAction("复制选中项", self)
        self.copy_action.setShortcut(QKeySequence.Copy)
        self.copy_action.triggered.connect(self._copy_selected_nodes)

        self.paste_action = QAction("粘贴选中项", self)
        self.paste_action.setShortcut(QKeySequence.Paste)
        self.paste_action.triggered.connect(self._paste_nodes)

        self.duplicate_action = QAction("复制一份", self)
        self.duplicate_action.setShortcut(QKeySequence("Ctrl+D"))
        self.duplicate_action.triggered.connect(self._duplicate_selected)

        self.undo_action = QAction("撤销", self)
        self.undo_action.setShortcut(QKeySequence.Undo)
        self.undo_action.triggered.connect(self._undo)

        self.redo_action = QAction("重做", self)
        self.redo_action.setShortcuts([QKeySequence.Redo, QKeySequence("Ctrl+Y")])
        self.redo_action.triggered.connect(self._redo)

        self.template_action = QAction("节点模板...", self)
        self.template_action.triggered.connect(self._manage_templates)

        self.ai_chat_action = QAction("AI 助手...", self)
        self.ai_chat_action.setShortcut(QKeySequence("Ctrl+Shift+A"))
        self.ai_chat_action.triggered.connect(self._open_ai_chat)

        self.ai_image_action = QAction("AI 生图助手...", self)
        self.ai_image_action.triggered.connect(self._open_ai_image_assistant)

        self.sequence_frame_action = QAction("序列帧动画...", self)
        self.sequence_frame_action.triggered.connect(lambda: self._open_sequence_frame_dialog(pixel_mode=False))

        self.pixel_sequence_frame_action = QAction("像素序列帧动画...", self)
        self.pixel_sequence_frame_action.triggered.connect(lambda: self._open_sequence_frame_dialog(pixel_mode=True))

        self.canvas_notes_action = QAction("画布便签...", self)
        self.canvas_notes_action.triggered.connect(self._open_canvas_notes)

        self.ai_settings_action = QAction("AI 设置...", self)
        self.ai_settings_action.triggered.connect(self._open_ai_settings)

        self.import_data_sheet_action = QAction("导入画布 CSV/Excel...", self)
        self.import_data_sheet_action.triggered.connect(self._import_canvas_sheet)
        self.convert_to_data_canvas_action = QAction("转换为排序画布", self)
        self.convert_to_data_canvas_action.triggered.connect(lambda: self._convert_current_canvas_type("data"))
        self.convert_to_normal_canvas_action = QAction("转换为自由画布", self)
        self.convert_to_normal_canvas_action.triggered.connect(lambda: self._convert_current_canvas_type("normal"))

        self.reset_view_action = QAction("重置视图", self)
        self.reset_view_action.setShortcut(QKeySequence("Ctrl+0"))
        self.reset_view_action.triggered.connect(self._reset_view)

        self.dark_mode_action = QAction("黑夜工作模式", self)
        self.dark_mode_action.setCheckable(True)
        self.dark_mode_action.setChecked(self.theme == "dark")
        self.dark_mode_action.triggered.connect(self._toggle_theme)

        self.exit_action = QAction("退出", self)
        self.exit_action.triggered.connect(self.close)

    def _build_menu(self) -> None:
        self.file_menu = QMenu("文件", self)
        self.file_menu.addAction(self.new_action)
        self.file_menu.addAction(self.open_action)
        self.file_menu.addSeparator()
        self.file_menu.addAction(self.save_action)
        self.file_menu.addAction(self.save_as_action)
        self.file_menu.addAction(self.close_tab_action)
        self.file_menu.addAction(self.project_settings_action)
        self.file_menu.addSeparator()
        self.file_menu.addAction(self.import_action)
        self.file_menu.addAction(self.export_action)
        self.file_menu.addSeparator()
        self.file_menu.addAction(self.exit_action)

        self.edit_menu = QMenu("编辑", self)
        self.edit_menu.addAction(self.undo_action)
        self.edit_menu.addAction(self.redo_action)
        self.edit_menu.addSeparator()
        self.edit_menu.addAction(self.copy_action)
        self.edit_menu.addAction(self.paste_action)
        self.edit_menu.addAction(self.duplicate_action)
        self.edit_menu.addSeparator()
        self.edit_menu.addAction(self.add_node_action)
        self.edit_menu.addAction(self.edit_action)
        self.edit_menu.addAction(self.delete_action)
        self.edit_menu.addSeparator()
        self.edit_menu.addAction(self.import_data_sheet_action)
        self.edit_menu.addAction(self.convert_to_data_canvas_action)
        self.edit_menu.addAction(self.convert_to_normal_canvas_action)
        self.edit_menu.addSeparator()
        self.edit_menu.addAction(self.template_action)

        self.view_menu = QMenu("视图", self)
        self.view_menu.addAction(self.reset_view_action)
        self.view_menu.addAction(self.dark_mode_action)

        self.ai_menu = QMenu("AI", self)
        self.ai_menu.addAction(self.ai_chat_action)
        self.ai_menu.addAction(self.ai_image_action)
        self.ai_menu.addSeparator()
        self.ai_menu.addAction(self.sequence_frame_action)
        self.ai_menu.addAction(self.pixel_sequence_frame_action)
        self.ai_menu.addSeparator()
        self.ai_menu.addAction(self.canvas_notes_action)
        self.ai_menu.addAction(self.ai_settings_action)

    def _build_ai_assistant_collapsed_handle(self) -> QWidget:
        holder = QWidget(self)
        holder.setObjectName("aiAssistantCollapsed")
        layout = QVBoxLayout(holder)
        layout.setContentsMargins(3, 8, 3, 8)
        layout.setSpacing(6)
        button = QToolButton(holder)
        button.setObjectName("aiAssistantHandleButton")
        button.setText("AI\n助手")
        button.setToolTip("展开 AI 助手")
        button.setAutoRaise(True)
        button.clicked.connect(self._open_ai_chat)
        paint_canvas_button = QToolButton(holder)
        paint_canvas_button.setObjectName("aiAssistantHandleButton")
        paint_canvas_button.setText("AI\n作画")
        paint_canvas_button.setToolTip("创建或打开普通 AI 作画画布")
        paint_canvas_button.setAutoRaise(True)
        paint_canvas_button.clicked.connect(self._open_or_create_image_canvas_from_sidebar)
        pixel_button = QToolButton(holder)
        pixel_button.setObjectName("aiAssistantHandleButton")
        pixel_button.setText("像素\n生图")
        pixel_button.setToolTip("创建或打开像素作画画布")
        pixel_button.setAutoRaise(True)
        pixel_button.clicked.connect(self._open_or_create_pixel_canvas_from_sidebar)
        notes_button = QToolButton(holder)
        notes_button.setObjectName("aiAssistantHandleButton")
        notes_button.setText("便签")
        notes_button.setToolTip("当前画布便签")
        notes_button.setAutoRaise(True)
        notes_button.clicked.connect(self._open_canvas_notes)
        image_button = QToolButton(holder)
        image_button.setObjectName("aiAssistantHandleButton")
        image_button.setText("生图")
        image_button.setToolTip("展开 AI 生图助手")
        image_button.setAutoRaise(True)
        image_button.clicked.connect(self._open_ai_image_assistant)
        layout.addStretch(1)
        layout.addWidget(button)
        layout.addWidget(image_button)
        layout.addWidget(paint_canvas_button)
        layout.addWidget(pixel_button)
        layout.addWidget(notes_button)
        layout.addStretch(1)
        return holder

    def _build_toolbar(self) -> None:
        self.titlebar = CompactTitleBar(self, self.icon_path)
        self.setMenuWidget(self.titlebar)

    def _bind_shortcuts(self) -> None:
        escape = QShortcut(QKeySequence(Qt.Key_Escape), self)
        escape.activated.connect(self._cancel_connection)

    def nativeEvent(self, event_type, message):  # type: ignore[override]
        if sys.platform.startswith("win") and event_type == "windows_generic_MSG":
            msg = wintypes.MSG.from_address(int(message))
            if msg.message == WM_NCHITTEST:
                hit = self._windows_hit_test(QCursor.pos())
                if hit:
                    return True, hit
        return super().nativeEvent(event_type, message)

    def _windows_hit_test(self, global_pos: QPoint) -> int | None:
        if not self.isMaximized() and not self.isFullScreen() and not self.is_window_fullscreen():
            rect = self.frameGeometry()
            left = global_pos.x() <= rect.left() + RESIZE_BORDER
            right = global_pos.x() >= rect.right() - RESIZE_BORDER
            top = global_pos.y() <= rect.top() + RESIZE_BORDER
            bottom = global_pos.y() >= rect.bottom() - RESIZE_BORDER
            if top and left:
                return HTTOPLEFT
            if top and right:
                return HTTOPRIGHT
            if bottom and left:
                return HTBOTTOMLEFT
            if bottom and right:
                return HTBOTTOMRIGHT
            if left:
                return HTLEFT
            if right:
                return HTRIGHT
            if top:
                return HTTOP
            if bottom:
                return HTBOTTOM

        if hasattr(self, "titlebar"):
            title_pos = self.titlebar.mapFromGlobal(global_pos)
            if self.titlebar.rect().contains(title_pos):
                child = self.titlebar.childAt(title_pos)
                if not isinstance(child, QToolButton):
                    return HTCAPTION
        return None

    def _load_start_project(self) -> None:
        workspace = Path(self.settings.workspace_dir)
        workspace.mkdir(parents=True, exist_ok=True)
        Path(self.settings.export_dir).mkdir(parents=True, exist_ok=True)
        self._show_welcome_page()

    def _add_page(
        self,
        project: ProjectData,
        path: Path | None,
        dirty: bool,
        canvas_data: CanvasData | None = None,
        source_canvas_id: str = "",
        is_welcome: bool = False,
        welcome_actions: dict[str, str] | None = None,
    ) -> ProjectPage:
        if not is_welcome:
            self._remove_welcome_pages()
        project.ensure_canvas_structure()
        if not is_welcome:
            self._sync_project_templates(project)
        active_canvas = canvas_data or project.root_canvas()
        history = self._project_history(project)
        if history is None:
            self._ensure_project_history(project, dirty, active_canvas.id)
        else:
            dirty = history.is_dirty()
        page = ProjectPage(
            project=project,
            path=path,
            dirty=dirty,
            theme=self.theme,
            canvas_data=active_canvas,
            source_canvas_id=source_canvas_id,
            is_welcome=is_welcome,
            welcome_actions=welcome_actions,
        )
        self._wire_page(page)
        index = self.tabs.addTab(page, self._tab_title(page))
        if not is_welcome:
            self._install_tab_close_button(page)
        self.tabs.setCurrentIndex(index)
        self._update_title()
        self._update_status()
        QTimer.singleShot(0, self._sync_current_canvas_cursor)
        return page

    def _wire_page(self, page: ProjectPage) -> None:
        canvas = page.canvas
        canvas.selectionChanged.connect(lambda node_id, edge_id, page=page: self._on_selection_changed(page, node_id, edge_id))
        canvas.projectChanged.connect(lambda page=page: self._mark_dirty(page))
        canvas.nodeActivated.connect(lambda node_id, page=page: self._activate_welcome_node(page, node_id))
        canvas.nodePreviewRequested.connect(lambda node_id, page=page: self._show_node_preview(page, node_id))
        canvas.nodeFolderRequested.connect(lambda node_id, page=page: self._open_welcome_project_folder(page, node_id))
        canvas.nodeEditRequested.connect(self._edit_node)
        canvas.nodeNotesRequested.connect(self._edit_node_notes)
        canvas.nodeDeleteRequested.connect(self._delete_node_by_id)
        canvas.nodesDeleteRequested.connect(self._delete_nodes_by_ids)
        canvas.groupDeleteRequested.connect(self._delete_group_by_id)
        canvas.groupEditRequested.connect(self._edit_group)
        canvas.edgeEditRequested.connect(self._edit_edge)
        canvas.edgeDeleteRequested.connect(self._delete_edge_by_id)
        canvas.edgeStyleRequested.connect(self._set_edge_style)
        canvas.edgeCreated.connect(self._create_edge)
        canvas.connectionDroppedOnEmpty.connect(lambda source_id, scene_pos, _global_pos, page=page: self._handle_connection_drop_on_empty(page, source_id, scene_pos))
        canvas.createNodeRequested.connect(self._add_node_at)
        canvas.createCanvasNodeRequested.connect(self._add_canvas_node_at)
        canvas.createImageCanvasRequested.connect(self._add_image_canvas_node_at)
        canvas.createPixelCanvasRequested.connect(self._add_pixel_canvas_node_at)
        canvas.createDataCanvasRequested.connect(self._add_data_canvas_node_at)
        canvas.createLinkNodeRequested.connect(self._add_link_node_at)
        canvas.createGroupRequested.connect(self._add_blueprint_group_at)
        canvas.createNoteRequested.connect(self._add_note_at)
        canvas.noteEditRequested.connect(self._edit_canvas_note)
        canvas.noteDeleteRequested.connect(self._delete_canvas_note)
        canvas.createTemplateNodeRequested.connect(self._add_node_from_template_at)
        canvas.dataCanvasLayoutRequested.connect(self._set_data_canvas_layout)
        canvas.dataCanvasTemplateRequested.connect(self._set_data_canvas_template)
        canvas.dataCanvasGridRowsRequested.connect(self._set_data_canvas_grid_rows)
        canvas.dataCanvasImportRequested.connect(self._import_canvas_sheet)
        canvas.templateManagerRequested.connect(self._manage_templates)
        canvas.openProjectRequested.connect(self._open_project)
        canvas.aiIterateRequested.connect(self._open_ai_iteration_assistant)
        canvas.imageGenerateRequested.connect(lambda page=page: self._generate_image_canvas(page))
        canvas.imageRetouchRequested.connect(lambda page=page: self._retouch_image_canvas(page))
        page.imageGenerateRequested.connect(lambda page=page: self._generate_image_canvas(page))
        page.imageRetouchRequested.connect(lambda page=page: self._retouch_image_canvas(page))
        page.table_view.projectChanged.connect(lambda page=page: self._mark_dirty(page))
        page.parentJumpRequested.connect(lambda page=page: self._jump_to_parent_canvas(page))
        page.returnCloseRequested.connect(lambda page=page: self._return_to_previous_canvas(page))
        page.resetViewRequested.connect(lambda page=page: page.canvas.reset_view())
        page.previewCanvasOpenRequested.connect(lambda canvas_id, page=page: self._open_canvas_page(page.project, page.path, canvas_id, source_canvas_id=page.canvas_id))
        page.dataLayoutRequested.connect(self._set_data_canvas_layout)
        page.dataGridRowsRequested.connect(self._set_data_canvas_grid_rows)
        page.dataRowStyleRequested.connect(self._set_data_canvas_row_style)

    def _show_welcome_page(self) -> None:
        for index in range(self.tabs.count()):
            page = self.tabs.widget(index)
            if isinstance(page, ProjectPage) and page.is_welcome:
                self.tabs.setCurrentIndex(index)
                return
        project, actions = self._build_welcome_project()
        page = self._add_page(project, None, dirty=False, is_welcome=True, welcome_actions=actions)
        page.canvas.reset_view()

    def _build_welcome_project(self) -> tuple[ProjectData, dict[str, str]]:
        project = ProjectData(name=WELCOME_PROJECT_NAME)
        actions: dict[str, str] = {WELCOME_NEW_NODE_ID: "new"}

        guide = Node(
            id=WELCOME_GUIDE_NODE_ID,
            title="操作指南",
            icon="指",
            x=-600,
            y=-190,
            width=430,
            height=404,
            fields=[
                NodeField("移动画布", "操作", "按住鼠标右键拖动画布，右键轻点打开菜单"),
                NodeField("缩放画布", "操作", "鼠标滚轮放大缩小"),
                NodeField("新建节点", "操作", "进入项目后，右键空白画布创建节点"),
                NodeField("连接节点", "操作", "右键节点选择连接，再左键点击目标节点"),
                NodeField("复制节点", "快捷键", "Ctrl+C 复制选中节点，Ctrl+V 粘贴到旁边"),
                NodeField("回退步骤", "快捷键", "Ctrl+Z 撤销，Ctrl+Y 重做"),
                NodeField("保存项目", "快捷键", "Ctrl+S 保存当前画布标签"),
            ],
        )
        self._apply_saved_node_layout(guide, self.settings.welcome_layout.get(guide.id))
        create = Node(
            id=WELCOME_NEW_NODE_ID,
            title="新建项目",
            icon="新",
            x=-90,
            y=-150,
            width=350,
            height=186,
            fields=[
                NodeField("动作", "入口", "双击此节点创建新的设计项目"),
                NodeField("目录", "设置", "创建时设置项目源目录和输出目录"),
            ],
        )
        self._apply_saved_node_layout(create, self.settings.welcome_layout.get(create.id))
        project.nodes.extend([guide, create])
        project.add_edge(guide.id, create.id)

        recent_x = 350
        recent_paths = self._valid_recent_projects()[:5]
        if recent_paths:
            visible_layouts = [self._saved_recent_layout_for_path(path) for path in recent_paths]
            saved_bottoms = [
                float(layout.get("y", -190.0)) + float(layout.get("height", 156.0)) + 20.0
                for layout in visible_layouts
                if layout is not None
            ]
            next_recent_y = max([-190.0, *saved_bottoms])
            for index, path in enumerate(recent_paths):
                node_id = f"welcome_recent_{index}"
                title = _project_name_from_path(path)
                node = Node(
                    id=node_id,
                    title=title,
                    icon=(title.strip()[:1] or "项"),
                    x=recent_x,
                    y=-190 + index * 176,
                    width=350,
                    height=156,
                    fields=[
                        NodeField("动作", "入口", "双击打开这个项目"),
                        NodeField("路径", "文件", str(path)),
                    ],
                )
                layout = self._saved_recent_layout_for_path(path)
                if layout is not None:
                    self._apply_saved_node_layout(node, layout)
                else:
                    node.y = next_recent_y
                    next_recent_y += node.height + 20
                project.nodes.append(node)
                project.add_edge(create.id, node.id)
                actions[node_id] = str(path)
        else:
            node = Node(
                id=WELCOME_NO_RECENT_NODE_ID,
                title="最近项目",
                icon="近",
                x=recent_x,
                y=-150,
                width=350,
                height=166,
                fields=[
                    NodeField("暂无记录", "状态", "打开或保存项目后，这里会出现最近项目节点"),
                    NodeField("打开项目", "入口", "右键空白处或点击顶部“打开项目”"),
                ],
            )
            self._apply_saved_node_layout(node, self.settings.welcome_layout.get(node.id))
            project.nodes.append(node)
        return project, actions

    def _saved_recent_layout_for_path(self, path: Path) -> dict[str, float] | None:
        return self.settings.welcome_recent_layouts.get(self._welcome_recent_layout_key(path))

    def _apply_saved_node_layout(self, node: Node, layout: dict[str, float] | None) -> None:
        if not layout:
            return
        node.x = float(layout.get("x", node.x))
        node.y = float(layout.get("y", node.y))
        node.width = float(layout.get("width", node.width))
        node.height = float(layout.get("height", node.height))

    def _capture_node_layout(self, node: Node) -> dict[str, float]:
        return {
            "x": float(node.x),
            "y": float(node.y),
            "width": float(node.width),
            "height": float(node.height),
        }

    def _welcome_recent_layout_key(self, path: str | Path) -> str:
        return str(Path(path)).casefold()

    def _save_welcome_page_layout(self, page: ProjectPage | None = None, show_status: bool = False) -> bool:
        page = page or self._current_page()
        if not page or not page.is_welcome:
            return False
        try:
            fixed_layouts = dict(self.settings.welcome_layout)
            recent_layouts = dict(self.settings.welcome_recent_layouts)
            for node in page.project.nodes:
                layout = self._capture_node_layout(node)
                action = page.welcome_actions.get(node.id, "")
                if action and action != "new":
                    recent_layouts[self._welcome_recent_layout_key(action)] = layout
                else:
                    fixed_layouts[node.id] = layout
            self.settings.welcome_layout = {
                node_id: layout for node_id, layout in fixed_layouts.items() if node_id in WELCOME_LAYOUT_NODE_IDS
            }
            self.settings.welcome_recent_layouts = recent_layouts
            save_settings(self.settings)
        except Exception as exc:  # noqa: BLE001 - surface IO errors.
            if show_status:
                QMessageBox.critical(self, "保存失败", f"无法保存开始页布局：\n{exc}")
            else:
                self.status.showMessage("开始页布局保存失败", 3500)
            return False
        if show_status:
            self.status.showMessage("已保存：开始页布局", 2500)
        return True

    def _valid_recent_projects(self) -> list[Path]:
        candidates = []
        if self.settings.last_project:
            candidates.append(self.settings.last_project)
        candidates.extend(self.settings.recent_projects)
        if not candidates:
            candidates.extend(self._discover_recent_project_paths())
        result: list[Path] = []
        seen: set[str] = set()
        for raw_path in candidates:
            path = Path(raw_path)
            key = str(path).casefold()
            if key in seen or not path.exists():
                continue
            seen.add(key)
            result.append(path)
        self.settings.recent_projects = [str(path) for path in result[:8]]
        return result

    def _discover_recent_project_paths(self) -> list[Path]:
        roots = []
        if self.settings.workspace_dir:
            roots.append(Path(self.settings.workspace_dir))
        roots.append(Path.home() / "Documents" / "GameDesigner")
        paths: list[Path] = []
        seen_roots: set[str] = set()
        for root in roots:
            key = str(root).casefold()
            if key in seen_roots or not root.exists() or not root.is_dir():
                continue
            seen_roots.add(key)
            try:
                paths.extend(path for path in root.rglob(f"*{PROJECT_SUFFIX}") if path.is_file())
            except OSError:
                continue
        paths.sort(key=lambda path: path.stat().st_mtime, reverse=True)
        return paths[:8]

    def _remove_welcome_pages(self) -> None:
        for index in reversed(range(self.tabs.count())):
            page = self.tabs.widget(index)
            if isinstance(page, ProjectPage) and page.is_welcome:
                self.tabs.removeTab(index)
                page.deleteLater()

    def _ensure_welcome_if_empty(self) -> None:
        if not self._closing_app and self.tabs.count() == 0:
            self._show_welcome_page()

    def _remember_project(self, path: Path) -> None:
        path_text = str(path)
        rest = [item for item in self.settings.recent_projects if item.casefold() != path_text.casefold()]
        self.settings.recent_projects = [path_text, *rest][:8]
        self.settings.last_project = path_text
        save_settings(self.settings)

    def _activate_welcome_node(self, page: ProjectPage, node_id: str) -> None:
        if not page.is_welcome:
            node = page.canvas_data.find_node(node_id)
            if node and node.node_type == "画布":
                self._open_canvas_from_node(page, node)
                return
            if node and node.node_type == "超文本":
                self._open_link_document(page, node)
                return
            self._edit_node(node_id)
            return
        action = page.welcome_actions.get(node_id)
        if not action:
            return
        if action == "new":
            self._new_project()
            return
        self._open_project_path(Path(action))

    def _open_welcome_project_folder(self, page: ProjectPage, node_id: str) -> None:
        if not page.is_welcome:
            return
        action = page.welcome_actions.get(node_id)
        if not action or action == "new":
            return
        project_path = Path(action)
        folder = project_path.parent if project_path.suffix else project_path
        if not folder.exists():
            QMessageBox.warning(self, "文件夹不存在", f"无法找到项目所在文件夹：\n{folder}")
            return
        if not QDesktopServices.openUrl(QUrl.fromLocalFile(str(folder))):
            QMessageBox.warning(self, "打开失败", f"无法打开项目所在文件夹：\n{folder}")

    def _current_page(self) -> ProjectPage | None:
        widget = self.tabs.currentWidget()
        return widget if isinstance(widget, ProjectPage) else None

    def _project_pages(self, project: ProjectData) -> list[ProjectPage]:
        pages: list[ProjectPage] = []
        for index in range(self.tabs.count()):
            page = self.tabs.widget(index)
            if isinstance(page, ProjectPage) and page.project is project and not page.is_welcome:
                pages.append(page)
        return pages

    def _open_project_pages(self) -> list[ProjectPage]:
        pages: list[ProjectPage] = []
        for index in range(self.tabs.count()):
            page = self.tabs.widget(index)
            if isinstance(page, ProjectPage) and not page.is_welcome:
                pages.append(page)
        return pages

    def _sync_project_templates(self, project: ProjectData) -> bool:
        return sync_locked_template_nodes(project)

    def _sync_canvas_state(self, page: ProjectPage) -> bool:
        if not page.canvas_data.is_data_canvas():
            return False
        return sync_data_canvas(page.project, page.canvas_data)

    def _refresh_project_views(self, project: ProjectData) -> None:
        for project_page in self._project_pages(project):
            project_page.refresh_active_template()
            project_page.refresh_canvas_nav()
            project_page.refresh_canvas_mode()
            project_page.canvas.set_templates(project.templates)
            project_page.canvas.rebuild()
            project_page.canvas.viewport().update()
            self._update_tab_title(project_page)
        self._update_status()

    def _project_history(self, project: ProjectData) -> ProjectHistory | None:
        return self._project_histories.get(id(project))

    def _ensure_project_history(self, project: ProjectData, dirty: bool, canvas_id: str) -> ProjectHistory:
        history = self._project_history(project)
        if history is None:
            history = ProjectHistory()
            history.initialize(project, canvas_id=canvas_id, clean=not dirty)
            self._project_histories[id(project)] = history
        return history

    def _record_project_snapshot(self, page: ProjectPage) -> None:
        history = self._ensure_project_history(page.project, page.dirty, page.canvas_id)
        history.record(page.project, canvas_id=page.canvas_id)

    def _sync_project_dirty_state(self, project: ProjectData) -> None:
        history = self._project_history(project)
        dirty = history.is_dirty() if history is not None else any(page.dirty for page in self._project_pages(project))
        for project_page in self._project_pages(project):
            project_page.dirty = dirty
            self._update_tab_title(project_page)
        self._update_title()
        self._update_status()

    def _restore_project_snapshot(self, project: ProjectData, snapshot: ProjectSnapshot) -> None:
        self._restoring_history = True
        try:
            restored = ProjectData.from_dict(copy.deepcopy(snapshot.project))
            project.name = restored.name
            project.source_dir = restored.source_dir
            project.output_dir = restored.output_dir
            project.copy_link_docs_to_source = restored.copy_link_docs_to_source
            project.nodes = restored.nodes
            project.edges = restored.edges
            project.templates = restored.templates
            project.root_canvas_id = restored.root_canvas_id
            project.canvases = restored.canvases
            project.ensure_canvas_structure()
            self._sync_project_templates(project)

            target_canvas_id = snapshot.canvas_id or project.root_canvas_id
            target_page: ProjectPage | None = None
            for project_page in self._project_pages(project):
                target_canvas = project.find_canvas(project_page.canvas_id) or project.root_canvas()
                project_page.canvas_data = target_canvas
                project_page.canvas_id = target_canvas.id
                project_page.refresh_active_template()
                project_page.refresh_canvas_nav()
                project_page.refresh_canvas_mode()
                project_page.canvas.set_templates(project.templates)
                project_page.canvas.set_project(target_canvas)
                project_page.canvas.clear_selection()
                self._update_tab_title(project_page)
                if project_page.canvas_id == target_canvas_id:
                    target_page = project_page
            if target_page is None:
                target_page = self._open_canvas_page(project, self._project_pages(project)[0].path if self._project_pages(project) else None, target_canvas_id)
            if target_page is not None:
                index = self.tabs.indexOf(target_page)
                if index >= 0:
                    self.tabs.setCurrentIndex(index)
        finally:
            self._restoring_history = False

    def _project_dirty(self, project: ProjectData) -> bool:
        history = self._project_history(project)
        if history is not None:
            return history.is_dirty()
        return any(page.dirty for page in self._project_pages(project))

    def _canvas_tab_name(self, page: ProjectPage) -> str:
        if page.canvas_id == page.project.root_canvas_id:
            return page.project.name
        return f"{page.project.name} / {page.canvas_data.name}"

    def _tab_title(self, page: ProjectPage) -> str:
        if page.is_welcome:
            return "开始"
        mark = " *" if page.dirty else ""
        return f"{self._canvas_tab_name(page)}{mark}"

    def _update_tab_title(self, page: ProjectPage) -> None:
        index = self.tabs.indexOf(page)
        if index >= 0:
            self.tabs.setTabText(index, self._tab_title(page))
            self.tabs.tabBar().updateGeometry()

    def _install_tab_close_button(self, page: ProjectPage) -> None:
        index = self.tabs.indexOf(page)
        if index < 0:
            return
        holder = QWidget(self.tabs)
        holder.setObjectName("tabCloseHolder")
        layout = QHBoxLayout(holder)
        layout.setContentsMargins(0, 0, 8, 0)
        layout.setSpacing(0)
        button = QToolButton(holder)
        button.setObjectName("tabCloseButton")
        button.setText("×")
        button.setCursor(Qt.PointingHandCursor)
        button.setToolTip("关闭画布")
        button.setFixedSize(18, 18)
        button.clicked.connect(lambda _checked=False, page=page: self._close_page(page))
        layout.addWidget(button)
        self.tabs.tabBar().setTabButton(index, QTabBar.RightSide, holder)

    def _close_page(self, page: ProjectPage) -> None:
        index = self.tabs.indexOf(page)
        if index >= 0:
            self._close_tab(index)

    def _on_current_tab_changed(self) -> None:
        self._update_title()
        self._update_status()
        page = self._current_page()
        if page and not page.is_welcome and page.canvas_data.is_image_canvas():
            self._open_ai_image_assistant()
        QTimer.singleShot(0, self._sync_current_canvas_cursor)

    def _sync_current_canvas_cursor(self) -> None:
        page = self._current_page()
        if not page:
            return
        self.unsetCursor()
        self.tabs.unsetCursor()
        page.unsetCursor()
        page.canvas.hover_node_id = None
        page.canvas.viewport().unsetCursor()
        page.canvas.unsetCursor()
        page.canvas.sync_interaction_cursor()

    def _update_title(self) -> None:
        page = self._current_page()
        if not page:
            self._set_window_caption("GameDesigner - 游戏设计师")
            return
        if page.is_welcome:
            self._set_window_caption("GameDesigner - 开始")
            return
        mark = "*" if page.dirty else ""
        path = f" - {page.path}" if page.path else ""
        self._set_window_caption(f"{mark}GameDesigner - {self._canvas_tab_name(page)}{path}")

    def _set_window_caption(self, title: str) -> None:
        self.setWindowTitle(title)
        if hasattr(self, "titlebar"):
            self.titlebar.set_title(title)

    def _update_status(self) -> None:
        self.status.clearMessage()
        page = self._current_page()
        editable_canvas = bool(page and not page.is_welcome)
        is_data_canvas = bool(editable_canvas and page.canvas_data.is_data_canvas())
        self.import_data_sheet_action.setEnabled(editable_canvas)
        self.convert_to_data_canvas_action.setEnabled(bool(editable_canvas and not is_data_canvas))
        self.convert_to_normal_canvas_action.setEnabled(is_data_canvas)

    def _on_selection_changed(self, page: ProjectPage, node_id: str | None, edge_id: str | None) -> None:
        page.selected_node_id = node_id
        page.selected_edge_id = edge_id
        if node_id is None:
            page._hide_node_preview()
        elif page.preview_panel.isVisible() or page.preview_button.isChecked():
            page.show_node_preview(node_id)
        if page is self._current_page():
            self._update_status()

    def _show_node_preview(self, page: ProjectPage, node_id: str) -> None:
        if page.is_welcome:
            return
        if page is not self._current_page():
            index = self.tabs.indexOf(page)
            if index >= 0:
                self.tabs.setCurrentIndex(index)
        page.show_node_preview(node_id)
        self._position_preview_overlay(page)

    def _hide_node_preview(self, page: ProjectPage | None = None) -> None:
        page = page or self._current_page()
        if not page:
            return
        page._hide_node_preview()

    def _mark_dirty(self, page: ProjectPage | None = None) -> None:
        page = page or self._current_page()
        if not page:
            return
        if page.is_welcome:
            self._save_welcome_page_layout(page)
            return
        if self._restoring_history:
            return
        self._record_project_snapshot(page)
        self._sync_project_dirty_state(page.project)

    def _new_project(self) -> None:
        from .qt_dialogs import ProjectSettingsDialog

        dialog = ProjectSettingsDialog(
            self,
            "新建项目",
            "未命名设计",
            self.settings.workspace_dir,
            self.settings.export_dir,
            False,
        )
        if dialog.exec() != ProjectSettingsDialog.Accepted or not dialog.result_data:
            return
        project = default_project()
        project.name = dialog.result_data["name"]
        project.source_dir = dialog.result_data["source_dir"]
        project.output_dir = dialog.result_data["output_dir"]
        project.copy_link_docs_to_source = bool(dialog.result_data.get("copy_link_docs_to_source", False))
        Path(project.source_dir).mkdir(parents=True, exist_ok=True)
        Path(project.output_dir).mkdir(parents=True, exist_ok=True)
        self._sync_settings_from_project(project)
        filename = f"{_safe_filename(project.name)}{PROJECT_SUFFIX}"
        self._add_page(project, Path(project.source_dir) / filename, dirty=True)
        save_settings(self.settings)

    def _open_project(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self,
            "打开项目",
            self.settings.workspace_dir,
            f"GameDesigner 工程 (*{PROJECT_SUFFIX});;旧版工程 (*{LEGACY_PROJECT_SUFFIX} *.json);;所有文件 (*.*)",
        )
        if not path:
            return
        self._open_project_path(Path(path))

    def _open_project_path(self, project_path: Path) -> None:
        if not project_path.exists():
            QMessageBox.warning(self, "项目不存在", f"无法找到项目文件：\n{project_path}")
            return
        for index in range(self.tabs.count()):
            page = self.tabs.widget(index)
            if (
                isinstance(page, ProjectPage)
                and not page.is_welcome
                and page.path
                and page.path.resolve() == project_path.resolve()
            ):
                self._reload_project_pages(page.project, page.path)
                return
        try:
            QApplication.setOverrideCursor(Qt.WaitCursor)
            QApplication.processEvents()
            project = load_project(project_path)
            self._ensure_project_dirs(project, project_path)
            self._sync_settings_from_project(project)
            self._remember_project(project_path)
            self._add_page(project, project_path, dirty=False, canvas_data=project.root_canvas())
        except Exception as exc:  # noqa: BLE001 - selected by user.
            QMessageBox.critical(self, "打开失败", f"无法打开项目：\n{exc}")
            return
        finally:
            QApplication.restoreOverrideCursor()
            QTimer.singleShot(0, self._sync_current_canvas_cursor)

    def _reload_project_pages(self, project: ProjectData, project_path: Path) -> None:
        open_pages = self._project_pages(project)
        current_page = self._current_page()
        active_canvas_id = current_page.canvas_id if current_page in open_pages else project.root_canvas_id
        open_canvas_ids = [page.canvas_id for page in open_pages] or [project.root_canvas_id]
        source_canvas_by_id = {page.canvas_id: page.source_canvas_id for page in open_pages}
        insert_index = self.tabs.indexOf(open_pages[0]) if open_pages else self.tabs.count()

        for page in reversed(open_pages):
            index = self.tabs.indexOf(page)
            if index >= 0:
                self.tabs.removeTab(index)
            page.deleteLater()
        self._project_histories.pop(id(project), None)

        try:
            project = load_project(project_path)
            self._ensure_project_dirs(project, project_path)
            self._sync_settings_from_project(project)
            self._sync_project_templates(project)
            self._remember_project(project_path)
        except Exception as exc:  # noqa: BLE001 - selected by user.
            QMessageBox.critical(self, "打开失败", f"无法重新加载项目：\n{exc}")
            return

        self._ensure_project_history(project, False, project.root_canvas_id)
        reopened: list[ProjectPage] = []
        for canvas_id in open_canvas_ids:
            canvas = project.find_canvas(canvas_id) or project.root_canvas()
            page = ProjectPage(
                project=project,
                path=project_path,
                dirty=False,
                theme=self.theme,
                canvas_data=canvas,
                source_canvas_id=source_canvas_by_id.get(canvas_id, ""),
            )
            self._wire_page(page)
            self.tabs.insertTab(insert_index, page, self._tab_title(page))
            self._install_tab_close_button(page)
            reopened.append(page)
            insert_index += 1

        target_canvas_id = active_canvas_id if project.find_canvas(active_canvas_id) else project.root_canvas_id
        target_page = next((page for page in reopened if page.canvas_id == target_canvas_id), None)
        if target_page is None and reopened:
            target_page = reopened[0]
        if target_page is not None:
            self.tabs.setCurrentWidget(target_page)
        self._update_title()
        self._update_status()

    def _open_canvas_page(
        self,
        project: ProjectData,
        path: Path | None,
        canvas_id: str,
        source_canvas_id: str = "",
    ) -> ProjectPage | None:
        project.ensure_canvas_structure()
        canvas = project.find_canvas(canvas_id)
        if not canvas:
            return None
        for index in range(self.tabs.count()):
            page = self.tabs.widget(index)
            if (
                isinstance(page, ProjectPage)
                and not page.is_welcome
                and page.project is project
                and page.canvas_id == canvas.id
            ):
                if source_canvas_id:
                    page.source_canvas_id = source_canvas_id
                already_current = page is self._current_page()
                self.tabs.setCurrentIndex(index)
                if canvas.is_image_canvas() and already_current:
                    self._open_ai_image_assistant()
                return page
        page = self._add_page(
            project,
            path,
            dirty=self._project_dirty(project),
            canvas_data=canvas,
            source_canvas_id=source_canvas_id,
        )
        page.canvas.reset_view()
        return page

    def _open_canvas_from_node(self, page: ProjectPage, node: Node) -> None:
        canvas = self._ensure_canvas_node_link(page, node, mark_dirty=True)
        if not canvas:
            return
        self._open_canvas_page(page.project, page.path, canvas.id, source_canvas_id=page.canvas_id)

    def _ensure_canvas_node_link(self, page: ProjectPage, node: Node, mark_dirty: bool = False) -> CanvasData | None:
        if node.node_type != "画布":
            return None
        canvas = page.project.find_canvas(node.canvas_id) if node.canvas_id else None
        if not canvas:
            canvas = page.project.add_canvas(
                node.title or "新画布",
                parent_canvas_id=page.canvas_id,
                parent_node_id=node.id,
            )
            node.canvas_id = canvas.id
            if not node.icon:
                node.icon = "数" if canvas.is_data_canvas() else "画"
            if mark_dirty:
                self._mark_dirty(page)
            return canvas
        canvas.name = node.title or canvas.name
        if canvas.id != page.project.root_canvas_id:
            canvas.parent_canvas_id = canvas.parent_canvas_id or page.canvas_id
            canvas.parent_node_id = canvas.parent_node_id or node.id
        if canvas.is_data_canvas() and not node.icon:
            node.icon = "数"
        if canvas.is_image_canvas() and not node.icon:
            node.icon = "图"
        return canvas

    def _image_canvas_output_node_id(self, canvas: CanvasData) -> str:
        output = find_image_output_node(canvas)
        return output.id if output is not None else ""

    def _handle_connection_drop_on_empty(self, page: ProjectPage, source_id: str, scene_pos: QPointF) -> None:
        if page is not self._current_page() or page.is_welcome:
            return
        menu = QMenu(self)
        actions: dict[str, QAction] = {
            "node": menu.addAction("节点"),
            "canvas": menu.addAction("画布节点"),
            "image_canvas": menu.addAction("生图画布"),
            "pixel_canvas": menu.addAction("像素作画画布"),
            "data_canvas": menu.addAction("数据画布"),
            "note": menu.addAction("便签"),
            "group": menu.addAction("蓝图组"),
        }
        action = self._exec_app_context_menu(menu)
        if action is None:
            return
        before_nodes = {node.id for node in page.canvas_data.nodes}
        before_groups = {group.id for group in page.canvas_data.groups}
        created_node_id = ""
        if action == actions["node"]:
            self._add_node_at(scene_pos.x(), scene_pos.y())
        elif action == actions["canvas"]:
            created_node_id = self._add_canvas_node_at(scene_pos.x(), scene_pos.y()) or ""
        elif action == actions["image_canvas"]:
            created_node_id = self._add_image_canvas_node_at(scene_pos.x(), scene_pos.y()) or ""
        elif action == actions["pixel_canvas"]:
            created_node_id = self._add_pixel_canvas_node_at(scene_pos.x(), scene_pos.y()) or ""
        elif action == actions["data_canvas"]:
            created_node_id = self._add_data_canvas_node_at(scene_pos.x(), scene_pos.y()) or ""
        elif action == actions["note"]:
            self._add_note_at(scene_pos.x(), scene_pos.y(), None)
        elif action == actions["group"]:
            self._add_blueprint_group_at(scene_pos.x(), scene_pos.y())
        created_nodes = [node for node in page.canvas_data.nodes if node.id not in before_nodes]
        created_groups = [group for group in page.canvas_data.groups if group.id not in before_groups]
        if created_node_id:
            target_id = created_node_id
        elif created_nodes:
            target_id = created_nodes[0].id
        elif created_groups:
            target_id = created_groups[0].id
        else:
            return
        if target_id == source_id:
            return
        edge = page.canvas_data.add_edge(source_id, target_id)
        if edge is None:
            return
        if self._last_edge_style in EDGE_STYLES:
            edge.style = self._last_edge_style
        page.canvas.rebuild()
        if created_node_id or created_nodes:
            page.canvas.select_node(target_id)
        else:
            page.canvas.select_group(target_id)
        self._mark_dirty(page)

    def _ensure_project_path_for_files(self, page: ProjectPage) -> Path:
        self._ensure_project_dirs(page.project, page.path)
        if not page.path:
            filename = f"{_safe_filename(page.project.name)}{PROJECT_SUFFIX}"
            page.path = Path(page.project.source_dir) / filename
        Path(page.project.source_dir).mkdir(parents=True, exist_ok=True)
        Path(page.project.output_dir).mkdir(parents=True, exist_ok=True)
        return page.path

    def _ensure_link_node_file(self, page: ProjectPage, node: Node) -> None:
        from .project_files.linked_documents import (
            create_link_document,
            delete_link_document_copy,
            ensure_link_document,
        )

        if node.node_type != "超文本":
            return
        project_path = self._ensure_project_path_for_files(page)
        file_format = node.link_format if node.link_format in {"md", "txt"} else "md"
        existing_path = node.link_path or self._link_path_from_fields(node)
        if not existing_path:
            node.link_path = create_link_document(project_path, node.title or "新文档", file_format)
        else:
            old_path = node.link_path
            node.link_path = ensure_link_document(project_path, existing_path, node.title or "新文档", file_format)
            if old_path and old_path != node.link_path:
                delete_link_document_copy(page.project.source_dir, old_path)
        node.link_format = file_format
        node.canvas_id = ""
        self._normalize_link_node_title(node)
        node.fields = [
            field for field in node.fields
            if not (field.name == "文件" and field.data_type == "资源路径")
        ]
        node.fields.append(NodeField("文件", "资源路径", node.link_path))
        self._sync_link_document_copy(page, node)

    def _link_path_from_fields(self, node: Node) -> str:
        for field in node.fields:
            if field.name == "文件" and field.data_type == "资源路径" and field.value.strip():
                return field.value.strip()
        return ""

    def _normalize_link_node_title(self, node: Node) -> None:
        if node.node_type != "超文本" or not node.link_path:
            return
        raw_title = node.title.strip()
        normalized_path = node.link_path.replace("\\", "/")
        if not raw_title or raw_title.replace("\\", "/") == normalized_path:
            node.title = Path(node.link_path).stem or "新文档"

    def _sync_link_document_copy(self, page: ProjectPage, node: Node) -> None:
        from .project_files.linked_documents import sync_link_document_copy

        if (
            not page.project.copy_link_docs_to_source
            or not page.path
            or node.node_type != "超文本"
            or not node.link_path
        ):
            return
        sync_link_document_copy(page.path, node.link_path, page.project.source_dir)

    def _sync_all_link_document_copies(self, project: ProjectData, project_path: Path) -> None:
        from .project_files.linked_documents import sync_link_document_copy

        for canvas in project.canvases:
            for node in canvas.nodes:
                if node.node_type == "超文本" and node.link_path:
                    sync_link_document_copy(project_path, node.link_path, project.source_dir)

    def _ensure_all_link_node_files(self, project: ProjectData, project_path: Path) -> None:
        temp_page = ProjectPage(
            project=project,
            path=project_path,
            dirty=False,
            theme=self.theme,
            canvas_data=project.root_canvas(),
        )
        for canvas in project.canvases:
            for node in canvas.nodes:
                if node.node_type != "超文本":
                    continue
                existing_path = node.link_path or self._link_path_from_fields(node)
                if existing_path or node.fields or node.title.strip():
                    self._ensure_link_node_file(temp_page, node)
        temp_page.deleteLater()

    def _delete_link_document_with_copy(self, page: ProjectPage, node: Node) -> None:
        from .project_files.linked_documents import delete_link_document, delete_link_document_copy

        if node.node_type != "超文本" or not node.link_path:
            return
        if page.path:
            delete_link_document(page.path, node.link_path)
        delete_link_document_copy(page.project.source_dir, node.link_path)

    def _open_link_document(self, page: ProjectPage, node: Node) -> None:
        from .project_files.linked_documents import delete_link_document_copy
        from .ui.link_document_dialog import LinkDocumentDialog

        if node.node_type != "超文本":
            return
        dialog = LinkDocumentDialog(
            self,
            self._ensure_project_path_for_files(page),
            node.link_path,
            node.title,
            node.link_format,
        )
        result = dialog.exec()
        if dialog.saved:
            old_path = node.link_path
            node.link_path = dialog.relative_path
            node.link_format = dialog.file_format
            if old_path and old_path != node.link_path:
                delete_link_document_copy(page.project.source_dir, old_path)
            self._normalize_link_node_title(node)
            node.fields = [
                field for field in node.fields
                if not (field.name == "文件" and field.data_type == "资源路径")
            ]
            node.fields.append(NodeField("文件", "资源路径", node.link_path))
            self._sync_link_document_copy(page, node)
            self.status.showMessage(f"已保存超文本文件：{node.link_path}", 3000)
        if dialog.deleted and result == LinkDocumentDialog.Accepted:
            if node.link_path:
                delete_link_document_copy(page.project.source_dir, node.link_path)
            page.canvas_data.delete_node(node.id)
            page.canvas.rebuild()
            page.canvas.clear_selection()
            self._mark_dirty(page)
            return
        if dialog.saved:
            self._mark_dirty(page)

    def _jump_to_parent_canvas(self, page: ProjectPage) -> None:
        parent_id = page.canvas_data.parent_canvas_id
        if not parent_id:
            return
        self._open_canvas_page(page.project, page.path, parent_id, source_canvas_id=page.canvas_id)

    def _return_to_previous_canvas(self, page: ProjectPage) -> None:
        target_id = page.source_canvas_id or page.canvas_data.parent_canvas_id or page.project.root_canvas_id
        if not target_id or target_id == page.canvas_id:
            return
        if not self._save_project(page):
            return
        self._open_canvas_page(page.project, page.path, target_id)
        index = self.tabs.indexOf(page)
        if index >= 0:
            self.tabs.removeTab(index)
            page.deleteLater()
        self._ensure_welcome_if_empty()
        self._update_title()
        self._update_status()

    def _save_project(self, page: ProjectPage | None = None) -> bool:
        page = page or self._current_page()
        if not page:
            return False
        if page.is_welcome:
            return self._save_welcome_page_layout(page, show_status=True)
        if not page.path:
            return self._save_as_project(page)
        try:
            self._ensure_project_dirs(page.project, page.path)
            Path(page.project.source_dir).mkdir(parents=True, exist_ok=True)
            Path(page.project.output_dir).mkdir(parents=True, exist_ok=True)
            self._sync_project_templates(page.project)
            self._ensure_all_link_node_files(page.project, page.path)
            save_project(page.project, page.path)
            if page.project.copy_link_docs_to_source:
                self._sync_all_link_document_copies(page.project, page.path)
        except Exception as exc:  # noqa: BLE001 - surface IO errors.
            QMessageBox.critical(self, "保存失败", f"无法保存项目：\n{exc}")
            return False
        self._sync_settings_from_project(page.project)
        self._remember_project(page.path)
        history = self._ensure_project_history(page.project, dirty=False, canvas_id=page.canvas_id)
        history.mark_clean()
        for project_page in self._project_pages(page.project):
            project_page.path = page.path
        self._sync_project_dirty_state(page.project)
        self.status.showMessage(f"已保存：{page.path}", 3500)
        return True

    def _save_as_project(self, page: ProjectPage | None = None) -> bool:
        page = page or self._current_page()
        if not page or page.is_welcome:
            return False
        path, _ = QFileDialog.getSaveFileName(
            self,
            "导出 GDC 工程",
            str(Path(self.settings.workspace_dir) / f"{_safe_filename(page.project.name)}{PROJECT_SUFFIX}"),
            f"GameDesigner 工程 (*{PROJECT_SUFFIX})",
        )
        if not path:
            return False
        page.path = _ensure_suffix(Path(path), PROJECT_SUFFIX)
        return self._save_project(page)

    def _edit_project_settings(self) -> None:
        from .qt_dialogs import ProjectSettingsDialog

        page = self._current_page()
        if not page or page.is_welcome:
            return
        self._ensure_project_dirs(page.project, page.path)
        old_name = page.project.name
        dialog = ProjectSettingsDialog(
            self,
            "项目设置",
            page.project.name,
            page.project.source_dir,
            page.project.output_dir,
            page.project.copy_link_docs_to_source,
            page.path,
        )
        if dialog.exec() != ProjectSettingsDialog.Accepted or not dialog.result_data:
            return
        page.project.name = dialog.result_data["name"]
        page.project.source_dir = dialog.result_data["source_dir"]
        page.project.output_dir = dialog.result_data["output_dir"]
        page.project.copy_link_docs_to_source = bool(dialog.result_data.get("copy_link_docs_to_source", False))
        Path(page.project.source_dir).mkdir(parents=True, exist_ok=True)
        Path(page.project.output_dir).mkdir(parents=True, exist_ok=True)
        if page.project.copy_link_docs_to_source and page.path:
            self._sync_all_link_document_copies(page.project, page.path)
        if page.path is None or page.path.name == f"{_safe_filename(old_name)}{PROJECT_SUFFIX}":
            page.path = Path(page.project.source_dir) / f"{_safe_filename(page.project.name)}{PROJECT_SUFFIX}"
        self._sync_settings_from_project(page.project)
        save_settings(self.settings)
        self._mark_dirty(page)

    def _export_all_canvas_csv(self, sort_mode: str = "created") -> None:
        from .csv_io import export_all_canvas_csv
        from .qt_dialogs import ExportCanvasCsvDialog

        page = self._current_page()
        if not page or page.is_welcome:
            return
        self._ensure_project_dirs(page.project, page.path)
        default_folder = (
            page.project.output_dir
            or self.settings.export_dir
            or self.settings.workspace_dir
            or str(Path.home())
        )
        dialog = ExportCanvasCsvDialog(
            self,
            page.project,
            str(Path(default_folder)),
            default_sort_mode=sort_mode,
            project_path=page.path,
            theme=self.theme,
            export_state=self._export_canvas_csv_dialog_state(page),
        )
        if dialog.exec() != ExportCanvasCsvDialog.Accepted or not dialog.result_data:
            return
        folder = str(dialog.result_data["folder"])
        canvas_specs = list(dialog.result_data["canvas_specs"])
        self._save_export_canvas_csv_dialog_state(page, dict(dialog.result_data.get("export_state") or {}))
        try:
            export_paths = export_all_canvas_csv(
                page.project,
                Path(folder),
                sort_mode=sort_mode,
                canvas_specs=canvas_specs,
            )
        except Exception as exc:  # noqa: BLE001 - surface IO errors.
            QMessageBox.critical(self, "导出失败", f"无法导出所有画布 CSV：\n{exc}")
            return
        page.project.output_dir = folder
        self.settings.export_dir = folder
        save_settings(self.settings)
        self.status.showMessage(f"已导出 {len(export_paths)} 个画布 CSV：{folder}", 5000)

    def _export_canvas_csv_dialog_state(self, page: ProjectPage) -> dict[str, Any]:
        raw = self.settings.export_canvas_csv_dialog
        if not isinstance(raw, dict):
            return {}
        projects = raw.get("projects")
        if not isinstance(projects, dict):
            return {}
        state = projects.get(self._export_canvas_csv_dialog_state_key(page))
        return dict(state) if isinstance(state, dict) else {}

    def _save_export_canvas_csv_dialog_state(self, page: ProjectPage, state: dict[str, Any]) -> None:
        key = self._export_canvas_csv_dialog_state_key(page)
        raw = self.settings.export_canvas_csv_dialog if isinstance(self.settings.export_canvas_csv_dialog, dict) else {}
        projects = raw.get("projects")
        if not isinstance(projects, dict):
            projects = {}
        projects[key] = state
        self.settings.export_canvas_csv_dialog = {"projects": projects}
        folder = str(state.get("folder") or "").strip()
        if folder:
            self.settings.export_dir = folder
        save_settings(self.settings)

    def _export_canvas_csv_dialog_state_key(self, page: ProjectPage) -> str:
        if page.path is not None:
            return str(Path(page.path).resolve())
        return f"unsaved:{page.project.name}:{page.project.root_canvas_id}"

    def _import_gdc(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self,
            "导入 GDC 工程",
            self.settings.workspace_dir,
            f"GameDesigner 工程 (*{PROJECT_SUFFIX});;旧版工程 (*{LEGACY_PROJECT_SUFFIX} *.json);;所有文件 (*.*)",
        )
        if not path:
            return
        self._open_project_path(Path(path))

    def _add_node(self) -> None:
        page = self._current_page()
        if not page:
            return
        if page.is_welcome:
            self._new_project()
            return
        pos = page.canvas.center_world()
        self._add_node_at(pos.x(), pos.y())

    def _data_canvas_template_for_page(self, page: ProjectPage):
        if not page.canvas_data.is_data_canvas():
            return None
        template = data_canvas_template(page.project, page.canvas_data)
        if template is not None:
            page.canvas_data.template_id = template.id
            page.active_template_id = template.id
        return template

    def _add_node_at(self, x: float, y: float) -> None:
        page = self._current_page()
        if not page:
            return
        if page.is_welcome:
            self._new_project()
            return
        if page.canvas_data.is_data_canvas():
            template = self._data_canvas_template_for_page(page)
            self._add_node_from_template_at(x, y, template.id if template is not None else None)
            return
        node = default_label_node()
        node_width, node_height = visual_node_size(node.fields, node.width, node.height)
        node.x = x - node_width / 2
        node.y = y - node_height / 2
        node.group_id = page.canvas.group_id_at_scene_pos(QPointF(x, y))
        page.canvas_data.add_node(node)
        page.canvas.rebuild()
        page.canvas.select_node(node.id)
        self._mark_dirty(page)

    def _add_canvas_node_at(self, x: float, y: float) -> str:
        page = self._current_page()
        if not page:
            return ""
        if page.is_welcome:
            self._new_project()
            return ""
        if page.canvas_data.is_data_canvas():
            return ""
        return self._add_canvas_node(page, x, y, canvas_type="normal")

    def _add_data_canvas_node_at(self, x: float, y: float) -> str:
        page = self._current_page()
        if not page:
            return ""
        if page.is_welcome:
            self._new_project()
            return ""
        template = page.project.find_template(page.active_template_id or "") or (
            page.project.templates[0] if page.project.templates else None
        )
        template_id = template.id if template is not None else ""
        return self._add_canvas_node(page, x, y, canvas_type="data", template_id=template_id)

    def _add_image_canvas_node_at(self, x: float, y: float) -> str:
        page = self._current_page()
        if not page:
            return ""
        if page.is_welcome:
            self._new_project()
            return ""
        return self._add_canvas_node(page, x, y, canvas_type="image")

    def _add_pixel_canvas_node_at(self, x: float, y: float) -> str:
        page = self._current_page()
        if not page:
            return ""
        if page.is_welcome:
            self._new_project()
            return ""
        return self._add_canvas_node(page, x, y, canvas_type="pixel")

    def _add_canvas_node(
        self,
        page: ProjectPage,
        x: float,
        y: float,
        *,
        canvas_type: str = "normal",
        template_id: str = "",
    ) -> str:
        if page.canvas_data.is_data_canvas():
            return ""
        canvas_type = canvas_type if canvas_type in {"normal", "data", "image", "pixel"} else "normal"
        if canvas_type == "data":
            title = "数据画布"
            icon = "数"
            fields = [NodeField("入口", "画布", "双击打开数据画布")]
        elif canvas_type == "image":
            title = "生图画布"
            icon = "图"
            fields = [NodeField("入口", "画布", "双击打开生图画布")]
        elif canvas_type == "pixel":
            title = "像素作画画布"
            icon = "像"
            fields = [NodeField("入口", "画布", "双击打开像素作画画布")]
        else:
            title = "新画布"
            icon = "画"
            fields = [NodeField("入口", "画布", "双击打开子画布")]
        node = Node(
            title=title,
            node_type="画布",
            icon=icon,
            x=x - 155,
            y=y - 72,
            fields=fields,
        )
        canvas = page.project.add_canvas(
            node.title,
            canvas_type=canvas_type,
            data_layout="grid",
            template_id=template_id,
            parent_canvas_id=page.canvas_id,
            parent_node_id=node.id,
        )
        node.canvas_id = canvas.id
        node.group_id = page.canvas.group_id_at_scene_pos(QPointF(x, y))
        page.canvas_data.add_node(node)
        if canvas.is_image_canvas():
            if canvas.is_pixel_canvas():
                nodes, edges = default_pixel_canvas_nodes()
                canvas.nodes.extend(nodes)
                canvas.edges.extend(edges)
                canvas.ai_rules = (
                    "像素作画画布规则：所有生成必须是专业像素游戏资产。"
                    "先生成高品质完整源图，再由本地算法按统一正方形像素格做严格网格采样；"
                    "用户指定 128x128 等目标尺寸时优先完整保留主体，等比放入目标画布并用透明边补齐，不为了填满而裁切。"
                    "严禁高分辨率插画伪装像素、模糊边缘、半透明脏边、照片级渐变和随机噪点。"
                )
            else:
                entry, output, edge = default_image_canvas_nodes()
                canvas.nodes.extend([entry, output])
                canvas.edges.append(edge)
        if canvas.is_data_canvas():
            self._sync_project_templates(page.project)
            self._refresh_project_views(page.project)
        page.canvas.rebuild()
        page.canvas.select_node(node.id)
        self._mark_dirty(page)
        self._open_canvas_page(page.project, page.path, canvas.id, source_canvas_id=page.canvas_id)
        return node.id

    def _add_link_node_at(self, x: float, y: float, file_format: str = "md") -> None:
        page = self._current_page()
        if not page:
            return
        if page.is_welcome:
            self._new_project()
            return
        if page.canvas_data.is_data_canvas():
            return
        title = "新文档"
        node = Node(
            title=title,
            node_type="超文本",
            icon="链",
            link_path="",
            link_format=file_format if file_format in {"md", "txt"} else "md",
            x=x - 155,
            y=y - 72,
            fields=[
                NodeField("文件", "资源路径", ""),
            ],
        )
        node.group_id = page.canvas.group_id_at_scene_pos(QPointF(x, y))
        page.canvas_data.add_node(node)
        page.canvas.rebuild()
        page.canvas.select_node(node.id)
        self._mark_dirty(page)
        self._open_link_document(page, node)

    def _add_blueprint_group_at(self, x: float, y: float) -> None:
        page = self._current_page()
        if not page:
            return
        if page.is_welcome:
            self._new_project()
            return
        if page.canvas_data.is_data_canvas():
            return
        group = BlueprintGroup(
            title="蓝图组",
            x=x,
            y=y,
            width=640,
            height=260,
        )
        page.canvas_data.add_group(group)
        page.canvas.rebuild()
        page.canvas.select_group(group.id)
        self._mark_dirty(page)

    def _add_note_at(self, x: float, y: float, owner_node_id: object = None) -> None:
        page = self._current_page()
        if not page or page.is_welcome:
            return
        owner_id = str(owner_node_id or "")
        if owner_id and page.canvas_data.find_node(owner_id) is None:
            owner_id = ""
        note = DesignNote(
            title="便签",
            content="",
            pinned=True,
            x=x,
            y=y,
        )
        if owner_id:
            node = page.canvas_data.find_node(owner_id)
            if node is None:
                return
            node.notes.append(note)
        else:
            page.canvas_data.notes.append(note)
        page.canvas.rebuild()
        page.canvas.select_note(note.id, owner_id)
        self._mark_dirty(page)

    def _add_node_from_template_at(self, x: float, y: float, template_id: str | None = None) -> None:
        page = self._current_page()
        if not page:
            return
        if page.is_welcome:
            self._new_project()
            return
        if page.canvas_data.is_data_canvas():
            forced_template = self._data_canvas_template_for_page(page)
            template_id = forced_template.id if forced_template is not None else template_id
        template_id = template_id or page.active_template_id
        if not template_id and page.project.templates:
            template_id = page.project.templates[0].id
        if not template_id:
            QMessageBox.information(self, "没有模板", "请先创建或选择一个节点模板。")
            return
        template = page.project.find_template(template_id)
        if not template:
            return
        page.active_template_id = template.id
        node = template.create_node(x - 155, y - 72)
        if page.canvas_data.is_data_canvas():
            node.group_id = ""
            page.canvas_data.template_id = template.id
        else:
            node.group_id = page.canvas.group_id_at_scene_pos(QPointF(x, y))
        page.canvas_data.add_node(node)
        if page.canvas_data.is_data_canvas():
            self._sync_canvas_state(page)
        page.canvas.rebuild()
        page.canvas.select_node(node.id)
        self._mark_dirty(page)

    def _set_data_canvas_layout(self, layout: str) -> None:
        page = self._current_page()
        if not page or page.is_welcome or not page.canvas_data.is_data_canvas():
            return
        if layout not in {"horizontal", "grid", "table"} or page.canvas_data.data_layout == layout:
            return
        page.canvas_data.data_layout = layout
        self._sync_canvas_state(page)
        page.refresh_canvas_mode()
        page.canvas.rebuild()
        self._mark_dirty(page)

    def _set_data_canvas_grid_rows(self, rows: int) -> None:
        page = self._current_page()
        if not page or page.is_welcome or not page.canvas_data.is_data_canvas():
            return
        normalized = max(0, int(rows))
        if page.canvas_data.data_grid_rows == normalized:
            return
        page.canvas_data.data_grid_rows = normalized
        self._sync_canvas_state(page)
        page.refresh_canvas_mode()
        page.canvas.rebuild()
        self._mark_dirty(page)

    def _set_data_canvas_row_style(self, style: str) -> None:
        page = self._current_page()
        if not page or page.is_welcome or not page.canvas_data.is_data_canvas():
            return
        if style not in {"independent", "thumbnail"} or page.canvas_data.data_row_style == style:
            return
        page.canvas_data.data_row_style = style
        self._sync_canvas_state(page)
        page.refresh_canvas_mode()
        page.canvas.rebuild()
        self._mark_dirty(page)

    def _set_data_canvas_template(self, template_id: str) -> None:
        page = self._current_page()
        if not page or page.is_welcome or not page.canvas_data.is_data_canvas():
            return
        template = page.project.find_template(template_id)
        if template is None:
            return
        page.canvas_data.template_id = template.id
        page.active_template_id = template.id
        self._sync_canvas_state(page)
        self._refresh_project_views(page.project)
        self._mark_dirty(page)

    def _import_canvas_sheet(self) -> None:
        page = self._current_page()
        if not page or page.is_welcome:
            return
        start = page.project.source_dir or self.settings.workspace_dir or str(Path.home())
        path, _ = QFileDialog.getOpenFileName(
            self,
            "导入画布数据",
            start,
            "表格文件 (*.csv *.xlsx *.xlsm)",
        )
        if not path:
            return
        try:
            template = import_canvas_sheet(page.project, page.canvas_data, path)
        except Exception as exc:  # noqa: BLE001
            QMessageBox.critical(self, "导入失败", f"无法导入表格：\n{exc}")
            return
        page.active_template_id = template.id
        self._refresh_project_views(page.project)
        self._mark_dirty(page)

    def _convert_current_canvas_type(self, target_type: str) -> None:
        page = self._current_page()
        if not page or page.is_welcome:
            return
        canvas = page.canvas_data
        target = "data" if target_type == "data" else "normal"
        if canvas.canvas_type == target:
            return

        if target == "data":
            answer = QMessageBox.question(
                self,
                "转换为排序画布",
                "转换后节点将按排序布局管理，连线功能会失效，节点不能再自由摆放。继续吗？",
            )
            if answer != QMessageBox.Yes:
                return
            canvas.canvas_type = "data"
            if canvas.data_layout not in {"horizontal", "grid", "table"}:
                canvas.data_layout = "grid"
            if page.active_template_id:
                canvas.template_id = page.active_template_id
            self._sync_canvas_state(page)
        else:
            answer = QMessageBox.question(
                self,
                "转换为自由画布",
                "转换后会恢复自由摆放能力，但之前排序画布的自动排序和表格模式将不再生效。继续吗？",
            )
            if answer != QMessageBox.Yes:
                return
            canvas.canvas_type = "normal"
            if canvas.data_layout == "table":
                canvas.data_layout = "grid"

        self._refresh_project_views(page.project)
        self._mark_dirty(page)

    def _copy_selected_nodes(self) -> None:
        page = self._current_page()
        if not page or page.is_welcome:
            return
        selected_ids = list(page.canvas.selected_node_ids)
        if not selected_ids and page.selected_node_id:
            selected_ids = [page.selected_node_id]
        selected_group_ids = list(page.canvas.selected_group_ids)
        selected_groups = [page.canvas_data.find_group(group_id) for group_id in selected_group_ids]
        copied_groups = [group.to_dict() for group in selected_groups if group is not None]
        group_node_ids = {
            node.id
            for node in page.canvas_data.nodes
            if node.group_id in selected_group_ids
        }
        selected_ids = list(dict.fromkeys([*selected_ids, *group_node_ids]))
        nodes = [page.canvas_data.find_node(node_id) for node_id in selected_ids]
        copied = [node.to_dict() for node in nodes if node is not None]
        copied_edges = self._copied_edges_for_selection(page, selected_ids, selected_group_ids)
        if not copied and not copied_groups:
            return
        self._copied_nodes = copied
        self._copied_groups = copied_groups
        self._copied_edges = copied_edges
        self._paste_serial = 0
        if copied_groups and copied:
            edge_text = f"和 {len(copied_edges)} 条组内连线" if copied_edges else ""
            self.status.showMessage(f"已复制 {len(copied_groups)} 个蓝图组、{len(copied)} 个节点{edge_text}", 2000)
        elif copied_groups:
            edge_text = f"和 {len(copied_edges)} 条组内连线" if copied_edges else ""
            self.status.showMessage(f"已复制 {len(copied_groups)} 个蓝图组{edge_text}", 2000)
        else:
            self.status.showMessage(f"已复制 {len(copied)} 个节点", 2000)

    def _paste_nodes(self) -> None:
        page = self._current_page()
        if not page or page.is_welcome or (not self._copied_nodes and not self._copied_groups):
            return
        offset = 40.0 * (self._paste_serial + 1)
        group_id_map: dict[str, str] = {}
        new_groups: list[BlueprintGroup] = []
        new_group_edges: list[Edge] = []
        if not page.canvas_data.is_data_canvas():
            for raw in self._copied_groups:
                group = self._clone_group_for_paste(raw, offset)
                group_id_map[str(raw.get("id") or "")] = group.id
                new_groups.append(group)
        new_nodes: list[Node] = []
        forced_group_memberships: dict[str, str] = {}
        for raw in self._copied_nodes:
            node = self._clone_node_for_paste(raw, offset, preserve_field_ids=page.canvas_data.is_data_canvas())
            source_group_id = str(raw.get("group_id") or "")
            if source_group_id in group_id_map:
                node.group_id = group_id_map[source_group_id]
                forced_group_memberships[node.id] = node.group_id
            elif page.canvas_data.is_data_canvas():
                node.group_id = ""
            if node.node_type == "画布":
                node.canvas_id = ""
                self._ensure_canvas_node_link(page, node, mark_dirty=False)
            elif node.node_type == "超文本":
                node.link_path = ""
                node.fields = [
                    field for field in node.fields if not (field.name == "文件" and field.data_type == "资源路径")
                ]
                node.fields.append(NodeField("文件", "资源路径", ""))
            else:
                node.canvas_id = ""
                node.link_path = ""
            new_nodes.append(node)
        node_id_map = self._build_copied_node_id_map(self._copied_nodes, new_nodes)
        if not page.canvas_data.is_data_canvas():
            new_group_edges = self._clone_edges_for_paste(self._copied_edges, group_id_map, node_id_map, offset)
        for group in new_groups:
            page.canvas_data.add_group(group)
        for node in new_nodes:
            page.canvas_data.add_node(node)
        for edge in new_group_edges:
            page.canvas_data.edges.append(edge)
        if page.canvas_data.is_data_canvas():
            self._sync_canvas_state(page)
        self._paste_serial += 1
        page.canvas.rebuild()
        if new_groups:
            page.canvas.select_group(new_groups[0].id)
        elif new_nodes:
            page.canvas.select_nodes({node.id for node in new_nodes})
        page.canvas.refresh_group_membership()
        for node in new_nodes:
            if node.id in forced_group_memberships:
                node.group_id = forced_group_memberships[node.id]
        self._mark_dirty(page)

    def _duplicate_selected(self) -> None:
        page = self._current_page()
        if not page or page.is_welcome:
            return
        self._copy_selected_nodes()
        self._paste_nodes()

    def _clone_group_for_paste(self, raw: dict[str, Any], offset: float) -> BlueprintGroup:
        group = BlueprintGroup.from_dict(copy.deepcopy(raw))
        group.id = new_id("group")
        group.x += offset
        group.y += offset
        return group

    def _copied_edges_for_selection(
        self,
        page: ProjectPage,
        selected_ids: list[str],
        selected_group_ids: list[str],
    ) -> list[dict[str, Any]]:
        selected_node_ids = set(selected_ids)
        selected_group_set = set(selected_group_ids)
        selected_group_members = {
            node.id
            for node in page.canvas_data.nodes
            if node.group_id in selected_group_set
        }
        selected_endpoints = selected_node_ids | selected_group_set | selected_group_members
        return [
            edge.to_dict()
            for edge in page.canvas_data.valid_edges()
            if edge.source in selected_endpoints and edge.target in selected_endpoints
        ]

    def _build_copied_node_id_map(self, copied_nodes: list[dict[str, Any]], new_nodes: list[Node]) -> dict[str, str]:
        id_map: dict[str, str] = {}
        for raw, node in zip(copied_nodes, new_nodes):
            source_id = str(raw.get("id") or "")
            if source_id:
                id_map[source_id] = node.id
        return id_map

    def _clone_edges_for_paste(
        self,
        copied_edges: list[dict[str, Any]],
        group_id_map: dict[str, str],
        node_id_map: dict[str, str],
        offset: float,
    ) -> list[Edge]:
        cloned_edges: list[Edge] = []
        for raw in copied_edges:
            edge = Edge.from_dict(copy.deepcopy(raw))
            source = group_id_map.get(edge.source) or node_id_map.get(edge.source)
            target = group_id_map.get(edge.target) or node_id_map.get(edge.target)
            if not source or not target or source == target:
                continue
            edge.id = new_id("edge")
            edge.source = source
            edge.target = target
            self._shift_edge_geometry(edge, offset, offset)
            cloned_edges.append(edge)
        return cloned_edges

    def _shift_edge_geometry(self, edge: Edge, dx: float, dy: float) -> None:
        if edge.orthogonal_bend_x is not None:
            edge.orthogonal_bend_x += dx
        if edge.orthogonal_bend_y is not None:
            edge.orthogonal_bend_y += dy
        for point in edge.orthogonal_route:
            point["x"] = float(point.get("x", 0.0)) + dx
            point["y"] = float(point.get("y", 0.0)) + dy

    def _clone_node_for_paste(self, raw: dict[str, Any], offset: float, preserve_field_ids: bool = False) -> Node:
        node = Node.from_dict(copy.deepcopy(raw))
        node.id = new_id("node")
        if not preserve_field_ids and not node.template_id:
            remapped_field_ids: dict[str, str] = {}
            for field in node.fields:
                remapped_field_ids[field.id] = new_id("field")
                field.id = remapped_field_ids[field.id]
            node.title_field_id = remapped_field_ids.get(node.title_field_id, "")
        node.order = 0
        node.x += offset
        node.y += offset
        return node

    def _undo(self) -> None:
        page = self._current_page()
        if not page or page.is_welcome:
            return
        history = self._project_history(page.project)
        if history is None:
            return
        snapshot = history.undo()
        if snapshot is None:
            return
        self._restore_project_snapshot(page.project, snapshot)
        self._sync_project_dirty_state(page.project)

    def _redo(self) -> None:
        page = self._current_page()
        if not page or page.is_welcome:
            return
        history = self._project_history(page.project)
        if history is None:
            return
        snapshot = history.redo()
        if snapshot is None:
            return
        self._restore_project_snapshot(page.project, snapshot)
        self._sync_project_dirty_state(page.project)

    def _edit_selected(self) -> None:
        page = self._current_page()
        if not page:
            return
        if page.is_welcome and page.selected_node_id:
            self._activate_welcome_node(page, page.selected_node_id)
            return
        if page.selected_node_id:
            self._edit_node(page.selected_node_id)
        elif page.canvas.selected_group_ids:
            self._edit_group(next(iter(page.canvas.selected_group_ids)))
        elif page.selected_edge_id:
            self._edit_edge(page.selected_edge_id)

    def _edit_node(self, node_id: str) -> None:
        from .qt_dialogs import NodeEditorDialog

        page = self._current_page()
        if not page:
            return
        node = page.canvas_data.find_node(node_id)
        if not node:
            return
        dialog = NodeEditorDialog(
            self,
            node,
            self.theme,
            page.project.templates,
            page.path,
            force_template_lock=page.canvas_data.is_data_canvas(),
        )
        if dialog.exec() != NodeEditorDialog.Accepted or not dialog.result:
            return
        result = dialog.result
        if page.canvas_data.is_data_canvas():
            template = page.project.find_template(page.canvas_data.template_id)
            if template is not None:
                template.name = result.title
                template.color = result.color
                template.title_field_id = result.title_field_id
                template.fields = [NodeField.from_dict(field.to_dict()) for field in result.fields]
                page.active_template_id = template.id
                page.canvas_data.template_id = template.id
            node.color = result.color
            node.icon = result.icon
            node.fields = [NodeField.from_dict(field.to_dict()) for field in result.fields]
            node.icon_from_title = result.icon_from_title
            node.title_field_id = result.title_field_id
            if template is not None:
                apply_template_to_node(node, template, preserve_values=True, force_lock=True)
                self._sync_canvas_state(page)
            self._refresh_project_views(page.project)
            page.canvas.rebuild()
            page.canvas.select_node(node.id)
            self._mark_dirty(page)
            return
        node.title = result.title
        node.color = result.color
        node.icon = result.icon
        node.width = result.width
        node.height = result.height
        node.fields = result.fields
        node.icon_from_title = result.icon_from_title
        node.title_field_id = result.title_field_id
        node.node_type = result.node_type
        node.canvas_id = result.canvas_id
        node.link_path = result.link_path
        node.link_format = result.link_format
        node.template_id = result.template_id
        node.template_locked = result.template_locked
        if node.node_type == "画布":
            self._ensure_canvas_node_link(page, node)
        elif node.node_type == "超文本":
            try:
                if node.link_path:
                    self._ensure_link_node_file(page, node)
            except Exception as exc:  # noqa: BLE001 - surface IO errors.
                QMessageBox.critical(self, "创建失败", f"无法创建超文本文件：\n{exc}")
                return
        if dialog.templates_changed and dialog.templates_result is not None:
            page.project.templates = dialog.templates_result
        self._sync_project_templates(page.project)
        self._refresh_project_views(page.project)
        page.canvas.rebuild()
        page.canvas.select_node(node.id)
        self._mark_dirty(page)

    def _edit_edge(self, edge_id: str) -> None:
        page = self._current_page()
        if not page:
            return
        edge = next((item for item in page.canvas_data.edges if item.id == edge_id), None)
        if not edge:
            return
        label, ok = QInputDialog.getText(self, "连线文本", "短文本（可留空）", text=edge.label)
        if not ok:
            return
        edge.label = self._normalize_edge_label(label)
        page.canvas.rebuild()
        page.canvas.select_edge(edge.id)
        self._mark_dirty(page)

    def _normalize_edge_label(self, label: str) -> str:
        return " ".join(label.split())[:EDGE_LABEL_MAX_LENGTH]

    def _edit_group(self, group_id: str) -> None:
        page = self._current_page()
        if not page or page.is_welcome:
            return
        group = page.canvas_data.find_group(group_id)
        if not group:
            return
        title, ok = QInputDialog.getText(self, "重命名蓝图组", "名称", text=group.title)
        if not ok:
            return
        title = title.strip()
        if not title:
            QMessageBox.warning(self, "名称不能为空", "请输入蓝图组名称。")
            return
        group.title = title
        item = page.canvas.group_items.get(group.id)
        if item:
            item.update()
        page.canvas.select_group(group.id)
        self._mark_dirty(page)

    def _edit_canvas_note(self, note_id: str, owner_node_id: object = None) -> None:
        from .ui.notes_dialog import NotesDialog

        page = self._current_page()
        if not page or page.is_welcome:
            return
        owner_id = str(owner_node_id or "")
        note = self._find_canvas_note(page, note_id, owner_id)
        if note is None:
            return
        dialog = NotesDialog(self, f"{note.display_title()} 便签", [note])
        if dialog.exec() != NotesDialog.Accepted or dialog.result_notes is None:
            return
        result = dialog.result_notes[0] if dialog.result_notes else None
        if result is None:
            self._delete_canvas_note(note_id, owner_id)
            return
        note.title = result.title
        note.content = result.content
        note.pinned = True
        self._refresh_project_views(page.project)
        page.canvas.rebuild()
        page.canvas.select_note(note.id, owner_id)
        self._mark_dirty(page)

    def _delete_canvas_note(self, note_id: str, owner_node_id: object = None) -> None:
        page = self._current_page()
        if not page or page.is_welcome:
            return
        owner_id = str(owner_node_id or "")
        if owner_id:
            node = page.canvas_data.find_node(owner_id)
            if node is not None:
                node.notes[:] = [note for note in node.notes if note.id != note_id]
        else:
            page.canvas_data.notes[:] = [note for note in page.canvas_data.notes if note.id != note_id]
        page.canvas.rebuild()
        page.canvas.clear_selection()
        self._mark_dirty(page)

    def _find_canvas_note(self, page: ProjectPage, note_id: str, owner_node_id: str = "") -> DesignNote | None:
        if owner_node_id:
            node = page.canvas_data.find_node(owner_node_id)
            if node is None:
                return None
            return next((note for note in node.notes if note.id == note_id), None)
        return next((note for note in page.canvas_data.notes if note.id == note_id), None)

    def _set_edge_style(self, edge_id: str, style: str) -> None:
        page = self._current_page()
        if not page or page.is_welcome:
            return
        if style not in EDGE_STYLES:
            return
        self._remember_last_edge_style(style)
        edge = next((item for item in page.canvas_data.edges if item.id == edge_id), None)
        if not edge or edge.style == style:
            return
        edge.style = style
        page.canvas.rebuild()
        page.canvas.select_edge(edge.id)
        self._mark_dirty(page)

    def _delete_selected(self) -> None:
        page = self._current_page()
        if not page or page.is_welcome:
            return
        if page.canvas.selected_note_key:
            owner_node_id, note_id = page.canvas.selected_note_key
            self._delete_canvas_note(note_id, owner_node_id)
        elif page.canvas.selected_node_ids:
            self._delete_nodes_by_ids(set(page.canvas.selected_node_ids))
        elif page.canvas.selected_group_ids:
            self._delete_group_by_id(next(iter(page.canvas.selected_group_ids)))
        elif page.selected_node_id:
            self._delete_node_by_id(page.selected_node_id)
        elif page.selected_edge_id:
            self._delete_edge_by_id(page.selected_edge_id)

    def _remove_canvas_pages(self, project: ProjectData, canvas_ids: set[str]) -> None:
        for index in reversed(range(self.tabs.count())):
            project_page = self.tabs.widget(index)
            if (
                isinstance(project_page, ProjectPage)
                and not project_page.is_welcome
                and project_page.project is project
                and project_page.canvas_id in canvas_ids
            ):
                self.tabs.removeTab(index)
                project_page.deleteLater()

    def _delete_canvas_branch(self, page: ProjectPage, canvas_id: str) -> None:
        deleted_ids = page.project.canvas_branch_ids(canvas_id)
        if not deleted_ids:
            return
        for canvas in list(page.project.canvases):
            if canvas.id not in deleted_ids:
                continue
            for node in list(canvas.nodes):
                self._delete_link_document_with_copy(page, node)
        self._remove_canvas_pages(page.project, deleted_ids)
        page.project.delete_canvas_tree(canvas_id)

    def _confirm_delete_nodes(self, page: ProjectPage, nodes: list[Node]) -> bool:
        if not nodes:
            return False
        canvas_nodes = [node for node in nodes if node.node_type == "画布" and node.canvas_id]
        link_nodes = [node for node in nodes if node.node_type == "超文本" and node.link_path]
        box = QMessageBox(self)
        box.setWindowTitle("删除节点")
        box.setIcon(QMessageBox.Warning if canvas_nodes else QMessageBox.Question)

        if len(nodes) == 1:
            node = nodes[0]
            if canvas_nodes:
                branch_ids = page.project.canvas_branch_ids(node.canvas_id)
                branch_count = max(1, len(branch_ids))
                box.setText("您删除的是画布节点，确定要删除吗？")
                box.setInformativeText(
                    f"这会同时删除关联的 {branch_count} 个画布，以及其中包含的节点和连线。"
                )
            elif link_nodes:
                box.setText(f"确定删除节点“{node.title}”吗？")
                box.setInformativeText("关联的超文本文件也会一起删除。")
            else:
                box.setText(f"确定删除节点“{node.title}”吗？")
        else:
            if canvas_nodes:
                canvas_branch_count = len({
                    canvas_id
                    for node in canvas_nodes
                    for canvas_id in page.project.canvas_branch_ids(node.canvas_id)
                })
                box.setText("选中的节点中包含画布节点，确定要删除吗？")
                box.setInformativeText(
                    f"这会同时删除 {len(nodes)} 个选中节点，以及关联的 {canvas_branch_count} 个画布。"
                )
            elif link_nodes:
                box.setText(f"确定删除选中的 {len(nodes)} 个节点吗？")
                box.setInformativeText("其中的超文本文件也会一起删除。")
            else:
                box.setText(f"确定删除选中的 {len(nodes)} 个节点吗？")

        delete_button = box.addButton("删除", QMessageBox.DestructiveRole)
        delete_button.setStyleSheet("color: #FF453A;")
        cancel_button = box.addButton("取消", QMessageBox.RejectRole)
        box.setDefaultButton(delete_button)
        box.setEscapeButton(cancel_button)
        box.exec()
        return box.clickedButton() == delete_button

    def _delete_node_by_id(self, node_id: str) -> None:
        page = self._current_page()
        if not page or page.is_welcome:
            return
        node = page.canvas_data.find_node(node_id)
        if not node:
            return
        if not self._confirm_delete_nodes(page, [node]):
            return
        self._delete_link_document_with_copy(page, node)
        if node.node_type == "画布" and node.canvas_id:
            self._delete_canvas_branch(page, node.canvas_id)
        page.canvas_data.delete_node(node.id)
        self._sync_canvas_state(page)
        page.canvas.rebuild()
        page.canvas.clear_selection()
        self._mark_dirty(page)

    def _delete_nodes_by_ids(self, node_ids: set[str]) -> None:
        page = self._current_page()
        if not page or page.is_welcome or not node_ids:
            return
        existing = [node for node in page.canvas_data.nodes if node.id in node_ids]
        if not existing:
            return
        if not self._confirm_delete_nodes(page, existing):
            return
        for node in existing:
            self._delete_link_document_with_copy(page, node)
            if node.node_type == "画布" and node.canvas_id:
                self._delete_canvas_branch(page, node.canvas_id)
        page.canvas_data.delete_nodes({node.id for node in existing})
        self._sync_canvas_state(page)
        page.canvas.rebuild()
        page.canvas.clear_selection()
        self._mark_dirty(page)

    def _delete_group_by_id(self, group_id: str) -> None:
        page = self._current_page()
        if not page or page.is_welcome:
            return
        group = page.canvas_data.find_group(group_id)
        if not group:
            return
        answer = QMessageBox.question(self, "删除蓝图组", f"确定删除蓝图组“{group.title}”吗？组内节点会保留。")
        if answer != QMessageBox.Yes:
            return
        page.canvas_data.delete_group(group.id)
        page.canvas.rebuild()
        page.canvas.clear_selection()
        self._mark_dirty(page)

    def _delete_edge_by_id(self, edge_id: str) -> None:
        page = self._current_page()
        if not page or page.is_welcome:
            return
        page.canvas_data.delete_edge(edge_id)
        page.canvas.rebuild()
        page.canvas.clear_selection()
        self._mark_dirty(page)

    def _create_edge(self, source: str, target: str) -> None:
        page = self._current_page()
        if not page or page.is_welcome:
            return
        existing = next(
            (item for item in page.canvas_data.edges if item.source == source and item.target == target),
            None,
        )
        edge = page.canvas_data.add_edge(source, target)
        if not edge:
            return
        if existing is None and self._last_edge_style in EDGE_STYLES:
            edge.style = self._last_edge_style
        page.canvas.rebuild()
        page.canvas.select_edge(edge.id)
        self._mark_dirty(page)

    def _exec_app_context_menu(self, menu: QMenu) -> QAction | None:
        return menu.exec(QCursor.pos())

    def _generate_image_canvas(self, page: ProjectPage | None = None) -> None:
        page = page or self._current_page()
        if not page or page.is_welcome or not page.canvas_data.is_image_canvas():
            return
        output = find_image_output_node(page.canvas_data)
        if output is None:
            QMessageBox.information(self, "生图画布", "当前生图画布没有输出节点。")
            return
        request_data = build_image_canvas_request(
            page.canvas_data,
            output.id,
            project=page.project,
            project_path=page.path,
        )
        if not request_data.prompt.strip():
            QMessageBox.information(self, "生图画布", "请先在入口节点或输出节点写入提示词。")
            return
        set_prompt = request_data.prompt.strip()
        apply_image_output_result(output, set_prompt, next((field.image_path for field in output.fields if field.name == IMAGE_CANVAS_IMAGE_FIELD), ""))
        page.canvas.rebuild()
        page.canvas.select_node(output.id)
        self._mark_dirty(page)
        self._open_ai_image_assistant()
        if hasattr(self.ai_assistant_panel, "bind_canvas"):
            self.ai_assistant_panel.bind_canvas(
                page.canvas_data.name,
                output.id,
                page.canvas_data.id,
                page.canvas_data.is_pixel_canvas(),
            )
        if hasattr(self.ai_assistant_panel, "generate_with_prompt"):
            self.ai_assistant_panel.generate_with_prompt(
                set_prompt,
                request_data.reference_paths,
                output_node_id=output.id,
            )

    def _on_ai_image_generation_succeeded(self, cached_image: object, prompt: str, output_node_id: str) -> None:
        from .image_ai import CachedAiImage

        if not isinstance(cached_image, CachedAiImage):
            return
        target = self._image_canvas_output_target(output_node_id)
        if target is None:
            return
        project, canvas, output = target
        if output is None:
            return
        apply_image_output_result(output, prompt, str(cached_image.path))
        open_pages = [
            project_page
            for project_page in self._project_pages(project)
            if project_page.canvas_id == canvas.id
        ]
        for project_page in open_pages:
            project_page.canvas.rebuild()
            project_page.canvas.select_node(output.id)
        dirty_page = open_pages[0] if open_pages else next(iter(self._project_pages(project)), None)
        if dirty_page is not None:
            self._mark_dirty(dirty_page)

    def _image_canvas_output_target(self, output_node_id: str) -> tuple[ProjectData, CanvasData, Node] | None:
        if output_node_id:
            seen_projects: set[int] = set()
            for project_page in self._open_project_pages():
                project_key = id(project_page.project)
                if project_key in seen_projects:
                    continue
                seen_projects.add(project_key)
                for canvas in project_page.project.canvases:
                    if not canvas.is_image_canvas():
                        continue
                    output = canvas.find_node(output_node_id)
                    if output is not None:
                        return project_page.project, canvas, output
        page = self._current_page()
        if page and not page.is_welcome and page.canvas_data.is_image_canvas():
            output = find_image_output_node(page.canvas_data)
            if output is not None:
                return page.project, page.canvas_data, output
        return None

    def _retouch_image_canvas(self, page: ProjectPage | None = None) -> None:
        page = page or self._current_page()
        if not page or page.is_welcome or not page.canvas_data.is_image_canvas():
            return
        output = find_image_output_node(page.canvas_data)
        if output is None:
            return
        previous_id = output.id
        convert_output_node_to_reference(output)
        new_output = new_image_output_node_from_previous(output)
        page.canvas_data.add_node(new_output)
        edge = edge_with_label(previous_id, new_output.id, IMAGE_CANVAS_EDIT_EDGE_LABEL)
        if self._last_edge_style in EDGE_STYLES:
            edge.style = self._last_edge_style
        page.canvas_data.edges.append(edge)
        page.canvas.rebuild()
        page.canvas.select_node(new_output.id)
        self._mark_dirty(page)
        self._open_ai_image_assistant()
        if hasattr(self.ai_assistant_panel, "bind_canvas"):
            self.ai_assistant_panel.bind_canvas(
                page.canvas_data.name,
                new_output.id,
                page.canvas_data.id,
                page.canvas_data.is_pixel_canvas(),
            )

    def _remember_last_edge_style(self, style: str) -> None:
        if style not in EDGE_STYLES or self._last_edge_style == style:
            return
        self._last_edge_style = style
        self.settings.last_edge_style = style
        save_settings(self.settings)

    def _manage_templates(self) -> None:
        from .qt_dialogs import TemplateManagerDialog

        page = self._current_page()
        if not page or page.is_welcome:
            return
        dialog = TemplateManagerDialog(self, page.project.templates, self.theme, page.path)
        if dialog.exec() != TemplateManagerDialog.Accepted or dialog.result is None:
            return
        for template in dialog.result:
            if not template.id:
                template.id = new_id("template")
        page.project.templates = dialog.result
        self._sync_project_templates(page.project)
        self._refresh_project_views(page.project)
        self._mark_dirty(page)

    def _open_ai_chat(self) -> None:
        self._open_ai_assistant_panel()

    def _open_or_create_image_canvas_from_sidebar(self) -> None:
        self._open_or_create_canvas_type_from_sidebar("image")

    def _open_or_create_pixel_canvas_from_sidebar(self) -> None:
        self._open_or_create_canvas_type_from_sidebar("pixel")

    def _open_or_create_canvas_type_from_sidebar(self, canvas_type: str) -> None:
        target_type = canvas_type if canvas_type in {"image", "pixel"} else "image"
        page = self._current_page()
        if not page:
            return
        if page.is_welcome:
            self._new_project()
            return
        if page.canvas_data.canvas_type == target_type:
            self._open_ai_image_assistant()
            return
        if page.canvas_data.is_data_canvas():
            return
        existing = next((canvas for canvas in page.project.canvases if canvas.canvas_type == target_type), None)
        if existing is not None:
            self._open_canvas_page(page.project, page.path, existing.id, source_canvas_id=page.canvas_id)
            return
        pos = page.canvas.center_world()
        if target_type == "pixel":
            self._add_pixel_canvas_node_at(pos.x(), pos.y())
        else:
            self._add_image_canvas_node_at(pos.x(), pos.y())

    def _open_ai_image_assistant(self) -> None:
        from .ui.ai_image_panel import AiImagePanel

        if not isinstance(self.ai_assistant_panel, AiImagePanel):
            panel = AiImagePanel(self, self.settings, self._current_ai_project_context)
            panel.collapseRequested.connect(self._collapse_ai_assistant)
            panel.generationSucceeded.connect(self._on_ai_image_generation_succeeded)
            self.ai_assistant_panel = panel
            self.ai_assistant_stack.addWidget(panel)
        self.ai_assistant_expanded = True
        self.ai_assistant_stack.setFixedWidth(820)
        self.ai_assistant_stack.setCurrentWidget(self.ai_assistant_panel)
        if hasattr(self.ai_assistant_panel, "load_project_cache"):
            self.ai_assistant_panel.load_project_cache()
        if hasattr(self.ai_assistant_panel, "bind_canvas"):
            page = self._current_page()
            if page and not page.is_welcome and page.canvas_data.is_image_canvas():
                self.ai_assistant_panel.bind_canvas(
                    page.canvas_data.name,
                    self._image_canvas_output_node_id(page.canvas_data),
                    page.canvas_data.id,
                    page.canvas_data.is_pixel_canvas(),
                )
            else:
                self.ai_assistant_panel.bind_canvas("", "")
        self.ai_assistant_panel.show()
        self.ai_assistant_panel.input.setFocus(Qt.OtherFocusReason)

    def _open_sequence_frame_dialog(self, *, pixel_mode: bool = False) -> None:
        page = self._current_page()
        if not page or page.is_welcome:
            QMessageBox.information(self, "序列帧动画", "请先打开一个项目画布。")
            return
        initial_path = self._sequence_frame_initial_path(page)
        output_path = self._sequence_frame_output_path(page, pixel_mode)
        dialog = SequenceFrameDialog(
            self,
            pixel_mode=pixel_mode,
            initial_path=initial_path,
            output_path=output_path,
        )
        dialog.exec()

    def _sequence_frame_initial_path(self, page: ProjectPage) -> str | None:
        candidates = []
        if page.canvas_data.is_image_canvas():
            output = find_image_output_node(page.canvas_data)
            if output is not None:
                candidates.append(output)
        if len(page.canvas.selected_node_ids) == 1:
            selected_id = next(iter(page.canvas.selected_node_ids))
            selected = page.canvas_data.find_node(selected_id)
            if selected is not None:
                candidates.append(selected)
        for node in candidates:
            for field in node.fields:
                if field.data_type == "图片" and field.image_path:
                    image_path = Path(field.image_path)
                    if image_path.exists():
                        return str(image_path)
        return None

    def _sequence_frame_output_path(self, page: ProjectPage, pixel_mode: bool) -> Path | None:
        if not page.path:
            return None
        folder = project_bundle_dir(page.path) / ("pixel_sequence_frames" if pixel_mode else "sequence_frames")
        folder.mkdir(parents=True, exist_ok=True)
        base_name = _safe_filename(page.canvas_data.name or ("像素序列帧" if pixel_mode else "序列帧"))
        path = folder / f"{base_name}.png"
        index = 2
        while path.exists():
            path = folder / f"{base_name}_{index}.png"
            index += 1
        return path

    def _open_ai_iteration_assistant(self) -> None:
        panel = self._open_ai_assistant_panel()
        if hasattr(panel, "enter_iteration_mode"):
            panel.enter_iteration_mode()

    def _open_ai_assistant_panel(self):
        from .ui.ai_chat_dialog import AiChatPanel

        if not isinstance(self.ai_assistant_panel, AiChatPanel):
            panel = AiChatPanel(self, self.settings, self._current_ai_project_context, self._apply_ai_canvas_actions)
            panel.collapseRequested.connect(self._collapse_ai_assistant)
            self.ai_assistant_panel = panel
            self.ai_assistant_stack.addWidget(panel)
        self.ai_assistant_expanded = True
        self.ai_assistant_stack.setFixedWidth(560)
        self.ai_assistant_stack.setCurrentWidget(self.ai_assistant_panel)
        self.ai_assistant_panel.show()
        self.ai_assistant_panel.input.setFocus(Qt.OtherFocusReason)
        return self.ai_assistant_panel

    def _collapse_ai_assistant(self) -> None:
        self.ai_assistant_expanded = False
        self.ai_assistant_stack.setFixedWidth(42)
        self.ai_assistant_stack.setCurrentWidget(self.ai_assistant_collapsed)

    def _open_ai_settings(self) -> None:
        from .ui.ai_chat_dialog import AiSettingsDialog

        dialog = AiSettingsDialog(self, self.settings)
        dialog.exec()

    def _open_canvas_notes(self) -> None:
        from .ui.notes_dialog import NotesDialog

        page = self._current_page()
        if not page or page.is_welcome:
            QMessageBox.information(self, "画布便签", "请先打开一个项目画布。")
            return
        dialog = NotesDialog(self, f"{page.canvas_data.name} 便签", page.canvas_data.notes)
        if dialog.exec() != NotesDialog.Accepted or dialog.result_notes is None:
            return
        page.canvas_data.notes = dialog.result_notes
        self._mark_dirty(page)
        page.canvas.rebuild()

    def _edit_node_notes(self, node_id: str) -> None:
        from .ui.notes_dialog import NotesDialog

        page = self._current_page()
        if not page or page.is_welcome:
            return
        node = page.canvas_data.find_node(node_id)
        if node is None:
            return
        dialog = NotesDialog(self, f"{node.title} 便签", node.notes)
        if dialog.exec() != NotesDialog.Accepted or dialog.result_notes is None:
            return
        node.notes = dialog.result_notes
        self._mark_dirty(page)
        page.canvas.rebuild()
        page.canvas.select_node(node.id)

    def _current_ai_project_context(self) -> tuple[str, Path, Path]:
        from .ai_tools import build_project_chat_context

        page = self._current_page()
        if not page or page.is_welcome:
            raise ValueError("请先打开一个项目画布，再使用 AI 助手。")
        project_path = self._ensure_project_path_for_files(page)
        context = build_project_chat_context(
            page.project,
            page.canvas_data,
            project_path,
            selected_node_ids=set(page.canvas.selected_node_ids),
            selected_group_ids=set(page.canvas.selected_group_ids),
            selected_edge_id=page.selected_edge_id,
        )
        return context, project_path.parent, project_path

    def _apply_ai_canvas_actions(self, actions: list[Any]) -> str:
        from .ai_tools import AiCanvasAction

        page = self._current_page()
        if not page or page.is_welcome:
            raise ValueError("请先打开一个项目画布，再应用 AI 画布操作。")
        if not actions:
            return "没有可应用的画布操作。"
        created = 0
        updated = 0
        created_groups = 0
        created_edges = 0
        updated_edges = 0
        updated_rules = 0
        validation_errors: list[str] = []
        tool_results = []
        selected_ids: set[str] = set()
        selected_group_ids: set[str] = set()
        selected_edge_id = ""
        base = self._ai_action_base_position(page)
        child_parent = self._ai_child_parent_node(page)
        top_level_create_count = sum(
            1
            for action in actions
            if isinstance(action, AiCanvasAction) and action.type == "create_node"
        )
        create_index = 0
        for action in actions:
            if not isinstance(action, AiCanvasAction):
                continue
            if action.type in AI_READ_ONLY_TOOL_NAMES:
                if action.type == "validate_actions":
                    tool_results.extend(self._validate_ai_canvas_actions(actions, page))
                else:
                    tool_results.append(execute_read_only_ai_canvas_tool(action, page.project, page.canvas_data))
                continue
            validation = validate_ai_canvas_tool_call(action, page.canvas_data)
            if not validation.success:
                validation_errors.append(validation.message)
                continue
            if action.type == "update_canvas_rules":
                rules = str(action.rules).strip()
                if not rules:
                    continue
                page.canvas_data.ai_rules = rules
                updated_rules += 1
                continue
            if action.type == "create_group":
                reference_group = self._reference_group_for_ai_action(page, action)
                reference_member_count = (
                    len([node for node in page.canvas_data.nodes if node.group_id == reference_group.id])
                    if reference_group is not None
                    else 0
                )
                use_reference_group = bool(
                    reference_group is not None
                    and (
                        str(getattr(action, "reference_group_id", "") or "").strip()
                        or not action.nodes
                        or len(action.nodes) == reference_member_count
                    )
                )
                if use_reference_group:
                    group, group_nodes, group_edges = self._create_group_from_reference(
                        page,
                        action,
                        reference_group,
                        base,
                        created_groups + 1,
                    )
                    page.canvas_data.add_group(group)
                    selected_group_ids.add(group.id)
                    created_groups += 1
                    for node in group_nodes:
                        page.canvas_data.add_node(node)
                        selected_ids.add(node.id)
                        created += 1
                    for edge in group_edges:
                        page.canvas_data.edges.append(edge)
                        created_edges += 1
                else:
                    group = self._group_from_ai_action(page, action, base, created_groups + 1)
                    page.canvas_data.add_group(group)
                    selected_group_ids.add(group.id)
                    created_groups += 1
                    group_base = QPointF(group.x + 32, group.y + 54)
                    for group_node_index, node_action in enumerate(action.nodes, start=1):
                        node_action.group_id = group.id
                        node = self._node_from_ai_action(page, node_action, group_base, group_node_index)
                        node.group_id = group.id
                        page.canvas_data.add_node(node)
                        selected_ids.add(node.id)
                        created += 1
                continue
            if action.type == "create_node":
                create_index += 1
                node = self._node_from_ai_action(page, action, base, create_index)
                if child_parent is not None and action.x is None and action.y is None:
                    self._position_ai_child_node(page, child_parent, node, create_index, top_level_create_count)
                page.canvas_data.add_node(node)
                if child_parent is not None and not page.canvas_data.is_data_canvas():
                    page.canvas_data.add_edge(child_parent.id, node.id)
                selected_ids.add(node.id)
                created += 1
            elif action.type == "update_node":
                node = page.canvas_data.find_node(action.node_id)
                if node is None:
                    continue
                self._apply_ai_update_to_node(node, action)
                selected_ids.add(node.id)
                updated += 1
            elif action.type == "create_edge":
                edge, error = self._create_ai_edge(page, action)
                if error:
                    validation_errors.append(error)
                    continue
                if edge is not None:
                    selected_edge_id = edge.id
                    created_edges += 1
            elif action.type == "update_edge_label":
                edge, error = self._update_ai_edge_label(page, action)
                if error:
                    validation_errors.append(error)
                    continue
                if edge is not None:
                    selected_edge_id = edge.id
                    updated_edges += 1
        mutated = bool(created or updated or created_groups or updated_rules or created_edges or updated_edges)
        if not mutated and not tool_results:
            if validation_errors:
                raise ValueError("AI 工具调用未通过校验：\n" + "\n".join(validation_errors))
            raise ValueError("AI 没有提供能应用到当前画布的操作。")
        if page.canvas_data.is_data_canvas() and mutated:
            self._sync_canvas_state(page)
            page.table_view.set_canvas(page.project, page.canvas_data)
        if mutated:
            page.canvas.rebuild()
            if selected_ids:
                page.canvas.select_nodes(selected_ids)
                if selected_group_ids:
                    page.canvas.selected_group_ids = set(selected_group_ids)
                    for group_id, item in page.canvas.group_items.items():
                        item.setSelected(group_id in selected_group_ids)
            elif selected_group_ids:
                page.canvas.select_group(next(iter(selected_group_ids)))
            elif selected_edge_id:
                page.canvas.select_edge(selected_edge_id)
            self._mark_dirty(page)
        parts: list[str] = []
        if created_groups:
            parts.append(f"创建 {created_groups} 个蓝图组")
        if created:
            parts.append(f"创建 {created} 个节点")
        if updated:
            parts.append(f"更新 {updated} 个节点")
        if created_edges:
            parts.append(f"创建 {created_edges} 条连线")
        if updated_edges:
            parts.append(f"更新 {updated_edges} 条连线文本")
        if updated_rules:
            parts.append("写入当前画布规则记忆")
        if validation_errors:
            parts.append(f"跳过 {len(validation_errors)} 个无效工具调用")
        result_prefix = "已应用到当前画布：" if mutated else "已执行画布工具："
        message = result_prefix + ("，".join(parts) if parts else "无写入操作") + "。"
        tool_text = format_ai_tool_results(tool_results)
        if tool_text:
            message += "\n工具结果：\n" + tool_text
        if validation_errors:
            message += "\n校验提示：\n" + "\n".join(f"- {error}" for error in validation_errors)
        return message

    def _validate_ai_canvas_actions(self, actions: list[Any], page: ProjectPage) -> list[Any]:
        results = []
        for action in actions:
            action_type = str(getattr(action, "type", "") or "")
            if action_type == "validate_actions":
                continue
            result = validate_ai_canvas_tool_call(action, page.canvas_data)
            if result.success and action_type == "create_edge":
                source = self._resolve_ai_endpoint_id(page, getattr(action, "source_node_id", ""))
                target = self._resolve_ai_endpoint_id(page, getattr(action, "target_node_id", ""))
                if not source or not target:
                    result.success = False
                    result.message = "create_edge 找不到源或目标端点。"
            elif result.success and action_type == "update_edge_label":
                edge = self._find_ai_edge_for_action(page, action)
                if edge is None:
                    result.success = False
                    result.message = "update_edge_label 找不到目标连线。"
            results.append(result)
        return results

    def _create_ai_edge(self, page: ProjectPage, action: Any):
        if page.canvas_data.is_data_canvas():
            return None, "排序画布不支持 create_edge。"
        source = self._resolve_ai_endpoint_id(page, getattr(action, "source_node_id", ""))
        target = self._resolve_ai_endpoint_id(page, getattr(action, "target_node_id", ""))
        if not source or not target:
            return None, "create_edge 找不到源或目标端点。"
        edge = page.canvas_data.add_edge(source, target)
        if edge is None:
            return None, "create_edge 无法创建连线。"
        label = str(getattr(action, "label", "") or "")
        if label:
            edge.label = self._normalize_edge_label(label)
        style = str(getattr(action, "style", "") or "")
        if style in EDGE_STYLES:
            edge.style = style
            self._remember_last_edge_style(style)
        return edge, ""

    def _update_ai_edge_label(self, page: ProjectPage, action: Any):
        edge = self._find_ai_edge_for_action(page, action)
        if edge is None:
            return None, "update_edge_label 找不到目标连线。"
        edge.label = self._normalize_edge_label(str(getattr(action, "label", "") or ""))
        return edge, ""

    def _find_ai_edge_for_action(self, page: ProjectPage, action: Any):
        edge_id = str(getattr(action, "edge_id", "") or "").strip()
        if edge_id:
            return next((edge for edge in page.canvas_data.edges if edge.id == edge_id), None)
        source = self._resolve_ai_endpoint_id(page, getattr(action, "source_node_id", ""))
        target = self._resolve_ai_endpoint_id(page, getattr(action, "target_node_id", ""))
        if not source or not target:
            return None
        return next(
            (
                edge
                for edge in page.canvas_data.edges
                if edge.source == source and edge.target == target
            ),
            None,
        )

    def _resolve_ai_endpoint_id(self, page: ProjectPage, value: str) -> str:
        value = str(value or "").strip()
        if not value:
            return ""
        if page.canvas_data.find_node(value) is not None or page.canvas_data.find_group(value) is not None:
            return value
        normalized = value.casefold()
        for node in page.canvas_data.nodes:
            if node.title.casefold() == normalized:
                return node.id
        for group in page.canvas_data.groups:
            if group.title.casefold() == normalized:
                return group.id
        return ""

    def _ai_action_base_position(self, page: ProjectPage) -> QPointF:
        selected = [
            node
            for node in page.canvas_data.nodes
            if node.id in page.canvas.selected_node_ids
        ]
        if selected:
            right = max(node.x + (node.width or 320) for node in selected)
            top = min(node.y for node in selected)
            return QPointF(right + 80, top)
        selected_groups = [
            group
            for group in page.canvas_data.groups
            if group.id in page.canvas.selected_group_ids
        ]
        if selected_groups:
            right = max(group.x + group.width for group in selected_groups)
            top = min(group.y for group in selected_groups)
            return QPointF(right + 80, top)
        center = page.canvas.center_world()
        return QPointF(center.x() - 180, center.y() - 90)

    def _ai_child_parent_node(self, page: ProjectPage) -> Node | None:
        if page.canvas_data.is_data_canvas() or len(page.canvas.selected_node_ids) != 1:
            return None
        parent_id = next(iter(page.canvas.selected_node_ids))
        return page.canvas_data.find_node(parent_id)

    def _position_ai_child_node(
        self,
        page: ProjectPage,
        parent: Node,
        child: Node,
        index: int,
        total: int,
    ) -> None:
        parent_width, parent_height = self._estimated_node_size(parent)
        _child_width, child_height = self._estimated_node_size(child)
        total = max(1, total)
        stack_height = total * child_height + max(0, total - 1) * AI_CHILD_NODE_GAP_Y
        child.x = parent.x + parent_width + AI_CHILD_NODE_GAP_X
        child.y = parent.y + (parent_height - stack_height) / 2 + (index - 1) * (child_height + AI_CHILD_NODE_GAP_Y)
        child.group_id = page.canvas.group_id_at_scene_pos(QPointF(child.x, child.y))

    def _estimated_node_size(self, node: Node) -> tuple[float, float]:
        visual_fields = [field for field in node.fields if field.has_visual_layout()]
        if visual_fields:
            return visual_node_size(visual_fields, node.width, node.height)
        width = max(AI_NODE_FALLBACK_WIDTH, float(node.width or 0.0))
        height = max(AI_NODE_FALLBACK_HEIGHT, float(node.height or 0.0))
        return width, height

    def _group_from_ai_action(
        self,
        page: ProjectPage,
        action: Any,
        base: QPointF,
        index: int,
    ) -> BlueprintGroup:
        x = action.x if action.x is not None else base.x() + ((index - 1) % 2) * 720
        y = action.y if action.y is not None else base.y() + ((index - 1) // 2) * 340
        width = max(360.0, float(action.width or 640.0))
        height = max(220.0, float(action.height or 300.0))
        if action.nodes:
            rows = (len(action.nodes) + 1) // 2
            height = max(height, 96.0 + rows * 240.0)
            width = max(width, 900.0 if len(action.nodes) > 1 else 460.0)
        return BlueprintGroup(
            title=action.title or "AI 蓝图组",
            x=float(x),
            y=float(y),
            width=width,
            height=height,
            color=action.color or "#486A96",
        )

    def _node_from_ai_action(
        self,
        page: ProjectPage,
        action: Any,
        base: QPointF,
        index: int,
    ) -> Node:
        x = action.x if action.x is not None else base.x() + ((index - 1) % 2) * 460
        y = action.y if action.y is not None else base.y() + ((index - 1) // 2) * 240
        template = self._template_for_ai_action(page, action)
        reference_node = self._reference_node_for_ai_action(page, action) if template is None else None
        node_type = action.node_type if action.node_type in {"普通", "画布", "超文本"} else "普通"
        if template is not None and node_type not in {"画布", "超文本"}:
            node = template.create_node(float(x), float(y))
            node.template_locked = page.canvas_data.is_data_canvas()
            node.title = action.title or node.title
            if action.icon:
                node.icon = action.icon
                node.icon_from_title = False
            if action.color:
                node.color = action.color
            self._apply_ai_fields_to_node(node, action)
        elif node_type in {"画布", "超文本"}:
            fields = [
                NodeField(field.name, field.data_type, field.value)
                for field in action.fields
            ]
            if not fields:
                fields = [NodeField("内容信息", "长文本", "")]
            node = Node(
                title=action.title or "AI 节点",
                node_type=node_type,
                icon=action.icon or ("画" if node_type == "画布" else "链"),
                icon_from_title=False,
                x=float(x),
                y=float(y),
                width=max(0.0, float(action.width or 0.0)),
                height=max(0.0, float(action.height or 0.0)),
                color=action.color or "#ffffff",
                group_id=action.group_id,
                fields=fields,
            )
        elif reference_node is not None:
            node = self._node_from_reference_for_ai_action(reference_node, action, float(x), float(y))
        else:
            node = default_label_node(
                float(x),
                float(y),
                title=action.title or "AI 节点",
            )
            if action.icon:
                node.icon = action.icon
                node.icon_from_title = False
            node.width = max(0.0, float(action.width or 0.0))
            node.height = max(0.0, float(action.height or 0.0))
            node.color = action.color or "#ffffff"
            node.group_id = action.group_id
            self._apply_ai_fields_to_label_node(node, action)
        node.x = float(x)
        node.y = float(y)
        if action.width is not None:
            node.width = max(0.0, float(action.width))
        if action.height is not None:
            node.height = max(0.0, float(action.height))
        if action.group_id:
            node.group_id = action.group_id
        if not any(group.id == node.group_id for group in page.canvas_data.groups):
            node.group_id = page.canvas.group_id_at_scene_pos(QPointF(node.x, node.y))
        if node.node_type == "画布":
            canvas = page.project.add_canvas(node.title, parent_canvas_id=page.canvas_id, parent_node_id=node.id)
            node.canvas_id = canvas.id
            node.icon = node.icon or "画"
        elif node.node_type == "超文本":
            node.link_format = "md"
            node.link_path = ""
        return node

    def _template_for_ai_action(self, page: ProjectPage, action: Any) -> NodeTemplate | None:
        if action.template_id:
            template = page.project.find_template(action.template_id)
            if template is not None:
                return template
        if page.canvas_data.is_data_canvas():
            return self._data_canvas_template_for_page(page)
        selected_nodes = [
            node
            for node in page.canvas_data.nodes
            if node.id in page.canvas.selected_node_ids
        ]
        for node in selected_nodes:
            template = page.project.find_template(node.template_id)
            if template is not None:
                return template
        for group_id in page.canvas.selected_group_ids:
            for node in page.canvas_data.nodes:
                if node.group_id != group_id:
                    continue
                template = page.project.find_template(node.template_id)
                if template is not None:
                    return template
        return None

    def is_window_fullscreen(self) -> bool:
        return self._window_fullscreen

    def toggle_window_mode(self) -> None:
        if self._window_fullscreen:
            self._exit_window_fullscreen()
        else:
            self._enter_window_fullscreen()

    def _enter_window_fullscreen(self) -> None:
        if self._window_fullscreen:
            return
        self._normal_window_geometry = QRect(self.geometry())
        screen = self._window_action_screen()
        if screen is None:
            return
        self._window_fullscreen = True
        self.setWindowState(self.windowState() & ~Qt.WindowMaximized & ~Qt.WindowFullScreen)
        self.setGeometry(screen.availableGeometry())
        self._sync_window_mode_button()

    def _exit_window_fullscreen(self) -> None:
        if not self._window_fullscreen:
            return
        geometry = self._normal_window_geometry or QRect(80, 80, 1360, 860)
        self._window_fullscreen = False
        self.setWindowState(self.windowState() & ~Qt.WindowMaximized & ~Qt.WindowFullScreen)
        self.setGeometry(self._constrain_window_geometry_to_screens(geometry))
        self._normal_window_geometry = QRect(self.geometry())
        self._sync_window_mode_button()

    def _sync_window_mode_button(self) -> None:
        titlebar = getattr(self, "titlebar", None)
        if isinstance(titlebar, CompactTitleBar):
            titlebar.set_fullscreen_mode(self._window_fullscreen)

    def _window_action_screen(self):
        for point in (QCursor.pos(), self.frameGeometry().center()):
            screen = QApplication.screenAt(point)
            if screen is not None:
                return screen
        handle = self.windowHandle()
        if handle is not None and handle.screen() is not None:
            return handle.screen()
        return QApplication.primaryScreen()

    def _constrain_window_geometry_to_screens(self, geometry: QRect) -> QRect:
        if geometry.isValid():
            for screen in QApplication.screens():
                if geometry.intersects(screen.availableGeometry()):
                    return QRect(geometry)
        screen = self._window_action_screen() or QApplication.primaryScreen()
        if screen is None:
            return QRect(geometry)
        available = screen.availableGeometry()
        width = min(max(geometry.width(), self.minimumWidth()), available.width())
        height = min(max(geometry.height(), self.minimumHeight()), available.height())
        return QRect(available.left() + 40, available.top() + 40, width, height)

    def _remember_normal_window_geometry(self) -> None:
        if self._window_fullscreen or self.isMinimized():
            return
        self._normal_window_geometry = QRect(self.geometry())

    def _window_layout_geometry(self) -> QRect:
        if self._window_fullscreen and self._normal_window_geometry is not None:
            return QRect(self._normal_window_geometry)
        return QRect(self.geometry())

    def moveEvent(self, event) -> None:  # type: ignore[override]
        super().moveEvent(event)
        self._remember_normal_window_geometry()

    def resizeEvent(self, event) -> None:  # type: ignore[override]
        super().resizeEvent(event)
        self._remember_normal_window_geometry()

    def _reference_node_for_ai_action(self, page: ProjectPage, action: Any) -> Node | None:
        if page.canvas_data.is_data_canvas():
            return None
        reference_id = str(getattr(action, "reference_node_id", "") or "").strip()
        if reference_id:
            reference = page.canvas_data.find_node(reference_id)
            if reference is not None and reference.node_type == "普通":
                return reference
        selected_nodes = [
            node
            for node in page.canvas_data.nodes
            if node.id in page.canvas.selected_node_ids and node.node_type == "普通"
        ]
        if selected_nodes:
            return sorted(selected_nodes, key=lambda node: (node.order, node.title))[0]
        target_title = str(getattr(action, "title", "") or "").strip()
        if not target_title:
            return None
        best: tuple[int, int, Node] | None = None
        for node in page.canvas_data.nodes:
            if node.node_type != "普通" or node.title.strip() == target_title:
                continue
            score = self._semantic_reference_score(target_title, node.title)
            if score <= 0:
                continue
            visual_priority = 1 if any(field.has_visual_layout() for field in node.fields) else 0
            candidate = (score, visual_priority, node)
            if best is None or candidate[:2] > best[:2]:
                best = candidate
        return best[2] if best and best[0] >= 12 else None

    def _reference_group_for_ai_action(self, page: ProjectPage, action: Any) -> BlueprintGroup | None:
        reference_id = str(getattr(action, "reference_group_id", "") or "").strip()
        if reference_id:
            reference = page.canvas_data.find_group(reference_id)
            if reference is not None:
                return reference
        selected_groups = [
            group
            for group in page.canvas_data.groups
            if group.id in page.canvas.selected_group_ids
        ]
        if len(selected_groups) == 1:
            return selected_groups[0]
        target_title = str(getattr(action, "title", "") or "").strip()
        if not target_title:
            return None
        best: tuple[int, BlueprintGroup] | None = None
        for group in page.canvas_data.groups:
            if group.title.strip() == target_title:
                continue
            score = self._semantic_reference_score(target_title, group.title)
            if score <= 0:
                continue
            candidate = (score, group)
            if best is None or candidate[0] > best[0]:
                best = candidate
        return best[1] if best and best[0] >= 12 else None

    def _semantic_reference_score(self, target_title: str, source_title: str) -> int:
        target_key = self._semantic_node_key(target_title)
        source_key = self._semantic_node_key(source_title)
        if len(target_key) < 2 or len(source_key) < 2:
            return 0
        if target_key == source_key:
            return 100 + len(target_key)
        if target_key in source_key or source_key in target_key:
            return 60 + min(len(target_key), len(source_key))
        common = self._longest_common_substring_length(target_key, source_key)
        return 10 + common if common >= 2 else 0

    def _semantic_node_key(self, title: str) -> str:
        key = title.lower()
        for term in AI_REFERENCE_COLOR_TERMS:
            key = key.replace(term.lower(), "")
        key = re.sub(r"第?[0-9０-９一二三四五六七八九十百千万]+", "", key)
        key = re.sub(r"[\s\-_·•:：,，.。()\[\]【】（）]", "", key)
        for generic in ("节点", "普通", "基础", "初级", "高级", "新", "旧"):
            key = key.replace(generic, "")
        return key

    def _longest_common_substring_length(self, left: str, right: str) -> int:
        best = 0
        for start in range(len(left)):
            for end in range(start + best + 1, len(left) + 1):
                if left[start:end] in right:
                    best = end - start
        return best

    def _node_from_reference_for_ai_action(self, reference: Node, action: Any, x: float, y: float) -> Node:
        field_id_map: dict[str, str] = {}
        fields: list[NodeField] = []
        for field in reference.fields:
            raw = field.to_dict()
            raw.pop("id", None)
            cloned = NodeField.from_dict(raw)
            field_id_map[field.id] = cloned.id
            fields.append(cloned)
        title_field_id = field_id_map.get(reference.title_field_id, "")
        node = Node(
            title=action.title or self._retarget_reference_text(reference.title, reference.title, action.title),
            node_type="普通",
            x=x,
            y=y,
            width=reference.width,
            height=reference.height,
            color=action.color or reference.color,
            icon=self._retarget_reference_icon(reference, action),
            icon_from_title=reference.icon_from_title if not action.icon else False,
            title_field_id=title_field_id,
            template_id=reference.template_id,
            fields=fields,
        )
        self._apply_ai_fields_to_reference_node(node, action, reference)
        return node

    def _create_group_from_reference(
        self,
        page: ProjectPage,
        action: Any,
        reference_group: BlueprintGroup,
        base: QPointF,
        index: int,
    ) -> tuple[BlueprintGroup, list[Node], list[Edge]]:
        reference_members = [node for node in page.canvas_data.nodes if node.group_id == reference_group.id]
        member_id_map: dict[str, str] = {}
        group = self._group_from_reference_group(page, action, reference_group, base, index)
        node_lookup = {node.id: node for node in reference_members}
        ordered_members = sorted(reference_members, key=lambda node: (node.order, node.y, node.x, node.title))
        group_nodes: list[Node] = []
        group_edges: list[Edge] = []
        node_base = QPointF(group.x, group.y)
        for member_index, reference_node in enumerate(ordered_members, start=1):
            node_action = self._action_for_reference_group_member(action, reference_node, member_index)
            node_action.group_id = group.id
            rel_x = reference_node.x - reference_group.x
            rel_y = reference_node.y - reference_group.y
            node = self._node_from_reference_group_member(
                page,
                reference_node,
                node_action,
                node_base.x() + rel_x,
                node_base.y() + rel_y,
            )
            node.group_id = group.id
            member_id_map[reference_node.id] = node.id
            group_nodes.append(node)
        group_edges.extend(
            self._clone_group_internal_edges(
                reference_group,
                member_id_map,
                group.id,
                page.canvas_data,
                group.x - reference_group.x,
                group.y - reference_group.y,
            )
        )
        group.width = max(group.width, reference_group.width)
        group.height = max(group.height, reference_group.height)
        return group, group_nodes, group_edges

    def _group_from_reference_group(
        self,
        page: ProjectPage,
        action: Any,
        reference_group: BlueprintGroup,
        base: QPointF,
        index: int,
    ) -> BlueprintGroup:
        x = action.x if action.x is not None else base.x() + ((index - 1) % 2) * (reference_group.width + 80.0)
        y = action.y if action.y is not None else base.y() + ((index - 1) // 2) * (reference_group.height + 80.0)
        return BlueprintGroup(
            title=action.title or f"{reference_group.title} 迭代",
            x=float(x),
            y=float(y),
            width=reference_group.width,
            height=reference_group.height,
            color=action.color or reference_group.color,
        )

    def _action_for_reference_group_member(self, action: Any, reference_node: Node, member_index: int) -> Any:
        node_action = next((item for item in getattr(action, "nodes", []) if getattr(item, "title", "") == reference_node.title), None)
        if node_action is not None:
            return node_action
        if member_index - 1 < len(getattr(action, "nodes", [])):
            return action.nodes[member_index - 1]
        from .ai_tools import AiCanvasFieldChange

        return type(action)(
            type="create_node",
            title=reference_node.title,
            fields=[AiCanvasFieldChange(field.name, field.data_type, field.value) for field in reference_node.fields],
            group_id=getattr(action, "group_id", ""),
        )

    def _node_from_reference_group_member(
        self,
        page: ProjectPage,
        reference_node: Node,
        action: Any,
        x: float,
        y: float,
    ) -> Node:
        node = self._node_from_reference_for_ai_action(reference_node, action, x, y)
        node.width = reference_node.width or node.width
        node.height = reference_node.height or node.height
        node.color = action.color or reference_node.color
        return node

    def _clone_group_internal_edges(
        self,
        reference_group: BlueprintGroup,
        member_id_map: dict[str, str],
        new_group_id: str,
        canvas: CanvasData,
        dx: float,
        dy: float,
    ) -> list[Edge]:
        member_ids = set(member_id_map)
        cloned_edges: list[Edge] = []
        for edge in canvas.valid_edges():
            if edge.source not in member_ids | {reference_group.id}:
                continue
            if edge.target not in member_ids | {reference_group.id}:
                continue
            source = member_id_map.get(edge.source, new_group_id)
            target = member_id_map.get(edge.target, new_group_id)
            if source == target:
                continue
            cloned = Edge.from_dict(edge.to_dict())
            cloned.id = new_id("edge")
            cloned.source = source
            cloned.target = target
            self._shift_edge_geometry(cloned, dx, dy)
            cloned_edges.append(cloned)
        return cloned_edges

    def _retarget_reference_icon(self, reference: Node, action: Any) -> str:
        if action.icon:
            return action.icon
        old_color, new_color = self._reference_color_pair(reference.title, action.title)
        icon = reference.icon
        if old_color and new_color and icon == old_color[:1]:
            return new_color[:1]
        return icon

    def _apply_ai_fields_to_reference_node(self, node: Node, action: Any, reference: Node) -> None:
        updated_ids: set[str] = set()
        if action.fields and len(action.fields) == len(node.fields):
            for field, field_change in zip(node.fields, action.fields):
                field.data_type = field_change.data_type or field.data_type
                field.value = field_change.value
                updated_ids.add(field.id)
        else:
            for field_change in action.fields:
                field = next((item for item in node.fields if item.name == field_change.name), None)
                if field is None and len(node.fields) == 1:
                    field = node.fields[0]
                if field is None and len(action.fields) == 1:
                    field = next((item for item in node.fields if item.data_type == "长文本"), None)
                if field is None:
                    continue
                field.data_type = field_change.data_type or field.data_type
                field.value = field_change.value
                updated_ids.add(field.id)
        for field in node.fields:
            if field.id not in updated_ids:
                field.value = self._retarget_reference_text(field.value, reference.title, action.title)
            if field.id == node.title_field_id and action.title:
                field.value = action.title

    def _retarget_reference_text(self, value: str, reference_title: str, target_title: str) -> str:
        old_color, new_color = self._reference_color_pair(reference_title, target_title)
        text = value
        if old_color and new_color:
            text = text.replace(old_color, new_color)
            old_short = old_color[:1]
            new_short = new_color[:1]
            if old_short != new_short:
                text = text.replace(old_short, new_short)
        return text

    def _reference_color_pair(self, reference_title: str, target_title: str) -> tuple[str, str]:
        old_color = self._first_color_term(reference_title)
        new_color = self._first_color_term(target_title)
        if not old_color or not new_color or old_color == new_color:
            return "", ""
        return old_color, new_color

    def _first_color_term(self, text: str) -> str:
        for term in AI_REFERENCE_COLOR_TERMS:
            if term in text:
                return term
        return ""

    def _apply_ai_update_to_node(self, node: Node, action: Any) -> None:
        if action.title:
            node.title = action.title
        if action.icon:
            node.icon = action.icon
            node.icon_from_title = False
        if action.color:
            node.color = action.color
        if action.width is not None:
            node.width = max(0.0, float(action.width))
        if action.height is not None:
            node.height = max(0.0, float(action.height))
        self._apply_ai_fields_to_node(node, action)

    def _apply_ai_fields_to_node(self, node: Node, action: Any) -> None:
        for field_change in action.fields:
            field = next((item for item in node.fields if item.name == field_change.name), None)
            if field is None:
                node.fields.append(NodeField(field_change.name, field_change.data_type, field_change.value))
                continue
            field.data_type = field_change.data_type
            field.value = field_change.value
        if node.title_field_id:
            title_field = next((item for item in node.fields if item.id == node.title_field_id), None)
            if title_field is not None and title_field.value.strip() and not action.title:
                node.title = title_field.value.strip()

    def _apply_ai_fields_to_label_node(self, node: Node, action: Any) -> None:
        if not action.fields or not node.fields:
            return
        label_field = node.fields[0]
        label_field.data_type = "长文本"
        if len(action.fields) == 1:
            field_change = action.fields[0]
            label_field.name = field_change.name or label_field.name
            label_field.value = field_change.value
            return
        label_field.name = "描述"
        label_field.value = "\n".join(
            f"{field_change.name}: {field_change.value}" if field_change.name else field_change.value
            for field_change in action.fields
        )

    def _reset_view(self) -> None:
        page = self._current_page()
        if page:
            page.canvas.reset_view()

    def _cancel_connection(self) -> None:
        page = self._current_page()
        if page:
            page.canvas.cancel_connection()

    def _toggle_theme(self) -> None:
        self.theme = "dark" if self.dark_mode_action.isChecked() else "light"
        self.settings.theme = self.theme
        save_settings(self.settings)
        self.setStyleSheet(stylesheet(self.theme))
        for index in range(self.tabs.count()):
            page = self.tabs.widget(index)
            if isinstance(page, ProjectPage):
                page.theme = self.theme
                page.canvas.set_theme(self.theme)
                page.preview_panel.set_theme(self.theme)
        self._update_status()

    def _position_preview_overlay(self, page: ProjectPage | None = None) -> None:
        page = page or self._current_page()
        if page is not None:
            page._position_preview_overlay()

    def _close_current_tab(self) -> None:
        index = self.tabs.currentIndex()
        if index >= 0:
            self._close_tab(index)

    def _close_tab(self, index: int) -> bool:
        page = self.tabs.widget(index)
        if not isinstance(page, ProjectPage):
            return True
        if page.dirty:
            decision = self._ask_close_dirty(page)
            if decision == "cancel":
                return False
            if decision == "save" and not self._save_project(page):
                return False
        self.tabs.removeTab(index)
        page.deleteLater()
        self._ensure_welcome_if_empty()
        self._update_title()
        self._update_status()
        return True

    def _ask_close_dirty(self, page: ProjectPage) -> str:
        box = QMessageBox(self)
        box.setWindowTitle("画布未保存")
        box.setText(f"“{page.project.name}”有未保存的更改。")
        save_button = box.addButton("保存后关闭", QMessageBox.AcceptRole)
        discard_button = box.addButton("直接关闭", QMessageBox.DestructiveRole)
        cancel_button = box.addButton("取消", QMessageBox.RejectRole)
        box.setDefaultButton(save_button)
        box.exec()
        clicked = box.clickedButton()
        if clicked == save_button:
            return "save"
        if clicked == discard_button:
            return "discard"
        if clicked == cancel_button:
            return "cancel"
        return "cancel"

    def _ensure_project_dirs(self, project: ProjectData, path: Path | None) -> None:
        if not project.source_dir:
            project.source_dir = str(path.parent if path else Path(self.settings.workspace_dir))
        if not project.output_dir:
            project.output_dir = self.settings.export_dir or str(Path(project.source_dir) / "exports")

    def _sync_settings_from_project(self, project: ProjectData) -> None:
        if project.source_dir:
            self.settings.workspace_dir = project.source_dir
        if project.output_dir:
            self.settings.export_dir = project.output_dir

    def closeEvent(self, event: QCloseEvent) -> None:  # type: ignore[override]
        self._closing_app = True
        while self.tabs.count() > 0:
            if not self._close_tab(self.tabs.count() - 1):
                self._closing_app = False
                event.ignore()
                return
        save_window_layout(self, "main_window", persist=False, geometry_override=self._window_layout_geometry())
        save_settings(self.settings)
        event.accept()


def _safe_filename(name: str) -> str:
    cleaned = re.sub(r"[\\/:*?\"<>|]+", "_", name.strip())
    return cleaned or "project"


def _ensure_suffix(path: Path, suffix: str) -> Path:
    return path if path.suffix.lower() == suffix.lower() else path.with_suffix(suffix)


def _project_name_from_path(path: Path) -> str:
    name = path.name
    if name.endswith(PROJECT_SUFFIX):
        name = name[: -len(PROJECT_SUFFIX)]
    elif name.endswith(LEGACY_PROJECT_SUFFIX):
        name = name[: -len(LEGACY_PROJECT_SUFFIX)]
    return name or path.stem


def _edge_style_name(style: str) -> str:
    return {
        "curve": "曲线",
        "straight": "直线",
        "orthogonal": "折直",
    }.get(style, "曲线")
