"""兼容 OpenAI API 的 LLM 客户端测试。"""

from types import SimpleNamespace
import unittest
from unittest.mock import patch

from coding_agent.config import Settings
from coding_agent.llm import LLMClient, LLMError


class LLMClientTests(unittest.TestCase):
    @patch("coding_agent.llm.OpenAI")
    def test_complete_passes_model_and_messages_and_returns_text(
        self, openai_class
    ) -> None:
        api_client = openai_class.return_value
        api_client.chat.completions.create.return_value = SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content="Hello"))]
        )
        settings = Settings(
            api_key="test-key",
            model="test-model",
            base_url="https://example.test/v1",
        )
        messages = [{"role": "user", "content": "Hi"}]

        client = LLMClient(settings)
        result = client.complete(messages)

        openai_class.assert_called_once_with(
            api_key="test-key", base_url="https://example.test/v1"
        )
        api_client.chat.completions.create.assert_called_once_with(
            model="test-model", messages=messages
        )
        self.assertEqual(result, "Hello")

    @patch("coding_agent.llm.OpenAI")
    def test_base_url_is_omitted_when_not_configured(self, openai_class) -> None:
        settings = Settings(api_key="test-key", model="test-model")

        LLMClient(settings)

        openai_class.assert_called_once_with(api_key="test-key")

    @patch("coding_agent.llm.OpenAI")
    def test_empty_response_raises_safe_error(self, openai_class) -> None:
        api_client = openai_class.return_value
        api_client.chat.completions.create.return_value = SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content=None))]
        )
        client = LLMClient(Settings(api_key="test-key", model="test-model"))

        with self.assertRaisesRegex(LLMError, "returned no text"):
            client.complete([{"role": "user", "content": "Hi"}])


if __name__ == "__main__":
    unittest.main()
