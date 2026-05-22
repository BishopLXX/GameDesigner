from __future__ import annotations

from pathlib import Path

from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from ..project_files.linked_documents import delete_link_document, read_link_document, write_link_document


class LinkDocumentDialog(QDialog):
    def __init__(
        self,
        parent: QWidget | None,
        project_path: str | Path,
        relative_path: str,
        title: str,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle(f"编辑超链接 - {title}")
        self.setModal(True)
        self.project_path = Path(project_path)
        self.relative_path = relative_path
        self.deleted = False
        self.saved = False

        self.editor = QPlainTextEdit()
        self.editor.setPlainText(read_link_document(self.project_path, self.relative_path))

        path_label = QLabel(relative_path)
        path_label.setObjectName("mutedLabel")

        save_button = QPushButton("保存")
        save_button.clicked.connect(self._save)
        delete_button = QPushButton("删除文件")
        delete_button.clicked.connect(self._delete)
        close_buttons = QDialogButtonBox(QDialogButtonBox.Close)
        close_buttons.button(QDialogButtonBox.Close).setText("关闭")
        close_buttons.rejected.connect(self.reject)

        tools = QHBoxLayout()
        tools.addWidget(save_button)
        tools.addWidget(delete_button)
        tools.addStretch(1)
        tools.addWidget(close_buttons)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 14)
        layout.setSpacing(10)
        layout.addWidget(path_label)
        layout.addWidget(self.editor, 1)
        layout.addLayout(tools)
        self.resize(760, 560)

    def _save(self) -> None:
        write_link_document(self.project_path, self.relative_path, self.editor.toPlainText())
        self.saved = True

    def _delete(self) -> None:
        answer = QMessageBox.question(self, "删除超链接文件", "确定删除这个文件和对应节点吗？")
        if answer != QMessageBox.Yes:
            return
        delete_link_document(self.project_path, self.relative_path)
        self.deleted = True
        self.accept()
