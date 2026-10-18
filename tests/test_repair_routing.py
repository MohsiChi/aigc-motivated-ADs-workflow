"""Offline routing and delivery regressions; compose is replaced to avoid ffmpeg."""
import copy
from pathlib import Path

import pytest

from app.agents.compose_agent import ComposeAgent
from app.agents.generation_agent import GenerationAgent
from app.agents.prompt_agent import PromptAgent
from tests.conftest import build_engine, create_task


@pytest.fixture(autouse=True)
def cheap_compose(monkeypatch):
    monkeypatch.setattr(ComposeAgent, "_execute", lambda self, ctx: {"composed": True})


class RepairLLM:
    """Real PromptAgent input/output boundary, with deterministic responses."""

    def __init__(self, defect, recover=True):
        self.defect = defect
        self.recover = recover
        self.requests = []
        self.initial = None

    def chat_json_robust(self, *, system, user):
        self.requests.append(user)
        repaired = len(self.requests) > 1 and self.recover and self.defect in user
        prompts = []
        for sid in (1, 2, 3):
            image = "轻便跑鞋特写，透气网面，摄影棚布光"
            video = "镜头缓慢推近轻便跑鞋，画面稳定"
            if sid == 2 and not repaired:
                if self.defect == "R1":
                    image = "鞋"
                elif self.defect == "R2":
                    image, video = "一段与商品无关的风景描述画面", "镜头缓慢移动，画面稳定"
                elif self.defect == "R5":
                    video = "这款给这双鞋子做个广告凭什么值得买？三个真实理由，一次讲清。"
            # A broad LLM response must never overwrite an accepted scene.
            if sid != 2 and len(self.requests) > 1:
                image += "，不应覆盖的改动"
            prompts.append(dict(scene_id=sid, image_prompt=image,
                                video_prompt=video, first_frame_desc="衔接"))
        result = {"topic": "广告", "prompts": prompts}
        if self.initial is None:
            self.initial = copy.deepcopy(result)
        return result


@pytest.mark.parametrize("rule", ["R1", "R2", "R5"])
def test_prompt_repair_recovers_with_feedback_and_keeps_accepted_assets(tmp_settings, monkeypatch, rule):
    store, engine = build_engine(tmp_settings.with_overrides(mock_llm=False))
    llm = RepairLLM(rule)
    engine._agents["prompt"] = PromptAgent(llm)
    generated = []
    original = GenerationAgent._execute

    def capture(self, ctx):
        result = original(self, ctx)
        generated.append(copy.deepcopy(result))
        return result

    monkeypatch.setattr(GenerationAgent, "_execute", capture)
    task_id = create_task(store)
    engine.run_task(task_id)
    rec = store.get_task(task_id)
    assert rec.status == "SUCCEEDED", rec.error
    assert rec.artifacts["review_report"]["passed_all"] is True
    assert len(llm.requests) == 2
    assert rule in llm.requests[1]
    assert "轻便跑鞋，透气网面" in llm.requests[1]
    assert len(generated) == 2
    for index in (0, 2):
        assert generated[0]["clips"][index] == generated[1]["clips"][index]
        for key, value in llm.initial["prompts"][index].items():
            assert rec.artifacts["prompts"]["prompts"][index][key] == value
    repaired = rec.artifacts["prompts"]["prompts"][1]
    assert generated[1]["clips"][1]["metadata"]["clip"]["prompt"] == repaired["video_prompt"]
    assert generated[1]["clips"][1]["attempt"] == 2


def test_generation_owned_retry_leaves_prompt_step_alone(tmp_settings, monkeypatch):
    store, engine = build_engine(tmp_settings)
    original = GenerationAgent._execute

    def missing_once(self, ctx):
        result = original(self, ctx)
        if ctx.extra.get("attempt", 1) == 1:
            Path(result["clips"][1]["keyframe_path"]).unlink()
        return result

    monkeypatch.setattr(GenerationAgent, "_execute", missing_once)
    task_id = create_task(store)
    engine.run_task(task_id)
    assert store.get_task(task_id).artifacts["review_report"]["passed_all"]
    assert [s["attempt"] for s in store.list_steps(task_id) if s["name"] == "prompt"] == [1]
    assert [s["attempt"] for s in store.list_steps(task_id) if s["name"] == "generation"] == [1, 2]


@pytest.mark.parametrize("blocked_scene", [1, 2])
def test_blocker_emerging_on_retry_prevents_compose(tmp_settings, monkeypatch, blocked_scene):
    store, engine = build_engine(tmp_settings)
    original = GenerationAgent._execute

    def mutate_on_retry(self, ctx):
        result = original(self, ctx)
        if ctx.extra.get("attempt", 1) == 1:
            Path(result["clips"][1]["keyframe_path"]).unlink()
        else:
            ctx.artifacts["storyboard"]["scenes"][blocked_scene - 1]["narration"] = "全网首发的跑鞋"
        return result

    monkeypatch.setattr(GenerationAgent, "_execute", mutate_on_retry)
    task_id = create_task(store)
    engine.run_task(task_id)
    rec = store.get_task(task_id)
    assert rec.status == "FAILED", rec.error
    assert "合规门禁" in rec.error
    assert "compose" not in rec.artifacts
    assert rec.artifacts["scene_states"][str(blocked_scene)] == ("ACCEPTED" if blocked_scene == 1 else "BLOCKED")
    assert [s["attempt"] for s in store.list_steps(task_id) if s["name"] == "generation"] == [1, 2]
    assert [s["attempt"] for s in store.list_steps(task_id) if s["name"] == "prompt"] == [1]


@pytest.mark.parametrize("retries", [0, 1, 2])
def test_prompt_retry_budget_is_bounded(tmp_settings, retries):
    store, engine = build_engine(tmp_settings.with_overrides(mock_llm=False, max_scene_retries=retries))
    llm = RepairLLM("R2", recover=False)
    engine._agents["prompt"] = PromptAgent(llm)
    task_id = create_task(store)
    engine.run_task(task_id)
    rec = store.get_task(task_id)
    assert rec.status == "SUCCEEDED", rec.error
    assert rec.artifacts["scene_states"]["2"] == "ACCEPTED_FALLBACK"
    assert len(llm.requests) == retries + 1
    assert [s["attempt"] for s in store.list_steps(task_id) if s["name"] == "generation"] == list(range(1, retries + 2))


def test_exhausted_missing_assets_cannot_be_delivered(tmp_settings, monkeypatch):
    store, engine = build_engine(tmp_settings.with_overrides(max_scene_retries=0))
    original = GenerationAgent._execute

    def missing(self, ctx):
        result = original(self, ctx)
        Path(result["clips"][1]["clip_path"]).unlink()
        return result

    monkeypatch.setattr(GenerationAgent, "_execute", missing)
    task_id = create_task(store)
    engine.run_task(task_id)
    rec = store.get_task(task_id)
    assert rec.status == "FAILED"
    assert "素材" in rec.error
    assert "compose" not in rec.artifacts
