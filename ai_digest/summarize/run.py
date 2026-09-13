"""摘要生成、完整性与事实审核；不合格重写一次，绝不硬截断。"""
from __future__ import annotations
import hashlib
import json
import uuid
from .. import config
from ..audit import operation
from .prompts import (SYSTEM_SUMMARY, SYSTEM_REVIEW, build_summary_user,
                      integrity_issues, year_issues)

MAX_CHARS = 500
QUALITY_VERSION = 3


def review_passed(review):
    if not (isinstance(review, dict) and review.get("complete") is True
            and review.get("faithful") is True and review.get("issues") == []):
        return False
    # 逐条举证中只要有一条断言"原文无依据"，即视为不通过
    claims = review.get("claims")
    if isinstance(claims, list):
        for claim in claims:
            if isinstance(claim, dict) and str(claim.get("verdict", "")).lower() == "unsupported":
                return False
    return True


def _fact_source(item):
    """年份核对的比对范围：正文 + 发布时间 + 标题（摘要里的年份可能来自元数据）。"""
    return " ".join(str(item.get(k) or "") for k in ("text", "published_at", "title"))


def summarize_article(client, item):
    user = build_summary_user(item)
    key = hashlib.sha256(json.dumps([
        QUALITY_VERSION, SYSTEM_SUMMARY, SYSTEM_REVIEW, user,
        getattr(client, "chat_model", config.DEEPSEEK_CHAT_MODEL),
        config.DEEPSEEK_SUMMARY_THINKING_MODE,
    ], ensure_ascii=False).encode()).hexdigest()
    cache = config.DATA_DIR / "summary_cache" / f"{key}.json"
    with operation("summary.article", url=item.get("url"), retries=0, cache_hit=False,
                   thinking_mode=config.DEEPSEEK_SUMMARY_THINKING_MODE) as log:
        try:
            saved = json.loads(cache.read_text(encoding="utf-8"))
            result = saved["result"]
            if (saved.get("quality_version") != QUALITY_VERSION or integrity_issues(result)
                    or year_issues(result, _fact_source(item))
                    or not review_passed(saved.get("review"))):
                raise ValueError("invalid cache")
            log["cache_hit"] = True
        except (OSError, ValueError, TypeError, AttributeError, KeyError):
            prompt = user
            for attempt in range(2):
                result = client.chat_json(SYSTEM_SUMMARY, prompt, thinking_mode=(
                    config.DEEPSEEK_SUMMARY_THINKING_MODE if attempt == 0 else "disabled"))
                issues = integrity_issues(result) + year_issues(result, _fact_source(item))
                log["retries"] = attempt
                if attempt == 0:
                    body = result.get("summary") if isinstance(result, dict) else None
                    log["initial_chars"] = len(body) if isinstance(body, str) else 0
                review = None
                if not issues:
                    result = {"zh_title": str(result.get("zh_title") or item.get("title") or "").strip(),
                              "summary": result["summary"].strip()}
                    with operation("summary.review", url=item.get("url"), attempt=attempt + 1) as audit:
                        review = client.chat_json(SYSTEM_REVIEW, user + "\n待审结果：\n"
                                                  + json.dumps(result, ensure_ascii=False), thinking_mode="disabled")
                        audit.update(result=review, passed=review_passed(review))
                    if not review_passed(review):
                        issues = ["语义/事实审核未通过：" + json.dumps(review, ensure_ascii=False)]
                log["quality_issues"] = issues
                if not issues:
                    break
                log["rejected_result"] = result
                prompt = (user + "\n上次结果未通过审核：" + json.dumps(issues, ensure_ascii=False)
                          + "\n请基于原文重新编写300—400字符、最多500字符的完整摘报。保留条件与否定，不得仅补标点掩盖断句。\n上次结果："
                          + json.dumps(result, ensure_ascii=False))
            else:
                raise ValueError("摘要重写后仍未通过完整性/事实审核；不缓存、不生成截断摘报")
            cache.parent.mkdir(parents=True, exist_ok=True)
            temp = cache.with_suffix(f".{uuid.uuid4().hex}.tmp")
            temp.write_text(json.dumps(dict(quality_version=QUALITY_VERSION, result=result, review=review),
                                       ensure_ascii=False), encoding="utf-8")
            temp.replace(cache)
        log.update(result=result, chars=len(result["summary"]))
        return dict(item, **result)
