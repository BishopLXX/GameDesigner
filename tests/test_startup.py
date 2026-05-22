import os
import subprocess
import sys
import unittest
from unittest import mock
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SOURCE_DIR = PROJECT_ROOT / "source"
if str(SOURCE_DIR) not in sys.path:
    sys.path.insert(0, str(SOURCE_DIR))


class StartupTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def test_main_entrypoint_does_not_eagerly_import_app(self) -> None:
        env = os.environ.copy()
        existing = env.get("PYTHONPATH", "")
        env["PYTHONPATH"] = str(SOURCE_DIR) if not existing else os.pathsep.join([str(SOURCE_DIR), existing])
        command = [
            sys.executable,
            "-c",
            "import sys; import main; print('gamedesigner.app' in sys.modules)",
        ]

        result = subprocess.run(
            command,
            cwd=PROJECT_ROOT,
            env=env,
            capture_output=True,
            text=True,
            check=True,
        )

        self.assertEqual(result.stdout.strip(), "False")

    def test_close_boot_splash_closes_alive_pyinstaller_splash(self) -> None:
        from gamedesigner import startup

        splash = mock.Mock()
        splash.is_alive.return_value = True

        with mock.patch.object(startup.sys, "frozen", True, create=True):
            with mock.patch.object(startup, "pyi_splash", splash):
                startup._close_boot_splash()

        splash.close.assert_called_once_with()

    def test_close_boot_splash_skips_dead_pyinstaller_splash(self) -> None:
        from gamedesigner import startup

        splash = mock.Mock()
        splash.is_alive.return_value = False

        with mock.patch.object(startup.sys, "frozen", True, create=True):
            with mock.patch.object(startup, "pyi_splash", splash):
                startup._close_boot_splash()

        splash.close.assert_not_called()

    def test_importing_app_does_not_eagerly_import_dialog_module(self) -> None:
        env = os.environ.copy()
        existing = env.get("PYTHONPATH", "")
        env["PYTHONPATH"] = str(SOURCE_DIR) if not existing else os.pathsep.join([str(SOURCE_DIR), existing])
        command = [
            sys.executable,
            "-c",
            "import sys; import gamedesigner.app; print('gamedesigner.qt_dialogs' in sys.modules)",
        ]

        result = subprocess.run(
            command,
            cwd=PROJECT_ROOT,
            env=env,
            capture_output=True,
            text=True,
            check=True,
        )

        self.assertEqual(result.stdout.strip(), "False")

    def test_show_runtime_splash_starts_at_zero_progress(self) -> None:
        from gamedesigner import startup

        app = mock.Mock()
        splash = mock.Mock()

        with mock.patch.object(startup, "build_startup_splash", return_value=splash):
            with mock.patch.object(startup, "_close_boot_splash") as close_boot:
                startup._show_runtime_splash(app)

        splash.set_progress.assert_called_once_with(0, "准备启动...")
        splash.show.assert_called_once_with()
        splash.raise_.assert_called_once_with()
        app.processEvents.assert_called()
        close_boot.assert_called_once_with()

    def test_finish_runtime_splash_closes_widget_after_finish(self) -> None:
        from gamedesigner import startup

        app = mock.Mock()
        splash = mock.Mock()
        window = mock.Mock()

        startup._finish_runtime_splash(app, splash, window)

        splash.set_progress.assert_called_once_with(100, "界面准备完成")
        splash.finish.assert_called_once_with(window)
        splash.close.assert_called_once_with()
        splash.deleteLater.assert_called_once_with()
        self.assertGreaterEqual(app.processEvents.call_count, 2)

    def test_runtime_splash_progress_clamps_into_valid_range(self) -> None:
        from gamedesigner.startup_splash import build_startup_splash

        splash = build_startup_splash()
        splash.set_progress(135, "收尾中")

        self.assertEqual(splash.progress, 100)
        self.assertEqual(splash.message, "收尾中")
        splash.close()


if __name__ == "__main__":
    unittest.main()
