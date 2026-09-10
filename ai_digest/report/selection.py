"""Classify once per URL and extend history only for underfilled groups."""
from datetime import timedelta

from ..audit import operation
from ..filter.classify import classify_items
from .docx_builder import collection_group


class InsufficientMaterials(ValueError):
    pass


def select_materials(client, items, *, minimum, window_start=None,
                     history_loader=None, backfill=None, max_days=30):
    picked, seen = [], set()
    stats = dict(candidates=0, expansion_days=0, blacklisted=0)

    def counts():
        return {g: sum(collection_group(x) == g for x in picked)
                for g in ("media", "institution")}

    def missing():
        return {g for g, n in counts().items() if n < minimum}

    def consume(values, groups):
        batch = []
        for item in values:
            url = item.get("url")
            if not url or url in seen or collection_group(item) not in groups:
                continue
            seen.add(url)
            batch.append(item)
        if not batch:
            return
        decisions = classify_items(client, batch)
        stats["candidates"] += len(batch)
        stats["blacklisted"] += sum(bool(d.get("blacklisted")) for d in decisions)
        if len(decisions) != len(batch) or any(d.get("classification_error") for d in decisions):
            raise ValueError("部分文章判定失败；本次不发布不完整邮件包")
        for item, decision in zip(batch, decisions):
            if decision["in_scope"]:
                picked.append(dict(item, importance=decision["importance"],
                                   category=decision["category"],
                                   decision_reason=decision["reason"]))

    with operation("selection", minimum_per_group=minimum) as log:
        consume(items, {"media", "institution"})
        earliest = window_start
        for day in range(1, max_days + 1):
            if not missing() or earliest is None or history_loader is None:
                break
            end, start = earliest, earliest - timedelta(days=1)
            with operation("selection.expand", day=day, start=start, end=end,
                           missing_groups=sorted(missing())) as step:
                consume(history_loader(start, end), missing())
                if missing() and backfill is not None:
                    step["backfill_result"] = backfill(start, end, missing())
                    consume(history_loader(start, end), missing())
                step["counts"] = counts()
            earliest = start
            stats["expansion_days"] = day
        stats.update(counts=counts(), effective_start=earliest)
        log.update(stats)
        if missing():
            raise InsufficientMaterials(
                f"向前扩展{stats['expansion_days']}天后材料仍不足："
                f"媒体{counts()['media']}篇、机构{counts()['institution']}篇，"
                f"每类至少{minimum}篇；停止准备和发送，请补充来源或增加扩窗上限"
            )
    return picked, stats
