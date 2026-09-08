"""H1 最小链路演示：读样例 → 语义判定 → 入选摘要 → 生成 docx（可选发信）。

用法：
  python scripts/demo_h1.py          # 生成 report_output/demo_摘报.docx
  python scripts/demo_h1.py --send   # 生成后发送到 .env 的 RECIPIENT
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path

# 保证从任何位置执行都能导入 ai_digest 包
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from ai_digest import config
from ai_digest.deliver.emailer import send_email
from ai_digest.filter.classify import classify_items
from ai_digest.llm.deepseek import DeepSeekClient
from ai_digest.report.docx_builder import build_report_docx
from ai_digest.report.rank import rank_items
from ai_digest.summarize.run import summarize_article

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("demo_h1")

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
    parser = argparse.ArgumentParser(description="H1 最小链路 demo")
    parser.add_argument("--send", action="store_true", help="生成后发邮件")
    args = parser.parse_args()

    if not config.has_deepseek_key():
        print("未配置 DEEPSEEK_API_KEY。请先：cp .env.example .env 并填入真实 key。")
        return 2

    items = load_sample(config.SAMPLE_DIR / "sample_articles.jsonl")
    logger.info("加载样例 %d 条", len(items))

    client = DeepSeekClient()
    decisions = classify_items(client, items)

    picked = []
    for it, dec in zip(items, decisions):
        logger.info("[%s] %s | %s | %s",
                    "收" if dec["in_scope"] else "剔",
                    dec.get("category"), dec.get("importance"), dec.get("reason"))
        if dec["in_scope"]:
            picked.append(it)

    ranked = rank_items(picked)
    logger.info("入选 %d 条（配额上限 20）", len(ranked))

    summarized = [summarize_article(client, it) for it in ranked]

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    out = build_report_docx(summarized, OUT_DIR / "demo_摘报.docx")
    logger.info("已生成：%s", out)

    if args.send:
        send_email("【AI安全治理摘报】demo", docx_path=out,
                   text_body=f"共 {len(summarized)} 条，见附件。")
        logger.info("已发送")
    return 0


if __name__ == "__main__":
    sys.exit(main())
