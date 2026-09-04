"""任务 API 路由。

对外形态对齐"任务调度系统"的接口惯例：
- POST 提交：立即返回 202 + task_id（不阻塞等待生成）
- GET 查询：状态 + 当前步骤 + 产物 + 步骤/事件留痕
"""
from __future__ import annotations

import uuid

from fastapi import APIRouter, HTTPException

from app.api.schemas import (
    EventView,
    StepView,
    TaskCreateRequest,
    TaskCreateResponse,
    TaskSummary,
    TaskView,
)
from app.core.models import TaskRecord as TaskRecordModel
from app.core.task_store import TaskStore, utc_now
from app.core.workflow import WorkflowEngine


def create_router(engine: WorkflowEngine, store: TaskStore) -> APIRouter:
    router = APIRouter(prefix="/api/v1", tags=["tasks"])

    @router.post("/tasks", status_code=202, response_model=TaskCreateResponse)
    def create_task(req: TaskCreateRequest) -> TaskCreateResponse:
        task_id = uuid.uuid4().hex[:12]
        now = utc_now()
        rec = TaskRecordModel(
            id=task_id,
            topic=req.topic,
            product_info=req.product_info or "",
            scene_count=req.scene_count,
            status="PENDING",
            created_at=now,
            updated_at=now,
        )
        store.create_task(rec)
        store.append_event(task_id, f"任务创建: topic={req.topic!r}")
        engine.submit(task_id)  # 提交即返回，后台 Worker 执行
        return TaskCreateResponse(task_id=task_id, status="PENDING", created_at=now)

    @router.get("/tasks", response_model=list[TaskSummary])
    def list_tasks(limit: int = 50) -> list[TaskSummary]:
        return [
            TaskSummary(
                task_id=r.id, topic=r.topic, status=r.status,
                current_step=r.current_step,
                created_at=r.created_at, updated_at=r.updated_at,
            )
            for r in store.list_tasks(limit=limit)
        ]

    @router.get("/tasks/{task_id}", response_model=TaskView)
    def get_task(task_id: str) -> TaskView:
        rec = store.get_task(task_id)
        if rec is None:
            raise HTTPException(status_code=404, detail="任务不存在")
        return _to_view(rec, store)

    return router


def _to_view(rec: TaskRecordModel, store: TaskStore) -> TaskView:
    return TaskView(
        task_id=rec.id,
        topic=rec.topic,
        product_info=rec.product_info,
        status=rec.status,
        current_step=rec.current_step,
        error=rec.error,
        warnings=rec.warnings,
        artifacts=rec.artifacts,
        scene_states=rec.artifacts.get("scene_states", {}),
        steps=[StepView(**s) for s in store.list_steps(rec.id)],
        events=[EventView(**e) for e in store.list_events(rec.id)],
        created_at=rec.created_at,
        updated_at=rec.updated_at,
    )
