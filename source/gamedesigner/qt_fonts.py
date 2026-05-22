from __future__ import annotations

from pathlib import Path

from PySide6.QtGui import QFont, QFontDatabase
from PySide6.QtWidgets import QApplication


FONT_FILES = [
    Path("C:/Windows/Fonts/NotoSansSC-VF.ttf"),
    Path("C:/Windows/Fonts/msyh.ttc"),
    Path("C:/Windows/Fonts/simhei.ttf"),
]


def configure_fonts() -> str:
    family = ""
    for font_path in FONT_FILES:
        if not font_path.exists():
            continue
        font_id = QFontDatabase.addApplicationFont(str(font_path))
        families = QFontDatabase.applicationFontFamilies(font_id)
        if families and not family:
            family = families[0]
    if not family:
        for candidate in ("Noto Sans SC", "Microsoft YaHei UI", "Microsoft YaHei", "SimHei", "Segoe UI"):
            if candidate in QFontDatabase.families():
                family = candidate
                break
    family = family or "Segoe UI"
    app = QApplication.instance()
    if app is not None:
        app.setFont(QFont(family, 10))
    return family
