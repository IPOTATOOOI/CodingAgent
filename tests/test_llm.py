"""兼容 OpenAI API 的模型响应规范化测试。"""

from types import SimpleNamespace
import unittest
from unittest.mock import patch

import httpx
from openai import (
    APIConnectionError,
    AuthenticationError,
    InternalServerError,
    RateLimitError,
)

from coding_agent.config import Settings
from coding_agent.llm import LLMClient, LLMError


class LLMClientTests(unittest.TestCase):
    @patch("coding_agent.llm.OpenAI")
    def test_complete_passes_model_messages_and_tools(self, openai_class) -> None:
        api_client = openai_class.return_value
        api_client.chat.completions.create.return_value = SimpleNamespace(
            choices=[
                SimpleNamespace(
                    message=SimpleNamespace(content="Hello", tool_calls=[])
                )
            ]
        )
        settings = Settings(
            api_key="test-key",
            model="test-model",
            base_url="https://example.test/v1",
        )
        messages = [{"role": "user", "content": "Hi"}]
        tools = [{"type": "function", "function": {"name": "read_file"}}]

        client = LLMClient(settings)
        result = client.complete(messages, tools=tools)

        openai_class.assert_called_once_with(
            api_key="test-key", base_url="https://example.test/v1"
        )
        api_client.chat.completions.create.assert_called_once_with(
            model="test-model", messages=messages, tools=tools
        )
        self.assertEqual(result.content, "Hello")
        self.assertEqual(result.tool_calls, [])

    @patch("coding_agent.llm.OpenAI")
    def test_complete_parses_native_tool_calls(self, openai_class) -> None:
        native_tool_call = SimpleNamespace(
            id="call-1",
            function=SimpleNamespace(
                name="read_file", arguments='{"path":"README.md"}'
            ),
        )
        openai_class.return_value.chat.completions.create.return_value = (
            SimpleNamespace(
                choices=[
                    SimpleNamespace(
                        message=SimpleNamespace(
                            content=None, tool_calls=[native_tool_call]
                        )
                    )
                ]
            )
        )
        client = LLMClient(Settings(api_key="test-key", model="test-model"))

        result = client.complete([{"role": "user", "content": "Read README"}])

        self.assertEqual(result.tool_calls[0].id, "call-1")
        self.assertEqual(result.tool_calls[0].name, "read_file")
        self.assertEqual(
            result.tool_calls[0].arguments, '{"path":"README.md"}'
        )

    @patch("coding_agent.llm.OpenAI")
    def test_base_url_is_omitted_when_not_configured(self, openai_class) -> None:
        LLMClient(Settings(api_key="test-key", model="test-model"))

        openai_class.assert_called_once_with(api_key="test-key")

    @patch("coding_agent.llm.OpenAI")
    def test_empty_response_raises_safe_error(self, openai_class) -> None:
        openai_class.return_value.chat.completions.create.return_value = (
            SimpleNamespace(
                choices=[
                    SimpleNamespace(
                        message=SimpleNamespace(content=None, tool_calls=[])
                    )
                ]
            )
        )
        client = LLMClient(Settings(api_key="test-key", model="test-model"))

        with self.assertRaisesRegex(LLMError, "neither text nor tool calls"):
            client.complete([{"role": "user", "content": "Hi"}])

    @patch("coding_agent.llm.OpenAI")
    def test_rate_limit_connection_and_server_errors_are_transient(
        self, openai_class
    ) -> None:
        request = httpx.Request("POST", "https://example.test/v1/chat/completions")
        errors = [
            RateLimitError(
                "rate limit",
                response=httpx.Response(429, request=request),
                body=None,
            ),
            APIConnectionError(request=request),
            InternalServerError(
                "server error",
                response=httpx.Response(500, request=request),
                body=None,
            ),
        ]
        client = LLMClient(Settings(api_key="test-key", model="test-model"))

        for error in errors:
            with self.subTest(error=type(error).__name__):
                openai_class.return_value.chat.completions.create.side_effect = error
                with self.assertRaises(LLMError) as raised:
                    client.complete([{"role": "user", "content": "Hi"}])
                self.assertTrue(raised.exception.transient)

    @patch("coding_agent.llm.OpenAI")
    def test_authentication_error_is_permanent(self, openai_class) -> None:
        request = httpx.Request("POST", "https://example.test/v1/chat/completions")
        openai_class.return_value.chat.completions.create.side_effect = (
            AuthenticationError(
                "invalid key",
                response=httpx.Response(401, request=request),
                body=None,
            )
        )
        client = LLMClient(Settings(api_key="test-key", model="test-model"))

        with self.assertRaises(LLMError) as raised:
            client.complete([{"role": "user", "content": "Hi"}])

        self.assertFalse(raised.exception.transient)

    @patch("coding_agent.llm.OpenAI")
    def test_streaming_combines_text_and_tool_call_deltas(self, openai_class) -> None:
        def chunk(content=None, tool_calls=None):
            return SimpleNamespace(
                choices=[
                    SimpleNamespace(
                        delta=SimpleNamespace(
                            content=content,
                            tool_calls=tool_calls or [],
                        )
                    )
                ]
            )

        first_tool = SimpleNamespace(
            index=0,
            id="call-1",
            function=SimpleNamespace(name="read_file", arguments='{"path":'),
        )
        second_tool = SimpleNamespace(
            index=0,
            id=None,
            function=SimpleNamespace(name=None, arguments='"README.md"}'),
        )
        openai_class.return_value.chat.completions.create.return_value = iter(
            [chunk("I will "), chunk("read."), chunk(tool_calls=[first_tool]), chunk(tool_calls=[second_tool])]
        )
        deltas = []
        client = LLMClient(Settings(api_key="test-key", model="test-model"))

        result = client.complete_stream(
            [{"role": "user", "content": "Read"}],
            on_text_delta=deltas.append,
        )

        self.assertEqual(result.content, "I will read.")
        self.assertEqual(deltas, ["I will ", "read."])
        self.assertEqual(result.tool_calls[0].id, "call-1")
        self.assertEqual(result.tool_calls[0].name, "read_file")
        self.assertEqual(result.tool_calls[0].arguments, '{"path":"README.md"}')
        request = openai_class.return_value.chat.completions.create.call_args.kwargs
        self.assertTrue(request["stream"])


if __name__ == "__main__":
    unittest.main()
