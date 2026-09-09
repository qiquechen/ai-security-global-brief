"""排序与配额。"""
from __future__ import annotations

from typing import Iterable

_ORDER = {"high": 0, "med": 1, "low": 2}
MAX_ITEMS = 20


def rank_items(items: Iterable[dict], max_items: int = MAX_ITEMS) -> list[dict]:
    """按重要度 + 时间排序，截断到 20 条。

    说明：事件级多源合并（同事件多源去重）属后续增强，当前先用 url 去重 + 重要度排序兜底。
    """
    seen: set[str] = set()
    uniq: list[dict] = []
    for it in items:
        url = it.get("url") or ""
        if not url or url in seen:
            continue
        seen.add(url)
        uniq.append(it)

    # 稳定排序：同等重要度下，优先高权威来源，再按时间从新到旧。
    uniq.sort(key=lambda x: x.get("published_at") or "", reverse=True)
    uniq.sort(key=lambda x: int(x.get("source_priority") or 50), reverse=True)
    uniq.sort(key=lambda x: _ORDER.get(x.get("importance"), 2))
    return uniq[:max_items]
