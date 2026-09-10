import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from ai_digest.audit import operation
from ai_digest.deliver.prepared import prepare_mail, send_prepared
from ai_digest.summarize.run import summarize_article


class PreparationTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        for key in ("DATA_DIR", "LOG_DIR"):
            p = patch(f"ai_digest.config.{key}", self.root)
            p.start()
            self.addCleanup(p.stop)

    def client(self, results):
        client = Mock(chat_model="test-model")
        client.chat_json.side_effect = results
        return client

    def test_summary_cache_and_content_invalidation(self):
        client = self.client([{"summary": "完整事实。"}, {"complete": True, "faithful": True, "issues": []}, {"summary": "新事实。"}, {"complete": True, "faithful": True, "issues": []}])
        item = {"text": "source", "title": "title"}
        first = summarize_article(client, item)
        self.assertEqual(first, summarize_article(client, item))
        self.assertEqual(2, client.chat_json.call_count)
        self.assertEqual("新事实。", summarize_article(client, dict(item, text="changed"))["summary"])

    def test_failed_compression_never_truncates_or_caches(self):
        client = self.client([{"summary": "长" * 501}] * 2)
        with self.assertRaises(ValueError):
            summarize_article(client, {"text": "source"})
        self.assertEqual(2, client.chat_json.call_count)
        self.assertEqual("disabled", client.chat_json.call_args.kwargs["thinking_mode"])
        self.assertFalse(list(self.root.glob("summary_cache/*.json")))
        events = [json.loads(line) for path in self.root.glob("operations-*.jsonl")
                  for line in path.read_text(encoding="utf-8").splitlines()]
        self.assertEqual("failed", events[-1]["status"])
        self.assertEqual(1, events[-1]["retries"])

    def test_prepared_send_and_changed_or_missing_attachment(self):
        attachment = self.root / "test.docx"
        attachment.write_bytes(b"document")
        manifest = prepare_mail(self.root, "subject", "body", [attachment])
        with patch("ai_digest.deliver.prepared.send_email") as send:
            send_prepared(manifest)
            send.assert_called_once_with("subject", text_body="body", attachment_paths=[attachment])
            send.reset_mock()
            attachment.write_bytes(b"changed")
            with self.assertRaises(ValueError):
                send_prepared(manifest)
            attachment.unlink()
            with self.assertRaises(FileNotFoundError):
                send_prepared(manifest)
            send.assert_not_called()

    def test_blacklist_skip_count_is_distinct_from_duplicates(self):
        from datetime import datetime, timezone, timedelta
        from ai_digest import config
        from ai_digest.ingest.run import KnownURLs
        from ai_digest.ingest.crawler import crawl_source, load_sources
        from ai_digest.ingest.models import Candidate
        source = next(s for s in load_sources(config.ROOT / "config/sources.json") if s.id == "aisi_uk")
        rejected = "https://www.aisi.gov.uk/research/blocked"
        duplicate = "https://www.aisi.gov.uk/research/existing"
        known = KnownURLs()
        known.update([rejected, duplicate])
        known.rejected.add(rejected)
        now = datetime.now(timezone.utc)
        fetcher = Mock()
        with patch("ai_digest.ingest.crawler.discover", return_value=[
            Candidate(rejected), Candidate(rejected), Candidate(duplicate)
        ]):
            stats, articles = crawl_source(source, fetcher, now-timedelta(days=2), now, known)
        self.assertEqual(1, stats.blacklisted)
        self.assertEqual(1, stats.existing)
        self.assertEqual([], articles)
        fetcher.fetch.assert_not_called()

    def test_send_only_does_not_require_llm_key_or_load_candidates(self):
        from scripts import run_daily
        with patch("sys.argv", ["run_daily.py", "--send-prepared", "bundle.json"]), \
             patch.object(run_daily, "send_prepared") as send, \
             patch.object(run_daily.config, "has_deepseek_key", side_effect=AssertionError("LLM touched")):
            self.assertEqual(0, run_daily.main())
            send.assert_called_once_with(Path("bundle.json"))

    def test_nested_log_events_share_run_and_record_failure(self):
        with self.assertRaises(RuntimeError), operation("outer"):
            with operation("inner"):
                raise RuntimeError("test")
        events = [json.loads(line) for path in self.root.glob("operations-*.jsonl")
                  for line in path.read_text(encoding="utf-8").splitlines()]
        self.assertEqual(1, len({e["run_id"] for e in events}))
        self.assertEqual(["started", "started", "failed", "failed"], [e["status"] for e in events])
        self.assertGreaterEqual(events[-1]["elapsed_seconds"], 0)


if __name__ == "__main__":
    unittest.main()
