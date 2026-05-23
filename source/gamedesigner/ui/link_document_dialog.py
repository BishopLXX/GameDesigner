from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QSplitter,
    QTextBrowser,
    QVBoxLayout,
    QWidget,
)

from ..project_files.linked_documents import (
    create_link_document,
    delete_link_document,
    import_link_document,
    read_link_document,
    write_link_document,
)
from ..window_layouts import restore_window_layout, save_window_layout


class LinkDocumentDialog(QDialog):
    def __init__(
        self,
        parent: QWidget | None,
        project_path: str | Path,
        relative_path: str,
        title: str,
        file_format: str = "md",
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle(f"编辑超文本 - {title}")
        self.setModal(True)
        self.project_path = Path(project_path)
        self.relative_path = relative_path
        self.title = title.strip() or "新文档"
        self.file_format = self._normalized_format(file_format)
        self.pending_external_path: Path | None = None
        self.deleted = False
        self.saved = False

        initial_content = read_link_document(self.project_path, self.relative_path)
        if not initial_content and not self.relative_path:
            initial_content = self._default_content()

        self.editor = QPlainTextEdit()
        self.editor.setPlainText(initial_content)
        self.editor.textChanged.connect(self._update_preview)

        self.preview = QTextBrowser()
        self.preview.setOpenExternalLinks(True)
        self.preview.setReadOnly(True)

        self.path_label = QLabel()
        self.path_label.setObjectName("mutedLabel")

        splitter = QSplitter(Qt.Horizontal)
        splitter.addWidget(self.editor)
        splitter.addWidget(self.preview)
        splitter.setSizes([440, 320])

        save_button = QPushButton("保存")
        save_button.clicked.connect(self._save)
        import_button = QPushButton("导入文档")
        import_button.clicked.connect(self._import_document)
        self.delete_button = QPushButton("删除节点")
        self.delete_button.clicked.connect(self._delete)
        close_buttons = QDialogButtonBox(QDialogButtonBox.Close)
        close_buttons.button(QDialogButtonBox.Close).setText("关闭")
        close_buttons.rejected.connect(self.reject)

        tools = QHBoxLayout()
        tools.addWidget(save_button)
        tools.addWidget(import_button)
        tools.addWidget(self.delete_button)
        tools.addStretch(1)
        tools.addWidget(close_buttons)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 14)
        layout.setSpacing(10)
        layout.addWidget(self.path_label)
        layout.addWidget(splitter, 1)
        layout.addLayout(tools)
        self.resize(920, 600)
        restore_window_layout(self, "link_document_dialog")
        self._update_path_label()
        self._update_preview()

    def _default_content(self) -> str:
        if self.file_format == "md":
            return f"# {self.title}\n"
        return ""

    def _normalized_format(self, file_format: str) -> str:
        return "txt" if file_format.lower().lstrip(".") == "txt" else "md"

    def _current_format(self) -> str:
        if self.pending_external_path is not None:
            return self._normalized_format(self.pending_external_path.suffix.lstrip("."))
        if self.relative_path:
            return self._normalized_format(Path(self.relative_path).suffix.lstrip("."))
        return self.file_format

    def _update_path_label(self) -> None:
        if self.pending_external_path is not None:
            self.path_label.setText(f"待导入：{self.pending_external_path}")
            return
        if self.relative_path:
            self.path_label.setText(self.relative_path)
            return
        self.path_label.setText("未创建文档，保存后生成工程内文件")

    def _update_preview(self) -> None:
        content = self.editor.toPlainText()
        current_format = self._current_format()
        if current_format == "md":
            self.preview.setMarkdown(content)
            return
        self.preview.setPlainText(content)

    def _import_document(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self,
            "导入文档",
            str(self.project_path.parent),
            "文档 (*.md *.txt);;所有文件 (*.*)",
        )
        if not path:
            return
        try:
            self._stage_external_document(Path(path))
        except OSError as exc:
            QMessageBox.warning(self, "导入失败", f"无法读取文件：\n{exc}")

    def _stage_external_document(self, source: Path) -> None:
        content = source.read_text(encoding="utf-8")
        self.pending_external_path = source
        self.file_format = self._normalized_format(source.suffix.lstrip("."))
        self.editor.setPlainText(content)
        self._update_path_label()
        self._update_preview()

    def _import_title(self) -> str:
        if self.title and self.title != "新文档":
            return self.title
        if self.pending_external_path is not None:
            return self.pending_external_path.stem
        return self.title

    def _save(self) -> None:
        if self.pending_external_path is not None:
            self.relative_path = import_link_document(
                self.project_path,
                self.pending_external_path,
                self._import_title(),
            )
            self.pending_external_path = None
        elif not self.relative_path:
            self.relative_path = create_link_document(self.project_path, self.title, self.file_format)
        self.file_format = self._current_format()
        write_link_document(self.project_path, self.relative_path, self.editor.toPlainText())
        self.saved = True
        self._update_path_label()
        self._update_preview()

    def _delete(self) -> None:
        if not self.relative_path and self.pending_external_path is None:
            answer = QMessageBox.question(self, "删除超文本节点", "确定删除这个节点吗？")
            if answer != QMessageBox.Yes:
                return
            self.deleted = True
            self.accept()
            return
        answer = QMessageBox.question(self, "删除超文本文件", "确定删除这个文件和对应节点吗？")
        if answer != QMessageBox.Yes:
            return
        if self.relative_path:
            delete_link_document(self.project_path, self.relative_path)
        self.deleted = True
        self.accept()

    def done(self, result: int) -> None:  # type: ignore[override]
        save_window_layout(self, "link_document_dialog")
        super().done(result)
