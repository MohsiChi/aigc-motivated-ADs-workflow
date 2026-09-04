"""Mock Provider：把"生成产物"以 JSON sidecar 文件真实写盘。

为什么写真实文件而不是纯字符串？
- 质检 Agent 的"文件存在性"规则是真的在校验磁盘上的产物；
- 每一次尝试的产物都真实落盘留痕（含 attempt 路径可查），可直接打开查看。
"""
from __future__ import annotations

import json
import time
import uuid
from pathlib import Path

from app.core.output_layout import task_dirs
from app.tools.providers.base import GenerationProvider


class MockProvider(GenerationProvider):
    name = "mock"

    def __init__(self, output_root: Path, latency_s: float = 0.02):
        self._root = Path(output_root)
        self._latency = latency_s

    def generate_image(self, *, task_id: str, scene_id: int, prompt: str,
                       aspect_ratio: str = "9:16") -> dict:
        time.sleep(self._latency)  # 模拟真实生成耗时
        dirs = task_dirs(self._root / task_id)
        path = dirs["keyframes"] / f"scene_{scene_id:02d}_keyframe.json"
        artifact = {
            "type": "keyframe_image",
            "scene_id": scene_id,
            "prompt": prompt,
            "aspect_ratio": aspect_ratio,
            "provider": self.name,
            "fake_url": f"mock://image/{uuid.uuid4().hex}",
        }
        path.write_text(json.dumps(artifact, ensure_ascii=False, indent=2), encoding="utf-8")
        return {"path": str(path), "aspect_ratio": aspect_ratio,
                "provider": self.name, "metadata": artifact}

    def generate_video_clip(self, *, task_id: str, scene_id: int, prompt: str,
                            duration_s: float, keyframe_path: str | None = None) -> dict:
        time.sleep(self._latency)
        dirs = task_dirs(self._root / task_id)
        path = dirs["clips"] / f"scene_{scene_id:02d}_clip.json"
        artifact = {
            "type": "video_clip",
            "scene_id": scene_id,
            "prompt": prompt,
            "duration_s": duration_s,
            "first_frame_ref": keyframe_path,  # 首尾帧衔接：明确记录首帧来源
            "provider": self.name,
            "fake_url": f"mock://video/{uuid.uuid4().hex}",
        }
        path.write_text(json.dumps(artifact, ensure_ascii=False, indent=2), encoding="utf-8")
        return {"path": str(path), "duration_s": duration_s,
                "provider": self.name, "metadata": artifact}
