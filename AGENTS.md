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
