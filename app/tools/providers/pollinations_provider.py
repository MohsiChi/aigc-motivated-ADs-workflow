"""Pollinations.AI：免费、无 Key 的 AI 图片生成（真实外部 Provider，可选）。

- generate_image：真实调用 https://image.pollinations.ai 生成 9:16 关键帧图（PNG）。
- generate_video_clip：v1 用关键帧 + 本地 ffmpeg zoompan 产出真实镜头 MP4
  （"图生视频"的本地等价物；v2 可换成真视频模型）。

免费档的生产级写法（Session 4 真实事故换来的两条纪律）：
1. 礼貌限速：两次请求之间强制最小间隔（匿名档有硬性限流，429 是常态）；
2. 指数退避重试：429/5xx 时退避重试，超限则干净失败（不把重试风暴传导给上层）。

注意：该服务在境外且限流严格，仅适合演示/低成本场景；生产请用生产档。
"""
from __future__ import annotations

import hashlib
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

from app.core.output_layout import task_dirs
from app.tools.providers.base import GenerationProvider


class PollinationsProvider(GenerationProvider):
    name = "pollinations"

    _MIN_INTERVAL_S = 4.0    # 礼貌限速：全局两次请求最小间隔
    _MAX_ATTEMPTS = 3        # 单请求最多尝试次数
    _last_request_ts = 0.0   # 类级共享，防止多镜头并发时打爆限流

    def __init__(self, output_root: Path, timeout_s: int = 180):
        self._root = Path(output_root)
        self._timeout = timeout_s

    def _polite_get(self, url: str) -> bytes:
        """带限速与退避的 GET：429/5xx 指数退避重试，超限抛干净错误。"""
        last_err: Exception | None = None
        for attempt in range(1, self._MAX_ATTEMPTS + 1):
            wait = self._MIN_INTERVAL_S - (time.time() - PollinationsProvider._last_request_ts)
            if wait > 0:
                time.sleep(wait)
            PollinationsProvider._last_request_ts = time.time()
            try:
                req = urllib.request.Request(url, headers={"User-Agent": "aigc-agent-workflow/0.1"})
                with urllib.request.urlopen(req, timeout=self._timeout) as resp:
                    return resp.read()
            except urllib.error.HTTPError as e:
                last_err = e
                if e.code in (429, 500, 502, 503) and attempt < self._MAX_ATTEMPTS:
                    time.sleep(8.0 * attempt)  # 8s, 16s 退避
                    continue
                raise RuntimeError(f"pollinations HTTP {e.code}（{attempt} 次尝试后放弃）") from e
            except OSError as e:
                # URLError / socket.timeout / ConnectionResetError / RemoteDisconnected：
                # 读阶段的超时与断连同样必须走重试，不能逃逸（审查实锤的缺口）
                last_err = e
                if attempt < self._MAX_ATTEMPTS:
                    time.sleep(5.0)
                    continue
                raise RuntimeError(f"pollinations 网络错误: {e}") from e
        raise RuntimeError(f"pollinations 重试耗尽: {last_err}")

    def generate_image(self, *, task_id: str, scene_id: int, prompt: str,
                       aspect_ratio: str = "9:16") -> dict:
        width, height = (720, 1280) if aspect_ratio == "9:16" else (1280, 720)
        seed = int(hashlib.md5(f"{task_id}-{scene_id}-{prompt[:40]}".encode()).hexdigest()[:8], 16)
        url = (
            "https://image.pollinations.ai/prompt/"
            + urllib.parse.quote(prompt[:400])
            + f"?width={width}&height={height}&nologo=true&seed={seed}"
        )
        data = self._polite_get(url)
        if len(data) < 1024:
            raise RuntimeError(f"pollinations 返回内容异常（{len(data)} 字节）")
        dirs = task_dirs(self._root / task_id)
        path = dirs["keyframes"] / f"scene_{scene_id:02d}_keyframe.png"
        path.write_bytes(data)
        return {"path": str(path), "aspect_ratio": aspect_ratio,
                "provider": self.name, "metadata": {"bytes": len(data), "seed": seed}}

    def generate_video_clip(self, *, task_id: str, scene_id: int, prompt: str,
                            duration_s: float, keyframe_path: str | None = None) -> dict:
        if not keyframe_path or not Path(keyframe_path).exists():
            raise RuntimeError("图生视频需要先有关键帧（keyframe_path 缺失）")
        from app.tools.render.video_render import render_scene_video  # 延迟导入，避免循环
        dirs = task_dirs(self._root / task_id)
        path = dirs["clips"] / f"scene_{scene_id:02d}_clip.mp4"
        render_scene_video(
            image_path=Path(keyframe_path), duration_s=duration_s, out_path=path
        )
        return {"path": str(path), "duration_s": duration_s,
                "provider": self.name, "metadata": {"first_frame_ref": keyframe_path}}
