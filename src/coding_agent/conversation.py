"""包含工具消息的本地对话历史。"""

from copy import deepcopy
from typing import Any, TYPE_CHECKING, TypeAlias

if TYPE_CHECKING:
    from coding_agent.llm import ToolCall


Message: TypeAlias = dict[str, Any]


class Conversation:
    """按协议顺序维护 system、user、assistant 和 tool 消息。"""

    def __init__(self, system_prompt: str) -> None:
        self._messages: list[Message] = [
            {"role": "system", "content": system_prompt}
        ]

    def add_user_message(self, content: str) -> None:
        """向历史记录追加一条用户消息。"""
        self._messages.append({"role": "user", "content": content})

    def add_system_message(self, content: str) -> None:
        """追加由本地 Runtime 产生的系统约束消息。"""
        self._messages.append({"role": "system", "content": content})

    def add_assistant_message(self, content: str) -> None:
        """向历史记录追加一条助手回复。"""
        self._messages.append({"role": "assistant", "content": content})

    def add_assistant_tool_calls(
        self, content: str | None, tool_calls: list["ToolCall"]
    ) -> None:
        """追加包含原生 function tool calls 的助手消息。"""
        self._messages.append(
            {
                "role": "assistant",
                "content": content,
                "tool_calls": [
                    {
                        "id": tool_call.id,
                        "type": "function",
                        "function": {
                            "name": tool_call.name,
                            "arguments": tool_call.arguments,
                        },
                    }
                    for tool_call in tool_calls
                ],
            }
        )

    def add_tool_result(self, tool_call_id: str, content: str) -> None:
        """追加与指定工具调用对应的结构化结果。"""
        self._messages.append(
            {
                "role": "tool",
                "tool_call_id": tool_call_id,
                "content": content,
            }
        )

    @property
    def messages(self) -> list[Message]:
        """返回副本，避免调用者修改内部历史记录。"""
        return deepcopy(self._messages)
