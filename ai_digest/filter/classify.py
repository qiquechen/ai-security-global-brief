"""语义判定执行：单篇/批量。"""
from __future__ import annotations

import logging
import time
from typing import Any, Iterable

from .. import config, db
from ..audit import operation
from ..llm.deepseek import DeepSeekClient
from .prompts import SYSTEM_PROMPT, build_user_message, validate_decision

logger = logging.getLogger(__name__)


def classify_article(client: DeepSeekClient, item: dict[str, Any]) -> dict[str, Any]:
    """对单篇文章做判定，返回 {in_scope, category, importance, reason, ...}。"""
    user = build_user_message(item)
    raw = client.chat_json(SYSTEM_PROMPT, user,
                           thinking_mode=config.DEEPSEEK_FILTER_THINKING_MODE)
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
    rejected = db.active_rejections()
    for item in items:
        key = db.rejection_key(item.get("url", ""))
        if key and key in rejected:
            results.append({"in_scope": False, "reason": "命中有效期内剔除记录",
                            "url": item.get("url", ""), "blacklisted": True})
            continue
        try:
            with operation("classification.article", url=item.get("url")) as log:
                decision = classify_article(client, item)
                log["result"] = decision
        except Exception as exc:  # noqa: BLE001
            logger.warning("判定失败 %s: %s", item.get("url"), exc)
            results.append({"in_scope": False, "category": None, "importance": None,
                            "reason": f"调用失败：{exc}", "url": item.get("url", ""),
                            "classification_error": True})
        else:
            if decision["in_scope"] is False:
                db.reject_article(item, decision.get("reason", ""))
                logger.info("记录剔除并删除候选：%s", item.get("url", ""))
                if key:
                    rejected.add(key)
            results.append(decision)
        if sleep_between:
            time.sleep(sleep_between)
    return results
