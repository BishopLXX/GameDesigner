from __future__ import annotations

import json
import re
import subprocess
import sys
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any

from PySide6.QtCore import QProcessEnvironment

from .models import FIELD_TYPES, NODE_TYPES, BlueprintGroup, CanvasData, Edge, Node, ProjectData
from .storage import AppSettings, project_bundle_dir


AI_PROVIDERS = {"codex", "claude"}
AI_AUTH_MODES = {"official", "api_key"}
AI_MODEL_PRESETS = ["gpt-5.4", "gpt-5.5", "gpt-5", "opus", "sonnet"]
AI_CHAT_DIR = "ai_chat"
AI_CHAT_HISTORY_FILE = "history.json"
AI_CHAT_HISTORY_LIMIT = 24
AI_LINKED_DOC_LIMIT = 8
AI_LINKED_DOC_CHARS = 1200
AI_ACTION_BLOCK_START = "【GD_ACTIONS】"
AI_ACTION_BLOCK_END = "【/GD_ACTIONS】"
AI_CANVAS_ACTION_TYPES = {"create_node", "update_node", "create_group", "update_canvas_rules"}


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


@dataclass
class AiCanvasFieldChange:
    name: str
    data_type: str = "长文本"
    value: str = ""


@dataclass
class AiCanvasAction:
    type: str
    title: str = ""
    node_id: str = ""
    rules: str = ""
    template_id: str = ""
    icon: str = ""
    node_type: str = "普通"
    x: float | None = None
    y: float | None = None
    width: float | None = None
    height: float | None = None
    color: str = ""
    group_id: str = ""
    fields: list[AiCanvasFieldChange] = field(default_factory=list)
    nodes: list["AiCanvasAction"] = field(default_factory=list)


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


