"""摘要提示词与长度校验。"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

# 术语表加载，注入提示词以约束译法
GLOSSARY_PATH = Path(__file__).resolve().parent.parent / "filter" / "glossary.json"


def _load_glossary() -> list[dict[str, str]]:
    with open(GLOSSARY_PATH, encoding="utf-8") as f:
        return json.load(f)


def glossary_block() -> str:
    terms = _load_glossary()
    lines = [f"- {t['en']} = {t['zh']}" + (f"（{t['note']}）" if t.get("note") else "")
             for t in terms]
    return "\n".join(lines)


SYSTEM_SUMMARY = """你是一名专业的中文编译人员，服务对象为境外人工智能安全治理每日摘报。请把给定的英文（或外文）信息改写成中文标题和中文摘报。

要求：
1. 只基于原文内容改写，不补充外部知识，不推测，不评价；事实与数字必须能溯源原文。
2. 全程客观陈述，禁止任何主观研判、倾向性结论、预测或建议（例如"这对我方有利/不利""值得警惕/借鉴"等一律禁止）。
3. 单条总字数不超过500字（含标点）。
4. 术语须按下表译法；机构名/专名首次出现时括注英文原文。军事与自主武器相关内容一律中性用词。
5. 如原文含具体日期、机构、政策名称、数字，必须保留。
6. 只输出合法JSON对象，格式为：{"zh_title":"中文标题","summary":"中文摘报正文"}。
7. summary只写正文，不重复标题、来源、时间和链接，不使用Markdown标记。

术语对照表：
""" + glossary_block()


def build_summary_user(item: dict[str, Any]) -> str:
    return (
        f"- 标题：{item.get('title', '')}\n"
        f"- 来源：{item.get('source_name', '')}（{item.get('country', '')}）\n"
        f"- 时间：{item.get('published_at', '')}\n"
        f"- 链接：{item.get('url', '')}\n"
        f"- 原文：\n{(item.get('text') or '')[:6000]}\n\n"
        "请严格按JSON格式输出中文标题和摘报正文。"
    )


def length_ok(text: str, max_chars: int = 500) -> bool:
    return len(text) <= max_chars
