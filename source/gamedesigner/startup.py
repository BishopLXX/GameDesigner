from __future__ import annotations

import sys

from PySide6.QtWidgets import QApplication, QWidget

from .ai_tools import configure_ai_runtime_environment
from .startup_splash import StartupSplash, build_startup_splash

try:
    import pyi_splash  # type: ignore[import-not-found]
except Exception:
    pyi_splash = None


def _close_boot_splash() -> None:
    if not getattr(sys, "frozen", False):
        return
    if pyi_splash is None:
        return

    try:
        is_alive = getattr(pyi_splash, "is_alive", None)
        if callable(is_alive) and not is_alive():
            return
        close = getattr(pyi_splash, "close", None)
        if callable(close):
            close()
    except Exception:
        return


def _update_boot_splash_text(message: str) -> None:
    if pyi_splash is None:
        return

    try:
        is_alive = getattr(pyi_splash, "is_alive", None)
        if callable(is_alive) and not is_alive():
            return
        update_text = getattr(pyi_splash, "update_text", None)
        if callable(update_text):
            update_text(message)
    except Exception:
        return


def _show_runtime_splash(app: QApplication) -> StartupSplash:
    _update_boot_splash_text("初始化界面...")
    splash = build_startup_splash()
    splash.set_progress(0, "准备启动...")
    splash.show()
    splash.raise_()
    app.processEvents()
    _close_boot_splash()
    return splash


def _advance_runtime_splash(app: QApplication, splash: StartupSplash, progress: int, message: str) -> None:
    splash.set_progress(progress, message)
    app.processEvents()


def _finish_runtime_splash(app: QApplication, splash: StartupSplash, window: QWidget) -> None:
    splash.set_progress(100, "界面准备完成")
    app.processEvents()
    splash.finish(window)
    splash.close()
    splash.deleteLater()
    app.processEvents()


def main() -> int:
    configure_ai_runtime_environment()
    app = QApplication(sys.argv)
    splash = _show_runtime_splash(app)

    _advance_runtime_splash(app, splash, 12, "加载 Qt 中文资源...")
    from .qt_i18n import install_qt_translations
    install_qt_translations(app)

    _advance_runtime_splash(app, splash, 24, "导入主窗口模块...")
    from .app import GameDesignerApp

    _advance_runtime_splash(app, splash, 36, "初始化主界面...")
    window = GameDesignerApp(
        startup_progress=lambda progress, message: _advance_runtime_splash(app, splash, progress, message)
    )
    window.show()
    _advance_runtime_splash(app, splash, 96, "显示工作区...")
    _finish_runtime_splash(app, splash, window)
    return app.exec()
