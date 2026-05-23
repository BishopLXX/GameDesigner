from __future__ import annotations

from .models import CanvasData, Node, NodeField, NodeTemplate, ProjectData


DATA_CANVAS_MARGIN_X = 72.0
DATA_CANVAS_MARGIN_Y = 72.0
DATA_CANVAS_GAP_X = 18.0
DATA_CANVAS_GAP_Y = 18.0
DATA_CANVAS_DEFAULT_CARD_WIDTH = 360.0
DATA_CANVAS_DEFAULT_CARD_HEIGHT = 220.0
DATA_CANVAS_GRID_MAX_WIDTH = 1360.0
DATA_CANVAS_THUMBNAIL_HEADER_HEIGHT = 64.0
DATA_CANVAS_THUMBNAIL_ROW_HEIGHT = 34.0


def apply_template_to_node(
    node: Node,
    template: NodeTemplate,
    *,
    preserve_values: bool = True,
    force_lock: bool | None = None,
) -> None:
    existing_fields = {field.id: field for field in node.fields}
    cloned_fields: list[NodeField] = []
    for template_field in template.fields:
        cloned = NodeField.from_dict(template_field.to_dict())
        if preserve_values:
            existing = existing_fields.get(cloned.id)
            if existing:
                if cloned.data_type == "图片":
                    if existing.data_type == "图片":
                        cloned.image_path = existing.image_path
                elif existing.data_type != "图片":
                    cloned.value = existing.value
        cloned_fields.append(cloned)

    node.node_type = "普通"
    node.canvas_id = ""
    node.link_path = ""
    node.color = template.color
    node.template_id = template.id
    node.fields = cloned_fields

    title_field_id = template.title_field_id if any(field.id == template.title_field_id for field in cloned_fields) else ""
    node.title_field_id = title_field_id
    node.title = _template_title(template, cloned_fields, title_field_id)
    if force_lock is not None:
        node.template_locked = force_lock


def sync_data_canvas(project: ProjectData, canvas: CanvasData) -> bool:
    if not canvas.is_data_canvas():
        return False
    template = data_canvas_template(project, canvas)
    changed = False
    canvas.normalize_node_order()
    if template is None:
        return changed
    for node in canvas.nodes:
        before = (
            node.node_type,
            node.canvas_id,
            node.link_path,
            node.color,
            node.icon,
            node.icon_from_title,
            node.title,
            node.title_field_id,
            node.template_id,
            node.template_locked,
            [(field.id, field.name, field.data_type, field.value, field.image_path) for field in node.fields],
        )
        apply_template_to_node(node, template, preserve_values=True, force_lock=True)
        after = (
            node.node_type,
            node.canvas_id,
            node.link_path,
            node.color,
            node.icon,
            node.icon_from_title,
            node.title,
            node.title_field_id,
            node.template_id,
            node.template_locked,
            [(field.id, field.name, field.data_type, field.value, field.image_path) for field in node.fields],
        )
        changed = changed or before != after
    if canvas.data_layout != "table":
        changed = layout_data_canvas(canvas) or changed
    return changed


def sync_locked_template_nodes(project: ProjectData) -> bool:
    template_map = {template.id: template for template in project.templates}
    changed = False
    for canvas in project.canvases:
        if canvas.is_data_canvas():
            changed = sync_data_canvas(project, canvas) or changed
            continue
        for node in canvas.nodes:
            template = template_map.get(node.template_id)
            if not node.template_locked or template is None:
                continue
            before = (
                node.color,
                node.icon,
                node.icon_from_title,
                node.title,
                node.title_field_id,
                [(field.id, field.name, field.data_type, field.value, field.image_path) for field in node.fields],
            )
            apply_template_to_node(node, template, preserve_values=True, force_lock=True)
            after = (
                node.color,
                node.icon,
                node.icon_from_title,
                node.title,
                node.title_field_id,
                [(field.id, field.name, field.data_type, field.value, field.image_path) for field in node.fields],
            )
            changed = changed or before != after
    return changed


def data_canvas_template(project: ProjectData, canvas: CanvasData) -> NodeTemplate | None:
    template = project.find_template(canvas.template_id)
    if template is not None:
        return template
    if not project.templates:
        canvas.template_id = ""
        return None
    canvas.template_id = project.templates[0].id
    return project.templates[0]


def layout_data_canvas(canvas: CanvasData) -> bool:
    if not canvas.is_data_canvas():
        return False
    return layout_canvas_nodes(canvas)


def layout_canvas_nodes(canvas: CanvasData) -> bool:
    ordered_nodes = sorted(canvas.nodes, key=lambda item: item.order)
    if not ordered_nodes:
        return False
    if canvas.data_layout == "table":
        return False
    positions = _planned_positions(canvas, ordered_nodes)
    changed = False
    for node in ordered_nodes:
        x, y = positions[node.id]
        changed = _set_node_position(node, x, y) or changed
    return changed


