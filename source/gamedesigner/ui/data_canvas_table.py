from __future__ import annotations

import csv
from io import StringIO

from PySide6.QtCore import QPoint, Qt, Signal
from PySide6.QtGui import QKeySequence
from PySide6.QtWidgets import QApplication, QAbstractItemView, QInputDialog, QMenu, QTableWidget, QTableWidgetItem

from ..models import CanvasData, Node, NodeField, NodeTemplate, ProjectData


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
        self._renaming_column = False

        self.setAlternatingRowColors(True)
        self.setSelectionBehavior(QAbstractItemView.SelectItems)
        self.setSelectionMode(QAbstractItemView.SingleSelection)
        self.setEditTriggers(
            QAbstractItemView.DoubleClicked
            | QAbstractItemView.EditKeyPressed
            | QAbstractItemView.AnyKeyPressed
        )
        self.horizontalHeader().setStretchLastSection(True)
        self.horizontalHeader().setSectionsClickable(True)
        self.horizontalHeader().sectionClicked.connect(self._edit_column_name)
        self.itemChanged.connect(self._apply_item_change)
        self.setContextMenuPolicy(Qt.CustomContextMenu)
        self.customContextMenuRequested.connect(self._show_context_menu)

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

    def _edit_column_name(self, column: int) -> None:
        if self._renaming_column or self.template is None or not (0 <= column < len(self._field_ids)):
            return
        field = self._template_field_for_column(column)
        if field is None:
            return
        self._renaming_column = True
        try:
            name, ok = QInputDialog.getText(self, "重命名列", "列名", text=field.name or "字段")
        finally:
            self._renaming_column = False
        if not ok:
            return
        self._rename_column(column, name)

    def _rename_column(self, column: int, name: str) -> None:
        if self.template is None or not (0 <= column < len(self._field_ids)):
            return
        new_name = name.strip()
        if not new_name:
            return
        field = self._template_field_for_column(column)
        if field is None or field.name == new_name:
            return
        field_id = self._field_ids[column]
        field.name = new_name
        if self.canvas is not None:
            for node in self.canvas.nodes:
                node_field = next((candidate for candidate in node.fields if candidate.id == field_id), None)
                if node_field is None:
                    continue
                node_field.name = new_name
                if node.title_field_id == field_id:
                    value = node_field.image_path if node_field.data_type == "图片" else node_field.value
                    node.title = value.strip() or new_name or (self.template.name if self.template else "数据项")
        self._reload()
        self.projectChanged.emit()

    def _template_field_for_column(self, column: int) -> NodeField | None:
        if self.template is None or not (0 <= column < len(self._field_ids)):
            return None
        field_id = self._field_ids[column]
        return next((candidate for candidate in self.template.fields if candidate.id == field_id), None)

    def keyPressEvent(self, event) -> None:  # type: ignore[override]
        if event.matches(QKeySequence.Copy):
            self._copy_selection()
            event.accept()
            return
        if event.matches(QKeySequence.Paste):
            self._paste_clipboard()
            event.accept()
            return
        if event.key() in (Qt.Key_Delete, Qt.Key_Backspace):
            self._clear_selection()
            event.accept()
            return
        super().keyPressEvent(event)

    def _show_context_menu(self, pos: QPoint) -> None:
        menu = QMenu(self)
        add_row = menu.addAction("新增行")
        add_column = menu.addAction("新增列")
        menu.addSeparator()
        clear_cells = menu.addAction("清空选区")
        delete_rows = menu.addAction("删除选中行")
        delete_columns = menu.addAction("删除选中列")
        menu.addSeparator()
        fit_columns = menu.addAction("自适应列宽")
        action = menu.exec(self.viewport().mapToGlobal(pos))
        if action == add_row:
            self._append_row()
        elif action == add_column:
            self._append_column()
        elif action == clear_cells:
            self._clear_selection()
        elif action == delete_rows:
            self._delete_selected_rows()
        elif action == delete_columns:
            self._delete_selected_columns()
        elif action == fit_columns:
            self.resizeColumnsToContents()

    def _copy_selection(self) -> None:
        selected_range = self._primary_selection_range()
        if selected_range is None:
            return
        rows: list[str] = []
        for row in range(selected_range.topRow(), selected_range.bottomRow() + 1):
            values: list[str] = []
            for column in range(selected_range.leftColumn(), selected_range.rightColumn() + 1):
                item = self.item(row, column)
                values.append((item.text() if item is not None else "").replace("\t", " ").replace("\n", " "))
            rows.append("\t".join(values))
        QApplication.clipboard().setText("\n".join(rows))

    def _paste_clipboard(self) -> None:
        text = QApplication.clipboard().text()
        rows = self._parse_clipboard_rows(text)
        if not rows:
            return
        start_row, start_column = self._paste_start_cell()
        max_columns = max(len(row) for row in rows)
        self._ensure_column_count(start_column + max_columns)
        self._ensure_row_count(start_row + len(rows))
        self._reload()

        changed = False
        self._loading = True
        try:
            for row_offset, values in enumerate(rows):
                for column_offset, value in enumerate(values):
                    row = start_row + row_offset
                    column = start_column + column_offset
                    self._write_cell_value(row, column, value)
                    item = self.item(row, column)
                    if item is None:
                        item = QTableWidgetItem()
                        self.setItem(row, column, item)
                    item.setText(value)
                    changed = True
        finally:
            self._loading = False
        if changed:
            self._reload()
            self.projectChanged.emit()

    def _parse_clipboard_rows(self, text: str) -> list[list[str]]:
        text = text.strip("\r\n")
        if not text:
            return []
        delimiter = "\t" if "\t" in text else ","
        reader = csv.reader(StringIO(text), delimiter=delimiter)
        return [row for row in reader]

    def _paste_start_cell(self) -> tuple[int, int]:
        selected_range = self._primary_selection_range()
        if selected_range is not None:
            return selected_range.topRow(), selected_range.leftColumn()
        current = self.currentIndex()
        if current.isValid():
            return max(0, current.row()), max(0, current.column())
        return 0, 0

    def _clear_selection(self) -> None:
        indexes = self.selectedIndexes()
        if not indexes:
            return
        changed = False
        self._loading = True
        try:
            for index in indexes:
                self._write_cell_value(index.row(), index.column(), "")
                item = self.item(index.row(), index.column())
                if item is not None and item.text():
                    item.setText("")
                changed = True
        finally:
            self._loading = False
        if changed:
            self._reload()
            self.projectChanged.emit()

    def _append_row(self) -> None:
        self._ensure_row_count(self.rowCount() + 1)
        self._reload()
        self.projectChanged.emit()

    def _append_column(self) -> None:
        self._ensure_column_count(self.columnCount() + 1)
        self._reload()
        self.projectChanged.emit()

    def _delete_selected_rows(self) -> None:
        if self.canvas is None:
            return
        rows = sorted({index.row() for index in self.selectedIndexes()}, reverse=True)
        if not rows:
            current = self.currentRow()
            rows = [current] if current >= 0 else []
        node_ids = {self._row_node_ids[row] for row in rows if 0 <= row < len(self._row_node_ids)}
        if not node_ids:
            return
        self.canvas.nodes[:] = [node for node in self.canvas.nodes if node.id not in node_ids]
        self.canvas.normalize_node_order()
        self._reload()
        self.projectChanged.emit()

    def _delete_selected_columns(self) -> None:
        if self.template is None:
            return
        columns = sorted({index.column() for index in self.selectedIndexes()}, reverse=True)
        if not columns:
            current = self.currentColumn()
            columns = [current] if current >= 0 else []
        field_ids = {self._field_ids[column] for column in columns if 0 <= column < len(self._field_ids)}
        if not field_ids:
            return
        self.template.fields[:] = [field for field in self.template.fields if field.id not in field_ids]
        if self.template.title_field_id in field_ids:
            self.template.title_field_id = self.template.fields[0].id if self.template.fields else ""
        if self.canvas is not None:
            for node in self.canvas.nodes:
                node.fields[:] = [field for field in node.fields if field.id not in field_ids]
                if node.title_field_id in field_ids:
                    node.title_field_id = self.template.title_field_id
        self._reload()
        self.projectChanged.emit()

    def _primary_selection_range(self):
        ranges = self.selectedRanges()
        return ranges[0] if ranges else None

    def _ensure_template(self) -> NodeTemplate | None:
        if self.project is None or self.canvas is None:
            return None
        if self.template is not None:
            return self.template
        self.template = NodeTemplate(name=f"{self.canvas.name}模板")
        self.project.templates.append(self.template)
        self.canvas.template_id = self.template.id
        return self.template

    def _ensure_column_count(self, count: int) -> None:
        template = self._ensure_template()
        if template is None:
            return
        while len(template.fields) < count:
            template.fields.append(NodeField(name=f"字段{len(template.fields) + 1}", data_type="文本"))
        if self.canvas is None:
            return
        template_fields = {field.id: field for field in template.fields}
        for node in self.canvas.nodes:
            node_fields = {field.id for field in node.fields}
            for field in template.fields:
                if field.id not in node_fields:
                    node.fields.append(NodeField.from_dict(template_fields[field.id].to_dict()))

    def _ensure_row_count(self, count: int) -> None:
        if self.canvas is None:
            return
        template = self._ensure_template()
        while len(self.canvas.nodes) < count:
            if template is not None:
                node = template.create_node(0, 0)
            else:
                node = Node(title=f"数据{len(self.canvas.nodes) + 1}")
            node.node_type = "普通"
            node.template_locked = True
            if template is not None:
                node.template_id = template.id
            self.canvas.add_node(node)

    def _write_cell_value(self, row: int, column: int, value: str) -> None:
        if self.canvas is None or not (0 <= row < len(self._row_node_ids) and 0 <= column < len(self._field_ids)):
            return
        node = self.canvas.find_node(self._row_node_ids[row])
        if node is None:
            return
        field_id = self._field_ids[column]
        field = next((candidate for candidate in node.fields if candidate.id == field_id), None)
        if field is None:
            template_field = next((candidate for candidate in (self.template.fields if self.template else []) if candidate.id == field_id), None)
            if template_field is None:
                return
            field = NodeField.from_dict(template_field.to_dict())
            node.fields.append(field)
        if field.data_type == "图片":
            field.image_path = value
        else:
            field.value = value
        if node.title_field_id == field.id:
            node.title = value.strip() or field.name.strip() or (self.template.name if self.template else "数据项")
