from __future__ import annotations

from collections.abc import Sequence

from .models import NodeField


VISUAL_NODE_HEADER_HEIGHT = 52.0
VISUAL_NODE_MIN_WIDTH = 220.0
VISUAL_NODE_MIN_HEIGHT = 140.0
VISUAL_NODE_DEFAULT_WIDTH = 430.0
VISUAL_NODE_DEFAULT_HEIGHT = 300.0
VISUAL_NODE_CONTENT_PADDING = 24.0


def visual_node_size(
    fields: Sequence[NodeField],
    node_width: float = 0.0,
    node_height: float = 0.0,
) -> tuple[float, float]:
    if node_width > 0 and node_height > 0:
        return max(VISUAL_NODE_MIN_WIDTH, node_width), max(VISUAL_NODE_MIN_HEIGHT, node_height)

    visual_fields = [field for field in fields if field.has_visual_layout()]
    natural_width = max(
        [field.x + field.width + VISUAL_NODE_CONTENT_PADDING for field in visual_fields]
        + [VISUAL_NODE_DEFAULT_WIDTH]
    )
    natural_height = max(
        [
            VISUAL_NODE_HEADER_HEIGHT
            + field.y
            + field.height
            + VISUAL_NODE_CONTENT_PADDING
            for field in visual_fields
        ]
        + [VISUAL_NODE_DEFAULT_HEIGHT]
    )
    return max(VISUAL_NODE_MIN_WIDTH, natural_width), max(VISUAL_NODE_MIN_HEIGHT, natural_height)
