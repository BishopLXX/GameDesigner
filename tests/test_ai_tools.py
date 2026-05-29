import base64
from io import BytesIO
import os
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from PIL import Image
from PySide6.QtGui import QImage

from gamedesigner.image_ai import (
    AiGeneratedImage,
    AiImageError,
    ai_image_cache_dir,
    build_ai_image_request,
    cache_generated_ai_image,
    generate_pixel_refiner_candidates,
    generate_pixel_downscale_candidates,
    load_cached_ai_images,
    pixel_source_path_for_candidate,
    _encode_multipart,
    _multipart_body,
)
from gamedesigner.pixel_refiner import (
    DEFAULT_PIXEL_REFINER_CANDIDATES,
    DEFAULT_PIXEL_REFINER_MODEL_ID,
    DEFAULT_PIXEL_REFINER_SERVICE_URL,
    DEFAULT_PIXEL_REFINER_STRENGTH,
    PixelRefinerOutput,
    PixelRefinerRequest,
    PixelRefinerResult,
    normalize_pixel_refiner_service_url,
    refine_pixel_art_with_service,
)
from gamedesigner.image_rendering import is_pixel_art_image_path
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
    resolve_ai_cli_program,
    qprocess_command,
    portable_ai_runtime_environment,
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
from gamedesigner.storage import AppSettings, load_settings, save_settings, settings_backup_path, settings_path


PNG_1X1 = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAACXBIWXMAAA9hAAAPYQGoP6dp"
    "AAAADUlEQVQImWP4z8DwHwAFAAH/q842iQAAAABJRU5ErkJggg=="
)


