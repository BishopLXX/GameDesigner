from __future__ import annotations

import sys
import tempfile
from pathlib import Path
from typing import Callable

from PySide6.QtCore import QProcess, Qt, QTimer, Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QTextBrowser,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from ..ai_attachments import AiImageAttachment, save_ai_chat_image_attachment
from ..ai_presets import (
    AI_CUSTOM_API_PROFILE_KEY,
    AI_FREE_MODEL_PRESETS,
    AI_OFFICIAL_PROFILE_KEY,
    ai_connection_snapshot,
    ai_profile_key_for_snapshot,
    free_model_preset_by_key,
)
from ..ai_tools import (
    AI_REASONING_EFFORTS,
    AI_MODEL_PRESETS,
    AiCanvasAction,
    AiChatMessage,
    build_ai_cli_invocation,
    build_ai_assistant_prompt,
    describe_ai_canvas_actions,
    invocation_with_last_message_output,
    load_project_chat_memory,
    process_environment,
    qprocess_command,
    save_project_chat_history,
    split_ai_canvas_action_response,
)
from ..storage import AppSettings, save_settings
from ..window_layouts import restore_window_layout, save_window_layout
from .submit_text_edit import SubmitPlainTextEdit


AI_MEMORY_PAGE_SIZE = 30


class AiMemoryDialog(QDialog):
    def __init__(self, parent: QWidget | None, project_path: Path, messages: list[AiChatMessage]) -> None:
        super().__init__(parent)
        self.setWindowTitle("AI 记忆")
        self.setModal(False)
        self.project_path = project_path
        self.messages = list(messages)
        self._loaded_from = len(self.messages)
        self._loading_previous = False

        self.summary_label = QLabel()
        self.summary_label.setObjectName("mutedLabel")
        self.summary_label.setWordWrap(True)

        self.load_previous_button = QPushButton("加载更早")
        self.load_previous_button.clicked.connect(self.load_previous_page)

        self.memory_view = QTextBrowser()
        self.memory_view.setOpenExternalLinks(False)
        self.memory_view.verticalScrollBar().valueChanged.connect(self._maybe_load_previous)

        close_button = QPushButton("关闭")
        close_button.clicked.connect(self.accept)

        top_row = QHBoxLayout()
        top_row.setContentsMargins(0, 0, 0, 0)
        top_row.addWidget(self.summary_label, 1)
        top_row.addWidget(self.load_previous_button)

        buttons = QHBoxLayout()
        buttons.setContentsMargins(0, 0, 0, 0)
        buttons.addStretch(1)
        buttons.addWidget(close_button)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(18, 18, 18, 16)
        layout.setSpacing(12)
        layout.addLayout(top_row)
        layout.addWidget(self.memory_view, 1)
        layout.addLayout(buttons)
        self.resize(760, 620)
        restore_window_layout(self, "ai_memory_dialog")
        self.load_previous_page(scroll_to_bottom=True)

    def load_previous_page(self, *, scroll_to_bottom: bool = False) -> None:
        if self._loaded_from <= 0:
            self._loaded_from = 0
            self._render_loaded_messages()
            bar = self.memory_view.verticalScrollBar()
            if scroll_to_bottom:
                bar.setValue(bar.maximum())
            return
        old_bar = self.memory_view.verticalScrollBar()
        old_maximum = old_bar.maximum()
        old_value = old_bar.value()
        self._loaded_from = max(0, self._loaded_from - AI_MEMORY_PAGE_SIZE)
        self._render_loaded_messages()
        bar = self.memory_view.verticalScrollBar()
        if scroll_to_bottom:
            bar.setValue(bar.maximum())
        else:
            bar.setValue(max(0, old_value + bar.maximum() - old_maximum))

    def _maybe_load_previous(self, value: int) -> None:
        if value > 0 or self._loaded_from <= 0 or self._loading_previous:
            return
        self._loading_previous = True
        try:
            self.load_previous_page()
        finally:
            self._loading_previous = False

    def _render_loaded_messages(self) -> None:
        self.memory_view.clear()
        loaded = self.messages[self._loaded_from :]
        if not loaded:
            self.memory_view.append("<p style='color:#8E8E93;'>当前项目还没有 AI 会话记忆。</p>")
        for index, message in enumerate(loaded, start=self._loaded_from + 1):
            speaker = "用户" if message.role == "user" else "AI"
            color = "#E8E8EA" if message.role == "user" else "#CFE1FF"
            content = self._html(message.content).replace(chr(10), "<br>")
            self.memory_view.append(
                f"<p><span style='color:#8E8E93;'>#{index}</span> "
                f"<b style='color:{color};'>{speaker}</b><br>{content}</p>"
            )
        self._refresh_summary()

    def _refresh_summary(self) -> None:
        loaded_count = len(self.messages) - self._loaded_from
        self.summary_label.setText(
            f"工程：{self.project_path.name}    已显示 {loaded_count}/{len(self.messages)} 条"
        )
        self.load_previous_button.setEnabled(self._loaded_from > 0)

    def _html(self, text: str) -> str:
        return (
            text.replace("&", "&amp;")
            .replace("<", "&lt;")
            .replace(">", "&gt;")
            .replace('"', "&quot;")
        )

    def done(self, result: int) -> None:  # type: ignore[override]
        save_window_layout(self, "ai_memory_dialog")
        super().done(result)


