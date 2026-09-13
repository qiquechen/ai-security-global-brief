"""H2 采集入口；供命令行及 H1 的 scripts/run_crawl.py 调用。"""
from __future__ import annotations

import argparse
import json
import logging
import sys
import time
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from contextlib import closing
from datetime import UTC, datetime, timedelta
from pathlib import Path

from .. import config, db
from ..audit import operation, event
from dataclasses import asdict
from .crawler import (
    FetchError,
    Fetcher,
    RequestCoordinator,
    canonicalize_url,
    crawl_source,
    load_sources,
    parse_datetime,
)
from .lnc import Crawl4AIBackend
from .models import CrawlStats, Source

logger = logging.getLogger("ingest.run")


def configure_logging(verbose: bool = False) -> None:
    config.LOG_DIR.mkdir(parents=True, exist_ok=True)
    log_path = config.LOG_DIR / f"ingest-{datetime.now(UTC):%Y%m%d}.log"
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
        handlers=[logging.StreamHandler(sys.stdout), logging.FileHandler(log_path, encoding="utf-8")],
        force=True,
    )
    if not verbose:
        logging.getLogger("urllib3").setLevel(logging.WARNING)


class KnownURLs(set):
    """Keep rejection membership separate from ordinary duplicates."""
    def __init__(self):
        super().__init__()
        self.rejected = set()
        self.published_dates: dict[str, str] = {}


def _existing_urls_by_source(source_ids: set[str]) -> dict[str, set[str]]:
    """一次查询加载所有来源的已入库 URL，避免每个来源重复打开数据库。"""
    result: dict[str, set[str]] = defaultdict(KnownURLs)
    rejected = db.active_rejections()
    for source_id in source_ids:
        result[source_id].update(rejected)
        result[source_id].rejected.update(rejected)
    if not source_ids:
        return result
    placeholders = ",".join("?" for _ in source_ids)
    with closing(db.connect()) as conn:
        rows = conn.execute(
            f"SELECT source_id, url FROM articles WHERE source_id IN ({placeholders})",
            tuple(sorted(source_ids)),
        ).fetchall()
    for row in rows:
        result[row["source_id"]].add(canonicalize_url(row["url"]))
    for row in db.load_crawl_observations(source_ids):
        result[row["source_id"]].published_dates[
            canonicalize_url(row["url"])
        ] = row["published_at"]
    return result


def _crawl_one(
    source: Source,
    coordinator: RequestCoordinator,
    start: datetime,
    end: datetime,
    existing_urls: set[str],
    max_items: int | None,
    native_crawler=None,
) -> tuple[CrawlStats, list]:
    # requests.Session 不保证线程安全，因此每个任务独立会话，只共享限速状态。
    started = time.monotonic()
    with Fetcher(coordinator) as fetcher:
        stats, articles = crawl_source(
            source, fetcher, start, end, existing_urls, max_items=max_items,
            native_crawler=native_crawler,
        )
    stats.elapsed_seconds = time.monotonic() - started
    return stats, articles


def crawl_sources(
    sources: list[Source],
    start: datetime,
    end: datetime,
    existing_by_source: dict[str, set[str]],
    *,
    max_items: int | None = None,
    workers: int = 1,
    coordinator: RequestCoordinator | None = None,
    native_crawler=None,
) -> list[tuple[CrawlStats, list]]:
    """并行抓取不同来源；共享按域名限速，结果由调用方串行入库。"""
    coordinator = coordinator or RequestCoordinator()
    worker_count = max(1, min(workers, len(sources)))
    if worker_count == 1:
        return [
            _crawl_one(
                source, coordinator, start, end,
                existing_by_source.get(source.id, set()), max_items, native_crawler,
            )
            for source in sources
        ]
    results: list[tuple[CrawlStats, list]] = []
    with ThreadPoolExecutor(max_workers=worker_count, thread_name_prefix="ingest") as pool:
        futures = {
            pool.submit(
                _crawl_one, source, coordinator, start, end,
                existing_by_source.get(source.id, set()), max_items, native_crawler,
            ): source
            for source in sources
        }
        for future in as_completed(futures):
            source = futures[future]
            try:
                results.append(future.result())
            except Exception as exc:  # noqa: BLE001
                logger.exception("source=%s 未处理异常", source.id)
                stats = CrawlStats(source.id)
                stats.errors.append(f"来源任务异常：{exc}")
                results.append((stats, []))
    return results


