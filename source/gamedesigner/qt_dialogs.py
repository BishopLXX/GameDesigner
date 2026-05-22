from __future__ import annotations

import copy
from pathlib import Path

from PySide6.QtCore import QPoint, QPointF, QRectF, Qt, QTimer, Signal
from PySide6.QtGui import (
    QAction,
    QColor,
    QPainter,
    QPainterPath,
    QPen,
    QPixmap,
)
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QGraphicsItem,
    QGraphicsObject,
    QGraphicsScene,
    QGraphicsView,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QMessageBox,
    QPushButton,
    QPlainTextEdit,
    QSplitter,
    QToolBar,
    QVBoxLayout,
    QWidget,
    QColorDialog,
)

from .models import DEFAULT_NODE_COLOR, FIELD_TYPES, Node, NodeField, NodeTemplate, new_id
from .qt_theme import palette


HEADER_HEIGHT = 52.0
FIELD_HANDLE = 16.0


def _safe_color(value: str, fallback: str) -> QColor:
    color = QColor(value)
    return color if color.isValid() else QColor(fallback)


class ProjectSettingsDialog(QDialog):
    def __init__(
        self,
        parent: QWidget | None,
        title: str,
        project_name: str,
        source_dir: str,
        output_dir: str,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle(title)
        self.setModal(True)
        self.result_data: dict[str, str] | None = None

        self.name_edit = QLineEdit(project_name)
        self.source_edit = QLineEdit(source_dir)
        self.output_edit = QLineEdit(output_dir)

        source_button = QPushButton("浏览")
        source_button.clicked.connect(self._pick_source)
        output_button = QPushButton("浏览")
        output_button.clicked.connect(self._pick_output)

        form = QFormLayout()
        form.setFieldGrowthPolicy(QFormLayout.AllNonFixedFieldsGrow)
        form.addRow("项目名称", self.name_edit)
        form.addRow("项目源目录", self._path_row(self.source_edit, source_button))
        form.addRow("输出目录", self._path_row(self.output_edit, output_button))

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
        self.resize(620, 180)

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
        self.result_data = {"name": name, "source_dir": source, "output_dir": output}
        self.accept()


class NodeFrameItem(QGraphicsItem):
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
        self.setZValue(-10)

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


class EditorFieldItem(QGraphicsObject):
    changed = Signal()
    clicked = Signal(int)

    def __init__(self, index: int, field: NodeField, theme: str) -> None:
        super().__init__()
        self.index = index
        self.field = field
        self.theme = theme
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
        painter.setPen(QPen(QColor(colors["blue"] if self.isSelected() else "#DADAE0"), 2 if self.isSelected() else 1))
        painter.drawPath(path)

        if self.field.image_path:
            pixmap = QPixmap(self.field.image_path)
            if not pixmap.isNull():
                target = QRectF(10, 10, max(10.0, rect.width() - 20), max(10.0, rect.height() - 20))
                scaled = pixmap.scaled(
                    int(target.width()),
                    int(target.height()),
                    Qt.KeepAspectRatio,
                    Qt.SmoothTransformation,
                )
                painter.drawPixmap(int(target.x()), int(target.y()), scaled)
            else:
                painter.setPen(QColor(colors["accent_dark"]))
                painter.drawText(rect.adjusted(10, 10, -10, -10), Qt.AlignLeft | Qt.AlignTop, "图片无法读取")

        text = self.field.value or self.field.name
        painter.setPen(QColor(self.field.text_color or colors["node_text"]))
        font = painter.font()
        font.setPointSize(max(8, min(48, self.field.font_size)))
        painter.setFont(font)
        painter.drawText(rect.adjusted(10, 9, -10, -9), Qt.TextWordWrap | Qt.AlignLeft | Qt.AlignTop, text)

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
        event.accept()

    def mouseMoveEvent(self, event) -> None:  # type: ignore[override]
        if self._resizing:
            delta = event.scenePos() - self._press_pos
            self.prepareGeometryChange()
            self.field.width = max(44.0, self._origin_rect.width() + delta.x())
            self.field.height = max(34.0, self._origin_rect.height() + delta.y())
            self.update()
            event.accept()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event) -> None:  # type: ignore[override]
        self.field.x = max(0.0, self.pos().x())
        self.field.y = max(0.0, self.pos().y() - HEADER_HEIGHT)
        self._resizing = False
        self.changed.emit()
        super().mouseReleaseEvent(event)

    def itemChange(self, change: QGraphicsItem.GraphicsItemChange, value):  # type: ignore[override]
        if change == QGraphicsItem.ItemPositionChange and self.scene():
            pos = value
            return QPointF(max(0.0, pos.x()), max(HEADER_HEIGHT, pos.y()))
        if change == QGraphicsItem.ItemPositionHasChanged:
            self.field.x = max(0.0, self.pos().x())
            self.field.y = max(0.0, self.pos().y() - HEADER_HEIGHT)
        return super().itemChange(change, value)

    def _on_handle(self, pos: QPointF) -> bool:
        rect = self.boundingRect()
        return pos.x() >= rect.right() - FIELD_HANDLE and pos.y() >= rect.bottom() - FIELD_HANDLE


