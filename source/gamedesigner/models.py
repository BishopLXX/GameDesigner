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

NODE_TYPES = ["普通", "画布", "超文本"]
CANVAS_TYPES = ["normal", "data"]
DATA_LAYOUT_MODES = ["horizontal", "grid", "table"]
DATA_ROW_STYLE_MODES = ["independent", "thumbnail"]
IMAGE_FIT_MODES = ["stretch", "contain", "cover", "nine_slice"]
TEXT_H_ALIGNMENTS = ["left", "center", "right"]
TEXT_V_ALIGNMENTS = ["top", "center", "bottom"]
DEFAULT_NODE_COLOR = "#ffffff"
EDGE_STYLES = ["curve", "straight", "orthogonal"]


def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid4().hex[:10]}"


@dataclass
class DesignNote:
    id: str = field(default_factory=lambda: new_id("note"))
    title: str = "便签"
    content: str = ""

    def is_empty(self) -> bool:
        return not self.title.strip() and not self.content.strip()

    def display_title(self) -> str:
        return self.title.strip() or "未命名便签"

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "title": self.title,
            "content": self.content,
        }

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "DesignNote":
        return cls(
            id=str(raw.get("id") or new_id("note")),
            title=str(raw.get("title") or ""),
            content=str(raw.get("content") or ""),
        )


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
    image_fit: str = "stretch"
    slice_left: int = 0
    slice_top: int = 0
    slice_right: int = 0
    slice_bottom: int = 0
    export_props: list[str] = field(default_factory=list)
    show_label: bool = False
    id: str = field(default_factory=lambda: new_id("field"))

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
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
            "image_fit": self.image_fit if self.image_fit in IMAGE_FIT_MODES else "stretch",
            "slice_left": max(0, int(self.slice_left)),
            "slice_top": max(0, int(self.slice_top)),
            "slice_right": max(0, int(self.slice_right)),
            "slice_bottom": max(0, int(self.slice_bottom)),
            "export_props": [item for item in self.export_props if item in FIELD_EXPORT_PROPS],
            "show_label": self.show_label,
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
            id=str(raw.get("id") or new_id("field")),
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
            image_fit=_choice_or(raw.get("image_fit"), IMAGE_FIT_MODES, "stretch"),
            slice_left=max(0, int(_float_or(raw.get("slice_left"), 0.0))),
            slice_top=max(0, int(_float_or(raw.get("slice_top"), 0.0))),
            slice_right=max(0, int(_float_or(raw.get("slice_right"), 0.0))),
            slice_bottom=max(0, int(_float_or(raw.get("slice_bottom"), 0.0))),
            export_props=[str(item) for item in export_props if str(item) in FIELD_EXPORT_PROPS],
            show_label=bool(raw.get("show_label", False)),
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
    group_id: str = ""
    order: int = 0
    x: float = 0.0
    y: float = 0.0
    width: float = 0.0
    height: float = 0.0
    color: str = DEFAULT_NODE_COLOR
    icon: str = ""
    icon_from_title: bool = False
    title_field_id: str = ""
    template_id: str = ""
    template_locked: bool = False
    fields: list[NodeField] = field(default_factory=list)
    notes: list[DesignNote] = field(default_factory=list)

    def normalized_node_type(self) -> str:
        return "超文本" if self.node_type == "超链接" else self.node_type

    def display_icon(self) -> str:
        if self.icon_from_title:
            return (self.title.strip()[:1] or self.icon).strip()
        return self.icon

    def to_dict(self) -> dict[str, Any]:
        node_type = self.normalized_node_type()
        return {
            "id": self.id,
            "title": self.title,
            "node_type": node_type if node_type in NODE_TYPES else "普通",
            "canvas_id": self.canvas_id,
            "link_path": self.link_path if node_type == "超文本" else "",
            "link_format": self.link_format if self.link_format in {"md", "txt"} else "md",
            "group_id": self.group_id,
            "order": max(0, int(self.order)),
            "x": self.x,
            "y": self.y,
            "width": self.width,
            "height": self.height,
            "color": self.color,
            "icon": self.icon,
            "icon_from_title": self.icon_from_title,
            "title_field_id": self.title_field_id,
            "template_id": self.template_id,
            "template_locked": self.template_locked,
            "fields": [item.to_dict() for item in self.fields],
            "notes": [note.to_dict() for note in self.notes if not note.is_empty()],
        }

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "Node":
        fields = raw.get("fields", [])
        notes = raw.get("notes", [])
        if not isinstance(fields, list):
            fields = []
        if not isinstance(notes, list):
            notes = []
        node_type = str(raw.get("node_type") or raw.get("type") or "普通")
        if node_type == "超链接":
            node_type = "超文本"
        if node_type not in NODE_TYPES:
            node_type = "普通"
        node = cls(
            id=str(raw.get("id") or new_id("node")),
            title=str(raw.get("title", "新节点")),
            node_type=node_type,
            canvas_id=str(raw.get("canvas_id") or ""),
            link_path=str(raw.get("link_path") or ""),
            link_format=_choice_or(raw.get("link_format"), ["md", "txt"], "md"),
            group_id=str(raw.get("group_id") or ""),
            order=max(0, int(_float_or(raw.get("order"), 0.0))),
            x=_float_or(raw.get("x"), 0.0),
            y=_float_or(raw.get("y"), 0.0),
            width=max(0.0, _float_or(raw.get("width"), 0.0)),
            height=max(0.0, _float_or(raw.get("height"), 0.0)),
            color=str(raw.get("color") or DEFAULT_NODE_COLOR),
            icon=str(raw.get("icon") or ""),
            icon_from_title=bool(raw.get("icon_from_title", False)),
            title_field_id=str(raw.get("title_field_id") or ""),
            template_id=str(raw.get("template_id") or ""),
            template_locked=bool(raw.get("template_locked", False)),
            fields=[NodeField.from_dict(item) for item in fields if isinstance(item, dict)],
            notes=[DesignNote.from_dict(item) for item in notes if isinstance(item, dict)],
        )
        if node.title_field_id and not any(field.id == node.title_field_id for field in node.fields):
            node.title_field_id = ""
        return node


