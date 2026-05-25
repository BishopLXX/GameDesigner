from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from PySide6.QtGui import QImage

from .ai_tools import AI_CHAT_DIR
from .storage import project_bundle_dir


AI_CHAT_ATTACHMENTS_DIR = "attachments"


@dataclass(frozen=True)
class AiImageAttachment:
    path: Path
    width: int
    height: int


def save_ai_chat_image_attachment(project_path: str | Path, image: QImage) -> AiImageAttachment:
    if image.isNull():
        raise ValueError("剪贴板图片为空。")
    folder = project_bundle_dir(project_path) / AI_CHAT_DIR / AI_CHAT_ATTACHMENTS_DIR
    folder.mkdir(parents=True, exist_ok=True)
    filename = f"clipboard_{datetime.now().strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:8]}.png"
    path = folder / filename
    if not image.save(str(path), "PNG"):
        raise OSError(f"无法保存剪贴板图片：{path}")
    return AiImageAttachment(path=path, width=image.width(), height=image.height())
