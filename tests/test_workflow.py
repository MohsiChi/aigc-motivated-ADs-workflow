"""流水线集成测试（全 Mock）：正常路径 + 质检返工路径 + 致命失败路径。"""
import json
import pathlib

import pytest

from app.agents.base import AgentContext, AgentError
from app.agents.prompt_agent import PromptAgent
from app.agents.script_agent import ScriptAgent
from tests.conftest import build_engine, create_task


def _build(tmp_settings):
    return build_engine(tmp_settings)


def _create_task(store, topic="给这双鞋子做个广告", scene_count=3) -> str:
    return create_task(store, topic=topic, scene_count=scene_count)


def test_full_pipeline_happy_path(tmp_settings):
    store, engine = _build(tmp_settings)
    task_id = _create_task(store)

    engine.run_task(task_id)

    rec = store.get_task(task_id)
    assert rec.status == "SUCCEEDED"
    assert rec.error in (None, "")
    # 五件套产物齐全
    for key in ("storyboard", "prompts", "generation", "review_report", "scene_states"):
        assert key in rec.artifacts, f"缺少产物 {key}"
    # 三个镜头全部质检通过
    assert rec.artifacts["scene_states"] == {"1": "ACCEPTED", "2": "ACCEPTED", "3": "ACCEPTED"}
    assert rec.artifacts["review_report"]["passed_all"] is True
    # 素材文件真实落盘
    clips = rec.artifacts["generation"]["clips"]
    assert len(clips) == 3
    for clip in clips:
        assert (tmp_settings.output_dir / task_id).exists()
        assert pathlib.Path(clip["clip_path"]).exists()
        assert pathlib.Path(clip["keyframe_path"]).exists()
    # 步骤留痕：5 步各一次，无重试
    steps = store.list_steps(task_id)
    assert [s["name"] for s in steps] == ["script", "prompt", "generation", "review", "compose"]
    # 成片真实存在（零成本合成路线）
    final_video = rec.artifacts["compose"]["final_video_path"]
    assert pathlib.Path(final_video).exists()
    assert pathlib.Path(final_video).stat().st_size > 10_000
    # 规范化产物目录（00 元信息 / 01 脚本 / 02 提示词 / 05 质检 / 06 成片）
    bundle = pathlib.Path(rec.artifacts["export_dir"])
    for rel in (
        "00_task.json",
        "01_script/storyboard.json",
        "01_script/分镜脚本.md",
        "02_prompts/prompts.json",
        "02_prompts/提示词.md",
        "05_review/review_report.json",
        "06_final/final.mp4",
    ):
        assert (bundle / rel).exists(), f"missing bundle file: {rel}"
    assert str(bundle / "06_final" / "final.mp4") == final_video
    # 00_task.json 的 status 必须是终态（审查发现：曾在导出时仍是 RUNNING）
    meta = json.loads((bundle / "00_task.json").read_text(encoding="utf-8"))
    assert meta["status"] == "SUCCEEDED"


def test_qc_failure_triggers_retry_then_fallback(tmp_settings, monkeypatch):
    """构造"提示词永远缺关键词"：质检必失败 -> 触发重试 -> 耗尽后降级采用。"""
    store, engine = _build(tmp_settings)
    task_id = _create_task(store)

    def broken_prompts(self, ctx: AgentContext) -> dict:
        # 直接给一份不含任何商品关键词的提示词，R2 必挂
        return {
            "topic": ctx.topic,
            "prompts": [
                {"scene_id": i, "image_prompt": "一段与商品无关的风景描述画面",
                 "video_prompt": "镜头缓慢移动，画面稳定", "first_frame_desc": ""}
                for i in range(1, ctx.scene_count + 1)
            ],
        }

    monkeypatch.setattr(PromptAgent, "_execute", broken_prompts)

    engine.run_task(task_id)

    rec = store.get_task(task_id)
    # 重试耗尽不阻塞整任务：SUCCEEDED + 降级 + 告警
    assert rec.status == "SUCCEEDED"
    assert all(v == "ACCEPTED_FALLBACK" for v in rec.artifacts["scene_states"].values())
    assert len(rec.warnings) == 3
    # 步骤历史里能看到 attempt=2、3 的返工记录
    steps = store.list_steps(task_id)
    gen_attempts = [s["attempt"] for s in steps if s["name"] == "generation"]
    assert gen_attempts == [1, 2, 3]  # 首次 + 2 次返工
    # 事件表留痕
    events = " | ".join(e["message"] for e in store.list_events(task_id))
    assert "返工" in events