@dataclass
class BlueprintGroup:
    id: str = field(default_factory=lambda: new_id("group"))
    title: str = "蓝图组"
    x: float = 0.0
    y: float = 0.0
    width: float = 560.0
    height: float = 260.0
    color: str = "#486A96"

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "title": self.title,
            "x": self.x,
            "y": self.y,
            "width": self.width,
            "height": self.height,
            "color": self.color,
        }

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "BlueprintGroup":
        return cls(
            id=str(raw.get("id") or new_id("group")),
            title=str(raw.get("title") or "蓝图组"),
            x=_float_or(raw.get("x"), 0.0),
            y=_float_or(raw.get("y"), 0.0),
            width=max(180.0, _float_or(raw.get("width"), 560.0)),
            height=max(120.0, _float_or(raw.get("height"), 260.0)),
            color=str(raw.get("color") or "#486A96"),
        )


@dataclass
class Edge:
    id: str = field(default_factory=lambda: new_id("edge"))
    source: str = ""
    target: str = ""
    label: str = ""
    style: str = "curve"
    orthogonal_bend_x: float | None = None
    orthogonal_bend_y: float | None = None
    orthogonal_route: list[dict[str, float]] = field(default_factory=list)

    def __post_init__(self) -> None:
        if self.style not in EDGE_STYLES:
            self.style = "curve"
        self.orthogonal_bend_x = _optional_float(self.orthogonal_bend_x)
        self.orthogonal_bend_y = _optional_float(self.orthogonal_bend_y)
        self.orthogonal_route = _point_dicts(self.orthogonal_route)
        if self.orthogonal_route and self.orthogonal_bend_x is None and self.orthogonal_bend_y is None:
            first = self.orthogonal_route[0]
            self.orthogonal_bend_x = first["x"]
            self.orthogonal_bend_y = first["y"]

    def to_dict(self) -> dict[str, Any]:
        data = {
            "id": self.id,
            "source": self.source,
            "target": self.target,
            "label": self.label,
            "style": self.style if self.style in EDGE_STYLES else "curve",
        }
        if self.orthogonal_bend_x is not None:
            data["orthogonal_bend_x"] = self.orthogonal_bend_x
        if self.orthogonal_bend_y is not None:
            data["orthogonal_bend_y"] = self.orthogonal_bend_y
        if self.orthogonal_route:
            data["orthogonal_route"] = [dict(point) for point in self.orthogonal_route]
        return data

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
            orthogonal_bend_x=_optional_float(raw.get("orthogonal_bend_x")),
            orthogonal_bend_y=_optional_float(raw.get("orthogonal_bend_y")),
            orthogonal_route=_point_dicts(raw.get("orthogonal_route")),
        )


