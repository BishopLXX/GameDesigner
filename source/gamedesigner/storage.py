from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .ai_presets import clean_ai_saved_connections
from .models import EDGE_STYLES, ProjectData, default_project


APP_NAME = "GameDesigner"
PROJECT_SUFFIX = ".gdc"
LEGACY_PROJECT_SUFFIX = ".gdesigner.json"
PROJECT_BUNDLE_SUFFIX = ".files"
CANVASES_DIR = "canvases"
TEMPLATES_FILE = "templates.json"
WINDOW_LAYOUTS_FILE = "window_layouts.json"


@dataclass
class AppSettings:
    workspace_dir: str = ""
    export_dir: str = ""
    last_project: str = ""
    theme: str = "dark"
    last_edge_style: str = "curve"
    ai_provider: str = "codex"
    ai_model: str = "gpt-5.4"
    ai_auth_mode: str = "official"
    ai_api_key: str = ""
    ai_base_url: str = ""
    ai_saved_connections: dict[str, dict[str, str]] = field(default_factory=dict)
    recent_projects: list[str] = field(default_factory=list)
    welcome_layout: dict[str, dict[str, float]] = field(default_factory=dict)
    welcome_recent_layouts: dict[str, dict[str, float]] = field(default_factory=dict)
    window_layouts: dict[str, dict[str, Any]] = field(default_factory=dict)
    export_canvas_csv_dialog: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, object]:
        return {
            "workspace_dir": self.workspace_dir,
            "export_dir": self.export_dir,
            "last_project": self.last_project,
            "theme": self.theme,
            "last_edge_style": self.last_edge_style,
            "ai_provider": self.ai_provider,
            "ai_model": self.ai_model,
            "ai_auth_mode": self.ai_auth_mode,
            "ai_api_key": self.ai_api_key,
            "ai_base_url": self.ai_base_url,
            "ai_saved_connections": self.ai_saved_connections,
            "recent_projects": self.recent_projects,
            "welcome_layout": self.welcome_layout,
            "welcome_recent_layouts": self.welcome_recent_layouts,
            "window_layouts": self.window_layouts,
            "export_canvas_csv_dialog": self.export_canvas_csv_dialog,
        }

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "AppSettings":
        recent = raw.get("recent_projects", [])
        if not isinstance(recent, list):
            recent = []
        last_edge_style = str(raw.get("last_edge_style", "curve") or "curve")
        if last_edge_style not in EDGE_STYLES:
            last_edge_style = "curve"
        ai_provider = str(raw.get("ai_provider", "codex") or "codex")
        if ai_provider not in {"codex", "claude"}:
            ai_provider = "codex"
        ai_auth_mode = str(raw.get("ai_auth_mode", "official") or "official")
        if ai_auth_mode not in {"official", "api_key"}:
            ai_auth_mode = "official"
        return cls(
            workspace_dir=str(raw.get("workspace_dir", "")),
            export_dir=str(raw.get("export_dir", "")),
            last_project=str(raw.get("last_project", "")),
            theme=str(raw.get("theme", "dark") or "dark"),
            last_edge_style=last_edge_style,
            ai_provider=ai_provider,
            ai_model=str(raw.get("ai_model", "gpt-5.4") or "gpt-5.4"),
            ai_auth_mode=ai_auth_mode,
            ai_api_key=str(raw.get("ai_api_key", "")),
            ai_base_url=str(raw.get("ai_base_url", "")),
            ai_saved_connections=clean_ai_saved_connections(raw.get("ai_saved_connections")),
            recent_projects=[str(item) for item in recent if item],
            welcome_layout=_coerce_layout_map(raw.get("welcome_layout")),
            welcome_recent_layouts=_coerce_layout_map(raw.get("welcome_recent_layouts")),
            window_layouts=_coerce_layout_map(raw.get("window_layouts"), include_geometry=True),
            export_canvas_csv_dialog=_coerce_export_canvas_csv_dialog(raw.get("export_canvas_csv_dialog")),
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
    if settings.last_edge_style not in EDGE_STYLES:
        settings.last_edge_style = "curve"
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


def _coerce_layout_map(raw: Any, *, include_geometry: bool = False) -> dict[str, dict[str, Any]]:
    if not isinstance(raw, dict):
        return {}
    result: dict[str, dict[str, Any]] = {}
    for key, value in raw.items():
        if not isinstance(key, str) or not isinstance(value, dict):
            continue
        layout = _coerce_layout(value, include_geometry=include_geometry)
        if layout:
            result[key] = layout
    return result


def _coerce_layout(raw: dict[str, Any], *, include_geometry: bool = False) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for name in ("x", "y", "width", "height"):
        value = raw.get(name)
        if isinstance(value, int | float):
            result[name] = float(value)
    geometry = raw.get("geometry")
    if include_geometry and isinstance(geometry, str) and geometry:
        result["geometry"] = geometry
    return result


def _coerce_export_canvas_csv_dialog(raw: Any) -> dict[str, Any]:
    if not isinstance(raw, dict):
        return {}
    projects_raw = raw.get("projects")
    if not isinstance(projects_raw, dict):
        return {}
    projects: dict[str, Any] = {}
    for project_key, state_raw in projects_raw.items():
        if not isinstance(project_key, str) or not isinstance(state_raw, dict):
            continue
        canvases_raw = state_raw.get("canvases")
        canvases: dict[str, Any] = {}
        if isinstance(canvases_raw, dict):
            for canvas_id, canvas_raw in canvases_raw.items():
                if not isinstance(canvas_id, str) or not isinstance(canvas_raw, dict):
                    continue
                canvases[canvas_id] = {
                    "canvas_name": str(canvas_raw.get("canvas_name") or ""),
                    "enabled": bool(canvas_raw.get("enabled", True)),
                    "sort_mode": str(canvas_raw.get("sort_mode") or "created"),
                    "target_folder": str(canvas_raw.get("target_folder") or ""),
                }
        projects[project_key] = {
            "folder": str(state_raw.get("folder") or ""),
            "canvases": canvases,
        }
    return {"projects": projects} if projects else {}


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


def project_window_layouts_path(project_path: str | Path) -> Path:
    return project_bundle_dir(project_path) / WINDOW_LAYOUTS_FILE


def load_project_window_layouts(project_path: str | Path) -> dict[str, dict[str, Any]]:
    path = project_window_layouts_path(project_path)
    try:
        with path.open("r", encoding="utf-8") as file:
            raw = json.load(file)
    except (OSError, json.JSONDecodeError):
        return {}
    return _coerce_layout_map(raw, include_geometry=True)


def save_project_window_layouts(project_path: str | Path, layouts: dict[str, dict[str, Any]]) -> None:
    path = project_window_layouts_path(project_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    cleaned = _coerce_layout_map(layouts, include_geometry=True)
    with path.open("w", encoding="utf-8") as file:
        json.dump(cleaned, file, ensure_ascii=False, indent=2)


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
