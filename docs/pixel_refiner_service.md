# Pixel Refiner Local Service

Pixel Refiner is a separate local service and model package used by GameDesigner to refine draft pixel art into final PNG candidates.

The GameDesigner desktop app does not bundle PyTorch, ONNX Runtime, or model weights. It only calls this service over local HTTP.

## Service Contract

Default URL:

```text
http://127.0.0.1:8765
```

Health check:

```http
GET /v1/health
```

Refine request:

```http
POST /v1/pixel/refine
Content-Type: application/json
```

```json
{
  "input_path": "D:/project/.gdc.files/ai_images/cache/canvas/pixel/128x128/source.png",
  "output_dir": "D:/project/.gdc.files/ai_images/cache/canvas/pixel/128x128",
  "target_size": "128x128",
  "alpha_mode": "preserve",
  "palette_limit": 128,
  "strength": 0.45,
  "return_candidates": 4,
  "model": {
    "id": "pixel-refiner-v2",
    "dir": "D:/GameDesignerData/pixel_refiner/models/pixel-refiner-v2"
  },
  "client": {
    "name": "GameDesigner",
    "protocol": "pixel-refiner-v1"
  }
}
```

Response:

```json
{
  "ok": true,
  "model": "pixel-refiner-v2",
  "outputs": [
    {
      "path": "D:/project/.gdc.files/ai_images/cache/canvas/pixel/128x128/refined_1.png",
      "label": "AI 像素修正 只清理"
    },
    {
      "path": "D:/project/.gdc.files/ai_images/cache/canvas/pixel/128x128/refined_2.png",
      "label": "AI 像素修正 保守"
    },
    {
      "path": "D:/project/.gdc.files/ai_images/cache/canvas/pixel/128x128/refined_3.png",
      "label": "AI 像素修正 强化"
    },
    {
      "path": "D:/project/.gdc.files/ai_images/cache/canvas/pixel/128x128/refined_4.png",
      "label": "AI 像素修正 标准"
    }
  ],
  "checks": {
    "transparent_png": true,
    "grid_aligned": true
  }
}
```

GameDesigner will then re-open the PNG, enforce final pixel-art PNG constraints, and add its own metadata before showing the candidate.

When the request comes from the GameDesigner right-click action, `input_path` is the selected pixel candidate shown in the cache list. The refiner no longer replaces it with the hidden high-resolution source copy, because that path caused size drift, alpha expansion, and soft redraw artifacts.

For single-output ONNX models, `return_candidates` is implemented by the service output layer. It runs one model inference and writes strength variants: `只清理`, `保守`, `强化`, and `标准`. This gives the desktop app several practical choices without requiring the ONNX graph to be stochastic.

## Run From Source

```powershell
py -3.13 -m pip install -r .\source\pixel_refiner_requirements.txt
$env:PYTHONPATH=".\source"
py -3.13 .\source\pixel_refiner_service_main.py --model-dir "D:\GameDesignerData\pixel_refiner\models\pixel-refiner-v2" --model-id pixel-refiner-v2
```

## Run With Visible Console

Use the standalone console when you want to see whether the service is running, whether requests are arriving, where outputs are written, and when training/retrain is active:

```powershell
.\start_pixel_refiner_service_window.bat
```

The console includes:

- service run/connect/stop controls
- `/v1/health` and `/v1/stats` monitoring
- dataset folder shortcuts and summary/evaluate output
- smoke training, full training, and retrain controls
- a Help tab explaining usage and the image-to-image refiner principle

GameDesigner can also collect real feedback pairs from the pixel result list. Right-click a bad-but-structured software candidate, choose `加入 Pixel Refiner 训练对...`, then select the matching true pixel-art PNG. The pair is stored as `input_kind=software_candidate`, which is the most valuable data for the next retrain. The two PNGs must have identical dimensions.

The service will start even when the model package is missing, but `/v1/health` will report `ok: false` and refine requests will fail clearly until a valid package is installed.

## Model Package

Default Windows location:

```text
D:\GameDesignerData\pixel_refiner\models\pixel-refiner-v2
```

Required layout:

```text
pixel-refiner-v2/
├─ model_manifest.json
└─ weights/
   └─ pixel_refiner_v2.onnx
```

Use `pixel_refiner_model_package.template/` as the starting layout.

## ONNX Interface

Inputs:

- `image`: required RGB tensor, float32, `NCHW`, values in `[0, 1]`
- `alpha`: optional alpha tensor, float32, `NCHW`, values in `[0, 1]`
- `strength`: optional float32 scalar

Output:

- First output tensor must be RGB or RGBA, float32, values in `[0, 1]`
- `NCHW` and `NHWC` are both accepted

If the model output is RGB and `alpha_mode` is `preserve`, the service preserves the input alpha.

The v2 service also applies a conservative pixel-art output layer after ONNX inference:

- `strength` is honored even when the ONNX graph does not expose a `strength` input.
- Dark outlines, high-contrast edges, and alpha boundaries receive lower model coverage so they are less likely to be washed out.
- Palette cleanup prefers the selected input image's color system before falling back to opaque-pixel quantization.
- Alpha is clamped after blending so transparent PNG output stays hard-edged.

## Safety Rule

This service intentionally has no procedural fallback image generation path. If dependencies or weights are missing, it fails instead of producing a fake final asset.
