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
from datetime import datetime
from pathlib import Path
from typing import Any

from PySide6.QtCore import QProcess, QProcessEnvironment, QTimer, Qt, QUrl, Signal
from PySide6.QtGui import QDesktopServices, QPixmap
from PySide6.QtWidgets import (
    QApplication,
    QComboBox,
    QDoubleSpinBox,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QPlainTextEdit,
    QProgressBar,
    QSpinBox,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from gamedesigner.paths import game_designer_data_root, pixel_refiner_model_dir, pixel_refiner_runs_root
from gamedesigner.pixel_refiner_dataset import dataset_dir
from pixel_refiner_service.manifest import DEFAULT_MODEL_ID


V2_MODEL_ID = "pixel-refiner-v2"
V3_MODEL_ID = "pixel-refiner-v3"
V4_MODEL_ID = "pixel-refiner-v4"
DEFAULT_CONSOLE_MODEL_ID = V4_MODEL_ID
PROJECT_ROOT = Path(__file__).resolve().parent.parent
SOURCE_DIR = Path(__file__).resolve().parent
SERVICE_MAIN = SOURCE_DIR / "pixel_refiner_service_main.py"
TRAINING_MAIN = SOURCE_DIR / "pixel_refiner_training_main.py"
REFINE_TEST_MAIN = SOURCE_DIR / "pixel_refiner_test_runner.py"
DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8765
RUN_SERVICE_ARG = "--run-service"
RUN_TRAINING_CLI_ARG = "--run-training-cli"
RUN_REFINE_TEST_ARG = "--run-refine-test"
RUN_OUTPUT_FILE_ARG = "--run-output-file"
IMAGE_FILE_FILTER = "图片 (*.png *.jpg *.jpeg *.webp *.bmp);;所有文件 (*.*)"
IMAGE_FILE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".webp", ".bmp"}


def _is_supported_image_path(path: Path) -> bool:
    return path.suffix.lower() in IMAGE_FILE_EXTENSIONS


def _first_supported_image_path_from_mime(mime_data: Any) -> str:
    if mime_data is None:
        return ""
    if mime_data.hasUrls():
        for url in mime_data.urls():
            if not url.isLocalFile():
                continue
            path = Path(url.toLocalFile())
            if path.is_file() and _is_supported_image_path(path):
                return str(path)
    if mime_data.hasText():
        raw = str(mime_data.text() or "").strip().strip('"')
        if raw.startswith("file:///"):
            raw = QUrl(raw).toLocalFile()
        path = Path(raw)
        if path.is_file() and _is_supported_image_path(path):
            return str(path)
    return ""


class ImageDropLineEdit(QLineEdit):
    imageDropped = Signal(str)

    def __init__(self) -> None:
        super().__init__()
        self.setAcceptDrops(True)
        self.setPlaceholderText("可选择图片，也可直接拖入 PNG/JPG/WebP/BMP")

    def dragEnterEvent(self, event) -> None:  # type: ignore[override]
        if _first_supported_image_path_from_mime(event.mimeData()):
            event.acceptProposedAction()
            return
        super().dragEnterEvent(event)

    def dragMoveEvent(self, event) -> None:  # type: ignore[override]
        if _first_supported_image_path_from_mime(event.mimeData()):
            event.acceptProposedAction()
            return
        super().dragMoveEvent(event)

    def dropEvent(self, event) -> None:  # type: ignore[override]
        path = _first_supported_image_path_from_mime(event.mimeData())
        if not path:
            super().dropEvent(event)
            return
        self.setText(path)
        self.imageDropped.emit(path)
        event.acceptProposedAction()


class ImagePreviewLabel(QLabel):
    imageDropped = Signal(str)
    clicked = Signal()

    def __init__(self, text: str, *, accepts_image_drops: bool = False, clickable: bool = False) -> None:
        super().__init__(text)
        self._clickable = clickable
        self.setAcceptDrops(accepts_image_drops)
        if clickable:
            self.setCursor(Qt.PointingHandCursor)

    def dragEnterEvent(self, event) -> None:  # type: ignore[override]
        if self.acceptDrops() and _first_supported_image_path_from_mime(event.mimeData()):
            event.acceptProposedAction()
            return
        super().dragEnterEvent(event)

    def dragMoveEvent(self, event) -> None:  # type: ignore[override]
        if self.acceptDrops() and _first_supported_image_path_from_mime(event.mimeData()):
            event.acceptProposedAction()
            return
        super().dragMoveEvent(event)

    def dropEvent(self, event) -> None:  # type: ignore[override]
        path = _first_supported_image_path_from_mime(event.mimeData()) if self.acceptDrops() else ""
        if not path:
            super().dropEvent(event)
            return
        self.imageDropped.emit(path)
        event.acceptProposedAction()

    def mousePressEvent(self, event) -> None:  # type: ignore[override]
        if self._clickable and event.button() == Qt.LeftButton:
            self.clicked.emit()
            event.accept()
            return
        super().mousePressEvent(event)


def default_dataset_dir() -> Path:
    return dataset_dir()


def default_test_output_dir() -> Path:
    return pixel_refiner_runs_root() / "test_outputs"


