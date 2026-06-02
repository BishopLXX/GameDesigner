from __future__ import annotations

import csv
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from .models import FIELD_EXPORT_PROPS, BlueprintGroup, CanvasData, Node, NodeField, ProjectData


CSV_SORT_MODES = {"created", "x", "y"}
CSV_SORT_MODE_LABELS = {
    "created": "按创建顺序",
    "x": "按 X 往右排序",
    "y": "按 Y 往下排序",
}
DATA_CANVAS_SORT_LABEL = "按画布顺序"


BASE_COLUMNS = [
    ("名字", "文本", lambda node: node.title),
    ("图标", "文本", lambda node: node.icon),
]

GROUP_MEMBERSHIP_COLUMNS = [
    ("id", "蓝图组ID", "文本"),
    ("title", "蓝图组名称", "文本"),
]

NODE_LAYOUT_COLUMNS = [
    ("x", "节点X", "数字"),
    ("y", "节点Y", "数字"),
]

GROUP_LAYOUT_COLUMNS = [
    ("x", "蓝图组X", "数字"),
    ("y", "蓝图组Y", "数字"),
    ("width", "蓝图组宽", "数字"),
    ("height", "蓝图组高", "数字"),
    ("color", "蓝图组颜色", "颜色"),
]

PROP_LABELS = {
    "x": "X",
    "y": "Y",
    "width": "宽",
    "height": "高",
    "font_size": "字号",
    "text_color": "文字色",
    "bg_color": "背景色",
}

PROP_TYPES = {
    "x": "数字",
    "y": "数字",
    "width": "数字",
    "height": "数字",
    "font_size": "整数",
    "text_color": "颜色",
    "bg_color": "颜色",
}


@dataclass(frozen=True)
class Column:
    header: str
    data_type: str
    getter: Callable[[Node], str]


@dataclass(frozen=True)
class CanvasCsvExportSpec:
    canvas_id: str
    enabled: bool = True
    sort_mode: str = "created"
    target_folder: str = ""
    export_edges: bool = False
    export_groups: bool = False
    export_layout_info: bool = False


def export_game_csv(
    project: ProjectData,
    target: str | Path,
    canvas: CanvasData | None = None,
    sort_mode: str = "created",
    export_edges: bool = False,
    export_groups: bool = False,
    export_layout_info: bool = False,
) -> Path:
    path = Path(target)
    if path.suffix.lower() != ".csv":
        path = path / f"{_safe_filename(project.name)}.csv"
    path.parent.mkdir(parents=True, exist_ok=True)

    project.ensure_canvas_structure()
    source_canvas = canvas or project.root_canvas()
    return _write_canvas_csv(
        path,
        source_canvas,
        sort_mode,
        export_edges=export_edges,
        export_groups=export_groups,
        export_layout_info=export_layout_info,
    )


def export_all_canvas_csv(
    project: ProjectData,
    target: str | Path,
    sort_mode: str = "created",
    canvas_specs: list[CanvasCsvExportSpec] | None = None,
) -> list[Path]:
    project.ensure_canvas_structure()
    folder = Path(target)
    if folder.suffix.lower() == ".csv":
        folder = folder.parent
    folder.mkdir(parents=True, exist_ok=True)

    paths: list[Path] = []
    export_specs = _resolved_canvas_specs(project, sort_mode, canvas_specs)
    for canvas, spec in export_specs:
        if not spec.enabled:
            continue
        export_folder = Path(spec.target_folder.strip()) if spec.target_folder.strip() else folder
        if export_folder.suffix.lower() == ".csv":
            export_folder = export_folder.parent
        export_folder.mkdir(parents=True, exist_ok=True)
        path = export_folder / _canvas_csv_filename(canvas)
        paths.append(
            _write_canvas_csv(
                path,
                canvas,
                spec.sort_mode,
                export_edges=spec.export_edges,
                export_groups=spec.export_groups,
                export_layout_info=spec.export_layout_info,
            )
        )
    return paths


