import json
import tempfile
from pathlib import Path
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from ai_digest.llm.deepseek import DeepSeekClient, LLMError
from ai_digest.filter.classify import classify_article
from ai_digest.summarize.run import summarize_article


class DeepSeekTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        patcher = patch("ai_digest.config.DATA_DIR", Path(directory.name))
        patcher.start()
        self.addCleanup(patcher.stop)
        log_patcher = patch("ai_digest.config.LOG_DIR", Path(directory.name))
        log_patcher.start()
        self.addCleanup(log_patcher.stop)

    def make_client(self, **kwargs):
        patcher = patch("ai_digest.llm.deepseek.openai.OpenAI")
        sdk = patcher.start()
        self.addCleanup(patcher.stop)
        create = sdk.return_value.chat.completions.create
        create.return_value = SimpleNamespace(
            model="deepseek-v4-flash",
            choices=[SimpleNamespace(message=SimpleNamespace(
                content=' {"ok": true} ', reasoning_content="internal reasoning",
            ))],
        )
        return DeepSeekClient(api_key="test-key", **kwargs), create

    def test_configured_model_and_mode_reach_request(self):
        with patch("ai_digest.config.DEEPSEEK_CHAT_MODEL", "deepseek-v4-flash"), patch(
            "ai_digest.config.DEEPSEEK_THINKING_MODE", "disabled"
        ):
            client, create = self.make_client()
        with self.assertLogs("ai_digest.llm.deepseek", level="INFO") as logs:
            self.assertEqual({"ok": True}, client.chat_json("system", "user"))
        request = create.call_args.kwargs
        self.assertEqual("deepseek-v4-flash", request["model"])
        self.assertEqual({"thinking": {"type": "disabled"}}, request["extra_body"])
        self.assertEqual(0.0, request["temperature"])
        self.assertIn("响应模型=deepseek-v4-flash", logs.output[0])

    def test_thinking_enabled_omits_temperature_and_preserves_model_override(self):
        client, create = self.make_client(thinking_mode=" enabled ")
        self.assertEqual({"ok": True}, client.chat_json("system", "user", model="deepseek-v4-pro"))
        request = create.call_args.kwargs
        self.assertEqual("deepseek-v4-pro", request["model"])
        self.assertEqual({"thinking": {"type": "enabled"}}, request["extra_body"])
        self.assertNotIn("temperature", request)

    def test_invalid_mode_fails_before_api_request(self):
        with self.assertRaisesRegex(LLMError, "DEEPSEEK_THINKING_MODE"):
            self.make_client(thinking_mode="enabeld")

    def test_stage_modes_reach_api_without_leaking_between_calls(self):
        client, create = self.make_client(thinking_mode="enabled")
        def response(payload):
            return SimpleNamespace(model="deepseek-v4-flash", choices=[
                SimpleNamespace(message=SimpleNamespace(content=json.dumps(payload)))
            ])
        create.side_effect = [
            response({"in_scope": False}),
            response({"summary": "长" * 501}),
            response({"summary": "压缩后的摘要。"}),
            response({"complete": True, "faithful": True, "issues": []}),
            response({"in_scope": False}),
        ]
        with patch("ai_digest.config.DEEPSEEK_FILTER_THINKING_MODE", "disabled"), patch(
            "ai_digest.config.DEEPSEEK_SUMMARY_THINKING_MODE", "enabled"
        ):
            classify_article(client, {})
            self.assertEqual("压缩后的摘要。", summarize_article(client, {})["summary"])
            classify_article(client, {})
        requests = [call.kwargs for call in create.call_args_list]
        self.assertEqual(["disabled", "enabled", "disabled", "disabled", "disabled"],
                         [r["extra_body"]["thinking"]["type"] for r in requests])
        self.assertEqual([True, False, True, True, True], ["temperature" in r for r in requests])
        self.assertEqual("enabled", client.thinking_mode)

    def test_invalid_request_mode_does_not_call_api(self):
        client, create = self.make_client()
        with self.assertRaisesRegex(LLMError, "thinking_mode"):
            client.chat_json("system", "user", thinking_mode="invalid")
        create.assert_not_called()

    def test_length_finish_retried_even_when_json_looks_valid(self):
        client, create = self.make_client(max_retries=2)
        response = create.return_value
        response.usage = SimpleNamespace(prompt_tokens=100, completion_tokens=30, total_tokens=130)
        response.choices[0].finish_reason = "length"
        with patch("ai_digest.llm.deepseek.time.sleep"), self.assertRaises(LLMError):
            client.chat_json("system", "user")
        self.assertEqual(2, create.call_count)
        from ai_digest import config
        records = [json.loads(line) for p in config.LOG_DIR.glob("operations-*.jsonl")
                   for line in p.read_text(encoding="utf-8").splitlines()]
        failed = [r for r in records if r["status"] == "failed"]
        self.assertEqual(2, len(failed))
        self.assertEqual(130, failed[0]["usage"]["total_tokens"])
        self.assertEqual("length", failed[0]["finish_reason"])
        self.assertIn("elapsed_seconds", failed[0])

    def test_logs_response_and_usage_but_not_reasoning(self):
        client, create = self.make_client()
        create.return_value.choices[0].finish_reason = "stop"
        create.return_value.usage = SimpleNamespace(prompt_tokens=10, completion_tokens=5, total_tokens=15)
        with patch("ai_digest.config.LLM_LOG_RESPONSE", True):
            client.chat_json("system", "user")
        from ai_digest import config
        raw = next(config.LOG_DIR.glob("operations-*.jsonl")).read_text(encoding="utf-8")
        record = json.loads(raw.splitlines()[-1])
        self.assertEqual(15, record["usage"]["total_tokens"])
        self.assertEqual("deepseek-v4-flash", record["response_model"])
        self.assertIn('"ok": true', record["response_text"])
        self.assertNotIn("internal reasoning", raw)


if __name__ == "__main__":
    unittest.main()