class FieldCanvas(QGraphicsView):
    fieldSelected = Signal(int)
    fieldActivated = Signal(int)
    fieldChanged = Signal()

    def __init__(self, fields: list[NodeField], theme: str) -> None:
        super().__init__()
        self.fields = fields
        self.theme = theme
        self.title = ""
        self.icon = ""
        self.node_color = "#FFFFFF"
        self.selected_index: int | None = 0 if fields else None
        self._panning = False
        self._last_pan = QPointF()
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

    def refresh(self, selected_index: int | None = None) -> None:
        self.scene_obj.clear()
        self.selected_index = selected_index if selected_index is not None else self.selected_index
        width, height = self._card_size()
        frame = NodeFrameItem(self.title, self.icon, width, height, self.theme, self.node_color)
        self.scene_obj.addItem(frame)
        for index, field in enumerate(self.fields):
            item = EditorFieldItem(index, field, self.theme)
            item.clicked.connect(self._select_item)
            item.changed.connect(self.fieldChanged.emit)
            item.setSelected(index == self.selected_index)
            self.scene_obj.addItem(item)
        self.scene_obj.setSceneRect(QRectF(-80, -80, width + 160, height + 160))

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
                self._select_item(item.index)
                self.fieldActivated.emit(item.index)
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

    def _select_item(self, index: int) -> None:
        self.selected_index = index
        for item in self.scene_obj.items():
            if isinstance(item, EditorFieldItem):
                item.setSelected(item.index == index)
        self.fieldSelected.emit(index)

    def _field_at(self, pos: QPoint) -> EditorFieldItem | None:
        for item in self.items(pos):
            if isinstance(item, EditorFieldItem):
                return item
        return None

    def _card_size(self) -> tuple[float, float]:
        width = max([field.x + field.width + 24 for field in self.fields] + [430])
        height = max([HEADER_HEIGHT + field.y + field.height + 24 for field in self.fields] + [300])
        return max(430.0, width), max(300.0, height)


