from __future__ import annotations

import sys
from pathlib import Path

from PySide6.QtCore import QLibraryInfo, QLocale, QTranslator
from PySide6.QtWidgets import QApplication


QT_TRANSLATION_FILES = ("qtbase_zh_CN.qm", "qt_zh_CN.qm")


def install_qt_translations(app: QApplication) -> None:
    QLocale.setDefault(QLocale(QLocale.Chinese, QLocale.China))
    translators: list[QTranslator] = []
    for folder in _translation_folders():
        for filename in QT_TRANSLATION_FILES:
            path = folder / filename
            if not path.exists():
                continue
            translator = QTranslator(app)
            if translator.load(str(path)):
                app.installTranslator(translator)
                translators.append(translator)
    app._gamedesigner_translators = translators  # type: ignore[attr-defined]


def _translation_folders() -> list[Path]:
    folders = [Path(QLibraryInfo.path(QLibraryInfo.LibraryPath.TranslationsPath))]
    bundle_root = getattr(sys, "_MEIPASS", "")
    if bundle_root:
        folders.append(Path(bundle_root) / "PySide6" / "translations")
        folders.append(Path(bundle_root) / "translations")
    folders.append(Path(sys.executable).resolve().parent / "PySide6" / "translations")

    unique: list[Path] = []
    seen: set[Path] = set()
    for folder in folders:
        resolved = folder.resolve() if folder.exists() else folder
        if resolved in seen:
            continue
        seen.add(resolved)
        unique.append(folder)
    return unique
