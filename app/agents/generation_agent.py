"""Generation Agent：用 Tool Calling 驱动素材生成工具表。

这是全项目最直观体现 "Tool Calling" 的组件：
1. 工具表（JSON Schema）告诉 LLM 有哪些工具、参数长什么样；
2. LLM 决定调用哪些工具、传什么参数（先关键帧图，再图生视频，并把
   关键帧路径传给后者——复刻蛋壳黑陶的首尾帧衔接经验）；
3. 程序真正执行（registry.dispatch -> Provider），结果以 role=tool 喂回；
4. 直到 LLM 不再点菜为止。

Mock 模式用一份确定性的"点菜单"模拟 LLM 决策，但走完全相同的
dispatch 路径——机制不因省钱而改变，这正是 Tool Calling 的要点：LLM 只做决策，真正执行的永远是代码。
"""
from __future__ import annotations

import json
import math

from app.agents.base import AgentContext, AgentError, BaseAgent
from app.core.models import Clip, PromptPack, Storyboard
from app.llm.client import DeepSeekClient
from app.tools.providers.base import GenerationProvider
from app.tools.registry import build_default_registry

_TOOL_SYSTEM_PROMPT = """你是素材生成执行者。你手上有两个工具：
1. generate_keyframe_image(scene_id, prompt)：先生成镜头关键帧；
2. generate_video_clip(scene_id, prompt, duration_s, keyframe_path)：再用关键帧图生视频。
请为任务单里的每个镜头依次完成"先图后视频"，并把关键帧工具返回的 path 作为
下一个镜头视频生成的 keyframe_path 之外的本镜 keyframe_path 传入。
全部完成后，输出一句总结即可。"""


