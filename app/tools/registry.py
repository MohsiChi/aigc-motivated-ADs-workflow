"""工具注册表：用 JSON Schema 管理工具（OpenAI function calling 格式）。

标准 OpenAI function calling 回路的可复用组件化：
- openai_schema()：给 LLM 看的"菜单"
- dispatch()：LLM 点菜后，程序真正执行（真正干活的永远是代码，不是 LLM）
"""
from __future__ import annotations

import json
from typing import Any, Callable

from app.tools.providers.base import GenerationProvider


class ToolRegistry:
    def __init__(self):
        self._tools: dict[str, dict] = {}

    def register(self, fn: Callable, *, name: str, description: str, parameters: dict) -> None:
        self._tools[name] = {
            "fn": fn,
            "spec": {
                "type": "function",
                "function": {
                    "name": name,
                    "description": description,
                    "parameters": parameters,
                },
            },
        }

    def openai_schema(self) -> list[dict]:
        """OpenAI tools 参数要的格式。"""
        return [t["spec"] for t in self._tools.values()]

    def names(self) -> list[str]:
        return list(self._tools)

    def dispatch(self, name: str, arguments: dict) -> str:
        """执行工具，返回 JSON 字符串（标准 tool 结果，可直接喂回 LLM）。"""
        if name not in self._tools:
            return json.dumps({"error": f"未知工具: {name}"}, ensure_ascii=False)
        try:
            result = self._tools[name]["fn"](**arguments)
        except Exception as e:  # noqa: BLE001 工具错误作为结果返回，由上层决定重试
            return json.dumps({"error": f"{type(e).__name__}: {e}"}, ensure_ascii=False)
        return json.dumps(result, ensure_ascii=False)


def build_default_registry(provider: GenerationProvider, task_id: str) -> ToolRegistry:
    """为某个任务装配生成类工具表（工具内部绑定 task_id，产物落到任务目录）。"""
    registry = ToolRegistry()

    def generate_keyframe_image(scene_id: int, prompt: str, aspect_ratio: str = "9:16") -> dict:
        return provider.generate_image(
            task_id=task_id, scene_id=int(scene_id), prompt=prompt, aspect_ratio=aspect_ratio
        )

    def generate_video_clip(scene_id: int, prompt: str, duration_s: float = 5.0,
                            keyframe_path: str = "") -> dict:
        return provider.generate_video_clip(
            task_id=task_id, scene_id=int(scene_id), prompt=prompt,
            duration_s=float(duration_s), keyframe_path=keyframe_path or None,
        )

    registry.register(
        generate_keyframe_image,
        name="generate_keyframe_image",
        description="根据提示词为指定镜头生成关键帧图片",
        parameters={
            "type": "object",
            "properties": {
                "scene_id": {"type": "integer", "description": "镜头编号"},
                "prompt": {"type": "string", "description": "关键帧图生提示词"},
                "aspect_ratio": {"type": "string", "description": "画幅，默认 9:16", "default": "9:16"},
            },
            "required": ["scene_id", "prompt"],
        },
    )
    registry.register(
        generate_video_clip,
        name="generate_video_clip",
        description="基于关键帧与动态提示词，为指定镜头图生视频（请把关键帧路径作为 keyframe_path 传入，保证首帧衔接）",
        parameters={
            "type": "object",
            "properties": {
                "scene_id": {"type": "integer", "description": "镜头编号"},
                "prompt": {"type": "string", "description": "图生视频动态提示词"},
                "duration_s": {"type": "number", "description": "时长（秒）", "default": 5.0},
                "keyframe_path": {"type": "string", "description": "关键帧文件路径（首帧衔接）"},
            },
            "required": ["scene_id", "prompt"],
        },
    )
    return registry
