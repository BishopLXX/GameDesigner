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

from ..ai_tools import (
    AI_MODEL_PRESETS,
    AiCanvasAction,
    AiChatMessage,
    build_ai_cli_invocation,
    build_ai_assistant_prompt,
    describe_ai_canvas_actions,
    invocation_with_last_message_output,
    load_project_chat_history,
    process_environment,
    qprocess_command,
    save_project_chat_history,
    split_ai_canvas_action_response,
)
from ..storage import AppSettings, save_settings
from ..window_layouts import restore_window_layout, save_window_layout
from .submit_text_edit import SubmitPlainTextEdit


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
        enabled = self.api_key_radio.isChecked()
        self.api_key_edit.setEnabled(enabled)
        self.base_url_edit.setEnabled(enabled)
        self.login_button.setEnabled(not enabled)

    def _save(self) -> None:
        self.settings.ai_provider = str(self.provider_combo.currentData() or "codex")
        self.settings.ai_model = self.model_combo.currentText().strip() or "gpt-5.4"
        self.settings.ai_auth_mode = "api_key" if self.api_key_radio.isChecked() else "official"
        self.settings.ai_api_key = self.api_key_edit.text().strip()
        self.settings.ai_base_url = self.base_url_edit.text().strip()
        save_settings(self.settings)
        self.accept()

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

        self.settings_button = QPushButton("AI 设置")
        self.settings_button.clicked.connect(self._open_settings)
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
        layout.addWidget(self.input)
        layout.addLayout(tools)
        self._append_system("AI 助手会带上当前工程、当前画布、选中对象和低权重参考文档；需要改画布时会自动把可执行操作应用到当前画布。")
        self._refresh_header()
        self._ensure_history_loaded()

    def _refresh_header(self) -> None:
        self.header_label.setText(f"工具: {self.settings.ai_provider}    模型: {self.settings.ai_model or '未设置'}")

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
        if not message or self._process is not None:
            return
        try:
            context, cwd, project_path = self.context_provider()
        except ValueError as exc:
            QMessageBox.information(self, "没有可用工程", str(exc))
            return
        self._ensure_history_loaded(project_path)
        prompt_message = self._iteration_prompt(message) if self._iteration_mode else message
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
        process.finished.connect(self._process_finished)
        self._process = process
        self._pending_user_message = message
        self._pending_output_chunks = []
        self._pending_error_chunks = []
        self._pending_output_file = output_file
        self._pending_actions = []
        self._clear_activity_log()
        self._append_user(message)
        self.input.clear()
        self.send_button.setEnabled(False)
        self.cancel_button.setEnabled(True)
        self._start_activity(f"{invocation.program} 正在思考")
        self._append_system(f"正在调用 {invocation.program}...")
        self._append_activity_lines(
            [
                f"已启动 {invocation.program} CLI",
                f"工作目录: {invocation.cwd}",
                f"模型: {self.settings.ai_model or '未设置'}",
            ],
            "运行状态",
        )
        process.start()
        if not process.waitForStarted(1500):
            self._process = None
            self.send_button.setEnabled(True)
            self.cancel_button.setEnabled(False)
            self._stop_activity("启动失败")
            QMessageBox.warning(self, "AI 启动失败", f"无法启动 {invocation.program}。请确认 CLI 已安装并可在 PATH 中使用。")
            self._cleanup_pending_output_file()
            return
        process.write(invocation.stdin.encode("utf-8"))
        process.closeWriteChannel()

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
            "如果选中节点有模板，新建节点必须沿用相同模板和字段结构；如果选中蓝图组，请可以参考其成员结构创建新的蓝图组和组内节点。\n"
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
        self._history = load_project_chat_history(project_path)
        self._render_history()

    def _render_history(self) -> None:
        self.transcript.clear()
        self._append_system("AI 助手会带上当前工程、当前画布、选中对象和低权重参考文档；需要改画布时会自动把可执行操作应用到当前画布。")
        if not self._history:
            self._append_system("当前项目还没有历史对话。")
            return
        self._append_system(f"已载入 {len(self._history)} 条历史消息。")
        for message in self._history:
            if message.role == "user":
                self._append_user(message.content)
            else:
                self._append_ai(message.content)

    def _trim_history(self) -> None:
        self._history = self._history[-24:]

    def _save_history(self) -> None:
        if self._history_project_path is not None:
            save_project_chat_history(self._history_project_path, self._history)

    def _clear_screen(self) -> None:
        self.transcript.clear()
        self._clear_activity_log()
        self._append_system("已清空当前屏幕显示，项目 AI 会话记忆仍保留。")
        self._pending_actions = []

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
