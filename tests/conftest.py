import pytest
from fastapi.testclient import TestClient

from app.agents.compose_agent import ComposeAgent
from app.agents.generation_agent import GenerationAgent
from app.agents.prompt_agent import PromptAgent
from app.agents.review_agent import ReviewAgent
from app.agents.script_agent import ScriptAgent
from app.config import Settings
from app.core.models import TaskRecord
from app.core.task_store import TaskStore, utc_now
from app.core.workflow import WorkflowEngine
from app.main import build_app
from app.tools.providers.mock_provider import MockProvider


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


# ---------------- 共享装配 helper（非 fixture，测试文件直接 import） ----------------

def build_engine(settings: Settings):
    """装配一条全 Mock 的流水线，返回 (store, engine)。测试共用，避免各文件重复装配。"""
    store = TaskStore(settings.db_path)
    agents = {
        "script": ScriptAgent(llm=None),
        "prompt": PromptAgent(llm=None),
        "generation": GenerationAgent(MockProvider(settings.output_dir), llm=None),
        "review": ReviewAgent(),
        "compose": ComposeAgent(),
    }
    return store, WorkflowEngine(store, settings, agents)


def create_task(store: TaskStore, task_id: str = "testtask0001",
                topic: str = "给这双鞋子做个广告",
                product_info: str = "轻便跑鞋，透气网面", scene_count: int = 3) -> str:
    """落一条 PENDING 任务，返回 task_id。"""
    now = utc_now()
    store.create_task(TaskRecord(
        id=task_id, topic=topic, product_info=product_info,
        scene_count=scene_count, status="PENDING", created_at=now, updated_at=now,
    ))
    return task_id
