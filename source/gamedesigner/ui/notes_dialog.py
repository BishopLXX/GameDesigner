from __future__ import annotations

from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QPushButton,
    QPlainTextEdit,
    QSplitter,
    QVBoxLayout,
    QWidget,
)

from ..models import DesignNote


class NotesDialog(QDialog):
    def __init__(self, parent: QWidget | None, title: str, notes: list[DesignNote]) -> None:
        super().__init__(parent)
        self.setWindowTitle(title)
        self.setModal(True)
        self.result_notes: list[DesignNote] | None = None
        self._notes = [DesignNote.from_dict(note.to_dict()) for note in notes]
        self._updating = False

        self.list_widget = QListWidget(self)
        self.list_widget.currentRowChanged.connect(self._load_note)

        add_button = QPushButton("新增", self)
        delete_button = QPushButton("删除", self)
        add_button.clicked.connect(self._add_note)
        delete_button.clicked.connect(self._delete_current_note)

        list_buttons = QHBoxLayout()
        list_buttons.setContentsMargins(0, 0, 0, 0)
        list_buttons.setSpacing(8)
        list_buttons.addWidget(add_button)
        list_buttons.addWidget(delete_button)

        list_panel = QWidget(self)
        list_layout = QVBoxLayout(list_panel)
        list_layout.setContentsMargins(0, 0, 0, 0)
        list_layout.setSpacing(8)
        list_layout.addWidget(self.list_widget, 1)
        list_layout.addLayout(list_buttons)

        self.title_edit = QLineEdit(self)
        self.title_edit.textChanged.connect(self._update_current_note)
        self.content_edit = QPlainTextEdit(self)
        self.content_edit.textChanged.connect(self._update_current_note)

        editor_panel = QWidget(self)
        editor_layout = QVBoxLayout(editor_panel)
        editor_layout.setContentsMargins(0, 0, 0, 0)
        editor_layout.setSpacing(8)
        editor_layout.addWidget(QLabel("标题", self))
        editor_layout.addWidget(self.title_edit)
        editor_layout.addWidget(QLabel("内容", self))
        editor_layout.addWidget(self.content_edit, 1)

        splitter = QSplitter(self)
        splitter.addWidget(list_panel)
        splitter.addWidget(editor_panel)
        splitter.setStretchFactor(0, 0)
        splitter.setStretchFactor(1, 1)
        splitter.setSizes([180, 500])

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel, self)
        buttons.button(QDialogButtonBox.Ok).setText("保存")
        buttons.button(QDialogButtonBox.Cancel).setText("取消")
        buttons.accepted.connect(self._accept)
        buttons.rejected.connect(self.reject)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(18, 18, 18, 16)
        layout.setSpacing(14)
        layout.addWidget(splitter, 1)
        layout.addWidget(buttons)

        self.resize(760, 480)
        if not self._notes:
            self._notes.append(DesignNote(title="", content=""))
        self._refresh_list(0)

    def _refresh_list(self, selected_row: int = 0) -> None:
        self._updating = True
        try:
            self.list_widget.clear()
            for note in self._notes:
                self.list_widget.addItem(note.display_title())
            if self._notes:
                self.list_widget.setCurrentRow(max(0, min(selected_row, len(self._notes) - 1)))
            else:
                self.title_edit.clear()
                self.content_edit.clear()
        finally:
            self._updating = False
        self._load_note(self.list_widget.currentRow())

    def _load_note(self, row: int) -> None:
        if self._updating:
            return
        self._updating = True
        try:
            note = self._note_at(row)
            self.title_edit.setEnabled(note is not None)
            self.content_edit.setEnabled(note is not None)
            self.title_edit.setText(note.title if note else "")
            self.content_edit.setPlainText(note.content if note else "")
        finally:
            self._updating = False

    def _update_current_note(self) -> None:
        if self._updating:
            return
        row = self.list_widget.currentRow()
        note = self._note_at(row)
        if note is None:
            return
        note.title = self.title_edit.text()
        note.content = self.content_edit.toPlainText()
        item = self.list_widget.item(row)
        if item is not None:
            item.setText(note.display_title())

    def _add_note(self) -> None:
        self._notes.append(DesignNote(title=f"便签 {len(self._notes) + 1}", content=""))
        self._refresh_list(len(self._notes) - 1)
        self.title_edit.selectAll()
        self.title_edit.setFocus()

    def _delete_current_note(self) -> None:
        row = self.list_widget.currentRow()
        if not (0 <= row < len(self._notes)):
            return
        del self._notes[row]
        if not self._notes:
            self._notes.append(DesignNote(title="", content=""))
        self._refresh_list(min(row, len(self._notes) - 1))

    def _accept(self) -> None:
        self._update_current_note()
        self.result_notes = [note for note in self._notes if not note.is_empty()]
        self.accept()

    def _note_at(self, row: int) -> DesignNote | None:
        if 0 <= row < len(self._notes):
            return self._notes[row]
        return None
