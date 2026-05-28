from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtGui import QFontMetrics, QPixmap
from PySide6.QtWidgets import (
    QFrame,
    QGraphicsView,
    QHBoxLayout,
    QLabel,
    QScrollArea,
    QSizePolicy,
    QTextBrowser,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from ..models import CanvasData, Node, NodeField, ProjectData
from ..project_files.linked_documents import read_link_document
from ..qt_canvas import NodeGraphView
from ..image_rendering import is_pixel_art_image_path


class NodePreviewPanel(QWidget):
    closeRequested = Signal()
    openCanvasRequested = Signal(str)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("nodePreviewPanel")
        self.setMinimumSize(360, 320)
        self.setMaximumSize(560, 680)
        self._theme = "dark"
        self._project_path: Path | None = None
        self._canvas_view: NodeGraphView | None = None

        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 10, 12, 12)
        layout.setSpacing(8)

        header = QHBoxLayout()
        header.setContentsMargins(0, 0, 0, 0)
        header.setSpacing(8)
        self.type_label = QLabel("节点预览", self)
        self.type_label.setObjectName("nodePreviewTypeLabel")
        self.title_label = QLabel("", self)
        self.title_label.setObjectName("nodePreviewTitle")
        self.title_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self.title_label.setWordWrap(False)
        header.addWidget(self.type_label)
        header.addWidget(self.title_label, 1)
        self.close_button = QToolButton(self)
        self.close_button.setObjectName("nodePreviewCloseButton")
        self.close_button.setText("×")
        self.close_button.setFixedSize(24, 24)
        self.close_button.setToolTip("关闭预览")
        self.close_button.clicked.connect(self.closeRequested.emit)
        header.addWidget(self.close_button)
        layout.addLayout(header)

        self.body = QScrollArea(self)
        self.body.setObjectName("nodePreviewScroll")
        self.body.setWidgetResizable(True)
        self.body.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.body.setFrameShape(QFrame.NoFrame)
        layout.addWidget(self.body, 1)

        self.empty_label = QLabel("选择节点后预览", self)
        self.empty_label.setObjectName("nodePreviewEmptyLabel")
        self.empty_label.setAlignment(Qt.AlignCenter)
        self.empty_label.setWordWrap(True)
        self.body.setWidget(self.empty_label)

    def set_theme(self, theme: str) -> None:
        self._theme = theme
        if self._canvas_view is not None:
            self._canvas_view.set_theme(theme)

    def clear_preview(self) -> None:
        self._clear_canvas_view()
        self.type_label.setText("节点预览")
        self.title_label.setText("")
        self._set_body_widget(self._empty_message("选择节点后预览"))

    def set_node(
        self,
        project: ProjectData,
        canvas: CanvasData,
        node: Node,
        project_path: Path | None,
        theme: str,
    ) -> None:
        self.set_theme(theme)
        self._project_path = project_path
        self._clear_canvas_view()
        self.type_label.setText(self._node_type_label(node))
        self.title_label.setText(node.title)
        normalized_type = node.normalized_node_type()
        if normalized_type == "画布":
            widget = self._canvas_preview(project, node)
        elif normalized_type == "超文本":
            widget = self._document_preview(node, project_path)
        else:
            widget = self._normal_node_preview(canvas, node)
        self._set_body_widget(widget)

    def _clear_canvas_view(self) -> None:
        if self._canvas_view is not None:
            self._canvas_view.deleteLater()
            self._canvas_view = None

    def _node_type_label(self, node: Node) -> str:
        normalized_type = node.normalized_node_type()
        if normalized_type == "画布":
            return "画布预览"
        if normalized_type == "超文本":
            return f"{node.link_format.upper()} 预览"
        return "节点预览"

    def _normal_node_preview(self, canvas: CanvasData, node: Node) -> QWidget:
        container = QWidget()
        container.setObjectName("nodePreviewBody")
        layout = QVBoxLayout(container)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)

        summary = self._summary_card()
        summary_layout = QVBoxLayout(summary)
        summary_layout.setContentsMargins(10, 8, 10, 8)
        summary_layout.setSpacing(5)
        summary_layout.addWidget(self._meta_row("类型", node.normalized_node_type()))
        summary_layout.addWidget(self._meta_row("字段", str(len(node.fields))))
        if node.notes:
            summary_layout.addWidget(self._meta_row("便签", str(len(node.notes))))
        layout.addWidget(summary)

        if canvas.is_data_canvas() and canvas.data_layout == "table":
            layout.addWidget(self._meta_row("画布模式", "表格画布节点"))

        if not node.fields:
            layout.addWidget(self._empty_card("暂无数据字段"))
            layout.addStretch(1)
            return container

        visual_fields = [field for field in node.fields if field.has_visual_layout()]
        if visual_fields:
            layout.addWidget(self._visual_fields_preview(visual_fields))
        else:
            for field in node.fields:
                layout.addWidget(self._field_card(field))
        layout.addStretch(1)
        return container

    def _canvas_preview(self, project: ProjectData, node: Node) -> QWidget:
        target = project.find_canvas(node.canvas_id) if node.canvas_id else None
        container = QWidget()
        container.setObjectName("nodePreviewBody")
        layout = QVBoxLayout(container)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)

        if target is None:
            layout.addWidget(self._empty_card("这个画布节点还没有绑定画布"))
            layout.addStretch(1)
            return container

        tools = QHBoxLayout()
        tools.setContentsMargins(0, 0, 0, 0)
        tools.setSpacing(6)
        canvas_name = QLabel(target.name, container)
        canvas_name.setObjectName("nodePreviewFieldName")
        canvas_name.setWordWrap(False)
        tools.addWidget(canvas_name, 1)
        fit_button = QToolButton(container)
        fit_button.setObjectName("nodePreviewMiniButton")
        fit_button.setText("适配")
        fit_button.setToolTip("适配预览画布")
        open_button = QToolButton(container)
        open_button.setObjectName("nodePreviewMiniButton")
        open_button.setText("打开")
        open_button.setToolTip("打开这个画布")
        open_button.clicked.connect(lambda _checked=False, canvas_id=target.id: self.openCanvasRequested.emit(canvas_id))
        tools.addWidget(fit_button)
        tools.addWidget(open_button)
        layout.addLayout(tools)

        canvas_holder = QFrame(container)
        canvas_holder.setObjectName("nodePreviewCanvasFrame")
        canvas_layout = QVBoxLayout(canvas_holder)
        canvas_layout.setContentsMargins(0, 0, 0, 0)
        canvas_layout.setSpacing(0)
        preview = NodeGraphView(target, self._theme, read_only=True, templates=project.templates)
        preview.setObjectName("nodePreviewCanvasView")
        preview.setMinimumHeight(300)
        preview.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        preview.setHorizontalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        preview.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        preview.setInteractive(True)
        preview.setDragMode(QGraphicsView.ScrollHandDrag)
        preview.setTransformationAnchor(QGraphicsView.AnchorUnderMouse)
        fit_button.clicked.connect(lambda _checked=False, view=preview: self._fit_canvas_preview(view))
        self._canvas_view = preview
        canvas_layout.addWidget(preview)
        layout.addWidget(canvas_holder, 1)

        summary = self._summary_card()
        summary_layout = QHBoxLayout(summary)
        summary_layout.setContentsMargins(10, 8, 10, 8)
        summary_layout.setSpacing(8)
        summary_layout.addWidget(self._meta_row("画布", target.name), 1)
        summary_layout.addWidget(self._meta_row("节点", str(len(target.nodes))))
        summary_layout.addWidget(self._meta_row("连线", str(len(target.valid_edges()))))
        layout.addWidget(summary)

        if target.ai_rules.strip():
            rules = QTextBrowser(container)
            rules.setObjectName("nodePreviewBrowser")
            rules.setMaximumHeight(92)
            rules.setPlainText(target.ai_rules.strip())
            layout.addWidget(rules)

        self._fit_canvas_preview(preview)
        QTimer.singleShot(0, lambda view=preview: self._fit_canvas_preview(view) if view is self._canvas_view else None)
        return container

    def _document_preview(self, node: Node, project_path: Path | None) -> QWidget:
        browser = QTextBrowser()
        browser.setObjectName("nodePreviewBrowser")
        browser.setOpenExternalLinks(True)
        if not project_path or not node.link_path:
            browser.setPlainText("这个文档节点还没有可预览的文档路径。")
            return browser
        try:
            content = read_link_document(project_path, node.link_path)
        except OSError as exc:
            browser.setPlainText(f"读取文档失败：{exc}")
            return browser
        if not content.strip():
            browser.setPlainText("文档为空。")
            return browser
        if node.link_format == "md" or node.link_path.lower().endswith(".md"):
            browser.setMarkdown(content)
        else:
            browser.setPlainText(content)
        return browser

    def _visual_fields_preview(self, fields: list[NodeField]) -> QWidget:
        container = QFrame()
        container.setObjectName("nodePreviewVisualCanvas")
        width = max(field.x + field.width for field in fields)
        height = max(field.y + field.height for field in fields)
        scale = min(1.0, 500.0 / max(width, 1.0))
        container.setMinimumSize(max(260, int(width * scale) + 24), max(120, int(height * scale) + 24))
        for field in fields:
            card = self._visual_field_card(field, scale, container)
            card.move(int(12 + field.x * scale), int(12 + field.y * scale))
            card.resize(max(60, int(field.width * scale)), max(44, int(field.height * scale)))
        return container

    def _visual_field_card(self, field: NodeField, scale: float, parent: QWidget) -> QFrame:
        card = QFrame(parent)
        card.setObjectName("nodePreviewVisualField")
        layout = QVBoxLayout(card)
        layout.setContentsMargins(8, 6, 8, 6)
        layout.setSpacing(4)
        if field.data_type == "图片" and field.image_path:
            image = QLabel(card)
            image.setObjectName("nodePreviewImageLabel")
            pixmap = QPixmap(str(self._resolved_image_path(field.image_path)))
            if not pixmap.isNull():
                image.setPixmap(
                    pixmap.scaled(
                        max(1, int(220 * scale)),
                        max(1, int(150 * scale)),
                        Qt.KeepAspectRatio,
                        Qt.FastTransformation if is_pixel_art_image_path(field.image_path) else Qt.SmoothTransformation,
                    )
                )
                image.setAlignment(Qt.AlignCenter)
                layout.addWidget(image, 1)
            else:
                layout.addWidget(self._small_text("图片路径无效"))
        if field.show_label and field.name.strip():
            layout.addWidget(self._small_text(field.name.strip(), bold=True))
        value = field.value.strip() or (field.image_path if field.data_type == "图片" else "")
        if value:
            layout.addWidget(self._small_text(value))
        return card

    def _field_card(self, field: NodeField) -> QWidget:
        card = self._summary_card()
        layout = QVBoxLayout(card)
        layout.setContentsMargins(10, 8, 10, 8)
        layout.setSpacing(6)
        top = QHBoxLayout()
        top.setContentsMargins(0, 0, 0, 0)
        name = QLabel(field.name or "字段", card)
        name.setObjectName("nodePreviewFieldName")
        name.setWordWrap(True)
        field_type = QLabel(field.data_type or "文本", card)
        field_type.setObjectName("nodePreviewFieldType")
        top.addWidget(name, 1)
        top.addWidget(field_type)
        layout.addLayout(top)
        if field.data_type == "图片":
            layout.addWidget(self._image_preview(field))
        else:
            value = QLabel(field.value or " ", card)
            value.setObjectName("nodePreviewValue")
            value.setWordWrap(True)
            value.setTextInteractionFlags(Qt.TextSelectableByMouse)
            layout.addWidget(value)
        return card

    def _image_preview(self, field: NodeField) -> QWidget:
        holder = QFrame()
        holder.setObjectName("nodePreviewImageFrame")
        layout = QVBoxLayout(holder)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(6)
        image = QLabel(holder)
        image.setObjectName("nodePreviewImageLabel")
        image.setAlignment(Qt.AlignCenter)
        pixmap = QPixmap(str(self._resolved_image_path(field.image_path)))
        if not pixmap.isNull():
            image.setPixmap(
                pixmap.scaled(
                    420,
                    220,
                    Qt.KeepAspectRatio,
                    Qt.FastTransformation if is_pixel_art_image_path(field.image_path) else Qt.SmoothTransformation,
                )
            )
            layout.addWidget(image)
        else:
            layout.addWidget(self._small_text("图片路径无效或为空"))
        if field.image_path:
            layout.addWidget(self._small_text(field.image_path))
        return holder

    def _summary_card(self) -> QFrame:
        card = QFrame()
        card.setObjectName("nodePreviewCard")
        return card

    def _empty_card(self, text: str) -> QWidget:
        card = self._summary_card()
        layout = QVBoxLayout(card)
        layout.setContentsMargins(12, 18, 12, 18)
        layout.addWidget(self._empty_message(text))
        return card

    def _meta_row(self, label: str, value: str) -> QWidget:
        row = QWidget()
        row.setObjectName("nodePreviewMetaRow")
        layout = QHBoxLayout(row)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)
        key = QLabel(label, row)
        key.setObjectName("nodePreviewMetaKey")
        val = QLabel(self._elide(value, 220), row)
        val.setObjectName("nodePreviewMetaValue")
        val.setToolTip(value)
        layout.addWidget(key)
        layout.addWidget(val, 1)
        return row

    def _small_text(self, text: str, *, bold: bool = False) -> QLabel:
        label = QLabel(text)
        label.setObjectName("nodePreviewSmallText")
        label.setWordWrap(True)
        label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        font = label.font()
        font.setBold(bold)
        label.setFont(font)
        return label

    def _elide(self, text: str, width: int) -> str:
        metrics = QFontMetrics(self.font())
        return metrics.elidedText(text or " ", Qt.ElideRight, width)

    def _resolved_image_path(self, raw_path: str) -> Path:
        path = Path(raw_path)
        if path.is_absolute() or self._project_path is None:
            return path
        return self._project_path.parent / path

    def _fit_canvas_preview(self, view: NodeGraphView | None) -> None:
        if view is None:
            return
        view.resetTransform()
        bounds = view.scene_obj.itemsBoundingRect().adjusted(-120, -120, 120, 120)
        if bounds.isValid() and bounds.width() > 0 and bounds.height() > 0:
            view.fitInView(bounds, Qt.KeepAspectRatio)
        else:
            view.reset_view()
        view.viewport().update()

    def _empty_message(self, text: str) -> QLabel:
        label = QLabel(text)
        label.setObjectName("nodePreviewEmptyLabel")
        label.setAlignment(Qt.AlignCenter)
        label.setWordWrap(True)
        return label

    def _set_body_widget(self, widget: QWidget) -> None:
        old = self.body.takeWidget()
        if old is not None:
            old.deleteLater()
        self.body.setWidget(widget)