class GenerationAgent(BaseAgent):
    name = "generation"

    def __init__(self, provider: GenerationProvider, llm: DeepSeekClient | None = None):
        super().__init__()
        self._provider = provider
        self._llm = llm

    def _execute(self, ctx: AgentContext) -> dict:
        storyboard = Storyboard.model_validate(ctx.artifacts["storyboard"])
        prompts = PromptPack.model_validate(ctx.artifacts["prompts"])
        prompt_by_id = {p.scene_id: p for p in prompts.prompts}
        # 英文图生提示词表：Flux 等英文训练模型对中文提示词不敏感，
        # 调图生工具时确定性切换为英文版（不依赖 LLM 自己选语言）
        en_by_id = {
            p.scene_id: p.image_prompt_en
            for p in prompts.prompts if p.image_prompt_en.strip()
        }

        only: list[int] | None = ctx.extra.get("scene_ids")  # 返工模式：只重做这些镜头
        attempt: int = int(ctx.extra.get("attempt", 1))
        scenes = [s for s in storyboard.scenes if only is None or s.scene_id in only]
        if not scenes:
            raise AgentError("没有可生成的镜头")

        registry = build_default_registry(self._provider, ctx.task_id)
        tool_results: dict[tuple[str, int], dict] = {}

        if ctx.settings.mock_llm or self._llm is None:
            self._mock_tool_loop(registry, scenes, prompt_by_id, tool_results, en_by_id)
        else:
            self._llm_tool_loop(ctx, registry, scenes, prompt_by_id, tool_results, en_by_id=en_by_id)
            # 完备性兜底：自由 Tool Calling 下 LLM 可能漏镜头（真实运行抓到过），
            # 逐镜检查，缺口自动补做一轮（与质检返工同一哲学）
            missing = [
                s.scene_id for s in scenes
                if ("generate_keyframe_image", s.scene_id) not in tool_results
                or ("generate_video_clip", s.scene_id) not in tool_results
            ]
            if missing:
                self.log.warning("[%s] LLM 漏做镜头 %s，启动补做轮", ctx.task_id, missing)
                redo = [s for s in scenes if s.scene_id in missing]
                self._llm_tool_loop(
                    ctx, registry, redo, prompt_by_id, tool_results,
                    en_by_id=en_by_id,
                    extra_note=(
                        f"!! 补做轮：上一轮结束后以下镜头仍未完成，请只补做这些镜头：{missing}。"
                        "每个镜头必须先 generate_keyframe_image 再 generate_video_clip。"
                    ),
                )

        new_clips = self._assemble_clips(scenes, prompt_by_id, tool_results, attempt)
        if not new_clips:
            raise AgentError("工具执行完毕但没有产出任何素材")

        # 返工时保留其他镜头的既有素材，只覆盖重做的镜头
        prev = ctx.artifacts.get("generation", {}).get("clips", [])
        redone = {c.scene_id for c in new_clips}
        merged = [Clip.model_validate(c) for c in prev if c["scene_id"] not in redone]
        merged.extend(new_clips)
        merged.sort(key=lambda c: c.scene_id)
        return {"clips": [c.model_dump() for c in merged], "attempt": attempt}

    # ---------------- Mock：确定性"点菜单" ----------------

    @staticmethod
    def _mock_tool_loop(registry, scenes, prompt_by_id, tool_results, en_by_id=None) -> None:
        en_by_id = en_by_id or {}
        keyframe_paths: dict[int, str] = {}
        for scene in scenes:  # 第一轮：所有关键帧
            p = prompt_by_id[scene.scene_id]
            out = json.loads(registry.dispatch(
                "generate_keyframe_image",
                {"scene_id": scene.scene_id,
                 "prompt": en_by_id.get(scene.scene_id) or p.image_prompt},
            ))
            if "error" in out:
                raise AgentError(f"关键帧生成失败: {out['error']}")
            tool_results[("generate_keyframe_image", scene.scene_id)] = out
            keyframe_paths[scene.scene_id] = out["path"]
        for scene in scenes:  # 第二轮：图生视频（携带关键帧路径，首帧衔接）
            p = prompt_by_id[scene.scene_id]
            out = json.loads(registry.dispatch(
                "generate_video_clip",
                {
                    "scene_id": scene.scene_id,
                    "prompt": p.video_prompt,
                    "duration_s": scene.duration_s,
                    "keyframe_path": keyframe_paths[scene.scene_id],
                },
            ))
            if "error" in out:
                raise AgentError(f"视频生成失败: {out['error']}")
            tool_results[("generate_video_clip", scene.scene_id)] = out

    # ---------------- 真实：LLM 决策的 Tool Calling 循环 ----------------

    def _llm_tool_loop(self, ctx, registry, scenes, prompt_by_id, tool_results,
                       max_rounds: int | None = None, extra_note: str = "", en_by_id=None) -> None:
        en_by_id = en_by_id or {}
        scene_by_id = {s.scene_id: s for s in scenes}
        if max_rounds is None:
            max_rounds = max(12, 2 * len(scenes) + 4)
        task_lines = []
        for s in scenes:
            p = prompt_by_id[s.scene_id]
            task_lines.append(
                f"- 镜头 {s.scene_id}：image_prompt={p.image_prompt!r}, "
                f"video_prompt={p.video_prompt!r}, duration_s={s.duration_s}"
            )
        # 返工闭环：把质检原因喂回给 LLM，否则它会重复犯同样的错（真实冒烟验证过）
        feedback: dict = ctx.extra.get("review_feedback") or {}
        if feedback:
            task_lines.append("")
            task_lines.append(
                f"!! 这是第 {int(ctx.extra.get('attempt', 2)) - 1} 次返工。"
                "上一轮这些镜头质检不通过，本轮必须逐条规避："
            )
            for sid, reasons in feedback.items():
                task_lines.append(f"- 镜头 {sid}：{'；'.join(reasons)}")
        messages = [
            {"role": "system", "content": _TOOL_SYSTEM_PROMPT},
            {"role": "user",
             "content": "任务单：\n" + "\n".join(task_lines) + (("\n\n" + extra_note) if extra_note else "")},
        ]
        for _ in range(max_rounds):
            message = self._llm.chat(messages=messages, tools=registry.openai_schema())
            if not message.tool_calls:
                return
            messages.append(message)
            for tool_call in message.tool_calls:
                name = tool_call.function.name
                parsed = self._execute_tool_call(
                    registry, name, tool_call.function.arguments, scene_by_id,
                    tool_results, en_by_id,
                )
                if "error" in parsed:
                    self.log.warning("[%s] 工具执行失败 %s: %s",
                                     ctx.task_id, name, parsed["error"])
                messages.append({
                    "role": "tool",
                    "tool_call_id": tool_call.id,
                    "content": json.dumps(parsed, ensure_ascii=False),
                })
        raise AgentError(f"Tool Calling 轮数超过 {max_rounds}，可能存在死循环")

    @staticmethod
    def _execute_tool_call(registry, name, arguments, scene_by_id, tool_results, en_by_id):
        """只执行当前镜头的有效调用；错误反馈给 LLM，成功产物在本次生成内复用。"""
        try:
            if name not in {"generate_keyframe_image", "generate_video_clip"}:
                raise ValueError(f"未知工具: {name}")
            args = json.loads(arguments)
            if not isinstance(args, dict):
                raise ValueError("工具参数必须是 JSON 对象")
            sid = args.get("scene_id")
            if type(sid) is not int or sid not in scene_by_id:
                raise ValueError("scene_id 必须是本轮任务单中的整数镜头编号")
            key = (name, sid)
            if key in tool_results:
                return tool_results[key]
            if not isinstance(args.get("prompt"), str) or not args["prompt"].strip():
                raise ValueError("prompt 必须是非空字符串")
            if name == "generate_keyframe_image":
                if sid in en_by_id:
                    args["prompt"] = en_by_id[sid] + ", commercial advertising photography, high detail"
            else:
                duration = args.get("duration_s", scene_by_id[sid].duration_s)
                if (type(duration) not in (int, float) or not 2 <= duration <= 15
                        or not math.isfinite(duration)):
                    raise ValueError("duration_s 必须是 2~15 之间的有限数值")
                args["duration_s"] = duration
                image = tool_results.get(("generate_keyframe_image", sid))
                if image is None:
                    raise ValueError("必须先成功生成本镜头关键帧")
                # 无论 LLM 是否提供路径，都绑定本镜头已成功生成的关键帧。
                args["keyframe_path"] = image["path"]
            parsed = json.loads(registry.dispatch(name, args))
            if not isinstance(parsed, dict):
                raise ValueError("工具结果必须是 JSON 对象")
            if "error" in parsed:
                return parsed
            if not isinstance(parsed.get("path"), str) or not parsed["path"].strip():
                raise ValueError("工具结果缺少非空 path")
            tool_results[key] = parsed
            return parsed
        except (ValueError, TypeError) as exc:
            return {"error": str(exc)}

    # ---------------- 组装素材清单 ----------------

    @staticmethod
    def _assemble_clips(scenes, prompt_by_id, tool_results, attempt) -> list[Clip]:
        clips: list[Clip] = []
        for scene in scenes:
            sid = scene.scene_id
            kf = tool_results.get(("generate_keyframe_image", sid))
            vc = tool_results.get(("generate_video_clip", sid))
            if not kf or not vc:
                raise AgentError(f"镜头 {sid} 缺少生成结果")
            clips.append(Clip(
                scene_id=sid,
                attempt=attempt,
                provider=vc.get("provider", "unknown"),
                keyframe_path=kf["path"],
                clip_path=vc["path"],
                duration_s=float(vc.get("duration_s", scene.duration_s)),
                metadata={"keyframe": kf.get("metadata", {}), "clip": vc.get("metadata", {}),
                          "first_frame_desc": prompt_by_id[sid].first_frame_desc},
            ))
        return clips
