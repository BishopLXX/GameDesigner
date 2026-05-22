import unittest

from gamedesigner.models import Node, ProjectData
from gamedesigner.project_history import ProjectHistory


class ProjectHistoryTests(unittest.TestCase):
    def test_clean_project_becomes_dirty_after_record_and_clean_after_undo(self) -> None:
        project = ProjectData(name="历史测试")
        project.ensure_canvas_structure()
        history = ProjectHistory()
        history.initialize(project, canvas_id=project.root_canvas_id, clean=True)

        project.root_canvas().add_node(Node(title="节点A"))
        history.record(project, canvas_id=project.root_canvas_id)

        self.assertTrue(history.is_dirty())
        snapshot = history.undo()
        self.assertIsNotNone(snapshot)
        self.assertFalse(history.is_dirty())

    def test_record_after_undo_clears_redo_branch(self) -> None:
        project = ProjectData(name="分支测试")
        project.ensure_canvas_structure()
        history = ProjectHistory()
        history.initialize(project, canvas_id=project.root_canvas_id, clean=True)

        project.root_canvas().add_node(Node(title="节点A"))
        history.record(project, canvas_id=project.root_canvas_id)
        project.root_canvas().add_node(Node(title="节点B"))
        history.record(project, canvas_id=project.root_canvas_id)

        history.undo()
        project.root_canvas().add_node(Node(title="节点C"))
        history.record(project, canvas_id=project.root_canvas_id)

        self.assertFalse(history.can_redo())

    def test_multi_step_undo_can_redo_forward_back_to_latest(self) -> None:
        project = ProjectData(name="多步前进测试")
        project.ensure_canvas_structure()
        history = ProjectHistory()
        history.initialize(project, canvas_id=project.root_canvas_id, clean=True)

        for title in ("节点A", "节点B", "节点C"):
            project.root_canvas().add_node(Node(title=title))
            history.record(project, canvas_id=project.root_canvas_id)

        snapshot = history.undo()
        self.assertIsNotNone(snapshot)
        self.assertEqual(len(snapshot.project["canvases"][0]["nodes"]), 2)

        snapshot = history.undo()
        self.assertIsNotNone(snapshot)
        self.assertEqual(len(snapshot.project["canvases"][0]["nodes"]), 1)

        snapshot = history.redo()
        self.assertIsNotNone(snapshot)
        self.assertEqual(len(snapshot.project["canvases"][0]["nodes"]), 2)

        snapshot = history.redo()
        self.assertIsNotNone(snapshot)
        self.assertEqual(len(snapshot.project["canvases"][0]["nodes"]), 3)


if __name__ == "__main__":
    unittest.main()
