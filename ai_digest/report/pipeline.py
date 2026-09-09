"""每日出报编排：判定 → 摘要 → 排序 → docx →（可选）发信。"""
from __future__ import annotations

import logging
from datetime import datetime
from pathlib import Path
from typing import Iterable, Optional
from zipfile import ZIP_DEFLATED, ZipFile

from .. import config
from ..deliver.emailer import send_email
from ..filter.classify import classify_items
from ..llm.deepseek import DeepSeekClient
from ..report.docx_builder import build_report_bundle
from ..report.rank import rank_items
from ..summarize.run import summarize_article

logger = logging.getLogger("pipeline")


def run_daily_pipeline(
    client: DeepSeekClient,
    items: Iterable[dict],
    output_dir: str | Path,
    *,
    send: bool = False,
    max_items: int = 20,
    date_text: Optional[str] = None,
    date_stamp: Optional[str] = None,
    window_text: Optional[str] = None,
) -> dict:
    """生成双合集和原文，并按配置逐个或压缩投递原文。"""
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

    # 4) 双合集 + 每篇抓取原文 Word
    output_dir = Path(output_dir)
    date_stamp = date_stamp or datetime.now().strftime("%Y%m%d")
    bundle = build_report_bundle(
        summarized,
        output_dir,
        date_text=date_text or datetime.now().strftime("%Y年%m月%d日"),
        date_stamp=date_stamp,
        window_text=window_text,
    )
    collections = []
    originals = []
    for key in ("media", "institution"):
        group = bundle[key]
        collections.append(group["collection"])
        originals.extend(group["originals"])
        logger.info(
            "已生成%s：合集1份，原文%d份",
            "新闻媒体" if key == "media" else "机构信息",
            group["count"],
        )
    stats["media"] = bundle["media"]["count"]
    stats["institution"] = bundle["institution"]["count"]
    stats["collections"] = [
        str(bundle["media"]["collection"]),
        str(bundle["institution"]["collection"]),
    ]
    stats["originals"] = sum(group["count"] for group in bundle.values())

    # 5) 发信（可选）
    if send:
        zip_originals = bool(originals) and (
            config.MAIL_ORIGINALS_ZIP
            or len(originals) > config.MAIL_ORIGINALS_ZIP_THRESHOLD
        )
        attachments = list(collections)
        if zip_originals:
            archive_path = output_dir / f"原文_{date_stamp}.zip"
            # 只归档本次生成的原文；保留分类目录以避免不同类别文件同名。
            with ZipFile(archive_path, "w", compression=ZIP_DEFLATED) as archive:
                for original in originals:
                    archive.write(original, arcname=original.relative_to(output_dir).as_posix())
            attachments.append(archive_path)
            logger.info("原文%d份已打包：%s", len(originals), archive_path)
        else:
            attachments.extend(originals)
        attachment_description = "两个合集"
        if originals:
            attachment_description += "及原文压缩包" if zip_originals else "及逐条原文"
        subject_date = date_text or datetime.now().strftime("%Y-%m-%d")
        subject = f"{config.MAIL_SUBJECT_PREFIX}{subject_date}"
        send_email(
            subject,
            attachment_paths=attachments,
            text_body=(
                f"今日摘报共 {len(summarized)} 条：新闻媒体 {stats['media']} 条，"
                f"机构信息 {stats['institution']} 条。{attachment_description}见附件。"
            ),
        )
        stats["sent"] = True

    return stats
