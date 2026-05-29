from __future__ import annotations

import argparse
import contextlib
import io
import json
import sys
import tempfile
import urllib.error
import urllib.request
import uuid
from pathlib import Path
from typing import Any

from PySide6.QtCore import QProcess, QProcessEnvironment, QTimer, Qt, QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (
    QApplication,
    QComboBox,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QPlainTextEdit,
    QSpinBox,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from gamedesigner.paths import game_designer_data_root, pixel_refiner_model_dir
from pixel_refiner_service.manifest import DEFAULT_MODEL_ID


V2_MODEL_ID = "pixel-refiner-v2"
DEFAULT_CONSOLE_MODEL_ID = V2_MODEL_ID
PROJECT_ROOT = Path(__file__).resolve().parent.parent
SOURCE_DIR = Path(__file__).resolve().parent
SERVICE_MAIN = SOURCE_DIR / "pixel_refiner_service_main.py"
TRAINING_MAIN = SOURCE_DIR / "pixel_refiner_training_main.py"
DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8765
DATASET_VERSION = "character_large_v1"
RUN_SERVICE_ARG = "--run-service"
RUN_TRAINING_CLI_ARG = "--run-training-cli"
RUN_OUTPUT_FILE_ARG = "--run-output-file"


def default_dataset_dir() -> Path:
    return game_designer_data_root() / "pixel_refiner" / DATASET_VERSION


def default_training_python() -> Path:
    candidate = game_designer_data_root() / "venvs" / "pixel-refiner-train" / "Scripts" / "python.exe"
    return candidate if candidate.exists() else Path(sys.executable)


class PixelRefinerServiceWindow(QMainWindow):
    def __init__(self, *, model_dir: Path, host: str, port: int, auto_start: bool) -> None:
        super().__init__()
        self.setWindowTitle("Pixel Refiner 控制台")
        self.resize(980, 720)

        self.service_process = QProcess(self)
        self.service_process.readyReadStandardOutput.connect(self._read_service_stdout)
        self.service_process.readyReadStandardError.connect(self._read_service_stderr)
        self.service_process.finished.connect(self._service_finished)

        self.training_process = QProcess(self)
        self.training_process.readyReadStandardOutput.connect(self._read_training_stdout)
        self.training_process.readyReadStandardError.connect(self._read_training_stderr)
        self.training_process.finished.connect(self._training_finished)

        self.last_output_path = ""
        self.attached_external = False

        self._build_widgets(model_dir=model_dir, host=host, port=port)
        self._build_layout()
        self._apply_style()

        self.timer = QTimer(self)
        self.timer.setInterval(1000)
        self.timer.timeout.connect(self.refresh_status)
        self.timer.start()

        QTimer.singleShot(100, self.refresh_status)
        QTimer.singleShot(150, self.refresh_dataset_summary)
        if auto_start:
            QTimer.singleShot(300, self.start_or_attach)

    def _build_widgets(self, *, model_dir: Path, host: str, port: int) -> None:
        self.host_edit = QLineEdit(host)
        self.host_edit.setMaximumWidth(180)
        self.port_spin = QSpinBox()
        self.port_spin.setRange(1, 65535)
        self.port_spin.setValue(port)
        self.model_dir_edit = QLineEdit(str(model_dir))
        self.model_id_combo = QComboBox()
        self.model_id_combo.addItem("Pixel Refiner V2（U-Net/NAF + 像素约束）", V2_MODEL_ID)
        self.model_id_combo.addItem("Pixel Refiner V1（基础 CNN）", DEFAULT_MODEL_ID)
        if model_dir.name == DEFAULT_MODEL_ID:
            self.model_id_combo.setCurrentIndex(1)

        self.status_label = QLabel("未连接")
        self.model_label = QLabel("-")
        self.request_count_label = QLabel("0")
        self.last_request_label = QLabel("-")
        self.last_input_label = QLabel("-")
        self.last_output_label = QLabel("-")
        self.last_error_label = QLabel("-")
        self.duration_label = QLabel("-")

        self.start_button = QPushButton("运行/连接服务")
        self.stop_button = QPushButton("关闭服务")
        self.refresh_button = QPushButton("刷新")
        self.open_output_button = QPushButton("打开最后输出")
        self.open_model_button = QPushButton("打开模型包")
        self.choose_model_button = QPushButton("选择")
        self.service_log_edit = QPlainTextEdit()
        self.service_log_edit.setReadOnly(True)
        self.service_log_edit.setMaximumBlockCount(2000)

        self.start_button.clicked.connect(self.start_or_attach)
        self.stop_button.clicked.connect(self.stop_service)
        self.refresh_button.clicked.connect(self.refresh_status)
        self.open_output_button.clicked.connect(self.open_last_output)
        self.open_model_button.clicked.connect(lambda: self.open_folder(Path(self.model_dir_edit.text().strip())))
        self.choose_model_button.clicked.connect(self.choose_model_dir)

        self.dataset_dir_edit = QLineEdit(str(default_dataset_dir()))
        self.choose_dataset_button = QPushButton("选择")
        self.open_dataset_button = QPushButton("打开数据集")
        self.open_targets_button = QPushButton("targets")
        self.open_pairs_button = QPushButton("pairs")
        self.open_generated_button = QPushButton("generated_inputs")
        self.refresh_dataset_button = QPushButton("刷新统计")
        self.evaluate_dataset_button = QPushButton("完整评估")
        self.dataset_summary_edit = QPlainTextEdit()
        self.dataset_summary_edit.setReadOnly(True)
        self.dataset_summary_edit.setMaximumBlockCount(2000)

        self.choose_dataset_button.clicked.connect(self.choose_dataset_dir)
        self.open_dataset_button.clicked.connect(lambda: self.open_folder(Path(self.dataset_dir_edit.text().strip())))
        self.open_targets_button.clicked.connect(lambda: self.open_folder(Path(self.dataset_dir_edit.text().strip()) / "targets"))
        self.open_pairs_button.clicked.connect(lambda: self.open_folder(Path(self.dataset_dir_edit.text().strip()) / "pairs"))
        self.open_generated_button.clicked.connect(lambda: self.open_folder(Path(self.dataset_dir_edit.text().strip()) / "generated_inputs"))
        self.refresh_dataset_button.clicked.connect(self.refresh_dataset_summary)
        self.evaluate_dataset_button.clicked.connect(self.evaluate_dataset)

        self.train_python_edit = QLineEdit(str(default_training_python()))
        self.choose_train_python_button = QPushButton("选择")
        self.train_output_dir_edit = QLineEdit(str(model_dir))
        self.choose_train_output_button = QPushButton("选择")
        self.epochs_spin = QSpinBox()
        self.epochs_spin.setRange(1, 1000)
        self.epochs_spin.setValue(4)
        self.steps_spin = QSpinBox()
        self.steps_spin.setRange(1, 1000000)
        self.steps_spin.setValue(900)
        self.batch_spin = QSpinBox()
        self.batch_spin.setRange(1, 128)
        self.batch_spin.setValue(4)
        self.patch_spin = QSpinBox()
        self.patch_spin.setRange(16, 1024)
        self.patch_spin.setValue(256)
        self.val_batches_spin = QSpinBox()
        self.val_batches_spin.setRange(1, 4096)
        self.val_batches_spin.setValue(32)
        self.limit_spin = QSpinBox()
        self.limit_spin.setRange(0, 1000000)
        self.limit_spin.setValue(0)
        self.device_combo = QComboBox()
        self.device_combo.addItem("显卡 CUDA（推荐）", "cuda")
        self.device_combo.addItem("自动选择", "auto")
        self.device_combo.addItem("CPU（很慢）", "cpu")
        self.training_param_help_edit = QPlainTextEdit()
        self.training_param_help_edit.setReadOnly(True)
        self.training_param_help_edit.setMaximumHeight(190)
        self.training_param_help_edit.setPlainText(TRAINING_PARAMETER_HELP)

        self.smoke_train_button = QPushButton("小训练 smoke")
        self.train_button = QPushButton("训练当前模型")
        self.retrain_button = QPushButton("Retrain / 覆盖重训")
        self.stop_training_button = QPushButton("停止训练")
        self.open_train_output_button = QPushButton("打开训练输出")
        self.training_log_edit = QPlainTextEdit()
        self.training_log_edit.setReadOnly(True)
        self.training_log_edit.setMaximumBlockCount(5000)

        self.choose_train_python_button.clicked.connect(self.choose_training_python)
        self.choose_train_output_button.clicked.connect(self.choose_training_output_dir)
        self.smoke_train_button.clicked.connect(self.start_smoke_training)
        self.train_button.clicked.connect(lambda: self.start_training(retrain=False))
        self.retrain_button.clicked.connect(lambda: self.start_training(retrain=True))
        self.stop_training_button.clicked.connect(self.stop_training)
        self.open_train_output_button.clicked.connect(lambda: self.open_folder(Path(self.train_output_dir_edit.text().strip())))

        self.help_edit = QPlainTextEdit()
        self.help_edit.setReadOnly(True)
        self.help_edit.setPlainText(HELP_TEXT)

    def _build_layout(self) -> None:
        tabs = QTabWidget(self)
        tabs.addTab(self._build_service_tab(), "服务")
        tabs.addTab(self._build_dataset_tab(), "数据集")
        tabs.addTab(self._build_training_tab(), "训练 / Retrain")
        tabs.addTab(self._build_help_tab(), "Help")
        self.setCentralWidget(tabs)

    def _build_service_tab(self) -> QWidget:
        tab = QWidget(self)
        layout = QVBoxLayout(tab)
        layout.setContentsMargins(18, 18, 18, 18)
        layout.setSpacing(12)

        service_row = QHBoxLayout()
        service_row.addWidget(QLabel("Host"))
        service_row.addWidget(self.host_edit)
        service_row.addWidget(QLabel("Port"))
        service_row.addWidget(self.port_spin)
        service_row.addStretch(1)
        service_row.addWidget(self.start_button)
        service_row.addWidget(self.stop_button)
        service_row.addWidget(self.refresh_button)
        layout.addLayout(service_row)

        model_row = QHBoxLayout()
        model_row.addWidget(QLabel("模型包"))
        model_row.addWidget(self.model_dir_edit, 1)
        model_row.addWidget(self.choose_model_button)
        model_row.addWidget(self.open_model_button)
        layout.addLayout(model_row)

        model_id_row = QHBoxLayout()
        model_id_row.addWidget(QLabel("模型 ID"))
        model_id_row.addWidget(self.model_id_combo, 1)
        layout.addLayout(model_id_row)

        form = QFormLayout()
        form.setLabelAlignment(Qt.AlignRight)
        form.addRow("状态", self.status_label)
        form.addRow("模型", self.model_label)
        form.addRow("已处理请求", self.request_count_label)
        form.addRow("最后请求时间", self.last_request_label)
        form.addRow("最后输入", self.last_input_label)
        form.addRow("最后输出", self.last_output_label)
        form.addRow("最后耗时", self.duration_label)
        form.addRow("最后错误", self.last_error_label)
        layout.addLayout(form)

        output_row = QHBoxLayout()
        output_row.addStretch(1)
        output_row.addWidget(self.open_output_button)
        layout.addLayout(output_row)

        layout.addWidget(QLabel("服务日志"))
        layout.addWidget(self.service_log_edit, 1)
        return tab

    def _build_dataset_tab(self) -> QWidget:
        tab = QWidget(self)
        layout = QVBoxLayout(tab)
        layout.setContentsMargins(18, 18, 18, 18)
        layout.setSpacing(12)

        dataset_row = QHBoxLayout()
        dataset_row.addWidget(QLabel("数据集"))
        dataset_row.addWidget(self.dataset_dir_edit, 1)
        dataset_row.addWidget(self.choose_dataset_button)
        dataset_row.addWidget(self.open_dataset_button)
        layout.addLayout(dataset_row)

        folder_row = QHBoxLayout()
        folder_row.addWidget(self.open_targets_button)
        folder_row.addWidget(self.open_pairs_button)
        folder_row.addWidget(self.open_generated_button)
        folder_row.addStretch(1)
        folder_row.addWidget(self.refresh_dataset_button)
        folder_row.addWidget(self.evaluate_dataset_button)
        layout.addLayout(folder_row)

        layout.addWidget(QLabel("数据集统计"))
        layout.addWidget(self.dataset_summary_edit, 1)
        return tab

    def _build_training_tab(self) -> QWidget:
        tab = QWidget(self)
        layout = QVBoxLayout(tab)
        layout.setContentsMargins(18, 18, 18, 18)
        layout.setSpacing(12)

        python_row = QHBoxLayout()
        python_row.addWidget(QLabel("训练 Python"))
        python_row.addWidget(self.train_python_edit, 1)
        python_row.addWidget(self.choose_train_python_button)
        layout.addLayout(python_row)

        output_row = QHBoxLayout()
        output_row.addWidget(QLabel("输出模型包"))
        output_row.addWidget(self.train_output_dir_edit, 1)
        output_row.addWidget(self.choose_train_output_button)
        output_row.addWidget(self.open_train_output_button)
        layout.addLayout(output_row)

        form = QFormLayout()
        form.setLabelAlignment(Qt.AlignRight)
        form.addRow("训练轮数", self.epochs_spin)
        form.addRow("每轮步数", self.steps_spin)
        form.addRow("批量大小", self.batch_spin)
        form.addRow("训练裁剪尺寸", self.patch_spin)
        form.addRow("验证批次数", self.val_batches_spin)
        form.addRow("样本上限（0=全量）", self.limit_spin)
        form.addRow("训练设备", self.device_combo)
        layout.addLayout(form)

        layout.addWidget(QLabel("训练参数说明"))
        layout.addWidget(self.training_param_help_edit)

        buttons = QHBoxLayout()
        buttons.addWidget(self.smoke_train_button)
        buttons.addWidget(self.train_button)
        buttons.addWidget(self.retrain_button)
        buttons.addWidget(self.stop_training_button)
        buttons.addStretch(1)
        layout.addLayout(buttons)

        layout.addWidget(QLabel("训练日志"))
        layout.addWidget(self.training_log_edit, 1)
        return tab

    def _build_help_tab(self) -> QWidget:
        tab = QWidget(self)
        layout = QVBoxLayout(tab)
        layout.setContentsMargins(18, 18, 18, 18)
        layout.addWidget(self.help_edit, 1)
        return tab

    def _apply_style(self) -> None:
        self.setStyleSheet(
            """
            QMainWindow, QWidget {
                background: #f5f5f7;
                color: #202124;
                font-size: 13px;
            }
            QTabWidget::pane {
                border: 1px solid #d8d8dd;
                border-radius: 8px;
                top: -1px;
            }
            QTabBar::tab {
                background: #ececf1;
                border: 1px solid #d8d8dd;
                border-bottom: 0;
                padding: 8px 16px;
                margin-right: 2px;
                border-top-left-radius: 7px;
                border-top-right-radius: 7px;
            }
            QTabBar::tab:selected {
                background: #ffffff;
            }
            QLineEdit, QSpinBox, QComboBox, QPlainTextEdit {
                background: #ffffff;
                border: 1px solid #d8d8dd;
                border-radius: 6px;
                padding: 6px;
            }
            QPushButton {
                background: #ffffff;
                border: 1px solid #c8c8ce;
                border-radius: 6px;
                padding: 6px 12px;
            }
            QPushButton:hover {
                background: #ececf1;
            }
            QPushButton:disabled {
                color: #8a8a91;
                background: #f0f0f2;
            }
            """
        )

    def start_or_attach(self) -> None:
        health = self._get_json("/v1/health", timeout=0.5)
        if isinstance(health, dict) and health.get("ok"):
            self.attached_external = not self._own_service_running()
            self._append_service_log("已连接到正在运行的 Pixel Refiner 服务。")
            self.refresh_status()
            return
        if self._own_service_running():
            self._append_service_log("服务进程已经由本窗口启动。")
            return

        env = self._process_env()
        env.insert("PIXEL_REFINER_VERBOSE", "1")
        self.service_process.setProcessEnvironment(env)
        self.service_process.setWorkingDirectory(str(PROJECT_ROOT))
        service_args = [
            "--host",
            self.host_edit.text().strip() or DEFAULT_HOST,
            "--port",
            str(self.port_spin.value()),
            "--model-dir",
            self.model_dir_edit.text().strip() or str(pixel_refiner_model_dir(self.current_model_id())),
            "--model-id",
            self.current_model_id(),
        ]
        program, args = _subprocess_command(SERVICE_MAIN, service_args, frozen_arg=RUN_SERVICE_ARG)
        self._append_service_log("启动服务：" + " ".join([program, *args]))
        self.service_process.start(program, args)
        if not self.service_process.waitForStarted(3000):
            self._append_service_log("服务启动失败。")
        self.attached_external = False

    def stop_service(self) -> None:
        if not self._own_service_running():
            QMessageBox.information(self, "Pixel Refiner", "当前窗口没有接管正在运行的服务。外部服务请在启动它的窗口里关闭。")
            return
        self._append_service_log("正在停止服务进程。")
        self.service_process.terminate()
        if not self.service_process.waitForFinished(3000):
            self.service_process.kill()
        self.refresh_status()

    def refresh_status(self) -> None:
        health = self._get_json("/v1/health", timeout=0.5)
        stats = self._get_json("/v1/stats", timeout=0.5)
        if isinstance(health, dict) and health.get("ok"):
            suffix = "外部服务" if self.attached_external and not self._own_service_running() else "本窗口服务"
            self.status_label.setText(f"运行中 ({suffix})")
            self.status_label.setStyleSheet("color: #188038; font-weight: 600;")
            self.model_label.setText(str(health.get("model") or "-"))
        else:
            message = ""
            if isinstance(health, dict):
                message = str(health.get("message") or "")
            self.status_label.setText(f"未运行{(': ' + message) if message else ''}")
            self.status_label.setStyleSheet("color: #b3261e; font-weight: 600;")
            self.model_label.setText("-")

        if isinstance(stats, dict) and "request_count" in stats:
            self.request_count_label.setText(str(stats.get("request_count") or 0))
            self.last_request_label.setText(str(stats.get("last_request_at") or "-"))
            self.last_input_label.setText(_compact_path(stats.get("last_input_path")))
            outputs = stats.get("last_output_paths") if isinstance(stats.get("last_output_paths"), list) else []
            self.last_output_path = str(outputs[-1]) if outputs else ""
            self.last_output_label.setText(_compact_path(self.last_output_path))
            duration = stats.get("last_duration_ms")
            self.duration_label.setText(f"{duration} ms" if duration not in {None, ""} else "-")
            self.last_error_label.setText(str(stats.get("last_error") or "-"))
        else:
            self.last_error_label.setText("stats 接口不可用" if health else "-")

    def refresh_dataset_summary(self) -> None:
        self._run_dataset_command(["summary"])

    def evaluate_dataset(self) -> None:
        self._run_dataset_command(["evaluate"])

    def start_smoke_training(self) -> None:
        model_id = self.current_model_id()
        smoke_output = pixel_refiner_model_dir(model_id).with_name(f"{model_id}-smoke")
        args = [
            "train",
            "--model-id",
            model_id,
            "--architecture",
            self.current_architecture(),
            "--output-dir",
            str(smoke_output),
            "--epochs",
            "1",
            "--steps-per-epoch",
            "10",
            "--batch-size",
            "2",
            "--patch-size",
            str(self.patch_spin.value()),
            "--limit",
            "64",
            "--device",
            str(self.device_combo.currentData() or "auto"),
            "--features",
            "64" if model_id == V2_MODEL_ID else "48",
            "--palette-levels",
            "64",
            "--pixel-constraint-weight",
            "0.08",
            "--val-batches",
            "4",
            "--log-interval",
            "1",
        ]
        self._start_training_process(args, label="小训练 smoke")

    def start_training(self, *, retrain: bool) -> None:
        if retrain:
            confirm = QMessageBox.question(
                self,
                "Retrain / 覆盖重训",
                "Retrain 会重新写入当前输出模型包里的权重和 manifest。继续吗？",
            )
            if confirm != QMessageBox.Yes:
                return
        args = [
            "train",
            "--model-id",
            self.current_model_id(),
            "--architecture",
            self.current_architecture(),
            "--output-dir",
            self.train_output_dir_edit.text().strip() or str(pixel_refiner_model_dir(self.current_model_id())),
            "--epochs",
            str(self.epochs_spin.value()),
            "--steps-per-epoch",
            str(self.steps_spin.value()),
            "--batch-size",
            str(self.batch_spin.value()),
            "--patch-size",
            str(self.patch_spin.value()),
            "--device",
            str(self.device_combo.currentData() or "auto"),
            "--features",
            "64" if self.current_model_id() == V2_MODEL_ID else "48",
            "--palette-levels",
            "64",
            "--pixel-constraint-weight",
            "0.08",
            "--val-batches",
            str(self.val_batches_spin.value()),
            "--log-interval",
            "50",
        ]
        if self.limit_spin.value() > 0:
            args.extend(["--limit", str(self.limit_spin.value())])
        self._start_training_process(args, label="Retrain" if retrain else f"训练 {self.current_model_id()}")

    def stop_training(self) -> None:
        if self.training_process.state() == QProcess.NotRunning:
            QMessageBox.information(self, "Pixel Refiner", "当前没有训练进程。")
            return
        self._append_training_log("正在停止训练进程。")
        self.training_process.terminate()
        if not self.training_process.waitForFinished(5000):
            self.training_process.kill()

    def choose_model_dir(self) -> None:
        folder = QFileDialog.getExistingDirectory(self, "选择模型包目录", self.model_dir_edit.text().strip())
        if folder:
            self.model_dir_edit.setText(folder)
            self.train_output_dir_edit.setText(folder)
            if Path(folder).name == V2_MODEL_ID:
                self.model_id_combo.setCurrentIndex(0)
            elif Path(folder).name == DEFAULT_MODEL_ID:
                self.model_id_combo.setCurrentIndex(1)

    def choose_dataset_dir(self) -> None:
        folder = QFileDialog.getExistingDirectory(self, "选择数据集目录", self.dataset_dir_edit.text().strip())
        if folder:
            self.dataset_dir_edit.setText(folder)
            self.refresh_dataset_summary()

    def choose_training_python(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "选择训练 Python", self.train_python_edit.text().strip(), "Python (python.exe);;All files (*.*)")
        if path:
            self.train_python_edit.setText(path)

    def choose_training_output_dir(self) -> None:
        folder = QFileDialog.getExistingDirectory(self, "选择训练输出模型包", self.train_output_dir_edit.text().strip())
        if folder:
            self.train_output_dir_edit.setText(folder)

    def open_last_output(self) -> None:
        if not self.last_output_path:
            QMessageBox.information(self, "Pixel Refiner", "还没有输出文件。")
            return
        path = Path(self.last_output_path)
        self.open_folder(path.parent if path.is_file() else path)

    def open_folder(self, path: Path) -> None:
        if not path:
            return
        path.mkdir(parents=True, exist_ok=True)
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(path)))

    def closeEvent(self, event) -> None:  # noqa: N802
        if self._own_service_running():
            self.service_process.terminate()
            self.service_process.waitForFinished(1000)
        if self.training_process.state() != QProcess.NotRunning:
            self.training_process.terminate()
            self.training_process.waitForFinished(1000)
        super().closeEvent(event)

    def _start_training_process(self, args: list[str], *, label: str) -> None:
        if self.training_process.state() != QProcess.NotRunning:
            QMessageBox.information(self, "Pixel Refiner", "训练进程已经在运行。")
            return
        python = Path(self.train_python_edit.text().strip())
        if not python.is_file():
            QMessageBox.warning(self, "Pixel Refiner", f"训练 Python 不存在：{python}")
            return
        env = self._process_env()
        self.training_process.setProcessEnvironment(env)
        self.training_process.setWorkingDirectory(str(PROJECT_ROOT))
        full_args = [str(TRAINING_MAIN), *args]
        self.training_log_edit.clear()
        self._append_training_log(f"{label} 启动：{' '.join([str(python), *full_args])}")
        self.training_process.start(str(python), full_args)
        if not self.training_process.waitForStarted(3000):
            self._append_training_log("训练进程启动失败。")

    def current_model_id(self) -> str:
        return str(self.model_id_combo.currentData() or DEFAULT_CONSOLE_MODEL_ID)

    def current_architecture(self) -> str:
        return "unet-naf-v2" if self.current_model_id() == V2_MODEL_ID else "cnn-v1"

    def _run_dataset_command(self, args: list[str]) -> None:
        env = self._process_env()
        process = QProcess(self)
        process.setProcessEnvironment(env)
        process.setWorkingDirectory(str(PROJECT_ROOT))
        output_file = Path(tempfile.gettempdir()) / f"pixel_refiner_console_{uuid.uuid4().hex}.txt"
        frozen_args = [RUN_OUTPUT_FILE_ARG, str(output_file), *args]
        program, process_args = _subprocess_command(TRAINING_MAIN, args, frozen_arg=RUN_TRAINING_CLI_ARG, frozen_args=frozen_args)
        process.start(program, process_args)
        if not process.waitForFinished(30000):
            process.kill()
            self.dataset_summary_edit.setPlainText("数据集命令超时。")
            return
        output = bytes(process.readAllStandardOutput()).decode("utf-8", errors="replace")
        error = bytes(process.readAllStandardError()).decode("utf-8", errors="replace")
        if output_file.is_file():
            try:
                output = output_file.read_text(encoding="utf-8")
            except OSError:
                pass
            try:
                output_file.unlink()
            except OSError:
                pass
        text = output.strip() or error.strip()
        if output.strip():
            text = _pretty_json(output.strip())
        self.dataset_summary_edit.setPlainText(text or "没有输出。")

    def _own_service_running(self) -> bool:
        return self.service_process.state() != QProcess.NotRunning

    def _read_service_stdout(self) -> None:
        text = bytes(self.service_process.readAllStandardOutput()).decode("utf-8", errors="replace")
        if text:
            self._append_service_log(text.rstrip())

    def _read_service_stderr(self) -> None:
        text = bytes(self.service_process.readAllStandardError()).decode("utf-8", errors="replace")
        if text:
            self._append_service_log(text.rstrip())

    def _read_training_stdout(self) -> None:
        text = bytes(self.training_process.readAllStandardOutput()).decode("utf-8", errors="replace")
        if text:
            self._append_training_log(text.rstrip())

    def _read_training_stderr(self) -> None:
        text = bytes(self.training_process.readAllStandardError()).decode("utf-8", errors="replace")
        if text:
            self._append_training_log(text.rstrip())

    def _service_finished(self, exit_code: int, _status: QProcess.ExitStatus) -> None:
        self._append_service_log(f"服务进程已退出，exit_code={exit_code}")
        self.refresh_status()

    def _training_finished(self, exit_code: int, _status: QProcess.ExitStatus) -> None:
        self._append_training_log(f"训练进程已退出，exit_code={exit_code}")
        if exit_code == 0:
            self._append_training_log("训练完成。模型包已写入输出目录；如果服务正在运行，请重启服务后加载新权重。")

    def _append_service_log(self, text: str) -> None:
        self.service_log_edit.appendPlainText(text)

    def _append_training_log(self, text: str) -> None:
        self.training_log_edit.appendPlainText(text)

    def _get_json(self, path: str, *, timeout: float) -> dict[str, Any] | None:
        url = f"http://{self.host_edit.text().strip() or DEFAULT_HOST}:{self.port_spin.value()}{path}"
        try:
            with urllib.request.urlopen(url, timeout=timeout) as response:
                raw = response.read()
            payload = json.loads(raw.decode("utf-8-sig"))
        except (OSError, urllib.error.URLError, UnicodeDecodeError, json.JSONDecodeError):
            return None
        return payload if isinstance(payload, dict) else None

    def _process_env(self) -> QProcessEnvironment:
        env = QProcessEnvironment.systemEnvironment()
        env.insert("PYTHONPATH", str(SOURCE_DIR))
        return env


