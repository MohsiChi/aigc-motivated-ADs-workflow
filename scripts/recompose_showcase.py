"""从已有任务的关键帧重剪成片（不重新花钱生成素材）。

用法：.venv/Scripts/python.exe scripts/recompose_showcase.py <task_id> <输出mp4路径>
用途：想换转场/字幕效果时，用既有素材重新剪辑导出，零生成成本。
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.config import get_settings  # noqa: E402
from app.core.models import Storyboard  # noqa: E402
from app.core.task_store import TaskStore  # noqa: E402
from app.tools.render.local_render import overlay_subtitle  # noqa: E402
from app.tools.render.video_render import (  # noqa: E402
    concat_videos_with_transitions,
    render_scene_video,
)

_IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".webp"}


def main() -> int:
    if len(sys.argv) != 3:
        print(__doc__)
        return 1
    task_id, out_path = sys.argv[1], Path(sys.argv[2])
    settings = get_settings()
    store = TaskStore(settings.db_path)
    rec = store.get_task(task_id)
    if rec is None:
        print(f"task not found: {task_id}")
        return 2
    storyboard = Storyboard.model_validate(rec.artifacts["storyboard"])
    clips = {c["scene_id"]: c for c in rec.artifacts["generation"]["clips"]}

    work = settings.output_dir / task_id / "_recompose"
    work.mkdir(parents=True, exist_ok=True)

    videos: list[Path] = []
    durations: list[float] = []
    for scene in storyboard.scenes:
        clip = clips.get(scene.scene_id)
        kf = Path(clip["keyframe_path"]) if clip else Path("")
        if not kf.exists() or kf.suffix.lower() not in _IMAGE_SUFFIXES:
            print(f"scene {scene.scene_id}: keyframe missing or not an image: {kf}")
            return 3
        framed = overlay_subtitle(
            kf, scene.narration, work / f"scene_{scene.scene_id:02d}_framed.png"
        )
        videos.append(render_scene_video(
            image_path=framed, duration_s=scene.duration_s,
            out_path=work / f"scene_{scene.scene_id:02d}.mp4",
        ))
        durations.append(scene.duration_s)

    final = concat_videos_with_transitions(videos, out_path, durations)
    print(f"done: {final}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
