"""素材生成 Provider 工厂。"""
from __future__ import annotations

from app.config import Settings
from app.tools.providers.base import GenerationProvider
from app.tools.providers.jimeng_provider import JimengProvider
from app.tools.providers.mock_provider import MockProvider
from app.tools.providers.pollinations_provider import PollinationsProvider


def build_provider(settings: Settings) -> GenerationProvider:
    if settings.gen_provider == "mock":
        return MockProvider(settings.output_dir)
    if settings.gen_provider == "jimeng":
        return JimengProvider()
    if settings.gen_provider == "pollinations":
        return PollinationsProvider(settings.output_dir)
    raise ValueError(f"未知 GEN_PROVIDER: {settings.gen_provider}")
