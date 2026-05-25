import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QEvent, QMimeData, QPointF, Qt
from PySide6.QtGui import QImage, QKeyEvent, QTextCursor
from PySide6.QtWidgets import QApplication, QDialog, QMessageBox, QWidget

from gamedesigner.app import EDGE_LABEL_MAX_LENGTH, GameDesignerApp
from gamedesigner.ai_tools import AiCanvasAction, AiCanvasFieldChange
from gamedesigner.ai_tools import AiChatMessage, load_project_chat_history, save_project_chat_history
from gamedesigner.canvas_io import import_canvas_sheet
from gamedesigner.qt_dialogs import HEADER_HEIGHT, InlineFieldEditor, NodeEditorDialog
from gamedesigner.models import BlueprintGroup, Node, NodeField, NodeTemplate, ProjectData
from gamedesigner.storage import (
    AppSettings,
    load_settings,
    load_project,
    load_project_window_layouts,
    save_project,
    save_project_window_layouts,
    save_settings,
)
from gamedesigner.ui.link_document_dialog import LinkDocumentDialog
from gamedesigner.ui.ai_chat_dialog import AiChatPanel, AiSettingsDialog
from gamedesigner.ui.submit_text_edit import SubmitPlainTextEdit


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

    def test_copy_paste_selected_blueprint_group_duplicates_group_and_members(self) -> None:
        window = GameDesignerApp()
        project = ProjectData(name="蓝图复制测试")
        project.ensure_canvas_structure()
        canvas = project.root_canvas()
        group = canvas.add_group(BlueprintGroup(title="流程组", x=100, y=120, width=520, height=260))
        inside = canvas.add_node(Node(title="组内节点", x=160, y=180, group_id=group.id))
        outside = canvas.add_node(Node(title="组外节点", x=720, y=180))
        page = window._add_page(project, None, dirty=False, canvas_data=canvas)
        window.tabs.setCurrentWidget(page)
        page.canvas.select_group(group.id)

        window._copy_selected_nodes()
        window._paste_nodes()

        self.assertEqual(len(canvas.groups), 2)
        pasted_group = next(item for item in canvas.groups if item.id != group.id)
        self.assertEqual(pasted_group.title, group.title)
        self.assertEqual(pasted_group.x, group.x + 40)
        self.assertEqual(pasted_group.y, group.y + 40)
        pasted_inside = next(node for node in canvas.nodes if node.title == inside.title and node.id != inside.id)
        self.assertEqual(pasted_inside.group_id, pasted_group.id)
        self.assertEqual(pasted_inside.x, inside.x + 40)
        self.assertEqual(pasted_inside.y, inside.y + 40)
        self.assertIsNone(next((node for node in canvas.nodes if node.title == outside.title and node.id != outside.id), None))
        self.assertEqual(page.canvas.selected_group_ids, {pasted_group.id})
        window.deleteLater()

    def test_duplicate_selected_node_copies_and_pastes_immediately(self) -> None:
        window = GameDesignerApp()
        project = ProjectData(name="直接复制测试")
        project.ensure_canvas_structure()
        source = project.root_canvas().add_node(Node(title="原节点", x=20, y=30))
        page = window._add_page(project, None, dirty=False, canvas_data=project.root_canvas())
        window.tabs.setCurrentWidget(page)
        page.canvas.select_node(source.id)

        window._duplicate_selected()

        nodes = project.root_canvas().nodes
        self.assertEqual(len(nodes), 2)
        pasted = next(node for node in nodes if node.id != source.id)
        self.assertEqual(pasted.title, source.title)
        self.assertEqual(pasted.x, source.x + 40)
        self.assertEqual(pasted.y, source.y + 40)
        window.deleteLater()

    def test_ai_project_context_uses_current_page_selection(self) -> None:
        window = GameDesignerApp()
        project = ProjectData(name="AI上下文测试")
        project.ensure_canvas_structure()
        canvas = project.root_canvas()
        node = canvas.add_node(Node(title="关键节点", fields=[NodeField("说明", "文本", "需要分析")]))
        page = window._add_page(project, Path("D:/GameDesigner/AiContext.gdc"), dirty=False, canvas_data=canvas)
        window.tabs.setCurrentWidget(page)
        page.canvas.select_node(node.id)

        context, cwd, project_path = window._current_ai_project_context()

        self.assertEqual(cwd, Path("D:/GameDesigner"))
        self.assertEqual(project_path, Path("D:/GameDesigner/AiContext.gdc"))
        self.assertIn("AI上下文测试", context)
        self.assertIn("关键节点", context)
        self.assertIn("当前选中", context)
        window.deleteLater()

    def test_ai_assistant_lives_in_collapsible_right_panel(self) -> None:
        window = GameDesignerApp()

        self.assertFalse(window.ai_assistant_expanded)
        self.assertEqual(window.ai_assistant_stack.width(), 42)

        window._open_ai_chat()

        self.assertTrue(window.ai_assistant_expanded)
        self.assertIsNotNone(window.ai_assistant_panel)
        self.assertEqual(window.ai_assistant_stack.width(), 560)
        self.assertIs(window.ai_assistant_stack.currentWidget(), window.ai_assistant_panel)

        window._collapse_ai_assistant()

        self.assertFalse(window.ai_assistant_expanded)
        self.assertEqual(window.ai_assistant_stack.width(), 42)
        self.assertIs(window.ai_assistant_stack.currentWidget(), window.ai_assistant_collapsed)
        window.deleteLater()

    def test_ai_settings_dialog_one_click_uses_ollama_free_preset(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            settings = AppSettings(workspace_dir=folder, export_dir=str(Path(folder) / "exports"))
            with mock.patch.dict(os.environ, {"APPDATA": folder}):
                save_settings(settings)
                dialog = AiSettingsDialog(None, settings)
                self.assertEqual(dialog.free_model_combo.currentData(), "")
                index = dialog.free_model_combo.findData("free_ollama_gpt_oss_20b")
                self.assertGreaterEqual(index, 0)
                dialog.free_model_combo.setCurrentIndex(index)

                dialog.free_model_apply_button.click()

                self.assertEqual(dialog.result(), QDialog.Accepted)
                self.assertEqual(settings.ai_provider, "codex")
                self.assertEqual(settings.ai_model, "gpt-oss:20b")
                self.assertEqual(settings.ai_auth_mode, "api_key")
                self.assertEqual(settings.ai_api_key, "ollama")
                self.assertEqual(settings.ai_base_url, "http://localhost:11434/v1")
                loaded = load_settings()
                self.assertEqual(loaded.ai_model, "gpt-oss:20b")
                self.assertIn("free_ollama_gpt_oss_20b", loaded.ai_saved_connections)
                dialog.deleteLater()

    def test_ai_settings_dialog_can_restore_previous_custom_api_after_free_preset(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            settings = AppSettings(
                workspace_dir=folder,
                export_dir=str(Path(folder) / "exports"),
                ai_provider="codex",
                ai_model="gpt-5.5",
                ai_auth_mode="api_key",
                ai_api_key="paid-secret",
                ai_base_url="https://api.example.test/v1",
            )
            with mock.patch.dict(os.environ, {"APPDATA": folder}):
                save_settings(settings)
                dialog = AiSettingsDialog(None, settings)
                self.assertEqual(dialog.free_model_combo.currentData(), "")
                dialog.free_model_combo.setCurrentIndex(dialog.free_model_combo.findData("free_ollama_qwen3_8b"))
                dialog.free_model_apply_button.click()
                dialog.deleteLater()

                reopened = AiSettingsDialog(None, settings)
                reopened.api_profile_button.click()

                self.assertEqual(reopened.result(), QDialog.Accepted)
                self.assertEqual(settings.ai_model, "gpt-5.5")
                self.assertEqual(settings.ai_auth_mode, "api_key")
                self.assertEqual(settings.ai_api_key, "paid-secret")
                self.assertEqual(settings.ai_base_url, "https://api.example.test/v1")
                self.assertEqual(reopened.free_model_combo.currentData(), "")
                reopened.deleteLater()

    def test_ai_settings_dialog_shows_no_free_model_for_own_api_key(self) -> None:
        settings = AppSettings(
            ai_provider="codex",
            ai_model="openrouter/free",
            ai_auth_mode="api_key",
            ai_api_key="user-openrouter-key",
            ai_base_url="https://openrouter.ai/api/v1",
        )

        dialog = AiSettingsDialog(None, settings)

        self.assertEqual(dialog.free_model_combo.currentText(), "不使用")
        self.assertEqual(dialog.free_model_combo.currentData(), "")
        dialog.deleteLater()

    def test_ai_settings_dialog_free_remote_preset_waits_for_api_key(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            settings = AppSettings(workspace_dir=folder, export_dir=str(Path(folder) / "exports"))
            with mock.patch.dict(os.environ, {"APPDATA": folder}):
                save_settings(settings)
                dialog = AiSettingsDialog(None, settings)
                dialog.free_model_combo.setCurrentIndex(dialog.free_model_combo.findData("free_openrouter_router"))

                with mock.patch.object(QMessageBox, "information") as information:
                    dialog.free_model_apply_button.click()

                self.assertNotEqual(dialog.result(), QDialog.Accepted)
                information.assert_called_once()
                self.assertEqual(dialog.model_combo.currentText(), "openrouter/free")
                self.assertEqual(dialog.base_url_edit.text(), "https://openrouter.ai/api/v1")
                self.assertTrue(dialog.api_key_radio.isChecked())
                self.assertTrue(dialog.api_key_edit.isEnabled())
                dialog.deleteLater()

    def test_ai_panel_clear_screen_keeps_project_memory(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            project_path = Path(folder) / "MemoryProject.gdc"
            save_project_chat_history(
                project_path,
                [AiChatMessage("user", "上一轮"), AiChatMessage("assistant", "上一答")],
            )

            panel = AiChatPanel(
                None,
                AppSettings(ai_provider="codex", ai_model="gpt-5.4"),
                lambda: ("项目上下文", project_path.parent, project_path),
            )

            self.assertEqual(panel.clear_button.text(), "清空屏幕")
            self.assertIn("上一轮", panel.transcript.toPlainText())

            panel.clear_button.click()

            self.assertNotIn("上一轮", panel.transcript.toPlainText())
            self.assertIn("会话记忆仍保留", panel.transcript.toPlainText())
            loaded = load_project_chat_history(project_path)
            self.assertEqual([message.content for message in loaded], ["上一轮", "上一答"])
            self.assertEqual([message.content for message in panel._history], ["上一轮", "上一答"])
            panel.deleteLater()

    def test_ai_panel_activity_log_and_busy_state_are_visible(self) -> None:
        project_path = Path("D:/GameDesigner/ActivityProject.gdc")
        panel = AiChatPanel(
            None,
            AppSettings(ai_provider="codex", ai_model="gpt-5.4"),
            lambda: ("项目上下文", project_path.parent, project_path),
        )

        panel._start_activity("codex 正在思考")
        panel._tick_activity()
        panel._append_activity_from_chunk("thinking **Inspecting canvas**\n普通回复正文不显示\nrunning command: rg nodes", "运行日志")

        self.assertFalse(panel.busy_bar.isHidden())
        self.assertIn("codex 正在思考", panel.activity_label.text())
        log_text = panel.activity_log.toPlainText()
        self.assertIn("Inspecting canvas", log_text)
        self.assertIn("running command", log_text)
        self.assertNotIn("普通回复正文不显示", log_text)
        self.assertNotIn("Inspecting canvas", panel.transcript.toPlainText())

        panel._stop_activity()

        self.assertTrue(panel.busy_bar.isHidden())
        self.assertEqual(panel.activity_label.text(), "就绪")
        panel.deleteLater()

    def test_ai_panel_activity_log_is_separate_and_keeps_recent_lines(self) -> None:
        project_path = Path("D:/GameDesigner/ActivityTrimProject.gdc")
        panel = AiChatPanel(
            None,
            AppSettings(ai_provider="codex", ai_model="gpt-5.4"),
            lambda: ("项目上下文", project_path.parent, project_path),
        )

        panel._append_activity_lines([f"running command {index}" for index in range(60)], "运行日志")

        log_lines = panel.activity_log.toPlainText().splitlines()
        self.assertEqual(len(log_lines), 48)
        self.assertNotIn("running command 0", panel.activity_log.toPlainText())
        self.assertIn("running command 59", panel.activity_log.toPlainText())
        self.assertNotIn("running command", panel.transcript.toPlainText())

        panel._clear_screen()

        self.assertEqual(panel.activity_log.toPlainText(), "")
        panel.deleteLater()

    def test_submit_plain_text_edit_enter_submits_shift_enter_inserts_newline(self) -> None:
        editor = SubmitPlainTextEdit()
        submitted: list[bool] = []
        editor.submitted.connect(lambda: submitted.append(True))
        editor.setPlainText("问题")
        editor.moveCursor(QTextCursor.End)

        shift_enter = QKeyEvent(QEvent.Type.KeyPress, Qt.Key_Return, Qt.ShiftModifier)
        editor.keyPressEvent(shift_enter)

        self.assertEqual(editor.toPlainText(), "问题\n")
        self.assertEqual(submitted, [])

        enter = QKeyEvent(QEvent.Type.KeyPress, Qt.Key_Return, Qt.NoModifier)
        editor.keyPressEvent(enter)

        self.assertTrue(enter.isAccepted())
        self.assertEqual(submitted, [True])
        editor.deleteLater()

    def test_submit_plain_text_edit_pasted_image_emits_signal(self) -> None:
        editor = SubmitPlainTextEdit()
        image = QImage(18, 12, QImage.Format_ARGB32)
        image.fill(Qt.red)
        mime = QMimeData()
        mime.setImageData(image)
        captured: list[QImage] = []
        editor.imagePasted.connect(captured.append)

        editor.insertFromMimeData(mime)

        self.assertEqual(len(captured), 1)
        self.assertEqual(captured[0].width(), 18)
        self.assertEqual(captured[0].height(), 12)
        self.assertEqual(editor.toPlainText(), "")
        editor.deleteLater()

    def test_ai_panel_pasted_image_is_saved_and_added_to_prompt(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            project_path = Path(folder) / "ImageChatProject.gdc"
            panel = AiChatPanel(
                None,
                AppSettings(ai_provider="codex", ai_model="gpt-5.4"),
                lambda: ("项目上下文", project_path.parent, project_path),
            )
            image = QImage(24, 16, QImage.Format_ARGB32)
            image.fill(Qt.blue)

            panel._attach_clipboard_image(image)

            self.assertEqual(len(panel._pending_image_attachments), 1)
            attachment = panel._pending_image_attachments[0]
            self.assertTrue(attachment.path.exists())
            self.assertIn("ai_chat", attachment.path.parts)
            self.assertIn("attachments", attachment.path.parts)
            self.assertIn(attachment.path.name, panel.attachments_label.text())
            prompt = panel._message_with_image_attachments("看这张图", panel._pending_image_attachments)
            self.assertIn(str(attachment.path.resolve()), prompt)
            self.assertIn("24x16", prompt)
            panel.deleteLater()

    def test_inline_field_editor_enter_commits_shift_enter_inserts_newline(self) -> None:
        editor = InlineFieldEditor()
        committed: list[bool] = []
        editor.editingFinished.connect(lambda: committed.append(True))
        editor.setPlainText("内容")
        editor.moveCursor(QTextCursor.End)

        shift_enter = QKeyEvent(QEvent.Type.KeyPress, Qt.Key_Return, Qt.ShiftModifier)
        editor.keyPressEvent(shift_enter)

        self.assertEqual(editor.toPlainText(), "内容\n")
        self.assertEqual(committed, [])

        enter = QKeyEvent(QEvent.Type.KeyPress, Qt.Key_Return, Qt.NoModifier)
        editor.keyPressEvent(enter)

        self.assertTrue(enter.isAccepted())
        self.assertEqual(committed, [True])
        editor.deleteLater()

    def test_apply_ai_canvas_actions_creates_and_updates_current_canvas_nodes(self) -> None:
        window = GameDesignerApp()
        project = ProjectData(name="AI助手操作测试")
        project.ensure_canvas_structure()
        canvas = project.root_canvas()
        source = canvas.add_node(Node(title="参考节点", x=100, y=120, fields=[NodeField("内容信息", "长文本", "旧内容")]))
        page = window._add_page(project, None, dirty=False, canvas_data=canvas)
        window.tabs.setCurrentWidget(page)
        page.canvas.select_node(source.id)

        message = window._apply_ai_canvas_actions(
            [
                AiCanvasAction(
                    type="create_node",
                    title="延伸节点",
                    icon="延",
                    fields=[AiCanvasFieldChange("内容信息", "长文本", "基于参考节点继续迭代")],
                ),
                AiCanvasAction(
                    type="update_node",
                    node_id=source.id,
                    title="参考节点强化版",
                    fields=[AiCanvasFieldChange("内容信息", "长文本", "更新后的内容")],
                ),
            ]
        )

        self.assertIn("创建 1 个节点", message)
        self.assertIn("更新 1 个节点", message)
        self.assertEqual(len(canvas.nodes), 2)
        created = next(node for node in canvas.nodes if node.id != source.id)
        self.assertEqual(created.title, "延伸节点")
        self.assertEqual(created.fields[0].value, "基于参考节点继续迭代")
        self.assertGreater(created.x, source.x)
        self.assertEqual(len(canvas.edges), 1)
        self.assertEqual((canvas.edges[0].source, canvas.edges[0].target), (source.id, created.id))
        self.assertEqual(source.title, "参考节点强化版")
        self.assertEqual(source.fields[0].value, "更新后的内容")
        self.assertEqual(page.canvas.selected_node_ids, {source.id, created.id})
        self.assertTrue(page.dirty)
        window.deleteLater()

    def test_ai_child_node_is_created_to_the_right_and_connected(self) -> None:
        window = GameDesignerApp()
        project = ProjectData(name="AI子节点位置测试")
        project.ensure_canvas_structure()
        canvas = project.root_canvas()
        parent = canvas.add_node(Node(title="父节点", x=100, y=120, width=520, height=300))
        page = window._add_page(project, None, dirty=False, canvas_data=canvas)
        window.tabs.setCurrentWidget(page)
        page.canvas.select_node(parent.id)

        window._apply_ai_canvas_actions(
            [
                AiCanvasAction(
                    type="create_node",
                    title="子节点",
                    width=220,
                    height=140,
                    fields=[AiCanvasFieldChange("内容信息", "长文本", "子节点内容")],
                )
            ]
        )

        child = next(node for node in canvas.nodes if node.id != parent.id)
        self.assertEqual(child.title, "子节点")
        self.assertEqual(child.x, parent.x + parent.width + 100)
        self.assertEqual(child.y, parent.y + (parent.height - 140) / 2)
        self.assertEqual(len(canvas.edges), 1)
        self.assertEqual(canvas.edges[0].source, parent.id)
        self.assertEqual(canvas.edges[0].target, child.id)
        window.deleteLater()

    def test_ai_untemplated_create_node_uses_label_default(self) -> None:
        window = GameDesignerApp()
        project = ProjectData(name="AI默认Label节点")
        project.ensure_canvas_structure()
        canvas = project.root_canvas()
        page = window._add_page(project, None, dirty=False, canvas_data=canvas)
        window.tabs.setCurrentWidget(page)

        window._apply_ai_canvas_actions([AiCanvasAction(type="create_node", title="玩法描述")])

        created = canvas.nodes[-1]
        self.assertEqual(created.title, "玩法描述")
        self.assertEqual(created.icon, "")
        self.assertFalse(created.icon_from_title)
        self.assertEqual(len(created.fields), 1)
        self.assertEqual(created.fields[0].name, "描述")
        self.assertEqual(created.fields[0].data_type, "长文本")
        self.assertEqual(created.fields[0].value, "节点的描述")
        self.assertTrue(created.fields[0].has_visual_layout())
        window.deleteLater()

    def test_ai_create_node_with_reference_id_clones_reference_layout(self) -> None:
        window = GameDesignerApp()
        project = ProjectData(name="AI参考节点布局")
        project.ensure_canvas_structure()
        canvas = project.root_canvas()
        reference = canvas.add_node(
            Node(
                title="绿色史莱姆",
                x=120,
                y=100,
                width=320,
                height=180,
                fields=[
                    NodeField("行为", "长文本", "被攻击后追近玩家", x=10, y=0, width=280, height=74),
                    NodeField("掉落", "长文本", "100%绿色粘液", x=10, y=84, width=280, height=74),
                ],
            )
        )
        page = window._add_page(project, None, dirty=False, canvas_data=canvas)
        window.tabs.setCurrentWidget(page)

        window._apply_ai_canvas_actions(
            [
                AiCanvasAction(
                    type="create_node",
                    title="黄色史莱姆",
                    reference_node_id=reference.id,
                    fields=[
                        AiCanvasFieldChange("行为", "长文本", "会吐出减速泡沫并靠近玩家"),
                        AiCanvasFieldChange("掉落", "长文本", "100%黄色粘液"),
                    ],
                )
            ]
        )

        created = next(node for node in canvas.nodes if node.id != reference.id)
        self.assertEqual(created.title, "黄色史莱姆")
        self.assertEqual((created.width, created.height), (reference.width, reference.height))
        self.assertEqual([field.name for field in created.fields], ["行为", "掉落"])
        self.assertEqual([field.value for field in created.fields], ["会吐出减速泡沫并靠近玩家", "100%黄色粘液"])
        self.assertEqual(
            [(field.x, field.y, field.width, field.height) for field in created.fields],
            [(field.x, field.y, field.width, field.height) for field in reference.fields],
        )
        window.deleteLater()

    def test_ai_create_node_semantically_reuses_existing_same_kind_node(self) -> None:
        window = GameDesignerApp()
        project = ProjectData(name="AI同类节点参考")
        project.ensure_canvas_structure()
        canvas = project.root_canvas()
        reference = canvas.add_node(
            Node(
                title="绿色史莱姆",
                width=320,
                height=180,
                fields=[
                    NodeField("行为", "长文本", "行为简单，被攻击后会追近玩家进行攻击。", x=10, y=0, width=280, height=74),
                    NodeField("掉落", "长文本", "100%绿色粘液", x=10, y=84, width=280, height=74),
                ],
            )
        )
        canvas.add_node(Node(title="绿色森林1", x=420, fields=[NodeField("产出", "长文本", "绿色史莱姆100%")]))
        page = window._add_page(project, None, dirty=False, canvas_data=canvas)
        window.tabs.setCurrentWidget(page)

        window._apply_ai_canvas_actions([AiCanvasAction(type="create_node", title="黄色史莱姆")])

        created = next(node for node in canvas.nodes if node.title == "黄色史莱姆")
        self.assertEqual((created.width, created.height), (reference.width, reference.height))
        self.assertEqual([field.name for field in created.fields], ["行为", "掉落"])
        self.assertEqual(created.fields[0].value, "行为简单，被攻击后会追近玩家进行攻击。")
        self.assertEqual(created.fields[1].value, "100%黄色粘液")
        self.assertEqual(created.fields[0].width, reference.fields[0].width)
        self.assertEqual(created.fields[1].y, reference.fields[1].y)
        window.deleteLater()

    def test_ai_create_node_inherits_selected_node_template(self) -> None:
        window = GameDesignerApp()
        name_field = NodeField("节点名字", "文本", "原技能")
        desc_field = NodeField("解锁描述", "长文本", "旧描述")
        template = NodeTemplate(
            id="template_skill",
            name="技能模板",
            title_field_id=name_field.id,
            fields=[name_field, desc_field],
        )
        project = ProjectData(name="AI模板继承测试", templates=[template])
        project.ensure_canvas_structure()
        canvas = project.root_canvas()
        source = template.create_node(100, 120)
        canvas.add_node(source)
        page = window._add_page(project, None, dirty=False, canvas_data=canvas)
        window.tabs.setCurrentWidget(page)
        page.canvas.select_node(source.id)

        message = window._apply_ai_canvas_actions(
            [
                AiCanvasAction(
                    type="create_node",
                    title="火焰冲刺",
                    fields=[
                        AiCanvasFieldChange("节点名字", "文本", "火焰冲刺"),
                        AiCanvasFieldChange("解锁描述", "长文本", "冲刺后留下燃烧路径"),
                    ],
                )
            ]
        )

        created = next(node for node in canvas.nodes if node.id != source.id)
        self.assertIn("创建 1 个节点", message)
        self.assertEqual(created.template_id, template.id)
        self.assertEqual([field.name for field in created.fields], ["节点名字", "解锁描述"])
        self.assertEqual(created.fields[0].value, "火焰冲刺")
        self.assertEqual(created.fields[1].value, "冲刺后留下燃烧路径")
        window.deleteLater()

    def test_ai_create_group_creates_group_and_member_nodes(self) -> None:
        window = GameDesignerApp()
        project = ProjectData(name="AI蓝图组测试")
        project.ensure_canvas_structure()
        canvas = project.root_canvas()
        group = canvas.add_group(BlueprintGroup(title="参考组", x=100, y=120, width=640, height=280))
        canvas.add_node(Node(title="参考节点", group_id=group.id, x=140, y=180, fields=[NodeField("内容信息", "长文本", "参考")]))
        page = window._add_page(project, None, dirty=False, canvas_data=canvas)
        window.tabs.setCurrentWidget(page)
        page.canvas.select_group(group.id)

        message = window._apply_ai_canvas_actions(
            [
                AiCanvasAction(
                    type="create_group",
                    title="迭代组",
                    nodes=[
                        AiCanvasAction(
                            type="create_node",
                            title="迭代节点A",
                            fields=[AiCanvasFieldChange("内容信息", "长文本", "A")],
                        ),
                        AiCanvasAction(
                            type="create_node",
                            title="迭代节点B",
                            fields=[AiCanvasFieldChange("内容信息", "长文本", "B")],
                        ),
                    ],
                )
            ]
        )

        self.assertIn("创建 1 个蓝图组", message)
        self.assertIn("创建 2 个节点", message)
        created_group = next(item for item in canvas.groups if item.id != group.id)
        created_nodes = [node for node in canvas.nodes if node.group_id == created_group.id]
        self.assertEqual(created_group.title, "迭代组")
        self.assertEqual([node.title for node in created_nodes], ["迭代节点A", "迭代节点B"])
        self.assertEqual(page.canvas.selected_node_ids, {node.id for node in created_nodes})
        self.assertEqual(page.canvas.selected_group_ids, {created_group.id})
        window.deleteLater()

    def test_ai_canvas_actions_can_write_current_canvas_rules_memory(self) -> None:
        window = GameDesignerApp()
        project = ProjectData(name="AI画布规则测试")
        project.ensure_canvas_structure()
        canvas = project.root_canvas()
        page = window._add_page(project, None, dirty=False, canvas_data=canvas)
        window.tabs.setCurrentWidget(page)

        message = window._apply_ai_canvas_actions(
            [
                AiCanvasAction(
                    type="update_canvas_rules",
                    rules="- 本画布新增节点必须沿用当前模板\n- 设计内容优先服务 Boss Rush",
                )
            ]
        )

        self.assertIn("写入当前画布规则记忆", message)
        self.assertIn("沿用当前模板", canvas.ai_rules)
        self.assertTrue(page.dirty)
        window.deleteLater()

    def test_ai_iteration_assistant_expands_panel_and_enters_iteration_mode(self) -> None:
        window = GameDesignerApp()
        project = ProjectData(name="AI右键迭代测试")
        project.ensure_canvas_structure()
        canvas = project.root_canvas()
        node = canvas.add_node(Node(title="参考节点"))
        page = window._add_page(project, None, dirty=False, canvas_data=canvas)
        window.tabs.setCurrentWidget(page)
        page.canvas.select_node(node.id)

        window._open_ai_iteration_assistant()

        self.assertTrue(window.ai_assistant_expanded)
        self.assertIsNotNone(window.ai_assistant_panel)
        self.assertTrue(window.ai_assistant_panel._iteration_mode)
        self.assertIn("基于当前选中对象继续迭代", window.ai_assistant_panel.input.toPlainText())
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

    def test_add_node_uses_default_label_node(self) -> None:
        window = GameDesignerApp()
        project = ProjectData(name="默认Label节点测试")
        project.ensure_canvas_structure()
        page = window._add_page(project, None, dirty=False, canvas_data=project.root_canvas())
        window.tabs.setCurrentWidget(page)

        window._add_node_at(260, 180)

        created = project.root_canvas().nodes[-1]
        self.assertEqual(created.title, "Label节点")
        self.assertEqual(created.icon, "")
        self.assertFalse(created.icon_from_title)
        self.assertEqual(len(created.fields), 1)
        self.assertEqual(created.fields[0].name, "描述")
        self.assertEqual(created.fields[0].data_type, "长文本")
        self.assertEqual(created.fields[0].value, "节点的描述")
        self.assertTrue(created.fields[0].has_visual_layout())
        window.deleteLater()

    def test_new_edges_use_last_selected_edge_style(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            tmp_path = Path(folder)
            settings = AppSettings(workspace_dir=folder, export_dir=str(tmp_path / "exports"))
            with mock.patch.dict(os.environ, {"APPDATA": folder}):
                save_settings(settings)
                window = GameDesignerApp()
                project = ProjectData(name="连线样式记忆")
                project.ensure_canvas_structure()
                canvas = project.root_canvas()
                first = canvas.add_node(Node(title="A"))
                second = canvas.add_node(Node(title="B", x=420))
                third = canvas.add_node(Node(title="C", y=260))
                existing = canvas.add_edge(first.id, second.id)
                page = window._add_page(project, None, dirty=False, canvas_data=canvas)
                window.tabs.setCurrentWidget(page)

                window._set_edge_style(existing.id, "straight")
                window._create_edge(first.id, third.id)

                created = next(edge for edge in canvas.edges if edge.target == third.id)
                self.assertEqual(existing.style, "straight")
                self.assertEqual(created.style, "straight")
                window.deleteLater()

    def test_last_selected_edge_style_persists_between_app_launches(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            tmp_path = Path(folder)
            settings = AppSettings(workspace_dir=folder, export_dir=str(tmp_path / "exports"))
            with mock.patch.dict(os.environ, {"APPDATA": folder}):
                save_settings(settings)
                first_window = GameDesignerApp()
                first_project = ProjectData(name="连线样式持久化")
                first_project.ensure_canvas_structure()
                first_canvas = first_project.root_canvas()
                first = first_canvas.add_node(Node(title="A"))
                second = first_canvas.add_node(Node(title="B", x=420))
                existing = first_canvas.add_edge(first.id, second.id)
                first_page = first_window._add_page(first_project, None, dirty=False, canvas_data=first_canvas)
                first_window.tabs.setCurrentWidget(first_page)

                first_window._set_edge_style(existing.id, "orthogonal")
                first_window.deleteLater()

                reopened = GameDesignerApp()
                reopened_project = ProjectData(name="新窗口沿用连线样式")
                reopened_project.ensure_canvas_structure()
                reopened_canvas = reopened_project.root_canvas()
                source = reopened_canvas.add_node(Node(title="A"))
                target = reopened_canvas.add_node(Node(title="B", x=420))
                reopened_page = reopened._add_page(
                    reopened_project,
                    None,
                    dirty=False,
                    canvas_data=reopened_canvas,
                )
                reopened.tabs.setCurrentWidget(reopened_page)

                reopened._create_edge(source.id, target.id)

                self.assertEqual(reopened.settings.last_edge_style, "orthogonal")
                self.assertEqual(reopened_canvas.edges[0].style, "orthogonal")
                reopened.deleteLater()

    def test_edit_edge_sets_trimmed_short_label(self) -> None:
        window = GameDesignerApp()
        project = ProjectData(name="连线文本测试")
        project.ensure_canvas_structure()
        canvas = project.root_canvas()
        first = canvas.add_node(Node(title="A"))
        second = canvas.add_node(Node(title="B", x=420))
        edge = canvas.add_edge(first.id, second.id)
        page = window._add_page(project, None, dirty=False, canvas_data=canvas)
        window.tabs.setCurrentWidget(page)

        with mock.patch("gamedesigner.app.QInputDialog.getText", return_value=("  解锁   高级   订单 后续文字超出长度  ", True)):
            window._edit_edge(edge.id)

        self.assertEqual(edge.label, "解锁 高级 订单 后续文字超出长度"[:EDGE_LABEL_MAX_LENGTH])
        self.assertTrue(page.dirty)
        self.assertEqual(page.canvas.selected_edge_id, edge.id)
        window.deleteLater()

    def test_edit_edge_can_clear_label(self) -> None:
        window = GameDesignerApp()
        project = ProjectData(name="清空连线文本")
        project.ensure_canvas_structure()
        canvas = project.root_canvas()
        first = canvas.add_node(Node(title="A"))
        second = canvas.add_node(Node(title="B", x=420))
        edge = canvas.add_edge(first.id, second.id)
        edge.label = "解锁"
        page = window._add_page(project, None, dirty=False, canvas_data=canvas)
        window.tabs.setCurrentWidget(page)

        with mock.patch("gamedesigner.app.QInputDialog.getText", return_value=("   ", True)):
            window._edit_edge(edge.id)

        self.assertEqual(edge.label, "")
        self.assertTrue(page.dirty)
        window.deleteLater()

    def test_ai_tool_layer_can_create_edge_and_update_edge_label(self) -> None:
        window = GameDesignerApp()
        project = ProjectData(name="AI连线工具")
        project.ensure_canvas_structure()
        canvas = project.root_canvas()
        first = canvas.add_node(Node(title="前置节点"))
        second = canvas.add_node(Node(title="后续节点", x=420))
        page = window._add_page(project, None, dirty=False, canvas_data=canvas)
        window.tabs.setCurrentWidget(page)

        message = window._apply_ai_canvas_actions(
            [
                AiCanvasAction(
                    type="create_edge",
                    source_node_id=first.id,
                    target_node_id=second.id,
                    label="解锁",
                    style="straight",
                )
            ]
        )

        self.assertIn("创建 1 条连线", message)
        self.assertEqual(len(canvas.edges), 1)
        edge = canvas.edges[0]
        self.assertEqual((edge.source, edge.target), (first.id, second.id))
        self.assertEqual(edge.label, "解锁")
        self.assertEqual(edge.style, "straight")
        self.assertEqual(page.canvas.selected_edge_id, edge.id)

        message = window._apply_ai_canvas_actions(
            [AiCanvasAction(type="update_edge_label", edge_id=edge.id, label="前置")]
        )

        self.assertIn("更新 1 条连线文本", message)
        self.assertEqual(edge.label, "前置")
        window.deleteLater()

    def test_ai_read_only_tools_return_results_without_dirtying_canvas(self) -> None:
        window = GameDesignerApp()
        project = ProjectData(name="AI只读工具")
        project.ensure_canvas_structure()
        canvas = project.root_canvas()
        canvas.add_node(Node(title="绿色史莱姆", fields=[NodeField("掉落", "长文本", "100%绿色粘液")]))
        page = window._add_page(project, None, dirty=False, canvas_data=canvas)
        window.tabs.setCurrentWidget(page)

        message = window._apply_ai_canvas_actions(
            [
                AiCanvasAction(type="query_canvas", include_nodes=True),
                AiCanvasAction(type="search_nodes", query="史莱姆", limit=5),
            ]
        )

        self.assertIn("已执行画布工具", message)
        self.assertIn("当前画布", message)
        self.assertIn("绿色史莱姆", message)
        self.assertFalse(page.dirty)
        window.deleteLater()

    def test_add_data_canvas_creates_templated_child_canvas(self) -> None:
        window = GameDesignerApp()
        template = NodeTemplate(name="数据模板", fields=[NodeField("名称", "文本", "数据项")])
        project = ProjectData(name="数据画布测试", templates=[template])
        project.ensure_canvas_structure()
        page = window._add_page(project, None, dirty=False, canvas_data=project.root_canvas())
        window.tabs.setCurrentWidget(page)
        page.active_template_id = template.id

        window._add_data_canvas_node_at(260, 180)

        node = project.root_canvas().nodes[-1]
        canvas = project.find_canvas(node.canvas_id)
        self.assertIsNotNone(canvas)
        self.assertEqual(node.node_type, "画布")
        self.assertEqual(node.icon, "数")
        self.assertEqual(canvas.canvas_type, "data")
        self.assertEqual(canvas.data_layout, "grid")
        self.assertEqual(canvas.template_id, template.id)
        window.deleteLater()

    def test_add_node_in_data_canvas_uses_canvas_template_and_forces_lock(self) -> None:
        window = GameDesignerApp()
        title_field = NodeField("名称", "文本", "条目")
        template = NodeTemplate(
            name="数据模板",
            icon="数",
            title_field_id=title_field.id,
            fields=[title_field, NodeField("数值", "整数", "0")],
        )
        project = ProjectData(name="数据项测试", templates=[template])
        project.ensure_canvas_structure()
        data_canvas = project.add_canvas(
            "数据画布",
            canvas_type="data",
            data_layout="horizontal",
            template_id=template.id,
            parent_canvas_id=project.root_canvas_id,
            parent_node_id="node_parent",
        )
        page = window._add_page(project, None, dirty=False, canvas_data=data_canvas)
        window.tabs.setCurrentWidget(page)

        window._add_node_at(200, 160)
        window._add_node_at(320, 240)

        self.assertEqual(len(data_canvas.nodes), 2)
        self.assertTrue(all(node.template_locked for node in data_canvas.nodes))
        self.assertTrue(all(node.template_id == template.id for node in data_canvas.nodes))
        self.assertTrue(all(node.node_type == "普通" for node in data_canvas.nodes))
        self.assertEqual(data_canvas.nodes[0].x, data_canvas.nodes[1].x)
        self.assertLess(data_canvas.nodes[0].y, data_canvas.nodes[1].y)
        window.deleteLater()

    def test_data_canvas_table_mode_refreshes_page_view(self) -> None:
        window = GameDesignerApp()
        template = NodeTemplate(name="数据模板", fields=[NodeField("名称", "文本", "条目")])
        project = ProjectData(name="表格模式测试", templates=[template])
        project.ensure_canvas_structure()
        data_canvas = project.add_canvas(
            "排序画布",
            canvas_type="data",
            data_layout="table",
            template_id=template.id,
            parent_canvas_id=project.root_canvas_id,
            parent_node_id="node_parent",
        )
        data_canvas.add_node(template.create_node(0, 0))
        page = window._add_page(project, None, dirty=False, canvas_data=data_canvas)
        window.tabs.setCurrentWidget(page)
        page.show()
        window.show()
        self.app.processEvents()

        page.refresh_canvas_mode()

        self.assertFalse(page.canvas.isVisible())
        self.assertFalse(page.table_view.isHidden())
        self.assertTrue(page.function_bar.isVisible())
        self.assertTrue(page.table_layout_button.isVisible())
        self.assertTrue(page.table_layout_button.isChecked())
        self.assertEqual(page.table_view.rowCount(), 1)
        self.assertEqual(page.table_view.columnCount(), 1)
        window.deleteLater()

    def test_normal_canvas_function_bar_shows_reset_only(self) -> None:
        window = GameDesignerApp()
        project = ProjectData(name="自由画布功能栏")
        project.ensure_canvas_structure()
        page = window._add_page(project, None, dirty=False, canvas_data=project.root_canvas())
        window.tabs.setCurrentWidget(page)
        page.show()
        window.show()
        self.app.processEvents()

        page.refresh_canvas_mode()

        self.assertTrue(page.function_bar.isVisible())
        self.assertTrue(page.reset_view_button.isVisible())
        self.assertFalse(page.parent_button.isVisible())
        self.assertFalse(page.return_button.isVisible())
        self.assertFalse(page.horizontal_layout_button.isVisible())
        self.assertFalse(page.grid_layout_button.isVisible())
        self.assertFalse(page.table_layout_button.isVisible())
        self.assertFalse(page.independent_row_button.isVisible())
        self.assertFalse(page.thumbnail_row_button.isVisible())
        self.assertFalse(page.grid_rows_spin.isVisible())
        window.deleteLater()

    def test_data_canvas_function_bar_switches_layout_and_rows(self) -> None:
        window = GameDesignerApp()
        template = NodeTemplate(name="数据模板", fields=[NodeField("名称", "文本", "条目")])
        project = ProjectData(name="排序画布功能栏", templates=[template])
        project.ensure_canvas_structure()
        data_canvas = project.add_canvas(
            "排序画布",
            canvas_type="data",
            data_layout="grid",
            data_grid_rows=3,
            template_id=template.id,
            parent_canvas_id=project.root_canvas_id,
            parent_node_id="node_parent",
        )
        page = window._add_page(project, None, dirty=False, canvas_data=data_canvas)
        window.tabs.setCurrentWidget(page)
        page.show()
        window.show()
        self.app.processEvents()

        page.refresh_canvas_mode()

        self.assertTrue(page.parent_button.isVisible())
        self.assertTrue(page.return_button.isVisible())
        self.assertIs(page.function_bar.layout().itemAt(0).widget(), page.parent_button)
        self.assertIs(page.function_bar.layout().itemAt(1).widget(), page.return_button)
        self.assertIsNotNone(page.function_bar.layout().itemAt(2).spacerItem())
        self.assertTrue(page.horizontal_layout_button.isVisible())
        self.assertTrue(page.grid_layout_button.isVisible())
        self.assertTrue(page.table_layout_button.isVisible())
        self.assertTrue(page.grid_layout_button.isChecked())
        self.assertTrue(page.grid_rows_spin.isVisible())
        self.assertEqual(page.grid_rows_spin.value(), 3)

        page.table_layout_button.click()
        self.app.processEvents()
        self.assertEqual(data_canvas.data_layout, "table")
        self.assertFalse(page.canvas.isVisible())
        self.assertTrue(page.table_view.isVisible())

        page.horizontal_layout_button.click()
        self.app.processEvents()
        self.assertEqual(data_canvas.data_layout, "horizontal")
        self.assertTrue(page.canvas.isVisible())
        self.assertFalse(page.table_view.isVisible())
        self.assertTrue(page.independent_row_button.isVisible())
        self.assertTrue(page.thumbnail_row_button.isVisible())
        self.assertTrue(page.independent_row_button.isChecked())
        page.thumbnail_row_button.click()
        self.app.processEvents()
        self.assertEqual(data_canvas.data_row_style, "thumbnail")
        self.assertIsNotNone(page.canvas.data_header_item)

        page.grid_layout_button.click()
        self.app.processEvents()
        self.assertEqual(data_canvas.data_layout, "grid")
        self.assertFalse(page.independent_row_button.isVisible())
        self.assertFalse(page.thumbnail_row_button.isVisible())
        page.grid_rows_spin.setValue(5)
        self.app.processEvents()
        self.assertEqual(data_canvas.data_grid_rows, 5)
        window.deleteLater()

    def test_import_canvas_sheet_marks_project_dirty_and_refreshes_table(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            csv_path = Path(folder) / "rows.csv"
            csv_path.write_text("名称,数值\nA,1\nB,2\n", encoding="utf-8")

            window = GameDesignerApp()
            project = ProjectData(name="导入刷新测试")
            project.ensure_canvas_structure()
            data_canvas = project.add_canvas(
                "排序画布",
                canvas_type="data",
                data_layout="table",
                parent_canvas_id=project.root_canvas_id,
                parent_node_id="node_parent",
            )
            page = window._add_page(project, None, dirty=False, canvas_data=data_canvas)
            window.tabs.setCurrentWidget(page)

            import_canvas_sheet(project, data_canvas, csv_path)
            window._refresh_project_views(project)

            self.assertEqual(page.table_view.rowCount(), 2)
            self.assertEqual(page.table_view.columnCount(), 2)
            self.assertEqual(page.table_view.item(0, 0).text(), "A")
            self.assertEqual(page.table_view.item(1, 1).text(), "2")
            window.deleteLater()

    def test_data_canvas_table_paste_expands_rows_and_columns(self) -> None:
        window = GameDesignerApp()
        project = ProjectData(name="表格粘贴测试")
        project.ensure_canvas_structure()
        data_canvas = project.add_canvas(
            "排序画布",
            canvas_type="data",
            data_layout="table",
            parent_canvas_id=project.root_canvas_id,
            parent_node_id="node_parent",
        )
        page = window._add_page(project, None, dirty=False, canvas_data=data_canvas)
        window.tabs.setCurrentWidget(page)
        page.show()
        window.show()
        self.app.processEvents()

        QApplication.clipboard().setText("A\t1\nB\t2")
        page.table_view._paste_clipboard()

        self.assertEqual(len(data_canvas.nodes), 2)
        self.assertEqual(len(project.find_template(data_canvas.template_id).fields), 2)
        self.assertEqual(page.table_view.rowCount(), 2)
        self.assertEqual(page.table_view.columnCount(), 2)
        self.assertEqual(data_canvas.nodes[0].fields[0].value, "A")
        self.assertEqual(data_canvas.nodes[1].fields[1].value, "2")
        window.deleteLater()

    def test_export_canvas_csv_dialog_collects_per_canvas_folder(self) -> None:
        from gamedesigner.qt_dialogs import ExportCanvasCsvDialog

        project = ProjectData(name="导出目录测试")
        project.ensure_canvas_structure()
        root = project.root_canvas()
        link = root.add_node(Node(title="自由画布", node_type="画布"))
        child = project.add_canvas("自由画布", parent_canvas_id=root.id, parent_node_id=link.id)
        link.canvas_id = child.id

        dialog = ExportCanvasCsvDialog(None, project, "D:/default")
        checkbox, combo, folder_edit, edge_check = dialog._canvas_rows[child.id]
        self.assertIn("自由画布", checkbox.text())
        folder_edit.setText("D:/custom")
        edge_check.setChecked(True)
        dialog._accept()

        self.assertIsNotNone(dialog.result_data)
        specs = dialog.result_data["canvas_specs"]
        child_spec = next(spec for spec in specs if spec.canvas_id == child.id)
        self.assertEqual(child_spec.target_folder, "D:/custom")
        self.assertTrue(child_spec.export_edges)
        dialog.deleteLater()

    def test_export_canvas_csv_dialog_restores_saved_state(self) -> None:
        from gamedesigner.qt_dialogs import ExportCanvasCsvDialog

        project = ProjectData(name="导出状态测试")
        project.ensure_canvas_structure()
        root = project.root_canvas()
        root.name = "主画布"
        child = project.add_canvas("BodyData")
        state = {
            "folder": "D:/saved",
            "canvases": {
                child.id: {
                    "canvas_name": child.name,
                    "enabled": False,
                    "sort_mode": "x",
                    "target_folder": "D:/body",
                    "export_edges": True,
                }
            },
        }

        dialog = ExportCanvasCsvDialog(None, project, "D:/default", export_state=state)
        checkbox, combo, folder_edit, edge_check = dialog._canvas_rows[child.id]

        self.assertEqual(dialog.folder_edit.text(), "D:/saved")
        self.assertFalse(checkbox.isChecked())
        self.assertEqual(combo.currentData(), "x")
        self.assertEqual(folder_edit.text(), "D:/body")
        self.assertTrue(edge_check.isChecked())

        checkbox.setChecked(True)
        edge_check.setChecked(False)
        folder_edit.setText("D:/changed")
        dialog._accept()

        self.assertIsNotNone(dialog.result_data)
        saved = dialog.result_data["export_state"]
        self.assertEqual(saved["folder"], "D:/saved")
        self.assertTrue(saved["canvases"][child.id]["enabled"])
        self.assertFalse(saved["canvases"][child.id]["export_edges"])
        self.assertEqual(saved["canvases"][child.id]["target_folder"], "D:/changed")
        dialog.deleteLater()

    def test_export_canvas_csv_dialog_disables_edge_export_for_data_canvas(self) -> None:
        from gamedesigner.qt_dialogs import ExportCanvasCsvDialog

        project = ProjectData(name="数据画布导出连线")
        project.ensure_canvas_structure()
        data_canvas = project.add_canvas("数据画布", canvas_type="data")

        dialog = ExportCanvasCsvDialog(
            None,
            project,
            "D:/default",
            export_state={
                "canvases": {
                    data_canvas.id: {
                        "canvas_name": data_canvas.name,
                        "export_edges": True,
                    }
                }
            },
        )
        _checkbox, _combo, _folder_edit, edge_check = dialog._canvas_rows[data_canvas.id]

        self.assertFalse(edge_check.isEnabled())
        self.assertFalse(edge_check.isChecked())
        dialog._accept()
        spec = next(item for item in dialog.result_data["canvas_specs"] if item.canvas_id == data_canvas.id)
        self.assertFalse(spec.export_edges)
        dialog.deleteLater()

    def test_export_canvas_csv_dialog_uses_dark_list_colors(self) -> None:
        from gamedesigner.qt_dialogs import ExportCanvasCsvDialog
        from gamedesigner.qt_theme import palette

        project = ProjectData(name="夜间导出测试")
        project.ensure_canvas_structure()

        dialog = ExportCanvasCsvDialog(None, project, "D:/default", theme="dark")
        colors = palette("dark")
        list_widget = dialog.findChild(QWidget, "exportCanvasList")

        self.assertIsNotNone(list_widget)
        style = list_widget.styleSheet()
        self.assertIn(colors["panel"], style)
        self.assertIn(colors["panel_alt"], style)
        self.assertIn(colors["text"], style)
        dialog.deleteLater()

    def test_notes_dialog_does_not_save_initial_empty_note(self) -> None:
        from gamedesigner.ui.notes_dialog import NotesDialog

        dialog = NotesDialog(None, "画布便签", [])
        dialog._accept()

        self.assertEqual(dialog.result_notes, [])
        dialog.deleteLater()

    def test_convert_canvas_type_switches_between_normal_and_data(self) -> None:
        window = GameDesignerApp()
        project = ProjectData(name="转换测试")
        project.ensure_canvas_structure()
        canvas = project.root_canvas()
        canvas.add_node(Node(title="节点A"))
        page = window._add_page(project, None, dirty=False, canvas_data=canvas)
        window.tabs.setCurrentWidget(page)

        with mock.patch("gamedesigner.app.QMessageBox.question", return_value=QMessageBox.Yes):
            window._convert_current_canvas_type("data")
            self.assertEqual(canvas.canvas_type, "data")
            window._convert_current_canvas_type("normal")
            self.assertEqual(canvas.canvas_type, "normal")
        window.deleteLater()

    def test_sync_project_templates_updates_locked_nodes_only(self) -> None:
        window = GameDesignerApp()
        title_field = NodeField("名称", "文本", "默认名称")
        template = NodeTemplate(
            name="通用模板",
            color="#AACCEE",
            icon="模",
            title_field_id=title_field.id,
            fields=[title_field, NodeField("说明", "文本", "默认说明")],
        )
        project = ProjectData(name="模板同步测试", templates=[template])
        project.ensure_canvas_structure()
        canvas = project.root_canvas()

        locked = template.create_node(0, 0)
        locked.template_locked = True
        locked.fields[0].value = "锁定节点"
        unlocked = template.create_node(0, 0)
        unlocked.template_locked = False
        unlocked.fields[0].value = "未锁定节点"
        canvas.add_node(locked)
        canvas.add_node(unlocked)

        template.color = "#334455"
        locked.icon = "锁"
        template.icon = "新"
        template.fields[0].name = "主名称"
        template.fields.append(NodeField("新增字段", "文本", "默认新增"))

        window._sync_project_templates(project)

        self.assertEqual(locked.color, "#334455")
        self.assertEqual(locked.icon, "锁")
        self.assertEqual(locked.fields[0].name, "主名称")
        self.assertEqual(locked.fields[0].value, "锁定节点")
        self.assertEqual(len(locked.fields), 3)
        self.assertEqual(unlocked.color, "#AACCEE")
        self.assertEqual(unlocked.icon, "模")
        self.assertEqual(unlocked.fields[0].name, "名称")
        self.assertEqual(len(unlocked.fields), 2)
        window.deleteLater()

    def test_locked_template_sync_preserves_field_content_when_template_fields_are_rebuilt(self) -> None:
        window = GameDesignerApp()
        title_field = NodeField("名称", "文本", "默认名称")
        desc_field = NodeField("说明", "长文本", "默认说明")
        template = NodeTemplate(
            name="通用模板",
            title_field_id=title_field.id,
            fields=[title_field, desc_field],
        )
        project = ProjectData(name="模板内容保护", templates=[template])
        project.ensure_canvas_structure()
        canvas = project.root_canvas()
        locked = template.create_node(0, 0)
        locked.template_locked = True
        locked.fields[0].value = "节点自己的名称"
        locked.fields[1].value = "节点自己的说明"
        canvas.add_node(locked)

        rebuilt_title = NodeField("名称", "文本", "模板新名称")
        rebuilt_desc = NodeField("说明", "长文本", "模板新说明")
        template.title_field_id = rebuilt_title.id
        template.fields = [rebuilt_title, rebuilt_desc]

        window._sync_project_templates(project)

        self.assertEqual(locked.fields[0].value, "节点自己的名称")
        self.assertEqual(locked.fields[1].value, "节点自己的说明")
        self.assertEqual(locked.title, "节点自己的名称")
        self.assertEqual(locked.fields[0].id, rebuilt_title.id)
        self.assertEqual(locked.fields[1].id, rebuilt_desc.id)
        window.deleteLater()

    def test_free_canvas_locked_template_can_unlock_and_restore_template_buttons(self) -> None:
        template = NodeTemplate(name="通用模板", fields=[NodeField("名称", "文本", "条目")])
        node = template.create_node(0, 0)
        node.template_locked = True

        dialog = NodeEditorDialog(None, node, templates=[template])

        self.assertTrue(dialog.template_lock_button.isChecked())
        self.assertIsNotNone(dialog.import_template_button)
        self.assertIsNotNone(dialog.save_template_button)
        self.assertTrue(dialog.import_template_button.isHidden())
        self.assertTrue(dialog.save_template_button.isHidden())

        dialog.template_lock_button.click()

        self.assertFalse(dialog.template_lock_button.isChecked())
        self.assertFalse(dialog.import_template_button.isHidden())
        self.assertFalse(dialog.save_template_button.isHidden())
        dialog.deleteLater()

    def test_save_template_does_not_capture_node_icon(self) -> None:
        node = Node(title="Boss", icon="Boss", icon_from_title=False, fields=[NodeField("名称", "文本", "HeroBody")])
        dialog = NodeEditorDialog(None, node, templates=[])

        with mock.patch("gamedesigner.qt_dialogs.QInputDialog.getText", return_value=("敌人模板", True)):
            dialog._save_current_template()

        self.assertIsNotNone(dialog.templates)
        self.assertEqual(dialog.templates[-1].name, "敌人模板")
        self.assertEqual(dialog.templates[-1].icon, "")
        self.assertFalse(dialog.templates[-1].icon_from_title)
        dialog.deleteLater()

    def test_node_editor_field_show_label_toggle_is_saved(self) -> None:
        node = Node(title="属性", fields=[NodeField("moveSpeed", "文本", "4")])
        dialog = NodeEditorDialog(None, node, templates=[])

        self.assertFalse(dialog.fields[0].show_label)
        dialog.field_label_button.click()
        self.assertTrue(dialog.fields[0].show_label)
        dialog._accept()

        self.assertIsNotNone(dialog.result)
        self.assertTrue(dialog.result.fields[0].show_label)
        dialog.deleteLater()

    def test_node_editor_field_value_enter_accepts_shift_enter_keeps_multiline(self) -> None:
        node = Node(title="属性", fields=[NodeField("说明", "长文本", "第一行")])
        dialog = NodeEditorDialog(None, node, templates=[])
        dialog.field_value.setPlainText("第一行")
        dialog.field_value.moveCursor(QTextCursor.End)

        shift_enter = QKeyEvent(QEvent.Type.KeyPress, Qt.Key_Return, Qt.ShiftModifier)
        dialog.field_value.keyPressEvent(shift_enter)
        dialog.field_value.insertPlainText("第二行")

        self.assertIsNone(dialog.result)
        self.assertEqual(dialog.field_value.toPlainText(), "第一行\n第二行")

        enter = QKeyEvent(QEvent.Type.KeyPress, Qt.Key_Return, Qt.NoModifier)
        dialog.field_value.keyPressEvent(enter)

        self.assertEqual(dialog.result.fields[0].value, "第一行\n第二行")
        dialog.deleteLater()

    def test_node_editor_canvas_resize_sets_node_size(self) -> None:
        node = Node(title="模块", fields=[NodeField("名称", "文本", "攻击模块", x=20, y=18, width=180, height=40)])
        dialog = NodeEditorDialog(None, node, templates=[])

        dialog.canvas._on_frame_resized(640, 360)
        dialog._accept()

        self.assertIsNotNone(dialog.result)
        self.assertEqual(dialog.result.width, 640)
        self.assertEqual(dialog.result.height, 360)
        dialog.deleteLater()

    def test_node_editor_field_drag_snaps_to_other_field_edges(self) -> None:
        node = Node(
            title="模块",
            fields=[
                NodeField("左", "文本", "A", x=23, y=10, width=90, height=40),
                NodeField("右", "文本", "B", x=220, y=84, width=80, height=40),
            ],
        )
        dialog = NodeEditorDialog(None, node, templates=[])
        items = {
            item.index: item
            for item in dialog.canvas.scene_obj.items()
            if item.__class__.__name__ == "EditorFieldItem"
        }

        snapped = dialog.canvas.snap_field_position(items[1], QPointF(112, HEADER_HEIGHT + 84))

        self.assertEqual(snapped.x(), 113.0)
        self.assertTrue(any(guide.axis == "x" and guide.value == 113.0 for guide in dialog.canvas.snap_guides))
        dialog.deleteLater()

    def test_node_editor_field_resize_snaps_to_other_field_edges(self) -> None:
        node = Node(
            title="模块",
            fields=[
                NodeField("左", "文本", "A", x=17, y=10, width=120, height=40),
                NodeField("右", "文本", "B", x=40, y=84, width=80, height=40),
            ],
        )
        dialog = NodeEditorDialog(None, node, templates=[])
        items = {
            item.index: item
            for item in dialog.canvas.scene_obj.items()
            if item.__class__.__name__ == "EditorFieldItem"
        }

        width, _height = dialog.canvas.snap_field_resize(items[1], 94, 40)

        self.assertEqual(width, 97.0)
        self.assertTrue(any(guide.axis == "x" and guide.value == 137.0 for guide in dialog.canvas.snap_guides))
        dialog.deleteLater()

    def test_edit_data_canvas_node_updates_canvas_template_and_syncs_all_nodes(self) -> None:
        window = GameDesignerApp()
        title_field = NodeField("名称", "文本", "条目")
        template = NodeTemplate(
            name="数据模板",
            icon="数",
            title_field_id=title_field.id,
            fields=[title_field, NodeField("说明", "长文本", "默认说明")],
        )
        project = ProjectData(name="数据模板同步", templates=[template])
        project.ensure_canvas_structure()
        data_canvas = project.add_canvas(
            "排序画布",
            canvas_type="data",
            data_layout="horizontal",
            template_id=template.id,
            parent_canvas_id=project.root_canvas_id,
            parent_node_id="node_parent",
        )
        first = template.create_node(0, 0)
        first.fields[0].value = "第一项"
        second = template.create_node(0, 0)
        second.fields[0].value = "第二项"
        data_canvas.add_node(first)
        data_canvas.add_node(second)
        page = window._add_page(project, None, dirty=False, canvas_data=data_canvas)
        window.tabs.setCurrentWidget(page)

        updated = Node(
            id=first.id,
            title="新模板标题",
            node_type="普通",
            color="#CCE4FF",
            icon="新",
            icon_from_title=False,
            title_field_id=first.fields[0].id,
            template_id=template.id,
            template_locked=True,
            fields=[
                NodeField(
                    id=first.fields[0].id,
                    name="名称",
                    data_type="文本",
                    value="第一项",
                    x=20,
                    y=18,
                    width=340,
                    height=78,
                ),
                NodeField(
                    name="职业",
                    data_type="文本",
                    value="策划",
                    x=20,
                    y=108,
                    width=340,
                    height=78,
                ),
            ],
        )

        class _FakeDialog:
            Accepted = 1

            def __init__(self, *_args, **_kwargs) -> None:
                self.result = updated
                self.templates_changed = False
                self.templates_result = None

            def exec(self) -> int:
                return self.Accepted

        with mock.patch("gamedesigner.qt_dialogs.NodeEditorDialog", _FakeDialog):
            window._edit_node(first.id)

        synced_template = project.find_template(template.id)
        self.assertIsNotNone(synced_template)
        self.assertEqual(synced_template.icon, "数")
        self.assertEqual(synced_template.color, "#CCE4FF")
        self.assertEqual([field.name for field in synced_template.fields], ["名称", "职业"])
        self.assertTrue(all(node.template_locked for node in data_canvas.nodes))
        self.assertEqual(data_canvas.nodes[0].icon, "新")
        self.assertEqual(data_canvas.nodes[1].icon, "数")
        self.assertEqual([field.name for field in data_canvas.nodes[0].fields], ["名称", "职业"])
        self.assertEqual([field.name for field in data_canvas.nodes[1].fields], ["名称", "职业"])
        self.assertEqual(data_canvas.nodes[0].fields[0].value, "第一项")
        self.assertEqual(data_canvas.nodes[1].fields[0].value, "第二项")
        window.deleteLater()

    def test_data_canvas_template_sync_preserves_other_node_content_when_field_ids_change(self) -> None:
        window = GameDesignerApp()
        title_field = NodeField("名称", "文本", "条目")
        desc_field = NodeField("说明", "长文本", "默认说明")
        template = NodeTemplate(
            name="数据模板",
            title_field_id=title_field.id,
            fields=[title_field, desc_field],
        )
        project = ProjectData(name="数据内容保护", templates=[template])
        project.ensure_canvas_structure()
        data_canvas = project.add_canvas("排序画布", canvas_type="data", template_id=template.id)
        first = template.create_node(0, 0)
        first.fields[0].value = "第一项"
        first.fields[1].value = "第一自己的说明"
        second = template.create_node(0, 0)
        second.fields[0].value = "第二项"
        second.fields[1].value = "第二自己的说明"
        data_canvas.add_node(first)
        data_canvas.add_node(second)
        page = window._add_page(project, None, dirty=False, canvas_data=data_canvas)
        window.tabs.setCurrentWidget(page)

        rebuilt_title = NodeField("名称", "文本", "第一项")
        rebuilt_desc = NodeField("说明", "长文本", "第一改过的说明")
        updated = Node(
            id=first.id,
            title="数据模板",
            node_type="普通",
            color=template.color,
            title_field_id=rebuilt_title.id,
            template_id=template.id,
            template_locked=True,
            fields=[rebuilt_title, rebuilt_desc],
        )

        class _FakeDialog:
            Accepted = 1

            def __init__(self, *_args, **_kwargs) -> None:
                self.result = updated
                self.templates_changed = False
                self.templates_result = None

            def exec(self) -> int:
                return self.Accepted

        with mock.patch("gamedesigner.qt_dialogs.NodeEditorDialog", _FakeDialog):
            window._edit_node(first.id)

        self.assertEqual(data_canvas.nodes[0].fields[1].value, "第一改过的说明")
        self.assertEqual(data_canvas.nodes[1].fields[0].value, "第二项")
        self.assertEqual(data_canvas.nodes[1].fields[1].value, "第二自己的说明")
        self.assertEqual(data_canvas.nodes[1].fields[0].id, rebuilt_title.id)
        self.assertEqual(data_canvas.nodes[1].fields[1].id, rebuilt_desc.id)
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

    def test_reopen_same_project_path_reloads_from_disk(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            project_path = Path(folder) / "ReloadProject.gdc"
            project = ProjectData(name="重载测试")
            project.ensure_canvas_structure()
            project.root_canvas().add_node(Node(title="旧标题"))
            save_project(project, project_path)

            window = GameDesignerApp()
            window._open_project_path(project_path)
            page = window._current_page()
            self.assertIsNotNone(page)
            self.assertEqual(page.project.root_canvas().nodes[0].title, "旧标题")

            disk_project = load_project(project_path)
            disk_project.root_canvas().nodes[0].title = "磁盘新标题"
            save_project(disk_project, project_path)

            window._open_project_path(project_path)
            reloaded_page = window._current_page()
            self.assertIsNotNone(reloaded_page)
            self.assertEqual(reloaded_page.project.root_canvas().nodes[0].title, "磁盘新标题")
            window.deleteLater()

    def test_existing_link_document_is_not_renamed_when_reopened(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            tmp_path = Path(folder)
            project_path = tmp_path / "LinkProject.gdc"
            project = ProjectData(
                name="超文本测试",
                source_dir=str(tmp_path / "source"),
                output_dir=str(tmp_path / "out"),
            )
            project.ensure_canvas_structure()
            link_node = project.root_canvas().add_node(
                Node(
                    title="linked_docs/新文档.md",
                    node_type="超文本",
                    link_path="linked_docs/新文档.md",
                    link_format="md",
                    fields=[NodeField("文件", "资源路径", "linked_docs/新文档.md")],
                )
            )
            save_project(project, project_path)
            linked_dir = project_path.parent / f"{project_path.name}.files" / "linked_docs"
            linked_dir.mkdir(parents=True, exist_ok=True)
            original = linked_dir / "新文档.md"
            original.write_text("# 新文档\n", encoding="utf-8")

            window = GameDesignerApp()
            window._open_project_path(project_path)
            page = window._current_page()
            self.assertIsNotNone(page)
            node = page.project.root_canvas().find_node(link_node.id)
            self.assertIsNotNone(node)

            window._ensure_link_node_file(page, node)

            self.assertTrue(original.exists())
            self.assertFalse((linked_dir / "linked_docs_新文档.md.md").exists())
            self.assertEqual(node.link_path, "linked_docs/新文档.md")
            self.assertEqual(node.title, "新文档")
            window.deleteLater()

    def test_add_link_node_does_not_create_file_until_project_save(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            tmp_path = Path(folder)
            window = GameDesignerApp()
            project = ProjectData(
                name="延迟创建测试",
                source_dir=str(tmp_path / "source"),
                output_dir=str(tmp_path / "out"),
            )
            project.ensure_canvas_structure()
            page = window._add_page(project, tmp_path / "DelayedCreate.gdc", dirty=False, canvas_data=project.root_canvas())
            window.tabs.setCurrentWidget(page)

            with mock.patch.object(window, "_open_link_document", autospec=True, return_value=None):
                window._add_link_node_at(200, 160)

            bundle_link_dir = tmp_path / "DelayedCreate.gdc.files" / "linked_docs"
            self.assertFalse(bundle_link_dir.exists())

            node = project.root_canvas().nodes[-1]
            self.assertEqual(node.node_type, "超文本")
            self.assertEqual(node.link_path, "")

            self.assertTrue(window._save_project(page))
            self.assertTrue(bundle_link_dir.exists())
            self.assertTrue((bundle_link_dir / "新文档.md").exists())
            window.deleteLater()

    def test_link_document_dialog_markdown_preview_and_delayed_create(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            project_path = Path(folder) / "PreviewProject.gdc"
            dialog = LinkDocumentDialog(None, project_path, "", "预览文档", "md")

            self.assertEqual(dialog.path_label.text(), "未创建文档，保存后生成工程内文件")
            dialog.editor.setPlainText("# 标题\n\n**加粗**")
            self.app.processEvents()
            self.assertIn("标题", dialog.preview.toPlainText())
            self.assertIn("加粗", dialog.preview.toPlainText())

            dialog._save()
            self.assertTrue(dialog.saved)
            self.assertEqual(dialog.relative_path, "linked_docs/预览文档.md")
            self.assertTrue((project_path.parent / f"{project_path.name}.files" / "linked_docs" / "预览文档.md").exists())
            dialog.deleteLater()

    def test_link_document_editor_enter_saves_shift_enter_inserts_newline(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            project_path = Path(folder) / "EnterSaveProject.gdc"
            dialog = LinkDocumentDialog(None, project_path, "", "回车保存", "md")
            dialog.editor.setPlainText("# 回车保存")
            dialog.editor.moveCursor(QTextCursor.End)

            shift_enter = QKeyEvent(QEvent.Type.KeyPress, Qt.Key_Return, Qt.ShiftModifier)
            dialog.editor.keyPressEvent(shift_enter)
            dialog.editor.insertPlainText("第二行")

            self.assertFalse(dialog.saved)
            self.assertEqual(dialog.editor.toPlainText(), "# 回车保存\n第二行")

            enter = QKeyEvent(QEvent.Type.KeyPress, Qt.Key_Return, Qt.NoModifier)
            dialog.editor.keyPressEvent(enter)

            self.assertTrue(dialog.saved)
            path = project_path.parent / f"{project_path.name}.files" / "linked_docs" / "回车保存.md"
            self.assertEqual(path.read_text(encoding="utf-8"), "# 回车保存\n第二行")
            dialog.deleteLater()

    def test_node_editor_dialog_restores_project_window_layout(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            project_path = Path(folder) / "EditorLayout.gdc"
            save_project_window_layouts(
                project_path,
                {
                    "node_editor_dialog": {
                        "x": 120,
                        "y": 140,
                        "width": 1220,
                        "height": 820,
                    },
                },
            )
            node = Node(title="编辑节点", fields=[NodeField("内容", "长文本", "测试")])
            dialog = NodeEditorDialog(None, node, project_path=project_path)
            self.assertEqual(dialog.width(), 1220)
            self.assertEqual(dialog.height(), 820)

            dialog.resize(1280, 860)
            dialog.done(0)

            saved_layouts = load_project_window_layouts(project_path)
            self.assertEqual(saved_layouts["node_editor_dialog"]["width"], 1280.0)
            self.assertEqual(saved_layouts["node_editor_dialog"]["height"], 860.0)
            dialog.deleteLater()

    def test_link_document_dialog_restores_saved_window_layout(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            tmp_path = Path(folder)
            project_path = tmp_path / "LayoutProject.gdc"
            save_project_window_layouts(
                project_path,
                {
                    "link_document_dialog": {
                        "x": 80,
                        "y": 96,
                        "width": 980,
                        "height": 640,
                    },
                },
            )
            settings = AppSettings(
                workspace_dir=folder,
                export_dir=str(tmp_path / "exports"),
                window_layouts={
                    "link_document_dialog": {"x": 11, "y": 22, "width": 760, "height": 480},
                },
            )
            parent = QWidget()
            parent.settings = settings  # type: ignore[attr-defined]
            with mock.patch.dict(os.environ, {"APPDATA": folder}):
                dialog = LinkDocumentDialog(parent, project_path, "", "布局文档", "md")
                self.assertEqual(dialog.width(), 980)
                self.assertEqual(dialog.height(), 640)

                dialog.resize(1040, 720)
                dialog.done(0)

                saved_layouts = load_project_window_layouts(project_path)
                self.assertEqual(saved_layouts["link_document_dialog"]["width"], 1040.0)
                self.assertEqual(saved_layouts["link_document_dialog"]["height"], 720.0)
                self.assertEqual(settings.window_layouts["link_document_dialog"]["width"], 760)

                reopened = LinkDocumentDialog(parent, project_path, "", "布局文档", "md")
                self.assertEqual(reopened.width(), 1040)
                self.assertEqual(reopened.height(), 720)
                reopened.deleteLater()
            dialog.deleteLater()
            parent.deleteLater()

    def test_main_window_restores_saved_window_layout(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            tmp_path = Path(folder)
            settings = AppSettings(
                workspace_dir=folder,
                export_dir=str(tmp_path / "exports"),
                window_layouts={
                    "main_window": {"x": 64, "y": 72, "width": 1480, "height": 920},
                },
            )
            with mock.patch.dict(os.environ, {"APPDATA": folder}):
                save_settings(settings)

                window = GameDesignerApp()
                self.assertEqual(window.width(), 1480)
                self.assertEqual(window.height(), 920)

                window.resize(1520, 960)
                window.close()

                reopened = GameDesignerApp()
                self.assertEqual(reopened.width(), 1520)
                self.assertEqual(reopened.height(), 960)
                reopened.deleteLater()


if __name__ == "__main__":
    unittest.main()