def _compact_path(value: Any) -> str:
    text = str(value or "").strip()
    if not text:
        return "-"
    if len(text) <= 120:
        return text
    return "..." + text[-117:]


def _pretty_json(text: str) -> str:
    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        return text
    return json.dumps(payload, ensure_ascii=False, indent=2)


def _subprocess_command(
    script: Path,
    args: list[str],
    *,
    frozen_arg: str,
    frozen_args: list[str] | None = None,
) -> tuple[str, list[str]]:
    if getattr(sys, "frozen", False):
        return sys.executable, [frozen_arg, *(frozen_args if frozen_args is not None else args)]
    return sys.executable, [str(script), *args]


def _run_training_cli_from_frozen(raw_args: list[str]) -> int:
    output_file: Path | None = None
    args = list(raw_args)
    if len(args) >= 2 and args[0] == RUN_OUTPUT_FILE_ARG:
        output_file = Path(args[1])
        args = args[2:]

    try:
        from pixel_refiner_training_main import main as training_main
    except Exception as exc:
        if output_file is not None:
            output_file.parent.mkdir(parents=True, exist_ok=True)
            output_file.write_text(f"{type(exc).__name__}: {exc}", encoding="utf-8")
        return 1

    if output_file is None:
        return training_main(args)

    stdout_buffer = io.StringIO()
    stderr_buffer = io.StringIO()
    exit_code = 1
    with contextlib.redirect_stdout(stdout_buffer), contextlib.redirect_stderr(stderr_buffer):
        try:
            exit_code = training_main(args)
        except SystemExit as exc:
            exit_code = int(exc.code or 0) if isinstance(exc.code, int) else 1
        except Exception as exc:
            print(f"{type(exc).__name__}: {exc}", file=sys.stderr)
            exit_code = 1
    output_file.parent.mkdir(parents=True, exist_ok=True)
    output_file.write_text(stdout_buffer.getvalue() or stderr_buffer.getvalue(), encoding="utf-8")
    return exit_code


