from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .models import CanvasData, Edge, Node, ProjectData


@dataclass(frozen=True)
class AiCanvasToolSpec:
    name: str
    description: str
    mutates_canvas: bool
    required: tuple[str, ...] = ()
    optional: tuple[str, ...] = ()


@dataclass
class AiCanvasToolResult:
    tool: str
    success: bool
    message: str
    data: dict[str, Any] = field(default_factory=dict)


AI_CANVAS_TOOL_SPECS: tuple[AiCanvasToolSpec, ...] = (
    AiCanvasToolSpec(
        "create_node",
        "Create a node on the current canvas. Use reference_node_id when iterating an existing same-kind node.",
        True,
        required=("title",),
        optional=("reference_node_id", "template_id", "icon", "node_type", "x", "y", "width", "height", "color", "group_id", "fields"),
    ),
    AiCanvasToolSpec(
        "update_node",
        "Update an existing node by node_id.",
        True,
        required=("node_id",),
        optional=("title", "icon", "width", "height", "color", "fields"),
    ),
    AiCanvasToolSpec(
        "create_group",
        "Create a blueprint group and optionally create nodes inside it.",
        True,
        required=("title",),
        optional=("reference_group_id", "x", "y", "width", "height", "color", "nodes"),
    ),
    AiCanvasToolSpec(
        "update_canvas_rules",
        "Replace current canvas AI rules with the complete merged rule text.",
        True,
        required=("rules",),
    ),
    AiCanvasToolSpec(
        "create_edge",
        "Create a connection between two current-canvas nodes or groups.",
        True,
        required=("source_node_id", "target_node_id"),
        optional=("label", "style"),
    ),
    AiCanvasToolSpec(
        "update_edge_label",
        "Set or clear a short editable label on an existing edge.",
        True,
        optional=("edge_id", "label", "source_node_id", "target_node_id"),
    ),
    AiCanvasToolSpec(
        "query_canvas",
        "Read a compact summary of the current canvas. Does not mutate the project.",
        False,
        optional=("include_nodes", "include_edges"),
    ),
    AiCanvasToolSpec(
        "search_nodes",
        "Search current-canvas nodes by title, field name, field value, or note text. Does not mutate the project.",
        False,
        required=("query",),
        optional=("limit",),
    ),
    AiCanvasToolSpec(
        "validate_actions",
        "Validate the current GD_ACTIONS block against the current canvas before applying it.",
        False,
    ),
)

AI_CANVAS_TOOL_NAMES = {spec.name for spec in AI_CANVAS_TOOL_SPECS}
AI_MUTATING_TOOL_NAMES = {spec.name for spec in AI_CANVAS_TOOL_SPECS if spec.mutates_canvas}
AI_READ_ONLY_TOOL_NAMES = AI_CANVAS_TOOL_NAMES - AI_MUTATING_TOOL_NAMES


def ai_canvas_tool_protocol_text() -> str:
    lines = [
        "内部画布工具层：GD_ACTIONS 是受控工具调用，不是自由 JSON。只允许使用下列工具；所有写操作会先校验再由 GameDesigner 执行。",
        "可写工具会修改当前画布；只读工具只返回查询或校验结果，不会改工程。",
    ]
    for spec in AI_CANVAS_TOOL_SPECS:
        mode = "写" if spec.mutates_canvas else "读"
        required = f" required={','.join(spec.required)}" if spec.required else ""
        optional = f" optional={','.join(spec.optional)}" if spec.optional else ""
        lines.append(f"- {spec.name} [{mode}]: {spec.description}{required}{optional}")
    return "\n".join(lines) + "\n"


def validate_ai_canvas_tool_call(action: Any, canvas: CanvasData) -> AiCanvasToolResult:
    action_type = str(getattr(action, "type", "") or "")
    spec = next((item for item in AI_CANVAS_TOOL_SPECS if item.name == action_type), None)
    if spec is None:
        return AiCanvasToolResult(action_type or "unknown", False, f"未知工具：{action_type or '空'}")
    missing = [name for name in spec.required if not str(getattr(action, name, "") or "").strip()]
    if missing:
        return AiCanvasToolResult(action_type, False, f"{action_type} 缺少必填参数：{', '.join(missing)}")
    if action_type == "update_node" and canvas.find_node(str(getattr(action, "node_id", "") or "")) is None:
        return AiCanvasToolResult(action_type, False, f"update_node 找不到节点：{getattr(action, 'node_id', '')}")
    if action_type == "update_edge_label":
        edge_id = str(getattr(action, "edge_id", "") or "")
        source = str(getattr(action, "source_node_id", "") or "")
        target = str(getattr(action, "target_node_id", "") or "")
        if not edge_id and not (source and target):
            return AiCanvasToolResult(action_type, False, "update_edge_label 需要 edge_id，或 source_node_id + target_node_id。")
        if edge_id and not _find_edge(canvas, edge_id):
            return AiCanvasToolResult(action_type, False, f"update_edge_label 找不到连线：{edge_id}")
    return AiCanvasToolResult(action_type, True, f"{action_type} 参数有效")


