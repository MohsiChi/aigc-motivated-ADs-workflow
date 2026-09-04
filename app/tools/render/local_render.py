"""零成本本地渲染：让 Mock 模式也能产出真实图片文件。

设计动机：不花任何生成 API 的钱，流水线也要能产出真实可见的素材，
否则剪辑合成没有输入。这是 v1 的"自给自足"设计。
"""
from __future__ import annotations

import json
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

_BG = [(23, 37, 84), (49, 46, 129), (6, 78, 59), (76, 29, 149), (120, 53, 15)]
_ACCENT = [(56, 189, 248), (250, 204, 21), (52, 211, 153), (244, 114, 182), (251, 146, 60)]


def _font(size: int):
    for candidate in (
        "C:/Windows/Fonts/msyhbd.ttc",
        "C:/Windows/Fonts/msyh.ttc",
        "C:/Windows/Fonts/simhei.ttf",
    ):
        try:
            return ImageFont.truetype(candidate, size)
        except OSError:
            continue
    return ImageFont.load_default()


def _wrap(draw: ImageDraw.ImageDraw, text: str, font, max_width: int) -> list[str]:
    lines: list[str] = []
    cur = ""
    for ch in text:
        if draw.textlength(cur + ch, font=font) <= max_width:
            cur += ch
        else:
            if cur:
                lines.append(cur)
            cur = ch
    if cur:
        lines.append(cur)
    return lines


def _vertical_gradient(size: tuple[int, int], top: tuple[int, int, int],
                       bottom: tuple[int, int, int]) -> Image.Image:
    w, h = size
    img = Image.new("RGB", size, top)
    px = img.load()
    for y in range(h):
        t = y / max(h - 1, 1)
        row = tuple(int(top[i] + (bottom[i] - top[i]) * t) for i in range(3))
        for x in range(w):
            px[x, y] = row
    return img


def render_keyframe_card(*, scene_id: int, title: str, prompt: str, out_path: Path,
                         width: int = 720, height: int = 1280) -> Path:
    """渲染一张占位关键帧图（深色渐变 + 标题 + 提示词节选），真实 PNG。"""
    bg = _BG[scene_id % len(_BG)]
    accent = _ACCENT[scene_id % len(_ACCENT)]
    img = _vertical_gradient((width, height), bg, tuple(max(0, c - 18) for c in bg))
    draw = ImageDraw.Draw(img)

    badge = "AIGC WORKFLOW · MOCK KEYFRAME"
    badge_font = _font(22)
    draw.rounded_rectangle(
        [40, 48, 40 + int(draw.textlength(badge, font=badge_font)) + 36, 92],
        radius=22, fill=accent,
    )
    draw.text((58, 54), badge, font=badge_font, fill=(15, 23, 42))

    title_font = _font(64)
    y = height // 3
    for line in _wrap(draw, f"镜头 {scene_id} · {title}", title_font, width - 120):
        draw.text((60, y), line, font=title_font, fill=(255, 255, 255))
        y += 84

    body_font = _font(30)
    y += 40
    for line in _wrap(draw, prompt[:120], body_font, width - 140)[:6]:
        draw.text((60, y), line, font=body_font, fill=(203, 213, 225))
        y += 48

    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    img.save(out_path, "PNG")
    return out_path


def overlay_subtitle(image_path: Path, narration: str, out_path: Path,
                     width: int = 720, height: int = 1280) -> Path:
    """把旁白作为字幕条叠加到画面底部（电商短视频标配）。"""
    img = Image.open(image_path).convert("RGB").resize((width, height))
    draw = ImageDraw.Draw(img, "RGBA")
    font = _font(38)
    lines = _wrap(draw, narration, font, width - 120)[-3:]  # 最多 3 行
    line_h = 56
    box_h = line_h * len(lines) + 48
    draw.rectangle([0, height - box_h, width, height], fill=(0, 0, 0, 160))
    y = height - box_h + 24
    for line in lines:
        tw = draw.textlength(line, font=font)
        draw.text(((width - tw) / 2, y), line, font=font, fill=(255, 255, 255))
        y += line_h
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    img.save(out_path, "PNG")
    return out_path


def read_sidecar_prompt(sidecar_path: Path) -> str:
    """从 Mock Provider 的 JSON sidecar 里读出提示词（供本地渲染使用）。"""
    try:
        return str(json.loads(Path(sidecar_path).read_text(encoding="utf-8")).get("prompt", ""))
    except (OSError, json.JSONDecodeError):
        return ""
