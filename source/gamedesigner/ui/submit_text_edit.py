from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QPlainTextEdit


class SubmitPlainTextEdit(QPlainTextEdit):
    submitted = Signal()

    def keyPressEvent(self, event) -> None:  # type: ignore[override]
        if event.key() in (Qt.Key_Return, Qt.Key_Enter):
            if event.modifiers() & Qt.ShiftModifier:
                super().keyPressEvent(event)
                return
            self.submitted.emit()
            event.accept()
            return
        super().keyPressEvent(event)
