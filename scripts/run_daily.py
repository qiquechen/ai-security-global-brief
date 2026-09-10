"""每日自动出报入口。

用法（Windows PowerShell，项目根目录）：
  python scripts\\run_daily.py --input sample             # 用样例联调（当前 H2 未接入时）
  python scripts\\run_daily.py --input db --send         # 读取06:00截止窗口，生成双合集并发信
  python scripts\\run_daily.py --input db --hours 48     # 自定义时间窗（小时）

若 --input db 且库为空，会提示并退出码 3（便于定时任务判断）。
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from ai_digest import config, db
from ai_digest.llm.deepseek import DeepSeekClient
from ai_digest.report.pipeline import run_daily_pipeline
from ai_digest.deliver.prepared import send_prepared
from ai_digest.audit import format_duration, operation
from ai_digest.report.history import backfill_history

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("run_daily")

OUT_DIR = config.ROOT / "report_output"


def load_sample(path: Path) -> list[dict]:
    items = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                items.append(json.loads(line))
    return items


def daily_window(report_time: datetime) -> tuple[datetime, datetime]:
    """返回以本地06:00为边界的上一完整24小时窗口。"""
    window_end = report_time.replace(
        hour=config.REPORT_CUTOFF_HOUR, minute=0, second=0, microsecond=0
    )
    if report_time < window_end:
        window_end -= timedelta(days=1)
    return window_end - timedelta(days=1), window_end


def main() -> int:
    parser = argparse.ArgumentParser(description="每日出报")
    parser.add_argument("--input", choices=["db", "sample"], default="db",
                        help="数据来源：db=SQLite正式库；sample=样例联调")
    parser.add_argument("--hours", type=int, help="调试用滚动时间窗；正式运行时不要传")
    parser.add_argument("--at", help="测试用当前时间，ISO 8601")
    parser.add_argument("--send", action="store_true", help="生成后发邮件")
    parser.add_argument("--send-prepared", type=Path, help="只发送指定邮件清单，不调用模型、不重新生成文件")
    parser.add_argument("--ready-file", type=Path, help="准备成功后原子写入待发送清单路径")
    args = parser.parse_args()
    if args.send_prepared:
        if args.send or args.hours is not None or args.at or args.ready_file:
            parser.error("--send-prepared 不可与 --send/--hours/--at 混用")
        started = time.monotonic()
        send_prepared(args.send_prepared)
        print(f"发送完成，耗时：{format_duration(time.monotonic() - started)}")
        return 0

    # A failed replacement batch must not leave an older ready pointer sendable.
    if args.ready_file:
        args.ready_file.unlink(missing_ok=True)

    if not config.has_deepseek_key():
        logger.error("未配置 DEEPSEEK_API_KEY（请检查 .env）")
        return 2

    if args.input == "db":
        db.init_db()
        if args.hours is not None:
            if args.hours <= 0:
                parser.error("--hours 必须大于0")
            window_end = datetime.now(ZoneInfo(config.REPORT_TZ))
            window_start = window_end - timedelta(hours=args.hours)
            items = db.load_articles_between(window_start, window_end)
            logger.info("从库读取最近 %d 小时文章 %d 条", args.hours, len(items))
            report_time = datetime.now(ZoneInfo(config.REPORT_TZ))
        else:
            report_time = datetime.fromisoformat(args.at) if args.at else datetime.now(ZoneInfo(config.REPORT_TZ))
            if report_time.tzinfo is None:
                report_time = report_time.replace(tzinfo=ZoneInfo(config.REPORT_TZ))
            report_time = report_time.astimezone(ZoneInfo(config.REPORT_TZ))
            window_start, window_end = daily_window(report_time)
            items = db.load_articles_between(window_start, window_end)
            logger.info(
                "从库读取日报窗口 [%s, %s) 文章 %d 条",
                window_start.isoformat(), window_end.isoformat(), len(items),
            )
    else:
        items = load_sample(config.SAMPLE_DIR / "sample_articles.jsonl")
        logger.info("加载样例 %d 条", len(items))
        report_time = datetime.now(ZoneInfo(config.REPORT_TZ))

    if not items and args.input != "db":
        logger.warning("当前查询窗口没有可处理文章")
        return 3

    client = DeepSeekClient()
    date_text = report_time.strftime("%Y年%m月%d日")
    date_stamp = report_time.strftime("%Y%m%d_%H%M%S" if args.hours is not None else "%Y%m%d")
    output_dir = OUT_DIR / date_stamp
    if args.input == "db":
        window_text = (
            f"{window_start:%Y-%m-%d %H:%M} 至 {window_end:%Y-%m-%d %H:%M}"
            f"（{config.REPORT_TZ}）"
        )
    else:
        window_text = None
    stats = run_daily_pipeline(
        client,
        items,
        output_dir,
        send=args.send,
        date_text=date_text,
        date_stamp=date_stamp,
        window_text=window_text,
        window_start=window_start if args.input == "db" else None,
        history_loader=db.load_articles_between if args.input == "db" else None,
        backfill=backfill_history if args.input == "db" else None,
        minimum_per_group=None if args.input == "db" else 0,
    )

    if args.ready_file:
        args.ready_file.parent.mkdir(parents=True, exist_ok=True)
        temporary = args.ready_file.with_suffix(".tmp")
        temporary.write_text(stats["manifest"], encoding="utf-8")
        temporary.replace(args.ready_file)
    print(f"待发送清单：{stats['manifest']}")
    logger.info("统计：候选 %d → 入选 %d → 摘要 %d", stats["candidates"],
                stats["in_scope"], stats["summarized"])
    print("完成。双合集：")
    for path in stats["collections"]:
        print(f"- {path}")
    print(f"逐条原文：{stats['originals']} 份；输出目录：{output_dir}")
    print(f"摘要生成耗时：{format_duration(stats.get('summary_seconds', 0))}")
    print(f"本次整理总耗时：{format_duration(stats.get('total_seconds', 0))}")
    return 0


if __name__ == "__main__":
    with operation("daily.command") as log:
        code = main()
        log["exit_code"] = code
    sys.exit(code)
