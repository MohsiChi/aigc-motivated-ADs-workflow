import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.main import build_app


@pytest.fixture()
def tmp_settings(tmp_path) -> Settings:
    """全 Mock + 临时目录：测试零成本、零外部依赖。"""
    return Settings(
        deepseek_api_key=None,
        mock_llm=True,
        gen_provider="mock",
        max_scene_retries=2,
        default_scene_count=3,
        data_dir=tmp_path / "data",
        output_dir=tmp_path / "outputs",
        log_dir=tmp_path / "logs",
    )


@pytest.fixture()
def client(tmp_settings):
    app = build_app(tmp_settings)
    with TestClient(app) as c:  # with 块内会触发 lifespan（启动/停止 Worker）
        yield c
