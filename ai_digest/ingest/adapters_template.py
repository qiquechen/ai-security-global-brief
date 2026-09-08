"""H2 采集适配器模板。

目标：把境外源（RSS/sitemap/新闻列表页）的新文章抓下来，
正文抽取清洗后写入 SQLite（C2 契约）。写入统一走 db.insert_article()。

这份文件是"模板/脚手架"，你需要：
1. 把 TODO 处补成真实实现；
2. 逐个核对 config/sources.json 里每个源的 method / 入口 URL；
3. 运行 python scripts/dev_ingest.py（你自己写）做单源联调。

注意：
- 本机访问境外源需要代理：读取 config.PROXY（.env 里填，如 http://127.0.0.1:10809）。
- 遵守站点 robots / 频率，别高并发硬爬。
- 入库前先调用 db.init_db() 建表。
"""
from __future__ import annotations

import hashlib
import logging
import re
import time
from typing import Any, Iterable

import requests

from .. import config, db

logger = logging.getLogger("ingest")

UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"


def http_get(url: str, timeout: int = 30) -> requests.Response:
    """带代理与 UA 的 GET。代理在 .env 配 PROXY；为空则直连。"""
    proxies = None
    if config.PROXY:
        proxies = {"http": config.PROXY, "https": config.PROXY}
    resp = requests.get(url, headers={"User-Agent": UA}, proxies=proxies,
                        timeout=timeout)
    resp.raise_for_status()
    return resp


def digest(url: str, title: str) -> str:
    """URL+标题 哈希，用于去重。"""
    return hashlib.sha256(f"{url}|{title}".encode()).hexdigest()


# ---------------- 三类抓取示例 ----------------

def fetch_rss(feed_url: str) -> Iterable[dict[str, Any]]:
    """RSS 源：用 feedparser 解析，产出候选文章 dict。"""
    import feedparser  # 延迟导入，避免缺包导致整个 ingest 不可用
    feed = feedparser.parse(feed_url)
    for e in feed.entries:
        yield {
            "url": e.get("link", ""),
            "title": e.get("title", ""),
            "published_at": _norm_date(e.get("published", e.get("updated", ""))),
            "text": "",  # RSS 的 summary 通常不完整，需再抓正文
        }


def fetch_sitemap(sitemap_url: str, url_contains: str | None = None) -> Iterable[dict[str, Any]]:
    """sitemap 源：提取 <loc> 列表（TODO：处理嵌套 sitemap / lastmod）。"""
    resp = http_get(sitemap_url)
    urls = re.findall(r"<loc>\s*(.*?)\s*</loc>", resp.text, flags=re.S)
    for u in urls:
        if url_contains and url_contains not in u:
            continue
        yield {"url": u, "title": "", "published_at": "", "text": ""}


def fetch_page_links(homepage: str, link_pattern: str | None = None) -> Iterable[str]:
    """页面源：解析新闻列表页里的文章链接（TODO：按各站结构调整选择器）。"""
    from bs4 import BeautifulSoup
    resp = http_get(homepage)
    soup = BeautifulSoup(resp.text, "html.parser")
    seen = set()
    for a in soup.find_all("a", href=True):
        href = a["href"]
        if not href.startswith("http"):
            href = requests.compat.urljoin(homepage, href)
        if link_pattern and not re.search(link_pattern, href):
            continue
        if href not in seen:
            seen.add(href)
            yield href


def extract_body(url: str) -> str:
    """正文抽取：trafilatura。失败返回空串由调用方跳过。"""
    try:
        import trafilatura
        downloaded = trafilatura.fetch_url(url)
        if not downloaded:
            return ""
        return trafilatura.extract(downloaded, include_comments=False,
                                   include_tables=False) or ""
    except Exception as exc:  # noqa: BLE001
        logger.warning("正文抽取失败 %s: %s", url, exc)
        return ""


def _norm_date(s: str) -> str:
    """把各种日期字符串规整为 YYYY-MM-DD 或 ISO。TODO：用 dateparser 覆盖更多格式。"""
    if not s:
        return ""
    import dateparser
    dt = dateparser.parse(s)
    return dt.strftime("%Y-%m-%d") if dt else s[:10]


# ---------------- 入库管线骨架 ----------------

def store_item(source_id: str, item: dict[str, Any]) -> bool:
    """单条入库：缺正文时尝试抓正文。返回是否新插入。"""
    url, title = item["url"], item.get("title") or url
    text = item.get("text") or ""
    if not text:
        text = extract_body(url)
    if not text and not item.get("force_keep"):
        logger.debug("无正文，跳过：%s", url)
        return False
    with db.connect() as conn:
        return db.insert_article(
            conn, source_id=source_id, url=url, title=title,
            published_at=item.get("published_at"),
            text=text, dedup_hash=digest(url, title),
        )


def run_source(source: dict[str, Any], max_items: int = 50) -> tuple[int, int]:
    """按 source 的 method 抓取并入库，返回 (抓取数, 新入库数)。"""
    added = fetched = 0
    method = source.get("method", "page")
    if method == "rss" and source.get("feed"):
        cands = list(fetch_rss(source["feed"]))
    elif method == "sitemap" and source.get("feed"):
        cands = list(fetch_sitemap(source["feed"]))
    else:
        links = list(fetch_page_links(source.get("homepage", "")))
        cands = [{"url": u, "title": "", "published_at": "", "text": ""} for u in links]

    for item in cands[:max_items]:
        fetched += 1
        try:
            if store_item(source["id"], item):
                added += 1
        except Exception as exc:  # noqa: BLE001
            logger.warning("入库失败 %s: %s", item.get("url"), exc)
        time.sleep(0.3)  # 基本限速

    logger.info("%s：抓取 %d 条，新入库 %d 条", source.get("id"), fetched, added)
    return fetched, added


def run_all(max_per_source: int = 50) -> None:
    """遍历 config/sources.json 中 enabled 的源。"""
    import json
    db.init_db()
    path = config.ROOT / "config" / "sources.json"
    sources = json.loads(path.read_text(encoding="utf-8"))["sources"]
    for s in sources:
        if not s.get("enabled", True):
            continue
        try:
            run_source(s, max_items=max_per_source)
        except Exception as exc:  # noqa: BLE001
            logger.error("源 %s 失败：%s", s.get("id"), exc)
