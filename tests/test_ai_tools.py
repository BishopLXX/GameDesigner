import shutil
import unittest
from pathlib import Path

from gamedesigner.ai_tools import (
    AI_ACTION_BLOCK_END,
    AI_ACTION_BLOCK_START,
    AiCanvasFieldChange,
    AiChatMessage,
    build_ai_cli_invocation,
    build_ai_assistant_prompt,
    build_project_chat_context,
    build_project_chat_prompt,
    invocation_with_last_message_output,
    load_project_chat_history,
    parse_ai_canvas_actions,
    project_chat_history_path,
    qprocess_command,
    save_project_chat_history,
    split_ai_canvas_action_response,
)
from gamedesigner.models import BlueprintGroup, CanvasData, Edge, Node, NodeField, ProjectData
from gamedesigner.storage import AppSettings


class AiToolsTests(unittest.TestCase):
    def test_codex_invocation_uses_model_cwd_and_stdin_prompt(self) -> None:
        settings = AppSettings(ai_provider="codex", ai_model="gpt-5.5")

        invocation = build_ai_cli_invocation(settings, "hello", Path("D:/GameDesigner"))

        self.assertEqual(invocation.program, "codex")
        self.assertIn("exec", invocation.arguments)
        self.assertIn("gpt-5.5", invocation.arguments)
        self.assertEqual(invocation.stdin, "hello")
        self.assertEqual(invocation.cwd, Path("D:/GameDesigner"))

    def test_claude_invocation_can_pass_api_key_environment(self) -> None:
        settings = AppSettings(
            ai_provider="claude",
            ai_model="opus",
            ai_auth_mode="api_key",
            ai_api_key="secret",
            ai_base_url="https://example.test",
        )

        invocation = build_ai_cli_invocation(settings, "hello", Path("D:/GameDesigner"))

        self.assertEqual(invocation.program, "claude")
        self.assertEqual(invocation.arguments[:3], ["--print", "--model", "opus"])
        self.assertEqual(invocation.environment["ANTHROPIC_API_KEY"], "secret")
        self.assertEqual(invocation.environment["ANTHROPIC_BASE_URL"], "https://example.test")

    def test_qprocess_command_wraps_windows_cli_with_cmd(self) -> None:
        settings = AppSettings(ai_provider="codex", ai_model="gpt-5.5")
        invocation = build_ai_cli_invocation(settings, "hello", Path("D:/GameDesigner"))

        program, arguments = qprocess_command(invocation, "win32")

        self.assertEqual(program, "cmd.exe")
        self.assertEqual(arguments[:3], ["/d", "/s", "/c"])
        self.assertIn("codex", arguments[3])
        self.assertIn("gpt-5.5", arguments[3])

    def test_qprocess_command_keeps_native_program_outside_windows(self) -> None:
        settings = AppSettings(ai_provider="codex", ai_model="gpt-5.5")
        invocation = build_ai_cli_invocation(settings, "hello", Path("D:/GameDesigner"))

        program, arguments = qprocess_command(invocation, "linux")

        self.assertEqual(program, "codex")
        self.assertEqual(arguments, invocation.arguments)

    def test_codex_invocation_can_write_last_message_to_file(self) -> None:
        settings = AppSettings(ai_provider="codex", ai_model="gpt-5.5")
        invocation = build_ai_cli_invocation(settings, "hello", Path("D:/GameDesigner"))

        updated = invocation_with_last_message_output(invocation, Path("D:/tmp/answer.md"))

        self.assertEqual(updated.program, "codex")
        self.assertIn("--output-last-message", updated.arguments)
        output_index = updated.arguments.index("--output-last-message")
        self.assertEqual(updated.arguments[output_index + 1], "D:\\tmp\\answer.md")
        self.assertEqual(updated.arguments[-1], "-")
        self.assertLess(output_index, len(updated.arguments) - 1)

    def test_project_chat_context_includes_current_canvas_and_selection(self) -> None:
        selected = Node(
            id="node_a",
            title="攻击模块",
            icon="攻",
            x=12,
            y=34,
            fields=[NodeField("攻击", "整数", "5")],
        )
        other = Node(id="node_b", title="防御模块")
        group = BlueprintGroup(id="group_a", title="战斗组")
        selected.group_id = group.id
        canvas = CanvasData(
            id="canvas_a",
            name="科技树",
            nodes=[selected, other],
            groups=[group],
            edges=[Edge(id="edge_a", source=selected.id, target=other.id, label="解锁")],
        )
        project = ProjectData(name="方块崩演", root_canvas_id=canvas.id, canvases=[canvas])

        context = build_project_chat_context(
            project,
            canvas,
            "D:/GameDesigner/demo.gdc",
            selected_node_ids={selected.id},
            selected_group_ids={group.id},
            selected_edge_id="edge_a",
        )

        self.assertIn("项目名称: 方块崩演", context)
        self.assertIn("当前画布: 科技树", context)
        self.assertIn("节点: 攻击模块", context)
        self.assertIn("蓝图组: 战斗组", context)
        self.assertIn("攻击模块 -> 防御模块", context)

    def test_project_chat_prompt_wraps_context_and_question(self) -> None:
        prompt = build_project_chat_prompt(
            "项目名称: 测试",
            "怎么优化？",
            [AiChatMessage("user", "上一问"), AiChatMessage("assistant", "上一答")],
        )

        self.assertIn("当前工程上下文", prompt)
        self.assertIn("项目名称: 测试", prompt)
        self.assertIn("上一问", prompt)
        self.assertIn("上一答", prompt)
        self.assertIn("怎么优化？", prompt)

    def test_ai_assistant_prompt_includes_canvas_action_protocol(self) -> None:
        prompt = build_ai_assistant_prompt("当前画布: 主画布", "帮我创建节点")

        self.assertIn("当前画布与当前选中对象最高", prompt)
        self.assertIn(AI_ACTION_BLOCK_START, prompt)
        self.assertIn("create_node", prompt)
        self.assertIn("update_node", prompt)
        self.assertIn("create_group", prompt)
        self.assertIn("自动应用到当前画布", prompt)

    def test_parse_ai_canvas_actions_supports_create_update_and_group(self) -> None:
        actions = parse_ai_canvas_actions(
            """
            {
              "actions": [
                {
                  "type": "create_node",
                  "title": "冲刺技能",
                  "icon": "冲",
                  "template_id": "template_skill",
                  "fields": [
                    {"name": "内容信息", "data_type": "长文本", "value": "向前突进"}
                  ]
                },
                {
                  "type": "create_group",
                  "title": "冲刺流派",
                  "nodes": [
                    {
                      "type": "create_node",
                      "title": "冲刺强化",
                      "fields": [
                        {"name": "内容信息", "data_type": "长文本", "value": "冲刺后增伤"}
                      ]
                    }
                  ]
                },
                {
                  "type": "update_node",
                  "node_id": "node_a",
                  "title": "基础攻击",
                  "fields": [
                    {"name": "伤害", "data_type": "数字", "value": "12"}
                  ]
                }
              ]
            }
            """
        )

        self.assertEqual([action.type for action in actions], ["create_node", "create_group", "update_node"])
        self.assertEqual(actions[0].title, "冲刺技能")
        self.assertEqual(actions[0].template_id, "template_skill")
        self.assertEqual(actions[0].fields[0], AiCanvasFieldChange("内容信息", "长文本", "向前突进"))
        self.assertEqual(actions[1].title, "冲刺流派")
        self.assertEqual(actions[1].nodes[0].title, "冲刺强化")
        self.assertEqual(actions[2].node_id, "node_a")

    def test_split_ai_canvas_action_response_hides_action_block(self) -> None:
        visible, actions, error = split_ai_canvas_action_response(
            "我建议先补一个节点。\n"
            f"{AI_ACTION_BLOCK_START}\n"
            '{"actions":[{"type":"create_node","title":"Boss一阶段"}]}\n'
            f"{AI_ACTION_BLOCK_END}"
        )

        self.assertEqual(error, "")
        self.assertEqual(visible, "我建议先补一个节点。")
        self.assertEqual(len(actions), 1)
        self.assertEqual(actions[0].title, "Boss一阶段")

    def test_project_chat_history_roundtrip_uses_project_bundle(self) -> None:
        project_path = Path(self._testMethodName) / "MemoryProject.gdc"
        messages = [AiChatMessage("user", "你好"), AiChatMessage("assistant", "在")]
        try:
            save_project_chat_history(project_path, messages)

            self.assertTrue(project_chat_history_path(project_path).exists())
            loaded = load_project_chat_history(project_path)
            self.assertEqual([message.role for message in loaded], ["user", "assistant"])
            self.assertEqual([message.content for message in loaded], ["你好", "在"])
        finally:
            shutil.rmtree(project_path.parent, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
