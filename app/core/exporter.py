"""任务产物导出器：把 artifacts（DB 里的 JSON）落盘成规范目录里的人可读文件。

03/04/06 目录由 Provider 与 ComposeAgent 在生成时就写入；
本导出器补齐 00 元信息、01 脚本、02 提示词、05 质检报告（各含 json + md 人读版）。
"""
from __future__ import annotations

import json
from pathlib import Path

from app.core.models import TaskRecord
from app.core.output_layout import task_dirs


def export_task_bundle(task: TaskRecord, task_dir: Path) -> Path:
    dirs = task_dirs(task_dir)
    art = task.artifacts

    # 00 任务元信息
    meta = {
        "task_id": task.id,
        "topic": task.topic,
        "product_info": task.product_info,
        "status": task.status,
        "error": task.error,
        "current_step": task.current_step,
        "warnings": task.warnings,
        "created_at": task.created_at,
        "updated_at": task.updated_at,
        "artifacts_index": sorted(art.keys()),
        # 双层状态机的镜头级那一层必须在交付物里可见（治理的前提是可审计）
        "scene_states": art.get("scene_states", {}),
    }

    # 01 分镜脚本
    storyboard = art.get("storyboard")
    if storyboard:
        _write_json(dirs["script"] / "storyboard.json", storyboard)
        (dirs["script"] / "分镜脚本.md").write_text(
            _storyboard_md(task.topic, storyboard), encoding="utf-8"
        )

    # 02 逐镜提示词
    prompts = art.get("prompts")
    if prompts:
        _write_json(dirs["prompts"] / "prompts.json", prompts)
        (dirs["prompts"] / "提示词.md").write_text(
            _prompts_md(task.topic, prompts), encoding="utf-8"
        )

    # 05 质检报告
    review = art.get("review_report")
    if review:
        _write_json(dirs["review"] / "review_report.json", review)

    # 元信息最后写，避免正文导出失败时留下看似完整的成功快照。
    _write_json(dirs["root"] / "00_task.json", meta)
    return dirs["root"]


def _write_json(path: Path, obj) -> None:
    path.write_text(json.dumps(obj, ensure_ascii=False, indent=2), encoding="utf-8")


def _storyboard_md(topic: str, storyboard: dict) -> str:
    lines = [f"# 分镜脚本：{topic}", "",
             "| 镜头 | 小标题 | 旁白 | 画面 | 时长(秒) |",
             "|---|---|---|---|---|"]
    for s in storyboard.get("scenes", []):
        lines.append(
            f"| {s.get('scene_id')} | {s.get('title','')} | {s.get('narration','')} "
            f"| {s.get('visual','')} | {s.get('duration_s','')} |"
        )
    return "\n".join(lines) + "\n"


def _prompts_md(topic: str, prompts: dict) -> str:
    lines = [f"# 生成提示词：{topic}", ""]
    for p in prompts.get("prompts", []):
        lines += [
            f"## 镜头 {p.get('scene_id')}",
            "",
            f"- 关键帧图提示词：{p.get('image_prompt','')}",
            f"- 图生视频提示词：{p.get('video_prompt','')}",
            f"- 首帧衔接说明：{p.get('first_frame_desc','')}",
            "",
        ]
    return "\n".join(lines)
