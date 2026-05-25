from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class AiConnectionPreset:
    key: str
    label: str
    provider: str
    model: str
    auth_mode: str
    api_key: str = ""
    base_url: str = ""
    needs_api_key: bool = False
    description: str = ""

    def to_snapshot(self, stored: dict[str, str] | None = None) -> dict[str, str]:
        stored = stored or {}
        api_key = stored.get("ai_api_key") or self.api_key
        return {
            "ai_provider": self.provider,
            "ai_model": self.model,
            "ai_auth_mode": self.auth_mode,
            "ai_api_key": api_key,
            "ai_base_url": self.base_url,
        }


AI_OFFICIAL_PROFILE_KEY = "official"
AI_CUSTOM_API_PROFILE_KEY = "api_key"

AI_FREE_MODEL_PRESETS: tuple[AiConnectionPreset, ...] = (
    AiConnectionPreset(
        key="free_ollama_gpt_oss_20b",
        label="Ollama 本地 · gpt-oss:20b",
        provider="codex",
        model="gpt-oss:20b",
        auth_mode="api_key",
        api_key="ollama",
        base_url="http://localhost:11434/v1",
        description="本机免费运行；需要先安装 Ollama 并拉取 gpt-oss:20b。",
    ),
    AiConnectionPreset(
        key="free_ollama_qwen3_8b",
        label="Ollama 本地 · qwen3:8b",
        provider="codex",
        model="qwen3:8b",
        auth_mode="api_key",
        api_key="ollama",
        base_url="http://localhost:11434/v1",
        description="本机免费运行；比 20B 更轻，需要先安装 Ollama 并拉取 qwen3:8b。",
    ),
    AiConnectionPreset(
        key="free_openrouter_router",
        label="OpenRouter Free · 自动路由",
        provider="codex",
        model="openrouter/free",
        auth_mode="api_key",
        base_url="https://openrouter.ai/api/v1",
        needs_api_key=True,
        description="使用 OpenRouter 免费模型路由；需要 OpenRouter API Key。",
    ),
    AiConnectionPreset(
        key="free_gemini_flash",
        label="Gemini 免费额度 · 2.5 Flash",
        provider="codex",
        model="gemini-2.5-flash",
        auth_mode="api_key",
        base_url="https://generativelanguage.googleapis.com/v1beta/openai/",
        needs_api_key=True,
        description="使用 Gemini API 免费额度；需要 Gemini API Key。",
    ),
)


def free_model_preset_by_key(key: str) -> AiConnectionPreset | None:
    for preset in AI_FREE_MODEL_PRESETS:
        if preset.key == key:
            return preset
    return None


def ai_connection_snapshot(
    *,
    provider: str,
    model: str,
    auth_mode: str,
    api_key: str,
    base_url: str,
) -> dict[str, str]:
    return {
        "ai_provider": provider.strip() or "codex",
        "ai_model": model.strip(),
        "ai_auth_mode": auth_mode.strip() or "official",
        "ai_api_key": api_key.strip(),
        "ai_base_url": base_url.strip(),
    }


def ai_profile_key_for_snapshot(snapshot: dict[str, str]) -> str:
    auth_mode = snapshot.get("ai_auth_mode", "official")
    if auth_mode != "api_key":
        return AI_OFFICIAL_PROFILE_KEY
    model = snapshot.get("ai_model", "")
    base_url = _normalize_base_url(snapshot.get("ai_base_url", ""))
    for preset in AI_FREE_MODEL_PRESETS:
        if model == preset.model and base_url == _normalize_base_url(preset.base_url):
            return preset.key
    return AI_CUSTOM_API_PROFILE_KEY


def clean_ai_connection_snapshot(raw: Any) -> dict[str, str]:
    if not isinstance(raw, dict):
        return {}
    provider = str(raw.get("ai_provider", "codex") or "codex")
    if provider not in {"codex", "claude"}:
        provider = "codex"
    auth_mode = str(raw.get("ai_auth_mode", "official") or "official")
    if auth_mode not in {"official", "api_key"}:
        auth_mode = "official"
    return ai_connection_snapshot(
        provider=provider,
        model=str(raw.get("ai_model", "") or ""),
        auth_mode=auth_mode,
        api_key=str(raw.get("ai_api_key", "") or ""),
        base_url=str(raw.get("ai_base_url", "") or ""),
    )


def clean_ai_saved_connections(raw: Any) -> dict[str, dict[str, str]]:
    if not isinstance(raw, dict):
        return {}
    result: dict[str, dict[str, str]] = {}
    for key, value in raw.items():
        if not isinstance(key, str) or not key or len(key) > 96:
            continue
        snapshot = clean_ai_connection_snapshot(value)
        if snapshot:
            result[key] = snapshot
    return result


def _normalize_base_url(value: str) -> str:
    return value.strip().rstrip("/")
