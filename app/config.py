"""全局配置：只从环境变量 / .env 读取，绝不把密钥写死在代码里。

安全默认值：MOCK_LLM 默认开启（零成本、可离线演示）；
要走真实 DeepSeek 必须显式设置 MOCK_LLM=0。
"""
from __future__ import annotations

import os
from dataclasses import dataclass, replace
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

PROJECT_ROOT = Path(__file__).resolve().parent.parent


def _env_bool(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _env_int(name: str, default: int) -> int:
    raw = os.getenv(name)
    if raw is None:
        return default
    try:
        return int(raw)
    except ValueError:
        return default


@dataclass(frozen=True)
class Settings:
    # ---- LLM ----
    deepseek_api_key: str | None = None
    deepseek_base_url: str = "https://api.deepseek.com"
    llm_model: str = "deepseek-chat"
    # MOCK_LLM=1：所有 LLM 调用走确定性本地响应（零成本、可测试、可离线）
    mock_llm: bool = True

    # ---- 素材生成 ----
    gen_provider: str = "mock"  # mock | jimeng（v2 实现）

    # ---- 流水线参数 ----
    max_scene_retries: int = 2
    default_scene_count: int = 3

    # ---- 路径 ----
    data_dir: Path = PROJECT_ROOT / "data"
    output_dir: Path = PROJECT_ROOT / "outputs"
    log_dir: Path = PROJECT_ROOT / "logs"

    @property
    def db_path(self) -> Path:
        return self.data_dir / "tasks.db"

    def with_overrides(self, **kwargs) -> "Settings":
        """返回覆盖部分字段的新 Settings（frozen dataclass 的 evolve 写法）。"""
        return replace(self, **kwargs)


def get_settings() -> Settings:
    """从环境变量构造 Settings。测试里可直接实例化 Settings 覆盖字段。"""
    return Settings(
        deepseek_api_key=os.getenv("DEEPSEEK_API_KEY"),
        deepseek_base_url=os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com"),
        llm_model=os.getenv("LLM_MODEL", "deepseek-chat"),
        mock_llm=_env_bool("MOCK_LLM", default=True),
        gen_provider=os.getenv("GEN_PROVIDER", "mock"),
        max_scene_retries=_env_int("MAX_SCENE_RETRIES", 2),
        default_scene_count=_env_int("DEFAULT_SCENE_COUNT", 3),
    )
