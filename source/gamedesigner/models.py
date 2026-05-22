from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any
from uuid import uuid4


SCHEMA_VERSION = 1
FIELD_EXPORT_PROPS = [
    "x",
    "y",
    "width",
    "height",
    "font_size",
    "text_color",
    "bg_color",
]

FIELD_TYPES = [
    "文本",
    "长文本",
    "整数",
    "数字",
    "布尔",
    "枚举",
    "颜色",
    "日期",
    "图片",
    "画布",
    "资源路径",
]

NODE_TYPES = ["普通", "画布", "超链接"]
TEXT_H_ALIGNMENTS = ["left", "center", "right"]
TEXT_V_ALIGNMENTS = ["top", "center", "bottom"]
DEFAULT_NODE_COLOR = "#ffffff"
EDGE_STYLES = ["curve", "straight", "orthogonal"]


def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid4().hex[:10]}"


@dataclass
class NodeField:
    name: str = "字段"
    data_type: str = "文本"
    value: str = ""
    x: float = 0.0
    y: float = 0.0
    width: float = 0.0
    height: float = 0.0
    font_size: int = 12
    text_color: str = "#1D1D1F"
    bg_color: str = "#ffffff"
    text_h_align: str = "left"
    text_v_align: str = "top"
    image_path: str = ""
    export_props: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "data_type": self.data_type,
            "value": "" if self.data_type == "图片" else self.value,
            "x": self.x,
            "y": self.y,
            "width": self.width,
            "height": self.height,
            "font_size": self.font_size,
            "text_color": self.text_color,
            "bg_color": self.bg_color,
            "text_h_align": self.text_h_align if self.text_h_align in TEXT_H_ALIGNMENTS else "left",
            "text_v_align": self.text_v_align if self.text_v_align in TEXT_V_ALIGNMENTS else "top",
            "image_path": self.image_path if self.data_type == "图片" else "",
            "export_props": [item for item in self.export_props if item in FIELD_EXPORT_PROPS],
        }

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "NodeField":
        data_type = str(raw.get("data_type", "文本"))
        image_path = str(raw.get("image_path") or "")
        if data_type == "资源路径" and image_path:
            data_type = "图片"
        if data_type not in FIELD_TYPES:
            data_type = "文本"
        if data_type != "图片":
            image_path = ""
        export_props = raw.get("export_props", [])
        if not isinstance(export_props, list):
            export_props = []
        return cls(
            name=str(raw.get("name", "字段")),
            data_type=data_type,
            value=str(raw.get("value", "")),
            x=_float_or(raw.get("x"), 0.0),
            y=_float_or(raw.get("y"), 0.0),
            width=_float_or(raw.get("width"), 0.0),
            height=_float_or(raw.get("height"), 0.0),
            font_size=max(8, min(48, int(_float_or(raw.get("font_size"), 12)))),
            text_color=str(raw.get("text_color") or "#1D1D1F"),
            bg_color=str(raw.get("bg_color") or "#ffffff"),
            text_h_align=_choice_or(raw.get("text_h_align"), TEXT_H_ALIGNMENTS, "left"),
            text_v_align=_choice_or(raw.get("text_v_align"), TEXT_V_ALIGNMENTS, "top"),
            image_path=image_path,
            export_props=[str(item) for item in export_props if str(item) in FIELD_EXPORT_PROPS],
        )

    def has_visual_layout(self) -> bool:
        return self.width > 0 and self.height > 0