class AiToolsTests(unittest.TestCase):
    def setUp(self) -> None:
        self._appdata_temp = tempfile.TemporaryDirectory()
        self._appdata_patch = mock.patch.dict(
            os.environ,
            {
                "APPDATA": self._appdata_temp.name,
                "GAMEDESIGNER_DATA_DIR": str(Path(self._appdata_temp.name) / "GameDesignerData"),
            },
        )
        self._appdata_patch.start()

    def tearDown(self) -> None:
        self._appdata_patch.stop()
        self._appdata_temp.cleanup()

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

    def test_ai_image_request_allows_small_gpt_image_2_size(self) -> None:
        settings = AppSettings(
            ai_image_provider="openai",
            ai_image_model="gpt-image-2",
            ai_image_api_key="secret",
            ai_image_size="816x816",
        )

        request = build_ai_image_request(settings, "pixel character")

        self.assertEqual(request.size, "816x816")

    def test_ai_image_request_keeps_custom_size_for_compatible_provider(self) -> None:
        settings = AppSettings(
            ai_image_provider="compatible",
            ai_image_model="custom-image-model",
            ai_image_api_key="secret",
            ai_image_base_url="https://images.example.test/v1",
            ai_image_size="816x816",
        )

        request = build_ai_image_request(settings, "pixel character")

        self.assertEqual(request.size, "816x816")

    def test_ai_image_request_drops_unsupported_small_legacy_size(self) -> None:
        settings = AppSettings(
            ai_image_provider="openai",
            ai_image_model="gpt-image-1.5",
            ai_image_api_key="secret",
            ai_image_size="816x816",
        )

        request = build_ai_image_request(settings, "pixel character")

        self.assertEqual(request.size, "auto")

    def test_ai_image_request_drops_tiny_gpt_image_2_size(self) -> None:
        settings = AppSettings(
            ai_image_provider="openai",
            ai_image_model="gpt-image-2",
            ai_image_api_key="secret",
            ai_image_size="256x256",
        )

        request = build_ai_image_request(settings, "pixel character")

        self.assertEqual(request.size, "auto")

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
            ai_image_size="816x816",
            ai_image_quality="medium",
            ai_image_background="opaque",
            ai_image_count=3,
            ai_image_output_format="jpeg",
            ai_pixel_output_size="256x384",
            aseprite_cli_path="D:/Tools/Aseprite/Aseprite.exe",
            pixel_refiner_service_url="127.0.0.1:9001",
            pixel_refiner_model_dir="D:/Models/pixel-refiner-v1",
            pixel_refiner_model_id="pixel-refiner-custom",
            pixel_refiner_strength=0.8,
            pixel_refiner_candidates=3,
        )

        loaded = AppSettings.from_dict(settings.to_dict())

        self.assertEqual(loaded.ai_image_provider, "compatible")
        self.assertEqual(loaded.ai_image_model, "gpt-image-2")
        self.assertEqual(loaded.ai_image_api_key, "image-key")
        self.assertEqual(loaded.ai_image_base_url, "https://images.example.test/v1")
        self.assertEqual(loaded.ai_image_size, "816x816")
        self.assertEqual(loaded.ai_image_quality, "medium")
        self.assertEqual(loaded.ai_image_background, "opaque")
        self.assertEqual(loaded.ai_image_count, 3)
        self.assertEqual(loaded.ai_image_output_format, "jpeg")
        self.assertEqual(loaded.ai_pixel_output_size, "256x384")
        self.assertEqual(loaded.aseprite_cli_path, "D:/Tools/Aseprite/Aseprite.exe")
        self.assertEqual(loaded.pixel_refiner_service_url, "http://127.0.0.1:9001")
        self.assertEqual(loaded.pixel_refiner_model_dir, "D:/Models/pixel-refiner-v1")
        self.assertEqual(loaded.pixel_refiner_model_id, "pixel-refiner-custom")
        self.assertEqual(loaded.pixel_refiner_strength, 0.8)
        self.assertEqual(loaded.pixel_refiner_candidates, 3)

    def test_pixel_refiner_settings_fall_back_to_v1_defaults(self) -> None:
        settings = AppSettings.from_dict(
            {
                "pixel_refiner_service_url": "not a url",
                "pixel_refiner_strength": 8,
                "pixel_refiner_candidates": 99,
                "pixel_refiner_model_id": "",
            }
        )

        self.assertEqual(settings.pixel_refiner_service_url, DEFAULT_PIXEL_REFINER_SERVICE_URL)
        self.assertEqual(settings.pixel_refiner_strength, 1.0)
        self.assertEqual(settings.pixel_refiner_candidates, 8)
        self.assertEqual(settings.pixel_refiner_model_id, DEFAULT_PIXEL_REFINER_MODEL_ID)

    def test_app_settings_normalizes_url_leaked_into_api_key_fields(self) -> None:
        settings = AppSettings.from_dict(
            {
                "ai_auth_mode": "api_key",
                "ai_api_key": "https://www.packyapi.com/v1",
                "ai_base_url": "https://www.packyapi.com/v1",
                "ai_image_api_key": "https://www.packyapi.com",
                "ai_image_base_url": "https://www.packyapi.com",
            }
        )

        self.assertEqual(settings.ai_api_key, "")
        self.assertEqual(settings.ai_base_url, "https://www.packyapi.com/v1")
        self.assertEqual(settings.ai_image_api_key, "")
        self.assertEqual(settings.ai_image_base_url, "https://www.packyapi.com")

    def test_settings_loads_backup_when_primary_file_is_invalid(self) -> None:
        previous = AppSettings(
            ai_model="gpt-5.5",
            ai_auth_mode="api_key",
            ai_api_key="saved-key",
            ai_base_url="https://api.example.test/v1",
            ai_image_model="gpt-image-2",
            ai_image_api_key="image-key",
        )
        current = AppSettings(ai_model="gpt-5.4", ai_image_model="gpt-image-1.5")

        save_settings(previous)
        save_settings(current)
        settings_path().write_text("{broken", encoding="utf-8")

        loaded = load_settings()

        self.assertTrue(settings_backup_path().exists())
        self.assertEqual(loaded.ai_model, "gpt-5.5")
        self.assertEqual(loaded.ai_api_key, "saved-key")
        self.assertEqual(loaded.ai_base_url, "https://api.example.test/v1")
        self.assertEqual(loaded.ai_image_model, "gpt-image-2")
        self.assertEqual(loaded.ai_image_api_key, "image-key")

    def test_save_settings_keeps_existing_recent_projects_when_new_snapshot_is_empty(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            with mock.patch.dict(os.environ, {"APPDATA": folder}):
                previous = AppSettings(
                    workspace_dir="D:/unityObj/SlimeBarman设计案",
                    export_dir="D:/unityObj/SlimeBarman设计案/exports",
                    last_project="D:/unityObj/SlimeBarman设计案/酒保设计案.gdc",
                    recent_projects=["D:/unityObj/SlimeBarman设计案/酒保设计案.gdc"],
                )
                save_settings(previous)

                current = AppSettings(workspace_dir="D:/unityObj/SlimeBarman设计案")
                save_settings(current)

                loaded = load_settings()

                self.assertEqual(loaded.last_project, "D:/unityObj/SlimeBarman设计案/酒保设计案.gdc")
                self.assertEqual(
                    loaded.recent_projects,
                    ["D:/unityObj/SlimeBarman设计案/酒保设计案.gdc"],
                )

    def test_ai_image_results_are_written_to_project_cache(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            project_path = Path(folder) / "CacheProject.gdc"

            cached = cache_generated_ai_image(project_path, AiGeneratedImage(PNG_1X1, "png"), index=1)
            loaded = load_cached_ai_images(project_path)

            self.assertTrue(cached.path.exists())
            self.assertEqual(cached.path.parent, ai_image_cache_dir(project_path))
            self.assertEqual(len(loaded), 1)
            self.assertEqual(loaded[0].path, cached.path)

    def test_pixel_ai_image_cache_preserves_hidden_source_copy(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            project_path = Path(folder) / "PixelSourceCache.gdc"

            cached = cache_generated_ai_image(
                project_path,
                AiGeneratedImage(PNG_1X1, "png"),
                index=1,
                cache_key="canvas-a",
                pixel_mode=True,
                pixel_output_size="8x8",
            )

            source_path = pixel_source_path_for_candidate(cached.path)
            self.assertTrue(source_path.exists())
            self.assertIn("sources", {part.lower() for part in source_path.parts})

    def test_pixel_refiner_client_posts_v1_payload_and_reads_outputs(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            input_path = Path(folder) / "input.png"
            output_dir = Path(folder) / "outputs"
            output_dir.mkdir()
            output_path = output_dir / "refined.png"
            Image.new("RGBA", (8, 8), (40, 80, 180, 255)).save(input_path)
            Image.new("RGBA", (8, 8), (60, 90, 200, 255)).save(output_path)

            def fake_post(_url: str, payload: dict, *, timeout: int) -> dict:
                self.assertEqual(payload["input_path"], str(input_path.resolve()))
                self.assertEqual(payload["output_dir"], str(output_dir.resolve()))
                self.assertEqual(payload["target_size"], "8x8")
                self.assertEqual(payload["palette_limit"], 48)
                self.assertEqual(payload["return_candidates"], 2)
                self.assertEqual(payload["model"]["id"], "pixel-refiner-test")
                self.assertEqual(payload["client"]["protocol"], "pixel-refiner-v1")
                self.assertEqual(timeout, 180)
                return {
                    "ok": True,
                    "model": "pixel-refiner-test",
                    "outputs": [{"path": str(output_path), "label": "Refined A"}],
                    "checks": {"transparent_png": True},
                }

            request = PixelRefinerRequest(
                input_path=input_path,
                output_dir=output_dir,
                target_size="8x8",
                palette_limit=48,
                return_candidates=2,
                model_id="pixel-refiner-test",
            )
            with mock.patch("gamedesigner.pixel_refiner._post_json", side_effect=fake_post):
                result = refine_pixel_art_with_service(request, service_url="127.0.0.1:9999")

            self.assertEqual(result.model, "pixel-refiner-test")
            self.assertEqual(result.outputs[0].path, output_path)
            self.assertEqual(result.outputs[0].label, "Refined A")
            self.assertTrue(result.checks["transparent_png"])

    def test_pixel_refiner_candidates_finalize_service_pngs_in_pixel_cache(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            project_path = Path(folder) / "PixelRefinerProject.gdc"
            source_path = Path(folder) / "source.png"
            Image.new("RGBA", (16, 16), (120, 90, 200, 255)).save(source_path)
            hidden_source_dir = source_path.parent / "sources"
            hidden_source_dir.mkdir()
            hidden_source_path = hidden_source_dir / f"{source_path.stem}_source.png"
            Image.new("RGBA", (32, 32), (240, 240, 240, 255)).save(hidden_source_path)

            def fake_refine(request: PixelRefinerRequest, *, service_url: str | None = None) -> PixelRefinerResult:
                self.assertEqual(service_url, "http://127.0.0.1:8765")
                self.assertEqual(request.input_path, source_path)
                self.assertEqual(request.target_size, "8x8")
                self.assertEqual(request.return_candidates, 2)
                output_path = request.output_dir / "service_output.png"
                Image.new("RGBA", (12, 12), (12, 34, 56, 180)).save(output_path)
                return PixelRefinerResult(
                    outputs=[PixelRefinerOutput(output_path, "Service A")],
                    model="pixel-refiner-test",
                )

            with mock.patch("gamedesigner.image_ai.refine_pixel_art_with_service", side_effect=fake_refine):
                candidates = generate_pixel_refiner_candidates(
                    project_path,
                    source_path,
                    cache_key="canvas-a",
                    pixel_output_size="8x8",
                    candidates=2,
                )

            self.assertEqual(len(candidates), 1)
            self.assertEqual(candidates[0].label, "Service A")
            self.assertTrue(is_pixel_art_image_path(str(candidates[0].image.path)))
            processed = QImage(str(candidates[0].image.path))
            self.assertEqual((processed.width(), processed.height()), (8, 8))
            self.assertIn(processed.pixelColor(4, 4).alpha(), {0, 255})

    def test_pixel_ai_image_cache_auto_preserves_source_detail(self) -> None:
        source = Image.new("RGBA", (12, 20), (0, 0, 0, 0))
        pixels = source.load()
        for y in range(20):
            for x in range(12):
                pixels[x, y] = ((x * 21 + y * 5) % 256, (x * 13 + y * 9) % 256, (x * 3 + y * 17) % 256, 255)
        buffer = BytesIO()
        source.save(buffer, format="PNG")

        with tempfile.TemporaryDirectory() as folder:
            project_path = Path(folder) / "PixelAutoProject.gdc"

            cached = cache_generated_ai_image(
                project_path,
                AiGeneratedImage(buffer.getvalue(), "webp"),
                index=1,
                cache_key="canvas-a",
                pixel_mode=True,
                pixel_output_size="auto",
            )
            processed = QImage(str(cached.path))
            colors = {
                processed.pixelColor(x, y).rgba()
                for y in range(processed.height())
                for x in range(processed.width())
            }

            self.assertEqual((processed.width(), processed.height()), (12, 20))
            self.assertTrue(is_pixel_art_image_path(str(cached.path)))
            self.assertGreater(len(colors), 128)

    def test_pixel_ai_image_cache_enforces_fixed_grid_png(self) -> None:
        source = Image.new("RGBA", (8, 8))
        pixels = source.load()
        colors = [
            (240, 96, 48, 255),
            (32, 96, 200, 255),
            (32, 200, 96, 255),
            (220, 180, 64, 96),
        ]
        for y in range(8):
            for x in range(8):
                index = (x // 4) + (y // 4) * 2
                pixels[x, y] = colors[index]
        buffer = BytesIO()
        source.save(buffer, format="PNG")

        with tempfile.TemporaryDirectory() as folder:
            project_path = Path(folder) / "PixelCacheProject.gdc"

            cached = cache_generated_ai_image(
                project_path,
                AiGeneratedImage(buffer.getvalue(), "webp"),
                index=1,
                cache_key="canvas-a",
                pixel_mode=True,
                pixel_output_size="2x2",
            )
            loaded = load_cached_ai_images(
                project_path,
                cache_key="canvas-a",
                pixel_mode=True,
                pixel_output_size="2x2",
            )
            normal_loaded = load_cached_ai_images(project_path, cache_key="canvas-a")
            processed = QImage(str(cached.path))
            alpha_values = {
                processed.pixelColor(x, y).alpha()
                for y in range(processed.height())
                for x in range(processed.width())
            }

            self.assertEqual(cached.path.suffix.lower(), ".png")
            self.assertIn("pixel", {part.lower() for part in cached.path.parts})
            self.assertTrue(is_pixel_art_image_path(str(cached.path)))
            self.assertEqual((processed.width(), processed.height()), (2, 2))
            self.assertTrue(alpha_values.issubset({0, 255}))
            self.assertEqual(processed.pixelColor(0, 0).rgba(), QImage.fromData(buffer.getvalue()).pixelColor(0, 0).rgba())
            self.assertEqual([image.path for image in loaded], [cached.path])
            self.assertEqual(normal_loaded, [])

    def test_pixel_ai_image_cache_drops_sparse_edge_fragments(self) -> None:
        source = Image.new("RGBA", (8, 8), (0, 0, 0, 0))
        pixels = source.load()
        for y in range(4):
            for x in range(4):
                pixels[x, y] = (238, 136, 72, 255)
        pixels[4, 0] = (238, 136, 72, 255)
        pixels[5, 0] = (238, 136, 72, 255)
        buffer = BytesIO()
        source.save(buffer, format="PNG")

        with tempfile.TemporaryDirectory() as folder:
            project_path = Path(folder) / "PixelSparseEdgeProject.gdc"

            cached = cache_generated_ai_image(
                project_path,
                AiGeneratedImage(buffer.getvalue(), "png"),
                index=1,
                cache_key="canvas-a",
                pixel_mode=True,
                pixel_output_size="2x2",
            )
            processed = QImage(str(cached.path))

            self.assertEqual(processed.pixelColor(0, 0).alpha(), 255)
            self.assertEqual(processed.pixelColor(1, 0).alpha(), 0)

    def test_pixel_ai_image_cache_preserves_thin_dark_outline(self) -> None:
        source = Image.new("RGBA", (12, 12), (236, 238, 244, 255))
        pixels = source.load()
        for y in range(12):
            for x in range(5, 7):
                pixels[x, y] = (18, 20, 28, 255)
        for y in range(12):
            pixels[8, y] = (248, 248, 252, 255)
        buffer = BytesIO()
        source.save(buffer, format="PNG")

        with tempfile.TemporaryDirectory() as folder:
            project_path = Path(folder) / "PixelOutlineProject.gdc"

            cached = cache_generated_ai_image(
                project_path,
                AiGeneratedImage(buffer.getvalue(), "png"),
                index=1,
                cache_key="canvas-a",
                pixel_mode=True,
                pixel_output_size="3x3",
            )
            processed = QImage(str(cached.path))

            self.assertEqual((processed.width(), processed.height()), (3, 3))
            self.assertLess(processed.pixelColor(1, 0).red(), 64)
            self.assertLess(processed.pixelColor(1, 1).red(), 64)
            self.assertLess(processed.pixelColor(1, 2).red(), 64)

    def test_pixel_ai_image_cache_preserves_clustered_blue_accent(self) -> None:
        source = Image.new("RGBA", (12, 12), (52, 64, 84, 255))
        pixels = source.load()
        for y in range(4, 8):
            for x in range(4, 8):
                pixels[x, y] = (24, 224, 246, 255)
        for y in range(12):
            for x in range(12):
                if (x + y) % 5 == 0:
                    pixels[x, y] = (70, 78, 96, 255)
        buffer = BytesIO()
        source.save(buffer, format="PNG")

        with tempfile.TemporaryDirectory() as folder:
            project_path = Path(folder) / "PixelAccentProject.gdc"

            cached = cache_generated_ai_image(
                project_path,
                AiGeneratedImage(buffer.getvalue(), "png"),
                index=1,
                cache_key="canvas-a",
                pixel_mode=True,
                pixel_output_size="3x3",
            )
            processed = QImage(str(cached.path))
            accent = processed.pixelColor(1, 1)

            self.assertGreater(accent.blue(), 180)
            self.assertGreater(accent.green(), 160)
            self.assertLess(accent.red(), 80)

    def test_pixel_ai_image_cache_clamps_output_size_to_source(self) -> None:
        source = Image.new("RGBA", (8, 8), (10, 20, 30, 255))
        buffer = BytesIO()
        source.save(buffer, format="PNG")

        with tempfile.TemporaryDirectory() as folder:
            project_path = Path(folder) / "PixelClampProject.gdc"

            cached = cache_generated_ai_image(
                project_path,
                AiGeneratedImage(buffer.getvalue(), "png"),
                index=1,
                cache_key="canvas-a",
                pixel_mode=True,
                pixel_output_size="256x384",
            )
            processed = QImage(str(cached.path))

            self.assertEqual((processed.width(), processed.height()), (8, 8))

    def test_pixel_ai_image_cache_contains_tall_source_in_square_output(self) -> None:
        source = Image.new("RGBA", (24, 36), (0, 0, 0, 0))
        pixels = source.load()
        for y in range(36):
            for x in range(8, 16):
                pixels[x, y] = ((y * 7 + x * 3) % 255, 80 + (y % 80), 160, 255)
        buffer = BytesIO()
        source.save(buffer, format="PNG")

        with tempfile.TemporaryDirectory() as folder:
            project_path = Path(folder) / "PixelContainProject.gdc"

            cached = cache_generated_ai_image(
                project_path,
                AiGeneratedImage(buffer.getvalue(), "png"),
                index=1,
                cache_key="canvas-a",
                pixel_mode=True,
                pixel_output_size="8x8",
            )
            processed = QImage(str(cached.path))
            opaque_positions = [
                (x, y)
                for y in range(processed.height())
                for x in range(processed.width())
                if processed.pixelColor(x, y).alpha() == 255
            ]
            colors = {
                processed.pixelColor(x, y).rgba()
                for x, y in opaque_positions
            }

            self.assertEqual((processed.width(), processed.height()), (8, 8))
            self.assertEqual(min(y for _x, y in opaque_positions), 0)
            self.assertEqual(max(y for _x, y in opaque_positions), 7)
            self.assertLess(min(x for x, _y in opaque_positions), 3)
            self.assertGreaterEqual(max(x for x, _y in opaque_positions), 4)
            self.assertLessEqual(len(colors), 48)

    def test_pixel_ai_image_cache_uses_bounded_palette_without_crushing_detail(self) -> None:
        source = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
        pixels = source.load()
        for y in range(64):
            for x in range(64):
                pixels[x, y] = (x * 3 % 256, y * 4 % 256, (x + y) * 2 % 256, 255)
        buffer = BytesIO()
        source.save(buffer, format="PNG")

        with tempfile.TemporaryDirectory() as folder:
            project_path = Path(folder) / "PixelPaletteProject.gdc"

            cached = cache_generated_ai_image(
                project_path,
                AiGeneratedImage(buffer.getvalue(), "png"),
                index=1,
                cache_key="canvas-a",
                pixel_mode=True,
                pixel_output_size="16x16",
            )
            processed = QImage(str(cached.path))
            colors = {
                processed.pixelColor(x, y).rgba()
                for y in range(processed.height())
                for x in range(processed.width())
                if processed.pixelColor(x, y).alpha() == 255
            }

            self.assertGreater(len(colors), 16)
            self.assertLessEqual(len(colors), 48)

    def test_pixel_ai_image_cache_preserves_rich_detail_palette(self) -> None:
        source = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
        pixels = source.load()
        for y in range(8, 56):
            for x in range(12, 52):
                shade = (x * 5 + y * 7) % 96
                pixels[x, y] = (96 + shade, 44 + (x % 9) * 6, 132 + (y % 11) * 5, 255)
        for x in range(12, 52):
            pixels[x, 8] = (28, 24, 36, 255)
            pixels[x, 55] = (28, 24, 36, 255)
        for y in range(8, 56):
            pixels[12, y] = (28, 24, 36, 255)
            pixels[51, y] = (28, 24, 36, 255)
        for x, y in [(26, 24), (38, 24), (28, 34), (36, 34), (32, 42)]:
            pixels[x, y] = (246, 226, 116, 255)
        for x, y in [(27, 25), (39, 25), (32, 35)]:
            pixels[x, y] = (18, 16, 24, 255)
        buffer = BytesIO()
        source.save(buffer, format="PNG")

        with tempfile.TemporaryDirectory() as folder:
            project_path = Path(folder) / "PixelDetailProject.gdc"

            cached = cache_generated_ai_image(
                project_path,
                AiGeneratedImage(buffer.getvalue(), "png"),
                index=1,
                cache_key="canvas-a",
                pixel_mode=True,
                pixel_output_size="64x64",
            )
            processed = QImage(str(cached.path))
            colors = {
                processed.pixelColor(x, y).rgba()
                for y in range(processed.height())
                for x in range(processed.width())
                if processed.pixelColor(x, y).alpha() == 255
            }
            eye = processed.pixelColor(27, 25)
            highlight = processed.pixelColor(32, 42)

            self.assertGreater(len(colors), 32)
            self.assertLessEqual(len(colors), 96)
            self.assertLess(eye.red(), 50)
            self.assertGreater(highlight.red(), 180)

    def test_pixel_downscale_candidates_create_reference_pngs(self) -> None:
        source = Image.new("RGBA", (16, 24), (0, 0, 0, 0))
        pixels = source.load()
        for y in range(3, 21):
            for x in range(4, 12):
                pixels[x, y] = (80 + x * 8, 40 + y * 4, 180, 255)
        with tempfile.TemporaryDirectory() as folder:
            project_path = Path(folder) / "PixelCandidates.gdc"
            source_path = Path(folder) / "source.png"
            source.save(source_path)

            candidates = generate_pixel_downscale_candidates(
                project_path,
                source_path,
                cache_key="canvas-a",
                pixel_output_size="8x8",
                include_aseprite=False,
            )

            self.assertEqual(len(candidates), 5)
            self.assertEqual({candidate.method for candidate in candidates}, {"grid", "box", "lanczos", "nearest", "palette_box"})
            for candidate in candidates:
                self.assertTrue(candidate.image.path.exists())
                self.assertTrue(candidate.image.path.name.startswith("candidate_"))
                expected_label = "".join(
                    char if char.isalnum() or char in {"-", "_"} else "_"
                    for char in candidate.label.strip()
                ).strip("_")[:40]
                self.assertIn(expected_label, candidate.image.path.name)
                self.assertTrue(is_pixel_art_image_path(str(candidate.image.path)))
                processed = QImage(str(candidate.image.path))
                self.assertEqual((processed.width(), processed.height()), (8, 8))

    def test_pixel_downscale_candidates_ignore_missing_aseprite(self) -> None:
        source = Image.new("RGBA", (16, 16), (120, 90, 200, 255))
        with tempfile.TemporaryDirectory() as folder:
            project_path = Path(folder) / "PixelNoAseprite.gdc"
            source_path = Path(folder) / "source.png"
            source.save(source_path)

            with mock.patch("gamedesigner.image_ai.find_aseprite_executable", return_value=None):
                candidates = generate_pixel_downscale_candidates(
                    project_path,
                    source_path,
                    cache_key="canvas-a",
                    pixel_output_size="8x8",
                    include_aseprite=True,
                )

            self.assertEqual(len(candidates), 5)
            self.assertFalse(any(candidate.used_aseprite for candidate in candidates))

    def test_ai_image_request_with_reference_omits_input_fidelity_for_gpt_image_2(self) -> None:
        settings = AppSettings(
            ai_image_provider="compatible",
            ai_image_model="gpt-image-2",
            ai_image_api_key="secret",
            ai_image_base_url="https://images.example.test/v1",
            ai_image_quality="high",
        )
        with tempfile.TemporaryDirectory() as folder:
            reference = Path(folder) / "ref.png"
            reference.write_bytes(PNG_1X1)

            request = build_ai_image_request(settings, "请基于酒吧场景生成更清晰的地面", [reference])
            body, _content_type = _multipart_body(request)

        text = body.decode("utf-8", errors="ignore")

        self.assertIn("quality", text)
        self.assertIn("high", text)
        self.assertIn("output_format", text)
        self.assertNotIn("input_fidelity", text)

    def test_ai_image_multipart_handles_chinese_prompt_and_filename(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            reference = Path(folder) / "\u9152\u5427\u5730\u9762.png"
            reference.write_bytes(PNG_1X1)

            body, content_type = _encode_multipart(
                [("prompt", "\u8bf7\u751f\u6210\u66f4\u6e05\u6670\u7684\u9152\u5427\u5730\u9762")],
                [("image", reference)],
            )

        text = body.decode("utf-8", errors="ignore")

        self.assertIn("multipart/form-data; boundary=", content_type)
        self.assertIn("请生成更清晰的酒吧地面", text)
        self.assertIn('filename="reference_1.png"', text)
        self.assertIn("filename*=UTF-8''%E9%85%92%E5%90%A7%E5%9C%B0%E9%9D%A2.png", text)
        self.assertNotIn('filename="酒吧地面.png"', text)

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
        self.assertEqual(invocation.arguments[config_index + 1], "model_reasoning_effort=medium")

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

    def test_qprocess_command_uses_windows_cmd_shim_when_available(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            shim = Path(folder) / "codex.cmd"
            shim.write_text("@echo off\r\nexit /b 0\r\n", encoding="ascii")
            with mock.patch.dict(os.environ, {"PATH": folder}):
                settings = AppSettings(ai_provider="codex", ai_model="gpt-5.5")
                invocation = build_ai_cli_invocation(settings, "hello", Path("D:/GameDesigner"))

                program, arguments = qprocess_command(invocation, "win32")

        self.assertEqual(program, str(shim))
        self.assertEqual(arguments, invocation.arguments)

    def test_qprocess_command_uses_windows_claude_shim_when_available(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            shim = Path(folder) / "claude.cmd"
            shim.write_text("@echo off\r\nexit /b 0\r\n", encoding="ascii")
            with mock.patch.dict(os.environ, {"PATH": folder}):
                settings = AppSettings(ai_provider="claude", ai_model="opus")
                invocation = build_ai_cli_invocation(settings, "hello", Path("D:/GameDesigner"))

                program, arguments = qprocess_command(invocation, "win32")

        self.assertEqual(program, str(shim))
        self.assertEqual(arguments, invocation.arguments)

    def test_qprocess_command_keeps_native_program_outside_windows(self) -> None:
        settings = AppSettings(ai_provider="codex", ai_model="gpt-5.5")
        invocation = build_ai_cli_invocation(settings, "hello", Path("D:/GameDesigner"))

        program, arguments = qprocess_command(invocation, "linux")

        self.assertEqual(program, "codex")
        self.assertEqual(arguments, invocation.arguments)

    def test_resolve_ai_cli_program_prefers_portable_runtime_directory(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            runtime_root = Path(folder) / "runtime"
            cli_bin = runtime_root / "ai-cli" / "node_modules" / ".bin"
            cli_bin.mkdir(parents=True)
            (cli_bin / "codex").write_text("#!/bin/sh\n", encoding="ascii")
            shim = cli_bin / "codex.cmd"
            shim.write_text("@echo off\r\nexit /b 0\r\n", encoding="ascii")
            with mock.patch.dict(os.environ, {"GAMEDESIGNER_RUNTIME_DIR": str(runtime_root)}):
                resolved = resolve_ai_cli_program("codex", "win32")
                env = portable_ai_runtime_environment()

        self.assertEqual(resolved, str(shim))
        self.assertIn(str(cli_bin), env["PATH"])
        self.assertEqual(env["GAMEDESIGNER_RUNTIME_DIR"], str(runtime_root))

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

    def test_split_ai_canvas_action_response_recovers_fenced_json_actions(self) -> None:
        visible, actions, error = split_ai_canvas_action_response(
            "可以，下面是新蓝图。\n"
            "```json\n"
            '[{"type":"create_group","title":"酒馆经营蓝图","nodes":[{"type":"create_node","title":"接单"}]}]\n'
            "```"
        )

        self.assertEqual(error, "")
        self.assertEqual(visible, "可以，下面是新蓝图。")
        self.assertEqual(len(actions), 1)
        self.assertEqual(actions[0].type, "create_group")
        self.assertEqual(actions[0].title, "酒馆经营蓝图")
        self.assertEqual(actions[0].nodes[0].title, "接单")

    def test_parse_ai_canvas_actions_supports_single_action_object_and_adjacent_objects(self) -> None:
        actions = parse_ai_canvas_actions(
            '{"type":"create_group","title":"模块A"}\n'
            '{"type":"create_node","title":"模块节点"}'
        )

        self.assertEqual([action.type for action in actions], ["create_group", "create_node"])
        self.assertEqual(actions[0].title, "模块A")
        self.assertEqual(actions[1].title, "模块节点")

    def test_split_ai_canvas_action_response_keeps_non_action_json_visible(self) -> None:
        visible, actions, error = split_ai_canvas_action_response(
            "这里是数据示例。\n"
            "```json\n"
            '{"title":"不是画布动作","items":[1,2,3]}\n'
            "```"
        )

        self.assertEqual(error, "")
        self.assertEqual(actions, [])
        self.assertIn("不是画布动作", visible)

    def test_split_ai_canvas_action_response_recovers_inline_json_actions(self) -> None:
        visible, actions, error = split_ai_canvas_action_response(
            '可以创建。 [{"type":"create_group","title":"战斗系统蓝图"}]'
        )

        self.assertEqual(error, "")
        self.assertEqual(visible, "可以创建。")
        self.assertEqual(len(actions), 1)
        self.assertEqual(actions[0].type, "create_group")
        self.assertEqual(actions[0].title, "战斗系统蓝图")

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
