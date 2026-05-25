# Agent Rules

## UI Direction

- Keep the canvas as the primary workspace. Do not add side panels, helper text, or floating controls unless the user explicitly asks for them.
- Use a compact, Apple-like desktop visual language: restrained colors, clean spacing, subtle borders, rounded corners, and quiet controls.
- Avoid large or scattered buttons. Put persistent commands in the top category row, and put context-specific commands in right-click menus.
- Prefer right-click workflows for creation and editing actions on the canvas, nodes, edges, and node editor sub-cards.
- Keep toolbars shallow and dense. If a control is not used constantly, place it in a menu instead of taking canvas space.
- Dialogs should preview the actual object being edited whenever possible, with properties beside the preview.
- When adding UI, check that it does not reduce usable canvas area or make the interface feel like a form-heavy admin tool.

## Interaction Defaults

- Blank canvas right-click creates or imports node content.
- Node right-click exposes node-specific actions only.
- Edge right-click exposes edge-specific actions only.
- In the node editor, blank preview-area right-click adds sub-cards, and selected sub-card right-click exposes sub-card actions.
- Keyboard shortcuts should remain available, but visible UI should stay compact.

## Persistence Defaults

- User-facing window and dialog settings must persist across close and restart unless explicitly temporary.
- Persist changed directories, checkbox selections, sort modes, per-item dialog choices, and window geometry; restore them the next time the same workflow opens.

## Project Structure

- Do not grow the project into one giant file. New features should be split into focused modules or subfolders by responsibility.
- The `.gdc` file is a project manifest, not a dumping ground for every canvas, template, and document body.
- Project-owned data that can grow large belongs in the adjacent `.gdc.files` folder, with clear subfolders such as `canvases/`, `linked_docs/`, and focused JSON/text files.
- UI dialogs that are not core window orchestration should live outside `app.py`; prefer dedicated modules under `ui/`.
- File and persistence helpers should live outside UI code; prefer dedicated modules under `project_files/` or storage-focused modules.