TRAINING_PARAMETER_HELP = """
训练轮数：完整扫训练计划几轮。轮数越多越可能学到风格，也越容易过拟合；v2 默认 4。
每轮步数：每一轮实际训练多少次参数更新。它比“图片数量”更直接决定训练时间；v2 默认 700。
批量大小：每次更新同时喂多少张裁剪 patch。越大越吃显存，梯度越稳；4070 12GB 当前 v2 推荐 4。
训练裁剪尺寸：从大图里裁出多大的训练块。128 学局部边缘和颜色，256 更能学结构但更吃显存；v2 推荐 256。
验证批次数：每轮结束拿多少批样本检查 loss，不参与训练。
样本上限：0 表示全量训练；小数值用于快速试参数。
训练设备：显卡 CUDA 用 NVIDIA GPU；自动选择会优先 CUDA；CPU 只适合排错。
Retrain：会覆盖输出模型包里的 ONNX 权重和 manifest。训练完成后必须重启服务，主程序才会用新模型。
""".strip()


HELP_TEXT = """
Pixel Refiner 是什么

Pixel Refiner 不是从零生图的大模型。它是一个本地 image-to-image 修正器：输入一张已经有角色结构的候选像素图，输出一张更接近训练集中真像素画规则的 PNG。GameDesigner 主程序只通过本机 HTTP 调用它；模型包和服务是独立的。

服务页

运行/连接服务：启动本地服务，或连接已经占用 127.0.0.1:8765 的 Pixel Refiner 服务。
关闭服务：关闭本窗口启动的服务。外部服务不会被强杀。
已处理请求：每点一次 GameDesigner 里的“AI 修正像素画”，这里应该加 1。
最后输入/输出：能看到软件刚刚把哪张图发过来，以及输出 PNG 存到了哪里。

数据集页

默认数据集目录：
D:\\GameDesignerData\\pixel_refiner\\character_large_v1

主要子目录：
targets：真像素画目标图。
generated_inputs：由程序降质或 AI 伪输入生成的训练输入。
pairs：一对一训练样本，每个 pair 里有 input.png 和 target.png。
index.jsonl：训练对索引。
licensed_sources.csv：来源记录。

刷新统计：快速看 targets、inputs、pairs 数量。
完整评估：统计尺寸、类别、input_kind、有效 pair 数。

从主程序收集真实失败样本：
在 GameDesigner 像素图结果列表里，右键一张“不够好但结构对”的候选图，选择“加入 Pixel Refiner 训练对...”，再选择匹配的真像素 PNG。软件会把坏候选保存为 input，把真像素图保存为 target，并写入 input_kind=software_candidate 的 pair。输入图和目标图必须尺寸一致，避免训练学到错误缩放。

训练页

小训练 smoke：只跑极小样本，验证训练环境、CUDA、ONNX 导出是否正常。
训练当前模型：按界面参数训练并导出当前选择的模型包，默认是 pixel-refiner-v2。
Retrain / 覆盖重训：重新写当前输出模型包的权重和 manifest。服务如果正在运行，训练完成后要重启服务，旧服务才会加载新权重。
停止训练：终止当前训练进程。

关键参数：
训练轮数：训练计划重复几轮。
每轮步数：每轮做多少次参数更新。
批量大小：每次更新同时喂多少张裁剪 patch。
训练裁剪尺寸：从大图里裁多大的训练块。128 学局部，256 更能学结构。
验证批次数：每轮结束做多少批验证。
样本上限：0 表示全量数据；非 0 用于快速实验。
训练设备：显卡 CUDA / 自动选择 / CPU。

底层原理

当前 v2 不是扩散模型，不是从一张纯色图开始一层一层加噪/去噪生成图片。它也不是 Stable Diffusion 那种“从随机噪声里采样出一张图”的流程。

当前 v2 是监督式 image-to-image 修正：
input.png -> 模型 -> refined RGB
target.png -> 监督目标

模型输入：
image：RGB，float32，NCHW，范围 0 到 1。
alpha：透明通道，float32，NCHW，范围 0 到 1。

模型输出：
RGB 或 RGBA，float32，范围 0 到 1。服务会按 alpha_mode 保留透明度。

当前 v2 是中型 U-Net/NAFNet 风格修正网络，导出成 ONNX，服务用 ONNX Runtime 跑。它直接看输入图的局部像素和 alpha，通过多尺度特征修正边缘、颜色、透明度和像素块结构；服务输出前还会按 manifest 做 palette clamp 和 alpha clamp。

当前服务还会做一层“保守像素艺术输出层”：
1. GameDesigner 右键“AI 修正像素画”时，输入就是你当前选中的像素候选图，不再偷偷改用隐藏的原始大图。
2. 即使 ONNX 模型本身没有 strength 输入，服务也会在输出端按强度混合原图和模型结果。
3. 暗轮廓、高对比边缘、alpha 边界会自动降低模型覆盖率，避免把线条洗灰。
4. 调色板优先贴回输入图的颜色体系，再做 alpha clamp，减少全局 median-cut 导致的偏色。
5. 单输出模型会扩展成多个候选：只清理、保守、强化、标准，方便你直接比较。

更符合像素画的目标流程应该是：
1. 保护角色轮廓和透明 alpha。
2. 强制硬边，减少半透明灰雾。
3. 限制调色盘数量。
4. 让阴影、亮部、轮廓线、抗锯齿都符合像素画习惯。
5. 最后做 palette clamp、alpha clamp、nearest cleanup，避免输出软渐变。

所以后续 v2/v3 不应该只靠普通 RGB loss。需要把像素画技法和限制放进训练目标、后处理和模型结构里。当前 v2 已经加入 palette/alpha 约束，后续可以继续加入更强的边缘损失、轮廓损失和真实软件候选图 pair。

为什么你刚才那张图“不对”

如果输出仍然“不对”，通常不是服务没跑，而是训练 pair 还不够贴近真实软件坏输出。v2 已经比 v1 强很多，但仍需要继续补“软件实际坏输出 -> 真像素目标”的 pair，尤其是 ai_pseudo / software_candidate，而不是只靠程序退化样本。

如果输出比输入更灰、更大、更糊，优先检查两件事：第一，主程序是否已经更新到“选中像素图作为输入”的版本；第二，强度是否过高。当前推荐先用 0.45 到 0.65，强化候选只作为比较，不一定是默认最好结果。

可选模型路线

轻量 CNN Refiner：v1 路线。快、包小、适合验证流程，但效果基础。
U-Net/NAFNet Refiner：当前 v2 路线。更强的局部和多尺度结构能力，4070 12GB 可以用 features=64、patch=256。
SwinIR / Restormer / NAFNet 类图像修复模型：更适合“去模糊、去伪影、重建边缘和纹理”，是像素修正器的优先候选。
条件扩散 Refiner：最强但最重，可以做“伪 AI 图 -> 真像素图”的强重绘；缺点是训练和推理更慢，也更难保证不改角色结构。

如果目标是 4070 大约 70% GPU 负载，优先做中型 U-Net 或 SwinIR/NAFNet 风格 refiner，而不是继续当前小 CNN。

推荐下一步

1. 用 GameDesigner 多生成一些你觉得“不对但结构对”的候选图。
2. 把这些图作为 input，和高质量真像素 target 配成 pairs。
3. 用 Retrain 覆盖训练一个 v2。
4. 每轮训练后重启服务，再在主程序里右键“AI 修正像素画”验证。
""".strip()


def main(argv: list[str] | None = None) -> int:
    raw_args = list(sys.argv[1:] if argv is None else argv)
    if raw_args and raw_args[0] == RUN_SERVICE_ARG:
        from pixel_refiner_service.server import main as service_main

        return service_main(raw_args[1:])
    if raw_args and raw_args[0] == RUN_TRAINING_CLI_ARG:
        return _run_training_cli_from_frozen(raw_args[1:])

    parser = argparse.ArgumentParser(description="Visible Pixel Refiner service and training console")
    parser.add_argument("--model-dir", type=Path, default=pixel_refiner_model_dir(DEFAULT_CONSOLE_MODEL_ID))
    parser.add_argument("--host", default=DEFAULT_HOST)
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    parser.add_argument("--no-auto-start", action="store_true")
    args = parser.parse_args(raw_args)

    app = QApplication(sys.argv[:1])
    app.setApplicationName("Pixel Refiner 控制台")
    window = PixelRefinerServiceWindow(
        model_dir=args.model_dir,
        host=args.host,
        port=args.port,
        auto_start=not bool(args.no_auto_start),
    )
    window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
