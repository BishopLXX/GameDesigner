import unittest
from pathlib import Path

from gamedesigner.pixel_refiner_dataset import PixelRefinerPairRecord

try:
    from gamedesigner.pixel_refiner_training import (
        PairPatchDataset,
        PixelRefinerTrainConfig,
        V41_AI_PSEUDO_WEIGHT,
        V41_MODEL_ID,
        V41_SOFTWARE_CANDIDATE_WEIGHT,
        _config_to_json,
    )
    TRAINING_IMPORT_ERROR = None
except Exception as exc:  # pragma: no cover - depends on local training env
    TRAINING_IMPORT_ERROR = exc


def _record(input_kind: str, category: str) -> PixelRefinerPairRecord:
    return PixelRefinerPairRecord(
        pair_id=f"{input_kind}-{category}",
        target_path=Path("target.png"),
        input_path=Path("input.png"),
        source_id="test",
        category=category,
        target_sha256="target",
        input_sha256="input",
        width=64,
        height=64,
        created_at="2026-05-30T00:00:00",
        input_kind=input_kind,
    )


@unittest.skipIf(TRAINING_IMPORT_ERROR is not None, f"Pixel Refiner training env unavailable: {TRAINING_IMPORT_ERROR}")
class PixelRefinerTrainingTests(unittest.TestCase):
    def test_v41_config_serializes_real_failure_sample_weights(self) -> None:
        config = PixelRefinerTrainConfig(
            model_id=V41_MODEL_ID,
            software_candidate_weight=V41_SOFTWARE_CANDIDATE_WEIGHT,
            ai_pseudo_weight=V41_AI_PSEUDO_WEIGHT,
        )

        payload = _config_to_json(config)

        self.assertEqual(payload["software_candidate_weight"], 32.0)
        self.assertEqual(payload["ai_pseudo_weight"], 16.0)
        self.assertEqual(payload["sample_weights"]["input_kind"]["software_candidate"], 32.0)
        self.assertEqual(payload["sample_weights"]["input_kind"]["ai_pseudo"], 16.0)

    def test_pair_patch_dataset_uses_configurable_kind_and_category_weights(self) -> None:
        records = [
            _record("software_candidate", "character_portrait"),
            _record("ai_pseudo", "character_sprite"),
            _record("soft_bilinear", "character_portrait"),
        ]

        dataset = PairPatchDataset(
            records,
            patch_size=64,
            length=3,
            seed=1,
            weighted=True,
            input_kind_weights={"software_candidate": 32.0, "ai_pseudo": 16.0, "soft_bilinear": 1.0},
            category_weights={"character_portrait": 2.0, "character_sprite": 1.5},
        )

        self.assertEqual(dataset.weights, [64.0, 24.0, 2.0])


if __name__ == "__main__":
    unittest.main()
