"""真实 LLM 冒烟：MOCK_LLM=0 全流程真实调用 DeepSeek。

运行：.venv/Scripts/python.exe scripts/smoke_real_llm.py
成本：一个任务约 4~6 次 LLM 调用（几分钱量级）。
用途：验证真实链路（脚本/提示词生成 + Tool Calling 决策 + 质检 + 产物落库）。
"""
from __future__ import annotations

import json
import sys
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.config import get_settings  # noqa: E402
from app.core.models import TaskRecord  # noqa: E402
from app.core.task_store import utc_now  # noqa: E402
from app.main import build_components  # noqa: E402


def main() -> int:
    settings, store, engine = build_components(get_settings().with_overrides(mock_llm=False))
    if settings.mock_llm:
        print("!! 未能进入真实模式（缺 Key 或初始化失败），已降级 MOCK。请检查 .env")
        return 1

    task_id = uuid.uuid4().hex[:12]
    now = utc_now()
    store.create_task(TaskRecord(
        id=task_id,
        topic="给这个保温杯做个广告",
        product_info="316 不锈钢保温杯，12 小时保温，一键开盖",
        scene_count=3,
        status="PENDING",
        created_at=now,
        updated_at=now,
    ))
    print(f"== 真实模式冒烟开始 task_id={task_id} ==")
    engine.run_task(task_id)

    rec = store.get_task(task_id)
    print(f"== 结果: {rec.status} ==")
    if rec.error:
        print(f"错误: {rec.error}")
    for step in store.list_steps(task_id):
        print(f"  step={step['name']:<10} status={step['status']:<10} attempt={step['attempt']}")
    print("---- 分镜脚本（真实 LLM 产出） ----")
    print(json.dumps(rec.artifacts.get("storyboard", {}), ensure_ascii=False, indent=2)[:1500])
    print("---- 质检报告 ----")
    print(json.dumps(rec.artifacts.get("review_report", {}), ensure_ascii=False, indent=2))
    return 0 if rec.status == "SUCCEEDED" else 2


if __name__ == "__main__":
    raise SystemExit(main())
