import os
import unittest
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QPoint, QPointF, Qt
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import QApplication

from gamedesigner.data_canvas import layout_data_canvas
from gamedesigner.models import BlueprintGroup, CanvasData, Node, NodeField, ProjectData
from gamedesigner.qt_canvas import NodeGraphView


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

    def test_data_canvas_allows_node_move_but_not_resize(self) -> None:
        canvas = CanvasData(name="数据", canvas_type="data")
        canvas.add_node(Node(title="条目"))
        view = NodeGraphView(canvas)

        self.assertTrue(view.can_move_nodes())
        self.assertFalse(view.can_resize_nodes())
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
