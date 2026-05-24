from __future__ import annotations

import json
import subprocess
import sys
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any

from PySide6.QtCore import QProcessEnvironment

from .models import CanvasData, Edge, Node, ProjectData
from .storage import AppSettings, project_bundle_dir


AI_PROVIDERS = {"codex", "claude"}
AI_AUTH_MODES = {"official", "api_key"}
AI_MODEL_PRESETS = ["gpt-5.4", "gpt-5.5", "gpt-5", "opus", "sonnet"]
AI_CHAT_DIR = "ai_chat"
AI_CHAT_HISTORY_FILE = "history.json"
AI_CHAT_HISTORY_LIMIT = 24


@dataclass
class AiCliInvocation:
    program: str
    arguments: list[str]
    stdin: str
    cwd: Path
    environment: dict[str, str] = field(default_factory=dict)


@dataclass
class AiChatMessage:
    role: str
    content: str

    def to_dict(self) -> dict[str, str]:
        return {
            "role": self.role if self.role in {"user", "assistant"} else "assistant",
            "content": self.content,
        }

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "AiChatMessage | None":
        role = str(raw.get("role") or "")
        content = str(raw.get("content") or "")
        if role not in {"user", "assistant"} or not content.strip():
            return None
        return cls(role=role, content=content)


def normalized_ai_provider(value: str) -> str:
    return value if value in AI_PROVIDERS else "codex"


def normalized_ai_auth_mode(value: str) -> str:
    return value if value in AI_AUTH_MODES else "official"


def project_chat_history_path(project_path: str | Path) -> Path:
    return project_bundle_dir(project_path) / AI_CHAT_DIR / AI_CHAT_HISTORY_FILE


def load_project_chat_history(project_path: str | Path) -> list[AiChatMessage]:
    path = project_chat_history_path(project_path)
    try:
        with path.open("r", encoding="utf-8") as file:
            raw = json.load(file)
    except (OSError, json.JSONDecodeError):
        return []
    if not isinstance(raw, list):
        return []
    messages: list[AiChatMessage] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        message = AiChatMessage.from_dict(item)
        if message is not None:
            messages.append(message)
    return messages[-AI_CHAT_HISTORY_LIMIT:]


