"""每日出报编排：判定 → 摘要 → 排序 → docx →（可选）发信。"""
from __future__ import annotations

import logging
import time
from datetime import datetime
from pathlib import Path
from typing import Iterable, Optional
from zipfile import ZIP_DEFLATED, ZipFile

from .. import config
from ..deliver.prepared import prepare_mail, send_prepared
from ..audit import operation
import uuid
from .selection import select_materials
from ..llm.deepseek import DeepSeekClient
from ..report.docx_builder import build_report_bundle, collection_group
from ..report.rank import rank_items
from ..summarize.run import summarize_article

logger = logging.getLogger("pipeline")


def _run_daily_pipeline(
    client: DeepSeekClient,
    items: Iterable[dict],
    output_dir: str | Path,
    *,
    send: bool = False,
    max_items: int = 20,
    date_text: Optional[str] = None,
    date_stamp: Optional[str] = None,
    window_text: Optional[str] = None,
    window_start: Optional[datetime] = None,
    history_loader=None,
    backfill=None,
    minimum_per_group: Optional[int] = None,
) -> dict:
    """生成双合集和原文，并按配置逐个或压缩投递原文。"""
    started = time.monotonic()
    items = list(items)
    minimum = config.REPORT_MIN_PER_GROUP if minimum_per_group is None else minimum_per_group
    if minimum < 0 or max_items < 2 * minimum:
        raise ValueError("总篇数上限必须至少为分类保底数量的两倍，保底数量不得为负")
    with operation("classification", initial_candidates=len(items)) as stage:
        picked, selection = select_materials(
            client, items, minimum=minimum, window_start=window_start,
            history_loader=history_loader, backfill=backfill,
            max_days=config.REPORT_MAX_EXPANSION_DAYS,
        )
        stage.update(selection, in_scope=len(picked))
    stats = dict(selection, initial_candidates=len(items), in_scope=len(picked))
    if selection["expansion_days"]:
        note = (f"向前扩展{selection['expansion_days']}天补足分类配额；"
                f"实际覆盖起点：{selection['effective_start']:%Y-%m-%d %H:%M}")
        window_text = f"{window_text}；{note}" if window_text else note
    ranked = rank_items(picked, max_items=max_items, minimum_per_group=minimum)
    stats["ranked"] = len(ranked)

    # 3) 摘要
    with operation("summary", articles=len(ranked)) as stage:
        summary_started = time.monotonic()
        summarized = []
        failed = []
        for it in ranked:
            try:
                summarized.append(summarize_article(client, it))
            except Exception as exc:  # 单篇审核不通过不影响整份报告
                failed.append({"url": it.get("url"), "error": str(exc)})
                logger.warning("摘要未通过审核，已跳过该篇：%s（%s）", it.get("url"), exc)
        summary_seconds = round(time.monotonic() - summary_started, 3)
        if ranked and not summarized:
            raise ValueError(f"全部 {len(ranked)} 篇摘要均未通过审核，不生成报告")
        summary_counts = {
            group: sum(collection_group(item) == group for item in summarized)
            for group in ("media", "institution")
        }
        below_minimum = {
            group: count for group, count in summary_counts.items() if count < minimum
        }
        if below_minimum:
            detail = "、".join(
                f"{group}={count}/{minimum}" for group, count in below_minimum.items()
            )
            raise ValueError(f"摘要审核后分类保底数量不足：{detail}；不生成不完整报告")
        stage["summarized"] = len(summarized)
        stage["summary_failed"] = failed
        stage["summary_counts"] = summary_counts
        stage["summary_seconds"] = summary_seconds
    stats["summarized"] = len(summarized)
    stats["summary_seconds"] = summary_seconds
    logger.info("摘要生成完成：%d 篇，耗时 %.1f 秒", len(summarized), summary_seconds)

    # 4) 双合集 + 每篇抓取原文 Word
    output_dir = Path(output_dir) / (datetime.now().strftime("%H%M%S") + "_" + uuid.uuid4().hex[:8])
    date_stamp = date_stamp or datetime.now().strftime("%Y%m%d")
    with operation("report.files"):
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

    # 准备阶段完成附件打包，发送阶段只读取清单。
    with operation("mail.prepare") as stage:
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
        manifest = prepare_mail(
            output_dir, subject,
            f"今日摘报共 {len(summarized)} 条：新闻媒体 {stats['media']} 条，"
            f"机构信息 {stats['institution']} 条。{attachment_description}见附件。"
            + (f"统计窗口：{window_text}。" if window_text else ""),
            attachments,
        )
        stats["manifest"] = str(manifest)
        stage.update(manifest=str(manifest), attachment_count=len(attachments))
    if send:
        send_prepared(manifest)
        stats["sent"] = True

    stats["total_seconds"] = round(time.monotonic() - started, 3)
    return stats


def run_daily_pipeline(*args, **kwargs):
    with operation("report") as log:
        result = _run_daily_pipeline(*args, **kwargs)
        log.update(result)
        return result
