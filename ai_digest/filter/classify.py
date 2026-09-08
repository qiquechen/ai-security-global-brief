"""语义判定执行：单篇/批量。"""
from __future__ import annotations

import logging
import time
from typing import Any, Iterable

from ..llm.deepseek import DeepSeekClient
from .prompts import SYSTEM_PROMPT, build_user_message, validate_decision

logger = logging.getLogger(__name__)


def classify_article(client: DeepSeekClient, item: dict[str, Any]) -> dict[str, Any]:
    """对单篇文章做判定，返回 {in_scope, category, importance, reason, ...}。"""
    user = build_user_message(item)
    raw = client.chat_json(SYSTEM_PROMPT, user)
    decision = validate_decision(raw)
    decision["url"] = item.get("url", "")
    decision["title"] = item.get("title", "")
    decision["source_name"] = item.get("source_name", "")
    decision["published_at"] = item.get("published_at", "")
    return decision


def classify_items(client: DeepSeekClient, items: Iterable[dict[str, Any]],
                   sleep_between: float = 0.3) -> list[dict[str, Any]]:
    """批量判定（MVP 顺序执行即可；量大后再并发化）。"""
    results = []
    for item in items:
        try:
            results.append(classify_article(client, item))
        except Exception as exc:  # noqa: BLE001
            logger.warning("判定失败 %s: %s", item.get("url"), exc)
            results.append({"in_scope": False, "category": None, "importance": None,
                            "reason": f"调用失败：{exc}", "url": item.get("url", "")})
        if sleep_between:
            time.sleep(sleep_between)
    return results
