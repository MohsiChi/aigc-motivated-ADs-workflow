"""Compose Agent（剪辑）：把验收通过的素材合成真实可播放的 MP4。

零成本合成路线（v1）：
1. 每个镜头：关键帧（真实图片 or PIL 渲染占位图）+ 旁白字幕叠加 -> zoompan 推近 -> 镜头片段
2. 全部镜头：concat 拼接 -> final.mp4

若素材 Provider 已产出真实镜头 MP4（如 pollinations/即梦路线），
本 Agent 自动切换为"纯拼接"模式——这就是组件化的好处。
"""
from __future__ import annotations

from pathlib import Path

from app.agents.base import AgentContext, AgentError, BaseAgent
from app.core.models import Clip, Storyboard
from app.core.output_layout import task_dirs
from app.tools.render.local_render import overlay_subtitle, read_sidecar_prompt, render_keyframe_card
from app.tools.render.video_render import concat_videos_with_transitions, render_scene_video

_IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".webp", ".bmp"}


class ComposeAgent(BaseAgent):
    name = "compose"

    def _execute(self, ctx: AgentContext) -> dict:
        storyboard = Storyboard.model_validate(ctx.artifacts["storyboard"])
        clips = [Clip.model_validate(c) for c in ctx.artifacts["generation"]["clips"]]
        clip_by_id = {c.scene_id: c for c in clips}

        dirs = task_dirs(ctx.settings.output_dir / ctx.task_id)

        scene_videos: list[Path] = []
        durations: list[float] = []
        for scene in storyboard.scenes:
            clip = clip_by_id.get(scene.scene_id)
            if clip is None:
                raise AgentError(f"镜头 {scene.scene_id} 没有素材，无法剪辑")

            # 模式一：Provider 已给真实镜头 MP4 -> 直接用
            real_clip = Path(clip.clip_path)
            if real_clip.suffix == ".mp4" and real_clip.exists():
                scene_videos.append(real_clip)
                durations.append(clip.duration_s or scene.duration_s)
                continue

            # 模式二：从关键帧合成（真实图片直接用；sidecar 则 PIL 渲染占位图）
            kf = Path(clip.keyframe_path)
            if kf.exists() and kf.suffix.lower() in _IMAGE_SUFFIXES:
                base_img = kf
            else:
                prompt = read_sidecar_prompt(kf) or scene.visual
                base_img = render_keyframe_card(
                    scene_id=scene.scene_id, title=scene.title, prompt=prompt,
                    out_path=dirs["keyframes"] / f"scene_{scene.scene_id:02d}_card.png",
                )
            framed = overlay_subtitle(
                base_img, scene.narration,
                dirs["keyframes"] / f"scene_{scene.scene_id:02d}_framed.png",
            )
            scene_videos.append(render_scene_video(
                image_path=framed, duration_s=scene.duration_s,
                out_path=dirs["clips"] / f"scene_{scene.scene_id:02d}.mp4",
            ))
            durations.append(scene.duration_s)

        final_path = concat_videos_with_transitions(
            scene_videos, dirs["final"] / "final.mp4", durations
        )
        total = sum(s.duration_s for s in storyboard.scenes)
        self.log.info("[%s] 成片已生成: %s (约 %.1fs)", ctx.task_id, final_path, total)
        return {
            "final_video_path": str(final_path),
            "scene_videos": [str(p) for p in scene_videos],
            "scene_count": len(scene_videos),
            "approx_duration_s": round(total, 1),
            "transition": "xfade-fade",
        }
