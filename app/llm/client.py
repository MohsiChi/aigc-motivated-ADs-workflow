"""LLM 抽象层：DeepSeek 真实客户端 + JSON 解析工具。

- MOCK_LLM=1（默认）：各 Agent 走各自的确定性本地响应，不产生任何 API 费用。
- MOCK_LLM=0：真实调用 DeepSeek（需要 .env 里的 DEEPSEEK_API_KEY）。
"""
from __future__ import annotations

import json
import re

from openai import OpenAI


class LLMError(RuntimeError):
    """LLM 调用或输出解析失败。"""


def extract_json(text: str) -> dict:
    """从 LLM 输出中提取第一个 JSON 对象。

    容忍 markdown ```json 围栏与前后多余的解释文字；花括号配对扫描，
    解析失败抛 LLMError（由上层 Agent 转成步骤失败）。
    """
    if not text:
        raise LLMError("LLM 输出为空")
    fence = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL)
    if fence:
        candidate = fence.group(1)
    else:
        start = text.find("{")
        if start == -1:
            raise LLMError(f"LLM 输出中未找到 JSON: {text[:200]!r}")
        depth = 0
        candidate = None
        for i in range(start, len(text)):
            if text[i] == "{":
                depth += 1
            elif text[i] == "}":
                depth -= 1
                if depth == 0:
                    candidate = text[start : i + 1]
                    break
        if candidate is None:
            raise LLMError("LLM 输出 JSON 花括号不配对")
    try:
        return json.loads(candidate)
    except json.JSONDecodeError as e:
        raise LLMError(f"JSON 解析失败: {e}") from e


class DeepSeekClient:
    """DeepSeek（OpenAI 兼容接口）客户端，只做两件事：普通补全 + Tool Calling。"""

    def __init__(self, api_key: str | None, base_url: str, model: str):
        if not api_key:
            raise LLMError("缺少 DEEPSEEK_API_KEY（放在 .env 里，不要写进代码）")
        self._client = OpenAI(api_key=api_key, base_url=base_url)
        self._model = model

    def chat(self, *, messages: list[dict], tools: list[dict] | None = None,
             temperature: float = 0.7, max_tokens: int = 2000):
        """单次补全；带 tools 时返回的 message 可能含 tool_calls（点菜单）。"""
        kwargs: dict = {
            "model": self._model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
        }
        if tools:
            kwargs["tools"] = tools
        resp = self._client.chat.completions.create(**kwargs)
        return resp.choices[0].message

    def chat_json_robust(self, *, system: str, user: str,
                         temperature: float = 0.7, max_tokens: int = 2000) -> dict:
        """chat_json + 一次自动修复重试：LLM 偶尔输出非法 JSON（真实抓到过），
        带着错误信息再要一次，而不是直接让任务失败。"""
        try:
            return self.chat_json(system=system, user=user,
                                  temperature=temperature, max_tokens=max_tokens)
        except LLMError as e:
            retry_user = (
                user
                + f"\n\n!! 你上一次的输出不是合法 JSON（错误：{e}）。"
                "请严格重新输出：只有一个合法 JSON 对象，不要解释文字，不要 markdown 代码围栏。"
            )
            return self.chat_json(system=system, user=retry_user,
                                  temperature=temperature, max_tokens=max_tokens)

    def chat_json(self, *, system: str, user: str,
                  temperature: float = 0.7, max_tokens: int = 2000) -> dict:
        """要求 LLM 输出结构化 JSON 产物，返回解析后的 dict。"""
        message = self.chat(
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            temperature=temperature,
            max_tokens=max_tokens,
        )
        return extract_json(message.content)
