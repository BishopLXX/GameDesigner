from __future__ import annotations

from ctypes import wintypes
import re
import sys
from pathlib import Path

from PySide6.QtCore import QPoint, QRectF, QSize, Qt, QUrl, Signal
from PySide6.QtGui import (
    QAction,
    QColor,
    QCloseEvent,
    QCursor,
    QDesktopServices,
    QFont,
    QFontMetrics,
    QIcon,
    QKeySequence,
    QPainter,
    QPainterPath,
    QPen,
    QPixmap,
    QShortcut,
)
from PySide6.QtWidgets import (
    QApplication,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QMenu,
    QMessageBox,
    QSizePolicy,
    QSplashScreen,
    QStatusBar,
    QTabBar,
    QTabWidget,
    QToolButton,
    QInputDialog,
    QWidget,
    QVBoxLayout,
)

from .csv_io import export_all_canvas_csv
from .models import BlueprintGroup, CanvasData, Node, NodeField, ProjectData, default_project, default_tech_tree_node, new_id
from .project_files.linked_documents import (
    create_link_document,
    delete_link_document,
    delete_link_document_copy,
    rename_link_document,
    sync_link_document_copy,
)
from .qt_canvas import NodeGraphView
from .qt_dialogs import NodeEditorDialog, ProjectSettingsDialog, TemplateManagerDialog
from .qt_fonts import configure_fonts
from .qt_i18n import install_qt_translations
from .qt_theme import stylesheet
from .storage import (
    PROJECT_SUFFIX,
    LEGACY_PROJECT_SUFFIX,
    default_project_path,
    load_project,
    load_settings,
    save_project,
    save_settings,
)
from .ui.link_document_dialog import LinkDocumentDialog


WELCOME_PROJECT_NAME = "开始"
WELCOME_NEW_NODE_ID = "welcome_new_project"
WELCOME_GUIDE_NODE_ID = "welcome_guide"
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
STARTUP_SPLASH_SIZE = QSize(520, 190)


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


def _startup_splash() -> QSplashScreen:
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
    painter.drawText(QRectF(118, 47, 350, 34), Qt.AlignLeft | Qt.AlignVCenter, "GameDesigner")

    message_font = QFont()
    message_font.setPointSize(10)
    painter.setFont(message_font)
    painter.setPen(QColor("#A1A1AA"))
    painter.drawText(QRectF(120, 84, 350, 26), Qt.AlignLeft | Qt.AlignVCenter, "正在启动工程...")

    bar_rect = QRectF(120, 126, 314, 5)
    bar_bg = QPainterPath()
    bar_bg.addRoundedRect(bar_rect, 2.5, 2.5)
    painter.fillPath(bar_bg, QColor("#30303B"))
    bar_fg = QPainterPath()
    bar_fg.addRoundedRect(QRectF(bar_rect.x(), bar_rect.y(), 116, bar_rect.height()), 2.5, 2.5)
    painter.fillPath(bar_fg, QColor("#0A84FF"))
    painter.end()

    splash = QSplashScreen(pixmap)
    splash.setWindowFlag(Qt.WindowStaysOnTopHint, True)
    return splash