def test_fatal_step_failure_marks_task_failed(tmp_settings, monkeypatch):
    store, engine = _build(tmp_settings)
    task_id = _create_task(store)

    def boom(self, ctx):
        raise AgentError("模拟编剧爆炸")

    monkeypatch.setattr(ScriptAgent, "_execute", boom)

    engine.run_task(task_id)

    rec = store.get_task(task_id)
    assert rec.status == "FAILED"
    assert "模拟编剧爆炸" in (rec.error or "")


def test_idempotent_double_run(tmp_settings):
    """同一任务重复调度：第二次直接跳过（幂等保护）。"""
    store, engine = _build(tmp_settings)
    task_id = _create_task(store)
    engine.run_task(task_id)
    steps_after_first = len(store.list_steps(task_id))
    engine.run_task(task_id)  # 重复调度
    assert len(store.list_steps(task_id)) == steps_after_first
    assert store.get_task(task_id).status == "SUCCEEDED"


def test_scene_state_not_leaked_across_tasks(tmp_settings, monkeypatch):
    """任务在 script 步骤失败时，不得把上一个任务的 scene_states 写进来（审查发现）。"""
    store, engine = _build(tmp_settings)
    first = _create_task(store)
    engine.run_task(first)
    assert store.get_task(first).status == "SUCCEEDED"

    def boom(self, ctx):
        raise AgentError("模拟编剧失败")

    monkeypatch.setattr(ScriptAgent, "_execute", boom)
    second = create_task(store, task_id="testtask0002", topic="另一个主题测试")
    engine.run_task(second)
    rec = store.get_task(second)
    assert rec.status == "FAILED"
    assert rec.artifacts.get("scene_states", {}) == {}, "上一个任务的镜头状态串进了新任务"


def test_fail_task_safe_closes_running_steps(tmp_settings):
    """Worker 兜底失败：走状态机守卫，且停留在 RUNNING 的步骤要被关闭（审查发现）。"""
    store, engine = _build(tmp_settings)
    task_id = _create_task(store)
    store.update_task(task_id, status="RUNNING")
    store.start_step(task_id, "script")

    engine._fail_task_safe(task_id, "模拟 worker 异常")

    rec = store.get_task(task_id)
    assert rec.status == "FAILED"
    assert "模拟 worker 异常" in (rec.error or "")
    steps = store.list_steps(task_id)
    assert steps[0]["status"] == "FAILED"


def test_failed_task_exports_audit_bundle(tmp_settings, monkeypatch):
    """P0-A1：失败任务也必须落盘审计包——合规/致命失败最需要留痕。"""
    store, engine = _build(tmp_settings)
    task_id = _create_task(store)

    def boom(self, ctx):
        raise AgentError("模拟编剧爆炸")

    monkeypatch.setattr(ScriptAgent, "_execute", boom)
    engine.run_task(task_id)

    rec = store.get_task(task_id)
    assert rec.status == "FAILED"
    assert "export_dir" in rec.artifacts, "失败路径没有导出产物目录"
    meta_path = pathlib.Path(rec.artifacts["export_dir"]) / "00_task.json"
    assert meta_path.exists()
    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    assert meta["status"] == "FAILED", "00_task.json 必须是终态快照"


def test_bundle_meta_contains_scene_states(tmp_settings):
    """P0-A1：00_task.json 必须带镜头级状态——双层状态机要在交付物里可见。"""
    store, engine = _build(tmp_settings)
    task_id = _create_task(store)
    engine.run_task(task_id)

    rec = store.get_task(task_id)
    meta_path = pathlib.Path(rec.artifacts["export_dir"]) / "00_task.json"
    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    assert meta["scene_states"] == {"1": "ACCEPTED", "2": "ACCEPTED", "3": "ACCEPTED"}
