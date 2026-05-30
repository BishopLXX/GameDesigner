import tempfile
import unittest
import os
from pathlib import Path
from unittest import mock

from PIL import Image

from gamedesigner.pixel_refiner_dataset import (
    PixelRefinerSourceRecord,
    add_source_record,
    build_pair_record,
    dataset_dir,
    ensure_dataset_dirs,
    generated_inputs_dir,
    global_dataset_root,
    ingest_generated_input_image,
    ingest_software_candidate_pair,
    ingest_target_image,
    load_pair_records,
    load_source_records,
    summarize_dataset,
    targets_dir,
)
from gamedesigner.pixel_refiner_pair_generation import build_pairs_from_targets, generate_training_inputs_from_target
from gamedesigner.pixel_refiner_character_crops import CharacterCropConfig, extract_character_crops
from gamedesigner.pixel_refiner_patch_expansion import PatchExpansionConfig, expand_target_patches
from gamedesigner.pixel_refiner_authorized_import import (
    AuthorizedImportConfig,
    AuthorizedSiteCrawlConfig,
    crawl_authorized_site,
    import_authorized_targets,
)


class PixelRefinerDatasetTests(unittest.TestCase):
    def _isolated_app_env(self, folder: str):
        return mock.patch.dict(
            os.environ,
            {
                "APPDATA": folder,
                "LOCALAPPDATA": folder,
                "GAMEDESIGNER_DATA_DIR": str(Path(folder) / "GameDesignerData"),
            },
        )

    def test_dataset_defaults_to_d_drive_data_root(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            with self._isolated_app_env(folder):
                self.assertIn("pixel_refiner", str(global_dataset_root()))
                self.assertIn("GameDesignerData", str(global_dataset_root()))

    def test_source_records_roundtrip_and_pairs(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            with self._isolated_app_env(folder):
                ensure_dataset_dirs()
                add_source_record(
                    PixelRefinerSourceRecord(
                        source_id="kenney_pixel_platformer",
                        title="Pixel Platformer",
                        author="Kenney",
                        url="https://kenney.nl/assets/pixel-platformer",
                        license="CC0",
                        license_url="https://creativecommons.org/publicdomain/zero/1.0/",
                        ai_training_allowed=True,
                        category="sprite",
                    )
                )
                self.assertEqual(len(load_source_records()), 1)
                self.assertTrue(dataset_dir().exists())

                target_path = Path(folder) / "target.png"
                input_path = Path(folder) / "input.png"
                Image.new("RGBA", (8, 8), (120, 80, 200, 255)).save(target_path)
                Image.new("RGBA", (8, 8), (90, 60, 180, 255)).save(input_path)

                target_copy = ingest_target_image(
                    target_path,
                    source_id="kenney_pixel_platformer",
                    category="sprite",
                    title="Hero",
                    license="CC0",
                )
                input_copy = ingest_generated_input_image(
                    input_path,
                    source_id="kenney_pixel_platformer",
                    category="sprite",
                    input_kind="software_candidate",
                )
                record = build_pair_record(
                    target_path=target_copy,
                    input_path=input_copy,
                    source_id="kenney_pixel_platformer",
                    category="sprite",
                    input_kind="software_candidate",
                    license="CC0",
                )

                self.assertTrue(record.target_path.exists())
                self.assertTrue(record.input_path.exists())
                self.assertEqual(len(load_pair_records()), 1)
                summary = summarize_dataset()
                self.assertEqual(summary["pairs"], 1)
                self.assertEqual(summary["software_candidate"], 1)
                self.assertEqual(summary["input_kinds"]["software_candidate"], 1)
                self.assertGreaterEqual(summary["targets"], 1)
                self.assertGreaterEqual(summary["inputs"], 1)

    def test_generate_training_inputs_creates_multiple_methods(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            with self._isolated_app_env(folder):
                target_path = Path(folder) / "target.png"
                Image.new("RGBA", (8, 8), (80, 120, 200, 255)).save(target_path)

                outputs = generate_training_inputs_from_target(
                    target_path,
                    source_id="demo",
                    category="sprite",
                )

                self.assertGreaterEqual(len(outputs), 5)
                for path, method in outputs:
                    self.assertTrue(path.exists())
                    self.assertTrue(method)
                self.assertTrue(generated_inputs_dir().exists())

    def test_ingest_software_candidate_pair_is_deduplicated_feedback_data(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            with self._isolated_app_env(folder):
                input_path = Path(folder) / "software_bad.png"
                target_path = Path(folder) / "true_pixel.png"
                Image.new("RGBA", (32, 32), (90, 60, 180, 255)).save(input_path)
                Image.new("RGBA", (32, 32), (120, 80, 200, 255)).save(target_path)

                first = ingest_software_candidate_pair(
                    input_path,
                    target_path,
                    category="character_portrait",
                    notes="manual failure capture",
                )
                second = ingest_software_candidate_pair(
                    input_path,
                    target_path,
                    category="character_portrait",
                )

                self.assertTrue(first.created)
                self.assertFalse(second.created)
                self.assertEqual(first.record.pair_id, second.record.pair_id)
                self.assertEqual(first.record.input_kind, "software_candidate")
                self.assertEqual(first.record.category, "character_portrait")
                self.assertIn("original_input=", first.record.notes)
                self.assertEqual(len(load_pair_records()), 1)
                sources = load_source_records()
                self.assertEqual(sources[0].source_id, "gamedesigner_feedback")
                self.assertTrue(sources[0].ai_training_allowed)

    def test_ingest_software_candidate_pair_requires_matching_size(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            with self._isolated_app_env(folder):
                input_path = Path(folder) / "software_bad.png"
                target_path = Path(folder) / "true_pixel.png"
                Image.new("RGBA", (32, 32), (90, 60, 180, 255)).save(input_path)
                Image.new("RGBA", (64, 64), (120, 80, 200, 255)).save(target_path)

                with self.assertRaisesRegex(ValueError, "尺寸必须一致"):
                    ingest_software_candidate_pair(input_path, target_path)

    def test_build_pairs_from_targets_is_deduplicated_and_batched(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            with self._isolated_app_env(folder):
                target_path = (
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
                target_path.parent.mkdir(parents=True, exist_ok=True)
                image = Image.new("RGBA", (128, 160), (80, 120, 200, 0))
                for x in range(20, 108):
                    for y in range(18, 142):
                        image.putpixel((x, y), (80, 120, 200, 255))
                for x in range(36, 92):
                    for y in range(46, 110):
                        image.putpixel((x, y), (220, 180, 90, 255))
                image.save(target_path)

                first = build_pairs_from_targets()
                second = build_pairs_from_targets()
                summary = summarize_dataset()

                self.assertEqual(first["targets_matched"], 1)
                self.assertEqual(first["inputs_generated"], 5)
                self.assertEqual(first["pairs_created"], 5)
                self.assertEqual(second["pairs_created"], 0)
                self.assertEqual(summary["pairs"], 5)
                self.assertEqual(summary["generated_inputs"], 5)

    def test_import_authorized_targets_classifies_and_builds_pairs(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            with self._isolated_app_env(folder):
                source_dir = Path(folder) / "authorized"
                source_dir.mkdir()
                tall = source_dir / "portrait.png"
                wide = source_dir / "run.png"
                small = source_dir / "tiny.png"
                Image.new("RGBA", (128, 256), (90, 60, 180, 255)).save(tall)
                Image.new("RGBA", (256, 96), (120, 80, 200, 255)).save(wide)
                Image.new("RGBA", (16, 16), (120, 80, 200, 255)).save(small)

                stats = import_authorized_targets(
                    AuthorizedImportConfig(
                        input_dir=source_dir,
                        source_id="authorized_artist",
                        title="Authorized Artist Pack",
                        author="Artist",
                        url="local://authorized",
                        rights_basis="Local folder provided by rights holder for training.",
                        build_pairs=True,
                    )
                )

                self.assertEqual(stats["targets_imported"], 2)
                self.assertEqual(stats["rejected"], 1)
                self.assertEqual(stats["build_pairs"]["inputs_generated"], 10)
                self.assertGreater(stats["build_pairs"]["pairs_created"], 0)
                self.assertTrue((targets_dir() / "authorized_artist" / "character_portrait").exists())
                self.assertTrue((targets_dir() / "authorized_artist" / "side_scroller_action_character").exists())
                summary = summarize_dataset()
                self.assertEqual(summary["targets"], 2)
                self.assertEqual(summary["pairs"], stats["build_pairs"]["pairs_created"])

    def test_expand_target_patches_builds_overlapping_patch_pairs(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            with self._isolated_app_env(folder):
                source_root = targets_dir() / "demo_source"
                target_path = source_root / "character_portrait" / "hero.png"
                target_path.parent.mkdir(parents=True, exist_ok=True)
                image = Image.new("RGBA", (128, 128), (0, 0, 0, 0))
                for x in range(16, 112):
                    for y in range(16, 112):
                        image.putpixel((x, y), (x % 255, y % 255, 180, 255))
                image.save(target_path)

                stats = expand_target_patches(
                    PatchExpansionConfig(
                        source_root=source_root,
                        output_source_id="demo_source_patch64",
                        patch_size=64,
                        overlap=32,
                        max_patches=4,
                        max_patches_per_image=4,
                        build_pairs=True,
                    )
                )

                self.assertEqual(stats["patches_created"], 4)
                self.assertEqual(stats["build_pairs"]["targets_matched"], 4)
                self.assertEqual(stats["build_pairs"]["inputs_generated"], 20)
                self.assertGreaterEqual(stats["build_pairs"]["pairs_created"], 16)
                self.assertEqual(summarize_dataset()["pairs"], stats["build_pairs"]["pairs_created"])
                self.assertTrue((targets_dir() / "demo_source_patch64" / "character_portrait").exists())

    def test_extract_character_crops_splits_sprite_sheet_components(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            with self._isolated_app_env(folder):
                source_root = targets_dir() / "demo_source"
                target_path = source_root / "character_sprite" / "sheet.png"
                target_path.parent.mkdir(parents=True, exist_ok=True)
                image = Image.new("RGBA", (220, 96), (0, 0, 0, 0))
                for offset, color in ((18, (220, 80, 100, 255)), (124, (80, 180, 220, 255))):
                    for x in range(offset, offset + 52):
                        for y in range(18, 78):
                            image.putpixel((x, y), color)
                image.save(target_path)

                stats = extract_character_crops(
                    CharacterCropConfig(
                        source_root=source_root,
                        output_source_id="demo_source_single_crops",
                        min_width=40,
                        min_height=40,
                        min_area=500,
                        max_crops=10,
                        build_pairs=True,
                    )
                )

                self.assertEqual(stats["crops_created"], 2)
                self.assertEqual(stats["build_pairs"]["targets_matched"], 2)
                self.assertEqual(stats["build_pairs"]["inputs_generated"], 10)
                self.assertGreaterEqual(stats["build_pairs"]["pairs_created"], 2)
                self.assertTrue((targets_dir() / "demo_source_single_crops" / "character_sprite").exists())

    def test_crawl_authorized_site_dry_run_discovers_public_images(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            with self._isolated_app_env(folder):
                with mock.patch(
                    "gamedesigner.pixel_refiner_authorized_import.crawl_site",
                    return_value={
                        "image_pages": {
                            "https://cdn.example.test/hero.png": {"https://artist.example.test/gallery"},
                            "https://cdn.example.test/run.gif": {"https://artist.example.test/gallery"},
                        },
                        "page_records": [{"url": "https://artist.example.test/gallery", "status": "ok"}],
                    },
                ) as crawl:
                    stats = crawl_authorized_site(
                        AuthorizedSiteCrawlConfig(
                            start_urls=("https://artist.example.test/gallery",),
                            source_id="artist_site",
                            rights_basis="Owned platform authorized for training.",
                            dry_run=True,
                        )
                    )

                self.assertTrue(stats["ok"])
                self.assertEqual(stats["page_host"], "artist.example.test")
                self.assertEqual(stats["pages"], 1)
                self.assertEqual(stats["discovered_images"], 2)
                self.assertIn("https://cdn.example.test/hero.png", stats["first_images"])
                crawl.assert_called_once()


if __name__ == "__main__":
    unittest.main()
