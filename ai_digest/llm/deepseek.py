"""DeepSeek 客户端封装。

说明：
- DeepSeek 提供 OpenAI 兼容 API，故复用 openai SDK。
- 本模块是"模型抽象层"唯一入口；日后若要换 Claude/OpenAI，
  只需在 llm/ 下新增适配器并保持 chat()/chat_json() 签名一致。
"""
from __future__ import annotations

import json
import logging
import re
import time
from typing import Any, Optional

import openai

from .. import config
from ..audit import operation

logger = logging.getLogger(__name__)


class LLMError(RuntimeError):
    """模型调用失败。"""


class DeepSeekClient:
    def __init__(
        self,
        api_key: Optional[str] = None,
        base_url: Optional[str] = None,
        chat_model: Optional[str] = None,
        timeout: float = 90.0,
        max_retries: int = 3,
        thinking_mode: Optional[str] = None,
    ) -> None:
        if not api_key:
            api_key = config.DEEPSEEK_API_KEY
        if not base_url:
            base_url = config.DEEPSEEK_BASE_URL
        if not chat_model:
            chat_model = config.DEEPSEEK_CHAT_MODEL
        if not api_key or api_key == "sk-xxxx":
            raise LLMError("未配置 DEEPSEEK_API_KEY（请复制 .env.example 为 .env 并填入）")

        self.thinking_mode = (
            config.DEEPSEEK_THINKING_MODE if thinking_mode is None else thinking_mode
        ).strip().lower()
        if self.thinking_mode not in {"enabled", "disabled"}:
            raise LLMError("DEEPSEEK_THINKING_MODE 必须为 enabled 或 disabled")
        self.chat_model = chat_model
        self.client = openai.OpenAI(api_key=api_key, base_url=base_url, timeout=timeout, max_retries=0)
        self.max_retries = max_retries

    # ---------- 基础调用 ----------
    def chat_text(self, system: str, user: str, model: Optional[str] = None,
                  temperature: float = 0.1, *, thinking_mode: Optional[str] = None) -> str:
        model = model or self.chat_model
        mode = self.thinking_mode if thinking_mode is None else thinking_mode.strip().lower()
        if mode not in {"enabled", "disabled"}:
            raise LLMError("thinking_mode 必须为 enabled 或 disabled")
        last_err: Optional[Exception] = None
        for attempt in range(1, self.max_retries + 1):
            try:
                started = time.monotonic()
                with operation("llm.request", model=model, thinking_mode=mode, attempt=attempt) as log:
                    resp = self.client.chat.completions.create(
                        model=model,
                        extra_body={"thinking": {"type": mode}},
                        **({"temperature": temperature} if mode == "disabled" else {}),
                        messages=[
                            {"role": "system", "content": system},
                            {"role": "user", "content": user},
                        ],
                    )
                    choice = resp.choices[0]
                    content = choice.message.content or ""
                    finish = getattr(choice, "finish_reason", None)
                    usage = getattr(resp, "usage", None)
                    counts = {name: getattr(usage, name, None) for name in (
                        "prompt_tokens", "completion_tokens", "total_tokens",
                        "prompt_cache_hit_tokens", "prompt_cache_miss_tokens")}
                    details = getattr(usage, "completion_tokens_details", None)
                    counts["reasoning_tokens"] = getattr(details, "reasoning_tokens", None)
                    log.update(response_model=resp.model, response_id=getattr(resp, "id", None),
                               finish_reason=finish, usage=counts, response_chars=len(content))
                    if config.LLM_LOG_RESPONSE:
                        log["response_text"] = content
                    logger.info(
                        "DeepSeek 请求模型=%s，思考模式=%s，响应模型=%s，结束原因=%s；"
                        "Token 输入=%s 输出=%s 总计=%s；耗时=%.2f秒",
                        model, mode, resp.model, finish, counts["prompt_tokens"],
                        counts["completion_tokens"], counts["total_tokens"], time.monotonic() - started,
                    )
                    if finish not in (None, "stop"):
                        raise LLMError(f"模型响应未正常完成：finish_reason={finish}")
                    if not content.strip():
                        raise LLMError("模型返回空正文")
                return content.strip()
            except Exception as exc:  # noqa: BLE001
                last_err = exc
                logger.warning("DeepSeek 调用第 %s 次失败: %s", attempt, exc)
                if attempt < self.max_retries:
                    time.sleep(2 ** attempt)
        raise LLMError(f"DeepSeek 调用多次失败: {last_err}")

    def chat_json(self, system: str, user: str, model: Optional[str] = None,
                  temperature: float = 0.0, *, thinking_mode: Optional[str] = None) -> dict[str, Any]:
        """请求模型只输出 JSON，并做健壮解析（剥离 ``` 包裹/首尾噪音）。"""
        raw = self.chat_text(system, user, model=model, temperature=temperature,
                             thinking_mode=thinking_mode)
        return self._parse_json(raw)

    @staticmethod
    def _parse_json(raw: str) -> dict[str, Any]:
        text = raw.strip()
        # 去掉可能的 ```json ... ``` 代码块
        fence = re.match(r"^```(?:json)?\s*(.*?)\s*```$", text, flags=re.S)
        if fence:
            text = fence.group(1).strip()
        try:
            obj = json.loads(text)
        except json.JSONDecodeError:
            # 退路：截取第一个 { 到最后一个 }
            start, end = text.find("{"), text.rfind("}")
            if start >= 0 and end > start:
                obj = json.loads(text[start:end + 1])
            else:
                raise LLMError(f"模型输出不是合法 JSON: {raw[:200]}")
        if not isinstance(obj, dict):
            raise LLMError(f"模型输出 JSON 非对象: {raw[:200]}")
        return obj
