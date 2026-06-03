from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from .paths import game_designer_data_root


GAMEDESIGNER_IMAGEGEN_SKILL_NAME = "gamedesigner-imagegen"


@dataclass(frozen=True)
class CodexSkillExportResult:
    skill_name: str
    codex_skill_dir: Path
    portable_skill_dir: Path
    legacy_skill_dir: Path | None
    written_dirs: tuple[Path, ...]


def install_gamedesigner_imagegen_codex_skill(
    *,
    home: Path | None = None,
    data_root: Path | None = None,
    include_legacy_agents: bool = True,
) -> CodexSkillExportResult:
    home_dir = Path(home).expanduser() if home is not None else Path.home()
    data_dir = Path(data_root).expanduser() if data_root is not None else game_designer_data_root()
    codex_skill_dir = home_dir / ".codex" / "skills" / GAMEDESIGNER_IMAGEGEN_SKILL_NAME
    portable_skill_dir = data_dir / "codex_skills" / GAMEDESIGNER_IMAGEGEN_SKILL_NAME
    legacy_skill_dir: Path | None = None

    targets = [codex_skill_dir, portable_skill_dir]
    legacy_root = home_dir / ".agents" / "skills"
    if include_legacy_agents and legacy_root.exists():
        legacy_skill_dir = legacy_root / GAMEDESIGNER_IMAGEGEN_SKILL_NAME
        targets.append(legacy_skill_dir)

    written: list[Path] = []
    for target in targets:
        write_gamedesigner_imagegen_skill(target)
        written.append(target)

    return CodexSkillExportResult(
        skill_name=GAMEDESIGNER_IMAGEGEN_SKILL_NAME,
        codex_skill_dir=codex_skill_dir,
        portable_skill_dir=portable_skill_dir,
        legacy_skill_dir=legacy_skill_dir,
        written_dirs=tuple(written),
    )


def write_gamedesigner_imagegen_skill(target_dir: Path) -> None:
    target = Path(target_dir).expanduser()
    (target / "agents").mkdir(parents=True, exist_ok=True)
    (target / "SKILL.md").write_text(_skill_markdown(), encoding="utf-8", newline="\n")
    (target / "agents" / "openai.yaml").write_text(_openai_yaml(), encoding="utf-8", newline="\n")


def _skill_markdown() -> str:
    return """---
name: gamedesigner-imagegen
description: Generates bitmap game assets through the GameDesigner global agent-imagegen CLI, using GameDesigner AI image settings as fallback and post-processing final PNG size/compression. Use when any project needs generated icons, item art, skill art, UI frames, 9-slice chrome, slot/panel/bar art, VFX masks, textures, sprites, reference redraws, or when the user asks to draw/create/generate a game image asset.
---

# GameDesigner ImageGen

## Required Rule

Use `%USERPROFILE%\\.agent-imagegen\\bin\\agent-imagegen.cmd` for missing bitmap game assets. If the shell PATH is refreshed, `agent-imagegen` may also be available.

Do not ship placeholders, emoji, Unity built-in sprites, generated `Texture2D` pixels, `.asset` texture stand-ins, or runtime-created images as final art.

The command reads config in this order:

1. CLI arguments.
2. `%USERPROFILE%/.agent-imagegen/config.json`.
3. GameDesigner fallback: `D:/GameDesignerData/config/settings.json` or `%GAMEDESIGNER_DATA_DIR%/config/settings.json`.
4. `OPENAI_API_KEY`.

Never print or expose API keys.

If `%USERPROFILE%\\.agent-imagegen\\bin\\agent-imagegen.cmd` is missing, report the missing path and ask the user to install or update GameDesigner/agent-imagegen. Do not replace the asset with procedural art.

## Commands

Item, skill, HUD button, inventory icon, or small readable asset:

```powershell
& "$env:USERPROFILE\\.agent-imagegen\\bin\\agent-imagegen.cmd" gen --prompt "transparent PNG game item icon, centered, readable at small size, no text, no watermark, ..." --output "Assets/GameRes/Item/Icons/MyIcon.png" --size 1024x1024 --final-size 256x256 --format png --png-optimize
```

Larger square UI art:

```powershell
& "$env:USERPROFILE\\.agent-imagegen\\bin\\agent-imagegen.cmd" gen --prompt "transparent PNG game UI art, ..." --output "Assets/GameRes/UI/Textures/MyArt.png" --size 1024x1024 --final-size 512x512 --format png --png-optimize
```

9-slice frame, panel, slot, bar, or HUD chrome:

```powershell
& "$env:USERPROFILE\\.agent-imagegen\\bin\\agent-imagegen.cmd" gen --prompt "transparent PNG UI frame, 9-slice safe, stable corners, stretchable center and edges, no text, no icon, ..." --output "Assets/GameRes/UI/Textures/MyFrame.png" --size 1024x1024 --max-edge 1024 --format png --png-optimize
```

Reference redraw:

```powershell
& "$env:USERPROFILE\\.agent-imagegen\\bin\\agent-imagegen.cmd" gen --prompt "redraw this as a clean transparent game UI icon, preserve subject silhouette, no text" --reference "Assets/Refs/source.png" --output "Assets/GameRes/UI/Textures/MyIcon.png" --final-size 256x256 --format png --png-optimize
```

## Size Policy

- Inventory/item/skill/button icons: final project PNG is `256x256`.
- Larger square UI art: final project PNG is usually `512x512`.
- Frames, panels, bars, and 9-slice chrome: keep aspect ratio and use `--max-edge 1024` unless the UI needs a different resolution.
- Generated source can be larger for quality, but the file bound in the project must be resized/compressed before use.

## Engine Import

After generation, import the PNG as the engine's normal 2D sprite/texture asset. For Unity, use `Sprite (2D and UI)`; for 9-slice UI chrome, set Sprite borders and use `Image.Type.Sliced`.

If the API/config is missing, report the exact missing config path and stop.
"""


def _openai_yaml() -> str:
    return """interface:
  display_name: "GameDesigner ImageGen"
  short_description: "Generate game PNG assets through GameDesigner"
  default_prompt: "Use $gamedesigner-imagegen to generate a transparent game item icon PNG for my project."
policy:
  allow_implicit_invocation: true
"""
