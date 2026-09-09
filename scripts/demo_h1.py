"""H1 最小链路演示：样例判定 → 双合集/原文 Word → 可选单次发信。"""
from __future__ import annotations

import argparse
import json
import logging
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from ai_digest import config
from ai_digest.llm.deepseek import DeepSeekClient
from ai_digest.report.pipeline import run_daily_pipeline

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("demo_h1")

OUT_DIR = config.ROOT / "report_output" / "demo"


def load_sample(path: Path) -> list[dict]:
    items = []
    with open(path, encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if line:
                items.append(json.loads(line))
    return items


def main() -> int:
    parser = argparse.ArgumentParser(description="H1 双合集最小链路 demo")
    parser.add_argument("--send", action="store_true", help="全部生成后只发一封邮件")
    args = parser.parse_args()
    if not config.has_deepseek_key():
        print("未配置 DEEPSEEK_API_KEY。请检查项目 .env。")
        return 2

    items = load_sample(config.SAMPLE_DIR / "sample_articles.jsonl")
    logger.info("加载样例 %d 条", len(items))
    now = datetime.now(ZoneInfo(config.REPORT_TZ))
    stats = run_daily_pipeline(
        DeepSeekClient(),
        items,
        OUT_DIR,
        send=args.send,
        date_text=now.strftime("%Y年%m月%d日"),
        date_stamp="demo",
    )
    logger.info(
        "完成：新闻媒体%d条，机构信息%d条，逐条原文%d份",
        stats["media"], stats["institution"], stats["originals"],
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
