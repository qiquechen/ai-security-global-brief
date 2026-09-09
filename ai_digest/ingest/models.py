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


@dataclass(frozen=True)
class Candidate:
    url: str
    title_hint: str = ""
    published_hint: datetime | None = None


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
    discovered: int = 0
    accepted: int = 0
    inserted: int = 0
    existing: int = 0
    outside_window: int = 0
    missing_time: int = 0
    empty_text: int = 0
    errors: list[str] = field(default_factory=list)

    @property
    def status(self) -> str:
        if self.errors and self.accepted == 0:
            return "failed"
        if self.errors:
            return "partial"
        return "success"