def _export_jsonl(path: Path, start: datetime, end: datetime) -> int:
    with closing(db.connect()) as conn:
        rows = conn.execute(
            """
            SELECT id, source_id, url, title, published_at, crawled_at, text, dedup_hash
            FROM articles
            WHERE published_at >= ? AND published_at < ?
            ORDER BY published_at DESC, source_id ASC
            """,
            (start.replace(microsecond=0).isoformat(), end.replace(microsecond=0).isoformat()),
        ).fetchall()
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="\n") as handle:
        for row in rows:
            handle.write(json.dumps(dict(row), ensure_ascii=False) + "\n")
    return len(rows)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="H2 境外 AI 安全治理信息采集")
    parser.add_argument("--days", type=float, default=2.0, help="采集最近 N 天，默认2天")
    parser.add_argument("--source", action="append", help="只抓指定来源 id，可重复传入")
    parser.add_argument("--max-per-source", type=int, help="本次每来源最大候选数")
    parser.add_argument(
        "--workers", type=int, default=config.INGEST_WORKERS,
        help=f"不同来源并行数，默认 {config.INGEST_WORKERS}",
    )
    parser.add_argument("--export", type=Path, help="把本次时间窗内文章导出为 JSONL")
    parser.add_argument("--at", help="测试用当前时间，ISO 8601")
    parser.add_argument("--validate-only", action="store_true", help="只验证来源配置")
    parser.add_argument(
        "--disable-lnc", action="store_true",
        help="本次禁用 Crawl4AI 浏览器增强，仅使用静态抓取",
    )
    parser.add_argument(
        "--check-connectivity", action="store_true",
        help="验证来源配置并只检测主备网络线路",
    )
    parser.add_argument("--verbose", action="store_true")
    return parser


