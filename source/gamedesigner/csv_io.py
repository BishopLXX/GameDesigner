from __future__ import annotations

import csv
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from .models import FIELD_EXPORT_PROPS, CanvasData, Node, NodeField, ProjectData


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


def export_game_csv(
    project: ProjectData,
    target: str | Path,
    canvas: CanvasData | None = None,
    sort_mode: str = "created",
) -> Path:
    path = Path(target)
    if path.suffix.lower() != ".csv":
        path = path / f"{_safe_filename(project.name)}.csv"
    path.parent.mkdir(parents=True, exist_ok=True)

    project.ensure_canvas_structure()
    source_canvas = canvas or project.root_canvas()
    return _write_canvas_csv(path, source_canvas, sort_mode)


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

    used_names_by_folder: dict[Path, dict[str, int]] = {}
    paths: list[Path] = []
    export_specs = _resolved_canvas_specs(project, sort_mode, canvas_specs)
    for canvas, spec in export_specs:
        if not spec.enabled:
            continue
        export_folder = Path(spec.target_folder.strip()) if spec.target_folder.strip() else folder
        if export_folder.suffix.lower() == ".csv":
            export_folder = export_folder.parent
        export_folder.mkdir(parents=True, exist_ok=True)
        used_names = used_names_by_folder.setdefault(export_folder, {})
        path = export_folder / _canvas_csv_filename(project, canvas, used_names)
        paths.append(_write_canvas_csv(path, canvas, spec.sort_mode))
    return paths


def _write_canvas_csv(path: Path, source_canvas: CanvasData, sort_mode: str) -> Path:
    source_canvas.normalize_node_order()
    nodes = _sorted_nodes(source_canvas.nodes, _resolved_sort_mode(source_canvas, sort_mode))
    columns = _build_columns(nodes)
    with path.open("w", encoding="utf-8-sig", newline="") as file:
        writer = csv.writer(file)
        writer.writerow([column.header for column in columns])
        writer.writerow([column.data_type for column in columns])
        for node in nodes:
            writer.writerow([column.getter(node) for column in columns])
    return path


def _build_columns(nodes: list[Node]) -> list[Column]:
    columns = [
        Column(header, data_type, getter)
        for header, data_type, getter in BASE_COLUMNS
    ]

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
                ),
            )
        )
    return result


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


def _canvas_csv_filename(
    project: ProjectData,
    canvas: CanvasData,
    used_names: dict[str, int],
) -> str:
    base = f"{_safe_filename(project.name)}__{_safe_filename(canvas.name)}"
    used_names[base] = used_names.get(base, 0) + 1
    if used_names[base] > 1:
        base = f"{base}_{used_names[base]}"
    return f"{base}.csv"


def _safe_filename(name: str) -> str:
    cleaned = "".join("_" if char in '\\/:*?"<>|' else char for char in name.strip())
    return cleaned or "game_data"