def _write_canvas_csv(
    path: Path,
    source_canvas: CanvasData,
    sort_mode: str,
    export_edges: bool = False,
    export_groups: bool = False,
    export_layout_info: bool = False,
) -> Path:
    source_canvas.normalize_node_order()
    nodes = _sorted_nodes(source_canvas.nodes, _resolved_sort_mode(source_canvas, sort_mode))
    is_data_canvas = source_canvas.is_data_canvas()
    has_groups = bool(source_canvas.groups)
    columns = _build_columns(
        nodes,
        source_canvas if export_edges and not is_data_canvas else None,
        source_canvas if (export_groups or export_layout_info) and not is_data_canvas and has_groups else None,
        source_canvas if export_layout_info and not is_data_canvas else None,
    )
    with path.open("w", encoding="utf-8-sig", newline="") as file:
        writer = csv.writer(file)
        writer.writerow([column.header for column in columns])
        writer.writerow([column.data_type for column in columns])
        for node in nodes:
            writer.writerow([column.getter(node) for column in columns])
    return path


def _build_columns(
    nodes: list[Node],
    edge_canvas: CanvasData | None = None,
    group_canvas: CanvasData | None = None,
    layout_canvas: CanvasData | None = None,
) -> list[Column]:
    columns = [
        Column(header, data_type, getter)
        for header, data_type, getter in BASE_COLUMNS
    ]

    if group_canvas is not None:
        _append_group_membership_columns(columns, group_canvas)

    if layout_canvas is not None:
        _append_layout_columns(columns, layout_canvas)

    field_columns: dict[str, tuple[str, str]] = {}
    pinned_columns: dict[tuple[str, str], str] = {}
    for node in nodes:
        for key, field in _field_keys(node):
            field_columns.setdefault(key, (field.name or "字段", field.data_type))
            for prop in field.export_props:
                if prop in FIELD_EXPORT_PROPS:
                    pinned_columns.setdefault((key, prop), field.name or "字段")

    for key, (label, data_type) in field_columns.items():
        columns.append(
            Column(
                _unique_header(columns, label),
                _field_type_for_csv(data_type),
                lambda node, key=key: _field_value(_field_by_key(node, key)),
            )
        )

    for (key, prop), label in pinned_columns.items():
        columns.append(
            Column(
                _unique_header(columns, f"{label}.{PROP_LABELS.get(prop, prop)}"),
                PROP_TYPES.get(prop, "文本"),
                lambda node, key=key, prop=prop: _property_value(_field_by_key(node, key), prop),
            )
        )

    if edge_canvas is not None:
        edge_targets = _edge_targets_by_source(edge_canvas)
        columns.append(
            Column(
                _unique_header(columns, "连线"),
                "文本",
                lambda node, edge_targets=edge_targets: edge_targets.get(node.id, ""),
            )
        )

    return columns


def _sorted_nodes(nodes: list[Node], sort_mode: str) -> list[Node]:
    mode = sort_mode if sort_mode in CSV_SORT_MODES else "created"
    if mode == "x":
        return sorted(nodes, key=lambda node: (node.x, node.y, node.order))
    if mode == "y":
        return sorted(nodes, key=lambda node: (node.y, node.x, node.order))
    return sorted(nodes, key=lambda node: (node.order, node.x, node.y))


def _resolved_sort_mode(canvas: CanvasData, sort_mode: str) -> str:
    if canvas.is_data_canvas():
        return "created"
    return sort_mode if sort_mode in CSV_SORT_MODES else "created"


def _resolved_canvas_specs(
    project: ProjectData,
    default_sort_mode: str,
    canvas_specs: list[CanvasCsvExportSpec] | None,
) -> list[tuple[CanvasData, CanvasCsvExportSpec]]:
    requested = {spec.canvas_id: spec for spec in (canvas_specs or [])}
    result: list[tuple[CanvasData, CanvasCsvExportSpec]] = []
    for canvas in project.canvases:
        spec = requested.get(canvas.id)
        if spec is None:
            spec = CanvasCsvExportSpec(canvas_id=canvas.id, enabled=True, sort_mode=default_sort_mode)
        result.append(
            (
                canvas,
                CanvasCsvExportSpec(
                    canvas_id=canvas.id,
                    enabled=bool(spec.enabled),
                    sort_mode=_resolved_sort_mode(canvas, spec.sort_mode),
                    target_folder=str(spec.target_folder or ""),
                    export_edges=bool(spec.export_edges) and not canvas.is_data_canvas(),
                    export_groups=bool(spec.export_groups) and not canvas.is_data_canvas(),
                    export_layout_info=bool(spec.export_layout_info) and not canvas.is_data_canvas(),
                ),
            )
        )
    return result