class ProjectPage(QWidget):
    parentJumpRequested = Signal()
    returnCloseRequested = Signal()

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
        if is_welcome:
            self.canvas.set_folder_action_node_ids({
                node_id for node_id, action in self.welcome_actions.items() if action != "new"
            })
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        layout.addWidget(self.canvas)
        self.nav_overlay = QWidget(self)
        self.nav_overlay.setObjectName("canvasNav")
        nav_layout = QHBoxLayout(self.nav_overlay)
        nav_layout.setContentsMargins(5, 5, 5, 5)
        nav_layout.setSpacing(4)
        self.parent_button = QToolButton(self.nav_overlay)
        self.parent_button.setObjectName("canvasNavButton")
        self.parent_button.setText("回跳")
        self.parent_button.setToolTip("跳到父画布")
        self.parent_button.clicked.connect(self.parentJumpRequested.emit)
        self.return_button = QToolButton(self.nav_overlay)
        self.return_button.setObjectName("canvasNavButton")
        self.return_button.setText("退回")
        self.return_button.setToolTip("保存并关闭当前画布，回到上一个画布")
        self.return_button.clicked.connect(self.returnCloseRequested.emit)
        nav_layout.addWidget(self.parent_button)
        nav_layout.addWidget(self.return_button)
        self.refresh_canvas_nav()
        self.refresh_active_template()

    def refresh_active_template(self) -> None:
        ids = [template.id for template in self.project.templates]
        if self.active_template_id not in ids:
            self.active_template_id = ids[0] if ids else None
        self.canvas.set_templates(self.project.templates)

    def refresh_canvas_nav(self) -> None:
        show_nav = bool(not self.is_welcome and self.canvas_data.parent_canvas_id)
        self.nav_overlay.setVisible(show_nav)
        if show_nav:
            self.nav_overlay.adjustSize()
            self.nav_overlay.raise_()

    def resizeEvent(self, event) -> None:  # type: ignore[override]
        super().resizeEvent(event)
        self.nav_overlay.adjustSize()
        self.nav_overlay.move(10, 10)


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
        layout.addStretch(1)
        layout.addWidget(self._action_button(window.reset_view_action, "重置"))
        layout.addWidget(self._action_button(window.dark_mode_action, "夜间"))
        layout.addSpacing(4)
        layout.addWidget(self._window_button("-", window.showMinimized))
        layout.addWidget(self._window_button("□", self._toggle_maximized))
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
        if self.window.isMaximized():
            self.window.showNormal()
        else:
            self.window.showMaximized()

    def mousePressEvent(self, event) -> None:  # type: ignore[override]
        if event.button() == Qt.LeftButton:
            self._drag_offset = event.globalPosition().toPoint() - self.window.frameGeometry().topLeft()
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event) -> None:  # type: ignore[override]
        if self._drag_offset is not None and event.buttons() & Qt.LeftButton:
            if not self.window.isMaximized():
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
    def __init__(self) -> None:
        super().__init__()
        self.setWindowFlag(Qt.FramelessWindowHint, True)
        self.settings = load_settings()
        self.theme = self.settings.theme if self.settings.theme in {"dark", "light"} else "dark"
        self._closing_app = False
        configure_fonts()
        self.setWindowTitle("GameDesigner - 游戏设计师")
        self.icon_path = _app_icon_path()
        if self.icon_path:
            self.setWindowIcon(QIcon(str(self.icon_path)))
        self.resize(1360, 860)
        self.setMinimumSize(1020, 640)
        self.setStyleSheet(stylesheet(self.theme))

        self.tabs = QTabWidget()
        self.tabs.setTabBar(AdaptiveTabBar(self.tabs))
        self.tabs.setTabsClosable(False)
        self.tabs.setMovable(True)
        self.tabs.currentChanged.connect(lambda _index: self._on_current_tab_changed())
        self.tabs.tabCloseRequested.connect(self._close_tab)
        self.setCentralWidget(self.tabs)

        self.status = QStatusBar()
        self.setStatusBar(self.status)
        self.status.hide()

        self._build_actions()
        self._build_menu()
        self._build_toolbar()
        self._bind_shortcuts()
        self._load_start_project()
        self._update_title()

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

        self.export_action = QAction("按创建顺序...", self)
        self.export_action.setShortcut(QKeySequence("Ctrl+E"))
        self.export_action.triggered.connect(lambda: self._export_all_canvas_csv("created"))

        self.export_x_action = QAction("按 X 往右排序...", self)
        self.export_x_action.triggered.connect(lambda: self._export_all_canvas_csv("x"))

        self.export_y_action = QAction("按 Y 往下排序...", self)
        self.export_y_action.triggered.connect(lambda: self._export_all_canvas_csv("y"))

        self.add_node_action = QAction("新增节点", self)
        self.add_node_action.setShortcut(QKeySequence("N"))
        self.add_node_action.triggered.connect(self._add_node)

        self.edit_action = QAction("编辑选中项", self)
        self.edit_action.setShortcut(QKeySequence(Qt.Key_Return))
        self.edit_action.triggered.connect(self._edit_selected)

        self.delete_action = QAction("删除选中项", self)
        self.delete_action.setShortcut(QKeySequence.Delete)
        self.delete_action.triggered.connect(self._delete_selected)

        self.template_action = QAction("节点模板...", self)
        self.template_action.triggered.connect(self._manage_templates)

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
        self.export_menu = self.file_menu.addMenu("导出所有画布 CSV")
        self.export_menu.addAction(self.export_action)
        self.export_menu.addAction(self.export_x_action)
        self.export_menu.addAction(self.export_y_action)
        self.file_menu.addSeparator()
        self.file_menu.addAction(self.exit_action)

        self.edit_menu = QMenu("编辑", self)
        self.edit_menu.addAction(self.add_node_action)
        self.edit_menu.addAction(self.edit_action)
        self.edit_menu.addAction(self.delete_action)
        self.edit_menu.addSeparator()
        self.edit_menu.addAction(self.template_action)

        self.view_menu = QMenu("视图", self)
        self.view_menu.addAction(self.reset_view_action)
        self.view_menu.addAction(self.dark_mode_action)

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
        if not self.isMaximized() and not self.isFullScreen():
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
        page = ProjectPage(
            project=project,
            path=path,
            dirty=dirty,
            theme=self.theme,
            canvas_data=canvas_data,
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
        return page

    def _wire_page(self, page: ProjectPage) -> None:
        canvas = page.canvas
        canvas.selectionChanged.connect(lambda node_id, edge_id, page=page: self._on_selection_changed(page, node_id, edge_id))
        canvas.projectChanged.connect(lambda page=page: self._mark_dirty(page))
        canvas.nodeActivated.connect(lambda node_id, page=page: self._activate_welcome_node(page, node_id))
        canvas.nodeFolderRequested.connect(lambda node_id, page=page: self._open_welcome_project_folder(page, node_id))
        canvas.nodeEditRequested.connect(self._edit_node)
        canvas.nodeDeleteRequested.connect(self._delete_node_by_id)
        canvas.nodesDeleteRequested.connect(self._delete_nodes_by_ids)
        canvas.groupDeleteRequested.connect(self._delete_group_by_id)
        canvas.groupEditRequested.connect(self._edit_group)
        canvas.edgeEditRequested.connect(self._edit_edge)
        canvas.edgeDeleteRequested.connect(self._delete_edge_by_id)
        canvas.edgeStyleRequested.connect(self._set_edge_style)
        canvas.edgeCreated.connect(self._create_edge)
        canvas.createNodeRequested.connect(self._add_node_at)
        canvas.createCanvasNodeRequested.connect(self._add_canvas_node_at)
        canvas.createLinkNodeRequested.connect(self._add_link_node_at)
        canvas.createGroupRequested.connect(self._add_blueprint_group_at)
        canvas.createTemplateNodeRequested.connect(self._add_node_from_template_at)
        canvas.templateManagerRequested.connect(self._manage_templates)
        canvas.openProjectRequested.connect(self._open_project)
        page.parentJumpRequested.connect(lambda page=page: self._jump_to_parent_canvas(page))
        page.returnCloseRequested.connect(lambda page=page: self._return_to_previous_canvas(page))

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
            height=326,
            fields=[
                NodeField("移动画布", "操作", "空格 + 鼠标左键拖动画布"),
                NodeField("缩放画布", "操作", "鼠标滚轮放大缩小"),
                NodeField("新建节点", "操作", "进入项目后，右键空白画布创建节点"),
                NodeField("连接节点", "操作", "右键节点选择连接，再左键点击目标节点"),
                NodeField("保存项目", "快捷键", "Ctrl+S 保存当前画布标签"),
            ],
        )
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
        project.nodes.extend([guide, create])
        project.add_edge(guide.id, create.id)

        recent_x = 350
        recent_paths = self._valid_recent_projects()
        if recent_paths:
            for index, path in enumerate(recent_paths[:5]):
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
                project.nodes.append(node)
                project.add_edge(create.id, node.id)
                actions[node_id] = str(path)
        else:
            project.nodes.append(
                Node(
                    id="welcome_no_recent",
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
            )
        return project, actions

    def _valid_recent_projects(self) -> list[Path]:
        candidates = []
        if self.settings.last_project:
            candidates.append(self.settings.last_project)
        candidates.extend(self.settings.recent_projects)
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
            if node and node.node_type == "超链接":
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

    def _project_dirty(self, project: ProjectData) -> bool:
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

    def _on_selection_changed(self, page: ProjectPage, node_id: str | None, edge_id: str | None) -> None:
        page.selected_node_id = node_id
        page.selected_edge_id = edge_id
        if page is self._current_page():
            self._update_status()

    def _mark_dirty(self, page: ProjectPage | None = None) -> None:
        page = page or self._current_page()
        if not page or page.is_welcome:
            return
        for project_page in self._project_pages(page.project):
            project_page.dirty = True
            self._update_tab_title(project_page)
        self._update_title()
        self._update_status()

    def _new_project(self) -> None:
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
                self._open_canvas_page(page.project, page.path, page.project.root_canvas_id)
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
                self.tabs.setCurrentIndex(index)
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
        canvas = self._ensure_canvas_node_link(page, node)
        if not canvas:
            return
        self._open_canvas_page(page.project, page.path, canvas.id, source_canvas_id=page.canvas_id)

    def _ensure_canvas_node_link(self, page: ProjectPage, node: Node) -> CanvasData | None:
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
                node.icon = "画"
            self._mark_dirty(page)
            return canvas
        canvas.name = node.title or canvas.name
        if canvas.id != page.project.root_canvas_id:
            canvas.parent_canvas_id = canvas.parent_canvas_id or page.canvas_id
            canvas.parent_node_id = canvas.parent_node_id or node.id
        return canvas

    def _ensure_project_path_for_files(self, page: ProjectPage) -> Path:
        self._ensure_project_dirs(page.project, page.path)
        if not page.path:
            filename = f"{_safe_filename(page.project.name)}{PROJECT_SUFFIX}"
            page.path = Path(page.project.source_dir) / filename
        Path(page.project.source_dir).mkdir(parents=True, exist_ok=True)
        Path(page.project.output_dir).mkdir(parents=True, exist_ok=True)
        return page.path

    def _ensure_link_node_file(self, page: ProjectPage, node: Node) -> None:
        if node.node_type != "超链接":
            return
        project_path = self._ensure_project_path_for_files(page)
        file_format = node.link_format if node.link_format in {"md", "txt"} else "md"
        if not node.link_path:
            node.link_path = create_link_document(project_path, node.title or "新文档", file_format)
        else:
            old_path = node.link_path
            node.link_path = rename_link_document(project_path, node.link_path, node.title or "新文档")
            if old_path != node.link_path:
                delete_link_document_copy(page.project.source_dir, old_path)
        node.link_format = file_format
        node.canvas_id = ""
        node.fields = [
            field for field in node.fields
            if not (field.name == "文件" and field.data_type == "资源路径")
        ]
        node.fields.append(NodeField("文件", "资源路径", node.link_path))
        self._sync_link_document_copy(page, node)

    def _sync_link_document_copy(self, page: ProjectPage, node: Node) -> None:
        if (
            not page.project.copy_link_docs_to_source
            or not page.path
            or node.node_type != "超链接"
            or not node.link_path
        ):
            return
        sync_link_document_copy(page.path, node.link_path, page.project.source_dir)

    def _sync_all_link_document_copies(self, project: ProjectData, project_path: Path) -> None:
        for canvas in project.canvases:
            for node in canvas.nodes:
                if node.node_type == "超链接" and node.link_path:
                    sync_link_document_copy(project_path, node.link_path, project.source_dir)

    def _delete_link_document_with_copy(self, page: ProjectPage, node: Node) -> None:
        if node.node_type != "超链接" or not node.link_path:
            return
        if page.path:
            delete_link_document(page.path, node.link_path)
        delete_link_document_copy(page.project.source_dir, node.link_path)

    def _open_link_document(self, page: ProjectPage, node: Node) -> None:
        if node.node_type != "超链接":
            return
        try:
            self._ensure_link_node_file(page, node)
        except Exception as exc:  # noqa: BLE001 - surface IO errors.
            QMessageBox.critical(self, "打开失败", f"无法准备超链接文件：\n{exc}")
            return
        dialog = LinkDocumentDialog(self, self._ensure_project_path_for_files(page), node.link_path, node.title)
        result = dialog.exec()
        if dialog.saved:
            self._sync_link_document_copy(page, node)
            self.status.showMessage(f"已保存超链接文件：{node.link_path}", 3000)
        if dialog.deleted and result == LinkDocumentDialog.Accepted:
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
        if not page or page.is_welcome:
            return False
        if not page.path:
            return self._save_as_project(page)
        try:
            self._ensure_project_dirs(page.project, page.path)
            Path(page.project.source_dir).mkdir(parents=True, exist_ok=True)
            Path(page.project.output_dir).mkdir(parents=True, exist_ok=True)
            save_project(page.project, page.path)
            if page.project.copy_link_docs_to_source:
                self._sync_all_link_document_copies(page.project, page.path)
        except Exception as exc:  # noqa: BLE001 - surface IO errors.
            QMessageBox.critical(self, "保存失败", f"无法保存项目：\n{exc}")
            return False
        self._sync_settings_from_project(page.project)
        self._remember_project(page.path)
        for project_page in self._project_pages(page.project):
            project_page.path = page.path
            project_page.dirty = False
            self._update_tab_title(project_page)
        self._update_title()
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
        page = self._current_page()
        if not page or page.is_welcome:
            return
        self._ensure_project_dirs(page.project, page.path)
        default = str(Path(page.project.output_dir or self.settings.export_dir))
        folder = QFileDialog.getExistingDirectory(
            self,
            "导出所有画布 CSV",
            default,
        )
        if not folder:
            return
        try:
            export_paths = export_all_canvas_csv(page.project, Path(folder), sort_mode)
        except Exception as exc:  # noqa: BLE001 - surface IO errors.
            QMessageBox.critical(self, "导出失败", f"无法导出所有画布 CSV：\n{exc}")
            return
        page.project.output_dir = folder
        self.settings.export_dir = folder
        save_settings(self.settings)
        self.status.showMessage(f"已导出 {len(export_paths)} 个画布 CSV：{folder}", 5000)

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

    def _add_node_at(self, x: float, y: float) -> None:
        page = self._current_page()
        if not page:
            return
        if page.is_welcome:
            self._new_project()
            return
        node = default_tech_tree_node(x - 255, y - 165)
        page.canvas_data.add_node(node)
        page.canvas.rebuild()
        page.canvas.select_node(node.id)
        self._mark_dirty(page)

    def _add_canvas_node_at(self, x: float, y: float) -> None:
        page = self._current_page()
        if not page:
            return
        if page.is_welcome:
            self._new_project()
            return
        node = Node(
            title="新画布",
            node_type="画布",
            icon="画",
            x=x - 155,
            y=y - 72,
            fields=[
                NodeField("入口", "画布", "双击打开子画布"),
            ],
        )
        canvas = page.project.add_canvas(
            node.title,
            parent_canvas_id=page.canvas_id,
            parent_node_id=node.id,
        )
        node.canvas_id = canvas.id
        page.canvas_data.add_node(node)
        page.canvas.rebuild()
        page.canvas.select_node(node.id)
        self._mark_dirty(page)
        self._open_canvas_page(page.project, page.path, canvas.id, source_canvas_id=page.canvas_id)

    def _add_link_node_at(self, x: float, y: float, file_format: str = "md") -> None:
        page = self._current_page()
        if not page:
            return
        if page.is_welcome:
            self._new_project()
            return
        project_path = self._ensure_project_path_for_files(page)
        title = "新文档"
        try:
            relative_path = create_link_document(project_path, title, file_format)
        except Exception as exc:  # noqa: BLE001 - surface IO errors.
            QMessageBox.critical(self, "创建失败", f"无法创建超链接文件：\n{exc}")
            return
        node = Node(
            title=title,
            node_type="超链接",
            icon="链",
            link_path=relative_path,
            link_format=file_format if file_format in {"md", "txt"} else "md",
            x=x - 155,
            y=y - 72,
            fields=[
                NodeField("文件", "资源路径", relative_path),
            ],
        )
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

    def _add_node_from_template_at(self, x: float, y: float, template_id: str | None = None) -> None:
        page = self._current_page()
        if not page:
            return
        if page.is_welcome:
            self._new_project()
            return
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
        page.canvas_data.add_node(node)
        page.canvas.rebuild()
        page.canvas.select_node(node.id)
        self._mark_dirty(page)

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
        page = self._current_page()
        if not page:
            return
        node = page.canvas_data.find_node(node_id)
        if not node:
            return
        dialog = NodeEditorDialog(self, node, self.theme, page.project.templates)
        if dialog.exec() != NodeEditorDialog.Accepted or not dialog.result:
            return
        result = dialog.result
        node.title = result.title
        node.color = result.color
        node.icon = result.icon
        node.width = result.width
        node.height = result.height
        node.fields = result.fields
        node.node_type = result.node_type
        node.canvas_id = result.canvas_id
        node.link_path = result.link_path
        node.link_format = result.link_format
        if node.node_type == "画布":
            self._ensure_canvas_node_link(page, node)
        elif node.node_type == "超链接":
            try:
                self._ensure_link_node_file(page, node)
            except Exception as exc:  # noqa: BLE001 - surface IO errors.
                QMessageBox.critical(self, "创建失败", f"无法创建超链接文件：\n{exc}")
                return
        if dialog.templates_changed and dialog.templates_result is not None:
            page.project.templates = dialog.templates_result
            for project_page in self._project_pages(page.project):
                project_page.refresh_active_template()
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
        label, ok = QInputDialog.getText(self, "连接标签", "标签（可留空）", text=edge.label)
        if not ok:
            return
        edge.label = label.strip()
        page.canvas.rebuild()
        page.canvas.select_edge(edge.id)
        self._mark_dirty(page)

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

    def _set_edge_style(self, edge_id: str, style: str) -> None:
        page = self._current_page()
        if not page or page.is_welcome:
            return
        if style not in {"curve", "straight", "orthogonal"}:
            return
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
        if page.canvas.selected_node_ids:
            self._delete_nodes_by_ids(set(page.canvas.selected_node_ids))
        elif page.canvas.selected_group_ids:
            self._delete_group_by_id(next(iter(page.canvas.selected_group_ids)))
        elif page.selected_node_id:
            self._delete_node_by_id(page.selected_node_id)
        elif page.selected_edge_id:
            self._delete_edge_by_id(page.selected_edge_id)

    def _delete_node_by_id(self, node_id: str) -> None:
        page = self._current_page()
        if not page or page.is_welcome:
            return
        node = page.canvas_data.find_node(node_id)
        if not node:
            return
        message = (
            f"确定删除节点“{node.title}”及其超链接文件吗？"
            if node.node_type == "超链接" and node.link_path
            else f"确定删除节点“{node.title}”吗？"
        )
        answer = QMessageBox.question(self, "删除节点", message)
        if answer != QMessageBox.Yes:
            return
        self._delete_link_document_with_copy(page, node)
        page.canvas_data.delete_node(node.id)
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
        message = f"确定删除选中的 {len(existing)} 个节点吗？"
        if any(node.node_type == "超链接" and node.link_path for node in existing):
            message = f"确定删除选中的 {len(existing)} 个节点及其中的超链接文件吗？"
        answer = QMessageBox.question(self, "删除节点", message)
        if answer != QMessageBox.Yes:
            return
        for node in existing:
            self._delete_link_document_with_copy(page, node)
        page.canvas_data.delete_nodes({node.id for node in existing})
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
        edge = page.canvas_data.add_edge(source, target)
        if not edge:
            return
        page.canvas.rebuild()
        page.canvas.select_edge(edge.id)
        self._mark_dirty(page)

    def _manage_templates(self) -> None:
        page = self._current_page()
        if not page or page.is_welcome:
            return
        dialog = TemplateManagerDialog(self, page.project.templates, self.theme)
        if dialog.exec() != TemplateManagerDialog.Accepted or dialog.result is None:
            return
        for template in dialog.result:
            if not template.id:
                template.id = new_id("template")
        page.project.templates = dialog.result
        for project_page in self._project_pages(page.project):
            project_page.refresh_active_template()
            project_page.canvas.viewport().update()
        self._mark_dirty(page)

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
        self._update_status()

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


def main() -> int:
    app = QApplication(sys.argv)
    install_qt_translations(app)
    splash = _startup_splash()
    splash.show()
    app.processEvents()
    window = GameDesignerApp()
    window.show()
    splash.finish(window)
    return app.exec()
