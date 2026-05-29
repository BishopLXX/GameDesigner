from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from PIL import Image, ImageOps

from .errors import BackendUnavailableError, RequestValidationError
from .manifest import ModelManifest


@dataclass(frozen=True)
class RefineJob:
    input_path: Path
    output_dir: Path
    target_size: tuple[int, int]
    alpha_mode: str
    palette_limit: int
    strength: float
    return_candidates: int


@dataclass(frozen=True)
class RefineOutput:
    path: Path
    label: str


class PixelRefinerBackend:
    def refine(self, job: RefineJob) -> list[RefineOutput]:
        raise NotImplementedError


class OnnxPixelRefinerBackend(PixelRefinerBackend):
    def __init__(self, manifest: ModelManifest) -> None:
        self.manifest = manifest
        try:
            import numpy as np  # noqa: F401
            import onnxruntime as ort
        except ImportError as exc:
            raise BackendUnavailableError(
                "Pixel Refiner 模型包需要 onnxruntime 和 numpy。请安装独立服务依赖后再启动。"
            ) from exc
        providers = _available_providers(ort)
        try:
            self.session = ort.InferenceSession(str(manifest.weights), providers=providers)
        except Exception as exc:
            raise BackendUnavailableError(f"ONNX 模型加载失败：{manifest.weights}") from exc

    def refine(self, job: RefineJob) -> list[RefineOutput]:
        import numpy as np

        source_image = _load_rgba(job.input_path, job.target_size)
        rgb, alpha = _image_to_model_input(source_image)
        inputs = self._session_inputs(rgb, alpha, job)
        try:
            raw_outputs = self.session.run(None, inputs)
        except Exception as exc:
            raise BackendUnavailableError("ONNX 像素修正推理失败。") from exc
        if not raw_outputs:
            raise BackendUnavailableError("ONNX 模型没有返回输出张量。")
        tensor = np.asarray(raw_outputs[0])
        images = _tensor_to_images(tensor, alpha if job.alpha_mode == "preserve" else None)
        if not images:
            raise BackendUnavailableError("ONNX 模型输出无法转换为图片。")
        candidate_specs = _candidate_specs(images, job.return_candidates, job.strength)
        outputs: list[RefineOutput] = []
        for index, (candidate, variant_strength, variant_label) in enumerate(candidate_specs, start=1):
            candidate = _apply_strength_preserving_refine(
                source_image,
                candidate,
                strength=variant_strength,
                alpha_mode=job.alpha_mode,
            )
            if self.manifest.pixel_art_cleanup:
                candidate = _apply_pixel_art_cleanup(
                    candidate,
                    palette_limit=job.palette_limit or self.manifest.palette_limit,
                    alpha_threshold=self.manifest.alpha_threshold,
                    source_image=source_image,
                )
            path = job.output_dir / f"refined_{index}.png"
            candidate.save(path, format="PNG", optimize=True)
            outputs.append(RefineOutput(path=path, label=f"AI 像素修正 {variant_label}"))
        return outputs

    def _session_inputs(self, rgb, alpha, job: RefineJob) -> dict[str, object]:
        input_names = {item.name for item in self.session.get_inputs()}
        inputs: dict[str, object] = {}
        if "image" in input_names:
            inputs["image"] = rgb
        else:
            first = self.session.get_inputs()[0].name
            inputs[first] = rgb
        if "alpha" in input_names:
            inputs["alpha"] = alpha
        if "strength" in input_names:
            import numpy as np

            inputs["strength"] = np.asarray([job.strength], dtype=np.float32)
        return inputs


def build_backend(manifest: ModelManifest) -> PixelRefinerBackend:
    if manifest.runtime == "onnxruntime":
        return OnnxPixelRefinerBackend(manifest)
    raise BackendUnavailableError(f"暂不支持的模型运行时：{manifest.runtime}")


def parse_target_size(value: str) -> tuple[int, int]:
    text = str(value or "").strip().lower()
    if "x" not in text:
        raise RequestValidationError("target_size 必须是明确尺寸，例如 128x128。")
    width_text, height_text = text.split("x", 1)
    try:
        width = int(width_text)
        height = int(height_text)
    except ValueError as exc:
        raise RequestValidationError("target_size 必须是明确尺寸，例如 128x128。") from exc
    if width <= 0 or height <= 0 or width > 1024 or height > 1024:
        raise RequestValidationError("target_size 超出支持范围。")
    return width, height


