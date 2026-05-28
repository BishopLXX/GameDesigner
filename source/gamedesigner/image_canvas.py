from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from .models import CanvasData, Edge, Node, NodeField, ProjectData


IMAGE_CANVAS_ENTRY_TITLE = "入口"
IMAGE_CANVAS_OUTPUT_TITLE = "输出"
IMAGE_CANVAS_EDIT_TITLE = "修图输出"
IMAGE_CANVAS_PROMPT_FIELD = "生成提示词"
IMAGE_CANVAS_IMAGE_FIELD = "生成图片"
IMAGE_CANVAS_REFERENCE_EDGE_LABEL = "参考图"
IMAGE_CANVAS_EDIT_EDGE_LABEL = "修图参考"
IMAGE_CANVAS_LINKED_DOC_LIMIT = 8
IMAGE_CANVAS_LINKED_DOC_CHARS = 6000


@dataclass
class ImageCanvasRequestData:
    prompt: str
    reference_paths: list[Path] = field(default_factory=list)
    output_node_id: str = ""
    prompt_lines: list[str] = field(default_factory=list)


def canvas_type_label(canvas: CanvasData) -> str:
    if canvas.is_data_canvas():
        return "排序画布"
    if canvas.is_pixel_canvas():
        return "像素作画画布"
    if canvas.is_image_canvas():
        return "生图画布"
    return "自由画布"


def image_canvas_prompt_protocol() -> str:
    return (
        "生图画布协议：入口节点写核心意图；普通节点可写提示词、限制、风格、构图、角色、材质、负面约束或放参考图片；"
        "连线文本是高权重语义标签，例如“参考风格”“参考视角”“参考角色”“负面限制”“修图参考”。"
        "生成时应沿入口到输出的连接关系汇总节点内容；输出节点的生成提示词字段是最终提示词，用户可直接编辑后再次生图。"
        "如果当前生图画布由父画布节点打开，还应沿父画布中所有输入到该画布节点的上游链路汇总外部参考节点、图片和超文本文档。"
        "如果用户添加修图节点，应把旧输出当作普通参考图节点，新输出连接旧输出，并基于上一张图继续修改。"
    )


def find_image_output_node(canvas: CanvasData) -> Node | None:
    if not canvas.nodes:
        return None
    incoming_targets = {edge.target for edge in canvas.valid_edges()}
    candidates = [node for node in canvas.nodes if is_image_output_node(node)]
    if candidates:
        candidates.sort(key=lambda node: (0 if node.id in incoming_targets else 1, node.order, node.x))
        return candidates[-1]
    titled = [node for node in canvas.nodes if node.title.strip() in {IMAGE_CANVAS_OUTPUT_TITLE, IMAGE_CANVAS_EDIT_TITLE}]
    if titled:
        titled.sort(key=lambda node: (node.order, node.x))
        return titled[-1]
    return max(canvas.nodes, key=lambda node: (node.x, node.order))


def build_image_canvas_request(
    canvas: CanvasData,
    output_node_id: str = "",
    *,
    project: ProjectData | None = None,
    project_path: str | Path | None = None,
) -> ImageCanvasRequestData:
    output = canvas.find_node(output_node_id) if output_node_id else find_image_output_node(canvas)
    if output is None:
        return ImageCanvasRequestData(prompt="", output_node_id="")

    output_prompt = _field_value(output, IMAGE_CANVAS_PROMPT_FIELD).strip()
    output_title = output.title.strip()
    if output_title and output_title not in {IMAGE_CANVAS_ENTRY_TITLE, IMAGE_CANVAS_OUTPUT_TITLE, IMAGE_CANVAS_EDIT_TITLE, "参考图"}:
        output_prompt = output_title
    node_lines: list[str] = []
    reference_paths: list[Path] = []
    for node in _ordered_upstream_nodes(canvas, output.id):
        if node.id == output.id:
            continue
        incoming_labels = _incoming_labels(canvas, node.id)
        prefix = f"{node.title}"
        if incoming_labels:
            prefix += f"（{', '.join(incoming_labels)}）"
        content = _node_prompt_text(node)
        if content:
            node_lines.append(f"{prefix}: {content}")
        for path in _node_image_paths(node):
            reference_paths.append(path)

    for path in _node_image_paths(output):
        reference_paths.append(path)

    external_lines, external_reference_paths = _external_image_canvas_context(project, canvas, project_path)
    reference_paths.extend(external_reference_paths)

    prompt_lines: list[str] = []
    rules = canvas.ai_rules.strip()
    if rules:
        prompt_lines.append(rules)
    if canvas.is_pixel_canvas():
        prompt_lines.append(pixel_canvas_prompt_rules())
    if output_prompt:
        prompt_lines.append(output_prompt)
    if external_lines:
        prompt_lines.append("父画布外部输入链路（高权重）:")
        prompt_lines.extend(external_lines)
    prompt_lines.extend(node_lines)
    if not prompt_lines:
        prompt_lines = ["基于生图画布生成一张游戏设计参考图。"]
    prompt = "\n".join(dict.fromkeys(line for line in prompt_lines if line.strip()))
    return ImageCanvasRequestData(
        prompt=prompt,
        reference_paths=_unique_paths(reference_paths),
        output_node_id=output.id,
        prompt_lines=prompt_lines,
    )