def _main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    configure_logging(args.verbose)
    if args.days <= 0:
        logger.error("--days 必须大于0")
        return 2
    if args.max_per_source is not None and args.max_per_source <= 0:
        logger.error("--max-per-source 必须大于0")
        return 2
    if args.workers <= 0:
        logger.error("--workers 必须大于0")
        return 2
    sources_path = config.ROOT / "config" / "sources.json"
    try:
        sources = load_sources(sources_path)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        logger.error("来源配置无效：%s", exc)
        return 2
    selected_ids = set(args.source or [])
    known_ids = {source.id for source in sources}
    unknown = sorted(selected_ids - known_ids)
    if unknown:
        logger.error("未知来源 id：%s", ", ".join(unknown))
        return 2
    selected = [
        source for source in sources
        if source.enabled and (not selected_ids or source.id in selected_ids)
    ]
    logger.info(
        "来源配置有效：共%d项，启用%d项，本次%d项",
        len(sources), sum(source.enabled for source in sources), len(selected),
    )
    if args.validate_only and not args.check_connectivity:
        return 0
    if not selected:
        logger.error("没有可执行的启用来源")
        return 2
    now = parse_datetime(args.at) if args.at else datetime.now(UTC)
    if now is None:
        logger.error("--at 不是有效时间：%s", args.at)
        return 2
    end = now.astimezone(UTC)
    start = end - timedelta(days=args.days)
    logger.info("采集窗口 UTC：[%s, %s)", start.isoformat(), end.isoformat())
    db.init_db()
    all_stats = []
    coordinator = RequestCoordinator()
    with Fetcher(coordinator) as fetcher:
        if config.INGEST_CONNECTIVITY_CHECK or args.check_connectivity:
            try:
                fetcher.check_connectivity()
            except FetchError as exc:
                logger.error("网络连通性检测失败：%s", exc)
                return 1
        if args.check_connectivity:
            return 0
    started = time.monotonic()
    existing_by_source = _existing_urls_by_source({source.id for source in selected})
    logger.info("开始并行采集：workers=%d", min(args.workers, len(selected)))
    native_crawler = None
    lnc_sources = [source.id for source in selected if source.lnc_mode != "off"]
    if config.INGEST_LNC_ENABLED and not args.disable_lnc and lnc_sources:
        try:
            native_crawler = Crawl4AIBackend()
            logger.info("Crawl4AI 增强已启用：%s", ", ".join(lnc_sources))
        except Exception as exc:  # noqa: BLE001
            logger.warning("Crawl4AI 初始化失败，降级为静态采集：%s", exc)
    try:
        crawled = crawl_sources(
            selected, start, end, existing_by_source,
            max_items=args.max_per_source, workers=args.workers, coordinator=coordinator,
            native_crawler=native_crawler,
        )
    finally:
        if native_crawler is not None:
            native_crawler.close()
    for stats, articles in crawled:
        with closing(db.connect()) as conn:
            with conn:
                for article in articles:
                    inserted = db.insert_article(
                        conn,
                        source_id=article.source_id,
                        url=article.url,
                        title=article.title,
                        published_at=article.published_at,
                        crawled_at=article.crawled_at,
                        text=article.text,
                        dedup_hash=article.dedup_hash,
                    )
                    if inserted:
                        stats.inserted += 1
                    else:
                        stats.existing += 1
                db.upsert_crawl_observations(
                    conn, stats.source_id, stats.observed_dates,
                )
        all_stats.append(stats)
        stats_payload = asdict(stats)
        stats_payload.pop("observed_dates", None)
        event("crawl.source", stats.status, **stats_payload,
              articles=[{"url": a.url, "title": a.title} for a in articles])
        logger.info(
            "source=%s status=%s discovered=%d accepted=%d inserted=%d existing=%d "
            "outside=%d missing_time=%d empty=%d errors=%d duration=%.1fs "
            "raw=%d url_filtered=%d hint_outside=%d blacklisted=%d "
            "eligible=%d fetched=%d cached_outside=%d "
            "lnc=%d/%d recovered=%d",
            stats.source_id, stats.status, stats.discovered, stats.accepted,
            stats.inserted, stats.existing, stats.outside_window,
            stats.missing_time, stats.empty_text, len(stats.errors), stats.elapsed_seconds,
            stats.raw_discovered, stats.url_filtered, stats.hint_outside,
            stats.blacklisted, stats.eligible, stats.fetched, stats.cached_outside,
            stats.lnc_succeeded, stats.lnc_attempted, stats.lnc_recovered,
        )
        for message in stats.errors[:10]:
            logger.warning("source=%s %s", stats.source_id, message)
    if args.export:
        output = args.export if args.export.is_absolute() else config.ROOT / args.export
        count = _export_jsonl(output, start, end)
        logger.info("已导出%d条：%s", count, output)
    inserted = sum(item.inserted for item in all_stats)
    errors = sum(len(item.errors) for item in all_stats)
    elapsed = time.monotonic() - started
    url_filtered = sum(s.url_filtered for s in all_stats)
    hint_outside = sum(s.hint_outside for s in all_stats)
    blacklisted = sum(s.blacklisted for s in all_stats)
    event("crawl.result", "completed", inserted=inserted, errors=errors,
          elapsed_seconds=elapsed, blacklisted=blacklisted,
          url_filtered=url_filtered, hint_outside=hint_outside,
          existing=sum(s.existing for s in all_stats),
          accepted=sum(s.accepted for s in all_stats),
          lnc_attempted=sum(s.lnc_attempted for s in all_stats),
          lnc_succeeded=sum(s.lnc_succeeded for s in all_stats),
          lnc_recovered=sum(s.lnc_recovered for s in all_stats),
          window_start=start.isoformat(), window_end=end.isoformat())
    logger.info(
        "采集完成：新增=%d 错误=%d url_filtered=%d hint_outside=%d "
        "blacklisted=%d 耗时=%.1f秒",
        inserted, errors, url_filtered, hint_outside, blacklisted, elapsed,
    )
    return 0 if any(item.status != "failed" for item in all_stats) else 1


def main(argv=None):
    with operation("crawl") as log:
        code = _main(argv)
        log["exit_code"] = code
        log["outcome"] = "success" if code == 0 else "failed"
        return code


if __name__ == "__main__":
    raise SystemExit(main())
