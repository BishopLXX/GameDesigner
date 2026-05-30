from __future__ import annotations

import json
import math
import random
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Sequence

import numpy as np
from PIL import Image, ImageOps

from .paths import pixel_refiner_model_dir
from .pixel_refiner_dataset import PixelRefinerPairRecord, dataset_dir, load_pair_records


MODEL_ID = "pixel-refiner-v1"
V2_MODEL_ID = "pixel-refiner-v2"
V3_MODEL_ID = "pixel-refiner-v3"
V4_MODEL_ID = "pixel-refiner-v4"
V41_MODEL_ID = "pixel-refiner-v4.1-real-failures"
MODEL_VERSION = "0.1.0"
MODEL_FILENAME = "pixel_refiner_v1.onnx"
V2_MODEL_FILENAME = "pixel_refiner_v2.onnx"
V3_MODEL_FILENAME = "pixel_refiner_v3.onnx"
V4_MODEL_FILENAME = "pixel_refiner_v4.onnx"
INPUT_KIND_SAMPLE_WEIGHTS = {
    "software_candidate": 16.0,
    "ai_pseudo": 8.0,
    "soft_bilinear": 1.0,
    "alpha_fringe": 1.0,
    "palette_drift": 1.0,
    "lost_detail": 1.0,
    "dirty_outline": 1.0,
}
V41_SOFTWARE_CANDIDATE_WEIGHT = 32.0
V41_AI_PSEUDO_WEIGHT = 16.0
CATEGORY_SAMPLE_WEIGHTS = {
    "character_portrait": 2.0,
    "character_sprite": 1.5,
    "side_scroller_action_character": 1.0,
}


@dataclass(frozen=True)
class PixelRefinerTrainConfig:
    output_dir: Path = field(default_factory=lambda: pixel_refiner_model_dir(MODEL_ID))
    model_id: str = MODEL_ID
    architecture: str = "cnn-v1"
    epochs: int = 2
    steps_per_epoch: int = 800
    batch_size: int = 8
    patch_size: int = 128
    learning_rate: float = 2.0e-4
    features: int = 48
    seed: int = 1337
    device: str = "auto"
    num_workers: int = 0
    limit: int = 0
    val_batches: int = 16
    log_interval: int = 25
    amp: bool = True
    categories: tuple[str, ...] = ()
    input_kinds: tuple[str, ...] = ()
    palette_levels: int = 64
    alpha_threshold: int = 128
    pixel_constraint_weight: float = 0.08
    internal_scale: int = 1
    tile_overlap: int = 0
    block_consistency_weight: float = 0.0
    edge_loss_weight: float = 0.25
    anti_blur_weight: float = 0.0
    software_candidate_weight: float = INPUT_KIND_SAMPLE_WEIGHTS["software_candidate"]
    ai_pseudo_weight: float = INPUT_KIND_SAMPLE_WEIGHTS["ai_pseudo"]
    grad_clip: float = 1.0
    event_log_path: Path | None = None