def _append_group_membership_columns(columns: list[Column], canvas: CanvasData) -> None:
    groups_by_id = {group.id: group for group in canvas.groups}
    for prop, header, data_type in GROUP_MEMBERSHIP_COLUMNS:
        columns.append(
            Column(
                _unique_header(columns, header),
                data_type,
                lambda node, prop=prop, groups_by_id=groups_by_id: _group_property_value(
                    groups_by_id.get(node.group_id),
                    prop,
                ),
            )
        )


def _append_layout_columns(columns: list[Column], canvas: CanvasData) -> None:
    groups_by_id = {group.id: group for group in canvas.groups}
    for prop, header, data_type in NODE_LAYOUT_COLUMNS:
        columns.append(
            Column(
                _unique_header(columns, header),
                data_type,
                lambda node, prop=prop: _node_layout_value(node, prop),
            )
        )
    if groups_by_id:
        for prop, header, data_type in GROUP_LAYOUT_COLUMNS:
            columns.append(
                Column(
                    _unique_header(columns, header),
                    data_type,
                    lambda node, prop=prop, groups_by_id=groups_by_id: _group_property_value(
                        groups_by_id.get(node.group_id),
                        prop,
                    ),
                )
            )


def _node_layout_value(node: Node, prop: str) -> str:
    if prop == "x":
        return _format_number(node.x)
    if prop == "y":
        return _format_number(node.y)
    return ""


def _group_property_value(group: BlueprintGroup | None, prop: str) -> str:
    if group is None:
        return ""
    if prop == "id":
        return group.id
    if prop == "title":
        return group.title
    if prop == "x":
        return _format_number(group.x)
    if prop == "y":
        return _format_number(group.y)
    if prop == "width":
        return _format_number(group.width)
    if prop == "height":
        return _format_number(group.height)
    if prop == "color":
        return group.color
    return ""


def _edge_targets_by_source(canvas: CanvasData) -> dict[str, str]:
    titles_by_id: dict[str, str] = {
        node.id: node.title.strip() or "未命名节点"
        for node in canvas.nodes
    }
    titles_by_id.update(
        {
            group.id: group.title.strip() or "未命名蓝图组"
            for group in canvas.groups
        }
    )
    targets: dict[str, list[str]] = defaultdict(list)
    for edge in canvas.valid_edges():
        if edge.source not in titles_by_id:
            continue
        target_name = titles_by_id.get(edge.target)
        if target_name:
            targets[edge.source].append(target_name)
    return {source: "|".join(names) for source, names in targets.items()}


def _field_keys(node: Node) -> list[tuple[str, NodeField]]:
    counts: defaultdict[str, int] = defaultdict(int)
    result: list[tuple[str, NodeField]] = []
    for field in node.fields:
        base = field.name.strip() or "字段"
        counts[base] += 1
        suffix = f"#{counts[base]}" if counts[base] > 1 else ""
        result.append((f"{base}{suffix}", field))
    return result


def _field_by_key(node: Node, key: str) -> NodeField | None:
    for candidate_key, field in _field_keys(node):
        if candidate_key == key:
            return field
    return None


def _field_value(field: NodeField | None) -> str:
    if not field:
        return ""
    if field.data_type == "图片":
        return field.image_path
    return field.value


def _property_value(field: NodeField | None, prop: str) -> str:
    if not field:
        return ""
    if prop == "x":
        return _format_number(field.x)
    if prop == "y":
        return _format_number(field.y)
    if prop == "width":
        return _format_number(field.width)
    if prop == "height":
        return _format_number(field.height)
    if prop == "font_size":
        return str(field.font_size)
    if prop == "text_color":
        return field.text_color
    if prop == "bg_color":
        return field.bg_color
    return ""


def _field_type_for_csv(data_type: str) -> str:
    return data_type or "文本"


def _unique_header(columns: list[Column], header: str) -> str:
    existing = {column.header for column in columns}
    if header not in existing:
        return header
    index = 2
    while f"{header}_{index}" in existing:
        index += 1
    return f"{header}_{index}"


def _format_number(value: float) -> str:
    return str(int(value)) if float(value).is_integer() else f"{value:g}"


def _canvas_csv_filename(canvas: CanvasData) -> str:
    return f"{_safe_filename(canvas.name)}.csv"


def _safe_filename(name: str) -> str:
    cleaned = "".join("_" if char in '\\/:*?"<>|' else char for char in name.strip())
    return cleaned or "game_data"
