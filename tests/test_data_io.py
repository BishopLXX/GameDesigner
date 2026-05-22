import csv
import tempfile
import unittest
from pathlib import Path

from gamedesigner.csv_io import export_all_canvas_csv, export_game_csv
from gamedesigner.models import BlueprintGroup, Node, NodeField, ProjectData, default_templates
from gamedesigner.project_files.linked_documents import (
    create_link_document,
    delete_link_document,
    delete_link_document_copy,
    read_link_document,
    rename_link_document,
    resolve_link_document,
    sync_link_document_copy,
    write_link_document,
)
from gamedesigner.storage import load_project, project_bundle_dir, save_project


class DataIoTests(unittest.TestCase):
    def test_project_gdc_roundtrip(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            tmp_path = Path(folder)
            project = ProjectData(
                name="测试项目",
                source_dir=str(tmp_path / "source"),
                output_dir=str(tmp_path / "out"),
                copy_link_docs_to_source=True,
            )
            first = Node(
                title="A",
                x=10,
                y=20,
                width=360,
                height=220,
                fields=[
                    NodeField("内容信息", "文本", "起点", text_h_align="center", text_v_align="bottom"),
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
            group = BlueprintGroup(title="战斗流程", x=0, y=0, width=600, height=240)
            project.nodes = [first, second]
            project.ensure_canvas_structure()
            project.root_canvas().groups.append(group)
            first.group_id = group.id
            edge = project.add_edge(first.id, second.id)
            edge.style = "orthogonal"

            path = tmp_path / "project.gdc"
            save_project(project, path)
            loaded = load_project(path)
            bundle = project_bundle_dir(path)

            self.assertEqual(loaded.name, "测试项目")
            self.assertTrue((bundle / "canvases" / f"{loaded.root_canvas_id}.json").exists())
            self.assertTrue((bundle / "templates.json").exists())
            self.assertEqual(loaded.source_dir, str(tmp_path / "source"))
            self.assertEqual(loaded.output_dir, str(tmp_path / "out"))
            self.assertTrue(loaded.copy_link_docs_to_source)
            self.assertEqual([node.title for node in loaded.nodes], ["A", "B"])
            self.assertEqual([node.order for node in loaded.nodes], [1, 2])
            self.assertEqual(loaded.nodes[0].width, 360)
            self.assertEqual(loaded.nodes[0].height, 220)
            self.assertEqual(loaded.nodes[0].fields[1].data_type, "图片")
            self.assertEqual(loaded.nodes[0].fields[1].image_path, str(tmp_path / "hero.png"))
            self.assertEqual(loaded.nodes[0].fields[1].export_props, ["x", "width"])
            self.assertEqual(loaded.nodes[0].fields[0].text_h_align, "center")
            self.assertEqual(loaded.nodes[0].fields[0].text_v_align, "bottom")
            self.assertEqual(loaded.root_canvas().groups[0].title, "战斗流程")
            self.assertEqual(loaded.nodes[0].group_id, loaded.root_canvas().groups[0].id)
            self.assertEqual(loaded.edges[0].source, first.id)
            self.assertEqual(loaded.edges[0].target, second.id)
            self.assertEqual(loaded.edges[0].style, "orthogonal")

    def test_split_project_manifest_does_not_embed_canvases(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            tmp_path = Path(folder)
            project = ProjectData(name="拆分工程")
            project.ensure_canvas_structure()
            project.root_canvas().add_node(Node(title="主节点"))

            path = tmp_path / "split.gdc"
            save_project(project, path)
            manifest = path.read_text(encoding="utf-8")

            self.assertIn('"mode": "split_bundle"', manifest)
            self.assertIn('"canvas_refs"', manifest)
            self.assertNotIn('"canvases": [', manifest)
            self.assertNotIn('"nodes": [', manifest)
            self.assertEqual(load_project(path).root_canvas().nodes[0].title, "主节点")

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

    def test_game_csv_export_sort_modes(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            tmp_path = Path(folder)
            project = ProjectData(name="排序测试")
            project.nodes = [
                Node(title="创建二", order=2, x=300, y=0),
                Node(title="创建一", order=1, x=0, y=300),
                Node(title="创建三", order=3, x=160, y=160),
            ]

            created = export_game_csv(project, tmp_path / "created.csv", sort_mode="created")
            by_x = export_game_csv(project, tmp_path / "x.csv", sort_mode="x")
            by_y = export_game_csv(project, tmp_path / "y.csv", sort_mode="y")

            self.assertEqual(self._csv_names(created), ["创建一", "创建二", "创建三"])
            self.assertEqual(self._csv_names(by_x), ["创建一", "创建三", "创建二"])
            self.assertEqual(self._csv_names(by_y), ["创建二", "创建三", "创建一"])

    def test_all_canvas_csv_export_writes_one_file_per_canvas(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            tmp_path = Path(folder)
            project = ProjectData(name="多画布CSV")
            project.ensure_canvas_structure()
            root = project.root_canvas()
            root.add_node(Node(title="主入口", fields=[NodeField("阶段", "文本", "开始")]))
            link = root.add_node(Node(title="战斗画布", node_type="画布"))
            child = project.add_canvas("战斗画布", parent_canvas_id=root.id, parent_node_id=link.id)
            link.canvas_id = child.id
            child.add_node(Node(title="敌人B", order=1, x=200, fields=[NodeField("血量", "整数", "100")]))
            child.add_node(Node(title="敌人A", order=2, x=100, fields=[NodeField("血量", "整数", "80")]))

            outputs = export_all_canvas_csv(project, tmp_path, sort_mode="x")
            root_csv = tmp_path / "多画布CSV__主画布.csv"
            child_csv = tmp_path / "多画布CSV__战斗画布.csv"

            self.assertEqual(outputs, [root_csv, child_csv])
            self.assertTrue(root_csv.exists())
            self.assertTrue(child_csv.exists())
            self.assertEqual(self._csv_names(root_csv), ["主入口", "战斗画布"])
            self.assertEqual(self._csv_names(child_csv), ["敌人A", "敌人B"])

    def test_delete_node_compacts_creation_order(self) -> None:
        project = ProjectData(name="删除排序")
        project.ensure_canvas_structure()
        canvas = project.root_canvas()
        first = canvas.add_node(Node(title="一"))
        second = canvas.add_node(Node(title="二"))
        third = canvas.add_node(Node(title="三"))

        self.assertEqual([node.order for node in canvas.nodes], [1, 2, 3])
        canvas.delete_node(second.id)

        self.assertEqual([node.title for node in canvas.nodes], ["一", "三"])
        self.assertEqual([node.order for node in canvas.nodes], [1, 2])
        self.assertEqual(third.order, 2)

    def test_blueprint_group_delete_ungroups_nodes(self) -> None:
        project = ProjectData(name="蓝图组")
        project.ensure_canvas_structure()
        canvas = project.root_canvas()
        group = canvas.add_group(BlueprintGroup(title="组"))
        node = canvas.add_node(Node(title="节点", group_id=group.id))

        canvas.delete_group(group.id)

        self.assertEqual(canvas.groups, [])
        self.assertEqual(node.group_id, "")

    def test_blueprint_group_can_be_edge_endpoint(self) -> None:
        project = ProjectData(name="蓝图组连线")
        project.ensure_canvas_structure()
        canvas = project.root_canvas()
        group = canvas.add_group(BlueprintGroup(title="组"))
        node = canvas.add_node(Node(title="节点"))

        edge = canvas.add_edge(group.id, node.id)

        self.assertIsNotNone(edge)
        self.assertEqual(canvas.valid_edges()[0].source, group.id)
        canvas.delete_group(group.id)
        self.assertEqual(canvas.valid_edges(), [])

    def test_project_gdc_roundtrip_with_multiple_canvases(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            tmp_path = Path(folder)
            project = ProjectData(name="多画布项目")
            project.ensure_canvas_structure()
            root = project.root_canvas()
            link = Node(title="战斗子画布", node_type="画布", icon="画")
            root.nodes.append(link)
            child = project.add_canvas("战斗子画布", parent_canvas_id=root.id, parent_node_id=link.id)
            link.canvas_id = child.id
            child.nodes.append(Node(title="敌人配置", fields=[NodeField("血量", "整数", "100")]))

            path = tmp_path / "multi.gdc"
            save_project(project, path)
            loaded = load_project(path)
            loaded_root = loaded.root_canvas()
            loaded_link = loaded_root.nodes[0]
            loaded_child = loaded.find_canvas(loaded_link.canvas_id)

            self.assertEqual(loaded_link.node_type, "画布")
            self.assertIsNotNone(loaded_child)
            self.assertEqual(loaded_child.parent_canvas_id, loaded_root.id)
            self.assertEqual(loaded_child.parent_node_id, loaded_link.id)
            self.assertEqual(loaded_child.nodes[0].title, "敌人配置")

    def test_link_document_files_live_in_project_bundle(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            tmp_path = Path(folder)
            project_path = tmp_path / "links.gdc"
            source_dir = tmp_path / "source"

            relative = create_link_document(project_path, "设计文档", "md")
            resolved = resolve_link_document(project_path, relative)
            self.assertEqual(resolved.parent, project_bundle_dir(project_path) / "linked_docs")
            self.assertEqual(resolved.suffix, ".md")
            self.assertIn("# 设计文档", read_link_document(project_path, relative))

            write_link_document(project_path, relative, "hello")
            self.assertEqual(read_link_document(project_path, relative), "hello")
            renamed = rename_link_document(project_path, relative, "重命名文档")
            self.assertEqual(renamed, "linked_docs/重命名文档.md")
            self.assertFalse(resolved.exists())
            self.assertEqual(read_link_document(project_path, renamed), "hello")
            copied = sync_link_document_copy(project_path, relative, source_dir)
            self.assertIsNone(copied)
            copied = sync_link_document_copy(project_path, renamed, source_dir)
            self.assertEqual(copied, source_dir / renamed)
            self.assertEqual((source_dir / renamed).read_text(encoding="utf-8"), "hello")
            delete_link_document_copy(source_dir, renamed)
            self.assertFalse((source_dir / renamed).exists())
            delete_link_document(project_path, renamed)
            self.assertFalse(resolve_link_document(project_path, renamed).exists())

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

    def test_field_defaults_and_extra_export_props_are_separate(self) -> None:
        field = NodeField.from_dict(
            {
                "name": "日期字段",
                "data_type": "日期",
                "value": "2026-05-22",
                "image_path": "D:/should/not/export.png",
                "export_props": ["name", "data_type", "value", "image_path", "x", "font_size"],
            }
        )

        self.assertEqual(field.data_type, "日期")
        self.assertEqual(field.image_path, "")
        self.assertEqual(field.export_props, ["x", "font_size"])

    def test_default_tech_tree_template_uses_visual_cards(self) -> None:
        template = next(item for item in default_templates() if item.name == "科技树节点")
        node = template.create_node(0, 0)

        self.assertEqual(template.icon, "N")
        self.assertEqual(len(template.fields), 5)
        self.assertEqual([field.value for field in template.fields], [
            "节点名字",
            "最大等级",
            "解锁后获得效果的描述",
            "0% -> 5%",
            "5000$",
        ])
        self.assertTrue(all(field.has_visual_layout() for field in node.fields))
        self.assertEqual(node.fields[0].text_h_align, "center")
        self.assertEqual(node.fields[0].text_v_align, "center")

    def _csv_names(self, path: Path) -> list[str]:
        with path.open("r", encoding="utf-8-sig", newline="") as file:
            rows = list(csv.reader(file))
        return [row[0] for row in rows[2:]]


if __name__ == "__main__":
    unittest.main()