def train_pixel_refiner(config: PixelRefinerTrainConfig) -> dict[str, Any]:
    torch = _import_torch()
    from torch.utils.data import DataLoader

    _seed_everything(config.seed, torch)
    records = _training_records(config)
    if len(records) < 8:
        raise RuntimeError(f"Not enough training pairs: {len(records)}")

    rng = random.Random(config.seed)
    rng.shuffle(records)
    val_count = max(1, min(len(records) // 20, 128))
    val_records = records[:val_count]
    train_records = records[val_count:] or records

    device = _resolve_device(config.device, torch)
    model = build_training_model(config).to(device)
    optimizer = torch.optim.AdamW(model.parameters(), lr=config.learning_rate, betas=(0.9, 0.99), weight_decay=1.0e-4)
    use_amp = bool(config.amp and device.type == "cuda")
    scaler = torch.cuda.amp.GradScaler(enabled=use_amp)

    config.output_dir.mkdir(parents=True, exist_ok=True)
    checkpoints_dir = config.output_dir / "checkpoints"
    checkpoints_dir.mkdir(parents=True, exist_ok=True)

    training_log: list[dict[str, Any]] = []
    total_steps = 0
    _emit_training_event(
        {
            "event": "train_start",
            "records": len(records),
            "train_records": len(train_records),
            "val_records": len(val_records),
            "device": str(device),
            "model_id": config.model_id,
            "architecture": config.architecture,
            "epochs": config.epochs,
            "steps_per_epoch": config.steps_per_epoch,
            "batch_size": config.batch_size,
            "patch_size": config.patch_size,
            "model_input_size": _model_input_patch_size(config),
            "internal_scale": config.internal_scale,
            "sample_weights": {
                "input_kind": _input_kind_sample_weights(config),
                "category": CATEGORY_SAMPLE_WEIGHTS,
            },
        },
        config,
    )

    for epoch in range(1, config.epochs + 1):
        train_dataset = PairPatchDataset(
            train_records,
            patch_size=config.patch_size,
            internal_scale=config.internal_scale,
            length=max(1, config.steps_per_epoch * config.batch_size),
            seed=config.seed + epoch,
            weighted=True,
            input_kind_weights=_input_kind_sample_weights(config),
            category_weights=CATEGORY_SAMPLE_WEIGHTS,
        )
        train_loader = DataLoader(
            train_dataset,
            batch_size=config.batch_size,
            shuffle=False,
            num_workers=max(0, config.num_workers),
            pin_memory=device.type == "cuda",
            drop_last=True,
        )
        model.train()
        running = 0.0
        for step, batch in enumerate(train_loader, start=1):
            total_steps += 1
            image = batch["image"].to(device, non_blocking=True)
            alpha = batch["alpha"].to(device, non_blocking=True)
            target = batch["target"].to(device, non_blocking=True)
            target_alpha = batch["target_alpha"].to(device, non_blocking=True)
            optimizer.zero_grad(set_to_none=True)
            with torch.cuda.amp.autocast(enabled=use_amp):
                refined = model(image, alpha)
                loss = _refiner_loss(refined, target, target_alpha, torch, config=config)
            if not bool(torch.isfinite(loss).all().detach().cpu()):
                event = {"event": "train_abort", "reason": "non_finite_loss", "epoch": epoch, "step": step}
                _emit_training_event(event, config)
                raise RuntimeError(f"Non-finite training loss at epoch {epoch} step {step}.")
            scaler.scale(loss).backward()
            scaler.unscale_(optimizer)
            if config.grad_clip > 0:
                torch.nn.utils.clip_grad_norm_(model.parameters(), max_norm=float(config.grad_clip))
            scaler.step(optimizer)
            scaler.update()
            running += float(loss.detach().cpu())
            if step == 1 or step % max(1, config.log_interval) == 0:
                avg = running / step
                event = {"event": "train_step", "epoch": epoch, "step": step, "loss": round(avg, 6)}
                _emit_training_event(event, config)
        val_loss = _validate(model, val_records, config, device, torch)
        if not math.isfinite(float(val_loss)):
            event = {"event": "train_abort", "reason": "non_finite_val_loss", "epoch": epoch}
            _emit_training_event(event, config)
            raise RuntimeError(f"Non-finite validation loss at epoch {epoch}.")
        epoch_event = {
            "event": "epoch_end",
            "epoch": epoch,
            "train_loss": round(running / max(1, len(train_loader)), 6),
            "val_loss": round(val_loss, 6),
        }
        training_log.append(epoch_event)
        _emit_training_event(epoch_event, config)
        checkpoint_path = checkpoints_dir / f"{_safe_model_file_stem(config.model_id)}_epoch_{epoch:03d}.pt"
        torch.save(
            {
                "model": model.state_dict(),
                "config": _config_to_json(config),
                "epoch": epoch,
                "training_log": training_log,
            },
            checkpoint_path,
        )

    weights_path = config.output_dir / "weights" / _model_filename(config.model_id)
    export_onnx(model, weights_path, patch_size=_model_input_patch_size(config), torch=torch)
    manifest_path = write_model_manifest(
        config.output_dir,
        weights_path=weights_path,
        config=config,
        records=len(records),
        training_log=training_log,
    )
    training_config_path = config.output_dir / "training_config.json"
    training_config_path.write_text(json.dumps(_config_to_json(config), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return {
        "ok": True,
        "model_dir": str(config.output_dir),
        "weights": str(weights_path),
        "manifest": str(manifest_path),
        "training_config": str(training_config_path),
        "records": len(records),
        "device": str(device),
        "total_steps": total_steps,
        "log": training_log,
    }


class PairPatchDataset:
    def __init__(
        self,
        records: Sequence[PixelRefinerPairRecord],
        *,
        patch_size: int,
        internal_scale: int = 1,
        length: int,
        seed: int,
        weighted: bool,
        input_kind_weights: dict[str, float] | None = None,
        category_weights: dict[str, float] | None = None,
    ) -> None:
        self.records = list(records)
        self.patch_size = int(patch_size)
        self.internal_scale = max(1, int(internal_scale))
        self.length = int(length)
        self.seed = int(seed)
        self.weighted = bool(weighted)
        self.input_kind_weights = dict(input_kind_weights or INPUT_KIND_SAMPLE_WEIGHTS)
        self.category_weights = dict(category_weights or CATEGORY_SAMPLE_WEIGHTS)
        self.weights = [_sample_weight(record, self.input_kind_weights, self.category_weights) for record in self.records]

    def __len__(self) -> int:
        return self.length

    def __getitem__(self, index: int) -> dict[str, Any]:
        rng = random.Random(self.seed + index * 104729)
        if self.weighted:
            record = rng.choices(self.records, weights=self.weights, k=1)[0]
        else:
            record = self.records[rng.randrange(len(self.records))]
        input_image = _load_rgba(record.input_path)
        target_image = _load_rgba(record.target_path)
        if input_image.size != target_image.size:
            target_image = target_image.resize(input_image.size, Image.Resampling.NEAREST)
        input_patch, target_patch = _aligned_patch(input_image, target_image, self.patch_size, rng)
        if rng.random() < 0.5:
            input_patch = ImageOps.mirror(input_patch)
            target_patch = ImageOps.mirror(target_patch)
        if self.internal_scale > 1:
            scaled_size = self.patch_size * self.internal_scale
            input_patch = input_patch.resize((scaled_size, scaled_size), Image.Resampling.NEAREST)
            target_patch = target_patch.resize((scaled_size, scaled_size), Image.Resampling.NEAREST)
        input_array = _rgba_array(input_patch)
        target_array = _rgba_array(target_patch)
        return {
            "image": _chw(input_array[:, :, :3]),
            "alpha": _chw(input_array[:, :, 3:4]),
            "target": _chw(target_array[:, :, :3]),
            "target_alpha": _chw(target_array[:, :, 3:4]),
        }


def export_onnx(model: Any, output_path: Path, *, patch_size: int, torch: Any | None = None) -> None:
    torch = torch or _import_torch()
    output_path.parent.mkdir(parents=True, exist_ok=True)
    was_training = model.training
    model = model.cpu().eval()
    image = torch.rand(1, 3, patch_size, patch_size, dtype=torch.float32)
    alpha = torch.ones(1, 1, patch_size, patch_size, dtype=torch.float32)
    torch.onnx.export(
        model,
        (image, alpha),
        str(output_path),
        input_names=["image", "alpha"],
        output_names=["refined"],
        dynamic_axes={
            "image": {0: "batch", 2: "height", 3: "width"},
            "alpha": {0: "batch", 2: "height", 3: "width"},
            "refined": {0: "batch", 2: "height", 3: "width"},
        },
        opset_version=17,
    )
    if was_training:
        model.train()
    _validate_onnx(output_path, patch_size)


def write_model_manifest(
    model_dir: Path,
    *,
    weights_path: Path,
    config: PixelRefinerTrainConfig,
    records: int,
    training_log: list[dict[str, Any]],
) -> Path:
    manifest_path = model_dir / "model_manifest.json"
    relative_weights = weights_path.relative_to(model_dir)
    payload = {
        "id": config.model_id,
        "version": MODEL_VERSION,
        "runtime": "onnxruntime",
        "weights": str(relative_weights).replace("\\", "/"),
        "target_sizes": [],
        "alpha_modes": ["preserve"],
        "recommended_vram_mb": 8192 if _is_v4(config) else (6144 if _is_v3(config) else (4096 if _is_v2(config) else 1024)),
        "trained_at": datetime.now().strftime("%Y-%m-%dT%H:%M:%S"),
        "dataset_dir": str(dataset_dir()),
        "training_pairs": records,
        "architecture": config.architecture,
        "patch_size": config.patch_size,
        "model_input_size": _model_input_patch_size(config),
        "features": config.features,
        "pixel_art_cleanup": _is_v2(config),
        "hard_pixel_output": _is_v4(config),
        "output_layer_version": "v4-hard-pixel" if _is_v4(config) else ("v2-cleanup" if _is_v2(config) else ""),
        "palette_strategy": "model_quantized" if _is_v4(config) else "source",
        "cluster_cleanup": _is_v4(config),
        "palette_limit": config.palette_levels if _is_v2(config) else 0,
        "alpha_threshold": config.alpha_threshold if _is_v2(config) else 128,
        "pixel_constraint_weight": config.pixel_constraint_weight if _is_v2(config) else 0.0,
        "internal_scale": max(1, int(config.internal_scale)),
        "tiled_inference": _uses_tiled_inference(config),
        "tile_size": config.patch_size if _uses_tiled_inference(config) else 0,
        "tile_overlap": max(0, int(config.tile_overlap)) if _uses_tiled_inference(config) else 0,
        "block_consistency_weight": max(0.0, float(config.block_consistency_weight)),
        "edge_loss_weight": max(0.0, float(config.edge_loss_weight)),
        "anti_blur_weight": max(0.0, float(config.anti_blur_weight)),
        "grad_clip": max(0.0, float(config.grad_clip)),
        "sample_weights": {
            "input_kind": _input_kind_sample_weights(config),
            "category": CATEGORY_SAMPLE_WEIGHTS,
        },
        "last_val_loss": training_log[-1]["val_loss"] if training_log else None,
        "notes": _manifest_notes(config),
    }
    manifest_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return manifest_path


def _training_records(config: PixelRefinerTrainConfig) -> list[PixelRefinerPairRecord]:
    records = [
        record
        for record in load_pair_records()
        if record.input_path.is_file()
        and record.target_path.is_file()
        and (not config.categories or record.category in config.categories)
        and (not config.input_kinds or record.input_kind in config.input_kinds)
    ]
    if config.limit > 0:
        rng = random.Random(config.seed)
        rng.shuffle(records)
        records = records[: config.limit]
    return records


def _input_kind_sample_weights(config: PixelRefinerTrainConfig) -> dict[str, float]:
    weights = dict(INPUT_KIND_SAMPLE_WEIGHTS)
    weights["software_candidate"] = max(0.0, float(config.software_candidate_weight))
    weights["ai_pseudo"] = max(0.0, float(config.ai_pseudo_weight))
    return weights


def _sample_weight(
    record: PixelRefinerPairRecord,
    input_kind_weights: dict[str, float] | None = None,
    category_weights: dict[str, float] | None = None,
) -> float:
    input_weights = input_kind_weights or INPUT_KIND_SAMPLE_WEIGHTS
    category_weight_map = category_weights or CATEGORY_SAMPLE_WEIGHTS
    input_weight = input_weights.get(record.input_kind, 1.0)
    category_weight = category_weight_map.get(record.category, 1.0)
    return float(input_weight * category_weight)


def _validate(model: Any, records: Sequence[PixelRefinerPairRecord], config: PixelRefinerTrainConfig, device: Any, torch: Any) -> float:
    from torch.utils.data import DataLoader

    if not records:
        return math.nan
    dataset = PairPatchDataset(
        records,
        patch_size=config.patch_size,
        internal_scale=config.internal_scale,
        length=max(1, config.val_batches * config.batch_size),
        seed=config.seed + 9001,
        weighted=False,
    )
    loader = DataLoader(dataset, batch_size=config.batch_size, shuffle=False, num_workers=0, pin_memory=device.type == "cuda")
    model.eval()
    losses: list[float] = []
    with torch.no_grad():
        for batch in loader:
            image = batch["image"].to(device, non_blocking=True)
            alpha = batch["alpha"].to(device, non_blocking=True)
            target = batch["target"].to(device, non_blocking=True)
            target_alpha = batch["target_alpha"].to(device, non_blocking=True)
            refined = model(image, alpha)
            losses.append(float(_refiner_loss(refined, target, target_alpha, torch, config=config).detach().cpu()))
    model.train()
    return sum(losses) / max(1, len(losses))


def _refiner_loss(refined: Any, target: Any, target_alpha: Any, torch: Any, *, config: PixelRefinerTrainConfig) -> Any:
    import torch.nn.functional as F

    weight = 0.15 + 0.85 * target_alpha
    pixel = F.smooth_l1_loss(refined * weight, target * weight)
    dx_refined = refined[:, :, :, 1:] - refined[:, :, :, :-1]
    dx_target = target[:, :, :, 1:] - target[:, :, :, :-1]
    dy_refined = refined[:, :, 1:, :] - refined[:, :, :-1, :]
    dy_target = target[:, :, 1:, :] - target[:, :, :-1, :]
    wx = weight[:, :, :, 1:].expand_as(dx_refined)
    wy = weight[:, :, 1:, :].expand_as(dy_refined)
    edge = F.smooth_l1_loss(dx_refined * wx, dx_target * wx) + F.smooth_l1_loss(dy_refined * wy, dy_target * wy)
    loss = pixel + float(config.edge_loss_weight) * edge
    if _is_v2(config):
        quantized = _ste_quantize(refined, max(2, int(config.palette_levels)))
        palette = F.smooth_l1_loss(refined * weight, quantized * weight)
        background = F.smooth_l1_loss(refined * (1.0 - target_alpha), target * (1.0 - target_alpha))
        loss = loss + float(config.pixel_constraint_weight) * palette + 0.1 * background
    if _is_v4(config) and config.anti_blur_weight > 0:
        loss = loss + float(config.anti_blur_weight) * _local_variance_loss(refined, target, target_alpha, torch)
    if max(1, int(config.internal_scale)) > 1:
        scale = max(1, int(config.internal_scale))
        down_refined = F.avg_pool2d(refined, kernel_size=scale, stride=scale)
        down_target = F.avg_pool2d(target, kernel_size=scale, stride=scale)
        down_alpha = F.avg_pool2d(target_alpha, kernel_size=scale, stride=scale)
        down_weight = 0.15 + 0.85 * down_alpha
        downsampled = F.smooth_l1_loss(down_refined * down_weight, down_target * down_weight)
        block = _block_consistency_loss(refined, target_alpha, scale, torch)
        loss = loss + 0.5 * downsampled + float(config.block_consistency_weight) * block
    return loss


def _aligned_patch(input_image: Image.Image, target_image: Image.Image, patch_size: int, rng: random.Random) -> tuple[Image.Image, Image.Image]:
    width, height = input_image.size
    crop_w = min(width, patch_size)
    crop_h = min(height, patch_size)
    left = rng.randint(0, max(0, width - crop_w)) if width > crop_w else 0
    top = rng.randint(0, max(0, height - crop_h)) if height > crop_h else 0
    box = (left, top, left + crop_w, top + crop_h)
    input_crop = input_image.crop(box)
    target_crop = target_image.crop(box)
    if crop_w == patch_size and crop_h == patch_size:
        return input_crop, target_crop
    return _paste_on_canvas(input_crop, patch_size), _paste_on_canvas(target_crop, patch_size)


def _paste_on_canvas(image: Image.Image, size: int) -> Image.Image:
    canvas = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    canvas.alpha_composite(image, ((size - image.width) // 2, (size - image.height) // 2))
    return canvas


def _load_rgba(path: Path) -> Image.Image:
    with Image.open(path) as loaded:
        return ImageOps.exif_transpose(loaded).convert("RGBA")


def _rgba_array(image: Image.Image) -> np.ndarray:
    return np.asarray(image, dtype=np.float32) / 255.0


def _chw(array: np.ndarray) -> Any:
    return np.ascontiguousarray(array.transpose(2, 0, 1), dtype=np.float32)


def _validate_onnx(path: Path, patch_size: int) -> None:
    try:
        import onnx

        model = onnx.load(str(path))
        onnx.checker.check_model(model)
    except ImportError:
        return
    try:
        import onnxruntime as ort

        session = ort.InferenceSession(str(path), providers=["CPUExecutionProvider"])
        image = np.zeros((1, 3, patch_size, patch_size), dtype=np.float32)
        alpha = np.ones((1, 1, patch_size, patch_size), dtype=np.float32)
        outputs = session.run(None, {"image": image, "alpha": alpha})
        if not outputs or outputs[0].shape[:2] != (1, 3):
            raise RuntimeError(f"Unexpected ONNX output shape: {outputs[0].shape if outputs else None}")
    except ImportError:
        return


def _resolve_device(value: str, torch: Any) -> Any:
    text = str(value or "auto").lower()
    if text == "auto":
        text = "cuda" if torch.cuda.is_available() else "cpu"
    device = torch.device(text)
    if device.type == "cuda" and not torch.cuda.is_available():
        raise RuntimeError("CUDA was requested, but torch.cuda.is_available() is false.")
    return device


def _seed_everything(seed: int, torch: Any) -> None:
    random.seed(seed)
    np.random.seed(seed)
    torch.manual_seed(seed)
    if torch.cuda.is_available():
        torch.cuda.manual_seed_all(seed)


def build_training_model(config: PixelRefinerTrainConfig) -> Any:
    if _is_v2(config):
        return PixelRefinerV2Net(features=config.features, palette_levels=config.palette_levels)
    return PixelRefinerNet(features=config.features)


def _is_v2(config: PixelRefinerTrainConfig) -> bool:
    return config.model_id in {V2_MODEL_ID, V3_MODEL_ID, V4_MODEL_ID} or config.model_id.startswith("pixel-refiner-v4") or config.architecture in {
        "unet-naf-v2",
        "v2",
        "pixel-tile-v3",
        "v3",
        "pixel-hard-v4",
        "v4",
    }


def _is_v3(config: PixelRefinerTrainConfig) -> bool:
    return config.model_id == V3_MODEL_ID or config.architecture in {"pixel-tile-v3", "v3"}


def _is_v4(config: PixelRefinerTrainConfig) -> bool:
    return config.model_id.startswith("pixel-refiner-v4") or config.architecture in {"pixel-hard-v4", "v4"}


def _uses_tiled_inference(config: PixelRefinerTrainConfig) -> bool:
    return _is_v3(config) or _is_v4(config)


def _model_filename(model_id: str) -> str:
    if model_id == V4_MODEL_ID:
        return V4_MODEL_FILENAME
    if model_id.startswith("pixel-refiner-v4"):
        return f"{_safe_model_file_stem(model_id)}.onnx"
    if model_id == V3_MODEL_ID:
        return V3_MODEL_FILENAME
    return V2_MODEL_FILENAME if model_id == V2_MODEL_ID else MODEL_FILENAME


def _safe_model_file_stem(model_id: str) -> str:
    return "".join(char if char.isalnum() or char in {"-", "_"} else "_" for char in model_id).strip("_") or "pixel_refiner"


def _manifest_notes(config: PixelRefinerTrainConfig) -> str:
    if config.model_id == V41_MODEL_ID:
        return (
            "Pixel Refiner v4.1 real-failures: v4 hard-pixel tile refiner tuned to over-sample real "
            "GameDesigner software_candidate and ai_pseudo failure pairs before synthetic degradation pairs."
        )
    if _is_v4(config):
        return (
            "Pixel Refiner v4 gold: NAF/U-Net tile refiner trained on high-quality character pixel art, "
            "with 2x internal patch learning, stronger edge/anti-blur losses, tiled inference, and a hard "
            "service-side pixel-art output layer that clamps alpha, quantizes model colors, and removes tiny clusters."
        )
    if _is_v3(config):
        return (
            "Pixel-tile v3 refiner: trains on overlapped original-grid patches, optionally upscales each patch "
            "inside the model, enforces block consistency, and uses tiled service inference for pixel-aligned output."
        )
    if _is_v2(config):
        return (
            "Medium U-Net/NAFNet-style v2 pixel refiner with differentiable palette constraint output "
            "and service-side pixel-art cleanup. Empty target_sizes means dynamic sizes up to the service validation limit."
        )
    return "Fully convolutional v1 pixel refiner. Empty target_sizes means dynamic sizes up to the service validation limit."


def _ste_quantize(tensor: Any, levels: int) -> Any:
    levels = max(2, int(levels))
    quantized = (tensor * float(levels - 1)).round() / float(levels - 1)
    return tensor + (quantized - tensor).detach()


def _config_to_json(config: PixelRefinerTrainConfig) -> dict[str, Any]:
    return {
        "output_dir": str(config.output_dir),
        "model_id": config.model_id,
        "architecture": config.architecture,
        "epochs": config.epochs,
        "steps_per_epoch": config.steps_per_epoch,
        "batch_size": config.batch_size,
        "patch_size": config.patch_size,
        "learning_rate": config.learning_rate,
        "features": config.features,
        "seed": config.seed,
        "device": config.device,
        "num_workers": config.num_workers,
        "limit": config.limit,
        "val_batches": config.val_batches,
        "log_interval": config.log_interval,
        "amp": config.amp,
        "categories": list(config.categories),
        "input_kinds": list(config.input_kinds),
        "palette_levels": config.palette_levels,
        "alpha_threshold": config.alpha_threshold,
        "pixel_constraint_weight": config.pixel_constraint_weight,
        "internal_scale": config.internal_scale,
        "tile_overlap": config.tile_overlap,
        "block_consistency_weight": config.block_consistency_weight,
        "edge_loss_weight": config.edge_loss_weight,
        "anti_blur_weight": config.anti_blur_weight,
        "software_candidate_weight": config.software_candidate_weight,
        "ai_pseudo_weight": config.ai_pseudo_weight,
        "grad_clip": config.grad_clip,
        "event_log_path": str(config.event_log_path) if config.event_log_path else "",
        "sample_weights": {
            "input_kind": _input_kind_sample_weights(config),
            "category": CATEGORY_SAMPLE_WEIGHTS,
        },
    }


def _emit_training_event(event: dict[str, Any], config: PixelRefinerTrainConfig) -> None:
    text = json.dumps(event, ensure_ascii=False)
    print(text, flush=True)
    if config.event_log_path is None:
        return
    config.event_log_path.parent.mkdir(parents=True, exist_ok=True)
    with config.event_log_path.open("a", encoding="utf-8") as file:
        file.write(text + "\n")


def _model_input_patch_size(config: PixelRefinerTrainConfig) -> int:
    return max(16, int(config.patch_size)) * max(1, int(config.internal_scale))


def _block_consistency_loss(tensor: Any, alpha: Any, scale: int, torch: Any) -> Any:
    scale = max(1, int(scale))
    if scale <= 1:
        return tensor.new_tensor(0.0)
    height = (tensor.shape[-2] // scale) * scale
    width = (tensor.shape[-1] // scale) * scale
    if height <= 0 or width <= 0:
        return tensor.new_tensor(0.0)
    cropped = tensor[:, :, :height, :width]
    cropped_alpha = alpha[:, :, :height, :width]
    block = cropped.reshape(cropped.shape[0], cropped.shape[1], height // scale, scale, width // scale, scale)
    block_mean = block.mean(dim=(3, 5), keepdim=True)
    alpha_block = cropped_alpha.reshape(cropped_alpha.shape[0], 1, height // scale, scale, width // scale, scale)
    weight = 0.15 + 0.85 * alpha_block
    return ((block - block_mean) ** 2 * weight).mean()


def _local_variance_loss(refined: Any, target: Any, alpha: Any, torch: Any) -> Any:
    import torch.nn.functional as F

    def variance(tensor: Any) -> Any:
        mean = F.avg_pool2d(tensor, kernel_size=3, stride=1, padding=1)
        mean_sq = F.avg_pool2d(tensor * tensor, kernel_size=3, stride=1, padding=1)
        return torch.clamp(mean_sq - mean * mean, min=0.0)

    weight = 0.15 + 0.85 * alpha
    return F.smooth_l1_loss(variance(refined) * weight, variance(target) * weight)


def _import_torch() -> Any:
    try:
        import torch
    except ImportError as exc:
        raise RuntimeError(
            "Pixel Refiner training requires PyTorch. Install the training environment before running train."
        ) from exc
    return torch


class ResidualBlock(_import_torch().nn.Module):
    def __init__(self, channels: int, *, dilation: int = 1) -> None:
        torch = _import_torch()
        super().__init__()
        self.block = torch.nn.Sequential(
            torch.nn.Conv2d(channels, channels, kernel_size=3, padding=dilation, dilation=dilation),
            torch.nn.SiLU(inplace=True),
            torch.nn.Conv2d(channels, channels, kernel_size=3, padding=1),
        )

    def forward(self, x: Any) -> Any:
        torch = _import_torch()
        return torch.nn.functional.silu(x + self.block(x))


class PixelRefinerNet(_import_torch().nn.Module):
    def __init__(self, features: int = 48) -> None:
        torch = _import_torch()
        super().__init__()
        self.stem = torch.nn.Sequential(
            torch.nn.Conv2d(4, features, kernel_size=3, padding=1),
            torch.nn.SiLU(inplace=True),
        )
        self.body = torch.nn.Sequential(
            ResidualBlock(features, dilation=1),
            ResidualBlock(features, dilation=2),
            ResidualBlock(features, dilation=1),
            ResidualBlock(features, dilation=3),
            ResidualBlock(features, dilation=1),
            ResidualBlock(features, dilation=2),
        )
        self.out = torch.nn.Conv2d(features, 3, kernel_size=3, padding=1)

    def forward(self, image: Any, alpha: Any) -> Any:
        torch = _import_torch()

        x0 = torch.cat([image, alpha], dim=1)
        features = self.body(self.stem(x0))
        delta = torch.tanh(self.out(features)) * 0.5
        return torch.clamp(image + delta, 0.0, 1.0)


class NAFBlock(_import_torch().nn.Module):
    def __init__(self, channels: int, *, expansion: int = 2) -> None:
        torch = _import_torch()
        super().__init__()
        hidden = channels * expansion
        self.conv1 = torch.nn.Conv2d(channels, hidden * 2, kernel_size=1)
        self.dwconv = torch.nn.Conv2d(hidden * 2, hidden * 2, kernel_size=3, padding=1, groups=hidden * 2)
        self.channel_attention = torch.nn.Sequential(
            torch.nn.AdaptiveAvgPool2d(1),
            torch.nn.Conv2d(hidden, hidden, kernel_size=1),
        )
        self.conv2 = torch.nn.Conv2d(hidden, channels, kernel_size=1)
        self.ffn1 = torch.nn.Conv2d(channels, hidden * 2, kernel_size=1)
        self.ffn2 = torch.nn.Conv2d(hidden, channels, kernel_size=1)
        self.beta = torch.nn.Parameter(torch.zeros(1, channels, 1, 1))
        self.gamma = torch.nn.Parameter(torch.zeros(1, channels, 1, 1))

    def forward(self, x: Any) -> Any:
        torch = _import_torch()

        y = self.dwconv(self.conv1(x))
        a, b = torch.chunk(y, 2, dim=1)
        y = a * b
        y = y * self.channel_attention(y)
        x = x + self.beta * self.conv2(y)

        z = self.ffn1(x)
        a, b = torch.chunk(z, 2, dim=1)
        z = self.ffn2(a * b)
        return x + self.gamma * z


class PixelRefinerV2Net(_import_torch().nn.Module):
    def __init__(self, features: int = 64, palette_levels: int = 64) -> None:
        torch = _import_torch()
        super().__init__()
        self.palette_levels = max(2, int(palette_levels))
        self.stem = torch.nn.Sequential(
            torch.nn.Conv2d(4, features, kernel_size=3, padding=1),
            torch.nn.SiLU(inplace=True),
        )
        self.enc1 = torch.nn.Sequential(NAFBlock(features), NAFBlock(features))
        self.down1 = torch.nn.Sequential(
            torch.nn.Conv2d(features, features * 2, kernel_size=4, stride=2, padding=1),
            torch.nn.SiLU(inplace=True),
        )
        self.enc2 = torch.nn.Sequential(NAFBlock(features * 2), NAFBlock(features * 2))
        self.down2 = torch.nn.Sequential(
            torch.nn.Conv2d(features * 2, features * 4, kernel_size=4, stride=2, padding=1),
            torch.nn.SiLU(inplace=True),
        )
        self.mid = torch.nn.Sequential(
            NAFBlock(features * 4),
            NAFBlock(features * 4),
            NAFBlock(features * 4),
            NAFBlock(features * 4),
        )
        self.up2 = torch.nn.Sequential(
            torch.nn.Conv2d(features * 6, features * 2, kernel_size=3, padding=1),
            torch.nn.SiLU(inplace=True),
            NAFBlock(features * 2),
            NAFBlock(features * 2),
        )
        self.up1 = torch.nn.Sequential(
            torch.nn.Conv2d(features * 3, features, kernel_size=3, padding=1),
            torch.nn.SiLU(inplace=True),
            NAFBlock(features),
            NAFBlock(features),
        )
        self.delta_head = torch.nn.Conv2d(features, 3, kernel_size=3, padding=1)
        self.constraint_gate = torch.nn.Conv2d(features, 1, kernel_size=3, padding=1)

    def forward(self, image: Any, alpha: Any) -> Any:
        torch = _import_torch()
        import torch.nn.functional as F

        x = torch.cat([image, alpha], dim=1)
        enc1 = self.enc1(self.stem(x))
        enc2 = self.enc2(self.down1(enc1))
        mid = self.mid(self.down2(enc2))

        up2 = F.interpolate(mid, size=enc2.shape[-2:], mode="nearest")
        up2 = self.up2(torch.cat([up2, enc2], dim=1))
        up1 = F.interpolate(up2, size=enc1.shape[-2:], mode="nearest")
        up1 = self.up1(torch.cat([up1, enc1], dim=1))

        refined = torch.clamp(image + torch.tanh(self.delta_head(up1)) * 0.75, 0.0, 1.0)
        quantized = _ste_quantize(refined, self.palette_levels)
        gate = torch.sigmoid(self.constraint_gate(up1)) * 0.85
        return torch.clamp(refined + gate * (quantized - refined), 0.0, 1.0)
