"""每日出报编排：判定 → 摘要 → 排序 → docx →（可选）发信。"""
from __future__ import annotations

import logging
from datetime import datetime
from pathlib import Path
from typing import Iterable, Optional

from .. import config
from ..deliver.emailer import send_email
from ..filter.classify import classify_items
from ..llm.deepseek import DeepSeekClient
from ..report.docx_builder import build_report_docx
from ..report.rank import rank_items
from ..summarize.run import summarize_article

logger = logging.getLogger("pipeline")


def run_daily_pipeline(
    client: DeepSeekClient,
    items: Iterable[dict],
    docx_path: str | Path,
    *,
    send: bool = False,
    max_items: int = 20,
    date_text: Optional[str] = None,
) -> dict:
    """执行完整出报流程，返回统计信息。items 需含 title/url/published_at/text/source_name/country。"""
    items = list(items)
    stats = {"candidates": len(items)}

    # 1) 语义判定
    decisions = classify_items(client, items)
    picked: list[dict] = []
    for it, dec in zip(items, decisions):
        logger.info("[%s] %s | %s | %s",
                    "收" if dec["in_scope"] else "剔",
                    dec.get("category"), dec.get("importance"), dec.get("reason"))
        if dec["in_scope"]:
            it["importance"] = dec["importance"]
            it["category"] = dec["category"]
            it["decision_reason"] = dec["reason"]
            picked.append(it)
    stats["in_scope"] = len(picked)

    # 2) 排序与配额
    ranked = rank_items(picked, max_items=max_items)
    stats["ranked"] = len(ranked)

    # 3) 摘要
    summarized = [summarize_article(client, it) for it in ranked]
    stats["summarized"] = len(summarized)

    # 4) 公文 docx
    docx_path = Path(docx_path)
    docx_path.parent.mkdir(parents=True, exist_ok=True)
    build_report_docx(summarized, docx_path,
                      date_text=date_text or datetime.now().strftime("%Y年%m月%d日"))
    logger.info("已生成：%s", docx_path)

    # 5) 发信（可选）
    if send:
        subject_date = date_text or datetime.now().strftime("%Y-%m-%d")
        subject = f"{config.MAIL_SUBJECT_PREFIX}{subject_date}"
        send_email(subject, docx_path=docx_path,
                   text_body=f"今日摘报共 {len(summarized)} 条，见附件。")
        stats["sent"] = True

    return stats
