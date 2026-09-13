import tempfile
import unittest
from contextlib import closing
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import Mock, patch

from ai_digest import config, db
from ai_digest.filter.classify import classify_items
from ai_digest.ingest.run import _existing_urls_by_source


class RejectionTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        patcher = patch.object(config, "DB_PATH", Path(directory.name) / "test.db")
        patcher.start()
        self.addCleanup(patcher.stop)
        db.init_db()
        self.item = {"source_id": "sample", "url": "https://example.com/story", "title": "Article"}
        with closing(db.connect()) as conn, conn:
            db.insert_article(conn, **self.item)

    def test_rejection_deletes_article_and_skips_future_requests_and_inserts(self):
        client = Mock()
        client.chat_json.return_value = {"in_scope": False, "reason": "无收录价值"}
        classify_items(client, [self.item], sleep_between=0)
        with closing(db.connect()) as conn, conn:
            self.assertEqual(0, conn.execute("SELECT count(*) FROM articles").fetchone()[0])
            self.assertFalse(db.insert_article(conn, **self.item))
        classify_items(client, [self.item], sleep_between=0)
        client.chat_json.assert_called_once()
        exclusions = _existing_urls_by_source({"sample", "other"})
        self.assertIn(self.item["url"], exclusions["other"])
        variant = dict(self.item, url=self.item["url"] + "#section")
        classify_items(client, [variant], sleep_between=0)
        client.chat_json.assert_called_once()

    def test_errors_and_invalid_decisions_are_retryable(self):
        for result in (RuntimeError("timeout"), {}, {"in_scope": "false"}):
            client = Mock()
            if isinstance(result, Exception):
                client.chat_json.side_effect = result
            else:
                client.chat_json.return_value = result
            decision = classify_items(client, [self.item], sleep_between=0, retry_sleep=0)[0]
            self.assertTrue(decision["classification_error"])
            self.assertEqual(set(), db.active_rejections())
        with closing(db.connect()) as conn:
            self.assertEqual(1, conn.execute("SELECT count(*) FROM articles").fetchone()[0])

    def test_expiration_removes_only_old_records_and_allows_recrawl(self):
        db.reject_article(self.item, "不收录")
        now = datetime.now(UTC)
        with closing(db.connect()) as conn, conn:
            conn.execute("UPDATE rejected_articles SET rejected_at = ?",
                         ((now - timedelta(days=7)).isoformat(),))
            conn.execute("INSERT INTO rejected_articles VALUES (?, ?, ?)",
                         ("https://example.com/recent", "新记录", now.isoformat()))
        with patch.object(config, "REJECTION_RETENTION_DAYS", 7):
            self.assertEqual({"https://example.com/recent"}, db.active_rejections())
        with closing(db.connect()) as conn, conn:
            self.assertTrue(db.insert_article(conn, **self.item))

    def test_accepted_article_stays_in_database(self):
        client = Mock()
        client.chat_json.return_value = {"in_scope": True, "category": "M1", "importance": "high"}
        classify_items(client, [self.item], sleep_between=0)
        self.assertEqual(set(), db.active_rejections())
        with closing(db.connect()) as conn:
            self.assertEqual(1, conn.execute("SELECT count(*) FROM articles").fetchone()[0])
