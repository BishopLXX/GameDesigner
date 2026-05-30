# Pixel Refiner Training Workflow

Pixel Refiner v2 is trained as a supervised image-to-image refiner.

The intended workflow is:

1. Collect legally usable high-quality pixel art targets.
2. Generate degraded or candidate inputs with GameDesigner.
3. Pair input and target into an auditable dataset.
4. Train the model.
5. Export ONNX into the local model package.
6. Load the package in the standalone local service.

## Allowed Sources

Only use sources that are clearly usable for training:

- self-authored assets
- commissioned or bought assets with training rights
- public-domain assets
- CC0 assets
- assets with explicit training permission

Do not use:

- random web images
- game screenshots
- fan art
- unlicensed sprite sheets
- assets with unclear or restrictive license terms

Keep a `licensed_sources.csv` file with per-source provenance and license notes.

## Dataset Layout

Default global location:

```text
D:\GameDesignerData\pixel_refiner\character_large_v1\
```

Layout:

```text
character_large_v1/
├─ licensed_sources.csv
├─ index.jsonl
├─ sources/
├─ raw/
├─ manifests/
├─ targets/
├─ inputs/
├─ generated_inputs/
├─ pairs/
└─ eval/
```

## Recommended Pair Types

For v2, focus on strong character pairs:

- character portraits
- side-scroller action characters
- large transparent character sprites

Keep target and input aligned in size and semantic structure. The first version should learn refinement, not scene reconstruction.

Do not force all collected targets down to 256x256 during collection. Keep the original image and export training variants later by bucket, crop, or resize policy.

Once targets are collected, build the full pair set in one pass:

```powershell
py -3 .\source\pixel_refiner_training_main.py build-pairs
```

That command walks every target PNG under `targets/`, generates multiple degraded inputs, and writes pair folders under `pairs/`.

To build the stronger pseudo-AI branch, use the configured image-generation provider from GameDesigner settings:

```powershell
py -3 .\source\pixel_refiner_training_main.py generate-ai-pseudo --source-id pndsndn_fc2 --limit 5 --request-timeout 90 --background auto
```

This sends true pixel-art targets as image references, saves normalized pseudo-AI inputs under `generated_inputs/`, and pairs them back to the true targets with `input_kind=ai_pseudo`. Keep the first runs small; increase `--limit` only after reviewing the pseudo inputs.

## Site Image Collection

For a user-owned FC2 pixel archive:

```powershell
py -3 .\tools\download_pixel_site_images.py
```

The downloader writes raw originals into:

```text
D:\GameDesignerData\pixel_refiner\character_large_v1\raw\pndsndn_fc2\
```

It also exports training candidate PNG files into:

```text
D:\GameDesignerData\pixel_refiner\character_large_v1\targets\pndsndn_fc2\
```

The latest crawl manifest is stored at:

```text
D:\GameDesignerData\pixel_refiner\character_large_v1\manifests\pndsndn_fc2_images_latest.jsonl
```

For CC0 256x256 character/monster targets from FreeGameSprites:

```powershell
$env:PYTHONPATH=".\source"
py -3 .\tools\collect_open_pixel_character_assets.py --max-pages-per-category 60 --max-assets 2500
```

The collector records the CC0 source metadata, applies strict character-like slug filtering, converts transparent PNG/WebP assets to PNG, and writes targets under:

```text
D:\GameDesignerData\pixel_refiner\character_large_v1\targets\freegamesprites_cc0
```

After collection, build procedural input/target pairs:

```powershell
py -3 .\source\pixel_refiner_training_main.py build-pairs --source-id freegamesprites_cc0
```

## CLI Usage

Run from source:

```powershell
$env:PYTHONPATH=".\source"
py -3.13 .\source\pixel_refiner_training_main.py summary
```

Evaluate dataset:

```powershell
py -3.13 .\source\pixel_refiner_training_main.py evaluate
```

Add a source record:

```powershell
$env:PYTHONPATH=".\source"
py -3.13 .\source\pixel_refiner_training_main.py add-source `
  --source-id kenney_pixel_platformer `
  --title "Pixel Platformer" `
  --author "Kenney" `
  --license CC0 `
  --license-url "https://creativecommons.org/publicdomain/zero/1.0/" `
  --allowed `
  --category sprite
