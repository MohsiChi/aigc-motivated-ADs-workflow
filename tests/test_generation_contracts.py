import json
from types import SimpleNamespace

import pytest

from app.agents.base import AgentContext, AgentError
from app.agents.generation_agent import GenerationAgent
from app.core.models import Scene, ScenePrompt
from app.tools.registry import build_default_registry


IMAGE = "generate_keyframe_image"
VIDEO = "generate_video_clip"


def call(name=IMAGE, sid=2, **args):
    return SimpleNamespace(id=f"{name}-{sid}", function=SimpleNamespace(
        name=name, arguments=json.dumps({"scene_id": sid, "prompt": "valid prompt", **args})))


class FakeLLM:
    def __init__(self, rounds):
        self.rounds = iter(rounds)
        self.requests = []

    def chat(self, *, messages, tools):
        self.requests.append(list(messages))
        return SimpleNamespace(tool_calls=next(self.rounds, []))


class FakeProvider:
    def __init__(self, image_outputs=None):
        self.calls = []
        self.image_outputs = iter(image_outputs or [])

    def generate_image(self, **args):
        self.calls.append((IMAGE, args))
        return next(self.image_outputs, {"path": f"image-{args['scene_id']}.png"})

    def generate_video_clip(self, **args):
        self.calls.append((VIDEO, args))
        return {"path": f"video-{args['scene_id']}.mp4", "provider": "fake"}


def inputs(ids=(2,)):
    scenes = [Scene(scene_id=sid, title="镜头", narration="旁白", visual="画面", duration_s=8)
              for sid in ids]
    prompts = {sid: ScenePrompt(scene_id=sid, image_prompt="中文图", video_prompt="中文视频",
                               image_prompt_en=f"English scene {sid}") for sid in ids}
    return scenes, prompts


def run_loop(rounds, ids=(2,), provider=None, max_rounds=None, en_by_id=None):
    llm = FakeLLM(rounds)
    provider = provider or FakeProvider()
    agent = GenerationAgent(provider, llm)
    registry = build_default_registry(provider, "contract")
    dispatched = []
    dispatch = registry.dispatch

    def record_dispatch(name, args):
        dispatched.append((name, dict(args)))
        return dispatch(name, args)

    registry.dispatch = record_dispatch
    scenes, prompts = inputs(ids)
    results = {}
    kwargs = {} if max_rounds is None else {"max_rounds": max_rounds}
    agent._llm_tool_loop(SimpleNamespace(task_id="contract", extra={}), registry,
                         scenes, prompts, results, en_by_id=en_by_id, **kwargs)
    return results, provider, llm, dispatched


@pytest.mark.parametrize("sid", [1, True, 2.0, "2", None])
def test_invalid_or_out_of_scope_scene_never_dispatches(sid):
    _, provider, llm, dispatched = run_loop([[call(sid=sid)], []])
    assert not dispatched
    assert not provider.calls
    assert "error" in json.loads(llm.requests[-1][-1]["content"])


@pytest.mark.parametrize("raw", ['{"scene_id":', "[]", "null", '"string"'])
def test_bad_arguments_return_tool_error_then_recover(raw):
    bad = call()
    bad.function.arguments = raw
    results, _, llm, _ = run_loop([[bad], [call()], []])
    assert results[(IMAGE, 2)]["path"] == "image-2.png"
    assert llm.requests[1][-1]["role"] == "tool"
    assert "error" in json.loads(llm.requests[1][-1]["content"])


def test_unknown_tool_never_dispatches():
    _, _, llm, dispatched = run_loop([[call(name="delete_everything")], []])
    assert not dispatched
    assert "error" in json.loads(llm.requests[-1][-1]["content"])


@pytest.mark.parametrize("prompt", [None, [], 42, "", "   "])
def test_invalid_prompt_never_reaches_provider(prompt):
    _, provider, llm, dispatched = run_loop([[call(prompt=prompt)], []])
    assert not provider.calls
    assert not dispatched
    assert "error" in json.loads(llm.requests[-1][-1]["content"])


@pytest.mark.parametrize("duration", [None, True, "8", -1, 0, 16, float("nan"), float("inf"), 10**400])
def test_invalid_video_duration_never_reaches_provider(duration):
    _, provider, llm, _ = run_loop([[call()], [call(VIDEO, duration_s=duration)], []])
    assert [name for name, _ in provider.calls] == [IMAGE]
    assert "error" in json.loads(llm.requests[-1][-1]["content"])