def pixel_canvas_prompt_rules() -> str:
    return (
        "【像素作画硬约束】\n"
        "- 目标是专业像素游戏素材，不是普通插画加马赛克滤镜；先生成高品质完整源图，再由本地算法做像素对齐采样。\n"
        "- 源图应按 ASE/Aseprite 式像素绘制思路组织：清晰轮廓、硬边色块、统一正方形像素格；如果源图为 1024x1536，最终会优先采样成 256x384；如果用户指定输出尺寸，则会中心裁切到目标比例后按整数网格采样。\n"
        "- 画面必须适合 1/4 或指定比例的严格网格采样：大形、线条、眼睛、手、道具、边缘都应落在稳定色块上，避免小于采样格的噪点细节。\n"
        "- 禁止抗锯齿、模糊采样、柔焦、高光晕、半透明羽化边、照片级渐变、过量细碎噪点和 AI 伪纹理。\n"
        "- 线条必须 pixel-perfect：硬边阶梯规律，曲线由干净像素簇构成，清除 L 形拐角夹心像素、双宽斜线和孤立脏点。\n"
        "- 使用受控调色板：主色和色阶稳定，允许有控制的 hue shift；不要把整张图压成脏灰或过少颜色。\n"
        "- 形体优先读性：强剪影、大块明暗、少量关键点色；最终小尺寸下仍能读出主体和用途。\n"
        "- 如果是 tile 或地面，必须考虑无缝平铺、边缘连续、角落衔接和重复图案节奏。\n"
        "- 可以提炼优秀动作/地牢手游像素美术的通用质量原则，但不得复刻任何现有游戏的角色、图标、场景或具体资产。"
    )


def set_image_output_prompt(node: Node, prompt: str) -> None:
    field = _field_by_name(node, IMAGE_CANVAS_PROMPT_FIELD)
    if field is None:
        field = NodeField(IMAGE_CANVAS_PROMPT_FIELD, "长文本", "")
        node.fields.insert(0, field)
    field.data_type = "长文本"
    field.value = prompt.strip()


def set_image_output_path(node: Node, image_path: str) -> None:
    field = _field_by_name(node, IMAGE_CANVAS_IMAGE_FIELD)
    if field is None:
        field = NodeField(IMAGE_CANVAS_IMAGE_FIELD, "图片", "")
        node.fields.append(field)
    field.data_type = "图片"
    field.image_path = image_path
    field.value = Path(image_path).name if image_path else ""


def image_output_path(node: Node) -> str:
    field = _field_by_name(node, IMAGE_CANVAS_IMAGE_FIELD)
    return field.image_path if field is not None else ""


def apply_image_output_result(node: Node, prompt: str, image_path: str) -> None:
    node.title = prompt.strip() or node.title or IMAGE_CANVAS_OUTPUT_TITLE
    set_image_output_prompt(node, prompt.strip() or node.title)
    if image_path:
        set_image_output_path(node, image_path)


