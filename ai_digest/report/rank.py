"""排序与配额。"""
from __future__ import annotations

from typing import Iterable
from .. import config
from .docx_builder import collection_group

_ORDER = {"high": 0, "med": 1, "low": 2}
MAX_ITEMS = 20


def rank_items(items: Iterable[dict], max_items: int = MAX_ITEMS, *, minimum_per_group: int = 0) -> list[dict]:
    """按重要度、加权来源评分与时间排序，并为两类保留名额。

    说明：事件级多源合并（同事件多源去重）属后续增强，当前先用 url 去重 + 重要度排序兜底。
    """
    if max_items < 2 * minimum_per_group:
        raise ValueError("总篇数上限必须至少为分类保底数量的两倍")
    seen: set[str] = set()
    uniq: list[dict] = []
    for it in items:
        url = it.get("url") or ""
        if not url or url in seen:
            continue
        seen.add(url)
        uniq.append(it)

    # 稳定排序：同等重要度下，机构来源适度加权，再按时间从新到旧。
    uniq.sort(key=lambda x: x.get("published_at") or "", reverse=True)
    uniq.sort(key=lambda x: (int(x.get("source_priority") or 50) * (config.REPORT_INSTITUTION_WEIGHT if collection_group(x) == "institution" else 1)), reverse=True)
    uniq.sort(key=lambda x: _ORDER.get(x.get("importance"), 2))
    reserved = set()
    for group in ("media", "institution"):
        group_items = [x for x in uniq if collection_group(x) == group]
        reserved.update(x["url"] for x in group_items[:minimum_per_group])
    selected = set(reserved)
    for item in uniq:
        if len(selected) >= max_items:
            break
        selected.add(item["url"])
    return [x for x in uniq if x["url"] in selected]
