import shutil
import unittest
from pathlib import Path

from gamedesigner.ai_tools import (
    AiChatMessage,
    build_ai_cli_invocation,
    build_project_chat_context,
    build_project_chat_prompt,
    invocation_with_last_message_output,
    load_project_chat_history,
    project_chat_history_path,
    qprocess_command,
    save_project_chat_history,
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
