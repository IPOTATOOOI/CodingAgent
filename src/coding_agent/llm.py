"""兼容 OpenAI API 的文本与工具调用客户端。"""

from dataclasses import dataclass
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
