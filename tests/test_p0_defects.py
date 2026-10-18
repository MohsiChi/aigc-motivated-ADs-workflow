"""P0 缺陷的回归测试。

来源：2026-09-05 三个只读诊断脚本的实测结论。
两个已修复缺陷均作为常规回归测试运行；提示词路由原有的 strict xfail 已移除。

两个缺陷的性质区别（很重要，别混）：
- BLOCKER 级合规失败进入交付物：已由逐字节帧比对实证（违规旁白确实烧进了成片帧）。
- 提示词级失败返工空转：已由 steps 表实证（prompt 步骤只执行 1 次而 review 执行 3 次，
  三轮裁决逐字相同）；仓库历史数据中同一模式已真实发生两次。
"""
import pathlib

from app.agents.compose_agent import ComposeAgent
from app.agents.prompt_agent import PromptAgent
from app.agents.script_agent import ScriptAgent
from app.core.models import Scene, Storyboard
from tests.conftest import build_engine, create_task

BANNED_NARRATION = "全网首发的透气科技，这双鞋第一次上脚就懂。"


def _poisoned_storyboard(ctx):
    """镜头 2 的旁白含广告法极限词（R6 -> BLOCKER），其余镜头干净。"""
    return Storyboard(topic=ctx.topic, scenes=[
        Scene(scene_id=1, title="钩子开场", narration="别划走，3 秒就让你心动。",
              visual="跑鞋特写，光影对比鲜明", duration_s=3),
        Scene(scene_id=2, title="卖点演示", narration=BANNED_NARRATION,
              visual="跑鞋真实使用场景，细节特写", duration_s=8),
        Scene(scene_id=3, title="收尾转化", narration="点击下方小黄车，限时优惠。",
              visual="产品全景 + 购买引导按钮", duration_s=4),
    ])


def test_blocker_compliance_failure_blocks_delivery(tmp_settings, monkeypatch):
    """R6 命中旁白（BLOCKER，当前无修复通道）时的正确行为：
    不浪费返工额度、不降级采用、不出成片、任务不报成功、但必须留下审计包。

    曾是缺陷（本轮已修复）：违规旁白会被烧成字幕进入 final.mp4，而任务对外报 SUCCEEDED。
    """
    store, engine = build_engine(tmp_settings)
    task_id = create_task(store, task_id="p0blocker0001")

    monkeypatch.setattr(ScriptAgent, "_mock_storyboard",
                        staticmethod(_poisoned_storyboard))
    engine.run_task(task_id)

    rec = store.get_task(task_id)
    assert rec.status == "FAILED", "合规失败的任务不应对外报成功"
    assert "合规门禁" in (rec.error or "")
    assert rec.artifacts["scene_states"]["2"] == "BLOCKED"

    # 返工注定得到逐字相同的裁决，不该烧掉重试额度
    gen = [s["attempt"] for s in store.list_steps(task_id) if s["name"] == "generation"]
    assert gen == [1], f"BLOCKER 不应进入返工循环，实际 generation attempts={gen}"

    # 被拦截的任务更要留下审计包
    bundle = pathlib.Path(rec.artifacts["export_dir"])
    assert (bundle / "00_task.json").exists()
    assert (bundle / "05_review" / "review_report.json").exists()
    assert not (bundle / "06_final" / "final.mp4").exists(), "违规旁白不得进入成片"


def test_prompt_owned_failure_reruns_prompt_step(tmp_settings, monkeypatch):
    """提示词级失败（R2）必须重跑 prompt 步骤，否则返工无法改变 Review 的输入。"""
    store, engine = build_engine(tmp_settings)
    task_id = create_task(store, task_id="p0routing0001")

    calls = {"n": 0}

    def broken_prompts(self, ctx):
        calls["n"] += 1
        return {"topic": ctx.topic, "prompts": [
            {"scene_id": i,
             "image_prompt": "一段与商品完全无关的风景描述画面",
             "video_prompt": "镜头缓慢移动，画面稳定",
             "first_frame_desc": ""}
            for i in range(1, ctx.scene_count + 1)]}

    monkeypatch.setattr(PromptAgent, "_execute", broken_prompts)
    monkeypatch.setattr(ComposeAgent, "_execute", lambda self, ctx: {"composed": True})
    engine.run_task(task_id)

    assert calls["n"] > 1, "R2 归属 prompt，返工必须重跑 prompt 步骤"
