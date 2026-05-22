from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any
from uuid import uuid4


SCHEMA_VERSION = 1
FIELD_EXPORT_PROPS = [
    "name",
    "data_type",
    "value",
    "image_path",
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
    "图片",
    "资源路径",
]

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
    image_path: str = ""
    export_props: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "data_type": self.data_type,
            "value": self.value,
            "x": self.x,
            "y": self.y,
            "width": self.width,
            "height": self.height,
            "font_size": self.font_size,
            "text_color": self.text_color,
            "bg_color": self.bg_color,
            "image_path": self.image_path,
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
            image_path=image_path,
            export_props=[str(item) for item in export_props if str(item) in FIELD_EXPORT_PROPS],
        )

    def has_visual_layout(self) -> bool:
        return self.width > 0 and self.height > 0


@dataclass
class Node:
    id: str = field(default_factory=lambda: new_id("node"))
    title: str = "新节点"
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
        return cls(
            id=str(raw.get("id") or new_id("node")),
            title=str(raw.get("title", "新节点")),
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
    nodes: list[Node] = field(default_factory=list)
    edges: list[Edge] = field(default_factory=list)
    templates: list[NodeTemplate] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "file_format": "GameDesigner.GDC",
            "schema_version": SCHEMA_VERSION,
            "name": self.name,
            "source_dir": self.source_dir,
            "output_dir": self.output_dir,
            "nodes": [node.to_dict() for node in self.nodes],
            "edges": [edge.to_dict() for edge in self.valid_edges()],
            "templates": [template.to_dict() for template in self.templates],
        }

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "ProjectData":
        nodes_raw = raw.get("nodes", [])
        edges_raw = raw.get("edges", [])
        templates_raw = raw.get("templates", [])
        if not isinstance(nodes_raw, list):
            nodes_raw = []
        if not isinstance(edges_raw, list):
            edges_raw = []
        if not isinstance(templates_raw, list):
            templates_raw = []

        project = cls(
            name=str(raw.get("name", "未命名设计")),
            source_dir=str(raw.get("source_dir", "")),
            output_dir=str(raw.get("output_dir", "")),
            nodes=[Node.from_dict(item) for item in nodes_raw if isinstance(item, dict)],
            edges=[Edge.from_dict(item) for item in edges_raw if isinstance(item, dict)],
            templates=[
                NodeTemplate.from_dict(item) for item in templates_raw if isinstance(item, dict)
            ],
        )
        project.remove_broken_edges()
        if not project.templates:
            project.templates = default_templates()
        return project

    def find_node(self, node_id: str) -> Node | None:
        return next((node for node in self.nodes if node.id == node_id), None)

    def find_template(self, template_id: str) -> NodeTemplate | None:
        return next((template for template in self.templates if template.id == template_id), None)

    def valid_edges(self) -> list[Edge]:
        node_ids = {node.id for node in self.nodes}
        return [
            edge
            for edge in self.edges
            if edge.source in node_ids and edge.target in node_ids and edge.source != edge.target
        ]

    def remove_broken_edges(self) -> None:
        self.edges = self.valid_edges()

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

    def delete_edge(self, edge_id: str) -> None:
        self.edges = [edge for edge in self.edges if edge.id != edge_id]


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
