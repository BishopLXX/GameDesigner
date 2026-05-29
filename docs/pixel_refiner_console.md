# Pixel Refiner Console

Pixel Refiner Console is the standalone control window for the local Pixel Refiner model package. It is separate from the main GameDesigner app.

Start from source:

```powershell
.\start_pixel_refiner_service_window.bat
```

Build a standalone GUI executable:

```powershell
.\build_pixel_refiner_console.bat
```

The console has four tabs:

- `服务`: run, connect, stop, and inspect the local service.
- `数据集`: open the dataset folders and view summary/evaluation output.
- `训练 / Retrain`: run smoke training, full training, or overwrite-retrain the current model package.
- `Help`: explains usage, dataset folders, request flow, model inputs/outputs, and current v2 limitations.

Default service:

```text
http://127.0.0.1:8765
```

Default dataset:

```text
D:\GameDesignerData\pixel_refiner\character_large_v1
```

Default model package:

```text
D:\GameDesignerData\pixel_refiner\models\pixel-refiner-v2
```

Default training Python:

```text
D:\GameDesignerData\venvs\pixel-refiner-train\Scripts\python.exe
```

Important: after training or retraining, restart the service so it reloads the newly exported ONNX weights.

## Training Tab Parameters

- `训练轮数`: how many full training rounds to run. More rounds can learn more style but can also overfit.
- `每轮步数`: how many optimizer updates happen in each epoch. This is the strongest control over training time.
- `批量大小`: how many cropped patches are used per optimizer update. Larger batches use more VRAM.
- `训练裁剪尺寸`: the crop size sampled from large images. `128` learns local edges and color cleanup; `256` learns more structure and uses more VRAM.
- `验证批次数`: how many validation batches are measured at the end of each epoch.
- `样本上限（0=全量）`: `0` uses the full dataset; small values are for quick experiments.
- `训练设备`: `显卡 CUDA（推荐）`, `自动选择`, or `CPU（很慢）`.

## Model Principle

The current v2 model is not a diffusion model and does not start from noise or a flat color. It is a supervised image-to-image refiner:

```text
input.png -> ONNX refiner -> refined RGB
target.png -> supervised target
```

The current default model is a medium U-Net/NAFNet-style refiner. It predicts corrected RGB from input RGB and alpha, then the service applies manifest-driven palette and alpha cleanup. Future versions should add more real software-candidate pairs and stronger edge/outline losses rather than relying only on procedural degradation.

## Current V2 Dataset And Launchers

Current local dataset after the 2026-05-29 expansion:

- `targets`: 2706
- `pairs`: 14627
- `FreeGameSprites CC0 strict character targets`: 1790
- `ai_pseudo` pairs: 324

Useful commands:

```powershell
py -3 .\tools\collect_open_pixel_character_assets.py --max-pages-per-category 60 --max-assets 2500
py -3 .\source\pixel_refiner_training_main.py build-pairs --source-id freegamesprites_cc0
.\train_pixel_refiner_v2.bat
```
