from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QAbstractItemView, QTableWidget, QTableWidgetItem

from ..models import CanvasData, Node, NodeTemplate, ProjectData


class DataCanvasTableWidget(QTableWidget):
    projectChanged = Signal()

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.project: ProjectData | None = None
        self.canvas: CanvasData | None = None
        self.template: NodeTemplate | None = None
        self._field_ids: list[str] = []
        self._row_node_ids: list[str] = []
        self._loading = False

        self.setAlternatingRowColors(True)
        self.setSelectionBehavior(QAbstractItemView.SelectItems)
        self.setSelectionMode(QAbstractItemView.SingleSelection)
        self.setEditTriggers(
            QAbstractItemView.DoubleClicked
            | QAbstractItemView.EditKeyPressed
            | QAbstractItemView.AnyKeyPressed
        )
        self.horizontalHeader().setStretchLastSection(True)
        self.itemChanged.connect(self._apply_item_change)

    def set_canvas(self, project: ProjectData, canvas: CanvasData) -> None:
        self.project = project
        self.canvas = canvas
        self.template = project.find_template(canvas.template_id) if canvas.template_id else None
        self._reload()

    def _reload(self) -> None:
        self._loading = True
        try:
            self.clear()
            self._field_ids = []
            self._row_node_ids = []
            if self.canvas is None:
                self.setRowCount(0)
                self.setColumnCount(0)
                return

            fields = list(self.template.fields) if self.template is not None else []
            self._field_ids = [field.id for field in fields]
            self.setColumnCount(len(fields))
            self.setHorizontalHeaderLabels([field.name or "字段" for field in fields])

            ordered_nodes = sorted(self.canvas.nodes, key=lambda node: node.order)
            self._row_node_ids = [node.id for node in ordered_nodes]
            self.setRowCount(len(ordered_nodes))
            self.setVerticalHeaderLabels([str(index) for index in range(1, len(ordered_nodes) + 1)])

            for row, node in enumerate(ordered_nodes):
                field_map = {field.id: field for field in node.fields}
                for column, field_id in enumerate(self._field_ids):
                    field = field_map.get(field_id)
                    value = ""
                    if field is not None:
                        value = field.image_path if field.data_type == "图片" else field.value
                    item = QTableWidgetItem(value)
                    item.setTextAlignment(Qt.AlignLeft | Qt.AlignVCenter)
                    self.setItem(row, column, item)
        finally:
            self._loading = False

    def _apply_item_change(self, item: QTableWidgetItem) -> None:
        if self._loading or self.canvas is None:
            return
        row = item.row()
        column = item.column()
        if not (0 <= row < len(self._row_node_ids) and 0 <= column < len(self._field_ids)):
            return
        node = self.canvas.find_node(self._row_node_ids[row])
        if node is None:
            return
        field_id = self._field_ids[column]
        field = next((candidate for candidate in node.fields if candidate.id == field_id), None)
        if field is None:
            return
        if field.data_type == "图片":
            field.image_path = item.text()
        else:
            field.value = item.text()
        if node.title_field_id == field.id:
            node.title = item.text().strip() or field.name.strip() or (self.template.name if self.template else "数据项")
        self.projectChanged.emit()
