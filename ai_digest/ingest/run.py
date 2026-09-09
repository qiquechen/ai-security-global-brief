"""H2 采集入口；供命令行及 H1 的 scripts/run_crawl.py 调用。"""
from __future__ import annotations

import argparse
import json
import logging
import sys
from contextlib import closing
from datetime import UTC, datetime, timedelta
from pathlib import Path

from .. import config, db
from .crawler import FetchError, Fetcher, canonicalize_url, crawl_source, load_sources, parse_datetime

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


def _existing_urls(source_id: str) -> set[str]:
    with closing(db.connect()) as conn:
        rows = conn.execute("SELECT url FROM articles WHERE source_id = ?", (source_id,)).fetchall()
    return {canonicalize_url(row["url"]) for row in rows}


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
    parser.add_argument("--export", type=Path, help="把本次时间窗内文章导出为 JSONL")
    parser.add_argument("--at", help="测试用当前时间，ISO 8601")
    parser.add_argument("--validate-only", action="store_true", help="只验证来源配置")
    parser.add_argument(
        "--check-connectivity", action="store_true",
        help="验证来源配置并只检测主备网络线路",
    )
    parser.add_argument("--verbose", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    configure_logging(args.verbose)
    if args.days <= 0:
        logger.error("--days 必须大于0")
        return 2
    if args.max_per_source is not None and args.max_per_source <= 0:
        logger.error("--max-per-source 必须大于0")
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
    with Fetcher() as fetcher:
        if config.INGEST_CONNECTIVITY_CHECK or args.check_connectivity:
            try:
                fetcher.check_connectivity()
            except FetchError as exc:
                logger.error("网络连通性检测失败：%s", exc)
                return 1
        if args.check_connectivity:
            return 0
        for source in selected:
            existing = _existing_urls(source.id)
            stats, articles = crawl_source(
                source, fetcher, start, end, existing, max_items=args.max_per_source
            )
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
            all_stats.append(stats)
            logger.info(
                "source=%s status=%s discovered=%d accepted=%d inserted=%d existing=%d "
                "outside=%d missing_time=%d empty=%d errors=%d",
                stats.source_id, stats.status, stats.discovered, stats.accepted,
                stats.inserted, stats.existing, stats.outside_window,
                stats.missing_time, stats.empty_text, len(stats.errors),
            )
            for message in stats.errors[:10]:
                logger.warning("source=%s %s", stats.source_id, message)
    if args.export:
        output = args.export if args.export.is_absolute() else config.ROOT / args.export
        count = _export_jsonl(output, start, end)
        logger.info("已导出%d条：%s", count, output)
    inserted = sum(item.inserted for item in all_stats)
    errors = sum(len(item.errors) for item in all_stats)
    logger.info("采集完成：新增=%d 错误=%d", inserted, errors)
    return 0 if any(item.status != "failed" for item in all_stats) else 1


if __name__ == "__main__":
    raise SystemExit(main())
