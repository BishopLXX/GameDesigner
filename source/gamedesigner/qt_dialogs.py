from __future__ import annotations

import copy
from dataclasses import dataclass
from pathlib import Path

from PySide6.QtCore import QPoint, QPointF, QRectF, QSize, Qt, QTimer, Signal
from PySide6.QtGui import (
    QColor,
    QFontMetrics,
    QIcon,
    QPainter,
    QPainterPath,
    QPen,
    QPixmap,
)
from PySide6.QtWidgets import (
    QButtonGroup,
    QApplication,
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QGraphicsItem,
    QGraphicsObject,
    QGraphicsProxyWidget,
    QGraphicsScene,
    QGraphicsView,
    QGroupBox,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
    QListWidget,
    QMenu,
    QMessageBox,
    QPushButton,
    QPlainTextEdit,
    QScrollArea,
    QSplitter,
    QToolButton,
    QVBoxLayout,
    QWidget,
    QColorDialog,
)

from .csv_io import CanvasCsvExportSpec, CSV_SORT_MODE_LABELS, DATA_CANVAS_SORT_LABEL
from .data_canvas import apply_template_to_node
from .image_rendering import IMAGE_FIT_MODES, draw_field_pixmap
from .models import (
    DEFAULT_NODE_COLOR,
    FIELD_EXPORT_PROPS,
    FIELD_TYPES,
    NODE_TYPES,
    CanvasData,
    Node,
    NodeField,
    NodeTemplate,
    ProjectData,
    new_id,
)
from .node_visuals import VISUAL_NODE_HEADER_HEIGHT, VISUAL_NODE_MIN_HEIGHT, VISUAL_NODE_MIN_WIDTH, visual_node_size
from .project_files.linked_documents import import_link_document, read_link_document
from .qt_theme import palette
from .storage import project_bundle_dir
from .ui.image_paint_dialog import ImagePaintDialog
from .ui.submit_text_edit import SubmitPlainTextEdit
from .window_layouts import restore_window_layout, save_window_layout


HEADER_HEIGHT = VISUAL_NODE_HEADER_HEIGHT
FIELD_HANDLE = 16.0
FIELD_SNAP_UNIT = 10.0
FIELD_SNAP_THRESHOLD = 6.0


@dataclass
class FieldSnapGuide:
    axis: str
    value: float
    label: str
    kind: str = "align"


def _safe_color(value: str, fallback: str) -> QColor:
    color = QColor(value)
    return color if color.isValid() else QColor(fallback)


def _safe_asset_name(value: str) -> str:
    cleaned = "".join("_" if char in '\\/:*?"<>|' else char for char in value.strip())
    return cleaned or "image"


def _field_text_flags(field: NodeField) -> Qt.AlignmentFlag:
    h_flags = {
        "left": Qt.AlignLeft,
        "center": Qt.AlignHCenter,
        "right": Qt.AlignRight,
    }
    v_flags = {
        "top": Qt.AlignTop,
        "center": Qt.AlignVCenter,
        "bottom": Qt.AlignBottom,
    }
    return (
        Qt.TextWordWrap
        | Qt.TextWrapAnywhere
        | h_flags.get(field.text_h_align, Qt.AlignLeft)
        | v_flags.get(field.text_v_align, Qt.AlignTop)
    )


class ProjectSettingsDialog(QDialog):
    def __init__(
        self,
        parent: QWidget | None,
        title: str,
        project_name: str,
        source_dir: str,
        output_dir: str,
        copy_link_docs_to_source: bool = False,
        project_path: str | Path | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle(title)
        self.setModal(True)
        self.project_path = Path(project_path) if project_path else None
        self.result_data: dict[str, object] | None = None

        self.name_edit = QLineEdit(project_name)
        self.source_edit = QLineEdit(source_dir)
        self.output_edit = QLineEdit(output_dir)
        self.link_copy_check = QCheckBox("超文本文件在输入目录保留复制本")
        self.link_copy_check.setChecked(copy_link_docs_to_source)

        source_button = QPushButton("浏览")
        source_button.clicked.connect(self._pick_source)
        output_button = QPushButton("浏览")
        output_button.clicked.connect(self._pick_output)

        form = QFormLayout()
        form.setFieldGrowthPolicy(QFormLayout.AllNonFixedFieldsGrow)
        form.addRow("项目名称", self.name_edit)
        form.addRow("项目源目录", self._path_row(self.source_edit, source_button))
        form.addRow("输出目录", self._path_row(self.output_edit, output_button))
        form.addRow("", self.link_copy_check)

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.button(QDialogButtonBox.Ok).setText("确定")
        buttons.button(QDialogButtonBox.Cancel).setText("取消")
        buttons.accepted.connect(self._accept)
        buttons.rejected.connect(self.reject)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(22, 22, 22, 18)
        layout.setSpacing(16)
        layout.addLayout(form)
        layout.addWidget(buttons)
        self.resize(620, 210)
        restore_window_layout(self, "project_settings_dialog")

    def _path_row(self, edit: QLineEdit, button: QPushButton) -> QWidget:
        row = QWidget()
        layout = QHBoxLayout(row)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(edit, 1)
        layout.addWidget(button)
        return row

    def _pick_source(self) -> None:
        path = QFileDialog.getExistingDirectory(
            self,
            "选择项目源目录",
            self.source_edit.text() or str(Path.home()),
        )
        if path:
            self.source_edit.setText(path)

    def _pick_output(self) -> None:
        path = QFileDialog.getExistingDirectory(
            self,
            "选择输出目录",
            self.output_edit.text() or self.source_edit.text() or str(Path.home()),
        )
        if path:
            self.output_edit.setText(path)

    def _accept(self) -> None:
        name = self.name_edit.text().strip()
        source = self.source_edit.text().strip()
        output = self.output_edit.text().strip()
        if not name:
            QMessageBox.warning(self, "项目名称不能为空", "请输入项目名称。")
            return
        if not source:
            QMessageBox.warning(self, "源目录不能为空", "请选择项目源目录。")
            return
        if not output:
            QMessageBox.warning(self, "输出目录不能为空", "请选择输出目录。")
            return
        self.result_data = {
            "name": name,
            "source_dir": source,
            "output_dir": output,
            "copy_link_docs_to_source": self.link_copy_check.isChecked(),
        }
        self.accept()

    def done(self, result: int) -> None:  # type: ignore[override]
        save_window_layout(self, "project_settings_dialog")
        super().done(result)


class ExportCanvasCsvDialog(QDialog):
    def __init__(
        self,
        parent: QWidget | None,
        project: ProjectData,
        default_folder: str,
        default_sort_mode: str = "created",
        project_path: str | Path | None = None,
        theme: str = "dark",
        export_state: dict[str, object] | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("导出所有画布 CSV")
        self.setModal(True)
        self.project_path = Path(project_path) if project_path else None
        self.project = project
        self.theme = theme if theme in {"dark", "light"} else "dark"
        self._export_state = export_state if isinstance(export_state, dict) else {}
        self._canvas_export_state = self._coerce_canvas_export_state(self._export_state.get("canvases"))
        self.result_data: dict[str, object] | None = None
        self._default_sort_mode = (
            default_sort_mode
            if default_sort_mode in CSV_SORT_MODE_LABELS
            else "created"
        )
        self._canvas_rows: dict[str, tuple[QCheckBox, QComboBox, QLineEdit]] = {}

        saved_folder = str(self._export_state.get("folder") or "").strip()
        self.folder_edit = QLineEdit(saved_folder or default_folder)
        browse_button = QPushButton("浏览")
        browse_button.clicked.connect(self._pick_folder)

        folder_form = QFormLayout()
        folder_form.setFieldGrowthPolicy(QFormLayout.AllNonFixedFieldsGrow)
        folder_form.addRow("导出目录", self._path_row(self.folder_edit, browse_button))

        select_all_button = QPushButton("全选")
        select_all_button.clicked.connect(lambda: self._set_all_enabled(True))
        clear_all_button = QPushButton("全不选")
        clear_all_button.clicked.connect(lambda: self._set_all_enabled(False))
        self.count_label = QLabel()
        controls = QHBoxLayout()
        controls.setContentsMargins(0, 0, 0, 0)
        controls.addWidget(select_all_button)
        controls.addWidget(clear_all_button)
        controls.addWidget(self.count_label)
        controls.addStretch(1)

        list_host = QWidget()
        list_host.setObjectName("exportCanvasListHost")
        self.canvas_list_layout = QVBoxLayout(list_host)
        self.canvas_list_layout.setContentsMargins(10, 10, 10, 10)
        self.canvas_list_layout.setSpacing(10)
        for canvas in self.project.canvases:
            self._add_canvas_row(canvas)
        self.canvas_list_layout.addStretch(1)

        list_scroll = QScrollArea()
        list_scroll.setObjectName("exportCanvasList")
        list_scroll.setWidgetResizable(True)
        list_scroll.setWidget(list_host)
        list_scroll.viewport().setObjectName("exportCanvasListViewport")
        list_scroll.setStyleSheet(self._list_stylesheet())

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.button(QDialogButtonBox.Ok).setText("导出")
        buttons.button(QDialogButtonBox.Cancel).setText("取消")
        buttons.accepted.connect(self._accept)
        buttons.rejected.connect(self.reject)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(18, 18, 18, 16)
        layout.setSpacing(14)
        layout.addLayout(folder_form)
        layout.addLayout(controls)
        layout.addWidget(list_scroll, 1)
        layout.addWidget(buttons)

        self.resize(720, 440)
        restore_window_layout(self, "export_canvas_csv_dialog")
        self._refresh_selected_count()

    def _path_row(self, edit: QLineEdit, button: QPushButton) -> QWidget:
        row = QWidget()
        row.setObjectName("pathRow")
        layout = QHBoxLayout(row)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)
        layout.addWidget(edit, 1)
        layout.addWidget(button)
        return row

    def _add_canvas_row(self, canvas: CanvasData) -> None:
        saved = self._state_for_canvas(canvas)
        row = QWidget()
        row.setObjectName("exportCanvasRow")
        layout = QVBoxLayout(row)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)

        checkbox = QCheckBox(self._canvas_label(canvas))
        checkbox.setObjectName("exportCanvasCheck")
        checkbox.setChecked(bool(saved.get("enabled", True)))
        checkbox.toggled.connect(lambda _checked=False: self._refresh_selected_count())

        combo = QComboBox()
        combo.setMinimumWidth(170)
        if canvas.is_data_canvas():
            combo.addItem(DATA_CANVAS_SORT_LABEL, "created")
            combo.setEnabled(False)
        else:
            for mode, label in CSV_SORT_MODE_LABELS.items():
                combo.addItem(label, mode)
            saved_sort = str(saved.get("sort_mode") or self._default_sort_mode)
            combo.setCurrentIndex(max(0, combo.findData(saved_sort)))

        header = QWidget()
        header.setObjectName("exportCanvasRowHeader")
        header_layout = QHBoxLayout(header)
        header_layout.setContentsMargins(12, 10, 12, 0)
        header_layout.setSpacing(12)
        header_layout.addWidget(checkbox, 1)
        sort_label = QLabel("排序")
        sort_label.setObjectName("exportCanvasMutedLabel")
        header_layout.addWidget(sort_label, 0)
        header_layout.addWidget(combo, 0)

        folder_edit = QLineEdit()
        folder_edit.setPlaceholderText("单独导出目录，留空则使用上方默认目录")
        folder_edit.setText(str(saved.get("target_folder") or ""))
        folder_button = QPushButton("浏览")
        folder_button.clicked.connect(lambda _checked=False, edit=folder_edit: self._pick_canvas_folder(edit))
        folder_row = self._path_row(folder_edit, folder_button)
        folder_row_host = QWidget()
        folder_row_host.setObjectName("exportCanvasFolderHost")
        folder_row_layout = QFormLayout(folder_row_host)
        folder_row_layout.setContentsMargins(12, 0, 12, 10)
        folder_row_layout.setFieldGrowthPolicy(QFormLayout.AllNonFixedFieldsGrow)
        folder_row_layout.addRow("单独目录", folder_row)

        layout.addWidget(header)
        layout.addWidget(folder_row_host)
        self.canvas_list_layout.addWidget(row)
        self._canvas_rows[canvas.id] = (checkbox, combo, folder_edit)

    def _coerce_canvas_export_state(self, raw: object) -> dict[str, dict[str, object]]:
        if not isinstance(raw, dict):
            return {}
        result: dict[str, dict[str, object]] = {}
        for key, value in raw.items():
            if isinstance(key, str) and isinstance(value, dict):
                result[key] = dict(value)
        return result

    def _state_for_canvas(self, canvas: CanvasData) -> dict[str, object]:
        direct = self._canvas_export_state.get(canvas.id)
        if direct is not None:
            return direct
        for state in self._canvas_export_state.values():
            if str(state.get("canvas_name") or "") == canvas.name:
                return state
        return {}

    def export_state(self) -> dict[str, object]:
        canvases: dict[str, object] = {}
        for canvas in self.project.canvases:
            checkbox, combo, folder_edit = self._canvas_rows[canvas.id]
            canvases[canvas.id] = {
                "canvas_name": canvas.name,
                "enabled": checkbox.isChecked(),
                "sort_mode": str(combo.currentData() or "created"),
                "target_folder": folder_edit.text().strip(),
            }
        return {
            "folder": self.folder_edit.text().strip(),
            "canvases": canvases,
        }

    def _list_stylesheet(self) -> str:
        colors = palette(self.theme)
        return f"""
        QScrollArea#exportCanvasList {{
            background: {colors["panel"]};
            border: 1px solid {colors["hairline"]};
            border-radius: 10px;
        }}
        QWidget#exportCanvasListViewport,
        QWidget#exportCanvasListHost {{
            background: {colors["panel"]};
        }}
        QWidget#exportCanvasRow {{
            background: {colors["panel_alt"]};
            border: 1px solid {colors["hairline"]};
            border-radius: 9px;
        }}
        QWidget#exportCanvasRowHeader,
        QWidget#exportCanvasFolderHost,
        QWidget#pathRow {{
            background: transparent;
        }}
        QCheckBox#exportCanvasCheck {{
            background: transparent;
            color: {colors["text"]};
            font-weight: 600;
        }}
        QLabel#exportCanvasMutedLabel {{
            color: {colors["text_muted"]};
            background: transparent;
        }}
        """

    def _canvas_label(self, canvas: CanvasData) -> str:
        name = canvas.name.strip() or "未命名画布"
        suffix = "数据画布" if canvas.is_data_canvas() else "自由画布"
        return f"{name}（{suffix}）"

    def _pick_folder(self) -> None:
        start = self.folder_edit.text().strip() or str(Path.home())
        folder = QFileDialog.getExistingDirectory(self, "选择导出目录", start)
        if folder:
            self.folder_edit.setText(folder)

    def _pick_canvas_folder(self, edit: QLineEdit) -> None:
        start = edit.text().strip() or self.folder_edit.text().strip() or str(Path.home())
        folder = QFileDialog.getExistingDirectory(self, "选择画布导出目录", start)
        if folder:
            edit.setText(folder)

    def _set_all_enabled(self, enabled: bool) -> None:
        for checkbox, _combo, _folder_edit in self._canvas_rows.values():
            checkbox.setChecked(enabled)
        self._refresh_selected_count()

    def _refresh_selected_count(self) -> None:
        total = len(self._canvas_rows)
        selected = sum(1 for checkbox, _combo, _folder_edit in self._canvas_rows.values() if checkbox.isChecked())
        self.count_label.setText(f"已选 {selected} / {total}")

    def _accept(self) -> None:
        folder = self.folder_edit.text().strip()
        if not folder:
            QMessageBox.warning(self, "导出目录不能为空", "请选择导出目录。")
            return

        specs: list[CanvasCsvExportSpec] = []
        for canvas in self.project.canvases:
            checkbox, combo, folder_edit = self._canvas_rows[canvas.id]
            mode = combo.currentData()
            specs.append(
                CanvasCsvExportSpec(
                    canvas_id=canvas.id,
                    enabled=checkbox.isChecked(),
                    sort_mode=str(mode or "created"),
                    target_folder=folder_edit.text().strip(),
                )
            )

        if not any(spec.enabled for spec in specs):
            QMessageBox.warning(self, "没有可导出的画布", "请至少勾选一个画布。")
            return

        self.result_data = {
            "folder": folder,
            "canvas_specs": specs,
            "export_state": self.export_state(),
        }
        self.accept()

    def done(self, result: int) -> None:  # type: ignore[override]
        save_window_layout(self, "export_canvas_csv_dialog")
        super().done(result)


