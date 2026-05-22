from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .models import ProjectData, default_project


APP_NAME = "GameDesigner"
PROJECT_SUFFIX = ".gdc"
LEGACY_PROJECT_SUFFIX = ".gdesigner.json"
PROJECT_BUNDLE_SUFFIX = ".files"
CANVASES_DIR = "canvases"
TEMPLATES_FILE = "templates.json"


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
    raw = _hydrate_split_project(project_path, raw)
    return ProjectData.from_dict(raw)


def save_project(project: ProjectData, path: str | Path) -> None:
    project_path = Path(path)
    project_path.parent.mkdir(parents=True, exist_ok=True)
    project.ensure_canvas_structure()
    bundle_dir = project_bundle_dir(project_path)
    canvases_dir = bundle_dir / CANVASES_DIR
    canvases_dir.mkdir(parents=True, exist_ok=True)
    for canvas in project.canvases:
        with (canvases_dir / f"{_safe_path_name(canvas.id)}.json").open("w", encoding="utf-8") as file:
            json.dump(canvas.to_dict(), file, ensure_ascii=False, indent=2)
    _remove_stale_canvas_files(canvases_dir, {f"{_safe_path_name(canvas.id)}.json" for canvas in project.canvases})
    with (bundle_dir / TEMPLATES_FILE).open("w", encoding="utf-8") as file:
        json.dump([template.to_dict() for template in project.templates], file, ensure_ascii=False, indent=2)
    manifest = _project_manifest(project, project_path)
    with project_path.open("w", encoding="utf-8") as file:
        json.dump(manifest, file, ensure_ascii=False, indent=2)


def default_project_path(workspace_dir: str | Path) -> Path:
    return Path(workspace_dir) / f"project{PROJECT_SUFFIX}"


def project_bundle_dir(project_path: str | Path) -> Path:
    path = Path(project_path)
    return path.parent / f"{path.name}{PROJECT_BUNDLE_SUFFIX}"


def _project_manifest(project: ProjectData, project_path: Path) -> dict[str, Any]:
    bundle_dir = project_bundle_dir(project_path)
    return {
        "file_format": "GameDesigner.GDC",
        "schema_version": 1,
        "name": project.name,
        "source_dir": project.source_dir,
        "output_dir": project.output_dir,
        "copy_link_docs_to_source": project.copy_link_docs_to_source,
        "root_canvas_id": project.root_canvas_id,
        "storage": {
            "mode": "split_bundle",
            "bundle": bundle_dir.name,
            "canvases_dir": CANVASES_DIR,
            "templates": TEMPLATES_FILE,
        },
        "canvas_refs": [
            {
                "id": canvas.id,
                "name": canvas.name,
                "path": f"{CANVASES_DIR}/{_safe_path_name(canvas.id)}.json",
            }
            for canvas in project.canvases
        ],
    }


def _hydrate_split_project(project_path: Path, raw: dict[str, Any]) -> dict[str, Any]:
    storage = raw.get("storage")
    if not isinstance(storage, dict) or storage.get("mode") != "split_bundle":
        return raw
    bundle_name = str(storage.get("bundle") or f"{project_path.name}{PROJECT_BUNDLE_SUFFIX}")
    bundle_dir = project_path.parent / bundle_name
    canvases: list[dict[str, Any]] = []
    refs = raw.get("canvas_refs", [])
    if isinstance(refs, list):
        for item in refs:
            if not isinstance(item, dict):
                continue
            relative = str(item.get("path") or "")
            if not relative:
                continue
            canvas_path = bundle_dir / relative
            try:
                with canvas_path.open("r", encoding="utf-8") as file:
                    canvas_raw = json.load(file)
            except (OSError, json.JSONDecodeError):
                continue
            if isinstance(canvas_raw, dict):
                canvases.append(canvas_raw)
    templates: list[dict[str, Any]] = []
    templates_ref = str(storage.get("templates") or TEMPLATES_FILE)
    try:
        with (bundle_dir / templates_ref).open("r", encoding="utf-8") as file:
            templates_raw = json.load(file)
    except (OSError, json.JSONDecodeError):
        templates_raw = []
    if isinstance(templates_raw, list):
        templates = [item for item in templates_raw if isinstance(item, dict)]
    hydrated = dict(raw)
    hydrated["canvases"] = canvases
    hydrated["templates"] = templates
    return hydrated


def _remove_stale_canvas_files(canvases_dir: Path, expected_names: set[str]) -> None:
    for path in canvases_dir.glob("*.json"):
        if path.name not in expected_names:
            path.unlink(missing_ok=True)


def _safe_path_name(name: str) -> str:
    cleaned = "".join("_" if char in '\\/:*?"<>|' else char for char in name.strip())
    return cleaned or "item"