def save_project_chat_history(project_path: str | Path, messages: list[AiChatMessage]) -> None:
    path = project_chat_history_path(project_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    trimmed = messages[-AI_CHAT_HISTORY_LIMIT:]
    with path.open("w", encoding="utf-8") as file:
        json.dump([message.to_dict() for message in trimmed], file, ensure_ascii=False, indent=2)


def build_project_chat_prompt(context: str, user_message: str, history: list[AiChatMessage] | None = None) -> str:
    history = history or []
    history_text = _history_text(history[-AI_CHAT_HISTORY_LIMIT:])
    return (
        "你是 GameDesigner 内置的当前工程 AI 助手。请基于下面的工程上下文回答用户。\n"
        "默认只提供设计建议、分析、文案、节点规划和可执行步骤；不要声称你已经直接修改了工程文件。\n"
        "如果需要用户在画布中操作，请给出简洁明确的操作建议。\n"
        "你可以参考历史对话保持连续性，但当前工程上下文优先级最高。\n\n"
        "【当前工程上下文】\n"
        f"{context.strip()}\n\n"
        "【历史对话】\n"
        f"{history_text}\n\n"
        "【用户问题】\n"
        f"{user_message.strip()}\n"
    )


def _history_text(history: list[AiChatMessage]) -> str:
    if not history:
        return "无"
    lines: list[str] = []
    for message in history:
        speaker = "用户" if message.role == "user" else "AI"
        content = message.content.strip()
        if len(content) > 3000:
            content = f"{content[:3000]}\n..."
        lines.append(f"{speaker}: {content}")
    return "\n\n".join(lines)


def build_ai_cli_invocation(settings: AppSettings, prompt: str, cwd: str | Path) -> AiCliInvocation:
    provider = normalized_ai_provider(settings.ai_provider)
    model = settings.ai_model.strip() or ("opus" if provider == "claude" else "gpt-5.4")
    cwd_path = Path(cwd)
    environment: dict[str, str] = {}
    if normalized_ai_auth_mode(settings.ai_auth_mode) == "api_key":
        key = settings.ai_api_key.strip()
        base_url = settings.ai_base_url.strip()
        if provider == "claude":
            if key:
                environment["ANTHROPIC_API_KEY"] = key
            if base_url:
                environment["ANTHROPIC_BASE_URL"] = base_url
        else:
            if key:
                environment["OPENAI_API_KEY"] = key
            if base_url:
                environment["OPENAI_BASE_URL"] = base_url
    if provider == "claude":
        return AiCliInvocation(
            program="claude",
            arguments=["--print", "--model", model],
            stdin=prompt,
            cwd=cwd_path,
            environment=environment,
        )
    return AiCliInvocation(
        program="codex",
        arguments=[
            "exec",
            "--skip-git-repo-check",
            "--sandbox",
            "read-only",
            "--color",
            "never",
            "-m",
            model,
            "-",
        ],
        stdin=prompt,
        cwd=cwd_path,
        environment=environment,
    )


def process_environment(extra: dict[str, str]) -> QProcessEnvironment:
    env = QProcessEnvironment.systemEnvironment()
    for key, value in extra.items():
        env.insert(key, value)
    return env


def qprocess_command(invocation: AiCliInvocation, platform: str | None = None) -> tuple[str, list[str]]:
    platform = platform or sys.platform
    if platform.startswith("win"):
        command = subprocess.list2cmdline([invocation.program, *invocation.arguments])
        return "cmd.exe", ["/d", "/s", "/c", command]
    return invocation.program, invocation.arguments


def invocation_with_last_message_output(invocation: AiCliInvocation, output_file: str | Path) -> AiCliInvocation:
    if invocation.program != "codex":
        return invocation
    arguments = list(invocation.arguments)
    insert_at = len(arguments) - 1 if arguments and arguments[-1] == "-" else len(arguments)
    arguments[insert_at:insert_at] = ["--output-last-message", str(output_file)]
    return replace(invocation, arguments=arguments)


def build_project_chat_context(
    project: ProjectData,
    canvas: CanvasData,
    project_path: str | Path | None = None,
    selected_node_ids: set[str] | None = None,
    selected_group_ids: set[str] | None = None,
    selected_edge_id: str | None = None,
) -> str:
    selected_node_ids = selected_node_ids or set()
    selected_group_ids = selected_group_ids or set()
    lines: list[str] = [
        f"项目名称: {project.name}",
        f"工程文件: {Path(project_path) if project_path else '尚未保存'}",
        f"源目录: {project.source_dir or '未设置'}",
        f"输出目录: {project.output_dir or '未设置'}",
        f"当前画布: {canvas.name} ({canvas.id})",
        f"画布类型: {'排序画布' if canvas.is_data_canvas() else '自由画布'}",
    ]
    if canvas.is_data_canvas():
        lines.append(f"排序布局: {canvas.data_layout}, 行样式: {canvas.data_row_style}, 每列行数: {canvas.data_grid_rows}")
    lines.extend(
        [
            f"当前画布节点数: {len(canvas.nodes)}",
            f"当前画布连接数: {len(canvas.edges)}",
            f"当前画布蓝图组数: {len(canvas.groups)}",
            f"项目画布总数: {len(project.canvases)}",
            f"节点模板数: {len(project.templates)}",
        ]
    )

    selected_nodes = [node for node in canvas.nodes if node.id in selected_node_ids]
    selected_groups = [group for group in canvas.groups if group.id in selected_group_ids]
    selected_edge = next((edge for edge in canvas.edges if edge.id == selected_edge_id), None) if selected_edge_id else None
    if selected_nodes or selected_groups or selected_edge:
        lines.append("")
        lines.append("当前选中:")
        for node in selected_nodes[:8]:
            lines.append(f"- 节点: {_node_summary(node, detailed=True)}")
        for group in selected_groups[:4]:
            member_count = sum(1 for node in canvas.nodes if node.group_id == group.id)
            lines.append(f"- 蓝图组: {group.title} ({group.id}), 成员节点 {member_count} 个")
        if selected_edge:
            lines.append(f"- 连接: {_edge_summary(selected_edge, canvas)}")

    lines.append("")
    lines.append("当前画布节点概要:")
    for node in canvas.nodes[:60]:
        lines.append(f"- {_node_summary(node)}")
    if len(canvas.nodes) > 60:
        lines.append(f"- ... 还有 {len(canvas.nodes) - 60} 个节点未列出")

    if canvas.groups:
        lines.append("")
        lines.append("蓝图组概要:")
        for group in canvas.groups[:30]:
            members = [node.title for node in canvas.nodes if node.group_id == group.id]
            lines.append(f"- {group.title}: {', '.join(members[:12]) or '暂无成员'}")
        if len(canvas.groups) > 30:
            lines.append(f"- ... 还有 {len(canvas.groups) - 30} 个蓝图组未列出")

    if canvas.edges:
        lines.append("")
        lines.append("连接概要:")
        for edge in canvas.valid_edges()[:80]:
            lines.append(f"- {_edge_summary(edge, canvas)}")
        if len(canvas.valid_edges()) > 80:
            lines.append(f"- ... 还有 {len(canvas.valid_edges()) - 80} 条连接未列出")

    if project.templates:
        lines.append("")
        lines.append("节点模板:")
        for template in project.templates[:20]:
            fields = ", ".join(field.name for field in template.fields[:8])
            lines.append(f"- {template.name}: {fields or '无字段'}")
    return "\n".join(lines)


def _node_summary(node: Node, *, detailed: bool = False) -> str:
    fields = ", ".join(_field_summary(field.to_dict()) for field in node.fields[:6])
    summary = (
        f"{node.title} ({node.id}), 类型 {node.normalized_node_type()}, "
        f"图标 {node.display_icon() or '无'}, 位置 ({node.x:.0f}, {node.y:.0f})"
    )
    if node.group_id:
        summary += f", 蓝图组 {node.group_id}"
    if fields:
        summary += f", 字段: {fields}"
    if detailed and len(node.fields) > 6:
        summary += f", 另有 {len(node.fields) - 6} 个字段"
    return summary


def _field_summary(raw: dict[str, Any]) -> str:
    name = str(raw.get("name") or "字段")
    data_type = str(raw.get("data_type") or "文本")
    value = str(raw.get("value") or raw.get("image_path") or "")
    value = value.replace("\n", " ").strip()
    if len(value) > 48:
        value = f"{value[:45]}..."
    return f"{name}/{data_type}={value or '空'}"


def _edge_summary(edge: Edge, canvas: CanvasData) -> str:
    source = _endpoint_title(edge.source, canvas)
    target = _endpoint_title(edge.target, canvas)
    label = f" [{edge.label}]" if edge.label else ""
    return f"{source} -> {target}{label}, 样式 {edge.style}"


def _endpoint_title(endpoint_id: str, canvas: CanvasData) -> str:
    node = canvas.find_node(endpoint_id)
    if node:
        return node.title
    group = canvas.find_group(endpoint_id)
    if group:
        return f"蓝图组:{group.title}"
    return endpoint_id