def _display_field_text(field: NodeField, project_path: str | Path | None = None) -> str:
    value = _display_field_value(field, project_path)
    if field.data_type == "图片":
        return value
    return value or field.name


def _display_field_value(field: NodeField, project_path: str | Path | None = None) -> str:
    if field.data_type == "图片":
        return field.value
    if field.data_type != "资源路径":
        return field.value
    path_text = (field.value or "").strip()
    if not path_text:
        return ""
    suffix = Path(path_text).suffix.lower()
    if suffix not in {".md", ".txt"}:
        return path_text
    if not project_path:
        return path_text
    try:
        content = read_link_document(project_path, path_text) if path_text.startswith("linked_docs/") else Path(path_text).read_text(encoding="utf-8")
    except OSError:
        return path_text
    content = content.strip()
    return content or path_text


def _draw_field_display_text(
    painter: QPainter,
    rect: QRectF,
    field: NodeField,
    colors: dict[str, str],
    project_path: str | Path | None = None,
) -> None:
    text_color = _safe_color(field.text_color or colors["node_text"], colors["node_text"])
    font = painter.font()
    font.setPointSize(max(8, min(48, field.font_size)))
    if field.show_label and field.data_type != "图片":
        label = field.name.strip() or "字段"
        value = _display_field_value(field, project_path).strip() or " "
        label_font = painter.font()
        label_font.setPointSize(max(8, min(48, field.font_size)))
        label_font.setBold(True)
        value_font = painter.font()
        value_font.setPointSize(max(8, min(48, field.font_size)))
        value_font.setBold(False)
        label_metrics = QFontMetrics(label_font)
        value_metrics = QFontMetrics(value_font)
        label_width = min(
            max(42.0, float(label_metrics.horizontalAdvance(label) + 8)),
            max(42.0, rect.width() * 0.48),
        )
        label_rect = QRectF(rect.x(), rect.y(), label_width, rect.height())
        value_rect = QRectF(label_rect.right() + 8, rect.y(), max(1.0, rect.right() - label_rect.right() - 8), rect.height())
        label_color = QColor(text_color)
        label_color.setAlpha(178)
        painter.setPen(label_color)
        painter.setFont(label_font)
        painter.drawText(
            label_rect,
            Qt.AlignLeft | Qt.AlignVCenter | Qt.TextSingleLine,
            label_metrics.elidedText(label, Qt.ElideRight, max(1, int(label_rect.width()))),
        )
        painter.setPen(text_color)
        painter.setFont(value_font)
        painter.drawText(
            value_rect,
            Qt.AlignLeft | Qt.AlignVCenter | Qt.TextSingleLine,
            value_metrics.elidedText(value, Qt.ElideRight, max(1, int(value_rect.width()))),
        )
        return

    text = _display_field_text(field, project_path)
    if not text:
        return
    painter.setPen(text_color)
    painter.setFont(font)
    painter.drawText(rect, _field_text_flags(field), text)


class NodeFrameItem(QGraphicsObject):
    resized = Signal(float, float)

    def __init__(
        self,
        title: str,
        icon: str,
        width: float,
        height: float,
        theme: str,
        node_color: str,
    ) -> None:
        super().__init__()
        self.title = title
        self.icon = icon
        self.width = width
        self.height = height
        self.theme = theme
        self.node_color = node_color
        self._resizing = False
        self._press_pos = QPointF()
        self._origin_size = (width, height)
        self.setZValue(-10)
        self.setAcceptHoverEvents(True)

    def boundingRect(self) -> QRectF:
        return QRectF(0, 0, self.width, self.height)

    def paint(self, painter: QPainter, _option, _widget=None) -> None:  # type: ignore[override]
        colors = palette(self.theme)
        painter.setRenderHint(QPainter.Antialiasing, True)
        rect = self.boundingRect()
        path = QPainterPath()
        path.addRoundedRect(rect, 14, 14)
        painter.fillPath(path, _safe_color(self.node_color, "#FFFFFF"))
        painter.setPen(QPen(QColor(colors["node_header"]), 2.2))
        painter.drawPath(path)
        painter.save()
        painter.setClipPath(path)
        painter.fillRect(QRectF(0, 0, rect.width(), 18), QColor(colors["node_header"]))
        painter.restore()
        painter.setPen(QPen(QColor(colors["node_header_line"]), 1))
        painter.drawLine(QPointF(10, 24), QPointF(rect.width() - 10, 24))
        title = f"{self.icon}  {self.title}" if self.icon else self.title
        painter.setPen(QColor(colors["node_text"]))
        font = painter.font()
        font.setPointSize(12)
        font.setBold(True)
        painter.setFont(font)
        painter.drawText(QRectF(18, 26, rect.width() - 36, 22), Qt.AlignVCenter | Qt.AlignLeft, title)
        painter.setPen(QPen(QColor(colors["blue"]), 1.4))
        for offset in (6, 11, 16):
            painter.drawLine(
                QPointF(rect.right() - offset, rect.bottom() - 4),
                QPointF(rect.right() - 4, rect.bottom() - offset),
            )

    def hoverMoveEvent(self, event) -> None:  # type: ignore[override]
        self.setCursor(Qt.SizeFDiagCursor if self._on_handle(event.pos()) else Qt.ArrowCursor)
        super().hoverMoveEvent(event)

    def mousePressEvent(self, event) -> None:  # type: ignore[override]
        if event.button() == Qt.LeftButton and self._on_handle(event.pos()):
            self._resizing = True
            self._press_pos = event.scenePos()
            self._origin_size = (self.width, self.height)
            self.setCursor(Qt.SizeFDiagCursor)
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event) -> None:  # type: ignore[override]
        if self._resizing:
            delta = event.scenePos() - self._press_pos
            self.prepareGeometryChange()
            self.width = max(VISUAL_NODE_MIN_WIDTH, self._origin_size[0] + delta.x())
            self.height = max(VISUAL_NODE_MIN_HEIGHT, self._origin_size[1] + delta.y())
            self.resized.emit(self.width, self.height)
            self.update()
            event.accept()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event) -> None:  # type: ignore[override]
        if self._resizing:
            self._resizing = False
            self.resized.emit(self.width, self.height)
            event.accept()
            return
        super().mouseReleaseEvent(event)

    def _on_handle(self, pos: QPointF) -> bool:
        rect = self.boundingRect()
        return pos.x() >= rect.right() - FIELD_HANDLE and pos.y() >= rect.bottom() - FIELD_HANDLE


