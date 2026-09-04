"""API 请求/响应模型。"""
from __future__ import annotations

from pydantic import BaseModel, Field


class TaskCreateRequest(BaseModel):
    topic: str = Field(..., min_length=2, max_length=200,
                       description="一句话主题", examples=["给这双鞋子做个广告"])
    product_info: str | None = Field(None, max_length=2000, description="商品信息（可选）")
    scene_count: int = Field(3, ge=1, le=8, description="镜头数（1~8）")


class TaskCreateResponse(BaseModel):
    task_id: str
    status: str
    created_at: str


class TaskSummary(BaseModel):
    task_id: str
    topic: str
    status: str
    current_step: str | None = None
    created_at: str
    updated_at: str


class StepView(BaseModel):
    name: str
    status: str
    attempt: int = 1
    error: str | None = None
    started_at: str | None = None
    finished_at: str | None = None


class EventView(BaseModel):
    ts: str
    step: str | None = None
    message: str


class TaskView(BaseModel):
    task_id: str
    topic: str
    product_info: str = ""
    status: str
    current_step: str | None = None
    error: str | None = None
    warnings: list[str] = Field(default_factory=list)
    artifacts: dict = Field(default_factory=dict)
    scene_states: dict = Field(default_factory=dict)
    steps: list[StepView] = Field(default_factory=list)
    events: list[EventView] = Field(default_factory=list)
    created_at: str
    updated_at: str
