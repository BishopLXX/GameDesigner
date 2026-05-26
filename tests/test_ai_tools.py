import os
import shutil
import unittest
from pathlib import Path
from unittest import mock

from gamedesigner.image_ai import AiImageError, build_ai_image_request
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
    load_project_chat_memory,
    parse_ai_canvas_actions,
    project_chat_history_path,
    qprocess_command,
    save_project_chat_history,
    split_ai_canvas_action_response,
)
from gamedesigner.ai_canvas_tools import (
    AI_CANVAS_TOOL_NAMES,
    ai_canvas_tool_protocol_text,
)
from gamedesigner.ai_presets import (
    AI_CUSTOM_API_PROFILE_KEY,
    AI_FREE_MODEL_PRESETS,
    ai_profile_key_for_snapshot,
    clean_ai_saved_connections,
)
from gamedesigner.models import BlueprintGroup, CanvasData, DesignNote, Edge, Node, NodeField, ProjectData
from gamedesigner.storage import AppSettings


class AiToolsTests(unittest.TestCase):
    def test_ai_image_request_uses_official_base_url_and_model_settings(self) -> None:
        settings = AppSettings(
            ai_image_provider="openai",
            ai_image_model="gpt-image-1.5",
            ai_image_api_key="secret",
            ai_image_base_url="https://ignored.example/v1",
            ai_image_size="1024x1024",
            ai_image_quality="high",
            ai_image_background="transparent",
            ai_image_count=2,
            ai_image_output_format="webp",
        )

        request = build_ai_image_request(settings, "slime icon", [Path("D:/ref.png")])

        self.assertEqual(request.base_url, "https://api.openai.com/v1")
        self.assertEqual(request.model, "gpt-image-1.5")
        self.assertEqual(request.size, "1024x1024")
        self.assertEqual(request.quality, "high")
        self.assertEqual(request.background, "transparent")
        self.assertEqual(request.count, 2)
        self.assertEqual(request.output_format, "webp")
        self.assertEqual(request.reference_paths, [Path("D:/ref.png")])

    def test_ai_image_request_uses_compatible_base_url(self) -> None:
        settings = AppSettings(
            ai_image_provider="compatible",
            ai_image_model="custom-image-model",
            ai_image_api_key="secret",
            ai_image_base_url="https://images.example.test/v1/",
        )

        request = build_ai_image_request(settings, "asset")

        self.assertEqual(request.base_url, "https://images.example.test/v1")
        self.assertEqual(request.model, "custom-image-model")

    def test_ai_image_request_adds_v1_to_compatible_root_base_url(self) -> None:
        settings = AppSettings(
            ai_image_provider="compatible",
            ai_image_model="gpt-image-2",
            ai_image_api_key="secret",
            ai_image_base_url="https://www.packyapi.com",
        )

        request = build_ai_image_request(settings, "asset")

        self.assertEqual(request.base_url, "https://www.packyapi.com/v1")

    def test_ai_image_request_requires_api_key(self) -> None:
        settings = AppSettings(ai_image_api_key="")

        with mock.patch.dict(os.environ, {"OPENAI_API_KEY": ""}):
            with self.assertRaises(AiImageError):
                build_ai_image_request(settings, "asset")

    def test_app_settings_roundtrip_ai_image_settings(self) -> None:
        settings = AppSettings(
            ai_image_provider="compatible",
            ai_image_model="gpt-image-2",
            ai_image_api_key="image-key",
            ai_image_base_url="https://images.example.test/v1",
            ai_image_size="1536x1024",
            ai_image_quality="medium",
            ai_image_background="opaque",
            ai_image_count=3,
            ai_image_output_format="jpeg",
        )

        loaded = AppSettings.from_dict(settings.to_dict())

        self.assertEqual(loaded.ai_image_provider, "compatible")
        self.assertEqual(loaded.ai_image_model, "gpt-image-2")
        self.assertEqual(loaded.ai_image_api_key, "image-key")
        self.assertEqual(loaded.ai_image_base_url, "https://images.example.test/v1")
        self.assertEqual(loaded.ai_image_size, "1536x1024")
        self.assertEqual(loaded.ai_image_quality, "medium")
        self.assertEqual(loaded.ai_image_background, "opaque")
        self.assertEqual(loaded.ai_image_count, 3)
        self.assertEqual(loaded.ai_image_output_format, "jpeg")

    def test_codex_invocation_uses_model_cwd_and_stdin_prompt(self) -> None:
        settings = AppSettings(ai_provider="codex", ai_model="gpt-5.5")

        invocation = build_ai_cli_invocation(settings, "hello", Path("D:/GameDesigner"))

        self.assertEqual(invocation.program, "codex")
        self.assertIn("exec", invocation.arguments)
        self.assertIn("gpt-5.5", invocation.arguments)
        self.assertEqual(invocation.stdin, "hello")
        self.assertEqual(invocation.cwd, Path("D:/GameDesigner"))

    def test_codex_invocation_includes_reasoning_effort_config(self) -> None:
        settings = AppSettings(ai_provider="codex", ai_model="gpt-5.5", ai_reasoning_effort="medium")

        invocation = build_ai_cli_invocation(settings, "hello", Path("D:/GameDesigner"))

        self.assertIn("-c", invocation.arguments)
        config_index = invocation.arguments.index("-c")
        self.assertEqual(invocation.arguments[config_index + 1], 'model_reasoning_effort="medium"')

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

    def test_free_ollama_preset_uses_openai_compatible_environment(self) -> None:
        preset = next(item for item in AI_FREE_MODEL_PRESETS if item.key == "free_ollama_gpt_oss_20b")
        snapshot = preset.to_snapshot()
        settings = AppSettings(
            ai_provider=snapshot["ai_provider"],
            ai_model=snapshot["ai_model"],
            ai_auth_mode=snapshot["ai_auth_mode"],
            ai_api_key=snapshot["ai_api_key"],
            ai_base_url=snapshot["ai_base_url"],
        )

        invocation = build_ai_cli_invocation(settings, "hello", Path("D:/GameDesigner"))

        self.assertEqual(invocation.program, "codex")
        self.assertIn("gpt-oss:20b", invocation.arguments)
        self.assertEqual(invocation.environment["OPENAI_API_KEY"], "ollama")
        self.assertEqual(invocation.environment["OPENAI_BASE_URL"], "http://localhost:11434/v1")

    def test_ai_saved_connections_are_sanitized(self) -> None:
        cleaned = clean_ai_saved_connections(
            {
                "api_key": {
                    "ai_provider": "bad",
                    "ai_model": "model",
                    "ai_auth_mode": "api_key",
                    "ai_api_key": "secret",
                    "ai_base_url": "https://example.test/v1",
                },
                "": {"ai_provider": "codex"},
                "official": "invalid",
            }
        )

        self.assertEqual(cleaned["api_key"]["ai_provider"], "codex")
        self.assertEqual(cleaned["api_key"]["ai_auth_mode"], "api_key")
        self.assertNotIn("", cleaned)
        self.assertNotIn("official", cleaned)

    def test_own_api_key_matching_free_provider_is_stored_as_custom_api(self) -> None:
        key = ai_profile_key_for_snapshot(
            {
                "ai_provider": "codex",
                "ai_model": "openrouter/free",
                "ai_auth_mode": "api_key",
                "ai_api_key": "user-openrouter-key",
                "ai_base_url": "https://openrouter.ai/api/v1",
            }
        )

        self.assertEqual(key, AI_CUSTOM_API_PROFILE_KEY)

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
        self.assertIn("update_canvas_rules", prompt)
        self.assertIn("当前画布规则记忆是高权重上下文", prompt)
        self.assertIn("自动应用到当前画布", prompt)
        self.assertIn("子节点放到父节点右侧", prompt)
        self.assertIn("文案设计", prompt)
        self.assertIn("迭代设计", prompt)
        self.assertIn("迭代必须基于这些文案和现有内容", prompt)
        self.assertIn("Label 节点结构", prompt)
        self.assertIn("只保留一个长文本描述卡片", prompt)
        self.assertIn("迭代黄色史莱姆", prompt)
        self.assertIn("reference_node_id", prompt)
        self.assertIn("字段、尺寸、视觉卡片布局", prompt)

    def test_ai_canvas_tool_protocol_lists_standard_internal_tools(self) -> None:
        protocol = ai_canvas_tool_protocol_text()

        for name in (
            "create_node",
            "update_node",
            "create_edge",
            "update_edge_label",
            "query_canvas",
            "search_nodes",
            "validate_actions",
        ):
            self.assertIn(name, AI_CANVAS_TOOL_NAMES)
            self.assertIn(name, protocol)
        self.assertIn("受控工具调用", protocol)

    def test_parse_ai_canvas_actions_supports_create_update_group_and_canvas_rules(self) -> None:
        actions = parse_ai_canvas_actions(
            """
            {
              "actions": [
                {
                  "type": "update_canvas_rules",
                  "rules": "- 本画布只生成 Boss 设计节点\\n- 输出必须包含机制弱点"
                },
                {
                  "type": "create_node",
                  "title": "冲刺技能",
                  "icon": "冲",
                  "template_id": "template_skill",
                  "reference_node_id": "node_reference",
                  "fields": [
                    {"name": "内容信息", "data_type": "长文本", "value": "向前突进"}
                  ]
                },
                {
                  "type": "create_group",
                  "title": "冲刺流派",
                  "reference_group_id": "group_reference",
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

        self.assertEqual(
            [action.type for action in actions],
            ["update_canvas_rules", "create_node", "create_group", "update_node"],
        )
        self.assertIn("Boss 设计节点", actions[0].rules)
        self.assertEqual(actions[1].title, "冲刺技能")
        self.assertEqual(actions[1].template_id, "template_skill")
        self.assertEqual(actions[1].reference_node_id, "node_reference")
        self.assertEqual(actions[1].fields[0], AiCanvasFieldChange("内容信息", "长文本", "向前突进"))
        self.assertEqual(actions[2].title, "冲刺流派")
        self.assertEqual(actions[2].reference_group_id, "group_reference")
        self.assertEqual(actions[2].nodes[0].title, "冲刺强化")
        self.assertEqual(actions[3].node_id, "node_a")

    def test_ai_assistant_prompt_requires_blueprint_group_structure_cloning(self) -> None:
        prompt = build_ai_assistant_prompt("当前画布: 主画布", "参考蓝图组继续迭代")

        self.assertIn("结构蓝图来克隆", prompt)
        self.assertIn("边界尺寸", prompt)
        self.assertIn("内部连接拓扑", prompt)
        self.assertIn("reference_group_id", prompt)

    def test_parse_ai_canvas_actions_supports_tool_calls_shape(self) -> None:
        actions = parse_ai_canvas_actions(
            """
            {
              "tool_calls": [
                {
                  "function": {
                    "name": "create_edge",
                    "arguments": "{\\"source_node_id\\": \\"node_a\\", \\"target_node_id\\": \\"node_b\\", \\"label\\": \\"解锁\\"}"
                  }
                },
                {
                  "name": "search_nodes",
                  "arguments": {
                    "query": "史莱姆",
                    "limit": 5
                  }
                }
              ]
            }
            """
        )

        self.assertEqual([action.type for action in actions], ["create_edge", "search_nodes"])
        self.assertEqual(actions[0].source_node_id, "node_a")
        self.assertEqual(actions[0].target_node_id, "node_b")
        self.assertEqual(actions[0].label, "解锁")
        self.assertEqual(actions[1].query, "史莱姆")
        self.assertEqual(actions[1].limit, 5)

    def test_project_chat_context_includes_canvas_rules_as_high_priority_memory(self) -> None:
        canvas = CanvasData(
            id="canvas_rules",
            name="Boss画布",
            ai_rules="- 本画布新增节点必须延续几何 Boss Rush 风格\n- 每个 Boss 必须有清晰弱点",
            nodes=[Node(title="基准Boss")],
        )
        project = ProjectData(name="规则测试", root_canvas_id=canvas.id, canvases=[canvas])

        context = build_project_chat_context(project, canvas, "D:/GameDesigner/demo.gdc")

        self.assertIn("当前画布规则记忆（高权重", context)
        self.assertIn("几何 Boss Rush 风格", context)
        self.assertIn("每个 Boss 必须有清晰弱点", context)

    def test_project_chat_context_includes_node_size_and_visual_layout(self) -> None:
        field = NodeField("掉落", "长文本", "100%绿色粘液", x=10, y=70, width=280, height=82)
        canvas = CanvasData(
            id="canvas_visual",
            name="怪物画布",
            nodes=[Node(title="绿色史莱姆", x=100, y=120, width=320, height=180, fields=[field])],
        )
        project = ProjectData(name="布局上下文测试", root_canvas_id=canvas.id, canvases=[canvas])

        context = build_project_chat_context(project, canvas)

        self.assertIn("绿色史莱姆", context)
        self.assertIn("尺寸 (320x180)", context)
        self.assertIn("布局(10,70,280x82)", context)

    def test_project_chat_context_includes_canvas_and_node_notes(self) -> None:
        node = Node(
            id="node_unlock",
            title="解锁节点",
            notes=[DesignNote(title="节点参考", content="这个节点应当只负责开放新玩法。")],
        )
        canvas = CanvasData(
            id="canvas_notes",
            name="科技树",
            notes=[DesignNote(title="布局规则", content="科技树上面大部分是解锁，下面大部分是养成。")],
            nodes=[node],
        )
        project = ProjectData(name="便签测试", root_canvas_id=canvas.id, canvases=[canvas])

        context = build_project_chat_context(project, canvas, "D:/GameDesigner/demo.gdc", selected_node_ids={node.id})

        self.assertIn("当前画布便签（高权重", context)
        self.assertIn("科技树上面大部分是解锁", context)
        self.assertIn("节点便签（绑定到具体节点", context)
        self.assertIn("这个节点应当只负责开放新玩法", context)

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

    def test_project_chat_memory_preserves_full_history_while_short_history_limits_visible_slice(self) -> None:
        project_path = Path(self._testMethodName) / "MemoryProject.gdc"
        messages = [AiChatMessage("user" if index % 2 == 0 else "assistant", f"消息{index}") for index in range(30)]
        try:
            save_project_chat_history(project_path, messages)

            loaded_memory = load_project_chat_memory(project_path)
            loaded_history = load_project_chat_history(project_path)

            self.assertEqual(len(loaded_memory), 30)
            self.assertEqual(len(loaded_history), 24)
            self.assertEqual(loaded_memory[0].content, "消息0")
            self.assertEqual(loaded_memory[-1].content, "消息29")
            self.assertEqual(loaded_history[0].content, "消息6")
            self.assertEqual(loaded_history[-1].content, "消息29")
        finally:
            shutil.rmtree(project_path.parent, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
