from __future__ import annotations

import os
from pathlib import Path


APP_NAME = "GameDesigner"
APP_DATA_ENV = "GAMEDESIGNER_DATA_DIR"
PIXEL_REFINER_DATASET_ENV = "GAMEDESIGNER_PIXEL_REFINER_DATASET_DIR"
DEFAULT_DATA_ROOT = Path("D:/GameDesignerData")
DEFAULT_PIXEL_REFINER_DATASET_ID = "gold_pndsndn_v1"
LEGACY_PIXEL_REFINER_DATASET_ID = "character_large_v1"


def game_designer_data_root() -> Path:
    override = str(os.getenv(APP_DATA_ENV) or "").strip()
    if override:
        return Path(override).expanduser()
    return DEFAULT_DATA_ROOT


def game_designer_data_dir() -> Path:
    return game_designer_data_root()


def game_designer_config_dir() -> Path:
    return game_designer_data_root() / "config"


def game_designer_workspace_dir() -> Path:
    return game_designer_data_root() / "projects"


def pixel_refiner_root() -> Path:
    return game_designer_data_root() / "pixel_refiner"


def pixel_refiner_datasets_root() -> Path:
    return pixel_refiner_root() / "datasets"


def pixel_refiner_data_root(version: str) -> Path:
    override = str(os.getenv(PIXEL_REFINER_DATASET_ENV) or "").strip()
    if override:
        return Path(override).expanduser()
    dataset_id = str(version or DEFAULT_PIXEL_REFINER_DATASET_ID).strip() or DEFAULT_PIXEL_REFINER_DATASET_ID
    return pixel_refiner_datasets_root() / dataset_id


def legacy_pixel_refiner_data_root(version: str = LEGACY_PIXEL_REFINER_DATASET_ID) -> Path:
    return pixel_refiner_root() / version


def pixel_refiner_eval_root() -> Path:
    return pixel_refiner_root() / "eval"


def pixel_refiner_runs_root() -> Path:
    return pixel_refiner_root() / "runs"


def pixel_refiner_model_dir(model_id: str) -> Path:
    return pixel_refiner_root() / "models" / model_id
