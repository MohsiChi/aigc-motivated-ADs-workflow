"""Prompt Agent：分镜脚本 -> 逐镜生成提示词。

每个镜头产出三件套：
- image_prompt：关键帧图生提示词
- video_prompt：图生视频动态提示词（运镜 + 主体动作）
- first_frame_desc：首帧衔接控制说明（传承蛋壳黑陶首尾帧串联经验）
"""
from __future__ import annotations

import json

from app.agents.base import AgentContext, AgentError, BaseAgent
from app.core.models import PromptPack, ScenePrompt, Storyboard
from app.llm.client import DeepSeekClient

SYSTEM_PROMPT = """你是 AI 生成提示词工程师，熟悉即梦等图生视频模型的提示词写法。
任务：把分镜脚本的每个镜头转成三件套：
- image_prompt：关键帧图生提示词（主体+场景+光线+质感+构图+风格，40~120 字）
- image_prompt_en：image_prompt 的英文版（给 Flux 等英文训练的文生图模型用；商品广告的英文主体词必须准确，如 running shoes / thermos bottle）
- video_prompt：图生视频动态提示词（运镜+主体动作，20~60 字）
- first_frame_desc：首帧衔接控制说明（如何与上一镜保持光线/色调/主体位置连续）
只输出一个 JSON 对象，不要任何解释文字。格式：
{"topic": "...", "prompts": [{"scene_id": 1, "image_prompt": "...", "image_prompt_en": "...", "video_prompt": "...", "first_frame_desc": "..."}]}"""


class PromptAgent(BaseAgent):
    name = "prompt"

    def __init__(self, llm: DeepSeekClient | None = None):
        super().__init__()
        self._llm = llm

    def _execute(self, ctx: AgentContext) -> dict:
        storyboard = Storyboard.model_validate(ctx.artifacts["storyboard"])
        only = ctx.extra.get("scene_ids")
        scenes = [s for s in storyboard.scenes if only is None or s.scene_id in only]
        if not scenes:
            raise AgentError("没有可生成提示词的镜头")
        selected = Storyboard(topic=storyboard.topic, scenes=scenes)
        if ctx.settings.mock_llm or self._llm is None:
            pack = self._mock_prompts(ctx, selected)
        else:
            user_prompt = (
                f"主题：{ctx.topic}\n商品信息：{ctx.product_info}\n"
                f"分镜脚本：{selected.model_dump_json()}\n"
                f"只为镜头 {[s.scene_id for s in scenes]} 输出提示词三件套。只输出 JSON。"
            )
            feedback = ctx.extra.get("review_feedback") or {}
            if feedback:
                previous = [p for p in ctx.artifacts.get("prompts", {}).get("prompts", [])
                            if p["scene_id"] in {s.scene_id for s in scenes}]
                user_prompt += (
                    f"\n这是第 {int(ctx.extra.get('attempt', 2)) - 1} 次返工。"
                    "请逐条修复质检问题，保留商品信息并重新编写提示词。"
                    f"\n上轮提示词：{json.dumps(previous, ensure_ascii=False)}"
                    f"\n质检反馈：{json.dumps(feedback, ensure_ascii=False)}"
                )
            data = self._llm.chat_json_robust(system=SYSTEM_PROMPT, user=user_prompt)
            self._repair_in_place(data)
            try:
                pack = PromptPack.model_validate(data)
            except Exception as e:
                raise AgentError(f"提示词 JSON 校验失败: {e}") from e
        expected = {s.scene_id for s in scenes}
        # 返工响应可能仍包含全片；只采纳本轮指定镜头，保护已通过的提示词。
        generated = [p for p in pack.prompts if only is None or p.scene_id in expected]
        got = {p.scene_id for p in generated}
        if expected != got or len(generated) != len(expected):
            raise AgentError(f"提示词镜头覆盖不完整: 期望 {sorted(expected)}, 实际 {sorted(got)}")
        previous = ctx.artifacts.get("prompts", {}).get("prompts", []) if only is not None else []
        merged = [ScenePrompt.model_validate(p) for p in previous if p["scene_id"] not in expected]
        merged.extend(generated)
        merged.sort(key=lambda p: p.scene_id)
        return PromptPack(topic=storyboard.topic, prompts=merged).model_dump()

    @staticmethod
    def _repair_in_place(data: dict) -> None:
        """防御性修复：LLM 偶尔不按契约输出（真实冒烟抓到过：把分镜字段原样返回）。

        策略：缺 image_prompt/video_prompt 的条目，用其携带的 visual/title 字段
        按固定模板合成——确定性兜底，不再赌下一轮 LLM。
        """
        for item in data.get("prompts", []):
            if not isinstance(item, dict):
                continue
            visual = str(item.get("visual") or item.get("title") or "").strip()
            if "image_prompt" not in item or not str(item.get("image_prompt", "")).strip():
                item["image_prompt"] = (
                    f"{visual}，商品特写，电商广告质感，摄影棚布光，竖屏 9:16 构图，细节丰富"
                )
            if "video_prompt" not in item or not str(item.get("video_prompt", "")).strip():
                item["video_prompt"] = f"镜头缓慢推近，{visual}，画面稳定，光影自然过渡"
            if "first_frame_desc" not in item:
                item["first_frame_desc"] = "与上一镜保持光线与色调一致，主体位置居中，避免跳变"

    @staticmethod
    def _mock_prompts(ctx: AgentContext, storyboard: Storyboard) -> PromptPack:
        prompts = [
            ScenePrompt(
                scene_id=s.scene_id,
                image_prompt=(
                    f"{s.visual}，商品特写，电商广告质感，摄影棚布光，竖屏 9:16 构图，细节丰富"
                ),
                image_prompt_en=(
                    "commercial product advertisement still, studio lighting, vertical 9:16, high detail"
                ),
                video_prompt=f"镜头缓慢推近，{s.visual}，画面稳定，光影自然过渡",
                first_frame_desc="与上一镜保持光线与色调一致，主体位置居中，避免跳变",
            )
            for s in storyboard.scenes
        ]
        return PromptPack(topic=ctx.topic, prompts=prompts)