def convert_output_node_to_reference(node: Node) -> None:
    if node.title.strip() in {IMAGE_CANVAS_OUTPUT_TITLE, IMAGE_CANVAS_EDIT_TITLE}:
        node.title = "参考图"
    node.icon = "参"
    prompt_field = _field_by_name(node, IMAGE_CANVAS_PROMPT_FIELD)
    if prompt_field is not None:
        prompt_field.name = "参考说明"
        prompt_field.data_type = "长文本"
    else:
        node.fields.insert(0, NodeField("参考说明", "长文本", ""))
    image_field = _field_by_name(node, IMAGE_CANVAS_IMAGE_FIELD)
    if image_field is None:
        node.fields.append(NodeField(IMAGE_CANVAS_IMAGE_FIELD, "图片", ""))


def new_image_output_node_from_previous(previous: Node) -> Node:
    x = previous.x + max(previous.width or 506.0, 506.0) + 160.0
    y = previous.y
    prompt = NodeField(
        IMAGE_CANVAS_PROMPT_FIELD,
        "长文本",
        "在上一张图基础上继续修改：",
        x=16,
        y=16,
        width=456,
        height=120,
        font_size=13,
        text_color="#000000",
        bg_color="#FFFFFF",
        text_h_align="center",
        text_v_align="center",
    )
    image = NodeField(
        IMAGE_CANVAS_IMAGE_FIELD,
        "图片",
        "",
        x=16,
        y=150,
        width=456,
        height=256,
        font_size=12,
        text_color="#000000",
        bg_color="#FFFFFF",
        text_h_align="center",
        text_v_align="center",
    )
    return Node(
        title=IMAGE_CANVAS_EDIT_TITLE,
        x=x,
        y=y,
        width=506,
        height=480,
        icon="图",
        fields=[prompt, image],
    )


def edge_with_label(source: str, target: str, label: str) -> Edge:
    edge = Edge(source=source, target=target)
    edge.label = label
    return edge


def is_image_output_node(node: Node) -> bool:
    names = {field.name for field in node.fields}
    return (
        {IMAGE_CANVAS_PROMPT_FIELD, IMAGE_CANVAS_IMAGE_FIELD}.issubset(names)
        or node.title.strip() in {IMAGE_CANVAS_OUTPUT_TITLE, IMAGE_CANVAS_EDIT_TITLE}
    )


def _external_image_canvas_context(
    project: ProjectData | None,
    canvas: CanvasData,
    project_path: str | Path | None,
) -> tuple[list[str], list[Path]]:
    if project is None:
        return [], []
    parent_canvas, parent_node = _parent_canvas_node(project, canvas)
    if parent_canvas is None or parent_node is None:
        return [], []

    ordered_nodes = _ordered_upstream_nodes(parent_canvas, parent_node.id)
    chain_nodes = [node for node in ordered_nodes if node.id != parent_node.id]
    if not chain_nodes:
        return [], []

    chain_ids = {node.id for node in ordered_nodes}
    lines: list[str] = []
    reference_paths: list[Path] = []
    target_title = parent_node.title.strip()
    if target_title:
        lines.append(f"- 父画布目标节点: {target_title}")

    linked_doc_count = 0
    for node in chain_nodes:
        labels = _chain_labels(parent_canvas, node.id, chain_ids, parent_node.id)
        prefix = node.title.strip() or "节点"
        if labels:
            prefix += f"（{', '.join(labels)}）"

        parts: list[str] = []
        content = _node_prompt_text(node)
        if content:
            parts.append(content)
        if node.node_type == "超文本" and node.link_path:
            doc_text = ""
            if linked_doc_count < IMAGE_CANVAS_LINKED_DOC_LIMIT:
                doc_text = _read_linked_document_text(project_path, node.link_path)
                if doc_text:
                    linked_doc_count += 1
            if doc_text:
                parts.append(f"文档内容: {doc_text}")
            else:
                parts.append(f"文档路径: {node.link_path}")

        if parts:
            lines.append(f"- {prefix}: {'；'.join(parts)}")
        else:
            lines.append(f"- {prefix}")
        reference_paths.extend(_node_image_paths(node))

    return lines, reference_paths


