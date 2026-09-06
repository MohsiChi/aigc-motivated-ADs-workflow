"""F15 回归测试：已 ACCEPTED 的镜头在后续轮次质检翻转为不通过时的正确行为。

构造方式：让镜头 2 的提示词必然触发 R2（MAJOR，可返工）以产生返工轮；
在第 2 轮生成开始前删掉镜头 1 的关键帧文件，使镜头 1 在复检时 R3 失败。
这是唯一可达的翻转路径（R1/R2/R5/R6 的输入在返工轮之间不变，只有 R3 查磁盘）。

修复前（F15）的行为：镜头 1 留在 rejected 里 -> ACCEPTED->GENERATING 非法流转被吞成
warning -> 镜头 1 被无谓重新生成 -> 收尾再非法一次 -> 状态机/报告/行为三者矛盾且无留痕。
修复后的行为：终态不动、留下不一致告警、镜头 1 不被重生成、全程无非法流转。
"""
import pathlib

from app.agents.generation_agent import GenerationAgent
from app.agents.prompt_agent import PromptAgent
from tests.conftest import build_engine, create_task

GOOD_IMG = "轻便跑鞋特写，透气网面，电商广告质感，摄影棚布光"
BAD_IMG = "一段与商品完全无关的风景描述画面"


def test_accepted_scene_late_r3_failure_is_recorded_not_regenerated(tmp_settings, monkeypatch):
    store, engine = build_engine(tmp_settings)
    task_id = create_task(store, task_id="f15test000001")

    def partial_bad_prompts(self, ctx):
        """镜头 2 触发 R2（MAJOR，可返工）；镜头 1/3 正常通过。"""
        prompts = []
        for i in range(1, ctx.scene_count + 1):
            bad = (i == 2)
            prompts.append({
                "scene_id": i,
                "image_prompt": BAD_IMG if bad else GOOD_IMG,
                "video_prompt": "镜头缓慢移动，画面稳定" if bad else "镜头缓慢推近跑鞋，画面稳定",
                "first_frame_desc": "",
            })
        return {"topic": ctx.topic, "prompts": prompts}

    monkeypatch.setattr(PromptAgent, "_execute", partial_bad_prompts)

    real_gen = GenerationAgent._execute
    seen_scene_ids = []

    def gen_wrapper(self, ctx):
        # 记录每轮 generation 收到的 scene_ids（首轮为空 dict，返工轮才有）
        seen_scene_ids.append(list(ctx.extra.get("scene_ids") or []))
        if len(seen_scene_ids) >= 2:
            # 第 2 轮起：删掉镜头 1 的关键帧，制造 R3 翻转
            for c in ctx.artifacts.get("generation", {}).get("clips", []):
                if c["scene_id"] == 1:
                    pathlib.Path(c["keyframe_path"]).unlink(missing_ok=True)
        return real_gen(self, ctx)

    monkeypatch.setattr(GenerationAgent, "_execute", gen_wrapper)

    engine.run_task(task_id)
    rec = store.get_task(task_id)

    # ① 终态不动：已 ACCEPTED 的镜头不因后续矛盾裁决而回退
    assert rec.artifacts["scene_states"]["1"] == "ACCEPTED", rec.artifacts["scene_states"]

    # ② 矛盾必须留痕，不能静默
    assert any("不一致" in w and "镜头 1" in w for w in rec.warnings), rec.warnings

    # ③ 镜头 1 不得被无谓重新生成（返工轮的 scene_ids 里不能有它）
    retry_rounds = [s for s in seen_scene_ids[1:] if s]
    assert retry_rounds, "应当至少发生一轮返工（镜头 2 的 R2 失败）"
    assert all(1 not in s for s in retry_rounds), f"镜头 1 被无谓重生成: {retry_rounds}"

    # ④ 全程不因非法流转而崩（选项 A：记警告并继续，任务仍可完成）
    assert "非法状态流转" not in (rec.error or ""), rec.error
    assert rec.status == "SUCCEEDED", (rec.status, rec.error)

    # ⑤ 镜头 2 走的是正常返工->降级路径（证明修复没有削弱原有行为）
    assert rec.artifacts["scene_states"]["2"] == "ACCEPTED_FALLBACK"
    assert any("降级采用" in w for w in rec.warnings), rec.warnings