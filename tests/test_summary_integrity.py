import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from ai_digest.summarize.prompts import integrity_issues
from ai_digest.summarize.run import summarize_article

PASS = {"complete": True, "faithful": True, "issues": []}


class SummaryIntegrityTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        for name in ("DATA_DIR", "LOG_DIR"):
            p = patch(f"ai_digest.config.{name}", self.root)
            p.start()
            self.addCleanup(p.stop)

    def client(self, *results):
        return Mock(chat_model="test", chat_json=Mock(side_effect=results))

    def test_screenshot_fragment_rewritten_from_source(self):
        client = self.client({"summary": "一项研究发现低媒体自"},
                             {"summary": "研究发现模型可能隐瞒自身行为。"}, PASS)
        result = summarize_article(client, {"text": "Original evidence"})
        self.assertEqual("研究发现模型可能隐瞒自身行为。", result["summary"])
        self.assertIn("Original evidence", client.chat_json.call_args_list[1].args[1])

    def test_punctuated_fragment_requires_semantic_rewrite_and_second_review(self):
        client = self.client({"summary": "一项研究发现低媒体自。"},
                             {"complete": False, "faithful": False, "issues": ["残句"]},
                             {"summary": "机构发布模型安全评估。"}, PASS)
        self.assertEqual("机构发布模型安全评估。", summarize_article(client, {})["summary"])
        self.assertEqual(4, client.chat_json.call_count)

    def test_failed_or_malformed_review_never_cached(self):
        for review in ({"complete": "true", "faithful": True, "issues": []},
                       {"complete": True, "faithful": False, "issues": ["否定丢失"]}, {}):
            with self.subTest(review=review):
                client = self.client({"summary": "政策已经生效。"}, review,
                                     {"summary": "政策已经生效。"}, review)
                with self.assertRaises(ValueError):
                    summarize_article(client, {"text": "The policy is not in force."})
                self.assertFalse(list(self.root.glob("summary_cache/*.json")))

    def test_cached_result_is_revalidated(self):
        client = self.client({"summary": "机构发布报告。"}, PASS,
                             {"summary": "机构发布新报告。"}, PASS)
        summarize_article(client, {})
        cache = next(self.root.glob("summary_cache/*.json"))
        saved = json.loads(cache.read_text(encoding="utf-8"))
        saved["result"]["summary"] = "句子被截"
        cache.write_text(json.dumps(saved), encoding="utf-8")
        self.assertEqual("机构发布新报告。", summarize_article(client, {})["summary"])

    def test_structural_edge_cases(self):
        for body in (None, [], "", "介绍（研究结果。", "继续...", "长" * 500 + "。"):
            self.assertTrue(integrity_issues({"summary": body}), repr(body))
        for body in ("报告指出：“模型仍有风险。”", "机构发布报告（初稿）。", "短" * 499 + "。"):
            self.assertFalse(integrity_issues({"summary": body}), body)
