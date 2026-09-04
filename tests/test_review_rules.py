"""质检规则单测（审查前此处无覆盖，而 bug 最集中）。"""
import json

from app.agents.base import AgentContext
from app.agents.review_agent import ReviewAgent
from app.config import Settings


def _ctx(tmp_path, *, narration="普通口播文案", video_prompt="镜头缓慢推近，商品特写画面",
         image_prompt=None, duration=5.0, clip_duration=None) -> AgentContext:
    kf = tmp_path / "kf.json"
    kf.write_text(json.dumps({"prompt": "x"}), encoding="utf-8")
    clip = tmp_path / "clip.json"
    clip.write_text("{}", encoding="utf-8")
    settings = Settings(data_dir=tmp_path / "data", output_dir=tmp_path / "out",
                        log_dir=tmp_path / "logs")
    return AgentContext(
        task_id="t", topic="给这个保温杯做个广告", product_info="316 不锈钢保温杯",
        scene_count=1, settings=settings,
        artifacts={
            "storyboard": {"topic": "t", "scenes": [
                {"scene_id": 1, "title": "开场", "narration": narration,
                 "visual": "保温杯特写", "duration_s": duration}]},
            "prompts": {"topic": "t", "prompts": [
                {"scene_id": 1,
                 "image_prompt": image_prompt or "保温杯 特写 电商广告质感 摄影棚布光",
                 "video_prompt": video_prompt, "first_frame_desc": ""}]},
            "generation": {"clips": [
                {"scene_id": 1, "attempt": 1, "provider": "mock",
                 "keyframe_path": str(kf), "clip_path": str(clip),
                 "duration_s": clip_duration if clip_duration is not None else duration,
                 "metadata": {}}], "attempt": 1},
        },
    )


def _verdict(tmp_path, **kw):
    result = ReviewAgent().run(_ctx(tmp_path, **kw))
    assert result.ok
    return result.output["verdicts"][0]


def test_r6_safe_words_not_flagged(tmp_path):
    """最后/最新/最近 是合法词，不应触发极限词规则（审查发现的误伤）。"""
    v = _verdict(tmp_path, narration="最后更新于今天，最新款，最近很受欢迎")
    r6 = [r for r in v["reasons"] if r.startswith("R6")]
    assert r6 == [], f"误伤: {r6}"


def test_r6_real_violations_flagged(tmp_path):
    v = _verdict(tmp_path, narration="全网最好用的杯子，顶级材质")
    assert not v["passed"]
    assert any(r.startswith("R6") for r in v["reasons"])


def test_r4_duration_out_of_spec(tmp_path):
    """场景时长已被 ScriptAgent 钳制到 [2,15]；R4 守的是 Provider 返回的异常时长。"""
    v = _verdict(tmp_path, duration=5.0, clip_duration=20.0)
    assert any(r.startswith("R4") for r in v["reasons"])