@dataclass
class Node:
    id: str = field(default_factory=lambda: new_id("node"))
    title: str = "新节点"
    node_type: str = "普通"
    canvas_id: str = ""
    link_path: str = ""
    link_format: str = "md"
    order: int = 0
    x: float = 0.0
    y: float = 0.0
    width: float = 0.0
    height: float = 0.0
    color: str = DEFAULT_NODE_COLOR
    icon: str = ""
    fields: list[NodeField] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "title": self.title,
            "node_type": self.node_type if self.node_type in NODE_TYPES else "普通",
            "canvas_id": self.canvas_id,
            "link_path": self.link_path if self.node_type == "超链接" else "",
            "link_format": self.link_format if self.link_format in {"md", "txt"} else "md",
            "order": max(0, int(self.order)),
            "x": self.x,
            "y": self.y,
            "width": self.width,
            "height": self.height,
            "color": self.color,
            "icon": self.icon,
            "fields": [item.to_dict() for item in self.fields],
        }

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "Node":
        fields = raw.get("fields", [])
        if not isinstance(fields, list):
            fields = []
        node_type = str(raw.get("node_type") or raw.get("type") or "普通")
        if node_type not in NODE_TYPES:
            node_type = "普通"
        return cls(
            id=str(raw.get("id") or new_id("node")),
            title=str(raw.get("title", "新节点")),
            node_type=node_type,
            canvas_id=str(raw.get("canvas_id") or ""),
            link_path=str(raw.get("link_path") or ""),
            link_format=_choice_or(raw.get("link_format"), ["md", "txt"], "md"),
            order=max(0, int(_float_or(raw.get("order"), 0.0))),
            x=_float_or(raw.get("x"), 0.0),
            y=_float_or(raw.get("y"), 0.0),
            width=max(0.0, _float_or(raw.get("width"), 0.0)),
            height=max(0.0, _float_or(raw.get("height"), 0.0)),
            color=str(raw.get("color") or DEFAULT_NODE_COLOR),
            icon=str(raw.get("icon") or ""),
            fields=[NodeField.from_dict(item) for item in fields if isinstance(item, dict)],
        )


@dataclass
class Edge:
    id: str = field(default_factory=lambda: new_id("edge"))
    source: str = ""
    target: str = ""
    label: str = ""
    style: str = "curve"

    def __post_init__(self) -> None:
        if self.style not in EDGE_STYLES:
            self.style = "curve"

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "source": self.source,
            "target": self.target,
            "label": self.label,
            "style": self.style if self.style in EDGE_STYLES else "curve",
        }

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "Edge":
        style = str(raw.get("style", "curve") or "curve")
        if style not in EDGE_STYLES:
            style = "curve"
        return cls(
            id=str(raw.get("id") or new_id("edge")),
            source=str(raw.get("source", "")),
            target=str(raw.get("target", "")),
            label=str(raw.get("label", "")),
            style=style,
        )


@dataclass
class CanvasData:
    id: str = field(default_factory=lambda: new_id("canvas"))
    name: str = "主画布"
    parent_canvas_id: str = ""
    parent_node_id: str = ""
    nodes: list[Node] = field(default_factory=list)
    edges: list[Edge] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "parent_canvas_id": self.parent_canvas_id,
            "parent_node_id": self.parent_node_id,
            "nodes": [node.to_dict() for node in self.nodes],
            "edges": [edge.to_dict() for edge in self.valid_edges()],
        }

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "CanvasData":
        nodes_raw = raw.get("nodes", [])
        edges_raw = raw.get("edges", [])
        if not isinstance(nodes_raw, list):
            nodes_raw = []
        if not isinstance(edges_raw, list):
            edges_raw = []
        canvas = cls(
            id=str(raw.get("id") or new_id("canvas")),
            name=str(raw.get("name") or "画布"),
            parent_canvas_id=str(raw.get("parent_canvas_id") or ""),
            parent_node_id=str(raw.get("parent_node_id") or ""),
            nodes=[Node.from_dict(item) for item in nodes_raw if isinstance(item, dict)],
            edges=[Edge.from_dict(item) for item in edges_raw if isinstance(item, dict)],
        )
        canvas.remove_broken_edges()
        canvas.normalize_node_order()
        return canvas

    def find_node(self, node_id: str) -> Node | None:
        return next((node for node in self.nodes if node.id == node_id), None)

    def valid_edges(self) -> list[Edge]:
        node_ids = {node.id for node in self.nodes}
        return [
            edge
            for edge in self.edges
            if edge.source in node_ids and edge.target in node_ids and edge.source != edge.target
        ]

    def remove_broken_edges(self) -> None:
        self.edges = self.valid_edges()

    def normalize_node_order(self) -> None:
        original_index = {node.id: index for index, node in enumerate(self.nodes)}
        ordered = sorted(
            self.nodes,
            key=lambda node: (
                node.order if node.order > 0 else len(self.nodes) + original_index[node.id] + 1,
                original_index[node.id],
            ),
        )
        for index, node in enumerate(ordered, start=1):
            node.order = index

    def next_node_order(self) -> int:
        self.normalize_node_order()
        return len(self.nodes) + 1

    def add_node(self, node: Node) -> Node:
        node.order = self.next_node_order()
        self.nodes.append(node)
        return node

    def add_edge(self, source: str, target: str) -> Edge | None:
        if source == target:
            return None
        if not self.find_node(source) or not self.find_node(target):
            return None
        for edge in self.edges:
            if edge.source == source and edge.target == target:
                return edge
        edge = Edge(source=source, target=target)
        self.edges.append(edge)
        return edge

    def delete_node(self, node_id: str) -> None:
        self.nodes = [node for node in self.nodes if node.id != node_id]
        self.edges = [edge for edge in self.edges if edge.source != node_id and edge.target != node_id]
        self.normalize_node_order()

    def delete_edge(self, edge_id: str) -> None:
        self.edges = [edge for edge in self.edges if edge.id != edge_id]


