"""素材生成 Provider 抽象。

v1 = MockProvider；v2 增加 JimengProvider（火山引擎即梦）时，
上层 Agent 与 Tool 注册表一行代码都不用改——这就是"这层抽象的意义"
（工具内部实现可以从查字典换成发网络请求，签名不变，上层无感）。
"""
from __future__ import annotations

from abc import ABC, abstractmethod


class GenerationProvider(ABC):
    name: str = "base"

    @abstractmethod
    def generate_image(self, *, task_id: str, scene_id: int, prompt: str,
                       aspect_ratio: str = "9:16") -> dict:
        """生成关键帧图。返回 {"path": ..., "provider": ..., "metadata": {...}}。"""

    @abstractmethod
    def generate_video_clip(self, *, task_id: str, scene_id: int, prompt: str,
                            duration_s: float, keyframe_path: str | None = None) -> dict:
        """图生视频。返回 {"path": ..., "provider": ..., "duration_s": ..., "metadata": {...}}。"""
