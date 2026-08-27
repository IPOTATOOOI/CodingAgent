"""纯文本 LLM 交互使用的本地对话历史。"""

from typing import TypeAlias


Message: TypeAlias = dict[str, str]


class Conversation:
    """按顺序维护 system、user 和 assistant 消息。"""

    def __init__(self, system_prompt: str) -> None:
        self._messages: list[Message] = [
            {"role": "system", "content": system_prompt}
        ]

    def add_user_message(self, content: str) -> None:
        """向历史记录追加一条用户消息。"""
        self._messages.append({"role": "user", "content": content})

    def add_assistant_message(self, content: str) -> None:
        """向历史记录追加一条助手回复。"""
        self._messages.append({"role": "assistant", "content": content})

    @property
    def messages(self) -> list[Message]:
        """返回副本，避免调用者修改内部历史记录。"""
        return [message.copy() for message in self._messages]
