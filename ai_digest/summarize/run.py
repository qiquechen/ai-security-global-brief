"""摘要执行。"""
from __future__ import annotations

import logging
from typing import Any

from .. import config
from ..llm.deepseek import DeepSeekClient
from .prompts import SYSTEM_SUMMARY, build_summary_user, length_ok

logger = logging.getLogger(__name__)

MAX_CHARS = 500


def summarize_article(client: DeepSeekClient, item: dict[str, Any]) -> dict[str, Any]:
    """生成单条中文摘报。返回带 summary 字段的字典。"""
    result = client.chat_json(SYSTEM_SUMMARY, build_summary_user(item),
                              thinking_mode=config.DEEPSEEK_SUMMARY_THINKING_MODE)
    zh_title = str(result.get("zh_title") or item.get("title") or "").strip()
    body = str(result.get("summary") or "").strip()
    if not body:
        raise ValueError("摘要模型未返回 summary")
    if not length_ok(body, MAX_CHARS):
        logger.info("摘要超长(%s字)，做一次压缩重写", len(body))
        retry = client.chat_json(
            SYSTEM_SUMMARY,
            "以下摘报超过500字，请保持事实不变，压缩到500字以内并按规定JSON输出：\n\n" + body,
            thinking_mode=config.DEEPSEEK_SUMMARY_THINKING_MODE,
        )
        retry_body = str(retry.get("summary") or "").strip()
        if retry_body and length_ok(retry_body, MAX_CHARS):
            body = retry_body
            zh_title = str(retry.get("zh_title") or zh_title).strip()
        else:
            body = body[:MAX_CHARS]
    out = dict(item)
    out["zh_title"] = zh_title
    out["summary"] = body
    return out
