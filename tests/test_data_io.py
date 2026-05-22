import tempfile
import unittest
from pathlib import Path

from gamedesigner.csv_io import export_project_csv, import_project_csv
from gamedesigner.models import Node, NodeField, ProjectData
from gamedesigner.storage import load_project, save_project


class DataIoTests(unittest.TestCase):
    def test_project_json_roundtrip(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            tmp_path = Path(folder)
            project = ProjectData(
                name="测试项目",
                source_dir=str(tmp_path / "source"),
                output_dir=str(tmp_path / "out"),
            )
            first = Node(
                title="A",
                x=10,
                y=20,
                width=360,
                height=220,
                fields=[NodeField("内容信息", "文本", "起点")],
            )
            second = Node(title="B", x=100, y=120, fields=[NodeField("数值", "数字", "42")])
            project.nodes = [first, second]
            edge = project.add_edge(first.id, second.id)
            edge.style = "orthogonal"

            path = tmp_path / "project.gdesigner.json"
            save_project(project, path)
            loaded = load_project(path)

            self.assertEqual(loaded.name, "测试项目")
            self.assertEqual(loaded.source_dir, str(tmp_path / "source"))
            self.assertEqual(loaded.output_dir, str(tmp_path / "out"))
            self.assertEqual([node.title for node in loaded.nodes], ["A", "B"])
            self.assertEqual(loaded.nodes[0].width, 360)
            self.assertEqual(loaded.nodes[0].height, 220)
            self.assertEqual(loaded.edges[0].source, first.id)
            self.assertEqual(loaded.edges[0].target, second.id)
            self.assertEqual(loaded.edges[0].style, "orthogonal")

    def test_csv_roundtrip(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            tmp_path = Path(folder)
            project = ProjectData(name="CSV测试")
            first = Node(title="科技入口", width=330, height=180, fields=[NodeField("数据类型", "枚举", "科技树")])
            second = Node(title="天赋节点", fields=[NodeField("消耗", "整数", "3")])
            project.nodes = [first, second]
            edge = project.add_edge(first.id, second.id)
            edge.style = "straight"

            export_project_csv(project, tmp_path)
            loaded = import_project_csv(tmp_path)

            self.assertEqual([node.title for node in loaded.nodes], ["科技入口", "天赋节点"])
            self.assertEqual(loaded.nodes[0].width, 330)
            self.assertEqual(loaded.nodes[0].height, 180)
            self.assertEqual(loaded.nodes[0].fields[0].value, "科技树")
            self.assertEqual(len(loaded.edges), 1)
            self.assertEqual(loaded.edges[0].style, "straight")


if __name__ == "__main__":
    unittest.main()
