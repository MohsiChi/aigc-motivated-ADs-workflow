"""ffmpeg 渲染助手：用 imageio-ffmpeg 自带的 ffmpeg.exe，不需要系统安装。

这是"零成本出真视频"的关键组件：关键帧图片 -> zoompan 运镜 -> 镜头片段，
镜头片段 -> concat -> 成片（MoneyPrinterTurbo 的同类做法）。
"""
from __future__ import annotations

import subprocess
import tempfile
from pathlib import Path

from imageio_ffmpeg import get_ffmpeg_exe


# ffmpeg 子进程超时护栏：挂死时干净失败，不永久占用 Worker（审查发现的缺口）
_RENDER_TIMEOUT_S = 300
_CONCAT_TIMEOUT_S = 600

def render_scene_video(*, image_path: Path, duration_s: float, out_path: Path,
                       fps: int = 25, size: tuple[int, int] = (720, 1280)) -> Path:
    """单张图片 -> 带缓慢推近（Ken Burns）的镜头片段。"""
    w, h = size
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    vf = (
        f"scale={w}:{h}:force_original_aspect_ratio=increase,crop={w}:{h},"
        f"zoompan=z='min(zoom+0.0012,1.15)':x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)'"
        f":d=1:s={w}x{h}:fps={fps}"
    )
    cmd = [
        get_ffmpeg_exe(), "-y",
        "-loop", "1", "-framerate", str(fps), "-t", f"{max(duration_s, 1.0):.2f}",
        "-i", str(image_path),
        "-vf", vf,
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "23", "-pix_fmt", "yuv420p",
        str(out_path),
    ]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8",
                              errors="replace", timeout=_RENDER_TIMEOUT_S)
    except subprocess.TimeoutExpired as e:
        raise RuntimeError(f"ffmpeg 渲染镜头超时（>{_RENDER_TIMEOUT_S}s），已终止") from e
    if proc.returncode != 0 or not out_path.exists():
        raise RuntimeError(f"ffmpeg 渲染镜头失败: {proc.stderr[-500:]}")
    return out_path


def concat_videos(video_paths: list[Path], out_path: Path) -> Path:
    """拼接镜头成片（统一重编码，避免参数不一致的花屏）。"""
    return _plain_concat(video_paths, out_path)


def concat_videos_with_transitions(video_paths: list[Path], out_path: Path,
                                   durations: list[float], fade_s: float = 0.5) -> Path:
    """带淡入淡出转场的拼接（避免"PPT 式硬切"）。

    条件不满足（单镜头 / 时长未知 / 转场空间不够）时自动退回普通拼接。
    """
    if len(video_paths) >= 2 and durations and len(durations) == len(video_paths):
        fade = min(fade_s, min(durations) / 2.0)
        if fade >= 0.15:
            return _xfade_concat(video_paths, out_path, durations, fade)
    return _plain_concat(video_paths, out_path)


def _xfade_concat(video_paths: list[Path], out_path: Path,
                  durations: list[float], fade: float) -> Path:
    """xfade 转场链：offset = 已累计时长 - fade。输入先归一化分辨率/帧率。"""
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    inputs: list[str] = []
    for p in video_paths:
        inputs += ["-i", str(p)]
    norm = "scale=720:1280:force_original_aspect_ratio=increase,crop=720:1280,fps=25,format=yuv420p"
    filters = [f"[{i}:v]{norm}[v{i}]" for i in range(len(video_paths))]
    prev_label = "v0"
    cumulative = durations[0]
    n = len(video_paths)
    for k in range(1, n):
        offset = max(cumulative - fade, 0.0)
        out_label = "vout" if k == n - 1 else f"x{k}"
        filters.append(
            f"[{prev_label}][v{k}]xfade=transition=fade:duration={fade:.2f}:offset={offset:.2f}[{out_label}]"
        )
        cumulative = cumulative + durations[k] - fade
        prev_label = out_label
    cmd = [
        get_ffmpeg_exe(), "-y", *inputs,
        "-filter_complex", ";".join(filters),
        "-map", "[vout]",
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "22", "-pix_fmt", "yuv420p",
        str(out_path),
    ]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8",
                              errors="replace", timeout=_CONCAT_TIMEOUT_S)
    except subprocess.TimeoutExpired as e:
        raise RuntimeError(f"ffmpeg 转场拼接超时（>{_CONCAT_TIMEOUT_S}s），已终止") from e
    if proc.returncode != 0 or not out_path.exists():
        # 转场失败不阻塞出片：退回普通拼接
        return _plain_concat(video_paths, out_path)
    return out_path


def _plain_concat(video_paths: list[Path], out_path: Path) -> Path:
    """concat demuxer 直连（统一重编码）。"""
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False, encoding="utf-8") as f:
        for p in video_paths:
            f.write(f"file '{Path(p).as_posix()}'\n")
        listfile = f.name
    try:
        cmd = [
            get_ffmpeg_exe(), "-y", "-f", "concat", "-safe", "0", "-i", listfile,
            "-c:v", "libx264", "-preset", "veryfast", "-crf", "22", "-pix_fmt", "yuv420p",
            str(out_path),
        ]
        try:
            proc = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8",
                                  errors="replace", timeout=_CONCAT_TIMEOUT_S)
        except subprocess.TimeoutExpired as e:
            raise RuntimeError(f"ffmpeg 拼接超时（>{_CONCAT_TIMEOUT_S}s），已终止") from e
        if proc.returncode != 0 or not out_path.exists():
            raise RuntimeError(f"ffmpeg 拼接失败: {proc.stderr[-500:]}")
        return out_path
    finally:
        Path(listfile).unlink(missing_ok=True)  # 不留临时文件
