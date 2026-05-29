# GameDesigner Pixel Refiner v1 Model Package

This folder is a package template for the external local Pixel Refiner service.

Required layout:

```text
pixel-refiner-v1/
├─ model_manifest.json
└─ weights/
   └─ pixel_refiner_v1.onnx
```

`model_manifest.json` is read by the standalone service, not by the main GameDesigner UI.
The service refuses to run refinement when the ONNX weight file is missing, because final
project image assets must come from a real trained/generated model path rather than a
procedural placeholder.

The default install location on Windows is:

```text
D:\GameDesignerData\pixel_refiner\models\pixel-refiner-v1
```

The ONNX model is expected to accept an RGB image tensor named `image` in `NCHW` float32
format, with values in `[0, 1]`. Optional inputs:

- `alpha`: `NCHW` float32 alpha mask in `[0, 1]`
- `strength`: one float32 scalar

The first output tensor should be RGB or RGBA in either `NCHW` or `NHWC`, values in `[0, 1]`.
If the output has no alpha channel and `alpha_mode` is `preserve`, the service preserves the
input alpha mask.