@dataclass
class CanvasData:
    id: str = field(default_factory=lambda: new_id("canvas"))
    name: str = "主画布"
    canvas_type: str = "normal"
    data_layout: str = "grid"
    data_row_style: str = "independent"
    data_grid_rows: int = 0
    template_id: str = ""
    parent_canvas_id: str = ""
    parent_node_id: str = ""
    nodes: list[Node] = field(default_factory=list)
    edges: list[Edge] = field(default_factory=list)
    groups: list[BlueprintGroup] = field(default_factory=list)
    ai_rules: str = ""
    notes: list[DesignNote] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "canvas_type": self.canvas_type if self.canvas_type in CANVAS_TYPES else "normal",
            "ai_rules": self.ai_rules,
            "notes": [note.to_dict() for note in self.notes if not note.is_empty()],
            "data_layout": self.data_layout if self.data_layout in DATA_LAYOUT_MODES else "grid",
            "data_row_style": self.data_row_style if self.data_row_style in DATA_ROW_STYLE_MODES else "independent",
            "data_grid_rows": max(0, int(self.data_grid_rows)),
            "template_id": self.template_id,
            "parent_canvas_id": self.parent_canvas_id,
            "parent_node_id": self.parent_node_id,
            "groups": [group.to_dict() for group in self.groups],
            "nodes": [node.to_dict() for node in self.nodes],
            "edges": [edge.to_dict() for edge in self.valid_edges()],
        }

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "CanvasData":
        nodes_raw = raw.get("nodes", [])
        edges_raw = raw.get("edges", [])
        groups_raw = raw.get("groups", [])
        notes_raw = raw.get("notes", [])
        if not isinstance(nodes_raw, list):
            nodes_raw = []
        if not isinstance(edges_raw, list):
            edges_raw = []
        if not isinstance(groups_raw, list):
            groups_raw = []
        if not isinstance(notes_raw, list):
            notes_raw = []
        canvas = cls(
            id=str(raw.get("id") or new_id("canvas")),
            name=str(raw.get("name") or "画布"),
            canvas_type=_choice_or(raw.get("canvas_type"), CANVAS_TYPES, "normal"),
            ai_rules=str(raw.get("ai_rules") or raw.get("rules_memory") or ""),
            data_layout=_choice_or(raw.get("data_layout"), DATA_LAYOUT_MODES, "grid"),
            data_row_style=_choice_or(raw.get("data_row_style"), DATA_ROW_STYLE_MODES, "independent"),
            data_grid_rows=max(0, int(_float_or(raw.get("data_grid_rows"), 0.0))),
            template_id=str(raw.get("template_id") or ""),
            parent_canvas_id=str(raw.get("parent_canvas_id") or ""),
            parent_node_id=str(raw.get("parent_node_id") or ""),
            groups=[BlueprintGroup.from_dict(item) for item in groups_raw if isinstance(item, dict)],
            nodes=[Node.from_dict(item) for item in nodes_raw if isinstance(item, dict)],
            edges=[Edge.from_dict(item) for item in edges_raw if isinstance(item, dict)],
            notes=[DesignNote.from_dict(item) for item in notes_raw if isinstance(item, dict)],
        )
        canvas.remove_broken_edges()
        canvas.remove_broken_groups()
        canvas.normalize_node_order()
        return canvas

    def is_data_canvas(self) -> bool:
        return self.canvas_type == "data"

    def find_node(self, node_id: str) -> Node | None:
        return next((node for node in self.nodes if node.id == node_id), None)

    def find_group(self, group_id: str) -> BlueprintGroup | None:
        return next((group for group in self.groups if group.id == group_id), None)

    def valid_edges(self) -> list[Edge]:
        endpoint_ids = {node.id for node in self.nodes} | {group.id for group in self.groups}
        return [
            edge
            for edge in self.edges
            if edge.source in endpoint_ids and edge.target in endpoint_ids and edge.source != edge.target
        ]

    def remove_broken_edges(self) -> None:
        self.edges[:] = self.valid_edges()

    def remove_broken_groups(self) -> None:
        group_ids = {group.id for group in self.groups}
        for node in self.nodes:
            if node.group_id not in group_ids:
                node.group_id = ""

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

    def add_group(self, group: BlueprintGroup) -> BlueprintGroup:
        self.groups.append(group)
        return group

    def add_edge(self, source: str, target: str) -> Edge | None:
        if source == target:
            return None
        endpoint_ids = {node.id for node in self.nodes} | {group.id for group in self.groups}
        if source not in endpoint_ids or target not in endpoint_ids:
            return None
        for edge in self.edges:
            if edge.source == source and edge.target == target:
                return edge
        edge = Edge(source=source, target=target)
        self.edges.append(edge)
        return edge

    def delete_node(self, node_id: str) -> None:
        self.nodes[:] = [node for node in self.nodes if node.id != node_id]
        self.edges[:] = [edge for edge in self.edges if edge.source != node_id and edge.target != node_id]
        self.normalize_node_order()

    def delete_nodes(self, node_ids: set[str]) -> None:
        self.nodes[:] = [node for node in self.nodes if node.id not in node_ids]
        self.edges[:] = [edge for edge in self.edges if edge.source not in node_ids and edge.target not in node_ids]
        self.normalize_node_order()

    def delete_group(self, group_id: str) -> None:
        self.groups[:] = [group for group in self.groups if group.id != group_id]
        self.edges[:] = [edge for edge in self.edges if edge.source != group_id and edge.target != group_id]
        for node in self.nodes:
            if node.group_id == group_id:
                node.group_id = ""

    def delete_edge(self, edge_id: str) -> None:
        self.edges[:] = [edge for edge in self.edges if edge.id != edge_id]


