import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from PIL import Image

from gamedesigner.pixel_refiner import PixelRefinerOutput, PixelRefinerResult
from gamedesigner.pixel_refiner_dataset import build_pair_record
from gamedesigner.pixel_refiner_eval_suite import (
    build_fixed_eval_suite,
    fixed_eval_manifest_path,
    load_fixed_eval_suite,
    render_fixed_eval_contact_sheet,
    run_fixed_eval_model,
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

    def test_run_fixed_eval_model_writes_outputs_and_model_contact_sheet(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            with self._isolated_app_env(folder):
                root = Path(folder)
                input_path = root / "input.png"
                target_path = root / "target.png"
                Image.new("RGBA", (32, 32), (40, 80, 120, 255)).save(input_path)
                Image.new("RGBA", (32, 32), (80, 120, 180, 255)).save(target_path)
                build_pair_record(
                    target_path=target_path,
                    input_path=input_path,
                    source_id="demo_source",
                    category="character_portrait",
                    input_kind="dirty_outline",
                )
                build_fixed_eval_suite(limit=1, source_id="demo_source")

                def fake_refine(request, *, service_url, timeout):  # noqa: ANN001
                    output_path = request.output_dir / "refined_1.png"
                    output_path.parent.mkdir(parents=True, exist_ok=True)
                    Image.new("RGBA", (32, 32), (100, 140, 200, 255)).save(output_path)
                    return PixelRefinerResult(
                        outputs=[PixelRefinerOutput(path=output_path, label="标准")],
                        model=request.model_id,
                        checks={"grid_aligned": True},
                    )

                with mock.patch("gamedesigner.pixel_refiner_eval_suite.refine_pixel_art_with_service", side_effect=fake_refine) as refine:
                    result = run_fixed_eval_model(
                        service_url="http://127.0.0.1:8765",
                        model_id="pixel-refiner-test",
                        output_dir=root / "eval_run",
                        limit=1,
                        cell_size=96,
                    )

                self.assertTrue(result["ok"])
                self.assertEqual(result["succeeded"], 1)
                self.assertEqual(result["failed"], 0)
                self.assertTrue(Path(result["contact_sheet"]).is_file())
                self.assertTrue(Path(result["manifest"]).is_file())
                request = refine.call_args.args[0]
                self.assertEqual(request.target_size, "32x32")
                self.assertEqual(request.model_id, "pixel-refiner-test")


if __name__ == "__main__":
    unittest.main()