class NodeEditorDialog(QDialog):
    def __init__(self, parent: QWidget | None, node: Node, theme: str = "dark") -> None:
        super().__init__(parent)
        self.setWindowTitle("编辑节点")
        self.setModal(True)
        self.theme = theme
        self.result: Node | None = None
        self._node_id = node.id
        self._x = node.x
        self._y = node.y
        self._width = node.width
        self._height = node.height
        self.fields = [copy.deepcopy(field) for field in node.fields]
        self._ensure_visual_layout()
        self._updating = False

        self.title_edit = QLineEdit(node.title)
        self.icon_edit = QLineEdit(node.icon)
        self.color_edit = QLineEdit(node.color or DEFAULT_NODE_COLOR)

        self.field_name = QLineEdit()
        self.field_type = QComboBox()
        self.field_type.addItems(FIELD_TYPES)
        self.field_value = QPlainTextEdit()
        self.image_path = QLineEdit()
        self.field_x = QLineEdit()
        self.field_y = QLineEdit()
        self.field_w = QLineEdit()
        self.field_h = QLineEdit()
        self.font_size = QLineEdit()
        self.text_color = QLineEdit()
        self.bg_color = QLineEdit()

        self.canvas = FieldCanvas(self.fields, theme)
        self.canvas.set_header(self.title_edit.text(), self.icon_edit.text(), self.color_edit.text())
        self.canvas.fieldSelected.connect(self._load_selected_props)
        self.canvas.fieldActivated.connect(self._focus_field_value)
        self.canvas.fieldChanged.connect(self._on_field_changed)

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
        self.resize(1160, 760)
        self._connect_changes()
        self._load_selected_props(self.canvas.selected_index if self.canvas.selected_index is not None else -1)

    def _side_panel(self) -> QWidget:
        panel = QWidget()
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(12, 0, 0, 0)
        layout.setSpacing(12)

        card_box = QGroupBox("节点卡牌")
        card_form = QFormLayout(card_box)
        card_form.addRow("名称", self.title_edit)
        card_form.addRow("图标", self.icon_edit)
        card_form.addRow("颜色", self._color_row(self.color_edit, self._pick_node_color))

        toolbar = QToolBar()
        add_text = QAction("新增文字卡片", self)
        add_text.triggered.connect(self._add_text_card)
        add_image = QAction("新增图片卡片", self)
        add_image.triggered.connect(self._add_image_card)
        delete_card = QAction("删除选中卡片", self)
        delete_card.triggered.connect(self._delete_selected_card)
        toolbar.addAction(add_text)
        toolbar.addAction(add_image)
        toolbar.addAction(delete_card)

        props = QGroupBox("选中卡片属性")
        props_form = QFormLayout(props)
        props_form.addRow("字段", self.field_name)
        props_form.addRow("类型", self.field_type)
        props_form.addRow("内容", self.field_value)
        props_form.addRow("图片", self._path_row(self.image_path, "选择图片", self._pick_image))
        props_form.addRow("X", self.field_x)
        props_form.addRow("Y", self.field_y)
        props_form.addRow("宽", self.field_w)
        props_form.addRow("高", self.field_h)
        props_form.addRow("字号", self.font_size)
        props_form.addRow("文字色", self._color_row(self.text_color, self._pick_text_color))
        props_form.addRow("背景色", self._color_row(self.bg_color, self._pick_bg_color))

        layout.addWidget(card_box)
        layout.addWidget(toolbar)
        layout.addWidget(props, 1)
        return panel

    def _path_row(self, edit: QLineEdit, label: str, slot) -> QWidget:
        row = QWidget()
        layout = QHBoxLayout(row)
        layout.setContentsMargins(0, 0, 0, 0)
        button = QPushButton(label)
        button.clicked.connect(slot)
        layout.addWidget(edit, 1)
        layout.addWidget(button)
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
        self.title_edit.textChanged.connect(lambda _text: self._update_canvas_header())
        self.icon_edit.textChanged.connect(lambda _text: self._update_canvas_header())
        self.color_edit.textChanged.connect(lambda _text: self._update_canvas_header())
        for widget in (
            self.field_name,
            self.image_path,
            self.field_x,
            self.field_y,
            self.field_w,
            self.field_h,
            self.font_size,
            self.text_color,
            self.bg_color,
        ):
            widget.textChanged.connect(self._apply_props)
        self.field_type.currentTextChanged.connect(lambda _text: self._apply_props())
        self.field_value.textChanged.connect(self._apply_props)

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
        self.canvas.set_header(self.title_edit.text(), self.icon_edit.text(), self.color_edit.text())

    def _selected_field(self) -> NodeField | None:
        index = self.canvas.selected_index
        if index is None or index < 0 or index >= len(self.fields):
            return None
        return self.fields[index]

    def _load_selected_props(self, index: int) -> None:
        self._updating = True
        self.canvas.selected_index = index if 0 <= index < len(self.fields) else None
        field = self._selected_field()
        if not field:
            for widget in (
                self.field_name,
                self.image_path,
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
            self._updating = False
            return
        self.field_name.setText(field.name)
        self.field_type.setCurrentText(field.data_type if field.data_type in FIELD_TYPES else "文本")
        self.field_value.setPlainText(field.value)
        self.image_path.setText(field.image_path)
        self.field_x.setText(f"{field.x:.0f}")
        self.field_y.setText(f"{field.y:.0f}")
        self.field_w.setText(f"{field.width:.0f}")
        self.field_h.setText(f"{field.height:.0f}")
        self.font_size.setText(str(field.font_size))
        self.text_color.setText(field.text_color)
        self.bg_color.setText(field.bg_color)
        self._updating = False

    def _apply_props(self) -> None:
        if self._updating:
            return
        field = self._selected_field()
        if not field:
            return
        field.name = self.field_name.text().strip() or "字段"
        field.data_type = self.field_type.currentText() if self.field_type.currentText() in FIELD_TYPES else "文本"
        field.value = self.field_value.toPlainText().strip()
        field.image_path = self.image_path.text().strip()
        field.x = self._float_text(self.field_x, field.x)
        field.y = self._float_text(self.field_y, field.y)
        field.width = max(44.0, self._float_text(self.field_w, field.width))
        field.height = max(34.0, self._float_text(self.field_h, field.height))
        field.font_size = max(8, min(48, int(self._float_text(self.font_size, field.font_size))))
        field.text_color = self.text_color.text().strip() or "#1D1D1F"
        field.bg_color = self.bg_color.text().strip() or "#FFFFFF"
        self.canvas.refresh(self.canvas.selected_index)

    def _on_field_changed(self) -> None:
        if self.canvas.selected_index is not None:
            self._load_selected_props(self.canvas.selected_index)
        selected_index = self.canvas.selected_index
        QTimer.singleShot(0, lambda: self.canvas.refresh(selected_index))

    def _focus_field_value(self, index: int) -> None:
        self._load_selected_props(index)
        self.field_value.setFocus()
        self.field_value.selectAll()

    def _add_text_card(self) -> None:
        field = NodeField(name="文字", data_type="长文本", value="双击右侧内容编辑文字")
        field.x = 24
        field.y = max([item.y + item.height + 12 for item in self.fields] + [18])
        field.width = 320
        field.height = 92
        field.font_size = 14
        field.text_color = "#1D1D1F"
        field.bg_color = "#FFFFFF"
        self.fields.append(field)
        self.canvas.refresh(len(self.fields) - 1)
        self._load_selected_props(len(self.fields) - 1)

    def _add_image_card(self) -> None:
        field = NodeField(name="图片", data_type="资源路径", value="图片")
        field.x = 24
        field.y = max([item.y + item.height + 12 for item in self.fields] + [18])
        field.width = 280
        field.height = 170
        field.bg_color = "#F6F6F8"
        self.fields.append(field)
        self.canvas.refresh(len(self.fields) - 1)
        self._load_selected_props(len(self.fields) - 1)
        self._pick_image()

    def _delete_selected_card(self) -> None:
        index = self.canvas.selected_index
        if index is None or index < 0 or index >= len(self.fields):
            return
        del self.fields[index]
        next_index = min(index, len(self.fields) - 1) if self.fields else None
        self.canvas.refresh(next_index)
        self._load_selected_props(next_index if next_index is not None else -1)

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
        self.result = Node(
            id=self._node_id,
            title=title,
            x=self._x,
            y=self._y,
            width=self._width,
            height=self._height,
            color=self.color_edit.text().strip() or DEFAULT_NODE_COLOR,
            icon=self.icon_edit.text().strip(),
            fields=[copy.deepcopy(field) for field in self.fields],
        )
        self.accept()


class TemplateManagerDialog(QDialog):
    def __init__(self, parent: QWidget | None, templates: list[NodeTemplate], theme: str = "dark") -> None:
        super().__init__(parent)
        self.setWindowTitle("节点模板")
        self.setModal(True)
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
            fields=[NodeField.from_dict(field.to_dict()) for field in template.fields],
        )

    def _node_to_template(self, node: Node, template_id: str) -> NodeTemplate:
        return NodeTemplate(
            id=template_id,
            name=node.title,
            color=node.color,
            icon=node.icon,
            fields=[NodeField.from_dict(field.to_dict()) for field in node.fields],
        )

    def _accept(self) -> None:
        self.result = [copy.deepcopy(template) for template in self.templates]
        self.accept()