class AiSettingsDialog(QDialog):
    def __init__(self, parent: QWidget | None, settings: AppSettings) -> None:
        super().__init__(parent)
        self.setWindowTitle("AI 设置")
        self.setModal(True)
        self.settings = settings

        self.provider_combo = QComboBox()
        self.provider_combo.addItem("Codex CLI", "codex")
        self.provider_combo.addItem("Claude CLI", "claude")
        self.provider_combo.setCurrentIndex(max(0, self.provider_combo.findData(settings.ai_provider)))

        self.model_combo = QComboBox()
        self.model_combo.setEditable(True)
        self.model_combo.addItems(AI_MODEL_PRESETS)
        self.model_combo.setEditText(settings.ai_model or AI_MODEL_PRESETS[0])
        self.model_combo.setToolTip("模型名会原样传给 CLI。必须是当前 Provider 支持的真实模型 ID，例如 gpt-5.5。")

        self.reasoning_combo = QComboBox()
        self.reasoning_combo.addItems([
            "极快 / minimal",
            "快速 / low",
            "均衡 / medium",
            "聪明 / high",
            "最强 / xhigh",
        ])
        for index, effort in enumerate(AI_REASONING_EFFORTS):
            self.reasoning_combo.setItemData(index, effort)
        reasoning_index = self.reasoning_combo.findData(settings.ai_reasoning_effort)
        self.reasoning_combo.setCurrentIndex(reasoning_index if reasoning_index >= 0 else self.reasoning_combo.findData("xhigh"))
        self.reasoning_combo.setToolTip("Codex 的推理强度。模型名仍只填模型 ID，智能等级在这里单独设置。")

        self.free_model_combo = QComboBox()
        self.free_model_combo.addItem("不使用", "")
        self.free_model_combo.setItemData(0, "当前使用官方登录或手动 API Key 配置。", Qt.ToolTipRole)
        for preset in AI_FREE_MODEL_PRESETS:
            self.free_model_combo.addItem(preset.label, preset.key)
            self.free_model_combo.setItemData(self.free_model_combo.count() - 1, preset.description, Qt.ToolTipRole)
        self.free_model_apply_button = QPushButton("一键使用")
        self.free_model_apply_button.setToolTip("保存并切换到选中的免费模型配置")
        self.free_model_apply_button.clicked.connect(self._use_free_model_preset)

        self.official_profile_button = QPushButton("官方登录")
        self.official_profile_button.setToolTip("切回 Codex/Claude CLI 官方登录模式")
        self.official_profile_button.clicked.connect(self._use_official_profile)
        self.api_profile_button = QPushButton("上次 API")
        self.api_profile_button.setToolTip("恢复上次手动填写的 API Key 配置")
        self.api_profile_button.clicked.connect(self._use_custom_api_profile)

        self.official_radio = QCheckBox("使用 CLI 官方登录状态")
        self.api_key_radio = QCheckBox("使用第三方 API Key")
        self.official_radio.toggled.connect(self._sync_auth_checks)
        self.api_key_radio.toggled.connect(self._sync_auth_checks)
        if settings.ai_auth_mode == "api_key":
            self.api_key_radio.setChecked(True)
        else:
            self.official_radio.setChecked(True)

        self.api_key_edit = QLineEdit(settings.ai_api_key)
        self.api_key_edit.setEchoMode(QLineEdit.Password)
        self.api_key_edit.setPlaceholderText("OpenAI/Anthropic/第三方兼容服务的 API Key")
        self.base_url_edit = QLineEdit(settings.ai_base_url)
        self.base_url_edit.setPlaceholderText("可选，例如 https://api.openai.com/v1")
        self.login_button = QPushButton("打开官方登录")
        self.login_button.clicked.connect(self._open_official_login)

        form = QFormLayout()
        form.setFieldGrowthPolicy(QFormLayout.AllNonFixedFieldsGrow)
        form.addRow("工具", self.provider_combo)
        form.addRow("模型", self.model_combo)
        form.addRow("智能等级", self.reasoning_combo)
        form.addRow("免费模型", self._free_model_row())
        form.addRow("快速切回", self._profile_row())
        form.addRow("认证", self._auth_row())
        form.addRow("", self.login_button)
        form.addRow("API Key", self.api_key_edit)
        form.addRow("Base URL", self.base_url_edit)

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
        self.resize(560, 280)
        restore_window_layout(self, "ai_settings_dialog")
        self._refresh_auth_fields()
        self._sync_free_model_combo_to_snapshot(self._current_snapshot())

    def _free_model_row(self) -> QWidget:
        row = QWidget(self)
        layout = QHBoxLayout(row)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)
        layout.addWidget(self.free_model_combo, 1)
        layout.addWidget(self.free_model_apply_button)
        return row

    def _profile_row(self) -> QWidget:
        row = QWidget(self)
        layout = QHBoxLayout(row)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)
        layout.addWidget(self.official_profile_button)
        layout.addWidget(self.api_profile_button)
        layout.addStretch(1)
        return row

    def _auth_row(self) -> QWidget:
        row = QWidget(self)
        layout = QHBoxLayout(row)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self.official_radio)
        layout.addWidget(self.api_key_radio)
        layout.addStretch(1)
        return row

    def _sync_auth_checks(self, checked: bool) -> None:
        if not checked:
            if not self.official_radio.isChecked() and not self.api_key_radio.isChecked():
                self.official_radio.setChecked(True)
            self._refresh_auth_fields()
            return
        sender = self.sender()
        if sender is self.official_radio:
            self.api_key_radio.blockSignals(True)
            self.api_key_radio.setChecked(False)
            self.api_key_radio.blockSignals(False)
        elif sender is self.api_key_radio:
            self.official_radio.blockSignals(True)
            self.official_radio.setChecked(False)
            self.official_radio.blockSignals(False)
        self._refresh_auth_fields()

    def _refresh_auth_fields(self) -> None:
        if not hasattr(self, "api_key_edit"):
            return
        enabled = self.api_key_radio.isChecked()
        self.api_key_edit.setEnabled(enabled)
        self.base_url_edit.setEnabled(enabled)
        self.login_button.setEnabled(not enabled)

    def _save(self) -> None:
        snapshot = self._current_snapshot()
        self._store_snapshot(snapshot)
        self._write_settings(snapshot)
        save_settings(self.settings)
        self.accept()

    def _use_free_model_preset(self) -> None:
        preset = free_model_preset_by_key(str(self.free_model_combo.currentData() or ""))
        if preset is None:
            return
        self._store_snapshot(self._current_snapshot())
        stored = self.settings.ai_saved_connections.get(preset.key, {})
        snapshot = preset.to_snapshot(stored)
        if preset.needs_api_key and not snapshot["ai_api_key"] and self._same_base_url(self.base_url_edit.text(), preset.base_url):
            snapshot["ai_api_key"] = self.api_key_edit.text().strip()
        self._apply_snapshot_to_controls(snapshot)
        if preset.needs_api_key and not snapshot["ai_api_key"]:
            QMessageBox.information(self, "需要 API Key", "已填好模型和 Base URL。这个免费额度服务需要粘贴自己的 API Key 后再保存。")
            self.api_key_edit.setFocus(Qt.OtherFocusReason)
            return
        self._save()

    def _use_official_profile(self) -> None:
        self._store_snapshot(self._current_snapshot())
        snapshot = self.settings.ai_saved_connections.get(
            AI_OFFICIAL_PROFILE_KEY,
            ai_connection_snapshot(
                provider="codex",
                model="gpt-5.4",
                reasoning_effort=str(self.reasoning_combo.currentData() or "xhigh"),
                auth_mode="official",
                api_key="",
                base_url="",
            ),
        )
        self._apply_snapshot_to_controls(snapshot)
        self._save()

    def _use_custom_api_profile(self) -> None:
        self._store_snapshot(self._current_snapshot())
        snapshot = self.settings.ai_saved_connections.get(AI_CUSTOM_API_PROFILE_KEY)
        if snapshot is None:
            snapshot = ai_connection_snapshot(
                provider=str(self.provider_combo.currentData() or "codex"),
                model=self.model_combo.currentText().strip() or "gpt-5.4",
                reasoning_effort=str(self.reasoning_combo.currentData() or "xhigh"),
                auth_mode="api_key",
                api_key="",
                base_url="",
            )
        self._apply_snapshot_to_controls(snapshot)
        if not snapshot["ai_api_key"] and not snapshot["ai_base_url"]:
            QMessageBox.information(self, "填写 API", "已经切到 API Key 模式。填写 API Key 和 Base URL 后保存。")
            self.api_key_edit.setFocus(Qt.OtherFocusReason)
            return
        self._save()

    def _current_snapshot(self) -> dict[str, str]:
        return ai_connection_snapshot(
            provider=str(self.provider_combo.currentData() or "codex"),
            model=self.model_combo.currentText().strip() or "gpt-5.4",
            reasoning_effort=str(self.reasoning_combo.currentData() or "xhigh"),
            auth_mode="api_key" if self.api_key_radio.isChecked() else "official",
            api_key=self.api_key_edit.text().strip(),
            base_url=self.base_url_edit.text().strip(),
        )

    def _store_snapshot(self, snapshot: dict[str, str]) -> None:
        key = ai_profile_key_for_snapshot(snapshot)
        self.settings.ai_saved_connections[key] = dict(snapshot)

    def _write_settings(self, snapshot: dict[str, str]) -> None:
        self.settings.ai_provider = snapshot["ai_provider"]
        self.settings.ai_model = snapshot["ai_model"] or "gpt-5.4"
        self.settings.ai_reasoning_effort = snapshot.get("ai_reasoning_effort") or "xhigh"
        self.settings.ai_auth_mode = snapshot["ai_auth_mode"]
        if snapshot["ai_auth_mode"] == "official":
            self.settings.ai_api_key = ""
            self.settings.ai_base_url = ""
            return
        if snapshot["ai_api_key"] or not self.settings.ai_api_key:
            self.settings.ai_api_key = snapshot["ai_api_key"]
        if snapshot["ai_base_url"] or not self.settings.ai_base_url:
            self.settings.ai_base_url = snapshot["ai_base_url"]

    def _apply_snapshot_to_controls(self, snapshot: dict[str, str]) -> None:
        provider_index = self.provider_combo.findData(snapshot.get("ai_provider", "codex"))
        self.provider_combo.setCurrentIndex(provider_index if provider_index >= 0 else 0)
        self.model_combo.setEditText(snapshot.get("ai_model") or "gpt-5.4")
        reasoning_index = self.reasoning_combo.findData(snapshot.get("ai_reasoning_effort") or "xhigh")
        self.reasoning_combo.setCurrentIndex(reasoning_index if reasoning_index >= 0 else self.reasoning_combo.findData("xhigh"))
        self.api_key_edit.setText(snapshot.get("ai_api_key", ""))
        self.base_url_edit.setText(snapshot.get("ai_base_url", ""))
        if snapshot.get("ai_auth_mode") == "api_key":
            self.api_key_radio.setChecked(True)
        else:
            self.official_radio.setChecked(True)
        self._refresh_auth_fields()
        self._sync_free_model_combo_to_snapshot(snapshot)

    def _sync_free_model_combo_to_snapshot(self, snapshot: dict[str, str]) -> None:
        key = ai_profile_key_for_snapshot(snapshot)
        if key in {AI_OFFICIAL_PROFILE_KEY, AI_CUSTOM_API_PROFILE_KEY}:
            key = ""
        index = self.free_model_combo.findData(key)
        self.free_model_combo.setCurrentIndex(index if index >= 0 else 0)

    def _same_base_url(self, left: str, right: str) -> bool:
        return left.strip().rstrip("/") == right.strip().rstrip("/")

    def _open_official_login(self) -> None:
        provider = str(self.provider_combo.currentData() or "codex")
        program = "codex" if provider == "codex" else "claude"
        args = ["login"] if provider == "codex" else ["auth"]
        if sys.platform.startswith("win"):
            started = QProcess.startDetached("cmd.exe", ["/c", "start", "", program, *args])
        else:
            started = QProcess.startDetached(program, args)
        if not started:
            QMessageBox.warning(self, "无法打开登录", f"无法启动 {program} {' '.join(args)}。请确认 CLI 已安装并在 PATH 中。")

    def done(self, result: int) -> None:  # type: ignore[override]
        save_window_layout(self, "ai_settings_dialog")
        super().done(result)