def execute_read_only_ai_canvas_tool(action: Any, project: ProjectData, canvas: CanvasData) -> AiCanvasToolResult:
    action_type = str(getattr(action, "type", "") or "")
    if action_type == "query_canvas":
        return _query_canvas(project, canvas, action)
    if action_type == "search_nodes":
        return _search_nodes(canvas, action)
    if action_type == "validate_actions":
        return AiCanvasToolResult(action_type, True, "工具层可用；具体动作会在应用前按当前画布校验。")
    return AiCanvasToolResult(action_type, False, f"{action_type} 不是只读工具。")


def format_ai_tool_results(results: list[AiCanvasToolResult]) -> str:
    if not results:
        return ""
    return "\n".join(f"- {result.message}" for result in results)


def _query_canvas(project: ProjectData, canvas: CanvasData, action: Any) -> AiCanvasToolResult:
    include_nodes = bool(getattr(action, "include_nodes", False))
    include_edges = bool(getattr(action, "include_edges", False))
    data: dict[str, Any] = {
        "project": project.name,
        "canvas_id": canvas.id,
        "canvas_name": canvas.name,
        "canvas_type": canvas.canvas_type,
        "node_count": len(canvas.nodes),
        "edge_count": len(canvas.valid_edges()),
        "group_count": len(canvas.groups),
    }
    if include_nodes:
        data["nodes"] = [_node_result(node) for node in canvas.nodes[:80]]
    if include_edges:
        data["edges"] = [_edge_result(edge, canvas) for edge in canvas.valid_edges()[:120]]
    return AiCanvasToolResult(
        "query_canvas",
        True,
        f"当前画布“{canvas.name}”：节点 {len(canvas.nodes)} 个，连线 {len(canvas.valid_edges())} 条，蓝图组 {len(canvas.groups)} 个。",
        data,
    )


def _search_nodes(canvas: CanvasData, action: Any) -> AiCanvasToolResult:
    query = str(getattr(action, "query", "") or "").strip()
    limit = int(getattr(action, "limit", 12) or 12)
    limit = max(1, min(50, limit))
    if not query:
        return AiCanvasToolResult("search_nodes", False, "search_nodes 缺少 query。")
    matches = [
        _node_result(node)
        for node in canvas.nodes
        if _node_matches(node, query)
    ][:limit]
    if not matches:
        return AiCanvasToolResult("search_nodes", True, f"没有找到匹配“{query}”的节点。", {"query": query, "nodes": []})
    titles = "、".join(str(item["title"]) for item in matches[:8])
    suffix = f" 等 {len(matches)} 个" if len(matches) > 8 else ""
    return AiCanvasToolResult("search_nodes", True, f"搜索“{query}”：找到 {titles}{suffix}。", {"query": query, "nodes": matches})


def _node_matches(node: Node, query: str) -> bool:
    lowered = query.lower()
    haystacks = [node.title, node.id, node.node_type]
    haystacks.extend(field.name for field in node.fields)
    haystacks.extend(field.value for field in node.fields)
    haystacks.extend(note.title for note in node.notes)
    haystacks.extend(note.content for note in node.notes)
    return any(lowered in item.lower() for item in haystacks if item)


def _node_result(node: Node) -> dict[str, Any]:
    return {
        "id": node.id,
        "title": node.title,
        "node_type": node.normalized_node_type(),
        "x": node.x,
        "y": node.y,
        "width": node.width,
        "height": node.height,
        "fields": [
            {"id": field.id, "name": field.name, "data_type": field.data_type, "value": field.value}
            for field in node.fields
        ],
    }


def _edge_result(edge: Edge, canvas: CanvasData) -> dict[str, Any]:
    return {
        "id": edge.id,
        "source": edge.source,
        "source_title": _endpoint_title(edge.source, canvas),
        "target": edge.target,
        "target_title": _endpoint_title(edge.target, canvas),
        "label": edge.label,
        "style": edge.style,
    }


def _endpoint_title(endpoint_id: str, canvas: CanvasData) -> str:
    node = canvas.find_node(endpoint_id)
    if node is not None:
        return node.title
    group = canvas.find_group(endpoint_id)
    if group is not None:
        return group.title
    return endpoint_id


def _find_edge(canvas: CanvasData, edge_id: str) -> Edge | None:
    return next((edge for edge in canvas.edges if edge.id == edge_id), None)
