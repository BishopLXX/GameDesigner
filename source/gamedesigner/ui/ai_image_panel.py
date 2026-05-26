from __future__ import annotations

import shutil
from pathlib import Path
from typing import Callable

from PySide6.QtCore import QSize, Qt, QThread, QUrl, Signal
from PySide6.QtGui import QDesktopServices, QImage, QPixmap
from PySide6.QtWidgets import (
    QApplication,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
    QSpinBox,
    QTextBrowser,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from ..image_ai import (
    AI_IMAGE_BACKGROUND_PRESETS,
    AI_IMAGE_MODEL_PRESETS,
    AI_IMAGE_OUTPUT_FORMAT_PRESETS,
    AI_IMAGE_QUALITY_PRESETS,
    AI_IMAGE_SIZE_PRESETS,
    AiImageError,
    AiImageReference,
    AiImageRequest,
    SavedAiImage,
    build_ai_image_request,
    generate_ai_images,
    save_ai_image_reference,
    save_ai_image_reference_from_qimage,
    save_generated_ai_image,
)
from ..storage import AppSettings, save_settings
from ..window_layouts import restore_window_layout, save_window_layout
from .submit_text_edit import SubmitPlainTextEdit


class AiImageSettingsDialog(QDialog):
    def __init__(self, parent: QWidget | None, settings: AppSettings) -> None:
        super().__init__(parent)
        self.setWindowTitle("AI 生图设置")
        self.setModal(True)
        self.settings = settings

        self.provider_combo = QComboBox()
        self.provider_combo.addItem("OpenAI 官方 API", "openai")
        self.provider_combo.addItem("OpenAI 兼容 API", "compatible")
        provider_index = self.provider_combo.findData(getattr(settings, "ai_image_provider", "openai"))
        self.provider_combo.setCurrentIndex(provider_index if provider_index >= 0 else 0)
        self.provider_combo.currentIndexChanged.connect(self._refresh_provider_fields)

        self.model_combo = QComboBox()
        self.model_combo.setEditable(True)
        self.model_combo.addItems(AI_IMAGE_MODEL_PRESETS)
        self.model_combo.setEditText(settings.ai_image_model or AI_IMAGE_MODEL_PRESETS[0])

        self.api_key_edit = QLineEdit(settings.ai_image_api_key)
        self.api_key_edit.setEchoMode(QLineEdit.Password)
        self.api_key_edit.setPlaceholderText("OpenAI 或兼容服务 API Key")

        self.base_url_edit = QLineEdit(settings.ai_image_base_url)
        self.base_url_edit.setPlaceholderText("例如 https://api.openai.com/v1")

        self.output_format_combo = QComboBox()
        self.output_format_combo.addItems(AI_IMAGE_OUTPUT_FORMAT_PRESETS)
        output_index = self.output_format_combo.findText(settings.ai_image_output_format or "png")
        self.output_format_combo.setCurrentIndex(output_index if output_index >= 0 else 0)

        form = QFormLayout()
        form.setFieldGrowthPolicy(QFormLayout.AllNonFixedFieldsGrow)
        form.addRow("服务", self.provider_combo)
        form.addRow("模型", self.model_combo)
        form.addRow("API Key", self.api_key_edit)
        form.addRow("Base URL", self.base_url_edit)
        form.addRow("输出格式", self.output_format_combo)

        buttons = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        buttons.button(QDialogButtonBox.Save).setText("保存")
        buttons.button(QDialogButtonBox.Cancel).setText("取消")
        buttons.accepted.connect(self._save)
        buttons.rejected.connect(self.reject)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(18, 18, 18, 16)
        layout.setSpacing(14)
        layout.addLayout(form)
        layout.addWidget(buttons)
        self.resize(520, 260)
        restore_window_layout(self, "ai_image_settings_dialog")
        self._refresh_provider_fields()

    def _refresh_provider_fields(self) -> None:
        official = self.provider_combo.currentData() == "openai"
        self.base_url_edit.setEnabled(not official)
        if official:
            self.base_url_edit.setText("")

    def _save(self) -> None:
        self.settings.ai_image_provider = str(self.provider_combo.currentData() or "openai")
        self.settings.ai_image_model = self.model_combo.currentText().strip() or AI_IMAGE_MODEL_PRESETS[0]
        self.settings.ai_image_api_key = self.api_key_edit.text().strip()
        self.settings.ai_image_base_url = self.base_url_edit.text().strip()
        self.settings.ai_image_output_format = self.output_format_combo.currentText().strip() or "png"
        save_settings(self.settings)
        self.accept()

    def done(self, result: int) -> None:  # type: ignore[override]
        save_window_layout(self, "ai_image_settings_dialog")
        super().done(result)


class ReferenceImageList(QListWidget):
    filesDropped = Signal(list)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("aiImageReferenceList")
        self.setAcceptDrops(True)
        self.setViewMode(QListWidget.IconMode)
        self.setIconSize(QSize(76, 76))
        self.setGridSize(QSize(92, 104))
        self.setResizeMode(QListWidget.Adjust)
        self.setSelectionMode(QListWidget.ExtendedSelection)
        self.setSpacing(6)

    def dragEnterEvent(self, event) -> None:  # type: ignore[override]
        if self._image_paths_from_event(event):
            event.acceptProposedAction()
            return
        super().dragEnterEvent(event)

    def dragMoveEvent(self, event) -> None:  # type: ignore[override]
        if self._image_paths_from_event(event):
            event.acceptProposedAction()
            return
        super().dragMoveEvent(event)

    def dropEvent(self, event) -> None:  # type: ignore[override]
        paths = self._image_paths_from_event(event)
        if paths:
            self.filesDropped.emit(paths)
            event.acceptProposedAction()
            return
        super().dropEvent(event)

    def _image_paths_from_event(self, event) -> list[str]:
        mime = event.mimeData()
        if not mime.hasUrls():
            return []
        paths: list[str] = []
        for url in mime.urls():
            path = Path(url.toLocalFile())
            if path.suffix.lower() in {".png", ".jpg", ".jpeg", ".webp"} and path.exists():
                paths.append(str(path))
        return paths


class ImageGenerationThread(QThread):
    succeeded = Signal(object)
    failed = Signal(str)

    def __init__(self, request: AiImageRequest, project_path: Path, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.request = request
        self.project_path = project_path

    def run(self) -> None:  # type: ignore[override]
        try:
            images = generate_ai_images(self.request)
            saved = [
                save_generated_ai_image(self.project_path, image, index=index)
                for index, image in enumerate(images, start=1)
            ]
        except Exception as exc:
            self.failed.emit(str(exc))
            return
        self.succeeded.emit(saved)


class AiImagePanel(QWidget):
    collapseRequested = Signal()

    def __init__(
        self,
        parent: QWidget | None,
        settings: AppSettings,
        context_provider: Callable[[], tuple[str, Path, Path]],
    ) -> None:
        super().__init__(parent)
        self.setObjectName("aiAssistantPanel")
        self.settings = settings
        self.context_provider = context_provider
        self.references: list[AiImageReference] = []
        self.generated_images: list[SavedAiImage] = []
        self._current_image_path: Path | None = None
        self._thread: ImageGenerationThread | None = None

        title_label = QLabel("AI 生图助手")
        title_label.setObjectName("aiAssistantTitle")
        collapse_button = QToolButton(self)
        collapse_button.setObjectName("aiAssistantCollapseButton")
        collapse_button.setText("收起")
        collapse_button.clicked.connect(self.collapseRequested.emit)
        title_row = QHBoxLayout()
        title_row.setContentsMargins(0, 0, 0, 0)
        title_row.addWidget(title_label)
        title_row.addStretch(1)
        title_row.addWidget(collapse_button)

        self.header_label = QLabel()
        self.header_label.setObjectName("mutedLabel")

        self.model_combo = QComboBox()
        self.model_combo.setEditable(True)
        self.model_combo.addItems(AI_IMAGE_MODEL_PRESETS)
        self.model_combo.setEditText(settings.ai_image_model or AI_IMAGE_MODEL_PRESETS[0])
        self.model_combo.currentTextChanged.connect(self._save_inline_settings)

        self.size_combo = QComboBox()
        self._fill_combo(self.size_combo, AI_IMAGE_SIZE_PRESETS, settings.ai_image_size)
        self.size_combo.currentTextChanged.connect(self._save_inline_settings)

        self.quality_combo = QComboBox()
        self._fill_combo(self.quality_combo, AI_IMAGE_QUALITY_PRESETS, settings.ai_image_quality)
        self.quality_combo.currentTextChanged.connect(self._save_inline_settings)

        self.background_combo = QComboBox()
        self._fill_combo(self.background_combo, AI_IMAGE_BACKGROUND_PRESETS, settings.ai_image_background)
        self.background_combo.currentTextChanged.connect(self._save_inline_settings)

        self.count_spin = QSpinBox()
        self.count_spin.setRange(1, 4)
        self.count_spin.setValue(max(1, min(4, int(settings.ai_image_count or 1))))
        self.count_spin.valueChanged.connect(self._save_inline_settings)

        controls = QHBoxLayout()
        controls.setContentsMargins(0, 0, 0, 0)
        controls.setSpacing(6)
        controls.addWidget(QLabel("模型"))
        controls.addWidget(self.model_combo, 1)
        controls.addWidget(QLabel("尺寸"))
        controls.addWidget(self.size_combo)
        controls.addWidget(QLabel("质量"))
        controls.addWidget(self.quality_combo)

        controls2 = QHBoxLayout()
        controls2.setContentsMargins(0, 0, 0, 0)
        controls2.setSpacing(6)
        controls2.addWidget(QLabel("背景"))
        controls2.addWidget(self.background_combo)
        controls2.addWidget(QLabel("数量"))
        controls2.addWidget(self.count_spin)
        controls2.addStretch(1)

        self.preview = QLabel("未生成图片")
        self.preview.setObjectName("aiImagePreview")
        self.preview.setAlignment(Qt.AlignCenter)
        self.preview.setMinimumHeight(260)

        self.output_list = QListWidget()
        self.output_list.setObjectName("aiImageOutputList")
        self.output_list.setViewMode(QListWidget.IconMode)
        self.output_list.setIconSize(QSize(74, 74))
        self.output_list.setGridSize(QSize(88, 92))
        self.output_list.setMaximumHeight(104)
        self.output_list.currentItemChanged.connect(lambda _current, _previous: self._select_output_item())

        self.transcript = QTextBrowser()
        self.transcript.setOpenExternalLinks(True)
        self.transcript.setMaximumHeight(150)

        self.input = SubmitPlainTextEdit()
        self.input.setPlaceholderText("描述要生成的图片，例如：绿色史莱姆图标，透明背景，Q版游戏资产。")
        self.input.setFixedHeight(82)
        self.input.submitted.connect(self._send)
        self.input.imagePasted.connect(self._attach_clipboard_image)

        self.settings_button = QPushButton("生图设置")
        self.settings_button.clicked.connect(self._open_settings)
        self.save_button = QPushButton("保存为...")
        self.save_button.setEnabled(False)
        self.save_button.clicked.connect(self._save_current_image_as)
        self.open_folder_button = QPushButton("打开目录")
        self.open_folder_button.setEnabled(False)
        self.open_folder_button.clicked.connect(self._open_generated_folder)
        self.clear_button = QPushButton("清屏")
        self.clear_button.clicked.connect(self._clear_screen)
        self.send_button = QPushButton("生成")
        self.send_button.setObjectName("accentButton")
        self.send_button.clicked.connect(self._send)

        action_row = QHBoxLayout()
        action_row.setContentsMargins(0, 0, 0, 0)
        action_row.addWidget(self.settings_button)
        action_row.addWidget(self.save_button)
        action_row.addWidget(self.open_folder_button)
        action_row.addWidget(self.clear_button)
        action_row.addStretch(1)
        action_row.addWidget(self.send_button)

        left = QVBoxLayout()
        left.setContentsMargins(0, 0, 0, 0)
        left.setSpacing(8)
        left.addLayout(controls)
        left.addLayout(controls2)
        left.addWidget(self.preview, 1)
        left.addWidget(self.output_list)
        left.addWidget(self.transcript)
        left.addWidget(self.input)
        left.addLayout(action_row)

        self.reference_list = ReferenceImageList()
        self.reference_list.filesDropped.connect(self._add_reference_files)
        add_reference_button = QPushButton("添加")
        add_reference_button.clicked.connect(self._choose_reference_files)
        paste_reference_button = QPushButton("粘贴")
        paste_reference_button.clicked.connect(self._paste_clipboard_reference)
        remove_reference_button = QPushButton("移除")
        remove_reference_button.clicked.connect(self._remove_selected_references)
        clear_reference_button = QPushButton("清空")
        clear_reference_button.clicked.connect(self._clear_references)

        reference_buttons = QHBoxLayout()
        reference_buttons.setContentsMargins(0, 0, 0, 0)
        reference_buttons.setSpacing(6)
        reference_buttons.addWidget(add_reference_button)
        reference_buttons.addWidget(paste_reference_button)
        reference_buttons.addWidget(remove_reference_button)
        reference_buttons.addWidget(clear_reference_button)

        reference_title = QLabel("参考图")
        reference_title.setObjectName("aiAssistantTitle")
        right = QVBoxLayout()
        right.setContentsMargins(0, 0, 0, 0)
        right.setSpacing(8)
        right.addWidget(reference_title)
        right.addLayout(reference_buttons)
        right.addWidget(self.reference_list, 1)

        body = QHBoxLayout()
        body.setContentsMargins(0, 0, 0, 0)
        body.setSpacing(12)
        body.addLayout(left, 5)
        body.addLayout(right, 2)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 12, 14, 12)
        layout.setSpacing(10)
        layout.addLayout(title_row)
        layout.addWidget(self.header_label)
        layout.addLayout(body, 1)
        self._append_system("准备就绪。")
        self._refresh_header()

    def _fill_combo(self, combo: QComboBox, values: list[str], current: str) -> None:
        combo.addItems(values)
        index = combo.findText(current or values[0])
        combo.setCurrentIndex(index if index >= 0 else 0)

    def _refresh_header(self) -> None:
        provider = "OpenAI 官方" if self.settings.ai_image_provider == "openai" else "兼容 API"
        base_url = self.settings.ai_image_base_url.strip() if self.settings.ai_image_provider == "compatible" else "api.openai.com"
        if self.settings.ai_image_provider == "compatible" and base_url and "/v1" not in base_url.rstrip("/"):
            base_url = f"{base_url.rstrip('/')}/v1"
        self.header_label.setText(f"服务: {provider}    地址: {base_url}")

    def _save_inline_settings(self, *_args) -> None:
        self.settings.ai_image_model = self.model_combo.currentText().strip() or AI_IMAGE_MODEL_PRESETS[0]
        self.settings.ai_image_size = self.size_combo.currentText().strip() or "auto"
        self.settings.ai_image_quality = self.quality_combo.currentText().strip() or "auto"
        self.settings.ai_image_background = self.background_combo.currentText().strip() or "auto"
        self.settings.ai_image_count = self.count_spin.value()
        save_settings(self.settings)

    def _send(self) -> None:
        if self._thread is not None:
            return
        prompt = self.input.toPlainText().strip()
        if not prompt and not self.references:
            return
        if not prompt:
            prompt = "基于参考图生成一张新的游戏美术资产。"
        try:
            _context, _cwd, project_path = self.context_provider()
        except ValueError as exc:
            QMessageBox.information(self, "没有可用工程", str(exc))
            return
        self._save_inline_settings()
        try:
            request = build_ai_image_request(self.settings, prompt, [reference.path for reference in self.references])
        except AiImageError as exc:
            QMessageBox.information(self, "需要设置", str(exc))
            self._open_settings()
            return
        self._append_user(prompt)
        if self.references:
            self._append_system(f"已带入 {len(self.references)} 张参考图。")
        self.input.clear()
        self.send_button.setEnabled(False)
        self.send_button.setText("生成中")
        self._append_system(f"正在调用 {request.model} 生成图片...")
        thread = ImageGenerationThread(request, project_path, self)
        thread.succeeded.connect(self._generation_succeeded)
        thread.failed.connect(self._generation_failed)
        thread.finished.connect(self._thread_finished)
        self._thread = thread
        thread.start()

    def _generation_succeeded(self, saved_images: object) -> None:
        images = list(saved_images) if isinstance(saved_images, list) else []
        typed_images = [image for image in images if isinstance(image, SavedAiImage)]
        if not typed_images:
            self._append_system("生图服务没有返回可保存的图片。")
            return
        self.generated_images.extend(typed_images)
        for image in typed_images:
            self._add_output_item(image)
        last = typed_images[-1]
        self._append_ai(f"已生成 {len(typed_images)} 张图片，已保存到项目文件夹。")
        if last.revised_prompt:
            self._append_system(f"优化后的提示词：{last.revised_prompt}")
        self._select_generated_image(last.path)

    def _generation_failed(self, message: str) -> None:
        self._append_system(message or "生图失败。")
        QMessageBox.warning(self, "生图失败", message or "生图失败。")

    def _thread_finished(self) -> None:
        self._thread = None
        self.send_button.setEnabled(True)
        self.send_button.setText("生成")

    def _add_output_item(self, image: SavedAiImage) -> None:
        item = QListWidgetItem(image.path.name)
        item.setData(Qt.UserRole, str(image.path))
        pixmap = QPixmap(str(image.path))
        if not pixmap.isNull():
            item.setIcon(pixmap.scaled(74, 74, Qt.KeepAspectRatio, Qt.SmoothTransformation))
        self.output_list.addItem(item)
        self.output_list.setCurrentItem(item)

    def _select_output_item(self) -> None:
        item = self.output_list.currentItem()
        if item is None:
            return
        path = Path(str(item.data(Qt.UserRole) or ""))
        if path.exists():
            self._select_generated_image(path)

    def _select_generated_image(self, path: Path) -> None:
        self._current_image_path = path
        self._render_current_preview()
        self.save_button.setEnabled(True)
        self.open_folder_button.setEnabled(True)

    def _render_current_preview(self) -> None:
        if self._current_image_path is None:
            self.preview.setPixmap(QPixmap())
            self.preview.setText("未生成图片")
            return
        pixmap = QPixmap(str(self._current_image_path))
        if pixmap.isNull():
            self.preview.setPixmap(QPixmap())
            self.preview.setText("无法预览图片")
            return
        target = self.preview.size()
        self.preview.setText("")
        self.preview.setPixmap(pixmap.scaled(target, Qt.KeepAspectRatio, Qt.SmoothTransformation))

    def resizeEvent(self, event) -> None:  # type: ignore[override]
        super().resizeEvent(event)
        self._render_current_preview()

    def _choose_reference_files(self) -> None:
        paths, _ = QFileDialog.getOpenFileNames(
            self,
            "选择参考图",
            str(Path(self.settings.workspace_dir or ".").expanduser()),
            "图片 (*.png *.jpg *.jpeg *.webp)",
        )
        self._add_reference_files(paths)

    def _add_reference_files(self, paths: list[str]) -> None:
        try:
            _context, _cwd, project_path = self.context_provider()
        except ValueError as exc:
            QMessageBox.information(self, "没有可用工程", str(exc))
            return
        for path in paths:
            try:
                reference = save_ai_image_reference(project_path, path)
            except (OSError, AiImageError) as exc:
                QMessageBox.warning(self, "参考图失败", str(exc))
                continue
            self.references.append(reference)
            self._add_reference_item(reference)
        if paths:
            self._append_system(f"参考图数量：{len(self.references)}")

    def _paste_clipboard_reference(self) -> None:
        image = QApplication.clipboard().image()
        if image.isNull():
            QMessageBox.information(self, "没有图片", "剪贴板里没有可用图片。")
            return
        self._attach_clipboard_image(image)

    def _attach_clipboard_image(self, image: QImage) -> None:
        try:
            _context, _cwd, project_path = self.context_provider()
        except ValueError as exc:
            QMessageBox.information(self, "没有可用工程", str(exc))
            return
        try:
            reference = save_ai_image_reference_from_qimage(project_path, image)
        except AiImageError as exc:
            QMessageBox.warning(self, "参考图失败", str(exc))
            return
        self.references.append(reference)
        self._add_reference_item(reference)
        self._append_system(f"已添加参考图：{reference.path.name}")

    def _add_reference_item(self, reference: AiImageReference) -> None:
        item = QListWidgetItem(reference.path.name)
        item.setData(Qt.UserRole, str(reference.path))
        item.setToolTip(str(reference.path))
        pixmap = QPixmap(str(reference.path))
        if not pixmap.isNull():
            item.setIcon(pixmap.scaled(76, 76, Qt.KeepAspectRatio, Qt.SmoothTransformation))
        self.reference_list.addItem(item)

    def _remove_selected_references(self) -> None:
        rows = sorted((self.reference_list.row(item) for item in self.reference_list.selectedItems()), reverse=True)
        for row in rows:
            item = self.reference_list.takeItem(row)
            path = Path(str(item.data(Qt.UserRole) or ""))
            self.references = [reference for reference in self.references if reference.path != path]
        if rows:
            self._append_system(f"参考图数量：{len(self.references)}")

    def _clear_references(self) -> None:
        self.references = []
        self.reference_list.clear()
        self._append_system("已清空参考图。")

    def _save_current_image_as(self) -> None:
        if self._current_image_path is None:
            return
        start = Path(self.settings.export_dir or self._current_image_path.parent) / self._current_image_path.name
        path, _ = QFileDialog.getSaveFileName(
            self,
            "保存图片",
            str(start),
            "图片 (*.png *.jpg *.jpeg *.webp)",
        )
        if not path:
            return
        target = Path(path)
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(self._current_image_path, target)
        except OSError as exc:
            QMessageBox.warning(self, "保存失败", str(exc))
            return
        self._append_system(f"已保存：{target}")

    def _open_generated_folder(self) -> None:
        if self._current_image_path is not None:
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(self._current_image_path.parent)))

    def _open_settings(self) -> None:
        dialog = AiImageSettingsDialog(self, self.settings)
        if dialog.exec() == QDialog.Accepted:
            self.model_combo.setEditText(self.settings.ai_image_model or AI_IMAGE_MODEL_PRESETS[0])
            self._refresh_header()

    def _clear_screen(self) -> None:
        self.transcript.clear()
        self._append_system("已清空当前显示。")

    def _append_system(self, text: str) -> None:
        self.transcript.append(f"<p style='color:#8E8E93;'>{self._html(text)}</p>")
        self._scroll_transcript_to_bottom()

    def _append_user(self, text: str) -> None:
        self.transcript.append(f"<p><b>你</b><br>{self._html(text).replace(chr(10), '<br>')}</p>")
        self._scroll_transcript_to_bottom()

    def _append_ai(self, text: str) -> None:
        self.transcript.append(f"<p><b>生图</b><br>{self._html(text).replace(chr(10), '<br>')}</p>")
        self._scroll_transcript_to_bottom()

    def _scroll_transcript_to_bottom(self) -> None:
        bar = self.transcript.verticalScrollBar()
        bar.setValue(bar.maximum())

    def _html(self, text: str) -> str:
        return (
            text.replace("&", "&amp;")
            .replace("<", "&lt;")
            .replace(">", "&gt;")
            .replace('"', "&quot;")
        )
