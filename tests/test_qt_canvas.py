import os
import math
import unittest
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QEvent, QPoint, QPointF, Qt
from PySide6.QtGui import QAction, QImage, QKeyEvent, QPainter, QPixmap, QTextCursor
from PySide6.QtWidgets import QApplication, QMenu

from gamedesigner.data_canvas import layout_data_canvas
from gamedesigner.models import BlueprintGroup, CanvasData, DesignNote, Node, NodeField, NodeTemplate, ProjectData
from gamedesigner.qt_canvas import InlineNodeFieldEditor, NodeGraphView


class _ScenePointerEvent:
    def __init__(self, pos: QPointF, scene_pos: QPointF) -> None:
        self._pos = pos
        self._scene_pos = scene_pos
        self.accepted = False

    def button(self):
        return Qt.LeftButton

    def pos(self) -> QPointF:
        return self._pos

    def scenePos(self) -> QPointF:
        return self._scene_pos

    def accept(self) -> None:
        self.accepted = True


class _ViewMouseEvent:
    def __init__(self, button: Qt.MouseButton, pos: QPoint, global_pos: QPoint | None = None) -> None:
        self._button = button
        self._pos = pos
        self._global_pos = global_pos or pos
        self.accepted = False

    def button(self):
        return self._button

    def position(self) -> QPointF:
        return QPointF(self._pos)

    def globalPosition(self) -> QPointF:
        return QPointF(self._global_pos)

    def accept(self) -> None:
        self.accepted = True


class _ViewWheelEvent:
    def __init__(self, pos: QPoint, angle_delta_y: int) -> None:
        self._pos = pos
        self._angle_delta_y = angle_delta_y
        self.accepted = False

    def position(self) -> QPointF:
        return QPointF(self._pos)

    def pixelDelta(self) -> QPoint:
        return QPoint(0, 0)

    def angleDelta(self) -> QPoint:
        return QPoint(0, self._angle_delta_y)

    def accept(self) -> None:
        self.accepted = True


class _ViewContextMenuEvent:
    def __init__(self, pos: QPoint, global_pos: QPoint | None = None) -> None:
        self._pos = pos
        self._global_pos = global_pos or pos
        self.accepted = False

    def pos(self) -> QPoint:
        return self._pos

    def globalPos(self) -> QPoint:
        return self._global_pos

    def accept(self) -> None:
        self.accepted = True


class _KeyEvent:
    def __init__(self, key: Qt.Key) -> None:
        self._key = key
        self.accepted = False

    def key(self):
        return self._key

    def isAutoRepeat(self) -> bool:
        return False

    def accept(self) -> None:
        self.accepted = True


def _iter_menu_actions(menu: QMenu):
    for action in menu.actions():
        yield action
        submenu = action.menu()
        if submenu is not None:
            yield from _iter_menu_actions(submenu)


class QtCanvasTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def test_rebuild_resets_stale_hand_cursor_to_arrow(self) -> None:
        view = NodeGraphView(ProjectData(nodes=[Node(title="Current")]))
        view.setCursor(Qt.OpenHandCursor)
        view.viewport().setCursor(Qt.OpenHandCursor)

        view.rebuild()

        self.assertEqual(view.cursor().shape(), Qt.ArrowCursor)
        self.assertEqual(view.viewport().cursor().shape(), Qt.ArrowCursor)
        view.deleteLater()

    def test_show_event_resets_stale_hand_cursor_to_arrow(self) -> None:
        view = NodeGraphView(ProjectData(nodes=[Node(title="Current")]))
        view.setCursor(Qt.OpenHandCursor)
        view.viewport().setCursor(Qt.OpenHandCursor)

        view.show()
        self.app.processEvents()

        self.assertEqual(view.cursor().shape(), Qt.ArrowCursor)
        self.assertEqual(view.viewport().cursor().shape(), Qt.ArrowCursor)
        view.deleteLater()

    def test_read_only_draggable_node_can_resize_from_handle(self) -> None:
        project = ProjectData(nodes=[Node(title="Recent", width=350, height=156)])
        view = NodeGraphView(project, read_only=True, allow_node_drag=True)
        node = project.nodes[0]
        item = view.node_items[node.id]
        handle_pos = QPointF(item.width - 1, item.height - 1)
        press_scene_pos = item.mapToScene(handle_pos)

        item.mousePressEvent(_ScenePointerEvent(handle_pos, press_scene_pos))
        item.mouseMoveEvent(_ScenePointerEvent(handle_pos, press_scene_pos + QPointF(48, 32)))

        self.assertEqual(node.width, 398)
        self.assertEqual(node.height, 188)
        view.deleteLater()

    def test_compact_zoom_click_node_requests_preview_without_inline_edit(self) -> None:
        canvas = CanvasData(name="主画布")
        node = canvas.add_node(
            Node(
                title="紧凑节点",
                x=0,
                y=0,
                width=320,
                height=160,
                fields=[NodeField("描述", "长文本", "内容")],
            )
        )
        view = NodeGraphView(canvas)
        view.scale(0.5, 0.5)
        emitted: list[str] = []
        activated: list[str] = []
        view.nodePreviewRequested.connect(emitted.append)
        view.nodeActivated.connect(activated.append)
        item = view.node_items[node.id]
        local_pos = QPointF(120, 64)
        scene_pos = item.mapToScene(local_pos)

        self.assertTrue(view.is_compact_node_preview_mode())
        self.assertIsNone(item._editable_node_text_at(local_pos))
        self.assertIsNone(item._editable_field_at(local_pos))

        item.mouseReleaseEvent(_ScenePointerEvent(local_pos, scene_pos))

        self.assertEqual(emitted, [node.id])
        self.assertEqual(activated, [])
        self.assertIsNone(view._inline_proxy)
        view.deleteLater()

    def test_compact_zoom_double_click_node_requests_preview_not_activation(self) -> None:
        canvas = CanvasData(name="主画布")
        node = canvas.add_node(Node(title="子画布", node_type="画布", x=0, y=0, width=320, height=160))
        view = NodeGraphView(canvas)
        view.scale(0.5, 0.5)
        emitted: list[str] = []
        activated: list[str] = []
        view.nodePreviewRequested.connect(emitted.append)
        view.nodeActivated.connect(activated.append)
        item = view.node_items[node.id]
        local_pos = QPointF(120, 64)
        scene_pos = item.mapToScene(local_pos)

        item.mouseDoubleClickEvent(_ScenePointerEvent(local_pos, scene_pos))

        self.assertEqual(emitted, [node.id])
        self.assertEqual(activated, [])
        view.deleteLater()

    def test_dragging_node_edge_handle_creates_edge(self) -> None:
        canvas = CanvasData(name="主画布")
        source = canvas.add_node(Node(title="源", x=0, y=0, width=320, height=180))
        target = canvas.add_node(Node(title="目标", x=480, y=20, width=320, height=180))
        view = NodeGraphView(canvas)
        created: list[tuple[str, str]] = []
        view.edgeCreated.connect(lambda source_id, target_id: created.append((source_id, target_id)))
        source_item = view.node_items[source.id]
        target_item = view.node_items[target.id]
        handle_pos = source_item._connection_handle_points()["right"]
        press_scene_pos = source_item.mapToScene(handle_pos)
        release_scene_pos = target_item.sceneBoundingRect().center()

        source_item.mousePressEvent(_ScenePointerEvent(handle_pos, press_scene_pos))
        self.assertTrue(view.is_connection_dragging_from(source.id))
        self.assertEqual(view.connection_anchor_scene, press_scene_pos)

        source_item.mouseMoveEvent(
            _ScenePointerEvent(source_item.mapFromScene(release_scene_pos), release_scene_pos)
        )
        source_item.mouseReleaseEvent(
            _ScenePointerEvent(source_item.mapFromScene(release_scene_pos), release_scene_pos)
        )

        self.app.processEvents()
        self.assertEqual(created, [(source.id, target.id)])
        self.assertFalse(view.connecting)
        view.deleteLater()

    def test_connection_drag_near_viewport_edge_auto_pans_canvas(self) -> None:
        canvas = CanvasData(name="主画布")
        source = canvas.add_node(Node(title="源", x=0, y=0, width=320, height=180))
        view = NodeGraphView(canvas)
        view.resize(420, 280)
        view.show()
        view.centerOn(source.x, source.y)
        self.app.processEvents()
        source_item = view.node_items[source.id]
        handle_pos = source_item._connection_handle_points()["right"]
        anchor_scene = source_item.mapToScene(handle_pos)
        edge_view_pos = QPoint(view.viewport().width() - 2, view.viewport().height() // 2)
        edge_scene_pos = view.mapToScene(edge_view_pos)

        view.begin_connection_drag(source.id, anchor_scene)
        view.update_connection_drag(edge_scene_pos)
        before_scroll = view.horizontalScrollBar().value()

        view._tick_connection_auto_pan()

        self.assertGreater(view.horizontalScrollBar().value(), before_scroll)
        self.assertEqual(view._connection_auto_pan_view_pos, edge_view_pos)
        self.assertAlmostEqual(view.mouse_scene.x(), view.mapToScene(edge_view_pos).x(), delta=0.01)
        self.assertAlmostEqual(view.mouse_scene.y(), view.mapToScene(edge_view_pos).y(), delta=0.01)

        view.cancel_connection()
        self.assertFalse(view._connection_auto_pan_timer.isActive())
        view.deleteLater()

    def test_drag_edge_from_node_to_pasted_node_defers_rebuild_until_release_returns(self) -> None:
        canvas = CanvasData(name="主画布")
        source = canvas.add_node(Node(title="源", x=0, y=0, width=320, height=180))
        original = canvas.add_node(Node(title="复制源", x=0, y=280, width=320, height=180))
        pasted = canvas.add_node(Node.from_dict(original.to_dict()))
        pasted.id = "node_pasted"
        pasted.x = 420
        pasted.y = 280
        view = NodeGraphView(canvas)
        source_item = view.node_items[source.id]
        target_item = view.node_items[pasted.id]
        handle_pos = source_item._connection_handle_points()["bottom"]
        press_scene_pos = source_item.mapToScene(handle_pos)
        release_scene_pos = target_item.sceneBoundingRect().center()

        def add_and_rebuild(source_id: str, target_id: str) -> None:
            canvas.add_edge(source_id, target_id)
            view.rebuild()

        view.edgeCreated.connect(add_and_rebuild)

        source_item.mousePressEvent(_ScenePointerEvent(handle_pos, press_scene_pos))
        source_item.mouseReleaseEvent(
            _ScenePointerEvent(source_item.mapFromScene(release_scene_pos), release_scene_pos)
        )

        self.assertEqual(canvas.edges, [])
        self.app.processEvents()
        self.assertEqual(len(canvas.edges), 1)
        self.assertEqual((canvas.edges[0].source, canvas.edges[0].target), (source.id, pasted.id))
        self.assertIn(pasted.id, view.node_items)
        view.deleteLater()

    def test_orthogonal_edge_keeps_stubs_perpendicular_to_nodes(self) -> None:
        canvas = CanvasData(name="主画布")
        source = canvas.add_node(Node(title="源", x=0, y=0, width=320, height=180))
        target = canvas.add_node(Node(title="目标", x=80, y=360, width=320, height=180))
        edge = canvas.add_edge(source.id, target.id)
        edge.style = "orthogonal"
        edge.orthogonal_route = [{"x": 260.0, "y": 270.0}]

        view = NodeGraphView(canvas)
        item = view.edge_items[edge.id]
        points = list(item.path().toSubpathPolygons()[0])
        source_rect = view.node_items[source.id].sceneBoundingRect()
        target_rect = view.node_items[target.id].sceneBoundingRect()

        self.assertEqual(points[0].x(), source_rect.center().x())
        self.assertEqual(points[0].y(), source_rect.bottom())
        self.assertEqual(points[1].x(), points[0].x())
        self.assertGreater(points[1].y(), points[0].y())
        self.assertLess(points[-2].y(), points[-1].y())
        self.assertEqual(points[-2].x(), points[-1].x())
        view.deleteLater()

    def test_orthogonal_route_point_near_target_entry_axis_merges_into_entry_line(self) -> None:
        canvas = CanvasData(name="主画布")
        source = canvas.add_node(Node(title="源", x=0, y=0, width=320, height=180))
        target = canvas.add_node(Node(title="目标", x=80, y=360, width=320, height=180))
        edge = canvas.add_edge(source.id, target.id)
        edge.style = "orthogonal"
        edge.orthogonal_route = [{"x": 260.0, "y": 270.0}]

        view = NodeGraphView(canvas)
        item = view.edge_items[edge.id]
        route_points = item._route_points()
        points = list(item.path().toSubpathPolygons()[0])
        source_rect = view.node_items[source.id].sceneBoundingRect()
        target_rect = view.node_items[target.id].sceneBoundingRect()
        target_anchor = item._anchor(target_rect, source_rect)
        distances = [
            math.hypot(points[index + 1].x() - points[index].x(), points[index + 1].y() - points[index].y())
            for index in range(len(points) - 1)
        ]

        self.assertEqual(len(route_points), 1)
        self.assertAlmostEqual(route_points[0].x(), target_anchor.x())
        self.assertTrue(all(distance > 14.0 for distance in distances))
        self.assertEqual(points[-2].x(), target_anchor.x())
        view.deleteLater()

    def test_dragging_orthogonal_edge_bend_updates_edge_and_emits_change(self) -> None:
        canvas = CanvasData(name="主画布")
        source = canvas.add_node(Node(title="源", x=0, y=0, width=320, height=180))
        target = canvas.add_node(Node(title="目标", x=560, y=80, width=320, height=180))
        edge = canvas.add_edge(source.id, target.id)
        edge.style = "orthogonal"
        view = NodeGraphView(canvas)
        changed: list[bool] = []
        view.projectChanged.connect(lambda: changed.append(True))
        item = view.edge_items[edge.id]
        view.select_edge(edge.id)
        handle_scene = item._bend_handles()[0][0]
        handle_pos = item.mapFromScene(handle_scene)

        item.mousePressEvent(_ScenePointerEvent(handle_pos, handle_scene))
        item.mouseMoveEvent(_ScenePointerEvent(handle_pos, QPointF(455, handle_scene.y() + 18)))
        item.mouseReleaseEvent(_ScenePointerEvent(handle_pos, QPointF(455, handle_scene.y() + 18)))

        self.assertEqual(edge.orthogonal_route, [{"x": 455.0, "y": handle_scene.y() + 18}])
        self.assertAlmostEqual(edge.orthogonal_bend_x, 455.0)
        self.assertAlmostEqual(edge.orthogonal_bend_y, handle_scene.y() + 18)
        self.assertEqual(changed, [True])
        view.deleteLater()

    def test_close_orthogonal_route_points_merge(self) -> None:
        canvas = CanvasData(name="主画布")
        source = canvas.add_node(Node(title="源", x=0, y=0, width=320, height=180))
        target = canvas.add_node(Node(title="目标", x=560, y=260, width=320, height=180))
        edge = canvas.add_edge(source.id, target.id)
        edge.style = "orthogonal"
        view = NodeGraphView(canvas)
        item = view.edge_items[edge.id]

        item._set_route_points([QPointF(320, 180), QPointF(330, 188), QPointF(460, 230)])

        self.assertEqual(len(edge.orthogonal_route), 2)
        self.assertAlmostEqual(edge.orthogonal_route[0]["x"], 325.0)
        self.assertAlmostEqual(edge.orthogonal_route[0]["y"], 184.0)
        view.deleteLater()

    def test_short_orthogonal_step_is_removed_from_rendered_path(self) -> None:
        canvas = CanvasData(name="主画布")
        source = canvas.add_node(Node(title="源", x=0, y=0, width=320, height=180))
        target = canvas.add_node(Node(title="目标", x=560, y=260, width=320, height=180))
        edge = canvas.add_edge(source.id, target.id)
        edge.style = "orthogonal"
        edge.orthogonal_route = [{"x": 380.0, "y": 190.0}, {"x": 392.0, "y": 190.0}, {"x": 500.0, "y": 260.0}]
        view = NodeGraphView(canvas)
        item = view.edge_items[edge.id]
        points = list(item.path().toSubpathPolygons()[0])

        distances = [
            math.hypot(points[index + 1].x() - points[index].x(), points[index + 1].y() - points[index].y())
            for index in range(len(points) - 1)
        ]

        self.assertTrue(all(distance > 14.0 for distance in distances))
        view.deleteLater()

    def test_clicking_orthogonal_segment_inserts_route_point_and_right_click_deletes_it(self) -> None:
        canvas = CanvasData(name="主画布")
        source = canvas.add_node(Node(title="源", x=0, y=0, width=320, height=180))
        target = canvas.add_node(Node(title="目标", x=560, y=260, width=320, height=180))
        edge = canvas.add_edge(source.id, target.id)
        edge.style = "orthogonal"
        view = NodeGraphView(canvas)
        changed: list[bool] = []
        deleted_edges: list[str] = []
        view.projectChanged.connect(lambda: changed.append(True))
        view.edgeDeleteRequested.connect(lambda edge_id: deleted_edges.append(edge_id))
        item = view.edge_items[edge.id]
        view.select_edge(edge.id)
        points = item._orthogonal_points(
            item._anchor(item.source.sceneBoundingRect(), item.target.sceneBoundingRect()),
            item._anchor(item.target.sceneBoundingRect(), item.source.sceneBoundingRect()),
            item.source.sceneBoundingRect(),
            item.target.sceneBoundingRect(),
        )
        insert_scene = QPointF((points[1].x() + points[2].x()) / 2, (points[1].y() + points[2].y()) / 2)

        item.mousePressEvent(_ScenePointerEvent(item.mapFromScene(insert_scene), insert_scene))
        item.mouseReleaseEvent(_ScenePointerEvent(item.mapFromScene(insert_scene), insert_scene))

        self.assertEqual(edge.orthogonal_route, [{"x": insert_scene.x(), "y": insert_scene.y()}])
        self.assertEqual(changed, [True])

        self.assertEqual(item.route_point_index_at_scene(insert_scene), 0)
        self.assertTrue(item.delete_route_point_at_scene(insert_scene))
        view.projectChanged.emit()

        self.assertEqual(edge.orthogonal_route, [])
        self.assertEqual(changed, [True, True])
        self.assertEqual(deleted_edges, [])
        view.deleteLater()

    def test_delete_key_on_selected_edge_deletes_whole_edge_even_when_route_point_selected(self) -> None:
        canvas = CanvasData(name="主画布")
        source = canvas.add_node(Node(title="源", x=0, y=0, width=320, height=180))
        target = canvas.add_node(Node(title="目标", x=560, y=260, width=320, height=180))
        edge = canvas.add_edge(source.id, target.id)
        edge.style = "orthogonal"
        edge.orthogonal_route = [{"x": 410.0, "y": 170.0}]
        view = NodeGraphView(canvas)
        deleted_edges: list[str] = []
        view.edgeDeleteRequested.connect(lambda edge_id: deleted_edges.append(edge_id))
        item = view.edge_items[edge.id]
        view.select_edge(edge.id)
        item._selected_route_index = 0

        delete = _KeyEvent(Qt.Key_Delete)
        view.keyPressEvent(delete)

        self.assertTrue(delete.accepted)
        self.assertEqual(deleted_edges, [edge.id])
        self.assertEqual(edge.orthogonal_route, [{"x": 410.0, "y": 170.0}])
        view.deleteLater()

    def test_edge_context_menu_add_label_action_emits_edit_signal(self) -> None:
        canvas = CanvasData(name="主画布")
        source = canvas.add_node(Node(title="源", x=0, y=0, width=320, height=180))
        target = canvas.add_node(Node(title="目标", x=560, y=80, width=320, height=180))
        edge = canvas.add_edge(source.id, target.id)
        view = NodeGraphView(canvas)
        emitted: list[str] = []
        view.edgeEditRequested.connect(lambda edge_id: emitted.append(edge_id))
        item = view.edge_items[edge.id]
        view_pos = view.mapFromScene(item.path().pointAtPercent(0.5))

        def fake_exec(menu: QMenu, _global_pos: QPoint):
            for action in _iter_menu_actions(menu):
                if action.text() == "添加连线文本":
                    return action
            return None

        with mock.patch.object(view, "_exec_context_menu", side_effect=fake_exec):
            view._show_context_menu(view_pos, QPoint(20, 20))

        self.assertEqual(emitted, [edge.id])
        self.assertEqual(view.selected_edge_id, edge.id)
        view.deleteLater()

    def test_edge_label_rect_sits_just_above_line_and_is_clickable(self) -> None:
        canvas = CanvasData(name="主画布")
        source = canvas.add_node(Node(title="源", x=0, y=0, width=320, height=180))
        target = canvas.add_node(Node(title="目标", x=560, y=0, width=320, height=180))
        edge = canvas.add_edge(source.id, target.id)
        edge.style = "straight"
        edge.label = "解锁"
        view = NodeGraphView(canvas)
        item = view.edge_items[edge.id]
        middle = item.path().pointAtPercent(0.5)
        label_rect = item._label_rect()

        self.assertTrue(label_rect.isValid())
        self.assertLess(label_rect.bottom(), middle.y())
        self.assertLess(middle.y() - label_rect.bottom(), 8.0)
        self.assertTrue(item.shape().contains(label_rect.center()))
        view.deleteLater()

    def test_curve_edge_enters_target_instead_of_running_parallel(self) -> None:
        canvas = CanvasData(name="主画布")
        source = canvas.add_node(Node(title="源", x=0, y=0, width=320, height=180))
        target = canvas.add_node(Node(title="目标", x=560, y=240, width=320, height=180))
        edge = canvas.add_edge(source.id, target.id)
        edge.style = "curve"
        view = NodeGraphView(canvas)
        item = view.edge_items[edge.id]
        path = item.path()
        end = path.pointAtPercent(1)
        before = item._point_before_end(path)
        tangent = end - before
        into_target = view.node_items[target.id].sceneBoundingRect().center() - end
        dot = tangent.x() * into_target.x() + tangent.y() * into_target.y()

        self.assertGreater(dot, 0)
        view.deleteLater()

    def test_zoomed_out_curve_edge_uses_diagonal_corner_anchors(self) -> None:
        canvas = CanvasData(name="主画布")
        source = canvas.add_node(Node(title="源", x=0, y=0, width=320, height=180))
        target = canvas.add_node(Node(title="目标", x=560, y=240, width=320, height=180))
        edge = canvas.add_edge(source.id, target.id)
        edge.style = "curve"
        view = NodeGraphView(canvas)
        item = view.edge_items[edge.id]

        view.scale(0.5, 0.5)
        view._refresh_edge_paths()
        path = item.path()
        source_rect = view.node_items[source.id].sceneBoundingRect()
        target_rect = view.node_items[target.id].sceneBoundingRect()

        self.assertEqual(path.pointAtPercent(0), source_rect.bottomRight())
        self.assertEqual(path.pointAtPercent(1), target_rect.topLeft())
        view.deleteLater()

    def test_zoomed_out_edge_width_is_thicker_on_screen(self) -> None:
        canvas = CanvasData(name="主画布")
        source = canvas.add_node(Node(title="源", x=0, y=0, width=320, height=180))
        target = canvas.add_node(Node(title="目标", x=560, y=240, width=320, height=180))
        edge = canvas.add_edge(source.id, target.id)
        view = NodeGraphView(canvas)
        item = view.edge_items[edge.id]

        self.assertGreater(item._edge_screen_width(False, zoom=0.45), item._edge_screen_width(False, zoom=1.0))
        self.assertGreater(item._edge_screen_width(True, zoom=0.45), item._edge_screen_width(True, zoom=1.0))
        view.deleteLater()

    def test_zoomed_out_connection_preview_starts_from_diagonal_corner(self) -> None:
        canvas = CanvasData(name="主画布")
        source = canvas.add_node(Node(title="源", x=0, y=0, width=320, height=180))
        view = NodeGraphView(canvas)
        view.scale(0.5, 0.5)
        source_rect = view.node_items[source.id].sceneBoundingRect()
        start = view._connection_start(source_rect, QPointF(720, 420))

        self.assertEqual(start, source_rect.bottomRight())
        view.deleteLater()

    def test_resizing_visual_node_scales_inner_fields(self) -> None:
        canvas = CanvasData(name="主画布")
        field = NodeField("说明", "文本", "内容", x=20, y=10, width=120, height=40, font_size=12)
        node = canvas.add_node(Node(title="视觉节点", width=400, height=252, fields=[field]))
        view = NodeGraphView(canvas)
        item = view.node_items[node.id]
        handle_pos = QPointF(item.width - 1, item.height - 1)
        press_scene_pos = item.mapToScene(handle_pos)

        item.mousePressEvent(_ScenePointerEvent(handle_pos, press_scene_pos))
        item.mouseMoveEvent(_ScenePointerEvent(handle_pos, press_scene_pos + QPointF(100, 50)))

        self.assertEqual(node.width, 500)
        self.assertEqual(node.height, 302)
        self.assertAlmostEqual(field.x, 25.0)
        self.assertAlmostEqual(field.y, 12.5)
        self.assertAlmostEqual(field.width, 150.0)
        self.assertAlmostEqual(field.height, 50.0)
        self.assertEqual(field.font_size, 15)
        view.deleteLater()

    def test_inline_edit_visual_field_updates_value_and_emits_change(self) -> None:
        canvas = CanvasData(name="主画布")
        field = NodeField("数值", "文本", "5", x=20, y=10, width=120, height=40)
        node = canvas.add_node(Node(title="视觉节点", width=320, height=180, fields=[field]))
        view = NodeGraphView(canvas)
        item = view.node_items[node.id]
        changed: list[bool] = []
        view.projectChanged.connect(lambda: changed.append(True))

        view.start_inline_field_edit(item, field, item._editable_field_rects()[0][1])
        self.assertTrue(view.is_inline_field_editing())
        self.assertIsNotNone(view._inline_editor)
        view._inline_editor.setPlainText("9")
        view._close_inline_field_editor(commit=True)

        self.assertEqual(field.value, "9")
        self.assertEqual(changed, [True])
        view.deleteLater()

    def test_inline_edit_cancel_keeps_original_value(self) -> None:
        canvas = CanvasData(name="主画布")
        field = NodeField("数值", "文本", "5", x=20, y=10, width=120, height=40)
        node = canvas.add_node(Node(title="视觉节点", width=320, height=180, fields=[field]))
        view = NodeGraphView(canvas)
        item = view.node_items[node.id]
        changed: list[bool] = []
        view.projectChanged.connect(lambda: changed.append(True))

        view.start_inline_field_edit(item, field, item._editable_field_rects()[0][1])
        self.assertIsNotNone(view._inline_editor)
        view._inline_editor.setPlainText("9")
        view._close_inline_field_editor(commit=False)

        self.assertEqual(field.value, "5")
        self.assertEqual(changed, [])
        view.deleteLater()

    def test_wheel_over_inline_field_editor_scrolls_editor_instead_of_zooming_canvas(self) -> None:
        canvas = CanvasData(name="主画布")
        field = NodeField("说明", "长文本", "\n".join(f"第 {index} 行" for index in range(40)), x=20, y=10, width=260, height=86)
        node = canvas.add_node(Node(title="视觉节点", width=360, height=220, fields=[field]))
        view = NodeGraphView(canvas)
        view.resize(720, 520)
        view.show()
        self.app.processEvents()
        item = view.node_items[node.id]

        view.start_inline_field_edit(item, field, item._editable_field_rects()[0][1])
        self.assertIsNotNone(view._inline_editor)
        self.assertIsNotNone(view._inline_proxy)
        editor = view._inline_editor
        scrollbar = editor.verticalScrollBar()
        scrollbar.setRange(0, 100)
        scrollbar.setSingleStep(10)
        before_zoom = view.transform().m11()
        view_pos = view.mapFromScene(view._inline_proxy.sceneBoundingRect().center())

        wheel = _ViewWheelEvent(view_pos, -120)
        view.wheelEvent(wheel)

        self.assertTrue(wheel.accepted)
        self.assertTrue(view.is_inline_field_editing())
        self.assertEqual(view.transform().m11(), before_zoom)
        self.assertGreater(scrollbar.value(), 0)
        self.assertIn("width: 18px", editor.styleSheet())
        view.deleteLater()

    def test_inline_node_field_editor_enter_commits_shift_enter_adds_newline(self) -> None:
        editor = InlineNodeFieldEditor()
        commits: list[bool] = []
        editor.editingFinished.connect(commits.append)
        editor.setPlainText("A")
        editor.moveCursor(QTextCursor.End)

        shift_enter = QKeyEvent(QEvent.Type.KeyPress, Qt.Key_Return, Qt.ShiftModifier)
        editor.keyPressEvent(shift_enter)

        self.assertEqual(editor.toPlainText(), "A\n")
        self.assertEqual(commits, [])

        enter = QKeyEvent(QEvent.Type.KeyPress, Qt.Key_Return, Qt.NoModifier)
        editor.keyPressEvent(enter)

        self.assertTrue(enter.isAccepted())
        self.assertEqual(commits, [True])
        editor.deleteLater()

    def test_inline_edit_node_title_updates_title_and_emits_change(self) -> None:
        canvas = CanvasData(name="主画布")
        node = canvas.add_node(Node(title="旧名称", icon="旧", width=320, height=180))
        view = NodeGraphView(canvas)
        item = view.node_items[node.id]
        changed: list[bool] = []
        view.projectChanged.connect(lambda: changed.append(True))
        title_rect = next(rect for part, rect in item._editable_node_text_rects() if part == "title")

        view.start_inline_node_text_edit(item, "title", title_rect)
        self.assertTrue(view.is_inline_field_editing())
        self.assertIsNotNone(view._inline_editor)
        view._inline_editor.setPlainText("新名称")
        view._close_inline_field_editor(commit=True)

        self.assertEqual(node.title, "新名称")
        self.assertEqual(changed, [True])
        view.deleteLater()

    def test_inline_edit_node_icon_updates_icon_and_emits_change(self) -> None:
        canvas = CanvasData(name="主画布")
        node = canvas.add_node(Node(title="节点", icon="旧", icon_from_title=True, width=320, height=180))
        view = NodeGraphView(canvas)
        item = view.node_items[node.id]
        changed: list[bool] = []
        view.projectChanged.connect(lambda: changed.append(True))
        icon_rect = next(rect for part, rect in item._editable_node_text_rects() if part == "icon")

        view.start_inline_node_text_edit(item, "icon", icon_rect)
        self.assertIsNotNone(view._inline_editor)
        view._inline_editor.setPlainText("新")
        view._close_inline_field_editor(commit=True)

        self.assertEqual(node.icon, "新")
        self.assertFalse(node.icon_from_title)
        self.assertEqual(changed, [True])
        view.deleteLater()

    def test_left_click_blank_canvas_commits_inline_edit(self) -> None:
        canvas = CanvasData(name="主画布")
        field = NodeField("数值", "文本", "5", x=20, y=10, width=120, height=40)
        node = canvas.add_node(Node(title="视觉节点", width=320, height=180, fields=[field]))
        view = NodeGraphView(canvas)
        item = view.node_items[node.id]
        changed: list[bool] = []
        view.projectChanged.connect(lambda: changed.append(True))
        view.start_inline_field_edit(item, field, item._editable_field_rects()[0][1])
        self.assertIsNotNone(view._inline_editor)
        view._inline_editor.setPlainText("9")

        press = _ViewMouseEvent(Qt.LeftButton, QPoint(760, 520))
        view.mousePressEvent(press)

        self.assertTrue(press.accepted)
        self.assertFalse(view.is_inline_field_editing())
        self.assertEqual(field.value, "9")
        self.assertEqual(changed, [True])
        view.deleteLater()

    def test_data_canvas_nodes_do_not_offer_connection_handles(self) -> None:
        canvas = CanvasData(name="数据", canvas_type="data")
        node = canvas.add_node(Node(title="数据节点", width=320, height=180))
        view = NodeGraphView(canvas)
        item = view.node_items[node.id]
        handle_pos = item._connection_handle_points()["right"]

        self.assertFalse(view.can_create_edges())
        self.assertIsNone(item._connection_handle_at(handle_pos))
        view.deleteLater()

    def test_data_canvas_without_visual_template_allows_node_move_but_not_resize(self) -> None:
        canvas = CanvasData(name="数据", canvas_type="data")
        canvas.add_node(Node(title="条目"))
        view = NodeGraphView(canvas)

        self.assertTrue(view.can_move_nodes())
        self.assertFalse(view.can_resize_nodes())
        view.deleteLater()

    def test_data_canvas_resize_scales_template_and_all_nodes(self) -> None:
        field = NodeField("数值", "文本", "1", x=20, y=18, width=120, height=44, font_size=12)
        template = NodeTemplate(name="数据模板", fields=[field])
        canvas = CanvasData(name="数据", canvas_type="data", data_layout="grid", template_id=template.id)
        first = template.create_node(0, 0)
        first.fields[0].value = "10"
        second = template.create_node(0, 0)
        second.fields[0].value = "20"
        canvas.add_node(first)
        canvas.add_node(second)
        view = NodeGraphView(canvas, templates=[template])
        item = view.node_items[first.id]
        handle_pos = QPointF(item.width - 1, item.height - 1)
        press_scene_pos = item.mapToScene(handle_pos)

        self.assertTrue(view.can_resize_nodes())
        item.mousePressEvent(_ScenePointerEvent(handle_pos, press_scene_pos))
        item.mouseMoveEvent(_ScenePointerEvent(handle_pos, press_scene_pos + QPointF(120, 72)))
        changed = view.resize_data_canvas_template(first.id, item.width, item.height)

        self.assertTrue(changed)
        self.assertGreater(template.fields[0].width, 120)
        self.assertGreater(template.fields[0].height, 44)
        self.assertGreater(template.fields[0].font_size, 12)
        self.assertEqual(first.fields[0].value, "10")
        self.assertEqual(second.fields[0].value, "20")
        self.assertEqual(first.fields[0].width, template.fields[0].width)
        self.assertEqual(second.fields[0].width, template.fields[0].width)
        self.assertEqual(first.width, second.width)
        self.assertEqual(view.node_items[second.id].width, view.node_items[first.id].width)
        view.deleteLater()

    def test_data_canvas_drag_reorders_nodes(self) -> None:
        canvas = CanvasData(name="数据", canvas_type="data", data_layout="horizontal")
        first = canvas.add_node(Node(title="第一", width=320, height=180))
        second = canvas.add_node(Node(title="第二", width=320, height=180))
        third = canvas.add_node(Node(title="第三", width=320, height=180))
        layout_data_canvas(canvas)

        view = NodeGraphView(canvas)
        second_item = view.node_items[second.id]
        first_item = view.node_items[first.id]

        second_item.setPos(QPointF(second_item.pos().x(), first_item.pos().y() - 48))
        changed = view.commit_data_canvas_node_reorder(second.id)

        self.assertTrue(changed)
        self.assertEqual(
            [node.title for node in sorted(canvas.nodes, key=lambda node: node.order)],
            ["第二", "第一", "第三"],
        )
        self.assertLess(canvas.find_node(second.id).y, canvas.find_node(first.id).y)
        view.deleteLater()

    def test_horizontal_data_canvas_node_uses_single_row_layout(self) -> None:
        horizontal_canvas = CanvasData(name="数据", canvas_type="data", data_layout="horizontal")
        horizontal_node = horizontal_canvas.add_node(
            Node(
                title="横向",
                fields=[
                    NodeField(name="姓名", value="小明"),
                    NodeField(name="职业", value="策划"),
                    NodeField(name="城市", value="上海"),
                ],
            )
        )
        horizontal_view = NodeGraphView(horizontal_canvas)
        horizontal_item = horizontal_view.node_items[horizontal_node.id]

        vertical_canvas = CanvasData(name="数据", canvas_type="data", data_layout="grid")
        vertical_node = vertical_canvas.add_node(
            Node(
                title="纵向",
                fields=[
                    NodeField(name="姓名", value="小明"),
                    NodeField(name="职业", value="策划"),
                    NodeField(name="城市", value="上海"),
                ],
            )
        )
        vertical_view = NodeGraphView(vertical_canvas)
        vertical_item = vertical_view.node_items[vertical_node.id]

        self.assertGreater(horizontal_item.width, vertical_item.width)
        self.assertLess(horizontal_item.height, vertical_item.height)
        self.assertEqual(horizontal_node.width, horizontal_item.width)
        self.assertEqual(horizontal_node.height, horizontal_item.height)
        self.assertEqual(horizontal_node.y, 72.0)

        horizontal_view.deleteLater()
        vertical_view.deleteLater()

    def test_grid_card_rows_do_not_reserve_type_column(self) -> None:
        canvas = CanvasData(name="数据", canvas_type="data", data_layout="grid")
        node = canvas.add_node(
            Node(
                title="角色",
                fields=[
                    NodeField(name="最大生命值", data_type="文本", value="100"),
                    NodeField(name="移动速度", data_type="数字", value="4"),
                ],
            )
        )
        view = NodeGraphView(canvas)
        item = view.node_items[node.id]
        _name_w, type_w = item._row_column_widths()

        self.assertEqual(type_w, 0.0)
        self.assertGreater(item.width, 0)
        view.deleteLater()

    def test_horizontal_data_canvas_node_width_fits_many_fields(self) -> None:
        canvas = CanvasData(name="数据", canvas_type="data", data_layout="horizontal")
        node = canvas.add_node(
            Node(
                title="很多字段",
                fields=[
                    NodeField(name=f"field{i}", value=str(i))
                    for i in range(24)
                ],
            )
        )
        view = NodeGraphView(canvas)
        item = view.node_items[node.id]
        expected_width = sum(segment_w for _field, _label_w, segment_w in item._horizontal_data_segments())
        expected_width += 23 * 7.0 + 20.0

        self.assertGreater(item.width, 2400.0)
        self.assertGreaterEqual(item.width, expected_width)
        self.assertEqual(node.width, item.width)
        view.deleteLater()

    def test_horizontal_thumbnail_data_canvas_uses_table_like_rows(self) -> None:
        canvas = CanvasData(name="数据", canvas_type="data", data_layout="horizontal", data_row_style="thumbnail")
        node = canvas.add_node(
            Node(
                title="缩略",
                fields=[
                    NodeField(name="姓名", data_type="文本", value="小明"),
                    NodeField(name="数值", data_type="整数", value="12"),
                ],
            )
        )

        view = NodeGraphView(canvas)
        item = view.node_items[node.id]

        self.assertIsNotNone(view.data_header_item)
        self.assertEqual(item.height, 34.0)
        self.assertEqual(node.y, 136.0)
        self.assertEqual(len(view.horizontal_thumbnail_columns()), 2)
        view.deleteLater()

    def test_visual_field_show_label_paints_field_name_and_value(self) -> None:
        canvas = CanvasData(name="主画布")
        node = canvas.add_node(
            Node(
                title="显示字段",
                fields=[
                    NodeField(
                        name="moveSpeed",
                        data_type="文本",
                        value="4",
                        x=20,
                        y=18,
                        width=220,
                        height=58,
                        show_label=True,
                    )
                ],
            )
        )
        view = NodeGraphView(canvas)
        item = view.node_items[node.id]
        image = QImage(int(item.width), int(item.height), QImage.Format_ARGB32_Premultiplied)
        image.fill(Qt.transparent)
        painter = QPainter(image)
        item.paint(painter, None)
        painter.end()

        self.assertTrue(node.fields[0].show_label)
        self.assertGreater(image.pixelColor(36, 84).alpha(), 0)
        view.deleteLater()

    def test_visual_node_default_size_matches_editor_preview_size(self) -> None:
        canvas = CanvasData(name="主画布")
        node = canvas.add_node(
            Node(
                title="紧凑布局",
                fields=[
                    NodeField(
                        name="名称",
                        value="攻击模块",
                        x=1,
                        y=0,
                        width=185,
                        height=40,
                    )
                ],
            )
        )

        view = NodeGraphView(canvas)
        item = view.node_items[node.id]

        self.assertEqual(item.width, 430.0)
        self.assertEqual(item.height, 300.0)
        view.deleteLater()

    def test_visual_fields_use_exact_editor_coordinates_on_canvas(self) -> None:
        canvas = CanvasData(name="主画布")
        node = canvas.add_node(
            Node(
                title="紧凑布局",
                width=430,
                height=300,
                fields=[
                    NodeField(
                        name="名称",
                        value="",
                        x=1,
                        y=0,
                        width=185,
                        height=40,
                        bg_color="#FF0000",
                    )
                ],
            )
        )
        view = NodeGraphView(canvas)
        item = view.node_items[node.id]
        image = QImage(int(item.width), int(item.height), QImage.Format_ARGB32_Premultiplied)
        image.fill(Qt.transparent)
        painter = QPainter(image)
        item.paint(painter, None)
        painter.end()

        raw_left_pixel = image.pixelColor(8, 72)
        self.assertGreater(raw_left_pixel.red(), 220)
        self.assertLess(raw_left_pixel.green(), 80)
        self.assertLess(raw_left_pixel.blue(), 80)
        self.assertGreater(raw_left_pixel.alpha(), 200)
        view.deleteLater()

    def test_blueprint_group_snaps_to_grid(self) -> None:
        canvas = CanvasData(name="主画布")
        group = canvas.add_group(BlueprintGroup(title="蓝图组", x=83, y=118, width=320, height=220))
        view = NodeGraphView(canvas)
        item = view.group_items[group.id]

        snapped = view.snap_position(item, QPointF(83, 118))

        self.assertEqual(snapped, QPointF(80, 120))
        view.deleteLater()

    def test_blueprint_group_snaps_to_node_alignment(self) -> None:
        canvas = CanvasData(name="主画布")
        canvas.add_node(Node(title="节点", x=103, y=100))
        group = canvas.add_group(BlueprintGroup(title="蓝图组", x=280, y=220, width=320, height=220))
        view = NodeGraphView(canvas)
        item = view.group_items[group.id]

        snapped = view.snap_position(item, QPointF(108, 153))

        self.assertEqual(snapped.x(), 103)
        self.assertTrue(any(guide.kind == "align" and guide.axis == "x" for guide in view.snap_guides))
        view.deleteLater()

    def test_right_click_without_drag_opens_context_menu(self) -> None:
        view = NodeGraphView(ProjectData(nodes=[Node(title="Current")]))
        view.resize(800, 600)

        with mock.patch.object(view, "_show_context_menu") as show_context_menu:
            press = _ViewMouseEvent(Qt.RightButton, QPoint(120, 140))
            release = _ViewMouseEvent(Qt.RightButton, QPoint(123, 142))

            view.mousePressEvent(press)
            view.mouseReleaseEvent(release)

        show_context_menu.assert_called_once_with(QPoint(123, 142), QPoint(123, 142))
        self.assertTrue(release.accepted)
        view.deleteLater()

    def test_right_drag_pans_without_opening_context_menu(self) -> None:
        view = NodeGraphView(ProjectData(nodes=[Node(title="Current")]))
        view.resize(800, 600)
        view.show()
        self.app.processEvents()

        start_horizontal = view.horizontalScrollBar().value()
        start_vertical = view.verticalScrollBar().value()

        with mock.patch.object(view, "_show_context_menu") as show_context_menu:
            view.mousePressEvent(_ViewMouseEvent(Qt.RightButton, QPoint(220, 200)))
            view.mouseMoveEvent(_ViewMouseEvent(Qt.NoButton, QPoint(244, 228)))
            self.assertTrue(view._panning)
            view.mouseMoveEvent(_ViewMouseEvent(Qt.NoButton, QPoint(280, 260)))
            view.mouseReleaseEvent(_ViewMouseEvent(Qt.RightButton, QPoint(280, 260)))

        show_context_menu.assert_not_called()
        self.assertNotEqual(view.horizontalScrollBar().value(), start_horizontal)
        self.assertNotEqual(view.verticalScrollBar().value(), start_vertical)
        self.assertFalse(view._panning)
        view.deleteLater()

    def test_first_right_press_clears_stale_hand_cursor_before_drag(self) -> None:
        view = NodeGraphView(ProjectData(nodes=[Node(title="Current")]))
        view.resize(800, 600)
        view.setCursor(Qt.OpenHandCursor)
        view.viewport().setCursor(Qt.OpenHandCursor)
        view.show()
        self.app.processEvents()

        press = _ViewMouseEvent(Qt.RightButton, QPoint(220, 200))
        view.mousePressEvent(press)

        self.assertTrue(view._right_drag_pending)
        self.assertEqual(view.cursor().shape(), Qt.ArrowCursor)
        self.assertEqual(view.viewport().cursor().shape(), Qt.ArrowCursor)
        view.deleteLater()

    def test_right_drag_release_suppresses_followup_context_menu_event(self) -> None:
        view = NodeGraphView(ProjectData(nodes=[Node(title="Current")]))
        view.resize(800, 600)
        view.show()
        self.app.processEvents()

        with mock.patch.object(view, "_show_context_menu") as show_context_menu:
            view.mousePressEvent(_ViewMouseEvent(Qt.RightButton, QPoint(220, 200)))
            view.mouseMoveEvent(_ViewMouseEvent(Qt.NoButton, QPoint(252, 234)))
            view.mouseReleaseEvent(_ViewMouseEvent(Qt.RightButton, QPoint(252, 234)))

            event = _ViewContextMenuEvent(QPoint(252, 234))
            view.contextMenuEvent(event)

        show_context_menu.assert_not_called()
        self.assertTrue(event.accepted)
        self.assertFalse(view._suppress_context_menu)
        view.deleteLater()

    def test_node_context_menu_ai_iteration_action_emits_signal(self) -> None:
        canvas = CanvasData(name="主画布")
        node = canvas.add_node(Node(title="当前节点", x=0, y=0, width=320, height=180))
        view = NodeGraphView(canvas)
        emitted: list[bool] = []
        view.aiIterateRequested.connect(lambda: emitted.append(True))
        item = view.node_items[node.id]
        scene_pos = item.sceneBoundingRect().center()
        view_pos = view.mapFromScene(scene_pos)

        def fake_exec(menu: QMenu, _global_pos: QPoint):
            for action in _iter_menu_actions(menu):
                if action.text() == "迭代助手":
                    return action
            return None

        with mock.patch.object(view, "_exec_context_menu", side_effect=fake_exec):
            view._show_context_menu(view_pos, QPoint(20, 20))

        self.assertEqual(emitted, [True])
        self.assertEqual(view.selected_node_ids, {node.id})
        view.deleteLater()

    def test_node_context_menu_notes_action_emits_signal(self) -> None:
        canvas = CanvasData(name="主画布")
        node = canvas.add_node(Node(title="当前节点", x=0, y=0, width=320, height=180))
        view = NodeGraphView(canvas)
        emitted: list[str] = []
        view.nodeNotesRequested.connect(lambda node_id: emitted.append(node_id))
        item = view.node_items[node.id]
        scene_pos = item.sceneBoundingRect().center()
        view_pos = view.mapFromScene(scene_pos)

        def fake_exec(menu: QMenu, _global_pos: QPoint):
            for action in _iter_menu_actions(menu):
                if action.text() == "便签...":
                    return action
            return None

        with mock.patch.object(view, "_exec_context_menu", side_effect=fake_exec):
            view._show_context_menu(view_pos, QPoint(20, 20))

        self.assertEqual(emitted, [node.id])
        self.assertEqual(view.selected_node_ids, {node.id})
        view.deleteLater()

    def test_node_context_menu_create_note_emits_canvas_note_request(self) -> None:
        canvas = CanvasData(name="主画布")
        node = canvas.add_node(Node(title="当前节点", x=0, y=0, width=320, height=180))
        view = NodeGraphView(canvas)
        emitted: list[tuple[float, float, object]] = []
        view.createNoteRequested.connect(lambda x, y, owner_id: emitted.append((x, y, owner_id)))
        item = view.node_items[node.id]
        scene_pos = item.sceneBoundingRect().center()
        view_pos = view.mapFromScene(scene_pos)

        def fake_exec(menu: QMenu, _global_pos: QPoint):
            for action in _iter_menu_actions(menu):
                if action.text() == "创建便签":
                    return action
            return None

        with mock.patch.object(view, "_exec_context_menu", side_effect=fake_exec):
            view._show_context_menu(view_pos, QPoint(20, 20))

        self.assertEqual(len(emitted), 1)
        self.assertEqual(emitted[0][2], node.id)
        view.deleteLater()

    def test_dragging_note_onto_node_moves_note_to_node(self) -> None:
        canvas = CanvasData(name="主画布")
        source = canvas.add_node(Node(title="源", x=0, y=0, width=320, height=180))
        target = canvas.add_node(Node(title="目标", x=500, y=0, width=320, height=180))
        note = DesignNote(title="参考", content="前期偏高")
        canvas.notes.append(note)
        view = NodeGraphView(canvas)
        source_item = view.note_items[("", note.id)]
        target_item = view.node_items[target.id]
        view.begin_connection_drag(note.id, source_item.mapToScene(source_item._connection_handle_points()["right"]))
        release_scene_pos = target_item.sceneBoundingRect().center()

        source_item.mouseReleaseEvent(_ScenePointerEvent(source_item.mapFromScene(release_scene_pos), release_scene_pos))

        self.assertEqual(canvas.notes, [])
        self.assertEqual(len(target.notes), 1)
        self.assertEqual(target.notes[0].id, note.id)
        self.assertEqual(view.selected_note_key, (target.id, note.id))
        view.deleteLater()

    def test_node_note_visibility_tracks_selected_node(self) -> None:
        canvas = CanvasData(name="主画布")
        node = canvas.add_node(Node(title="当前节点", x=0, y=0, width=320, height=180))
        note = DesignNote(title="节点便签", content="选中后显示")
        node.notes.append(note)
        view = NodeGraphView(canvas)

        item = view.note_items[(node.id, note.id)]
        self.assertFalse(item.isVisible())

        view.select_node(node.id)

        self.assertTrue(item.isVisible())
        view.deleteLater()

    def test_group_move_moves_attached_notes_once(self) -> None:
        canvas = CanvasData(name="主画布")
        group = canvas.add_group(BlueprintGroup(title="流程组", x=0, y=0, width=500, height=260))
        node = canvas.add_node(Node(title="组内节点", x=40, y=50, group_id=group.id))
        note = DesignNote(title="节点便签", content="跟随节点")
        note.x = 80
        note.y = 90
        node.notes.append(note)
        view = NodeGraphView(canvas)
        note_item = view.note_items[(node.id, note.id)]

        view.move_nodes_in_group(group.id, QPointF(20, 30))

        self.assertEqual((node.x, node.y), (60, 80))
        self.assertEqual((note.x, note.y), (100, 120))
        self.assertEqual((note_item.pos().x(), note_item.pos().y()), (100, 120))
        view.deleteLater()

    def test_group_context_menu_ai_iteration_action_emits_signal(self) -> None:
        canvas = CanvasData(name="主画布")
        group = canvas.add_group(BlueprintGroup(title="参考组", x=0, y=0, width=420, height=260))
        view = NodeGraphView(canvas)
        emitted: list[bool] = []
        view.aiIterateRequested.connect(lambda: emitted.append(True))
        item = view.group_items[group.id]
        scene_pos = item.sceneBoundingRect().center()
        view_pos = view.mapFromScene(scene_pos)

        def fake_exec(menu: QMenu, _global_pos: QPoint):
            for action in _iter_menu_actions(menu):
                if action.text() == "迭代助手":
                    return action
            return None

        with mock.patch.object(view, "_exec_context_menu", side_effect=fake_exec):
            view._show_context_menu(view_pos, QPoint(20, 20))

        self.assertEqual(emitted, [True])
        self.assertEqual(view.selected_group_ids, {group.id})
        view.deleteLater()

    def test_source_image_cache_reuses_loaded_pixmap(self) -> None:
        view = NodeGraphView(ProjectData(nodes=[Node(title="Current")]))
        pixmap = QPixmap(64, 48)
        pixmap.fill(Qt.white)
        calls: list[str] = []

        def load(path: str) -> QPixmap | None:
            calls.append(path)
            return pixmap

        view._load_source_pixmap = load  # type: ignore[method-assign]

        first = view._source_image_pixmap("D:/assets/hero.png")
        second = view._source_image_pixmap("D:/assets/hero.png")

        self.assertEqual(calls, ["D:/assets/hero.png"])
        self.assertIs(first, second)
        view.deleteLater()

    def test_scaled_image_cache_reuses_same_target_size(self) -> None:
        view = NodeGraphView(ProjectData(nodes=[Node(title="Current")]))
        pixmap = QPixmap(96, 64)
        pixmap.fill(Qt.white)

        with mock.patch.object(view, "_load_source_pixmap", return_value=pixmap) as load_source:
            first = view._scaled_image_pixmap("D:/assets/hero.png", 220, 160)
            second = view._scaled_image_pixmap("D:/assets/hero.png", 220, 160)

        self.assertEqual(load_source.call_count, 1)
        self.assertIs(first, second)
        view.deleteLater()


if __name__ == "__main__":
    unittest.main()
