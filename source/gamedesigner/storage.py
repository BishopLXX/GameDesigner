from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .models import ProjectData, default_project


APP_NAME = "GameDesigner"
PROJECT_SUFFIX = ".gdesigner.json"


@dataclass
class AppSettings:
    workspace_dir: str = ""
    export_dir: str = ""
    last_project: str = ""
    theme: str = "dark"
    recent_projects: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, object]:
        return {
            "workspace_dir": self.workspace_dir,
            "export_dir": self.export_dir,
            "last_project": self.last_project,
            "theme": self.theme,
            "recent_projects": self.recent_projects,
        }

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "AppSettings":
        recent = raw.get("recent_projects", [])
        if not isinstance(recent, list):
            recent = []
        return cls(
            workspace_dir=str(raw.get("workspace_dir", "")),
            export_dir=str(raw.get("export_dir", "")),
            last_project=str(raw.get("last_project", "")),
            theme=str(raw.get("theme", "dark") or "dark"),
            recent_projects=[str(item) for item in recent if item],
        )


def app_data_dir() -> Path:
    base = os.getenv("APPDATA")
    if base:
        return Path(base) / APP_NAME
    return Path.home() / f".{APP_NAME.lower()}"


def default_workspace_dir() -> Path:
    return Path.home() / "Documents" / APP_NAME


def settings_path() -> Path:
    return app_data_dir() / "settings.json"


def load_settings() -> AppSettings:
    path = settings_path()
    if not path.exists():
        workspace = default_workspace_dir()
        return AppSettings(workspace_dir=str(workspace), export_dir=str(workspace / "exports"))
    try:
        with path.open("r", encoding="utf-8") as file:
            raw = json.load(file)
    except (OSError, json.JSONDecodeError):
        workspace = default_workspace_dir()
        return AppSettings(workspace_dir=str(workspace), export_dir=str(workspace / "exports"))
    if not isinstance(raw, dict):
        workspace = default_workspace_dir()
        return AppSettings(workspace_dir=str(workspace), export_dir=str(workspace / "exports"))
    settings = AppSettings.from_dict(raw)
    if not settings.workspace_dir:
        settings.workspace_dir = str(default_workspace_dir())
    if not settings.export_dir:
        settings.export_dir = str(Path(settings.workspace_dir) / "exports")
    if settings.theme not in {"dark", "light"}:
        settings.theme = "dark"
    settings.recent_projects = _dedupe_existing(settings.recent_projects)
    return settings


def _dedupe_existing(paths: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for raw_path in paths:
        path = str(raw_path)
        key = path.casefold()
        if key in seen:
            continue
        seen.add(key)
        result.append(path)
    return result[:8]


def save_settings(settings: AppSettings) -> None:
    path = settings_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as file:
        json.dump(settings.to_dict(), file, ensure_ascii=False, indent=2)


def load_project(path: str | Path) -> ProjectData:
    project_path = Path(path)
    with project_path.open("r", encoding="utf-8") as file:
        raw = json.load(file)
    if not isinstance(raw, dict):
        return default_project()
    return ProjectData.from_dict(raw)


def save_project(project: ProjectData, path: str | Path) -> None:
    project_path = Path(path)
    project_path.parent.mkdir(parents=True, exist_ok=True)
    with project_path.open("w", encoding="utf-8") as file:
        json.dump(project.to_dict(), file, ensure_ascii=False, indent=2)


def default_project_path(workspace_dir: str | Path) -> Path:
    return Path(workspace_dir) / f"project{PROJECT_SUFFIX}"