def default_training_event_log() -> Path:
    return pixel_refiner_runs_root() / "20260530_v4_gold_small" / "train_events.jsonl"


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

        self.test_process = QProcess(self)
        self.test_process.readyReadStandardOutput.connect(self._read_test_stdout)
        self.test_process.readyReadStandardError.connect(self._read_test_stderr)
        self.test_process.finished.connect(self._test_finished)

        self.last_output_path = ""
        self.last_test_output_path = ""
        self._test_output_file: Path | None = None
        self._test_stdout_parts: list[str] = []
        self._test_stderr_parts: list[str] = []
        self._last_training_monitor_count = 0
        self.attached_external = False

        self._build_widgets(model_dir=model_dir, host=host, port=port)
        self._build_layout()
        self._apply_style()

        self.timer = QTimer(self)
        self.timer.setInterval(1000)
        self.timer.timeout.connect(self.refresh_status)
        self.timer.start()

        self.training_monitor_timer = QTimer(self)
        self.training_monitor_timer.setInterval(5000)
        self.training_monitor_timer.timeout.connect(self.refresh_training_monitor)
        self.training_monitor_timer.start()

        QTimer.singleShot(100, self.refresh_status)
        QTimer.singleShot(150, self.refresh_dataset_summary)
        QTimer.singleShot(250, self.refresh_training_monitor)
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
        self.model_id_combo.addItem("Pixel Refiner V4（Gold + 硬像素输出层）", V4_MODEL_ID)
        self.model_id_combo.addItem("Pixel Refiner V3（重叠 Tile + 2x 像素级）", V3_MODEL_ID)
        self.model_id_combo.addItem("Pixel Refiner V2（U-Net/NAF + 像素约束）", V2_MODEL_ID)
        self.model_id_combo.addItem("Pixel Refiner V1（基础 CNN）", DEFAULT_MODEL_ID)
        if model_dir.name == V4_MODEL_ID:
            self.model_id_combo.setCurrentIndex(0)
        elif model_dir.name == V3_MODEL_ID:
            self.model_id_combo.setCurrentIndex(1)
        elif model_dir.name == V2_MODEL_ID:
            self.model_id_combo.setCurrentIndex(2)
        elif model_dir.name == DEFAULT_MODEL_ID:
            self.model_id_combo.setCurrentIndex(3)

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

        self.test_input_edit = ImageDropLineEdit()
        self.choose_test_input_button = QPushButton("选择图片")
        self.test_output_dir_edit = QLineEdit(str(default_test_output_dir()))
        self.choose_test_output_button = QPushButton("选择")
        self.open_test_output_dir_button = QPushButton("打开输出目录")
        self.test_width_spin = QSpinBox()
        self.test_width_spin.setRange(16, 1024)
        self.test_width_spin.setValue(256)
        self.test_height_spin = QSpinBox()
        self.test_height_spin.setRange(16, 1024)
        self.test_height_spin.setValue(256)
        self.test_strength_spin = QDoubleSpinBox()
        self.test_strength_spin.setRange(0.0, 1.0)
        self.test_strength_spin.setSingleStep(0.05)
        self.test_strength_spin.setDecimals(2)
        self.test_strength_spin.setValue(0.45)
        self.test_candidates_spin = QSpinBox()
        self.test_candidates_spin.setRange(1, 8)
        self.test_candidates_spin.setValue(1)
        self.test_palette_spin = QSpinBox()
        self.test_palette_spin.setRange(0, 512)
        self.test_palette_spin.setValue(64)
        self.test_alpha_combo = QComboBox()
        self.test_alpha_combo.addItem("保留透明通道", "preserve")
        self.test_run_button = QPushButton("测试生成")
        self.open_test_output_button = QPushButton("打开当前输出")
        self.test_input_preview = ImagePreviewLabel("输入预览", accepts_image_drops=True)
        self.test_output_preview = ImagePreviewLabel("输出预览", clickable=True)
        for preview in (self.test_input_preview, self.test_output_preview):
            preview.setAlignment(Qt.AlignCenter)
            preview.setMinimumSize(260, 260)
            preview.setStyleSheet("background: #ffffff; border: 1px solid #d8d8dd; border-radius: 6px;")
        self.test_input_preview.setToolTip("拖入图片作为测试输入")
        self.test_output_preview.setToolTip("生成后点击可在文件夹中定位输出图")
        self.test_log_edit = QPlainTextEdit()
        self.test_log_edit.setReadOnly(True)
        self.test_log_edit.setMaximumBlockCount(2000)

        self.choose_test_input_button.clicked.connect(self.choose_test_input_image)
        self.test_input_edit.imageDropped.connect(self._use_test_input_image)
        self.test_input_preview.imageDropped.connect(self._use_test_input_image)
        self.test_output_preview.clicked.connect(self.open_test_output_in_folder)
        self.test_input_edit.editingFinished.connect(
            lambda: self._load_test_input_preview(self.test_input_edit.text().strip(), update_size=True)
        )
        self.choose_test_output_button.clicked.connect(self.choose_test_output_dir)
        self.open_test_output_dir_button.clicked.connect(
            lambda: self.open_folder(Path(self.test_output_dir_edit.text().strip() or str(default_test_output_dir())))
        )
        self.test_run_button.clicked.connect(lambda: self.start_test_refine())
        self.open_test_output_button.clicked.connect(self.open_test_output)

        self.train_python_edit = QLineEdit(str(default_training_python()))
        self.choose_train_python_button = QPushButton("选择")
        self.train_output_dir_edit = QLineEdit(str(model_dir))
        self.choose_train_output_button = QPushButton("选择")
        self.train_event_log_edit = QLineEdit(str(default_training_event_log()))
        self.monitor_train_button = QPushButton("监控当前训练")
        self.open_train_run_button = QPushButton("打开 run")
        self.training_progress_label = QLabel("未监控")
        self.training_progress_bar = QProgressBar()
        self.training_progress_bar.setRange(0, 1000)
        self.training_progress_bar.setValue(0)
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
        self.model_id_combo.currentIndexChanged.connect(self._model_selection_changed)
        self.smoke_train_button.clicked.connect(self.start_smoke_training)
        self.train_button.clicked.connect(lambda: self.start_training(retrain=False))
        self.retrain_button.clicked.connect(lambda: self.start_training(retrain=True))
        self.stop_training_button.clicked.connect(self.stop_training)
        self.open_train_output_button.clicked.connect(lambda: self.open_folder(Path(self.train_output_dir_edit.text().strip())))
        self.monitor_train_button.clicked.connect(lambda: self.refresh_training_monitor(force_append=True))
        self.open_train_run_button.clicked.connect(lambda: self.open_folder(Path(self.train_event_log_edit.text().strip() or str(default_training_event_log())).parent))

        self.help_edit = QPlainTextEdit()
        self.help_edit.setReadOnly(True)
        self.help_edit.setPlainText(HELP_TEXT)

    def _build_layout(self) -> None:
        tabs = QTabWidget(self)
        tabs.addTab(self._build_service_tab(), "服务")
        tabs.addTab(self._build_dataset_tab(), "数据集")
        tabs.addTab(self._build_test_tab(), "测试生成")
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

    def _build_test_tab(self) -> QWidget:
        tab = QWidget(self)
        layout = QVBoxLayout(tab)
        layout.setContentsMargins(18, 18, 18, 18)
        layout.setSpacing(12)

        input_row = QHBoxLayout()
        input_row.addWidget(QLabel("输入图片"))
        input_row.addWidget(self.test_input_edit, 1)
        input_row.addWidget(self.choose_test_input_button)
        layout.addLayout(input_row)

        output_row = QHBoxLayout()
        output_row.addWidget(QLabel("输出目录"))
        output_row.addWidget(self.test_output_dir_edit, 1)
        output_row.addWidget(self.choose_test_output_button)
        output_row.addWidget(self.open_test_output_dir_button)
        layout.addLayout(output_row)

        form = QFormLayout()
        form.setLabelAlignment(Qt.AlignRight)
        size_row = QHBoxLayout()
        size_row.addWidget(self.test_width_spin)
        size_row.addWidget(QLabel("x"))
        size_row.addWidget(self.test_height_spin)
        size_row.addStretch(1)
        form.addRow("目标尺寸", size_row)
        form.addRow("修正强度", self.test_strength_spin)
        form.addRow("候选数量", self.test_candidates_spin)
        form.addRow("调色板上限", self.test_palette_spin)
        form.addRow("透明通道", self.test_alpha_combo)
        layout.addLayout(form)

        buttons = QHBoxLayout()
        buttons.addWidget(self.test_run_button)
        buttons.addWidget(self.open_test_output_button)
        buttons.addStretch(1)
        layout.addLayout(buttons)

        preview_row = QHBoxLayout()
        input_preview_col = QVBoxLayout()
        input_preview_col.addWidget(QLabel("输入"))
        input_preview_col.addWidget(self.test_input_preview, 1)
        output_preview_col = QVBoxLayout()
        output_preview_col.addWidget(QLabel("输出"))
        output_preview_col.addWidget(self.test_output_preview, 1)
        preview_row.addLayout(input_preview_col, 1)
        preview_row.addLayout(output_preview_col, 1)
        layout.addLayout(preview_row, 1)

        layout.addWidget(QLabel("测试日志"))
        layout.addWidget(self.test_log_edit, 1)
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

        monitor_row = QHBoxLayout()
        monitor_row.addWidget(QLabel("训练事件日志"))
        monitor_row.addWidget(self.train_event_log_edit, 1)
        monitor_row.addWidget(self.monitor_train_button)
        monitor_row.addWidget(self.open_train_run_button)
        layout.addLayout(monitor_row)

        progress_row = QHBoxLayout()
        progress_row.addWidget(QLabel("当前训练"))
        progress_row.addWidget(self.training_progress_bar, 1)
        progress_row.addWidget(self.training_progress_label, 2)
        layout.addLayout(progress_row)

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

    def choose_test_input_image(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self,
            "选择测试输入图片",
            self.test_input_edit.text().strip(),
            IMAGE_FILE_FILTER,
        )
        if path:
            self._use_test_input_image(path)

    def _use_test_input_image(self, path: str) -> None:
        image_path = Path(path)
        if not image_path.is_file() or not _is_supported_image_path(image_path):
            QMessageBox.warning(self, "Pixel Refiner", f"不是可用的图片文件：{image_path}")
            return
        self.test_input_edit.setText(str(image_path))
        self._load_test_input_preview(str(image_path), update_size=True)

    def choose_test_output_dir(self) -> None:
        folder = QFileDialog.getExistingDirectory(self, "选择测试输出目录", self.test_output_dir_edit.text().strip())
        if folder:
            self.test_output_dir_edit.setText(folder)

    def start_test_refine(self, *, retry_count: int = 0) -> None:
        if self.test_process.state() != QProcess.NotRunning:
            QMessageBox.information(self, "Pixel Refiner", "测试生成已经在运行。")
            return

        health = self._get_json("/v1/health", timeout=0.5)
        if not (isinstance(health, dict) and health.get("ok")):
            if retry_count == 0:
                self._append_test_log("服务未运行，正在启动/连接服务...")
                self.test_run_button.setEnabled(False)
                self.start_or_attach()
            if retry_count < 12:
                QTimer.singleShot(1000, lambda: self.start_test_refine(retry_count=retry_count + 1))
                return
            self.test_run_button.setEnabled(True)
            QMessageBox.warning(self, "Pixel Refiner", "服务还没有准备好。请确认模型包能正常加载，再点击测试生成。")
            return
        self.test_run_button.setEnabled(True)

        input_path = Path(self.test_input_edit.text().strip())
        if not input_path.is_file():
            QMessageBox.warning(self, "Pixel Refiner", f"测试输入图不存在：{input_path}")
            return
        self._load_test_input_preview(str(input_path), update_size=False)

        output_root = Path(self.test_output_dir_edit.text().strip() or str(default_test_output_dir()))
        run_name = f"{_safe_stem(input_path.stem)}_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
        output_dir = output_root / run_name
        target_size = f"{self.test_width_spin.value()}x{self.test_height_spin.value()}"
        service_url = f"http://{self.host_edit.text().strip() or DEFAULT_HOST}:{self.port_spin.value()}"
        args = [
            "--service-url",
            service_url,
            "--input",
            str(input_path),
            "--output-dir",
            str(output_dir),
            "--target-size",
            target_size,
            "--model-id",
            self.current_model_id(),
            "--model-dir",
            self.model_dir_edit.text().strip() or str(pixel_refiner_model_dir(self.current_model_id())),
            "--alpha-mode",
            str(self.test_alpha_combo.currentData() or "preserve"),
            "--palette-limit",
            str(self.test_palette_spin.value()),
            "--strength",
            f"{self.test_strength_spin.value():.4f}",
            "--return-candidates",
            str(self.test_candidates_spin.value()),
            "--timeout",
            "300",
        ]
        output_file = Path(tempfile.gettempdir()) / f"pixel_refiner_test_{uuid.uuid4().hex}.txt"
        self._test_output_file = output_file
        frozen_args = [RUN_OUTPUT_FILE_ARG, str(output_file), *args]
        program, process_args = _subprocess_command(
            REFINE_TEST_MAIN,
            args,
            frozen_arg=RUN_REFINE_TEST_ARG,
            frozen_args=frozen_args,
        )
        self._test_stdout_parts.clear()
        self._test_stderr_parts.clear()
        self.last_test_output_path = ""
        self.test_output_preview.setPixmap(QPixmap())
        self.test_output_preview.setText("输出预览")
        self.test_log_edit.clear()
        self._append_test_log("测试生成启动：" + " ".join([program, *process_args]))
        self.test_process.setProcessEnvironment(self._process_env())
        self.test_process.setWorkingDirectory(str(PROJECT_ROOT))
        self.test_run_button.setEnabled(False)
        self.test_process.start(program, process_args)
        if not self.test_process.waitForStarted(3000):
            self.test_run_button.setEnabled(True)
            self._append_test_log("测试生成进程启动失败。")

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
            "96" if model_id == V4_MODEL_ID else ("64" if model_id in {V2_MODEL_ID, V3_MODEL_ID} else "48"),
            "--palette-levels",
            "64",
            "--pixel-constraint-weight",
            "0.12" if model_id == V4_MODEL_ID else "0.08",
            "--internal-scale",
            "2" if model_id in {V3_MODEL_ID, V4_MODEL_ID} else "1",
            "--tile-overlap",
            "16" if model_id in {V3_MODEL_ID, V4_MODEL_ID} else "0",
            "--block-consistency-weight",
            "0.25" if model_id == V4_MODEL_ID else ("0.20" if model_id == V3_MODEL_ID else "0.0"),
            "--edge-loss-weight",
            "0.55" if model_id == V4_MODEL_ID else "0.25",
            "--anti-blur-weight",
            "0.12" if model_id == V4_MODEL_ID else "0.0",
            "--event-log",
            str(default_training_event_log()),
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
            "96" if self.current_model_id() == V4_MODEL_ID else ("64" if self.current_model_id() in {V2_MODEL_ID, V3_MODEL_ID} else "48"),
            "--palette-levels",
            "64",
            "--pixel-constraint-weight",
            "0.12" if self.current_model_id() == V4_MODEL_ID else "0.08",
            "--internal-scale",
            "2" if self.current_model_id() in {V3_MODEL_ID, V4_MODEL_ID} else "1",
            "--tile-overlap",
            "16" if self.current_model_id() in {V3_MODEL_ID, V4_MODEL_ID} else "0",
            "--block-consistency-weight",
            "0.25" if self.current_model_id() == V4_MODEL_ID else ("0.20" if self.current_model_id() == V3_MODEL_ID else "0.0"),
            "--edge-loss-weight",
            "0.55" if self.current_model_id() == V4_MODEL_ID else "0.25",
            "--anti-blur-weight",
            "0.12" if self.current_model_id() == V4_MODEL_ID else "0.0",
            "--event-log",
            self.train_event_log_edit.text().strip() or str(default_training_event_log()),
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
            if Path(folder).name == V4_MODEL_ID:
                self.model_id_combo.setCurrentIndex(0)
            elif Path(folder).name == V3_MODEL_ID:
                self.model_id_combo.setCurrentIndex(1)
            elif Path(folder).name == V2_MODEL_ID:
                self.model_id_combo.setCurrentIndex(2)
            elif Path(folder).name == DEFAULT_MODEL_ID:
                self.model_id_combo.setCurrentIndex(3)

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
        self.open_path_in_file_browser(Path(self.last_output_path))

    def open_test_output(self) -> None:
        if not self.last_test_output_path:
            QMessageBox.information(self, "Pixel Refiner", "还没有测试输出文件。")
            return
        self.open_path_in_file_browser(Path(self.last_test_output_path))

    def open_test_output_in_folder(self) -> None:
        if not self.last_test_output_path:
            return
        self.open_path_in_file_browser(Path(self.last_test_output_path))

    def open_folder(self, path: Path) -> None:
        if not path:
            return
        path.mkdir(parents=True, exist_ok=True)
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(path)))

    def open_path_in_file_browser(self, path: Path) -> None:
        if path.is_file():
            if sys.platform.startswith("win") and QProcess.startDetached("explorer.exe", ["/select,", str(path)]):
                return
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(path.parent)))
            return
        self.open_folder(path)

    def closeEvent(self, event) -> None:  # noqa: N802
        if self._own_service_running():
            self.service_process.terminate()
            self.service_process.waitForFinished(1000)
        if self.training_process.state() != QProcess.NotRunning:
            self.training_process.terminate()
            self.training_process.waitForFinished(1000)
        if self.test_process.state() != QProcess.NotRunning:
            self.test_process.terminate()
            self.test_process.waitForFinished(1000)
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
        if self.current_model_id() == V4_MODEL_ID:
            return "pixel-hard-v4"
        if self.current_model_id() == V3_MODEL_ID:
            return "pixel-tile-v3"
        return "unet-naf-v2" if self.current_model_id() == V2_MODEL_ID else "cnn-v1"

    def _model_selection_changed(self) -> None:
        model_id = self.current_model_id()
        known_model_ids = {DEFAULT_MODEL_ID, V2_MODEL_ID, V3_MODEL_ID, V4_MODEL_ID}
        model_dir_text = self.model_dir_edit.text().strip()
        train_dir_text = self.train_output_dir_edit.text().strip()
        if not model_dir_text or Path(model_dir_text).name in known_model_ids:
            self.model_dir_edit.setText(str(pixel_refiner_model_dir(model_id)))
        if not train_dir_text or Path(train_dir_text).name in known_model_ids:
            self.train_output_dir_edit.setText(str(pixel_refiner_model_dir(model_id)))
        if model_id in {V3_MODEL_ID, V4_MODEL_ID} and self.patch_spin.value() == 256:
            self.patch_spin.setValue(64)
        elif model_id == V2_MODEL_ID and self.patch_spin.value() == 64:
            self.patch_spin.setValue(256)

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

    def refresh_training_monitor(self, *, force_append: bool = False) -> None:
        path = Path(self.train_event_log_edit.text().strip() or str(default_training_event_log()))
        events = _read_training_events(path)
        if not events:
            self.training_progress_label.setText("未读到训练事件")
            self.training_progress_bar.setValue(0)
            if force_append:
                self._append_training_log(f"没有读到训练事件日志：{path}")
            return
        status = _training_status_from_events(events, Path(self.train_output_dir_edit.text().strip() or str(pixel_refiner_model_dir(self.current_model_id()))))
        self.training_progress_label.setText(status["label"])
        self.training_progress_bar.setValue(int(status["progress"] * 1000))
        if force_append or len(events) > self._last_training_monitor_count:
            start_index = 0 if force_append or self._last_training_monitor_count <= 0 else self._last_training_monitor_count
            if force_append:
                self._append_training_log(f"正在监控训练事件：{path}")
                self._append_training_log(status["detail"])
            for event in events[start_index:][-12:]:
                self._append_training_log(json.dumps(event, ensure_ascii=False))
            self._last_training_monitor_count = len(events)

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

    def _read_test_stdout(self) -> None:
        text = bytes(self.test_process.readAllStandardOutput()).decode("utf-8", errors="replace")
        if text:
            self._test_stdout_parts.append(text)

    def _read_test_stderr(self) -> None:
        text = bytes(self.test_process.readAllStandardError()).decode("utf-8", errors="replace")
        if text:
            self._test_stderr_parts.append(text)
            self._append_test_log(text.rstrip())

    def _service_finished(self, exit_code: int, _status: QProcess.ExitStatus) -> None:
        self._append_service_log(f"服务进程已退出，exit_code={exit_code}")
        self.refresh_status()

    def _training_finished(self, exit_code: int, _status: QProcess.ExitStatus) -> None:
        self._append_training_log(f"训练进程已退出，exit_code={exit_code}")
        if exit_code == 0:
            self._append_training_log("训练完成。模型包已写入输出目录；如果服务正在运行，请重启服务后加载新权重。")

    def _test_finished(self, exit_code: int, _status: QProcess.ExitStatus) -> None:
        self.test_run_button.setEnabled(True)
        stdout = "".join(self._test_stdout_parts).strip()
        stderr = "".join(self._test_stderr_parts).strip()
        if self._test_output_file is not None and self._test_output_file.is_file():
            try:
                stdout = self._test_output_file.read_text(encoding="utf-8").strip()
            except OSError:
                pass
            try:
                self._test_output_file.unlink()
            except OSError:
                pass
        self._test_output_file = None
        payload = _json_from_text(stdout)
        if isinstance(payload, dict):
            self._append_test_log(_pretty_json(json.dumps(payload, ensure_ascii=False)))
        elif stdout:
            self._append_test_log(stdout)
        if stderr and not stdout:
            self._append_test_log(stderr)
        if exit_code != 0 or not (isinstance(payload, dict) and payload.get("ok")):
            message = ""
            if isinstance(payload, dict):
                message = str(payload.get("message") or "")
            self._append_test_log(f"测试生成失败，exit_code={exit_code}{('：' + message) if message else ''}")
            return
        outputs = payload.get("outputs") if isinstance(payload.get("outputs"), list) else []
        output_paths = [
            str(item.get("path") or "")
            for item in outputs
            if isinstance(item, dict) and str(item.get("path") or "").strip()
        ]
        if not output_paths:
            self._append_test_log("测试生成完成，但没有返回输出文件。")
            return
        self.last_test_output_path = output_paths[-1]
        self.last_output_path = self.last_test_output_path
        self._set_preview_image(self.test_output_preview, self.last_test_output_path)
        self.refresh_status()
        self._append_test_log(f"测试生成完成。当前预览：{self.last_test_output_path}")

    def _append_service_log(self, text: str) -> None:
        self.service_log_edit.appendPlainText(text)

    def _append_training_log(self, text: str) -> None:
        self.training_log_edit.appendPlainText(text)

    def _append_test_log(self, text: str) -> None:
        self.test_log_edit.appendPlainText(text)

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

    def _load_test_input_preview(self, path_text: str, *, update_size: bool) -> None:
        path = Path(path_text) if path_text else Path()
        if not path.is_file():
            return
        pixmap = self._set_preview_image(self.test_input_preview, str(path))
        if pixmap is not None and update_size:
            width, height = _fit_target_size(pixmap.width(), pixmap.height())
            self.test_width_spin.setValue(width)
            self.test_height_spin.setValue(height)
            self._append_test_log(f"已读取输入尺寸：{pixmap.width()}x{pixmap.height()}，测试目标尺寸：{width}x{height}")

    def _set_preview_image(self, label: QLabel, path_text: str) -> QPixmap | None:
        pixmap = QPixmap(path_text)
        if pixmap.isNull():
            label.setPixmap(QPixmap())
            label.setText("无法预览")
            label.setToolTip(path_text)
            return None
        preview = pixmap.scaled(360, 360, Qt.KeepAspectRatio, Qt.SmoothTransformation)
        label.setPixmap(preview)
        label.setText("")
        label.setToolTip(path_text)
        return pixmap


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


