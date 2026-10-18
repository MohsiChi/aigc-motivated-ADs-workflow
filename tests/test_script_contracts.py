from types import SimpleNamespace

import pytest

from app.agents.base import AgentContext
from app.agents.script_agent import ScriptAgent


def context(settings, count):
    return AgentContext(task_id="script-contract", topic="跑鞋", scene_count=count,
                        settings=settings)


@pytest.mark.parametrize("count", range(1, 9))
def test_mock_script_honors_requested_count(tmp_settings, count):
    result = ScriptAgent().run(context(tmp_settings, count))
    assert result.ok, result.error
    assert len(result.output["scenes"]) == count
    assert [s["scene_id"] for s in result.output["scenes"]] == list(range(1, count + 1))


def test_default_mock_script_preserves_existing_copy(tmp_settings):
    scenes = ScriptAgent().run(context(tmp_settings, 3)).output["scenes"]
    assert [(s["title"], s["duration_s"]) for s in scenes] == [
        ("钩子开场", 3), ("卖点演示", 8), ("收尾转化", 4)]
    assert scenes[1]["narration"] == "这款跑鞋凭什么值得买？三个真实理由，一次讲清。"


def script_data(ids):
    return {"topic": "跑鞋", "scenes": [
        {"scene_id": sid, "title": "镜头", "narration": "旁白", "visual": "画面", "duration_s": 5}
        for sid in ids]}


@pytest.mark.parametrize("ids", [[1, 2], [1, 2, 3, 4], [1, 1, 3],
                                      [True, 2, 3], [1.0, 2, 3], ["1", 2, 3], [0, 2, 3], []])
def test_real_script_rejects_wrong_count_or_invalid_ids(tmp_settings, ids):
    llm = SimpleNamespace(chat_json_robust=lambda **_: script_data(ids))
    result = ScriptAgent(llm).run(context(tmp_settings.with_overrides(mock_llm=False), 3))
    assert not result.ok
    assert result.error


def test_real_script_rejects_missing_id(tmp_settings):
    data = script_data([1, 2, 3])
    del data["scenes"][0]["scene_id"]
    llm = SimpleNamespace(chat_json_robust=lambda **_: data)
    assert not ScriptAgent(llm).run(context(tmp_settings.with_overrides(mock_llm=False), 3)).ok


def test_real_script_accepts_unique_nonconsecutive_positive_ids(tmp_settings):
    llm = SimpleNamespace(chat_json_robust=lambda **_: script_data([7, 3, 42]))
    result = ScriptAgent(llm).run(context(tmp_settings.with_overrides(mock_llm=False), 3))
    assert result.ok, result.error
    assert [s["scene_id"] for s in result.output["scenes"]] == [7, 3, 42]
