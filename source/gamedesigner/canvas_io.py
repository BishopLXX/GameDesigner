from __future__ import annotations

import csv
from dataclasses import dataclass
from pathlib import Path

from .data_canvas import layout_canvas_nodes
from .models import CanvasData, NodeField, NodeTemplate, ProjectData, new_id


@dataclass(frozen=True)
class ImportedSheet:
    headers: list[str]
    rows: list[list[str]]


def import_canvas_sheet(
    project: ProjectData,
    canvas: CanvasData,
    source: str | Path,
) -> NodeTemplate:
    sheet = read_sheet(source)
    template = _build_template_for_canvas(canvas, sheet.headers)
    _replace_or_add_template(project, template)
    canvas.template_id = template.id
    canvas.nodes.clear()
    canvas.edges.clear()
    canvas.groups.clear()
    for index, row in enumerate(sheet.rows, start=1):
        node = template.create_node(0.0, 0.0)
        node.template_locked = canvas.is_data_canvas()
        for field_index, field in enumerate(node.fields):
            field.value = row[field_index] if field_index < len(row) else ""
        title_field = next((field for field in node.fields if field.id == node.title_field_id), None)
        if title_field is not None:
            title = title_field.value.strip() or title_field.name.strip() or template.name
            node.title = title
        node.order = index
        canvas.nodes.append(node)
    layout_canvas_nodes(canvas)
    return template


def read_sheet(source: str | Path) -> ImportedSheet:
    path = Path(source)
    suffix = path.suffix.lower()
    if suffix == ".csv":
        return _read_csv(path)
    if suffix in {".xlsx", ".xlsm"}:
        return _read_excel(path)
    raise ValueError("仅支持导入 CSV 或 Excel（.xlsx/.xlsm）文件。")


def _read_csv(path: Path) -> ImportedSheet:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        rows = list(csv.reader(handle))
    return _normalize_rows(rows)


def _read_excel(path: Path) -> ImportedSheet:
    try:
        from openpyxl import load_workbook
    except ModuleNotFoundError as exc:
        raise RuntimeError("缺少 Excel 导入依赖 openpyxl，请重新安装依赖或重新打包 exe。") from exc

    workbook = load_workbook(path, read_only=True, data_only=True)
    sheet = workbook.active
    rows = []
    for values in sheet.iter_rows(values_only=True):
        rows.append(["" if value is None else str(value) for value in values])
    workbook.close()
    return _normalize_rows(rows)


def _normalize_rows(raw_rows: list[list[str]]) -> ImportedSheet:
    trimmed = [_trim_row(list(row)) for row in raw_rows]
    meaningful = [row for row in trimmed if any(cell.strip() for cell in row)]
    if not meaningful:
        raise ValueError("表格内容为空。")
    headers = meaningful[0]
    if not any(header.strip() for header in headers):
        raise ValueError("表头不能为空。")
    normalized_headers = [_normalize_header(header, index) for index, header in enumerate(headers, start=1)]
    width = len(normalized_headers)
    rows = [_pad_row(row, width) for row in meaningful[1:]]
    return ImportedSheet(headers=normalized_headers, rows=rows)


def _build_template_for_canvas(canvas: CanvasData, headers: list[str]) -> NodeTemplate:
    fields = [NodeField(name=header, data_type="文本", value="") for header in headers]
    title_field_id = fields[0].id if fields else ""
    return NodeTemplate(
        id=canvas.template_id or new_id("template"),
        name=f"{canvas.name} 模板",
        icon="数" if canvas.is_data_canvas() else "N",
        icon_from_title=not canvas.is_data_canvas(),
        title_field_id=title_field_id,
        fields=fields,
    )


def _replace_or_add_template(project: ProjectData, template: NodeTemplate) -> None:
    for index, existing in enumerate(project.templates):
        if existing.id == template.id:
            project.templates[index] = template
            return
    project.templates.append(template)


def _trim_row(row: list[str]) -> list[str]:
    end = len(row)
    while end > 0 and not str(row[end - 1]).strip():
        end -= 1
    return [str(cell) for cell in row[:end]]


def _pad_row(row: list[str], width: int) -> list[str]:
    if len(row) < width:
        return [*row, *([""] * (width - len(row)))]
    return row[:width]


def _normalize_header(value: str, index: int) -> str:
    text = value.strip()
    return text or f"字段{index}"
