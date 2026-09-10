import unittest
from scripts.inspect_operations import summarize


class OperationReportTests(unittest.TestCase):
    def test_counts_failed_attempt_usage_and_exposes_missing_usage(self):
        result = summarize([
            dict(stage="llm.request", status="started"),
            dict(stage="llm.request", status="failed", usage={"total_tokens": 12},
                 response_model="model-a", elapsed_seconds=2),
            dict(stage="llm.request", status="completed", usage={"total_tokens": 20},
                 response_model="model-a", elapsed_seconds=3),
            dict(stage="llm.request", status="failed", elapsed_seconds=1),
            dict(stage="report", status="completed", elapsed_seconds=10),
        ])
        self.assertEqual(3, result["request_attempts"])
        self.assertEqual(2, result["failed_attempts"])
        self.assertEqual({"known_total": 32, "missing_calls": 1}, result["usage"]["total_tokens"])
        self.assertEqual(6, result["llm_elapsed_seconds"])
        self.assertEqual(10, result["stages"][0]["elapsed_seconds"])
