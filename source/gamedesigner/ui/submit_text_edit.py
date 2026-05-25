from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QImage, QPixmap
from PySide6.QtWidgets import QPlainTextEdit


class SubmitPlainTextEdit(QPlainTextEdit):
    submitted = Signal()
    imagePasted = Signal(QImage)

    def keyPressEvent(self, event) -> None:  # type: ignore[override]
        if event.key() in (Qt.Key_Return, Qt.Key_Enter):
            if event.modifiers() & Qt.ShiftModifier:
                super().keyPressEvent(event)
                return
            self.submitted.emit()
            event.accept()
            return
        super().keyPressEvent(event)

    def insertFromMimeData(self, source) -> None:  # type: ignore[override]
        if source.hasImage():
            image_data = source.imageData()
            image = self._image_from_mime_data(image_data)
            if not image.isNull():
                self.imagePasted.emit(image)
                return
        super().insertFromMimeData(source)

    def _image_from_mime_data(self, image_data) -> QImage:
        if isinstance(image_data, QImage):
            return image_data
        if isinstance(image_data, QPixmap):
            return image_data.toImage()
        return QImage()