def _json_from_text(text: str) -> dict[str, Any] | None:
    if not text.strip():
        return None
    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        return None
    return payload if isinstance(payload, dict) else None


def _read_training_events(path: Path) -> list[dict[str, Any]]:
    if not path.is_file():
        return []
    events: list[dict[str, Any]] = []
    try:
        with path.open("r", encoding="utf-8") as file:
            for line in file:
                raw = line.strip()
                if not raw:
                    continue
                try:
                    payload = json.loads(raw)
                except json.JSONDecodeError:
                    continue
                if isinstance(payload, dict):
                    events.append(payload)
    except OSError:
        return []
    return events


def _training_status_from_events(events: list[dict[str, Any]], model_dir: Path) -> dict[str, Any]:
    start = next((event for event in events if event.get("event") == "train_start"), {})
    step_events = [event for event in events if event.get("event") == "train_step"]
    epoch_events = [event for event in events if event.get("event") == "epoch_end"]
    abort_events = [event for event in events if event.get("event") == "train_abort"]
    steps_per_epoch = max(1, int(start.get("steps_per_epoch") or 1))
    epochs = max(1, int(start.get("epochs") or 1))
    if step_events:
        current = step_events[-1]
        epoch = max(1, int(current.get("epoch") or 1))
        step = max(0, int(current.get("step") or 0))
        loss = current.get("loss")
        done_steps = max(0, (epoch - 1) * steps_per_epoch + step)
        label = f"epoch {epoch}/{epochs}  step {step}/{steps_per_epoch}  loss {loss}"
    elif epoch_events:
        current = epoch_events[-1]
        epoch = max(1, int(current.get("epoch") or 1))
        done_steps = epoch * steps_per_epoch
        label = f"epoch {epoch}/{epochs} 完成  val_loss {current.get('val_loss')}"
    else:
        done_steps = 0
        label = "训练已启动，等待 step 事件"
    if abort_events:
        label = f"训练异常：{abort_events[-1].get('reason', 'unknown')}"
    progress = max(0.0, min(1.0, done_steps / max(1, steps_per_epoch * epochs)))
    manifest = model_dir / "model_manifest.json"
    export_state = "已导出 manifest" if manifest.is_file() else "尚未导出模型包"
    detail = (
        f"{label}\n"
        f"records={start.get('records', '-')} device={start.get('device', '-')} "
        f"architecture={start.get('architecture', '-')}\n"
        f"{export_state}: {manifest}"
    )
    return {"label": f"{label}  {progress * 100.0:.1f}%  {export_state}", "progress": progress, "detail": detail}


