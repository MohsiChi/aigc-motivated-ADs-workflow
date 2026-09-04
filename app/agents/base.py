"""Agent 基类与执行上下文（Sub-Agent 组件化的地基）。

设计要点（对应 JD"Sub-Agent 积木化、组件化"）：
- 所有 Agent 统一接口 run(ctx) -> AgentResult，像积木一样插进流水线；
- 基类统一处理日志、计时、异常捕获，子类只写业务逻辑 _execute；
- Agent 之间不互相通信：数据通过 ctx.artifacts 的结构化产物传递。
"""
from __future__ import annotations

import logging
import time
from abc import ABC, abstractmethod

from pydantic import BaseModel, ConfigDict, Field

from app.config import Settings


class AgentContext(BaseModel):
    """一次任务执行中，传给每个 Agent 的上下文。"""

    model_config = ConfigDict(arbitrary_types_allowed=True)

    task_id: str
    topic: str
    product_info: str = ""
    scene_count: int = 3
    settings: Settings
    artifacts: dict = Field(default_factory=dict)  # 前序步骤产物容器（Artifact 驱动）
    extra: dict = Field(default_factory=dict)      # 编排层临时指令（重试清单/质检反馈等）


class AgentResult(BaseModel):
    """Agent 的统一执行结果。"""

    agent: str
    ok: bool = True
    output: dict = Field(default_factory=dict)
    error: str | None = None
    meta: dict = Field(default_factory=dict)


class AgentError(RuntimeError):
    """业务性失败（会被基类捕获并转成 ok=False 的 AgentResult）。"""


class BaseAgent(ABC):
    name: str = "base"

    def __init__(self):
        self.log = logging.getLogger(f"agent.{self.name}")

    def run(self, ctx: AgentContext) -> AgentResult:
        """统一执行外壳：日志、计时、异常捕获。子类只实现 _execute。"""
        started = time.perf_counter()
        self.log.info("[%s] 开始 topic=%r", ctx.task_id, ctx.topic)
        try:
            output = self._execute(ctx)
            elapsed = round(time.perf_counter() - started, 3)
            self.log.info("[%s] 完成 (%.3fs)", ctx.task_id, elapsed)
            return AgentResult(
                agent=self.name, ok=True, output=output,
                meta={"duration_s": elapsed, "mock": bool(ctx.settings.mock_llm)},
            )
        except AgentError as e:
            elapsed = round(time.perf_counter() - started, 3)
            self.log.warning("[%s] 业务失败: %s", ctx.task_id, e)
            return AgentResult(agent=self.name, ok=False, error=str(e),
                               meta={"duration_s": elapsed})
        except Exception as e:  # noqa: BLE001 兜底：任何意外都不允许炸穿流水线
            elapsed = round(time.perf_counter() - started, 3)
            self.log.exception("[%s] 未预期异常", ctx.task_id)
            return AgentResult(agent=self.name, ok=False,
                               error=f"{type(e).__name__}: {e}",
                               meta={"duration_s": elapsed})

    @abstractmethod
    def _execute(self, ctx: AgentContext) -> dict:
        """子类实现：返回要写进 artifacts 的结构化产物。"""
