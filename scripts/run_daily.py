"""每日自动出报入口。

用法（Windows PowerShell，项目根目录）：
  python scripts\\run_daily.py --input sample             # 用样例联调（当前 H2 未接入时）
  python scripts\\run_daily.py --input db --send         # 从数据库取最近24h并发信（正式）
  python scripts\\run_daily.py --input db --hours 48     # 自定义时间窗（小时）

若 --input db 且库为空，会提示并退出码 3（便于定时任务判断）。
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from ai_digest import config, db
from ai_digest.llm.deepseek import DeepSeekClient
from ai_digest.report.pipeline import run_daily_pipeline

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


def main() -> int:
    parser = argparse.ArgumentParser(description="每日出报")
    parser.add_argument("--input", choices=["db", "sample"], default="db",
                        help="数据来源：db=SQLite正式库；sample=样例联调")
    parser.add_argument("--hours", type=int, help="调试用滚动时间窗；正式运行时不要传")
    parser.add_argument("--at", help="测试用当前时间，ISO 8601")
    parser.add_argument("--send", action="store_true", help="生成后发邮件")
    args = parser.parse_args()

    if not config.has_deepseek_key():
        logger.error("未配置 DEEPSEEK_API_KEY（请检查 .env）")
        return 2

    if args.input == "db":
        db.init_db()
        if args.hours is not None:
            items = db.load_recent_articles(hours=args.hours)
            logger.info("从库读取最近 %d 小时文章 %d 条", args.hours, len(items))
            report_time = datetime.now(ZoneInfo(config.REPORT_TZ))
        else:
            report_time = datetime.fromisoformat(args.at) if args.at else datetime.now(ZoneInfo(config.REPORT_TZ))
            if report_time.tzinfo is None:
                report_time = report_time.replace(tzinfo=ZoneInfo(config.REPORT_TZ))
            report_time = report_time.astimezone(ZoneInfo(config.REPORT_TZ))
            window_end = report_time.replace(
                hour=config.REPORT_HOUR, minute=0, second=0, microsecond=0
            )
            if report_time < window_end:
                window_end -= timedelta(days=1)
            window_start = window_end - timedelta(days=1)
            items = db.load_articles_between(window_start, window_end)
            logger.info(
                "从库读取日报窗口 [%s, %s) 文章 %d 条",
                window_start.isoformat(), window_end.isoformat(), len(items),
            )
    else:
        items = load_sample(config.SAMPLE_DIR / "sample_articles.jsonl")
        logger.info("加载样例 %d 条", len(items))
        report_time = datetime.now(ZoneInfo(config.REPORT_TZ))

    if not items:
        logger.warning("当前查询窗口没有可处理文章")
        return 3

    client = DeepSeekClient()
    date_text = report_time.strftime("%Y年%m月%d日")
    docx_path = OUT_DIR / f"摘报_{report_time:%Y%m%d}.docx"
    stats = run_daily_pipeline(client, items, docx_path, send=args.send, date_text=date_text)

    logger.info("统计：候选 %d → 入选 %d → 摘要 %d", stats["candidates"],
                stats["in_scope"], stats["summarized"])
    print(f"完成。docx：{docx_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