def build_ai_assistant_prompt(context: str, user_message: str, history: list[AiChatMessage] | None = None) -> str:
    history = history or []
    history_text = _history_text(history[-AI_CHAT_HISTORY_LIMIT:])
    return (
        "你是 GameDesigner 内置 AI 助手。请基于下面的工程上下文帮助用户迭代当前设计工程。\n"
        "优先级规则：当前画布与当前选中对象最高；历史对话次之；低权重参考文档只用于补充灵感，不能覆盖当前画布事实。\n"
        "当用户从节点或蓝图组进入迭代助手模式时，必须优先参考当前选中对象；创建新节点时默认沿用选中节点的模板、字段结构和当前数据画布模板。\n"
        "如果用户要求参考某个蓝图组继续迭代，可以创建新的蓝图组，并在其中创建一批结构相近但内容迭代后的节点。\n"
        "你可以给出设计建议、文案、节点规划，也可以在用户要求改画布时输出可执行的画布动作。\n"
        "不要声称你自己直接改了工程文件；如果输出画布动作，GameDesigner 会在回复结束后自动应用到当前画布，不需要用户再点击确认。\n\n"
        "每个画布都有自己的规则记忆。当前画布规则记忆是高权重上下文，生成与迭代必须优先遵守；"
        "当用户说“记住、规则、以后都按、这个画布要遵守”等内容时，应优先输出 update_canvas_rules 动作写入当前画布规则。\n\n"
        "当需要创建或更新节点、蓝图组或当前画布规则时，请在自然语言回复后追加一个严格 JSON 动作块，格式如下：\n"
        f"{AI_ACTION_BLOCK_START}\n"
        "{\n"
        '  "actions": [\n'
        "    {\n"
        '      "type": "update_canvas_rules",\n'
        '      "rules": "当前画布需要长期遵守的规则，合并保留旧规则后写成清晰条目"\n'
        "    },\n"
        "    {\n"
        '      "type": "create_node",\n'
        '      "title": "节点标题",\n'
        '      "icon": "一到两个字",\n'
        '      "template_id": "可选，通常留空以继承当前选中节点模板",\n'
        '      "node_type": "普通",\n'
        '      "x": null,\n'
        '      "y": null,\n'
        '      "fields": [\n'
        '        {"name": "内容信息", "data_type": "长文本", "value": "节点内容"}\n'
        "      ]\n"
        "    },\n"
        "    {\n"
        '      "type": "create_group",\n'
        '      "title": "蓝图组标题",\n'
        '      "x": null,\n'
        '      "y": null,\n'
        '      "width": 640,\n'
        '      "height": 260,\n'
        '      "nodes": [\n'
        "        {\n"
        '          "type": "create_node",\n'
        '          "title": "组内节点",\n'
        '          "fields": [\n'
        '            {"name": "内容信息", "data_type": "长文本", "value": "节点内容"}\n'
        "          ]\n"
        "        }\n"
        "      ]\n"
        "    },\n"
        "    {\n"
        '      "type": "update_node",\n'
        '      "node_id": "node_xxx",\n'
        '      "title": "可选新标题",\n'
        '      "fields": [\n'
        '        {"name": "字段名", "data_type": "长文本", "value": "新内容"}\n'
        "      ]\n"
        "    }\n"
        "  ]\n"
        "}\n"
        f"{AI_ACTION_BLOCK_END}\n\n"
        "动作块要求：只使用 create_node、update_node、create_group 或 update_canvas_rules；create_group 的 nodes 只放 create_node；"
        "update_canvas_rules 只写入当前画布规则，rules 必须是完整合并后的规则文本，不要只写增量片段；"
        "update_node 必须使用上下文中真实存在的 node_id；"
        "字段 data_type 优先用 文本、长文本、整数、数字、布尔、枚举、日期、资源路径；"
        "如果是迭代新节点或新蓝图组，请直接输出动作块；如果用户只是咨询，不要输出动作块。\n\n"
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


def split_ai_canvas_action_response(text: str) -> tuple[str, list[AiCanvasAction], str]:
    errors: list[str] = []
    actions: list[AiCanvasAction] = []

    def parse_block(match: re.Match[str]) -> str:
        block = match.group(1)
        try:
            actions.extend(parse_ai_canvas_actions(block))
        except ValueError as exc:
            errors.append(str(exc))
        return ""

    visible = re.sub(
        rf"{re.escape(AI_ACTION_BLOCK_START)}(.*?){re.escape(AI_ACTION_BLOCK_END)}",
        parse_block,
        text,
        flags=re.DOTALL,
    ).strip()
    return visible, actions, "\n".join(errors)


def parse_ai_canvas_actions(text: str) -> list[AiCanvasAction]:
    raw = _extract_json_text(text)
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValueError(f"无法解析 AI 画布动作 JSON：{exc}") from exc
    items = data.get("actions") if isinstance(data, dict) else data
    if not isinstance(items, list):
        raise ValueError("AI 画布动作必须是列表，或包含 actions 列表的对象。")
    actions: list[AiCanvasAction] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        action = _ai_canvas_action_from_dict(item)
        if action is not None:
            actions.append(action)
    return actions


def describe_ai_canvas_actions(actions: list[AiCanvasAction]) -> list[str]:
    lines: list[str] = []
    for action in actions:
        if action.type == "create_node":
            field_text = f"，{len(action.fields)} 个字段" if action.fields else ""
            lines.append(f"创建节点：{action.title or '未命名节点'}{field_text}")
        elif action.type == "update_node":
            target = action.title or action.node_id or "未知节点"
            field_text = f"，更新 {len(action.fields)} 个字段" if action.fields else ""
            lines.append(f"更新节点：{target}{field_text}")
        elif action.type == "create_group":
            node_text = f"，{len(action.nodes)} 个组内节点" if action.nodes else ""
            lines.append(f"创建蓝图组：{action.title or '未命名蓝图组'}{node_text}")
        elif action.type == "update_canvas_rules":
            lines.append("写入当前画布规则记忆")
    return lines


def _extract_json_text(text: str) -> str:
    cleaned = text.strip()
    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned, flags=re.IGNORECASE)
        cleaned = re.sub(r"\s*```$", "", cleaned)
    starts = [index for index in (cleaned.find("{"), cleaned.find("[")) if index >= 0]
    if not starts:
        raise ValueError("AI 画布动作中没有找到 JSON。")
    start = min(starts)
    end = max(cleaned.rfind("}"), cleaned.rfind("]"))
    if end < start:
        raise ValueError("AI 画布动作 JSON 不完整。")
    return cleaned[start : end + 1]