class EditorFieldItem(QGraphicsObject):
    changed = Signal()
    clicked = Signal(int)
    activated = Signal(int)

    def __init__(self, index: int, field: NodeField, theme: str, project_path: str | Path | None = None) -> None:
        super().__init__()
        self.index = index
        self.field = field
        self.theme = theme
        self.project_path = Path(project_path) if project_path else None
        self._resizing = False
        self._press_pos = QPointF()
        self._origin_rect = QRectF()
        self.setFlags(
            QGraphicsItem.ItemIsMovable
            | QGraphicsItem.ItemIsSelectable
            | QGraphicsItem.ItemSendsGeometryChanges
        )
        self.setAcceptHoverEvents(True)
        self.setPos(field.x, HEADER_HEIGHT + field.y)

    def boundingRect(self) -> QRectF:
        return QRectF(0, 0, max(44.0, self.field.width), max(34.0, self.field.height))

    def paint(self, painter: QPainter, _option, _widget=None) -> None:  # type: ignore[override]
        colors = palette(self.theme)
        painter.setRenderHint(QPainter.Antialiasing, True)
        rect = self.boundingRect()
        path = QPainterPath()
        path.addRoundedRect(rect, 10, 10)
        painter.fillPath(path, QColor(self.field.bg_color or "#FFFFFF"))

        is_image = self.field.data_type == "图片"
        if is_image and self.field.image_path:
            pixmap = QPixmap(self.field.image_path)
            if not pixmap.isNull():
                painter.save()
                painter.setClipPath(path)
                draw_field_pixmap(painter, pixmap, rect, self.field)
                painter.restore()
            else:
                painter.setPen(QColor(colors["accent_dark"]))
                painter.drawText(rect.adjusted(10, 10, -10, -10), Qt.AlignLeft | Qt.AlignTop, "图片无法读取")
        elif is_image:
            painter.setPen(QColor(colors["node_muted"]))
            painter.drawText(rect.adjusted(10, 10, -10, -10), Qt.AlignCenter | Qt.TextWordWrap, "选择图片")

        painter.setPen(QPen(QColor(colors["blue"] if self.isSelected() else "#DADAE0"), 2 if self.isSelected() else 1))
        painter.drawPath(path)

        _draw_field_display_text(painter, rect.adjusted(10, 9, -10, -9), self.field, colors, self.project_path)

        if self.isSelected():
            painter.setPen(QPen(QColor(colors["blue"]), 1.4))
            for offset in (5, 9, 13):
                painter.drawLine(
                    QPointF(rect.right() - offset, rect.bottom() - 3),
                    QPointF(rect.right() - 3, rect.bottom() - offset),
                )

    def hoverMoveEvent(self, event) -> None:  # type: ignore[override]
        self.setCursor(Qt.SizeFDiagCursor if self._on_handle(event.pos()) else Qt.OpenHandCursor)
        super().hoverMoveEvent(event)

    def mousePressEvent(self, event) -> None:  # type: ignore[override]
        self.clicked.emit(self.index)
        self.setSelected(True)
        self._resizing = self._on_handle(event.pos())
        self._press_pos = event.scenePos()
        self._origin_rect = QRectF(0, 0, self.field.width, self.field.height)
        if self._resizing:
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseDoubleClickEvent(self, event) -> None:  # type: ignore[override]
        self.clicked.emit(self.index)
        self.setSelected(True)
        self.activated.emit(self.index)
        event.accept()

    def mouseMoveEvent(self, event) -> None:  # type: ignore[override]
        if self._resizing:
            delta = event.scenePos() - self._press_pos
            width = max(44.0, self._origin_rect.width() + delta.x())
            height = max(34.0, self._origin_rect.height() + delta.y())
            canvas = self._field_canvas()
            if canvas is not None and not QApplication.keyboardModifiers() & Qt.ControlModifier:
                width, height = canvas.snap_field_resize(self, width, height)
            elif canvas is not None:
                canvas.clear_snap_guides()
            self.prepareGeometryChange()
            self.field.width = width
            self.field.height = height
            self.update()
            event.accept()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event) -> None:  # type: ignore[override]
        self.field.x = max(0.0, self.pos().x())
        self.field.y = max(0.0, self.pos().y() - HEADER_HEIGHT)
        self._resizing = False
        canvas = self._field_canvas()
        if canvas is not None:
            canvas.clear_snap_guides()
        self.changed.emit()
        super().mouseReleaseEvent(event)

    def itemChange(self, change: QGraphicsItem.GraphicsItemChange, value):  # type: ignore[override]
        if change == QGraphicsItem.ItemPositionChange and self.scene():
            pos = value
            constrained = QPointF(max(0.0, pos.x()), max(HEADER_HEIGHT, pos.y()))
            canvas = self._field_canvas()
            if canvas is None or QApplication.keyboardModifiers() & Qt.ControlModifier:
                if canvas is not None:
                    canvas.clear_snap_guides()
                return constrained
            return canvas.snap_field_position(self, constrained)
        if change == QGraphicsItem.ItemPositionHasChanged:
            self.field.x = max(0.0, self.pos().x())
            self.field.y = max(0.0, self.pos().y() - HEADER_HEIGHT)
        return super().itemChange(change, value)

    def _on_handle(self, pos: QPointF) -> bool:
        rect = self.boundingRect()
        return pos.x() >= rect.right() - FIELD_HANDLE and pos.y() >= rect.bottom() - FIELD_HANDLE

    def _field_canvas(self) -> "FieldCanvas | None":
        scene = self.scene()
        if scene is None:
            return None
        for view in scene.views():
            if isinstance(view, FieldCanvas):
                return view
        return None


class InlineFieldEditor(QPlainTextEdit):
    editingFinished = Signal()

    def focusOutEvent(self, event) -> None:  # type: ignore[override]
        self.editingFinished.emit()
        super().focusOutEvent(event)

    def keyPressEvent(self, event) -> None:  # type: ignore[override]
        if event.key() in (Qt.Key_Return, Qt.Key_Enter):
            if event.modifiers() & Qt.ShiftModifier:
                super().keyPressEvent(event)
                return
            self.editingFinished.emit()
            event.accept()
            return
        if event.key() == Qt.Key_Escape:
            self.editingFinished.emit()
            event.accept()
            return
        super().keyPressEvent(event)