def _parent_canvas_node(project: ProjectData, canvas: CanvasData) -> tuple[CanvasData | None, Node | None]:
    parent_canvas = project.find_canvas(canvas.parent_canvas_id) if canvas.parent_canvas_id else None
    parent_node = parent_canvas.find_node(canvas.parent_node_id) if parent_canvas and canvas.parent_node_id else None
    if parent_canvas is not None and parent_node is None:
        parent_node = next((node for node in parent_canvas.nodes if node.canvas_id == canvas.id), None)
    if parent_canvas is not None and parent_node is not None:
        return parent_canvas, parent_node

    for candidate_canvas in project.canvases:
        for node in candidate_canvas.nodes:
            if node.canvas_id == canvas.id:
                return candidate_canvas, node
    return None, None


def _chain_labels(canvas: CanvasData, node_id: str, chain_ids: set[str], target_id: str) -> list[str]:
    labels = _incoming_labels(canvas, node_id)
    for edge in canvas.valid_edges():
        if edge.source != node_id or (edge.target not in chain_ids and edge.target != target_id):
            continue
        label = edge.label.strip()
        if label and label not in labels:
            labels.append(label)
    return labels


def _read_linked_document_text(project_path: str | Path | None, link_path: str) -> str:
    if not project_path or not link_path:
        return ""
    try:
        from .project_files.linked_documents import read_link_document
    except ImportError:
        return ""
    try:
        content = read_link_document(project_path, link_path)
    except (OSError, UnicodeDecodeError):
        return ""
    content = "\n".join(line.strip() for line in content.splitlines() if line.strip())
    if len(content) > IMAGE_CANVAS_LINKED_DOC_CHARS:
        return f"{content[:IMAGE_CANVAS_LINKED_DOC_CHARS]}..."
    return content


def _ordered_upstream_nodes(canvas: CanvasData, output_id: str) -> list[Node]:
    by_id = {node.id: node for node in canvas.nodes}
    incoming: dict[str, list[str]] = {}
    for edge in canvas.valid_edges():
        incoming.setdefault(edge.target, []).append(edge.source)
    seen: set[str] = set()
    ordered: list[Node] = []

    def visit(node_id: str) -> None:
        if node_id in seen:
            return
        seen.add(node_id)
        for source_id in incoming.get(node_id, []):
            visit(source_id)
        node = by_id.get(node_id)
        if node is not None:
            ordered.append(node)

    visit(output_id)
    return ordered


def _incoming_labels(canvas: CanvasData, node_id: str) -> list[str]:
    labels: list[str] = []
    for edge in canvas.valid_edges():
        if edge.target != node_id:
            continue
        label = edge.label.strip()
        if label and label not in labels:
            labels.append(label)
    return labels


def _node_prompt_text(node: Node) -> str:
    parts: list[str] = []
    for field in node.fields:
        if field.data_type == "图片":
            if field.value.strip():
                parts.append(f"{field.name}: {field.value.strip()}")
            continue
        value = field.value.strip()
        if not value:
            continue
        if field.name.strip() in {"描述", "提示词", IMAGE_CANVAS_PROMPT_FIELD}:
            parts.append(value)
        else:
            parts.append(f"{field.name}: {value}")
    if node.title.strip() not in {IMAGE_CANVAS_ENTRY_TITLE, IMAGE_CANVAS_OUTPUT_TITLE, IMAGE_CANVAS_EDIT_TITLE, "参考图"} and node.title.strip():
        parts.insert(0, node.title.strip())
    return "；".join(parts)


def _node_image_paths(node: Node) -> list[Path]:
    paths: list[Path] = []
    for field in node.fields:
        path = field.image_path.strip() if field.data_type == "图片" else ""
        if path:
            paths.append(Path(path))
    return paths


def _field_by_name(node: Node, name: str) -> NodeField | None:
    return next((field for field in node.fields if field.name == name), None)


def _field_value(node: Node, name: str) -> str:
    field = _field_by_name(node, name)
    return field.value if field is not None else ""


def _unique_paths(paths: list[Path]) -> list[Path]:
    seen: set[str] = set()
    result: list[Path] = []
    for path in paths:
        key = str(path)
        if key in seen:
            continue
        seen.add(key)
        result.append(path)
    return result
