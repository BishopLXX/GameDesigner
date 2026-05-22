from __future__ import annotations

import struct
import sys
from pathlib import Path

from PySide6.QtCore import QBuffer, QIODevice, Qt
from PySide6.QtGui import QImage


ICON_SIZES = (16, 24, 32, 48, 64, 128, 256)


def main() -> int:
    if len(sys.argv) != 3:
        print("Usage: py -3.13 source/make_icon.py <input.png> <output.ico>")
        return 2

    source = Path(sys.argv[1])
    target = Path(sys.argv[2])
    image = QImage(str(source))
    if image.isNull():
        print(f"Cannot read icon source: {source}")
        return 1

    images = [_scaled_png(image, size) for size in ICON_SIZES]
    header_size = 6 + 16 * len(images)
    offset = header_size
    entries = []
    for size, payload in zip(ICON_SIZES, images, strict=True):
        width_byte = 0 if size == 256 else size
        entries.append(
            struct.pack(
                "<BBBBHHII",
                width_byte,
                width_byte,
                0,
                0,
                1,
                32,
                len(payload),
                offset,
            )
        )
        offset += len(payload)

    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open("wb") as file:
        file.write(struct.pack("<HHH", 0, 1, len(images)))
        for entry in entries:
            file.write(entry)
        for payload in images:
            file.write(payload)
    return 0


def _scaled_png(image: QImage, size: int) -> bytes:
    scaled = image.scaled(size, size, Qt.KeepAspectRatio, Qt.SmoothTransformation)
    canvas = QImage(size, size, QImage.Format_ARGB32)
    canvas.fill(Qt.transparent)
    x = (size - scaled.width()) // 2
    y = (size - scaled.height()) // 2
    for row in range(scaled.height()):
        for column in range(scaled.width()):
            canvas.setPixelColor(x + column, y + row, scaled.pixelColor(column, row))

    buffer = QBuffer()
    buffer.open(QIODevice.WriteOnly)
    canvas.save(buffer, "PNG")
    return bytes(buffer.data())


if __name__ == "__main__":
    raise SystemExit(main())