@dataclass
class NodeTemplate:
    id: str = field(default_factory=lambda: new_id("template"))
    name: str = "节点模板"
    color: str = DEFAULT_NODE_COLOR
    icon: str = ""
    fields: list[NodeField] = field(default_factory=list)

    def create_node(self, x: float, y: float) -> Node:
        return Node(
            title=self.name,
            x=x,
            y=y,
            color=self.color,
            icon=self.icon,
            fields=[NodeField.from_dict(field.to_dict()) for field in self.fields],
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "color": self.color,
            "icon": self.icon,
            "fields": [item.to_dict() for item in self.fields],
        }

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "NodeTemplate":
        fields = raw.get("fields", [])
        if not isinstance(fields, list):
            fields = []
        return cls(
            id=str(raw.get("id") or new_id("template")),
            name=str(raw.get("name", "节点模板")),
            color=str(raw.get("color") or DEFAULT_NODE_COLOR),
            icon=str(raw.get("icon") or ""),
            fields=[NodeField.from_dict(item) for item in fields if isinstance(item, dict)],
        )


@dataclass
class ProjectData:
    name: str = "未命名设计"
    source_dir: str = ""
    output_dir: str = ""
    copy_link_docs_to_source: bool = False
    nodes: list[Node] = field(default_factory=list)
    edges: list[Edge] = field(default_factory=list)
    templates: list[NodeTemplate] = field(default_factory=list)
    root_canvas_id: str = ""
    canvases: list[CanvasData] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        self.ensure_canvas_structure()
        root = self.root_canvas()
        return {
            "file_format": "GameDesigner.GDC",
            "schema_version": SCHEMA_VERSION,
            "name": self.name,
            "source_dir": self.source_dir,
            "output_dir": self.output_dir,
            "copy_link_docs_to_source": self.copy_link_docs_to_source,
            "root_canvas_id": self.root_canvas_id,
            "nodes": [node.to_dict() for node in root.nodes],
            "edges": [edge.to_dict() for edge in root.valid_edges()],
            "canvases": [canvas.to_dict() for canvas in self.canvases],
            "templates": [template.to_dict() for template in self.templates],
        }

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "ProjectData":
        nodes_raw = raw.get("nodes", [])
        edges_raw = raw.get("edges", [])
        templates_raw = raw.get("templates", [])
        canvases_raw = raw.get("canvases", [])
        if not isinstance(nodes_raw, list):
            nodes_raw = []
        if not isinstance(edges_raw, list):
            edges_raw = []
        if not isinstance(templates_raw, list):
            templates_raw = []
        if not isinstance(canvases_raw, list):
            canvases_raw = []

        project = cls(
            name=str(raw.get("name", "未命名设计")),
            source_dir=str(raw.get("source_dir", "")),
            output_dir=str(raw.get("output_dir", "")),
            copy_link_docs_to_source=bool(raw.get("copy_link_docs_to_source", False)),
            nodes=[Node.from_dict(item) for item in nodes_raw if isinstance(item, dict)],
            edges=[Edge.from_dict(item) for item in edges_raw if isinstance(item, dict)],
            templates=[
                NodeTemplate.from_dict(item) for item in templates_raw if isinstance(item, dict)
            ],
            root_canvas_id=str(raw.get("root_canvas_id") or ""),
            canvases=[CanvasData.from_dict(item) for item in canvases_raw if isinstance(item, dict)],
        )
        project.ensure_canvas_structure()
        if not project.templates:
            project.templates = default_templates()
        return project

    def ensure_canvas_structure(self) -> None:
        if not self.canvases:
            self.root_canvas_id = self.root_canvas_id or new_id("canvas")
            self.canvases = [
                CanvasData(
                    id=self.root_canvas_id,
                    name="主画布",
                    nodes=self.nodes,
                    edges=self.edges,
                )
            ]
        if not self.root_canvas_id or not self.find_canvas(self.root_canvas_id):
            self.root_canvas_id = self.canvases[0].id
        root = self.find_canvas(self.root_canvas_id)
        if root and self.nodes is not root.nodes and (self.nodes or self.edges):
            root.nodes = self.nodes
            root.edges = self.edges
        for canvas in self.canvases:
            canvas.remove_broken_edges()
            for node in canvas.nodes:
                if node.node_type != "画布":
                    node.canvas_id = ""
                if node.node_type != "超链接":
                    node.link_path = ""
            canvas.normalize_node_order()
        root = self.root_canvas()
        self.nodes = root.nodes
        self.edges = root.edges

    def root_canvas(self) -> CanvasData:
        if not self.canvases:
            self.ensure_canvas_structure()
        canvas = self.find_canvas(self.root_canvas_id)
        if canvas:
            return canvas
        self.root_canvas_id = self.canvases[0].id
        return self.canvases[0]

    def find_canvas(self, canvas_id: str) -> CanvasData | None:
        return next((canvas for canvas in self.canvases if canvas.id == canvas_id), None)

    def add_canvas(
        self,
        name: str,
        parent_canvas_id: str = "",
        parent_node_id: str = "",
    ) -> CanvasData:
        self.ensure_canvas_structure()
        canvas = CanvasData(
            id=new_id("canvas"),
            name=name.strip() or "新画布",
            parent_canvas_id=parent_canvas_id,
            parent_node_id=parent_node_id,
        )
        self.canvases.append(canvas)
        return canvas

    def delete_canvas(self, canvas_id: str) -> None:
        if canvas_id == self.root_canvas_id:
            return
        self.canvases = [canvas for canvas in self.canvases if canvas.id != canvas_id]
        for canvas in self.canvases:
            for node in canvas.nodes:
                if node.canvas_id == canvas_id:
                    node.canvas_id = ""
                    node.node_type = "普通"

    def find_node(self, node_id: str) -> Node | None:
        return self.root_canvas().find_node(node_id)

    def find_template(self, template_id: str) -> NodeTemplate | None:
        return next((template for template in self.templates if template.id == template_id), None)

    def valid_edges(self) -> list[Edge]:
        return self.root_canvas().valid_edges()

    def remove_broken_edges(self) -> None:
        self.root_canvas().remove_broken_edges()

    def add_edge(self, source: str, target: str) -> Edge | None:
        return self.root_canvas().add_edge(source, target)

    def delete_node(self, node_id: str) -> None:
        self.root_canvas().delete_node(node_id)

    def delete_edge(self, edge_id: str) -> None:
        self.root_canvas().delete_edge(edge_id)