class FieldCanvas(QGraphicsView):
    fieldSelected = Signal(int)
    fieldActivated = Signal(int)
    fieldContentEdited = Signal(int)
    fieldChanged = Signal()
    nodeSizeChanged = Signal(float, float)
    cardAddRequested = Signal(str, float, float)
    cardDeleteRequested = Signal(int)

    def __init__(
        self,
        fields: list[NodeField],
        theme: str,
        project_path: str | Path | None = None,
        node_width: float = 0.0,
        node_height: float = 0.0,
    ) -> None:
        super().__init__()
        self.fields = fields
        self.theme = theme
        self.project_path = Path(project_path) if project_path else None
        self.node_width = max(0.0, float(node_width))
        self.node_height = max(0.0, float(node_height))
        self.title = ""
        self.icon = ""
        self.node_color = "#FFFFFF"
        self.selected_index: int | None = 0 if fields else None
        self._panning = False
        self._last_pan = QPointF()
        self._inline_proxy: QGraphicsProxyWidget | None = None
        self._inline_editor: InlineFieldEditor | None = None
        self._inline_index: int | None = None
        self.snap_guides: list[FieldSnapGuide] = []
        self.scene_obj = QGraphicsScene(self)
        self.setScene(self.scene_obj)
        self.setRenderHints(QPainter.Antialiasing | QPainter.TextAntialiasing | QPainter.SmoothPixmapTransform)
        self.setDragMode(QGraphicsView.NoDrag)
        self.setViewportUpdateMode(QGraphicsView.FullViewportUpdate)
        self.setTransformationAnchor(QGraphicsView.AnchorUnderMouse)
        self.setFrameShape(QGraphicsView.NoFrame)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.refresh()

    def set_header(self, title: str, icon: str, node_color: str | None = None) -> None:
        self.title = title
        self.icon = icon
        if node_color is not None:
            self.node_color = node_color
        self.refresh(self.selected_index)

    def set_theme(self, theme: str) -> None:
        self.theme = theme
        self.refresh(self.selected_index)

    def set_project_path(self, project_path: str | Path | None) -> None:
        self.project_path = Path(project_path) if project_path else None
        self.refresh(self.selected_index)

    def drawBackground(self, painter: QPainter, rect: QRectF) -> None:  # type: ignore[override]
        colors = palette(self.theme)
        painter.fillRect(rect, QColor(colors["canvas"]))
        painter.setPen(QPen(QColor(colors["grid"]), 1))
        step = 40
        left = int(rect.left()) - int(rect.left()) % step
        top = int(rect.top()) - int(rect.top()) % step
        x = left
        while x < rect.right():
            painter.drawLine(QPointF(x, rect.top()), QPointF(x, rect.bottom()))
            x += step
        y = top
        while y < rect.bottom():
            painter.drawLine(QPointF(rect.left(), y), QPointF(rect.right(), y))
            y += step

    def drawForeground(self, painter: QPainter, rect: QRectF) -> None:  # type: ignore[override]
        colors = palette(self.theme)
        for guide in self.snap_guides:
            color = QColor(colors["blue"] if guide.kind == "align" else colors.get("warning", "#FF9500"))
            painter.setPen(QPen(color, 0, Qt.DashLine))
            if guide.axis == "x":
                painter.drawLine(QPointF(guide.value, rect.top()), QPointF(guide.value, rect.bottom()))
                painter.drawText(QPointF(guide.value + 8, rect.top() + 24), guide.label)
            else:
                painter.drawLine(QPointF(rect.left(), guide.value), QPointF(rect.right(), guide.value))
                painter.drawText(QPointF(rect.left() + 18, guide.value - 8), guide.label)

    def refresh(self, selected_index: int | None = None) -> None:
        self._close_inline_editor(emit_changed=False)
        self.clear_snap_guides()
        self.scene_obj.clear()
        self.selected_index = selected_index if selected_index is not None else self.selected_index
        width, height = self._card_size()
        frame = NodeFrameItem(self.title, self.icon, width, height, self.theme, self.node_color)
        frame.resized.connect(self._on_frame_resized)
        self.scene_obj.addItem(frame)
        for index, field in enumerate(self.fields):
            item = EditorFieldItem(index, field, self.theme, self.project_path)
            item.clicked.connect(self._select_item)
            item.activated.connect(self._activate_item)
            item.changed.connect(self.fieldChanged.emit)
            item.setSelected(index == self.selected_index)
            self.scene_obj.addItem(item)
        self.scene_obj.setSceneRect(QRectF(-80, -80, width + 160, height + 160))

    def _on_frame_resized(self, width: float, height: float) -> None:
        self.node_width = max(VISUAL_NODE_MIN_WIDTH, float(width))
        self.node_height = max(VISUAL_NODE_MIN_HEIGHT, float(height))
        self.scene_obj.setSceneRect(QRectF(-80, -80, self.node_width + 160, self.node_height + 160))
        self.nodeSizeChanged.emit(self.node_width, self.node_height)

    def clear_snap_guides(self) -> None:
        if not self.snap_guides:
            return
        self.snap_guides = []
        self.viewport().update()

    def snap_field_position(self, moving: EditorFieldItem, target: QPointF) -> QPointF:
        scale = max(0.001, self.transform().m11())
        snapped = QPointF(target)
        guides: list[FieldSnapGuide] = []
        target_rect = self._field_rect_at(moving, target)
        x_result = self._best_field_grid_snap(target.x(), "x", scale)
        y_result = self._best_field_grid_snap(target.y() - HEADER_HEIGHT, "y", scale, HEADER_HEIGHT)
        align_x = self._best_field_align_snap(moving, target_rect, "x", scale)
        align_y = self._best_field_align_snap(moving, target_rect, "y", scale)
        if align_x and (not x_result or align_x[1] <= x_result[1]):
            x_result = align_x
        if align_y and (not y_result or align_y[1] <= y_result[1]):
            y_result = align_y
        if x_result:
            snapped.setX(max(0.0, target.x() + x_result[0]))
            guides.append(x_result[2])
        if y_result:
            snapped.setY(max(HEADER_HEIGHT, target.y() + y_result[0]))
            guides.append(y_result[2])
        self.snap_guides = guides
        self.viewport().update()
        return snapped

    def snap_field_resize(self, moving: EditorFieldItem, width: float, height: float) -> tuple[float, float]:
        scale = max(0.001, self.transform().m11())
        snapped_width = max(44.0, width)
        snapped_height = max(34.0, height)
        target_rect = QRectF(moving.pos().x(), moving.pos().y(), snapped_width, snapped_height)
        guides: list[FieldSnapGuide] = []
        x_result = self._best_field_edge_grid_snap(target_rect.right(), "x", scale)
        y_result = self._best_field_edge_grid_snap(target_rect.bottom() - HEADER_HEIGHT, "y", scale, HEADER_HEIGHT)
        align_x = self._best_field_edge_align_snap(moving, target_rect, "x", scale)
        align_y = self._best_field_edge_align_snap(moving, target_rect, "y", scale)
        if align_x and (not x_result or align_x[1] <= x_result[1]):
            x_result = align_x
        if align_y and (not y_result or align_y[1] <= y_result[1]):
            y_result = align_y
        if x_result:
            snapped_width = max(44.0, snapped_width + x_result[0])
            guides.append(x_result[2])
        if y_result:
            snapped_height = max(34.0, snapped_height + y_result[0])
            guides.append(y_result[2])
        self.snap_guides = guides
        self.viewport().update()
        return snapped_width, snapped_height

    def _best_field_grid_snap(
        self,
        value: float,
        axis: str,
        scale: float,
        scene_offset: float = 0.0,
    ) -> tuple[float, float, FieldSnapGuide] | None:
        grid_value = round(value / FIELD_SNAP_UNIT) * FIELD_SNAP_UNIT
        distance = abs(grid_value - value) * scale
        if distance > FIELD_SNAP_THRESHOLD:
            return None
        return (
            grid_value - value,
            distance,
            FieldSnapGuide(axis, grid_value + scene_offset, f"{axis.upper()} {grid_value:.0f}", "grid"),
        )

    def _best_field_edge_grid_snap(
        self,
        value: float,
        axis: str,
        scale: float,
        scene_offset: float = 0.0,
    ) -> tuple[float, float, FieldSnapGuide] | None:
        result = self._best_field_grid_snap(value, axis, scale, scene_offset)
        if result is None:
            return None
        delta, distance, guide = result
        return delta, distance, FieldSnapGuide(axis, guide.value, f"{axis.upper()} 边 {value + delta:.0f}", "grid")

    def _best_field_align_snap(
        self,
        moving: EditorFieldItem,
        target_rect: QRectF,
        axis: str,
        scale: float,
    ) -> tuple[float, float, FieldSnapGuide] | None:
        moving_values = self._snap_axis_values(target_rect, axis)
        best: tuple[float, float, FieldSnapGuide] | None = None
        for other_rect, target_name in self._field_alignment_targets(moving):
            other_values = self._snap_axis_values(other_rect, axis)
            for moving_label, moving_value in moving_values.items():
                for other_label, other_value in other_values.items():
                    distance = abs(other_value - moving_value) * scale
                    if distance <= FIELD_SNAP_THRESHOLD:
                        delta = other_value - moving_value
                        guide = FieldSnapGuide(
                            axis,
                            other_value,
                            f"{target_name} {moving_label}/{other_label}",
                        )
                        if best is None or distance < best[1]:
                            best = (delta, distance, guide)
        return best

    def _best_field_edge_align_snap(
        self,
        moving: EditorFieldItem,
        target_rect: QRectF,
        axis: str,
        scale: float,
    ) -> tuple[float, float, FieldSnapGuide] | None:
        edge_value = target_rect.right() if axis == "x" else target_rect.bottom()
        min_edge = target_rect.left() + 44.0 if axis == "x" else target_rect.top() + 34.0
        best: tuple[float, float, FieldSnapGuide] | None = None
        for other_rect, target_name in self._field_alignment_targets(moving):
            other_values = self._snap_axis_values(other_rect, axis)
            for other_label, other_value in other_values.items():
                if other_value < min_edge:
                    continue
                distance = abs(other_value - edge_value) * scale
                if distance <= FIELD_SNAP_THRESHOLD:
                    delta = other_value - edge_value
                    guide = FieldSnapGuide(axis, other_value, f"{target_name} 边/{other_label}")
                    if best is None or distance < best[1]:
                        best = (delta, distance, guide)
        return best

    def _field_alignment_targets(self, moving: EditorFieldItem) -> list[tuple[QRectF, str]]:
        targets: list[tuple[QRectF, str]] = []
        for item in self.scene_obj.items():
            if not isinstance(item, EditorFieldItem) or item is moving:
                continue
            targets.append((item.sceneBoundingRect(), "字段"))
        width, height = self._card_size()
        content_rect = QRectF(0.0, HEADER_HEIGHT, width, max(1.0, height - HEADER_HEIGHT))
        targets.append((content_rect, "节点"))
        return targets

    def _field_rect_at(self, item: EditorFieldItem, target: QPointF) -> QRectF:
        rect = item.boundingRect()
        return QRectF(target.x(), target.y(), rect.width(), rect.height())

    def _snap_axis_values(self, rect: QRectF, axis: str) -> dict[str, float]:
        if axis == "x":
            return {"左": rect.left(), "中": rect.center().x(), "右": rect.right()}
        return {"上": rect.top(), "中": rect.center().y(), "下": rect.bottom()}

    def wheelEvent(self, event) -> None:  # type: ignore[override]
        factor = 1.12 if event.angleDelta().y() > 0 else 1 / 1.12
        current = self.transform().m11()
        target = max(0.35, min(2.6, current * factor))
        self.scale(target / current, target / current)

    def mousePressEvent(self, event) -> None:  # type: ignore[override]
        if event.button() == Qt.MiddleButton:
            self._panning = True
            self._last_pan = event.position()
            self.setCursor(Qt.ClosedHandCursor)
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseDoubleClickEvent(self, event) -> None:  # type: ignore[override]
        if event.button() == Qt.LeftButton:
            item = self._field_at(event.position().toPoint())
            if item:
                self._activate_item(item.index)
                event.accept()
                return
        super().mouseDoubleClickEvent(event)

    def mouseMoveEvent(self, event) -> None:  # type: ignore[override]
        if self._panning:
            delta = event.position() - self._last_pan
            self._last_pan = event.position()
            self.horizontalScrollBar().setValue(self.horizontalScrollBar().value() - int(delta.x()))
            self.verticalScrollBar().setValue(self.verticalScrollBar().value() - int(delta.y()))
            event.accept()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event) -> None:  # type: ignore[override]
        if event.button() == Qt.MiddleButton:
            self._panning = False
            self.setCursor(Qt.ArrowCursor)
            event.accept()
            return
        super().mouseReleaseEvent(event)

    def contextMenuEvent(self, event) -> None:  # type: ignore[override]
        item = self._field_at(event.pos())
        menu = QMenu(self)
        if item:
            self._select_item(item.index)
            delete = menu.addAction("删除卡片")
            action = menu.exec(event.globalPos())
            if action == delete:
                self.cardDeleteRequested.emit(item.index)
            return

        scene_pos = self.mapToScene(event.pos())
        field_x = max(0.0, scene_pos.x())
        field_y = max(0.0, scene_pos.y() - HEADER_HEIGHT)
        add_text = menu.addAction("新增文字卡片")
        add_image = menu.addAction("新增图片卡片")
        action = menu.exec(event.globalPos())
        if action == add_text:
            self.cardAddRequested.emit("text", field_x, field_y)
        elif action == add_image:
            self.cardAddRequested.emit("image", field_x, field_y)

    def _select_item(self, index: int) -> None:
        if self._inline_index != index:
            self._close_inline_editor()
        self.selected_index = index
        for item in self.scene_obj.items():
            if isinstance(item, EditorFieldItem):
                item.setSelected(item.index == index)
        self.fieldSelected.emit(index)

    def _activate_item(self, index: int) -> None:
        self._select_item(index)
        if index < 0 or index >= len(self.fields):
            return
        field = self.fields[index]
        if field.data_type == "图片":
            self.fieldActivated.emit(index)
            return
        self._start_inline_editor(index)

    def _start_inline_editor(self, index: int) -> None:
        self._close_inline_editor()
        if index < 0 or index >= len(self.fields):
            return
        field = self.fields[index]
        editor = InlineFieldEditor()
        editor.setPlainText(field.value)
        editor.setObjectName("inlineFieldEditor")
        editor.setStyleSheet(
            "QPlainTextEdit#inlineFieldEditor {"
            "border: 2px solid #007AFF;"
            "border-radius: 8px;"
            "padding: 6px;"
            f"color: {field.text_color or '#1D1D1F'};"
            f"background: {field.bg_color or '#FFFFFF'};"
            "}"
        )
        font = editor.font()
        font.setPointSize(max(8, min(48, field.font_size)))
        editor.setFont(font)
        editor.textChanged.connect(lambda index=index: self._apply_inline_text(index))
        editor.editingFinished.connect(lambda: self._close_inline_editor())

        proxy = QGraphicsProxyWidget()
        proxy.setWidget(editor)
        proxy.setZValue(100)
        proxy.setPos(QPointF(field.x + 6, HEADER_HEIGHT + field.y + 6))
        proxy.resize(max(40.0, field.width - 12), max(30.0, field.height - 12))
        self.scene_obj.addItem(proxy)
        self._inline_proxy = proxy
        self._inline_editor = editor
        self._inline_index = index
        editor.setFocus()
        editor.selectAll()

    def _apply_inline_text(self, index: int) -> None:
        if not self._inline_editor or index < 0 or index >= len(self.fields):
            return
        field = self.fields[index]
        if field.data_type == "图片":
            return
        field.value = self._inline_editor.toPlainText()
        self.fieldContentEdited.emit(index)

    def _close_inline_editor(self, emit_changed: bool = True) -> None:
        if not self._inline_proxy:
            self._inline_editor = None
            self._inline_index = None
            return
        proxy = self._inline_proxy
        self._inline_proxy = None
        self._inline_editor = None
        self._inline_index = None
        self.scene_obj.removeItem(proxy)
        proxy.deleteLater()
        if emit_changed:
            self.fieldChanged.emit()

    def _field_at(self, pos: QPoint) -> EditorFieldItem | None:
        for item in self.items(pos):
            if isinstance(item, EditorFieldItem):
                return item
        return None

    def _card_size(self) -> tuple[float, float]:
        return visual_node_size(self.fields, self.node_width, self.node_height)


