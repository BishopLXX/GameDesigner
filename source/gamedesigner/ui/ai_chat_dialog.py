from __future__ import annotations

import sys
import tempfile
from pathlib import Path
from typing import Callable

from PySide6.QtCore import QProcess, Qt
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
    QPushButton,
    QTextBrowser,
    QVBoxLayout,
    QWidget,
)

from ..ai_tools import (
    AI_MODEL_PRESETS,
    AiChatMessage,
    build_ai_cli_invocation,
    build_project_chat_prompt,
    invocation_with_last_message_output,
    load_project_chat_history,
    process_environment,
    qprocess_command,
    save_project_chat_history,
)
from ..storage import AppSettings, save_settings
from ..window_layouts import restore_window_layout, save_window_layout


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


class AiChatDialog(QDialog):
    def __init__(
        self,
        parent: QWidget | None,
        settings: AppSettings,
        context_provider: Callable[[], tuple[str, Path, Path]],
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("AI 工程聊天")
        self.setModal(False)
        self.settings = settings
        self.context_provider = context_provider
        self._process: QProcess | None = None
        self._history: list[AiChatMessage] = []
        self._history_project_path: Path | None = None
        self._pending_user_message = ""
        self._pending_output_chunks: list[str] = []
        self._pending_error_chunks: list[str] = []
        self._pending_output_file: Path | None = None

        self.header_label = QLabel()
        self.header_label.setObjectName("mutedLabel")
        self.transcript = QTextBrowser()
        self.transcript.setOpenExternalLinks(True)
        self.input = QPlainTextEdit()
        self.input.setPlaceholderText("询问当前工程，例如：帮我检查当前科技树节奏、给选中节点补字段、分析蓝图结构。")
        self.input.setFixedHeight(94)

        self.settings_button = QPushButton("AI 设置")
        self.settings_button.clicked.connect(self._open_settings)
        self.clear_button = QPushButton("清空记忆")
        self.clear_button.clicked.connect(self._clear_history)
        self.send_button = QPushButton("发送")
        self.send_button.setObjectName("accentButton")
        self.send_button.clicked.connect(self._send)
        self.cancel_button = QPushButton("停止")
        self.cancel_button.clicked.connect(self._cancel)
        self.cancel_button.setEnabled(False)
        close_button = QPushButton("关闭")
        close_button.clicked.connect(self.close)

        tools = QHBoxLayout()
        tools.addWidget(self.settings_button)
        tools.addWidget(self.clear_button)
        tools.addStretch(1)
        tools.addWidget(self.cancel_button)
        tools.addWidget(self.send_button)
        tools.addWidget(close_button)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 14)
        layout.setSpacing(10)
        layout.addWidget(self.header_label)
        layout.addWidget(self.transcript, 1)
        layout.addWidget(self.input)
        layout.addLayout(tools)
        self.resize(820, 640)
        restore_window_layout(self, "ai_chat_dialog")
        self._append_system("聊天会自动带上当前打开工程、当前画布和选中对象的概要，并记住本项目的历史对话。")
        self._refresh_header()
        self._ensure_history_loaded()

    def _refresh_header(self) -> None:
        self.header_label.setText(f"工具: {self.settings.ai_provider}    模型: {self.settings.ai_model or '未设置'}")

    def _append_system(self, text: str) -> None:
        self.transcript.append(f"<p style='color:#8E8E93;'>{self._html(text)}</p>")

    def _append_user(self, text: str) -> None:
        self.transcript.append(f"<p><b>你</b><br>{self._html(text).replace(chr(10), '<br>')}</p>")

    def _append_ai(self, text: str) -> None:
        self.transcript.append(f"<p><b>AI</b><br>{self._html(text).replace(chr(10), '<br>')}</p>")

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
        prompt = build_project_chat_prompt(context, message, self._history)
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
        self._append_user(message)
        self.input.clear()
        self.send_button.setEnabled(False)
        self.cancel_button.setEnabled(True)
        self._append_system(f"正在调用 {invocation.program}...")
        process.start()
        if not process.waitForStarted(1500):
            self._process = None
            self.send_button.setEnabled(True)
            self.cancel_button.setEnabled(False)
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

    def _read_stderr(self) -> None:
        if self._process is None:
            return
        text = bytes(self._process.readAllStandardError()).decode("utf-8", errors="replace")
        if text:
            self._pending_error_chunks.append(text)

    def _process_finished(self, exit_code: int, _status: QProcess.ExitStatus) -> None:
        ai_text = self._read_pending_output_file()
        if not ai_text:
            ai_text = self._clean_cli_output("".join(self._pending_output_chunks))
        if ai_text:
            self._append_ai(ai_text)
        if self._pending_user_message and ai_text:
            self._history.append(AiChatMessage("user", self._pending_user_message))
            self._history.append(AiChatMessage("assistant", ai_text))
            self._trim_history()
            self._save_history()
        elif exit_code != 0:
            error_text = "\n".join(chunk for chunk in self._pending_error_chunks if chunk.strip()).strip()
            if error_text:
                self._append_system(f"AI 进程失败，日志如下：\n{error_text}")
            else:
                self._append_system(f"AI 进程失败，退出码 {exit_code}。")
        elif not ai_text:
            self._append_system("AI 没有返回可显示的内容。")
        self._pending_user_message = ""
        self._pending_output_chunks = []
        self._pending_error_chunks = []
        self._cleanup_pending_output_file()
        self._process = None
        self.send_button.setEnabled(True)
        self.cancel_button.setEnabled(False)
        self._append_system("回复结束。")

    def _cancel(self) -> None:
        if self._process is not None:
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
        self._append_system("聊天会自动带上当前打开工程、当前画布和选中对象的概要，并记住本项目的历史对话。")
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

    def _clear_history(self) -> None:
        self._ensure_history_loaded()
        self._history.clear()
        self._save_history()
        self.transcript.clear()
        self._append_system("已清空当前项目的 AI 会话记忆。")

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

    def closeEvent(self, event) -> None:  # type: ignore[override]
        if self._process is not None:
            self._process.kill()
        self._cleanup_pending_output_file()
        save_window_layout(self, "ai_chat_dialog")
        super().closeEvent(event)
