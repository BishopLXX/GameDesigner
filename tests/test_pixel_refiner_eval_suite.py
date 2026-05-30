import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from PIL import Image

from gamedesigner.pixel_refiner_dataset import build_pair_record
from gamedesigner.pixel_refiner_eval_suite import (
    build_fixed_eval_suite,
    fixed_eval_manifest_path,
    load_fixed_eval_suite,
    render_fixed_eval_contact_sheet,
)


class PixelRefinerEvalSuiteTests(unittest.TestCase):
    def _isolated_app_env(self, folder: str):
        return mock.patch.dict(
            os.environ,
            {
                "APPDATA": folder,
                "LOCALAPPDATA": folder,
                "GAMEDESIGNER_DATA_DIR": str(Path(folder) / "GameDesignerData"),
            },
        )

    def test_builds_fixed_eval_suite_and_contact_sheet(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            with self._isolated_app_env(folder):
                root = Path(folder)
                for index in range(3):
                    input_path = root / f"input_{index}.png"
                    target_path = root / f"target_{index}.png"
                    Image.new("RGBA", (32, 32), (40 + index, 80, 120, 255)).save(input_path)
                    Image.new("RGBA", (32, 32), (80 + index, 120, 180, 255)).save(target_path)
                    build_pair_record(
                        target_path=target_path,
                        input_path=input_path,
                        source_id="demo_source",
                        category="character_portrait",
                        input_kind="dirty_outline",
                    )

                stats = build_fixed_eval_suite(limit=2, source_id="demo_source")
                items = load_fixed_eval_suite()
                sheet = render_fixed_eval_contact_sheet(cell_size=96)

                self.assertTrue(stats["ok"])
                self.assertEqual(stats["items"], 2)
                self.assertTrue(fixed_eval_manifest_path().is_file())
                self.assertEqual(len(items), 2)
                self.assertTrue(Path(sheet["path"]).is_file())


if __name__ == "__main__":
    unittest.main()
