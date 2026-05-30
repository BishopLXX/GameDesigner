import tempfile
import unittest
import os
from io import BytesIO
from pathlib import Path
from unittest import mock

from PIL import Image

from gamedesigner.image_ai import AiGeneratedImage
from gamedesigner.pixel_refiner_ai_pseudo import (
    AI_PSEUDO_INPUT_KIND,
    generate_ai_pseudo_pairs,
    normalize_generated_to_target,
    select_target_paths,
)
from gamedesigner.pixel_refiner_dataset import load_pair_records, summarize_dataset
from gamedesigner.storage import AppSettings


class PixelRefinerAiPseudoTests(unittest.TestCase):
    def _isolated_app_env(self, folder: str):
        return mock.patch.dict(
            os.environ,
            {
                "APPDATA": folder,
                "LOCALAPPDATA": folder,
                "GAMEDESIGNER_DATA_DIR": str(Path(folder) / "GameDesignerData"),
            },
        )

    def test_normalizes_generated_image_to_target_size_and_soft_mask(self) -> None:
        target = Image.new("RGBA", (32, 48), (0, 0, 0, 0))
        for x in range(8, 24):
            for y in range(10, 38):
                target.putpixel((x, y), (255, 255, 255, 255))
        generated = Image.new("RGBA", (128, 128), (120, 80, 200, 255))
        buffer = BytesIO()
        generated.save(buffer, format="PNG")

        normalized = normalize_generated_to_target(buffer.getvalue(), target_image=target)

        self.assertEqual(normalized.size, target.size)
        self.assertLess(normalized.getchannel("A").getextrema()[0], 255)

    def test_selects_targets_and_generates_ai_pseudo_pairs_with_mock_generator(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            with self._isolated_app_env(folder):
                target = (
                    Path(folder)
                    / "GameDesignerData"
                    / "pixel_refiner"
                    / "datasets"
                    / "gold_pndsndn_v1"
                    / "targets"
                    / "demo_source"
                    / "character_portrait"
                    / "hero.png"
                )
                target.parent.mkdir(parents=True, exist_ok=True)
                Image.new("RGBA", (64, 80), (20, 40, 80, 255)).save(target)
                settings = AppSettings(
                    ai_image_provider="compatible",
                    ai_image_model="mock-image-model",
                    ai_image_api_key="test-key",
                    ai_image_base_url="https://images.example.test/v1",
                    ai_image_count=1,
                    ai_image_output_format="png",
                )

                def fake_generator(request):
                    self.assertEqual(request.reference_paths, [target])
                    self.assertEqual(request.background, "auto")
                    image = Image.new("RGBA", (128, 128), (200, 120, 80, 255))
                    buffer = BytesIO()
                    image.save(buffer, format="PNG")
                    return [AiGeneratedImage(buffer.getvalue(), "png")]

                selected = select_target_paths(target.parent.parent.parent, source_id="demo_source", limit=1)
                stats = generate_ai_pseudo_pairs(
                    source_id="demo_source",
                    limit=1,
                    settings=settings,
                    generator=fake_generator,
                )
                summary = summarize_dataset()
                pairs = load_pair_records()

                self.assertEqual(selected, [target])
                self.assertEqual(stats["pairs_created"], 1)
                self.assertEqual(summary["pairs"], 1)
                self.assertEqual(pairs[0].input_kind, AI_PSEUDO_INPUT_KIND)
                self.assertEqual(pairs[0].width, 64)
                self.assertEqual(pairs[0].height, 80)

    def test_select_target_paths_can_filter_large_scene_targets(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            sprite = root / "demo_source" / "character_sprite" / "hero.png"
            scene = root / "demo_source" / "character_sprite" / "scene.png"
            sprite.parent.mkdir(parents=True, exist_ok=True)
            Image.new("RGBA", (96, 144), (20, 40, 80, 255)).save(sprite)
            Image.new("RGBA", (512, 384), (20, 40, 80, 255)).save(scene)

            selected = select_target_paths(
                root,
                source_id="demo_source",
                limit=0,
                max_width=320,
                max_height=320,
            )

            self.assertEqual(selected, [sprite])


if __name__ == "__main__":
    unittest.main()
