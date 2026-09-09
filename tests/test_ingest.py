import json
import tempfile
import unittest
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import patch
from zipfile import ZipFile

import requests

from ai_digest import config, db
from ai_digest.ingest.crawler import Fetcher, canonicalize_url, load_sources, parse_datetime, url_allowed
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
    def test_fused_source_config(self):
        sources = load_sources(config.ROOT / "config" / "sources.json")
        self.assertEqual(47, len(sources))
        self.assertEqual(43, sum(source.enabled for source in sources))
        self.assertEqual(7, sum(source.type == "media" for source in sources))
        aisi = next(source for source in sources if source.id == "aisi_uk")
        self.assertTrue(url_allowed(aisi, "https://www.aisi.gov.uk/research/example"))
        self.assertFalse(url_allowed(aisi, "https://example.com/research/example"))

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
                self.assertEqual("aisi_uk", row["source_id"])
                self.assertGreater(len(row["text"]), 180)
                self.assertEqual("gov", loaded[0]["source_type"])
                self.assertGreaterEqual(loaded[0]["source_priority"], 1)
        finally:
            config.DB_PATH = original

    def test_source_json_remains_h1_compatible(self):
        raw = json.loads((config.ROOT / "config" / "sources.json").read_text(encoding="utf-8"))
        required = {"id", "name", "country", "type", "method", "homepage", "feed", "enabled"}
        self.assertTrue(all(required <= set(item) for item in raw["sources"]))

    def test_summary_contract_has_chinese_title_and_plain_body(self):
        class FakeClient:
            def chat_json(self, _system, _user):
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
            def chat_json(self, system, _user):
                if "筛选官" in system:
                    return {"in_scope": True, "category": "M1", "importance": "high", "reason": "测试"}
                return {"zh_title": "中文标题", "summary": "客观摘要。"}

        items = [
            {"source_type": "media", "source_name": "媒体", "title": "One", "text": "body",
             "published_at": "2026-09-09T01:00:00+00:00", "url": "https://example.com/one"},
            {"source_type": "gov", "source_name": "机构", "title": "Two", "text": "body",
             "published_at": "2026-09-09T02:00:00+00:00", "url": "https://example.com/two"},
        ]
        with tempfile.TemporaryDirectory() as directory, patch(
            "ai_digest.report.pipeline.send_email"
        ) as mocked_send:
            stats = run_daily_pipeline(
                FakeClient(), items, directory, send=True, date_stamp="20260909"
            )
            mocked_send.assert_called_once()
            self.assertEqual(4, len(mocked_send.call_args.kwargs["attachment_paths"]))
            self.assertEqual(1, stats["media"])
            self.assertEqual(1, stats["institution"])

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
