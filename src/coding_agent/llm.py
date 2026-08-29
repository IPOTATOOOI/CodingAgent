"""兼容 OpenAI API 的文本与工具调用客户端。"""

from dataclasses import dataclass
from collections.abc import Callable
from typing import Any

from openai import (
    APIConnectionError,
    APIStatusError,
    APITimeoutError,
    AuthenticationError,
    NotFoundError,
    OpenAI,
    OpenAIError,
    RateLimitError,
)

from coding_agent.config import Settings
from coding_agent.conversation import Message


class LLMError(RuntimeError):
    """可安全展示给用户的 LLM 请求异常。"""

    def __init__(self, message: str, transient: bool = False) -> None:
        super().__init__(message)
        self.transient = transient


class LLMInterrupted(LLMError):
    """流式请求在本地取消检查点被协作式中止。"""


@dataclass(frozen=True)
class ToolCall:
    """从模型原生响应中规范化得到的函数工具调用。"""

    id: str
    name: str
    arguments: str


@dataclass(frozen=True)
class LLMResponse:
    """不泄漏供应商 SDK 类型的规范化模型响应。"""

    content: str | None
    tool_calls: list[ToolCall]


class LLMClient:
    """将对话消息发送给兼容 OpenAI API 的服务。"""

    def __init__(self, settings: Settings) -> None:
        client_options: dict[str, str] = {"api_key": settings.api_key}
        if settings.base_url is not None:
            client_options["base_url"] = settings.base_url

        self._client = OpenAI(**client_options)
        self._model = settings.model
        self.supports_streaming = True

    def complete(
        self,
        messages: list[Message],
        tools: list[dict[str, Any]] | None = None,
    ) -> LLMResponse:
        """返回规范化的助手文本和函数工具调用。"""
        request: dict[str, Any] = {
            "model": self._model,
            "messages": messages,
        }
        if tools is not None:
            request["tools"] = tools

        try:
            response = self._client.chat.completions.create(**request)
        except AuthenticationError:
            raise LLMError("authentication error.") from None
        except RateLimitError:
            raise LLMError("rate limit exceeded.", transient=True) from None
        except NotFoundError:
            raise LLMError("model or API endpoint was not found.") from None
        except (APIConnectionError, APITimeoutError):
            raise LLMError(
                "network connection or timeout error.", transient=True
            ) from None
        except APIStatusError as error:
            if error.status_code >= 500:
                raise LLMError("temporary server error.", transient=True) from None
            raise LLMError(
                "Please check your network and model configuration."
            ) from None
        except OpenAIError:
            raise LLMError(
                "Please check your network and model configuration."
            ) from None

        message = response.choices[0].message
        tool_calls = [
            ToolCall(
                id=tool_call.id,
                name=tool_call.function.name,
                arguments=tool_call.function.arguments,
            )
            for tool_call in (message.tool_calls or [])
        ]
        if not message.content and not tool_calls:
            raise LLMError("the model returned neither text nor tool calls.")
        return LLMResponse(content=message.content, tool_calls=tool_calls)

    def complete_stream(
        self,
        messages: list[Message],
        tools: list[dict[str, Any]] | None = None,
        on_text_delta: Callable[[str], None] | None = None,
        should_cancel: Callable[[], bool] | None = None,
    ) -> LLMResponse:
        """流式接收文本/工具参数，并返回与 ``complete`` 相同的规范化结果。"""
        request: dict[str, Any] = {
            "model": self._model,
            "messages": messages,
            "stream": True,
        }
        if tools is not None:
            request["tools"] = tools

        stream = None
        content_parts: list[str] = []
        calls: dict[int, dict[str, str]] = {}
        try:
            stream = self._client.chat.completions.create(**request)
            for chunk in stream:
                if should_cancel is not None and should_cancel():
                    raise LLMInterrupted("request interrupted by user.")
                if not chunk.choices:
                    continue
                delta = chunk.choices[0].delta
                if delta.content:
                    content_parts.append(delta.content)
                    if on_text_delta is not None:
                        on_text_delta(delta.content)
                for tool_delta in delta.tool_calls or []:
                    current = calls.setdefault(
                        tool_delta.index,
                        {"id": "", "name": "", "arguments": ""},
                    )
                    if tool_delta.id:
                        current["id"] += tool_delta.id
                    function = tool_delta.function
                    if function is not None:
                        if function.name:
                            current["name"] += function.name
                        if function.arguments:
                            current["arguments"] += function.arguments
        except LLMInterrupted:
            raise
        except OpenAIError as error:
            raise self._safe_error(error) from None
        finally:
            if stream is not None and should_cancel is not None and should_cancel():
                close = getattr(stream, "close", None)
                if callable(close):
                    close()

        content = "".join(content_parts) or None
        tool_calls = [
            ToolCall(
                id=value["id"] or f"stream-call-{index}",
                name=value["name"],
                arguments=value["arguments"],
            )
            for index, value in sorted(calls.items())
        ]
        if not content and not tool_calls:
            raise LLMError("the model returned neither text nor tool calls.")
        return LLMResponse(content=content, tool_calls=tool_calls)

    @staticmethod
    def _safe_error(error: OpenAIError) -> LLMError:
        """把供应商 SDK 异常转换为稳定且不泄漏敏感信息的异常。"""
        if isinstance(error, AuthenticationError):
            return LLMError("authentication error.")
        if isinstance(error, RateLimitError):
            return LLMError("rate limit exceeded.", transient=True)
        if isinstance(error, NotFoundError):
            return LLMError("model or API endpoint was not found.")
        if isinstance(error, (APIConnectionError, APITimeoutError)):
            return LLMError("network connection or timeout error.", transient=True)
        if isinstance(error, APIStatusError) and error.status_code >= 500:
            return LLMError("temporary server error.", transient=True)
        return LLMError("Please check your network and model configuration.")
