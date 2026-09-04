"""Pydantic 数据模型：Agent 之间的"契约"。

Artifact 驱动架构：每个 Agent 只接收/产出结构化产物（而不是背着全量对话历史），
这是解决"上下文越滚越长"的正确姿势。
"""
from __future__ import annotations

from pydantic import BaseModel, Field


# ---------------- Script Agent 产物 ----------------

class Scene(BaseModel):
    """单个镜头的脚本。"""

    scene_id: int = Field(..., ge=1)
    title: str
    narration: str = Field(..., description="旁白/口播文案")
    visual: str = Field(..., description="画面内容描述（自然语言）")
    duration_s: float = Field(5.0, ge=2, le=15, description="建议时长（秒）；与质检 R4 的 [2,15] 平台规格一致")


class Storyboard(BaseModel):
    """分镜脚本（整片）。"""

    topic: str
    scenes: list[Scene]


# ---------------- Prompt Agent 产物 ----------------

class ScenePrompt(BaseModel):
    """单个镜头的生成提示词。"""

    scene_id: int
    image_prompt: str = Field(..., description="关键帧图生提示词")
    image_prompt_en: str = Field("", description="英文版图生提示词（Flux 等英文训练模型用；为空则回退中文版）")
    video_prompt: str = Field(..., description="图生视频动态提示词")
    first_frame_desc: str = Field("", description="首帧衔接控制说明（传承蛋壳黑陶首尾帧经验）")


class PromptPack(BaseModel):
    """整片的提示词包。"""

    topic: str
    prompts: list[ScenePrompt]


# ---------------- Generation Agent 产物 ----------------

class Clip(BaseModel):
    """一条素材片段。"""

    scene_id: int
    attempt: int = 1
    provider: str
    keyframe_path: str
    clip_path: str
    duration_s: float
    metadata: dict = Field(default_factory=dict)


# ---------------- Review Agent 产物 ----------------

class SceneVerdict(BaseModel):
    """单个镜头的质检裁决。"""

    scene_id: int
    passed: bool
    reasons: list[str] = Field(default_factory=list)


class ReviewReport(BaseModel):
    """质检报告。"""

    passed_all: bool
    verdicts: list[SceneVerdict]
    rules_version: str = "v1"


# ---------------- 任务主体 ----------------

class TaskRecord(BaseModel):
    """任务持久化记录（与 tasks 表一一对应）。"""

    id: str
    topic: str
    product_info: str = ""
    scene_count: int = 3
    status: str = "PENDING"
    current_step: str | None = None
    error: str | None = None
    warnings: list[str] = Field(default_factory=list)
    artifacts: dict = Field(default_factory=dict)
    created_at: str = ""
    updated_at: str = ""
