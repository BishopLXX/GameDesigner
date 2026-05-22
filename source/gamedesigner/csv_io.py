from __future__ import annotations

import csv
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from .models import FIELD_EXPORT_PROPS, Node, NodeField, ProjectData


BASE_COLUMNS = [
    ("名字", "文本", lambda node: node.title),
    ("图标", "文本", lambda node: node.icon),
]

PROP_LABELS = {
    "name": "字段",
    "data_type": "类型",
    "value": "内容",
    "image_path": "图片",
    "x": "X",
    "y": "Y",
    "width": "宽",
    "height": "高",
    "font_size": "字号",
    "text_color": "文字色",
    "bg_color": "背景色",
}

PROP_TYPES = {
    "name": "文本",
    "data_type": "文本",
    "value": "文本",
    "image_path": "资源路径",
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


def export_game_csv(project: ProjectData, target: str | Path) -> Path:
    path = Path(target)
    if path.suffix.lower() != ".csv":
        path = path / f"{_safe_filename(project.name)}.csv"
    path.parent.mkdir(parents=True, exist_ok=True)

    columns = _build_columns(project)
    with path.open("w", encoding="utf-8-sig", newline="") as file:
        writer = csv.writer(file)
        writer.writerow([column.header for column in columns])
        writer.writerow([column.data_type for column in columns])
        for node in project.nodes:
            writer.writerow([column.getter(node) for column in columns])
    return path


def _build_columns(project: ProjectData) -> list[Column]:
    columns = [
        Column(header, data_type, getter)
        for header, data_type, getter in BASE_COLUMNS
    ]

    field_columns: dict[str, tuple[str, str]] = {}
    pinned_columns: dict[tuple[str, str], str] = {}
    for node in project.nodes:
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
    if prop == "name":
        return field.name
    if prop == "data_type":
        return field.data_type
    if prop == "value":
        return _field_value(field)
    if prop == "image_path":
        return field.image_path
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
    return "图片" if data_type == "图片" else data_type or "文本"


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


def _safe_filename(name: str) -> str:
    cleaned = "".join("_" if char in '\\/:*?"<>|' else char for char in name.strip())
    return cleaned or "game_data"