class AiChatPanel(QWidget):
    collapseRequested = Signal()

    def __init__(
        self,
        parent: QWidget | None,
        settings: AppSettings,
        context_provider: Callable[[], tuple[str, Path, Path]],
        action_applier: Callable[[list[AiCanvasAction]], str] | None = None,
    ) -> None:
        super().__init__(parent)
        self.setObjectName("aiAssistantPanel")
        self.settings = settings
        self.context_provider = context_provider
        self.action_applier = action_applier
        self._process: QProcess | None = None
        self._history: list[AiChatMessage] = []
        self._history_project_path: Path | None = None
        self._pending_user_message = ""
        self._pending_output_chunks: list[str] = []
        self._pending_error_chunks: list[str] = []
        self._pending_output_file: Path | None = None
        self._pending_actions: list[AiCanvasAction] = []
        self._iteration_mode = False
        self._activity_base = "就绪"
        self._activity_step = 0
        self._activity_log_lines: list[str] = []
        self._pending_image_attachments: list[AiImageAttachment] = []

        title_label = QLabel("AI 助手")
        title_label.setObjectName("aiAssistantTitle")
        collapse_button = QToolButton(self)
        collapse_button.setObjectName("aiAssistantCollapseButton")
        collapse_button.setText("收起")
        collapse_button.setToolTip("收起 AI 助手")
        collapse_button.clicked.connect(self.collapseRequested.emit)
        title_row = QHBoxLayout()
        title_row.setContentsMargins(0, 0, 0, 0)
        title_row.addWidget(title_label)
        title_row.addStretch(1)
        title_row.addWidget(collapse_button)

        self.header_label = QLabel()
        self.header_label.setObjectName("mutedLabel")
        self.activity_label = QLabel("就绪")
        self.activity_label.setObjectName("aiAssistantActivityLabel")
        self.busy_bar = QProgressBar()
        self.busy_bar.setObjectName("aiAssistantBusyBar")
        self.busy_bar.setRange(0, 0)
        self.busy_bar.setTextVisible(False)
        self.busy_bar.setFixedHeight(4)
        self.busy_bar.hide()
        self.activity_timer = QTimer(self)
        self.activity_timer.setInterval(420)
        self.activity_timer.timeout.connect(self._tick_activity)
        self.activity_log = QPlainTextEdit()
        self.activity_log.setObjectName("aiAssistantActivityLog")
        self.activity_log.setReadOnly(True)
        self.activity_log.setFixedHeight(118)
        self.activity_log.setPlaceholderText("运行日志会显示在这里，并自动保留最近的内容。")
        self.transcript = QTextBrowser()
        self.transcript.setOpenExternalLinks(True)
        self.input = SubmitPlainTextEdit()
        self.input.setPlaceholderText("询问当前工程，例如：帮我检查当前科技树节奏、给选中节点补字段、分析蓝图结构。")
        self.input.setFixedHeight(94)
        self.input.submitted.connect(self._send)
        self.input.imagePasted.connect(self._attach_clipboard_image)
        self.attachments_label = QLabel()
        self.attachments_label.setObjectName("mutedLabel")
        self.attachments_label.setWordWrap(True)
        self.attachments_label.hide()

        self.settings_button = QPushButton("AI 设置")
        self.settings_button.clicked.connect(self._open_settings)
        self.memory_button = QPushButton("查阅记忆")
        self.memory_button.setToolTip("打开当前项目的 AI 会话记忆，向上滚动可继续加载更早内容")
        self.memory_button.clicked.connect(self._open_memory_view)
        self.clear_button = QPushButton("清空屏幕")
        self.clear_button.setToolTip("只清空当前显示，不删除本项目的 AI 会话记忆")
        self.clear_button.clicked.connect(self._clear_screen)
        self.send_button = QPushButton("发送")
        self.send_button.setObjectName("accentButton")
        self.send_button.clicked.connect(self._send)
        self.cancel_button = QPushButton("停止")
        self.cancel_button.clicked.connect(self._cancel)
        self.cancel_button.setEnabled(False)
        close_button = QPushButton("收起")
        close_button.clicked.connect(self.collapseRequested.emit)

        tools = QHBoxLayout()
        tools.addWidget(self.settings_button)
        tools.addWidget(self.memory_button)
        tools.addWidget(self.clear_button)
        tools.addStretch(1)
        tools.addWidget(self.cancel_button)
        tools.addWidget(self.send_button)
        tools.addWidget(close_button)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 12, 14, 12)
        layout.setSpacing(10)
        layout.addLayout(title_row)
        layout.addWidget(self.header_label)
        layout.addWidget(self.activity_label)
        layout.addWidget(self.busy_bar)
        layout.addWidget(self.activity_log)
        layout.addWidget(self.transcript, 1)
        layout.addWidget(self.attachments_label)
        layout.addWidget(self.input)
        layout.addLayout(tools)
        self._append_system("AI 助手会带上当前工程、当前画布、选中对象和低权重参考文档；需要改画布时会自动把可执行操作应用到当前画布。")
        self._refresh_header()
        self._ensure_history_loaded()

    def _refresh_header(self) -> None:
        reasoning = getattr(self.settings, "ai_reasoning_effort", "xhigh") or "xhigh"
        self.header_label.setText(
            f"工具: {self.settings.ai_provider}    模型: {self.settings.ai_model or '未设置'}    智能: {reasoning}"
        )

    def _start_activity(self, text: str) -> None:
        self._activity_base = text.strip() or "正在思考"
        self._activity_step = 0
        self.activity_label.setText(self._activity_base)
        self.busy_bar.show()
        if not self.activity_timer.isActive():
            self.activity_timer.start()

    def _tick_activity(self) -> None:
        self._activity_step = (self._activity_step + 1) % 4
        dots = "." * self._activity_step
        self.activity_label.setText(f"{self._activity_base}{dots}")

    def _stop_activity(self, text: str = "就绪") -> None:
        self.activity_timer.stop()
        self._activity_base = text.strip() or "就绪"
        self.activity_label.setText(self._activity_base)
        self.busy_bar.hide()

    def enter_iteration_mode(self) -> None:
        self._iteration_mode = True
        self.input.setPlaceholderText("迭代当前选中节点或蓝图组，例如：基于它继续做 3 个同模板节点，或参考这个蓝图组做一个新蓝图组。")
        self.input.setPlainText("基于当前选中对象继续迭代，直接在当前画布创建新的节点或蓝图组。")
        self.input.selectAll()
        self._append_system("已进入迭代助手：会优先参考当前选中节点/蓝图组；如果生成画布动作，会自动创建到当前画布。")

    def _append_system(self, text: str) -> None:
        self.transcript.append(f"<p style='color:#8E8E93;'>{self._html(text)}</p>")
        self._scroll_transcript_to_bottom()

    def _append_user(self, text: str) -> None:
        self.transcript.append(f"<p><b>你</b><br>{self._html(text).replace(chr(10), '<br>')}</p>")
        self._scroll_transcript_to_bottom()

    def _append_ai(self, text: str) -> None:
        self.transcript.append(f"<p><b>AI</b><br>{self._html(text).replace(chr(10), '<br>')}</p>")
        self._scroll_transcript_to_bottom()

    def _scroll_transcript_to_bottom(self) -> None:
        bar = self.transcript.verticalScrollBar()
        bar.setValue(bar.maximum())

    def _send(self) -> None:
        message = self.input.toPlainText().strip()
        attachments = list(self._pending_image_attachments)
        if (not message and not attachments) or self._process is not None:
            return
        try:
            context, cwd, project_path = self.context_provider()
        except ValueError as exc:
            QMessageBox.information(self, "没有可用工程", str(exc))
            return
        self._ensure_history_loaded(project_path)
        if not message and attachments:
            message = "请分析这张图片。"
        message_with_attachments = self._message_with_image_attachments(message, attachments)
        prompt_message = self._iteration_prompt(message_with_attachments) if self._iteration_mode else message_with_attachments
        prompt = build_ai_assistant_prompt(context, prompt_message, self._history)
        invocation = build_ai_cli_invocation(self.settings, prompt, cwd)
        output_file = self._new_output_file() if invocation.program == "codex" else None
        if output_file is not None:
            invocation = invocation_with_last_message_output(invocation, output_file)
        program, arguments = qprocess_command(invocation)
        process = QProcess(self)
        process.setProgram(program)
        process.setArguments(arguments)
        process.setWorkingDirectory(str(invocation.cwd))
        process.setProcessEnvironment(process_environment(invocation.environment))
        process.readyReadStandardOutput.connect(self._read_stdout)
        process.readyReadStandardError.connect(self._read_stderr)
        process.errorOccurred.connect(self._process_error)
        process.finished.connect(self._process_finished)
        self._process = process
        self._pending_user_message = prompt_message
        self._pending_output_chunks = []
        self._pending_error_chunks = []
        self._pending_output_file = output_file
        self._pending_actions = []
        self._clear_activity_log()
        self._append_user(self._display_user_message(message, attachments))
        self.input.clear()
        self._pending_image_attachments = []
        self._refresh_attachments_label()
        self.send_button.setEnabled(False)
        self.cancel_button.setEnabled(True)
        self._start_activity(f"{invocation.program} 正在思考")
        self._append_system(f"正在调用 {invocation.program}...")
        self._append_activity_lines(
            [
                f"已启动 {invocation.program} CLI",
                f"工作目录: {invocation.cwd}",
                f"模型: {self.settings.ai_model or '未设置'}",
                f"智能等级: {getattr(self.settings, 'ai_reasoning_effort', 'xhigh') or 'xhigh'}",
                f"图片附件: {len(attachments)}" if attachments else "",
                f"程序: {program}",
            ],
            "运行状态",
        )
        process.start()
        if not process.waitForStarted(1500):
            error_text = process.errorString().strip() or "未知错误"
            self._process = None
            self.send_button.setEnabled(True)
            self.cancel_button.setEnabled(False)
            self._stop_activity("启动失败")
            self._append_system(f"无法启动 {invocation.program}。\n程序：{program}\n错误：{error_text}")
            QMessageBox.warning(
                self,
                "AI 启动失败",
                f"无法启动 {invocation.program}。\n\n程序：{program}\n错误：{error_text}\n\n"
                "请确认 CLI 已安装，并且可执行文件在 PATH 中。",
            )
            self._cleanup_pending_output_file()
            return
        process.write(invocation.stdin.encode("utf-8"))
        process.closeWriteChannel()

    def _process_error(self, _error: QProcess.ProcessError) -> None:
        if self._process is None:
            return
        error_text = self._process.errorString().strip()
        if not error_text:
            return
        self._pending_error_chunks.append(error_text)
        self._append_activity_lines([error_text], "进程错误")

    def _attach_clipboard_image(self, image) -> None:
        if image.isNull():
            return
        try:
            _context, _cwd, project_path = self.context_provider()
        except ValueError as exc:
            QMessageBox.information(self, "没有可用工程", str(exc))
            return
        try:
            attachment = save_ai_chat_image_attachment(project_path, image)
        except (OSError, ValueError) as exc:
            QMessageBox.warning(self, "图片粘贴失败", str(exc))
            return
        self._pending_image_attachments.append(attachment)
        self._refresh_attachments_label()
        self._append_system(f"已附加图片：{attachment.path.name}（{attachment.width}x{attachment.height}）")

    def _refresh_attachments_label(self) -> None:
        if not self._pending_image_attachments:
            self.attachments_label.clear()
            self.attachments_label.hide()
            return
        names = [attachment.path.name for attachment in self._pending_image_attachments]
        self.attachments_label.setText(f"已附加图片：{'、'.join(names)}")
        self.attachments_label.show()

    def _message_with_image_attachments(self, message: str, attachments: list[AiImageAttachment]) -> str:
        if not attachments:
            return message
        lines = [message.strip(), "", "【用户附加图片】"]
        for index, attachment in enumerate(attachments, start=1):
            lines.append(f"{index}. 文件: {attachment.path.resolve()}")
            lines.append(f"   尺寸: {attachment.width}x{attachment.height}")
        lines.append("请先读取或查看这些本地图片，再结合当前工程上下文回答；如果当前 CLI 或模型无法读取图片，请明确说明，不要编造图片内容。")
        return "\n".join(line for line in lines if line)

    def _display_user_message(self, message: str, attachments: list[AiImageAttachment]) -> str:
        if not attachments:
            return message
        names = "、".join(attachment.path.name for attachment in attachments)
        return f"{message.strip()}\n[图片附件: {names}]"

    def _read_stdout(self) -> None:
        if self._process is None:
            return
        text = bytes(self._process.readAllStandardOutput()).decode("utf-8", errors="replace")
        if text:
            self._pending_output_chunks.append(text)
            if self._pending_output_file is not None:
                self._append_activity_from_chunk(text, "输出")
            else:
                self._start_activity("AI 正在输出回复")

    def _read_stderr(self) -> None:
        if self._process is None:
            return
        text = bytes(self._process.readAllStandardError()).decode("utf-8", errors="replace")
        if text:
            self._pending_error_chunks.append(text)
            self._append_activity_from_chunk(text, "运行日志")

    def _process_finished(self, exit_code: int, _status: QProcess.ExitStatus) -> None:
        ai_text = self._read_pending_output_file()
        if not ai_text:
            ai_text = self._clean_cli_output("".join(self._pending_output_chunks))
        visible_text, actions, action_error = split_ai_canvas_action_response(ai_text) if ai_text else ("", [], "")
        if visible_text:
            self._append_ai(visible_text)
        if action_error:
            self._append_system(action_error)
        if actions:
            self._auto_apply_actions(actions)
        history_ai_text = visible_text.strip()
        if not history_ai_text and actions:
            history_ai_text = "已生成画布操作：\n" + "\n".join(f"- {line}" for line in describe_ai_canvas_actions(actions))
        if self._pending_user_message and history_ai_text:
            self._history.append(AiChatMessage("user", self._pending_user_message))
            self._history.append(AiChatMessage("assistant", history_ai_text))
            self._trim_history()
            self._save_history()
        elif exit_code != 0:
            error_text = "\n".join(chunk for chunk in self._pending_error_chunks if chunk.strip()).strip()
            if error_text:
                self._append_system(f"AI 进程失败，日志如下：\n{error_text}")
            else:
                self._append_system(f"AI 进程失败，退出码 {exit_code}。")
        elif not visible_text and not actions:
            self._append_system("AI 没有返回可显示的内容。")
        self._pending_user_message = ""
        self._pending_output_chunks = []
        self._pending_error_chunks = []
        self._cleanup_pending_output_file()
        self._process = None
        self.send_button.setEnabled(True)
        self.cancel_button.setEnabled(False)
        self._stop_activity("就绪")
        self._append_system("回复结束。")

    def _append_activity_from_chunk(self, text: str, title: str) -> None:
        lines = self._activity_lines_from_chunk(text)
        if lines:
            self._append_activity_lines(lines, title)
        elif text.strip():
            self._start_activity("AI 仍在运行")

    def _activity_lines_from_chunk(self, text: str) -> list[str]:
        keywords = (
            "thinking",
            "reasoning",
            "running",
            "exec",
            "command",
            "tool",
            "mcp",
            "error",
            "warning",
            "deprecated",
            "tokens used",
            "codex",
            "claude",
            "正在",
            "运行",
            "思考",
            "调用",
            "读取",
            "写入",
            "失败",
            "错误",
            "警告",
        )
        hidden_prefixes = (
            "user ",
            "system ",
            "developer ",
            "assistant ",
            "【当前工程上下文】",
            "【历史对话】",
            "【用户问题】",
        )
        lines: list[str] = []
        for raw_line in text.replace("\r", "\n").splitlines():
            line = raw_line.strip()
            if not line or line.startswith(hidden_prefixes):
                continue
            if len(line) > 360:
                line = f"{line[:357]}..."
            lowered = line.lower()
            if any(keyword in lowered for keyword in keywords):
                lines.append(line)
            if len(lines) >= 10:
                break
        return lines

    def _append_activity_lines(self, lines: list[str], title: str) -> None:
        lines = [line for line in lines if line]
        if not lines:
            return
        self._activity_log_lines.append(f"[{title}]")
        self._activity_log_lines.extend(lines)
        self._activity_log_lines = self._activity_log_lines[-48:]
        self.activity_log.setPlainText("\n".join(self._activity_log_lines))
        bar = self.activity_log.verticalScrollBar()
        bar.setValue(bar.maximum())

    def _clear_activity_log(self) -> None:
        self._activity_log_lines = []
        self.activity_log.clear()

    def _iteration_prompt(self, message: str) -> str:
        return (
            "【迭代助手模式】\n"
            "用户是从当前画布的节点或蓝图组右键进入的。请优先基于当前选中对象迭代新内容。\n"
            "如果选中节点有模板，新建节点必须沿用相同模板和字段结构；如果选中蓝图组，请把它当作结构蓝图来克隆，新蓝图组必须优先继承成员顺序、相对位置、组内连线和字段结构。\n"
            "请在自然语言后输出动作块，让 GameDesigner 自动创建或更新画布内容。\n\n"
            f"用户输入：{message.strip()}"
        )

    def _cancel(self) -> None:
        if self._process is not None:
            self._append_system("正在停止 AI...")
            self._start_activity("正在停止 AI")
            self._process.kill()

    def _open_settings(self) -> None:
        dialog = AiSettingsDialog(self, self.settings)
        if dialog.exec() == QDialog.Accepted:
            self._refresh_header()

    def _ensure_history_loaded(self, project_path: Path | None = None) -> None:
        if project_path is None:
            try:
                _context, _cwd, project_path = self.context_provider()
            except ValueError:
                return
        if self._history_project_path == project_path:
            return
        self._history_project_path = project_path
        self._history = load_project_chat_memory(project_path)
        self._render_history()

    def _render_history(self) -> None:
        self.transcript.clear()
        self._append_system("AI 助手会带上当前工程、当前画布、选中对象和低权重参考文档；需要改画布时会自动把可执行操作应用到当前画布。")
        if not self._history:
            self._append_system("当前项目还没有历史对话。")
            return
        visible_history = self._history[-24:]
        hidden_count = max(0, len(self._history) - len(visible_history))
        self._append_system(f"已载入 {len(self._history)} 条历史消息，当前显示最近 {len(visible_history)} 条。")
        if hidden_count > 0:
            self._append_system(f"更早的 {hidden_count} 条可通过“查阅记忆”打开查看。")
        for message in visible_history:
            if message.role == "user":
                self._append_user(message.content)
            else:
                self._append_ai(message.content)

    def _trim_history(self) -> None:
        return

    def _save_history(self) -> None:
        if self._history_project_path is not None:
            save_project_chat_history(self._history_project_path, self._history)

    def _clear_screen(self) -> None:
        self.transcript.clear()
        self._clear_activity_log()
        self._append_system("已清空当前屏幕显示，项目 AI 会话记忆仍保留。")
        self._pending_actions = []

    def _open_memory_view(self) -> None:
        try:
            _context, _cwd, project_path = self.context_provider()
        except ValueError as exc:
            QMessageBox.information(self, "没有可用工程", str(exc))
            return
        memory = load_project_chat_memory(project_path)
        dialog = AiMemoryDialog(self, project_path, memory)
        self._memory_dialog = dialog
        dialog.show()
        dialog.raise_()
        dialog.activateWindow()

    def _html(self, text: str) -> str:
        return (
            text.replace("&", "&amp;")
            .replace("<", "&lt;")
            .replace(">", "&gt;")
            .replace('"', "&quot;")
        )

    def _new_output_file(self) -> Path:
        handle = tempfile.NamedTemporaryFile(prefix="gamedesigner_ai_", suffix=".md", delete=False)
        path = Path(handle.name)
        handle.close()
        return path

    def _read_pending_output_file(self) -> str:
        if self._pending_output_file is None:
            return ""
        try:
            return self._pending_output_file.read_text(encoding="utf-8").strip()
        except OSError:
            return ""

    def _cleanup_pending_output_file(self) -> None:
        if self._pending_output_file is None:
            return
        try:
            self._pending_output_file.unlink(missing_ok=True)
        except OSError:
            pass
        self._pending_output_file = None

    def _clean_cli_output(self, text: str) -> str:
        text = text.strip()
        if not text:
            return ""
        if "\ncodex " in text:
            return text.rsplit("\ncodex ", 1)[1].strip()
        if text.startswith("codex "):
            return text.removeprefix("codex ").strip()
        return text

    def _append_action_preview(self, actions: list[AiCanvasAction]) -> None:
        lines = describe_ai_canvas_actions(actions)
        if not lines:
            return
        html_lines = "<br>".join(self._html(f"- {line}") for line in lines)
        self.transcript.append(
            "<p style='color:#5E8FD6;'><b>已识别画布操作</b><br>"
            f"{html_lines}</p>"
        )
        self._scroll_transcript_to_bottom()

    def _auto_apply_actions(self, actions: list[AiCanvasAction]) -> None:
        if not actions:
            return
        self._pending_actions = list(actions)
        self._append_action_preview(actions)
        if self.action_applier is None:
            QMessageBox.information(self, "无法应用", "当前窗口没有连接到画布操作器。")
            return
        try:
            message = self.action_applier(list(actions))
        except ValueError as exc:
            QMessageBox.warning(self, "无法应用", str(exc))
            return
        self._append_system(message)
        self._pending_actions = []

    def closeEvent(self, event) -> None:  # type: ignore[override]
        if self._process is not None:
            self._process.kill()
        self._cleanup_pending_output_file()
        super().closeEvent(event)