@dataclass
class NodeTemplate:
    id: str = field(default_factory=lambda: new_id("template"))
    name: str = "节点模板"
    color: str = DEFAULT_NODE_COLOR
    icon: str = ""
    icon_from_title: bool = False
    title_field_id: str = ""
    fields: list[NodeField] = field(default_factory=list)

    def create_node(self, x: float, y: float) -> Node:
        fields = [NodeField.from_dict(field.to_dict()) for field in self.fields]
        title_field_id = self.title_field_id if any(item.id == self.title_field_id for item in fields) else ""
        title = self.name
        if title_field_id:
            title_field = next((item for item in fields if item.id == title_field_id), None)
            if title_field and title_field.value.strip():
                title = title_field.value.strip()
        return Node(
            title=title,
            x=x,
            y=y,
            color=self.color,
            icon=self.icon,
            icon_from_title=self.icon_from_title,
            title_field_id=title_field_id,
            template_id=self.id,
            fields=fields,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "color": self.color,
            "icon": self.icon,
            "icon_from_title": self.icon_from_title,
            "title_field_id": self.title_field_id,
            "fields": [item.to_dict() for item in self.fields],
        }

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "NodeTemplate":
        fields = raw.get("fields", [])
        if not isinstance(fields, list):
            fields = []
        template = cls(
            id=str(raw.get("id") or new_id("template")),
            name=str(raw.get("name", "节点模板")),
            color=str(raw.get("color") or DEFAULT_NODE_COLOR),
            icon=str(raw.get("icon") or ""),
            icon_from_title=bool(raw.get("icon_from_title", False)),
            title_field_id=str(raw.get("title_field_id") or ""),
            fields=[NodeField.from_dict(item) for item in fields if isinstance(item, dict)],
        )
        if template.title_field_id and not any(field.id == template.title_field_id for field in template.fields):
            template.title_field_id = ""
        return template


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
        if root and self.nodes is not root.nodes and not root.nodes and not root.edges and (self.nodes or self.edges):
            root.nodes = self.nodes
            root.edges = self.edges
        for canvas in self.canvases:
            canvas.remove_broken_edges()
            canvas.remove_broken_groups()
            for node in canvas.nodes:
                if node.node_type != "画布":
                    node.canvas_id = ""
                if node.node_type != "超文本":
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
        canvas_type: str = "normal",
        data_layout: str = "grid",
        data_row_style: str = "independent",
        data_grid_rows: int = 0,
        template_id: str = "",
        parent_canvas_id: str = "",
        parent_node_id: str = "",
    ) -> CanvasData:
        self.ensure_canvas_structure()
        canvas = CanvasData(
            id=new_id("canvas"),
            name=name.strip() or "新画布",
            canvas_type=canvas_type if canvas_type in CANVAS_TYPES else "normal",
            data_layout=data_layout if data_layout in DATA_LAYOUT_MODES else "grid",
            data_row_style=data_row_style if data_row_style in DATA_ROW_STYLE_MODES else "independent",
            data_grid_rows=max(0, int(data_grid_rows)),
            template_id=template_id,
            parent_canvas_id=parent_canvas_id,
            parent_node_id=parent_node_id,
        )
        self.canvases.append(canvas)
        return canvas

    def delete_canvas(self, canvas_id: str) -> None:
        self.delete_canvas_tree(canvas_id)

    def canvas_branch_ids(self, canvas_id: str) -> set[str]:
        if not canvas_id or not self.find_canvas(canvas_id):
            return set()
        pending = [canvas_id]
        result: set[str] = set()
        while pending:
            current = pending.pop()
            if current in result:
                continue
            result.add(current)
            pending.extend(
                canvas.id
                for canvas in self.canvases
                if canvas.parent_canvas_id == current and canvas.id not in result
            )
        return result

    def delete_canvas_tree(self, canvas_id: str) -> set[str]:
        if canvas_id == self.root_canvas_id:
            return set()
        deleted_ids = self.canvas_branch_ids(canvas_id)
        if not deleted_ids:
            return set()
        self.canvases = [canvas for canvas in self.canvases if canvas.id not in deleted_ids]
        for canvas in self.canvases:
            for node in canvas.nodes:
                if node.canvas_id in deleted_ids:
                    node.canvas_id = ""
                    node.node_type = "普通"
        return deleted_ids

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
        default_tech_tree_node(160, 70),
    ]
    project.add_edge(project.nodes[0].id, project.nodes[1].id)
    return project