def _available_providers(ort) -> list[str]:
    available = set(ort.get_available_providers())
    preferred = ["CUDAExecutionProvider", "DmlExecutionProvider", "CPUExecutionProvider"]
    providers = [provider for provider in preferred if provider in available]
    return providers or ["CPUExecutionProvider"]


def _load_rgba(path: Path, target_size: tuple[int, int]) -> Image.Image:
    try:
        with Image.open(path) as loaded:
            image = ImageOps.exif_transpose(loaded).convert("RGBA")
    except OSError as exc:
        raise RequestValidationError(f"无法读取输入图：{path}") from exc
    if image.size != target_size:
        image = ImageOps.contain(image, target_size, method=Image.Resampling.LANCZOS)
        canvas = Image.new("RGBA", target_size, (0, 0, 0, 0))
        canvas.alpha_composite(image, ((target_size[0] - image.width) // 2, (target_size[1] - image.height) // 2))
        image = canvas
    return image


def _image_to_model_input(image: Image.Image):
    import numpy as np

    array = np.asarray(image, dtype=np.float32) / 255.0
    rgb = array[:, :, :3].transpose(2, 0, 1)[None, :, :, :]
    alpha = array[:, :, 3:4].transpose(2, 0, 1)[None, :, :, :]
    return rgb.astype(np.float32), alpha.astype(np.float32)


def _tensor_to_images(tensor, preserved_alpha) -> list[Image.Image]:
    import numpy as np

    array = np.asarray(tensor)
    if array.ndim == 3:
        array = array[None, :, :, :]
    if array.ndim != 4:
        return []
    if array.shape[1] in {3, 4}:
        array = array.transpose(0, 2, 3, 1)
    images: list[Image.Image] = []
    for item in array:
        if item.shape[-1] < 3:
            continue
        rgb = np.clip(item[:, :, :3], 0.0, 1.0)
        if item.shape[-1] >= 4:
            alpha = np.clip(item[:, :, 3:4], 0.0, 1.0)
        elif preserved_alpha is not None:
            alpha = preserved_alpha[0].transpose(1, 2, 0)
        else:
            alpha = np.ones((*rgb.shape[:2], 1), dtype=np.float32)
        rgba = np.concatenate([rgb, alpha], axis=2)
        images.append(Image.fromarray((rgba * 255.0).round().astype(np.uint8), mode="RGBA"))
    return images


def _candidate_specs(
    images: list[Image.Image],
    return_candidates: int,
    strength: float,
) -> list[tuple[Image.Image, float, str]]:
    count = max(1, min(8, int(return_candidates)))
    if not images:
        return []
    if len(images) > 1:
        return [
            (candidate, max(0.0, min(1.0, float(strength))), str(index))
            for index, candidate in enumerate(images[:count], start=1)
        ]
    return [
        (images[0], variant_strength, variant_label)
        for variant_label, variant_strength in _strength_variants(strength, count)
    ]


def _strength_variants(strength: float, count: int) -> list[tuple[str, float]]:
    requested = max(0.0, min(1.0, float(strength)))
    if count <= 1:
        return [("标准", requested)]
    prefix = [
        ("只清理", 0.0),
        ("保守", max(0.0, requested - 0.20)),
        ("强化", min(1.0, requested + 0.20)),
        ("强修正", min(1.0, requested + 0.35)),
        ("轻修正", max(0.0, requested - 0.35)),
        ("极强", 1.0),
        ("极轻", 0.15),
    ]
    variants: list[tuple[str, float]] = []
    seen: set[float] = set()
    for label, value in prefix:
        rounded = round(value, 4)
        if rounded == round(requested, 4) or rounded in seen:
            continue
        variants.append((label, value))
        seen.add(rounded)
        if len(variants) >= count - 1:
            break
    variants.append(("标准", requested))
    return variants


def _apply_strength_preserving_refine(
    source_image: Image.Image,
    model_image: Image.Image,
    *,
    strength: float,
    alpha_mode: str,
) -> Image.Image:
    import numpy as np

    source = source_image.convert("RGBA")
    candidate = model_image.convert("RGBA")
    if candidate.size != source.size:
        candidate = candidate.resize(source.size, Image.Resampling.LANCZOS)
    strength = max(0.0, min(1.0, float(strength)))
    if strength <= 0.0:
        return source.copy()

    source_array = np.asarray(source, dtype=np.float32)
    candidate_array = np.asarray(candidate, dtype=np.float32)
    source_rgb = source_array[:, :, :3]
    candidate_rgb = candidate_array[:, :, :3]
    source_alpha = source_array[:, :, 3:4]
    candidate_alpha = candidate_array[:, :, 3:4]

    source_luma = (
        0.2126 * source_rgb[:, :, 0]
        + 0.7152 * source_rgb[:, :, 1]
        + 0.0722 * source_rgb[:, :, 2]
    )
    luma_gradient = np.zeros_like(source_luma)
    luma_gradient[:, 1:] = np.maximum(luma_gradient[:, 1:], np.abs(source_luma[:, 1:] - source_luma[:, :-1]))
    luma_gradient[:, :-1] = np.maximum(luma_gradient[:, :-1], np.abs(source_luma[:, 1:] - source_luma[:, :-1]))
    luma_gradient[1:, :] = np.maximum(luma_gradient[1:, :], np.abs(source_luma[1:, :] - source_luma[:-1, :]))
    luma_gradient[:-1, :] = np.maximum(luma_gradient[:-1, :], np.abs(source_luma[1:, :] - source_luma[:-1, :]))

    source_alpha_2d = source_alpha[:, :, 0]
    alpha_gradient = np.zeros_like(source_alpha_2d)
    alpha_gradient[:, 1:] = np.maximum(
        alpha_gradient[:, 1:],
        np.abs(source_alpha_2d[:, 1:] - source_alpha_2d[:, :-1]),
    )
    alpha_gradient[:, :-1] = np.maximum(
        alpha_gradient[:, :-1],
        np.abs(source_alpha_2d[:, 1:] - source_alpha_2d[:, :-1]),
    )
    alpha_gradient[1:, :] = np.maximum(
        alpha_gradient[1:, :],
        np.abs(source_alpha_2d[1:, :] - source_alpha_2d[:-1, :]),
    )
    alpha_gradient[:-1, :] = np.maximum(
        alpha_gradient[:-1, :],
        np.abs(source_alpha_2d[1:, :] - source_alpha_2d[:-1, :]),
    )

    dark_outline_protect = np.clip((96.0 - source_luma) / 64.0, 0.0, 1.0)
    edge_protect = np.clip((luma_gradient - 12.0) / 56.0, 0.0, 1.0)
    alpha_protect = np.clip(alpha_gradient / 128.0, 0.0, 1.0)
    protect = np.maximum(dark_outline_protect, np.maximum(edge_protect * 0.85, alpha_protect))
    effective_strength = strength * (1.0 - 0.65 * protect)

    mixed_rgb = source_rgb * (1.0 - effective_strength[:, :, None]) + candidate_rgb * effective_strength[:, :, None]
    mixed_luma = (
        0.2126 * mixed_rgb[:, :, 0]
        + 0.7152 * mixed_rgb[:, :, 1]
        + 0.0722 * mixed_rgb[:, :, 2]
    )
    luma_repair = (source_luma - mixed_luma)[:, :, None] * protect[:, :, None] * 0.45
    mixed_rgb = np.clip(mixed_rgb + luma_repair, 0.0, 255.0)

    if alpha_mode == "preserve":
        mixed_alpha = source_alpha
    else:
        mixed_alpha = source_alpha * (1.0 - strength) + candidate_alpha * strength
    rgba = np.concatenate([mixed_rgb, mixed_alpha], axis=2)
    return Image.fromarray(np.rint(np.clip(rgba, 0.0, 255.0)).astype(np.uint8), mode="RGBA")


def _apply_pixel_art_cleanup(
    image: Image.Image,
    *,
    palette_limit: int,
    alpha_threshold: int,
    source_image: Image.Image | None = None,
) -> Image.Image:
    rgba = image.convert("RGBA")
    alpha_threshold = max(0, min(255, int(alpha_threshold)))
    if alpha_threshold > 0:
        alpha = rgba.getchannel("A").point(lambda value: 255 if value >= alpha_threshold else 0)
        rgba.putalpha(alpha)
    palette_limit = max(0, min(256, int(palette_limit or 0)))
    if palette_limit <= 0:
        return rgba
    source_palette = _palette_from_source_image(
        source_image,
        palette_limit=palette_limit,
        alpha_threshold=alpha_threshold,
    )
    if source_palette:
        return _snap_to_palette(rgba, source_palette, alpha_threshold=alpha_threshold)
    return _quantize_opaque_pixels(rgba, palette_limit=palette_limit, alpha_threshold=alpha_threshold)


def _palette_from_source_image(
    source_image: Image.Image | None,
    *,
    palette_limit: int,
    alpha_threshold: int,
) -> list[tuple[int, int, int]]:
    if source_image is None:
        return []
    source = source_image.convert("RGBA")
    pixels = _flatten_pixels(source)
    opaque_rgb = [
        tuple(pixel[:3])
        for pixel in pixels
        if len(pixel) >= 4 and pixel[3] >= alpha_threshold
    ]
    if not opaque_rgb:
        return []
    unique = sorted(set(opaque_rgb))
    if len(unique) <= palette_limit:
        return unique
    return _quantized_palette(opaque_rgb, palette_limit)


def _snap_to_palette(
    image: Image.Image,
    palette: list[tuple[int, int, int]],
    *,
    alpha_threshold: int,
) -> Image.Image:
    import numpy as np

    if not palette:
        return image
    array = np.asarray(image.convert("RGBA"), dtype=np.uint8).copy()
    opaque_mask = array[:, :, 3] >= alpha_threshold
    if not bool(opaque_mask.any()):
        return image
    palette_array = np.asarray(palette, dtype=np.int32)
    opaque_rgb = array[:, :, :3][opaque_mask].astype(np.int32)
    snapped = np.empty_like(opaque_rgb, dtype=np.uint8)
    chunk_size = 8192
    for start in range(0, opaque_rgb.shape[0], chunk_size):
        chunk = opaque_rgb[start : start + chunk_size]
        distances = ((chunk[:, None, :] - palette_array[None, :, :]) ** 2).sum(axis=2)
        snapped[start : start + chunk_size] = palette_array[np.argmin(distances, axis=1)].astype(np.uint8)
    rgb_view = array[:, :, :3]
    rgb_view[opaque_mask] = snapped
    rgba_view = array[:, :, :4]
    rgba_view[~opaque_mask] = 0
    alpha_view = array[:, :, 3]
    alpha_view[opaque_mask] = 255
    return Image.fromarray(array, mode="RGBA")


def _quantize_opaque_pixels(
    image: Image.Image,
    *,
    palette_limit: int,
    alpha_threshold: int,
) -> Image.Image:
    rgba = image.convert("RGBA")
    pixels = _flatten_pixels(rgba)
    opaque_rgb = [
        tuple(pixel[:3])
        for pixel in pixels
        if len(pixel) >= 4 and pixel[3] >= alpha_threshold
    ]
    if len(set(opaque_rgb)) <= palette_limit:
        return rgba
    palette = _quantized_palette(opaque_rgb, palette_limit)
    if not palette:
        return rgba
    return _snap_to_palette(rgba, palette, alpha_threshold=alpha_threshold)


def _quantized_palette(colors: list[tuple[int, int, int]], limit: int) -> list[tuple[int, int, int]]:
    if not colors:
        return []
    swatch = Image.new("RGB", (len(colors), 1))
    swatch.putdata(colors)
    quantized = swatch.quantize(
        colors=max(1, int(limit)),
        method=Image.Quantize.MEDIANCUT,
        dither=Image.Dither.NONE,
    ).convert("RGB")
    return sorted(set(tuple(pixel[:3]) for pixel in _flatten_pixels(quantized)))


def _flatten_pixels(image: Image.Image) -> list[tuple[int, ...]]:
    if hasattr(image, "get_flattened_data"):
        return [tuple(pixel) for pixel in image.get_flattened_data()]
    return [tuple(pixel) for pixel in image.getdata()]