def default_project() -> ProjectData:
    project = ProjectData(templates=default_templates())
    project.nodes = [
        Node(
            title="版本目标",
            x=-170,
            y=-90,
            color=DEFAULT_NODE_COLOR,
            icon="目",
            fields=[
                NodeField("内容信息", "长文本", "定义版本目标、范围和验收标准"),
                NodeField("数据类型", "枚举", "计划"),
            ],
        ),
        Node(
            title="科技树入口",
            x=160,
            y=70,
            color=DEFAULT_NODE_COLOR,
            icon="技",
            fields=[
                NodeField("内容信息", "长文本", "描述解锁条件、消耗和产出"),
                NodeField("数据类型", "枚举", "科技树"),
            ],
        ),
    ]
    project.add_edge(project.nodes[0].id, project.nodes[1].id)
    return project


def default_templates() -> list[NodeTemplate]:
    return [
        NodeTemplate(
            name="设计节点",
            color=DEFAULT_NODE_COLOR,
            icon="设",
            fields=[
                NodeField("内容信息", "长文本", ""),
                NodeField("数据类型", "枚举", "普通"),
            ],
        ),
        NodeTemplate(
            name="数值节点",
            color=DEFAULT_NODE_COLOR,
            icon="数",
            fields=[
                NodeField("字段名", "文本", ""),
                NodeField("数值", "数字", "0"),
                NodeField("备注", "长文本", ""),
            ],
        ),
        NodeTemplate(
            name="任务节点",
            color=DEFAULT_NODE_COLOR,
            icon="任",
            fields=[
                NodeField("负责人", "文本", ""),
                NodeField("状态", "枚举", "未开始"),
                NodeField("工期", "整数", "1"),
            ],
        ),
    ]


def _float_or(value: Any, fallback: float) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return fallback


def _choice_or(value: Any, choices: list[str], fallback: str) -> str:
    text = str(value or "")
    return text if text in choices else fallback
