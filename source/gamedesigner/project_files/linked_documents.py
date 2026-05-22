from __future__ import annotations

import shutil
from pathlib import Path

from ..storage import project_bundle_dir


LINKED_DOCS_DIR = "linked_docs"
SUPPORTED_LINK_FORMATS = {"md", "txt"}


def create_link_document(project_path: str | Path, title: str, file_format: str) -> str:
    extension = _normalized_format(file_format)
    folder = linked_documents_dir(project_path)
    folder.mkdir(parents=True, exist_ok=True)
    base_name = _safe_filename(title) or "link"
    path = _unique_path(folder, base_name, extension)
    path.write_text(_default_content(title, extension), encoding="utf-8")
    return f"{LINKED_DOCS_DIR}/{path.name}"


def read_link_document(project_path: str | Path, relative_path: str) -> str:
    path = resolve_link_document(project_path, relative_path)
    if not path.exists():
        return ""
    return path.read_text(encoding="utf-8")


def write_link_document(project_path: str | Path, relative_path: str, content: str) -> Path:
    path = resolve_link_document(project_path, relative_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return path


def rename_link_document(project_path: str | Path, relative_path: str, title: str) -> str:
    source = resolve_link_document(project_path, relative_path)
    extension = _normalized_format(source.suffix.lstrip(".") or Path(relative_path).suffix.lstrip("."))
    folder = linked_documents_dir(project_path)
    folder.mkdir(parents=True, exist_ok=True)
    target = _unique_path(folder, _safe_filename(title), extension, current=source)
    if source == target:
        if not target.exists():
            target.write_text(_default_content(title, extension), encoding="utf-8")
        return f"{LINKED_DOCS_DIR}/{target.name}"
    if source.exists():
        source.rename(target)
    else:
        target.write_text(_default_content(title, extension), encoding="utf-8")
    return f"{LINKED_DOCS_DIR}/{target.name}"


def delete_link_document(project_path: str | Path, relative_path: str) -> None:
    path = resolve_link_document(project_path, relative_path)
    if path.exists() and path.is_file():
        path.unlink()


def sync_link_document_copy(
    project_path: str | Path,
    relative_path: str,
    source_dir: str | Path,
) -> Path | None:
    if not source_dir:
        return None
    source = resolve_link_document(project_path, relative_path)
    if not source.exists() or not source.is_file():
        return None
    target = Path(source_dir) / relative_path
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, target)
    return target


def delete_link_document_copy(source_dir: str | Path, relative_path: str) -> None:
    if not source_dir:
        return
    path = Path(source_dir) / relative_path
    if path.exists() and path.is_file():
        path.unlink()


def resolve_link_document(project_path: str | Path, relative_path: str) -> Path:
    path = Path(relative_path)
    if path.is_absolute():
        return path
    return project_bundle_dir(project_path) / path


def linked_documents_dir(project_path: str | Path) -> Path:
    return project_bundle_dir(project_path) / LINKED_DOCS_DIR


def _default_content(title: str, file_format: str) -> str:
    if file_format == "md":
        return f"# {title.strip() or '新文档'}\n"
    return ""


def _normalized_format(file_format: str) -> str:
    text = file_format.lower().lstrip(".")
    return text if text in SUPPORTED_LINK_FORMATS else "md"


def _unique_path(folder: Path, base_name: str, extension: str, current: Path | None = None) -> Path:
    path = folder / f"{base_name}.{extension}"
    if current and path.resolve() == current.resolve():
        return path
    index = 2
    while path.exists():
        path = folder / f"{base_name}_{index}.{extension}"
        if current and path.resolve() == current.resolve():
            return path
        index += 1
    return path


def _safe_filename(name: str) -> str:
    cleaned = "".join("_" if char in '\\/:*?"<>|' else char for char in name.strip())
    return cleaned or "link"
