"""FastAPI 应用入口。

运行（见 scripts/run.ps1）：
    uvicorn app.main:app --host 127.0.0.1 --port 8000

默认 MOCK_LLM=1（零成本）；真实模式设置 MOCK_LLM=0。
"""
from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.agents.generation_agent import GenerationAgent
from app.agents.compose_agent import ComposeAgent
from app.agents.prompt_agent import PromptAgent
from app.agents.review_agent import ReviewAgent
from app.agents.script_agent import ScriptAgent
from app.api.tasks import create_router
from app.config import Settings, get_settings
from app.core.logging_setup import setup_logging
from app.core.task_store import TaskStore
from app.core.workflow import WorkflowEngine
from app.llm.client import DeepSeekClient, LLMError
from app.tools.providers import build_provider

log = logging.getLogger("main")


def build_components(settings: Settings | None = None) -> tuple[Settings, TaskStore, WorkflowEngine]:
    """组件装配：FastAPI 应用与冒烟脚本共用同一条装配路径。

    返回 (可能被降级过的) settings、store、engine。
    """
    settings = settings or get_settings()
    setup_logging(settings.log_dir)

    # 真实模式但缺 Key：降级回 Mock（安全默认），不启动失败
    if not settings.mock_llm and not settings.deepseek_api_key:
        log.warning("MOCK_LLM=0 但缺少 DEEPSEEK_API_KEY，自动降级为 MOCK 模式")
        settings = settings.with_overrides(mock_llm=True)

    store = TaskStore(settings.db_path)
    try:
        provider = build_provider(settings)
    except Exception as e:  # noqa: BLE001 例如 jimeng 占位未实现：回退 mock，不让启动崩溃
        log.warning("Provider %s 不可用，回退 mock：%s", settings.gen_provider, e)
        settings = settings.with_overrides(gen_provider="mock")
        provider = build_provider(settings)

    llm: DeepSeekClient | None = None
    if not settings.mock_llm:
        try:
            llm = DeepSeekClient(
                settings.deepseek_api_key, settings.deepseek_base_url, settings.llm_model
            )
            log.info("LLM: DeepSeek 真实模式 (model=%s)", settings.llm_model)
        except LLMError as e:
            log.warning("真实 LLM 初始化失败，降级 MOCK: %s", e)
            settings = settings.with_overrides(mock_llm=True)
    else:
        log.info("LLM: MOCK 模式（零成本）")

    agents = {
        "script": ScriptAgent(llm),
        "prompt": PromptAgent(llm),
        "generation": GenerationAgent(provider, llm),
        "review": ReviewAgent(),
        "compose": ComposeAgent(),
    }
    engine = WorkflowEngine(store, settings, agents)
    return settings, store, engine


def build_app(settings: Settings | None = None) -> FastAPI:
    """应用工厂：测试可注入自定义 Settings（临时目录、强制 Mock 等）。"""
    settings, store, engine = build_components(settings)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        try:
            engine.start()
            yield
        finally:
            await asyncio.to_thread(engine.stop)
            store.close()

    app = FastAPI(
        title="AIGC Multi-Agent 视频生产 Workflow",
        version="0.1.0",
        description="任务状态机 + 多 Agent 流水线：编剧 -> 提示词 -> 素材生成(Tool Calling) -> 质检",
        lifespan=lifespan,
    )
    app.include_router(create_router(engine, store))

    @app.get("/healthz")
    def healthz() -> dict:
        return {
            "status": "ok",
            "mock_llm": settings.mock_llm,
            "gen_provider": settings.gen_provider,
            "llm_model": settings.llm_model,
        }

    return app


app = build_app()
