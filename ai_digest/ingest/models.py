"""采集侧内部数据模型；不改变 H1 的 SQLite 契约。"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime


@dataclass(frozen=True)
class Source:
    id: str
    name: str
    country: str
    type: str
    method: str
    homepage: str
    url: str
    feed: str
    enabled: bool
    name_en: str = ""
    category_hint: str = ""
    allowed_domains: tuple[str, ...] = ()
    include_patterns: tuple[str, ...] = ()
    exclude_patterns: tuple[str, ...] = ()
    exclude_exact_paths: tuple[str, ...] = ()
    max_candidates: int = 20
    use_discovered_feed: bool = False
    priority: int = 50
    # off: requests only; fallback: use Crawl4AI after static failure/weak body;
    # always: browser-render entry and article pages.
    lnc_mode: str = "off"
    article_url_pattern: str = ""


@dataclass(frozen=True)
class Candidate:
    url: str
    title_hint: str = ""
    published_hint: datetime | None = None
    relevance_score: int = 0


@dataclass(frozen=True)
class Article:
    source_id: str
    url: str
    title: str
    published_at: str
    crawled_at: str
    text: str
    dedup_hash: str


@dataclass
class CrawlStats:
    source_id: str
    elapsed_seconds: float = 0.0
    raw_discovered: int = 0
    url_filtered: int = 0
    hint_outside: int = 0
    eligible: int = 0
    discovered: int = 0
    fetched: int = 0
    cached_outside: int = 0
    accepted: int = 0
    inserted: int = 0
    existing: int = 0
    blacklisted: int = 0
    outside_window: int = 0
    missing_time: int = 0
    empty_text: int = 0
    lnc_attempted: int = 0
    lnc_succeeded: int = 0
    lnc_recovered: int = 0
    errors: list[str] = field(default_factory=list)
    # 运行结束后由主线程持久化；审计事件会主动移除此内部明细。
    observed_dates: dict[str, str] = field(default_factory=dict, repr=False)

    @property
    def status(self) -> str:
        if self.errors and self.accepted == 0:
            return "failed"
        if self.errors:
            return "partial"
        return "success"