class NodeEditorDialog(QDialog):
    def __init__(
        self,
        parent: QWidget | None,
        node: Node,
        theme: str = "dark",
        templates: list[NodeTemplate] | None = None,
        project_path: str | Path | None = None,
        force_template_lock: bool = False,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("编辑节点")
        self.setModal(True)
        self.theme = theme
        self.project_path = Path(project_path) if project_path else None
        self.result: Node | None = None
        self._node_id = node.id
        self._x = node.x
        self._y = node.y
        self._width = node.width
        self._height = node.height
        self._canvas_id = node.canvas_id
        self._link_path = node.link_path
        self._link_format = node.link_format
        self._order = node.order
        self.fields = [copy.deepcopy(field) for field in node.fields]
        self.title_field_id = node.title_field_id if any(field.id == node.title_field_id for field in self.fields) else ""
        self.templates = [copy.deepcopy(template) for template in templates] if templates is not None else None
        template_ids = {template.id for template in self.templates} if self.templates is not None else set()
        self._template_id = node.template_id if node.template_id in template_ids or not template_ids else ""
        self._force_template_lock = force_template_lock
        self.templates_result: list[NodeTemplate] | None = None
        self.templates_changed = False
        self.import_template_button: QToolButton | None = None
        self.import_template_menu: QMenu | None = None
        self.save_template_button: QToolButton | None = None
        self._ensure_visual_layout()
        self._updating = False

        self.title_edit = QLineEdit(node.title)
        self.node_type = QComboBox()
        self.node_type.addItems(NODE_TYPES)
        self.node_type.setCurrentText("超文本" if node.node_type == "超链接" else (node.node_type if node.node_type in NODE_TYPES else "普通"))
        if self._force_template_lock:
            self.node_type.setCurrentText("普通")
            self.node_type.setEnabled(False)
        self.icon_edit = QLineEdit(node.icon)
        self.color_edit = QLineEdit(node.color or DEFAULT_NODE_COLOR)
        self.icon_from_title_button = QToolButton()
        self.icon_from_title_button.setObjectName("bindingToolButton")
        self.icon_from_title_button.setText("首")
        self.icon_from_title_button.setCheckable(True)
        self.icon_from_title_button.setChecked(node.icon_from_title)
        self.icon_from_title_button.setToolTip("图标自动使用节点名称的第一个字")
        self.title_from_content_button = QToolButton()
        self.title_from_content_button.setObjectName("bindingToolButton")
        self.title_from_content_button.setText("名称")
        self.title_from_content_button.setCheckable(True)
        self.title_from_content_button.setToolTip("节点名称自动使用当前卡片内容")
        self.template_lock_button = QToolButton()
        self.template_lock_button.setObjectName("bindingToolButton")
        self.template_lock_button.setText("钉")
        self.template_lock_button.setCheckable(True)
        self.template_lock_button.setChecked(bool(node.template_locked or self._force_template_lock))
        self.template_lock_button.setFixedSize(34, 30)

        self.field_name = QLineEdit()
        self.field_label_button = QToolButton()
        self.field_label_button.setObjectName("bindingToolButton")
        self.field_label_button.setText("显")
        self.field_label_button.setCheckable(True)
        self.field_label_button.setFixedSize(34, 30)
        self.field_label_button.setToolTip("在节点卡片上显示字段名")
        self.field_type = QComboBox()
        self.field_type.addItems(FIELD_TYPES)
        self.field_value = SubmitPlainTextEdit()
        self.field_value.setFixedHeight(86)
        self.field_value.submitted.connect(self._accept)
        self.image_path = QLineEdit()
        self.image_button: QPushButton | None = None
        self.draw_image_button: QPushButton | None = None
        self.file_load_button: QPushButton | None = None
        self.image_fit = QComboBox()
        self.image_fit.addItem("自由拉伸", "stretch")
        self.image_fit.addItem("等比完整", "contain")
        self.image_fit.addItem("等比裁切", "cover")
        self.image_fit.addItem("九宫格", "nine_slice")
        self.slice_left = QLineEdit()
        self.slice_top = QLineEdit()
        self.slice_right = QLineEdit()
        self.slice_bottom = QLineEdit()
        self.field_x = QLineEdit()
        self.field_y = QLineEdit()
        self.field_w = QLineEdit()
        self.field_h = QLineEdit()
        self.font_size = QLineEdit()
        self.text_color = QLineEdit()
        self.bg_color = QLineEdit()
        self.h_align_group = QButtonGroup(self)
        self.h_align_group.setExclusive(True)
        self.v_align_group = QButtonGroup(self)
        self.v_align_group.setExclusive(True)
        self.h_align_buttons: dict[str, QToolButton] = {}
        self.v_align_buttons: dict[str, QToolButton] = {}
        self.pin_buttons: dict[str, QToolButton] = {}

        self.canvas = FieldCanvas(self.fields, theme, self.project_path, self._width, self._height)
        self._sync_title_from_field()
        self._sync_icon_from_title()
        self.canvas.set_header(self.title_edit.text(), self._display_icon(), self.color_edit.text())
        self.canvas.fieldSelected.connect(self._load_selected_props)
        self.canvas.fieldActivated.connect(self._focus_field_value)
        self.canvas.fieldContentEdited.connect(self._on_canvas_field_content_edited)
        self.canvas.fieldChanged.connect(self._on_field_changed)
        self.canvas.nodeSizeChanged.connect(self._on_canvas_node_size_changed)
        self.canvas.cardAddRequested.connect(self._add_card_at)
        self.canvas.cardDeleteRequested.connect(self._delete_card_at)

        splitter = QSplitter()
        splitter.addWidget(self.canvas)
        splitter.addWidget(self._side_panel())
        splitter.setSizes([780, 320])

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.button(QDialogButtonBox.Ok).setText("确定")
        buttons.button(QDialogButtonBox.Cancel).setText("取消")
        buttons.accepted.connect(self._accept)
        buttons.rejected.connect(self.reject)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 14, 14, 14)
        layout.addWidget(splitter, 1)
        layout.addWidget(buttons)
        self._restoring_window_layout = True
        self._layout_save_timer = QTimer(self)
        self._layout_save_timer.setSingleShot(True)
        self._layout_save_timer.timeout.connect(lambda: save_window_layout(self, "node_editor_dialog"))
        self.resize(1160, 760)
        restore_window_layout(self, "node_editor_dialog")
        QTimer.singleShot(0, lambda: setattr(self, "_restoring_window_layout", False))
        self._connect_changes()
        self._update_template_lock_controls()
        self._load_selected_props(self.canvas.selected_index if self.canvas.selected_index is not None else -1)

    def moveEvent(self, event) -> None:  # type: ignore[override]
        super().moveEvent(event)
        self._schedule_layout_save()

    def resizeEvent(self, event) -> None:  # type: ignore[override]
        super().resizeEvent(event)
        self._schedule_layout_save()

    def _schedule_layout_save(self) -> None:
        if getattr(self, "_restoring_window_layout", False) or not self.isVisible():
            return
        timer = getattr(self, "_layout_save_timer", None)
        if isinstance(timer, QTimer):
            timer.start(350)

    def _on_canvas_node_size_changed(self, width: float, height: float) -> None:
        self._width = max(0.0, float(width))
        self._height = max(0.0, float(height))

    def _side_panel(self) -> QWidget:
        panel = QWidget()
        panel.setMinimumWidth(356)
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(12, 0, 0, 0)
        layout.setSpacing(12)

        card_box = QGroupBox("节点卡牌")
        card_form = QFormLayout(card_box)
        card_form.addRow("名称", self.title_edit)
        card_form.addRow("类型", self.node_type)
        card_form.addRow("图标", self._icon_binding_row())
        card_form.addRow("颜色", self._color_row(self.color_edit, self._pick_node_color))

        template_tools = self._template_tools()

        props = QGroupBox("选中卡片属性")
        props_layout = QVBoxLayout(props)
        props_layout.setContentsMargins(12, 16, 12, 12)
        props_layout.setSpacing(8)
        self.content_label = QLabel("内容")
        self.image_label = QLabel("图片")
        self.image_row = self._image_path_row()
        self.image_fit_row = self._labeled_row("缩放", self.image_fit)
        self.image_slice_row = self._labeled_row("九宫", self._slice_row())
        self.file_row = self._path_row(self.field_value, "加载", self._load_field_file, button_attr="file_load_button")
        props_layout.addWidget(self._labeled_row("字段", self._field_name_row()))
        props_layout.addWidget(self._labeled_row("类型", self.field_type))
        self.content_row = self._labeled_row(self.content_label, self._content_binding_row())
        self.image_picker_row = self._labeled_row(self.image_label, self.image_row)
        props_layout.addWidget(self.content_row)
        props_layout.addWidget(self.image_picker_row)
        props_layout.addWidget(self.image_fit_row)
        props_layout.addWidget(self.image_slice_row)
        props_layout.addWidget(self._geometry_row())
        props_layout.addWidget(self._font_row())
        props_layout.addWidget(self._alignment_row())
        props_layout.addWidget(self._background_row())
        props_layout.addStretch(1)

        if template_tools:
            layout.addWidget(template_tools)
        layout.addWidget(card_box)
        layout.addWidget(props, 1)
        return panel

    def _template_tools(self) -> QWidget | None:
        if self.templates is None:
            return None
        row = QWidget()
        layout = QHBoxLayout(row)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)
        layout.addStretch(1)

        import_button = QToolButton(row)
        import_button.setObjectName("compactToolButton")
        import_button.setText("导入模板")
        import_button.setToolButtonStyle(Qt.ToolButtonTextOnly)
        import_button.setPopupMode(QToolButton.InstantPopup)
        import_menu = QMenu(import_button)
        self._populate_template_import_menu(import_menu)
        import_button.setMenu(import_menu)
        import_button.setEnabled(bool(self.templates))

        save_button = QToolButton(row)
        save_button.setObjectName("compactToolButton")
        save_button.setText("保存模板")
        save_button.setToolButtonStyle(Qt.ToolButtonTextOnly)
        save_button.clicked.connect(self._save_current_template)

        layout.addWidget(self.template_lock_button)
        layout.addWidget(import_button)
        layout.addWidget(save_button)
        self.import_template_button = import_button
        self.import_template_menu = import_menu
        self.save_template_button = save_button
        return row

    def _populate_template_import_menu(self, menu: QMenu) -> None:
        menu.clear()
        if not self.templates:
            empty = menu.addAction("暂无模板")
            empty.setEnabled(False)
            return
        for template in self.templates:
            action = menu.addAction(template.name)
            action.setData(template.id)
            action.triggered.connect(lambda _checked=False, template_id=template.id: self._import_template(template_id))

    def _update_template_lock_controls(self) -> None:
        if self.templates is None:
            return
        has_template = self._find_template(self._template_id) is not None
        has_templates = bool(self.templates)
        can_lock = self._force_template_lock or self.node_type.currentText() == "普通"
        self.template_lock_button.blockSignals(True)
        if self._force_template_lock:
            self.template_lock_button.setChecked(True)
            self.template_lock_button.setEnabled(False)
            self.template_lock_button.setToolTip("排序画布节点始终跟随当前画布模板")
        else:
            self.template_lock_button.setEnabled(can_lock and has_templates)
            if not can_lock:
                self.template_lock_button.setChecked(False)
                self.template_lock_button.setToolTip("只有普通节点支持模板锁定")
            elif not has_templates:
                self.template_lock_button.setChecked(False)
                self.template_lock_button.setToolTip("先保存一个模板，再选择锁定")
            elif self.template_lock_button.isChecked() and has_template:
                self.template_lock_button.setToolTip("已锁定到模板，取消后恢复独立编辑")
            else:
                self.template_lock_button.setToolTip("点击后选择模板并锁定")
        self.template_lock_button.blockSignals(False)
        bound_template = self._uses_bound_template()
        if self.import_template_button is not None:
            self.import_template_button.setHidden(bound_template)
            self.import_template_button.setEnabled(bool(self.templates) and not bound_template)
        if self.save_template_button is not None:
            self.save_template_button.setHidden(bound_template)

    def _find_template(self, template_id: str) -> NodeTemplate | None:
        if self.templates is None:
            return None
        return next((template for template in self.templates if template.id == template_id), None)

    def _uses_bound_template(self) -> bool:
        if self.templates is None:
            return False
        if self._force_template_lock:
            return True
        return bool(
            self.node_type.currentText() == "普通"
            and self.template_lock_button.isChecked()
            and self._find_template(self._template_id) is not None
        )

    def _set_template_lock_checked(self, checked: bool) -> None:
        self.template_lock_button.blockSignals(True)
        self.template_lock_button.setChecked(checked)
        self.template_lock_button.blockSignals(False)

    def _select_template_for_lock(self) -> str | None:
        if not self.templates:
            QMessageBox.information(self, "暂无模板", "请先保存一个模板，再选择锁定。")
            return None
        names = [template.name for template in self.templates]
        current_index = next(
            (index for index, template in enumerate(self.templates) if template.id == self._template_id),
            0,
        )
        name, ok = QInputDialog.getItem(self, "选择模板", "锁定到模板", names, current_index, False)
        if not ok or not name:
            return None
        template = next((item for item in self.templates if item.name == name), None)
        return template.id if template is not None else None

    def _apply_template_to_editor(self, template: NodeTemplate, *, preserve_values: bool) -> None:
        if preserve_values:
            working = Node(
                id=self._node_id,
                title=self.title_edit.text().strip() or template.name,
                node_type="普通",
                x=self._x,
                y=self._y,
                width=self._width,
                height=self._height,
                color=self.color_edit.text().strip() or DEFAULT_NODE_COLOR,
                icon=self.icon_edit.text().strip(),
                icon_from_title=self.icon_from_title_button.isChecked(),
                title_field_id=self.title_field_id,
                template_id=self._template_id,
                fields=[NodeField.from_dict(field.to_dict()) for field in self.fields],
            )
            apply_template_to_node(working, template, preserve_values=True)
            title = working.title
            icon_from_title = working.icon_from_title
            icon = working.icon
            color = working.color
            fields = [NodeField.from_dict(field.to_dict()) for field in working.fields]
            title_field_id = working.title_field_id
        else:
            title = template.name
            icon_from_title = self.icon_from_title_button.isChecked()
            icon = self.icon_edit.text().strip()
            color = template.color or DEFAULT_NODE_COLOR
            fields = [NodeField.from_dict(field.to_dict()) for field in template.fields]
            title_field_id = template.title_field_id if any(field.id == template.title_field_id for field in fields) else ""

        self._template_id = template.id
        self.title_edit.setText(title)
        self.icon_from_title_button.setChecked(icon_from_title)
        self.icon_edit.setText(icon)
        self.color_edit.setText(color)
        self.fields = fields
        self.title_field_id = title_field_id
        self._sync_title_from_field()
        self._sync_icon_from_title()
        self._ensure_visual_layout()
        self.canvas.fields = self.fields
        self.canvas.refresh(0 if self.fields else None)
        self._update_template_lock_controls()
        self._load_selected_props(self.canvas.selected_index if self.canvas.selected_index is not None else -1)

    def _on_template_lock_toggled(self, checked: bool) -> None:
        if self.templates is None:
            return
        if self._force_template_lock:
            self._update_template_lock_controls()
            return
        if not checked:
            self._update_template_lock_controls()
            return
        template_id = self._select_template_for_lock()
        if not template_id:
            self._set_template_lock_checked(False)
            self._update_template_lock_controls()
            return
        template = self._find_template(template_id)
        if template is None:
            self._set_template_lock_checked(False)
            self._update_template_lock_controls()
            return
        self._apply_template_to_editor(template, preserve_values=True)
        self._set_template_lock_checked(True)
        self._update_template_lock_controls()

    def _labeled_row(self, label: str | QLabel, editor: QWidget) -> QWidget:
        row = QWidget()
        layout = QHBoxLayout(row)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)
        label_widget = label if isinstance(label, QLabel) else QLabel(label)
        label_widget.setFixedWidth(38)
        layout.addWidget(label_widget)
        layout.addWidget(editor, 1)
        return row

    def _icon_binding_row(self) -> QWidget:
        row = QWidget()
        layout = QHBoxLayout(row)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)
        self.icon_from_title_button.setFixedSize(38, 30)
        layout.addWidget(self.icon_edit, 1)
        layout.addWidget(self.icon_from_title_button)
        return row

    def _field_name_row(self) -> QWidget:
        row = QWidget()
        layout = QHBoxLayout(row)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)
        layout.addWidget(self.field_name, 1)
        layout.addWidget(self.field_label_button)
        return row

    def _content_binding_row(self) -> QWidget:
        row = QWidget()
        layout = QHBoxLayout(row)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)
        self.title_from_content_button.setFixedSize(48, 30)
        content_stack = QWidget()
        content_stack_layout = QVBoxLayout(content_stack)
        content_stack_layout.setContentsMargins(0, 0, 0, 0)
        content_stack_layout.setSpacing(6)
        content_stack_layout.addWidget(self.field_value, 1)
        content_stack_layout.addWidget(self.file_row)
        layout.addWidget(content_stack, 1)
        layout.addWidget(self.title_from_content_button, 0, Qt.AlignTop)
        return row

    def _path_row(self, edit: QWidget, label: str, slot, button_attr: str = "image_button") -> QWidget:
        row = QWidget()
        layout = QHBoxLayout(row)
        layout.setContentsMargins(0, 0, 0, 0)
        button = QPushButton(label)
        setattr(self, button_attr, button)
        button.clicked.connect(slot)
        layout.addWidget(edit, 1)
        layout.addWidget(button)
        return row

    def _image_path_row(self) -> QWidget:
        row = QWidget()
        layout = QHBoxLayout(row)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)
        self.image_button = QPushButton("选择图片")
        self.image_button.clicked.connect(self._pick_image)
        self.draw_image_button = QPushButton("绘制")
        self.draw_image_button.clicked.connect(self._paint_image)
        layout.addWidget(self.image_path, 1)
        layout.addWidget(self.image_button)
        layout.addWidget(self.draw_image_button)
        return row

    def _slice_row(self) -> QWidget:
        row = QWidget()
        layout = QHBoxLayout(row)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(5)
        for label, editor in (
            ("左", self.slice_left),
            ("上", self.slice_top),
            ("右", self.slice_right),
            ("下", self.slice_bottom),
        ):
            label_widget = QLabel(label)
            label_widget.setFixedWidth(16)
            editor.setAlignment(Qt.AlignCenter)
            editor.setFixedWidth(42)
            layout.addWidget(label_widget)
            layout.addWidget(editor)
        layout.addStretch(1)
        return row

    def _geometry_row(self) -> QWidget:
        row = QWidget()
        layout = QHBoxLayout(row)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(5)
        for prop, label, editor in (
            ("x", "X", self.field_x),
            ("y", "Y", self.field_y),
            ("width", "宽", self.field_w),
            ("height", "高", self.field_h),
        ):
            layout.addWidget(self._compact_pin_input(prop, label, editor, 42))
        return row

    def _font_row(self) -> QWidget:
        row = QWidget()
        layout = QHBoxLayout(row)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)
        layout.addWidget(self._compact_pin_input("font_size", "字号", self.font_size, 44))
        layout.addWidget(self._compact_color_input("text_color", "字色", self.text_color, self._pick_text_color), 1)
        return row

    def _alignment_row(self) -> QWidget:
        row = QWidget()
        layout = QVBoxLayout(row)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)
        layout.addWidget(
            self._alignment_button_row(
                "横向",
                self.h_align_group,
                self.h_align_buttons,
                (
                    ("left", "左对齐"),
                    ("center", "水平居中"),
                    ("right", "右对齐"),
                ),
                horizontal=True,
            )
        )
        layout.addWidget(
            self._alignment_button_row(
                "纵向",
                self.v_align_group,
                self.v_align_buttons,
                (
                    ("top", "顶端对齐"),
                    ("center", "垂直居中"),
                    ("bottom", "底端对齐"),
                ),
                horizontal=False,
            )
        )
        return row

    def _background_row(self) -> QWidget:
        return self._compact_color_input("bg_color", "背景色", self.bg_color, self._pick_bg_color)

    def _compact_pin_input(self, prop: str, label: str, editor: QLineEdit, width: int) -> QWidget:
        row = QWidget()
        layout = QHBoxLayout(row)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(2)
        name = QLabel(label)
        name.setFixedWidth(16 if len(label) == 1 else 24)
        editor.setAlignment(Qt.AlignCenter)
        editor.setFixedWidth(width)
        editor.setMinimumWidth(width)
        layout.addWidget(name)
        layout.addWidget(editor)
        layout.addWidget(self._pin_button(prop, 20))
        return row

    def _compact_color_input(self, prop: str, label: str, edit: QLineEdit, slot) -> QWidget:
        row = QWidget()
        layout = QHBoxLayout(row)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)
        name = QLabel(label)
        name.setFixedWidth(42)
        edit.setMinimumWidth(0)
        button = QToolButton(row)
        button.setObjectName("colorPickButton")
        button.setText("…")
        button.setToolTip(f"选择{label}")
        button.setFixedSize(28, 28)
        button.clicked.connect(slot)
        layout.addWidget(name)
        layout.addWidget(edit, 1)
        layout.addWidget(button)
        layout.addWidget(self._pin_button(prop, 20))
        return row

    def _alignment_button_row(
        self,
        label: str,
        group: QButtonGroup,
        buttons: dict[str, QToolButton],
        options: tuple[tuple[str, str], ...],
        *,
        horizontal: bool,
    ) -> QWidget:
        row = QWidget()
        layout = QHBoxLayout(row)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(5)
        label_widget = QLabel(label)
        label_widget.setFixedWidth(42)
        layout.addWidget(label_widget)
        for value, tooltip in options:
            button = QToolButton(row)
            button.setObjectName("alignToolButton")
            button.setCheckable(True)
            button.setToolTip(tooltip)
            button.setIcon(self._alignment_icon(value, horizontal))
            button.setIconSize(QSize(18, 18))
            button.setFixedSize(30, 30)
            button.toggled.connect(lambda checked, button=button: self._on_alignment_toggled(button, checked))
            group.addButton(button)
            buttons[value] = button
            layout.addWidget(button)
        layout.addStretch(1)
        return row

    def _alignment_icon(self, value: str, horizontal: bool) -> QIcon:
        colors = palette(self.theme)
        pixmap = QPixmap(22, 22)
        pixmap.fill(Qt.transparent)
        painter = QPainter(pixmap)
        painter.setRenderHint(QPainter.Antialiasing)
        color = QColor(colors["text"])
        muted = QColor(colors["text_muted"])
        muted.setAlpha(95)
        painter.setPen(QPen(muted, 1))
        painter.drawRoundedRect(QRectF(3.5, 3.5, 15, 15), 3, 3)
        painter.setPen(Qt.NoPen)
        painter.setBrush(color)
        if horizontal:
            widths = (9, 13, 11)
            y_positions = (6, 10, 14)
            for width, y in zip(widths, y_positions):
                if value == "center":
                    x = (22 - width) / 2
                elif value == "right":
                    x = 17 - width
                else:
                    x = 5
                painter.drawRoundedRect(QRectF(x, y, width, 2), 1, 1)
        else:
            top = {"top": 5, "center": 8, "bottom": 11}.get(value, 5)
            for offset, width in ((0, 12), (4, 9), (8, 12)):
                painter.drawRoundedRect(QRectF((22 - width) / 2, top + offset, width, 2), 1, 1)
        painter.end()
        return QIcon(pixmap)

    def _pin_button(self, prop: str, size: int = 26) -> QToolButton:
        pin = QToolButton()
        pin.setObjectName("exportPinButton")
        pin.setText("📌")
        pin.setCheckable(True)
        pin.setToolTip("导出所有画布 CSV 时包含此属性")
        pin.setFixedSize(size, size)
        pin.toggled.connect(lambda checked, prop=prop: self._set_export_prop(prop, checked))
        self.pin_buttons[prop] = pin
        return pin

    def _pin_row(self, prop: str, editor: QWidget) -> QWidget:
        row = QWidget()
        layout = QHBoxLayout(row)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)
        layout.addWidget(editor, 1)
        layout.addWidget(self._pin_button(prop))
        return row

    def _color_row(self, edit: QLineEdit, slot) -> QWidget:
        row = QWidget()
        layout = QHBoxLayout(row)
        layout.setContentsMargins(0, 0, 0, 0)
        button = QPushButton("选择")
        button.clicked.connect(slot)
        layout.addWidget(edit, 1)
        layout.addWidget(button)
        return row

    def _connect_changes(self) -> None:
        self.title_edit.textChanged.connect(lambda _text: self._on_title_changed())
        self.icon_edit.textChanged.connect(lambda _text: self._update_canvas_header())
        self.color_edit.textChanged.connect(lambda _text: self._update_canvas_header())
        self.icon_from_title_button.toggled.connect(self._on_icon_from_title_toggled)
        self.title_from_content_button.toggled.connect(self._on_title_from_content_toggled)
        for widget in (
            self.field_name,
            self.image_path,
            self.slice_left,
            self.slice_top,
            self.slice_right,
            self.slice_bottom,
            self.field_x,
            self.field_y,
            self.field_w,
            self.field_h,
            self.font_size,
            self.text_color,
            self.bg_color,
        ):
            widget.textChanged.connect(self._apply_props)
        self.field_type.currentTextChanged.connect(self._on_field_type_changed)
        self.image_fit.currentIndexChanged.connect(lambda _index: self._on_image_fit_changed())
        self.field_label_button.toggled.connect(lambda _checked=False: self._apply_props())
        self.node_type.currentTextChanged.connect(lambda _text: self._update_template_lock_controls())
        self.field_value.textChanged.connect(self._apply_props)
        self.template_lock_button.toggled.connect(self._on_template_lock_toggled)

    def _ensure_visual_layout(self) -> None:
        y = 18.0
        for field in self.fields:
            if not field.has_visual_layout():
                field.x = 20.0
                field.y = y
                field.width = 340.0
                field.height = 78.0
                field.font_size = 13
                field.bg_color = "#FFFFFF"
                field.text_color = "#1D1D1F"
            y = max(y + 92, field.y + field.height + 12)

    def _update_canvas_header(self) -> None:
        self.canvas.set_header(self.title_edit.text(), self._display_icon(), self.color_edit.text())

    def _on_title_changed(self) -> None:
        self._sync_icon_from_title()
        self._update_canvas_header()

    def _display_icon(self) -> str:
        if self.icon_from_title_button.isChecked():
            return self.title_edit.text().strip()[:1] or self.icon_edit.text().strip()
        return self.icon_edit.text().strip()

    def _sync_icon_from_title(self) -> None:
        checked = self.icon_from_title_button.isChecked()
        self.icon_edit.setEnabled(not checked)
        if not checked:
            return
        icon = self.title_edit.text().strip()[:1]
        self.icon_edit.blockSignals(True)
        self.icon_edit.setText(icon)
        self.icon_edit.blockSignals(False)

    def _title_source_field(self) -> NodeField | None:
        if not self.title_field_id:
            return None
        return next((field for field in self.fields if field.id == self.title_field_id), None)

    def _sync_title_from_field(self) -> None:
        field = self._title_source_field()
        if not field or field.data_type in {"图片", "资源路径"}:
            return
        title = (field.value or field.name).strip()
        if not title:
            return
        self.title_edit.setText(title)

    def _update_title_binding_controls(self) -> None:
        field = self._selected_field()
        can_bind = bool(field and field.data_type not in {"图片", "资源路径"})
        checked = bool(field and field.id == self.title_field_id)
        self.title_from_content_button.blockSignals(True)
        self.title_from_content_button.setChecked(checked)
        self.title_from_content_button.setEnabled(can_bind)
        self.title_from_content_button.blockSignals(False)
        self.title_edit.setReadOnly(bool(self._title_source_field()))

    def _on_icon_from_title_toggled(self, _checked: bool) -> None:
        self._sync_icon_from_title()
        self._update_canvas_header()

    def _on_title_from_content_toggled(self, checked: bool) -> None:
        if self._updating:
            return
        field = self._selected_field()
        if checked and field and field.data_type not in {"图片", "资源路径"}:
            self.title_field_id = field.id
            self._sync_title_from_field()
        elif field and field.id == self.title_field_id:
            self.title_field_id = ""
        self._update_title_binding_controls()
        self._update_canvas_header()

    def _selected_field(self) -> NodeField | None:
        index = self.canvas.selected_index
        if index is None or index < 0 or index >= len(self.fields):
            return None
        return self.fields[index]

    def _set_alignment_value(self, buttons: dict[str, QToolButton], value: str, fallback: str) -> None:
        target = buttons.get(value) or buttons.get(fallback)
        if not target:
            return
        for button in buttons.values():
            button.blockSignals(True)
        target.setChecked(True)
        for button in buttons.values():
            button.blockSignals(False)

    def _alignment_value(self, buttons: dict[str, QToolButton], fallback: str) -> str:
        for value, button in buttons.items():
            if button.isChecked():
                return value
        return fallback

    def _set_alignment_enabled(self, enabled: bool) -> None:
        for button in (*self.h_align_buttons.values(), *self.v_align_buttons.values()):
            button.setEnabled(enabled)

    def _on_alignment_toggled(self, _button: QToolButton, checked: bool) -> None:
        if checked:
            self._apply_props()

    def _load_selected_props(self, index: int) -> None:
        self._updating = True
        self.canvas.selected_index = index if 0 <= index < len(self.fields) else None
        field = self._selected_field()
        if not field:
            for widget in (
                self.field_name,
                self.image_path,
                self.slice_left,
                self.slice_top,
                self.slice_right,
                self.slice_bottom,
                self.field_x,
                self.field_y,
                self.field_w,
                self.field_h,
                self.font_size,
                self.text_color,
                self.bg_color,
            ):
                widget.clear()
            self.field_value.setPlainText("")
            self.field_label_button.blockSignals(True)
            self.field_label_button.setChecked(False)
            self.field_label_button.blockSignals(False)
            self.image_fit.setCurrentIndex(max(0, self.image_fit.findData("stretch")))
            self._set_alignment_value(self.h_align_buttons, "left", "left")
            self._set_alignment_value(self.v_align_buttons, "top", "top")
            self._updating = False
            self._update_type_controls()
            self._update_pin_controls()
            self._update_title_binding_controls()
            return
        self.field_name.setText(field.name)
        self.field_label_button.blockSignals(True)
        self.field_label_button.setChecked(bool(field.show_label))
        self.field_label_button.blockSignals(False)
        self.field_type.setCurrentText(field.data_type if field.data_type in FIELD_TYPES else "文本")
        self.field_value.setPlainText(field.value)
        self.image_path.setText(field.image_path)
        self.image_fit.setCurrentIndex(max(0, self.image_fit.findData(field.image_fit if field.image_fit in IMAGE_FIT_MODES else "stretch")))
        self.slice_left.setText(str(max(0, int(field.slice_left))))
        self.slice_top.setText(str(max(0, int(field.slice_top))))
        self.slice_right.setText(str(max(0, int(field.slice_right))))
        self.slice_bottom.setText(str(max(0, int(field.slice_bottom))))
        self.field_x.setText(f"{field.x:.0f}")
        self.field_y.setText(f"{field.y:.0f}")
        self.field_w.setText(f"{field.width:.0f}")
        self.field_h.setText(f"{field.height:.0f}")
        self.font_size.setText(str(field.font_size))
        self.text_color.setText(field.text_color)
        self.bg_color.setText(field.bg_color)
        self._set_alignment_value(self.h_align_buttons, field.text_h_align, "left")
        self._set_alignment_value(self.v_align_buttons, field.text_v_align, "top")
        self._updating = False
        self._update_type_controls()
        self._update_pin_controls()
        self._update_title_binding_controls()

    def _on_field_type_changed(self, _text: str) -> None:
        is_image = self.field_type.currentText() == "图片"
        if is_image and self.field_value.toPlainText():
            self.field_value.blockSignals(True)
            self.field_value.setPlainText("")
            self.field_value.blockSignals(False)
        if not is_image and self.image_path.text():
            self.image_path.blockSignals(True)
            self.image_path.clear()
            self.image_path.blockSignals(False)
        self._apply_props()
        field = self._selected_field()
        if field:
            if is_image and field.id == self.title_field_id:
                self.title_field_id = ""
            self._normalize_export_props_for_type(field)
        self._update_type_controls()
        self._update_pin_controls()

    def _on_image_fit_changed(self) -> None:
        self._apply_props()
        self._update_type_controls()

    def _update_type_controls(self) -> None:
        has_field = self._selected_field() is not None
        is_image = has_field and self.field_type.currentText() == "图片"
        is_resource = has_field and self.field_type.currentText() == "资源路径"
        show_content = has_field and not is_image
        show_image = has_field and is_image
        self.content_row.setVisible(show_content)
        self.field_value.setVisible(show_content)
        self.image_picker_row.setVisible(show_image)
        self.image_fit_row.setVisible(show_image)
        self.image_slice_row.setVisible(show_image and self.image_fit.currentData() == "nine_slice")
        self.field_value.setEnabled(show_content)
        self.field_label_button.setEnabled(show_content)
        self.image_path.setEnabled(show_image)
        self.image_fit.setEnabled(show_image)
        self._set_alignment_enabled(has_field)
        if self.image_button:
            self.image_button.setEnabled(show_image)
        if self.draw_image_button:
            self.draw_image_button.setEnabled(show_image)
        if self.file_load_button:
            self.file_load_button.setVisible(show_content)
            self.file_load_button.setEnabled(show_content and self.field_type.currentText() in {"文本", "长文本", "资源路径"})
            self.file_load_button.setText("加载")
        self.content_label.setText("资源内容" if is_resource else "内容")
        self._update_title_binding_controls()

    def _update_pin_controls(self) -> None:
        field = self._selected_field()
        for prop, button in self.pin_buttons.items():
            button.blockSignals(True)
            button.setChecked(bool(field and prop in field.export_props))
            button.setEnabled(bool(field))
            button.blockSignals(False)
        self._update_type_controls()

    def _set_export_prop(self, prop: str, checked: bool) -> None:
        if self._updating or prop not in FIELD_EXPORT_PROPS:
            return
        field = self._selected_field()
        if not field:
            return
        props = [item for item in field.export_props if item in FIELD_EXPORT_PROPS]
        if checked and prop not in props:
            props.append(prop)
        elif not checked:
            props = [item for item in props if item != prop]
        field.export_props = props
        self.canvas.refresh(self.canvas.selected_index)

    def _normalize_export_props_for_type(self, field: NodeField) -> None:
        field.export_props = [
            prop for prop in field.export_props
            if prop in FIELD_EXPORT_PROPS
        ]

    def _apply_props(self) -> None:
        if self._updating:
            return
        field = self._selected_field()
        if not field:
            return
        field.name = self.field_name.text().strip() or "字段"
        field.data_type = self.field_type.currentText() if self.field_type.currentText() in FIELD_TYPES else "文本"
        field.show_label = bool(self.field_label_button.isChecked() and field.data_type != "图片")
        field.value = "" if field.data_type == "图片" else self.field_value.toPlainText().strip()
        field.image_path = self.image_path.text().strip() if field.data_type == "图片" else ""
        field.image_fit = str(self.image_fit.currentData() or "stretch") if field.data_type == "图片" else "stretch"
        field.slice_left = max(0, int(self._float_text(self.slice_left, field.slice_left)))
        field.slice_top = max(0, int(self._float_text(self.slice_top, field.slice_top)))
        field.slice_right = max(0, int(self._float_text(self.slice_right, field.slice_right)))
        field.slice_bottom = max(0, int(self._float_text(self.slice_bottom, field.slice_bottom)))
        field.x = self._float_text(self.field_x, field.x)
        field.y = self._float_text(self.field_y, field.y)
        field.width = max(44.0, self._float_text(self.field_w, field.width))
        field.height = max(34.0, self._float_text(self.field_h, field.height))
        field.font_size = max(8, min(48, int(self._float_text(self.font_size, field.font_size))))
        field.text_color = self.text_color.text().strip() or "#1D1D1F"
        field.bg_color = self.bg_color.text().strip() or "#FFFFFF"
        field.text_h_align = self._alignment_value(self.h_align_buttons, "left")
        field.text_v_align = self._alignment_value(self.v_align_buttons, "top")
        self._normalize_export_props_for_type(field)
        if field.id == self.title_field_id:
            self._sync_title_from_field()
        self.canvas.refresh(self.canvas.selected_index)

    def _on_field_changed(self) -> None:
        if self.canvas.selected_index is not None:
            self._load_selected_props(self.canvas.selected_index)
        selected_index = self.canvas.selected_index
        QTimer.singleShot(0, lambda: self.canvas.refresh(selected_index))

    def _on_canvas_field_content_edited(self, index: int) -> None:
        if self._updating or index != self.canvas.selected_index:
            return
        field = self._selected_field()
        if not field or field.data_type == "图片":
            return
        self.field_value.blockSignals(True)
        self.field_value.setPlainText(field.value)
        self.field_value.blockSignals(False)
        if field.id == self.title_field_id:
            self._sync_title_from_field()

    def _focus_field_value(self, index: int) -> None:
        self._load_selected_props(index)
        field = self._selected_field()
        if field and field.data_type == "图片":
            self.image_path.setFocus()
            return
        self.field_value.setFocus()
        self.field_value.selectAll()

    def _add_card_at(self, kind: str, x: float, y: float) -> None:
        if kind == "image":
            self._add_image_card(QPointF(x, y))
            return
        self._add_text_card(QPointF(x, y))

    def _next_card_position(self) -> QPointF:
        return QPointF(24.0, max([item.y + item.height + 12 for item in self.fields] + [18]))

    def _add_text_card(self, position: QPointF | None = None) -> None:
        if not isinstance(position, QPointF):
            position = self._next_card_position()
        field = NodeField(name="文字", data_type="长文本", value="双击右侧内容编辑文字")
        field.x = max(0.0, position.x())
        field.y = max(0.0, position.y())
        field.width = 320
        field.height = 92
        field.font_size = 14
        field.text_color = "#1D1D1F"
        field.bg_color = "#FFFFFF"
        self.fields.append(field)
        self.canvas.refresh(len(self.fields) - 1)
        self._load_selected_props(len(self.fields) - 1)

    def _add_image_card(self, position: QPointF | None = None, pick_image: bool = True) -> None:
        if not isinstance(position, QPointF):
            position = self._next_card_position()
        field = NodeField(name="图片", data_type="图片", value="")
        field.x = max(0.0, position.x())
        field.y = max(0.0, position.y())
        field.width = 280
        field.height = 170
        field.bg_color = "#F6F6F8"
        self.fields.append(field)
        self.canvas.refresh(len(self.fields) - 1)
        self._load_selected_props(len(self.fields) - 1)
        if pick_image:
            self._pick_image()

    def _delete_card_at(self, index: int) -> None:
        if index < 0 or index >= len(self.fields):
            return
        if self.fields[index].id == self.title_field_id:
            self.title_field_id = ""
        del self.fields[index]
        next_index = min(index, len(self.fields) - 1) if self.fields else None
        self.canvas.refresh(next_index)
        self._load_selected_props(next_index if next_index is not None else -1)

    def _delete_selected_card(self) -> None:
        index = self.canvas.selected_index
        if index is None or index < 0 or index >= len(self.fields):
            return
        self._delete_card_at(index)

    def _import_template(self, template_id: str) -> None:
        if self.templates is None:
            return
        template = self._find_template(template_id)
        if not template:
            return
        self._apply_template_to_editor(template, preserve_values=False)

    def _save_current_template(self) -> None:
        if self.templates is None:
            return
        self._apply_props()
        if self._force_template_lock:
            QMessageBox.information(self, "排序画布模板", "排序画布使用当前画布自己的模板，不支持在这里导出为通用模板。")
            return
        default_name = self.title_edit.text().strip() or "节点模板"
        name, ok = QInputDialog.getText(self, "保存模板", "模板名称", text=default_name)
        if not ok:
            return
        name = name.strip()
        if not name:
            QMessageBox.warning(self, "模板名称不能为空", "请输入模板名称。")
            return

        existing_index = next((index for index, item in enumerate(self.templates) if item.name == name), None)
        existing_template = self.templates[existing_index] if existing_index is not None else None
        template = NodeTemplate(
            id=new_id("template"),
            name=name,
            color=self.color_edit.text().strip() or DEFAULT_NODE_COLOR,
            icon=existing_template.icon if existing_template is not None else "",
            icon_from_title=existing_template.icon_from_title if existing_template is not None else False,
            title_field_id=self.title_field_id,
            fields=[NodeField.from_dict(field.to_dict()) for field in self.fields],
        )
        if existing_index is not None:
            answer = QMessageBox.question(self, "覆盖模板", f"模板“{name}”已存在，是否覆盖？")
            if answer != QMessageBox.Yes:
                return
            template.id = self.templates[existing_index].id
            self.templates[existing_index] = template
        else:
            self.templates.append(template)
        self._template_id = template.id
        self.templates_changed = True
        self.templates_result = [copy.deepcopy(item) for item in self.templates]
        if hasattr(self, "import_template_menu"):
            self._populate_template_import_menu(self.import_template_menu)
        if hasattr(self, "import_template_button"):
            self.import_template_button.setEnabled(bool(self.templates))
        self._update_template_lock_controls()

    def _pick_node_color(self) -> None:
        self._pick_color(self.color_edit)

    def _pick_text_color(self) -> None:
        self._pick_color(self.text_color)

    def _pick_bg_color(self) -> None:
        self._pick_color(self.bg_color)

    def _pick_color(self, edit: QLineEdit) -> None:
        color = QColorDialog.getColor(QColor(edit.text() or "#FFFFFF"), self, "选择颜色")
        if color.isValid():
            edit.setText(color.name())

    def _pick_image(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self,
            "选择图片",
            self.image_path.text() or str(Path.home()),
            "图片 (*.png *.jpg *.jpeg *.bmp *.gif);;所有文件 (*.*)",
        )
        if path:
            self.image_path.setText(path)

    def _paint_image(self) -> None:
        field = self._selected_field()
        if not field or self.field_type.currentText() != "图片":
            return
        self._apply_props()
        current_path = self.image_path.text().strip()
        initial_path = current_path if current_path and Path(current_path).exists() else None
        output_path = Path(current_path) if current_path else self._next_drawn_image_path(field)
        dialog = ImagePaintDialog(self, initial_path=initial_path, output_path=output_path)
        if dialog.exec() != ImagePaintDialog.Accepted or not dialog.result_path:
            return
        self.image_path.setText(dialog.result_path)
        self._apply_props()

    def _next_drawn_image_path(self, field: NodeField) -> Path | None:
        if not self.project_path:
            return None
        folder = project_bundle_dir(self.project_path) / "images"
        folder.mkdir(parents=True, exist_ok=True)
        base = _safe_asset_name(field.name or self.title_edit.text() or "绘制图片")
        path = folder / f"{base}.png"
        index = 2
        while path.exists():
            path = folder / f"{base}_{index}.png"
            index += 1
        return path

    def _load_field_file(self) -> None:
        field = self._selected_field()
        if not field:
            return
        field_type = self.field_type.currentText()
        filters = "文档 (*.md *.txt);;所有文件 (*.*)" if field_type == "资源路径" else "文本文件 (*.txt *.md);;所有文件 (*.*)"
        path, _ = QFileDialog.getOpenFileName(
            self,
            "加载文件",
            str(self.project_path.parent) if self.project_path else str(Path.home()),
            filters,
        )
        if not path:
            return
        source = Path(path)
        try:
            if field_type == "资源路径":
                if not self.project_path:
                    raise ValueError("当前节点还没有工程路径，无法导入超文本文件。")
                relative = import_link_document(self.project_path, source, self.title_edit.text().strip() or source.stem)
                content = read_link_document(self.project_path, relative)
                self.field_value.setPlainText(relative)
                field.value = relative
                if self.node_type.currentText() == "超文本":
                    self._link_path = relative
                    self._link_format = Path(relative).suffix.lstrip(".").lower() or "md"
            else:
                content = source.read_text(encoding="utf-8")
                self.field_value.setPlainText(content)
                field.value = content
        except (OSError, ValueError) as exc:
            QMessageBox.warning(self, "加载失败", f"无法读取文件：\n{exc}")
            return

        self.canvas.refresh(self.canvas.selected_index)
        if field.id == self.title_field_id:
            self._sync_title_from_field()

    def _float_text(self, edit: QLineEdit, fallback: float) -> float:
        try:
            return float(edit.text())
        except ValueError:
            return fallback

    def _accept(self) -> None:
        title = self.title_edit.text().strip()
        if not title:
            QMessageBox.warning(self, "节点名称不能为空", "请输入节点名称。")
            return
        self._apply_props()
        node_type = self.node_type.currentText() if self.node_type.currentText() in NODE_TYPES else "普通"
        template_locked = bool(
            node_type == "普通" and (self._force_template_lock or self.template_lock_button.isChecked())
        )
        self.result = Node(
            id=self._node_id,
            title=title,
            node_type=node_type,
            canvas_id=self._canvas_id if node_type == "画布" else "",
            link_path=self._link_path if node_type == "超文本" else "",
            link_format=self._link_format if self._link_format in {"md", "txt"} else "md",
            order=self._order,
            x=self._x,
            y=self._y,
            width=self._width,
            height=self._height,
            color=self.color_edit.text().strip() or DEFAULT_NODE_COLOR,
            icon=self.icon_edit.text().strip(),
            icon_from_title=self.icon_from_title_button.isChecked(),
            title_field_id=self.title_field_id,
            template_id=self._template_id if node_type == "普通" else "",
            template_locked=template_locked,
            fields=[copy.deepcopy(field) for field in self.fields],
        )
        self.accept()

    def done(self, result: int) -> None:  # type: ignore[override]
        save_window_layout(self, "node_editor_dialog")
        super().done(result)


