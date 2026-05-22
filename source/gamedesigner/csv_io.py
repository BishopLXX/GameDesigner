from __future__ import annotations

import csv
import json
from pathlib import Path

from .models import DEFAULT_NODE_COLOR, Edge, Node, NodeField, NodeTemplate, ProjectData


NODES_FILE = "nodes.csv"
EDGES_FILE = "edges.csv"
TEMPLATES_FILE = "templates.csv"


def export_project_csv(project: ProjectData, target_dir: str | Path) -> Path:
    folder = Path(target_dir)
    folder.mkdir(parents=True, exist_ok=True)
    _write_nodes(project, folder / NODES_FILE)
    _write_edges(project, folder / EDGES_FILE)
    _write_templates(project, folder / TEMPLATES_FILE)
    return folder


def import_project_csv(source: str | Path) -> ProjectData:
    source_path = Path(source)
    folder = source_path.parent if source_path.is_file() else source_path
    nodes_path = folder / NODES_FILE
    edges_path = folder / EDGES_FILE
    templates_path = folder / TEMPLATES_FILE

    project = ProjectData(name=folder.name or "CSV导入项目")
    if nodes_path.exists():
        project.nodes = _read_nodes(nodes_path)
    if edges_path.exists():
        project.edges = _read_edges(edges_path)
    if templates_path.exists():
        project.templates = _read_templates(templates_path)
    project.remove_broken_edges()
    if not project.templates:
        project.templates = []
    return project


def _write_nodes(project: ProjectData, path: Path) -> None:
    with path.open("w", encoding="utf-8-sig", newline="") as file:
        writer = csv.DictWriter(
            file,
            fieldnames=["id", "title", "x", "y", "width", "height", "color", "icon", "fields_json"],
        )
        writer.writeheader()
        for node in project.nodes:
            writer.writerow(
                {
                    "id": node.id,
                    "title": node.title,
                    "x": node.x,
                    "y": node.y,
                    "width": node.width,
                    "height": node.height,
                    "color": node.color,
                    "icon": node.icon,
                    "fields_json": json.dumps(
                        [field.to_dict() for field in node.fields], ensure_ascii=False
                    ),
                }
            )


def _write_edges(project: ProjectData, path: Path) -> None:
    with path.open("w", encoding="utf-8-sig", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=["id", "source", "target", "label", "style"])
        writer.writeheader()
        for edge in project.valid_edges():
            writer.writerow(edge.to_dict())


def _write_templates(project: ProjectData, path: Path) -> None:
    with path.open("w", encoding="utf-8-sig", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=["id", "name", "color", "icon", "fields_json"])
        writer.writeheader()
        for template in project.templates:
            writer.writerow(
                {
                    "id": template.id,
                    "name": template.name,
                    "color": template.color,
                    "icon": template.icon,
                    "fields_json": json.dumps(
                        [field.to_dict() for field in template.fields], ensure_ascii=False
                    ),
                }
            )


def _read_nodes(path: Path) -> list[Node]:
    nodes: list[Node] = []
    with path.open("r", encoding="utf-8-sig", newline="") as file:
        for row in csv.DictReader(file):
            fields = _loads_fields(row.get("fields_json", "[]"))
            nodes.append(
                Node(
                    id=row.get("id") or "",
                    title=row.get("title") or "新节点",
                    x=_float_or(row.get("x"), 0.0),
                    y=_float_or(row.get("y"), 0.0),
                    width=_float_or(row.get("width"), 0.0),
                    height=_float_or(row.get("height"), 0.0),
                    color=row.get("color") or DEFAULT_NODE_COLOR,
                    icon=row.get("icon") or "",
                    fields=fields,
                )
            )
    return nodes


def _read_edges(path: Path) -> list[Edge]:
    edges: list[Edge] = []
    with path.open("r", encoding="utf-8-sig", newline="") as file:
        for row in csv.DictReader(file):
            edges.append(
                Edge(
                    id=row.get("id") or "",
                    source=row.get("source") or "",
                    target=row.get("target") or "",
                    label=row.get("label") or "",
                    style=row.get("style") or "curve",
                )
            )
    return edges


def _read_templates(path: Path) -> list[NodeTemplate]:
    templates: list[NodeTemplate] = []
    with path.open("r", encoding="utf-8-sig", newline="") as file:
        for row in csv.DictReader(file):
            templates.append(
                NodeTemplate(
                    id=row.get("id") or "",
                    name=row.get("name") or "节点模板",
                    color=row.get("color") or DEFAULT_NODE_COLOR,
                    icon=row.get("icon") or "",
                    fields=_loads_fields(row.get("fields_json", "[]")),
                )
            )
    return templates


def _loads_fields(raw: str | None) -> list[NodeField]:
    if not raw:
        return []
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return []
    if not isinstance(data, list):
        return []
    return [NodeField.from_dict(item) for item in data if isinstance(item, dict)]


def _float_or(value: str | None, fallback: float) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return fallback
