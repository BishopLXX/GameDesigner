from __future__ import annotations

import copy
from dataclasses import dataclass, field
from typing import Any

from .models import ProjectData


@dataclass
class ProjectSnapshot:
    project: dict[str, Any]
    canvas_id: str = ""


@dataclass
class ProjectHistory:
    entries: list[ProjectSnapshot] = field(default_factory=list)
    index: int = -1
    clean_index: int | None = None

    def initialize(self, project: ProjectData, canvas_id: str = "", clean: bool = False) -> None:
        snapshot = self._snapshot(project, canvas_id)
        self.entries = [snapshot]
        self.index = 0
        self.clean_index = 0 if clean else None

    def record(self, project: ProjectData, canvas_id: str = "") -> bool:
        snapshot = self._snapshot(project, canvas_id)
        if self.index >= 0 and self.entries[self.index].project == snapshot.project:
            self.entries[self.index].canvas_id = canvas_id
            return False
        self.entries = self.entries[: self.index + 1]
        self.entries.append(snapshot)
        self.index = len(self.entries) - 1
        if self.clean_index is not None and self.clean_index >= len(self.entries):
            self.clean_index = None
        return True

    def can_undo(self) -> bool:
        return self.index > 0

    def can_redo(self) -> bool:
        return 0 <= self.index < len(self.entries) - 1

    def undo(self) -> ProjectSnapshot | None:
        if not self.can_undo():
            return None
        self.index -= 1
        return self.current()

    def redo(self) -> ProjectSnapshot | None:
        if not self.can_redo():
            return None
        self.index += 1
        return self.current()

    def mark_clean(self) -> None:
        if self.index >= 0:
            self.clean_index = self.index

    def is_dirty(self) -> bool:
        return self.clean_index != self.index

    def current(self) -> ProjectSnapshot | None:
        if not (0 <= self.index < len(self.entries)):
            return None
        return copy.deepcopy(self.entries[self.index])

    def _snapshot(self, project: ProjectData, canvas_id: str) -> ProjectSnapshot:
        return ProjectSnapshot(
            project=copy.deepcopy(project.to_dict()),
            canvas_id=canvas_id,
        )
