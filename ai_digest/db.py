"""SQLite 存取（C2 契约）。

- schema 由 H1 维护；H2 的采集模块只需调用 insert_article() 写入。
- 文章被读取时，source_name / country 由本模块根据 config/sources.json 中
  source_id 自动附上，保证下游 filter 拿到完整字段。
"""
from __future__ import annotations

import json
import sqlite3
from contextlib import closing
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Iterable, Optional

from . import config

SCHEMA = """
CREATE TABLE IF NOT EXISTS articles (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    source_id    TEXT NOT NULL,
    url          TEXT NOT NULL UNIQUE,
    title        TEXT NOT NULL,
    published_at TEXT,               -- ISO 格式：YYYY-MM-DD 或 YYYY-MM-DDTHH:MM:SS
    crawled_at   TEXT,
    text         TEXT,               -- 清洗后的正文全文
    dedup_hash   TEXT
);
CREATE INDEX IF NOT EXISTS idx_articles_published ON articles(published_at);
CREATE INDEX IF NOT EXISTS idx_articles_source   ON articles(source_id);
CREATE INDEX IF NOT EXISTS idx_articles_dedup    ON articles(dedup_hash);
"""


def _connect() -> sqlite3.Connection:
    db = sqlite3.connect(config.DB_PATH)
    db.row_factory = sqlite3.Row
    return db


# 公开别名：采集模块 import db 后调用 db.connect()
connect = _connect


def init_db() -> None:
    """建表（幂等）。run_daily / ingest 启动时调用即可。"""
    with closing(_connect()) as connection:
        connection.executescript(SCHEMA)
        connection.commit()


def _source_map() -> dict[str, dict]:
    """读取来源元数据，供筛选、分组和排序使用。"""
    path = config.ROOT / "config" / "sources.json"
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}
    out = {}
    for s in data.get("sources", []):
        sid = s.get("id", "")
        if sid:
            out[sid] = {
                "name": s.get("name", sid),
                "name_en": s.get("name_en", ""),
                "country": s.get("country", ""),
                "type": s.get("type", ""),
                "priority": int(s.get("priority", 50)),
                "category_hint": s.get("category_hint", ""),
            }
    return out


def insert_article(db: sqlite3.Connection, *, source_id: str, url: str, title: str,
                   published_at: Optional[str] = None, text: Optional[str] = None,
                   crawled_at: Optional[str] = None, dedup_hash: Optional[str] = None) -> bool:
    """插入一篇文章；URL 重复时忽略（返回 False）。"""
    crawled_at = crawled_at or datetime.now().isoformat(timespec="seconds")
    # URL 去重之外再按正文指纹去重，避免转载链接或规范链接变化造成重复入库。
    cur = db.execute(
        """
        INSERT OR IGNORE INTO articles
            (source_id, url, title, published_at, crawled_at, text, dedup_hash)
        SELECT ?,?,?,?,?,?,?
        WHERE NOT EXISTS (
            SELECT 1 FROM articles
            WHERE url = ? OR (? IS NOT NULL AND ? != '' AND dedup_hash = ?)
        )
        """,
        (
            source_id, url, title, published_at, crawled_at, text, dedup_hash,
            url, dedup_hash, dedup_hash, dedup_hash,
        ),
    )
    return cur.rowcount > 0


def load_articles_between(start: datetime, end: datetime) -> list[dict]:
    """按精确的左闭右开时间窗读取文章，并附上来源名称与国家。"""
    if start.tzinfo is None:
        start = start.replace(tzinfo=UTC)
    if end.tzinfo is None:
        end = end.replace(tzinfo=UTC)
    smap = _source_map()
    with closing(_connect()) as connection:
        rows = connection.execute(
            "SELECT * FROM articles WHERE published_at >= ? AND published_at < ? "
            "ORDER BY published_at DESC",
            (
                start.astimezone(UTC).replace(microsecond=0).isoformat(),
                end.astimezone(UTC).replace(microsecond=0).isoformat(),
            ),
        ).fetchall()
    out = []
    for r in rows:
        meta = smap.get(r["source_id"], {})
        d = dict(r)
        d["source_name"] = meta.get("name", r["source_id"])
        d["source_name_en"] = meta.get("name_en", "")
        d["country"] = meta.get("country", "")
        d["source_type"] = meta.get("type", "")
        d["source_priority"] = meta.get("priority", 50)
        d["source_category_hint"] = meta.get("category_hint", "")
        out.append(d)
    return out


def load_recent_articles(hours: int = 24) -> list[dict]:
    """读取滚动的最近 hours 小时；调试用，正式日报使用 load_articles_between。"""
    end = datetime.now(UTC)
    return load_articles_between(end - timedelta(hours=hours), end)


def import_jsonl(path: Path, source_id: str = "sample") -> int:
    """工具函数：把 jsonl 样例导入库（用于联调/测试，H2 正式采集不用它）。"""
    init_db()
    n = 0
    with closing(_connect()) as connection:
        with connection, open(path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                item = json.loads(line)
                if insert_article(
                    connection, source_id=item.get("source_id", source_id),
                    url=item.get("url", ""), title=item.get("title", ""),
                    published_at=item.get("published_at"),
                    text=item.get("text"),
                ):
                    n += 1
    return n
