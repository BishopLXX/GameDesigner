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
- When creating a child or follow-up node from a selected node, place the new node to the right of the parent and connect parent to child.
- In the node editor, blank preview-area right-click adds sub-cards, and selected sub-card right-click exposes sub-card actions.
- Keyboard shortcuts should remain available, but visible UI should stay compact.

## AI Design Defaults

- AI must distinguish copywriting design from iteration design. Copywriting design describes gameplay, systems, rules, flow, goals, tone, and player experience in natural language.
- Iteration design creates variants, types, numbers, growth stages, unlocks, drops, balancing changes, and other structured extensions.
- When reference nodes, canvas rules, notes, or existing canvas content are present, iteration must be based on the existing copywriting and content before extending or differentiating it.
- When iterating a new color, type, stage, or variant and the canvas already has a same-kind node, use that existing node as the reference. Preserve its node size, field names, field count, visual card layout, and design logic; only change the variant-specific content.
- When iterating a blueprint group, module, mod, or any same-kind group of nodes, treat the reference group as a structural blueprint. Preserve the group bounds, member count, member ordering, relative node positions, field names, field count, visual card layout, internal edge topology, edge labels, edge style, and manual route points unless the user explicitly asks to rearrange, redesign the layout, or change connections.
- When a new blueprint group is based on an existing same-kind group, use the existing group as the reference and clone its structure first; only change variant-specific titles, copywriting, numbers, and naming. Do not flatten the reference into unrelated standalone nodes.
- AI canvas mutations must go through the internal validated tool layer. Prefer explicit tool calls such as create_node, update_node, create_edge, update_edge_label, query_canvas, search_nodes, and validate_actions over ad hoc JSON.
- Default normal nodes should use the Label node structure: a simple title, blank icon, and exactly one long-text description card.

## Persistence Defaults

- User-facing window and dialog settings must persist across close and restart unless explicitly temporary.
- Persist changed directories, checkbox selections, sort modes, per-item dialog choices, and window geometry; restore them the next time the same workflow opens.

## Project Structure

- Do not grow the project into one giant file. New features should be split into focused modules or subfolders by responsibility.
- The `.gdc` file is a project manifest, not a dumping ground for every canvas, template, and document body.
- Project-owned data that can grow large belongs in the adjacent `.gdc.files` folder, with clear subfolders such as `canvases/`, `linked_docs/`, and focused JSON/text files.
- UI dialogs that are not core window orchestration should live outside `app.py`; prefer dedicated modules under `ui/`.
- File and persistence helpers should live outside UI code; prefer dedicated modules under `project_files/` or storage-focused modules.

## Pixel Refiner Console And Training

- Pixel Refiner is a separate local service, model package, dataset pipeline, and control console. Do not fold its training or service controls into the main canvas UI unless the user explicitly asks.
- The visible control app is `source/pixel_refiner_service_window.py`. Start it from source with `start_pixel_refiner_service_window.bat`; build a standalone GUI with `build_pixel_refiner_console.bat`.
- Keep the console organized around the existing tabs: `service`, `dataset`, `training/retrain`, and `help`. The Help tab should stay current whenever service behavior, dataset layout, training commands, or model limitations change.
- Default service URL is `http://127.0.0.1:8765`. The service exposes `GET /v1/health`, `GET /v1/stats`, and `POST /v1/pixel/refine`.
- Default dataset root is `D:\GameDesignerData\pixel_refiner\character_large_v1`. Important subfolders are `targets`, `generated_inputs`, `pairs`, `raw`, `manifests`, and `eval`.
- Default model package root is `D:\GameDesignerData\pixel_refiner\models\pixel-refiner-v2`. The runtime package must contain `model_manifest.json` and `weights/pixel_refiner_v2.onnx` plus any ONNX external data file. Keep old `D:\GameDesignerData\models\...` paths as legacy-only and migrate them into `D:\GameDesignerData\pixel_refiner\models\...`.
- Default training Python is `D:\GameDesignerData\venvs\pixel-refiner-train\Scripts\python.exe`. Use this venv for PyTorch training instead of installing training dependencies into the main app runtime.
- The training CLI is `source/pixel_refiner_training_main.py`. Use `summary` and `evaluate` to inspect data, `train` for full training, `smoke-model` for package/service validation, and `generate-ai-pseudo` only when intentionally expanding pseudo-AI pairs. `train_pixel_refiner_v2.bat` is the default full training launcher.
- Open/authorized dataset collection lives in `source/gamedesigner/pixel_refiner_open_assets.py` with the wrapper `tools/collect_open_pixel_character_assets.py`. Its default FreeGameSprites mode records CC0 provenance, uses strict character-like slug filtering, and writes targets under `D:\GameDesignerData\pixel_refiner\character_large_v1\targets\freegamesprites_cc0`.
- After any train or retrain that overwrites the model package, restart the Pixel Refiner service so ONNX Runtime reloads the new weights.
- Always verify Pixel Refiner changes with targeted tests, at minimum `py -3 -m unittest tests.test_pixel_refiner_service tests.test_pixel_refiner_dataset tests.test_pixel_refiner_ai_pseudo tests.test_pixel_site_downloader tests.test_pixel_refiner_open_assets` when those areas are touched.
- Pixel Refiner v2 is a supervised U-Net/NAFNet-style image-to-image ONNX refiner with palette/alpha cleanup, not a from-scratch image generator. If outputs remain soft, hazy, or non-pixel-accurate, first inspect `/v1/stats` to confirm requests are reaching the service, then improve real software-candidate pairs, sampling weights, loss/post-processing, or retraining strategy instead of assuming the service did not run.
