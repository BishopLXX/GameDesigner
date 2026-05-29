from __future__ import annotations

import os
from pathlib import Path


APP_NAME = "GameDesigner"
APP_DATA_ENV = "GAMEDESIGNER_DATA_DIR"
DEFAULT_DATA_ROOT = Path("D:/GameDesignerData")


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


def pixel_refiner_data_root(version: str) -> Path:
    return game_designer_data_root() / "pixel_refiner" / version


def pixel_refiner_model_dir(model_id: str) -> Path:
    return game_designer_data_root() / "pixel_refiner" / "models" / model_id
