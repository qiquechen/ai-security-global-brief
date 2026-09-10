import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import Mock, patch

from ai_digest.report.rank import rank_items
from ai_digest.report.selection import select_materials, InsufficientMaterials
from ai_digest.report.history import backfill_history


def article(group, index, importance="med", priority=50):
    return dict(url=f"https://example.com/{group}/{index}", source_type=group,
                importance=importance, source_priority=priority,
                published_at="2026-09-09T01:00:00+00:00")


def accept(_client, items):
    return [dict(in_scope=True, importance=x["importance"], category="M1", reason="test")
            for x in items]


class QuotaTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        p = patch("ai_digest.config.LOG_DIR", Path(temp.name))
        p.start()
        self.addCleanup(p.stop)
        p = patch("ai_digest.report.selection.classify_items", side_effect=accept)
        self.classify = p.start()
        self.addCleanup(p.stop)
        self.start = datetime(2026, 9, 9, 6, tzinfo=timezone.utc)

    def test_no_expansion_when_both_groups_sufficient(self):
        loader = Mock()
        items = [article(g, i) for g in ("media", "gov") for i in range(5)]
        picked, stats = select_materials(None, items, minimum=5, window_start=self.start,
                                        history_loader=loader)
        self.assertEqual(10, len(picked))
        self.assertEqual(0, stats["expansion_days"])
        loader.assert_not_called()

    def test_expand_only_missing_group_and_skip_duplicate_urls(self):
        initial = [article("media", i) for i in range(8)] + [article("gov", 0)]
        loader = Mock(side_effect=[
            [article("gov", 0), article("gov", 1), article("media", 9)],
            [article("gov", i) for i in range(2, 5)],
        ])
        picked, stats = select_materials(None, initial, minimum=5,
                                        window_start=self.start, history_loader=loader)
        self.assertEqual({"media": 8, "institution": 5}, stats["counts"])
        self.assertEqual(2, stats["expansion_days"])
        self.assertEqual(13, stats["candidates"])
        self.assertEqual(self.start-timedelta(days=2), stats["effective_start"])
        self.assertEqual((self.start-timedelta(days=1), self.start), loader.call_args_list[0].args)

    def test_empty_initial_window_backfills_real_history(self):
        values = [article(g, i) for g in ("media", "gov") for i in range(5)]
        loader = Mock(side_effect=[[], values])
        backfill = Mock(return_value={"exit_code": 0})
        picked, stats = select_materials(None, [], minimum=5, window_start=self.start,
                                        history_loader=loader, backfill=backfill)
        self.assertEqual(10, len(picked))
        self.assertEqual({"media", "institution"}, backfill.call_args.args[2])

    def test_exhaustion_raises_instead_of_shipping_short_batch(self):
        with self.assertRaisesRegex(InsufficientMaterials, "扩展2天"):
            select_materials(None, [], minimum=5, window_start=self.start,
                             history_loader=Mock(return_value=[]), max_days=2)

    def test_ranking_reserves_five_institutions_despite_media_priority(self):
        items = [article("media", i, "high") for i in range(25)]
        items += [article("gov", i, "low") for i in range(6)]
        ranked = rank_items(items, max_items=20, minimum_per_group=5)
        self.assertEqual(20, len(ranked))
        self.assertEqual(5, sum(x["source_type"] == "gov" for x in ranked))
        with self.assertRaises(ValueError):
            rank_items(items, max_items=9, minimum_per_group=5)

    def test_institution_weight_is_moderate_and_keeps_importance_order(self):
        with patch("ai_digest.config.REPORT_INSTITUTION_WEIGHT", 1.2):
            items = [article("media", 0, priority=55), article("gov", 0, priority=50),
                     article("media", 1, "high", 10)]
            self.assertEqual([items[2], items[1], items[0]], rank_items(items))

    def test_backfill_targets_only_missing_source_types_and_exact_day(self):
        with patch("ai_digest.ingest.run.main", return_value=0) as crawl:
            result = backfill_history(self.start-timedelta(days=1), self.start, {"institution"})
        args = crawl.call_args.args[0]
        self.assertEqual(["--days", "1.0", "--at", self.start.isoformat()], args[:4])
        self.assertGreater(result["sources"], 0)
        self.assertNotIn("bbc_tech", args)
        self.assertIn("aisi_uk", args)

    def test_pipeline_fails_before_summary_files_or_send(self):
        from ai_digest.report.pipeline import run_daily_pipeline
        with patch("ai_digest.report.pipeline.summarize_article") as summary, \
             patch("ai_digest.report.pipeline.build_report_bundle") as build, \
             patch("ai_digest.report.pipeline.send_prepared") as send:
            with self.assertRaises(InsufficientMaterials):
                run_daily_pipeline(None, [article("media", 0)], "unused", send=True)
            summary.assert_not_called()
            build.assert_not_called()
            send.assert_not_called()


if __name__ == "__main__":
    unittest.main()