def reorder_data_canvas_node(canvas: CanvasData, node_id: str, x: float, y: float) -> bool:
    if not canvas.is_data_canvas():
        return False
    if canvas.data_layout == "table":
        return False
    moving = canvas.find_node(node_id)
    if moving is None:
        return False

    canvas.normalize_node_order()
    ordered_nodes = sorted(canvas.nodes, key=lambda item: item.order)
    if len(ordered_nodes) <= 1:
        return layout_data_canvas(canvas)

    original_order = [node.id for node in ordered_nodes]
    remaining = [node for node in ordered_nodes if node.id != node_id]
    target_center = _node_center_from_top_left(moving, x, y)
    best_index = 0
    best_distance: float | None = None

    for index in range(len(remaining) + 1):
        candidate_order = list(remaining)
        candidate_order.insert(index, moving)
        positions = _planned_positions(canvas, candidate_order)
        candidate_center = _node_center_from_top_left(moving, *positions[moving.id])
        distance = _distance_sq(target_center, candidate_center)
        if best_distance is None or distance < best_distance:
            best_distance = distance
            best_index = index

    reordered = list(remaining)
    reordered.insert(best_index, moving)
    changed = [node.id for node in reordered] != original_order
    for index, node in enumerate(reordered, start=1):
        if node.order != index:
            changed = True
            node.order = index
    return layout_data_canvas(canvas) or changed


def _planned_positions(canvas: CanvasData, ordered_nodes: list[Node]) -> dict[str, tuple[float, float]]:
    positions: dict[str, tuple[float, float]] = {}
    if not ordered_nodes:
        return positions
    if canvas.data_layout == "horizontal":
        y = DATA_CANVAS_MARGIN_Y
        if canvas.data_row_style == "thumbnail":
            y += DATA_CANVAS_THUMBNAIL_HEADER_HEIGHT
        for node in ordered_nodes:
            positions[node.id] = (DATA_CANVAS_MARGIN_X, y)
            if canvas.data_row_style == "thumbnail":
                y += DATA_CANVAS_THUMBNAIL_ROW_HEIGHT
            else:
                y += _node_height(node) + DATA_CANVAS_GAP_Y
        return positions

    row_limit = 0
    if canvas.data_grid_rows > 0:
        row_limit = max(0, int(canvas.data_grid_rows))

    column_width = max(
        [_node_width(node) for node in ordered_nodes] + [DATA_CANVAS_DEFAULT_CARD_WIDTH]
    )
    if row_limit > 0:
        column_count = max(1, (len(ordered_nodes) + row_limit - 1) // row_limit)
    else:
        column_count = max(
            1,
            min(
                len(ordered_nodes),
                int((DATA_CANVAS_GRID_MAX_WIDTH + DATA_CANVAS_GAP_X) // (column_width + DATA_CANVAS_GAP_X)),
            ),
        )
    row_heights: list[float] = []
    for index, node in enumerate(ordered_nodes):
        row = index % row_limit if row_limit > 0 else index // column_count
        height = _node_height(node)
        if row >= len(row_heights):
            row_heights.append(height)
        else:
            row_heights[row] = max(row_heights[row], height)

    for index, node in enumerate(ordered_nodes):
        if row_limit > 0:
            row = index % row_limit
            column = index // row_limit
        else:
            row = index // column_count
            column = index % column_count
        x = DATA_CANVAS_MARGIN_X + column * (column_width + DATA_CANVAS_GAP_X)
        row_y = DATA_CANVAS_MARGIN_Y + sum(row_heights[:row]) + DATA_CANVAS_GAP_Y * row
        positions[node.id] = (x, row_y)
    return positions


def _node_width(node: Node) -> float:
    return node.width if node.width > 0 else DATA_CANVAS_DEFAULT_CARD_WIDTH


def _node_height(node: Node) -> float:
    return node.height if node.height > 0 else DATA_CANVAS_DEFAULT_CARD_HEIGHT


def _node_center_from_top_left(node: Node, x: float, y: float) -> tuple[float, float]:
    return (x + _node_width(node) / 2.0, y + _node_height(node) / 2.0)


def _distance_sq(a: tuple[float, float], b: tuple[float, float]) -> float:
    dx = a[0] - b[0]
    dy = a[1] - b[1]
    return dx * dx + dy * dy


def _set_node_position(node: Node, x: float, y: float) -> bool:
    changed = abs(node.x - x) > 0.001 or abs(node.y - y) > 0.001
    node.x = x
    node.y = y
    return changed


def _template_title(template: NodeTemplate, fields: list[NodeField], title_field_id: str) -> str:
    if title_field_id:
        title_field = next((field for field in fields if field.id == title_field_id), None)
        if title_field:
            text = (title_field.value or title_field.name).strip()
            if text:
                return text
    return template.name
