import csv
import tempfile
import unittest
from pathlib import Path

from gamedesigner.csv_io import export_game_csv
from gamedesigner.models import Node, NodeField, ProjectData
from gamedesigner.storage import load_project, save_project


class DataIoTests(unittest.TestCase):
    def test_project_gdc_roundtrip(self) -> None:
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
                fields=[
                    NodeField("内容信息", "文本", "起点"),
                    NodeField(
                        "立绘",
                        "图片",
                        "",
                        image_path=str(tmp_path / "hero.png"),
                        export_props=["x", "width"],
                    ),
                ],
            )
            second = Node(title="B", x=100, y=120, fields=[NodeField("数值", "数字", "42")])
            project.nodes = [first, second]
            edge = project.add_edge(first.id, second.id)
            edge.style = "orthogonal"

            path = tmp_path / "project.gdc"
            save_project(project, path)
            loaded = load_project(path)

            self.assertEqual(loaded.name, "测试项目")
            self.assertEqual(loaded.source_dir, str(tmp_path / "source"))
            self.assertEqual(loaded.output_dir, str(tmp_path / "out"))
            self.assertEqual([node.title for node in loaded.nodes], ["A", "B"])
            self.assertEqual(loaded.nodes[0].width, 360)
            self.assertEqual(loaded.nodes[0].height, 220)
            self.assertEqual(loaded.nodes[0].fields[1].data_type, "图片")
            self.assertEqual(loaded.nodes[0].fields[1].image_path, str(tmp_path / "hero.png"))
            self.assertEqual(loaded.nodes[0].fields[1].export_props, ["x", "width"])
            self.assertEqual(loaded.edges[0].source, first.id)
            self.assertEqual(loaded.edges[0].target, second.id)
            self.assertEqual(loaded.edges[0].style, "orthogonal")

    def test_game_csv_export_is_single_flat_table(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            tmp_path = Path(folder)
            project = ProjectData(name="CSV测试")
            first = Node(
                title="科技入口",
                width=330,
                height=180,
                fields=[
                    NodeField("数据类型", "枚举", "科技树", export_props=["x"]),
                    NodeField("立绘", "图片", "", image_path=str(tmp_path / "icon.png")),
                ],
            )
            second = Node(title="天赋节点", fields=[NodeField("消耗", "整数", "3")])
            project.nodes = [first, second]

            output = export_game_csv(project, tmp_path / "game_data.csv")
            with output.open("r", encoding="utf-8-sig", newline="") as file:
                rows = list(csv.reader(file))

            self.assertEqual(rows[0], ["名字", "图标", "数据类型", "立绘", "消耗", "数据类型.X"])
            self.assertEqual(rows[1], ["文本", "文本", "枚举", "图片", "整数", "数字"])
            self.assertEqual(rows[2], ["科技入口", "", "科技树", str(tmp_path / "icon.png"), "", "0"])
            self.assertEqual(rows[3], ["天赋节点", "", "", "", "3", ""])
            self.assertFalse((tmp_path / "nodes.csv").exists())
            self.assertFalse((tmp_path / "edges.csv").exists())
            self.assertFalse((tmp_path / "templates.csv").exists())

    def test_legacy_resource_path_image_field_migrates_to_image_type(self) -> None:
        field = NodeField.from_dict(
            {
                "name": "旧图片",
                "data_type": "资源路径",
                "value": "",
                "image_path": "D:/assets/old.png",
            }
        )

        self.assertEqual(field.data_type, "图片")
        self.assertEqual(field.image_path, "D:/assets/old.png")


if __name__ == "__main__":
    unittest.main()