def _fit_target_size(width: int, height: int, *, max_edge: int = 1024) -> tuple[int, int]:
    width = max(1, int(width))
    height = max(1, int(height))
    if width <= max_edge and height <= max_edge:
        return width, height
    scale = min(max_edge / width, max_edge / height)
    return max(16, int(round(width * scale))), max(16, int(round(height * scale)))


def _safe_stem(value: str) -> str:
    cleaned = "".join(char if char.isalnum() or char in {"-", "_"} else "_" for char in str(value or "").strip())
    return cleaned[:48] or "image"


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


def _run_refine_test_from_frozen(raw_args: list[str]) -> int:
    output_file: Path | None = None
    args = list(raw_args)
    if len(args) >= 2 and args[0] == RUN_OUTPUT_FILE_ARG:
        output_file = Path(args[1])
        args = args[2:]

    try:
        from pixel_refiner_test_runner import main as test_main
    except Exception as exc:
        if output_file is not None:
            output_file.parent.mkdir(parents=True, exist_ok=True)
            output_file.write_text(f"{type(exc).__name__}: {exc}", encoding="utf-8")
        return 1

    if output_file is None:
        return test_main(args)

    stdout_buffer = io.StringIO()
    stderr_buffer = io.StringIO()
    exit_code = 1
    with contextlib.redirect_stdout(stdout_buffer), contextlib.redirect_stderr(stderr_buffer):
        try:
            exit_code = test_main(args)
        except SystemExit as exc:
            exit_code = int(exc.code or 0) if isinstance(exc.code, int) else 1
        except Exception as exc:
            print(f"{type(exc).__name__}: {exc}", file=sys.stderr)
            exit_code = 1
    output_file.parent.mkdir(parents=True, exist_ok=True)
    output_file.write_text(stdout_buffer.getvalue() or stderr_buffer.getvalue(), encoding="utf-8")
    return exit_code