class TemplateManagerDialog(QDialog):
    def __init__(
        self,
        parent: QWidget | None,
        templates: list[NodeTemplate],
        theme: str = "dark",
        project_path: str | Path | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("节点模板")
        self.setModal(True)
        self.project_path = Path(project_path) if project_path else None
        self.theme = theme
        self.templates = [copy.deepcopy(template) for template in templates]
        self.result: list[NodeTemplate] | None = None

        self.list_widget = QListWidget()
        self.list_widget.itemDoubleClicked.connect(lambda _item: self._edit_template())

        add_button = QPushButton("新增")
        add_button.clicked.connect(self._add_template)
        edit_button = QPushButton("编辑")
        edit_button.clicked.connect(self._edit_template)
        copy_button = QPushButton("复制")
        copy_button.clicked.connect(self._copy_template)
        delete_button = QPushButton("删除")
        delete_button.clicked.connect(self._delete_template)

        tools = QVBoxLayout()
        for button in (add_button, edit_button, copy_button, delete_button):
            tools.addWidget(button)
        tools.addStretch(1)

        body = QHBoxLayout()
        body.addWidget(self.list_widget, 1)
        body.addLayout(tools)

        buttons = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        buttons.button(QDialogButtonBox.Save).setText("保存模板")
        buttons.button(QDialogButtonBox.Cancel).setText("取消")
        buttons.accepted.connect(self._accept)
        buttons.rejected.connect(self.reject)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(18, 18, 18, 16)
        layout.addLayout(body, 1)
        layout.addWidget(buttons)
        self.resize(560, 440)
        restore_window_layout(self, "template_manager_dialog")
        self._refresh()

    def _selected_index(self) -> int | None:
        row = self.list_widget.currentRow()
        return row if 0 <= row < len(self.templates) else None

    def _refresh(self, selected: int | None = None) -> None:
        self.list_widget.clear()
        for template in self.templates:
            icon = f"{template.icon}  " if template.icon else ""
            self.list_widget.addItem(f"{icon}{template.name}    {len(template.fields)} 项")
        if selected is not None and 0 <= selected < len(self.templates):
            self.list_widget.setCurrentRow(selected)

    def _add_template(self) -> None:
        template = NodeTemplate(id=new_id("template"), name="节点模板", color=DEFAULT_NODE_COLOR)
        node = self._template_to_node(template)
        dialog = NodeEditorDialog(self, node, self.theme)
        if dialog.exec() == QDialog.Accepted and dialog.result:
            self.templates.append(self._node_to_template(dialog.result, template.id))
            self._refresh(len(self.templates) - 1)

    def _edit_template(self) -> None:
        index = self._selected_index()
        if index is None:
            return
        template = self.templates[index]
        dialog = NodeEditorDialog(self, self._template_to_node(template), self.theme)
        if dialog.exec() == QDialog.Accepted and dialog.result:
            self.templates[index] = self._node_to_template(dialog.result, template.id)
            self._refresh(index)

    def _copy_template(self) -> None:
        index = self._selected_index()
        if index is None:
            return
        template = copy.deepcopy(self.templates[index])
        template.id = new_id("template")
        template.name = f"{template.name} 副本"
        self.templates.append(template)
        self._refresh(len(self.templates) - 1)

    def _delete_template(self) -> None:
        index = self._selected_index()
        if index is None:
            return
        del self.templates[index]
        self._refresh(min(index, len(self.templates) - 1) if self.templates else None)

    def _template_to_node(self, template: NodeTemplate) -> Node:
        return Node(
            id=template.id,
            title=template.name,
            color=template.color,
            icon=template.icon,
            icon_from_title=template.icon_from_title,
            title_field_id=template.title_field_id,
            fields=[NodeField.from_dict(field.to_dict()) for field in template.fields],
        )

    def _node_to_template(self, node: Node, template_id: str) -> NodeTemplate:
        existing = next((template for template in self.templates if template.id == template_id), None)
        return NodeTemplate(
            id=template_id,
            name=node.title,
            color=node.color,
            icon=existing.icon if existing is not None else "",
            icon_from_title=existing.icon_from_title if existing is not None else False,
            title_field_id=node.title_field_id,
            fields=[NodeField.from_dict(field.to_dict()) for field in node.fields],
        )

    def _accept(self) -> None:
        self.result = [copy.deepcopy(template) for template in self.templates]
        self.accept()

    def done(self, result: int) -> None:  # type: ignore[override]
        save_window_layout(self, "template_manager_dialog")
        super().done(result)