def _ai_canvas_action_from_dict(raw: dict[str, Any]) -> AiCanvasAction | None:
    action_type = str(raw.get("type") or "").strip()
    if action_type not in AI_CANVAS_ACTION_TYPES:
        return None
    node_type = str(raw.get("node_type") or "普通").strip()
    if node_type not in NODE_TYPES:
        node_type = "普通"
    fields = _ai_canvas_fields_from_raw(raw.get("fields"))
    nodes = _ai_canvas_nodes_from_raw(raw.get("nodes")) if action_type == "create_group" else []
    return AiCanvasAction(
        type=action_type,
        title=str(raw.get("title") or "").strip(),
        node_id=str(raw.get("node_id") or "").strip(),
        rules=str(raw.get("rules") or raw.get("content") or "").strip(),
        template_id=str(raw.get("template_id") or "").strip(),
        icon=str(raw.get("icon") or "").strip()[:4],
        node_type=node_type,
        x=_optional_number(raw.get("x")),
        y=_optional_number(raw.get("y")),
        width=_optional_number(raw.get("width")),
        height=_optional_number(raw.get("height")),
        color=str(raw.get("color") or "").strip(),
        group_id=str(raw.get("group_id") or "").strip(),
        fields=fields,
        nodes=nodes,
    )


def _ai_canvas_nodes_from_raw(raw: Any) -> list[AiCanvasAction]:
    if not isinstance(raw, list):
        return []
    actions: list[AiCanvasAction] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        action = _ai_canvas_action_from_dict(item)
        if action is not None and action.type == "create_node":
            actions.append(action)
    return actions


def _ai_canvas_fields_from_raw(raw: Any) -> list[AiCanvasFieldChange]:
    if not isinstance(raw, list):
        return []
    fields: list[AiCanvasFieldChange] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name") or "").strip()
        if not name:
            continue
        data_type = str(item.get("data_type") or "长文本").strip()
        if data_type not in FIELD_TYPES:
            data_type = "长文本"
        fields.append(
            AiCanvasFieldChange(
                name=name,
                data_type=data_type,
                value=str(item.get("value") or ""),
            )
        )
    return fields


