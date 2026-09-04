"""任务输出目录规范：一个任务一个目录，固定子目录布局。

outputs/<task_id>/
├── 00_task.json          任务元信息（主题/状态/告警/产物索引）
├── 01_script/            分镜脚本（storyboard.json + 分镜脚本.md 人读版）
├── 02_prompts/           逐镜生成提示词（prompts.json + 提示词.md 人读版）
├── 03_keyframes/         每镜关键帧（真实图片或 PIL 渲染占位图）
├── 04_clips/             每镜素材片段（真实视频或本地合成片段）
├── 05_review/            质检报告（review_report.json）
└── 06_final/             成片（final.mp4）

所有 Provider / ComposeAgent / Exporter 共用这一份布局定义，
改布局只动这一个文件。
"""
from __future__ import annotations

from pathlib import Path

SUBDIRS = ("01_script", "02_prompts", "03_keyframes", "04_clips", "05_review", "06_final")


def task_dirs(task_dir: Path) -> dict[str, Path]:
    """创建并返回任务的规范子目录。"""
    root = Path(task_dir)
    dirs = {"root": root}
    for name in SUBDIRS:
        d = root / name
        d.mkdir(parents=True, exist_ok=True)
        dirs[name.split("_", 1)[1]] = d  # script/prompts/keyframes/clips/review/final
    return dirs
