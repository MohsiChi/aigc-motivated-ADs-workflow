"""Review Agent（质检）：质量门禁。

v1 为规则引擎：确定性、可测试、零成本，规则蒸馏自蛋壳黑陶大赛的质检经验：
- R1 提示词非空且长度达标（防空提示词抽奖）
- R2 提示词覆盖商品关键词（防跑题）
- R3 素材文件真实存在（产物落盘校验）
- R4 时长在 [2, 15] 秒（平台规格）
- R5 视频提示词不得照抄旁白（防偷懒）
- R6 广告法极限词检查（合规门禁）

每条命中同时产出两种形态：人读的 reasons（字符串）与机器读的 findings
（Finding：rule_id / field / owner / severity）。owner 决定返工路由，severity 决定
交付策略，两轴正交——门禁与路由见 app/core/repair_policy.py 与 workflow 的交付门禁。

v2 扩展：规则层保留为第一道门禁，叠加 LLM 语义复核 / 接入敏感词服务。
"""
from __future__ import annotations

import re
from pathlib import Path

from app.agents.base import AgentContext, BaseAgent
from app.core.models import Clip, Finding, PromptPack, ReviewReport, SceneVerdict, Storyboard

BANNED_WORDS = ["第一", "顶级", "国家级", "绝对", "全网首发", "史上"]
# "最"单独做正则：排除 最后/最新/最近/最多/最少/最终/最初 等合法词（审查发现的误伤），
# 同时仍拦截 最好/最优/最低价 这类真极限词
_MOST_PATTERN = re.compile(r"最(?![后新近多少终初])")
_PARTICLES = set("的了是在有个给为和与就做都也还很更这那对把被从到")

# 规则 -> (归属步骤, 严重级)。这是"返工路由 + 交付策略"的唯一真相来源。
# field 维度只对 R6 有意义：旁白归 script（编剧产物），提示词归 prompt。
# 未登记的 (rule_id, field) 回落到 (rule_id, "*")；rule_id 本身未登记则 KeyError——
# 刻意让它炸，符合本项目"非法状态在第一现场暴露"的一贯做法。
_RULE_META: dict[tuple[str, str], tuple[str, str]] = {
    ("R1", "*"): ("prompt", "MAJOR"),             # 提示词过短/缺失 -> 重生成提示词
    ("R2", "*"): ("prompt", "MAJOR"),             # 未覆盖商品关键词 -> 重生成提示词
    ("R3", "*"): ("generation", "MAJOR"),         # 素材文件缺失 -> 重跑生成（现状已正确）
    ("R4", "*"): ("generation", "MINOR"),         # 时长越界 -> 上游已钳制，纯兜底
    ("R5", "*"): ("prompt", "MAJOR"),             # 照抄旁白 -> 重生成提示词
    ("R6", "narration"): ("script", "BLOCKER"),   # 旁白极限词 -> 合规，不得降级交付
    ("R6", "*"): ("prompt", "BLOCKER"),           # 提示词极限词 -> 合规，不得降级交付
}


def make_finding(rule_id: str, field: str, message: str) -> Finding:
    """把一次规则命中翻译成结构化事实（owner + severity 由 _RULE_META 决定）。"""
    owner, severity = _RULE_META.get((rule_id, field)) or _RULE_META[(rule_id, "*")]
    return Finding(rule_id=rule_id, field=field, message=message,
                   owner=owner, severity=severity)


def extract_keywords(text: str, top_k: int = 6) -> list[str]:
    """极简中文关键词抽取（v1：去停用词切段，刻意朴素；v2 可换 jieba/embedding）。"""
    cleaned = re.sub(r"[^\w\u4e00-\u9fff]+", " ", text or "")
    chunks: list[str] = []
    cur = ""
    for ch in cleaned:
        if ch in _PARTICLES or ch == " ":
            if cur:
                chunks.append(cur)
                cur = ""
        else:
            cur += ch
    if cur:
        chunks.append(cur)
    seen: list[str] = []
    for c in chunks:
        if len(c) >= 2 and c not in seen:
            seen.append(c)
    return seen[:top_k]


class ReviewAgent(BaseAgent):
    name = "review"
    rules_version = "v1"

    def _execute(self, ctx: AgentContext) -> dict:
        storyboard = Storyboard.model_validate(ctx.artifacts["storyboard"])
        prompts = PromptPack.model_validate(ctx.artifacts["prompts"])
        clips = [Clip.model_validate(c) for c in ctx.artifacts["generation"]["clips"]]

        prompt_by_id = {p.scene_id: p for p in prompts.prompts}
        clip_by_id = {c.scene_id: c for c in clips}
        keywords = extract_keywords(f"{ctx.topic} {ctx.product_info}")

        verdicts: list[SceneVerdict] = []
        for scene in storyboard.scenes:
            reasons: list[str] = []
            findings: list[Finding] = []

            def add(rule_id: str, field: str, message: str) -> None:
                """单一真相来源：一次命中同时产出人读 reason 与机器读 finding。"""
                reasons.append(message)
                findings.append(make_finding(rule_id, field, message))

            p = prompt_by_id.get(scene.scene_id)
            c = clip_by_id.get(scene.scene_id)

            # R1 提示词非空且长度达标
            if p is None:
                add("R1", "prompts", "R1 缺少该镜头的提示词")
            else:
                if len(p.image_prompt.strip()) < 8:
                    add("R1", "image_prompt", "R1 image_prompt 过短（<8 字）")
                if len(p.video_prompt.strip()) < 8:
                    add("R1", "video_prompt", "R1 video_prompt 过短（<8 字）")

            # R2 提示词覆盖商品关键词
            if p is not None and keywords:
                joined = p.image_prompt + p.video_prompt
                if not any(k in joined for k in keywords):
                    add("R2", "image_prompt", f"R2 提示词未覆盖任何商品关键词 {keywords}")

            # R3 素材文件真实存在
            if c is None:
                add("R3", "clips", "R3 缺少该镜头的素材记录")
            else:
                for field, label, path in (("keyframe_path", "关键帧", c.keyframe_path),
                                           ("clip_path", "视频片段", c.clip_path)):
                    if not path or not Path(path).exists():
                        add("R3", field, f"R3 {label}文件不存在: {path!r}")

            # R4 时长规格
            if c is not None and not (2 <= c.duration_s <= 15):
                add("R4", "duration_s", f"R4 时长 {c.duration_s}s 超出 [2,15]s 规格")

            # R5 视频提示词不得照抄旁白
            if p is not None and p.video_prompt.strip() == scene.narration.strip():
                add("R5", "video_prompt", "R5 video_prompt 照抄旁白，缺少运镜/动作描述")

            # R6 广告法极限词
            for field, field_name, text in (
                ("narration", "旁白", scene.narration),
                ("image_prompt", "image_prompt", p.image_prompt if p else ""),
                ("video_prompt", "video_prompt", p.video_prompt if p else ""),
            ):
                hits = [w for w in BANNED_WORDS if w in text]
                if _MOST_PATTERN.search(text):
                    hits.append("最*")
                if hits:
                    add("R6", field, f"R6 {field_name}含极限词: {hits}")

            verdicts.append(SceneVerdict(scene_id=scene.scene_id,
                                         passed=not reasons, reasons=reasons,
                                         findings=findings))

        report = ReviewReport(
            passed_all=all(v.passed for v in verdicts),
            verdicts=verdicts,
            rules_version=self.rules_version,
        )
        self.log.info("[%s] 质检结果: %d/%d 通过", ctx.task_id,
                      sum(1 for v in verdicts if v.passed), len(verdicts))
        return report.model_dump()