@pytest.mark.parametrize("keyframe_path", [None, "", "image-1.png"])
def test_video_uses_authoritative_image_for_same_scene(keyframe_path):
    _, provider, _, _ = run_loop([[call()], [call(VIDEO, keyframe_path=keyframe_path)], []])
    assert provider.calls[1][1]["keyframe_path"] == "image-2.png"
    assert provider.calls[1][1]["duration_s"] == 8


def test_video_without_same_scene_image_is_rejected():
    _, provider, llm, dispatched = run_loop([[call(VIDEO, keyframe_path="image-1.png")], []])
    assert not provider.calls
    assert not dispatched
    assert "error" in json.loads(llm.requests[-1][-1]["content"])


def test_duplicate_successes_do_not_repeat_provider_work():
    _, provider, llm, _ = run_loop([[call()], [call(VIDEO)], [call(), call(VIDEO)], []])
    assert [name for name, _ in provider.calls] == [IMAGE, VIDEO]
    assert len([m for m in llm.requests[-1] if isinstance(m, dict) and m["role"] == "tool"]) == 4


@pytest.mark.parametrize("bad_output", [{"error": "temporary"}, {}, {"path": ""},
                                       {"path": "  "}, {"path": 42}])
def test_failed_or_invalid_outputs_are_not_cached(bad_output):
    provider = FakeProvider([bad_output, {"path": "recovered.png"}])
    results, provider, llm, _ = run_loop([[call()], [call()], []], provider=provider)
    assert len(provider.calls) == 2
    assert results[(IMAGE, 2)]["path"] == "recovered.png"
    assert "error" in json.loads(llm.requests[1][-1]["content"])


def test_eight_scenes_allow_sequential_image_and_video_calls():
    rounds = [[call(name, sid)] for sid in range(1, 9) for name in (IMAGE, VIDEO)] + [[]]
    results, provider, _, _ = run_loop(rounds, ids=tuple(range(1, 9)))
    assert len(results) == len(provider.calls) == 16


@pytest.mark.parametrize("explicit_limit, expected", [(None, 12), (3, 3)])
def test_runaway_remains_bounded(explicit_limit, expected):
    llm = FakeLLM([[call(sid=1)]] * 30)
    provider = FakeProvider()
    scenes, prompts = inputs()
    kwargs = {} if explicit_limit is None else {"max_rounds": explicit_limit}
    with pytest.raises(AgentError, match=f"{expected}"):
        GenerationAgent(provider, llm)._llm_tool_loop(
            SimpleNamespace(task_id="contract", extra={}),
            build_default_registry(provider, "contract"), scenes, prompts, {}, **kwargs)
    assert len(llm.requests) == expected


def test_supplement_preserves_cache_and_english_prompts_and_retry_is_fresh(tmp_settings):
    scenes, prompts = inputs((1, 2))
    ctx = AgentContext(task_id="contract", topic="topic", scene_count=2,
                       settings=tmp_settings.with_overrides(mock_llm=False), artifacts={
                           "storyboard": {"topic": "topic", "scenes": [s.model_dump() for s in scenes]},
                           "prompts": {"topic": "topic", "prompts": [p.model_dump() for p in prompts.values()]}})
    llm = FakeLLM([[call(sid=1), call(VIDEO, 1)], [],
                   [call(sid=2), call(VIDEO, 2)], [],
                   [call(sid=2), call(VIDEO, 2)], []])
    provider = FakeProvider()
    agent = GenerationAgent(provider, llm)
    first = agent.run(ctx)
    assert first.ok, first.error
    assert provider.calls[2][1]["prompt"].startswith("English scene 2")
    ctx.artifacts["generation"] = first.output
    ctx.extra = {"scene_ids": [2], "attempt": 2}
    second = agent.run(ctx)
    assert second.ok, second.error
    assert len(provider.calls) == 6
    assert [c["attempt"] for c in second.output["clips"]] == [1, 2]


def test_supplement_reuses_image_already_generated(tmp_settings):
    scenes, prompts = inputs()
    ctx = AgentContext(task_id="contract", topic="topic", scene_count=1,
                       settings=tmp_settings.with_overrides(mock_llm=False), artifacts={
                           "storyboard": {"topic": "topic", "scenes": [s.model_dump() for s in scenes]},
                           "prompts": {"topic": "topic", "prompts": [p.model_dump() for p in prompts.values()]}})
    llm = FakeLLM([[call()], [], [call(), call(VIDEO)], []])
    provider = FakeProvider()
    result = GenerationAgent(provider, llm).run(ctx)
    assert result.ok, result.error
    assert [name for name, _ in provider.calls] == [IMAGE, VIDEO]
