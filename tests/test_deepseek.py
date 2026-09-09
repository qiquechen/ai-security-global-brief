import json
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from ai_digest.llm.deepseek import DeepSeekClient, LLMError
from ai_digest.filter.classify import classify_article
from ai_digest.summarize.run import summarize_article


class DeepSeekTests(unittest.TestCase):
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
            response({"summary": "压缩后的摘要"}),
            response({"in_scope": False}),
        ]
        with patch("ai_digest.config.DEEPSEEK_FILTER_THINKING_MODE", "disabled"), patch(
            "ai_digest.config.DEEPSEEK_SUMMARY_THINKING_MODE", "enabled"
        ):
            classify_article(client, {})
            self.assertEqual("压缩后的摘要", summarize_article(client, {})["summary"])
            classify_article(client, {})
        requests = [call.kwargs for call in create.call_args_list]
        self.assertEqual(["disabled", "enabled", "enabled", "disabled"],
                         [r["extra_body"]["thinking"]["type"] for r in requests])
        self.assertEqual([True, False, False, True], ["temperature" in r for r in requests])
        self.assertEqual("enabled", client.thinking_mode)

    def test_invalid_request_mode_does_not_call_api(self):
        client, create = self.make_client()
        with self.assertRaisesRegex(LLMError, "thinking_mode"):
            client.chat_json("system", "user", thinking_mode="invalid")
        create.assert_not_called()


if __name__ == "__main__":
    unittest.main()