def _optional_number(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


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
    if canvas.ai_rules.strip():
        lines.append("")
        lines.append("当前画布规则记忆（高权重，生成与迭代必须优先遵守）:")
        lines.append(canvas.ai_rules.strip())

    selected_nodes = [node for node in canvas.nodes if node.id in selected_node_ids]
    selected_groups = [group for group in canvas.groups if group.id in selected_group_ids]
    selected_edge = next((edge for edge in canvas.edges if edge.id == selected_edge_id), None) if selected_edge_id else None
    if selected_nodes or selected_groups or selected_edge:
        lines.append("")
        lines.append("当前选中:")
        for node in selected_nodes[:8]:
            lines.append(f"- 节点: {_node_summary(node, project, detailed=True)}")
        for group in selected_groups[:4]:
            lines.extend(_group_detail_lines(group, canvas, project))
        if selected_edge:
            lines.append(f"- 连接: {_edge_summary(selected_edge, canvas)}")

    lines.append("")
    lines.append("当前画布节点概要:")
    for node in canvas.nodes[:60]:
        lines.append(f"- {_node_summary(node, project)}")
    if len(canvas.nodes) > 60:
        lines.append(f"- ... 还有 {len(canvas.nodes) - 60} 个节点未列出")

    if canvas.groups:
        lines.append("")
        lines.append("蓝图组概要:")
        for group in canvas.groups[:30]:
            members = [node for node in canvas.nodes if node.group_id == group.id]
            member_titles = ", ".join(node.title for node in members[:12])
            lines.append(
                f"- {group.title} ({group.id}), 位置 ({group.x:.0f}, {group.y:.0f}), "
                f"尺寸 ({group.width:.0f}x{group.height:.0f}): {member_titles or '暂无成员'}"
            )
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
            lines.append(f"- {template.name} ({template.id}): {fields or '无字段'}")
    other_canvas_lines = _other_canvas_summaries(project, canvas)
    if other_canvas_lines:
        lines.append("")
        lines.append("项目其他画布摘要（AI 可参考，当前画布优先）:")
        lines.extend(other_canvas_lines)
    if project_path:
        linked_docs = _linked_document_summaries(project, project_path)
        if linked_docs:
            lines.append("")
            lines.append("低权重参考文档摘要（仅供补充，当前画布与选中对象优先）:")
            lines.extend(linked_docs)
    return "\n".join(lines)


def _node_summary(node: Node, project: ProjectData | None = None, *, detailed: bool = False) -> str:
    field_limit = 12 if detailed else 6
    fields = ", ".join(_field_summary(field.to_dict()) for field in node.fields[:field_limit])
    template = project.find_template(node.template_id) if project and node.template_id else None
    template_text = f", 模板 {template.name}({template.id})" if template else (f", 模板ID {node.template_id}" if node.template_id else "")
    lock_text = ", 模板锁定" if node.template_locked else ""
    summary = (
        f"{node.title} ({node.id}), 类型 {node.normalized_node_type()}, "
        f"图标 {node.display_icon() or '无'}, 位置 ({node.x:.0f}, {node.y:.0f})"
        f"{template_text}{lock_text}"
    )
    if node.group_id:
        summary += f", 蓝图组 {node.group_id}"
    if fields:
        summary += f", 字段: {fields}"
    if detailed and len(node.fields) > field_limit:
        summary += f", 另有 {len(node.fields) - field_limit} 个字段"
    return summary


def _group_detail_lines(group: BlueprintGroup, canvas: CanvasData, project: ProjectData) -> list[str]:
    members = [node for node in canvas.nodes if node.group_id == group.id]
    lines = [
        (
            f"- 蓝图组: {group.title} ({group.id}), 位置 ({group.x:.0f}, {group.y:.0f}), "
            f"尺寸 ({group.width:.0f}x{group.height:.0f}), 成员节点 {len(members)} 个"
        )
    ]
    for node in members[:12]:
        lines.append(f"  - 组内节点: {_node_summary(node, project, detailed=True)}")
    if len(members) > 12:
        lines.append(f"  - ... 还有 {len(members) - 12} 个组内节点未列出")
    return lines


def _other_canvas_summaries(project: ProjectData, current_canvas: CanvasData) -> list[str]:
    lines: list[str] = []
    for canvas in project.canvases:
        if canvas.id == current_canvas.id:
            continue
        kind = "排序画布" if canvas.is_data_canvas() else "自由画布"
        summary = (
            f"- {canvas.name} ({canvas.id}), {kind}, 节点 {len(canvas.nodes)} 个, "
            f"蓝图组 {len(canvas.groups)} 个, 连接 {len(canvas.valid_edges())} 条"
        )
        if canvas.is_data_canvas():
            template = project.find_template(canvas.template_id)
            template_text = f", 模板 {template.name}({template.id})" if template else ""
            summary += f", 布局 {canvas.data_layout}{template_text}"
        lines.append(summary)
        for node in canvas.nodes[:8]:
            lines.append(f"  - {_node_summary(node, project)}")
        if len(canvas.nodes) > 8:
            lines.append(f"  - ... 还有 {len(canvas.nodes) - 8} 个节点未列出")
        if len(lines) >= 48:
            lines.append("- ... 其他画布摘要已截断")
            break
    return lines


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


def _linked_document_summaries(project: ProjectData, project_path: str | Path) -> list[str]:
    try:
        from .project_files.linked_documents import read_link_document
    except ImportError:
        return []
    summaries: list[str] = []
    seen: set[str] = set()
    for canvas in project.canvases:
        for node in canvas.nodes:
            if node.node_type != "超文本" or not node.link_path or node.link_path in seen:
                continue
            seen.add(node.link_path)
            try:
                content = read_link_document(project_path, node.link_path)
            except OSError:
                content = ""
            content = " ".join(content.split())
            if not content:
                continue
            if len(content) > AI_LINKED_DOC_CHARS:
                content = f"{content[:AI_LINKED_DOC_CHARS]}..."
            summaries.append(f"- {node.title} ({node.link_path}): {content}")
            if len(summaries) >= AI_LINKED_DOC_LIMIT:
                return summaries
    return summaries
