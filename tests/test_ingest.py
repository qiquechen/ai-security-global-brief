import json
import tempfile
import threading
import unittest
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import patch
from zipfile import ZipFile

import requests

from ai_digest import config, db
from ai_digest.ingest.crawler import (
    Fetcher,
    _extract_article,
    canonicalize_url,
    crawl_source,
    load_sources,
    parse_datetime,
    url_allowed,
)
from ai_digest.ingest.models import Candidate, CrawlStats
from ai_digest.ingest.run import crawl_sources
from ai_digest.report.docx_builder import (
    build_report_bundle,
    collection_group,
    original_filename,
)
from ai_digest.report.pipeline import run_daily_pipeline
from ai_digest.report.rank import rank_items
from ai_digest.summarize.run import summarize_article
from scripts.run_daily import daily_window


class IngestTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        patcher = patch.object(config, "DB_PATH", Path(directory.name) / "test.db")
        patcher.start()
        self.addCleanup(patcher.stop)
        for key in ("DATA_DIR", "LOG_DIR"):
            isolated = patch.object(config, key, Path(directory.name))
            isolated.start()
            self.addCleanup(isolated.stop)

    def test_fused_source_config(self):
        sources = load_sources(config.ROOT / "config" / "sources.json")
        # 只做下限/包含断言，避免每次增源都要同步改测试
        self.assertGreaterEqual(len(sources), 67)
        self.assertGreaterEqual(sum(source.enabled for source in sources), 40)
        self.assertGreaterEqual(sum(source.type == "media" for source in sources), 17)
        self.assertTrue(
            {"ars_ai", "bbc_tech", "cbs_tech", "scmp_ai", "japan_times_ai", "independent_ai"}
            <= {source.id for source in sources if source.enabled}
        )
        aisi = next(source for source in sources if source.id == "aisi_uk")
        self.assertTrue(url_allowed(aisi, "https://www.aisi.gov.uk/research/example"))
        self.assertFalse(url_allowed(aisi, "https://example.com/research/example"))
        bbc = next(source for source in sources if source.id == "bbc_tech")
        self.assertTrue(url_allowed(
            bbc,
            "https://www.bbc.co.uk/news/articles/example?at_medium=RSS&at_campaign=rss",
        ))

    def test_anthropic_header_date_overrides_guessed_historical_date(self):
        response = requests.Response()
        response.url = "https://www.anthropic.com/research/example"
        response.status_code = 200
        response.encoding = "utf-8"
        response._content = b'<article><div class="PostDetail-module__header"><div class="body-3 agate">Sep 4, 2026</div></div><p>Earlier research in 2023.</p></article>'
        from types import SimpleNamespace
        with patch("ai_digest.ingest.crawler.trafilatura.extract_metadata", return_value=SimpleNamespace(
            title="Example", date="2023-11-03", url=response.url
        )):
            _, _, published, _ = _extract_article(response, Candidate(response.url))
        self.assertEqual(datetime(2026, 9, 4, tzinfo=UTC), published)

    def test_url_and_time_normalization(self):
        self.assertEqual(
            "https://example.org/a?x=1",
            canonicalize_url("HTTPS://EXAMPLE.ORG//a/?utm_source=test&x=1#part"),
        )
        self.assertEqual(
            datetime(2026, 9, 7, 22, 0, tzinfo=UTC),
            parse_datetime("2026-09-08T06:00:00+08:00"),
        )

    def test_h1_database_contract_and_duplicate_guard(self):
        original = config.DB_PATH
        try:
            with tempfile.TemporaryDirectory() as directory:
                config.DB_PATH = Path(directory) / "app.db"
                db.init_db()
                conn = db.connect()
                try:
                    first = db.insert_article(
                        conn,
                        source_id="aisi_uk",
                        url="https://www.aisi.gov.uk/research/example",
                        title="Example AI safety evaluation",
                        published_at="2026-09-08T00:00:00+00:00",
                        text="body " * 100,
                        dedup_hash="hash",
                    )
                    second = db.insert_article(
                        conn,
                        source_id="aisi_uk",
                        url="https://www.aisi.gov.uk/research/example",
                        title="Example AI safety evaluation",
                        published_at="2026-09-08T00:00:00+00:00",
                        text="body " * 100,
                        dedup_hash="hash",
                    )
                    same_content_at_new_url = db.insert_article(
                        conn,
                        source_id="another_source",
                        url="https://example.org/copied-story",
                        title="Copied story",
                        published_at="2026-09-08T01:00:00+00:00",
                        text="body " * 100,
                        dedup_hash="hash",
                    )
                    row = conn.execute("SELECT * FROM articles").fetchone()
                    conn.commit()
                finally:
                    conn.close()
                loaded = db.load_articles_between(
                    datetime(2026, 9, 7, tzinfo=UTC),
                    datetime(2026, 9, 9, tzinfo=UTC),
                )
                self.assertTrue(first)
                self.assertFalse(second)
                self.assertFalse(same_content_at_new_url)
                self.assertEqual("aisi_uk", row["source_id"])
                self.assertGreater(len(row["text"]), 180)
                self.assertEqual("gov", loaded[0]["source_type"])
                self.assertGreaterEqual(loaded[0]["source_priority"], 1)
        finally:
            config.DB_PATH = original

    def test_candidate_priority_skips_existing_before_limit(self):
        source = next(
            source for source in load_sources(config.ROOT / "config" / "sources.json")
            if source.id == "ars_ai"
        )
        existing = "https://arstechnica.com/ai/2026/09/existing"
        relevant = "https://arstechnica.com/security/2026/09/ai-safety-law"
        irrelevant = "https://arstechnica.com/ai/2026/09/new-phone"
        candidates = [
            Candidate(existing, "AI safety", relevance_score=20),
            Candidate(irrelevant, "New phone", relevance_score=1),
            Candidate(relevant, "AI safety regulation", relevance_score=10),
        ]

        class FakeFetcher:
            def __init__(self):
                self.fetched = []

            def fetch(self, _source, url):
                self.fetched.append(url)
                return type("Response", (), {
                    "headers": {"content-type": "text/html"}, "url": url, "text": "",
                })()

        fetcher = FakeFetcher()
        published = datetime(2026, 9, 8, 1, tzinfo=UTC)
        with patch("ai_digest.ingest.crawler.discover", return_value=candidates), patch(
            "ai_digest.ingest.crawler._extract_article",
            return_value=("AI safety regulation", "body " * 100, published, relevant),
        ):
            stats, articles = crawl_source(
                source, fetcher, datetime(2026, 9, 8, tzinfo=UTC),
                datetime(2026, 9, 9, tzinfo=UTC), {existing}, max_items=1,
            )
        self.assertEqual([relevant], fetcher.fetched)
        self.assertEqual(1, stats.existing)
        self.assertEqual(1, len(articles))

    def test_article_published_time_beats_modified_and_feed_time(self):
        html = """
        <html><head>
          <meta property="og:title" content="AI safety policy">
          <meta property="article:modified_time" content="2026-09-09T03:00:00Z">
          <script type="application/ld+json">
          {"@type":"NewsArticle","headline":"AI safety policy",
           "datePublished":"2026-09-07T02:00:00Z","dateModified":"2026-09-09T03:00:00Z"}
          </script>
        </head><body><article>Article body about AI safety policy.</article></body></html>
        """
        response = type("Response", (), {
            "text": html, "url": "https://example.com/story",
        })()
        _title, _text, published, _canonical = _extract_article(
            response,
            Candidate("https://example.com/story", published_hint=datetime(2026, 9, 8, tzinfo=UTC)),
        )
        self.assertEqual(datetime(2026, 9, 7, 2, tzinfo=UTC), published)

    def test_sources_can_crawl_concurrently(self):
        sources = load_sources(config.ROOT / "config" / "sources.json")[:2]
        barrier = threading.Barrier(2, timeout=2)

        def fake_crawl(source, *_args):
            barrier.wait()
            return CrawlStats(source.id), []

        with patch("ai_digest.ingest.run._crawl_one", side_effect=fake_crawl):
            results = crawl_sources(
                sources, datetime(2026, 9, 8, tzinfo=UTC),
                datetime(2026, 9, 9, tzinfo=UTC), {}, workers=2,
            )
        self.assertEqual({source.id for source in sources}, {item[0].source_id for item in results})

    def test_source_json_remains_h1_compatible(self):
        raw = json.loads((config.ROOT / "config" / "sources.json").read_text(encoding="utf-8"))
        required = {"id", "name", "country", "type", "method", "homepage", "feed", "enabled"}
        self.assertTrue(all(required <= set(item) for item in raw["sources"]))

    def test_summary_contract_has_chinese_title_and_plain_body(self):
        class FakeClient:
            def chat_json(self, _system, _user, **kwargs):
                if "事实核对员" in _system:
                    return {"complete": True, "faithful": True, "issues": []}
                return {"zh_title": "人工智能安全评估", "summary": "机构发布了安全评估结果。"}

        item = summarize_article(FakeClient(), {"title": "AI safety evaluation"})
        self.assertEqual("人工智能安全评估", item["zh_title"])
        self.assertNotIn("**", item["summary"])

    def test_source_type_controls_two_report_groups(self):
        self.assertEqual("media", collection_group({"source_type": "media"}))
        self.assertEqual("institution", collection_group({"source_type": "thinktank"}))
        self.assertEqual("institution", collection_group({"source_type": "gov"}))

    def test_original_filename_is_windows_safe(self):
        name = original_filename(
            3,
            {"source_name": "机构/A", "zh_title": '标题：含*非法?字符"'},
        )
        self.assertTrue(name.startswith("003_机构_A_"))
        self.assertTrue(name.endswith(".docx"))
        self.assertFalse(any(char in name for char in '<>:"/\\|?*'))

    def test_rank_uses_source_priority_inside_importance(self):
        items = [
            {"url": "https://example/a", "importance": "med", "source_priority": 50,
             "published_at": "2026-09-09T02:00:00+00:00"},
            {"url": "https://example/b", "importance": "med", "source_priority": 90,
             "published_at": "2026-09-08T02:00:00+00:00"},
        ]
        self.assertEqual("https://example/b", rank_items(items)[0]["url"])

    def test_bundle_creates_two_collections_originals_and_hyperlinks(self):
        items = [
            {
                "source_type": "media", "source_name": "测试媒体", "title": "Media title",
                "zh_title": "媒体标题", "summary": "媒体摘要。", "text": "Original media body.",
                "published_at": "2026-09-09T01:00:00+00:00", "url": "https://example.com/media",
                "category": "M1", "importance": "high",
            },
            {
                "source_type": "gov", "source_name": "测试机构", "title": "Agency title",
                "zh_title": "机构标题", "summary": "机构摘要。", "text": "Original agency body.",
                "published_at": "2026-09-09T02:00:00+00:00", "url": "https://example.com/agency",
                "category": "M3", "importance": "med",
            },
        ]
        with tempfile.TemporaryDirectory() as directory:
            bundle = build_report_bundle(
                items, directory, date_text="2026年09月09日", date_stamp="20260909",
                window_text="2026-09-08 06:00 至 2026-09-09 06:00（Asia/Shanghai）",
            )
            paths = [bundle["media"]["collection"], bundle["institution"]["collection"]]
            paths += bundle["media"]["originals"] + bundle["institution"]["originals"]
            self.assertEqual(4, len(paths))
            self.assertTrue(all(path.exists() for path in paths))
            for path in paths:
                with ZipFile(path) as archive:
                    relationships = archive.read("word/_rels/document.xml.rels").decode("utf-8")
                    self.assertIn("https://example.com/", relationships)

    def test_pipeline_sends_all_outputs_once(self):
        class FakeClient:
            def chat_json(self, system, _user, **kwargs):
                if "事实核对员" in system:
                    return {"complete": True, "faithful": True, "issues": []}
                if "筛选官" in system:
                    return {"in_scope": True, "category": "M1", "importance": "high", "reason": "测试"}
                return {"zh_title": "中文标题", "summary": "客观摘要。"}

        items = [
            {"source_type": "media", "source_name": "媒体", "title": "One", "text": "body",
             "published_at": "2026-09-09T01:00:00+00:00", "url": "https://example.com/one"},
            {"source_type": "gov", "source_name": "机构", "title": "Two", "text": "body",
             "published_at": "2026-09-09T02:00:00+00:00", "url": "https://example.com/two"},
        ]
        cases = [
            # 开关、阈值、是否发信、是否有原文、预期压缩。
            (False, 10, True, True, False),
            (False, 2, True, True, False),  # 等于阈值不强制压缩。
            (False, 1, True, True, True),  # 两类原文合并计数。
            (True, 10, True, True, True),
            (False, 0, True, True, True),
            (True, 0, True, False, False),
            (True, 0, False, True, True),
        ]
        for enabled, threshold, send, has_originals, zipped in cases:
            with self.subTest(case=(enabled, threshold, send, has_originals)), \
                    tempfile.TemporaryDirectory() as directory, \
                    patch("ai_digest.deliver.prepared.send_email") as mocked_send, \
                    patch.object(config, "MAIL_ORIGINALS_ZIP", enabled), \
                    patch.object(config, "MAIL_ORIGINALS_ZIP_THRESHOLD", threshold):
                stale_path = Path(directory) / "历史原文.docx"
                stale_path.write_bytes(b"old report")
                archive_path = Path(directory) / "原文_20260909.zip"
                if zipped:
                    with ZipFile(archive_path, "w") as archive:
                        archive.writestr("旧原文.docx", b"stale archive entry")
                stats = run_daily_pipeline(
                    FakeClient(), items if has_originals else [], directory,
                    send=send, date_stamp="20260909", minimum_per_group=0,
                )
                self.assertEqual(int(has_originals), stats["media"])
                self.assertEqual(int(has_originals), stats["institution"])
                batch_dir = Path(stats["manifest"]).parent
                archive_path = batch_dir / "原文_20260909.zip"
                self.assertEqual(zipped, archive_path.exists())
                if not send:
                    mocked_send.assert_not_called()
                    continue
                mocked_send.assert_called_once()
                attachments = mocked_send.call_args.kwargs["attachment_paths"]
                self.assertEqual(
                    2 + (1 if zipped else 2 if has_originals else 0), len(attachments)
                )
                self.assertEqual(stats["collections"], [str(p) for p in attachments[:2]])
                self.assertTrue(all(p.is_file() for p in attachments))
                if zipped:
                    self.assertEqual(archive_path, attachments[-1])
                    self.assertIn("原文压缩包", mocked_send.call_args.kwargs["text_body"])
                    originals = [
                        p for p in batch_dir.rglob("*.docx")
                        if str(p) not in stats["collections"] and p != stale_path
                    ]
                    with ZipFile(archive_path) as archive:
                        self.assertEqual(
                            {p.relative_to(batch_dir).as_posix() for p in originals},
                            set(archive.namelist()),
                        )
                        self.assertIsNone(archive.testzip())
                        for original in originals:
                            self.assertEqual(
                                original.read_bytes(),
                                archive.read(original.relative_to(batch_dir).as_posix()),
                            )

    def test_fetcher_switches_to_backup_on_connection_failure(self):
        class FakeResponse:
            status_code = 204

        class FakeSession:
            def __init__(self):
                self.proxies = {}

            def get(self, _url, **_kwargs):
                if self.proxies.get("https") == "http://primary.test:1":
                    raise requests.ConnectionError("primary down")
                return FakeResponse()

            def close(self):
                pass

        with patch.object(config, "PROXY_PRIMARY", "http://primary.test:1"), patch.object(
            config, "PROXY_BACKUP", "http://backup.test:2"
        ):
            fetcher = Fetcher()
            fetcher.session = FakeSession()
            fetcher._apply_route(0)
            response = fetcher._get_with_failover("https://example.com", timeout=1)
            self.assertEqual(204, response.status_code)
            self.assertEqual("备用线路", fetcher.active_route_name)

    def test_daily_window_closes_at_six_for_seven_oclock_delivery(self):
        local = datetime.fromisoformat("2026-09-09T07:00:00+08:00")
        start, end = daily_window(local)
        self.assertEqual("2026-09-08T06:00:00+08:00", start.isoformat())
        self.assertEqual("2026-09-09T06:00:00+08:00", end.isoformat())
        self.assertEqual(7, config.DELIVERY_HOUR)


if __name__ == "__main__":
    unittest.main()
