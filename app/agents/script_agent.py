"""Script Agent（编剧）：主题 + 商品信息 -> 分镜脚本。

真实模式：DeepSeek 写脚本，要求严格 JSON 输出（Prompt Engineering 的落点）。
Mock 模式：确定性三段式脚本（3秒钩子 -> 卖点演示 -> 收尾转化），
这是 TikTok 商品广告的通用节奏，也保证测试与演示可复现。
"""
from __future__ import annotations

from app.agents.base import AgentContext, AgentError, BaseAgent
from app.core.models import Scene, Storyboard
from app.llm.client import DeepSeekClient

SYSTEM_PROMPT = """你是资深 TikTok 电商短视频编剧。
要求：
1. 为给定商品/主题写竖屏短视频广告分镜脚本。
2. 每个镜头包含 title（小标题）、narration（口播旁白）、visual（画面描述）、duration_s（2~15 秒）。
3. 结构遵循"3 秒钩子 -> 卖点演示 -> 收尾转化"的节奏。
4. 文案合规：禁止使用"最""第一""顶级""国家级""绝对"等广告法极限词。
5. 只输出一个 JSON 对象，不要任何解释文字。格式：
{"topic": "...", "scenes": [{"scene_id": 1, "title": "...", "narration": "...", "visual": "...", "duration_s": 5}]}"""


class ScriptAgent(BaseAgent):
    name = "script"

    def __init__(self, llm: DeepSeekClient | None = None):
        super().__init__()
        self._llm = llm

    def _execute(self, ctx: AgentContext) -> dict:
        if ctx.settings.mock_llm or self._llm is None:
            return self._mock_storyboard(ctx).model_dump()
        user_prompt = (
            f"商品/主题：{ctx.topic}\n"
            f"商品信息：{ctx.product_info or '（无）'}\n"
            f"请写 {ctx.scene_count} 个镜头。只输出 JSON。"
        )
        data = self._llm.chat_json_robust(system=SYSTEM_PROMPT, user=user_prompt)
        self._clamp_durations(data)
        try:
            storyboard = Storyboard.model_validate(data)
        except Exception as e:
            raise AgentError(f"脚本 JSON 校验失败: {e}") from e
        if not storyboard.scenes:
            raise AgentError("LLM 返回了空脚本")
        return storyboard.model_dump()

    @staticmethod
    def _clamp_durations(data: dict) -> None:
        """防御：LLM 可能给出超规格时长，钳制到 [2,15]（与质检 R4 一致），
        避免"模型放行、质检必拒"的白烧返工（审查发现的契约矛盾）。"""
        for scene in data.get("scenes", []):
            if isinstance(scene, dict) and isinstance(scene.get("duration_s"), (int, float)):
                scene["duration_s"] = min(max(float(scene["duration_s"]), 2.0), 15.0)

    @staticmethod
    def _mock_storyboard(ctx: AgentContext) -> Storyboard:
        topic = ctx.topic.strip()
        scenes = [
            Scene(
                scene_id=1, title="钩子开场",
                narration=f"别划走！{topic}，3 秒就让你心动。",
                visual=f"{topic} 特写，开场强视觉冲击，光影对比鲜明",
                duration_s=3,
            ),
            Scene(
                scene_id=2, title="卖点演示",
                narration=f"这款{topic}凭什么值得买？三个真实理由，一次讲清。",
                visual=f"{topic} 真实使用场景，细节特写，节奏明快",
                duration_s=8,
            ),
            Scene(
                scene_id=3, title="收尾转化",
                narration="点击下方小黄车，限时优惠，手慢无！",
                visual="产品全景 + 价格标签 + 购买引导按钮，氛围明亮温暖",
                duration_s=4,
            ),
        ][: ctx.scene_count]
        return Storyboard(topic=topic, scenes=scenes)