def default_tech_tree_node(x: float = 0.0, y: float = 0.0) -> Node:
    fields = default_tech_tree_fields()
    return Node(
        title="节点名字",
        x=x,
        y=y,
        width=510,
        height=330,
        color=DEFAULT_NODE_COLOR,
        icon="N",
        icon_from_title=True,
        title_field_id=fields[0].id,
        fields=fields,
    )


def default_tech_tree_fields() -> list[NodeField]:
    return [
        _visual_field("节点名字", "文本", "节点名字", 20, 18, 320, 44, 13),
        _visual_field("最大等级", "整数", "最大等级", 360, 18, 96, 44, 13),
        _visual_field("解锁描述", "长文本", "解锁后获得效果的描述", 20, 72, 440, 94, 13),
        _visual_field("效果数值", "文本", "0% -> 5%", 20, 178, 440, 44, 13),
        _visual_field("消耗", "文本", "5000$", 20, 232, 440, 44, 13),
    ]


def _visual_field(
    name: str,
    data_type: str,
    value: str,
    x: float,
    y: float,
    width: float,
    height: float,
    font_size: int,
) -> NodeField:
    return NodeField(
        name=name,
        data_type=data_type,
        value=value,
        x=x,
        y=y,
        width=width,
        height=height,
        font_size=font_size,
        text_color="#000000",
        bg_color="#FFFFFF",
        text_h_align="center",
        text_v_align="center",
    )


def default_templates() -> list[NodeTemplate]:
    tech_fields = default_tech_tree_fields()
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
            name="科技树节点",
            color=DEFAULT_NODE_COLOR,
            icon="N",
            icon_from_title=True,
            title_field_id=tech_fields[0].id,
            fields=tech_fields,
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


def _optional_float(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _point_dicts(value: Any) -> list[dict[str, float]]:
    if not isinstance(value, list):
        return []
    points: list[dict[str, float]] = []
    for item in value:
        if not isinstance(item, dict):
            continue
        x = _optional_float(item.get("x"))
        y = _optional_float(item.get("y"))
        if x is None or y is None:
            continue
        points.append({"x": x, "y": y})
    return points


def _choice_or(value: Any, choices: list[str], fallback: str) -> str:
    text = str(value or "")
    return text if text in choices else fallback
