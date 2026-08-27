"""兼容 OpenAI API 的文本补全客户端。"""

from openai import (
    APIConnectionError,
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


class LLMClient:
    """将对话消息发送给兼容 OpenAI API 的服务。"""

    def __init__(self, settings: Settings) -> None:
        client_options: dict[str, str] = {"api_key": settings.api_key}
        if settings.base_url is not None:
            client_options["base_url"] = settings.base_url

        self._client = OpenAI(**client_options)
        self._model = settings.model

    def complete(self, messages: list[Message]) -> str:
        """根据传入的历史记录返回助手文本回复。"""
        try:
            response = self._client.chat.completions.create(
                model=self._model,
                messages=messages,
            )
        except AuthenticationError:
            raise LLMError("authentication error.") from None
        except RateLimitError:
            raise LLMError("rate limit exceeded.") from None
        except NotFoundError:
            raise LLMError("model or API endpoint was not found.") from None
        except (APIConnectionError, APITimeoutError):
            raise LLMError("network connection or timeout error.") from None
        except OpenAIError:
            raise LLMError(
                "Please check your network and model configuration."
            ) from None

        content = response.choices[0].message.content
        if not content:
            raise LLMError("the model returned no text.")
        return content