```

Import a target:

```powershell
py -3.13 .\source\pixel_refiner_training_main.py import-target D:\assets\target.png --source-id kenney_pixel_platformer --category sprite
```

Generate inputs from a target:

```powershell
py -3.13 .\source\pixel_refiner_training_main.py generate-inputs D:\assets\target.png --source-id kenney_pixel_platformer --category sprite
```

Import an external/generated input:

```powershell
py -3.13 .\source\pixel_refiner_training_main.py import-input D:\assets\candidate.png --source-id kenney_pixel_platformer --category sprite --input-kind software_candidate
```

Import a real GameDesigner failure pair in one step:

```powershell
py -3.13 .\source\pixel_refiner_training_main.py import-software-pair `
  --input D:\assets\bad_candidate.png `
  --target D:\assets\true_pixel.png `
  --category character_portrait
```

This is the preferred way to add “software output -> true pixel art” examples. The two PNGs must be the same size.

Pair input and target:

```powershell
py -3.13 .\source\pixel_refiner_training_main.py make-pair --target D:\... \target.png --input D:\...\input.png --source-id kenney_pixel_platformer --category sprite --input-kind software_candidate
```

## Model Goal

The first model should:

- preserve alpha cleanly
- preserve silhouette
- keep palette count bounded
- remove fringe noise and fake smoothing
- keep pixel structure aligned to the final output size

It should not be expected to invent new composition or redraw semantic content from scratch.

## Train v2

Training is intentionally separate from the desktop app. Create a dedicated training environment under `D:\GameDesignerData`, install PyTorch there, and export an ONNX model package into:

```text
D:\GameDesignerData\pixel_refiner\models\pixel-refiner-v2\
```

Recommended first smoke run:

```powershell
$env:PYTHONPATH=".\source"
$trainPy = "D:\GameDesignerData\venvs\pixel-refiner-train\Scripts\python.exe"
& $trainPy .\source\pixel_refiner_training_main.py train `
  --epochs 1 `
  --steps-per-epoch 10 `
  --batch-size 2 `
  --patch-size 128 `
  --limit 64
```

Recommended v2 run for the current character dataset:

```powershell
.\train_pixel_refiner_v2.bat
```

The training command writes:

```text
pixel-refiner-v2/
├─ model_manifest.json
├─ training_config.json
├─ checkpoints/
└─ weights/
   └─ pixel_refiner_v2.onnx
```

The v2 trainer uses a medium U-Net/NAFNet-style architecture, patch size 256, palette/alpha loss terms, and manifest-driven palette/alpha cleanup in the service package.

## Train v3

V3 is the pixel-tile route for the problem where the image is readable at small size but the enlarged pixel logic is still weak.

Recommended v3 run:

```powershell
$env:PYTHONPATH=".\source"
$trainPy = "D:\GameDesignerData\venvs\pixel-refiner-train\Scripts\python.exe"
& $trainPy .\source\pixel_refiner_training_main.py train `
  --model-id pixel-refiner-v3 `
  --architecture pixel-tile-v3 `
  --output-dir "D:\GameDesignerData\pixel_refiner\models\pixel-refiner-v3" `
  --epochs 4 `
  --steps-per-epoch 900 `
  --batch-size 4 `
  --patch-size 64 `
  --internal-scale 2 `
  --tile-overlap 16 `
  --block-consistency-weight 0.20 `
  --features 64 `
  --device cuda
```

V3 differs from v2 in three places:

- Training samples 64x64 original-grid patches and nearest-upscales them to 128x128 before the model sees them.
- Loss includes direct 2x supervision, downscaled 1x supervision, and 2x2 block consistency.
- The service reads the manifest and performs tiled inference with overlap, then downscales each tile back to the original pixel grid before merging.

The exported package layout is:

```text
pixel-refiner-v3/
├─ model_manifest.json
├─ training_config.json
├─ checkpoints/
└─ weights/
   └─ pixel_refiner_v3.onnx
```

After export, run a service-level smoke test:

```powershell
$env:PYTHONPATH=".\source"
$trainPy = "D:\GameDesignerData\venvs\pixel-refiner-train\Scripts\python.exe"
& $trainPy .\source\pixel_refiner_training_main.py smoke-model
```

The current Windows training environment uses CUDA PyTorch:

```powershell
$trainPy = "D:\GameDesignerData\venvs\pixel-refiner-train\Scripts\python.exe"
& $trainPy -m pip install torch==2.12.0+cu126 --extra-index-url https://download.pytorch.org/whl/cu126
& $trainPy -m pip install -r .\source\pixel_refiner_training_requirements.txt
```
