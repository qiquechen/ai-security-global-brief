import json
import tempfile
import unittest
from datetime import UTC, datetime
from pathlib import Path

from ai_digest import config, db
from ai_digest.ingest.crawler import canonicalize_url, load_sources, parse_datetime, url_allowed
from ai_digest.summarize.run import summarize_article


class IngestTests(unittest.TestCase):
    def test_fused_source_config(self):
        sources = load_sources(config.ROOT / "config" / "sources.json")
        self.assertEqual(17, len(sources))
        self.assertEqual(15, sum(source.enabled for source in sources))
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
                self.assertTrue(first)
                self.assertFalse(second)
                self.assertEqual("aisi_uk", row["source_id"])
                self.assertGreater(len(row["text"]), 180)
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


if __name__ == "__main__":
    unittest.main()
