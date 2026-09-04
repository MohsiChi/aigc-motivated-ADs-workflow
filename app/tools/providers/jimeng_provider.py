"""生产级生成 Provider 占位 —— v2 待办。

这一层是"接入真实多模态生成"的预留插座：任何后端只要实现
generate_image / generate_video_clip 两个方法，即可通过环境变量
GEN_PROVIDER 切换接入，上层 Agent 与 API 零改动。候选后端：
- 即梦 / Seedance（火山引擎方舟平台，图生视频强项）
- GPT Image（OpenAI images API）
- Gemini（Google 图像/视频生成）
- 其他任意文生图/图生视频服务

接入要做的事（与 TikTok Shop 业务同构的"异步任务"模式）：
1. 从环境变量读 API Key（JIMENG_API_KEY / ARK_API_KEY），绝不写进代码；
2. 提交生成任务 -> 拿到 task id -> 轮询任务状态 -> 完成后下载产物；
3. 失败分类：内容审核拒绝（不可重试）/ 超时与限流（可重试）；
4. 成本记录：每次调用计费 -> 写入 steps/事件表，支持成本核算与用量治理。
"""
from __future__ import annotations

from app.tools.providers.base import GenerationProvider


class JimengProvider(GenerationProvider):
    name = "jimeng"

    def __init__(self):
        raise NotImplementedError(
            "v2 待办：接入火山引擎即梦 API。接口骨架见本文件 docstring。"
        )

    def generate_image(self, *, task_id: str, scene_id: int, prompt: str,
                       aspect_ratio: str = "9:16") -> dict:
        raise NotImplementedError

    def generate_video_clip(self, *, task_id: str, scene_id: int, prompt: str,
                            duration_s: float, keyframe_path: str | None = None) -> dict:
        raise NotImplementedError
