import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication

from gamedesigner.app import GameDesignerApp
from gamedesigner.models import BlueprintGroup, Node, NodeField, ProjectData
from gamedesigner.storage import AppSettings, save_settings


class AppEditingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def test_copy_paste_and_undo_redo_selected_node(self) -> None:
        window = GameDesignerApp()
        project = ProjectData(name="复制测试")
        project.ensure_canvas_structure()
        source = project.root_canvas().add_node(
            Node(
                title="原节点",
                x=20,
                y=30,
                width=350,
                height=180,
                fields=[NodeField("内容", "文本", "值")],
            )
        )
        page = window._add_page(project, None, dirty=False, canvas_data=project.root_canvas())
        window.tabs.setCurrentWidget(page)
        page.canvas.select_node(source.id)
        self.app.processEvents()

        window._copy_selected_nodes()
        window._paste_nodes()

        nodes = project.root_canvas().nodes
        self.assertEqual(len(nodes), 2)
        pasted = next(node for node in nodes if node.id != source.id)
        self.assertEqual(pasted.title, source.title)
        self.assertEqual(pasted.x, source.x + 40)
        self.assertEqual(pasted.y, source.y + 40)

        window._undo()
        self.assertEqual(len(project.root_canvas().nodes), 1)

        window._redo()
        self.assertEqual(len(project.root_canvas().nodes), 2)
        window.deleteLater()

    def test_add_node_inside_group_assigns_group_membership(self) -> None:
        window = GameDesignerApp()
        project = ProjectData(name="蓝图创建测试")
        project.ensure_canvas_structure()
        group = project.root_canvas().add_group(BlueprintGroup(title="组", x=0, y=0, width=700, height=420))
        page = window._add_page(project, None, dirty=False, canvas_data=project.root_canvas())
        window.tabs.setCurrentWidget(page)

        window._add_node_at(260, 180)

        created = project.root_canvas().nodes[-1]
        self.assertEqual(created.group_id, group.id)
        window.deleteLater()

    def test_delete_canvas_branch_closes_canvas_tabs(self) -> None:
        window = GameDesignerApp()
        project = ProjectData(name="删子画布测试")
        project.ensure_canvas_structure()
        root = project.root_canvas()
        root_link = root.add_node(Node(title="子画布", node_type="画布"))
        child = project.add_canvas("子画布", parent_canvas_id=root.id, parent_node_id=root_link.id)
        root_link.canvas_id = child.id
        child_link = child.add_node(Node(title="孙画布", node_type="画布"))
        grandchild = project.add_canvas("孙画布", parent_canvas_id=child.id, parent_node_id=child_link.id)
        child_link.canvas_id = grandchild.id

        root_page = window._add_page(project, None, dirty=False, canvas_data=root)
        child_page = window._add_page(project, None, dirty=False, canvas_data=child)
        grandchild_page = window._add_page(project, None, dirty=False, canvas_data=grandchild)

        window._delete_canvas_branch(root_page, child.id)

        self.assertIsNotNone(window.tabs.indexOf(root_page))
        self.assertEqual(window.tabs.indexOf(child_page), -1)
        self.assertEqual(window.tabs.indexOf(grandchild_page), -1)
        self.assertEqual([canvas.id for canvas in project.canvases], [root.id])
        window.deleteLater()

    def test_multi_step_undo_can_redo_to_latest_state(self) -> None:
        window = GameDesignerApp()
        project = ProjectData(name="历史前进测试")
        project.ensure_canvas_structure()
        page = window._add_page(project, None, dirty=False, canvas_data=project.root_canvas())
        window.tabs.setCurrentWidget(page)

        for title in ("节点A", "节点B", "节点C"):
            project.root_canvas().add_node(Node(title=title))
            window._mark_dirty(page)

        window._undo()
        self.assertEqual([node.title for node in project.root_canvas().nodes], ["节点A", "节点B"])
        window._undo()
        self.assertEqual([node.title for node in project.root_canvas().nodes], ["节点A"])

        window._redo()
        self.assertEqual([node.title for node in project.root_canvas().nodes], ["节点A", "节点B"])
        window._redo()
        self.assertEqual([node.title for node in project.root_canvas().nodes], ["节点A", "节点B", "节点C"])
        window.deleteLater()

    def test_delete_confirmation_accepts_enter_by_default(self) -> None:
        window = GameDesignerApp()
        project = ProjectData(name="删除确认测试")
        project.ensure_canvas_structure()
        node = project.root_canvas().add_node(Node(title="节点A"))
        page = window._add_page(project, None, dirty=False, canvas_data=project.root_canvas())
        window.tabs.setCurrentWidget(page)

        with mock.patch("gamedesigner.app.QMessageBox.exec", autospec=True, return_value=0):
            with mock.patch(
                "gamedesigner.app.QMessageBox.clickedButton",
                new=lambda box: box.defaultButton(),
            ):
                self.assertTrue(window._confirm_delete_nodes(page, [node]))
        window.deleteLater()

    def test_sequential_delete_does_not_restore_previously_deleted_nodes(self) -> None:
        window = GameDesignerApp()
        project = ProjectData(name="连续删除测试")
        project.ensure_canvas_structure()
        page = window._add_page(project, None, dirty=False, canvas_data=project.root_canvas())
        window.tabs.setCurrentWidget(page)
        root = project.root_canvas()

        node_a = root.add_node(Node(title="节点A"))
        window._mark_dirty(page)
        node_b = root.add_node(Node(title="节点B"))
        window._mark_dirty(page)

        with mock.patch.object(window, "_confirm_delete_nodes", return_value=True):
            window._delete_node_by_id(node_a.id)
            self.assertEqual([node.title for node in root.nodes], ["节点B"])

            window._delete_node_by_id(node_b.id)
            self.assertEqual(root.nodes, [])
        window.deleteLater()

    def test_welcome_page_layout_persists_recent_node_positions_and_sizes(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            recent_path = Path(folder) / "RecentProject.gdc"
            recent_path.write_text("{}", encoding="utf-8")
            settings = AppSettings(
                workspace_dir=folder,
                export_dir=str(Path(folder) / "exports"),
                last_project=str(recent_path),
                recent_projects=[str(recent_path)],
            )
            with mock.patch.dict(os.environ, {"APPDATA": folder}):
                save_settings(settings)

                window = GameDesignerApp()
                welcome_page = window._current_page()
                self.assertIsNotNone(welcome_page)
                self.assertTrue(welcome_page.is_welcome)

                guide = next(node for node in welcome_page.project.nodes if node.id == "welcome_guide")
                recent = next(node for node in welcome_page.project.nodes if node.id.startswith("welcome_recent_"))
                guide.x = -720
                guide.y = -250
                recent.x = 560
                recent.y = -20
                recent.width = 420
                recent.height = 220

                self.assertTrue(window._save_project(welcome_page))
                window.deleteLater()

                reopened = GameDesignerApp()
                reopened_page = reopened._current_page()
                self.assertIsNotNone(reopened_page)
                self.assertTrue(reopened_page.is_welcome)
                reopened_guide = next(node for node in reopened_page.project.nodes if node.id == "welcome_guide")
                reopened_recent = next(node for node in reopened_page.project.nodes if node.id.startswith("welcome_recent_"))

                self.assertEqual(reopened_guide.x, -720)
                self.assertEqual(reopened_guide.y, -250)
                self.assertEqual(reopened_recent.x, 560)
                self.assertEqual(reopened_recent.y, -20)
                self.assertEqual(reopened_recent.width, 420)
                self.assertEqual(reopened_recent.height, 220)
                reopened.deleteLater()


if __name__ == "__main__":
    unittest.main()