TRAINING_PARAMETER_HELP = """
训练轮数：完整扫训练计划几轮。轮数越多越可能学到风格，也越容易过拟合；v2/v3 默认 4。
每轮步数：每一轮实际训练多少次参数更新。它比“图片数量”更直接决定训练时间；v2/v3 建议 700 到 1200。
批量大小：每次更新同时喂多少张裁剪 patch。越大越吃显存，梯度越稳；4070 12GB 当前 v2 推荐 4。
训练裁剪尺寸：从大图里裁出多大的原始像素块。v2 推荐 256；v3 推荐 64，然后内部 2x 放大成 128 送进模型。
验证批次数：每轮结束拿多少批样本检查 loss，不参与训练。
样本上限：0 表示全量训练；小数值用于快速试参数。
训练设备：显卡 CUDA 用 NVIDIA GPU；自动选择会优先 CUDA；CPU 只适合排错。
Retrain：会覆盖输出模型包里的 ONNX 权重和 manifest。训练完成后必须重启服务，主程序才会用新模型。
v3：重叠 tile 训练/推理。服务按 64x64 原像素块、16px 重叠切图，内部 2x 放大给模型学习，再用 2x2 block 缩回原网格，减少整图重绘和像素错位。
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
D:\\GameDesignerData\\pixel_refiner\\datasets\\gold_pndsndn_v1

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

测试生成页

选择一张 PNG/JPG/WebP/BMP，设置目标尺寸、修正强度、候选数量和调色板上限，然后点击“测试生成”。控制台会调用当前本地服务的 /v1/pixel/refine，把输出 PNG 写到：
D:\\GameDesignerData\\pixel_refiner\\runs\\test_outputs

这里走的就是 GameDesigner 主程序同一条模型服务路径，不是另外写的一套处理逻辑。输出预览默认显示最后一张候选；当候选数量大于 1 时，v2 单输出模型的最后一张通常是“标准”强度结果。

训练页

小训练 smoke：只跑极小样本，验证训练环境、CUDA、ONNX 导出是否正常。
训练当前模型：按界面参数训练并导出当前选择的模型包，默认是 pixel-refiner-v4；也可以选择 v2/v3 做旧路线对比。
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

当前 v2/v3/v4 都不是扩散模型，不是从一张纯色图开始一层一层加噪/去噪生成图片。它也不是 Stable Diffusion 那种“从随机噪声里采样出一张图”的流程。

当前模型是监督式 image-to-image 修正：
input.png -> 模型 -> refined RGB
target.png -> 监督目标

模型输入：
image：RGB，float32，NCHW，范围 0 到 1。
alpha：透明通道，float32，NCHW，范围 0 到 1。

模型输出：
RGB 或 RGBA，float32，范围 0 到 1。服务会按 alpha_mode 保留透明度。

当前 v4 是中型 U-Net/NAFNet 风格修正网络，导出成 ONNX，服务用 ONNX Runtime 跑。它直接看输入图的局部像素和 alpha，通过多尺度特征修正边缘、颜色、透明度和像素块结构；服务输出前还会按 manifest 做 hard pixel output、palette clamp 和 alpha clamp。

当前服务还会做一层“保守像素艺术输出层”：
1. GameDesigner 右键“AI 修正像素画”时，输入就是你当前选中的像素候选图，不再偷偷改用隐藏的原始大图。
2. 即使 ONNX 模型本身没有 strength 输入，服务也会在输出端按强度混合原图和模型结果。
3. 暗轮廓、高对比边缘、alpha 边界会自动降低模型覆盖率，避免把线条洗灰。
4. 调色板优先贴回输入图的颜色体系，再做 alpha clamp，减少全局 median-cut 导致的偏色。
5. 单输出模型会扩展成多个候选：只清理、保守、强化、标准，方便你直接比较。

v3/v4 的新思路

你提出的“小块、重叠、放大后逐像素学习”已经落到 v3/v4 协议里：
1. 训练时从 input/target 对里裁 64x64 原始像素块。
2. 每块用 Nearest 2x 放大成 128x128，让模型在更大的张量里看每个原始像素的局部关系。
3. loss 同时看 2x 输出、缩回 1x 后的像素结果、以及每个 2x2 block 内部是否一致。
4. manifest 写入 tiled_inference、tile_size、tile_overlap、internal_scale。
5. 推理时服务按重叠 tile 分块跑 ONNX，再用 feather weight 合并，最后缩回原像素网格。

这不是扩散式重绘，而是更接近像素画师“看局部连接、修边、合并色块”的 refiner。它牺牲一些速度，换来更强的像素对齐和局部规则学习。

v4 在 v3 的 tile 训练上继续加三件事：
1. 只用 gold_pndsndn_v1 这种高质量数据作为当前核心训练集。
2. loss 更重视边缘、局部方差和抗模糊，不再只追平均 RGB。
3. manifest 开启 hard_pixel_output，服务最后强制 hard alpha、model palette quantize 和小孤立像素清理，避免输出继续软、灰、糊。

更符合像素画的目标流程应该是：
1. 保护角色轮廓和透明 alpha。
2. 强制硬边，减少半透明灰雾。
3. 限制调色盘数量。
4. 让阴影、亮部、轮廓线、抗锯齿都符合像素画习惯。
5. 最后做 palette clamp、alpha clamp、nearest cleanup，避免输出软渐变。

所以 v2/v3/v4 不应该只靠普通 RGB loss。需要把像素画技法和限制放进训练目标、后处理和模型结构里。当前 v4 已经加入高质量 gold 数据、重叠 tile、2x block 一致性、抗模糊 loss 和硬像素输出层。

为什么你刚才那张图“不对”

如果输出仍然“不对”，通常不是服务没跑，而是训练 pair 还不够贴近真实软件坏输出。v2 已经比 v1 强很多，但仍需要继续补“软件实际坏输出 -> 真像素目标”的 pair，尤其是 ai_pseudo / software_candidate，而不是只靠程序退化样本。

如果输出比输入更灰、更大、更糊，优先检查两件事：第一，主程序是否已经更新到“选中像素图作为输入”的版本；第二，强度是否过高。当前推荐先用 0.45 到 0.65，强化候选只作为比较，不一定是默认最好结果。

可选模型路线

轻量 CNN Refiner：v1 路线。快、包小、适合验证流程，但效果基础。
U-Net/NAFNet Refiner：当前 v2 路线。更强的局部和多尺度结构能力，4070 12GB 可以用 features=64、patch=256。
Pixel-Tile V3 Refiner：重叠小块 + 内部 2x + block 一致性。更贴近像素级连接规则，适合你这类“缩小看还行，放大看像素逻辑不够好”的问题。
SwinIR / Restormer / NAFNet 类图像修复模型：更适合“去模糊、去伪影、重建边缘和纹理”，是像素修正器的优先候选。
条件扩散 Refiner：最强但最重，可以做“伪 AI 图 -> 真像素图”的强重绘；缺点是训练和推理更慢，也更难保证不改角色结构。

如果目标是 4070 大约 70% GPU 负载，优先做中型 U-Net 或 SwinIR/NAFNet 风格 refiner，而不是继续当前小 CNN。

推荐下一步

1. 用 GameDesigner 多生成一些你觉得“不对但结构对”的候选图。
2. 把这些图作为 input，和高质量真像素 target 配成 pairs。
3. 用 Retrain 覆盖训练 pixel-refiner-v4，旧的 v2/v3 只作为对照。
4. 每轮训练后重启服务，再在主程序里右键“AI 修正像素画”验证。
""".strip()


def main(argv: list[str] | None = None) -> int:
    raw_args = list(sys.argv[1:] if argv is None else argv)
    if raw_args and raw_args[0] == RUN_SERVICE_ARG:
        from pixel_refiner_service.server import main as service_main

        return service_main(raw_args[1:])
    if raw_args and raw_args[0] == RUN_TRAINING_CLI_ARG:
        return _run_training_cli_from_frozen(raw_args[1:])
    if raw_args and raw_args[0] == RUN_REFINE_TEST_ARG:
        return _run_refine_test_from_frozen(raw_args[1:])

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
